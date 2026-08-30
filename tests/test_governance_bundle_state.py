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
    """Регрессия спеки §3/§9: candidate-срез не строит git-facts и не зовёт
    git-зависимые проверки. Ловушки — на конкретных реализациях и на
    run_checks (GitFacts — Protocol, его патчить бессмысленно).

    run_checks патчится в двух местах: на модуле steward.gatecheck.checks
    (где он определён) и на governance.bundle_state (куда его мог бы
    затянуть будущий `from steward.gatecheck.checks import run_checks`,
    ``raising=False`` — атрибута там сейчас нет). Прямой импорт создаёт
    отдельное локальное имя в момент импорта, и патч только первого места
    его не перехватывает (подтверждено экспериментом — см. отчёт задачи)."""

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("candidate_state не должен трогать git-facts")

    monkeypatch.setattr(
        "steward.gatecheck.git_facts.LiveGitFacts.__init__", boom
    )
    monkeypatch.setattr(
        "steward.gatecheck.git_facts.InjectedGitFacts.__init__", boom
    )
    monkeypatch.setattr("steward.gatecheck.checks.run_checks", boom)
    monkeypatch.setattr(
        "governance.bundle_state.run_checks", boom, raising=False
    )
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    assert candidate_state(profile, bundle).error_count == 0
