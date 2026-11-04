"""Shared trust types (installer spec §6.1) and their publisher mirror."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

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


def test_detail_byte_limit() -> None:
    ok = "я" * 256  # 512 bytes in UTF-8
    assert gov.Finding("token_write", "df-trust", ok).detail == ok
    with pytest.raises(ValueError):
        gov.Finding("token_write", "df-trust", ok + "x")
    with pytest.raises(ValueError):
        gov.Finding("not_descendant", "approval-policy", "")
    with pytest.raises(ValueError):
        gov.Finding("nope", "df-trust", "")


def test_range_descriptor_epoch_limit() -> None:
    rows = tuple((i, "a", "b") for i in range(64))
    assert gov.RangeDescriptor(rows, "d").ranges == rows
    with pytest.raises(ValueError):
        gov.RangeDescriptor(rows + ((64, "a", "b"),), "d")


def _shape(mod: object) -> dict[str, object]:
    return {
        "kinds": {k: set(v) for k, v in mod.KINDS.items()},  # type: ignore[attr-defined]
        "routes": {k: set(v) for k, v in mod.ROUTE_KINDS.items()},  # type: ignore[attr-defined]
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
