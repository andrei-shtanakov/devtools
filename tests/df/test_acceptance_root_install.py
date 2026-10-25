"""Root acceptance of the install route on the real host (Task 13, R73).

§3.8 lines of the install group over the root world of
`tests/df/root_world.py`: a real `df-build`, the approved CLI, a real build
of the fixture pin, the admin Python from uv. Runs only in CI df-stage1.
"""

from __future__ import annotations

import pytest

from df import doctor, state
from df.journal import read_journal
from tests.df.install_world import CLEAN_PASS
from tests.df.root_world import RootWorld

pytestmark = pytest.mark.root

#: Steps 1-3 of a fresh install, as `run_steps` journals them.
PREP_STEPS = [("manifest", "skip"), ("trusted-paths", "done"), ("build-user", "done")]


def _level1(world: RootWorld) -> list[doctor.Check]:
    lay = world.layout
    snap = state.parse_snapshot(state.read_raw(lay), lay, mode="reader")
    return doctor.level1_checks(snap, lay, euid=0)


def test_clean_install_reaches_level1(root_world: RootWorld) -> None:
    """`bootstrap init` on a fresh host ends with level 1 reached."""
    assert root_world.bootstrap(root_world.init_argv()) == 0
    checks = _level1(root_world)
    assert doctor.level1_reached(checks), [c for c in checks if c.status != "ok"]


def test_clean_pass_walks_automaton_in_order(root_world: RootWorld) -> None:
    """One clean install: steps 1-3, then each action of the table once, in
    order, each `start` followed by its `done`; nothing failed."""
    assert root_world.bootstrap(root_world.init_argv()) == 0
    rows = read_journal(root_world.layout.journal)
    assert [r for r in rows if r["event"] == "failed"] == []
    prep = [(r["step"], r["event"]) for r in rows if r["step"] in dict(PREP_STEPS)]
    assert [p for p in prep if p[1] in ("skip", "done")] == PREP_STEPS
    walk = [
        (r["step"], r["event"])
        for r in rows
        if r["step"] in CLEAN_PASS and r["event"] in ("start", "done")
    ]
    assert walk == [(a, e) for a in CLEAN_PASS for e in ("start", "done")]
