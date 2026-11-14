"""Чтение override-ключей журнала решений (governance/edge_check/override.py)."""

from __future__ import annotations

import json
from pathlib import Path

from governance.edge_check import override


def _row(wave: int, kind: str, key: list[str]) -> str:
    return json.dumps({"wave": wave, "kind": kind, "key": key}, ensure_ascii=False)


def test_overridden_filters_wave_and_kind(tmp_path: Path) -> None:
    rows = [
        _row(2, "override", ["requirements", "e1", "R1"]),
        _row(2, "option", ["requirements", "e1", "R2"]),
        _row(3, "override", ["design", "e2", "R3"]),
    ]
    (tmp_path / override.JOURNAL).write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert override.overridden(tmp_path, 2) == {("requirements", "e1", "R1")}


def test_overridden_missing_and_corrupt(tmp_path: Path) -> None:
    assert override.overridden(tmp_path, 1) == set()
    text = "не json\n" + _row(1, "override", ["n", "e", "R1"]) + '\n{"wave": 1}\n'
    (tmp_path / override.JOURNAL).write_text(text, encoding="utf-8")
    assert override.overridden(tmp_path, 1) == {("n", "e", "R1")}
