"""edge-check — смысловая проверка узла бандла против его оснований."""

from governance.edge_check.check import SCHEMA_VERSION, decide, run_check
from governance.edge_check.rules import EdgeCheckError, RuleSet, load_rules

__all__ = [
    "EdgeCheckError",
    "RuleSet",
    "SCHEMA_VERSION",
    "decide",
    "load_rules",
    "run_check",
]
