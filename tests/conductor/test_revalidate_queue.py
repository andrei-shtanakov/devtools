"""owner_queue: свежая сверка очереди и ответов (§5.1 шаг 8, §6.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.owner_queue import plan_owner_queue
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, world
from tests.conductor.slice1_world import (
    UMB,
    World,
    outcomes,
)

QQ = {
    "question_id": "11111111",
    "subject": "a#1",
    "reason": "cancelled",
    "evidence": "e",
    "question": "a#1: предпосылка отменена?",
    "options": ["drop-wait", "keep"],
    "default": "keep",
}


def queue_world(tmp: Path, exists: bool) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    queue = []
    if exists:
        w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT, body="старое")
        queue = [
            record(
                "ai-orchestrators-workspace",
                9,
                body="старое",
                author=BOT,
                labels=["owner-queue"],
                comments=[],
                pinned=True,
                repo_full=UMB,
            )
        ]
    _, inp = world(queue_records=queue)
    notes: list[dict[str, Any]] = []
    recs = plan_owner_queue([QQ], [], inp, UMB, "own", BOT, w.fresh, notes)
    return w, recs


@pytest.mark.parametrize("exists", [True, False])
def test_queue_twin_writes(tmp_path: Path, exists: bool) -> None:
    w, recs = queue_world(tmp_path, exists)
    reports = w.run(recs, "owner_queue")
    assert {r.outcome for r in reports} == {"success"}


def test_queue_removed_when_owner_answers_change(tmp_path: Path) -> None:
    """Ответы владельца изменились (конфликт → новый производный вопрос):
    проекция плана устарела — ни тела, ни вопросов."""
    w, recs = queue_world(tmp_path, exists=True)
    w.client.add_comment(UMB, 9, "own", "Q-11111111: drop-wait")
    w.client.add_comment(UMB, 9, "own", "Q-11111111: keep")
    reports = w.run(recs, "owner_queue")
    assert outcomes(reports)[0] == ("removed", "ответы владельца изменились")
    assert w.client.sent == []


def test_queue_create_removed_when_queue_appeared(tmp_path: Path) -> None:
    w, recs = queue_world(tmp_path, exists=False)
    w.client.issue(UMB, 50, labels=["owner-queue"], user=BOT)
    reports = w.run(recs, "owner_queue")
    assert outcomes(reports)[0] == ("removed", "очередь уже есть")
    assert w.client.sent == []
