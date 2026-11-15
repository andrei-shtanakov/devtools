"""Tests for lite.personal.check (Task 6)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lite import edge_journal as ej
from lite.personal import check, state
from lite.personal.brief import Brief, Context, Function, Goal

FIXTURES = Path(__file__).parent / "fixtures" / "personal"


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    return root


def _run(tmp_path: Path) -> state.PersonalRun:
    run = state.new_personal_run(
        "Заметки", "git@github.com:o/r.git", "o/r", str(tmp_path), date(2026, 11, 15)
    )
    state.save(run)
    return run


def _brief(*counts: int) -> Brief:
    fns = tuple(
        Function(f"FR-{i:02d}", "f", ("G-1",), tuple("c" * n), "w")
        for i, n in enumerate(counts, 1)
    )
    return Brief((Goal("G-1", "g", "t"),), fns, Context("u", "a", "n", "l", "p"))


def _fake(verdict: str, findings: list[dict]):
    calls: list[tuple] = []

    def fake(edge_id, bundle, subject, bases, **kw):
        calls.append((edge_id, bundle, subject, bases, kw))
        return {
            "edge": edge_id,
            "verdict": verdict,
            "findings": findings,
            "subject": [],
            "error_code": "reviewer_failed",
            "reason": "boom",
        }

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_record_layout_read_by_journal(tmp_path: Path) -> None:
    run = _run(tmp_path)
    finding = {
        "rule_id": "R1",
        "class": "major",
        "statement": "плохо",
        "location": {"path": "20-design.md", "lines": "1"},
    }
    fake = _fake("FAIL", [finding])
    res = check.check_node(run, "personal-design", check=fake)
    edge = "personal-design-vs-brief"
    path = state.run_dir(run.run_id) / "edge-check/w1" / f"personal-design--{edge}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["node"] == "personal-design" and rec["wave"] == 1
    assert res.verdict == "FAIL" and res.failure == ""
    assert [f.node for f in res.findings] == ["personal-design"]
    assert res.findings == ej.blocking_findings(state.run_dir(run.run_id), 1)
    assert [r for r, _ in fake.calls[0][3]] == ["brief"]


def test_error_is_failure_not_findings(tmp_path: Path) -> None:
    run = _run(tmp_path)
    res = check.check_node(run, "personal-decomposition", check=_fake("ERROR", []))
    assert res.verdict == "ERROR" and res.failure and res.findings == []

    def boom(*a, **k):
        raise RuntimeError("down")

    res = check.check_node(run, "personal-design", check=boom)
    assert res.verdict == "ERROR" and "down" in res.failure and res.findings == []


def test_decomposition_problems_uncovered_criterion() -> None:
    text = (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8")
    problems = check.decomposition_problems(text, _brief(2, 1))
    assert problems == ["критерий FR-01/2 не покрыт ни одной проверкой BEH"]


def test_decomposition_valid_sample() -> None:
    text = (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8")
    assert check.decomposition_problems(text, _brief(1, 1)) == []


def test_split_decomposition() -> None:
    text = (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8")
    checks, whole = check.split_decomposition(text)
    assert "BEH-01" in checks and "DT-01" not in checks and whole == text
    assert check.split_decomposition("x")[0] == ""


def test_error_after_fail_leaves_no_stale_findings(tmp_path: Path) -> None:
    run = _run(tmp_path)
    finding = {"rule_id": "R1", "class": "major", "statement": "x", "location": {}}
    check.check_node(run, "personal-design", check=_fake("FAIL", [finding]))
    rd = state.run_dir(run.run_id)
    assert ej.blocking_findings(rd, 1)

    def boom(*a, **k):
        raise RuntimeError("down")

    res = check.check_node(run, "personal-design", check=boom)
    assert res.failure and ej.blocking_findings(rd, 1) == []


def test_cited_accepts_semicolons() -> None:
    assert check._cited("**Критерии:** FR-01/1; FR-02/1, FR-03/2") == {
        "FR-01/1",
        "FR-02/1",
        "FR-03/2",
    }


def test_unmet_criteria_from_record(tmp_path: Path) -> None:
    """FAIL без блокирующих находок: непройденные критерии записи — в `unmet`."""
    run = _run(tmp_path)

    def fake(edge_id, bundle, subject, bases, **kw):
        return {
            "edge": edge_id,
            "verdict": "FAIL",
            "findings": [],
            "subject": [],
            "criteria": [
                {"id": "C1", "status": "pass", "reason": "ок"},
                {"id": "C2", "status": "fail", "reason": "нет модулей"},
            ],
        }

    res = check.check_node(run, "personal-design", check=fake)
    assert res.verdict == "FAIL" and res.findings == []
    assert res.unmet == ("C2 (fail): нет модулей",)


@pytest.mark.parametrize(
    "line",
    ["FR-01/1 и FR-02/1", "FR-01/1 FR-02/1", "`FR-01/1`, FR-02/1.", "FR-01/1; FR-02/1"],
)
def test_cited_refs_any_separator(line: str) -> None:
    text = f"## Проверки\n\n#### BEH-01: x\n**Критерии:** {line}\n"
    assert check._cited(check.split_decomposition(text)[0]) == {"FR-01/1", "FR-02/1"}
