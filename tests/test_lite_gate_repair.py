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
from lite import brief_v13 as bv
from lite import gate_repair as gr
from lite import gate_repair_v13 as v13
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


@pytest.mark.parametrize(
    "yes",
    ["согласен", "Подходит.", "ага", "конечно!", "ок, давай", "да, всё верно",
     "Верно", "yes", "OK", "согласна, так и запишите", "угу"],
)  # fmt: skip
def test_criteria_ordinary_agreement_records_hypothesis(yes: str) -> None:
    """Обычное согласие — запись гипотезы, а не слова в `Preference` и
    повторное предложение агента по одному слову (финальное ревью v1.2)."""
    disc = Disc()
    ask = _asker(yes)
    agent = _agent(_fr(FR_HYP))
    _crit(CRIT_FINDINGS[:1], ask, agent, disc)
    assert len(agent.prompts) == 1 and len(ask.asked) == 1
    [(_qid, funcs)] = disc.resent
    assert funcs["entries"] == [{**FR1, "Acceptance": FR_HYP}]


@pytest.mark.parametrize("no", ["?!", "...", "", "не так", "Нет, не то.", "не то"])
def test_criteria_dismissal_asks_for_words_once(no: str) -> None:
    """Ответ без букв или голое отрицание — как «нет»: один раз попросить
    своих слов, в `Preference` сам ответ не пишется."""
    disc = Disc()
    ask = _asker(no, "без ручных шагов")
    agent = _agent(_fr(FR_HYP), _fr("Без ручных действий"))
    _crit(CRIT_FINDINGS[:1], ask, agent, disc)
    assert ask.asked[1] == gr.WORDS_ASK
    [(_qid, funcs)] = disc.resent
    assert funcs["entries"][0]["Preference"] == "без ручных шагов"


def test_criteria_dismissal_twice_not_written(capsys) -> None:
    disc = Disc()
    _crit(CRIT_FINDINGS[:1], _asker("?!", "не то"), _agent(_fr(FR_HYP)), disc)
    assert disc.resent == []
    assert gr.GATE_LEFT.format(n=1) in capsys.readouterr().out


@pytest.mark.parametrize("yes", ["согласен", "Да, всё верно."])
def test_traces_and_verdicts_accept_ordinary_agreement(yes: str) -> None:
    disc = Disc()
    _repair(TRACE_FINDINGS, _asker(yes), _agent(TRACES), disc)
    assert len(disc.resent) == 2
    disc = Disc()
    _repair(VERDICT_FINDINGS[:1], _asker(yes), _agent(), disc)
    [(_qid, payload)] = disc.resent
    assert payload["entries"][-1]["status"] == "resolved"


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


def test_appended_merges_translations_by_words_and_constraint() -> None:
    held = {
        "text": "a",
        "translations": [
            {"words": "w1", "category": "делать", "constraint": ["FR-01"]},
            {"words": "w2", "category": "делать", "constraint": ["FR-02"]},
        ],
    }
    new = yaml.safe_dump({
        "text": "b",
        "translations": [
            {"words": "w2", "category": "стоить", "constraint": ["FR-02"]},
            {"words": "w3", "category": "не делать", "constraint": ["OUT-01"]},
        ],
    }, allow_unicode=True)  # fmt: skip
    out = yaml.safe_load(gr.appended(held, new))
    assert [(r["words"], r["category"]) for r in out["translations"]] == [
        ("w1", "делать"),
        ("w2", "стоить"),
        ("w3", "не делать"),
    ]
    plain = yaml.safe_load(gr.appended({"text": "a"}, yaml.safe_dump({"text": "b"})))
    assert "translations" not in plain


# --- GC-18…GC-21: контракт брифа v1.3, фрейм заказчика (#813) ---

V13_NFR_FINDING = (
    "GC-20 error [NFR-01] нет **Category**: без неё perf/cost-цель не проверить"
)
_OWNER = "GC-21 error [owns: {}] владелец не назван: нет P или открытого Q с этим owns"
PERS_QID = "customer.personas.01"
OUT_QID = "customer.out_of_scope.01"


def _v13_answers() -> dict[str, dict[str, Any]]:
    return {
        "customer.goals.01": {
            "text": "цель",
            "entries": [{"id": "G-01", "body": "Цель"}],
        },
        PERS_QID: {
            "text": "аналитики",
            "entries": [{"id": "P-01", "body": "Аналитик"}],
        },
        FUNCS_QID: {
            "text": FUNCS_TEXT,
            "entries": [
                {
                    **FR1,
                    "Preference": "запуск одной командой",
                    "Acceptance": "одна команда",
                }
            ],  # fmt: skip
        },
        NFR_QID: {
            "text": "чтобы не ждать",
            "entries": [
                {
                    "id": "NFR-01",
                    "body": "Ответ быстро, за секунды",
                    "Preference": "чтобы не ждать",
                }
            ],  # fmt: skip
        },
        OUT_QID: {
            "text": "мобилка не нужна",
            "entries": [
                {
                    "id": "OUT-01",
                    "body": "Без мобильного клиента",
                    "Preference": "мобилка не нужна",
                }
            ],  # fmt: skip
        },
    }


@dataclass
class Live:
    """Фейк discovery с состоянием: пересылка меняет ответ, `load` — снимок."""

    answers: dict[str, dict[str, Any]] = field(default_factory=_v13_answers)
    frame: str = "customer"
    resent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def resend(self, qid: str, payload: str) -> iv.DiscoveryReply:
        doc = yaml.safe_load(payload)
        self.resent.append((qid, doc))
        self.answers.pop(qid, None)
        self.answers[qid] = doc
        return iv.DiscoveryReply(10, {"operation": {"status": "ok"}}, "")

    def load(self, _session: str) -> SessionView:
        holders: dict[str, str] = {}
        records: dict[str, dict[str, Any]] = {}
        for qid, answer in self.answers.items():
            for e in answer.get("entries") or []:
                holders[e["id"]] = qid
                records[e["id"]] = e
        entries = [
            Entry(
                eid, r["body"], {k: v for k, v in r.items() if k not in ("id", "body")}
            )
            for eid, r in records.items()
        ]
        return SessionView(
            frame=self.frame,
            required={
                "goals": "G",
                "personas": "P",
                "functions": "FR",
                "nfr": "NFR",
                "out_of_scope": "OUT",
            },  # fmt: skip
            optional={},
            entries=entries,
            declared=list(records),
            holders=holders,
            answers={q: dict(a) for q, a in self.answers.items()},
            answered_keys=["goals", "personas", "functions", "nfr", "out_of_scope"],
            readiness="ready",
            asked={q: q.split(".")[1] for q in self.answers},
        )


def _v13(findings: list[str], ask: Any, agent: Any, live: Live) -> None:
    gr.repair(findings, SESSION, ask=ask, agent=agent, resend=live.resend,
              load=live.load, upstream=lambda: UPSTREAM)  # fmt: skip


def _entry_of(doc: dict[str, Any], eid: str) -> dict[str, Any]:
    return next(e for e in doc["entries"] if e["id"] == eid)


def test_category_confirmed_by_yes() -> None:
    live = Live()
    ask = _asker("да")
    _v13([V13_NFR_FINDING], ask, _agent(), live)
    assert ask.asked == [
        gr.CATEGORY_ASK.format(body="Ответ быстро, за секунды", hypothesis="скорость")
    ]
    [(qid, doc)] = live.resent
    assert qid == NFR_QID and _entry_of(doc, "NFR-01")["category"] == ["perf"]


def test_category_named_in_words() -> None:
    live = Live()
    _v13([V13_NFR_FINDING], _asker("это про стоимость"), _agent(), live)
    [(_qid, doc)] = live.resent
    assert _entry_of(doc, "NFR-01")["category"] == ["cost"]


def test_category_without_hypothesis_asks_without_yes() -> None:
    live = Live()
    live.answers[NFR_QID]["entries"][0]["body"] = "Удобно"
    ask = _asker("скорость")
    _v13([V13_NFR_FINDING], ask, _agent(), live)
    assert ask.asked == [gr.CATEGORY_ASK_OPEN.format(body="Удобно")]
    assert "«да»" not in ask.asked[0]
    assert _entry_of(live.resent[0][1], "NFR-01")["category"] == ["perf"]


def test_numeric_unknown_creates_nfr_and_q() -> None:
    live = Live()
    ask = _asker("неизвестно")
    finding = "GC-20 error [Category: perf] нет ни одной NFR категории perf"
    _v13([finding], ask, _agent(), live)
    assert ask.asked == [gr.NUMERIC_ASK.format(question=bv.NUMERIC_QUESTIONS["perf"])]
    [(qid, doc)] = live.resent
    assert qid == NFR_QID
    nfr, q = _entry_of(doc, "NFR-02"), _entry_of(doc, "Q-01")
    assert nfr["category"] == ["perf"] and nfr["Target"] == "значение из Q-01"
    assert q["owner_role"] == "analyst"
    assert _entry_of(doc, "NFR-01")["body"] == "Ответ быстро, за секунды"


def test_numeric_answer_without_number_not_categorised(capsys) -> None:
    """Ревью #819: `perf` не клеится к `NFR` без числовой цели — GC-20
    иначе залип бы красным, а вопрос `nfr:perf` больше не ставился."""
    live = Live()
    item = {"prefix": "NFR", "body": "Работает без отказов днём",
            "acceptance": "работает весь день"}  # fmt: skip
    finding = "GC-20 error [Category: perf] нет ни одной NFR категории perf"
    _v13([finding], _asker("чтобы не ждать"), _agent({"items": [item]}), live)
    assert live.resent == []
    assert "нет требования с числом" in capsys.readouterr().out


def test_with_category_checks_target() -> None:
    def nfr(n: int, target: str, *cat: str) -> dict:
        return {"id": f"NFR-0{n}", "Target": target, "category": list(cat)}

    doc = {"entries": [nfr(1, "весь день"), nfr(2, "не более 2 с")]}
    assert v13.with_category(doc, "perf")
    assert doc["entries"][0]["category"] == []
    assert doc["entries"][1]["category"] == ["perf"]
    assert v13.with_category({"entries": [nfr(1, "значение из Q-03")]}, "cost")
    assert not v13.with_category({"entries": [nfr(1, "быстро", "perf")]}, "perf")
    assert not v13.with_category({"entries": [nfr(1, "из NFR-02")]}, "perf")


def test_numeric_answer_structured_into_nfr() -> None:
    live = Live()
    said = "не больше ста тысяч в месяц"
    item = {"prefix": "NFR", "body": "Бюджет в месяц", "category": ["cost"],
            "preference": said, "target": "не больше 100000 ₽ в месяц"}  # fmt: skip
    agent = _agent({"items": [item]})
    finding = "GC-20 error [Category: cost] нет ни одной NFR категории cost"
    _v13([finding], _asker(said), agent, live)
    assert bv.NUMERIC_QUESTIONS["cost"] in agent.prompts[0]
    [(qid, doc)] = live.resent
    assert qid == NFR_QID
    assert _entry_of(doc, "NFR-02")["category"] == ["cost"]
    assert said in doc["text"]


def test_owner_named_and_unknown() -> None:
    live = Live()
    ask = _asker("Иван, руководитель отдела", "никто")
    _v13([_OWNER.format("process"), _OWNER.format("data")], ask, _agent(), live)
    assert ask.asked == [
        gr.OWNER_ASK.format(label="процесс"),
        gr.OWNER_ASK.format(label="данные"),
    ]
    (q1, first), (q2, second) = live.resent
    assert q1 == q2 == PERS_QID
    assert _entry_of(first, "P-02")["owns"] == ["process"]
    q = _entry_of(second, "Q-01")
    assert q["owns"] == ["data"] and q["owner_role"] == "analyst"
    assert _entry_of(second, "P-02")["owns"] == ["process"]  # не потеряна


def test_owner_already_named_not_asked() -> None:
    live = Live()
    live.answers[PERS_QID]["entries"][0]["owns"] = ["process"]
    ask = _asker()
    _v13([_OWNER.format("process")], ask, _agent(), live)
    assert ask.asked == [] and live.resent == []


def test_table_rows_derived_without_question(capsys) -> None:
    live = Live()
    ask = _asker(None, None, None)
    empty = "GC-18 error [Translation table] секции нет или в ней нет строк"
    _v13([empty], ask, _agent(), live)
    assert ask.asked[:2] == [
        gr.TABLE_ASK.format(category="стоить"),
        gr.TABLE_ASK.format(category="доказывать"),
    ]
    rows = {q: d["translations"] for q, d in live.resent}
    assert rows[FUNCS_QID] == [{"words": "запуск одной командой",
                                "category": "делать", "constraint": ["FR-01"],
                                "assumption": "одна команда"}]  # fmt: skip
    assert rows[OUT_QID][0]["category"] == "не делать"
    assert gr.GATE_LEFT.format(n=3) in capsys.readouterr().out


def _row(words: str, category: str, eid: str, **extra: Any) -> dict[str, Any]:
    return {"words": words, "category": category, "constraint": [eid],
            "assumption": "", **extra}  # fmt: skip


def test_table_missing_category_asks() -> None:
    live = Live()
    live.answers[FUNCS_QID]["translations"] = [
        _row("a", "делать", "FR-01"),
        _row("b", "доказывать", "FR-01"),
    ]
    live.answers[OUT_QID]["translations"] = [_row("c", "не делать", "OUT-01")]
    said = "дешевле нынешнего подрядчика"
    item = {"prefix": "NFR", "body": "Дешевле подрядчика", "ref": "new-1",
            "category": ["cost"], "preference": said,
            "target": "не дороже значения из q-1"}  # fmt: skip
    q_item = {"prefix": "Q", "body": "Сколько стоит подрядчик?", "ref": "q-1",
              "owner_role": "analyst"}  # fmt: skip
    rows = [{"words": said, "category": "стоить", "constraint": ["new-1"],
             "assumption": None}]  # fmt: skip
    agent = _agent({"items": [item, q_item], "translations": rows})
    finding = "GC-19 error [Translation table] нет ни одной строки категории «стоить»"
    ask = _asker(said, None)  # затем вопрос о допущении выведенной строки
    _v13([finding], ask, agent, live)
    assert ask.asked == [gr.TABLE_ASK.format(category="стоить"), gr.CONFIRM_ASK]
    # строки FR-01/OUT-01 из Preference — без вопроса, держателям пунктов
    assert [q for q, _d in live.resent] == [FUNCS_QID, OUT_QID, NFR_QID]
    funcs, doc = live.resent[0][1], live.resent[2][1]
    assert funcs["translations"][-1]["words"] == "запуск одной командой"
    [row] = doc["translations"]
    assert row["category"] == "стоить" and row["constraint"] == ["NFR-02"]


def test_confirmation_one_question_for_all_rows(capsys) -> None:
    live = Live()
    live.answers[FUNCS_QID]["translations"] = [
        _row("a", "делать", "FR-01", assumption="одна команда"),
        _row("b", "доказывать", "FR-01"),
    ]
    live.answers[OUT_QID]["translations"] = [
        _row("c", "не делать", "OUT-01", assumption="без iOS и Android"),
    ]
    finding = "GC-18 error [Translation table#1] у допущения нет подтверждающего"
    ask = _asker("владелец продукта, 01.12.2099")
    _v13([finding], ask, _agent(), live)
    assert ask.asked == [gr.CONFIRM_ASK]
    out = capsys.readouterr().out
    assert "одна команда" in out and "без iOS и Android" in out
    sent = {q: d["translations"] for q, d in live.resent}
    assert set(sent) == {FUNCS_QID, OUT_QID}
    a, b = sent[FUNCS_QID]
    assert (a["confirmer"], a["due"]) == ("владелец продукта", "2099-12-01")
    assert "confirmer" not in b  # без допущения — без подтверждающего
    assert sent[OUT_QID][0]["due"] == "2099-12-01"


def test_confirmation_bad_date_reasks_then_skip(capsys) -> None:
    live = Live()
    live.answers[FUNCS_QID]["translations"] = [
        _row("a", "делать", "FR-01", assumption="одна команда"),
    ]
    finding = "GC-18 error [Translation table#1] у допущения нет срока YYYY-MM-DD"
    ask = _asker("до конца месяца", None)
    _v13([finding], ask, _agent(), live)
    assert ask.asked == [gr.CONFIRM_ASK, gr.CONFIRM_ASK]
    out = capsys.readouterr().out
    assert bv.NEED_DATE in out
    assert live.resent == [] and gr.GATE_LEFT.format(n=1) in out


def test_engineer_frame_no_v13_steps(capsys) -> None:
    live = Live(frame="engineer")
    ask = _asker()
    _v13([V13_NFR_FINDING, _OWNER.format("data")], ask, _agent(), live)
    assert ask.asked == [] and live.resent == []
    assert capsys.readouterr().out == ""


def test_skip_counts_in_gate_left(capsys) -> None:
    live = Live()
    _v13([V13_NFR_FINDING, _OWNER.format("process")], _asker(None, None),
         _agent(), live)  # fmt: skip
    assert live.resent == []
    assert gr.GATE_LEFT.format(n=2) in capsys.readouterr().out


# слово подсказки вопроса о категории → категория (ревью задачи 3, #813)
CATEGORY_HINT_WORDS = {
    "скорость": "perf",
    "стоимость": "cost",
    "безопасность": "security",
    "доступность": "availability",
    "регламент": "compliance",
    "вред людям": "safety",
    "качество": "quality",
}


@pytest.mark.parametrize(("word", "category"), CATEGORY_HINT_WORDS.items())
def test_category_hint_words_recognised(word: str, category: str) -> None:
    assert word in gr.CATEGORY_ASK and word in gr.CATEGORY_ASK_OPEN
    assert bv.parse_categories(word, ["perf"]) == [category]


def test_category_hint_lists_only_known_words() -> None:
    hint = gr.CATEGORY_ASK.split("иначе: ")[1].split("; «пропустить»")[0]
    assert "…" not in gr.CATEGORY_ASK and "…" not in gr.CATEGORY_ASK_OPEN
    assert hint.split(", ") == list(CATEGORY_HINT_WORDS)


def test_availability_not_security() -> None:
    assert bv.parse_categories("доступность", []) == ["availability"]
    assert bv.category_hypothesis("Доступность 99,9 % в месяц") == ["availability"]


def test_category_labels_round_trip() -> None:
    from lite import gate_repair_v13 as v13

    for code, label in v13.CATEGORY_LABELS.items():
        assert bv.parse_categories(label, []) == [code], label


@pytest.mark.parametrize("said", ["Не знаю.", "пропустить."])
def test_owner_skip_with_period_writes_nothing(said: str) -> None:
    from lite.interview_chat import _ask_or_skip

    live = Live()
    asked: list[str] = []
    lines = iter([said, ""])  # ответ, затем пустая строка — конец ввода

    def ask(header: str) -> str | None:
        asked.append(header)
        return _ask_or_skip(header, lambda _prompt: next(lines))

    _v13([_OWNER.format("process")], ask, _agent(), live)
    assert asked and live.resent == []


def test_owner_negation_phrase_writes_open_q() -> None:
    live = Live()
    _v13([_OWNER.format("data")], _asker("нет владельца"), _agent(), live)
    [(_qid, doc)] = live.resent
    q = _entry_of(doc, "Q-01")
    assert q["owns"] == ["data"] and "нет владельца" not in q["body"]
