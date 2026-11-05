"""Shared trust types (installer spec §6.1) and their publisher mirror."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
