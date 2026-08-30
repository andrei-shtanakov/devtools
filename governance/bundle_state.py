"""Read-модель бандла, candidate-срез (спека §3/§4).

Единственное место, знающее раскладку бандла. Только контентные проверки:
три публичных символа пинованного steward + локальный stale-адаптер. Никаких
git-facts — регрессия закреплена тестом. Authoritative-срез (default branch,
--emit-verdicts) — этап B.

Кроме трёх content-check символов, модуль импортирует ещё два ЧИСТО-YAML'ных
загрузчика графа — ``steward.graph.load_profile``/``SpecGraph`` и
``steward.roles.load_roles_catalog``. Они не входят в закрытый список §3
(там про сами гейты), но остаются безопасными: обе функции — предусловие
вызова трёх проверок (граф и роли нужны, чтобы вообще было что проверять),
ни одна не трогает git и не читает ничего вне ``profile_path``/сиблинг
``roles.yaml`` (собственная конвенция steward,
``gatecheck/cli.py``: «roles.yaml is a MANDATORY sibling»).

ВАЖНО потребителю: ``error_count == 0`` САМ ПО СЕБЕ не означает «бандл
зелёный». Это агрегат по findings, а не по составу узлов — он ничего не
знает о том, каких узлов в бандле вообще нет. Отдельно нужно проверить:

- ``required_absent`` — обязательные (``required``, не-``delegate``) узлы
  профиля, отсутствующие в бандле. Их отсутствие само по себе не ошибка
  (выбор узлов текущего этапа — не дело read-модели), но потребитель обязан
  решить, ожидаются ли они на своём этапе;
- статусы узлов (``NodeState.status``) — узел может быть ``"blocked"``
  (гейты по нему физически не могли отработать, потому что ни один его
  upstream-артефакт не присутствует в бандле) или ``"delegated"`` (узел
  живёт вне бандла по профилю — не считать ни absent, ни ошибкой).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    trace_matrix: dict[str, Any] | None
    required_absent: tuple[str, ...]
    bundle_findings: tuple[str, ...]


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
    profile_node_ids = set(graph.nodes)

    nodes: list[NodeState] = []
    required_absent: list[str] = []
    for node_id in _profile_node_ids(graph):
        node = graph.nodes[node_id]
        if node.delegate is not None:
            # Живёт вне бандла по построению (per-workstream/per-release) —
            # ни absent, ни ошибка (находка I-5).
            status = "delegated"
        elif node_id not in present:
            status = "absent"
            if node.required:
                required_absent.append(node_id)
        elif node.upstream and not any(u in present for u in node.upstream):
            # Узел присутствует, но НИ ОДИН его upstream — нет: гейты по нему
            # физически не могли отработать (`check_behaviour_spec` молча
            # возвращает `[]` ровно в этом случае). Читать как отказ, не как
            # "зелено" (спека §8; финальное ревью, находка C-1).
            missing = ", ".join(u for u in node.upstream if u not in present)
            per_node.setdefault(node_id, []).append(
                f"error GC-UPSTREAM-ABSENT(prospective): {node_id} — "
                f"upstream(ы) {missing} отсутствуют в бандле"
            )
            status = "blocked"
        elif node_id in stale_nodes:
            status = "stale"
        elif any(f.startswith("error") for f in per_node.get(node_id, ())):
            status = "draft"
        else:
            status = "candidate_valid"
        nodes.append(NodeState(node_id, status, tuple(per_node.get(node_id, ()))))

    # Находки, чей artifact не мапится ни на один узел профиля (GC-META на
    # незапарсенном файле, GC-DUP на втором претенденте на узел, GC-STAGE на
    # незнакомом spec_stage) не попадают в состав ни одного NodeState — иначе
    # они бы молча терялись из per-node вида (финальное ревью, находка I-4).
    bundle_findings = tuple(
        f for key, fs in per_node.items() if key not in profile_node_ids for f in fs
    )

    error_count = sum(
        1
        for fs in per_node.values()
        for f in fs
        if f.startswith("error")
    )
    matrix = build_trace_matrix(graph, artifacts)
    return BundleState(
        tuple(nodes), error_count, matrix, tuple(required_absent), bundle_findings
    )


def _node_of(artifacts: list[Any], path: str) -> str:
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
