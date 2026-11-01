"""Идентичность судимого объекта акта (ревизия b1): одна на объект, одна проверка.

Каждое суждение о заявке, её доставке, маркере миграции сверяет ВСЕ
компоненты идентичности объекта, а не те, что оказались под рукой: продукт,
воркстрим, ключ заявки, пути узлов в DAG прогона и байты манифеста в
одобренном head. Таблица «место суждения × компоненты» — ревизия части B;
отрицательный двойник на каждый компонент — `tests/test_act_identity.py`.
Модуль чистый и входит в цепочку `act_premerge`: только `act_request`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from governance.act_request import NodeRequest, manifest_path


@dataclass(frozen=True)
class RequestIdentity:
    """Чья это заявка: продукт доставки, воркстрим прогона, ключ леджера."""

    product: str
    ws_id: str
    request_id: str

    @property
    def manifest_path(self) -> str:
        """Где манифест обязан лежать в одобренном коммите."""
        return manifest_path(self.ws_id, self.request_id)

    @property
    def migration_marker(self) -> str:
        """Маркер `act_migrated` этой и только этой заявки (§10 п. 2, B1-14)."""
        return f"<!-- act-migrated: {self.product}/{self.ws_id}/{self.request_id} -->"

    @property
    def memo_key(self) -> tuple[str, str, str]:
        """Ключ памяти вердиктов: заявка, а не коммит (B1-2)."""
        return (self.product, self.ws_id, self.request_id)


def manifest_refusal(req: NodeRequest, ident: RequestIdentity) -> str | None:
    """Манифест называет именно эту заявку этого воркстрима этого продукта."""
    for name, got, want in (
        ("product", req.product, ident.product),
        ("ws_id", req.ws_id, ident.ws_id),
        ("request_id", req.request_id, ident.request_id),
    ):
        if got != want:
            return f"манифест: {name} {got} ≠ {want}"
    return None


def paths_refusal(req: NodeRequest, expected: Mapping[str, str]) -> str | None:
    """Узлы манифеста — узлы активного DAG, путь каждого — его путь в прогоне."""
    for node, entry in sorted(req.nodes.items()):
        want = expected.get(node)
        if want is None:
            return f"манифест: узел {node} не из активного DAG"
        if entry.path != want:
            return f"манифест: путь узла {node} {entry.path} ≠ {want}"
    return None


def head_manifest_refusal(text_at_head: str | None, text_delivered: str) -> str | None:
    """Доставленный манифест лежит в одобренном head теми же байтами (§9.1 (а) п. 0)."""
    if text_at_head is None:
        return "манифест есть в доставке, но не в одобренном head"
    if text_at_head != text_delivered:
        return "манифест доставки ≠ манифесту одобренного head"
    return None
