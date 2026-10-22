"""Trusted inputs of the installer (spec 3.5): reference checkout, sources.

Root fetches each repo itself at the manifest SHA and compares the release
sources with that checkout. Nothing from a checkout or a release is executed.
"""

from __future__ import annotations

import email.parser
import hashlib
import json
import shutil
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
from df.digest import TreeError, tree_digest
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
_VIRTUALENV_PTH_BODY = (b"import _virtualenv", b"import _virtualenv\n")


@dataclass(frozen=True)
class InstalledDist:
    """One installed distribution: name, version and source identity."""

    name: str
    version: str
    source: SourceId


def fetch_reference(pin: RepoPin, dest: Path, *, git: Path, layout: Layout) -> Path:
    """Fetch `pin` as root and unpack it into `dest`; return `dest`.

    `dest` must lie under `layout.verify_dir`; an existing `dest` (a retry) is
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
        dest.parent.mkdir(parents=True, exist_ok=True)
        for sub in ("home", "config", "git"):
            (work / sub).mkdir(parents=True)
        _fetch(pin, work, git)
        dest.mkdir()
        _unpack(work / "ref.tar", dest)
    except BaseException:
        _remove(dest)
        raise
    finally:
        _remove(work)
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
    """Name, version and source of every `*.dist-info` in `venv` (read only)."""
    dists = [
        _read_dist(info)
        for site in _site_packages(venv)
        for info in sorted(site.glob("*.dist-info"))
    ]
    return sorted(dists, key=lambda d: (d.name, d.version))


def pth_problems(venv: Path) -> list[str]:
    """`.pth` files in `site-packages` except virtualenv's own (they run code)."""
    problems: list[str] = []
    for site in _site_packages(venv):
        for pth in sorted(site.glob("*.pth")):
            if pth.name == _VIRTUALENV_PTH and _is_virtualenv_pth(pth):
                continue
            problems.append(f"{pth.relative_to(venv)}: .pth исполняет код при старте")
    return problems


def _check_dest(dest: Path, layout: Layout) -> None:
    root = layout.verify_dir
    if dest == root or not dest.is_relative_to(root) or ".." in dest.parts:
        raise StepFailed(_FETCH_STEP, f"{dest} вне {root}")


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


def _site_packages(venv: Path) -> list[Path]:
    return sorted(p for p in venv.glob("lib*/python*/site-packages") if p.is_dir())


def _read_dist(info: Path) -> InstalledDist:
    headers = email.parser.BytesParser().parsebytes(
        (info / "METADATA").read_bytes(), headersonly=True
    )
    name, version = headers.get("Name"), headers.get("Version")
    if not name or not version:
        raise TreeError(f"{info}: в METADATA нет Name/Version")
    return InstalledDist(name, version, source_from_direct_url(_read_direct_url(info)))


def _read_direct_url(info: Path) -> Mapping[str, object] | None:
    path = info / "direct_url.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_bytes())
    except ValueError as exc:
        raise TreeError(f"{path}: не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TreeError(f"{path}: не объект")
    return data


def _is_virtualenv_pth(pth: Path) -> bool:
    if pth.is_symlink() or not pth.is_file():
        return False
    return pth.read_bytes() in _VIRTUALENV_PTH_BODY
