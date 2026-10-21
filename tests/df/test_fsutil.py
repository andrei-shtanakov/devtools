"""Tests for durable-write primitives."""

from __future__ import annotations

import errno
import os
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
    assert sync_dir(tmp_path) is SyncNote.FULLFSYNC_UNSUPPORTED


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
