"""Crashes while `df` creates its own directories and files (9-C fix round 1).

The class (review I1-I3): an object `df/` creates, or whose metadata it
changes, must be durable in its final form -- a visible name never has a
non-final mode, and an object a retry finds is settled before it is used
(spec 3.3 r6). These tests crash at every position of the lock and step-2
phase -- every crash point of the model and, between them, every creating or
mode-changing system call `df` makes -- by a process crash and by an OS
crash; the retry must reach `drive` (no refusal forever) with every
directory and the lock at their final modes, durably.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from df import accept, admin, install, journal, launcher_entry
from tests.df.install_world import FIRST, World, at_rest_problems, leftover_ids

#: System calls that create an object or change its metadata.
SYSCALLS = ("mkdir", "open", "fchmod", "chmod", "fchown", "rename", "symlink")


def final_modes(w: World) -> dict[Path, int]:
    """Every directory and file the lock and step 2 make, at its final mode."""
    lay = w.lay
    out = dict(install.prep_dirs(lay))
    for path in (lay.root / "opt", lay.root / "etc", lay.root / "var", lay.var.parent):
        out[path] = 0o755
    out[lay.install_dir] = 0o755
    out[lay.lock] = 0o600
    return out


def nonfinal(
    w: World, *, all_present: bool = False, theirs: tuple[Path, ...] = ()
) -> list[str]:
    """Visible names at a mode other than their final one (or missing);
    `theirs` -- pre-existing directories `df` must leave as they are."""
    out: list[str] = []
    for path, mode in final_modes(w).items():
        if path in theirs:
            continue
        try:
            have = stat.S_IMODE(os.lstat(path).st_mode)
        except FileNotFoundError:
            if all_present:
                out.append(f"{path.relative_to(w.lay.root)}: missing")
            continue
        if have != mode:
            out.append(f"{path.relative_to(w.lay.root)}: {have:04o} != {mode:04o}")
    return out


class SyscallTrap:
    """A process crash right after the n-th creating/mode-changing system
    call made directly by `df` code (once armed)."""

    def __init__(self, w: World) -> None:
        self.w = w
        self.count = 0
        self.target: int | None = None
        self.seen: list[str] = []
        for name in SYSCALLS:
            w.mp.setattr(os, name, self._wrap(name, getattr(os, name)))

    def _wrap(self, name: str, real: Callable[..., object]) -> Callable[..., object]:
        def call(*a: object, **kw: object) -> object:
            out = real(*a, **kw)
            module = str(sys._getframe(1).f_globals.get("__name__"))  # noqa: SLF001
            flags = a[1] if name == "open" else os.O_CREAT
            creating = bool(int(flags) & os.O_CREAT)  # type: ignore[call-overload]
            if module.startswith("df.") and creating:
                self.count += 1
                self.seen.append(f"{name} {module}")
                if self.count == self.target:
                    self.target = None
                    self.w.m.kill_now(f"after {name} #{self.count}")
            return out

        return call

    def arm(self, n: int) -> None:
        self.count, self.target = 0, n


@pytest.mark.parametrize("crash", ["process", "os"])
def test_lock_and_step2_every_crash_position(fake_world: World, crash: str) -> None:
    """Exhaustive over the phase before `drive` (R49: the run count is
    asserted). After each crash no visible name has a non-final mode; the
    retry reaches `drive`; after a further OS crash every directory, the
    lock and `pending.toml` are there at their final modes."""
    w = fake_world
    trap = SyscallTrap(w)
    start = w.m.snapshot()
    w.kill_at("build-dir")
    first = len(w.m.points)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    points = len(w.m.points) - first - 1  # the last one is the kill's
    calls = trap.count
    positions = [("point", k) for k in range(points)] + [
        ("syscall", n) for n in range(1, calls + 1)
    ]
    runs = 0
    for kind, k in positions:
        w.m.restore(start)
        w.m.disarm()
        if kind == "point":
            w.m.trap(lambda p, i=first + k: p.index == i)
        else:
            trap.arm(k)
        w.killed(lambda: w.bootstrap(w.init_argv()))
        trap.target = None
        if crash == "os":
            w.m.os_crash()
        where = f"{kind} {k}"
        assert nonfinal(w) == [], where
        w.kill_at("build-dir")
        w.killed(lambda: w.bootstrap(w.init_argv()))  # reached drive
        assert nonfinal(w, all_present=True) == [], where
        w.m.os_crash()
        assert nonfinal(w, all_present=True) == [], where
        assert os.path.lexists(w.lay.pending), where
        runs += 1
    assert runs == len(positions)
    assert points >= 40 and calls >= 20, (points, calls)


@pytest.mark.parametrize("which", ["var/lib", "opt", "opt-ancestor"])
def test_found_directory_is_settled_before_use(fake_world: World, which: str) -> None:
    """Review I2 (S1-R7 form): a crash leaves a created directory whose name
    is not durable; the retry finds it and must settle it before writing
    beneath it. An install interrupted before `verify`, then an OS crash:
    no tree without `pending.toml`."""
    w = fake_world
    target = {
        "var/lib": w.lay.var.parent,
        "opt": w.lay.opt,
        "opt-ancestor": w.lay.root / "opt",  # step 2's missing ancestor
    }[which]
    w.m.trap(lambda p: p.kind == "sync" and p.path == target)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    w.kill_at("verify")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    w.m.os_crash()
    assert at_rest_problems(w) == []
    assert os.path.lexists(w.lay.pending)
    assert FIRST in leftover_ids(w)


def test_found_var_survives_a_retry_and_an_os_crash(fake_world: World) -> None:
    """Review I4 probe: a crash right after `/var/lib/df`'s own sync; the
    retry commits; an OS crash keeps all of `/var/lib/df`."""
    from tests.df.install_world import committed_problems  # noqa: PLC0415

    w = fake_world
    target = w.lay.var.parent
    w.m.trap(lambda p: p.kind == "sync" and p.path == target)
    w.killed(lambda: w.bootstrap(w.init_argv()))
    assert w.bootstrap(w.init_argv()) == 0
    assert committed_problems(w, FIRST, w.cfg()) == []
    w.m.os_crash()
    assert committed_problems(w, FIRST, w.cfg()) == []


ODD_UMASK = 0o277  # clears the owner's write and execute bits


def test_lock_file_mode_is_durable_under_any_umask(fake_world: World) -> None:
    """Review I3: the lock is created `0600` whatever the umask, durably; an
    OS crash never leaves `0400`, which every later writer refuses."""
    w = fake_world
    old = os.umask(ODD_UMASK)
    try:
        with journal.writer_lock(w.lay):
            accept.settle_on_acquire(w.lay)
    finally:
        os.umask(old)
    w.m.os_crash()
    assert stat.S_IMODE(os.stat(w.lay.lock).st_mode) == 0o600
    with journal.writer_lock(w.lay):
        pass


def test_entry_lock_file_mode_is_durable_under_any_umask(fake_world: World) -> None:
    """The entry's stdlib copy creates the lock the same way."""
    w = fake_world
    with journal.writer_lock(w.lay):
        pass
    os.unlink(w.lay.lock)
    w.m.baseline()
    paths = launcher_entry.EntryPaths(str(w.lay.root))
    w.mp.setattr(launcher_entry, "owner_uid", w.fl.owner_of)
    old = os.umask(ODD_UMASK)
    try:
        fd = launcher_entry.open_writer_lock(paths)
        os.close(fd)
    finally:
        os.umask(old)
    w.m.os_crash()
    assert stat.S_IMODE(os.stat(w.lay.lock).st_mode) == 0o600


def test_admin_home_mode_is_durable_under_any_umask(fake_world: World) -> None:
    """Review I3: `admin-home` is `0700` whatever the umask, durably."""
    w = fake_world
    with journal.writer_lock(w.lay):
        pass
    old = os.umask(ODD_UMASK)
    try:
        admin._ensure_admin_home(w.lay)  # noqa: SLF001
    finally:
        os.umask(old)
    accept.settle_on_acquire(w.lay)  # the name, as any later acquire would
    w.m.os_crash()
    assert stat.S_IMODE(os.stat(w.lay.admin_home).st_mode) == 0o700
    admin._ensure_admin_home(w.lay)  # noqa: SLF001


def test_points_of_the_positions_are_df_points(fake_world: World) -> None:
    """The positions enumerated above are the phase's own (a sanity check of
    the instrument): every model point before `drive` is under the lock's or
    step 2's directories, or a settle of `/usr/local/bin` or the homes."""
    w = fake_world
    w.kill_at("build-dir")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    roots = (
        w.lay.var,
        w.lay.opt,
        w.lay.etc,
        w.lay.root / "opt",
        w.lay.root / "etc",
        w.lay.local_bin,  # settled by settle_on_acquire / settle
        w.lay.build_home.parent,
    )
    stray = [
        p
        for p in w.m.points
        if not (p.path == w.lay.root or any(_under(p.path, r) for r in roots))
        and p.path not in (w.lay.root / "var", w.lay.var.parent)
    ]
    assert stray == [], stray


def _under(path: Path, top: Path) -> bool:
    return path == top or top in path.parents


@pytest.mark.parametrize(
    "name,mode,outcome",
    [
        ("staging", 0o700, "widened"),  # exact-mode directory, narrower
        ("releases", 0o700, "widened"),  # a 0755 directory, narrower
        ("staging", 0o755, "refused"),  # wider: never narrowed
    ],
)
def test_step2_widens_roots_narrower_dirs_only(
    fake_world: World, name: str, mode: int, outcome: str
) -> None:
    """A root directory narrower than its mode (an older version, an
    external chmod) is widened by step 2 instead of refused forever; a wider
    one stays a refusal (trust is never loosened)."""
    w = fake_world
    with journal.writer_lock(w.lay):
        install._make_prep_dirs(w.lay)  # noqa: SLF001 - all there: check decides
    path = getattr(w.lay, name)
    os.chmod(path, mode)
    w.m.baseline()
    code = w.bootstrap(w.init_argv())
    want = dict(install.prep_dirs(w.lay))[path]
    if outcome == "widened":
        assert code == 0
        assert stat.S_IMODE(os.stat(path).st_mode) == want
        w.m.os_crash()
        assert stat.S_IMODE(os.stat(path).st_mode) == want
    else:
        assert code == 2
        assert stat.S_IMODE(os.stat(path).st_mode) == mode


def test_pending_written_then_entry_retry_then_os_crash(fake_world: World) -> None:
    """An upgrade dies right after `pending.toml`'s rename (the generation
    already odd); the retry goes through the shipped entry, whose own
    settle makes the name durable before anything is built under it; an OS
    crash in the middle of the retry leaves no tree without it."""
    from tests.df.install_world import installed  # noqa: PLC0415

    w = fake_world
    installed(w)
    w.m.trap(lambda p: p.kind == "fault" and p.path == w.lay.pending)
    w.killed(lambda: w.entry(["install"]))
    assert not w.m.is_durable(w.lay.pending), "the window is open"
    w.kill_at("verify")
    w.killed(w.retry)
    w.m.os_crash()
    assert at_rest_problems(w) == []
    assert os.path.lexists(w.lay.pending)


@pytest.mark.parametrize("crash", ["none", "process", "os"])
def test_setgid_parents_never_leave_a_setgid_dir(fake_world: World, crash: str) -> None:
    """Review I5 (Linux, emulated by the model's R9): `/opt` and `/var/lib`
    are `2755`, so every directory made in them is born `S_ISGID`. `make_dir`
    must bring each to its exact mode before its own sync; a crash right
    after any `mkdir` leaves a name the retry repairs (clearing `S_ISGID` is
    narrowing) -- never a permanent «права 2711, нужны 0711»."""
    w = fake_world
    w.m.linux_setgid = True
    theirs = (w.lay.root / "opt", w.lay.var.parent)
    for top in theirs:
        top.mkdir(parents=True)
        os.chmod(top, 0o2755)
    w.m.baseline()
    trap = SyscallTrap(w)
    start = w.m.snapshot()
    w.kill_at("build-dir")
    w.killed(lambda: w.bootstrap(w.init_argv()))
    mkdirs = [i for i, s in enumerate(trap.seen, 1) if s.startswith("mkdir")]
    assert len(mkdirs) >= 12, trap.seen
    runs = 0
    for n in mkdirs if crash != "none" else [0]:
        w.m.restore(start)
        w.m.disarm()
        if n:
            trap.arm(n)
            w.killed(lambda: w.bootstrap(w.init_argv()))
            trap.target = None
            if crash == "os":
                w.m.os_crash()
        w.kill_at("build-dir")
        w.killed(lambda: w.bootstrap(w.init_argv()))  # reached drive
        assert nonfinal(w, all_present=True, theirs=theirs) == [], n
        w.m.os_crash()
        assert nonfinal(w, all_present=True, theirs=theirs) == [], n
        assert all(os.stat(p).st_mode & 0o7777 == 0o2755 for p in theirs), n
        runs += 1
    assert runs == (len(mkdirs) if crash != "none" else 1)
