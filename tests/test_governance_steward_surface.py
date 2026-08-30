"""Characterization пинованного steward: поверхность трёх символов и их gate_id.

Ломается при bump'е пина, если steward изменил контракт, — это фича: план этапа B
строится на доказанной поверхности (спека §12 п.0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.gatecheck.behaviour import check_behaviour_spec
from steward.gatecheck.checks import Artifact, Finding, collect_bundle
from steward.gatecheck.trace_matrix import build_trace_matrix
from steward.graph import load_profile
from steward.roles import load_roles_catalog

from tests.governance_fixtures.bundles import (
    BEHAVIOUR_NO_CHECKED_MD,
    make_bundle,
    make_bundle_with_behaviour,
    make_profile,
)


def _graph(tmp_path: Path):
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    return load_profile(profile, roles)


def test_collect_bundle_surface(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, findings = collect_bundle(graph, bundle)
    assert all(isinstance(a, Artifact) for a in artifacts)
    assert all(isinstance(f, Finding) for f in findings)
    node_ids = {a.node_id for a in artifacts}
    assert {"requirements", "behaviour-spec"} <= node_ids
    # файл без frontmatter не становится managed-артефактом
    assert not any(a.path == "notes.md" for a in artifacts)


def test_finding_shape_is_pinned(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_behaviour_spec(graph, artifacts)
    assert findings, "плохой behaviour-spec обязан дать findings"
    f = findings[0]
    assert f.severity in ("error", "warn")
    assert isinstance(f.rule_id, str) and f.rule_id
    assert isinstance(f.artifact, str)
    assert isinstance(f.message, str)


def test_behaviour_gate_ids(tmp_path: Path) -> None:
    """Полный набор gate_id, который выдаёт check_behaviour_spec на плохом бандле.

    Положительное равенство (не подмножество, финальное ревью I-1): если
    bump пина снимет или переименует один из двух gate_id, множество сожмётся
    и тест обязан упасть, а не остаться зелёным.
    """
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    artifacts, _ = collect_bundle(graph, bundle)
    ids = {f.rule_id for f in check_behaviour_spec(graph, artifacts)}
    assert ids == {"GC-BEH-TRACE", "GC-BEH-COVERAGE"}, ids


def test_behaviour_gate_ids_check_planned(tmp_path: Path) -> None:
    """Третий gate_id, GC-CHECK-PLANNED, не триггерится BEHAVIOUR_BAD_MD (его
    сценарий не трейсит ничего, так что _check_planned его пропускает) —
    зафиксировать его отдельной фикстурой: сценарий трейсит Must-FR, но без
    checked_by-биндинга (`behaviour.py:184-198`, финальное ревью I-1)."""
    graph = _graph(tmp_path)
    bundle = make_bundle_with_behaviour(tmp_path, BEHAVIOUR_NO_CHECKED_MD)
    artifacts, _ = collect_bundle(graph, bundle)
    ids = {f.rule_id for f in check_behaviour_spec(graph, artifacts)}
    assert ids == {"GC-CHECK-PLANNED"}, ids


def test_good_bundle_is_clean(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, collect_findings = collect_bundle(graph, bundle)
    beh_findings = check_behaviour_spec(graph, artifacts)
    errors = [
        f for f in [*collect_findings, *beh_findings] if f.severity == "error"
    ]
    assert errors == []


def test_trace_matrix_surface(tmp_path: Path) -> None:
    """Положительная характеризация структуры матрицы (не tautology
    ``None or dict`` — финальное ревью I-1): ключи, и полная FR-01-строка с
    непустым ``checks``, на реально хорошем бандле фикстур."""
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    matrix = build_trace_matrix(graph, artifacts)
    assert matrix is not None
    assert matrix.keys() == {"profile", "requirements"}
    assert matrix["profile"] == "mini"
    assert matrix["requirements"] == [
        {
            "id": "FR-01",
            "priority": "Must",
            "scenarios": ["BEH-01"],
            "structural": [],
            "waived": None,
            "checks": [
                {
                    "scenario": "BEH-01",
                    "kind": "e2e",
                    "owner": "qa",
                    "status": "planned",
                    "target": "tests/test_x.py",
                }
            ],
        }
    ]


def test_spec_graph_nodes_is_a_dict(tmp_path: Path) -> None:
    """Закрепление для Task 5: ``SpecGraph.nodes`` — ``dict[id, SpecNode]``, не
    список узлов с атрибутом ``.id`` (предположение брифа Task 5 не подтвердилось).
    Порядок узлов профиля берётся через ``graph.topo_order() -> list[str]``.
    Профиль несёт третий узел, ``tasks`` (``delegate``, без upstream), поэтому
    он готов к обработке сразу — Kahn-порядок ставит его после requirements,
    перед зависящим от requirements behaviour-spec.
    """
    graph = _graph(tmp_path)
    assert isinstance(graph.nodes, dict)
    assert set(graph.nodes) == {"requirements", "behaviour-spec", "tasks"}
    order = graph.topo_order()
    assert order == ["requirements", "tasks", "behaviour-spec"]


def test_upstream_hashes_shape(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    beh = next(a for a in artifacts if a.node_id == "behaviour-spec")
    assert isinstance(beh.meta.upstream_hashes, tuple)
    pinned = dict(beh.meta.upstream_hashes)
    assert "requirements" in pinned
    assert len(pinned["requirements"]) == 40  # git blob sha1 hex
