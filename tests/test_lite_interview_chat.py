"""Цикл интервью разговором (lite/interview_chat.py): подделки вместо сети.

Агент, `discovery status`/`answer`, зонд сессии и ввод человека подменены;
раскладка ответа — настоящая (`lite.structure`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from governance import interview as iv
from governance import run_state as rs
from lite import interview_chat as ic
from lite.interview_state import Entry, SessionView
from lite.structure import AgentUnavailable

SESSION = "sess-1"
G_ITEM = {"items": [{"prefix": "G", "body": "Сверка за полчаса"}], "ask": None}
J_ITEM = {"items": [{"prefix": "J", "body": "Выгрузить операции"}], "ask": None}
QUESTIONS = [
    ("customer.goals.01", "goals", "Зачем это нужно?"),
    ("customer.jobs.01", "jobs", "Какие задачи решают пользователи?"),
]


def _view(answered: int = 0) -> SessionView:
    return SessionView(
        frame="customer",
        required={"goals": "G", "jobs": "J", "functions": "FR"},
        optional={},
        entries=[Entry("G-01", "Цель", {})] if answered else [],
        declared=[],
        holders={},
        answers={f"q{i}": {} for i in range(answered)},
        answered_keys=[],
        readiness="incomplete",
    )


@dataclass
class Discovery:
    """Подделка discovery: вопросы по очереди, принятый ответ двигает очередь."""

    questions: list[tuple[str, str, str]] = field(
        default_factory=lambda: list(QUESTIONS)
    )
    refuse: int = 0
    sent: list[tuple[str, str]] = field(default_factory=list)
    accepted: int = 0

    def status(self, session_id: str) -> iv.DiscoveryReply:
        assert session_id == SESSION
        if not self.questions:
            return iv.DiscoveryReply(0, {"next_action": {}}, "")
        qid, key, text = self.questions[0]
        action = {
            "session_id": SESSION,
            "question_id": qid,
            "coverage_key": key,
            "question_text": text,
        }
        return iv.DiscoveryReply(20, {"next_action": action}, "")

    def send(self, question_id: str, payload: str) -> iv.DiscoveryReply:
        self.sent.append((question_id, payload))
        if self.refuse:
            self.refuse -= 1
            reason = {"operation": {"reason": "payload missing entries"}}
            return iv.DiscoveryReply(1, reason, "")
        assert self.questions[0][0] == question_id
        self.questions.pop(0)
        self.accepted += 1
        return iv.DiscoveryReply(0, {"operation": {"status": "ok"}}, "")

    def load(self, session_id: str) -> SessionView:
        return _view(self.accepted)


def _agent(*answers: Any) -> Any:
    queue = [a if isinstance(a, str) else json.dumps(a) for a in answers]
    prompts: list[str] = []

    def agent(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    agent.prompts = prompts  # type: ignore[attr-defined]
    return agent


def _reader(*lines: str | None) -> Any:
    """Ввод человека: строки по очереди, конец очереди — EOF (`None`)."""
    queue = list(lines)
    prompts: list[str] = []

    def read(prompt: str) -> str | None:
        prompts.append(prompt)
        return queue.pop(0) if queue else None

    read.prompts = prompts  # type: ignore[attr-defined]
    return read


def _state() -> rs.RunState:
    state = rs.RunState.__new__(rs.RunState)
    state.interview = {"session_id": SESSION, "stakeholder_role": "customer"}
    return state


def _chat(disc: Discovery, read: Any, agent: Any) -> ic.ChatResult:
    return ic.chat(
        _state(),
        read=read,
        agent=agent,
        status=disc.status,
        send=disc.send,
        resend=lambda qid, payload: pytest.fail("добор без непокрытых тем"),
        load=disc.load,
    )


def _text(payload: str) -> str:
    return yaml.safe_load(payload)["text"]


def test_chat_answers_until_interview_complete(capsys) -> None:
    disc = Discovery()
    read = _reader("Чтобы сверять платежи", "", "Выгружают операции", "")
    assert _chat(disc, read, _agent(G_ITEM, J_ITEM)) is ic.ChatResult.FINISHED
    assert [q for q, _ in disc.sent] == ["customer.goals.01", "customer.jobs.01"]
    out = capsys.readouterr().out
    assert "Вопрос 1 (цели): Зачем это нужно?" in out
    assert "Вопрос 2 (задачи пользователей): Какие задачи" in out
    assert "из" not in out.split("Вопрос 1")[1].split(":")[0]
    assert set(read.prompts) == {"> "}
    assert "{" not in out and "text:" not in out  # ни JSON, ни YAML


def test_chat_follow_up_appended_to_text(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1])
    ask = {"items": [{"prefix": "G", "body": "Сверка"}], "ask": "Как поймём?"}
    agent = _agent(ask, G_ITEM)
    read = _reader("Сверять платежи", "", "Полчаса вместо дня", "")
    assert _chat(disc, read, agent) is ic.ChatResult.FINISHED
    assert "Уточнение: Как поймём?" in capsys.readouterr().out
    text = _text(disc.sent[0][1])
    assert "Сверять платежи" in text and "Полчаса вместо дня" in text
    assert "Полчаса вместо дня" in agent.prompts[1]


def test_chat_max_two_follow_ups(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1])
    ask = {"items": [{"prefix": "G", "body": "Сверка"}], "ask": "Ещё?"}
    read = _reader("а", "", "б", "", "в", "")
    assert _chat(disc, read, _agent(ask, ask, ask)) is ic.ChatResult.FINISHED
    assert capsys.readouterr().out.count("Уточнение:") == 2
    assert len(disc.sent) == 1 and "в" in _text(disc.sent[0][1])


def test_chat_skip_sends_what_is_there(capsys) -> None:
    """M1: пропуск без прежнего текста — только text, агент не зовётся."""
    disc = Discovery(questions=QUESTIONS[:1])
    agent = _agent()
    assert _chat(disc, _reader("  Пропустить ", ""), agent) is (ic.ChatResult.FINISHED)
    assert "Уточнение:" not in capsys.readouterr().out
    assert agent.prompts == []
    assert len(disc.sent) == 1
    assert yaml.safe_load(disc.sent[0][1]) == {"text": "Пропустить"}


def test_chat_skip_in_follow_up_sends_earlier_text(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1])
    ask = {"items": [{"prefix": "G", "body": "Сверка"}], "ask": "Как поймём?"}
    read = _reader("Сверять платежи", "", "не знаю", "")
    assert _chat(disc, read, _agent(ask, ask)) is ic.ChatResult.FINISHED
    assert _text(disc.sent[0][1]) == "Сверять платежи"


def test_chat_eof_stops_and_resume_takes_next_question(capsys) -> None:
    disc = Discovery()
    first = _reader("Сверять платежи", "")  # затем EOF
    assert _chat(disc, first, _agent(G_ITEM)) is ic.ChatResult.STOPPED
    assert [q for q, _ in disc.sent] == ["customer.goals.01"]
    capsys.readouterr()
    again = _reader("Выгружают", "")
    assert _chat(disc, again, _agent(J_ITEM)) is ic.ChatResult.FINISHED
    assert [q for q, _ in disc.sent][1:] == ["customer.jobs.01"]
    assert "Вопрос 2 (задачи пользователей)" in capsys.readouterr().out


def test_chat_stop_command_stops_without_send() -> None:
    disc = Discovery()
    assert _chat(disc, _reader("/стоп"), _agent()) is ic.ChatResult.STOPPED
    assert disc.sent == []


def test_chat_discovery_refusal_retries_agent_once(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1], refuse=1)
    agent = _agent(G_ITEM, G_ITEM)
    assert _chat(disc, _reader("Сверять", ""), agent) is ic.ChatResult.FINISHED
    assert len(disc.sent) == 2 and len(agent.prompts) == 2
    assert "payload missing entries" in agent.prompts[1]
    assert "не принят" not in capsys.readouterr().out


def test_chat_second_refusal_reasks_same_question(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1], refuse=2)
    agent = _agent(G_ITEM, G_ITEM, G_ITEM)
    read = _reader("Сверять", "", "Сверять платежи", "")
    assert _chat(disc, read, agent) is ic.ChatResult.FINISHED
    out = capsys.readouterr().out
    assert "ответ не принят" in out and "payload" not in out
    assert out.count("Вопрос 1 (цели)") == 2
    assert [q for q, _ in disc.sent] == ["customer.goals.01"] * 3


def test_chat_failed_structure_reprompts_human(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1])
    agent = _agent("мусор", "снова мусор", G_ITEM)
    read = _reader("Сверять", "", "Сверять платежи", "")
    assert _chat(disc, read, agent) is ic.ChatResult.FINISHED
    out = capsys.readouterr().out
    assert "не удалось разобрать ответ — переформулируйте" in out
    assert out.count("Вопрос 1 (цели)") == 2
    assert _text(disc.sent[0][1]) == "Сверять платежи"


def test_chat_agent_unavailable_raises() -> None:
    def down(prompt: str) -> str:
        raise AgentUnavailable("claude не найден")

    disc = Discovery()
    with pytest.raises(ic.ChatError, match="агент недоступен: claude не найден"):
        _chat(disc, _reader("Сверять", ""), down)
    assert disc.sent == []


def test_chat_unicode_and_empty_answer(capsys) -> None:
    disc = Discovery(questions=QUESTIONS[:1])
    read = _reader("", "Сверка «платежей» — за ½ часа ✅", "вторая строка", "")
    assert _chat(disc, read, _agent(G_ITEM)) is ic.ChatResult.FINISHED
    assert capsys.readouterr().out.count("Вопрос 1 (цели)") == 2
    assert _text(disc.sent[0][1]) == "Сверка «платежей» — за ½ часа ✅\nвторая строка"


def test_chat_finished_when_no_question() -> None:
    disc = Discovery(questions=[])
    assert _chat(disc, _reader(), _agent()) is ic.ChatResult.FINISHED
    assert disc.sent == []


def test_read_answer_lines_until_empty() -> None:
    assert ic.read_answer(_reader("а", "б", "")) == "а\nб"
    assert ic.read_answer(_reader("а")) is None  # EOF посреди ответа
    assert ic.read_answer(_reader(" /стоп ")) is None
    assert ic.read_answer(_reader("")) == ""
    assert ic.is_skip("Не знаю") and not ic.is_skip("не знаю как")


def test_stdin_read_ctrl_c_is_stop(monkeypatch, capsys) -> None:
    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)
    assert ic.stdin_read("> ") is None
    assert capsys.readouterr().out == "\n"  # строка приглашения закрыта
    disc = Discovery()
    assert _chat(disc, ic.stdin_read, _agent()) is ic.ChatResult.STOPPED
    assert disc.sent == []


# --- dfl interview / dfl correct (lite.interview_view) ---

from lite import interview_view as iview  # noqa: E402
from lite.structure import Ask, Question  # noqa: E402

FR1 = {
    "id": "FR-01",
    "body": "выгрузка раз в неделю",
    "traces": ["G-01"],
    "Priority": "Must",
    "Acceptance": "файл открывается в Excel",
}
FR2 = {
    "id": "FR-02",
    "body": "письмо с отчётом",
    "traces": ["G-01"],
    "Priority": "Should",
    "Acceptance": "письмо приходит",
}


def _corr_view() -> SessionView:
    def entry(d: dict[str, Any]) -> Entry:
        fields = {k: v for k, v in d.items() if k not in ("id", "body")}
        return Entry(d["id"], d["body"], fields)

    return SessionView(
        frame="customer",
        required={"goals": "G", "functions": "FR", "nfr": "NFR"},
        optional={"risks": "R"},
        entries=[Entry("G-01", "Сверка", {}), entry(FR1), entry(FR2)],
        declared=["G-01", "FR-01", "FR-02"],
        holders={"G-01": "q-goals", "FR-01": "q-fn", "FR-02": "q-fn"},
        answers={
            "q-goals": {"text": "цель", "entries": [{"id": "G-01", "body": "Сверка"}]},
            "q-fn": {"text": "функции", "entries": [dict(FR1), dict(FR2)]},
        },
        answered_keys=["goals", "functions"],
        readiness="incomplete",
        findings=[
            "required topic 'nfr' is not covered",
            "FR-02 has no trace to an existing G/J entry",
            "X-01 is a blocking open question",
        ],
    )


def test_render_view_topics_entries_and_findings() -> None:
    q = Question("q-nfr", "nfr", "Какие требования к качеству?")
    out = iview.render_view(_corr_view(), q)
    assert "Вопрос 3 (нефункциональные требования): Какие требования" in out
    assert "[x] цели" in out and "[ ] нефункциональные требования" in out
    assert "[ ] риски" in out
    assert out.index("[x] цели") < out.index("[ ] риски")
    assert (
        "FR-01 выгрузка раз в неделю — Must; критерий: файл открывается в Excel; → G-01"
    ) in out
    assert "Мешает готовности:" in out
    assert "не раскрыта тема: нефункциональные требования" in out
    assert "required topic" not in out and "has no trace" not in out


def test_render_view_no_current_question_and_nfr_target() -> None:
    view = _corr_view()
    view.entries.append(Entry("NFR-01", "быстро", {"Target": "до 2 с"}))
    out = iview.render_view(view, None)
    assert "вопросов больше нет" in out
    assert "NFR-01 быстро — цель: до 2 с" in out


def _fr1_item(**over: Any) -> dict[str, Any]:
    item = {
        "id": "FR-01",
        "prefix": "FR",
        "body": "выгрузка раз в месяц",
        "traces": ["G-01"],
        "priority": "Must",
        "acceptance": "файл открывается в Excel",
    }
    return item | over


class Sender:
    def __init__(self, code: int = 0) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.code = code

    def __call__(self, qid: str, payload: str) -> iv.DiscoveryReply:
        self.sent.append((qid, yaml.safe_load(payload)))
        env = {"operation": {"reason": "bad"}}
        return iv.DiscoveryReply(self.code, env, "")


def _correct(agent: Any, send: Any, text: str = "раз в месяц") -> Any:
    return iview.correct(
        _state(), text, agent=agent, load=lambda s: _corr_view(), send=send
    )


def test_correct_rebuilds_holder_answer_keeping_other_entries() -> None:
    send = Sender()
    agent = _agent({"items": [_fr1_item()], "ask": None})
    out = _correct(agent, send)
    assert out == "FR-01: «выгрузка раз в неделю» → «выгрузка раз в месяц»"
    [(qid, payload)] = send.sent
    assert qid == "q-fn"
    assert payload["text"] == "функции"
    first, second = payload["entries"]
    assert first["id"] == "FR-01" and first["body"] == "выгрузка раз в месяц"
    assert first["Priority"] == "Must" and first["traces"] == ["G-01"]
    assert second == FR2  # нетронутый пункт — как был


def test_correct_two_holders_two_sends_and_field_change() -> None:
    send = Sender()
    g = {"id": "G-01", "prefix": "G", "body": "Сверка за час"}
    fr2 = _fr1_item(id="FR-02", body="письмо с отчётом", acceptance="письмо за минуту")
    out = _correct(_agent({"items": [g, fr2], "ask": None}), send)
    assert {qid for qid, _ in send.sent} == {"q-goals", "q-fn"}
    assert "G-01: «Сверка» → «Сверка за час»" in out
    assert "FR-02 Acceptance: «письмо приходит» → «письмо за минуту»" in out


def test_correct_uses_supersede_with_holder_question() -> None:
    """dfl передаёт `supersede=True` отправителю поправок (см. test_lite_dfl)."""
    send = Sender()
    _correct(_agent({"items": [_fr1_item()], "ask": None}), send)
    assert send.sent[0][0] == "q-fn"


def test_correct_unclear_asks_once_without_sending() -> None:
    send = Sender()
    agent = _agent({"items": [], "ask": "Какую функцию поправить?"})
    out = _correct(agent, send, "поменяй")
    assert out == Ask("Какую функцию поправить?")
    assert send.sent == []


def test_correct_unknown_id_retries_then_refuses() -> None:
    send = Sender()
    bad = _fr1_item(id="FR-09")
    agent = _agent({"items": [bad], "ask": None}, {"items": [bad], "ask": None})
    with pytest.raises(iview.CorrectError, match="не понял, что исправить"):
        _correct(agent, send)
    assert send.sent == [] and len(agent.prompts) == 2
    assert "FR-09" in agent.prompts[1]


def test_correct_unknown_id_then_fixed_on_retry() -> None:
    send = Sender()
    agent = _agent(
        {"items": [_fr1_item(id="FR-09")], "ask": None},
        {"items": [_fr1_item()], "ask": None},
    )
    _correct(agent, send)
    assert len(send.sent) == 1


def test_correct_must_not_break_required_fields() -> None:
    send = Sender()
    item = _fr1_item(traces=[])  # агент не прислал traces → берём прежние
    out = _correct(_agent({"items": [item], "ask": None}), send)
    assert "FR-01" in out
    assert send.sent[0][1]["entries"][0]["traces"] == ["G-01"]


def test_correct_refuses_gap_introduced_by_correction() -> None:
    old = dict(FR1)
    new = {k: v for k, v in FR1.items() if k != "Acceptance"}
    with pytest.raises(iview.CorrectError, match="проверить"):
        iview._check_required([iview._Change(old, new)], _corr_view())


def test_correct_preexisting_gap_does_not_block_and_is_hinted() -> None:
    send = Sender()
    view = _corr_view()
    view.answers["q-fn"]["entries"][0].pop("Acceptance")  # уже неполный пункт
    agent = _agent({"items": [_fr1_item(acceptance=None)], "ask": None})
    out = iview.correct(_state(), "x", agent=agent, load=lambda s: view, send=send)
    assert len(send.sent) == 1
    assert "FR-01: «выгрузка раз в неделю» → «выгрузка раз в месяц»" in out
    assert "у FR-01 по-прежнему нет:" in out
    assert "проверить" in out and "dfl correct" in out


def test_correct_discovery_refusal_is_error() -> None:
    agent = _agent({"items": [_fr1_item()], "ask": None})
    with pytest.raises(iview.CorrectError, match="не принял"):
        _correct(agent, Sender(code=1))


def test_correct_partial_send_names_applied_and_not_applied() -> None:
    class FailSecond(Sender):
        def __call__(self, qid: str, payload: str) -> iv.DiscoveryReply:
            super().__call__(qid, payload)
            return iv.DiscoveryReply(0 if len(self.sent) == 1 else 2,
                                     {"operation": {"reason": "конфликт"}}, "")  # fmt: skip

    g = {"id": "G-01", "prefix": "G", "body": "Сверка за час"}
    fr2 = _fr1_item(id="FR-02", body="письмо с отчётом", acceptance="за минуту")
    with pytest.raises(iview.CorrectError) as exc:
        _correct(_agent({"items": [g, fr2], "ask": None}), FailSecond())
    assert "применено: G-01; не применено: FR-02 (discovery: конфликт)" in str(
        exc.value
    )


def test_correct_agent_down_is_error() -> None:
    def down(prompt: str) -> str:
        raise AgentUnavailable("нет claude")

    with pytest.raises(iview.CorrectError, match="нет claude"):
        _correct(down, Sender())


def test_correct_no_actual_change_is_not_understood() -> None:
    same = _fr1_item(body="выгрузка раз в неделю")
    with pytest.raises(iview.CorrectError, match="не понял"):
        _correct(_agent({"items": [same], "ask": None}), Sender())


# --- финальное ревью: I1 (Ctrl-C во время работы агента и discovery) ---


def test_ctrl_c_while_agent_works_stops_without_send() -> None:
    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    disc = Discovery()
    assert _chat(disc, _reader("Сверять", ""), interrupted) is ic.ChatResult.STOPPED
    assert disc.sent == []


def test_ctrl_c_while_discovery_works_stops() -> None:
    disc = Discovery()

    def status(session_id: str) -> iv.DiscoveryReply:
        raise KeyboardInterrupt

    got = ic.chat(
        _state(), read=_reader(), agent=_agent(), status=status,
        send=disc.send, resend=disc.send, load=disc.load,
    )  # fmt: skip
    assert got is ic.ChatResult.STOPPED


def test_correct_resend_skips_entries_held_by_later_answer() -> None:
    """I4: FR-01 позже обновлён другим ответом — пересылка ответа q-fn
    (поправка FR-02) не возвращает старую версию FR-01."""
    view = _corr_view()
    view.holders["FR-01"] = "q-prio"
    view.answers["q-prio"] = {"text": "важность", "entries": [dict(FR1)]}
    send = Sender()
    fr2 = _fr1_item(id="FR-02", body="письмо с отчётом", acceptance="за минуту")
    iview.correct(
        _state(), "x", agent=_agent({"items": [fr2], "ask": None}),
        load=lambda s: view, send=send,
    )  # fmt: skip
    [(qid, payload)] = send.sent
    assert qid == "q-fn"
    assert [e["id"] for e in payload["entries"]] == ["FR-02"]


# --- финальное ревью: I5 (добор непокрытых обязательных тем) ---

GOALS_ANSWER = {"text": "цели", "entries": [{"id": "G-01", "body": "Цель"}]}


def _ended_view(**over: Any) -> SessionView:
    """Интервью кончилось: jobs и functions не покрыты (jobs пропущен)."""
    base: dict[str, Any] = {
        "frame": "customer",
        "required": {"goals": "G", "jobs": "J", "functions": "FR"},
        "optional": {"risks": "RK"},
        "entries": [Entry("G-01", "Цель", {})],
        "declared": ["G-01"],
        "holders": {"G-01": "customer.goals.01"},
        "answers": {
            "customer.goals.01": dict(GOALS_ANSWER),
            "customer.jobs.01": {"text": "пропустить"},
        },
        "answered_keys": ["goals", "jobs"],
        "readiness": "incomplete",
        "asked": {"customer.goals.01": "goals", "customer.jobs.01": "jobs"},
    }
    return SessionView(**{**base, **over})


@dataclass
class Ended:
    """discovery после последнего ответа: вопросов нет, код `code`."""

    view: SessionView = field(default_factory=_ended_view)
    code: int = 11
    resent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    statuses: int = 0

    def status(self, session_id: str) -> iv.DiscoveryReply:
        self.statuses += 1
        return iv.DiscoveryReply(self.code, {"next_action": {}}, "")

    def resend(self, qid: str, payload: str) -> iv.DiscoveryReply:
        self.resent.append((qid, yaml.safe_load(payload)))
        return iv.DiscoveryReply(11, {"operation": {"status": "ok"}}, "")

    def load(self, session_id: str) -> SessionView:
        return self.view


def _top_up(ended: Ended, read: Any, agent: Any) -> ic.ChatResult:
    return ic.chat(
        _state(), read=read, agent=agent, status=ended.status,
        send=lambda q, p: pytest.fail("send в доборе"),
        resend=ended.resend, load=ended.load,
    )  # fmt: skip


FR_ITEM = {
    "prefix": "FR", "body": "Выгрузка в CSV", "traces": ["G-01"],
    "priority": "Must", "acceptance": "Открывается в Excel",
}  # fmt: skip


def test_top_up_asks_uncovered_topics_and_appends_to_holder(capsys) -> None:
    ended = Ended()
    read = _reader("Выгружают операции", "", "Выгрузка в CSV", "")
    fr = {"items": [FR_ITEM], "ask": None}
    agent = _agent(J_ITEM, fr)
    assert _top_up(ended, read, agent) is ic.ChatResult.FINISHED
    out = capsys.readouterr().out
    assert "Не хватает: задачи пользователей —" in out
    assert "Не хватает: функции —" in out
    (q1, jobs), (q2, funcs) = ended.resent
    # тема jobs: держатель — последний отвеченный вопрос темы
    assert q1 == "customer.jobs.01"
    assert jobs["text"] == "пропустить\n\nВыгружают операции"
    assert [e["body"] for e in jobs["entries"]] == ["Выгрузить операции"]
    # тема functions без своих вопросов: последний отвеченный вообще
    assert q2 == "customer.jobs.01"
    assert [e["body"] for e in funcs["entries"]] == ["Выгрузка в CSV"]
    assert ended.statuses == 2  # добор — один раз, затем снова status


def test_top_up_keeps_holder_entries_and_text() -> None:
    ended = Ended(view=_ended_view(required={"goals": "G", "functions": "FR"},
                                   answered_keys=["goals"]))  # fmt: skip
    read = _reader("Выгрузка в CSV", "")
    assert _top_up(ended, read, _agent({"items": [FR_ITEM]})) is (
        ic.ChatResult.FINISHED
    )
    [(qid, payload)] = ended.resent
    assert qid == "customer.jobs.01"
    ended2 = Ended(view=_ended_view(
        required={"goals": "G", "functions": "FR"},
        answers={"customer.goals.01": dict(GOALS_ANSWER)},
        asked={"customer.goals.01": "goals"},
    ))  # fmt: skip
    _top_up(ended2, _reader("Выгрузка в CSV", ""), _agent({"items": [FR_ITEM]}))
    [(qid, payload)] = ended2.resent
    assert qid == "customer.goals.01"
    assert payload["text"] == "цели\n\nВыгрузка в CSV"
    assert [e["id"] for e in payload["entries"]] == ["G-01", "FR-01"]


def test_top_up_skip_leaves_topic() -> None:
    ended = Ended()
    agent = _agent()
    read = _reader("пропустить", "", "не знаю", "")
    assert _top_up(ended, read, agent) is ic.ChatResult.FINISHED
    assert ended.resent == [] and agent.prompts == []


def test_top_up_follow_ups_limited() -> None:
    ended = Ended(view=_ended_view(required={"goals": "G", "jobs": "J"}))
    ask = {"items": [{"prefix": "J", "body": "Выгрузить"}], "ask": "Когда?"}
    read = _reader("а", "", "б", "", "в", "")
    assert _top_up(ended, read, _agent(ask, ask, ask)) is ic.ChatResult.FINISHED
    [(_qid, payload)] = ended.resent
    assert "в" in payload["text"]


@pytest.mark.parametrize("code", [0, 1, 2])
def test_no_top_up_unless_incomplete_end(code: int) -> None:
    ended = Ended(code=code)
    assert _top_up(ended, _reader(), _agent()) is ic.ChatResult.FINISHED
    assert ended.resent == []


def test_no_top_up_when_required_covered() -> None:
    ended = Ended(view=_ended_view(required={"goals": "G"}))
    assert _top_up(ended, _reader(), _agent()) is ic.ChatResult.FINISHED
    assert ended.resent == []


def test_top_up_eof_stops() -> None:
    ended = Ended()
    assert _top_up(ended, _reader(), _agent()) is ic.ChatResult.STOPPED
    assert ended.resent == []


def test_top_up_skips_topics_without_prefix() -> None:
    """Тему без префикса закрывает только ответ на её вопрос — добор её не
    покроет, человека о ней не спрашиваем."""
    view = _ended_view(required={"goals": "G", "feasibility_review": None})
    ended = Ended(view=view)
    assert _top_up(ended, _reader(), _agent()) is ic.ChatResult.FINISHED
    assert ended.resent == []


def test_chat_lines_reach_log_sink() -> None:
    """Реплики интервью разговором идут через `say` — и в лог вызова."""
    import io

    from lite.output import set_sink

    buf = io.StringIO()
    set_sink(buf)
    try:
        disc = Discovery()
        read = _reader("Чтобы сверять платежи", "", "Выгружают операции", "")
        _chat(disc, read, _agent(G_ITEM, J_ITEM))
    finally:
        set_sink(None)
    assert "Вопрос 1 (цели): Зачем это нужно?" in buf.getvalue()


def test_stdin_read_broken_utf8_asks_again(monkeypatch, capsys) -> None:
    """Битая UTF-8-строка (стёрт полсимвола) — подсказка и повтор, не крах."""
    calls = iter([UnicodeDecodeError("utf-8", b"\xd1", 0, 1, "invalid"), "ответ"])

    def fake_input(prompt: str) -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("builtins.input", fake_input)
    assert ic.stdin_read("> ") == "ответ"
    assert ic.BROKEN_INPUT in capsys.readouterr().out
