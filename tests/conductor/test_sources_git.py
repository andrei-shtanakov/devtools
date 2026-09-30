import subprocess
from pathlib import Path

import pytest

import conductor.sources_git as sg
from conductor.manifest import FleetRepo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _clone(tmp: Path, files: dict[str, str], msg: str = "init @id:x") -> Path:
    up = tmp / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "master")
    _git(up, "config", "user.email", "t@t")
    _git(up, "config", "user.name", "t")
    for name, text in files.items():
        (up / name).parent.mkdir(parents=True, exist_ok=True)
        (up / name).write_text(text, encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-q", "--allow-empty", "-m", msg)
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", str(up), str(clone)], check=True)
    return clone


def test_reads_origin_not_worktree(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "- [ ] a @id:a\n"})
    (clone / "TODO.md").write_text("- [x] a @id:a\n", encoding="utf-8")
    text, sha, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert (state, text) == ("read", "- [ ] a @id:a\n") and sha


def test_origin_head_unset_or_broken_falls_back(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    _git(clone, "remote", "set-head", "origin", "-d")
    assert sg.default_ref(clone) == "origin/master"
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    assert sg.default_ref(clone) == "origin/master"


def test_no_default_branch_is_error(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    _git(clone, "remote", "set-head", "origin", "-d")
    _git(clone, "update-ref", "-d", "refs/remotes/origin/master")
    assert sg.read_file_at_origin(clone, "TODO.md")[2] == "error"


def test_missing_todo_is_absent(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"README": "r"})
    _, sha, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert state == "absent" and sha


def test_timeout_is_error_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    real = sg.git

    def slow(repo_dir: Path, *args: str) -> tuple[int, str, str]:
        if args[0] == "show":
            return 124, "", "git timeout"
        return real(repo_dir, *args)

    monkeypatch.setattr(sg, "git", slow)
    assert sg.read_file_at_origin(clone, "TODO.md")[2] == "error"


def test_missing_checkout_is_error(tmp_path: Path) -> None:
    assert sg.read_todo(FleetRepo("x", "x", "x"), tmp_path, False).state == "error"


def test_history_helpers(tmp_path: Path) -> None:
    clone = _clone(
        tmp_path,
        {
            "TODO.md": "- [ ] a @id:a @blocked_by:todo://b/c\n",
            "contracts/v2/x.json": "{}",
        },
    )
    ref = "origin/master"
    assert sg.last_commit_mentioning(clone, ref, "@id:x")
    assert sg.last_commit_mentioning(clone, ref, "@id:nope") is None
    assert sg.line_since(clone, ref, 1).endswith("Z")
    with pytest.raises(sg.GitError):
        sg.ever_had(clone, "origin/nope", "@id:a")
    assert sg.ever_had(clone, ref, "@id:a")
    assert sg.ever_had(clone, ref, "@id:zzz") is None
    fact = sg.path_fact(clone, "contracts/v1/x.json")
    assert fact["exists"] is False and "v2" in fact["siblings"]
    assert sg.path_fact(clone, "contracts/v2/x.json")["exists"] is True


# ревью рубежа 1 (2026-09-30)


def test_path_fact_missing_parent_is_absent_not_error(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"README": "x\n"})
    fact = sg.path_fact(clone, "contracts/bar/v1/schema.json")
    assert (fact["exists"], fact["siblings"]) == (False, [])


def test_fetch_follows_renamed_default_branch(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "- [ ] a @id:a\n"})
    up = tmp_path / "up"
    _git(up, "branch", "-m", "master", "main")
    (up / "TODO.md").write_text("- [x] a @id:a\n", encoding="utf-8")
    _git(up, "commit", "-q", "-am", "done")
    assert sg.fetch(clone) is None
    text, _, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert (state, text) == ("read", "- [x] a @id:a\n")


def test_id_tokens_do_not_match_longer_ids(tmp_path: Path) -> None:
    clone = _clone(
        tmp_path, {"TODO.md": "- [ ] b @id:foo-bar\n"}, msg="work @id:foo-bar"
    )
    ref = sg.default_ref(clone)
    assert ref is not None
    assert sg.last_commit_mentioning(clone, ref, "@id:foo") is None
    assert sg.last_commit_mentioning(clone, ref, "@id:foo-bar") is not None
    assert sg.ever_had(clone, ref, "@id:foo") is None
    assert sg.ever_had(clone, ref, "@id:foo-bar") is not None


def test_invalid_utf8_todo_is_read_not_a_crash(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"README": "x\n"})
    up = tmp_path / "up"
    (up / "TODO.md").write_bytes(b"- [ ] a @id:a \xff\n")
    _git(up, "add", "-A")
    _git(up, "commit", "-q", "-m", "bad bytes")
    assert sg.fetch(clone) is None
    text, _, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert state == "read" and text is not None and "@id:a" in text


def test_shallow_clone_is_a_read_error(tmp_path: Path) -> None:
    # рубеж 3 C1: у мелкого клона blame/движение/история удалений неверны
    up = tmp_path / "up"
    _clone(tmp_path, {"TODO.md": "- [ ] a @id:a\n"})
    (up / "TODO.md").write_text("- [ ] a @id:a\n- [ ] b @id:b\n", encoding="utf-8")
    _git(up, "commit", "-q", "-am", "second")
    shallow = tmp_path / "sh"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{up}", str(shallow)],
        check=True,
    )
    todo = sg.read_todo(FleetRepo("sh", "sh", "sh"), tmp_path, False)
    assert todo.state == "error" and "мелкий клон" in todo.detail
