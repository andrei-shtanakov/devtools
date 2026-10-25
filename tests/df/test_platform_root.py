"""Root tests of the platform primitives (Task 13, ruling R73).

Run only in the disposable CI runners of `.github/workflows/df-stage1.yml`
(`-m root`, euid 0, `DF_ROOT_ACCEPTANCE=1`); everywhere else the root gate of
`tests/df/conftest.py` skips them. 13-A holds the probe part: the gate canary
and the launch-mechanism binaries the production paths name. The account,
`sudo -l`, ACL and loader tests come with 13-C.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from df import platform as plat
from tests.df.conftest import ROOT_ACCEPTANCE_ENV

pytestmark = pytest.mark.root

#: The absolute paths `run_as`/`spawn_as`/account creation execute, per OS.
MECHANISM_BINARIES: dict[plat.PlatformName, tuple[str, ...]] = {
    "macos-arm64": (plat._SUDO, plat._SANDBOX_EXEC, plat._ENV, plat._DSCL, plat._OTOOL),
    "linux-x86_64": (
        plat._UNSHARE,
        plat._SETPRIV,
        plat._ENV,
        plat._GETENT,
        plat._GROUPADD,
        plat._USERADD,
        plat._READELF,
    ),
}


def test_root_gate_open() -> None:
    """Canary: the root run really runs root tests (none of them is skipped
    by a gate that never opened)."""
    assert os.geteuid() == 0
    assert os.environ.get(ROOT_ACCEPTANCE_ENV) == "1"


def test_mechanism_binaries_present() -> None:
    """Every launch binary the production code names is a regular file."""
    platform = plat.detect_platform()
    missing = [p for p in MECHANISM_BINARIES[platform] if not Path(p).is_file()]
    assert missing == []
