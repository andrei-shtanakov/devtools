"""Порядок аргументов командной строки не должен прятать фикстуры conftest.

Регрессия `df-tests-world-fixture-order`: `tests/df/a.py tests/b.py tests/df/c.py`
в pytest 9 терял фикстуры `tests/df/conftest.py` у `c.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _arg_path

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ["tests/df/a.py", "tests/b.py", "tests/df/c.py"],
            ["tests/b.py", "tests/df/a.py", "tests/df/c.py"],
        ),
        (
            ["tests/df/a.py::t2", "tests/df/a.py::t1", "tests/b.py"],
            ["tests/b.py", "tests/df/a.py::t2", "tests/df/a.py::t1"],
        ),
    ],
)
def test_arg_path_groups_directories(args: list[str], expected: list[str]) -> None:
    assert sorted(args, key=_arg_path) == expected


def test_interleaved_directories_keep_conftest_fixtures() -> None:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/df/test_publisher_merge.py",
        "tests/test_act_admission.py",
        "tests/df/test_publisher_main.py",
        "-k",
        (
            "test_patterns_path_is_in_the_release_tree"
            " or test_before_first_mode_entry_is_legacy"
            " or test_check_run_returns_the_record"
        ),
    ]
    done = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=300, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 passed" in done.stdout
