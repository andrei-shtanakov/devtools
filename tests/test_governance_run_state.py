"""Журнал операций runner'а: write-ahead, атомарность, resume (спека §4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import run_state as rs


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path)
    return tmp_path


def _mk(runs_root) -> rs.RunState:
    s = rs.new_run(
        subject="тестовый функционал", repo="alpha", repo_slug="owner/alpha",
        ws_id="WS-T1", target_dir="/tmp/alpha", bundle_dir="workstreams/WS-T1/spec",
        profile="profiles/team-exp.yaml", run_id="r-0001",
    )
    rs.save(s)
    return s


def test_roundtrip(runs_root) -> None:
    s = _mk(runs_root)
    loaded = rs.load("r-0001")
    assert loaded == s
    assert loaded.status == "running" and loaded.ops == {}


def test_write_ahead_persists_started(runs_root) -> None:
    s = _mk(runs_root)
    rs.op_start(s, "branch")
    on_disk = rs.load("r-0001")
    assert rs.op_status(on_disk, "branch") == "started"  # записано ДО эффекта


def test_op_complete_stores_result(runs_root) -> None:
    s = _mk(runs_root)
    rs.op_start(s, "pr")
    rs.op_complete(s, "pr", number=87)
    on_disk = rs.load("r-0001")
    assert on_disk.ops["pr"]["status"] == "completed"
    assert on_disk.ops["pr"]["number"] == 87


def test_atomic_no_partial_file(runs_root) -> None:
    s = _mk(runs_root)
    rs.save(s)
    files = list(rs.run_dir("r-0001").iterdir())
    assert [f.name for f in files] == ["run.json"]
    assert json.loads((rs.run_dir("r-0001") / "run.json").read_text())


def test_run_override_only_tightens(runs_root) -> None:
    with pytest.raises(ValueError):
        rs.new_run(subject="s", repo="a", repo_slug="o/a", ws_id="w",
                   target_dir="/t", bundle_dir="b", profile="p",
                   run_id="r-2", merge_authority="agent")
