"""`darkfactory roles setup` (spec 4.1-4.2; stage 2a, Task 4).

A writer: the caller holds the writer lock for the whole run. The steps, in
this order, each settled by its own check so that a repeat changes nothing:

1. `roles`: `ensure_roles` -- the accounts are created one after another,
   only here and only under the lock (`platform._free_id` ignores `creating`
   intents, so two parallel creations could take the same id);
2. `dirs`: the stage-2 directories, born in their final owner and mode;
3. `publisher-config`: `/etc/df/publisher.toml` from the accounts;
4. `perimeter-init`: the perimeter database, created as `df-publisher` by the
   admin Python of `current` running the entry (`publisher perimeter-init`),
   then root's `/var/lib/df/perimeter.created`; a marker (either) without the
   database is lost and refused, never recreated;
5. `libexec`: the wrappers, for the admin Python of `current`;
6. `sudoers`: `/etc/sudoers.d/df`;
7. `verify`: wrappers and the effective sudo policy of five names (always run,
   once; a failure names the retry).
"""

from __future__ import annotations

import os
import pwd
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from df import fsutil, journal, libexec, roles, sudoers
from df import platform as plat
from df.config import Account, ConfigError, load_accounts
from df.journal import Step, StepFailed, run_steps
from df.paths import Layout
from df.publisher import perimeter
from df.stage2_config import dump_publisher, expected_publisher
from df.state import Snapshot

STEP_NAMES = (
    "roles",
    "dirs",
    "publisher-config",
    "perimeter-init",
    "libexec",
    "sudoers",
    "verify",
)
PUBLISHER_MODE = 0o644
CREATED_MODE = 0o644
SUDOERS_MODE = 0o440
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
#: Directory states `do()` settles: absent, a mode `make_dir` repairs, ours but
#: still root's (a crash between `mkdir` and `fchown`).
_NEEDS_DO = frozenset({"absent", "repair", "unowned"})
_RETRY = "; исправьте и повторите darkfactory roles setup"
PUBLISHER_USER = "df-publisher"
#: The environment of `perimeter-init` as the role (`env -i`).
_ROLE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
_INIT_TIMEOUT = 60.0
_NO_OPENSSL = (
    "openssl: нет в install.toml (approved_cli.openssl): задайте путь "
    "командой sudo darkfactory cli set openssl /usr/bin/openssl"
)


@dataclass(frozen=True)
class DirSpec:
    """A directory of stage 2a: its owner (`None` is root) and exact mode."""

    path: Path
    owner: str | None
    mode: int


def dir_specs(layout: Layout) -> tuple[DirSpec, ...]:
    """The directories of step `dirs` (spec 3.3, 4.2), in creation order."""
    publisher = "df-publisher"
    return (
        DirSpec(layout.libexec, None, 0o755),
        DirSpec(layout.runs_dir, None, 0o755),
        DirSpec(layout.publisher_dir, publisher, 0o700),
        DirSpec(layout.perimeter_dir, publisher, 0o700),
        DirSpec(layout.secrets_dir, publisher, 0o700),
        DirSpec(layout.role_home("df-exec") / "work", "df-exec", 0o700),
        DirSpec(layout.role_home("df-review") / "work", "df-review", 0o700),
    )


def run(layout: Layout, *, operator: str, snap: Snapshot) -> None:
    """Set up the roles, directories, wrappers and sudoers for `operator`.

    Everything that can be refused is refused before the first step. The
    caller holds the writer lock."""
    check_preconditions(layout, operator, snap)
    # a re-run may change nothing: a stale odd generation is closed anyway
    journal.close_odd_generation(
        layout,
        generation=snap.generation,
        pending_present=snap.pending_present,
        current=snap.release_id,
    )
    python = libexec.admin_python_of(layout, snap)
    run_steps(
        _steps(layout, operator, python),
        layout.journal,
        run_id=uuid.uuid4().hex,
    )


def check_preconditions(layout: Layout, operator: str, snap: Snapshot) -> None:
    """The operator, the approved `openssl` and the active release."""
    try:
        sudoers.render(layout, operator)
    except ValueError as exc:
        raise StepFailed("operator", str(exc)) from exc
    try:
        pwd.getpwnam(operator)
    except KeyError as exc:
        raise StepFailed("operator", f"нет учётной записи {operator!r}") from exc
    install = snap.install
    if install is None:
        raise StepFailed(
            "install", "нет /etc/df/install.toml: начните с bootstrap init"
        )
    openssl = install.approved_cli.get("openssl")
    if openssl is None:
        raise StepFailed("openssl", _NO_OPENSSL)
    problems = plat.approved_cli_problems({"openssl": openssl}, plat.detect_platform())
    if problems:
        raise StepFailed("openssl", "; ".join(problems))
    libexec.admin_python_of(layout, snap)


def _steps(layout: Layout, operator: str, python: Path) -> list[Step]:
    def wrapper_problems() -> list[str]:
        return libexec.wrapper_problems(layout, python)

    def sudoers_text() -> str:
        return sudoers.render(layout, operator)

    def sudoers_problems() -> list[str]:
        return libexec.root_file_problems(
            sudoers.SUDOERS_FILE, sudoers_text(), SUDOERS_MODE
        )

    verified: list[bool] = []

    def verify() -> None:
        """One probe per run; a pure check would be run three times."""
        problems = _verify_problems(layout, operator, python)
        if problems:
            raise StepFailed("verify", "; ".join(problems) + _RETRY)
        verified.append(True)

    return [
        Step(
            "roles",
            lambda: not _role_problems(layout),
            lambda: _ensure_roles(layout),
            lambda: _role_problems(layout),
        ),
        Step("dirs", lambda: _dirs_ready(layout), lambda: _make_dirs(layout)),
        Step(
            "publisher-config",
            lambda: not _publisher_problems(layout),
            lambda: _write_publisher(layout),
            lambda: _publisher_problems(layout),
        ),
        Step(
            "perimeter-init",
            lambda: _perimeter_ready(layout),
            lambda: _perimeter_init(layout, python),
            lambda: _perimeter_problems(layout),
        ),
        Step(
            "libexec",
            lambda: not wrapper_problems(),
            lambda: libexec.install_wrappers(layout, python),
            wrapper_problems,
        ),
        Step(
            "sudoers",
            lambda: not sudoers_problems(),
            lambda: sudoers.install(sudoers_text()),
            sudoers_problems,
        ),
        Step("verify", lambda: bool(verified), verify),
    ]


# --- roles ---------------------------------------------------------------------


def _ensure_roles(layout: Layout) -> None:
    """The only creation of the roles; the caller holds the writer lock."""
    roles.ensure_roles(layout)


def _role_problems(layout: Layout) -> list[str]:
    problems: list[str] = []
    for name in roles.ROLE_USERS:
        problems += plat.verify_service_user(layout, name)
    return problems


# --- directories ---------------------------------------------------------------


def _accounts(layout: Layout) -> dict[str, Account]:
    try:
        return load_accounts(layout.accounts)
    except ConfigError as exc:
        raise StepFailed("dirs", f"accounts.toml: {exc}") from exc


def _dirs_ready(layout: Layout) -> bool:
    """Every directory is as specified; absent (or a root-owned empty role
    directory, a crash between `mkdir` and `fchown`) asks for `do()`;
    anything else that differs refuses."""
    accounts = _accounts(layout)
    problems: list[str] = []
    missing = False
    for spec in dir_specs(layout):
        found = _dir_state(spec, accounts)
        if found in _NEEDS_DO:
            missing = True
        elif found != "ok":
            problems.append(found)
    if problems:
        raise StepFailed("dirs", "; ".join(problems))
    return not missing


def _dir_state(spec: DirSpec, accounts: dict[str, Account]) -> str:
    """`ok`, a state of `_NEEDS_DO`, or a problem text."""
    try:
        st = os.lstat(spec.path)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return f"{spec.path}: {exc.strerror or exc}"
    if not stat.S_ISDIR(st.st_mode):
        return f"{spec.path}: не каталог"
    have = stat.S_IMODE(st.st_mode)
    want = plat.ROOT_UID if spec.owner is None else _account(accounts, spec.owner).uid
    owner = fsutil.owner_uid(st)
    if have != spec.mode:
        if (
            owner == plat.ROOT_UID
            and fsutil.repairable(st, spec.mode)
            and (spec.owner is None or not os.listdir(spec.path))
        ):
            return "repair"  # a crash left an inherited S_ISGID
        return (
            f"{spec.path}: права {have:04o}, нужны {spec.mode:04o}; "
            f"исправьте (chmod {spec.mode:04o} {spec.path}) или удалите каталог"
        )
    if owner == want:
        return "ok"
    if owner == plat.ROOT_UID and not os.listdir(spec.path):
        return "unowned"
    return f"{spec.path}: владелец uid {owner}, нужен {want}"


def _account(accounts: dict[str, Account], name: str) -> Account:
    account = accounts.get(name)
    if account is None or account.state != "ready":
        raise StepFailed("dirs", f"нет готовой учётки {name}")
    return account


def _make_dirs(layout: Layout) -> None:
    accounts = _accounts(layout)
    for spec in dir_specs(layout):
        owner = None if spec.owner is None else _account(accounts, spec.owner)
        try:
            _make_dir(spec, owner)
        except OSError as exc:
            raise StepFailed("dirs", f"{spec.path}: {exc}") from exc


def _make_dir(spec: DirSpec, owner: Account | None) -> None:
    """Born at the final mode as root's, then handed over while still empty
    and unreachable by anyone else (`0700` or root's `0755`)."""
    parent = os.open(spec.path.parent, _DIR_FLAGS)
    try:
        fsutil.make_dir(parent, spec.path.name, spec.mode, path=spec.path)
        if owner is not None:
            _hand_over(parent, spec, owner)
    finally:
        os.close(parent)


def _hand_over(parent: int, spec: DirSpec, owner: Account) -> None:
    fd = os.open(spec.path.name, _DIR_FLAGS, dir_fd=parent)
    try:
        if fsutil.owner_uid(os.fstat(fd)) == owner.uid:
            return
        os.fchown(fd, owner.uid, owner.gid)
        fsutil.fault_point("chown", spec.path)
        fsutil.sync_dir(fd)
    finally:
        os.close(fd)
    fsutil.sync_dir(parent)


# --- publisher.toml ------------------------------------------------------------


def _publisher_text(layout: Layout) -> str:
    return dump_publisher(expected_publisher(load_accounts(layout.accounts)))


def _publisher_problems(layout: Layout) -> list[str]:
    return libexec.root_file_problems(
        layout.etc / "publisher.toml", _publisher_text(layout), PUBLISHER_MODE
    )


def _write_publisher(layout: Layout) -> None:
    """The only write under `/etc/df`: inside a generation bracket, so that a
    doctor that sees the temp of `write_atomic` says «идёт запись», not
    «осталась после сбоя». A crash leaves the bracket odd; the next writer
    (`generation_begin` keeps an odd value) closes it."""
    path = layout.etc / "publisher.toml"
    journal.generation_begin(layout)
    try:
        fsutil.write_atomic(path, _publisher_text(layout), PUBLISHER_MODE)
    except OSError as exc:
        journal.generation_end(layout)  # a refused write leaves the old file whole
        raise StepFailed("publisher-config", f"{path}: {exc}") from exc
    journal.generation_end(layout)


# --- perimeter-init ------------------------------------------------------------


def _perimeter_files(layout: Layout) -> tuple[Path, ...]:
    """The database, its marker and root's record of the creation."""
    base = layout.perimeter_dir
    return (base / perimeter.DB_NAME, base / perimeter.MARKER, layout.perimeter_created)


def _perimeter_ready(layout: Layout) -> bool:
    """All three files: done; no database while a marker (either) exists:
    lost, a refusal -- even if the whole `perimeter/` is gone; otherwise
    `do()` (nothing yet, or a creation interrupted before a marker)."""
    db, marker, created = (os.path.lexists(p) for p in _perimeter_files(layout))
    if not db and (marker or created):
        raise StepFailed("perimeter-init", perimeter.LOST_TEXT)
    return db and marker and created


def _perimeter_problems(layout: Layout) -> list[str]:
    files = _perimeter_files(layout)
    return [f"{p}: нет файла" for p in files if not os.path.lexists(p)]


def _perimeter_init(layout: Layout, python: Path) -> None:
    """`create_db` as `df-publisher` (`publisher perimeter-init`)."""
    result = run_as_publisher(layout, python, ["perimeter-init"], timeout=_INIT_TIMEOUT)
    if publisher_succeeded(result):
        try:
            record_created(layout)
        except OSError as exc:
            raise StepFailed("perimeter-init", f"{exc}{_RETRY}") from exc
        return
    raise StepFailed("perimeter-init", publisher_failure(result) + _RETRY)


def run_as_publisher(
    layout: Layout,
    python: Path,
    words: list[str],
    *,
    timeout: float,
    stdin: bytes | None = None,
) -> plat.RunResult:
    """The entry's `publisher <words>` as `df-publisher`, run by the admin
    Python `python` without network, `env -i`, cwd `/` (the entry reads no
    data relative to the code: the admin tree holds `df/` only)."""
    argv = [str(python), "-I", "-B", "-S", str(layout.entry), "publisher", *words]
    return plat.run_as(
        PUBLISHER_USER,
        argv,
        env=_ROLE_ENV,
        cwd=Path("/"),
        network=False,
        timeout=timeout,
        stdin=stdin,
    )


def publisher_succeeded(result: plat.RunResult) -> bool:
    """Launched, in time, exit 0."""
    return result.launched and not result.timed_out and result.returncode == 0


def publisher_failure(result: plat.RunResult) -> str:
    """Why a run of `run_as_publisher` failed: its state and stderr."""
    err = result.stderr.decode("utf-8", "replace").strip()
    state = "не стартовал" if not result.launched else f"код {result.returncode}"
    if result.timed_out:
        state = "срок истёк"
    return f"от {PUBLISHER_USER}: {state}" + (f": {err}" if err else "")


def record_created(layout: Layout) -> None:
    """Root's `perimeter.created` (`0644`, so that `df-publisher` reads it),
    if absent, in `/var/lib/df`, which must be root's and writable by root
    alone (else `OSError`). After `perimeter-init` and `perimeter restore`."""
    st = os.lstat(layout.var)
    mode = stat.S_IMODE(st.st_mode)
    ok = (
        stat.S_ISDIR(st.st_mode)
        and fsutil.owner_uid(st) == plat.ROOT_UID
        and not mode & (stat.S_IWGRP | stat.S_IWOTH)
    )
    if not ok:
        raise OSError(f"{layout.var}: не каталог root")
    if not os.path.lexists(layout.perimeter_created):
        text = f"{perimeter.now()}\n"
        fsutil.write_atomic(layout.perimeter_created, text, CREATED_MODE)


# --- verify --------------------------------------------------------------------


def _verify_problems(layout: Layout, operator: str, python: Path) -> list[str]:
    """Wrappers, then the effective sudo policy of the three roles, `df-build`
    and the operator, then the I/O log of `df-publish`."""
    problems = libexec.wrapper_problems(layout, python)
    names = (*roles.ROLE_USERS, plat.BUILD_USER, operator)
    for name in names:
        problems += sudoers.policy_problems(name, layout=layout, operator=operator)
    return problems + sudoers.io_log_problems(layout)
