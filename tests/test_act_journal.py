"""Журнал событий периметра потребителя (спека A rev 16 §6.3; Р-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from df.publisher import request as preq
from governance import act_journal, act_trust
from governance.act_epochs import Epoch
from governance.act_source import SourceV2
from governance.act_time import Unavailable

ROOT = act_trust.TrustRoot("github.com", 11, "main", "1" * 40)
#: Транспорт решения стенда: снимок несёт его, журнал передаёт `ForgeEpochs.via`.
TRANSPORT = object()


def snap(epoch: int = 1, sha: str = "1" * 40) -> act_trust.TrustSnapshot:
    epochs = tuple(Epoch(i, "a" * 40) for i in range(1, epoch + 1))
    root = act_trust.TrustRoot("github.com", 11, "main", sha)
    return act_trust.TrustSnapshot(
        root,
        "owner/df-trust",
        SourceV2("owner/ap", "main", "a" * 40, frozenset({"o"})),
        epochs,
        transport=TRANSPORT,
    )


class Head:
    """`read_trust_snapshot` стенда: текущий снимок корня или недоступность;
    перечитывание обязано идти транспортом решения."""

    def __init__(self, current: object) -> None:
        self.current = current

    def __call__(self, transport: object, binding: act_trust.Binding) -> object:
        assert transport is TRANSPORT and binding == BINDING
        return self.current


POLICY_ID = 22
BINDING = act_trust.Binding(
    "github.com", 11, "owner/df-trust", "main", POLICY_ID, "public"
)


@pytest.fixture()
def journal(monkeypatch: pytest.MonkeyPatch):
    """`journal(tmp_path, head)` — dev-журнал, перечитывающий голову через `head`."""

    def make(tmp_path: Path, head: Head) -> act_journal.DevJournal:
        monkeypatch.setattr(act_journal, "read_trust_snapshot", head)
        return act_journal.DevJournal(tmp_path.resolve(), "run-1", BINDING, POLICY_ID)

    return make


def test_event_names_the_violated_repository(tmp_path: Path, journal) -> None:
    """B1-18: событие роли approval-policy несёт id approval-policy, не df-trust."""
    import sqlite3

    j = journal(tmp_path, Head(snap(1)))
    j.record(snap(1), "approval-policy", "push у токена")
    j.record(snap(1), "df-trust", "push у профиля мержа")
    db = j.layout.perimeter_dir / "events.sqlite"
    rows = (
        sqlite3.connect(db)
        .execute(
            "SELECT violated_role, violated_repository_id FROM events ORDER BY event_id"
        )
        .fetchall()
    )
    assert rows == [("approval-policy", POLICY_ID), ("df-trust", 11)]


def test_clear_then_blocked_then_new_epoch(tmp_path: Path, journal) -> None:
    head = Head(snap(1))
    j = journal(tmp_path, head)
    assert j.status(snap(1)) == "clear"
    assert j.record(snap(1), "approval-policy", "push у токена") is None
    assert j.status(snap(1)) == "blocked"
    head.current = snap(2, "2" * 40)
    assert j.status(snap(2, "2" * 40)) == "clear"


def test_pending_blocks_until_epochs_known(tmp_path: Path, journal) -> None:
    """Приёмка №36: журнал эпох недоступен при регистрации → pending, блокирует."""
    head = Head(Unavailable("df-trust: 403"))
    j = journal(tmp_path, head)
    assert j.record(snap(1), "df-trust", "x") is None
    assert isinstance(j.status(snap(1)), Unavailable)
    head.current = snap(1)
    assert j.status(snap(1)) == "blocked"  # pending завершён номером 1, блокирует
    head.current = snap(2, "2" * 40)
    assert j.status(snap(2, "2" * 40)) == "clear"


def test_stale_snapshot_reported(tmp_path: Path, journal) -> None:
    head = Head(snap(1, "2" * 40))
    assert journal(tmp_path, head).status(snap(1)) == act_trust.STALE


def test_corrupted_store_blocks(tmp_path: Path, journal) -> None:
    """Заметки части A, п. 12: битое хранилище — fail-closed, новая эпоха не снимает."""
    head = Head(snap(1))
    j = journal(tmp_path, head)
    j.status(snap(1))
    db = j.layout.perimeter_dir / "events.sqlite"
    db.write_bytes(b"not a database")
    got = j.status(snap(1))
    assert isinstance(got, Unavailable) and got.reason.startswith(
        "журнал периметра (dev): "
    )
    head.current = snap(2, "2" * 40)
    assert isinstance(j.status(snap(2, "2" * 40)), Unavailable)


def test_status_answer_about_other_snapshot_unavailable() -> None:
    """B3-6: ответ журнала не о снимке решения (другой sha или эпоха) — не `clear`."""
    for result in (
        {"state": "clear", "snapshot_sha": "9" * 40, "last_epoch": 1},
        {"state": "clear", "snapshot_sha": "1" * 40, "last_epoch": 2},
    ):
        got = act_journal._status_of(preq.Response("done", None, result), snap(1))
        assert (
            isinstance(got, Unavailable)
            and got.reason == "периметр: ответ не о снимке решения"
        )
    clear = {"state": "clear", "snapshot_sha": "1" * 40, "last_epoch": 1}
    assert (
        act_journal._status_of(preq.Response("done", None, clear), snap(1)) == "clear"
    )
    odd = act_journal._status_of(preq.Response("done", "x", {"state": "odd"}), snap(1))
    assert isinstance(odd, Unavailable) and odd.reason == "периметр: x"


def test_forge_epochs_refuse_other_root(monkeypatch) -> None:
    """b4: источник эпох отвечает только о своём корне."""
    monkeypatch.setattr(act_journal, "read_trust_snapshot", Head(snap(1)))
    epochs = act_journal.ForgeEpochs(BINDING)
    epochs.via(TRANSPORT)
    other = epochs.head(("github.com", 99, "main"))
    assert (
        isinstance(other, act_journal.store.Unavailable)
        and other.reason == "root_mismatch"
    )
    monkeypatch.setattr(act_journal, "read_trust_snapshot", Head(Unavailable("403")))
    gone = act_journal.ForgeEpochs(BINDING)
    gone.via(TRANSPORT)
    lost = gone.head(ROOT.id_tuple)
    assert (
        isinstance(lost, act_journal.store.Unavailable)
        and lost.reason == "df-trust: 403"
    )


@pytest.mark.parametrize("passed", [False, True], ids=["no_via", "via_none"])
def test_forge_epochs_without_transport_unavailable(monkeypatch, passed: bool) -> None:
    """Р-23, C7-01: без транспорта решения голова не перечитывается и транспорт
    не строится — `store.Unavailable`, `read_trust_snapshot` не вызывается."""
    monkeypatch.setattr(
        act_journal, "read_trust_snapshot", lambda t, b: pytest.fail("перечитано")
    )
    epochs = act_journal.ForgeEpochs(BINDING)
    if passed:  # снимок без транспорта (`TrustSnapshot.transport` — None)
        epochs.via(None)
    got = epochs.head(ROOT.id_tuple)
    assert isinstance(got, act_journal.store.Unavailable)
    assert got.reason == "транспорт решения не передан"


def test_dev_record_refused_by_store_is_unavailable(
    tmp_path: Path, journal, monkeypatch
) -> None:
    """b4: хранилище отказало в записи события — `Unavailable` с его причиной."""
    j = journal(tmp_path, Head(snap(1)))
    j.status(snap(1))

    def refuse(*a, **k):
        raise act_journal.store.Unavailable("integrity")

    monkeypatch.setattr(act_journal.store, "record_violation", refuse)
    got = j.record(snap(1), "df-trust", "x")
    assert (
        isinstance(got, Unavailable)
        and got.reason == "журнал периметра (dev): integrity"
    )


def test_installed_record_refused_is_unavailable(monkeypatch) -> None:
    """b4: издатель не принял событие — `Unavailable` с исходом и причиной."""

    def call(
        data: bytes, layout=None, *, operation: str, timeout: float = 60.0
    ) -> bytes:
        resp = {
            "schema": "df/publisher-response/v1",
            "outcome": "refused",
            "reason": "busy",
            "result": {},
            "evidence": {},
        }
        return (json.dumps(resp) + "\n").encode()

    monkeypatch.setattr(act_journal.perimeter_client, "sudo_call", call)
    got = act_journal.InstalledJournal("run-7").record(snap(1), "df-trust", "x")
    assert (
        isinstance(got, Unavailable) and got.reason == "record-violation: refused: busy"
    )


def test_deleted_store_with_marker_is_lost(tmp_path: Path, journal) -> None:
    head = Head(snap(1))
    j = journal(tmp_path, head)
    j.status(snap(1))
    (j.layout.perimeter_dir / "events.sqlite").unlink()
    got = j.status(snap(1))
    assert isinstance(got, Unavailable) and "lost" in got.reason


def test_installed_journal_requires_df_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: True)
    got = act_journal.journal_for(
        state_home=tmp_path,
        run_id="r",
        binding=BINDING,
        env={},
        policy_repository_id=POLICY_ID,
    )
    assert isinstance(got, Unavailable) and "DF_RUN_ID" in got.reason
    dev = act_journal.journal_for(
        state_home=tmp_path,
        run_id="r",
        binding=BINDING,
        env={"DF_RUN_ID": "run-7"},
        policy_repository_id=POLICY_ID,
    )
    assert isinstance(dev, act_journal.InstalledJournal)


def test_dev_journal_without_publisher(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: False)
    got = act_journal.journal_for(
        state_home=tmp_path,
        run_id="r",
        binding=BINDING,
        env={"DF_RUN_ID": "ignored"},
        policy_repository_id=POLICY_ID,
    )
    assert isinstance(got, act_journal.DevJournal)


def test_installed_record_sends_publisher_request(monkeypatch) -> None:
    sent: list[tuple[bytes, str]] = []

    def call(
        data: bytes, layout=None, *, operation: str, timeout: float = 60.0
    ) -> bytes:
        sent.append((data, operation))
        resp = {
            "schema": "df/publisher-response/v1",
            "outcome": "done",
            "reason": None,
            "result": {"event_id": 1, "state": "registered", "epoch": 1},
            "evidence": {},
        }
        return (json.dumps(resp) + "\n").encode()

    monkeypatch.setattr(act_journal.perimeter_client, "sudo_call", call)
    assert (
        act_journal.InstalledJournal("run-7").record(snap(1), "df-trust", "x") is None
    )
    data, op = sent[0]
    req = preq.parse("record-violation", data)
    assert op == "record-violation" and req.fields["source"] == "df-trust"


def _installed_answer(monkeypatch, outcome: str, result: dict) -> None:
    """Издатель стенда: `perimeter_client.status` зовёт `sudo_call` модуля."""

    def call(
        data: bytes,
        layout=None,
        *,
        operation: str = "perimeter-status",
        timeout: float = 60.0,
    ):
        assert operation == "perimeter-status"
        resp = {
            "schema": "df/publisher-response/v1",
            "outcome": outcome,
            "reason": "snapshot_moved" if outcome != "done" else None,
            "result": result,
            "evidence": {},
        }
        return (json.dumps(resp) + "\n").encode()

    monkeypatch.setattr(act_journal.perimeter_client, "sudo_call", call)


def test_installed_status_stale_snapshot_is_stale(monkeypatch) -> None:
    """M4 (R1): снимок сдвинулся у издателя — `STALE`, решающий перечитает доверие."""
    _installed_answer(monkeypatch, "stale_snapshot", {"snapshot_sha": "2" * 40})
    assert act_journal.InstalledJournal("run-7").status(snap(1)) == act_trust.STALE


def test_installed_status_unavailable(monkeypatch) -> None:
    _installed_answer(
        monkeypatch,
        "done",
        preq.perimeter_result("unavailable", "no_binding", None, None),
    )
    got = act_journal.InstalledJournal("run-7").status(snap(1))
    assert isinstance(got, Unavailable) and got.reason == "периметр: no_binding"


def test_installed_status_passes_clear_and_blocked(monkeypatch) -> None:
    for state in ("clear", "blocked"):
        _installed_answer(
            monkeypatch, "done", preq.perimeter_result(state, None, "1" * 40, 1)
        )
        assert act_journal.InstalledJournal("run-7").status(snap(1)) == state


def test_dev_journal_mkdir_failure_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    """M8: каталог хранилища не создать — `Unavailable` из `journal_for`."""
    monkeypatch.setattr(act_journal, "_publisher_present", lambda: False)
    (tmp_path / "darkfactory").write_text("file", encoding="utf-8")
    got = act_journal.journal_for(
        state_home=tmp_path,
        run_id="r",
        binding=BINDING,
        env={},
        policy_repository_id=POLICY_ID,
    )
    assert isinstance(got, Unavailable) and got.reason.startswith(
        "журнал периметра (dev): "
    )


def test_record_without_prior_status_registers_epoch(tmp_path: Path, journal) -> None:
    """Р-23 (ревью Task 11c, Minor 2): `record` сам передаёт транспорт снимка
    `ForgeEpochs.via` — событие сразу `registered` с эпохой, а не `pending`."""
    import sqlite3

    j = journal(tmp_path, Head(snap(1)))
    assert j.record(snap(1), "df-trust", "push у профиля мержа") is None
    db = j.layout.perimeter_dir / "events.sqlite"
    rows = sqlite3.connect(db).execute("SELECT state, epoch FROM events").fetchall()
    assert rows == [("registered", 1)]
