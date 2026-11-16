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
_HEADING_RE = re.compile(r"^####\s+([A-Z][A-Z0-9]*-\d+[a-z]?)(?=[\s:]|$)(.*)$")
_FIELD_RE = re.compile(r"^\*\*[^*]+\*\*:")  # `**Priority**: Must` — не текст пункта
_CODE_SPAN_RE = re.compile(r"`[^`]*`")
TITLE_MAX = 60


def node_items(text: str) -> dict[str, str | None]:
    """Id пунктов узла (как у `node_id_counts`) и их Priority по гейту или None."""
    priorities, _ = _parse_requirements(text)
    return {item: priorities.get(item) for item in node_id_counts(text)}


def item_titles(text: str) -> dict[str, str]:
    """Id пункта → название из заголовка `#### <ID>: <название>`; без
    названия — начало текста пункта (до `TITLE_MAX` знаков с «…»)."""
    titles: dict[str, str] = {}
    lines = text.splitlines()
    for n, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match or match.group(1) in titles:
            continue
        title = _clean(match.group(2))
        if not title:
            title = next((_clean(b) for b in _body(lines[n + 1 :]) if _clean(b)), "")
        titles[match.group(1)] = _short(title)
    return titles


def _body(lines: list[str]) -> list[str]:
    """Строки пункта до следующего заголовка, без полей `**Поле**:`."""
    body: list[str] = []
    for line in lines:
        if line.startswith("#"):
            break
        if not _FIELD_RE.match(line.strip()):
            body.append(line)
    return body


def _clean(text: str) -> str:
    """Без code-span (`traces: […]`) и разделителей после id."""
    return _CODE_SPAN_RE.sub("", text).strip().lstrip(":—–-. ").strip()


def _short(text: str) -> str:
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX].rstrip() + "…"


def _findings_by_id(text: str) -> dict[str, Counter[str]]:
    """Находки гейта, сгруппированные по id (`<grammar>`, если id не извлечь)."""
    grouped: dict[str, Counter[str]] = {}
    for finding in _parse_requirements(text)[1]:
        match = _FINDING_ID.match(finding)
        key = match.group(1) if match else GRAMMAR_SENTINEL
        grouped.setdefault(key, Counter())[finding] += 1
    return grouped


def weakened(
    before: str, after: str, removes: set[str], downgrades: set[str]
) -> list[str]:
    """Id, исчезнувшие, потерявшие Must, дублированные или с новой находкой гейта.

    Id с находкой гейта в `before` (старый приоритет гейту не известен)
    fail-closed: тронутый — набор его находок изменился — сообщается, если
    в `after` он не строгий Must и понижение не разрешено. Нетронутый
    (находки те же) молчит: прежнее состояние, не ослабление.
    """
    old_ids, new_ids = node_id_counts(before), node_id_counts(after)
    old_prio, new_prio = _parse_requirements(before)[0], _parse_requirements(after)[0]
    old_found, new_found = _findings_by_id(before), _findings_by_id(after)
    bad = {item for item, n in new_ids.items() if n > 1}
    bad |= {i for i, f in new_found.items() if f - old_found.get(i, Counter())}
    bad |= {i for i in old_ids if i not in new_ids and i not in removes}
    bad |= {
        i
        for i, prio in old_prio.items()
        if prio == "Must" and new_prio.get(i) != "Must" and i not in downgrades
    }
    bad |= {
        i
        for i, f in old_found.items()
        if f != new_found.get(i, Counter())
        and new_prio.get(i) != "Must"
        and i not in downgrades
    }
    return sorted(bad)
