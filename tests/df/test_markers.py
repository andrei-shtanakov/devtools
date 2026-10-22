"""Tests for the restricted marker grammar of spec 3.5 (Task 7a)."""

from __future__ import annotations

import pytest

from df.markers import evaluate, marker_env
from df.sources import UnsupportedLock

MACOS_312 = marker_env("macos-arm64", "3.12.13")
LINUX_312 = marker_env("linux-x86_64", "3.12.13")


def test_marker_env_has_the_eight_variables() -> None:
    assert dict(MACOS_312) == {
        "sys_platform": "darwin",
        "platform_system": "Darwin",
        "platform_machine": "arm64",
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "python_version": "3.12",
        "python_full_version": "3.12.13",
    }
    assert (
        LINUX_312["sys_platform"],
        LINUX_312["platform_system"],
        LINUX_312["platform_machine"],
    ) == ("linux", "Linux", "x86_64")


@pytest.mark.parametrize(
    "marker,expected",
    [
        ("sys_platform == 'darwin'", True),
        ("python_full_version >= '3.12' and python_full_version < '3.14'", True),
        ("(sys_platform == 'win32') or (platform_machine == 'arm64')", True),
        ("implementation_name != 'cpython'", False),
    ],
)
def test_evaluate_macos_312(marker: str, expected: bool) -> None:
    assert evaluate(marker, MACOS_312) is expected


@pytest.mark.parametrize(
    "marker,expected",
    [
        # `and` binds tighter than `or`
        ("sys_platform == 'win32' and os_name == 'nt' or os_name == 'posix'", True),
        ("sys_platform == 'win32' and (os_name == 'nt' or os_name == 'posix')", False),
        ('sys_platform == "darwin"', True),
        # reversed form
        ("'darwin' == sys_platform", True),
        ("'3.13' > python_version", True),
        ("'3.12' < python_version", False),
        # zero padding (PEP 440 release semantics)
        ("python_version == '3.12.0'", True),
        ("python_full_version == '3.12.13'", True),
        ("python_full_version != '3.12.13'", False),
        ("python_full_version == '3.12'", False),
        ("python_full_version > '3.12'", True),
        ("python_full_version <= '3.12.13'", True),
        ("python_version >= '3.9'", True),
        ("python_version < '3.10'", False),
        ("((sys_platform == 'darwin'))", True),
    ],
)
def test_evaluate_operators_and_versions(marker: str, expected: bool) -> None:
    assert evaluate(marker, MACOS_312) is expected


def test_evaluate_linux() -> None:
    assert evaluate("sys_platform == 'linux' and platform_system == 'Linux'", LINUX_312)
    assert not evaluate("platform_machine == 'arm64'", LINUX_312)


@pytest.mark.parametrize(
    "marker",
    [
        "extra == 'dev'",
        "platform_release >= '20'",
        "sys_platform in 'linux darwin'",
        "python_version ~= '3.12'",
        "sys_platform < 'z'",
        # F6: other constructs
        "group == 'dev'",
        "python_version === '3.12'",
        "sys_platform not in 'linux'",
        "not sys_platform == 'linux'",
        "python_version == '3.12.*'",
        "python_full_version >= '3.13.0rc1'",
        "'a' == 'a'",
        "sys_platform == os_name",
        "sys_platform >= 'darwin'",
        # extra tokens and broken syntax
        "sys_platform == 'darwin' sys_platform",
        "sys_platform == 'darwin')",
        "(sys_platform == 'darwin'",
        "sys_platform == 'darwin' and",
        "sys_platform = 'darwin'",
        "sys_platform == 'darwin",
        "",
        # F5: no short-circuit acceptance of an unsupported tail
        "sys_platform == 'darwin' or extra == 'x'",
        "sys_platform == 'win32' and extra == 'x'",
    ],
)
def test_unsupported_marker(marker: str) -> None:
    with pytest.raises(UnsupportedLock) as exc:
        evaluate(marker, MACOS_312)
    assert repr(marker) in str(exc.value)
