"""Root tests of the platform primitives (Task 13, ruling R73).

Run only in the disposable CI runners of `.github/workflows/df-stage1.yml`
(`-m root`, euid 0, `DF_ROOT_ACCEPTANCE=1`); everywhere else the root gate of
`tests/df/conftest.py` skips them. 13-A holds the probe part: the gate canary
and the launch-mechanism binaries the production paths name. The account,
`sudo -l`, ACL and loader tests come with 13-C. 13-B adds the positive
controls' failure mode and the OS observer's twins (`tests/df/observe.py`).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from df import fsutil
from df import platform as plat
from df.leaves import prepare_dir
from tests.df.conftest import ROOT_ACCEPTANCE_ENV, stage1_out
from tests.df.observe import Observation, observe_root_access
from tests.df.root_world import (
    INFRA,
    ROLE_ENV,
    Controls,
    RootWorld,
    check_controls,
)

pytestmark = pytest.mark.root

#: The xfail of an unavailable observer (Task 13 Step 2): not a PASS.
UNOBSERVED = "наблюдатель недоступен: гарантия §3.3 не подтверждена"
_LIBC = "/usr/lib/libSystem.B.dylib" if sys.platform == "darwin" else None

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


# ---------- 13-B: the positive controls and the observer ----------


def test_broken_run_as_fails_suite(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `run_as` that always returns 1 fails the controls as infrastructure,
    never as a PASS of a negative test (spec 3.8)."""
    root_world.prepare_build_user()
    broken = plat.RunResult(1, b"", b"", True)
    monkeypatch.setattr(plat, "run_as", lambda *_a, **_k: broken)
    with pytest.raises(pytest.fail.Exception, match=INFRA):
        check_controls(root_world)


@pytest.mark.parametrize(
    "twin", ["root-child-cat", "root-ctypes-open", "prepare-dir-reopen"]
)
def test_root_access_observer_twins(
    root_world: RootWorld, controls: Controls, twin: str
) -> None:
    """S1-R5-N4: a root child `/bin/cat` reading a source file and a root
    process opening it through libc (both missed by Python's audit hook) are
    violations on both platforms. S1-R8-N2: the real `prepare_dir`, observed
    from its `fchown` on, has none; one reopening `releases/<id>` by path
    after it has one."""
    if twin == "prepare-dir-reopen":
        _prepare_dir_twin(root_world)
        return
    src = _builder_tree(root_world)
    target = src / "f"
    with _observing(root_world, [src], twin) as obs:
        if twin == "root-child-cat":
            subprocess.run(["/bin/cat", str(target)], capture_output=True, check=True)
        else:
            _ctypes_open(target)
    _require_observed(obs)
    assert [a for a in obs.violations if a.path == str(target)], obs.violations


def _prepare_dir_twin(world: RootWorld) -> None:
    lay = world.layout
    outcomes: dict[str, Observation] = {}
    for rid, reopen in (("twin-clean", False), ("twin-reopen", True)):
        with _observing(world, [], f"prepare-dir-{rid}") as obs:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(fsutil, "fault_point", _watch_at_chown(obs))
                prepare_dir(lay, rid)
            if reopen:
                fd = os.open(lay.release_dir(rid), os.O_RDONLY | os.O_DIRECTORY)
                os.close(fd)
        outcomes[rid] = obs
    for obs in outcomes.values():
        _require_observed(obs)
    assert outcomes["twin-clean"].violations == []
    reopened = str(lay.release_dir("twin-reopen"))
    hits = [a for a in outcomes["twin-reopen"].violations if a.path == reopened]
    assert hits, outcomes["twin-reopen"].violations


def _watch_at_chown(obs: Observation) -> Callable[[str, Path], None]:
    def hook(kind: str, path: Path) -> None:
        if kind == "chown":
            obs.watch(path)

    return hook


def _observing(
    world: RootWorld, sources: list[Path], label: str
) -> AbstractContextManager[Observation]:
    out = stage1_out()
    record = None if out is None else out / "observe" / f"{label}.json"
    return observe_root_access(
        sources, control=world.control_dir(), workdir=world.workdir(), record=record
    )


def _require_observed(obs: Observation) -> None:
    if obs.status == "unobserved":
        pytest.xfail(f"{UNOBSERVED}: {obs.reason}")


def _builder_tree(world: RootWorld) -> Path:
    """`build_home/twin-src/f`, made by `df-build` (the source)."""
    home = world.layout.build_home
    result = plat.run_as(
        plat.BUILD_USER,
        ["/bin/sh", "-c", "mkdir twin-src && echo sentinel > twin-src/f"],
        env=ROLE_ENV,
        cwd=home,
        network=False,
        timeout=30,
    )
    assert result.launched and result.returncode == 0, result
    return home / "twin-src"


def _ctypes_open(path: Path) -> None:
    """open(2)/close(2) through libc: invisible to Python's audit hook."""
    libc = ctypes.CDLL(_LIBC, use_errno=True)
    libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.open.restype = ctypes.c_int
    fd = libc.open(bytes(path), os.O_RDONLY)
    assert fd >= 0, ctypes.get_errno()
    libc.close(fd)
