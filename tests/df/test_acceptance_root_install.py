"""Root acceptance of the install route on the real host (Task 13, R73).

§3.8 lines of the install group over the root world of
`tests/df/root_world.py`: a real `df-build`, the approved CLI, a real build
of the fixture pin, the admin Python from uv. Runs only in CI df-stage1.
"""

from __future__ import annotations

import subprocess

import pytest

from df import doctor, state
from df.journal import read_journal
from tests.df.install_world import CLEAN_PASS
from tests.df.root_world import PIN_NAME, ROLE_ENV, RootWorld

pytestmark = pytest.mark.root

#: Steps 1-3 of a fresh install, as `run_steps` journals them.
PREP_STEPS = [("manifest", "skip"), ("trusted-paths", "done"), ("build-user", "done")]
#: The only other journal step of a clean run: `drive`'s `sync-note`.
OTHER_STEPS = ("drive",)
#: Events of an action besides start/done in a clean run (Task 7a).
NOTES = {("verify", "distributions-note"), ("drive", "sync-note")}


#: Removed with Tcl/Tk (owner decision 25.10, spec 3.4).
NO_TK = ("tkinter", "_tkinter", "turtle", "turtledemo", "idlelib")
#: Core capabilities that must survive the removal.
CORE = (
    "import ctypes, sqlite3, ssl, venv; ssl.create_default_context(); "
    "sqlite3.connect(':memory:').execute('select 1'); ctypes.CDLL(None)"
)


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
    order, each `start` followed by its `done`; no other step (no recovery
    action such as `copy-partial`), nothing failed, and the last action
    ends in `committed`."""
    assert root_world.bootstrap(root_world.init_argv()) == 0
    rows = read_journal(root_world.layout.journal)
    assert [r for r in rows if r["event"] == "failed"] == []
    allowed = {*dict(PREP_STEPS), *CLEAN_PASS, *OTHER_STEPS}
    assert {str(r["step"]) for r in rows} <= allowed, rows
    prep = [(r["step"], r["event"]) for r in rows if r["step"] in dict(PREP_STEPS)]
    assert [p for p in prep if p[1] in ("skip", "done")] == PREP_STEPS
    events = {(str(r["step"]), str(r["event"])) for r in rows}
    extra = {e for e in events if e[0] not in dict(PREP_STEPS)}
    assert extra - NOTES <= {(a, e) for a in CLEAN_PASS for e in ("start", "done")}
    walk = [
        r for r in rows if r["step"] in CLEAN_PASS and r["event"] in ("start", "done")
    ]
    assert [(r["step"], r["event"]) for r in walk] == [
        (a, e) for a in CLEAN_PASS for e in ("start", "done")
    ]
    assert str(walk[-1]["detail"]).endswith("→ committed"), walk[-1]


def _role_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    env = {**ROLE_ENV, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        argv, env=env, capture_output=True, text=True, check=False, timeout=120
    )


def test_installed_python_has_no_tk_but_core_works(root_world: RootWorld) -> None:
    """The installed release's Python cannot import Tcl/Tk modules; `ssl`,
    `sqlite3`, `ctypes`, `venv` (a real environment is created and runs) and
    the pin's entrypoint still work."""
    assert root_world.bootstrap(root_world.init_argv()) == 0
    lay = root_world.layout
    snap = state.parse_snapshot(state.read_raw(lay), lay, mode="reader")
    assert snap.release_id is not None
    bin_dir = lay.release_dir(snap.release_id) / "venv" / PIN_NAME / "bin"
    python = str(bin_dir / "python")
    for module in NO_TK:
        done = _role_run([python, "-I", "-c", f"import {module}"])
        assert done.returncode != 0, module
        assert "ModuleNotFoundError" in done.stderr, (module, done.stderr)
    core = _role_run([python, "-I", "-c", CORE])
    assert core.returncode == 0, core.stderr
    env_dir = root_world.top / "core-venv"
    made = _role_run([python, "-I", "-m", "venv", "--without-pip", str(env_dir)])
    assert made.returncode == 0, made.stderr
    used = _role_run([str(env_dir / "bin" / "python"), "-I", "-c", "import ssl"])
    assert used.returncode == 0, used.stderr
    probe = _role_run([str(bin_dir / "dfprobe"), "--help"])
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.startswith("usage: dfprobe"), probe.stdout
