"""Поведенческие тесты runner'а S0–S7: FakeOps + reconciliation (спека §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("steward")

from governance import bundle_state, merge_gate, runner
from governance import run_state as rs

GREEN_PR_FACTS: dict[str, Any] = {
    "statusCheckRollup": [{"conclusion": "SUCCESS"}],
    "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN",
    "isDraft": False,
    "headRefOid": "deadbeef",
    "baseRefName": "master",
    "state": "OPEN",
    "mergedAt": None,
}
BUNDLE_DIR = "workstreams/WS-1/spec"
GREEN_BUNDLE_FILES = [f"{BUNDLE_DIR}/15-behaviour-spec.md"]


@dataclass
class FakeOps:
    """Ops-сценарий для тестов runner'а: журнал вызовов + управляемый исход."""

    existing_branches: set[str] = field(default_factory=set)
    existing_prs: dict[str, int] = field(default_factory=dict)
    review_exit: int = 0
    facts: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    threads: bool | None = False
    merge_ok: bool = True
    head: str = "deadbeef"
    s8_exit: int = 0
    authored: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    merged: list[tuple[int, str]] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("ensure_branch", branch))
        self.existing_branches.add(branch)

    def head_sha(self, target_dir: str, branch: str) -> str:
        self.calls.append(("head_sha", branch))
        return self.head

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", branch))

    def find_pr(self, repo_slug: str, branch: str) -> int | None:
        self.calls.append(("find_pr", branch))
        return self.existing_prs.get(branch)

    def create_draft_pr(
        self, target_dir: str, repo_slug: str, branch: str, title: str,
        body: str, label: str,
    ) -> int:
        self.calls.append(("create_draft_pr", branch, label))
        number = 100 + len(self.existing_prs)
        self.existing_prs[branch] = number
        return number

    def mark_ready(self, repo_slug: str, pr: int) -> None:
        self.calls.append(("mark_ready", pr))

    def review(self, repo_name: str, pr: int) -> int:
        self.calls.append(("review", pr))
        return self.review_exit

    def pr_facts(self, repo_slug: str, pr: int) -> dict:
        self.calls.append(("pr_facts", pr))
        return self.facts

    def pr_files(self, repo_slug: str, pr: int) -> list[str]:
        self.calls.append(("pr_files", pr))
        return self.files

    def unresolved_threads(self, repo_slug: str, pr: int) -> bool | None:
        self.calls.append(("unresolved_threads", pr))
        return self.threads

    def merge(self, repo_slug: str, pr: int, sha: str) -> bool:
        self.calls.append(("merge", pr, sha))
        if self.merge_ok:
            self.merged.append((pr, sha))
        return self.merge_ok

    def comment(self, repo_slug: str, pr: int, body: str) -> None:
        self.calls.append(("comment", pr, body))
        self.comments.append(body)

    def author(
        self, target_dir: str, kind: str, subject: str, bundle_dir: str
    ) -> int:
        self.calls.append(("author", kind))
        self.authored.append(kind)
        filename = {
            "charter": "00-charter.md",
            "requirements": "10-requirements.md",
            "behaviour-spec": "15-behaviour-spec.md",
        }[kind]
        path = Path(target_dir) / bundle_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {kind}\n", encoding="utf-8")
        return 0

    def gate_check_s8(self, target_dir: str, bundle_dir: str, profile: str) -> int:
        self.calls.append(("gate_check_s8", bundle_dir))
        return self.s8_exit

    def create_issue(self, repo_slug: str, title: str, body: str) -> int:
        self.calls.append(("create_issue", repo_slug, title))
        self.issues.append((repo_slug, title, body))
        return 900 + len(self.issues)


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


def _start_kwargs(tmp_path: Path, run_id: str, ops: FakeOps, **overrides):
    target_dir = tmp_path / f"target-{run_id}"
    target_dir.mkdir(exist_ok=True)
    kwargs = dict(
        subject="тестовый функционал",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-1",
        target_dir=str(target_dir),
        bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml",
        run_id=run_id,
        ops=ops,
    )
    kwargs.update(overrides)
    return kwargs


def _green_bundle(profile, bundle) -> bundle_state.BundleState:
    return bundle_state.BundleState((), 0, None, (), ())


def test_happy_path_agent_merge(tmp_path: Path, runs_root, monkeypatch) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-happy", ops))

    assert state.ops["merge"]["status"] == "completed"
    assert ops.merged == [(state.pr, ops.head)]


def test_today_reality_waits_human(tmp_path: Path, runs_root, monkeypatch) -> None:
    """Без monkeypatch safety: реальная вендоренная копия — allowed=False."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-human", ops))

    assert "merge" not in state.ops
    assert ops.merged == []
    assert ops.comments
    assert state.status == "waiting_human_merge"


def test_review_request_changes_stops(tmp_path: Path, runs_root, monkeypatch) -> None:
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1)

    state = runner.start(**_start_kwargs(tmp_path, "r-review", ops))

    assert state.status == "stopped_review"
    assert ops.merged == []
    assert "verdict" not in state.ops


def test_resume_does_not_duplicate_pr(tmp_path: Path, runs_root) -> None:
    ops = FakeOps()
    kwargs = _start_kwargs(tmp_path, "r-resume", ops)
    branch = "spec/WS-1-behaviour"
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = branch
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "gate-candidate": {
            "status": "completed", "error_count": 0, "required_absent": [],
        },
        "push": {"status": "completed"},
        "pr": {"status": "started"},
    }
    rs.save(state)
    # Симулируем: PR реально создан до "гибели" прогона.
    ops.existing_prs[branch] = 42

    result = runner.advance(state, ops)

    assert "create_draft_pr" not in [c[0] for c in ops.calls]
    assert result.pr == 42
    assert result.ops["pr"] == {"status": "completed", "number": 42}


def test_gate_red_stops(tmp_path: Path, runs_root, monkeypatch) -> None:
    red_bundle = bundle_state.BundleState(
        (bundle_state.NodeState("charter", "draft", ("error GC-X: bad",)),),
        2, None, (), (),
    )
    monkeypatch.setattr(runner, "candidate_state", lambda profile, bundle: red_bundle)
    ops = FakeOps()

    state = runner.start(**_start_kwargs(tmp_path, "r-gate", ops))

    assert state.status == "stopped_gate"
    assert "pr" not in state.ops
    findings_file = rs.run_dir("r-gate") / "gate-findings.txt"
    assert findings_file.exists()
    assert "GC-X" in findings_file.read_text(encoding="utf-8")


def test_author_skips_existing_files(tmp_path: Path, runs_root, monkeypatch) -> None:
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    kwargs = _start_kwargs(tmp_path, "r-skip", ops)
    bundle_dir = Path(kwargs["target_dir"]) / kwargs["bundle_dir"]
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "00-charter.md").write_text("# charter\n", encoding="utf-8")
    (bundle_dir / "10-requirements.md").write_text(
        "# requirements\n", encoding="utf-8"
    )
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "# behaviour\n", encoding="utf-8"
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)

    state = runner.start(**kwargs)

    assert ops.authored == []
    assert state.ops["author-charter"]["skipped"] is True
    assert state.ops["author-requirements"]["skipped"] is True
    assert state.ops["author-behaviour"]["skipped"] is True


def test_facts_from_fail_closed() -> None:
    empty_rollup = runner.facts_from(
        {"statusCheckRollup": [], "mergeable": "UNKNOWN", "mergeStateStatus": "CLEAN"},
        [], None, BUNDLE_DIR,
    )
    assert empty_rollup.checks_rollup == "empty"
    assert empty_rollup.unresolved_threads is True

    outside_bundle = runner.facts_from(
        {
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        },
        ["src/main.py"], False, BUNDLE_DIR,
    )
    assert outside_bundle.diff_class == "code"


def _agent_merge_kwargs(tmp_path: Path, run_id: str, ops: FakeOps, **overrides):
    return _start_kwargs(tmp_path, run_id, ops, **overrides)


def test_s8_success_completes(tmp_path: Path, runs_root, monkeypatch) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-ok", ops))

    assert state.status == "completed"
    assert state.ops["gate-authoritative"] == {"status": "completed", "exit": 0}
    assert ops.issues == []


def test_s8_fail_marks_merged_unverified_and_opens_issue(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-fail"

    state = runner.start(**_agent_merge_kwargs(tmp_path, run_id, ops))

    assert state.status == "merged_unverified"
    # Op не помечается completed на провале — отличается от "прошёл".
    assert state.ops["gate-authoritative"]["status"] == "started"

    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert findings_file.exists()
    assert "1" in findings_file.read_text(encoding="utf-8")

    assert len(ops.issues) == 1
    repo_slug, _title, body = ops.issues[0]
    assert repo_slug == "owner/alpha"
    assert body.startswith("slug: beh-remediation-WS-1")
    assert f"from: devtools#{run_id}" in body

    with pytest.raises(ValueError):
        runner.advance(state, ops)
    with pytest.raises(ValueError):
        runner.resume(run_id, ops)


def test_verify_child_completes_parent_stays_merged_unverified(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены фикс-PR'ом в целевом репо
    child = runner.verify(parent_id, ops, "r-s8-child")

    assert child.status == "completed"
    assert child.remediated_by == parent_id
    assert child.ops["gate-authoritative"] == {"status": "completed", "exit": 0}

    reloaded_parent = rs.load(parent_id)
    assert reloaded_parent.status == "merged_unverified"


def test_resume_waiting_human_merge_open_still_waits(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Без monkeypatch safety: реальная вендоренная копия — waiting_human_merge."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=dict(GREEN_PR_FACTS), files=GREEN_BUNDLE_FILES)
    run_id = "r-resume-open"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "waiting_human_merge"

    result = runner.resume(run_id, ops)

    assert result.status == "waiting_human_merge"
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_resume_waiting_human_merge_merged_runs_s8(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=dict(GREEN_PR_FACTS), files=GREEN_BUNDLE_FILES, s8_exit=0,
    )
    run_id = "r-resume-merged"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "waiting_human_merge"
    assert "merge" not in state.ops

    ops.facts = {**ops.facts, "state": "MERGED"}
    result = runner.resume(run_id, ops)

    assert result.ops["merge"] == {"status": "completed", "merged": True}
    assert result.status == "completed"
    assert result.ops["gate-authoritative"]["status"] == "completed"


def test_cli_status_prints_run_state(
    tmp_path: Path, runs_root, capsys: pytest.CaptureFixture[str],
) -> None:
    """`python -m governance.runner status --run-id …` — argparse-проводка жива.

    Только `load()` из диска (fixture `runs_root` → `rs.RUNS_ROOT`), без Ops и
    без внешних вызовов — `status` не строит `RealOps`.
    """
    run_id = "r-cli-status"
    state = rs.new_run(
        subject="тестовый функционал", repo="alpha", repo_slug="owner/alpha",
        ws_id="WS-1", target_dir=str(tmp_path / "target-cli"),
        bundle_dir=BUNDLE_DIR, profile="profiles/team-exp.yaml", run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 7
    state.ops = {"branch": {"status": "completed"}}
    rs.save(state)

    exit_code = runner.main(["status", "--run-id", run_id])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"run_id:        {run_id}" in out
    assert "status:        running" in out
    assert "pr:            7" in out
    assert "branch: completed" in out
