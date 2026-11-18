"""Ручные проверки без терминала: вопрос наружу, ответ внутрь (#766)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import manual_checks as mc
from lite import manual_ask as ma
from lite import pending

BEH = """#### BEH-01: первая
- **checked_by**: `kind: manual` `target: docs/a.md`

#### BEH-02: вторая
- **checked_by**: `kind: manual` `target: docs/b.md`
"""
DT = """#### DT-01: a · type: verify · owner: qa
scenarios: [BEH-01]
depends_on: []
parallel_group: solo

#### DT-02: b · type: verify · owner: qa
scenarios: [BEH-02]
depends_on: [DT-01]
parallel_group: solo
"""


PUBLISHED: list[str] = []


@pytest.fixture(autouse=True)
def _fake_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    PUBLISHED.clear()

    def publish(_ops, rec, **_kw) -> None:
        PUBLISHED.append("x")
        rec["status"] = "completed"

    monkeypatch.setattr(ma.mc, "publish", publish)


def _settle(record: dict, run_dir: Path, *, tty: bool = False, read=None):
    reason = ma.settle(
        record,
        load_texts=lambda: (BEH, DT),
        tty=tty,
        read=read or (lambda _p: None),
        ops=object(),
        repo_slug="o/r",
        pr=1,
        pr_url="u",
        run_id="r1",
        save=lambda: None,
        run_dir=run_dir,
    )
    return reason, list(PUBLISHED)


def _q(record: dict, i: int) -> pending.Question:
    check = mc.checks_of(record)[i]
    return ma.question_of(check, 1)


def test_remote_first_call_writes_question_and_waits(tmp_path: Path) -> None:
    record: dict = {}
    reason, _ = _settle(record, tmp_path)
    assert reason is not None and reason.startswith("ждёт ответа:")
    assert ma.is_wait(reason)
    assert pending.load(tmp_path)["key"] == "BEH-01"
    assert record["answers"] == {}


def test_remote_reply_then_next_question(tmp_path: Path) -> None:
    record: dict = {}
    _settle(record, tmp_path)
    pending.record(tmp_path, _q(record, 0).id, "1")
    reason, _ = _settle(record, tmp_path)
    assert record["answers"]["BEH-01"]["verdict"] == mc.DONE
    assert reason is not None and ma.is_wait(reason)
    assert pending.load(tmp_path)["key"] == "BEH-02"


def test_remote_all_answered_publishes(tmp_path: Path) -> None:
    record: dict = {}
    _settle(record, tmp_path)
    pending.record(tmp_path, _q(record, 0).id, "1")
    _settle(record, tmp_path)
    pending.record(tmp_path, _q(record, 1).id, "2: плохо")
    reason, published = _settle(record, tmp_path)
    assert reason is None and published == ["x"]
    assert record["answers"]["BEH-02"] == {
        "verdict": mc.NOT_DONE,
        "note": "плохо",
        "at": record["answers"]["BEH-02"]["at"],
        "issue": None,
    }


def test_recorded_reply_used_in_terminal(tmp_path: Path) -> None:
    record: dict = {}
    _settle(record, tmp_path)
    pending.record(tmp_path, _q(record, 0).id, "1")
    lines = iter(["1"])

    def read(_prompt: str):
        if "BEH-01" not in record["answers"]:
            pytest.fail("прочитано при записанном ответе")
        return next(lines, None)

    reason, published = _settle(record, tmp_path, tty=True, read=read)
    assert reason is None and published == ["x"]
    assert set(record["answers"]) == {"BEH-01", "BEH-02"}


def test_terminal_answer_clears_pending(tmp_path: Path) -> None:
    record: dict = {}
    _settle(record, tmp_path)
    assert pending.load(tmp_path) is not None
    lines = iter(["1", "1"])
    reason, published = _settle(
        record, tmp_path, tty=True, read=lambda _p: next(lines, None)
    )
    assert reason is None and published == ["x"]
    assert pending.load(tmp_path) is None


def test_live_answer_drops_recorded_reply(tmp_path: Path) -> None:
    """Ответ, записанный пока человек отвечал в терминале, не переживает его."""
    record: dict = {}
    _settle(record, tmp_path)
    q = _q(record, 0)
    lines = iter(["2", "", "1"])
    sent: list[int] = []

    def read(_prompt: str):
        if not sent:
            sent.append(1)
            pending.record(tmp_path, q.id, "1")  # reply приходит во время ввода
        return next(lines, None)

    _settle(record, tmp_path, tty=True, read=read)
    replies = tmp_path / pending.REPLIES_FILE
    assert not replies.exists()
    # следующий вызов без терминала спрашивает заново, а не проходит молча
    record2: dict = {}
    reason, _ = _settle(record2, tmp_path)
    assert reason is not None and ma.is_wait(reason)
