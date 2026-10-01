"""notify_satisfied: свежая сверка перед отправкой (§5.1 шаг 8, §7.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.notify import plan_notify
from conductor.writer import PlanRecord
from tests.conductor.slice1_fixtures import BOT, REQUESTS, world
from tests.conductor.slice1_world import (
    B_DONE,
    B_OPEN,
    GOAL,
    GOAL_FREE,
    World,
    commit,
    outcomes,
)


def notify_world(tmp: Path) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    commit(tmp / "a", GOAL)
    commit(tmp / "b", B_OPEN)
    done = commit(tmp / "b", B_DONE, "done")
    result, inp = world(
        {"a": GOAL, "b": B_DONE}, REQUESTS, done_facts={"todo://b/b": done}
    )
    notes: list[dict[str, Any]] = []
    return w, plan_notify(result, inp, BOT, w.fresh, notes)


def test_notify_twin_sends(tmp_path: Path) -> None:
    w, recs = notify_world(tmp_path)
    assert outcomes(w.run(recs, "notify_satisfied")) == [("success", "")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda w: commit(w.tmp / "b", B_OPEN, "reopen"), "доказательство изменилось"),
        (lambda w: commit(w.tmp / "a", GOAL_FREE, "untag"), "тег ожидания снят"),
        (lambda w: w.client.issue("own/a", 3, state="closed"), "субъект закрыт"),
        # канонический разбор: `@id` предпосылки в бэктиках — пункта нет
        (
            lambda w: commit(w.tmp / "b", B_DONE.replace("@id:b", "`@id:b`"), "q"),
            "доказательство изменилось",
        ),
        # тег ожидания в бэктиках — ребра нет
        (
            lambda w: commit(
                w.tmp / "a",
                GOAL.replace("@blocked_by:todo://b/b", "`@blocked_by:todo://b/b`"),
                "q",
            ),
            "тег ожидания снят",
        ),
    ],
)
def test_notify_removed_when_basis_changes(tmp_path: Path, change, reason) -> None:
    w, recs = notify_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "notify_satisfied")) == [("removed", reason)]
    assert w.client.sent == []


def test_notify_removed_when_fact_redone(tmp_path: Path) -> None:
    """Снятие и повторная отметка [x] — новый факт: старая запись снята."""
    w, recs = notify_world(tmp_path)
    commit(tmp_path / "b", B_OPEN, "reopen")
    commit(tmp_path / "b", B_DONE, "done again")
    assert outcomes(w.run(recs, "notify_satisfied")) == [
        ("removed", "доказательство изменилось")
    ]


def test_notify_quoted_id_redone_fact_is_new(tmp_path: Path) -> None:
    """Раунд 4 (R4-2): `@id:"b"` — тот же пункт для ядра; повторная отметка
    после переписывания id — новый факт, старая запись снята."""
    w, recs = notify_world(tmp_path)
    quoted = B_DONE.replace("@id:b", '@id:"b"')
    commit(tmp_path / "b", quoted, "quote id")
    commit(tmp_path / "b", quoted.replace("[x]", "[ ]"), "reopen")
    commit(tmp_path / "b", quoted, "done again")
    assert outcomes(w.run(recs, "notify_satisfied")) == [
        ("removed", "доказательство изменилось")
    ]
    assert w.client.sent == []
