"""Интервью разговором в терминале: вопрос → ответ → (уточнения) → отправка.

Текущий вопрос — у `discovery status` сессии (код 20 → `next_action`, иначе
интервью кончилось). Ответ человека раскладывает `lite.structure`
(агент + проверка скриптом); уточнений — не больше `MAX_FOLLOW_UPS` на вопрос.
Ответ уходит `discovery answer --question <id>` (функция `send`). Отказ
`discovery` — один повтор раскладки с причиной в промпте агента, затем
человеку — простое сообщение и тот же вопрос заново. Человек не видит ни
JSON, ни YAML, ни id пунктов.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

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
    structure_answer,
)

Read = Callable[[str], str | None]
Status = Callable[[str], iv.DiscoveryReply]
Send = Callable[[str, str], iv.DiscoveryReply]
Load = Callable[[str], SessionView]

MAX_FOLLOW_UPS = 2
PROMPT = "> "
STOP_WORD = "/стоп"
SKIP_WORDS = frozenset({"пропустить", "не знаю"})
REFUSED_TEXT = "ответ не принят — ответьте на вопрос ещё раз"


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
    load: Load,
) -> ChatResult:
    """Вести интервью прогона `state`, пока у `discovery` есть вопрос.

    `read` — строка ввода (`None` — EOF), `status`/`load` — по `session_id`,
    `send(question_id, yaml)` — ответ на вопрос. Агент недоступен —
    `ChatError`."""
    session = str((state.interview or {}).get("session_id"))
    while True:
        q = current_question(status(session))
        if q is None:
            return ChatResult.FINISHED
        try:
            _answer_question(q, load(session), read, agent, send)
        except _Stop:
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
    skip, follow_ups, refusal = is_skip(text), 0, ""
    while True:
        may_ask = not skip and follow_ups < MAX_FOLLOW_UPS
        result = structure_answer(
            q, text, view, _with_note(agent, refusal), may_ask=may_ask
        )
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
        reply = send(q.id, result.payload)
        if reply.code not in (1, 2):
            return
        if refusal:
            print(REFUSED_TEXT)
            return
        refusal = reply.envelope.get("operation", {}).get("reason", "") or "отказ"


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
