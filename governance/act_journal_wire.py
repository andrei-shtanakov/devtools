"""Узкий клиент журнала периметра для обвязки мержа (ревизия b1, спека §6.3).

`act_premerge` исполняется `merge-pr.sh`, и его цепочка импортов — только
защищённые `_HARNESS_PREFIXES`; полный журнал (`act_journal`) тянет `df/`.
Этот модуль — тот же журнал без `df/`: в установке `df` — протокол издателя
`df/publisher-request/v1` поверх `sudo -n -u df-publisher …/df-publish`; в
режиме разработки — то же хранилище SQLite: правило блокировки (событие
`pending` либо `registered(E)` с `E ≥` последней эпохи) и вставка события
`pending` одной транзакцией. Номер эпохи `pending` назначает полный журнал
на пути решения. Совпадение форматов держит `tests/test_act_journal_wire.py`.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from governance.act_time import Unavailable
from governance.act_trust import STALE, TrustSnapshot

REQUEST_SCHEMA = "df/publisher-request/v1"
RESPONSE_SCHEMA = "df/publisher-response/v1"
PUBLISHER = "df-publisher"
SUDO = "/usr/bin/sudo"
PUBLISH = Path("/opt/df/libexec/df-publish")
#: Операции издателя, которые этот клиент вызывает (и только они).
OPERATIONS = ("perimeter-status", "record-violation")
#: Каталог хранилища режима разработки — тот же, что `act_journal.DevJournal`.
DEV_BASE = Path("darkfactory/perimeter/root/var/lib/df/perimeter")
DB_NAME, LOCK, MARKER, RESTORE_INTENT = (
    "events.sqlite",
    "db.lock",
    "created",
    "restore.intent",
)
BUSY_TIMEOUT_MS = 5000
LOCK_WAIT = 5.0
MAX_DETAIL = 4000
_BLOCKING = (
    "SELECT event_id FROM events WHERE root_host = ? AND root_repository_id = ? "
    "AND root_branch = ? "
    "AND (state = 'pending' OR (state = 'registered' AND epoch >= ?))"
)
_LOCK_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
# Обязательные проверки открытия хранилища — копия `df.publisher.perimeter`
# (`open_db` → `_verify`): на том же файле узкий клиент отказывает там же,
# где полный журнал. Совпадение констант и SQL держит
# `tests/test_act_journal_wire.py` (сверка с `df/` и двойники на одном файле).
MAX_EVENTS = 10_000
SCHEMA_VERSION = "2"
RESTORED = ("false", "true")
#: sha256 отсортированных строк `type\tname\tsql` из `sqlite_master` схемы издателя.
SCHEMA_DIGEST = "012d109ababcd26fb8ddb5e1ee702491b294724bf2aeeafbb48e6cadd803cef3"
_INVARIANTS = (
    "SELECT count(*) FROM events WHERE state NOT IN ('pending', 'registered')",
    (
        "SELECT count(*) FROM events WHERE state = 'registered' AND typeof(epoch) "
        "!= 'integer'"
    ),
    "SELECT count(*) FROM events WHERE state = 'pending' AND epoch IS NOT NULL",
    (
        "SELECT count(*) FROM transitions WHERE event_id NOT IN (SELECT event_id "
        "FROM events)"
    ),
    (
        "SELECT count(*) FROM trust_observations WHERE outcome NOT IN ('open', "
        "'unresolved', 'accepted', 'finding_registered', 'superseded', "
        "'conservative')"
    ),
    (
        "SELECT count(*) FROM trust_observations WHERE route NOT IN "
        "('perimeter-status', 'trust-status', 'merge')"
    ),
    "SELECT count(*) FROM events WHERE typeof(root_repository_id) != 'integer'",
    (
        "SELECT count(*) FROM trust_heads WHERE typeof(root_repository_id) != "
        "'integer' OR typeof(last_epoch) != 'integer' OR typeof(row_version) != "
        "'integer'"
    ),
    (
        "SELECT count(*) FROM trust_observations WHERE typeof(root_repository_id) "
        "!= 'integer' OR typeof(row_version) != 'integer'"
    ),
    "SELECT count(*) FROM trust_heads WHERE row_version < 1",
    (
        "SELECT count(*) FROM trust_heads WHERE finding_event_id IS NOT NULL AND "
        "finding_event_id NOT IN (SELECT event_id FROM events)"
    ),
)
#: The longest observation JSON column `_json` parses (bytes). A legitimate
#: `event_ids` holds the ids of at most 16 findings (kind x role) and one
#: conservative event -- a few hundred bytes; a `binding` holds two ids, the
#: host, the full name (<= 140 B), the branch (<= 255 B) and the history
#: profile path (<= PATH_MAX, 4096 B) -- under 5 KiB. 64 KiB is over ten times
#: either and bounds the parse of a hostile row.
MAX_JSON = 64 << 10
#: The repository ids every observation's `binding` names.
_BINDING_IDS = ("df_trust_repository_id", "approval_policy_repository_id")


def _observations_ok(conn: sqlite3.Connection) -> bool:
    """The JSON of every observation row, checked in Python (SQLite's JSON
    functions are not guaranteed below 3.38): `event_ids` a list of ids of
    existing events, `binding` an object with integer repository ids."""
    events = {row[0] for row in conn.execute("SELECT event_id FROM events")}
    rows = conn.execute("SELECT event_ids, binding FROM trust_observations")
    for raw_ids, raw_binding in rows:
        ids, binding = _json(raw_ids), _json(raw_binding)
        if not isinstance(ids, list) or not isinstance(binding, dict):
            return False
        if any(type(i) is not int or i not in events for i in ids):
            return False
        if any(type(binding.get(k)) is not int for k in _BINDING_IDS):
            return False
    return True


def _json(raw: str) -> object:
    """`raw` parsed; `None` when it is not JSON text of at most `MAX_JSON`
    bytes, nests deeper than the parser's recursion allows, or names
    NaN/Infinity. An int past the digit limit is a `ValueError` too."""
    if not isinstance(raw, str):
        return None
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_JSON:
        return None
    try:
        return json.loads(raw, parse_constant=_no_constant)
    except (RecursionError, ValueError):
        return None


def _no_constant(name: str) -> object:
    """`parse_constant`: NaN, Infinity and -Infinity are not JSON."""
    raise ValueError(f"JSON constant {name}")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def schema_digest(conn: sqlite3.Connection) -> str:
    """Отпечаток схемы базы (то, что издатель сравнивает целиком)."""
    found = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    )
    rows = sorted("\t".join((str(t), str(n), str(s))) for t, n, s in found)
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _refusal(conn: sqlite3.Connection) -> str | None:
    """Проверки `_verify` издателя одним чтением; `None` — база годна."""
    conn.execute("BEGIN")
    try:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            return "integrity"
        if schema_digest(conn) != SCHEMA_DIGEST:
            return "integrity"
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.keys() != {"schema", "admission", "restored"}:
            return "integrity"
        if meta["schema"] != SCHEMA_VERSION or meta["restored"] not in RESTORED:
            return "integrity"
        if any(conn.execute(sql).fetchone()[0] for sql in _INVARIANTS):
            return "integrity"
        if not _observations_ok(conn):
            return "integrity"
        if conn.execute("SELECT count(*) FROM events").fetchone()[0] > MAX_EVENTS:
            return "too_many_events"
        # `restored_pending` допускает только полный журнал (`admit`)
        return None if meta["admission"] == "ready" else "restore_pending"
    finally:
        conn.execute("COMMIT")


def _root_id(snap: TrustSnapshot) -> dict[str, object]:
    host, repository_id, branch = snap.root.id_tuple
    return {"host": host, "repository_id": repository_id, "branch": branch}


def _publish(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    """Единственное место порождения процесса модуля (`spawn-sites.toml`):
    только `sudo -n -u df-publisher …/df-publish <операция>`."""
    prefix = [SUDO, "-n", "-u", PUBLISHER, str(PUBLISH)]
    if argv[:5] != prefix or len(argv) != 6 or argv[5] not in OPERATIONS:
        raise OSError(f"не вызов издателя: {argv}")
    return subprocess.run(argv, **kwargs)  # noqa: PLW1510 — check=False у вызывающего


class WireJournal:
    """Установка `df`: операции издателя `perimeter-status` и `record-violation`."""

    def __init__(self, df_run_id: str, *, run: Callable[..., Any] = _publish) -> None:
        self.run_id = df_run_id
        self._run = run

    def _call(self, op: str, fields: Mapping[str, object]) -> dict | Unavailable:
        data = {
            "schema": REQUEST_SCHEMA,
            "request_id": f"perimeter-{uuid.uuid4().hex}",
            "run_id": self.run_id,
            **fields,
        }
        argv = [SUDO, "-n", "-u", PUBLISHER, str(PUBLISH), op]
        try:
            done = self._run(
                argv,
                input=json.dumps(data, sort_keys=True).encode("utf-8"),
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Unavailable(f"{op}: вызов издателя не состоялся: {exc}")
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        try:
            resp = json.loads(lines[0]) if len(lines) == 1 else None
        except ValueError:
            resp = None
        if not isinstance(resp, dict) or resp.get("schema") != RESPONSE_SCHEMA:
            return Unavailable(
                f"{op}: ответ издателя не разобран (код {done.returncode})"
            )
        return resp

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        resp = self._call(
            "perimeter-status",
            {
                "root_id": _root_id(snap),
                "expected_snapshot_sha": snap.root.snapshot_sha,
            },
        )
        if isinstance(resp, Unavailable):
            return resp
        if resp.get("outcome") == "stale_snapshot":
            return STALE
        result = resp.get("result") if resp.get("outcome") == "done" else None
        state = result.get("state") if isinstance(result, dict) else None
        if state not in ("blocked", "clear"):
            reason = (
                result.get("reason") if isinstance(result, dict) else resp.get("reason")
            )
            return Unavailable(f"периметр: {reason}")
        if (result.get("snapshot_sha"), result.get("last_epoch")) != (
            snap.root.snapshot_sha,
            snap.epochs[-1].epoch,
        ):
            return Unavailable("периметр: ответ не о снимке решения")
        return str(state)

    def record(
        self, snap: TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        resp = self._call(
            "record-violation",
            {
                "root_id": _root_id(snap),
                "source": source,
                "observed_at": _now(),
                "detail": detail[:MAX_DETAIL],
            },
        )
        if isinstance(resp, Unavailable):
            return resp
        if resp.get("outcome") != "done":
            return Unavailable(
                f"record-violation: {resp.get('outcome')}: {resp.get('reason')}"
            )
        return None


class DevStoreJournal:
    """Режим разработки: то же хранилище, что `act_journal.DevJournal`, без `df/`."""

    def __init__(
        self, state_home: Path, run_id: str, policy_repository_id: int
    ) -> None:
        self.base = state_home / DEV_BASE
        self.run_id = run_id
        self.policy_repository_id = policy_repository_id

    def _open(self) -> tuple[int, sqlite3.Connection] | Unavailable:
        if os.path.lexists(self.base / RESTORE_INTENT):
            return Unavailable("журнал периметра (dev): restore_incomplete")
        db = self.base / DB_NAME
        try:
            if not stat.S_ISREG(os.lstat(db).st_mode):
                return Unavailable("журнал периметра (dev): integrity")
            fd = os.open(self.base / LOCK, _LOCK_FLAGS)
        except FileNotFoundError as exc:
            if Path(exc.filename or "") == self.base / LOCK:
                return Unavailable(f"журнал периметра (dev): нет {LOCK} при базе")
            lost = os.path.lexists(self.base / MARKER)
            return Unavailable(
                f"журнал периметра (dev): {'lost' if lost else 'не создан'}"
            )
        except OSError as exc:
            return Unavailable(f"журнал периметра (dev): {exc}")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return Unavailable("журнал периметра (dev): busy")
                time.sleep(0.02)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                f"{db.as_uri()}?mode=rw&nofollow=1", uri=True, isolation_level=None
            )
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous = EXTRA")
            if sys.platform == "darwin":
                conn.execute("PRAGMA fullfsync = ON")
            refused = _refusal(conn)
            if refused is not None:
                raise sqlite3.DatabaseError(refused)
        except sqlite3.Error as exc:
            if conn is not None:
                conn.close()
            os.close(fd)
            return Unavailable(f"журнал периметра (dev): {exc}")
        return fd, conn

    def status(self, snap: TrustSnapshot) -> str | Unavailable:
        opened = self._open()
        if isinstance(opened, Unavailable):
            return opened
        fd, conn = opened
        try:
            rows = conn.execute(
                _BLOCKING, (*snap.root.id_tuple, snap.epochs[-1].epoch)
            ).fetchall()
        except sqlite3.Error as exc:
            return Unavailable(f"журнал периметра (dev): {exc}")
        finally:
            conn.close()
            os.close(fd)
        return "blocked" if rows else "clear"

    def record(
        self, snap: TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        opened = self._open()
        if isinstance(opened, Unavailable):
            return opened
        fd, conn = opened
        key = f"perimeter-{uuid.uuid4().hex}"
        violated = (
            self.policy_repository_id
            if source == "approval-policy"
            else snap.root.repository_id
        )
        provenance = json.dumps(
            {
                "snapshot_sha": snap.root.snapshot_sha,
                "unavailable": "epoch_assigned_on_completion",
            },
            sort_keys=True,
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO events (root_host, root_repository_id, root_branch, "
                "request_key, request_id, run_id, sender, violated_role, "
                "violated_repository_id, detected_by, observed_at, registered_at, "
                "state, epoch, provenance, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)",
                (
                    *snap.root.id_tuple,
                    f"premerge:{key}",
                    key,
                    self.run_id,
                    os.getuid(),
                    source,
                    violated,
                    f"sender:{os.getuid()}",
                    _now(),
                    _now(),
                    provenance,
                    detail[:MAX_DETAIL],
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            return Unavailable(f"журнал периметра (dev): событие не записано: {exc}")
        finally:
            conn.close()
            os.close(fd)
        return None


def journal_for_premerge(
    *, env: Mapping[str, str], state_home: Path, run_id: str, policy_repository_id: int
) -> WireJournal | DevStoreJournal | Unavailable:
    """Тот же выбор, что `act_journal.journal_for` (Р-5): издатель есть — протокол."""
    if PUBLISH.exists():
        df_run = env.get("DF_RUN_ID")
        if not df_run:
            return Unavailable(
                "периметр: установка df без контекста прогона (DF_RUN_ID)"
            )
        return WireJournal(df_run)
    return DevStoreJournal(state_home, run_id, policy_repository_id)
