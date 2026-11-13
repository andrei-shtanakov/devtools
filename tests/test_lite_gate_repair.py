"""Починка брифа по находкам проверки (lite/gate_repair.py), devtools#755.

Человек, агент и discovery подменены; находки — в формате gate_check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from governance import interview as iv
from governance.discovery_contract import gate_check
from lite import gate_repair as gr
from lite.interview_state import Entry, SessionView
from lite.structure import AgentUnavailable

SESSION = "sess-e"
SYS_QID = "engineer.systems.01"
IF_QID = "engineer.interfaces.01"
AP_QID = "engineer.arch_preferences.01"
FEAS_QID = "engineer.feasibility_review.01"
S1 = {"id": "S-01", "body": "spec-runner"}
CON1 = {"id": "CON-01", "body": "только Claude Agent SDK"}
IF1 = {"id": "IF-01", "body": "CLI-команда запуска"}
IF2 = {"id": "IF-02", "body": "формат tasks.md"}
AP1 = {"id": "AP-01", "body": "ядро без сети"}
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
- **FR-05** TUI
  traces: [J-02]
  **Priority**: Should
  **Acceptance**: удобно
"""
TRACE_FINDINGS = [
    "GC-13 error [IF-01] нет traces → существующий S",
    "GC-13 error [IF-02] нет traces → существующий S",
    "GC-13 error [AP-01] нет traces → существующий S/CON",
]
_GC05 = (
    "GC-05 error [coverage.feasibility_review] Must-требование {} из "
    "upstream.md не получило feasibility-вердикта"
)
VERDICT_FINDINGS = [_GC05.format("FR-03"), _GC05.format("FR-01")]


def _view() -> SessionView:
    records = {SYS_QID: [S1, CON1], IF_QID: [IF1, IF2], AP_QID: [AP1]}
    return SessionView(
        frame="engineer",
        required={
            "systems": "S",
            "interfaces": "IF",
            "constraints": "CON",
            "arch_preferences": "AP",
            "feasibility_review": None,
        },  # fmt: skip
        optional={},
        entries=[
            Entry(r["id"], r["body"], {}) for rs_ in records.values() for r in rs_
        ],
        declared=[],
        holders={r["id"]: q for q, rs_ in records.items() for r in rs_},
        answers={
            **{
                q: {"text": q, "entries": [dict(r) for r in rs_]}
                for q, rs_ in records.items()
            },  # fmt: skip
            FEAS_QID: {"text": "в целом реализуемо"},
        },
        answered_keys=[
            "systems",
            "interfaces",
            "arch_preferences",
            "feasibility_review",
        ],
        readiness="ready",
        asked={
            SYS_QID: "systems",
            IF_QID: "interfaces",
            AP_QID: "arch_preferences",
            FEAS_QID: "feasibility_review",
        },  # fmt: skip
    )


@dataclass
class Disc:
    refuse: bool = False
    resent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def resend(self, qid: str, payload: str) -> iv.DiscoveryReply:
        self.resent.append((qid, yaml.safe_load(payload)))
        if self.refuse:
            return iv.DiscoveryReply(1, {"operation": {"reason": "bad"}}, "")
        return iv.DiscoveryReply(10, {"operation": {"status": "ok"}}, "")


def _asker(*answers: str | None) -> Any:
    queue = list(answers)
    asked: list[str] = []

    def ask(header: str) -> str | None:
        asked.append(header)
        return queue.pop(0)

    ask.asked = asked  # type: ignore[attr-defined]
    return ask


def _agent(*answers: Any) -> Any:
    queue = [a if isinstance(a, str) else json.dumps(a) for a in answers]

    def agent(prompt: str) -> str:
        item = queue.pop(0)
        if item == "DOWN":
            raise AgentUnavailable("нет сети")
        return item

    return agent


def _repair(findings: list[str], ask: Any, agent: Any, disc: Disc) -> None:
    gr.repair(findings, SESSION, ask=ask, agent=agent, resend=disc.resend,
              load=lambda s: _view(), upstream=lambda: UPSTREAM)  # fmt: skip


TRACES = {"traces": {"IF-01": ["S-01"], "IF-02": ["S-01", "CON-01"],
                     "AP-01": ["S-01", "CON-01", "S-99"]}}  # fmt: skip


def test_traces_proposed_confirmed_and_written_per_holder(capsys) -> None:
    disc = Disc()
    _repair(TRACE_FINDINGS, _asker("да"), _agent(TRACES), disc)
    out = capsys.readouterr().out
    assert "«CLI-команда запуска» → «spec-runner»" in out
    (q1, ifs), (q2, aps) = disc.resent
    assert q1 == IF_QID and q2 == AP_QID
    # IF → только S (CON отброшен), AP → S/CON, несуществующий S-99 отброшен
    assert ifs["entries"] == [
        {**IF1, "traces": ["S-01"]},
        {**IF2, "traces": ["S-01"]},
    ]
    assert aps["entries"] == [{**AP1, "traces": ["S-01", "CON-01"]}]
    assert gr.GATE_LEFT.split(":")[0] not in out


def test_traces_declined_nothing_sent(capsys) -> None:
    disc = Disc()
    _repair(TRACE_FINDINGS, _asker("нет"), _agent(TRACES), disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=3) in capsys.readouterr().out


def test_traces_agent_down_counts_left(capsys) -> None:
    disc = Disc()
    _repair(TRACE_FINDINGS, _asker(), _agent("DOWN"), disc)
    out = capsys.readouterr().out
    assert "связи не предложены: нет сети" in out
    assert gr.GATE_LEFT.format(n=3) in out and disc.resent == []


def test_traces_unlinked_item_counts_left(capsys) -> None:
    disc = Disc()
    partial = {"traces": {"IF-01": ["S-01"], "IF-02": [], "AP-01": ["CON-01"]}}
    _repair(TRACE_FINDINGS, _asker("да"), _agent(partial), disc)
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_verdicts_become_x_entries_in_feasibility_answer() -> None:
    disc = Disc()
    ask = _asker("да", "к 1 декабря — только минимальный срез")
    _repair(VERDICT_FINDINGS, ask, _agent(), disc)
    assert "FR-03 «Повторить весь функционал spec-runner»" in ask.asked[0]
    [(qid, payload)] = disc.resent
    assert qid == FEAS_QID
    assert payload["entries"] == [
        {"id": "X-01", "body": "FR-03: реализуемо как есть",
         "target": "FR-03", "status": "resolved"},
        {"id": "X-02", "body": "FR-01: к 1 декабря — только минимальный срез",
         "target": "FR-01", "status": "open"},
    ]  # fmt: skip
    assert payload["text"].startswith("в целом реализуемо\n\nFR-03:")


def test_verdict_skip_counts_left(capsys) -> None:
    disc = Disc()
    _repair(VERDICT_FINDINGS, _asker(None, "да"), _agent(), disc)
    [(_qid, payload)] = disc.resent
    assert [e["target"] for e in payload["entries"]] == ["FR-01"]
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_verdict_refusal_counts_all_left(capsys) -> None:
    disc = Disc(refuse=True)
    _repair(VERDICT_FINDINGS, _asker("да", "да"), _agent(), disc)
    out = capsys.readouterr().out
    assert "не записано: bad" in out and gr.GATE_LEFT.format(n=2) in out


def test_verdict_entries_pass_gate_rules() -> None:
    """Записи-вердикты в отрисованном виде закрывают GC-05 и проходят GC-09."""
    body = "\n".join([
        "## Stakeholder Conflicts", "",
        "- **X-01** FR-03: реализуемо как есть", "  target: FR-03",
        "  status: resolved",
    ])  # fmt: skip
    [entry] = gate_check.parse_body(body)
    assert entry.status() == "resolved"
    assert "FR-03" in body


def test_must_bodies_only_must_frs() -> None:
    assert gr.must_bodies(UPSTREAM) == {
        "FR-01": "Модель под задачу в конфигурации",
        "FR-03": "Повторить весь функционал spec-runner",
    }


def test_unrelated_findings_ignored(capsys) -> None:
    disc = Disc()
    _repair(["GC-06 error [FR-01] нет traces → G/J"], _asker(), _agent(), disc)
    assert disc.resent == [] and capsys.readouterr().out == ""
