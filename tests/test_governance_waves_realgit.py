"""Волны на настоящем git (ревью среза 1, C1): то, что FakeOps.commit_paths
не проверяет — git add путей, которых нет на свежей base следующей волны."""

from __future__ import annotations

import subprocess

from governance import charter_guard, runner
from governance import run_state as rs
from governance.ops import RealOps


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_code_run_commits_on_fresh_base_of_wave2(tmp_path, monkeypatch):
    """W1 штампует charter схемы 2 и коммитит бандл; candidate переносит в base
    только узел (как approve_node._sync_branch_to_snapshot); W2 ветвится от
    свежей base и коммитит бандл — git add не падает, код charter'а — в base."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    target = tmp_path / "alpha"
    subprocess.run(["git", "clone", "-q", str(origin), str(target)], check=True, capture_output=True)
    (target / "README.md").write_text("x\n")
    _git(target, "checkout", "-q", "-b", "master")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "init")
    _git(target, "push", "-q", "origin", "master")
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = rs.new_run(
        subject="s", repo="alpha", repo_slug="owner/alpha", ws_id="ws",
        target_dir=str(target), bundle_dir="workstreams/ws/spec",
        profile="p", run_id="r", authoring="waves", code="ENC", plan_item="todo://alpha/x",
    )
    ops = RealOps()
    bundle = target / state.bundle_dir
    # W1: ветка волны, авторинг charter + штамп, коммит бандла
    _git(target, "switch", "-q", "-c", "spec/ws-w1")
    bundle.mkdir(parents=True)
    (bundle / "00-charter.md").write_text(
        charter_guard.stamp_charter("# C\n", code="ENC", plan_item="todo://alpha/x"))
    runner._commit_bundle(state, ops, "w1")
    # candidate: в base едет только узел
    _git(target, "switch", "-q", "master")
    _git(target, "checkout", "spec/ws-w1", "--", f"{state.bundle_dir}/00-charter.md")
    _git(target, "commit", "-qm", "approve charter")
    _git(target, "push", "-q", "origin", "master")
    # W2: свежая base, новый узел, коммит бандла
    _git(target, "switch", "-q", "-c", "spec/ws-w2", "origin/master")
    (bundle / "10-requirements.md").write_text("#### FR-01: a\n**Priority**: Must\n")
    runner._commit_bundle(state, ops, "w2")
    assert "10-requirements.md" in _git(target, "show", "--name-only", "--format=", "HEAD")
    base_charter = _git(target, "show", f"origin/master:{state.bundle_dir}/00-charter.md")
    assert charter_guard.read_charter(base_charter).code == "ENC"
