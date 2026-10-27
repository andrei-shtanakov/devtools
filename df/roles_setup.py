"""`darkfactory roles setup` (spec 4.1-4.2; stage 2a, Task 4).

A writer: the caller holds the writer lock for the whole run. The steps, in
this order, each settled by its own check so that a repeat changes nothing:

1. `roles`: `ensure_roles` -- the accounts are created one after another,
   only here and only under the lock (`platform._free_id` ignores `creating`
   intents, so two parallel creations could take the same id);
2. `dirs`: the stage-2 directories, born in their final owner and mode;
3. `publisher-config`: `/etc/df/publisher.toml` from the accounts;
4. `libexec`: the wrappers, for the admin Python of `current`;
5. `sudoers`: `/etc/sudoers.d/df`;
6. `verify`: wrappers and the effective sudo policy of five names.

TODO(Task 11): `perimeter-init` (the perimeter database, created as
`df-publisher`) goes between `publisher-config` and `libexec`; `dirs` already
makes its directory.
"""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from df import fsutil, libexec, roles, sudoers
from df import platform as plat
from df.config import Account, ConfigError, load_accounts
from df.journal import Step, StepFailed, run_steps
from df.paths import Layout
from df.stage2_config import dump_publisher, expected_publisher
from df.state import Snapshot

STEP_NAMES = ("roles", "dirs", "publisher-config", "libexec", "sudoers", "verify")
PUBLISHER_MODE = 0o644
SUDOERS_MODE = 0o440
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
#: Directory states `do()` settles: absent, a mode `make_dir` repairs, ours but
#: still root's (a crash between `mkdir` and `fchown`).
_NEEDS_DO = frozenset({"absent", "repair", "unowned"})
_NO_OPENSSL = (
    "openssl: нет в install.toml (approved_cli.openssl): добавьте путь, "
    "например openssl = /usr/bin/openssl"
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

    def verify_problems() -> list[str]:
        return _verify_problems(layout, operator, python)

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
        Step(
            "verify",
            lambda: not verify_problems(),
            lambda: None,
            verify_problems,
        ),
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
        if owner == plat.ROOT_UID and fsutil.repairable(st, spec.mode):
            return "repair"  # a crash left an inherited S_ISGID
        return f"{spec.path}: права {have:04o}, нужны {spec.mode:04o}"
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
    path = layout.etc / "publisher.toml"
    try:
        fsutil.write_atomic(path, _publisher_text(layout), PUBLISHER_MODE)
    except OSError as exc:
        raise StepFailed("publisher-config", f"{path}: {exc}") from exc


# --- verify --------------------------------------------------------------------


def _verify_problems(layout: Layout, operator: str, python: Path) -> list[str]:
    """Wrappers, then the effective sudo policy of the three roles, `df-build`
    and the operator, then the I/O log of `df-publish`."""
    problems = libexec.wrapper_problems(layout, python)
    names = (*roles.ROLE_USERS, plat.BUILD_USER, operator)
    for name in names:
        problems += sudoers.policy_problems(name, layout=layout, operator=operator)
    return problems + sudoers.io_log_problems(layout)
