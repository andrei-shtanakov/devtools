"""Prospective stale-проверка по локальному контенту worktree (спека §3).

ВРЕМЕННЫЙ адаптер: публичного prospective-API у steward нет, а его внутренний
stale-каскад требует git-facts. Сверяем пины upstream_hashes артефактов бандла с
blob-хешами фактического содержимого тех же артефактов. Удалить при переходе S4
на candidate-контракт steward (`ref_kind: candidate`).

Семантика зафиксирована тестами, но совпадает с `check_stale_cascade`
(`steward/gatecheck/checks.py:232-298`) лишь частично — три оси расхождения
(финальное ревью I-3):

1. steward проверяет только артефакты со `status: approved`; этот адаптер —
   любые, включая `status: draft` (на S4 бандл именно draft). Строже и
   безопаснее, но означает, что кандидатский срез может краснеть там, где
   S8 на дефолтной ветке зелёный;
2. где steward даёт `warn` (`GC-STALE-UNPINNED` на неразрешимый хеш,
   `GC-STALE-KEY` на пин не-upstream-узла), этот адаптер даёт `error`
   (`actual=None` → находка). Строже, соответствует §8, но S4 и S8 могут
   разойтись по одному и тому же артефакту;
3. «объявленное upstream-ребро без пина» steward ловит warn'ом; этот адаптер
   сам по себе — по-прежнему нет: цикл `check_stale` идёт по
   `artifact.meta.upstream_hashes` (что ЗАПИНЕНО), не по графу профиля (что
   ОБЯЗАНО быть запинено) — `check_stale` графа не принимает и не собирается
   (сигнатуру решили не трогать). Дыру закрывает не адаптер, а
   `bundle_state.candidate_state`: пройдя по объявленным профилем
   upstream-рёбрам узла, она ловит присутствующий-но-незапиненный upstream
   находкой `GC-UNPINNED(prospective)` (error, блокирует узел через общую
   error-ветку), а полностью отсутствующий upstream — статусом `"blocked"`
   (см. `bundle_state.py`). Итого: и «упстрим есть, но не запинен», и
   «упстрима нет вовсе» теперь блокируют candidate-срез; временная дыра
   закрыта на уровне read-модели, не самого адаптера (замена рулинга I-3
   п.3 после codex-ревью PR #87 — прежняя формулировка «осознанная дыра»
   для этого случая была неверной: спека требует пины в том же PR, так что
   их отсутствие — нарушение, а не неизвестность-как-успех). Полное
   закрытие на уровне steward — candidate-контракт (`ref_kind: candidate`).
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
