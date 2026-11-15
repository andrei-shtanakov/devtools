"""Пауза на просмотр этапа в enterprise-волне (#768, Task 5).

Раннер только останавливается (`WAITING_OWNER_REVIEW`) после прошедшего
edge-check волны, до candidate-PR, и продолжает по op `review-pause-w<k>`,
который пишет `dfl`: без правок — без повторной проверки (RF-3), с правками —
сброс `commit/gate-candidate/edge` волны и пауза снова после перепроверки.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import run_lock as rl
from governance import run_state as rs
from governance import runner, spec_loop
from tests import locked_runner
from tests.test_governance_runner import (
    _OVR_KEYS,
    FakeOps,
    _fail_records,
    _fake_wave_adapters,
    _override_journal,
    _set_cfg_mode,
    _test_mode_ops,
    _waves_kwargs,
)


@pytest.fixture(autouse=True)
def _hermetic_claude_home(tmp_path_factory, monkeypatch) -> None:
    """Трипвайр конфиг-поверхности не читает настоящий ~/.claude оператора."""
    home = tmp_path_factory.mktemp("claude-home")
    monkeypatch.setattr(runner, "_claude_home", lambda: home)


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


def _ops() -> FakeOps:
    return FakeOps(facts={"state": "OPEN", "baseRefName": "main"})


def _names(ops: FakeOps) -> list[str]:
    return [c[0] for c in ops.calls]


def _paused(tmp_path: Path, monkeypatch, run_id: str, ops: FakeOps | None = None):
    """Волна 1 с паузой на charter: edge-check PASS → стоп до candidate."""
    ops = ops or _ops()
    _fake_wave_adapters(monkeypatch, ops)
    kwargs = _waves_kwargs(tmp_path, run_id, ops, review_pause=("charter",))
    return locked_runner.start(**kwargs), ops


def _pass_review(run_id: str, *, revised: bool) -> None:
    """Запись `dfl` (Task 6): пауза пройдена, были ли правки."""
    state = rs.load(run_id)
    state.ops[runner.wave_key(state, "review-pause")] = {
        "status": "completed",
        "revised": revised,
    }
    rs.save(state)


def test_no_pause_without_review_list(tmp_path: Path, runs_root, monkeypatch) -> None:
    ops = _ops()
    _fake_wave_adapters(monkeypatch, ops)
    state = locked_runner.start(**_waves_kwargs(tmp_path, "r-rp-none", ops))
    assert state.review_pause == []
    assert state.status == "waiting_human_merge"
    assert "propose_from_source" in _names(ops)
    assert "review-pause-1" not in state.ops


def test_pass_with_pause_stops_before_candidate(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    state, ops = _paused(tmp_path, monkeypatch, "r-rp-stop")
    assert runner.WAITING_OWNER_REVIEW == "waiting_owner_review"
    assert state.status == runner.WAITING_OWNER_REVIEW
    assert state.ops["edge-1"]["status"] == "completed"
    assert "propose_from_source" not in _names(ops)
    assert "candidate-1" not in state.ops
    assert rs.load("r-rp-stop").status == runner.WAITING_OWNER_REVIEW


def test_pause_on_other_stage_does_not_stop_this_wave(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    ops = _ops()
    _fake_wave_adapters(monkeypatch, ops)
    kwargs = _waves_kwargs(tmp_path, "r-rp-other", ops, review_pause=("design",))
    state = locked_runner.start(**kwargs)
    assert state.status == "waiting_human_merge"
    assert "propose_from_source" in _names(ops)


def test_override_also_pauses(tmp_path: Path, runs_root, monkeypatch) -> None:
    _set_cfg_mode("test")
    ops = _test_mode_ops(edge_results={"charter": "FAIL"})
    _fake_wave_adapters(monkeypatch, ops)
    _fail_records(monkeypatch)
    kwargs = _waves_kwargs(tmp_path, "r-rp-ovr", ops, review_pause=("charter",))
    assert locked_runner.start(**kwargs).status == "stopped_review"
    _override_journal("r-rp-ovr", _OVR_KEYS)
    state = locked_runner.resume("r-rp-ovr", ops)
    assert state.ops["edge-1"]["verdict"] == "OVERRIDE"
    assert state.status == runner.WAITING_OWNER_REVIEW
    assert "propose_from_source" not in _names(ops)


def test_resume_refused_while_pause_not_passed(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    _, ops = _paused(tmp_path, monkeypatch, "r-rp-refuse")
    with pytest.raises(ValueError, match="пауза просмотра не пройдена — dfl run"):
        locked_runner.resume("r-rp-refuse", ops)
    assert rs.load("r-rp-refuse").status == runner.WAITING_OWNER_REVIEW


def test_resume_done_without_remarks_skips_recheck(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    """RF-3: «дальше» без замечаний — candidate без повторной проверки."""
    _, ops = _paused(tmp_path, monkeypatch, "r-rp-done")
    _pass_review("r-rp-done", revised=False)
    before = _names(ops)
    state = locked_runner.resume("r-rp-done", ops)
    after = _names(ops)[len(before) :]
    assert state.status == "waiting_human_merge"
    assert "edge_check_level" not in after
    assert "propose_from_source" in after
    assert _names(ops).count("edge_check_level") == 1
    assert state.ops["review-pause-1"]["status"] == "completed"


def test_resume_revised_resets_and_pauses_again(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    """RF-3: правки — commit/gate/edge волны заново, пауза после перепроверки."""
    _, ops = _paused(tmp_path, monkeypatch, "r-rp-rev")
    _pass_review("r-rp-rev", revised=True)
    before = _names(ops)
    state = locked_runner.resume("r-rp-rev", ops)
    after = _names(ops)[len(before) :]
    assert "edge_check_level" in after
    assert "propose_from_source" not in after
    assert state.status == runner.WAITING_OWNER_REVIEW
    assert "review-pause-1" not in state.ops
    assert state.ops["edge-1"]["status"] == "completed"
    assert state.ops["commit-1"]["status"] == "completed"


@pytest.mark.parametrize("stage", ["design", "acceptance"])
def test_w4_pause_when_either_node_selected(
    tmp_path: Path, runs_root, monkeypatch, stage: str
) -> None:
    state, ops = _paused(tmp_path, monkeypatch, f"r-rp-w4-{stage}")
    state.status = "running"
    state.wave = 4
    state.review_pause = [stage]
    state.ops["edge-4"] = {"status": "completed", "verdict": "PASS"}
    assert runner._step_edge(state, ops) is False
    assert state.status == runner.WAITING_OWNER_REVIEW


def test_w4_no_pause_for_other_wave_stage(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    state, ops = _paused(tmp_path, monkeypatch, "r-rp-w4-none")
    state.status = "running"
    state.wave = 4
    state.review_pause = ["charter", "decomposition"]
    state.ops["edge-4"] = {"status": "completed", "verdict": "PASS"}
    assert runner._step_edge(state, ops) is True
    assert state.status == "running"


def test_spec_loop_does_not_autoresume_waiting_owner_review(
    tmp_path: Path, runs_root, monkeypatch, capsys
) -> None:
    _set_cfg_mode("test")
    state, ops = _paused(tmp_path, monkeypatch, "r-rp-loop", _test_mode_ops())
    resumed: list[str] = []
    monkeypatch.setattr(
        spec_loop.runner, "resume", lambda run_id, *a, **k: resumed.append(run_id)
    )
    with rl.run_lock(state.run_id) as lock:
        code = spec_loop._dispatch(state, ops, lock)
    assert code == 0 and resumed == []
    assert "ждёт просмотра этапа: dfl run" in capsys.readouterr().out


def test_spec_loop_resumes_after_pause_passed(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    _set_cfg_mode("test")
    state, ops = _paused(tmp_path, monkeypatch, "r-rp-loop2", _test_mode_ops())
    _pass_review("r-rp-loop2", revised=False)
    resumed: list[str] = []

    def _resume(run_id, *_a, **_k):
        resumed.append(run_id)
        return rs.load(run_id)

    monkeypatch.setattr(spec_loop.runner, "resume", _resume)
    with rl.run_lock(state.run_id) as lock:
        spec_loop._dispatch(rs.load("r-rp-loop2"), ops, lock)
    assert resumed == ["r-rp-loop2"]


def test_spec_loop_report_state_names_review_pause(
    tmp_path: Path, runs_root, monkeypatch, capsys
) -> None:
    """Пауза, встреченная после start/resume, — подсказка `dfl run`, код 0."""
    state, _ = _paused(tmp_path, monkeypatch, "r-rp-report")
    assert spec_loop._report_state(state) == 0
    out = capsys.readouterr().out
    assert "ждёт просмотра этапа: dfl run" in out and "без скрытых" not in out


def test_dfl_outcome_line_is_plain_russian(
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    from lite import dfl

    state, _ = _paused(tmp_path, monkeypatch, "r-rp-dfl")
    line = dfl._outcome(state, 0)
    assert line == "прогон r-rp-dfl ждёт просмотра этапа: dfl run"


def test_later_stop_reset_pauses_again(tmp_path: Path, runs_root, monkeypatch) -> None:
    """Пауза пройдена без правок, затем стоп волны до candidate: сброс
    commit/gate/edge снимает и паузу — правка на стопе снова покажется."""
    _, ops = _paused(tmp_path, monkeypatch, "r-rp-late")
    _pass_review("r-rp-late", revised=False)
    state = rs.load("r-rp-late")
    state.status = "stopped_review"
    rs.save(state)
    assert "review-pause-1" in runner.reset_ops_for(state)
    before = _names(ops)
    state = locked_runner.resume("r-rp-late", ops)
    after = _names(ops)[len(before) :]
    assert "edge_check_level" in after and "propose_from_source" not in after
    assert state.status == runner.WAITING_OWNER_REVIEW
    assert "review-pause-1" not in state.ops


def test_reopen_resets_owner_remarks(tmp_path: Path, runs_root, monkeypatch) -> None:
    """Final review #768 minor 3: `--reopen <node>` сбрасывает журнал замечаний
    переоткрытых этапов — старые применённые не попадают в новый candidate-PR,
    недоигранные не переигрываются над новым документом; ниже — не тронуты."""
    from governance import approval_ledger as al
    from governance import review_remarks as rr
    from tests.test_governance_runner import _all_approved, _fake_approve_node_full

    ops = FakeOps(facts={"state": "OPEN", "baseRefName": "master"})
    _fake_wave_adapters(monkeypatch, ops)
    _fake_approve_node_full(monkeypatch, ops)
    state = locked_runner.start(**_waves_kwargs(tmp_path, "r-rm", ops))
    al.abandon_request(state, state.ops["candidate-1"]["request"], "стенд")
    state.wave = 5
    rs.save(state)
    ops.base_files = _all_approved()
    rd = rs.run_dir("r-rm")
    rr.record(rd, "charter", "к уставу")
    rr.mark_applied(rd, "charter", 1)
    rr.record(rd, "requirements", "старое")
    rr.mark_applied(rd, "requirements", 1)
    rr.record(rd, "design", "недоигранное")
    locked_runner.reopen("r-rm", "requirements", ops, manual=True)
    assert rr.pr_section(rd, ["requirements"]) == ""
    assert rr.pending(rd, "design") == []
    assert "- устав: к уставу" in rr.pr_section(rd, ["charter"])
    rr.record(rd, "requirements", "новое")
    assert [r.text for r in rr.pending(rd, "requirements")] == ["новое"]
    rr.mark_applied(rd, "requirements", 1)
    assert "- требования: новое" in rr.pr_section(rd, ["requirements"])
    assert "старое" not in rr.pr_section(rd)
