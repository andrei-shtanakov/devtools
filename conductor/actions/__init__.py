"""Планировщики действий среза 1 (часть B). Часть A: план пуст."""

from __future__ import annotations

from dataclasses import dataclass

from conductor.host_config import HostConfig
from conductor.inputs import Inputs
from conductor.opstate import OpState
from conductor.snapshot import Result
from conductor.writer import PlanRecord


@dataclass(frozen=True)
class PlanContext:
    """Что планировщикам нужно сверх результата ядра."""

    cfg: HostConfig
    umbrella: str
    bot_login: str
    state: OpState


def plan_records(result: Result, inputs: Inputs, ctx: PlanContext) -> list[PlanRecord]:
    """Записи плана прогона (часть B наполняет)."""
    return []
