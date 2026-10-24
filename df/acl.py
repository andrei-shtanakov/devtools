"""ACL presence probe (spec 3.10).

`True` means the object carries an ACL. Any error other than "no ACL" is
raised as `OSError`; the caller treats it as a refusal, never as "no ACL".
"""

from __future__ import annotations

import ctypes
import errno
import functools
import os
import sys
from collections.abc import Callable
from pathlib import Path

_IS_DARWIN = sys.platform == "darwin"
_LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
_ACL_TYPE_EXTENDED = 0x00000100
_LINUX_ACL_XATTRS = frozenset({"system.posix_acl_access", "system.posix_acl_default"})
_ACL_TEXT_HEADER = "!#acl 1"
#: Permissions a tolerated `deny` entry may name: they cannot reduce read or
#: search (spec 3.6 auth observation, Task 11-A ruling round 3).
_DELETE_ONLY = frozenset({"delete", "delete_child"})
#: Flags a tolerated entry may carry besides `deny` (propagation only; an
#: `inherited` entry is not tolerated).
_PROPAGATION_FLAGS = frozenset(
    {"file_inherit", "directory_inherit", "limit_inherit", "only_inherit"}
)


def acl_present_fd(fd: int) -> bool:
    """Whether the object open as `fd` carries an ACL."""
    if _IS_DARWIN:
        return _darwin_probe(lambda lib: lib.acl_get_fd_np(fd, _ACL_TYPE_EXTENDED))
    return _has_linux_acl(os.listxattr(fd))


def acl_present_path(path: Path) -> bool:
    """Whether `path` itself (a symlink is not followed) carries an ACL."""
    os.lstat(path)  # "no such file" must not read as "no ACL"
    if _IS_DARWIN:
        raw = os.fsencode(path)
        return _darwin_probe(lambda lib: lib.acl_get_link_np(raw, _ACL_TYPE_EXTENDED))
    return _has_linux_acl(os.listxattr(path, follow_symlinks=False))


def acl_text_fd(fd: int) -> str | None:
    """macOS: the extended ACL of the object open as `fd` in `acl_to_text`
    form; `None` on any failure, without an ACL, or on another platform.
    Call it after `acl_present_fd` said an ACL is there."""
    if not _IS_DARWIN:
        return None
    lib = _libsystem()
    acl = lib.acl_get_fd_np(fd, _ACL_TYPE_EXTENDED)
    if not acl:
        return None
    try:
        text = lib.acl_to_text(acl, None)
        if not text:
            return None
        try:
            return ctypes.string_at(text).decode("utf-8")
        except UnicodeDecodeError:
            return None
        finally:
            lib.acl_free(text)
    finally:
        lib.acl_free(acl)


def acl_only_denies_delete(text: str) -> bool:
    """Whether a macOS `acl_to_text` ACL only denies deleting: every entry is
    `deny` (propagation flags allowed, `inherited` not) of permissions within
    `delete`, `delete_child`. Anything else, or text that does not parse, is
    `False`."""
    lines = text.splitlines()
    if not lines or lines[0] != _ACL_TEXT_HEADER:
        return False
    return all(_entry_denies_delete(line) for line in lines[1:] if line)


def _entry_denies_delete(line: str) -> bool:
    """`user|group:<uuid>:<name>:<id>:<type>[,flags]:<perms>`."""
    head, sep, rest = line.partition(":")
    parts = rest.rsplit(":", 2)
    if not sep or head not in ("user", "group") or len(parts) != 3:
        return False
    kind, *flags = parts[1].split(",")
    perms = set(parts[2].split(","))
    return kind == "deny" and set(flags) <= _PROPAGATION_FLAGS and perms <= _DELETE_ONLY


def _has_linux_acl(names: list[str]) -> bool:
    return not _LINUX_ACL_XATTRS.isdisjoint(names)


@functools.cache
def _libsystem() -> ctypes.CDLL:
    lib = ctypes.CDLL(_LIBSYSTEM, use_errno=True)
    lib.acl_get_fd_np.restype = ctypes.c_void_p
    lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.acl_get_link_np.restype = ctypes.c_void_p
    lib.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.acl_to_text.restype = ctypes.c_void_p
    lib.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.acl_free.restype = ctypes.c_int
    lib.acl_free.argtypes = [ctypes.c_void_p]
    return lib


def _darwin_probe(call: Callable[[ctypes.CDLL], int | None]) -> bool:
    lib = _libsystem()
    ctypes.set_errno(0)
    acl = call(lib)
    if acl:
        lib.acl_free(acl)
        return True
    err = ctypes.get_errno() or errno.EIO
    if err == errno.ENOENT:
        return False
    raise OSError(err, f"ACL probe failed: {os.strerror(err)}")
