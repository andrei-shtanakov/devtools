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
        "/tmp/devtools", REPO_SLUG, "feat/x", "title", "body", "codex-review",
    )

    assert number == 42
    call = calls[0]
    assert call.argv[:3] == ["gh", "pr", "create"]
    assert "--draft" in call.argv
    assert "-R" in call.argv and REPO_SLUG in call.argv
    label_idx = call.argv.index("--label")
    assert call.argv[label_idx + 1] == "codex-review"
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


# --- Кейс 8: commit_all ----------------------------------------------------


def test_commit_all_adds_and_commits_with_message(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=1)  # diff --cached: dirty
    ops = RealOps()

    ops.commit_all("/tmp/devtools", "docs(governance): x\n\nCo-Authored-By: y")

    assert [c.argv[:2] for c in calls] == [
        ["git", "add"], ["git", "diff"], ["git", "commit"],
    ]
    assert calls[0].argv == ["git", "add", "-A"]
    assert calls[0].kwargs["cwd"] == "/tmp/devtools"
    assert calls[2].argv == [
        "git", "commit", "-m", "docs(governance): x\n\nCo-Authored-By: y",
    ]
    assert calls[2].kwargs["check"] is True


def test_commit_all_empty_index_does_not_commit(monkeypatch):
    calls = _install_fake_run(monkeypatch, returncode=0)  # diff --cached: clean
    ops = RealOps()

    ops.commit_all("/tmp/devtools", "message")

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


# --- Кейс 6: unresolved_threads -------------------------------------------


def test_unresolved_threads_true_when_open_thread_present(monkeypatch):
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {"isResolved": True},
                            {"isResolved": False},
                        ]
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


def test_unresolved_threads_false_when_all_resolved(monkeypatch):
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"nodes": [{"isResolved": True}]}
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
