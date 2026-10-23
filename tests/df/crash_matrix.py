"""The crash matrix of Task 9 (part 9-D2): the model of `r18-local-check` on
the real code.

A clean pass of a real install (`fake_world`: the OS-crash model of
`tests/df/crash_model.py` over `fake_layout`) records the files at every
`fault_point` call -- every crash point, the window «visible, not durable».
From each point (a `State` snapshot, never by re-running the prefix, R49) a
crash is taken -- a process crash (the files as they were) or an OS crash
(only what was synced) -- and the retry goes the real path: the entry's
writer half while `current` exists, else bootstrap; lock,
`settle_on_acquire`, `main`, `recover_pending`, `drive` (never `drive`
alone, S1-R6-N1). The retry must reach a committed state on the expected
release id (C7: asserted explicitly, `at_rest_problems` cannot see «current
and install.toml disagree» when the configuration text is equal).

The oracle (`final_problems`) is `committed_problems` plus what the model's
`final_ok` asserts and the table's facts do not read: the admin Python is
there and `admin-python/.staging` is not, no `staging/<id>` of a registered
release, and no tree lost from its parent and the quarantine at once --
by the tree's identity (S1-R13-N2), not by a field. A `damaged` release
under `pending.toml` must end in the I9 refusal (G1-G4 are on the path);
the matrix classifies such a point as `i9`, never as a pass.

Each protocol call of a route is watched (`_RouteSpy`): its variant and
parent are recorded (every variant but `NOT_MODELED` must be executed), and
in a strict run it must return with its change durable. The cleaner's
outcome is the environment's (S1-R12-N1): a run that reached the cleaner is
repeated with the cleaner failing, so `cleanup_tree` goes `quarantine`.

R49 budget (task-9-rulings.md):
- depth 1: every point x {process, OS} x both starts (first install,
  upgrade); a first install is also retried with another configuration
  (the model's `other_cfg`) while `current` is absent -- through the entry
  another configuration is unreachable (I8);
- depth 2 only as «process crash, then OS crash at the first sync of the
  retry» (`R49`): the first sync after the retry's settle on acquisition. A
  trapped sync crashes before it takes effect, so the very first sync (the
  lock chain's) would equal a depth-1 OS crash; the first one after the
  settle leaves exactly the settled visible facts durable;
- twins expecting a counterexample stop at the first one (`hunt`), passing
  twins run only the points from the first call of the changed primitive
  or guard on (`sweep`); the full enumeration (`full`: depth 2 at every
  point of the retry) is `@pytest.mark.slow`.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from df import accept, install, launcher_entry, routes
from df import install_table as table
from df import platform as plat
from df.config import load_pending
from df.paths import Layout
from tests.df.crash_model import Crash, ModelError, Point, State
from tests.df.install_world import (
    FIRST,
    OTHER_SHA,
    PLATFORM,
    SECOND,
    SHA,
    World,
    at_rest_problems,
    committed_problems,
    current_id,
    registry,
)
from tests.df.route_world import KEY

FIRST_INSTALL = "first-install"
UPGRADE = "upgrade"
STARTS = (FIRST_INSTALL, UPGRADE)
UPGRADE_ID = f"{SHA[:12]}-{PLATFORM}-2"
TARGETS = {FIRST_INSTALL: FIRST, UPGRADE: UPGRADE_ID}
#: Crash schedules: depth 1 (process, OS) and the R49 form of depth 2.
CRASHES = ("process", "os", "r49")
#: The phases of a twin's search (`Matrix.hunt`), cheap first.
PHASES = ("d1", "sync", "deep", "deep-other")
#: The marker the upgrade's entry code carries (`L` false: `launcher` runs).
NEXT_RELEASE = "# the next release\n"
#: The I9 refusal (`install.damaged_refusal`), in every one of its forms.
I9_TEXT = "повреждён (§3.8)"

#: Innermost frame -> the protocol it implements (`install_table.PROTOCOLS`).
PROTOCOL_FRAMES: Mapping[tuple[str, str], str] = {
    ("df.fsutil", "write_atomic"): "write_atomic",
    ("df.fsutil", "remove_durably"): "remove_durably",
    ("df.receive", "remove_own_tree"): "remove_own_tree",
    ("df.receive", "remove_empty_dir"): "remove_empty_dir",
    ("df.platform", "cleanup_tree"): "cleanup_tree",
    ("df.leaves", "prepare_dir"): "prepare_dir",
    ("df.leaves", "seal"): "seal",
    ("df.leaves", "accept_copy"): "accept_copy",
    ("df.leaves", "verify_chmod"): "verify_chmod",
    ("df.admin", "install_admin"): "install_admin",
    ("df.admin", "install_admin_python"): "install_admin_python",
    ("df.admin", "link_command"): "link_command",
    ("df.release", "activate"): "activate",
}
#: The settles (RC-C1/RC-C2): what each syncs is a twin's to drop, together.
_CLEANUP_FRAME = frozenset({("df.platform", "_cleanup_at")})
#: `settle_on_acquire`: the entry's stdlib copy and `df.accept`'s (bootstrap).
ACQUIRE_FRAMES = frozenset(
    {("df.accept", "settle_on_acquire"), ("df.launcher_entry", "settle_on_acquire")}
)
SETTLE_FRAMES = frozenset(
    {
        ("df.accept", "settle"),
        ("df.accept", "settle_on_acquire"),
        ("df.launcher_entry", "settle_on_acquire"),
        ("df.install", "_settle_prep_dirs"),
    }
)


@dataclass(frozen=True)
class Branch:
    """A crash point of a run and the files at it (R49: branched from)."""

    point: Point
    state: State
    #: trees quarantined so far while their names were durable (S1-R13-N2)
    moved: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Schedule:
    """A pinned crash schedule (R55): the start's pass killed at its fault
    point `k` (`None`: the pass itself), then `kind` -- `process`, `os`,
    `r49`, or `deep` (a process crash, the retry with `sha`, an OS crash at
    its fault point `j`) -- and the retry to the end."""

    start: str
    k: int | None
    kind: str = "process"
    sha: str = SHA
    j: int | None = None
    quarantine: bool = False
    #: the kind of point `k` counts: `fault`, or `sync` (a trapped sync
    #: crashes before it takes effect; then `kind` is `os`)
    at: str = "fault"


@dataclass(frozen=True)
class Pass:
    """A clean pass of one start: where it began and its crash points."""

    start: str
    origin: State
    branches: tuple[Branch, ...]
    #: protocol, `(protocol, parent)`, route -> the point count at its first call
    first_call: Mapping[object, int]
    #: what the pass executed: protocol variants, parents, sync twin cases
    variants: frozenset[tuple[str, str | None]]
    parents: frozenset[tuple[str, str]]
    sync_cases: frozenset[table.TwinCase]


@dataclass
class Matrix:
    """The engine over one `World` (see the module docstring)."""

    w: World
    #: rule checks (each primitive durable on return, nothing unsynced at the
    #: end); off for twins, which drop a sync on purpose (the model's
    #: `SKIP_SYNC` turns its `Unsynced` check off the same way)
    strict: bool = True
    runs: int = 0
    i9: list[str] = field(default_factory=list)
    variants: set[tuple[str, str | None]] = field(default_factory=set)
    parents: set[tuple[str, str]] = field(default_factory=set)
    sync_cases: set[table.TwinCase] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    first_call: dict[object, int] = field(default_factory=dict)
    start: str = FIRST_INSTALL
    quarantine: bool = False
    #: parents (`SYNC_PARENTS` names) of the trees the cleaner ran on
    cleaned: set[str] = field(default_factory=set)
    #: (start, protocol, variant, parent) whose return was checked durable
    checked: set[tuple[object, ...]] = field(default_factory=set)
    settled: bool = False
    moved: set[int] = field(default_factory=set)
    passes: dict[str, Pass] = field(default_factory=dict)
    #: the passes with the cleaner failing: `cleanup_tree` goes `quarantine`
    qpasses: dict[str, Pass] = field(default_factory=dict)
    empty: State | None = None
    installed: State | None = None

    # ---- set-up ----------------------------------------------------------

    def install_spies(self) -> None:
        """Watch routes, the cleaner, the settles and every sync (once)."""
        mp, m = self.w.mp, self.w.m
        spy = _RouteSpy(self)
        real_run_route = routes.run_route

        def run_route(layout: Layout, release_id: str, name: str, **kw: object) -> None:
            self.first_call.setdefault(name, len(m.points))
            calls = kw.pop("calls")
            assert isinstance(calls, Mapping)
            spied = spy.wrap(calls, layout, release_id, kw.get("python_key"))
            real_run_route(layout, release_id, name, calls=spied, **kw)  # type: ignore[arg-type]

        mp.setattr(routes, "run_route", run_route)
        mp.setattr(install, "run_route", run_route)
        real_cleanup = plat._cleanup_at  # noqa: SLF001 - the environment's outcome
        real_runner = plat._run_cleaner  # noqa: SLF001

        def cleanup_at(layout: Layout, parent: Path, *a: object) -> object:
            if parent != layout.quarantine:
                self.cleaned.add(parent_key(layout, parent) or str(parent))
            tree = self.tree_id(parent / str(a[1]))
            out = real_cleanup(layout, parent, *a)  # type: ignore[arg-type]
            if out == "removed":
                self.moved.discard(tree)  # destroyed on purpose (model: q_ever - tid)
            return out

        def run_cleaner(*a: object, **kw: object) -> bool:
            if self.quarantine and innermost(_CLEANUP_FRAME) is not None:
                return False  # the cleaner's command failed (environment)
            return real_runner(*a, **kw)  # type: ignore[arg-type]

        mp.setattr(plat, "_cleanup_at", cleanup_at)
        mp.setattr(plat, "_run_cleaner", run_cleaner)
        real_home = plat.cleanup_build_home

        def cleanup_build_home(*a: object, **kw: object) -> str:
            out = real_home(*a, **kw)  # type: ignore[arg-type]
            self.variants.add(
                ("cleanup_build_home", "absent" if out == "absent" else "tree")
            )
            return out

        mp.setattr(plat, "cleanup_build_home", cleanup_build_home)
        for module in (accept, launcher_entry):
            real_settle = module.settle_on_acquire

            def settle_on_acquire(*a: object, _real: object = real_settle) -> None:
                _real(*a)  # type: ignore[operator]
                self.settled = True

            mp.setattr(module, "settle_on_acquire", settle_on_acquire)
        real_refs = install.fetch_references

        def references(*a: object, **kw: object) -> dict[str, Path]:
            refs = real_refs(*a, **kw)  # type: ignore[arg-type]
            if self.start == UPGRADE:
                entry = refs["devtools"] / "df" / "launcher_entry.py"
                entry.write_text(entry.read_text() + NEXT_RELEASE)
            return refs

        mp.setattr(install, "fetch_references", references)
        m.trap(self._watch)

    def _watch(self, p: Point) -> bool:
        """Every point: the sync twin case it implements (coverage)."""
        if p.kind == "sync":
            case = sync_case(self, p)
            if case is not None:
                self.sync_cases.add(case)
        return False

    def base(self) -> None:
        """The clean passes of both starts (once per world)."""
        if self.passes:
            return
        self.install_spies()
        self.empty = self.w.m.snapshot()
        for start in STARTS:
            self.passes[start] = self.clean_pass(start)
        self.quarantine = True
        try:
            for start in STARTS:
                self.qpasses[start] = self.clean_pass(start)
        finally:
            self.quarantine = False
        assert self.problems == [], self.problems

    # ---- the searches ----------------------------------------------------

    def schedules(self, start: str) -> tuple[tuple[str, str], ...]:
        """Depth 1 (and R49) per point: (crash, configuration)."""
        same = tuple((kind, SHA) for kind in CRASHES)
        if start == FIRST_INSTALL:
            return same + (("process", OTHER_SHA), ("os", OTHER_SHA))
        return same

    def sweep(self, branches: Iterator[Branch] | tuple[Branch, ...]) -> list[str]:
        """Every schedule of `schedules` at every branch; all problems."""
        found: list[str] = []
        for branch in branches:
            for kind, sha in self.schedules(self.start):
                found += self.crash(branch, kind, sha) or []
        return found

    def hunt(
        self, focus: Callable[[Point], bool], phases: tuple[str, ...] = PHASES
    ) -> list[str]:
        """The first counterexample of a twin (R49: stop at the first), in
        `phases` (`PHASES` order; `focus` points first in each): `d1` --
        each start's pass, then depth 1 and R49 at every fault point; `sync`
        -- an OS crash at every sync point; `deep` and `deep-other` --
        depth 2 with the same and with another configuration."""
        passes: dict[str, list[Branch]] = {}
        for start in STARTS:
            branches, found = self.run_pass(start)
            if found and "d1" in phases:
                return found
            passes[start] = branches

        def ordered(branches: list[Branch]) -> list[Branch]:
            return sorted(branches, key=lambda b: not focus(b.point))

        for phase in phases:
            for start in STARTS:
                self.start = start
                found = self._phase(phase, start, ordered, passes[start])
                if found:
                    return found
        return []

    def _phase(
        self,
        phase: str,
        start: str,
        ordered: Callable[[list[Branch]], list[Branch]],
        branches: list[Branch],
    ) -> list[str]:
        if phase == "sync":
            for branch in ordered(self.sync_points(start)):
                found = self.os_at(branch)
                if found:
                    return found
            return []
        for branch in ordered(branches):
            if phase == "d1":
                for kind, sha in self.schedules(start):
                    found = self.crash(branch, kind, sha) or []
                    if found:
                        return found
                continue
            found = self.deep(branch, sha=SHA if phase == "deep" else OTHER_SHA)
            if found:
                return found
        return []

    def sync_points(self, start: str) -> list[Branch]:
        """The start's pass recorded at every sync point (the model's every
        step is a crash point; the plan's matrix has the fault points)."""
        m = self.w.m
        m.restore(self.origin(start))
        self.start, self.moved = start, set()
        with self.recording("sync") as out:
            quiet(self.command)
        self.runs += 1
        return out

    def os_at(self, branch: Branch) -> list[str]:
        """An OS crash at `branch` (a sync point), settled and completed."""
        self.w.m.restore(branch.state)
        self.moved = set(branch.moved)
        self.w.m.os_crash()
        where = f"{self.start} OS at sync {describe(self.w, branch.point)}"
        return [f"{where}: {p}" for p in self.settle_down(SHA)]

    def deep(
        self,
        branch: Branch,
        *,
        sha: str = SHA,
        stop: bool = True,
        only: int | None = None,
    ) -> list[str]:
        """Depth 2: a process crash at `branch`, then an OS crash at every
        fault point of the retry with `sha` (`only`: at that one); each
        settled and completed. Another configuration while `current` exists
        is unreachable (I8): nothing."""
        m = self.w.m
        m.restore(branch.state)
        self.moved = set(branch.moved)
        if sha != SHA and os.path.lexists(self.w.lay.current):
            return []
        where = f"{self.start} deep {sha[:1]} at {describe(self.w, branch.point)}"
        with self.recording() as inner:
            quiet(lambda: self.retry(sha))
        self.runs += 1
        found: list[str] = []
        for n, j in enumerate(inner):
            if only is not None and n != only:
                continue
            m.restore(j.state)
            self.moved = set(j.moved)
            m.os_crash()
            here = f"{where}, OS at {n}: {describe(self.w, j.point)}"
            found += [f"{here}: {p}" for p in self.settle_down(sha)]
            if found and stop:
                return found
        return found

    def pinned(self, s: Schedule) -> list[str]:
        """One schedule from the start's origin (the pass runs only up to
        its crash point): the problems, or `["unreachable: …"]`."""
        m = self.w.m
        self.quarantine = s.quarantine
        if s.k is None:
            return self.run_pass(s.start)[1]
        m.restore(self.origin(s.start))
        self.start, self.moved = s.start, set()
        seen = [0]

        def at_k(p: Point) -> bool:
            seen[0] += p.kind == s.at
            return p.kind == s.at and seen[0] == s.k + 1

        m.trap(at_k)
        try:
            died = self.dies(self.command)
        finally:
            m.untrap(at_k)
        self.runs += 1
        if not died:
            return [f"unreachable: the pass ended before point {s.k}"]
        branch = Branch(m.points[-1], m.snapshot(), frozenset(self.moved))
        if s.kind == "deep":
            found = self.deep(branch, sha=s.sha, only=s.j)
        else:
            found = self.crash(branch, s.kind, s.sha)
        return ["unreachable: another configuration"] if found is None else found

    def dies(self, run: Callable[[], int]) -> bool:
        """`run()`, killed at an armed trap (then reaped); whether it died."""
        try:
            quiet(run)
        except Crash:
            self.w.m.reap()
            return True
        return False

    # ---- a pass ----------------------------------------------------------

    def target(self, start: str | None = None) -> str:
        return TARGETS[start or self.start]

    def command(self) -> int:
        """The start's own command: bootstrap `init`, or the entry's `install`."""
        if self.start == FIRST_INSTALL:
            return self.w.bootstrap(self.w.init_argv())
        return self.w.entry(["install"])

    def quarantined(self, start: str) -> tuple[Branch, ...]:
        """The quarantine pass's points from its first move into the
        quarantine on (S1-R12-N1: the other outcome of the cleaner)."""
        branches = self.qpasses[start].branches
        q = self.w.lay.quarantine
        first = next(
            i
            for i, b in enumerate(branches)
            if b.point.op == "renamed" and b.point.path.parent == q
        )
        return branches[first:]

    def origin(self, start: str) -> State:
        """Before the start's command: an empty host, or `FIRST` committed."""
        state = self.empty if start == FIRST_INSTALL else self.installed
        assert state is not None, "base() first"
        return state

    def reset(self) -> None:
        """A test's own counters and twin switches; the base stays."""
        self.w.m.disarm()
        self.w.m.trap(self._watch)
        self.strict, self.quarantine = True, False
        self.runs = 0
        self.i9, self.problems, self.moved = [], [], set()
        self.variants, self.parents, self.sync_cases = set(), set(), set()
        self.checked = set()

    def clean_pass(self, start: str) -> Pass:
        """The start's command without a crash, from its origin; every fault
        point recorded, with what the pass executed."""
        seen = (set(self.variants), set(self.parents), set(self.sync_cases))
        self.first_call.clear()
        branches, found = self.run_pass(start)
        self.problems += found
        return Pass(
            start,
            self.origin(start),
            tuple(branches),
            dict(self.first_call),
            frozenset(self.variants - seen[0]),
            frozenset(self.parents - seen[1]),
            frozenset(self.sync_cases - seen[2]),
        )

    def run_pass(self, start: str) -> tuple[list[Branch], list[str]]:
        """The start's command from its origin (a twin's pass too): the
        recorded points and the oracle's problems (empty: committed)."""
        m = self.w.m
        m.restore(self.origin(start))
        self.start = start
        self.moved = set()
        with self.recording() as branches:
            rc, err = quiet(self.command)
        self.runs += 1
        if rc != 0:
            found = [f"exit {rc}: {err[-300:]}"]
        else:
            found = self.final_problems(self.target())
        if start == FIRST_INSTALL and self.installed is None:
            self.installed = m.snapshot()
        return branches, [f"{start} pass: {p}" for p in found]

    @contextlib.contextmanager
    def recording(self, kind: str = "fault") -> Iterator[list[Branch]]:
        """Record a `Branch` at every point of `kind` until the block ends
        (at a sync: the state before it takes effect)."""
        m = self.w.m
        out: list[Branch] = []

        def record(p: Point) -> bool:
            if p.kind == kind:
                out.append(Branch(p, m.snapshot(), frozenset(self.moved)))
            return False

        m.trap(record)
        try:
            yield out
        finally:
            m.untrap(record)

    # ---- one branch ------------------------------------------------------

    def crash(self, branch: Branch, kind: str, sha: str = SHA) -> list[str] | None:
        """Crash at `branch` (`process`, `os` or `r49`), then the retry with
        `sha`'s configuration; the problems (a counterexample), or none.
        `None`: another configuration while `current` exists -- unreachable
        through the entry (I8), not a run."""
        m = self.w.m
        m.restore(branch.state)
        self.moved = set(branch.moved)
        where = f"{self.start} {kind} {sha[:1]} at {describe(self.w, branch.point)}"
        if kind == "os":
            m.os_crash()
        if sha != SHA and os.path.lexists(self.w.lay.current):
            return None
        if kind == "r49":
            self.settled = False
            m.trap(self._after_settle)
            try:
                died = self.killed(sha)
            finally:
                m.untrap(self._after_settle)
            if not died:
                return [f"{where}: the retry never synced after its settle"]
            m.os_crash()
        return [f"{where}: {p}" for p in self.settle_down(sha)]

    def _after_settle(self, p: Point) -> bool:
        return p.kind == "sync" and self.settled

    def killed(self, sha: str) -> bool:
        """The retry, killed at an armed trap; whether it died."""
        return self.dies(lambda: self.retry(sha))

    def settle_down(self, sha: str) -> list[str]:
        """At rest after a crash: the invariants, then the retry to the
        expected id; a retry that reached the cleaner is repeated from the
        same state with the cleaner failing (S1-R12-N1)."""
        rest = self.at_rest()
        if rest:
            return [f"at rest: {p}" for p in rest]
        before = self.w.m.snapshot()
        expected = self.expected(sha)
        found = self.finish(sha, expected)
        if found or self.quarantine or "releases" not in self.cleaned:
            return found
        self.w.m.restore(before)
        self.quarantine = True
        try:
            return [f"quarantine: {p}" for p in self.finish(sha, expected)]
        finally:
            self.quarantine = False

    def tree_id(self, path: Path) -> int:
        """The model id of a directory at `path`, else 0."""
        try:
            return self.w.m.identity(path) if os.path.isdir(path) else 0
        except (ModelError, OSError):
            return 0

    def at_rest(self) -> list[str]:
        return at_rest_problems(self.w) + self.lost()

    def finish(self, sha: str, expected: str | None) -> list[str]:
        """The retry; then the oracle. A strict run has nothing unsynced
        (`committed_problems`), so an OS crash would change nothing the
        oracle reads; a twin's run (not strict) is checked after one too."""
        self.cleaned = set()
        self.runs += 1
        damaged = self.damaged_under_pending()
        rc, err = quiet(lambda: self.retry(sha))
        if damaged:
            if rc == 2 and I9_TEXT in err:
                self.i9.append(damaged)
                return [f"i9: {damaged}"]
            return [f"damaged under pending ({damaged}), retry: {rc} {err[-200:]}"]
        if rc != 0:
            return [f"retry exit {rc}: {err[-300:]}"]
        assert expected is not None
        found = self.final_problems(expected, sha)
        if found or self.strict:
            return found
        self.w.m.os_crash()
        return [f"after OS crash: {p}" for p in self.final_problems(expected, sha)]

    def retry(self, sha: str) -> int:
        """What the operator runs again: `init` through bootstrap while
        there is no `current`, else the command's `install`."""
        if os.path.lexists(self.w.lay.current):
            return self.w.entry(["install"])
        return self.w.bootstrap(self.w.init_argv(sha))

    def expected(self, sha: str) -> str | None:
        """The release id the retry must commit (C7: explicit).

        `pending.toml` survived: its id (the run's, or -- in depth 2 after
        the run had committed -- the reinstall's next one), unless another
        configuration rolls it back while `current` is not it. None survived
        and `current` moved past the start's: the run had committed, the
        retry is a reinstall (the next id). Otherwise the run's id.
        """
        lay = self.w.lay
        pending = load_pending(lay.pending)
        cur = current_id(self.w)
        other = sha != SHA
        before = None if self.start == FIRST_INSTALL else FIRST
        if pending is not None:
            rid = pending.release_id
            if rid not in {x for t in (self.target(), SECOND) for x in (t, bump(t))}:
                self.problems.append(f"pending.toml names {rid}")
            return SECOND if other and cur != rid else rid
        if cur not in (None, before):
            return bump(cur)  # committed: whatever was asked, a reinstall
        return SECOND if other else self.target()

    def damaged_under_pending(self) -> str:
        """`current` is the pending id and that release is damaged (I9)."""
        pending = load_pending(self.w.lay.pending)
        cur = current_id(self.w)
        if pending is None or cur != pending.release_id:
            return ""
        entry = registry(self.w).get(cur)
        if entry is not None and accept.entry_intact(
            self.w.lay, cur, entry, admin_ready=install.admin.admin_ready
        ):
            return ""
        return f"{cur} damaged under pending.toml"

    # ---- the oracle ------------------------------------------------------

    def final_problems(self, release_id: str, sha: str = SHA) -> list[str]:
        """`committed_problems` (its `unsynced` rule only when strict) and
        the model's `final_ok`: the admin Python present, no `.staging`, no
        `staging/<id>` of the release, no tree lost."""
        lay = self.w.lay
        found = committed_problems(self.w, release_id, self.w.cfg(sha))
        if not self.strict:
            found = [p for p in found if not p.startswith("unsynced ")]
        if not os.path.isdir(lay.admin_python / KEY):
            found.append("admin Python missing")
        if os.path.lexists(lay.admin_python / ".staging"):
            found.append("admin-python/.staging left")
        if os.path.lexists(lay.staging_dir(release_id)):
            found.append(f"staging/{release_id} left")
        return found + self.lost()

    def lost(self) -> list[str]:
        """Trees moved to the quarantine while durable that are now neither
        at a source nor in the quarantine (by identity, S1-R13-N2)."""
        if not self.moved:
            return []
        alive = self.alive()
        return [f"tree {t} lost from parent and quarantine" for t in self.moved - alive]

    def alive(self) -> set[int]:
        lay, m = self.w.lay, self.w.m
        found: set[int] = set()
        for top, sub in (
            (lay.releases, ""),
            (lay.staging, "tree"),
            (lay.quarantine, ""),
        ):
            if not os.path.isdir(top):
                continue
            for name in os.listdir(top):
                path = top / name / sub if sub else top / name
                if name != ".quarantine" and os.path.isdir(path):
                    try:
                        found.add(m.identity(path))
                    except ModelError:  # never seen: not a tree moved earlier
                        continue
        return found


# ---------- the routes' calls ----------


class _Spied(dict[str, routes.Call]):
    """Calls already wrapped (a nested route gets the same mapping)."""


@dataclass
class _RouteSpy:
    """Each protocol call of a route: its variant, parent, durability."""

    mx: Matrix

    def wrap(
        self,
        calls: Mapping[str, routes.Call],
        layout: Layout,
        rid: str,
        key: object,
    ) -> Mapping[str, routes.Call]:
        if isinstance(calls, _Spied):
            return calls
        python_key = key if isinstance(key, str) else None
        return _Spied(
            {k: self._call(k, c, layout, rid, python_key) for k, c in calls.items()}
        )

    def _call(
        self,
        key: str,
        inner: routes.Call,
        layout: Layout,
        rid: str,
        python_key: str | None,
    ) -> routes.Call:
        protocol = table.WRAPS.get(key, key)
        if protocol not in table.PROTOCOLS or protocol == "cleanup_build_home":
            return inner
        mx = self.mx

        def call(target: str) -> None:
            parent, name = routes.where(layout, rid, target, python_key)
            mx.first_call.setdefault(protocol, len(mx.w.m.points))
            pkey = parent_key(layout, parent)
            if pkey is not None:
                mx.first_call.setdefault((protocol, pkey), len(mx.w.m.points))
            before = _variant_before(protocol, layout, rid, parent / name)
            if before == "dir" and mx.quarantine and _durable_name(mx, parent / name):
                mx.moved.add(mx.w.m.identity(parent / name))
            quarantined = _names(layout.quarantine)
            tree = mx.tree_id(parent / name) if protocol == "remove_own_tree" else 0
            try:
                inner(target)
            except Exception:
                if protocol == "cleanup_tree" and before == "dir":
                    mx.variants.add((protocol, "failed"))
                raise
            mx.moved.discard(tree)  # root's tree removed on purpose
            variant = before
            if protocol == "cleanup_tree" and before == "dir":
                grew = _names(layout.quarantine) - quarantined
                variant = "quarantine" if grew else "tree"
            mx.variants.add((protocol, variant))
            if protocol in table.SYNC_PARENTS and pkey is not None:
                mx.parents.add((protocol, pkey))
            seen = (mx.start, protocol, variant, pkey, mx.quarantine)
            if mx.strict and seen not in mx.checked:
                mx.checked.add(seen)  # a primitive's own property: once per kind
                self._durable(protocol, layout, rid, parent / name, target)

        return call

    def _durable(
        self, protocol: str, layout: Layout, rid: str, path: Path, target: str
    ) -> None:
        """Each primitive returns with its change durable (rule check): the
        object and, for root's trees, all inside it. A builder tree (`seal`,
        `cleanup_tree`) is df-build's: only its names are root's to sync, so
        they are checked by name (`durable_names`), not by state."""
        m = self.mx.w.m
        if protocol in ("seal", "cleanup_tree"):
            box = layout.staging_dir(rid)
            names = [path, box, box / "tree"] if protocol == "seal" else [path]
            names += [layout.quarantine / n for n in _names(layout.quarantine)]
            left = [
                str(n.relative_to(layout.root))
                for n in names
                if os.path.lexists(n) != (n.name in m.durable_names(n.parent))
            ]
        else:
            rel = str(path.relative_to(layout.root))
            inside = protocol != "prepare_dir"  # its tree is df-build's
            left = [
                u
                for u in m.unsynced()
                if u == rel or (inside and u.startswith(rel + "/"))
            ]
        if left:
            self.mx.problems.append(
                f"{protocol}({target}) returns with {left} unsynced"
            )


def _durable_name(mx: Matrix, path: Path) -> bool:
    """The tree's name is durable in its parent (its content is df-build's
    and never synced: the model's «existed durably» is the name's)."""
    return path.name in mx.w.m.durable_names(path.parent)


def _variant_before(protocol: str, layout: Layout, rid: str, path: Path) -> str | None:
    """The variant a call starts in (cleanup_tree: what is there)."""
    if protocol == "seal":
        return "reuse" if os.path.lexists(layout.staging_dir(rid)) else "new"
    if protocol != "cleanup_tree":
        return None
    if not os.path.lexists(path):
        return "absent"
    return "dir" if os.path.isdir(path) and not path.is_symlink() else "link"


def _names(path: Path) -> set[str]:
    return set(os.listdir(path)) if os.path.isdir(path) else set()


def parent_key(layout: Layout, parent: Path) -> str | None:
    """The `SYNC_PARENTS` name of a parent directory."""
    named = {
        layout.releases: "releases",
        layout.admin: "admin",
        layout.admin_python: "admin_python",
        layout.install_dir: "install",
        layout.marks_dir: "accept",
        layout.staging: "staging",
    }
    if parent in named:
        return named[parent]
    if parent.parent == layout.staging:
        return "staging"
    return None


# ---------- sync twin cases ----------


#: Frames inside a protocol whose syncs are not the protocol's steps.
INNER_FRAMES = frozenset({("df.fsutil", "make_dir"), ("df.fsutil", "create_file")})


def innermost(
    names: frozenset[tuple[str, str]],
) -> tuple[FrameType, tuple[str, str]] | None:
    """The innermost frame on the stack whose (module, function) is named."""
    frame: FrameType | None = sys._getframe(2)  # noqa: SLF001
    while frame is not None:
        key = (str(frame.f_globals.get("__name__")), frame.f_code.co_name)
        if key in names:
            return frame, key
        frame = frame.f_back
    return None


def sync_case(mx: Matrix, p: Point) -> table.TwinCase | None:
    """The sync twin case (`install_table.sync_twin_cases`) a sync point is
    the step of: its protocol by the innermost protocol frame, the step by
    the operation and the directory synced; anything else is None."""
    found = innermost(frozenset(PROTOCOL_FRAMES) | INNER_FRAMES)
    if found is None or found[1] not in PROTOCOL_FRAMES:
        return None
    frame, key = found
    n = PROTOCOL_FRAMES[key]
    lay = mx.w.lay
    loc = frame.f_locals
    if n in ("write_atomic", "remove_durably"):
        parent = Path(loc["path"]).parent
        if p.op != "sync_dir" or p.path != parent:
            return None
        return (n, None, 1) if n == "write_atomic" else _with_parent(lay, n, 1, parent)
    if n in ("remove_own_tree", "remove_empty_dir"):
        parent = Path(loc["parent"])
        return _with_parent(lay, n, 1, parent) if p.path == parent else None
    if n == "cleanup_tree":
        return _cleanup_case(mx, p, Path(loc["parent"]))
    if n == "seal":
        new = bool(loc.get("new"))
        if p.path == lay.releases:
            return (n, "new", 2) if new else (n, "reuse", 1)
        if p.path == lay.staging or p.path.parent == lay.staging:
            return (n, "new", 3) if new else (n, "reuse", 2)
        return None
    for step, pred in _STEP_SYNCS[n]:
        if pred(lay, p):
            return (n, None, step)
    return None


def _cleanup_case(mx: Matrix, p: Point, parent: Path) -> table.TwinCase | None:
    lay = mx.w.lay
    if p.path == lay.quarantine:
        return _with_parent(lay, "cleanup_tree", 2, parent, "quarantine")
    if p.path != parent:
        return None
    if mx.quarantine:
        return _with_parent(lay, "cleanup_tree", 3, parent, "quarantine")
    return _with_parent(lay, "cleanup_tree", 2, parent, "tree")


SyncPred = Callable[[Layout, Point], bool]
#: The sync steps of the protocols without variants or parents: (step, which sync).
_STEP_SYNCS: Mapping[str, tuple[tuple[int, SyncPred], ...]] = {
    "prepare_dir": (
        (2, lambda lay, p: p.op == "sync_dir" and p.path.parent == lay.releases),
        (3, lambda lay, p: p.op == "sync_dir" and p.path == lay.releases),
    ),
    "accept_copy": (
        (3, lambda lay, p: p.op == "sync_tree" and p.path.parent == lay.releases),
        (4, lambda lay, p: p.op == "sync_dir" and p.path == lay.releases),
    ),
    "verify_chmod": (
        (1, lambda lay, p: p.op == "sync_dir" and p.path.parent == lay.releases),
    ),
    "install_admin": (
        (2, lambda lay, p: p.op == "sync_tree" and p.path.parent == lay.admin),
        (3, lambda lay, p: p.op == "sync_dir" and p.path == lay.admin),
    ),
    "install_admin_python": (
        (1, lambda lay, p: p.op == "sync_tree" and lay.admin_python in p.path.parents),
        (3, lambda lay, p: p.op == "sync_dir" and p.path == lay.admin_python),
    ),
    "activate": ((1, lambda lay, p: p.op == "sync_dir" and p.path == lay.opt),),
    "link_command": (
        (1, lambda lay, p: p.op == "sync_dir" and p.path == lay.local_bin),
    ),
}


def _with_parent(
    lay: Layout, name: str, step: int, parent: Path, variant: str | None = None
) -> table.TwinCase:
    return (name, variant, step, parent_key(lay, parent))


# ---------- helpers ----------


def bump(release_id: str) -> str:
    """The next id of the same prefix (`…-1` -> `…-2`)."""
    prefix, n = release_id.rsplit("-", 1)
    return f"{prefix}-{int(n) + 1}"


def quiet(run: Callable[[], int]) -> tuple[int, str]:
    """`run()` with stdout and stderr captured; (exit code, stderr). An
    exception (the entry's own refusal, a program error) is exit -1; a
    model `Crash` (a `BaseException`) passes."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = run()
        except Exception as exc:  # noqa: BLE001 - reported as the run's result
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            rc = -1
    return rc, err.getvalue()


def describe(w: World, p: Point) -> str:
    return f"#{p.index} {p.op} {os.path.relpath(p.path, w.lay.root)}"
