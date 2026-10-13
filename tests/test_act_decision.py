"""D1–D3 на срезе T (спека §3)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import pytest

pytest.importorskip("steward")  # decide → classify → steward classify_actor

from governance.act_decision import (  # noqa: E402
    D1_NO_ACT,
    D3_MERGER,
    I_BYTES,
    I_CONTENT,
    Accepted,
    Decision,
    DeliveryView,
    Rejected,
    ReviewView,
    WaitingDelivery,
    decide,
)
from governance.act_policy import parse_policy_set  # noqa: E402
from governance.act_request import NodeEntry, NodeRequest, PolicyPin  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402
from tests.act_cases import COMMIT, FILES, code  # noqa: E402

HEAD = "1" * 40
PATH = "workstreams/ws-7/requirements.md"
REQ = NodeRequest(
    "approve-2-1-1",
    "ws-7",
    "owner/product-b",
    {"requirements": NodeEntry(PATH, "h" * 40, 1)},
    {},
    PolicyPin("owner/approval-policy", "main", COMMIT, {}),
)
DELIVERY = DeliveryView(
    merged=True,
    merge_commit="m" * 40,
    merged_at="2026-10-12T10:00:00Z",
    merged_by_login="df-reviewer[bot]",
    merged_by_type_hint="Bot",
    head_at_merge=HEAD,
    in_base=True,
    blobs={PATH: ("b" * 40, "b" * 40)},
)
APPROVE = ReviewView("11", "alice", "User", "APPROVED", HEAD)
POLICY = parse_policy_set(COMMIT, FILES)


def run(**over: object) -> Decision:
    args: dict[str, object] = {
        "request": REQ,
        "head_sha": HEAD,
        "delivery": DELIVERY,
        "effective": {"alice": APPROVE},
        "head_self_hashes": {PATH: "h" * 40},
        "policy": POLICY,
    }
    args.update(over)
    return decide(**args)  # type: ignore[arg-type]


def test_happy_path_accepted() -> None:
    result = run()
    assert result == Accepted("m" * 40, "df-reviewer[bot]")
    assert isinstance(result, Accepted)
    assert (result.review_id, result.login) == ("11", "alice")


def test_not_merged_waits() -> None:
    not_merged = dataclasses.replace(DELIVERY, merged=False)
    assert run(delivery=not_merged) == WaitingDelivery()


@pytest.mark.parametrize(
    "effective",
    [
        {"alice": dataclasses.replace(APPROVE, commit_id="0" * 40)},
        {"alice": dataclasses.replace(APPROVE, state="DISMISSED")},
        {"mallory": dataclasses.replace(APPROVE, login="mallory")},
        {"alice": dataclasses.replace(APPROVE, type_hint="Bot")},
        {},
    ],
    ids=["stale-commit", "dismissed", "not-approver", "bot-hint", "no-review"],
)
def test_d1_refusals(effective: Mapping[str, ReviewView]) -> None:
    assert isinstance(run(effective=effective), Rejected)


def test_latest_review_commit_wins() -> None:
    """Review Focus 3: эффективный — последний; старый commit_id не подтягивается."""
    newer_on_old = dataclasses.replace(APPROVE, review_id="12", commit_id="0" * 40)
    assert isinstance(run(effective={"alice": newer_on_old}), Rejected)


def test_head_moved_before_merge_rejected() -> None:
    moved = dataclasses.replace(DELIVERY, head_at_merge="2" * 40)
    assert isinstance(run(delivery=moved), Rejected)


def test_bytes_and_content_are_separate_invariants() -> None:
    """Приёмка №18: I-bytes и I-content — разные исходы."""
    bytes_moved = dataclasses.replace(DELIVERY, blobs={PATH: ("c" * 40, "b" * 40)})
    assert code(run(delivery=bytes_moved)) == I_BYTES
    assert code(run(head_self_hashes={PATH: "x" * 40})) == I_CONTENT


def test_merger_human_approver_allowed() -> None:
    human = dataclasses.replace(
        DELIVERY, merged_by_login="alice", merged_by_type_hint="User"
    )
    assert isinstance(run(delivery=human), Accepted)


def test_merger_graphql_bot_form_accepted() -> None:
    """C8: GraphQL-форма логина без `[bot]` — тот же мержер, что и REST."""
    graphql = dataclasses.replace(DELIVERY, merged_by_login="df-reviewer")
    assert isinstance(run(delivery=graphql), Accepted)


def test_agent_in_approvers_but_not_merger_refused() -> None:
    """Приёмка №16 / D3: agent проходит только через allowlist merger."""
    files = dict(FILES)
    files["policy/approvers.env"] = (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice,ai-prosto\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    )
    policy = parse_policy_set(COMMIT, files)
    agent = dataclasses.replace(
        DELIVERY, merged_by_login="ai-prosto", merged_by_type_hint="User"
    )
    assert code(run(delivery=agent, policy=policy)) == D3_MERGER


def test_not_in_base_rejected() -> None:
    not_in_base = dataclasses.replace(DELIVERY, in_base=False)
    assert isinstance(run(delivery=not_in_base), Rejected)


def test_key_login_mismatch_unavailable() -> None:
    """Ключ эффективного ревью ≠ его логин — факт искажён, не установлен."""
    forged = ReviewView("1", "mallory[bot]", "User", "APPROVED", HEAD)
    assert isinstance(run(effective={"alice": forged}), Unavailable)


def test_bot_form_approver_with_user_hint_not_act() -> None:
    """D1: `alice[bot]` — бот при любой подсказке, даже если внесён в approvers."""
    files = dict(FILES)
    files["policy/approvers.env"] = (
        b"AUTHORIZED_APPROVER_ACCOUNTS=alice,alice[bot]\n"
        b"AUTHORIZED_MERGER_ACCOUNTS=df-reviewer[bot]\n"
    )
    policy = parse_policy_set(COMMIT, files)
    bot = ReviewView("1", "alice[bot]", "User", "APPROVED", HEAD)
    assert code(run(effective={"alice[bot]": bot}, policy=policy)) == D1_NO_ACT


def test_human_merger_with_bot_hint_not_via_a() -> None:
    """D3(а): `type_hint = Bot` закрывает путь человека; в mergers его нет."""
    hinted = dataclasses.replace(
        DELIVERY, merged_by_login="alice", merged_by_type_hint="Bot"
    )
    assert code(run(delivery=hinted)) == D3_MERGER


def test_bot_agent_outside_merger_allowlist_refused() -> None:
    """Приёмка №6: бот-agent вне allowlist merger — доставка не засчитана."""
    other = dataclasses.replace(
        DELIVERY, merged_by_login="other[bot]", merged_by_type_hint="Bot"
    )
    assert code(run(delivery=other)) == D3_MERGER


@pytest.mark.parametrize(
    "field_name",
    [
        "merge_commit",
        "merged_at",
        "merged_by_login",
        "merged_by_type_hint",
        "head_at_merge",
        "in_base",
    ],
)
def test_missing_delivery_fact_unavailable(field_name: str) -> None:
    """§5.5: неустановленный факт доставки — `Unavailable`, не вердикт."""
    missing = dataclasses.replace(DELIVERY, **{field_name: None})
    assert isinstance(run(delivery=missing), Unavailable)


def test_unread_blob_unavailable() -> None:
    """Пары blob пути нет — байты не прочитаны, отказ не установлен."""
    unread = dataclasses.replace(DELIVERY, blobs={})
    assert isinstance(run(delivery=unread), Unavailable)


def test_missing_self_hash_unavailable() -> None:
    """Хеш проекции не вычислен — I-content не установлен."""
    assert isinstance(run(head_self_hashes={}), Unavailable)


def test_deleted_at_merge_is_rejected_bytes() -> None:
    """Установленное удаление пути в merge-коммите — `(None, sha)` → I_BYTES."""
    deleted = dataclasses.replace(DELIVERY, blobs={PATH: (None, "b" * 40)})
    result = run(delivery=deleted)
    assert isinstance(result, Rejected)
    assert result.code == I_BYTES
