"""The perimeter event store: `record-violation`, `perimeter-status` (spec
6.1, 5.2, 5.5; stage 2a, Task 11).

`/var/lib/df/perimeter/events.sqlite` is opened by the publisher only
(`df-publisher`, directory `0700`); consumers ask `perimeter-status`. Next to
it: `created`, the marker of the first creation (`roles setup`'s
`perimeter-init`, the one place that creates), and `db.lock`, the barrier:
every publisher process holds `flock(LOCK_SH)` on it from before
`sqlite3.connect` until the connection is closed; creation (and restore,
Task 11r) hold `LOCK_EX`. While `restore.intent` exists the database is not
opened at all (`restore_incomplete`).

Every open checks the effective pragmas, `PRAGMA integrity_check`, the schema
and the application invariants; any failure is `Unavailable` and the file is
not touched. Every change is one transaction (`BEGIN IMMEDIATE`, bounded by
`busy_timeout`). The blocking rule: a root's decision is blocked while one of
its events is `pending` or `registered(E)` with `E >=` the root's last epoch.

A restored copy (`admission = restored_pending`) is not complete: once the
epoch source answers for every known root, one transaction records a
conservative `registered(E)` per root (source `db_restore`) and admits the
copy; until then it is `unavailable: restore_pending`. In 2a the only epoch
source in the code is `NO_BINDING`.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import os
import sqlite3
import stat
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from df import fsutil
from df.config import ConfigError
from df.paths import Layout
from df.publisher.github import PerimeterStats
from df.publisher.request import Refused, Request, Response, perimeter_result
from df.stage2_config import TrustBinding, load_trust

if TYPE_CHECKING:
    from df.publisher.main import Context

DB_NAME = "events.sqlite"
#: Where `create_db` builds the database before renaming it into place.
NEW_DB = "events.sqlite.new"
MARKER = "created"
LOCK = "db.lock"
RESTORE_INTENT = "restore.intent"
MAX_EVENTS = 10_000
BUSY_TIMEOUT_MS = 5000
#: Seconds the barrier is waited for before `busy`.
BARRIER_WAIT = 5.0
#: `synchronous = EXTRA` exists since SQLite 3.24.
MIN_SQLITE = (3, 24, 0)
SQLITE_VERSION: tuple[int, int, int] = sqlite3.sqlite_version_info
SCHEMA_VERSION = "1"
STATES = ("pending", "registered")
ADMISSIONS = ("ready", "restored_pending")
DB_RESTORE = "db_restore"
LOST_TEXT = (
    "база периметра потеряна: есть отметка perimeter/created, нет "
    "perimeter/events.sqlite; заново она не создаётся — нужен человек "
    "(восстановление из копии)"
)
_POLL = 0.02
_LOCK_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

#: A root of trust: host, `df-trust` repository id, branch (spec 6.1).
Root = tuple[str, int, str]

SCHEMA: tuple[str, ...] = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    (
        "CREATE TABLE events ("
        "event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "root_host TEXT NOT NULL, "
        "root_repository_id INTEGER NOT NULL, "
        "root_branch TEXT NOT NULL, "
        "request_key TEXT NOT NULL, "
        "request_id TEXT, "
        "run_id TEXT, "
        "sender INTEGER, "
        "violated_role TEXT, "
        "violated_repository_id INTEGER, "
        "detected_by TEXT NOT NULL, "
        "observed_at TEXT NOT NULL, "
        "registered_at TEXT NOT NULL, "
        "state TEXT NOT NULL, "
        "epoch INTEGER, "
        "provenance TEXT NOT NULL, "
        "detail TEXT NOT NULL)"
    ),
    "CREATE UNIQUE INDEX events_request_key ON events (request_key)",
    (
        "CREATE INDEX events_root_state "
        "ON events (root_host, root_repository_id, root_branch, state)"
    ),
    (
        "CREATE TABLE transitions ("
        "transition_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_id INTEGER NOT NULL, "
        "transition TEXT NOT NULL, "
        "at TEXT NOT NULL, "
        "epoch INTEGER, "
        "provenance TEXT NOT NULL, "
        "actor TEXT NOT NULL, "
        "evidence TEXT NOT NULL)"
    ),
    "CREATE INDEX transitions_event ON transitions (event_id)",
)
#: Each counts the rows that break an invariant (spec 6.1).
_INVARIANTS = (
    "SELECT count(*) FROM events WHERE state NOT IN ('pending', 'registered')",
    (
        "SELECT count(*) FROM events "
        "WHERE state = 'registered' AND typeof(epoch) != 'integer'"
    ),
    "SELECT count(*) FROM events WHERE state = 'pending' AND epoch IS NOT NULL",
    (
        "SELECT count(*) FROM transitions "
        "WHERE event_id NOT IN (SELECT event_id FROM events)"
    ),
)
_ROOT_WHERE = "root_host = ? AND root_repository_id = ? AND root_branch = ?"
_BLOCKING = (
    f"SELECT event_id FROM events WHERE {_ROOT_WHERE} AND "
    "(state = 'pending' OR (state = 'registered' AND epoch >= ?)) "
    "ORDER BY event_id"
)
_BUSY_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_BROKEN_CODES = frozenset(
    {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_SCHEMA}
)


class Unavailable(Exception):
    """The perimeter cannot answer: `reason` is what `unavailable` carries.

    Raised by the store; an `EpochSource` returns one instead of a head."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Snapshot:
    """The head of the pinned `df-trust` branch and its last epoch."""

    sha: str
    last_epoch: int


class EpochSource(Protocol):
    """Where the publisher reads a root's epochs (spec 6.1)."""

    def head(self, root: Root) -> Snapshot | Unavailable:
        """The root's current snapshot, or why it cannot be read."""
        ...


class _NoBinding:
    """Stage 2a: no epoch journal is read; every head is unavailable."""

    def head(self, root: Root) -> Snapshot | Unavailable:
        return Unavailable("no_binding")


NO_BINDING: EpochSource = _NoBinding()
#: The source the operations use; the only one in the code is `NO_BINDING`
#: (tests substitute theirs).
EPOCHS: EpochSource = NO_BINDING


# --- the database ---------------------------------------------------------------


class Db:
    """An open, verified connection with the barrier held until `close`."""

    def __init__(self, conn: sqlite3.Connection, lock_fd: int) -> None:
        self.conn = conn
        self._lock_fd: int | None = lock_fd

    def close(self) -> None:
        """Close the connection, then release the barrier (idempotent)."""
        fd, self._lock_fd = self._lock_fd, None
        if fd is None:
            return
        try:
            self.conn.close()
        finally:
            os.close(fd)

    def __enter__(self) -> Db:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def pragmas() -> list[tuple[str, str | int, str | int]]:
    """`(name, value set, value read back)` of every connection."""
    table: list[tuple[str, str | int, str | int]] = [
        ("busy_timeout", BUSY_TIMEOUT_MS, BUSY_TIMEOUT_MS),
        ("journal_mode", "DELETE", "delete"),
        ("synchronous", "EXTRA", 3),
    ]
    if sys.platform == "darwin":
        table += [("fullfsync", "ON", 1), ("checkpoint_fullfsync", "ON", 1)]
    return table


def open_db(layout: Layout) -> Db:
    """The perimeter database, verified; never created (`Unavailable`).

    The barrier is taken (`LOCK_SH`, bounded: `busy`) before the connection
    is made. No database: `lost` if the marker says it was created, else
    `not_initialized`; `restore.intent` present: `restore_incomplete`."""
    _require_sqlite()
    base = layout.perimeter_dir
    if _lexists(base / RESTORE_INTENT):
        raise Unavailable("restore_incomplete")  # without waiting for restore
    fd = _open_lock(base)
    try:
        _flock(fd, fcntl.LOCK_SH)
        conn = _open_verified(base)
    except BaseException:
        os.close(fd)
        raise
    return Db(conn, fd)


def create_db(layout: Layout) -> str:
    """`perimeter-init` (as `df-publisher`): `created` or `present`.

    Under `LOCK_EX`: only when neither the database nor the marker exists is
    the database built (a temporary file, renamed into place) and then the
    marker written; the marker without the database is `Unavailable("lost")`
    and nothing is created; a database without the marker (a crash before
    the marker) is verified and marked."""
    _require_sqlite()
    base = layout.perimeter_dir
    _own_dir(base)
    fd = _lock_for_create(base)
    try:
        _flock(fd, fcntl.LOCK_EX)
        if _lexists(base / RESTORE_INTENT):
            raise Unavailable("restore_incomplete")
        db, marker = _lexists(base / DB_NAME), _lexists(base / MARKER)
        if marker and not db:
            raise Unavailable("lost")
        if db and marker:
            return "present"
        if not db:
            _build(base)
        _open_verified(base).close()
        fsutil.write_atomic(base / MARKER, _now() + "\n", 0o600)
        return "created"
    finally:
        os.close(fd)


def _require_sqlite() -> None:
    if SQLITE_VERSION < MIN_SQLITE:
        raise Unavailable("sqlite_too_old")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _missing(base: Path) -> Unavailable:
    return Unavailable("lost" if _lexists(base / MARKER) else "not_initialized")


def _open_lock(base: Path) -> int:
    """The barrier file, opened for reading: never created here."""
    try:
        fd = os.open(base / LOCK, _LOCK_FLAGS)
    except FileNotFoundError:
        raise _missing(base) from None
    except OSError as exc:
        raise Unavailable(f"io: {exc.strerror or exc}") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Unavailable("integrity")
    return fd


def _lock_for_create(base: Path) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = fsutil.create_file(base / LOCK, flags, 0o600)
    except FileExistsError:
        fd = os.open(base / LOCK, _LOCK_FLAGS)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{base / LOCK}: not a regular file")
    return fd


def _own_dir(base: Path) -> None:
    st = os.lstat(base)
    ok = (
        stat.S_ISDIR(st.st_mode)
        and stat.S_IMODE(st.st_mode) == 0o700
        and fsutil.owner_uid(st) == os.geteuid()
    )
    if not ok:
        raise OSError(f"{base}: not a 0700 directory of this user")


def _flock(fd: int, op: int) -> None:
    deadline = time.monotonic() + BARRIER_WAIT
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise Unavailable("busy") from None
            time.sleep(_POLL)


def _open_verified(base: Path) -> sqlite3.Connection:
    """Connect to the existing database (the caller holds the barrier)."""
    if _lexists(base / RESTORE_INTENT):
        raise Unavailable("restore_incomplete")
    path = base / DB_NAME
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise _missing(base) from None
    if not stat.S_ISREG(st.st_mode):
        raise Unavailable("integrity")
    with _mapped():
        uri = f"{path.as_uri()}?mode=rw&nofollow=1"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        try:
            _apply_pragmas(conn)
            _verify(conn)
        except BaseException:
            conn.close()
            raise
    return conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for name, value, want in pragmas():
        conn.execute(f"PRAGMA {name} = {value}").fetchall()
        got = conn.execute(f"PRAGMA {name}").fetchone()
        if got is None or got[0] != want:
            raise Unavailable(f"pragma: {name}")


@functools.cache
def _expected_schema() -> frozenset[tuple[str, str, str]]:
    conn = sqlite3.connect(":memory:")
    try:
        for sql in SCHEMA:
            conn.execute(sql)
        return _schema(conn)
    finally:
        conn.close()


def _schema(conn: sqlite3.Connection) -> frozenset[tuple[str, str, str]]:
    found = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    )
    return frozenset((str(t), str(n), str(s)) for t, n, s in found)


def _verify(conn: sqlite3.Connection) -> None:
    """Integrity, schema, meta, invariants and the volume, in one read."""
    with _transaction(conn, "BEGIN"):
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise Unavailable("integrity")
        if _schema(conn) != _expected_schema():
            raise Unavailable("integrity")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.keys() != {"schema", "admission"}:
            raise Unavailable("integrity")
        if meta["schema"] != SCHEMA_VERSION or meta["admission"] not in ADMISSIONS:
            raise Unavailable("integrity")
        if any(conn.execute(sql).fetchone()[0] for sql in _INVARIANTS):
            raise Unavailable("integrity")
        if _count(conn) > MAX_EVENTS:
            raise Unavailable("too_many_events")


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM events").fetchone()[0])


def _build(base: Path) -> None:
    """The schema in a fresh `NEW_DB` (born `0600`), synced, renamed into
    place, the directory synced. A leftover of a crash is swept first."""
    tmp = base / NEW_DB
    for leftover in (tmp, base / f"{NEW_DB}-journal"):
        with contextlib.suppress(FileNotFoundError):
            fsutil.remove_durably(leftover)
    os.close(fsutil.create_file(tmp, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600))
    with _mapped():
        conn = sqlite3.connect(tmp, isolation_level=None)
        try:
            _apply_pragmas(conn)
            with _write(conn):
                for sql in SCHEMA:
                    conn.execute(sql)
                conn.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)",
                    [("schema", SCHEMA_VERSION), ("admission", "ready")],
                )
        finally:
            conn.close()
    fd = os.open(tmp, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fsutil.sync_file(fd)
    finally:
        os.close(fd)
    os.rename(tmp, base / DB_NAME)
    fsutil.sync_dir(base)


def _write(conn: sqlite3.Connection) -> contextlib.AbstractContextManager[None]:
    """One write transaction: committed on success, rolled back otherwise."""
    return _transaction(conn, "BEGIN IMMEDIATE")


@contextlib.contextmanager
def _transaction(conn: sqlite3.Connection, begin: str) -> Iterator[None]:
    conn.execute(begin)
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextlib.contextmanager
def _mapped() -> Iterator[None]:
    """SQLite's errors as `Unavailable`: `busy`, `integrity` or
    `sqlite: <name>`."""
    try:
        yield
    except sqlite3.Error as exc:
        raise _unavailable_of(exc) from exc


def _unavailable_of(exc: sqlite3.Error) -> Unavailable:
    code = getattr(exc, "sqlite_errorcode", None)
    primary = None if code is None else code & 0xFF
    if primary in _BUSY_CODES or "locked" in str(exc):
        return Unavailable("busy")
    if primary in _BROKEN_CODES:
        return Unavailable("integrity")
    name = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
    return Unavailable(f"sqlite: {name}")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def admission(conn: sqlite3.Connection) -> str:
    """`ready` or `restored_pending`."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'admission'").fetchone()
    return str(row[0])


# --- admission of a restored copy ------------------------------------------------


def admit(db: Db, epochs: EpochSource, current: Root) -> None:
    """A `restored_pending` database is admitted by `restore_conservative`
    over every root of its events and the current binding's; until the
    epochs of all are known it is `Unavailable("restore_pending")`."""
    with _mapped():
        if admission(db.conn) == "ready":
            return
        found = db.conn.execute(
            "SELECT DISTINCT root_host, root_repository_id, root_branch FROM events"
        ).fetchall()
    roots = {(str(h), int(r), str(b)) for h, r, b in found} | {current}
    restore_conservative(db.conn, sorted(roots), epochs)


def restore_conservative(
    conn: sqlite3.Connection, roots: Sequence[Root], epochs: EpochSource
) -> None:
    """One transaction: a `registered(E)` event per root, `E` its last epoch
    now (source `db_restore`), then `admission = ready`. An unknown epoch of
    any root changes nothing: `Unavailable("restore_pending")`."""
    with _mapped(), _write(conn):
        if admission(conn) == "ready":
            return  # another process admitted it meanwhile
        for root in roots:
            head = epochs.head(root)
            if isinstance(head, Unavailable):
                raise Unavailable("restore_pending")
            _insert(
                conn,
                root,
                key=f"{DB_RESTORE}:{uuid.uuid4().hex}",
                head=head,
                detected_by=DB_RESTORE,
                detail="conservative event of a restored database",
            )
        conn.execute("UPDATE meta SET value = 'ready' WHERE key = 'admission'")


# --- record-violation ------------------------------------------------------------


def root_of(req: Request) -> Root:
    """The `root_id` a request names."""
    raw = req.fields["root_id"]
    assert isinstance(raw, dict)
    return (str(raw["host"]), int(raw["repository_id"]), str(raw["branch"]))


def _provenance(head: Snapshot | Unavailable) -> dict[str, object]:
    if isinstance(head, Unavailable):
        return {"snapshot_sha": None, "unavailable": head.reason}
    return {"snapshot_sha": head.sha, "unavailable": None}


def _insert(
    conn: sqlite3.Connection,
    root: Root,
    *,
    key: str,
    head: Snapshot | Unavailable,
    detected_by: str,
    detail: str,
    req: Request | None = None,
    sender: int | None = None,
    violated: tuple[str, int] | None = None,
) -> int:
    pending = isinstance(head, Unavailable)
    now = _now()
    fields = {} if req is None else req.fields
    cur = conn.execute(
        "INSERT INTO events (root_host, root_repository_id, root_branch, "
        "request_key, request_id, run_id, sender, violated_role, "
        "violated_repository_id, detected_by, observed_at, registered_at, "
        "state, epoch, provenance, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            *root,
            key,
            fields.get("request_id"),
            fields.get("run_id"),
            sender,
            None if violated is None else violated[0],
            None if violated is None else violated[1],
            detected_by,
            str(fields.get("observed_at", now)),
            now,
            "pending" if pending else "registered",
            None if isinstance(head, Unavailable) else head.last_epoch,
            json.dumps(_provenance(head), sort_keys=True),
            detail,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _violated(req: Request, binding: TrustBinding) -> tuple[str, int]:
    role = str(req.fields["source"])
    if role == "approval-policy":
        return role, binding.approval_policy_repository_id
    return role, binding.df_trust_repository_id


def record_violation(
    conn: sqlite3.Connection,
    sender: int,
    req: Request,
    key: str,
    epochs: EpochSource,
    binding: TrustBinding,
) -> Response:
    """Register the event of `req` under `key` (one transaction).

    The same `key` again finds its row and answers it (no new epoch);
    otherwise `pending` if the epochs are unavailable, else `registered(E)`
    with the root's last epoch now. A full store refuses, verified: the
    lookup in the same transaction found nothing. SQLite's failures are
    `Unavailable`."""
    root = root_of(req)
    head = epochs.head(root)
    with _mapped(), _write(conn):
        row = _by_key(conn, key)
        if row is None:
            if _count(conn) >= MAX_EVENTS:
                raise Refused(
                    "perimeter_unavailable: too_many_events", verified_not_done=True
                )
            _insert(
                conn,
                root,
                key=key,
                head=head,
                detected_by=f"sender:{sender}",
                detail=str(req.fields["detail"]),
                req=req,
                sender=sender,
                violated=_violated(req, binding),
            )
            row = _by_key(conn, key)
    assert row is not None
    return _recorded(root, row)


def _by_key(
    conn: sqlite3.Connection, key: str
) -> tuple[int, str, int | None, str] | None:
    return conn.execute(
        "SELECT event_id, state, epoch, provenance FROM events WHERE request_key = ?",
        (key,),
    ).fetchone()


def _recorded(root: Root, row: tuple[int, str, int | None, str]) -> Response:
    event_id, state, epoch, provenance = row
    trust_root = {
        "host": root[0],
        "repository_id": root[1],
        "branch": root[2],
        **json.loads(provenance),
    }
    result = {"event_id": event_id, "state": state, "epoch": epoch}
    return Response(
        "done", None, result, {"event_id": event_id, "trust_root": trust_root}
    )


# --- perimeter-status ------------------------------------------------------------


def status_unavailable(reason: str) -> Response:
    """`done` with `state = unavailable` and null snapshot fields."""
    return Response("done", None, perimeter_result("unavailable", reason, None, None))


def perimeter_status(
    conn: sqlite3.Connection, req: Request, epochs: EpochSource
) -> Response:
    """The root's state against the current head (spec 6.1).

    A head other than `expected_snapshot_sha` is `stale_snapshot` with the
    head's SHA. Otherwise one transaction registers every `pending` event of
    the root at the head's last epoch and computes `blocked`/`clear`."""
    root = root_of(req)
    head = epochs.head(root)
    if isinstance(head, Unavailable):
        return status_unavailable(head.reason)
    if head.sha != req.fields["expected_snapshot_sha"]:
        return Response("stale_snapshot", "snapshot_moved", {"snapshot_sha": head.sha})
    try:
        with _mapped(), _write(conn):
            _register_pending(conn, root, head)
            found = conn.execute(_BLOCKING, (*root, head.last_epoch)).fetchall()
    except Unavailable as exc:
        return status_unavailable(exc.reason)
    blocking = [int(r[0]) for r in found]
    state = "blocked" if blocking else "clear"
    result = perimeter_result(state, None, head.sha, head.last_epoch)
    return Response("done", None, {**result, "events": blocking})


def _register_pending(conn: sqlite3.Connection, root: Root, head: Snapshot) -> None:
    pending = conn.execute(
        f"SELECT event_id FROM events WHERE {_ROOT_WHERE} AND state = 'pending'",
        root,
    ).fetchall()
    now = _now()
    provenance = json.dumps({"snapshot_sha": head.sha}, sort_keys=True)
    for (event_id,) in pending:
        conn.execute(
            "UPDATE events SET state = 'registered', epoch = ? "
            "WHERE event_id = ? AND state = 'pending'",
            (head.last_epoch, event_id),
        )
        conn.execute(
            "INSERT INTO transitions (event_id, transition, at, epoch, "
            "provenance, actor, evidence) VALUES (?, 'registered', ?, ?, ?, "
            "'publisher', '{}')",
            (event_id, now, head.last_epoch, provenance),
        )


# --- self-check ------------------------------------------------------------------


def stats(layout: Layout) -> PerimeterStats | Unavailable:
    """Events, database size and free space (read under the barrier)."""
    base = layout.perimeter_dir
    try:
        with open_db(layout) as db, _mapped():
            events = _count(db.conn)
            size = os.lstat(base / DB_NAME).st_size
    except Unavailable as exc:
        return exc
    vfs = os.statvfs(base)
    return PerimeterStats(events, size, vfs.f_bavail * vfs.f_frsize)


# --- the operations --------------------------------------------------------------


def _binding(ctx: Context) -> TrustBinding | None:
    try:
        return load_trust(ctx.layout.etc / "trust.toml")
    except ConfigError as exc:
        print(f"publisher: trust.toml: {exc}", file=sys.stderr)
        raise Refused("trust_config_invalid") from exc


def _check_root(ctx: Context, binding: TrustBinding) -> Root:
    """The request's root must be the binding's and the run's (spec 5.3):
    a message of an old root is never carried over to a new one."""
    want = root_of(ctx.request)
    record = ctx.record
    if binding.root_id != want or record is None or record.root_id != want:
        raise Refused("root_mismatch")
    return want


def record_violation_op(ctx: Context) -> Response:
    """`record-violation`: the bindings and the database are checked before
    the intent (their refusals are truthful), then the intent, then one
    transaction. A repeat (an intent exists) answers the row its key found."""
    binding = _binding(ctx)
    if binding is None:
        raise Refused("no_binding")
    root = _check_root(ctx, binding)
    try:
        db = open_db(ctx.layout)
    except Unavailable as exc:
        raise Refused(f"perimeter_unavailable: {exc.reason}") from None
    key = ctx.reservation.key
    with db:
        try:
            admit(db, EPOCHS, root)
        except Unavailable as exc:
            raise Refused(f"perimeter_unavailable: {exc.reason}") from None
        if ctx.intent is None:
            ctx.record_intent({"request_key": key})
        try:
            return record_violation(
                db.conn, ctx.sender, ctx.request, key, EPOCHS, binding
            )
        except Unavailable as exc:
            raise Refused(f"perimeter_unavailable: {exc.reason}") from None


def perimeter_status_op(ctx: Context) -> Response:
    """`perimeter-status`: computed anew at every call, no intent. No
    binding is `unavailable: no_binding` (decision 14)."""
    binding = _binding(ctx)
    if binding is None:
        return status_unavailable("no_binding")
    root = _check_root(ctx, binding)
    try:
        db = open_db(ctx.layout)
    except Unavailable as exc:
        return status_unavailable(exc.reason)
    with db:
        try:
            admit(db, EPOCHS, root)
        except Unavailable as exc:
            return status_unavailable(exc.reason)
        return perimeter_status(db.conn, ctx.request, EPOCHS)


__all__ = [
    "BUSY_TIMEOUT_MS",
    "DB_NAME",
    "EPOCHS",
    "LOCK",
    "LOST_TEXT",
    "MARKER",
    "MAX_EVENTS",
    "NO_BINDING",
    "RESTORE_INTENT",
    "Db",
    "EpochSource",
    "Root",
    "Snapshot",
    "Unavailable",
    "admission",
    "admit",
    "create_db",
    "open_db",
    "perimeter_status",
    "perimeter_status_op",
    "pragmas",
    "record_violation",
    "record_violation_op",
    "restore_conservative",
    "root_of",
    "stats",
    "status_unavailable",
]
