from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check-arch-evidence-freshness.py"


@pytest.fixture(scope="session")
def sensor():
    spec = importlib.util.spec_from_file_location("arch_freshness_sensor", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить модуль сенсора из {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["arch_freshness_sensor"] = mod
    spec.loader.exec_module(mod)
    return mod


#: CI ставит `GOVERNANCE_REQUIRED=1` там, где группа governance синхронизирована:
#: модуль, скипнутый `importorskip("steward")`, тогда роняет прогон, а не
#: исчезает молча (devtools#493: 219 тестов раннера не исполнялись с 30.08,
#: потому что CI-шаг перечислял модули руками). Образец — SELFCHECK_REQUIRE_TOOLS.
_GOVERNANCE_REQUIRED = os.environ.get("GOVERNANCE_REQUIRED") == "1"
_skipped_for_steward: list[str] = []


def _note_steward_skip(report: pytest.CollectReport | pytest.TestReport) -> None:
    if _GOVERNANCE_REQUIRED and report.skipped and "steward" in str(report.longrepr):
        _skipped_for_steward.append(report.nodeid)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _note_steward_skip(report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _note_steward_skip(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _skipped_for_steward:
        names = ", ".join(_skipped_for_steward)
        print(f"\nGOVERNANCE_REQUIRED=1, но steward не импортируется: {names}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
