"""Shared fixtures of the df tests: `fake_layout` (Task 7, F1/F16/F19/F27).

`fake_layout` runs as the current user and keeps production code real: only
the lower operations are replaced. Root is the current uid (`ROOT_UID`
patched); `df-build` is a distinct fake uid, kept in a virtual-owner map that
`df.fsutil.owner_uid` — the one owner seam of every module — consults and
`os.fchown`/`os.chown` write. The map is keyed by inode, so an entry is
dropped when the fake sees the object removed (`os.rmdir`/`os.unlink`/
`os.remove`), replaced (the target of `os.rename`/`os.replace`) or created
(`os.mkdir`, `os.open` with `O_CREAT`): a reused inode never inherits a stale
owner, and a renamed object keeps its own. The launch
mechanism (`role_argv`) becomes a plain `env -i` exec as the current user;
`pkill`/`ps` in `reap_user_processes` are recorded as one `process(df-build)`.
Events of fsutil (`fault_point`, `sync_dir`, `sync_tree`) and every `run_as`
are recorded in order; `sync_tree` is one event and syncs nothing (F16).

`fake_world` (Task 9-C) is the install world of `tests/df/install_world.py`
over `fake_layout` with the OS-crash model of `tests/df/crash_model.py`
installed on top: the model replaces the sync seams (so `sync_tree` syncs
again, in the model) and keeps only synced effects across `os_crash()`.
Every test runs under `umask 022` (autouse; carried from Task 7).

Two run gates (Task 13, R77/R78): a `root` test is skipped unless euid 0 and
`DF_ROOT_ACCEPTANCE=1`; with `DF_REQUIRE_PINNED_UV=1` a pinned-uv skip fails
the session (CI df-stage1 sets it).
"""

from __future__ import annotations

import os
import pwd
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from df import accept, fsutil, journal, receive
from df import platform as plat
from df.paths import Layout
from df.platform import RunResult
from tests.df.crash_model import CrashModel, fd_path

if TYPE_CHECKING:
    from tests.df.install_world import World
    from tests.df.publisher_world import World as PublisherWorld
    from tests.df.root_world import Controls, FixtureRepo, InstalledWorld, RootWorld

#: The fake uid of `df-build`: never the current user (F1).
BUILD_UID = os.getuid() + 100000
Event = tuple[object, ...]

#: Root gate (Task 13, R77): a `root` test runs only as euid 0 and only when
#: the acceptance run asks for it with `DF_ROOT_ACCEPTANCE=1`.
ROOT_ACCEPTANCE_ENV = "DF_ROOT_ACCEPTANCE"
#: R78: df-stage1 sets it, so a pinned-uv test can never vanish as a skip.
PINNED_UV_ENV = "DF_REQUIRE_PINNED_UV"
_PINNED_UV_SKIP = re.compile(r"uv не найден|не закреплённая")
_skipped_for_uv: list[str] = []


def root_gate_reason(euid: int, env: Mapping[str, str]) -> str | None:
    """Why a `root` test is skipped here; `None` when the gate is open."""
    if euid == 0 and env.get(ROOT_ACCEPTANCE_ENV) == "1":
        return None
    return f"root-приёмка: нужны euid 0 и {ROOT_ACCEPTANCE_ENV}=1 (CI df-stage1)"


def pytest_configure(config: pytest.Config) -> None:
    """The `stage2a` marker (Task 14, R14-6): the stage-2a root acceptance,
    run by its own CI job; the stage-1 root run deselects it."""
    config.addinivalue_line(
        "markers", "stage2a: stage-2a root acceptance (CI job stage2a, with root)"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip every `root` test unless the root gate is open."""
    reason = root_gate_reason(os.geteuid(), os.environ)
    if reason is None:
        return
    for item in items:
        if item.get_closest_marker("root") is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Note a pinned-uv skip when `DF_REQUIRE_PINNED_UV=1` (R78)."""
    if os.environ.get(PINNED_UV_ENV) != "1" or not report.skipped:
        return
    longrepr = report.longrepr
    reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    if _PINNED_UV_SKIP.search(reason):
        _skipped_for_uv.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session that skipped a pinned-uv test under R78."""
    if _skipped_for_uv:
        names = ", ".join(_skipped_for_uv)
        print(f"\n{PINNED_UV_ENV}=1, но закреплённого uv нет: {names}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@dataclass
class FakeLayout:
    """A layout under `tmp_path` with recorded lower operations."""

    layout: Layout
    build_uid: int
    events: list[Event] = field(default_factory=list)
    owners: dict[tuple[int, int], int] = field(default_factory=dict)

    def make_dirs(self) -> None:
        """The installer's directories, owned by the fake root."""
        lay = self.layout
        for path in (
            lay.install_dir,
            lay.marks_dir,
            lay.etc,
            lay.releases,
            lay.quarantine,
            lay.admin,
            lay.admin_python,
            lay.bin_dir,
            lay.verify_dir,
            lay.local_bin,
            lay.build_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        lay.admin_home.mkdir(mode=0o700, exist_ok=True)  # as step 2 leaves it

    def set_owner(self, path: Path, uid: int) -> None:
        """Give `path` a virtual owner (as a recorded `chown` would)."""
        st = os.stat(path, follow_symlinks=False)
        self.owners[(st.st_dev, st.st_ino)] = uid

    def owner_of(self, st: os.stat_result) -> int:
        """Virtual owner of a stat result, else its real owner (= fake root)."""
        return self.owners.get((st.st_dev, st.st_ino), st.st_uid)

    def builder_dir(self, path: Path) -> Path:
        """A directory of `df-build` (as `prepare_dir` leaves it)."""
        path.mkdir(mode=0o700)
        self.set_owner(path, self.build_uid)
        return path

    def clear(self) -> None:
        """Forget the recorded events."""
        self.events.clear()


@pytest.fixture()
def fake_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeLayout:
    """See the module docstring."""
    return make_fake_layout(monkeypatch, tmp_path)


def make_fake_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeLayout:
    """`fake_layout` under `tmp_path` with `monkeypatch` (any scope's)."""
    root = Path(os.path.realpath(tmp_path)) / "root"
    root.mkdir()
    fake = FakeLayout(Layout(root), BUILD_UID)
    me = os.getuid()
    for module in (accept, receive, journal, plat):
        monkeypatch.setattr(module, "ROOT_UID", me)
    monkeypatch.setattr(fsutil, "owner_uid", fake.owner_of)
    _patch_chown(monkeypatch, fake)
    _patch_inode_reuse(monkeypatch, fake)
    _patch_fsutil(monkeypatch, fake)
    _patch_launch(monkeypatch, fake)
    return fake


def _patch_chown(monkeypatch: pytest.MonkeyPatch, fake: FakeLayout) -> None:
    def fchown(fd: int, uid: int, gid: int) -> None:
        st = os.fstat(fd)
        fake.events.append(("fchown", fd_path(fd), uid))
        fake.owners[(st.st_dev, st.st_ino)] = uid

    def chown(path: object, uid: int, gid: int, **kwargs: object) -> None:
        target = Path(str(path))
        fake.events.append(("chown", target, uid))
        follow = bool(kwargs.get("follow_symlinks", True))
        st = os.stat(target, follow_symlinks=follow)
        fake.owners[(st.st_dev, st.st_ino)] = uid

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(os, "chown", chown)


def _patch_inode_reuse(monkeypatch: pytest.MonkeyPatch, fake: FakeLayout) -> None:
    real_mkdir, real_rmdir = os.mkdir, os.rmdir
    real_unlink, real_remove = os.unlink, os.remove
    real_rename, real_replace, real_open = os.rename, os.replace, os.open

    def forget(path: object, dir_fd: int | None) -> None:
        try:
            st = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)  # type: ignore[arg-type]
        except OSError:
            return
        fake.owners.pop((st.st_dev, st.st_ino), None)

    def mkdir(path: object, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)  # type: ignore[arg-type]
        forget(path, dir_fd)

    def removing(real: Callable[..., None]) -> Callable[..., None]:
        def remove(path: object, *, dir_fd: int | None = None) -> None:
            forget(path, dir_fd)
            real(path, dir_fd=dir_fd)

        return remove

    def replacing(real: Callable[..., None]) -> Callable[..., None]:
        def rename(
            src: object,
            dst: object,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            forget(dst, dst_dir_fd)
            real(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        return rename

    def open_(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        new = bool(flags & os.O_CREAT) and not _lexists_at(path, dir_fd)
        fd = real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]
        if new:
            st = os.fstat(fd)
            fake.owners.pop((st.st_dev, st.st_ino), None)
        return fd

    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setattr(os, "rename", replacing(real_rename))
    monkeypatch.setattr(os, "replace", replacing(real_replace))
    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "rmdir", removing(real_rmdir))
    monkeypatch.setattr(os, "unlink", removing(real_unlink))
    monkeypatch.setattr(os, "remove", removing(real_remove))


def _lexists_at(path: object, dir_fd: int | None) -> bool:
    try:
        os.stat(path, dir_fd=dir_fd, follow_symlinks=False)  # type: ignore[arg-type]
    except OSError:
        return False
    return True


def _patch_fsutil(monkeypatch: pytest.MonkeyPatch, fake: FakeLayout) -> None:
    real_sync_dir = fsutil.sync_dir

    def fault_point(kind: str, path: Path) -> None:
        fake.events.append((kind, path))

    def sync_dir(fd_or_path: int | Path) -> fsutil.SyncNote:
        path = fd_path(fd_or_path) if isinstance(fd_or_path, int) else fd_or_path
        fake.events.append(("sync_dir", path))
        return real_sync_dir(fd_or_path)

    def sync_tree(root: Path) -> None:
        fake.events.append(("sync_tree", root))

    monkeypatch.setattr(fsutil, "fault_point", fault_point)
    monkeypatch.setattr(fsutil, "sync_dir", sync_dir)
    monkeypatch.setattr(fsutil, "sync_tree", sync_tree)
    monkeypatch.setattr(fsutil, "_SYNC_NOTES", set())


def _patch_launch(monkeypatch: pytest.MonkeyPatch, fake: FakeLayout) -> None:
    real_run_as = plat.run_as
    real_run_command = plat.run_command
    real_getpwnam = pwd.getpwnam
    me = real_getpwnam(pwd.getpwuid(os.getuid()).pw_name)

    def role_argv(
        platform: object,
        user: str,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        network: bool,
    ) -> list[str]:
        return plat._env_argv(argv, env)  # noqa: SLF001 - the real `env -i` chain

    def run_as(user: str, argv: Sequence[str], **kwargs: object) -> RunResult:
        fake.events.append(("run_as", user, tuple(argv)))
        return real_run_as(user, argv, **kwargs)  # type: ignore[arg-type]

    def getpwnam(name: str) -> pwd.struct_passwd:
        return me if name == plat.BUILD_USER else real_getpwnam(name)

    def run_command(argv: Sequence[str], **kwargs: object) -> RunResult:
        if list(argv[:1]) == ["/usr/bin/pkill"]:
            fake.events.append(("process", argv[-1]))
            return RunResult(0, b"", b"", launched=True)
        if list(argv[:1]) == ["/bin/ps"]:
            return RunResult(0, b"", b"", launched=True)
        return real_run_command(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(plat, "role_argv", role_argv)
    monkeypatch.setattr(plat, "run_as", run_as)
    monkeypatch.setattr(plat.pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(plat, "run_command", run_command)


@pytest.fixture(autouse=True)
def _umask_022() -> Iterator[None]:
    """Every df test runs under `umask 022` (carry of Task 7, M18)."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


@pytest.fixture()
def fake_world(
    monkeypatch: pytest.MonkeyPatch, fake_layout: FakeLayout
) -> Iterator[World]:
    """The install world with the OS-crash model (`World.model`)."""
    world = make_fake_world(fake_layout, monkeypatch)
    try:
        yield world
    finally:
        world.m.close()


def make_fake_world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
    """`fake_world` over `fake_layout` with `monkeypatch` (any scope's)."""
    from tests.df.install_world import make_world  # it imports this module

    world = make_world(fake_layout, monkeypatch)
    model = CrashModel(fake_layout.layout.root, fake_layout)
    model.install(monkeypatch)
    world.model = model
    return world


# ---------- the root world (Task 13-B; only root tests reach these) ----------

#: Where CI collects evidence (`$RUNNER_TEMP/df-stage1`); unset locally.
STAGE1_OUT_ENV = "DF_STAGE1_OUT"


def stage1_out() -> Path | None:
    """The evidence directory of the CI run, if any."""
    value = os.environ.get(STAGE1_OUT_ENV)
    return Path(value) if value else None


@pytest.fixture(scope="session")
def acceptance_top() -> Iterator[Path]:
    """`<base>/df-acc-<pid>` (R83), removed at the end of the session."""
    from tests.df.root_world import acceptance_dir

    with acceptance_dir() as top:
        yield top


@pytest.fixture(scope="session")
def fixture_repo(acceptance_top: Path) -> Iterator[FixtureRepo]:
    """The bare fixture repo and the URL the build fetches it from (R91)."""
    from tests.df.root_world import FixtureRepo, build_fixture_repo, serve_repo

    bare, sha = build_fixture_repo(acceptance_top)
    with serve_repo(acceptance_top, bare) as url:
        yield FixtureRepo(url, sha)


@pytest.fixture(scope="session")
def control_url() -> Iterator[str]:
    """The control address on the runner's external interface (spec 3.8)."""
    from tests.df.root_world import control_server

    with control_server() as url:
        yield url


@pytest.fixture()
def root_world(
    acceptance_top: Path,
    fixture_repo: FixtureRepo,
    control_url: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[RootWorld]:
    """A fresh root world (R90): `<R>`, the root uv copy, the profile pin
    through `install._profile` (R91); `df-build` removed afterwards."""
    from df import install
    from tests.df.root_world import make_world, teardown_world

    world = make_world(acceptance_top, fixture_repo, control_url)
    monkeypatch.setattr(install, "_profile", lambda _cfg: world.profile())
    yield world
    out = stage1_out()
    keep = None
    if out is not None:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)
        keep = out / "journals" / f"{name}.jsonl"
    teardown_world(world, keep)


@pytest.fixture()
def controls(root_world: RootWorld) -> Controls:
    """Spec 3.8 positive controls over a real `df-build` (before any
    negative test); a failure fails the test as infrastructure."""
    from tests.df.root_world import check_controls

    root_world.prepare_build_user()
    return check_controls(root_world)


@pytest.fixture(scope="module")
def installed_world(
    acceptance_top: Path,
    fixture_repo: FixtureRepo,
    control_url: str,
    request: pytest.FixtureRequest,
) -> Iterator[InstalledWorld]:
    """R90: one committed install (`install_once`) shared by the read-only
    tests of a module; they must come after its `root_world` tests (a live
    `df-build` fails a fresh world as infrastructure). Torn down like
    `root_world` at the end of the module."""
    from df import install
    from tests.df.root_world import install_once, make_world, teardown_world

    world = make_world(acceptance_top, fixture_repo, control_url)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(install, "_profile", lambda _cfg: world.profile())
            installed = install_once(world)
        yield installed
    finally:
        out = stage1_out()
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", request.module.__name__)
        keep = None if out is None else out / "journals" / f"{name}-installed.jsonl"
        teardown_world(world, keep)


@pytest.fixture()
def installed_controls(installed_world: InstalledWorld) -> Controls:
    """The positive `controls` over the installed world's `df-build`."""
    from tests.df.root_world import check_controls

    return check_controls(installed_world.world)


@pytest.fixture()
def world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> PublisherWorld:
    """The publisher's world (`tests/df/publisher_world.py`)."""
    from tests.df.publisher_world import build_world

    return build_world(fake_layout, monkeypatch)
