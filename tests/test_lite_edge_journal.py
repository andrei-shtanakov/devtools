"""Находки волны и журнал решений (lite/edge_journal.py)."""

from __future__ import annotations

import json
from pathlib import Path

from lite import edge_journal as ej


def _write_record(run_dir: Path, wave: int) -> None:
    out = run_dir / "edge-check" / f"w{wave}"
    out.mkdir(parents=True)
    record = {
        "node": "requirements",
        "edge": "requirements-vs-charter",
        "verdict": "FAIL",
        "findings": [
            {
                "rule_id": "R1",
                "class": "major",
                "statement": "Цель не покрыта",
                "location": {"path": "10-requirements.md", "lines": [410, 439]},
            },
            {
                "rule_id": "R4",
                "class": "minor",
                "statement": "субъективен",
                "location": {"path": "10-requirements.md", "lines": [1, 1]},
            },
        ],
    }
    (out / "requirements--requirements-vs-charter.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


def _decision(rule: str, kind: str = "option", rnd: int = 1) -> ej.Decision:
    return ej.Decision(
        key=("requirements", "requirements-vs-charter", rule),
        question="что делаем?",
        answer="правим",
        kind=kind,
        round=rnd,
        allows={"removes": [rule], "downgrades": []},
    )


def test_blocking_findings_from_records(tmp_path: Path) -> None:
    _write_record(tmp_path, 2)
    found = ej.blocking_findings(tmp_path, 2)
    assert len(found) == 1
    assert found[0].key == ("requirements", "requirements-vs-charter", "R1")
    assert found[0].path == "10-requirements.md"
    assert found[0].lines == "[410, 439]"


def test_journal_roundtrip_per_wave(tmp_path: Path) -> None:
    ej.append(tmp_path, 2, _decision("R1"))
    ej.append(tmp_path, 3, _decision("R2", rnd=2))
    assert ej.load(tmp_path, 2) == [_decision("R1")]
    assert ej.load(tmp_path, 3) == [_decision("R2", rnd=2)]
    assert ej.load(tmp_path, 4) == []


def test_overridden_keys(tmp_path: Path) -> None:
    ej.append(tmp_path, 2, _decision("R1", kind="override"))
    ej.append(tmp_path, 2, _decision("R2"))
    assert ej.overridden(tmp_path, 2) == {
        ("requirements", "requirements-vs-charter", "R1")
    }


def test_corrupt_line_skipped(tmp_path: Path) -> None:
    ej.append(tmp_path, 2, _decision("R1"))
    with (tmp_path / ej.JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write("{битая строка\n")
    ej.append(tmp_path, 2, _decision("R2"))
    assert [d.key[2] for d in ej.load(tmp_path, 2)] == ["R1", "R2"]
