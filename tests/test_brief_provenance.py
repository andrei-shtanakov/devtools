"""Предикаты акта (§11.4.2) и политики (§11.4.3) на стенде форджа.

Каждый тест портит РОВНО один факт согласованного мира; сам мир — двойник
(`test_consistent_world_passes`).
"""

from __future__ import annotations

import pytest

from governance import approval_request as ar
from governance import brief_provenance as bp
from tests.approval_request_cases import DEFECTS
from tests.forge_fake import (
    C1,
    C2,
    MERGE,
    POLICY_REPO,
    PR,
    REPO,
    SIGNED,
    P,
    consistent_world,
    request,
)
from tests.provenance_cases import (
    ACT_DEFECTS,
    ACT_UNAVAILABLE,
    OPERATOR_DEFECTS,
    POLICY_DEFECTS,
    POLICY_UNAVAILABLE,
    REQUEST,
    ids,
)


@pytest.fixture()
def world(monkeypatch):
    return consistent_world(monkeypatch)


def _act(forge) -> bp.Act:
    act = bp.read_act(forge, REPO, PR)
    assert isinstance(act, bp.Act), act
    return act


def _refused(result, reason: str) -> None:
    assert isinstance(result, bp.Refusal), result
    assert result.reason == reason, result


def test_consistent_world_passes(world) -> None:
    act = _act(world)
    assert bp.check_operator_brief(act, SIGNED) is None
    ok = bp.check_policy(world, act)
    assert isinstance(ok, bp.PolicyOk) and ok.current == ok.working == P


def test_act_record_has_spec_fields(world) -> None:
    record = _act(world).as_record()
    assert set(record) == {
        "repo",
        "pr",
        "dir",
        "merge_commit",
        "approver",
        "approved_at",
        "self_hash",
        "act_policy_sha",
    }


def test_manual_envelope_mirroring_the_merge_is_accepted(world) -> None:  # T17
    # Конверт фикстуры подписан `stamp` соседа, но ничем не отличим от
    # вписанного руками точного зеркала: авторство записи не проверяется.
    assert bp.check_operator_brief(_act(world), SIGNED) is None


@pytest.mark.parametrize(("_id", "spoil", "reason"), ACT_DEFECTS, ids=ids(ACT_DEFECTS))
def test_each_act_defect_refuses(world, _id, spoil, reason) -> None:  # T19–T22, T25
    spoil(world)
    _refused(bp.read_act(world, REPO, PR), reason)


@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_merged_request_defects_refuse(world, case, mutate, _m) -> None:  # T21a/T21b
    world.files[(MERGE, REQUEST)] = mutate(ar.render(request()))
    _refused(bp.read_act(world, REPO, PR), "request_invalid")


@pytest.mark.parametrize("fact", ACT_UNAVAILABLE)
def test_unavailable_is_retry_not_rejection(world, fact) -> None:  # T31
    world.unavailable_facts.add(fact)
    got = bp.read_act(world, REPO, PR)
    _refused(got, "forge_unavailable")
    assert got.retry and "не одобрено" not in got.detail


@pytest.mark.parametrize(
    ("_id", "spoil", "text", "reason"), OPERATOR_DEFECTS, ids=ids(OPERATOR_DEFECTS)
)
def test_operator_brief_defects(
    world, _id, spoil, text, reason
) -> None:  # T14–T18, T22
    spoil(world)
    _refused(bp.check_operator_brief(_act(world), text), reason)


def test_drift_without_reconfirm_stops(world) -> None:  # T26
    world.add_policy(C1)
    got = bp.check_policy(world, _act(world))
    _refused(got, "upstream_policy_drift")
    assert bp.reconfirm_line(POLICY_REPO, C1) in got.detail


def test_valid_reconfirm_continues(world) -> None:  # T27
    world.add_policy(C1)
    world.reconfirm(C1)
    got = bp.check_policy(world, _act(world))
    assert isinstance(got, bp.PolicyOk) and got.working == C1


@pytest.mark.parametrize(
    ("_id", "spoil", "reason"), POLICY_DEFECTS, ids=ids(POLICY_DEFECTS)
)
def test_each_policy_defect_refuses(world, _id, spoil, reason) -> None:  # T26, T27
    spoil(world)
    _refused(bp.check_policy(world, _act(world)), reason)


def test_reconfirm_does_not_replace_merger_membership(world) -> None:  # T27
    world.add_policy(C1, "helper")
    world.reconfirm(C1, author="helper")
    _refused(bp.check_policy(world, _act(world)), "merger_not_in_current_policy")


def test_edited_or_deleted_reconfirm_stops_counting(world) -> None:  # Review Focus
    world.add_policy(C1)
    world.reconfirm(C1)
    assert isinstance(bp.check_policy(world, _act(world)), bp.PolicyOk)
    world.comments[PR] = []
    _refused(bp.check_policy(world, _act(world)), "upstream_policy_drift")


def test_latest_reconfirm_wins(world) -> None:  # T28 (логика)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2)
    _refused(bp.check_policy(world, _act(world)), "upstream_policy_drift")
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    assert bp.check_policy(world, _act(world)).working == C2


@pytest.mark.parametrize("fact", POLICY_UNAVAILABLE)
def test_policy_unavailable_is_retry(world, fact) -> None:
    act = _act(world)
    world.unavailable_facts.add(fact)
    got = bp.check_policy(world, act)
    _refused(got, "forge_unavailable")
    assert got.retry


def test_unavailable_while_judging_reconfirm_is_retry(world) -> None:  # P5
    world.add_policy(C1)
    world.reconfirm(C1)
    act = _act(world)
    world.unavailable_facts.add("history")
    got = bp.check_policy(world, act)
    _refused(got, "forge_unavailable")
    assert got.retry


def test_malformed_comments_stop_policy_with_retry(monkeypatch) -> None:
    """Цепочка адаптер → политика: неполный ответ о подтверждениях не даёт
    продолжить по прежнему W, а требует повтора."""
    from governance.ops import RealOps
    from tests.test_governance_ops_brief_facts import _comment, _gh, _page

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1)
    act = bp.read_act(world, "owner/alpha", 7)
    assert isinstance(act, bp.Act)
    node = _comment()
    del node["author"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    world.pr_comments_fact = RealOps().pr_comments_fact  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry


def test_incomplete_read_of_later_reconfirm_policy_is_retry(monkeypatch) -> None:
    """A8: неполное чтение состава позднего подтверждения не позволяет
    продолжить по прежнему W — это retry, а не «подтверждение недействительно»."""
    from governance.facts import unavailable

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2, head=False)
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    act = bp.read_act(world, REPO, PR)
    assert isinstance(act, bp.Act)
    real = world.repo_file_fact

    def flaky(repo_slug, sha, path):
        if sha == C2:
            return unavailable("ответ без поля file")
        return real(repo_slug, sha, path)

    world.repo_file_fact = flaky  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry


@pytest.mark.parametrize(
    "blob",
    [
        {"text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n"},
        {"text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n", "isBinary": False},
        {
            "text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n",
            "isBinary": None,
            "isTruncated": False,
        },
    ],
    ids=["no-flags", "no-truncated", "binary-null"],
)
def test_incomplete_policy_blob_of_later_reconfirm_is_retry(monkeypatch, blob) -> None:
    """A10, цепочка адаптер RealOps.repo_file_fact → check_policy: неполный ответ о
    составе позднего подтверждения — retry, а не продолжение по прежнему W."""
    import json
    import subprocess

    from governance.ops import RealOps

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2, head=False)
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    act = bp.read_act(world, REPO, PR)
    assert isinstance(act, bp.Act)
    real = world.repo_file_fact
    payload = {"data": {"repository": {"object": {"file": {"object": blob}}}}}

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(payload), stderr=""
        )

    def via_adapter(repo_slug, sha, path):
        if sha != C2:
            return real(repo_slug, sha, path)
        monkeypatch.setattr(subprocess, "run", fake_run)
        return RealOps().repo_file_fact(repo_slug, sha, path)

    world.repo_file_fact = via_adapter  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry


def _unknown_sha_via_adapter(monkeypatch, world, unknown: str) -> None:
    """`policy_version_fact_at` для `unknown` — настоящий адаптер RealOps на
    записанных ответах GitHub: compare → 404, GraphQL → `object: null`."""
    import subprocess

    from governance.ops import RealOps

    real = world.policy_version_fact_at
    answers = [
        subprocess.CompletedProcess([], 1, stdout="", stderr="HTTP 404"),
        subprocess.CompletedProcess(
            [], 0, stdout='{"data":{"repository":{"object":null}}}', stderr=""
        ),
    ]

    def fact_at(repo_slug, ref, path, sha):
        if sha != unknown:
            return real(repo_slug, ref, path, sha)
        queue = list(answers)
        monkeypatch.setattr(subprocess, "run", lambda argv, **kw: queue.pop(0))
        return RealOps().policy_version_fact_at(repo_slug, ref, path, sha)

    world.policy_version_fact_at = fact_at  # type: ignore[method-assign]


def test_reconfirm_with_unknown_sha_is_skipped_not_retried(monkeypatch) -> None:
    """Ревью #571: чужой комментарий с выдуманным sha — недействительное
    подтверждение (дрейф остаётся), а не вечное «повторите» (T27)."""
    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    unknown = "9" * 40
    world.reconfirm(unknown, author="stranger")
    _unknown_sha_via_adapter(monkeypatch, world, unknown)
    got = bp.check_policy(world, _act(world))
    _refused(got, "upstream_policy_drift")
    assert not got.retry


def test_request_pin_to_unknown_sha_is_final_refusal(monkeypatch) -> None:
    """Смерженная заявка с несуществующим `policy.sha` — окончательный отказ
    `not_policy_version` (T48), а не retry."""
    world = consistent_world(monkeypatch)
    unknown = "9" * 40
    world.files[(MERGE, REQUEST)] = ar.render(request(policy_sha=unknown))
    _unknown_sha_via_adapter(monkeypatch, world, unknown)
    got = bp.read_act(world, REPO, PR)
    _refused(got, "not_policy_version")
    assert not got.retry
