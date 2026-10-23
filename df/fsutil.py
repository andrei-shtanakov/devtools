"""Durable-write primitives (spec 3.3, "Order of durable writing").

Every synchronisation error is raised: a failed sync is a refusal.
"""

from __future__ import annotations

import contextlib
import enum
import errno
import fcntl
import os
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

_IS_DARWIN = sys.platform == "darwin"
_F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", 51)
_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
_SYSCTL = "/usr/sbin/sysctl"
_SAFE_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
_BOOT_ID_LINUX = Path("/proc/sys/kernel/random/boot_id")
_DIR_NOFOLLOW = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
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


@contextlib.contextmanager
def exact_mode() -> Iterator[None]:
    """`umask 0` around one creation: the object is born with exactly the
    requested mode, so its name is never visible at a narrower one. The
    umask is process-wide: no other thread of `df` creates objects (the
    only one, `SpawnedProducer`'s stderr drain, reads and closes its own
    descriptor)."""
    old: int | None = None
    try:
        old = os.umask(0)
        yield
    finally:
        if old is not None:
            os.umask(old)


def make_dir(
    parent_fd: int, name: str, mode: int, *, path: Path, repair: bool = True
) -> bool:
    """Make directory `name` in `parent_fd` durable in its final form.

    Absent: `mkdir(mode)` under `exact_mode` -> `fault_point("created")` ->
    `sync_dir(itself)` -> `sync_dir(parent)`: the name never exists at
    another mode, and the mode (the directory's own metadata) and the name
    (the parent's entry) are durable on return (spec 3.3).

    A created directory whose mode is not `mode` after all (Linux: born
    `S_ISGID` in a setgid parent) gets `fchmod(mode)` ->
    `fault_point("chmod")` before its own sync.

    Present -- settle what is seen (spec 3.3, r6): a directory of the
    current user whose mode is `repairable` (narrower, or with an inherited
    `S_ISGID`) gets `fchmod(mode)` -> `fault_point("chmod")` (only with `repair`; a
    system directory above `df`'s own is never changed); a wider mode or
    another owner is never touched -- the caller refuses it. Then a
    directory is synced itself, and the parent always. Returns whether it
    was created (`path` names it for the fault points)."""
    try:
        with exact_mode():
            os.mkdir(name, mode, dir_fd=parent_fd)
    except FileExistsError:
        _settle_found(parent_fd, name, mode, path=path, repair=repair)
        return False
    fault_point("created", path)
    fd = os.open(name, _DIR_NOFOLLOW, dir_fd=parent_fd)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:  # Linux: inherited S_ISGID
            os.fchmod(fd, mode)
            fault_point("chmod", path)
        sync_dir(fd)
    finally:
        os.close(fd)
    sync_dir(parent_fd)
    return True


def _settle_found(
    parent_fd: int, name: str, mode: int, *, path: Path, repair: bool
) -> None:
    st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISDIR(st.st_mode):
        fd = os.open(name, _DIR_NOFOLLOW, dir_fd=parent_fd)
        try:
            if repair and repairable(st, mode) and owner_uid(st) == os.geteuid():
                os.fchmod(fd, mode)
                fault_point("chmod", path)
            sync_dir(fd)
        finally:
            os.close(fd)
    sync_dir(parent_fd)


def repairable(st: os.stat_result, mode: int) -> bool:
    """A mode `make_dir` may set to `mode`: not equal, and its bits a subset
    of `mode`'s plus `S_ISGID` (Linux inherits it from a setgid parent).
    Only narrowing: no write for group or others is ever added."""
    have = stat.S_IMODE(st.st_mode)
    return have != mode and not have & ~(mode | stat.S_ISGID)


def create_file(path: Path, flags: int, mode: int, *, dir_fd: int | None = None) -> int:
    """Create `path` exclusively, durable in its final form; return the fd.

    `open(O_CREAT|O_EXCL, mode)` under `exact_mode` (born with exactly
    `mode`) -> `fault_point("created")` -> `sync_file` -> `sync_dir(parent)`.
    A found name is settled first -- a regular file synced itself, the
    parent always (spec 3.3 r6) -- then `FileExistsError` propagates: the
    caller opens and judges what is there."""
    target = path.name if dir_fd is not None else path
    parent: int | Path = dir_fd if dir_fd is not None else path.parent
    try:
        with exact_mode():
            fd = os.open(target, flags | os.O_CREAT | os.O_EXCL, mode, dir_fd=dir_fd)
    except FileExistsError:
        _settle_found_file(target, dir_fd, parent)
        raise
    try:
        fault_point("created", path)
        sync_file(fd)
        sync_dir(parent)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _settle_found_file(
    target: Path | str, dir_fd: int | None, parent: int | Path
) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(target, flags, dir_fd=dir_fd)
    except OSError:
        fd = None  # a link or no longer there: only the name is settled
    if fd is not None:
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                sync_file(fd)
        finally:
            os.close(fd)
    sync_dir(parent)


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
