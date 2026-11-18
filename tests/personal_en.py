"""Тестовые утилиты английского режима личного пути `dfl`."""

from __future__ import annotations

import re
from collections.abc import Iterable

_CYRILLIC = re.compile("[А-Яа-яЁё]")


def cyrillic(text: str, allowed: Iterable[str] = ()) -> list[str]:
    """Строки `text` с кириллицей после вычёркивания `allowed`."""
    for fragment in allowed:
        text = text.replace(fragment, "")
    return [line for line in text.splitlines() if _CYRILLIC.search(line)]
