"""The perimeter event store: `record-violation`, `perimeter-status` (spec
6.1, 5.2, 5.5; stage 2a, Task 11).

`/var/lib/df/perimeter/events.sqlite` is opened by the publisher only
(`df-publisher`, directory `0700`); consumers ask `perimeter-status`. Next to
it: `created`, the marker of the first creation (`roles setup`'s
`perimeter-init`, the one place that creates), and `db.lock`, the barrier:
every publisher process holds `flock(LOCK_SH)` on it from before
`sqlite3.connect` until the connection is closed; creation (and restore,
Task 11r) hold `LOCK_EX`. While `restore.intent` exists the database is not
opened at all (`restore_incomplete`). Outside the directory, root's
`/var/lib/df/perimeter.created` (`0644`, written by `roles setup` after a
successful `perimeter-init`) keeps the "created once" fact when the whole
`perimeter/` is lost: then the answer is `lost`, never a new database.

Every open checks the effective pragmas, `PRAGMA integrity_check`, the schema
and the application invariants; any failure is `Unavailable` and the file is
not touched. Every change is one transaction (`BEGIN IMMEDIATE`, bounded by
`busy_timeout`). The blocking rule: a root's decision is blocked while one of
its events is `pending` or `registered(E)` with `E >=` the root's last epoch.

A restored copy (`admission = restored_pending`) is not complete: once the
epoch source answers for every known root, one transaction records a
conservative `registered(E)` per root (source `db_restore`) and admits the
copy; until then it is `unavailable: restore_pending`. In 2a the only epoch
source in the code is `NO_BINDING`. `restore` (root's `darkfactory perimeter
restore`, run as `df-publisher`; Task 11r) puts a copy, or an empty database,
in place: always `restored_pending`, never `ready`.

The epoch of a registration is read inside the write transaction, with the
write lock held. A remote read cannot be made atomic with the `COMMIT`: an
epoch opened between the read and the `COMMIT` is not seen, so the event is
registered one epoch too low.
TODO: when a real `EpochSource` arrives, re-read the head after `COMMIT` and,
if the epoch moved, raise the event's epoch in a second transaction
(conservative).
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import os
import re
import sqlite3
import stat
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
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
JOURNAL = "events.sqlite-journal"
#: Where `create_db` builds the database before renaming it into place.
NEW_DB = "events.sqlite.new"
MARKER = "created"
LOCK = "db.lock"
RESTORE_INTENT = "restore.intent"
MAX_EVENTS = 10_000
BUSY_TIMEOUT_MS = 5000
#: Seconds the barrier is waited for before `busy`.
BARRIER_WAIT = 5.0
#: `synchronous = EXTRA` needs SQLite 3.24, the `nofollow` URI parameter 3.31
#: (below it is silently ignored): the larger of the two.
MIN_SQLITE = (3, 31, 0)
SQLITE_VERSION: tuple[int, int, int] = sqlite3.sqlite_version_info
SCHEMA_VERSION = "1"
STATES = ("pending", "registered")
ADMISSIONS = ("ready", "restored_pending")
DB_RESTORE = "db_restore"
LOST_TEXT = (
    "база периметра потеряна: есть отметка perimeter/created, нет "
    "perimeter/events.sqlite; заново она не создаётся — нужен человек: "
    "sudo darkfactory perimeter restore --from <копия> (или --empty); если "
    "нет самого каталога perimeter/ — сначала sudo darkfactory roles setup"
)
INTEGRITY_TEXT = (
    "база периметра не прошла проверку целостности, или рядом с отсутствующей "
    "базой остался журнал perimeter/events.sqlite-journal; нужен человек "
    "(журнал не удалять)"
)
#: Where `restore` stages the copy (or the empty database) before the barrier.
RESTORE_DB = "events.sqlite.restore"
#: Where `--set-aside-old` moves the old pair: `set-aside/<UTC>/`.
SET_ASIDE = "set-aside"
#: The largest copy `restore` accepts (bytes).
MAX_COPY = 256 << 20
#: Seconds `restore` waits for `LOCK_EX` before `busy`.
RESTORE_WAIT = 30.0
RESTORE_TEXTS = {
    "busy": (
        "база периметра занята: издатель держит её открытой дольше "
        f"{RESTORE_WAIT:g} с; ничего не изменено, повторите позже"
    ),
    "perimeter_dir": (
        "каталога perimeter/ нет или он не 0700 учётки df-publisher: сначала "
        "sudo darkfactory roles setup (создаёт каталог), затем sudo darkfactory "
        "perimeter restore --from <копия> (или --empty)"
    ),
    "set_aside": (
        "perimeter/set-aside/ не создаётся или это не каталог 0700 учётки "
        "df-publisher; ничего не изменено, база доступна: устраните причину "
        "(или уберите чужой объект — нужен человек) и повторите команду"
    ),
    "journal": (
        "у прежней базы журнал perimeter/events.sqlite-journal, который не "
        "откатывается (или журнал без базы); ничего не изменено. Аварийная "
        "ветка: sudo darkfactory perimeter restore --from <копия> "
        "--set-aside-old (прежняя пара уходит в perimeter/set-aside/); журнал "
        "не удалять"
    ),
    "restore_incomplete": (
        "прерванное восстановление (perimeter/restore.intent) не продолжается "
        "по фактам на диске; нужен человек"
    ),
}
_POLL = 0.02
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_INTENT_DIR = re.compile(rf"{SET_ASIDE}/[0-9]{{8}}T[0-9]{{6}}\.[0-9]{{6}}Z", re.ASCII)
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
    fd = _open_lock(layout)
    try:
        _flock(fd, fcntl.LOCK_SH)
        conn = _open_verified(layout)
    except BaseException:
        os.close(fd)
        raise
    return Db(conn, fd)


def create_db(layout: Layout) -> str:
    """`perimeter-init` (as `df-publisher`): `created` or `present`.

    Under `LOCK_EX`: only when neither the database nor the marker exists is
    the database built (a temporary file, renamed into place) and then the
    marker written. No database while the marker or root's
    `perimeter.created` exists is `Unavailable("lost")`; no database next to
    an `events.sqlite-journal` is `Unavailable("integrity")`: nothing is
    created in either case. A database without the marker (a crash before
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
        if not db and (marker or _lexists(layout.perimeter_created)):
            raise Unavailable("lost")
        if db and marker:
            return "present"
        if not db and _lexists(base / JOURNAL):
            raise Unavailable("integrity")  # never build next to a foreign journal
        if not db:
            _build(base)
        _open_verified(layout).close()
        _mark_created(base)
        return "created"
    finally:
        os.close(fd)


def _mark_created(base: Path) -> None:
    """`created`, the marker of the first creation, written if absent."""
    if not _lexists(base / MARKER):
        fsutil.write_atomic(base / MARKER, now() + "\n", 0o600)


def _require_sqlite() -> None:
    if SQLITE_VERSION < MIN_SQLITE:
        raise Unavailable("sqlite_too_old")


def _io(exc: OSError) -> Unavailable:
    return Unavailable(f"io: {exc.strerror or exc}")


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _missing(layout: Layout) -> Unavailable:
    """No database: `lost` if it was ever created (the marker in the
    directory or root's record outside it), else `not_initialized`."""
    created = _lexists(layout.perimeter_dir / MARKER) or _lexists(
        layout.perimeter_created
    )
    return Unavailable("lost" if created else "not_initialized")


def _open_lock(layout: Layout) -> int:
    """The barrier file, opened for reading: never created here. Missing
    next to an existing database: `lock_missing`."""
    base = layout.perimeter_dir
    try:
        fd = os.open(base / LOCK, _LOCK_FLAGS)
    except FileNotFoundError:
        if _lexists(base / DB_NAME):
            raise Unavailable("lock_missing") from None
        raise _missing(layout) from None
    except OSError as exc:
        raise _io(exc) from None
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
    if not _is_own_dir(base):
        raise OSError(f"{base}: not a 0700 directory of this user")


def _is_own_dir(path: Path) -> bool:
    """A `0700` directory (not a link) of this user; `lstat` errors raise."""
    st = os.lstat(path)
    return (
        stat.S_ISDIR(st.st_mode)
        and stat.S_IMODE(st.st_mode) == 0o700
        and fsutil.owner_uid(st) == os.geteuid()
    )


def _flock(fd: int, op: int, wait: float | None = None) -> None:
    """`op` on the barrier, waited for at most `wait` (`BARRIER_WAIT`)."""
    deadline = time.monotonic() + (BARRIER_WAIT if wait is None else wait)
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise Unavailable("busy") from None
            time.sleep(_POLL)


def _open_verified(layout: Layout) -> sqlite3.Connection:
    """Connect to the existing database (the caller holds the barrier)."""
    base = layout.perimeter_dir
    if _lexists(base / RESTORE_INTENT):
        raise Unavailable("restore_incomplete")
    path = base / DB_NAME
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise _missing(layout) from None
    except OSError as exc:
        raise _io(exc) from None
    if not stat.S_ISREG(st.st_mode):
        raise Unavailable("integrity")
    return _connect_verified(path)


def _connect_verified(path: Path) -> sqlite3.Connection:
    """`_connect`, then `_verify`; SQLite's errors are `Unavailable`."""
    with _mapped():
        conn = _connect(path)
        try:
            _verify(conn)
        except BaseException:
            conn.close()
            raise
    return conn


def _connect(path: Path) -> sqlite3.Connection:
    """An existing file (never a link), with every pragma set and read back."""
    uri = f"{path.as_uri()}?mode=rw&nofollow=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _apply_pragmas(conn)
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
    """A fresh `ready` database in `NEW_DB`, renamed into place, the
    directory synced."""
    tmp = base / NEW_DB
    _new_db(tmp, "ready")
    os.rename(tmp, base / DB_NAME)
    fsutil.sync_dir(base)


def _new_db(tmp: Path, admission: str) -> None:
    """The schema and `meta` in a fresh `tmp` (born `0600`), synced. A
    leftover of a crash is swept first."""
    _sweep(tmp)
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
                    [("schema", SCHEMA_VERSION), ("admission", admission)],
                )
        finally:
            conn.close()
    _sync_file(tmp)


def _sweep(tmp: Path) -> None:
    """A temporary database and its journal, removed durably if present."""
    for leftover in (tmp, tmp.with_name(f"{tmp.name}-journal")):
        with contextlib.suppress(FileNotFoundError):
            fsutil.remove_durably(leftover)


def _sync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fsutil.sync_file(fd)
    finally:
        os.close(fd)


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
    if primary == sqlite3.SQLITE_FULL:
        return Unavailable("storage_full")
    name = getattr(exc, "sqlite_errorname", None) or type(exc).__name__
    return Unavailable(f"sqlite: {name}")


def now() -> str:
    """The current UTC instant, as the store writes it."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def admission(conn: sqlite3.Connection) -> str:
    """`ready` or `restored_pending`."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'admission'").fetchone()
    return str(row[0])


# --- restore (Task 11r) ----------------------------------------------------------


def _no_step(_name: str) -> None:
    """The default `RESTORE_STEP`: nothing."""


#: Called with the name of each step of `restore` once it is done; tests
#: crash there (`os._exit`).
RESTORE_STEP: Callable[[str], None] = _no_step


def restore(layout: Layout, copy: bytes | None, *, set_aside_old: bool = False) -> str:
    """`perimeter restore` as `df-publisher`: `restored`, or `resumed` when an
    interrupted `--set-aside-old` (`restore.intent`) was finished instead --
    the copy of this call is then not used.

    1. the copy (`None`: an empty database) is staged in `RESTORE_DB`,
       verified as `open_db` verifies and marked `restored_pending` -- all
       before the barrier;
    2. `LOCK_EX`, waited for at most `RESTORE_WAIT`: `busy`, nothing changed
       (the stage is removed);
    3. a hot journal of the old database is rolled back (`_roll_back`);
    4. `rename` onto `events.sqlite`, `fsync` of the directory.
    With `set_aside_old`, steps 3-4 are (а)-(д) of `_set_aside`, its
    directories made before the intent. A database put in place gets the
    `created` marker if absent (root then writes `perimeter.created`). A
    refusal is `Unavailable` (`RESTORE_TEXTS`, `copy: <reason>`)."""
    _require_sqlite()
    base = layout.perimeter_dir
    try:
        _own_dir(base)
    except OSError:
        raise Unavailable("perimeter_dir") from None
    if _lexists(base / RESTORE_INTENT):
        with _exclusive(base):
            _set_aside(base, _read_intent(base))
            _mark_created(base)
        return "resumed"
    tmp = base / RESTORE_DB
    try:
        _stage(tmp, copy)
        RESTORE_STEP("staged")
        with _exclusive(base):
            if set_aside_old:
                intent = _new_intent()
                # (б) before the intent: a failure here changes nothing
                _make_aside_dirs(base, intent, "set_aside")
                _set_aside(base, _write_intent(base, intent))
            else:
                _roll_back(base)
                RESTORE_STEP("rolled-back")
                _move(tmp, base / DB_NAME)
                RESTORE_STEP("installed")
            _mark_created(base)
    except BaseException:
        if not _lexists(base / RESTORE_INTENT):  # the intent keeps its stage
            with contextlib.suppress(OSError):
                _sweep(tmp)
        raise
    return "restored"


def _stage(tmp: Path, copy: bytes | None) -> None:
    """Step 1: the database to put in place, verified, `restored_pending`."""
    if copy is None:
        _new_db(tmp, "restored_pending")
        return
    if len(copy) > MAX_COPY:
        raise Unavailable("copy: too_large")
    _sweep(tmp)
    fd = fsutil.create_file(tmp, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(copy)
        while view:
            view = view[os.write(fd, view) :]
        fsutil.sync_file(fd)
    finally:
        os.close(fd)
    try:
        conn = _connect_verified(tmp)
    except Unavailable as exc:
        raise Unavailable(f"copy: {exc.reason}") from None
    try:
        with _mapped(), _write(conn):
            conn.execute(
                "UPDATE meta SET value = 'restored_pending' WHERE key = 'admission'"
            )
    finally:
        conn.close()
    _sync_file(tmp)


@contextlib.contextmanager
def _exclusive(base: Path) -> Iterator[None]:
    """Step 2: the barrier `LOCK_EX` (the lock file made if lost), waited for
    at most `RESTORE_WAIT`; then no publisher has the database open."""
    fd = _lock_for_create(base)
    try:
        _flock(fd, fcntl.LOCK_EX, RESTORE_WAIT)
        RESTORE_STEP("barrier")
        yield
    finally:
        os.close(fd)


def _roll_back(base: Path) -> None:
    """Step 3: SQLite rolls a hot journal back at the start of a read
    transaction of the old database, opened with `open_db`'s pragmas; the
    journal is gone after it. A journal without a database, a failed read or
    a journal still there is `Unavailable("journal")`; nothing is removed."""
    if not _lexists(base / JOURNAL):
        return
    if not _lexists(base / DB_NAME):
        raise Unavailable("journal")
    try:
        with _mapped():
            conn = _connect(base / DB_NAME)
            try:
                with _transaction(conn, "BEGIN"):
                    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            finally:
                conn.close()
    except Unavailable:
        raise Unavailable("journal") from None
    if _lexists(base / JOURNAL):
        raise Unavailable("journal")


def _move(src: Path, dst: Path) -> None:
    """`rename`, then `fsync` of both directories (once when they are one)."""
    os.rename(src, dst)
    fsutil.sync_dir(dst.parent)
    if src.parent != dst.parent:
        fsutil.sync_dir(src.parent)


def _new_intent() -> dict[str, str]:
    """The set-aside directory (`set-aside/<UTC>`) and the stage of a run."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return {"set_aside_dir": f"{SET_ASIDE}/{stamp}", "new_tmp": RESTORE_DB}


def _write_intent(base: Path, intent: dict[str, str]) -> dict[str, str]:
    """(а): `restore.intent` names the set-aside directory and the stage;
    `write_atomic` syncs the file and the directory."""
    text = json.dumps(intent, sort_keys=True) + "\n"
    fsutil.write_atomic(base / RESTORE_INTENT, text, 0o600)
    RESTORE_STEP("intent")
    return intent


def _read_intent(base: Path) -> dict[str, str]:
    """The intent of an interrupted `--set-aside-old`; anything but the
    shape `_write_intent` writes is `restore_incomplete`."""
    try:
        fd = os.open(base / RESTORE_INTENT, _READ_FLAGS)
        try:
            raw = os.read(fd, 4096)
        finally:
            os.close(fd)
        intent = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise Unavailable("restore_incomplete") from None
    ok = (
        isinstance(intent, dict)
        and intent.keys() == {"set_aside_dir", "new_tmp"}
        and intent["new_tmp"] == RESTORE_DB
        and isinstance(intent["set_aside_dir"], str)
        and _INTENT_DIR.fullmatch(intent["set_aside_dir"]) is not None
    )
    if not ok:
        raise Unavailable("restore_incomplete")
    return intent


def _set_aside(base: Path, intent: dict[str, str]) -> None:
    """(б)-(д) under `LOCK_EX`, each judged by the files on disk, so that a
    repeat continues from the first step not done: (б) `set-aside/<UTC>/`;
    (в) the journal, then the database moved there; (г) the stage renamed
    into place; (д) the intent removed. While the stage exists the database
    in place (if any) is the old one; the journal goes first, so the new
    database never meets the old journal."""
    aside, new = base / intent["set_aside_dir"], base / intent["new_tmp"]
    _make_aside_dirs(base, intent, "restore_incomplete")  # idempotent
    RESTORE_STEP("set-aside-dir")
    if _lexists(new):
        _move_aside(base / JOURNAL, aside / JOURNAL)
        RESTORE_STEP("journal-moved")
        _move_aside(base / DB_NAME, aside / DB_NAME)
        RESTORE_STEP("db-moved")
        _move(new, base / DB_NAME)
        RESTORE_STEP("installed")
    elif not _lexists(base / DB_NAME) or _lexists(base / JOURNAL):
        raise Unavailable("restore_incomplete")
    fsutil.remove_durably(base / RESTORE_INTENT)


def _make_aside_dirs(base: Path, intent: dict[str, str], reason: str) -> None:
    """(б): `set-aside/` and `set-aside/<UTC>/`, each `_make_dir`; made
    before the intent (failure: `reason` = `set_aside`, nothing changed) and
    again, idempotently, by `_set_aside` (`restore_incomplete`)."""
    aside = base / intent["set_aside_dir"]
    for path in (aside.parent, aside):
        _make_dir(path, reason)


def _make_dir(path: Path, reason: str) -> None:
    """A `0700` directory of this user, durable (present: settled). Anything
    else found there (a file, a link, another mode or owner) or an `OSError`
    is `Unavailable(reason)`: nothing is moved through it."""
    try:
        fd = os.open(path.parent, _DIR_FLAGS)
        try:
            fsutil.make_dir(fd, path.name, 0o700, path=path)
        finally:
            os.close(fd)
        ok = _is_own_dir(path)
    except OSError:
        ok = False
    if not ok:
        raise Unavailable(reason)


def _move_aside(src: Path, dst: Path) -> None:
    if not _lexists(src):
        return
    if _lexists(dst):
        raise Unavailable("restore_incomplete")  # never two of one name
    _move(src, dst)


def restore_text(reason: str) -> str:
    """The human's text of a refused `restore`."""
    if reason.startswith("copy: "):
        return f"копия не принята ({reason.removeprefix('copy: ')}): ничего не изменено"
    return RESTORE_TEXTS.get(reason, f"perimeter restore: {reason}")


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
    stamp = now()
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
            str(fields.get("observed_at", stamp)),
            stamp,
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
    with the root's last epoch, read with the write lock held. The event is
    always inserted: one past `MAX_EVENTS` is kept, and from then on every
    open is `too_many_events` (fail-closed, a human decides). SQLite's
    failures are `Unavailable`."""
    root = root_of(req)
    with _mapped(), _write(conn):
        row = _by_key(conn, key)
        if row is None:
            head = epochs.head(root)
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

    One write transaction; the head is read with its lock held. A head other
    than `expected_snapshot_sha` is `stale_snapshot` with the head's SHA.
    Otherwise every `pending` event of the root is registered at the head's
    last epoch and `blocked`/`clear` computed."""
    root = root_of(req)
    try:
        with _mapped(), _write(conn):
            head = epochs.head(root)
            if isinstance(head, Unavailable):
                return status_unavailable(head.reason)
            if head.sha != req.fields["expected_snapshot_sha"]:
                moved = {"snapshot_sha": head.sha}
                return Response("stale_snapshot", "snapshot_moved", moved)
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
    stamp = now()
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
            (event_id, stamp, head.last_epoch, provenance),
        )


# --- self-check ------------------------------------------------------------------


def stats(layout: Layout) -> PerimeterStats | Unavailable:
    """Events, database size and free space (read under the barrier)."""
    base = layout.perimeter_dir
    try:
        with open_db(layout) as db, _mapped():
            events = _count(db.conn)
            size = os.lstat(base / DB_NAME).st_size
        vfs = os.statvfs(base)
    except Unavailable as exc:
        return exc
    except OSError as exc:
        return _io(exc)
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
            if exc.reason == "storage_full" and _not_committed(db.conn, key):
                # the transaction rolled back and the key has no row: not done
                raise Refused("storage_full", verified_not_done=True) from None
            raise Refused(f"perimeter_unavailable: {exc.reason}") from None


def _not_committed(conn: sqlite3.Connection, key: str) -> bool:
    """The event of `key` is provably absent (a fresh read finds no row)."""
    try:
        return _by_key(conn, key) is None
    except sqlite3.Error:
        return False


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
    "INTEGRITY_TEXT",
    "JOURNAL",
    "LOCK",
    "LOST_TEXT",
    "MARKER",
    "MAX_COPY",
    "MAX_EVENTS",
    "NO_BINDING",
    "RESTORE_DB",
    "RESTORE_INTENT",
    "RESTORE_STEP",
    "RESTORE_TEXTS",
    "RESTORE_WAIT",
    "SET_ASIDE",
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
    "restore",
    "restore_conservative",
    "restore_text",
    "root_of",
    "stats",
    "status_unavailable",
]
