"""Tests for the free interviewer of the personal path."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from lite.personal import interviewer as iv
from lite.personal.brief import Brief
from lite.structure import AgentUnavailable

GOOD: dict[str, Any] = {
    "goals": [{"id": "G-01", "title": "Быстро", "text": "Стало быстро."}],
    "functions": [
        {
            "id": "FR-01",
            "title": "Умеет",
            "goals": ["G-01"],
            "criteria": ["команда печатает итог"],
            "words": "чтоб печатал",
        }
    ],
    "context": {
        "users": "только я",
        "auth": "нет",
        "network": "нет",
        "local": "читает ~/notes",
        "platform": "macOS",
    },
}


def bare_fr() -> dict[str, Any]:
    """GOOD plus FR-02 without a criterion."""
    data = copy.deepcopy(GOOD)
    data["functions"].append(
        {
            "id": "FR-02",
            "title": "Экспорт",
            "goals": ["G-01"],
            "criteria": [],
            "words": "и экспорт",
        }  # fmt: skip
    )
    return data


def ask(question: str, options: Iterable[str] = ()) -> str:
    return json.dumps({"ask": question, "options": list(options)}, ensure_ascii=False)


def brief(data: dict[str, Any]) -> str:
    return json.dumps({"brief": data}, ensure_ascii=False)


class Script:
    """Scripted agent (list of replies) recording prompts and read events."""

    def __init__(self, replies: list[str | Exception], events: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.events = events

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.events.append("agent")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Reader:
    """Scripted `read`: lines in order, `None` once exhausted (EOF)."""

    def __init__(self, lines: list[str], events: list[str]) -> None:
        self.lines = list(lines)
        self.events = events

    def __call__(self, prompt: str) -> str | None:
        self.events.append("read")
        return self.lines.pop(0) if self.lines else None


def run(
    tmp_path: Path, replies: list[str | Exception], lines: list[str]
) -> tuple[Brief | None, Script, list[str]]:
    events: list[str] = []
    agent = Script(replies, events)
    result = iv.interview(tmp_path, "заметки", read=Reader(lines, events), agent=agent)
    return result, agent, events


def transcript(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / iv.TRANSCRIPT
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def test_ask_then_brief_confirmed(tmp_path: Path) -> None:
    replies = [ask("Кто пользуется?", ["только я", "я и семья"]),
               ask("Что главное?"), brief(GOOD)]  # fmt: skip
    result, _, _ = run(tmp_path, replies, ["1", "быстро искать", "", "1"])
    assert isinstance(result, Brief)
    assert result.functions[0].id == "FR-01"
    roles = [(e["role"], e.get("kind")) for e in transcript(tmp_path)]
    assert roles == [
        ("agent", "ask"),
        ("human", None),
        ("agent", "ask"),
        ("human", None),
        ("agent", "summary"),
    ]
    entries = transcript(tmp_path)
    assert entries[1]["text"] == "1. только я"
    assert entries[3]["text"] == "быстро искать"


def test_problems_go_back_to_agent_not_human(tmp_path: Path) -> None:
    replies = [brief(bare_fr()), brief(GOOD)]
    result, agent, events = run(tmp_path, replies, ["1"])
    assert result is not None
    assert "FR-02: нет критерия приёмки" in agent.prompts[1]
    assert events[:2] == ["agent", "agent"]
    check = [e for e in transcript(tmp_path) if e["role"] == "check"]
    assert len(check) == 1


def test_cap_forces_summary_and_demotes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replies = [ask(f"Вопрос {n}?", ["да", "нет"]) for n in range(iv.MAX_QUESTIONS)]
    replies.append(brief(bare_fr()))
    lines = ["1"] * iv.MAX_QUESTIONS + ["1"]
    result, agent, _ = run(tmp_path, replies, lines)
    assert iv.CAP_NOTE in agent.prompts[iv.MAX_QUESTIONS]
    assert iv.CAP_NOTE not in agent.prompts[iv.MAX_QUESTIONS - 1]
    out = capsys.readouterr().out
    assert "## Уточнить" in out
    assert result is not None
    assert [f.id for f in result.functions] == ["FR-01"]
    assert any("Экспорт" in i for i in result.open_items)


def test_ask_after_cap_is_form_error(tmp_path: Path) -> None:
    replies = [ask(f"Вопрос {n}?", ["да"]) for n in range(iv.MAX_QUESTIONS)]
    replies += [ask("Ещё один?"), brief(GOOD)]
    result, agent, _ = run(tmp_path, replies, ["1"] * (iv.MAX_QUESTIONS + 1))
    assert result is not None
    assert "brief" in agent.prompts[-1].split(iv.CAP_NOTE)[-1]
    asks = [e for e in transcript(tmp_path) if e.get("kind") == "ask"]
    assert len(asks) == iv.MAX_QUESTIONS


def test_three_checks_in_row_then_forced(tmp_path: Path) -> None:
    replies = [brief(bare_fr())] * 4
    result, agent, events = run(tmp_path, replies, ["1"])
    assert len(agent.prompts) == 4
    assert events[:4] == ["agent"] * 4
    assert result is not None
    assert [f.id for f in result.functions] == ["FR-01"]
    checks = [e for e in transcript(tmp_path) if e["role"] == "check"]
    assert len(checks) == iv.MAX_CHECKS_IN_ROW


def test_remaining_problems_go_to_open_items_when_forced(tmp_path: Path) -> None:
    data = copy.deepcopy(GOOD)
    data["context"]["platform"] = ""
    result, _, _ = run(tmp_path, [brief(data)] * 4, ["1"])
    assert result is not None
    assert "контекст: пустое поле «Платформа»" in result.open_items


def test_correction_continues_conversation(tmp_path: Path) -> None:
    fixed = copy.deepcopy(GOOD)
    fixed["context"]["platform"] = "Linux"
    replies = [brief(GOOD), brief(fixed)]
    result, agent, _ = run(
        tmp_path, replies, ["2. поправить: платформа Linux", "", "1"]
    )
    assert result is not None
    assert result.context.platform == "Linux"
    assert "Человек: поправить: платформа Linux" in agent.prompts[1]


def test_correction_at_cap_still_reaches_agent(tmp_path: Path) -> None:
    fixed = copy.deepcopy(GOOD)
    fixed["context"]["platform"] = "Linux"
    replies = [ask(f"Вопрос {n}?", ["да"]) for n in range(iv.MAX_QUESTIONS)]
    replies += [brief(GOOD), brief(fixed)]
    lines = ["1"] * iv.MAX_QUESTIONS + ["2", "платформа Linux", "", "1"]
    result, agent, _ = run(tmp_path, replies, lines)
    assert result is not None and result.context.platform == "Linux"
    assert "платформа Linux" in agent.prompts[-1]
    assert iv.CAP_NOTE in agent.prompts[-1]


def test_resume_from_transcript(tmp_path: Path) -> None:
    (tmp_path / iv.TRANSCRIPT).write_text(
        json.dumps(
            {"role": "agent", "kind": "ask", "text": "Кто пользуется?", "options": []},
            ensure_ascii=False,
        )
        + "\n"  # fmt: skip
        + json.dumps({"role": "human", "text": "только я"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    result, agent, events = run(tmp_path, [brief(GOOD)], ["1"])
    assert result is not None
    assert "Кто пользуется?" in agent.prompts[0]
    assert "только я" in agent.prompts[0]
    assert events == ["agent", "read"]


def test_resume_pending_question_without_agent(tmp_path: Path) -> None:
    (tmp_path / iv.TRANSCRIPT).write_text(
        json.dumps(
            {
                "role": "agent",
                "kind": "ask",
                "text": "Кто пользуется?",
                "options": ["я", "семья"],
            },
            ensure_ascii=False,
        )
        + "\n",  # fmt: skip
        encoding="utf-8",
    )
    result, agent, events = run(tmp_path, [brief(GOOD)], ["2", "1"])
    assert result is not None
    assert events[0] == "read"
    assert "Человек: 2. семья" in agent.prompts[0]


def test_malformed_agent_twice_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, agent, _ = run(tmp_path, ["привет", "не знаю, что сказать"], [])
    assert result is None
    assert "агент не ответил по форме" in capsys.readouterr().out
    assert "в ответе нет JSON-объекта" in agent.prompts[1]


def test_malformed_once_then_ok(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path, ['{"brief": {"goals": 1}}', brief(GOOD)], ["1"])
    assert result is not None


def test_agent_unavailable_stops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result, _, _ = run(tmp_path, [AgentUnavailable("нет ответа за 300 с")], [])
    assert result is None
    out = capsys.readouterr().out
    assert "нет ответа за 300 с" in out
    assert "платных вызовов агента: 1" in out


def test_stop_word_and_skip_on_confirmation(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path, [ask("Что?")], ["/стоп"])
    assert result is None
    result, _, _ = run(tmp_path / "b", [brief(GOOD)], ["пропустить"])
    assert result is None


def test_paid_calls_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replies = [ask("Кто?", ["я"]), ask("Что?", ["это"]), brief(GOOD)]
    result, agent, _ = run(tmp_path, replies, ["1", "1", "1"])
    assert result is not None
    out = capsys.readouterr().out
    assert f"платных вызовов агента: {len(agent.prompts)}" in out
    assert out.count("платных вызовов агента") == 1


def test_prompt_forbids_enterprise_topics() -> None:
    text = iv.PROMPT_PATH.read_text(encoding="utf-8")
    for word in ("спонсор", "compliance", "противореч", "MoSCoW", "бюджет"):
        assert word in text
    assert iv.CAP_NOTE not in text


def test_free_text_correction_keeps_leading_number(tmp_path: Path) -> None:
    replies = [brief(GOOD), brief(GOOD)]
    _, agent, _ = run(tmp_path, replies, ["20 файлов в день, не 2", "", "1"])
    assert "Человек: поправить: 20 файлов в день, не 2" in agent.prompts[1]


def test_empty_brief_not_offered_for_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only_bare = copy.deepcopy(bare_fr())
    only_bare["functions"] = only_bare["functions"][1:]
    only_bare["functions"][0]["id"] = "FR-01"
    replies = [ask(f"Вопрос {n}?", ["да"]) for n in range(iv.MAX_QUESTIONS)]
    replies += [brief(only_bare), brief(GOOD)]
    lines = ["1"] * iv.MAX_QUESTIONS + ["экспорт в csv за секунду", "", "1"]
    result, agent, _ = run(tmp_path, replies, lines)
    out = capsys.readouterr().out
    assert iv.NOTHING_TO_BUILD in out
    assert out.count(iv.CONFIRM) == 1
    assert "Человек: экспорт в csv за секунду" in agent.prompts[-1]
    assert result is not None and result.functions[0].id == "FR-01"


def test_corrupt_transcript_line_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = json.dumps({"role": "human", "text": "только я"}, ensure_ascii=False)
    (tmp_path / iv.TRANSCRIPT).write_text(
        '{"role": "agent", "te\n' + good + "\n[1, 2]\n", encoding="utf-8"
    )
    result, agent, _ = run(tmp_path, [brief(GOOD)], ["1"])
    assert result is not None
    out = capsys.readouterr().out
    assert out.count(iv.CORRUPT_LINE) == 2
    assert "Человек: только я" in agent.prompts[0]
