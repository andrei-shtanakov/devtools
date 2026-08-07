"""Regression: project names compare case-folded on every side.

The registry is prose and keeps product spellings (`Maestro` the product lives
in `maestro/`), while the prograph snapshot names projects after the directory.
Before folding, that split one project into two nodes: the same edge landed in
UNDETECTED and UNDOCUMENTED at once, and an allowlist entry stopped covering
its own pair the moment either side changed case.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check-graph-registry-drift.py"


@pytest.fixture(scope="module")
def drift():
    spec = importlib.util.spec_from_file_location("graph_registry_drift", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить модуль чекера из {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["graph_registry_drift"] = mod
    spec.loader.exec_module(mod)
    return mod


REGISTRY = """
## Integration map

- `Maestro → spec-runner` via `plan --full` CLI.
- `maestro → arbiter` via MCP.
- Maestro internal sub-package graph (Arbiter, spec-runner).

## Next section
"""


def test_registry_names_fold_to_one_node(drift):
    """`Maestro` and `maestro` in one map are the same project, not two."""
    pairs, _covered, _unconnected = drift.parse_registry(REGISTRY)
    names = {n for pair in pairs for n in pair}
    assert names == {"maestro", "spec-runner", "arbiter"}


def test_covered_group_owner_and_members_fold_together(drift):
    """The COVERED shorthand follows the same normalization as arrow pairs."""
    _pairs, covered, _unconnected = drift.parse_registry(REGISTRY)
    assert {"maestro", "arbiter", "spec-runner"} in covered


def test_allowlist_covers_pair_whatever_the_spelling(drift):
    """One allowlist entry covers the pair however either side spells it."""
    rules = [("maestro", "spec-runner", "declared-edge candidate")]
    for a, b in (("Maestro", "spec-runner"), ("maestro", "spec-runner")):
        assert drift.allowed(drift.Pair((drift._norm(a), drift._norm(b))), rules)


def test_loaded_patterns_fold_too(drift, tmp_path, monkeypatch):
    """An entry written with capitals still matches once loaded from disk."""
    allowlist = tmp_path / "graph-registry-allowlist.toml"
    allowlist.write_text(
        '[[allow]]\na = "Maestro"\nb = "spec-*"\nreason = "legacy spelling"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(drift, "ALLOWLIST", allowlist)

    pairs, _covered, _unconnected = drift.parse_registry(REGISTRY)
    maestro_spec = drift.Pair(("maestro", "spec-runner"))
    assert maestro_spec in pairs
    assert drift.allowed(maestro_spec, drift.load_allowlist()) == "legacy spelling"
