"""Read-only view-model для behaviour console (спека Task 3): списки
прогонов, детальная карточка, срез бандла — без ops/subprocess."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import console_model as cm
from governance import run_state as rs


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path)
    return tmp_path


def _mk(run_id: str, **overrides) -> rs.RunState:
    kwargs = dict(
        subject="тест", repo="alpha", repo_slug="owner/alpha", ws_id="WS-T1",
        target_dir="/tmp/alpha", bundle_dir="workstreams/WS-T1/spec",
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    kwargs.update(overrides)
    s = rs.new_run(**kwargs)
    rs.save(s)
    return s


# --- list_runs ---------------------------------------------------------


def test_list_runs_two_live_one_corrupt(runs_root) -> None:
    s1 = _mk("r-0001")
    rs.op_start(s1, "branch")
    rs.op_complete(s1, "branch", branch="feat/x")

    s2 = _mk("r-0002", ws_id="WS-T2")
    s2.pr = 42
    s2.remediated_by = "r-0001"
    rs.save(s2)

    (runs_root / "r-broken").mkdir()
    (runs_root / "r-broken" / "run.json").write_text("{not json")

    rows = cm.list_runs()
    by_id = {r.run_id: r for r in rows}
    assert set(by_id) == {"r-0001", "r-0002", "r-broken"}

    assert by_id["r-broken"].status == "corrupt"
    assert by_id["r-broken"].pr is None
    assert by_id["r-broken"].remediated_by is None

    assert by_id["r-0001"].status == "running"
    assert by_id["r-0001"].ws_id == "WS-T1"
    assert by_id["r-0001"].repo == "alpha"
    assert by_id["r-0001"].step == "author-charter"  # branch completed

    assert by_id["r-0002"].pr == 42
    assert by_id["r-0002"].remediated_by == "r-0001"
    assert by_id["r-0002"].step == "branch"  # ничего не начато


def test_list_runs_empty_when_no_runs_root(runs_root) -> None:
    assert cm.list_runs() == ()


# --- run_detail ----------------------------------------------------------


def test_run_detail_with_findings_and_verdict(runs_root) -> None:
    s = _mk("r-0003")
    rs.op_start(s, "branch")
    rs.op_complete(s, "branch", branch="feat/y")
    rs.op_start(s, "verdict")
    rs.op_complete(s, "verdict", decision="agent", reason="all green")
    (rs.run_dir("r-0003") / "gate-findings.txt").write_text("error GC-1: bad\n")
    (rs.run_dir("r-0003") / "s8-findings.txt").write_text("error GC-2: also bad\n")

    detail = cm.run_detail("r-0003")
    assert detail.row.run_id == "r-0003"
    ops_dict = dict(detail.ops)
    assert ops_dict["branch"] == "completed"
    assert ops_dict["verdict"] == "completed"
    assert ops_dict["author-charter"] == "new"
    # порядок пайплайна сохранён
    keys = [k for k, _ in detail.ops]
    assert keys == [
        "branch", "author-charter", "author-requirements", "author-behaviour",
        "commit", "gate-candidate", "push", "pr", "ready", "review",
        "verdict", "merge", "sync-default", "gate-authoritative",
        "remediation-issue",
    ]
    assert "GC-1: bad" in detail.findings
    assert "GC-2: also bad" in detail.findings
    assert detail.verdict_reason == "all green"


def test_run_detail_without_findings_or_verdict(runs_root) -> None:
    _mk("r-0004")
    detail = cm.run_detail("r-0004")
    assert detail.findings == ""
    assert detail.verdict_reason is None
    assert dict(detail.ops)["branch"] == "new"


# --- step computation ------------------------------------------------------


def test_step_all_completed_is_dash(runs_root) -> None:
    s = _mk("r-0005")
    for key in cm.PIPELINE_KEYS:
        rs.op_start(s, key)
        rs.op_complete(s, key)
    rows = cm.list_runs()
    row = next(r for r in rows if r.run_id == "r-0005")
    assert row.step == "—"


def test_step_first_incomplete_key(runs_root) -> None:
    s = _mk("r-0006")
    rs.op_start(s, "branch")
    rs.op_complete(s, "branch")
    rs.op_start(s, "author-charter")
    rs.op_complete(s, "author-charter")
    rs.op_start(s, "author-requirements")  # started, не completed
    rows = cm.list_runs()
    row = next(r for r in rows if r.run_id == "r-0006")
    assert row.step == "author-requirements"


# --- bundle_summary ----------------------------------------------------


def test_bundle_summary_on_fixtures(tmp_path: Path) -> None:
    pytest.importorskip("steward")
    from tests.governance_fixtures.bundles import make_bundle, make_profile

    make_profile(tmp_path)
    make_bundle(tmp_path, behaviour_ok=True)

    result = cm.bundle_summary(str(tmp_path), "profiles/mini.yaml", "spec")
    by_id = dict(result)
    assert by_id["behaviour-spec"] == "candidate_valid"
    assert by_id["requirements"] == "candidate_valid"
    assert by_id["tasks"] == "delegated"


def test_bundle_summary_nonexistent_path_is_error_not_exception(
    tmp_path: Path,
) -> None:
    result = cm.bundle_summary(
        str(tmp_path / "nope"), "profiles/missing.yaml", "spec"
    )
    assert len(result) == 1
    node_id, status = result[0]
    assert node_id == "error"
    assert status  # непустое сообщение


# --- JSON serialization -----------------------------------------------


def test_rows_to_json_roundtrips(runs_root) -> None:
    _mk("r-0007")
    rows = cm.list_runs()
    payload = json.loads(cm.rows_to_json(rows))
    assert isinstance(payload, list)
    assert payload[0]["run_id"] == "r-0007"


def test_detail_to_json_roundtrips(runs_root) -> None:
    _mk("r-0008")
    detail = cm.run_detail("r-0008")
    payload = json.loads(cm.detail_to_json(detail))
    assert payload["row"]["run_id"] == "r-0008"
    assert payload["ops"][0] == ["branch", "new"]
    assert payload["verdict_reason"] is None
