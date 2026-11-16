"""Прогресс фазы реализации dfl (#765): путь БД, снимок, тексты, наблюдатель."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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


# --- Task 2: наблюдатель -----------------------------------------------------

T0 = datetime(2026, 11, 17, 10, 0, 0)


def _ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def _at(seconds: float) -> float:
    return (T0 + timedelta(seconds=seconds)).timestamp()


def _watcher(db: Path, *, live: bool = True):
    out: list[str] = []
    lives: list[str] = []
    w = rp.Watcher(
        db,
        rp.read_tasks(TASKS),
        emit=out.append,
        live=lives.append if live else None,
    )
    return w, out, lives


def test_baseline_suppresses_old_events(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    _task(db, "TASK-001", "success", _ts(0), _ts(60))
    w, out, _ = _watcher(db)
    w.begin(_at(100))
    w.tick(_at(101))
    assert out == []
    assert w.summary(_at(160)) == "реализация: сделано 1 из 3 · 01:00"


def test_started_then_done_lines(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    w, out, _ = _watcher(db)
    w.begin(_at(0))
    _task(db, "TASK-001", "running", _ts(1))
    w.tick(_at(2))
    _task(db, "TASK-001", "success", _ts(1), _ts(1123))
    w.tick(_at(1124))
    w.tick(_at(1125))
    assert out == ["▶ TASK-001 «Команда add» — начата", "✓ TASK-001 готова · 18:42"]


def test_retry_event_and_timer_reset(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    w, out, lives = _watcher(db)
    w.begin(_at(0))
    _task(db, "TASK-002", "running", _ts(0))
    w.tick(_at(10))
    _attempt(db, "TASK-002", _ts(50), False, "тесты красные")
    w.tick(_at(51))  # попытка записана, статус ещё running — не событие
    _task(db, "TASK-002", "running", _ts(60))
    w.tick(_at(65))
    assert out[-1] == "↻ TASK-002 «Команда find» — попытка 2"
    assert lives[-1] == "  TASK-002 · 1 из 3 · 00:05"
    _task(db, "TASK-002", "success", _ts(60), _ts(120))
    w.tick(_at(121))
    assert out[-1] == "✓ TASK-002 готова · 02:00"  # с первого старта


def test_failed_line_has_first_error_line(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    w, out, _ = _watcher(db)
    w.begin(_at(0))
    _task(db, "TASK-003", "running", _ts(0))
    w.tick(_at(1))
    _attempt(db, "TASK-003", _ts(5), False, "лимит исчерпан\nподробности")
    _task(db, "TASK-003", "failed", _ts(0), _ts(5))
    w.tick(_at(6))
    assert out[-1] == "✗ TASK-003 не прошла: лимит исчерпан"


def test_live_line_counts_and_elapsed(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    _task(db, "TASK-001", "success", _ts(0), _ts(10))
    w, _, lives = _watcher(db)
    w.begin(_at(10))
    _task(db, "TASK-002", "running", _ts(20))
    w.tick(_at(25))
    assert lives[-1] == "  TASK-002 · 2 из 3 · 00:05"


def test_unavailable_once_after_30s(tmp_path: Path) -> None:
    w, out, _ = _watcher(tmp_path / "нет.db")
    w.begin(_at(0))
    w.tick(_at(29))
    assert out == []
    w.tick(_at(31))
    w.tick(_at(40))
    assert out == [rp.UNAVAILABLE.format(path=tmp_path / "нет.db")]


def test_tick_swallows_errors(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")

    def boom(_line: str) -> None:
        raise RuntimeError("экран закрыт")

    w = rp.Watcher(db, rp.read_tasks(TASKS), emit=boom, live=boom)
    w.begin(_at(0))
    _task(db, "TASK-001", "running", _ts(0))
    w.tick(_at(1))  # не бросает


def test_event_line_strips_terminal_controls() -> None:
    """Ошибка из вывода агента не управляет терминалом оператора."""
    line = rp.event_line(
        "failed", "TASK-1\x1b]0;x\x07", "t", error="\x1b[2Jстерто\x9b31m" + "я" * 500
    )
    assert "\x1b" not in line and "\x07" not in line and "\x9b" not in line
    assert line.startswith("✗ TASK-1]0;x не прошла: [2Jстерто31m")
    assert len(line) < 260
    title = rp.event_line("started", "TASK-2", "a\x1b[1mb")
    assert title == "▶ TASK-2 «a[1mb» — начата"


def _drain(master: int) -> bytes:
    """Всё, что pty успел отдать: на Linux записи читаются кусками."""
    import select

    chunks = []
    os.set_blocking(master, False)
    while select.select([master], [], [], 0.2)[0]:
        try:
            data = os.read(master, 65536)
        except (BlockingIOError, OSError):
            break
        if not data:
            break
        chunks.append(data)
    return b"".join(chunks)


# --- Task 3: экран и обёртка вызова раннера ---------------------------------


def _emit_list():
    out: list[str] = []
    return out, out.append


def _running_then_done(db: Path, code: int = 0):
    def call() -> int:
        _db(db)
        _task(db, "TASK-001", "running", datetime.now().isoformat())
        time.sleep(0.15)
        _task(db, "TASK-001", "success", datetime.now().isoformat(),
              datetime.now().isoformat())  # fmt: skip
        time.sleep(0.15)
        return code

    return call


def _tasks_file(target: Path) -> str:
    rel = "spec/WS-1-tasks.md"
    (target / "spec").mkdir(parents=True, exist_ok=True)
    (target / rel).write_text("### TASK-001: Команда add\nP1 | TODO\n", "utf-8")
    return rel


def test_screen_fd_env_matches_runlog() -> None:
    from governance import auto_tasks
    from lite import runlog

    assert rp.SCREEN_FD_ENV == auto_tasks.SCREEN_FD_ENV == runlog.SCREEN_FD_ENV


def test_watched_returns_runner_code_and_stops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(rp.SCREEN_FD_ENV, raising=False)
    rel = _tasks_file(tmp_path)
    db = tmp_path / "spec" / ".executor-WS-1-state.db"
    out, emit = _emit_list()
    code = rp.watched(_running_then_done(db, 3), target_dir=str(tmp_path),
                      prefix="WS-1-", tasks_rel=rel, emit=emit, period=0.05)  # fmt: skip
    assert code == 3
    assert not [t for t in threading.enumerate() if t.name == rp.THREAD_NAME]
    assert "▶ TASK-001 «Команда add» — начата" in out
    assert out[-2].startswith("✓ TASK-001 готова")
    assert out[-1].startswith("реализация: сделано 1 из 1 · ")


def test_watched_propagates_exception_and_stops(tmp_path: Path) -> None:
    def call() -> int:
        raise RuntimeError("раннер упал")

    _, emit = _emit_list()
    with pytest.raises(RuntimeError, match="раннер упал"):
        rp.watched(call, target_dir=str(tmp_path), prefix="WS-1-",
                   tasks_rel="spec/x.md", emit=emit, period=0.05)  # fmt: skip
    assert not [t for t in threading.enumerate() if t.name == rp.THREAD_NAME]


def test_no_tty_no_live_line(tmp_path: Path, monkeypatch) -> None:
    r, w = os.pipe()
    monkeypatch.setenv(rp.SCREEN_FD_ENV, str(w))
    rel = _tasks_file(tmp_path)
    db = tmp_path / "spec" / ".executor-WS-1-state.db"
    _, emit = _emit_list()
    rp.watched(_running_then_done(db), target_dir=str(tmp_path), prefix="WS-1-",
               tasks_rel=rel, emit=emit, period=0.05)  # fmt: skip
    os.close(w)
    assert b"\r" not in os.read(r, 65536)
    os.close(r)


def test_no_screen_fd_no_live_line(tmp_path: Path, monkeypatch, capfd) -> None:
    """Без перехвата (`-vvv`, прямой вызов) живой строки нет."""
    monkeypatch.delenv(rp.SCREEN_FD_ENV, raising=False)
    rel = _tasks_file(tmp_path)
    db = tmp_path / "spec" / ".executor-WS-1-state.db"
    _, emit = _emit_list()
    rp.watched(_running_then_done(db), target_dir=str(tmp_path), prefix="WS-1-",
               tasks_rel=rel, emit=emit, period=0.05)  # fmt: skip
    assert "\r" not in capfd.readouterr().out


def test_live_line_on_tty(tmp_path: Path, monkeypatch) -> None:
    master, slave = os.openpty()
    monkeypatch.setenv(rp.SCREEN_FD_ENV, str(slave))
    rel = _tasks_file(tmp_path)
    db = tmp_path / "spec" / ".executor-WS-1-state.db"
    _, emit = _emit_list()
    rp.watched(_running_then_done(db), target_dir=str(tmp_path), prefix="WS-1-",
               tasks_rel=rel, emit=emit, period=0.05)  # fmt: skip
    got = _drain(master)
    os.close(slave)
    os.close(master)
    assert b"\r  TASK-001 \xc2\xb7 1 \xd0\xb8\xd0\xb7 1 \xc2\xb7 00:00" in got
    assert got.endswith(b"\r\x1b[K")


def test_summary_total_falls_back_to_db(tmp_path: Path) -> None:
    """tasks.md не прочитался — M из БД, не «1 из 0»."""
    db = _db(tmp_path / "s.db")
    w = rp.Watcher(db, [], emit=lambda _l: None, live=None)
    w.begin(_at(0))
    _task(db, "TASK-001", "success", _ts(0), _ts(5))
    w.tick(_at(6))
    assert w.summary(_at(6)) == "реализация: сделано 1 из 1 · 00:06"


# --- финальное ревью #765 ----------------------------------------------------


def test_live_clear_after_capture_ends(monkeypatch) -> None:
    """Ревью I1: личный путь — перехват кончился, строку всё равно стереть."""
    master, slave = os.openpty()
    live = rp._LiveScreen(fallback_fd=slave)
    monkeypatch.setenv(rp.SCREEN_FD_ENV, str(slave))
    live("  TASK-001 · 00:01")
    monkeypatch.delenv(rp.SCREEN_FD_ENV)
    live.clear()
    got = _drain(master)
    os.close(slave)
    os.close(master)
    assert got.endswith(b"\r\x1b[K")


def test_stale_running_from_killed_run_is_quiet(tmp_path: Path) -> None:
    """Ревью I2: `running` от убитого прошлого запуска — не «не прошла»."""
    db = _db(tmp_path / "s.db")
    _task(db, "TASK-002", "running", _ts(-7200))
    w, out, lives = _watcher(db)
    w.begin(_at(0))
    w.tick(_at(1))
    assert lives == [] or "2:00:0" not in lives[-1]
    _task(db, "TASK-002", "failed", _ts(-7200), _ts(2))  # recover_stale_tasks
    w.tick(_at(3))
    assert out == []
    _task(db, "TASK-002", "running", _ts(4))  # настоящий старт этого запуска
    w.tick(_at(5))
    assert out == ["▶ TASK-002 «Команда find» — начата"]


def test_live_between_tasks_counts_from_last_end(tmp_path: Path) -> None:
    """Ревью I3: между задачами отсчёт от конца предыдущей, без номера."""
    db = _db(tmp_path / "s.db")
    w, _, lives = _watcher(db)
    w.begin(_at(0))
    _task(db, "TASK-001", "success", _ts(1), _ts(10))
    w.tick(_at(22))
    assert lives[-1] == "  … · 00:12"


def test_watched_survives_unreadable_tasks(tmp_path: Path) -> None:
    """Прогресс не валит прогон: битый tasks.md — раннер всё равно идёт."""
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "x.md").write_bytes(b"\xff\xfe\xfa")
    called = []
    code = rp.watched(lambda: called.append(1) or 0, target_dir=str(tmp_path),
                      prefix="WS-1-", tasks_rel="spec/x.md",
                      emit=lambda _l: None, period=0.05)  # fmt: skip
    assert code == 0 and called == [1]
