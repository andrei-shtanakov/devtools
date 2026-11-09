"""auto_tasks: цепочка tasks-PR → spec approve → approve-PR (план 3c-3, T2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance import auto_tasks, spec_loop
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


def _run_pr(number: int, state: str = "OPEN") -> dict:
    return {
        "number": number,
        "headRefName": f"spec-runner/run-{number}",
        "state": state,
    }


class _ChainOps:
    def __init__(self, facts: dict[int, dict]) -> None:
        self.facts = facts
        self.calls: list[tuple] = []
        self.merge_rc = 0
        self.merge_exc: Exception | None = None
        self.spec_rc = 0
        self.ready_ok = True
        self.prs: list[dict] = []
        self.run_pr: int | None = 9
        self.prs_exc: Exception | None = None

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
        if args[0] == "run" and self.spec_rc == 0 and self.run_pr is not None:
            self.prs.append(_run_pr(self.run_pr))
        return self.spec_rc

    def prs_by_head_prefix(self, slug: str, prefix: str) -> list[dict]:
        self.calls.append(("prs", slug, prefix))
        if self.prs_exc is not None:
            raise self.prs_exc
        return [p for p in self.prs if p["headRefName"].startswith(prefix)]

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
    monkeypatch.setattr(rs, "mode_refusal", lambda _st: None)
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
    return _AcceptOps({T: _facts(T, draft=True), A: _facts(A), **kw})


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
    calls = [c for c in ops.calls if c[0] == "spec_runner" and c[2][0] != "run"]
    assert len(calls) == 1
    assert calls[0][1] == state.target_dir
    assert calls[0][2][:3] == expected[:3]
    assert calls[0][2][-2:] == expected[-2:]
    assert state.ops["tasks-approve"]["pr"] == A


def test_spec_approve_skipped_when_already_approved(state, bridge):
    bridge["ok"] = True
    ops = _ops()
    assert run_chain(state, ops, T) == 0
    assert _run_calls(ops) and len(
        [c for c in ops.calls if c[0] == "spec_runner"]
    ) == len(_run_calls(ops))


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


def test_skip_constant_matches_literal():
    assert TASKS_SKIP_TEST == "утверждение задач человеком не требуется (test)"


def test_merged_after_crash_keeps_skip_and_pulls(state, bridge):
    rs.op_start(state, "tasks-merge", pr=T, head=HEAD[T], tasks_skip=TASKS_SKIP_TEST)
    ops = _ops()
    ops.facts[T] = _facts(T, state="MERGED")
    assert run_chain(state, ops, T) == 0
    assert ("pull", state.target_dir, "main") in ops.calls
    assert ops.calls.index(("pull", state.target_dir, "main")) == 0
    assert state.ops["tasks-merge"] == {
        "status": "completed",
        "pr": T,
        "head": HEAD[T],
        "tasks_skip": TASKS_SKIP_TEST,
    }


def test_pull_error_is_named_stop(state, bridge, capsys):
    ops = _ops()

    def boom(target_dir, branch):
        raise RuntimeError("dirty tree")

    ops.checkout_and_pull = boom
    assert run_chain(state, ops, T) == 1
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):") and "dirty tree" in out
    assert state.ops["tasks-merge"]["status"] == "started"


def test_strict_state_makes_no_calls(state, bridge, capsys):
    state.mode = "strict"
    ops = _ops()
    assert run_chain(state, ops, T) == 1
    assert ops.calls == []
    assert "spec-loop (test):" in capsys.readouterr().out
    assert auto_tasks._merge_spec_pr(state, ops, "tasks-merge", T, "tasks-PR") is False
    assert ops.calls == []


def test_config_mode_mismatch_stops(state, bridge, monkeypatch):
    monkeypatch.setattr(rs, "mode_refusal", lambda _st: "режим конфига strict")
    ops = _ops()
    assert run_chain(state, ops, T) == 1
    assert ops.calls == []


RUN_ARGS = ["run", "--all", "--strict", "--spec-prefix=WS-1-", "--integration-pr"]


def _run_calls(ops: _ChainOps) -> list[tuple]:
    return [c for c in ops.calls if c[0] == "spec_runner" and c[2][0] == "run"]


def test_spec_run_argv_and_new_pr_found(state):
    ops = _ops()
    ops.prs = [_run_pr(5)]
    assert auto_tasks._spec_run(state, ops) == 9
    assert _run_calls(ops) == [("spec_runner", state.target_dir, RUN_ARGS)]
    assert state.ops["spec-run"] == {
        "status": "completed",
        "before": [5],
        "pr": 9,
        "exit": 0,
    }


def test_spec_run_two_new_prs_stop(state, capsys):
    ops = _ops()
    ops.prs = [_run_pr(5)]
    orig = ops.spec_runner

    def two(target_dir, args):
        rc = orig(target_dir, args)
        ops.prs.append(_run_pr(10))
        return rc

    ops.spec_runner = two
    assert auto_tasks._spec_run(state, ops) is None
    out = capsys.readouterr().out
    assert "9" in out and "10" in out
    assert state.ops["spec-run"]["status"] == "started"


def test_spec_run_no_new_pr_stop(state, capsys):
    ops = _ops()
    ops.run_pr = None
    assert auto_tasks._spec_run(state, ops) is None
    assert "PR" in capsys.readouterr().out


def _failing_run(ops: _ChainOps, rc: int, partial: int) -> None:
    """spec-runner финализирует integration-PR и при упавшем прогоне."""
    orig = ops.spec_runner
    ops.run_pr = None

    def run(target_dir, args):
        orig(target_dir, args)
        if args[0] == "run":
            ops.prs.append(_run_pr(partial))
            return rc
        return 0

    ops.spec_runner = run


def test_spec_run_nonzero_exit_stop(state, capsys):
    ops = _ops()
    ops.prs = [_run_pr(5)]
    _failing_run(ops, 2, 7)
    assert auto_tasks._spec_run(state, ops) is None
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):")
    assert "вернул 2" in out and "#7" in out


def test_spec_run_nonzero_exit_keeps_op_started(state):
    ops = _ops()
    ops.prs = [_run_pr(5)]
    _failing_run(ops, 2, 7)
    auto_tasks._spec_run(state, ops)
    assert rs.load(state.run_id).ops["spec-run"] == {
        "status": "started",
        "before": [5],
        "exit": 2,
        "failed_prs": [7],
    }


def test_spec_run_resume_reruns_and_ignores_old_pr(state):
    rs.op_start(state, "spec-run", before=[5], exit=2, failed_prs=[7])
    ops = _ops()
    ops.prs = [_run_pr(5), _run_pr(7)]
    assert auto_tasks._spec_run(state, ops) == 9
    assert _run_calls(ops) == [("spec_runner", state.target_dir, RUN_ARGS)]
    assert state.ops["spec-run"] == {
        "status": "completed",
        "before": [5, 7],
        "pr": 9,
        "exit": 0,
    }


def test_spec_run_resume_after_crash_does_not_take_pr(state):
    """Падение контроллера: PR без наблюдённого кода 0 не принимается."""
    rs.op_start(state, "spec-run", before=[5])
    ops = _ops()
    ops.prs = [_run_pr(5), _run_pr(7)]
    ops.run_pr = None
    assert auto_tasks._spec_run(state, ops) is None
    assert len(_run_calls(ops)) == 1
    assert state.ops["spec-run"]["status"] == "started"
    assert state.ops["spec-run"]["before"] == [5, 7]


def test_spec_run_failed_resume_never_reaches_code_accept(state, bridge, capsys):
    ops = _accepting_ops()
    _failing_run(ops, 1, 7)
    assert run_chain(state, ops, T) == 1
    assert run_chain(state, ops, T) == 1
    assert "code-accept" not in state.ops
    assert _reviews(ops) == []
    assert state.ops["spec-run"]["exit"] == 1
    assert state.ops["spec-run"]["status"] == "started"


def test_spec_run_write_ahead_before_runner(state):
    ops = _ops()
    ops.prs = [_run_pr(5)]
    seen: list[dict] = []
    orig = ops.spec_runner

    def run(target_dir, args):
        seen.append(rs.load(state.run_id).ops.get("spec-run"))
        return orig(target_dir, args)

    ops.spec_runner = run
    assert auto_tasks._spec_run(state, ops) == 9
    assert seen == [{"status": "started", "before": [5]}]


def test_spec_run_completed_is_noop(state):
    rs.op_start(state, "spec-run", before=[])
    rs.op_complete(state, "spec-run", before=[], pr=9, exit=0)
    ops = _ops()
    assert auto_tasks._spec_run(state, ops) == 9
    assert ops.calls == []


def test_spec_run_forge_error_is_named_stop(state, capsys):
    ops = _ops()
    ops.prs_exc = RuntimeError("gh down")
    assert auto_tasks._spec_run(state, ops) is None
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):") and "gh down" in out
    assert _run_calls(ops) == []


def test_spec_run_strict_makes_no_calls(state):
    state.mode = "strict"
    ops = _ops()
    assert auto_tasks._spec_run(state, ops) is None
    assert ops.calls == []


def test_chain_runs_spec_run_then_code_accept(state, bridge):
    ops = _accepting_ops()
    assert run_chain(state, ops, T) == 0
    assert state.ops["spec-run"]["pr"] == 9
    assert state.ops["code-accept"]["status"] == "completed"


URL = "https://github.com/o/alpha/pull/9"
H0, H1 = "c" * 40, "d" * 40


def _marker(head: str) -> str:
    return f"t\n<!-- codex-terminal-review head={head} verdict=approve -->\n"


class _AcceptOps(_ChainOps):
    def __init__(self, facts, head: str = H0) -> None:
        super().__init__(facts)
        self.head = head
        self.facts[9] = self._pr9()

    def _pr9(self) -> dict:
        return {
            "state": "OPEN",
            "headRefOid": self.head,
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": GREEN,
        }

    def set_head(self, head: str) -> None:
        self.head = head
        self.facts[9] = self._pr9()

    def is_dirty(self, d):
        return False

    def current_branch(self, d):
        return "main"

    def materialize_pr_head(self, d, pr, sha):
        return sha

    def ensure_branch(self, d, b):
        pass

    def changed_paths(self, d, base):
        return ("base0", ["lib/x.ex"])

    def review(self, repo, pr, *, target_dir=None):
        self.calls.append(("review", repo, pr, target_dir))
        return 0

    def latest_review_body(self, slug, pr):
        return _marker(self.head)

    def merge(self, *a, **k):
        raise AssertionError("мерж в test запрещён")


def _accepting_ops(head: str = H0) -> _AcceptOps:
    return _AcceptOps({T: _facts(T, draft=True), A: _facts(A)}, head)


def _reviews(ops) -> list:
    return [c for c in ops.calls if c[0] == "review"]


def test_code_accept_records_verdict_and_result(state):
    ops = _accepting_ops()
    assert auto_tasks._accept_code(state, ops, 9) == 0
    rec = state.ops["code-accept"]
    assert rec["status"] == "completed"
    assert rec["verdict"]["head"] == H0
    assert rec["result"]["url"] == URL
    assert rec["result"]["merged"] is False
    assert _reviews(ops) == [("review", "alpha", 9, state.target_dir)]


def test_code_accept_same_head_no_paid_call(state, capsys):
    auto_tasks._accept_code(state, _accepting_ops(), 9)
    capsys.readouterr()
    ops = _accepting_ops()
    assert auto_tasks._accept_code(state, ops, 9) == 0
    assert _reviews(ops) == []
    assert URL in capsys.readouterr().out


def test_code_accept_new_head_reviews_again(state):
    auto_tasks._accept_code(state, _accepting_ops(H0), 9)
    ops = _accepting_ops(H1)
    assert auto_tasks._accept_code(state, ops, 9) == 0
    assert len(_reviews(ops)) == 1
    rec = state.ops["code-accept"]
    assert rec["result"]["head"] == H1 and rec["verdict"]["head"] == H1
    assert rec["prev"]["head"] == H0


def test_code_accept_strict_makes_no_calls(state):
    state.mode = "strict"
    ops = _accepting_ops()
    assert auto_tasks._accept_code(state, ops, 9) == 1
    assert ops.calls == []


def _request_changes(head: str) -> str:
    return f"t\n<!-- codex-terminal-review head={head} verdict=request-changes -->\n"


def test_code_accept_request_changes_records_nothing_and_rereviews(state):
    ops = _accepting_ops()
    ops.latest_review_body = lambda slug, pr: _request_changes(H0)
    assert auto_tasks._accept_code(state, ops, 9) == 1
    rec = state.ops["code-accept"]
    assert rec["status"] == "started"
    assert "verdict" not in rec and "result" not in rec
    again = _accepting_ops()
    assert auto_tasks._accept_code(state, again, 9) == 0
    assert len(_reviews(again)) == 1
    assert state.ops["code-accept"]["status"] == "completed"


def test_non_dict_facts_named_stop(state, bridge, capsys):
    ops = _ops()
    ops.facts[T] = ["not", "a", "dict"]
    assert run_chain(state, ops, T) == 1
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):") and f"#{T}" in out
    assert ops.calls == []


def test_empty_head_stops_before_checks_and_merge(state, bridge, monkeypatch, capsys):
    def no_wait(*a, **k):
        raise AssertionError("ожидание проверок без головы")

    monkeypatch.setattr(auto_tasks.an, "_await_required_checks", no_wait)
    ops = _ops()
    ops.facts[T] = {**_facts(T, draft=True), "headRefOid": ""}
    assert run_chain(state, ops, T) == 1
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):") and "headRefOid" in out
    assert "human_merge" not in ops.names()
    assert "mark_ready" not in ops.names()


def _tasks_merged(state) -> None:
    rs.op_start(state, "tasks-deliver")
    rs.op_complete(state, "tasks-deliver", pr=T)
    rs.op_start(state, "tasks-merge", pr=T, head=HEAD[T], tasks_skip=TASKS_SKIP_TEST)
    rs.op_complete(state, "tasks-merge", pr=T, head=HEAD[T], tasks_skip=TASKS_SKIP_TEST)


def test_resume_with_uncommitted_stamp_skips_delivery(state, bridge, monkeypatch):
    """Стоп после `spec approve`: штамп в дереве базы, tasks-merge завершён."""
    _tasks_merged(state)
    rs.op_start(state, "tasks-approve")
    bridge["ok"] = True
    delivered: list[int] = []

    def no_deliver(st, ops):
        raise AssertionError("deliver_for_run после мержа tasks-PR")

    monkeypatch.setattr(spec_loop.task_bridge, "deliver_for_run", no_deliver)
    monkeypatch.setattr(
        auto_tasks.tb, "deliver_approve", lambda st, ops: delivered.append(A) or A
    )
    ops = _accepting_ops()
    assert spec_loop._deliver_phase(state, ops) == 0
    assert delivered == [A]
    assert [c for c in ops.calls if c[0] == "spec_runner" and c[2][0] != "run"] == []
    assert state.ops["tasks-approve"]["status"] == "completed"


def test_deliver_approve_git_error_is_named_stop(state, bridge, monkeypatch, capsys):
    _tasks_merged(state)

    def git_fails(st, ops):
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(auto_tasks.tb, "deliver_approve", git_fails)
    ops = _accepting_ops()
    assert run_chain(state, ops, T) == 1
    out = capsys.readouterr().out
    assert out.startswith("spec-loop (test):") and "approve-PR" in out
    assert state.ops["tasks-approve"]["status"] == "started"
