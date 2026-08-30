"""Candidate-срез read-модели бандла (спека §4): чистое чтение, никаких git-facts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance.bundle_state import BundleState, NodeState, candidate_state
from tests.governance_fixtures.bundles import (
    REQUIREMENTS_MD,
    make_bundle,
    make_profile,
)


def test_good_bundle_is_candidate_valid(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    state = candidate_state(profile, bundle)
    assert isinstance(state, BundleState)
    assert state.error_count == 0
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["behaviour-spec"].status == "candidate_valid"
    assert by_id["requirements"].status == "candidate_valid"


def test_bad_bundle_is_draft_with_findings(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    state = candidate_state(profile, bundle)
    assert state.error_count > 0
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "draft"
    assert beh.findings  # каждая строка несёт rule_id
    assert any("GC-" in f for f in beh.findings)


def test_missing_node_is_absent(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    (bundle / "15-behaviour-spec.md").unlink()
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "absent"


def test_stale_pin_marks_node_stale(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    (bundle / "10-requirements.md").write_text(
        REQUIREMENTS_MD + "\n## FR-2 (Must) Новое\n"
    )
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "stale"
    assert state.error_count > 0  # stale блокирует (спека §7: GC-STALE — ноль)


def test_no_git_facts_are_used(tmp_path: Path, monkeypatch) -> None:
    """Регрессия спеки §3/§9: candidate-срез не строит GitFacts вовсе."""
    import steward.gatecheck.git_facts as gf

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("candidate_state не должен трогать git_facts")

    monkeypatch.setattr(gf, "GitFacts", boom)
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    assert candidate_state(profile, bundle).error_count == 0
