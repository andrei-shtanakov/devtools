"""Unit tests of df.platform (no root; system accounts only through fakes)."""

from __future__ import annotations

import fnmatch
import os
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from df import fsutil
from df import platform as plat
from df.config import Account, ConfigError, dump_accounts, load_accounts
from df.digest import TreeError
from df.install_table import (
    CLEANUP_OUTCOME,
    CLEANUP_TREE_CASES,
    cleanup_tree_variant,
    leaf_variants,
)
from df.journal import StepFailed
from df.paths import Layout
from df.platform import (
    ACCOUNT_ATTRS,
    CLEANER_ARGV,
    HOME_TMP_NAME,
    NO_NETWORK_PROFILE,
    TAR_FLAGS,
    LibDeps,
    PlatformName,
    RunResult,
)
from df.receive import CopyLimits, receive_tree
from tests.df.producers import TAR, LocalProducer

FIXTURES = Path(__file__).parent / "fixtures"
MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
PLATFORMS: list[PlatformName] = ["macos-arm64", "linux-x86_64"]


class Interrupted(Exception):
    """Simulated process death inside `create_build_user`."""


def ok(stdout: bytes = b"", code: int = 0) -> RunResult:
    return RunResult(code, stdout, b"", launched=True)


@pytest.fixture()
def layout(tmp_path: Path) -> Layout:
    root = Path(os.path.realpath(tmp_path)) / "root"
    lay = Layout(root)
    lay.install_dir.mkdir(parents=True)
    lay.build_home.parent.mkdir(parents=True)
    lay.quarantine.mkdir(parents=True)
    return lay


# --- platform, tar flags, cleaner argv --------------------------------------


def test_detect_platform_refuses_linux_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    uname = SimpleNamespace(sysname="Linux", machine="aarch64")
    monkeypatch.setattr(plat, "_uname", lambda: uname)
    with pytest.raises(ConfigError, match="aarch64"):
        plat.detect_platform()


@pytest.mark.parametrize(
    "sysname,machine,expected",
    [("Darwin", "arm64", "macos-arm64"), ("Linux", "x86_64", "linux-x86_64")],
)
def test_detect_platform_supported(
    monkeypatch: pytest.MonkeyPatch, sysname: str, machine: str, expected: str
) -> None:
    uname = SimpleNamespace(sysname=sysname, machine=machine)
    monkeypatch.setattr(plat, "_uname", lambda: uname)
    assert plat.detect_platform() == expected


def test_tar_flags_exact_per_platform() -> None:
    assert dict(TAR_FLAGS) == {
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


def test_cleaner_argv_exact() -> None:
    root = Path("/opt/df/staging/x/tree")
    assert CLEANER_ARGV(root) == [
        ["/bin/chmod", "u+rwx", str(root)],
        [
            "/usr/bin/find",
            str(root),
            "-mindepth",
            "1",
            "-type",
            "d",
            "!",
            "-perm",
            "-u+rwx",
            "-exec",
            "/bin/chmod",
            "u+rwx",
            "{}",
            ";",
        ],
        ["/usr/bin/find", str(root), "-mindepth", "1", "-delete"],
    ]


def _locked_tree(root: Path) -> None:
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "d").mkdir()
    (root / "a" / "b" / "c" / "f").write_bytes(b"x")
    (root / "d" / "g").write_bytes(b"y")
    for path in (root / "a/b/c", root / "a/b", root / "d", root):
        path.chmod(0)


def _unlock(base: Path) -> None:
    subprocess.run(["/bin/chmod", "-R", "u+rwx", str(base)], check=False)


R3_CLEANER = [
    [
        "/usr/bin/find",
        "{root}",
        "-mindepth",
        "1",
        "-type",
        "d",
        "!",
        "-perm",
        "-u+rwx",
        "-exec",
        "/bin/chmod",
        "u+rwx",
        "{}",
        "+",
    ],
    ["/usr/bin/find", "{root}", "-mindepth", "1", "-delete"],
]


@pytest.mark.parametrize("version", ["r4", "r3-twin"])
def test_cleaner_argv_removes_000_tree(tmp_path: Path, version: str) -> None:
    root = tmp_path / "p" / "t"
    _locked_tree(root)
    if version == "r4":
        commands = CLEANER_ARGV(root)
    else:
        commands = [[str(root) if a == "{root}" else a for a in c] for c in R3_CLEANER]
    try:
        codes = [
            subprocess.run(c, capture_output=True, check=False).returncode
            for c in commands
        ]
        try:
            os.rmdir(root)
        except OSError:
            pass
        removed = not os.path.lexists(root)
    finally:
        _unlock(tmp_path / "p")
    if version == "r4":
        assert codes == [0, 0, 0]
        assert removed
    else:
        assert not removed


# --- run_as / spawn_as -------------------------------------------------------


def _capture_execute(
    monkeypatch: pytest.MonkeyPatch, platform: PlatformName, result: RunResult
) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        return result

    monkeypatch.setattr(plat, "detect_platform", lambda: platform)
    monkeypatch.setattr(plat, "_execute", fake)
    return seen


def test_run_as_argv_macos_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_execute(monkeypatch, "macos-arm64", ok())
    plat.run_as(
        "df-build",
        ["/usr/bin/id", "-u"],
        env={"PATH": "/usr/bin:/bin"},
        cwd=Path("/"),
        network=False,
        timeout=5,
    )
    assert seen == [
        [
            "/usr/bin/sudo",
            "-n",
            "-u",
            "df-build",
            "/usr/bin/sandbox-exec",
            "-p",
            NO_NETWORK_PROFILE,
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "/usr/bin/id",
            "-u",
        ]
    ]
    assert NO_NETWORK_PROFILE == "(version 1)(allow default)(deny network*)"


def test_run_as_argv_linux_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_execute(monkeypatch, "linux-x86_64", ok())
    plat.run_as(
        "df-build", ["/usr/bin/id"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    assert seen == [
        [
            "/usr/bin/unshare",
            "--net",
            "--",
            "/usr/bin/setpriv",
            "--reuid=df-build",
            "--regid=df-build",
            "--init-groups",
            "--",
            "/usr/bin/env",
            "-i",
            "/usr/bin/id",
        ]
    ]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_run_as_with_network_has_no_sandbox(
    monkeypatch: pytest.MonkeyPatch, platform: PlatformName
) -> None:
    seen = _capture_execute(monkeypatch, platform, ok())
    plat.run_as("u", ["/bin/true"], env={}, cwd=Path("/"), network=True, timeout=5)
    assert "/usr/bin/sandbox-exec" not in seen[0]
    assert "/usr/bin/unshare" not in seen[0]


def test_run_as_env_is_exactly_given() -> None:
    inner = plat._env_argv(["/usr/bin/env"], {"A": "1", "B": "two words"})
    assert inner[:2] == ["/usr/bin/env", "-i"]
    out = subprocess.run(inner, capture_output=True, env={"LEAK": "x"}, check=True)
    assert out.stdout.decode().splitlines() == ["A=1", "B=two words"]


@pytest.mark.parametrize("argv", [["id"], ["/a=b"], []])
def test_run_as_refuses_relative_or_assignment_command(argv: list[str]) -> None:
    with pytest.raises(StepFailed):
        plat._env_argv(argv, {})


def test_run_as_reports_launch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(plat, "_SUDO", "/nonexistent/sudo")
    result = plat.run_as(
        "df-build", ["/usr/bin/id"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    assert not result.launched


@pytest.mark.parametrize(
    "code,stderr,launched",
    [
        (1, b"sudo: a password is required\n", False),
        (1, b"setpriv: setresuid failed\n", False),
        (65, b"sandbox-exec: invalid profile\n", False),
        (127, b"env: /x: No such file or directory\n", False),
        (1, b"find: /x: Permission denied\n", True),
        (2, b"sudo: looks like the mechanism, but not its code\n", True),
    ],
)
def test_run_as_launch_mechanism_codes(
    monkeypatch: pytest.MonkeyPatch, code: int, stderr: bytes, launched: bool
) -> None:
    _capture_execute(monkeypatch, "macos-arm64", RunResult(code, b"", stderr, True))
    result = plat.run_as(
        "df-build", ["/usr/bin/true"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    assert result.launched is launched


def test_execute_timeout_kills_group(tmp_path: Path) -> None:
    started = time.monotonic()
    result = plat._execute(
        ["/bin/sh", "-c", "sleep 30 & sleep 30"],
        env={},
        cwd=tmp_path,
        timeout=0.5,
        limit=1024,
    )
    assert result.timed_out and result.returncode != 0
    assert time.monotonic() - started < 15


def test_execute_stdout_limit_kills(tmp_path: Path) -> None:
    result = plat._execute(
        ["/bin/sh", "-c", "yes"], env={}, cwd=tmp_path, timeout=20, limit=4096
    )
    assert result.returncode != 0
    assert len(result.stdout) == 4096


def test_whoami_as_requires_launch_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {"-u": ok(b"401\n"), "-g": ok(b"402\n")}
    monkeypatch.setattr(plat, "run_as", lambda _u, argv, **_kw: answers[argv[1]])
    assert plat.whoami_as("df-build") == (401, 402)
    answers["-g"] = RunResult(0, b"402\n", b"", launched=False)
    with pytest.raises(StepFailed):
        plat.whoami_as("df-build")
    answers["-g"] = ok(b"")
    with pytest.raises(StepFailed):
        plat.whoami_as("df-build")


class FakePopen:
    """Records Popen arguments; real pipes stand in for the chain's streams.

    `stdout_data` is pending on stdout (the writer stays open: a running
    chain); `stderr_data` is the whole stderr stream (then EOF).
    """

    instances: list[FakePopen] = []
    stdout_data = b"x"
    stderr_data = b""
    exits_at_once: int | None = None

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 4242
        out_r, self._out_w = os.pipe()
        err_r, err_w = os.pipe()
        os.write(self._out_w, self.stdout_data)
        os.write(err_w, self.stderr_data)
        os.close(err_w)
        self.stdout = os.fdopen(out_r, "rb")
        self.stderr = os.fdopen(err_r, "rb")
        self.waits: list[float] = []
        self.exit_on_wait: int | None = self.exits_at_once
        self.returncode: int | None = None
        FakePopen.instances.append(self)

    def poll(self) -> int | None:
        return self.exits_at_once

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout or 0.0)
        if self.exit_on_wait is None:
            raise subprocess.TimeoutExpired(self.argv, timeout or 0.0)
        self.returncode = self.exit_on_wait
        if self._out_w >= 0:
            os.close(self._out_w)
            self._out_w = -1
        return self.exit_on_wait


def test_spawn_as_new_session_no_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    FakePopen.instances.clear()
    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    prod = plat.spawn_as(
        "df-build", ["/usr/bin/tar", "-cf", "-"], env={}, cwd=Path("/"), network=False
    )
    proc = FakePopen.instances[0]
    assert proc.kwargs["start_new_session"] is True
    assert proc.kwargs["stdin"] is subprocess.DEVNULL
    assert proc.kwargs["stdout"] is subprocess.PIPE
    assert proc.argv[:4] == ["/usr/bin/sudo", "-n", "-u", "df-build"]
    assert prod.fd == proc.stdout.fileno()
    with pytest.raises(TreeError):
        prod.wait(0.1)


def test_spawn_as_refuses_mechanism_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing(FakePopen):
        stdout_data = b""
        stderr_data = b"sudo: a password is required\n"
        exits_at_once = 1

    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(subprocess, "Popen", Failing)
    monkeypatch.setattr(os, "killpg", lambda _p, _s: None)
    monkeypatch.setattr(plat, "reap_user_processes", lambda _u: [])
    with pytest.raises(StepFailed, match="password"):
        plat.spawn_as(
            "df-build", ["/usr/bin/tar"], env={}, cwd=Path("/"), network=False
        )


@pytest.mark.parametrize(
    "killpg_error", [None, ProcessLookupError(), PermissionError()]
)
@pytest.mark.parametrize("left", [[], [777]])
def test_spawned_producer_end_kills_group_and_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    killpg_error: OSError | None,
    left: list[int],
) -> None:
    calls: list[str] = []
    proc = FakePopen(["tar"])
    proc.exit_on_wait = 0

    def killpg(pgid: int, sig: int) -> None:
        calls.append(f"killpg({pgid},{sig})")
        if killpg_error is not None:
            raise killpg_error

    def reap(user: str) -> list[int]:
        calls.append(f"reap({user})")
        return left

    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(plat, "reap_user_processes", reap)
    prod = plat.SpawnedProducer(proc, "df-build", 7.0)  # type: ignore[arg-type]
    if left:
        with pytest.raises(TreeError, match="777"):
            prod.end()
    else:
        prod.end()
    assert calls == [f"killpg(4242,{int(plat.signal.SIGKILL)})", "reap(df-build)"]
    assert proc.waits == [7.0]
    assert proc.stdout.closed and proc.stderr.closed


def test_spawned_producer_end_unreaped_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = FakePopen(["tar"])
    monkeypatch.setattr(os, "killpg", lambda _p, _s: None)
    monkeypatch.setattr(plat, "reap_user_processes", lambda _u: [])
    prod = plat.SpawnedProducer(
        proc,  # type: ignore[arg-type]
        "df-build",
        0.1,
    )
    with pytest.raises(TreeError, match="not reaped"):
        prod.end()


def test_reap_returns_leftovers_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    ps = {"out": b"  12   401 S\n  13   401 Z\n  14   0 S\n"}

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        calls.append(list(argv))
        if argv[0] == "/bin/ps":
            return ok(ps["out"])
        return ok(code=1)

    entry = SimpleNamespace(pw_uid=401)
    monkeypatch.setattr(plat, "pwd", SimpleNamespace(getpwnam=lambda _n: entry))
    monkeypatch.setattr(plat, "run_command", run)
    assert plat.reap_user_processes("df-build", timeout=0.2) == [12]
    assert calls[0] == ["/usr/bin/pkill", "-KILL", "-u", "df-build"]
    ps["out"] = b"garbage line here and more\n"
    assert plat.reap_user_processes("df-build", timeout=0.1) == [plat.UNKNOWN_PID]
    ps["out"] = b"  13   401 Z+\n"
    assert plat.reap_user_processes("df-build", timeout=0.1) == []


# --- sudo, parsers -----------------------------------------------------------


def _fixture(name: str) -> tuple[int, bytes]:
    base = FIXTURES / "sudo-l"
    code = int((base / f"{name}.rc").read_text().strip())
    return code, (base / f"{name}.out").read_bytes()


# `linux-some` is the CI runner's own capture (user `runner`), R74.
@pytest.mark.parametrize(
    "fixture,user,expected",
    [
        ("macos-none", "df-build", "none"),
        ("linux-none", "df-build", "none"),
        ("linux-some", "runner", "some"),
        ("garbage", "df-build", "unknown"),
        ("exit-1", "df-build", "unknown"),
    ],
)
def test_sudo_rules_parsing(
    monkeypatch: pytest.MonkeyPatch, fixture: str, user: str, expected: str
) -> None:
    code, out = _fixture(fixture)
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        return ok(out, code)

    monkeypatch.setattr(plat, "run_command", run)
    assert plat.sudo_rules(user) == expected
    assert seen == [["/usr/bin/sudo", "-n", "-l", "-U", user]]


def test_sudo_rules_other_name_or_launch_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _code, out = _fixture("macos-none")
    assert plat.parse_sudo_rules("df-exec", 0, out) == "unknown"
    failed = RunResult(127, b"", b"", launched=False)
    monkeypatch.setattr(plat, "run_command", lambda _a, **_k: failed)
    assert plat.sudo_rules("df-build") == "unknown"


def test_parse_otool_and_readelf_samples() -> None:
    base = FIXTURES / "libdeps"
    deps = plat.parse_otool_deps((base / "otool-deps.txt").read_text())
    assert deps == (
        "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation",
        "@rpath/libpython3.10.dylib",
        "/usr/lib/libSystem.B.dylib",
    )
    loads = plat.parse_otool_loads((base / "otool-loadcmds.txt").read_text())
    assert loads == {
        "LC_LOAD_DYLINKER": ("/usr/lib/dyld",),
        "LC_RPATH": ("@executable_path/../lib",),
    }
    interp = plat.parse_readelf_interp((base / "readelf-l.txt").read_text())
    assert interp == "/lib64/ld-linux-x86-64.so.2"
    assert plat.parse_readelf_interp("no program headers\n") is None
    assert plat.parse_readelf((base / "readelf-d.txt").read_text()) == LibDeps(
        ("libpcre2-8.so.0", "libz.so.1", "libc.so.6"),
        ("/opt/a/lib", "/opt/b/lib", "$ORIGIN/../lib"),
    )


def test_linked_libraries_not_a_binary(tmp_path: Path) -> None:
    script = tmp_path / "s"
    script.write_text("#!/bin/sh\n")
    assert plat.linked_libraries(script) is None


@MACOS_ONLY
def test_linked_libraries_real_macho() -> None:
    if not Path("/Library/Developer/CommandLineTools/usr/bin/otool-classic").exists():
        pytest.skip("otool needs the Command Line Tools")
    deps = plat.linked_libraries(Path("/usr/bin/git"))
    assert deps is not None
    assert "/usr/lib/libSystem.B.dylib" in deps.needed


# --- fake world of system accounts -------------------------------------------


POLICY_BLOB = plistlib.dumps({"creationTime": 1.0}).decode()
#: Attributes macOS stores as native (`dscl -plist . -read /Users/_sentinel`).
NATIVE_ATTRS = frozenset({"IsHidden"})


def _ds_prefix(attr: str) -> str:
    return "dsAttrTypeNative:" if attr in NATIVE_ATTRS else "dsAttrTypeStandard:"


@dataclass
class World:
    """System accounts as seen through `run_command` and pwd/grp."""

    platform: PlatformName
    users: dict[str, dict[str, str]] = field(default_factory=dict)
    groups: dict[str, dict[str, str]] = field(default_factory=dict)
    others: list[SimpleNamespace] = field(default_factory=list)
    other_groups: list[SimpleNamespace] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    sudo: tuple[int, bytes] = (0, b"User df-build is not allowed to run sudo on h.\n")
    extra_groups: tuple[int, ...] = ()
    stop_after_command: int | None = None

    # -- commands --------------------------------------------------------------
    def run(self, argv: Sequence[str], **_kw: object) -> RunResult:
        argv = list(argv)
        if argv[0] == "/usr/bin/sudo":
            return ok(self.sudo[1], self.sudo[0])
        if argv[:4] == ["/usr/bin/dscl", "-plist", ".", "-read"]:
            return self._dscl_read(argv[4])
        if argv[0] == "/usr/bin/getent":
            return self._getent(argv[1], argv[2])
        self.commands.append(argv)
        self._apply(argv)
        if self.stop_after_command == len(self.commands):
            raise Interrupted(f"after {argv}")
        return ok()

    def _table(self, path: str) -> dict[str, dict[str, str]]:
        return self.groups if path.startswith("/Groups/") else self.users

    def _dscl_read(self, path: str) -> RunResult:
        record = self._table(path).get(path.rsplit("/", 1)[1])
        if record is None:
            return RunResult(56, b"", b"DS Error: -14136 (eDSRecordNotFound)", True)
        data = {f"{_ds_prefix(k)}{k}": [v] for k, v in record.items()}
        data["dsAttrTypeNative:accountPolicyData"] = [POLICY_BLOB]
        return ok(plistlib.dumps(data))

    def _getent(self, db: str, name: str) -> RunResult:
        table = self.groups if db == "group" else self.users
        record = table.get(name)
        if record is None:
            return ok(code=2)
        if db == "group":
            return ok(f"{name}:x:{record['gid']}:\n".encode())
        r = record
        line = f"{name}:x:{r['uid']}:{r['gid']}::{r['home']}:{r['shell']}\n"
        return ok(line.encode())

    def _apply(self, argv: list[str]) -> None:
        if argv[0] == "/usr/bin/dscl":
            path = argv[3]
            record = self._table(path).setdefault(path.rsplit("/", 1)[1], {})
            name = path.rsplit("/", 1)[1]
            record.setdefault("RecordName", name)
            if len(argv) == 6:
                record[argv[4]] = argv[5]
            return
        if argv[0] == "/usr/sbin/groupadd":
            self.groups[argv[-1]] = {"gid": argv[3]}
            return
        opts = {a: argv[i + 1] for i, a in enumerate(argv[:-1]) if a.startswith("--")}
        gid = self.groups[opts["--gid"]]["gid"]
        self.users[argv[-1]] = {
            "uid": opts["--uid"],
            "gid": gid,
            "home": opts["--home-dir"],
            "shell": "/usr/sbin/nologin",
        }

    # -- pwd / grp -------------------------------------------------------------
    def _user_entry(self, name: str) -> SimpleNamespace | None:
        r = self.users.get(name)
        if r is None:
            return None
        if self.platform == "macos-arm64":
            if "UniqueID" not in r:
                return None
            return SimpleNamespace(
                pw_name=name,
                pw_uid=int(r["UniqueID"]),
                pw_gid=int(r.get("PrimaryGroupID", "-1")),
                pw_dir=r.get("NFSHomeDirectory", "/var/empty"),
                pw_shell=r.get("UserShell", "/bin/sh"),
            )
        return SimpleNamespace(
            pw_name=name,
            pw_uid=int(r["uid"]),
            pw_gid=int(r["gid"]),
            pw_dir=r["home"],
            pw_shell=r["shell"],
        )

    def _group_entries(self) -> list[SimpleNamespace]:
        key = "PrimaryGroupID" if self.platform == "macos-arm64" else "gid"
        own = [
            SimpleNamespace(gr_name=n, gr_gid=int(r[key]))
            for n, r in self.groups.items()
            if key in r
        ]
        return own + self.other_groups

    def getpwall(self) -> list[SimpleNamespace]:
        own = [e for n in self.users if (e := self._user_entry(n)) is not None]
        return own + self.others

    def getpwnam(self, name: str) -> SimpleNamespace:
        for entry in self.getpwall():
            if entry.pw_name == name:
                return entry
        raise KeyError(name)

    def getgrgid(self, gid: int) -> SimpleNamespace:
        for entry in self._group_entries():
            if entry.gr_gid == gid:
                return entry
        raise KeyError(gid)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(plat, "detect_platform", lambda: self.platform)
        monkeypatch.setattr(plat, "run_command", self.run)
        monkeypatch.setattr(
            plat,
            "pwd",
            SimpleNamespace(getpwnam=self.getpwnam, getpwall=self.getpwall),
        )
        monkeypatch.setattr(
            plat,
            "grp",
            SimpleNamespace(getgrgid=self.getgrgid, getgrall=self._group_entries),
        )
        monkeypatch.setattr(
            plat, "_getgrouplist", lambda _n, gid: [gid, *self.extra_groups]
        )


@pytest.fixture()
def no_chown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-root tests: the home keeps the current user, who is the account."""
    monkeypatch.setattr(os, "fchown", lambda _fd, _u, _g: None)


def _world(
    monkeypatch: pytest.MonkeyPatch, platform: PlatformName = "macos-arm64"
) -> World:
    world = World(platform)
    world.install(monkeypatch)
    me = os.getuid()
    monkeypatch.setattr(plat, "ID_RANGE", {platform: range(me, me + 1)})
    return world


def _account(layout: Layout) -> Account | None:
    return load_accounts(layout.accounts).get("df-build")


def _write_account(layout: Layout, account: Account) -> None:
    layout.accounts.write_text(dump_accounts({account.name: account}))


# --- account identity ---------------------------------------------------------


def _ready_world(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None
) -> tuple[World, Account]:
    world = _world(monkeypatch)
    account = plat.create_build_user(layout)
    assert account.state == "ready"
    return world, account


@pytest.mark.parametrize(
    "case,needle",
    [
        ("uid0", "uid 0"),
        ("uid-shared", "есть и у"),
        ("admin-group", "административные"),
        ("wrong-primary-group", "основная группа"),
        ("login-shell", "оболочка"),
        ("home-0755", "0755"),
        ("home-foreign-owner", "владелец"),
        ("sudo-unknown", "sudo -l"),
        ("not-in-accounts", "нет записи"),
        ("state-creating", "state"),
        ("home-elsewhere", "в намерении"),
    ],
)
def test_verify_build_user_problems(
    monkeypatch: pytest.MonkeyPatch,
    layout: Layout,
    no_chown: None,
    case: str,
    needle: str,
) -> None:
    world, account = _ready_world(monkeypatch, layout, no_chown)
    assert plat.verify_build_user(layout) == []
    user = world.users["df-build"]
    if case == "uid0":
        user["UniqueID"] = "0"
        _write_account(layout, Account("df-build", 0, account.gid, "ready", "t"))
    elif case == "uid-shared":
        world.others.append(SimpleNamespace(pw_name="intruder", pw_uid=account.uid))
    elif case == "admin-group":
        world.other_groups.append(SimpleNamespace(gr_name="admin", gr_gid=80))
        world.extra_groups = (80,)
    elif case == "wrong-primary-group":
        user["PrimaryGroupID"] = "20"
    elif case == "login-shell":
        user["UserShell"] = "/bin/zsh"
    elif case == "home-0755":
        layout.build_home.chmod(0o755)
    elif case == "home-foreign-owner":
        other = account.uid + 1
        user["UniqueID"] = str(other)
        _write_account(layout, Account("df-build", other, account.gid, "ready", "t"))
    elif case == "sudo-unknown":
        world.sudo = (1, b"sudo: a password is required\n")
    elif case == "not-in-accounts":
        layout.accounts.unlink()
    elif case == "home-elsewhere":
        elsewhere = layout.root / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        user["NFSHomeDirectory"] = str(elsewhere)
    elif case == "state-creating":
        _write_account(
            layout, Account("df-build", account.uid, account.gid, "creating", "t")
        )
    problems = plat.verify_build_user(layout)
    assert any(needle in p for p in problems), problems


def test_account_problems_ignores_state(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None
) -> None:
    _, account = _ready_world(monkeypatch, layout, no_chown)
    creating = Account("df-build", account.uid, account.gid, "creating", "t")
    _write_account(layout, creating)
    assert plat.account_problems(creating) == []
    assert plat.verify_build_user(layout) == ["state = creating"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_create_absent_to_ready_without_stubbing_checks(
    monkeypatch: pytest.MonkeyPatch,
    layout: Layout,
    no_chown: None,
    platform: PlatformName,
) -> None:
    world = _world(monkeypatch, platform)
    written: list[str] = []
    real_write = fsutil.write_atomic

    def spy(path: Path, text: str, mode: int = 0o644) -> None:
        written.append(load_state(text))
        real_write(path, text, mode)

    def load_state(text: str) -> str:
        return "ready" if 'state = "ready"' in text else "creating"

    monkeypatch.setattr(fsutil, "write_atomic", spy)
    account = plat.create_build_user(layout)
    assert written == ["creating", "ready"]
    assert account.state == "ready" and account.uid == os.getuid()
    assert plat.verify_build_user(layout) == []
    assert world.commands == [
        plat.attr_argv(platform, op, account, layout) for op in ACCOUNT_ATTRS[platform]
    ]
    assert stat.S_IMODE(os.lstat(layout.build_home).st_mode) == 0o700
    assert not os.path.lexists(layout.build_home.parent / HOME_TMP_NAME)


def test_create_refuses_preexisting_foreign_user(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    world = _world(monkeypatch)
    world.users["df-build"] = {"RecordName": "df-build", "UniqueID": "77"}
    with pytest.raises(StepFailed, match="создан не darkfactory"):
        plat.create_build_user(layout)
    assert world.commands == []
    assert _account(layout) is None


HOME_STOPS = ["home-mkdir", "home-chown", "home-rename", "home-parent-sync"]


def _arm_home_stop(monkeypatch: pytest.MonkeyPatch, layout: Layout, stop: str) -> None:
    kinds = {"home-mkdir": "created", "home-chown": "chown", "home-rename": "renamed"}
    if stop in kinds:

        def fault(kind: str, path: Path) -> None:
            if kind == kinds[stop]:
                raise Interrupted(stop)

        monkeypatch.setattr(fsutil, "fault_point", fault)
        return
    real_sync = fsutil.sync_dir
    parent_ino = os.stat(layout.build_home.parent).st_ino

    def sync(target: int | Path) -> fsutil.SyncNote:
        note = real_sync(target)
        if isinstance(target, int) and os.fstat(target).st_ino == parent_ino:
            raise Interrupted(stop)
        return note

    monkeypatch.setattr(fsutil, "sync_dir", sync)


def _arm_intent_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = fsutil.write_atomic

    def write(path: Path, text: str, mode: int = 0o644) -> None:
        real_write(path, text, mode)
        raise Interrupted("intent")

    monkeypatch.setattr(fsutil, "write_atomic", write)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("stop_after", ["intent", "attr:*", *HOME_STOPS])
def test_create_resumes_own_partial_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_chown: None,
    platform: PlatformName,
    stop_after: str,
) -> None:
    count = len(ACCOUNT_ATTRS[platform])
    stops = (
        [f"attr:{i}" for i in range(1, count + 1)]
        if stop_after == "attr:*"
        else [stop_after]
    )
    for n, stop in enumerate(stops):
        with monkeypatch.context() as mp:
            root = Path(os.path.realpath(tmp_path)) / f"w{n}"
            lay = Layout(root)
            lay.install_dir.mkdir(parents=True)
            lay.build_home.parent.mkdir(parents=True)
            world = _world(mp, platform)
            if stop.startswith("attr:"):
                world.stop_after_command = int(stop.split(":")[1])
            with mp.context() as armed:
                if stop == "intent":
                    _arm_intent_stop(armed)
                elif stop in HOME_STOPS:
                    _arm_home_stop(armed, lay, stop)
                with pytest.raises(Interrupted):
                    plat.create_build_user(lay)
            first = _account(lay)
            assert first is not None and first.state == "creating", stop
            world.stop_after_command = None
            account = plat.create_build_user(lay)
            assert account.state == "ready", stop
            assert plat.verify_build_user(lay) == [], stop
            expected = [
                plat.attr_argv(platform, op, account, lay)
                for op in ACCOUNT_ATTRS[platform]
            ]
            assert world.commands == expected, stop
            assert not os.path.lexists(lay.build_home.parent / HOME_TMP_NAME)


@pytest.mark.parametrize(
    "where",
    [
        "home-foreign-owner",
        "home-root-owned",
        "home-link",
        "reserved-name-before-intent",
        "reserved-name-not-empty",
        "reserved-name-foreign-owner",
        "reserved-name-link",
    ],
)
def test_create_never_adopts_foreign_home(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None, where: str
) -> None:
    world = _world(monkeypatch)
    home = layout.build_home
    reserved = home.parent / HOME_TMP_NAME
    me = os.getuid()
    if where == "reserved-name-before-intent":
        reserved.mkdir()
        target = reserved
    else:
        stranger = me + 1  # the recorded account is not the owner of the objects
        _write_account(layout, Account("df-build", stranger, stranger, "creating", "t"))
        if where == "home-root-owned":
            monkeypatch.setattr(plat, "ROOT_UID", me)
        if where == "home-link":
            (layout.root / "elsewhere").mkdir(mode=0o700)
            home.symlink_to(layout.root / "elsewhere")
            target = home
        elif where == "reserved-name-foreign-owner":
            monkeypatch.setattr(plat, "ROOT_UID", me + 2)  # owner: neither
            reserved.mkdir()
            target = reserved
        elif where == "reserved-name-link":
            (layout.root / "elsewhere").mkdir()
            reserved.symlink_to(layout.root / "elsewhere")
            target = reserved
        elif where == "reserved-name-not-empty":
            _write_account(layout, Account("df-build", me, me, "creating", "t"))
            reserved.mkdir()
            (reserved / "planted").write_bytes(b"x")
            target = reserved
        else:
            home.mkdir(mode=0o700)
            target = home
    before = os.lstat(target)
    with pytest.raises(StepFailed, match="создан не darkfactory"):
        plat.create_build_user(layout)
    after = os.lstat(target)
    assert (before.st_ino, before.st_mode, before.st_uid) == (
        after.st_ino,
        after.st_mode,
        after.st_uid,
    )
    if where == "reserved-name-not-empty":
        assert (reserved / "planted").read_bytes() == b"x"
    assert world.commands == []  # every refusal precedes the first OS command


def test_reserved_name_of_root_is_recreated(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None
) -> None:
    _world(monkeypatch)
    me = os.getuid()
    _write_account(layout, Account("df-build", me, me, "creating", "t"))
    (layout.build_home.parent / HOME_TMP_NAME).mkdir()
    assert plat.create_build_user(layout).state == "ready"


def test_rename_noreplace_refuses_existing_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "marker").write_bytes(b"m")
    (tmp_path / "dst").mkdir()
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(StepFailed, match="существует"):
            plat.rename_noreplace(fd, "src", "dst")
        assert (tmp_path / "src" / "marker").exists()
        assert os.listdir(tmp_path / "dst") == []
        plat.rename_noreplace(fd, "src", "fresh")
        assert (tmp_path / "fresh" / "marker").exists()
        os.rename(tmp_path / "fresh", tmp_path / "dst")  # a plain rename replaces
    finally:
        os.close(fd)


def test_rename_noreplace_missing_call_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(plat, "_rename_function", lambda: None)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(StepFailed, match="недоступно"):
            plat.rename_noreplace(fd, "src", "dst")
    finally:
        os.close(fd)
    assert (tmp_path / "src").is_dir() and not (tmp_path / "dst").exists()


def test_create_refuses_mismatch_with_intent(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None
) -> None:
    world = _world(monkeypatch)
    me = os.getuid()
    _write_account(layout, Account("df-build", me, me, "creating", "t"))
    world.groups["df-build"] = {"RecordName": "df-build", "PrimaryGroupID": str(me)}
    world.users["df-build"] = {"RecordName": "df-build", "UniqueID": str(me + 5)}
    with pytest.raises(StepFailed, match="UniqueID"):
        plat.create_build_user(layout)
    assert world.commands == []


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize(
    "case,needle",
    [
        ("record-before-intent", "создан не darkfactory"),
        ("partial-with-conflicting-gid", "gid|GroupID"),
        ("partial-with-login-shell", "hell"),
        ("other-name-same-uid", "uid"),
    ],
)
def test_create_never_adopts_conflicting_record(
    monkeypatch: pytest.MonkeyPatch,
    layout: Layout,
    platform: PlatformName,
    case: str,
    needle: str,
) -> None:
    world = _world(monkeypatch, platform)
    me = os.getuid()
    mac = platform == "macos-arm64"
    if case != "record-before-intent":
        _write_account(layout, Account("df-build", me, me, "creating", "t"))
    if case == "record-before-intent":
        world.groups["df-build"] = {"RecordName": "df-build"} if mac else {"gid": "1"}
    elif case == "partial-with-conflicting-gid":
        world.groups["df-build"] = (
            {"RecordName": "df-build", "PrimaryGroupID": str(me + 3)}
            if mac
            else {"gid": str(me + 3)}
        )
    elif case == "partial-with-login-shell":
        world.users["df-build"] = (
            {"RecordName": "df-build", "UserShell": "/bin/zsh"}
            if mac
            else {
                "uid": str(me),
                "gid": str(me),
                "home": str(layout.build_home),
                "shell": "/bin/bash",
            }
        )
    else:
        world.others.append(SimpleNamespace(pw_name="someone", pw_uid=me))
    with pytest.raises(StepFailed, match=needle):
        plat.create_build_user(layout)
    assert world.commands == []


def test_created_home_is_settled_after_rename_retry_does_no_sync(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, no_chown: None
) -> None:
    """R21: created home -> `sync_dir(parent)` after the rename; a home found by
    a retry is accepted without a sync of its own before `ready`."""
    _world(monkeypatch)
    events: list[str] = []
    parent_ino = os.stat(layout.build_home.parent).st_ino
    real_sync = fsutil.sync_dir

    def sync(target: int | Path) -> fsutil.SyncNote:
        is_parent = isinstance(target, int) and os.fstat(target).st_ino == parent_ino
        events.append("sync(parent)" if is_parent else "sync(other)")
        return real_sync(target)

    def fault(kind: str, path: Path) -> None:
        if path.name == "accounts.toml":
            return  # the record's own write_atomic is not the home protocol
        events.append(kind)
        if kind == "renamed" and stop["on"]:
            raise Interrupted("after rename")

    stop = {"on": True}
    monkeypatch.setattr(fsutil, "sync_dir", sync)
    monkeypatch.setattr(fsutil, "fault_point", fault)
    with pytest.raises(Interrupted):
        plat.create_build_user(layout)
    assert events[-1] == "renamed"
    stop["on"] = False
    events.clear()
    plat.create_build_user(layout)
    # the retry found the home: no mkdir/rename, and no sync of the home parent
    assert "created" not in events and "renamed" not in events
    assert "sync(parent)" not in events
    # a fresh creation syncs the parent right after the rename
    events.clear()
    other = Layout(layout.root.parent / "fresh")
    other.install_dir.mkdir(parents=True)
    other.build_home.parent.mkdir(parents=True)
    parent_ino = os.stat(other.build_home.parent).st_ino
    _world(monkeypatch)
    plat.create_build_user(other)
    i = events.index("renamed")
    assert events[i + 1] == "sync(parent)"


def test_account_attrs_match_spec_order() -> None:
    def shape(platform: PlatformName) -> list[tuple[str, str]]:
        return [(op.kind, op.attr) for op in ACCOUNT_ATTRS[platform]]

    assert shape("macos-arm64") == [
        ("group", "RecordName"),
        ("group", "PrimaryGroupID"),
        ("user", "RecordName"),
        ("user", "Password"),
        ("user", "UserShell"),
        ("user", "IsHidden"),
        ("user", "PrimaryGroupID"),
        ("user", "NFSHomeDirectory"),
        ("user", "UniqueID"),
    ]
    assert shape("linux-x86_64") == [("group", "line"), ("user", "line")]
    lay = Layout(Path("/"))
    acc = Account("df-build", 401, 402, "creating", "t")
    home = str(lay.build_home)
    assert [
        plat.attr_argv("macos-arm64", op, acc, lay)
        for op in ACCOUNT_ATTRS["macos-arm64"]
    ] == [
        ["/usr/bin/dscl", ".", "-create", "/Groups/df-build"],
        ["/usr/bin/dscl", ".", "-create", "/Groups/df-build", "PrimaryGroupID", "402"],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build"],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build", "Password", "*"],
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            "/Users/df-build",
            "UserShell",
            "/usr/bin/false",
        ],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build", "IsHidden", "1"],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build", "PrimaryGroupID", "402"],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build", "NFSHomeDirectory", home],
        ["/usr/bin/dscl", ".", "-create", "/Users/df-build", "UniqueID", "401"],
    ]
    assert [
        plat.attr_argv("linux-x86_64", op, acc, lay)
        for op in ACCOUNT_ATTRS["linux-x86_64"]
    ] == [
        ["/usr/sbin/groupadd", "--system", "--gid", "402", "df-build"],
        [
            "/usr/sbin/useradd",
            "--system",
            "--uid",
            "401",
            "--gid",
            "df-build",
            "--home-dir",
            home,
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            "df-build",
        ],
    ]


def test_read_record_error_is_not_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = RunResult(70, b"", b"DS Error: -14009 (eDSUnknownNodeName)", True)
    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(plat, "run_command", lambda _a, **_k: failed)
    with pytest.raises(StepFailed):
        plat.read_record("user", "df-build")
    monkeypatch.setattr(plat, "detect_platform", lambda: "linux-x86_64")
    with pytest.raises(StepFailed):
        plat.read_record("user", "df-build")


def test_parse_dscl_captured_records() -> None:
    base = FIXTURES / "dscl"
    group = plat.parse_dscl((base / "group-staff.plist").read_bytes())
    assert group["RecordName"] == "staff, BUILTIN\\Users"
    assert group["PrimaryGroupID"] == "20"
    user = plat.parse_dscl((base / "user-human-df-build.plist").read_bytes())
    assert user["UniqueID"] == "503" and user["UserShell"] == "/bin/zsh"
    assert not any(k.startswith("dsAttrType") for k in user)  # native keys ignored


@pytest.mark.parametrize(
    "output",
    [
        b"RecordName: df-build\n",
        b"",
        plistlib.dumps(["df-build"]),
        plistlib.dumps({"dsAttrTypeStandard:UniqueID": "401"}),
        plistlib.dumps({"dsAttrTypeStandard:UniqueID": [401]}),
    ],
    ids=["text", "empty", "not-a-dict", "not-a-list", "not-strings"],
)
def test_parse_dscl_unparsable_is_refusal(output: bytes) -> None:
    with pytest.raises(StepFailed, match="неразборн"):
        plat.parse_dscl(output)


def test_human_df_build_refused_with_instruction(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    world = _world(monkeypatch)
    human = (FIXTURES / "dscl" / "user-human-df-build.plist").read_bytes()
    real_run = world.run

    def run(argv: Sequence[str], **kw: object) -> RunResult:
        if list(argv)[-1] == "/Users/df-build" and "-read" in argv:
            return ok(human)
        return real_run(argv, **kw)

    monkeypatch.setattr(plat, "run_command", run)
    with pytest.raises(StepFailed, match="удалите или переименуйте"):
        plat.create_build_user(layout)
    assert world.commands == []


@pytest.mark.parametrize(
    "output",
    [b"df-build:x:abc:20::/h:/bin/sh\n", b"df-build:x:1:2::/h:/bin/sh\nx\n", b"\xff"],
)
def test_parse_getent_unparsable_is_refusal(output: bytes) -> None:
    with pytest.raises(StepFailed, match="неразборн"):
        plat.parse_getent("user", "df-build", output)


# --- cleanup_tree ------------------------------------------------------------

#: Task 5 vocabulary of the fake environment: obj absent | link | file | dir;
#: cleaner ok | code-1 | timeout | "-"; rmdir ok | enotempty | "-".
_CLEANER_WORD = {"ok": "ok", "exit": "code-1", "timeout": "timeout", None: "-"}
_RMDIR_WORD = {"ok": "ok", "ENOTEMPTY": "enotempty", None: "-"}


def _real_run(argv: Sequence[str]) -> RunResult:
    done = subprocess.run(list(argv), capture_output=True, check=False)
    return RunResult(done.returncode, done.stdout, done.stderr, True)


def _cleanup_layout(tmp_path: Path) -> Layout:
    root = Path(os.path.realpath(tmp_path)) / "cleanup-root"
    lay = Layout(root)
    lay.staging.mkdir(parents=True)
    lay.quarantine.mkdir(parents=True)
    return lay


def _run_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    layout: Layout,
    obj: str,
    cleaner: str,
    rmdir: str,
    label: str | None,
) -> tuple[list[str], str, Path]:
    """Real `cleanup_tree`; only `run_as` and the fsutil observer replaced."""
    parent = layout.staging
    tree = parent / "t"
    if obj == "link":
        (layout.root / "target").mkdir()
        tree.symlink_to(layout.root / "target")
    elif obj == "file":
        tree.write_bytes(b"f")
    elif obj == "dir":
        _locked_tree(tree)
    events: list[str] = []
    calls = {"n": 0}

    def run_as(user: str, argv: Sequence[str], **kw: Any) -> RunResult:
        assert user == "df-build" and kw["network"] is False
        events.append("run_as(df-build)")
        calls["n"] += 1
        if cleaner == "timeout" and calls["n"] == 2:
            return RunResult(-9, b"", b"", True, timed_out=True)
        if cleaner == "code-1" and calls["n"] == 2:
            return RunResult(1, b"", b"find: denied", True)
        result = _real_run(argv)
        if rmdir == "enotempty" and calls["n"] == 3:
            (tree / "late").write_bytes(b"x")
        return result

    p_ino = os.stat(parent).st_ino
    q_ino = os.stat(layout.quarantine).st_ino

    def sync(target: int | Path) -> fsutil.SyncNote:
        assert isinstance(target, int)
        ino = os.fstat(target).st_ino
        events.append({p_ino: "sync_dir(parent)", q_ino: "sync_dir(quarantine)"}[ino])
        return fsutil.SyncNote.OK

    def fault(kind: str, path: Path) -> None:
        events.append({"unlinked": "unlink", "renamed": "renamed"}[kind])

    monkeypatch.setattr(plat, "run_as", run_as)
    monkeypatch.setattr(fsutil, "sync_dir", sync)
    monkeypatch.setattr(fsutil, "fault_point", fault)
    outcome = plat.cleanup_tree(layout, parent, "t", quarantine_label=label)
    return _collapse(events), outcome, tree


def run_cleanup_tree_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: tuple[str, str | None, str | None, bool],
) -> tuple[list[str], str]:
    """Run one selection case (obj, cleaner, rmdir, label) of the table vocabulary.

    Prepares the case in a fresh layout under `tmp_path`, runs the real
    `cleanup_tree` (`_run_cleanup`), checks what the events cannot show (the name
    stays on `failed`; the quarantine entry; a link's target untouched) and unlocks
    the tree before returning the collapsed events and the outcome.
    """
    obj, cleaner, rmdir, label = case
    layout = _cleanup_layout(tmp_path)
    try:
        events, outcome, tree = _run_cleanup(
            monkeypatch,
            layout,
            obj,
            _CLEANER_WORD[cleaner],
            _RMDIR_WORD[rmdir],
            "rel1" if label else None,
        )
        quarantined = os.listdir(layout.quarantine)
        if outcome == "failed":
            assert os.path.lexists(tree)
            assert quarantined == []
        elif outcome == "quarantined":
            assert not os.path.lexists(tree)
            assert quarantined == ["rel1.1"]
        else:
            assert not os.path.lexists(tree)
            assert quarantined == []
        if obj == "link":
            assert (layout.root / "target").is_dir()
    finally:
        _unlock(layout.root)
    return events, outcome


def _collapse(events: list[str]) -> list[str]:
    out: list[str] = []
    for raw in events:
        e = "run_as(df-build)+" if raw == "run_as(df-build)" else raw
        if not (out and out[-1] == e == "run_as(df-build)+"):
            out.append(e)
    return out


@pytest.mark.parametrize("case", [inputs for inputs, _ in CLEANUP_TREE_CASES])
def test_cleanup_tree_variant_events(monkeypatch, tmp_path, case):
    expected_variant = cleanup_tree_variant(*case)
    events, outcome = run_cleanup_tree_case(monkeypatch, tmp_path, case)
    assert events == list(leaf_variants("cleanup_tree")[expected_variant])
    assert outcome == CLEANUP_OUTCOME[expected_variant]
    # S1-R11-N1, S1-R12-N1, S1-R13-N1, S1-R14-N2: case = (obj, cleaner, rmdir, label) —
    # входы выбора; ожидание вычисляется из данных в самом тесте (строки выше);
    # подготовка строит их (obj: нет имени / ссылка или файл / каталог; cleaner: подменённый
    # run_as возвращает 0, код 1 или срок, причём первая команда действительно меняет режим
    # корня; rmdir: ENOTEMPTY — после трёх успешных команд в дереве появляется файл; label:
    # quarantine_label=id или None). run_cleanup_tree_case — фикстура: настоящий
    # cleanup_tree, подменены только run_as и fsutil-наблюдатель; CLEANUP_OUTCOME =
    # {"tree": "removed", "link": "removed", "absent": "removed", "quarantine":
    # "quarantined", "failed": "failed"}. События — порядок, кратность, цели
    # (sync_dir(quarantine) — по fd карантина, sync_dir(parent) — по pfd); при failed имя
    # в parent на месте, root-событий нет, изменённый режим допустим. Двойники — провал:
    # link без sync_dir(parent); quarantine без sync_dir(quarantine); quarantine с parent
    # раньше quarantine; sync_dir(parent) до rename (S1-R12-N2); ENOTEMPTY после успешного
    # очистителя как tree (S1-R13-N1)


@pytest.mark.parametrize("label", ["rel1", None])
def test_cleanup_tree_file_in_place_of_tree_is_link_variant(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, label: str | None
) -> None:
    events, outcome, tree = _run_cleanup(monkeypatch, layout, "file", "-", "-", label)
    assert events == list(leaf_variants("cleanup_tree")["link"])
    assert outcome == "removed"
    assert not os.path.lexists(tree)


@pytest.mark.parametrize(
    "variant,twin",
    [
        ("link", ["unlink"]),
        ("quarantine", ["run_as(df-build)+", "renamed", "sync_dir(parent)"]),
        (
            "quarantine",
            [
                "run_as(df-build)+",
                "renamed",
                "sync_dir(parent)",
                "sync_dir(quarantine)",
            ],
        ),
        (
            "quarantine",
            [
                "run_as(df-build)+",
                "sync_dir(parent)",
                "renamed",
                "sync_dir(quarantine)",
            ],
        ),
        ("quarantine", ["run_as(df-build)+", "unlink", "sync_dir(parent)"]),
    ],
    ids=[
        "link-without-parent-sync",
        "quarantine-without-quarantine-sync",
        "parent-before-quarantine",
        "parent-sync-before-rename",
        "enotempty-after-cleaner-as-tree",
    ],
)
def test_cleanup_tree_variant_twins_rejected(variant: str, twin: list[str]) -> None:
    assert twin != list(leaf_variants("cleanup_tree")[variant])


_AUDIT: dict[str, Any] = {"on": False, "seen": []}


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if _AUDIT["on"] and event in (
        "open",
        "os.listdir",
        "os.scandir",
        "os.remove",
        "os.rmdir",
        "os.rename",
    ):
        _AUDIT["seen"].append((event, args))


sys.addaudithook(_audit_hook)


def test_cleanup_tree_root_ops_audited(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    tree = layout.staging / "t"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f").write_bytes(b"x")
    _AUDIT["seen"] = []

    def run_as(user: str, argv: Sequence[str], **_kw: Any) -> RunResult:
        return RunResult(1, b"", b"", True)  # cleaner fails -> quarantine path

    monkeypatch.setattr(plat, "run_as", run_as)
    try:
        _AUDIT["on"] = True
        try:
            outcome = plat.cleanup_tree(
                layout, layout.staging, "t", quarantine_label="rel1"
            )
        finally:
            _AUDIT["on"] = False
        assert outcome == "quarantined"
        inside = str(tree) + "/"
        for event, args in _AUDIT["seen"]:
            paths = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
            text = [os.fsdecode(p) for p in paths]
            assert not any(t.startswith(inside) for t in text), (event, args)
            if event in ("os.rmdir", "os.rename", "os.remove"):
                assert text[0] == "t", (event, args)
            assert event not in ("os.listdir", "os.scandir"), (event, args)
        assert "os.rename" in [e for e, _ in _AUDIT["seen"]]
    finally:
        _unlock(layout.root)


def test_cleanup_tree_link_in_place_of_tree_unlinked(layout: Layout) -> None:
    victim = layout.root / "victim"
    victim.mkdir()
    (victim / "keep").write_bytes(b"k")
    (layout.staging / "t").symlink_to(victim)
    outcome = plat.cleanup_tree(layout, layout.staging, "t", quarantine_label="x")
    assert outcome == "removed"
    assert not os.path.lexists(layout.staging / "t")
    assert (victim / "keep").read_bytes() == b"k"


@pytest.mark.parametrize("name", ["", ".", "..", "a/b"])
def test_cleanup_tree_refuses_bad_name(layout: Layout, name: str) -> None:
    with pytest.raises(StepFailed):
        plat.cleanup_tree(layout, layout.staging, name, quarantine_label=None)


def test_retry_quarantine_uses_cleaner_per_entry(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    for name in ("a.1", "b.1", "c.1"):
        _locked_tree(layout.quarantine / name)
    targets: list[str] = []

    def run_as(user: str, argv: Sequence[str], **kw: Any) -> RunResult:
        targets.append(argv[2] if argv[0] == "/bin/chmod" else argv[1])
        if "b.1" in argv[1]:
            return RunResult(1, b"", b"", True)
        return _real_run(argv)

    monkeypatch.setattr(plat, "run_as", run_as)
    try:
        assert plat.retry_quarantine(layout) == ["b.1"]
        assert sorted(os.listdir(layout.quarantine)) == ["b.1"]
        assert {Path(t).name for t in targets} == {"a.1", "b.1", "c.1"}
    finally:
        _unlock(layout.root)


def test_retry_quarantine_absent_dir(layout: Layout) -> None:
    layout.quarantine.rmdir()
    assert plat.retry_quarantine(layout) == []


# --- cleanup_build_home ---------------------------------------------------------


def _home_runner(
    monkeypatch: pytest.MonkeyPatch, find: RunResult | None = None
) -> list[list[str]]:
    seen: list[list[str]] = []

    def run_as(user: str, argv: Sequence[str], **kw: Any) -> RunResult:
        assert user == "df-build" and kw["network"] is False
        seen.append(list(argv))
        if find is not None and "-maxdepth" in argv:
            return find
        return _real_run(argv)

    monkeypatch.setattr(plat, "run_as", run_as)
    return seen


def test_cleanup_build_home_removes_whole_tree(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    layout.build_home.mkdir()
    _locked_tree(layout.build_home / "tmp")
    seen = _home_runner(monkeypatch)
    try:
        assert plat.cleanup_build_home(layout, "tmp") == "removed"
    finally:
        _unlock(layout.root)
    target = str(layout.build_home / "tmp")
    assert not os.path.lexists(target)
    assert seen[3] == ["/usr/bin/find", target, "-delete"]
    assert seen[1:3] == CLEANER_ARGV(Path(target))[:2]


def test_cleanup_build_home_absent(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    layout.build_home.mkdir()
    seen = _home_runner(monkeypatch)
    assert plat.cleanup_build_home(layout, "cache") == "absent"
    assert len(seen) == 1


@pytest.mark.parametrize(
    "find",
    [
        RunResult(1, b"", b"find: denied", True),
        RunResult(0, b"", b"", False),
        RunResult(0, b"/elsewhere\n", b"", True),
        RunResult(-9, b"", b"", True, timed_out=True),
    ],
)
def test_cleanup_build_home_error_is_not_absence(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, find: RunResult
) -> None:
    layout.build_home.mkdir()
    _home_runner(monkeypatch, find)
    with pytest.raises(StepFailed) as info:
        plat.cleanup_build_home(layout, "tmp")
    assert info.value.step == "verify"


# --- trusted paths --------------------------------------------------------------


@dataclass
class FakeFs:
    """`lstat`/`readlink`/ACL view: path -> (mode, uid, link target)."""

    nodes: dict[str, tuple[int, int, str | None]]
    acl: dict[str, bool | OSError] = field(default_factory=dict)

    def lstat(self, path: os.PathLike[str] | str) -> SimpleNamespace:
        mode, uid, _ = self._node(path)
        return SimpleNamespace(st_mode=mode, st_uid=uid)

    def readlink(self, path: os.PathLike[str] | str) -> str:
        target = self._node(path)[2]
        assert target is not None
        return target

    def acl_present(self, path: Path) -> bool:
        value = self.acl.get(str(path), False)
        if isinstance(value, OSError):
            raise value
        return value

    def _node(self, path: os.PathLike[str] | str) -> tuple[int, int, str | None]:
        """Like the kernel: links in the middle are followed, `..` is physical."""
        try:
            return self.nodes[self._resolve(os.fspath(path))]
        except KeyError:
            raise FileNotFoundError(path) from None

    def _resolve(self, text: str) -> str:
        parts = Path(text).parts
        current = "/"
        for i, part in enumerate(parts[1:], start=1):
            if part == "..":
                current = os.path.dirname(current)
                continue
            current = os.path.join(current, part)
            node = self.nodes.get(current)
            if node is None:
                raise KeyError(current)
            if node[2] is not None and i < len(parts) - 1:
                link = node[2]
                base = "/" if link.startswith("/") else os.path.dirname(current)
                current = self._resolve(os.path.join(base, link))
        return current

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(plat, "_lstat", self.lstat)
        monkeypatch.setattr(plat, "_readlink", self.readlink)
        monkeypatch.setattr(plat, "acl_present_path", self.acl_present)


DIR = stat.S_IFDIR
REG = stat.S_IFREG
LNK = stat.S_IFLNK


def _good_fs() -> FakeFs:
    return FakeFs(
        {
            "/": (DIR | 0o755, 0, None),
            "/usr": (DIR | 0o755, 0, None),
            "/usr/local": (DIR | 0o755, 0, None),
            "/usr/local/bin": (DIR | 0o755, 0, None),
            "/usr/local/bin/tool": (REG | 0o755, 0, None),
        }
    )


def test_trusted_path_owner_write_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _good_fs()
    fs.install(monkeypatch)
    tool = Path("/usr/local/bin/tool")
    assert plat.trusted_path_problems(tool) == []
    fs.nodes["/usr/local/bin"] = (DIR | 0o755, 501, None)
    assert plat.trusted_path_problems(tool) == [
        "/usr/local/bin: владелец uid 501, не root"
    ]
    fs.nodes["/usr/local/bin"] = (DIR | 0o775, 0, None)
    assert plat.trusted_path_problems(tool) == [
        "/usr/local/bin: запись для группы или всех"
    ]
    fs.nodes["/usr/local/bin"] = (DIR | 0o755, 0, None)
    fs.acl["/usr/local"] = True
    assert plat.trusted_path_problems(tool) == ["/usr/local: есть ACL"]
    fs.acl["/usr/local"] = OSError(5, "Input/output error")
    problems = plat.trusted_path_problems(tool)
    assert len(problems) == 1 and "ACL не установлен" in problems[0]
    fs.acl.clear()
    assert plat.trusted_path_problems(Path("/usr/local/bin/missing")) == [
        "/usr/local/bin/missing: не существует или не читается"
    ]
    assert plat.trusted_path_problems(Path("relative")) == [
        "relative: путь не абсолютный"
    ]


def test_trusted_path_follows_intermediate_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fs = FakeFs(
        {
            "/": (DIR | 0o755, 0, None),
            "/a": (LNK | 0o777, 0, "/b"),
            "/b": (DIR | 0o755, 501, None),
            "/b/tool": (REG | 0o755, 0, None),
            "/c": (LNK | 0o777, 0, "b/../b"),
        }
    )
    fs.install(monkeypatch)
    assert plat.trusted_path_problems(Path("/a/tool")) == [
        "/b: владелец uid 501, не root"
    ]
    assert plat.trusted_path_problems(Path("/c/tool")) == [
        "/b: владелец uid 501, не root",
        "/b: владелец uid 501, не root",
    ]
    fs.nodes["/b"] = (DIR | 0o755, 0, None)
    assert plat.trusted_path_problems(Path("/a/tool")) == []
    assert plat._walk(Path("/a/tool"))[1] == Path("/b/tool")


def test_trusted_path_link_loop_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeFs(
        {
            "/": (DIR | 0o755, 0, None),
            "/x": (LNK | 0o777, 0, "/y"),
            "/y": (LNK | 0o777, 0, "/x"),
        }
    ).install(monkeypatch)
    problems = plat.trusted_path_problems(Path("/x"))
    assert problems and "больше 40" in problems[-1]


def test_approved_cli_env_shebang_refused(tmp_path: Path) -> None:
    script = tmp_path / "tool"
    script.write_text("#!/usr/bin/env python3\nprint(1)\n")
    script.chmod(0o755)
    problems = plat.approved_cli_problems({"tool": script}, "macos-arm64")
    assert any("#!/usr/bin/env" in p for p in problems), problems
    assert any("/usr/local/libexec/darkfactory/bin/tool" in p for p in problems)


def test_approved_cli_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _good_fs()
    fs.nodes["/usr/local/bin/script"] = (REG | 0o755, 0, None)
    fs.nodes["/usr/local/bin/dir"] = (DIR | 0o755, 0, None)
    fs.install(monkeypatch)
    heads = {"/usr/local/bin/script": b"#!/usr/local/bin/tool -x\n"}
    deps: dict[str, LibDeps | None] = {"/usr/local/bin/tool": LibDeps((), ())}
    monkeypatch.setattr(
        plat, "_read_regular", lambda p, _n: heads.get(str(p), b"\0\0\0\0")
    )
    monkeypatch.setattr(plat, "linked_libraries", lambda p: deps.get(str(p)))
    cli = {"tool": Path("/usr/local/bin/tool"), "script": Path("/usr/local/bin/script")}
    assert plat.approved_cli_problems(cli, "macos-arm64") == []
    assert plat.approved_cli_problems({"r": Path("tool")}, "macos-arm64")[0] == (
        "r: tool: путь не абсолютный"
    )
    dir_problems = plat.approved_cli_problems(
        {"d": Path("/usr/local/bin/dir")}, "macos-arm64"
    )
    assert dir_problems[0] == "d: /usr/local/bin/dir: не обычный файл"
    heads["/usr/local/bin/script"] = b"#!python3\n"
    problems = plat.approved_cli_problems({"s": cli["script"]}, "macos-arm64")
    assert (
        problems[0] == "s: /usr/local/bin/script: интерпретатор python3 не абсолютный"
    )
    # the interpreter goes through the same checks
    heads["/usr/local/bin/script"] = b"#!/usr/local/bin/tool\n"
    fs.nodes["/usr/local/bin/tool"] = (REG | 0o777, 0, None)
    problems = plat.approved_cli_problems({"s": cli["script"]}, "macos-arm64")
    assert (
        "s: /usr/local/bin/script: интерпретатор /usr/local/bin/tool: "
        "запись для группы или всех"
    ) in problems, problems


# --- sparse files through the system tar (R24) ---------------------------------


def _sparse_source(tmp_path: Path) -> tuple[Path, bytes, int]:
    src = tmp_path / "src"
    src.mkdir()
    head, tail = b"head-bytes", b"tail-bytes"
    gap = 64 << 20
    with (src / "sparse.bin").open("wb") as fh:
        fh.write(head)
        fh.seek(gap, os.SEEK_CUR)
        fh.write(tail)
    size = len(head) + gap + len(tail)
    return src, head + tail, size


def _tar_producer(src: Path) -> LocalProducer:
    flags = TAR_FLAGS[plat.detect_platform()]
    return LocalProducer([TAR, "-cf", "-", *flags, "-C", str(src), "."])


@MACOS_ONLY
def test_sparse_file_received_expanded(tmp_path: Path) -> None:
    src, ends, size = _sparse_source(tmp_path)
    dst = tmp_path / "rel"
    dst.mkdir()
    receive_tree(_tar_producer(src), dst, "r")
    got = dst / "r" / "sparse.bin"
    assert got.stat().st_size == size
    with got.open("rb") as fh:
        assert fh.read(10) == ends[:10]
        zeros = 0
        while zeros < size - 20:
            chunk = fh.read(min(1 << 20, size - 20 - zeros))
            assert chunk == bytes(len(chunk))
            zeros += len(chunk)
        assert fh.read() == ends[10:]


@MACOS_ONLY
def test_sparse_file_counts_expanded_bytes(tmp_path: Path) -> None:
    src, _ends, size = _sparse_source(tmp_path)
    dst = tmp_path / "rel"
    dst.mkdir()
    with pytest.raises(TreeError):
        receive_tree(
            _tar_producer(src), dst, "r", limits=CopyLimits(max_bytes=size - 1)
        )


def test_no_root_tree_walk_in_module() -> None:
    """Root never removes a builder tree itself (spec 3.3 "Очистка", п. 2)."""
    source = Path(plat.__file__).read_text()
    assert "rmtree" not in source and "os.walk" not in source


# --- approved CLI fixtures ---------------------------------------------------------


def _closure_fs(extra: dict[str, tuple[int, int, str | None]]) -> FakeFs:
    fs = _good_fs()
    fs.nodes.update(
        {
            "/usr/local/lib": (DIR | 0o755, 0, None),
            "/opt": (DIR | 0o755, 0, None),
            "/opt/evil": (DIR | 0o777, 501, None),
        }
    )
    fs.nodes.update(extra)
    return fs


TOOL = {"tool": Path("/usr/local/bin/tool")}
LIB = REG | 0o644


# --- strict parsers of tool output (fix round 1) -------------------------------------


@pytest.mark.parametrize(
    "parser,text",
    [
        (plat.parse_otool_deps, "/x:\n\t/usr/lib/libz.dylib (weird)\n"),
        (plat.parse_otool_deps, "/x:\nstray line\n"),
        (plat.parse_otool_loads, "Load command 1\n      cmd LC_RPATH\n  cmdsize 32\n"),
        (
            plat.parse_otool_loads,
            "Load command 1\n      cmd LC_LOAD_DYLINKER\n   name /usr/lib/dyld\n",
        ),
        (plat.parse_readelf, " 0x1 (NEEDED)             Shared library: libz\n"),
        (plat.parse_readelf_interp, "  [Requesting program interpreter /lib/ld]\n"),
    ],
)
def test_tool_output_unparsable_is_refusal(
    parser: Callable[[str], object], text: str
) -> None:
    with pytest.raises(StepFailed, match="неразборный"):
        parser(text)


def test_otool_without_command_line_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "bin"
    binary.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(60))
    fs = _good_fs()  # host-independent: trusted xcrun and otool on any OS
    fs.nodes.update(
        {
            "/usr/bin": (DIR | 0o755, 0, None),
            "/usr/bin/xcrun": (REG | 0o755, 0, None),
            "/usr/bin/otool": (REG | 0o755, 0, None),
        }
    )
    fs.install(monkeypatch)
    no_clt = RunResult(1, b"", b"xcrun: error: invalid active developer path", True)
    monkeypatch.setattr(plat, "run_command", lambda _a, **_k: no_clt)
    with pytest.raises(StepFailed, match="xcode-select --install"):
        plat.linked_libraries(binary)
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        return ok(b"/usr/bin/otool\n") if argv[0] == "/usr/bin/xcrun" else no_clt

    monkeypatch.setattr(plat, "run_command", run)
    with pytest.raises(StepFailed, match="Command Line Tools"):
        plat.linked_libraries(binary)
    assert seen[0] == ["/usr/bin/xcrun", "--find", "otool"]


# --- review minors (fix round 1) -----------------------------------------------------


def test_spawned_producer_end_skips_killpg_after_reap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proc = FakePopen(["tar"])
    proc.exit_on_wait = 0
    proc.wait(1.0)  # the consumer reaped the leader: its pid may be reused
    monkeypatch.setattr(os, "killpg", lambda _p, _s: pytest.fail("killpg called"))
    monkeypatch.setattr(plat, "reap_user_processes", lambda _u: [])
    prod = plat.SpawnedProducer(
        proc,  # type: ignore[arg-type]
        "df-build",
        1.0,
    )
    prod.end()


def test_retry_quarantine_listing_error_is_named(
    monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    def denied(_fd: object) -> list[str]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "listdir", denied)
    with pytest.raises(StepFailed) as info:
        plat.retry_quarantine(layout)
    assert info.value.step == "cleanup"


@pytest.mark.parametrize("rel", ["t*", "c?che", "[t]mp", "a\\b"])
def test_cleanup_build_home_refuses_glob(
    monkeypatch: pytest.MonkeyPatch, layout: Layout, rel: str
) -> None:
    monkeypatch.setattr(plat, "run_as", lambda *_a, **_k: pytest.fail("ran"))
    with pytest.raises(StepFailed) as info:
        plat.cleanup_build_home(layout, rel)
    assert info.value.step == "verify"


# --- host loader config, regular reads, inspection tools -----------------------------


def _with_files(
    monkeypatch: pytest.MonkeyPatch, fs: FakeFs, files: dict[str, bytes]
) -> None:
    """Fake `_read_regular` / `_glob` over `fs` (non-binary files by default)."""

    def read(path: Path, limit: int) -> bytes:
        mode = fs.lstat(path).st_mode
        if not stat.S_ISREG(mode):
            raise StepFailed("cli", f"{path}: не обычный файл")
        return files.get(str(path), b"\0\0\0\0")[:limit]

    def globber(pattern: str) -> list[str]:
        return [n for n in fs.nodes if fnmatch.fnmatchcase(n, pattern)]

    monkeypatch.setattr(plat, "_read_regular", read)
    monkeypatch.setattr(plat, "_glob", globber)


@pytest.mark.parametrize(
    "conf",
    [b"hwcap 1 nosegneg\n", b"/usr/lib=libc5\n", b"lib/relative\n", b"\xff\n"],
    ids=["hwcap", "typed", "relative", "not-utf8"],
)
def test_unparsable_ld_so_conf_is_a_problem(
    monkeypatch: pytest.MonkeyPatch, conf: bytes
) -> None:
    fs = _closure_fs(
        {"/etc": (DIR | 0o755, 0, None), "/etc/ld.so.conf": (LIB, 0, None)}
    )
    fs.install(monkeypatch)
    _with_files(monkeypatch, fs, {"/etc/ld.so.conf": conf})
    monkeypatch.setattr(
        plat,
        "linked_libraries",
        lambda p: LibDeps((), ()) if str(p) == "/usr/local/bin/tool" else None,
    )
    problems = plat.approved_cli_problems(TOOL, "linux-x86_64")
    assert any(p.startswith("загрузчик ОС: /etc/ld.so.conf:") for p in problems), (
        problems
    )


def test_untrusted_ld_so_conf_include_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fs = _closure_fs(
        {
            "/etc": (DIR | 0o755, 0, None),
            "/etc/ld.so.conf": (LIB, 0, None),
            "/etc/ld.so.conf.d": (DIR | 0o755, 0, None),
            "/etc/ld.so.conf.d/x.conf": (LIB | 0o666, 0, None),
        }
    )
    fs.install(monkeypatch)
    _with_files(
        monkeypatch,
        fs,
        {
            "/etc/ld.so.conf": b"include ld.so.conf.d/*.conf\n",
            "/etc/ld.so.conf.d/x.conf": b"",
        },
    )
    monkeypatch.setattr(
        plat,
        "linked_libraries",
        lambda p: LibDeps((), ()) if str(p) == "/usr/local/bin/tool" else None,
    )
    problems = plat.approved_cli_problems(TOOL, "linux-x86_64")
    assert (
        "загрузчик ОС: /etc/ld.so.conf.d/x.conf: запись для группы или всех" in problems
    )


@pytest.mark.parametrize("kind", ["dir", "fifo"])
def test_read_regular_refuses_dir_and_fifo(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "libx.dylib"
    if kind == "dir":
        target.mkdir()
    else:
        os.mkfifo(target)
    with pytest.raises(StepFailed, match="не обычный файл"):
        plat.linked_libraries(target)


def test_untrusted_inspection_tools_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _good_fs()
    fs.nodes.update(
        {
            "/usr/bin": (DIR | 0o755, 0, None),
            "/usr/bin/xcrun": (REG | 0o755, 0, None),
            "/usr/bin/readelf": (REG | 0o755, 501, None),
            "/Applications": (DIR | 0o775, 0, None),
            "/Applications/Xcode.app": (DIR | 0o755, 501, None),
            "/Applications/Xcode.app/otool": (REG | 0o755, 501, None),
        }
    )
    fs.install(monkeypatch)
    xcrun = ok(b"/Applications/Xcode.app/otool\n")
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        return xcrun

    monkeypatch.setattr(plat, "run_command", run)
    for magic in (b"\xcf\xfa\xed\xfe", b"\x7fELF"):
        monkeypatch.setattr(plat, "_read_regular", lambda _p, _n, m=magic: m)
        with pytest.raises(StepFailed, match="не доверен"):
            plat.linked_libraries(Path("/x"))
    assert seen == [["/usr/bin/xcrun", "--find", "otool"]]  # nothing inspected


def test_trusted_otool_is_run_by_its_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = _good_fs()
    fs.nodes.update(
        {
            "/usr/bin": (DIR | 0o755, 0, None),
            "/usr/bin/xcrun": (REG | 0o755, 0, None),
            "/Library": (DIR | 0o755, 0, None),
            "/Library/clt": (DIR | 0o755, 0, None),
            "/Library/clt/otool": (REG | 0o755, 0, None),
        }
    )
    fs.install(monkeypatch)
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        if argv[0] == "/usr/bin/xcrun":
            return ok(b"/Library/clt/otool\n")
        return ok(b"/x:\n" if argv[1] == "-L" else b"")

    monkeypatch.setattr(plat, "run_command", run)
    monkeypatch.setattr(plat, "_read_regular", lambda _p, _n: b"\xcf\xfa\xed\xfe")
    assert plat.linked_libraries(Path("/x")) == LibDeps((), ())
    assert [a[0] for a in seen[1:]] == ["/Library/clt/otool", "/Library/clt/otool"]


def test_parse_dscl_native_intended_attribute() -> None:
    data = {
        "dsAttrTypeNative:IsHidden": ["1"],
        "dsAttrTypeNative:accountPolicyData": ["<plist/>"],
        "dsAttrTypeStandard:UniqueID": ["401"],
    }
    assert plat.parse_dscl(plistlib.dumps(data)) == {"IsHidden": "1", "UniqueID": "401"}
    data["dsAttrTypeStandard:IsHidden"] = ["0"]
    with pytest.raises(StepFailed, match="два разных значения IsHidden"):
        plat.parse_dscl(plistlib.dumps(data))


# --- R26: approved CLI policy without a loader model (fix round 3) -------------------

SYS_LIBS = {
    "/usr/lib": (DIR | 0o755, 0, None),
    "/usr/lib/x86_64-linux-gnu": (DIR | 0o755, 0, None),
    "/usr/lib/x86_64-linux-gnu/libz.so.1": (LIB, 0, None),
    "/usr/bin": (DIR | 0o755, 0, None),
    "/usr/bin/xcrun": (REG | 0o755, 0, None),
}


def _policy(
    monkeypatch: pytest.MonkeyPatch,
    table: dict[str, LibDeps],
    platform: PlatformName,
    nodes: dict[str, tuple[int, int, str | None]] | None = None,
    files: dict[str, bytes] | None = None,
    cli: Mapping[str, Path] = TOOL,
) -> list[str]:
    fs = _closure_fs({**SYS_LIBS, **(nodes or {})})
    fs.install(monkeypatch)
    _with_files(monkeypatch, fs, files or {})
    monkeypatch.setattr(plat, "linked_libraries", lambda p: table.get(str(p)))
    monkeypatch.setattr(plat, "_shared_cache_function", lambda: _fake_cache)
    return plat.approved_cli_problems(cli, platform)


def _fake_cache(path: bytes) -> bool:
    """dyld shared cache stand-in: every system path except `*nope*`."""
    return b"nope" not in path


RPATH_PROBLEM = "tool: /usr/local/bin/tool: путь поиска '/usr/lib' запрещён"


def _spelled(prefix: str) -> str:
    return f"tool: /usr/local/bin/tool: зависимость {prefix}/libz.dylib: пути поиска не поддерживаются"


def _tool(deps: LibDeps) -> dict[str, LibDeps]:
    return {"/usr/local/bin/tool": deps}


MAC_OK = LibDeps(("/usr/lib/libSystem.B.dylib",), (), "/usr/lib/dyld")
ELF_OK = LibDeps(("libz.so.1",), (), "/lib64/ld-linux-x86-64.so.2")


@pytest.mark.parametrize("owner,bad", [(0, False), (501, True)])
def test_r26_program_must_be_trusted(
    monkeypatch: pytest.MonkeyPatch, owner: int, bad: bool
) -> None:
    nodes = {"/usr/local/bin/tool": (REG | 0o755, owner, None)}
    problems = _policy(monkeypatch, _tool(MAC_OK), "macos-arm64", nodes)
    assert bool(problems) is bad
    if bad:
        assert "tool: /usr/local/bin/tool: владелец uid 501, не root" in problems


@pytest.mark.parametrize(
    "deps,platform,needle",
    [
        (ELF_OK, "linux-x86_64", None),
        (
            replace(ELF_OK, loader="/usr/local/lib/ld.so"),
            "linux-x86_64",
            "tool: /usr/local/bin/tool: загрузчик /usr/local/lib/ld.so вне системных каталогов",
        ),
        (MAC_OK, "macos-arm64", None),
        (
            replace(MAC_OK, dyld_environment=("DYLD_X=1",)),
            "macos-arm64",
            "tool: /usr/local/bin/tool: LC_DYLD_ENVIRONMENT DYLD_X=1 запрещён",
        ),
    ],
    ids=["elf-system-loader", "elf-foreign-loader", "macho-plain", "macho-dyld-env"],
)
def test_r26_loader_must_be_system(
    monkeypatch: pytest.MonkeyPatch,
    deps: LibDeps,
    platform: PlatformName,
    needle: str | None,
) -> None:
    nodes = {"/usr/local/lib/ld.so": (REG | 0o755, 0, None)}  # trusted, not system
    problems = _policy(monkeypatch, _tool(deps), platform, nodes)
    if needle is None:
        assert problems == []
    else:
        assert needle in problems, problems


@pytest.mark.parametrize(
    "deps,platform,needle",
    [
        (replace(MAC_OK, rpaths=("/usr/lib",)), "macos-arm64", RPATH_PROBLEM),
        (replace(ELF_OK, rpaths=("/usr/lib",)), "linux-x86_64", RPATH_PROBLEM),
        (LibDeps(("@rpath/libz.dylib",), ()), "macos-arm64", _spelled("@rpath")),
        (
            LibDeps(("@loader_path/libz.dylib",), ()),
            "macos-arm64",
            _spelled("@loader_path"),
        ),
        (
            LibDeps(("@executable_path/libz.dylib",), ()),
            "macos-arm64",
            _spelled("@executable_path"),
        ),
        (
            LibDeps(("$ORIGIN/libz.so.1",), ()),
            "linux-x86_64",
            "tool: /usr/local/bin/tool: зависимость $ORIGIN/libz.so.1: пути поиска не поддерживаются",
        ),
        (
            LibDeps(("libz.dylib",), ()),
            "macos-arm64",
            "tool: /usr/local/bin/tool: зависимость libz.dylib: относительное имя, ищется от cwd",
        ),
        (MAC_OK, "macos-arm64", None),
        (ELF_OK, "linux-x86_64", None),
    ],
    ids=[
        "lc-rpath",
        "elf-rpath",
        "at-rpath",
        "at-loader",
        "at-executable",
        "dollar",
        "macho-bare",
        "twin-macho",
        "twin-elf",
    ],
)
def test_r26_search_paths_are_problems(
    monkeypatch: pytest.MonkeyPatch,
    deps: LibDeps,
    platform: PlatformName,
    needle: str | None,
) -> None:
    problems = _policy(monkeypatch, _tool(deps), platform)
    if needle is None:
        assert problems == []
    else:
        assert needle in problems, problems


@pytest.mark.parametrize(
    "dep,bad",
    [
        ("/usr/lib/libSystem.B.dylib", False),  # absent on disk: shared cache
        ("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", False),
        ("/usr/local/lib/libz.dylib", True),  # present and trusted, not system
        ("/usr/lib/../local/lib/libz.dylib", True),
    ],
)
def test_r26_macos_dependency_absolute_under_system(
    monkeypatch: pytest.MonkeyPatch, dep: str, bad: bool
) -> None:
    nodes = {"/usr/local/lib/libz.dylib": (LIB, 0, None)}
    problems = _policy(monkeypatch, _tool(LibDeps((dep,), ())), "macos-arm64", nodes)
    assert bool(problems) is bad, problems


@pytest.mark.parametrize(
    "soname,bad", [("libz.so.1", False), ("libq.so.1", True), ("libmissing.so", True)]
)
def test_r26_linux_soname_in_loader_dirs(
    monkeypatch: pytest.MonkeyPatch, soname: str, bad: bool
) -> None:
    nodes = {"/usr/local/lib/libq.so.1": (LIB, 0, None)}  # trusted, not a loader dir
    problems = _policy(
        monkeypatch, _tool(LibDeps((soname,), ())), "linux-x86_64", nodes
    )
    assert bool(problems) is bad, problems
    if bad:
        assert f"зависимость {soname} не найдена в системных каталогах" in problems[0]


CONF_NODES = {
    "/etc": (DIR | 0o755, 0, None),
    "/etc/ld.so.conf": (LIB, 0, None),
    "/etc/ld.so.conf.d": (DIR | 0o755, 0, None),
}


@pytest.mark.parametrize("owner,bad", [(0, False), (501, True)])
def test_r26_ld_so_conf_directory_trusted(
    monkeypatch: pytest.MonkeyPatch, owner: int, bad: bool
) -> None:
    nodes = {**CONF_NODES, "/opt/libs": (DIR | 0o755, owner, None)}
    files = {"/etc/ld.so.conf": b"/opt/libs\n/usr/lib/x86_64-linux-gnu\n"}
    problems = _policy(monkeypatch, _tool(ELF_OK), "linux-x86_64", nodes, files)
    assert bool(problems) is bad, problems
    if bad:
        assert problems[0] == "загрузчик ОС: /opt/libs: владелец uid 501, не root"


@pytest.mark.parametrize("owner,bad", [(0, False), (501, True)])
def test_r26_include_glob_prefix_checked_without_matches(
    monkeypatch: pytest.MonkeyPatch, owner: int, bad: bool
) -> None:
    nodes = {**CONF_NODES, "/opt/conf.d": (DIR | 0o755, owner, None)}
    files = {"/etc/ld.so.conf": b"include /opt/conf.d/*.conf\n"}
    problems = _policy(monkeypatch, _tool(ELF_OK), "linux-x86_64", nodes, files)
    assert bool(problems) is bad, problems


@pytest.mark.parametrize(
    "content,owner,needle",
    [
        (b"/usr/lib/x86_64-linux-gnu/libz.so.1\n", 0, None),
        (
            b"/opt/evil/libhook.so\n",
            0,
            "загрузчик ОС: /opt/evil: владелец uid 501, не root",
        ),
        (
            b"/usr/lib/x86_64-linux-gnu/libz.so.1\n",
            501,
            "загрузчик ОС: /etc/ld.so.preload: владелец uid 501, не root",
        ),
        (
            b"libhook.so\n",
            0,
            "загрузчик ОС: /etc/ld.so.preload: запись не разобрана: 'libhook.so'",
        ),
    ],
    ids=["system-entry", "untrusted-entry", "untrusted-file", "relative-entry"],
)
def test_r26_ld_so_preload(
    monkeypatch: pytest.MonkeyPatch, content: bytes, owner: int, needle: str | None
) -> None:
    nodes = {
        "/etc": (DIR | 0o755, 0, None),
        "/etc/ld.so.preload": (LIB, owner, None),
        "/opt/evil/libhook.so": (LIB, 501, None),
    }
    files = {"/etc/ld.so.preload": content}
    problems = _policy(monkeypatch, _tool(ELF_OK), "linux-x86_64", nodes, files)
    if needle is None:
        assert problems == []
    else:
        assert needle in problems, problems


def test_r26_host_loader_not_checked_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = {"/etc": (DIR | 0o755, 0, None), "/etc/ld.so.preload": (LIB, 501, None)}
    assert _policy(monkeypatch, _tool(MAC_OK), "macos-arm64", nodes) == []


def test_r26_refusal_names_supported_way(monkeypatch: pytest.MonkeyPatch) -> None:
    problems = _policy(
        monkeypatch, _tool(LibDeps(("@rpath/x.dylib",), ())), "macos-arm64"
    )
    assert problems[-1] == (
        "tool: поддерживаемый способ — системный CLI или "
        "`sudo install -o root -m 0755 <src> /usr/local/libexec/darkfactory/bin/tool`"
    )


def test_n5_user_owned_usr_local_lib_does_not_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = {"/usr/local/lib": (DIR | 0o755, 501, None)}
    deps = LibDeps(("/usr/lib/libSystem.B.dylib",), (), "/usr/lib/dyld")
    assert _policy(monkeypatch, _tool(deps), "macos-arm64", nodes) == []


@MACOS_ONLY
def test_n5_real_binaries_ignore_user_owned_usr_local_lib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not Path("/Library/Developer/CommandLineTools/usr/bin/otool").exists():
        pytest.skip("otool needs the Command Line Tools")
    real = os.lstat

    def lstat(path: os.PathLike[str] | str) -> object:
        st = real(path)
        if os.fspath(path).startswith("/usr/local/lib"):
            return SimpleNamespace(st_mode=st.st_mode, st_uid=501)
        return st

    monkeypatch.setattr(plat, "_lstat", lstat)
    cli = {"chmod": Path("/bin/chmod"), "tar": Path("/usr/bin/tar")}
    assert plat.approved_cli_problems(cli, "macos-arm64") == []


def _shim_world(
    monkeypatch: pytest.MonkeyPatch, resolved: bytes, code: int = 0
) -> list[dict[str, str]]:
    envs: list[dict[str, str]] = []

    def run(argv: Sequence[str], **kw: Any) -> RunResult:
        assert list(argv) == ["/usr/bin/xcrun", "--find", "tool"]
        envs.append(dict(kw["env"]))
        return RunResult(code, resolved, b"xcrun: error: unable to find", True)

    monkeypatch.setattr(plat, "run_command", run)
    return envs


SHIM = LibDeps((plat.XCSELECT_LIB, "/usr/lib/libSystem.B.dylib"), (), "/usr/lib/dyld")
CLT = "/Library/Developer/CommandLineTools/usr/bin/tool"
CLT_NODES = {
    "/Library": (DIR | 0o755, 0, None),
    "/Library/Developer": (DIR | 0o755, 0, None),
    "/Library/Developer/CommandLineTools": (DIR | 0o755, 0, None),
    "/Library/Developer/CommandLineTools/usr": (DIR | 0o755, 0, None),
    "/Library/Developer/CommandLineTools/usr/bin": (DIR | 0o755, 0, None),
    CLT: (REG | 0o755, 0, None),
    "/Applications": (DIR | 0o775, 0, None),
    "/Applications/Xcode.app": (DIR | 0o755, 501, None),
    "/Applications/Xcode.app/tool": (REG | 0o755, 501, None),
}


def test_n6_shim_resolving_to_clt_tool_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVELOPER_DIR", "/Applications/Xcode.app")
    envs = _shim_world(monkeypatch, f"{CLT}\n".encode())
    table = {"/usr/local/bin/tool": SHIM, CLT: MAC_OK}
    assert _policy(monkeypatch, table, "macos-arm64", CLT_NODES) == []
    assert envs and all("DEVELOPER_DIR" not in e for e in envs)


def test_n6_shim_resolving_to_foreign_tool_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shim_world(monkeypatch, b"/Applications/Xcode.app/tool\n")
    table = {"/usr/local/bin/tool": SHIM, "/Applications/Xcode.app/tool": MAC_OK}
    problems = _policy(monkeypatch, table, "macos-arm64", CLT_NODES)
    assert (
        "tool: /usr/local/bin/tool: xcode-select → /Applications/Xcode.app: "
        "владелец uid 501, не root"
    ) in problems


def test_n6_shim_resolving_to_bad_closure_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shim_world(monkeypatch, f"{CLT}\n".encode())
    table = {"/usr/local/bin/tool": SHIM, CLT: LibDeps(("@rpath/x.dylib",), ())}
    problems = _policy(monkeypatch, table, "macos-arm64", CLT_NODES)
    assert (
        f"tool: /usr/local/bin/tool: xcode-select → {CLT}: "
        "зависимость @rpath/x.dylib: пути поиска не поддерживаются"
    ) in problems, problems


def test_n6_shim_unresolved_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    _shim_world(monkeypatch, b"", code=1)
    problems = _policy(monkeypatch, _tool(SHIM), "macos-arm64", CLT_NODES)
    assert any("xcode-select: xcrun --find tool" in p for p in problems), problems


def test_n6_role_env_has_no_developer_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVELOPER_DIR", "/Applications/Xcode.app")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def execute(argv: Sequence[str], **kw: Any) -> RunResult:
        calls.append((list(argv), dict(kw["env"])))
        return ok()

    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(plat, "_execute", execute)
    plat.run_as(
        "df-build", ["/usr/bin/git"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    plat.run_as(
        "df-build",
        ["/usr/bin/git"],
        env={"DEVELOPER_DIR": "/x"},
        cwd=Path("/"),
        network=False,
        timeout=5,
    )
    first, second = calls
    assert not any("DEVELOPER_DIR" in a for a in first[0])
    assert "DEVELOPER_DIR" not in first[1] and "DEVELOPER_DIR" not in second[1]
    assert "DEVELOPER_DIR=/x" in second[0]  # only because the caller asked
    FakePopen.instances.clear()
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    plat.spawn_as("df-build", ["/usr/bin/tar"], env={}, cwd=Path("/"), network=False)
    spawned = FakePopen.instances[0]
    assert "DEVELOPER_DIR" not in spawned.kwargs["env"]
    assert not any("DEVELOPER_DIR" in a for a in spawned.argv)


@MACOS_ONLY
def test_real_mac_system_clis_pass() -> None:
    clt = Path("/Library/Developer/CommandLineTools/usr/bin")
    if not (clt / "otool").exists() or not (clt / "git").exists():
        pytest.skip("needs the Command Line Tools")
    cli = {
        "git": Path("/usr/bin/git"),
        "tar": Path("/usr/bin/tar"),
        "chmod": Path("/bin/chmod"),
    }
    assert plat.approved_cli_problems(cli, "macos-arm64") == []


# --- final fix wave ---------------------------------------------------------------


def test_f1_large_stderr_success_keeps_tail(tmp_path: Path) -> None:
    script = "head -c 300000 /dev/zero | tr '\\0' e >&2; printf END >&2; echo ok"
    result = plat._execute(
        ["/bin/sh", "-c", script], env={}, cwd=tmp_path, timeout=30, limit=1000
    )
    assert result.returncode == 0 and result.stdout == b"ok\n"
    assert result.stderr.endswith(b"eeeEND")
    assert len(result.stderr) == plat.STDERR_TAIL_BYTES
    assert result.stderr_truncated and not result.stdout_truncated
    assert plat.STDERR_TAIL_BYTES >= 4096  # Task 6 reports the last 4 KiB


def test_f1_stdout_overflow_flagged(tmp_path: Path) -> None:
    result = plat._execute(
        ["/bin/sh", "-c", "yes"], env={}, cwd=tmp_path, timeout=20, limit=4096
    )
    assert result.stdout_truncated and not result.timed_out
    assert result.returncode != 0


@pytest.mark.parametrize(
    "result,reaped",
    [
        (RunResult(0, b"", b"", True), False),
        (RunResult(-9, b"x", b"", True, stdout_truncated=True), True),
        (RunResult(-9, b"", b"", True, timed_out=True), True),
        (RunResult(0, b"", b"e" * 10, True, stderr_truncated=True), False),
    ],
    ids=["normal", "stdout-overflow", "timeout", "stderr-tail"],
)
def test_f1_run_as_reaps_on_every_abnormal_exit(
    monkeypatch: pytest.MonkeyPatch, result: RunResult, reaped: bool
) -> None:
    calls: list[str] = []
    _capture_execute(monkeypatch, "macos-arm64", result)
    monkeypatch.setattr(plat, "reap_user_processes", lambda u: calls.append(u) or [])
    plat.run_as(
        "df-build", ["/bin/true"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    assert calls == (["df-build"] if reaped else [])


def test_f1_truncated_mechanism_text_is_not_a_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tail = RunResult(1, b"", b"sudo: x", True, stderr_truncated=True)
    _capture_execute(monkeypatch, "macos-arm64", tail)
    result = plat.run_as(
        "df-build", ["/bin/true"], env={}, cwd=Path("/"), network=False, timeout=5
    )
    assert result.launched


def test_f2_launch_check_returns_once_stdout_has_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakePopen.instances.clear()
    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    started = time.monotonic()
    plat.spawn_as("df-build", ["/usr/bin/tar"], env={}, cwd=Path("/"), network=False)
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize(
    "script,expect",
    [
        ("printf x; sleep 5", None),  # data pending: launched, nothing consumed
        ("exit 3", 3),  # ended in the window: its code
        ("exec 1>&-; sleep 0.1; exit 4", 4),  # stdout EOF, then the code
    ],
)
def test_f2_launch_outcome_real_process(script: str, expect: int | None) -> None:
    proc = subprocess.Popen(
        ["/bin/sh", "-c", script], stdout=subprocess.PIPE, start_new_session=True
    )
    assert proc.stdout is not None
    try:
        started = time.monotonic()
        code = plat._launch_outcome(proc, proc.stdout.fileno(), started + 2.0)
        assert code == expect
        assert time.monotonic() - started < 1.0
        if expect is None:
            assert os.read(proc.stdout.fileno(), 1) == b"x"  # not consumed
    finally:
        plat.kill_group(proc.pid)
        proc.wait(5)
        proc.stdout.close()


def test_f3_producer_stderr_bounded_and_never_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plat, "reap_user_processes", lambda _u: [])  # never pkill me
    script = "head -c 2000000 /dev/zero | tr '\\0' e >&2; printf TAIL >&2; printf out"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    prod = plat.SpawnedProducer(proc, "df-build", 5.0)
    assert prod.wait(20) == 0  # a full stderr pipe did not block the producer
    assert os.read(prod.fd, 16) == b"out"
    prod.end()
    tail = prod.stderr_tail()
    assert tail.endswith(b"eeTAIL") and len(tail) == plat.STDERR_TAIL_BYTES


@MACOS_ONLY
def test_o1_shared_cache_real_paths() -> None:
    target = Path("/usr/bin/x")
    assert plat._shared_cache_problems(target, "/usr/lib/libSystem.B.dylib") == []
    problems = plat._shared_cache_problems(target, "/usr/lib/libnope-df.dylib")
    assert problems == [
        "/usr/bin/x: зависимость /usr/lib/libnope-df.dylib отсутствует (fallback dyld)"
    ]


@pytest.mark.parametrize(
    "dep,cache,needle",
    [
        ("/usr/lib/libSystem.B.dylib", "fake", None),
        ("/usr/lib/libnope.dylib", "fake", "fallback dyld"),
        ("/usr/lib/libSystem.B.dylib", "missing", "нельзя проверить кеш dyld"),
    ],
)
def test_o1_absent_system_dependency_needs_shared_cache(
    monkeypatch: pytest.MonkeyPatch, dep: str, cache: str, needle: str | None
) -> None:
    deps = LibDeps((dep,), (), "/usr/lib/dyld")
    fs = _closure_fs(SYS_LIBS)
    fs.install(monkeypatch)
    _with_files(monkeypatch, fs, {})
    monkeypatch.setattr(plat, "linked_libraries", lambda p: deps)
    function = _fake_cache if cache == "fake" else None
    monkeypatch.setattr(plat, "_shared_cache_function", lambda: function)
    problems = plat.approved_cli_problems(TOOL, "macos-arm64")
    if needle is None:
        assert problems == []
    else:
        assert any(needle in p for p in problems), problems


@pytest.mark.parametrize("owner,bad", [(0, False), (501, True)])
def test_o2_ld_so_conf_dir_under_system_prefix_is_walked(
    monkeypatch: pytest.MonkeyPatch, owner: int, bad: bool
) -> None:
    nodes = {**CONF_NODES, "/usr/lib/extra": (DIR | 0o755, owner, None)}
    files = {"/etc/ld.so.conf": b"/usr/lib/extra\n"}
    problems = _policy(monkeypatch, _tool(ELF_OK), "linux-x86_64", nodes, files)
    assert bool(problems) is bad, problems


@pytest.mark.parametrize("owner,bad", [(0, False), (501, True)])
def test_o2_preload_entry_under_system_prefix_is_walked(
    monkeypatch: pytest.MonkeyPatch, owner: int, bad: bool
) -> None:
    nodes = {
        "/etc": (DIR | 0o755, 0, None),
        "/etc/ld.so.preload": (LIB, 0, None),
        "/usr/lib/x86_64-linux-gnu/libhook.so": (LIB, owner, None),
    }
    files = {"/etc/ld.so.preload": b"/usr/lib/x86_64-linux-gnu/libhook.so\n"}
    problems = _policy(monkeypatch, _tool(ELF_OK), "linux-x86_64", nodes, files)
    assert bool(problems) is bad, problems


def test_o3_nested_shim_refused_with_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _shim_world(monkeypatch, f"{CLT}\n".encode())
    table = {"/usr/local/bin/tool": SHIM, CLT: SHIM}  # CLT tool is a wrapper too
    problems = _policy(monkeypatch, table, "macos-arm64", CLT_NODES)
    assert any("обёртка xcode-select" in p and "make → gnumake" in p for p in problems)
    assert not any("указывает на сам shim" in p for p in problems)


@pytest.mark.parametrize("name,bad", [("foo", True), ("tool", False)])
def test_s7_shim_link_with_other_name_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: bool
) -> None:
    _shim_world(monkeypatch, f"{CLT}\n".encode())
    nodes = {**CLT_NODES, "/opt/bin": (DIR | 0o755, 0, None)}
    nodes[f"/opt/bin/{name}"] = (LNK | 0o777, 0, "/usr/local/bin/tool")
    table = {"/usr/local/bin/tool": SHIM, CLT: MAC_OK}
    cli = {"x": Path(f"/opt/bin/{name}")}
    problems = _policy(monkeypatch, table, "macos-arm64", nodes, cli=cli)
    assert bool(problems) is bad, problems
    if bad:
        assert any("по имени 'foo'" in p for p in problems)


@MACOS_ONLY
def test_o3_real_make_verdict_follows_clt() -> None:
    """R97: on some hosts CLT make is itself an xcode-select shim (make ->
    gnumake), and df refuses `/usr/bin/make` as a nested wrapper; where it is
    a real binary (the CI runner), df accepts it. Either way the real host's
    verdict is asserted, never skipped (PR #632 review)."""
    make = Path("/Library/Developer/CommandLineTools/usr/bin/make")
    if not make.exists() or not Path("/usr/bin/make").exists():
        pytest.skip("needs the Command Line Tools")
    deps = plat.linked_libraries(make)
    assert deps is not None, make
    problems = plat.approved_cli_problems(
        {"make": Path("/usr/bin/make")}, "macos-arm64"
    )
    if plat.XCSELECT_LIB in deps.needed:
        assert any("обёртка xcode-select" in p for p in problems), problems
    else:
        assert problems == [], problems


def test_drain_thread_owns_its_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    """end() never closes the fd the drain thread reads; the thread closes it."""

    class HeldStderr(FakePopen):
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            super().__init__(argv, **kwargs)
            err_r, self.err_w = os.pipe()  # a writer keeps stderr open
            self.stderr.close()
            self.stderr = os.fdopen(err_r, "rb")

    proc = HeldStderr(["tar"])
    proc.exit_on_wait = 0
    monkeypatch.setattr(os, "killpg", lambda _p, _s: None)
    monkeypatch.setattr(plat, "reap_user_processes", lambda _u: [])
    prod = plat.SpawnedProducer(proc, "df-build", 0.2)  # type: ignore[arg-type]
    drain_fd = prod._drain_fd
    os.write(proc.err_w, b"warn")
    prod.end()  # the join times out: the thread is still in os.read
    assert prod._drain.is_alive()
    os.fstat(drain_fd)  # still open, still the thread's
    os.close(proc.err_w)
    prod._drain.join(5)
    assert not prod._drain.is_alive()
    with pytest.raises(OSError):
        os.fstat(drain_fd)  # closed by its owner
    assert prod.stderr_tail() == b"warn"


# --- executable format (PR #622 review) ------------------------------------------

MACHO_MAGICS = [
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
]


def _format_problems(monkeypatch: pytest.MonkeyPatch, head: bytes) -> list[str]:
    fs = _good_fs()
    fs.nodes["/usr/bin"] = (DIR | 0o755, 0, None)
    fs.nodes["/usr/bin/xcrun"] = (REG | 0o755, 0, None)  # trusted: not the refusal
    fs.install(monkeypatch)
    monkeypatch.setattr(plat, "_read_regular", lambda _p, n: head[:n])
    return plat.approved_cli_problems(TOOL, "macos-arm64")


@pytest.mark.parametrize("magic", MACHO_MAGICS, ids=lambda m: m.hex())
def test_every_macho_magic_is_parsed_or_refused(
    monkeypatch: pytest.MonkeyPatch, magic: bytes
) -> None:
    seen: list[list[str]] = []

    def run(argv: Sequence[str], **_kw: object) -> RunResult:
        seen.append(list(argv))
        return RunResult(1, b"", b"", True)

    monkeypatch.setattr(plat, "run_command", run)
    problems = _format_problems(monkeypatch, magic + bytes(60))
    # the Mach-O branch ran: it asked xcrun for otool and refused without CLT
    assert seen == [["/usr/bin/xcrun", "--find", "otool"]]
    assert problems == [
        f"tool: {plat._NO_CLT}",
        f"tool: {plat._INSTALL_HINT.format(name='tool')}",
    ]


@pytest.mark.parametrize(
    "head,unknown",
    [
        (b"\x00asm\x01\x00\x00\x00", True),
        (b"MZ\x90\x00", True),
        (b"#!/bin/sh\n", False),
    ],
    ids=["wasm", "pe", "script"],
)
def test_unknown_executable_format_is_a_problem(
    monkeypatch: pytest.MonkeyPatch, head: bytes, unknown: bool
) -> None:
    fs = _good_fs()
    fs.nodes["/bin"] = (DIR | 0o755, 0, None)
    fs.nodes["/bin/sh"] = (REG | 0o755, 0, None)
    fs.install(monkeypatch)
    heads = {"/usr/local/bin/tool": head}
    monkeypatch.setattr(plat, "_read_regular", lambda p, n: heads.get(str(p), b"")[:n])
    monkeypatch.setattr(
        plat,
        "linked_libraries",
        lambda p: LibDeps((), ()) if str(p) == "/bin/sh" else None,
    )
    problems = plat.approved_cli_problems(TOOL, "macos-arm64")
    expected = "tool: /usr/local/bin/tool: неизвестный формат исполняемого файла"
    assert (expected in problems) is unknown, problems
    if not unknown:
        assert problems == []
