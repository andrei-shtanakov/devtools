"""criteria_graph — типизированный граф BEH/AC бандла (спека §1.5–1.7, §5.2).

Приоритет выводится по трассам (максимум), пустая/неразрешимая трасса —
ошибка. Сирота — BEH (кроме Won't) вне scenarios всех не-Won't AC.
Статусы AC выводятся только здесь (§5.2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from governance import acceptance_guard as ag

PRIORITIES = ("Must", "Should", "Could", "Won't")
EXEC_KINDS = frozenset({"unit", "integration", "contract", "e2e", "atp"})
_BEH_HEAD = re.compile(r"^####\s+(BEH-\d+[a-z]?):", re.M)
_TRACES = re.compile(r"`traces:\s*\[([^\]]*)\]`")
_CHECKED = re.compile(r"\*\*checked_by\*\*:(.*)$", re.M)
_FIELD = re.compile(r"`(\w+):\s*([^`]*)`")


@dataclass(frozen=True)
class Beh:
    id: str
    kind: str
    waived: bool
    traces: tuple[str, ...]
    priority: str | None


@dataclass(frozen=True)
class Ac:
    id: str
    verification: str
    traces: tuple[str, ...]
    scenarios: tuple[str, ...]
    priority: str | None


@dataclass
class Graph:
    behs: dict[str, Beh]
    acs: dict[str, Ac]
    errors: list[str] = field(default_factory=list)


def _max_priority(refs: tuple[str, ...], prio: dict[str, str]) -> str | None:
    found = [prio[r] for r in refs if r in prio]
    if not refs or len(found) != len(refs):
        return None
    return min(found, key=PRIORITIES.index)


def _parse_behs(beh_text: str, prio: dict[str, str], errors: list[str]) -> dict[str, Beh]:
    heads = list(_BEH_HEAD.finditer(beh_text))
    out: dict[str, Beh] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(beh_text)
        block = beh_text[m.end():end]
        tr = _TRACES.search(block)
        traces = tuple(x.strip() for x in tr.group(1).split(",") if x.strip()) if tr else ()
        cb = _CHECKED.search(block)
        fields = dict(_FIELD.findall(cb.group(1))) if cb else {}
        kind = fields.get("kind", "").strip()
        priority = _max_priority(traces, prio)
        if priority is None:
            errors.append(f"{m.group(1)}: пустая или неразрешимая трасса {list(traces)}")
        if kind not in EXEC_KINDS | {"manual"}:
            errors.append(f"{m.group(1)}: неизвестный kind {kind!r}")
        out[m.group(1)] = Beh(
            id=m.group(1), kind=kind, waived=fields.get("status", "").strip() == "waived",
            traces=traces, priority=priority,
        )
    return out


def build_graph(req_text: str, beh_text: str, acc_text: str) -> Graph:
    """Граф из байтов трёх узлов (читаются по пину вызывающим)."""
    prio, errors = ag._parse_requirements(req_text)
    behs = _parse_behs(beh_text, prio, errors)
    crits, ac_findings = ag.parse_ac_criteria(acc_text)
    errors += ac_findings
    acs: dict[str, Ac] = {}
    for c in crits:
        priority = _max_priority(tuple(c.traces), prio)
        if priority is None:
            errors.append(f"{c.ac_id}: пустая или неразрешимая трасса {list(c.traces)}")
        acs[c.ac_id] = Ac(c.ac_id, c.verification, tuple(c.traces), tuple(c.scenarios), priority)
    return Graph(behs, acs, errors)


def orphan_findings(graph: Graph) -> list[str]:
    """§1.7: каждый не-Won't BEH входит в scenarios хотя бы одного не-Won't AC."""
    live = {s for a in graph.acs.values() if a.priority not in (None, "Won't") for s in a.scenarios}
    return [
        f"{b.id}: сирота — не входит в scenarios ни одного не-Won't AC"
        for b in graph.behs.values()
        if b.priority != "Won't" and b.id not in live
    ]


def test_behs(graph: Graph) -> list[Beh]:
    """Test-критерии: исполняемый kind и не waived (waived приоритетнее)."""
    return [b for b in graph.behs.values() if b.kind in EXEC_KINDS and not b.waived]


def derive_ac(ac: Ac, graph: Graph, beh_status: dict[str, str]) -> str:
    """Таблица §5.2."""
    if ac.verification != "test":
        return "human"
    statuses = []
    for s in ac.scenarios:
        beh = graph.behs.get(s)
        if beh is None:
            return "error"
        if beh.waived or beh.kind == "manual":
            statuses.append("human")
        else:
            statuses.append(beh_status.get(s, "error"))
    if "error" in statuses:
        return "error"
    if "unconfirmed" in statuses:
        return "unconfirmed"
    if "human" in statuses:
        return "human"
    return "traced"
