"""Tests for durable-write primitives."""

from __future__ import annotations

import ast
import errno
import os
import stat
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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
        ("setgid-own", 0o2711, 0o711, 0o711),  # Linux-inherited S_ISGID cleared
        ("setgid-wider", 0o2755, 0o711, 0o2755),  # S_ISGID and wider: untouched
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


#: Every finding of the scan of `df/` (`_FsSites`: a use that may create an
#: object, change its metadata, rename or remove it, unless its flags or
#: mode resolve to a read) -- the class of the 9-C findings: an object not
#: durable in its final form, or a found one used unsettled -- keyed by
#: (module, function, operation, ordinal), with the reason it is safe. A new
#: finding fails `test_every_fs_mutation_is_accounted_for` until it goes
#: through an `fsutil` primitive or names its protocol here. What the scan
#: does not see is listed in `_FsSites`' docstring; processes `df` starts
#: are pinned separately (`PROCESS_SITES`, `SUBPROCESS_CREATORS`).
PRIMITIVE = "an fsutil primitive (durable in final form on return)"
FS_SITES: dict[tuple[str, str, str, int], str] = {
    ("fsutil", "write_atomic", "os.rename", 1): PRIMITIVE,
    ("fsutil", "_write_tmp", "os.fchmod", 1): PRIMITIVE + ": temp file, sync_file",
    ("fsutil", "_discard", "os.unlink", 1): PRIMITIVE + ": its own temp file",
    ("fsutil", "make_dir", "os.mkdir", 1): PRIMITIVE,
    ("fsutil", "make_dir", "os.fchmod", 1): PRIMITIVE + ": inherited S_ISGID",
    ("fsutil", "_settle_found", "os.fchmod", 1): PRIMITIVE + ": then own sync",
    ("fsutil", "create_file", "os.open", 1): PRIMITIVE,
    ("fsutil", "remove_durably", "os.unlink", 1): PRIMITIVE,
    ("fsutil", "_write_tmp", "os.open", 1): (
        PRIMITIVE + ": write_atomic's O_EXCL temp, fchmod, sync_file, rename"
    ),
    ("journal", "_open_regular", "os.open no-create", 1): (
        "the callers in its own module pass flags without O_CREAT/O_TRUNC "
        "(a call from another module is not seen): the lock "
        "(never written), the journal (append, then sync_file) or a read"
    ),
    ("launcher_entry", "_open_regular", "os.open no-create", 1): (
        "the callers in its own module pass flags without O_CREAT/O_TRUNC "
        "(a call from another module is not seen): the lock "
        "(never written) or a read"
    ),
    ("inputs", "", "import shutil", 1): "root's checkout: scratch",
    ("receive", "", "import shutil", 1): "protocol remove_own_tree: rmtree",
    ("acl", "_libsystem", "ctypes.CDLL", 1): "ACL reads only: acl_get_*_np, acl_free",
    ("platform", "_rename_function", "ctypes.CDLL", 1): (
        "rename_noreplace (renameatx_np/renameat2) of the build home: "
        "reserved name, own sync, parent synced (protocol create_build_user)"
    ),
    ("platform", "_shared_cache_function", "ctypes.CDLL", 1): (
        "a read: _dyld_shared_cache_contains_path"
    ),
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
    ("sudoers", "_place", "os.rename", 1): (
        "protocol install: temp born 0440 by create_file, sync_file, visudo, "
        "rename, sync_dir(parent)"
    ),
    ("sudoers", "_discard", "os.unlink", 1): (
        "install's own temp: a dotted name sudo's includedir never reads"
    ),
    ("sudoers", "", "import tempfile", 1): "validate's scratch for visudo",
    ("sudoers", "validate", "tempfile.mkstemp", 1): (
        "validate's scratch for visudo, removed"
    ),
    ("sudoers", "validate", "open", 1): "validate's scratch for visudo",
    ("sudoers", "validate", "os.unlink", 1): "validate's scratch for visudo",
}

#: Attribute calls that create, change the metadata of, rename or remove an
#: object, whatever the object (`os.<x>`, a `Path`, a `TarFile`); `os.<x>`
#: of these is a finding as a reference too (`map(os.unlink, ...)`).
_MUTATORS = frozenset(
    {
        "mkdir", "makedirs", "chmod", "fchmod", "lchmod", "chown", "fchown",
        "lchown", "rename", "replace", "symlink", "link", "unlink", "remove",
        "rmdir", "removedirs", "mkfifo", "mknod", "truncate", "ftruncate",
        "utime", "chflags", "lchflags", "write_text", "write_bytes", "touch",
        "symlink_to", "hardlink_to", "extractall", "extract", "setxattr",
        "removexattr",
    }
)  # fmt: skip
#: `Path` (3.14) copies and moves: findings only with an argument, so that
#: `dict.copy()` is none (`copy.copy(x)` of the module is none either).
_TARGET_MUTATORS = frozenset({"copy", "copy_into", "move", "move_into"})
#: `os.open` flags that only read or open a directory.
_READ_FLAGS = frozenset(
    {"O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK", "O_NOCTTY"}
)
#: `os.open` flags that open an existing file for writing, never create or
#: truncate it: `os.open no-create`, a finding of its own.
_NO_CREATE_FLAGS = _READ_FLAGS | {"O_RDWR", "O_WRONLY", "O_APPEND"}
#: Modules whose every use is a finding (they create or remove files); `os`
#: and `ctypes` by their attributes.
_FS_MODULES = frozenset({"os", "shutil", "tempfile", "ctypes"})
#: `ctypes` loaders: each reaches C code the scan cannot see.
_C_LOADERS = frozenset({"CDLL", "PyDLL", "cdll", "pydll", "LibraryLoader"})
#: Process creators: each start site is in `PROCESS_SITES`.
_OS_PROCESSES = frozenset(
    {"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp"}
    | {f"spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
    | {f"exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
)
_SUBPROCESS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput"}
    | {"getstatusoutput"}
)

#: Processes `df` starts that create objects -- outside the AST scan, so
#: listed here with why their result is safe (the guard's limit).
SUBPROCESS_CREATORS = {
    "uv python install (admin)": "protocol install_admin_python: sync_tree, rename",
    "uv sync / git / tar (build, as df-build)": "builder tree: copied, digested",
    "git / tar (root's checkout)": "scratch under verify/, fetched every run",
    "cleaner find/chmod/rm (as df-build)": "protocol cleanup_tree: root syncs",
    "useradd / dscl (account)": "OS records, judged by verify_build_user",
    "probes: ps / pkill / sudo -l / getent / dscl -read / xcrun": (
        "create nothing: reads, or a kill of df-build's processes"
    ),
    "tar -cf - (copy producer, as df-build)": (
        "creates nothing: its stdout is the stream accept_copy receives"
    ),
    "sysctl (boot id)": "creates nothing: a read",
}
#: Every site that starts a process (`os.system`/`posix_spawn*`/`spawn*`/
#: `exec*`/`popen`/`fork`, `subprocess.*`), keyed like `FS_SITES`, with the
#: `SUBPROCESS_CREATORS` entries it may start: a new site fails
#: `test_every_process_start_is_named` until it is named there.
PROCESS_SITES: dict[tuple[str, str, str, int], tuple[str, ...]] = {
    ("fsutil", "_sysctl_boot_id", "subprocess.run", 1): ("sysctl (boot id)",),
    # run_command (root) and run_as (df-build)
    ("platform", "_execute", "subprocess.Popen", 1): (
        "uv python install (admin)",
        "uv sync / git / tar (build, as df-build)",
        "git / tar (root's checkout)",
        "cleaner find/chmod/rm (as df-build)",
        "useradd / dscl (account)",
        "probes: ps / pkill / sudo -l / getent / dscl -read / xcrun",
    ),
    ("platform", "spawn_as", "subprocess.Popen", 1): (
        "tar -cf - (copy producer, as df-build)",
    ),
}

Site = tuple[str, str, str, int]
Flags = frozenset[str]


@dataclass
class _Scope:
    """A function (or lambda) scope: its parameters and how often each name
    is bound in it; `values` holds a name's only binding's value."""

    name: str
    parent: _Scope | None
    params: list[str] = field(default_factory=list)
    kwonly: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    values: dict[str, ast.expr] = field(default_factory=dict)

    def binds(self, name: str) -> bool:
        return name in self.counts or name in self.params or name in self.kwonly


class _FsSites(ast.NodeVisitor):
    """The scan of one module: every use that may create, change metadata,
    rename or remove -- unless it resolves to a read.

    Findings: `<os>.<mutator>` and `.<mutator>` attribute calls (`copy`,
    `move`, ... with an argument); a reference `os.<mutator>` that is not
    called; every `os.open` whose flags do not resolve to read/directory
    flags -- those resolving to no-create flags are `os.open no-create`;
    `open`/`.open` with a mode that is not a literal read (the mode is
    `args[1]` for `<module>.open`, `args[0]` for a value's `.open`); any
    `shutil`/`tempfile` import or use; a `ctypes` loader (`CDLL`, ...);
    `from os|shutil|tempfile|ctypes import`; `getattr`/`vars` on those
    modules and their `__dict__`; a call of a name imported from them.
    Aliases by `import os as x` and by assignment `x = os` are tracked. Process starts are `processes`.

    Flags resolve through: literal `os.O_*` and `|`/conditional
    expressions; a name bound exactly once -- in its function (a local
    shadows the module), else at module level; a parameter -- through every
    call of its function in the same module, if the function is never used
    there otherwise. Calls from other modules (`journal._open_regular(...)`
    in `cli`, `from df.journal import _open_regular`) are not seen: such a
    parameter is judged by its own module's callers only. Anything else
    does not resolve and is a finding.

    Not seen (the guard's limit): code outside `df/*.py` (the stdlib, the C
    functions a `ctypes` loader reaches -- the loader is a finding), other
    file classes' constructors (`io.FileIO`, `zipfile.ZipFile`,
    `sqlite3.connect`, ...), dynamic code (`eval`, `exec`, `importlib`, a
    non-literal `getattr` on any other object), a mutator reached through
    an object of another module (its own module is scanned), a resolved
    parameter's callers in other modules, and what started processes do
    (`SUBPROCESS_CREATORS`).
    """

    def __init__(self, module: str, tree: ast.Module) -> None:
        self.module = module
        self.aliases: dict[str, str] = {}  # local name -> fs module
        self.subprocess: set[str] = set()  # local names of `subprocess`
        self.modules: set[str] = set()  # every imported module name
        self.imported: set[str] = set()  # names from `from os import ...`
        self.module_scope = _Scope("", None)
        _bindings(self.module_scope, tree.body)
        self.scope = self.module_scope
        self.stack: list[str] = []
        self.calls: dict[str, list[tuple[ast.Call, _Scope]]] = {}
        self.loads: dict[str, int] = {}
        self.called: set[int] = set()  # ids of `Call.func` nodes
        self.events: list[tuple[str, str | tuple[ast.expr, _Scope]]] = []
        self.processes: list[Site] = []
        self._proc_ordinal: dict[tuple[str, str, str], int] = {}

    # ---- results ----

    @property
    def found(self) -> list[Site]:
        """The findings in source order, ordinals per (function, op)."""
        ordinal: dict[tuple[str, str, str], int] = {}
        out: list[Site] = []
        for function, what in self.events:
            op = what if isinstance(what, str) else self._open_op(*what)
            if op is None:
                continue
            key = (self.module, function, op)
            ordinal[key] = ordinal.get(key, 0) + 1
            out.append((*key, ordinal[key]))
        return out

    def _open_op(self, flags: ast.expr, scope: _Scope) -> str | None:
        have = self._flags(flags, scope, set())
        if have is not None and have <= _READ_FLAGS:
            return None
        if have is not None and have <= _NO_CREATE_FLAGS:
            return "os.open no-create"
        return "os.open"

    def _flags(self, expr: ast.expr, scope: _Scope, seen: set[object]) -> Flags | None:
        """The `os.O_*` names `expr` may evaluate to, else `None`."""
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return _union(
                self._flags(expr.left, scope, seen),
                self._flags(expr.right, scope, seen),
            )
        if isinstance(expr, ast.IfExp):
            return _union(
                self._flags(expr.body, scope, seen),
                self._flags(expr.orelse, scope, seen),
            )
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            if self.aliases.get(expr.value.id) == "os" and expr.attr.startswith("O_"):
                return frozenset({expr.attr})
            return None
        if not isinstance(expr, ast.Name) or (id(expr), id(scope)) in seen:
            return None
        seen = seen | {(id(expr), id(scope))}
        owner: _Scope | None = scope
        while owner is not None and not owner.binds(expr.id):
            owner = owner.parent
        owner = owner or self.module_scope
        if expr.id in owner.params or expr.id in owner.kwonly:
            return self._param_flags(owner, expr.id, seen)
        if owner.counts.get(expr.id) != 1 or expr.id not in owner.values:
            return None
        return self._flags(owner.values[expr.id], owner, seen)

    def _param_flags(self, owner: _Scope, name: str, seen: set[object]) -> Flags | None:
        """A parameter: the union over every call of its function in this
        module (callers in other modules are not seen)."""
        calls = self.calls.get(owner.name, [])
        if not owner.name or not calls or self.loads.get(owner.name, 0) != len(calls):
            return None  # a lambda, never called here, or used as a value
        out: Flags | None = frozenset()
        for call, at in calls:
            arg = _argument(call, owner, name)
            out = _union(out, None if arg is None else self._flags(arg, at, seen))
        return out

    # ---- the walk ----

    def _event(self, what: str | tuple[ast.expr, _Scope]) -> None:
        self.events.append((self.stack[-1] if self.stack else "", what))

    def _process(self, op: str) -> None:
        key = (self.module, self.stack[-1] if self.stack else "", op)
        self._proc_ordinal[key] = self._proc_ordinal.get(key, 0) + 1
        self.processes.append((*key, self._proc_ordinal[key]))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        for dec in node.decorator_list:
            self.visit(dec)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        scope = _Scope(node.name, self.scope)
        scope.params = [a.arg for a in (*node.args.posonlyargs, *node.args.args)]
        scope.kwonly = [a.arg for a in node.args.kwonlyargs]
        for extra in (node.args.vararg, node.args.kwarg):
            if extra is not None:
                scope.counts[extra.arg] = 2
        _bindings(scope, node.body)
        outer, self.scope = self.scope, scope
        self.stack.append(node.name)
        for stmt in node.body:  # annotations are not uses
            self.visit(stmt)
        self.stack.pop()
        self.scope = outer

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        scope = _Scope("", self.scope)
        scope.params = [a.arg for a in (*node.args.posonlyargs, *node.args.args)]
        outer, self.scope = self.scope, scope
        self.visit(node.body)
        self.scope = outer

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for stmt in node.body:
            self.visit(stmt)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self.visit(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if isinstance(node.value, ast.Name):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if node.value.id in self.aliases:
                    self.aliases[target.id] = self.aliases[node.value.id]
                if node.value.id in self.subprocess:
                    self.subprocess.add(target.id)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for a in node.names:
            local = a.asname or a.name.split(".")[0]
            self.modules.add(local)
            if a.name == "subprocess":
                self.subprocess.add(local)
            if a.name in _FS_MODULES:
                self.aliases[local] = a.name
                if a.name in ("shutil", "tempfile"):
                    self._event(f"import {a.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module in _FS_MODULES:
            for a in node.names:
                self.imported.add(a.asname or a.name)
                self._event(f"from {node.module} import {a.name}")
        if node.module == "subprocess":
            for a in node.names:
                if a.name in _SUBPROCESS:
                    self._process(f"from subprocess import {a.name}")

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self.loads[node.id] = self.loads.get(node.id, 0) + 1

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        base = node.value.id if isinstance(node.value, ast.Name) else None
        module = self.aliases.get(base or "")
        if module is not None and node.attr == "__dict__":
            self._event(f"{module}.__dict__")
        if module == "ctypes" and node.attr in _C_LOADERS:
            self._event(f"ctypes.{node.attr}")
        if base in self.subprocess and node.attr in _SUBPROCESS:
            self._process(f"subprocess.{node.attr}")
        elif module == "os" and node.attr in _OS_PROCESSES:
            self._process(f"os.{node.attr}")
        elif module == "os" and id(node) not in self.called:
            if node.attr in _MUTATORS or node.attr == "open":
                self._event(f"ref os.{node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        f = node.func
        self.called.add(id(f))
        if isinstance(f, ast.Attribute):
            self._attribute_call(f, node)
        elif isinstance(f, ast.Name):
            self.calls.setdefault(f.id, []).append((node, self.scope))
            self._name_call(f, node)
        self.generic_visit(node)

    def _attribute_call(self, f: ast.Attribute, node: ast.Call) -> None:
        base = f.value.id if isinstance(f.value, ast.Name) else None
        module = self.aliases.get(base or "")
        if module in ("shutil", "tempfile"):
            self._event(f"{module}.{f.attr}")
        elif module == "os" and f.attr == "open":
            flags = node.args[1] if len(node.args) > 1 else _kw(node, "flags")
            self._event("os.open" if flags is None else (flags, self.scope))
        elif f.attr in _MUTATORS or (
            f.attr in _TARGET_MUTATORS
            and (node.args or node.keywords)
            and base not in self.modules
        ):
            self._event(f"{module}.{f.attr}" if module else f".{f.attr}")
        elif f.attr == "open" and module is None:
            at = 1 if base in self.modules else 0  # `<module>.open(path, mode)`
            mode = node.args[at] if len(node.args) > at else _kw(node, "mode")
            if not _read_mode(mode):
                self._event(".open")

    def _name_call(self, f: ast.Name, node: ast.Call) -> None:
        if f.id in self.imported:
            self._event(f"imported {f.id}")
        elif f.id in ("getattr", "vars") and node.args:
            target = node.args[0]
            if isinstance(target, ast.Name) and target.id in self.aliases:
                self._event(f"{f.id}({self.aliases[target.id]})")
        elif f.id == "open":
            mode = node.args[1] if len(node.args) > 1 else _kw(node, "mode")
            if not _read_mode(mode):
                self._event("open")


def _bindings(scope: _Scope, body: list[ast.stmt]) -> None:
    """Count the bindings of each name in `body`, not in nested scopes;
    keep the value of a plain single assignment."""
    for node in _walk_scope(body):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.AugAssign):
            targets = [node.target, node.target]  # a rebinding: never single
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                scope.counts[name] = 2
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.counts[node.name] = scope.counts.get(node.name, 0) + 1
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    scope.counts[name.id] = scope.counts.get(name.id, 0) + 1
        if value is not None and len(targets) == 1 and isinstance(targets[0], ast.Name):
            scope.values[targets[0].id] = value


def _walk_scope(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of `body`, not descending into nested functions, lambdas
    and classes (their names are bindings here, their bodies are not)."""
    todo: list[ast.AST] = list(body)
    while todo:
        node = todo.pop()
        yield node
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        todo.extend(ast.iter_child_nodes(node))


def _union(a: Flags | None, b: Flags | None) -> Flags | None:
    return None if a is None or b is None else a | b


def _argument(call: ast.Call, owner: _Scope, name: str) -> ast.expr | None:
    """The argument a call passes for parameter `name` (none: `None`)."""
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if name in owner.params and owner.params.index(name) < len(call.args):
        return call.args[owner.params.index(name)]
    return _kw(call, name)


def _kw(node: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in node.keywords if k.arg == name), None)


def _read_mode(mode: ast.expr | None) -> bool:
    """No mode, or a literal read mode (`"r"`, `"rb"`, `"r:*"`, ...)."""
    if mode is None:
        return True
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False
    return mode.value.startswith("r") and "+" not in mode.value


def _visitor(path: Path) -> _FsSites:
    tree = ast.parse(path.read_text())
    visitor = _FsSites(path.stem, tree)
    visitor.visit(tree)
    return visitor


def _scan(path: Path) -> list[Site]:
    return _visitor(path).found


def _df_modules() -> list[Path]:
    return sorted(Path(fsutil.__file__).parent.glob("*.py"))


def test_every_fs_mutation_is_accounted_for() -> None:
    found: list[Site] = []
    for path in _df_modules():
        found += _scan(path)
    assert sorted(found) == sorted(FS_SITES)


def test_every_process_start_is_named() -> None:
    """m-F: every process start of `df/` is pinned and names only
    `SUBPROCESS_CREATORS` entries; every entry is started somewhere."""
    found: list[Site] = []
    for path in _df_modules():
        found += _visitor(path).processes
    assert sorted(found) == sorted(PROCESS_SITES)
    named = {c for creators in PROCESS_SITES.values() for c in creators}
    assert named == set(SUBPROCESS_CREATORS)


#: Each blind form of the guard (9-C rounds 1-2 and re-review 2), planted:
#: the scan must flag each.
PLANTED = {
    "local O_CREAT flags": "def f():\n    fl = os.O_WRONLY | os.O_CREAT\n    os.open('a', fl)\n",
    "unknown flags variable": "def f(fl):\n    os.open('a', fl)\n",
    "Path.open write": "def f(p):\n    p.open('w')\n",
    "Path.open variable mode": "def f(p, m):\n    p.open(m)\n",
    "builtin open append": "def f():\n    open('x', 'a')\n",
    "from os import": "from os import mkdir\ndef f():\n    mkdir('a')\n",
    "from os import, passed on": "from os import chmod as c\nHOOK = c\n",
    "import alias": "import os as o\ndef f():\n    o.chmod('a', 0)\n",
    "shutil alias": "import shutil as sh\ndef f():\n    sh.move('a', 'b')\n",
    "tempfile": "import tempfile\ndef f():\n    tempfile.mkdtemp()\n",
    "getattr os": "def f():\n    getattr(os, 'fchmod')(3, 0)\n",
    "mkfifo": "def f():\n    os.mkfifo('a')\n",
    "mknod": "def f():\n    os.mknod('a')\n",
    "truncate": "def f():\n    os.truncate('a', 0)\n",
    "utime": "def f():\n    os.utime('a')\n",
    "chflags": "def f():\n    os.chflags('a', 0)\n",
    "touch": "def f(p):\n    p.touch()\n",
    "tar extract": "def f(t):\n    t.extract('m')\n",
    "tarfile write": "def f():\n    tarfile.open('x', 'w')\n",
    # re-review 2, m-F(a)
    "map os.unlink": "def f(ps):\n    list(map(os.unlink, ps))\n",
    "alias of os.rmdir": "def f():\n    rm = os.rmdir\n    rm('a')\n",
    "partial os.chmod": "import functools\nP = functools.partial(os.chmod, mode=0)\n",
    "default os.chmod": "def f(op=os.chmod):\n    op('a', 0)\n",
    "shadowed const": (
        "FL = os.O_RDONLY\ndef f():\n    FL = os.O_WRONLY | os.O_CREAT\n"
        "    os.open('a', FL)\n"
    ),
    "reassigned const": (
        "FL = os.O_RDONLY\nFL = os.O_WRONLY | os.O_CREAT\n"
        "def f():\n    os.open('a', FL)\n"
    ),
    "augmented const": (
        "FL = os.O_RDONLY\nFL |= os.O_CREAT\ndef f():\n    os.open('a', FL)\n"
    ),
    "augmented local": (
        "def f():\n    fl = os.O_RDONLY\n    fl |= os.O_CREAT\n    os.open('a', fl)\n"
    ),
    "io.open r-path": "import io\ndef f():\n    io.open('rx', 'w')\n",
    "gzip.open r-path": "import gzip\ndef f():\n    gzip.open('rx', 'wb')\n",
    "codecs.open r-path": "import codecs\ndef f():\n    codecs.open('rx', 'a')\n",
    "os alias by assignment": "def f():\n    m = os\n    m.open('rx', os.O_CREAT)\n",
    "setxattr": "def f():\n    os.setxattr('a', 'k', b'v')\n",
    "removexattr": "def f():\n    os.removexattr('a', 'k')\n",
    "Path.copy": "def f(p, q):\n    p.copy(q)\n",
    "Path.move": "def f(p, q):\n    p.move(q)\n",
    "Path.copy_into": "def f(p, q):\n    p.copy_into(q)\n",
    "Path.move_into": "def f(p, q):\n    p.move_into(q)\n",
    "vars(os)": "def f():\n    vars(os)['chmod']('a', 0)\n",
    "os.__dict__": "def f():\n    os.__dict__['chmod']('a', 0)\n",
    "ctypes": "import ctypes\ndef f():\n    ctypes.CDLL(None).unlink(b'a')\n",
    "param of a function used as a value": (
        "def f(fl):\n    os.open('a', fl)\nHOOK = f\ndef g():\n    f(os.O_RDONLY)\n"
    ),
    "param with a creating caller": (
        "def f(fl):\n    os.open('a', fl)\ndef g():\n    f(os.O_RDONLY)\n"
        "def h():\n    f(os.O_WRONLY | os.O_CREAT)\n"
    ),
    "no-create flags": "def f():\n    os.open('a', os.O_WRONLY | os.O_APPEND)\n",
}


@pytest.mark.parametrize("form", sorted(PLANTED))
def test_the_guard_flags_a_planted_form(tmp_path: Path, form: str) -> None:
    """A negative twin per form: a planted instance is a finding."""
    planted = tmp_path / "planted.py"
    planted.write_text("import os\nimport tarfile\n" + PLANTED[form])
    assert _scan(planted), form


#: Process starts, planted: each is a process site.
PLANTED_PROCESSES = {
    "os.system": "def f():\n    os.system('x')\n",
    "os.posix_spawn": "def f():\n    os.posix_spawn('x', ['x'], {})\n",
    "os.spawnv": "def f():\n    os.spawnv(os.P_WAIT, 'x', ['x'])\n",
    "os.execv": "def f():\n    os.execv('x', ['x'])\n",
    "os.popen": "def f():\n    os.popen('x')\n",
    "subprocess alias": "import subprocess as sp\ndef f():\n    sp.call(['x'])\n",
    "from subprocess": "from subprocess import check_output\n",
    "subprocess reference": "import subprocess\nRUN = subprocess.run\n",
}


@pytest.mark.parametrize("form", sorted(PLANTED_PROCESSES))
def test_the_guard_finds_a_planted_process(tmp_path: Path, form: str) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("import os\n" + PLANTED_PROCESSES[form])
    assert _visitor(planted).processes, form


def test_the_guard_passes_literal_reads(tmp_path: Path) -> None:
    """...and only those: read flags (literal, a module constant, a local
    bound once, a parameter every caller passes reads) and read modes are
    not findings; neither is `dict.copy()` nor a process-free annotation."""
    planted = tmp_path / "reads.py"
    planted.write_text(
        "import os\nimport tarfile\nimport io\nimport subprocess\n"
        "FLAGS = os.O_RDONLY | os.O_NOFOLLOW\n"
        "def f(p):\n    os.open('a', FLAGS)\n    os.open('b', os.O_RDONLY)\n"
        "    open('c')\n    open('d', 'rb')\n    p.open()\n"
        "    tarfile.open('e', 'r:*')\n    io.open('w-named', 'r')\n"
        "    fl = os.O_RDONLY | os.O_CLOEXEC\n    os.open('g', fl)\n"
        "    {}.copy()\n"
        "def g(fl):\n    os.open('h', fl)\n"
        "def h(follow: bool) -> subprocess.Popen[bytes]:\n"
        "    g(os.O_RDONLY if follow else os.O_RDONLY | os.O_NOFOLLOW)\n"
        "import os as o\ndef k():\n    o.open('f', o.O_RDONLY)\n"
    )
    found = _scan(planted)
    assert found == [], found


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
    assert model.unsynced() == [], "not durable on return"  # m-E
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


@pytest.mark.parametrize("setgid", [False, True])
def test_make_dir_twins_every_position(
    crash_model: CrashModel, monkeypatch: pytest.MonkeyPatch, setgid: bool
) -> None:
    """Every crash position of `make_dir`; with `setgid` (m-E) in a `2755`
    parent under Linux inheritance (R9): a visible name is at `0711`, or at
    the inherited `2711` until repaired; on return the cleared bit is
    durable (`_primitive_runs`). The exact run count pins each position,
    the created branch's `fault_point("chmod")` included (nit 7)."""
    parent = crash_model.root
    if setgid:
        crash_model.linux_setgid = True
        parent = crash_model.root / "p"
        parent.mkdir()
        os.chmod(parent, 0o2755)
        crash_model.baseline()
    d = parent / "d"

    def run() -> None:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        old = os.umask(0o277)
        try:
            fsutil.make_dir(fd, "d", 0o711, path=d)
        finally:
            os.umask(old)
            os.close(fd)

    def check(*, final: bool) -> None:
        if os.path.lexists(d) or final:
            have = stat.S_IMODE(os.stat(d).st_mode)
            assert have == 0o711 or (setgid and not final and have == 0o2711)

    runs = _primitive_runs(crash_model, monkeypatch, run, check)
    assert runs == (RUNS_SETGID if setgid else RUNS_PLAIN)


#: `make_dir`'s crash positions x 2 crash kinds (R49: asserted exactly).
RUNS_PLAIN = 12
RUNS_SETGID = 16  # + the fchmod syscall and the chmod point


@pytest.mark.parametrize(
    "have,want",
    [
        (0o2711, True),  # an inherited S_ISGID: cleared
        (0o2700, True),
        (0o700, True),  # narrower: widened to the target
        (0o711, False),  # already the target
        (0o4711, False),  # S_ISUID: never repaired (nit 7)
        (0o1711, False),  # S_ISVTX
        (0o6711, False),
        (0o731, False),  # group write: never
        (0o755, False),  # a bit outside the target
    ],
)
def test_repairable_is_exactly_the_target_plus_setgid(have: int, want: bool) -> None:
    """nit 7: `repairable`'s extra-bit set is exactly `S_ISGID`."""
    st = os.stat_result((stat.S_IFDIR | have, 0, 0, 2, 0, 0, 0, 0, 0, 0))
    assert fsutil.repairable(st, 0o711) is want


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


def test_create_file_race_settles_the_winners_file(
    crash_model: CrashModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """m-C, the O_EXCL race forced: between `_open_private`'s absence check
    and `create_file`, another writer creates the journal and writes a line
    without its syncs yet (it dies there). The loser finds the name, settles
    the file and its directory, then opens and appends: after an OS crash
    the winner's line is there as well as its own."""
    from df import journal

    path = crash_model.root / "journal.jsonl"
    real_absent = journal._is_absent  # noqa: SLF001

    def absent_then_raced(p: Path, dir_fd: int | None) -> bool:
        answer = real_absent(p, dir_fd)
        if answer:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, b'{"winner": true}\n')
            os.close(fd)
        return answer

    monkeypatch.setattr(journal, "_is_absent", absent_then_raced)
    journal.journal_event(path, run_id="r", step="s", event="loser")
    assert crash_model.unsynced() == []
    crash_model.os_crash()
    lines = path.read_text().splitlines()
    assert lines[0] == '{"winner": true}' and '"loser"' in lines[1]
