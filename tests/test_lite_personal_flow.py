"""Tests for lite.personal.flow (Task 8): the step orchestrator."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance import run_resume
from governance.author_launch import LaunchOutcome
from governance.brief_facts import DefaultBranch
from governance.facts import Fact, Outcome
from lite import edge_journal as ej
from lite import edge_resolve as er
from lite import pending
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
        #: голова локальной ветки прогона; None — ветки нет
        self.run_head: str | None = None
        self.run_head_fact: Fact | None = None  # переопределить факт целиком
        self.ancestor: bool | None = False
        self.push_fail = False
        self.fetched = True
        self.tasks_md: str | None = None
        #: пути ветки прогона сверх закоммиченных прогоном (чужой коммит)
        self.extra_changed: list[str] = []

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
        self.branch = branch

    def switch_to(self, target_dir: str, branch: str, start_point: str) -> None:
        self.calls.append(("switch_to", target_dir, branch, start_point))
        self.branch, self.run_head = branch, "base-sha"

    def local_branch_head_fact(self, target_dir: str, branch: str) -> Fact:
        self.calls.append(("local_branch_head_fact", target_dir, branch))
        if self.run_head_fact is not None:
            return self.run_head_fact
        if self.run_head is None:
            return Fact(Outcome.ABSENT, None, "нет")
        return Fact(Outcome.FOUND, self.run_head, "есть")

    def is_ancestor(self, target_dir: str, sha: str, ref: str) -> bool | None:
        self.calls.append(("is_ancestor", target_dir, sha, ref))
        return self.ancestor

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("ensure_branch", target_dir, branch))
        self.branch = branch

    def commit_paths(self, target_dir, paths, message, force_paths=()) -> None:
        self.calls.append(("commit_paths", target_dir, list(paths)))
        self.run_head = "bundle-sha"

    def changed_paths(self, target_dir: str, base_branch: str):
        self.calls.append(("changed_paths", target_dir, base_branch))
        committed = next(c for c in reversed(self.calls) if c[0] == "commit_paths")
        files = [
            f"{p}/20-design.md" if p == self.run.bundle_dir else p for p in committed[2]
        ]
        return "base-sha", [*files, *self.extra_changed]

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", target_dir, branch))
        if self.push_fail:
            raise RuntimeError("push отвергнут")

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
            return {"headRefOid": HEAD, "state": "OPEN"}
        return {"headRefOid": "h2", "state": self.code_state}

    def human_merge(self, repo_name, pr, sha, *, target_dir=None) -> int:
        self.calls.append(("human_merge", repo_name, pr, sha))
        return self.merge_rc

    def spec_runner(self, target_dir: str, args: list[str]) -> int:
        self.calls.append(("spec_runner", target_dir, args))
        if self.spec_rc == 0:
            self.prs.append(
                {
                    "number": CODE_PR,
                    "state": "OPEN",
                    "head": {"ref": "spec-runner/run-1"},
                }
            )
        return self.spec_rc

    def fetch_branch(self, target_dir: str, branch: str) -> bool:
        self.calls.append(("fetch_branch", branch))
        return self.fetched

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None:
        self.calls.append(("show_file", ref, path))
        return self.tasks_md

    def prs_by_head_prefix(self, repo_slug: str, prefix: str) -> list[dict]:
        self.calls.append(("prs_by_head_prefix", repo_slug, prefix))
        return [p for p in self.prs if p["head"]["ref"].startswith(prefix)]

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


def _on_node(edge_id: str) -> dict:
    """FINDING, локализованная на файле узла ребра (как у настоящего ревьюера)."""
    name = "30-decomposition.md" if "decomposition" in edge_id else "20-design.md"
    return {**FINDING, "location": {"path": name, "lines": "1"}}


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
            "findings": [_on_node(edge_id)] if verdict == "FAIL" else [],
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
        ("local_branch_head_fact", target, branch),
        ("checkout_and_pull", target, "master"),
        ("switch_to", target, branch, "master"),
        ("commit_paths", target, [run.bundle_dir, tasks_path]),
        ("changed_paths", target, "master"),
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
        ("fetch_branch", "spec-runner/run-1"),
        ("show_file", "origin/spec-runner/run-1", tasks_path),
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
    assert (
        "spec-runner tui" in out
        and "смержите https://github.com/o/repo/pull/77 — затем dfl run" in out
    )


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
        {"number": CODE_PR, "state": "OPEN", "head": {"ref": "spec-runner/run-1"}}
    )
    assert _advance(run, ops) == 0
    assert "spec_runner" not in ops.names() and run.code_pr == CODE_PR


def test_code_run_unobserved_outcome_with_new_pr_stops(run) -> None:
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    ops.prs.append(
        {"number": CODE_PR, "state": "OPEN", "head": {"ref": "spec-runner/run-1"}}
    )
    assert _advance(run, ops) == 1
    assert "spec_runner" not in ops.names() and f"#{CODE_PR}" in run.stop_reason


def _partial(number: int, state_: str = "OPEN") -> dict:
    return {
        "number": number,
        "state": state_,
        "head": {"ref": f"spec-runner/run-{number}"},
    }


def test_code_run_failed_attempt_with_open_partial_pr_stops_with_banner(
    run, capsys
) -> None:
    """#767: повтор после упавшего прогона — плашка и стоп, не новая ветка."""
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    run.run_exit = 1
    ops.prs.append(_partial(7))
    ops.tasks_md = "### TASK-001: a\nP1 | ✅ DONE\n\n### TASK-002: b\nP1 | TODO\n"
    assert _advance(run, ops) == 1
    assert "spec_runner" not in ops.names()
    tasks_rel = f"spec/{run.ws_id}-tasks.md"
    assert ("show_file", "origin/spec-runner/run-7", tasks_rel) in ops.calls
    expected = run_resume.banner(
        run.subject, run.repo_slug, "spec-runner/run-7", (1, 2)
    )
    assert expected in capsys.readouterr().out
    assert run.stop_reason == run_resume.resume_stop("spec-runner/run-7", 7)
    assert run.before_prs == [] and run.run_exit == 1


def test_code_run_failed_attempt_closed_partial_pr_reruns(run) -> None:
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    run.run_exit = 1
    ops.prs.append(_partial(7, "CLOSED"))
    assert _advance(run, ops) == 0
    assert "spec_runner" in ops.names() and run.code_pr == CODE_PR


def test_code_run_two_partial_prs_stop(run) -> None:
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    run.run_exit = 1
    ops.prs += [_partial(7), _partial(8)]
    assert _advance(run, ops) == 1
    assert "spec_runner" not in ops.names()
    assert run.stop_reason == run_resume.several_stop([7, 8])


def test_spec_runner_failure_with_partial_pr_promises_banner(run) -> None:
    """После падения с частичным PR повтор не перезапустит раннер (#767)."""
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    ops.spec_rc = 1
    orig = ops.spec_runner

    def run_with_partial(target_dir, args):
        ops.prs.append(_partial(7))
        return orig(target_dir, args)

    ops.spec_runner = run_with_partial
    assert _advance(run, ops) == 1
    assert "перезапустит" not in run.stop_reason
    assert "не создаст новую ветку" in run.stop_reason


INCOMPLETE_TASKS = "### TASK-001: a\nP1 | ✅ DONE\n\n### TASK-002: b\nP1 | TODO\n"


def test_code_run_exit_zero_with_unfinished_tasks_stops_with_banner(
    run, capsys
) -> None:
    """spec-runner, остановленный штатно (SIGINT), выходит 0 и открывает
    частичный PR — не принимать за готовый: плашка и стоп (#767)."""
    ops = FakeOps(run)
    run.step = "code-run"
    ops.tasks_md = INCOMPLETE_TASKS
    assert _advance(run, ops) == 1
    expected = run_resume.banner(
        run.subject, run.repo_slug, "spec-runner/run-1", (1, 2)
    )
    assert expected in capsys.readouterr().out
    assert run.stop_reason == run_resume.resume_stop("spec-runner/run-1", CODE_PR)
    assert run.step == "code-run" and run.code_pr is None
    assert run.run_exit == run_resume.INCOMPLETE_EXIT
    # Повтор: тот же стоп, раннер второй раз не запускается.
    ops.calls.clear()
    assert _advance(run, ops) == 1
    assert "spec_runner" not in ops.names()
    assert run.stop_reason == run_resume.resume_stop("spec-runner/run-1", CODE_PR)


def test_code_run_unfinished_pr_merged_by_human_reruns(run) -> None:
    """Частичный PR влит человеком — раннер доделывает остальное."""
    ops = FakeOps(run)
    run.step = "code-run"
    run.before_prs = []
    run.run_exit = run_resume.INCOMPLETE_EXIT
    ops.prs.append(_partial(7, "MERGED"))
    assert _advance(run, ops) == 0
    assert "spec_runner" in ops.names() and run.code_pr == CODE_PR


def test_code_run_all_tasks_done_takes_pr(run) -> None:
    ops = FakeOps(run)
    run.step = "code-run"
    ops.tasks_md = INCOMPLETE_TASKS.replace("P1 | TODO", "P1 | ✅ DONE")
    assert _advance(run, ops) == 0
    assert run.code_pr == CODE_PR and run.step == "code-pr"


def test_code_pr_with_unfinished_tasks_returns_to_code_run(run, capsys) -> None:
    """Запись, уже принявшая частичный PR (до исправления): ручные проверки
    и мерж не начинаются, прогон возвращается к развилке #767."""
    ops = FakeOps(run)
    run.step = "code-pr"
    run.code_pr = CODE_PR
    run.before_prs = []
    ops.prs.append(
        {"number": CODE_PR, "state": "OPEN", "head": {"ref": "spec-runner/run-1"}}
    )
    ops.tasks_md = INCOMPLETE_TASKS
    assert _advance(run, ops) == 1
    assert "pr_facts" not in ops.names()
    assert "▶ продолжаю проект" in capsys.readouterr().out
    assert run.step == "code-run" and run.code_pr is None
    assert run.run_exit == run_resume.INCOMPLETE_EXIT
    assert state.load(run.run_id).step == "code-run"


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


def test_spec_pr_resume_from_base_keeps_committed_branch(run) -> None:
    """Регрессия ревью #774: коммит бандла сделан, push упал, человек
    переключился на базу — повтор не пересоздаёт ветку прогона от базы
    (`switch -C` сбросил бы коммит), а переходит на неё как есть."""
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.push_fail = True
    assert _advance(run, ops) == 1
    assert "spec-PR не создан" in run.stop_reason and run.step == "spec-pr"
    assert state.load(run.run_id).spec_committed is True
    ops.branch, ops.push_fail = "master", False  # человек: git switch master
    ops.calls.clear()
    assert _advance(run, ops) == 0
    names = ops.names()
    assert "switch_to" not in names
    assert names.index("ensure_branch") < names.index("push_branch")
    assert "checkout_and_pull" not in names[: names.index("push_branch")]
    assert ops.run_head == "bundle-sha" and run.step == "code-pr"


def test_spec_pr_foreign_commits_on_existing_branch_stop(run) -> None:
    """Ветка прогона есть, прогон в неё не коммитил, а она ушла от базы —
    стоп с названной причиной до любого git-эффекта."""
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.run_head, ops.ancestor = "foreign-sha", False
    assert _advance(run, ops) == 1
    assert "уже есть" in run.stop_reason and run.step == "spec-pr"
    effects = {"checkout_and_pull", "switch_to", "ensure_branch", "commit_paths"}
    assert not effects & set(ops.names())


def test_spec_pr_foreign_commit_on_committed_branch_stops_before_push(run) -> None:
    """Регрессия ревью #774: на ветке прогона с коммитом бандла человек
    сделал свой коммит — push и автомерж spec-PR не увозят его в базу."""
    _nodes_on_disk(run)
    run.spec_committed = True
    ops = FakeOps(run)
    ops.branch, ops.run_head = f"spec/{run.ws_id}-personal", "bundle-sha"
    ops.extra_changed = ["src/app.py"]
    assert _advance(run, ops) == 1
    assert "origin/master: src/app.py" in run.stop_reason
    assert "незапушенный коммит локальной master" in run.stop_reason
    assert run.step == "spec-pr"
    assert not {"push_branch", "create_pr", "human_merge"} & set(ops.names())


def test_spec_pr_empty_existing_branch_entered_as_is(run) -> None:
    """Ветка прогона есть, но пуста (падение между веткой и коммитом) —
    переход как есть, без пересоздания."""
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.run_head, ops.ancestor = "base-sha", True
    assert _advance(run, ops) == 0
    assert "switch_to" not in ops.names() and "ensure_branch" in ops.names()


def test_spec_pr_committed_branch_missing_stops(run) -> None:
    _nodes_on_disk(run)
    run.spec_committed = True
    ops = FakeOps(run)
    assert _advance(run, ops) == 1
    assert "пропала" in run.stop_reason
    assert not {"checkout_and_pull", "switch_to", "commit_paths"} & set(ops.names())


def test_spec_pr_branch_fact_unavailable_stops(run) -> None:
    _nodes_on_disk(run)
    ops = FakeOps(run)
    ops.run_head_fact = Fact(Outcome.UNAVAILABLE, None, "git сломан")
    assert _advance(run, ops) == 1
    assert "git сломан" in run.stop_reason
    assert not {"checkout_and_pull", "switch_to", "commit_paths"} & set(ops.names())


def test_paid_calls_counted_on_happy_path(run, capsys) -> None:
    """Регрессия ревью #774: авторинг и каждая проверка ребра (и первая)
    — платные вызовы; строка расходов печатается."""
    assert _advance(run, FakeOps(run)) == 0
    out = capsys.readouterr().out
    assert (
        "платных вызовов: авторинг 2, сортировка 0, переписывание 0, проверка 2" in out
    )


def test_paid_calls_count_recheck_and_revision(run, monkeypatch) -> None:
    """Перепроверка после разбора и правка инвариантов — тоже в счётчике."""
    _wider_brief_at_decomposition(run)
    seen: list[flow.PaidCalls] = []
    original = flow._Ctx.__init__

    def spy(self, *a, **k):
        original(self, *a, **k)
        seen.append(self.calls)

    monkeypatch.setattr(flow._Ctx, "__init__", spy)
    assert _advance_with(run, FakeOps(run), _reviser(FIXED), checker("PASS")) == 0
    [calls] = seen
    # дизайн на диске (не автор), декомпозиция: автор 1, правка 1, проверки 2
    assert (calls.author, calls.revise, calls.check) == (1, 1, 2)


def test_finding_on_basis_file_names_file_and_action(run, monkeypatch) -> None:
    """Регрессия ревью #774: находка на основании (00-brief.md) — стоп,
    который называет файл, замечание и что делать; без «not_applicable» и
    без платного разбора."""

    def no_resolve(*a, **k):
        raise AssertionError("resolution must not be called")

    def fake(edge_id, bundle, subject, bases, **kw):
        on_brief = {**FINDING, "location": {"path": "00-brief.md", "lines": "3"}}
        return {
            "edge": edge_id,
            "verdict": "FAIL",
            "findings": [FINDING, on_brief],
            "subject": [],
        }

    monkeypatch.setattr(flow, "resolve_scope", no_resolve)
    assert _advance(run, FakeOps(run), fake) == 1
    reason = run.stop_reason
    assert run.step == "design" and "not_applicable" not in reason
    assert "00-brief.md:3" in reason and "R1 major" in reason and "плохо" in reason
    assert "dfl abandon" in reason and "20-design.md" in reason


# --- ручные проверки перед «конвейер закончен» (#771) ----------------------

MANUAL_DECOMP = """#### BEH-01: порог надёжности ≥ 9/10
- **checked_by**: `kind: manual` `target: docs/reliability.md`

#### DT-01: Надёжность · type: verify · owner: qa
scenarios: [BEH-01]
depends_on: []
parallel_group: solo
"""


class ManualOps(FakeOps):
    def caller_login(self) -> str:
        return "me"

    def find_issue(self, slug, prefix, **kw):
        self.calls.append(("find_issue",))
        return None

    def create_issue(self, slug, title, body, **kw):
        self.calls.append(("create_issue", title, kw))
        return 24

    def pr_comments_fact(self, slug, pr):
        from governance.facts import Fact, Outcome

        return Fact(Outcome.FOUND, [], "комментарии")

    def comment(self, slug, pr, body):
        self.calls.append(("comment", pr))


def _manual_run(run, *, manual: bool = True, merged: bool = False) -> ManualOps:
    bundle = _bundle(run)
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "30-decomposition.md").write_text(MANUAL_DECOMP, encoding="utf-8")
    run.manual_checks = manual
    run.step = "code-pr"
    run.code_pr = CODE_PR
    ops = ManualOps(run)
    ops.calls = []
    if merged:
        ops.code_state = "MERGED"
    return ops


def _go(run, ops, lines=(), tty: bool = True) -> int:
    it = iter(lines)
    return flow.advance(
        run,
        ops,
        read=lambda _p: next(it, None),
        agent=lambda p: "",
        launch=launch,
        check=checker("PASS"),
        tty=lambda: tty,
    )


def test_old_personal_run_merged_without_questions(run, capsys) -> None:
    ops = _manual_run(run, manual=False, merged=True)
    assert _go(run, ops, ["1"]) == 0
    out = capsys.readouterr().out
    assert f"integration-PR #{CODE_PR} вмержен — конвейер закончен\n" in out
    assert "Ручная проверка" not in out


def test_personal_asks_before_merge_line(run, capsys) -> None:
    ops = _manual_run(run)
    assert _go(run, ops, ["1"]) == 0
    out = capsys.readouterr().out
    assert out.index("Ручная проверка BEH-01 (TASK-001)") < out.index("смержите")
    assert run.manual["status"] == "completed"
    assert ("comment", CODE_PR) in ops.calls


def test_personal_no_tty_stops(run, capsys) -> None:
    ops = _manual_run(run)
    assert _go(run, ops, tty=False) == 0
    assert (run.stop_reason or "").startswith("ждёт ответа:")
    rec = pending.load(state.run_dir(run.run_id))
    assert rec is not None and rec["kind"] == pending.MANUAL
    assert rec["key"] == "BEH-01"
    assert run.step == "code-pr" and "смержите" not in capsys.readouterr().out


def test_personal_no_tty_reply_answers_next_advance(run) -> None:
    ops = _manual_run(run)
    assert _go(run, ops, tty=False) == 0
    rd = state.run_dir(run.run_id)
    pending.record(rd, pending.load(rd)["id"], "1")
    assert _go(run, ops, tty=False) == 0
    assert run.manual["answers"]["BEH-01"]["verdict"] == "done"
    assert pending.load(rd) is None


def test_personal_merged_line_has_summary(run, capsys) -> None:
    ops = _manual_run(run, merged=True)
    assert _go(run, ops, ["2", "5/10", ""]) == 0
    out = capsys.readouterr().out
    assert (
        f"integration-PR #{CODE_PR} вмержен — конвейер закончен; ручные "
        "проверки — не выполнено: 1 (BEH-01 → issue #24)"
    ) in out
    assert run.step == "done"


def test_personal_state_roundtrip_manual(run) -> None:
    run.manual_checks = True
    run.manual = {"status": "started", "checks": [], "answers": {}}
    state.save(run)
    loaded = state.load(run.run_id)
    assert loaded.manual_checks is True and loaded.manual == run.manual
    path = state.run_dir(run.run_id) / "run.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("manual_checks"), raw.pop("manual")
    path.write_text(json.dumps(raw), encoding="utf-8")
    old = state.load(run.run_id)
    assert old.manual_checks is False and old.manual == {}


def test_code_run_prints_progress_summary(run, capsys) -> None:
    """#765: итог реализации — в выводе личного шага."""
    from tests.test_governance_auto_tasks import _progress_tasks, _write_progress

    ops = FakeOps(run)
    run.step = "code-run"
    orig = ops.spec_runner

    def runner(target_dir, args):
        _write_progress(target_dir, run.ws_id)
        return orig(target_dir, args)

    ops.spec_runner = runner
    _progress_tasks(run.target_dir, run.ws_id)
    assert _advance(run, ops) == 0
    out = capsys.readouterr().out
    assert "✓ TASK-001 готова" in out and "реализация: сделано 1 из 1 · " in out


def test_foreign_findings_advice_names_the_basis_file() -> None:
    """Ревью #774: находка ребра декомпозиции на `20-design.md` — совет
    про дизайн, а не про бриф; бриф упоминается, только если он назван."""
    f = ej.Finding("30-decomposition.md", "e", "R1", "major", "x", "20-design.md", "3")
    text = flow._foreign_findings_reason("30-decomposition.md", [f])
    assert "20-design.md можно поправить вручную" in text
    assert "00-brief.md" not in text
    brief = ej.Finding(
        "30-decomposition.md", "e", "R2", "major", "y", "00-brief.md", "1"
    )
    text = flow._foreign_findings_reason("30-decomposition.md", [f, brief])
    assert "00-brief.md подтверждён в разговоре" in text
