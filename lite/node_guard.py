"""Сверка «узел не ослаблен» после переписывания автором.

Разбор совпадает с инструментами бандла: id и их кратность — канонический
`node_id_counts`, строка приоритета — `**Priority**:` как у гейта. Fail-closed:
любая неоднозначность в новом тексте (дубль id, несколько строк Priority,
нечитаемый Priority у бывшего Must) считается ослаблением. Блоки кода и
HTML-комментарии НЕ вырезаются: строка Priority или заголовок внутри них
учитываются, чтобы агент не мог спрятать вторую строку от гейта.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from governance.decomposition_guard import node_id_counts

# Только разметка секций: id берёт `node_id_counts`, граница — не продолжение id.
_SECTION_ID = re.compile(r"^####\s+([A-Z][A-Z0-9]*-\d+[a-z]?)(?![-\w])")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_PRIORITY = re.compile(r"\*\*Priority\*\*:(.*)$")
_LEADING_WORD = re.compile(r"[\s*]*([A-Za-z]+)")


@dataclass
class _Parsed:
    items: dict[str, str | None] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)


def _normalize(raw: str) -> str | None:
    """Ведущее слово значения Priority; `must` в любом регистре — `Must`."""
    match = _LEADING_WORD.match(raw)
    if match is None:
        return None
    word = match.group(1)
    return "Must" if word.lower() == "must" else word


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Секции по заголовкам: (id секции или None, значения строк Priority)."""
    sections: list[tuple[str | None, list[str]]] = []
    for line in text.splitlines():
        if _ANY_HEADING.match(line):
            match = _SECTION_ID.match(line)
            sections.append((match.group(1) if match else None, []))
            continue
        prio = _PRIORITY.search(line)
        if prio is not None and sections:
            sections[-1][1].append(prio.group(1))
    return sections


def _parse(text: str) -> _Parsed:
    counts = node_id_counts(text)
    parsed = _Parsed(ambiguous={item for item, n in counts.items() if n > 1})
    parsed.items = dict.fromkeys(counts)
    for item, values in _sections(text):
        if item is None or item not in counts:
            continue
        if len(values) > 1:
            parsed.ambiguous.add(item)
        if parsed.items[item] is None and values:
            parsed.items[item] = _normalize(values[0])
    return parsed


def node_items(text: str) -> dict[str, str | None]:
    """Id пунктов узла (как у `node_id_counts`) и их Priority (или None).

    При повторе id берётся приоритет первой секции с читаемым значением.
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
