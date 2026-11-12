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
    disc = Discovery(questions=QUESTIONS[:1])
    # агент спросил бы, но при пропуске уточнений нет
    ask = {"items": [], "ask": "Зачем?"}
    assert _chat(disc, _reader("  Пропустить ", ""), _agent(ask)) is (
        ic.ChatResult.FINISHED
    )
    assert "Уточнение:" not in capsys.readouterr().out
    assert len(disc.sent) == 1 and _text(disc.sent[0][1]) == "Пропустить"


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
