"""Тесты fleet_config_policy.py — сенсор политики флота для конфига spec-runner.

Репо — настоящие git-клоны во временном каталоге: сенсор читает конфиг с
`origin/<default>`, так что фикстура обязана иметь origin, а не только
рабочее дерево.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import fleet_config_policy as fcp

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

POLICY = [fcp.PolicyKey("harness_guard", "strict", "warn", "tripwire")]


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args], env=GIT_ENV, check=True, capture_output=True
    )


def clone_with(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """Клон, у которого `files` лежат на origin/master (не только в дереве)."""
    up = tmp_path / f"{name}-up"
    up.mkdir()
    run_git(up, "init", "-q", "-b", "master")
    for rel, text in {"README.md": "x\n", **files}.items():
        (up / rel).parent.mkdir(parents=True, exist_ok=True)
        (up / rel).write_text(text, encoding="utf-8")
    run_git(up, "add", "-A")
    run_git(up, "commit", "-q", "-m", "init")
    clone = tmp_path / name
    subprocess.run(
        ["git", "clone", "-q", str(up), str(clone)],
        env=GIT_ENV,
        check=True,
        capture_output=True,
    )
    return clone


def states(findings: list[fcp.Finding]) -> list[tuple[str, str]]:
    return [(f.state, f.value) for f in findings]


def test_flat_config_with_required_value_is_ok(tmp_path):
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "harness_guard: strict\n"}
    )
    assert states(fcp.check_repo(repo, POLICY)) == [("ok", "strict")]


def test_wrong_value_is_a_violation(tmp_path):
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "harness_guard: warn\n"}
    )
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.value, f.source) == (
        "violation",
        "warn",
        "spec-runner.config.yaml",
    )


def test_missing_key_means_the_spec_runner_default(tmp_path):
    """Ключа нет — действует умолчание spec-runner, и оно названо как есть."""
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "claude_model: sonnet\n"}
    )
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.value) == ("violation", "warn")
    assert "умолчание" in f.detail


def test_legacy_location_and_executor_wrapper_are_read(tmp_path):
    """v1: `spec/executor.config.yaml` с обёрткой `executor:` — как у spec-runner."""
    repo = clone_with(
        tmp_path,
        "a",
        {"spec/executor.config.yaml": "executor:\n  harness_guard: strict\n"},
    )
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.source) == ("ok", "spec/executor.config.yaml")


def test_root_config_wins_over_legacy(tmp_path):
    repo = clone_with(
        tmp_path,
        "a",
        {
            "spec-runner.config.yaml": "harness_guard: warn\n",
            "spec/executor.config.yaml": "executor:\n  harness_guard: strict\n",
        },
    )
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.source) == ("violation", "spec-runner.config.yaml")


def test_no_config_but_spec_runner_artifacts_is_unconfigured(tmp_path):
    """Конфига нет, а spec-runner в репо гоняется (tasks-спека) — действуют
    умолчания: это нарушение, а не «неприменимо» (maestro, kapelle)."""
    repo = clone_with(
        tmp_path,
        "a",
        {
            "spec/tasks.md": "# tasks\n",
            "executor.config.yaml": "harness_guard: strict\n",
        },
    )
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.value) == ("unconfigured", "warn")


def test_repo_without_spec_runner_is_not_applicable(tmp_path):
    repo = clone_with(tmp_path, "a", {})
    assert fcp.check_repo(repo, POLICY) == []


def test_only_origin_counts_not_the_working_tree(tmp_path):
    """Флот — это `origin/<default>`: локальная незакоммиченная правка не в счёт."""
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "harness_guard: warn\n"}
    )
    (repo / "spec-runner.config.yaml").write_text("harness_guard: strict\n")
    [f] = fcp.check_repo(repo, POLICY)
    assert f.state == "violation"
    # и незапушенный локальный коммит — тоже не флот: HEAD ≠ origin/<default>
    run_git(repo, "commit", "-qam", "local only")
    [f] = fcp.check_repo(repo, POLICY)
    assert f.state == "violation"


@pytest.mark.parametrize(
    "text",
    ["harness_guard: [unclosed\n", "- just\n- a list\n", "executor: 5\n"],
    ids=["bad-yaml", "not-a-mapping", "executor-not-a-mapping"],
)
def test_unreadable_config_is_named_not_guessed(tmp_path, text):
    repo = clone_with(tmp_path, "a", {"spec-runner.config.yaml": text})
    [f] = fcp.check_repo(repo, POLICY)
    assert f.state == "unreadable"


def test_contract_loads_and_names_harness_guard():
    policy = fcp.load_policy(fcp.CONTRACT)
    assert [(k.name, k.required, k.default) for k in policy] == [
        ("harness_guard", "strict", "warn")
    ]


def test_main_is_silent_when_clean_and_exit_1_on_violation(tmp_path, capsys):
    clone_with(tmp_path, "good", {"spec-runner.config.yaml": "harness_guard: strict\n"})
    clone_with(tmp_path, "bad", {"spec-runner.config.yaml": "harness_guard: warn\n"})
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[cores.good]\nrepo_url = "git@github.com:o/good.git"\ngit_dir = "good"\n',
        encoding="utf-8",
    )
    assert fcp.main(["--workspace", str(tmp_path), "--manifest", str(manifest)]) == 0
    assert capsys.readouterr().out == ""

    manifest.write_text(
        manifest.read_text()
        + '[cores.bad]\nrepo_url = "git@github.com:o/bad.git"\ngit_dir = "bad"\n',
        encoding="utf-8",
    )
    assert fcp.main(["--workspace", str(tmp_path), "--manifest", str(manifest)]) == 1
    out = capsys.readouterr().out
    assert "bad" in out and "harness_guard" in out and "warn" in out


def test_main_exit_2_on_unreadable_manifest(tmp_path, capsys):
    assert (
        fcp.main(["--workspace", str(tmp_path), "--manifest", str(tmp_path / "x")]) == 2
    )


def test_unresolvable_origin_ref_is_unreadable_not_clean(tmp_path, capsys):
    """Терм. ревью #562 (major): нет `origin/<default>` (remote назван иначе)
    — конфиг не прочитан, и это называется, а не красится «неприменимо»."""
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "harness_guard: warn\n"}
    )
    run_git(repo, "remote", "rename", "origin", "upstream")
    [f] = fcp.check_repo(repo, POLICY)
    assert f.state == "unreadable" and "origin" in f.detail
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[cores.a]\nrepo_url = "git@github.com:o/a.git"\ngit_dir = "a"\n',
        encoding="utf-8",
    )
    assert fcp.main(["--workspace", str(tmp_path), "--manifest", str(manifest)]) == 1
    assert "unreadable" in capsys.readouterr().out


def test_unknown_default_branch_is_unreadable_not_head(tmp_path):
    """Терм. ревью #562: default-ветку не определить (нет origin/HEAD, нет
    master/main) — не откат на HEAD (это локальный клон, не флот)."""
    up = tmp_path / "up"
    up.mkdir()
    run_git(up, "init", "-q", "-b", "trunk")
    (up / "spec-runner.config.yaml").write_text("harness_guard: warn\n")
    run_git(up, "add", "-A")
    run_git(up, "commit", "-q", "-m", "init")
    repo = tmp_path / "a"
    subprocess.run(
        ["git", "clone", "-q", str(up), str(repo)],
        env=GIT_ENV,
        check=True,
        capture_output=True,
    )
    run_git(repo, "remote", "set-head", "origin", "--delete")
    (repo / "spec-runner.config.yaml").write_text("harness_guard: strict\n")
    run_git(repo, "commit", "-qam", "local only")
    [f] = fcp.check_repo(repo, POLICY)
    assert f.state == "unreadable"


def test_origin_head_pointing_outside_remotes_is_ignored(tmp_path):
    """Ревью #562 (recheck): `origin/HEAD`, перенаправленный на локальную
    ветку, — не флот; читается `origin/master`, а не незапушенный коммит."""
    repo = clone_with(
        tmp_path, "a", {"spec-runner.config.yaml": "harness_guard: warn\n"}
    )
    (repo / "spec-runner.config.yaml").write_text("harness_guard: strict\n")
    run_git(repo, "commit", "-qam", "local only")
    run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/heads/master")
    [f] = fcp.check_repo(repo, POLICY)
    assert (f.state, f.value) == ("violation", "warn")
