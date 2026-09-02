"""Тесты RealOps — точные команды внешних эффектов (спека §5/§8, Task 3 Step 1).

Стратегия: `governance.ops.subprocess.run` подменяется фейком, который
записывает argv/cwd/env каждого вызова и отдаёт заданные
stdout/stderr/returncode. Живых `git`/`gh`/`codex`/`gate-check` вызовов нет —
проверяется только обвязка RealOps: что она строит и как разбирает результат.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import governance.ops as ops_mod
from governance.ops import RealOps

REPO_SLUG = "andrei-shtanakov/devtools"


class RecordedCall:
    """Один перехваченный вызов ``subprocess.run`` — argv и остальные kwargs."""

    def __init__(self, argv: list, kwargs: dict) -> None:
        self.argv = argv
        self.kwargs = kwargs


def _install_fake_run(monkeypatch, *, returncode=0, stdout="", stderr=""):
    """Подменяет ``governance.ops.subprocess.run`` фейком без живых вызовов."""
    calls: list[RecordedCall] = []

    def fake_run(argv, **kwargs):
        calls.append(RecordedCall(list(argv), kwargs))
        return subprocess.CompletedProcess(
            argv, returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    return calls


# --- Кейс 1: merge -----------------------------------------------------


def test_merge_command_and_env_rc0_true(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    result = ops.merge(REPO_SLUG, 42, "deadbeef")

    assert result is True
    assert len(calls) == 1
    call = calls[0]
    assert call.argv == [
        "gh", "api", "-X", "PUT",
        f"repos/{REPO_SLUG}/pulls/42/merge",
        "-f", "merge_method=merge",
        "-f", "sha=deadbeef",
    ]
    assert call.kwargs["env"]["GH_CONFIG_DIR"] == str(
        Path.home() / ".config" / "review"
    )


def test_merge_rc_nonzero_returns_false_not_exception(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1)
    ops = RealOps()

    result = ops.merge(REPO_SLUG, 42, "deadbeef")

    assert result is False


# --- Кейс 2: review ------------------------------------------------------


def test_review_command_cwd_and_passthrough_returncode(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=3)
    ops = RealOps()

    result = ops.review("devtools", 7)

    assert result == 3
    call = calls[0]
    assert call.argv == [
        "sh", str(ops_mod.DEVTOOLS_ROOT / "review-pr.sh"), "devtools", "7",
    ]
    assert call.kwargs["cwd"] == ops_mod.DEVTOOLS_ROOT


# --- Кейс 2a: find_pr — сбой запроса ≠ «PR нет» (F-5, круг 2) -------------


def test_find_pr_rc_nonzero_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stderr="rate limited")
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.find_pr(REPO_SLUG, "feat/x")


def test_find_pr_invalid_json_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="not json")
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.find_pr(REPO_SLUG, "feat/x")


def test_find_pr_valid_empty_list_returns_none(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="[]")
    ops = RealOps()

    result = ops.find_pr(REPO_SLUG, "feat/x")

    assert result is None


def test_find_pr_valid_list_returns_number(monkeypatch):
    _install_fake_run(
        monkeypatch, returncode=0, stdout=json.dumps([{"number": 42}]),
    )
    ops = RealOps()

    result = ops.find_pr(REPO_SLUG, "feat/x")

    assert result == 42


# --- Кейс 3: create_draft_pr ---------------------------------------------


def test_create_draft_pr_command_has_required_label_and_parses_number(
    monkeypatch,
):
    calls = _install_fake_run(
        monkeypatch,
        returncode=0,
        stdout="https://github.com/andrei-shtanakov/devtools/pull/42\n",
    )
    ops = RealOps()

    number = ops.create_draft_pr(
        "/tmp/devtools", REPO_SLUG, "feat/x", "title", "body", "",
    )

    assert number == 42
    call = calls[0]
    assert call.argv[:3] == ["gh", "pr", "create"]
    assert "--draft" in call.argv
    assert "-R" in call.argv and REPO_SLUG in call.argv
    # пустой label не передаётся вовсе (решение владельца 2026-08-31:
    # лейбл codex-review снят — не триггерить платный CI-контур)
    assert "--label" not in call.argv
    assert call.kwargs["cwd"] == "/tmp/devtools"


# --- Кейс 4: gate_check_s8 (мини-характеризация) -------------------------


def test_gate_check_s8_command_matches_real_cli(monkeypatch):
    """`gate-check --help` (пинованный steward, прогнан вручную) не знает
    `--bundle` — bundle_dir передаётся ПОЗИЦИОННЫМ аргументом (`[spec_dir]`,
    default: spec); `--profile <str>` и `--emit-verdicts` — обычные опции.
    Реальный usage: ``gate-check [OPTIONS] [spec_dir]``.
    """
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="ok\n")
    ops = RealOps()

    result = ops.gate_check_s8("/tmp/devtools", "/tmp/devtools/spec", "lite")

    assert result == (0, "ok\n")
    call = calls[0]
    assert call.argv[-4:] == [
        "/tmp/devtools/spec", "--profile", "lite", "--emit-verdicts",
    ]
    assert call.argv[0].endswith("gate-check")
    assert call.kwargs["cwd"] == "/tmp/devtools"
    assert call.kwargs["capture_output"] is True


def test_gate_check_s8_returns_combined_output_on_failure(monkeypatch):
    """M-2: findings текста, не только код возврата — идёт в леджер и issue."""
    calls = _install_fake_run(
        monkeypatch, returncode=1, stdout="GC-X: bad\n", stderr="warn\n",
    )
    ops = RealOps()

    result = ops.gate_check_s8("/tmp/devtools", "/tmp/devtools/spec", "lite")

    assert result == (1, "GC-X: bad\nwarn\n")
    assert len(calls) == 1


# --- Кейс 8: commit_paths ---------------------------------------------------


def test_commit_paths_adds_only_given_paths_and_commits_with_message(monkeypatch):
    """Круг 5: `git add -- <paths>`, не `git add -A` — не сгребает чужие
    незакоммиченные изменения в target_dir."""
    calls = _install_fake_run(monkeypatch, returncode=1)  # diff --cached: dirty
    ops = RealOps()

    ops.commit_paths(
        "/tmp/devtools", ["workstreams/WS-1/spec"],
        "docs(governance): x\n\nCo-Authored-By: y",
    )

    assert [c.argv[:2] for c in calls] == [
        ["git", "add"], ["git", "diff"], ["git", "commit"],
    ]
    assert calls[0].argv == ["git", "add", "--", "workstreams/WS-1/spec"]
    assert calls[0].kwargs["cwd"] == "/tmp/devtools"
    assert calls[2].argv == [
        "git", "commit", "-m", "docs(governance): x\n\nCo-Authored-By: y",
    ]
    assert calls[2].kwargs["check"] is True


def test_commit_paths_multiple_paths(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=1)
    ops = RealOps()

    ops.commit_paths("/tmp/devtools", ["a/spec", "b/spec"], "message")

    assert calls[0].argv == ["git", "add", "--", "a/spec", "b/spec"]


def test_commit_paths_empty_index_does_not_commit(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)  # diff --cached: clean
    ops = RealOps()

    ops.commit_paths("/tmp/devtools", ["workstreams/WS-1/spec"], "message")

    assert [c.argv[:2] for c in calls] == [["git", "add"], ["git", "diff"]]


# --- Кейс 5: author -------------------------------------------------------


def test_author_command_and_prompt_contains_fields(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    result = ops.author("/tmp/devtools", "adr", "eco-099", "/tmp/devtools/spec")

    assert result == 0
    call = calls[0]
    assert call.argv[:5] == [
        "codex", "exec", "--ephemeral", "--sandbox", "workspace-write",
    ]
    prompt = call.argv[5]
    assert "adr" in prompt
    assert "eco-099" in prompt
    assert "/tmp/devtools/spec" in prompt
    assert call.kwargs["cwd"] == "/tmp/devtools"


def test_author_prompt_carries_dsl_and_filenames(monkeypatch):
    """Промпт несёт канонические имена файлов и DSL гейта (боевой прогон
    kapelle#47: без них codex писал в своём диалекте)."""
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    ops.author("/t", "requirements", "s", "ws/spec")
    ops.author("/t", "behaviour-spec", "s", "ws/spec")

    req_prompt = calls[0].argv[5]
    assert "ws/spec/10-requirements.md" in req_prompt
    assert "#### FR-NN:" in req_prompt
    assert "**Priority**: Must" in req_prompt
    assert "upstream_hashes" in req_prompt and "git hash-object" in req_prompt

    beh_prompt = calls[1].argv[5]
    assert "ws/spec/15-behaviour-spec.md" in beh_prompt
    assert "#### BEH-NN:" in beh_prompt
    assert "traces: [FR-NN" in beh_prompt
    assert "checked_by" in beh_prompt


# --- B2 Task 2: author_disp — opt-in бэкенд disp (спека §5, OQ-1) ----------


def test_author_disp_document_pipeline_command(monkeypatch):
    """Контур вида document (disputatio#52 -> PR #64): disp pipeline run
    со slug/config/root, не суррогат run --mode develop."""
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    result = ops.author_disp(
        "/tmp/devtools", "subject='x' bundle=spec/15.md",
        "beh-ws-1", "/runs/r1/disp-doc.toml",
    )

    assert result == 0
    call = calls[0]
    expected_project = str(ops_mod.DEVTOOLS_ROOT.parent / "disputatio")
    assert call.argv == [
        "uv", "run", "--project", expected_project,
        "disp", "pipeline", "run",
        "--task", "subject='x' bundle=spec/15.md",
        "--slug", "beh-ws-1",
        "--config", "/runs/r1/disp-doc.toml",
        "--root", "/tmp/devtools",
    ]
    assert call.kwargs["cwd"] == "/tmp/devtools"


def test_author_disp_returncode_passthrough(monkeypatch):
    _install_fake_run(monkeypatch, returncode=2)
    ops = RealOps()

    result = ops.author_disp("/tmp/devtools", "task", "s", "/tmp/c.toml")

    assert result == 2


def test_latest_review_body_honours_review_login_env(monkeypatch):
    """Личность ревьюера — env REVIEW_LOGIN (запаркованный minor PR #102):
    дефолт ai-prosto, override подхватывается в jq-фильтре."""
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="review body\n")
    ops = RealOps()

    assert ops.latest_review_body("o/r", 7) == "review body"
    assert 'select(.user.login == "ai-prosto")' in calls[0].argv[-1]

    monkeypatch.setenv("REVIEW_LOGIN", "other-bot")
    ops.latest_review_body("o/r", 7)
    assert 'select(.user.login == "other-bot")' in calls[1].argv[-1]


# --- Кейс 6: unresolved_threads -------------------------------------------


def test_unresolved_threads_true_when_open_thread_present(monkeypatch):
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {"isResolved": True},
                            {"isResolved": False},
                        ],
                    }
                }
            }
        }
    }
    calls = _install_fake_run(
        monkeypatch, returncode=0, stdout=json.dumps(payload)
    )
    ops = RealOps()

    result = ops.unresolved_threads(REPO_SLUG, 42)

    assert result is True
    call = calls[0]
    assert call.argv[:3] == ["gh", "api", "graphql"]
    query = call.argv[call.argv.index("-f") + 1]
    assert "reviewThreads" in query
    assert "isResolved" in query
    assert "hasNextPage" in query


def test_unresolved_threads_false_when_all_resolved(monkeypatch):
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"isResolved": True}],
                    }
                }
            }
        }
    }
    _install_fake_run(monkeypatch, returncode=0, stdout=json.dumps(payload))
    ops = RealOps()

    result = ops.unresolved_threads(REPO_SLUG, 42)

    assert result is False


def test_unresolved_threads_none_on_error_not_false(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stderr="boom")
    ops = RealOps()

    result = ops.unresolved_threads(REPO_SLUG, 42)

    assert result is None


def test_unresolved_threads_none_when_more_pages_exist(monkeypatch):
    """Круг 7 (codex-major): первая страница целиком resolved, но
    `hasNextPage=true` — сотый+ thread мог быть неразрешён, результат
    обязан быть `None` (unknown), не оптимистичное `False`."""
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True},
                        "nodes": [{"isResolved": True}] * 100,
                    }
                }
            }
        }
    }
    _install_fake_run(monkeypatch, returncode=0, stdout=json.dumps(payload))
    ops = RealOps()

    result = ops.unresolved_threads(REPO_SLUG, 42)

    assert result is None


# --- Кейс 7: create_issue --------------------------------------------------


def test_create_issue_command_and_parses_number(monkeypatch):
    calls = _install_fake_run(
        monkeypatch,
        returncode=0,
        stdout="https://github.com/andrei-shtanakov/devtools/issues/99\n",
    )
    ops = RealOps()

    number = ops.create_issue(REPO_SLUG, "title", "body")

    assert number == 99
    call = calls[0]
    assert call.argv[:3] == ["gh", "issue", "create"]
    assert "-R" in call.argv and REPO_SLUG in call.argv
    label_idx = call.argv.index("--label")
    assert call.argv[label_idx + 1] == "inbox"


# --- Кейс 9: find_issue — реконсиляция remediation-issue (круг 3) ---------


def test_find_issue_command_shape(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="[]")
    ops = RealOps()

    ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")

    call = calls[0]
    assert call.argv[:3] == ["gh", "issue", "list"]
    assert "-R" in call.argv and REPO_SLUG in call.argv
    label_idx = call.argv.index("--label")
    assert call.argv[label_idx + 1] == "inbox"
    state_idx = call.argv.index("--state")
    assert call.argv[state_idx + 1] == "open"


def test_find_issue_returns_number_when_body_matches_prefix(monkeypatch):
    payload = [
        {"number": 10, "body": "unrelated\n"},
        {"number": 42, "body": "slug: beh-remediation-WS-1\nfrom: x\n"},
    ]
    _install_fake_run(monkeypatch, returncode=0, stdout=json.dumps(payload))
    ops = RealOps()

    result = ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")

    assert result == 42


def test_find_issue_returns_none_when_no_match(monkeypatch):
    payload = [{"number": 10, "body": "unrelated\n"}]
    _install_fake_run(monkeypatch, returncode=0, stdout=json.dumps(payload))
    ops = RealOps()

    result = ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")

    assert result is None


def test_find_issue_rc_nonzero_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stderr="boom")
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")


def test_find_issue_invalid_json_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="not json")
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")


# --- Кейс 10: is_dirty — fail-closed гард S1 (круг 5) -----------------------


def test_is_dirty_true_on_nonempty_porcelain(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout=" M foo.py\n")
    ops = RealOps()

    result = ops.is_dirty("/tmp/devtools")

    assert result is True
    assert calls[0].argv == ["git", "status", "--porcelain"]
    assert calls[0].kwargs["cwd"] == "/tmp/devtools"


def test_is_dirty_false_on_empty_porcelain(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="")
    ops = RealOps()

    result = ops.is_dirty("/tmp/devtools")

    assert result is False


# --- Кейс 11: checkout_and_pull — S8 на default-ветке (круг 5) -------------


def test_checkout_and_pull_switch_then_pull_ff_only(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    ops.checkout_and_pull("/tmp/devtools", "master")

    assert [c.argv for c in calls] == [
        ["git", "switch", "master"],
        ["git", "pull", "--ff-only"],
    ]
    assert calls[0].kwargs["cwd"] == "/tmp/devtools"
    assert calls[1].kwargs["cwd"] == "/tmp/devtools"


def test_checkout_and_pull_switch_failure_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stderr="unknown branch")
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.checkout_and_pull("/tmp/devtools", "master")


def test_checkout_and_pull_pull_failure_raises_runtime_error(monkeypatch):
    calls_seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls_seen.append(list(argv))
        rc = 0 if argv[1] == "switch" else 1
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="diverged")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    with pytest.raises(RuntimeError):
        ops.checkout_and_pull("/tmp/devtools", "master")
    assert len(calls_seen) == 2  # switch ran, then pull failed


def test_author_disp_resumes_when_state_exists(monkeypatch, tmp_path):
    """Приёмка PR #106, круг 3 (major): состояние .disputatio/<slug> на
    диске → pipeline resume, не повторный run по занятому slug."""
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()
    (tmp_path / ".disputatio" / "pipelines" / "beh-r1").mkdir(parents=True)

    ops.author_disp(str(tmp_path), "task", "beh-r1", "/tmp/c.toml")
    # сперва status (integrity), затем чистый resume; --adopt-external
    # без внешних правок недопустим
    assert calls[0].argv[4:7] == ["disp", "pipeline", "status"]
    assert calls[1].argv[4:7] == ["disp", "pipeline", "resume"]
    assert "--adopt-external" not in calls[1].argv
    assert "--task" not in calls[1].argv

    ops.author_disp(str(tmp_path), "task", "beh-other", "/tmp/c.toml")
    assert calls[2].argv[4:7] == ["disp", "pipeline", "run"]


def test_author_disp_resume_falls_back_to_adopt_external(
    monkeypatch, tmp_path
):
    """Приёмка PR #106, круги 5–6: отказ чистого resume (внешняя правка) →
    одна повторная попытка с --adopt-external; --discard-round — никогда."""
    import types

    (tmp_path / ".disputatio" / "pipelines" / "beh-r1").mkdir(parents=True)
    script = [(0, "phase: DOC_LOOP\n"), (3, ""), (3, "")]
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        rc_, out = script.pop(0)
        return types.SimpleNamespace(returncode=rc_, stdout=out, stderr="")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    rc = ops.author_disp(str(tmp_path), "task", "beh-r1", "/tmp/c.toml")

    assert rc == 3
    assert seen[1][4:7] == ["disp", "pipeline", "resume"]
    assert "--adopt-external" not in seen[1]
    assert "--adopt-external" in seen[2]
    assert not any("--discard-round" in c for c in seen)


def test_author_disp_terminal_phase_via_status(monkeypatch, tmp_path):
    """Приёмка PR #106, круги 11–12: фаза берётся у `disp pipeline status`
    (он верифицирует integrity anchor — прямое чтение pipeline.json обходило
    бы защиту от подмены): DONE => 0, FAILED => 1, отказ status — как есть,
    нетерминальная фаза — resume-поток."""
    import types

    (tmp_path / ".disputatio" / "pipelines" / "beh-r1").mkdir(parents=True)
    script: list[tuple[int, str]] = []
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        rc, out = script.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    script[:] = [(0, "kind: document\nphase: DONE\n")]
    assert ops.author_disp(str(tmp_path), "t", "beh-r1", "/c.toml") == 0
    assert seen[-1][4:7] == ["disp", "pipeline", "status"]

    script[:] = [(0, "phase: FAILED\n")]
    assert ops.author_disp(str(tmp_path), "t", "beh-r1", "/c.toml") == 1

    script[:] = [(4, "")]  # anchor/integrity провал — код как есть
    assert ops.author_disp(str(tmp_path), "t", "beh-r1", "/c.toml") == 4

    script[:] = [(0, "phase: DOC_LOOP\n"), (0, "")]
    assert ops.author_disp(str(tmp_path), "t", "beh-r1", "/c.toml") == 0
    assert seen[-1][4:7] == ["disp", "pipeline", "resume"]


# --- Кейс 12: current_branch / materialize_pr_head (ретроспектива 09-02) ----


def test_current_branch_returns_name(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="master\n")
    ops = RealOps()
    assert ops.current_branch("/tmp/kapelle") == "master"
    assert calls[0].argv == ["git", "branch", "--show-current"]
    assert calls[0].kwargs["cwd"] == "/tmp/kapelle"


def test_current_branch_detached_returns_none(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="\n")
    ops = RealOps()
    assert ops.current_branch("/tmp/kapelle") is None


def test_materialize_pr_head_fetch_then_detach(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()
    ops.materialize_pr_head("/tmp/kapelle", 59, "cafe" * 10)
    assert calls[0].argv == ["git", "fetch", "origin", "pull/59/head"]
    # --no-overwrite-ignore (приёмка PR #113, круг 5): голый switch молча
    # перезаписал бы ignored-файл оператора версией из PR.
    assert calls[1].argv == [
        "git", "switch", "--no-overwrite-ignore", "--detach", "cafe" * 10,
    ]
    assert all(c.kwargs["cwd"] == "/tmp/kapelle" for c in calls)


def test_materialize_pr_head_fetch_failure_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        rc = 128 if argv[:2] == ["git", "fetch"] else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()
    with pytest.raises(RuntimeError, match="fetch"):
        ops.materialize_pr_head("/tmp/kapelle", 59, "cafe" * 10)


def test_materialize_pr_head_switch_failure_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        rc = 1 if argv[:2] == ["git", "switch"] else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()
    with pytest.raises(RuntimeError, match="switch"):
        ops.materialize_pr_head("/tmp/kapelle", 59, "cafe" * 10)


def test_changed_paths_fetch_base_then_three_dot_diff(monkeypatch):
    calls = _install_fake_run(
        monkeypatch, returncode=0, stdout="lib/a.py\nlib/b.py\n"
    )
    ops = RealOps()
    paths = ops.changed_paths("/tmp/kapelle", "master")
    assert calls[0].argv == ["git", "fetch", "origin", "master"]
    # FETCH_HEAD, не origin/master (приёмка PR #113, круг 4): fetch без
    # destination-refspec не обязан обновить remote-tracking ref, а
    # FETCH_HEAD пишется именно этим fetch — база доказуемо свежая.
    assert calls[1].argv == [
        "git", "diff", "--name-only", "FETCH_HEAD...HEAD",
    ]
    assert all(c.kwargs["cwd"] == "/tmp/kapelle" for c in calls)
    assert paths == ["lib/a.py", "lib/b.py"]


def test_changed_paths_fetch_failure_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        rc = 128 if argv[:2] == ["git", "fetch"] else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()
    with pytest.raises(RuntimeError, match="fetch"):
        ops.changed_paths("/tmp/kapelle", "master")


def test_changed_paths_diff_failure_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        rc = 129 if argv[:2] == ["git", "diff"] else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()
    with pytest.raises(RuntimeError, match="diff"):
        ops.changed_paths("/tmp/kapelle", "master")


# --- Кейс 13: collect_gate_verdicts (@id:runner-s8-verdicts-cleanup) --------


def test_collect_gate_verdicts_moves_file_and_prunes_empty_dir(tmp_path):
    target = tmp_path / "target"
    (target / ".steward").mkdir(parents=True)
    src = target / ".steward" / "gate_verdicts.jsonl"
    src.write_text('{"gate": "ok"}\n', encoding="utf-8")
    dest = tmp_path / "runs" / "r-1" / "s8-gate-verdicts.jsonl"
    ops = RealOps()

    assert ops.collect_gate_verdicts(str(target), str(dest)) is True
    assert not src.exists()
    assert not (target / ".steward").exists()
    assert dest.read_text(encoding="utf-8") == '{"gate": "ok"}\n'


def test_collect_gate_verdicts_keeps_nonempty_steward_dir(tmp_path):
    target = tmp_path / "target"
    (target / ".steward").mkdir(parents=True)
    (target / ".steward" / "gate_verdicts.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    (target / ".steward" / "other.txt").write_text("x", encoding="utf-8")
    dest = tmp_path / "dest.jsonl"
    ops = RealOps()

    assert ops.collect_gate_verdicts(str(target), str(dest)) is True
    assert (target / ".steward" / "other.txt").exists()


def test_collect_gate_verdicts_absent_returns_false(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    ops = RealOps()

    assert (
        ops.collect_gate_verdicts(str(target), str(tmp_path / "d.jsonl"))
        is False
    )
    assert not (tmp_path / "d.jsonl").exists()
