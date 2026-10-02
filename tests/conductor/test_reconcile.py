"""Поиск результата незавершённых попыток (спека среза 1, §5.3; регрессия P2-3).

Обрыв `after_send` → новый процесс находит эффект по идентичности →
попытке дописан `ok`, задержка эффекта снята, повторной мутации нет.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from conductor.fresh import FreshReader
from conductor.gh_app import Blocked
from conductor.gh_write import Mutation
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import OpState, init_state, open_state
from conductor.reconcile import reconcile
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step, StopPoint, Writer, effect_key
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import BOT, NOW

ACC = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="acceptance",
    shadow=False,
    state_dir=Path("/s"),
    sandbox="own/a",
    stop_points=frozenset({"after_send"}),
)
FLEET = HostConfig(**{**ACC.__dict__, "profile": "fleet", "stop_points": frozenset()})
MARKER = make(
    "close", node=h1("node", "a#1"), period=h1("period", "0"), evidence=h1("e", "x")
)
COMMENT = Step(
    Mutation("comment", "own/a", 1, text=with_marker("ok", MARKER)), render(MARKER)
)
CLOSE = Step(Mutation("close", "own/a", 1), "close")
BODY = Step(Mutation("body", "own/a", 1, text="тело очереди"), "body")


def _roadmap():
    text = ROADMAP.replace(
        "autonomy = 0", 'autonomy = 1\nenabled_actions = ["close_shipped"]'
    )
    return parse_roadmap(text, EPICS)


def _state(tmp: Path) -> OpState:
    init_state(tmp / "state", NOW - timedelta(hours=3))
    state = open_state(tmp / "state", NOW).state
    assert state is not None
    return state


def _writer(tmp: Path, cfg: HostConfig, client: FakeClient, state: OpState, run: str):
    return Writer(
        cfg=cfg,
        client=client,
        state=state,
        log=MutationLog(tmp / run),
        journal=RunJournal(tmp / run),
        fence=frozenset({"own/a"}),
        load_roadmap=_roadmap,
        hostname="vps",
        run_id=run,
        level_cap=3,
        clock=lambda: NOW,
    )


def _interrupted(tmp: Path, steps: tuple[Step, ...]) -> tuple[FakeClient, OpState]:
    client, state = FakeClient(), _state(tmp)
    record = PlanRecord("close_shipped", "a#1", "r", 1, steps)
    with pytest.raises(StopPoint):
        _writer(tmp, ACC, client, state, "r1").execute([record])
    assert [a["status"] for a in state.attempts.values()] == ["in_flight"]
    return client, open_state(tmp / "state", NOW).state  # новый процесс


def test_after_send_comment_found_settled_and_close_proceeds(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (COMMENT, CLOSE))
    assert state is not None
    eff = effect_key(COMMENT, COMMENT.mutation)
    assert state.delayed(eff, NOW)
    notes = reconcile(state, FreshReader(client), BOT, NOW)
    assert notes == [{"settled": "own/a#1", "op": "comment"}]
    assert [a["status"] for a in state.attempts.values()] == ["ok"]
    assert not state.delayed(eff, NOW)
    # планировщик видит маркер — в плане только закрытие; оно идёт сразу
    record = PlanRecord("close_shipped", "a#1", "r", 1, (CLOSE,))
    reports = _writer(tmp_path, FLEET, client, state, "r2").execute([record])
    assert [r.outcome for r in reports] == ["success"]
    assert [s[0] for s in client.sent] == ["POST", "PATCH"]  # без второго POST


def test_without_reconcile_the_comment_stays_delayed(tmp_path: Path) -> None:
    """Двойник: без поиска эффекта повтор комментария задержан (in_flight)."""
    client, state = _interrupted(tmp_path, (COMMENT, CLOSE))
    assert state is not None
    record = PlanRecord("close_shipped", "a#1", "r", 1, (COMMENT, CLOSE))
    reports = _writer(tmp_path, FLEET, client, state, "r2").execute([record])
    assert [r.outcome for r in reports] == ["delay", "skipped_dependent"]


def test_invisible_effect_is_not_settled(tmp_path: Path) -> None:
    client = FakeClient()
    client.apply = False  # GitHub принял, но в чтении эффекта нет
    state = _state(tmp_path)
    record = PlanRecord("close_shipped", "a#1", "r", 1, (COMMENT,))
    with pytest.raises(StopPoint):
        _writer(tmp_path, ACC, client, state, "r1").execute([record])
    assert reconcile(state, FreshReader(client), BOT, NOW) == []
    assert state.delayed(effect_key(COMMENT, COMMENT.mutation), NOW)


def test_body_identity_and_close_state(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (BODY,))
    assert state is not None
    client.issue("own/a", 1, body="другая проекция")
    assert reconcile(state, FreshReader(client), BOT, NOW) == []  # не наше тело
    client.issue("own/a", 1, body="тело очереди")
    assert reconcile(state, FreshReader(client), BOT, NOW) == [
        {"settled": "own/a#1", "op": "body"}
    ]


def test_read_failure_and_ban_change_nothing(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    client.blocked = True
    assert reconcile(state, FreshReader(client), BOT, NOW) == []
    assert [a["status"] for a in state.attempts.values()] == ["in_flight"]


def test_attempt_row_keeps_effect_identity(tmp_path: Path) -> None:
    _, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    rows = (tmp_path / "state" / "attempts.jsonl").read_text(encoding="utf-8")
    begin = [json.loads(r) for r in rows.splitlines() if '"begin"' in r][0]
    assert (begin["op"], begin["expected"]) == ("comment", render(MARKER))


def test_blocked_reader_stops(tmp_path: Path) -> None:
    class Boom(FakeClient):
        def call(self, *a, **k):
            raise Blocked("лимит")

    _, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    assert reconcile(state, FreshReader(Boom()), BOT, NOW) == []
