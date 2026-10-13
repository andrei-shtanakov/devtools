"""Проекции self-hash узла по версиям акта (спека §7).

Для каждой версии — своё множество исключаемых полей. Инвариант: хеш до
штампа = хеш после штампа. Неизвестная версия — отказ до вычисления.
"""

from __future__ import annotations

# TODO(Part B, Task 15 `act_premerge`): когда проекция акта станет частью
# харнесс-записи, `governance/node_approval` и `governance/stale_adapter`
# обязаны войти в authority-root/_HARNESS_PREFIXES
# (tests/test_governance_authority_root.py это проверяет). Сейчас — нет.
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.node_approval import APPROVAL_ENVELOPE_KEYS
from governance.stale_adapter import blob_sha1

REVIEW_V1_STAMP_KEYS = (
    "approval_act",
    "approval_review_id",
    "delivered_by",
    "delivered_at",
)
_EXCLUDED: dict[str | None, tuple[str, ...]] = {
    None: APPROVAL_ENVELOPE_KEYS,
    "review/v1": APPROVAL_ENVELOPE_KEYS + REVIEW_V1_STAMP_KEYS,
}


class ProjectionError(ValueError):
    """Неизвестная версия акта — хеш не вычисляется."""


def self_hash_v(text: str, act: str | None) -> str:
    """Хеш собственной части узла в проекции версии `act`.

    Маркер `approval_act` во frontmatter, если есть, обязан быть известной
    версией и совпадать с `act` (режимом из манифеста) — иначе отказ до хеша.
    Отсутствие штампа допустимо (узел до финализации).
    """
    if act not in _EXCLUDED:
        raise ProjectionError(f"неизвестная версия акта: {act!r}")
    meta, body = split_frontmatter(text)
    marker = meta.get("approval_act")
    if marker is not None and (marker not in _EXCLUDED or marker != act):
        raise ProjectionError(f"маркер узла {marker!r} ≠ режиму заявки {act!r}")
    for key in _EXCLUDED[act]:
        meta.pop(key, None)
    return blob_sha1(join_frontmatter(meta, body))
