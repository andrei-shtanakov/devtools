"""The OS-crash model of `fake_world` (Task 9-C): only synced effects survive.

The model replaces the sync seams (`fsutil.sync_file`, `sync_dir`,
`sync_tree`, the names `df.journal` bound, and `os.fsync` for code outside
`fsutil`) and keeps, per object, the last state a sync made durable.
Nothing is synced for real: the model's record is the durable state. The
rules (spec 2026-10-13-df-installer-design.md §3.3 «Синхронизация»,
«Протоколы примитивов», «Модель сбоя»):

- R1 A sync of a regular file (`sync_file`) makes durable its content, mode
  and owner -- never its name. On macOS a bare `os.fsync` of a file is not
  one (§3.3: «Файл -- F_FULLFSYNC на macOS»); `fcntl(F_FULLFSYNC)` outside
  `fsutil` (the entry's stdlib copy) is.
- R2 A sync of a directory (`sync_dir`, its own fsync) makes durable its
  entries -- names to objects, a symbolic link by its target -- and its own
  mode and owner, never a child's content or metadata. So a `rename`,
  `unlink`, `mkdir` or `rmdir` is durable only by the parent's sync (the
  window «виден, но не долговечен», rev 8 r5), and a `chmod`/`chown` only by
  a sync of the object itself (protocol `verify_chmod`: `chmod` ->
  `sync_dir(releases/<id>)`; `prepare_dir`: `fchown` -> `fsync(fd)`).
- R3 `sync_tree(root)` is R1 for every regular file and R2 for every
  directory under `root`, `root` included; links are not followed; `root`'s
  own name in its parent is not made durable.
- R4 An OS crash (`os_crash`) keeps exactly the durable state reachable from
  the world root. An object whose name is durable but which was never
  synced itself comes back as born: a file empty, a directory empty, with
  the mode and owner it was created with (the spec is silent; the harsher
  case). In-process creations (`os.mkdir`, `os.open(O_CREAT)`) are recorded
  at creation, and every in-process `chmod`/`chown` records the object as it
  was before its first change; an object another process (or Python's
  built-in `open`) created is recorded when the model first sees it (L1).
- R5 A name durable in two directories (a rename synced at the destination
  only) comes back in both -- a file as one inode (a hard link), a
  directory as a copy whose objects keep their identity through `origin`;
  a rename synced at the source only is lost from both (spec: «имя
  долговечно в карантине раньше, чем уходит из родителя»).
- R6 An OS crash is a new boot: `fsutil.boot_id` changes.
- R7 A process crash (`Crash`, raised at a trapped point or by `kill_now`)
  leaves the filesystem exactly as at that point: `reap` undoes what the
  dying code's exception handlers did afterwards (a killed process runs
  none), and any point reached after the crash raises `Crash` again.
- R8 Only the world root is modeled (the fake uv outside it survives).
- R9 (opt-in, `linux_setgid`): a directory created in an `S_ISGID` parent
  is born `S_ISGID`, as Linux `inode_init_owner` does; on macOS (BSD) it is
  not, so the knob emulates the other supported platform.

Limits: L1 above; L2 every object must be readable and searchable by its
owner (the fake runs as one user) -- otherwise `ModelError`, never a guess;
L3 a symbolic link, fifo or socket is not synced on its own (a link is a
value of its directory, anything else is `ModelError`); L4 an OS crash
loses *all* unsynced effects at once -- a real journaling filesystem may
also persist a part of them (a periodic commit: a half-done `rmtree` back
as a partial tree). Only the two extremes are modeled: a process crash
keeps everything, an OS crash nothing unsynced; a prefix is not tested
(the evidence's level «не испытано»).

Identity: every observed object gets a model id, and the model keeps a
descriptor open on it (a pin), so an inode number is never reused while
the model may confuse it with an old object. Points (`Point`) are numbered
in order: every `fault_point` (the window «visible, not durable») and every
sync. `trap(pred)` turns the first point matching `pred` into a process
crash; a trapped sync crashes before it takes effect. `drop_syncs(pred)`
turns matching syncs into no-ops (twins). `snapshot`/`restore` branch a run
from a state (R49): the files, the durable records, the boot.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol

import pytest

from df import fsutil, journal

#: The real functions, taken at import -- before any fixture replaces them.
_MKDIR, _RMDIR, _UNLINK, _OPEN = os.mkdir, os.rmdir, os.unlink, os.open
_SYMLINK, _CHMOD, _FCHMOD, _LINK = os.symlink, os.chmod, os.fchmod, os.link
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_ANY_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
#: `open` errors that mean "no object to record" (a link, or nothing there).
_NO_OBJECT = frozenset({errno.ELOOP, errno.ENOENT, errno.ENOTDIR})
_IS_DARWIN = sys.platform == "darwin"
_F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", 51)
_F_GETPATH = getattr(fcntl, "F_GETPATH", 50)
_FCNTL = fcntl.fcntl

Kind = Literal["dir", "file"]
Key = tuple[int, int]


class Crash(BaseException):
    """A process crash: the run stops here; no handler may swallow it."""


class ModelError(AssertionError):
    """A state the model does not model: the test must not pass on it."""


class Owners(Protocol):
    """What the model needs of `FakeLayout`: virtual owners and events."""

    owners: dict[Key, int]
    events: list[tuple[object, ...]]

    def owner_of(self, st: os.stat_result) -> int: ...


@dataclass(frozen=True)
class Link:
    """A symbolic link: a value of its directory's entry (R2, L3)."""

    target: str


Entry = int | Link


@dataclass(frozen=True)
class Rec:
    """One object: `data` is a file's bytes or a directory's entries."""

    kind: Kind
    mode: int
    owner: int
    data: bytes | Mapping[str, Entry]

    def entries(self) -> Mapping[str, Entry]:
        """A directory's entries."""
        assert not isinstance(self.data, bytes)
        return self.data

    def as_born(self) -> Rec:
        """The same object as created: no content, no entries (R4)."""
        return replace(self, data=b"" if self.kind == "file" else {})


@dataclass(frozen=True)
class Point:
    """A crash point: `fault` (its `fault_point` kind) or `sync` (the call)."""

    index: int
    kind: Literal["fault", "sync"]
    op: str
    path: Path


@dataclass(frozen=True)
class State:
    """A branch point (R49): the files and the model's durable knowledge."""

    current: dict[int, Rec]
    durable: dict[int, Rec]
    born: dict[int, Rec]
    boot: int
    origin: dict[int, int]
    points: int


def fd_path(fd: int) -> Path:
    """The path of an open descriptor (macOS `F_GETPATH`, Linux `/proc`)."""
    if _IS_DARWIN:
        raw = _FCNTL(fd, _F_GETPATH, b"\0" * 1024)
        return Path(raw.rstrip(b"\0").decode())
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


@dataclass
class CrashModel:
    """See the module docstring."""

    root: Path
    fake: Owners
    durable: dict[int, Rec] = field(default_factory=dict)
    born: dict[int, Rec] = field(default_factory=dict)
    origin: dict[int, int] = field(default_factory=dict)
    points: list[Point] = field(default_factory=list)
    boot: int = 1
    root_id: int = 0
    _live: dict[Key, int] = field(default_factory=dict)
    _pins: dict[Key, int] = field(default_factory=dict)
    _next: int = 1
    _traps: list[Callable[[Point], bool]] = field(default_factory=list)
    _drops: list[Callable[[Point], bool]] = field(default_factory=list)
    _dead: State | None = None
    #: R9: emulate Linux setgid inheritance of created directories (opt-in).
    linux_setgid: bool = False

    # ---- installation -------------------------------------------------------

    def install(self, mp: pytest.MonkeyPatch) -> None:
        """Replace the seams (over `fake_layout`'s), then take the baseline."""
        mp.setattr(fsutil, "sync_file", self._sync_file)
        mp.setattr(fsutil, "sync_dir", self._sync_dir)
        mp.setattr(fsutil, "sync_tree", self._sync_tree)
        for name, hook in (
            ("sync_file", self._sync_file),
            ("sync_dir", self._sync_dir),
        ):
            if hasattr(journal, name):  # names `df.journal` bound at import
                mp.setattr(journal, name, hook)
        mp.setattr(fsutil, "fault_point", self._wrap_fault(fsutil.fault_point))
        mp.setattr(fsutil, "boot_id", self.boot_id)
        mp.setattr(os, "fsync", self._fsync)
        mp.setattr(fcntl, "fcntl", self._fcntl(fcntl.fcntl))
        mp.setattr(os, "mkdir", self._creating_mkdir(os.mkdir))
        mp.setattr(os, "open", self._creating_open(os.open))
        for name in ("chmod", "chown"):
            mp.setattr(os, name, self._changing_path(getattr(os, name)))
        for name in ("fchmod", "fchown"):
            mp.setattr(os, name, self._changing_fd(getattr(os, name)))
        self.root_id = self._id_path(self.root)
        self.baseline()

    def close(self) -> None:
        """Release every pin."""
        for fd in self._pins.values():
            os.close(fd)
        self._pins.clear()
        self._live.clear()

    def boot_id(self) -> str:
        """`fsutil.boot_id` of the model: new after each OS crash (R6)."""
        return f"model-boot-{self.boot}"

    # ---- the public operations -----------------------------------------------

    def baseline(self) -> None:
        """Everything on disk now is durable (the start, a prepared state)."""
        self._require_alive()
        self.durable = self._capture()
        self.born = {}

    def trap(self, pred: Callable[[Point], bool]) -> None:
        """The first later point matching `pred` becomes a process crash."""
        self._traps.append(pred)

    def untrap(self, pred: Callable[[Point], bool]) -> None:
        """Forget one trap (a watcher that never fires), keeping the others."""
        if pred in self._traps:
            self._traps.remove(pred)

    def drop_syncs(self, pred: Callable[[Point], bool]) -> None:
        """Twins: a sync point matching `pred` takes no effect (still counted)."""
        self._drops.append(pred)

    def disarm(self) -> None:
        """Forget every trap and every dropped sync."""
        self._traps.clear()
        self._drops.clear()

    def kill_now(self, why: str = "killed") -> None:
        """A process crash here (R7), for spies between points."""
        self._require_alive()
        self._dead = self.snapshot()
        raise Crash(why)

    @property
    def dead(self) -> bool:
        """Whether a process crash awaits `reap`."""
        return self._dead is not None

    def reap(self) -> None:
        """After a process crash: the files exactly as the process left them."""
        if self._dead is None:
            return
        dead, self._dead = self._dead, None
        self.restore(dead)

    def os_crash(self) -> None:
        """An OS crash (R4-R6): only the durable state, then a new boot."""
        self.reap()
        self.durable = self._materialize(self._durable_image())
        self.born = {}
        self.boot += 1

    def snapshot(self) -> State:
        """The state now, to branch from (R49)."""
        return State(
            current=self._capture(),
            durable=dict(self.durable),
            born=dict(self.born),
            boot=self.boot,
            origin=dict(self.origin),
            points=len(self.points),
        )

    def restore(self, state: State) -> None:
        """Back to `state`: files, durable records, boot, point count.

        Traps and dropped syncs are not part of a state: they stay as armed
        (a branch keeps its twin's drops; `disarm` clears them)."""
        self._materialize(state.current)
        self.durable = dict(state.durable)
        self.born = dict(state.born)
        self.boot = state.boot
        self.origin = dict(state.origin)
        del self.points[state.points :]
        self._dead = None

    def unsynced(self) -> list[str]:
        """Root-relative paths an OS crash now would change (not durable)."""
        now = _flatten(self._capture(), self.root_id)
        then = _flatten(self._durable_image(), self.root_id)
        return sorted(p for p in now.keys() | then.keys() if now.get(p) != then.get(p))

    def is_durable(self, path: Path) -> bool:
        """Whether the object at `path` is the one an OS crash now keeps
        there: the same object with the same state (a link: the same target)."""
        image = self._durable_image()
        entry: Entry = self.root_id
        for name in path.relative_to(self.root).parts:
            if isinstance(entry, Link) or image[entry].kind != "dir":
                return False
            found = image[entry].entries().get(name)
            if found is None:
                return False
            entry = found
        st = os.stat(path, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            return entry == Link(os.readlink(path))
        if isinstance(entry, Link) or entry != self._live.get(_key(st)):
            return False
        now = _flatten(self._capture(), self.root_id)
        then = _flatten(image, self.root_id)
        rel = str(path.relative_to(self.root))
        return now.get(rel) == then.get(rel)

    def durable_names(self, path: Path) -> frozenset[str]:
        """The names an OS crash now would leave in the directory `path`
        (empty if the directory itself would not be there)."""
        image = self._durable_image()
        entry: Entry = self.root_id
        for name in path.relative_to(self.root).parts:
            if isinstance(entry, Link) or image[entry].kind != "dir":
                return frozenset()
            found = image[entry].entries().get(name)
            if found is None:
                return frozenset()
            entry = found
        if isinstance(entry, Link) or image[entry].kind != "dir":
            return frozenset()
        return frozenset(image[entry].entries())

    def identity(self, path: Path) -> int:
        """The model id of the object at `path` (its origin after R5)."""
        found = self._live.get(_key(os.stat(path, follow_symlinks=False)))
        if found is None:
            raise ModelError(f"{path}: not seen by the model")
        return self.origin.get(found, found)

    # ---- points ----------------------------------------------------------------

    def _point(self, kind: Literal["fault", "sync"], op: str, path: Path) -> bool:
        """Count a point, crash if trapped; whether a sync takes effect."""
        self._require_alive()
        point = Point(len(self.points), kind, op, path)
        self.points.append(point)
        for pred in list(self._traps):
            if pred(point):
                self._traps.remove(pred)
                self._dead = self.snapshot()
                raise Crash(f"{kind} {op} {path}")
        return not (kind == "sync" and any(p(point) for p in self._drops))

    def _require_alive(self) -> None:
        if self._dead is not None:
            raise Crash("the process is dead")

    # ---- the seams ---------------------------------------------------------------

    def _wrap_fault(
        self, inner: Callable[[fsutil.FaultKind, Path], None]
    ) -> Callable[[fsutil.FaultKind, Path], None]:
        def fault_point(kind: fsutil.FaultKind, path: Path) -> None:
            inner(kind, path)
            self._point("fault", kind, path)

        return fault_point

    def _sync_file(self, fd: int) -> None:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            # the real sync_file of a directory may fail on macOS: never a guess
            raise ModelError(f"sync_file of a non-file: {fd_path(fd)}")
        if self._point("sync", "sync_file", fd_path(fd)):
            self._record(fd)

    def _sync_dir(self, fd_or_path: int | Path) -> fsutil.SyncNote:
        if isinstance(fd_or_path, int):
            path = fd_path(fd_or_path)
            self.fake.events.append(("sync_dir", path))
            if self._point("sync", "sync_dir", path):
                self._record(fd_or_path)
            return fsutil.SyncNote.OK
        self.fake.events.append(("sync_dir", fd_or_path))
        fd = _OPEN(fd_or_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            if self._point("sync", "sync_dir", fd_or_path):
                self._record(fd)
        finally:
            os.close(fd)
        return fsutil.SyncNote.OK

    def _sync_tree(self, root: Path) -> None:
        """R3, the walk of the real `fsutil.sync_tree`."""
        self.fake.events.append(("sync_tree", root))
        if not self._point("sync", "sync_tree", root):
            return
        for dirpath, _dirs, files in os.walk(
            root, topdown=False, followlinks=False, onerror=_raise
        ):
            base = Path(dirpath)
            for name in files:
                if stat.S_ISREG(os.lstat(base / name).st_mode):
                    with _opened(base / name, _FILE_FLAGS) as fd:
                        self._record(fd)
            with _opened(base, _DIR_FLAGS) as fd:
                self._record(fd)

    def _fsync(self, fd: int) -> None:
        """`os.fsync` outside `fsutil` (R1: not a file sync on macOS)."""
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        if self._point("sync", "fsync", fd_path(fd)) and (is_dir or not _IS_DARWIN):
            self._record(fd)

    def _fcntl(self, inner: Callable[..., object]) -> Callable[..., object]:
        """`F_FULLFSYNC` outside `fsutil` (the entry's copy): a file or a
        directory sync (R1/R2); every other command passes through."""

        def call(fd: int, cmd: int, *args: object) -> object:
            if not _IS_DARWIN or cmd != _F_FULLFSYNC:
                return inner(fd, cmd, *args)
            if self._point("sync", "fullfsync", fd_path(fd)):
                self._record(fd)
            return 0

        return call

    def _creating_mkdir(self, inner: Callable[..., None]) -> Callable[..., None]:
        def mkdir(
            path: object, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> None:
            inner(path, mode, dir_fd=dir_fd)
            with _opened(path, _DIR_FLAGS, dir_fd) as fd:
                if self.linux_setgid:
                    self._inherit_setgid(fd)
                self._note_birth(fd)

        return mkdir

    def _inherit_setgid(self, fd: int) -> None:
        """R9 (opt-in): Linux `inode_init_owner` -- a directory created in a
        `S_ISGID` parent is born `S_ISGID` too (macOS does not inherit)."""
        parent = os.stat(fd_path(fd).parent)
        if parent.st_mode & stat.S_ISGID:
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            _FCHMOD(fd, mode | stat.S_ISGID)

    def _creating_open(self, inner: Callable[..., int]) -> Callable[..., int]:
        def open_(
            path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            new = bool(flags & os.O_CREAT) and not _lexists(path, dir_fd)
            fd = inner(path, flags, mode, dir_fd=dir_fd)
            if new:
                self._note_birth(fd)
            return fd

        return open_

    def _changing_path(self, inner: Callable[..., None]) -> Callable[..., None]:
        def change(path: object, *args: object, **kwargs: object) -> None:
            follow = bool(kwargs.get("follow_symlinks", True))
            flags = _ANY_FLAGS | (0 if follow else os.O_NOFOLLOW)
            try:
                dir_fd = kwargs.get("dir_fd")
                fd = _OPEN(path, flags, dir_fd=dir_fd)  # type: ignore[arg-type]
            except OSError as exc:
                if exc.errno not in _NO_OBJECT:
                    raise ModelError(f"L2: {path}: {exc}") from exc
                fd = None  # a link itself, or nothing: no object to record
            if fd is not None:
                try:
                    self._note_birth(fd)
                finally:
                    os.close(fd)
            inner(path, *args, **kwargs)

        return change

    def _changing_fd(self, inner: Callable[..., None]) -> Callable[..., None]:
        def change(fd: int, *args: object) -> None:
            self._note_birth(fd)
            inner(fd, *args)

        return change

    # ---- records -------------------------------------------------------------------

    def _note_birth(self, fd: int) -> None:
        """Record the object as it is before its first change (R4)."""
        st = os.fstat(fd)
        kind = _kind(st)
        if kind is None:
            return
        oid = self._id_fd(fd)
        if oid not in self.durable and oid not in self.born:
            self.born[oid] = self._rec(st, b"" if kind == "file" else {})

    def _record(self, fd: int) -> None:
        """R1/R2: the object of `fd` becomes durable as it is now."""
        st = os.fstat(fd)
        oid = self._id_fd(fd)
        if stat.S_ISDIR(st.st_mode):
            self.durable[oid] = self._rec(st, self._entries(fd, note=True))
        elif stat.S_ISREG(st.st_mode):
            self.durable[oid] = self._rec(st, _read_fd(fd))
        else:
            raise ModelError(f"L3: sync of {fd_path(fd)}")

    def _rec(self, st: os.stat_result, data: bytes | Mapping[str, Entry]) -> Rec:
        kind = _kind(st)
        assert kind is not None
        return Rec(kind, stat.S_IMODE(st.st_mode), self.fake.owner_of(st), data)

    def _entries(self, fd: int, *, note: bool) -> dict[str, Entry]:
        """A directory's entries; with `note`, unseen children are born (R4)."""
        out: dict[str, Entry] = {}
        for name in sorted(_listdir(fd)):
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode):
                out[name] = Link(os.readlink(name, dir_fd=fd))
                continue
            flags = _DIR_FLAGS if stat.S_ISDIR(st.st_mode) else _FILE_FLAGS
            if _kind(st) is None:
                raise ModelError(f"L3: {fd_path(fd) / name}")
            with _opened(name, flags, fd) as child:
                if note:
                    self._note_birth(child)
                out[name] = self._id_fd(child)
        return out

    # ---- identity ------------------------------------------------------------------

    def _id_fd(self, fd: int) -> int:
        """The model id of `fd`'s object; a new one is pinned.

        The pin is a fresh open of the object, never a `dup` of the
        caller's descriptor: a `dup` shares its open file description and
        would keep the caller's `flock` alive. An object with no path left
        (unlinked) gets an id but no pin and no live entry: its inode
        number may be reused, and nothing can sync it by name again.
        """
        st = os.fstat(fd)
        key = _key(st)
        found = self._live.get(key)
        if found is not None:
            return found
        oid = self._new_id()
        flags = _DIR_FLAGS if stat.S_ISDIR(st.st_mode) else _FILE_FLAGS
        try:
            pin = _OPEN(fd_path(fd), flags)
        except OSError:
            return oid
        if _key(os.fstat(pin)) != key:
            os.close(pin)
            return oid
        self._live[key] = oid
        self._pins[key] = pin
        return oid

    def _id_path(self, path: Path) -> int:
        with _opened(path, _DIR_FLAGS if path.is_dir() else _FILE_FLAGS) as fd:
            return self._id_fd(fd)

    def _new_id(self) -> int:
        oid, self._next = self._next, self._next + 1
        return oid

    # ---- images ----------------------------------------------------------------------

    def _capture(self) -> dict[int, Rec]:
        """The current image: every object under the root, by model id."""
        out: dict[int, Rec] = {}
        with _opened(self.root, _DIR_FLAGS) as fd:
            self._capture_dir(fd, out)
        return out

    def _capture_dir(self, fd: int, out: dict[int, Rec]) -> None:
        oid = self._id_fd(fd)
        entries = self._entries(fd, note=False)
        out[oid] = self._rec(os.fstat(fd), entries)
        for name, entry in entries.items():
            if isinstance(entry, Link) or entry in out:
                continue
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                with _opened(name, _DIR_FLAGS, fd) as child:
                    self._capture_dir(child, out)
            else:
                with _opened(name, _FILE_FLAGS, fd) as child:
                    out[entry] = self._rec(os.fstat(child), _read_fd(child))

    def _durable_image(self) -> dict[int, Rec]:
        """What an OS crash keeps (R4): durable records reachable from the root."""
        out: dict[int, Rec] = {}
        todo = [self.root_id]
        while todo:
            oid = todo.pop()
            if oid in out:
                continue
            rec = self.durable.get(oid) or self.born.get(oid)
            if rec is None:
                raise ModelError(f"object {oid}: durable name, no record")
            out[oid] = rec
            if rec.kind == "dir":
                todo += [e for e in rec.entries().values() if isinstance(e, int)]
        return out

    def _materialize(self, image: dict[int, Rec]) -> dict[int, Rec]:
        """Make the files under the root exactly `image`; returns it as made
        (a directory reached twice is a copy with a new id, R5)."""
        self._forget_tree()
        for name in os.listdir(self.root):
            _wipe(self.root / name)
        made: dict[int, Rec] = {}
        files: dict[int, Path] = {}
        self._make(self.root_id, self.root, image, made, files, top=True)
        return made

    def _make(
        self,
        oid: int,
        path: Path,
        image: dict[int, Rec],
        made: dict[int, Rec],
        files: dict[int, Path],
        *,
        top: bool = False,
    ) -> int:
        rec = image[oid]
        if rec.kind == "file":
            if oid in files:
                _LINK(files[oid], path)
            else:
                fd = _OPEN(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, rec.data)  # type: ignore[arg-type]
                    _FCHMOD(fd, rec.mode)
                finally:
                    os.close(fd)
                files[oid] = path
            self._adopt(path, oid, rec, made)
            return oid
        if oid in made:  # R5: the same directory durable twice
            copy = self._new_id()
            self.origin[copy] = self.origin.get(oid, oid)
            oid = copy
        if not top:
            _MKDIR(path, 0o700)
        entries: dict[str, Entry] = {}
        for name, entry in rec.entries().items():
            if isinstance(entry, Link):
                _SYMLINK(entry.target, path / name)
                entries[name] = entry
            else:
                entries[name] = self._make(entry, path / name, image, made, files)
        _CHMOD(path, rec.mode)
        self._adopt(path, oid, replace(rec, data=entries), made)
        return oid

    def _adopt(self, path: Path, oid: int, rec: Rec, made: dict[int, Rec]) -> None:
        made[oid] = rec
        flags = _DIR_FLAGS if rec.kind == "dir" else _FILE_FLAGS
        fd = _OPEN(path, flags)
        key = _key(os.fstat(fd))
        if key in self._live:
            os.close(fd)  # a hard link of a file already adopted
            return
        self._live[key] = oid
        self._pins[key] = fd
        self.fake.owners[key] = rec.owner

    def _forget_tree(self) -> None:
        """Before rebuilding: no pin, no live id, no virtual owner survives."""
        for dirpath, dirs, files in os.walk(self.root):
            for name in [*dirs, *files]:
                st = os.stat(Path(dirpath) / name, follow_symlinks=False)
                self.fake.owners.pop(_key(st), None)
        self.close()


# ---- helpers ------------------------------------------------------------------


def _key(st: os.stat_result) -> Key:
    return (st.st_dev, st.st_ino)


def _kind(st: os.stat_result) -> Kind | None:
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return None


@contextmanager
def _opened(path: object, flags: int, dir_fd: int | None = None) -> Iterator[int]:
    try:
        fd = _OPEN(path, flags, dir_fd=dir_fd)  # type: ignore[arg-type]
    except PermissionError as exc:
        raise ModelError(f"L2: {path}: {exc}") from exc
    try:
        yield fd
    finally:
        os.close(fd)


def _listdir(fd: int) -> list[str]:
    try:
        return os.listdir(fd)
    except OSError:
        pass
    with _opened(fd_path(fd), _DIR_FLAGS) as again:  # a search-only descriptor
        return os.listdir(again)


def _read_fd(fd: int) -> bytes:
    """A file's bytes, also through a write-only descriptor (its path)."""
    try:
        return _pread_all(fd)
    except OSError:
        pass
    key = _key(os.fstat(fd))
    with _opened(fd_path(fd), _FILE_FLAGS) as again:
        if _key(os.fstat(again)) != key:
            raise ModelError(f"{fd_path(fd)}: replaced while being read")
        return _pread_all(again)


def _pread_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while chunk := os.pread(fd, 1 << 20, offset):
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _lexists(path: object, dir_fd: int | None) -> bool:
    try:
        os.stat(path, dir_fd=dir_fd, follow_symlinks=False)  # type: ignore[arg-type]
    except OSError:
        return False
    return True


def _raise(exc: OSError) -> None:
    raise exc


def _wipe(path: Path) -> None:
    """Remove `path` with the real functions (no fake sees it)."""
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        _UNLINK(path)
        return
    _CHMOD(path, 0o700)
    for name in os.listdir(path):
        _wipe(path / name)
    _RMDIR(path)


def _flatten(image: dict[int, Rec], root_id: int) -> dict[str, object]:
    """Root-relative path -> what is there, for comparisons."""
    out: dict[str, object] = {}

    def walk(oid: int, rel: str) -> None:
        rec = image[oid]
        if rec.kind == "file":
            out[rel] = ("file", rec.mode, rec.owner, rec.data)
            return
        out[rel] = ("dir", rec.mode, rec.owner)
        for name, entry in rec.entries().items():
            sub = f"{rel}/{name}" if rel else name
            if isinstance(entry, Link):
                out[sub] = ("link", entry.target)
            else:
                walk(entry, sub)

    walk(root_id, "")
    return out
