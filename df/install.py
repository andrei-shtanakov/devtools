"""The install transaction (spec 3.3, Task 9).

Part 1 (9-A): the real route calls.

`InstallRun` carries everything one install run's routes need (I1): it is
built after the preparation steps, the trusted checkouts and the account.
`install_calls` is every route call (`acceptance_calls` plus the release's
own: `activate`, `write_release_json`, `register`, `write_atomic`);
`install_actions` makes each action of the table a `route_action` over them;
`read_for` reads the facts with the real admin predicates (`admin_ready`,
`command_ready`). `rollback_calls` is the route `rollback` without a profile,
a network or an account (I5).

Part 2 (9-B): the preparation steps 1-3 (`install_steps`), the trusted
checkouts and the uv settings, the writer's `pending.toml` (`read_pending`,
`new_pending`), the recovery of an interrupted install (`recover_pending`,
`rollback`) and the whole run under the writer lock (`run_install`), in the
fixed security order of I6, I7 and M4.
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from df import accept, admin, fsutil, inputs, journal, lockset, release
from df import build as build_mod
from df import install_table as table
from df import manifest as manifest_mod
from df import platform as plat
from df.accept import Facts
from df.build import BuildPlan
from df.config import (
    Account,
    ConfigError,
    InstallConfig,
    Pending,
    ReleaseEntry,
    config_digest,
    dump_install,
    dump_pending,
    load_accounts,
    load_pending,
    parse_install,
)
from df.digest import TreeError, release_digest
from df.inputs import InstalledDist, installed_distributions, lock_digest
from df.journal import Step, StepFailed, run_steps
from df.manifest import ManifestError, Profile, RepoPin
from df.paths import Layout
from df.platform import PlatformName
from df.receive import CopyLimits
from df.release import ReleaseRecord, RepoRecord
from df.routes import (
    Call,
    RouteContext,
    acceptance_calls,
    implementation,
    is_nested,
    removal_calls,
    route_action,
    run_route,
    where,
)
from df.state import Snapshot

#: Modes of the files `write_atomic` writes without a wrapper (M13): the
#: entry is read, the command executed, `install.toml` read by everyone.
ENTRY_MODE = 0o644
COMMAND_MODE = 0o755
INSTALL_MODE = 0o644

_REGISTER_STEP = "register"
_LAUNCHER_STEP = "launcher"


@dataclass(frozen=True)
class InstallRun:
    """One install run (I1): the configuration, its `pending.toml`, the
    profile, the plan and the trusted checkouts, the journal, the admin
    Python key and `df-build`'s uid — one carrier for every route call.

    `ctx` is the run's `RouteContext`, shared by every call. The install
    digest is one value, `config_digest(cfg)`: `acceptance_calls` writes the
    marks under it and `read_for` reads them under it (M8); a `pending` of
    another digest or whose `install_text` is not `cfg` rendered (the text
    `commit-config` writes) is `StepFailed("pending")`.
    """

    layout: Layout
    cfg: InstallConfig
    pending: Pending
    profile: Profile
    platform: PlatformName
    plan: BuildPlan
    limits: CopyLimits
    refs: Mapping[str, Path]
    journal: Path
    run_id: str
    python_key: str
    build_uid: int
    ctx: RouteContext = field(default_factory=RouteContext)

    def __post_init__(self) -> None:
        if self.install_digest != self.pending.install_digest:
            raise StepFailed("pending", "install_digest не равен конфигурации")
        if dump_install(self.cfg) != self.pending.install_text:
            raise StepFailed("pending", "install_text не равен конфигурации")
        rid = self.release_id
        if (self.plan.release_id, self.plan.release_dir) != (
            rid,
            self.layout.release_dir(rid),
        ):
            raise AssertionError(f"plan of another release: {self.plan.release_id}")
        if self.platform != plat.detect_platform():
            raise AssertionError(f"platform {self.platform} is not the host's (m2)")
        if self.python_key != build_mod.python_key(self.profile.python, self.platform):
            raise AssertionError(f"python_key {self.python_key!r} != the profile's")

    @property
    def release_id(self) -> str:
        """The release this run installs: `pending.release_id`."""
        return self.pending.release_id

    @property
    def install_digest(self) -> str:
        """The one install digest of the run (M8)."""
        return config_digest(self.cfg)


def install_calls(run: InstallRun) -> dict[str, Call]:
    """Every call of every route: `acceptance_calls` and the release's own.

    Keys are exactly `route_leaves()` and `WRAPS`. `write_release_json`
    builds the record (`release_record`), `register` writes the registry
    entry of the same record, `activate` is `release.activate`;
    `write_atomic` (targets without a wrapper) writes the entry text from
    the release's own admin code and the command for the registered admin
    Python (one source with `command_ready`, I3), and `install.toml` from
    `pending.toml`.
    """
    calls = acceptance_calls(
        run.layout,
        run.release_id,
        install=run.cfg,
        profile=run.profile,
        platform=run.platform,
        plan=run.plan,
        limits=run.limits,
        refs=run.refs,
        journal=run.journal,
        run_id=run.run_id,
        python_key=run.python_key,
        ctx=run.ctx,
    )
    return calls | _ReleaseCalls(run).calls()


def install_actions(run: InstallRun) -> Mapping[str, Callable[[Facts], None]]:
    """Every action of the table as `route_action` over `install_calls(run)`.

    Keys are the actions of `ROWS` (and `UNKNOWN_ROW`) without `""` and
    `"stop"`; each checks its own phase, then runs its route (F14) with the
    run's admin Python key and `df-build` uid. The calls of each route, in
    order:

    <!-- install-route-calls:begin -->
    | Действие | Вызовы по порядку (с условиями и объектами) |
    |---|---|
    | `build-dir` | `prepare_dir` releases/<id> |
    | `build` | `run_build` от `df-build` → `write_atomic` accept/<id>.toml: built |
    | `seal` | `reap_user_processes` df-build → `seal` releases/<id> |
    | `copy` | `accept_copy` releases/<id> → `write_atomic` accept/<id>.toml: copied |
    | `copy-partial` | `remove_own_tree` releases/<id> → `accept_copy` releases/<id> → `write_atomic` accept/<id>.toml: copied |
    | `verify` | если есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → `check_links` releases/<id> → `verify_chmod` releases/<id> → `verify_sources` releases/<id> → `distribution_problems` releases/<id> → `static_problems` releases/<id> → `cleanup_build_home` build_home/tmp → `cleanup_build_home` build_home/cache → `transfer_check` releases/<id> → `tree_digest` releases/<id> = отметке → `write_atomic` accept/<id>.toml: max(стадия, verified) |
    | `admin` | если есть `admin-python/.staging`: `remove_own_tree` admin-python/.staging → если нет `admin-python/cpython-…`: `install_admin_python` admin-python/cpython-… → если есть `admin-python/.staging`: `remove_own_tree` admin-python/.staging → если есть `admin/<id>`: `remove_own_tree` admin/<id> → `install_admin` admin/<id> → `write_atomic` accept/<id>.toml: admin |
    | `register` | `write_atomic` releases/<id>/release.json → `write_atomic` /etc/df/releases.toml |
    | `activate` | `activate` /opt/df/current |
    | `launcher` | `write_atomic` bin/darkfactory-entry.py → `write_atomic` bin/darkfactory → если `/usr/local/bin` проходит политику доверенных путей: `link_command` /usr/local/bin/darkfactory |
    | `commit-config` | `write_atomic` /etc/df/install.toml → `remove_durably` pending.toml |
    | `cleanup` | если корень `releases/<id>` у root: `remove_own_tree` releases/<id> → если корень у `df-build`: `cleanup_tree` releases/<id> → если есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → если есть `admin/<id>`: `remove_own_tree` admin/<id> → если есть отметка: `remove_durably` accept/<id>.toml |
    | `rollback` | если id не в `releases.toml` и корень `releases/<id>` у root: `remove_own_tree` releases/<id> → если id не в `releases.toml` и корень у `df-build`: `cleanup_tree` releases/<id> → если id не в `releases.toml` и есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если id не в `releases.toml` и есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → если id не в `releases.toml` и есть `admin/<id>`: `remove_own_tree` admin/<id> → если id не в `releases.toml` и есть отметка: `remove_durably` accept/<id>.toml → `remove_durably` pending.toml |
    <!-- install-route-calls:end -->
    """
    calls = install_calls(run)
    return {
        action: route_action(
            run.layout,
            run.release_id,
            action,
            calls=calls,
            python_key=run.python_key,
            build_uid=run.build_uid,
        )
        for action in ACTIONS
    }


#: Every action of the table: `Row.action` of `ROWS` and `UNKNOWN_ROW`.
ACTIONS: tuple[str, ...] = tuple(
    sorted({r.action for r in (*table.ROWS, table.UNKNOWN_ROW)} - {"", "stop"})
)


def read_for(run: InstallRun) -> Callable[[], Facts]:
    """The facts of the run's release for `drive` (R33.F5): `read_facts`
    under the run's install digest, with `admin.admin_ready` and `L` =
    `command_ready` of the release."""
    layout, rid = run.layout, run.release_id

    def read() -> Facts:
        return accept.read_facts(
            layout,
            rid,
            install_digest=run.install_digest,
            build_uid=run.build_uid,
            launcher_ready=lambda: command_ready(layout, rid),
            admin_ready=admin.admin_ready,
        )

    return read


def command_ready(layout: Layout, release_id: str) -> bool:
    """`L` of `release_id` (I3): the command is what its own admin code makes.

    No registry entry, or one not `intact` — false, and nothing of the
    release's admin tree is read. Otherwise `admin.launcher_ready` for the
    entry's admin Python: the same Python and the same entry source the
    route `launcher` writes. The entry's status is not read: like `intact`,
    `L` is a fact of the files; revocation is the table's (n4).
    """
    return command_ready_of(
        layout, release_id, accept.registry_entry(layout, release_id)
    )


def command_ready_of(
    layout: Layout, release_id: str, entry: ReleaseEntry | None
) -> bool:
    """`command_ready` for a given registry entry: a snapshot's value, so
    bootstrap's healthy-command check (M16) re-reads no registry (I2)."""
    if entry is None or not accept.entry_intact(
        layout, release_id, entry, admin_ready=admin.admin_ready
    ):
        return False
    return admin.launcher_ready(layout, release_id, python=entry.admin_python)


def rollback_calls(
    layout: Layout, release_id: str, journal: Path, run_id: str
) -> dict[str, Call]:
    """The calls of the route `rollback`, exactly (I5): the removing calls of
    `acceptance_calls`, the same functions (`routes.removal_calls`), without
    a profile or a network. No account is needed unless a builder tree is
    there: `cleanup_tree` runs the cleaner as `df-build`, so it first
    requires `verify_build_user` empty (security order: no `run_as` before
    it), else `StepFailed("rollback")` before the cleaner runs (I-1); route
    steps before it (e.g. a root-owned `releases/<id>`) may already be
    removed, which a retry repeats idempotently. `journal`
    and `run_id` name the run that rolls back (the step naming of its
    failures is the caller's).
    """
    del journal, run_id  # the caller journals (M7)
    calls = removal_calls(layout, release_id)
    cleanup = calls["cleanup_tree"]

    def cleanup_tree(target: str) -> None:
        problems = build_mod.verify_build_user(layout)
        if problems:
            parent, name = where(layout, release_id, target)
            raise StepFailed(
                "rollback",
                f"{parent / name}: учётка df-build не готова — очистка дерева "
                f"сборщика невозможна: {'; '.join(problems)}",
            )
        cleanup(target)

    return calls | {"cleanup_tree": cleanup_tree}


# ---------- the release's own calls ----------


@dataclass
class _ReleaseCalls:
    """The calls of Task 9 over one run: the record, the registry, the link
    `current` and the files written without a wrapper."""

    run: InstallRun
    record: ReleaseRecord | None = None

    def calls(self) -> dict[str, Call]:
        return {
            "write_release_json": self.write_release_json,
            "register": self.register,
            "activate": self.activate,
            "write_atomic": self.write_atomic,
        }

    def write_release_json(self, _target: str) -> None:
        """Wrapper `write_release_json`: the record of the accepted copy."""
        self.record = release_record(self.run)
        lay = self.run.layout
        release.write_release_json(lay.release_dir(self.run.release_id), self.record)

    def register(self, _target: str) -> None:
        """Wrapper `register`: the registry entry of the same record."""
        rec = self.record if self.record is not None else release_record(self.run)
        release.register(self.run.layout, rec)

    def activate(self, _target: str) -> None:
        release.activate(self.run.layout, self.run.release_id)

    def write_atomic(self, target: str) -> None:
        """Protocol `write_atomic` for the targets without a wrapper."""
        lay, rid = self.run.layout, self.run.release_id
        if target == "bin/darkfactory-entry.py":
            fsutil.write_atomic(
                lay.entry, admin.release_entry_text(lay, rid), ENTRY_MODE
            )
        elif target == "bin/darkfactory":
            text = admin.launcher_text(
                lay, _registered_python(lay, rid), self.run.platform
            )
            fsutil.write_atomic(lay.launcher, text, COMMAND_MODE)
        elif target == "/etc/df/install.toml":
            path = lay.etc / "install.toml"
            fsutil.write_atomic(path, self.run.pending.install_text, INSTALL_MODE)
        else:
            raise AssertionError(f"write_atomic without a wrapper: {target!r}")


def _registered_python(layout: Layout, release_id: str) -> str:
    """The admin Python of the registry entry: the command names it (I3).

    The status is not read here: the rows that run `launcher` already hold
    `reg ∧ intact`, and revocation is the table's, as for `intact` (n4).
    """
    entry = accept.registry_entry(layout, release_id)
    if entry is None:
        raise StepFailed(_LAUNCHER_STEP, f"{release_id}: нет в releases.toml")
    return entry.admin_python


def release_record(run: InstallRun) -> ReleaseRecord:
    """The record of the run's accepted copy (spec 3.5, I4, M12).

    From the valid `admin` mark: the digest, the admin digest and Python,
    `python_declared` and `uv_version_declared`. The copy's digest must
    equal the mark's (`StepFailed("register")`); only then are the
    environments read: `distributions_declared` is each repo's
    `installed_distributions` of `venv/<repo>` (the lists
    `distribution_problems` accepted on this tree, I4b). `lock_sha256` is
    the trusted checkout's `uv.lock` (`inputs.lock_digest`).
    """
    lay, rid = run.layout, run.release_id
    mark = accept.load_mark(lay, rid, install_digest=run.install_digest)
    if mark is None or mark.stage != "admin":
        raise StepFailed(_REGISTER_STEP, f"{rid}: нет отметки admin")
    tree = lay.release_dir(rid)
    try:
        have = release_digest(tree)
    except (TreeError, OSError) as exc:
        raise StepFailed(_REGISTER_STEP, f"{tree}: {exc}") from exc
    if have != mark.tree_digest:
        raise StepFailed(
            _REGISTER_STEP, f"{tree}: digest {have}, отметка {mark.tree_digest}"
        )
    if mark.admin_digest is None or mark.admin_python is None:
        raise AssertionError("an admin mark carries its admin fields")
    if mark.python_declared is None:
        raise AssertionError("an admin mark carries python_declared")
    return ReleaseRecord(
        id=rid,
        digest=have,
        admin_digest=mark.admin_digest,
        admin_python=mark.admin_python,
        manifest_repo=run.cfg.manifest_repo,
        manifest_sha=run.cfg.manifest_sha,
        profile=run.profile.name,
        platform=run.platform,
        created_at=_utc_now().isoformat(timespec="seconds"),
        repos=tuple(_repo_record(run, pin) for pin in run.profile.repos),
        python_declared=accept.parse_declared(mark.python_declared),
        uv_version_declared=mark.uv_version_declared,
        distributions_declared=_distributions(run, tree),
    )


def _repo_record(run: InstallRun, pin: RepoPin) -> RepoRecord:
    ref = run.refs.get(pin.name)
    if ref is None:
        raise StepFailed(_REGISTER_STEP, f"{pin.name}: нет доверенного чекаута")
    try:
        lock = lock_digest(ref / "uv.lock", pin.name)
    except StepFailed as exc:
        raise StepFailed(_REGISTER_STEP, exc.detail) from exc
    return RepoRecord(pin.name, pin.url, pin.sha, lock, pin.groups)


def _distributions(run: InstallRun, tree: Path) -> dict[str, tuple[InstalledDist, ...]]:
    out: dict[str, tuple[InstalledDist, ...]] = {}
    for pin in run.profile.repos:
        try:
            out[pin.name] = tuple(installed_distributions(tree / "venv" / pin.name))
        except (TreeError, OSError) as exc:
            raise StepFailed(_REGISTER_STEP, f"{pin.name}: {exc}") from exc
    return out


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------- part 2: preparation, recovery and the run (9-B) ----------

#: Mode of `pending.toml`: it holds the text of `install.toml` (`0644`).
PENDING_MODE = 0o644
#: Directories whose mode step 2 requires exactly (M3): `staging` lists no
#: id to `df-build`, the marks and the evidence are root's alone.
EXACT_MODES = ("staging", "quarantine", "marks_dir", "verify_dir")
_PREP_DIR_MODES: tuple[tuple[str, int], ...] = (
    ("opt", 0o755),
    ("releases", 0o755),
    ("staging", 0o711),
    ("quarantine", 0o711),
    ("admin", 0o755),
    ("admin_python", 0o755),
    ("bin_dir", 0o755),
    ("etc", 0o755),
    ("var", 0o755),
    ("marks_dir", 0o700),
    ("verify_dir", 0o700),
)
#: Parents of what a rollback removes (I7): trusted before any removal.
ROLLBACK_PARENTS = ("opt", "releases", "staging", "admin")
_DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_DIR_NOFOLLOW = _DIR_OPEN | os.O_NOFOLLOW
_DATA_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_TRUST_STEP = "trusted-paths"
_PENDING_STEP = "pending"
_ROLLBACK_STEP = "rollback"

Decision = Literal["none", "resume", "rollback"]


def prep_dirs(layout: Layout) -> tuple[tuple[Path, int], ...]:
    """The directories step 2 creates and requires, in creation order, with
    their modes (M3); `install_dir` is `writer_lock`'s."""
    return tuple((getattr(layout, name), mode) for name, mode in _PREP_DIR_MODES)


@dataclass
class Prepared:
    """What the preparation steps found: step 1 puts the profile here (I1)."""

    profile: Profile | None = None


def install_steps(layout: Layout, cfg: InstallConfig, prepared: Prepared) -> list[Step]:
    """The preparation steps 1-3 for `run_steps` (spec 3.3; I1, I6, M3, M4).

    1. `manifest`: the check fetches the manifest (HTTPS read only) and
       parses the profile into `prepared`; a refusal is
       `StepFailed("manifest", <problems>)` from the check itself.
    2. `trusted-paths`: the approved CLI first, then every directory of
       `prep_dirs`; any problem is `StepFailed("trusted-paths", <paths and
       reasons>)` — `do()` never repairs an existing directory, it only
       creates missing ones (`fchmod` to the exact mode, the parent synced).
    3. `build-user`: `verify_build_user` empty, and only then `whoami_as`
       equals the record (no process of `df-build` before readiness);
       `do()` is `create_build_user`.
    """
    platform = plat.detect_platform()

    def manifest_ready() -> bool:
        prepared.profile = _profile(cfg)
        return True

    return [
        Step("manifest", manifest_ready, lambda: None),
        Step(
            _TRUST_STEP,
            lambda: _trusted_ready(layout, cfg, platform),
            lambda: _make_prep_dirs(layout),
        ),
        Step(
            "build-user",
            lambda: _build_user_ready(layout),
            lambda: _create_build_user(layout),
        ),
    ]


def _profile(cfg: InstallConfig) -> Profile:
    where_ = f"{cfg.manifest_repo}@{cfg.manifest_sha}"
    try:
        data = manifest_mod.fetch_manifest(cfg.manifest_repo, cfg.manifest_sha)
        return manifest_mod.parse_profile(data, cfg.profile)
    except ManifestError as exc:
        raise StepFailed("manifest", f"{where_}: {'; '.join(exc.problems)}") from exc
    except OSError as exc:
        raise StepFailed("manifest", f"{where_}: {exc}") from exc


def _trusted_ready(layout: Layout, cfg: InstallConfig, platform: PlatformName) -> bool:
    """Step 2's check: every problem refuses; only absence asks for `do()`."""
    problems = list(plat.approved_cli_problems(cfg.approved_cli, platform))
    missing = False
    exact = {getattr(layout, name) for name in EXACT_MODES}
    for path, mode in prep_dirs(layout):
        problems += policy_problems(path)
        st = _lstat(path)
        if st is None:
            missing = True
        elif stat.S_ISDIR(st.st_mode) and path in exact:
            have = stat.S_IMODE(st.st_mode)
            if have != mode:
                problems.append(f"{path}: права {have:04o}, нужны {mode:04o}")
    if problems:
        raise StepFailed(_TRUST_STEP, "; ".join(dict.fromkeys(problems)))
    return not missing


def policy_problems(path: Path) -> list[str]:
    """The trusted-path policy of a directory root acts under (I-1, I-3).

    Absent: the policy of its nearest existing ancestor — never skipped, so
    only root could create anything between that ancestor and `path`. A
    non-directory (a link included, by `lstat`): a problem. Otherwise
    `trusted_path_problems(path)`. One helper for step 2 and the rollback.
    """
    st = _lstat(path)
    if st is None:
        return plat.trusted_path_problems(_existing_ancestor(path))
    if not stat.S_ISDIR(st.st_mode):
        return [f"{path}: не каталог"]
    return plat.trusted_path_problems(path)


def _make_prep_dirs(layout: Layout) -> None:
    """Step 2's `do()`: only after `_trusted_ready` passed for the same
    layout (m4). `_make_dir` opens each parent following links (macOS `/etc`
    is one); that is safe only because the check proved every component
    down to the nearest existing ancestor root's and unwritable by others,
    and `fchmod` acts only on the directory this call created and opened
    `O_NOFOLLOW`."""
    for path, mode in prep_dirs(layout):
        for ancestor in _missing_ancestors(layout, path):
            _make_dir(ancestor, 0o755)
        _make_dir(path, mode)


def _missing_ancestors(layout: Layout, path: Path) -> list[Path]:
    out: list[Path] = []
    parent = path.parent
    while parent != layout.root and _lstat(parent) is None:
        out.insert(0, parent)
        parent = parent.parent
    return out


def _make_dir(path: Path, mode: int) -> None:
    """Create `path` (if absent) with exactly `mode`; sync its parent."""
    try:
        parent = os.open(path.parent, _DIR_OPEN)
    except OSError as exc:
        raise StepFailed(_TRUST_STEP, f"{path.parent}: {exc}") from exc
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            return  # the check judges what is there
        fd = os.open(path.name, _DIR_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
        fsutil.sync_dir(parent)
    except OSError as exc:
        raise StepFailed(_TRUST_STEP, f"{path}: {exc}") from exc
    finally:
        os.close(parent)


def _existing_ancestor(path: Path) -> Path:
    parent = path.parent
    while _lstat(parent) is None and parent != parent.parent:
        parent = parent.parent
    return parent


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.stat(path, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _build_user_ready(layout: Layout) -> bool:
    """M4: `whoami_as` only after `verify_build_user` is empty."""
    if build_mod.verify_build_user(layout):
        return False
    account = build_account(layout)
    if account is None:
        return False
    return build_mod.whoami_as(plat.BUILD_USER) == (account.uid, account.gid)


def _create_build_user(layout: Layout) -> None:
    plat.create_build_user(layout)


def build_account(layout: Layout) -> Account | None:
    """The `df-build` record of `accounts.toml` in any state, else `None`
    (I5); an unreadable `accounts.toml` is `StepFailed("build-user")`."""
    try:
        return load_accounts(layout.accounts).get(plat.BUILD_USER)
    except ConfigError as exc:
        raise StepFailed("build-user", str(exc)) from exc


# ---------- the trusted checkouts ----------


def fetch_references(layout: Layout, profile: Profile, *, git: Path) -> dict[str, Path]:
    """Root's trusted checkout of every repo of the profile (Task 7,
    R33.F34): `verify/<repo>` by `inputs.fetch_reference` with the approved
    `git` — after step 2 only (I6)."""
    return {
        pin.name: inputs.fetch_reference(
            pin, layout.verify_dir / pin.name, git=git, layout=layout
        )
        for pin in profile.repos
    }


def uv_config_refusal(profile: Profile, refs: Mapping[str, Path]) -> None:
    """uv settings `UV_NO_CONFIG=1` would drop (spec 3.2, M5): the problems
    of **all** repos, each line `<repo>: …`, as one `StepFailed("build")`;
    the checkout's files are read as data, an unreadable one is a problem."""
    problems: list[str] = []
    for pin in profile.repos:
        ref = refs[pin.name]
        try:
            pyproject = _checkout_file(ref / "pyproject.toml", required=True)
            uv_toml = _checkout_file(ref / "uv.toml", required=False)
            lock = _checkout_file(ref / "uv.lock", required=True)
        except OSError as exc:
            problems.append(f"{pin.name}: {exc}")
            continue
        assert pyproject is not None and lock is not None
        problems += [
            f"{pin.name}: {p}"
            for p in lockset.uv_config_problems(pyproject, uv_toml, lock)
        ]
    if problems:
        raise StepFailed("build", "; ".join(problems))


def _checkout_file(path: Path, *, required: bool) -> bytes | None:
    """A regular file of a checkout, as data (no link followed)."""
    try:
        fd = os.open(path, _DATA_FLAGS)
    except FileNotFoundError:
        if required:
            raise OSError(f"{path.name}: нет файла") from None
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name}: не обычный файл")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read()
    finally:
        os.close(fd)


# ---------- pending.toml ----------


def new_pending(cfg: InstallConfig, release_id: str, previous: str | None) -> Pending:
    """A fresh `pending.toml` for `release_id` (M8): `install_text` is `cfg`
    rendered and `install_digest` its one digest."""
    return Pending(
        run_id=uuid.uuid4().hex,
        release_id=release_id,
        install_text=dump_install(cfg),
        install_digest=config_digest(cfg),
        previous_current=previous,
    )


def read_pending(layout: Layout) -> Pending | None:
    """The writer's own `pending.toml`, read once under its lock (I2).

    Absent -> `None`. Invalid, an invalid id, or a text/digest pair that is
    not one configuration rendered (M8) -> `StepFailed("pending")`.
    """
    try:
        pending = load_pending(layout.pending)
        if pending is None:
            return None
        layout.release_dir(pending.release_id)
        cfg = parse_install(pending.install_text)
    except ConfigError as exc:
        raise StepFailed(_PENDING_STEP, f"{layout.pending}: {exc}") from exc
    if (
        config_digest(cfg) != pending.install_digest
        or dump_install(cfg) != pending.install_text
    ):
        raise StepFailed(
            _PENDING_STEP, f"{layout.pending}: install_text и install_digest разошлись"
        )
    return pending


def recover_pending(
    layout: Layout, snap: Snapshot, requested: InstallConfig, pending: Pending | None
) -> Decision:
    """§3.3 «Незавершённая установка», first thing under the writer lock.

    `snap` was read after `settle_on_acquire`; `pending` is `read_pending`'s.
    - no `pending.toml` -> `none`;
    - `current` is the pending id -> `resume`, whatever was requested (a
      differing request is journaled as `warn`, I8);
    - `current` is `previous_current` and the request is the pending
      configuration -> `resume`;
    - `current` is `previous_current`, another configuration -> the route
      `rollback` runs here, then `generation_end`, and only then `rollback`
      is returned (M11);
    - any other `current` -> `StepFailed("pending")`: a human decides (M10).
    """
    if pending is None:
        return "none"
    current = snap.release_id
    same = config_digest(requested) == pending.install_digest
    if current == pending.release_id:
        if not same:
            _journal(
                layout,
                pending.run_id,
                "pending",
                "warn",
                f"{pending.release_id} уже активен: запрошенная конфигурация "
                "не применяется, установка продолжается",
            )
        return "resume"
    if current != pending.previous_current:
        raise StepFailed(
            _PENDING_STEP,
            f"current = {current}: ни {pending.release_id}, ни прежний "
            f"{pending.previous_current} — решает человек (darkfactory doctor)",
        )
    if same:
        return "resume"
    rollback(layout, pending)
    return "rollback"


def rollback(layout: Layout, pending: Pending) -> None:
    """The route `rollback` of `pending`'s release, then `generation_end`.

    Security order (I7): `policy_problems` of `/opt/df`, `releases`,
    `staging` and `admin` (an absent one judged by its nearest existing
    ancestor, I-1) must be empty before any removal, else
    `StepFailed("trusted-paths")`. The calls are `rollback_calls` (no
    profile, network or account; the cleaner gated on
    `verify_build_user`); `build_uid` is the `df-build` record's in any
    state, or `None` (I5). A `TreeError` or `OSError` of a call becomes
    `StepFailed("rollback", <route>[<step>] …)` (M7).
    """
    problems = [
        p for name in ROLLBACK_PARENTS for p in policy_problems(getattr(layout, name))
    ]
    if problems:
        raise StepFailed(_TRUST_STEP, "; ".join(dict.fromkeys(problems)))
    rid, run_id = pending.release_id, pending.run_id
    account = build_account(layout)
    calls = rollback_calls(layout, rid, layout.journal, run_id)
    journal.generation_begin(layout)
    _journal(layout, run_id, _ROLLBACK_STEP, "start", rid)
    try:
        run_route(
            layout,
            rid,
            _ROLLBACK_STEP,
            calls=_named_calls(calls, layout, rid),
            python_key=None,
            build_uid=None if account is None else account.uid,
        )
    except Exception as exc:  # noqa: BLE001 - journaled, then re-raised
        _journal_failure(layout, run_id, exc)
        raise
    _journal(layout, run_id, _ROLLBACK_STEP, "done", rid)
    journal.generation_end(layout)


def _named_calls(
    calls: Mapping[str, Call], layout: Layout, release_id: str
) -> dict[str, Call]:
    """Each call names its route step on a bare `TreeError`/`OSError` (M7)."""
    steps = _route_steps(_ROLLBACK_STEP)

    def named(key: str, call: Call) -> Call:
        def run(target: str) -> None:
            try:
                call(target)
            except (TreeError, OSError) as exc:
                step = steps.get((key, target), "?")
                parent, name = where(layout, release_id, target)
                raise StepFailed(
                    _ROLLBACK_STEP, f"{step} {key} {parent / name}: {exc}"
                ) from exc

        return run

    return {key: named(key, call) for key, call in calls.items()}


def _route_steps(name: str) -> dict[tuple[str, str], str]:
    """`(call key, target) -> "<route>[<index>]"` over a route and its
    nested routes (every key/target pair of `rollback` is unique)."""
    out: dict[tuple[str, str], str] = {}
    for i, (_guard, call, target) in enumerate(table.ROUTES[name]):
        if is_nested(call):
            out |= _route_steps(call)
        else:
            out[(implementation(call, target), target)] = f"{name}[{i}]"
    return out


# ---------- the run ----------


def run_install(
    layout: Layout,
    requested: InstallConfig,
    snap: Snapshot,
    pending: Pending | None,
    *,
    fail_after: str | None = None,
) -> str:
    """One install under the writer lock (spec 3.3); returns the release id.

    The order is fixed (I6, M5, M6): `recover_pending`; `generation_begin`;
    a new `pending.toml` unless resuming; steps 1-3 (`run_steps`; the
    manifest, the approved CLI and paths, the account); the trusted
    checkouts; `uv_config_refusal`; `retry_quarantine` (a leftover is a
    `warn`); `drive`; `generation_end`. No `git`/`uv`/`tar` runs before
    step 2 succeeds. A resumed release that is `damaged` (current = its id)
    is refused naming the id, the state, the manual way out and `doctor`
    (R47, option (a); v1 limit).
    """
    decision = recover_pending(layout, snap, requested, pending)
    journal.generation_begin(layout)
    if decision == "resume":
        assert pending is not None
        pend = pending
    else:
        platform = plat.detect_platform()
        rid = release.next_release_id(layout, requested.manifest_sha, platform)
        pend = new_pending(requested, rid, snap.release_id)
        fsutil.write_atomic(layout.pending, dump_pending(pend), PENDING_MODE)
    run = _prepare(layout, pend, fail_after)
    try:
        accept.drive(
            layout,
            run.release_id,
            actions=install_actions(run),
            read=read_for(run),
            journal=run.journal,
            run_id=run.run_id,
        )
    except StepFailed as exc:
        if exc.step == "damaged" and snap.release_id == pend.release_id:
            raise StepFailed("damaged", _damaged(layout, pend)) from exc
        raise
    journal.generation_end(layout)
    return pend.release_id


def _prepare(layout: Layout, pend: Pending, fail_after: str | None) -> InstallRun:
    """Steps 1-3, the checkouts, the uv settings and the quarantine: the run."""
    cfg = parse_install(pend.install_text)
    prepared = Prepared()
    run_steps(
        install_steps(layout, cfg, prepared),
        layout.journal,
        run_id=pend.run_id,
        fail_after=fail_after,
    )
    profile = prepared.profile
    account = build_account(layout)
    if profile is None or account is None:
        raise AssertionError("steps 1 and 3 hold the profile and the account")
    refs = fetch_references(layout, profile, git=cfg.approved_cli["git"])
    uv_config_refusal(profile, refs)
    left = plat.retry_quarantine(layout)
    if left:
        _journal(layout, pend.run_id, "quarantine", "warn", ", ".join(left))
    platform = plat.detect_platform()
    rid = pend.release_id
    plan = BuildPlan(
        rid,
        layout.release_dir(rid),
        profile,
        cfg.approved_cli["uv"],
        cfg.approved_cli["git"],
        cfg.approved_cli["tar"],
    )
    return InstallRun(
        layout=layout,
        cfg=cfg,
        pending=pend,
        profile=profile,
        platform=platform,
        plan=plan,
        limits=CopyLimits(),
        refs=refs,
        journal=layout.journal,
        run_id=pend.run_id,
        python_key=build_mod.python_key(profile.python, platform),
        build_uid=account.uid,
    )


def _damaged(layout: Layout, pend: Pending) -> str:
    back = (
        ""
        if pend.previous_current is None
        else f"верните current на прежний релиз ({pend.previous_current}) или "
    )
    return (
        f"релиз {pend.release_id}: состояние damaged (повреждён) при "
        f"возобновлении, current = {pend.release_id}; автоматического выхода в "
        f"v1 нет — восстановление вручную: {back}удалите {layout.pending} после "
        "разбора, затем darkfactory doctor"
    )


def _journal(layout: Layout, run_id: str, step: str, event: str, detail: str) -> None:
    journal.journal_event(
        layout.journal, run_id=run_id, step=step, event=event, detail=detail
    )


def _journal_failure(layout: Layout, run_id: str, exc: Exception) -> None:
    step = exc.step if isinstance(exc, StepFailed) else _ROLLBACK_STEP
    try:
        _journal(layout, run_id, step, "failed", f"{type(exc).__name__}: {exc}")
    except Exception as log_exc:  # noqa: BLE001 - the original failure wins
        exc.add_note(f"журнал: не записано failed: {log_exc}")
