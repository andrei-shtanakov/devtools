"""Единое правило временной неоднозначности (спека §8.2).

Метки форджа — с точностью до секунды; события с равной меткой не
упорядочены. Каждое сравнение «до T?» во всех проверках акта идёт через
`Clock.precedes`: событие с меткой T считается предшествующим доставке
ровно тогда, когда его ключ входит в рассматриваемое множество `before`.
`resolve` перебирает все допустимые множества и принимает исход, только
если он один.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations

EventKey = tuple[str, str]
MAX_GROUP = 8
AMBIGUOUS = "ambiguous_event_order"


def parse_ts(value: str) -> datetime:
    """Метка форджа → aware UTC с отброшенными долями секунды."""
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"метка без часового пояса: {value!r}")
    return stamp.astimezone(UTC).replace(microsecond=0)


def _require_normalised(stamp: datetime, what: str) -> None:
    """Метка обязана быть результатом `parse_ts`: aware и без долей секунды.

    Не нормализуем молча: ненормализованная метка — ошибка адаптера, иначе
    событие той же секунды тихо выпало бы из группы §8.2.
    """
    if stamp.tzinfo is None or stamp.microsecond != 0:
        raise ValueError(f"{what}: метка не нормализована parse_ts: {stamp!r}")


@dataclass(frozen=True)
class Ambiguous:
    """Вердикты при допустимых порядках различаются → `unavailable`."""

    reason: str = AMBIGUOUS


@dataclass(frozen=True)
class Unavailable:
    """Факт не установлен: решение не принимается, заявка жива."""

    reason: str


@dataclass(frozen=True)
class Clock:
    """Момент T и выбранное множество событий группы, стоящих до доставки."""

    t: datetime
    before: frozenset[EventKey] = frozenset()

    def __post_init__(self) -> None:
        _require_normalised(self.t, "Clock.t")

    def precedes(self, key: EventKey, stamp: datetime) -> bool:
        """Событие `key` с меткой `stamp` предшествует доставке.

        Ненормализованная метка (naive или с долями секунды) — `ValueError`.
        """
        _require_normalised(stamp, f"событие {key}")
        return stamp < self.t or (stamp == self.t and key in self.before)


def before_sets(
    group: Sequence[EventKey], requires: Mapping[EventKey, frozenset[EventKey]]
) -> list[frozenset[EventKey]]:
    """Все множества событий группы, замкнутые вниз по зависимостям группы.

    Зависимость от события вне группы выполнена: оно упорядочено своей
    меткой. Порядок результата — по возрастанию размера; последнее —
    полное множество.
    """
    members = frozenset(group)
    found: list[frozenset[EventKey]] = []
    for size in range(len(group) + 1):
        for combo in combinations(group, size):
            chosen = frozenset(combo)
            if all(requires.get(e, frozenset()) & members <= chosen for e in chosen):
                found.append(chosen)
    return found


def resolve[R: Hashable](
    t: datetime,
    group: Sequence[EventKey],
    requires: Mapping[EventKey, frozenset[EventKey]],
    decide: Callable[[Clock], R],
) -> R | Ambiguous:
    """Исход по §8.2: единственный вердикт либо `Ambiguous`.

    Возвращается исход полного множества — свидетельство одного
    допустимого порядка; равенство исходов игнорирует свидетельства.
    """
    if len(group) + 1 > MAX_GROUP:
        return Ambiguous()
    outcomes = [decide(Clock(t, before)) for before in before_sets(group, requires)]
    if len(set(outcomes)) != 1:
        return Ambiguous()
    return outcomes[-1]
