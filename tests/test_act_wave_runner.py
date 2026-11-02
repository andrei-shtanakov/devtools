"""Волновой раннер на пути `review/v1` (интеграционное ревью части B: I-1, I-2).

Стенд — настоящий git и форджа стенда (`tests.test_governance_approve_node`),
подмены доверия и фактов ревью — `tests.test_act_node.Stand`. Раннер ведётся
своими шагами (`_step_candidate`, `_resume_wave`), а не обходом через
`approve_node`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_node, runner  # noqa: E402
from governance import approval_ledger as al  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance.act_envelope import envelope_path  # noqa: E402
from governance.act_request import manifest_path  # noqa: E402
from governance.edge_check import publish as pub  # noqa: E402
from tests.test_act_node import BodyOps, Stand  # noqa: E402
from tests.test_governance_approve_node import (  # noqa: E402
    WS_ID,
    World,
    _git,
    _make_world,
    _node_text,
    _show,
    _wave_records,
    authored_branch,
)

BRANCH = f"spec/{WS_ID}-behaviour-w1"


class WaveOps(BodyOps):
    """+ комментарии в PR — в память стенда (без `gh`)."""

    def __init__(self, forge) -> None:
        super().__init__(forge)
        self.comments: list[tuple[int, str]] = []

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        self.comments.append((pr, body))


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = _make_world(tmp_path, monkeypatch, seed_nodes=False, authoring="waves")
    w.state.wave = 1
    w.state.branch = BRANCH
    monkeypatch.setattr(
        runner.edge_coordinator,
        "load_level_records",
        lambda run_dir, wave: _wave_records(),
    )
    return replace(w, ops=WaveOps(w.forge))


@pytest.fixture()
def stand(world: World, monkeypatch: pytest.MonkeyPatch) -> Stand:
    return Stand(world, monkeypatch)


def _published(world: World) -> tuple[str, dict, int]:
    authored_branch(world, 1, {"00-charter.md": _node_text("charter")})
    assert runner._step_candidate(world.state, world.ops) is False
    key = world.state.ops["candidate-1"]["request"]
    op = world.state.ops[key]
    return key, op, op["candidate_pr"]


# --- I-1: манифест своей заявки в поверхности D16 --------------------------------


def test_review_wave_publishes_candidate_with_its_manifest(
    world: World, stand: Stand
) -> None:
    key, op, pr = _published(world)
    assert op["act"] == "review/v1"
    assert world.state.ops["candidate-1"]["status"] == "completed"
    rec = world.forge.prs[pr]
    assert rec["label"] == act_node.HUMAN_APPROVAL_LABEL
    head = world.forge.head_of(rec["branch"])
    assert head == op["head_sha"]
    text = _show(world.target, f"{head}:{manifest_path(WS_ID, key)}")
    assert f"request_id: {key}" in text or key in text
    assert world.forge.reviews[pr][-1]["state"] == "APPROVED"  # edge-check


def _tamper(world: World, key: str, path: str) -> None:
    op = world.state.ops[key]
    _git(world.target, "switch", "-q", op["branch"])
    file = world.target / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("x: 1\n", encoding="utf-8")
    _git(world.target, "add", "-A")
    _git(world.target, "commit", "-qm", "foreign path")
    al.record_head_sha(world.state, key, _git(world.target, "rev-parse", "HEAD"))


def _proposed(world: World) -> str:
    sha = authored_branch(world, 1, {"00-charter.md": _node_text("charter")})
    return an.propose_from_source(
        world.state, world.ops, ["charter"], sha, push=False
    ).request


@pytest.mark.parametrize(
    "path",
    [
        manifest_path(WS_ID, "approve-9-9-9"),  # чужой манифест
        manifest_path("WS-OTHER", "approve-1-0-1"),  # тот же ключ, чужой воркстрим
        envelope_path(WS_ID, "charter"),  # иной путь approvals/
        f"workstreams/{WS_ID}/approvals/requests/notes.txt",
    ],
)
def test_review_wave_refuses_foreign_approvals_paths(
    world: World, stand: Stand, path: str
) -> None:
    key = _proposed(world)
    assert world.state.ops[key]["act"] == "review/v1"
    _tamper(world, key, path)
    with pytest.raises(RuntimeError, match="вне поверхности"):
        pub.publish_wave(world.state, world.ops, key, _wave_records())
    assert world.forge.prs == {}


def test_legacy_request_may_not_carry_a_manifest(world: World, stand: Stand) -> None:
    """Без `act: review/v1` манифест своей же заявки — посторонний путь."""
    from governance import act_trust

    stand.mode = act_trust.PRE_MIGRATION
    key = _proposed(world)
    assert world.state.ops[key].get("act") is None
    _tamper(world, key, manifest_path(WS_ID, key))
    with pytest.raises(RuntimeError, match="вне поверхности"):
        pub.publish_wave(world.state, world.ops, key, _wave_records())


def test_review_runner_twin_foreign_manifest_stops_review(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же отказ на уровне раннера: `stopped_review`, PR не создан."""
    real = an.propose_from_source

    def propose(state, ops, nodes, sha, *, push=True):
        out = real(state, ops, nodes, sha, push=push)
        _tamper(world, out.request, manifest_path(WS_ID, "approve-9-9-9"))
        return out

    monkeypatch.setattr(runner.an, "propose_from_source", propose)
    authored_branch(world, 1, {"00-charter.md": _node_text("charter")})
    assert runner._step_candidate(world.state, world.ops) is False
    assert world.state.status == "stopped_review"
    assert world.forge.prs == {}


# --- I-2: resume волны доставляет одобренный candidate (§9.1) ---------------------


@pytest.fixture()
def next_wave(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """`_next_wave` — граница теста: переход к следующей волне не ведём."""
    seen: list[int] = []

    def fake(state, ops, *, lock):
        seen.append(state.wave)
        return state

    monkeypatch.setattr(runner, "_next_wave", fake)
    return seen


def _resume(world: World):
    return runner._resume_wave(world.state, world.ops, lock=None)  # type: ignore[arg-type]


def test_review_candidate_pause_asks_for_approve_not_merge(
    world: World, stand: Stand
) -> None:
    _key, _op, pr = _published(world)
    assert world.state.status == act_node.WAITING_HUMAN_APPROVAL
    (where, body) = world.ops.comments[-1]
    assert where == pr and "approve" in body
    assert "ждёт человеческого мержа" not in body and "мерж = акт" not in body


def test_resume_approve_agent_merge_then_finalize(
    world: World, stand: Stand, next_wave: list[int]
) -> None:
    key, _op, pr = _published(world)
    stand.approve_head(pr)
    first = _resume(world)
    assert first.status == act_node.WAITING_DELIVERY
    assert [c[0] for c in world.forge.merge_calls] == [pr], "агентская доставка"
    assert world.forge.prs[pr]["state"] == "MERGED"
    assert next_wave == []
    second = _resume(world)
    op = second.ops[key]
    assert op["status"] == al.STATUS_COMPLETED and op["approved_by"] == "alice"
    assert next_wave == [1], "finalize → следующая волна"
    world.sync()
    assert (world.target / envelope_path(WS_ID, "charter")).is_file()


def test_resume_without_approve_waits_and_does_not_merge(
    world: World, stand: Stand, next_wave: list[int]
) -> None:
    key, _op, _pr = _published(world)
    out = _resume(world)
    assert out.status == act_node.WAITING_HUMAN_APPROVAL
    assert world.forge.merge_calls == [] and next_wave == []
    assert out.ops[key]["status"] == al.STATUS_STARTED


def test_resume_non_human_approve_does_not_merge(
    world: World, stand: Stand, next_wave: list[int]
) -> None:
    from governance.act_decision import ReviewView

    _key, _op, pr = _published(world)
    head = world.forge.head_of(world.forge.prs[pr]["branch"])
    stand.effective = {
        "ai-prosto": ReviewView("12", "ai-prosto", "User", "APPROVED", head),
        "df-reviewer[bot]": ReviewView(
            "13", "df-reviewer[bot]", "Bot", "APPROVED", head
        ),
    }
    out = _resume(world)
    assert out.status == act_node.WAITING_HUMAN_APPROVAL
    assert world.forge.merge_calls == [] and next_wave == []


def test_resume_human_merge_is_still_counted(
    world: World, stand: Stand, next_wave: list[int]
) -> None:
    """D3(а): человек смержил candidate сам — доставка засчитывается."""
    from tests.test_governance_approve_node import merge_pr

    key, _op, pr = _published(world)
    stand.approve_head(pr)
    merge_pr(world, pr)
    out = _resume(world)
    assert out.ops[key]["status"] == al.STATUS_COMPLETED
    assert world.forge.merge_calls == [] or world.forge.merge_calls[0][0] != pr
    assert next_wave == [1]


def test_resume_unresolved_is_stopped_review(
    world: World, stand: Stand, next_wave: list[int]
) -> None:
    key, _op, _pr = _published(world)
    stand.mode_unset = "нет привязки корня доверия"
    out = _resume(world)
    assert out.status == "stopped_review"
    assert out.ops[key]["status"] == al.STATUS_STARTED, "заявка жива"
    assert world.forge.merge_calls == [] and next_wave == []


def test_resume_agent_merge_refused_is_not_a_merge_request(
    world: World, stand: Stand, monkeypatch: pytest.MonkeyPatch, next_wave: list[int]
) -> None:
    """Approve есть, обвязка отказала: стоп с причиной, без просьбы о мерже."""
    _key, _op, pr = _published(world)
    stand.approve_head(pr)
    monkeypatch.setattr(world.ops, "merge", lambda *a, **k: 3)
    out = _resume(world)
    assert out.status == "stopped_review"
    assert world.forge.prs[pr]["state"] == "OPEN" and next_wave == []
