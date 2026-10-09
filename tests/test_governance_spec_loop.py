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

from governance import brief_input, run_lock, spec_loop
from governance import interview as iv
from governance import run_state as rs


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
        spec_loop.ws_id_for("Fleet Inbox", date(2026, 9, 7)) == "fleet-inbox-20260907"
    )


def test_repo_slug_from_url_forms() -> None:
    assert spec_loop.repo_slug_from_url("git@github.com:o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("https://github.com/o/n.git") == "o/n"
    assert spec_loop.repo_slug_from_url("ssh://git@github.com/o/n") == "o/n"


def test_manifest_single_match() -> None:
    entry = spec_loop.resolve_repo_entry(MANIFEST, "alpha", None)
    assert entry.repo == "alpha"
    assert entry.repo_slug == "owner/alpha"


def test_manifest_shared_git_dir_same_url_is_one_repo() -> None:
    entry = spec_loop.resolve_repo_entry(MANIFEST, "beta", None)
    assert entry.repo_slug == "owner/beta"


def test_manifest_zero_matches_fails_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="нет ни в манифесте"):
        spec_loop.resolve_repo_entry(MANIFEST, "nope", None)


def test_manifest_conflicting_urls_fail_closed() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="gamma"):
        spec_loop.resolve_repo_entry(MANIFEST, "gamma", None)


# --- цели приёмки вне флота (решение владельца 2026-10-09) ----------------

TARGETS = """\
[targets.polygon]
git_dir = "polygon"
repo_url = "https://github.com/DarkFactory-polygon/polygon.git"

[targets.alpha]
git_dir = "alpha"
repo_url = "https://github.com/someone-else/alpha.git"
"""


def test_acceptance_target_used_when_absent_from_fleet() -> None:
    entry = spec_loop.resolve_repo_entry(MANIFEST, "polygon", TARGETS)
    assert (entry.repo, entry.repo_slug, entry.source) == (
        "polygon",
        "DarkFactory-polygon/polygon",
        "acceptance",
    )


def test_fleet_manifest_has_priority_over_acceptance_list() -> None:
    entry = spec_loop.resolve_repo_entry(MANIFEST, "alpha", TARGETS)
    assert (entry.repo_slug, entry.source) == ("owner/alpha", "fleet")


@pytest.mark.parametrize("targets", [TARGETS, None, ""])
def test_unknown_target_refused(targets) -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="нет ни в манифесте"):
        spec_loop.resolve_repo_entry(MANIFEST, "nope", targets)


@pytest.mark.parametrize(
    "manifest",
    [
        "not = [toml",  # не TOML
        MANIFEST  # неоднозначный git_dir у другой цели не мешает, а у искомой —
        + '\n[cores.poly-a]\nrepo_url = "git@github.com:a/p.git"\ngit_dir = "polygon"\n'
        + '\n[cores.poly-b]\nrepo_url = "git@github.com:b/p.git"\ngit_dir = "polygon"\n',
    ],
    ids=["broken-toml", "ambiguous-in-fleet"],
)
def test_broken_fleet_manifest_never_falls_back(manifest) -> None:
    """Ошибка манифеста не разрешает fallback, даже если цель есть в списке."""
    with pytest.raises(spec_loop.SpecLoopError) as exc:
        spec_loop.resolve_repo_entry(manifest, "polygon", TARGETS)
    assert "нет ни в манифесте" not in str(exc.value)


@pytest.mark.parametrize("repo", ["../polygon", "/abs/polygon", "a/b", ".hidden"])
@pytest.mark.parametrize("targets", [TARGETS, None], ids=["with-list", "fleet-only"])
def test_target_must_be_one_dir_component(repo, targets) -> None:
    """И флот, и список приёмки: `WORKSPACE_ROOT / repo` не выходит из workspace
    (даже если такой git_dir записан в файле состава)."""
    manifest = (
        MANIFEST
        + f'\n[cores.evil]\nrepo_url = "git@github.com:o/e.git"\ngit_dir = "{repo}"\n'
    )
    with pytest.raises(spec_loop.SpecLoopError, match="невалиден"):
        spec_loop.resolve_repo_entry(manifest, repo, targets)


def test_broken_acceptance_list_refused() -> None:
    with pytest.raises(spec_loop.SpecLoopError, match="targets"):
        spec_loop.resolve_repo_entry(MANIFEST, "polygon", "not = [toml")


def test_shipped_acceptance_list_is_relative_and_parses() -> None:
    """Shipped-файл: публичная песочница, git_dir — одно имя каталога, без
    путей машины."""
    import tomllib

    text = spec_loop.ACCEPTANCE_TARGETS_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    for entry in data["targets"].values():
        rs.validate_id_component(entry["git_dir"], label="git_dir")
    assert "/Users/" not in text and "/home/" not in text
    entry = spec_loop.resolve_repo_entry(MANIFEST, "spec-loop-sandbox", text)
    assert entry.repo_slug == "andrei-shtanakov/spec-loop-sandbox"


@pytest.mark.skipif(
    not __import__("os").environ.get("DEVTOOLS_TARGETS_PROBE"),
    reason="opt-in: DEVTOOLS_TARGETS_PROBE=1 (живые запросы к GitHub)",
)
def test_every_acceptance_target_is_public_and_admitted_by_halt_gate() -> None:
    """Гарантия списка: КАЖДАЯ цель публична (API репо) и проходит стоп-кран
    тем же вызовом, что `runner.start` (`halt_gate.check`) — без исключений.
    Приватный репо на бесплатном плане даёт refuse_unknown и здесь краснеет."""
    import json
    import subprocess
    import tomllib

    from governance import halt_gate

    data = tomllib.loads(spec_loop.ACCEPTANCE_TARGETS_PATH.read_text("utf-8"))
    assert data["targets"]
    for entry in data["targets"].values():
        slug = spec_loop.repo_slug_from_url(entry["repo_url"])
        repo = json.loads(
            subprocess.run(
                ["gh", "api", f"repos/{slug}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        assert repo["visibility"] == "public", (slug, repo["visibility"])
        admit, code, reason = halt_gate.check(slug)
        assert admit, (slug, code, reason)


def test_acceptance_targets_list_is_authority_root() -> None:
    """Список целей приёмки — authority-root: агентский мерж его не меняет."""
    from governance import authority_root

    rel = spec_loop.ACCEPTANCE_TARGETS_PATH.relative_to(spec_loop.DEVTOOLS_ROOT)
    assert authority_root.touched([str(rel)]) == [str(rel)]


def test_manifest_unreadable_is_refused_without_fallback(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "missing.toml")
    rc = spec_loop.main(["--subject", "s", "--repo", "polygon"])
    assert rc == 1 and env.calls == []
    assert "манифест флота" in capsys.readouterr().out


@pytest.mark.parametrize("origin_ok", [True, False])
def test_acceptance_target_keeps_origin_check(
    runs_root, tmp_path, monkeypatch, capsys, origin_ok
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    (tmp_path / "polygon" / ".git").mkdir(parents=True)
    targets = tmp_path / "targets.toml"
    targets.write_text(TARGETS, encoding="utf-8")
    monkeypatch.setattr(spec_loop, "ACCEPTANCE_TARGETS_PATH", targets)
    origin = (
        "https://github.com/DarkFactory-polygon/polygon.git"
        if origin_ok
        else "git@github.com:attacker/polygon.git"
    )
    monkeypatch.setattr(spec_loop, "_origin_url", lambda d: origin)
    rc = spec_loop.main(["--subject", "s", "--repo", "polygon"])
    out = capsys.readouterr().out
    if origin_ok:
        assert rc == 0 and env.calls[0][0] == "start"
        assert env.calls[0][1]["repo_slug"] == "DarkFactory-polygon/polygon"
        assert env.calls[0][1]["target_dir"] == str(tmp_path / "polygon")
    else:
        assert rc == 1 and env.calls == []
        assert "расхождение origin" in out


def test_target_dir_does_not_widen_the_target_set(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """`--target-dir` не делает произвольный репо целью: цель — из SSOT, а
    origin каталога обязан совпасть с ней."""
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(spec_loop, "ACCEPTANCE_TARGETS_PATH", tmp_path / "none.toml")
    rc = spec_loop.main(
        ["--subject", "s", "--repo", "anything", "--target-dir", str(tmp_path)]
    )
    assert rc == 1 and env.calls == []
    assert "нет ни в манифесте" in capsys.readouterr().out


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
        authoring=kw.get("authoring", "legacy"),
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
    def __init__(self, prs, *, state="MERGED", files=None, head_files=None):
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

    def pr_closure(self, repo_slug: str, pr: int) -> dict | None:
        return None  # факты закрытия (§I10 для v1) этому стенду не нужны

    def agent_login(self) -> str | None:
        return None

    def caller_login(self) -> str | None:
        return None

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


def test_recover_run_from_github_refuses_the_removed_path(runs_root, tmp_path) -> None:
    """S13: бандл-PR прежнего пути РАСПОЗНАЁТСЯ, но его исполнение не
    восстанавливается. Отказ адресный — называет PR, ветку и run-id, —
    и наступает ДО создания леджера: каталог прогонов остаётся пуст.
    """
    ops = _RecoveryOps([_bundle_pr()])

    with pytest.raises(spec_loop.SpecLoopError) as exc:
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

    message = str(exc.value)
    assert "#41" in message and "spec/fleet-inbox-20260901-behaviour" in message
    assert "fleet-inbox-20260901-a1b2c3" in message, "run-id прежнего прогона"
    assert "2026-09-23" in message and "S13" in message
    assert "waves" in message, "что делать вместо"
    assert rs.all_run_ids() == [], "леджер не создан — отказ бесследен"


def test_recover_multiple_candidates_requires_ws_id(runs_root, tmp_path) -> None:
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


def test_recover_refuses_closed_unmerged_bundle_pr(runs_root, tmp_path) -> None:
    # S13: отказ по состоянию PR больше не наступает — распознав
    # бандл-PR прежнего пути, восстановление отказывает раньше и по
    # более общей причине. Закрытый без мержа PR прежнего пути
    # интересен теперь только тем, что он прежнего пути.
    with pytest.raises(spec_loop.SpecLoopError, match="S13"):
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
    # S13: отказ по состоянию PR больше не наступает — распознав
    # бандл-PR прежнего пути, восстановление отказывает раньше и по
    # более общей причине. Ещё открытый PR прежнего пути
    # интересен теперь только тем, что он прежнего пути.
    with pytest.raises(spec_loop.SpecLoopError, match="S13"):
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


def _seed_wave_candidate(state: rs.RunState, wave: int, pr: int) -> None:
    """Леджер волнового прогона на паузе: заявка волны с candidate-PR."""
    key = f"approve-1-{wave - 1}-1"
    state.ops[key] = {
        "status": "started",
        "wave": 1,
        "step": wave - 1,
        "attempt": 1,
        "nodes": ["charter"],
        "candidate_pr": pr,
    }
    state.ops[f"candidate-{wave}"] = {
        "status": "completed",
        "request": key,
        "candidate_pr": pr,
    }
    state.wave = wave


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
        monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "manifest.toml")
        (tmp_path / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
        monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(
            spec_loop, "_origin_url", lambda d: "git@github.com:owner/alpha.git"
        )
        monkeypatch.setattr(spec_loop.runner, "start", self._start)
        monkeypatch.setattr(spec_loop.runner, "resume", self._resume)
        monkeypatch.setattr(spec_loop.task_bridge, "deliver_for_run", self._deliver)

        class _NoRemoteRuns:
            def prs_by_head_prefix(self, repo_slug, branch_prefix):
                return []

        monkeypatch.setattr(spec_loop, "_real_ops", _NoRemoteRuns)

    def _start(self, **kwargs):
        assert isinstance(kwargs.pop("lock"), run_lock.RunLock)
        self.calls.append(("start", kwargs))
        interview = (
            kwargs["interview_spec"].as_state()
            if kwargs.get("interview_spec")
            else None
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
                if kwargs.get("brief_source")
                else None
            ),
            interview=interview,
            authoring=kwargs.get("authoring", "legacy"),
        )
        state.status = "waiting_interview" if interview else "waiting_human_merge"
        if state.authoring == "waves":
            _seed_wave_candidate(state, 1, 501)
        if self.start_override is not None:
            state.status, session_id = self.start_override
            if state.interview is not None:
                state.interview["session_id"] = session_id
        return state

    def _resume(self, run_id, ops, *, lock):
        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
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

    rc = spec_loop.main(
        [
            "--subject",
            "Fleet Inbox",
            "--repo",
            "alpha",
            "--brief",
            str(source),
        ]
    )

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
    source.write_text(_customer_brief(validation="pending"), encoding="utf-8")

    rc = spec_loop.main(
        [
            "--subject",
            "Fleet Inbox",
            "--repo",
            "alpha",
            "--brief",
            str(source),
        ]
    )

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
        "fleet-inbox-existing",
        "Fleet Inbox",
        status="completed",
        target_dir=str(env.target),
    )
    state.brief = descriptor
    rs.save(state)
    changed = tmp_path / "changed.md"
    changed.write_text(
        _customer_brief().replace("Goal", "Changed goal"), encoding="utf-8"
    )

    rc = spec_loop.main(
        [
            "--subject",
            "Fleet Inbox",
            "--repo",
            "alpha",
            "--brief",
            str(changed),
        ]
    )

    assert rc == 1
    assert env.calls == []
    assert "без --need невозможен" in capsys.readouterr().out


def test_repeat_finds_run_without_date_and_resumes(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Повтор ищет по (repo, subject), а не по ws-id с сегодняшней датой."""
    state = _mk_run(
        "fleet-inbox-20260901-abc123",
        "Fleet Inbox",
        ws_id="fleet-inbox-20260901",
        status="waiting_human_merge",
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
    _mk_run(
        "r-w",
        "S",
        status="waiting_human_merge",
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
    _mk_run(
        "r-w2",
        "S",
        status="waiting_human_merge",
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
    rc = spec_loop.main(["--subject", "S", "--repo", "alpha", "--run-id", "r-x"])
    assert rc == 1
    assert env.calls == []
    assert "subject" in capsys.readouterr().out


def test_ws_id_collision_with_other_subject_fails_closed(
    runs_root,
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    today = date.today().strftime("%Y%m%d")
    _mk_run(
        "r-coll",
        "Другой subject",
        ws_id=f"s-{today}",
        target_dir=str(tmp_path / "alpha"),
    )
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "s", "--repo", "alpha"])
    assert rc == 1
    assert env.calls == []
    assert "ws-id" in capsys.readouterr().out.lower()


def test_origin_mismatch_fails_closed(runs_root, tmp_path, monkeypatch, capsys) -> None:
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
    env,
    run_id_suffix="",
    status="waiting_interview",
    session="s-1",
    stakeholder="product owner",
):
    st = rs.new_run(
        subject="Fleet Inbox",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="ws-a" + run_id_suffix,
        target_dir=str(env.target),
        bundle_dir="workstreams/ws-a/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-a" + run_id_suffix,
        merge_authority="human",
        interview={
            **iv.InterviewSpec(
                "customer", stakeholder, "owner/alpha", None, None
            ).as_state(),
            "session_id": session,
        },
    )
    st.status = status
    st.ops["interview-start"] = {"status": "completed" if session else "started"}
    rs.save(st)
    return st


def _need(*extra):
    return [
        "--subject",
        "Fleet Inbox",
        "--repo",
        "alpha",
        "--need",
        "--frame",
        "customer",
        "--stakeholder",
        "product owner",
        *extra,
    ]


def test_need_customer_starts_with_interview_spec(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(_need())
    assert rc == 0
    spec = env.calls[0][1]["interview_spec"]
    assert (spec.frame, spec.stakeholder_role, spec.target) == (
        "customer",
        "product owner",
        "owner/alpha",
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
                "--subject",
                "s",
                "--repo",
                "alpha",
                "--need",
                "--frame",
                "engineer",
                "--stakeholder",
                "r",
                "--traces-to",
                "c.md",
            ],
            "--approval-pr",
        ),
        (_need("--approval-pr", "7"), "--approval-pr"),
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
    rc = spec_loop.main(
        [
            "--subject",
            "Fleet Inbox",
            "--repo",
            "alpha",
            "--need",
            "--frame",
            "customer",
            "--stakeholder",
            "qa",
        ]
    )
    assert rc == 1 and "координаты" in capsys.readouterr().out
    assert env.calls == []


def test_need_against_run_without_interview_refuses(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _mk_run(
        "r-legacy",
        "Fleet Inbox",
        target_dir=str(env.target),
        status="waiting_human_merge",
    )
    rc = spec_loop.main(_need())
    assert rc == 1 and "--new-run --ws-id" in capsys.readouterr().out
    assert env.calls == []


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


def test_orphan_hint_wins_over_a_findings_file(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Сирота важнее findings: восстанавливать нечего, пока нет сессии.

    До сведения подсказок в одну функцию orphan-ветка `_dispatch` про файл
    findings не знала вовсе, и порядок был гарантирован структурой кода.
    Теперь оба случая решает одна функция — порядок стал утверждением, и
    его надо держать тестом, иначе сирота получит совет «ответьте на
    findings», отвечать на которые некому.
    """
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview", session=None)
    findings = rs.run_dir(st.run_id) / spec_loop.runner.INTERVIEW_FINDINGS
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("{}", encoding="utf-8")

    assert spec_loop.main(_need()) == 1

    assert env.calls == []
    out = capsys.readouterr().out
    assert "--session" in out and "--new-run --ws-id" in out
    assert "findings" not in out


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

    def _attach(run_id, session_id, ops, *, lock):
        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
        attached.append((run_id, session_id))
        st.interview["session_id"] = session_id
        st.status = "waiting_interview"
        rs.save(st)
        return st

    monkeypatch.setattr(spec_loop.runner, "attach_session", _attach)
    env.resume_result = st
    assert spec_loop.main(_need("--session", "s-77")) == 0
    assert attached == [("r-a", "s-77")] and [c[0] for c in env.calls] == ["resume"]


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


# --- Волновой режим кнопки (план Task 11, S13) ------------------------------


def test_waves_flag_starts_a_wave_run_and_names_the_candidate(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", "--waves"])
    assert rc == 0
    kwargs = env.calls[0][1]
    assert kwargs["authoring"] == "waves"
    assert kwargs["merge_authority"] == "human"
    out = capsys.readouterr().out
    assert "authoring:" in out and "waves" in out
    assert "wave=1/5" in out and "candidate-PR #501" in out
    assert "бандл-PR" not in out


def test_default_start_is_waves_after_the_flip(
    runs_root, tmp_path, monkeypatch
) -> None:
    """Тот же вызов, что до 2026-09-23 давал legacy, теперь даёт waves.

    Тест не удалён, а переписан: развилка та же, изменился её исход, и
    удаление унесло бы единственное место, где дефолт кнопки пинуется.
    """
    env = _LoopEnv(monkeypatch, tmp_path)
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert env.calls[0][1]["authoring"] == "waves"


def test_wave_resume_pause_names_wave_and_pr(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    state = _mk_run(
        "fleet-inbox-20260901-abc123",
        "Fleet Inbox",
        ws_id="fleet-inbox-20260901",
        status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
        authoring="waves",
    )
    _seed_wave_candidate(state, 3, 640)
    rs.save(state)
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=state)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0 and env.calls == [("resume", "fleet-inbox-20260901-abc123")]
    out = capsys.readouterr().out
    assert "wave=3/5" in out and "candidate-PR #640" in out
    # finalize остался человеку (аттестация не опубликована) — назван он.
    state.ops["approve-1-2-1"]["finalize_pr"] = 641
    state.ops["finalize-3"] = {
        "status": "completed",
        "finalize_pr": 641,
        "review_exit": 6,
    }
    rs.save(state)
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert "finalize-PR #641" in capsys.readouterr().out


def test_wave_pause_names_finalize_when_its_merge_was_refused(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Аттестация прошла (`review_exit: 0`), а мерж finalize отказал — пауза
    обязана назвать finalize-PR, а не уже влитый candidate.

    Живой волновой прогон 2026-09-22: candidate #352 влит человеком,
    агентский мерж finalize #353 отказал по незелёным проверкам, и кнопка
    печатала «ждём человеческий мерж candidate-PR #352» — отправляла
    человека делать сделанное. Условие смотрело на ИСТИННОСТЬ `review_exit`,
    поэтому нулевой код (успех аттестации) читался как «finalize ещё нет».
    """
    state = _mk_run(
        "fleet-inbox-20260901-abc123",
        "Fleet Inbox",
        ws_id="fleet-inbox-20260901",
        status="waiting_human_merge",
        target_dir=str(tmp_path / "alpha"),
        authoring="waves",
    )
    _seed_wave_candidate(state, 3, 640)
    state.ops["approve-1-2-1"]["finalize_pr"] = 641
    state.ops["finalize-3"] = {
        "status": "completed",
        "finalize_pr": 641,
        "review_exit": 0,
    }
    rs.save(state)
    _LoopEnv(monkeypatch, tmp_path, resume_result=state)
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    out = capsys.readouterr().out
    assert "finalize-PR #641" in out
    assert "candidate-PR #640" not in out, (
        "candidate уже влит человеком — звать его мержить снова неверно"
    )


class _WaveRecoveryOps:
    def __init__(self, prs, states=None):
        self.prs = prs
        self.states = states or {}
        self.prefixes: list[str] = []

    def prs_by_head_prefix(self, repo_slug, branch_prefix):
        self.prefixes.append(branch_prefix)
        return self.prs

    def pr_facts(self, repo_slug, pr):
        return {
            "state": self.states.get(pr, "MERGED"),
            "baseRefName": "master",
            "headRefOid": "b" * 40,
        }

    def pr_closure(self, repo_slug: str, pr: int) -> dict | None:
        return None  # факты закрытия (§I10 для v1) этому стенду не нужны

    def agent_login(self) -> str | None:
        return None

    def caller_login(self) -> str | None:
        return None


def _wave_pr(
    number,
    wave,
    step,
    attempt,
    *,
    final=False,
    run_id="fleet-inbox-20260901-a1b2c3",
    ws_id="fleet-inbox-20260901",
):
    branch = f"spec/{ws_id}-approve-{wave}-{step}-{attempt}" + (
        "-final" if final else ""
    )
    body = (
        ""
        if final
        else f"Предложение об одобрении узлов бандла {ws_id} (§I12).\n\nrun-id: {run_id}\n"
    )
    return {"number": number, "head": {"ref": branch}, "title": "x", "body": body}


def _wave_recovery_env(tmp_path, monkeypatch, ops):
    target = tmp_path / "alpha"
    (target / ".git").mkdir(parents=True)
    (tmp_path / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "manifest.toml")
    monkeypatch.setattr(spec_loop, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda _d: "git@github.com:owner/alpha.git"
    )
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: ops)


def test_historical_bundle_pr_does_not_block_wave_recovery(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """Сосуществование (S13, решение владельца 2026-09-23): в фордже лежат
    И бандл-PR прежнего прогона, И candidate-PR живого волнового.

    Порядок обязан отдать волновой: иначе присутствие исторического
    бандла одним своим фактом закрывало бы восстановление живого прогона
    отказом «путь удалён» — отказ был бы формально верен и практически
    вреден. Проверяется не порядок вызовов, а ИСХОД: прогон восстановлен,
    отказа нет.
    """
    ops = _WaveRecoveryOps(
        [
            _bundle_pr(),  # исторический бандл-PR прежнего пути — в той же выдаче
            _wave_pr(10, 1, 0, 1),
            _wave_pr(11, 1, 0, 1, final=True),
            _wave_pr(12, 1, 1, 1),
            _wave_pr(13, 1, 1, 1, final=True),
        ]
    )
    _wave_recovery_env(tmp_path, monkeypatch, ops)
    seen: list[str] = []

    def _resume(run_id, passed_ops, *, lock):
        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
        state = rs.load(run_id)
        seen.append(run_id)
        assert state.authoring == "waves", "восстановлен волновой прогон, а не прежний"
        state.wave = 3
        _seed_wave_candidate(state, 3, 14)
        rs.save(state)
        return state

    monkeypatch.setattr(spec_loop.runner, "resume", _resume)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])

    assert rc == 0 and seen == ["fleet-inbox-20260901-a1b2c3"]
    assert ops.prefixes == ["spec/fleet-inbox-"], (
        "к бандл-PR прежнего пути обвязка не обращалась вовсе"
    )


def test_missing_ledger_recovers_wave_run_from_candidate_prs(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    ops = _WaveRecoveryOps(
        [
            _wave_pr(10, 1, 0, 1),
            _wave_pr(11, 1, 0, 1, final=True),
            _wave_pr(12, 1, 1, 1),
            _wave_pr(13, 1, 1, 1, final=True),
        ]
    )
    _wave_recovery_env(tmp_path, monkeypatch, ops)
    seen: list[str] = []

    def _resume(run_id, passed_ops, *, lock):
        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
        state = rs.load(run_id)
        seen.append(run_id)
        assert state.authoring == "waves" and state.wave == 2
        assert state.ops["candidate-1"]["request"] == "approve-1-0-1"
        assert state.ops["candidate-2"] == {
            "status": "completed",
            "request": "approve-1-1-1",
            "candidate_pr": 12,
        }
        assert state.ops["approve-1-1-1"]["status"] == "completed"
        assert state.ops["approve-1-1-1"]["finalize_pr"] == 13
        assert state.base_ref == "master" and state.pr is None
        assert state.ops["ledger-recovery"]["kind"] == "waves"
        state.wave = 3
        _seed_wave_candidate(state, 3, 14)
        rs.save(state)
        return state

    monkeypatch.setattr(spec_loop.runner, "resume", _resume)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0 and seen == ["fleet-inbox-20260901-a1b2c3"]
    # ОДИН поиск, не два: волновое восстановление идёт ПЕРВЫМ (S13), и
    # найдя candidate-PR, к бандл-PR прежнего пути обвязка не обращается
    # вовсе — исторический бандл не заслоняет живой волновой прогон.
    assert ops.prefixes == ["spec/fleet-inbox-"]
    out = capsys.readouterr().out
    assert "восстановлен из candidate-PR волн (последняя — #12, волна 2)" in out
    assert "wave=3/5" in out and "candidate-PR #14" in out


@pytest.mark.parametrize(
    ("prs", "states", "message"),
    [
        (
            [_wave_pr(10, 1, 0, 1), _wave_pr(11, 1, 0, 1, final=True)],
            {11: "OPEN"},
            "в полёте",
        ),
        ([_wave_pr(10, 1, 0, 1)], {}, "finalize-PR волны"),
        (
            [
                _wave_pr(10, 1, 0, 1, run_id="") | {"body": "без run-id"},
                _wave_pr(11, 1, 0, 1, final=True),
            ],
            {},
            "run-id",
        ),
        (
            [
                _wave_pr(10, 1, 0, 1),
                _wave_pr(11, 1, 0, 1, final=True),
                _wave_pr(12, 1, 1, 1, run_id="other-run"),
                _wave_pr(13, 1, 1, 1, final=True),
            ],
            {},
            "разные run-id",
        ),
        (
            [
                _wave_pr(10, 1, 0, 1),
                _wave_pr(15, 1, 0, 1),
                _wave_pr(11, 1, 0, 1, final=True),
            ],
            {},
            "несколько MERGED candidate-PR",
        ),
    ],
)
def test_wave_recovery_refusals(
    runs_root, tmp_path, monkeypatch, capsys, prs, states, message
) -> None:
    _wave_recovery_env(tmp_path, monkeypatch, _WaveRecoveryOps(prs, states))
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 1 and message in capsys.readouterr().out


def test_wave_recovery_with_no_candidates_starts_a_new_run(
    runs_root, tmp_path, monkeypatch
) -> None:
    ops = _WaveRecoveryOps([_wave_pr(10, 1, 0, 1, ws_id="other-subject-20260901")])
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: ops)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0 and [c[0] for c in env.calls] == ["start"]


# --- S13: дефолт авторинга — волны ----------------------------------------
# Два живых прогона выполнены (evidence 2026-09-22 и 2026-09-23), и второй
# подтвердил самостоятельное завершение волны после devtools#362. S13:
# «дефолт после двух живых прогонов — волны; прежний путь удаляется отдельным
# пунктом» — поэтому здесь ТОЛЬКО флип, прежний путь остаётся достижим.


def test_new_run_without_flags_is_waves(runs_root, tmp_path, monkeypatch) -> None:
    """Кнопка без флагов заводит волновой прогон. До флипа — legacy."""
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    assert rc == 0
    assert env.calls[0][1]["authoring"] == "waves"


def test_legacy_flag_refuses_with_a_named_reason(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    """S13: `--legacy` принимается парсером и ОТКАЗЫВАЕТ с названной
    причиной — оператор обязан прочитать «путь удалён», а не
    `unrecognized arguments`, и не пойти искать опечатку.

    Отказ до побочных эффектов: прогон не заводится, `runner.start` не
    зовётся вовсе.
    """
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", "--legacy"])
    assert rc != 0, "отказ обязан быть отличим кодом возврата"
    assert env.calls == [], "ни одного прогона заведено не было"
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "2026-09-23" in out, "дата решения"
    assert "S13" in out, "пункт спеки"
    assert "waves" in out, "что делать вместо"


def test_waves_flag_survives_the_flip_as_a_noop(
    runs_root, tmp_path, monkeypatch
) -> None:
    """`--waves` остаётся принимаемым: он в доках, скриптах и Makefile."""
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", "--waves"])
    assert rc == 0
    assert env.calls[0][1]["authoring"] == "waves"


def test_ledger_without_authoring_field_still_reads_as_legacy(
    runs_root, tmp_path
) -> None:
    """Исторический run.json без поля — по-прежнему legacy (инвариант S13).

    Флип касается СОЗДАНИЯ прогона. Десериализация читает отсутствие поля
    как прежний путь: иначе давно завершённые прогоны задним числом стали бы
    волновыми, и восстановление из фактов GitHub искало бы candidate-PR там,
    где был бандл-PR.
    """
    import json

    run_id = "r-historic-no-authoring"
    d = rs.run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "ws_id": "ws",
                "subject": "s",
                "repo": "alpha",
                "repo_slug": "owner/alpha",
                "target_dir": str(tmp_path),
                "bundle_dir": "workstreams/ws/spec",
                "profile": "profiles/p.yaml",
                "merge_authority": "human",
                "status": "completed",
                "ops": {},
                "branch": None,
                "pr": None,
                "head": None,
                "remediated_by": None,
            }
        ),
        encoding="utf-8",
    )
    assert rs.load(run_id).authoring == "legacy"


def test_new_run_passes_code_and_plan_item(runs_root, tmp_path, monkeypatch) -> None:
    """Спека оракула §1.1: --code/--plan-item рождают charter схемы 2."""
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(
        [
            "--subject",
            "Fleet Inbox",
            "--repo",
            "alpha",
            "--code",
            "ENC",
            "--plan-item",
            "todo://alpha/oracle",
        ]
    )
    assert rc == 0
    kwargs = env.calls[0][1]
    assert (kwargs["code"], kwargs["plan_item"]) == ("ENC", "todo://alpha/oracle")


@pytest.mark.parametrize(
    "extra",
    [
        ["--code", "enc", "--plan-item", "todo://alpha/oracle"],
        ["--code", "ENC", "--plan-item", "alpha#oracle"],
        ["--code", "ENC"],
        ["--plan-item", "todo://alpha/oracle"],
    ],
)
def test_bad_code_or_plan_item_refuses_before_runner(
    runs_root, tmp_path, monkeypatch, extra
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", *extra])
    assert rc == 1
    assert env.calls == []


# --- блокировка на входе: настоящие пути main (ревью части A, A1) ---


def _spy_loads(monkeypatch) -> list[str]:
    real = rs.load
    seen: list[str] = []

    def spy(run_id: str) -> rs.RunState:
        seen.append(run_id)
        return real(run_id)

    monkeypatch.setattr(rs, "load", spy)
    return seen


def test_run_id_path_locks_before_first_load(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env)
    loads = _spy_loads(monkeypatch)
    with run_lock.run_lock("r-a"):
        assert spec_loop.main(_need("--run-id", "r-a")) == 1
    assert loads == [] and env.calls == []
    assert "другим процессом" in capsys.readouterr().out


def test_search_path_decides_only_after_locking_every_match(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="stopped_interview")
    _make_need_run(env, run_id_suffix="2", status="waiting_interview")
    with run_lock.run_lock("r-a2"):  # второй совпавший занят другим процессом
        assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
    assert env.calls == []  # решение «все до S1» не принято без блокировки
    assert "другим процессом" in capsys.readouterr().out


def test_new_run_check_uses_state_reread_under_lock(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview")
    stale = rs.load("r-a")
    st.status = "waiting_human_merge"  # другой процесс успел дойти до S1
    st.branch = "spec/ws-a-behaviour"
    rs.save(st)
    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [stale])
    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
    assert env.calls == []


def test_wave_recovery_locks_before_reading_or_writing_ledger(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    ops = _WaveRecoveryOps([_wave_pr(10, 1, 0, 1), _wave_pr(11, 1, 0, 1, final=True)])
    _wave_recovery_env(tmp_path, monkeypatch, ops)
    with run_lock.run_lock("fleet-inbox-20260901-a1b2c3"):
        assert spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"]) == 1
    assert rs.all_run_ids() == []  # леджер не записан мимо блокировки
    assert "другим процессом" in capsys.readouterr().out


# --- §11.2: --brief-only (Task 7, часть B) ---


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--subject", "s", "--repo", "alpha", "--brief-only"], "--need"),
        (_need("--brief-only", "--session", "s-1"), "--brief-only"),
        (_need("--brief-only", "--brief", "x.md"), "--brief"),
        (
            [
                "--subject",
                "s",
                "--repo",
                "alpha",
                "--need",
                "--frame",
                "engineer",
                "--stakeholder",
                "r",
                "--brief-only",
            ],
            "--brief-only",
        ),
    ],
    ids=["without-need", "with-session", "with-brief", "engineer"],
)
def test_brief_only_invalid_combinations_refuse_before_run(  # T5
    runs_root, tmp_path, monkeypatch, capsys, argv, needle
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    assert spec_loop.main(argv) == 1
    assert env.calls == [] and rs.all_run_ids() == []
    assert needle in capsys.readouterr().out


def test_brief_only_customer_starts_with_flag(  # T5 двойник
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    assert spec_loop.main(_need("--brief-only")) == 0
    assert env.calls[0][1]["interview_spec"].brief_only is True


def test_repeat_with_other_brief_only_refuses(  # T6
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env)  # прогон стартовал без --brief-only
    before = (rs.run_dir("r-a") / "run.json").read_bytes()
    assert spec_loop.main(_need("--brief-only")) == 1
    assert "brief_only" in capsys.readouterr().out
    assert env.calls == []
    assert (rs.run_dir("r-a") / "run.json").read_bytes() == before


def test_new_run_allowed_next_to_brief_ready(  # T11
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="brief_ready")
    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-eng")) == 0
    assert env.calls[0][0] == "start" and env.calls[0][1]["ws_id"] == "ws-eng"


def test_brief_ready_on_entry_prints_next_step_without_resume(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="brief_ready")
    st.interview["brief_only"] = True
    rs.save(st)
    assert spec_loop.main(_need("--brief-only")) == 0
    assert env.calls == []
    assert "make brief-propose RUN=r-a" in capsys.readouterr().out


def test_first_transition_to_brief_ready_prints_next_step(  # P14
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env)
    st.interview["brief_only"] = True
    rs.save(st)
    done = rs.load("r-a")
    done.status = "brief_ready"
    env.resume_result = done
    assert spec_loop.main(_need("--brief-only")) == 0
    assert [c[0] for c in env.calls] == ["resume"]
    assert "make brief-propose RUN=r-a" in capsys.readouterr().out


# --- §11.4.1–§11.4.2: engineer-preflight (Task 8, часть B) ---

from tests.forge_fake import (  # noqa: E402
    DRAFT,
    POLICY_PATH,
    SIGNED,
    P,
    consistent_world,
)

ENGINEER_REPO = "owner/alpha"


def _engineer_ops(monkeypatch):
    """Мир форджа + пустой поиск candidate-PR (путь восстановления main)."""
    forge = consistent_world(monkeypatch)
    forge.prs_by_head_prefix = lambda repo_slug, prefix: []  # type: ignore[attr-defined]
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: forge)
    return forge


def _operator(
    tmp_path: Path, text: str = SIGNED, name: str = "customer-brief.md"
) -> str:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def test_engineer_preflight_accepts_consistent_world(tmp_path, monkeypatch) -> None:
    forge = consistent_world(monkeypatch)
    intake = spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
    assert intake.buffer == SIGNED.encode() and intake.approval_pr == 7
    assert intake.approval["act_policy_sha"] == P


def test_operator_symlink_read_once(tmp_path, monkeypatch) -> None:  # T33
    forge = consistent_world(monkeypatch)
    real = tmp_path / "real.md"
    real.write_text(SIGNED, encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    reads: list[str] = []
    original = Path.read_bytes

    def spy(self: Path) -> bytes:
        reads.append(str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    intake = spec_loop.engineer_preflight(str(link), 7, ENGINEER_REPO, forge)
    real.write_text("подмена после чтения", encoding="utf-8")
    assert reads == [str(link)] and intake.buffer == SIGNED.encode()


@pytest.mark.parametrize(
    ("case", "needle"),
    [
        ("draft", "approved"),
        ("no-hash", "migration"),
        ("edited", "self_hash"),
        ("crlf", "CR"),
        ("frame-engineer", "upstream не годится"),
        ("merger-out", "merger_not_in_act_policy"),
        ("bundle-pr", "pr_files"),
        ("forge-down", "повторите"),
    ],
)
def test_engineer_preflight_refusals(tmp_path, monkeypatch, case, needle) -> None:
    forge = consistent_world(monkeypatch)
    text = SIGNED
    if case == "draft":
        text = DRAFT
    elif case == "no-hash":
        text = SIGNED.replace("approved_content_hash: ", "x_hash: ")
    elif case == "edited":
        text = SIGNED.replace("## Goals", "## Goals\n\nправка после подписи\n", 1)
    elif case == "crlf":
        text = SIGNED.replace("\n", "\r\n")
    elif case == "frame-engineer":
        text = SIGNED.replace("frame: customer", "frame: engineer")
    elif case == "merger-out":
        forge.files[(P, POLICY_PATH)] = "AUTHORIZED_APPROVER_ACCOUNTS=other\n"
    elif case == "bundle-pr":
        forge.set_pr(files=forge.prs[7].files + (("x/30-decomposition.md", "added"),))
    elif case == "forge-down":
        forge.unavailable_facts.add("pr")
    with pytest.raises(spec_loop.SpecLoopError) as exc:
        spec_loop.engineer_preflight(_operator(tmp_path, text), 7, ENGINEER_REPO, forge)
    assert needle in str(exc.value)
    if case == "forge-down":
        assert "не одобрено" not in str(exc.value)


def test_engineer_preflight_reads_no_local_git(tmp_path, monkeypatch) -> None:  # T24
    # FakeForge не реализует ни одного git-метода Ops: любое чтение локального
    # git упало бы AttributeError — решение принимается только по форджу.
    forge = consistent_world(monkeypatch)
    spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)


def _engineer_args(*extra: str) -> list[str]:
    return [
        "--subject",
        "Fleet Inbox",
        "--repo",
        "alpha",
        "--need",
        "--frame",
        "engineer",
        "--stakeholder",
        "product owner",
        *extra,
    ]


def test_engineer_new_run_passes_intake_and_upstream_blob(
    runs_root, tmp_path, monkeypatch
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _engineer_ops(monkeypatch)
    rc = spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
    )
    assert rc == 0
    kwargs = env.calls[0][1]
    spec = kwargs["interview_spec"]
    assert (spec.frame, spec.traces_to) == ("engineer", "upstream.md")
    intake = kwargs["engineer_intake"]
    assert spec.upstream_blob == intake.blob and intake.buffer == SIGNED.encode()


def test_engineer_preflight_refusal_creates_no_run(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _engineer_ops(monkeypatch)
    rc = spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path, DRAFT), "--approval-pr", "7")
    )
    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []


def test_engineer_next_to_customer_run_needs_new_run(  # T12
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="brief_ready")
    _engineer_ops(monkeypatch)
    rc = spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
    )
    assert rc == 1 and env.calls == []
    assert "--new-run --ws-id" in capsys.readouterr().out
    rc = spec_loop.main(
        _engineer_args(
            "--traces-to",
            _operator(tmp_path),
            "--approval-pr",
            "7",
            "--new-run",
            "--ws-id",
            "ws-eng",
        )
    )
    assert rc == 0 and env.calls[0][1]["ws_id"] == "ws-eng"


def test_engineer_session_flag_refused(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(
        _engineer_args("--traces-to", "x.md", "--approval-pr", "7", "--session", "s")
    )
    assert rc == 1 and env.calls == []
    assert "--session" in capsys.readouterr().out


def test_engineer_repeat_with_other_approval_pr_refuses(
    runs_root, tmp_path, monkeypatch, capsys
) -> None:
    env = _LoopEnv(monkeypatch, tmp_path)
    st = rs.new_run(
        subject="Fleet Inbox",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="ws-eng",
        target_dir=str(env.target),
        bundle_dir="workstreams/ws-eng/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-eng",
        merge_authority="human",
        interview={
            **iv.InterviewSpec(
                "engineer", "product owner", "owner/alpha", "upstream.md", "b" * 40
            ).as_state(),
            "session_id": "s-r-eng-e",
            "approval_pr": 7,
        },
    )
    st.status = "waiting_interview"
    rs.save(st)
    before = (rs.run_dir("r-eng") / "run.json").read_bytes()
    _engineer_ops(monkeypatch)
    rc = spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "8")
    )
    assert rc == 1 and env.calls == []
    assert "--approval-pr" in capsys.readouterr().out
    assert (rs.run_dir("r-eng") / "run.json").read_bytes() == before


# --- ревью части B, круг 1 (B5): матрица preflight на уровне spec-loop ---

from tests.approval_request_cases import DEFECTS  # noqa: E402
from tests.forge_fake import C1, DIR, MERGE  # noqa: E402
from tests.provenance_cases import (  # noqa: E402
    ACT_DEFECTS,
    ACT_UNAVAILABLE,
    OPERATOR_DEFECTS,
    POLICY_DEFECTS,
    POLICY_UNAVAILABLE,
    ids,
)

_REQ = f"{DIR}/approval-request.yaml"

#: Весь набор дефектов предикатов (Task 4) — через полный вход spec-loop:
#: новая форма в `tests/provenance_cases.py` проверяется здесь автоматически.
_PREFLIGHT_DEFECTS = (
    [(f"act-{i}", spoil, SIGNED, reason) for i, spoil, reason in ACT_DEFECTS]
    + [(f"operator-{i}", spoil, text, r) for i, spoil, text, r in OPERATOR_DEFECTS]
    + [(f"policy-{i}", spoil, SIGNED, reason) for i, spoil, reason in POLICY_DEFECTS]
)
#: Гейт upstream (§11.4.2 п.1) срабатывает раньше чтения акта.
_GATE_FIRST = {"operator-draft", "operator-crlf"}


def _main_engineer(tmp_path, monkeypatch, spoil=None, text: str = SIGNED):
    """Полный вход `spec_loop.main` engineer-прогона над миром форджа."""
    env = _LoopEnv(monkeypatch, tmp_path)
    forge = _engineer_ops(monkeypatch)
    if spoil is not None:
        spoil(forge)
    operator = _operator(tmp_path, text)
    rc = spec_loop.main(_engineer_args("--traces-to", operator, "--approval-pr", "7"))
    return env, rc


@pytest.mark.parametrize(
    ("case", "spoil", "text", "reason"),
    _PREFLIGHT_DEFECTS,
    ids=[d[0] for d in _PREFLIGHT_DEFECTS],
)
def test_engineer_main_refuses_every_provenance_defect(  # T14–T22, T25–T27
    runs_root, tmp_path, monkeypatch, capsys, case, spoil, text, reason
) -> None:
    env, rc = _main_engineer(tmp_path, monkeypatch, spoil, text)
    out = capsys.readouterr().out
    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []
    assert ("upstream не годится" if case in _GATE_FIRST else f"{reason}:") in out


def test_engineer_main_accepts_consistent_world(  # двойник матрицы; T17
    runs_root, tmp_path, monkeypatch
) -> None:
    # Конверт фикстуры — точное зеркало мержа; кем он вписан, не проверяется.
    env, rc = _main_engineer(tmp_path, monkeypatch)
    assert rc == 0 and [c[0] for c in env.calls] == ["start"]


@pytest.mark.parametrize("fact", ACT_UNAVAILABLE + POLICY_UNAVAILABLE)
def test_engineer_main_unavailable_each_fact(  # T31
    runs_root, tmp_path, monkeypatch, capsys, fact
) -> None:
    env, rc = _main_engineer(
        tmp_path, monkeypatch, lambda f: f.unavailable_facts.add(fact)
    )
    out = capsys.readouterr().out
    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []
    assert "повторите" in out and "не одобрено" not in out


@pytest.mark.parametrize("spoiled", [False, True], ids=["authentic", "merger-out"])
def test_engineer_main_decides_by_forge_not_local_git(  # T24
    runs_root, tmp_path, monkeypatch, spoiled
) -> None:
    """Цель — настоящий git-чекаут с верным origin, но с ПОДДЕЛЬНОЙ локальной
    историей: «смерженные» бриф и заявка, ветка brief/WS-1. Настоящий
    `_origin_url` читает origin; любой другой вызов git падает. Решение —
    ровно по форджу: подлинный акт принят, испорченный — нет."""
    import subprocess

    from governance import approval_request as _ar
    from tests.forge_fake import request as _request

    real_origin = spec_loop._origin_url
    env = _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(spec_loop, "_origin_url", real_origin)
    target = env.target
    (target / ".git").rmdir()
    forged = target / DIR
    forged.mkdir(parents=True)
    (forged / "brief.md").write_text(SIGNED, encoding="utf-8")
    (forged / "approval-request.yaml").write_text(
        _ar.render(_request()), encoding="utf-8"
    )
    for argv in (
        ["init", "-q", "-b", "main"],
        ["remote", "add", "origin", "git@github.com:owner/alpha.git"],
        ["add", "-A"],
        ["-c", "user.name=h", "-c", "user.email=h@x", "commit", "-qm", "Merge #7"],
        ["branch", "brief/WS-1"],
    ):
        subprocess.run(["git", "-C", str(target), *argv], check=True)
    real_run = subprocess.run
    origin_reads: list[list[str]] = []

    def only_origin(argv, *args, **kwargs):
        if argv[0] == "git":
            assert argv[-3:] == ["remote", "get-url", "origin"], argv
            origin_reads.append(argv)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", only_origin)
    forge = _engineer_ops(monkeypatch)
    if spoiled:
        ACT_DEFECTS[ids(ACT_DEFECTS).index("merger-out-of-p")][1](forge)
    rc = spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
    )
    assert origin_reads  # настоящий путь чтения origin пройден
    assert rc == (1 if spoiled else 0)


def test_preflight_facts_carry_no_pr_body_or_labels() -> None:  # T23
    """Метки/тело PR, изменённые после мержа, не меняют решения: факт PR их не
    несёт и запрос форджа их не читает — доказательство только в коммите."""
    import dataclasses

    from governance import brief_facts

    names = {f.name for f in dataclasses.fields(brief_facts.BriefPrFacts)}
    assert not names & {"body", "labels", "title"}
    query = brief_facts._BRIEF_PR_QUERY
    assert "labels" not in query and "body" not in query and "title" not in query


def test_engineer_preflight_valid_reconfirm_passes(
    tmp_path, monkeypatch
) -> None:  # T27
    forge = consistent_world(monkeypatch)
    forge.add_policy(C1)
    forge.reconfirm(C1)
    assert spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)


@pytest.mark.parametrize(
    "spoil",
    [
        ("gate-fail", lambda t: t.replace("gate_passed: true", "gate_passed: false")),
        ("path-traces", lambda t: t.replace("traces_to: []", "traces_to: [notes.md]")),
        (
            "blocking",
            lambda t: t.replace(
                "blocking_open_questions: 0", "blocking_open_questions: 2"
            ),
        ),
    ],
    ids=lambda v: v[0] if isinstance(v, tuple) else v,
)
def test_engineer_preflight_upstream_gate_refusals(
    tmp_path, monkeypatch, spoil
) -> None:  # T32
    forge = consistent_world(monkeypatch)
    text = spoil[1](SIGNED)
    assert text != SIGNED
    with pytest.raises(spec_loop.SpecLoopError, match="upstream не годится"):
        spec_loop.engineer_preflight(_operator(tmp_path, text), 7, ENGINEER_REPO, forge)


@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_engineer_preflight_ambiguous_merged_request(  # T21a/T21b через preflight
    tmp_path, monkeypatch, case, mutate, _m
) -> None:
    from governance import approval_request as _ar
    from tests.forge_fake import request as _request

    forge = consistent_world(monkeypatch)
    forge.files[(MERGE, _REQ)] = mutate(_ar.render(_request()))
    with pytest.raises(spec_loop.SpecLoopError, match="request_invalid"):
        spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)


def test_engineer_stop_without_session_is_not_an_orphan(  # Task 9
    runs_root, tmp_path, monkeypatch
) -> None:
    """Стоп перепроверки ДО первого `start` (например, фордж недоступен):
    у engineer `session_id` ещё пуст, но это не сирота — повтор идёт в resume."""
    env = _LoopEnv(monkeypatch, tmp_path)
    st = rs.new_run(
        subject="Fleet Inbox",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="ws-eng",
        target_dir=str(env.target),
        bundle_dir="workstreams/ws-eng/spec",
        profile="profiles/team-exp.yaml",
        run_id="r-eng",
        merge_authority="human",
        interview={
            **iv.InterviewSpec(
                "engineer", "product owner", "owner/alpha", "upstream.md", "b" * 40
            ).as_state(),
            "approval_pr": 7,
        },
    )
    st.status = "stopped_interview"
    rs.save(st)
    env.resume_result = st
    _engineer_ops(monkeypatch)
    spec_loop.main(
        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
    )
    assert [c[0] for c in env.calls] == ["resume"]
