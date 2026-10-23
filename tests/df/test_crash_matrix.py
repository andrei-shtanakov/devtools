"""The crash matrix and its twin families (Task 9, part 9-D2; R49 budget).

The engine is `tests/df/crash_matrix.py`. One world serves the module: the
clean passes of both starts are taken once and every test branches from
their snapshots (R49: no prefix is re-run per branch), so `fake_world` is
module-scoped here. Each test resets the engine's counters and its twin
switches (`Matrix.reset`); a twin's patches are the test's `monkeypatch`.

A twin removes one rule and must give a counterexample -- found by `hunt`,
which stops at the first -- or, where the model of `r18-local-check` says
the rule is not needed (the literals below, M20: copied, never imported),
must pass the R49 scope from the first call of the changed primitive or
guard on (`sweep`). Every test asserts its number of runs.

`slow` tests are deselected by default (`pyproject.toml` addopts): run them
with `-m slow` -- a slow test named by node id alone is deselected (exit 5).
A pinned schedule that answers `unreachable` means the pass changed shape
(re-pin it), not a durability defect.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from df import install_table as table
from df import platform as plat
from df.journal import StepFailed
from df.paths import Layout
from tests.df.conftest import make_fake_layout, make_fake_world
from tests.df.crash_matrix import (
    ACQUIRE_FRAMES,
    DRIVE_SETTLE_FRAMES,
    PHASES,
    SETTLE_FRAMES,
    SHA,
    STARTS,
    Matrix,
    Schedule,
    innermost,
    sync_case,
)
from tests.df.crash_model import Point
from tests.df.install_world import OTHER_SHA, World

_MATRICES: dict[int, Matrix] = {}


@pytest.fixture(scope="module")
def fake_world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    """`fake_world` for the whole module (the base passes are shared)."""
    mp = pytest.MonkeyPatch()
    old = os.umask(0o022)
    try:
        world = make_fake_world(make_fake_layout(mp, tmp_path_factory.mktemp("mx")), mp)
        yield world
        world.m.close()
        _MATRICES.pop(id(world), None)
    finally:
        mp.undo()
        os.umask(old)


def matrix(w: World) -> Matrix:
    """The module's engine, its base passes taken once, counters reset."""
    mx = _MATRICES.get(id(w))
    if mx is None:
        mx = Matrix(w)
        mx.base()  # cached only when complete (a failed base is not reused)
        _MATRICES[id(w)] = mx
    mx.reset()
    return mx


#: Fault points of a clean pass per start, pinned (R49: no silent truncation).
POINTS = {"first-install": 49, "upgrade": 26}
FI, UP = "first-install", "upgrade"
OTHER = OTHER_SHA


def at(start: str, k: int | None, kind: str = "process", **kw: object) -> Schedule:
    return Schedule(start, k, kind, **kw)  # type: ignore[arg-type]


#: Sync schedules by name (M-6): the first sync of the quarantine (the post
#: action settle's first) after the mark's `verified` / `admin` write.
MARK = "var/lib/df/install/accept/<id>.toml"
MARK_VERIFIED = ("renamed", MARK, 3)
MARK_ADMIN = ("renamed", MARK, 4)
QUARANTINE = "opt/df/staging/.quarantine"


def rollback_deep(k: int, j: int, *, quarantine: bool = False) -> Schedule:
    """Killed at `k`, a retry with the other configuration (rollback), an OS
    crash at its fault point `j`."""
    return Schedule(FI, k, "deep", OTHER, j, quarantine)


#: Runs of `test_crash_matrix`: per start, every point x `CRASHES`; the first
#: install's other configuration where `current` is absent after the crash;
#: the quarantine passes' points x 2; the retries repeated with the cleaner
#: failing (S1-R12-N1); the four clean passes of the base are not counted.
MATRIX_RUNS = 386

# r18-local-check/twins.py (M20: literals, never imported).
#: Guards whose removal gives no counterexample: ("launcher", 2) --
#: /usr/local/bin is trusted in the matrix, the false branch is
#: test_usr_local_link_only_when_trusted (Task 8a). twins.py also lists
#: ("cleanup", 1) (cleanup_tree of an absent object is "removed"); the real
#: rollback's cleanup_tree first requires the df-build account (I-1), so the
#: guard is needed here (9-D2 F1, R55).
GUARD_NOT_NEEDED = {("launcher", 2)}
#: cleanup_tree's absent outcome is not needed: every call is guarded.
ABSENT_NOT_NEEDED = {"cleanup_tree"}


def test_crash_matrix(fake_world: World) -> None:
    """Depth 1 (every point x process/OS x both starts, a first install's
    retry also with another configuration while `current` is absent), the
    R49 depth 2, and the cleaner's other outcome; every retry commits the
    expected id, no point reaches `damaged`, every modeled variant, parent
    and sync twin case is executed."""
    mx = matrix(fake_world)
    started = time.monotonic()
    found: list[str] = []
    for start in STARTS:
        mx.start = start
        p = mx.passes[start]
        assert len(p.branches) == POINTS[start], [b.point for b in p.branches]
        chunks = [(a, b) for s_, a, b in FULL_CHUNKS if s_ == start]
        covered = [i for a, b in chunks for i in range(a, b)]
        assert covered == list(range(POINTS[start])), "FULL_CHUNKS (M-5)"
        found += mx.sweep(p.branches)
    quarantined = 0
    mx.quarantine = True
    for start in STARTS:
        mx.start = start
        branches = mx.quarantined(start)
        quarantined += len(branches)
        for branch in branches:
            for kind in ("process", "os"):
                found += mx.crash(branch, kind) or []
    mx.quarantine = False
    elapsed = time.monotonic() - started
    print(
        f"\ncrash matrix: {mx.runs} runs ({quarantined} quarantine points), {elapsed:.1f} s"
    )
    assert found == [], "\n".join(found[:20])
    assert mx.problems == [], "\n".join(mx.problems[:20])
    assert mx.i9 == []
    passes = [*mx.passes.values(), *mx.qpasses.values()]
    variants = mx.variants.union(*(p.variants for p in passes))
    parents = mx.parents.union(*(p.parents for p in passes))
    cases = mx.sync_cases.union(*(p.sync_cases for p in passes))
    want = {(n, v) for n in table.PROTOCOLS for v in table.modeled_variants(n)}
    assert want - variants == set()
    assert parents == {(n, p) for n, ps in table.SYNC_PARENTS.items() for p in ps}
    assert set(table.sync_twin_cases()) - cases == set()
    assert mx.runs == MATRIX_RUNS


# ---------- twins ----------

Twin = tuple[str, object]  # (family, argument)
SETTLE_DROPPED = ["etc_df", "opt_df", "releases", "release_root", "staging", "admin",
                  "opt_bin", "local_bin"]  # fmt: skip
GUARDED = [(r, i) for r, s in table.ROUTES.items() for i, (g, *_) in enumerate(s) if g]
ACQUIRE_FORMS = ["no-op", "without-id-paths"]


#: Twins the model expects a counterexample of, but whose search on the real
#: code -- every fault and sync point, depth 2 with both configurations --
#: finds none (9-D2 findings, R55: argued, not deleted):
#: - settle without release_root: its only fact is the root's mode. Three
#:   syncs make it durable: the settle, `verify_chmod`'s own and `register`'s
#:   `write_atomic(releases/<id>/release.json)` (its parent sync is the
#:   root's); only all three dropped give a counterexample
#:   (`test_crash_matrix_release_root_masking`);
#: - settle without admin: `install_admin`'s and `remove_own_tree`'s own
#:   syncs cover `admin/`; dropped together with `install_admin`'s sync it is
#:   a counterexample at once (`test_crash_matrix_settle_admin_masking`);
#: - settle_on_acquire without the pending id's paths (C1 of 9-C): what reads
#:   them before `drive` is the rollback's guards, and each guarded step's
#:   primitive syncs its own result before `pending.toml` goes.
REDUNDANT: set[Twin] = {
    ("settle", "release_root"),
    ("settle", "admin"),
    ("acquire", "without-id-paths"),
}


def expects_counterexample(twin: Twin) -> bool:
    """The model's expectation (r18-local-check/twins.py, M20 literals),
    less `REDUNDANT`."""
    if twin in REDUNDANT:
        return False
    family, arg = twin
    if family == "guard":
        return arg not in GUARD_NOT_NEEDED
    if family == "protocol":
        return arg in table.SYNC_NEEDED
    if family == "absent":
        return arg not in ABSENT_NOT_NEEDED
    return True  # settle, acquire


def apply_twin(
    mx: Matrix, mp: pytest.MonkeyPatch, twin: Twin
) -> Callable[[Point], bool]:
    """Remove the twin's rule; returns its focus (where its effect begins)."""
    family, arg = twin
    mx.strict = False
    lay = mx.w.lay
    if family == "settle":
        match = dir_of(lay, str(arg))
        mx.w.m.drop_syncs(
            lambda p: (
                p.kind == "sync"
                and match(p.path)
                and innermost(SETTLE_FRAMES) is not None
            )
        )
        return lambda p: match(p.path) or match(p.path.parent)
    if family == "drive-settle":  # `drive`'s settle frame only (RC-C2)
        mx.w.m.drop_syncs(
            lambda p: p.kind == "sync" and innermost(DRIVE_SETTLE_FRAMES) is not None
        )
        return lambda p: True
    if family == "release-json":  # register's release.json: its parent sync
        mx.w.m.drop_syncs(
            lambda p: (
                p.kind == "sync"
                and p.path.parent == lay.releases
                and sync_case(mx, p) == ("write_atomic", None, 1)
            )
        )
        return lambda p: True
    if family == "acquire":

        def id_path(p: Path) -> bool:
            return p.parent == lay.releases or (
                p.parent == lay.staging and p != lay.quarantine
            )

        mx.w.m.drop_syncs(
            lambda p: (
                p.kind == "sync"
                and innermost(ACQUIRE_FRAMES) is not None
                and (arg == "no-op" or id_path(p.path))
            )
        )
        return lambda p: True
    if family == "guard":
        assert isinstance(arg, tuple)
        route, index = arg
        steps = list(table.ROUTES[route])
        _guard, call, target = steps[index]
        steps[index] = (None, call, target)
        mp.setattr(table, "ROUTES", {**table.ROUTES, route: tuple(steps)})
        return first_point(mx, route)
    if family == "protocol":
        assert isinstance(arg, tuple)
        mx.quarantine = arg[1] == "quarantine"
        mx.w.m.drop_syncs(lambda p: p.kind == "sync" and sync_case(mx, p) == arg)
        return first_point(mx, (arg[0], arg[3]) if len(arg) > 3 else arg[0])
    assert family == "absent"
    if arg == "cleanup_build_home":
        real_home = plat.cleanup_build_home

        def home(*a: object, **kw: object) -> str:
            out = real_home(*a, **kw)  # type: ignore[arg-type]
            if out == "absent":
                raise StepFailed("verify", "twin: absent refused")
            return out

        mp.setattr(plat, "cleanup_build_home", home)
        return first_point(mx, "verify")
    real_tree = plat.cleanup_tree

    def tree(layout: Layout, parent: Path, name: str, **kw: object) -> str:
        if not os.path.lexists(parent / name):
            raise StepFailed("cleanup", f"twin: {parent / name} absent")
        return real_tree(layout, parent, name, **kw)  # type: ignore[arg-type]

    mp.setattr(plat, "cleanup_tree", tree)
    return first_point(mx, "cleanup_tree")


def first_point(mx: Matrix, key: object) -> Callable[[Point], bool]:
    """Points from the first call of `key` on, in the start's clean pass."""
    first = {s: mx.passes[s].first_call.get(key, 0) for s in STARTS}
    return lambda p: p.index >= first[mx.start]


def dir_of(lay: Layout, name: str) -> Callable[[Path], bool]:
    """Whether a synced path is the `install_table.DIRS` directory `name`."""
    exact = {
        "etc_df": lay.etc,
        "opt_df": lay.opt,
        "releases": lay.releases,
        "admin": lay.admin,
        "opt_bin": lay.bin_dir,
        "local_bin": lay.local_bin,
    }
    if name in exact:
        return lambda p: p == exact[name]
    if name == "release_root":
        return lambda p: p.parent == lay.releases
    assert name == "staging"
    return lambda p: (
        p == lay.staging or (p.parent == lay.staging and p != lay.quarantine)
    )


def check_twin(mx: Matrix, mp: pytest.MonkeyPatch, twin: Twin) -> None:
    """R55. A counterexample twin: its pinned schedule gives none with the
    rule in place and a counterexample without it (both halves). A passing
    twin (slow): the R49 sweep from its focus on finds none."""
    if not expects_counterexample(twin):
        focus = apply_twin(mx, mp, twin)
        found: list[str] = []
        if twin in REDUNDANT:  # its first phase; the rest: ..._redundant_twins
            found = mx.hunt(focus, ("d1",))
        for start in STARTS if twin not in REDUNDANT else ():
            branches, problems = mx.run_pass(start)
            found += problems + mx.sweep(tuple(b for b in branches if focus(b.point)))
        print(f"\n{twin}: {mx.runs} runs, {found[:1] or 'no counterexample'}")
        assert found == []
        assert mx.runs == SWEEP_RUNS[twin]
        return
    schedule = PINNED[twin]
    mx.strict = False  # the twin's oracle for both halves
    assert mx.pinned(schedule) == [], "the rule in place: no counterexample"
    apply_twin(mx, mp, twin)
    found = mx.pinned(schedule)
    print(f"\n{twin}: {mx.runs} runs, {found[:1]}")
    assert found and not found[0].startswith("unreachable"), found
    assert mx.runs == PINNED_RUNS[twin]


def twin_params(family: str, args: list[object]) -> list[object]:
    """The family's parameters; the passing ones are `slow` (R55)."""
    return [
        a
        if expects_counterexample((family, a))
        else pytest.param(a, marks=pytest.mark.slow)
        for a in args
    ]


@pytest.mark.parametrize("dropped", twin_params("settle", SETTLE_DROPPED))
def test_crash_matrix_settle_twins(
    fake_world: World, dropped: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every settle without the directory `dropped`, in combination (the
    entry's and bootstrap's `settle_on_acquire`, step 2's
    `_settle_prep_dirs`, `drive`'s `settle`: RC-C1/RC-C2 -- singly each is
    masked by another) -> a counterexample. install/accept are not dropped:
    «not needed for safety in the model, kept by the rule» (twins.py)."""
    check_twin(matrix(fake_world), monkeypatch, ("settle", dropped))


@pytest.mark.parametrize("prim", twin_params("absent", sorted(table.ABSENT_OK)))
def test_crash_matrix_absent_ok_twins(
    fake_world: World, prim: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prim` refuses an absent object: for `cleanup_build_home` the refused
    retry of `verify` (the r9 form); `cleanup_tree` passes (every call is
    guarded), as in r18-local-check/twins.py."""
    check_twin(matrix(fake_world), monkeypatch, ("absent", prim))


@pytest.mark.parametrize("step", twin_params("guard", GUARDED))
def test_crash_matrix_route_guard_twins(
    fake_world: World, step: tuple[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard of `step` taken as true -> a primitive's refusal on an
    absent object; exactly `GUARD_NOT_NEEDED` pass (S1-R10-N3)."""
    check_twin(matrix(fake_world), monkeypatch, ("guard", step))


@pytest.mark.parametrize("case", twin_params("protocol", table.sync_twin_cases()))
def test_crash_matrix_protocol_twins(
    fake_world: World, case: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync step of `case` taken as empty (with a parent: only in calls
    with that parent) -> a counterexample exactly when `case` is in
    `SYNC_NEEDED`; the others pass (C2: the admin syncs among them)."""
    check_twin(matrix(fake_world), monkeypatch, ("protocol", case))


def test_crash_matrix_acquire_twin(
    fake_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`settle_on_acquire` (the entry's and bootstrap's) taken as empty -> a
    counterexample (twins.py «no settle_on_acquire»)."""
    check_twin(matrix(fake_world), monkeypatch, ("acquire", "no-op"))


@pytest.mark.slow
def test_crash_matrix_acquire_twin_id_paths(
    fake_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1: `settle_on_acquire` without only the pending id's paths
    (`release_root`, `staging/<id>`)."""
    check_twin(matrix(fake_world), monkeypatch, ("acquire", "without-id-paths"))


def test_crash_matrix_settle_admin_masking(
    fake_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REDUNDANT settle of admin/ and `install_admin`'s own
    `sync_dir(admin)` (a passing protocol twin) mask each other: with the
    sync dropped alone none, with both dropped a counterexample."""
    mx = matrix(fake_world)
    masking(
        mx,
        monkeypatch,
        at(FI, 13),
        [("settle", "admin")],
        [("protocol", ("install_admin", None, 3))],
    )
    assert mx.runs == ADMIN_MASKING_RUNS


def masking(
    mx: Matrix,
    mp: pytest.MonkeyPatch,
    schedule: Schedule,
    covers: list[Twin],
    covered: list[Twin],
) -> list[str]:
    """`covers` mask `covered`: none with all in place, none with only
    `covered` dropped, a counterexample with both dropped."""
    mx.strict = False
    assert mx.pinned(schedule) == [], "all in place"
    with pytest.MonkeyPatch.context() as only:
        for twin in covered:
            apply_twin(mx, only, twin)
        assert mx.pinned(schedule) == [], f"{covered} dropped, {covers} in place"
        mx.undrop()  # the drops of `covered` go with the context
    for twin in covers + covered:
        apply_twin(mx, mp, twin)
    found = mx.pinned(schedule)
    print(f"\n{covers} + {covered}: {mx.runs} runs, {found[:1]}")
    assert found and not found[0].startswith("unreachable"), found
    return found


DRIVE_SETTLE_PAIRS = {
    "activate": (("protocol", ("activate", None, 1)), at(FI, 42, "os")),
    "install_admin/3": (("protocol", ("install_admin", None, 3)), at(FI, 37)),
    "link_command": (("protocol", ("link_command", None, 1)), at(FI, 45, "os")),
    "accept_copy/4": (("protocol", ("accept_copy", None, 4)), at(FI, 27)),
}
#: Runs of each masking test: three halves of 2 runs each.
DRIVE_SETTLE_RUNS = dict.fromkeys(DRIVE_SETTLE_PAIRS, 6)
ADMIN_MASKING_RUNS = 6


@pytest.mark.parametrize("pair", sorted(DRIVE_SETTLE_PAIRS))
def test_crash_matrix_drive_settle_masking(
    fake_world: World, pair: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-C2: `drive`'s own settle is masked alone (by `settle_on_acquire`)
    and witnessed paired: it covers the C2 sync `pair` -- with only that sync
    dropped no counterexample, with `drive`'s settle dropped too one. A
    no-op `drive` settle turns the middle half red."""
    mx = matrix(fake_world)
    twin, schedule = DRIVE_SETTLE_PAIRS[pair]
    masking(mx, monkeypatch, schedule, [("drive-settle", None)], [twin])
    assert mx.runs == DRIVE_SETTLE_RUNS[pair]


def test_crash_matrix_release_root_masking(
    fake_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-1: the REDUNDANT settle of release_root is backed by
    `verify_chmod`'s own sync and by register's release.json parent sync:
    with those two dropped no counterexample, with the settle dropped too
    one."""
    mx = matrix(fake_world)
    masking(
        mx,
        monkeypatch,
        RELEASE_ROOT_SCHEDULE,
        [("settle", "release_root")],
        [("protocol", ("verify_chmod", None, 1)), ("release-json", None)],
    )
    assert mx.runs == RELEASE_ROOT_RUNS


COUNTEREXAMPLE_TWINS: list[Twin] = [
    t
    for t in (
        *[("settle", d) for d in SETTLE_DROPPED],
        *[("acquire", f) for f in ACQUIRE_FORMS],
        *[("guard", g) for g in GUARDED],
        *[("protocol", c) for c in table.sync_twin_cases()],
        *[("absent", a) for a in sorted(table.ABSENT_OK)],
    )
    if expects_counterexample(t)
]


def phase_of(schedule: Schedule) -> str:
    """The `Matrix.hunt` phase a pinned schedule was found in."""
    if schedule.kind == "deep":
        return "deep" if schedule.sha == SHA else "deep-other"
    return "sync" if schedule.at == "sync" else "d1"


@pytest.mark.slow
@pytest.mark.parametrize("twin", COUNTEREXAMPLE_TWINS, ids=str)
def test_crash_matrix_twin_hunts(
    fake_world: World, twin: Twin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open-ended search behind each pinned schedule (R55: outside CI):
    `Matrix.hunt` in the phase the schedule belongs to finds a
    counterexample."""
    mx = matrix(fake_world)
    found = mx.hunt(apply_twin(mx, monkeypatch, twin), (phase_of(PINNED[twin]),))
    print(f"\nhunt {twin}: {mx.runs} runs, {found[:1]}")
    assert found
    assert mx.runs == HUNT_RUNS[twin]


@pytest.mark.slow
@pytest.mark.parametrize("phase", PHASES[1:])
@pytest.mark.parametrize("twin", sorted(REDUNDANT, key=str), ids=str)
def test_crash_matrix_redundant_twins(
    fake_world: World, twin: Twin, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`REDUNDANT`: no counterexample in the whole search (its `d1` phase is
    the family test's), one phase per test (each under 600 s)."""
    mx = matrix(fake_world)
    found = mx.hunt(apply_twin(mx, monkeypatch, twin), (phase,))
    print(f"\nredundant {twin} {phase}: {mx.runs} runs, {found[:1]}")
    assert found == []
    assert mx.runs == REDUNDANT_RUNS[(twin, phase)]


#: Pinned first-counterexample schedules (R55), found by the hunts: `k` is
#: the pass's fault point (`at="sync"`: its sync point), `None` the pass.
PINNED: dict[Twin, Schedule] = {
    ("settle", "etc_df"): at(FI, 41, "deep", j=1),
    ("settle", "opt_df"): at(FI, 42),
    ("settle", "releases"): rollback_deep(26, 4),
    ("settle", "staging"): at(FI, 31),
    ("settle", "opt_bin"): at(UP, 20),
    ("settle", "local_bin"): at(FI, 45),
    ("acquire", "no-op"): rollback_deep(26, 4),
    ("guard", ("admin", 0)): at(FI, None),
    ("guard", ("admin", 1)): at(UP, None),
    ("guard", ("admin", 2)): at(UP, None),
    ("guard", ("admin", 3)): at(FI, None),
    ("guard", ("cleanup", 0)): at(FI, 6, sha=OTHER),
    ("guard", ("cleanup", 1)): at(FI, 6, sha=OTHER),
    ("guard", ("cleanup", 3)): at(FI, 6, sha=OTHER),
    ("guard", ("cleanup", 4)): at(FI, 6, sha=OTHER),
    ("guard", ("cleanup_staging", 0)): at(FI, 31),
    ("guard", ("cleanup_staging", 1)): at(FI, 31),
    ("guard", ("rollback", 0)): at(FI, 41, sha=OTHER),
    ("protocol", ("write_atomic", None, 1)): at(FI, 24),
    ("protocol", ("remove_own_tree", None, 1, "releases")): rollback_deep(22, 2),
    ("protocol", ("remove_own_tree", None, 1, "admin")): rollback_deep(37, 4),
    ("protocol", ("remove_own_tree", None, 1, "admin_python")): at(
        FI, None, "os", at="sync", anchor=MARK_ADMIN, sync_rel=QUARANTINE
    ),
    ("protocol", ("cleanup_tree", "tree", 2, "releases")): rollback_deep(23, 2),
    ("protocol", ("cleanup_tree", "quarantine", 2, "releases")): rollback_deep(
        23, 1, quarantine=True
    ),
    ("protocol", ("cleanup_tree", "quarantine", 2, "staging")): at(
        FI, 31, "os", quarantine=True
    ),
    ("protocol", ("cleanup_tree", "quarantine", 3, "releases")): rollback_deep(
        23, 2, quarantine=True
    ),
    ("protocol", ("accept_copy", None, 3)): at(FI, 27),
    ("protocol", ("install_admin", None, 2)): at(FI, 37),
    ("protocol", ("remove_empty_dir", None, 1, "staging")): at(
        FI, None, "os", at="sync", anchor=MARK_VERIFIED, sync_rel=QUARANTINE
    ),
    ("protocol", ("install_admin_python", None, 1)): at(FI, 34),
    ("absent", "cleanup_build_home"): at(FI, None),
}
#: Runs of each pinned twin: both halves (each half = the pass up to `k`
#: plus the crash's retries; `deep`: plus its recorded retry).
#: Process crash at the first point, the retry, the OS crash after its commit.
RELEASE_ROOT_SCHEDULE = at(FI, 0)
RELEASE_ROOT_RUNS = 6
REDUNDANT_RUNS: dict[tuple[Twin, str], int] = {
    (("settle", "release_root"), "sync"): 740,
    (("settle", "release_root"), "deep"): 1706,
    (("settle", "release_root"), "deep-other"): 1587,
    (("settle", "admin"), "sync"): 740,
    (("settle", "admin"), "deep"): 1706,
    (("settle", "admin"), "deep-other"): 1587,
    (("acquire", "without-id-paths"), "sync"): 740,
    (("acquire", "without-id-paths"), "deep"): 1706,
    (("acquire", "without-id-paths"), "deep-other"): 1587,
}
PINNED_RUNS: dict[Twin, int] = {
    ("settle", "etc_df"): 5,
    ("settle", "opt_df"): 4,
    ("settle", "releases"): 5,
    ("settle", "staging"): 4,
    ("settle", "opt_bin"): 4,
    ("settle", "local_bin"): 4,
    ("absent", "cleanup_build_home"): 2,
    ("guard", ("admin", 0)): 2,
    ("guard", ("admin", 1)): 2,
    ("guard", ("admin", 2)): 2,
    ("guard", ("admin", 3)): 2,
    ("guard", ("cleanup", 0)): 4,
    ("guard", ("cleanup", 1)): 4,
    ("guard", ("cleanup", 3)): 4,
    ("guard", ("cleanup", 4)): 4,
    ("guard", ("cleanup_staging", 0)): 4,
    ("guard", ("cleanup_staging", 1)): 4,
    ("guard", ("rollback", 0)): 4,
    ("protocol", ("write_atomic", None, 1)): 4,
    ("protocol", ("remove_own_tree", None, 1, "releases")): 5,
    ("protocol", ("remove_own_tree", None, 1, "admin")): 5,
    ("protocol", ("remove_own_tree", None, 1, "admin_python")): 4,
    ("protocol", ("cleanup_tree", "tree", 2, "releases")): 5,
    ("protocol", ("cleanup_tree", "quarantine", 2, "releases")): 5,
    ("protocol", ("cleanup_tree", "quarantine", 2, "staging")): 3,
    ("protocol", ("cleanup_tree", "quarantine", 3, "releases")): 5,
    ("protocol", ("accept_copy", None, 3)): 4,
    ("protocol", ("install_admin", None, 2)): 4,
    ("protocol", ("remove_empty_dir", None, 1, "staging")): 4,
    ("protocol", ("install_admin_python", None, 1)): 4,
    ("acquire", "no-op"): 5,
}
SWEEP_RUNS: dict[Twin, int] = {
    ("protocol", ("remove_durably", None, 1, "install")): 11,
    ("protocol", ("remove_durably", None, 1, "accept")): 318,
    ("protocol", ("cleanup_tree", "tree", 2, "staging")): 129,
    ("protocol", ("cleanup_tree", "quarantine", 3, "staging")): 132,
    ("protocol", ("prepare_dir", None, 2)): 196,
    ("protocol", ("prepare_dir", None, 3)): 199,
    ("protocol", ("seal", "new", 2)): 172,
    ("absent", "cleanup_tree"): 129,
    ("guard", ("launcher", 2)): 38,
    ("protocol", ("seal", "new", 3)): 172,
    ("protocol", ("seal", "reuse", 1)): 172,
    ("protocol", ("seal", "reuse", 2)): 172,
    ("protocol", ("accept_copy", None, 4)): 153,
    ("protocol", ("verify_chmod", None, 1)): 113,
    ("protocol", ("install_admin", None, 3)): 82,
    ("protocol", ("activate", None, 1)): 42,
    ("protocol", ("link_command", None, 1)): 23,
    ("protocol", ("install_admin_python", None, 3)): 142,
    ("settle", "release_root"): 318,
    ("settle", "admin"): 318,
    ("acquire", "without-id-paths"): 318,
}
HUNT_RUNS: dict[Twin, int] = {
    ("settle", "opt_df"): 33,
    ("settle", "staging"): 31,
    ("settle", "opt_bin"): 241,
    ("settle", "local_bin"): 3,
    ("guard", ("admin", 0)): 1,
    ("guard", ("admin", 1)): 2,
    ("guard", ("admin", 2)): 2,
    ("guard", ("admin", 3)): 1,
    ("guard", ("cleanup", 0)): 36,
    ("guard", ("cleanup", 1)): 36,
    ("guard", ("cleanup", 3)): 36,
    ("guard", ("cleanup", 4)): 36,
    ("guard", ("cleanup_staging", 0)): 8,
    ("guard", ("cleanup_staging", 1)): 8,
    ("guard", ("rollback", 0)): 217,
    ("protocol", ("write_atomic", None, 1)): 3,
    ("protocol", ("accept_copy", None, 3)): 3,
    ("protocol", ("install_admin", None, 2)): 3,
    ("protocol", ("install_admin_python", None, 1)): 3,
    ("absent", "cleanup_build_home"): 1,
    ("protocol", ("remove_own_tree", None, 1, "admin_python")): 9,
    ("protocol", ("cleanup_tree", "quarantine", 2, "staging")): 8,
    ("protocol", ("remove_empty_dir", None, 1, "staging")): 9,
    ("settle", "etc_df"): 36,
    ("settle", "releases"): 1167,
    ("acquire", "no-op"): 1061,
    ("protocol", ("remove_own_tree", None, 1, "releases")): 918,
    ("protocol", ("remove_own_tree", None, 1, "admin")): 1435,
    ("protocol", ("cleanup_tree", "tree", 2, "releases")): 951,
    ("protocol", ("cleanup_tree", "quarantine", 2, "releases")): 950,
    ("protocol", ("cleanup_tree", "quarantine", 3, "releases")): 951,
}


# ---------- the full enumeration (outside CI) ----------


#: The full enumeration in chunks of the passes' fault points (< 600 s each).
FULL_CHUNKS = [(FI, 0, 17), (FI, 17, 34), (FI, 34, POINTS[FI]), (UP, 0, POINTS[UP])]
FULL_RUNS: dict[tuple[str, int, int], int] = {
    ("first-install", 0, 17): 699,
    ("first-install", 17, 34): 416,
    ("first-install", 34, 49): 178,
    ("upgrade", 0, 26): 411,
}
SYNC_RUNS: dict[str, int] = {
    "first-install": 383,
    "upgrade": 355,
}


@pytest.mark.slow
@pytest.mark.parametrize("chunk", FULL_CHUNKS, ids=str)
def test_crash_matrix_full(fake_world: World, chunk: tuple[str, int, int]) -> None:
    """Depth 2 everywhere: a process crash at every point of the pass, then
    an OS crash at every fault point of its retry; each settled and
    completed. Prints its run count (R49)."""
    mx = matrix(fake_world)
    start, first, end = chunk
    mx.start = start
    started = time.monotonic()
    found: list[str] = []
    for branch in mx.passes[start].branches[first:end]:
        found += mx.deep(branch, stop=False)
    print(f"\nfull {chunk}: {mx.runs} runs, {time.monotonic() - started:.0f} s")
    assert found == [], "\n".join(found[:20])
    assert mx.i9 == []
    assert mx.runs == FULL_RUNS[chunk]


@pytest.mark.slow
@pytest.mark.parametrize("start", STARTS)
def test_crash_matrix_sync_points(fake_world: World, start: str) -> None:
    """An OS crash at every sync point of the pass (the model's every step is
    a crash point; the plan's matrix has the fault points): settled and
    completed, no counterexample (9-D2 F5)."""
    mx = matrix(fake_world)
    mx.strict = False
    found: list[str] = []
    for branch in mx.sync_points(start):
        found += mx.os_at(branch)
    print(f"\nsync points {start}: {mx.runs} runs")
    assert found == [], "\n".join(found[:20])
    assert mx.runs == SYNC_RUNS[start]


QUARANTINE_BRANCH_RUNS = {FI: 685, UP: 203}
ALL_CLEANERS_RUNS = {FI: 413, UP: 114}


@pytest.mark.slow
@pytest.mark.parametrize("start", STARTS)
def test_crash_matrix_quarantine_branches(fake_world: World, start: str) -> None:
    """M-3: the quarantine passes' points from the first move into the
    quarantine on, with what CI gives only the plain passes: R49, the other
    configuration (first install), depth 2 with both configurations."""
    mx = matrix(fake_world)
    mx.quarantine = True
    mx.start = start
    found: list[str] = []
    for branch in mx.quarantined(start):
        found += mx.crash(branch, "r49") or []
        if start == FI:
            for kind in ("process", "os"):
                found += mx.crash(branch, kind, OTHER) or []
        found += mx.deep(branch, stop=False)
        found += mx.deep(branch, sha=OTHER, stop=False)
    print(f"\nquarantine branches {start}: {mx.runs} runs")
    assert found == [], "\n".join(found[:20])
    assert mx.runs == QUARANTINE_BRANCH_RUNS[start]


@pytest.mark.slow
@pytest.mark.parametrize("start", STARTS)
def test_crash_matrix_all_cleaners(fake_world: World, start: str) -> None:
    """M-2: depth 1 and R49 with every retry whose cleaner ran repeated with
    the cleaner failing (CI repeats only those that cleaned a releases/
    tree; the staging one's quarantine is the quarantine passes')."""
    mx = matrix(fake_world)
    mx.branch_cleaners = "all"
    mx.start = start
    found = mx.sweep(mx.passes[start].branches)
    print(f"\nall cleaners {start}: {mx.runs} runs")
    assert found == [], "\n".join(found[:20])
    assert mx.runs == ALL_CLEANERS_RUNS[start]
