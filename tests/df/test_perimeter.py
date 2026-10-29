"""The perimeter event store (spec 6.1; stage 2a, Task 11)."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import multiprocessing as mp
import os
import sqlite3
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from df.paths import Layout
from df.publisher import perimeter, store
from df.publisher.perimeter import Root, Snapshot, Unavailable
from df.publisher.request import Request, Response, perimeter_result
from df.stage2_config import TrustBinding
from tests.df.conftest import FakeLayout
from tests.df.launch_world import put_record, record
from tests.df.publisher_world import World, build_world, make_req, raw_of

ROOT: Root = ("github.com", 5, "main")
OTHER: Root = ("github.com", 8, "release")
BINDING = TrustBinding("github.com", 5, "o/df-trust", "main", 6, "public")
S1, S2, S3 = "1" * 40, "2" * 40, "3" * 40
EXEC = 601


class Epochs:
    """A fake epoch source: one head for every root unless set per root."""

    def __init__(self, default: Snapshot | Unavailable | None = None) -> None:
        self.default = default or Unavailable("epochs_unavailable")
        self.heads: dict[Root, Snapshot | Unavailable] = {}
        self.calls = 0

    def head(self, root: Root) -> Snapshot | Unavailable:
        self.calls += 1
        return self.heads.get(root, self.default)

    def at(self, sha: str, epoch: int) -> Epochs:
        self.default = Snapshot(sha, epoch)
        return self


DOWN = Epochs()


def at(sha: str, epoch: int) -> Epochs:
    return Epochs(Snapshot(sha, epoch))


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    layout = Layout(Path(os.path.realpath(tmp_path)) / "root")
    layout.perimeter_dir.mkdir(parents=True, mode=0o700)
    return layout


@pytest.fixture()
def ready(lay: Layout) -> Layout:
    assert perimeter.create_db(lay) == "created"
    return lay


def db_file(lay: Layout) -> Path:
    return lay.perimeter_dir / perimeter.DB_NAME


def raw(lay: Layout) -> sqlite3.Connection:
    """A plain connection to the database (the test's own hands)."""
    return sqlite3.connect(db_file(lay), isolation_level=None)


def violation(n: int = 1, **over: object) -> Request:
    root = {"host": ROOT[0], "repository_id": ROOT[1], "branch": ROOT[2]}
    fields: dict[str, object] = {"request_id": f"req-viol-{n:04d}", "root_id": root}
    return make_req("record-violation", **{**fields, **over})


def status_req(sha: str, n: int = 1, root: Root = ROOT) -> Request:
    rid = {"host": root[0], "repository_id": root[1], "branch": root[2]}
    return make_req(
        "perimeter-status",
        request_id=f"req-stat-{n:04d}",
        root_id=rid,
        expected_snapshot_sha=sha,
    )


def record_in(lay: Layout, n: int = 1, epochs: Epochs = DOWN) -> Response:
    with perimeter.open_db(lay) as db:
        return perimeter.record_violation(
            db.conn, EXEC, violation(n), f"key-{n}", epochs, BINDING
        )


def status(lay: Layout, epochs: Epochs, sha: str | None = None) -> Response:
    with perimeter.open_db(lay) as db:
        try:
            perimeter.admit(db, epochs, ROOT)
        except Unavailable as exc:
            return perimeter.status_unavailable(exc.reason)
        head = epochs.head(ROOT)
        want = sha or (head.sha if isinstance(head, Snapshot) else S1)
        return perimeter.perimeter_status(db.conn, status_req(want), epochs)


def state_of(resp: Response) -> str:
    return str(resp.result["state"])


def rows(lay: Layout, sql: str = "SELECT state, epoch FROM events") -> list[tuple]:
    conn = raw(lay)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def unavailable(lay: Layout) -> str:
    with pytest.raises(Unavailable) as info:
        perimeter.open_db(lay).close()
    return info.value.reason


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- life cycle ------------------------------------------------------------------


def test_create_db_makes_the_files(ready: Layout) -> None:
    base = ready.perimeter_dir
    for name in (perimeter.DB_NAME, perimeter.MARKER, perimeter.LOCK):
        st = os.lstat(base / name)
        assert stat.S_ISREG(st.st_mode), name
        assert stat.S_IMODE(st.st_mode) == 0o600, name
    assert not (base / f"{perimeter.DB_NAME}-journal").exists()
    with perimeter.open_db(ready) as db:
        assert perimeter.admission(db.conn) == "ready"
        assert db.conn.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_create_db_is_idempotent(ready: Layout) -> None:
    record_in(ready)
    before = digest(db_file(ready))
    assert perimeter.create_db(ready) == "present"
    assert digest(db_file(ready)) == before


def test_create_db_never_replaces_a_lost_database(ready: Layout) -> None:
    db_file(ready).unlink()
    with pytest.raises(Unavailable) as info:
        perimeter.create_db(ready)
    assert info.value.reason == "lost"
    assert not db_file(ready).exists()


def test_create_db_finishes_an_interrupted_creation(ready: Layout) -> None:
    record_in(ready)
    (ready.perimeter_dir / perimeter.MARKER).unlink()
    assert perimeter.create_db(ready) == "created"
    assert (ready.perimeter_dir / perimeter.MARKER).exists()
    assert rows(ready) == [("pending", None)]


def test_create_db_sweeps_a_leftover_temporary(lay: Layout) -> None:
    tmp = lay.perimeter_dir / perimeter.NEW_DB
    tmp.write_bytes(b"half a database")
    assert perimeter.create_db(lay) == "created"
    assert not tmp.exists()
    assert unavailable_or_none(lay) is None


def unavailable_or_none(lay: Layout) -> str | None:
    try:
        perimeter.open_db(lay).close()
    except Unavailable as exc:
        return exc.reason
    return None


def test_create_db_refuses_a_foreign_directory(lay: Layout) -> None:
    os.chmod(lay.perimeter_dir, 0o750)
    with pytest.raises(OSError, match="0700"):
        perimeter.create_db(lay)
    assert os.listdir(lay.perimeter_dir) == []


def test_open_never_creates(lay: Layout) -> None:
    base = lay.perimeter_dir
    assert unavailable(lay) == "not_initialized"
    assert os.listdir(base) == []
    assert perimeter.create_db(lay) == "created"
    db_file(lay).unlink()
    assert unavailable(lay) == "lost"
    assert not db_file(lay).exists()
    (base / perimeter.LOCK).unlink()
    assert unavailable(lay) == "lost"
    assert sorted(os.listdir(base)) == [perimeter.MARKER]
    lay.perimeter_created.write_text("t\n")  # root's record outside perimeter/
    (base / perimeter.MARKER).unlink()
    assert unavailable(lay) == "lost"
    lay.perimeter_created.unlink()
    assert unavailable(lay) == "not_initialized"
    assert os.listdir(base) == []


def test_open_never_creates_in_a_race(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database vanishes between the existence check and the connect:
    the connect itself must not create it either."""
    real = os.lstat
    target = db_file(ready)

    def lstat(path: object, *a: object, **kw: object) -> os.stat_result:
        st = real(path, *a, **kw)  # type: ignore[arg-type]
        if Path(str(path)) == target:
            target.unlink()
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    reason = unavailable(ready)
    monkeypatch.undo()
    assert reason.startswith("sqlite: ")
    assert not target.exists()


def test_whole_directory_lost_is_lost(ready: Layout) -> None:
    """`perimeter/` gone as a whole: root's `perimeter.created` says it was
    created; neither open nor create makes a new database."""
    ready.perimeter_created.write_text("t\n")
    for name in os.listdir(ready.perimeter_dir):
        (ready.perimeter_dir / name).unlink()
    assert unavailable(ready) == "lost"
    with pytest.raises(Unavailable, match="lost"):
        perimeter.create_db(ready)
    assert os.listdir(ready.perimeter_dir) == [perimeter.LOCK]  # the lock only


def test_lock_missing_next_to_the_database(ready: Layout) -> None:
    (ready.perimeter_dir / perimeter.LOCK).unlink()
    assert unavailable(ready) == "lock_missing"
    assert perimeter.create_db(ready) == "present"  # recreates the lock
    assert unavailable_or_none(ready) is None


def test_create_db_never_builds_next_to_an_orphan_journal(lay: Layout) -> None:
    journal = lay.perimeter_dir / perimeter.JOURNAL
    journal.write_bytes(b"pages of an old database")
    with pytest.raises(Unavailable, match="integrity"):
        perimeter.create_db(lay)
    assert not db_file(lay).exists()
    assert journal.read_bytes() == b"pages of an old database"


def test_io_errors_are_unavailable(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.lstat

    def lstat(path: object, *a: object, **kw: object) -> os.stat_result:
        if Path(str(path)) == db_file(ready):
            raise PermissionError(13, "Permission denied")
        return real(path, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", lstat)
    assert unavailable(ready) == "io: Permission denied"
    got = perimeter.stats(ready)
    assert isinstance(got, Unavailable) and got.reason == "io: Permission denied"
    monkeypatch.undo()

    def statvfs(_path: object) -> os.statvfs_result:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "statvfs", statvfs)
    got = perimeter.stats(ready)
    assert isinstance(got, Unavailable) and got.reason == "io: Input/output error"


def test_open_without_marker_is_not_lost(ready: Layout) -> None:
    """A database whose marker is missing still opens (the marker only
    distinguishes "never created" from "lost")."""
    (ready.perimeter_dir / perimeter.MARKER).unlink()
    assert unavailable_or_none(ready) is None


def test_restore_intent_keeps_the_database_closed(ready: Layout) -> None:
    db_file(ready).write_bytes(b"not a database at all")
    (ready.perimeter_dir / perimeter.RESTORE_INTENT).write_text("{}")
    assert unavailable(ready) == "restore_incomplete"
    assert db_file(ready).read_bytes() == b"not a database at all"


def test_garbage_database_is_integrity(ready: Layout) -> None:
    db_file(ready).write_bytes(b"not a database at all" * 100)
    assert unavailable(ready) == "integrity"


def test_database_must_be_a_regular_file(ready: Layout) -> None:
    db_file(ready).unlink()
    os.symlink("/dev/null", db_file(ready))
    assert unavailable(ready) == "integrity"


# --- pragmas ---------------------------------------------------------------------


def test_pragmas_effective(ready: Layout) -> None:
    with perimeter.open_db(ready) as db:
        conn = db.conn
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert conn.execute("PRAGMA synchronous").fetchone() == (3,)
        busy = conn.execute("PRAGMA busy_timeout").fetchone()
        assert busy == (perimeter.BUSY_TIMEOUT_MS,)
        if sys.platform == "darwin":
            assert conn.execute("PRAGMA fullfsync").fetchone() == (1,)
            assert conn.execute("PRAGMA checkpoint_fullfsync").fetchone() == (1,)


def test_pragma_not_taking_effect_is_unavailable(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = [p if p[0] != "synchronous" else ("synchronous", "EXTRA", 2)
             for p in perimeter.pragmas()]  # fmt: skip
    monkeypatch.setattr(perimeter, "pragmas", lambda: table)
    assert unavailable(ready) == "pragma: synchronous"


def test_old_sqlite_is_refused(ready: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perimeter, "SQLITE_VERSION", (3, 30, 1))  # no nofollow
    assert unavailable(ready) == "sqlite_too_old"
    with pytest.raises(Unavailable, match="sqlite_too_old"):
        perimeter.create_db(ready)


# --- integrity -------------------------------------------------------------------


def _set_sql(lay: Layout, name: str, sql: str) -> None:
    conn = raw(lay)
    try:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=? WHERE name=?", (sql, name))
    finally:
        conn.close()


def _sql_of(lay: Layout, name: str) -> str:
    return str(rows(lay, f"SELECT sql FROM sqlite_master WHERE name='{name}'")[0][0])


def _copy_row(lay: Layout, **over: object) -> None:
    """Insert a copy of event 1 with columns replaced (a new event_id)."""
    conn = raw(lay)
    try:
        cur = conn.execute("SELECT * FROM events WHERE event_id = 1")
        names = [d[0] for d in cur.description]
        row = dict(zip(names, cur.fetchone(), strict=True))
        row.pop("event_id")
        row.update(over)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO events ({cols}) VALUES ({marks})", list(row.values())
        )
    finally:
        conn.close()


def test_corrupt_index_is_integrity(ready: Layout) -> None:
    record_in(ready)
    good = _sql_of(ready, "events_root_state")
    _set_sql(ready, "events_root_state", good.replace("state)", "detail)"))
    _copy_row(ready, request_key="key-2")
    _set_sql(ready, "events_root_state", good)
    before = digest(db_file(ready))
    assert unavailable(ready) == "integrity"
    assert digest(db_file(ready)) == before


def test_duplicate_unique_is_integrity(ready: Layout) -> None:
    record_in(ready)
    good = _sql_of(ready, "events_request_key")
    assert good.startswith("CREATE UNIQUE INDEX")
    _set_sql(ready, "events_request_key", good.replace("UNIQUE ", ""))
    _copy_row(ready)  # the same request_key
    _set_sql(ready, "events_request_key", good)
    before = digest(db_file(ready))
    assert unavailable(ready) == "integrity"
    assert digest(db_file(ready)) == before


def test_dropped_unique_index_is_integrity(ready: Layout) -> None:
    conn = raw(ready)
    conn.execute("DROP INDEX events_request_key")
    conn.close()
    assert unavailable(ready) == "integrity"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE events SET state = 'forgotten'",
        "UPDATE events SET state = 'registered', epoch = NULL",
        "UPDATE events SET epoch = 3",  # pending with an epoch
        (
            "INSERT INTO transitions (event_id, transition, at, epoch, provenance,"
            " actor, evidence)"
            " VALUES (99, 'registered', 't', 1, 'p', 'publisher', '{}')"
        ),
        "UPDATE meta SET value = 'maybe' WHERE key = 'admission'",
        "DELETE FROM meta WHERE key = 'schema'",
    ],
)
def test_invariant_violation_is_integrity(ready: Layout, sql: str) -> None:
    record_in(ready)
    conn = raw(ready)
    conn.execute(sql)
    conn.close()
    before = digest(db_file(ready))
    assert unavailable(ready) == "integrity"
    assert digest(db_file(ready)) == before


def test_more_than_max_events_is_unavailable(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The event past the limit is kept (never dropped): from then on the
    store is `unavailable: too_many_events` until a human decides."""
    monkeypatch.setattr(perimeter, "MAX_EVENTS", 2)
    for n in (1, 2):
        record_in(ready, n, epochs=at(S1, 1))
    third = record_in(ready, 3, epochs=at(S1, 1))
    assert (third.outcome, third.result["state"]) == ("done", "registered")
    assert len(rows(ready)) == 3
    assert unavailable(ready) == "too_many_events"


def test_more_than_max_events_status_through_the_op(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perimeter, "MAX_EVENTS", 1)
    perimeter.EPOCHS.at(S1, 1)  # type: ignore[attr-defined]
    frame_record(world, capsys)
    _, resp = frame_record(world, capsys, request_id="req-viol-0002")
    assert (resp.outcome, resp.result["state"]) == ("done", "registered")
    assert len(rows(world.lay)) == 2
    perimeter.EPOCHS.at(S2, 2)  # type: ignore[attr-defined]
    _, resp = frame_status(world, capsys, expected_snapshot_sha=S2)
    assert resp.result == perimeter_result("unavailable", "too_many_events", None, None)


# --- record-violation and perimeter-status ---------------------------------------


def test_detection_to_unblock_flow(ready: Layout) -> None:
    """Spec 6.1: detection, epochs unavailable, pending, restart without a new
    epoch, epochs back, status registers, a new epoch unblocks."""
    first = record_in(ready)
    assert (first.outcome, first.result["state"]) == ("done", "pending")
    assert first.result["epoch"] is None
    trust = first.evidence["trust_root"]
    assert trust == {
        "host": "github.com",
        "repository_id": 5,
        "branch": "main",
        "snapshot_sha": None,
        "unavailable": "epochs_unavailable",
    }
    down = status(ready, DOWN)
    assert down.result == {
        "state": "unavailable",
        "reason": "epochs_unavailable",
        "snapshot_sha": None,
        "last_epoch": None,
    }
    assert rows(ready) == [("pending", None)]
    up = status(ready, at(S1, 4))
    assert (state_of(up), up.result["snapshot_sha"], up.result["last_epoch"]) == (
        "blocked",
        S1,
        4,
    )
    assert up.result["events"] == [first.result["event_id"]]
    assert rows(ready) == [("registered", 4)]
    assert state_of(status(ready, at(S1, 4))) == "blocked"  # restart, same epoch
    assert state_of(status(ready, at(S2, 5))) == "clear"
    assert rows(ready, "SELECT transition, epoch FROM transitions") == [
        ("registered", 4)
    ]


def test_epoch_moves_between_detection_and_registration(ready: Layout) -> None:
    resp = record_in(ready, epochs=at(S2, 2))  # observed in E1, registered in E2
    assert (resp.result["state"], resp.result["epoch"]) == ("registered", 2)
    assert resp.evidence["trust_root"]["snapshot_sha"] == S2  # type: ignore[index]
    assert state_of(status(ready, at(S2, 2))) == "blocked"
    assert state_of(status(ready, at(S3, 3))) == "clear"


def test_events_of_another_root_do_not_block(ready: Layout) -> None:
    record_in(ready, epochs=at(S1, 1))
    with perimeter.open_db(ready) as db:
        resp = perimeter.perimeter_status(
            db.conn, status_req(S1, root=OTHER), at(S1, 1)
        )
    assert state_of(resp) == "clear"


def test_stale_snapshot_names_the_head_and_changes_nothing(ready: Layout) -> None:
    record_in(ready)
    resp = status(ready, at(S2, 2), sha=S1)
    assert (resp.outcome, resp.reason) == ("stale_snapshot", "snapshot_moved")
    assert resp.result == {"snapshot_sha": S2}
    assert rows(ready) == [("pending", None)]


def test_client_epoch_has_no_field() -> None:
    from df.publisher import request

    for op in ("record-violation", "perimeter-status"):
        with pytest.raises(request.Refused, match="unknown field 'epoch'"):
            request.parse(op, raw_of(op, epoch=1))


def test_repeat_after_commit_returns_the_same_event(ready: Layout) -> None:
    first = record_in(ready, epochs=at(S1, 1))
    moved = at(S2, 2)
    again = record_in(ready, epochs=moved)  # the answer was lost; epoch moved
    assert again.result == first.result
    assert rows(ready) == [("registered", 1)]
    assert moved.calls == 0  # a replay never reads a new epoch


def test_violated_repository_is_the_bindings(ready: Layout) -> None:
    with perimeter.open_db(ready) as db:
        perimeter.record_violation(
            db.conn, EXEC, violation(1, source="approval-policy"), "k", DOWN, BINDING
        )
    got = rows(ready, "SELECT violated_role, violated_repository_id FROM events")
    assert got == [("approval-policy", 6)]


class LockProbe:
    """An epoch source that records whether the database's write lock was
    held when the head was read (another connection cannot take it)."""

    def __init__(self, lay: Layout) -> None:
        self.lay = lay
        self.held: list[bool] = []

    def head(self, _root: Root) -> Snapshot | Unavailable:
        conn = sqlite3.connect(db_file(self.lay), isolation_level=None, timeout=0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
            self.held.append(False)
        except sqlite3.OperationalError:
            self.held.append(True)
        finally:
            conn.close()
        return Snapshot(S1, 1)


def test_head_is_read_with_the_write_lock_held(ready: Layout) -> None:
    probe = LockProbe(ready)
    with perimeter.open_db(ready) as db:
        perimeter.record_violation(db.conn, EXEC, violation(1), "k1", probe, BINDING)
        perimeter.perimeter_status(db.conn, status_req(S1), probe)
    assert probe.held == [True, True]
    record_in(ready, 2)  # pending
    with perimeter.open_db(ready) as db:
        perimeter.perimeter_status(db.conn, status_req(S1), probe)
    assert probe.held == [True, True, True]
    assert rows(ready) == [("registered", 1), ("registered", 1)]


def test_busy_is_unavailable(ready: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perimeter, "BUSY_TIMEOUT_MS", 50)
    record_in(ready)
    holder = raw(ready)
    holder.execute("BEGIN IMMEDIATE")
    try:
        resp = status(ready, at(S1, 1))
        assert resp.result["state"] == "unavailable"
        assert resp.result["reason"] == "busy"
        with pytest.raises(Unavailable, match="busy"):
            record_in(ready, 2)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert rows(ready) == [("pending", None)]


def test_barrier_wait_is_bounded(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perimeter, "BARRIER_WAIT", 0.1)
    fd = os.open(ready.perimeter_dir / perimeter.LOCK, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert unavailable(ready) == "busy"
    finally:
        os.close(fd)


def test_open_connection_holds_the_shared_barrier(ready: Layout) -> None:
    fd = os.open(ready.perimeter_dir / perimeter.LOCK, os.O_RDONLY)
    try:
        with perimeter.open_db(ready):
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)  # readers share it
            fcntl.flock(fd, fcntl.LOCK_UN)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # closed: released
    finally:
        os.close(fd)


def test_barrier_taken_before_connect(
    ready: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real_flock, real_connect = fcntl.flock, sqlite3.connect

    def flock(fd: int, op: int) -> None:
        seen.append("flock")
        real_flock(fd, op)

    def connect(*a: object, **kw: object) -> sqlite3.Connection:
        seen.append("connect")
        return real_connect(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(perimeter.fcntl, "flock", flock)
    monkeypatch.setattr(perimeter.sqlite3, "connect", connect)
    perimeter.open_db(ready).close()
    assert seen[:2] == ["flock", "connect"]


# --- processes -------------------------------------------------------------------


def _ctx() -> mp.context.BaseContext:
    return mp.get_context("fork")


def _record_child(root: str, n: int, barrier: object, out: object) -> None:
    lay = Layout(Path(root))
    barrier.wait()  # type: ignore[attr-defined]
    resp = record_in(lay, n)
    out.put((resp.outcome, resp.result.get("event_id")))  # type: ignore[attr-defined]


def _status_child(root: str, barrier: object, out: object) -> None:
    lay = Layout(Path(root))
    barrier.wait()  # type: ignore[attr-defined]
    resp = status(lay, at(S1, 1))
    out.put((resp.outcome, resp.result.get("state")))  # type: ignore[attr-defined]


def _run_pair(target: Callable[..., None], args: list[tuple]) -> list[object]:
    ctx = _ctx()
    barrier, out = ctx.Barrier(len(args), timeout=10), ctx.Queue()
    procs = [ctx.Process(target=target, args=(*a, barrier, out)) for a in args]
    for p in procs:
        p.start()
    got = [out.get(timeout=30) for _ in procs]
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0
    return got


def test_two_concurrent_events(ready: Layout) -> None:
    got = _run_pair(_record_child, [(str(ready.root), 1), (str(ready.root), 2)])
    assert sorted(g[0] for g in got) == ["done", "done"]  # type: ignore[index]
    assert len({g[1] for g in got}) == 2  # type: ignore[index]
    assert rows(ready) == [("pending", None), ("pending", None)]


def test_two_concurrent_statuses_register_once(ready: Layout) -> None:
    record_in(ready, 1)
    record_in(ready, 2)
    got = _run_pair(_status_child, [(str(ready.root),), (str(ready.root),)])
    assert got == [("done", "blocked"), ("done", "blocked")]
    assert rows(ready) == [("registered", 1), ("registered", 1)]
    count = rows(ready, "SELECT event_id, count(*) FROM transitions GROUP BY event_id")
    assert [c for _e, c in count] == [1, 1]


def _crash_in_transaction(root: str) -> None:
    lay = Layout(Path(root))
    db = perimeter.open_db(lay)
    db.conn.execute("BEGIN IMMEDIATE")
    db.conn.execute("UPDATE events SET detail = 'half'")
    db.conn.execute("DELETE FROM events")
    os._exit(0)


def test_writer_crash_keeps_the_committed_event(ready: Layout) -> None:
    first = record_in(ready)
    p = _ctx().Process(target=_crash_in_transaction, args=(str(ready.root),))
    p.start()
    p.join(timeout=30)
    assert p.exitcode == 0
    journal = ready.perimeter_dir / f"{perimeter.DB_NAME}-journal"
    assert journal.exists()  # a hot journal: the transaction never ended
    resp = status(ready, at(S1, 1))
    assert resp.result["events"] == [first.result["event_id"]]
    assert not journal.exists()  # rolled back by the publisher's open
    assert rows(ready, "SELECT detail FROM events") == [("the head moved",)]


# --- restored copy ---------------------------------------------------------------


def restored(lay: Layout) -> None:
    """Mark the database as a restored copy (what restore leaves)."""
    conn = raw(lay)
    conn.execute("UPDATE meta SET value = 'restored_pending' WHERE key = 'admission'")
    conn.close()


def test_restore_stale_copy_blocks(ready: Layout) -> None:
    """A sound stale copy without the last event: once epochs are available,
    a conservative event per root (the events' and the binding's) blocks until
    the next epoch."""
    with perimeter.open_db(ready) as db:  # an event of an old root
        req = violation(1, root_id={"host": OTHER[0], "repository_id": OTHER[1],
                                    "branch": OTHER[2]})  # fmt: skip
        old = TrustBinding("github.com", 8, "o/old", "release", 6, "public")
        perimeter.record_violation(db.conn, EXEC, req, "k-old", at(S1, 1), old)
    restored(ready)
    assert status(ready, DOWN).result["reason"] == "restore_pending"
    resp = status(ready, at(S1, 1))
    assert state_of(resp) == "blocked"
    got = rows(
        ready,
        "SELECT root_repository_id, state, epoch, detected_by FROM events "
        "WHERE detected_by = 'db_restore' ORDER BY root_repository_id",
    )
    assert got == [
        (5, "registered", 1, "db_restore"),
        (8, "registered", 1, "db_restore"),
    ]
    with perimeter.open_db(ready) as db:
        assert perimeter.admission(db.conn) == "ready"
    assert state_of(status(ready, at(S1, 1))) == "blocked"  # no second event
    assert len(rows(ready)) == 3
    assert state_of(status(ready, at(S2, 2))) == "clear"


def test_restore_pending_without_epochs_unavailable(ready: Layout) -> None:
    restored(ready)
    with perimeter.open_db(ready) as db:
        with pytest.raises(Unavailable, match="restore_pending"):
            perimeter.admit(db, DOWN, ROOT)
        with pytest.raises(Unavailable, match="restore_pending"):
            perimeter.admit(db, perimeter.NO_BINDING, ROOT)
    assert rows(ready) == []
    resp = status(ready, DOWN)
    assert resp.result == {
        "state": "unavailable",
        "reason": "restore_pending",
        "snapshot_sha": None,
        "last_epoch": None,
    }


def test_restore_partly_known_epochs_change_nothing(ready: Layout) -> None:
    record_in(ready, epochs=at(S1, 1))
    _copy_row(ready, request_key="k-other", root_repository_id=8,
              root_branch="release")  # fmt: skip
    restored(ready)
    epochs = at(S1, 1)
    epochs.heads[OTHER] = Unavailable("epochs_unavailable")
    with perimeter.open_db(ready) as db:
        with pytest.raises(Unavailable, match="restore_pending"):
            perimeter.admit(db, epochs, ROOT)
        assert perimeter.admission(db.conn) == "restored_pending"
    assert len(rows(ready)) == 2


class _CrashOnSecond:
    def __init__(self) -> None:
        self.calls = 0

    def head(self, _root: Root) -> Snapshot | Unavailable:
        self.calls += 1
        if self.calls == 2:
            os._exit(0)  # between the two inserts
        return Snapshot(S1, 1)


def _restore_crash(root: str) -> None:
    lay = Layout(Path(root))
    with perimeter.open_db(lay) as db:
        perimeter.admit(db, _CrashOnSecond(), ROOT)


def test_restore_interrupted(ready: Layout) -> None:
    _copy_row_root = {"host": OTHER[0], "repository_id": OTHER[1], "branch": OTHER[2]}
    with perimeter.open_db(ready) as db:
        old = TrustBinding("github.com", 8, "o/old", "release", 6, "public")
        perimeter.record_violation(
            db.conn, EXEC, violation(1, root_id=_copy_row_root), "k", at(S1, 1), old
        )
    restored(ready)
    p = _ctx().Process(target=_restore_crash, args=(str(ready.root),))
    p.start()
    p.join(timeout=30)
    assert p.exitcode == 0
    with perimeter.open_db(ready) as db:
        assert perimeter.admission(db.conn) == "restored_pending"
    assert rows(
        ready, "SELECT count(*) FROM events WHERE detected_by = 'db_restore'"
    ) == [(0,)]
    assert state_of(status(ready, at(S1, 1))) == "blocked"
    assert rows(
        ready, "SELECT count(*) FROM events WHERE detected_by = 'db_restore'"
    ) == [(2,)]


# --- volume ----------------------------------------------------------------------


@pytest.mark.parametrize("pending", [False, True])
def test_ten_thousand_events_within_budget(ready: Layout, pending: bool) -> None:
    """Registered rows (a read), or pending rows all registered in the one
    transaction of the status."""
    record_in(ready, epochs=DOWN if pending else at(S1, 1))
    conn = raw(ready)
    cur = conn.execute("SELECT * FROM events WHERE event_id = 1")
    names = [d[0] for d in cur.description][1:]
    first = cur.fetchone()[1:]
    key = names.index("request_key")
    conn.execute("BEGIN")
    conn.executemany(
        f"INSERT INTO events ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        (
            [*first[:key], f"bulk-{i}", *first[key + 1 :]]
            for i in range(perimeter.MAX_EVENTS - 1)
        ),
    )
    conn.execute("COMMIT")
    conn.close()
    start = time.monotonic()
    resp = status(ready, at(S2, 2))
    took = time.monotonic() - start
    kind = "pending" if pending else "registered"
    print(f"perimeter_status over {perimeter.MAX_EVENTS} {kind}: {took:.3f} s")
    assert state_of(resp) == ("blocked" if pending else "clear")
    if pending:
        assert len(cast(list[int], resp.result["events"])) == perimeter.MAX_EVENTS
    assert took < 2.0


# --- stats -----------------------------------------------------------------------


def test_stats(ready: Layout) -> None:
    record_in(ready)
    got = perimeter.stats(ready)
    assert not isinstance(got, Unavailable)
    assert got.events == 1
    assert got.db_bytes == db_file(ready).stat().st_size
    assert got.free_bytes > 0


def test_stats_unavailable(lay: Layout) -> None:
    got = perimeter.stats(lay)
    assert isinstance(got, Unavailable) and got.reason == "not_initialized"


def test_no_binding_source() -> None:
    got = perimeter.NO_BINDING.head(ROOT)
    assert isinstance(got, Unavailable) and got.reason == "no_binding"


def test_store_does_not_replay_status() -> None:
    assert "perimeter-status" in store.NOT_REPLAYED
    assert "record-violation" not in store.NOT_REPLAYED


# --- through the frame -----------------------------------------------------------

TRUST = (
    'schema = "df/trust/v1"\n'
    'host = "github.com"\n'
    "df_trust_repository_id = {repo}\n"
    'df_trust_full_name = "o/df-trust"\n'
    'df_trust_branch = "{branch}"\n'
    "approval_policy_repository_id = 6\n"
    'transport = "public"\n'
)


@pytest.fixture()
def world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
    w = build_world(fake_layout, monkeypatch)
    w.lay.perimeter_dir.mkdir(mode=0o700)
    perimeter.create_db(w.lay)
    (w.lay.etc / "trust.toml").write_text(TRUST.format(repo=5, branch="main"))
    monkeypatch.setattr(perimeter, "EPOCHS", Epochs())
    return w


def frame_record(
    world: World, capsys: pytest.CaptureFixture[str], **over: object
) -> tuple[int, Response]:
    raw = raw_of("record-violation", **over)
    return world.run("record-violation", raw, capsys=capsys)


def frame_status(
    world: World, capsys: pytest.CaptureFixture[str], **over: object
) -> tuple[int, Response]:
    raw = raw_of("perimeter-status", **{"request_id": "req-stat-0001", **over})
    return world.run("perimeter-status", raw, capsys=capsys)


def test_disk_full_refuses(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SQLITE_FULL` while writing is `refused: storage_full`, not `done` and
    not `unknown`: the rolled-back transaction proves the event is absent;
    committed events stay. (The size-bounded file system variant is Task 14's
    root cells.)"""
    frame_record(world, capsys)
    opened = perimeter.open_db

    def full(lay: Layout) -> perimeter.Db:
        db = opened(lay)
        pages = db.conn.execute("PRAGMA page_count").fetchone()[0]
        db.conn.execute(f"PRAGMA max_page_count = {pages}")
        return db

    monkeypatch.setattr(perimeter, "open_db", full)
    code, resp = frame_record(
        world, capsys, request_id="req-viol-0002", detail="x" * 4000
    )
    assert (code, resp.outcome, resp.reason) == (2, "refused", "storage_full")
    assert rows(world.lay) == [("pending", None)]
    assert world.files("intents") and len(world.files("results")) == 2


def _space(monkeypatch: pytest.MonkeyPatch, err: int | None, at: str) -> None:
    """Inject `err` into the space probe's `open` or `write` (only the
    probe's: other calls pass through)."""
    real_open, real_write = os.open, os.write
    probes: set[int] = set()

    def open_(path: object, flags: int, *args: object, **kwargs: object) -> int:
        probe = ".space-probe." in str(path)
        if probe and at == "open" and err is not None:
            raise OSError(err, os.strerror(err))
        fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if probe:
            probes.add(fd)
        return fd

    def write(fd: int, data: bytes) -> int:
        if fd in probes and at == "write" and err is not None:
            raise OSError(err, os.strerror(err))
        return real_write(fd, data)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "write", write)


@pytest.mark.parametrize(
    ("err", "at", "want"),
    [
        (errno.ENOSPC, "open", "storage_full"),
        (errno.EDQUOT, "open", "storage_full"),
        (errno.ENOSPC, "write", "storage_full"),
        (errno.EACCES, "open", "sqlite: SQLITE_CANTOPEN"),
        (None, "open", "sqlite: SQLITE_CANTOPEN"),
    ],
)
def test_cantopen_on_a_full_directory_is_storage_full(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    err: int | None,
    at: str,
    want: str,
) -> None:
    """F4 (plan Task 14a): SQLite says CANTOPEN when a full file system cannot even
    take its journal (macOS); a probe of the directory tells `storage_full`
    from any other CANTOPEN, and leaves nothing behind."""
    base = world.lay.perimeter_dir
    before = sorted(os.listdir(base))
    _space(monkeypatch, err, at)
    got = perimeter.full_or(Unavailable("sqlite: SQLITE_CANTOPEN"), base)
    assert got.reason == want
    assert sorted(os.listdir(base)) == before


def test_full_or_probes_only_cantopen(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _space(monkeypatch, errno.ENOSPC, "open")
    for reason in ("busy", "integrity", "sqlite: SQLITE_IOERR", "storage_full"):
        assert (
            perimeter.full_or(Unavailable(reason), world.lay.perimeter_dir).reason
            == reason
        )


def test_disk_full_cantopen_refuses(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frame of F4: CANTOPEN while writing on a full directory is
    `refused: storage_full` (as `SQLITE_FULL`), the committed event stays."""
    frame_record(world, capsys)

    def cantopen(*args: object, **kwargs: object) -> Response:
        raise Unavailable("sqlite: SQLITE_CANTOPEN")

    monkeypatch.setattr(perimeter, "record_violation", cantopen)
    _space(monkeypatch, errno.ENOSPC, "open")
    code, resp = frame_record(world, capsys, request_id="req-viol-0002")
    assert (code, resp.outcome, resp.reason) == (2, "refused", "storage_full")
    assert rows(world.lay) == [("pending", None)]


def test_frame_record_kept_and_replayed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, first = frame_record(world, capsys)
    assert (code, first.outcome, first.result["state"]) == (0, "done", "pending")
    assert first.evidence["event_id"] == first.result["event_id"]
    trust = cast(dict[str, object], first.evidence["trust_root"])
    assert trust["unavailable"] == "epochs_unavailable"
    assert len(world.files("intents")) == 1 and len(world.files("results")) == 1
    code, again = frame_record(world, capsys)
    assert (code, again.result, again.evidence["replayed"]) == (0, first.result, True)
    assert rows(world.lay) == [("pending", None)]


def test_frame_repeat_after_commit_finds_the_event(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """The process died between `COMMIT` and the answer: the intent is there,
    no result; the repeat answers the row, with no new epoch."""
    _, first = frame_record(world, capsys)
    for name in world.files("results"):
        (world.lay.publisher_dir / "results" / name).unlink()
    perimeter.EPOCHS.at(S2, 2)  # type: ignore[attr-defined]
    code, again = frame_record(world, capsys)
    assert (code, again.outcome) == (0, "done")
    assert again.result == first.result
    assert "replayed" not in again.evidence
    assert rows(world.lay) == [("pending", None)]


def test_frame_intent_before_insert(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "intent", broken)
    _, resp = frame_record(world, capsys)
    assert resp.outcome != "done"
    assert rows(world.lay) == []


def test_frame_unavailable_database_refused_before_intent(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    (world.lay.perimeter_dir / perimeter.DB_NAME).unlink()
    code, resp = frame_record(world, capsys)
    assert (code, resp.outcome, resp.reason) == (
        2,
        "refused",
        "perimeter_unavailable: lost",
    )
    assert world.files("intents") == []
    _, resp = frame_status(world, capsys)
    assert resp.result == perimeter_result("unavailable", "lost", None, None)


def test_frame_failure_after_intent_is_unknown(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_a: object) -> Response:
        raise Unavailable("busy")

    monkeypatch.setattr(perimeter, "record_violation", busy)
    code, resp = frame_record(world, capsys)
    assert (code, resp.outcome) == (3, "unknown")
    assert resp.reason == "state_exists: perimeter_unavailable: busy"


@pytest.mark.parametrize("op", ["record-violation", "perimeter-status"])
def test_frame_root_of_another_binding(
    world: World, capsys: pytest.CaptureFixture[str], op: str
) -> None:
    other = {"host": "github.com", "repository_id": 5, "branch": "next"}
    raw = raw_of(op, root_id=other)
    code, resp = world.run(op, raw, capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "root_mismatch")
    (world.lay.etc / "trust.toml").write_text(TRUST.format(repo=5, branch="next"))
    raw = raw_of(op, root_id=other, request_id="req-other-001")
    code, resp = world.run(op, raw, capsys=capsys)  # the run's root is main
    assert (code, resp.outcome, resp.reason) == (2, "refused", "root_mismatch")
    raw = raw_of(op, request_id="req-other-002")
    code, resp = world.run(op, raw, capsys=capsys)  # the binding is next
    assert (code, resp.outcome, resp.reason) == (2, "refused", "root_mismatch")
    assert world.files("intents") == []
    assert rows(world.lay) == []


@pytest.mark.parametrize("op", ["record-violation", "perimeter-status"])
def test_frame_run_without_root(
    world: World, capsys: pytest.CaptureFixture[str], op: str
) -> None:
    put_record(world.fake, replace(record("run-2", "rel-a"), root_id=None))
    code, resp = world.run(op, raw_of(op, run_id="run-2"), capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "root_mismatch")


@pytest.mark.parametrize("op", ["record-violation", "perimeter-status"])
def test_frame_broken_trust_refused(
    world: World, capsys: pytest.CaptureFixture[str], op: str
) -> None:
    (world.lay.etc / "trust.toml").write_text("garbage = [")
    code, resp = world.run(op, raw_of(op), capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "trust_config_invalid")


def test_no_binding_refuses(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    (world.lay.etc / "trust.toml").unlink()
    code, resp = frame_record(world, capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "no_binding")
    code, resp = frame_status(world, capsys)
    assert (code, resp.outcome) == (0, "done")
    assert resp.result == perimeter_result("unavailable", "no_binding", None, None)
    assert world.files("intents") == [] and rows(world.lay) == []


def test_frame_status_flow(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    _, rec = frame_record(world, capsys)
    perimeter.EPOCHS.at(S1, 3)  # type: ignore[attr-defined]
    code, resp = frame_status(world, capsys, expected_snapshot_sha=S2)
    assert (code, resp.outcome, resp.result) == (
        2,
        "stale_snapshot",
        {"snapshot_sha": S1},
    )
    code, resp = frame_status(world, capsys, expected_snapshot_sha=S1)
    assert (code, resp.reason) == (2, "request_id_reused")  # a new id is needed
    fresh = {"request_id": "req-stat-0002", "expected_snapshot_sha": S1}
    code, resp = frame_status(world, capsys, **fresh)
    assert (code, resp.outcome, resp.result["state"]) == (0, "done", "blocked")
    assert resp.result["events"] == [rec.result["event_id"]]
    code, resp = frame_status(world, capsys, **fresh)  # recomputed, same id
    assert resp.result["state"] == "blocked" and "replayed" not in resp.evidence
    assert world.files("intents") == world.files("results")  # the record's only
    assert len(world.files("intents")) == 1
    assert rows(world.lay) == [("registered", 3)]
