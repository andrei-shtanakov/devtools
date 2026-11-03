"""`act_git_rows`: разбор first-parent строк и синонимы в `ops`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("steward", reason="steward отсутствует: без группы governance")

from governance import act_git_rows, ops  # noqa: E402


def test_ops_reexports_rows() -> None:
    assert ops._parse_first_parent is act_git_rows.parse_first_parent
    assert ops._FIRST_PARENT_LOG is act_git_rows.FIRST_PARENT_LOG
    assert ops.FirstParentRows is act_git_rows.FirstParentRows


def test_parse_first_parent_unchanged() -> None:
    out = "\x00aaa bbb\nM\tx.py\nA\ty.py\n\x00bbb\n\x00ccc bbb ddd\nnote\n"
    assert act_git_rows.parse_first_parent(out) == [
        ("aaa", ("bbb",), (("M", "x.py"), ("A", "y.py"))),
        ("bbb", (), ()),
        ("ccc", ("bbb", "ddd"), ()),
    ]
    assert act_git_rows.parse_first_parent("") == []


def test_parse_real_git_log(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
            + list(args),
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **env},
        ).stdout

    git("init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("1")
    git("add", ".")
    git("commit", "-qm", "c1")
    git("checkout", "-qb", "side")
    (tmp_path / "b.txt").write_text("2")
    git("add", ".")
    git("commit", "-qm", "c2")
    git("checkout", "-q", "main")
    git("merge", "-q", "--no-ff", "-m", "m", "side")
    sha = lambda ref: git("rev-parse", ref).strip()  # noqa: E731
    rows = act_git_rows.parse_first_parent(git(*act_git_rows.FIRST_PARENT_LOG, "main"))
    first = sha("main~1")
    assert rows == [
        (first, (), (("A", "a.txt"),)),
        (sha("main"), (first, sha("side")), (("A", "b.txt"),)),
    ]


def test_narrow_import_closure() -> None:
    code = (
        "import sys; import governance.act_git_rows, governance.act_forge_http;"
        "bad=[m for m in sys.modules if m in ('df','governance.ops',"
        "'governance.runner','governance.act_status')];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
