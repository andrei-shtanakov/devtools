"""Gates of the df test run (Task 13-A, rulings R77/R78).

- the root gate: a `root` test runs only as euid 0 with `DF_ROOT_ACCEPTANCE=1`;
- `DF_REQUIRE_PINNED_UV=1` turns the pinned-uv skip into a failed session;
- the `network` marker is out of the default selection (`addopts`).

The last two wiring checks run pytest in a subprocess over real test files
(collection only, or the root tests as the current non-root user).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.df import conftest as df_conftest

REPO = Path(__file__).resolve().parents[2]
GATE_OPEN = {"DF_ROOT_ACCEPTANCE": "1"}


@pytest.mark.parametrize(
    ("euid", "env", "is_open"),
    [
        (0, GATE_OPEN, True),
        (0, {}, False),
        (0, {"DF_ROOT_ACCEPTANCE": "yes"}, False),
        (501, GATE_OPEN, False),
        (501, {}, False),
    ],
)
def test_root_gate_reason(euid: int, env: dict[str, str], is_open: bool) -> None:
    reason = df_conftest.root_gate_reason(euid, env)
    assert (reason is None) is is_open


class _Item:
    """The two methods of `pytest.Item` the gate uses."""

    def __init__(self, *markers: str) -> None:
        self.markers = list(markers)
        self.added: list[pytest.MarkDecorator] = []

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.markers else None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.added.append(marker)


@pytest.mark.parametrize(("euid", "env"), [(0, {}), (501, GATE_OPEN)])
def test_closed_gate_skips_only_root_items(
    monkeypatch: pytest.MonkeyPatch, euid: int, env: dict[str, str]
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    monkeypatch.delenv("DF_ROOT_ACCEPTANCE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    root, plain = _Item("root"), _Item("slow")
    df_conftest.pytest_collection_modifyitems(
        SimpleNamespace(),
        [root, plain],
    )
    assert [m.name for m in root.added] == ["skip"]
    assert "DF_ROOT_ACCEPTANCE" in str(root.added[0].kwargs["reason"])
    assert plain.added == []


def test_open_gate_keeps_root_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("DF_ROOT_ACCEPTANCE", "1")
    root = _Item("root")
    df_conftest.pytest_collection_modifyitems(
        SimpleNamespace(),
        [root],
    )
    assert root.added == []


def _skip_report(reason: str) -> pytest.TestReport:
    return pytest.TestReport(
        nodeid="tests/df/test_admin.py::t",
        location=("tests/df/test_admin.py", 1, "t"),
        keywords={},
        outcome="skipped",
        longrepr=("tests/df/test_admin.py", 1, f"Skipped: {reason}"),
        when="call",
    )


@pytest.mark.parametrize(
    ("required", "reason", "fails"),
    [
        ("1", "uv не найден", True),
        ("1", "uv ['0.9.0'] — не закреплённая 0.11.23", True),
        ("1", "сеть недоступна", False),
        (None, "uv не найден", False),
        ("0", "uv не найден", False),
    ],
)
def test_pinned_uv_skip_fails_session_only_when_required(
    monkeypatch: pytest.MonkeyPatch, required: str | None, reason: str, fails: bool
) -> None:
    monkeypatch.setattr(df_conftest, "_skipped_for_uv", [])
    if required is None:
        monkeypatch.delenv("DF_REQUIRE_PINNED_UV", raising=False)
    else:
        monkeypatch.setenv("DF_REQUIRE_PINNED_UV", required)
    df_conftest.pytest_runtest_logreport(_skip_report(reason))
    session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
    df_conftest.pytest_sessionfinish(session, 0)
    want = pytest.ExitCode.TESTS_FAILED if fails else pytest.ExitCode.OK
    assert session.exitstatus == want


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DF_ROOT_ACCEPTANCE", "PYTEST_ADDOPTS"}
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_network_marker_out_of_default_selection() -> None:
    """R77: `addopts` deselects `network`; `-m network` selects the converted
    relocation test (formerly opt-in through `DF_NETWORK_TESTS`)."""
    target = "tests/df/test_admin.py"
    default = _pytest("--collect-only", "-q", target)
    assert default.returncode == 0, default.stdout + default.stderr
    assert "test_admin_python_relocation_precondition" not in default.stdout
    chosen = _pytest("--collect-only", "-q", "-m", "network", target)
    assert chosen.returncode == 0, chosen.stdout + chosen.stderr
    assert "test_admin_python_relocation_precondition" in chosen.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="проверка закрытого шлюза — не от root")
def test_root_tests_skip_cleanly_as_non_root() -> None:
    """R77: as a non-root user the root run selects the root tests and skips
    every one by the gate; nothing fails."""
    done = _pytest("-q", "-rs", "-m", "root", "tests/df/test_platform_root.py")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert " passed" not in out
    assert "skipped" in out
    assert "DF_ROOT_ACCEPTANCE" in out
