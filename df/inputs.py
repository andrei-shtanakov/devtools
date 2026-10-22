"""Trusted inputs of the installer (spec 3.5): reference checkout, sources.

Root fetches each repo itself at the manifest SHA and compares the release
sources with that checkout. Nothing from a checkout or a release is executed.
"""

from __future__ import annotations

import email.parser
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from df.build import (
    GIT_ISOLATION,
    git_archive_argv,
    git_fetch_argv,
    git_rev_parse_argv,
)
from df.digest import TreeError, open_regular, tree_digest
from df.journal import StepFailed
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.platform import RunResult, run_command
from df.sources import SourceId, source_from_direct_url

#: Seconds allowed for one root git command.
GIT_TIMEOUT = 600
#: Bytes of stderr quoted in a failure.
STDERR_REPORT_BYTES = 4096
_FETCH_STEP = "reference-fetch"
_VERIFY_STEP = "verify-sources"
_VIRTUALENV_PTH = "_virtualenv.pth"
#: Caps for files read from a release (untrusted build output).
METADATA_CAP = 1 << 20
DIRECT_URL_CAP = 1 << 16
PTH_CAP = 1 << 16
_NAME_RUNS = re.compile(r"[-_.]+")
_VIRTUALENV_PTH_BODY = (b"import _virtualenv", b"import _virtualenv\n")


@dataclass(frozen=True)
class InstalledDist:
    """One installed distribution: name, version and source identity."""

    name: str
    version: str
    source: SourceId


def fetch_reference(pin: RepoPin, dest: Path, *, git: Path, layout: Layout) -> Path:
    """Fetch `pin` as root and unpack it into `dest`; return `dest`.

    `dest` must be a direct child of `layout.verify_dir`, which must already
    exist as a real directory (it is never created here); an existing `dest` (a retry) is
    removed first. Git runs as the current user with the build's isolation
    (empty `HOME`/`XDG_CONFIG_HOME`, no templates, no hooks) and the same
    fetch/rev-parse/archive argv as the build. `FETCH_HEAD` must equal
    `pin.sha`; the archive is unpacked with `filter="data"`. Nothing from the
    checkout is executed. Effects: none on the journal (root's own scratch).
    """
    _check_dest(dest, layout)
    work = dest.with_name(f".{dest.name}.work")
    for stale in (dest, work):
        _remove(stale)
    try:
        for sub in ("home", "config", "git"):
            (work / sub).mkdir(parents=True)
        _fetch(pin, work, git)
        dest.mkdir()
        _unpack(work / "ref.tar", dest)
    except BaseException:
        _cleanup(dest, work, raising=False)
        raise
    _cleanup(dest, work, raising=True)
    return dest


def verify_sources(
    release_dir: Path, profile: Profile, refs: Mapping[str, Path]
) -> dict[str, str]:
    """Compare `release_dir/src/<repo>` with the trusted checkout `refs[repo]`.

    Both trees are digested with nothing excluded. Returns
    `{repo: sha256:<hex> of refs[repo]/uv.lock}`; a difference is a
    `StepFailed` naming the repo. Effects: none.
    """
    locks: dict[str, str] = {}
    for pin in profile.repos:
        ref = refs.get(pin.name)
        if ref is None:
            raise StepFailed(_VERIFY_STEP, f"{pin.name}: нет доверенного чекаута")
        want = tree_digest(ref, exclude=frozenset())
        have = tree_digest(release_dir / "src" / pin.name, exclude=frozenset())
        if have != want:
            raise StepFailed(
                _VERIFY_STEP,
                f"{pin.name}: исходники релиза {have}, в чекауте {want}",
            )
        locks[pin.name] = _lock_digest(ref / "uv.lock", pin.name)
    return locks


def installed_distributions(venv: Path) -> list[InstalledDist]:
    """Name, version and source of every `*.dist-info` in `venv` (read only).

    Names are PEP 503 normalised. Only real directories are walked and the
    files are read as untrusted input (no links, size caps); otherwise
    `TreeError`.
    """
    dists = [
        _read_dist(info)
        for site in _site_packages(venv)
        for info in _real_subdirs(site, "", ".dist-info")
    ]
    return sorted(dists, key=lambda d: (d.name, d.version))


def pth_problems(venv: Path) -> list[str]:
    """`.pth` files in `site-packages` except virtualenv's own (they run code).

    A walk that cannot be trusted (links, unreadable entries) raises
    `TreeError` instead of being reported: it fails closed.
    """
    problems: list[str] = []
    for site in _site_packages(venv):
        for pth in _children(site, "", ".pth"):
            if pth.name == _VIRTUALENV_PTH and _is_virtualenv_pth(pth):
                continue
            problems.append(f"{pth.relative_to(venv)}: .pth исполняет код при старте")
    return problems


def _check_dest(dest: Path, layout: Layout) -> None:
    root = layout.verify_dir
    if dest.parent != root or ".." in dest.parts or dest.name in ("", "."):
        raise StepFailed(_FETCH_STEP, f"{dest} не внутри {root}")
    if not _is_real_dir(root):
        raise StepFailed(_FETCH_STEP, f"{root} нет или не каталог")


def _cleanup(dest: Path, work: Path, *, raising: bool) -> None:
    """Remove scratch; on the failure path errors must not mask the cause."""
    try:
        _remove(work)
        if not raising:
            _remove(dest)
    except OSError:
        if raising:
            raise


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _git_env(git: Path, work: Path) -> dict[str, str]:
    return {
        "PATH": ":".join(dict.fromkeys([str(git.parent), "/usr/bin", "/bin"])),
        "LANG": "C",
        "HOME": str(work / "home"),
        "XDG_CONFIG_HOME": str(work / "config"),
        **GIT_ISOLATION,
    }


def _fetch(pin: RepoPin, work: Path, git: Path) -> None:
    env, tool, repo = _git_env(git, work), str(git), str(work / "git")
    steps = [
        ("init", [tool, "init", "-q", "--template=", repo]),
        ("fetch", git_fetch_argv(tool, repo, pin.url, pin.sha)),
        ("rev-parse", git_rev_parse_argv(tool, repo)),
        ("archive", git_archive_argv(tool, repo, str(work / "ref.tar"), pin.sha)),
    ]
    for label, argv in steps:
        result = run_command(argv, timeout=GIT_TIMEOUT, env=env)
        _check_result(f"{label}:{pin.name}", result)
        if label == "rev-parse":
            head = result.stdout.decode("utf-8", "replace").strip()
            if head != pin.sha:
                raise StepFailed(
                    f"rev-parse:{pin.name}",
                    f"FETCH_HEAD {head!r}, в профиле {pin.sha!r}",
                )


def _check_result(label: str, result: RunResult) -> None:
    if result.launched and not (
        result.timed_out or result.stdout_truncated or result.returncode != 0
    ):
        return
    state = "не стартовал" if not result.launched else f"код {result.returncode}"
    if result.timed_out:
        state = f"срок {GIT_TIMEOUT} с истёк"
    elif result.stdout_truncated:
        state = "stdout переполнен"
    tail = result.stderr[-STDERR_REPORT_BYTES:].decode("utf-8", "replace").strip()
    raise StepFailed(label, f"{state}: {tail}" if tail else state)


def _unpack(archive: Path, dest: Path) -> None:
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise TreeError(f"архив чекаута отказан: {exc}") from exc


def _lock_digest(lock: Path, repo: str) -> str:
    try:
        data = lock.read_bytes()
    except OSError as exc:
        raise StepFailed(_VERIFY_STEP, f"{repo}: uv.lock не читается: {exc}") from exc
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _is_real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _children(directory: Path, prefix: str = "", suffix: str = "") -> list[Path]:
    """Sorted entries of `directory` by name, without following links."""
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise TreeError(f"{directory}: не читается: {exc}") from exc
    return [directory / n for n in names if n.startswith(prefix) and n.endswith(suffix)]


def _real_subdirs(directory: Path, prefix: str, suffix: str = "") -> list[Path]:
    """Matching children that are real directories; a link or file is refused."""
    found: list[Path] = []
    for child in _children(directory, prefix, suffix):
        if not _is_real_dir(child):
            raise TreeError(f"{child}: ожидался настоящий каталог")
        found.append(child)
    return found


def _lib_dirs(venv: Path) -> list[Path]:
    """`lib*` directories; only the platform link `lib64 -> lib` is skipped.

    Every other link (any name, any target) is refused: Python may load
    `site-packages` behind it, so skipping it would hide what runs.
    """
    dirs: list[Path] = []
    for child in _children(venv, "lib"):
        if _is_lib64_link(child, venv):
            continue
        if not _is_real_dir(child):
            raise TreeError(f"{child}: ожидался настоящий каталог")
        dirs.append(child)
    return dirs


def _is_lib64_link(child: Path, venv: Path) -> bool:
    try:
        if not stat.S_ISLNK(os.lstat(child).st_mode):
            return False
        target = os.readlink(child)
    except OSError as exc:
        raise TreeError(f"{child}: не читается: {exc}") from exc
    return child.name == "lib64" and target == "lib" and _is_real_dir(venv / "lib")


def _site_packages(venv: Path) -> list[Path]:
    sites: list[Path] = []
    for lib in _lib_dirs(venv):
        for python in _real_subdirs(lib, "python"):
            sites.extend(_real_subdirs(python, "site-packages"))
    return sites


def normalize_name(name: str) -> str:
    """The PEP 503 normalised distribution name."""
    return _NAME_RUNS.sub("-", name).lower()


def _read_capped(path: Path, cap: int) -> bytes:
    """Read a regular file of at most `cap` bytes; never follow a link.

    The file is untrusted build output read by root: a link, a FIFO or a
    device is refused and an oversize file is not read past `cap + 1`.
    """
    try:
        fd = open_regular(path)
    except OSError as exc:
        raise TreeError(f"{path}: не открывается: {exc}") from exc
    try:
        data = os.read(fd, cap + 1)
        while len(data) <= cap:
            chunk = os.read(fd, cap + 1 - len(data))
            if not chunk:
                break
            data += chunk
    except OSError as exc:
        raise TreeError(f"{path}: не читается: {exc}") from exc
    finally:
        os.close(fd)
    if len(data) > cap:
        raise TreeError(f"{path}: больше {cap} байт")
    return data


def _read_dist(info: Path) -> InstalledDist:
    headers = email.parser.BytesParser().parsebytes(
        _read_capped(info / "METADATA", METADATA_CAP), headersonly=True
    )
    name, version = headers.get("Name"), headers.get("Version")
    if not name or not version:
        raise TreeError(f"{info}: в METADATA нет Name/Version")
    source = source_from_direct_url(_read_direct_url(info))
    return InstalledDist(normalize_name(name), version, source)


def _read_direct_url(info: Path) -> Mapping[str, object] | None:
    path = info / "direct_url.json"
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TreeError(f"{path}: не читается: {exc}") from exc
    try:
        data = json.loads(_read_capped(path, DIRECT_URL_CAP))
    except ValueError as exc:
        raise TreeError(f"{path}: не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TreeError(f"{path}: не объект")
    return data


def _is_virtualenv_pth(pth: Path) -> bool:
    try:
        return _read_capped(pth, PTH_CAP) in _VIRTUALENV_PTH_BODY
    except TreeError:
        return False
