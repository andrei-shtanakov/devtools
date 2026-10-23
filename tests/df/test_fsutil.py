"""Tests for durable-write primitives."""

from __future__ import annotations

import ast
import errno
import os
import stat
import subprocess
from pathlib import Path

import pytest

from df import fsutil
from df.fsutil import (
    SyncNote,
    boot_id,
    remove_durably,
    sync_dir,
    sync_tree,
    write_atomic,
)


def test_write_atomic_syncs_file_then_dir(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    real_sync_file = fsutil.sync_file
    real_rename = os.rename

    def spy_sync_file(fd: int) -> None:
        calls.append("sync_file")
        real_sync_file(fd)

    def spy_rename(src, dst) -> None:  # type: ignore[no-untyped-def]
        calls.append("rename")
        real_rename(src, dst)

    monkeypatch.setattr(fsutil, "sync_file", spy_sync_file)
    monkeypatch.setattr(fsutil.os, "rename", spy_rename)
    monkeypatch.setattr(
        fsutil, "fault_point", lambda kind, path: calls.append(f"fault:{kind}")
    )
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda d: calls.append("sync_dir") or SyncNote.OK
    )
    target = tmp_path / "f.toml"
    write_atomic(target, "a = 1\n")
    assert calls == ["sync_file", "rename", "fault:renamed", "sync_dir"]
    assert target.read_text() == "a = 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f.toml"]


def test_write_atomic_mode(tmp_path: Path) -> None:
    target = tmp_path / "f"
    write_atomic(target, "x", mode=0o600)
    assert target.stat().st_mode & 0o777 == 0o600


def test_fault_point_sees_visible_unsynced_file(monkeypatch, tmp_path: Path) -> None:
    synced: list[object] = []

    def boom(kind: str, path: Path) -> None:
        assert kind == "renamed"
        assert path.read_text() == "new"  # already visible
        assert synced == []  # parent not yet synced
        raise RuntimeError("crash")

    monkeypatch.setattr(fsutil, "fault_point", boom)
    monkeypatch.setattr(fsutil, "sync_dir", lambda d: synced.append(d))
    with pytest.raises(RuntimeError):
        write_atomic(tmp_path / "f", "new")
    assert (tmp_path / "f").read_text() == "new"


def test_sync_error_propagates(monkeypatch, tmp_path: Path) -> None:
    def eio(fd: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(fsutil.os, "fsync", eio)
    monkeypatch.setattr(fsutil, "_IS_DARWIN", False)
    with pytest.raises(OSError) as exc:
        write_atomic(tmp_path / "f", "x")
    assert exc.value.errno == errno.EIO
    assert not (tmp_path / "f").exists()
    assert list(tmp_path.iterdir()) == []


def test_write_atomic_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "f"
    write_atomic(target, "one")
    write_atomic(target, "two")
    assert target.read_text() == "two"


def test_remove_durably(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    target = tmp_path / "f"
    target.write_text("x")
    monkeypatch.setattr(
        fsutil,
        "fault_point",
        lambda kind, path: calls.append(f"fault:{kind}:{path.exists()}"),
    )
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda d: calls.append("sync_dir") or SyncNote.OK
    )
    remove_durably(target)
    assert calls == ["fault:unlinked:False", "sync_dir"]


def test_remove_durably_missing_is_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        remove_durably(tmp_path / "absent")


def test_dir_fullfsync_unsupported_is_reported(monkeypatch, tmp_path: Path) -> None:
    def unsupported(fd: int, cmd: int) -> None:
        raise OSError(errno.ENOTSUP, "nope")

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil, "_F_FULLFSYNC", 51)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", unsupported)
    monkeypatch.setattr(fsutil, "_SYNC_NOTES", set())
    assert sync_dir(tmp_path) is SyncNote.FULLFSYNC_UNSUPPORTED


def test_unsupported_fullfsync_is_collected_by_device(
    monkeypatch, tmp_path: Path
) -> None:
    """Decision (a): callers drop the SyncNote, the collector keeps it (F10)."""

    def unsupported(fd: int, cmd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.ENOTSUP, "nope")

    monkeypatch.setattr(fsutil, "_SYNC_NOTES", set())
    assert fsutil.sync_notes() == frozenset()
    sync_dir(tmp_path)  # supported (or Linux): nothing collected
    assert fsutil.sync_notes() == frozenset()
    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", unsupported)
    write_atomic(tmp_path / "f", "x")  # a caller that drops the note
    assert fsutil.sync_notes() == frozenset({os.stat(tmp_path).st_dev})
    fsutil.reset_sync_notes()
    assert fsutil.sync_notes() == frozenset()


def test_dir_fullfsync_other_error_raises(monkeypatch, tmp_path: Path) -> None:
    def broken(fd: int, cmd: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil, "_F_FULLFSYNC", 51)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", broken)
    with pytest.raises(OSError):
        sync_dir(tmp_path)


def test_sync_dir_accepts_fd_and_path(tmp_path: Path) -> None:
    assert sync_dir(tmp_path) in set(SyncNote)
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        assert sync_dir(fd) in set(SyncNote)
    finally:
        os.close(fd)


def test_sync_tree_does_not_follow_links(monkeypatch, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a").write_text("a")
    (root / "link").symlink_to(outside)
    (root / "flink").symlink_to(outside / "secret")
    seen: list[str] = []
    monkeypatch.setattr(fsutil, "sync_file", lambda fd: seen.append("f"))
    monkeypatch.setattr(fsutil, "sync_dir", lambda d: seen.append("d") or SyncNote.OK)
    sync_tree(root)
    assert seen == ["f", "d", "d"]  # sub/a, sub, root (bottom-up)


def test_boot_id_nonempty() -> None:
    assert boot_id()


def test_boot_id_empty_is_error(monkeypatch) -> None:
    class R:
        stdout = "\n"
        returncode = 0

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(OSError):
        boot_id()


def test_sync_tree_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        sync_tree(tmp_path / "absent")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores chmod")
def test_sync_tree_unreadable_subdir_raises(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a").write_text("a")
    sub.chmod(0)
    try:
        with pytest.raises(OSError):
            sync_tree(tmp_path)
    finally:
        sub.chmod(0o755)


def test_boot_id_uses_absolute_sysctl_and_minimal_env(monkeypatch) -> None:
    seen: dict = {}

    class R:
        stdout = "uuid-1\n"

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen["argv"], seen["env"] = argv, kwargs.get("env")
        return R()

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", fake_run)
    assert boot_id() == "uuid-1"
    assert seen["argv"][0] == "/usr/sbin/sysctl"
    assert seen["env"] == {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def test_boot_id_sysctl_failure_is_oserror(monkeypatch) -> None:
    def fail(argv, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["timeout"] == 10
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", fail)
    with pytest.raises(OSError):
        boot_id()


def test_sync_tree_does_not_hang_on_fifo(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    sync_tree(tmp_path)  # fifo is skipped, must return


# ---------- make_dir: a created directory is durable with its mode ----------


@pytest.fixture()
def crash_model(fake_layout, monkeypatch):  # noqa: ANN001, ANN201
    from tests.df.crash_model import CrashModel

    model = CrashModel(fake_layout.layout.root, fake_layout)
    model.install(monkeypatch)
    yield model
    model.close()


@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_make_dir_mode_and_name_are_durable(crash_model, umask: int) -> None:  # noqa: ANN001
    """The mode is the directory's own metadata (its own sync), the name the
    parent's: an OS crash right after `make_dir` keeps both."""
    root = crash_model.root
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    old = os.umask(umask)
    try:
        assert fsutil.make_dir(fd, "d", 0o711) is True
    finally:
        os.umask(old)
        os.close(fd)
    assert crash_model.unsynced() == []
    crash_model.os_crash()
    assert stat.S_IMODE(os.stat(root / "d").st_mode) == 0o711


def test_make_dir_leaves_an_existing_name(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "d").mkdir(mode=0o700)
    synced: list[object] = []
    monkeypatch.setattr(fsutil, "sync_dir", synced.append)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert fsutil.make_dir(fd, "d", 0o755) is False
    finally:
        os.close(fd)
    assert stat.S_IMODE(os.stat(tmp_path / "d").st_mode) == 0o700
    assert synced == []


#: Every directory-creating call site of `df/` and why its result is durable
#: (the class of the 9-C finding: a mode set after `mkdir` and only the
#: parent synced). A new site fails this test until it is accounted for --
#: by using `fsutil.make_dir`, or by naming its own protocol here.
MKDIR_SITES = {
    ("fsutil", "make_dir"): "the primitive: own sync, then the parent's",
    ("leaves", "prepare_dir"): "protocol prepare_dir: fsync(fd) after fchown",
    ("leaves", "seal"): "protocol seal: sync_dir(staging/<id>) itself",
    ("receive", "_receive"): "protocol accept_copy: sync_tree with the root",
    ("receive", "_create"): "protocol accept_copy: sync_tree",
    ("admin", "_make_dir"): "protocol install_admin: sync_tree(admin/<id>)",
    ("admin", "_ensure_admin_home"): "scratch, mode = creation mode, no fact",
    ("platform", "_make_home"): "sync_dir(tmp_fd) after fchown/fchmod",
    ("inputs", "fetch_reference"): "root's checkout, fetched again every run",
}


class _MkdirSites(ast.NodeVisitor):
    """(module, innermost function) of every `*.mkdir`/`*.makedirs` call."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.found: set[tuple[str, str]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("mkdir", "makedirs"):
            self.found.add((self.module, self.stack[-1] if self.stack else ""))
        self.generic_visit(node)


def test_every_mkdir_site_is_accounted_for() -> None:
    found: set[tuple[str, str]] = set()
    for path in sorted(Path(fsutil.__file__).parent.glob("*.py")):
        visitor = _MkdirSites(path.stem)
        visitor.visit(ast.parse(path.read_text()))
        found |= visitor.found
    assert found == set(MKDIR_SITES)
