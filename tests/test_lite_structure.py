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
    # тема функций ещё не покрыта (FR нет), а ответ дал только цель
    agent = _agent({"items": [{"prefix": "G", "body": "Цель"}], "ask": None})
    got = sx.structure_answer(FUNCS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ask)
    assert "функции" in got.question
    assert "G-" not in got.question and "{" not in got.question


def test_fr_without_acceptance_is_form_error_for_agent() -> None:
    """v1.2: критерий — перевод агента: его нет — повтор агента, не вопрос
    человеку."""
    item = {k: v for k, v in FR_FULL.items() if k != "acceptance"}
    agent = _agent({"items": [item]}, {"items": [FR_FULL]})
    got = sx.structure_answer(FUNCS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert len(agent.prompts) == 2
    assert "гипотез" in agent.prompts[1] and "Q" in agent.prompts[1]
    assert _entries(got.payload)[0]["Acceptance"] == "Файл открывается в Excel"


@pytest.mark.parametrize("may_ask", [True, False])
def test_missing_criterion_twice_fails_without_human_question(may_ask: bool) -> None:
    item = {"prefix": "NFR", "body": "Быстро", "preference": "быстро"}
    agent = _agent({"items": [item]}, {"items": [item]})
    got = sx.structure_answer(FUNCS, "ответ", _customer(), agent, may_ask=may_ask)
    assert isinstance(got, sx.Failed)
    assert len(agent.prompts) == 2


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


@pytest.mark.parametrize(
    ("prefix", "extra"),
    [
        ("Q", {}),
        ("Q", {"owner_role": "product"}),
        ("X", {}),
        ("ZZ", {}),
        ("S", {}),
    ],
)
def test_q_without_research_role_and_x_prefixes_rejected(
    prefix: str, extra: dict[str, Any]
) -> None:
    bad = {"items": [{"prefix": prefix, "body": "Вопрос", **extra}]}
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


def test_agent_supplied_unknown_id_is_form_error() -> None:
    """I4: id, которого нет среди действующих пунктов, — повтор агента."""
    items = [{"prefix": "G", "body": "Цель", "traces": [], "id": "G-07"}]
    assert any("G-07" in e for e in sx.form_errors(items, _customer()))


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
    ("outcome", "cause"),
    [
        (subprocess.TimeoutExpired(["claude"], 300), "300"),
        (OSError("нет claude"), "нет claude"),
        (
            subprocess.CompletedProcess(
                ["claude"], 1, '{"items": []}', "Not logged in\nподробности"
            ),
            "Not logged in",
        ),
        (subprocess.CompletedProcess(["claude"], 2, "", ""), "код 2"),
    ],
)
def test_claude_agent_failure_not_retried_and_named(
    monkeypatch, outcome, cause: str
) -> None:
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append(argv)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(sx.subprocess, "run", run)
    got = sx.structure_answer(
        GOALS, "ответ", _customer(), sx.claude_agent("m"), may_ask=True
    )
    assert len(calls) == 1
    assert isinstance(got, sx.Failed)
    assert got.message.startswith("агент недоступен: ")
    assert cause in got.message and "\n" not in got.message
    assert got.message != sx.FAILED_TEXT


def test_agent_unavailable_on_retry_not_retried_again() -> None:
    calls: list[str] = []

    def agent(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "не JSON"
        raise sx.AgentUnavailable("код 1")

    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert got == sx.Failed("агент недоступен: код 1")
    assert len(calls) == 2


# --- финальное ревью: I2 (тема уже покрыта) ---

GOALS_2 = sx.Question("customer.goals.02", "goals", "Почему сейчас?")


def test_covered_topic_follow_up_without_new_item_is_ready() -> None:
    """Второй вопрос покрытой темы: нового G нет — это не повод переспрашивать."""
    agent = _agent({"items": [], "ask": None})
    got = sx.structure_answer(
        GOALS_2, "квартал горит", _customer(), agent, may_ask=True
    )
    assert isinstance(got, sx.Ready)
    assert len(agent.prompts) == 1


def test_uncovered_topic_still_requires_prefix() -> None:
    view = _customer(entries=[], answered_keys=[])
    agent = _agent({"items": [], "ask": None})
    got = sx.structure_answer(GOALS, "ответ", view, agent, may_ask=True)
    assert isinstance(got, sx.Ask) and "цели" in got.question


# --- финальное ревью: I4 (ответ дополняет существующий пункт) ---

PRIO = sx.Question("customer.functions.02", "functions", "Что подождёт?")
FR_OLD = {
    "id": "FR-01",
    "body": "Выгрузка операций в CSV",
    "traces": ["G-01"],
    "Priority": "Must",
    "Note": "поле, о котором dfl не знает",
}


def _with_fr(**fields: Any) -> SessionView:
    raw = {**FR_OLD, **fields}
    entry = Entry("FR-01", raw["body"],
                  {k: v for k, v in raw.items() if k not in ("id", "body")})  # fmt: skip
    return _customer(
        entries=[Entry("G-01", "Сверка платежей без ручной работы", {}), entry],
        declared=["G-01", "G-02", "FR-01"],
        holders={"G-01": "customer.goals.01", "FR-01": "customer.functions.01"},
        answers={"customer.functions.01": {"text": "ф", "entries": [raw]}},
        answered_keys=["goals", "functions"],
    )


def test_update_by_existing_id_merges_and_keeps_id() -> None:
    upd = {"id": "FR-01", "prefix": "FR", "body": "Выгрузка операций в CSV",
           "acceptance": "Файл открывается в Excel"}  # fmt: skip
    agent = _agent({"items": [upd], "ask": None})
    got = sx.structure_answer(PRIO, "проверим в Excel", _with_fr(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert _entries(got.payload) == [
        {**FR_OLD, "Acceptance": "Файл открывается в Excel"}
    ]


def test_update_and_new_item_new_gets_next_id() -> None:
    upd = {"id": "FR-01", "prefix": "FR", "body": "Выгрузка операций в CSV",
           "acceptance": "Файл открывается в Excel"}  # fmt: skip
    new = {**FR_FULL, "body": "Письмо с отчётом"}
    agent = _agent({"items": [upd, new], "ask": None})
    got = sx.structure_answer(PRIO, "ответ", _with_fr(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert [e["id"] for e in _entries(got.payload)] == ["FR-01", "FR-02"]


def test_update_validated_as_merged_result() -> None:
    upd = {"id": "FR-01", "prefix": "FR", "body": "Выгрузка операций в CSV",
           "priority": "Should"}  # fmt: skip
    # traces нет ни в прежнем пункте, ни в обновлении — спросить человека
    no_traces = _with_fr(traces=[], Acceptance="Файл открывается в Excel")
    got = sx.structure_answer(
        PRIO, "ответ", no_traces, _agent({"items": [upd]}), may_ask=True
    )
    assert isinstance(got, sx.Ask) and "цели или задаче" in got.question
    # прежний пункт полный: обновление одного поля проходит без уточнений
    full = _with_fr(Acceptance="Файл открывается в Excel")
    got = sx.structure_answer(PRIO, "ответ", full, _agent({"items": [upd]}),
                              may_ask=True)  # fmt: skip
    assert isinstance(got, sx.Ready)
    (entry,) = _entries(got.payload)
    assert entry["Priority"] == "Should" and entry["traces"] == ["G-01"]


def test_update_unknown_id_retries_agent() -> None:
    bad = {"id": "FR-09", "prefix": "FR", "body": "Ф", "acceptance": "а"}
    good = {**bad, "id": "FR-01"}
    agent = _agent({"items": [bad]}, {"items": [good]})
    got = sx.structure_answer(PRIO, "ответ", _with_fr(), agent, may_ask=False)
    assert isinstance(got, sx.Ready)
    assert "FR-09" in agent.prompts[1]
    assert _entries(got.payload)[0]["id"] == "FR-01"


def test_update_id_prefix_mismatch_is_form_error() -> None:
    items = [{"id": "FR-01", "prefix": "NFR", "body": "Б", "traces": []}]
    assert sx.form_errors(items, _with_fr())


def test_prompt_allows_updating_existing_by_id() -> None:
    prompt = sx.build_prompt(PRIO, "ответ", _with_fr())
    assert '"id"' in prompt and "Priority: Must" in prompt


SYSQ = sx.Question("engineer.interfaces.01", "interfaces", "Какие интерфейсы?")


def test_validate_if_and_ap_need_system_traces() -> None:
    """GC-13: IF → S, AP → S/CON (devtools#755)."""
    items = [
        {"prefix": "IF", "body": "CLI", "traces": []},
        {"prefix": "AP", "body": "Чистое ядро", "traces": ["RK-01"]},
    ]
    gaps = sx.validate(items, SYSQ, _engineer())
    assert gaps == [
        "К какой системе относится «CLI»?",
        "Какую систему или какое ограничение затрагивает «Чистое ядро»?",
    ]
    ok = [
        {"prefix": "IF", "body": "CLI", "traces": ["S-01"]},
        {"prefix": "AP", "body": "Ядро", "traces": ["CON-01"]},
    ]
    assert sx.validate(ok, SYSQ, _engineer()) == []


def test_prompt_names_if_and_ap_trace_rules() -> None:
    prompt = sx.build_prompt(SYSQ, "CLI", _engineer())
    assert "IF (интерфейс): traces — список id систем S" in prompt
    assert "AP (архитектурное предпочтение)" in prompt


# --- контракт v1.2: предпочтение дословно, гипотеза агента, числа в Q-NN ---

NFRQ = sx.Question("customer.nfr.01", "nfr", "Каким должно быть удобство?")
SEAMLESS = "чтобы было бесшовно"


def test_preference_verbatim_with_agent_hypothesis_no_number_question() -> None:
    """(a) Ответ без чисел: слова человека в Preference, гипотеза в Acceptance."""
    item = {
        "prefix": "NFR",
        "body": "Бесшовный переход между шагами",
        "preference": SEAMLESS,
        "acceptance": "Переход между шагами без повторного входа и ввода данных",
    }
    agent = _agent({"items": [item], "ask": None})
    got = sx.structure_answer(NFRQ, SEAMLESS, _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    (entry,) = _entries(got.payload)
    assert entry["Preference"] == SEAMLESS
    assert entry["Acceptance"].startswith("Переход между шагами")
    assert len(agent.prompts) == 1


def test_criterion_gap_is_not_a_number_question() -> None:
    """(d) Дыра критерия (для dfl correct) не просит у человека значения."""
    item = {"prefix": "NFR", "body": "Быстро", "preference": "быстро"}
    fr = {k: v for k, v in FR_FULL.items() if k != "acceptance"}
    gaps = sx.validate([item, fr], NFRQ, _customer())
    assert len(gaps) == 2
    for gap in gaps:
        assert "?" not in gap and "значени" not in gap and "агент" in gap


def test_update_without_criterion_anywhere_reasks_agent() -> None:
    upd = {"id": "FR-01", "prefix": "FR", "body": "Выгрузка операций в CSV",
           "priority": "Should"}  # fmt: skip
    fixed = {**upd, "acceptance": "Файл открывается в Excel"}
    agent = _agent({"items": [upd]}, {"items": [fixed]})
    got = sx.structure_answer(PRIO, "ответ", _with_fr(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert len(agent.prompts) == 2 and "FR" in agent.prompts[1]


def test_number_goes_to_research_q_in_same_answer() -> None:
    """(b) Число — открытый Q-NN исследования в том же ответе, ссылка на него."""
    items = [
        {"prefix": "Q", "body": "Какое время отклика приемлемо?", "ref": "q-1",
         "owner_role": "analyst"},
        {"prefix": "NFR", "body": "Быстрый отклик", "preference": "быстро",
         "target": "Отклик не хуже значения из q-1"},
    ]  # fmt: skip
    agent = _agent({"items": items, "ask": None})
    got = sx.structure_answer(NFRQ, "быстро", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    q, nfr = _entries(got.payload)
    assert q == {
        "id": "Q-01",
        "body": "Какое время отклика приемлемо?",
        "owner_role": "analyst",
        "blocking": False,
    }
    assert nfr["Target"] == "Отклик не хуже значения из Q-01"
    assert nfr["Preference"] == "быстро"


def test_q_entry_rendered_fields_pass_gc09_and_gc17_shape() -> None:
    """Запись Q в YAML: `blocking` — булево false, `owner_role` — роль."""
    items = [
        {"prefix": "Q", "body": "Порог?", "ref": "q-1", "owner_role": "engineer"},
        {"prefix": "M", "body": "Время сверки", "traces": ["G-01"],
         "target": "не хуже значения из q-1"},
    ]  # fmt: skip
    got = sx.structure_answer(GOALS, "ответ", _customer(), _agent({"items": items}),
                              may_ask=True)  # fmt: skip
    assert isinstance(got, sx.Ready)
    assert "blocking: false" in got.payload
    assert "owner_role: engineer" in got.payload


def test_undefined_q_reference_rejected_and_agent_reasked() -> None:
    """(c) Ссылка на Q-NN, которого нет ни в ответе, ни в сессии (GC-17)."""
    bad = {"prefix": "NFR", "body": "Быстро", "target": "не хуже значения из Q-05"}
    good = [
        {"prefix": "Q", "body": "Порог?", "ref": "q-1", "owner_role": "analyst"},
        {**bad, "target": "не хуже значения из q-1"},
    ]
    agent = _agent({"items": [bad]}, {"items": good})
    got = sx.structure_answer(NFRQ, "быстро", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Ready)
    assert len(agent.prompts) == 2
    assert "Q-05" in agent.prompts[1] and "отклонён" in agent.prompts[1]
    assert _entries(got.payload)[1]["Target"] == "не хуже значения из Q-01"


def test_undefined_q_reference_twice_fails() -> None:
    bad = {"items": [{"prefix": "M", "body": "Метрика", "traces": ["G-01"],
                      "target": "из Q-07"}]}  # fmt: skip
    agent = _agent(bad, bad)
    got = sx.structure_answer(GOALS, "ответ", _customer(), agent, may_ask=True)
    assert isinstance(got, sx.Failed)
    assert len(agent.prompts) == 2


def test_reference_to_session_q_accepted() -> None:
    view = _customer(
        entries=[
            Entry("G-01", "Сверка платежей без ручной работы", {}),
            Entry("Q-01", "Порог?", {"owner_role": "analyst", "blocking": False}),
        ],
        declared=["G-01", "G-02", "Q-01"],
    )
    item = {"prefix": "NFR", "body": "Быстро", "target": "не хуже значения из Q-01"}
    assert sx.form_errors([item], view) == []
    assert sx.form_errors([{**item, "target": "из Q-02"}], view)


def test_prompt_is_translator_and_names_v12_form() -> None:
    prompt = sx.build_prompt(NFRQ, SEAMLESS, _customer())
    for needed in ("preference", "дословно", "гипотез", "owner_role",
                   "analyst", "не хуже значения из", "чисел"):  # fmt: skip
        assert needed in prompt, needed


# --- fix round 1: висячие ref, ref в body, мелочи ---


def test_dangling_lowercase_ref_rejected() -> None:
    items = [
        {"prefix": "Q", "body": "Порог?", "ref": "q-1", "owner_role": "analyst"},
        {"prefix": "NFR", "body": "Быстро", "target": "не хуже значения из q-2"},
    ]
    errors = sx.form_errors(items, _customer())
    assert any("q-2" in e for e in errors)


def test_ref_of_non_q_item_in_criterion_rejected() -> None:
    items = [
        {"prefix": "G", "body": "Цель", "ref": "new-1"},
        {**FR_FULL, "traces": ["new-1"], "acceptance": "не хуже значения из new-1"},
    ]
    errors = sx.form_errors(items, _customer())
    assert any("new-1" in e for e in errors)


def test_q_ref_in_body_replaced_by_id() -> None:
    items = [
        {"prefix": "Q", "body": "Порог?", "ref": "q-1", "owner_role": "analyst"},
        {"prefix": "NFR", "body": "Отклик не хуже значения из q-1",
         "target": "не хуже значения из q-1"},
    ]  # fmt: skip
    got = sx.structure_answer(NFRQ, "быстро", _customer(), _agent({"items": items}),
                              may_ask=True)  # fmt: skip
    assert isinstance(got, sx.Ready)
    nfr = _entries(got.payload)[1]
    assert nfr["body"] == "Отклик не хуже значения из Q-01"
    assert nfr["Target"] == "не хуже значения из Q-01"


def test_q_id_in_preference_message_names_verbatim_words() -> None:
    item = {"prefix": "NFR", "body": "Быстро", "preference": "как в Q-3",
            "target": "т"}  # fmt: skip
    (error,) = sx.form_errors([item], _customer())
    assert "Q-3" in error and "дословн" in error and "заведи Q" not in error


def test_owner_role_only_on_q() -> None:
    items, _ = sx.parse_items(json.dumps({"items": [
        {**FR_FULL, "owner_role": "analyst"},
        {"prefix": "Q", "body": "Порог?", "owner_role": "analyst"},
    ]}))  # fmt: skip
    assert "owner_role" not in items[0] and items[1]["owner_role"] == "analyst"


def test_prompt_labels_existing_q_as_research() -> None:
    view = _customer(
        entries=[Entry("Q-01", "Порог?", {"owner_role": "analyst"})],
        declared=["Q-01"],
    )
    prompt = sx.build_prompt(NFRQ, "ответ", view)
    assert "Q-01 (исследование): Порог?" in prompt and "тема ?" not in prompt


# --- fix round 2: висячая ссылка — только грамматика ref ---


@pytest.mark.parametrize("token", ["utf-8", "iso-8601", "sha-256", "covid-19"])
def test_plain_word_number_tokens_are_not_refs(token: str) -> None:
    items = [
        {"prefix": "Q", "body": "Порог?", "ref": "q-1", "owner_role": "analyst"},
        {"prefix": "NFR", "body": f"Экспорт в {token}",
         "acceptance": f"Файл в {token}, отклик не хуже значения из q-1"},
        {**FR_FULL, "body": f"Даты в {token}", "acceptance": f"Формат {token}"},
    ]  # fmt: skip
    assert sx.form_errors(items, _customer()) == []
