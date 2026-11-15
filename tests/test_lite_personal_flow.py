"""Tests for lite.personal.flow (Task 8): the step orchestrator."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance.author_launch import LaunchOutcome
from governance.brief_facts import DefaultBranch
from governance.facts import Fact, Outcome
from lite import edge_resolve as er
from lite.personal import flow, state
from lite.personal.brief import Brief, Context, Function, Goal, brief_from_json
from lite.personal.interviewer import Halt

FIXTURES = Path(__file__).parent / "fixtures" / "personal"
SPEC_PR = 41
CODE_PR = 77
HEAD = "abc123def456"
BRIEF = Brief(
    (Goal("G-01", "цель", "текст"),),
    (
        Function("FR-01", "одно", ("G-01",), ("работает",), "слова"),
        Function("FR-02", "другое", ("G-01",), ("тоже",), "слова"),
    ),
    Context("я", "нет", "нет", "файлы", "macOS"),
)
NODE_TEXT = {
    "20-design.md": (FIXTURES / "20-design.md").read_text(encoding="utf-8"),
    "30-decomposition.md": (FIXTURES / "30-decomposition.md").read_text(
        encoding="utf-8"
    ),
}


class FakeOps:
    """Records every effect; forge facts are plain dicts."""

    def __init__(self, run: state.PersonalRun) -> None:
        self.run = run
        self.calls: list[tuple] = []
        self.dirty: list[str] = []
        self.branch: str | None = "master"
        self.merge_rc = 0
        self.spec_rc = 0
        self.code_state = "OPEN"
        self.prs: list[dict] = []
        self.existing_pr: int | None = None
        self.spec_head = HEAD
        self.default: Fact = Fact(Outcome.FOUND, DefaultBranch("master", "s"))

    def default_branch_fact(self, repo_slug: str) -> Fact:
        self.calls.append(("default_branch_fact", repo_slug))
        return self.default

    def dirty_paths(self, target_dir: str) -> list[str]:
        self.calls.append(("dirty_paths", target_dir))
        return list(self.dirty)

    def current_branch(self, target_dir: str) -> str | None:
        self.calls.append(("current_branch", target_dir))
        return self.branch

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        self.calls.append(("checkout_and_pull", target_dir, branch))

    def switch_to(self, target_dir: str, branch: str, start_point: str) -> None:
        self.calls.append(("switch_to", target_dir, branch, start_point))

    def commit_paths(self, target_dir, paths, message, force_paths=()) -> None:
        self.calls.append(("commit_paths", target_dir, list(paths)))

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", target_dir, branch))

    def head_sha(self, target_dir: str, branch: str) -> str:
        self.calls.append(("head_sha", target_dir, branch))
        return self.spec_head

    def find_pr(self, repo_slug: str, branch: str, *, any_state=False) -> int | None:
        self.calls.append(("find_pr", repo_slug, branch))
        return self.existing_pr

    def create_pr(
        self,
        target_dir,
        repo_slug,
        branch,
        title,
        body,
        label,
        *,
        draft=False,
        base=None,
    ) -> int:
        self.calls.append(("create_pr", repo_slug, branch, label, base))
        return SPEC_PR

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts", repo_slug, pr))
        if pr == SPEC_PR:
            return {"headRefOid": HEAD, "state": "OPEN", "url": "u/41"}
        return {"headRefOid": "h2", "state": self.code_state, "url": "u/77"}

    def human_merge(self, repo_name, pr, sha, *, target_dir=None) -> int:
        self.calls.append(("human_merge", repo_name, pr, sha))
        return self.merge_rc

    def spec_runner(self, target_dir: str, args: list[str]) -> int:
        self.calls.append(("spec_runner", target_dir, args))
        if self.spec_rc == 0:
            self.prs.append(
                {"number": CODE_PR, "state": "OPEN", "headRefName": "spec-runner/run-1"}
            )
        return self.spec_rc

    def prs_by_head_prefix(self, repo_slug: str, prefix: str) -> list[dict]:
        self.calls.append(("prs_by_head_prefix", repo_slug, prefix))
        return [p for p in self.prs if p["headRefName"].startswith(prefix)]

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def personal_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "personal-runs"
    monkeypatch.setattr(state, "PERSONAL_ROOT", root)
    monkeypatch.setattr(flow, "interview", lambda *a, **k: BRIEF)
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    return root


@pytest.fixture
def run(tmp_path: Path) -> state.PersonalRun:
    target = tmp_path / "target"
    target.mkdir()
    r = state.new_personal_run(
        "Заметки", "repo", "o/repo", str(target), date(2026, 11, 15)
    )
    state.save(r)
    return r


def launch(call, prompt) -> LaunchOutcome:
    """Fake author boundary: publishes the fixture text as the node file."""
    prompt("/tmp/out.md")
    path = Path(call.target_dir) / call.bundle_dir / call.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NODE_TEXT[call.filename], encoding="utf-8")
    return LaunchOutcome(0)


FINDING = {
    "rule_id": "R1",
    "class": "major",
    "statement": "плохо",
    "location": {"path": "20-design.md", "lines": "1"},
}


def checker(*verdicts: str):
    """Fake edge-check: verdicts in order (last one repeats)."""
    seq = list(verdicts)
    calls: list[str] = []

    def fake(edge_id, bundle, subject, bases, **kw):
        calls.append(edge_id)
        verdict = seq.pop(0) if len(seq) > 1 else seq[0]
        return {
            "edge": edge_id,
            "verdict": verdict,
            "findings": [FINDING] if verdict == "FAIL" else [],
            "subject": [],
            "error_code": "reviewer_failed",
            "reason": "прибор упал",
        }

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _advance(run, ops, check=None) -> int:
    return flow.advance(
        run,
        ops,
        read=lambda: None,
        agent=lambda p: "",
        launch=launch,
        check=check or checker("PASS"),
    )


def _bundle(run: state.PersonalRun) -> Path:
    return Path(run.target_dir) / run.bundle_dir


def test_happy_path_to_code_pr(run, capsys) -> None:
    ops = FakeOps(run)
    assert _advance(run, ops) == 0
    assert run.step == "code-pr" and run.spec_pr == SPEC_PR and run.code_pr == CODE_PR
    assert state.load(run.run_id).step == "code-pr"
    target, ws = run.target_dir, run.ws_id
    branch = f"spec/{ws}-personal"
    tasks_path = f"spec/{ws}-tasks.md"
    effects = [c for c in ops.calls if c[0] not in ("prs_by_head_prefix",)]
    assert effects == [
        ("default_branch_fact", "o/repo"),
        ("dirty_paths", target),
        ("current_branch", target),
        ("dirty_paths", target),
        ("current_branch", target),
        ("checkout_and_pull", target, "master"),
        ("switch_to", target, branch, "master"),
        ("commit_paths", target, [run.bundle_dir, tasks_path]),
        ("push_branch", target, branch),
        ("head_sha", target, branch),
        ("find_pr", "o/repo", branch),
        ("create_pr", "o/repo", branch, "", "master"),
        ("pr_facts", "o/repo", SPEC_PR),
        ("human_merge", "repo", SPEC_PR, HEAD),
        ("checkout_and_pull", target, "master"),
        (
            "spec_runner",
            target,
            ["run", "--all", "--no-strict", f"--spec-prefix={ws}-", "--integration-pr"],
        ),
        ("pr_facts", "o/repo", CODE_PR),
    ]
    rd = state.run_dir(run.run_id)
    assert brief_from_json(json.loads((rd / "brief.json").read_text())) == BRIEF
    approvals = (rd / "approvals.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(a) for a in approvals] == [
        {"node": "brief", "how": "подтверждено в разговоре"}
    ]
    for name in ("00-brief.md", "20-design.md", "30-decomposition.md"):
        assert (_bundle(run) / name).is_file()
    assert (Path(target) / tasks_path).is_file()
    out = capsys.readouterr().out
    assert "spec-runner tui" in out and "смержите u/77" in out


@pytest.mark.parametrize("step", state.STEPS[:-1])
def test_resume_each_step(run, step, monkeypatch) -> None:
    ops = FakeOps(run)
    done: list[str] = []
    monkeypatch.setattr(
        flow, "STEP_HANDLERS", {s: _recorder(s, done) for s in state.STEPS}
    )
    run.step = step
    assert _advance(run, ops) == 0
    assert done[0] == step
    assert done == list(state.STEPS[state.STEPS.index(step) : -1])


def _recorder(step: str, done: list[str]):
    def handler(ctx) -> bool:
        done.append(step)
        nxt = state.STEPS[state.STEPS.index(step) + 1]
        if nxt == "done":
            return False  # waits for the human
        ctx.run.step = nxt
        return True

    return handler


def test_resume_real_handlers_from_spec_pr(run) -> None:
    """Resume at `spec-pr`: no interview, no authoring, no edge-check."""
    _bundle(run).mkdir(parents=True)
    for name, text in NODE_TEXT.items():
        (_bundle(run) / name).write_text(text, encoding="utf-8")
    run.step = "spec-pr"
    check = checker("PASS")
    ops = FakeOps(run)
    assert _advance(run, ops, check) == 0
    assert check.calls == [] and run.step == "code-pr"  # type: ignore[attr-defined]
    assert ops.names()[:2] == ["default_branch_fact", "dirty_paths"]


def test_fail_then_revised_then_pass(run, monkeypatch) -> None:
    outcomes: list[tuple] = []

    def fake_resolve(scope, findings, *, read, agent, revise, round_no, calls):
        outcomes.append((scope.wave, scope.subject, round_no, len(findings)))
        assert scope.filenames["brief"] == "00-brief.md"
        assert scope.filenames["design"] == "20-design.md"
        assert scope.can_override is True
        return er.Outcome.REVISED

    monkeypatch.setattr(flow, "resolve_scope", fake_resolve)
    check = checker("FAIL", "PASS")
    assert _advance(run, FakeOps(run), check) == 0
    assert outcomes == [(1, "Заметки", 1, 1)]
    assert run.step == "code-pr"
    assert check.calls == [  # type: ignore[attr-defined]
        "personal-design-vs-brief",
        "personal-design-vs-brief",
        "personal-decomposition-vs-design-brief",
    ]


def test_not_converged_stops_after_max_rounds(run, monkeypatch, capsys) -> None:
    rounds: list[int] = []

    def fake_resolve(scope, findings, *, read, agent, revise, round_no, calls):
        rounds.append(round_no)
        return er.Outcome.REVISED

    monkeypatch.setattr(flow, "resolve_scope", fake_resolve)
    ops = FakeOps(run)
    assert _advance(run, ops, checker("FAIL")) == 1
    assert rounds == list(range(1, er.MAX_ROUNDS + 1))
    assert run.step == "design" and "не сошлись" in run.stop_reason
    assert flow.ABANDON in run.stop_reason
    assert state.load(run.run_id).stop_reason == run.stop_reason
    assert run.stop_reason in capsys.readouterr().out
    assert ops.names() == ["default_branch_fact", "dirty_paths", "current_branch"]


def test_resolution_stopped_stops(run, monkeypatch) -> None:
    monkeypatch.setattr(flow, "resolve_scope", lambda *a, **k: er.Outcome.STOPPED)
    assert _advance(run, FakeOps(run), checker("FAIL")) == 1
    assert run.step == "design" and run.stop_reason


def test_dirty_tree_stops_before_git_effects(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.dirty = [f"{run.bundle_dir}/00-brief.md", "src/app.py"]
    assert _advance(run, ops) == 1
    assert run.step == "spec-pr" and "src/app.py" in run.stop_reason
    assert f"{run.bundle_dir}/00-brief.md" not in run.stop_reason
    assert not {"checkout_and_pull", "switch_to", "commit_paths"} & set(ops.names())


def test_own_bundle_files_are_not_dirt(run) -> None:
    ops = FakeOps(run)
    ops.dirty = [f"{run.bundle_dir}/00-brief.md", f"spec/{run.ws_id}-tasks.md"]
    assert _advance(run, ops) == 0
    assert run.step == "code-pr"


def test_wrong_branch_stops_before_git_effects(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.branch = "feature/x"
    assert _advance(run, ops) == 1
    assert run.step == "spec-pr" and "feature/x" in run.stop_reason
    assert not {"checkout_and_pull", "switch_to", "commit_paths"} & set(ops.names())


def test_edge_error_stops_without_resolve(run, monkeypatch) -> None:
    def no_resolve(*a, **k):
        raise AssertionError("resolution must not be called")

    monkeypatch.setattr(flow, "resolve_scope", no_resolve)
    _bundle(run).mkdir(parents=True)
    design = _bundle(run) / "20-design.md"
    design.write_text("мой дизайн", encoding="utf-8")
    run.step = "design"
    assert _advance(run, FakeOps(run), checker("ERROR")) == 1
    assert run.stop_reason.startswith("проверка ребра не состоялась: ")
    assert "прибор упал" in run.stop_reason
    assert design.read_text(encoding="utf-8") == "мой дизайн"
    assert run.step == "design"


def test_edge_error_on_recheck_stops(run, monkeypatch) -> None:
    monkeypatch.setattr(flow, "resolve_scope", lambda *a, **k: er.Outcome.REVISED)
    assert _advance(run, FakeOps(run), checker("FAIL", "ERROR")) == 1
    assert run.stop_reason.startswith("проверка ребра не состоялась: ")


def _wider_brief_at_decomposition(run: state.PersonalRun) -> None:
    """Бриф с FR-03, которого эталонная декомпозиция не покрывает."""
    from dataclasses import asdict

    rd = state.run_dir(run.run_id)
    rd.mkdir(parents=True, exist_ok=True)
    wider = Brief(
        BRIEF.goals,
        (*BRIEF.functions, Function("FR-03", "ещё", ("G-01",), ("x",), "w")),
        BRIEF.context,
    )
    (rd / "brief.json").write_text(json.dumps(asdict(wider)), encoding="utf-8")
    run.step = "decomposition"
    _bundle(run).mkdir(parents=True)
    (_bundle(run) / "20-design.md").write_text("d", encoding="utf-8")


DECOMP = NODE_TEXT["30-decomposition.md"]
FIXED = DECOMP.replace("**Критерии:** FR-02/1", "**Критерии:** FR-02/1, FR-03/1")


def _reviser(revised: str | None):
    """Автор: первый вызов пишет эталон, правка (режим правки) — `revised`."""
    prompts: list[str] = []

    def fake(call, prompt) -> LaunchOutcome:
        text = prompt("/tmp/out.md")
        prompts.append(text)
        body = NODE_TEXT[call.filename]
        if "## Режим правки" in text and revised is not None:
            body = revised
        path = Path(call.target_dir) / call.bundle_dir / call.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return LaunchOutcome(0)

    fake.prompts = prompts  # type: ignore[attr-defined]
    return fake


def _advance_with(run, ops, launch_fn, check) -> int:
    return flow.advance(
        run, ops, read=lambda: None, agent=lambda p: "", launch=launch_fn, check=check
    )


def test_decomposition_problems_revised_by_agent(run) -> None:
    """Нарушения инвариантов уходят автору (не человеку); после правки —
    снова edge-check и инварианты, затем дальше."""
    _wider_brief_at_decomposition(run)
    author = _reviser(FIXED)
    check = checker("PASS")
    assert _advance_with(run, FakeOps(run), author, check) == 0
    assert run.step == "code-pr"
    revisions = [p for p in author.prompts if "## Режим правки" in p]
    assert len(revisions) == 1 and "FR-03/1" in revisions[0]
    edge = "personal-decomposition-vs-design-brief"
    assert check.calls.count(edge) == 2  # type: ignore[attr-defined]


def test_decomposition_problems_persist_stop(run) -> None:
    _wider_brief_at_decomposition(run)
    author = _reviser(None)  # правка ничего не чинит
    assert _advance_with(run, FakeOps(run), author, checker("PASS")) == 1
    revisions = [p for p in author.prompts if "## Режим правки" in p]
    assert len(revisions) == er.MAX_ROUNDS
    assert "FR-03/1" in run.stop_reason and run.step == "decomposition"
    assert f"после {er.MAX_ROUNDS} кругов правки" in run.stop_reason


def test_decomposition_revision_weakening_restored(run) -> None:
    """Правка, удалившая проверку BEH-02, отклоняется, файл возвращается."""
    _wider_brief_at_decomposition(run)
    cut = DECOMP.split("#### BEH-02")[0] + "## Задачи" + DECOMP.split("## Задачи")[1]
    author = _reviser(cut)
    assert _advance_with(run, FakeOps(run), author, checker("PASS")) == 1
    assert "BEH-02" in run.stop_reason and run.step == "decomposition"
    node = _bundle(run) / "30-decomposition.md"
    assert node.read_text(encoding="utf-8") == DECOMP


def test_interview_failed_stops(run, monkeypatch) -> None:
    monkeypatch.setattr(flow, "interview", lambda *a, **k: Halt.FAILED)
    assert _advance(run, FakeOps(run)) == 1
    assert run.step == "interview" and run.stop_reason
    assert not (_bundle(run) / "00-brief.md").exists()


def test_interview_paused_is_not_failure(run, monkeypatch, capsys) -> None:
    """Человек прервал интервью (/стоп, Ctrl-D, Ctrl-C) — код 0, не сбой."""
    monkeypatch.setattr(flow, "interview", lambda *a, **k: Halt.PAUSED)
    assert _advance(run, FakeOps(run)) == 0
    assert run.step == "interview"
    assert run.stop_reason == flow.INTERVIEW_PAUSED
    assert flow.INTERVIEW_PAUSED in capsys.readouterr().out
    assert not (_bundle(run) / "00-brief.md").exists()


def test_author_failure_stops(run) -> None:
    def failing(call, prompt) -> LaunchOutcome:
        return LaunchOutcome(2, "нет харнесса")

    run.step = "design"
    _bundle(run).mkdir(parents=True)
    rc = flow.advance(
        run,
        FakeOps(run),
        read=lambda: None,
        agent=lambda p: "",
        launch=failing,
        check=checker("PASS"),
    )
    assert rc == 1 and "нет харнесса" in run.stop_reason


def test_merge_refused_stops(run) -> None:
    ops = FakeOps(run)
    ops.merge_rc = 3
    assert _advance(run, ops) == 1
    assert run.step == "spec-pr" and run.spec_pr == SPEC_PR
    assert "проверки PR ещё идут или не прошли" in run.stop_reason
    assert "повторите dfl run позже" in run.stop_reason
    # Resume: the PR is not created again.
    ops.merge_rc = 0
    ops.calls.clear()
    assert _advance(run, ops) == 0
    assert "create_pr" not in ops.names() and "switch_to" not in ops.names()
    assert run.step == "code-pr"


def test_spec_runner_failure_stops_then_retries(run) -> None:
    ops = FakeOps(run)
    ops.spec_rc = 1
    assert _advance(run, ops) == 1
    assert run.step == "code-run" and "spec-runner run вернул 1" in run.stop_reason
    ops.spec_rc = 0
    assert _advance(run, ops) == 0
    assert run.code_pr == CODE_PR and run.step == "code-pr"


def test_code_run_resume_takes_pr_after_observed_success(run) -> None:
    """Crash after rc 0 was saved: the new PR is taken without a second run."""
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    run.run_exit = 0
    ops.prs.append(
        {"number": CODE_PR, "state": "OPEN", "headRefName": "spec-runner/run-1"}
    )
    assert _advance(run, ops) == 0
    assert "spec_runner" not in ops.names() and run.code_pr == CODE_PR


def test_code_run_unobserved_outcome_with_new_pr_stops(run) -> None:
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    ops.prs.append(
        {"number": CODE_PR, "state": "OPEN", "headRefName": "spec-runner/run-1"}
    )
    assert _advance(run, ops) == 1
    assert "spec_runner" not in ops.names() and f"#{CODE_PR}" in run.stop_reason


def test_code_pr_merged_marks_done(run, capsys) -> None:
    ops = FakeOps(run)
    ops.code_state = "MERGED"
    run.step = "code-pr"
    run.code_pr = CODE_PR
    assert _advance(run, ops) == 0
    assert run.step == "done" and state.load(run.run_id).step == "done"
    assert "конвейер закончен" in capsys.readouterr().out


def test_stop_reason_cleared_on_progress(run) -> None:
    run.stop_reason = "старое"
    run.step = "code-pr"
    run.code_pr = CODE_PR
    assert _advance(run, FakeOps(run)) == 0
    assert run.stop_reason == ""


def test_overridden_then_recheck_fail_accepted_passes(run, monkeypatch) -> None:
    """Принятие вопреки: перепроверка снова FAIL, но все находки приняты."""
    monkeypatch.setattr(flow, "resolve_scope", lambda *a, **k: er.Outcome.OVERRIDDEN)
    monkeypatch.setattr(
        flow.override,
        "accepted",
        lambda records, digests: (
            {("personal-design", "personal-design-vs-brief", "R1")}
            if ("personal-design", "personal-design-vs-brief") in records
            else None
        ),
    )
    bodies: list[str] = []
    ops = FakeOps(run)
    original = ops.create_pr

    def create_pr(target_dir, repo_slug, branch, title, body, label, **kw):
        bodies.append(body)
        return original(target_dir, repo_slug, branch, title, body, label, **kw)

    ops.create_pr = create_pr  # type: ignore[method-assign]
    check = checker("FAIL", "FAIL", "PASS")
    assert _advance(run, ops, check) == 0
    assert run.step == "code-pr"
    assert len(check.calls) == 3  # type: ignore[attr-defined]
    assert "personal-design/personal-design-vs-brief R1" in bodies[0]


def _nodes_on_disk(run: state.PersonalRun) -> None:
    _bundle(run).mkdir(parents=True, exist_ok=True)
    for name, text in NODE_TEXT.items():
        (_bundle(run) / name).write_text(text, encoding="utf-8")
    run.step = "spec-pr"


def test_spec_pr_resume_after_commit_before_pr(run) -> None:
    """Падение после коммита/push до PR: ветка прогона — не стоп, без switch."""
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.branch = f"spec/{run.ws_id}-personal"
    assert _advance(run, ops) == 0
    names = ops.names()
    assert "switch_to" not in names and "checkout_and_pull" in names
    assert names.index("commit_paths") < names.index("push_branch")
    assert names.count("find_pr") == 1 and names.count("create_pr") == 1
    assert names.index("find_pr") < names.index("create_pr")
    assert run.spec_pr == SPEC_PR and run.step == "code-pr"


def test_spec_pr_resume_existing_pr(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.branch = f"spec/{run.ws_id}-personal"
    ops.existing_pr = 7
    _advance(run, ops)
    assert "create_pr" not in ops.names()
    assert run.spec_pr == 7 and state.load(run.run_id).spec_pr == 7


def test_spec_pr_merge_pinned_to_pushed_sha(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    assert _advance(run, ops) == 0
    assert run.spec_head == HEAD and state.load(run.run_id).spec_head == HEAD
    merges = [c for c in ops.calls if c[0] == "human_merge"]
    assert merges == [("human_merge", "repo", SPEC_PR, HEAD)]
    names = ops.names()
    assert names.index("head_sha") < names.index("create_pr")


def test_spec_pr_head_moved_refuses_merge(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.spec_head = "0000000000ff"  # we pushed this; the PR head is HEAD
    assert _advance(run, ops) == 1
    assert "human_merge" not in ops.names()
    assert "голова PR" in run.stop_reason and run.step == "spec-pr"


def test_spec_pr_not_test_mode_refuses(run, monkeypatch) -> None:
    monkeypatch.setattr(flow, "config_mode", lambda: "strict")
    _nodes_on_disk(run)
    ops = FakeOps(run)
    assert _advance(run, ops) == 1
    assert "human_merge" not in ops.names()
    assert "режиме test" in run.stop_reason and run.spec_pr == SPEC_PR


def test_approval_not_duplicated_on_rerun(run) -> None:
    rd = state.run_dir(run.run_id)
    rd.mkdir(parents=True, exist_ok=True)
    flow._approve_brief(rd / flow.APPROVALS)
    flow._approve_brief(rd / flow.APPROVALS)
    assert len((rd / flow.APPROVALS).read_text(encoding="utf-8").splitlines()) == 1


def test_main_default_branch_resolved_before_interview(run, monkeypatch) -> None:
    """Цель на `main`: база — ветка по умолчанию форджи, записана до интервью."""
    seen: list[str] = []
    monkeypatch.setattr(
        flow, "interview", lambda *a, **k: seen.append(run.base_ref) or BRIEF
    )
    ops = FakeOps(run)
    ops.default = Fact(Outcome.FOUND, DefaultBranch("main", "s"))
    ops.branch = "main"
    assert _advance(run, ops) == 0
    assert seen == ["main"] and state.load(run.run_id).base_ref == "main"
    assert ("checkout_and_pull", run.target_dir, "main") in ops.calls
    assert ("create_pr", "o/repo", f"spec/{run.ws_id}-personal", "", "main") in (
        ops.calls
    )
    assert run.step == "code-pr"


def test_default_branch_unknown_not_persisted(run, capsys) -> None:
    ops = FakeOps(run)
    ops.default = Fact(Outcome.UNAVAILABLE, None, "сеть")
    assert _advance(run, ops) == 0
    assert run.base_ref == "" and state.load(run.run_id).base_ref == ""
    assert ("checkout_and_pull", run.target_dir, "master") in ops.calls
    assert "сеть" in capsys.readouterr().out


def test_stored_base_ref_not_asked_again(run) -> None:
    run.base_ref = "trunk"
    ops = FakeOps(run)
    ops.branch = "trunk"
    assert _advance(run, ops) == 0
    assert "default_branch_fact" not in ops.names()


def test_wrong_branch_stops_before_interview(run, monkeypatch) -> None:
    monkeypatch.setattr(
        flow, "interview", lambda *a, **k: pytest.fail("платное интервью")
    )
    ops = FakeOps(run)
    ops.branch = "feature/x"
    assert _advance(run, ops) == 1
    assert run.step == "interview"
    assert "master" in run.stop_reason and f"spec/{run.ws_id}-personal" in (
        run.stop_reason
    )


def test_dirty_tree_stops_before_authoring(run) -> None:
    def no_launch(call, prompt):
        raise AssertionError("платный автор")

    run.step = "design"
    ops = FakeOps(run)
    ops.dirty = ["src/app.py"]
    rc = flow.advance(
        run,
        ops,
        read=lambda: None,
        agent=lambda p: "",
        launch=no_launch,
        check=checker("PASS"),
    )
    assert rc == 1 and "src/app.py" in run.stop_reason and run.step == "design"


def test_fail_without_findings_names_criteria(run, monkeypatch) -> None:
    """FAIL без блокирующих находок — стоп с критериями, без разбора."""

    def no_resolve(*a, **k):
        raise AssertionError("resolution must not be called")

    def fake(edge_id, bundle, subject, bases, **kw):
        return {
            "edge": edge_id,
            "verdict": "FAIL",
            "findings": [],
            "subject": [],
            "criteria": [{"id": "C2", "status": "fail", "reason": "нет модулей"}],
        }

    monkeypatch.setattr(flow, "resolve_scope", no_resolve)
    assert _advance(run, FakeOps(run), fake) == 1
    assert run.step == "design"
    assert "C2 (fail): нет модулей" in run.stop_reason
    assert "not_applicable" not in run.stop_reason
    assert "20-design.md" in run.stop_reason


def test_closed_spec_pr_mentions_abandon(run, monkeypatch) -> None:
    _nodes_on_disk(run)
    run.spec_pr, run.spec_head = SPEC_PR, HEAD
    ops = FakeOps(run)
    monkeypatch.setattr(ops, "pr_facts", lambda slug, pr: {"state": "CLOSED"})
    assert _advance(run, ops) == 1
    assert "закрыт без мержа" in run.stop_reason and flow.ABANDON in run.stop_reason


def test_design_rewrite_dropping_decision_restored(run, capsys) -> None:
    """Регрессия ревью #774: переписывание дизайна, удалившее D-02 без
    решения человека, отклоняется node_guard'ом, файл возвращается."""
    design = NODE_TEXT["20-design.md"]
    cut = design.split("#### D-02")[0]
    author = _reviser(cut)
    fix = [{"finding": 1, "kind": "fix", "question": "", "options": []}]
    rc = flow.advance(
        run,
        FakeOps(run),
        read=lambda: None,
        agent=lambda p: json.dumps({"items": fix}),
        launch=author,
        check=checker("FAIL", "PASS"),
    )
    assert rc == 1 and run.step == "design"
    assert [p for p in author.prompts if "## Режим правки" in p]
    assert (_bundle(run) / "20-design.md").read_text(encoding="utf-8") == design
    assert "D-02" in capsys.readouterr().out
