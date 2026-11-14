"""Сверка «узел не ослаблен» после переписывания автором.

Fail-closed: любая неоднозначность в новом тексте (дубль заголовка, несколько
строк Priority, нечитаемый Priority у бывшего Must) считается ослаблением.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^####\s+([A-Z]+-\d+)(?=$|[\s:.])")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_PRIORITY = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Priority(?:\*\*)?\s*:\s*(.*)$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class _Parsed:
    items: dict[str, str | None] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)


def _visible_lines(text: str) -> list[str]:
    """Строки без HTML-комментариев и огороженных блоков кода."""
    lines: list[str] = []
    in_fence = False
    for line in _COMMENT.sub("", text).splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _normalize(raw: str) -> str | None:
    value = raw.replace("*", "").strip()
    if not value:
        return None
    return "Must" if value.lower() == "must" else value


def _parse(text: str) -> _Parsed:
    parsed = _Parsed()
    current: str | None = None
    seen_priority = False
    for line in _visible_lines(text):
        if _ANY_HEADING.match(line):
            match = _HEADING.match(line)
            current = match.group(1) if match else None
            seen_priority = False
            if current is None:
                continue
            if current in parsed.items:
                parsed.ambiguous.add(current)
            else:
                parsed.items[current] = None
            continue
        prio = _PRIORITY.match(line)
        if prio is None or current is None:
            continue
        if seen_priority:
            parsed.ambiguous.add(current)
            continue
        seen_priority = True
        if parsed.items[current] is None:
            parsed.items[current] = _normalize(prio.group(1))
    return parsed


def node_items(text: str) -> dict[str, str | None]:
    """Id требований из заголовков `#### <ID>` и их Priority (или None).

    При повторе id берётся приоритет первого заголовка.
    """
    return _parse(text).items


def weakened(
    before: str, after: str, removes: set[str], downgrades: set[str]
) -> list[str]:
    """Id, исчезнувшие, потерявшие Must или неоднозначные в новом тексте."""
    old, new = _parse(before).items, _parse(after)
    bad = set(new.ambiguous)
    for item, prio in old.items():
        if item not in new.items:
            if item not in removes:
                bad.add(item)
        elif prio == "Must" and new.items[item] != "Must":
            if item not in downgrades:
                bad.add(item)
    return sorted(bad)
