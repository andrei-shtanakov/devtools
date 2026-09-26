"""Dead: class → eligibility → confidence; roots (spec §2.3, §3.2.2)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from selfcheck.graph.model import NON_EXEC, EdgeKind, Graph, NodeKind
from selfcheck.model import Confidence, Finding, Location, cap, make_text_key

DAY = 86400.0
AGE_DAYS = 60
ROOT_STALE_DAYS = 180


@dataclass(frozen=True)
class Surface:
    """Sources this run consulted (spec §3.2.4)."""

    fleet: str
    sched_dir: str | None
    plists: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeFacts:
    """Inputs of the dead decision for one node."""

    klass: str
    root: bool
    in_zone: bool
    history: bool
    age_days: float | None
    mentioned: bool


def dead_confidence(
    facts: NodeFacts, surface: Surface
) -> tuple[Confidence | None, list[str]]:
    """Steps 2 and 3 of spec §2.3: eligibility, then min(base, caps)."""
    if facts.klass == "live" or facts.root or facts.in_zone:
        return None, []
    value = Confidence.CONFIRMED if facts.klass == "orphan" else Confidence.CANDIDATE
    caps: list[str] = []
    if surface.fleet != "complete":
        caps.append("P1")
    if surface.sched_dir is None:
        caps.append("P2")
    if not facts.history:
        caps.append("P3")
    elif facts.age_days is not None and facts.age_days < AGE_DAYS:
        caps.append("P4")
    if facts.mentioned:
        caps.append("P5")
    if caps:
        value = cap(value, Confidence.LIKELY)
    return value, caps


def klass_of(g: Graph, anchor: str) -> str:
    """Step 1 of spec §2.3."""
    kinds = {e.kind for e in g.incoming(anchor)}
    if kinds - NON_EXEC:
        return "live"
    if kinds == {EdgeKind.TEST}:
        return "test-only"
    if EdgeKind.DOC in kinds:
        return "doc-only"
    return "orphan"


def graph_payload(g: Graph) -> dict[str, dict[str, object]]:
    """Every node with its class, root flag and evidence edges (spec §0 п.2)."""
    return {
        anchor: {
            "class": klass_of(g, anchor),
            "root": node.root,
            "edges": [
                {"kind": e.kind.value, "from": f"{e.where.path}:{e.where.line}"}
                for e in g.incoming(anchor)
            ],
        }
        for anchor, node in sorted(g.nodes.items())
    }


def classify(
    g: Graph,
    *,
    repo: str,
    surface: Surface,
    ages: Callable[[str], float | None],
    now: float,
) -> list[Finding]:
    """All usage-graph findings of one graph."""
    in_zone = {m for z in g.zones for m in z.members}
    findings: list[Finding] = []
    for anchor, node in sorted(g.nodes.items()):
        if node.kind is not NodeKind.FILE:
            continue
        ts = ages(node.path)
        facts = NodeFacts(
            klass_of(g, anchor),
            node.root,
            anchor in in_zone,
            ts is not None,
            (now - ts) / DAY if ts is not None else None,
            bool(g.mentions.get(anchor)),
        )
        value, caps = dead_confidence(facts, surface)
        if value is None:
            continue
        rule = "usage-graph/dead.file" if node.executable else "usage-graph/dead.module"
        findings.append(
            Finding(
                rule=rule,
                category="dead",
                severity="medium",
                confidence=value,
                owner_repo=repo,
                anchor=anchor,
                locations=[Location(node.path, 1)],
                evidence=[
                    {"kind": "class", "detail": facts.klass},
                    *[{"kind": "cap", "detail": c} for c in caps],
                    *[
                        {"kind": "mentioned-in", "detail": p}
                        for p in g.mentions.get(anchor, [])
                    ],
                ],
                suggestion="удалить или перенести в docs/archive",
            )
        )
    findings += _zones(g, repo)
    findings += _broken(g, repo)
    findings += _stale_roots(g, repo, ages, now)
    return findings


def _zones(g: Graph, repo: str) -> list[Finding]:
    out = []
    for zone in g.zones:
        text = g.root_texts.get(zone.caller.path, "")
        lines = text.splitlines()
        line_text = (
            lines[zone.caller.line - 1] if 0 < zone.caller.line <= len(lines) else ""
        )
        out.append(
            Finding(
                rule="usage-graph/unresolved-exec",
                category="quality",
                severity="low",
                confidence=Confidence.CANDIDATE,
                owner_repo=repo,
                anchor=f"file:{zone.caller.path}",
                locations=[zone.caller],
                text_key=make_text_key(line_text),
                evidence=[
                    {"kind": "zone-member", "detail": m} for m in sorted(zone.members)
                ],
                suggestion="сделайте вызов разрешимым (литеральный путь)",
            )
        )
    return out


def _broken(g: Graph, repo: str) -> list[Finding]:
    return [
        Finding(
            rule="usage-graph/broken-root",
            category="bug",
            severity="high",
            confidence=Confidence.LIKELY,
            owner_repo=repo,
            anchor=anchor,
            locations=[where],
            text_key=make_text_key(token),
            evidence=[{"kind": "missing", "detail": token}],
        )
        for anchor, where, token in g.broken
    ]


def _stale_roots(
    g: Graph, repo: str, ages: Callable[[str], float | None], now: float
) -> list[Finding]:
    out: list[Finding] = []
    for anchor, node in sorted(g.nodes.items()):
        if not node.root or node.kind is NodeKind.FILE:
            continue
        ts = ages(node.path)
        if ts is None or (now - ts) / DAY <= ROOT_STALE_DAYS:
            continue
        pattern = re.compile(rf"\b{re.escape(node.name)}\b")
        if any(pattern.search(t) for p, t in g.root_texts.items() if p != node.path):
            continue
        out.append(
            Finding(
                rule="usage-graph/root-stale",
                category="dead",
                severity="low",
                confidence=Confidence.CANDIDATE,
                owner_repo=repo,
                anchor=anchor,
                locations=[Location(node.path, 1)],
                evidence=[{"kind": "age-days", "detail": str(int((now - ts) / DAY))}],
            )
        )
    return out
