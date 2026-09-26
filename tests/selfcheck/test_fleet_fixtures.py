"""S2 Task 0 — the fleet fixtures themselves (red phase: green before any code)."""

from __future__ import annotations

from pathlib import Path

from tests.selfcheck.helpers import commit_bytes, fleet_ws, git, make_repo, synced


def test_synced_has_origin_head_without_network(tmp_path: Path) -> None:
    repo = synced(make_repo(tmp_path / "r", {"a.md": "x\n"}))
    assert git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip() == (
        "refs/remotes/origin/main"
    )
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "remote") == ""  # no remote configured: nothing to fetch


def test_commit_bytes_keeps_bytes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.md": "x\n"})
    commit_bytes(repo, {"b.bin": b"\x00\xff"})
    assert (repo / "b.bin").read_bytes() == b"\x00\xff"
    assert "b.bin" in git(repo, "ls-files")


def test_fleet_ws_layout(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    for name in ("devtools", "nb", "docs-nb", "umbrella"):
        assert (ws / name / ".git").is_dir()
        assert git(ws / name, "status", "--porcelain") == ""
    manifest = (ws / "umbrella" / "m.toml").read_text()
    assert manifest.count("git_dir") == 3
    assert (ws / "devtools" / "scripts" / "review" / "PIN").is_file()
