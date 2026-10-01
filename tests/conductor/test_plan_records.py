"""Сборка плана и сквозной прогон через исполнитель (срез 1)."""

import json
from datetime import timedelta
from pathlib import Path

from conductor.actions import PlanContext, plan_records
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import Writer
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import BOT, NOW, TODOS, world

CFG = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
DONE = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
UMB = "own/ai-orchestrators-workspace"


def _ctx(tmp_path: Path) -> PlanContext:
    init_state(tmp_path, NOW - timedelta(hours=3))
    state = open_state(tmp_path, NOW).state
    assert state is not None
    return PlanContext(CFG, UMB, BOT, state, owner_login="own", now=NOW)


def test_partial_graph_plans_nothing(tmp_path: Path) -> None:
    result, inp = world(gh_state="error")
    ctx = _ctx(tmp_path)
    assert plan_records(result, inp, ctx) == []
    assert ctx.notes == [{"note": "граф partial — записей нет"}]


def test_end_to_end_with_writer(tmp_path: Path) -> None:
    result, inp = world(DONE, done_facts={"todo://b/b": "s1"})
    ctx = _ctx(tmp_path / "state")
    records = plan_records(result, inp, ctx)
    actions = [r.action for r in records]
    assert "notify_satisfied" in actions
    enabled = json.dumps(
        ["owner_queue", "notify_satisfied", "nudge", "pr_nudge", "close_shipped"]
    )
    rm = parse_roadmap(
        ROADMAP.replace("autonomy = 0", f"autonomy = 1\nenabled_actions = {enabled}"),
        EPICS,
    )
    client = FakeClient()
    writer = Writer(
        cfg=CFG,
        client=client,
        state=ctx.state,
        log=MutationLog(tmp_path / "run"),
        journal=RunJournal(tmp_path / "run"),
        fence=frozenset({"own/a", "own/b", UMB}),
        load_roadmap=lambda: rm,
        hostname="vps",
        run_id="r1",
        level_cap=3,
        clock=lambda: NOW,
    )
    reports = writer.execute(records)
    assert reports and all(r.outcome == "success" for r in reports)
    assert any(p.endswith("/issues/3/comments") for _, p, _ in client.sent)
