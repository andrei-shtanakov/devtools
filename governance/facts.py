"""Общие типы для фактов, где отсутствие нельзя путать со сбоем чтения."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class Outcome(Enum):
    """Четыре однозначных исхода чтения факта.

    `FOUND` — факт прочитан, значение есть; `ABSENT` — прочитано, что факта
    нет; `FORBIDDEN` — факт есть, но запрещает проверяемый переход;
    `UNAVAILABLE` — установить факт не удалось.
    """

    FOUND = "found"
    ABSENT = "absent"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"


#: Исходы, которые контракт считает положительно установленным фактом.
ESTABLISHED = (Outcome.FOUND, Outcome.ABSENT, Outcome.FORBIDDEN)


@dataclass(frozen=True)
class Fact(Generic[T]):
    """Исход чтения одного факта плюс значение и человеческий повод.

    `detail` — часть контракта наблюдаемости: вызывающий обязан назвать,
    какой именно факт не установлен, не приписывая неустановленную причину.
    """

    outcome: Outcome
    value: T | None = None
    detail: str = ""

    @property
    def established(self) -> bool:
        """Факт положительно установлен и пригоден для решения."""
        return self.outcome in ESTABLISHED


def unavailable(detail: str) -> Fact[T]:
    """Исход «установить не удалось»."""
    return Fact(Outcome.UNAVAILABLE, None, detail)
