"""Tests for the ACL probe (spec 3.10)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from df.acl import acl_present_fd, acl_present_path


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
