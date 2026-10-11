"""Безопасный ввод-вывод раннера (песочница авторского агента, TASK-001).

Дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§15, F27; сценарии §16 Z11, Z14, Z15. Поведение ядра Darwin
(`O_NOFOLLOW_ANY`) проверяется только на macOS — на другой ОС тесты с
маркером `darwin_only` пропускаются с названной причиной; переносимые тесты
(контракт `bundle_dir`, отказ «только macOS») идут везде.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from governance import safe_io

darwin_only = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW_ANY"),
    reason="O_NOFOLLOW_ANY есть только в ядре Darwin (macOS)",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    # /var/folders → /private/var/folders: корень канонизируется заранее,
    # как его канонизирует вызывающий (§15, F27)
    real = Path(os.path.realpath(tmp_path)) / "root"
    real.mkdir()
    return real


def _snapshot(base: Path) -> dict[str, tuple[int, bytes | str]]:
    """Тип, inode и содержимое (или цель ссылки) всего под `base`."""
    out: dict[str, tuple[int, bytes | str]] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                out[str(p)] = (st.st_ino, os.readlink(p))
            elif stat.S_ISREG(st.st_mode):
                out[str(p)] = (st.st_ino, p.read_bytes())
            else:
                out[str(p)] = (st.st_ino, "dir")
    return out


# --- переносимые: контракт bundle_dir (Z15) и отказ вне macOS ---------------


@pytest.mark.parametrize(
    "bundle_dir",
    [
        "",
        ".",
        "/abs/bundle",
        "../outside",
        "ws/../../outside",
        "ws//bundle",
        "ws/./bundle",
        "ws/.git/bundle",
        ".git",
    ],
)
def test_bundle_dir_rejects_unsafe_forms(tmp_path: Path, bundle_dir: str) -> None:
    base = Path(os.path.realpath(tmp_path))
    with pytest.raises(safe_io.SafeIOError):
        safe_io.validate_bundle_dir(base, bundle_dir, git_dirs=())


def test_bundle_dir_accepts_plain_relative(tmp_path: Path) -> None:
    base = Path(os.path.realpath(tmp_path))
    got = safe_io.validate_bundle_dir(
        base, "workstreams/x/spec", git_dirs=(base / ".git",)
    )
    assert str(got) == "workstreams/x/spec"


def test_bundle_dir_must_not_overlap_git_dirs(tmp_path: Path) -> None:
    base = Path(os.path.realpath(tmp_path))
    gitdir = base / "ws" / "meta"
    with pytest.raises(safe_io.SafeIOError, match="gitdir"):
        safe_io.validate_bundle_dir(base, "ws/meta/bundle", git_dirs=(gitdir,))
    with pytest.raises(safe_io.SafeIOError, match="gitdir"):
        safe_io.validate_bundle_dir(base, "ws", git_dirs=(gitdir,))


def test_bundle_dir_rejects_symlinked_target_root(tmp_path: Path) -> None:
    base = Path(os.path.realpath(tmp_path))
    real = base / "real"
    real.mkdir()
    link = base / "link"
    link.symlink_to(real)
    # корень цели обязан быть каноническим: ссылка в нём — отказ
    with pytest.raises(safe_io.SafeIOError, match="канонич"):
        safe_io.validate_bundle_dir(link, "bundle", git_dirs=())


def test_without_nofollow_any_every_call_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_io, "NOFOLLOW_ANY", None)
    for call in (
        lambda: safe_io.read_regular(tmp_path, "f", limit=10),
        lambda: safe_io.list_tree(tmp_path, "."),
        lambda: safe_io.ensure_dirs(tmp_path, "a"),
        lambda: safe_io.publish(tmp_path, "a/f", b"x"),
    ):
        with pytest.raises(safe_io.SafeIOError, match="только macOS"):
            call()


# --- macOS: канонизация -----------------------------------------------------


@darwin_only
def test_non_canonical_root_refuses(root: Path, tmp_path: Path) -> None:
    """Корень со ссылкой в пути (как `/var` → `/private/var`) — отказ:
    канонизирует вызывающий, модуль ссылок не раскрывает."""
    (root / "f.md").write_bytes(b"x")
    alias = Path(os.path.realpath(tmp_path)) / "alias"
    alias.symlink_to(root)
    with pytest.raises(safe_io.SafeIOError, match="ссылка"):
        safe_io.read_regular(alias, "f.md", limit=10)
    with pytest.raises(safe_io.SafeIOError, match="ссылка"):
        safe_io.publish(alias, "g.md", b"x")
    assert not (root / "g.md").exists()


# --- macOS: источник (Z11) ---------------------------------------------------


@darwin_only
def test_read_regular_returns_bytes(root: Path) -> None:
    (root / "a").mkdir()
    (root / "a" / "f.md").write_bytes(b"node")
    assert safe_io.read_regular(root, "a/f.md", limit=100) == b"node"


@darwin_only
def test_read_refuses_symlink_leaf(root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_bytes(b"token")
    (root / "f.md").symlink_to(secret)
    with pytest.raises(safe_io.SafeIOError):
        safe_io.read_regular(root, "f.md", limit=100)


@darwin_only
def test_read_refuses_symlinked_parent(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.md").write_bytes(b"token")
    (root / "a").symlink_to(outside)
    with pytest.raises(safe_io.SafeIOError):
        safe_io.read_regular(root, "a/f.md", limit=100)


@darwin_only
def test_read_refuses_hardlink(root: Path, tmp_path: Path) -> None:
    twin = tmp_path / "twin"
    twin.write_bytes(b"x")
    os.link(twin, root / "f.md")
    with pytest.raises(safe_io.SafeIOError, match="st_nlink"):
        safe_io.read_regular(root, "f.md", limit=100)


@darwin_only
def test_read_refuses_fifo_without_hanging(root: Path) -> None:
    os.mkfifo(root / "f.md")
    result: list[BaseException | bytes] = []

    def go() -> None:
        try:
            result.append(safe_io.read_regular(root, "f.md", limit=100))
        except BaseException as exc:  # noqa: BLE001 — фиксируем исход в потоке
            result.append(exc)

    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "открытие FIFO зависло"
    assert isinstance(result[0], safe_io.SafeIOError)


@darwin_only
def test_read_refuses_over_limit(root: Path) -> None:
    (root / "f.md").write_bytes(b"x" * 11)
    assert safe_io.read_regular(root, "f.md", limit=11) == b"x" * 11
    with pytest.raises(safe_io.SafeIOError, match="потолк"):
        safe_io.read_regular(root, "f.md", limit=10)


@darwin_only
def test_read_bounds_bytes_of_growing_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Рост файла после открытия: читается не больше `limit + 1` байт."""
    path = root / "f.md"
    path.write_bytes(b"x" * 4)
    reads: list[int] = []
    real_read = os.read

    def growing_read(fd: int, n: int) -> bytes:
        reads.append(n)
        with open(path, "ab") as fh:  # рост между чтениями
            fh.write(b"y" * 1000)
        return real_read(fd, n)

    monkeypatch.setattr(safe_io.os, "read", growing_read)
    with pytest.raises(safe_io.SafeIOError, match="потолк"):
        safe_io.read_regular(root, "f.md", limit=16)
    assert sum(reads) <= 17


@darwin_only
def test_read_timeout(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "f.md").write_bytes(b"x" * 100)
    real_read = os.read

    def slow_read(fd: int, n: int) -> bytes:
        time.sleep(0.05)
        return real_read(fd, 1)

    monkeypatch.setattr(safe_io.os, "read", slow_read)
    with pytest.raises(safe_io.SafeIOError, match="время"):
        safe_io.read_regular(root, "f.md", limit=1000, timeout=0.1)


# --- macOS: обход без следования ссылкам -------------------------------------


@darwin_only
def test_list_tree_reports_links_without_following(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    (root / "d").mkdir()
    (root / "d" / "f.md").write_bytes(b"n")
    (root / "d" / "link").symlink_to(outside)
    os.mkfifo(root / "fifo")
    got = {(e.path, e.kind) for e in safe_io.list_tree(root, ".")}
    assert got == {
        ("d", "dir"),
        ("d/f.md", "file"),
        ("d/link", "symlink"),
        ("fifo", "other"),
    }


# --- macOS: назначение (Z14) -------------------------------------------------


@darwin_only
def test_publish_creates_parents_and_file(root: Path) -> None:
    safe_io.publish(root, "ws/x/spec/00-charter.md", b"charter")
    assert (root / "ws/x/spec/00-charter.md").read_bytes() == b"charter"
    assert not [p for p in (root / "ws/x/spec").iterdir() if p.name.startswith(".")]


@darwin_only
def test_publish_over_dangling_symlink_does_not_create_target(
    root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target" / ".claude" / "settings.local.json"
    target.parent.mkdir(parents=True)
    (root / "spec").mkdir()
    (root / "spec" / "00-charter.md").symlink_to(target)
    outside_before = _snapshot(tmp_path / "target")
    safe_io.publish(root, "spec/00-charter.md", b'{"hooks": "evil"}')
    node = root / "spec" / "00-charter.md"
    assert not node.is_symlink()
    assert node.read_bytes() == b'{"hooks": "evil"}'
    assert not target.exists()
    assert _snapshot(tmp_path / "target") == outside_before


@darwin_only
def test_publish_over_hardlink_leaves_twin_untouched(
    root: Path, tmp_path: Path
) -> None:
    twin = tmp_path / "twin"
    twin.write_bytes(b"orig")
    (root / "spec").mkdir()
    os.link(twin, root / "spec" / "00-charter.md")
    old_ino = twin.stat().st_ino
    safe_io.publish(root, "spec/00-charter.md", b"new")
    node = root / "spec" / "00-charter.md"
    assert node.read_bytes() == b"new"
    assert node.stat().st_ino != old_ino
    assert twin.read_bytes() == b"orig"


@darwin_only
def test_publish_refuses_symlinked_parent(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "spec").symlink_to(outside)
    before = _snapshot(outside)
    with pytest.raises(safe_io.SafeIOError):
        safe_io.publish(root, "spec/00-charter.md", b"x")
    assert _snapshot(outside) == before


@darwin_only
def test_publish_refuses_directory_leaf(root: Path) -> None:
    (root / "spec" / "00-charter.md").mkdir(parents=True)
    with pytest.raises(safe_io.SafeIOError, match="каталог"):
        safe_io.publish(root, "spec/00-charter.md", b"x")
    assert sorted(p.name for p in (root / "spec").iterdir()) == ["00-charter.md"]


@darwin_only
def test_ensure_dirs_refuses_symlink_component(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "a").symlink_to(outside)
    with pytest.raises(safe_io.SafeIOError):
        safe_io.ensure_dirs(root, "a/b")
    assert list(outside.iterdir()) == []


@darwin_only
def test_failed_publish_changes_nothing_outside_root(
    root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"k")
    (root / "spec").mkdir()
    (root / "spec" / "deep").symlink_to(outside)
    before = _snapshot(tmp_path)
    with pytest.raises(safe_io.SafeIOError):
        safe_io.publish(root, "spec/deep/00-charter.md", b"x")
    assert _snapshot(tmp_path) == before
