"""`governance/review_verdict`: вердикт ревью из маркера тела и его запись в
журнал прогона (план 3c-2, Task 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import review_verdict as rv
from governance import run_state as rs

HEAD = "a" * 40
OTHER = "b" * 40


def _marker(head: str = HEAD, fp: str = "f00d", verdict: str = "approve") -> str:
    fp_part = f" fp={fp}" if fp else ""
    return f"<!-- codex-terminal-review head={head}{fp_part} verdict={verdict} -->"


def test_strict_marker_without_verdict_is_none() -> None:
    body = f"тело\n<!-- codex-terminal-review head={HEAD} fp=f00d -->\n"
    assert rv.parse_marker(body) is None


def test_single_test_marker_yields_values() -> None:
    body = f"## Вердикт\n\nтекст\n{_marker(verdict='request-changes')}\n"
    assert rv.parse_marker(body) == rv.ReviewVerdict(HEAD, "f00d", "request-changes")


def test_marker_without_fp_yields_empty_fp() -> None:
    assert rv.parse_marker(f"x\n{_marker(fp='')}") == rv.ReviewVerdict(
        HEAD, "", "approve"
    )


def test_two_markers_are_none() -> None:
    body = f"{_marker(OTHER)}\nтекст\n{_marker()}\n"
    assert rv.parse_marker(body) is None


def test_marker_not_last_line_is_none() -> None:
    assert rv.parse_marker(f"текст\n{_marker()}\nхвост\n") is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        _marker(head="abc"),
        _marker(verdict="approved"),
        "<!-- ai-prosto-scope-review version=1 kind=prose-only head=" + HEAD + " -->",
    ],
)
def test_malformed_or_absent_is_none(body: str | None) -> None:
    assert rv.parse_marker(body) is None


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    return tmp_path / "runs"


def _state(runs_root: Path) -> rs.RunState:
    state = rs.new_run(
        "s", "r", "o/r", "WS-1", "/t", "b", "default", "run-v", mode="test"
    )
    rs.op_complete(state, "finalize-1", finalize_pr=7, review_exit=0)
    return state


def test_record_review_persists_and_positive_on(runs_root: Path) -> None:
    state = _state(runs_root)
    assert rv.positive_on(state, "finalize-1", HEAD) is False
    rv.record_review(state, "finalize-1", rv.ReviewVerdict(HEAD, "f00d", "approve"))
    on_disk = rs.load("run-v").ops["finalize-1"]
    assert on_disk["verdict"] == {"head": HEAD, "fp": "f00d", "verdict": "approve"}
    assert on_disk["review_exit"] == 0, "прочие поля операции не тронуты"
    assert rv.positive_on(state, "finalize-1", HEAD) is True
    assert rv.positive_on(state, "finalize-1", OTHER) is False, "чужая голова"
    assert rv.positive_on(state, "missing", HEAD) is False


def test_negative_verdict_is_not_positive(runs_root: Path) -> None:
    state = _state(runs_root)
    rv.record_review(state, "finalize-1", rv.ReviewVerdict(HEAD, "", "request-changes"))
    assert rv.positive_on(state, "finalize-1", HEAD) is False
