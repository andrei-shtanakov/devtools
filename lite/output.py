"""Вывод `dfl`: отказ — отдельным блоком, а не строкой под баннером."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import TextIO

MAX_VERBOSITY = 3
VERBOSITY_ENV = "DFL_VERBOSITY"

_verbosity = 0
_sink: TextIO | None = None


def set_verbosity(level: int) -> None:
    """Уровень подробности, ограниченный до 0..3."""
    global _verbosity
    _verbosity = max(0, min(level, MAX_VERBOSITY))


def verbosity() -> int:
    """Текущий уровень подробности (0..3)."""
    return _verbosity


def set_sink(sink: TextIO | None) -> None:
    """Текущий лог: туда идёт всё, что печатает `say`; `None` — без лога."""
    global _sink
    _sink = sink


def say(text: str, level: int = 0, *, err: bool = False) -> None:
    """Печатает строку, если уровень подробности не ниже `level`.

    В лог (если задан) строка попадает всегда, после сброса stdout/stderr —
    чтобы порядок строк в логе совпадал с порядком на экране.
    """
    if _verbosity >= level:
        print(text, file=sys.stderr if err else sys.stdout)
    if _sink is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    _sink.write(text + "\n")
    _sink.flush()


def verbosity_from_env(env: Mapping[str, str]) -> tuple[int, str | None]:
    """Уровень из `DFL_VERBOSITY` и предупреждение (или `None`).

    Пусто или нет — 0; целое ≥ 0 — оно же, не выше 3; иное — 0 и предупреждение.
    """
    raw = env.get(VERBOSITY_ENV, "")
    if not raw:
        return 0, None
    try:
        level = int(raw)
    except ValueError:
        level = -1
    if level < 0:
        warning = f"{VERBOSITY_ENV}={raw!r} не целое число ≥ 0 — использую уровень 0"
        return 0, warning
    return min(level, MAX_VERBOSITY), None


_SEP = " — "
_INDENT = "               "  # ширина «  что сделать: »


def refusal(command: str, message: str, code: int = 2) -> str:
    """Блок отказа: заголовок с командой и кодом, «причина», «что сделать».

    Сообщения отказов пишутся как «причина — что сделать»: делим по первому
    « — » вне кавычек — подставленные данные (тема `{subject!r}`, путь) сами
    могут нести тире. Нет разделителя — только причина. Перенос строки в
    сообщении продолжается с отступом, чтобы блок не рассыпался.
    """
    cause, sep, action = split_refusal(message)
    lines = ["", f"{command}: отказ (код {code})", _field("причина:", cause)]
    if sep:
        lines.append(_field("что сделать:", action))
    return "\n".join(lines)


def split_refusal(message: str) -> tuple[str, str, str]:
    """(причина, разделитель, что сделать) — по первому « — » вне кавычек."""
    quote = ""
    for i, ch in enumerate(message):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif message.startswith(_SEP, i):
            return message[:i], _SEP, message[i + len(_SEP) :]
    return message, "", ""


def _field(label: str, text: str) -> str:
    body = text.strip().replace("\n", "\n" + _INDENT)
    return f"  {label:<13}{body}"
