"""I9: a damaged release under an unfinished install (owner 23.10, R51-R53).

The guards of task-9-i9-design.md: G1 (`cli._writer`, before anything else
of a writer), G2 (`recover_pending`, the pending release registered and
damaged while `current` is the previous one), G3 (M9 closes an odd
generation only after G1) and G4 (the backstop of `run_install` reads its
own `current`). Every refusal is `damaged`, names the release id and the
remaining manual steps, never `doctor`, and changes nothing. The way out is
the manual procedure (delete `pending.toml`, unlink `current` only when
named, then bootstrap); after it the next install gets a NEW id and the
damaged release stays registered and inactive (R53).

Everything runs through the real `World.writer` / `World.bootstrap` (the
entry's admin check in-process for D-admin), as in `test_install_steps`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from df import accept, fsutil, install, journal, launcher_entry, release
from df import platform as plat
from df.config import ReleasesConfig, dump_install, dump_releases, load_releases
from df.journal import StepFailed, read_journal
from df.paths import Layout
from tests.df.conftest import FakeLayout
from tests.df.install_world import (
    FIRST,
    OTHER_SHA,
    PLATFORM,
    SECOND,
    SHA,
    World,
    committed_problems,
    current_id,
    files,
    installed,
    interrupt,
    make_world,
    registry,
)
from tests.df.route_world import BOOT

#: The second id of `SHA` (a reinstall's release, C7: asserted explicitly).
REINSTALL = f"{SHA[:12]}-{PLATFORM}-2"
THIRD = f"{SHA[:12]}-{PLATFORM}-3"
#: How the refusals name bootstrap (the command line of `df/bootstrap.py`).
BOOTSTRAP = "sudo <python ≥ 3.12> -I -B -S <чекаут devtools>/df/bootstrap.py"
#: Calls of steps 1-3, the checkouts and the build: none before a refusal.
NOT_BEFORE_REFUSAL = (
    "fetch_manifest",
    "approved_cli_problems",
    "fetch_references",
    "create_build_user",
    "run_as",
)


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def w(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> World:
    world = make_world(fake_layout, monkeypatch)
    real = plat.run_as

    def run_as(*a: object, **kw: object) -> object:
        world.calls.append(("run_as",))
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(plat, "run_as", run_as)
    return world


# ---------- states ----------


def damage(world: World, release_id: str, kind: str = "tree") -> None:
    """D-tree: a file in `releases/<id>`; D-admin: in `admin/<id>`; D-unreg:
    the registry entry removed (`cur` without `reg`)."""
    lay = world.lay
    if kind == "tree":
        (lay.release_dir(release_id) / "injected").write_text("x\n")
    elif kind == "admin":
        (lay.admin_dir(release_id) / "injected").write_text("x\n")
    else:
        path = lay.etc / "releases.toml"
        kept = tuple(e for e in load_releases(path).releases if e.id != release_id)
        path.write_text(dump_releases(ReleasesConfig(kept)))


def switched(world: World, state: str) -> str:
    """`<first|reinstall>-<A2|A4|A5>`: an unfinished install that already
    switched `current` to its release (design §1.1); returns that id. A2 is
    killed before `launcher`, A4 before `commit-config`, A5 inside it
    (`install.toml` written, `pending.toml` still there). A reinstall never
    runs `launcher` (the command is the same), so its A2 is its A4."""
    run, _, point = state.partition("-")
    action = "launcher" if point == "A2" else "commit-config"
    if run == "first":
        interrupt(world, world.bootstrap, world.init_argv(), action)
        rid = FIRST
    else:
        assert installed(world) == FIRST
        interrupt(world, world.writer, ["install"], action)
        rid = REINSTALL
    pending = install.read_pending(world.lay)
    assert pending is not None and pending.release_id == rid
    assert current_id(world) == rid
    if point == "A5":
        (world.lay.etc / "install.toml").write_text(pending.install_text)
    return rid


def run_id(world: World) -> str:
    pending = install.read_pending(world.lay)
    assert pending is not None
    return pending.run_id


def refused(
    world: World,
    capsys: pytest.CaptureFixture[str],
    run: Callable[[list[str]], int],
    argv: list[str],
) -> str:
    """`run(argv)` is a `damaged` refusal that changes nothing and reaches no
    step; returns the message."""
    capsys.readouterr()
    before = (files(world), current_id(world))
    world.calls.clear()
    assert run(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("darkfactory: damaged: "), err
    assert (files(world), current_id(world)) == before
    assert not [c for c in world.calls if c[0] in NOT_BEFORE_REFUSAL], world.calls
    assert_no_bad_advice(err)
    return err


def assert_no_bad_advice(text: str) -> None:
    """Never `doctor`, never «return current», the generation only as a
    prohibition."""
    assert "doctor" not in text
    assert "верните" not in text and "generation" not in text
    assert text.count("поколение") == text.count("поколение не правьте")


def boot(cmd: str) -> str:
    return f"{BOOTSTRAP} {cmd}"


def unlink_step(lay: Layout) -> str:
    return f"удалите ссылку: unlink {lay.current}"


# ---------- 5.1 refusals ----------

STATES = ["first-A2", "first-A4", "first-A5", "reinstall-A4", "reinstall-A5"]
PATHS = [
    "writer-install",
    "bootstrap-install",
    "bootstrap-init",
    "bootstrap-init-other",
]


@pytest.mark.parametrize("state", STATES)
def test_i9_switched_damaged_refused(
    w: World, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    """G1(a): A2/A4/A5 x D-tree, every writer path refuses before any change;
    `unlink` is named exactly for a first install before `commit-config`,
    `init` exactly when there is no `install.toml`. The state is built once
    and every path runs on it in turn: `refused` proves each changed
    nothing, so each path starts from the same state (m-4)."""
    rid = switched(w, state)
    damage(w, rid)
    lay = w.lay
    first_unlinked = state in ("first-A2", "first-A4")
    for path in PATHS:
        run = w.writer if path.startswith("writer") else w.bootstrap
        argv = {
            "writer-install": ["install"],
            "bootstrap-install": ["install"],
            "bootstrap-init": w.init_argv(),
            "bootstrap-init-other": w.init_argv(OTHER_SHA),
        }[path]
        err = refused(w, capsys, run, argv)
        assert f"релиз {rid}: повреждён" in err, path
        assert f"(run_id {run_id(w)}) уже переключила на него current" in err, path
        assert f"2) удалите {lay.pending}" in err, path
        assert (unlink_step(lay) in err) is first_unlinked, path
        assert (boot("init") in err) is first_unlinked, path
        assert (boot("install") in err) is not first_unlinked, path


def test_i9_switched_damaged_admin_entry_then_bootstrap(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A4 x D-admin: the entry refuses before the release's code («через
    bootstrap»); bootstrap then reaches G1(a)."""
    rid = switched(w, "first-A4")
    damage(w, rid, "admin")
    paths = launcher_entry.EntryPaths(str(w.lay.root))
    w.mp.setattr(launcher_entry, "ROOT_UID", os.geteuid())
    w.mp.setattr(launcher_entry, "owner_uid", w.fl.owner_of)
    fd = launcher_entry.acquire(paths)
    try:
        snap = launcher_entry.take_snapshot(paths, "writer")
        with pytest.raises(launcher_entry.Refused, match="через bootstrap"):
            launcher_entry.verified_admin_dir(paths, snap)
    finally:
        os.close(fd)
    err = refused(w, capsys, w.bootstrap, ["install"])
    assert "уже переключила на него current" in err


@pytest.mark.parametrize("pending", [True, False])
def test_i9_switched_unregistered(
    w: World, capsys: pytest.CaptureFixture[str], pending: bool
) -> None:
    """D-unreg (`cur` without `reg`): with `pending.toml` G1(a), without it
    G1(b); both name `unlink` (an unregistered `current` never agrees)."""
    rid = switched(w, "reinstall-A4")
    damage(w, rid, "unreg")
    if not pending:
        w.lay.pending.unlink()
    err = refused(w, capsys, w.bootstrap, ["install"])
    assert unlink_step(w.lay) in err and boot("install") in err
    if pending:
        assert "уже переключила на него current" in err
    else:
        assert f"релиз {rid}: повреждён (§3.8), current указывает на него" in err
        assert "а релиза нет в releases.toml" in err
        assert str(w.lay.pending) not in err


@pytest.mark.parametrize("sha", [SHA, OTHER_SHA])
def test_i9_bare_pending_deletion_first_install_refused(
    w: World, capsys: pytest.CaptureFixture[str], sha: str
) -> None:
    """H1 of a first install (probe P1): `pending.toml` deleted alone — G1(b)
    for `init` of either configuration. G3: M9 does not close the odd
    generation (no `generation_end`, no `warn`), the evidence stays."""
    rid = switched(w, "first-A2")
    damage(w, rid)
    w.lay.pending.unlink()
    gen = journal.read_generation(w.lay)
    assert gen % 2 == 1
    ends: list[object] = []
    real_end = journal.generation_end
    w.mp.setattr(
        journal, "generation_end", lambda lay: ends.append(lay) or real_end(lay)
    )
    err = refused(w, capsys, w.bootstrap, w.init_argv(sha))
    assert "а install.toml нет" in err
    assert unlink_step(w.lay) in err and boot("init") in err
    assert ends == [] and journal.read_generation(w.lay) == gen
    rows = read_journal(w.lay.journal)
    assert not [r for r in rows if r["step"] == "generation"]


def test_i9_bare_deletion_and_even_generation_first_install_refused(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """H1+H4: the generation made even by hand does not help — G1(b)."""
    rid = switched(w, "first-A2")
    damage(w, rid)
    w.lay.pending.unlink()
    journal.generation_end(w.lay)
    assert journal.read_generation(w.lay) % 2 == 0
    err = refused(w, capsys, w.bootstrap, w.init_argv())
    assert unlink_step(w.lay) in err


def switch_back(world: World, state: str) -> str:
    """H2 (probe P3): `current` returned by hand to the previous release (a
    first install: the link removed); returns the pending id."""
    rid = switched(world, state)
    os.unlink(world.lay.current)
    if state.startswith("reinstall"):
        os.symlink(f"releases/{FIRST}", world.lay.current)
    return rid


@pytest.mark.parametrize("state", ["reinstall-A4", "first-A4"])
def test_i9_switch_back_only_refused_early(
    w: World, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    """G2: `pending.toml` names its registered, damaged release while
    `current` is the previous one — refused before `generation_begin` and
    any step (no `fetch_references`), with G2's text."""
    rid = switch_back(w, state)
    damage(w, rid)
    argv = ["install"] if state.startswith("reinstall") else w.init_argv()
    err = refused(w, capsys, w.bootstrap, argv)
    assert f"релиз {rid} незавершённой установки (run_id {run_id(w)})" in err
    assert f"2) удалите {w.lay.pending}" in err
    assert unlink_step(w.lay) not in err
    if state.startswith("reinstall"):
        assert f"current = {FIRST} — прежний" in err
        assert "затем darkfactory install (если команда отказывает — " in err
    else:
        assert "current = нет — прежний" in err and boot("init") in err


def test_i9_registered_damaged_before_activate_refused_early(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G-pre: killed before `activate`, then the registered release damaged:
    the retry is G2, not the generic `drive` refusal after the network."""
    installed(w)
    interrupt(w, w.writer, ["install"], "activate")
    damage(w, REINSTALL)
    err = refused(w, capsys, w.writer, ["install"])
    assert f"релиз {REINSTALL} незавершённой установки" in err


def test_i9_models_set_refused_first(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G1 comes before the pending filter (`_FINISH`) and the dispatch stub."""
    rid = switched(w, "first-A2")
    damage(w, rid)
    err = refused(w, capsys, w.writer, ["models", "set", "x"])
    assert "уже переключила на него current" in err
    assert "завершите" not in err and "Task 10" not in err


def damage_after(world: World, action: str, release_id: Callable[[], str]) -> None:
    """The next run's `action` damages the release's tree right after it."""
    real = install.install_actions

    def actions(run: install.InstallRun) -> dict[str, Callable[..., None]]:
        out = dict(real(run))
        inner = out[action]

        def act(facts: object) -> None:
            inner(facts)
            damage(world, release_id())

        out[action] = act
        return out

    world.mp.setattr(install, "install_actions", actions)


def test_i9_backstop_uses_fresh_current(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G4: a new install activates its release, which is damaged in the same
    run: the refusal is G1(a)'s already in this run (the snapshot's
    `current` was none of it)."""
    damage_after(w, "activate", lambda: FIRST)
    assert w.bootstrap(w.init_argv()) == 2
    err = capsys.readouterr().err
    assert err.startswith("darkfactory: damaged: ")
    assert f"релиз {FIRST}: повреждён" in err
    assert "уже переключила на него current" in err
    assert unlink_step(w.lay) in err and boot("init") in err
    assert_no_bad_advice(err)


def test_i9_backstop_damage_during_resume(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G4: a resume intact at G1, the tree damaged inside `launcher`: G1(a)'s
    text in this run."""
    rid = switched(w, "first-A2")
    damage_after(w, "launcher", lambda: rid)
    assert w.writer(["install"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("darkfactory: damaged: ")
    assert f"(run_id {run_id(w)}) уже переключила на него current" in err
    assert "(остановлено: launcher: active → damaged)" in err  # nit 3
    assert_no_bad_advice(err)


def test_i9_backstop_before_activate_names_g2(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """G4, the other branch: the run's release damaged right after
    `register`, before `activate` -- `current` is not it, so G2's text
    (`current = нет`, no `unlink`)."""
    damage_after(w, "register", lambda: FIRST)
    assert w.bootstrap(w.init_argv()) == 2
    err = capsys.readouterr().err
    assert err.startswith("darkfactory: damaged: ")
    assert f"релиз {FIRST} незавершённой установки (run_id {run_id(w)})" in err
    assert "current = нет — прежний" in err and boot("init") in err
    assert "уже переключила" not in err and unlink_step(w.lay) not in err
    assert "(остановлено: " in err
    assert_no_bad_advice(err)


def fail_after(world: World, action: str, *, unreadable: bool = False) -> None:
    """The next run's `action` runs, then fails with `StepFailed(action,
    "x")`; with `unreadable`, it also damages the release and every later
    read of the facts fails."""
    real_actions, real_read = install.install_actions, install.read_for
    failed = [False]

    def actions(run: install.InstallRun) -> dict[str, Callable[..., None]]:
        out = dict(real_actions(run))
        inner = out[action]

        def act(facts: object) -> None:
            inner(facts)
            if unreadable:
                damage(world, run.release_id)
            failed[0] = True
            raise StepFailed(action, "x")

        out[action] = act
        return out

    def read_for(run: install.InstallRun) -> Callable[[], object]:
        read = real_read(run)

        def facts() -> object:
            if failed[0]:
                raise StepFailed("facts", "не читается")
            return read()

        return facts if unreadable else read

    world.mp.setattr(install, "install_actions", actions)
    world.mp.setattr(install, "read_for", read_for)


@pytest.mark.parametrize("unreadable", [False, True])
def test_i9_backstop_keeps_other_failures(
    w: World, capsys: pytest.CaptureFixture[str], unreadable: bool
) -> None:
    """I-1, the negative twin of G4's widening: an action fails on an intact
    release (or the facts cannot be read after it) -- the refusal is that
    step's own, never the damaged advice to delete `pending.toml`."""
    rid = switched(w, "first-A2")
    fail_after(w, "launcher", unreadable=unreadable)
    assert w.writer(["install"]) == 2
    err = capsys.readouterr().err
    assert err == "darkfactory: launcher: x\n", err
    assert os.path.lexists(w.lay.pending) and current_id(w) == rid


def test_i9_bare_deletion_first_install_other_config_refused(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """m-1, the prefix half of «согласован»: a first install's
    `pending.toml` deleted and `install.toml` written by hand for another
    sha -- registered, but not agreeing: G1(b)."""
    rid = switched(w, "first-A2")
    damage(w, rid)
    w.lay.pending.unlink()
    (w.lay.etc / "install.toml").write_text(dump_install(w.cfg(OTHER_SHA)))
    err = refused(w, capsys, w.bootstrap, ["install"])
    assert "а install.toml не согласован с ним" in err
    assert unlink_step(w.lay) in err and boot("install") in err


# ---------- 5.2 the procedure ----------


def assert_new_install(world: World, damaged: str, new: str, sha: str = SHA) -> None:
    """R53: committed on a NEW id; the damaged release registered, inactive."""
    assert new != damaged
    assert committed_problems(world, new, world.cfg(sha)) == []
    assert damaged in registry(world) and current_id(world) == new


def test_i9_procedure_reinstall(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """A4 x D-tree of a reinstall: the refusal names steps 1, 2, 4 only;
    after step 2 bootstrap installs a new id; both old releases stay."""
    rid = switched(w, "reinstall-A4")
    damage(w, rid)
    err = refused(w, capsys, w.bootstrap, ["install"])
    assert unlink_step(w.lay) not in err
    w.lay.pending.unlink()
    assert w.bootstrap(["install"]) == 0
    assert_new_install(w, rid, THIRD)
    assert FIRST in registry(w)


@pytest.mark.parametrize("state", ["first-A2", "first-A5"])
def test_i9_procedure_first_install(
    w: World, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    """A first install: steps 2, (3), 4 — `unlink` and `init` before
    `commit-config`; at A5 no `unlink` and `install`."""
    rid = switched(w, state)
    damage(w, rid)
    err = refused(w, capsys, w.bootstrap, w.init_argv())
    w.lay.pending.unlink()
    if state == "first-A2":
        assert unlink_step(w.lay) in err
        os.unlink(w.lay.current)
        assert w.bootstrap(w.init_argv()) == 0
    else:
        assert unlink_step(w.lay) not in err
        assert w.bootstrap(["install"]) == 0
    assert_new_install(w, rid, f"{SHA[:12]}-{PLATFORM}-2")


@pytest.mark.parametrize("state", ["reinstall-A4", "first-A4"])
def test_i9_procedure_after_switch_back(
    w: World, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    """H2 -> G2 -> step 2 -> the named command: a new id."""
    rid = switch_back(w, state)
    damage(w, rid)
    argv = ["install"] if state.startswith("reinstall") else w.init_argv()
    refused(w, capsys, w.bootstrap, argv)
    w.lay.pending.unlink()
    if state.startswith("reinstall"):
        assert w.writer(["install"]) == 0
        assert_new_install(w, rid, THIRD)
    else:
        assert w.bootstrap(w.init_argv()) == 0
        assert_new_install(w, rid, f"{SHA[:12]}-{PLATFORM}-2")


def test_i9_procedure_reentrant(w: World, capsys: pytest.CaptureFixture[str]) -> None:
    """After each single step bootstrap either refuses naming exactly what
    is left, or (after the last) installs; none lands in M10."""
    rid = switched(w, "first-A2")
    damage(w, rid)
    err = refused(w, capsys, w.bootstrap, w.init_argv())
    assert f"удалите {w.lay.pending}" in err and unlink_step(w.lay) in err
    w.lay.pending.unlink()
    err = refused(w, capsys, w.bootstrap, w.init_argv())
    assert str(w.lay.pending) not in err and unlink_step(w.lay) in err
    assert "pending: current =" not in err
    os.unlink(w.lay.current)
    assert w.bootstrap(w.init_argv()) == 0
    assert_new_install(w, rid, f"{SHA[:12]}-{PLATFORM}-2")


def test_i9_procedure_step_undone_by_os_crash(
    w: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 2 undone (the removal was not durable): the same G1(a) text."""
    rid = switched(w, "first-A4")
    damage(w, rid)
    first = refused(w, capsys, w.bootstrap, w.init_argv())
    saved = w.lay.pending.read_bytes()
    w.lay.pending.unlink()
    w.lay.pending.write_bytes(saved)
    assert refused(w, capsys, w.bootstrap, w.init_argv()) == first


# ---------- 5.3 negative twins: no wider than needed ----------


@pytest.mark.parametrize("kind", ["tree", "admin"])
def test_i9_normal_path_damaged_after_commit(w: World, kind: str) -> None:
    """§3.8: damaged after a committed install -> bootstrap, a new id."""
    installed(w)
    damage(w, FIRST, kind)
    assert w.bootstrap(["install"]) == 0
    assert_new_install(w, FIRST, REINSTALL)


def test_i9_normal_path_retry_between_generation_and_pending(w: World) -> None:
    """N-retry: the new install killed after `generation_begin`, before its
    `pending.toml` (odd, `current` damaged, no `pending`) -> the retry
    installs; breaks if G1 refuses on «odd and damaged»."""
    installed(w)
    damage(w, FIRST)
    real = install.new_pending

    def killed(*_a: object, **_k: object) -> object:
        raise KeyboardInterrupt("after generation_begin")

    w.mp.setattr(install, "new_pending", killed)
    with pytest.raises(KeyboardInterrupt):
        w.bootstrap(["install"])
    w.mp.setattr(install, "new_pending", real)
    assert journal.read_generation(w.lay) % 2 == 1
    assert not os.path.lexists(w.lay.pending)
    assert w.bootstrap(["install"]) == 0
    assert_new_install(w, FIRST, REINSTALL)


def test_i9_normal_path_retry_before_activate(w: World) -> None:
    """The new install killed before `activate` (`current` = the damaged
    previous release, not the pending one) -> the retry resumes; breaks if
    G1 fires on any damaged `current` under `pending.toml`."""
    installed(w)
    damage(w, FIRST)
    interrupt(w, w.bootstrap, ["install"], "activate")
    assert w.bootstrap(["install"]) == 0
    assert_new_install(w, FIRST, REINSTALL)


def test_i9_committed_odd_generation_damaged(w: World) -> None:
    """A6 x D-tree: committed, the generation left odd, the release damaged
    -> bootstrap installs; M9's `warn` names `current`."""
    installed(w)
    journal.generation_begin(w.lay)
    damage(w, FIRST)
    assert w.bootstrap(["install"]) == 0
    rows = read_journal(w.lay.journal)
    warns = [r for r in rows if r["step"] == "generation" and r["event"] == "warn"]
    assert len(warns) == 1 and f"current = {FIRST}" in str(warns[0]["detail"])
    assert_new_install(w, FIRST, REINSTALL)


def test_i9_bare_deletion_reinstall_proceeds(w: World) -> None:
    """R53 (option (а)): a reinstall's `pending.toml` deleted alone (H1, probe
    P2) is the manual unlock: bootstrap installs a NEW id; the damaged one
    is never activated or accepted again and stays registered, inactive."""
    rid = switched(w, "reinstall-A4")
    assert rid == REINSTALL
    damage(w, rid)
    entry = registry(w)[rid]
    w.lay.pending.unlink()
    activated: list[str] = []
    driven: list[str] = []
    real_activate, real_drive = release.activate, accept.drive
    w.mp.setattr(
        release,
        "activate",
        lambda lay, i: activated.append(i) or real_activate(lay, i),
    )
    w.mp.setattr(
        accept,
        "drive",
        lambda lay, i, **kw: driven.append(i) or real_drive(lay, i, **kw),
    )
    assert w.bootstrap(["install"]) == 0
    assert activated == [THIRD] and driven == [THIRD]
    assert_new_install(w, rid, THIRD)
    assert registry(w)[rid] == entry


@pytest.mark.parametrize("state", ["first-A2", "first-A4"])
def test_i9_resume_intact_unaffected(w: World, state: str) -> None:
    """No damage: the resume continues the same id."""
    rid = switched(w, state)
    assert w.writer(["install"]) == 0
    assert committed_problems(w, rid, w.cfg()) == []


def test_i9_rollback_other_config_unaffected(w: World) -> None:
    """G-pre of a first install, `init` of another sha: the rollback (G2 is
    only the same-configuration branch), then a new id; the damaged release
    stays registered, inactive."""
    interrupt(w, w.bootstrap, w.init_argv(), "activate")
    damage(w, FIRST)
    assert w.bootstrap(w.init_argv(OTHER_SHA)) == 0
    assert_new_install(w, FIRST, SECOND, OTHER_SHA)


# ---------- 5.5 the message is a pure function ----------

LAY = Layout(Path("/"))
BOOT_INIT = f"{BOOTSTRAP} init …"
TAIL = (
    "current не переключайте, поколение не правьте, releases/, admin/ и "
    "releases.toml не трогайте"
)
SAVE = "вне /opt/df, /etc/df и /var/lib/df"
P = "/var/lib/df/install/pending.toml"
J = "/var/lib/df/install/journal.jsonl"
R = f"{SHA[:12]}-{PLATFORM}-2"


def d(
    kind: str,
    *,
    run: str | None = "abc",
    current: str | None = R,
    registered: bool = True,
    install_: bool = True,
    agrees: bool = True,
) -> tuple[object, ...]:
    """The fields of an `install.Damage` of `R`."""
    return (kind, R, run, current, registered, install_, agrees)


def refusal(fields: tuple[object, ...]) -> str:
    return install.damaged_refusal(LAY, install.Damage(*fields))  # type: ignore[arg-type]


SWITCHED_HEAD = (
    f"релиз {R}: повреждён (§3.8), а незавершённая установка (run_id abc) уже "
    "переключила на него current; автоматическое продолжение и новая "
    "установка запрещены до ручного восстановления согласованного состояния "
    f"(ограничение v1). Под root: 1) сохраните копии {P} и {J} {SAVE}; "
    f"2) удалите {P}; "
)
UNCLOSED_HEAD = f"релиз {R}: повреждён (§3.8), current указывает на него, а "
UNCLOSED_TAIL = (
    ": незавершённая установка закрыта вручную не полностью; новая установка "
    "запрещена до ручного восстановления согласованного состояния "
    f"(ограничение v1). Под root: 1) сохраните копию {J} {SAVE}; "
    "2) удалите ссылку: unlink /opt/df/current; затем "
)
PENDING_HEAD = f"релиз {R} незавершённой установки (run_id abc) повреждён (§3.8), "
PENDING_MID = (
    "продолжить установку нельзя (ограничение v1). Под root: "
    f"1) сохраните копии {P} и {J} {SAVE}; 2) удалите {P}; затем "
)
HINT = " (конфигурация прежней попытки — install_text в копии pending.toml)"

TEXTS = {
    "switched-agrees": (
        d("switched"),
        SWITCHED_HEAD + f"затем {BOOTSTRAP} install. {TAIL}",
    ),
    "switched-no-install": (
        d("switched", install_=False, agrees=False),
        SWITCHED_HEAD
        + "3) удалите ссылку: unlink /opt/df/current; "
        + f"затем {BOOT_INIT}{HINT}. {TAIL}",
    ),
    "switched-disagrees": (
        d("switched", agrees=False),
        SWITCHED_HEAD
        + "3) удалите ссылку: unlink /opt/df/current; "
        + f"затем {BOOTSTRAP} install. {TAIL}",
    ),
    "switched-unregistered": (
        d("switched", registered=False, agrees=False),
        SWITCHED_HEAD
        + "3) удалите ссылку: unlink /opt/df/current; "
        + f"затем {BOOTSTRAP} install. {TAIL}",
    ),
    "unclosed-no-install": (
        d("unclosed", run=None, install_=False, agrees=False),
        UNCLOSED_HEAD + "install.toml нет" + UNCLOSED_TAIL + f"{BOOT_INIT}. {TAIL}",
    ),
    "unclosed-disagrees": (
        d("unclosed", run=None, agrees=False),
        UNCLOSED_HEAD
        + "install.toml не согласован с ним"
        + UNCLOSED_TAIL
        + f"{BOOTSTRAP} install. {TAIL}",
    ),
    "unclosed-unregistered": (
        d("unclosed", run=None, registered=False, agrees=False),
        UNCLOSED_HEAD
        + "релиза нет в releases.toml"
        + UNCLOSED_TAIL
        + f"{BOOTSTRAP} install. {TAIL}",
    ),
    "pending-previous": (
        d("pending", current=FIRST),
        PENDING_HEAD
        + f"current = {FIRST} — прежний; "
        + PENDING_MID
        + "darkfactory install (если команда отказывает — "
        + f"{BOOTSTRAP} install). {TAIL}",
    ),
    "pending-no-previous": (
        d("pending", current=None, install_=False, agrees=False),
        PENDING_HEAD
        + "current = нет — прежний; "
        + PENDING_MID
        + f"{BOOT_INIT}{HINT}. {TAIL}",
    ),
}


@pytest.mark.parametrize("case", sorted(TEXTS))
def test_damaged_refusal_text(case: str) -> None:
    """The exact text of each kind x `install.toml` x previous."""
    damage_, want = TEXTS[case]
    text = refusal(damage_)
    assert text == want
    assert_no_bad_advice(text)


def test_damaged_refusal_never_advises_doctor() -> None:
    """Every combination: no `doctor`, no «верните», generation only as a
    prohibition."""
    for kind in ("switched", "unclosed", "pending"):
        for flags in range(16):
            reg, inst, agr, cur = (bool(flags & b) for b in (1, 2, 4, 8))
            text = refusal(
                d(
                    kind,
                    run=None if kind == "unclosed" else "abc",
                    current=R if cur else None,
                    registered=reg,
                    install_=inst,
                    agrees=reg and inst and agr,
                )
            )
            assert_no_bad_advice(text)
