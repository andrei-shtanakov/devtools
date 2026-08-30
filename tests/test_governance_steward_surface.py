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

from tests.governance_fixtures.bundles import make_bundle, make_profile


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
    """Полный набор gate_id, который выдаёт check_behaviour_spec на плохом бандле."""
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    artifacts, _ = collect_bundle(graph, bundle)
    ids = {f.rule_id for f in check_behaviour_spec(graph, artifacts)}
    # Ожидаемое множество — из спеки §7; фактическое зафиксировать здесь же.
    assert ids <= {"GC-BEH-TRACE", "GC-BEH-COVERAGE", "GC-CHECK-PLANNED"}, ids
    assert ids, "хотя бы один GC-BEH-* обязан сработать"


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
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    matrix = build_trace_matrix(graph, artifacts)
    assert matrix is None or isinstance(matrix, dict)


def test_upstream_hashes_shape(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    beh = next(a for a in artifacts if a.node_id == "behaviour-spec")
    assert isinstance(beh.meta.upstream_hashes, tuple)
    pinned = dict(beh.meta.upstream_hashes)
    assert "requirements" in pinned
    assert len(pinned["requirements"]) == 40  # git blob sha1 hex
