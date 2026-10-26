"""Tests for the entry of `darkfactory` and for bootstrap (Task 8a part 2).

Two kinds of runs:
- in process, `launcher_entry.run(argv, root)` with `_import_df` replaced by a
  recorder (the test process has already imported the dev `df`); writer mode
  is tested only this way, with `ROOT_UID` patched to the current uid;
- as a subprocess, the generated entry text run by a real interpreter with
  `-I -B -S` against a temporary administrative dir holding stub `df/cli.py`
  and `df/state.py` (a subprocess cannot import the whole dev `df` from a
  stub admin dir); the stub's `RawSnapshot` is checked against the real
  `df.state` (Task 9), which the in-process runs use.

Every stdlib copy in the entry is run against its `df` original on the same
inputs, hostile ones included.
"""

from __future__ import annotations

import ast
import dataclasses
import errno
import fcntl
import json
import os
import shutil
import subprocess
import sys
import types
import typing
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from df import accept, admin, bootstrap, journal
from df import launcher_entry as entry
from df import state as df_state
from df.config import (
    ConfigError,
    ReleaseEntry,
    ReleasesConfig,
    dump_releases,
    parse_releases,
)
from df.digest import TreeError, check_names, tree_digest
from df.journal import (
    Stable,
    StepFailed,
    Transitional,
    generation_begin,
    generation_end,
    read_consistent,
    writer_lock,
)
from df.paths import Layout
from tests.df.test_digest import GOLDEN_DIGEST, _golden_tree
from tests.df.test_journal import GENERATION_CANONICAL, GENERATION_MALFORMED

ID = "abc123def456-macos-arm64-1"
OTHER = "abc123def456-macos-arm64-2"
REPO = Path(__file__).resolve().parents[2]
INSTALL_TEXT = b'schema = "df/install/v1"\n# install\n'
MODELS_TEXT = b'schema = "df/models/v1"\n'

STUB_STATE = '''\
"""Stub of df.state (Task 9) for the entry tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RawSnapshot:
    generation: int
    current_id: str | None
    releases_text: bytes | None
    install_text: bytes | None
    models_text: bytes | None
    pending_present: bool


@dataclass(frozen=True)
class Stable:
    value: object
    generation: int


@dataclass(frozen=True)
class Transitional:
    generation: int
'''

STUB_CLI = '''\
"""Stub of df.cli (Task 9): prints what the entry handed over."""
import dataclasses
import json
import sys

from df import state

print("IMPORTED-CLI")


def _plain(value):
    return value.decode() if isinstance(value, bytes) else value


def main(argv, *, mode, read, lock_fd, layout):
    out = {
        "argv": list(argv),
        "mode": mode,
        "lock_fd": lock_fd,
        "layout": [type(layout).__module__, str(layout.root)],
        "kind": type(read).__name__,
        "generation": read.generation,
        "path0": sys.path[0],
        "state_file": state.__file__,
        "df_modules": sorted(m for m in sys.modules if m.split(".")[0] == "df"),
    }
    if isinstance(read, state.Stable):
        raw = dataclasses.asdict(read.value)
        out["raw"] = {k: _plain(v) for k, v in raw.items()}
    print("MAIN " + json.dumps(out))
    return 7
'''

STUB_PATHS = '''\
"""Stub of df.paths: the entry builds the layout of its root from it (D2)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Layout:
    root: object
'''


# --- worlds -----------------------------------------------------------------


def _layout(tmp_path: Path) -> Layout:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = Path(os.path.realpath(tmp_path)) / "root"
    root.mkdir()
    return Layout(root)


def write_admin(lay: Layout, release_id: str) -> str:
    """Stub admin code of `release_id`; returns its admin digest."""
    pkg = lay.admin_dir(release_id) / "df"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "state.py").write_text(STUB_STATE)
    (pkg / "cli.py").write_text(STUB_CLI)
    (pkg / "paths.py").write_text(STUB_PATHS)
    return admin.admin_digest(lay, release_id)


def write_registry(lay: Layout, records: list[ReleaseEntry]) -> None:
    """`releases.toml` as `df.config` renders it."""
    lay.etc.mkdir(parents=True, exist_ok=True)
    (lay.etc / "releases.toml").write_text(
        dump_releases(ReleasesConfig(tuple(records)))
    )


def record(release_id: str, admin_digest: str, **kw: Any) -> ReleaseEntry:
    """A registry record with the given admin digest."""
    fields: dict[str, Any] = {
        "id": release_id,
        "digest": "sha256:" + "a" * 64,
        "admin_digest": admin_digest,
        "admin_python": "3.12.13",
        "status": "allowed",
        "reason": None,
    }
    fields.update(kw)
    return ReleaseEntry(**fields)


def set_current(lay: Layout, release_id: str) -> None:
    """`current` -> `releases/<id>` (the protocol `activate`'s link)."""
    lay.release_dir(release_id).mkdir(parents=True, exist_ok=True)
    tmp = lay.opt / ".current.tmp"
    os.symlink(f"releases/{release_id}", tmp)
    os.rename(tmp, lay.current)


def world(
    tmp_path: Path,
    *,
    generation: bytes = b"2\n",
    status: str = "allowed",
    pending: bool = False,
) -> Layout:
    """An installed layout: one active release with stub admin code."""
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    lay.generation.write_bytes(generation)
    reason = "bad" if status == "revoked" else None
    write_registry(
        lay, [record(ID, write_admin(lay, ID), status=status, reason=reason)]
    )
    (lay.etc / "install.toml").write_bytes(INSTALL_TEXT)
    (lay.etc / "models.toml").write_bytes(MODELS_TEXT)
    set_current(lay, ID)
    if pending:
        lay.pending.write_text("pending\n")
    lay.bin_dir.mkdir(parents=True)
    make_interpreter(lay)
    lay.entry.write_text(entry_text_as_user(lay))
    return lay


def entry_text_as_user(lay: Layout) -> str:
    """`entry_text` with the current user as root (the subprocess has no
    monkeypatch): the one line `ROOT_UID = 0` substituted."""
    text = entry.entry_text(lay)
    exe_line = "\n_EXECUTABLE = sys.executable\n"
    assert text.count("\nROOT_UID = 0\n") == 1 and text.count(exe_line) == 1
    text = text.replace(exe_line, f"\n_EXECUTABLE = {str(interpreter(lay))!r}\n")
    return text.replace("\nROOT_UID = 0\n", f"\nROOT_UID = {os.geteuid()}\n")


def interpreter(lay: Layout) -> Path:
    """The stand-in admin interpreter the entry takes as its own (M-4)."""
    return lay.admin_python / "cpython-3.12.13-test" / "bin" / "python3.12"


def make_interpreter(lay: Layout) -> None:
    exe = interpreter(lay)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"stand-in\n")
    exe.chmod(0o755)


def run_entry(
    lay: Layout, *args: str, python: str = sys.executable, flags: str = "-IBS"
) -> subprocess.CompletedProcess[str]:
    """The installed entry as `bin/darkfactory` runs it."""
    return subprocess.run(
        [python, *([flags] if flags else []), str(lay.entry), *args],
        cwd=lay.root,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def main_call(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The stub main's record from the subprocess output."""
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("MAIN ")]
    assert len(lines) == 1, done.stdout + done.stderr
    return json.loads(lines[0].removeprefix("MAIN "))


# --- in-process recorder ----------------------------------------------------

STUB = types.ModuleType("stub_state")
exec(compile(STUB_STATE, "stub_state", "exec", dont_inherit=True), STUB.__dict__)
#: The in-process runs hand `main` the real `df.state` (Task 9).
STATE = df_state


@dataclass
class Recorder:
    """Stands in for `df.cli.main`; `before` runs at the import point."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    before: Callable[[], None] | None = None
    admin_dirs: list[Path] = field(default_factory=list)

    def import_df(self, admin_dir: Path) -> tuple[Any, Callable[..., int], Any]:
        self.admin_dirs.append(admin_dir)
        if self.before is not None:
            self.before()
        return STATE, self.main, Layout

    def main(
        self,
        argv: list[str],
        *,
        mode: str,
        read: Any,
        lock_fd: int | None,
        layout: Layout,
    ) -> int:
        self.calls.append(
            {
                "argv": argv,
                "mode": mode,
                "read": read,
                "lock_fd": lock_fd,
                "layout": layout,
            }
        )
        return 7


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    recorder = Recorder()
    monkeypatch.setattr(entry, "_import_df", recorder.import_df)
    yield recorder
    for call in recorder.calls:
        if call["lock_fd"] is not None:
            os.close(call["lock_fd"])


@pytest.fixture(autouse=True)
def _owner_is_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test trees are the current user's: the entry takes them as root's."""
    monkeypatch.setattr(entry, "ROOT_UID", os.geteuid())


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The current user plays root for the entry."""
    monkeypatch.setattr(entry, "ROOT_UID", os.geteuid())


def run_in(lay: Layout, *argv: str) -> int:
    return entry.run(list(argv), root=str(lay.root), executable=str(interpreter(lay)))


# --- the module itself --------------------------------------------------------


def test_entry_imports_only_stdlib_at_runtime() -> None:
    tree = ast.parse(Path(entry.__file__).read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names - {"__future__"} <= set(sys.stdlib_module_names)
    guarded = [n for n in tree.body if isinstance(n, ast.If)]
    assert any("TYPE_CHECKING" in ast.unparse(n.test) for n in guarded)


def test_entry_text_is_the_module_with_root(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    source = Path(entry.__file__).read_text()
    text = entry.entry_text(lay)
    assert text == source.replace('_ROOT = "/"\n', f"_ROOT = {str(lay.root)!r}\n")
    assert text != source


def test_entry_text_from_a_source(tmp_path: Path) -> None:
    """M-1: the text comes from the named source (a release's admin code)."""
    lay = _layout(tmp_path)
    source = tmp_path / "launcher_entry.py"
    source.write_text('x = 1\n_ROOT = "/"\ny = 2\n')
    assert entry.entry_text(lay, source) == f"x = 1\n_ROOT = {str(lay.root)!r}\ny = 2\n"


@pytest.mark.parametrize(
    "body",
    [
        b"x = 1\n",  # no _ROOT line
        b'_ROOT = "/"\n_ROOT = "/"\n',  # two
        b'_ROOT = "/"\n\xff\n',  # not UTF-8
        b'_ROOT = "/"\n' + b"#" * (1 << 20),  # over the size limit
    ],
    ids=["none", "two", "not-utf8", "too-big"],
)
def test_entry_text_refuses_a_bad_source(tmp_path: Path, body: bytes) -> None:
    """M13: not exactly one `_ROOT` line (the frozen cross-version contract) —
    `Refused("launcher")`, never an `AssertionError`."""
    lay = _layout(tmp_path)
    source = tmp_path / "launcher_entry.py"
    source.write_bytes(body)
    with pytest.raises(entry.Refused) as exc:
        entry.entry_text(lay, source)
    assert exc.value.step == "launcher"


@pytest.mark.parametrize("kind", ["link", "dir", "fifo"])
def test_entry_text_source_is_a_regular_file(tmp_path: Path, kind: str) -> None:
    """The source is opened without following a link and must be regular."""
    lay = _layout(tmp_path)
    real = tmp_path / "real.py"
    real.write_text('_ROOT = "/"\n')
    source = tmp_path / "launcher_entry.py"
    if kind == "link":
        source.symlink_to(real)
    elif kind == "dir":
        source.mkdir()
    else:
        os.mkfifo(source)
    with pytest.raises((entry.Refused, OSError)) as exc:
        entry.entry_text(lay, source)
    if isinstance(exc.value, OSError):
        assert exc.value.errno in (errno.ELOOP, errno.EMLINK)
    else:
        assert exc.value.step == "launcher"


def test_entry_paths_match_layout(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    paths = entry.EntryPaths(str(lay.root))
    for name in (
        "opt", "etc", "var", "install_dir", "lock", "generation", "pending",
        "marks_dir", "current", "releases", "staging", "quarantine", "admin",
        "admin_python", "bin_dir", "local_bin",
    ):  # fmt: skip
        assert getattr(paths, name) == getattr(lay, name), name
    assert paths.home_parent == lay.build_home.parent
    assert paths.releases_toml == lay.etc / "releases.toml"
    assert paths.install_toml == lay.etc / "install.toml"
    assert paths.models_toml == lay.etc / "models.toml"
    assert paths.admin_dir(ID) == lay.admin_dir(ID)
    assert paths.release_dir(ID) == lay.release_dir(ID)
    assert paths.staging_dir(ID) == lay.staging_dir(ID)


def test_entry_raw_fields_match_df_state() -> None:
    """F4: `RAW_FIELDS` are the fields of the real `df.state.RawSnapshot`,
    and the subprocess stub mirrors it field for field, types included."""
    real = typing.get_type_hints(df_state.RawSnapshot)
    stub = typing.get_type_hints(STUB.RawSnapshot)
    names = tuple(f.name for f in dataclasses.fields(df_state.RawSnapshot))
    assert entry.RAW_FIELDS == names == tuple(real)
    assert stub == real


@pytest.mark.parametrize(
    "argv,mode",
    [
        (["init", "--profile", "p"], "writer"),
        (["install"], "writer"),
        (["models", "set", "x"], "writer"),
        (["models"], "reader"),
        (["models", "list"], "reader"),
        (["doctor", "--json"], "reader"),
        ([], "reader"),
        (["--json", "install"], "reader"),
    ],
)
def test_mode_of(argv: list[str], mode: str) -> None:
    assert entry.mode_of(argv) == mode


# --- stdlib copies against their originals -----------------------------------


def _outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    """A comparable result: value, or the refusal kind and message."""
    try:
        return ("ok", call())
    except (TreeError, entry.TreeError) as exc:
        return ("tree", str(exc))
    except (StepFailed, entry.Refused) as exc:
        return ("refused", exc.step, exc.detail)
    except OSError as exc:
        return ("os", type(exc).__name__, exc.errno)


def _tree(root: Path, build: Callable[[Path], None]) -> Path:
    root.mkdir()
    (root / "a").mkdir()
    (root / "a" / "f").write_bytes(b"x")
    build(root)
    return root


def _fifo(r: Path) -> None:
    os.mkfifo(r / "pipe")


def _nfc_twins(r: Path) -> None:
    (r / "é").write_bytes(b"1")
    try:
        (r / unicodedata.normalize("NFD", "é")).write_bytes(b"2")
    except FileExistsError:
        pass


def _case_twins(r: Path) -> None:
    """`a/F` beside `a/f`; a case-insensitive FS refuses it (`O_EXCL`)."""
    try:
        os.close(os.open(r / "a" / "F", os.O_WRONLY | os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        pytest.skip("case-insensitive file system: case twins cannot exist")


def _unreadable(r: Path) -> None:
    (r / "secret").write_bytes(b"s")
    (r / "secret").chmod(0)


TREES: dict[str, Callable[[Path], None]] = {
    "plain": lambda r: None,
    "exec": lambda r: (r / "a" / "f").chmod(0o755),
    "rel-link": lambda r: os.symlink("a/f", r / "l"),
    "abs-link": lambda r: os.symlink("/etc/passwd", r / "l"),
    "dangling": lambda r: os.symlink("nowhere", r / "l"),
    "dir-link": lambda r: os.symlink("a", r / "l"),
    "link-newline": lambda r: os.symlink("a\nb", r / "l"),
    "name-newline": lambda r: (r / "a\nb").write_bytes(b""),
    "fifo": _fifo,
    "nfc-twins": _nfc_twins,
    "case-twins": _case_twins,
    "upper-name": lambda r: (r / "B").write_bytes(b"1"),
    "long-name": lambda r: (r / ("n" * 255)).write_bytes(b""),
    "unreadable": _unreadable,
}


@pytest.mark.parametrize("name", sorted(TREES))
def test_entry_digest_matches_df_digest(tmp_path: Path, name: str) -> None:
    root = _tree(tmp_path / "t", TREES[name])
    want = _outcome(lambda: tree_digest(root, exclude=frozenset()))
    assert _outcome(lambda: entry.tree_digest(root)) == want
    if name in ("plain", "exec", "rel-link", "case-twins", "upper-name"):
        assert want[0] == "ok"


def test_entry_digest_golden_vector(tmp_path: Path) -> None:
    """Shared golden tree (`test_digest.py`, carry FM5): `release.json` is a
    record here, since the admin digest excludes nothing."""
    root = _golden_tree(tmp_path / "golden")
    assert entry.tree_digest(root) == tree_digest(root, exclude=frozenset())
    os.unlink(root / "release.json")
    assert entry.tree_digest(root) == GOLDEN_DIGEST


@pytest.mark.parametrize("kind", ["missing", "file", "link"])
def test_entry_digest_root_refusals_match(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "t"
    if kind == "file":
        target.write_bytes(b"")
    elif kind == "link":
        (tmp_path / "d").mkdir()
        os.symlink(tmp_path / "d", target)
    want = _outcome(lambda: tree_digest(target, exclude=frozenset()))
    assert want[0] == "tree"
    assert _outcome(lambda: entry.tree_digest(target)) == want


@pytest.mark.parametrize("code", [errno.ENOENT, errno.ENOTDIR, errno.EIO, errno.EACCES])
def test_entry_digest_wraps_errors_like_df(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
) -> None:
    root = _tree(tmp_path / "t", lambda r: None)
    bad = root / "a" / "f"
    real = os.lstat

    def lstat(path: Any, *args: Any, **kw: Any) -> os.stat_result:
        if Path(path) == bad:
            raise OSError(code, os.strerror(code), str(path))
        return real(path, *args, **kw)

    monkeypatch.setattr(os, "lstat", lstat)
    want = _outcome(lambda: tree_digest(root, exclude=frozenset()))
    assert _outcome(lambda: entry.tree_digest(root)) == want


NAME_LISTS = [
    [b"a", b"b"],
    [b"A", b"a"],
    ["é".encode(), unicodedata.normalize("NFD", "é").encode()],
    [b"a\0b"],
    [b"a\nb"],
    [b"a/b"],
    [b""],
    [b"."],
    [b".."],
    [b"\xff"],
    [b"n" * 300],
    [b"ok", b"\xc3"],
]


@pytest.mark.parametrize("names", NAME_LISTS)
def test_entry_check_names_matches_df(names: list[bytes]) -> None:
    want = _outcome(lambda: check_names(names))
    assert _outcome(lambda: entry.check_names(names)) == want


def _gen_file(tmp_path: Path, content: bytes | str | None) -> Path:
    path = tmp_path / "generation"
    if content == "absent":
        return path
    if content == "link":
        (tmp_path / "t").write_text("2\n")
        os.symlink(tmp_path / "t", path)
    elif content == "dangling":
        os.symlink(tmp_path / "nowhere", path)
    elif content == "dir":
        path.mkdir()
    elif content == "fifo":
        os.mkfifo(path)
    else:
        assert isinstance(content, bytes)
        path.write_bytes(content)
    return path


GENERATION_CASES = [
    *GENERATION_MALFORMED,
    *GENERATION_CANONICAL,
    b"1\x00",
    b"\x002",
    b"1" * 64,
    b"1" * 65,
    b"7\n",
    "absent",
    "link",
    "dangling",
    "dir",
    "fifo",
]


@pytest.mark.parametrize("content", GENERATION_CASES)
def test_entry_generation_parsing_matches_journal(
    tmp_path: Path, content: bytes | str
) -> None:
    lay = _layout(tmp_path)
    lay.install_dir.mkdir(parents=True)
    _gen_file(lay.install_dir, content)
    want = _outcome(lambda: journal._read_generation(lay))
    assert _outcome(lambda: entry.read_generation(lay.generation)) == want


def _bump(lay: Layout) -> None:
    generation_begin(lay)
    generation_end(lay)


def _scenario(name: str, lay: Layout) -> Callable[[], Any]:
    """A `read` callback; each call builds fresh state for one run."""
    calls: list[int] = []
    if name == "changes-always":

        def read() -> int:
            calls.append(1)
            _bump(lay)
            return len(calls)

    elif name == "changes-once":

        def read() -> int:
            calls.append(1)
            if len(calls) == 1:
                _bump(lay)
            return len(calls)

    elif name == "aba":

        def read() -> int:
            generation_begin(lay)
            generation_end(lay)
            generation_begin(lay)
            generation_end(lay)
            return 1

    elif name == "raises":

        def read() -> int:
            raise OSError(errno.EIO, "boom")

    else:

        def read() -> int:
            return 42

    return read


@pytest.mark.parametrize(
    "name,start",
    [
        ("steady", None),
        ("steady", b"4\n"),
        ("steady", b"5\n"),
        ("changes-always", b"0\n"),
        ("changes-once", b"0\n"),
        ("aba", b"2\n"),
        ("raises", b"2\n"),
    ],
)
@pytest.mark.parametrize("attempts", [1, 3])
def test_entry_read_consistent_matches_journal(
    tmp_path: Path, name: str, start: bytes | None, attempts: int
) -> None:
    def setup(sub: str) -> Layout:
        lay = _layout(tmp_path / sub)
        lay.install_dir.mkdir(parents=True)
        if start is not None:
            lay.generation.write_bytes(start)
        return lay

    def df_side() -> tuple[Any, ...]:
        lay = setup("df")
        result = read_consistent(lay, _scenario(name, lay), attempts=attempts)
        if isinstance(result, Transitional):
            return (False, result.generation, None)
        assert isinstance(result, Stable)
        return (True, result.generation, result.value)

    def entry_side() -> tuple[Any, ...]:
        lay = setup("entry")
        got = entry.read_consistent(
            lay.generation, _scenario(name, lay), attempts=attempts
        )
        return tuple(got)

    (tmp_path / "df").mkdir()
    (tmp_path / "entry").mkdir()
    assert _outcome(entry_side) == _outcome(df_side)


def test_entry_read_consistent_rejects_zero_attempts(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        entry.read_consistent(tmp_path / "g", lambda: 1, attempts=0)


# --- lock-open hardening ----------------------------------------------------


def _lock_case(lay: Layout, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    lay.install_dir.mkdir(parents=True)
    lock = lay.lock
    if case == "existing":
        lock.write_text("")
        lock.chmod(0o600)
    elif case == "link":
        (lay.install_dir / "t").write_text("")
        os.symlink(lay.install_dir / "t", lock)
    elif case == "fifo":
        os.mkfifo(lock)
    elif case == "dir":
        lock.mkdir()
    elif case == "mode-644":
        lock.write_text("")
        lock.chmod(0o644)
    elif case == "nlink":
        lock.write_text("")
        lock.chmod(0o600)
        os.link(lock, lay.install_dir / "twin")
    elif case == "foreign-owner":
        lock.write_text("")
        lock.chmod(0o600)
        me = os.getuid()
        monkeypatch.setattr(journal, "ROOT_UID", me)
        monkeypatch.setattr(entry, "ROOT_UID", me)
        monkeypatch.setattr(os, "geteuid", lambda: me + 1)
    elif case == "foreign-dir-owner":
        me = os.getuid()
        monkeypatch.setattr(journal, "ROOT_UID", me + 1)
        monkeypatch.setattr(entry, "ROOT_UID", me + 1)
        monkeypatch.setattr(os, "geteuid", lambda: me + 2)
    elif case.startswith("dir-mode-"):
        _, _, which, bit = case.split("-")
        target = {"var": lay.root / "var", "df": lay.var, "install": lay.install_dir}
        target[which].chmod(0o755 | int(bit, 8))
    elif case == "install-link":
        os.rename(lay.install_dir, lay.var / "real")
        os.symlink(lay.var / "real", lay.install_dir)
    elif case == "df-link":
        os.rename(lay.var, lay.var.parent / "real")
        os.symlink(lay.var.parent / "real", lay.var)


LOCK_CASES = [
    "missing-file",
    "existing",
    "link",
    "fifo",
    "dir",
    "mode-644",
    "nlink",
    "foreign-owner",
    "foreign-dir-owner",
    "dir-mode-var-020",
    "dir-mode-df-002",
    "dir-mode-install-020",
    "install-link",
    "df-link",
]


def _df_lock(lay: Layout) -> int:
    with writer_lock(lay) as fd:
        return os.fstat(fd).st_mode & 0o777


def _entry_lock(lay: Layout) -> int:
    fd = entry.open_writer_lock(entry.EntryPaths(str(lay.root)))
    try:
        return os.fstat(fd).st_mode & 0o777
    finally:
        os.close(fd)


@pytest.mark.parametrize("case", LOCK_CASES)
def test_entry_lock_open_matches_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    results = []
    for side, opener in (("df", _df_lock), ("entry", _entry_lock)):
        lay = Layout(_mk(tmp_path / side))
        _lock_case(lay, case, monkeypatch)
        results.append(_outcome(lambda lay=lay, opener=opener: opener(lay)))
    df_result, entry_result = results
    assert entry_result == _rebase(df_result, tmp_path / "df", tmp_path / "entry")
    ok = case in ("missing-file", "existing")
    assert (entry_result == ("ok", 0o600)) is ok


def _mk(path: Path) -> Path:
    path.mkdir()
    return Path(os.path.realpath(path))


def _rebase(result: tuple[Any, ...], old: Path, new: Path) -> tuple[Any, ...]:
    """The df side's result with its tree's path replaced by the entry's."""
    return tuple(
        x.replace(os.path.realpath(old), os.path.realpath(new))
        if isinstance(x, str)
        else x
        for x in result
    )


def test_entry_lock_busy_refused(tmp_path: Path) -> None:
    lay = Layout(_mk(tmp_path / "w"))
    with writer_lock(lay):
        got = _outcome(lambda: _entry_lock(lay))
    assert got == ("refused", "lock", "идёт другая административная операция")


@pytest.mark.parametrize("missing", ["var", "df", "install"])
def test_entry_lock_creates_no_directory(tmp_path: Path, missing: str) -> None:
    """Deliberate difference from `writer_lock`: bootstrap creates the dirs."""
    lay = Layout(_mk(tmp_path / "w"))
    lay.install_dir.mkdir(parents=True)
    shutil.rmtree(
        {"var": lay.root / "var", "df": lay.var, "install": lay.install_dir}[missing]
    )
    got = _outcome(lambda: _entry_lock(lay))
    assert got[:2] == ("refused", "lock")
    assert got[2].endswith("каталога установки нет: восстановление через bootstrap")
    assert not lay.install_dir.exists()


def test_entry_lock_new_file_is_0600_under_umask(tmp_path: Path) -> None:
    lay = Layout(_mk(tmp_path / "w"))
    lay.install_dir.mkdir(parents=True)
    old = os.umask(0o277)
    try:
        assert _entry_lock(lay) == 0o600
    finally:
        os.umask(old)


# --- settle copy ----------------------------------------------------------------

PENDING_OK = (
    'schema = "df/pending/v1"\nrun_id = "r"\nrelease_id = "{id}"\n'
    'install_text = "t"\ninstall_digest = "' + "b" * 64 + '"\n'
)
PENDING_CASES: dict[str, str | bytes | None] = {
    "absent": None,
    "valid": PENDING_OK.format(id=ID),
    "valid-previous": PENDING_OK.format(id=ID) + 'previous_current = "x"\n',
    "bad-id": PENDING_OK.format(id="../x"),
    "id-newline": PENDING_OK.format(id="a\\nb"),
    "empty-previous": PENDING_OK.format(id=ID) + 'previous_current = ""\n',
    "bad-digest": PENDING_OK.format(id=ID).replace("b" * 64, "B" * 64),
    "bad-schema": PENDING_OK.format(id=ID).replace("pending/v1", "pending/v2"),
    "extra-key": PENDING_OK.format(id=ID) + 'x = "1"\n',
    "missing-key": PENDING_OK.format(id=ID).replace('run_id = "r"\n', ""),
    "empty-run": PENDING_OK.format(id=ID).replace('"r"', '""'),
    "non-string": PENDING_OK.format(id=ID).replace('"r"', "1"),
    "bad-toml": "schema = \n",
    "non-utf8": b'schema = "df/pending/v1"\n\xff\n',
    "link": "link",
    "fifo": "fifo",
    "dir": "dir",
}


def _write_pending(lay: Layout, content: str | bytes | None) -> None:
    lay.install_dir.mkdir(parents=True, exist_ok=True)
    if content is None:
        return
    if content == "link":
        (lay.install_dir / "p").write_text(PENDING_OK.format(id=ID))
        os.symlink(lay.install_dir / "p", lay.pending)
    elif content == "fifo":
        os.mkfifo(lay.pending)
    elif content == "dir":
        lay.pending.mkdir()
    elif isinstance(content, bytes):
        lay.pending.write_bytes(content)
    else:
        lay.pending.write_text(content)


@pytest.mark.parametrize("case", sorted(PENDING_CASES))
def test_entry_pending_id_matches_accept(tmp_path: Path, case: str) -> None:
    lay = _layout(tmp_path)
    _write_pending(lay, PENDING_CASES[case])
    want = accept._pending_id(lay)
    assert entry.pending_id(entry.EntryPaths(str(lay.root))) == want
    assert (want is not None) is case.startswith("valid")


@pytest.mark.parametrize("pending_id", [None, ID])
@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize("root_owned", [True, False])
def test_entry_acquire_paths_copy_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_id: str | None,
    trusted: bool,
    root_owned: bool,
) -> None:
    """F3: equal with `local_bin` trusted; otherwise exactly `local_bin` more."""
    lay = _layout(tmp_path)
    lay.release_dir(ID).mkdir(parents=True)
    owner = os.getuid() if root_owned else os.getuid() + 1
    monkeypatch.setattr(accept, "ROOT_UID", owner)
    monkeypatch.setattr(entry, "ROOT_UID", owner)
    monkeypatch.setattr(accept, "local_bin_trusted", lambda _lay: trusted)
    want = accept.acquire_paths(lay, pending_id)
    got = entry.acquire_paths(entry.EntryPaths(str(lay.root)), pending_id)
    if trusted:
        assert got == want
    else:
        assert set(got) - set(want) == {lay.local_bin}
        assert tuple(p for p in got if p != lay.local_bin) == want
    assert (lay.release_dir(ID) in got) is (root_owned and pending_id is not None)


def test_entry_settle_syncs_like_settle_on_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = _layout(tmp_path)
    for path in (lay.install_dir, lay.etc, lay.releases, lay.local_bin, lay.admin):
        path.mkdir(parents=True)
    lay.staging_dir(ID).mkdir(parents=True)
    _write_pending(lay, PENDING_OK.format(id=ID))
    synced: dict[str, list[Path]] = {"df": [], "entry": []}
    monkeypatch.setattr(accept.fsutil, "sync_dir", lambda p: synced["df"].append(p))
    monkeypatch.setattr(entry, "_sync_dir", lambda p: synced["entry"].append(p))
    monkeypatch.setattr(accept, "local_bin_trusted", lambda _lay: True)
    accept.settle_on_acquire(lay)
    entry.settle_on_acquire(entry.EntryPaths(str(lay.root)))
    assert synced["entry"] == synced["df"]
    assert synced["df"][0] == lay.install_dir
    assert lay.staging_dir(ID) in synced["df"]


def test_entry_sync_never_follows_a_link(tmp_path: Path) -> None:
    """A directory swapped for a link after `_is_real_dir` is not synced."""
    (tmp_path / "d").mkdir()
    os.symlink(tmp_path / "d", tmp_path / "l")
    with pytest.raises(OSError):
        entry._sync_dir(tmp_path / "l")
    entry._sync_dir(tmp_path / "d")


# --- releases.toml copy -------------------------------------------------------

REL = (
    'schema = "df/releases/v1"\n[[release]]\nid = "{id}"\ndigest = "d"\n'
    'admin_digest = "a"\nadmin_python = "3.12.13"\nstatus = "allowed"\n'
)
REVOKED = REL.format(id=OTHER).replace('"allowed"', '"revoked"') + 'reason = "r"\n'
RELEASES_CASES: dict[str, str | bytes] = {
    "valid": REL.format(id=ID),
    "two": REL.format(id=ID) + REVOKED.removeprefix('schema = "df/releases/v1"\n'),
    "empty": 'schema = "df/releases/v1"\n',
    "schema-in-row": REL.format(id=ID) + 'schema = "x"\n',
    "dup": REL.format(id=ID)
    + REL.format(id=ID).removeprefix('schema = "df/releases/v1"\n'),
    "bad-schema": REL.format(id=ID).replace("releases/v1", "releases/v2"),
    "no-schema": REL.format(id=ID).replace('schema = "df/releases/v1"\n', ""),
    "top-extra": "x = 1\n" + REL.format(id=ID),
    "not-list": 'schema = "df/releases/v1"\nrelease = "x"\n',
    "row-not-table": 'schema = "df/releases/v1"\nrelease = ["x"]\n',
    "missing": REL.format(id=ID).replace('digest = "d"\n', ""),
    "extra": REL.format(id=ID) + 'extra = "x"\n',
    "non-string": REL.format(id=ID).replace('digest = "d"', "digest = 1"),
    "empty-id": REL.format(id=""),
    "empty-admin": REL.format(id=ID).replace('admin_digest = "a"', 'admin_digest = ""'),
    "bad-python": REL.format(id=ID).replace("3.12.13", "3.12"),
    "py-unicode": REL.format(id=ID).replace("3.12.13", "3.12.١"),
    "bad-status": REL.format(id=ID).replace('"allowed"', '"maybe"'),
    "reason-allowed": REL.format(id=ID) + 'reason = "r"\n',
    "empty-reason": REVOKED.replace('reason = "r"', 'reason = ""'),
    "bad-toml": "schema = ",
    "non-utf8": b'schema = "df/releases/v1"\n# \xff\n',
}


@pytest.mark.parametrize("case", sorted(RELEASES_CASES))
def test_entry_releases_parse_matches_config(case: str) -> None:
    raw = RELEASES_CASES[case]
    data = raw if isinstance(raw, bytes) else raw.encode()

    def df_side() -> dict[str, dict[str, str]]:
        cfg = parse_releases(data.decode("utf-8"))
        return {e.id: _entry_dict(e) for e in cfg.releases}

    try:
        want: Any = df_side()
    except (ConfigError, UnicodeDecodeError):
        want = "refused"
    try:
        got: Any = {
            k: {f: v for f, v in r.items() if f != "schema"}
            for k, r in entry.parse_releases(data).items()
        }
    except entry.Refused:
        got = "refused"
    assert got == want
    assert (want != "refused") is (case in ("valid", "two", "empty", "schema-in-row"))


def _entry_dict(e: ReleaseEntry) -> dict[str, str]:
    out = {
        "id": e.id,
        "digest": e.digest,
        "admin_digest": e.admin_digest,
        "admin_python": e.admin_python,
        "status": e.status,
    }
    if e.reason is not None:
        out["reason"] = e.reason
    return out


@pytest.mark.parametrize("case", ["absent", "valid", "link", "dangling", "fifo", "dir"])
def test_entry_read_optional_matches_config(tmp_path: Path, case: str) -> None:
    from df.config import read_optional

    path = tmp_path / "f.toml"
    if case == "valid":
        path.write_bytes(b"x = 1\n")
    elif case == "link":
        (tmp_path / "t").write_text("x")
        os.symlink(tmp_path / "t", path)
    elif case == "dangling":
        os.symlink(tmp_path / "none", path)
    elif case == "fifo":
        os.mkfifo(path)
    elif case == "dir":
        path.mkdir()
    try:
        text = read_optional(path)
        want: Any = None if text is None else text.encode()
    except ConfigError:
        want = "refused"
    try:
        got: Any = entry.read_optional(path)
    except entry.Refused as exc:
        assert exc.step == "snapshot"
        got = "refused"
    assert got == want


# --- the run, in process ----------------------------------------------------------


def test_entry_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder, as_root: None
) -> None:
    lay = world(tmp_path)
    held: list[bool] = []

    def probe() -> None:
        fd = os.open(lay.lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            held.append(False)
        except BlockingIOError:
            held.append(True)
        finally:
            os.close(fd)

    rec.before = probe
    assert run_in(lay, "models", "set", "x") == 7
    assert rec.calls[-1]["mode"] == "writer" and isinstance(
        rec.calls[-1]["lock_fd"], int
    )
    assert held == [True]
    os.close(rec.calls[-1]["lock_fd"])
    rec.calls[-1]["lock_fd"] = None

    rec.before = None
    opened: list[str] = []
    real_open = os.open

    def spy(path: Any, *args: Any, **kw: Any) -> int:
        opened.append(os.fsdecode(path))
        return real_open(path, *args, **kw)

    monkeypatch.setattr(os, "open", spy)
    assert run_in(lay, "doctor") == 7
    monkeypatch.setattr(os, "open", real_open)
    assert rec.calls[-1]["mode"] == "reader" and rec.calls[-1]["lock_fd"] is None
    assert not any(os.path.basename(p) == "lock" for p in opened)

    with writer_lock(lay):
        assert run_in(lay, "install") == entry.EXIT_REFUSED
    assert len(rec.calls) == 2


def test_entry_writer_requires_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rec: Recorder,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lay = world(tmp_path)
    monkeypatch.setattr(entry, "ROOT_UID", os.geteuid() + 1)
    assert run_in(lay, "install") == entry.EXIT_REFUSED
    assert "требует root" in capsys.readouterr().err
    assert rec.calls == [] and not lay.lock.exists()


def test_entry_writer_settles_before_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder, as_root: None
) -> None:
    lay = world(tmp_path)
    lay.local_bin.mkdir(parents=True)
    events: list[tuple[str, str]] = []
    real_flock, real_readlink = fcntl.flock, os.readlink

    def flock(fd: int, op: int) -> None:
        events.append(("flock", ""))
        real_flock(fd, op)

    def readlink(path: Any, *args: Any, **kw: Any) -> Any:
        if Path(os.fsdecode(path)) == lay.current:
            events.append(("read", "current"))
        return real_readlink(path, *args, **kw)

    monkeypatch.setattr(fcntl, "flock", flock)
    monkeypatch.setattr(os, "readlink", readlink)
    monkeypatch.setattr(entry, "_sync_dir", lambda p: events.append(("sync", str(p))))
    assert run_in(lay, "install") == 7
    paths = entry.EntryPaths(str(lay.root))
    expected = [
        str(p)
        for p in entry.acquire_paths(paths, None)
        if p == lay.install_dir or entry._is_real_dir(p)
    ]
    assert events[0] == ("flock", "")
    assert [e[1] for e in events[1:-1]] == expected
    assert str(lay.local_bin) in expected
    assert events[-1] == ("read", "current")

    events.clear()
    assert run_in(lay, "doctor") == 7
    assert [e for e in events if e[0] != "read"] == []


def test_entry_resolves_current_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder, as_root: None
) -> None:
    lay = world(tmp_path)
    write_registry(
        lay,
        [record(ID, admin.admin_digest(lay, ID)), record(OTHER, "sha256:" + "0" * 64)],
    )
    real = os.readlink
    seen: list[int] = []

    def readlink(path: Any, *args: Any, **kw: Any) -> Any:
        if Path(os.fsdecode(path)) == lay.current:
            seen.append(1)
            if len(seen) > 1:
                return f"releases/{OTHER}"
        return real(path, *args, **kw)

    monkeypatch.setattr(os, "readlink", readlink)
    for argv in (["install"], ["doctor"]):
        seen.clear()
        assert run_in(lay, *argv) == 7
        assert seen == [1]
        assert rec.admin_dirs[-1] == lay.admin_dir(ID)
        assert rec.calls[-1]["read"].value.current_id == ID


def test_entry_refuses_revoked_and_pending(
    tmp_path: Path, rec: Recorder, as_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    lay = world(tmp_path / "revoked", status="revoked")
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert "отозван" in capsys.readouterr().err

    lay = world(tmp_path / "unknown")
    write_registry(lay, [record(OTHER, "sha256:" + "0" * 64)])
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert "не зарегистрирован" in capsys.readouterr().err

    lay = world(tmp_path / "nocurrent")
    os.unlink(lay.current)
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert "bootstrap" in capsys.readouterr().err

    lay = world(tmp_path / "foreign")
    os.unlink(lay.current)
    os.symlink(f"../../elsewhere/{ID}", lay.current)
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert "чужая цель" in capsys.readouterr().err
    assert rec.calls == []

    # pending: not the entry's refusal (spec 3.9 item 3) — the command decides
    lay = world(tmp_path / "pending", pending=True)
    assert run_in(lay, "doctor") == 7
    assert rec.calls[-1]["read"] == STATE.Transitional(2)
    assert run_in(lay, "install") == 7
    read = rec.calls[-1]["read"]
    assert isinstance(read, STATE.Stable) and read.value.pending_present is True


def test_entry_reader_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    lay = world(tmp_path)
    real = entry.read_parts
    reads: list[int] = []

    def changing(paths: entry.EntryPaths) -> dict[str, Any]:
        reads.append(1)
        parts = real(paths)
        _bump(lay)
        return parts

    monkeypatch.setattr(entry, "read_parts", changing)
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert len(reads) == 3 and rec.calls == []  # spec 3.3: three attempts

    monkeypatch.setattr(entry, "read_parts", real)
    lay.generation.write_bytes(b"3\n")  # an interrupted writer
    assert run_in(lay, "doctor") == 7
    assert rec.calls[-1]["read"] == STATE.Transitional(3)
    assert rec.admin_dirs[-1] == lay.admin_dir(ID)

    lay.generation.write_bytes(b"4\n")
    lay.pending.write_text("x")
    assert run_in(lay, "status") == 7
    assert rec.calls[-1]["read"] == STATE.Transitional(4)


def test_entry_writer_odd_generation_is_stable(
    tmp_path: Path, rec: Recorder, as_root: None
) -> None:
    lay = world(tmp_path, generation=b"5\n")
    assert run_in(lay, "install") == 7
    read = rec.calls[-1]["read"]
    assert isinstance(read, STATE.Stable) and read.generation == 5


def test_entry_passes_full_snapshot(
    tmp_path: Path, rec: Recorder, as_root: None
) -> None:
    """Entry half (F4): a change after the read does not reach `main`; that
    `main` re-reads nothing is Task 9's."""
    for argv in (["install"], ["doctor"]):
        lay = world(tmp_path / argv[0])
        digest = write_admin(lay, OTHER)
        write_registry(
            lay, [record(ID, admin.admin_digest(lay, ID)), record(OTHER, digest)]
        )
        releases = (lay.etc / "releases.toml").read_bytes()

        def install_runs(lay: Layout = lay) -> None:
            set_current(lay, OTHER)
            (lay.etc / "install.toml").write_bytes(b"changed")
            (lay.etc / "models.toml").unlink()

        rec.before = install_runs
        assert run_in(lay, *argv) == 7
        raw = rec.calls[-1]["read"].value
        assert raw == STATE.RawSnapshot(
            generation=2,
            current_id=ID,
            releases_text=releases,
            install_text=INSTALL_TEXT,
            models_text=MODELS_TEXT,
            pending_present=False,
        )
        assert rec.calls[-1]["read"].generation == 2


def test_entry_refuses_bad_snapshot_parts(
    tmp_path: Path, rec: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    lay = world(tmp_path)
    install = lay.etc / "install.toml"
    install.unlink()
    os.mkfifo(install)
    assert run_in(lay, "doctor") == entry.EXIT_REFUSED
    assert "not a regular file" in capsys.readouterr().err
    assert rec.calls == []


def test_entry_hands_its_root_layout_to_main(tmp_path: Path, rec: Recorder) -> None:
    """D2: `main` gets the admin code's `Layout` of the entry's own root."""
    lay = world(tmp_path)
    assert run_in(lay, "doctor") == 7
    assert rec.calls[-1]["layout"] == lay


def test_entry_one_root_through_real_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D2: through the real `df.cli.main`, a reader and a writer of a non-"/"
    root work on that root's layout; `default_layout()` is never reached."""
    from df import cli

    def no_default() -> Layout:
        raise AssertionError("default_layout() reached under the entry")

    seen: list[tuple[str, Layout]] = []
    real_reader = cli._reader  # noqa: SLF001

    def reader(args: Any, layout: Layout, read: Any) -> int:
        seen.append(("reader", layout))
        return real_reader(args, layout, read)

    def writer(args: Any, layout: Layout, raw: Any, mode: str) -> int:
        seen.append(("writer", layout))
        return 0

    monkeypatch.setattr(cli, "default_layout", no_default)
    monkeypatch.setattr(cli, "_reader", reader)
    monkeypatch.setattr(cli, "_writer", writer)
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())

    def main(argv: list[str], **kw: Any) -> int:
        try:
            return cli.main(argv, **kw)
        finally:  # the process would exit here, releasing the writer lock
            if kw["lock_fd"] is not None:
                os.close(kw["lock_fd"])

    monkeypatch.setattr(entry, "_import_df", lambda _d: (STATE, main, Layout))
    lay = world(tmp_path)
    code = run_in(lay, "doctor", "--json")
    assert code in (cli.EXIT_OK, cli.EXIT_REFUSED), code
    assert "ошибка программы" not in capsys.readouterr().err  # not the D2 guard
    setter = ["models", "set", "author", "--harness", "claude", "--model", "m"]
    assert run_in(lay, *setter) == cli.EXIT_OK
    assert "ошибка программы" not in capsys.readouterr().err
    assert seen == [("reader", lay), ("writer", lay)]


@pytest.mark.parametrize("root", ["relative/root", "/a/../b"])
def test_entry_layout_refuses_a_root_the_admin_code_refuses(root: str) -> None:
    """The admin code's `Layout` refuses a non-canonical root: the entry
    turns that into its own refusal, never a traceback."""
    with pytest.raises(entry.Refused, match="не принят"):
        entry._entry_layout(Layout, root)  # noqa: SLF001


def test_import_guard_refuses_preloaded_df(tmp_path: Path) -> None:
    """In this process `df` is loaded: the real `_import_df` refuses."""
    with pytest.raises(entry.Refused, match="загружен до проверки"):
        entry._import_df(tmp_path)
    assert str(tmp_path) not in sys.path


# --- the run, as the installed command ------------------------------------------


def test_entry_subprocess_reader(tmp_path: Path) -> None:
    lay = world(tmp_path)
    done = run_entry(lay, "doctor", "--json")
    assert done.returncode == 7, done.stderr
    call = main_call(done)
    assert call["mode"] == "reader" and call["lock_fd"] is None
    assert call["kind"] == "Stable" and call["generation"] == 2
    assert call["path0"] == str(lay.admin_dir(ID))
    assert call["state_file"] == str(lay.admin_dir(ID) / "df" / "state.py")
    assert call["raw"]["current_id"] == ID
    assert call["raw"]["install_text"] == INSTALL_TEXT.decode()
    assert call["argv"] == ["doctor", "--json"]
    assert call["layout"] == ["df.paths", str(lay.root)]  # D2: the entry's root


def test_entry_reader_transitional_reaches_main(tmp_path: Path) -> None:
    """F4: the entry half of `test_doctor_transitional_via_public_entry`; the
    doctor JSON is Task 12's."""
    lay = world(tmp_path, generation=b"3\n")
    done = run_entry(lay, "doctor", "--json")
    assert done.returncode == 7, done.stderr
    call = main_call(done)
    assert (call["kind"], call["generation"]) == ("Transitional", 3)


def test_admin_digest_mismatch_blocks_entry(tmp_path: Path) -> None:
    lay = world(tmp_path)
    cli = lay.admin_dir(ID) / "df" / "cli.py"
    cli.write_text(cli.read_text() + "\n# changed\n")
    done = run_entry(lay, "doctor")
    assert done.returncode == entry.EXIT_REFUSED
    assert "административный код повреждён" in done.stderr
    assert "IMPORTED-CLI" not in done.stdout


def test_entry_refuses_without_isolation_flags(tmp_path: Path) -> None:
    lay = world(tmp_path)
    for flags in ("", "-IB", "-IS", "-BS"):
        done = run_entry(lay, "doctor", flags=flags)
        assert done.returncode == entry.EXIT_REFUSED, flags
        assert "IMPORTED-CLI" not in done.stdout


def test_entry_writes_no_bytecode(tmp_path: Path) -> None:
    lay = world(tmp_path)
    before = admin.admin_digest(lay, ID)
    for _ in range(2):
        assert run_entry(lay, "doctor").returncode == 7
    assert admin.admin_digest(lay, ID) == before
    assert not list(lay.admin_dir(ID).rglob("__pycache__"))


@pytest.fixture
def foreign_python(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway interpreter whose site-packages holds a foreign `df` and a
    `.pth` with an import (never the dev venv)."""
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        check=True,
        capture_output=True,
    )
    site = next(venv.glob("lib/python*/site-packages"))
    marker = tmp_path / "pth-ran"
    (site / "evil.pth").write_text(f"import os; open({str(marker)!r}, 'w').close()\n")
    (site / "df").mkdir()
    (site / "df" / "__init__.py").write_text("print('FOREIGN-DF')\n")
    return venv / "bin" / "python", marker


def test_entry_ignores_foreign_df_and_pth(
    tmp_path: Path, foreign_python: tuple[Path, Path]
) -> None:
    python, marker = foreign_python
    lay = world(tmp_path / "w")
    done = run_entry(lay, "doctor", python=str(python))
    assert done.returncode == 7, done.stderr
    assert "FOREIGN-DF" not in done.stdout and not marker.exists()
    assert main_call(done)["path0"] == str(lay.admin_dir(ID))
    # positive control: without -S the same interpreter runs the .pth
    subprocess.run([str(python), "-I", "-c", "pass"], check=True)
    assert marker.exists()


# --- bootstrap ----------------------------------------------------------------

STUB_BOOT_CLI = """\
import json, sys
def main(argv, *, mode, read, lock_fd):
    print("MAIN " + json.dumps({"argv": list(argv), "mode": mode,
          "read": read, "lock_fd": lock_fd, "path0": sys.path[0]}))
    return 7
"""


def _checkout(tmp_path: Path) -> Path:
    pkg = tmp_path / "co" / "df"
    pkg.mkdir(parents=True)
    shutil.copy(bootstrap.__file__, pkg / "bootstrap.py")
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(STUB_BOOT_CLI)
    return pkg / "bootstrap.py"


def _boot(
    script: Path, *args: str, python: str = sys.executable, flags: str = "-IBS"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python, *([flags] if flags else []), str(script), *args],
        cwd="/",
        env={"PATH": "/usr/bin:/bin"},
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("cmd", ["init", "install"])
def test_bootstrap_allows_init_install(tmp_path: Path, cmd: str) -> None:
    script = _checkout(tmp_path)
    done = _boot(script, cmd, "--x")
    assert done.returncode == 7, done.stderr
    call = main_call(done)
    assert call == {
        "argv": [cmd, "--x"],
        "mode": "bootstrap",
        "read": None,
        "lock_fd": None,
        "path0": os.path.realpath(tmp_path / "co"),
    }


@pytest.mark.parametrize("argv", [[], ["doctor"], ["models", "set"], ["--recover"]])
def test_bootstrap_allows_only_init_install(tmp_path: Path, argv: list[str]) -> None:
    script = _checkout(tmp_path)
    done = _boot(script, *argv)
    assert done.returncode == bootstrap.EXIT_REFUSED
    assert "MAIN" not in done.stdout and "init или install" in done.stderr


@pytest.mark.parametrize("flags", ["", "-I", "-IB", "-IS"])
def test_bootstrap_requires_isolation_flags(tmp_path: Path, flags: str) -> None:
    script = _checkout(tmp_path)
    done = _boot(script, "install", flags=flags)
    assert done.returncode == bootstrap.EXIT_REFUSED
    assert "MAIN" not in done.stdout and "-I -B -S" in done.stderr


def test_bootstrap_root_from_own_path_through_link(tmp_path: Path) -> None:
    script = _checkout(tmp_path)
    os.symlink(script, tmp_path / "boot.py")
    done = _boot(tmp_path / "boot.py", "install")
    assert main_call(done)["path0"] == os.path.realpath(tmp_path / "co")


def test_bootstrap_parses_under_python39() -> None:
    source = Path(bootstrap.__file__).read_text()
    ast.parse(source, feature_version=(3, 9))


def test_bootstrap_refuses_old_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    assert bootstrap.refusal(["install"]) == "нужен Python >= 3.12"


SYSTEM_PY = "/usr/bin/python3"


@pytest.mark.skipif(not os.access(SYSTEM_PY, os.X_OK), reason="no system python3")
def test_bootstrap_under_system_python_says_what_is_needed(tmp_path: Path) -> None:
    probe = subprocess.run(
        [SYSTEM_PY, "-I", "-c", "import sys; print(sys.version_info >= (3, 12))"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "False":
        pytest.skip("system python3 is >= 3.12 or unusable")
    done = _boot(_checkout(tmp_path), "install", python=SYSTEM_PY)
    assert done.returncode == bootstrap.EXIT_REFUSED
    assert "Python >= 3.12" in done.stderr


# --- fix round 1: trust of the admin code (I-1a), import failure (M-2), n-2 ------


def _spoil(lay: Layout, spoil: str) -> None:
    pkg = lay.admin_dir(ID) / "df"
    if spoil == "world-writable":  # reviewer probe P1
        (pkg / "cli.py").chmod(0o666)
        pkg.chmod(0o777)
    elif spoil == "group-writable":
        pkg.chmod(0o775)
    elif spoil == "admin-link":  # reviewer probe P3
        os.rename(lay.admin, lay.opt / "elsewhere")
        os.symlink(lay.opt / "elsewhere", lay.admin)
    elif spoil == "inner-link":
        os.symlink("cli.py", pkg / "alias.py")
    elif spoil == "opt-writable":
        lay.opt.chmod(0o777)
    elif spoil == "bytecode":  # reviewer probe Q2
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "cli.cpython-313.pyc").write_bytes(b"\0" * 16)
    elif spoil == "bytecode-upper":
        (pkg / "X.PYC").write_bytes(b"\0" * 16)


TRUST_SPOILS = [
    "world-writable",
    "group-writable",
    "admin-link",
    "inner-link",
    "opt-writable",
    "bytecode",
    "bytecode-upper",
]


@pytest.mark.parametrize("spoil", TRUST_SPOILS)
def test_untrusted_admin_code_blocks_entry(tmp_path: Path, spoil: str) -> None:
    """I-1(a): the digest agrees (re-registered), the tree is not root-only."""
    lay = world(tmp_path)
    _spoil(lay, spoil)
    write_registry(lay, [record(ID, admin.admin_digest(lay, ID))])
    done = run_entry(lay, "doctor")
    assert done.returncode == entry.EXIT_REFUSED, done.stdout
    assert "не защищён от записи" in done.stderr
    assert "bootstrap" in done.stderr and "IMPORTED-CLI" not in done.stdout


@pytest.mark.parametrize("spoil", [*TRUST_SPOILS, "foreign-owner", "fifo"])
def test_admin_tree_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spoil: str
) -> None:
    lay = world(tmp_path)
    paths = entry.EntryPaths(str(lay.root))
    assert entry.admin_tree_problems(paths, ID) == []  # positive control
    target = lay.admin_dir(ID) / "df" / "state.py"
    if spoil == "foreign-owner":
        real = entry.owner_uid
        foreign = os.lstat(target).st_ino

        def owner(st: os.stat_result) -> int:
            return os.geteuid() + 1 if st.st_ino == foreign else real(st)

        monkeypatch.setattr(entry, "owner_uid", owner)
    elif spoil == "fifo":
        os.mkfifo(lay.admin_dir(ID) / "df" / "pipe")
    else:
        _spoil(lay, spoil)
    problems = entry.admin_tree_problems(paths, ID)
    assert problems, spoil


def test_entry_import_failure_is_refusal(tmp_path: Path) -> None:
    """M-2 (reviewer probe P5): code matching its digest that fails to import
    exits 2 with the bootstrap hint, not 1 with a traceback."""
    lay = world(tmp_path)
    (lay.admin_dir(ID) / "df" / "cli.py").write_text("def (:\n")
    write_registry(lay, [record(ID, admin.admin_digest(lay, ID))])
    done = run_entry(lay, "doctor")
    assert done.returncode == entry.EXIT_REFUSED
    assert "административный код повреждён" in done.stderr
    assert "SyntaxError" in done.stderr and "Traceback" not in done.stderr


def test_reader_digest_failure_during_change_says_changing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """n-2: an activation between the snapshot and the digest is «состояние
    меняется», not «повреждён»; with the generation unchanged it stays «повреждён»."""
    lay = world(tmp_path)

    def racing(_root: Path) -> str:
        _bump(lay)
        raise entry.TreeError("vanished")

    monkeypatch.setattr(entry, "tree_digest", racing)
    err = _stderr_of(lambda: run_in(lay, "doctor"))
    assert "состояние меняется" in err

    def broken(_root: Path) -> str:
        raise entry.TreeError("vanished")

    monkeypatch.setattr(entry, "tree_digest", broken)
    err = _stderr_of(lambda: run_in(lay, "doctor"))
    assert "повреждён" in err
    assert rec.calls == []


def _stderr_of(call: Callable[[], int]) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        assert call() == entry.EXIT_REFUSED
    return buf.getvalue()


# --- fix round 1 minors: the admin interpreter (M-4), SystemExit (n-7) ------------


def _spoil_python(lay: Layout, spoil: str) -> str:
    exe = interpreter(lay)
    if spoil == "outside":
        return sys.executable
    if spoil == "link":
        exe.rename(exe.with_name("real"))
        exe.symlink_to("real")
    elif spoil == "bin-writable":
        exe.parent.chmod(0o775)
    elif spoil == "file-writable":
        exe.chmod(0o777)
    elif spoil == "admin-python-link":
        os.rename(lay.admin_python, lay.opt / "py-elsewhere")
        os.symlink(lay.opt / "py-elsewhere", lay.admin_python)
    elif spoil == "missing":
        exe.unlink()
    return str(exe)


PYTHON_SPOILS = [
    "outside",
    "link",
    "bin-writable",
    "file-writable",
    "admin-python-link",
    "missing",
]


@pytest.mark.parametrize("spoil", PYTHON_SPOILS)
def test_interpreter_problems(tmp_path: Path, spoil: str) -> None:
    lay = world(tmp_path)
    paths = entry.EntryPaths(str(lay.root))
    assert entry.interpreter_problems(paths, str(interpreter(lay))) == []
    assert entry.interpreter_problems(paths, _spoil_python(lay, spoil))


@pytest.mark.parametrize("spoil", ["file-writable", "admin-python-link"])
def test_untrusted_interpreter_blocks_entry(tmp_path: Path, spoil: str) -> None:
    lay = world(tmp_path)
    _spoil_python(lay, spoil)
    done = run_entry(lay, "doctor")
    assert done.returncode == entry.EXIT_REFUSED, done.stdout
    assert "административный Python не защищён" in done.stderr
    assert "IMPORTED-CLI" not in done.stdout


def test_entry_import_system_exit_is_refusal(tmp_path: Path) -> None:
    """n-7: `SystemExit` raised while importing verified code is the same
    refusal (exit 2 with the hint), not its own exit code."""
    lay = world(tmp_path)
    (lay.admin_dir(ID) / "df" / "cli.py").write_text("raise SystemExit(5)\n")
    write_registry(lay, [record(ID, admin.admin_digest(lay, ID))])
    done = run_entry(lay, "doctor")
    assert done.returncode == entry.EXIT_REFUSED
    assert "административный код повреждён" in done.stderr
