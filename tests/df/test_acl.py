"""Tests for the ACL probe (spec 3.10)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from df.acl import (
    acl_only_denies_delete,
    acl_present_fd,
    acl_present_path,
    acl_text_fd,
)


def _add_acl(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", path], check=True)
        return
    setfacl = shutil.which("setfacl")
    if setfacl is None:
        pytest.skip("setfacl is not installed; cannot create a POSIX ACL")
    result = subprocess.run(
        [setfacl, "-m", "u:nobody:rw", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"filesystem refuses POSIX ACLs: {result.stderr.strip()}")


def test_acl_absent_on_fresh_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"")
    assert not acl_present_path(tmp_path / "f")
    fd = os.open(tmp_path / "f", os.O_RDONLY)
    try:
        assert not acl_present_fd(fd)
    finally:
        os.close(fd)


def test_acl_detected(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"")
    _add_acl(target)
    assert acl_present_path(target)
    fd = os.open(target, os.O_RDONLY)
    try:
        assert acl_present_fd(fd)
    finally:
        os.close(fd)


def test_acl_path_does_not_follow_symlink(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"")
    _add_acl(target)
    os.symlink(target, tmp_path / "l")
    assert not acl_present_path(tmp_path / "l")


def test_acl_probe_error_is_raised(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        acl_present_path(tmp_path / "missing")
    with pytest.raises(OSError):
        acl_present_fd(9999)


# ---------- Task 11-A round 3: delete-only deny ACLs ----------

_G = "group:ABCDEFAB-CDEF-ABCD-EFAB-CDEF0000000C:everyone:12"
_U = "user:11111111-2222-3333-4444-555555555555:someone:501"


@pytest.mark.parametrize(
    "text,expected",
    [
        (f"!#acl 1\n{_G}:deny:delete\n", True),
        (f"!#acl 1\n{_G}:deny:delete,delete_child\n", True),
        (
            f"!#acl 1\n{_G}:deny,file_inherit,directory_inherit:delete,delete_child\n",
            True,
        ),
        ("!#acl 1\n", True),
        (f"!#acl 1\n{_G}:deny:delete\n{_G}:allow:read\n", False),
        (f"!#acl 1\n{_U}:deny:read\n", False),
        (f"!#acl 1\n{_G}:deny:delete,read\n", False),
        (f"!#acl 1\n{_G}:deny:delete,search\n", False),
        (f"!#acl 1\n{_G}:deny:readattr\n", False),
        (f"!#acl 1\n{_G}:deny:readsecurity\n", False),
        (f"!#acl 1\n{_G}:deny,inherited:delete\n", False),
        (f"!#acl 1\n{_G}:deny,bogus_flag:delete\n", False),
        (f"!#acl 1\n{_G}:deny:\n", False),
        (f"!#acl 1\nother:{_G[6:]}:deny:delete\n", False),
        (f"{_G}:deny:delete\n", False),
        ("garbage", False),
        ("", False),
    ],
)
def test_acl_only_denies_delete_parser(text: str, expected: bool) -> None:
    assert acl_only_denies_delete(text) is expected


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACL text")
def test_acl_text_fd_reads_real_acl(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"")
    subprocess.run(
        ["/bin/chmod", "+a", "group:everyone deny delete", target], check=True
    )
    fd = os.open(target, os.O_RDONLY)
    try:
        text = acl_text_fd(fd)
    finally:
        os.close(fd)
        subprocess.run(["/bin/chmod", "-N", target], check=True)
    assert text is not None and text.startswith("!#acl 1\n")
    assert acl_only_denies_delete(text)


def test_acl_text_fd_none_without_acl(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"")
    fd = os.open(tmp_path / "f", os.O_RDONLY)
    try:
        assert acl_text_fd(fd) is None
    finally:
        os.close(fd)
