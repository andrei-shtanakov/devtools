"""Contents of the administrative tree beyond `df/` (spec 3.9 rev 10, 2b Task 5).

`/opt/df/admin/<id>/` carries, next to `df/`, what the precheck program
`governance/act_precheck.py` runs on: its import closure
(`GOVERNANCE_MODULES`), the contract files it reads relative to its own
`__file__` (`CONTRACT_FILES`) and `site/` -- pure Python only: `yaml/` from
the PyYAML sdist and `steward/` from `src/steward` of the steward commit,
both pinned in root's trusted `devtools/uv.lock` (`locked_pins`).

Nothing is built and nothing from `site/` runs as root: the sdist is read as
a tar stream after its sha256 matched the pin, the steward tree comes from
`git archive` of the pinned commit. Only source artifacts are cached, in
`admin-home/site-src/` (scratch, not a fact: each use re-checks them, a bad
one is fetched again): `pyyaml-<sha256>.tar.gz` and the bare repository
`steward.git`. `site/` is written from those streams straight into the
admin tree inside `install_admin`'s copy step, so an interruption leaves an
ordinary partial `admin/<id>` (removed by the route), never another tree.
Only the future `precheck serve` puts `site/` on `sys.path` (at its end);
the command's entry and the publisher never do.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tarfile
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from df import platform as plat
from df.build import GIT_ISOLATION, git_fetch_argv, git_rev_parse_argv
from df.digest import TreeError, open_regular
from df.paths import Layout
from df.sources import UnsupportedLock, source_from_lock

#: The AST import closure of `governance/act_precheck.py`, deferred imports
#: included (`tests/df/test_admin_tree.py` recomputes it). Its external names
#: are the stdlib, `steward` and `yaml` (`site/`).
GOVERNANCE_MODULES: tuple[str, ...] = (
    "governance",
    "governance.act_anon",
    "governance.act_envelope",
    "governance.act_forge_http",
    "governance.act_git_rows",
    "governance.act_history",
    "governance.act_history_snapshot",
    "governance.act_precheck",
    "governance.act_request",
    "governance.act_time",
    "governance.approval_branches",
    "governance.ssot_env",
)
#: The precheck program: its presence makes a checkout a precheck release.
PRECHECK_FILE = "governance/act_precheck.py"
#: Files those modules read relative to their `__file__` (repo-relative).
CONTRACT_FILES: tuple[str, ...] = ("contracts/approval-branches/v1/patterns.env",)
#: Never in the admin tree: native code, startup hooks, bytecode.
FORBIDDEN_SUFFIXES = (".so", ".dylib", ".pyd", ".pth", ".pyc")
#: The two packages of `site/` and where each comes from.
YAML_PACKAGE = "pyyaml"
STEWARD_PACKAGE = "steward"
YAML_SOURCE_DIR = ("lib", "yaml")
STEWARD_SOURCE_DIR = "src/steward"
#: Cache of source artifacts under `admin-home` (scratch, re-checked on use).
SITE_SRC = "site-src"
STEWARD_REPO = "steward.git"
#: Seconds for one git command or download; bytes a download may have.
GIT_TIMEOUT = 600.0
FETCH_TIMEOUT = 120.0
SDIST_CAP = 64 << 20
STDERR_REPORT_BYTES = 4096

_NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_CHUNK = 1 << 20

Fetch = Callable[[str], bytes]


@dataclass(frozen=True)
class SitePins:
    """The two `site/` sources as `uv.lock` pins them."""

    yaml_url: str
    yaml_sha256: str
    steward_url: str
    steward_commit: str


@dataclass(frozen=True)
class SiteSources:
    """Checked source artifacts: the sdist's bytes and the steward repo."""

    sdist: bytes
    steward_repo: Path
    steward_commit: str


def governance_files() -> tuple[str, ...]:
    """Repo-relative files of `GOVERNANCE_MODULES` (all plain modules but
    the package itself)."""
    return tuple(
        "governance/__init__.py"
        if name == "governance"
        else "/".join(name.split(".")) + ".py"
        for name in GOVERNANCE_MODULES
    )


def release_has_precheck(devtools_ref: Path) -> bool:
    """Whether the checkout carries the precheck program (spec 3.9 rev 10).

    No `governance/act_precheck.py`: a legacy release (`False`), its admin
    tree is `df/` only and `precheck serve` (Task 6) refuses on it. With it,
    every file of `governance_files()` and `CONTRACT_FILES` must be there
    (`True`); a partial closure is `TreeError` naming what is missing --
    never a silent fallback. Modules of the closure an older release has for
    other reasons do not make it a precheck release."""
    if not os.path.lexists(devtools_ref / PRECHECK_FILE):
        return False
    missing = [
        rel
        for rel in (*governance_files(), *CONTRACT_FILES)
        if not os.path.lexists(devtools_ref / rel)
    ]
    if missing:
        raise TreeError(
            f"{devtools_ref}: неполное замыкание предпроверки, нет: "
            + ", ".join(missing)
        )
    return True


def forbidden_name(name: str) -> bool:
    """A name `FORBIDDEN_SUFFIXES` refuses (casefold)."""
    return name.casefold().endswith(FORBIDDEN_SUFFIXES)


def locked_pins(uv_lock: bytes) -> SitePins:
    """`SitePins` from `uv.lock`: PyYAML's sdist (https URL, sha256) and
    steward's git source (https URL, commit); anything else is `TreeError`."""
    try:
        data = tomllib.loads(uv_lock.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TreeError(f"uv.lock не разбирается: {exc}") from exc
    packages = [p for p in data.get("package", []) if isinstance(p, dict)]
    yaml = _one_package(packages, YAML_PACKAGE)
    steward = _one_package(packages, STEWARD_PACKAGE)
    sdist = yaml.get("sdist")
    if not isinstance(sdist, dict):
        raise TreeError(f"uv.lock: у {YAML_PACKAGE} нет sdist")
    url, digest = sdist.get("url"), sdist.get("hash")
    if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != "https":
        raise TreeError(f"uv.lock: sdist {YAML_PACKAGE} не https: {url!r}")
    if not isinstance(digest, str) or not _is_sha256(digest.removeprefix("sha256:")):
        raise TreeError(f"uv.lock: hash sdist {YAML_PACKAGE} не sha256: {digest!r}")
    source = steward.get("source")
    try:
        sid = source_from_lock(
            source if isinstance(source, dict) else {}, root_dir=Path("/")
        )
    except UnsupportedLock as exc:
        raise TreeError(f"uv.lock: источник {STEWARD_PACKAGE}: {exc}") from exc
    if sid.kind != "git" or sid.subdirectory is not None:
        raise TreeError(f"uv.lock: {STEWARD_PACKAGE} не git-источник корня репо")
    assert sid.url is not None and sid.commit is not None
    return SitePins(url, digest.removeprefix("sha256:"), sid.url, sid.commit)


def _one_package(packages: list[dict[str, object]], name: str) -> dict[str, object]:
    found = [p for p in packages if p.get("name") == name]
    if len(found) != 1:
        raise TreeError(f"uv.lock: пакетов {name}: {len(found)}, нужен один")
    return found[0]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def fetch_url(url: str) -> bytes:
    """GET `url` as root, no proxy or other setting from the environment,
    at most `SDIST_CAP` bytes (more is `TreeError`)."""
    if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
        raise TreeError(f"{url}: не http(s)")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=FETCH_TIMEOUT) as response:
            data = response.read(SDIST_CAP + 1)
    except OSError as exc:
        raise TreeError(f"{url}: {exc}") from exc
    if len(data) > SDIST_CAP:
        raise TreeError(f"{url}: больше {SDIST_CAP} байт")
    return data


def install_site(
    dest: Path, pins: SitePins, *, layout: Layout, fetch: Fetch | None, git: Path
) -> None:
    """`site/` at `dest` (absent): checked sources, then `write_site`.

    `fetch=None` is offline: only the cache, nothing downloaded or fetched
    (`expected_admin_digest`)."""
    write_site(dest, site_sources(pins, layout=layout, fetch=fetch, git=git), git=git)


def site_sources(
    pins: SitePins, *, layout: Layout, fetch: Fetch | None, git: Path
) -> SiteSources:
    """The checked source artifacts, from `admin-home/site-src` or fetched.

    The sdist: the cached `pyyaml-<sha256>.tar.gz` if its bytes match the
    pin, else it is removed and `fetch(yaml_url)` must match (then cached).
    steward: `steward.git` (bare, made if absent) must hold the pinned commit
    and its tree (`_has_commit`), else `git fetch <url> <commit>` with
    `FETCH_HEAD` equal to the pin, then the same check. `fetch=None`: a
    missing or bad artifact is `TreeError`, never fetched."""
    cache = layout.admin_home / SITE_SRC
    _ensure_cache_dir(cache)
    sdist = _sdist(cache, pins, fetch)
    repo = cache / STEWARD_REPO
    env = git_env(layout, git)
    if not _has_commit(git, repo, pins.steward_commit, env):
        if fetch is None:
            raise TreeError(f"{repo}: нет коммита {pins.steward_commit} (без сети)")
        _fetch_steward(git, repo, pins, env)
    return SiteSources(sdist, repo, pins.steward_commit)


def git_env(layout: Layout, git: Path) -> dict[str, str]:
    """The whole environment of root's git for `site-src`; nothing inherited."""
    return {
        "PATH": ":".join(dict.fromkeys([str(git.parent), "/usr/bin", "/bin"])),
        "LANG": "C",
        "HOME": str(layout.admin_home),
        **GIT_ISOLATION,
    }


def _ensure_cache_dir(cache: Path) -> None:
    """`site-src`: root's `0700` directory in `admin-home` (scratch, no sync)."""
    try:
        os.mkdir(cache, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(cache)
    if not stat.S_ISDIR(st.st_mode):
        raise TreeError(f"{cache}: не каталог")


def _sdist(cache: Path, pins: SitePins, fetch: Fetch | None) -> bytes:
    path = cache / f"pyyaml-{pins.yaml_sha256}.tar.gz"
    cached = _read_cached(path)
    if cached is not None and _sha256(cached) == pins.yaml_sha256:
        return cached
    if cached is not None:
        os.unlink(path)
    if fetch is None:
        raise TreeError(f"{path}: нет в кэше или не сходится (без сети)")
    data = fetch(pins.yaml_url)
    got = _sha256(data)
    if got != pins.yaml_sha256:
        raise TreeError(f"{pins.yaml_url}: sha256 {got}, в uv.lock {pins.yaml_sha256}")
    _write_cache(path, data)
    return data


def _read_cached(path: Path) -> bytes | None:
    try:
        fd = open_regular(path)
    except FileNotFoundError:
        return None
    try:
        data = b""
        while len(data) <= SDIST_CAP and (chunk := os.read(fd, _CHUNK)):
            data += chunk
    finally:
        os.close(fd)
    return data


def _write_cache(path: Path, data: bytes) -> None:
    """Cache `data` at `path`: an `O_EXCL` temp, then `rename` (scratch: a
    lost write is fetched again, so no sync)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, _NEW_FILE, 0o600)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)
    os.rename(tmp, path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(git: Path, args: list[str], env: dict[str, str]) -> plat.RunResult:
    return plat.run_command([str(git), *args], timeout=GIT_TIMEOUT, env=env)


def _has_commit(git: Path, repo: Path, commit: str, env: dict[str, str]) -> bool:
    """`repo` holds `commit` as a commit and its tree (`git cat-file`, one
    process: `<commit>^{commit}` and `<commit>^{tree}` resolve to objects of
    those types)."""
    if not os.path.isdir(repo):
        return False
    argv = [str(git), "-C", str(repo), "cat-file", "--batch-check"]
    query = f"{commit}^{{commit}}\n{commit}^{{tree}}\n".encode()
    result = plat.run_with_input(argv, query, timeout=GIT_TIMEOUT, env=env)
    if not _ok(result):
        return False
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    kinds = [ln.split()[1] if len(ln.split()) == 3 else "" for ln in lines]
    return kinds == ["commit", "tree"] and lines[0].split()[0] == commit


def _fetch_steward(git: Path, repo: Path, pins: SitePins, env: dict[str, str]) -> None:
    tool, where = str(git), str(repo)
    _checked(_git(git, ["init", "-q", "--bare", "--template=", where], env), "init")
    _checked(
        plat.run_command(
            git_fetch_argv(tool, where, pins.steward_url, pins.steward_commit),
            timeout=GIT_TIMEOUT,
            env=env,
        ),
        "fetch",
    )
    head = _checked(
        plat.run_command(git_rev_parse_argv(tool, where), timeout=GIT_TIMEOUT, env=env),
        "rev-parse",
    )
    got = head.decode("utf-8", "replace").strip()
    if got != pins.steward_commit:
        raise TreeError(
            f"steward: FETCH_HEAD {got!r}, в uv.lock {pins.steward_commit!r}"
        )
    if not _has_commit(git, repo, pins.steward_commit, env):
        raise TreeError(f"steward: нет дерева коммита {pins.steward_commit}")


def _ok(result: plat.RunResult) -> bool:
    return (
        result.launched
        and not (result.timed_out or result.stdout_truncated)
        and result.returncode == 0
    )


def _checked(result: plat.RunResult, label: str) -> bytes:
    if _ok(result):
        return result.stdout
    tail = result.stderr[-STDERR_REPORT_BYTES:].decode("utf-8", "replace").strip()
    state = "срок истёк" if result.timed_out else f"код {result.returncode}"
    raise TreeError(f"git {label} (steward): {state}: {tail}")


# ---------- writing site/ ----------


def write_site(dest: Path, sources: SiteSources, *, git: Path) -> None:
    """Write `dest/yaml` from the sdist and `dest/steward` from `git archive`.

    Each member is checked before it is written: no absolute name and no
    `..` anywhere in the archive; inside the taken directory only regular
    files and directories, and no `FORBIDDEN_SUFFIXES` or `__pycache__`.
    Files are born `0600`, then `fchmod` `0755` (any x bit) or `0644`;
    directories `0755`. No sync here: the caller's protocol syncs the tree.
    A malformed archive is `TreeError`."""
    _make_dir(dest)
    _make_dir(dest / "yaml")
    try:
        with tarfile.open(fileobj=io.BytesIO(sources.sdist), mode="r:gz") as tar:
            _extract(tar, dest / "yaml", _sdist_rel)
        _make_dir(dest / "steward")
        with (
            _archive(git, sources) as stream,
            tarfile.open(fileobj=stream, mode="r|") as tar,
        ):
            _extract(tar, dest / "steward", _steward_rel)
    except (tarfile.TarError, EOFError) as exc:
        raise TreeError(f"{dest}: архив отказан: {exc}") from exc


def _sdist_rel(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """`<top>/lib/yaml/<rel>` -> `<rel>`; anything else is not taken."""
    if len(parts) > 3 and parts[1:3] == YAML_SOURCE_DIR:
        return parts[3:]
    return None


def _steward_rel(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """`src/steward/<rel>` -> `<rel>`."""
    head = tuple(STEWARD_SOURCE_DIR.split("/"))
    if len(parts) > len(head) and parts[: len(head)] == head:
        return parts[len(head) :]
    return None


def _extract(
    tar: tarfile.TarFile,
    dest: Path,
    taken: Callable[[tuple[str, ...]], tuple[str, ...] | None],
) -> None:
    written = 0
    for member in tar:
        parts = _member_parts(member.name)
        rel = taken(parts)
        if rel is None:
            continue
        if any(
            name.casefold() == "__pycache__" or forbidden_name(name) for name in rel
        ):
            raise TreeError(f"{member.name}: запрещённое имя в site/")
        target = dest.joinpath(*rel)
        if member.isdir():
            _make_parents(dest, rel)
            continue
        if not member.isreg():
            raise TreeError(f"{member.name}: не файл и не каталог")
        _make_parents(dest, rel[:-1])
        source = tar.extractfile(member)
        if source is None:
            raise TreeError(f"{member.name}: не читается")
        _write_member(target, source, 0o755 if member.mode & 0o111 else 0o644)
        written += 1
    if written == 0:
        raise TreeError(f"{dest}: в архиве нет файлов пакета")


def _member_parts(name: str) -> tuple[str, ...]:
    if name.startswith("/") or "\\" in name:
        raise TreeError(f"{name!r}: абсолютное имя в архиве")
    parts = tuple(p for p in PurePosixPath(name).parts if p != ".")
    if ".." in parts:
        raise TreeError(f"{name!r}: '..' в архиве")
    return parts


def _make_parents(dest: Path, rel: tuple[str, ...]) -> None:
    current = dest
    for name in rel:
        current = current / name
        try:
            _make_dir(current)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise TreeError(f"{current}: не каталог") from None


def _make_dir(path: Path) -> None:
    os.mkdir(path, 0o700)
    fd = os.open(path, _DIR_FLAGS)
    try:
        os.fchmod(fd, 0o755)
    finally:
        os.close(fd)


def _write_member(path: Path, source: IO[bytes], mode: int) -> None:
    fd = os.open(path, _NEW_FILE, 0o600)
    try:
        os.fchmod(fd, mode)
        while chunk := source.read(_CHUNK):
            _write_all(fd, chunk)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


@contextmanager
def _archive(git: Path, sources: SiteSources) -> Iterator[IO[bytes]]:
    """`git archive <commit> src/steward` of the cached repo as a stream;
    a non-zero exit (after the stream is read) is `TreeError`, an early
    exit of the reader kills the process group."""
    argv = [
        str(git),
        "-C",
        str(sources.steward_repo),
        "archive",
        "--format=tar",
        sources.steward_commit,
        STEWARD_SOURCE_DIR,
    ]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", **GIT_ISOLATION}
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd="/",
        start_new_session=True,
    )
    assert proc.stdout is not None
    done = False
    try:
        yield proc.stdout
        proc.stdout.read()  # drain the end-of-archive padding
        done = True
    finally:
        proc.stdout.close()
        if not done:
            _kill_group(proc)
        code = _wait(proc)
    if code != 0:
        raise TreeError(f"git archive (steward): код {code}")


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the group; gone already (macOS: EPERM for a group of zombies)
    is fine."""
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


def _wait(proc: subprocess.Popen[bytes]) -> int:
    try:
        return proc.wait(timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return proc.wait()
