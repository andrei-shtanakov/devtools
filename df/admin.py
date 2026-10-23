"""Administrative environment of `darkfactory` (spec 3.9, Task 8a part 1).

Root runs nothing the builder could write: the admin code is root's copy of
`df/` from the trusted `devtools` checkout (`install_admin`), the admin
Python is installed by root with the approved `uv` (`install_admin_python`).
Both are leaves of the route `admin`; `admin_ready` is the one definition of
their readiness. The command `bin/darkfactory` runs the admin Python on the
entry file (`launcher_text`, `entry_text`); `link_command` publishes it in
`/usr/local/bin` when the route's guard says that directory is trusted, and
`launcher_ready` says whether the command files are what this code generates.
The whole admin tree is root's and writable by root only: modes are set
explicitly (`fchmod`, `umask`), never inherited.
"""

from __future__ import annotations

import errno
import os
import re
import shlex
import stat
from pathlib import Path, PurePosixPath

from df import accept, acl, fsutil
from df import build as build_mod
from df import platform as plat
from df.digest import TreeError, check_links, open_regular, tree_digest
from df.journal import StepFailed
from df.launcher_entry import Refused, entry_text
from df.lockset import UV_PINNED_VERSION
from df.paths import Layout
from df.platform import PlatformName

#: uv versions for which both premises of spec 3.9 are confirmed: the
#: download's SHA-256 is checked and the installed Python relocates (see
#: `install_admin_python`). A uv bump re-probes before it joins.
UV_ADMIN_CONFIRMED = frozenset({UV_PINNED_VERSION})
#: The admin code needs Python 3.12 or newer (PEP 695 syntax in `df`).
ADMIN_PYTHON_FLOOR = (3, 12)
#: The umask root's `uv` runs under: no group/other write in the admin tree.
ADMIN_UMASK = 0o022
#: Seconds allowed for `uv --version`.
VERSION_TIMEOUT = 60.0
#: Bytes of uv's stderr a refusal quotes.
STDERR_REPORT_BYTES = 4096
#: The one temporary name of `link_command` in `local_bin` (C13).
LINK_TMP = ".darkfactory.tmp"

_STEP = "admin"
_LAUNCHER_STEP = "launcher"
_FACTS_STEP = "facts"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_PEEK = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_NO_WRITE_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_SPECIAL_BITS = stat.S_ISUID | stat.S_ISGID
_CHUNK = 1 << 20
_PYTHON = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_UV_VERSION = re.compile(r"uv (\S+)(?: .*)?")


# ---------- the admin Python ----------


def admin_python_path(layout: Layout, python: str, platform: PlatformName) -> Path:
    """`admin-python/cpython-<python>-<triple>/bin/python<X.Y>`.

    The key is `build.python_key`, the one source of the route guard
    `py=absent` and of the install's argv (F1).
    """
    short = ".".join(python.split(".")[:2])
    key = build_mod.python_key(python, platform)
    return layout.admin_python / key / "bin" / f"python{short}"


def admin_env(layout: Layout) -> dict[str, str]:
    """The whole environment of root's `uv`; nothing is inherited."""
    return {
        "UV_PYTHON_INSTALL_DIR": str(layout.admin_python / ".staging"),
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_DOWNLOADS": "automatic",
        "HOME": str(layout.admin_home),
        "UV_CACHE_DIR": str(layout.admin_home / "cache"),
        "PATH": "/usr/bin:/bin",
    }


def install_admin_python(
    layout: Layout, python: str, *, uv: Path, platform: PlatformName
) -> Path:
    """Protocol `install_admin_python`: the admin Python, staged then renamed.

    Preconditions (refusal `StepFailed("admin")`, nothing changed): `python`
    is at least `ADMIN_PYTHON_FLOOR` (F11); `admin-python/.staging` and the
    target are absent; `admin-home` is root's directory `0700` (created if
    absent, F6); `uv --version` is in `UV_ADMIN_CONFIRMED` (F8).

    Root runs `uv python install --no-bin <python_key>` (exact build, no link
    outside `admin-python`, F1) with exactly `admin_env` under `ADMIN_UMASK`
    (F7), `fault_point("created")`; every object of the installed tree must
    be root's, without group/other write, every link relative and resolving
    inside the tree (F7, F8); `sync_tree(.staging/<key>)`; `rename` to
    `admin-python/<key>`, `fault_point("renamed")`; `sync_dir(admin-python)`;
    the interpreter's path passes `trusted_path_problems`. What uv leaves in
    `.staging` besides the tree is removed by the next route step. Effects:
    `created` -> `sync_tree(py)` -> `renamed` -> `sync_dir(admin_python)`.

    Premises of spec 3.9, confirmed for uv 0.11.23 (`UV_PINNED_VERSION`):
    - the download is checked against its SHA-256: uv carries the expected
      hash of every managed Python in its embedded download metadata and
      refuses a mismatch (`HashMismatch`,
      https://github.com/astral-sh/uv/blob/0.11.23/crates/uv-python/src/downloads.rs);
      measured by `tests/df/test_admin.py::test_uv_refuses_corrupt_download`
      (a local mirror serving another archive -> «Hash mismatch», exit 1);
    - relocation: after the `rename` the interpreter's `sys.prefix` ==
      `sys.base_prefix` == the new path, and no symbolic link in the tree
      points into `.staging` (all of uv's links are relative and inside the
      tree); `test_admin_python_relocation_precondition` (network, opt-in).
      Two text references to `.staging` remain and are harmless at runtime:
      `_sysconfigdata_*.py` (build variables) and the `LC_ID_DYLIB` of
      `libpython*.dylib`, which the interpreter does not link. So the check
      is links plus `sys.prefix`, never a byte search (F8).
    """
    _require_floor(python)
    staging = layout.admin_python / ".staging"
    key = build_mod.python_key(python, platform)
    final = layout.admin_python / key
    for path in (staging, final):
        if os.path.lexists(path):
            raise StepFailed(_STEP, f"{path}: уже есть")
    env = admin_env(layout)
    try:
        _ensure_admin_home(layout)
        _require_confirmed_uv(uv, env)
        _run_uv(uv, ["python", "install", "--no-bin", key], env)
        fsutil.fault_point("created", staging)
        problems = admin_tree_problems(staging / key, final)
        if problems:
            raise StepFailed(_STEP, "; ".join(problems))
        fsutil.sync_tree(staging / key)
        os.rename(staging / key, final)
        fsutil.fault_point("renamed", final)
        fsutil.sync_dir(layout.admin_python)
    except (OSError, TreeError) as exc:
        raise StepFailed(_STEP, f"административный Python: {exc}") from exc
    interpreter = admin_python_path(layout, python, platform)
    problems = plat.trusted_path_problems(interpreter)
    if problems:
        raise StepFailed(_STEP, "; ".join(problems))
    return interpreter


def _require_floor(python: str) -> None:
    m = _PYTHON.fullmatch(python)
    if m is None:
        raise StepFailed(_STEP, f"версия Python {python!r}: не X.Y.Z")
    if (int(m.group(1)), int(m.group(2))) < ADMIN_PYTHON_FLOOR:
        floor = ".".join(map(str, ADMIN_PYTHON_FLOOR))
        raise StepFailed(
            _STEP, f"Python {python}: административному коду нужен ≥ {floor}"
        )


def _ensure_admin_home(layout: Layout) -> None:
    """`admin-home`: root's directory, mode exactly `0700` (F6).

    uv creates its lock files `0666` whatever the umask, so nobody but root
    may reach them. Scratch, not a fact: no sync.
    """
    home = layout.admin_home
    created = True
    try:
        os.mkdir(home, 0o700)
    except FileExistsError:
        created = False
    fd = os.open(home, _DIR_FLAGS)
    try:
        if created:
            os.fchmod(fd, 0o700)
        st = os.fstat(fd)
    finally:
        os.close(fd)
    owner = fsutil.owner_uid(st)
    if owner != plat.ROOT_UID or stat.S_IMODE(st.st_mode) != 0o700:
        mode = oct(stat.S_IMODE(st.st_mode))
        raise StepFailed(_STEP, f"{home}: владелец uid {owner}, режим {mode}")


def _require_confirmed_uv(uv: Path, env: dict[str, str]) -> None:
    out = _run_uv(uv, ["--version"], env, timeout=VERSION_TIMEOUT)
    m = _UV_VERSION.fullmatch(out.decode("utf-8", "replace").strip())
    version = m.group(1) if m else "?"
    if version not in UV_ADMIN_CONFIRMED:
        raise StepFailed(
            _STEP,
            f"uv {version}: сверка загрузки Python не подтверждена; "
            "перенос Python не подтверждён",
        )


def _run_uv(
    uv: Path,
    args: list[str],
    env: dict[str, str],
    *,
    timeout: float = build_mod.COMMAND_TIMEOUT,
) -> bytes:
    """Run root's `uv` under `ADMIN_UMASK` (set, then restored); its stdout."""
    old = os.umask(ADMIN_UMASK)
    try:
        result = plat.run_command([str(uv), *args], env=env, timeout=timeout)
    finally:
        os.umask(old)
    if result.launched and not result.timed_out and result.returncode == 0:
        return result.stdout
    tail = result.stderr[-STDERR_REPORT_BYTES:].decode("utf-8", "replace")
    state = "срок истёк" if result.timed_out else f"код {result.returncode}"
    raise StepFailed(_STEP, f"uv {' '.join(args)}: {state}: {tail}")


def admin_tree_problems(tree: Path, final: Path) -> list[str]:
    """Problems of an installed admin Python tree, as `"<path>: <reason>"`.

    Every object (the root too) is root's and only a file, a directory or a
    link; no group/other write, setuid/setgid or ACL on non-links; every link
    is relative and resolves inside the tree, read as if it already were at
    `final` (F7). Measured on uv 0.11.23 macOS arm64 (1804 objects): the only
    links are the 9 relative aliases uv writes (`bin/python`, `bin/python3`,
    `bin/2to3`, `bin/idle3`, `bin/pydoc3`, `bin/python3-config`, two in
    `lib/pkgconfig`, one man page), all inside the tree. Fact `A` applies the
    same rule to the installed tree (`admin_python_problems`, M-4).
    """
    try:
        st = os.lstat(tree)
    except FileNotFoundError:
        return [f"{tree}: uv не создал интерпретатор"]
    if not stat.S_ISDIR(st.st_mode):
        return [f"{tree}: не каталог"]
    problems = _object_problems(tree, st)
    for dirpath, dirs, files in os.walk(tree, onerror=_raise):
        for name in sorted([*dirs, *files]):
            path = Path(dirpath) / name
            st = os.lstat(path)
            problems += _object_problems(path, st)
            if not stat.S_ISLNK(st.st_mode) and acl.acl_present_path(path):
                problems.append(f"{path}: есть ACL")
            if stat.S_ISLNK(st.st_mode) and os.readlink(path).startswith("/"):
                problems.append(f"{path}: абсолютная ссылка {os.readlink(path)!r}")
    problems += [f"{tree}/{p}" for p in check_links(tree, PurePosixPath(final))]
    return problems


def _object_problems(path: Path, st: os.stat_result) -> list[str]:
    mode = st.st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
        return [f"{path}: не файл, не каталог и не ссылка"]
    problems: list[str] = []
    owner = fsutil.owner_uid(st)
    if owner != plat.ROOT_UID:
        problems.append(f"{path}: владелец uid {owner}, не root")
    if not stat.S_ISLNK(mode) and mode & _NO_WRITE_OTHERS:
        problems.append(f"{path}: запись для группы или всех")
    if not stat.S_ISLNK(mode) and mode & _SPECIAL_BITS:
        problems.append(f"{path}: бит setuid или setgid")
    return problems


def _raise(exc: OSError) -> None:
    raise exc


# ---------- the admin code ----------


def admin_digest(layout: Layout, release_id: str) -> str:
    """Digest of `admin/<id>`: the whole tree, nothing excluded (F12).

    The one definition for the mark `admin`, the registry and `admin_ready`.
    """
    return tree_digest(layout.admin_dir(release_id), exclude=frozenset())


def admin_python_problems(
    layout: Layout, python: str, platform: PlatformName
) -> list[str]:
    """Trust of the installed admin Python tree for fact `A` (M-4, M-5).

    The whole `admin-python/<key>` tree by the install rule
    (`admin_tree_problems`, logical root = the tree itself), and no link on
    the chain from `/` to the tree (the entry refuses one, so `A` must too).
    """
    tree = layout.admin_python / build_mod.python_key(python, platform)
    return chain_link_problems(tree) + admin_tree_problems(tree, tree)


def chain_link_problems(path: Path) -> list[str]:
    """A link anywhere on `/ … path` (M-5): a v1 limit, the entry refuses it.

    `trusted_path_problems` follows a root-owned link by its text; the entry
    walks real directories only, so `A` refuses what the entry refuses (a
    host with `/opt` as a link is not supported in v1).
    """
    problems: list[str] = []
    current = Path("/")
    for part in path.parts[1:]:
        current = current / part
        try:
            st = os.lstat(current)
        except (FileNotFoundError, NotADirectoryError):
            return [*problems, f"{current}: нет"]
        if stat.S_ISLNK(st.st_mode):
            problems.append(f"{current}: ссылка на пути (не поддерживается в v1)")
    return problems


def is_bytecode(name: str) -> bool:
    """`__pycache__` or `*.pyc` (casefold): never part of the admin code (n-5)."""
    folded = name.casefold()
    return folded == "__pycache__" or folded.endswith(".pyc")


def admin_code_problems(layout: Layout, release_id: str) -> list[str]:
    """Trust of `admin/<id>` (I-1): what root imports nobody else may write.

    No link on the chain from `/` to `admin/<id>` (M-5) and the chain passes
    `trusted_path_problems` (ACLs included); every object inside is a
    directory or a regular file (never a link: `install_admin` copies none),
    never bytecode (`is_bytecode`: under `-B` a `.pyc` is still read, n-5),
    root's, without group or other write and setuid/setgid bits
    (`_object_problems`), and carries no ACL. A vanished object is a
    `TreeError`; any other `OSError` propagates.
    """
    admin = layout.admin_dir(release_id)
    problems = chain_link_problems(admin) + list(plat.trusted_path_problems(admin))
    for dirpath, dirs, files in os.walk(admin, onerror=_raise_tree):
        for name in sorted([*dirs, *files]):
            path = Path(dirpath) / name
            if is_bytecode(name):
                problems.append(f"{path}: байт-код в административном коде")
            st = _tree_lstat(path)
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"{path}: ссылка в административном коде")
                continue
            problems += _object_problems(path, st)
            if acl.acl_present_path(path):
                problems.append(f"{path}: есть ACL")
    return problems


def _raise_tree(exc: OSError) -> None:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        raise TreeError(f"{exc.filename}: {exc.strerror}") from exc
    raise exc


def _tree_lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise TreeError(f"{path}: {exc.strerror}") from exc


def install_admin(layout: Layout, release_id: str, *, devtools_ref: Path) -> None:
    """Protocol `install_admin`: root's copy of `<devtools_ref>/df/`.

    `devtools_ref` is root's trusted checkout (Task 7), never the release
    tree. Precondition: `admin/<id>` is absent, else `StepFailed`, nothing
    touched. Only directories and regular files are copied (a link or any
    other type is a refusal); modes are set by `fchmod` — directories and
    files with any x bit `0755`, other files `0644` (F13). Effects:
    `created` (partial) -> `created` (full) -> `sync_tree(admin/<id>)` ->
    `sync_dir(admin)`.
    """
    dest = layout.admin_dir(release_id)
    if os.path.lexists(dest):
        raise StepFailed(_STEP, f"{dest}: уже есть")
    src = devtools_ref / "df"
    if not accept.is_real_dir(src):
        raise StepFailed(_STEP, f"{src}: не каталог")
    try:
        _make_dir(dest)
        fsutil.fault_point("created", dest)
        _copy_dir(src, dest / "df")
    except (OSError, TreeError) as exc:
        raise StepFailed(_STEP, f"административный код: {exc}") from exc
    fsutil.fault_point("created", dest)
    fsutil.sync_tree(dest)
    fsutil.sync_dir(layout.admin)


def _make_dir(path: Path) -> None:
    os.mkdir(path, 0o700)
    fd = os.open(path, _DIR_FLAGS)
    try:
        os.fchmod(fd, 0o755)
    finally:
        os.close(fd)


def _copy_dir(src: Path, dst: Path) -> None:
    _make_dir(dst)
    with os.scandir(src) as entries:
        listing = sorted(entries, key=lambda e: e.name)
    for entry in listing:
        path = Path(entry.path)
        if is_bytecode(entry.name):
            continue  # sources at the pin only (n-5)
        if entry.is_symlink():
            raise StepFailed(_STEP, f"{path}: ссылка в административном коде")
        if entry.is_dir(follow_symlinks=False):
            _copy_dir(path, dst / entry.name)
        elif entry.is_file(follow_symlinks=False):
            _copy_file(path, dst / entry.name)
        else:
            raise StepFailed(_STEP, f"{path}: не файл и не каталог")


def _copy_file(src: Path, dst: Path) -> None:
    fd_in = open_regular(src)
    try:
        mode = 0o755 if os.fstat(fd_in).st_mode & 0o111 else 0o644
        fd_out = os.open(dst, _NEW_FILE, 0o600)
        try:
            os.fchmod(fd_out, mode)
            while chunk := os.read(fd_in, _CHUNK):
                _write_all(fd_out, chunk)
        finally:
            os.close(fd_out)
    finally:
        os.close(fd_in)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def admin_ready(
    layout: Layout,
    release_id: str,
    *,
    expected_admin_digest: str,
    expected_python: str,
) -> bool:
    """The one definition of a ready admin environment (spec 3.3, rev 8 r10).

    `admin/<id>` is a directory whose `admin_digest` equals the passed value
    and whose trust holds (`admin_code_problems` empty: the chain to it passes
    `trusted_path_problems`, every object in it is root's, without group or
    other write, setuid/setgid or an ACL, and nothing in it is a link — I-1),
    and the admin Python of exactly `expected_python` is a regular file (by
    `lstat`) whose path passes `trusted_path_problems`, and whose whole tree
    passes `admin_python_problems` (M-4: per object, as at install; no link on
    the chain, M-5). Both values come from
    the caller's one source (the mark, or the registry entry); nothing else
    is read. A tree the digest refuses is not ready; any other `OSError` is
    `StepFailed("facts")` (F12).
    """
    admin = layout.admin_dir(release_id)
    if not accept.is_real_dir(admin):
        return False
    try:
        if admin_digest(layout, release_id) != expected_admin_digest:
            return False
        if admin_code_problems(layout, release_id):
            return False
    except TreeError:
        return False
    except OSError as exc:
        raise StepFailed(_FACTS_STEP, f"{admin}: не читается: {exc}") from exc
    interpreter = admin_python_path(layout, expected_python, plat.detect_platform())
    try:
        st = os.lstat(interpreter)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise StepFailed(_FACTS_STEP, f"{interpreter}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode) or plat.trusted_path_problems(interpreter):
        return False
    try:
        return not admin_python_problems(
            layout, expected_python, plat.detect_platform()
        )
    except (TreeError, FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise StepFailed(_FACTS_STEP, f"{interpreter}: {exc}") from exc


# ---------- the command ----------


def launcher_text(layout: Layout, python: str, platform: PlatformName) -> str:
    """Text of `bin/darkfactory`: the admin Python on the entry file (F2, F14)."""
    interpreter = shlex.quote(str(admin_python_path(layout, python, platform)))
    entry = shlex.quote(str(layout.entry))
    return f'#!/bin/sh\nexec {interpreter} -I -B -S {entry} "$@"\n'


def link_command(layout: Layout) -> None:
    """Protocol `link_command`: `/usr/local/bin/darkfactory` -> the command.

    A temporary link, `rename` over the name, `fault_point("renamed")`,
    `sync_dir(local_bin)`. The temporary name is one fixed name,
    `LINK_TMP`: a leftover of a killed run is unlinked first, never
    accumulated (C13; the writer lock makes the name exclusive). No
    branching: whether to link is the route's guard (`local_bin=trusted`).
    The name is the command's own; nothing named `df` is ever created.
    Effects: `renamed` -> `sync_dir(local_bin)`.
    """
    name = layout.launcher.name
    tmp = LINK_TMP
    try:
        fd = os.open(layout.local_bin, _DIR_FLAGS)
    except OSError as exc:
        raise StepFailed(_LAUNCHER_STEP, f"{layout.local_bin}: {exc}") from exc
    try:
        _unlink_absent_ok(tmp, fd)
        os.symlink(str(layout.launcher), tmp, dir_fd=fd)
        try:
            os.rename(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            _unlink_quietly(tmp, fd)
            raise
        fsutil.fault_point("renamed", layout.local_bin / name)
        fsutil.sync_dir(fd)
    except OSError as exc:
        raise StepFailed(_LAUNCHER_STEP, f"{layout.local_bin / name}: {exc}") from exc
    finally:
        os.close(fd)


def _unlink_absent_ok(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _unlink_quietly(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def entry_source(layout: Layout, release_id: str) -> Path:
    """The entry module of a release's admin code (M-1): the one source."""
    return layout.admin_dir(release_id) / "df" / "launcher_entry.py"


def release_entry_text(layout: Layout, release_id: str) -> str:
    """`entry_text` from `release_id`'s own admin code (M-1, M13).

    The text of `bin/darkfactory-entry.py` the route `launcher` writes; a
    source that is absent, a link, not regular, too big or without exactly
    one `_ROOT` line is `StepFailed("launcher")`.
    """
    source = entry_source(layout, release_id)
    try:
        return entry_text(layout, source)
    except Refused as exc:
        raise StepFailed(_LAUNCHER_STEP, exc.detail) from exc
    except OSError as exc:
        raise StepFailed(_LAUNCHER_STEP, f"{source}: {exc}") from exc


def launcher_ready(layout: Layout, release_id: str, *, python: str) -> bool:
    """Fact `L`: the command files are what `release_id`'s admin code
    generates (F2, M-1).

    The entry and the command are root's regular files, `0644` and `0755`,
    with exactly the entry text of `release_id`'s own source
    (`entry_source`, the one the route `launcher` writes) and
    `launcher_text` for `python`, and both pass `trusted_path_problems`
    (chain, ACL — spec 3.10, M-3); when `/usr/local/bin` is trusted
    (`accept.local_bin_trusted`), its `darkfactory` is a link whose text is
    the command's path. Absent or different — a source that cannot render
    an entry included — is false; any other `OSError` is
    `StepFailed("facts")`.
    """
    entry = _source_entry_text(layout, release_id)
    if entry is None:
        return False
    platform = plat.detect_platform()
    files = (
        (layout.entry, entry, 0o644),
        (layout.launcher, launcher_text(layout, python, platform), 0o755),
    )
    if not all(_file_is(path, text, mode) for path, text, mode in files):
        return False
    if any(plat.trusted_path_problems(path) for path, _, _ in files):
        return False
    if not accept.local_bin_trusted(layout):
        return True
    return _link_is(layout.local_bin / layout.launcher.name, str(layout.launcher))


def _source_entry_text(layout: Layout, release_id: str) -> str | None:
    """The entry text of the release's source; `None` if it renders none."""
    source = entry_source(layout, release_id)
    try:
        return entry_text(layout, source)
    except Refused:
        return None
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise StepFailed(_FACTS_STEP, f"{source}: {exc}") from exc


def _file_is(path: Path, text: str, mode: int) -> bool:
    try:
        fd = os.open(path, _PEEK)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return False
        raise StepFailed(_FACTS_STEP, f"{path}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != mode:
            return False
        if fsutil.owner_uid(st) != plat.ROOT_UID:
            return False
        want = text.encode("utf-8")
        return _read_up_to(fd, len(want) + 1) == want
    finally:
        os.close(fd)


def _read_up_to(fd: int, limit: int) -> bytes:
    data = b""
    while len(data) < limit and (chunk := os.read(fd, limit - len(data))):
        data += chunk
    return data


def _link_is(path: Path, text: str) -> bool:
    try:
        return os.readlink(path) == text
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        if exc.errno == errno.EINVAL:  # not a link
            return False
        raise StepFailed(_FACTS_STEP, f"{path}: {exc}") from exc
