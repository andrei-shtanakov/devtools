"""Тесты RealOps — точные команды внешних эффектов (спека §5/§8, Task 3 Step 1).

Стратегия: `governance.ops.subprocess.run` подменяется фейком, который
записывает argv/cwd/env каждого вызова и отдаёт заданные
stdout/stderr/returncode. Живых `git`/`gh`/`codex`/`gate-check` вызовов нет —
проверяется только обвязка RealOps: что она строит и как разбирает результат.
"""

from __future__ import annotations

import json
import shutil
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

    with pytest.raises(RuntimeError, match="gh pr list rc=1: rate limited"):
        ops.find_pr(REPO_SLUG, "feat/x")


def test_find_pr_invalid_json_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="not json")
    ops = RealOps()

    with pytest.raises(RuntimeError, match="invalid JSON: 'not json'"):
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
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")
    monkeypatch.delenv("AUTHOR_HARNESS", raising=False)
    monkeypatch.delenv("AUTHOR_MODEL", raising=False)
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
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", "/nonexistent")
    monkeypatch.delenv("AUTHOR_HARNESS", raising=False)
    monkeypatch.delenv("AUTHOR_MODEL", raising=False)
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


def test_author_disp_command_and_project_path(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    result = ops.author_disp("/tmp/devtools", "subject='x' bundle=spec/15.md")

    assert result == 0
    call = calls[0]
    expected_project = str(ops_mod.DEVTOOLS_ROOT.parent / "disputatio")
    assert call.argv == [
        "uv", "run", "--project", expected_project,
        "disp", "run", "--mode", "develop",
        "--root", "/tmp/devtools", "subject='x' bundle=spec/15.md",
    ]
    assert call.kwargs["cwd"] == "/tmp/devtools"


def test_author_disp_returncode_passthrough(monkeypatch):
    _install_fake_run(monkeypatch, returncode=2)
    ops = RealOps()

    result = ops.author_disp("/tmp/devtools", "task")

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

    with pytest.raises(RuntimeError, match="gh issue list rc=1: boom"):
        ops.find_issue(REPO_SLUG, "slug: beh-remediation-WS-1")


def test_find_issue_invalid_json_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="not json")
    ops = RealOps()

    with pytest.raises(RuntimeError, match="invalid JSON: 'not json'"):
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

    with pytest.raises(
        RuntimeError, match="git switch master rc=1: unknown branch"
    ):
        ops.checkout_and_pull("/tmp/devtools", "master")


def test_checkout_and_pull_pull_failure_raises_runtime_error(monkeypatch):
    calls_seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls_seen.append(list(argv))
        rc = 0 if argv[1] == "switch" else 1
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="diverged")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    with pytest.raises(
        RuntimeError, match="git pull --ff-only rc=1: diverged"
    ):
        ops.checkout_and_pull("/tmp/devtools", "master")
    assert len(calls_seen) == 2  # switch ran, then pull failed


# --- Кейс 11b: push_branch — сбой пуша сообщением, не трейсбеком -----------


def test_push_branch_command(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)

    RealOps().push_branch("/tmp/devtools", "spec/WS-alpha-7-tasks-v2")

    assert [c.argv for c in calls] == [
        ["git", "push", "-u", "origin", "spec/WS-alpha-7-tasks-v2"],
    ]
    assert calls[0].kwargs["cwd"] == "/tmp/devtools"


def test_push_branch_failure_raises_runtime_error(monkeypatch):
    """Отвергнутый push — RuntimeError со stderr git'а, не CalledProcessError.

    `main` ловит только `RuntimeError`; с `check=True` ходовой non-ff
    («ветка на remote ушла вперёд локальной») уходил оператору сырым
    трейсбеком, и текст git'а — единственная диагностика — терялся."""
    _install_fake_run(
        monkeypatch, returncode=1, stderr="! [rejected] (non-fast-forward)",
    )

    with pytest.raises(RuntimeError, match="non-fast-forward") as exc:
        RealOps().push_branch("/tmp/devtools", "spec/WS-alpha-7-tasks-v2")
    assert "rc=1" in str(exc.value)


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


# --- Кейс 14: харнесс авторинга (лимиты codex, парный к review-pr.sh) -------


def _author_hermetic(monkeypatch, tmp_path, cfg: str | None = None):
    path = tmp_path / "harness.env"
    if cfg is not None:
        path.write_text(cfg, encoding="utf-8")
    monkeypatch.setenv("AI_PROSTO_HARNESS_ENV", str(path))
    monkeypatch.delenv("AUTHOR_HARNESS", raising=False)
    monkeypatch.delenv("AUTHOR_MODEL", raising=False)


def test_author_config_flips_to_claude(monkeypatch, tmp_path):
    """Конфиг оператора переключает авторинг на claude: паритет с флотским
    пресетом spec-runner (skip-permissions — авторинг пишет бандл и считает
    git hash-object), изоляция от MCP/сессий оператора."""
    _author_hermetic(
        monkeypatch, tmp_path,
        "AUTHOR_HARNESS=claude\nAUTHOR_MODEL=claude-opus-5\n",
    )
    calls = _install_fake_run(monkeypatch, returncode=0)
    ops = RealOps()

    assert ops.author("/t", "charter", "s", "ws/spec") == 0
    argv = calls[0].argv
    assert argv[:4] == ["claude", "-p", "--model", "claude-opus-5"]
    for flag in ("--dangerously-skip-permissions",
                 "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    assert "ws/spec/00-charter.md" in argv[-1]  # промпт — последним


def test_author_claude_default_model(monkeypatch, tmp_path):
    _author_hermetic(monkeypatch, tmp_path, "AUTHOR_HARNESS=claude\n")
    calls = _install_fake_run(monkeypatch, returncode=0)
    RealOps().author("/t", "charter", "s", "ws/spec")
    argv = calls[0].argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_author_env_harness_ignores_config_model(monkeypatch, tmp_path):
    """Урок ревью PR #121: харнесс со слоя env не наследует модель слоя
    конфига — codex не получит claude-модель."""
    _author_hermetic(
        monkeypatch, tmp_path,
        "AUTHOR_HARNESS=claude\nAUTHOR_MODEL=claude-opus-5\n",
    )
    monkeypatch.setenv("AUTHOR_HARNESS", "codex")
    calls = _install_fake_run(monkeypatch, returncode=0)
    RealOps().author("/t", "charter", "s", "ws/spec")
    argv = calls[0].argv
    assert argv[:5] == [
        "codex", "exec", "--ephemeral", "--sandbox", "workspace-write",
    ]
    assert "-m" not in argv


def test_author_config_accepts_export_prefix(monkeypatch, tmp_path):
    _author_hermetic(
        monkeypatch, tmp_path, "  export AUTHOR_HARNESS=claude\n"
    )
    calls = _install_fake_run(monkeypatch, returncode=0)
    RealOps().author("/t", "charter", "s", "ws/spec")
    assert calls[0].argv[0] == "claude"


def test_author_codex_model_from_config(monkeypatch, tmp_path):
    _author_hermetic(
        monkeypatch, tmp_path,
        "AUTHOR_HARNESS=codex\nAUTHOR_MODEL=gpt-5.5\n",
    )
    calls = _install_fake_run(monkeypatch, returncode=0)
    RealOps().author("/t", "charter", "s", "ws/spec")
    argv = calls[0].argv
    assert argv[argv.index("-m") + 1] == "gpt-5.5"
    assert argv[-1].startswith("kind=charter")


def test_author_unknown_harness_is_config_error(monkeypatch, tmp_path, capsys):
    """Неизвестный харнесс — код 2 с причиной, не traceback: шаг authoring
    остаётся resumable после правки конфига."""
    _author_hermetic(monkeypatch, tmp_path, "AUTHOR_HARNESS=gemini\n")
    _install_fake_run(monkeypatch, returncode=0)
    assert RealOps().author("/t", "charter", "s", "ws/spec") == 2
    assert "gemini" in capsys.readouterr().out


# --- Task 2: DSL авторинга decomposition -----------------------------------


def test_author_dsl_covers_decomposition() -> None:
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["decomposition"] == "30-decomposition.md"
    dsl = _AUTHOR_DSL["decomposition"]
    for token in (
        "spec_stage: decomposition", "owner_role: tech-lead",
        "traces_to: [design, acceptance]", "#### DT-NN:", "type: implement|verify",
        "scenarios:", "depends_on:", "delivered_by:", "parallel_group:",
        "topological declaration order",
        # Major ревью PR #161, finding 1: `verifies:` — обязательное
        # структурное поле type: verify (owner ruling DT-14 multi-file
        # group) — промпт авторинга обязан его знать, иначе агент авторит
        # verify-DT без него и S4-гейт стопит КАЖДЫЙ такой бандл.
        "verifies:",
    ):
        assert token in dsl


def test_author_dsl_decomposition_explains_verifies_field() -> None:
    """Major ревью PR #161, finding 1: промпт учит и ФОРМЕ (список файлов,
    блочная YAML), и обязательности/запрету по type, и что это НАБЛЮДЕНИЕ
    (checked_by остаётся владением) — не только упоминает токен.

    Round 5 ревью PR #161, finding 3 (контракт владельца): промпт больше
    НЕ обещает "REQUIRED for type: verify" (verifies опционален — находка
    формы, не fatal-инвариант) и точно описывает УЗКОЕ правило single-owner
    исключения (владение всегда важнее наблюдения), а не «exempt for THIS
    task only» без уточнения про собственный checked_by.

    Round 10 ревью PR #161, major (контракт владельца): промпт обязан
    учить FATAL graph-инварианту замыкания (round 9) — владелец каждого
    файла из verifies обязан быть в транзитивном замыкании depends_on
    наблюдающей задачи, тот же контракт, что уже описан для delivered_by
    — иначе конформный по промпту бандл стопит S4-гейт (третий раз этот
    класс кусает).

    Round 11 ревью PR #161, минор (контракт владельца, выбран вариант
    «честная формулировка», не promotion в fatal): промпт больше НЕ
    обещает, что ЛЮБОЙ verifies-путь без владельца стопит доставку —
    это ДВУХУРОВНЕВАЯ гарантия (FATAL — только когда владелец есть и вне
    замыкания; путь БЕЗ владельца вовсе — non-fatal warning на гейте, не
    факт остановки deliver()).

    Round 14 ревью PR #161, минор (контракт владельца): промпт обещал
    single-owner ИСКЛЮЧЕНИЕ через verifies, которого в коде нет вовсе
    (round 4 сняло его целиком — single-owner решается ИСКЛЮЧИТЕЛЬНО
    checked_by, verifies цикл вообще не читает). Текст исправлен: verifies
    НЕ даёт никакого исключения ни в какой форме."""
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["decomposition"]
    verifies_tail = dsl.split("`verifies:")[1]
    assert "RECOMMENDED for type: verify" in verifies_tail[:200]
    assert "REQUIRED for type: verify" not in verifies_tail[:400]
    assert "FORBIDDEN for type: implement" in verifies_tail[:400]
    assert "checked_by" in verifies_tail[:400]
    assert "ownership always beats observation" in verifies_tail[:1200]
    assert "NO exemption from the single-owner invariant" in (
        verifies_tail[:1200]
    )
    assert "NOT also this same task's own checked_by target" not in dsl
    assert "FATAL graph invariant" in verifies_tail[:1600]
    assert (
        "transitive closure of THIS task's OWN depends_on"
        in verifies_tail[:1900]
    )
    assert "NON-fatal form recommendation" in verifies_tail[:2200]
    assert "does NOT stop delivery on its own" in verifies_tail[:2400]


def test_author_dsl_covers_acceptance() -> None:
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["acceptance"] == "25-acceptance.md"
    dsl = _AUTHOR_DSL["acceptance"]
    for token in (
        "spec_stage: acceptance", "owner_role: qa",
        "traces_to: [requirements, behaviour-spec]",
        "#### AC-NN:", "verification: test|manual|metric",
        "traces:", "scenarios:",
        "Must-требований во входном наборе нет",
    ):
        assert token in dsl


def test_decomposition_dsl_carries_acceptance_pin() -> None:
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["decomposition"]
    assert "traces_to: [design, acceptance]" in dsl
    assert "<hash25>" in dsl
    assert "25-acceptance.md" in dsl


def test_requirements_dsl_mandates_priority_for_nfr() -> None:
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["requirements"]
    nfr_part = dsl.split("NFR-NN")[1]
    assert "**Priority**" in nfr_part


# --- Task 1: провенанс и восстановление коммита -----------------------------


def test_ops_protocol_declares_provenance_primitives() -> None:
    from governance.ops import Ops

    for name in (
        "last_commit_touching", "prs_containing_commit",
        "rev_parse", "blob_in_commit", "commit_parent",
    ):
        assert hasattr(Ops, name), name


def test_real_ops_last_commit_touching_returns_none_without_history(
    tmp_path,
) -> None:
    """Пустой репо: `git log` даёт rc != 0, но ответ честно «истории нет»."""
    import subprocess as real_subprocess

    real_subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert RealOps().last_commit_touching(str(tmp_path), "nope.md") is None


def test_last_commit_touching_returns_sha_when_history_exists(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="abc123\n")
    ops = RealOps()

    result = ops.last_commit_touching("/tmp/devtools", "spec/x.md")

    assert result == "abc123"
    assert calls[0].argv == ["git", "-C", "/tmp/devtools", "rev-parse",
                              "--verify", "--quiet", "HEAD"]
    assert calls[1].argv == [
        "git", "-C", "/tmp/devtools", "log", "-1", "--format=%H",
        "--", "spec/x.md",
    ]


def test_last_commit_touching_never_touched_returns_none(monkeypatch):
    def fake_run(argv, **kwargs):
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="head\n",
                                                 stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    assert ops.last_commit_touching("/tmp/devtools", "never.md") is None


def test_last_commit_touching_log_failure_raises_runtime_error(monkeypatch):
    def fake_run(argv, **kwargs):
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="head\n",
                                                 stderr="")
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="boom")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    ops = RealOps()

    with pytest.raises(RuntimeError, match="git log"):
        ops.last_commit_touching("/tmp/devtools", "x.md")


# Нормализующее jq-выражение держится тестом ЦЕЛИКОМ: правка выражения
# обязана быть осознанной правкой теста. Подстрочные вхождения этого не
# удерживали — правка `.base.ref` → `.head.ref` проходила зелёной (F-13).
_PRS_CONTAINING_COMMIT_JQ = (
    "[.[] | {number, "
    'state: (if .merged_at then "MERGED" '
    "else (.state | ascii_upcase) end), "
    "baseRefName: .base.ref, mergedAt: .merged_at, "
    "mergeCommit: .merge_commit_sha}]"
)

# Форма живого ответа `repos/:slug/commits/:sha/pulls` (REST): snake_case,
# `state: open|closed`, `merged_by: null` даже у вмерженного PR. Ветки base и
# head различны намеренно — иначе подмена одной другой была бы незаметна.
_REST_COMMIT_PULLS_PAYLOAD = [
    {
        "number": 42,
        "state": "closed",
        "merged_at": "2026-09-01T00:00:00Z",
        "merge_commit_sha": "deadbeef",
        "base": {"ref": "master"},
        "head": {"ref": "feat/supersede-code"},
        "merged_by": None,
    },
    {
        "number": 43,
        "state": "open",
        "merged_at": None,
        "merge_commit_sha": None,
        "base": {"ref": "master"},
        "head": {"ref": "fix/other"},
        "merged_by": None,
    },
]


def test_prs_containing_commit_command_and_normalization(monkeypatch):
    payload = [
        {"number": 42, "state": "MERGED", "baseRefName": "master",
         "mergedAt": "2026-09-01T00:00:00Z", "mergeCommit": "deadbeef"},
    ]
    calls = _install_fake_run(
        monkeypatch, returncode=0, stdout=json.dumps(payload),
    )
    ops = RealOps()

    result = ops.prs_containing_commit(REPO_SLUG, "deadbeef")

    assert result == payload
    assert calls[0].argv == [
        "gh", "api", f"repos/{REPO_SLUG}/commits/deadbeef/pulls",
        "--paginate", "--jq", _PRS_CONTAINING_COMMIT_JQ,
    ]


def test_prs_containing_commit_collects_every_page(monkeypatch):
    """Кандидаты §I7 собираются со ВСЕХ страниц, а не с первой.

    Эндпоинт REST-пагинируемый (30 на страницу), а `_resolve_correction_pr`
    держит на списке гвард «ровно один кандидат» — усечение по первой
    странице выродило бы его в молчаливый выбор первого. `--slurp` для
    этого непригоден (`gh` 2.98: «not supported with --jq»), поэтому
    `--paginate` печатает по массиву на страницу подряд, и разбирать
    stdout нужно как ПОСЛЕДОВАТЕЛЬНОСТЬ JSON-документов: `json.loads`
    целиком на такой выдаче падал бы «invalid JSON».
    """
    page1 = [{"number": 42, "state": "MERGED", "baseRefName": "master",
              "mergedAt": "2026-09-01T00:00:00Z", "mergeCommit": "aaa"}]
    page2 = [{"number": 43, "state": "MERGED", "baseRefName": "master",
              "mergedAt": "2026-09-02T00:00:00Z", "mergeCommit": "bbb"}]
    _install_fake_run(
        monkeypatch, returncode=0,
        stdout=f"{json.dumps(page1)}\n{json.dumps(page2)}\n[]\n",
    )

    assert RealOps().prs_containing_commit(REPO_SLUG, "deadbeef") == [
        *page1, *page2,
    ]


def test_prs_containing_commit_jq_really_normalizes_rest_payload(
    monkeypatch,
) -> None:
    """Настоящий `jq` прогоняется по REST-фикстуре — проверяется поведение.

    Фейковый subprocess отдаёт уже нормализованный ответ, поэтому само
    выражение в его тестах не исполняется. Здесь оно берётся из построенного
    argv и применяется живым `jq`: удерживаются и `MERGED` по `merged_at`,
    и `baseRefName` из `.base.ref` — поле, по которому `_resolve_correction_pr`
    отбирает correction-PR (§I7).
    """
    jq_bin = shutil.which("jq")
    if jq_bin is None:
        pytest.skip("jq не установлен — нормализацию не на чем прогнать")

    calls = _install_fake_run(monkeypatch, returncode=0, stdout="[]")
    RealOps().prs_containing_commit(REPO_SLUG, "deadbeef")
    argv = calls[0].argv
    jq_expr = argv[argv.index("--jq") + 1]
    # Фейк подменяет subprocess.run глобально — снимаем перед живым вызовом.
    monkeypatch.undo()

    done = subprocess.run(
        [jq_bin, "-c", jq_expr],
        input=json.dumps(_REST_COMMIT_PULLS_PAYLOAD),
        capture_output=True, text=True,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [
        {"number": 42, "state": "MERGED", "baseRefName": "master",
         "mergedAt": "2026-09-01T00:00:00Z", "mergeCommit": "deadbeef"},
        {"number": 43, "state": "OPEN", "baseRefName": "master",
         "mergedAt": None, "mergeCommit": None},
    ]


def test_prs_containing_commit_rc_nonzero_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stderr="rate limited")
    ops = RealOps()

    with pytest.raises(RuntimeError, match="gh api rc=1: rate limited"):
        ops.prs_containing_commit(REPO_SLUG, "deadbeef")


def test_prs_containing_commit_invalid_json_raises_runtime_error(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="not json")
    ops = RealOps()

    with pytest.raises(RuntimeError, match="invalid JSON: 'not json'"):
        ops.prs_containing_commit(REPO_SLUG, "deadbeef")


def test_prs_containing_commit_empty_returns_empty_list(monkeypatch):
    _install_fake_run(monkeypatch, returncode=0, stdout="")
    ops = RealOps()

    assert ops.prs_containing_commit(REPO_SLUG, "deadbeef") == []


def test_rev_parse_returns_sha(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="cafe1234\n")
    ops = RealOps()

    result = ops.rev_parse("/tmp/devtools", "HEAD")

    assert result == "cafe1234"
    assert calls[0].argv == [
        "git", "-C", "/tmp/devtools", "rev-parse", "--verify", "--quiet",
        "HEAD",
    ]


def test_rev_parse_missing_ref_returns_none(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stdout="", stderr="bad ref")
    ops = RealOps()

    assert ops.rev_parse("/tmp/devtools", "nope") is None


def test_blob_in_commit_returns_hash(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="blobsha\n")
    ops = RealOps()

    result = ops.blob_in_commit("/tmp/devtools", "deadbeef", "spec/x.md")

    assert result == "blobsha"
    assert calls[0].argv == [
        "git", "-C", "/tmp/devtools", "rev-parse", "deadbeef:spec/x.md",
    ]


def test_blob_in_commit_absent_returns_none(monkeypatch):
    _install_fake_run(monkeypatch, returncode=128, stdout="", stderr="bad")
    ops = RealOps()

    assert ops.blob_in_commit("/tmp/devtools", "deadbeef", "nope.md") is None


def test_show_file_returns_content_at_ref(tmp_path) -> None:
    import subprocess as real_subprocess

    real_subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "x.md").write_text("hello\n")
    real_subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    real_subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c",
         "user.name=t", "commit", "-q", "-m", "c"],
        check=True,
    )

    assert RealOps().show_file(str(tmp_path), "HEAD", "spec/x.md") == "hello\n"


def test_show_file_none_for_missing_path(tmp_path) -> None:
    import subprocess as real_subprocess

    real_subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert RealOps().show_file(str(tmp_path), "HEAD", "spec/nope.md") is None


def test_commit_parent_returns_sha(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0, stdout="parentsha\n")
    ops = RealOps()

    result = ops.commit_parent("/tmp/devtools", "deadbeef")

    assert result == "parentsha"
    assert calls[0].argv == [
        "git", "-C", "/tmp/devtools", "rev-parse", "--verify", "--quiet",
        "deadbeef^1",
    ]


def test_commit_parent_root_commit_returns_none(monkeypatch):
    _install_fake_run(monkeypatch, returncode=1, stdout="", stderr="no parent")
    ops = RealOps()

    assert ops.commit_parent("/tmp/devtools", "deadbeef") is None
