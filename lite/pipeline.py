"""Когда конвейер режима test закончен — одно определение для `dfl` и install.

Engineer-прогон в `test` доходит до `completed` раньше, чем закончится
цепочка после прогона (tasks-merge, spec-runner, code-accept): её стопы
статус не меняют. Поэтому «закончен» для такого прогона — завершённый
`code-accept` с URL проверенного PR, а не статус (Ruling 7).
"""

from __future__ import annotations

from collections.abc import Mapping

CHAIN_STOPPED_HINT = (
    "конвейер после прогона остановился — причина в выводе dfl run; повторите dfl run"
)


def accepted_url(ops: Mapping[str, object]) -> str | None:
    """URL проверенного PR с кодом, если `code-accept` завершён с URL."""
    record = ops.get("code-accept")
    if not isinstance(record, Mapping) or record.get("status") != "completed":
        return None
    result = record.get("result")
    url = result.get("url") if isinstance(result, Mapping) else None
    return str(url) if url else None


def merged_unreviewed(ops: Mapping[str, object]) -> bool:
    """`code-accept` закрыт мержем человека без автоматической приёмки."""
    record = ops.get("code-accept")
    result = record.get("result") if isinstance(record, Mapping) else None
    return isinstance(result, Mapping) and result.get("reviewed") is False


def chain_open(
    mode: object, frame: object, status: object, ops: Mapping[str, object]
) -> bool:
    """Test-engineer прогон `completed`, но цепочка после него не дошла до конца."""
    return (
        mode == "test"
        and frame == "engineer"
        and status == "completed"
        and accepted_url(ops) is None
    )
