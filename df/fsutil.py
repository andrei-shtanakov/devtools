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

FaultKind = Literal["renamed", "unlinked", "created", "chown", "chmod"]


class SyncNote(enum.Enum):
    """Evidence note returned by `sync_dir`."""

    OK = "ok"
    FULLFSYNC_UNSUPPORTED = "fullfsync-unsupported"


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
            return SyncNote.FULLFSYNC_UNSUPPORTED
        raise
    return SyncNote.OK


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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        sync_file(fd)
    finally:
        os.close(fd)


def boot_id() -> str:
    """Return the current boot identifier; an empty value is an error."""
    if _IS_DARWIN:
        result = subprocess.run(
            [_SYSCTL, "-n", "kern.bootsessionuuid"],
            env=_SAFE_ENV,
            capture_output=True,
            text=True,
            check=True,
        )
        value = result.stdout.strip()
    else:
        value = _BOOT_ID_LINUX.read_text().strip()
    if not value:
        raise OSError("empty boot id")
    return value
