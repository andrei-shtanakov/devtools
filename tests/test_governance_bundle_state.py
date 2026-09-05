"""Candidate-срез read-модели бандла (спека §4): чистое чтение, никаких git-facts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance.bundle_state import BundleState, NodeState, candidate_state
from governance.stale_adapter import blob_sha1
from tests.governance_fixtures.bundles import (
    BEHAVIOUR_NO_UPSTREAM_MD,
    REQUIREMENTS_MD,
    make_bundle,
    make_bundle_with_behaviour,
    make_profile,
)

_DESIGN_PROFILE_YAML = """\
profile: mini-design
solo_auto_approve: true
artifacts:
  - {id: charter, template: charter.md, owner_role: product, upstream: []}
  - id: requirements
    template: requirements.md
    owner_role: product
    upstream: [charter]
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: product
    upstream: [requirements]
  - {id: design, template: design.md, owner_role: architects,
     upstream: [requirements, behaviour-spec]}
"""

_DESIGN_ROLES_YAML = """\
version: 1
slug_pattern: "^[a-z][a-z-]*$"
roles:
  - {slug: product, display: Product}
  - {slug: architects, display: Architects}
"""


def _make_design_profile(tmp_path: Path) -> Path:
    """4-узловой профиль (charter/requirements/behaviour-spec/design) —
    отдельный от общей `mini`-фикстуры (та трёхузловая, без design):
    Task 4 Step 5 нужен профиль, где design — required-узел (default),
    не влияя на остальные тесты этого файла."""
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir(exist_ok=True)
    (prof_dir / "roles.yaml").write_text(_DESIGN_ROLES_YAML)
    profile = prof_dir / "mini-design.yaml"
    profile.write_text(_DESIGN_PROFILE_YAML)
    return profile


def _make_design_bundle(tmp_path: Path) -> Path:
    """charter+requirements+behaviour-spec, БЕЗ design (вызывающий дописывает)."""
    bundle = tmp_path / "spec"
    bundle.mkdir(exist_ok=True)
    (bundle / "00-charter.md").write_text(
        "---\nspec_stage: charter\nstatus: approved\nowner_role: product\n"
        "---\n# Charter\n"
    )
    requirements_text = (
        "---\nspec_stage: requirements\nstatus: approved\n"
        "owner_role: product\n---\n"
        "#### FR-01: Список\n**Priority**: Must\n\nПользователь видит список.\n"
    )
    (bundle / "10-requirements.md").write_text(requirements_text)
    req_pin = blob_sha1(requirements_text)
    (bundle / "15-behaviour-spec.md").write_text(
        "---\nspec_stage: behaviour-spec\nstatus: draft\n"
        "owner_role: product\ntraces_to: [requirements]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        "---\n#### BEH-01: Просмотр списка\n`traces: [FR-01]`\n"
        "- **checked_by**: `status: planned` `kind: e2e` `owner: qa` "
        "`target: tests/test_x.py`\n"
    )
    return bundle


def _write_design_node(bundle: Path, *, status: str) -> None:
    req_pin = blob_sha1((bundle / "10-requirements.md").read_text())
    beh_pin = blob_sha1((bundle / "15-behaviour-spec.md").read_text())
    (bundle / "20-design.md").write_text(
        "---\n"
        "spec_stage: design\n"
        f"status: {status}\n"
        "owner_role: architects\n"
        "traces_to: [requirements, behaviour-spec]\n"
        "upstream_hashes:\n"
        f'  requirements: "{req_pin}"\n'
        f'  behaviour-spec: "{beh_pin}"\n'
        "---\n"
        "Открытых архитектурных вопросов нет (входной набор пуст)\n"
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


# --- Task 4 Step 5: design — required-узел, отсутствие/статус в read-model --


def test_bundle_state_requires_design(tmp_path: Path) -> None:
    """4-узловой профиль (charter/requirements/behaviour-spec/design):
    design — required (профильный default), поэтому его отсутствие в
    бандле обязано всплывать в `required_absent`; его наличие (в любом
    approval-статусе frontmatter) — обязано читаться как валидный
    candidate-узел, не как отсутствующий."""
    from steward.gatecheck.checks import collect_bundle
    from steward.graph import load_profile
    from steward.roles import load_roles_catalog

    profile = _make_design_profile(tmp_path)
    bundle = _make_design_bundle(tmp_path)

    # 1) design в required (профильный default `required: True`) — без
    # 20-design.md он обязан всплыть в required_absent.
    state = candidate_state(profile, bundle)
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["design"].status == "absent"
    assert "design" in state.required_absent

    # 2) design со `status: draft` — присутствует, читается валидным
    # candidate-узлом (не absent, без error-находок); raw frontmatter —
    # не approved.
    _write_design_node(bundle, status="draft")
    state = candidate_state(profile, bundle)
    by_id = {n.node_id: n for n in state.nodes}
    assert "design" not in state.required_absent
    assert by_id["design"].status == "candidate_valid"

    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    artifacts, _findings = collect_bundle(graph, bundle)
    design_artifact = next(a for a in artifacts if a.node_id == "design")
    assert design_artifact.meta.status == "draft"

    # 3) после штампа (status: draft -> approved) — та же картина: design
    # остаётся валидным candidate-узлом, а raw frontmatter теперь approved.
    _write_design_node(bundle, status="approved")
    state = candidate_state(profile, bundle)
    by_id = {n.node_id: n for n in state.nodes}
    assert "design" not in state.required_absent
    assert by_id["design"].status == "candidate_valid"

    artifacts, _findings = collect_bundle(graph, bundle)
    design_artifact = next(a for a in artifacts if a.node_id == "design")
    assert design_artifact.meta.status == "approved"
