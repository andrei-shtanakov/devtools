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


def _ls_files(monkeypatch, listing: bytes, raws: set[str]) -> None:
    """Feed ``ls-files`` output with non-UTF-8 names (APFS refuses to create
    them) and make those raw names regular files."""
    import subprocess

    from selfcheck import corpus

    proc = subprocess.CompletedProcess([], 0, listing, b"")
    monkeypatch.setattr(corpus, "_git", lambda *a, **k: proc)
    real = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: self.name in raws or real(self))


def test_non_utf8_name_is_in_the_corpus_by_its_shown_name(
    tmp_path: Path, monkeypatch
) -> None:
    """#409: a Latin-1 name from ``ls-files`` (Linux) decodes like the
    filesystem does — no UnicodeDecodeError (exit 1, no report) — and stays in
    the corpus under its shown name: dropping it would drop its references."""
    from selfcheck.corpus import corpus_names

    repo = make_repo(tmp_path / "r", {"a.py": ""})
    _ls_files(monkeypatch, b"a.py\0caf\xe9.py\0", {"caf\udce9.py"})
    assert list_corpus(repo) == ["a.py", "caf\ufffd.py"]
    assert corpus_names(repo)["caf\ufffd.py"] == "caf\udce9.py"


def test_colliding_shown_names_fail_the_listing(tmp_path: Path, monkeypatch) -> None:
    """Two raw names, one shown name: keeping either drops the other's
    references, so the scope listing refuses with a named reason."""
    repo = make_repo(tmp_path / "r", {"a.py": ""})
    latin1, cp1252 = "caf\udce9.py", "caf\udce8.py"  # ruff merges the literals
    _ls_files(monkeypatch, b"caf\xe9.py\0caf\xe8.py\0", {latin1, cp1252})
    with pytest.raises(OSError, match="collide"):
        list_corpus(repo)


def test_raw_path_maps_a_shown_name_back(tmp_path: Path, monkeypatch) -> None:
    """#420: reads of the source checkout (git log, existence) need the raw
    name; the corpus carries the shown one."""
    from selfcheck.corpus import raw_path

    repo = make_repo(tmp_path / "r", {"a.py": ""})
    _ls_files(monkeypatch, b"a.py\0caf\xe9.py\0", {"caf\udce9.py"})
    assert raw_path(repo, "caf\ufffd.py") == "caf\udce9.py"
    assert raw_path(repo, "a.py") == "a.py"


def test_history_is_read_by_the_raw_name(tmp_path: Path, monkeypatch) -> None:
    """#420: ``git log -- caf\ufffd.py`` finds nothing — a committed file got
    ``history: false`` and a false cap P3."""
    import subprocess

    from selfcheck import corpus

    asked: list[str] = []

    def git(repo, *a, **k):
        if a[0] == "ls-files":
            return subprocess.CompletedProcess(a, 0, b"caf\xe9.py\0", b"")
        asked.append(a[-1])
        return subprocess.CompletedProcess(a, 0, b"1700000000\n", b"")

    monkeypatch.setattr(corpus, "_git", git)
    real = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda self: self.name == "caf\udce9.py" or real(self)
    )
    assert corpus.last_commit_ts(tmp_path, "caf\ufffd.py") == 1700000000
    assert asked == ["caf\udce9.py"]
