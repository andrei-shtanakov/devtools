"""Узкий клиент журнала: совпадение формата с df/ на одном файле базы (b1, Р-16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from df.publisher import request as preq
from governance import act_journal, act_journal_wire
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_time import Unavailable
from governance.act_trust import STALE, Binding, TrustRoot, TrustSnapshot

#: Транспорт решения стенда: полный журнал перечитывает голову им (Р-23, Task 11c).
TRANSPORT = object()
BINDING = Binding("github.com", 11, "owner/df-trust", "main", 22, "public")


def snap(epoch: int = 1, sha: str = "1" * 40) -> TrustSnapshot:
    return TrustSnapshot(
        TrustRoot("github.com", 11, "main", sha),
        "owner/df-trust",
        SourceV2("owner/ap", "main", "a" * 40, frozenset({"o"})),
        tuple(Epoch(i, "a" * 40) for i in range(1, epoch + 1)),
        transport=TRANSPORT,
    )


class Head:
    def __init__(self, current: object) -> None:
        self.current = current

    def __call__(self, transport: object, binding: Binding) -> object:
        assert transport is TRANSPORT and binding == BINDING
        return self.current


@pytest.fixture()
def pair(monkeypatch: pytest.MonkeyPatch):
    """`pair(tmp_path, head)` — полный и узкий журналы на одной базе; полный
    перечитывает голову через `head` (`act_journal.read_trust_snapshot`)."""

    def make(tmp_path: Path, head: Head):
        monkeypatch.setattr(act_journal, "read_trust_snapshot", head)
        full = act_journal.DevJournal(tmp_path.resolve(), "run-1", BINDING, 22)
        narrow = act_journal_wire.DevStoreJournal(tmp_path.resolve(), "run-1", 22)
        return full, narrow

    return make


def test_same_directory_as_full_journal(tmp_path: Path, pair) -> None:
    full, narrow = pair(tmp_path, Head(snap()))
    assert narrow.base == full.layout.perimeter_dir


def test_missing_store_is_unavailable(tmp_path: Path, pair) -> None:
    _, narrow = pair(tmp_path, Head(snap()))
    got = narrow.status(snap())
    assert isinstance(got, Unavailable) and got.reason.endswith("не создан")
    assert isinstance(narrow.record(snap(), "df-trust", "x"), Unavailable)


def test_narrow_pending_blocks_and_full_journal_completes_it(
    tmp_path: Path, pair
) -> None:
    head = Head(snap(1))
    full, narrow = pair(tmp_path, head)
    assert full.status(snap(1)) == "clear"  # полный журнал создаёт базу
    assert narrow.status(snap(1)) == "clear"
    assert narrow.record(snap(1), "approval-policy", "push у профиля мержа") is None
    assert narrow.status(snap(1)) == "blocked"  # pending блокирует
    assert full.status(snap(1)) == "blocked"  # база цела, pending → registered(1)
    head.current = snap(2, "2" * 40)
    assert full.status(snap(2, "2" * 40)) == "clear"
    assert narrow.status(snap(2, "2" * 40)) == "clear"


def test_registered_by_full_journal_seen_by_narrow(tmp_path: Path, pair) -> None:
    head = Head(snap(1))
    full, narrow = pair(tmp_path, head)
    full.record(snap(1), "df-trust", "x")
    assert narrow.status(snap(1)) == full.status(snap(1)) == "blocked"


def test_restore_intent_and_corruption_unavailable(tmp_path: Path, pair) -> None:
    full, narrow = pair(tmp_path, Head(snap()))
    full.status(snap())
    (narrow.base / "restore.intent").write_text("x")
    got = narrow.status(snap())
    assert isinstance(got, Unavailable) and got.reason.endswith("restore_incomplete")
    (narrow.base / "restore.intent").unlink()
    (narrow.base / "events.sqlite").write_bytes(b"not a database")
    assert isinstance(narrow.status(snap()), Unavailable)


class Run:
    def __init__(self, response: dict) -> None:
        self.response, self.calls = response, []

    def __call__(self, argv, *, input, capture_output, timeout, check):  # noqa: A002
        self.calls.append((argv, input))

        class Done:
            returncode = 0
            stdout = (json.dumps(self.response) + "\n").encode()

        return Done()


def _resp(outcome: str, result: dict) -> dict:
    return {
        "schema": "df/publisher-response/v1",
        "outcome": outcome,
        "reason": None,
        "result": result,
        "evidence": {},
    }


def test_wire_requests_parse_as_publisher_requests() -> None:
    run = Run(
        _resp(
            "done",
            {
                "state": "clear",
                "reason": None,
                "snapshot_sha": "1" * 40,
                "last_epoch": 1,
                "events": [],
            },
        )
    )
    wire = act_journal_wire.WireJournal("run-7", run=run)
    assert wire.status(snap()) == "clear"
    wire.record(snap(), "approval-policy", "x")
    (argv1, data1), (argv2, data2) = run.calls
    assert argv1[-1] == "perimeter-status" and argv2[-1] == "record-violation"
    assert (
        preq.parse("perimeter-status", data1).fields["expected_snapshot_sha"]
        == "1" * 40
    )
    assert preq.parse("record-violation", data2).fields["source"] == "approval-policy"


def test_wire_stale_and_foreign_snapshot() -> None:
    stale = act_journal_wire.WireJournal("r", run=Run(_resp("stale_snapshot", {})))
    assert stale.status(snap()) == STALE
    foreign = act_journal_wire.WireJournal(
        "r",
        run=Run(
            _resp(
                "done",
                {
                    "state": "clear",
                    "reason": None,
                    "snapshot_sha": "9" * 40,
                    "last_epoch": 1,
                },
            )
        ),
    )
    got = foreign.status(snap())
    assert (
        isinstance(got, Unavailable)
        and got.reason == "периметр: ответ не о снимке решения"
    )


def test_wire_garbled_or_refused_answers_unavailable() -> None:
    """b4: ответ издателя не разобран / запись не принята — `Unavailable` с причиной."""

    class Garbled(Run):
        def __call__(self, argv, **kw):  # noqa: ANN001, ANN003
            class Done:
                returncode = 1
                stdout = b"not json\n"

            return Done()

    garbled = act_journal_wire.WireJournal("r", run=Garbled({}))
    got = garbled.status(snap())
    assert isinstance(got, Unavailable) and "ответ издателя не разобран" in got.reason
    refused = act_journal_wire.WireJournal(
        "r", run=Run({**_resp("refused", {}), "reason": "busy"})
    )
    got = refused.record(snap(), "df-trust", "x")
    assert (
        isinstance(got, Unavailable) and got.reason == "record-violation: refused: busy"
    )


def test_record_insert_failure_is_unavailable(
    tmp_path: Path, pair, monkeypatch
) -> None:
    """b4: вставка события в хранилище не удалась — `Unavailable`, не «записано»."""
    import os
    import sqlite3

    full, narrow = pair(tmp_path, Head(snap()))
    full.status(snap())

    class Refusing:
        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    monkeypatch.setattr(
        narrow, "_open", lambda: (os.open(os.devnull, os.O_RDONLY), Refusing())
    )
    got = narrow.record(snap(), "df-trust", "x")
    assert isinstance(got, Unavailable) and "событие не записано" in got.reason


def test_premerge_selection_follows_publisher_presence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(act_journal_wire, "PUBLISH", tmp_path / "df-publish")
    dev = act_journal_wire.journal_for_premerge(
        env={}, state_home=tmp_path, run_id="r", policy_repository_id=22
    )
    assert isinstance(dev, act_journal_wire.DevStoreJournal)
    (tmp_path / "df-publish").write_text("")
    got = act_journal_wire.journal_for_premerge(
        env={}, state_home=tmp_path, run_id="r", policy_repository_id=22
    )
    assert isinstance(got, Unavailable) and "DF_RUN_ID" in got.reason
    assert isinstance(
        act_journal_wire.journal_for_premerge(
            env={"DF_RUN_ID": "run-7"},
            state_home=tmp_path,
            run_id="r",
            policy_repository_id=22,
        ),
        act_journal_wire.WireJournal,
    )


def test_publish_refuses_anything_but_the_publisher(monkeypatch) -> None:
    """Место порождения процесса — только вызов издателя; иное argv — отказ."""
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
    prefix = [act_journal_wire.SUDO, "-n", "-u", "df-publisher"]
    prefix.append(str(act_journal_wire.PUBLISH))
    for argv in (
        ["sh", "-c", "x"],
        [act_journal_wire.SUDO, "-n", "-u", "root", "x", "y"],
        [*prefix, "merge"],  # чужая операция издателя
        [*prefix, "perimeter-status", "extra"],
        prefix,
    ):
        with pytest.raises(OSError, match="не вызов издателя"):
            act_journal_wire._publish(argv)


def test_publish_runs_known_operations(monkeypatch) -> None:
    import subprocess

    ran: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: ran.append(argv))
    for op in ("perimeter-status", "record-violation"):
        argv = [act_journal_wire.SUDO, "-n", "-u", "df-publisher"]
        act_journal_wire._publish([*argv, str(act_journal_wire.PUBLISH), op])
    assert [a[-1] for a in ran] == ["perimeter-status", "record-violation"]


def test_missing_lock_with_database_named_exactly(tmp_path: Path, pair) -> None:
    """M8: нет `db.lock` при существующей базе — не «lost»/«не создан»."""
    full, narrow = pair(tmp_path, Head(snap()))
    full.status(snap())
    (narrow.base / "db.lock").unlink()
    got = narrow.status(snap())
    assert isinstance(got, Unavailable) and got.reason.endswith("нет db.lock при базе")


def test_open_checks_are_the_publishers() -> None:
    """Константы и SQL проверок открытия — те же, что у `df.publisher.perimeter`."""
    import sqlite3

    from df.publisher import perimeter as store

    assert act_journal_wire.MAX_EVENTS == store.MAX_EVENTS
    assert act_journal_wire.SCHEMA_VERSION == store.SCHEMA_VERSION
    assert act_journal_wire._INVARIANTS == store._INVARIANTS
    for sql in act_journal_wire._INVARIANTS:  # JSON is checked in Python
        assert "json_" not in sql.lower(), sql
    import inspect

    assert act_journal_wire._BINDING_IDS == store._BINDING_IDS
    for name in ("_observations_ok", "_json", "_no_constant"):  # the same check
        mine = inspect.getsource(getattr(act_journal_wire, name))
        assert mine == inspect.getsource(getattr(store, name))
    conn = sqlite3.connect(":memory:")
    for sql in store.SCHEMA:
        conn.execute(sql)
    assert act_journal_wire.schema_digest(conn) == act_journal_wire.SCHEMA_DIGEST


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 100_000,
        "[" * 100_000 + "]" * 100_000,
        "[" * 30_000 + "]" * 30_000,  # under `MAX_JSON`: the parser's recursion
        "[" * 60_000,
        "[" + "1, " * 30_000 + "1]",  # over `MAX_JSON` bytes
        "[NaN]",
        '{"a": Infinity}',
        "[-Infinity]",
        "[" + "9" * 5000 + "]",
        b"[1]",
        5,
        None,
    ],
    ids=[
        "deep_open",
        "deep_closed",
        "deep_under_cap",
        "deep_open_under_cap",
        "oversized",
        "nan",
        "infinity",
        "minus_infinity",
        "huge_int",
        "blob",
        "integer",
        "null",
    ],
)
def test_hostile_json_is_not_json_in_both_copies(raw: object) -> None:
    """Both copies' `_json` turn a hostile column into `None` (the row is
    then `integrity`), never an exception: deep nesting, an oversized
    value, NaN/Infinity, a huge int, a non-text value."""
    from df.publisher import perimeter as store

    for module in (store, act_journal_wire):
        assert module._json(raw) is None  # type: ignore[arg-type]
    if isinstance(raw, str) and raw.startswith("[[[") and len(raw) <= store.MAX_JSON:
        with pytest.raises(RecursionError):  # the guard is what catches it
            json.loads(raw)
    assert act_journal_wire.MAX_JSON == store.MAX_JSON


def test_legitimate_json_still_parses_in_both_copies() -> None:
    from df.publisher import perimeter as store

    for module in (store, act_journal_wire):
        assert module._json("[1, 2]") == [1, 2]
        assert module._json('{"df_trust_repository_id": 5}') == {
            "df_trust_repository_id": 5
        }


def _sql(narrow: act_journal_wire.DevStoreJournal, *statements: str) -> None:
    import sqlite3

    conn = sqlite3.connect(narrow.base / "events.sqlite", isolation_level=None)
    for sql in statements:
        conn.execute(sql)
    conn.close()


def _too_many(tmp_path: Path, monkeypatch, full, narrow, head) -> None:
    from df.publisher import perimeter as store

    for i in range(3):
        full.record(snap(1), "df-trust", f"e{i}")
    head.current = snap(2, "2" * 40)  # события E1 больше не блокируют
    monkeypatch.setattr(store, "MAX_EVENTS", 2)
    monkeypatch.setattr(act_journal_wire, "MAX_EVENTS", 2)


def _schema_drift(tmp_path, monkeypatch, full, narrow, head) -> None:
    _sql(narrow, "CREATE TABLE extra (x INTEGER)")


def _bad_state(tmp_path, monkeypatch, full, narrow, head) -> None:
    full.record(snap(1), "df-trust", "x")
    head.current = snap(2, "2" * 40)
    _sql(narrow, "UPDATE events SET state = 'odd'")


def _meta_version(tmp_path, monkeypatch, full, narrow, head) -> None:
    _sql(narrow, "UPDATE meta SET value = '3' WHERE key = 'schema'")


def _observation(full, narrow, sql: str) -> None:
    """One observation through the publisher's code, then `sql` on its row."""
    from df.publisher import perimeter as store
    from df.publisher import trust_state
    from df.stage2_config import TrustBinding

    binding = TrustBinding("github.com", 11, "owner/df-trust", "main", 22, "public")
    ctx = trust_state.ctx_id("trust-status", binding, "a", "r", None, "1" * 40, None)
    with store.open_db(full.layout) as db:
        trust_state.open_observation(
            db, binding.root_id, ctx, route="trust-status", binding=binding,
            admin_digest="a", rules="r", accepted=None, head="1" * 40, policy=None,
        )  # fmt: skip
    _sql(narrow, f"UPDATE trust_observations SET {sql}")


def _observation_type(tmp_path, monkeypatch, full, narrow, head) -> None:
    _observation(full, narrow, "event_ids = '{}'")


def _observation_event(tmp_path, monkeypatch, full, narrow, head) -> None:
    _observation(full, narrow, "event_ids = '[7]'")


def _observation_binding(tmp_path, monkeypatch, full, narrow, head) -> None:
    _observation(full, narrow, "binding = '{}'")


def _observation_deep(tmp_path, monkeypatch, full, narrow, head) -> None:
    deep = "[" * 30_000 + "]" * 30_000  # under `MAX_JSON`: RecursionError
    _observation(full, narrow, f"event_ids = '{deep}'")


def _marker_link(tmp_path, monkeypatch, full, narrow, head) -> None:
    (narrow.base / "events.sqlite").unlink()
    (narrow.base / "created").unlink()
    (narrow.base / "created").symlink_to(tmp_path / "nowhere")


def _intent_link(tmp_path, monkeypatch, full, narrow, head) -> None:
    (narrow.base / "restore.intent").symlink_to(tmp_path / "nowhere")


@pytest.mark.parametrize(
    ("spoil", "reason"),
    [
        (_too_many, "too_many_events"),
        (_schema_drift, "integrity"),
        (_bad_state, "integrity"),
        (_meta_version, "integrity"),
        (_observation_type, "integrity"),
        (_observation_event, "integrity"),
        (_observation_binding, "integrity"),
        (_observation_deep, "integrity"),
        (_intent_link, "restore_incomplete"),
        (_marker_link, "lost"),
    ],
    ids=[
        "max_events",
        "schema",
        "invariant",
        "meta",
        "observation_type",
        "observation_event",
        "observation_binding",
        "observation_deep",
        "intent_symlink",
        "marker_link",
    ],
)
def test_both_clients_refuse_the_same_store(
    tmp_path: Path, pair, monkeypatch, spoil, reason: str
) -> None:
    """Ревью #666: на одном файле узкий клиент не отвечает `clear` там, где
    полный журнал (`open_db` → `_verify`) отказывает."""
    head = Head(snap(1))
    full, narrow = pair(tmp_path, head)
    assert full.status(snap(1)) == narrow.status(snap(1)) == "clear"
    spoil(tmp_path, monkeypatch, full, narrow, head)
    now = head.current
    got_full, got_narrow = full.status(now), narrow.status(now)
    assert isinstance(got_full, Unavailable) and reason in got_full.reason
    assert isinstance(got_narrow, Unavailable) and got_narrow.reason.endswith(reason)
