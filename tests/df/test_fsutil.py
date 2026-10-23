"""Tests for durable-write primitives."""

from __future__ import annotations

import ast
import errno
import os
import stat
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from df import fsutil
from df.fsutil import (
    SyncNote,
    boot_id,
    remove_durably,
    sync_dir,
    sync_tree,
    write_atomic,
)
from tests.df.conftest import FakeLayout
from tests.df.crash_model import CrashModel


def test_write_atomic_syncs_file_then_dir(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    real_sync_file = fsutil.sync_file
    real_rename = os.rename

    def spy_sync_file(fd: int) -> None:
        calls.append("sync_file")
        real_sync_file(fd)

    def spy_rename(src, dst) -> None:  # type: ignore[no-untyped-def]
        calls.append("rename")
        real_rename(src, dst)

    monkeypatch.setattr(fsutil, "sync_file", spy_sync_file)
    monkeypatch.setattr(fsutil.os, "rename", spy_rename)
    monkeypatch.setattr(
        fsutil, "fault_point", lambda kind, path: calls.append(f"fault:{kind}")
    )
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda d: calls.append("sync_dir") or SyncNote.OK
    )
    target = tmp_path / "f.toml"
    write_atomic(target, "a = 1\n")
    assert calls == ["sync_file", "rename", "fault:renamed", "sync_dir"]
    assert target.read_text() == "a = 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f.toml"]


def test_write_atomic_mode(tmp_path: Path) -> None:
    target = tmp_path / "f"
    write_atomic(target, "x", mode=0o600)
    assert target.stat().st_mode & 0o777 == 0o600


def test_fault_point_sees_visible_unsynced_file(monkeypatch, tmp_path: Path) -> None:
    synced: list[object] = []

    def boom(kind: str, path: Path) -> None:
        assert kind == "renamed"
        assert path.read_text() == "new"  # already visible
        assert synced == []  # parent not yet synced
        raise RuntimeError("crash")

    monkeypatch.setattr(fsutil, "fault_point", boom)
    monkeypatch.setattr(fsutil, "sync_dir", lambda d: synced.append(d))
    with pytest.raises(RuntimeError):
        write_atomic(tmp_path / "f", "new")
    assert (tmp_path / "f").read_text() == "new"


def test_sync_error_propagates(monkeypatch, tmp_path: Path) -> None:
    def eio(fd: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(fsutil.os, "fsync", eio)
    monkeypatch.setattr(fsutil, "_IS_DARWIN", False)
    with pytest.raises(OSError) as exc:
        write_atomic(tmp_path / "f", "x")
    assert exc.value.errno == errno.EIO
    assert not (tmp_path / "f").exists()
    assert list(tmp_path.iterdir()) == []


def test_write_atomic_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "f"
    write_atomic(target, "one")
    write_atomic(target, "two")
    assert target.read_text() == "two"


def test_remove_durably(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    target = tmp_path / "f"
    target.write_text("x")
    monkeypatch.setattr(
        fsutil,
        "fault_point",
        lambda kind, path: calls.append(f"fault:{kind}:{path.exists()}"),
    )
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda d: calls.append("sync_dir") or SyncNote.OK
    )
    remove_durably(target)
    assert calls == ["fault:unlinked:False", "sync_dir"]


def test_remove_durably_missing_is_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        remove_durably(tmp_path / "absent")


def test_dir_fullfsync_unsupported_is_reported(monkeypatch, tmp_path: Path) -> None:
    def unsupported(fd: int, cmd: int) -> None:
        raise OSError(errno.ENOTSUP, "nope")

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil, "_F_FULLFSYNC", 51)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", unsupported)
    monkeypatch.setattr(fsutil, "_SYNC_NOTES", set())
    assert sync_dir(tmp_path) is SyncNote.FULLFSYNC_UNSUPPORTED


def test_unsupported_fullfsync_is_collected_by_device(
    monkeypatch, tmp_path: Path
) -> None:
    """Decision (a): callers drop the SyncNote, the collector keeps it (F10)."""

    def unsupported(fd: int, cmd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.ENOTSUP, "nope")

    monkeypatch.setattr(fsutil, "_SYNC_NOTES", set())
    assert fsutil.sync_notes() == frozenset()
    sync_dir(tmp_path)  # supported (or Linux): nothing collected
    assert fsutil.sync_notes() == frozenset()
    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", unsupported)
    write_atomic(tmp_path / "f", "x")  # a caller that drops the note
    assert fsutil.sync_notes() == frozenset({os.stat(tmp_path).st_dev})
    fsutil.reset_sync_notes()
    assert fsutil.sync_notes() == frozenset()


def test_dir_fullfsync_other_error_raises(monkeypatch, tmp_path: Path) -> None:
    def broken(fd: int, cmd: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil, "_F_FULLFSYNC", 51)
    monkeypatch.setattr(fsutil.fcntl, "fcntl", broken)
    with pytest.raises(OSError):
        sync_dir(tmp_path)


def test_sync_dir_accepts_fd_and_path(tmp_path: Path) -> None:
    assert sync_dir(tmp_path) in set(SyncNote)
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        assert sync_dir(fd) in set(SyncNote)
    finally:
        os.close(fd)


def test_sync_tree_does_not_follow_links(monkeypatch, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a").write_text("a")
    (root / "link").symlink_to(outside)
    (root / "flink").symlink_to(outside / "secret")
    seen: list[str] = []
    monkeypatch.setattr(fsutil, "sync_file", lambda fd: seen.append("f"))
    monkeypatch.setattr(fsutil, "sync_dir", lambda d: seen.append("d") or SyncNote.OK)
    sync_tree(root)
    assert seen == ["f", "d", "d"]  # sub/a, sub, root (bottom-up)


def test_boot_id_nonempty() -> None:
    assert boot_id()


def test_boot_id_empty_is_error(monkeypatch) -> None:
    class R:
        stdout = "\n"
        returncode = 0

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(OSError):
        boot_id()


def test_sync_tree_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        sync_tree(tmp_path / "absent")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores chmod")
def test_sync_tree_unreadable_subdir_raises(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a").write_text("a")
    sub.chmod(0)
    try:
        with pytest.raises(OSError):
            sync_tree(tmp_path)
    finally:
        sub.chmod(0o755)


def test_boot_id_uses_absolute_sysctl_and_minimal_env(monkeypatch) -> None:
    seen: dict = {}

    class R:
        stdout = "uuid-1\n"

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen["argv"], seen["env"] = argv, kwargs.get("env")
        return R()

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", fake_run)
    assert boot_id() == "uuid-1"
    assert seen["argv"][0] == "/usr/sbin/sysctl"
    assert seen["env"] == {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def test_boot_id_sysctl_failure_is_oserror(monkeypatch) -> None:
    def fail(argv, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["timeout"] == 10
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(fsutil, "_IS_DARWIN", True)
    monkeypatch.setattr(fsutil.subprocess, "run", fail)
    with pytest.raises(OSError):
        boot_id()


def test_sync_tree_does_not_hang_on_fifo(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    sync_tree(tmp_path)  # fifo is skipped, must return


# ---------- make_dir: a created directory is durable with its mode ----------


@pytest.fixture()
def crash_model(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> Iterator[CrashModel]:
    model = CrashModel(fake_layout.layout.root, fake_layout)
    model.install(monkeypatch)
    yield model
    model.close()


@pytest.mark.parametrize("umask", [0o022, 0o077, 0o277])
def test_make_dir_mode_and_name_are_durable(
    crash_model: CrashModel, umask: int
) -> None:
    """Born at exactly `mode` whatever the umask, and durable on return: an
    OS crash right after `make_dir` keeps the name and the mode."""
    root = crash_model.root
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    old = os.umask(umask)
    try:
        assert fsutil.make_dir(fd, "d", 0o711, path=root / "d") is True
    finally:
        os.umask(old)
        os.close(fd)
    assert crash_model.unsynced() == []
    crash_model.os_crash()
    assert stat.S_IMODE(os.stat(root / "d").st_mode) == 0o711


@pytest.mark.parametrize(
    "case,have,want,after",
    [
        ("narrower-own", 0o700, 0o711, 0o711),  # widened to the mode
        ("equal", 0o711, 0o711, 0o711),
        ("wider", 0o755, 0o700, 0o755),  # never narrowed: the caller refuses
        ("foreign", 0o700, 0o711, 0o700),  # another owner: never touched
        ("no-repair", 0o700, 0o755, 0o700),  # a system directory
    ],
)
def test_make_dir_settles_a_found_name(
    crash_model: CrashModel,
    fake_layout: FakeLayout,
    case: str,
    have: int,
    want: int,
    after: int,
) -> None:
    """Settle what is seen (spec 3.3 r6): a found directory is synced itself
    and in its parent before `make_dir` returns; only the current user's
    narrower mode is widened, durably."""
    from tests.df.conftest import BUILD_UID

    root = crash_model.root
    d = root / "d"
    os.mkdir(d, have)
    os.chmod(d, have)
    if case == "foreign":
        fake_layout.set_owner(d, BUILD_UID)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        created = fsutil.make_dir(fd, "d", want, path=d, repair=case != "no-repair")
    finally:
        os.close(fd)
    assert created is False
    assert crash_model.unsynced() == []
    crash_model.os_crash()
    assert stat.S_IMODE(os.stat(d).st_mode) == after


def test_create_file_is_durable_in_final_form(crash_model: CrashModel) -> None:
    """Born at exactly `mode` whatever the umask; file and name durable on
    return; an existing name is `FileExistsError`."""
    path = crash_model.root / "f"
    old = os.umask(0o277)
    try:
        fd = fsutil.create_file(path, os.O_WRONLY, 0o600)
    finally:
        os.umask(old)
    os.close(fd)
    assert crash_model.unsynced() == []
    crash_model.os_crash()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with pytest.raises(FileExistsError):
        fsutil.create_file(path, os.O_WRONLY, 0o600)


#: Every call site in `df/` that creates an object, changes its metadata,
#: renames or removes it (the class of the 9-C findings: an object not
#: durable in its final form, or a found one used unsettled), keyed by
#: (module, function, operation, ordinal), with the reason its result is
#: durable in final form. A new site fails `test_every_fs_mutation_is_
#: accounted_for` until it goes through an `fsutil` primitive or names its
#: protocol here.
PRIMITIVE = "an fsutil primitive (durable in final form on return)"
FS_SITES: dict[tuple[str, str, str, int], str] = {
    ("fsutil", "write_atomic", "os.rename", 1): PRIMITIVE,
    ("fsutil", "_write_tmp", "os.fchmod", 1): PRIMITIVE + ": temp file, sync_file",
    ("fsutil", "_discard", "os.unlink", 1): PRIMITIVE + ": its own temp file",
    ("fsutil", "make_dir", "os.mkdir", 1): PRIMITIVE,
    ("fsutil", "_settle_found", "os.fchmod", 1): PRIMITIVE + ": then own sync",
    ("fsutil", "create_file", "os.open", 1): PRIMITIVE,
    ("fsutil", "remove_durably", "os.unlink", 1): PRIMITIVE,
    ("launcher_entry", "_create_private", "os.open", 1): (
        "stdlib copy of create_file: born 0600 (umask 0), file and dir synced"
    ),
    ("admin", "install_admin_python", "os.rename", 1): (
        "protocol install_admin_python: sync_tree before, sync_dir after"
    ),
    ("admin", "_make_dir", "os.mkdir", 1): "protocol install_admin: sync_tree",
    ("admin", "_make_dir", "os.fchmod", 1): "protocol install_admin: sync_tree",
    ("admin", "_copy_file", "os.open", 1): "protocol install_admin: sync_tree",
    ("admin", "_copy_file", "os.fchmod", 1): "protocol install_admin: sync_tree",
    ("admin", "link_command", "os.symlink", 1): "protocol link_command: temp",
    ("admin", "link_command", "os.rename", 1): (
        "protocol link_command: sync_dir(local_bin)"
    ),
    ("admin", "_unlink_absent_ok", "os.unlink", 1): "link_command's own temp",
    ("admin", "_unlink_quietly", "os.unlink", 1): "link_command's own temp",
    ("inputs", "fetch_reference", ".mkdir", 1): "root's checkout: scratch, refetched",
    ("inputs", "fetch_reference", ".mkdir", 2): "root's checkout: scratch, refetched",
    ("inputs", "_remove", "shutil.rmtree", 1): "root's checkout: scratch",
    ("inputs", "_remove", ".unlink", 1): "root's checkout: scratch",
    ("inputs", "_unpack", ".extractall", 1): "root's checkout: scratch",
    ("leaves", "prepare_dir", "os.mkdir", 1): (
        "protocol prepare_dir: a crash before fchown is row unknown (cleanup)"
    ),
    ("leaves", "prepare_dir", "os.fchmod", 1): "protocol prepare_dir: fsync(fd)",
    ("leaves", "prepare_dir", "os.fchown", 1): "protocol prepare_dir: fsync(fd)",
    ("leaves", "verify_chmod", "os.fchmod", 1): (
        "protocol verify_chmod: sync_dir(itself)"
    ),
    ("leaves", "seal", "os.mkdir", 1): "protocol seal: sync_dir(staging/<id>)",
    ("leaves", "seal", "os.fchmod", 1): "protocol seal: sync_dir(staging/<id>)",
    ("leaves", "seal", "os.rename", 1): "protocol seal: both parents synced",
    ("platform", "_make_home", "os.mkdir", 1): "reserved name; own sync, rename",
    ("platform", "_make_home", "os.fchown", 1): "reserved name; own sync, rename",
    ("platform", "_make_home", "os.fchmod", 1): "reserved name; own sync, rename",
    ("platform", "_remove_reserved", "os.rmdir", 1): (
        "idempotent: a retry removes it again before _make_home syncs the parent"
    ),
    ("platform", "_cleanup_at", "os.unlink", 1): "protocol cleanup_tree: link",
    ("platform", "_rmdir_empty", "os.rmdir", 1): "protocol cleanup_tree: tree",
    ("platform", "_quarantine", "os.rename", 1): (
        "protocol cleanup_tree: quarantine, then parent synced"
    ),
    ("receive", "_receive", "os.mkdir", 1): "protocol accept_copy: sync_tree",
    ("receive", "_receive", "os.fchmod", 1): "protocol accept_copy: sync_tree",
    ("receive", "_create", "os.mkdir", 1): "protocol accept_copy: sync_tree",
    ("receive", "_symlink", "os.symlink", 1): "protocol accept_copy: sync_tree",
    ("receive", "_regular", "os.open", 1): "protocol accept_copy: sync_tree",
    ("receive", "_regular", "os.fchmod", 1): "protocol accept_copy: sync_tree",
    ("receive", "_hardlink", "os.open", 1): "protocol accept_copy: sync_tree",
    ("receive", "_hardlink", "os.fchmod", 1): "protocol accept_copy: sync_tree",
    ("receive", "open_dirs", "os.fchmod", 1): "protocol accept_copy: sync_tree",
    ("receive", "_rmtree_at", "shutil.rmtree", 1): (
        "protocol remove_own_tree: sync_dir(parent)"
    ),
    ("receive", "_rmdir_at", "os.rmdir", 1): (
        "protocol remove_empty_dir: sync_dir(parent)"
    ),
    ("release", "_switch_current", "os.symlink", 1): "protocol activate: temp",
    ("release", "_switch_current", "os.rename", 1): "protocol activate: sync_dir",
    ("release", "_unlink_absent_ok", "os.unlink", 1): "activate's own temp",
    ("release", "_unlink_quietly", "os.unlink", 1): "activate's own temp",
}

_OS_OPS = frozenset(
    {
        "mkdir", "makedirs", "chmod", "fchmod", "lchmod", "chown", "fchown",
        "lchown", "rename", "replace", "symlink", "link", "unlink", "remove",
        "rmdir", "open",
    }
)  # fmt: skip
_PATH_OPS = frozenset(
    {
        "mkdir", "write_text", "write_bytes", "touch", "symlink_to", "rename",
        "replace", "unlink", "rmdir", "chmod", "extractall", "hardlink_to",
    }
)  # fmt: skip


class _FsSites(ast.NodeVisitor):
    """Every mutating call: `os.*`, `os.open` with `O_CREAT`, `shutil.*`,
    the `pathlib`/`tarfile` mutators, and `open(..., "w"/"x"/"a"/"+")`."""

    def __init__(self, module: str, tree: ast.Module) -> None:
        self.module = module
        self.creating = {
            t.id
            for n in tree.body
            if isinstance(n, ast.Assign) and "O_CREAT" in ast.unparse(n.value)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        self.stack: list[str] = []
        self.found: list[tuple[str, str, str, int]] = []
        self._ordinal: dict[tuple[str, str, str], int] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _add(self, op: str) -> None:
        key = (self.module, self.stack[-1] if self.stack else "", op)
        self._ordinal[key] = self._ordinal.get(key, 0) + 1
        self.found.append((*key, self._ordinal[key]))

    def _creates(self, flags: list[ast.expr]) -> bool:
        names = {n.id for f in flags for n in ast.walk(f) if isinstance(n, ast.Name)}
        text = " ".join(ast.unparse(f) for f in flags)
        return "O_CREAT" in text or bool(names & self.creating)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        f = node.func
        if isinstance(f, ast.Attribute):
            base = ast.unparse(f.value)
            if base == "os" and f.attr in _OS_OPS:
                if f.attr != "open" or self._creates(node.args[1:2]):
                    self._add(f"os.{f.attr}")
            elif base == "shutil":
                self._add(f"shutil.{f.attr}")
            elif f.attr in _PATH_OPS:
                self._add(f".{f.attr}")
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = node.args[1] if len(node.args) > 1 else None
            mode = mode or next(
                (k.value for k in node.keywords if k.arg == "mode"), None
            )
            if mode is not None and any(c in ast.unparse(mode) for c in "wxa+"):
                self._add("open")
        self.generic_visit(node)


def test_every_fs_mutation_is_accounted_for() -> None:
    found: list[tuple[str, str, str, int]] = []
    for path in sorted(Path(fsutil.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text())
        visitor = _FsSites(path.stem, tree)
        visitor.visit(tree)
        found += visitor.found
    assert sorted(found) == sorted(FS_SITES)


def test_the_guard_sees_every_form() -> None:
    """The scanner itself (a twin): each form it must catch is caught."""
    src = """
import os, shutil
from pathlib import Path
FLAGS = os.O_WRONLY | os.O_CREAT
def f(p: Path) -> None:
    os.mkdir("a"); os.chmod("a", 0o700); os.open("a", FLAGS)
    os.open("b", os.O_RDONLY | os.O_CREAT, 0o600); os.open("c", os.O_RDONLY)
    shutil.copy("a", "b"); p.write_text("x"); p.touch(); open("x", "w")
    open("y"); os.replace("a", "b"); p.chmod(0o700)
"""
    visitor = _FsSites("m", ast.parse(src))
    visitor.visit(ast.parse(src))
    ops = sorted(op for _m, _f, op, _n in visitor.found)
    assert ops == sorted(
        [
            "os.mkdir", "os.chmod", "os.open", "os.open", "shutil.copy",
            ".write_text", ".touch", "open", "os.replace", ".chmod",
        ]
    )  # fmt: skip


def _primitive_runs(
    model: CrashModel,
    monkeypatch: pytest.MonkeyPatch,
    run: Callable[[], None],
    check: Callable[..., None],
) -> int:
    """Crash a primitive at each position -- after each of its syscalls and
    at each model point -- by a process and by an OS crash; then no visible
    name is at a non-final mode, and a rerun leaves it final and durable.
    Returns the number of runs (asserted by the callers, R49)."""
    import sys

    from tests.df.crash_model import Crash

    count = [0]
    target: list[int | None] = [None]

    def wrap(name: str) -> Callable[..., object]:
        real = getattr(os, name)

        def call(*a: object, **kw: object) -> object:
            out = real(*a, **kw)
            caller = str(sys._getframe(1).f_globals.get("__name__"))  # noqa: SLF001
            if caller == "df.fsutil":
                count[0] += 1
                if count[0] == target[0]:
                    target[0] = None
                    model.kill_now(f"after {name}")
            return out

        return call

    for name in ("mkdir", "open", "fchmod", "close"):
        monkeypatch.setattr(os, name, wrap(name))
    start = model.snapshot()
    first = len(model.points)
    run()
    points, calls = len(model.points) - first, count[0]
    runs = 0
    for crash in ("process", "os"):
        for kind, k in [("point", i) for i in range(points)] + [
            ("syscall", n) for n in range(1, calls + 1)
        ]:
            model.restore(start)
            model.disarm()
            count[0] = 0
            if kind == "point":
                model.trap(lambda p, i=first + k: p.index == i)
            else:
                target[0] = k
            with pytest.raises(Crash):
                run()
            model.reap()
            if crash == "os":
                model.os_crash()
            check(final=False)
            run()
            check(final=True)
            assert model.unsynced() == [], (crash, kind, k)
            runs += 1
    return runs


def test_make_dir_twins_every_position(
    crash_model: CrashModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = crash_model.root
    d = root / "d"

    def run() -> None:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        old = os.umask(0o277)
        try:
            fsutil.make_dir(fd, "d", 0o711, path=d)
        finally:
            os.umask(old)
            os.close(fd)

    def check(*, final: bool) -> None:
        if os.path.lexists(d) or final:
            assert stat.S_IMODE(os.stat(d).st_mode) == 0o711

    assert _primitive_runs(crash_model, monkeypatch, run, check) >= 8


def test_create_file_twins_every_position(
    crash_model: CrashModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = crash_model.root / "f"

    def run() -> None:
        old = os.umask(0o277)
        try:
            fd = fsutil.create_file(path, os.O_WRONLY, 0o600)
        except FileExistsError:
            return  # the caller opens and judges an existing one
        finally:
            os.umask(old)
        os.close(fd)

    def check(*, final: bool) -> None:
        if os.path.lexists(path) or final:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    assert _primitive_runs(crash_model, monkeypatch, run, check) >= 6
