"""Override-решения журнала `dfl` (governance/edge_check/override.py).

Решение привязано к вердикту правила на этом тексте узла
(`finding_digest`: узел, ребро, правило, класс, хэши subject). Другое
правило, класс или изменённый текст узла — принятие не переносится;
переформулировка той же находки ревьюером (недетерминизм, devtools#728) —
переносится.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_digest_binds_rule_class_and_subject_not_wording() -> None:
    base = _digest()
    assert base == _digest(dict(FINDING))
    assert base == _digest({**FINDING, "statement": "другое"})
    assert base == _digest({**FINDING, "location": {"path": "x.md", "lines": [9]}})
    assert base != _digest({**FINDING, "rule_id": "R2"})
    assert base != _digest({**FINDING, "class": "blocker"})
    assert base != _digest(subject=[{"path": "10-requirements.md", "sha256": "s2"}])


def test_accepted_same_finding_unchanged() -> None:
    records = {(NODE, EDGE): _record()}
    assert override.accepted(records, {_digest()}) == {(NODE, EDGE, "R1")}


def test_accepted_rephrased_statement_same_subject() -> None:
    """Ревьюер переформулировал ту же находку на неизменённом узле."""
    rephrased = {**FINDING, "statement": "другое", "location": {"lines": [7, 8]}}
    records = {(NODE, EDGE): _record(rephrased)}
    assert override.accepted(records, {_digest()}) == {(NODE, EDGE, "R1")}


def test_accepted_different_rule_refused() -> None:
    records = {(NODE, EDGE): _record({**FINDING, "rule_id": "R2"})}
    assert override.accepted(records, {_digest()}) is None


def test_accepted_changed_node_text_refused() -> None:
    changed = [{"path": "10-requirements.md", "sha256": "s2"}]
    records = {(NODE, EDGE): _record(subject=changed)}
    assert override.accepted(records, {_digest()}) is None


def test_accepted_row_without_digest_refused(tmp_path: Path) -> None:
    _journal(tmp_path, _row(1, "override"))
    records = {(NODE, EDGE): _record()}
    assert override.accepted(records, override.overridden(tmp_path, 1)) is None


@pytest.mark.parametrize(
    "subject",
    [None, [], [{"path": "10-requirements.md"}], [{"path": "a", "sha256": ""}]],
    ids=["missing", "empty", "no-sha256", "blank-sha256"],
)
def test_accepted_without_subject_hashes_refused(subject: object) -> None:
    """Без хэшей subject принятие не к чему привязать — fail-closed, даже
    если в журнале ровно совпадающий digest."""
    record = {"verdict": "FAIL", "subject": subject, "findings": [FINDING]}
    shas = override.subject_shas(subject)
    digest = override.finding_digest(NODE, EDGE, FINDING, shas)
    assert override.accepted({(NODE, EDGE): record}, {digest}) is None


def test_entries_without_sha256_do_not_share_acceptance() -> None:
    """`[{path: a}]` и `[{path: b}]` не сводятся к одному `[""]`."""
    first = {"verdict": "FAIL", "subject": [{"path": "a"}], "findings": [FINDING]}
    second = {**first, "subject": [{"path": "b"}]}
    digest = override.finding_digest(
        NODE, EDGE, FINDING, override.subject_shas(first["subject"])
    )
    assert override.accepted({(NODE, EDGE): second}, {digest}) is None


@pytest.mark.parametrize(
    ("criteria", "verdict", "expected"),
    [
        ([{"id": "R1", "status": "fail", "reason": "x"}], "FAIL", True),
        ([{"id": "R3", "status": "insufficient", "reason": ""}], "FAIL", False),
        ([{"id": "R1", "status": "fail", "reason": ""}], "FAIL", False),
        ([], "ERROR", False),
    ],
)
def test_overridable_matches_accepted(
    criteria: list[dict], verdict: str, expected: bool
) -> None:
    """Один предикат: `accepted` не None ровно когда `overridable` и все
    digest'ы приняты (ревью #762, находка B)."""
    record = {**_record(), "verdict": verdict, "criteria": criteria}
    records = {(NODE, EDGE): record}
    assert override.overridable(records) is expected
    assert (override.accepted(records, {_digest()}) is not None) is expected


@pytest.mark.parametrize(
    ("record", "words"),
    [
        ({"criteria": [{"id": "R3", "status": "insufficient"}]},
         ["не оценён", "повторите проверку"]),
        ({"verdict": "N/A", "findings": []}, ["неприменимо", "поправьте узел"]),
        ({"findings": []}, ["без блокирующих замечаний", "поправьте узел"]),
        ({"verdict": "ERROR"}, ["ERROR", "повторите проверку"]),
        ({"subject": []}, ["хэш"]),
    ],
)  # fmt: skip
def test_non_overridable_reason_names_cause(record: dict, words: list[str]) -> None:
    records = {(NODE, EDGE): {**_record(), **record}}
    reason = override.non_overridable_reason(records)
    assert reason is not None and EDGE in reason
    assert all(w in reason for w in words)
    if "повторите проверку" not in words:
        assert "повторите проверку" not in reason
    assert override.overridable(records) is False
    assert override.non_overridable_reason({(NODE, EDGE): _record()}) is None


def test_override_revoked_by_later_answer(tmp_path: Path) -> None:
    """override(D), затем option(D): раннер/публикация не принимают — отзыв
    один для dfl и гейта (recheck #762)."""
    _journal(tmp_path, _row(2, "override", _digest()), _row(2, "option", _digest()))
    digests = override.overridden(tmp_path, 2)
    assert digests == set()
    assert override.accepted({(NODE, EDGE): _record()}, digests) is None
    _journal(tmp_path, _row(2, "option", _digest()), _row(2, "override", _digest()))
    assert override.overridden(tmp_path, 2) == {_digest()}
