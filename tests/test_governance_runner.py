"""Поведенческие тесты runner'а S0–S7: FakeOps + reconciliation (спека §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("steward")

from governance import bundle_state, merge_gate, runner
from governance import run_state as rs
from tests.governance_fixtures.bundles import make_bundle, make_profile

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
    s8_output: str = ""
    find_pr_error: str | None = None
    authored: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    merged: list[tuple[int, str]] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)
    committed: list[tuple[str, str]] = field(default_factory=list)
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
        if self.find_pr_error is not None:
            raise RuntimeError(self.find_pr_error)
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

    def gate_check_s8(
        self, target_dir: str, bundle_dir: str, profile: str
    ) -> tuple[int, str]:
        self.calls.append(("gate_check_s8", bundle_dir))
        return self.s8_exit, self.s8_output

    def create_issue(self, repo_slug: str, title: str, body: str) -> int:
        self.calls.append(("create_issue", repo_slug, title))
        self.issues.append((repo_slug, title, body))
        return 900 + len(self.issues)

    def find_issue(self, repo_slug: str, body_prefix: str) -> int | None:
        self.calls.append(("find_issue", body_prefix))
        for idx, (slug, _title, body) in enumerate(self.issues):
            if slug == repo_slug and body.startswith(body_prefix):
                return 900 + idx + 1
        return None

    def commit_all(self, target_dir: str, message: str) -> None:
        self.calls.append(("commit_all",))
        self.committed.append((target_dir, message))


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
    # Круг 3 (codex-ревью PR #88): gate-authoritative — аудит-запись, тоже
    # completed на провале (exit хранит исход); отличает «прошёл» от «нет»
    # exit, не сам статус op'а — run терминален в обоих случаях.
    assert state.ops["gate-authoritative"] == {"status": "completed", "exit": 1}
    assert state.ops["remediation-issue"] == {"status": "completed", "number": 901}

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


def test_resume_after_death_between_create_issue_and_op_complete_reuses_issue(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 3 (codex-ревью PR #88): `create_issue` не имел собственного
    write-ahead op'а — гибель между вызовом `create_issue` (эффект
    состоялся) и фиксацией результата дублировала issue на resume. Op
    `remediation-issue` + `find_issue`-реконсиляция: issue уже существует
    (найден по `slug:`-префиксу тела) → берётся его номер, второй не
    создаётся."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-issue-died"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)

    # Состояние сразу после дохлого прогона: gate-authoritative уже
    # зафиксирован неуспехом, findings уже на диске, remediation-issue —
    # started (write-ahead отработал), а сам issue РЕАЛЬНО создан (эффект
    # состоялся), но op_complete не успел записаться.
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "error_count": 0, "required_absent": [],
        },
        "push": {"status": "completed"},
        "pr": {"status": "completed", "number": 100},
        "ready": {"status": "completed"},
        "review": {"status": "completed", "exit": 0},
        "verdict": {"status": "completed", "decision": "agent", "reason": "ok"},
        "merge": {"status": "completed", "merged": True},
        "gate-authoritative": {"status": "completed", "exit": 1},
        "remediation-issue": {"status": "started"},
    }
    run_dir_path = rs.run_dir(run_id)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    body_prefix = f"slug: beh-remediation-{state.ws_id}"
    findings_text = "gate-check (S8, authoritative) завершился с кодом 1\n\nGC-X\n"
    (run_dir_path / "s8-findings.txt").write_text(findings_text, encoding="utf-8")
    rs.save(state)
    # Issue РЕАЛЬНО создан прошлым (дохлым) вызовом ops.create_issue.
    ops.issues.append((
        state.repo_slug,
        "beh-remediation: тестовый функционал (WS-1)",
        f"{body_prefix}\nfrom: devtools#{run_id}\n\n{findings_text}",
    ))

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert len(ops.issues) == 1  # второй не создан
    assert result.ops["remediation-issue"] == {"status": "completed", "number": 901}
    assert "create_issue" not in [c[0] for c in ops.calls]
    assert ("find_issue", body_prefix) in ops.calls


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
    assert len(ops.issues) == 1  # родитель открыл ровно одно remediation-issue

    ops.s8_exit = 0  # находки устранены фикс-PR'ом в целевом репо
    child = runner.verify(parent_id, ops, "r-s8-child")

    assert child.status == "completed"
    assert child.remediated_by == parent_id
    assert child.ops["gate-authoritative"] == {"status": "completed", "exit": 0}
    assert "remediation-issue" not in child.ops  # gate прошёл — issue не нужен
    assert len(ops.issues) == 1  # потомок не плодит второй issue

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


# --- F-1/M-1: resume из stopped_* — reconciliation, не no-op ---------------


def test_resume_from_stopped_gate_reruns_gate_candidate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_gate переигрывает S4, не остаётся no-op'ом."""
    calls_n = {"n": 0}
    red_bundle = bundle_state.BundleState(
        (bundle_state.NodeState("charter", "draft", ("error GC-X: bad",)),),
        1, None, (), (),
    )

    def _candidate(profile, bundle):
        calls_n["n"] += 1
        return red_bundle if calls_n["n"] == 1 else _green_bundle(profile, bundle)

    monkeypatch.setattr(runner, "candidate_state", _candidate)
    ops = FakeOps()
    run_id = "r-resume-gate"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_gate"

    result = runner.resume(run_id, ops)

    assert calls_n["n"] == 2  # S4 реально переигран, не пропущен
    assert result.status != "stopped_gate"
    assert result.ops["gate-candidate"]["status"] == "completed"


def test_resume_from_stopped_review_reruns_ready_and_review(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_review переигрывает ready+review."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1)
    run_id = "r-resume-review"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    calls_before = len(ops.calls)

    ops.review_exit = 0
    result = runner.resume(run_id, ops)

    review_calls_after = [c for c in ops.calls[calls_before:] if c[0] == "review"]
    assert review_calls_after  # review реально перезапустился
    assert result.status != "stopped_review"
    assert result.ops["review"]["status"] == "completed"


def test_resume_from_stopped_author_reruns_unfinished_author(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_author переигрывает незавершённый author-*."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    original_author = ops.author
    fail_once = {"on": True}

    def flaky_author(target_dir, kind, subject, bundle_dir):
        if kind == "requirements" and fail_once["on"]:
            fail_once["on"] = False
            ops.calls.append(("author", kind))
            return 1
        return original_author(target_dir, kind, subject, bundle_dir)

    ops.author = flaky_author  # type: ignore[method-assign]
    run_id = "r-resume-author"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_author"
    assert state.ops["author-charter"]["status"] == "completed"
    assert state.ops["author-requirements"]["status"] == "started"
    assert "author-behaviour" not in state.ops

    result = runner.resume(run_id, ops)

    assert result.ops["author-requirements"]["status"] == "completed"
    assert result.ops["author-behaviour"]["status"] == "completed"
    assert result.status != "stopped_author"


def test_verdict_refuse_status_is_distinct_from_stopped_gate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """M-1: S7 `refuse` получает свой статус, не путается с S4 stopped_gate —
    у них разные причины и разная починка."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )

    state = runner.start(**_start_kwargs(tmp_path, "r-refuse", ops))

    assert state.status == "stopped_merge_refused"
    assert state.status != "stopped_gate"


def test_resume_from_stopped_merge_refused_reverdicts(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-1: resume из stopped_merge_refused пересверяет вердикт, не no-op."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )
    run_id = "r-resume-refused"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_merge_refused"

    ops.facts = dict(GREEN_PR_FACTS)  # rollup зазеленел
    result = runner.resume(run_id, ops)

    assert result.status != "stopped_merge_refused"
    assert result.ops["merge"]["status"] == "completed"
    assert ops.merged == [(result.pr, ops.head)]


# --- F-2: verdict — аудит, не кэш решения -----------------------------------


def test_stale_cached_agent_verdict_does_not_merge_on_fresh_red_facts(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-2: op verdict уже completed=agent в run.json (write-ahead между
    _step_verdict и _step_merge), но свежий опрос PR даёт красный rollup —
    merge НЕ вызывается, decide() пересчитывается заново на этом заходе."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    ops = FakeOps(
        review_exit=0,
        facts={**GREEN_PR_FACTS, "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        files=GREEN_BUNDLE_FILES,
    )
    kwargs = _start_kwargs(tmp_path, "r-verdict-stale", ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "error_count": 0, "required_absent": [],
        },
        "push": {"status": "completed"},
        "pr": {"status": "completed", "number": 100},
        "ready": {"status": "completed"},
        "review": {"status": "completed", "exit": 0},
        # Кэшированный вердикт с прошлого захода — устарел за время простоя.
        "verdict": {"status": "completed", "decision": "agent", "reason": "stale"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert ops.merged == []
    assert result.ops.get("merge", {}).get("status") != "completed"
    assert result.status == "stopped_merge_refused"
    assert result.ops["verdict"]["decision"] == "refuse"


# --- F-5: find_pr транзиентный сбой -----------------------------------------


def test_pr_reconciliation_find_pr_failure_stops_without_duplicate(
    tmp_path: Path, runs_root,
) -> None:
    """F-5: `find_pr` поднимает RuntimeError на сбое gh — не читать как
    "PR нет", не открывать второй; op остаётся started, run продолжает ждать."""
    ops = FakeOps(find_pr_error="gh pr list: transient network error")
    kwargs = _start_kwargs(tmp_path, "r-pr-transient", ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=kwargs["run_id"],
    )
    state.branch = "spec/WS-1-behaviour"
    state.ops = {
        "branch": {"status": "completed"},
        "author-charter": {"status": "completed", "skipped": True},
        "author-requirements": {"status": "completed", "skipped": True},
        "author-behaviour": {"status": "completed", "skipped": True},
        "commit": {"status": "completed"},
        "gate-candidate": {
            "status": "completed", "error_count": 0, "required_absent": [],
        },
        "push": {"status": "completed"},
        "pr": {"status": "started"},
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert "create_draft_pr" not in [c[0] for c in ops.calls]
    assert result.ops["pr"] == {"status": "started"}
    assert result.status == "running"


# --- F-6: commit перед push --------------------------------------------------


def test_commit_all_called_between_author_and_push(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-6: `ops.commit_all` вызывается между авторингом и push."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-commit", ops))

    call_names = [c[0] for c in ops.calls]
    assert call_names.index("author") < call_names.index("commit_all")
    assert call_names.index("commit_all") < call_names.index("push_branch")
    assert state.ops["commit"]["status"] == "completed"
    assert len(ops.committed) == 1
    _, message = ops.committed[0]
    assert "Co-Authored-By" in message


# --- F-7: exit 4 переигрывает и prospective-гейт -----------------------------


def test_review_exit4_resets_gate_candidate_and_push_too(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-7: голова PR уехала (exit 4) — S4 обязан переиграться, не только S6."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=4)

    state = runner.start(**_start_kwargs(tmp_path, "r-review-moved", ops))

    assert state.status == "running"
    assert "gate-candidate" not in state.ops
    assert "push" not in state.ops
    assert "ready" not in state.ops
    assert "review" not in state.ops
    assert state.ops["pr"]["status"] == "completed"  # PR не переоткрывается


# --- F-4: шов runner ↔ bundle_state, без мока candidate_state ---------------


def test_gate_seam_required_absent_blocks_without_mock(
    tmp_path: Path, runs_root,
) -> None:
    """F-4: интеграционный тест шва — реальный `candidate_state`, не мок.

    Хороший бандл (оба обязательных узла присутствуют и валидны) проходит S4
    зелёным; бандл без единого frontmatter-узла (``required_absent``
    непустой, ``error_count == 0``) обязан остановить S4, а не пройти
    насквозь.
    """

    def _run_to_gate(run_id: str, build_bundle) -> rs.RunState:
        target_dir = tmp_path / run_id
        target_dir.mkdir()
        make_profile(target_dir)
        build_bundle(target_dir)
        state = rs.new_run(
            subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
            target_dir=str(target_dir), bundle_dir="spec",
            profile="profiles/mini.yaml", run_id=run_id,
        )
        state.branch = "spec/WS-1-behaviour"
        state.ops = {
            "branch": {"status": "completed"},
            "author-charter": {"status": "completed", "skipped": True},
            "author-requirements": {"status": "completed", "skipped": True},
            "author-behaviour": {"status": "completed", "skipped": True},
            "commit": {"status": "completed"},
        }
        rs.save(state)
        return runner.advance(state, FakeOps())

    good_result = _run_to_gate(
        "r-gate-good-seam", lambda d: make_bundle(d, behaviour_ok=True),
    )
    assert good_result.status != "stopped_gate"
    assert good_result.ops["gate-candidate"]["status"] == "completed"

    def _no_frontmatter(target_dir: Path) -> None:
        bundle = target_dir / "spec"
        bundle.mkdir()
        (bundle / "notes.md").write_text("без frontmatter\n", encoding="utf-8")

    red_result = _run_to_gate("r-gate-empty-seam", _no_frontmatter)

    assert red_result.status == "stopped_gate"
    findings_file = rs.run_dir("r-gate-empty-seam") / "gate-findings.txt"
    assert findings_file.exists()
    assert "GC-REQUIRED-ABSENT" in findings_file.read_text(encoding="utf-8")


# --- M-2: s8-findings.txt несёт вывод gate-check, не только код -------------


def test_s8_findings_include_gate_check_output(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        s8_exit=1, s8_output="error GC-BEH-TRACE: BEH-01 не трейсит ничего\n",
    )
    run_id = "r-s8-output"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "merged_unverified"
    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert "GC-BEH-TRACE" in findings_file.read_text(encoding="utf-8")
    _repo_slug, _title, body = ops.issues[0]
    assert "GC-BEH-TRACE" in body
