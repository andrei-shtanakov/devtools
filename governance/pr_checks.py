"""Состояние обязательных проверок PR по `statusCheckRollup`.

Один вычислитель предиката на два читателя — `accept_pr` (ритуал приёмки) и
`approve_node` (ожидание перед агентским мержем finalize, devtools#277).
Второй экземпляр того же правила разошёлся бы с первым молча: у этих двух
путей одна и та же цена ошибки — мерж до старта CI.
"""

from __future__ import annotations

_PENDING = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", ""}
_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _states(pr_facts: dict) -> list[tuple[str, str]]:
    """`(имя, состояние)` по каждой записи rollup; состояние — в верхнем."""
    checks = pr_facts.get("statusCheckRollup") or []
    return [
        (
            str(c.get("name") or c.get("context") or "проверка"),
            (c.get("conclusion") or c.get("status") or "").upper(),
        )
        for c in checks
    ]


def checks_state(pr_facts: dict) -> str:
    """`green` | `red` | `pending` (fail-closed).

    Пустой rollup — `pending`, не green (приёмка PR #109, major): пустота
    двусмысленна — «чеков нет вовсе» неотличимо от «чеки ещё не создались»
    на свежем push, и green-чтение мержило бы до старта CI. Репо совсем без
    чеков упрётся в потолок опроса и уйдёт на человека — fail-closed.
    """
    states = _states(pr_facts)
    if not states:
        return "pending"
    if any(s in _PENDING for _, s in states):
        return "pending"
    if all(s in _GREEN for _, s in states):
        return "green"
    return "red"


def failing_names(pr_facts: dict) -> str:
    """Имена незелёных проверок через запятую — для текста отказа.

    Отказ обязан называть проверку поимённо: «проверки красные» отправляет
    читателя искать, какая именно, а имя ведёт прямо в её журнал.
    """
    names = [
        name for name, state in _states(pr_facts)
        if state not in _GREEN and state not in _PENDING
    ]
    return ", ".join(names) if names else "имя не установлено"
