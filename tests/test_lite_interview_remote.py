"""Вопросы интервью без терминала (lite/interview_remote.py), #766 срез 3.

discovery подменена; агент не нужен — модуль его не зовёт.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from governance import interview as iv
from lite import brief_v13 as bv
from lite import gate_repair as gr
from lite import gate_repair_v13 as gv
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
    bodies = "«CLI-команда», «ядро без сети»"  # GC-13 называет пункты, без id
    assert q.text == ir.TRACES_TEXT.format(bodies=bodies)
    assert "предлагаю" not in q.text and "IF-" not in q.text and "AP-" not in q.text
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


# --- контракт брифа v1.3: починка GC-18…GC-21 без терминала (#813) ---

V13_LIVE = [
    "GC-18 error [Translation table] секции нет или в ней нет строк",
    "GC-20 error [Category: cost] нет ни одной NFR категории cost",
    "GC-20 error [Category: perf] нет ни одной NFR категории perf",
    "GC-20 error [NFR-01] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-20 error [NFR-02] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-21 error [owns: process] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: data] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: quality] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: operations] владелец не назван: нет P или открытого Q с этим owns",
]  # fmt: skip
V13_ROW = {
    "words": "запуск одной командой",
    "category": "делать",
    "constraint": ["FR-01"],
    "assumption": "одна команда",
}


def _v13_view(frame: str = "customer") -> SessionView:
    """customer: цель, персона без owns, FR с таблицей, NFR без категории."""
    records = {
        "customer.goals.01": [{"id": "G-01", "body": "Цель"}],
        "customer.personas.01": [{"id": "P-01", "body": "Аналитик"}],
        "customer.functions.01": [
            {"id": "FR-01", "body": "Запуск", "Acceptance": "одна команда"}
        ],
        "customer.nfr.01": [{"id": "NFR-01", "body": "Ответ быстро, за секунды"}],
    }
    answers: dict[str, dict[str, Any]] = {
        q: {"text": q, "entries": [dict(r) for r in rs_]} for q, rs_ in records.items()
    }
    answers["customer.functions.01"]["translations"] = [dict(V13_ROW)]
    entries = [
        Entry(
            r["id"], r["body"], {k: v for k, v in r.items() if k not in ("id", "body")}
        )
        for rs_ in records.values()
        for r in rs_
    ]
    return SessionView(
        frame=frame,
        required={},
        optional={},
        entries=entries,
        declared=[],
        holders={r["id"]: q for q, rs_ in records.items() for r in rs_},
        answers=answers,
        answered_keys=[],
        readiness="incomplete",
        asked={q: q.split(".")[1] for q in records},
    )


def test_v13_keys_order() -> None:
    """Находки живого прогона: категория NFR (NFR-02 без держателя — нет),
    perf, cost, владельцы, недостающие категории таблицы, допущения."""
    view = _v13_view()
    reply = _status(11, V13_LIVE)
    first = ir.next_question(reply, view, EMPTY, incomplete=True, upstream="")
    assert first is not None
    q, data = first
    assert (q.kind, q.key) == (pending.INTERVIEW_REPAIR, "category:NFR-01")
    assert data["hypothesis"] == ["perf"] and data["holder"] == "customer.nfr.01"
    assert "Ответ быстро, за секунды" in q.text and "скорость" in q.text
    assert _keys(reply, view, EMPTY) == [
        "category:NFR-01",
        "nfr:perf",
        "nfr:cost",
        "owner:process",
        "owner:data",
        "owner:quality",
        "owner:operations",
        "table:не делать",
        "table:стоить",
        "table:доказывать",
        "assumptions",
    ]
    marks = {"skipped": [], "done": ["category:NFR-01", "nfr:perf"]}
    got = ir.next_question(reply, view, marks, incomplete=True, upstream="")
    assert got is not None and got[0].key == "nfr:cost"
    question = bv.NUMERIC_QUESTIONS["cost"]
    assert got[0].text == gr.NUMERIC_ASK.format(question=question)
    assert got[1]["holder"] == "customer.nfr.01"
    assert ir.HUMAN_PLACEHOLDER in got[1]["prompt"]
    assert "стоимости" in got[1]["prompt"]
    done = {"skipped": [], "done": _keys(reply, view, EMPTY)[:-1]}
    last = ir.next_question(reply, view, done, incomplete=True, upstream="")
    assert last is not None and last[0].key == "assumptions"
    assert last[1]["rows"] == [
        {"words": "запуск одной командой", "category": "делать",
         "constraint": ["FR-01"],
         "assumption": "одна команда"},
    ]  # fmt: skip
    assert "одна команда" in last[0].text


def test_v13_skipped_for_engineer() -> None:
    view = _v13_view("engineer")
    assert _keys(_status(11, V13_LIVE), view, EMPTY) == []


def test_category_refusal_hint_words_all_parse() -> None:
    """Каждое слово подсказки отказа `category:*` разбирается в свою
    категорию; непонятное слово — отказ с этой подсказкой."""
    view = _v13_view()
    rec = {"kind": pending.INTERVIEW_REPAIR, "key": "category:NFR-01"}
    for category, word in gv.CATEGORY_LABELS.items():
        assert bv.parse_categories(word, []) == [category], word
    try:
        ir.apply_reply(rec, "фиолетовая", view, send=_no_send, resend=_no_send)
    except ValueError as exc:
        hint = str(exc)
    else:
        raise AssertionError("отказа нет")
    assert all(word in hint for word in gv.CATEGORY_LABELS.values())


def _no_send(qid: str, payload: str) -> iv.DiscoveryReply:
    raise AssertionError("отправки быть не должно")
