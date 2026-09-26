"""Usage graph data model (spec §3.2.1)."""

from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass, field
from enum import StrEnum

from selfcheck.model import Location


class NodeKind(StrEnum):
    FILE = "file"
    MAKE = "make"
    SKILL = "skill"
    WORKFLOW = "workflow"
    CLI = "cli"
    UNIT = "unit"


@dataclass(frozen=True)
class Node:
    """A graph node; roots are public entry points (spec §3.2.2)."""

    anchor: str
    kind: NodeKind
    path: str
    name: str
    root: bool = False
    executable: bool = False


class EdgeKind(StrEnum):
    MAKE = "make"
    CI = "ci"
    IMPORT = "import"
    EXEC = "exec"
    ENTRY = "entry"
    SKILL = "skill"
    SCHED = "sched"
    RUNBOOK = "runbook"
    FLEET = "fleet"
    TEST = "test"
    DOC = "doc"


NON_EXEC = frozenset({EdgeKind.TEST, EdgeKind.DOC})


@dataclass(frozen=True)
class Edge:
    target: str
    kind: EdgeKind
    where: Location


@dataclass(frozen=True)
class Zone:
    """Nodes a non-resolvable launch might reach (spec §3.2.3)."""

    caller: Location
    members: frozenset[str]
    reason: str


@dataclass
class Graph:
    """Nodes, edges, zones and mentions of one repo (or one canary set)."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    mentions: dict[str, list[str]] = field(default_factory=dict)
    broken: list[tuple[str, Location, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    plists: list[str] = field(default_factory=list)
    root_texts: dict[str, str] = field(default_factory=dict)
    # launch targets that resolved to no node of this graph (spec §9.3)
    external: list[tuple[str, EdgeKind, Location]] = field(default_factory=list)

    def incoming(self, anchor: str) -> list[Edge]:
        """Edges pointing at ``anchor``."""
        return [e for e in self.edges if e.target == anchor]

    def add(self, target_path: str, kind: EdgeKind, where: Location) -> None:
        """Edge to a file node (ignored when unknown or self-referencing)."""
        self.add_anchor(f"file:{target_path}", kind, where)

    def add_anchor(self, anchor: str, kind: EdgeKind, where: Location) -> None:
        """Edge to any node anchor (ignored when unknown or self-referencing)."""
        node = self.nodes.get(anchor)
        if node is None or (node.kind is NodeKind.FILE and node.path == where.path):
            return
        edge = Edge(anchor, kind, where)
        if edge not in self.edges:
            self.edges.append(edge)

    def mention(self, anchor: str, where_path: str) -> None:
        """Record that ``where_path`` names ``anchor`` without launching it."""
        node = self.nodes.get(anchor)
        if node is None or node.path == where_path:
            return
        paths = self.mentions.setdefault(anchor, [])
        if where_path not in paths:
            paths.append(where_path)


def parse_python(source: str) -> ast.Module:
    """``ast.parse`` without SyntaxWarning noise: fleet code is someone else's,
    its escape sequences are not our finding (S2 acceptance 2026-09-26)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)
