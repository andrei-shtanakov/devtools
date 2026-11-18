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

import enum
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from lite import i18n
from lite.interview_chat import Read, read_answer
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
PROMPT_PATHS = {"ru": PROMPT_PATH, "en": PROMPT_PATH.parent / "en" / PROMPT_PATH.name}
CAP_NOTE = "СЕЙЧАС: лимит вопросов исчерпан"
CAP_NOTES = {"ru": CAP_NOTE, "en": "NOW: the question limit is reached"}
CONFIRM = "Верно? 1. да · 2. поправить: <что>"
#: префикс правки в журнале интервью по языку прогона
FIX_PREFIXES = {"ru": "поправить: ", "en": "fix: "}
WHAT_TO_FIX = "Что поправить?"
#: на «Верно?» словом «да» тоже подтверждают; «не знаю» — поправка, не стоп
YES_WORDS = frozenset({"да", "верно", "ок", "yes", "ok"})
SKIP_WORDS = frozenset({"пропустить", "skip"})
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
    r"^\s*(?:2\s*[.):](?!\d)\s*)?(?:(?:поправить|fix)\s*:?\s*)?", re.IGNORECASE
)
_SPEAKERS = {
    "ru": {"human": "Человек", "check": "Проверка брифа (человек её не видел)"},
    "en": {"human": "Person", "check": "Brief check (the person has not seen it)"},
}
_AGENT_SPEAKERS = {
    "ru": {
        "": "Интервьюер",
        "ask": "Интервьюер",
        "brief": "Интервьюер (бриф)",
        "summary": "Интервьюер (сводка человеку)",
    },
    "en": {
        "": "Interviewer",
        "ask": "Interviewer",
        "brief": "Interviewer (brief)",
        "summary": "Interviewer (summary to the person)",
    },
}
#: Прочие тексты промпта интервьюера по языку прогона.
_PROMPT_TEXTS = {
    "ru": {
        "subject": "## Тема проекта (слова человека)\n\n{subject}",
        "talk": "## Разговор\n\n{talk}",
        "empty": "(разговор ещё не начат)",
        "cap": '{note}: вопросов больше не задавай, ответь {{"brief": …}}.',
        "error": (
            "Прошлый ответ не по форме: {error}. "
            "Ответь ровно одним JSON-объектом по формату выше."
        ),
    },
    "en": {
        "subject": "## Project topic (the person's words)\n\n{subject}",
        "talk": "## Conversation\n\n{talk}",
        "empty": "(the conversation has not started yet)",
        "cap": '{note}: ask no more questions, answer {{"brief": …}}.',
        "error": (
            "The previous answer was not in the format: {error}. "
            "Answer with exactly one JSON object in the format above."
        ),
    },
}

#: ошибки протокола интервьюера по языку прогона (уходят агенту в повтор)
_FORM_ERRORS = {
    "ru": {
        "json": "в ответе нет JSON-объекта",
        "key": 'ожидается ключ "ask" или "brief"',
        "cap": 'лимит вопросов исчерпан — ожидается {"brief": …}, не вопрос',
        "ask": 'поле "ask" должно быть непустой строкой',
        "options": 'поле "options" должно быть списком строк',
    },
    "en": {
        "json": "the answer has no JSON object",
        "key": 'expected the key "ask" or "brief"',
        "cap": 'the question limit is reached — expected {"brief": …}, not a question',
        "ask": 'the field "ask" must be a non-empty string',
        "options": 'the field "options" must be a list of strings',
    },
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


class Halt(enum.Enum):
    """Почему интервью не дало бриф."""

    #: человек прервал (`/стоп`, EOF, Ctrl-C, «пропустить») — пауза, не сбой
    PAUSED = "paused"
    #: агент дважды не по форме или недоступен — сбой
    FAILED = "failed"


class _Stop(Exception):
    """Человек остановил разговор."""


class _Failed(Exception):
    """Агент не смог вести разговор; сообщение уже выведено."""


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
    say(i18n.t(CORRUPT_LINE))
    return None


def interview(
    run_dir: Path, subject: str, *, read: Read, agent: Agent, lang: str = "ru"
) -> Brief | Halt:
    """Вести свободное интервью; подтверждённый человеком бриф или `Halt`.

    `Halt.PAUSED` — человек остановился (`/стоп`, EOF, Ctrl-C, «пропустить»
    на подтверждении); `Halt.FAILED` — агент дважды подряд ответил не по
    форме или недоступен (сообщение уже выведено). Разговор хранится в `run_dir / TRANSCRIPT` и продолжается с него.
    Число платных вызовов агента печатается один раз в конце. `lang` — язык
    прогона: промпт агента, проверки брифа и сводка."""
    calls = 0

    def counted(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return agent(prompt)

    try:
        return _converse(_Log(run_dir / TRANSCRIPT), subject, read, counted, lang)
    except _Stop:
        return Halt.PAUSED
    except _Failed:
        return Halt.FAILED
    except AgentUnavailable as exc:
        say(i18n.t(UNAVAILABLE_STOP).format(reason=exc))
        return Halt.FAILED
    except KeyboardInterrupt:
        say("")
        return Halt.PAUSED
    finally:
        say(i18n.t(PAID_CALLS).format(n=calls))


def append_correction(run_dir: Path, text: str, *, lang: str = "ru") -> None:
    """Правка владельца к подтверждённому брифу — реплика человека
    «поправить: <text>» в журнал интервью (следующий `interview` пересоберёт
    бриф). Та же правка уже есть после последней сводки — не дублируется:
    продолжение прерванного переинтервью идёт с открытого вопроса."""
    log = _Log(run_dir / TRANSCRIPT)
    fix = f"{FIX_PREFIXES[lang]}{text}"
    summaries = [i for i, e in enumerate(log.entries) if e.get("kind") == "summary"]
    tail = log.entries[summaries[-1] + 1 :] if summaries else log.entries
    if any(e.get("role") == "human" and e.get("text") == fix for e in tail):
        return
    log.append({"role": "human", "text": fix})


def _converse(
    log: _Log, subject: str, read: Read, agent: Agent, lang: str = "ru"
) -> Brief:
    """Цикл разговора до подтверждённого брифа (остановка — `_Stop`)."""
    while True:
        pending = _pending_ask(log.entries)
        if pending is not None:
            _answer(pending, log, read)
            continue
        turn = _next_turn(log.entries, subject, agent, lang)
        if isinstance(turn, AskTurn):
            log.append(_ask_entry(turn))
            continue
        brief = _settle_brief(turn, log, lang)
        if brief is None:
            continue
        if not brief.goals or not brief.functions:
            _nothing_to_build(turn, log, read)
            continue
        summary = render_brief(brief, lang=lang)
        log.append({"role": "agent", "kind": "summary", "text": summary})
        if _confirmed(summary, log, read):
            return brief


def _nothing_to_build(turn: BriefTurn, log: _Log, read: Read) -> None:
    """Бриф без целей или функций не на подтверждение: спросить человека, что
    должно работать; бриф и ответ уходят в разговор (агент их увидит)."""
    while True:
        say(i18n.t(NOTHING_TO_BUILD))
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


def _next_turn(
    entries: list[Entry], subject: str, agent: Agent, lang: str = "ru"
) -> AskTurn | BriefTurn:
    """Ход агента; ответ не по форме — один повтор с причиной, затем стоп."""
    at_cap = _questions(entries) >= MAX_QUESTIONS
    error = ""
    for _ in range(MAX_FORM_ERRORS):
        prompt = build_prompt(entries, subject, at_cap=at_cap, error=error, lang=lang)
        raw = agent(prompt)
        try:
            return parse_turn(raw, allow_ask=not at_cap, lang=lang)
        except FormError as exc:
            error = str(exc)
    say(i18n.t(FORM_STOP))
    raise _Failed


def build_prompt(
    entries: list[Entry],
    subject: str,
    *,
    at_cap: bool = False,
    error: str = "",
    lang: str = "ru",
) -> str:
    """Промпт интервьюера: инструкция, тема, разговор и (если есть) пометки.

    `lang` — язык прогона: инструкция и все добавляемые кодом тексты."""
    texts = _PROMPT_TEXTS[lang]
    talk = "\n\n".join(_render_entry(e, lang) for e in entries) or texts["empty"]
    parts = [
        PROMPT_PATHS[lang].read_text(encoding="utf-8"),
        texts["subject"].format(subject=subject),
        texts["talk"].format(talk=talk),
    ]
    if at_cap:
        parts.append(texts["cap"].format(note=CAP_NOTES[lang]))
    if error:
        parts.append(texts["error"].format(error=error))
    return "\n\n".join(parts)


def _render_entry(entry: Entry, lang: str = "ru") -> str:
    role = entry.get("role")
    if role == "agent":
        speakers = _AGENT_SPEAKERS[lang]
        speaker = speakers.get(str(entry.get("kind")), speakers[""])
        options = entry.get("options") or []
        lines = [str(entry["text"])]
        lines += [f"{n}. {o}" for n, o in enumerate(options, start=1)]
        return f"{speaker}: " + "\n".join(lines)
    return f"{_SPEAKERS[lang].get(str(role), str(role))}: {entry['text']}"


def parse_turn(
    raw: str, *, allow_ask: bool = True, lang: str = "ru"
) -> AskTurn | BriefTurn:
    """Ответ агента → `AskTurn` | `BriefTurn`; не по протоколу — `FormError`
    (сообщение — на языке `lang`: оно уходит агенту в повтор)."""
    errors = _FORM_ERRORS[lang]
    try:
        doc = first_json_object(raw)
    except FormError:
        raise FormError(errors["json"]) from None
    if "brief" in doc:
        data = doc["brief"]
        brief = brief_from_json(data, lang=lang)
        return BriefTurn(brief, data)
    if "ask" not in doc:
        raise FormError(errors["key"])
    if not allow_ask:
        raise FormError(errors["cap"])
    return _ask_turn(doc, errors)


def _ask_turn(doc: dict[str, Any], errors: dict[str, str]) -> AskTurn:
    question, options = doc.get("ask"), doc.get("options", [])
    if not isinstance(question, str) or not question.strip():
        raise FormError(errors["ask"])
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise FormError(errors["options"])
    return AskTurn(question.strip(), tuple(o.strip() for o in options if o.strip()))


def _settle_brief(turn: BriefTurn, log: _Log, lang: str = "ru") -> Brief | None:
    """Бриф к сводке или `None` (проблемы отправлены агенту репликой `check`).

    На лимите вопросов или после `MAX_CHECKS_IN_ROW` проверок подряд бриф
    принимается: функции без критерия и оставшиеся проблемы — в «Уточнить»."""
    entries = log.entries
    forced = (
        _questions(entries) >= MAX_QUESTIONS
        or _checks_in_row(entries) >= MAX_CHECKS_IN_ROW
    )
    problems = brief_problems(turn.brief, lang=lang)
    if forced:
        demoted = demote_uncovered(turn.brief, lang=lang)
        rest = tuple(brief_problems(demoted, lang=lang))
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


def _confirmed(summary: str, log: _Log, read: Read) -> bool:
    """Сводка `summary` и «верно?»: «1»/«да»/«верно»/«ок» — да; «пропустить»,
    `/стоп`, EOF — стоп; иное (и «не знаю») — поправка записывается, `False`."""
    say(summary)
    while True:
        say(i18n.t(CONFIRM))
        text = read_answer(read, quick=str.isdigit)
        word = (text or "").strip().casefold()
        if text is None or word in SKIP_WORDS:
            raise _Stop
        if text == "1" or word in YES_WORDS:
            return True
        fix = _correction(text, read)
        if fix:
            log.append({"role": "human", "text": FIX_PREFIXES[i18n.current()] + fix})
            return False


def _correction(text: str, read: Read) -> str:
    """Текст поправки; голое «2» — дочитать, что именно; иной номер — пусто."""
    if text.isdigit():
        if text != "2":
            return ""
        say(i18n.t(WHAT_TO_FIX))
        more = read_answer(read)
        if more is None:
            raise _Stop
        return more.strip()
    return _FIX_PREFIX.sub("", text, count=1).strip() or text.strip()
