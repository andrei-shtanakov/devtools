"""Tests for receiving a tree from a tar stream (spec 3.3 item 3)."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
from pathlib import Path, PurePosixPath

import pytest

from df import fsutil, receive
from df.digest import TreeError, check_links, tree_digest
from df.receive import (
    BoundedReader,
    CopyLimits,
    parse_pax,
    receive_tree,
    remove_empty_dir,
    remove_own_tree,
)
from tests.df.producers import (
    TAR,
    KillOneProducer,
    LocalProducer,
    data_producer,
    group_gone,
    pid_gone,
    tar_producer,
)
from tests.df.tarlit import (
    BLK,
    CHR,
    CONT,
    DIR,
    FIFO,
    GLOBAL,
    LNK,
    PAX,
    SYM,
    Member,
    fix_checksum,
    header,
    tar_bytes,
)

LR = PurePosixPath("/opt/df/releases/abc-macos-arm64-1")
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def rel(tmp_path: Path) -> Path:
    path = tmp_path / "releases"
    path.mkdir()
    return path


def producer(
    tmp_path: Path, data: bytes | None = None, *, argv: list[str] | None = None
) -> LocalProducer:
    """LocalProducer emitting `data`, or running `argv`."""
    if argv is not None:
        return LocalProducer(argv)
    assert data is not None
    return data_producer(tmp_path, data)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _refused(tmp_path: Path, rel: Path, data: bytes, **limits: int) -> str:
    """Receive must refuse; producer ended and gone; partial copy root 0700."""
    prod = producer(tmp_path, data)
    with pytest.raises(TreeError) as info:
        receive_tree(prod, rel, "r", limits=CopyLimits(**limits))
    assert prod.ended
    assert group_gone(prod.pgid)
    assert _mode(rel / "r") == 0o700
    return str(info.value)


# --- the literal generator itself -------------------------------------------


def test_tar_bytes_is_literal(tmp_path: Path) -> None:
    members: list[Member] = [
        ("a", DIR),
        ("a/f", 0o644, b"hi"),
        ("a/l", SYM, "f"),
        ("a/h", LNK, "a/f"),
    ]
    archive = tmp_path / "t.tar"
    archive.write_bytes(tar_bytes(members))
    listing = subprocess.run(
        [TAR, "-tvf", archive], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    names = subprocess.run(
        [TAR, "-tf", archive], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert [n.rstrip("/") for n in names] == ["a", "a/f", "a/l", "a/h"]
    assert [line[0] for line in listing] == ["d", "-", "l", "h"]


# --- protocol and lifecycle -------------------------------------------------


class _NoEnd:
    fd = 0

    def wait(self, timeout: float) -> int:
        return 0


class _NoFd:
    def wait(self, timeout: float) -> int:
        return 0

    def end(self) -> None:
        pass


def test_receive_requires_producer_protocol(tmp_path: Path, rel: Path) -> None:
    for fake in (_NoEnd(), _NoFd()):
        with pytest.raises(TypeError):
            receive_tree(fake, rel, "r")  # type: ignore[arg-type]
    proc = subprocess.Popen(["/bin/cat", "/dev/null"], stdout=subprocess.PIPE)
    try:
        with pytest.raises(TypeError):
            receive_tree(proc, rel, "r")  # type: ignore[arg-type]
    finally:
        proc.wait()
        assert proc.stdout is not None
        proc.stdout.close()
    assert not (rel / "r").exists()


def test_receive_normalizes_modes_and_keeps_root_closed(
    tmp_path: Path, rel: Path
) -> None:
    data = tar_bytes(
        [("a", 0o4777, b"x"), ("b", 0o6644, b"y"), ("d", DIR), ("d/e", DIR)]
    )
    prod = producer(tmp_path, data)
    receive_tree(prod, rel, "r")
    assert prod.ended
    assert _mode(rel / "r/a") == 0o755
    assert _mode(rel / "r/b") == 0o644
    assert _mode(rel / "r/d") == 0o755
    assert _mode(rel / "r/d/e") == 0o755
    assert _mode(rel / "r") == 0o700
    assert (rel / "r/a").read_bytes() == b"x"
    assert os.lstat(rel / "r/a").st_uid == os.getuid()


def test_receive_effect_order_matches_accept_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: Path
) -> None:
    calls: list[str] = []
    real_sync_dir = fsutil.sync_dir

    def spy_tree(root: Path) -> None:
        calls.append(f"sync_tree:{root.name}:{_mode(root / 'd'):o}")

    def spy_dir(target: int | Path) -> fsutil.SyncNote:
        calls.append(f"sync_dir:{Path(target).name}")
        return real_sync_dir(target)

    def spy_fault(kind: str, path: Path) -> None:
        if not (path / "d").exists():
            calls.append(kind)
            return
        state = "partial" if _mode(path / "d") == 0o700 else "complete"
        calls.append(f"{kind}:{path.name}:{state}")

    monkeypatch.setattr(fsutil, "sync_tree", spy_tree)
    monkeypatch.setattr(fsutil, "sync_dir", spy_dir)
    monkeypatch.setattr(fsutil, "fault_point", spy_fault)
    monkeypatch.setattr(fsutil, "sync_file", lambda fd: calls.append("sync_file"))
    prod = producer(tmp_path, tar_bytes([("d", DIR), ("d/f", 0o644, b"x")]))
    real_end = prod.end
    monkeypatch.setattr(prod, "end", lambda: (calls.append("end"), real_end()))
    receive_tree(prod, rel, "r")
    assert calls == [
        "created",
        "end",
        "created:r:complete",
        "sync_tree:r:755",
        "sync_dir:releases",
    ]


def test_hooks_called_per_member(tmp_path: Path, rel: Path) -> None:
    seen: list[tuple[str, str]] = []
    data = tar_bytes([("d", DIR), ("d/f", 0o644, b"x" * 10), ("e", 0o644, b"")])
    receive_tree(
        producer(tmp_path, data),
        rel,
        "r",
        _hook=lambda point, path: seen.append((point, path)),
    )
    assert seen == [
        ("member", "d"),
        ("member", "d/f"),
        ("mid-copy", "d/f"),
        ("member", "e"),
    ]


def test_existing_destination_refused(tmp_path: Path, rel: Path) -> None:
    (rel / "r").mkdir(mode=0o755)
    (rel / "r" / "keep").write_bytes(b"k")
    prod = producer(tmp_path, tar_bytes([("a", 0o644, b"x")]))
    with pytest.raises(TreeError, match="exists"):
        receive_tree(prod, rel, "r")
    assert prod.ended
    assert sorted(os.listdir(rel / "r")) == ["keep"]


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\nb"])
def test_bad_destination_name_refused(tmp_path: Path, rel: Path, name: str) -> None:
    prod = producer(tmp_path, tar_bytes([]))
    with pytest.raises(TreeError):
        receive_tree(prod, rel, name)
    assert prod.ended


def test_refused_receive_removes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: Path
) -> None:
    data = tar_bytes([("a", DIR), ("a/f", 0o644, b"x" * 2000)])[:2048]
    prod = producer(tmp_path, data)
    removals: list[str] = []

    def forbid(name: str):  # type: ignore[no-untyped-def]
        def call(*args: object, **kwargs: object) -> None:
            removals.append(name)
            raise AssertionError(f"{name} called")

        return call

    for name in ("unlink", "rmdir", "remove"):
        monkeypatch.setattr(os, name, forbid(name))
    monkeypatch.setattr(shutil, "rmtree", forbid("rmtree"))
    monkeypatch.setattr(fsutil, "remove_durably", forbid("remove_durably"))
    with pytest.raises(TreeError):
        receive_tree(prod, rel, "r")
    monkeypatch.undo()
    assert removals == []
    assert prod.ended
    assert _mode(rel / "r") == 0o700
    assert (rel / "r/a/f").exists()


# --- names ------------------------------------------------------------------


@pytest.mark.parametrize("member", ["../x", "/abs", "a/../../x", "./..", "a//b"])
def test_bad_names_refused(tmp_path: Path, rel: Path, member: str) -> None:
    data = tar_bytes([("a", DIR), (member, 0o644, b"x")])
    _refused(tmp_path, rel, data)
    assert not (tmp_path / "x").exists()


def test_non_utf8_and_newline_names_refused(tmp_path: Path, rel: Path) -> None:
    for raw in (b"\xff", b"a\nb"):
        block = header(raw, "0", size=0)
        target = rel.parent / f"rel-{raw.hex()}"
        target.mkdir()
        _refused(tmp_path, target, block + bytes(1024))


def test_nfc_equal_names_refused(tmp_path: Path, rel: Path) -> None:
    nfc = "é"
    nfd = unicodedata.normalize("NFD", nfc)
    data = tar_bytes([(nfc, 0o644, b"1"), (nfd, 0o644, b"2")])
    assert "NFC" in _refused(tmp_path, rel, data)


def test_duplicate_member_refused(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes([("a", 0o644, b"1"), ("a", 0o644, b"2")])
    _refused(tmp_path, rel, data)
    assert (rel / "r/a").read_bytes() == b"1"


@pytest.mark.parametrize("case", ["root-not-dir", "root-not-first", "root-twice"])
def test_root_record_rules(tmp_path: Path, rel: Path, case: str) -> None:
    members: dict[str, list[Member]] = {
        "root-not-dir": [("./", 0o644, b"")],
        "root-not-first": [("a", DIR), ("./", DIR)],
        "root-twice": [("./", DIR), ("./", DIR)],
    }
    assert "root" in _refused(tmp_path, rel, tar_bytes(members[case]))


def test_root_record_first_accepted_and_mode_ignored(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes([("./", DIR), ("./a/", DIR), ("./a/f", 0o755, b"x")])
    receive_tree(producer(tmp_path, data), rel, "r")
    assert _mode(rel / "r") == 0o700
    assert _mode(rel / "r/a/f") == 0o755


def test_parent_must_come_from_stream(tmp_path: Path, rel: Path) -> None:
    _refused(tmp_path, rel, tar_bytes([("a/b", 0o644, b"")]))


def test_parent_must_be_directory_member(tmp_path: Path, rel: Path) -> None:
    _refused(tmp_path, rel, tar_bytes([("a", 0o644, b""), ("a/b", 0o644, b"")]))


def test_symlink_as_intermediate_dir_refused(tmp_path: Path, rel: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    members: list[Member] = [("d", SYM, "placeholder"), ("d/f", 0o644, b"x")]
    data = tar_bytes(members, pax={0: _pax(linkpath=str(outside))})
    _refused(tmp_path, rel, data)
    assert os.listdir(outside) == []


# --- types and links --------------------------------------------------------


@pytest.mark.parametrize("kind", [CHR, BLK, FIFO, CONT, "S", "L", "K", GLOBAL])
def test_device_fifo_sparse_refused(tmp_path: Path, rel: Path, kind: str) -> None:
    _refused(tmp_path, rel, tar_bytes([("dev", kind)]))
    assert os.listdir(rel / "r") == []


@pytest.mark.parametrize(
    "members",
    [
        [("g", LNK, "f")],
        [("d", DIR), ("g", LNK, "d")],
        [("f", 0o644, b"x"), ("s", SYM, "f"), ("g", LNK, "s")],
        [("g", LNK, "../f")],
        [("g", LNK, "g")],
    ],
)
def test_hardlink_to_unaccepted_target_refused(
    tmp_path: Path, rel: Path, members: list[Member]
) -> None:
    _refused(tmp_path, rel, tar_bytes(members))
    assert not (rel / "r/g").exists()


def test_hardlink_becomes_independent_file(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes(
        [("d", DIR), ("d/f", 0o755, b"data"), ("g", LNK, "./d/f"), ("h", LNK, "g")]
    )
    receive_tree(producer(tmp_path, data), rel, "r")
    f, g, h = (os.lstat(rel / "r" / p) for p in ("d/f", "g", "h"))
    assert len({f.st_ino, g.st_ino, h.st_ino}) == 3
    assert f.st_nlink == g.st_nlink == h.st_nlink == 1
    assert (rel / "r/g").read_bytes() == (rel / "r/h").read_bytes() == b"data"
    assert _mode(rel / "r/g") == 0o755


def test_hardlink_body_is_discarded(tmp_path: Path, rel: Path) -> None:
    body = b"ignored"
    data = (
        tar_bytes([("f", 0o644, b"data")])[:-1024]
        + header("g", LNK, size=len(body), linkname="f")
        + body
        + bytes(512 - len(body))
        + bytes(1024)
    )
    receive_tree(producer(tmp_path, data), rel, "r")
    assert (rel / "r/g").read_bytes() == b"data"


def test_symlink_text_kept_as_data(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes([("l", SYM, "../../etc/passwd")])
    receive_tree(producer(tmp_path, data), rel, "r")
    assert os.readlink(rel / "r/l") == "../../etc/passwd"
    assert check_links(rel / "r", LR) != []


def test_internal_absolute_link_kept_verbatim(tmp_path: Path, rel: Path) -> None:
    members: list[Member] = [
        ("python", DIR),
        ("python/cpython-3.12.13-x", DIR),
        ("python/cpython-3.12.13-x/bin", DIR),
        ("python/cpython-3.12-x", SYM, f"{LR}/python/cpython-3.12.13-x"),
    ]
    receive_tree(producer(tmp_path, tar_bytes(members)), rel, "r")
    link = rel / "r/python/cpython-3.12-x"
    assert os.readlink(link) == f"{LR}/python/cpython-3.12.13-x"
    assert check_links(rel / "r", LR) == []


def test_member_without_received_parent_refused(tmp_path: Path, rel: Path) -> None:
    members: list[Member] = [
        ("python/cpython-3.12.13-x", DIR),
        ("python/cpython-3.12.13-x/bin", DIR),
        ("python/cpython-3.12-x", SYM, f"{LR}/python/cpython-3.12.13-x"),
    ]
    _refused(tmp_path, rel, tar_bytes(members))


# --- pax --------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["SCHILY.xattr.user.k", "GNU.sparse.size", "hdrcharset"]
)
def test_unknown_pax_key_refused(tmp_path: Path, rel: Path, key: str) -> None:
    data = tar_bytes([("a", 0o644, b"")], pax={0: _pax(**{key: "v"})})
    assert key in _refused(tmp_path, rel, data)


@pytest.mark.parametrize(
    "key", ["SCHILY.xattr.user.x", "LIBARCHIVE.xattr.user.x", "SCHILY.acl.access"]
)
def test_pax_xattr_acl_keys_refused(tmp_path: Path, rel: Path, key: str) -> None:
    data = tar_bytes([("d", DIR), ("a", 0o644, b"")], pax={1: _pax(**{key: "v"})})
    assert key in _refused(tmp_path, rel, data)
    assert os.path.isdir(rel / "r/d")


def _pax(**items: str) -> bytes:
    """Well-formed pax body: each record's length counts itself."""
    out = b""
    for key, value in items.items():
        tail = f" {key}={value}\n".encode()
        length = len(tail) + 1
        while length != len(tail) + len(str(length)):
            length = len(tail) + len(str(length))
        out += str(length).encode() + tail
    return out


def test_pax_helper_lengths_are_exact() -> None:
    body = _pax(mtime="1", path="a" * 200)
    assert parse_pax(body) == {"path": b"a" * 200}


def test_pax_ignored_owner_keys_not_applied(tmp_path: Path, rel: Path) -> None:
    body = _pax(uid="0", gid="0", uname="root", gname="wheel", mtime="1.5")
    data = tar_bytes([("a", 0o4755, b"x")], pax={0: body})
    receive_tree(producer(tmp_path, data), rel, "r")
    st = os.lstat(rel / "r/a")
    assert st.st_uid == os.getuid()
    assert stat.S_IMODE(st.st_mode) == 0o755


def test_pax_path_linkpath_size_applied(tmp_path: Path, rel: Path) -> None:
    long_name = "n" * 150
    data = tar_bytes(
        [("x", 0o644, b"abc"), ("y", SYM, "zzz")],
        pax={0: _pax(path=long_name), 1: _pax(linkpath=long_name)},
    )
    receive_tree(producer(tmp_path, data), rel, "r")
    assert (rel / "r" / long_name).read_bytes() == b"abc"
    assert os.readlink(rel / "r/y") == long_name


def test_pax_size_overrides_header_size(tmp_path: Path, rel: Path) -> None:
    data = (
        header("PaxHeader/a", PAX, size=len(_pax(size="3")))
        + _pax(size="3").ljust(512, b"\0")
        + header("a", "0", size=0)
        + b"abc".ljust(512, b"\0")
        + bytes(1024)
    )
    receive_tree(producer(tmp_path, data), rel, "r")
    assert (rel / "r/a").read_bytes() == b"abc"


def test_two_pax_headers_with_ignored_keys_only_refused(
    tmp_path: Path, rel: Path
) -> None:
    one = tar_bytes([("a", 0o644, b"x")], pax={0: _pax(mtime="1")})
    receive_tree(producer(tmp_path, one), rel, "ok")
    assert (rel / "ok/a").read_bytes() == b"x"
    pax_block = one[:1024]
    two = pax_block + one
    assert "two pax headers" in _refused(tmp_path, rel, two)


def test_pax_without_member_refused(tmp_path: Path, rel: Path) -> None:
    _refused(tmp_path, rel, tar_bytes([("a", 0o644, b"")], pax={1: _pax(mtime="1")}))


def test_oversized_pax_refused_before_body_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: Path
) -> None:
    readers: list[BoundedReader] = []

    class Recording(BoundedReader):
        def __init__(self, fd: int, *, deadline: float, max_bytes: int) -> None:
            super().__init__(fd, deadline=deadline, max_bytes=max_bytes)
            readers.append(self)

    monkeypatch.setattr(receive, "BoundedReader", Recording)
    big = header("PaxHeader/a", PAX, size_field=b"77777777777\0")
    data = big + b"9 a=bcd\n".ljust(16 << 20, b"x")
    prod = producer(tmp_path, data)
    start = time.monotonic()
    with pytest.raises(TreeError, match="pax header too large"):
        receive_tree(prod, rel, "r")
    assert time.monotonic() - start < 1.0
    assert readers[0].consumed <= 1024
    assert prod.ended


_PAX_CHILD = """
import json, os, sys, time
from pathlib import Path
from df.digest import TreeError
from df.receive import receive_tree
from tests.df.producers import data_producer
from tests.df.tarlit import tar_bytes
tmp, body = Path(sys.argv[1]), bytes.fromhex(sys.argv[2])
rel = tmp / "releases"
rel.mkdir()
prod = data_producer(tmp, tar_bytes([("a", 0o644, b"x")], pax={0: body}))
start = time.monotonic()
try:
    receive_tree(prod, rel, "r")
    error = None
except TreeError as exc:
    error = str(exc)
print(json.dumps({"error": error, "elapsed": time.monotonic() - start,
                  "ended": prod.ended,
                  "mode": os.lstat(rel / "r").st_mode & 0o777}))
"""


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"0 mtime=x\n", "invalid pax record length"),
        (b"99 a=b\n", "runs past the header"),
        (b"9 mtime=x", "does not end with a newline"),
        (b"6 =xy\n", "without a valid key"),
        (b"010 mtime=1\n", "invalid pax record length"),
        (b"11 size=-1\n", "invalid pax size"),
        # brief's body: each record is really 10 bytes, so the length is wrong
        (b"12 path=a\n12 path=b\n", "does not end with a newline"),
        (b"10 path=a\n10 path=b\n", "repeated pax key: path"),
        (b"11 mtime=1\n11 mtime=2\n", "repeated pax key: mtime"),
    ],
)
def test_malformed_pax_record_refused_fast(
    tmp_path: Path, body: bytes, reason: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PAX_CHILD, str(tmp_path), body.hex()],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    outcome = json.loads(result.stdout)
    assert reason in outcome["error"]
    assert outcome["elapsed"] < 1.0
    assert outcome["ended"] is True
    assert outcome["mode"] == 0o700


# --- malformed streams ------------------------------------------------------


def _malformed(case: str) -> tuple[list[str] | None, bytes]:
    good = tar_bytes([("a", 0o644, b"hello")])
    if case == "bad-checksum":
        block = bytearray(good[:512])
        block[0:1] = b"b"
        return None, bytes(block) + good[512:]
    if case == "non-octal-size":
        block = header("a", "0", size_field=b"0000000001x\0")
        return None, block + good[512:]
    if case == "truncated-body":
        return None, good[:515]
    if case == "missing-second-zero-block":
        return None, good[:-512]
    return ["/bin/sh", "-c", 'cat "$0"; exit 1'], good


@pytest.mark.parametrize(
    "case",
    [
        "bad-checksum",
        "non-octal-size",
        "truncated-body",
        "missing-second-zero-block",
        "producer-exit-1",
    ],
)
def test_malformed_stream_is_tree_error(tmp_path: Path, rel: Path, case: str) -> None:
    argv, data = _malformed(case)
    if argv is not None:
        src = tmp_path / "stream.bin"
        src.write_bytes(data)
        prod = producer(tmp_path, argv=[*argv[:3], str(src)])
        with pytest.raises(TreeError, match="exit"):
            receive_tree(prod, rel, "r")
        assert prod.ended
        return
    reasons = {
        "bad-checksum": "checksum",
        "non-octal-size": "numeric field size",
        "truncated-body": "end of stream",
        "missing-second-zero-block": "end of stream",
    }
    assert reasons[case] in _refused(tmp_path, rel, data)


def test_bad_magic_refused(tmp_path: Path, rel: Path) -> None:
    block = bytearray(header("a", "0"))
    block[257:265] = b"ustar  \0"
    _refused(tmp_path, rel, fix_checksum(bytes(block)) + bytes(1024))


def test_data_after_end_refused(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes([("a", 0o644, b"x")]) + bytes(100) + b"x"
    assert "data after end of archive" in _refused(tmp_path, rel, data)


def test_nonzero_second_end_block_refused(tmp_path: Path, rel: Path) -> None:
    data = tar_bytes([("a", 0o644, b"x")])[:-512] + b"x" * 512
    _refused(tmp_path, rel, data)


def test_padding_counts_toward_stream_cap(tmp_path: Path, rel: Path) -> None:
    archive = tar_bytes([("a", 0o644, b"x")])
    data = archive + bytes(1 << 20)
    _refused(tmp_path, rel, data, max_stream_bytes=len(archive) + 4096)


# --- limits and deadlines ---------------------------------------------------


_LIMIT_CASES: dict[str, tuple[list[Member], dict[str, int]]] = {
    "max_objects": ([("a", 0o644, b""), ("b", 0o644, b"")], {"max_objects": 1}),
    "max_bytes": ([("a", 0o644, b"x" * 100)], {"max_bytes": 50}),
    "max_stream_bytes": ([("a", 0o644, b"x" * 4096)], {"max_stream_bytes": 2048}),
    "max_depth": ([("a", DIR), ("a/b", 0o644, b"")], {"max_depth": 1}),
    "max_path": ([("abcdefgh", 0o644, b"")], {"max_path": 4}),
}


@pytest.mark.parametrize("limit", sorted(_LIMIT_CASES))
def test_limits_refuse_and_reap_producer(tmp_path: Path, rel: Path, limit: str) -> None:
    members, limits = _LIMIT_CASES[limit]
    assert limit in _refused(tmp_path, rel, tar_bytes(members), **limits)


def test_limits_allow_exact_values(tmp_path: Path, rel: Path) -> None:
    members: list[Member] = [("a", DIR), ("a/b", 0o644, b"x" * 50)]
    data = tar_bytes(members)
    limits = CopyLimits(
        max_objects=2, max_bytes=50, max_stream_bytes=len(data), max_depth=2, max_path=3
    )
    receive_tree(producer(tmp_path, data), rel, "r", limits=limits)
    assert (rel / "r/a/b").read_bytes() == b"x" * 50


def test_silent_producer_with_open_stdout_hits_deadline(
    tmp_path: Path, rel: Path
) -> None:
    prod = producer(tmp_path, argv=["/bin/sh", "-c", "printf 0123456789; sleep 60"])
    start = time.monotonic()
    with pytest.raises(TreeError, match="deadline"):
        receive_tree(prod, rel, "r", limits=CopyLimits(max_seconds=0.3))
    elapsed = time.monotonic() - start
    assert 0.3 <= elapsed < 10.0
    assert prod.ended
    assert group_gone(prod.pgid)


def _await_file(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not (path.exists() and path.read_text().strip()):
        assert time.monotonic() < deadline, f"{path} never written"
        time.sleep(0.02)


def test_forked_producer_ended(tmp_path: Path, rel: Path) -> None:
    script = 'sleep 60 & echo $! > "$0"; exec sleep 60'
    pidfile = tmp_path / "grandchild.pid"
    prod = LocalProducer(["/bin/sh", "-c", script, str(pidfile)])
    _await_file(pidfile)
    with pytest.raises(TreeError, match="deadline"):
        receive_tree(prod, rel, "r", limits=CopyLimits(max_seconds=0.3))
    assert pid_gone(int(pidfile.read_text()))

    twin_pidfile = tmp_path / "twin.pid"
    twin = KillOneProducer(["/bin/sh", "-c", script, str(twin_pidfile)])
    _await_file(twin_pidfile)
    with pytest.raises(TreeError, match="deadline"):
        receive_tree(twin, rel, "twin", limits=CopyLimits(max_seconds=0.3))
    survivor = int(twin_pidfile.read_text())
    try:
        os.kill(survivor, 0)
    finally:
        os.killpg(twin.pgid, 9)
    assert pid_gone(survivor)


def test_bounded_reader_limits(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"abcdef")
        reader = BoundedReader(read_fd, deadline=time.monotonic() + 5, max_bytes=4)
        assert reader.read_exact(3) == b"abc"
        with pytest.raises(TreeError, match="stream exceeds"):
            reader.read_exact(3)
        late = BoundedReader(read_fd, deadline=time.monotonic() + 0.1, max_bytes=99)
        with pytest.raises(TreeError, match="deadline"):
            late.read_some()
        os.close(write_fd)
        write_fd = -1
        reader = BoundedReader(read_fd, deadline=time.monotonic() + 5, max_bytes=99)
        assert reader.read_some() == b""
        with pytest.raises(TreeError, match="end of stream"):
            BoundedReader(
                read_fd, deadline=time.monotonic() + 5, max_bytes=9
            ).read_exact(1)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


# --- real tar ---------------------------------------------------------------


def _source_tree(root: Path, kind: str) -> Path:
    root.mkdir()
    if kind == "empty":
        return root
    (root / "d" / "sub").mkdir(parents=True)
    (root / "d" / "f").write_bytes(b"hello\n")
    (root / "d" / "f").chmod(0o755)
    (root / ("long-" + "n" * 110)).write_bytes(b"long")
    (root / "d" / "été.txt").write_bytes(b"utf8")
    os.link(root / "d" / "f", root / "hard")
    os.symlink("d/f", root / "sym")
    return root


@pytest.mark.parametrize("tree", ["empty", "full"])
def test_real_tar_stream_accepted(tmp_path: Path, rel: Path, tree: str) -> None:
    src = _source_tree(tmp_path / "src", tree)
    prod = tar_producer(src)
    receive_tree(prod, rel, "r")
    assert prod.ended
    assert tree_digest(rel / "r") == tree_digest(src)
    assert _mode(rel / "r") == 0o700
    if tree == "full":
        assert os.lstat(rel / "r/hard").st_ino != os.lstat(rel / "r/d/f").st_ino
        assert _mode(rel / "r/d/sub") == 0o755


def test_end_padding_drained_before_wait(tmp_path: Path, rel: Path) -> None:
    src = _source_tree(tmp_path / "src", "full")
    prod = tar_producer(src, trailer=1 << 20)
    receive_tree(prod, rel, "r", limits=CopyLimits(max_seconds=60))
    assert tree_digest(rel / "r") == tree_digest(src)


def test_receive_then_digest_equals_source_digest(tmp_path: Path, rel: Path) -> None:
    src = _source_tree(tmp_path / "src", "full")
    members: list[Member] = [
        ("d", DIR),
        ("d/sub", DIR),
        ("d/f", 0o755, b"hello\n"),
        ("placeholder", 0o644, b"long"),
        ("d/été.txt", 0o600, b"utf8"),
        ("hard", LNK, "d/f"),
        ("sym", SYM, "d/f"),
    ]
    pax = {3: _pax(path="long-" + "n" * 110)}
    receive_tree(producer(tmp_path, tar_bytes(members, pax=pax)), rel, "r")
    assert tree_digest(rel / "r") == tree_digest(src)


def test_receiver_never_imports_tarfile() -> None:
    code = "import sys, df.receive; print('tarfile' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "False"


# --- removal primitives -----------------------------------------------------


@pytest.fixture
def as_root_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receive, "ROOT_UID", os.getuid())


def _tree(parent: Path, name: str, mode: int) -> Path:
    root = parent / name
    (root / "d").mkdir(parents=True)
    (root / "d" / "f").write_bytes(b"x")
    os.symlink("/etc", root / "l")
    root.chmod(mode)
    return root


@pytest.mark.usefixtures("as_root_owner")
def test_remove_own_tree_accepts_0755_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    real_sync_dir = fsutil.sync_dir
    monkeypatch.setattr(fsutil, "fault_point", lambda k, p: calls.append(f"{k}:{p}"))
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda t: calls.append("sync_dir") or real_sync_dir(t)
    )
    _tree(tmp_path, "r", 0o755)
    remove_own_tree(tmp_path, "r")
    assert not (tmp_path / "r").exists()
    assert calls == [f"unlinked:{tmp_path / 'r'}", "sync_dir"]


@pytest.mark.usefixtures("as_root_owner")
def test_remove_own_tree_accepts_partial_0700(tmp_path: Path) -> None:
    root = _tree(tmp_path, "r", 0o700)
    (root / "d").chmod(0o700)
    remove_own_tree(tmp_path, "r")
    assert not root.exists()


def test_remove_own_tree_refuses_foreign_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(receive, "ROOT_UID", os.getuid() + 1)
    _tree(tmp_path, "r", 0o700)
    with pytest.raises(TreeError, match="owner"):
        remove_own_tree(tmp_path, "r")
    assert (tmp_path / "r" / "d" / "f").exists()


@pytest.mark.usefixtures("as_root_owner")
def test_remove_own_tree_refuses_missing_symlink_and_bad_name(tmp_path: Path) -> None:
    os.symlink(_tree(tmp_path, "real", 0o755), tmp_path / "link")
    for name in ("missing", "link", "..", "a/b"):
        with pytest.raises(TreeError):
            remove_own_tree(tmp_path, name)
    assert (tmp_path / "real" / "d" / "f").exists()


@pytest.mark.usefixtures("as_root_owner")
def test_remove_empty_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []
    real_sync_dir = fsutil.sync_dir
    monkeypatch.setattr(fsutil, "fault_point", lambda k, p: calls.append(k))
    monkeypatch.setattr(
        fsutil, "sync_dir", lambda t: calls.append("sync_dir") or real_sync_dir(t)
    )
    (tmp_path / "e").mkdir()
    remove_empty_dir(tmp_path, "e")
    assert not (tmp_path / "e").exists()
    assert calls == ["unlinked", "sync_dir"]


@pytest.mark.usefixtures("as_root_owner")
def test_remove_empty_dir_refusals(tmp_path: Path) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_bytes(b"")
    (tmp_path / "file").write_bytes(b"")
    for name in ("missing", "full", "file"):
        with pytest.raises(TreeError):
            remove_empty_dir(tmp_path, name)
    assert (tmp_path / "full" / "x").exists()


def test_remove_empty_dir_refuses_foreign_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(receive, "ROOT_UID", os.getuid() + 1)
    (tmp_path / "e").mkdir()
    with pytest.raises(TreeError, match="owner"):
        remove_empty_dir(tmp_path, "e")
    assert (tmp_path / "e").is_dir()


# --- fix round 1: every refusal is a TreeError ------------------------------


def test_long_destination_path_refused(tmp_path: Path, rel: Path) -> None:
    """Nested 200-byte names beyond the platform PATH_MAX: refused up front."""
    levels = int(os.pathconf(rel, "PC_PATH_MAX")) // 201 + 2
    members: list[Member] = []
    pax: dict[int, bytes] = {}
    for depth in range(1, levels + 1):
        pax[len(members)] = _pax(path="/".join(["n" * 200] * depth))
        members.append(("placeholder", DIR))
    pax[len(members)] = _pax(path="/".join(["n" * 200] * levels) + "/f")
    members.append(("placeholder", 0o644, b"x"))
    reason = _refused(tmp_path, rel, tar_bytes(members, pax=pax))
    assert "path too long for the destination" in reason or "max_path" in reason


def test_path_room_counts_destination_prefix(tmp_path: Path, rel: Path) -> None:
    room = int(os.pathconf(rel, "PC_PATH_MAX")) - len(os.fsencode(rel / "r")) - 2
    fits = "/".join(["n" * 200] * (room // 201)) or "n"
    names = fits.split("/")
    members: list[Member] = [("p", DIR)] * len(names)
    pax = {i: _pax(path="/".join(names[: i + 1])) for i in range(len(names))}
    receive_tree(producer(tmp_path, tar_bytes(members, pax=pax)), rel, "r")
    assert (rel / "r" / fits).is_dir()


def test_sync_failure_is_tree_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: Path
) -> None:
    boom = OSError(errno.EIO, "injected")

    def fail(root: Path) -> None:
        raise boom

    monkeypatch.setattr(fsutil, "sync_tree", fail)
    prod = producer(tmp_path, tar_bytes([("a", 0o644, b"x")]))
    with pytest.raises(TreeError) as info:
        receive_tree(prod, rel, "r")
    assert info.value.__cause__ is boom
    assert prod.ended


def test_producer_tree_error_is_not_rewrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: Path
) -> None:
    prod = producer(tmp_path, tar_bytes([("a", 0o644, b"x")]))
    own = TreeError("producer left processes behind")
    real_end = prod.end

    def end() -> None:
        real_end()
        raise own

    monkeypatch.setattr(prod, "end", end)
    with pytest.raises(TreeError) as info:
        receive_tree(prod, rel, "r")
    assert info.value is own


def test_keyboard_interrupt_not_swallowed(tmp_path: Path, rel: Path) -> None:
    def hook(point: str, path: str) -> None:
        raise KeyboardInterrupt

    prod = producer(tmp_path, tar_bytes([("a", 0o644, b"x")]))
    with pytest.raises(KeyboardInterrupt):
        receive_tree(prod, rel, "r", _hook=hook)
    assert prod.ended


@pytest.mark.parametrize("case", ["dir", "symlink", "root"])
def test_body_on_bodiless_record_refused(tmp_path: Path, rel: Path, case: str) -> None:
    if case == "symlink":
        data = header("s", SYM, size=1, linkname="t") + b"x".ljust(512, b"\0")
        data += bytes(1024)
    else:
        name = "d" if case == "dir" else "./"
        data = tar_bytes([(name, DIR, b"x")])
    assert "body" in _refused(tmp_path, rel, data)


def test_bounded_reader_uses_poll_for_high_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(receive, "_FD_SETSIZE", 0)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"abc")
        reader = BoundedReader(read_fd, deadline=time.monotonic() + 5, max_bytes=9)
        assert reader.read_exact(3) == b"abc"
        late = BoundedReader(read_fd, deadline=time.monotonic() + 0.1, max_bytes=9)
        with pytest.raises(TreeError, match="deadline"):
            late.read_some()
        monkeypatch.delattr(receive.select, "poll", raising=False)
        with pytest.raises(TreeError, match="descriptor"):
            reader.read_some()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_open_dir_refuses_planted_symlink_and_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", root / "d")
    (root / "f").write_bytes(b"")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for parts in (("d",), ("f",), ("missing",)):
            with pytest.raises(TreeError):
                receive._open_dir(root_fd, parts)
    finally:
        os.close(root_fd)


def test_remove_own_tree_oserror_is_tree_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(receive, "ROOT_UID", os.getuid())
    _tree(tmp_path, "r", 0o755)
    boom = OSError(errno.EBUSY, "injected")

    def fail(*args: object, **kwargs: object) -> None:
        raise boom

    monkeypatch.setattr(shutil, "rmtree", fail)
    with pytest.raises(TreeError) as info:
        remove_own_tree(tmp_path, "r")
    assert info.value.__cause__ is boom
    for remove in (remove_own_tree, remove_empty_dir):
        with pytest.raises(TreeError, match="cannot open") as info:
            remove(tmp_path / "missing-parent", "r")
        assert isinstance(info.value.__cause__, OSError)
