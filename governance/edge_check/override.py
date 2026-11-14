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
#: Классы, валящие проверку: `severity.blocking` правил ребра
#: (`contracts/edge-check/v1/rules/*.yaml`, у всех рёбер одинаков — сверяет
#: `tests/test_lite_stop_findings.py`).
BLOCKING = frozenset({"blocker", "major"})

Key = tuple[str, str, str]


def _key_of(row: object, wave: int) -> Key | None:
    if not isinstance(row, dict):
        return None
    if row.get("wave") != wave or row.get("kind") != OVERRIDE_KIND:
        return None
    key = row.get("key")
    if not isinstance(key, list) or len(key) != 3:
        return None
    return (str(key[0]), str(key[1]), str(key[2]))


def overridden(run_dir: Path, wave: int) -> set[Key]:
    """Ключи (узел, ребро, правило) с решением `override` в волне `wave`.

    Битые строки журнала пропускаются; нет файла — пустое множество.
    """
    path = run_dir / JOURNAL
    if not path.is_file():
        return set()
    keys: set[Key] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            key = _key_of(json.loads(line), wave)
        except json.JSONDecodeError:
            continue
        if key is not None:
            keys.add(key)
    return keys


def accepted(records: dict[tuple[str, str], dict], keys: set[Key]) -> set[Key] | None:
    """Ключи блокирующих находок уровня, если человек принял их ВСЕ; иначе `None`.

    Принимается вопреки только FAIL, объяснённый блокирующими находками:
    ребро `ERROR` или с иным вердиктом, FAIL без блокирующих находок, пункт
    правил не `pass`, не покрытый находкой (`insufficient`, `fail` без
    причины), и непринятый ключ — `None` (прежний стоп).
    """
    found: set[Key] = set()
    for (node, edge), record in records.items():
        verdict = record.get("verdict")
        if verdict == "PASS":
            continue
        rules = _blocking_rules(record)
        if verdict != "FAIL" or not rules or not _explained(record):
            return None
        found |= {(node, edge, rule) for rule in rules}
    if not found or not found <= keys:
        return None
    return found


def _blocking_rules(record: dict) -> set[str]:
    return {
        str(f.get("rule_id"))
        for f in record.get("findings") or []
        if isinstance(f, dict) and f.get("class") in BLOCKING
    }


def _explained(record: dict) -> bool:
    """Каждый не-`pass` пункт — `fail` с причиной и находкой по нему."""
    remarked = {
        str(f.get("rule_id"))
        for f in record.get("findings") or []
        if isinstance(f, dict)
    }
    return all(
        c.get("status") == "pass"
        or (
            c.get("status") == "fail"
            and str(c.get("id")) in remarked
            and bool(str(c.get("reason") or "").strip())
        )
        for c in record.get("criteria") or []
        if isinstance(c, dict)
    )
