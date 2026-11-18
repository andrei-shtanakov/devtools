"""Вопросы интервью без терминала (lite/interview_remote.py), #766 срез 3.

discovery подменена; агент не нужен — модуль его не зовёт.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governance import interview as iv
from lite import gate_repair as gr
from lite import interview_chat as ic
from lite import interview_remote as ir
from lite import pending
from lite.interview_state import Entry, SessionView

UPSTREAM = """---
schema: discovery-brief
---

## Functional Requirements

- **FR-01** Модель под задачу в конфигурации
  traces: [G-01]
  **Priority**: Must
  **Acceptance**: выбирает пользователь
- **FR-03** Повторить весь функционал spec-runner
  traces: [J-01]
  **Priority**: Must
  **Acceptance**: как в spec-runner
"""
_GC05 = (
    "GC-05 error [coverage.feasibility_review] Must-требование {} из "
    "upstream.md не получило feasibility-вердикта"
)
FINDINGS = [
    _GC05.format("FR-03"),
    "GC-13 error [IF-01] нет traces → существующий S",
    "GC-08 error [NFR-01] нет **Acceptance**/**Target**",
    "GC-13 error [AP-01] нет traces → существующий S/CON",
    _GC05.format("FR-01"),
]
SYS_QID = "engineer.systems.01"
IF_QID = "engineer.interfaces.01"
FEAS_QID = "engineer.feasibility_review.01"


def _engineer_view(extra: list[dict[str, Any]] | None = None) -> SessionView:
    records = {
        SYS_QID: [{"id": "S-01", "body": "spec-runner"}],
        IF_QID: [
            {"id": "IF-01", "body": "CLI-команда"},
            {"id": "AP-01", "body": "ядро без сети"},
            {"id": "NFR-01", "body": "быстро"},
        ],
    }
    feas = {"text": "в целом реализуемо", "entries": list(extra or [])}
    entries = [Entry(r["id"], r["body"], {}) for rs_ in records.values() for r in rs_]
    entries += [Entry(e["id"], e["body"], {}) for e in extra or []]
    holders = {r["id"]: q for q, rs_ in records.items() for r in rs_}
    holders.update({e["id"]: FEAS_QID for e in extra or []})
    return SessionView(
        frame="engineer",
        required={"systems": "S", "interfaces": "IF", "feasibility_review": None},
        optional={"arch_preferences": "AP"},
        entries=entries,
        declared=[],
        holders=holders,
        answers={
            **{
                q: {"text": q, "entries": [dict(r) for r in v]}
                for q, v in records.items()
            },
            FEAS_QID: feas,
        },  # fmt: skip
        answered_keys=["systems", "interfaces", "feasibility_review"],
        readiness="incomplete",
        findings=["topic_uncovered:arch_preferences"],
        asked={
            SYS_QID: "systems",
            IF_QID: "interfaces",
            FEAS_QID: "feasibility_review",
        },
    )


def _customer_view() -> SessionView:
    return SessionView(
        frame="customer",
        required={"goals": "G", "jobs": "J", "functions": "FR"},
        optional={"metrics": "M"},
        entries=[Entry("G-01", "Цель", {"Priority": "Must"})],
        declared=[],
        holders={"G-01": "customer.goals.01"},
        answers={"customer.goals.01": {"text": "цель", "entries": []}},
        answered_keys=["goals"],
        readiness="incomplete",
        asked={"customer.goals.01": "goals"},
    )


def _status(code: int, findings: list[str] | None = None) -> iv.DiscoveryReply:
    envelope: dict[str, Any] = {"next_action": {}, "findings": findings or []}
    return iv.DiscoveryReply(code, envelope, "")


def _question_reply() -> iv.DiscoveryReply:
    action = {
        "question_id": "customer.jobs.01",
        "coverage_key": "jobs",
        "question_text": "Какие задачи решают пользователи?",
    }
    return iv.DiscoveryReply(20, {"next_action": action}, "")


EMPTY = {"skipped": [], "done": []}


def _keys(reply: iv.DiscoveryReply, view: SessionView, marks: dict) -> list[str]:
    """Все ключи прохода: каждый выданный помечается сделанным."""
    marks = {k: list(v) for k, v in marks.items()}
    out = []
    while got := ir.next_question(
        reply, view, marks, incomplete=True, upstream=UPSTREAM
    ):
        out.append(got[0].key)
        marks["done"].append(got[0].key)
    return out


def test_question_from_code_20() -> None:
    view = _customer_view()
    got = ir.next_question(
        _question_reply(), view, EMPTY, incomplete=False, upstream=""
    )
    assert got is not None
    q, data = got
    assert q.kind == pending.INTERVIEW_QUESTION
    assert q.key == "customer.jobs.01"
    assert (
        q.text == "Вопрос 2 (задачи пользователей): Какие задачи решают пользователи?"
    )
    assert ir.HUMAN_PLACEHOLDER in data["prompt"]
    assert "Правила полей" in data["prompt"]
    assert data["topic"] == "jobs"
    assert data["coverage"] == ir.coverage(view)
    assert data["findings"] == ir.findings(view)
    assert data["answer"].startswith(f"dfl answer --items <файл> --id {q.id}")
    assert "G-01" not in q.text


def test_topup_after_incomplete_skips_marked() -> None:
    view = _customer_view()
    got = ir.next_question(_status(10), view, EMPTY, incomplete=True, upstream="")
    assert got is not None
    q, data = got
    assert q.kind == pending.INTERVIEW_TOPUP
    assert q.key == "topic:jobs"
    assert q.text == f"Не хватает: задачи пользователей — {ic.TOP_UP_HINT}"
    assert data["holder"] == "customer.goals.01"
    assert ir.HUMAN_PLACEHOLDER in data["prompt"]
    marks = {"skipped": ["topic:jobs"], "done": []}
    q2, _ = ir.next_question(_status(10), view, marks, incomplete=True, upstream="")
    assert q2.key == "topic:functions"
    marks["done"].append("topic:functions")
    assert (
        ir.next_question(_status(10), view, marks, incomplete=True, upstream="") is None
    )
    # не неполный конец — добора нет
    assert (
        ir.next_question(_status(0), view, EMPTY, incomplete=False, upstream="") is None
    )


def test_repair_order_criterion_traces_verdict() -> None:
    view = _engineer_view()
    assert _keys(_status(10, FINDINGS), view, EMPTY) == [
        "criterion:NFR-01",
        "traces:IF-01,AP-01",
        "verdict:FR-03",
        "verdict:FR-01",
    ]
    reply = _status(10, FINDINGS)
    q, data = ir.next_question(reply, view, EMPTY, incomplete=True, upstream=UPSTREAM)
    assert q.kind == pending.INTERVIEW_REPAIR
    assert q.text == f"{gr.CRITERIA_INTRO}: «быстро»"
    assert data["entries"] == [{"id": "NFR-01", "body": "быстро"}]
    assert data["holder"] == IF_QID
    assert ir.HUMAN_PLACEHOLDER in data["prompt"]
    marks = {"skipped": ["criterion:NFR-01"], "done": []}
    q, data = ir.next_question(reply, view, marks, incomplete=True, upstream=UPSTREAM)
    assert q.text == gr.TRACES_INTRO
    assert [e["id"] for e in data["entries"]] == ["IF-01", "AP-01"]
    marks["done"].append(q.key)
    q, data = ir.next_question(reply, view, marks, incomplete=True, upstream=UPSTREAM)
    assert (
        q.text == f"FR-03 «Повторить весь функционал spec-runner» — {gr.VERDICT_HINT}"
    )
    assert data["prompt"] is None
    assert data["holder"] == FEAS_QID
    assert "dfl reply" in data["answer"]
    assert data["findings"] == [
        {"raw": "topic_uncovered:arch_preferences",
         "text": "topic_uncovered:arch_preferences"}
    ]  # fmt: skip


def test_verdict_reply_builds_x_entry_to_feasibility_holder() -> None:
    view = _engineer_view()
    q, _ = ir.next_question(
        _status(10, [_GC05.format("FR-03")]),
        view,
        EMPTY,
        incomplete=True,
        upstream=UPSTREAM,
    )
    sent: list[tuple[str, dict]] = []

    def resend(qid: str, payload: str) -> iv.DiscoveryReply:
        sent.append((qid, yaml.safe_load(payload)))
        return iv.DiscoveryReply(10, {"operation": {"status": "ok"}}, "")

    def send(qid: str, payload: str) -> iv.DiscoveryReply:
        raise AssertionError("вердикт не отправляется send")

    rec = pending.as_dict(q)
    assert ir.apply_reply(rec, "да", view, send=send, resend=resend) is None
    qid, doc = sent[0]
    assert qid == FEAS_QID
    assert doc["text"] == "в целом реализуемо\n\nFR-03: реализуемо как есть"
    assert doc["entries"] == [
        {"id": "X-01", "body": "FR-03: реализуемо как есть", "target": "FR-03",
         "status": "resolved"}
    ]  # fmt: skip
    # пропуск — ничего не отправлено
    assert ir.apply_reply(rec, "пропустить", view, send=send, resend=resend) is None
    assert len(sent) == 1


def test_two_verdicts_get_distinct_numbers() -> None:
    sent: list[dict] = []

    def resend(qid: str, payload: str) -> iv.DiscoveryReply:
        sent.append(yaml.safe_load(payload))
        return iv.DiscoveryReply(10, {}, "")

    def send(qid: str, payload: str) -> iv.DiscoveryReply:
        raise AssertionError

    rec = {"kind": pending.INTERVIEW_REPAIR, "key": "verdict:FR-03"}
    ir.apply_reply(rec, "да", _engineer_view(), send=send, resend=resend)
    # следующий вызов — по свежему снимку сессии: X-01 уже записан
    after = _engineer_view(sent[0]["entries"])
    rec2 = {"kind": pending.INTERVIEW_REPAIR, "key": "verdict:FR-01"}
    ir.apply_reply(rec2, "нужен прокси", after, send=send, resend=resend)
    assert [e["id"] for e in sent[1]["entries"]] == ["X-01", "X-02"]
    assert sent[1]["entries"][1] == {
        "id": "X-02", "body": "FR-01: нужен прокси", "target": "FR-01",
        "status": "open",
    }  # fmt: skip


def test_question_skip_sends_bare_text_and_returns_refusal() -> None:
    sent: list[tuple[str, str]] = []

    def send(qid: str, payload: str) -> iv.DiscoveryReply:
        sent.append((qid, payload))
        return iv.DiscoveryReply(1, {"operation": {"reason": "нельзя"}}, "")

    def resend(qid: str, payload: str) -> iv.DiscoveryReply:
        raise AssertionError

    rec = {"kind": pending.INTERVIEW_QUESTION, "key": "customer.jobs.01"}
    view = _customer_view()
    got = ir.apply_reply(rec, "не знаю", view, send=send, resend=resend)
    assert got == "нельзя"
    assert sent == [("customer.jobs.01", "text: не знаю\n")]
    topup = {"kind": pending.INTERVIEW_TOPUP, "key": "topic:jobs"}
    assert ir.apply_reply(topup, "пропустить", view, send=send, resend=resend) is None
    assert len(sent) == 1


def test_none_when_all_marked() -> None:
    view = _engineer_view()
    keys = _keys(_status(10, FINDINGS), view, EMPTY)
    marks = {"skipped": keys[:2], "done": keys[2:]}
    reply = _status(10, FINDINGS)
    assert (
        ir.next_question(reply, view, marks, incomplete=True, upstream=UPSTREAM) is None
    )


def test_marks_roundtrip_and_clear(tmp_path: Path) -> None:
    assert ir.load_marks(tmp_path) == {"skipped": [], "done": []}
    ir.mark(tmp_path, "topic:jobs", "skipped")
    ir.mark(tmp_path, "verdict:FR-01", "done")
    ir.mark(tmp_path, "topic:jobs", "skipped")
    assert ir.load_marks(tmp_path) == {
        "skipped": ["topic:jobs"],
        "done": ["verdict:FR-01"],
    }
    assert (tmp_path / ir.PASS_FILE).exists()
    ir.clear_marks(tmp_path)
    assert not (tmp_path / ir.PASS_FILE).exists()
    assert ir.load_marks(tmp_path) == {"skipped": [], "done": []}


def test_coverage_lists_required_then_optional() -> None:
    got = ir.coverage(_customer_view())
    assert [c["key"] for c in got] == ["goals", "jobs", "functions", "metrics"]
    assert [c["required"] for c in got] == [True, True, True, False]
    assert got[0] == {
        "key": "goals",
        "label": "цели",
        "required": True,
        "covered": True,
        "entries": [{"id": "G-01", "body": "Цель", "fields": {"Priority": "Must"}}],
    }
    assert got[1]["covered"] is False and got[1]["entries"] == []


def test_findings_render_plain_text() -> None:
    view = _customer_view()
    view.findings.append("required topic 'jobs' is not covered")
    assert ir.findings(view) == [
        {"raw": "required topic 'jobs' is not covered",
         "text": "не раскрыта тема: задачи пользователей"}
    ]  # fmt: skip
