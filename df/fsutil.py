"""Durable-write primitives (spec 3.3, "Order of durable writing").

Every synchronisation error is raised: a failed sync is a refusal.
"""

from __future__ import annotations

import enum
import errno
import fcntl
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Literal

_IS_DARWIN = sys.platform == "darwin"
_F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", 51)
_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
_SYSCTL = "/usr/sbin/sysctl"
_SAFE_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
_BOOT_ID_LINUX = Path("/proc/sys/kernel/random/boot_id")
# Devices (`st_dev`) where a directory refused F_FULLFSYNC in this process. Every
# caller of `sync_dir` drops its `SyncNote`; the note is collected here instead and
# the acceptance driver journals it for the evidence (decision (a), 2026-10-21).
_SYNC_NOTES: set[int] = set()

FaultKind = Literal["renamed", "unlinked", "created", "chown", "chmod"]


class SyncNote(enum.Enum):
    """Evidence note returned by `sync_dir`."""

    OK = "ok"
    FULLFSYNC_UNSUPPORTED = "fullfsync-unsupported"


def owner_uid(st: os.stat_result) -> int:
    """Owner of a stat result: the one seam every owner decision reads.

    Tests replace this function to model root and `df-build` while running
    as one user; production reads `st_uid`.
    """
    return st.st_uid


def fault_point(kind: FaultKind, path: Path) -> None:
    """Window "visible, not yet durable"; tests replace this function."""


def sync_file(fd: int) -> None:
    """Flush a file descriptor to stable storage (F_FULLFSYNC on macOS)."""
    if _IS_DARWIN:
        fcntl.fcntl(fd, _F_FULLFSYNC)
        return
    os.fsync(fd)


def sync_dir(fd_or_path: int | Path) -> SyncNote:
    """Flush a directory; report when the platform lacks F_FULLFSYNC for it."""
    if isinstance(fd_or_path, int):
        return _sync_dir_fd(fd_or_path)
    fd = os.open(fd_or_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return _sync_dir_fd(fd)
    finally:
        os.close(fd)


def _sync_dir_fd(fd: int) -> SyncNote:
    os.fsync(fd)
    if not _IS_DARWIN:
        return SyncNote.OK
    try:
        fcntl.fcntl(fd, _F_FULLFSYNC)
    except OSError as exc:
        if exc.errno in _UNSUPPORTED:
            _SYNC_NOTES.add(os.fstat(fd).st_dev)
            return SyncNote.FULLFSYNC_UNSUPPORTED
        raise
    return SyncNote.OK


def sync_notes() -> frozenset[int]:
    """Devices where a directory sync lacked F_FULLFSYNC in this process."""
    return frozenset(_SYNC_NOTES)


def reset_sync_notes() -> None:
    """Forget the collected notes (tests; a new evidence period)."""
    _SYNC_NOTES.clear()


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Atomically replace `path` with `text`, durably.

    Temp file in the same directory (O_EXCL) -> sync_file -> rename ->
    fault_point("renamed") -> sync_dir(parent).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    _write_tmp(tmp, text, mode)
    try:
        os.rename(tmp, path)
    except BaseException:
        _discard(tmp)
        raise
    fault_point("renamed", path)
    sync_dir(path.parent)


def _write_tmp(tmp: Path, text: str, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(tmp, flags, mode)
    try:
        os.fchmod(fd, mode)
        _write_all(fd, text.encode("utf-8"))
        sync_file(fd)
    except BaseException:
        os.close(fd)
        _discard(tmp)
        raise
    os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _discard(tmp: Path) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def make_dir(parent_fd: int, name: str, mode: int) -> bool:
    """Create directory `name` in `parent_fd` with exactly `mode`, durably.

    `mkdir 0700` -> open (`O_DIRECTORY|O_NOFOLLOW`) -> `fchmod(mode)` ->
    `sync_dir(fd)` -> `sync_dir(parent_fd)`. The mode is the directory's own
    metadata: only a sync of the directory makes it durable; the parent's
    sync makes the name durable (spec 3.3, protocol `verify_chmod`). An
    existing name is left untouched: `False`, nothing synced -- the caller
    judges what is there.
    """
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        return False
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        os.fchmod(fd, mode)
        sync_dir(fd)
    finally:
        os.close(fd)
    sync_dir(parent_fd)
    return True


def remove_durably(path: Path) -> None:
    """Unlink `path`, then fault_point("unlinked"), then sync its parent."""
    os.unlink(path)
    fault_point("unlinked", path)
    sync_dir(path.parent)


def sync_tree(root: Path) -> None:
    """Sync every regular file and directory under `root`, bottom-up.

    Symbolic links are neither followed nor opened.
    """
    for dirpath, _dirs, files in os.walk(
        root, topdown=False, followlinks=False, onerror=_raise
    ):
        base = Path(dirpath)
        for name in files:
            _sync_regular(base / name)
        sync_dir(base)


def _raise(exc: OSError) -> None:
    raise exc


def _sync_regular(path: Path) -> None:
    if not stat.S_ISREG(os.lstat(path).st_mode):
        return
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        sync_file(fd)
    finally:
        os.close(fd)


def _sysctl_boot_id() -> str:
    try:
        result = subprocess.run(
            [_SYSCTL, "-n", "kern.bootsessionuuid"],
            env=_SAFE_ENV,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"sysctl kern.bootsessionuuid failed: {exc}") from exc
    return result.stdout.strip()


def boot_id() -> str:
    """Return the current boot identifier; an empty value is an error."""
    if _IS_DARWIN:
        value = _sysctl_boot_id()
    else:
        value = _BOOT_ID_LINUX.read_text().strip()
    if not value:
        raise OSError("empty boot id")
    return value
