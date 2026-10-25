"""Root acceptance of the install route on the real host (Task 13, R73).

§3.8 lines of the install group over the root world of
`tests/df/root_world.py`: a real `df-build`, the approved CLI, a real build
of the fixture pin, the admin Python from uv. Runs only in CI df-stage1.

Fresh worlds (`root_world`) come first: the interrupted and crashed
installs, the tampered release, the refused artifacts. The read-only tests
of one committed install (`installed_world`, R90) come last: a module-scoped
world keeps `df-build` alive, which a fresh world refuses as infrastructure.

Interruptions are the crash model of 13-D: an in-process `BaseException`
(`Killed`) that no handler of `df` catches, raised at the first
`fsutil.fault_point` inside an action (the window «visible, not durable»)
or right after an action; `finally` blocks still run, unlike a real death.
The retry is what an operator runs: the shipped entry's writer half
(`RootWorld.entry`) once the command is installed, else bootstrap `init`.

The retry is observed (`tests/df/observe.py`, as in 13-D): every action and
every classification read of the retry that starts with a builder tree on
disk — `releases/<id>` of `df-build` (via its holder `releases/`),
`staging/<id>` (via `staging/`), the build scratch in `df-build`'s home —
runs inside `observe_root_access`, and `build-dir` from the `fchown` on
(R93). Root's own ops on a holder are allowed (`RouteObserver.require_clean`),
every access under it is a violation; an `unobserved` observation fails the
test. Without `scope` (Linux): root's copy and digest of a whole release
under a scope rule overran the audit log (`ausearch` lost the markers, CI
run 40223595064) and a deleted tree's inode, reused by root's next file,
matched by identity; so on Linux only accesses under the watched holders
count (the observer's limit `identity-scope`). `settle`, the preparation
steps and `retry_quarantine` outside `drive` are not observed (they touch
no builder tree: 13-D observed the leaves).

The installed command runs as a real process (`RootWorld.command`): its
entry reads `<R>` (its `_ROOT`) and admits the admin code, but `df.cli`
then uses `default_layout()` — `/` — so its level-1 checks read the host;
level 1 of `<R>` is the in-process `doctor.level1_checks` (`_level1`).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import threading
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from df import accept, admin, boundary, cli, doctor, fsutil, install, state
from df import build as build_mod
from df import platform as plat
from df.boundary import (
    SYS_PATH_PROBE,
    env_paths,
    expected_entrypoints,
    static_problems,
    transfer_check,
)
from df.digest import check_links, release_digest, tree_digest
from df.journal import StepFailed, read_journal
from df.paths import Layout
from tests.df.conftest import stage1_out
from tests.df.install_world import CLEAN_PASS
from tests.df.observe import Observation, observe_root_access
from tests.df.root_world import (
    PIN_NAME,
    PYTHON,
    ROLE_ENV,
    Controls,
    InstalledWorld,
    RootWorld,
    assert_committed,
    build_account,
    current_id,
    infra,
    write_evidence,
)

pytestmark = pytest.mark.root

PLATFORM = plat.detect_platform()
#: Steps 1-3 of a fresh install, as `run_steps` journals them.
PREP_STEPS = [("manifest", "skip"), ("trusted-paths", "done"), ("build-user", "done")]
#: The only other journal step of a clean run: `drive`'s `sync-note`.
OTHER_STEPS = ("drive",)
#: Events of an action besides start/done in a clean run (Task 7a).
NOTES = {("verify", "distributions-note"), ("drive", "sync-note")}
#: Root's own ops on a holder directory (13-D): opened, or listed (quarantine).
OPEN_OPS = frozenset({"open", "openat"})
LIST_OPS = OPEN_OPS | {"readdir", "getdents", "getdents64"}


#: Removed with Tcl/Tk (owner decision 25.10, spec 3.4).
NO_TK = ("tkinter", "_tkinter", "turtle", "turtledemo", "idlelib")
#: Core capabilities that must survive the removal.
CORE = (
    "import ctypes, sqlite3, ssl, venv; ssl.create_default_context(); "
    "sqlite3.connect(':memory:').execute('select 1'); ctypes.CDLL(None)"
)


class Killed(BaseException):
    """The crash model (13-D): raised where the process is to die; no
    handler of `df` catches it, `finally` blocks still run."""


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


# ---------- shared helpers of the install group ----------


def _q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _label(request: pytest.FixtureRequest) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in request.node.name)


def _pending_id(layout: Layout) -> str:
    pending = install.read_pending(layout)
    assert pending is not None, "pending.toml нет после прерывания"
    return pending.release_id


def _done_steps(rows: list[dict[str, Any]]) -> list[str]:
    return [str(r["step"]) for r in rows if r["event"] == "done"]


def _as_build(
    script: str, cwd: Path, *, network: bool = False, timeout: float = 120
) -> plat.RunResult:
    """`/bin/sh -c script` as `df-build` (`launched` or infrastructure)."""
    result = plat.run_as(
        plat.BUILD_USER,
        ["/bin/sh", "-c", script],
        env=ROLE_ENV,
        cwd=cwd,
        network=network,
        timeout=timeout,
    )
    if not result.launched:
        infra(f"{plat.BUILD_USER} не запущен: {script[:80]}: {result}")
    return result


def _retry(world: RootWorld) -> int:
    """What the operator runs after a crash: the installed command's
    `install` once `current` and the command exist, else bootstrap `init`
    with the same configuration (spec 3.8: «повтор init»)."""
    lay = world.layout
    if os.path.lexists(lay.current) and os.path.lexists(lay.launcher):
        return world.entry(["install"])
    return world.bootstrap(world.init_argv())


@pytest.fixture()
def notes(request: pytest.FixtureRequest) -> Iterator[dict[str, object]]:
    """Facts of one test, kept as CI evidence (`install/<test>.json`)
    whatever its outcome (junit xunit2 drops `record_property`)."""
    facts: dict[str, object] = {}
    yield facts
    text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
    write_evidence(stage1_out(), f"install/{_label(request)}.json", text + "\n")


def _doctor_reached(world: RootWorld) -> None:
    """Level 1 of `<R>` (in process), and the installed command's `doctor
    --json` runs: its entry admits the admin code, `df.cli` reports, and
    the snapshot it was handed (from `<R>`) has no `pending.toml`."""
    checks = _level1(world)
    assert doctor.level1_reached(checks), [c for c in checks if c.status != "ok"]
    report = _command_report(world)
    pending = [c for c in report["checks"] if c["id"] == "install.pending"]
    assert [c["status"] for c in pending] == [doctor.OK], pending


def _command_report(world: RootWorld) -> dict[str, Any]:
    """`darkfactory doctor --json` of the installed command: a report
    (exit 0 or 2, the module doc says why not 0), nothing on stderr."""
    done = world.command(["doctor", "--json"])
    codes = (doctor.EXIT_REACHED, doctor.EXIT_NOT_REACHED)
    assert done.returncode in codes and done.stderr == "", (
        done.returncode,
        done.stderr[-2000:],
    )
    report = json.loads(done.stdout)
    assert {"level1", "checks", "models"} <= set(report), report
    return report


def _only_release(layout: Layout, rid: str) -> None:
    """Nothing of another id and no builder leftover: `rid` alone in
    `releases/`, `admin/` and the marks; `staging/` holds only an empty
    quarantine."""
    assert os.listdir(layout.releases) == [rid]
    assert os.listdir(layout.admin) == [rid]
    assert os.listdir(layout.marks_dir) == [f"{rid}.toml"]
    assert os.listdir(layout.staging) in ([], [".quarantine"])
    if os.path.isdir(layout.quarantine):
        assert os.listdir(layout.quarantine) == []


# ---------- the observed retry ----------


@dataclass
class Observed:
    """One observation of the retry and root's allowed ops on its holders:
    by path, and (Linux, `openat` by a held descriptor) by the relative
    name the kernel recorded."""

    phase: str
    obs: Observation
    holder: dict[Path, frozenset[str]]
    relative: dict[str, frozenset[str]]


@dataclass
class RouteObserver:
    """Observes the retry's actions and reads that meet a builder tree."""

    world: RootWorld
    label: str
    uid: int
    seen: list[Observed] = field(default_factory=list)

    def install(self, mp: pytest.MonkeyPatch) -> None:
        """Wrap every action and the classification read of the next run."""
        real_actions, real_read_for = install.install_actions, install.read_for

        def actions(
            run_: install.InstallRun,
        ) -> Mapping[str, Callable[[accept.Facts], None]]:
            out = dict(real_actions(run_))
            return {
                name: self._action(name, run_.release_id, act)
                for name, act in out.items()
            }

        def read_for(run_: install.InstallRun) -> Callable[[], accept.Facts]:
            read = real_read_for(run_)
            return lambda: self._call("read", run_.release_id, read)

        mp.setattr(install, "install_actions", actions)
        mp.setattr(install, "read_for", read_for)

    def _action(
        self, name: str, rid: str, act: Callable[[accept.Facts], None]
    ) -> Callable[[accept.Facts], None]:
        def observed(facts: accept.Facts) -> None:
            self._call(name, rid, lambda: act(facts))

        return observed

    def _call(self, phase: str, rid: str, fn: Callable[[], Any]) -> Any:
        sources, holder, relative = self.sources(rid)
        if not sources and phase != "build-dir":
            return fn()
        tag = f"{self.label}-{len(self.seen):02d}-{phase}"
        out = stage1_out()
        record = None if out is None else out / "observe" / f"{tag}.json"
        with observe_root_access(
            sources,
            control=self.world.control_dir(),
            workdir=self.world.workdir(),
            scope=None,  # route volume: see the module doc
            record=record,
        ) as obs:
            try:
                if phase != "build-dir":
                    return fn()
                with pytest.MonkeyPatch.context() as mp:  # R93: from the fchown
                    mp.setattr(fsutil, "fault_point", _watch_at_chown(obs))
                    return fn()
            finally:
                self.seen.append(Observed(tag, obs, holder, relative))

    def sources(
        self, rid: str
    ) -> tuple[list[Path], dict[Path, frozenset[str]], dict[str, frozenset[str]]]:
        """The builder trees on disk now, each through its holder: root's
        directories `releases/`, `staging/`, `staging/<id>` and the
        quarantine (opened or listed: none is under a source, spec 3.8),
        `df-build`'s home (opened: `sudo`'s cwd on macOS, 13-D)."""
        lay = self.world.layout
        sources: list[Path] = []
        holder: dict[Path, frozenset[str]] = {}
        relative: dict[str, frozenset[str]] = {}
        try:
            st = os.lstat(lay.release_dir(rid))
        except FileNotFoundError:
            st = None
        builder_release = st is not None and st.st_uid == self.uid
        if builder_release:
            sources.append(lay.releases)
            holder[lay.releases] = LIST_OPS
        box = lay.staging_dir(rid)
        quarantine = lay.quarantine
        quarantined = os.path.isdir(quarantine) and bool(os.listdir(quarantine))
        if os.path.lexists(lay.staging_tree(rid)) or quarantined:
            sources.append(lay.staging)
            holder |= {lay.staging: LIST_OPS, box: LIST_OPS, quarantine: LIST_OPS}
            if not builder_release:  # then `<id>` under `staging/` is the box
                relative[rid] = LIST_OPS
        home = lay.build_home
        if any(os.path.lexists(home / name) for name in ("tmp", "cache")):
            sources.append(home)
            holder[home] = OPEN_OPS
        return sources, holder, relative

    def require_clean(self) -> None:
        """Every observation `observed`, no access but a holder's own op."""
        for item in self.seen:
            obs = item.obs
            if obs.status != "observed":
                pytest.fail(f"наблюдатель: unobserved ({item.phase}) — {obs.reason}")
            allowed = {str(path): ops for path, ops in item.holder.items()}
            allowed |= item.relative
            bad = [a for a in obs.violations if a.op not in allowed.get(a.path, ())]
            assert bad == [], (item.phase, bad)


def _watch_at_chown(obs: Observation) -> Callable[[str, Path], None]:
    def hook(kind: str, path: Path) -> None:
        if kind == "chown":
            obs.watch(path)

    return hook


def _observed_retry(
    world: RootWorld, label: str, retry: Callable[[], int]
) -> tuple[int, RouteObserver]:
    """`retry()` with its builder-tree phases observed."""
    account = build_account(world.layout)
    world.control_dir()  # made by df-build before the run (R92)
    observer = RouteObserver(world, label, account.uid)
    with pytest.MonkeyPatch.context() as mp:
        observer.install(mp)
        code = retry()
    return code, observer


# ---------- rerun after an interruption inside each action ----------


def _interrupt_inside(mp: pytest.MonkeyPatch, action: str) -> list[tuple[str, str]]:
    """The next run of `action` dies at the first fault point inside it;
    returns the (kind, path) it died at (one-shot)."""
    real = install.install_actions
    hits: list[tuple[str, str]] = []

    def actions(run_: install.InstallRun) -> Mapping[str, Callable[..., None]]:
        out = dict(real(run_))
        inner = out[action]

        def act(facts: accept.Facts) -> None:
            if hits:
                inner(facts)
                return

            def kill(kind: str, path: Path) -> None:
                hits.append((kind, str(path)))
                raise Killed(f"{action}: {kind} {path}")

            with pytest.MonkeyPatch.context() as fp:
                fp.setattr(fsutil, "fault_point", kill)
                inner(facts)

        out[action] = act
        return out

    mp.setattr(install, "install_actions", actions)
    return hits


@pytest.mark.parametrize("action", CLEAN_PASS)
def test_rerun_after_interrupt(
    root_world: RootWorld,
    action: str,
    request: pytest.FixtureRequest,
    notes: dict[str, object],
) -> None:
    """Spec 3.8: `init` killed inside each action (at its first fault
    point), then the retry completes the install with the same id: nothing
    failed, nothing of another id or builder leftover, a partial copy not
    accepted (the retry copies again), a registered release kept, level 1
    by the installed command's own `doctor`. The retry's phases over a
    builder tree are observed with no root access to it."""
    world = root_world
    lay = world.layout
    with pytest.MonkeyPatch.context() as mp:
        hits = _interrupt_inside(mp, action)
        with pytest.raises(Killed):
            world.bootstrap(world.init_argv())
    assert len(hits) == 1, f"{action}: внутри действия нет точки сбоя"
    notes["killed_at"] = f"{hits[0][0]} {hits[0][1]}"
    rid = _pending_id(lay)
    rows = read_journal(lay.journal)
    mine = [r["event"] for r in rows if r["step"] == action]
    assert mine == ["start"], mine
    since = len(rows)
    code, observer = _observed_retry(world, _label(request), lambda: _retry(world))
    notes["observations"] = [o.phase for o in observer.seen]
    assert code == cli.EXIT_OK
    assert_committed(lay, since=since)
    assert current_id(lay) == rid
    _only_release(lay, rid)
    retried = _done_steps(read_journal(lay.journal)[since:])
    if action == "copy":
        assert {"copy", "copy-partial"} & set(retried), retried
    observer.require_clean()
    _doctor_reached(world)


# ---------- crash after activation ----------


def _kill_after(mp: pytest.MonkeyPatch, action: str) -> None:
    """The next run dies right after `action` returns (its journal `done`
    not written); one-shot."""
    real = install.install_actions
    armed = [True]

    def actions(run_: install.InstallRun) -> Mapping[str, Callable[..., None]]:
        out = dict(real(run_))
        inner = out[action]

        def act(facts: accept.Facts) -> None:
            inner(facts)
            if armed[0]:
                armed[0] = False
                raise Killed(f"after {action}")

        out[action] = act
        return out

    mp.setattr(install, "install_actions", actions)


@pytest.mark.parametrize("after", ["activate", "launcher"])
def test_crash_after_activation_real_fs(
    root_world: RootWorld, after: str, notes: dict[str, object]
) -> None:
    """Spec 3.8: a crash between `current`'s switch and `launcher` or
    `commit-config` -> the retry (bootstrap `init` without the command, the
    command's `install` with it) completes with the same id, the command is
    installed, and `pending.toml` goes last."""
    world = root_world
    lay = world.layout
    with pytest.MonkeyPatch.context() as mp:
        _kill_after(mp, after)
        with pytest.raises(Killed):
            world.bootstrap(world.init_argv())
    rid = _pending_id(lay)
    assert current_id(lay) == rid
    assert os.path.lexists(lay.launcher) == (after == "launcher")
    rows = read_journal(lay.journal)
    since = len(rows)
    points: list[tuple[str, Path]] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fsutil, "fault_point", lambda k, p: points.append((k, p)))
        assert _retry(world) == cli.EXIT_OK
    notes["retry"] = "darkfactory install" if after == "launcher" else "init"
    assert current_id(lay) == rid
    assert_committed(lay, since=since)
    assert install.command_ready(lay, rid)
    tail = [(k, p) for k, p in points if p != lay.generation]
    assert tail[-1] == ("unlinked", lay.pending), tail[-3:]
    if after == "activate":  # the command is written by the retry, before
        assert ("renamed", lay.launcher) in tail
    _only_release(lay, rid)
    _doctor_reached(world)


# ---------- tamper ----------


def test_tamper_after_install_doctor_broken(root_world: RootWorld) -> None:
    """Spec 3.8: a file of the installed release changed afterwards ->
    `doctor` (its level 1 of `<R>`, in process: module doc) reports
    `release.digest` «нарушено» (the release is damaged), level 1 not
    reached."""
    world = root_world
    assert world.bootstrap(world.init_argv()) == cli.EXIT_OK
    lay = world.layout
    rid = current_id(lay)
    assert rid is not None
    _doctor_reached(world)
    site = env_paths(lay.release_dir(rid), PIN_NAME).site
    target = lay.release_dir(rid) / site / "dfprobe.py"
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("# tampered\n")
    checks = {c.id: c for c in _level1(world)}
    assert not doctor.level1_reached(list(checks.values()))
    assert checks["release.digest"].status == doctor.BROKEN, checks
    assert doctor.DAMAGED in checks["release.digest"].reason


# ---------- refused artifacts (df-build changes its tree after the build) ----------


def _after_build(mp: pytest.MonkeyPatch, change: Callable[[Path], None]) -> None:
    """`df-build`'s last act of the next run's `build`: `change(release)`
    while `releases/<id>` is still its tree (before `seal`)."""
    real = install.install_actions

    def actions(run_: install.InstallRun) -> Mapping[str, Callable[..., None]]:
        out = dict(real(run_))
        inner = out["build"]

        def act(facts: accept.Facts) -> None:
            inner(facts)
            change(run_.layout.release_dir(run_.release_id))

        out["build"] = act
        return out

    mp.setattr(install, "install_actions", actions)


def _refused(world: RootWorld, change: Callable[[Path], None]) -> dict[str, Any]:
    """`init` with `change` after the build is refused at `verify`;
    nothing is registered or activated. Returns the `failed` row."""
    lay = world.layout
    with pytest.MonkeyPatch.context() as mp:
        _after_build(mp, change)
        assert world.bootstrap(world.init_argv()) == cli.EXIT_REFUSED
    failed = [r for r in read_journal(lay.journal) if r["event"] == "failed"]
    assert failed, "отказ не записан в журнал"
    assert not os.path.lexists(lay.current)
    assert not os.path.lexists(lay.etc / "releases.toml")
    return failed[-1]


#: Link name in the environment's root and its texts per case (`{LR}`: the
#: release's final path, `releases/<id>`).
DANGER = "danger"
LINK_TARGETS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "/etc": ((DANGER, "/etc"),),
    "../../..": ((DANGER, "../../.."),),
    "{LR}/../../../../etc/passwd": ((DANGER, "{LR}/../../../../etc/passwd"),),
    "chain": ((DANGER, "danger-2"), ("danger-2", "../../../../etc")),
}


@pytest.mark.parametrize("target", list(LINK_TARGETS))
def test_dangerous_link_in_artifact_refused(
    root_world: RootWorld, target: str, notes: dict[str, object]
) -> None:
    """Spec 3.8: a link of the artifact leaving the release — an absolute
    target outside, `..` beyond the root, the release's own prefix followed
    by `..`, an escape through a chain of links — is refused before
    registration, naming the link."""

    def plant(release: Path) -> None:
        env = release / "venv" / PIN_NAME
        lines = [
            f"ln -s {_q(text.replace('{LR}', str(release)))} {_q(env / name)}"
            for name, text in LINK_TARGETS[target]
        ]
        done = _as_build(" && ".join(lines), root_world.layout.build_home)
        if done.returncode != 0:
            infra(f"ссылка от {plat.BUILD_USER} не создана: {done}")

    failed = _refused(root_world, plant)
    notes["refusal"] = f"{failed['step']}: {failed['detail']}"[-2000:]
    assert failed["step"] == "verify", failed
    assert f"venv/{PIN_NAME}/{DANGER}" in str(failed["detail"]), failed


#: Each case's change of the entrypoint `dfprobe` by `df-build`.
ENTRYPOINT_CHANGES: Mapping[str, str] = {
    "missing": "rm -f {f}",
    # the head (direct shebang or sh trampoline) stays byte-exact; `--help`
    # then exits 3
    "broken-help": (
        "/usr/bin/perl -pi -e 's/sys\\.exit\\(main\\(\\)\\)/sys.exit(3)/' {f}"
        " && /usr/bin/grep -q 'sys.exit(3)' {f}"
    ),
}


@pytest.mark.parametrize("case", list(ENTRYPOINT_CHANGES))
def test_missing_entrypoint_and_broken_help_refused(
    root_world: RootWorld, case: str, notes: dict[str, object]
) -> None:
    """Spec 3.8: the expected entrypoint `dfprobe` absent (rule (д)), or a
    script with its direct `shebang` whose `--help` fails (rule (з)) ->
    refused at `verify`, naming it."""

    def change(release: Path) -> None:
        entry = release / "venv" / PIN_NAME / "bin" / "dfprobe"
        script = ENTRYPOINT_CHANGES[case].format(f=_q(entry))
        done = _as_build(script, root_world.layout.build_home)
        if done.returncode != 0:
            infra(f"entrypoint от {plat.BUILD_USER} не изменён: {done}")

    failed = _refused(root_world, change)
    notes["refusal"] = f"{failed['step']}: {failed['detail']}"[-2000:]
    assert failed["step"] == "verify", failed
    assert "dfprobe" in str(failed["detail"]), failed


# ---------- the installed world (R90): read-only tests, last ----------


def test_internal_absolute_links_survive_rename_and_copy(
    installed_world: InstalledWorld,
) -> None:
    """Spec 3.8: uv's absolute links into the release (built at its final
    path, moved by `seal`, copied by `tar`) are accepted verbatim: every
    absolute link text starts with the release's path, resolves inside it,
    and `check_links` finds nothing; the interpreter they name runs."""
    release = installed_world.release
    prefix = f"{release}/"
    absolute: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(release):
        for name in (*dirs, *files):
            path = Path(dirpath, name)
            if path.is_symlink() and os.readlink(path).startswith("/"):
                absolute[str(path.relative_to(release))] = os.readlink(path)
    env = env_paths(release, PIN_NAME)
    alias = f"python/{env.alias}"
    assert absolute.get(f"venv/{PIN_NAME}/bin/python") == (
        f"{release}/{alias}/bin/python3.{env.minor}"
    ), absolute
    assert absolute.get(alias) == f"{release}/{env.python}", absolute
    for rel, text in absolute.items():
        assert text.startswith(prefix), (rel, text)
        assert os.path.realpath(release / rel).startswith(prefix), rel
    assert check_links(release, str(release)) == []
    # as df-build: root's `-I` run would write bytecode into the release
    # (`-I` ignores PYTHONDONTWRITEBYTECODE; CI run 40223595064)
    python = release / env.bin / "python"
    done = _as_build(
        f"{_q(python)} -I -c 'print(1)'", installed_world.layout.build_home
    )
    assert (done.returncode, done.stdout) == (0, b"1\n"), done


def test_runs_after_build_dir_and_cache_removed(
    installed_world: InstalledWorld, installed_controls: Controls
) -> None:
    """Spec 3.8: the staging tree, `build_home/tmp` and `build_home/cache`
    are gone after the install, and the entrypoint still runs as `df-build`
    without network (`--help`, and a call importing the locked dependency)."""
    lay = installed_world.layout
    for path in (
        lay.staging_dir(installed_world.release_id),
        lay.build_home / "tmp",
        lay.build_home / "cache",
    ):
        assert not os.path.lexists(path), path
    entry = installed_world.release / "venv" / PIN_NAME / "bin" / "dfprobe"
    helped = _as_build(f"{_q(entry)} --help", lay.build_home)
    assert helped.returncode == 0, helped
    assert helped.stdout.startswith(b"usage: dfprobe"), helped.stdout
    called = _as_build(_q(entry), lay.build_home)
    assert called.returncode == 0, called
    assert called.stdout.startswith(b"dfprobe: python " + PYTHON.encode()), called


def test_boundary_on_real_release(
    installed_world: InstalledWorld, installed_controls: Controls
) -> None:
    """R40/F9, R82: the real `static_problems` and `transfer_check` (as
    `df-build`) pass on the release `build.py` made; the §3.4 facts this
    platform shows are kept as JSON evidence (`boundary/<platform>.json`).
    A fact that contradicts rule (в) — a library loaded through a link from
    another directory whose `$ORIGIN` then resolves outside the release —
    fails as a «находка»; the rest are measurements."""
    lay = installed_world.layout
    release = installed_world.release
    expected = {PIN_NAME: expected_entrypoints(release / "src" / PIN_NAME)}
    assert expected[PIN_NAME] == frozenset({"dfprobe"})
    assert static_problems(release, PLATFORM, expected) == []
    result = transfer_check(release, lay, user=plat.BUILD_USER, expected=expected)
    assert result.problems == []
    assert result.python_declared[PIN_NAME].startswith(PYTHON), result
    facts = BoundaryFacts(installed_world).collect()
    text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
    write_evidence(stage1_out(), f"boundary/{PLATFORM}.json", text + "\n")
    escapes = [c for c in facts["origin_via_link"] if c["escapes"]]
    assert escapes == [], f"находка (правило (в), $ORIGIN через ссылку): {escapes}"


def test_darkfactory_command_works_and_is_immutable(
    installed_world: InstalledWorld,
) -> None:
    """Spec 3.9: the installed `darkfactory doctor` and `models list` (real
    processes of root) work — the entry admits the admin code, `df.cli`
    answers; two runs of each change neither the release, the admin code,
    the admin Python nor the command files; level 1 of `<R>` holds."""
    world, lay = installed_world.world, installed_world.layout
    before = _digests(installed_world)
    for _ in range(2):
        _doctor_reached(world)
        listed = world.command(["models", "list"])
        assert listed.returncode == cli.EXIT_OK, (listed.stdout, listed.stderr)
        assert listed.stdout.startswith("роль"), listed.stdout
    assert _digests(installed_world) == before
    assert not list(lay.admin_dir(installed_world.release_id).rglob("__pycache__"))


def _digests(installed: InstalledWorld) -> dict[str, str]:
    lay = installed.layout
    return {
        "release": release_digest(installed.release),
        "admin": admin.admin_digest(lay, installed.release_id),
        "admin-python": tree_digest(lay.admin_python, exclude=frozenset()),
        "bin": tree_digest(lay.bin_dir, exclude=frozenset()),
    }


def test_no_df_in_path(installed_world: InstalledWorld) -> None:
    """R84: the command is `darkfactory` only — `<R>/opt/df/bin` holds it and
    its entry, `<R>/usr/local/bin` only its link; no `df` anywhere; the
    runner's real `/opt/df` and `/usr/local/bin/{df,darkfactory}` absent."""
    lay = installed_world.layout
    assert sorted(os.listdir(lay.bin_dir)) == ["darkfactory", "darkfactory-entry.py"]
    assert os.listdir(lay.local_bin) == ["darkfactory"]
    assert os.readlink(lay.local_bin / "darkfactory") == str(lay.launcher)
    for path in ("/opt/df", "/usr/local/bin/df", "/usr/local/bin/darkfactory"):
        assert not os.path.lexists(path), path


#: A well-formed `.tar.gz` that is not the Python uv expects (test_admin).
def _wrong_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"not python\n"
        info = tarfile.TarInfo("python/README")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@contextlib.contextmanager
def _mirror(body: bytes) -> Iterator[tuple[str, list[str]]]:
    """A local `UV_PYTHON_INSTALL_MIRROR` answering every path with `body`."""
    paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Quiet."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
    finally:
        server.shutdown()
        server.server_close()


def test_admin_python_on_real_host(installed_world: InstalledWorld) -> None:
    """Task 8a F1/F7/F8, R82: the install's real `install_admin_python` (as
    root) left exactly the key `build.python_key` (on Linux: the build uv
    chose matches the key), its whole tree passes the check, fact `A` holds
    and the route ran `admin` once; after the rename `sys.prefix` is
    `admin-python/<key>` and no link points into `.staging`. A corrupt
    download through a local mirror is refused by uv's hash check, nothing
    installed. Facts kept as JSON evidence (`admin-python/<platform>.json`)."""
    lay, rid = installed_world.layout, installed_world.release_id
    key = build_mod.python_key(PYTHON, PLATFORM)
    final = lay.admin_python / key
    names = sorted(os.listdir(lay.admin_python))
    assert key in names and ".staging" not in names, names
    assert admin.admin_tree_problems(final, final) == []
    assert admin.admin_python_problems(lay, PYTHON, PLATFORM) == []
    entry = accept.registry_entry(lay, rid)
    assert entry is not None and entry.admin_python == PYTHON
    assert admin.admin_ready(
        lay, rid, expected_admin_digest=entry.admin_digest, expected_python=PYTHON
    )
    rows = read_journal(lay.journal)
    assert [r["event"] for r in rows if r["step"] == "admin"] == ["start", "done"]
    interpreter = admin.admin_python_path(lay, PYTHON, PLATFORM)
    code = (
        "import json, sys, sysconfig; print(json.dumps([sys.prefix, "
        "sys.base_prefix, sys.version, sysconfig.get_platform()]))"
    )
    out = subprocess.run(
        [str(interpreter), "-I", "-B", "-S", "-c", code],
        env=dict(ROLE_ENV),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    prefix, base, version, platform_tag = json.loads(out)
    assert prefix == base == str(final)
    assert version.startswith(PYTHON)
    links = _links_into(lay.admin_python, lay.admin_python / ".staging")
    assert links == [], links
    refusal = _corrupt_download_refused(installed_world)
    facts = {
        "key": key,
        "admin_python_listing": names,
        "sys_prefix": prefix,
        "sys_version": version,
        "sysconfig_platform": platform_tag,
        "mirror_refusal": refusal[-1500:],
    }
    text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
    write_evidence(stage1_out(), f"admin-python/{PLATFORM}.json", text + "\n")


def _links_into(top: Path, forbidden: Path) -> list[str]:
    """Links under `top` whose target (normalised) is under `forbidden`."""
    found: list[str] = []
    for dirpath, dirs, files in os.walk(top):
        for name in (*dirs, *files):
            path = Path(dirpath, name)
            if path.is_symlink():
                target = os.path.normpath(path.parent / os.readlink(path))
                if Path(target).is_relative_to(forbidden):
                    found.append(f"{path} -> {os.readlink(path)}")
    return found


def _corrupt_download_refused(installed: InstalledWorld) -> str:
    """The real `install_admin_python` into a scratch layout with uv's
    download mirror on a local server serving a wrong archive: refused
    (`StepFailed("admin")`, «Hash mismatch»), nothing staged or installed."""
    top = installed.world.top / f"mirror-{installed.layout.root.name}"
    os.mkdir(top, 0o755)
    scratch = Layout(Path(os.path.realpath(top)))
    for path in (scratch.admin_python, scratch.install_dir):
        os.makedirs(path, 0o755)
    key = build_mod.python_key(PYTHON, PLATFORM)
    real_env = admin.admin_env
    try:
        with _mirror(_wrong_archive()) as (url, paths):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    admin,
                    "admin_env",
                    lambda lay: {**real_env(lay), "UV_PYTHON_INSTALL_MIRROR": url},
                )
                with pytest.raises(StepFailed) as exc:
                    admin.install_admin_python(
                        scratch, PYTHON, uv=installed.world.uv, platform=PLATFORM
                    )
        assert exc.value.step == "admin"
        assert "Hash mismatch" in str(exc.value), exc.value
        assert paths, "зеркало не опрошено"
        assert not os.path.lexists(scratch.admin_python / key)
        assert not os.path.lexists(scratch.admin_python / ".staging" / key)
        return str(exc.value)
    finally:
        shutil.rmtree(top, ignore_errors=True)


#: `target` -> (directory of the control object, its real counterpart whose
#: mode it copies): the release, the journal, `/etc/df`, the admin code.
def _control_places(installed: InstalledWorld) -> Mapping[str, tuple[Path, Path]]:
    lay, rid = installed.layout, installed.release_id
    entry = installed.release / "venv" / PIN_NAME / "bin" / "dfprobe"
    cli_py = lay.admin_dir(rid) / "df" / "cli.py"
    return {
        "release": (entry.parent, entry),
        "journal": (lay.install_dir, lay.journal),
        "etc": (lay.etc, lay.etc / "install.toml"),
        "admin": (cli_py.parent, cli_py),
    }


#: Each operation as `df-build`: `{d}` the directory, `{f}` the object.
OPERATIONS: Mapping[str, str] = {
    "create": ": > {d}/df-build-new",
    "write": "printf x >> {f}",
    "rename": "/bin/mv {f} {f}.moved",
    "delete": "/bin/rm -f {f}",
    "chmod": "/bin/chmod 0666 {f}",
}
CONTROL_TEXT = b"control\n"


@pytest.mark.parametrize("op", list(OPERATIONS))
@pytest.mark.parametrize("target", ["release", "journal", "etc", "admin"])
def test_build_user_cannot_modify(
    installed_world: InstalledWorld,
    installed_controls: Controls,
    target: str,
    op: str,
) -> None:
    """Spec 3.8 «Границы»: on a control object the test makes (root's, the
    mode of its real neighbour) in the release, beside the journal, in
    `/etc/df` and in the admin code, `df-build` cannot create, write,
    rename, delete or change modes; the same operation on its own file in
    its home passes (the paired positive control)."""
    lay = installed_world.layout
    directory, model = _control_places(installed_world)[target]
    control = directory / f"df-control-{op}"
    fd = os.open(control, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, CONTROL_TEXT)
        os.fchmod(fd, stat.S_IMODE(os.lstat(model).st_mode))
    finally:
        os.close(fd)
    before = os.lstat(control)
    try:
        script = OPERATIONS[op].format(d=_q(directory), f=_q(control))
        denied = _as_build(script, lay.build_home)
        assert denied.returncode != 0, denied
        after = os.lstat(control)
        assert (after.st_ino, after.st_mode, after.st_uid) == (
            before.st_ino,
            before.st_mode,
            before.st_uid,
        )
        assert control.read_bytes() == CONTROL_TEXT
        assert not os.path.lexists(directory / "df-build-new")
        assert not os.path.lexists(f"{control}.moved")
    finally:
        for leftover in (control, Path(f"{control}.moved"), directory / "df-build-new"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(leftover)
    _paired_operation_passes(lay, op)


def _paired_operation_passes(lay: Layout, op: str) -> None:
    """The same operation on `df-build`'s own file in its home succeeds."""
    home = lay.build_home
    own = home / f"pair-{op}"
    made = _as_build(f"printf own > {_q(own)}", home)
    assert made.returncode == 0, made
    script = OPERATIONS[op].format(d=_q(home), f=_q(own))
    try:
        done = _as_build(script, home)
        assert done.returncode == 0, done
        effect = {
            "create": os.path.lexists(home / "df-build-new"),
            "write": own.exists() and own.read_bytes() == b"ownx",
            "rename": os.path.lexists(f"{own}.moved"),
            "delete": not os.path.lexists(own),
            "chmod": own.exists() and stat.S_IMODE(os.lstat(own).st_mode) == 0o666,
        }[op]
        assert effect, op
    finally:
        cleanup = (
            f"/bin/rm -f {_q(own)} {_q(f'{own}.moved')} {_q(home / 'df-build-new')}"
        )
        _as_build(cleanup, home)


def test_entrypoint_has_no_network(
    installed_world: InstalledWorld, installed_controls: Controls
) -> None:
    """Spec 3.8: the release's interpreter as `df-build` reaches the control
    address with `network=True` and gets a connection error with
    `network=False` (the paired positive control is the first run)."""
    python = installed_world.release / "venv" / PIN_NAME / "bin" / "python"
    code = (
        "import sys, urllib.request\n"
        "try:\n"
        f"    r = urllib.request.urlopen({installed_controls.url!r}, timeout=10)\n"
        "    print(r.status)\n"
        "except OSError as exc:\n"
        "    print(type(exc).__name__, exc)\n"
        "    sys.exit(7)\n"
    )
    home = installed_world.layout.build_home
    argv = [str(python), "-I", "-c", code]
    reached = plat.run_as(
        plat.BUILD_USER, argv, env=ROLE_ENV, cwd=home, network=True, timeout=60
    )
    assert reached.launched and reached.returncode == 0, reached
    assert reached.stdout.strip() == b"200", reached.stdout
    cut = plat.run_as(
        plat.BUILD_USER, argv, env=ROLE_ENV, cwd=home, network=False, timeout=60
    )
    assert cut.launched and cut.returncode == 7, cut
    assert cut.stdout.startswith(b"URLError"), cut.stdout


# ---------- §3.4 facts of the real release (R82) ----------

#: A wheel with one console script, for uv's shebang form by path length.
WHEEL_NAME = "thr-0.0.0-py3-none-any.whl"
#: The longest venv interpreter path probed for the trampoline switch: the
#: venv's own files stay under macOS `PATH_MAX` (1024; CI run 40222369099).
THRESHOLD_MAX = 900
#: A wheel with bundled native libraries (manylinux `pillow.libs`, macOS
#: `PIL/.dylibs`); cffi 1.17.1 bundles none (CI run 40223595064).
NATIVE_WHEEL = "pillow==11.0.0"


@dataclass
class BoundaryFacts:
    """The §3.4 measurements of one installed release (R82): data only."""

    installed: InstalledWorld

    @property
    def release(self) -> Path:
        return self.installed.release

    def collect(self) -> dict[str, Any]:
        env = env_paths(self.release, PIN_NAME)
        scratch = (
            self.installed.world.top / f"measure-{self.installed.layout.root.name}"
        )
        os.mkdir(scratch, 0o755)
        try:
            return {
                "platform": PLATFORM,
                "layout": self._layout(env),
                "interpreter": _deps(self.release / env.interpreter),
                "native_with_search_paths": self._natives(),
                "origin_via_link": self._origin_via_link(),
                "sys_path": _measure(lambda: self._sys_path(env)),
                "libpython_pth": _measure(lambda: self._pth(env, scratch)),
                "trampoline": _measure(lambda: self._trampoline(env, scratch)),
                "native_wheel": _measure(lambda: self._native_wheel(env, scratch)),
            }
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _layout(self, env: Any) -> dict[str, Any]:
        rel = self.release
        bin_dir = rel / env.bin
        names = ("python", "python3", f"python3.{env.minor}")
        return {
            "python": sorted(os.listdir(rel / "python")),
            "python_key": sorted(os.listdir(rel / env.python)),
            "python_key_bin": sorted(os.listdir(rel / env.py_bin)),
            "python_key_lib_lib": os.path.lexists(rel / env.lib / "lib"),
            "libpython": sorted(
                n for n in os.listdir(rel / env.lib) if n.startswith("libpython")
            ),
            "venv_bin_links": {
                n: os.readlink(bin_dir / n) for n in names if (bin_dir / n).is_symlink()
            },
            "alias_link": os.readlink(rel / "python" / env.alias),
            "pyvenv_cfg": (rel / "venv" / PIN_NAME / "pyvenv.cfg").read_text(),
            "entrypoint_head": _head_lines(bin_dir / "dfprobe", 3),
        }

    def _natives(self) -> dict[str, Any]:
        """Every Mach-O/ELF with a search path: its search paths and needs."""
        found: dict[str, Any] = {}
        count = 0
        for path in _native_files(self.release):
            count += 1
            deps = _deps(path)
            if deps.get("rpaths"):
                found[str(path.relative_to(self.release))] = deps
        return {"count": count, "with_search_paths": found}

    def _origin_via_link(self) -> list[dict[str, Any]]:
        """Links to a native file in another directory whose search paths
        name the file's own directory: glibc substitutes the directory of
        the path it loaded (the link's), rule (в) the real one (nit 5)."""
        out: list[dict[str, Any]] = []
        prefix = f"{self.release}/"
        for link in _links(self.release):
            real = Path(os.path.realpath(link))
            if not str(real).startswith(prefix) or not _is_native(real):
                continue
            if real.parent == link.parent:
                continue
            deps = plat.linked_libraries(real)
            if deps is None:
                continue
            tokens = ("$ORIGIN", "${ORIGIN}", "@loader_path")
            origin = [r for r in deps.rpaths if any(t in r for t in tokens)]
            if not origin:
                continue
            found = _resolve(deps.needed, origin, link.parent)
            outside = [
                p
                for p in found.values()
                if p is not None
                and not p.startswith((prefix, "system:"))
                and not plat.is_system_path(Path(p), PLATFORM)
            ]
            out.append(
                {
                    "link": str(link.relative_to(self.release)),
                    "target": str(real.relative_to(self.release)),
                    "rpaths": list(origin),
                    "via_link_dir": found,
                    "escapes": outside,
                }
            )
        return out

    def _sys_path(self, env: Any) -> dict[str, Any]:
        python = self.release / env.bin / "python"
        home = self.installed.layout.build_home
        runs: dict[str, Any] = {}
        for flag in ("-P", "-I"):
            done = plat.run_as(
                plat.BUILD_USER,
                [str(python), flag, "-c", SYS_PATH_PROBE],
                env=ROLE_ENV,
                cwd=home,
                network=False,
                timeout=60,
            )
            runs[flag] = json.loads(done.stdout) if done.returncode == 0 else str(done)
        return runs

    def _pth(self, env: Any, scratch: Path) -> dict[str, Any]:
        """A copy of `python/<key>` with `<libpython>._pth` beside the
        library the interpreter needs: does CPython read it (its `sys.path`
        replaced, so the start fails)? Run as `df-build`."""
        deps = plat.linked_libraries(self.release / env.interpreter)
        needed = [] if deps is None else [n for n in deps.needed if "libpython" in n]
        if not needed:
            return {"libpython_linked": False}
        copy = scratch / "pth-copy"
        shutil.copytree(self.release / env.python, copy, symlinks=True)
        interp = copy / "bin" / f"python3.{env.minor}"
        probe = [str(interp), "-I", "-c", "import sys; print(sys.path)"]
        before = _as_build_argv(probe, scratch)
        pth = copy / "lib" / f"{Path(needed[0]).name}._pth"
        pth.write_text("/df-pth-marker\n")
        after = _as_build_argv(probe, scratch)
        return {
            "libpython_linked": True,
            "needed": needed,
            "pth": str(pth.relative_to(copy)),
            "without": _brief(before),
            "with": _brief(after),
            "read": "/df-pth-marker" in after.stdout.decode("utf-8", "replace")
            or after.returncode != before.returncode,
        }

    def _trampoline(self, env: Any, scratch: Path) -> dict[str, Any]:
        """uv's script form by the length of the venv interpreter's path:
        the longest that is still a direct `#!` and the shortest that is the
        sh trampoline (binary search, as `df-build`, offline)."""
        work = _builder_dir(scratch / "thr")
        wheel = scratch / WHEEL_NAME
        wheel.write_bytes(_script_wheel())
        base = self.release / env.interpreter
        lo = len(str(work / "l0000" / "bin" / "python")) + 2
        hi = THRESHOLD_MAX
        forms = {lo: self._form(work, wheel, base, lo)}
        forms[hi] = self._form(work, wheel, base, hi)
        if forms[lo] != "direct" or forms[hi] != "trampoline":
            return {"forms": forms, "threshold": None}
        while hi - lo > 1:
            mid = (lo + hi) // 2
            forms[mid] = self._form(work, wheel, base, mid)
            lo, hi = (mid, hi) if forms[mid] == "direct" else (lo, mid)
        return {"longest_direct": lo, "shortest_trampoline": hi, "forms": forms}

    def _form(self, work: Path, wheel: Path, base: Path, length: int) -> str:
        """The form of `thr` installed into a venv whose `bin/python` path
        is `length` bytes long."""
        venv = _padded(work / f"l{length:04d}", length - len("/bin/python"))
        if venv is None or len(f"{venv}/bin/python") != length:
            return f"ошибка: длина {length} не построена"
        uv, env = _q(self.installed.world.uv), _uv_env(work)
        script = (
            f"/bin/mkdir -p {_q(venv.parent)} && {env} {uv} venv -q --python"
            f" {_q(base)} {_q(venv)} && {env} {uv} pip install -q --offline"
            f" --no-index --python {_q(venv)}/bin/python {_q(wheel)}"
            f" && /usr/bin/head -n 1 {_q(venv)}/bin/thr"
        )
        done = _as_build(script, work)
        head = done.stdout.decode("utf-8", "replace").strip()
        if done.returncode != 0:
            return f"ошибка: {done.stderr[-300:]!r}"
        if head == f"#!{venv}/bin/python":
            return "direct"
        return "trampoline" if head == "#!/bin/sh" else f"иное: {head[:80]}"

    def _native_wheel(self, env: Any, scratch: Path) -> dict[str, Any]:
        """A wheel with bundled native libraries installed (network, as
        `df-build`) into a venv of the release's Python: each native file's
        search paths, where its needs resolve, and what rule (в) itself
        (`boundary._NativeCheck` over `site-packages`) says of them — core
        is not buildable in stage 1 (R85, df-manifest-profiles), so this is
        the sample of a manylinux/delocate wheel."""
        work = _builder_dir(scratch / "wheel")
        venv = work / "venv"
        uv = self.installed.world.uv
        uv_env = _uv_env(work)
        script = (
            f"{uv_env} {_q(uv)} venv -q --python {_q(self.release / env.interpreter)}"
            f" {_q(venv)} && {uv_env} {_q(uv)} pip install -q --only-binary :all:"
            f" --python {_q(venv)}/bin/python {_q(NATIVE_WHEEL)}"
        )
        done = _as_build(script, work, network=True, timeout=300)
        if done.returncode != 0:
            return {"error": done.stderr[-500:].decode("utf-8", "replace")}
        site = venv / "lib" / f"python3.{env.minor}" / "site-packages"
        natives: dict[str, Any] = {}
        for path in _native_files(site):
            deps = _deps(path)
            deps["resolved"] = _resolve(
                tuple(deps["needed"]), tuple(deps["rpaths"]), path.parent
            )
            natives[str(path.relative_to(site))] = deps
        check = boundary._NativeCheck(site, PLATFORM, None)  # noqa: SLF001
        rule_v = _measure(check.problems)
        return {"wheel": NATIVE_WHEEL, "natives": natives, "rule_v_problems": rule_v}


def _measure(fn: Callable[[], Any]) -> Any:
    """A measurement, or its error (a fact, never the test's verdict)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - recorded as the measurement
        return {"error": f"{type(exc).__name__}: {exc}"}


def _deps(path: Path) -> dict[str, Any]:
    deps = plat.linked_libraries(path)
    if deps is None:
        return {}
    return {
        "needed": list(deps.needed),
        "rpaths": list(deps.rpaths),
        "loader": deps.loader,
    }


def _is_native(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic == plat.ELF_MAGIC or magic in plat.MACHO_MAGIC


def _native_files(top: Path) -> Iterator[Path]:
    for dirpath, _dirs, files in os.walk(top):
        for name in sorted(files):
            path = Path(dirpath, name)
            if stat.S_ISREG(os.lstat(path).st_mode) and _is_native(path):
                yield path


def _links(top: Path) -> Iterator[Path]:
    for dirpath, dirs, files in os.walk(top):
        for name in (*dirs, *files):
            path = Path(dirpath, name)
            if path.is_symlink():
                yield path


def _resolve(
    needed: tuple[str, ...] | list[str],
    rpaths: tuple[str, ...] | list[str],
    origin: Path,
) -> dict[str, str | None]:
    """Where each need is found through `rpaths` with the file's directory
    token set to `origin`, else `None`; an absolute need is itself, or
    `system:` when only the macOS dyld cache holds it."""
    found: dict[str, str | None] = {}
    for name in needed:
        if name.startswith("/"):
            if os.path.exists(name):
                found[name] = name
            elif plat.is_system_path(Path(name), PLATFORM):
                found[name] = f"system:{name}"
            else:
                found[name] = None
            continue
        if name.startswith("@loader_path/"):
            candidate = os.path.normpath(origin / name.removeprefix("@loader_path/"))
            found[name] = candidate if os.path.exists(candidate) else None
            continue
        leaf = name.removeprefix("@rpath/")
        found[name] = None
        for rpath in rpaths:
            base = rpath
            for token in ("${ORIGIN}", "$ORIGIN", "@loader_path"):
                base = base.replace(token, str(origin))
            candidate = os.path.normpath(os.path.join(base, leaf))
            if os.path.exists(candidate):
                found[name] = candidate
                break
        if found[name] is None:
            for base in plat.LOADER_DIRS[PLATFORM]:
                if os.path.exists(os.path.join(base, leaf)):
                    found[name] = f"system:{os.path.join(base, leaf)}"
                    break
    return found


def _head_lines(path: Path, count: int) -> list[str]:
    with open(path, "rb") as handle:
        lines = handle.read(4096).split(b"\n")[:count]
    return [line.decode("utf-8", "replace") for line in lines]


def _as_build_argv(argv: list[str], cwd: Path) -> plat.RunResult:
    result = plat.run_as(
        plat.BUILD_USER, argv, env=ROLE_ENV, cwd=cwd, network=False, timeout=60
    )
    if not result.launched:
        infra(f"{plat.BUILD_USER} не запущен: {argv}: {result}")
    return result


def _brief(result: plat.RunResult) -> dict[str, Any]:
    return {
        "code": result.returncode,
        "stdout": result.stdout.decode("utf-8", "replace")[-600:],
        "stderr": result.stderr.decode("utf-8", "replace")[-600:],
    }


def _builder_dir(path: Path) -> Path:
    """A scratch directory of `df-build` outside its home (`transfer_check`
    refuses leftovers there)."""
    uid, gid = plat.whoami_as(plat.BUILD_USER)
    os.mkdir(path, 0o755)
    os.chown(path, uid, gid)
    return path


def _uv_env(work: Path) -> str:
    """`env -i` of the measurement's uv runs: no config, no downloads."""
    items = {
        "HOME": str(work),
        "UV_CACHE_DIR": str(work / "cache"),
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "PATH": "/usr/bin:/bin",
    }
    return "/usr/bin/env -i " + " ".join(_q(f"{k}={v}") for k, v in items.items())


def _padded(start: Path, length: int) -> Path | None:
    """`start` extended by components of at most 200 bytes to exactly
    `length` bytes; `None` when `start` is already longer."""
    name, remain = start.name, length - len(str(start))
    if remain < 0:
        return None
    parts: list[str] = []
    while remain > 201:
        parts.append("p" * 200)
        remain -= 201
    if remain == 1:
        name += "x"
    elif remain >= 2:
        parts.append("p" * (remain - 1))
    return start.parent.joinpath(name, *parts)


def _script_wheel() -> bytes:
    """`thr-0.0.0`: a module and the console script `thr = thr:main`."""
    files = {
        "thr/__init__.py": b"def main():\n    print('thr')\n",
        "thr-0.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: thr\nVersion: 0.0.0\n"
        ),
        "thr-0.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: df-acceptance\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "thr-0.0.0.dist-info/entry_points.txt": b"[console_scripts]\nthr = thr:main\n",
    }
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        record.append(f"{name},sha256={digest.decode()},{len(data)}")
    record.append("thr-0.0.0.dist-info/RECORD,,")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("thr-0.0.0.dist-info/RECORD", "\n".join(record) + "\n")
    return buf.getvalue()
