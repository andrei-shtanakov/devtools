"""`darkfactory perimeter restore` (spec 6.1; stage 2a, Task 11r).

The protocol is tested at the level of the `df-publisher` half
(`perimeter.restore`, called in this process with the test layout; crash
points in forked children through `perimeter.RESTORE_STEP`). Root's writer
is tested through `cli.main` with `run_as` replaced by the role half run in
this process (as `roles setup`'s `perimeter-init` is tested).
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import os
import shutil
import sqlite3
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import cli, fsutil, journal, launcher_entry, libexec, roles_setup, sudoers
from df import platform as plat
from df.config import Account, dump_accounts, load_accounts
from df.journal import Stable
from df.paths import Layout
from df.publisher import perimeter
from df.publisher.perimeter import Unavailable
from tests.df.conftest import FakeLayout
from tests.df.install_world import World, files, installed, make_world
from tests.df.route_world import BOOT
from tests.df.test_perimeter import (
    DOWN,
    S1,
    _ctx,
    at,
    db_file,
    digest,
    raw,
    record_in,
    rows,
    state_of,
    status,
    unavailable,
)

JOURNAL = perimeter.JOURNAL
PENDING = [("pending", None)]


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    layout = Layout(Path(os.path.realpath(tmp_path)) / "root")
    layout.perimeter_dir.mkdir(parents=True, mode=0o700)
    return layout


@pytest.fixture()
def ready(lay: Layout) -> Layout:
    """A database with one committed (`pending`) event."""
    assert perimeter.create_db(lay) == "created"
    record_in(lay, 1)
    return lay


@pytest.fixture()
def copy(ready: Layout) -> bytes:
    """A healthy copy of `ready` (one event); the database then moves on."""
    data = db_file(ready).read_bytes()
    record_in(ready, 2)
    return data


def admission_of(lay: Layout) -> str:
    conn = raw(lay)
    try:
        return perimeter.admission(conn)
    finally:
        conn.close()


def listing(lay: Layout) -> dict[str, str]:
    """Every file under `perimeter/` with its digest (`set-aside/` too)."""
    base = lay.perimeter_dir
    out: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if path.is_file() and os.access(path, os.R_OK):
            out[str(path.relative_to(base))] = digest(path)
        else:
            out[str(path.relative_to(base))] = "dir" if path.is_dir() else "?"
    return out


def never_clear(lay: Layout) -> None:
    """Whatever is in place, an available epoch source never answers
    `clear` (a restored copy is admitted with conservative events)."""
    assert state_of(status(lay, at(S1, 1))) == "blocked"


# --- the protocol ------------------------------------------------------------------


def test_restore_from_copy_is_pending(ready: Layout, copy: bytes) -> None:
    assert admission_of(ready) == "ready"  # the copy was taken of a ready base
    assert perimeter.restore(ready, copy) == "restored"
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == PENDING  # the copy's one event, not the newer two
    assert status(ready, DOWN).result["reason"] == "restore_pending"
    base = ready.perimeter_dir
    assert stat_mode(base / perimeter.DB_NAME) == 0o600
    assert not (base / perimeter.RESTORE_DB).exists()
    assert not (base / JOURNAL).exists()
    never_clear(ready)


def stat_mode(path: Path) -> int:
    return os.lstat(path).st_mode & 0o777


@pytest.mark.parametrize("before", ["lost", "present", "dir-emptied"])
def test_restore_empty_is_pending(ready: Layout, before: str) -> None:
    """`--empty`: a fresh schema with `restored_pending`, never `ready`, over
    a lost database, a present one, or an emptied directory."""
    base = ready.perimeter_dir
    if before == "lost":
        db_file(ready).unlink()
        assert unavailable(ready) == "lost"
    if before == "dir-emptied":
        for name in os.listdir(base):
            (base / name).unlink()
    assert perimeter.restore(ready, None) == "restored"
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == []
    assert status(ready, DOWN).result["reason"] == "restore_pending"
    never_clear(ready)


def test_restore_lost_database_from_copy(ready: Layout, copy: bytes) -> None:
    db_file(ready).unlink()
    assert unavailable(ready) == "lost"
    assert perimeter.restore(ready, copy) == "restored"
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == PENDING


@pytest.mark.parametrize(
    "spoil",
    [
        "garbage",
        "empty",
        "unknown-state",
        "too-many-events",
    ],
)
def test_restore_bad_copy_refused_changes_nothing(
    ready: Layout, copy: bytes, spoil: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 1 verifies the copy as `open_db` verifies a database; a refusal
    changes nothing (the stage is removed)."""
    if spoil == "garbage":
        copy = b"not a database" * 100
    if spoil == "empty":
        copy = b""
    if spoil == "unknown-state":
        copy = _spoiled(ready, copy, "UPDATE events SET state = 'gone'")
    if spoil == "too-many-events":
        monkeypatch.setattr(perimeter, "MAX_EVENTS", 0)
    before = listing(ready)
    with pytest.raises(Unavailable) as info:
        perimeter.restore(ready, copy)
    assert info.value.reason.startswith("copy: ")
    assert listing(ready) == before
    assert "ничего не изменено" in perimeter.restore_text(info.value.reason)


def _spoiled(lay: Layout, copy: bytes, sql: str) -> bytes:
    path = lay.root / "spoiled.sqlite"
    path.write_bytes(copy)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(sql)
    conn.close()
    return path.read_bytes()


def test_restore_copy_size_cap(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert perimeter.MAX_COPY == 256 << 20
    monkeypatch.setattr(perimeter, "MAX_COPY", len(copy) - 1)
    before = listing(ready)
    with pytest.raises(Unavailable, match="copy: too_large"):
        perimeter.restore(ready, copy)
    assert listing(ready) == before
    monkeypatch.setattr(perimeter, "MAX_COPY", len(copy))
    assert perimeter.restore(ready, copy) == "restored"


def _hold_open(root: str, opened: object, release: object, closing: object) -> None:
    db = perimeter.open_db(Layout(Path(root)))
    opened.set()  # type: ignore[attr-defined]
    release.wait(30)  # type: ignore[attr-defined]
    closing.set()  # type: ignore[attr-defined]
    db.close()


def _admission_child(root: str, out: object) -> None:
    resp = status(Layout(Path(root)), DOWN)
    out.put(resp.result["reason"])  # type: ignore[attr-defined]


def test_restore_waits_for_open_connection(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Process A has the database open (the barrier `LOCK_SH`); restore stages
    its copy, then waits at the barrier until A closes; a new process B sees
    only the restored database, `restored_pending`."""
    ctx = _ctx()
    opened, release, closing = ctx.Event(), ctx.Event(), ctx.Event()
    a = ctx.Process(target=_hold_open, args=(str(ready.root), opened, release, closing))
    a.start()
    assert opened.wait(10)
    seen: list[tuple[str, bool]] = []

    def step(name: str) -> None:
        if name == "staged":
            threading.Timer(0.5, release.set).start()
        if name in ("staged", "barrier"):
            seen.append((name, closing.is_set()))

    monkeypatch.setattr(perimeter, "RESTORE_STEP", step)
    assert perimeter.restore(ready, copy) == "restored"
    a.join(timeout=30)
    assert a.exitcode == 0
    assert seen == [("staged", False), ("barrier", True)]
    out = ctx.Queue()
    b = ctx.Process(target=_admission_child, args=(str(ready.root), out))
    b.start()
    assert out.get(timeout=30) == "restore_pending"
    b.join(timeout=30)
    assert rows(ready) == PENDING


def test_restore_timeout_changes_nothing(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert perimeter.RESTORE_WAIT == 30.0
    monkeypatch.setattr(perimeter, "RESTORE_WAIT", 0.2)
    before = listing(ready)
    with holding(ready, fcntl.LOCK_SH):
        with pytest.raises(Unavailable) as info:
            perimeter.restore(ready, copy)
    assert info.value.reason == "busy"
    assert "занята" in perimeter.restore_text("busy")
    assert listing(ready) == before
    assert admission_of(ready) == "ready"


@contextlib.contextmanager
def holding(lay: Layout, op: int) -> Iterator[None]:
    """The barrier held by another open file description (a publisher)."""
    fd = os.open(lay.perimeter_dir / perimeter.LOCK, os.O_RDONLY)
    try:
        fcntl.flock(fd, op)
        yield
    finally:
        os.close(fd)


def _restore_crash(root: str, point: str, copy: bytes, set_aside: bool) -> None:
    def step(name: str) -> None:
        if name == point:
            os._exit(0)

    perimeter.RESTORE_STEP = step
    perimeter.restore(Layout(Path(root)), copy, set_aside_old=set_aside)
    os._exit(3)  # the point was never reached


def crash_at(lay: Layout, point: str, copy: bytes, *, set_aside: bool) -> None:
    p = _ctx().Process(
        target=_restore_crash, args=(str(lay.root), point, copy, set_aside)
    )
    p.start()
    p.join(timeout=60)
    assert p.exitcode == 0, point


@pytest.mark.parametrize(
    ("point", "installed"),
    [
        ("staged", False),
        ("barrier", False),
        ("rolled-back", False),
        ("installed", True),
    ],
)
def test_restore_crash_points(
    ready: Layout, copy: bytes, point: str, installed: bool
) -> None:
    """A crash at each of steps 1-4: the old database (its committed events
    in place) or the new one with `restored_pending`; never `clear`. A
    repeat finishes (a leftover stage is swept)."""
    old = rows(ready)
    assert len(old) == 2
    crash_at(ready, point, copy, set_aside=False)
    if installed:
        assert admission_of(ready) == "restored_pending"
        assert rows(ready) == PENDING
    else:
        assert admission_of(ready) == "ready"
        assert rows(ready) == old
    assert not (ready.perimeter_dir / JOURNAL).exists()
    never_clear(ready)
    assert perimeter.restore(ready, copy) == "restored"
    assert admission_of(ready) == "restored_pending"
    assert not (ready.perimeter_dir / perimeter.RESTORE_DB).exists()


def _crash_spilling(root: str) -> None:
    """A writer that dies inside a write transaction whose pages already
    reached the database file (a one-page cache spills): a hot journal."""
    db = perimeter.open_db(Layout(Path(root)))
    db.conn.execute("PRAGMA cache_size = 1")
    db.conn.execute("BEGIN IMMEDIATE")
    db.conn.execute("DELETE FROM events")
    for _ in range(40):
        db.conn.execute(
            "INSERT INTO transitions (event_id, transition, at, provenance, "
            "actor, evidence) VALUES (1, 'x', 'now', ?, 'a', '{}')",
            ("p" * 4000,),
        )
    os._exit(0)


def hot_journal(lay: Layout) -> int:
    """Leave a real hot journal; return the database size before the crash."""
    size = db_file(lay).stat().st_size
    p = _ctx().Process(target=_crash_spilling, args=(str(lay.root),))
    p.start()
    p.join(timeout=60)
    assert p.exitcode == 0
    assert (lay.perimeter_dir / JOURNAL).exists()
    assert db_file(lay).stat().st_size > size  # pages reached the file
    return size


def test_restore_with_hot_journal(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 3: the old database's hot journal is rolled back by a read under
    `LOCK_EX` before the rename, so the new database never meets it."""
    old = rows(ready)
    size = hot_journal(ready)
    seen: list[object] = []

    def step(name: str) -> None:
        if name == "rolled-back":
            seen.append((ready.perimeter_dir / JOURNAL).exists())
            seen.append(db_file(ready).stat().st_size == size)
            seen.append(rows(ready) == old)  # the committed events are back

    monkeypatch.setattr(perimeter, "RESTORE_STEP", step)
    assert perimeter.restore(ready, copy) == "restored"
    assert seen == [False, True, True]
    assert not (ready.perimeter_dir / JOURNAL).exists()
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == PENDING


@pytest.mark.parametrize(
    "spoil",
    [
        "zeroed-header",
        pytest.param(
            "unopenable",
            marks=pytest.mark.skipif(
                os.geteuid() == 0, reason="root opens a 0000 journal"
            ),
        ),
    ],
)
def test_restore_corrupt_pair_refused(ready: Layout, copy: bytes, spoil: str) -> None:
    """A pair the read does not resolve is refused with the `--set-aside-old`
    instruction; both files are untouched.

    `zeroed-header`: a real hot journal with its header zeroed -- SQLite
    takes it for not hot and neither rolls it back nor deletes it, while the
    database keeps the torn pages; only the check "no journal after the
    read" refuses it. `unopenable` (the I/O form): a journal SQLite cannot
    open, `SQLITE_CANTOPEN` at the read."""
    hot_journal(ready)
    journal_path = ready.perimeter_dir / JOURNAL
    if spoil == "zeroed-header":
        with journal_path.open("r+b") as fh:
            fh.write(bytes(28))
    before = listing(ready)
    if spoil == "unopenable":
        journal_path.chmod(0)
    try:
        with pytest.raises(Unavailable) as info:
            perimeter.restore(ready, copy)
    finally:
        journal_path.chmod(0o600)
    assert info.value.reason == "journal"
    assert "--set-aside-old" in perimeter.restore_text("journal")
    assert listing(ready) == before


def test_orphan_journal_is_refused(ready: Layout, copy: bytes) -> None:
    """A journal without its database is never removed alone, nor met by a
    new database: a refusal naming the emergency branch."""
    hot_journal(ready)
    db_file(ready).unlink()
    before = listing(ready)
    with pytest.raises(Unavailable, match="journal"):
        perimeter.restore(ready, copy)
    with pytest.raises(Unavailable, match="journal"):
        perimeter.restore(ready, None)
    assert listing(ready) == before


# --- --set-aside-old -----------------------------------------------------------------


def aside_dir(lay: Layout) -> Path:
    found = list((lay.perimeter_dir / perimeter.SET_ASIDE).iterdir())
    assert len(found) == 1
    return found[0]


def test_set_aside_moves_the_old_pair(ready: Layout, copy: bytes) -> None:
    hot_journal(ready)
    old = {n: digest(ready.perimeter_dir / n) for n in (perimeter.DB_NAME, JOURNAL)}
    assert perimeter.restore(ready, copy, set_aside_old=True) == "restored"
    aside = aside_dir(ready)
    assert {n: digest(aside / n) for n in old} == old
    assert stat_mode(aside) == stat_mode(aside.parent) == 0o700
    assert not (ready.perimeter_dir / perimeter.RESTORE_INTENT).exists()
    assert not (ready.perimeter_dir / JOURNAL).exists()
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == PENDING


@pytest.mark.parametrize(
    "point", ["intent", "set-aside-dir", "journal-moved", "db-moved", "installed"]
)
def test_set_aside_crash_points(ready: Layout, copy: bytes, point: str) -> None:
    """A crash after the intent, the set-aside directory, the journal's move,
    the database's move or the new database's rename: a new publisher
    process is `restore_incomplete` and creates nothing; never the new
    database next to the old journal; a repeat `restore` (any copy) finishes
    from the intent's stage; both old files end in `set-aside/`."""
    hot_journal(ready)
    base = ready.perimeter_dir
    old = {n: digest(base / n) for n in (perimeter.DB_NAME, JOURNAL)}
    crash_at(ready, point, copy, set_aside=True)
    # the journal leaves first; the database is absent only between the
    # database's move and the new one's rename
    assert (base / JOURNAL).exists() == (point in ("intent", "set-aside-dir"))
    assert db_file(ready).exists() == (point != "db-moved")
    db_was = db_file(ready).exists()
    assert unavailable(ready) == "restore_incomplete"
    assert db_file(ready).exists() == db_was  # nothing created
    new_in_place = not (base / perimeter.RESTORE_DB).exists()
    assert not (new_in_place and (base / JOURNAL).exists())
    assert perimeter.restore(ready, None) == "resumed"
    aside = aside_dir(ready)
    assert {n: digest(aside / n) for n in old} == old
    assert not (base / perimeter.RESTORE_INTENT).exists()
    assert not (base / JOURNAL).exists()
    assert admission_of(ready) == "restored_pending"
    assert rows(ready) == PENDING  # the intent's stage, not an empty base
    never_clear(ready)


def test_set_aside_failure_midway_keeps_its_stage(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error (not a crash) after the intent leaves the intent's stage in
    place: the repeat still finishes, from the stage of the first call."""
    hot_journal(ready)

    def step(name: str) -> None:
        if name == "db-moved":
            raise OSError("disk went away")

    monkeypatch.setattr(perimeter, "RESTORE_STEP", step)
    with pytest.raises(OSError, match="disk went away"):
        perimeter.restore(ready, copy, set_aside_old=True)
    assert (ready.perimeter_dir / perimeter.RESTORE_DB).exists()
    monkeypatch.setattr(perimeter, "RESTORE_STEP", perimeter._no_step)  # noqa: SLF001
    assert perimeter.restore(ready, None) == "resumed"
    assert rows(ready) == PENDING


def test_resume_waits_for_the_barrier(
    ready: Layout, copy: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(б)-(д) of a resume run under `LOCK_EX`: a publisher holding the
    barrier makes the resume `busy`, nothing moved."""
    hot_journal(ready)
    crash_at(ready, "journal-moved", copy, set_aside=True)
    monkeypatch.setattr(perimeter, "RESTORE_WAIT", 0.2)
    before = listing(ready)
    with holding(ready, fcntl.LOCK_SH):
        with pytest.raises(Unavailable, match="busy"):
            perimeter.restore(ready, None)
    assert listing(ready) == before


def test_resume_never_overwrites_the_set_aside_copy(ready: Layout, copy: bytes) -> None:
    """Intent, stage and `events.sqlite` in place, but `set-aside/<UTC>/`
    already holds an `events.sqlite`: never two of one name."""
    crash_at(ready, "journal-moved", copy, set_aside=True)
    (aside_dir(ready) / perimeter.DB_NAME).write_bytes(b"the only old copy")
    before = listing(ready)
    with pytest.raises(Unavailable, match="restore_incomplete"):
        perimeter.restore(ready, None)
    assert listing(ready) == before


def test_resume_never_admits_a_journal_next_to_the_new_base(
    ready: Layout, copy: bytes
) -> None:
    """Intent, no stage, `events.sqlite` and a journal in place: refused."""
    crash_at(ready, "installed", copy, set_aside=True)
    (ready.perimeter_dir / JOURNAL).write_bytes(b"a journal from elsewhere")
    before = listing(ready)
    with pytest.raises(Unavailable, match="restore_incomplete"):
        perimeter.restore(ready, None)
    assert listing(ready) == before


@pytest.mark.parametrize("form", ["file", "link"])
def test_set_aside_must_be_our_directory(
    ready: Layout, copy: bytes, form: str, tmp_path: Path
) -> None:
    """A `set-aside` that is not a 0700 directory of the role is refused
    before the intent: the old pair is not moved."""
    hot_journal(ready)
    aside = ready.perimeter_dir / perimeter.SET_ASIDE
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    if form == "file":
        aside.write_bytes(b"leftover")
    else:
        aside.symlink_to(outside)
    before = listing(ready)
    with pytest.raises(Unavailable, match="set_aside"):
        perimeter.restore(ready, copy, set_aside_old=True)
    assert listing(ready) == before
    assert list(outside.iterdir()) == []
    assert "нужен человек" in perimeter.restore_text("set_aside")


def test_resume_refuses_a_link_as_set_aside_dir(
    ready: Layout, copy: bytes, tmp_path: Path
) -> None:
    """The intent's `set-aside/<UTC>` is a link: nothing is moved through it."""
    crash_at(ready, "intent", copy, set_aside=True)
    intent = perimeter._read_intent(ready.perimeter_dir)  # noqa: SLF001
    aside = ready.perimeter_dir / intent["set_aside_dir"]
    aside.parent.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    aside.symlink_to(outside)
    before = listing(ready)
    with pytest.raises(Unavailable, match="restore_incomplete"):
        perimeter.restore(ready, None)
    assert listing(ready) == before
    assert list(outside.iterdir()) == []


def test_restore_dir_removed_names_roles_setup(ready: Layout) -> None:
    """The whole `perimeter/` is gone (root's `perimeter.created` says it
    existed): `lost`, and restore refuses naming `roles setup` first -- in
    the role half a refusal, not a traceback."""
    ready.perimeter_created.parent.mkdir(parents=True, exist_ok=True)
    ready.perimeter_created.write_text("t\n")
    shutil.rmtree(ready.perimeter_dir)
    assert unavailable(ready) == "lost"
    assert "sudo darkfactory roles setup" in perimeter.LOST_TEXT
    with pytest.raises(Unavailable) as info:
        perimeter.restore(ready, None)
    assert info.value.reason == "perimeter_dir"
    text = perimeter.restore_text("perimeter_dir")
    assert text.index("roles setup") < text.index("perimeter restore")
    ready.install_dir.mkdir(parents=True, exist_ok=True)
    row = Account("df-publisher", os.geteuid(), os.getgid(), "ready", "t")
    ready.accounts.write_text(dump_accounts({"df-publisher": row}))
    result = role_half(ready, ["perimeter-restore", "--empty"], None)
    assert result.returncode == 2
    assert text.encode() in result.stderr
    assert b"Traceback" not in result.stderr
    assert not ready.perimeter_dir.exists()


@pytest.mark.parametrize(
    "text",
    [
        b"not json",
        b'{"set_aside_dir": "../../etc", "new_tmp": "events.sqlite.restore"}',
        b'{"set_aside_dir": "set-aside/20261028T000000.000000Z", "new_tmp": "x"}',
        b'{"set_aside_dir": "set-aside/20261028T000000.000000Z"}',
    ],
)
def test_malformed_intent_is_incomplete(ready: Layout, text: bytes) -> None:
    (ready.perimeter_dir / perimeter.RESTORE_INTENT).write_bytes(text)
    before = listing(ready)
    with pytest.raises(Unavailable, match="restore_incomplete"):
        perimeter.restore(ready, None)
    assert listing(ready) == before


# --- root's writer -----------------------------------------------------------------

RESTORE = ["perimeter", "restore", "--from", "/backup/events.sqlite"]


def test_restore_is_root_writer_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from df import state

    raw_snap = state.RawSnapshot(0, None, None, None, None, False)
    assert ("perimeter", "restore") in launcher_entry.WRITER_COMMANDS
    for argv in (RESTORE, ["perimeter", "restore", "--empty", "--set-aside-old"]):
        assert launcher_entry.mode_of(argv) == "writer"
        assert cli.parsed_mode(cli._parse(argv)) == "writer"  # noqa: SLF001
    assert launcher_entry.mode_of(["perimeter"]) == "reader"
    lay = Layout(Path(os.path.realpath(tmp_path)))
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    code = cli.main(
        RESTORE, mode="writer", read=Stable(raw_snap, 0), lock_fd=99, layout=lay
    )
    assert code == 2
    assert "требует root" in capsys.readouterr().err
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    code = cli.main(
        RESTORE, mode="reader", read=Stable(raw_snap, 0), lock_fd=None, layout=lay
    )
    assert code == 2
    assert "без блокировки" in capsys.readouterr().err
    monkeypatch.setattr(launcher_entry, "ROOT_UID", os.geteuid() + 1)
    assert launcher_entry.run(RESTORE, root=str(tmp_path)) == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["perimeter", "restore"],
        ["perimeter", "restore", "--set-aside-old"],
        ["perimeter", "restore", "--empty", "--from", "/x"],
        ["perimeter", "restore", "--from"],
        ["perimeter", "restore", "--empty", "extra"],
    ],
)
def test_restore_needs_exactly_one_source(argv: list[str]) -> None:
    with pytest.raises(cli.UsageError):
        cli._parse(argv)  # noqa: SLF001


def test_role_half_unreachable_by_model_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`publisher perimeter-restore` is reached only by root's `run_as`: no
    sudoers rule names the entry or it, `df-publish` runs `publisher serve
    <op>` whose operations exclude it, and the half itself refuses any
    user but the `df-publisher` of `accounts.toml`."""
    lay = Layout(Path(os.path.realpath(tmp_path)))
    publish = libexec.wrapper_path(lay, "libexec/df-publish")
    wrappers = {libexec.wrapper_path(lay, n) for n in libexec.WRAPPERS}
    for _caller, grants in sudoers.expected_grants(lay, "alice").items():
        for grant in grants:
            command = str(grant.command)
            assert "perimeter-restore" not in command
            assert Path(command.split()[0]) in wrappers, command
    text = libexec.wrapper_text(lay, "libexec/df-publish", Path("/py"))
    assert text.endswith('publisher serve "$@"\n')
    assert "perimeter-restore" not in cli.PUBLISHER_ARGS
    with pytest.raises(cli.UsageError):
        cli._parse(["publisher", "serve", "perimeter-restore"])  # noqa: SLF001
    assert str(publish) in sudoers.render(lay, "alice")
    lay.install_dir.mkdir(parents=True)
    lay.perimeter_dir.mkdir(parents=True, mode=0o700)
    row = Account("df-publisher", os.geteuid() + 1, os.getgid(), "ready", "t")
    lay.accounts.write_text(dump_accounts({"df-publisher": row}))
    code = role_half(lay, ["perimeter-restore", "--empty"], b"")
    assert code.returncode == 2
    assert b"df-publisher" in code.stderr
    assert os.listdir(lay.perimeter_dir) == []


def role_half(lay: Layout, words: list[str], stdin: bytes | None) -> plat.RunResult:
    """The entry's `publisher <words>` in this process (as `run_as` would run
    it as `df-publisher`), its streams captured."""
    from df import state

    raw_snap = state.RawSnapshot(0, None, None, None, None, False)
    out, err = io.StringIO(), io.StringIO()
    real = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin or b""))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                ["publisher", *words],
                mode="reader",
                read=Stable(raw_snap, 0),
                lock_fd=None,
                layout=lay,
            )
    finally:
        sys.stdin = real
    return plat.RunResult(code, out.getvalue().encode(), err.getvalue().encode(), True)


@dataclass
class Restore:
    """An installed world with a perimeter and the role's `run_as` faked."""

    w: World
    copy_path: Path
    calls: list[tuple[str, list[str], dict[str, object]]] = field(default_factory=list)

    @property
    def lay(self) -> Layout:
        return self.w.lay

    def journal_rows(self) -> list[tuple[object, object]]:
        rows_ = journal.read_journal(self.lay.journal)
        return [
            (r["event"], r["detail"]) for r in rows_ if r["step"] == "perimeter-restore"
        ]


@pytest.fixture()
def rw(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> Restore:
    monkeypatch.setattr(fsutil, "boot_id", lambda: BOOT)
    world = make_world(fake_layout, monkeypatch)
    installed(world)
    lay = world.lay
    accounts = load_accounts(lay.accounts)
    accounts["df-publisher"] = Account(
        "df-publisher", os.geteuid(), os.getgid(), "ready", "t"
    )
    fsutil.write_atomic(lay.accounts, dump_accounts(accounts))
    lay.perimeter_dir.mkdir(parents=True, mode=0o700)
    perimeter.create_db(lay)
    record_in(lay, 1)
    copy_path = lay.root / "backup.sqlite"
    copy_path.write_bytes(db_file(lay).read_bytes())
    record_in(lay, 2)
    setup = Restore(world, copy_path)

    def run_as(user: str, argv: list[str], **kw: object) -> plat.RunResult:
        setup.calls.append((user, list(argv), kw))
        assert argv[1:5] == ["-I", "-B", "-S", str(lay.entry)]
        assert argv[5] == "publisher"
        stdin = kw.get("stdin")
        assert stdin is None or isinstance(stdin, bytes)
        return role_half(lay, list(argv[6:]), stdin)

    monkeypatch.setattr(plat, "run_as", run_as)
    return setup


def test_cli_restore_from_copy(rw: Restore, capsys: pytest.CaptureFixture[str]) -> None:
    gen = journal.read_generation(rw.lay)
    argv = ["perimeter", "restore", "--from", str(rw.copy_path)]
    assert rw.w.writer(argv) == 0
    assert "restored_pending" in capsys.readouterr().out
    assert admission_of(rw.lay) == "restored_pending"
    assert rows(rw.lay) == PENDING
    [(user, args, kw)] = rw.calls
    assert user == roles_setup.PUBLISHER_USER
    assert args[6:] == ["perimeter-restore", "--stdin"]
    assert kw["stdin"] == rw.copy_path.read_bytes()
    assert kw["network"] is False and kw["cwd"] == Path("/")
    assert rw.journal_rows() == [("done", f"--from {rw.copy_path}: restored")]
    assert journal.read_generation(rw.lay) == gen  # no /etc/df change


def test_cli_restore_empty_set_aside(rw: Restore) -> None:
    hot_journal(rw.lay)
    assert rw.w.writer(["perimeter", "restore", "--empty", "--set-aside-old"]) == 0
    [(_user, args, kw)] = rw.calls
    assert args[6:] == ["perimeter-restore", "--empty", "--set-aside-old"]
    assert kw["stdin"] is None
    assert admission_of(rw.lay) == "restored_pending"
    assert rows(rw.lay) == []
    assert (aside_dir(rw.lay) / JOURNAL).exists()
    assert rw.journal_rows() == [("done", "--empty: restored")]


def test_cli_restore_untrusted_path_refused(
    rw: Restore, capsys: pytest.CaptureFixture[str]
) -> None:
    rw.w.untrusted.add(rw.copy_path)
    before = files(rw.w)
    argv = ["perimeter", "restore", "--from", str(rw.copy_path)]
    assert rw.w.writer(argv) == 2
    assert "untrusted" in capsys.readouterr().err
    assert rw.calls == []
    assert files(rw.w) == before


def test_cli_restore_relative_path_refused(rw: Restore) -> None:
    assert rw.w.writer(["perimeter", "restore", "--from", "backup.sqlite"]) == 2
    assert rw.calls == []


def test_cli_restore_oversize_copy_refused(
    rw: Restore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    size = rw.copy_path.stat().st_size
    monkeypatch.setattr(perimeter, "MAX_COPY", size - 1)
    before = files(rw.w)
    argv = ["perimeter", "restore", "--from", str(rw.copy_path)]
    assert rw.w.writer(argv) == 2
    assert f"больше {size - 1} байт" in capsys.readouterr().err
    assert rw.calls == []
    assert files(rw.w) == before
    assert admission_of(rw.lay) == "ready"


def test_cli_restore_role_refusal_is_reported(
    rw: Restore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The role's refusal (here: the barrier held past the wait) is root's
    refusal with the role's text, and a `failed` journal line."""
    monkeypatch.setattr(perimeter, "RESTORE_WAIT", 0.1)
    argv = ["perimeter", "restore", "--from", str(rw.copy_path)]
    with holding(rw.lay, fcntl.LOCK_SH):
        assert rw.w.writer(argv) == 2
    err = capsys.readouterr().err
    assert perimeter.RESTORE_TEXTS["busy"] in err
    [(event, detail)] = rw.journal_rows()
    assert event == "failed" and "код 2" in str(detail)
    assert admission_of(rw.lay) == "ready"


def test_cli_restore_launch_failure_is_refused(
    rw: Restore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_as(_user: str, _argv: list[str], **_kw: object) -> plat.RunResult:
        return plat.RunResult(1, b"", b"sudo: a password is required\n", False)

    monkeypatch.setattr(plat, "run_as", run_as)
    assert rw.w.writer(["perimeter", "restore", "--empty"]) == 2
    assert rw.journal_rows()[0][0] == "failed"
    assert admission_of(rw.lay) == "ready"


def test_lost_text_names_the_command() -> None:
    assert "sudo darkfactory perimeter restore --from <копия>" in perimeter.LOST_TEXT
    assert "--set-aside-old" in perimeter.RESTORE_TEXTS["journal"]
    assert f"{perimeter.RESTORE_WAIT:g} с" in perimeter.RESTORE_TEXTS["busy"]


def test_resumed_text_names_either_source() -> None:
    assert "копия или --empty" in cli._RESTORE_DONE["resumed"]  # noqa: SLF001
