"""Admission режима акта — до выбора читателя, для обоих путей (спека §10).

`mode_history` — активации источника, менявшие `ACT_MODE[P]`, в порядке
first-parent; первая из них — «активация режима P» (граница legacy-эпохи).
Равная секунда с доставкой решается через `Clock` (§8.2): вызывающий
перебирает порядки `act_time.resolve` над ВСЕМ решением (admission +
вердикт выбранного читателя), а не над `admit` в одиночку — иначе два
порядка с одним `Admitted`, но разными вердиктами читателя, сошлись бы
ложно (приёмка №27a).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from governance.act_request import ACT_REVIEW_V1
from governance.act_time import Clock, EventKey, Unavailable


@dataclass(frozen=True)
class ModeEvent:
    """Активация, установившая `ACT_MODE[P] = mode`."""

    key: EventKey
    at: datetime
    mode: str


@dataclass(frozen=True)
class Admitted:
    """Какой читатель проверяет доставку."""

    reader: str


@dataclass(frozen=True)
class Refused:
    """Отказ admission; причина — свидетельство, не часть вердикта (§8.2)."""

    reason: str = field(compare=False)


def admit(
    request_act: str | None, mode_history: Sequence[ModeEvent], clock: Clock
) -> Admitted | Refused | Unavailable:
    """Шаги 1–5 admission §10."""
    effective = [e for e in mode_history if clock.precedes(e.key, e.at)]
    if not effective:
        if request_act is None:
            return Admitted("legacy")
        return Refused("заявка review/v1 до первой записи ACT_MODE продукта")
    modes = [e.mode for e in effective]
    if modes[-1] not in ("merge", "review"):
        return Refused("ACT_MODE продукта отсутствует или неизвестен")
    if "review" in modes and modes[-1] != "review":
        return Unavailable("обратное переключение review → merge не поддерживается")
    if modes[-1] == "merge":
        if request_act is None:
            return Admitted("merge")
        return Refused("ACT_MODE=merge: допустима только заявка без маркера")
    if request_act == ACT_REVIEW_V1:
        return Admitted("review")
    return Refused(
        f"ACT_MODE=review: заявка {request_act or 'без маркера'} — нужна новая"
    )
