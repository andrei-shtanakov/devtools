"""Negative tests of the OS-crash model (`tests/df/crash_model.py`, Task 9-C).

The model is the instrument of the crash tests: a model more lenient than
the spec's rules would let them pass for nothing. Each rule R1-R7 is pinned
from both sides -- what a sync makes durable, and what it does not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import fsutil, journal
from tests.df.conftest import BUILD_UID, FakeLayout
from tests.df.crash_model import Crash, CrashModel, ModelError


@pytest.fixture()
def m(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> Iterator[CrashModel]:
    root = fake_layout.layout.root
    (root / "d").mkdir()
    (root / "e").mkdir()
    model = CrashModel(root, fake_layout)
    model.install(monkeypatch)
    try:
        yield model
    finally:
        model.close()


def write(path: Path, data: bytes, *, sync: bool = False, mode: int = 0o644) -> None:
    """Create or overwrite in place (no rename); `sync` = `sync_file`."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        if sync:
            fsutil.sync_file(fd)
    finally:
        os.close(fd)


def mode_of(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o7777


# ---------- R1/R2: names, content, metadata ----------


def test_unsynced_create_is_lost(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x")
    assert m.unsynced() == ["d/f"]
    m.os_crash()
    assert not os.path.lexists(f)


def test_synced_create_survives(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    fsutil.sync_dir(f.parent)
    assert m.unsynced() == []
    m.os_crash()
    assert f.read_bytes() == b"x"


def test_parent_sync_keeps_the_name_not_the_content(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x")
    fsutil.sync_dir(f.parent)
    m.os_crash()
    assert f.read_bytes() == b""  # R4: born empty


def test_file_sync_without_parent_sync_loses_the_name(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    m.os_crash()
    assert not os.path.lexists(f)


def test_child_created_by_another_process_comes_back_empty(m: CrashModel) -> None:
    """L1: first seen at the parent's sync, still no content (R4)."""
    f = m.root / "d" / "f"
    subprocess.run(["/bin/sh", "-c", f"printf x > '{f}'"], check=True)
    fsutil.sync_dir(f.parent)
    m.os_crash()
    assert f.read_bytes() == b""


def test_in_place_write_without_sync_reverts(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"old", sync=True)
    fsutil.sync_dir(f.parent)
    write(f, b"new")
    fsutil.sync_dir(f.parent)  # the name's sync is not the content's
    m.os_crash()
    assert f.read_bytes() == b"old"


@pytest.mark.parametrize("synced", [False, True])
def test_rename_needs_the_parent_sync(m: CrashModel, synced: bool) -> None:
    a, b = m.root / "d" / "a", m.root / "d" / "b"
    write(a, b"x", sync=True)
    fsutil.sync_dir(a.parent)
    os.rename(a, b)
    if synced:
        fsutil.sync_dir(a.parent)
    m.os_crash()
    assert (os.path.lexists(a), os.path.lexists(b)) == (not synced, synced)
    assert (b if synced else a).read_bytes() == b"x"


def test_replacing_rename_needs_the_parent_sync(m: CrashModel) -> None:
    """`write_atomic` without its last step: the old file comes back."""
    target = m.root / "d" / "t"
    write(target, b"old", sync=True)
    fsutil.sync_dir(target.parent)
    tmp = m.root / "d" / "tmp"
    write(tmp, b"new", sync=True)
    os.rename(tmp, target)
    m.os_crash()
    assert target.read_bytes() == b"old"
    assert not os.path.lexists(tmp)


def test_write_atomic_leaves_nothing_unsynced(m: CrashModel) -> None:
    target = m.root / "d" / "t"
    fsutil.write_atomic(target, "v1")
    fsutil.write_atomic(target, "v2")
    assert m.unsynced() == []
    m.os_crash()
    assert target.read_text() == "v2"


def test_rename_synced_at_destination_only_is_in_both(m: CrashModel) -> None:
    """R5: «долговечно в карантине раньше, чем уходит из родителя»."""
    src = m.root / "d" / "tree"
    src.mkdir()
    write(src / "f", b"x", sync=True)
    fsutil.sync_tree(src)
    fsutil.sync_dir(src.parent)
    tree_id = m.identity(src)
    dst = m.root / "e" / "tree"
    os.rename(src, dst)
    fsutil.sync_dir(dst.parent)
    m.os_crash()
    assert (src / "f").read_bytes() == (dst / "f").read_bytes() == b"x"
    assert m.identity(src) == m.identity(dst) == tree_id


def test_rename_synced_at_source_only_is_lost_from_both(m: CrashModel) -> None:
    src = m.root / "d" / "f"
    write(src, b"x", sync=True)
    fsutil.sync_dir(src.parent)
    dst = m.root / "e" / "f"
    os.rename(src, dst)
    fsutil.sync_dir(src.parent)
    m.os_crash()
    assert not os.path.lexists(src)
    assert not os.path.lexists(dst)


@pytest.mark.parametrize("synced", [False, True])
def test_unlink_needs_the_parent_sync(m: CrashModel, synced: bool) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    fsutil.sync_dir(f.parent)
    os.unlink(f)
    if synced:
        fsutil.sync_dir(f.parent)
    m.os_crash()
    assert os.path.lexists(f) is not synced
    if not synced:
        assert f.read_bytes() == b"x"  # with its durable content


@pytest.mark.parametrize("synced", [False, True])
def test_rmtree_needs_the_parent_sync(m: CrashModel, synced: bool) -> None:
    tree = m.root / "d" / "tree"
    (tree / "sub").mkdir(parents=True)
    write(tree / "sub" / "f", b"x", sync=True)
    fsutil.sync_tree(tree)
    fsutil.sync_dir(tree.parent)
    import shutil

    shutil.rmtree(tree)
    if synced:
        fsutil.sync_dir(tree.parent)
    m.os_crash()
    assert os.path.lexists(tree) is not synced
    if not synced:
        assert (tree / "sub" / "f").read_bytes() == b"x"


def test_mkdir_needs_the_parent_sync(m: CrashModel) -> None:
    os.mkdir(m.root / "d" / "x", 0o700)
    os.mkdir(m.root / "d" / "y", 0o711)
    fsutil.sync_dir(m.root / "d")
    os.mkdir(m.root / "d" / "z")
    m.os_crash()
    assert sorted(os.listdir(m.root / "d")) == ["x", "y"]
    assert mode_of(m.root / "d" / "y") == 0o711  # the creation mode


@pytest.mark.parametrize("how", ["path", "fd"])
def test_chmod_needs_the_objects_own_sync(m: CrashModel, how: str) -> None:
    """The parent's sync keeps the name and the creation mode only."""
    d = m.root / "d" / "x"
    os.mkdir(d, 0o700)
    if how == "path":
        os.chmod(d, 0o711)
    else:
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        os.fchmod(fd, 0o711)
        os.close(fd)
    fsutil.sync_dir(d.parent)
    m.os_crash()
    assert mode_of(d) == 0o700


def test_chmod_with_the_objects_own_sync_survives(m: CrashModel) -> None:
    d = m.root / "d" / "x"
    os.mkdir(d, 0o700)
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    os.fchmod(fd, 0o711)
    fsutil.sync_dir(fd)
    os.close(fd)
    fsutil.sync_dir(d.parent)
    m.os_crash()
    assert mode_of(d) == 0o711


def test_chmod_of_a_durable_object_reverts_without_its_sync(m: CrashModel) -> None:
    """`verify_chmod` without `sync_dir(release_root)`: back to `0700`."""
    d = m.root / "d"
    os.chmod(d, 0o700)
    fsutil.sync_dir(d)
    os.chmod(d, 0o755)
    fsutil.sync_dir(m.root)  # the parent's sync: not the mode
    m.os_crash()
    assert mode_of(d) == 0o700


@pytest.mark.parametrize("synced", [False, True])
def test_chown_needs_the_objects_own_sync(
    m: CrashModel, fake_layout: FakeLayout, synced: bool
) -> None:
    d = m.root / "d" / "x"
    os.mkdir(d, 0o700)
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    os.fchown(fd, BUILD_UID, BUILD_UID)
    if synced:
        fsutil.sync_dir(fd)
    os.close(fd)
    fsutil.sync_dir(d.parent)
    m.os_crash()
    owner = fake_layout.owner_of(os.stat(d))
    assert owner == (BUILD_UID if synced else os.getuid())


# ---------- R3: sync_tree ----------


def test_sync_tree_keeps_contents_not_the_roots_name(m: CrashModel) -> None:
    tree = m.root / "d" / "tree"
    (tree / "sub").mkdir(parents=True)
    write(tree / "sub" / "f", b"x")
    os.symlink("sub/f", tree / "l")
    fsutil.sync_tree(tree)
    assert m.unsynced() == ["d/tree", "d/tree/l", "d/tree/sub", "d/tree/sub/f"]
    fsutil.sync_dir(tree.parent)
    assert m.unsynced() == []
    m.os_crash()
    assert (tree / "sub" / "f").read_bytes() == b"x"
    assert os.readlink(tree / "l") == "sub/f"


def test_sync_tree_alone_loses_the_tree(m: CrashModel) -> None:
    tree = m.root / "d" / "tree"
    tree.mkdir()
    write(tree / "f", b"x")
    fsutil.sync_tree(tree)
    m.os_crash()
    assert not os.path.lexists(tree)


def test_link_replaced_by_rename_needs_the_parent_sync(m: CrashModel) -> None:
    """`activate`: the old target comes back without `sync_dir(opt)`."""
    cur = m.root / "d" / "current"
    os.symlink("releases/a", cur)
    fsutil.sync_dir(cur.parent)
    tmp = m.root / "d" / ".current.tmp"
    os.symlink("releases/b", tmp)
    os.rename(tmp, cur)
    m.os_crash()
    assert os.readlink(cur) == "releases/a"
    os.symlink("releases/b", tmp)
    os.rename(tmp, cur)
    fsutil.sync_dir(cur.parent)
    m.os_crash()
    assert os.readlink(cur) == "releases/b"


def test_journal_sync_names_are_the_models(m: CrashModel) -> None:
    """`df.journal` bound `sync_file` at import: replaced too."""
    assert journal.sync_file == m._sync_file  # noqa: SLF001
    log = m.root / "d" / "journal"
    journal.journal_event(log, run_id="r", step="s", event="start")
    fsutil.sync_dir(log.parent)
    m.os_crash()
    assert len(journal.read_journal(log)) == 1


# ---------- R6, R7: boot, process crash, traps ----------


def test_os_crash_is_a_new_boot(m: CrashModel) -> None:
    before = fsutil.boot_id()
    m.os_crash()
    assert fsutil.boot_id() != before


def test_process_crash_undoes_what_handlers_do(m: CrashModel) -> None:
    """R7: a killed process runs no `except`/`finally`."""
    f = m.root / "d" / "f"
    m.trap(lambda p: p.kind == "fault")
    with pytest.raises(Crash):
        try:
            write(f, b"x")
            fsutil.fault_point("created", f)
        except BaseException:
            os.unlink(f)  # a handler the real crash would never run
            raise
    assert not os.path.lexists(f)
    m.reap()
    assert f.read_bytes() == b"x"


def test_a_point_after_the_crash_crashes_again(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    m.trap(lambda p: p.op == "created")
    with pytest.raises(Crash):
        try:
            fsutil.fault_point("created", f)
        except BaseException:  # noqa: BLE001 - the swallowing handler under test
            write(f, b"x", sync=True)  # swallowing would continue: refused
    m.reap()
    assert not os.path.lexists(f)


def test_trapped_sync_takes_no_effect(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    m.trap(lambda p: p.kind == "sync" and p.path == f.parent)
    with pytest.raises(Crash):
        fsutil.sync_dir(f.parent)
    m.os_crash()
    assert not os.path.lexists(f)


def test_dropped_sync_takes_no_effect(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    m.drop_syncs(lambda p: p.path == f.parent)
    fsutil.sync_dir(f.parent)
    assert [p.op for p in m.points][-1] == "sync_dir"  # still counted
    m.os_crash()
    assert not os.path.lexists(f)


def test_points_are_numbered_in_order(m: CrashModel) -> None:
    target = m.root / "d" / "t"
    start = len(m.points)
    fsutil.write_atomic(target, "x")
    got = [(p.kind, p.op, p.path) for p in m.points[start:]]
    assert got[1:] == [
        ("fault", "renamed", target),
        ("sync", "sync_dir", target.parent),
    ]
    assert got[0][:2] == ("sync", "sync_file")
    assert [p.index for p in m.points] == list(range(len(m.points)))


# ---------- branching (R49) ----------


def test_snapshot_restore_branches_the_same_state(m: CrashModel) -> None:
    f, g = m.root / "d" / "f", m.root / "d" / "g"
    write(f, b"durable", sync=True)
    fsutil.sync_dir(f.parent)
    write(g, b"visible")
    branch = m.snapshot()
    os.unlink(f)
    fsutil.sync_dir(f.parent)
    m.restore(branch)
    assert (f.read_bytes(), g.read_bytes()) == (b"durable", b"visible")
    assert m.unsynced() == ["d/g"]
    m.os_crash()
    assert f.read_bytes() == b"durable"
    assert not os.path.lexists(g)
    assert len(m.points) == branch.points


def test_restore_keeps_identities(m: CrashModel) -> None:
    f = m.root / "d" / "f"
    write(f, b"x", sync=True)
    fsutil.sync_dir(f.parent)
    oid = m.identity(f)
    branch = m.snapshot()
    m.os_crash()
    m.restore(branch)
    assert m.identity(f) == oid


def test_bare_fsync_of_a_file_is_no_file_sync_on_macos(m: CrashModel) -> None:
    """R1: on macOS only F_FULLFSYNC (`sync_file`) makes a file durable; a
    bare `os.fsync` from outside `fsutil` does on Linux only."""
    f = m.root / "d" / "f"
    write(f, b"old", sync=True)
    fsutil.sync_dir(f.parent)
    fd = os.open(f, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, b"new")
    os.fsync(fd)
    os.close(fd)
    m.os_crash()
    assert f.read_bytes() == (b"old" if sys.platform == "darwin" else b"new")


@pytest.mark.skipif(sys.platform != "darwin", reason="F_FULLFSYNC is macOS")
def test_fullfsync_outside_fsutil_is_a_file_sync(m: CrashModel) -> None:
    """The entry's stdlib copy syncs a file with `F_FULLFSYNC` on macOS."""
    import fcntl

    f = m.root / "d" / "f"
    write(f, b"old", sync=True)
    fsutil.sync_dir(f.parent)
    fd = os.open(f, os.O_WRONLY | os.O_TRUNC)
    os.write(fd, b"new")
    fcntl.fcntl(fd, getattr(fcntl, "F_FULLFSYNC", 51))
    os.close(fd)
    m.os_crash()
    assert f.read_bytes() == b"new"


def test_bare_fsync_of_a_directory_counts(m: CrashModel) -> None:
    """R2: a bare `os.fsync` of a directory (the entry's stdlib settle) makes
    its entries durable on both platforms."""
    a, b = m.root / "d" / "a", m.root / "d" / "b"
    write(a, b"x", sync=True)
    fsutil.sync_dir(a.parent)
    os.rename(a, b)
    fd = os.open(a.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)
    m.os_crash()
    assert not os.path.lexists(a)
    assert b.read_bytes() == b"x"


def test_is_durable_compares_the_state(m: CrashModel) -> None:
    """The same object after a `fchmod` without its own sync is not durable."""
    d = m.root / "d"
    assert m.is_durable(d)
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    os.fchmod(fd, 0o700)
    os.close(fd)
    assert not m.is_durable(d)
    fsutil.sync_dir(d)
    assert m.is_durable(d)


def test_sync_file_of_a_directory_is_a_model_error(m: CrashModel) -> None:
    fd = os.open(m.root / "d", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ModelError):
            fsutil.sync_file(fd)
    finally:
        os.close(fd)


def test_unreadable_object_changed_is_a_model_error(m: CrashModel) -> None:
    """L2: a `chmod` of an object the model cannot open is never a guess."""
    f = m.root / "d" / "f"
    write(f, b"x", mode=0o000)
    with pytest.raises(ModelError, match="L2"):
        os.chmod(f, 0o600)


@pytest.mark.parametrize("knob", [False, True])
def test_linux_setgid_inheritance_is_opt_in(m: CrashModel, knob: bool) -> None:
    """R9: with `linux_setgid` a directory made in a setgid parent is born
    setgid (Linux), durably with its name; without it, as on macOS, not."""
    m.linux_setgid = knob
    os.chmod(m.root / "d", 0o2755)
    os.mkdir(m.root / "d" / "x", 0o711)
    fsutil.sync_dir(m.root / "d")
    m.os_crash()
    have = os.stat(m.root / "d" / "x").st_mode & 0o7777
    assert have == (0o2711 if knob else 0o711)
