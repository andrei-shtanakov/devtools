"""Acceptance routes of spec 3.3: guards, the interpreter and the call table.

`ROUTES` (`df.install_table`) is the composition of every action and of the
rollback: ordered steps `(guard, call, target)`. `run_route` is its only
interpreter: a step whose guard is false is skipped, a nested route runs
recursively, any other call goes to its implementation in `calls` with the
step's target text. `route_action` makes a route an action of `drive` that
first checks it runs in its own phase.

A guard reads one name by `lstat` through the descriptor of its root-owned
parent; the object itself is never opened (spec 3.8). Target texts resolve
through one exact-match table, `TARGETS`; a protocol call whose target names a
wrapper (`WRAPS`) goes to that wrapper.

`acceptance_calls` builds the implementations Tasks 7 and 7a own (protocol
leaves live in `df.leaves`); the other leaves (`LATER_LEAVES`) come from their
owner tasks and Task 9 merges them.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from df import accept, fsutil, receive
from df import build as build_mod
from df import install_table as table
from df import platform as plat
from df.accept import STAGE_ORDER, AcceptMark, Facts, Stage
from df.build import BuildPlan
from df.config import InstallConfig, config_digest
from df.digest import TreeError, check_links, release_digest, tree_digest
from df.inputs import InstalledDist, installed_distributions, verify_sources
from df.journal import StepFailed, journal_event
from df.leaves import accept_copy, prepare_dir, seal, verify_chmod
from df.lockset import DECLARED_LIMIT, distribution_problems, expected_distributions
from df.manifest import Profile
from df.markers import marker_env
from df.paths import Layout
from df.platform import PlatformName
from df.receive import CopyLimits
from df.sources import UnsupportedLock

#: An implementation of a route call: takes the step's target text.
Call = Callable[[str], None]
#: (layout, release id, admin Python key) -> (parent directory, name).
Where = Callable[[Layout, str, str | None], tuple[Path, str]]

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_GUARD_STEP = "route"


# ---------- targets ----------


@dataclass(frozen=True)
class Target:
    """A route target text, resolved (F7).

    `where` names the object (`None`: a user or a description, no object);
    `wrap` is the `WRAPS` function that implements the protocol call for this
    target; `stage` is the mark stage a `write_mark` target writes, `at_least`
    keeps a higher recorded stage (`max(стадия, verified)`).
    """

    where: Where | None
    wrap: str | None = None
    stage: Stage | None = None
    at_least: bool = False


def _fixed(get: Callable[[Layout], Path], name: str) -> Where:
    return lambda layout, _id, _key: (get(layout), name)


def _release(layout: Layout, release_id: str, _key: str | None) -> tuple[Path, str]:
    return layout.releases, layout.release_dir(release_id).name


def _mark(layout: Layout, release_id: str, _key: str | None) -> tuple[Path, str]:
    return layout.marks_dir, layout.mark_file(release_id).name


def _admin(layout: Layout, release_id: str, _key: str | None) -> tuple[Path, str]:
    return layout.admin, layout.admin_dir(release_id).name


def _python(layout: Layout, _id: str, key: str | None) -> tuple[Path, str]:
    return layout.admin_python, _need_key(key)


def _staging_dir(layout: Layout, release_id: str, _key: str | None) -> tuple[Path, str]:
    return layout.staging, layout.staging_dir(release_id).name


def _staging_tree(
    layout: Layout, release_id: str, _key: str | None
) -> tuple[Path, str]:
    return layout.staging_dir(release_id), layout.staging_tree(release_id).name


def _release_json(
    layout: Layout, release_id: str, _key: str | None
) -> tuple[Path, str]:
    return layout.release_dir(release_id), "release.json"


def _need_key(key: str | None) -> str:
    if key is None:
        raise AssertionError("admin Python key is required (python_key)")
    return key


def _mark_stage(stage: Stage, *, at_least: bool = False) -> Target:
    return Target(_mark, wrap="write_mark", stage=stage, at_least=at_least)


#: Every target text of a leaf step of `ROUTES`, exactly (F7).
TARGETS: Mapping[str, Target] = {
    "releases/<id>": Target(_release),
    "от `df-build`": Target(None),
    "df-build": Target(None),
    "accept/<id>.toml: built": _mark_stage("built"),
    "accept/<id>.toml: copied": _mark_stage("copied"),
    "accept/<id>.toml: max(стадия, verified)": _mark_stage("verified", at_least=True),
    "accept/<id>.toml: admin": _mark_stage("admin"),
    "accept/<id>.toml": Target(_mark, wrap="drop_mark"),
    "releases/<id> = отметке": Target(_release),
    "build_home/tmp": Target(_fixed(lambda lay: lay.build_home, "tmp")),
    "build_home/cache": Target(_fixed(lambda lay: lay.build_home, "cache")),
    "admin-python/.staging": Target(_fixed(lambda lay: lay.admin_python, ".staging")),
    "admin-python/cpython-…": Target(_python),
    "admin/<id>": Target(_admin),
    "staging/<id>/tree": Target(_staging_tree),
    "staging/<id>": Target(_staging_dir),
    "releases/<id>/release.json": Target(_release_json, wrap="write_release_json"),
    "/etc/df/releases.toml": Target(
        _fixed(lambda lay: lay.etc, "releases.toml"), wrap="register"
    ),
    "/etc/df/install.toml": Target(_fixed(lambda lay: lay.etc, "install.toml")),
    "/opt/df/current": Target(_fixed(lambda lay: lay.opt, "current")),
    "bin/darkfactory-entry.py": Target(
        _fixed(lambda lay: lay.bin_dir, "darkfactory-entry.py")
    ),
    "bin/darkfactory": Target(_fixed(lambda lay: lay.bin_dir, "darkfactory")),
    "/usr/local/bin/darkfactory": Target(
        _fixed(lambda lay: lay.local_bin, "darkfactory")
    ),
    "pending.toml": Target(_fixed(lambda lay: lay.install_dir, "pending.toml")),
}

#: Route leaves (and wrappers) implemented by later tasks, with the owner task.
#: `write_atomic` itself is called only for targets without a wrapper: the
#: launcher files (texts of Task 8a) and `install.toml` (Task 9).
LATER_LEAVES: Mapping[str, str] = {
    "static_problems": "8",
    "transfer_check": "8",
    "install_admin_python": "8a",
    "install_admin": "8a",
    "link_command": "8a",
    "activate": "9",
    "write_release_json": "9",
    "register": "9",
    "write_atomic": "9",
}


def is_nested(call: str) -> bool:
    """A call that is a nested route (a name that is also a protocol is a leaf)."""
    return call in table.ROUTES and call not in table.PROTOCOLS


def leaf_steps() -> list[tuple[str, str]]:
    """Every (call, target) of a leaf step of `ROUTES`, in first-use order."""
    out: list[tuple[str, str]] = []
    for steps in table.ROUTES.values():
        for _guard, call, target in steps:
            if not is_nested(call) and (call, target) not in out:
                out.append((call, target))
    return out


def implementation(call: str, target: str) -> str:
    """The key of `calls` that runs `call` on `target`: the target's wrapper
    when it wraps this protocol, else the call itself."""
    wrap = TARGETS[target].wrap
    return wrap if wrap is not None and table.WRAPS[wrap] == call else call


def where(
    layout: Layout, release_id: str, target: str, python_key: str | None = None
) -> tuple[Path, str]:
    """(parent, name) of a target's object; a target without one is an error."""
    get = TARGETS[target].where
    if get is None:
        raise ValueError(f"target without an object: {target!r}")
    return get(layout, release_id, python_key)


def check_targets() -> None:
    """Refuse a `TARGETS` table that does not match `ROUTES` exactly.

    Every leaf target resolves and every entry is used; a wrapper wraps the
    protocol its steps call; a stage only with `write_mark`.
    """
    steps = leaf_steps()
    texts = {t for _, t in steps}
    bad = [f"no target {t!r}" for t in sorted(texts - set(TARGETS))]
    bad += [f"unused target {t!r}" for t in sorted(set(TARGETS) - texts)]
    bad += [
        f"{call} {target!r}: wrapper {TARGETS[target].wrap} wraps another protocol"
        for call, target in steps
        if target in TARGETS
        and TARGETS[target].wrap is not None
        and table.WRAPS[str(TARGETS[target].wrap)] != call
    ]
    bad += [
        f"stage without write_mark: {t!r}"
        for t, v in TARGETS.items()
        if (v.stage is not None) != (v.wrap == "write_mark")
    ]
    if bad:
        raise AssertionError(f"route targets: {bad}")


# ---------- guards ----------


def route_guard(
    layout: Layout,
    release_id: str,
    guard: str,
    *,
    python_key: str | None = None,
    build_uid: int | None = None,
) -> bool:
    """The value of a route guard (spec 3.3 «Маршруты»).

    A name is read by `lstat` through the descriptor of its root-owned parent
    (`releases/`, `staging/`, `staging/<id>`, `admin/`, `admin-python/`,
    `accept/`); the object itself is never opened. `not reg` reads the registry
    through the hardened reader; `local_bin=trusted` is
    `trusted_path_problems(layout.local_bin)` empty. `py=absent` needs the
    admin Python key (F6); `r=root`/`r=builder` need `build_uid`: the root of
    `releases/<id>` owned by neither root nor `df-build` is `StepFailed`. A
    parent that is not a root-owned directory, or any error but absence, is
    `StepFailed`; an absent parent means an absent name.
    """
    return GUARDS[guard](layout, release_id, python_key, build_uid)


def _lstat_in(parent: Path, *names: str) -> os.stat_result | None:
    """`lstat` of `parent/names...`: every directory on the way is opened
    without following links and must be root's; the last name is not opened.
    Absent anywhere — `None`."""
    fd = _open_dir(parent, None, parent)
    if fd is None:
        return None
    path = parent
    try:
        for name in names[:-1]:
            path = path / name
            child = _open_dir(name, fd, path)
            if child is None:
                return None
            os.close(fd)
            fd = child
        try:
            return os.stat(names[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            detail = f"{path / names[-1]}: {exc.strerror or exc}"
            raise StepFailed(_GUARD_STEP, detail) from exc
    finally:
        os.close(fd)


def _open_dir(name: str | Path, dir_fd: int | None, path: Path) -> int | None:
    """A root-owned directory descriptor, `None` if absent; else `StepFailed`."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StepFailed(_GUARD_STEP, f"{path}: {exc.strerror or exc}") from exc
    owner = fsutil.owner_uid(os.fstat(fd))
    if owner != accept.ROOT_UID:
        os.close(fd)
        raise StepFailed(_GUARD_STEP, f"{path}: владелец uid {owner}, не root")
    return fd


def _root_kind(layout: Layout, release_id: str, build_uid: int | None) -> str:
    """`releases/<id>`: "root" or "builder" directory by owner, else "none".

    Another owner is outside the table: `StepFailed` naming it (F20).
    """
    if build_uid is None:
        raise AssertionError("build_uid is required for the r guards")
    path = layout.release_dir(release_id)
    st = _lstat_in(layout.releases, path.name)
    if st is None or not stat.S_ISDIR(st.st_mode):
        return "none"
    owner = fsutil.owner_uid(st)
    if owner == accept.ROOT_UID:
        return "root"
    if owner == build_uid:
        return "builder"
    raise StepFailed(
        _GUARD_STEP, f"{path}: владелец uid {owner} — ни root, ни df-build"
    )


Guard = Callable[[Layout, str, str | None, int | None], bool]


def _present(where_of: Where) -> Guard:
    def present(
        layout: Layout, release_id: str, key: str | None, _uid: int | None
    ) -> bool:
        parent, name = where_of(layout, release_id, key)
        return _lstat_in(parent, name) is not None

    return present


def _staging_container(
    layout: Layout, release_id: str, _key: str | None, _uid: int | None
) -> bool:
    name = layout.staging_dir(release_id).name
    return (
        _lstat_in(layout.staging, name) is not None
        and _lstat_in(layout.staging, name, "tree") is None
    )


def _staging_has_tree(
    layout: Layout, release_id: str, _key: str | None, _uid: int | None
) -> bool:
    name = layout.staging_dir(release_id).name
    return _lstat_in(layout.staging, name, "tree") is not None


_PY_STAGING = _fixed(lambda lay: lay.admin_python, ".staging")

#: Guard name -> its evaluation; keys exactly `GUARD_NAMES`.
GUARDS: Mapping[str, Guard] = {
    "r=root": lambda lay, i, _k, uid: _root_kind(lay, i, uid) == "root",
    "r=builder": lambda lay, i, _k, uid: _root_kind(lay, i, uid) == "builder",
    "S=tree": _staging_has_tree,
    "S=container": _staging_container,
    "admin=present": _present(_admin),
    "m=present": _present(_mark),
    "not reg": lambda lay, i, _k, _u: accept.registry_entry(lay, i) is None,
    "py=absent": lambda lay, i, k, u: not _present(_python)(lay, i, k, u),
    "py-staging=present": _present(_PY_STAGING),
    "local_bin=trusted": lambda lay, _i, _k, _u: accept.local_bin_trusted(lay),
}


# ---------- the interpreter ----------


def _refuse_registered(layout: Layout, release_id: str) -> None:
    """F13: what is registered is never removed."""
    if accept.registry_entry(layout, release_id) is not None:
        raise StepFailed("cleanup", f"{release_id} в releases.toml: не удаляется")


#: Checks a route makes before its first step (F13).
ENTRY_CHECKS: Mapping[str, Callable[[Layout, str], None]] = {
    "cleanup": _refuse_registered,
}


def run_route(
    layout: Layout,
    release_id: str,
    name: str,
    *,
    calls: Mapping[str, Call],
    python_key: str | None = None,
    build_uid: int | None = None,
) -> None:
    """Run `ROUTES[name]`: the only interpreter of routes.

    Each step's guard is read just before the step (false — skipped); a nested
    route runs recursively; any other call runs `calls[implementation(call,
    target)](target)`. `python_key` and `build_uid` go to the guards.
    """
    check = ENTRY_CHECKS.get(name)
    if check is not None:
        check(layout, release_id)
    keys = {"python_key": python_key, "build_uid": build_uid}
    for guard, call, target in table.ROUTES[name]:
        if guard and not route_guard(layout, release_id, guard, **keys):
            continue
        if is_nested(call):
            run_route(layout, release_id, call, calls=calls, **keys)
        else:
            calls[implementation(call, target)](target)


def route_action(
    layout: Layout,
    release_id: str,
    action: str,
    *,
    calls: Mapping[str, Call],
    python_key: str | None,
    build_uid: int,
) -> Callable[[Facts], None]:
    """The action of `drive` for a row: its route, run only in its own phase.

    The facts `drive` classified must give a row with this action, else
    `StepFailed(action, "не в своей фазе")` before any step (F14).
    """

    def act(facts: Facts) -> None:
        if table.row_of(accept.classify(facts)).action != action:
            raise StepFailed(action, "не в своей фазе")
        run_route(
            layout,
            release_id,
            action,
            calls=calls,
            python_key=python_key,
            build_uid=build_uid,
        )

    return act


# ---------- the calls of Task 7 ----------


@dataclass
class RouteContext:
    """Values a leaf hands to a later step of its route (F7); taken once.

    `installed` is every repo's installed list as `distribution_problems`
    accepted it, for `release.json` `distributions_declared` (Task 9).
    """

    uv_version: str | None = None
    copied_digest: str | None = None
    installed: dict[str, list[InstalledDist]] = field(default_factory=dict)


def acceptance_calls(
    layout: Layout,
    release_id: str,
    *,
    install: InstallConfig,
    profile: Profile,
    platform: PlatformName,
    plan: BuildPlan,
    limits: CopyLimits,
    refs: Mapping[str, Path],
    journal: Path,
    run_id: str,
    python_key: str,
    ctx: RouteContext | None = None,
) -> dict[str, Call]:
    """Implementations of the route calls Tasks 7 and 7a own (leaves, WRAPS).

    Keys and `LATER_LEAVES` together are exactly `route_leaves()` and `WRAPS`;
    Task 9 merges the later ones. The calls share one `RouteContext`: the
    build's uv version and the copy's digest reach the next mark.
    `python_key` is the caller's admin Python key — the same value it passes
    to `run_route`/`route_action` for the guards (one source, M5). A caller
    that reads the context afterwards (Task 9) passes its own `ctx`.
    """
    leaves = _Leaves(
        layout,
        release_id,
        install=install,
        profile=profile,
        platform=platform,
        plan=plan,
        limits=limits,
        refs=refs,
        journal=journal,
        run_id=run_id,
        python_key=python_key,
        ctx=ctx if ctx is not None else RouteContext(),
    )
    return leaves.calls()


@dataclass
class _Leaves:
    """The closures of `acceptance_calls` over one release and its context."""

    layout: Layout
    release_id: str
    install: InstallConfig
    profile: Profile
    platform: PlatformName
    plan: BuildPlan
    limits: CopyLimits
    refs: Mapping[str, Path]
    journal: Path
    run_id: str
    python_key: str
    ctx: RouteContext = field(default_factory=RouteContext)

    def calls(self) -> dict[str, Call]:
        lay, rid = self.layout, self.release_id
        return {
            "prepare_dir": lambda _t: prepare_dir(lay, rid),
            "run_build": self.run_build,
            "write_mark": self.write_mark,
            "reap_user_processes": self.reap,
            "seal": lambda _t: seal(lay, rid),
            "accept_copy": self.accept_copy,
            "remove_own_tree": self.remove_own_tree,
            "check_links": self.check_links,
            "verify_chmod": lambda _t: verify_chmod(lay, rid),
            "verify_sources": self.verify_sources,
            "distribution_problems": self.distribution_problems,
            "cleanup_build_home": self.cleanup_build_home,
            "tree_digest": self.tree_digest,
            "cleanup_tree": self.cleanup_tree,
            "remove_empty_dir": self.remove_empty_dir,
            "remove_durably": self.remove_durably,
            "drop_mark": lambda _t: accept.drop_mark(lay, rid),
        }

    @property
    def install_digest(self) -> str:
        return config_digest(self.install)

    def path(self, target: str) -> tuple[Path, str]:
        return where(self.layout, self.release_id, target, self.python_key)

    def run_build(self, _target: str) -> None:
        """Leaf `run_build` (builder); keeps the declared uv version (R29)."""
        self.ctx.uv_version = build_mod.run_build(
            self.plan, self.layout, runner=plat.run_as
        )

    def accept_copy(self, _target: str) -> None:
        """Leaf `accept_copy`; keeps the copy's digest for the mark."""
        self.ctx.copied_digest = accept_copy(
            self.layout,
            self.release_id,
            install=self.install,
            platform=self.platform,
            limits=self.limits,
        )

    def write_mark(self, target: str) -> None:
        """Wrapper of `write_atomic`: the mark the target names.

        `built` takes this boot's id and the build's uv version; every later
        stage carries both from the recorded mark (never `boot_id()`), `copied`
        takes the copy's digest, `admin` the admin code's digest and the run's
        Python; `max(стадия, verified)` rewrites a higher mark unchanged.
        Effects: `write_atomic()`.
        """
        spec = TARGETS[target]
        if spec.stage is None:
            raise AssertionError(f"not a mark target: {target!r}")
        old = accept.load_mark(
            self.layout, self.release_id, install_digest=self.install_digest
        )
        mark = self._next_mark(old, spec.stage, at_least=spec.at_least)
        accept.write_mark(self.layout, self.release_id, mark)

    def _next_mark(
        self, old: AcceptMark | None, stage: Stage, *, at_least: bool
    ) -> AcceptMark:
        if stage == "built":
            uv = _take(self.ctx, "uv_version")
            return AcceptMark(
                self.install_digest, "built", fsutil.boot_id(), None, None, None, uv
            )
        if old is None:
            raise StepFailed("mark", f"нет действующей отметки для {stage}")
        if at_least and STAGE_ORDER.index(old.stage) > STAGE_ORDER.index(stage):
            return old
        tree = _take(self.ctx, "copied_digest") if stage == "copied" else None
        admin = stage == "admin"
        return AcceptMark(
            self.install_digest,
            stage,
            old.built_boot_id,
            tree if tree is not None else old.tree_digest,
            self._admin_digest() if admin else None,
            self.profile.python if admin else None,
            old.uv_version_declared,
        )

    def _admin_digest(self) -> str:
        return tree_digest(self.layout.admin_dir(self.release_id), exclude=frozenset())

    def reap(self, target: str) -> None:
        """Leaf `reap_user_processes`; leftovers are a journal warning (F28)."""
        left = plat.reap_user_processes(target)
        if left:
            journal_event(
                self.journal,
                run_id=self.run_id,
                step="seal",
                event="warn",
                detail=f"процессы {target} остались: {left}",
            )

    def remove_own_tree(self, target: str) -> None:
        """Protocol `remove_own_tree`; never on a registered release (F13)."""
        if target in ("releases/<id>", "admin/<id>"):
            _refuse_registered(self.layout, self.release_id)
        receive.remove_own_tree(*self.path(target))

    def check_links(self, target: str) -> None:
        """Check leaf: every link of the copy stays inside the release."""
        tree = Path(*self.path(target))
        problems = check_links(tree, PurePosixPath(tree))
        if problems:
            raise StepFailed("verify", "; ".join(problems))

    def verify_sources(self, target: str) -> None:
        """Check leaf: sources equal the trusted checkout.

        The lock digests it returns are not kept: `release.json` (Task 10)
        calls `verify_sources` itself for `lock_sha256`.
        """
        tree = Path(*self.path(target))
        verify_sources(tree, self.profile, self.refs)

    def distribution_problems(self, target: str) -> None:
        """Check leaf: each repo's environment against its lock (spec 3.5).

        The installed metadata of `venv/<repo>` against
        `expected_distributions` of the trusted checkout's `uv.lock`, with the
        profile's groups, the install platform and Python and the build copy
        as the root. Problems of every repo go into one `StepFailed`; on
        success the accepted lists go to `ctx.installed` and the journal
        records the named limit (`DECLARED_LIMIT`) as `distributions-note`
        (the evidence-note form of `sync-note`).
        """
        tree = Path(*self.path(target))
        env = marker_env(self.platform, self.profile.python)
        installed: dict[str, list[InstalledDist]] = {}
        problems: list[str] = []
        for pin in self.profile.repos:
            ref = self.refs.get(pin.name)
            if ref is None:
                problems.append(f"{pin.name}: нет доверенного чекаута")
                continue
            try:
                have = installed_distributions(tree / "venv" / pin.name)
                want = expected_distributions(
                    (ref / "uv.lock").read_bytes(),
                    groups=pin.groups,
                    env=env,
                    root_dir=build_mod.build_src_dir(
                        self.layout, self.release_id, pin.name
                    ),
                )
            except (TreeError, UnsupportedLock, OSError) as exc:
                problems.append(f"{pin.name}: {exc}")
                continue
            installed[pin.name] = have
            problems += [f"{pin.name}: {p}" for p in distribution_problems(have, want)]
        if problems:
            raise StepFailed("verify", "; ".join([DECLARED_LIMIT, *problems]))
        self.ctx.installed = installed
        journal_event(
            self.journal,
            run_id=self.run_id,
            step="verify",
            event="distributions-note",
            detail=DECLARED_LIMIT,
        )

    def cleanup_build_home(self, target: str) -> None:
        """Protocol `cleanup_build_home` on `build_home/<name>`."""
        parent, name = self.path(target)
        if parent != self.layout.build_home:
            raise AssertionError(f"not in build_home: {target!r}")
        plat.cleanup_build_home(self.layout, name)

    def tree_digest(self, target: str) -> None:
        """Check leaf: the copy's digest equals the mark's."""
        tree = Path(*self.path(target))
        mark = accept.load_mark(
            self.layout, self.release_id, install_digest=self.install_digest
        )
        want = None if mark is None else mark.tree_digest
        have = release_digest(tree)
        if want is None or have != want:
            raise StepFailed("verify", f"{tree}: digest {have}, отметка {want}")

    def cleanup_tree(self, target: str) -> None:
        """Protocol `cleanup_tree` with `quarantine_label=<id>`."""
        parent, name = self.path(target)
        outcome = plat.cleanup_tree(
            self.layout, parent, name, quarantine_label=self.release_id
        )
        if outcome == "failed":
            raise StepFailed("cleanup", f"{parent / name}: очистка не удалась")

    def remove_empty_dir(self, target: str) -> None:
        """Protocol `remove_empty_dir`."""
        receive.remove_empty_dir(*self.path(target))

    def remove_durably(self, target: str) -> None:
        """Protocol `remove_durably` (targets without a wrapper)."""
        parent, name = self.path(target)
        fsutil.remove_durably(parent / name)


def _take(ctx: RouteContext, name: str) -> str:
    """A context value handed by an earlier step of the route, then cleared."""
    value = getattr(ctx, name)
    if not isinstance(value, str):
        raise StepFailed("mark", f"нет значения {name} от предыдущего шага маршрута")
    setattr(ctx, name, None)
    return value


check_targets()
if set(GUARDS) != table.GUARD_NAMES:
    raise AssertionError(f"GUARDS keys != GUARD_NAMES: {sorted(GUARDS)}")
