"""Интервью разговором в терминале: вопрос → ответ → (уточнения) → отправка.

Текущий вопрос — у `discovery status` сессии (код 20 → `next_action`, иначе
интервью кончилось). Ответ человека раскладывает `lite.structure`
(агент + проверка скриптом); уточнений — не больше `MAX_FOLLOW_UPS` на вопрос.
Ответ уходит `discovery answer --question <id>` (функция `send`). Кончилось
интервью неполным (код 10/11, обязательные темы не покрыты) — один раз
добор: по каждой непокрытой теме вопрос человеку, раскладка и дописывание
к ответу держателя темы (`resend` — `--supersede`). Отказ
`discovery` — один повтор раскладки с причиной в промпте агента, затем
человеку — простое сообщение и тот же вопрос заново. Человек не видит ни
JSON, ни YAML, ни id пунктов.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any

import yaml

from governance import interview as iv
from governance import run_state as rs
from lite.interview_state import SessionView
from lite.structure import (
    FAILED_TEXT,
    TOPIC_LABELS,
    Agent,
    Ask,
    Failed,
    Question,
    Ready,
    structure_answer,
    to_payload,
)

Read = Callable[[str], str | None]
Deliver = Callable[[str], iv.DiscoveryReply]
Status = Callable[[str], iv.DiscoveryReply]
Send = Callable[[str, str], iv.DiscoveryReply]
Load = Callable[[str], SessionView]

MAX_FOLLOW_UPS = 2
PROMPT = "> "
STOP_WORD = "/стоп"
SKIP_WORDS = frozenset({"пропустить", "не знаю"})
REFUSED_TEXT = "ответ не принят — ответьте на вопрос ещё раз"
TOP_UP_HINT = "расскажите об этом своими словами («пропустить» — оставить так)"
INCOMPLETE_CODES = (10, 11)


class ChatResult(enum.Enum):
    """Итог цикла: вопросы кончились или человек вышел."""

    FINISHED = "finished"
    STOPPED = "stopped"


class ChatError(Exception):
    """Цикл не продолжить (агент недоступен); текст — для человека."""


class _Stop(Exception):
    """Человек вышел (EOF или `/стоп`)."""


def chat(
    state: rs.RunState,
    *,
    read: Read,
    agent: Agent,
    status: Status,
    send: Send,
    resend: Send,
    load: Load,
) -> ChatResult:
    """Вести интервью прогона `state`, пока у `discovery` есть вопрос.

    `read` — строка ввода (`None` — EOF), `status`/`load` — по `session_id`,
    `send(question_id, yaml)` — ответ на вопрос, `resend` — замена ответа
    (`--supersede`, для добора тем). Агент недоступен — `ChatError`. Ctrl-C
    во время работы агента или `discovery` — выход (`STOPPED`), как и на
    вопросе: отправленное уже записано."""
    session = str((state.interview or {}).get("session_id"))
    topped_up = False
    try:
        while True:
            reply = status(session)
            q = current_question(reply)
            if q is not None:
                _answer_question(q, load(session), read, agent, send)
                continue
            if topped_up or reply.code not in INCOMPLETE_CODES:
                return ChatResult.FINISHED
            topped_up = True
            _top_up(session, read, agent, resend, load)
    except _Stop:
        return ChatResult.STOPPED
    except KeyboardInterrupt:
        print()
        return ChatResult.STOPPED


def current_question(reply: iv.DiscoveryReply) -> Question | None:
    """Код 20 → вопрос из `next_action`; любой другой — вопросов нет."""
    if reply.code != 20:
        return None
    action = reply.envelope["next_action"]
    return Question(
        str(action["question_id"]),
        str(action.get("coverage_key", "")),
        str(action.get("question_text", "")),
    )


def _answer_question(
    q: Question, view: SessionView, read: Read, agent: Agent, send: Send
) -> None:
    """Один вопрос: ответ, уточнения, отправка. Неудача — молча вернуться:
    цикл снова спросит `status` и покажет тот же вопрос."""
    label = TOPIC_LABELS.get(q.coverage_key, q.coverage_key)
    text = _ask_human(f"Вопрос {len(view.answers) + 1} ({label}): {q.text}", read)
    _settle(q, text, view, read, agent, lambda payload: send(q.id, payload))


def _top_up(session: str, read: Read, agent: Agent, resend: Send, load: Load) -> None:
    """Добор непокрытых обязательных тем после неполного конца интервью.

    Темы без префикса пропускаются: их закрывает только ответ на вопрос
    самой темы, дописывание к чужому ответу их не покроет."""
    view = load(session)
    if view.readiness != "incomplete":
        return
    for key in view.uncovered_required():
        if view.required[key] is None:
            continue
        view = load(session)
        if key in view.covered():
            continue
        _top_up_topic(key, view, read, agent, resend)


def _top_up_topic(
    key: str, view: SessionView, read: Read, agent: Agent, resend: Send
) -> None:
    """Тема `key`: вопрос человеку; ответ дописывается к ответу держателя —
    последнего отвеченного вопроса темы, иначе последнего отвеченного."""
    qid = _holder_question(view, key)
    if qid is None:
        return
    label = TOPIC_LABELS.get(key, key)
    q = Question(qid, key, f"Расскажите про тему «{label}».")
    text = _ask_human(f"Не хватает: {label} — {TOP_UP_HINT}", read)
    if is_skip(text):
        return
    held = view.held_answer(qid)
    _settle(
        q,
        text,
        view,
        read,
        agent,
        lambda payload: resend(qid, _appended(held, payload)),
    )


def _holder_question(view: SessionView, key: str) -> str | None:
    """Последний отвеченный вопрос темы `key`, иначе последний отвеченный."""
    answered = list(view.answers)
    of_topic = [qid for qid in answered if view.asked.get(qid) == key]
    return (of_topic or answered or [None])[-1]


def _appended(held: dict[str, Any], payload: str) -> str:
    """Ответ держателя + новый: текст через пустую строку, записи — прежние
    (с заменой по id) и новые."""
    new = yaml.safe_load(payload)
    out = dict(held)
    out["text"] = "\n\n".join(t for t in (held.get("text"), new["text"]) if t)
    fresh = {e["id"]: e for e in new.get("entries") or []}
    entries = [fresh.pop(e.get("id"), e) for e in held.get("entries") or []]
    entries += fresh.values()
    out.pop("entries", None)
    if entries:
        out["entries"] = entries
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


def _settle(
    q: Question,
    text: str,
    view: SessionView,
    read: Read,
    agent: Agent,
    deliver: Deliver,
) -> None:
    """Раскладка ответа `text`, уточнения (не больше `MAX_FOLLOW_UPS`) и
    отправка `deliver(yaml)`; отказ `discovery` — один повтор раскладки."""
    skip, follow_ups, refusal = is_skip(text), 0, ""
    while True:
        may_ask = not skip and follow_ups < MAX_FOLLOW_UPS
        result = _structured(q, text, view, _with_note(agent, refusal), may_ask)
        if isinstance(result, Failed):
            if result.message != FAILED_TEXT:
                raise ChatError(result.message)
            print(result.message)
            return
        if isinstance(result, Ask):
            follow_ups += 1
            extra = _ask_human(f"Уточнение: {result.question}", read)
            if is_skip(extra):
                skip = True
            else:
                text = f"{text}\n\nУточнение: {result.question}\n{extra}"
            continue
        reply = deliver(result.payload)
        if reply.code not in (1, 2):
            return
        if refusal:
            print(REFUSED_TEXT)
            return
        refusal = reply.envelope.get("operation", {}).get("reason", "") or "отказ"


def _structured(
    q: Question, text: str, view: SessionView, agent: Agent, may_ask: bool
) -> Ready | Ask | Failed:
    """Раскладка ответа; голый пропуск (другого текста нет) — только `text`,
    без агента: раскладывать нечего, а агенту незачем что-то додумывать."""
    if is_skip(text):
        return Ready(to_payload(text, [], view))
    return structure_answer(q, text, view, agent, may_ask=may_ask)


def _with_note(agent: Agent, refusal: str) -> Agent:
    """Агент с причиной отказа `discovery` в конце промпта (если она есть)."""
    if not refusal:
        return agent
    note = (
        f"\n\nПрошлый собранный ответ не принят discovery: {refusal}. "
        "Исправь пункты с учётом этой причины."
    )
    return lambda prompt: agent(prompt + note)


def _ask_human(header: str, read: Read) -> str:
    """Показать вопрос и читать ответ; пустой — показать снова."""
    while True:
        print(header)
        text = read_answer(read)
        if text is None:
            raise _Stop
        if text:
            return text


def read_answer(read: Read) -> str | None:
    """Ответ — строки до пустой; `None` — EOF или `/стоп`."""
    lines: list[str] = []
    while True:
        line = read(PROMPT)
        if line is None or line.strip() == STOP_WORD:
            return None
        if not line.strip():
            return "\n".join(lines).strip()
        lines.append(line)


def is_skip(text: str) -> bool:
    """Ответ — «пропустить» или «не знаю» (регистр и пробелы не важны)."""
    return text.strip().casefold() in SKIP_WORDS


def stdin_read(prompt: str) -> str | None:
    """Строка из терминала; EOF (Ctrl-D) и Ctrl-C — `None` (выход из цикла,
    без трассировки); строка приглашения закрывается переводом строки."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None
