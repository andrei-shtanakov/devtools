"""The install transaction (spec 3.3, Task 9), part 1: the real route calls.

`InstallRun` carries everything one install run's routes need (I1): it is
built after the preparation steps, the trusted checkouts and the account.
`install_calls` is every route call (`acceptance_calls` plus the release's
own: `activate`, `write_release_json`, `register`, `write_atomic`);
`install_actions` makes each action of the table a `route_action` over them;
`read_for` reads the facts with the real admin predicates (`admin_ready`,
`command_ready`). `rollback_calls` is the route `rollback` without a profile,
a network or an account (I5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from df import accept, admin, fsutil, release
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.accept import Facts
from df.build import BuildPlan
from df.config import InstallConfig, Pending, config_digest, dump_install
from df.digest import TreeError, release_digest
from df.inputs import InstalledDist, installed_distributions, lock_digest
from df.journal import StepFailed
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.platform import PlatformName
from df.receive import CopyLimits
from df.release import ReleaseRecord, RepoRecord
from df.routes import (
    Call,
    RouteContext,
    acceptance_calls,
    removal_calls,
    route_action,
    where,
)

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
    entry = accept.registry_entry(layout, release_id)
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
