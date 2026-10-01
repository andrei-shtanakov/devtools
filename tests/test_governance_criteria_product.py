import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from governance import criteria_product as cp


def _repo(tmp_path: Path, files: dict[str, str]) -> cp.Tree:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return cp.Tree(tmp_path, sha)


CONFIG = "criteria:\n  product_roots: [pkg/, ./tool.py]\n  environment:\n    groups: [Gov_X]\n    extras: [cli]\n"


def test_declaration_is_normalised(tmp_path):
    tree = _repo(
        tmp_path, {"spec-runner.config.yaml": CONFIG, "pkg/a.py": "", "tool.py": ""}
    )
    decl = cp.read_declaration(tree)
    assert decl.roots == ("pkg", "tool.py")
    assert decl.groups == ("gov-x",) and decl.extras == ("cli",)


def test_undeclared_groups_are_none_not_empty(tmp_path):
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": "criteria:\n  product_roots: [pkg]\n",
            "pkg/a.py": "",
        },
    )
    decl = cp.read_declaration(tree)
    assert decl.groups is None and decl.extras == ()


@pytest.mark.parametrize("roots", ["[/abs]", "[a/../b]", "[pkg, pkg/]", "[]"])
def test_bad_declarations_refused(tmp_path, roots):
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": f"criteria:\n  product_roots: {roots}\n",
            "pkg/a.py": "",
        },
    )
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_resolve_roots_tracked_py_only(tmp_path):
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": CONFIG,
            "pkg/a.py": "",
            "pkg/b.txt": "",
            "pkg/sub/c.py": "",
            "tool.py": "",
        },
    )
    assert cp.resolve_roots(tree, ("pkg", "tool.py")) == (
        "pkg/a.py",
        "pkg/sub/c.py",
        "tool.py",
    )


def test_content_sha256_matches_design_6_1(tmp_path):
    files = {
        "spec-runner.config.yaml": CONFIG,
        "pkg/a.py": "x = 1\n",
        "tool.py": "",
        "pyproject.toml": "[project]\nname='p'\n",
        "tests/test_a.py": "def test_a(): pass\n",
        "tests/skip/test_s.py": "def test_s(): pass\n",
    }
    tree = _repo(tmp_path, files)
    decl = cp.read_declaration(tree)
    got = cp.content_sha256(tree, decl, "c" * 64, ["tests/test_a.py"], ["tests/skip"])
    paths = sorted(
        {
            "pkg/a.py",
            "tool.py",
            "tests/test_a.py",
            "pyproject.toml",
            "tests/skip/test_s.py",
        },
        key=lambda p: p.encode(),
    )
    obj = {
        "v": 1,
        "product_roots": ["pkg", "tool.py"],
        "lock": "c" * 64,
        "environment": {"groups": ["gov-x"], "extras": ["cli"]},
        "files": [[p, hashlib.sha256(files[p].encode()).hexdigest()] for p in paths],
    }
    want = hashlib.sha256(
        json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    ).hexdigest()
    assert got == want


def test_tracked_py_lists_every_regular_py(tmp_path):
    tree = _repo(
        tmp_path, {"pkg/a.py": "", "tests/skip/test_s.py": "", "notes.txt": ""}
    )
    assert cp.tracked_py(tree) == ("pkg/a.py", "tests/skip/test_s.py")


def test_function_body_lines(tmp_path):
    src = "X = 1\n\ndef f():\n    a = 1\n    return a\n"
    tree = _repo(tmp_path, {"pkg/a.py": src})
    assert cp.function_body_lines(tree, ["pkg/a.py"]) == {"pkg/a.py": {4, 5}}
