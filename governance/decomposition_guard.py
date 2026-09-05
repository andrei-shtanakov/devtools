"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DT_HEAD_RE = re.compile(
    r"^####\s+(DT-\d+):\s*(.+?)\s*·\s*type:\s*(implement|verify)"
    r"\s*·\s*owner:\s*(\S+)\s*$",
    re.M,
)
# near-miss: начинается как DT-заголовок, но строгую грамматику не прошёл
_DT_NEAR_RE = re.compile(r"^####\s+(DT-\d+)\b.*$", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class DtTask:
    """Одна задача decomposition-узла (заголовок #### DT-NN)."""

    dt_id: str
    title: str
    type: str
    owner: str
    scenarios: tuple[str, ...]
    depends_on: tuple[str, ...]
    delivered_by: tuple[str, ...]
    parallel_group: str


def parse_dt_tasks(text: str) -> tuple[list[DtTask], list[str]]:
    """DT-грамматика → (задачи, findings формы).

    Findings формы (не графа — граф в graph_findings): near-miss
    заголовок, дубль DT-id, отсутствие scenarios/depends_on/
    parallel_group, пустой scenarios. Блок задачи ограничен следующим
    DT-заголовком ЛИБО следующей секцией уровня 1–3 (урок major'а
    PR #145 — хвост документа не читается как метаданные последней
    задачи).
    """
    findings: list[str] = []
    strict = {m.start() for m in _DT_HEAD_RE.finditer(text)}
    for near in _DT_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике DT (`#### DT-NN: <название> · type: "
                "implement|verify · owner: <роль>`)"
            )
    matches = list(_DT_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    tasks: list[DtTask] = []
    for idx, m in enumerate(matches):
        dt_id = m.group(1)
        seen[dt_id] = seen.get(dt_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        scenarios = _list_field(block, "scenarios")
        depends_on = _list_field(block, "depends_on")
        delivered_by = _list_field(block, "delivered_by") or ()
        group_m = re.search(r"^parallel_group:\s*(\S+)\s*$", block, re.M)
        if scenarios is None or not scenarios:
            findings.append(f"{dt_id}: строка scenarios отсутствует или пуста")
        if depends_on is None:
            findings.append(f"{dt_id}: строка depends_on отсутствует")
        if group_m is None:
            findings.append(f"{dt_id}: строка parallel_group отсутствует")
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=m.group(3),
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
        ))
    for dt_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{dt_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return tasks, findings
