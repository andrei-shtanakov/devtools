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


def _has_linux_acl(names: list[str]) -> bool:
    return not _LINUX_ACL_XATTRS.isdisjoint(names)


@functools.cache
def _libsystem() -> ctypes.CDLL:
    lib = ctypes.CDLL(_LIBSYSTEM, use_errno=True)
    lib.acl_get_fd_np.restype = ctypes.c_void_p
    lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.acl_get_link_np.restype = ctypes.c_void_p
    lib.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
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
