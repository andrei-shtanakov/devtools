"""Ключи замечаний edge-check, снятых человеком (журнал решений `dfl`).

Журнал `edge-decisions.jsonl` пишет `lite/edge_journal.py`; читает его и
раннер, поэтому читатель override-ключей живёт здесь: governance не
импортирует lite (направление зависимости — lite → governance).
"""

from __future__ import annotations

import json
from pathlib import Path

JOURNAL = "edge-decisions.jsonl"
OVERRIDE_KIND = "override"


def _key_of(row: object, wave: int) -> tuple[str, str, str] | None:
    if not isinstance(row, dict):
        return None
    if row.get("wave") != wave or row.get("kind") != OVERRIDE_KIND:
        return None
    key = row.get("key")
    if not isinstance(key, list) or len(key) != 3:
        return None
    return (str(key[0]), str(key[1]), str(key[2]))


def overridden(run_dir: Path, wave: int) -> set[tuple[str, str, str]]:
    """Ключи (узел, ребро, правило) с решением `override` в волне `wave`.

    Битые строки журнала пропускаются; нет файла — пустое множество.
    """
    path = run_dir / JOURNAL
    if not path.is_file():
        return set()
    keys: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            key = _key_of(json.loads(line), wave)
        except json.JSONDecodeError:
            continue
        if key is not None:
            keys.add(key)
    return keys
