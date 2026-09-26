"""All S1 probes, in report order."""

from __future__ import annotations

from selfcheck.dups import AST_DUP, CLI_OVERLAP
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.llm import LLM_SITES
from selfcheck.probes.base import ProbeSpec
from selfcheck.probes.other_tools import OTHER_PROBES
from selfcheck.probes.python_tools import PYTHON_PROBES

REGISTRY: tuple[ProbeSpec, ...] = (
    *PYTHON_PROBES,
    *OTHER_PROBES,
    USAGE_GRAPH,
    AST_DUP,
    CLI_OVERLAP,
    LLM_SITES,
)
