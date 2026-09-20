"""Тесты операторской кнопки `make spec-loop` (governance.spec_loop).

Кнопка — тонкая маршрутизация над runner/task_bridge по состоянию
`run.json`: ни одного платного вызова здесь нет — runner.start/resume и
deliver_for_run подменяются, проверяется ТОЛЬКО деривация значений,
поиск прогона и выбор действия (fail-closed на любой неоднозначности).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from governance import run_state as rs
from governance import spec_loop
from governance import brief_input
from governance import interview as iv


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


MANIFEST = """\
schema_version = "0.3.0"

[cores.alpha]
repo_url = "git@github.com:owner/alpha.git"
git_dir  = "alpha"

[cores.beta]
repo_url = "https://github.com/owner/beta.git"
git_dir  = "beta"

[tools.beta-dist]
repo_url = "https://github.com/owner/beta.git"
git_dir  = "beta"

[cores.gamma-a]
repo_url = "git@github.com:owner/gamma-one.git"
git_dir  = "gamma"

[tools.gamma-b]
repo_url = "git@github.com:owner/gamma-two.git"
git_dir  = "gamma"
"""


def _customer_brief(*, status: str = "draft", validation: str = "pass") -> str:
    return f"""\
---
spec_stage: discovery
status: {status}
version: 1
generated_by: discovery-agent@test
generated_at: 2026-09-13
validation: {validation}
owner_role: product
schema: discovery-brief
schema_version: 1
feeds: [charter, requirements]
interview:
  frame: customer
  sessions:
    - participant_role: product-owner
coverage:
  goals: covered
  personas: covered
  jobs: covered
  functions: covered
  nfr: covered
  constraints: covered
  success_metrics: covered
  out_of_scope: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: []
---

- **G-01** Goal
- **P-01** Persona
- **J-01** `traces: [G-01]` Job
#### FR-01: Feature `traces: [G-01, J-01]`
**Priority**: Must
**Acceptance**: works
#### NFR-01: Safety `traces: [CON-01]`
**Target**: zero writes
- **CON-01** Constraint
- **M-01** `traces: [G-01]` Metric
- **OUT-01** Not in scope
"""


def _engineer_brief(ref: str = "customer.md") -> str:
    return f"""\
---
spec_stage: discovery
status: draft
version: 1
generated_by: discovery-agent@test
generated_at: 2026-09-13
validation: pass
owner_role: architect
schema: discovery-brief
schema_version: 1
feeds: [system-assessment, tech-selection]
interview:
  frame: engineer
  sessions:
    - participant_role: platform-engineer
coverage:
  systems: covered
  interfaces: covered
  constraints: covered
  arch_preferences: covered
  risks: covered
  feasibility_review: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: [{ref}]
---

- **S-01** System
- **IF-01** `traces: [S-01]` Interface
- **CON-01** Constraint
- **AP-01** `traces: [S-01, CON-01]` Preference
- **RK-01** Risk
## Feasibility
- FR-01 is feasible.
"""


# --- деривации -------------------------------------------------------------


def test_slug_from_subject_ascii() -> None:
    assert spec_loop.slug_from_subject("Fleet Inbox: v2 (MVP)") == "fleet-inbox-v2-mvp"


def test_slug_from_subject_empty_fails_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="--ws-id"):
        spec_loop.slug_from_subject("Только кириллица")


def test_ws_id_carries_date_only_at_creation() -> None:
    assert (
        spec_loop.ws_id_for("Fleet Inbox", date(2026, 9, 7))
        == "fleet-inbox-20260907"
    )


def test_repo_slug_from_url_forms() -> None:
    assert spec_loop.repo_slug_from_url("git@github.com:o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("https://github.com/o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("ssh://git@github.com/o/n") == "o/n"


def test_manifest_single_match() -> None:
    entry = spec_loop.manifest_repo_entry(MANIFEST, "alpha")
    assert entry.repo == "alpha"
    assert entry.repo_slug == "owner/alpha"


def test_manifest_shared_git_dir_same_url_is_one_repo() -> None:
    entry = spec_loop.manifest_repo_entry(MANIFEST, "beta")
    assert entry.repo_slug == "owner/beta"


def test_manifest_zero_matches_fails_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="не найден"):
        spec_loop.manifest_repo_entry(MANIFEST, "nope")


def test_manifest_conflicting_urls_fail_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="gamma"):
        spec_loop.manifest_repo_entry(MANIFEST, "gamma")


# --- поиск прогона ---------------------------------------------------------


def _mk_run(run_id: str, subject: str, repo: str = "alpha", **kw) -> rs.RunState:
    state = rs.new_run(
        subject=subject,
        repo=repo,
        repo_slug="owner/alpha",
        ws_id=kw.get("ws_id", f"{run_id}-ws"),
        target_dir=kw.get("target_dir", "/tmp/alpha"),
        bundle_dir="workstreams/x/spec",
        profile="profiles/team-exp.yaml",
        run_id=run_id,
    )
    state.status = kw.get("status", "running")
    rs.save(state)
    return state


def test_find_runs_exact_repo_subject(runs_root) -> None:
    _mk_run("r1", "Subject A")
    _mk_run("r2", "Subject B")
    _mk_run("r3", "Subject A", repo="beta")
    found = spec_loop.find_runs("alpha", "Subject A")
    assert [s.run_id for s in found] == ["r1"]


def test_find_runs_broken_ledger_fails_closed(runs_root) -> None:
    _mk_run("r1", "Subject A")
    bad = runs_root / "r-broken"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(spec_loop.SpecLoopError, match="r-broken"):
        spec_loop.find_runs("alpha", "Subject A")


class _RecoveryOps:
    def __init__(
        self, prs, *, state="MERGED", files=None, head_files=None
    ):
        self.prs = prs
        self.state = state
        self.files = files or [
            f"workstreams/fleet-inbox-20260901/spec/{name}"
            for name in sorted(spec_loop._BUNDLE_FILENAMES)
        ]
        self.prefixes: list[str] = []
        self.checkouts: list[tuple[str, str]] = []
        self.head_files = head_files or {}

    def prs_by_head_prefix(self, repo_slug, branch_prefix):
        self.prefixes.append(branch_prefix)
        return self.prs

    def pr_facts(self, repo_slug, pr):
        return {
            "state": self.state,
            "baseRefName": "master",
            "headRefOid": "a" * 40,
        }

    def pr_files(self, repo_slug, pr):
        return self.files

    def checkout_and_pull(self, target_dir, branch):
        self.checkouts.append((target_dir, branch))

    def show_repo_file_bytes(self, repo_slug, ref, path):
        return self.head_files.get(path)


def _bundle_pr(
    number=41,
    *,
    branch="spec/fleet-inbox-20260901-behaviour",
    subject="Fleet Inbox",
    run_id="fleet-inbox-20260901-a1b2c3",
):
    ws_id = branch.removeprefix("spec/").removesuffix("-behaviour")
    return {
        "number": number,
        "head": {"ref": branch},
        "title": f"{subject} — behaviour bundle {ws_id}",
        "body": f"Автоматический прогон governance runner'а ({run_id}).",
    }


def test_recover_run_from_github_rebuilds_minimal_merge_boundary(
    runs_root, tmp_path
) -> None:
    ops = _RecoveryOps([_bundle_pr()])

    state = spec_loop.recover_run_from_github(
        subject="Fleet Inbox",
        repo="alpha",
        repo_slug="owner/alpha",
        target_dir=str(tmp_path / "alpha"),
        profile="profiles/team-exp.yaml",
        author_backend="codex",
        requested_ws_id=None,
        requested_bundle_dir=None,
        ops=ops,
    )

    assert state is not None
    assert state.run_id == "fleet-inbox-20260901-a1b2c3"
    assert state.ws_id == "fleet-inbox-20260901"
    assert state.bundle_dir == "workstreams/fleet-inbox-20260901/spec"
    assert state.status == "waiting_human_merge"
    assert state.branch == "spec/fleet-inbox-20260901-behaviour"
    assert state.pr == 41
    assert state.base_ref == "master"
    assert state.ops == {
        "ledger-recovery": {
            "status": "completed",
            "source": "github",
            "bundle_pr": 41,
            "profile": "profiles/team-exp.yaml",
            "profile_source": "current-invocation-not-github",
            "author_backend": "codex",
            "author_backend_effective": False,
        }
    }
    assert rs.load(state.run_id) == state
    assert ops.prefixes == ["spec/fleet-inbox-"]
    assert ops.checkouts == []


def test_recover_customer_source_descriptor_from_merged_bundle(
    runs_root, tmp_path
) -> None:
    bundle = "workstreams/fleet-inbox-20260901/spec"
    target = tmp_path / "alpha"
    primary = target / bundle / brief_input.PRIMARY_REL
    primary.parent.mkdir(parents=True)
    primary.write_text(_customer_brief(), encoding="utf-8")
    files = [f"{bundle}/{name}" for name in spec_loop._BUNDLE_FILENAMES]
    source_path = f"{bundle}/{brief_input.PRIMARY_REL}"
    files.append(source_path)
    ops = _RecoveryOps(
        [_bundle_pr()],
        files=files,
        head_files={
            f"{bundle}/{brief_input.PRIMARY_REL}": primary.read_bytes()
        },
    )

    state = spec_loop.recover_run_from_github(
        subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
        target_dir=str(target), profile="profiles/team-exp.yaml",
        author_backend="codex", requested_ws_id=None,
        requested_bundle_dir=None, ops=ops,
    )

    assert state is not None
    assert state.brief == brief_input.inspect_brief(primary).as_state()
    assert ops.checkouts == [(str(target), "master")]


def test_recover_refuses_source_changed_after_bundle_pr(
    runs_root, tmp_path
) -> None:
    bundle = "workstreams/fleet-inbox-20260901/spec"
    target = tmp_path / "alpha"
    primary = target / bundle / brief_input.PRIMARY_REL
    primary.parent.mkdir(parents=True)
    original = _customer_brief().encode("utf-8")
    primary.write_text(
        _customer_brief().replace("Goal", "Changed goal"), encoding="utf-8"
    )
    source_path = f"{bundle}/{brief_input.PRIMARY_REL}"
    files = [f"{bundle}/{name}" for name in spec_loop._BUNDLE_FILENAMES]
    files.append(source_path)

    with pytest.raises(spec_loop.SpecLoopError, match="изменён"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
            target_dir=str(target), profile="profiles/team-exp.yaml",
            author_backend="codex", requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps(
                [_bundle_pr()], files=files,
                head_files={source_path: original},
            ),
        )


def test_recover_missing_head_source_is_contractual_refusal(
    runs_root, tmp_path
) -> None:
    bundle = "workstreams/fleet-inbox-20260901/spec"
    target = tmp_path / "alpha"
    primary = target / bundle / brief_input.PRIMARY_REL
    primary.parent.mkdir(parents=True)
    primary.write_text(_customer_brief(), encoding="utf-8")
    source_path = f"{bundle}/{brief_input.PRIMARY_REL}"
    files = [f"{bundle}/{name}" for name in spec_loop._BUNDLE_FILENAMES]
    files.append(source_path)

    with pytest.raises(spec_loop.SpecLoopError, match="immutable head"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
            target_dir=str(target), profile="profiles/team-exp.yaml",
            author_backend="codex", requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps([_bundle_pr()], files=files),
        )


def test_recover_engineer_refuses_incomplete_source_layer(
    runs_root, tmp_path
) -> None:
    bundle = "workstreams/fleet-inbox-20260901/spec"
    target = tmp_path / "alpha"
    primary = target / bundle / brief_input.PRIMARY_REL
    primary.parent.mkdir(parents=True)
    primary.write_text(_engineer_brief(), encoding="utf-8")
    customer = target / bundle / "00-discovery/customer.md"
    customer.write_text(_customer_brief(status="approved"), encoding="utf-8")
    files = [f"{bundle}/{name}" for name in spec_loop._BUNDLE_FILENAMES]
    source_path = f"{bundle}/{brief_input.PRIMARY_REL}"
    files.append(source_path)

    with pytest.raises(spec_loop.SpecLoopError, match="не восстанавливается"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
            target_dir=str(target), profile="profiles/team-exp.yaml",
            author_backend="codex", requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps(
                [_bundle_pr()], files=files,
                head_files={source_path: primary.read_bytes()},
            ),
        )


def test_recover_multiple_candidates_requires_ws_id(
    runs_root, tmp_path
) -> None:
    ops = _RecoveryOps(
        [
            _bundle_pr(),
            _bundle_pr(
                42,
                branch="spec/fleet-inbox-20260902-behaviour",
                run_id="fleet-inbox-20260902-d4e5f6",
            ),
        ]
    )

    with pytest.raises(spec_loop.SpecLoopError, match="--ws-id"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox",
            repo="alpha",
            repo_slug="owner/alpha",
            target_dir=str(tmp_path / "alpha"),
            profile="profiles/team-exp.yaml",
            author_backend="codex",
            requested_ws_id=None,
            requested_bundle_dir=None,
            ops=ops,
        )
    assert rs.all_run_ids() == []


def test_recover_duplicate_prs_on_same_branch_does_not_suggest_ws_id(
    runs_root, tmp_path
) -> None:
    ops = _RecoveryOps(
        [
            _bundle_pr(41),
            _bundle_pr(42, run_id="fleet-inbox-20260901-d4e5f6"),
        ]
    )

    with pytest.raises(spec_loop.SpecLoopError) as caught:
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox",
            repo="alpha",
            repo_slug="owner/alpha",
            target_dir=str(tmp_path / "alpha"),
            profile="profiles/team-exp.yaml",
            author_backend="codex",
            requested_ws_id=None,
            requested_bundle_dir=None,
            ops=ops,
        )
    assert "одна head-ветка соответствует нескольким PR" in str(caught.value)
    assert "задайте --ws-id" not in str(caught.value)


def test_recover_refuses_missing_run_id_fact(runs_root, tmp_path) -> None:
    pr = _bundle_pr()
    pr["body"] = "body отредактирован"

    with pytest.raises(spec_loop.SpecLoopError, match="run-id"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox",
            repo="alpha",
            repo_slug="owner/alpha",
            target_dir=str(tmp_path / "alpha"),
            profile="profiles/team-exp.yaml",
            author_backend="codex",
            requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps([pr]),
        )


def test_recover_refuses_closed_unmerged_bundle_pr(
    runs_root, tmp_path
) -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="закрыт без мержа"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox",
            repo="alpha",
            repo_slug="owner/alpha",
            target_dir=str(tmp_path / "alpha"),
            profile="profiles/team-exp.yaml",
            author_backend="codex",
            requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps([_bundle_pr()], state="CLOSED"),
        )


def test_recover_refuses_open_pr_because_review_verdict_is_unknown(
    runs_root, tmp_path
) -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="S6 review и S7 verdict"):
        spec_loop.recover_run_from_github(
            subject="Fleet Inbox",
            repo="alpha",
            repo_slug="owner/alpha",
            target_dir=str(tmp_path / "alpha"),
            profile="profiles/team-exp.yaml",
            author_backend="codex",
            requested_ws_id=None,
            requested_bundle_dir=None,
            ops=_RecoveryOps([_bundle_pr()], state="OPEN"),
        )
    assert rs.all_run_ids() == []


# --- CLI: жёсткий merge_authority ------------------------------------------


def test_cli_rejects_merge_authority_override(capsys) -> None:
    with pytest.raises(SystemExit):
        spec_loop.main(
            ["--subject", "s", "--repo", "alpha", "--merge-authority", "human"]
        )
    assert "merge-authority" in capsys.readouterr().err


# --- маршрутизация ---------------------------------------------------------


class _LoopEnv:
    """Подмены runner.start/resume и deliver_for_run с записью вызовов."""

    def __init__(self, monkeypatch, tmp_path: Path, resume_result=None):
        self.calls: list[tuple] = []
        self.resume_result = resume_result
        # devtools#247: start может закончиться и НЕпродолжаемым статусом —
        # `discovery start` вернул 1/2, сессия не создана вовсе. Тест задаёт
        # такой исход парой (status, session_id).
        self.start_override: tuple[str, str | None] | None = None
        target = tmp_path / "alpha"
        (target / ".git").mkdir(parents=True)
        self.target = target
        monkeypatch.setattr(
            spec_loop, "MANIFEST_PATH", tmp_path / "manifest.toml"
        )
        (tmp_path / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
        monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(
            spec_loop, "_origin_url", lambda d: "git@github.com:owner/alpha.git"
        )
        monkeypatch.setattr(spec_loop.runner, "start", self._start)
        monkeypatch.setattr(spec_loop.runner, "resume", self._resume)
        monkeypatch.setattr(
            spec_loop.task_bridge, "deliver_for_run", self._deliver
        )
        class _NoRemoteRuns:
            def prs_by_head_prefix(self, repo_slug, branch_prefix):
                return []

        monkeypatch.setattr(spec_loop, "_real_ops", _NoRemoteRuns)

    def _start(self, **kwargs):
        self.calls.append(("start", kwargs))
        interview = (
            kwargs["interview_spec"].as_state()
            if kwargs.get("interview_spec") else None
        )
        state = rs.new_run(
            subject=kwargs["subject"],
            repo=kwargs["repo"],
            repo_slug=kwargs["repo_slug"],
            ws_id=kwargs["ws_id"],
            target_dir=kwargs["target_dir"],
            bundle_dir=kwargs["bundle_dir"],
            profile=kwargs["profile"],
            run_id=kwargs["run_id"],
            merge_authority=kwargs["merge_authority"],
            brief=(
                kwargs["brief_source"].as_state()
                if kwargs.get("brief_source") else None
            ),
            interview=interview,
        )
        state.status = "waiting_interview" if interview else "waiting_human_merge"
        if self.start_override is not None:
            state.status, session_id = self.start_override
            if state.interview is not None:
                state.interview["session_id"] = session_id
        return state

    def _resume(self, run_id, ops):
        self.calls.append(("resume", run_id))
        return self.resume_result

    def _deliver(self, state, ops, legacy_bundle=None):
        self.calls.append(("deliver", state.run_id))
        return 42


def test_no_run_starts_with_human_authority_and_prints_values(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0
    kinds = [c[0] for c in env.calls]
    assert kinds == ["start"]
    kwargs = env.calls[0][1]
    assert kwargs["merge_authority"] == "human"
    assert kwargs["ws_id"].startswith("fleet-inbox-")
    assert kwargs["run_id"].startswith(kwargs["ws_id"])
    out = capsys.readouterr().out
    # Таблица разрешённых значений печатается ДО действия и несёт run-id.
    assert kwargs["run_id"] in out
    assert "owner/alpha" in out
    assert "waiting_human_merge" in out


def test_new_run_accepts_brief_before_start_and_prints_source(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    source = tmp_path / "input.md"
    source.write_text(_customer_brief(), encoding="utf-8")

    rc = spec_loop.main([
        "--subject", "Fleet Inbox", "--repo", "alpha",
        "--brief", str(source),
    ])

    assert rc == 0
    kwargs = env.calls[0][1]
    assert kwargs["brief_source"].frame == "customer"
    out = capsys.readouterr().out
    assert "brief-frame" in out and "customer" in out
    assert "00-discovery/brief.md" in out


def test_invalid_brief_refuses_before_runner_or_remote_calls(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    source = tmp_path / "bad.md"
    source.write_text(
        _customer_brief(validation="pending"), encoding="utf-8"
    )

    rc = spec_loop.main([
        "--subject", "Fleet Inbox", "--repo", "alpha",
        "--brief", str(source),
    ])

    assert rc == 1
    assert env.calls == []
    assert rs.all_run_ids() == []


def test_existing_run_rejects_different_brief(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    original = tmp_path / "original.md"
    original.write_text(_customer_brief(), encoding="utf-8")
    descriptor = brief_input.inspect_brief(original).as_state()
    state = _mk_run(
        "fleet-inbox-existing", "Fleet Inbox", status="completed",
        target_dir=str(env.target),
    )
    state.brief = descriptor
    rs.save(state)
    changed = tmp_path / "changed.md"
    changed.write_text(
        _customer_brief().replace("Goal", "Changed goal"), encoding="utf-8"
    )

    rc = spec_loop.main([
        "--subject", "Fleet Inbox", "--repo", "alpha",
        "--brief", str(changed),
    ])

    assert rc == 1
    assert env.calls == []
    assert "без --need невозможен" in capsys.readouterr().out


def test_missing_ledger_recovers_then_resumes_s8_and_reconciles_tasks_pr(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Приёмка R3: RUNS_ROOT пуст, durable GitHub-факты продолжают цикл."""
    target = tmp_path / "alpha"
    (target / ".git").mkdir(parents=True)
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda _d: "git@github.com:owner/alpha.git"
    )
    ops = _RecoveryOps([_bundle_pr()])
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: ops)
    calls: list[tuple] = []

    def _resume(run_id, passed_ops):
        state = rs.load(run_id)
        calls.append(("resume-s8", run_id, passed_ops is ops))
        state.status = "completed"
        rs.save(state)
        return state

    def _deliver(state, passed_ops, legacy_bundle=None):
        calls.append(("reconcile-tasks", state.ws_id, passed_ops is ops))
        return 77

    monkeypatch.setattr(spec_loop.runner, "resume", _resume)
    monkeypatch.setattr(spec_loop.task_bridge, "deliver_for_run", _deliver)

    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])

    assert rc == 0
    assert calls == [
        ("resume-s8", "fleet-inbox-20260901-a1b2c3", True),
        ("reconcile-tasks", "fleet-inbox-20260901", True),
    ]
    out = capsys.readouterr().out
    assert "ledger отсутствовал; восстановлен" in out
    assert "GitHub исторический profile не хранит" in out
    assert "tasks-спека доставлена: PR #77" in out


def test_repeat_finds_run_without_date_and_resumes(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Повтор ищет по (repo, subject), а не по ws-id с сегодняшней датой."""
    state = _mk_run(
        "fleet-inbox-20260901-abc123", "Fleet Inbox",
        ws_id="fleet-inbox-20260901", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=state)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0
    assert env.calls == [("resume", "fleet-inbox-20260901-abc123")]
    assert "ждём" in capsys.readouterr().out


def test_resume_completed_delivers_and_stops_at_approve(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    state = _mk_run(
        "r-w", "S", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    merged = rs.load("r-w")
    merged.status = "completed"
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=merged)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 0
    assert [c[0] for c in env.calls] == ["resume", "deliver"]
    assert "approve" in capsys.readouterr().out


@pytest.mark.parametrize(
    "message, expects_hint",
    [
        (
            "активный DAG не одобрен целиком — доставка не начата (§I12)",
            True,
        ),
        (
            "target_dir 'x' грязный — доставка не начата",
            False,
        ),
    ],
)
def test_gate_refusal_routes_operator_to_approve_node(
    runs_root, tmp_path, monkeypatch, capsys, message, expects_hint
) -> None:
    """Подсказка про `--approve-node` приходит на отказе гейта §I12 и ТОЛЬКО
    на нём (минор ревью #191, круг 2).

    Отказов у доставки много, и общая подсказка уводила бы оператора
    одобрять узлы там, где мешает грязное дерево. Обе половины проверяются
    одним тестом: без второй строки параметров «печатать всегда» прошло бы
    тоже.
    """
    _mk_run("r-g", "S", status="completed", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)

    def _refuse(state, ops, legacy_bundle=None):
        env.calls.append(("deliver", state.run_id))
        raise RuntimeError(message)

    monkeypatch.setattr(spec_loop.task_bridge, "deliver_for_run", _refuse)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    out = capsys.readouterr().out
    assert message in out
    assert ("--approve-node" in out) is expects_hint


def test_completed_run_goes_straight_to_deliver(
    runs_root, tmp_path, monkeypatch
) -> None:
    _mk_run("r-c", "S", status="completed", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 0
    assert [c[0] for c in env.calls] == ["deliver"]


@pytest.mark.parametrize(
    "status", ["stopped_gate", "merged_unverified", "running", "что-то"]
)
def test_non_continuable_statuses_report_without_calls(
    runs_root, tmp_path, monkeypatch, capsys, status
) -> None:
    """Статусы, которых диспетчер не берёт: отчёт без единого вызова.

    Перечня продолжаемых статусов тут нет намеренно — он устарел молча,
    когда E2 добавил `waiting_interview`/`stopped_interview`, и прожил так
    до devtools#247. Предмет проверки — что вызовов не было и статус
    назван, а не список.
    """
    _mk_run("r-s", "S", status=status, target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert status in capsys.readouterr().out


def test_resume_landing_on_stopped_reports_nonzero(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    state = _mk_run(
        "r-w2", "S", status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
    )
    after = rs.load("r-w2")
    after.status = "merged_unverified"
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=after)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert [c[0] for c in env.calls] == ["resume"]
    assert "merged_unverified" in capsys.readouterr().out


def test_multiple_matches_fail_closed_with_candidates(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    _mk_run("r-a", "S", target_dir=str(tmp_path / "alpha"))
    _mk_run("r-b", "S", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    out = capsys.readouterr().out
    assert "r-a" in out and "r-b" in out and "--run-id" in out


def test_run_id_override_with_subject_mismatch_fails(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    _mk_run("r-x", "Другой subject", target_dir=str(tmp_path / "alpha"))
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(
        ["--subject", "S", "--repo", "alpha", "--run-id", "r-x"]
    )
    assert rc == 1
    assert env.calls == []
    assert "subject" in capsys.readouterr().out


def test_ws_id_collision_with_other_subject_fails_closed(
    runs_root, tmp_path, monkeypatch, capsys,
) -> None:
    today = date.today().strftime("%Y%m%d")
    _mk_run(
        "r-coll", "Другой subject", ws_id=f"s-{today}",
        target_dir=str(tmp_path / "alpha"),
    )
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "s", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert "ws-id" in capsys.readouterr().out.lower()


def test_origin_mismatch_fails_closed(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda d: "git@github.com:other/fork.git"
    )
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert "origin" in capsys.readouterr().out


def test_find_runs_ignores_empty_ledger_stub(runs_root) -> None:
    """minor терм. ревью #156: пустой run.json — штатный труп runner'а
    (_reserve_run_id), кнопку глушить не должен."""
    _mk_run("r1", "Subject A")
    stub = runs_root / "r-stub"
    stub.mkdir(parents=True)
    (stub / "run.json").write_text("", encoding="utf-8")
    found = spec_loop.find_runs("alpha", "Subject A")
    assert [s.run_id for s in found] == ["r1"]


def test_find_runs_broken_message_hints_run_id(runs_root) -> None:
    bad = runs_root / "r-broken"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(spec_loop.SpecLoopError, match="--run-id"):
        spec_loop.find_runs("alpha", "Subject A")


def test_origin_url_on_non_git_dir_fails_closed(tmp_path: Path) -> None:
    """minor терм. ревью #156: не склонированный репо — fail-closed
    сообщение кнопки, не CalledProcessError-traceback."""
    with pytest.raises(spec_loop.SpecLoopError, match="чекаут"):
        spec_loop._origin_url(tmp_path)


# --- стадия Need: --need-флаги и preflight ----------------------------------


def _make_need_run(
    env, run_id_suffix="", status="waiting_interview", session="s-1",
    stakeholder="product owner",
):
    st = rs.new_run(
        subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
        ws_id="ws-a" + run_id_suffix, target_dir=str(env.target),
        bundle_dir="workstreams/ws-a/spec", profile="profiles/team-exp.yaml",
        run_id="r-a" + run_id_suffix, merge_authority="human",
        interview={
            **iv.InterviewSpec(
                "customer", stakeholder, "owner/alpha", None, None
            ).as_state(),
            "session_id": session,
        },
    )
    st.status = status
    st.ops["interview-start"] = {
        "status": "completed" if session else "started"
    }
    rs.save(st)
    return st


def _need(*extra):
    return [
        "--subject", "Fleet Inbox", "--repo", "alpha", "--need",
        "--frame", "customer", "--stakeholder", "product owner", *extra,
    ]


def test_need_customer_starts_with_interview_spec(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(_need())
    assert rc == 0
    spec = env.calls[0][1]["interview_spec"]
    assert (spec.frame, spec.stakeholder_role, spec.target) == (
        "customer", "product owner", "owner/alpha",
    )
    assert spec.traces_to is None
    assert "waiting_interview" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv,needle",
    [
        (
            ["--subject", "s", "--repo", "alpha", "--need", "--frame", "customer"],
            "--stakeholder",
        ),
        (
            ["--subject", "s", "--repo", "alpha", "--need", "--stakeholder", "r"],
            "--frame",
        ),
        (
            ["--subject", "s", "--repo", "alpha", "--frame", "customer"],
            "--need",
        ),
        (
            ["--subject", "s", "--repo", "alpha", "--stakeholder", "r"],
            "--need",
        ),
        (
            ["--subject", "s", "--repo", "alpha", "--session", "s-1"],
            "--need",
        ),
        (
            ["--subject", "s", "--repo", "alpha", "--new-run", "--ws-id", "x"],
            "--need",
        ),
        (_need("--traces-to", "c.md"), "customer"),
        (_need("--brief", "x.md"), "--brief"),
        (
            [
                "--subject", "s", "--repo", "alpha", "--need", "--frame", "engineer",
                "--stakeholder", "r", "--traces-to", "c.md",
            ],
            "discovery#49",
        ),
    ],
)
def test_need_preflight_refuses_before_run_id(
    runs_root, tmp_path, monkeypatch, capsys, argv, needle
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(argv)
    assert rc == 1
    assert env.calls == [] and rs.all_run_ids() == []
    assert needle in capsys.readouterr().out


def test_need_without_stakeholder_explains_rule_and_brief_route(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    _LoopEnv(monkeypatch, tmp_path)
    spec_loop.main(
        ["--subject", "s", "--repo", "alpha", "--need", "--frame", "customer"]
    )
    out = capsys.readouterr().out
    assert "реального стейкхолдера" in out and "--brief" in out


def test_need_repeat_with_other_coordinates_refuses(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    # сохранённый леджер в waiting_interview, stakeholder "product owner"
    _make_need_run(env)
    rc = spec_loop.main([
        "--subject", "Fleet Inbox", "--repo", "alpha", "--need",
        "--frame", "customer", "--stakeholder", "qa",
    ])
    assert rc == 1 and "координаты" in capsys.readouterr().out
    assert env.calls == []


def test_need_against_run_without_interview_refuses(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _mk_run(
        "r-legacy", "Fleet Inbox", target_dir=str(env.target),
        status="waiting_human_merge",
    )
    rc = spec_loop.main(_need())
    assert rc == 1 and "--new-run --ws-id" in capsys.readouterr().out
    assert env.calls == []


def test_need_against_recovered_run_without_interview_refuses(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Ruling 2: гвард стоит ПОСЛЕ recover_run_from_github — восстановленный
    из GitHub прогон тоже не несёт interview, и `--need` на нём отказывает
    той же подсказкой `--new-run --ws-id`, не вызывая resume."""
    target = tmp_path / "alpha"
    (target / ".git").mkdir(parents=True)
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda _d: "git@github.com:owner/alpha.git"
    )
    ops = _RecoveryOps([_bundle_pr()])
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: ops)
    resume_calls: list[str] = []
    monkeypatch.setattr(
        spec_loop.runner, "resume",
        lambda run_id, passed_ops: resume_calls.append(run_id),
    )

    rc = spec_loop.main(_need())

    assert rc == 1
    out = capsys.readouterr().out
    assert "--new-run --ws-id" in out
    assert resume_calls == []
    assert rs.all_run_ids() == ["fleet-inbox-20260901-a1b2c3"]


# --- стадия Need: диспетчер waiting/stopped_interview, --session, --new-run ---


def test_waiting_interview_resume_still_waiting_exits_0(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env)
    env.resume_result = st
    assert spec_loop.main(_need()) == 0
    assert [c[0] for c in env.calls] == ["resume"]
    # команду ответа печатает runner (`_print_answer_hint`), spec_loop её не
    # дублирует; здесь runner.resume заглушён — проверяется только код
    # выхода


def test_stopped_interview_with_session_resumes_and_exits_1_if_still_stopped(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview")
    env.resume_result = st
    assert spec_loop.main(_need()) == 1
    assert [c[0] for c in env.calls] == ["resume"]


def test_start_landing_on_orphan_stop_prints_the_same_recovery(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """devtools#247: на start-пути `stopped_interview` уходил в
    `_report_state`, а тот советовал `behaviour-run resume`.

    Восстанавливать этим нечего: `discovery start` вернул 1/2, сессии не
    существует. Подсказка обязана быть той же, что у диспетчера, — и
    буквально той же, а не похожей: иначе два текста разъедутся.
    """
    env = _LoopEnv(monkeypatch, tmp_path)
    env.start_override = ("stopped_interview", None)

    rc = spec_loop.main(_need())

    assert rc == 1
    out = capsys.readouterr().out
    assert "--session" in out and "--new-run --ws-id" in out
    assert "behaviour-run" not in out


def test_stopped_interview_orphan_prints_recovery_without_resume(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="stopped_interview", session=None)
    assert spec_loop.main(_need()) == 1
    assert env.calls == []
    out = capsys.readouterr().out
    assert "--session" in out and "--new-run --ws-id" in out


def test_session_attach_calls_attach_then_resume(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview", session=None)
    attached = []

    def _attach(run_id, session_id, ops):
        attached.append((run_id, session_id))
        st.interview["session_id"] = session_id
        st.status = "waiting_interview"
        rs.save(st)
        return st

    monkeypatch.setattr(spec_loop.runner, "attach_session", _attach)
    env.resume_result = st
    assert spec_loop.main(_need("--session", "s-77")) == 0
    assert attached == [("r-a", "s-77")] and [c[0] for c in env.calls] == [
        "resume"
    ]


def test_new_run_requires_pre_s1_runs_and_prints_run_id(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="stopped_interview")
    # два совпавших — гвард неоднозначности НЕ применяется
    _make_need_run(env, run_id_suffix="2", status="waiting_interview")
    rc = spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh"))
    assert rc == 0
    assert env.calls[0][0] == "start" and env.calls[0][1]["ws_id"] == "ws-fresh"
    out = capsys.readouterr().out
    assert f"--run-id {env.calls[0][1]['run_id']}" in out
    assert rs.load("r-a").status == "stopped_interview"  # старые леджеры целы


def test_new_run_refused_when_a_match_reached_s1(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="waiting_human_merge")
    st.branch = "spec/ws-a-behaviour"
    rs.save(st)
    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
    assert env.calls == [] and "--run-id" in capsys.readouterr().out


def test_need_with_run_id_on_repeat_is_allowed(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env)
    env.resume_result = st
    assert spec_loop.main(_need("--run-id", "r-a")) == 0
