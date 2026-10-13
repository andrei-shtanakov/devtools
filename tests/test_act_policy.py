"""Набор политик из одного activation_commit (спека §6, Q3, Q8)."""

from __future__ import annotations

import pytest

from governance.act_policy import PolicyError, classify, mode_for, parse_policy_set
from tests.act_cases import COMMIT, FILES


def test_parses_whole_set() -> None:
    ps = parse_policy_set(COMMIT, FILES)
    assert ps.approvers == frozenset({"alice"})
    assert ps.mergers == frozenset({"df-reviewer[bot]"})
    assert mode_for(ps, "owner/product-b") == "review"
    assert mode_for(ps, "owner/unknown") is None
    assert set(ps.files_sha256) == set(FILES)


def test_classification_uses_set_not_local_copy() -> None:
    pytest.importorskip("steward")
    ps = parse_policy_set(COMMIT, FILES)
    assert classify(ps, "alice", "User") == "human"
    assert classify(ps, "ai-prosto", "User") == "agent"
    assert classify(ps, "stranger", "User") == "unknown"


def test_merger_bot_forms_agree() -> None:
    """Review Focus 2: `slug[bot]` (REST) и `slug` (GraphQL) — одна личность."""
    pytest.importorskip("steward")
    ps = parse_policy_set(COMMIT, FILES)
    rest = classify(ps, "df-reviewer[bot]", "Bot")
    assert rest == classify(ps, "df-reviewer", "Bot")


def test_intersection_of_humans_and_agents_refused() -> None:
    files = dict(FILES)
    files["policy/actors.yaml"] = (
        b"version: 1\nhuman_identities: [github:alice]\n"
        b"agent_identities: [github:alice]\n"
    )
    with pytest.raises(PolicyError, match="пересечение"):
        parse_policy_set(COMMIT, files)


@pytest.mark.parametrize("missing", sorted(FILES))
def test_missing_file_refused(missing: str) -> None:
    files = {k: v for k, v in FILES.items() if k != missing}
    with pytest.raises(PolicyError):
        parse_policy_set(COMMIT, files)


def test_unknown_mode_value_refused() -> None:
    files = dict(FILES)
    files["policy/modes.yaml"] = (
        b"schema: approval-policy-modes/v1\nmodes:\n  owner/product-a: maybe\n"
    )
    with pytest.raises(PolicyError):
        parse_policy_set(COMMIT, files)
