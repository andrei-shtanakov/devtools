"""Tests for the canonical tree digest, name checks and link resolution."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path, PurePosixPath

import pytest

from df.digest import (
    TreeError,
    check_links,
    check_name,
    check_names,
    resolve_link_in_tree,
    tree_digest,
)

LR = PurePosixPath("/opt/df/releases/abc-macos-arm64-1")


def _make_tree(root: Path, order: list[str]) -> Path:
    root.mkdir()
    for item in order:
        if item == "d":
            (root / "d").mkdir()
        elif item == "f":
            (root / "f").write_bytes(b"content")
        elif item == "x":
            (root / "x").write_bytes(b"#!/bin/sh\n")
            (root / "x").chmod(0o755)
        elif item == "l":
            os.symlink("f", root / "l")
    if not (root / "d" / "g").exists():
        (root / "d" / "g").write_bytes(b"inner")
    return root


def test_digest_stable_and_order_independent(tmp_path: Path) -> None:
    a = _make_tree(tmp_path / "a", ["d", "f", "x", "l"])
    b = _make_tree(tmp_path / "b", ["l", "x", "f", "d"])
    digest = tree_digest(a)
    assert digest.startswith("sha256:") and len(digest) == len("sha256:") + 64
    assert digest == tree_digest(b) == tree_digest(a)


def test_digest_sensitive_to_exec_bit_content_link_target(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t", ["d", "f", "x", "l"])
    seen = {tree_digest(root)}
    (root / "f").chmod(0o744)
    seen.add(tree_digest(root))
    (root / "f").write_bytes(b"other")
    seen.add(tree_digest(root))
    os.unlink(root / "l")
    os.symlink("x", root / "l")
    seen.add(tree_digest(root))
    (root / "d" / "e").mkdir()
    seen.add(tree_digest(root))
    assert len(seen) == 5


def test_digest_ignores_non_exec_mode_bits(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t", ["d", "f"])
    before = tree_digest(root)
    (root / "f").chmod(0o600)
    (root / "d").chmod(0o700)
    assert tree_digest(root) == before


def test_digest_excludes_release_json(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t", ["d", "f"])
    before = tree_digest(root)
    (root / "release.json").write_text("{}")
    assert tree_digest(root) == before
    (root / "d" / "release.json").write_text("{}")
    assert tree_digest(root) != before


def test_digest_refuses_newline_name_fifo_nfc_collision(tmp_path: Path) -> None:
    newline = _make_tree(tmp_path / "n", ["d"])
    (newline / "a\nb").write_bytes(b"")
    with pytest.raises(TreeError, match="newline"):
        tree_digest(newline)

    fifo = _make_tree(tmp_path / "p", ["d"])
    os.mkfifo(fifo / "pipe")
    with pytest.raises(TreeError, match="unsupported file type"):
        tree_digest(fifo)

    nfc, nfd = "é", unicodedata.normalize("NFD", "é")
    with pytest.raises(TreeError, match="NFC"):
        check_names([nfc.encode(), nfd.encode()])
    collide = _make_tree(tmp_path / "c", ["d"])
    (collide / nfc).write_bytes(b"1")
    try:
        (collide / nfd).write_bytes(b"2")
    except FileExistsError:
        return  # normalization-insensitive FS: the pair cannot exist on disk
    if len(os.listdir(collide)) < 3:
        return  # APFS opened the NFC file: covered by check_names above
    with pytest.raises(TreeError, match="NFC"):
        tree_digest(collide)


def test_digest_refuses_newline_in_link_target(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t", ["d"])
    os.symlink("a\nb", root / "l")
    with pytest.raises(TreeError, match="newline"):
        tree_digest(root)


@pytest.mark.parametrize(
    "name",
    [b"\xff", b"a\nb", b"a\0b", b"", b".", b"..", b"a/b"],
)
def test_check_name_refuses(name: bytes) -> None:
    with pytest.raises(TreeError):
        check_name(name)


def test_check_name_accepts_utf8() -> None:
    assert check_name("é-file.txt".encode()) == "é-file.txt"


def test_digest_root_must_be_directory(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"")
    os.symlink(tmp_path, tmp_path / "l")
    for path in (tmp_path / "f", tmp_path / "l", tmp_path / "missing"):
        with pytest.raises(TreeError):
            tree_digest(path)


# --- link resolution -------------------------------------------------------


def _link_tree(tmp_path: Path, links: dict[str, str]) -> Path:
    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "file").write_bytes(b"")
    for name, target in links.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
    return tree


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "../../outside",
        "/opt/df/releases/other/x",
        f"{LR}/../../../../etc/passwd",
        f"{LR}/a/../../x",
        f"{LR}x/y",
    ],
)
def test_escaping_link_refused(tmp_path: Path, target: str) -> None:
    tree = _link_tree(tmp_path, {"a/l": target})
    with pytest.raises(TreeError):
        resolve_link_in_tree(tree, LR, PurePosixPath("a/l"))
    assert len(check_links(tree, LR)) == 1


def test_internal_links_resolve(tmp_path: Path) -> None:
    tree = _link_tree(
        tmp_path,
        {"rel": "a/b/file", "abs": f"{LR}/a/b/file", "a/up": "../a/./b/file"},
    )
    for link in ("rel", "abs", "a/up"):
        got = resolve_link_in_tree(tree, LR, PurePosixPath(link))
        assert got == PurePosixPath("a/b/file")
    assert check_links(tree, LR) == []


def test_escape_via_link_chain_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"a1": "b1", "b1": "/etc"})
    problems = check_links(tree, LR)
    assert len(problems) == 2
    assert any(p.startswith("a1:") for p in problems)
    assert any(p.startswith("b1:") for p in problems)


def test_escape_via_intermediate_dir_link_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"d": "/tmp", "f": "d/x"})
    with pytest.raises(TreeError):
        resolve_link_in_tree(tree, LR, PurePosixPath("f"))


def test_link_through_internal_dir_link_resolves(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"d": "a/b", "f": "d/file"})
    got = resolve_link_in_tree(tree, LR, PurePosixPath("f"))
    assert got == PurePosixPath("a/b/file")


def test_link_rel_through_symlinked_parent_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink("a/b/file", outside / "x")
    os.symlink(outside, tree / "d")
    with pytest.raises(TreeError, match="not a directory"):
        resolve_link_in_tree(tree, LR, PurePosixPath("d/x"))


def test_link_cycle_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"a1": "b1", "b1": "a1"})
    with pytest.raises(TreeError, match="too many"):
        resolve_link_in_tree(tree, LR, PurePosixPath("a1"))


def test_dangling_link_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"l": "a/missing"})
    with pytest.raises(TreeError, match="dangling"):
        resolve_link_in_tree(tree, LR, PurePosixPath("l"))


def test_file_as_intermediate_component_refused(tmp_path: Path) -> None:
    tree = _link_tree(tmp_path, {"l": "a/b/file/../file"})
    with pytest.raises(TreeError):
        resolve_link_in_tree(tree, LR, PurePosixPath("l"))


@pytest.mark.parametrize(
    "root",
    [
        "/opt/df/./r",
        "/opt//df/r",
        "/opt/df/../r",
        "opt/df/r",
        "//opt/df/r",
        "/opt/df/r/",
        "/",
        PurePosixPath("/opt/df/../r"),
    ],
)
def test_noncanonical_logical_root_refused(
    tmp_path: Path, root: str | PurePosixPath
) -> None:
    tree = _link_tree(tmp_path, {"l": "a/b/file"})
    with pytest.raises(TreeError, match="logical root"):
        check_links(tree, root)
    with pytest.raises(TreeError, match="logical root"):
        resolve_link_in_tree(tree, root, PurePosixPath("l"))
