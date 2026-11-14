"""Сверка «узел не ослаблен» после переписывания автором."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^####\s+([A-Z]+-\d+)\b")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_PRIORITY = re.compile(r"^\*\*Priority\*\*:\s*(\S+)")


def node_items(text: str) -> dict[str, str | None]:
    """Id требований из заголовков `#### <ID>` и их Priority (или None)."""
    items: dict[str, str | None] = {}
    current: str | None = None
    for line in text.splitlines():
        if _ANY_HEADING.match(line):
            match = _HEADING.match(line)
            current = match.group(1) if match else None
            if current is not None:
                items[current] = None
            continue
        prio = _PRIORITY.match(line)
        if prio and current is not None and items[current] is None:
            items[current] = prio.group(1)
    return items


def weakened(
    before: str, after: str, removes: set[str], downgrades: set[str]
) -> list[str]:
    """Id, исчезнувшие или потерявшие Must, кроме разрешённых решениями."""
    old, new = node_items(before), node_items(after)
    bad: list[str] = []
    for item, prio in old.items():
        if item not in new:
            if item not in removes:
                bad.append(item)
        elif prio == "Must" and new[item] != "Must" and item not in downgrades:
            bad.append(item)
    return sorted(bad)
