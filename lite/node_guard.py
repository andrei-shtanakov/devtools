"""Сверка «узел не ослаблен» после переписывания автором.

Своего разбора markdown здесь нет: id и их кратность — канонический
`node_id_counts`, приоритеты и находки грамматики — парсер требований
самого гейта. Fail-closed: новая находка грамматики в тексте после
переписывания считается ослаблением затронутого id.
"""

from __future__ import annotations

import re
from collections import Counter

# Приватный парсер гейта импортируется намеренно: единственный источник его
# грамматики требований — своя копия снова разошлась бы с гейтом.
from governance.acceptance_guard import _parse_requirements
from governance.decomposition_guard import node_id_counts

GRAMMAR_SENTINEL = "<grammar>"
_FINDING_ID = re.compile(r"([^\s:]+):\s")


def node_items(text: str) -> dict[str, str | None]:
    """Id пунктов узла (как у `node_id_counts`) и их Priority по гейту или None."""
    priorities, _ = _parse_requirements(text)
    return {item: priorities.get(item) for item in node_id_counts(text)}


def _new_finding_ids(before: str, after: str) -> set[str]:
    """Id из находок гейта в `after`, которых не было в `before` (мультимножество)."""
    fresh = Counter(_parse_requirements(after)[1]) - Counter(
        _parse_requirements(before)[1]
    )
    ids: set[str] = set()
    for finding in fresh:
        match = _FINDING_ID.match(finding)
        ids.add(match.group(1) if match else GRAMMAR_SENTINEL)
    return ids


def weakened(
    before: str, after: str, removes: set[str], downgrades: set[str]
) -> list[str]:
    """Id, исчезнувшие, потерявшие Must, дублированные или с новой находкой гейта."""
    old_ids, new_ids = node_id_counts(before), node_id_counts(after)
    old_prio, new_prio = _parse_requirements(before)[0], _parse_requirements(after)[0]
    bad = {item for item, n in new_ids.items() if n > 1}
    bad |= _new_finding_ids(before, after)
    bad |= {i for i in old_ids if i not in new_ids and i not in removes}
    bad |= {
        i
        for i, prio in old_prio.items()
        if prio == "Must" and new_prio.get(i) != "Must" and i not in downgrades
    }
    return sorted(bad)
