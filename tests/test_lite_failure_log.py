"""Запись сбоя `dfl` и снимок прогонов (lite/failure_log.py)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance import run_state as rs
from lite import failure_log as fl


class Refused(Exception):
    pass


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


def _run(root: Path, run_id: str, target: Path, mtime: float) -> Path:
    d = root / run_id
    d.mkdir()
    f = d / "run.json"
    f.write_text(json.dumps({"target_dir": str(target)}), encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return d


def _rec(**kw: object) -> fl.FailureRecord:
    base: dict[str, object] = {
        "command": "run",
        "argv": ["run"],
        "exit_code": 2,
        "time": "2026-11-12T00:00:00Z",
        "output_tail": ["a", "b"],
        "run_ids": [],
        "runs_note": "",
        "cwd_repo": "o/r",
        "cause": "c",
    }
    base.update(kw)
    return fl.FailureRecord(**base)  # type: ignore[arg-type]


def test_repo_key_same_for_subdir_of_git_repo(git_repo: Path) -> None:
    key = fl.repo_key(git_repo)
    assert key == fl.repo_key(git_repo / "sub")
    assert len(key) == 16


def test_repo_key_non_git_uses_cwd(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert fl.repo_key(tmp_path / "a") != fl.repo_key(tmp_path / "b")


def test_record_path_respects_absolute_xdg_state_home(tmp_path: Path) -> None:
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    path = fl.record_path(tmp_path, env)
    assert (
        path
        == (tmp_path / "state" / "darkfactory" / "failures" / fl.repo_key(tmp_path))
        / "last.json"
    )
    rel = fl.record_path(tmp_path, {"XDG_STATE_HOME": "rel"})
    assert rel.parts[-6:-3] == (".local", "state", "darkfactory")


def test_write_then_load_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    rec = _rec(run_ids=["r1"], runs_note="вызов его не менял")
    fl.write(tmp_path, rec)
    assert fl.load(tmp_path) == rec


def test_load_missing_or_broken_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert fl.load(tmp_path) is None
    path = fl.record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert fl.load(tmp_path) is None
    path.write_text('{"command": "run"}', encoding="utf-8")
    assert fl.load(tmp_path) is None


def test_changed_runs_detects_new_and_touched(runs_root: Path, tmp_path) -> None:
    _run(runs_root, "r-old", tmp_path, 100)
    _run(runs_root, "r-touched", tmp_path, 100)
    before = fl.snapshot_runs()
    os.utime(runs_root / "r-touched" / "run.json", (200, 200))
    _run(runs_root, "r-new", tmp_path, 300)
    ids, note = fl.changed_runs(before, fl.snapshot_runs(), tmp_path)
    assert (ids, note) == (["r-new", "r-touched"], "")


def test_changed_runs_falls_back_to_latest_of_repo(runs_root: Path, tmp_path) -> None:
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    _run(runs_root, "r-a", mine, 100)
    _run(runs_root, "r-b", mine, 200)
    _run(runs_root, "r-c", other, 300)
    snap = fl.snapshot_runs()
    assert fl.changed_runs(snap, snap, mine) == (["r-b"], "вызов его не менял")
    assert fl.changed_runs(snap, snap, tmp_path / "none") == ([], "")


def test_cause_prefers_refusal_then_stop_reason_then_exception_then_tail(
    runs_root: Path, tmp_path
) -> None:
    exc = Refused("нет 'a — b' — сделайте")
    assert fl.cause_from(exc, [], [], refusals=(Refused,)) == "нет 'a — b'"
    d = _run(runs_root, "r1", tmp_path, 1)
    (d / "stop-reason.txt").write_text("стоп раннера\nподробно", encoding="utf-8")
    assert fl.cause_from(None, ["r1"], ["x"]) == "стоп раннера"
    boom = RuntimeError("boom\nx")
    assert fl.cause_from(boom, [], ["x"]) == "RuntimeError: boom"
    assert fl.cause_from(None, [], ["", "last line", ""]) == "last line"
    assert fl.cause_from(None, [], []) == "причина не записана"


def test_cause_from_printed_refusal_block(runs_root: Path) -> None:
    tail = [
        "x",
        "dfl init-repo: отказ (код 2)",
        "  причина:     нет origin",
        "  что сделать: y",
    ]
    assert fl.cause_from(None, [], tail) == "нет origin"


def test_snapshot_runs_survives_bad_runs_root(tmp_path: Path, monkeypatch) -> None:
    def bad() -> list[str]:
        raise ValueError("битое имя")

    monkeypatch.setattr(rs, "all_run_ids", bad)
    assert fl.snapshot_runs() == {}


def test_old_record_without_log_fields_loads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    old = {
        "command": "run",
        "argv": ["run"],
        "exit_code": 2,
        "time": "2026-11-12T00:00:00Z",
        "output_tail": [],
        "run_ids": [],
        "runs_note": "",
        "cwd_repo": "",
        "cause": "x",
    }
    path = fl.record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old), encoding="utf-8")
    rec = fl.load(tmp_path)
    assert rec is not None
    assert (rec.log_path, rec.verbosity) == ("", 0)
