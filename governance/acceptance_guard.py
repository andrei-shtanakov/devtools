"""Гард acceptance-узла: AC-грамматика и покрытие Must-требований (спека
2026-09-07-acceptance-node §3). Чистые функции над строками — без
git/ФС/steward; потребители — S4-гейт (runner) и мост (task_bridge).
Канон — governance/design_guard.py и governance/decomposition_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_AC_HEAD_RE = re.compile(
    r"^####\s+(AC-\d+[a-z]?):\s*(.+?)\s*·\s*verification:\s*"
    r"(test|manual|metric)\s*$",
    re.M,
)
# NEAR ловит любую AC-подобную шапку (уроки PR #145/#148): неизвестная
# форма id/строки — находка формы, не молчание.
_AC_NEAR_RE = re.compile(r"^####\s+(AC-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)

#: Дословная декларация пустого набора Must (§3 спеки).
EMPTY_MUST_DECLARATION = "Must-требований во входном наборе нет"


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class AcCriterion:
    """Один критерий приёмки (заголовок #### AC-NN)."""

    ac_id: str
    title: str
    verification: str
    traces: tuple[str, ...]
    scenarios: tuple[str, ...]


def parse_ac_criteria(text: str) -> tuple[list[AcCriterion], list[str]]:
    """AC-грамматика → (критерии, findings формы).

    Findings формы: near-miss заголовок, дубль AC-id, пустой/отсутствующий
    traces, `verification: test` без непустого scenarios. Блок критерия
    ограничен следующим AC-заголовком ЛИБО секцией уровня 1–3 (урок
    PR #145 — хвост документа не читается как метаданные последнего AC).
    """
    findings: list[str] = []
    strict = {m.start() for m in _AC_HEAD_RE.finditer(text)}
    for near in _AC_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике AC (`#### AC-NN: <название> · verification: "
                "test|manual|metric`)"
            )
    matches = list(_AC_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    crits: list[AcCriterion] = []
    for idx, m in enumerate(matches):
        ac_id = m.group(1)
        seen[ac_id] = seen.get(ac_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        traces = _list_field(block, "traces")
        scenarios = _list_field(block, "scenarios") or ()
        if not traces:
            findings.append(
                f"{ac_id}: строка traces отсутствует или пуста (каждый "
                "критерий обязан покрывать хотя бы одно требование)"
            )
        if m.group(3) == "test" and not scenarios:
            findings.append(
                f"{ac_id}: verification: test без непустого scenarios — "
                "тестовый критерий обязан называть свои сценарии"
            )
        crits.append(AcCriterion(
            ac_id=ac_id, title=m.group(2), verification=m.group(3),
            traces=traces or (), scenarios=scenarios,
        ))
    for ac_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{ac_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return crits, findings
