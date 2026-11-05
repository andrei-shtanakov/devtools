"""Shared trust types (installer spec §6.1) and their publisher mirror."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import get_args

import pytest

from df.publisher import trust_types as mirror
from governance import act_trust_types as gov

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_KINDS = {
    "not_descendant": {"df-trust"},
    "journal_transition": {"df-trust"},
    "journal_parse": {"df-trust"},
    "bootstrap_mismatch": {"df-trust"},
    "source_env_invalid": {"df-trust"},
    "history_unverifiable": {"df-trust"},
    "policy_history_break": {"approval-policy"},
    "ruleset_missing": {"approval-policy", "df-trust"},
    "rebase_allowed": {"approval-policy", "df-trust"},
    "not_public": {"approval-policy", "df-trust"},
    "token_write": {"approval-policy", "df-trust"},
    "policy_branch_mismatch": {"approval-policy"},
}


def test_kinds_closed_list_12_codes_16_combinations() -> None:
    assert {k: set(v) for k, v in gov.KINDS.items()} == EXPECTED_KINDS
    assert len(gov.KINDS) == 12
    assert sum(len(v) for v in gov.KINDS.values()) == 16


def test_route_kinds() -> None:
    history = set(EXPECTED_KINDS) - {"policy_history_break"}
    assert set(gov.ROUTE_KINDS) == {"perimeter-status", "trust-status", "merge"}
    assert gov.ROUTE_KINDS["perimeter-status"] == history
    assert gov.ROUTE_KINDS["trust-status"] == history
    assert gov.ROUTE_KINDS["merge"] == set(EXPECTED_KINDS)
    assert all(v <= set(gov.KINDS) for v in gov.ROUTE_KINDS.values())


@pytest.fixture(params=[gov, mirror], ids=["governance", "publisher"])
def mod(request: pytest.FixtureRequest) -> ModuleType:
    return request.param


def test_detail_byte_limit(mod: ModuleType) -> None:
    ok = "я" * 256  # 512 bytes in UTF-8
    assert mod.Finding("token_write", "df-trust", ok).detail == ok
    with pytest.raises(ValueError):
        mod.Finding("token_write", "df-trust", ok + "x")
    with pytest.raises(ValueError):
        mod.Finding("not_descendant", "approval-policy", "")
    with pytest.raises(ValueError):
        mod.Finding("nope", "df-trust", "")


def test_range_descriptor_epoch_limit(mod: ModuleType) -> None:
    rows = tuple((i, "a", "b") for i in range(64))
    assert mod.RangeDescriptor(rows, "d").ranges == rows
    with pytest.raises(ValueError):
        mod.RangeDescriptor(rows + ((64, "a", "b"),), "d")


def test_clip_detail_at_character_boundary(mod: ModuleType) -> None:
    # "x" shifts the 2-byte characters so the 512-byte cut falls mid-character
    text = "x" + "я" * 300
    clipped = mod.clip_detail(text)
    assert mod.detail_size(clipped) <= mod.MAX_DETAIL_BYTES
    assert clipped == text[: len(clipped)] and len(clipped) == 256
    mod.Finding("token_write", "df-trust", clipped)


def test_clip_detail_short_text_unchanged(mod: ModuleType) -> None:
    assert mod.clip_detail("short") == "short"
    assert mod.clip_detail("я" * 256) == "я" * 256


def test_clip_detail_control_characters(mod: ModuleType) -> None:
    clipped = mod.clip_detail("\x01" * 600)
    assert mod.detail_size(clipped) <= mod.MAX_DETAIL_BYTES
    assert clipped == "\x01" * 85  # six serialised bytes each


def test_clip_detail_mixed_escapes(mod: ModuleType) -> None:
    text = '"\\\x01ж😀' * 100
    clipped = mod.clip_detail(text)
    assert mod.detail_size(clipped) <= mod.MAX_DETAIL_BYTES
    assert text.startswith(clipped)
    assert mod.detail_size(text[: len(clipped) + 1]) > mod.MAX_DETAIL_BYTES
    mod.Finding("token_write", "df-trust", clipped)


def test_clip_detail_replaces_lone_surrogates(mod: ModuleType) -> None:
    clipped = mod.clip_detail("a\ud800b")
    assert clipped == "a\ufffdb"
    mod.Finding("token_write", "df-trust", clipped)
    with pytest.raises(ValueError):
        mod.Finding("token_write", "df-trust", "a\ud800b")


def test_detail_measured_on_serialised_form(mod: ModuleType) -> None:
    with pytest.raises(ValueError):
        mod.Finding("token_write", "df-trust", "\x01" * 512)
    assert mod.detail_size("\x01") == 6
    assert mod.detail_size("я") == 2


def _shape(mod: ModuleType) -> dict[str, object]:
    return {
        "kinds": {k: set(v) for k, v in mod.KINDS.items()},
        "routes": {k: set(v) for k, v in mod.ROUTE_KINDS.items()},
        "fields": {
            n: [f.name for f in dataclasses.fields(getattr(mod, n))]
            for n in (
                "Finding",
                "AcceptedBase",
                "PolicyPin",
                "RangeDescriptor",
                "SourceDescriptor",
            )
        },
    }


def test_mirror_equal_to_governance() -> None:
    assert _shape(mirror) == _shape(gov)
    assert mirror.MAX_DETAIL_BYTES == gov.MAX_DETAIL_BYTES
    assert mirror.MAX_EPOCHS == gov.MAX_EPOCHS


@pytest.mark.parametrize(
    "module", ["governance.act_trust_types", "df.publisher.trust_types"]
)
def test_no_third_party_imports(module: str) -> None:
    code = (
        f"import sys, {module}; print(sorted(m for m in sys.modules "
        "if m.split('.')[0] in ('yaml', 'steward')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert out.stdout.strip() == "[]"


def test_finding_and_ranges_refusals_named() -> None:
    """Each invariant of the shared types refuses with its own reason (3a Task 7:
    the identity table)."""
    with pytest.raises(ValueError, match="unknown finding kind"):
        gov.Finding("nope", "df-trust", "")
    with pytest.raises(ValueError, match="not allowed for role"):
        gov.Finding("policy_history_break", "df-trust", "")
    with pytest.raises(ValueError, match="detail over 512 bytes"):
        gov.Finding("token_write", "df-trust", "x" * 600)
    with pytest.raises(ValueError, match="more than 64 epochs"):
        gov.RangeDescriptor(((1, "a", "b"),) * 65, "d")


# --- the checker's contract as the publisher mirrors it (stage 3a, Task 10) ---


def test_trust_routes_mirrors_checker_constants() -> None:
    """`df.publisher.trust_routes` repeats the checker's contract (`df` never
    imports `governance`); each value is pinned to its source."""
    pytest.importorskip("steward")
    from df.publisher import trust_routes as routes
    from governance import (
        act_history_snapshot,
        act_precheck,
        act_precheck_v2,
        act_trust_findings,
        act_trust_pair,
    )

    assert routes.REQUEST_SCHEMA_V2 == act_precheck.REQUEST_SCHEMA_V2
    assert routes.VERDICT_SCHEMA_V2 == act_precheck.VERDICT_SCHEMA_V2
    assert routes.PRECHECK_VERSION_V2 == act_precheck.PRECHECK_VERSION_V2
    assert routes.TREE_INCOMPLETE == act_precheck.TREE_INCOMPLETE
    assert routes.RULES_VERSION == act_precheck_v2.RULES_VERSION
    assert routes.validation(routes.RULES_VERSION, "sha256:x") == (
        act_precheck_v2.validation_of("sha256:x")
    )
    assert routes.VERDICT_LIMIT == act_trust_findings.VERDICT_LIMIT
    assert routes.MAX_FULL_NAME_BYTES == act_trust_findings.MAX_FULL_NAME_BYTES
    assert routes.MAX_BRANCH_BYTES == act_trust_findings.MAX_BRANCH_BYTES
    assert routes.MAX_INT == act_precheck_v2.MAX_INT
    assert routes.HISTORY_TOKEN_REQUIRED == act_trust_pair.HISTORY_TOKEN_REQUIRED
    assert routes.UNVERIFIED == act_trust_pair.UNVERIFIED
    assert routes.SOURCE_FINDINGS == act_precheck_v2.SOURCE_FINDINGS
    assert set(routes.RELATIONS) == set(get_args(act_trust_pair.Relation))
    assert routes.BRANCH.pattern == act_precheck._BRANCH.pattern
    assert routes.FULL_NAME.pattern == act_history_snapshot._FULL_NAME.pattern
    assert routes.HOST.pattern == act_precheck_v2._HOST.pattern
    outcomes = {
        act_precheck_v2.ALLOW,
        act_precheck_v2.REFUSE,
        act_precheck_v2.UNSET,
        act_precheck_v2.UNSUPPORTED,
    }
    assert {routes.ALLOW, routes.REFUSE, routes.UNSET, routes.UNSUPPORTED} == outcomes


@dataclasses.dataclass(frozen=True)
class _Epoch:
    epoch: int
    anchor: str
    last_confirmed: str | None


@pytest.mark.parametrize("count", [1, 2, 7, 64])
def test_range_digest_mirror_byte_equal(count: int) -> None:
    """Carry 10: the publisher's `range_digest` gives the digest
    `act_trust_findings.range_descriptor` gives, on the same ranges, and
    `check_ranges` accepts that descriptor."""
    pytest.importorskip("steward")
    from df.publisher import trust_routes as routes
    from governance import act_trust_findings

    p_sha = "f" * 40
    epochs = [
        _Epoch(n, f"{n:040x}", None if n == count else f"{n + 100:040x}")
        for n in range(1, count + 1)
    ]
    desc = act_trust_findings.range_descriptor(epochs, p_sha)
    assert routes.range_digest(desc.ranges) == desc.digest
    mirrored = mirror.RangeDescriptor(desc.ranges, desc.digest)
    assert routes.check_ranges(
        mirrored, last_epoch=count, p_sha=p_sha, stored_digest=desc.digest
    )
    # a non-ASCII value: the digest would differ with `ensure_ascii=True`
    odd = ((1, "ж" * 40, p_sha),)
    assert (
        routes.range_digest(odd)
        == act_trust_findings.hashlib.sha256(
            act_trust_findings._canonical([list(r) for r in odd])
        ).hexdigest()
    )
