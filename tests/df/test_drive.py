"""Tests for the acceptance driver `drive` (Task 7, part C: fake actions).

End-to-end runs through real routes come with parts 7-D/7-E.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from df import accept, fsutil
from df import install_table as table
from df.accept import Facts, classify, drive
from df.journal import StepFailed, read_journal
from tests.df.conftest import FakeLayout

ID = "rel1"
ACTION_ROWS = [r for r in table.ROWS if r.action not in ("", "stop")]


def facts_of(phase: str) -> Facts:
    """A point of `phase`: the smallest value of each cube fact, else the
    domain's first; `unknown` — a pre-registration point no row contains."""
    if phase == "unknown":
        point: dict[str, object] = {k: d[0] for k, d in table.DOMAINS.items()}
        point.update(P=1, m="copied", r="absent")
    else:
        cube = table.row_of(phase).cubes[0]
        point = {
            k: sorted(cube[k], key=str)[0] if k in cube else d[0]
            for k, d in table.DOMAINS.items()
        }
    f = Facts(**{k: v if isinstance(v, str) else bool(v) for k, v in point.items()})  # type: ignore[arg-type]
    assert classify(f) == phase
    return f


class World:
    """A fake world: the phase is the state; actions move it."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.reads = 0
        self.called: list[str] = []

    def read(self) -> Facts:
        self.reads += 1
        return facts_of(self.phase)

    def action(self, name: str, to: str | None = None) -> Callable[[Facts], None]:
        def run(f: Facts) -> None:
            assert classify(f) == self.phase
            self.called.append(name)
            row = table.row_of(self.phase)
            self.phase = to if to is not None else row.post[0]

        return run

    def actions(self, **override: str) -> dict[str, Callable[[Facts], None]]:
        names = {r.action for r in (*table.ROWS, table.UNKNOWN_ROW)} - {"", "stop"}
        return {n: self.action(n, override.get(n)) for n in names}


def run(fl: FakeLayout, world: World, **kw: object) -> None:
    drive(
        fl.layout,
        ID,
        actions=kw.pop("actions", None) or world.actions(),  # type: ignore[arg-type]
        read=world.read,
        journal=fl.layout.journal,
        run_id="run-1",
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture()
def fl(fake_layout: FakeLayout) -> FakeLayout:
    fake_layout.make_dirs()
    return fake_layout


def events(fl: FakeLayout) -> list[tuple[str, str, str]]:
    rows = read_journal(fl.layout.journal)
    return [(str(r["step"]), str(r["event"]), str(r["detail"])) for r in rows]


@pytest.mark.parametrize("phase", [r.phase for r in ACTION_ROWS] + ["unknown"])
def test_drive_calls_row_action_and_checks_post(fl: FakeLayout, phase: str) -> None:
    row = table.row_of(phase)
    world = World(phase)
    run(fl, world)
    assert world.called[0] == row.action
    assert world.phase == "committed"
    first = events(fl)[:2]
    assert first == [
        (row.action, "start", phase),
        (row.action, "done", f"{phase} → {row.post[0]}"),
    ]
    # A substituted action leading outside `post`: both phases named.
    bad = next(p for p in ("committed", "damaged", "none") if p not in row.post)
    world = World(phase)
    with pytest.raises(StepFailed) as exc:
        run(fl, world, actions=world.actions(**{row.action: bad}))
    assert exc.value.step == row.action
    assert exc.value.detail == f"{phase} → {bad}"


def test_drive_follows_every_post(fl: FakeLayout) -> None:
    """Rows with two posts: either is accepted."""
    for row in ACTION_ROWS:
        for post in row.post:
            world = World(row.phase)
            run(fl, world, actions=world.actions(**{row.action: post}))
            assert world.phase == "committed"


def test_committed_runs_nothing(fl: FakeLayout) -> None:
    world = World("committed")
    run(fl, world)
    assert world.called == [] and world.reads == 1
    assert events(fl) == []


def test_damaged_stops_without_action(fl: FakeLayout) -> None:
    world = World("damaged")
    with pytest.raises(StepFailed) as exc:
        run(fl, world)
    assert exc.value.step == "damaged"
    assert world.called == []
    assert [e[:2] for e in events(fl)] == [("damaged", "failed")]


def test_drive_settles_before_classify(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    order: list[str] = []
    real_settle = accept.settle

    def settle(layout: object, release_id: str) -> None:
        order.append("settle")
        real_settle(layout, release_id)  # type: ignore[arg-type]

    monkeypatch.setattr(accept, "settle", settle)
    world = World("launched")
    real_read = world.read

    def read() -> Facts:
        order.append("read")
        return real_read()

    world.read = read  # type: ignore[method-assign]
    run(fl, world)
    # iteration 1: settle, read, action, settle, read (post); iteration 2:
    # settle, read -> committed.
    assert order == ["settle", "read", "settle", "read", "settle", "read"]
    assert world.called == ["commit-config"]
    synced = [e[1] for e in fl.events if e[0] == "sync_dir"]
    assert synced[0] == fl.layout.quarantine


def test_no_progress_bounded(fl: FakeLayout) -> None:
    world = World("none")
    with pytest.raises(StepFailed) as exc:
        run(fl, world, max_iter=3)
    assert exc.value.step == "drive"
    assert "нет продвижения" in exc.value.detail
    assert world.called == ["build-dir", "build", "seal"]
    assert events(fl)[-1][:2] == ("drive", "failed")


def test_unchanged_action_fails_post(fl: FakeLayout) -> None:
    """An action that changes nothing is a post mismatch on its own row."""
    world = World("building")
    with pytest.raises(StepFailed) as exc:
        run(fl, world, actions=world.actions(cleanup="building"))
    assert exc.value.detail == "building → building"


def test_action_exception_journaled_and_reraised(fl: FakeLayout) -> None:
    """F29: `failed` with the exception, like `run_steps`."""
    world = World("sealed")

    def boom(f: Facts) -> None:
        raise OSError("disk")

    acts = world.actions()
    acts["copy"] = boom
    with pytest.raises(OSError, match="disk"):
        run(fl, world, actions=acts)
    assert events(fl) == [
        ("copy", "start", "sealed"),
        ("copy", "failed", "OSError: disk"),
    ]


def test_journal_failure_keeps_original(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout
) -> None:
    world = World("damaged")

    def broken(*a: object, **k: object) -> None:
        raise OSError("journal gone")

    monkeypatch.setattr(accept, "journal_event", broken)
    with pytest.raises(StepFailed) as exc:
        run(fl, world)
    assert exc.value.step == "damaged"
    assert any("journal gone" in n for n in exc.value.__notes__)


@pytest.mark.parametrize("outcome", ["return", "failure"])
def test_drive_journals_fullfsync_notes(
    monkeypatch: pytest.MonkeyPatch, fl: FakeLayout, outcome: str
) -> None:
    """F10, decision (a): unsupported F_FULLFSYNC reaches the journal."""
    world = World("committed" if outcome == "return" else "damaged")
    monkeypatch.setattr(fsutil, "_SYNC_NOTES", {7, 3})
    if outcome == "return":
        run(fl, world)
    else:
        with pytest.raises(StepFailed):
            run(fl, world)
    assert events(fl)[-1] == (
        "drive",
        "sync-note",
        "fullfsync-unsupported: dev=3, dev=7",
    )


def test_no_sync_note_without_notes(fl: FakeLayout) -> None:
    run(fl, World("committed"))
    assert not Path(fl.layout.journal).exists() or events(fl) == []
