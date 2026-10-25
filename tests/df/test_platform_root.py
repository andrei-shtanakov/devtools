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
from typing import Any

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

#: Which control each broken `run_as` defeats (M5): removing any one check
#: of `check_controls` makes one of these pass silently.
BROKEN_CONTROLS = (
    "always-1",
    "whoami-fails",
    "whoami-mismatch",
    "write-fails",
    "write-as-root",
    "network-code",
    "network-not-launched",
)


@pytest.mark.parametrize("broken", BROKEN_CONTROLS)
def test_broken_run_as_fails_suite(
    root_world: RootWorld, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    """A broken `run_as` fails the controls as infrastructure, never as a
    PASS of a negative test (spec 3.8); each control is exercised."""
    root_world.prepare_build_user()
    real = plat.run_as

    def fake(user: str, argv: list[str], **kw: Any) -> plat.RunResult:
        tool = argv[0]
        if broken == "always-1":
            return plat.RunResult(1, b"", b"", True)
        if tool == "/usr/bin/id" and broken == "whoami-fails":
            return plat.RunResult(1, b"", b"", True)
        if tool == "/usr/bin/id" and broken == "whoami-mismatch":
            return plat.RunResult(0, b"123456\n", b"", True)
        if tool == "/usr/bin/touch" and broken == "write-fails":
            real(user, argv, **kw)  # the file is there: only the code tells
            return plat.RunResult(1, b"", b"", True)
        if tool == "/usr/bin/touch" and broken == "write-as-root":
            subprocess.run(argv, check=True)  # root's file: only the owner tells
            return plat.RunResult(0, b"", b"", True)
        if tool == "/usr/bin/curl" and broken == "network-code":
            return plat.RunResult(0, b"000", b"", True)
        if tool == "/usr/bin/curl" and broken == "network-not-launched":
            return plat.RunResult(0, b"200", b"", False)
        return real(user, argv, **kw)

    monkeypatch.setattr(plat, "run_as", fake)
    with pytest.raises(pytest.fail.Exception, match=INFRA):
        check_controls(root_world)


#: Twins of the observer (S1-R5-N4, S1-R8-N2 and the form table of the 13-B
#: report): each holds one row on both OSes.
TWINS = (
    "root-child-cat",
    "root-ctypes-open",
    "prepare-dir-reopen",
    "rename-refused",
    "symlink-out",
    "hardlink-out",
    "held-fd-readdir",
    "held-fd-read",
    "moved-out",
    "exec-source",
)
#: Twin -> the limit key that would say it is not reported
#: (`observe.LIMITS`); a key no platform states means "always reported".
TWIN_LIMIT = {
    "symlink-out": "symlink-out",
    "hardlink-out": "hardlink-out",
    "held-fd-readdir": "held-fd-getdents",
    "held-fd-read": "held-fd-read",
    "moved-out": "moved-out",
    "exec-source": "exec-source",
}
#: The source every form twin uses: a file, and a script root may execute.
TWIN_SOURCE = (
    "echo sentinel > f && printf '#!/bin/sh\\nexit 0\\n' > tool && chmod 755 tool"
)


@pytest.mark.parametrize("twin", TWINS)
def test_root_access_observer_twins(
    root_world: RootWorld, controls: Controls, twin: str
) -> None:
    """S1-R5-N4: a root child `/bin/cat` reading a source file and a root
    process opening it through libc (both missed by Python's audit hook) are
    violations on both platforms. S1-R8-N2: the real `prepare_dir`, observed
    from its `fchown` on, has none; one reopening `releases/<id>` by path
    after it has one. The form-table twins: a source cannot be renamed away
    by `df-build`; a followed link out, a held descriptor listed or read are
    reported exactly when the platform states no limit for them; a hardlink
    out, a moved object and an executed source script are reported (R98:
    by identity)."""
    if twin == "prepare-dir-reopen":
        _prepare_dir_twin(root_world)
        return
    src = root_world.builder_dir(root_world.layout.root / "twins" / "src", TWIN_SOURCE)
    TWIN_RUNS[twin](root_world, src)


def _twin_cat(world: RootWorld, src: Path) -> None:
    with _observing(world, [src], "root-child-cat") as obs:
        subprocess.run(["/bin/cat", str(src / "f")], capture_output=True, check=True)
    _require_observed(obs)
    assert [a for a in obs.violations if a.path == str(src / "f")], obs.violations


def _twin_ctypes(world: RootWorld, src: Path) -> None:
    with _observing(world, [src], "root-ctypes-open") as obs:
        _ctypes_open(src / "f")
    _require_observed(obs)
    assert [a for a in obs.violations if a.path == str(src / "f")], obs.violations


def _twin_rename(world: RootWorld, src: Path) -> None:
    """F3: the parent is root's, so `df-build` cannot move the source."""
    with _observing(world, [src], "rename-refused") as obs:
        moved = plat.run_as(
            plat.BUILD_USER,
            ["/bin/mv", str(src), f"{src}-moved"],
            env=ROLE_ENV,
            cwd=Path("/"),
            network=False,
            timeout=30,
        )
    _require_observed(obs)
    assert moved.launched and moved.returncode != 0, moved
    assert src.is_dir() and not Path(f"{src}-moved").exists()
    assert obs.violations == []


def _twin_symlink(world: RootWorld, src: Path) -> None:
    """F5: root follows `src/link` to a file outside every source."""
    outside = world.layout.root / "twins" / "outside"
    outside.mkdir(mode=0o755)
    (outside / "f").write_text("outside\n")
    _as_builder(["/bin/ln", "-s", str(outside / "f"), str(src / "link")])
    with _observing(world, [src], "symlink-out") as obs:
        os.close(os.open(src / "link", os.O_RDONLY))
    _expect_per_limits(obs, "symlink-out")


def _twin_hardlink(world: RootWorld, src: Path) -> None:
    """F6: root opens a df-build hardlink (in its home) to `src/f`: the same
    object, reported by identity (R98)."""
    link = world.layout.build_home / "hardlink"
    _as_builder(["/bin/ln", str(src / "f"), str(link)])
    with _observing(world, [src], "hardlink-out") as obs:
        os.close(os.open(link, os.O_RDONLY))
    _expect_per_limits(obs, "hardlink-out")


def _twin_moved(world: RootWorld, src: Path) -> None:
    """F4: df-build moves `src/f` (present at entry) out of the source during
    the observation; root opens it at its new place."""
    moved = world.layout.build_home / "moved"
    with _observing(world, [src], "moved-out") as obs:
        _as_builder(["/bin/mv", str(src / "f"), str(moved)])
        os.close(os.open(moved, os.O_RDONLY))
    _expect_per_limits(obs, "moved-out")


def _twin_exec(world: RootWorld, src: Path) -> None:
    """N6: root executes a df-build script of the source."""
    with _observing(world, [src], "exec-source") as obs:
        subprocess.run([str(src / "tool")], capture_output=True, check=True)
    _expect_per_limits(obs, "exec-source")
    assert [a for a in obs.violations if a.path == str(src / "tool")], obs.violations


def _twin_held_readdir(world: RootWorld, src: Path) -> None:
    """F9: a directory descriptor opened before the observation is listed."""
    fd = os.open(src, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with _observing(world, [src], "held-fd-readdir") as obs:
            os.listdir(fd)
    finally:
        os.close(fd)
    _expect_per_limits(obs, "held-fd-readdir")


def _twin_held_read(world: RootWorld, src: Path) -> None:
    """F10: a file descriptor opened before the observation is read."""
    fd = os.open(src / "f", os.O_RDONLY)
    try:
        with _observing(world, [src], "held-fd-read") as obs:
            assert os.read(fd, 64) == b"sentinel\n"
    finally:
        os.close(fd)
    _expect_per_limits(obs, "held-fd-read")


TWIN_RUNS: dict[str, Callable[[RootWorld, Path], None]] = {
    "root-child-cat": _twin_cat,
    "root-ctypes-open": _twin_ctypes,
    "rename-refused": _twin_rename,
    "symlink-out": _twin_symlink,
    "hardlink-out": _twin_hardlink,
    "held-fd-readdir": _twin_held_readdir,
    "held-fd-read": _twin_held_read,
    "moved-out": _twin_moved,
    "exec-source": _twin_exec,
}


def _expect_per_limits(obs: Observation, twin: str) -> None:
    """The twin's access is reported iff no stated limit covers it here."""
    _require_observed(obs)
    limited = TWIN_LIMIT[twin] in obs.limits
    assert bool(obs.violations) is not limited, (twin, obs.violations, obs.limits)


def _as_builder(argv: list[str]) -> None:
    result = plat.run_as(
        plat.BUILD_USER, argv, env=ROLE_ENV, cwd=Path("/"), network=False, timeout=30
    )
    assert result.launched and result.returncode == 0, result


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
        sources,
        control=world.control_dir(),
        workdir=world.workdir(),
        scope=world.layout.root,
        record=record,
    )


def _require_observed(obs: Observation) -> None:
    if obs.status == "unobserved":
        pytest.xfail(f"{UNOBSERVED}: {obs.reason}")


def _ctypes_open(path: Path) -> None:
    """open(2)/close(2) through libc: invisible to Python's audit hook."""
    libc = ctypes.CDLL(_LIBC, use_errno=True)
    libc.open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.open.restype = ctypes.c_int
    fd = libc.open(bytes(path), os.O_RDONLY)
    assert fd >= 0, ctypes.get_errno()
    libc.close(fd)
