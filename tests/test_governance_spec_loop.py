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

    def show_file_bytes(self, target_dir, ref, path):
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
        )
        state.status = "waiting_human_merge"
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
    assert "другим --ws-id" in capsys.readouterr().out


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
    """Автоматически продолжается ТОЛЬКО waiting_human_merge/completed."""
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
