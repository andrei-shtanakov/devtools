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


#: Every finding of the fail-closed scan of `df/` (`_FsSites`: anything that
#: may create an object, change its metadata, rename or remove it, unless it
#: is literally a read) -- the class of the 9-C findings: an object not
#: durable in its final form, or a found one used unsettled -- keyed by
#: (module, function, operation, ordinal), with the reason it is safe. A new
#: finding fails `test_every_fs_mutation_is_accounted_for` until it goes
#: through an `fsutil` primitive or names its protocol here. Limit: objects
#: created by processes `df` starts are outside the scan
#: (`SUBPROCESS_CREATORS`).
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
    ("fsutil", "_settle_found_file", "os.open", 1): "local read-only flags",
    ("fsutil", "_sync_regular", "os.open", 1): "local read-only flags",
    ("config", "_read_optional", "os.open", 1): "local read-only flags",
    ("journal", "_open_or_make_dir", "os.open", 1): (
        "the chain's directory flags (after fsutil.make_dir)"
    ),
    ("launcher_entry", "_descend", "os.open", 1): "the chain's directory flags",
    ("journal", "_open_regular", "os.open", 1): (
        "no O_CREAT: the lock (never written), the journal (append, then "
        "sync_file) or a read"
    ),
    ("launcher_entry", "_open_regular", "os.open", 1): (
        "no O_CREAT: the lock (never written) or a read"
    ),
    ("inputs", "", "import shutil", 1): "root's checkout: scratch",
    ("receive", "", "import shutil", 1): "protocol remove_own_tree: rmtree",
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

#: Attribute calls that create, change the metadata of, rename or remove an
#: object, whatever the object (`os.<x>`, a `Path`, a `TarFile`).
_MUTATORS = frozenset(
    {
        "mkdir", "makedirs", "chmod", "fchmod", "lchmod", "chown", "fchown",
        "lchown", "rename", "replace", "symlink", "link", "unlink", "remove",
        "rmdir", "removedirs", "mkfifo", "mknod", "truncate", "ftruncate",
        "utime", "chflags", "lchflags", "write_text", "write_bytes", "touch",
        "symlink_to", "hardlink_to", "extractall", "extract",
    }
)  # fmt: skip
#: `os.open` flags that only read or open a directory.
_READ_FLAGS = frozenset(
    {"O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK", "O_NOCTTY"}
)
#: Modules whose every use is a finding (they create or remove files).
_FS_MODULES = frozenset({"os", "shutil", "tempfile"})

#: Processes `df` starts that create objects -- outside the AST scan, so
#: listed here with why their result is safe (the guard's limit).
SUBPROCESS_CREATORS = {
    "uv python install (admin)": "protocol install_admin_python: sync_tree, rename",
    "uv sync / git / tar (build, as df-build)": "builder tree: copied, digested",
    "git / tar (root's checkout)": "scratch under verify/, fetched every run",
    "cleaner find/chmod/rm (as df-build)": "protocol cleanup_tree: root syncs",
    "useradd / dscl (account)": "OS records, judged by verify_build_user",
}


class _FsSites(ast.NodeVisitor):
    """Fail-closed scan of one module: every use that may create, change
    metadata, rename or remove -- unless it is a literal read. Findings:
    `<os>.<mutator>` and `.<mutator>` attribute calls; every `os.open` whose
    flags are not literally read/directory ones (module constants of only
    such flags included); `open`/`.open` with a mode that is not a literal
    read mode; any `shutil`/`tempfile` use; `from os|shutil|tempfile import`;
    `getattr` on those modules; a call of a name imported from them.
    `import os as x` aliases are tracked. Subprocesses are outside the scan
    (`SUBPROCESS_CREATORS`)."""

    def __init__(self, module: str, tree: ast.Module) -> None:
        self.module = module
        self.aliases: dict[str, str] = {}  # local name -> fs module
        self.imported: set[str] = set()  # names from `from os import ...`
        self.read_consts = {
            t.id
            for n in tree.body
            if isinstance(n, ast.Assign) and self._literal_read(n.value, set())
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        self.stack: list[str] = []
        self.found: list[tuple[str, str, str, int]] = []
        self._ordinal: dict[tuple[str, str, str], int] = {}

    def _literal_read(self, expr: ast.expr, consts: set[str]) -> bool:
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self._literal_read(expr.left, consts) and self._literal_read(
                expr.right, consts
            )
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            return expr.attr in _READ_FLAGS
        return isinstance(expr, ast.Name) and expr.id in consts

    def _add(self, op: str) -> None:
        key = (self.module, self.stack[-1] if self.stack else "", op)
        self._ordinal[key] = self._ordinal.get(key, 0) + 1
        self.found.append((*key, self._ordinal[key]))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for a in node.names:
            if a.name in _FS_MODULES:
                self.aliases[a.asname or a.name] = a.name
                if a.name != "os":
                    self._add(f"import {a.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module in _FS_MODULES:
            for a in node.names:
                self.imported.add(a.asname or a.name)
                self._add(f"from {node.module} import {a.name}")

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        f = node.func
        if isinstance(f, ast.Attribute):
            self._attribute_call(f, node)
        elif isinstance(f, ast.Name):
            self._name_call(f, node)
        self.generic_visit(node)

    def _attribute_call(self, f: ast.Attribute, node: ast.Call) -> None:
        base = f.value.id if isinstance(f.value, ast.Name) else None
        module = self.aliases.get(base or "")
        if module in ("shutil", "tempfile"):
            self._add(f"{module}.{f.attr}")
        elif module == "os" and f.attr == "open":
            flags = node.args[1] if len(node.args) > 1 else _kw(node, "flags")
            if flags is None or not self._literal_read(flags, self.read_consts):
                self._add("os.open")
        elif f.attr in _MUTATORS:
            self._add(f"{module}.{f.attr}" if module else f".{f.attr}")
        elif f.attr == "open" and module is None:
            mode = node.args[0] if node.args else _kw(node, "mode")
            if base == "tarfile":
                mode = node.args[1] if len(node.args) > 1 else _kw(node, "mode")
            if not _read_mode(mode):
                self._add(".open")

    def _name_call(self, f: ast.Name, node: ast.Call) -> None:
        if f.id in self.imported:
            self._add(f"imported {f.id}")
        elif f.id == "getattr" and node.args:
            target = node.args[0]
            if isinstance(target, ast.Name) and target.id in self.aliases:
                self._add(f"getattr({self.aliases[target.id]})")
        elif f.id == "open":
            mode = node.args[1] if len(node.args) > 1 else _kw(node, "mode")
            if not _read_mode(mode):
                self._add("open")


def _kw(node: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in node.keywords if k.arg == name), None)


def _read_mode(mode: ast.expr | None) -> bool:
    """No mode, or a literal read mode (`"r"`, `"rb"`, `"r:*"`, ...)."""
    if mode is None:
        return True
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False
    return mode.value.startswith("r") and "+" not in mode.value


def _scan(path: Path) -> list[tuple[str, str, str, int]]:
    tree = ast.parse(path.read_text())
    visitor = _FsSites(path.stem, tree)
    visitor.visit(tree)
    return visitor.found


def test_every_fs_mutation_is_accounted_for() -> None:
    found: list[tuple[str, str, str, int]] = []
    for path in sorted(Path(fsutil.__file__).parent.glob("*.py")):
        found += _scan(path)
    assert sorted(found) == sorted(FS_SITES)


#: Each blind form of the first guard, planted: the scan must flag each.
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
}


@pytest.mark.parametrize("form", sorted(PLANTED))
def test_the_guard_flags_a_planted_form(tmp_path: Path, form: str) -> None:
    """A negative twin per form: a planted instance is a finding."""
    planted = tmp_path / "planted.py"
    planted.write_text("import os\nimport tarfile\n" + PLANTED[form])
    assert _scan(planted), form


def test_the_guard_passes_literal_reads(tmp_path: Path) -> None:
    """...and only those: literal read flags and modes are not findings."""
    planted = tmp_path / "reads.py"
    planted.write_text(
        "import os\nimport tarfile\nFLAGS = os.O_RDONLY | os.O_NOFOLLOW\n"
        "def f(p):\n    os.open('a', FLAGS)\n    os.open('b', os.O_RDONLY)\n"
        "    open('c')\n    open('d', 'rb')\n    p.open()\n"
        "    tarfile.open('e', 'r:*')\n"
        "import os as o\ndef g():\n    o.open('f', o.O_RDONLY)\n"
    )
    assert _scan(planted) == []


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
