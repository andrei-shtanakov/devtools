"""Самоотчёт прав credential на источниках доверия (спека §6.2 б).

Маркер-файлы части A выведены: регистрацию нарушений ведёт журнал событий
периметра установки (спека A rev 16 §6.3; `governance/act_journal.py`).
Здесь — только классификация самоотчёта: нарушение (`Violation`) и
неустановленный факт (`Unavailable`) — разные исходы §6.2 б / §6.3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from governance.act_time import Unavailable

_WRITE = ("push", "maintain", "admin")


@dataclass(frozen=True)
class Violation:
    """Установленное нарушение периметра: вызывающий регистрирует событие."""

    detail: str


def permissions_refusal(
    permissions: Mapping[str, object] | None,
) -> Violation | Unavailable | None:
    """Самоотчёт прав credential на источнике.

    Литеральное `True` у push/maintain/admin — установленное нарушение
    (`Violation`) независимо от прочих полей, в том числе недоступных.
    Иначе отсутствующее поле или не-bool значение — `Unavailable` (право не
    установлено ни как выданное, ни как отсутствующее; событие не пишется).
    Все три поля литерально `False` — `None`.
    """
    if permissions is None:
        return Unavailable("права credential на источнике не получены")
    granted = [k for k in _WRITE if permissions.get(k) is True]
    if granted:
        return Violation(
            f"у credential процесса есть {', '.join(granted)} на источнике политики"
        )
    unknown = [k for k in _WRITE if type(permissions.get(k)) is not bool]
    if unknown:
        return Unavailable(
            f"поле прав {', '.join(unknown)} отсутствует или не bool — не установлено"
        )
    return None
