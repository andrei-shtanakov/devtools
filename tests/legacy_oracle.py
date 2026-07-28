"""Frozen copy of the PRE-PACKAGE devtools blocker resolution — TEST ORACLE ONLY.

This is the old `check-plan-fields.py` resolution (checkbox regex + substring
blocker match + open/closed staleness), preserved verbatim so the migrated
checker can be characterized against it on a frozen fleet. It is NOT a second
production path: nothing outside the test suite imports it.
"""

from __future__ import annotations

import re

CHECKBOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(\S.*)$")
BLOCKED_BY = re.compile(r"@blocked_by:([A-Za-z0-9._-]+)#([A-Za-z0-9._/-]+)")


def _items(text: str) -> list[tuple[int, str, bool]]:
    """(lineno, text, is_open) for every checkbox line."""
    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = CHECKBOX.match(line)
        if m:
            out.append((lineno, m.group(2), m.group(1) == " "))
    return out


def flagged(fleet: dict[str, str | None]) -> set[tuple[str, int, str, str, str]]:
    """The old checker's flagged relations over an in-memory fleet.

    ``fleet`` maps a repo to its TODO text, or ``None`` when the repo is cloned
    but keeps no TODO (the old ``unplanned`` set). A referenced repo absent from
    the mapping is "not cloned". Returns
    ``{(source_repo, source_line, target, slug, outcome)}`` where ``outcome`` is
    one of ``dangling`` / ``stale`` / ``no-todo`` / ``not-cloned``.
    """
    planned = {r: _items(t) for r, t in fleet.items() if t is not None}
    unplanned = {r for r, t in fleet.items() if t is None}
    out: set[tuple[str, int, str, str, str]] = set()
    for srepo, text in fleet.items():
        if text is None:
            continue
        for lineno, itext, is_open in _items(text):
            if not is_open:
                continue
            for target, slug in BLOCKED_BY.findall(itext):
                key = target.lower()
                if key == srepo.lower():
                    continue
                if key in {u.lower() for u in unplanned}:
                    out.add((srepo, lineno, key, slug, "no-todo"))
                    continue
                lut = {r.lower(): items for r, items in planned.items()}
                if key not in lut:
                    out.add((srepo, lineno, key, slug, "not-cloned"))
                    continue
                hits = [it for it in lut[key] if slug in it[1]]
                if not hits:
                    out.add((srepo, lineno, key, slug, "dangling"))
                elif all(not it[2] for it in hits):
                    out.add((srepo, lineno, key, slug, "stale"))
    return out
