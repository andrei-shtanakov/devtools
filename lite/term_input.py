"""Строка из терминала без краха на битом вводе (devtools#751, #834).

Backspace в терминале без посимвольного редактирования стирает байт, а не
символ: от кириллической буквы остаётся полсимвола, и строгий `input`
падает `UnicodeDecodeError` (или отдаёт одиночный surrogate при
surrogateescape-stdin). Здесь такая строка — подсказка и повтор, после
`MAX_BROKEN_READS` битых чтений подряд — `None`, как при EOF.
"""

from __future__ import annotations

from lite.i18n import t
from lite.output import say

BROKEN_INPUT = (
    "строка не прочитана: в ней битый символ (часто — Backspace стёр полбуквы); "
    "введите эту строку ещё раз"
)
#: попыток прочитать строку подряд, пока ввод битый (без лимита — вечный цикл,
#: если терминал отдаёт тот же битый хвост на каждое чтение)
MAX_BROKEN_READS = 3


def read_line(prompt: str, *, stop_message: str) -> str | None:
    """Строка из терминала; EOF (Ctrl-D) и Ctrl-C — `None` без трассировки.

    Битая строка — подсказка `BROKEN_INPUT` и повтор; после
    `MAX_BROKEN_READS` битых подряд — `stop_message` и `None`."""
    _enable_line_editing()
    for attempt in range(1, MAX_BROKEN_READS + 1):
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            say("")
            return None
        except UnicodeDecodeError:
            line = None
        if line is not None and not _has_broken_bytes(line):
            return line
        if attempt < MAX_BROKEN_READS:
            say(t(BROKEN_INPUT))
    say(stop_message)
    return None


def _has_broken_bytes(line: str) -> bool:
    """Одиночный surrogate — непрочитанный байт (stdin с surrogateescape)."""
    return any("\udc80" <= ch <= "\udcff" for ch in line)


def _enable_line_editing() -> None:
    """Импорт `readline` включает посимвольное редактирование в `input`."""
    try:
        import readline  # noqa: F401
    except ImportError:  # нет в сборке Python — ввод работает и без него
        pass
