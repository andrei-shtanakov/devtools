import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from governance import criteria_product as cp


def _init_repo(tmp_path: Path) -> None:
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)


def _head_tree(tmp_path: Path) -> cp.Tree:
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return cp.Tree(tmp_path, sha)


def _repo(tmp_path: Path, files: dict[str, str]) -> cp.Tree:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _init_repo(tmp_path)
    return _head_tree(tmp_path)


CONFIG = (
    "criteria:\n  product_roots: [pkg/, ./tool.py]\n"
    "  environment:\n    groups: [Gov_X]\n    extras: [cli]\n"
)
PYPROJECT = (
    "[project]\nname='p'\n\n"
    "[dependency-groups]\ngov-x = []\n\n"
    "[project.optional-dependencies]\ncli = []\n"
)


def test_declaration_is_normalised(tmp_path):
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": CONFIG,
            "pkg/a.py": "",
            "tool.py": "",
            "pyproject.toml": PYPROJECT,
        },
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


def test_root_entry_is_stripped(tmp_path):
    cfg = 'criteria:\n  product_roots: [" pkg"]\n'
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    decl = cp.read_declaration(tree)
    assert decl.roots == ("pkg",)


def test_root_backslash_is_not_rewritten_and_refused(tmp_path):
    # Python "pkg\\a.py" is the single-backslash string pkg\a.py; a backslash
    # is part of the name, never rewritten to "/" — it then fails as missing
    # at product_sha, exactly like the producer (read_declaration itself does
    # not check tracking; resolve_roots, which content_sha256 also goes
    # through, is where a declared-but-absent root is refused).
    cfg = "criteria:\n  product_roots: ['pkg\\a.py']\n"
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    decl = cp.read_declaration(tree)
    assert decl.roots == ("pkg\\a.py",)
    with pytest.raises(cp.ProductError):
        cp.resolve_roots(tree, decl.roots)


def test_root_pathspec_magic_prefix_refused(tmp_path):
    # A root starting with ":" is git pathspec magic (":(top)", ":!x"), not a
    # path — refused regardless of whether a file literally named that way is
    # tracked, exactly like the producer's _normalise_root.
    cfg = 'criteria:\n  product_roots: [":weird.py"]\n'
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, ":weird.py": ""})
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


def test_resolve_roots_refuses_missing_root(tmp_path):
    tree = _repo(tmp_path, {"pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.resolve_roots(tree, ("missing",))


def test_resolve_roots_refuses_no_python(tmp_path):
    tree = _repo(tmp_path, {"docs/readme.txt": "hi\n"})
    with pytest.raises(cp.ProductError):
        cp.resolve_roots(tree, ("docs",))


def test_resolve_roots_refuses_symlink_root(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / "link.py").symlink_to("pkg/a.py")
    _init_repo(tmp_path)
    tree = _head_tree(tmp_path)
    with pytest.raises(cp.ProductError):
        cp.resolve_roots(tree, ("link.py",))


def test_resolve_roots_refuses_symlink_under_dir_root(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / "pkg" / "link.py").symlink_to("a.py")
    _init_repo(tmp_path)
    tree = _head_tree(tmp_path)
    with pytest.raises(cp.ProductError):
        cp.resolve_roots(tree, ("pkg",))


def test_declaration_refuses_duplicate_group_after_normalisation(tmp_path):
    cfg = "criteria:\n  product_roots: [pkg]\n  environment:\n    groups: [Gov_X, gov_x]\n"
    tree = _repo(
        tmp_path,
        {"spec-runner.config.yaml": cfg, "pkg/a.py": "", "pyproject.toml": PYPROJECT},
    )
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_name_with_trailing_newline(tmp_path):
    cfg = 'criteria:\n  product_roots: [pkg]\n  environment:\n    extras: ["cli\\n"]\n'
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_environment_non_mapping(tmp_path):
    cfg = "criteria:\n  product_roots: [pkg]\n  environment: []\n"
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_environment_unknown_key(tmp_path):
    cfg = "criteria:\n  product_roots: [pkg]\n  environment:\n    bogus: []\n"
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_malformed_yaml(tmp_path):
    cfg = "criteria:\n  product_roots: [pkg\n"
    tree = _repo(tmp_path, {"spec-runner.config.yaml": cfg, "pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_non_utf8_config(tmp_path):
    (tmp_path / "spec-runner.config.yaml").write_bytes(
        b"criteria:\n  product_roots: [pkg]\n\xff\xfe"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    _init_repo(tmp_path)
    tree = _head_tree(tmp_path)
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_group_not_in_pyproject(tmp_path):
    pyproject = "[project]\nname='p'\n\n[project.optional-dependencies]\ncli = []\n"
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": CONFIG,
            "pkg/a.py": "",
            "tool.py": "",
            "pyproject.toml": pyproject,
        },
    )
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_extra_not_in_pyproject(tmp_path):
    pyproject = "[project]\nname='p'\n\n[dependency-groups]\ngov-x = []\n"
    tree = _repo(
        tmp_path,
        {
            "spec-runner.config.yaml": CONFIG,
            "pkg/a.py": "",
            "tool.py": "",
            "pyproject.toml": pyproject,
        },
    )
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_declaration_refuses_declared_environment_without_pyproject(tmp_path):
    tree = _repo(
        tmp_path,
        {"spec-runner.config.yaml": CONFIG, "pkg/a.py": "", "tool.py": ""},
    )
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_content_sha256_matches_design_6_1(tmp_path):
    files = {
        "spec-runner.config.yaml": CONFIG,
        "pkg/a.py": "x = 1\n",
        "tool.py": "",
        "pyproject.toml": PYPROJECT,
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


def test_content_sha256_null_groups_differs_from_empty_groups(tmp_path):
    files_a = {
        "spec-runner.config.yaml": "criteria:\n  product_roots: [pkg]\n",
        "pkg/a.py": "x = 1\n",
        "pyproject.toml": "[project]\nname='p'\n",
    }
    tree_a = _repo(tmp_path / "a", files_a)
    decl_a = cp.read_declaration(tree_a)
    got_a = cp.content_sha256(tree_a, decl_a, "d" * 64, [], [])

    files_b = {
        "spec-runner.config.yaml": (
            "criteria:\n  product_roots: [pkg]\n  environment:\n    groups: []\n"
        ),
        "pkg/a.py": "x = 1\n",
        "pyproject.toml": "[project]\nname='p'\n",
    }
    tree_b = _repo(tmp_path / "b", files_b)
    decl_b = cp.read_declaration(tree_b)
    got_b = cp.content_sha256(tree_b, decl_b, "d" * 64, [], [])

    assert decl_a.groups is None
    assert decl_b.groups == ()
    assert got_a != got_b


def test_content_sha256_excluded_path_without_py_files(tmp_path):
    files = {
        "spec-runner.config.yaml": "criteria:\n  product_roots: [pkg]\n",
        "pkg/a.py": "x = 1\n",
        "pyproject.toml": "[project]\nname='p'\n",
        "skip/readme.txt": "hi\n",
    }
    tree = _repo(tmp_path, files)
    decl = cp.read_declaration(tree)
    got = cp.content_sha256(tree, decl, "d" * 64, [], ["skip"])
    want = cp.content_sha256(tree, decl, "d" * 64, [], [])
    assert got == want


def test_content_sha256_excluded_path_symlink_only(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "link.py").symlink_to("../pkg/a.py")
    (tmp_path / "spec-runner.config.yaml").write_text(
        "criteria:\n  product_roots: [pkg]\n"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\n")
    _init_repo(tmp_path)
    tree = _head_tree(tmp_path)
    decl = cp.read_declaration(tree)
    got = cp.content_sha256(tree, decl, "d" * 64, [], ["skip"])
    want = cp.content_sha256(tree, decl, "d" * 64, [], [])
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
