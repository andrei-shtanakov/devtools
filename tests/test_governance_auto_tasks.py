"""auto_tasks: цепочка tasks-PR → spec approve → approve-PR (план 3c-3, T2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import auto_tasks
from governance import run_state as rs
from governance.auto_tasks import TASKS_SKIP_TEST, run_chain

T, A = 11, 12
HEAD = {T: "t" * 40, A: "a" * 40}
GREEN = [{"name": "ci", "conclusion": "SUCCESS"}]


def _facts(pr: int, *, state="OPEN", draft=False, rollup=None) -> dict:
    return {
        "state": state,
        "isDraft": draft,
        "headRefOid": HEAD[pr],
        "statusCheckRollup": GREEN if rollup is None else rollup,
    }


class _ChainOps:
    def __init__(self, facts: dict[int, dict]) -> None:
        self.facts = facts
        self.calls: list[tuple] = []
        self.merge_rc = 0
        self.merge_exc: Exception | None = None
        self.spec_rc = 0
        self.ready_ok = True

    def pr_facts(self, slug: str, pr: int) -> dict:
        return self.facts[pr]

    def mark_ready(self, slug: str, pr: int) -> bool:
        self.calls.append(("mark_ready", slug, pr))
        return self.ready_ok

    def human_merge(self, repo, pr, sha, *, target_dir=None) -> int:
        self.calls.append(("human_merge", repo, pr, sha, target_dir))
        if self.merge_exc is not None:
            raise self.merge_exc
        return self.merge_rc

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        self.calls.append(("pull", target_dir, branch))

    def spec_runner(self, target_dir: str, args: list[str]) -> int:
        self.calls.append(("spec_runner", target_dir, args))
        return self.spec_rc

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    st = rs.new_run(
        mode="test",
        subject="s",
        repo="alpha",
        repo_slug="o/alpha",
        ws_id="WS-1",
        target_dir=str(tmp_path),
        bundle_dir="workstreams/WS-1/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-auto",
    )
    st.status = "completed"
    st.base_ref = "main"
    rs.save(st)
    return st


@pytest.fixture
def bridge(monkeypatch):
    """check_approved проходит; deliver_approve отдаёт A."""
    approved = {"ok": False}

    def check(target_dir, ws_id, bundle_dir):
        if not approved["ok"]:
            raise RuntimeError("не утверждено")

    monkeypatch.setattr(auto_tasks.tb, "check_approved", check)
    monkeypatch.setattr(auto_tasks.tb, "deliver_approve", lambda st, ops: A)
    return approved


def _ops(**kw) -> _ChainOps:
    return _ChainOps({T: _facts(T, draft=True), A: _facts(A), **kw})


def test_tasks_pr_ready_checks_merge_then_pull(state, bridge):
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    assert ops.calls[:3] == [
        ("mark_ready", "o/alpha", T),
        ("human_merge", "alpha", T, HEAD[T], state.target_dir),
        ("pull", state.target_dir, "main"),
    ]
    assert state.ops["tasks-merge"] == {
        "status": "completed",
        "pr": T,
        "head": HEAD[T],
        "tasks_skip": TASKS_SKIP_TEST,
    }


def test_tasks_skip_written_before_merge(state, bridge):
    ops = _ops()
    ops.merge_exc = OSError("boom")
    with pytest.raises(OSError):
        run_chain(state, ops, T)
    op = state.ops["tasks-merge"]
    assert op["status"] == "started"
    assert op["tasks_skip"] == TASKS_SKIP_TEST


def test_red_checks_stop_without_merge(state, bridge, capsys):
    red = [{"name": "lint", "conclusion": "FAILURE"}]
    ops = _ops()
    ops.facts[T] = _facts(T, draft=True, rollup=red)
    assert run_chain(state, ops, T) == 1
    assert "human_merge" not in ops.names()
    assert "lint" in capsys.readouterr().out


def test_already_merged_tasks_pr_is_not_merged_again(state, bridge):
    ops = _ops()
    ops.facts[T] = _facts(T, state="MERGED")
    assert run_chain(state, ops, T) == 0
    assert ("mark_ready", "o/alpha", T) not in ops.calls
    assert all(c[:3] != ("human_merge", "alpha", T) for c in ops.calls)
    assert state.ops["tasks-merge"]["status"] == "completed"


def test_closed_pr_stops(state, bridge, capsys):
    ops = _ops()
    ops.facts[T] = _facts(T, state="CLOSED")
    assert run_chain(state, ops, T) == 1
    assert "закрыт без мержа" in capsys.readouterr().out
    assert ops.calls == []


def test_spec_approve_runs_only_when_not_approved(state, bridge):
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    expected = [
        "spec",
        "approve",
        "tasks",
        "--profile",
        state.profile,
        "--spec-prefix",
        "WS-1-",
    ]
    calls = [c for c in ops.calls if c[0] == "spec_runner"]
    assert len(calls) == 1
    assert calls[0][1] == state.target_dir
    assert calls[0][2][:3] == expected[:3]
    assert calls[0][2][-2:] == expected[-2:]
    assert state.ops["tasks-approve"]["pr"] == A


def test_spec_approve_skipped_when_already_approved(state, bridge):
    bridge["ok"] = True
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    assert "spec_runner" not in ops.names()


def test_approve_pr_merged_with_same_gate(state, bridge):
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    assert ("human_merge", "alpha", A, HEAD[A], state.target_dir) in ops.calls
    assert state.ops["approve-merge"]["tasks_skip"] == TASKS_SKIP_TEST
    assert state.ops["approve-merge"]["pr"] == A


def test_resume_after_crash_between_merge_and_approve(state, bridge):
    rs.op_start(state, "tasks-merge", pr=T, head=HEAD[T], tasks_skip=TASKS_SKIP_TEST)
    rs.op_complete(state, "tasks-merge", pr=T, head=HEAD[T], tasks_skip="x")
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    assert ("mark_ready", "o/alpha", T) not in ops.calls
    assert all(c[:3] != ("human_merge", "alpha", T) for c in ops.calls)
    assert "spec_runner" in ops.names()


def test_spec_approve_nonzero_stops(state, bridge, capsys):
    ops = _ops()
    ops.spec_rc = 3
    assert run_chain(state, ops, T) == 1
    out = capsys.readouterr().out
    assert "spec approve" in out and "3" in out
    assert state.ops["tasks-approve"]["status"] == "started"


def test_merge_nonzero_rc_stops(state, bridge, capsys):
    ops = _ops()
    ops.merge_rc = 4
    assert run_chain(state, ops, T) == 1
    assert "4" in capsys.readouterr().out
    assert state.ops["tasks-merge"]["status"] == "started"
