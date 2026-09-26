"""Task 3 — corpus, read-only copy, cleanup, git reads (§1.3–1.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from selfcheck.corpus import (
    last_commit_ts,
    list_corpus,
    materialize,
    release,
    repo_state,
    snapshot_hashes,
)
from tests.selfcheck.helpers import ago, commit, corpus_repo, make_repo


def test_corpus_membership(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    assert list_corpus(repo, exclude=["vendor/**"]) == sorted(
        [".gitignore", "a.py", "untracked.py", "with space.sh", "юникод.py"]
    )


def test_read_only_copy_and_release(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    dest = tmp_path / "run" / "src" / "r"
    materialize(
        repo, list_corpus(repo), dest, {".selfcheck-canary/x/c.py": "import os\n"}
    )
    assert (dest / "with space.sh").read_text().startswith("#!/bin/sh")
    assert os.access(dest / "with space.sh", os.X_OK)
    assert (dest / ".selfcheck-canary/x/c.py").read_text() == "import os\n"
    with pytest.raises(PermissionError):
        (dest / "a.py").write_text("tampered")
    with pytest.raises(PermissionError):
        (dest / "new.py").write_text("")
    assert release(dest) is None and not dest.exists()


def test_materialize_never_reuses_dest(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    (tmp_path / "d").mkdir()
    with pytest.raises(FileExistsError):
        materialize(repo, list_corpus(repo), tmp_path / "d", {})


def test_materialize_requires_absolute_paths(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    with pytest.raises(ValueError):
        materialize(repo, list_corpus(repo), Path("relative/dest"), {})


def test_source_untouched_including_index(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    before = snapshot_hashes(repo)
    index_mtime = (repo / ".git" / "index").stat().st_mtime_ns
    materialize(repo, list_corpus(repo), tmp_path / "copy", {})
    last_commit_ts(repo, "a.py")
    repo_state(repo)
    release(tmp_path / "copy")
    assert snapshot_hashes(repo) == before
    assert (repo / ".git" / "index").stat().st_mtime_ns == index_mtime


def test_last_commit_ts(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    commit(repo, {"b.py": "b = 1\n"}, date=ago(5))
    a, b = last_commit_ts(repo, "a.py"), last_commit_ts(repo, "b.py")
    assert a is not None and b is not None and a < b
    assert last_commit_ts(repo, "untracked.py") is None


def test_repo_state(tmp_path: Path) -> None:
    clean = make_repo(tmp_path / "c", {"a.py": ""})
    state = repo_state(clean)
    assert len(state["head"]) == 40 and state["dirty"] is False
    (clean / "new.py").write_text("")
    assert repo_state(clean)["dirty"] is True
