"""Основание close_shipped и период открытия (спека среза 1, §7.5).

Период — от последнего reopened (любого автора, после любого закрытия) или
от создания. (а) — PR, влитый в ветку по умолчанию ПОСЛЕ начала периода.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conductor.inputs import Inputs
from conductor.opstate import parse_ts
from conductor.rank import rank_of
from conductor.snapshot import Result


@dataclass(frozen=True)
class Shipped:
    """Состояние основания закрытия issue."""

    subject: str
    period: str
    basis_a: dict[str, Any] | None
    old_basis: bool


def shipped_state(inputs: Inputs, subject: str) -> Shipped | None:
    """Основание для открытого issue; extras нет — None (неизвестно)."""
    ext = inputs.issue_extras.get(subject)
    if ext is None:
        return None
    start = parse_ts(ext["period_start"])
    merged = [
        p
        for p in ext["closed_by"]
        if p["merged"] and p["base_is_default"] and p["merged_at"]
    ]
    current = [p for p in merged if parse_ts(p["merged_at"]) >= start]
    old = [p for p in merged if parse_ts(p["merged_at"]) < start]
    return Shipped(
        subject,
        ext["period"],
        current[0] if current else None,
        bool(old) and not current and ext["period"] != "0",
    )


def reopened_with_old_basis(result: Result, inputs: Inputs) -> list[Shipped]:
    """Переоткрытые issues с рангом, у которых (а) осталось в прежнем периоде."""
    out = []
    for subject in sorted(inputs.issue_extras):
        node = result.graph.nodes.get(subject)
        if node is None or not node.is_open:
            continue
        if rank_of(result.graph, result.roadmap, subject) is None:
            continue
        state = shipped_state(inputs, subject)
        if state is not None and state.old_basis:
            out.append(state)
    return out
