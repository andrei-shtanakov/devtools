"""Candidate-срез read-модели бандла (спека §4): чистое чтение, никаких git-facts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance.bundle_state import BundleState, NodeState, candidate_state
from tests.governance_fixtures.bundles import (
    BEHAVIOUR_NO_UPSTREAM_MD,
    REQUIREMENTS_MD,
    make_bundle,
    make_bundle_with_behaviour,
    make_profile,
)


def test_good_bundle_is_candidate_valid(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    state = candidate_state(profile, bundle)
    assert isinstance(state, BundleState)
    assert state.error_count == 0
    assert state.required_absent == ()
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["behaviour-spec"].status == "candidate_valid"
    assert by_id["requirements"].status == "candidate_valid"
    # tasks несёт delegate в профиле — живёт вне бандла, не absent (I-5).
    assert by_id["tasks"].status == "delegated"


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
    assert state.required_absent == ("behaviour-spec",)


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


def test_upstream_absent_marks_blocked(tmp_path: Path) -> None:
    """behaviour-spec присутствует, но requirements нет и в бандле, и в пине —
    свежеавторенный на S3 документ (пины штампуются approval'ом steward, не
    автором). Гейты по behaviour-spec физически не могли отработать: статус
    обязан читаться как отказ, не как candidate_valid (финальное ревью C-1)."""
    profile = make_profile(tmp_path)
    bundle = tmp_path / "spec"
    bundle.mkdir()
    (bundle / "15-behaviour-spec.md").write_text(BEHAVIOUR_NO_UPSTREAM_MD)
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "blocked"
    assert any("GC-UPSTREAM-ABSENT" in f for f in beh.findings)
    assert state.error_count > 0
    assert state.required_absent == ("requirements",)


def test_unpinned_upstream_edge_is_draft(tmp_path: Path) -> None:
    """requirements И behaviour-spec оба присутствуют в бандле, но у
    behaviour-spec нет upstream_hashes вообще — объявленное профилем ребро
    requirements->behaviour-spec без пина. Спека требует пины в том же PR:
    их отсутствие — блокирующее нарушение, не неизвестность-как-успех
    (замена рулинга I-3 п.3 после codex-ревью PR #87)."""
    profile = make_profile(tmp_path)
    bundle = make_bundle_with_behaviour(tmp_path, BEHAVIOUR_NO_UPSTREAM_MD)
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "draft"
    assert any("GC-UNPINNED" in f for f in beh.findings)
    assert state.error_count > 0


def test_pinned_upstream_edge_stays_clean(tmp_path: Path) -> None:
    """Регрессия: хороший бандл (пин на месте) не должен получить
    GC-UNPINNED — иначе правка накрыла бы и корректный случай."""
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "candidate_valid"
    assert not any("GC-UNPINNED" in f for f in beh.findings)
    assert state.error_count == 0


def test_empty_bundle_dir_is_not_clean(tmp_path: Path) -> None:
    """Регрессия: пустой каталог бандла не должен читаться как пройденный
    контур. Раньше `error_count == 0` на пустом bundle_dir формально
    удовлетворял «ноль findings уровня error» (спека §7 контур 1) — теперь
    required_absent делает отсутствие обязательных узлов видимым (C-1)."""
    profile = make_profile(tmp_path)
    bundle = tmp_path / "empty-spec"
    bundle.mkdir()
    state = candidate_state(profile, bundle)
    assert state.error_count == 0
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["requirements"].status == "absent"
    assert by_id["behaviour-spec"].status == "absent"
    assert by_id["tasks"].status == "delegated"
    assert state.required_absent == ("requirements", "behaviour-spec")


def test_orphan_findings_land_in_bundle_findings(tmp_path: Path) -> None:
    """Два файла претендуют на один узел (behaviour-spec): второй получает
    GC-DUP с `node_id=None` и per-node вид его теряет — findings обязаны
    остаться видимыми через bundle_findings, а не молча исчезнуть (I-4)."""
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    dup_text = (bundle / "15-behaviour-spec.md").read_text()
    (bundle / "16-behaviour-spec-dup.md").write_text(dup_text)
    state = candidate_state(profile, bundle)
    assert state.error_count > 0
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["behaviour-spec"].status == "candidate_valid"
    assert any("GC-DUP" in f for f in state.bundle_findings)


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
