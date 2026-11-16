"""Прогресс фазы реализации dfl (#765): путь БД, снимок, тексты, наблюдатель."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from governance import run_progress as rp

SCHEMA = """
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
                    completed_at TEXT);
CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
                       timestamp TEXT, success INTEGER, duration_seconds REAL,
                       error TEXT);
"""


def _db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA)
    return path


def _task(db: Path, task_id: str, status: str, started: str | None = None,
          completed: str | None = None) -> None:  # fmt: skip
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO tasks VALUES (?, ?, ?, ?)",
            (task_id, status, started, completed),
        )


def _attempt(db: Path, task_id: str, ts: str, ok: bool, error: str | None = None):
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO attempts (task_id, timestamp, success, duration_seconds,"
            " error) VALUES (?, ?, ?, 1.0, ?)",
            (task_id, ts, int(ok), error),
        )


# --- Task 1: путь БД, задачи, снимок, тексты --------------------------------


def test_state_db_path_default(tmp_path: Path) -> None:
    got = rp.state_db_path(str(tmp_path), "WS-1-")
    assert got == tmp_path / "spec" / ".executor-WS-1-state.db"


def test_state_db_path_config_paths_state(tmp_path: Path) -> None:
    (tmp_path / "spec-runner.config.yaml").write_text(
        "paths:\n  state: x/s.db\n", encoding="utf-8"
    )
    assert rp.state_db_path(str(tmp_path), "WS-1-") == tmp_path / "x" / "s.db"


def test_state_db_path_legacy_wrapper(tmp_path: Path) -> None:
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "executor.config.yaml").write_text(
        "executor:\n  paths:\n    root: sub\n    state: s.db\n", encoding="utf-8"
    )
    assert rp.state_db_path(str(tmp_path), "WS-1-") == tmp_path / "sub" / "s.db"


def test_state_db_path_bad_yaml_default(tmp_path: Path) -> None:
    (tmp_path / "spec-runner.config.yaml").write_text(": : [", encoding="utf-8")
    got = rp.state_db_path(str(tmp_path), "WS-1-")
    assert got == tmp_path / "spec" / ".executor-WS-1-state.db"


TASKS = (
    "# Tasks\n\n### TASK-001: Команда add\nP1 | TODO\n\n"
    "### TASK-002: Команда find\nP1 | TODO\n\n### TASK-003: Порог\nP2 | TODO\n"
)


def test_read_tasks_titles_in_order() -> None:
    assert rp.read_tasks(TASKS) == [
        ("TASK-001", "Команда add"),
        ("TASK-002", "Команда find"),
        ("TASK-003", "Порог"),
    ]


def test_read_snapshot_rows(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    _task(db, "TASK-001", "running", "2026-11-17T10:00:00")
    _attempt(db, "TASK-001", "2026-11-17T10:01:00", False, "boom\nтрасса")
    _attempt(db, "TASK-001", "2026-11-17T10:02:00", False, "второй")
    snap = rp.read_snapshot(db)
    assert snap == {
        "TASK-001": rp.TaskRow("running", "2026-11-17T10:00:00", None, 2, "второй")
    }


def test_read_snapshot_missing_is_none(tmp_path: Path) -> None:
    assert rp.read_snapshot(tmp_path / "нет.db") is None


def test_read_snapshot_unreadable_is_none(tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    assert rp.read_snapshot(junk) is None


def test_fmt_duration() -> None:
    assert rp.fmt_duration(75) == "01:15"
    assert rp.fmt_duration(3725) == "1:02:05"
    assert rp.fmt_duration(-3) == "00:00"


def test_event_lines_exact() -> None:
    assert rp.event_line("started", "TASK-004", "find") == "▶ TASK-004 «find» — начата"
    assert rp.event_line("done", "TASK-004", "find", seconds=1122) == (
        "✓ TASK-004 готова · 18:42"
    )
    assert rp.event_line("retry", "TASK-005", "Порог", attempt=2) == (
        "↻ TASK-005 «Порог» — попытка 2"
    )
    assert rp.event_line("failed", "TASK-005", "Порог", error="boom\nтрасса") == (
        "✗ TASK-005 не прошла: boom"
    )


def test_live_and_summary_lines() -> None:
    assert rp.live_line("TASK-005", 5, 16, 197) == "  TASK-005 · 5 из 16 · 03:17"
    assert rp.live_line("TASK-005", 1, 0, 5) == "  TASK-005 · 00:05"
    assert rp.summary_line(4, 16, 3725) == "реализация: сделано 4 из 16 · 1:02:05"
