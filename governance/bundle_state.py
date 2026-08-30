"""Read-модель бандла, candidate-срез (спека §3/§4).

Единственное место, знающее раскладку бандла. Только контентные проверки:
три публичных символа пинованного steward + локальный stale-адаптер. Никаких
git-facts — регрессия закреплена тестом. Authoritative-срез (default branch,
--emit-verdicts) — этап B.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from steward.gatecheck.behaviour import check_behaviour_spec
from steward.gatecheck.checks import collect_bundle
from steward.gatecheck.trace_matrix import build_trace_matrix
from steward.graph import SpecGraph, load_profile
from steward.roles import load_roles_catalog

from governance.stale_adapter import check_stale


@dataclass(frozen=True)
class NodeState:
    node_id: str
    status: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class BundleState:
    nodes: tuple[NodeState, ...]
    error_count: int
    trace_matrix: dict | None


def candidate_state(profile_path: Path, bundle_dir: Path) -> BundleState:
    """Состояние бандла по его содержимому (голова ветки, PR ещё может не быть)."""
    roles = load_roles_catalog(Path(profile_path).parent / "roles.yaml")
    graph = load_profile(profile_path, roles)
    artifacts, findings = collect_bundle(graph, bundle_dir)
    findings = [*findings, *check_behaviour_spec(graph, artifacts)]
    stale = check_stale(artifacts)

    per_node: dict[str, list[str]] = {}
    for f in findings:
        artifact_node = _node_of(artifacts, f.artifact)
        per_node.setdefault(artifact_node, []).append(
            f"{f.severity} {f.rule_id}: {f.message}"
        )
    stale_nodes: set[str] = set()
    for s in stale:
        node = _node_of(artifacts, s.artifact)
        stale_nodes.add(node)
        per_node.setdefault(node, []).append(
            f"error GC-STALE(prospective): {s.artifact} пин {s.upstream} "
            f"{s.pinned[:8]} != {(s.actual or 'absent')[:8]}"
        )

    present = {a.node_id for a in artifacts if a.node_id is not None}
    nodes: list[NodeState] = []
    for node_id in _profile_node_ids(graph):
        node_findings = tuple(per_node.get(node_id, ()))
        if node_id not in present:
            status = "absent"
        elif node_id in stale_nodes:
            status = "stale"
        elif any(f.startswith("error") for f in node_findings):
            status = "draft"
        else:
            status = "candidate_valid"
        nodes.append(NodeState(node_id, status, node_findings))

    error_count = sum(
        1
        for fs in per_node.values()
        for f in fs
        if f.startswith("error")
    )
    matrix = build_trace_matrix(graph, artifacts)
    return BundleState(tuple(nodes), error_count, matrix)


def _node_of(artifacts: list, path: str) -> str:
    for a in artifacts:
        if a.path == path and a.node_id is not None:
            return a.node_id
    return path  # finding о неизвестном файле группируется по пути


def _profile_node_ids(graph: SpecGraph) -> list[str]:
    """Порядок узлов профиля: ``SpecGraph.nodes`` — ``dict[str, SpecNode]``.

    Предположение брифа (``graph.nodes`` как список объектов с ``.id``) не
    подтвердилось: characterization Task 2 (``test_governance_steward_surface.py``)
    и сам ``steward.graph.SpecGraph`` фиксируют его как маппинг id -> SpecNode.
    ``topo_order()`` даёт стабильный (топологический) порядок ключей этого маппинга.
    """
    return graph.topo_order()
