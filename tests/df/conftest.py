"""Shared fixtures of the df tests: `fake_layout` (Task 7, F1/F16/F19/F27).

`fake_layout` runs as the current user and keeps production code real: only
the lower operations are replaced. Root is the current uid (`ROOT_UID`
patched); `df-build` is a distinct fake uid, kept in a virtual-owner map that
`df.accept.owner_uid` consults and `os.fchown`/`os.chown` write. The launch
mechanism (`role_argv`) becomes a plain `env -i` exec as the current user;
`pkill`/`ps` in `reap_user_processes` are recorded as one `process(df-build)`.
Events of fsutil (`fault_point`, `sync_dir`, `sync_tree`) and every `run_as`
are recorded in order; `sync_tree` is one event and syncs nothing (F16).
"""

from __future__ import annotations

import fcntl
import os
import pwd
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import accept, fsutil, journal, receive
from df import platform as plat
from df.paths import Layout
from df.platform import RunResult

#: The fake uid of `df-build`: never the current user (F1).
BUILD_UID = os.getuid() + 100000
Event = tuple[object, ...]


def fd_path(fd: int) -> Path:
    """The path of an open descriptor (macOS `F_GETPATH`, Linux `/proc`)."""
    if sys.platform == "darwin":
        raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
        return Path(raw.rstrip(b"\0").decode())
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


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
    root = Path(os.path.realpath(tmp_path)) / "root"
    root.mkdir()
    fake = FakeLayout(Layout(root), BUILD_UID)
    me = os.getuid()
    for module in (accept, receive, journal, plat):
        monkeypatch.setattr(module, "ROOT_UID", me)
    monkeypatch.setattr(accept, "owner_uid", fake.owner_of)
    _patch_chown(monkeypatch, fake)
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
