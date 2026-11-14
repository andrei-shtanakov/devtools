"""Починка брифа по находкам проверки (lite/gate_repair.py), devtools#755.

Человек, агент и discovery подменены; находки — в формате gate_check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from governance import interview as iv
from governance.discovery_contract import gate_check
from lite import gate_repair as gr
from lite import structure as sx
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
    prompts: list[str] = []

    def agent(prompt: str) -> str:
        prompts.append(prompt)
        item = queue.pop(0)
        if item == "DOWN":
            raise AgentUnavailable("нет сети")
        return item

    agent.prompts = prompts  # type: ignore[attr-defined]
    return agent


def _repair(
    findings: list[str], ask: Any, agent: Any, disc: Disc, view: Any = _view
) -> None:
    gr.repair(findings, SESSION, ask=ask, agent=agent, resend=disc.resend,
              load=lambda s: view(), upstream=lambda: UPSTREAM)  # fmt: skip


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


# --- GC-08: гипотеза критерия агента на подтверждение (контракт v1.2) ---

CRIT_FINDINGS = [
    "GC-08 error [FR-01] нет **Acceptance**",
    "GC-08 error [NFR-01] нет **Acceptance**/**Target**",
]
FUNCS_QID = "customer.functions.01"
NFR_QID = "customer.nfr.01"
FUNCS_TEXT = "прогон спеки одной командой, без ручных шагов"
NFR_TEXT = "чтобы было бесшовно"
FR1 = {"id": "FR-01", "body": "Прогон спеки одной командой",
       "traces": ["G-01"], "Priority": "Must"}  # fmt: skip
NFR1 = {"id": "NFR-01", "body": "Бесшовная работа", "Preference": NFR_TEXT}
FR_HYP = "Спека проходит от запуска до отчёта одной командой без ручных шагов"
NFR_HYP = "Переход между шагами без повторного ввода, не дольше значения из q-1"
Q_ITEM = {"prefix": "Q", "body": "Какая пауза между шагами приемлема?",
          "ref": "q-1", "owner_role": "analyst"}  # fmt: skip


def _crit_view() -> SessionView:
    return SessionView(
        frame="customer",
        required={"goals": "G", "functions": "FR", "nfr": "NFR"},
        optional={},
        entries=[
            Entry("G-01", "Цель", {}),
            Entry("FR-01", FR1["body"], {"traces": ["G-01"], "Priority": "Must"}),
            Entry("NFR-01", NFR1["body"], {"Preference": NFR_TEXT}),
        ],
        declared=[],
        holders={
            "G-01": "customer.goals.01",
            "FR-01": FUNCS_QID,
            "NFR-01": NFR_QID,
        },  # fmt: skip
        answers={
            "customer.goals.01": {
                "text": "цель",
                "entries": [{"id": "G-01", "body": "Цель"}],
            },
            FUNCS_QID: {"text": FUNCS_TEXT, "entries": [dict(FR1)]},
            NFR_QID: {"text": NFR_TEXT, "entries": [dict(NFR1)]},
        },  # fmt: skip
        answered_keys=["goals", "functions", "nfr"],
        readiness="ready",
        asked={},
    )


def _fr(acceptance: str, **extra: Any) -> dict[str, Any]:
    item = {"id": "FR-01", "prefix": "FR", "body": FR1["body"],
            "acceptance": acceptance, **extra}  # fmt: skip
    return {"items": [item]}


def _nfr(acceptance: str, *more: dict[str, Any]) -> dict[str, Any]:
    item = {"id": "NFR-01", "prefix": "NFR", "body": NFR1["body"],
            "acceptance": acceptance}  # fmt: skip
    return {"items": [item, *more]}


def _crit(findings: list[str], ask: Any, agent: Any, disc: Disc) -> None:
    _repair(findings, ask, agent, disc, view=_crit_view)


def test_criteria_hypothesis_confirmed_written_with_q() -> None:
    disc = Disc()
    ask = _asker("да", "да")
    agent = _agent(_fr(FR_HYP), _nfr(NFR_HYP, Q_ITEM))
    _crit(CRIT_FINDINGS, ask, agent, disc)
    assert ask.asked[0] == (
        f"«{FR1['body']}»: проверка — {FR_HYP}. Так? («да» — записать; иначе "
        "скажите своими словами, что имелось в виду; «пропустить»)"
    )
    assert "Как проверить" not in "".join(ask.asked)
    # агенту — пункт и слова держащего ответа
    assert FR1["body"] in agent.prompts[0] and FUNCS_TEXT in agent.prompts[0]
    (q1, funcs), (q2, nfr) = disc.resent
    assert (q1, q2) == (FUNCS_QID, NFR_QID)
    assert funcs["entries"] == [{**FR1, "Acceptance": FR_HYP}]
    assert funcs["text"].startswith(FUNCS_TEXT + "\n\n")
    acc = NFR_HYP.replace("q-1", "Q-01")
    assert nfr["entries"] == [
        {**NFR1, "Acceptance": acc},
        {"id": "Q-01", "body": Q_ITEM["body"], "owner_role": "analyst",
         "blocking": False},
    ]  # fmt: skip


def test_criteria_agent_cannot_touch_preference_body_or_priority() -> None:
    disc = Disc()
    proposal = _fr(FR_HYP, preference="выдумано", priority="Could")
    proposal["items"][0]["body"] = "Другая формулировка"
    _crit(CRIT_FINDINGS[:1], _asker("да"), _agent(proposal), disc)
    [(_qid, funcs)] = disc.resent
    assert funcs["entries"] == [{**FR1, "Acceptance": FR_HYP}]


def test_criteria_correction_becomes_preference_and_reproposed(capsys) -> None:
    """«нет, имелось в виду …» → слова в Preference (дописаны к прежним),
    агент предлагает заново, итог записан без второго вопроса."""
    disc = Disc()
    fr_words = "нет, имелось в виду: без единого ручного шага"
    nfr_words = "не терять введённое при переходе"
    ask = _asker(fr_words, nfr_words)
    fr2 = "Прогон без ручных действий между шагами"
    nfr2 = "Введённые данные сохраняются при переходе между шагами"
    agent = _agent(_fr(FR_HYP), _fr(fr2), _nfr(NFR_HYP, Q_ITEM), _nfr(nfr2))
    _crit(CRIT_FINDINGS, ask, agent, disc)
    assert len(ask.asked) == 2  # без второго подтверждения
    assert fr_words in agent.prompts[1] and nfr_words in agent.prompts[3]
    (_q1, funcs), (_q2, nfr) = disc.resent
    assert funcs["entries"] == [{**FR1, "Preference": fr_words, "Acceptance": fr2}]
    assert nfr["entries"] == [
        {**NFR1, "Preference": f"{NFR_TEXT}; {nfr_words}", "Acceptance": nfr2}
    ]
    assert fr_words in funcs["text"]
    out = capsys.readouterr().out
    assert fr2 in out and gr.GATE_LEFT.split(":")[0] not in out


def test_criteria_skip_not_fixed(capsys) -> None:
    disc = Disc()
    _crit(CRIT_FINDINGS, _asker(None, "да"), _agent(_fr(FR_HYP), _nfr("ок")), disc)
    [(qid, _payload)] = disc.resent
    assert qid == NFR_QID
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_criteria_agent_down_no_how_to_check_question(capsys) -> None:
    disc = Disc()
    ask = _asker()
    _crit(CRIT_FINDINGS, ask, _agent("DOWN", "DOWN"), disc)
    out = capsys.readouterr().out
    assert ask.asked == [] and disc.resent == []
    assert "нет сети" in out and "Как проверить" not in out
    assert gr.GATE_LEFT.format(n=2) in out


def test_criteria_malformed_or_dangling_q_not_fixed(capsys) -> None:
    disc = Disc()
    dangling = _nfr("не дольше значения из Q-07")
    agent = _agent("не JSON", "не JSON", dangling, dangling)
    _crit(CRIT_FINDINGS, _asker(), agent, disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=2) in capsys.readouterr().out


def test_criteria_other_entries_rejected(capsys) -> None:
    """Агент чинит только свой пункт; новые не-Q пункты — отказ формы."""
    disc = Disc()
    extra = _fr(FR_HYP)
    extra["items"].append({"prefix": "FR", "body": "Ещё функция"})
    _crit(CRIT_FINDINGS[:1], _asker(), _agent(extra, extra), disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_criteria_reproposal_down_keeps_words_in_preference(capsys) -> None:
    """Повторное предложение не вышло — слова человека всё равно записаны
    (только Preference: без критерия и Q), пункт не исправлен."""
    disc = Disc()
    words = "без единого ручного шага"
    _crit(CRIT_FINDINGS[:1], _asker(words), _agent(_fr(FR_HYP), "DOWN"), disc)
    [(qid, funcs)] = disc.resent
    assert qid == FUNCS_QID
    assert funcs["entries"] == [{**FR1, "Preference": words}]
    assert words in funcs["text"]
    out = capsys.readouterr().out
    assert gr.WORDS_KEPT in out
    assert gr.GATE_LEFT.format(n=1) in out


def test_criteria_display_has_no_ids_and_find_line(capsys) -> None:
    disc = Disc()
    ask = _asker("да")
    _crit(CRIT_FINDINGS[1:], ask, _agent(_nfr(NFR_HYP, Q_ITEM)), disc)
    shown = ask.asked[0] + capsys.readouterr().out
    assert "Q-0" not in shown and "q-1" not in shown
    assert f"«{Q_ITEM['body']}»" in ask.asked[0]
    assert f"  найти: {Q_ITEM['body']}" in shown
    [(_qid, nfr)] = disc.resent
    assert nfr["entries"][0]["Acceptance"].endswith("Q-01")  # в брифе id есть


def test_criteria_correction_with_id_keeps_gate_clean() -> None:
    """Id в словах человека не попадает в Preference: висячий Q-NN завалил
    бы GC-17 (известный — текстом пункта, неизвестный — выброшен)."""
    disc = Disc()
    view = _crit_view()
    view.entries.append(Entry("Q-03", "Порог паузы", {}))
    words = "нет, Q-03 и Q-09 тут ни при чём, просто без повторного ввода"
    agent = _agent(_nfr(NFR_HYP, Q_ITEM), _nfr("Введённое сохраняется"))
    _repair(CRIT_FINDINGS[1:], _asker(words), agent, disc, view=lambda: view)
    [(_qid, nfr)] = disc.resent
    entry = nfr["entries"][0]
    assert not re.search(r"Q-\d", entry["Preference"])
    assert "«Порог паузы»" in entry["Preference"]
    item = sx.entry_as_item(entry)
    assert sx.form_errors([item], view) == []


@pytest.mark.parametrize("yes", ["Да.", "да, так", "  ок!  "])
def test_criteria_confirmation_normalised(yes: str) -> None:
    disc = Disc()
    _crit(CRIT_FINDINGS[:1], _asker(yes), _agent(_fr(FR_HYP)), disc)
    [(_qid, funcs)] = disc.resent
    assert funcs["entries"] == [{**FR1, "Acceptance": FR_HYP}]


def test_criteria_bare_no_asks_for_words_once() -> None:
    disc = Disc()
    ask = _asker("Нет.", "без ручных шагов")
    agent = _agent(_fr(FR_HYP), _fr("Без ручных действий"))
    _crit(CRIT_FINDINGS[:1], ask, agent, disc)
    assert ask.asked[1] == gr.WORDS_ASK
    [(_qid, funcs)] = disc.resent
    entry = funcs["entries"][0]
    assert entry["Preference"] == "без ручных шагов"
    assert entry["Acceptance"] == "Без ручных действий"


def test_criteria_bare_no_twice_not_fixed(capsys) -> None:
    disc = Disc()
    _crit(CRIT_FINDINGS[:1], _asker("нет", "нет"), _agent(_fr(FR_HYP)), disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_criteria_unreferenced_q_rejected(capsys) -> None:
    disc = Disc()
    stray = _nfr("Переход без повторного ввода", Q_ITEM)
    _crit(CRIT_FINDINGS[1:], _asker(), _agent(stray, stray), disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


def test_criteria_malformed_retried_once() -> None:
    disc = Disc()
    agent = _agent("не JSON", _fr(FR_HYP))
    _crit(CRIT_FINDINGS[:1], _asker("да"), agent, disc)
    assert "отклонён" in agent.prompts[1]
    [(_qid, funcs)] = disc.resent
    assert funcs["entries"] == [{**FR1, "Acceptance": FR_HYP}]
