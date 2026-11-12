"""Раскладка ответа интервью (lite/structure.py): агент + проверка скриптом."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
import yaml

from lite import structure as sx
from lite.interview_state import Entry, SessionView

GOALS = sx.Question("customer.goals.01", "goals", "Зачем это нужно?")
FUNCS = sx.Question("customer.functions.01", "functions", "Что система делает?")
FEAS = sx.Question("engineer.feasibility_review.01", "feasibility_review", "Риски?")


def _customer(**over: Any) -> SessionView:
    base: dict[str, Any] = {
        "frame": "customer",
        "required": {"goals": "G", "jobs": "J", "functions": "FR", "nfr": "NFR"},
        "optional": {"risks": "RK", "success_metrics": "M"},
        "entries": [Entry("G-01", "Сверка платежей без ручной работы", {})],
        "declared": ["G-01", "G-02"],
        "holders": {"G-01": "customer.goals.01"},
        "answers": {},
        "answered_keys": ["goals"],
        "readiness": "incomplete",
    }
    return SessionView(**{**base, **over})


def _engineer() -> SessionView:
    return SessionView(
        frame="engineer",
        required={"systems": "S", "feasibility_review": None},
        optional={"risks": "RK"},
        entries=[],
        declared=[],
        holders={},
        answers={},
        answered_keys=[],
        readiness="incomplete",
    )


def _agent(*answers: Any) -> Any:
    """Поддельный агент: отдаёт ответы по очереди, запоминает промпты."""
    queue = [a if isinstance(a, str) else json.dumps(a) for a in answers]
    prompts: list[str] = []

    def agent(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    agent.prompts = prompts  # type: ignore[attr-defined]
    return agent


def _entries(payload: str) -> list[dict[str, Any]]:
    return yaml.safe_load(payload).get("entries", [])


FR_FULL = {
    "prefix": "FR",
    "body": "Выгрузка операций в CSV",
    "traces": ["G-01"],
    "priority": "Must",
    "acceptance": "Файл открывается в Excel",
}


def test_goal_answer_becomes_g_entry_with_next_id() -> None:
    agent = _agent({"items": [{"prefix": "G", "body": "Сверка за полчаса"}]})
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    # G-02 уже объявлялся (заменён) — номер не переиспользуется
    assert _entries(got.payload) == [{"id": "G-03", "body": "Сверка за полчаса"}]


def test_items_of_other_prefixes_allowed() -> None:
    agent = _agent(
        {
            "items": [
                {"prefix": "G", "body": "Цель", "ref": "new-1"},
                {"prefix": "G", "body": "Ещё цель"},
                {**FR_FULL, "traces": ["new-1"]},
            ],
            "ask": None,
        }
    )
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    entries = _entries(got.payload)
    assert [e["id"] for e in entries] == ["G-03", "G-04", "FR-01"]
    assert entries[2] == {
        "id": "FR-01",
        "body": "Выгрузка операций в CSV",
        "traces": ["G-03"],
        "Priority": "Must",
        "Acceptance": "Файл открывается в Excel",
    }


def test_missing_required_prefix_asks() -> None:
    agent = _agent({"items": [FR_FULL], "ask": None})
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ask)
    assert "цели" in got.question
    assert "G-" not in got.question and "{" not in got.question


def test_fr_without_acceptance_asks_not_invents() -> None:
    item = {k: v for k, v in FR_FULL.items() if k != "acceptance"}
    got = sx.structure_answer(
        FUNCS, "ответ", _customer(), _agent({"items": [item]}), may_ask=True
    )
    assert isinstance(got, sx.Ask)
    assert "Выгрузка операций в CSV" in got.question
    got = sx.structure_answer(
        FUNCS, "ответ", _customer(), _agent({"items": [item]}), may_ask=False
    )
    assert isinstance(got, sx.Ready)
    (entry,) = _entries(got.payload)
    assert "Acceptance" not in entry and entry["Priority"] == "Must"


def test_agent_ask_becomes_ask() -> None:
    agent = _agent({"items": [{"prefix": "G", "body": "Цель"}], "ask": "Кто?"})
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert got == sx.Ask("Кто?")


def test_no_ask_left_sends_what_is_there() -> None:
    agent = _agent({"items": [], "ask": "А цель какая?"})
    got = sx.structure_answer(GOALS, "не знаю", _customer(), agent, may_ask=False)
    assert got == sx.Ready(yaml.safe_dump({"text": "не знаю"}, allow_unicode=True))


def test_validate_reports_gate_fields() -> None:
    items = [
        {"prefix": "FR", "body": "Ф", "traces": ["RK-01"]},
        {"prefix": "NFR", "body": "Быстро"},
        {"prefix": "M", "body": "Время сверки"},
    ]
    gaps = sx.validate(items, FUNCS, _customer())
    assert len(gaps) == 5  # FR: цель, важность, критерий; NFR; M
    assert sx.validate([{**FR_FULL}], FUNCS, _customer()) == []
    nfr = {"prefix": "NFR", "body": "Быстро", "target": "< 1 с"}
    assert sx.validate([nfr, FR_FULL], FUNCS, _customer()) == []


def test_broken_json_retried_once_then_failed() -> None:
    agent = _agent("не JSON вовсе", '{"items": [}')
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert got == sx.Failed("не удалось разобрать ответ — переформулируйте")
    assert len(agent.prompts) == 2
    assert "отклонён" in agent.prompts[1]


def test_broken_json_then_good_answer_succeeds() -> None:
    good = '```json\n{"items": [{"prefix": "G", "body": "Цель"}]}\n```\nГотово.'
    agent = _agent("Вот ответ без JSON", good)
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert _entries(got.payload)[0]["body"] == "Цель"


def test_traces_to_unknown_id_retries_agent() -> None:
    bad = {"items": [{**FR_FULL, "traces": ["G-09"]}]}
    agent = _agent(bad, {"items": [FR_FULL]})
    got = sx.structure_answer(FUNCS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert "G-09" in agent.prompts[1]
    assert _entries(got.payload)[0]["traces"] == ["G-01"]


@pytest.mark.parametrize("prefix", ["Q", "X", "ZZ", "S"])
def test_q_and_x_prefixes_rejected(prefix: str) -> None:
    bad = {"items": [{"prefix": prefix, "body": "Вопрос"}]}
    agent = _agent(bad, bad)
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Failed)
    assert len(agent.prompts) == 2


def test_form_errors_bad_priority_and_refs() -> None:
    view = _customer()
    with pytest.raises(sx.FormError, match="priority"):
        sx.parse_items(json.dumps({"items": [{**FR_FULL, "priority": "Срочно"}]}))
    dup = [
        {"prefix": "G", "body": "a", "ref": "n"},
        {"prefix": "G", "body": "b", "ref": "n"},
    ]
    assert sx.form_errors(dup, view)
    assert sx.form_errors([{"prefix": "G", "body": "a", "ref": "G-01"}], view)


def test_parse_items_schema() -> None:
    items, ask = sx.parse_items(
        'пролог {"items": [{"prefix": "FR", "body": " Б ", "priority": "must",'
        ' "acceptance": "", "id": "FR-07", "extra": 1}], "ask": null}'
    )
    assert ask is None
    assert items == [
        {"prefix": "FR", "body": "Б", "traces": [], "priority": "Must", "id": "FR-07"}
    ]
    for raw in ['{"items": "x"}', '{"items": [{"prefix": "G"}]}', "[]", "",
                '{"items": [{"prefix": "G", "body": "b", "traces": "G-01"}]}']:  # fmt: skip
        with pytest.raises(sx.FormError):
            sx.parse_items(raw)


def test_agent_supplied_id_ignored_in_payload() -> None:
    items = [{"prefix": "G", "body": "Цель", "traces": [], "id": "G-01"}]
    assert _entries(sx.to_payload("t", items, _customer()))[0]["id"] == "G-03"


def test_feasibility_review_text_only() -> None:
    agent = _agent({"items": [], "ask": None})
    got = sx.structure_answer(FEAS, "Рисков нет", _engineer(), agent, may_ask=True)
    assert got == sx.Ready(yaml.safe_dump({"text": "Рисков нет"}, allow_unicode=True))


def test_payload_text_is_human_answer_verbatim() -> None:
    text = "Строка 1: «кавычки»\n\n  отступ — и # не комментарий\nУточнение: да"
    agent = _agent({"items": [{"prefix": "G", "body": "Цель"}]})
    got = sx.structure_answer(GOALS, text, _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert yaml.safe_load(got.payload)["text"] == text


def test_prompt_forbids_inventing_and_lists_rules() -> None:
    prompt = sx.build_prompt(GOALS, "мой ответ", _customer())
    for needed in (
        "Зачем это нужно?",
        "мой ответ",
        "goals",
        "G-01",
        "Сверка платежей без ручной работы",
        "не выдумывай",
        "Must, Should, Could, Won't",
        "acceptance",
        "target",
        '"ask"',
        "ref",
        "Q",
        "FR",
        "NFR",
        "RK",
    ):
        assert needed in prompt, needed


def test_claude_agent_argv_has_safe_mode_and_model(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, '{"items": []}', "")

    monkeypatch.setattr(sx.subprocess, "run", run)
    assert sx.claude_agent("sonnet")("промпт") == '{"items": []}'
    argv, kw = calls[0]
    assert argv == [
        "claude", "-p", "--safe-mode", "--tools", "",
        "--model", "sonnet", "--output-format", "text",
    ]  # fmt: skip
    assert kw["input"] == "промпт" and kw["timeout"] == 300


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.TimeoutExpired(["claude"], 300),
        OSError("нет claude"),
        subprocess.CompletedProcess(["claude"], 1, '{"items": []}', "сбой"),
    ],
)
def test_claude_agent_failure_is_broken_answer(monkeypatch, outcome) -> None:
    def run(argv, **kw):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sx.subprocess, "run", run)
    assert sx.claude_agent("m")("p") == ""
