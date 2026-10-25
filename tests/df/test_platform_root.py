"""Root tests of the platform primitives (Task 13, ruling R73).

Run only in the disposable CI runners of `.github/workflows/df-stage1.yml`
(`-m root`, euid 0, `DF_ROOT_ACCEPTANCE=1`); everywhere else the root gate of
`tests/df/conftest.py` skips them. 13-A holds the probe part: the gate canary
and the launch-mechanism binaries the production paths name. 13-B adds the
positive controls' failure mode and the OS observer's twins
(`tests/df/observe.py`). 13-C adds the real `sudo -l` capture (R74), the
untrusted approved CLI, setgid parents on Linux, the Linux loader residuals
(R75) and the ACL carries C31/C32 (R86); the account group is in
`tests/df/test_acceptance_root_account.py`.
"""

from __future__ import annotations

import ctypes
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest

from df import acl, cli, fsutil, install, probe
from df import platform as plat
from df.journal import read_journal
from df.leaves import prepare_dir
from tests.df.conftest import ROOT_ACCEPTANCE_ENV, stage1_out
from tests.df.observe import Observation, observe_root_access
from tests.df.root_world import (
    INFRA,
    ROLE_ENV,
    Controls,
    RootWorld,
    assert_committed,
    check_controls,
    infra,
    run,
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


# ---------- 13-C: sudo -l, approved CLI, setgid, loader, ACL ----------

PLATFORM = plat.detect_platform()
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="только Linux")
MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="только macOS")


def _evidence(relative: str, text: str) -> None:
    """Keep a measured fact in the CI evidence directory (none locally)."""
    out = stage1_out()
    if out is None:
        return
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_sudo_rules_real(root_world: RootWorld, controls: Controls) -> None:
    """R74 (13-A review M4): the real `LC_ALL=C sudo -n -l -U df-build` after
    `create_build_user` parses as "none"; the text is kept as evidence (the
    `fixtures/sudo-l` replacement of 13-F)."""
    argv = [plat._SUDO, "-n", "-l", "-U", plat.BUILD_USER]
    result = plat.run_command(argv)
    _evidence(
        f"sudo-l/{PLATFORM}-none.txt",
        f"$ LC_ALL=C {' '.join(argv)}\nrc={result.returncode}\n"
        f"--- stdout\n{result.stdout.decode('utf-8', 'replace')}"
        f"--- stderr\n{result.stderr.decode('utf-8', 'replace')}",
    )
    assert result.launched and result.returncode == 0, result
    rules = plat.parse_sudo_rules(plat.BUILD_USER, result.returncode, result.stdout)
    assert rules == "none", result.stdout
    assert plat.sudo_rules(plat.BUILD_USER) == "none"


#: Case -> the problem `approved_cli_problems` must name (format of `_component`).
UNTRUSTED_CLI = {
    "builder-owned-parent": "владелец uid",
    "intermediate-link": "владелец uid",
    "acl-write": "есть ACL",
}


@pytest.mark.parametrize("case", list(UNTRUSTED_CLI))
def test_untrusted_cli_refused_before_exec(
    root_world: RootWorld,
    controls: Controls,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """An approved `uv` in a directory of `df-build`, behind a link of
    `df-build`, or in a directory whose ACL lets `df-build` write: `bootstrap
    init` refuses at `trusted-paths` and the program never runs (it would
    leave a marker). Twin: the same script in a root directory passes the
    policy, and run, it leaves the marker."""
    lay = root_world.layout
    marks = root_world.top / f"marks-{lay.root.name}"
    marks.mkdir()
    os.chmod(marks, 0o1777)
    marker = marks / "uv-ran"
    script = f'#!/bin/sh\n/usr/bin/touch {marker}\nexec {root_world.uv} "$@"\n'
    trusted = _cli_dir(lay.root / "cli-ok", script)
    assert plat.approved_cli_problems({"uv": trusted}, PLATFORM) == []
    ran = subprocess.run([str(trusted), "--version"], capture_output=True, check=False)
    assert ran.returncode == 0 and marker.exists(), ran  # the marker works
    marker.unlink()
    uv, culprit = _plant_cli(root_world, controls.account.uid, case, script)
    problems = plat.approved_cli_problems({"uv": uv}, PLATFORM)
    want = f"{culprit}: {UNTRUSTED_CLI[case]}"
    assert any(p.startswith(f"uv: {want}") for p in problems), problems
    launches: list[str] = []
    real = plat.role_argv
    monkeypatch.setattr(
        plat,
        "role_argv",
        lambda platform, user, argv, **kw: (
            launches.append(argv[0]) or real(platform, user, argv, **kw)
        ),
    )
    argv = [f"uv={uv}" if a.startswith("uv=") else a for a in root_world.init_argv()]
    assert root_world.bootstrap(argv) == cli.EXIT_REFUSED
    failed = [r for r in read_journal(lay.journal) if r["event"] == "failed"]
    assert [r["step"] for r in failed] == ["trusted-paths"], failed
    assert want in str(failed[0]["detail"]), failed
    assert not marker.exists()
    assert launches == []


def _cli_dir(directory: Path, script: str) -> Path:
    """`directory/uv`, root `0755` in a root `0755` directory."""
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    uv = directory / "uv"
    uv.write_text(script)
    os.chmod(uv, 0o755)
    return uv


def _plant_cli(world: RootWorld, uid: int, case: str, script: str) -> tuple[Path, Path]:
    """The untrusted `uv` of `case` and the path its problem names."""
    lay = world.layout
    if case == "builder-owned-parent":
        uv = _cli_dir(lay.root / "cli-builder", script)
        os.chown(uv.parent, uid, -1)
        return uv, uv.parent
    if case == "intermediate-link":
        real = _cli_dir(lay.root / "cli-real", script)
        link = lay.root / "cli-link"
        link.symlink_to(real.parent)
        os.lchown(link, uid, -1)
        return link / "uv", link
    uv = _cli_dir(lay.root / "cli-acl", script)
    _grant_write(uv.parent)
    assert acl.acl_present_path(uv.parent)
    return uv, uv.parent


def _grant_write(directory: Path) -> None:
    """An ACL entry letting `df-build` create and delete in `directory`."""
    if sys.platform == "darwin":
        entry = f"user:{plat.BUILD_USER} allow add_file,add_subdirectory,delete_child"
        run(["/bin/chmod", "+a", entry, str(directory)])
        return
    run([_setfacl(), "-m", f"u:{plat.BUILD_USER}:rwx", str(directory)])


def _setfacl() -> str:
    found = shutil.which("setfacl", path="/usr/bin:/bin")
    if found is None:
        infra("setfacl не найден (пакет acl)")
    assert found is not None
    return found


@LINUX_ONLY
def test_step2_and_lock_under_setgid_parents_real_linux(root_world: RootWorld) -> None:
    """9-C I5, RC2-C1 on a real Linux file system: `<R>/opt` and
    `<R>/var/lib` are `2755` (R84), so every directory made in them is born
    `S_ISGID`; `bootstrap init` commits, every directory of step 2 and of
    the lock chain is exactly at its mode, the operator's are untouched."""
    lay = root_world.layout
    var = lay.root / "var"
    var.mkdir(mode=0o755)
    os.chmod(var, 0o755)
    theirs = (lay.root / "opt", lay.var.parent)
    for top in theirs:
        top.mkdir(mode=0o755)
        os.chmod(top, 0o2755)
    probe_dir = theirs[0] / "probe"
    probe_dir.mkdir()
    born_setgid = bool(os.stat(probe_dir).st_mode & stat.S_ISGID)
    probe_dir.rmdir()
    assert born_setgid, "ядро не наследует S_ISGID: тест ничего не проверяет"
    assert root_world.bootstrap(root_world.init_argv()) == cli.EXIT_OK
    assert_committed(root_world.layout)
    final = dict(install.prep_dirs(lay))
    final |= {lay.root / "etc": 0o755, lay.install_dir: 0o755, lay.lock: 0o600}
    wrong = {
        str(path): f"{stat.S_IMODE(os.lstat(path).st_mode):04o} != {mode:04o}"
        for path, mode in final.items()
        if stat.S_IMODE(os.lstat(path).st_mode) != mode
    }
    assert wrong == {}
    assert [stat.S_IMODE(os.stat(p).st_mode) for p in theirs] == [0o2755, 0o2755]
    assert stat.S_IMODE(os.stat(var).st_mode) == 0o755


_LD_INIT = re.compile(r"^\s*\d+:\s+calling init: (\S+)$", re.MULTILINE)
_LDCONFIG_ENTRY = re.compile(r"=> (/\S+)$", re.MULTILINE)
#: Loader subdirectories outside the model of `approved_cli_problems` (R26).
_LOADER_SUBDIRS = ("glibc-hwcaps", "tls", "x86_64", "haswell")


@LINUX_ONLY
def test_linux_loader_residuals(root_world: RootWorld, controls: Controls) -> None:
    """R75 (R26, owner decision 22.10): each approved CLI run as `df-build`
    under `LD_DEBUG=libs` loads only objects whose real path is under
    `SYSTEM_LIB_DIRS`, and `calling init` covers its whole DT_NEEDED
    closure (else the instrument failed, not the CLI); the glibc-hwcaps/tls subdirectories that exist and
    the `ld.so.cache` entries outside `ld.so.conf` are recorded (evidence).
    A library outside is a finding to the owner, not a test edit."""
    lay = root_world.layout
    out_dir = lay.build_home / "ld-debug"
    _as_builder(["/bin/mkdir", str(out_dir)])
    loaded: dict[str, list[str]] = {}
    for name, path in sorted(root_world.cfg().approved_cli.items()):
        env = {**ROLE_ENV, "LD_DEBUG": "libs", "LD_DEBUG_OUTPUT": str(out_dir / name)}
        result = plat.run_as(
            plat.BUILD_USER,
            [str(path), "--version"],
            env=env,
            cwd=lay.build_home,
            network=False,
            timeout=60,
        )
        assert result.launched and result.returncode == 0, (name, result)
        text = "".join(p.read_text() for p in sorted(out_dir.glob(f"{name}.*")))
        loaded[name] = sorted(set(_LD_INIT.findall(text)))
        needed = _needed_closure(Path(os.path.realpath(path)))
        missing = needed - {Path(p).name for p in loaded[name]}
        if missing:  # fail-closed instrument (13-C review M3)
            infra(f"{name}: LD_DEBUG не показал {sorted(missing)}: {loaded[name]}")
    outside = {
        name: [p for p in paths if not _system_object(p)]
        for name, paths in loaded.items()
    }
    facts = {
        "loaded": loaded,
        "outside_system_lib_dirs": outside,
        "loader_subdirs": _loader_subdirs(),
        "ld_so_cache_outside_conf": _cache_outside_conf(),
    }
    _evidence("loader/linux-residuals.json", json.dumps(facts, indent=2) + "\n")
    assert all(not paths for paths in outside.values()), outside


def _needed_closure(binary: Path) -> set[str]:
    """Sonames of the DT_NEEDED closure of `binary` (and its loader), found
    in `LOADER_DIRS` as `dependency_problems` finds them."""
    seen: set[str] = set()
    pending = [binary]
    while pending:
        deps = plat.linked_libraries(pending.pop())
        if deps is None:
            continue
        names = list(deps.needed)
        if deps.loader is not None:
            names.append(Path(deps.loader).name)
        for soname in names:
            if soname in seen:
                continue
            seen.add(soname)
            for base in plat.LOADER_DIRS[PLATFORM]:
                if (Path(base) / soname).exists():
                    pending.append(Path(os.path.realpath(Path(base) / soname)))
                    break
    return seen


def _system_object(path: str) -> bool:
    real = Path(os.path.realpath(path))
    return plat.is_system_path(Path(path), PLATFORM) and plat.is_system_path(
        real, PLATFORM
    )


def _loader_subdirs() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for base in plat.LOADER_DIRS[PLATFORM]:
        for sub in _LOADER_SUBDIRS:
            path = Path(base) / sub
            if path.is_dir():
                found[str(path)] = sorted(os.listdir(path))
    return found


def _cache_outside_conf() -> list[str]:
    dirs: list[Path] = []
    problems: list[str] = []
    plat._read_ld_conf(plat.LD_SO_CONF, dirs, problems, set(), 0)
    known = {str(d) for d in dirs} | set(plat.LOADER_DIRS[PLATFORM])
    listing = run(["/usr/sbin/ldconfig", "-p"])
    cached = {str(Path(p).parent) for p in _LDCONFIG_ENTRY.findall(listing)}
    return sorted(cached - known) + [f"ld.so.conf: {p}" for p in problems]


#: The pinned auth file of the ACL probes (any relative path works).
AUTH_FILE = "auth/cred"


def _auth_profile() -> probe.HarnessProfile:
    return probe.HarnessProfile("0", probe.claude_argv, (AUTH_FILE,), (), True)


def _builder_auth(world: RootWorld) -> Path:
    """`df-build`'s home with the auth file it wrote (no ACL anywhere)."""
    home = world.layout.build_home
    script = f"mkdir -p {Path(AUTH_FILE).parent} && echo token > {AUTH_FILE}"
    result = plat.run_as(
        plat.BUILD_USER,
        ["/bin/sh", "-c", script],
        env=ROLE_ENV,
        cwd=home,
        network=False,
        timeout=30,
    )
    assert result.launched and result.returncode == 0, result
    for path in (home, home / Path(AUTH_FILE).parent, home / AUTH_FILE):
        assert not acl.acl_present_path(path), path
    return home


@LINUX_ONLY
def test_posix_acl_makes_auth_unreadable_linux(
    root_world: RootWorld, controls: Controls
) -> None:
    """C31 (R86): without an ACL the role's own file is `tracked`; any POSIX
    ACL on it (here one granting `df-build` read) makes it `unreadable`
    (v1 limit)."""
    home = _builder_auth(root_world)
    uid = controls.account.uid
    assert probe.observe_auth(_auth_profile(), home, role_uid=uid).tracking == (
        "tracked"
    )
    run([_setfacl(), "-m", f"u:{plat.BUILD_USER}:r", str(home / AUTH_FILE)])
    assert acl.acl_present_path(home / AUTH_FILE)
    obs = probe.observe_auth(_auth_profile(), home, role_uid=uid)
    assert obs == probe.AuthObservation("unavailable", "unreadable", ())


@MACOS_ONLY
def test_home_acl_facts_macos(root_world: RootWorld, controls: Controls) -> None:
    """C32 (R86): `ls -lde` of the runner's home and of a file made under it
    (inherited entries present?) is evidence; a `df-build` directory without
    an ACL is `tracked`. Real role homes are stage 2's (spec 4.5)."""
    owner = os.environ.get("SUDO_USER") or "runner"
    runner_home = Path(pwd.getpwnam(owner).pw_dir)
    made = runner_home / f".df-acl-probe-{os.getpid()}"
    made.write_text("probe\n")
    try:
        listing = run(["/bin/ls", "-lde", str(runner_home), str(made)])
    finally:
        made.unlink()
    _evidence("acl/macos-runner-home.txt", listing)
    home = _builder_auth(root_world)
    obs = probe.observe_auth(_auth_profile(), home, role_uid=controls.account.uid)
    assert obs.tracking == "tracked", obs
