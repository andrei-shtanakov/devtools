"""Task 14 — registry invariants (§1.2, §4.1)."""

from __future__ import annotations

from selfcheck.registry import REGISTRY
from selfcheck.roles import Role, role_of

EXPECTED = {
    "ruff",
    "pyrefly",
    "vulture",
    "radon",
    "deptry",
    "shellcheck",
    "actionlint",
    "zizmor",
    "jscpd",
    "usage-graph",
    "ast-dup",
    "cli-overlap",
    "llm-sites",
}


def test_registry_invariants() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)) and set(names) == EXPECTED
    assert all(s.executes_target_code is False for s in REGISTRY)
    paths = [s.canary.relpath for s in REGISTRY]
    assert len(paths) == len(set(paths))
    assert all(role_of(p) is Role.CANARY for p in paths)
    assert all(s.logic_version >= 1 for s in REGISTRY if s.analyze is not None)
    assert all(s.rules for s in REGISTRY)
