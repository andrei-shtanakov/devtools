"""Task 0: fixture builders are correct before any implementation exists."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

from tests.selfcheck.helpers import (
    NOW,
    ago,
    commit,
    corpus_repo,
    fake_tool,
    fake_venv,
    git,
    make_repo,
    plist_dir,
    tracked,
    workspace,
)


def test_commit_adds_only_listed_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": ""})
    (repo / "untracked.py").write_text("")
    commit(repo, {"b.py": ""}, date=ago(10))
    assert tracked(repo) == {"a.py", "b.py"}


def test_commit_dates_follow_fixed_clock(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": ""}, date=ago(200))
    ts = int(git(repo, "log", "-1", "--format=%ct").strip())
    assert 199 < (NOW - ts) / 86400 < 201


def test_corpus_repo_shape(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    assert "gone.py" in tracked(repo) and not (repo / "gone.py").exists()
    assert "untracked.py" not in tracked(repo) and (repo / "untracked.py").exists()
    assert (repo / "link.py").is_symlink()
    assert "with space.sh" in tracked(repo) and "юникод.py" in tracked(repo)
    assert os.access(repo / "with space.sh", os.X_OK)
    others = git(repo, "ls-files", "-z", "--others", "--exclude-standard").split("\0")
    assert ".venv/x.py" not in others


def test_fake_venv_layout(tmp_path: Path) -> None:
    marker = tmp_path / "EXECUTED"
    site = fake_venv(tmp_path, evil_marker=marker)
    assert site.name == "site-packages" and site.parent.name == "python3.12"
    assert (site / "PyYAML-6.0.3.dist-info" / "top_level.txt").read_text().split() == [
        "_yaml", "yaml"]
    assert (site / "sitecustomize.py").exists() and (site / "evil.pth").exists()
    assert not marker.exists()


def run_tool(tool: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(tool), *args], capture_output=True, text=True,
                          timeout=20)


def test_fake_tool_contract(tmp_path: Path) -> None:
    tool = fake_tool(tmp_path / "bin", unprocessed=".py")
    assert run_tool(tool, "--version").stdout.strip() == "fake 1.2.3"
    proc = run_tool(tool, "/x/a.py", "/x/.selfcheck-canary/fake/c.py")
    data = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert [i["code"] for i in data["items"]] == ["X", "CAN"]
    assert data["processed"] == []
    whole = json.loads(run_tool(fake_tool(tmp_path / "b2", unprocessed="b.py"),
                                "/x/.selfcheck-canary/c.py", "/x/b.py", "/x/a.py").stdout)
    assert whole["processed"] == ["/x/.selfcheck-canary/c.py", "/x/a.py"]


def test_fake_tool_write_to_read_only_dir(tmp_path: Path) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    (folder / "a.py").write_text("")
    folder.chmod(0o555)
    try:
        proc = run_tool(fake_tool(tmp_path / "bin", write_copy=True),
                        str(folder / "a.py"))
    finally:
        folder.chmod(0o755)
    assert proc.returncode == 2 and "Permission denied" in proc.stderr


def test_plist_dir(tmp_path: Path) -> None:
    folder = plist_dir(tmp_path, ["/w/repo/job.py"])
    with (folder / "dev.atp.x.plist").open("rb") as handle:
        assert plistlib.load(handle)["ProgramArguments"] == ["/w/repo/job.py"]


def test_workspace_layout(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    repo = ws / "devtools"
    assert {"Makefile", "live.py", "orphan.py", "pyproject.toml",
            "skills/s/SKILL.md", ".gitignore"} <= tracked(repo)
    assert (ws / "m.toml").read_text().startswith("[tools.devtools]")
    ts = int(git(repo, "log", "-1", "--format=%ct").strip())
    assert (NOW - ts) / 86400 > 60
