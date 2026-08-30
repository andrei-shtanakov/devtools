"""Prospective stale-проверка по локальному контенту worktree (спека §3).

ВРЕМЕННЫЙ адаптер: публичного prospective-API у steward нет, а его внутренний
stale-каскад требует git-facts. Сверяем пины upstream_hashes артефактов бандла с
blob-хешами фактического содержимого тех же артефактов. Удалить при переходе S4
на candidate-контракт steward (`ref_kind: candidate`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StaleFinding:
    """Один протухший (или непроверяемый) пин: artifact -> upstream."""

    artifact: str
    upstream: str
    pinned: str
    actual: str | None  # None = upstream-артефакта нет в бандле (fail-closed)


def blob_sha1(text: str) -> str:
    """`git hash-object` содержимого — чистым stdlib, git не нужен."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00%s" % (len(data), data)).hexdigest()


def check_stale(artifacts: list[Any]) -> list[StaleFinding]:
    """Пины каждого артефакта против blob-хешей upstream-узлов того же бандла."""
    by_node = {a.node_id: a for a in artifacts if a.node_id is not None}
    findings: list[StaleFinding] = []
    for artifact in artifacts:
        for upstream, pinned in artifact.meta.upstream_hashes:
            up = by_node.get(upstream)
            actual = blob_sha1(up.text) if up is not None else None
            if actual != pinned:
                findings.append(
                    StaleFinding(
                        artifact=artifact.path,
                        upstream=upstream,
                        pinned=pinned,
                        actual=actual,
                    )
                )
    return findings
