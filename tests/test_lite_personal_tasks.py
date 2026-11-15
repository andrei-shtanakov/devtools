"""Tests for lite.personal.tasks (Task 7)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from lite.personal import state, tasks

FIXTURES = Path(__file__).parent / "fixtures" / "personal"
GENERATED_AT = "2026-11-15T10:00:00Z"


def _run() -> state.PersonalRun:
    return state.new_personal_run(
        "notes cli", "/r", "o/notes", "/r", date(2026, 11, 15)
    )


def _render() -> str:
    return tasks.render_personal_tasks(
        _run(),
        (FIXTURES / "30-decomposition.md").read_text(encoding="utf-8"),
        (FIXTURES / "20-design.md").read_text(encoding="utf-8"),
        GENERATED_AT,
    )


def test_one_task_per_dt_with_targets() -> None:
    text = _render()
    assert text.count("### TASK-") == 2
    assert "tests/test_add.py::test_add_appends_line" in text
    assert "tests/test_find.py::test_find_prints_matches" in text


def test_no_frontmatter() -> None:
    text = _render()
    assert not text.startswith("---")
    assert "spec_stage" not in text


def test_tasks_path_template() -> None:
    assert tasks.TASKS_PATH.format(ws_id="w") == "spec/w-tasks.md"
