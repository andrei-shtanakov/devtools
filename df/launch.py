"""`launch`: the administrative part of starting a role (spec 4.2, rev 9).

`launch exec <role> <op>` is `df-launch` after `sudo` made the process the
role: still the administrative Python and code, before any code of a
release runs. In this order it closes inherited descriptors, compares the
process's UID/GID with the role's `accounts.toml` record, changes to the
role's working directory, scrubs the environment to an allowlist, chooses
the release (the snapshot's for `check`/`check-github`; for `run`/`review`
the one in the run record) and `execve`s the release's interpreter on
`launch_release.py`. `darkfactory launch <role> <op>` is the human's
unprivileged entry: `chdir("/")`, close descriptors, `execv` of `sudo`.
"""

from __future__ import annotations

import os
import re
import resource
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from df import fsutil
from df import platform as plat
from df.config import ConfigError, ReleaseEntry, load_accounts
from df.journal import StepFailed
from df.paths import Layout, default_layout
from df.roles import MODEL_ROLE_USERS
from df.stage2_config import RunRecord, parse_run
from df.state import Snapshot

ENV_ALLOW = ("HOME", "PATH", "LANG", "TZ")
SUDO = "/usr/bin/sudo"
#: The repository of the release that holds `df` (stage-1 layout of `build`:
#: `venv/<repo>`, `src/<repo>`).
RELEASE_REPO = "devtools"
LAUNCH_SCRIPT = ("df", "launch_release.py")
#: Operations that take the snapshot's release, and those that take the one
#: in the run record (`run_id` on stdin).
CURRENT_OPS = frozenset({"check", "check-github"})
RECORD_OPS = frozenset({"run", "review"})
#: What each role may be asked for.
ROLE_OPS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "df-exec": frozenset({"check", "check-github", "run"}),
        "df-review": frozenset({"check", "review"}),
    }
)
#: What the human's `darkfactory launch` may ask for: exactly the operator
#: rules of `sudoers.render`. `run` goes through `df-run-start` and `review`
#: through `df-review-run`, never through the operator's `df-launch` rule.
HUMAN_OPS: Mapping[str, frozenset[str]] = MappingProxyType(
    {"df-exec": frozenset({"check", "check-github"}), "df-review": frozenset({"check"})}
)
MAX_RUN_ID = 256
MAX_RECORD = 1 << 20
RUN_ID = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)
_RECORD_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_FD_CAP = 1 << 16
_STEP = "launch"

Stdin = Callable[[int], bytes]


def role_env(layout: Layout, role: str) -> dict[str, str]:
    """The whole environment of the role's process: the allowlist, fixed."""
    return {
        "HOME": str(layout.role_home(role)),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }


def role_cwd(layout: Layout, role: str) -> Path:
    """The role's working directory: `<home>/work`."""
    return layout.role_home(role) / "work"


def exec_main(argv: Sequence[str], *, layout: Layout, snap: Snapshot) -> int:
    """`launch exec <role> <op>`: `argv = [role, op]`; `execve`s the release.

    Returns only if `execve` does not replace the process (a test's fake).
    """
    os.closerange(3, _fd_limit())
    role, op = _role_and_op(argv)
    _check_identity(layout, role)
    os.chdir(role_cwd(layout, role))
    env = role_env(layout, role)
    os.environ.clear()
    os.environ.update(env)
    release_id, release_dir, run_id = release_for(layout, op, snap, _stdin)
    python = release_dir / "venv" / RELEASE_REPO / "bin" / "python"
    script = release_dir / "src" / RELEASE_REPO / Path(*LAUNCH_SCRIPT)
    args = [str(python), "-I", "-B", str(script), role, op, release_id]
    os.execve(python, [*args, run_id or "-"], env)
    return 1


def human_refusal(role: str, op: str) -> str | None:
    """Why `darkfactory launch <role> <op>` is refused (`None`: it is not)."""
    if op in HUMAN_OPS.get(role, ()):
        return None
    if op in ROLE_OPS.get(role, ()):
        return (
            f"{role} {op}: для человека это не вход; прогон начинает "
            "`darkfactory run start|resume` (df-run-start), ревью — df-review-run"
        )
    return f"{role}: операция {op!r} не допускается"


def human_main(argv: Sequence[str], *, layout: Layout | None = None) -> int:
    """`darkfactory launch <role> <op>`: `chdir("/")`, close descriptors,
    `execv` of `sudo -n -u <role> <libexec>/df-launch <role> <op>` (decision
    12). `sudo` opens the caller's directory as root first, hence the order.
    """
    role, op = _role_and_op(argv)
    reason = human_refusal(role, op)
    if reason is not None:
        raise StepFailed(_STEP, reason)
    lay = default_layout() if layout is None else layout
    os.chdir("/")
    os.closerange(3, _fd_limit())
    wrapper = str(lay.libexec / "df-launch")
    os.execv(SUDO, ["sudo", "-n", "-u", role, wrapper, role, op])
    return 1


def release_for(
    layout: Layout, op: str, snap: Snapshot, stdin: Stdin
) -> tuple[str, Path, str | None]:
    """`(release_id, release dir, run_id)` for `op`; a refusal is `StepFailed`.

    `check`/`check-github` run on the snapshot's `current` (`run_id` is
    `None`). `run`/`review` run on the release of the run record named on
    stdin, never on `current`; that release must be `allowed` in the
    snapshot's registry with the record's digest (`release_revoked`,
    `release_unknown`, `release_digest_mismatch`).
    """
    if op in CURRENT_OPS:
        if snap.release_id is None:
            raise StepFailed(_STEP, "нет активного релиза: release_unknown")
        _allowed_entry(snap, snap.release_id)
        return snap.release_id, layout.release_dir(snap.release_id), None
    if op not in RECORD_OPS:
        raise StepFailed(_STEP, f"неизвестная операция {op!r}")
    run_id = _read_run_id(stdin)
    rec = read_record(layout, run_id)
    entry = _allowed_entry(snap, rec.release_id)
    if entry.digest != rec.release_digest:
        raise StepFailed(
            _STEP, f"release_digest_mismatch: запись {run_id} и реестр расходятся"
        )
    return rec.release_id, layout.release_dir(rec.release_id), run_id


def _allowed_entry(snap: Snapshot, release_id: str) -> ReleaseEntry:
    registry = () if snap.releases is None else snap.releases.releases
    entry = next((e for e in registry if e.id == release_id), None)
    if entry is None:
        raise StepFailed(_STEP, f"release_unknown: {release_id} нет в реестре")
    if entry.status != "allowed":
        why = entry.reason or "без причины"
        raise StepFailed(_STEP, f"release_revoked: {release_id} отозван ({why})")
    return entry


def _role_and_op(argv: Sequence[str]) -> tuple[str, str]:
    if len(argv) != 2:
        raise StepFailed(_STEP, f"нужно <роль> <операция>, получено {list(argv)}")
    role, op = argv
    if role not in MODEL_ROLE_USERS:
        raise StepFailed(_STEP, f"неизвестная роль {role!r}")
    if op not in ROLE_OPS[role]:
        raise StepFailed(_STEP, f"{role}: операция {op!r} не допускается")
    return role, op


def _check_identity(layout: Layout, role: str) -> None:
    """Real and effective UID and GID are the role's record, which is `ready`."""
    try:
        account = load_accounts(layout.accounts).get(role)
    except ConfigError as exc:
        raise StepFailed(_STEP, f"accounts.toml: {exc}") from exc
    if account is None or account.state != "ready":
        raise StepFailed(_STEP, f"{role}: нет готовой записи в accounts.toml")
    want = (account.uid, account.uid, account.gid, account.gid)
    have = (os.getuid(), os.geteuid(), os.getgid(), os.getegid())
    if have != want:
        raise StepFailed(
            _STEP,
            f"процесс uid/euid/gid/egid {have}, у {role} в accounts.toml {want}",
        )


def _fd_limit() -> int:
    """Above every descriptor this process can have open."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return max(_FD_CAP, soft) if hard == resource.RLIM_INFINITY else hard


def _stdin(size: int) -> bytes:
    return os.read(0, size)


def _read_run_id(stdin: Stdin) -> str:
    data = b""
    while len(data) <= MAX_RUN_ID:
        chunk = stdin(MAX_RUN_ID + 1 - len(data))
        if not chunk:
            break
        data += chunk
    if len(data) > MAX_RUN_ID:
        raise StepFailed(_STEP, f"run_id длиннее {MAX_RUN_ID} байт")
    text = data.decode("ascii", errors="replace").removesuffix("\n")
    if not RUN_ID.fullmatch(text):
        raise StepFailed(_STEP, "run_id: допустимы [A-Za-z0-9._-]+")
    return text


def read_record(layout: Layout, run_id: str) -> RunRecord:
    """The run record `runs/<run_id>.json`: root's regular file, no links."""
    path = layout.runs_dir / f"{run_id}.json"
    text = read_root_file(path)
    assert text is not None
    try:
        rec = parse_run(text)
    except ConfigError as exc:
        raise StepFailed(_STEP, f"{path}: {exc}") from exc
    if rec.run_id != run_id:
        raise StepFailed(_STEP, f"{path}: в записи run_id {rec.run_id!r}")
    return rec


def read_root_file(path: Path, *, optional: bool = False) -> str | None:
    """A regular file of root without links, not writable by others, on a
    trusted path; `None` if it is absent and `optional`."""
    try:
        fd = os.open(path, _RECORD_FLAGS)
    except FileNotFoundError as exc:
        if optional:
            return None
        raise StepFailed(_STEP, f"{path}: {exc.strerror or exc}") from exc
    except OSError as exc:
        raise StepFailed(_STEP, f"{path}: {exc.strerror or exc}") from exc
    try:
        st = os.fstat(fd)
        bad = _record_problem(path, st)
        if bad:
            raise StepFailed(_STEP, bad)
        data = os.read(fd, MAX_RECORD + 1)
    except OSError as exc:
        raise StepFailed(_STEP, f"{path}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)
    problems = plat.trusted_path_problems(path)
    if problems:
        raise StepFailed(_STEP, "; ".join(problems))
    if len(data) > MAX_RECORD:
        raise StepFailed(_STEP, f"{path}: больше {MAX_RECORD} байт")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StepFailed(_STEP, f"{path}: не UTF-8: {exc}") from exc


def _record_problem(path: Path, st: os.stat_result) -> str | None:
    if not stat.S_ISREG(st.st_mode):
        return f"{path}: не обычный файл"
    if fsutil.owner_uid(st) != plat.ROOT_UID:
        return f"{path}: владелец uid {fsutil.owner_uid(st)}, не root"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"{path}: запись для группы или всех"
    if st.st_nlink != 1:
        return f"{path}: жёстких ссылок {st.st_nlink}, нужна одна"
    return None
