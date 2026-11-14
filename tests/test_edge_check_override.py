"""Override-решения журнала `dfl` (governance/edge_check/override.py).

Решение привязано к самой находке (`finding_digest`): тот же ключ
(узел, ребро, правило) с другой формулировкой, местом или на изменённом
тексте узла — другая находка, принятие на неё не переносится.
"""

from __future__ import annotations

import json
from pathlib import Path

from governance.edge_check import override

FINDING = {
    "rule_id": "R1",
    "class": "major",
    "statement": "цель не покрыта",
    "location": {"path": "10-requirements.md", "lines": [3, 5]},
}
SUBJECT = [{"path": "10-requirements.md", "sha256": "s1"}]
NODE, EDGE = "requirements", "requirements-vs-charter"


def _record(finding: dict = FINDING, subject: list[dict] = SUBJECT) -> dict:
    return {"verdict": "FAIL", "subject": subject, "findings": [finding]}


def _digest(finding: dict = FINDING, subject: list[dict] = SUBJECT) -> str:
    return override.finding_digest(NODE, EDGE, finding, override.subject_shas(subject))


def _row(wave: int, kind: str, digest: str | None = None) -> str:
    row: dict = {"wave": wave, "kind": kind, "key": [NODE, EDGE, "R1"]}
    if digest is not None:
        row["digest"] = digest
    return json.dumps(row, ensure_ascii=False)


def _journal(tmp_path: Path, *rows: str) -> None:
    (tmp_path / override.JOURNAL).write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_overridden_filters_wave_kind_and_needs_digest(tmp_path: Path) -> None:
    _journal(
        tmp_path,
        _row(2, "override", "d1"),
        _row(2, "option", "d2"),
        _row(3, "override", "d3"),
        _row(2, "override"),  # старая строка без digest — не принимает
    )
    assert override.overridden(tmp_path, 2) == {"d1"}


def test_overridden_missing_and_corrupt(tmp_path: Path) -> None:
    assert override.overridden(tmp_path, 1) == set()
    text = "не json\n" + _row(1, "override", "d") + '\n{"wave": 1}\n'
    (tmp_path / override.JOURNAL).write_text(text, encoding="utf-8")
    assert override.overridden(tmp_path, 1) == {"d"}


def test_digest_binds_statement_location_and_subject() -> None:
    base = _digest()
    assert base == _digest(dict(FINDING))
    assert base != _digest({**FINDING, "statement": "другое"})
    assert base != _digest({**FINDING, "location": {"path": "x.md", "lines": [3, 5]}})
    assert base != _digest({**FINDING, "class": "blocker"})
    assert base != _digest(subject=[{"path": "10-requirements.md", "sha256": "s2"}])


def test_accepted_same_finding_unchanged() -> None:
    records = {(NODE, EDGE): _record()}
    assert override.accepted(records, {_digest()}) == {(NODE, EDGE, "R1")}


def test_accepted_same_key_different_statement_refused() -> None:
    records = {(NODE, EDGE): _record({**FINDING, "statement": "другое"})}
    assert override.accepted(records, {_digest()}) is None


def test_accepted_changed_node_text_refused() -> None:
    changed = [{"path": "10-requirements.md", "sha256": "s2"}]
    records = {(NODE, EDGE): _record(subject=changed)}
    assert override.accepted(records, {_digest()}) is None


def test_accepted_row_without_digest_refused(tmp_path: Path) -> None:
    _journal(tmp_path, _row(1, "override"))
    records = {(NODE, EDGE): _record()}
    assert override.accepted(records, override.overridden(tmp_path, 1)) is None
