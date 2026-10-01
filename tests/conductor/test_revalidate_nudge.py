"""nudge: свежая сверка ожидания, периода и движения (§5.1 шаг 8, §7.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.gh_app import CallResult
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, world
from tests.conductor.slice1_world import (
    B_DONE,
    B_OPEN,
    GOAL,
    GOAL_FREE,
    World,
    commit,
    outcomes,
)


def nudge_world(tmp: Path) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    commit(tmp / "a", GOAL_FREE)
    p_sha = commit(tmp / "a", GOAL, "edge")
    commit(tmp / "b", B_OPEN)
    period = {"todo://a/goal|todo://b/b": [p_sha, "2026-09-30T00:00:00Z"]}
    result, inp = world({"a": GOAL, "b": B_OPEN}, REQUESTS, edge_periods=period)
    notes: list[dict[str, Any]] = []
    recs = plan_nudges(result, inp, BOT, NOW, w.fresh, notes)
    assert [r.subject for r in recs] == ["b#4"], notes
    return w, recs


def test_nudge_twin_sends(tmp_path: Path) -> None:
    w, recs = nudge_world(tmp_path)
    assert outcomes(w.run(recs, "nudge")) == [("success", "")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda w: w.client.add_comment(
                "own/b", 4, "dev", "делаю", created_at="2026-10-10T11:00:00Z"
            ),
            "новое движение",
        ),
        (lambda w: commit(w.tmp / "b", B_DONE, "done"), "ожидание больше не pending"),
        (lambda w: commit(w.tmp / "a", GOAL_FREE, "untag"), "тег ожидания снят"),
        (
            lambda w: commit(
                w.tmp / "a",
                GOAL.replace("@blocked_by:todo://b/b", "`@blocked_by:todo://b/b`"),
                "quote",
            ),
            "тег ожидания снят",
        ),
        (
            lambda w: (
                commit(w.tmp / "a", GOAL_FREE, "untag"),
                commit(w.tmp / "a", GOAL, "retag"),
            ),
            "период изменился",
        ),
    ],
)
def test_nudge_removed_when_wait_changes(tmp_path: Path, change, reason) -> None:
    w, recs = nudge_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "nudge")) == [("removed", reason)]
    assert w.client.sent == []


def test_nudge_bot_comment_is_not_movement(tmp_path: Path) -> None:
    w, recs = nudge_world(tmp_path)
    w.client.add_comment("own/b", 4, BOT, "наш", created_at="2026-10-10T11:00:00Z")
    assert outcomes(w.run(recs, "nudge")) == [("success", "")]


# --- pr_nudge: свежая потребность PR (F3 повторного ревью) -------------------

PR_TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}
RED = {
    "__typename": "CheckRun",
    "name": "test",
    "conclusion": "FAILURE",
    "detailsUrl": "https://ci/1",
}
GREEN = {
    "__typename": "CheckRun",
    "name": "test",
    "conclusion": "SUCCESS",
    "detailsUrl": "https://ci/1",
}
APPROVED = {"state": "APPROVED", "commit": {"oid": "h"}}


def pr_world(tmp: Path, ci: str, approved: bool) -> tuple[World, list[PlanRecord]]:
    """PR a!7 реализует цель фокуса; план — по CI ci и одобрению head."""
    w = World(tmp)
    red = [{"name": "test", "url": "https://ci/1"}] if ci == "red" else []
    pr = record(
        "a",
        7,
        is_pr=True,
        body="@id:goal",
        created_at="2026-09-20T00:00:00Z",
        last_commit_at="2026-09-21T00:00:00Z",
        reviews=[],
        is_draft=False,
        red_checks=red,
        ci=ci,
        approved_at_head=approved,
    )
    w.client.pull(
        "own/a",
        7,
        head={"sha": "h"},
        updated_at=pr["updated_at"],
        checks=[RED if ci == "red" else GREEN],
        reviews=[APPROVED] if approved else [],
    )
    result, inp = world(PR_TODOS, [pr])
    recs = plan_pr_nudges(result, inp, BOT, NOW, w.fresh, [])
    assert [r.subject for r in recs] == ["a!7"]
    return w, recs


@pytest.mark.parametrize(("ci", "approved"), [("red", False), ("green", True)])
def test_pr_nudge_twin_sends(tmp_path: Path, ci: str, approved: bool) -> None:
    w, recs = pr_world(tmp_path, ci, approved)
    assert outcomes(w.run(recs, "pr_nudge")) == [("success", "")]


@pytest.mark.parametrize(
    ("ci", "approved", "change", "reason"),
    [
        # тот же head и updated_at: CI перезапущен и позеленел — «CI красный» устарел
        ("red", False, {"checks": [GREEN]}, "потребность PR изменилась"),
        # и обратно: готовый к мержу PR покраснел
        ("green", True, {"checks": [RED]}, "потребность PR изменилась"),
        # одобрение снято — уже не «готов к мержу»
        ("green", True, {"reviews": []}, "потребность PR изменилась"),
        # красный, но упала другая проверка — текст называет не те имена
        (
            "red",
            False,
            {"checks": [{**RED, "name": "lint", "detailsUrl": "https://ci/2"}]},
            "набор упавших проверок изменился",
        ),
        ("red", False, {"draft": True}, "PR закрыт или драфт"),
        ("red", False, {"head": {"sha": "h2"}}, "новая голова PR"),
    ],
)
def test_pr_nudge_removed_when_need_changes(
    tmp_path: Path, ci: str, approved: bool, change, reason
) -> None:
    w, recs = pr_world(tmp_path, ci, approved)
    w.client.pull("own/a", 7, **change)
    assert outcomes(w.run(recs, "pr_nudge")) == [("removed", reason)]
    assert w.client.sent == []


def test_pr_nudge_unreadable_checks_do_not_write(tmp_path: Path) -> None:
    w, recs = pr_world(tmp_path, "red", False)
    w.client.override[("POST", "/graphql")] = CallResult("uncertain", None)
    assert outcomes(w.run(recs, "pr_nudge")) == [
        ("removed", "основания PR не прочитаны")
    ]
    assert w.client.sent == []


def test_nudge_quoted_id_readded_edge_is_new_period(tmp_path: Path) -> None:
    """Раунд 4 (R4-2): ребро снято и добавлено при `@id:"goal"` — новый период."""
    w, recs = nudge_world(tmp_path)
    quote = '@id:"goal"'
    commit(tmp_path / "a", GOAL.replace("@id:goal", quote), "quote id")
    commit(tmp_path / "a", GOAL_FREE.replace("@id:goal", quote), "drop edge")
    commit(tmp_path / "a", GOAL.replace("@id:goal", quote), "readd edge")
    assert outcomes(w.run(recs, "nudge")) == [("removed", "период изменился")]
    assert w.client.sent == []
