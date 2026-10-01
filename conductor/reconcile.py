"""Поиск результата незавершённых попыток по идентичности (спека среза 1, §5.3).

До планирования: каждая попытка `in_flight`/`uncertain` ищется в GitHub по
сохранённой идентичности эффекта (маркер комментария, sha256 тела, состояние
закрытия, закрепление, найденная очередь). Нашлась — попытке дописывается
`ok`, и задержка её эффекта снимается. Не нашлась или чтение не удалось —
ничего не меняется (действует задержка). Найденный эффект НЕ даёт права
писать (I7): планировщики заново читают удалённые объекты, исполнитель
проходит все проверки §5.1.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from conductor.fresh import FreshReader
from conductor.gh_app import Blocked, JournalLost
from conductor.opstate import OpState


def _split(target: str) -> tuple[str, int | None]:
    repo, _, number = target.partition("#")
    return repo, int(number) if number else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def found(fresh: FreshReader, attempt: dict[str, Any], bot: str) -> bool | None:
    """Виден ли эффект попытки; None — чтение не удалось."""
    op = attempt.get("op")
    repo, number = _split(attempt["target"])
    if op == "create":
        return fresh.queue_found(repo, bot)
    if number is None:
        return None
    if op == "comment":
        return fresh.marker_found(repo, number, attempt.get("expected", ""), bot)
    if op == "pin":
        return fresh.pinned(repo, number)
    issue = fresh.issue(repo, number)
    if issue is None:
        return None
    if op == "close":
        closed = issue.get("state") == "closed"
        return closed and issue.get("state_reason") == "completed"
    if op == "body":
        return _sha(issue.get("body") or "") == attempt.get("expected")
    return None


def reconcile(
    state: OpState, fresh: FreshReader, bot: str, now: datetime
) -> list[dict[str, Any]]:
    """Сверить незавершённые попытки; вернуть заметки о найденных эффектах.

    Запрет по лимиту или потеря журнала установки прекращают поиск: без
    вызовов App состояние попыток не меняется.
    """
    notes: list[dict[str, Any]] = []
    for attempt in state.open_attempts():
        try:
            seen = found(fresh, attempt, bot)
        except (Blocked, JournalLost):
            break
        if seen:
            state.settle(attempt["effect_key"], now)
            notes.append({"settled": attempt["target"], "op": attempt.get("op")})
    return notes
