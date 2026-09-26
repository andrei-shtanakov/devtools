"""Computed-launch resolution (spec §3.2.3) — implemented in Task 9."""

from __future__ import annotations

from collections.abc import Sequence

from selfcheck.graph.commands import Index
from selfcheck.graph.model import Graph
from selfcheck.roles import Role


def add_exec_edges(
    g: Graph,
    files: Sequence[str],
    texts: dict[str, str],
    roles: dict[str, Role],
    index: Index,
) -> None:
    """Add exec edges and zones; stub until Task 9."""
    del g, files, texts, roles, index
