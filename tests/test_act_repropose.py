"""--repropose: преемник заявки без переписывания терминальной записи (§6, Task 13)."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance import act_node, act_status  # noqa: E402
from governance import approval_ledger as al  # noqa: E402
from governance import approve_node as an  # noqa: E402
from governance.act_check import ActivationView  # noqa: E402
from governance.act_decision import Accepted, Rejected  # noqa: E402
from governance.act_envelope import (  # noqa: E402
    DeliveredRequest,
    envelope_path,
    next_generation,
)
from governance.act_time import Ambiguous, Unavailable  # noqa: E402
from tests.test_act_node import NOW, BodyOps, Stand  # noqa: E402
from tests.test_governance_approve_node import (  # noqa: E402
    SLUG,
    World,
    _git,
    _make_world,
    approve,
)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = _make_world(tmp_path, monkeypatch, seed_nodes=True)
    return replace(w, ops=BodyOps(w.forge))


@pytest.fixture()
def stand(world: World, monkeypatch: pytest.MonkeyPatch) -> Stand:
    return Stand(world, monkeypatch)


def _repropose(world: World, key: str):
    return act_node.repropose(world.state, world.ops, key)


def test_after_policy_changed_successor_carries_predecessor(world, stand, monkeypatch):
    first = approve(world, "charter")
    key = first.request
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda s, d=None: replace(stand.live(), activation=newer),
    )
    with pytest.raises(RuntimeError, match="policy_changed"):
        approve(world, "charter")
    before = copy.deepcopy(world.state.ops[key])
    out = _repropose(world, key)
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["predecessor"] == key
    assert world.state.ops[key] == before  # терминальная запись не тронута
    assert world.state.ops[succ]["policy"]["activation_commit"] == "b" * 40
    assert out.request == succ


def test_persistent_ambiguous_merged_candidate(world, stand):
    first = approve(world, "charter")
    key = first.request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")  # агентская доставка
    stand.verdict = Ambiguous()
    with pytest.raises(RuntimeError, match="--repropose"):
        approve(world, "charter")
    _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED
    assert world.state.ops[key]["reason"].startswith("reproposed:")
    assert al.successor_of(world.state, key) is not None


def _squash_pr(world: World, pr: int, *, login: str = "alice") -> str:
    """Человек мержит candidate squash-ом: один родитель, голова PR — только в
    `refs/pull/N/head` и фактах PR (B1-9)."""
    rec, clone = world.forge.prs[pr], world.forge.merger
    assert clone is not None
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "switch", "-q", "master")
    _git(clone, "reset", "-q", "--hard", "origin/master")
    _git(clone, "merge", "-q", "--squash", f"origin/{rec['branch']}")
    _git(clone, "commit", "-qm", f"PR #{pr} (squash)")
    sha = _git(clone, "rev-parse", "HEAD")
    _git(
        clone,
        "push",
        "-q",
        "origin",
        "master",
        f"refs/remotes/origin/{rec['branch']}:refs/pull/{pr}/head",
    )
    rec.update(
        state="MERGED",
        mergedBy={"login": login},
        mergedAt=NOW,
        mergeCommit={"oid": sha},
    )
    return sha


def test_squash_delivery_read_and_repropose_cycle(world, stand):
    """B1-9: squash-доставка читается историей (голова из фактов PR, не из второго
    родителя), постоянный Ambiguous уводит в --repropose, преемник создаётся."""
    first = approve(world, "charter")
    key = first.request
    pr = world.state.ops[key]["candidate_pr"]
    stand.approve_head(pr)
    _squash_pr(world, pr)
    # читатель истории идёт по ЛОКАЛЬНОЙ базе: сперва синхронизировать target (B2-4)
    world.ops.checkout_and_pull(world.state.target_dir, "master")
    history = act_status.load_history(world.ops, world.state, "master")
    assert not isinstance(history, Unavailable)
    delivered = [e for e in history if isinstance(e, DeliveredRequest)]
    assert [e.request.request_id for e in delivered] == [key]
    assert delivered[0].head_sha == world.ops.pr_facts(SLUG, pr)["headRefOid"]
    stand.verdict = Ambiguous()
    with pytest.raises(RuntimeError, match="--repropose"):
        approve(world, "charter")
    _repropose(world, key)
    assert world.state.ops[key]["reason"].startswith("reproposed:")
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["predecessor"] == key


def test_open_candidate_is_closed_before_successor(world, stand):
    first = approve(world, "charter")
    key = first.request
    pr = world.state.ops[key]["candidate_pr"]
    _repropose(world, key)
    assert world.forge.prs[pr]["state"] == "CLOSED"
    assert world.state.ops[key]["status"] == al.STATUS_INVALIDATED


def test_interrupted_repropose_creates_exactly_one_successor(world, stand, monkeypatch):
    first = approve(world, "charter")
    key = first.request
    real = act_node._create_successor
    calls: list[int] = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("упали после инвалидации")
        return real(*args, **kwargs)

    monkeypatch.setattr(act_node, "_create_successor", flaky)
    with pytest.raises(RuntimeError, match="упали"):
        _repropose(world, key)
    _repropose(world, key)
    _repropose(world, key)  # повтор продвигает того же преемника
    succs = [k for k, op in world.state.ops.items() if op.get("predecessor") == key]
    assert len(succs) == 1


def test_unknown_request_refused(world, stand):
    """b4 (`repropose`): ключа нет в леджере — отказ, преемник не создаётся."""
    with pytest.raises(RuntimeError, match="нет в леджере"):
        _repropose(world, "approve-9-9-9")


def test_two_successors_refused(world, stand):
    """b4 (`approval_ledger.successor_of`): два преемника одной заявки — разбор вручную."""
    first = approve(world, "charter")
    key = first.request
    for other in ("approve-8-8-1", "approve-8-8-2"):
        world.state.ops[other] = {**world.state.ops[key], "predecessor": key}
    with pytest.raises(RuntimeError, match="несколько преемников"):
        al.successor_of(world.state, key)


def test_successor_refused_when_step_has_live_request(world, stand, monkeypatch):
    """b5/b6 (`approve_node._propose#successor_joins_live`): над `acceptance` заявки нет, но
    у её шага есть живая (`design`) — преемник не создаётся и к ней не присоединяется."""
    from tests.test_act_node import _level_three

    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    first = approve(world, "design")
    with pytest.raises(RuntimeError, match="уже есть — продвиньте её"):
        an.approve_node(
            world.state, world.ops, "acceptance", predecessor="approve-9-9-9"
        )
    assert world.state.ops[first.request]["nodes"] == ["design"]


def test_completed_request_refused(world, stand):
    first = approve(world, "charter")
    key = first.request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    approve(world, "charter")
    assert world.state.ops[key]["status"] == al.STATUS_COMPLETED
    with pytest.raises(RuntimeError, match="--reopen"):
        _repropose(world, key)


def test_delivered_without_act_is_last_event_no_rollback() -> None:
    """R1/g1 одобрен, R2/g2 доставлен без действительного акта с теми же байтами:
    узел «не одобрен», конверт R1 отвергнут, следующее поколение — 3."""
    from tests.test_act_status import H_REQ, Snapshot, delivered, envelope, statuses

    r1 = delivered("approve-1-0-1", "a" * 40, {"requirements": (H_REQ, 1)})
    r2 = delivered("approve-1-0-2", "b" * 40, {"requirements": (H_REQ, 2)})
    files = {
        "spec/10-requirements.md": "---\nspec_stage: requirements\nstatus: "
        "approval_pending\nversion: 2\n---\nтело\n",
        envelope_path("ws-7", "requirements"): envelope(r1, "requirements"),
    }

    def decide(event: DeliveredRequest):
        return (
            Accepted("a" * 40, "df-reviewer", "11", "alice")
            if event is r1
            else Rejected("d1_no_act")
        )

    st = statuses((r1, r2), Snapshot(files, {}), decide=decide)
    assert st.of("requirements") == "not_approved:act:d1_no_act"
    assert next_generation((r1, r2), "requirements") == 3


# --- дополнительные двойники исполнителя (мутационный проход Task 13a) ---------------


def _ambiguous_predecessor(world: World, stand: Stand) -> str:
    """Смёрженный агентом candidate g1 с постоянным `Ambiguous` — заявка жива."""
    first = approve(world, "charter")
    key = first.request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    stand.verdict = Ambiguous()
    return key


def test_successor_generation_is_top_plus_one(world, stand):
    """Доставленный без решения g1 — последнее событие узла: преемник берёт g2,
    а не повторяет g1 (`node_history` проигнорировал бы неувеличенное)."""
    key = _ambiguous_predecessor(world, stand)
    out = _repropose(world, key)
    assert world.state.ops[out.request]["generations"] == {"charter": 2}
    assert world.state.ops[key]["generations"] == {"charter": 1}


def test_terminal_predecessor_pr_closed_with_its_own_reason(world, stand, monkeypatch):
    """Терминальный предшественник (policy_changed) с открытым PR: PR закрывается
    причиной из записи, запись не меняется."""
    key = approve(world, "charter").request
    pr = world.state.ops[key]["candidate_pr"]
    newer = ActivationView(("activation", "b" * 40), "b" * 40, NOW)
    monkeypatch.setattr(
        act_status,
        "mode_probe",
        lambda s, d=None: replace(stand.live(), activation=newer),
    )
    with pytest.raises(RuntimeError, match="policy_changed"):
        approve(world, "charter")
    assert world.forge.prs[pr]["state"] == "OPEN"
    reason = world.state.ops[key]["reason"]
    _repropose(world, key)
    assert world.forge.prs[pr]["state"] == "CLOSED"
    assert reason in world.forge.prs[pr]["closed_with"]
    assert world.state.ops[key]["reason"] == reason


def test_live_predecessor_invalidated_with_reproposed_reason(world, stand):
    key = approve(world, "charter").request
    pr = world.state.ops[key]["candidate_pr"]
    _repropose(world, key)
    assert world.state.ops[key]["reason"] == act_node.REPROPOSED
    assert act_node.REPROPOSED in world.forge.prs[pr]["closed_with"]


def test_unconfirmed_close_creates_no_successor(world, stand):
    """Закрытие PR предшественника не подтверждено — преемника нет; повтор доводит."""
    key = approve(world, "charter").request
    world.forge.close_confirms = False
    with pytest.raises(RuntimeError, match="закрытие PR"):
        _repropose(world, key)
    assert al.successor_of(world.state, key) is None
    world.forge.close_confirms = True
    _repropose(world, key)
    assert al.successor_of(world.state, key) is not None


@pytest.mark.parametrize("step", ["finalize", "await_finalize_merge"])
def test_after_merge_step_refused(world, stand, step):
    """Мерж candidate засчитан (акт записан) — продолжение `--approve-node`."""
    key = approve(world, "charter").request
    op = world.state.ops[key]
    extra = {"merged_by": "alice", "merged_at": "2026-10-12T12:00:00Z"}
    if step == "await_finalize_merge":
        extra["finalize_pr"] = 999
    world.state.ops[key] = before = {**op, **extra}
    with pytest.raises(RuntimeError, match="мерж candidate уже засчитан"):
        _repropose(world, key)
    assert world.state.ops[key] == before
    assert al.successor_of(world.state, key) is None
    assert world.forge.prs[op["candidate_pr"]]["state"] == "OPEN"


def test_unrecognized_status_refused(world, stand):
    key = approve(world, "charter").request
    world.state.ops[key] = {**world.state.ops[key], "status": "frozen"}
    with pytest.raises(RuntimeError, match="не распознан"):
        _repropose(world, key)
    assert al.successor_of(world.state, key) is None


def test_non_request_keys_refused(world, stand):
    """Запись волны и `audit-<key>` лежат в `state.ops`, но заявками не являются."""
    key = approve(world, "charter").request
    world.state.ops[f"{al.AUDIT_PREFIX}{key}"] = {"status": al.STATUS_STARTED}
    wave = next(k for k in world.state.ops if k.startswith(al.WAVE_PREFIX))
    for other in (wave, f"{al.AUDIT_PREFIX}{key}"):
        with pytest.raises(RuntimeError, match="нет в леджере"):
            _repropose(world, other)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_terminal_successor_refused(world, stand):
    key = approve(world, "charter").request
    succ = _repropose(world, key).request
    assert succ is not None
    al.invalidate_request(world.state, succ, "проверка")
    before = set(world.state.ops)
    with pytest.raises(RuntimeError, match=f"преемник {succ}"):
        _repropose(world, key)
    assert set(world.state.ops) == before


def test_repeat_advances_successor_not_new_one(world, stand):
    """Повтор после успешного переиздания продвигает того же преемника."""
    key = approve(world, "charter").request
    succ = _repropose(world, key).request
    again = _repropose(world, key)
    assert again.request == succ
    assert [k for k, op in world.state.ops.items() if op.get("predecessor")] == [succ]


def test_approve_node_refuses_successor_over_live_node_request(world, stand):
    """`approve_node(predecessor=…)` не продвигает чужую живую заявку над узлом."""
    key = approve(world, "charter").request
    with pytest.raises(RuntimeError, match="уже есть живая заявка"):
        an.approve_node(world.state, world.ops, "charter", predecessor="approve-9-9-9")
    assert [al.request_key(*n) for n, _ in al.requests(world.state)] == [key]
    assert "predecessor" not in world.state.ops[key]


def test_undecided_status_of_other_request_still_refuses(world, stand):
    """Неустановленный статус узла пропускает только преемника САМОЙ доставленной
    заявки; иной `predecessor` и обычный вызов — прежний отказ."""
    key = _ambiguous_predecessor(world, stand)
    al.invalidate_request(world.state, key, "проверка")
    for predecessor in (None, "approve-9-9-9"):
        with pytest.raises(RuntimeError, match="unavailable:ambiguous_event_order"):
            an.approve_node(world.state, world.ops, "charter", predecessor=predecessor)
    assert al.successor_of(world.state, key) is None


def test_undecided_upstream_with_accepted_event_still_refuses(
    world, stand, monkeypatch
):
    """Решение по доставке предшественника есть (`Accepted`), а статус неустановлен
    по иной причине — преемник не создаётся."""
    key = _ambiguous_predecessor(world, stand)
    al.invalidate_request(world.state, key, "проверка")
    stand.verdict = None  # Accepted
    monkeypatch.setattr(
        act_status.Statuses, "of", lambda self, node: "unavailable:upstream:x"
    )
    with pytest.raises(RuntimeError, match="unavailable:upstream:x"):
        an.approve_node(world.state, world.ops, "charter", predecessor=key)
    assert al.successor_of(world.state, key) is None


def test_undecided_status_with_unreadable_history_refuses(world, stand, monkeypatch):
    key = _ambiguous_predecessor(world, stand)
    al.invalidate_request(world.state, key, "проверка")
    real = act_status.statuses_at_base

    def no_history(*args, **kwargs):
        got = real(*args, **kwargs)
        got.history = Unavailable("история")
        return got

    monkeypatch.setattr(act_status, "statuses_at_base", no_history)
    with pytest.raises(RuntimeError, match="unavailable:история"):
        an.approve_node(world.state, world.ops, "charter", predecessor=key)
    assert al.successor_of(world.state, key) is None


def test_multi_node_recovery_joins_missing_node(world, stand, monkeypatch):
    """Падение между узлами при создании преемника: повтор дописывает узел в того же
    преемника, второго не заводит."""
    from tests.test_act_node import _level_three

    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    key = approve(world, "design").request
    approve(world, "acceptance")
    assert world.state.ops[key]["nodes"] == ["design", "acceptance"]
    real = an.approve_node
    failed: list[str] = []

    def flaky(state, ops, node, **kwargs):
        if node == "acceptance" and not failed:
            failed.append(node)
            raise RuntimeError("упали между узлами")
        return real(state, ops, node, **kwargs)

    monkeypatch.setattr(an, "approve_node", flaky)
    with pytest.raises(RuntimeError, match="между узлами"):
        _repropose(world, key)
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["nodes"] == ["design"]
    _repropose(world, key)
    assert al.successor_of(world.state, key) == succ
    assert world.state.ops[succ]["nodes"] == ["design", "acceptance"]


def test_source_successor_missing_nodes_refused(world, stand):
    key = approve(world, "charter").request
    pred = world.state.ops[key]
    world.state.ops[key] = {**pred, "nodes": ["charter", "requirements"]}
    world.state.ops["approve-8-8-1"] = {
        **pred,
        "predecessor": key,
        "source_sha": "c" * 40,
        "candidate_pr": None,
    }
    with pytest.raises(RuntimeError, match="разберите вручную"):
        _repropose(world, key)


def test_source_predecessor_goes_through_propose_from_source(world, stand, monkeypatch):
    key = approve(world, "charter").request
    world.state.ops[key] = {**world.state.ops[key], "source_sha": "c" * 40}
    seen: list[tuple] = []

    def fake(state, ops, nodes, sha, **kwargs):
        seen.append((nodes, sha, kwargs))
        return an.ApprovalOutcome("ok")

    monkeypatch.setattr(an, "propose_from_source", fake)
    act_node._create_successor(world.state, world.ops, key, world.state.ops[key], 6)
    assert seen == [(["charter"], "c" * 40, {"legacy_bundle": 6, "predecessor": key})]


def test_base_successor_passes_legacy_bundle_and_predecessor(world, stand, monkeypatch):
    seen: list[tuple] = []

    def fake(state, ops, node, **kwargs):
        seen.append((node, kwargs))
        return an.ApprovalOutcome("ok")

    monkeypatch.setattr(an, "approve_node", fake)
    op = {"nodes": ["design", "acceptance"], "source_sha": None}
    act_node._create_successor(world.state, world.ops, "approve-1-1-1", op, 6)
    assert seen == [
        ("design", {"legacy_bundle": 6, "predecessor": "approve-1-1-1"}),
        ("acceptance", {"legacy_bundle": 6, "predecessor": "approve-1-1-1"}),
    ]


# --- fix round 1: I-1 (многоузловой преемник), m-1 (засчитываемый акт) ------------


def _two_node_ambiguous(world: World, stand: Stand, monkeypatch) -> str:
    """Заявка над `[design, acceptance]`, смёржена агентом, вердикт `Ambiguous`."""
    from tests.test_act_node import _level_three

    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    key = approve(world, "design").request
    approve(world, "acceptance")
    assert world.state.ops[key]["nodes"] == ["design", "acceptance"]
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "design")  # агентская доставка
    stand.verdict = Ambiguous()
    with pytest.raises(RuntimeError, match="--repropose"):
        approve(world, "design")
    return key


def test_two_node_ambiguous_gives_one_successor_over_both(world, stand, monkeypatch):
    """I-1: второй узел (статус тоже `unavailable:` — та же доставка) дописывается
    в своего преемника, а не упирается в отказ."""
    key = _two_node_ambiguous(world, stand, monkeypatch)
    _repropose(world, key)
    succs = [k for k, op in world.state.ops.items() if op.get("predecessor") == key]
    assert len(succs) == 1
    assert world.state.ops[succs[0]]["nodes"] == ["design", "acceptance"]
    assert world.state.ops[succs[0]]["generations"] == {"design": 2, "acceptance": 2}


def test_two_node_ambiguous_crash_between_nodes_recovers(world, stand, monkeypatch):
    key = _two_node_ambiguous(world, stand, monkeypatch)
    real = an.approve_node
    failed: list[str] = []

    def flaky(state, ops, node, **kwargs):
        if node == "acceptance" and not failed:
            failed.append(node)
            raise RuntimeError("упали между узлами")
        return real(state, ops, node, **kwargs)

    monkeypatch.setattr(an, "approve_node", flaky)
    with pytest.raises(RuntimeError, match="между узлами"):
        _repropose(world, key)
    succ = al.successor_of(world.state, key)
    assert succ is not None and world.state.ops[succ]["nodes"] == ["design"]
    _repropose(world, key)
    succs = [k for k, op in world.state.ops.items() if op.get("predecessor") == key]
    assert succs == [succ]
    assert world.state.ops[succ]["nodes"] == ["design", "acceptance"]


def test_foreign_live_request_of_the_step_still_refused(world, stand, monkeypatch):
    """Чужая живая заявка шага (без `predecessor`) — преемник к ней не дописывается."""
    from tests.test_act_node import _level_three

    _level_three(world, stand, monkeypatch)
    stand.mode = "review"
    key = approve(world, "design").request
    approve(world, "acceptance")
    pr = world.state.ops[key]["candidate_pr"]
    al.invalidate_request(world.state, key, "проверка")
    world.forge.prs[pr]["state"] = "CLOSED"
    foreign = approve(world, "acceptance").request
    assert foreign != key
    with pytest.raises(RuntimeError, match="уже есть — продвиньте её"):
        _repropose(world, key)
    assert al.successor_of(world.state, key) is None
    assert world.state.ops[foreign]["nodes"] == ["acceptance"]


def test_approve_node_with_predecessor_advances_own_successor(world, stand):
    """Живая заявка над узлом — свой преемник: продвигается, а не отказ."""
    key = approve(world, "charter").request
    succ = _repropose(world, key).request
    out = an.approve_node(world.state, world.ops, "charter", predecessor=key)
    assert out.request == succ
    assert al.successor_of(world.state, key) == succ


def test_merged_predecessor_with_accepted_delivery_refused(world, stand):
    """m-1: candidate смёржен, решение по доставке — `Accepted` (акт ещё не
    засчитан `--approve-node`): не инвалидируется, PR не трогается."""
    key = approve(world, "charter").request
    pr = world.state.ops[key]["candidate_pr"]
    stand.approve_head(pr)
    approve(world, "charter")  # агентская доставка, акт ещё не записан
    before = copy.deepcopy(world.state.ops[key])
    with pytest.raises(RuntimeError, match="засчитывается .* `--approve-node`"):
        _repropose(world, key)
    assert world.state.ops[key] == before
    assert world.state.ops[key]["status"] == al.STATUS_STARTED
    assert world.forge.prs[pr]["state"] == "MERGED"
    assert al.successor_of(world.state, key) is None


def test_merged_predecessor_with_rejected_delivery_proceeds(world, stand):
    key = approve(world, "charter").request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    stand.verdict = Rejected("d3_merger", "мержер вне allowlist")
    _repropose(world, key)
    assert world.state.ops[key]["reason"] == act_node.REPROPOSED
    assert al.successor_of(world.state, key) is not None


def test_merged_predecessor_mode_unset_refused(world, stand):
    key = approve(world, "charter").request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    stand.mode_unset = "нет привязки"
    with pytest.raises(RuntimeError, match="нет привязки"):
        _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def _merged_ambiguous(world: World, stand: Stand) -> str:
    key = approve(world, "charter").request
    stand.approve_head(world.state.ops[key]["candidate_pr"])
    approve(world, "charter")
    stand.verdict = Ambiguous()
    return key


def test_merged_predecessor_outside_review_mode_refused(world, stand):
    key = _merged_ambiguous(world, stand)
    stand.mode = "merge"
    with pytest.raises(RuntimeError, match="режим продукта сейчас — merge"):
        _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_merged_predecessor_history_unreadable_refused(world, stand, monkeypatch):
    key = _merged_ambiguous(world, stand)
    monkeypatch.setattr(
        act_status, "load_history", lambda ops, state, base: Unavailable("сбой")
    )
    with pytest.raises(RuntimeError, match="история заявок узлов: сбой"):
        _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def test_merged_predecessor_missing_from_history_refused(world, stand, monkeypatch):
    key = _merged_ambiguous(world, stand)
    monkeypatch.setattr(act_status, "load_history", lambda ops, state, base: ())
    with pytest.raises(RuntimeError, match="доставки в истории"):
        _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED


def _legacy_merged(world: World, stand: Stand) -> tuple[str, int]:
    """Заявка без маркера (до миграции), candidate смёржен человеком, мерж ещё не
    засчитан `--approve-node`."""
    from governance import act_trust
    from tests.test_governance_approve_node import merge_pr

    stand.mode = act_trust.PRE_MIGRATION
    key = approve(world, "charter").request
    assert "act" not in world.state.ops[key]
    pr = world.state.ops[key]["candidate_pr"]
    merge_pr(world, pr)
    return key, pr


def test_legacy_merged_accepted_refused(world, stand):
    """m-3: вердикт старого кода `LegacyAccepted` — акт не хоронится."""
    from governance.act_check import LegacyAccepted

    key, pr = _legacy_merged(world, stand)
    op = world.state.ops[key]
    facts = an._pr_facts(world.state, world.ops, pr)
    dag = an.bundle_dag.dag_for(None)
    assert isinstance(
        an._legacy_outcome(world.state, world.ops, dag, dict(op), key, facts),
        LegacyAccepted,
    )
    before = copy.deepcopy(op)
    with pytest.raises(RuntimeError, match="засчитывается .* `--approve-node`"):
        _repropose(world, key)
    assert world.state.ops[key] == before
    assert world.forge.prs[pr]["state"] == "MERGED"
    assert al.successor_of(world.state, key) is None


def test_legacy_merged_not_accepted_proceeds(world, stand, monkeypatch):
    key, _pr = _legacy_merged(world, stand)
    monkeypatch.setattr(
        an, "_legacy_outcome", lambda *a: Rejected("legacy_signature", "не тот")
    )
    _repropose(world, key)
    assert world.state.ops[key]["reason"] == act_node.REPROPOSED
    assert al.successor_of(world.state, key) is not None


def test_legacy_merged_outcome_unresolved_refused(world, stand, monkeypatch):
    key, _pr = _legacy_merged(world, stand)
    monkeypatch.setattr(an, "_legacy_outcome", lambda *a: an.LegacyHalt("сеть"))
    with pytest.raises(RuntimeError, match="вердикт старого кода .*сеть"):
        _repropose(world, key)
    assert world.state.ops[key]["status"] == al.STATUS_STARTED
    assert al.successor_of(world.state, key) is None


# --- путь ветки волны (`propose_from_source`) ----------------------------------------


@pytest.fixture()
def waves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    from tests.test_governance_approve_node import _make_world as make

    return make(tmp_path, monkeypatch, seed_nodes=False, authoring="waves")


def test_source_successor_records_predecessor(waves: World) -> None:
    from tests.test_governance_approve_node import _node_text, authored_branch

    sha = authored_branch(waves, 1, {"00-charter.md": _node_text("charter")})
    key = an.propose_from_source(waves.state, waves.ops, ["charter"], sha).request
    assert key is not None
    out = act_node.repropose(waves.state, waves.ops, key)
    succ = waves.state.ops[out.request]
    assert succ["predecessor"] == key and succ["source_sha"] == sha
    assert waves.state.ops[key]["reason"] == act_node.REPROPOSED


def test_source_resume_refuses_successor(waves: World) -> None:
    """Живая заявка над теми же узлами из того же коммита — не доигрывается как
    преемник."""
    from tests.test_governance_approve_node import _node_text, authored_branch

    sha = authored_branch(waves, 1, {"00-charter.md": _node_text("charter")})
    key = an.propose_from_source(waves.state, waves.ops, ["charter"], sha).request
    with pytest.raises(RuntimeError, match="над теми же узлами уже есть"):
        an.propose_from_source(
            waves.state, waves.ops, ["charter"], sha, predecessor="approve-9-9-9"
        )
    assert "predecessor" not in waves.state.ops[key]


def test_source_resume_of_own_successor_continues(waves: World) -> None:
    from tests.test_governance_approve_node import _node_text, authored_branch

    sha = authored_branch(waves, 1, {"00-charter.md": _node_text("charter")})
    first = an.propose_from_source(
        waves.state, waves.ops, ["charter"], sha, predecessor="approve-9-9-9"
    )
    again = an.propose_from_source(
        waves.state, waves.ops, ["charter"], sha, predecessor="approve-9-9-9"
    )
    assert again.request == first.request
    assert len(al.requests(waves.state)) == 1


def test_source_successor_does_not_join_live_step(waves: World) -> None:
    from tests.test_governance_approve_node import (
        _node_text,
        authored_branch,
        drive_wave,
    )

    drive_wave(waves, 1, {"00-charter.md": _node_text("charter")})
    drive_wave(waves, 2, {"10-requirements.md": _node_text("requirements")})
    drive_wave(waves, 3, {"15-behaviour-spec.md": _node_text("behaviour-spec")})
    sha = authored_branch(
        waves,
        4,
        {
            "20-design.md": _node_text("design"),
            "25-acceptance.md": _node_text("acceptance"),
        },
    )
    key = an.propose_from_source(waves.state, waves.ops, ["design"], sha).request
    with pytest.raises(RuntimeError, match="уже есть — продвиньте её"):
        an.propose_from_source(
            waves.state, waves.ops, ["acceptance"], sha, predecessor="approve-9-9-9"
        )
    assert waves.state.ops[key]["nodes"] == ["design"]


# --- CLI -----------------------------------------------------------------------------


def _completed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from governance import run_state as rs

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = rs.new_run(
        subject="s",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-alpha-7",
        target_dir=str(tmp_path),
        bundle_dir="workstreams/WS-alpha-7/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-repropose",
    )
    state.status = "completed"
    rs.save(state)


@pytest.mark.parametrize(
    "extra", [["--approve-node", "charter"], ["--supersede"], ["--deliver-approve"]]
)
def test_cli_repropose_is_a_separate_action(tmp_path, monkeypatch, capsys, extra):
    from governance import task_bridge as tb

    _completed_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        tb.main(["--run-id", "r-repropose", "--repropose", "approve-1-0-1", *extra])
    assert "--repropose — отдельное действие" in capsys.readouterr().err


def test_cli_repropose_dispatches(tmp_path, monkeypatch, capsys):
    from governance import task_bridge as tb

    _completed_run(tmp_path, monkeypatch)
    seen: list[tuple] = []

    def fake(state, ops, key, *, legacy_bundle=None):
        seen.append((state.run_id, key, legacy_bundle))
        return an.ApprovalOutcome("преемник создан")

    monkeypatch.setattr(act_node, "repropose", fake)
    argv = ["--run-id", "r-repropose", "--repropose", "approve-1-0-1"]
    assert tb.main([*argv, "--legacy-bundle", "5"]) == 0
    assert seen == [("r-repropose", "approve-1-0-1", 5)]
    assert "преемник создан" in capsys.readouterr().out


def test_cli_repropose_refusal_returns_one(tmp_path, monkeypatch, capsys):
    from governance import task_bridge as tb

    _completed_run(tmp_path, monkeypatch)

    def fake(state, ops, key, *, legacy_bundle=None):
        raise RuntimeError(f"заявки {key} нет в леджере")

    monkeypatch.setattr(act_node, "repropose", fake)
    assert tb.main(["--run-id", "r-repropose", "--repropose", "approve-9-9-9"]) == 1
    assert "task_bridge: заявки approve-9-9-9 нет в леджере" in capsys.readouterr().out


def test_cli_repropose_with_reason_refused(tmp_path, monkeypatch, capsys):
    """m-2: причина `--repropose` — константа; `--reason` молча не съедается."""
    from governance import task_bridge as tb

    _completed_run(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        tb.main(
            ["--run-id", "r-repropose", "--repropose", "approve-1-0-1", "--reason", "x"]
        )
    assert "--reason осмыслен только" in capsys.readouterr().err
