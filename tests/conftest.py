from __future__ import annotations

import importlib.util
import os
import re
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
#: скип по её отсутствию (`importorskip("steward")`, «gate-check CLI недоступен
#: … без группы governance») тогда роняет прогон, а не исчезает молча
#: (devtools#493: 219 тестов раннера не исполнялись с 30.08, потому что CI-шаг
#: перечислял модули руками). Сверяется только ПРИЧИНА скипа, не путь файла
#: (ревью #499). Образец — SELFCHECK_REQUIRE_TOOLS.
_GOVERNANCE_REQUIRED = os.environ.get("GOVERNANCE_REQUIRED") == "1"
_GOVERNANCE_SKIP = re.compile(r"steward|группы governance")
_skipped_for_governance: list[str] = []


def _skip_reason(report: pytest.CollectReport | pytest.TestReport) -> str:
    longrepr = report.longrepr
    return longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)


def _note_governance_skip(report: pytest.CollectReport | pytest.TestReport) -> None:
    if (
        _GOVERNANCE_REQUIRED
        and report.skipped
        and _GOVERNANCE_SKIP.search(_skip_reason(report))
    ):
        _skipped_for_governance.append(report.nodeid)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _note_governance_skip(report)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _note_governance_skip(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _skipped_for_governance:
        names = ", ".join(_skipped_for_governance)
        print(f"\nGOVERNANCE_REQUIRED=1, но группа governance недоступна: {names}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _halt_gate_admits(monkeypatch):
    """Стоп-кран (D2) зовёт GitHub из start/verify/reopen; в тестах он по
    умолчанию «пускает», а кейсы стопа подменяют его сами. Без группы
    governance раннер не импортируется — тогда и подменять нечего."""
    try:
        from governance import runner
    except ImportError:
        return
    monkeypatch.setattr(runner, "_HALT_GATE", lambda slug: None)
