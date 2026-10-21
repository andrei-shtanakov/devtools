"""Platform layer: the `df-build` account, trusted paths, runs as a role.

Spec 3.2 (identity of `df-build`), 3.3 ("Создание учётки", "Очистка",
producer lifecycle) and 3.10 (trusted paths and ACL). Every command that
changes system accounts goes through `run_command`; every process of a role
goes through `run_as` / `spawn_as`. Every refusal is a named `StepFailed`
(or `TreeError` for the producer chain).
"""

from __future__ import annotations

import ctypes
import errno
import functools
import glob
import grp
import os
import plistlib
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Literal
from xml.parsers.expat import ExpatError

from df import fsutil
from df.acl import acl_present_path
from df.config import Account, ConfigError, dump_accounts, load_accounts
from df.digest import TreeError
from df.journal import StepFailed
from df.paths import Layout

PlatformName = Literal["macos-arm64", "linux-x86_64"]
RecordKind = Literal["group", "user"]
CleanupOutcome = Literal["removed", "quarantined", "failed"]

#: Owner of every trusted object; tests substitute it.
ROOT_UID = 0
BUILD_USER = "df-build"
HOME_TMP_NAME = ".df-build-home.creating"
ADMIN_GROUPS = frozenset({"root", "wheel", "admin", "sudo", "adm"})
NOLOGIN_SHELLS = frozenset({"/usr/bin/false", "/usr/sbin/nologin", "/sbin/nologin"})
NO_NETWORK_PROFILE = "(version 1)(allow default)(deny network*)"
#: Reported by `reap_user_processes` when `ps` could not be read.
UNKNOWN_PID = -1
#: `returncode` of a command whose executable could not be started.
LAUNCH_FAILED = 127

TAR_FLAGS: Mapping[PlatformName, tuple[str, ...]] = {
    "macos-arm64": (
        "--format",
        "pax",
        "--no-mac-metadata",
        "--no-xattrs",
        "--no-acls",
        "--no-fflags",
        "--no-read-sparse",
    ),
    "linux-x86_64": ("--format=pax", "--no-xattrs", "--no-acls", "--no-selinux"),
}
SYSTEM_LIB_DIRS: Mapping[PlatformName, tuple[str, ...]] = {
    "macos-arm64": ("/usr/lib/", "/System/Library/"),
    "linux-x86_64": ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/"),
}
#: Directories where an ELF soname must be found (the loader's defaults).
LOADER_DIRS: Mapping[PlatformName, tuple[str, ...]] = {
    "macos-arm64": ("/usr/lib",),
    "linux-x86_64": (
        "/lib/x86_64-linux-gnu",
        "/usr/lib/x86_64-linux-gnu",
        "/lib64",
        "/usr/lib64",
        "/lib",
        "/usr/lib",
    ),
}
#: Linux loader configuration (its directories are searched before defaults).
LD_SO_CONF = Path("/etc/ld.so.conf")
#: Linux libraries loaded into every process.
LD_SO_PRELOAD = Path("/etc/ld.so.preload")
#: A Mach-O linking it is an xcode-select shim (`/usr/bin/git` and friends).
XCSELECT_LIB = "/usr/lib/libxcselect.dylib"
#: Free UID/GID are chosen from these ranges (macOS: 400-499, spec 3.3).
ID_RANGE: Mapping[PlatformName, range] = {
    "macos-arm64": range(400, 500),
    "linux-x86_64": range(100, 1000),
}

_IS_DARWIN = sys.platform == "darwin"
_LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
_RENAME_EXCL = 0x00000004  # macOS <stdio.h>
_RENAME_NOREPLACE = 1  # Linux <linux/fs.h>
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PEEK_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_MAX_HOPS = 40
_MAX_SHEBANG_DEPTH = 4
_MAX_CONF_DEPTH = 8
_CONF_LIMIT = 1 << 20
_GLOB_CHARS = frozenset("*?[]\\")
_LAUNCH_CHECK_SECONDS = 2.0
_READ_CHUNK = 64 << 10
_ROLE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
_SYSTEM_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}

_SUDO = "/usr/bin/sudo"
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_UNSHARE = "/usr/bin/unshare"
_SETPRIV = "/usr/bin/setpriv"
_ENV = "/usr/bin/env"
_DSCL = "/usr/bin/dscl"
_GETENT = "/usr/bin/getent"
_GROUPADD = "/usr/sbin/groupadd"
_USERADD = "/usr/sbin/useradd"
_PKILL = "/usr/bin/pkill"
_PS = "/bin/ps"
_OTOOL = "/usr/bin/otool"
_READELF = "/usr/bin/readelf"
_FIND = "/usr/bin/find"
_CHMOD = "/bin/chmod"
#: Exit codes of the launch mechanism itself (sudo, setpriv, unshare: 1;
#: sandbox-exec: 65/71; env: 125-127), recognised together with its prefix.
_MECHANISM_EXIT = frozenset({1, 65, 71, 125, 126, 127})
_MECHANISM_PREFIX = (b"sudo: ", b"sandbox-exec: ", b"setpriv: ", b"unshare: ")
_ENV_PREFIX = b"env: "
_DSCL_NOT_FOUND = b"eDSRecordNotFound"
_DS_STANDARD = "dsAttrTypeStandard:"
_DS_NATIVE = "dsAttrTypeNative:"
_GETENT_NOT_FOUND = 2
_MACHO_MAGIC = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    }
)
_ELF_MAGIC = b"\x7fELF"
_INSTALL_HINT = (
    "поддерживаемый способ — системный CLI или "
    "`sudo install -o root -m 0755 <src> /usr/local/libexec/darkfactory/bin/{name}`"
)

# Seams for tests: the filesystem view of `trusted_path_problems`.
_lstat = os.lstat
_readlink = os.readlink
_getgrouplist = os.getgrouplist
_glob = glob.glob
_uname = os.uname


# --- platform ---------------------------------------------------------------


def detect_platform() -> PlatformName:
    """The supported platform of this host; anything else is `ConfigError`."""
    info = _uname()
    if info.sysname == "Darwin" and info.machine == "arm64":
        return "macos-arm64"
    if info.sysname == "Linux" and info.machine == "x86_64":
        return "linux-x86_64"
    raise ConfigError(f"unsupported platform: {info.sysname} {info.machine}")


# --- running commands --------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """Outcome of one command.

    `launched=False`: the launch mechanism (or the executable) did not start,
    an infrastructure failure rather than a refusal of the operation.
    `timed_out=True`: the deadline passed and the process group was killed.
    """

    returncode: int
    stdout: bytes
    stderr: bytes
    launched: bool
    timed_out: bool = False


def run_command(
    argv: Sequence[str],
    *,
    timeout: float = 60.0,
    env: Mapping[str, str] = _SYSTEM_ENV,
) -> RunResult:
    """Run a command as the current (root) user; tests replace this function."""
    return _execute(argv, env=env, cwd=Path("/"), timeout=timeout, limit=1 << 20)


def run_as(
    user: str,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    network: bool,
    timeout: float,
    stdout_limit: int = 1 << 20,
) -> RunResult:
    """Run `argv` as `user`, environment built only from `env` (`env -i`).

    Without network: macOS `sudo -n -u <user> sandbox-exec -p <profile>`,
    Linux `unshare --net -- setpriv --reuid=<user> ...`. On timeout the
    session group and every process of `user` are killed.
    """
    full = role_argv(detect_platform(), user, argv, env=env, network=network)
    result = _execute(
        full, env=_SYSTEM_ENV, cwd=cwd, timeout=timeout, limit=stdout_limit
    )
    if result.timed_out:
        reap_user_processes(user)
    if result.launched and _mechanism_failed(result.returncode, result.stderr):
        return replace(result, launched=False)
    return result


def role_argv(
    platform: PlatformName,
    user: str,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    network: bool,
) -> list[str]:
    """The full launch chain for `argv` as `user` on `platform`."""
    inner = _env_argv(argv, env)
    if platform == "macos-arm64":
        sandbox = [] if network else [_SANDBOX_EXEC, "-p", NO_NETWORK_PROFILE]
        return [_SUDO, "-n", "-u", user, *sandbox, *inner]
    drop = [_SETPRIV, f"--reuid={user}", f"--regid={user}", "--init-groups", "--"]
    netns = [] if network else [_UNSHARE, "--net", "--"]
    return [*netns, *drop, *inner]


def _env_argv(argv: Sequence[str], env: Mapping[str, str]) -> list[str]:
    if not argv or not argv[0].startswith("/") or "=" in argv[0]:
        raise StepFailed("run-as", f"команда должна быть абсолютным путём: {argv!r}")
    for key in env:
        if not key or "=" in key:
            raise StepFailed("run-as", f"недопустимое имя переменной: {key!r}")
    return [_ENV, "-i", *(f"{k}={v}" for k, v in env.items()), *argv]


def _mechanism_failed(code: int, stderr: bytes) -> bool:
    if code not in _MECHANISM_EXIT:
        return False
    head = stderr.lstrip()
    return head.startswith(_MECHANISM_PREFIX) or head.startswith(_ENV_PREFIX)


def _execute(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
    limit: int,
) -> RunResult:
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as exc:
        return RunResult(LAUNCH_FAILED, b"", str(exc).encode(), launched=False)
    try:
        return _collect(proc, time.monotonic() + timeout, limit)
    finally:
        # Not `with proc`: its exit waits without a limit (a process stuck in
        # uninterruptible sleep would hang the caller after the kill).
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def _collect(proc: subprocess.Popen[bytes], deadline: float, limit: int) -> RunResult:
    """Read both pipes up to `limit` bytes each until EOF or the deadline."""
    assert proc.stdout is not None and proc.stderr is not None
    buffers = {proc.stdout.fileno(): bytearray(), proc.stderr.fileno(): bytearray()}
    overflow = False
    with selectors.DefaultSelector() as sel:
        for fd in buffers:
            sel.register(fd, selectors.EVENT_READ)
        while sel.get_map() and not overflow:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            for key, _ in sel.select(left):
                try:
                    chunk = os.read(key.fd, _READ_CHUNK)
                except OSError:
                    chunk = b""  # a broken pipe ends that stream
                if not chunk:
                    sel.unregister(key.fd)
                    continue
                buffers[key.fd] += chunk
                overflow = overflow or len(buffers[key.fd]) > limit
    timed_out = not overflow and time.monotonic() >= deadline
    code = _finish(proc, deadline, kill=timed_out or overflow)
    if code is None:
        timed_out = True
        code = _finish(proc, deadline, kill=True)
    return RunResult(
        code if code is not None else -signal.SIGKILL,
        bytes(buffers[proc.stdout.fileno()][:limit]),
        bytes(buffers[proc.stderr.fileno()][:limit]),
        launched=True,
        timed_out=timed_out,
    )


def _finish(
    proc: subprocess.Popen[bytes], deadline: float, *, kill: bool
) -> int | None:
    if kill:
        kill_group(proc.pid)
        deadline = time.monotonic() + 10.0
    try:
        return proc.wait(max(deadline - time.monotonic(), 0.0))
    except subprocess.TimeoutExpired:
        return None


def kill_group(pgid: int) -> None:
    """`SIGKILL` a process group; no group (ESRCH, or EPERM for zombies) is fine."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass  # macOS: EPERM when every member is already a zombie


def whoami_as(user: str) -> tuple[int, int]:
    """Positive control: UID and GID a process of `user` actually starts with."""
    return _id_as(user, "-u"), _id_as(user, "-g")


def _id_as(user: str, flag: str) -> int:
    result = run_as(
        user,
        ["/usr/bin/id", flag],
        env=_ROLE_ENV,
        cwd=Path("/"),
        network=False,
        timeout=10.0,
    )
    text = result.stdout.strip()
    if not result.launched or result.returncode != 0 or not text:
        raise StepFailed("whoami", f"id {flag} от {user} не выполнен: {_brief(result)}")
    try:
        return int(text)
    except ValueError:
        raise StepFailed("whoami", f"id {flag} от {user}: {text!r}") from None


def _brief(result: RunResult) -> str:
    err = result.stderr.decode("utf-8", "replace").strip()
    state = "не стартовал" if not result.launched else f"код {result.returncode}"
    if result.timed_out:
        state = "срок истёк"
    return f"{state}: {err}" if err else state


# --- streaming producer ------------------------------------------------------


class SpawnedProducer:
    """`Producer` (df.receive) over a process chain of a role user."""

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        user: str,
        reap_seconds: float,
        stderr: IO[bytes],
    ) -> None:
        assert proc.stdout is not None
        self._proc = proc
        self._stdout = proc.stdout
        self._user = user
        self._reap_seconds = reap_seconds
        self._stderr = stderr

    @property
    def fd(self) -> int:
        """Producer stdout."""
        return self._stdout.fileno()

    def wait(self, timeout: float) -> int:
        """Exit code of the direct child; not exited in time -> `TreeError`."""
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired as exc:
            raise TreeError("producer did not exit in time") from exc

    def end(self) -> None:
        """Kill the group, reap the role's processes, reap the child.

        A process of the role left alive, or a child not reaped in
        `reap_seconds`, is a `TreeError`.
        """
        if self._proc.returncode is None:  # a reaped leader's pid may be reused
            kill_group(self._proc.pid)
        left = reap_user_processes(self._user)
        try:
            self._proc.wait(self._reap_seconds)
        except subprocess.TimeoutExpired as exc:
            raise TreeError("producer not reaped") from exc
        finally:
            self._stdout.close()
            self._stderr.close()
        if left:
            raise TreeError(f"processes of {self._user} left: {left}")


def spawn_as(
    user: str,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    network: bool,
    reap_seconds: float = 10.0,
) -> SpawnedProducer:
    """Start `argv` as `user` with stdout as a stream (same chain as `run_as`).

    Own session, stdin `/dev/null`: without a controlling terminal `sudo`
    creates no pseudo-terminal. A chain that exits within 2 s with the
    launch mechanism's own failure is `StepFailed("run-as")`.
    """
    full = role_argv(detect_platform(), user, argv, env=env, network=network)
    stderr = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            full,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=dict(_SYSTEM_ENV),
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as exc:
        stderr.close()
        raise StepFailed("run-as", f"запуск от {user} не стартовал: {exc}") from exc
    producer = SpawnedProducer(proc, user, reap_seconds, stderr)
    _check_launch(proc, producer, stderr, user)
    return producer


def _check_launch(
    proc: subprocess.Popen[bytes],
    producer: SpawnedProducer,
    stderr: IO[bytes],
    user: str,
) -> None:
    try:
        code = proc.wait(_LAUNCH_CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        return
    stderr.seek(0)
    head = stderr.read(4096)
    if not _mechanism_failed(code, head):
        return
    try:
        producer.end()
    except TreeError:
        pass  # the launch failure below is the reported refusal
    detail = head.decode("utf-8", "replace").strip()
    raise StepFailed("run-as", f"запуск от {user} не стартовал: {detail}")


def reap_user_processes(name: str, *, timeout: float = 5.0) -> list[int]:
    """`pkill -KILL -u <name>` until `ps` shows none (zombies excluded).

    Returns PIDs still alive at the timeout (`UNKNOWN_PID` if `ps` could not
    be read); never refuses. Effects: `process(df-build)`.
    """
    try:
        uid = pwd.getpwnam(name).pw_uid
    except KeyError:
        return []
    deadline = time.monotonic() + timeout
    while True:
        run_command([_PKILL, "-KILL", "-u", name], timeout=max(timeout, 1.0))
        left = _user_pids(uid)
        if left == []:
            return []
        if time.monotonic() >= deadline:
            return left if left is not None else [UNKNOWN_PID]
        time.sleep(0.05)


def _user_pids(uid: int) -> list[int] | None:
    result = run_command([_PS, "-A", "-o", "pid=,uid=,stat="])
    if not result.launched or result.returncode != 0:
        return None
    pids: list[int] = []
    for line in result.stdout.decode("ascii", "replace").splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3 or not all(_is_number(f) for f in fields[:2]):
            return None
        if int(fields[1]) == uid and not fields[2].startswith("Z"):
            pids.append(int(fields[0]))
    return pids


def _is_number(text: str) -> bool:
    return text.isascii() and text.isdigit()


# --- sudo rules ---------------------------------------------------------------


def sudo_rules(name: str) -> Literal["none", "some", "unknown"]:
    """Whether `sudo -n -l -U <name>` grants anything; failure is `"unknown"`."""
    result = run_command([_SUDO, "-n", "-l", "-U", name])
    if not result.launched:
        return "unknown"
    return parse_sudo_rules(name, result.returncode, result.stdout)


def parse_sudo_rules(
    name: str, returncode: int, output: bytes
) -> Literal["none", "some", "unknown"]:
    """`"none"` only for exit 0 and the exact "is not allowed" sentence."""
    if returncode != 0:
        return "unknown"
    try:
        lines = [line.strip() for line in output.decode("utf-8").splitlines()]
    except UnicodeDecodeError:
        return "unknown"
    user = re.escape(name)
    none = re.compile(rf"User {user} is not allowed to run sudo on \S+\.")
    some = re.compile(rf"User {user} may run the following commands on \S+:")
    has_none = any(none.fullmatch(line) for line in lines)
    has_some = any(some.fullmatch(line) for line in lines)
    if has_none and not has_some:
        return "none"
    if has_some and not has_none:
        return "some"
    return "unknown"


# --- account records --------------------------------------------------------


@dataclass(frozen=True)
class AttrOp:
    """One OS command writing one element of an account record.

    `value` is what the record must hold for this element. On Linux the
    element is the whole `getent` line, rendered as `key=value` lines.
    """

    kind: RecordKind
    attr: str
    value: Callable[[Account, Layout], str]


def _fields(**values: object) -> str:
    return "\n".join(f"{k}={v}" for k, v in values.items())


ACCOUNT_ATTRS: Mapping[PlatformName, tuple[AttrOp, ...]] = {
    "macos-arm64": (
        AttrOp("group", "RecordName", lambda a, _l: a.name),
        AttrOp("group", "PrimaryGroupID", lambda a, _l: str(a.gid)),
        AttrOp("user", "RecordName", lambda a, _l: a.name),
        AttrOp("user", "Password", lambda _a, _l: "*"),
        AttrOp("user", "UserShell", lambda _a, _l: "/usr/bin/false"),
        AttrOp("user", "IsHidden", lambda _a, _l: "1"),
        AttrOp("user", "PrimaryGroupID", lambda a, _l: str(a.gid)),
        AttrOp("user", "NFSHomeDirectory", lambda _a, lay: str(lay.build_home)),
        AttrOp("user", "UniqueID", lambda a, _l: str(a.uid)),
    ),
    "linux-x86_64": (
        AttrOp("group", "line", lambda a, _l: _fields(gid=a.gid)),
        AttrOp(
            "user",
            "line",
            lambda a, lay: _fields(
                uid=a.uid, gid=a.gid, home=lay.build_home, shell="/usr/sbin/nologin"
            ),
        ),
    ),
}


def attr_argv(
    platform: PlatformName, op: AttrOp, account: Account, layout: Layout
) -> list[str]:
    """The OS command that writes `op` for `account`."""
    if platform == "macos-arm64":
        path = f"/{'Groups' if op.kind == 'group' else 'Users'}/{account.name}"
        if op.attr == "RecordName":
            return [_DSCL, ".", "-create", path]
        return [_DSCL, ".", "-create", path, op.attr, op.value(account, layout)]
    if op.kind == "group":
        return [_GROUPADD, "--system", "--gid", str(account.gid), account.name]
    return [
        _USERADD,
        "--system",
        "--uid",
        str(account.uid),
        "--gid",
        account.name,
        "--home-dir",
        str(layout.build_home),
        "--no-create-home",
        "--shell",
        "/usr/sbin/nologin",
        account.name,
    ]


def read_record(kind: RecordKind, name: str) -> dict[str, str] | None:
    """Attributes of the user or group record `name`; `None` if absent.

    macOS: `dscl -plist . -read` (standard attributes, without the
    `dsAttrTypeStandard:` prefix); Linux: `getent passwd|group` (fields `uid`,
    `gid`, `home`, `shell` / `gid`). A failed read or unparsable output is
    `StepFailed`, never "absent"; unknown extra attributes are ignored.
    """
    if detect_platform() == "macos-arm64":
        return _read_dscl(kind, name)
    return _read_getent(kind, name)


def _read_dscl(kind: RecordKind, name: str) -> dict[str, str] | None:
    path = f"/{'Groups' if kind == 'group' else 'Users'}/{name}"
    result = run_command([_DSCL, "-plist", ".", "-read", path])
    if result.launched and result.returncode == 0:
        return parse_dscl(result.stdout)
    if result.launched and _DSCL_NOT_FOUND in result.stdout + result.stderr:
        return None
    raise StepFailed("build-user", f"чтение записи {kind} {name}: {_brief(result)}")


def parse_dscl(output: bytes) -> dict[str, str]:
    """Attributes of a `dscl -plist -read` record, without the type prefix.

    Standard attributes are taken; a native one only when the intent lists
    it (macOS keeps some, like `IsHidden`, as `dsAttrTypeNative:`). Both forms
    of one name with different values are refused. Every value is an array
    of strings; several values join with `", "` (and so never equal a single
    intended value). Other native attributes are ignored.
    """
    try:
        data = plistlib.loads(output, fmt=plistlib.FMT_XML)
    except (plistlib.InvalidFileException, ValueError, ExpatError) as exc:
        raise StepFailed("build-user", f"неразборный вывод dscl: {exc}") from exc
    if not isinstance(data, dict):
        raise StepFailed("build-user", "неразборный вывод dscl: не словарь")
    intended = {op.attr for op in ACCOUNT_ATTRS["macos-arm64"]}
    record: dict[str, str] = {}
    for key, values in data.items():
        name = _ds_attr_name(key, intended)
        if name is None:
            continue
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise StepFailed(
                "build-user", f"неразборное значение dscl {key}: {values!r}"
            )
        value = ", ".join(values)
        if record.setdefault(name, value) != value:
            raise StepFailed("build-user", f"dscl: два разных значения {name}")
    return record


def _ds_attr_name(key: object, intended: set[str]) -> str | None:
    if not isinstance(key, str):
        return None
    if key.startswith(_DS_STANDARD):
        return key.removeprefix(_DS_STANDARD)
    name = key.removeprefix(_DS_NATIVE)
    return name if key.startswith(_DS_NATIVE) and name in intended else None


def _read_getent(kind: RecordKind, name: str) -> dict[str, str] | None:
    db = "group" if kind == "group" else "passwd"
    result = run_command([_GETENT, db, name])
    if result.launched and result.returncode == _GETENT_NOT_FOUND:
        return None
    if not result.launched or result.returncode != 0:
        raise StepFailed("build-user", f"чтение записи {kind} {name}: {_brief(result)}")
    return parse_getent(kind, name, result.stdout)


def parse_getent(kind: RecordKind, name: str, output: bytes) -> dict[str, str]:
    """Fields of exactly one `getent passwd|group` line for `name`."""
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        lines = []
    parts = lines[0].split(":") if len(lines) == 1 else []
    want = 4 if kind == "group" else 7
    ids = parts[2:3] if kind == "group" else parts[2:4]
    if len(parts) != want or parts[0] != name or not all(map(_is_number, ids)):
        raise StepFailed("build-user", f"неразборный вывод getent: {output!r}")
    if kind == "group":
        return {"gid": parts[2]}
    return {"uid": parts[2], "gid": parts[3], "home": parts[5], "shell": parts[6]}


def _op_fields(
    platform: PlatformName, op: AttrOp, account: Account, layout: Layout
) -> dict[str, str]:
    value = op.value(account, layout)
    if platform == "macos-arm64":
        return {op.attr: value}
    return dict(line.split("=", 1) for line in value.splitlines())


# --- account identity ---------------------------------------------------------


def account_problems(account: Account, home: Path | None = None) -> list[str]:
    """Facts of the OS against the record, regardless of `state` (spec 3.2).

    With `home`, the record's home directory must also be that path.
    """
    try:
        entry = pwd.getpwnam(account.name)
    except KeyError:
        return [f"пользователя {account.name} нет"]
    problems = _uid_problems(account, entry)
    problems += _group_problems(account, entry)
    if entry.pw_shell not in NOLOGIN_SHELLS:
        problems.append(f"оболочка {entry.pw_shell} разрешает вход")
    if home is not None and Path(entry.pw_dir) != home:
        problems.append(f"домашний каталог {entry.pw_dir}, в намерении {home}")
    problems += _home_problems(account, Path(entry.pw_dir))
    rules = sudo_rules(account.name)
    if rules != "none":
        problems.append(f"sudo -l -U {account.name}: {rules}")
    return problems


def _uid_problems(account: Account, entry: pwd.struct_passwd) -> list[str]:
    problems: list[str] = []
    if entry.pw_uid != account.uid:
        problems.append(f"uid {entry.pw_uid}, записан {account.uid}")
    if entry.pw_uid == 0:
        problems.append("uid 0")
    others = sorted(
        {p.pw_name for p in pwd.getpwall() if p.pw_uid == entry.pw_uid} - {account.name}
    )
    if others:
        problems.append(f"uid {entry.pw_uid} есть и у {', '.join(others)}")
    return problems


def _group_problems(account: Account, entry: pwd.struct_passwd) -> list[str]:
    problems: list[str] = []
    if entry.pw_gid != account.gid:
        problems.append(f"основная группа gid {entry.pw_gid}, записан {account.gid}")
    try:
        primary = grp.getgrgid(entry.pw_gid).gr_name
    except KeyError:
        primary = None
    if primary != account.name:
        problems.append(f"основная группа {primary}, не {account.name}")
    groups = set(_getgrouplist(account.name, entry.pw_gid))
    admin = sorted(g for g in groups if g == 0 or _group_name(g) in ADMIN_GROUPS)
    if admin:
        problems.append(f"административные группы: {admin}")
    return problems


def _group_name(gid: int) -> str | None:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return None


def _home_problems(account: Account, home: Path) -> list[str]:
    try:
        st = os.lstat(home)
    except OSError as exc:
        return [f"домашний каталог {home}: {exc.strerror or exc}"]
    if not stat.S_ISDIR(st.st_mode):
        return [f"домашний каталог {home}: не каталог"]
    problems: list[str] = []
    if st.st_uid != account.uid:
        problems.append(f"домашний каталог {home}: владелец uid {st.st_uid}")
    if stat.S_IMODE(st.st_mode) != 0o700:
        problems.append(f"домашний каталог {home}: {stat.S_IMODE(st.st_mode):04o}")
    return problems


def verify_build_user(layout: Layout) -> list[str]:
    """Readiness: record present, `state = "ready"`, no `account_problems`."""
    try:
        account = load_accounts(layout.accounts).get(BUILD_USER)
    except ConfigError as exc:
        return [f"accounts.toml: {exc}"]
    if account is None:
        return [f"нет записи {BUILD_USER} в accounts.toml"]
    problems = [] if account.state == "ready" else [f"state = {account.state}"]
    return problems + account_problems(account, layout.build_home)


def create_build_user(layout: Layout) -> Account:
    """Create `df-build` by intent (spec 3.3 "Создание учётки"); resumable.

    The intent (`state = "creating"`) is written before the first OS
    command; a retry completes only our own partial records. The accepted
    home is settled before `ready`: a created one by `sync_dir(parent)` after
    `rename_noreplace`, one found by a retry by `settle_on_acquire` (Task 7).
    """
    platform = detect_platform()
    try:
        accounts = load_accounts(layout.accounts)
        account = accounts.get(BUILD_USER)
        if account is None:
            account = _write_intent(platform, layout, accounts)
        if account.state == "ready":
            return account
        _complete(platform, layout, account)
        problems = account_problems(account, layout.build_home)
        if problems:
            raise StepFailed("build-user", "; ".join(problems))
        ready = replace(account, state="ready")
        fsutil.write_atomic(
            layout.accounts, dump_accounts({**accounts, BUILD_USER: ready})
        )
        return ready
    except (OSError, ConfigError) as exc:
        raise StepFailed("build-user", str(exc)) from exc


def _write_intent(
    platform: PlatformName, layout: Layout, accounts: Mapping[str, Account]
) -> Account:
    records = (read_record("user", BUILD_USER), read_record("group", BUILD_USER))
    if any(record is not None for record in records):
        raise StepFailed(
            "build-user",
            f"пользователь {BUILD_USER} существует, но создан не darkfactory: "
            "удалите или переименуйте",
        )
    home = layout.build_home
    for path in (home, home.parent / HOME_TMP_NAME):
        if _exists(path):
            raise StepFailed(
                "build-user", f"каталог {path} существует, создан не darkfactory"
            )
    uid = _free_id(platform)
    created = datetime.now(UTC).isoformat(timespec="seconds")
    account = Account(BUILD_USER, uid, uid, "creating", created)
    fsutil.write_atomic(
        layout.accounts, dump_accounts({**accounts, BUILD_USER: account})
    )
    return account


def _exists(path: Path) -> bool:
    """Whether `path` is there; an unreadable path (loop, EACCES) counts as
    present, so the caller refuses it or the trusted-path walk names it."""
    try:
        _lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _free_id(platform: PlatformName) -> int:
    """The lowest id of the range free both as a UID and as a GID."""
    used = {p.pw_uid for p in pwd.getpwall()} | {g.gr_gid for g in grp.getgrall()}
    for n in ID_RANGE[platform]:
        if n not in used:
            return n
    raise StepFailed("build-user", f"нет свободного UID/GID в {ID_RANGE[platform]}")


def _complete(platform: PlatformName, layout: Layout, account: Account) -> None:
    """Under `creating`: refuse conflicts first, then fill records and home."""
    records = {kind: read_record(kind, account.name) for kind in ("group", "user")}
    ops = ACCOUNT_ATTRS[platform]
    for op in ops:
        _refuse_conflict(platform, op, account, layout, records[op.kind])
    _refuse_shared_ids(account)
    home_absent = _home_absent(layout, account)
    for op in ops:
        record = records[op.kind]
        fields = _op_fields(platform, op, account, layout)
        if record is not None and fields.keys() <= record.keys():
            continue
        _run_checked(attr_argv(platform, op, account, layout))
    if home_absent:
        _make_home(layout, account)


def _refuse_conflict(
    platform: PlatformName,
    op: AttrOp,
    account: Account,
    layout: Layout,
    record: Mapping[str, str] | None,
) -> None:
    if record is None:
        return
    for attr, want in _op_fields(platform, op, account, layout).items():
        have = record.get(attr)
        if have is not None and have != want:
            raise StepFailed(
                "build-user",
                f"{op.kind} {account.name}: {attr} = {have}, в намерении {want}",
            )


def _refuse_shared_ids(account: Account) -> None:
    users = {p.pw_name for p in pwd.getpwall() if p.pw_uid == account.uid}
    groups = {g.gr_name for g in grp.getgrall() if g.gr_gid == account.gid}
    for kind, names in (("uid", users), ("gid", groups)):
        others = sorted(names - {account.name})
        if others:
            value = account.uid if kind == "uid" else account.gid
            raise StepFailed(
                "build-user", f"{kind} {value} занят именем {', '.join(others)}"
            )


def _run_checked(argv: list[str]) -> None:
    result = run_command(argv)
    if not result.launched or result.returncode != 0:
        raise StepFailed("build-user", f"{' '.join(argv)}: {_brief(result)}")


def _home_absent(layout: Layout, account: Account) -> bool:
    """Whether the home must be created; a foreign home or reserved name refuses."""
    home = layout.build_home
    try:
        st = os.lstat(home)
    except FileNotFoundError:
        _clear_reserved(home.parent, account)
        return True
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != account.uid
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise StepFailed(
            "build-user", f"каталог {home} существует, создан не darkfactory"
        )
    return False


def _clear_reserved(parent: Path, account: Account) -> None:
    """Remove our empty reserved directory before any OS command.

    Ours: a directory (not a link) owned by root or the recorded UID; the name
    did not exist when the intent was written and only root writes `parent`.
    Anything else, or a non-empty one, refuses; root never lists inside it.
    """
    path = parent / HOME_TMP_NAME
    parent_fd = os.open(parent, _DIR_FLAGS)
    try:
        try:
            st = os.stat(HOME_TMP_NAME, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (ROOT_UID, account.uid):
            raise StepFailed(
                "build-user", f"каталог {path} существует, создан не darkfactory"
            )
        _remove_reserved(parent_fd, path)
    finally:
        os.close(parent_fd)


def _make_home(layout: Layout, account: Account) -> None:
    """Whole home under the reserved name, then rename without replacement."""
    home = layout.build_home
    parent_fd = os.open(home.parent, _DIR_FLAGS)
    try:
        os.mkdir(HOME_TMP_NAME, 0o700, dir_fd=parent_fd)
        fsutil.fault_point("created", home.parent / HOME_TMP_NAME)
        tmp_fd = os.open(HOME_TMP_NAME, _DIR_FLAGS, dir_fd=parent_fd)
        try:
            os.fchown(tmp_fd, account.uid, account.gid)
            fsutil.fault_point("chown", home.parent / HOME_TMP_NAME)
            os.fchmod(tmp_fd, 0o700)
            fsutil.sync_dir(tmp_fd)
        finally:
            os.close(tmp_fd)
        rename_noreplace(parent_fd, HOME_TMP_NAME, home.name)
        fsutil.fault_point("renamed", home)
        fsutil.sync_dir(parent_fd)
    finally:
        os.close(parent_fd)


def _remove_reserved(parent_fd: int, path: Path) -> None:
    """Remove our empty reserved directory; non-empty refuses."""
    try:
        os.rmdir(HOME_TMP_NAME, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            raise StepFailed(
                "build-user", f"каталог {path} существует, создан не darkfactory"
            ) from exc
        raise
    fsutil.fault_point("unlinked", path)


@functools.cache
def _rename_function() -> Callable[..., int] | None:
    lib = ctypes.CDLL(_LIBSYSTEM if _IS_DARWIN else None, use_errno=True)
    func = getattr(lib, "renameatx_np" if _IS_DARWIN else "renameat2", None)
    if func is None:
        return None
    func.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    func.argtypes += [ctypes.c_uint]
    func.restype = ctypes.c_int
    return func


def rename_noreplace(dir_fd: int, src: str, dst: str) -> None:
    """Rename `src` to `dst` within `dir_fd`, never replacing `dst`.

    macOS `renameatx_np(RENAME_EXCL)`, Linux `renameat2(RENAME_NOREPLACE)`.
    An existing `dst` or a missing call is `StepFailed`, never a plain rename.
    """
    func = _rename_function()
    if func is None:
        raise StepFailed("rename-noreplace", "переименование без замены недоступно")
    flag = _RENAME_EXCL if _IS_DARWIN else _RENAME_NOREPLACE
    ctypes.set_errno(0)
    if func(dir_fd, os.fsencode(src), dir_fd, os.fsencode(dst), flag) == 0:
        return
    err = ctypes.get_errno() or errno.EIO
    if err == errno.EEXIST:
        raise StepFailed("rename-noreplace", f"{dst} уже существует")
    raise StepFailed("rename-noreplace", f"{src} -> {dst}: {os.strerror(err)}")


# --- cleanup of builder trees -------------------------------------------------


def CLEANER_ARGV(root: Path) -> list[list[str]]:  # noqa: N802 - spec name
    """The three cleaner commands run as `df-build` (spec 3.3 "Очистка")."""
    return [
        [_CHMOD, "u+rwx", str(root)],
        [
            _FIND,
            str(root),
            "-mindepth",
            "1",
            "-type",
            "d",
            "!",
            "-perm",
            "-u+rwx",
            "-exec",
            _CHMOD,
            "u+rwx",
            "{}",
            ";",
        ],
        [_FIND, str(root), "-mindepth", "1", "-delete"],
    ]


def cleanup_tree(
    layout: Layout,
    parent: Path,
    name: str,
    *,
    quarantine_label: str | None,
    budget: float = 300.0,
) -> CleanupOutcome:
    """Remove the builder tree `parent/name` (protocol `cleanup_tree`).

    Root only stats, unlinks, rmdirs or renames the name relative to the
    held `parent` descriptor; the tree's content is touched only by the
    cleaner running as `df-build`.
    Effects (`tree`): `run_as(df-build)+` -> `unlink` -> `sync_dir(parent)`.
    Effects (`link`): `unlink` -> `sync_dir(parent)`.
    Effects (`quarantine`): `run_as(df-build)+` -> `renamed` ->
    `sync_dir(quarantine)` -> `sync_dir(parent)`.
    Effects (`absent`): none. Effects (`failed`): `run_as(df-build)+`.
    """
    _check_name(name, "cleanup")
    if quarantine_label is not None:
        _check_name(quarantine_label, "cleanup")
    deadline = time.monotonic() + budget
    parent_fd = _open_dir(parent, "cleanup")
    try:
        return _cleanup_at(layout, parent, parent_fd, name, quarantine_label, deadline)
    except OSError as exc:
        raise StepFailed("cleanup", f"{parent / name}: {exc}") from exc
    finally:
        os.close(parent_fd)


def _cleanup_at(
    layout: Layout,
    parent: Path,
    parent_fd: int,
    name: str,
    label: str | None,
    deadline: float,
) -> CleanupOutcome:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "removed"
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        fsutil.fault_point("unlinked", parent / name)
        fsutil.sync_dir(parent_fd)
        return "removed"
    if _run_cleaner(layout, CLEANER_ARGV(parent / name), deadline) and _rmdir_empty(
        parent_fd, name
    ):
        fsutil.fault_point("unlinked", parent / name)
        fsutil.sync_dir(parent_fd)
        return "removed"
    if label is None:
        return "failed"
    _quarantine(layout, parent_fd, name, label)
    fsutil.sync_dir(parent_fd)
    return "quarantined"


def _run_cleaner(layout: Layout, commands: list[list[str]], deadline: float) -> bool:
    for argv in commands:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        result = run_as(
            BUILD_USER,
            argv,
            env=_ROLE_ENV,
            cwd=layout.build_home,
            network=False,
            timeout=left,
        )
        if not result.launched or result.timed_out or result.returncode != 0:
            return False
    return True


def _rmdir_empty(parent_fd: int, name: str) -> bool:
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            return False
        raise
    return True


def _quarantine(layout: Layout, parent_fd: int, name: str, label: str) -> None:
    """Rename into the quarantine and settle it (before the parent)."""
    quarantine_fd = _open_dir(layout.quarantine, "cleanup")
    try:
        n = 1
        while _exists_at(quarantine_fd, f"{label}.{n}"):
            n += 1
        dst = f"{label}.{n}"
        os.rename(name, dst, src_dir_fd=parent_fd, dst_dir_fd=quarantine_fd)
        fsutil.fault_point("renamed", layout.quarantine / dst)
        fsutil.sync_dir(quarantine_fd)
    finally:
        os.close(quarantine_fd)


def _exists_at(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _check_name(name: str, step: str) -> None:
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise StepFailed(step, f"недопустимое имя: {name!r}")


def _open_dir(path: Path, step: str) -> int:
    try:
        return os.open(path, _DIR_FLAGS)
    except OSError as exc:
        raise StepFailed(step, f"{path}: {exc.strerror or exc}") from exc


def cleanup_build_home(
    layout: Layout, rel: str, *, budget: float = 300.0
) -> Literal["removed", "absent"]:
    """Remove `build_home/rel` entirely as `df-build` (protocol
    `cleanup_build_home`); root touches nothing there, nothing is synced.

    Effects (`tree`): `run_as(df-build)+`. Effects (`absent`):
    `run_as(df-build)+`.
    """
    _check_name(rel, "verify")
    if any(c in rel for c in _GLOB_CHARS):
        raise StepFailed("verify", f"имя с метасимволом find -name: {rel!r}")
    deadline = time.monotonic() + budget
    home = layout.build_home
    target = home / rel
    found = run_as(
        BUILD_USER,
        [_FIND, str(home), "-mindepth", "1", "-maxdepth", "1", "-name", rel, "-print"],
        env=_ROLE_ENV,
        cwd=home,
        network=False,
        timeout=budget,
    )
    if not found.launched or found.timed_out or found.returncode != 0:
        raise StepFailed("verify", f"поиск {target}: {_brief(found)}")
    if found.stdout == b"":
        return "absent"
    if found.stdout != f"{target}\n".encode():
        raise StepFailed(
            "verify", f"поиск {target}: неожиданный вывод {found.stdout!r}"
        )
    commands = CLEANER_ARGV(target)
    commands[2] = [_FIND, str(target), "-delete"]
    if not _run_cleaner(layout, commands, deadline):
        raise StepFailed("verify", f"очистка {target} от {BUILD_USER} не удалась")
    return "removed"


def retry_quarantine(layout: Layout) -> list[str]:
    """Clean every tree in our own quarantine; return the names left.

    A failure never stops the installation: the name stays for next time.
    """
    try:
        fd = os.open(layout.quarantine, _DIR_FLAGS)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StepFailed("cleanup", f"{layout.quarantine}: {exc.strerror}") from exc
    try:
        names = sorted(os.listdir(fd))
    except OSError as exc:
        raise StepFailed("cleanup", f"{layout.quarantine}: {exc.strerror}") from exc
    finally:
        os.close(fd)
    left: list[str] = []
    for name in names:
        try:
            outcome = cleanup_tree(
                layout, layout.quarantine, name, quarantine_label=None
            )
        except StepFailed:
            outcome = "failed"
        if outcome != "removed":
            left.append(name)
    return left


# --- native dependencies ----------------------------------------------------


@dataclass(frozen=True)
class LibDeps:
    """Native dependencies of a binary, its search paths and its loader.

    `loader`: ELF `PT_INTERP` / Mach-O `LC_LOAD_DYLINKER`; `dyld_environment`:
    Mach-O `LC_DYLD_ENVIRONMENT` entries (always a problem).
    """

    needed: tuple[str, ...]
    rpaths: tuple[str, ...]
    loader: str | None = None
    dyld_environment: tuple[str, ...] = ()


_OTOOL_DEP = re.compile(r"\t(.+?) \(compatibility version [^()]*\)")
_OTOOL_FIELD = re.compile(r"\s*(?:name|path) (.+) \(offset \d+\)")
#: `otool -l` load commands whose path field is read.
_OTOOL_PATH_CMDS = frozenset(
    {"LC_RPATH", "LC_LOAD_DYLINKER", "LC_DYLD_ENVIRONMENT", "LC_ID_DYLIB"}
)
_READELF_NEEDED = re.compile(r"\s*0x[0-9a-f]+ \(NEEDED\)\s+Shared library: \[(.+)\]")
_READELF_RPATH = re.compile(
    r"\s*0x[0-9a-f]+ \((?:RPATH|RUNPATH)\)\s+Library r(?:un)?path: \[(.*)\]"
)
_READELF_INTERP = re.compile(r"\s*\[Requesting program interpreter: (.+)\]")
_XCRUN = "/usr/bin/xcrun"
_XCRUN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
_NO_CLT = (
    "Command Line Tools не установлены (нужен otool): "
    "установите командой `xcode-select --install`"
)


def linked_libraries(path: Path) -> LibDeps | None:
    """Dependencies of a Mach-O (`otool -L`, `otool -l`) or ELF (`readelf -d`,
    `readelf -l`) file.

    Never `ldd`: glibc `ldd` may execute the file. The file must be regular;
    every inspection tool must pass `trusted_path_problems` before root runs
    it. Not a binary -> `None`; any failure -> `StepFailed("cli")`.
    """
    magic = _read_regular(path, 4)
    if magic in _MACHO_MAGIC:
        otool = _otool_path()
        loads = parse_otool_loads(_tool_output([otool, "-l", str(path)]))
        own = set(loads.get("LC_ID_DYLIB", ()))
        deps = parse_otool_deps(_tool_output([otool, "-L", str(path)]))
        dylinker = loads.get("LC_LOAD_DYLINKER", ())
        return LibDeps(
            tuple(d for d in deps if d not in own),
            loads.get("LC_RPATH", ()),
            dylinker[0] if dylinker else None,
            loads.get("LC_DYLD_ENVIRONMENT", ()),
        )
    if magic == _ELF_MAGIC:
        _require_trusted_tool(_READELF)
        dynamic = parse_readelf(_tool_output([_READELF, "-d", str(path)]))
        interp = parse_readelf_interp(_tool_output([_READELF, "-l", str(path)]))
        return replace(dynamic, loader=interp)
    return None


def _otool_path() -> str:
    """The real `otool` behind the Command Line Tools shim, if trusted.

    `/usr/bin/otool` only dispatches to the selected developer directory,
    which may belong to a user (a dragged-in Xcode); root runs the resolved
    binary, and only after it passes the trusted-path policy.
    """
    otool = xcrun_find("otool")
    _require_trusted_tool(otool)
    return otool


def xcrun_find(name: str) -> str:
    """The tool an xcode-select shim runs: trusted `xcrun --find <name>` with
    the minimal environment (never `DEVELOPER_DIR`); one absolute line."""
    _require_trusted_tool(_XCRUN)
    if not name or "/" in name:
        raise StepFailed("cli", f"недопустимое имя инструмента: {name!r}")
    result = run_command([_XCRUN, "--find", name], env=_XCRUN_ENV)
    if not result.launched or result.returncode != 0:
        if name == "otool":
            raise StepFailed("cli", _NO_CLT)
        raise StepFailed("cli", f"xcrun --find {name}: {_brief(result)}")
    try:
        lines = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        lines = []
    if len(lines) != 1 or not lines[0].startswith("/"):
        raise _unparsable(f"xcrun --find {name}", repr(result.stdout))
    return lines[0]


def _require_trusted_tool(tool: str) -> None:
    problems, target = _walk(Path(tool))
    final = _component(target) if target is not None else None
    if final is None or not stat.S_ISREG(final[0].st_mode):
        problems = [*problems, f"{target or tool}: не обычный файл"]
    if problems:
        detail = "; ".join(problems)
        raise StepFailed("cli", f"инструмент {tool} не доверен: {detail}")


def _read_regular(path: Path, limit: int) -> bytes:
    """Up to `limit` bytes of a regular file, opened without following a link
    or blocking; anything else (directory, FIFO, device) is `StepFailed`."""
    try:
        fd = os.open(path, _PEEK_FLAGS)
    except OSError as exc:
        raise StepFailed("cli", f"{path}: {exc.strerror or exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StepFailed("cli", f"{path}: не обычный файл")
        data = bytearray()
        while len(data) < limit:
            chunk = os.read(fd, limit - len(data))
            if not chunk:
                break
            data += chunk
        return bytes(data)
    except OSError as exc:
        raise StepFailed("cli", f"{path}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)


def _tool_output(argv: list[str]) -> str:
    result = run_command(argv)
    if b"xcrun: error" in result.stderr:
        raise StepFailed("cli", _NO_CLT)
    if not result.launched or result.returncode != 0:
        raise StepFailed("cli", f"{' '.join(argv)}: {_brief(result)}")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StepFailed("cli", f"{' '.join(argv)}: вывод не UTF-8") from exc


def _unparsable(tool: str, line: str) -> StepFailed:
    return StepFailed("cli", f"неразборный вывод {tool}: {line!r}")


def parse_otool_deps(text: str) -> tuple[str, ...]:
    """Install names from `otool -L`; header lines end with `:`."""
    found: list[str] = []
    for line in text.splitlines():
        if not line.startswith("\t"):
            if not line.endswith(":"):
                raise _unparsable("otool -L", line)
            continue
        match = _OTOOL_DEP.fullmatch(line)
        if match is None:
            raise _unparsable("otool -L", line)
        if match.group(1) not in found:
            found.append(match.group(1))
    return tuple(found)


def parse_otool_loads(text: str) -> dict[str, tuple[str, ...]]:
    """Path field of each `_OTOOL_PATH_CMDS` load command in `otool -l`.

    A listed command without a parsable `name`/`path` field is refused;
    other commands and fields are ignored.
    """
    found: dict[str, list[str]] = {}
    pending: str | None = None
    for line in text.splitlines():
        words = line.split()
        if words[:1] == ["cmd"] or words[:2] == ["Load", "command"]:
            if pending is not None:
                raise _unparsable("otool -l", f"{pending} без пути")
            pending = words[1] if words[:1] == ["cmd"] else None
            pending = pending if pending in _OTOOL_PATH_CMDS else None
            continue
        if pending is None or words[:1] not in (["name"], ["path"]):
            continue
        match = _OTOOL_FIELD.fullmatch(line)
        if match is None:
            raise _unparsable("otool -l", line)
        values = found.setdefault(pending, [])
        if match.group(1) not in values:
            values.append(match.group(1))
        pending = None
    if pending is not None:
        raise _unparsable("otool -l", f"{pending} без пути")
    return {k: tuple(v) for k, v in found.items()}


def parse_otool_rpaths(text: str) -> tuple[str, ...]:
    """`LC_RPATH` paths from `otool -l`."""
    return parse_otool_loads(text).get("LC_RPATH", ())


def parse_readelf(text: str) -> LibDeps:
    """`NEEDED` and `RPATH`/`RUNPATH` entries from `readelf -d`."""
    needed: list[str] = []
    rpaths: list[str] = []
    for line in text.splitlines():
        if "(NEEDED)" in line:
            match = _READELF_NEEDED.fullmatch(line)
            if match is None:
                raise _unparsable("readelf -d", line)
            needed.append(match.group(1))
        elif "(RPATH)" in line or "(RUNPATH)" in line:
            match = _READELF_RPATH.fullmatch(line)
            if match is None:
                raise _unparsable("readelf -d", line)
            rpaths += match.group(1).split(":")  # "" (cwd) is refused later
    return LibDeps(tuple(needed), tuple(rpaths))


def parse_readelf_interp(text: str) -> str | None:
    """`PT_INTERP` from `readelf -l`; `None` for a file without one."""
    found: list[str] = []
    for line in text.splitlines():
        if "Requesting program interpreter" not in line:
            continue
        match = _READELF_INTERP.fullmatch(line)
        if match is None:
            raise _unparsable("readelf -l", line)
        found.append(match.group(1))
    if len(set(found)) > 1:
        raise _unparsable("readelf -l", f"несколько интерпретаторов {found}")
    return found[0] if found else None


# --- trusted paths (spec 3.10) -------------------------------------------------


def trusted_path_problems(path: Path) -> list[str]:
    """Every component from `/` (links followed by their text): owner root,
    no group/other write on non-links, no ACL; more than 40 hops is a problem.
    """
    return _walk(path)[0]


def _walk(path: Path, *, missing_ok: bool = False) -> tuple[list[str], Path | None]:
    """Problems of every component, and the final target (`None` if unknown).

    With `missing_ok`, an absent component is not a problem by itself: its
    existing parent was checked, so only a trusted owner could create it.
    """
    if not path.is_absolute():
        return [f"{path}: путь не абсолютный"], None
    current = Path("/")
    root = _component(current)
    if root is None:
        return [f"{current}: не удалось прочитать"], None
    problems = root[1]
    pending = list(path.parts[1:])
    hops = 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        found = _component(candidate)
        if found is None:
            if missing_ok and not _exists(candidate):
                return problems, None
            return [*problems, f"{candidate}: не существует или не читается"], None
        problems += found[1]
        if not stat.S_ISLNK(found[0].st_mode):
            current = candidate
            continue
        hops += 1
        if hops > _MAX_HOPS:
            return [*problems, f"{path}: больше {_MAX_HOPS} переходов по ссылкам"], None
        try:
            target = Path(_readlink(candidate))
        except OSError as exc:
            return [*problems, f"{candidate}: {exc.strerror or exc}"], None
        if target.is_absolute():
            current = Path("/")
            pending = list(target.parts[1:]) + pending
        else:
            pending = list(target.parts) + pending
    return problems, current


def _component(path: Path) -> tuple[os.stat_result, list[str]] | None:
    """`lstat` of one component and its problems; `None` if it cannot be read."""
    try:
        st = _lstat(path)
    except OSError:
        return None
    problems: list[str] = []
    if st.st_uid != ROOT_UID:
        problems.append(f"{path}: владелец uid {st.st_uid}, не root")
    if not stat.S_ISLNK(st.st_mode) and st.st_mode & _NO_WRITE_OTHERS:
        problems.append(f"{path}: запись для группы или всех")
    try:
        if acl_present_path(path):
            problems.append(f"{path}: есть ACL")
    except OSError as exc:
        problems.append(f"{path}: ACL не установлен ({exc.strerror or exc})")
    return st, problems


def approved_cli_problems(cli: Mapping[str, Path], platform: PlatformName) -> list[str]:
    """Policy of spec 3.10 for every approved CLI; empty means all pass.

    No loader model (ruling R26). Only the two supported ways of spec 3.10
    pass: a system CLI, or a static / system-linked copy in a root directory.
    - The program (its link target included) passes `trusted_path_problems`
      and is a regular file; a `#!` interpreter passes the same policy.
    - Its loader (`PT_INTERP` / `LC_LOAD_DYLINKER`) lies in `SYSTEM_LIB_DIRS`;
      `LC_DYLD_ENVIRONMENT` is a problem.
    - Search paths are a problem: any `RPATH`/`RUNPATH`/`LC_RPATH`, any
      dependency spelled with `@rpath`, `@loader_path`, `@executable_path` or
      containing `$`, and on macOS a dependency name without `/`.
    - macOS: every dependency is an absolute path under `SYSTEM_LIB_DIRS`
      (absent on disk is fine: dyld shared cache). A program linking
      `libxcselect` is a developer shim: the tool it dispatches to
      (`xcrun --find <name>`) passes the same policy under the shim's name.
    - Linux: every bare soname exists in a `LOADER_DIRS` directory; the host
      loader configuration (`/etc/ld.so.conf` with its includes, include
      glob prefixes, `/etc/ld.so.preload`) names only system or trusted
      paths; anything unparsable is a problem.

    Trust boundary: system libraries are part of the OS and are not walked
    further. Named residuals (Task 13): glibc-hwcaps / legacy hwcap
    subdirectories of the loader directories, and `ld.so.cache` entries that
    are not in `ld.so.conf`. Each refused CLI gets the supported way.
    """
    problems = [f"загрузчик ОС: {p}" for p in _host_loader_problems(platform)]
    for name, path in sorted(cli.items()):
        found = _cli_problems(path, platform, 0)
        problems += [f"{name}: {p}" for p in found]
        if found:
            problems.append(f"{name}: {_INSTALL_HINT.format(name=name)}")
    return problems


def _cli_problems(path: Path, platform: PlatformName, depth: int) -> list[str]:
    problems, target = _walk(path)
    if target is None:
        return problems
    final = _component(target)
    if final is None or not stat.S_ISREG(final[0].st_mode):
        return [*problems, f"{target}: не обычный файл"]
    try:
        problems += _binary_problems(target, platform, depth)
        problems += _shebang_problems(target, platform, depth)
    except StepFailed as exc:
        problems.append(exc.detail)
    return problems


def _binary_problems(target: Path, platform: PlatformName, depth: int) -> list[str]:
    deps = linked_libraries(target)
    if deps is None:
        return []
    problems = [
        f"{target}: LC_DYLD_ENVIRONMENT {e} запрещён" for e in deps.dyld_environment
    ]
    if deps.loader is not None and not _is_system(Path(deps.loader), platform):
        problems.append(f"{target}: загрузчик {deps.loader} вне системных каталогов")
    problems += [f"{target}: путь поиска {r!r} запрещён" for r in deps.rpaths]
    for dep in deps.needed:
        problems += _dependency_problems(target, dep, platform)
    if platform == "macos-arm64" and XCSELECT_LIB in deps.needed:
        problems += _shim_problems(target, platform, depth)
    return problems


def _dependency_problems(target: Path, dep: str, platform: PlatformName) -> list[str]:
    if dep.startswith("@") or "$" in dep:
        return [f"{target}: зависимость {dep}: пути поиска не поддерживаются"]
    if platform == "macos-arm64" and "/" not in dep:
        return [f"{target}: зависимость {dep}: относительное имя, ищется от cwd"]
    if "/" in dep:
        if dep.startswith("/") and _is_system(Path(dep), platform):
            return []
        return [f"{target}: зависимость {dep} вне системных каталогов"]
    if any(_exists(Path(d) / dep) for d in LOADER_DIRS[platform]):
        return []
    return [f"{target}: зависимость {dep} не найдена в системных каталогах"]


def _shim_problems(target: Path, platform: PlatformName, depth: int) -> list[str]:
    """The tool an xcode-select shim dispatches to passes the same policy."""
    if depth >= _MAX_SHEBANG_DEPTH:
        return [f"{target}: слишком длинная цепочка xcode-select"]
    try:
        resolved = xcrun_find(target.name)
    except StepFailed as exc:
        return [f"{target}: xcode-select: {exc.detail}"]
    if Path(resolved) == target:
        return [f"{target}: xcode-select указывает на сам shim"]
    nested = _cli_problems(Path(resolved), platform, depth + 1)
    return [f"{target}: xcode-select → {p}" for p in nested]


def _is_system(path: Path, platform: PlatformName) -> bool:
    """Under a system directory; a path with `..` never is (links make `..`
    physical, so only the trusted-path walk may resolve it)."""
    if ".." in path.parts:
        return False
    return any(f"{path}/".startswith(d) for d in SYSTEM_LIB_DIRS[platform])


def _location_problems(path: Path, platform: PlatformName) -> list[str]:
    if _is_system(path, platform):
        return []
    return _walk(path, missing_ok=True)[0]


def _host_loader_problems(platform: PlatformName) -> list[str]:
    """Linux: `ld.so.conf` directories and `ld.so.preload` entries."""
    if platform != "linux-x86_64":
        return []
    problems: list[str] = []
    dirs: list[Path] = []
    _read_ld_conf(LD_SO_CONF, dirs, problems, set(), 0)
    for directory in dict.fromkeys(dirs):
        problems += _location_problems(directory, platform)
    return problems + _preload_problems(platform)


def _read_conf_text(path: Path, problems: list[str]) -> str | None:
    """A trusted, regular, bounded UTF-8 file; failures become problems."""
    problems += trusted_path_problems(path)
    try:
        data = _read_regular(path, _CONF_LIMIT + 1)
        text = data.decode("utf-8")
    except (StepFailed, UnicodeDecodeError) as exc:
        problems.append(f"{path}: не прочитан ({exc})")
        return None
    if len(data) > _CONF_LIMIT:
        problems.append(f"{path}: слишком большой")
        return None
    return text


def _read_ld_conf(
    path: Path, dirs: list[Path], problems: list[str], seen: set[Path], depth: int
) -> None:
    if path in seen or depth == 0 and not _exists(path):
        return
    seen.add(path)
    if depth > _MAX_CONF_DEPTH:
        problems.append(f"{path}: слишком глубокие include")
        return
    text = _read_conf_text(path, problems)
    if text is None:
        return
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        words = line.split()
        if not words:
            continue
        if words[0] == "include":
            for pattern in words[1:]:
                full = (
                    Path(pattern) if pattern.startswith("/") else path.parent / pattern
                )
                problems += _location_problems(_glob_prefix(full), "linux-x86_64")
                for match in sorted(_glob(str(full))):
                    _read_ld_conf(Path(match), dirs, problems, seen, depth + 1)
            continue
        for item in re.split(r"[\s,:]+", line):
            if not item.startswith("/") or "=" in item or "$" in item:
                problems.append(f"{path}: строка не разобрана: {raw!r}")
                break
            dirs.append(Path(item))


def _glob_prefix(pattern: Path) -> Path:
    """The directory part of an include glob before its first wildcard."""
    prefix = Path("/")
    for part in pattern.parts[1:-1]:
        if any(c in part for c in "*?["):
            break
        prefix /= part
    return prefix


def _preload_problems(platform: PlatformName) -> list[str]:
    if not _exists(LD_SO_PRELOAD):
        return []
    problems: list[str] = []
    text = _read_conf_text(LD_SO_PRELOAD, problems)
    if text is None:
        return problems
    for item in re.split(r"[\s:]+", text.strip()) if text.strip() else []:
        if not item.startswith("/") or "$" in item:
            problems.append(f"{LD_SO_PRELOAD}: запись не разобрана: {item!r}")
            continue
        problems += _location_problems(Path(item), platform)
    return problems


def _shebang_problems(target: Path, platform: PlatformName, depth: int) -> list[str]:
    head = _read_regular(target, 256)
    if not head.startswith(b"#!"):
        return []
    words = os.fsdecode(head[2:].split(b"\n", 1)[0]).split()
    if not words:
        return [f"{target}: пустая строка #!"]
    interpreter = words[0]
    if os.path.basename(interpreter) == "env":
        return [f"{target}: #!{interpreter} запрещён (интерпретатор ищется по PATH)"]
    if not interpreter.startswith("/"):
        return [f"{target}: интерпретатор {interpreter} не абсолютный"]
    if depth >= _MAX_SHEBANG_DEPTH:
        return [f"{target}: слишком длинная цепочка #!"]
    nested = _cli_problems(Path(interpreter), platform, depth + 1)
    return [f"{target}: интерпретатор {p}" for p in nested]
