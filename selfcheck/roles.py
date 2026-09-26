"""Source roles by path registry (spec §1.4)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import cache


class Role(StrEnum):
    """What a corpus file may contribute to the usage graph."""

    SOURCE = "source"
    SKILL_ROOT = "skill-root"
    TEST = "test"
    DOCUMENTATION = "documentation"
    DIAGNOSTIC_OUTPUT = "diagnostic-output"
    CANARY = "canary"


ROLE_ORDER = (
    Role.CANARY,
    Role.DIAGNOSTIC_OUTPUT,
    Role.SKILL_ROOT,
    Role.TEST,
    Role.DOCUMENTATION,
    Role.SOURCE,
)
DEFAULT_ROLES: dict[Role, tuple[str, ...]] = {
    Role.CANARY: (".selfcheck-canary/**", "selfcheck_canary/**"),
    Role.DIAGNOSTIC_OUTPUT: ("reports/**", "out/**"),
    Role.SKILL_ROOT: (
        "skills/*/SKILL.md",
        ".claude/skills/*/SKILL.md",
        "authored/skills/*/SKILL.md",
        ".claude/commands/*.md",
    ),
    Role.TEST: ("tests/**", "**/test_*.py", "**/*_test.py"),
    Role.DOCUMENTATION: ("**/*.md", "docs/**"),
    Role.SOURCE: (),
}
ROLE_NAMES = frozenset(r.value for r in Role)


@cache
def _regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    """Match a POSIX path; ``*`` stays within a segment, ``**`` crosses."""
    return _regex(pattern).match(path) is not None


def role_of(path: str, extra: Mapping[str, Sequence[str]] | None = None) -> Role:
    """Role of ``path``: configured extras first, then defaults, then source."""
    extra = extra or {}
    for role in ROLE_ORDER:
        if any(glob_match(p, path) for p in extra.get(role.value, ())):
            return role
    for role in ROLE_ORDER:
        if any(glob_match(p, path) for p in DEFAULT_ROLES[role]):
            return role
    return Role.SOURCE
