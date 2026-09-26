"""S2 Task 1 — reading one fleet repo: texts, binaries, problems, state (§9.2, §9.6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from selfcheck.fleet.reader import read_repo, stale_reasons
from tests.selfcheck.helpers import ago, commit, commit_bytes, git, make_repo, synced


def causes(repo) -> set[str]:
    return {cause for cause, _ in repo.problems}


def test_texts_binary_utf16_utf32_and_clean_state(tmp_path: Path) -> None:
    repo = synced(
        make_repo(tmp_path / "nb", {"a.md": "run x.sh\n", "с пробелом/ю.md": "y\n"})
    )
    commit_bytes(
        repo,
        {
            "bin.dat": b"\x00\x01\x02",
            "u16.txt": "call x.sh\n".encode("utf-16"),
            "u32.txt": "call y.sh\n".encode("utf-32"),  # BOM starts like UTF-16LE
            "latin.txt": "caf\xe9 x.sh\n".encode("latin-1"),
        },
    )
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    got = read_repo(repo, "nb")
    assert got.name == "nb" and got.problems == []
    assert set(got.texts) == {
        "a.md", "с пробелом/ю.md", "u16.txt", "u32.txt", "latin.txt",
    }  # fmt: skip
    assert "call x.sh" in got.texts["u16.txt"]
    assert "call y.sh" in got.texts["u32.txt"]
    assert "x.sh" in got.texts["latin.txt"]
    assert got.binary == 1
    st = got.state
    assert st is not None and st.stale == ()
    assert (st.branch, st.default, st.behind, st.ahead) == ("main", "main", 0, 0)
    assert st.dirty is False and len(st.head or "") == 40


def test_missing_and_broken_git(tmp_path: Path) -> None:
    assert causes(read_repo(tmp_path / "nope", "nope")) == {"missing"}
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /does/not/exist\n")
    assert causes(read_repo(broken, "broken")) == {"ls-files"}


def test_empty_when_nothing_textual(tmp_path: Path) -> None:
    repo = tmp_path / "e"
    repo.mkdir()
    git(tmp_path, "init", "-q", str(repo))
    commit_bytes(repo, {"only.bin": b"\x00"})
    synced(repo)
    assert "empty" in causes(read_repo(repo, "e"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_symlink_out_and_submodule(tmp_path: Path) -> None:
    repo = synced(make_repo(tmp_path / "nb", {"a.md": "x\n", "secret.md": "y\n"}))
    (repo / "inside.md").symlink_to(repo / "a.md")
    # the same target spelled through /var (macOS: /var → /private/var)
    spelled = str(repo / "a.md").replace("/private/var/", "/var/", 1)
    (repo / "inside2.md").symlink_to(spelled)
    (repo / "outside.md").symlink_to("/etc/hosts")
    git(repo, "add", "inside.md", "inside2.md", "outside.md")
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    (repo / "sub").mkdir()  # an uninitialised submodule: clean, not dirty
    git(repo, "commit", "-q", "-m", "links", date=ago(90))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "secret.md").chmod(0)  # git also sees a mode change → dirty
    try:
        got = read_repo(repo, "nb")
    finally:
        (repo / "secret.md").chmod(0o644)
    assert dict(got.problems) == {
        "unreadable": "secret.md",
        "symlink-out": "outside.md",
        "submodule": "sub",
        "stale": "dirty",
    }
    assert not {"inside.md", "inside2.md"} & set(got.texts)  # never read (§9.2)


def _stale(repo: Path) -> tuple[str, ...]:
    return stale_reasons(repo).stale


def test_stale_forms(tmp_path: Path) -> None:
    no_origin = make_repo(tmp_path / "a", {"a.md": "x\n"})
    assert _stale(no_origin) == ("no-origin-head",)

    ahead = synced(make_repo(tmp_path / "b", {"a.md": "x\n"}))
    commit(ahead, {"b.md": "y\n"}, date=ago(1))
    assert _stale(ahead) == ("ahead",)

    behind = synced(make_repo(tmp_path / "c", {"a.md": "x\n"}))
    commit(behind, {"b.md": "y\n"}, date=ago(1))
    git(behind, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(behind, "reset", "-q", "--hard", "HEAD~1")
    assert _stale(behind) == ("behind",)

    feature = synced(make_repo(tmp_path / "d", {"a.md": "x\n"}))
    git(feature, "switch", "-q", "-c", "feat")
    assert _stale(feature) == ("not-default-branch",)

    detached = synced(make_repo(tmp_path / "e", {"a.md": "x\n"}))
    git(detached, "checkout", "-q", "--detach")
    assert _stale(detached) == ("detached",)

    dirty = synced(make_repo(tmp_path / "f", {"a.md": "x\n"}))
    (dirty / "untracked.md").write_text("new\n")
    assert _stale(dirty) == ("dirty",)
    assert stale_reasons(dirty).dirty is True


def test_stat_only_change_is_not_dirty(tmp_path: Path) -> None:
    """`git status --porcelain` semantics, not `diff-index` (review r1 M2)."""
    repo = synced(make_repo(tmp_path / "t", {"a.md": "x\n"}))
    index = (repo / ".git" / "index").read_bytes()
    os.utime(repo / "a.md", (1_900_000_000, 1_900_000_000))
    assert _stale(repo) == ()
    assert (repo / ".git" / "index").read_bytes() == index  # nothing written


def test_stale_is_a_problem_and_fetched_at(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "a", {"a.md": "x\n"})
    got = read_repo(repo, "a")
    assert ("stale", "no-origin-head") in got.problems
    assert got.state is not None and got.state.fetched_at is None
    synced(repo)
    fetch_head = git(repo, "rev-parse", "--git-path", "FETCH_HEAD").strip()
    (repo / fetch_head).write_text("")
    state = read_repo(repo, "a").state
    assert state is not None and state.fetched_at is not None


def test_inherited_git_environment_is_ignored(tmp_path: Path, monkeypatch) -> None:
    other = synced(make_repo(tmp_path / "other", {"decoy.md": "decoy\n"}))
    nb = synced(make_repo(tmp_path / "nb", {"real.md": "real\n"}))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    assert set(read_repo(nb, "nb").texts) == {"real.md"}


def test_dangling_origin_head_is_stale(tmp_path: Path) -> None:
    """Final review I2: origin/HEAD → a ref that no longer exists (upstream
    renamed its default branch, fetch --prune dropped the old one)."""
    repo = synced(make_repo(tmp_path / "r", {"a.md": "x\n"}))
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert stale_reasons(repo).stale == ("no-origin-head",)


def test_skip_worktree_file_is_unreadable(tmp_path: Path) -> None:
    """Final review I3: a sparse / skip-worktree file is absent on disk while
    `git status` stays clean — its text is lost, so the repo is not complete."""
    repo = synced(make_repo(tmp_path / "r", {"a.md": "x\n", "b.md": "y\n"}))
    git(repo, "update-index", "--skip-worktree", "b.md")
    (repo / "b.md").unlink()
    got = read_repo(repo, "r")
    assert ("unreadable", "b.md") in got.problems
    assert got.state is not None and got.state.dirty is False


def test_non_utf8_index_path_does_not_crash() -> None:
    """Final review I4: a Latin-1 path in a neighbour's index (Linux) decodes
    with surrogates instead of raising UnicodeDecodeError out of the run."""
    from selfcheck.fleet.reader import parse_entries

    staged = b"100644 " + b"a" * 40 + b" 0\tcaf\xe9.md\0"
    assert parse_entries(staged, b"x\xff.md\0") == {
        "caf\udce9.md": "100644",
        "x\udcff.md": "",
    }


def test_non_utf8_path_is_report_safe(tmp_path: Path, monkeypatch) -> None:
    """Review of #405: a surrogate-bearing path must not reach the report —
    json/markdown writing would die with UnicodeEncodeError (exit 1, no JSON)."""
    import json

    from selfcheck.fleet import reader as reader_module

    repo = synced(make_repo(tmp_path / "r", {"a.md": "x\n"}))
    monkeypatch.setattr(
        reader_module,
        "_entries",
        lambda path: ({"a.md": "100644", "caf\udce9.md": "100644"}, None),
    )
    got = read_repo(repo, "r")
    text = json.dumps({"problems": got.problems, "texts": sorted(got.texts)})
    text.encode("utf-8")  # must not raise
    assert ("unreadable", "caf�.md") in got.problems
