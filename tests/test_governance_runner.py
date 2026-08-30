"""Поведенческие тесты runner'а S0–S7: FakeOps + reconciliation (спека §4)."""

from __future__ import annotations

import os
import time
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
    dirty: bool = False
    checkout_and_pull_error: str | None = None
    head_sha_error: str | None = None
    authored: list[str] = field(default_factory=list)
    author_disp_calls: list[tuple[str, str]] = field(default_factory=list)
    author_disp_exit: int = 0
    comments: list[str] = field(default_factory=list)
    merged: list[tuple[int, str]] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)
    committed: list[tuple[str, list[str], str]] = field(default_factory=list)
    checked_out: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    def ensure_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("ensure_branch", branch))
        self.existing_branches.add(branch)

    def is_dirty(self, target_dir: str) -> bool:
        self.calls.append(("is_dirty",))
        return self.dirty

    def head_sha(self, target_dir: str, branch: str) -> str:
        self.calls.append(("head_sha", branch))
        if self.head_sha_error is not None:
            raise RuntimeError(self.head_sha_error)
        return self.head

    def push_branch(self, target_dir: str, branch: str) -> None:
        self.calls.append(("push_branch", branch))

    def checkout_and_pull(self, target_dir: str, branch: str) -> None:
        self.calls.append(("checkout_and_pull", branch))
        if self.checkout_and_pull_error is not None:
            raise RuntimeError(self.checkout_and_pull_error)
        self.checked_out.append((target_dir, branch))

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

    def author_disp(self, target_dir: str, task: str) -> int:
        self.calls.append(("author_disp", task))
        self.author_disp_calls.append((target_dir, task))
        return self.author_disp_exit

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

    def commit_paths(self, target_dir: str, paths: list[str], message: str) -> None:
        self.calls.append(("commit_paths", tuple(paths)))
        self.committed.append((target_dir, paths, message))


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
    # Круг 11 (codex-major): пустой files — не "все файлы про документацию"
    # (`all()` вакуумно истинно на пустом) — fail-closed на "code".
    assert empty_rollup.diff_class == "code"

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
    # exit, не сам статус op'а — run терминален в обоих случаях. `output`
    # (круг 8) — источник для s8-findings.txt на resume, файл производный.
    assert state.ops["gate-authoritative"] == {
        "status": "completed", "exit": 1, "output": "",
    }
    assert state.ops["remediation-issue"] == {"status": "completed", "number": 901}

    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert findings_file.exists()
    assert "1" in findings_file.read_text(encoding="utf-8")

    assert len(ops.issues) == 1
    repo_slug, _title, body = ops.issues[0]
    assert repo_slug == "owner/alpha"
    # Round 4: slug — от cycle_id (`remediated_by or run_id`), не от
    # ws_id; для родителя (remediated_by ещё None) cycle_id == его run_id.
    assert body.startswith(f"slug: beh-remediation-{run_id}")
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
    # Round 4: slug — от cycle_id (`remediated_by or run_id`); у этого
    # состояния remediated_by ещё None (родитель), cycle_id == run_id.
    body_prefix = f"slug: beh-remediation-{run_id}"
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


def test_s8_fail_does_not_reuse_issue_from_different_cycle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 4, codex-major: slug строится от `cycle_id` (`remediated_by or
    run_id`), не от `ws_id`. Старый открытый issue ДРУГОГО цикла того же
    `ws_id` (например прошлый цикл, уже зелёно верифицированный и закрытый,
    или просто параллельный независимый прогон по тому же `ws_id`) НЕ
    должен реконсилироваться на текущий провал — иначе свежие findings
    молча терялись бы под чужим issue. `find_issue` всё равно вызывается
    (реконсиляция остаётся безусловной, round 3), но не находит совпадение
    по своему префиксу -> `create_issue` создаёт НОВЫЙ, отдельный issue."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    run_id = "r-s8-independent-failure"
    # Issue от ДРУГОГО цикла того же ws_id (другой parent run_id) уже
    # открыт — например прошлый цикл, уже верифицированный и закрытый.
    other_cycle_prefix = "slug: beh-remediation-r-earlier-cycle"
    ops.issues.append((
        "owner/alpha",
        "beh-remediation: прошлый цикл (WS-1)",
        f"{other_cycle_prefix}\nfrom: devtools#r-earlier-cycle\n\nGC-OLD\n",
    ))

    state = runner.start(**_agent_merge_kwargs(tmp_path, run_id, ops))

    own_prefix = f"slug: beh-remediation-{run_id}"
    assert state.status == "merged_unverified"
    assert len(ops.issues) == 2  # чужой issue не переиспользован — создан новый
    new_repo_slug, _title, new_body = ops.issues[-1]
    assert new_repo_slug == "owner/alpha"
    assert new_body.startswith(own_prefix)
    assert state.ops["remediation-issue"] == {"status": "completed", "number": 902}
    assert ("find_issue", own_prefix) in ops.calls
    assert "create_issue" in [c[0] for c in ops.calls]


def test_verify_child_reuses_parent_remediation_issue_same_cycle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 4: `cycle_id = state.remediated_by or state.run_id` — у
    verify-потомка `remediated_by` указывает на родителя, поэтому
    `cycle_id` совпадает с собственным `run_id` родителя (у которого
    `remediated_by` ещё `None`). Потомок с ФРЕШ `remediation-issue`
    ("new", round 3: реконсиляция безусловна) должен найти и переиспользовать
    issue родителя, а не открыть второй под тем же циклом."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-cycle-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"
    assert len(ops.issues) == 1
    parent_issue_number = parent.ops["remediation-issue"]["number"]

    child = runner.verify(parent_id, ops, "r-s8-cycle-child")

    assert child.status == "merged_unverified"  # тоже проваливается
    assert child.remediated_by == parent_id
    assert len(ops.issues) == 1  # НЕ второй issue — тот же цикл
    assert child.ops["remediation-issue"] == {
        "status": "completed", "number": parent_issue_number,
    }


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


def test_verify_refuses_when_parent_already_has_green_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 3, codex-major: tmux-дедуп в консоли защищает только пока
    сессия жива (и после round 2 она ещё и самозакрывается) — `verify()`
    сам по себе не проверял, есть ли у родителя уже подтверждающий
    (`completed`) потомок. Второй `verify()`-вызов (руками, мимо консоли,
    или после того как сессия уже закрылась) на уже зелёном потомке
    создавал бы ЕЩЁ ОДИН verification-run поверх уже верифицированного
    родителя."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-already-verified"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent_id, ops, "r-s8-child-green")
    assert child.status == "completed"

    with pytest.raises(ValueError, match="уже верифицирован"):
        runner.verify(parent_id, ops, "r-s8-child-second")

    # Второй потомок не зарезервирован — отказ ДО _reserve_run_id.
    assert not (rs.run_dir("r-s8-child-second") / "run.json").exists()


def test_verify_allowed_again_after_failed_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Провальный (`merged_unverified`) потомок НЕ блокирует повторный
    `verify()` — только ЗЕЛЁНЫЙ (`completed`) значит «уже верифицирован»;
    цикл «verify → всё ещё красный → verify снова» остаётся штатным."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-retry"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    failed_child = runner.verify(parent_id, ops, "r-s8-child-failed")
    assert failed_child.status == "merged_unverified"  # тоже провалился

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops, "r-s8-child-retry-green")
    assert second_child.status == "completed"
    assert second_child.remediated_by == parent_id


def test_verify_without_run_id_serializes_when_ids_collide(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 5 (TOCTOU): сериализация конкурентных `verify()` без `run_id`
    держится на атомарном `_reserve_run_id` (`O_CREAT|O_EXCL`), а не на
    конкретном способе счёта `attempt` внутри `_next_verify_run_id` (тот
    менялся в round 6 — см. `test_next_verify_run_id_skips_dangling_
    reservation` ниже). Настоящую гонку потоков/процессов синхронный тест
    воспроизвести не может — форсируем через monkeypatch общий результат
    вычисления id для "обоих конкурентов" (то, что в реальной гонке дало
    бы им одно и то же значение из одного стартового снапшота каталогов).
    Первый вызов резервирует и создаёт потомка, второй с тем же
    вычисленным id получает `ValueError` вместо параллельного запуска S8
    в одном `target_dir`."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-race"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    monkeypatch.setattr(runner, "_next_verify_run_id", lambda pid: f"{pid}-v1")

    winner = runner.verify(parent_id, ops)
    assert winner.run_id == f"{parent_id}-v1"

    with pytest.raises(ValueError, match="уже существует"):
        runner.verify(parent_id, ops)  # "проигравший" вычисляет тот же id


def test_next_verify_run_id_skips_dangling_reservation(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 6, codex-major: гибель между `_reserve_run_id` и
    `save(child)` оставляет ПУСТОЙ `<parent>-v1/run.json` (сам
    `_reserve_run_id` уже создал файл через `touch`). Счёт `attempt` по
    успешно ЗАГРУЖЕННЫМ `RunState` (round 5) молча пропускал бы этот
    каталог — `_next_verify_run_id` вечно вычислял бы `v1` снова, а
    `_reserve_run_id` вечно отвечал бы «уже существует» на уже занятом
    (хоть и оборванном) слоте — постоянный deadlock на этом родителе.
    Счёт по ИМЕНАМ каталогов (`all_run_ids()`) видит `v1` независимо от
    валидности JSON внутри и корректно берёт следующий номер.

    Файл состарен (`os.utime`, round 7): свежий пустой `run.json` теперь
    трактуется `_active_verify_child` как «только что зарезервировано
    конкурентом» и блокирует `verify()` (см. `test_verify_refuses_when_
    dangling_reservation_is_fresh`) — этот тест про труп round 6, который
    старше грейс-периода, поэтому его нужно состарить явно."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-dangling"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Оборванная резервация: процесс умер между _reserve_run_id и
    # save(child) — каталог и пустой run.json есть, RunState — нет.
    # Состарена на -300с (> _ACTIVE_VERIFY_GRACE_SECONDS=120) — труп, не
    # свежий конкурент.
    dangling_id = f"{parent_id}-v1"
    rs.run_dir(dangling_id).mkdir(parents=True, exist_ok=True)
    dangling_json = rs.run_dir(dangling_id) / "run.json"
    dangling_json.touch()
    old_time = time.time() - 300
    os.utime(dangling_json, (old_time, old_time))

    assert runner._next_verify_run_id(parent_id) == f"{parent_id}-v2"
    assert runner._active_verify_child(parent_id) is None

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent_id, ops)
    assert child.run_id == f"{parent_id}-v2"
    assert child.status == "completed"


def test_verify_refuses_when_child_is_running(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 7, codex-major: round 6 переоткрыл гонку round 5 (второй
    конкурентный `verify()` видел уже занятый каталог и спокойно
    резервировал следующий номер вместо коллизии). Сериализация теперь
    держится на СОСТОЯНИИ потомков: валидный `run.json` со `status` не в
    `{"completed", "merged_unverified"}` (например `"running"` — S8 ещё
    не отработал) — активный потомок, второй `verify()` отказывает."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-active-child"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Валидный, но ещё не терминальный потомок (S8 в процессе).
    child_id = f"{parent_id}-v1"
    child_state = rs.new_run(
        subject=parent.subject, repo=parent.repo, repo_slug=parent.repo_slug,
        ws_id=parent.ws_id, target_dir=parent.target_dir,
        bundle_dir=parent.bundle_dir, profile=parent.profile, run_id=child_id,
    )
    child_state.remediated_by = parent_id
    child_state.status = "running"
    rs.save(child_state)

    assert runner._active_verify_child(parent_id) == child_id
    with pytest.raises(ValueError, match="verify уже идёт"):
        runner.verify(parent_id, ops)


def test_verify_refuses_when_dangling_reservation_is_fresh(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Round 7: свежий (только что созданный) пустой `<parent>-v1/run.json`
    трактуется как «конкурент только что зарезервировал слот, ещё пишет
    свой RunState» — активный, а не труп round 6. mtime моложе
    `_ACTIVE_VERIFY_GRACE_SECONDS` (тест не состаривает файл, в отличие от
    `test_next_verify_run_id_skips_dangling_reservation`)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-fresh-dangling"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    dangling_id = f"{parent_id}-v1"
    rs.run_dir(dangling_id).mkdir(parents=True, exist_ok=True)
    (rs.run_dir(dangling_id) / "run.json").touch()  # свежий -> "прямо сейчас"

    assert runner._active_verify_child(parent_id) == dangling_id
    with pytest.raises(ValueError, match="verify уже идёт"):
        runner.verify(parent_id, ops)


def test_active_verify_child_ignores_merged_unverified_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Провальный (`merged_unverified`) потомок — терминальный статус, НЕ
    активный: не блокирует повторный `verify()` (round 3/round 7 согласны
    друг с другом — только `_has_green_child` реагирует на `completed`,
    `_active_verify_child` реагирует на нетерминальные статусы)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-failed-not-active"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    failed_child = runner.verify(parent_id, ops)
    assert failed_child.status == "merged_unverified"  # тоже провалился

    assert runner._active_verify_child(parent_id) is None

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops)
    assert second_child.status == "completed"


def test_verify_without_run_id_increments_attempt_after_failed_child(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`attempt = 1 + число существующих потомков` (любой статус) — после
    провального (`merged_unverified`) потомка следующий `verify()` без
    `run_id` вычисляет НОВЫЙ id (`-v2`), а не повторяет `-v1` (что упёрлось
    бы в уже занятый `run_id` того же провального потомка)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-parent-attempts"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    first_child = runner.verify(parent_id, ops)
    assert first_child.run_id == f"{parent_id}-v1"
    assert first_child.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены вторым фикс-PR'ом
    second_child = runner.verify(parent_id, ops)
    assert second_child.run_id == f"{parent_id}-v2"
    assert second_child.status == "completed"


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


def test_cli_start_rejects_traversal_ws_id_before_default_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 12 (codex-major): CLI `start` без `--run-id` строит дефолтный
    `run_id` из `--ws-id` — грязный `ws_id` (`../`) обязан отказать ДО
    генерации/резервирования, а не протащить traversal в автосгенерированный
    `run_id`."""
    with pytest.raises(ValueError):
        runner.main([
            "start",
            "--subject", "s",
            "--repo", "alpha",
            "--repo-slug", "owner/alpha",
            "--ws-id", "../../escape",
            "--target-dir", str(tmp_path),
        ])
    assert not runs_root.exists()  # ничего не зарезервировано/создано


def test_start_rejects_explicit_traversal_run_id(tmp_path: Path, runs_root) -> None:
    """Круг 12: `start()` с явным `--run-id` вне разрешённого алфавита —
    отказ через `run_dir()` (единая точка валидации, `_reserve_run_id`)."""
    target_dir = tmp_path / "target-traversal"
    target_dir.mkdir()
    kwargs = dict(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
        target_dir=str(target_dir), bundle_dir=BUNDLE_DIR,
        profile="profiles/team-exp.yaml", run_id="../../outside",
        ops=FakeOps(),
    )
    with pytest.raises(ValueError):
        runner.start(**kwargs)


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


def test_resume_from_stopped_gate_recommits_edited_bundle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 9 (codex-ревью PR #88): resume из `stopped_gate` сбрасывает не
    только `gate-candidate`, но и `commit`+`push`+`ready`+`review` — человек
    мог поправить бандл в worktree между стопом и resume, и старый `commit`
    (уже `completed` с первого прохода — конвейер коммитит ДО гейта) не
    должен уехать в PR со СТАРЫМ, докоррекционным деревом."""
    calls_n = {"n": 0}
    red_bundle = bundle_state.BundleState(
        (bundle_state.NodeState("charter", "draft", ("error GC-X: bad",)),),
        1, None, (), (),
    )

    def _candidate(profile, bundle):
        calls_n["n"] += 1
        return red_bundle if calls_n["n"] == 1 else _green_bundle(profile, bundle)

    monkeypatch.setattr(runner, "candidate_state", _candidate)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-resume-gate-recommit"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_gate"
    # commit шёл ДО гейта в конвейере — на первом проходе он уже completed
    # со СТАРЫМ (красным по гейту) содержимым.
    assert state.ops["commit"]["status"] == "completed"
    calls_before_resume = len(ops.calls)

    # Человек правит бандл в worktree, устраняя находку гейта.
    bundle_dir = Path(state.target_dir) / state.bundle_dir
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "# behaviour (fixed)\n", encoding="utf-8"
    )

    result = runner.resume(run_id, ops)

    new_calls = [c[0] for c in ops.calls[calls_before_resume:]]
    assert "commit_paths" in new_calls  # новый коммит, не пропущен по кэшу
    assert "push_branch" in new_calls
    assert new_calls.index("commit_paths") < new_calls.index("push_branch")
    assert calls_n["n"] == 2  # гейт реально переигран на отредактированном бандле
    assert result.status != "stopped_gate"
    assert result.ops["gate-candidate"]["status"] == "completed"
    committed_paths = [paths for _t, paths, _m in ops.committed]
    assert committed_paths  # commit_paths реально вызван с путями бандла


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


def test_resume_from_stopped_review_recommits_edited_bundle(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 9 (codex-ревью PR #88): resume из `stopped_review` тоже
    сбрасывает `commit`+`gate-candidate`+`push`, не только `ready`+`review`
    — человек мог отработать находки ревью правкой бандла; старый коммит
    (уже `completed` с первого прохода) не должен уехать дальше со старым
    деревом."""
    calls_n = {"n": 0}

    def _candidate(profile, bundle):
        calls_n["n"] += 1
        return _green_bundle(profile, bundle)

    monkeypatch.setattr(runner, "candidate_state", _candidate)
    ops = FakeOps(review_exit=1)
    run_id = "r-resume-review-recommit"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert state.ops["commit"]["status"] == "completed"
    assert calls_n["n"] == 1  # candidate_state уже прогнан на первом проходе
    calls_before_resume = len(ops.calls)

    # Человек правит бандл в worktree, отрабатывая находки ревью.
    bundle_dir = Path(state.target_dir) / state.bundle_dir
    (bundle_dir / "15-behaviour-spec.md").write_text(
        "# behaviour (review fix)\n", encoding="utf-8"
    )
    ops.review_exit = 0
    result = runner.resume(run_id, ops)

    new_calls = [c[0] for c in ops.calls[calls_before_resume:]]
    assert "commit_paths" in new_calls  # новый коммит, не пропущен по кэшу
    assert "push_branch" in new_calls
    assert new_calls.index("commit_paths") < new_calls.index("push_branch")
    assert calls_n["n"] == 2  # gate-candidate реально переигран, не кэш
    assert result.status != "stopped_review"
    assert result.ops["review"]["status"] == "completed"
    assert result.ops["gate-candidate"]["status"] == "completed"


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


def test_commit_paths_called_between_author_and_push(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """F-6: `ops.commit_paths` вызывается между авторингом и push, только с
    `bundle_dir` (круг 5: не `git add -A`, явный список путей)."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-commit", ops))

    call_names = [c[0] for c in ops.calls]
    assert call_names.index("author") < call_names.index("commit_paths")
    assert call_names.index("commit_paths") < call_names.index("push_branch")
    assert state.ops["commit"]["status"] == "completed"
    assert len(ops.committed) == 1
    _target_dir, paths, message = ops.committed[0]
    assert paths == [BUNDLE_DIR]
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


# --- Круг 4: start/verify не перезаписывают занятый run_id -----------------


def test_start_with_existing_run_id_raises_and_does_not_overwrite(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Codex-ревью PR #88 (major): `start()` с занятым `run_id` молча
    перезаписывала `run.json` (`os.replace` — атомарно, но без проверки
    занятости) — уничтожение чужого леджера. Отказ ДО каких-либо эффектов;
    существующий файл не тронут ни байтом."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    run_id = "r-taken"
    kwargs = _start_kwargs(tmp_path, run_id, FakeOps())
    original = runner.start(**kwargs)
    assert original.status != "merged_unverified"  # леджер реально живёт
    before = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")

    other_ops = FakeOps()
    with pytest.raises(ValueError):
        runner.start(**_start_kwargs(tmp_path, run_id, other_ops))

    after = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")
    assert after == before  # ни байта не изменилось
    assert other_ops.calls == []  # отказ ДО каких-либо эффектов


def test_reserve_run_id_is_atomic_touch_not_exists_check(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 7 (codex-major): первая починка (F-1/круг 4) была `exists()`
    отдельно от записи — TOCTOU между двумя параллельными `start()`/
    `verify()` с одним `run_id`. Атомарное резервирование —
    `Path.touch(exist_ok=False)` (`O_CREAT|O_EXCL`, один системный вызов):
    второй вызов отказывает БЕЗ предварительного `load()`, даже когда
    зарезервированный файл ещё пуст и `load()` прочитать бы его не смог
    (`json.JSONDecodeError` на пустой строке)."""
    run_id = "r-reserve-atomic"
    runner._reserve_run_id(run_id)
    raw = rs.run_dir(run_id).joinpath("run.json").read_text(encoding="utf-8")
    assert raw == ""  # только резерв, ещё не настоящий run.json

    with pytest.raises(ValueError):
        runner._reserve_run_id(run_id)


def test_verify_with_existing_run_id_raises(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`verify()` — та же защита для дочернего run_id."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-verify-parent-taken"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"

    # Занятый child run_id — например, случайно совпал с чужим прогоном.
    # Другой ws_id, чтобы не наткнуться на WS-lock того же ws_id — здесь
    # проверяется отдельно занятость run_id, не WS-lock (круг 5).
    taken_child_id = "r-verify-child-taken"
    runner.start(
        **_start_kwargs(tmp_path, taken_child_id, FakeOps(), ws_id="WS-9")
    )
    before = rs.run_dir(taken_child_id).joinpath("run.json").read_text(
        encoding="utf-8"
    )

    with pytest.raises(ValueError):
        runner.verify(parent_id, ops, taken_child_id)

    after = rs.run_dir(taken_child_id).joinpath("run.json").read_text(
        encoding="utf-8"
    )
    assert after == before


# --- Круг 5, часть 1: S8 на default-ветке -----------------------------------


def test_s8_syncs_to_default_branch_before_gate_check(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """S8 чекаутит default-ветку (`base_ref` из `pr_facts.baseRefName`,
    зафиксированный на S7) и подтягивает merge-коммит ПЕРЕД `gate_check_s8` —
    иначе authoritative-срез читал бы feature-ветку прогона."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": "main"},
        files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-sync", ops))

    assert state.status == "completed"
    assert state.base_ref == "main"
    assert ops.checked_out == [(state.target_dir, "main")]
    call_names = [c[0] for c in ops.calls]
    assert call_names.index("checkout_and_pull") < call_names.index("gate_check_s8")
    assert state.ops["sync-default"]["status"] == "completed"


def test_s8_sync_falls_back_to_master_when_base_ref_missing(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": ""},
        files=GREEN_BUNDLE_FILES, s8_exit=0,
    )

    state = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-s8-sync-fallback", ops)
    )

    assert state.base_ref == "master"
    assert ops.checked_out == [(state.target_dir, "master")]


def test_s8_sync_failure_stops_without_touching_status_or_gate(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`checkout_and_pull` падает (например, локальные правки/дивергенция) —
    S8 останавливается ДО `gate_check_s8`, статус run'а не меняется (retry
    на следующем `advance()`/`resume()`, тот же паттерн, что `_step_pr`)."""
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
        checkout_and_pull_error="ff-only diverged",
    )

    state = runner.start(**_agent_merge_kwargs(tmp_path, "r-s8-sync-fail", ops))

    assert state.status == "running"
    assert state.ops["sync-default"]["status"] == "started"
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_verify_child_reuses_parent_base_ref(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runner, "load_safety",
        lambda actor="ai-prosto": merge_gate.Safety(True, "agent"),
    )
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        review_exit=0, facts={**GREEN_PR_FACTS, "baseRefName": "main"},
        files=GREEN_BUNDLE_FILES, s8_exit=1,
    )
    parent_id = "r-s8-sync-parent"
    parent = runner.start(**_agent_merge_kwargs(tmp_path, parent_id, ops))
    assert parent.status == "merged_unverified"
    assert parent.base_ref == "main"

    ops.s8_exit = 0
    calls_before_verify = len(ops.calls)
    child = runner.verify(parent_id, ops, "r-s8-sync-child")
    child_calls = ops.calls[calls_before_verify:]

    assert child.base_ref == "main"
    assert ("checkout_and_pull", "main") in child_calls


# --- Круг 5, часть 2: dirty-гард S1 -----------------------------------------


def test_dirty_target_dir_stops_before_branch_created(
    tmp_path: Path, runs_root,
) -> None:
    """Грязный `target_dir` ДО начала прогона → `stopped_dirty`, ничего не
    создано: `ensure_branch` не вызван, `commit_paths` тем более."""
    ops = FakeOps(dirty=True)

    state = runner.start(**_start_kwargs(tmp_path, "r-dirty", ops))

    assert state.status == "stopped_dirty"
    assert "branch" not in state.ops
    call_names = [c[0] for c in ops.calls]
    assert "ensure_branch" not in call_names
    assert "commit_paths" not in call_names


def test_resume_after_cleanup_from_stopped_dirty_proceeds(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Resume после ручной очистки — `branch` так и не стартовала, проверка
    просто повторяется и на этот раз проходит."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(
        dirty=True, review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES,
    )
    run_id = "r-dirty-resume"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_dirty"

    ops.dirty = False  # человек прибрался
    result = runner.resume(run_id, ops)

    assert result.status != "stopped_dirty"
    assert result.ops["branch"]["status"] == "completed"


# --- Круг 5, часть 3: WS-lock по merged_unverified --------------------------


def test_start_blocked_by_merged_unverified_without_green_child(
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
    blocked_ws = "WS-LOCK-1"
    parent = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-lock-parent", ops, ws_id=blocked_ws)
    )
    assert parent.status == "merged_unverified"

    blocked_run_id = "r-lock-blocked-attempt"
    with pytest.raises(ValueError, match="WS-LOCK-1"):
        runner.start(**_start_kwargs(
            tmp_path, blocked_run_id, FakeOps(), ws_id=blocked_ws,
        ))
    # WS-lock проверяется до резервирования run_id (круг 7) — отказ не
    # оставляет пустую run.json-заглушку под несостоявшимся прогоном.
    assert not (rs.run_dir(blocked_run_id) / "run.json").exists()


def test_start_unblocked_after_verify_child_completes(
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
    ws = "WS-LOCK-2"
    parent = runner.start(
        **_agent_merge_kwargs(tmp_path, "r-lock-parent2", ops, ws_id=ws)
    )
    assert parent.status == "merged_unverified"

    ops.s8_exit = 0  # находки устранены фикс-PR'ом
    child = runner.verify(parent.run_id, ops, "r-lock-child2")
    assert child.status == "completed"

    # Разблокировано зелёным потомком — новый прогон стартует без ValueError.
    unblocked = runner.start(**_start_kwargs(
        tmp_path, "r-lock-after-fix", FakeOps(), ws_id=ws,
    ))
    assert unblocked.run_id == "r-lock-after-fix"


def test_start_broken_neighbor_run_json_is_skipped(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Битый (не-JSON) `run.json` среди соседей не мешает обходу WS-lock."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    broken_dir = rs.run_dir("r-broken-neighbor")
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("not json at all", encoding="utf-8")

    state = runner.start(**_start_kwargs(tmp_path, "r-after-broken", FakeOps()))

    assert state.run_id == "r-after-broken"


# --- Круг 6: два окна S8 (codex-ревью PR #88) -------------------------------


def _s8_preset_ops(**gate_authoritative: object) -> dict:
    """Общий журнал операций до захода в S8 — только gate-authoritative
    (и опционально sync-default) варьируется между тестами круга 6."""
    ops: dict = {
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
    }
    if gate_authoritative:
        ops["gate-authoritative"] = gate_authoritative
    return ops


def test_resume_completes_when_gate_authoritative_done_but_status_stuck_running(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 1 (успех): op `gate-authoritative` уже `completed(exit=0)` на
    диске, но `status` ещё `"running"` (гибель между `op_complete` и
    финальным `save`) — resume обязан довести до `completed`, не оставлять
    run вечно `running`."""
    ops = FakeOps()
    run_id = "r-s8-stuck-ok"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(status="completed", exit=0),
        "sync-default": {"status": "completed"},
    }
    rs.save(state)
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_resume_completes_fail_path_when_status_stuck_running_after_gate_fail(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 1 (провал): op `gate-authoritative` уже `completed(exit=1)` на
    диске, но `status` ещё `"running"` и `remediation-issue` ещё не начат —
    resume обязан довести fail-путь до конца: завести issue, выставить
    `merged_unverified`, не выйти на полпути."""
    ops = FakeOps()
    run_id = "r-s8-stuck-fail"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(status="completed", exit=1),
        "sync-default": {"status": "completed"},
    }
    run_dir_path = rs.run_dir(run_id)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    (run_dir_path / "s8-findings.txt").write_text(
        "gate-check (S8, authoritative) завершился с кодом 1\n\nGC-X\n",
        encoding="utf-8",
    )
    rs.save(state)
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert len(ops.issues) == 1
    assert result.ops["remediation-issue"]["status"] == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


def test_sync_default_always_rechecked_even_if_already_completed(
    tmp_path: Path, runs_root,
) -> None:
    """Окно 2: `sync-default` уже `completed` в журнале (из прошлой
    попытки), но `gate-authoritative` ещё не заведён — гибель случилась
    между sync'ом и самим `gate_check_s8`. Resume обязан перезапустить
    `checkout_and_pull`, а не пропустить его по журналу — default-ветка
    могла уехать дальше за то время, что прогон простоял."""
    ops = FakeOps(s8_exit=0)
    run_id = "r-s8-resync"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "main"
    state.ops = {
        **_s8_preset_ops(),
        "sync-default": {"status": "completed"},  # из ПРОШЛОЙ попытки
    }
    rs.save(state)

    result = runner.advance(state, ops)

    assert ("checkout_and_pull", "main") in ops.calls
    assert result.status == "completed"
    assert result.ops["gate-authoritative"] == {"status": "completed", "exit": 0}


# --- Круг 8: s8-findings.txt производный от журнала, не источник истины ----


def test_resume_rebuilds_missing_s8_findings_from_op_output(
    tmp_path: Path, runs_root,
) -> None:
    """Круг 8 (codex-ревью PR #88): op `gate-authoritative` уже
    `completed(exit=1, output=...)` на диске, но `s8-findings.txt` НЕТ
    (гибель между `op_complete` и `write_text`) и статус ещё `"running"` —
    resume обязан довести до `merged_unverified`, восстановив findings-файл
    из журнала (не упасть на `read_text()` c `FileNotFoundError`), и
    завести remediation-issue."""
    ops = FakeOps()
    run_id = "r-s8-findings-missing"
    kwargs = _agent_merge_kwargs(tmp_path, run_id, ops)
    state = rs.new_run(
        subject=kwargs["subject"], repo=kwargs["repo"],
        repo_slug=kwargs["repo_slug"], ws_id=kwargs["ws_id"],
        target_dir=kwargs["target_dir"], bundle_dir=kwargs["bundle_dir"],
        profile=kwargs["profile"], run_id=run_id,
    )
    state.branch = "spec/WS-1-behaviour"
    state.pr = 100
    state.head = "deadbeef"
    state.base_ref = "master"
    state.ops = {
        **_s8_preset_ops(
            status="completed", exit=1, output="error GC-X: bad\n",
        ),
        "sync-default": {"status": "completed"},
    }
    rs.save(state)
    findings_file = rs.run_dir(run_id) / "s8-findings.txt"
    assert not findings_file.exists()  # окно круга 8: файл не успел записаться
    assert state.status == "running"

    result = runner.advance(state, ops)

    assert result.status == "merged_unverified"
    assert findings_file.exists()
    restored = findings_file.read_text(encoding="utf-8")
    assert "1" in restored
    assert "GC-X" in restored
    assert len(ops.issues) == 1
    _repo_slug, _title, body = ops.issues[0]
    assert "GC-X" in body
    assert result.ops["remediation-issue"]["status"] == "completed"
    assert "checkout_and_pull" not in [c[0] for c in ops.calls]
    assert "gate_check_s8" not in [c[0] for c in ops.calls]


# --- Круг 10: статус фиксируется до стоп-комментария -----------------------


def test_stop_with_comment_saves_status_before_commenting(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 10 (codex-major): статус сохраняется на диске ДО best-effort
    комментария во всех стоп-с-комментарием путях (S6 exit 1/2/3, S7
    human/refuse, merge False) — иначе гибель между `ops.comment` и
    `save()` оставляла run в `"running"`, и следующий `advance()` переигрывал
    этот же шаг с нуля, включая повторный (дублирующий) комментарий.
    Проверка через "шпиона": `ops.comment`, вызванный, читает `run.json` с
    диска в момент своего вызова — статус там уже обязан быть терминальным."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1)
    run_id = "r-comment-order"
    seen_status_at_comment_time: dict[str, str] = {}
    original_comment = ops.comment

    def spying_comment(repo_slug: str, pr: int, body: str) -> None:
        seen_status_at_comment_time["status"] = rs.load(run_id).status
        return original_comment(repo_slug, pr, body)

    ops.comment = spying_comment  # type: ignore[method-assign]

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert seen_status_at_comment_time["status"] == "stopped_review"


def test_resume_from_stopped_review_does_not_repost_comment_when_fixed(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Круг 10: resume из уже сохранённого `stopped_review`, когда причина
    устранена (review теперь проходит) — второго комментария нет.
    Комментарий — часть самого стоп-пути (`_stop_with_comment` в
    `_step_review`), не безусловный побочный эффект `resume()` — он
    срабатывает, только если review реально проваливается СНОВА."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1)
    run_id = "r-comment-no-repost"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))
    assert state.status == "stopped_review"
    assert len(ops.comments) == 1

    ops.review_exit = 0  # находки устранены
    result = runner.resume(run_id, ops)

    # Пайплайн продолжается дальше и мог легитимно оставить свой комментарий
    # (например S7 human/refuse без мока безопасности) — важно, что комментарий
    # ИМЕННО про этот стоп на review не задублирован.
    assert result.status != "stopped_review"
    review_stop_comments = [c for c in ops.comments if "ревью нашло находки" in c]
    assert len(review_stop_comments) == 1


# --- B2 Task 1: follow-ups приёмки B1 ---------------------------------------


def test_start_rejects_invalid_merge_authority_before_reserving_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Minor из приёмки #88: невалидный `merge_authority` валидируется ДО
    `_reserve_run_id` — раньше он навсегда резервировал `run_id` пустым
    `run.json`, потому что единственная валидация жила в `new_run()`,
    вызываемом ПОСЛЕ резервирования."""
    run_id = "r-bad-authority"
    kwargs = _start_kwargs(
        tmp_path, run_id, FakeOps(), merge_authority="agent",
    )

    with pytest.raises(ValueError):
        runner.start(**kwargs)

    assert not (rs.run_dir(run_id) / "run.json").exists()


def test_start_rejects_invalid_author_backend_before_reserving_run_id(
    tmp_path: Path, runs_root,
) -> None:
    """Тот же класс minor, что и merge_authority выше (I-3, финальное
    ревью): Task 1 чинил его для merge_authority (приёмка #88), Task 2
    внесла заново для author_backend — `validate_author_backend` жила
    только внутри `new_run()`, вызываемом ПОСЛЕ `_reserve_run_id`. Через
    CLI недостижимо (`choices=["codex", "disp"]`), но `start()` —
    публичный API."""
    run_id = "r-bad-author-backend"
    kwargs = _start_kwargs(
        tmp_path, run_id, FakeOps(), author_backend="claude",
    )

    with pytest.raises(ValueError):
        runner.start(**kwargs)

    assert not (rs.run_dir(run_id) / "run.json").exists()


def test_stop_review_comment_includes_evidence_hint(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Спека §7: стоп-комментарий S6 (exit 1) дополняется evidence-подсказкой
    про известный ложный класс находок «файлов нет» — `git cat-file -e
    <head>:<путь>` с реальной подставленной головой."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1)
    run_id = "r-review-evidence"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert ops.comments
    assert f"git cat-file -e {ops.head}" in ops.comments[-1]


def test_stop_review_comment_survives_head_sha_failure(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """I-6, финальное ревью: `head_sha` — чисто косметическая evidence-
    подсказка, вызывается на СТОП-пути ДО `_stop_with_comment`. Если ветки
    нет локально/`target_dir` уехал (`RealOps.head_sha` зовёт `git
    rev-parse` с `check=True`), штатная остановка «ревью нашло находки» не
    должна превращаться в необработанное исключение вместо
    comment+`stopped_review` — сбой глотается, в подсказку идёт литерал
    `<head>`."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=1, head_sha_error="fatal: bad revision")
    run_id = "r-review-evidence-head-fails"

    state = runner.start(**_start_kwargs(tmp_path, run_id, ops))

    assert state.status == "stopped_review"
    assert ops.comments
    assert "git cat-file -e <head>" in ops.comments[-1]


# --- B2 Task 2: авторинг-бэкенд codex|disp ----------------------------------


def test_default_author_backend_is_codex_author_disp_not_called(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """Дефолт `author_backend="codex"` не меняет поведение B1: все три узла
    идут через `ops.author`, `ops.author_disp` не вызывается вовсе."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)

    state = runner.start(**_start_kwargs(tmp_path, "r-disp-default", ops))

    assert ops.authored == ["charter", "requirements", "behaviour-spec"]
    assert ops.author_disp_calls == []
    assert state.author_backend == "codex"


def test_disp_backend_used_only_for_behaviour_node(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """`author_backend="disp"` переключает ТОЛЬКО behaviour-spec узел на
    `ops.author_disp`; charter/requirements остаются на `ops.author` (codex)
    — disp-цикл осмыслен только для полируемого документа."""
    monkeypatch.setattr(runner, "candidate_state", _green_bundle)
    ops = FakeOps(review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    run_id = "r-disp-behaviour"

    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))

    assert ops.authored == ["charter", "requirements"]
    assert len(ops.author_disp_calls) == 1
    target_dir, task = ops.author_disp_calls[0]
    assert target_dir == str(tmp_path / f"target-{run_id}")
    assert "#### BEH-NN" in task
    assert "traces:" in task
    assert "checked_by" in task
    assert state.ops["author-behaviour"]["status"] == "completed"
    assert state.author_backend == "disp"


def test_disp_backend_author_disp_failure_stops_author(
    tmp_path: Path, runs_root,
) -> None:
    """Провал `author_disp` (rc != 0) останавливает прогон так же, как
    провал `ops.author` — `stopped_author`, статус не подменяется бэкендом."""
    ops = FakeOps(author_disp_exit=1)
    run_id = "r-disp-fail"

    state = runner.start(**_start_kwargs(
        tmp_path, run_id, ops, author_backend="disp",
    ))

    assert state.status == "stopped_author"
    assert state.ops["author-behaviour"]["status"] == "started"


def test_new_run_rejects_unknown_author_backend() -> None:
    with pytest.raises(ValueError):
        rs.new_run(
            subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="WS-1",
            target_dir="/tmp/x", bundle_dir="spec",
            profile="profiles/team-exp.yaml", run_id="r-bad-backend",
            author_backend="claude",
        )
