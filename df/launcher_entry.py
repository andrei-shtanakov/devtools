"""Entry file of the `darkfactory` command (spec 3.9, 3.3).

`bin/darkfactory` runs this file as `<admin python> -I -B -S <entry> ...`. It
is the first code of the administrative command: it decides the mode, takes
the writer lock (writer) and settles, reads the full snapshot, finds the
active release's administrative code, checks its digest, and only then
imports `df` from that code and calls `df.cli.main`.

Until that import nothing but stdlib may run, so the parts of `df` the entry
needs are copied here. A test runs each copy and its original on the same
inputs, hostile ones included (`tests/df/test_launcher_entry.py`):

- `tree_digest` with nothing excluded and `check_names` (`df.digest`);
- generation parsing and `read_consistent` (`df.journal`);
- the hardened lock open of `df.journal.writer_lock`, except that the entry
  creates no directory: a missing one is a refusal (bootstrap creates them);
- `settle_on_acquire`, `acquire_paths` and `_pending_id` (`df.accept`), with
  one deliberate superset (F3): `local_bin` is synced whenever it is a real
  directory, so the entry needs no copy of the trusted-path policy;
- the hardened reader of `df.config` (bytes, not decoded) and the strict rules
  of `df.config.parse_releases`.

`entry_text(layout)` is this module's own text with its `_ROOT` line set to
the layout root; the installer writes it to `bin/darkfactory-entry.py` and
`df.admin.launcher_ready` compares it byte for byte.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import importlib
import os
import pathlib
import re
import stat
import sys
import tomllib
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from df.paths import Layout

#: The layout root of the installed entry; `entry_text` substitutes this line.
_ROOT = "/"
_ROOT_LINE = '_ROOT = "/"\n'

#: Parts of `df.state.RawSnapshot` (Task 9) the entry reads before `df` loads.
RAW_FIELDS = (
    "generation",
    "current_id",
    "releases_text",
    "install_text",
    "models_text",
    "pending_present",
)
#: Subcommands run as writers (spec 3.3): root only, exclusive lock.
WRITER_COMMANDS = (("init",), ("install",), ("models", "set"))
#: Owner of root's objects; tests substitute their own uid.
ROOT_UID = 0
EXIT_REFUSED = 2
ATTEMPTS = 3

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CURRENT_PREFIX = "releases/"
_NO_CODE = "административный код повреждён: восстановление через bootstrap"
_UNTRUSTED = "административный код не защищён от записи: восстановление через bootstrap"


def entry_text(layout: Layout) -> str:
    """The text of `bin/darkfactory-entry.py` for `layout` (F14).

    This module's own source with its single `_ROOT` line set to the layout
    root; not exactly one such line is an `AssertionError`.
    """
    lines = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines(True)
    found = [i for i, line in enumerate(lines) if line == _ROOT_LINE]
    if len(found) != 1:
        raise AssertionError(f"entry text: {len(found)} lines {_ROOT_LINE!r}")
    lines[found[0]] = f"_ROOT = {str(layout.root)!r}\n"
    return "".join(lines)


class Refused(Exception):
    """The entry refuses before `df` is imported (copy of `StepFailed`)."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


class TreeError(Exception):
    """A tree is refused (copy of `df.digest.TreeError`)."""


class EntryPaths:
    """The `Layout` paths of the entry, from one root (a test compares them)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.opt = self.root / "opt" / "df"
        self.etc = self.root / "etc" / "df"
        self.var = self.root / "var" / "lib" / "df"
        self.install_dir = self.var / "install"
        self.lock = self.install_dir / "lock"
        self.generation = self.install_dir / "generation"
        self.pending = self.install_dir / "pending.toml"
        self.marks_dir = self.install_dir / "accept"
        self.current = self.opt / "current"
        self.releases = self.opt / "releases"
        self.staging = self.opt / "staging"
        self.quarantine = self.staging / ".quarantine"
        self.admin = self.opt / "admin"
        self.admin_python = self.opt / "admin-python"
        self.bin_dir = self.opt / "bin"
        self.local_bin = self.root / "usr" / "local" / "bin"
        home_base = "Users" if sys.platform == "darwin" else "home"
        self.home_parent = self.root / home_base
        self.releases_toml = self.etc / "releases.toml"
        self.install_toml = self.etc / "install.toml"
        self.models_toml = self.etc / "models.toml"

    def release_dir(self, release_id: str) -> Path:
        """`releases/<id>`."""
        return self.releases / _checked_id(release_id)

    def staging_dir(self, release_id: str) -> Path:
        """`staging/<id>`."""
        return self.staging / _checked_id(release_id)

    def admin_dir(self, release_id: str) -> Path:
        """`admin/<id>`."""
        return self.admin / _checked_id(release_id)


def owner_uid(st: os.stat_result) -> int:
    """The entry's one owner seam (copy of `df.fsutil.owner_uid`)."""
    return st.st_uid


def _checked_id(release_id: str) -> str:
    if not _ID.fullmatch(release_id):
        raise Refused("entry", f"invalid release id: {release_id!r}")
    return release_id


# --- the run ----------------------------------------------------------------


class Snap(NamedTuple):
    """What the entry read: `stable` false means `Transitional(generation)`.

    `parts` holds every `RAW_FIELDS` part but `generation` when stable; in a
    transitional state at least `current_id` and `releases_text` (§3.3: the
    entry still finds the code).
    """

    stable: bool
    generation: int
    parts: dict[str, Any]


def mode_of(argv: list[str]) -> str:
    """`writer` for `WRITER_COMMANDS`, else `reader` (spec 3.9 item 1)."""
    if any(tuple(argv[: len(cmd)]) == cmd for cmd in WRITER_COMMANDS):
        return "writer"
    return "reader"


def run(argv: list[str], root: str = _ROOT) -> int:
    """The entry (spec 3.9 items 0-5); refusals print and return 2."""
    paths = EntryPaths(root)
    mode = mode_of(argv)
    lock_fd: int | None = None
    try:
        if mode == "writer":
            lock_fd = acquire(paths)
        snap = take_snapshot(paths, mode)
        admin_dir = located_admin_dir(paths, snap, mode)
        state, main = _import_df(admin_dir)
    except (Refused, OSError) as exc:
        if lock_fd is not None:
            os.close(lock_fd)
        print(f"darkfactory: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    return main(argv, mode=mode, read=typed_read(state, snap), lock_fd=lock_fd)


def acquire(paths: EntryPaths) -> int:
    """Writer: root, then the lock (kept open), then `settle_on_acquire`."""
    if os.geteuid() != ROOT_UID:
        raise Refused("lock", "команда-писатель требует root")
    fd = open_writer_lock(paths)
    try:
        settle_on_acquire(paths)
    except BaseException:
        os.close(fd)
        raise
    return fd


def take_snapshot(paths: EntryPaths, mode: str) -> Snap:
    """One read under the lock (writer) or `read_consistent` (reader)."""
    if mode == "writer":
        generation = read_generation(paths.generation)
        return Snap(True, generation, read_parts(paths))
    result = read_consistent(paths.generation, lambda: read_parts(paths))
    if not result.stable:
        return Snap(False, result.generation, locate_parts(paths))
    parts = result.value
    return Snap(not parts["pending_present"], result.generation, parts)


def read_parts(paths: EntryPaths) -> dict[str, Any]:
    """Every snapshot part but the generation; `current` is read once."""
    return {
        "current_id": read_current(paths),
        "releases_text": read_optional(paths.releases_toml),
        "install_text": read_optional(paths.install_toml),
        "models_text": read_optional(paths.models_toml),
        "pending_present": _lexists(paths.pending),
    }


def locate_parts(paths: EntryPaths) -> dict[str, Any]:
    """Transitional state: only what finds the code (`current`, registry)."""
    return {
        "current_id": read_current(paths),
        "releases_text": read_optional(paths.releases_toml),
    }


def read_current(paths: EntryPaths) -> str | None:
    """The id of `current` (relative link `releases/<id>`); absent -> None."""
    try:
        text = os.readlink(paths.current)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refused("snapshot", f"{paths.current}: не ссылка: {_why(exc)}") from exc
    release_id = text.removeprefix(_CURRENT_PREFIX)
    if not text.startswith(_CURRENT_PREFIX) or not _ID.fullmatch(release_id):
        raise Refused("snapshot", f"{paths.current}: чужая цель {text!r}")
    return release_id


def located_admin_dir(paths: EntryPaths, snap: Snap, mode: str) -> Path:
    """`verified_admin_dir`; a reader's refusal under a changed generation is
    «состояние меняется», not «повреждён» (an activation landed meanwhile)."""
    try:
        return verified_admin_dir(paths, snap)
    except Refused:
        if mode == "reader" and read_generation(paths.generation) != snap.generation:
            raise Refused("snapshot", _CHANGING) from None
        raise


def verified_admin_dir(paths: EntryPaths, snap: Snap) -> Path:
    """`admin/<id>` of `current` if its record is `allowed`, its tree is
    trusted (`admin_tree_problems`) and digests agree — in that order, so the
    digest reads a tree only root can change."""
    release_id = snap.parts["current_id"]
    if release_id is None:
        raise Refused("entry", "нет активного релиза: установка через bootstrap")
    text = snap.parts["releases_text"]
    if text is None:
        raise Refused("entry", f"{paths.releases_toml}: нет реестра релизов")
    record = parse_releases(text).get(release_id)
    if record is None:
        raise Refused("entry", f"релиз {release_id} не зарегистрирован")
    if record["status"] != "allowed":
        raise Refused("entry", f"релиз {release_id} отозван: {record.get('reason')}")
    admin_dir = paths.admin_dir(release_id)
    try:
        problems = admin_tree_problems(paths, release_id)
    except OSError as exc:
        raise Refused("entry", f"{_UNTRUSTED} ({exc})") from exc
    if problems:
        raise Refused("entry", f"{_UNTRUSTED} ({'; '.join(problems)})")
    try:
        digest = tree_digest(admin_dir)
    except (TreeError, OSError) as exc:
        raise Refused("entry", f"{_NO_CODE} ({exc})") from exc
    if digest != record["admin_digest"]:
        raise Refused("entry", _NO_CODE)
    return admin_dir


def admin_tree_problems(paths: EntryPaths, release_id: str) -> list[str]:
    """Trust of the admin code before root imports it (I-1).

    Every directory on the chain `root … /opt/df/admin/<id>` is a real
    directory (`lstat`, never a link), root's, without group or other write;
    every object in `admin/<id>` is a directory or a regular file, root's,
    without group or other write. Links are refused although `tree_digest`
    digests them: `install_admin` copies none. With this, only root can
    change the tree between the digest and the import.

    ACLs are not read here (no ctypes in the entry): only the owner — root —
    can change the mode or ACL of a root-owned object, and an ACL present
    from creation (inherited from a directory on the chain) makes fact `A`
    false (`df.admin.admin_code_problems`, `trusted_path_problems`), so such
    a tree never becomes the active release. A vanished object is a
    problem; any other `OSError` propagates.
    """
    admin_dir = paths.admin_dir(release_id)
    chain = [paths.root]
    for part in admin_dir.relative_to(paths.root).parts:
        chain.append(chain[-1] / part)
    problems: list[str] = []
    for path in chain:
        st = _trust_lstat(path)
        if st is None or not stat.S_ISDIR(st.st_mode):
            return [*problems, f"{path}: не каталог (ссылка или нет)"]
        problems += _trust_problems(path, st)
    return problems + _tree_trust_problems(admin_dir)


def _tree_trust_problems(directory: Path) -> list[str]:
    with os.scandir(directory) as entries:
        listing = sorted(entries, key=lambda entry: entry.name)
    problems: list[str] = []
    for entry in listing:
        path = Path(entry.path)
        st = _trust_lstat(path)
        if st is None:
            problems.append(f"{path}: исчез")
        elif stat.S_ISLNK(st.st_mode):
            problems.append(f"{path}: ссылка")
        elif stat.S_ISDIR(st.st_mode):
            problems += _trust_problems(path, st) + _tree_trust_problems(path)
        elif stat.S_ISREG(st.st_mode):
            problems += _trust_problems(path, st)
        else:
            problems.append(f"{path}: не файл и не каталог")
    return problems


def _trust_lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _trust_problems(path: Path, st: os.stat_result) -> list[str]:
    problems: list[str] = []
    if owner_uid(st) != ROOT_UID:
        problems.append(f"{path}: владелец uid {owner_uid(st)}, не root")
    if st.st_mode & _NO_WRITE_OTHERS:
        problems.append(f"{path}: запись для группы или всех")
    return problems


def _import_df(admin_dir: Path) -> tuple[Any, Callable[..., int]]:
    """Import `df.state` and `df.cli` from the checked `admin_dir` only."""
    if any(name == "df" or name.startswith("df.") for name in sys.modules):
        raise Refused("entry", "пакет df загружен до проверки административного кода")
    sys.path.insert(0, str(admin_dir))
    try:
        state = importlib.import_module("df.state")
        cli = importlib.import_module("df.cli")
    except Exception as exc:  # noqa: BLE001 - any import failure is broken code (M-2)
        raise Refused("entry", f"{_NO_CODE} ({exc!r})") from exc
    if Path(str(cli.__file__)).parent != admin_dir / "df":
        raise Refused("entry", f"df.cli не из {admin_dir}")
    return state, cli.main


def typed_read(state: Any, snap: Snap) -> Any:
    """`Transitional(g)` or `Stable(RawSnapshot(...), g)` of the loaded `df`."""
    if not snap.stable:
        return state.Transitional(snap.generation)
    raw = state.RawSnapshot(generation=snap.generation, **snap.parts)
    return state.Stable(raw, snap.generation)


# --- copy of df.config: hardened reader, releases.toml ----------------------

_READ_OPTIONAL_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_TOML_ERRORS = (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError)
_NUM = r"(0|[1-9][0-9]*)"
_PY_VERSION = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASES_SCHEMA = "df/releases/v1"
_ENTRY_REQUIRED = frozenset({"id", "digest", "admin_digest", "admin_python", "status"})
_PENDING_SCHEMA = "df/pending/v1"
_PENDING_REQUIRED = frozenset(
    {"run_id", "release_id", "install_text", "install_digest"}
)


def read_optional(path: Path) -> bytes | None:
    """`df.config.read_optional` on bytes: None only if absent."""
    try:
        fd = os.open(path, _READ_OPTIONAL_FLAGS)
    except FileNotFoundError:
        if os.path.islink(path):
            raise Refused("snapshot", f"{path}: dangling symbolic link") from None
        return None
    except OSError as exc:
        raise Refused("snapshot", f"{path}: cannot read: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("snapshot", f"{path}: not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read()
    except OSError as exc:
        raise Refused("snapshot", f"{path}: cannot read: {exc}") from exc
    finally:
        os.close(fd)


def parse_releases(text: bytes) -> dict[str, dict[str, str]]:
    """Records of `releases.toml` by id, after `df.config.parse_releases`."""
    data = _toml(text, _RELEASES_SCHEMA)
    _keys_ok(data, frozenset(), frozenset({"release"}), "releases.toml")
    rows = data.get("release", [])
    _need(isinstance(rows, list), "release: must be an array of tables")
    records: dict[str, dict[str, str]] = {}
    for row in rows:
        record = _release_row(row)
        _need(record["id"] not in records, f"release: duplicate id {record['id']!r}")
        records[record["id"]] = record
    return records


def _release_row(row: object) -> dict[str, str]:
    _need(isinstance(row, dict), "release: must be a table")
    assert isinstance(row, dict)
    _keys_ok(row, _ENTRY_REQUIRED, frozenset({"reason"}), "release")
    for key, value in row.items():
        _need(isinstance(value, str), f"{key}: must be a string")
    for key in ("id", "digest", "admin_digest"):
        _need(bool(row[key]), f"{key}: must be a non-empty string")
    _need(bool(_PY_VERSION.fullmatch(row["admin_python"])), "admin_python")
    _need(row["status"] in ("allowed", "revoked"), "status")
    if "reason" in row:
        _need(row["status"] == "revoked", "reason: only a revoked release")
        _need(bool(row["reason"]), "reason: must be a non-empty string")
    return dict(row)


def _toml(text: bytes, schema: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(text.decode("utf-8"))
    except _TOML_ERRORS as exc:
        raise Refused("snapshot", f"schema {schema}: invalid TOML: {exc}") from exc
    _need(data.get("schema") == schema, f"schema: expected {schema!r}")
    return data


def _keys_ok(
    data: dict[str, Any],
    required: frozenset[str],
    optional: frozenset[str],
    where: str,
) -> None:
    # As `df.config._check_keys`: `schema` is allowed at every level.
    extra = sorted(set(data) - required - optional - {"schema"})
    _need(not extra, f"{where}: unknown key {extra[:1]}")
    missing = sorted(required - set(data))
    _need(not missing, f"{where}: missing key {missing[:1]}")


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise Refused("snapshot", what)


# --- copy of df.accept: settle_on_acquire -----------------------------------

_DIR_SYNC_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_F_FULLFSYNC = getattr(fcntl, "F_FULLFSYNC", 51)
_UNSUPPORTED = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})


def settle_on_acquire(paths: EntryPaths) -> None:
    """`df.accept.settle_on_acquire`: install dir, then the rest by its id."""
    _sync_existing((paths.install_dir,))
    rest = acquire_paths(paths, pending_id(paths))
    _sync_existing(tuple(p for p in rest if p != paths.install_dir))


def acquire_paths(paths: EntryPaths, release_id: str | None) -> tuple[Path, ...]:
    """`df.accept.acquire_paths` with `local_bin` always listed (F3)."""
    out = [paths.quarantine, paths.install_dir, paths.marks_dir, paths.etc]
    out += [paths.opt, paths.releases]
    out += _release_root(paths, release_id)
    out.append(paths.staging)
    if release_id is not None:
        out.append(paths.staging_dir(release_id))
    out += [paths.admin, paths.admin_python, paths.bin_dir, paths.local_bin]
    out.append(paths.home_parent)
    return tuple(out)


def _release_root(paths: EntryPaths, release_id: str | None) -> list[Path]:
    if release_id is None:
        return []
    path = paths.release_dir(release_id)
    try:
        st = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return []
    return [path] if stat.S_ISDIR(st.st_mode) and owner_uid(st) == ROOT_UID else []


def pending_id(paths: EntryPaths) -> str | None:
    """`df.accept._pending_id`: the id of a valid `pending.toml`, else None."""
    try:
        text = read_optional(paths.pending)
        if text is None:
            return None
        data = _toml(text, _PENDING_SCHEMA)
        _keys_ok(data, _PENDING_REQUIRED, frozenset({"previous_current"}), "pending")
        for key, value in data.items():
            _need(isinstance(value, str), f"{key}: must be a string")
        for key in ("run_id", "release_id", "install_text"):
            _need(bool(data[key]), f"{key}: must be a non-empty string")
        _need(bool(_SHA256.fullmatch(data["install_digest"])), "install_digest")
        _need(data.get("previous_current") != "", "previous_current")
        return _checked_id(data["release_id"])
    except Refused:
        return None


def _sync_existing(paths: Iterable[Path]) -> None:
    for path in paths:
        if _is_real_dir(path):
            _sync_dir(path)


def _is_real_dir(path: Path) -> bool:
    try:
        st = os.stat(path, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISDIR(st.st_mode)


def _sync_dir(path: Path) -> None:
    """`df.fsutil.sync_dir`, never following a final link."""
    fd = os.open(path, _DIR_SYNC_FLAGS)
    try:
        os.fsync(fd)
        if sys.platform == "darwin":
            _full_fsync(fd)
    finally:
        os.close(fd)


def _full_fsync(fd: int) -> None:
    try:
        fcntl.fcntl(fd, _F_FULLFSYNC)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED:
            raise


# --- copy of df.journal: lock, generation, read_consistent -------------------

_NB = os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FOLLOW_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_LOCK_FLAGS = os.O_RDWR | _NB
_READ_FLAGS = os.O_RDONLY | _NB
_GEN_MAX_BYTES = 64
_GEN_RE = re.compile(r"0|[1-9][0-9]*")
_BUSY = "идёт другая административная операция"
_CHANGING = "состояние меняется"


class Consistent(NamedTuple):
    """Copy of `Stable(value, g)` (`stable`) and `Transitional(g)`."""

    stable: bool
    generation: int
    value: Any


def open_writer_lock(paths: EntryPaths) -> int:
    """`df.journal.writer_lock` without creating directories; the fd stays open."""
    dir_fd = _open_install_dir(paths)
    try:
        fd = _open_private(dir_fd, paths.lock, _LOCK_FLAGS, "lock")
    finally:
        os.close(dir_fd)
    try:
        _flock_or_refuse(fd, paths.lock)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_install_dir(paths: EntryPaths) -> int:
    """Walk root -> install_dir; above `var` links are followed, below not."""
    parts = paths.install_dir.relative_to(paths.root).parts
    first_nofollow = len(paths.var.relative_to(paths.root).parts) - 1
    path = paths.root
    fd = _step_open(path, lambda: os.open(path, _DIR_FOLLOW_FLAGS))
    for i, name in enumerate(parts):
        path = path / name
        flags = _DIR_FLAGS if i >= first_nofollow else _DIR_FOLLOW_FLAGS
        fd = _descend(fd, path, flags)
    return fd


def _descend(parent_fd: int, path: Path, flags: int) -> int:
    """Open child `path.name` of `parent_fd` (never created); closes the parent."""
    try:
        return _step_open(path, lambda: os.open(path.name, flags, dir_fd=parent_fd))
    finally:
        os.close(parent_fd)


def _step_open(path: Path, opener: Callable[[], int]) -> int:
    try:
        fd = opener()
    except OSError as exc:
        raise Refused("lock", f"{path}: {_why(exc)}") from exc
    try:
        _require_trusted_dir(fd, path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _require_trusted_dir(fd: int, path: Path) -> None:
    st = os.fstat(fd)
    if owner_uid(st) not in (ROOT_UID, os.geteuid()):
        raise Refused("lock", f"{path}: не доверенный каталог (владелец)")
    if st.st_mode & _NO_WRITE_OTHERS:
        raise Refused("lock", f"{path}: не доверенный каталог (права)")


def _why(exc: OSError) -> str:
    if exc.errno == errno.ELOOP:
        return "символическая ссылка"
    if exc.errno == errno.EISDIR:
        return "не обычный файл"
    if exc.errno == errno.ENOTDIR:
        return "не каталог (символическая ссылка?)"
    return exc.strerror or errno.errorcode.get(exc.errno or 0, str(exc))


def _open_regular(
    dir_fd: int | None,
    path: Path,
    flags: int,
    step: str,
    *,
    writer: bool = False,
    exact_mode: int | None = None,
) -> int:
    target = path.name if dir_fd is not None else path
    try:
        fd = os.open(target, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise Refused(step, f"{path}: {_why(exc)}") from exc
    try:
        problem = _file_problem(os.fstat(fd), writer, exact_mode)
    except BaseException:
        os.close(fd)
        raise
    if problem is not None:
        os.close(fd)
        raise Refused(step, f"{path}: {problem}")
    return fd


def _file_problem(
    st: os.stat_result, writer: bool, exact_mode: int | None
) -> str | None:
    if not stat.S_ISREG(st.st_mode):
        return "не обычный файл"
    if not writer:
        return None
    if owner_uid(st) != os.geteuid():
        return "владелец не текущий пользователь"
    if st.st_nlink != 1:
        return "несколько жёстких ссылок"
    mode = stat.S_IMODE(st.st_mode)
    if exact_mode is not None and mode != exact_mode:
        return f"права {mode:04o}, нужны {exact_mode:04o}"
    return None


def _open_private(dir_fd: int, path: Path, flags: int, step: str) -> int:
    try:
        return _open_regular(dir_fd, path, flags, step, writer=True, exact_mode=0o600)
    except Refused:
        if not _is_absent(path, dir_fd):
            raise
    return _create_private(dir_fd, path, flags, step)


def _is_absent(path: Path, dir_fd: int) -> bool:
    try:
        os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _create_private(dir_fd: int, path: Path, flags: int, step: str) -> int:
    try:
        fd = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
    except FileExistsError:
        return _open_regular(dir_fd, path, flags, step, writer=True, exact_mode=0o600)
    except OSError as exc:
        raise Refused(step, f"{path}: {_why(exc)}") from exc
    try:
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _flock_or_refuse(fd: int, path: Path) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise Refused("lock", _BUSY) from exc
        raise Refused("lock", f"{path}: {_why(exc)}") from exc


def read_generation(path: Path) -> int:
    """Absent -> 0; otherwise a canonical decimal or `Refused("snapshot")`."""
    try:
        fd = _open_regular(None, path, _READ_FLAGS, "snapshot")
    except Refused:
        if _lexists(path):
            raise
        return 0
    try:
        raw = os.read(fd, _GEN_MAX_BYTES + 1)
    finally:
        os.close(fd)
    return parse_generation(raw)


def _lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def parse_generation(raw: bytes) -> int:
    """`df.journal._parse_generation`."""
    if len(raw) > _GEN_MAX_BYTES:
        raise Refused("snapshot", "generation: слишком длинное значение")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Refused("snapshot", "generation: не ASCII") from exc
    text = text.removesuffix("\n")
    if not _GEN_RE.fullmatch(text):
        raise Refused("snapshot", f"generation: не число: {text!r}")
    return int(text)


def read_consistent(
    generation: Path, read: Callable[[], Any], *, attempts: int = ATTEMPTS
) -> Consistent:
    """`df.journal.read_consistent`: an unchanged even generation, no lock."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for _ in range(attempts):
        g1 = read_generation(generation)
        if g1 % 2 == 1:
            return Consistent(False, g1, None)
        value = read()
        if read_generation(generation) == g1:
            return Consistent(True, g1, value)
    raise Refused("snapshot", _CHANGING)


# --- copy of df.digest: tree_digest (nothing excluded) -----------------------

_CHUNK = 1 << 20
_OPEN_REGULAR_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_TREE_CHANGED = frozenset({errno.ENOENT, errno.ENOTDIR})
_FORBIDDEN_CHARS = {"\n": "newline", "\0": "NUL", "/": "slash"}


def check_name(name: bytes) -> str:
    """`df.digest.check_name`."""
    try:
        text = name.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TreeError(f"name is not UTF-8: {name!r}") from exc
    if text in ("", ".", ".."):
        raise TreeError(f"forbidden name: {text!r}")
    for char, label in _FORBIDDEN_CHARS.items():
        if char in text:
            raise TreeError(f"name contains a {label}: {text!r}")
    return text


def check_names(names: Iterable[bytes]) -> list[str]:
    """`df.digest.check_names`: NFC-equal siblings are refused."""
    seen: dict[str, str] = {}
    result: list[str] = []
    for raw in names:
        text = check_name(raw)
        key = unicodedata.normalize("NFC", text)
        if key in seen:
            raise TreeError(f"names equal after NFC: {seen[key]!r} and {text!r}")
        seen[key] = text
        result.append(text)
    return result


def tree_digest(root: Path) -> str:
    """`df.digest.tree_digest(root, exclude=frozenset())` (spec 3.5)."""
    if not stat.S_ISDIR(_tree_lstat(root).st_mode):
        raise TreeError(f"digest root is not a directory: {root}")
    records = sorted(_records(root, ""), key=lambda item: item[0])
    digest = hashlib.sha256()
    for _rel, record in records:
        digest.update(record)
    return f"sha256:{digest.hexdigest()}"


def _tree_lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        if exc.errno in _TREE_CHANGED:
            raise TreeError(f"cannot stat {path}: {exc.strerror}") from exc
        raise


def _records(directory: Path, prefix: str) -> list[tuple[bytes, bytes]]:
    with os.scandir(directory) as entries:
        listing = list(entries)
    names = check_names(os.fsencode(entry.name) for entry in listing)
    out: list[tuple[bytes, bytes]] = []
    for entry, name in zip(listing, names, strict=True):
        rel = f"{prefix}{name}"
        out.append(_record(Path(entry.path), rel))
        if entry.is_dir(follow_symlinks=False):
            out.extend(_records(Path(entry.path), f"{rel}/"))
    return out


def _record(path: Path, rel: str) -> tuple[bytes, bytes]:
    st = _tree_lstat(path)
    if stat.S_ISLNK(st.st_mode):
        kind, mode, payload = b"l", b"-", _link_text(path)
    elif stat.S_ISDIR(st.st_mode):
        kind, mode, payload = b"d", b"-", b""
    elif stat.S_ISREG(st.st_mode):
        kind = b"f"
        mode = b"x" if st.st_mode & 0o111 else b"-"
        payload = _file_sha256(path).encode()
    else:
        raise TreeError(f"unsupported file type: {rel}")
    encoded = rel.encode("utf-8")
    return encoded, b"\0".join((kind, encoded, mode, payload)) + b"\n"


def _link_text(path: Path) -> bytes:
    text = os.readlink(os.fsencode(path))
    if b"\n" in text:
        raise TreeError(f"link target contains a newline: {path}")
    return text


def _file_sha256(path: Path) -> str:
    fd = os.open(path, _OPEN_REGULAR_FLAGS)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TreeError(f"not a regular file: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


# --- script -----------------------------------------------------------------


def _script() -> int:
    """`bin/darkfactory` runs the entry with `-I -B -S`; anything else is refused.

    A signal against a wrong launcher, not a defence: the module's stdlib
    imports above already ran, so without `-I` a shadowed stdlib module would
    have been imported before this check (n-3).
    """
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        print("darkfactory: вход запускается только как -I -B -S", file=sys.stderr)
        return EXIT_REFUSED
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(_script())
