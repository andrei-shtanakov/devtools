"""Поверхность заявки D16 — одна функция конвейера и издателя (спека df §5.4, O6).

Конвейер (`edge_check/publish.surface_violations`, пути одного коммита до PR) и
предпроверка издателя (полный состав PR, дифф к базе) получают допустимые пути
ОДНИМ кодом: расхождение конвейера и издателя исключено по построению. Модуль
чистый и с узким замыканием — только `act_request`, `bundle_dag` и stdlib: его
исполняет проверяющий административного дерева, а `df.*` и `governance.ops`
туда не входят.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from governance.act_request import ACT_REVIEW_V1, manifest_path
from governance.bundle_dag import downstream_closure, node_filenames

#: Evidence edge-check: только `.json` прямо в каталоге своего воркстрима (O6).
EVIDENCE_RE: re.Pattern[str] = re.compile(
    r"workstreams/[^/]+/evidence/edge-check/[^/]+\.json"
)


def evidence_dir(ws_id: str) -> str:
    """Каталог evidence edge-check воркстрима — один для писателя и проверки.

    Конвейер пишет сюда (`edge_check/publish.evidence_files`), поверхность
    допускает только отсюда: место не выводится из `bundle_dir`.
    """
    return f"workstreams/{ws_id}/evidence/edge-check"


def surface_violations(
    paths: Sequence[str],
    *,
    ws_id: str,
    request_key: str,
    nodes: Sequence[str],
    bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    act: str | None,
) -> list[str]:
    """Пути вне объявленной поверхности заявки D16, отсортированные.

    Поверхность: файлы узлов заявки и их downstream-замыкания (каскад `stale`
    того же коммита); `00-discovery/*` для W1 (charter несёт source-слой сам);
    evidence edge-check своего `ws` (`EVIDENCE_RE`); у заявки `review/v1` —
    ровно манифест ЕЁ САМОЙ. Чужой манифест, прочие `approvals/` и всё иное —
    нарушение.
    """
    names = dict(node_filenames(dag))
    covered = set(nodes)
    for node in nodes:
        covered |= downstream_closure(dag, node)
    allowed = {f"{bundle_dir}/{names[n]}" for n in covered if n in names}
    if act == ACT_REVIEW_V1:
        allowed.add(manifest_path(ws_id, request_key))
    discovery = f"{bundle_dir}/00-discovery/" if "charter" in nodes else None
    return sorted(
        path
        for path in paths
        if path not in allowed
        and not (discovery and path.startswith(discovery))
        and not _own_evidence(path, ws_id)
    )


def _own_evidence(path: str, ws_id: str) -> bool:
    """Файл evidence edge-check (`*.json`) того же `ws`, что у заявки."""
    own = path.startswith(evidence_dir(ws_id) + "/")
    return own and EVIDENCE_RE.fullmatch(path) is not None
