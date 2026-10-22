"""Acceptance scenarios through `drive` (Task 7, part E).

`drive` runs with the real `read_facts`, the real `route_action`/`run_route`
and the real calls of Task 7 (`acceptance_calls`); the leaves of later tasks
(`LATER_LEAVES`) are the table-generated stand-ins of the routes test, and
`admin_ready` (Task 8a) is a stand-in with its signature (F5). Everything
runs in `fake_layout` as the current user: no root, no network. A scenario
stops where a later task's action begins (`Halt`), or where the state it
pins is reached. Crashes are raised at the fault points of fsutil.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from df import accept, fsutil
from df import install_table as table
from df import platform as plat
from df.accept import AcceptMark, Facts, classify, load_mark, read_facts
from df.config import ReleaseEntry, ReleasesConfig, config_digest, dump_releases
from df.digest import tree_digest
from df.journal import StepFailed, read_journal
from df.leaves import release_digest
from df.paths import Layout
from df.routes import route_action, run_route
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.test_routes import (
    BOOT,
    ID,
    INSTALL,
    KEY,
    PY,
    StandIns,
    setup_layout,
    write_accounts,
)

DIGEST = config_digest(INSTALL)
#: Every action of the table (`unknown` included: its action is `cleanup`).
ACTIONS = sorted({r.action for r in (*table.ROWS, table.UNKNOWN_ROW)} - {"", "stop"})
#: The chain of a fresh release up to the first action of Task 9.
CHAIN = ["none", "prepared", "built", "sealed", "copied", "verified", "admin-ready"]


class Halt(Exception):
    """A scenario stops before the action of a later task."""


class Crash(Exception):
    """A simulated crash at a fault point."""


@pytest.fixture(autouse=True)
def _boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)


@pytest.fixture()
def fl(monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout) -> FakeLayout:
    layout = setup_layout(monkeypatch, fake_layout)
    write_accounts(layout)
    layout.layout.pending.write_text("raw\n")
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _p: ["untrusted"])
    return layout


# ---------- the world of a scenario ----------


def admin_ready(
    layout: Layout,
    release_id: str,
    *,
    expected_admin_digest: str,
    expected_python: str,
) -> bool:
    """Stand-in of Task 8a's predicate: the admin code has the digest and the
    admin Python of exactly that version is installed."""
    admin = layout.admin_dir(release_id)
    python = layout.admin_python / f"cpython-{expected_python}-test-triple"
    return (
        accept.is_real_dir(admin)
        and tree_digest(admin, exclude=frozenset()) == expected_admin_digest
        and accept.is_real_dir(python)
    )


def facts(fl: FakeLayout) -> Facts:
    return read_facts(
        fl.layout,
        ID,
        install_digest=DIGEST,
        build_uid=BUILD_UID,
        launcher_ready=lambda: False,
        admin_ready=admin_ready,
    )


def actions(
    fl: FakeLayout, *, halt: Iterable[str] = ("register",), **override: str
) -> dict[str, Callable[[Facts], None]]:
    """`route_action` of every action over one set of calls; `halt` actions
    stop the scenario; `override` binds an action to another's route."""
    calls = StandIns(fl).calls()
    stop = set(halt)

    def halted(name: str) -> Callable[[Facts], None]:
        def act(_facts: Facts) -> None:
            raise Halt(name)

        return act

    return {
        a: halted(a)
        if a in stop
        else route_action(
            fl.layout,
            ID,
            override.get(a, a),
            calls=calls,
            python_key=KEY,
            build_uid=BUILD_UID,
        )
        for a in ACTIONS
    }


def run(fl: FakeLayout, run_id: str, **kw: object) -> list[str]:
    """Drive to a halt; the phases whose actions started, in order."""
    with pytest.raises(Halt):
        drive_once(fl, run_id, **kw)
    return started(fl, run_id)


def drive_once(fl: FakeLayout, run_id: str, **kw: object) -> None:
    """One `drive` over the real routes (see `actions` for `kw`)."""
    accept.drive(
        fl.layout,
        ID,
        actions=actions(fl, **kw),  # type: ignore[arg-type]
        read=lambda: facts(fl),
        journal=fl.layout.journal,
        run_id=run_id,
    )


def journal(fl: FakeLayout, run_id: str) -> list[tuple[object, object, object]]:
    return [
        (r["step"], r["event"], r["detail"])
        for r in read_journal(fl.layout.journal)
        if r["run_id"] == run_id
    ]


def started(fl: FakeLayout, run_id: str) -> list[str]:
    return [str(d) for _s, e, d in journal(fl, run_id) if e == "start"]


def to_admin_ready(fl: FakeLayout) -> None:
    """A fresh release driven to `admin-ready` (the chain of Task 7)."""
    assert run(fl, "chain") == CHAIN
    assert classify(facts(fl)) == "admin-ready"


def crash_at(
    monkeypatch: pytest.MonkeyPatch, kind: str, path: Path, *, nth: int = 1
) -> None:
    """The recorded fault point raises `Crash` at the `nth` (`kind`, `path`)."""
    record = fsutil.fault_point
    seen = 0

    def fault_point(k: str, p: Path) -> None:
        nonlocal seen
        record(k, p)  # type: ignore[arg-type]
        if (k, p) == (kind, path):
            seen += 1
            if seen == nth:
                raise Crash(f"{k} {p}")

    monkeypatch.setattr(fsutil, "fault_point", fault_point)


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)


# ---------- scenarios ----------


def test_fresh_release_reaches_admin_ready(fl: FakeLayout) -> None:
    """The whole chain of Task 7 through `drive`: every action is its row's,
    every post holds; the mark ends `admin` with the copy's digest."""
    to_admin_ready(fl)
    lay = fl.layout
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None and mark.stage == "admin"
    assert mark.tree_digest == release_digest(lay.release_dir(ID))
    assert (mark.admin_python, mark.uv_version_declared) == (PY, "uv 0.9.2")
    assert mode(lay.release_dir(ID)) == 0o755
    assert not os.path.lexists(lay.staging_dir(ID))
    assert [s for s, e, _d in journal(fl, "chain") if e == "done"] == [
        "build-dir",
        "build",
        "seal",
        "copy",
        "verify",
        "admin",
    ]


def test_building_cleans_then_rebuilds(fl: FakeLayout) -> None:
    """A builder tree without a mark (a build cut short) is `building`: the
    route `cleanup` removes it, then the chain starts from `none`."""
    lay = fl.layout
    tree = fl.builder_dir(lay.release_dir(ID))
    (tree / "half-built").write_text("x\n")
    assert classify(facts(fl)) == "building"
    assert run(fl, "rebuild") == ["building", *CHAIN]
    assert not (lay.release_dir(ID) / "half-built").exists()
    assert journal(fl, "rebuild")[:2] == [
        ("cleanup", "start", "building"),
        ("cleanup", "done", "building → none"),
    ]


def test_partial_copy_removes_then_copies(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """A crash after the whole stream was received but before it is synced
    and marked leaves a root `0700` copy without the `copied` mark:
    `partial-copy`, never «copy done». The route `copy-partial` removes the
    remainder and copies again."""
    lay = fl.layout
    with pytest.raises(Halt):
        drive_once(fl, "to-sealed", halt=("copy",))
    assert classify(facts(fl)) == "sealed"
    with monkeypatch.context() as m, pytest.raises(Crash):
        crash_at(m, "created", lay.release_dir(ID), nth=2)
        drive_once(fl, "copy-crash")
    remainder = lay.release_dir(ID)
    assert mode(remainder) == 0o700
    f = facts(fl)
    assert (f.m, f.r, f.S, f.full) == ("built", "root-700", True, False)
    assert classify(f) == "partial-copy"
    assert run(fl, "resume", halt=("verify",)) == ["partial-copy", "copied"]
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None and mark.stage == "copied"
    assert mark.tree_digest == release_digest(lay.release_dir(ID))
    assert release_digest(lay.release_dir(ID)) == release_digest(lay.staging_tree(ID))


@pytest.mark.skipif(os.geteuid() == 0, reason="F35: root reads 000 files (Task 13)")
def test_tar_failure_refuses_and_keeps_partial_for_route(fl: FakeLayout) -> None:
    """The drive half of the leaf test of the same name (test_leaves): an
    unreadable file stops `copy` with `StepFailed("copy")`, journaled; the
    partial copy stays; the next drive starts at `partial-copy` and goes on
    through `copy-partial`."""
    lay = fl.layout
    with pytest.raises(Halt):
        drive_once(fl, "to-sealed", halt=("copy",))
    secret = lay.staging_tree(ID) / "secret"
    secret.write_text("s\n")
    secret.chmod(0)
    try:
        with pytest.raises(StepFailed) as exc:
            drive_once(fl, "tar")
    finally:
        secret.chmod(0o644)
    assert exc.value.step == "copy"
    events = journal(fl, "tar")
    assert events[-2] == ("copy", "start", "sealed")
    assert events[-1][:2] == ("copy", "failed")
    assert mode(lay.release_dir(ID)) == 0o700
    assert run(fl, "retry", halt=("verify",)) == ["partial-copy", "copied"]


def test_action_refuses_outside_its_phase(fl: FakeLayout) -> None:
    """`seal` bound where `drive` is at `building` (a miswired action table):
    the action refuses before any step — `StepFailed("seal")`, journaled,
    staging not created, the builder tree untouched."""
    lay = fl.layout
    tree = fl.builder_dir(lay.release_dir(ID))
    (tree / "half-built").write_text("x\n")
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        drive_once(fl, "miswired", cleanup="seal")
    assert (exc.value.step, str(exc.value.detail)) == ("seal", "не в своей фазе")
    assert journal(fl, "miswired") == [
        ("cleanup", "start", "building"),
        ("cleanup", "failed", "StepFailed: seal: не в своей фазе"),
    ]
    # Only the reads of the facts and the settles ran: no call, no change.
    assert {e[0] for e in fl.events} <= {"sync_dir", "run_as"}
    assert {e[2][0] for e in fl.events if e[0] == "run_as"} == {"/usr/bin/find"}
    assert not os.path.lexists(lay.staging_dir(ID))
    assert (tree / "half-built").exists()


def test_root_mode_regressed_reverifies(fl: FakeLayout) -> None:
    """The route half: `verified`, then the root's mode regresses to `0700`
    (a crash before the chmod became durable) — `copied`, the route `verify`
    runs again and gives back `verified`."""
    lay = fl.layout
    with pytest.raises(Halt):
        drive_once(fl, "to-verified", halt=("admin",))
    os.chmod(lay.release_dir(ID), 0o700)
    assert classify(facts(fl)) == "copied"
    assert run(fl, "reverify", halt=("admin",)) == ["copied", "verified"]
    assert mode(lay.release_dir(ID)) == 0o755
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None and mark.stage == "verified"


def test_reverify_keeps_admin_mark(fl: FakeLayout) -> None:
    """S1-R4-N2: `admin` mark with the right digests, root `0700` → `copied`
    → `verify` → the mark stays `admin` with its admin digest → `admin-ready`
    (write_mark never lowers it; the admin route is not run again)."""
    lay = fl.layout
    to_admin_ready(fl)
    before = load_mark(lay, ID, install_digest=DIGEST)
    os.chmod(lay.release_dir(ID), 0o700)
    f = facts(fl)
    assert (f.m, f.r, f.A) == ("admin", "root-700", True)
    assert classify(f) == "copied"
    assert run(fl, "reverify") == ["copied", "admin-ready"]
    assert load_mark(lay, ID, install_digest=DIGEST) == before
    assert [s for s, e, _d in journal(fl, "reverify") if e == "done"] == ["verify"]


def test_rollback_removes_unregistered_0755_tree(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    """A crash right after `verify_chmod`'s chmod: the tree is root `0755`
    but unregistered. Rollback (Task 9's route `rollback`) goes through
    `cleanup`: `remove_own_tree` removes it; mark and pending go too."""
    lay = fl.layout
    with pytest.raises(Halt):
        drive_once(fl, "to-copied", halt=("verify",))
    with monkeypatch.context() as m, pytest.raises(Crash):
        crash_at(m, "chmod", lay.release_dir(ID))
        drive_once(fl, "verify-crash")
    assert mode(lay.release_dir(ID)) == 0o755
    assert accept.registry_entry(lay, ID) is None
    fl.clear()
    run_route(
        lay,
        ID,
        "rollback",
        calls=StandIns(fl).calls(),
        python_key=KEY,
        build_uid=BUILD_UID,
    )
    assert ("unlinked", lay.release_dir(ID)) in fl.events
    assert not os.path.lexists(lay.release_dir(ID))
    assert not os.path.lexists(lay.mark_file(ID))
    assert not os.path.lexists(lay.pending)


def register_release(fl: FakeLayout) -> ReleaseEntry:
    """A real registry entry of the release as it is on disk (Task 9's
    `register`, written directly)."""
    lay = fl.layout
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None and mark.admin_digest is not None
    entry = ReleaseEntry(
        ID, release_digest(lay.release_dir(ID)), mark.admin_digest, PY, "allowed", None
    )
    (lay.etc / "releases.toml").write_text(dump_releases(ReleasesConfig((entry,))))
    return entry


def test_registered_release_never_removed(fl: FakeLayout) -> None:
    """Registered: `cleanup` refuses before its first step (F13), `rollback`
    skips it (`not reg`); damaged (the tree changed after registration)
    stops `drive` without any action. Nothing of the release is removed."""
    lay = fl.layout
    to_admin_ready(fl)
    register_release(fl)
    assert classify(facts(fl)) == "registered"
    kept = [lay.release_dir(ID), lay.admin_dir(ID), lay.mark_file(ID)]
    fl.clear()
    with pytest.raises(StepFailed) as exc:
        run_route(
            lay,
            ID,
            "cleanup",
            calls=StandIns(fl).calls(),
            python_key=KEY,
            build_uid=BUILD_UID,
        )
    assert exc.value.step == "cleanup"
    assert [e for e in fl.events if e[0] == "call"] == []
    (lay.release_dir(ID) / "tampered").write_text("x\n")
    assert classify(facts(fl)) == "damaged"
    with pytest.raises(StepFailed) as stop:
        drive_once(fl, "damaged")
    assert stop.value.step == "damaged"
    assert started(fl, "damaged") == []
    run_route(
        lay,
        ID,
        "rollback",
        calls=StandIns(fl).calls(),
        python_key=KEY,
        build_uid=BUILD_UID,
    )
    assert all(os.path.lexists(p) for p in kept)
    assert not os.path.lexists(lay.pending)


def test_partial_tree_not_accepted_by_journal(fl: FakeLayout) -> None:
    """The route half: a journal that says «copy: done» over a complete copy
    whose `copied` mark is lost (only `built` remains) changes nothing —
    `drive` takes `copy-partial`, not `verify`."""
    lay = fl.layout
    with pytest.raises(Halt):
        drive_once(fl, "to-sealed", halt=("copy",))
    with pytest.raises(Halt):
        drive_once(fl, "lost-mark", halt=("verify",))
    mark = load_mark(lay, ID, install_digest=DIGEST)
    assert mark is not None
    accept.drop_mark(lay, ID)
    built = AcceptMark(
        DIGEST, "built", mark.built_boot_id, None, None, None, mark.uv_version_declared
    )
    accept.write_mark(lay, ID, built)
    assert ("copy", "done", "sealed → copied") in journal(fl, "lost-mark")
    assert run(fl, "after", halt=("verify",)) == ["partial-copy", "copied"]


def test_stray_tmp_before_registration_rebuilds(fl: FakeLayout) -> None:
    """F24 (FM2): a `.release.json.*.tmp` left in `releases/<id>` by a crash
    of `register` before `releases.toml` makes the tree differ from the mark
    (`full` = 0): `unknown` → `cleanup` → the release is rebuilt; the phase
    is never `registered` or `damaged`."""
    lay = fl.layout
    to_admin_ready(fl)
    stray = lay.release_dir(ID) / f".release.json.{os.getpid()}.{'a' * 32}.tmp"
    stray.write_text("{}\n")
    f = facts(fl)
    assert (f.m, f.full, f.reg) == ("admin", False, False)
    assert classify(f) == "unknown"
    phases = run(fl, "fm2")
    assert phases == ["unknown", *CHAIN]
    assert not {"registered", "damaged"} & set(phases)
    assert not stray.exists()
    assert accept.registry_entry(lay, ID) is None
