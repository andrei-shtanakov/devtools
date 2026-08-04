from __future__ import annotations

import importlib.util
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
