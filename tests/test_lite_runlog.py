"""`lite.runlog`: лог вызова dfl, ротация и перехват вывода в файл."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lite.failure_log import repo_key
from lite.runlog import (
    KEEP,
    captured,
    close_log,
    list_logs,
    log_dir,
    open_log,
    prune,
    read_log,
)

CHILD = ["sh", "-c", "echo child-line; echo child-err >&2"]


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("GH_PROMPT_DISABLED", raising=False)
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)


def _bind_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """capfd подменяет sys.stdout файлом мимо fd 1; привязываем потоки к fd."""
    out = open(1, "w", closefd=False, encoding="utf-8")
    err = open(2, "w", closefd=False, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)


def test_log_dir_uses_repo_key(tmp_path: Path) -> None:
    env = {"XDG_STATE_HOME": str(tmp_path / "s")}
    assert log_dir(tmp_path, env) == (
        tmp_path / "s" / "darkfactory" / "logs" / repo_key(tmp_path)
    )


def test_log_dir_relative_xdg_falls_back_to_home(tmp_path: Path) -> None:
    got = log_dir(tmp_path, {"XDG_STATE_HOME": "rel"})
    assert got.is_absolute()
    assert got.parts[-3:] == ("darkfactory", "logs", repo_key(tmp_path))


def test_open_log_creates_private_dir_and_header(tmp_path: Path) -> None:
    log = open_log(tmp_path, "run", "dfl run", protect=None)
    close_log(log, 0)
    assert (log.path.parent.stat().st_mode & 0o777) == 0o700
    assert read_log(log.path).startswith("# dfl run\n")
    assert log.path.name.endswith("-run.log")


def test_captured_level0_hides_python_and_child(tmp_path, capfd, monkeypatch) -> None:
    _bind_streams(monkeypatch)
    log = open_log(tmp_path, "run", "dfl run", protect=None)
    with captured(log, 0):
        print("runner-line")
        sys.stdout.flush()
        subprocess.run(CHILD, check=False)
    print("after")
    sys.stdout.flush()
    close_log(log, 0)
    out, err = capfd.readouterr()
    assert "runner-line" not in out + err
    assert "child-line" not in out + err
    assert "after" in out
    text = read_log(log.path)
    assert "runner-line" in text
    assert "child-line" in text and "child-err" in text
    assert text.rstrip().endswith("# код выхода: 0")


def test_captured_level3_passes_through(tmp_path, capfd) -> None:
    log = open_log(tmp_path, "run", "h", protect=None)
    with captured(log, 3):
        subprocess.run(CHILD, check=False)
    close_log(log, 0)
    out, _ = capfd.readouterr()
    assert "child-line" in out
    assert "child-line" not in read_log(log.path)


def test_captured_none_log_passes_through(capfd) -> None:
    with captured(None, 0):
        subprocess.run(CHILD, check=False)
    assert "child-line" in capfd.readouterr().out


def test_captured_sets_and_restores_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    log = open_log(tmp_path, "run", "h", protect=None)
    with captured(log, 0):
        assert os.environ["GH_PROMPT_DISABLED"] == "1"
        assert os.environ["GIT_TERMINAL_PROMPT"] == "0"
    close_log(log, 0)
    assert "GH_PROMPT_DISABLED" not in os.environ
    assert os.environ["GIT_TERMINAL_PROMPT"] == "1"


def test_captured_restores_on_keyboardinterrupt(tmp_path, capfd, monkeypatch) -> None:
    _bind_streams(monkeypatch)
    log = open_log(tmp_path, "run", "h", protect=None)
    with pytest.raises(KeyboardInterrupt):
        with captured(log, 0):
            raise KeyboardInterrupt
    print("visible")
    sys.stdout.flush()
    assert "visible" in capfd.readouterr().out
    assert "GH_PROMPT_DISABLED" not in os.environ
    close_log(log, 130)


def test_captured_with_closed_screen(tmp_path, capfd, monkeypatch) -> None:
    _bind_streams(monkeypatch)
    log = open_log(tmp_path, "run", "h", protect=None)
    saved = os.dup(1)
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        os.dup2(write_end, 1)
        os.close(write_end)
        with captured(log, 0):
            print("closed-screen-line")
            sys.stdout.flush()
            subprocess.run(["sh", "-c", "echo closed-child"], check=False)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    close_log(log, 0)
    text = read_log(log.path)
    assert "closed-screen-line" in text and "closed-child" in text


def test_prune_keeps_20_and_protected(tmp_path) -> None:
    directory = tmp_path / "logs"
    directory.mkdir()
    names = [f"20260101T0000{i:08d}Z-run.log" for i in range(22)]
    for name in names:
        (directory / name).write_text("x")
    prune(directory, KEEP, directory / names[0])
    left = sorted(p.name for p in directory.iterdir())
    assert left == [names[0], *names[2:]]
    assert len(left) == 21


def test_prune_missing_dir_is_noop(tmp_path) -> None:
    prune(tmp_path / "nope", KEEP, None)


def test_open_log_names_unique_back_to_back(tmp_path) -> None:
    a = open_log(tmp_path, "run", "h", protect=None)
    b = open_log(tmp_path, "run", "h", protect=None)
    assert a.path != b.path
    close_log(a, 0)
    close_log(b, 0)


def test_open_log_prunes_old(tmp_path) -> None:
    for _ in range(KEEP + 3):
        close_log(open_log(tmp_path, "run", "h", protect=None), 0)
    assert len(list(log_dir(tmp_path).iterdir())) == KEEP


def test_list_logs_parses_command_and_code(tmp_path) -> None:
    a = open_log(tmp_path, "init-repo", "h", protect=None)
    close_log(a, 2)
    b = open_log(tmp_path, "run", "h", protect=None)
    infos = list_logs(tmp_path)
    assert [(i.path, i.command, i.code) for i in infos] == [
        (a.path, "init-repo", 2),
        (b.path, "run", None),
    ]
    close_log(b, 0)


def test_list_logs_no_dir(tmp_path) -> None:
    assert list_logs(tmp_path) == []
