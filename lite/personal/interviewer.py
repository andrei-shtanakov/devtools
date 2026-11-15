"""Свободный интервьюер личного пути: агент сам выбирает следующий вопрос.

Каждый ход — промпт `prompts/interviewer.md` + разговор → агент; ответ агента —
один JSON-объект: `{"ask": "<вопрос>", "options": [...]}` или
`{"brief": <объект brief_from_json>}`. Вопрос показывается человеку, ответ
пишется в разговор. Бриф проходит `brief_problems`: проблемы уходят агенту
репликой `check` (человек их не видит), не больше `MAX_CHECKS_IN_ROW` подряд.
После `MAX_QUESTIONS` вопросов (или `MAX_CHECKS_IN_ROW` проверок подряд) бриф
принимается как есть: функции без критерия и оставшиеся проблемы — в «Уточнить».
Затем сводка и «верно?»: «1» — бриф готов, поправка — реплика человека, разговор
продолжается.

Реплики — по одной JSON-строке в `<run_dir>/interview.jsonl`
(`{"role": "agent"|"human"|"check", "text": ...}`; у реплик агента ещё `kind`:
`ask` | `brief` | `summary`, у `ask` — `options`). При старте разговор читается
оттуда и продолжается (RF-1): незаданный ответ на вопрос — без нового вызова.
Битая строка журнала пропускается с предупреждением. Бриф без целей или функций
(после переноса в «Уточнить») на подтверждение не идёт: человека спрашивают,
что должно работать, и разговор продолжается.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from lite.interview_chat import Read, is_skip, read_answer
from lite.output import say
from lite.personal.brief import (
    Brief,
    brief_from_json,
    brief_problems,
    demote_uncovered,
    render_brief,
)
from lite.structure import Agent, AgentUnavailable, FormError, first_json_object

MAX_QUESTIONS = 20
MAX_CHECKS_IN_ROW = 3
MAX_FORM_ERRORS = 2
TRANSCRIPT = "interview.jsonl"
PROMPT_PATH = Path(__file__).parent / "prompts" / "interviewer.md"
CAP_NOTE = "СЕЙЧАС: лимит вопросов исчерпан"
CONFIRM = "Верно? 1. да · 2. поправить: <что>"
WHAT_TO_FIX = "Что поправить?"
FORM_STOP = (
    "агент не ответил по форме дважды подряд — интервью остановлено; "
    "продолжить: dfl run"
)
UNAVAILABLE_STOP = (
    "агент недоступен ({reason}) — интервью остановлено; продолжить: dfl run"
)
PAID_CALLS = "платных вызовов агента: {n}"
NOTHING_TO_BUILD = (
    "строить пока нечего: нет ни одной функции с критерием приёмки — "
    "расскажите, что должно работать (или /стоп)"
)
CORRUPT_LINE = "строка журнала интервью не прочитана — пропущена"
_FIX_PREFIX = re.compile(
    r"^\s*(?:2\s*[.):](?!\d)\s*)?(?:поправить\s*:?\s*)?", re.IGNORECASE
)
_SPEAKERS = {"human": "Человек", "check": "Проверка брифа (человек её не видел)"}
_AGENT_SPEAKERS = {
    "ask": "Интервьюер",
    "brief": "Интервьюер (бриф)",
    "summary": "Интервьюер (сводка человеку)",
}

Entry = dict[str, Any]


@dataclass(frozen=True)
class AskTurn:
    """Агент задаёт вопрос человеку."""

    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class BriefTurn:
    """Агент прислал бриф; `data` — исходный JSON (для разговора)."""

    brief: Brief
    data: dict[str, Any]


class _Stop(Exception):
    """Разговор остановлен; сообщение (если нужно) уже выведено."""


class _Log:
    """Разговор в `interview.jsonl`: читается при старте, пишется по строке."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[Entry] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            parsed = [_parse_entry(ln) for ln in lines if ln.strip()]
            self.entries = [e for e in parsed if e is not None]

    def append(self, entry: Entry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.entries.append(entry)


def _parse_entry(line: str) -> Entry | None:
    """Реплика из строки журнала; битая строка — `None` и предупреждение."""
    try:
        entry = json.loads(line)
    except ValueError:
        entry = None
    if (
        isinstance(entry, dict)
        and isinstance(entry.get("role"), str)
        and isinstance(entry.get("text"), str)
    ):
        return entry
    say(CORRUPT_LINE)
    return None


def interview(run_dir: Path, subject: str, *, read: Read, agent: Agent) -> Brief | None:
    """Вести свободное интервью; подтверждённый человеком бриф или `None`.

    `None` — человек остановился (`/стоп`, EOF, «пропустить» на подтверждении),
    агент дважды подряд ответил не по форме или недоступен (сообщение уже
    выведено). Разговор хранится в `run_dir / TRANSCRIPT` и продолжается с него.
    Число платных вызовов агента печатается один раз в конце."""
    calls = 0

    def counted(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return agent(prompt)

    try:
        return _converse(_Log(run_dir / TRANSCRIPT), subject, read, counted)
    except _Stop:
        return None
    except AgentUnavailable as exc:
        say(UNAVAILABLE_STOP.format(reason=exc))
        return None
    except KeyboardInterrupt:
        say("")
        return None
    finally:
        say(PAID_CALLS.format(n=calls))


def _converse(log: _Log, subject: str, read: Read, agent: Agent) -> Brief:
    """Цикл разговора до подтверждённого брифа (остановка — `_Stop`)."""
    while True:
        pending = _pending_ask(log.entries)
        if pending is not None:
            _answer(pending, log, read)
            continue
        turn = _next_turn(log.entries, subject, agent)
        if isinstance(turn, AskTurn):
            log.append(_ask_entry(turn))
            continue
        brief = _settle_brief(turn, log)
        if brief is None:
            continue
        if not brief.goals or not brief.functions:
            _nothing_to_build(turn, log, read)
            continue
        log.append({"role": "agent", "kind": "summary", "text": render_brief(brief)})
        if _confirmed(brief, log, read):
            return brief


def _nothing_to_build(turn: BriefTurn, log: _Log, read: Read) -> None:
    """Бриф без целей или функций не на подтверждение: спросить человека, что
    должно работать; бриф и ответ уходят в разговор (агент их увидит)."""
    while True:
        say(NOTHING_TO_BUILD)
        text = read_answer(read)
        if text is None:
            raise _Stop
        if text:
            break
    brief_text = json.dumps(turn.data, ensure_ascii=False)
    log.append({"role": "agent", "kind": "brief", "text": brief_text})
    log.append({"role": "human", "text": text})


def _pending_ask(entries: list[Entry]) -> AskTurn | None:
    """Последняя реплика — вопрос без ответа (продолжение после остановки)."""
    if not entries or entries[-1].get("kind") != "ask":
        return None
    last = entries[-1]
    return AskTurn(str(last["text"]), tuple(last.get("options") or ()))


def _ask_entry(turn: AskTurn) -> Entry:
    return {
        "role": "agent",
        "kind": "ask",
        "text": turn.question,
        "options": list(turn.options),
    }


def _answer(turn: AskTurn, log: _Log, read: Read) -> None:
    """Показать вопрос с вариантами, прочитать и записать ответ человека."""
    while True:
        say(turn.question)
        for n, option in enumerate(turn.options, start=1):
            say(f"{n}. {option}")
        text = read_answer(read, quick=str.isdigit)
        if text is None:
            raise _Stop
        if text:
            log.append({"role": "human", "text": _resolve_option(text, turn.options)})
            return


def _resolve_option(text: str, options: tuple[str, ...]) -> str:
    """Номер варианта → «N. текст варианта» (агенту номер без текста ни о чём)."""
    if text.isdigit() and 1 <= int(text) <= len(options):
        return f"{text}. {options[int(text) - 1]}"
    return text


def _next_turn(entries: list[Entry], subject: str, agent: Agent) -> AskTurn | BriefTurn:
    """Ход агента; ответ не по форме — один повтор с причиной, затем стоп."""
    at_cap = _questions(entries) >= MAX_QUESTIONS
    error = ""
    for _ in range(MAX_FORM_ERRORS):
        raw = agent(build_prompt(entries, subject, at_cap=at_cap, error=error))
        try:
            return parse_turn(raw, allow_ask=not at_cap)
        except FormError as exc:
            error = str(exc)
    say(FORM_STOP)
    raise _Stop


def build_prompt(
    entries: list[Entry], subject: str, *, at_cap: bool = False, error: str = ""
) -> str:
    """Промпт интервьюера: инструкция, тема, разговор и (если есть) пометки."""
    talk = "\n\n".join(_render_entry(e) for e in entries) or "(разговор ещё не начат)"
    parts = [
        PROMPT_PATH.read_text(encoding="utf-8"),
        f"## Тема проекта (слова человека)\n\n{subject}",
        f"## Разговор\n\n{talk}",
    ]
    if at_cap:
        parts.append(f'{CAP_NOTE}: вопросов больше не задавай, ответь {{"brief": …}}.')
    if error:
        parts.append(
            f"Прошлый ответ не по форме: {error}. "
            "Ответь ровно одним JSON-объектом по формату выше."
        )
    return "\n\n".join(parts)


def _render_entry(entry: Entry) -> str:
    role = entry.get("role")
    if role == "agent":
        speaker = _AGENT_SPEAKERS.get(str(entry.get("kind")), "Интервьюер")
        options = entry.get("options") or []
        lines = [str(entry["text"])]
        lines += [f"{n}. {o}" for n, o in enumerate(options, start=1)]
        return f"{speaker}: " + "\n".join(lines)
    return f"{_SPEAKERS.get(str(role), str(role))}: {entry['text']}"


def parse_turn(raw: str, *, allow_ask: bool = True) -> AskTurn | BriefTurn:
    """Ответ агента → `AskTurn` | `BriefTurn`; не по протоколу — `FormError`."""
    doc = first_json_object(raw)
    if "brief" in doc:
        data = doc["brief"]
        brief = brief_from_json(data)
        return BriefTurn(brief, data)
    if "ask" not in doc:
        raise FormError('ожидается ключ "ask" или "brief"')
    if not allow_ask:
        raise FormError('лимит вопросов исчерпан — ожидается {"brief": …}, не вопрос')
    return _ask_turn(doc)


def _ask_turn(doc: dict[str, Any]) -> AskTurn:
    question, options = doc.get("ask"), doc.get("options", [])
    if not isinstance(question, str) or not question.strip():
        raise FormError('поле "ask" должно быть непустой строкой')
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise FormError('поле "options" должно быть списком строк')
    return AskTurn(question.strip(), tuple(o.strip() for o in options if o.strip()))


def _settle_brief(turn: BriefTurn, log: _Log) -> Brief | None:
    """Бриф к сводке или `None` (проблемы отправлены агенту репликой `check`).

    На лимите вопросов или после `MAX_CHECKS_IN_ROW` проверок подряд бриф
    принимается: функции без критерия и оставшиеся проблемы — в «Уточнить»."""
    entries = log.entries
    forced = (
        _questions(entries) >= MAX_QUESTIONS
        or _checks_in_row(entries) >= MAX_CHECKS_IN_ROW
    )
    problems = brief_problems(turn.brief)
    if forced:
        demoted = demote_uncovered(turn.brief)
        rest = tuple(brief_problems(demoted))
        return replace(demoted, open_items=demoted.open_items + rest)
    if not problems:
        return turn.brief
    text = json.dumps(turn.data, ensure_ascii=False)
    log.append({"role": "agent", "kind": "brief", "text": text})
    log.append({"role": "check", "text": "\n".join(problems)})
    return None


def _questions(entries: list[Entry]) -> int:
    return sum(1 for e in entries if e.get("kind") == "ask")


def _checks_in_row(entries: list[Entry]) -> int:
    """Реплик `check` после последней реплики человека."""
    count = 0
    for entry in reversed(entries):
        if entry.get("role") == "human":
            break
        count += entry.get("role") == "check"
    return count


def _confirmed(brief: Brief, log: _Log, read: Read) -> bool:
    """Сводка и «верно?»: «1» — да; поправка записывается, `False`."""
    say(render_brief(brief))
    while True:
        say(CONFIRM)
        text = read_answer(read, quick=str.isdigit)
        if text is None or is_skip(text):
            raise _Stop
        if text == "1":
            return True
        fix = _correction(text, read)
        if fix:
            log.append({"role": "human", "text": f"поправить: {fix}"})
            return False


def _correction(text: str, read: Read) -> str:
    """Текст поправки; голое «2» — дочитать, что именно; иной номер — пусто."""
    if text.isdigit():
        if text != "2":
            return ""
        say(WHAT_TO_FIX)
        more = read_answer(read)
        if more is None:
            raise _Stop
        return more.strip()
    return _FIX_PREFIX.sub("", text, count=1).strip() or text.strip()
