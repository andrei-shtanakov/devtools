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


def test_roundtrip_brief_descriptor_without_machine_paths(runs_root) -> None:
    descriptor = {
        "frame": "customer",
        "primary": "00-discovery/brief.md",
        "requirements_source": "00-discovery/brief.md",
        "source_paths": ["00-discovery/brief.md"],
        "source_blobs": {"discovery-brief": "a" * 40},
    }
    state = rs.new_run(
        subject="brief input", repo="alpha", repo_slug="owner/alpha",
        ws_id="WS-BRIEF", target_dir="/tmp/alpha",
        bundle_dir="workstreams/WS-BRIEF/spec", profile="profiles/team-exp.yaml",
        run_id="r-brief", brief=descriptor,
    )
    rs.save(state)

    assert rs.load("r-brief").brief == descriptor
    raw = (rs.run_dir("r-brief") / "run.json").read_text(encoding="utf-8")
    assert "/tmp/source-machine" not in raw


def test_old_ledger_without_brief_field_loads(runs_root) -> None:
    state = _mk(runs_root)
    path = rs.run_dir(state.run_id) / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("brief")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert rs.load(state.run_id).brief is None


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


# --- Круг 12: run_id — одно-компонентная валидация, без traversal ----------


@pytest.mark.parametrize(
    "bad_run_id", ["../../x", "/abs", "a/b", "..", "", ".hidden"],
)
def test_run_dir_rejects_path_traversal_and_absolute(
    runs_root, bad_run_id: str,
) -> None:
    """Круг 12 (codex-major): без валидации `--run-id ../../outside` или
    абсолютный путь писал `run.json` ВНЕ `RUNS_ROOT`."""
    with pytest.raises(ValueError):
        rs.run_dir(bad_run_id)


def test_run_dir_accepts_normal_run_id(runs_root) -> None:
    assert rs.run_dir("WS-1-abc123") == runs_root / "WS-1-abc123"


def test_validate_id_component_accepts_and_rejects(runs_root) -> None:
    rs.validate_id_component("WS-1-abc123")  # не поднимает
    with pytest.raises(ValueError, match="ws_id"):
        rs.validate_id_component("../escape", label="ws_id")


def test_new_run_carries_interview_state_and_old_ledgers_load(runs_root) -> None:
    st = rs.new_run(
        subject="s", repo="alpha", repo_slug="o/alpha", ws_id="WS-1",
        target_dir="/tmp/x", bundle_dir="spec",
        profile="profiles/team-exp.yaml", run_id="r-int",
        interview={"session_id": None, "frame": "customer"},
    )
    rs.save(st)
    assert rs.load("r-int").interview == {"session_id": None, "frame": "customer"}
    # старый run.json без поля читается через default
    raw = (rs.run_dir("r-int") / "run.json").read_text(encoding="utf-8")
    data = json.loads(raw); del data["interview"]
    (rs.run_dir("r-int") / "run.json").write_text(json.dumps(data), encoding="utf-8")
    assert rs.load("r-int").interview is None


def test_run_state_defaults_to_legacy_authoring(tmp_path: Path, runs_root) -> None:
    state = rs.new_run(
        subject="s", repo="r", repo_slug="o/r", ws_id="WS",
        target_dir=str(tmp_path), bundle_dir="spec",
        profile="profiles/team-exp.yaml", run_id="r-1",
    )
    assert state.authoring == "legacy" and state.wave == 0  # 0 — не волны
    rs.save(state)
    assert rs.load("r-1").authoring == "legacy"


def test_waves_authoring_is_persisted(tmp_path: Path, runs_root) -> None:
    state = rs.new_run(
        subject="s", repo="r", repo_slug="o/r", ws_id="WS",
        target_dir=str(tmp_path), bundle_dir="spec",
        profile="profiles/team-exp.yaml", run_id="r-2", authoring="waves",
    )
    rs.save(state)
    assert rs.load("r-2").authoring == "waves"
    assert rs.load("r-2").wave == 1, "волны 1-based: прогон начинается с W1"


def test_unknown_authoring_is_refused() -> None:
    with pytest.raises(ValueError, match="authoring"):
        rs.validate_authoring("chunks")
