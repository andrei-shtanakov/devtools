"""Receive a release tree from a tar stream as root (spec 3.3 item 3).

The stream comes from the untrusted build user: every byte is hostile. It
is read only through `BoundedReader` (one deadline, one raw-byte cap) and
parsed by this module's own ustar/pax reader; `tarfile` is never imported,
since it parses extended headers before any limit can apply.

Also here: removal of root-owned trees (`remove_own_tree`,
`remove_empty_dir`), protocol rows of the same names in spec 3.3.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import shutil
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from df import fsutil
from df.digest import TreeError, check_name, nfc_key

#: Owner a tree must have before `remove_own_tree` / `remove_empty_dir`.
ROOT_UID = 0

BLOCK = 512
PAX_USED = frozenset({"path", "linkpath", "size"})
PAX_IGNORED = frozenset(
    {
        "mtime",
        "atime",
        "ctime",
        "uid",
        "gid",
        "uname",
        "gname",
        "LIBARCHIVE.creationtime",
    }
)

_CHUNK = 1 << 20
_READ_SOME = 64 << 10
_ZERO_BLOCK = bytes(BLOCK)
_MAGIC = b"ustar\x0000"
_OCTAL = re.compile(rb"[0-7]+[ \0]*")
_PAX_LENGTH = re.compile(rb"[1-9][0-9]*")
_PAX_SIZE = re.compile(rb"[0-9]{1,20}")
_PAX_LENGTH_DIGITS = 20
_REGULAR = (b"0", b"\0")
_HARDLINK = b"1"
_SYMLINK = b"2"
_DIRECTORY = b"5"
_PAX = b"x"
_SUPPORTED = (*_REGULAR, _HARDLINK, _SYMLINK, _DIRECTORY)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_OLD_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
#: `select.select` cannot watch descriptors at or above this value.
_FD_SETSIZE = 1024
_DEFAULT_PATH_MAX = 1024 if sys.platform == "darwin" else 4096

_Parts = tuple[str, ...]


@dataclass(frozen=True)
class CopyLimits:
    """Limits of one receive (spec 3.3 item 3, "Limits")."""

    max_bytes: int = 8 << 30
    max_stream_bytes: int = 9 << 30
    max_objects: int = 500_000
    max_depth: int = 64
    max_path: int = 4096
    max_pax: int = 64 << 10
    max_seconds: float = 1800.0
    reap_seconds: float = 10.0


class Producer(Protocol):
    """The whole producer launch chain (spec 3.3 item 3, "Lifecycle")."""

    @property
    def fd(self) -> int:
        """Producer stdout."""
        ...

    def wait(self, timeout: float) -> int:
        """Exit code of the direct child; not exited in time -> `TreeError`."""
        ...

    def end(self) -> None:
        """End the whole chain and verify nothing is left, else `TreeError`."""
        ...


class BoundedReader:
    """The only reader of the stream: one deadline, one raw-byte cap."""

    def __init__(self, fd: int, *, deadline: float, max_bytes: int) -> None:
        self._fd = fd
        self._deadline = deadline
        self._max_bytes = max_bytes
        self.consumed = 0
        os.set_blocking(fd, False)

    def remaining(self) -> float:
        """Seconds left until the deadline (may be negative)."""
        return self._deadline - time.monotonic()

    def read_exact(self, n: int) -> bytes:
        """Exactly `n` bytes; end of stream, deadline or cap -> `TreeError`."""
        out = bytearray()
        while len(out) < n:
            chunk = self._read(n - len(out))
            if not chunk:
                raise TreeError("unexpected end of stream")
            out += chunk
        return bytes(out)

    def read_some(self) -> bytes:
        """One read of up to 64 KiB; `b""` is end of stream."""
        return self._read(_READ_SOME)

    def _read(self, n: int) -> bytes:
        while True:
            left = self.remaining()
            if left <= 0:
                raise TreeError("deadline exceeded while reading the stream")
            if not self._wait_readable(left):
                continue
            try:
                chunk = os.read(self._fd, n)
            except BlockingIOError:
                continue
            self.consumed += len(chunk)
            if self.consumed > self._max_bytes:
                raise TreeError("stream exceeds max_stream_bytes")
            return chunk

    def _wait_readable(self, timeout: float) -> bool:
        if self._fd < _FD_SETSIZE:
            ready, _, _ = select.select([self._fd], [], [], timeout)
            return bool(ready)
        poll_factory = getattr(select, "poll", None)
        if poll_factory is None:
            raise TreeError(f"stream descriptor {self._fd} cannot be watched")
        poller = poll_factory()
        poller.register(self._fd, select.POLLIN)
        return bool(poller.poll(timeout * 1000))


@dataclass(frozen=True)
class _Header:
    typeflag: bytes
    name: bytes
    linkname: bytes
    size: int
    mode: int


def _noop(point: str, rel: str) -> None:
    """Default test hook."""


def receive_tree(
    producer: Producer,
    dst_parent: Path,
    name: str,
    *,
    limits: CopyLimits = CopyLimits(),
    _hook: Callable[[str, str], None] = _noop,
) -> None:
    """Receive the producer's tar stream into `dst_parent/name` (root `0700`).

    Effects (protocol `accept_copy`): `created` (partial copy) ->
    `producer.end()` -> directories `0755`, `created` (copy complete) ->
    `sync_tree` -> `sync_dir(dst_parent)`. The producer is always ended. On
    refusal a `TreeError` is raised and the partial copy stays: this
    function removes nothing. Every refusal, including an `OSError` or
    `ValueError` of the filesystem work, is a `TreeError`.
    """
    _require_producer(producer)
    try:
        _receive_and_settle(producer, dst_parent, name, limits, _hook)
    except (OSError, ValueError) as exc:
        raise TreeError(f"cannot receive {dst_parent / name}: {exc}") from exc


def _receive_and_settle(
    producer: Producer,
    dst_parent: Path,
    name: str,
    limits: CopyLimits,
    hook: Callable[[str, str], None],
) -> None:
    target = dst_parent / name
    with contextlib.ExitStack() as fds:
        try:
            receiver = _receive(producer, dst_parent, name, limits, hook, fds)
        finally:
            producer.end()
        receiver.open_dirs()
    fsutil.fault_point("created", target)
    fsutil.sync_tree(target)
    fsutil.sync_dir(dst_parent)


def _require_producer(producer: object) -> None:
    fd = getattr(producer, "fd", None)
    if (
        not isinstance(fd, int)
        or isinstance(fd, bool)
        or not callable(getattr(producer, "wait", None))
        or not callable(getattr(producer, "end", None))
    ):
        raise TypeError("receive_tree needs a Producer with fd, wait() and end()")


def _receive(
    producer: Producer,
    dst_parent: Path,
    name: str,
    limits: CopyLimits,
    hook: Callable[[str, str], None],
    fds: contextlib.ExitStack,
) -> _Receiver:
    deadline = time.monotonic() + limits.max_seconds
    leaf = check_name(os.fsencode(name))
    parent_fd = os.open(dst_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fds.callback(os.close, parent_fd)
    try:
        os.mkdir(leaf, 0o700, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise TreeError(f"destination exists: {dst_parent / leaf}") from exc
    fsutil.fault_point("created", dst_parent / leaf)
    root_fd = os.open(leaf, _DIR_FLAGS, dir_fd=parent_fd)
    fds.callback(os.close, root_fd)
    os.fchmod(root_fd, 0o700)
    reader = BoundedReader(
        producer.fd, deadline=deadline, max_bytes=limits.max_stream_bytes
    )
    room = _path_room(dst_parent, dst_parent / leaf, limits)
    receiver = _Receiver(reader, root_fd, limits, hook, room)
    receiver.run()
    _check_exit(producer, reader)
    return receiver


def _path_room(dst_parent: Path, target: Path, limits: CopyLimits) -> int:
    """Longest relative member path whose full destination path fits."""
    try:
        path_max = os.pathconf(dst_parent, "PC_PATH_MAX")
    except (OSError, ValueError):
        path_max = -1
    if path_max <= 0:
        path_max = _DEFAULT_PATH_MAX
    # full path = target + "/" + rel, plus the terminating NUL
    return min(limits.max_path, path_max - len(os.fsencode(target)) - 2)


def _check_exit(producer: Producer, reader: BoundedReader) -> None:
    left = reader.remaining()
    if left <= 0:
        raise TreeError("deadline exceeded before the producer exited")
    code = producer.wait(left)
    if code != 0:
        raise TreeError(f"producer exited with code {code}")


# --- stream format ----------------------------------------------------------


def parse_header(block: bytes) -> _Header:
    """Parse one 512-byte ustar header; anything malformed is a `TreeError`."""
    expected = _octal(block[148:156], "checksum")
    if expected != sum(block[:148]) + 8 * ord(" ") + sum(block[156:]):
        raise TreeError("header checksum mismatch")
    if block[257:265] != _MAGIC:
        raise TreeError("not a ustar/pax header")
    name = _cstr(block[0:100])
    prefix = _cstr(block[345:500])
    return _Header(
        typeflag=block[156:157],
        name=prefix + b"/" + name if prefix else name,
        linkname=_cstr(block[157:257]),
        size=_octal(block[124:136], "size"),
        mode=_octal(block[100:108], "mode"),
    )


def _cstr(field: bytes) -> bytes:
    return field.split(b"\0", 1)[0]


def _octal(field: bytes, label: str) -> int:
    if not _OCTAL.fullmatch(field):
        raise TreeError(f"invalid numeric field {label}: {field!r}")
    return int(field.rstrip(b" \0"), 8)


def parse_pax(body: bytes) -> dict[str, bytes]:
    """Validate a pax body; return the values of `PAX_USED` keys.

    Record: `<length> <key>=<value>\\n`, the length counts the whole record.
    Ignored keys are skipped but still count as seen; unknown keys refuse.
    """
    used: dict[str, bytes] = {}
    seen: set[str] = set()
    pos = 0
    while pos < len(body):
        end, key, value = _pax_record(body, pos)
        if key in seen:
            raise TreeError(f"repeated pax key: {key}")
        seen.add(key)
        if key in PAX_USED:
            used[key] = _pax_value(key, value)
        elif key not in PAX_IGNORED:
            raise TreeError(f"unsupported pax key: {key}")
        pos = end
    return used


def _pax_record(body: bytes, pos: int) -> tuple[int, str, bytes]:
    space = body.find(b" ", pos, pos + _PAX_LENGTH_DIGITS + 1)
    if space < 0 or not _PAX_LENGTH.fullmatch(body[pos:space]):
        raise TreeError("invalid pax record length")
    end = pos + int(body[pos:space])
    if end < space + 3:
        raise TreeError("pax record shorter than its prefix")
    if end > len(body):
        raise TreeError("pax record runs past the header")
    if body[end - 1 : end] != b"\n":
        raise TreeError("pax record does not end with a newline")
    raw_key, eq, value = body[space + 1 : end - 1].partition(b"=")
    if not eq or not raw_key or not raw_key.isascii():
        raise TreeError("pax record without a valid key")
    return end, raw_key.decode("ascii"), value


def _pax_value(key: str, value: bytes) -> bytes:
    if key == "size" and not _PAX_SIZE.fullmatch(value):
        raise TreeError(f"invalid pax size: {value!r}")
    return value


def member_path(
    raw: bytes, is_dir: bool, *, limits: CopyLimits = CopyLimits()
) -> _Parts:
    """Normalize a member name or hard-link target into checked components.

    Strips one leading `./` and, for a directory, one trailing `/`. The empty
    result is the root record.
    """
    if len(raw) > limits.max_path:
        raise TreeError(f"path longer than max_path: {raw[:64]!r}...")
    if raw.startswith(b"./"):
        raw = raw[2:]
    if is_dir and raw.endswith(b"/"):
        raw = raw[:-1]
    if not raw:
        return ()
    parts = raw.split(b"/")
    if len(parts) > limits.max_depth:
        raise TreeError(f"path deeper than max_depth: {raw[:64]!r}")
    return tuple(check_name(part) for part in parts)


# --- receiver ---------------------------------------------------------------


class _Receiver:
    """State of one receive: accepted directories, files and counters."""

    def __init__(
        self,
        reader: BoundedReader,
        root_fd: int,
        limits: CopyLimits,
        hook: Callable[[str, str], None],
        path_room: int,
    ) -> None:
        self.reader = reader
        self.path_room = path_room
        self.root_fd = root_fd
        self.limits = limits
        self.hook = hook
        self.dirs: set[_Parts] = {()}
        self.files: set[_Parts] = set()
        self.names: dict[_Parts, set[str]] = {}
        self.objects = 0
        self.content = 0
        self.first = True

    def run(self) -> None:
        """Read records up to the end of archive, then drain to EOF."""
        pax: dict[str, bytes] | None = None
        while True:
            block = self.reader.read_exact(BLOCK)
            if block == _ZERO_BLOCK:
                if pax is not None:
                    raise TreeError("pax header without a member")
                self._finish()
                return
            header = parse_header(block)
            if header.typeflag == _PAX:
                if pax is not None:
                    raise TreeError("two pax headers before one member")
                pax = self._read_pax(header.size)
                continue
            self._member(header, pax or {})
            pax = None
            self.first = False

    def _read_pax(self, size: int) -> dict[str, bytes]:
        if size > self.limits.max_pax:
            raise TreeError(f"pax header too large: {size} bytes")
        body = self.reader.read_exact(size)
        self._skip_padding(size)
        return parse_pax(body)

    def _finish(self) -> None:
        if self.reader.read_exact(BLOCK) != _ZERO_BLOCK:
            raise TreeError("missing second zero block")
        while chunk := self.reader.read_some():
            if chunk.count(0) != len(chunk):
                raise TreeError("data after end of archive")

    def _member(self, header: _Header, pax: dict[str, bytes]) -> None:
        kind = header.typeflag
        if kind not in _SUPPORTED:
            raise TreeError(f"unsupported member type {kind!r}")
        size = int(pax["size"]) if "size" in pax else header.size
        parts = member_path(
            pax.get("path", header.name), kind == _DIRECTORY, limits=self.limits
        )
        if not parts:
            self._root_record(kind, size)
            return
        rel = "/".join(parts)
        if len(rel.encode()) > self.path_room:
            raise TreeError(f"path too long for the destination: {rel[:64]}...")
        if size and kind in (_DIRECTORY, _SYMLINK):
            raise TreeError(f"body on a directory or symbolic link record: {rel}")
        self.objects += 1
        if self.objects > self.limits.max_objects:
            raise TreeError("stream exceeds max_objects")
        self.hook("member", rel)
        self._claim(parts)
        try:
            self._create(kind, parts, header, pax, size)
        except OSError as exc:
            raise TreeError(f"cannot create {rel}: {exc.strerror or exc}") from exc

    def _root_record(self, kind: bytes, size: int) -> None:
        if kind != _DIRECTORY:
            raise TreeError("root record must be a directory")
        if not self.first:
            raise TreeError("root record must be the first member")
        if size:
            raise TreeError("body on the root record")

    def _claim(self, parts: _Parts) -> None:
        parent = parts[:-1]
        if parent not in self.dirs:
            raise TreeError(f"parent not received from this stream: {'/'.join(parts)}")
        siblings = self.names.setdefault(parent, set())
        key = nfc_key(parts[-1])
        if key in siblings:
            raise TreeError(
                f"name repeats an earlier member after NFC: {'/'.join(parts)}"
            )
        siblings.add(key)

    def _create(
        self,
        kind: bytes,
        parts: _Parts,
        header: _Header,
        pax: dict[str, bytes],
        size: int,
    ) -> None:
        parent_fd = self._open_dir(parts[:-1])
        try:
            if kind == _DIRECTORY:
                os.mkdir(parts[-1], 0o700, dir_fd=parent_fd)
                self.dirs.add(parts)
            elif kind == _SYMLINK:
                self._symlink(
                    parent_fd, parts[-1], pax.get("linkpath", header.linkname)
                )
            elif kind == _HARDLINK:
                self._hardlink(parent_fd, parts, pax.get("linkpath", header.linkname))
            else:
                self._regular(parent_fd, parts, size, header.mode)
                return
        finally:
            if parent_fd != self.root_fd:
                os.close(parent_fd)
        self._discard(size)

    def _symlink(self, parent_fd: int, leaf: str, target: bytes) -> None:
        if not target or b"\0" in target:
            raise TreeError(f"invalid symbolic link target: {target!r}")
        os.symlink(os.fsdecode(target), leaf, dir_fd=parent_fd)

    def _regular(self, parent_fd: int, parts: _Parts, size: int, mode: int) -> None:
        self._count_content(size)
        fd = os.open(parts[-1], _NEW_FILE, 0o600, dir_fd=parent_fd)
        try:
            left = size
            while left:
                chunk = self.reader.read_exact(min(left, _CHUNK))
                _write_all(fd, chunk)
                if left == size:
                    self.hook("mid-copy", "/".join(parts))
                left -= len(chunk)
            self._skip_padding(size)
            os.fchmod(fd, 0o755 if mode & 0o111 else 0o644)
        finally:
            os.close(fd)
        self.files.add(parts)

    def _hardlink(self, parent_fd: int, parts: _Parts, link: bytes) -> None:
        target = member_path(link, False, limits=self.limits)
        if target not in self.files:
            raise TreeError(f"hard link to a file not received: {link!r}")
        src_fd = self._open_received_file(target)
        try:
            st = os.fstat(src_fd)
            self._count_content(st.st_size)
            dst_fd = os.open(parts[-1], _NEW_FILE, 0o600, dir_fd=parent_fd)
            try:
                while chunk := os.read(src_fd, _CHUNK):
                    _write_all(dst_fd, chunk)
                os.fchmod(dst_fd, stat.S_IMODE(st.st_mode))
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        self.files.add(parts)

    def _open_received_file(self, parts: _Parts) -> int:
        dir_fd = self._open_dir(parts[:-1])
        try:
            return os.open(parts[-1], _OLD_FILE, dir_fd=dir_fd)
        finally:
            if dir_fd != self.root_fd:
                os.close(dir_fd)

    def _count_content(self, size: int) -> None:
        if self.content + size > self.limits.max_bytes:
            raise TreeError("content exceeds max_bytes")
        self.content += size

    def _discard(self, size: int) -> None:
        left = size
        while left:
            left -= len(self.reader.read_exact(min(left, _CHUNK)))
        self._skip_padding(size)

    def _skip_padding(self, size: int) -> None:
        self.reader.read_exact(-size % BLOCK)

    def _open_dir(self, parts: _Parts) -> int:
        return _open_dir(self.root_fd, parts)

    def open_dirs(self) -> None:
        """Give every received directory except the root mode `0755`."""
        for parts in sorted(self.dirs - {()}):
            fd = self._open_dir(parts)
            try:
                os.fchmod(fd, 0o755)
            finally:
                os.close(fd)


def _open_dir(root_fd: int, parts: _Parts) -> int:
    """Walk directories from `root_fd` by descriptors, never following links.

    Returns `root_fd` itself for no parts, else a new descriptor. A link,
    non-directory or missing component is a `TreeError`.
    """
    fd = root_fd
    try:
        for part in parts:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            if fd != root_fd:
                os.close(fd)
            fd = next_fd
    except BaseException as exc:
        if fd != root_fd:
            os.close(fd)
        if isinstance(exc, OSError):
            raise TreeError(f"cannot open directory {'/'.join(parts)}: {exc}") from exc
        raise
    return fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


# --- removal of root-owned trees -------------------------------------------


def remove_own_tree(parent: Path, name: str) -> None:
    """Remove the root-owned tree `parent/name` durably (protocol row).

    Precondition: a directory owned by root (any mode). Effects: `unlink`
    (`fault_point("unlinked")`) -> `sync_dir(parent)` before returning.
    """
    leaf = check_name(os.fsencode(name))
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _require_root_dir(parent_fd, leaf, parent / leaf)
        shutil.rmtree(leaf, dir_fd=parent_fd)
        fsutil.fault_point("unlinked", parent / leaf)
        fsutil.sync_dir(parent_fd)
    finally:
        os.close(parent_fd)


def remove_empty_dir(parent: Path, name: str) -> None:
    """Remove the empty root-owned directory `parent/name` durably.

    A missing, non-empty or foreign object is a `TreeError`. Effects:
    `unlink` (`fault_point("unlinked")`) -> `sync_dir(parent)`.
    """
    leaf = check_name(os.fsencode(name))
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _require_root_dir(parent_fd, leaf, parent / leaf)
        try:
            os.rmdir(leaf, dir_fd=parent_fd)
        except OSError as exc:
            raise TreeError(f"cannot remove {parent / leaf}: {exc.strerror}") from exc
        fsutil.fault_point("unlinked", parent / leaf)
        fsutil.sync_dir(parent_fd)
    finally:
        os.close(parent_fd)


def _require_root_dir(parent_fd: int, leaf: str, path: Path) -> None:
    try:
        st = os.lstat(leaf, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        raise TreeError(f"missing: {path}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise TreeError(f"not a directory: {path}")
    if st.st_uid != ROOT_UID:
        raise TreeError(f"owner of {path} is uid {st.st_uid}, not root")
