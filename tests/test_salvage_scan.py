"""Тесты salvage_scan.py — детерминированный read-only скан флота (devtools#67).

Четыре класса находок на синтетических git-репо во временном каталоге;
GitHub-резолвер (open-PR heads) инжектируется — сеть в тестах не используется.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

import salvage_scan
from salvage_scan import (
    Finding,
    apply_waivers,
    default_branch,
    github_slug,
    humanize_age,
    render_table,
    scan_branches,
    scan_locks,
    scan_repo,
    scan_unpushed,
    scan_worktrees,
)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )
    return proc.stdout.strip()


def make_cloned_repo(tmp: Path, name: str = "repo") -> Path:
    """Рабочий клон с origin (bare) и запушенным main — «чистое» состояние."""
    src = tmp / f"{name}-src"
    src.mkdir()
    run_git(src, "init", "-q", "-b", "main")
    (src / "README.md").write_text("seed\n")
    run_git(src, "add", ".")
    run_git(src, "commit", "-q", "-m", "seed")
    origin = tmp / f"{name}-origin.git"
    run_git(tmp, "clone", "-q", "--bare", str(src), str(origin))
    repo = tmp / name
    run_git(tmp, "clone", "-q", str(origin), str(repo))
    return repo


def commit(repo: Path, fname: str, msg: str) -> None:
    (repo / fname).write_text(f"{msg}\n")
    run_git(repo, "add", fname)
    run_git(repo, "commit", "-q", "-m", msg)


NOW = time.time()


# ── чистое состояние молчит ─────────────────────────────────────────────


def test_clean_repo_yields_no_findings(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    findings = scan_repo(repo, now=NOW, pr_heads=set(), lock_age_s=3600)
    assert findings == []


# ── класс 1: orphan worktrees ───────────────────────────────────────────


def test_worktree_with_deleted_directory_is_orphan(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    wt = tmp_path / "wt-gone"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "wt-branch")
    import shutil

    shutil.rmtree(wt)
    findings = scan_worktrees(repo, now=NOW)
    assert len(findings) == 1
    assert findings[0].klass == "orphan-worktree"
    assert "wt-gone" in findings[0].obj


def test_healthy_worktree_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    wt = tmp_path / "wt-ok"
    run_git(repo, "worktree", "add", "-q", str(wt), "-b", "wt-ok-branch")
    assert scan_worktrees(repo, now=NOW) == []


# ── класс 2: ветки без PR и не влитые в default ─────────────────────────


def test_branch_without_pr_not_merged_is_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    run_git(repo, "switch", "-q", "-c", "feature-x")
    commit(repo, "f.txt", "wip")
    run_git(repo, "switch", "-q", "main")
    findings = scan_branches(repo, "main", now=NOW, pr_heads=set())
    assert [f.klass for f in findings] == ["branch-no-pr"]
    assert "feature-x" in findings[0].obj
    assert findings[0].age_seconds is not None


def test_branch_with_open_pr_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    run_git(repo, "switch", "-q", "-c", "feature-x")
    commit(repo, "f.txt", "wip")
    run_git(repo, "switch", "-q", "main")
    assert scan_branches(repo, "main", now=NOW, pr_heads={"feature-x"}) == []


def test_merged_branch_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    run_git(repo, "branch", "-q", "old-merged", "main")
    assert scan_branches(repo, "main", now=NOW, pr_heads=set()) == []


def test_unavailable_pr_state_is_marked_not_silent(tmp_path: Path) -> None:
    """gh недоступен → кандидаты показываются с явной пометкой, не скрываются."""
    repo = make_cloned_repo(tmp_path)
    run_git(repo, "switch", "-q", "-c", "feature-x")
    commit(repo, "f.txt", "wip")
    run_git(repo, "switch", "-q", "main")
    findings = scan_branches(repo, "main", now=NOW, pr_heads=None)
    assert len(findings) == 1
    assert "PR state unknown" in findings[0].obj


# ── класс 3: unpushed-коммиты default-ветки ─────────────────────────────


def test_unpushed_default_commits_are_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    commit(repo, "a.txt", "local one")
    commit(repo, "b.txt", "local two")
    findings = scan_unpushed(repo, "main", now=NOW)
    assert len(findings) == 1
    assert findings[0].klass == "unpushed-default"
    assert "2" in findings[0].obj
    assert findings[0].age_seconds is not None


def test_pushed_default_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    assert scan_unpushed(repo, "main", now=NOW) == []


# ── класс 4: stale locks ────────────────────────────────────────────────


def test_old_index_lock_is_stale(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    old = NOW - 7200
    os.utime(lock, (old, old))
    findings = scan_locks(repo, now=NOW, lock_age_s=3600)
    assert [f.klass for f in findings] == ["stale-lock"]
    assert "index.lock" in findings[0].obj


def test_fresh_index_lock_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    (repo / ".git" / "index.lock").write_text("")
    assert scan_locks(repo, now=NOW, lock_age_s=3600) == []


def test_gc_pid_with_dead_process_is_stale(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    # PID заведомо мёртвый: только что завершившийся дочерний процесс.
    dead = subprocess.Popen(["true"])
    dead.wait()
    gc_pid = repo / ".git" / "gc.pid"
    gc_pid.write_text(f"{dead.pid} nowhere.invalid\n")
    findings = scan_locks(repo, now=NOW, lock_age_s=3600)
    assert [f.klass for f in findings] == ["stale-lock"]
    assert "gc.pid" in findings[0].obj


def test_gc_pid_with_alive_process_is_not_reported(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    (repo / ".git" / "gc.pid").write_text(f"{os.getpid()} host\n")
    assert scan_locks(repo, now=NOW, lock_age_s=3600) == []


# ── waivers: помечать, не чинить и не скрывать ──────────────────────────


def test_waiver_marks_finding_but_keeps_it(tmp_path: Path) -> None:
    finding = Finding("prograph-vault", "unpushed-default", "main: 2", 60.0)
    waivers = [
        ("prograph-vault", "unpushed-default", "", "ждёт dispatcher#199")
    ]
    marked = apply_waivers([finding], waivers)
    assert len(marked) == 1
    assert marked[0].waived == "ждёт dispatcher#199"


def test_waiver_for_other_class_does_not_apply(tmp_path: Path) -> None:
    finding = Finding("prograph-vault", "stale-lock", "index.lock", 60.0)
    waivers = [
        ("prograph-vault", "unpushed-default", "", "ждёт dispatcher#199")
    ]
    assert apply_waivers([finding], waivers)[0].waived is None


def test_waiver_object_prefix_narrows_the_match(tmp_path: Path) -> None:
    """Waiver на конкретную ветку не гасит остальные находки того же класса."""
    waivers = [
        ("prograph-vault", "branch-no-pr", "derived-snapshots", "by design")
    ]
    delivery = Finding(
        "prograph-vault", "branch-no-pr", "derived-snapshots", 60.0
    )
    other = Finding("prograph-vault", "branch-no-pr", "feature-x", 60.0)
    marked = apply_waivers([delivery, other], waivers)
    assert marked[0].waived == "by design"
    assert marked[1].waived is None


# ── вспомогательное ─────────────────────────────────────────────────────


def test_default_branch_from_origin_head(tmp_path: Path) -> None:
    repo = make_cloned_repo(tmp_path)
    assert default_branch(repo) == "main"


def test_github_slug_forms() -> None:
    assert github_slug("git@github.com:o/r.git") == "o/r"
    assert github_slug("https://github.com/o/r.git") == "o/r"
    assert github_slug("https://github.com/o/r") == "o/r"
    assert github_slug("file:///somewhere/else") is None


def test_humanize_age() -> None:
    assert humanize_age(30) == "<1m"
    assert humanize_age(90) == "1m"
    assert humanize_age(3 * 3600) == "3h"
    assert humanize_age(2 * 86400 + 3600) == "2d"
    assert humanize_age(None) == "?"


def test_render_table_contains_columns_and_waiver() -> None:
    rows = [
        Finding("repo-a", "stale-lock", "index.lock", 7200.0),
        Finding(
            "prograph-vault",
            "unpushed-default",
            "master: 2 unpushed commit(s)",
            60.0,
            waived="ждёт dispatcher#199",
        ),
    ]
    text = render_table(rows, host="testhost")
    assert "testhost" in text
    assert "repo-a" in text
    assert "stale-lock" in text
    assert "2h" in text
    assert "[waived: ждёт dispatcher#199]" in text


# ── exit-code / молчание ────────────────────────────────────────────────


def _workspace_with_manifest(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    repo = make_cloned_repo(ws, "alpha")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[tools.alpha]\nrepo_url = "file:///x"\ngit_dir = "alpha"\n'
    )
    return ws, repo


def test_main_is_silent_and_zero_on_clean_fleet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws, _repo = _workspace_with_manifest(tmp_path)
    rc = salvage_scan.main(
        [
            "--workspace",
            str(ws),
            "--manifest",
            str(tmp_path / "manifest.toml"),
            "--no-github",
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_main_reports_findings_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws, repo = _workspace_with_manifest(tmp_path)
    commit(repo, "a.txt", "unpushed")
    rc = salvage_scan.main(
        [
            "--workspace",
            str(ws),
            "--manifest",
            str(tmp_path / "manifest.toml"),
            "--no-github",
        ]
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "unpushed-default" in out


def test_main_waived_only_exits_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws, repo = _workspace_with_manifest(tmp_path)
    commit(repo, "a.txt", "deliberate snapshot")
    monkeypatch.setattr(
        salvage_scan,
        "WAIVERS",
        [("alpha", "unpushed-default", "", "осознанно, тест")],
    )
    rc = salvage_scan.main(
        [
            "--workspace",
            str(ws),
            "--manifest",
            str(tmp_path / "manifest.toml"),
            "--no-github",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "[waived: осознанно, тест]" in out
