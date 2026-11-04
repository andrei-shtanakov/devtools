"""Порядок аргументов командной строки не должен прятать фикстуры conftest.

Регрессия `df-tests-world-fixture-order`: `tests/df/a.py tests/b.py tests/df/c.py`
в pytest 9 терял фикстуры `tests/df/conftest.py` у `c.py`."""

from __future__ import annotations

import os
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


def test_arg_path_mixes_absolute_and_relative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    args = [
        "tests/df/a.py",
        str(ROOT / "tests" / "b.py"),
        str(ROOT / "tests" / "df" / "c.py::t"),
    ]
    expected = [args[1], args[0], args[2]]
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
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DF_ROOT_ACCEPTANCE", "PYTEST_ADDOPTS"}
    }
    done = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    out = done.stdout + done.stderr
    assert "fixture 'world' not found" not in out, out
    assert " error" not in out and " failed" not in out, out
    assert done.returncode == 0, out
