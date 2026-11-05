"""Contents of the administrative tree beyond `df/` (spec 3.9 rev 11, 2b Task 5).

`/opt/df/admin/<id>/` carries, next to `df/`, what the precheck program
`governance/act_precheck.py` runs on: its import closure and the contract
files it reads relative to its own `__file__` -- both listed by the pin's
`df/precheck_tree.toml` (`TREE_LIST`, read as data; a legacy pin without it
gets `LEGACY_2B`) -- and `site/` -- pure Python only: `yaml/` from
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

import ast
import hashlib
import io
import os
import re
import select
import ssl
import stat
import subprocess
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import IO

from df import platform as plat
from df.build import GIT_ISOLATION
from df.digest import TreeError
from df.inputs import read_capped
from df.paths import Layout
from df.sources import UnsupportedLock, source_from_lock

#: The precheck program: its presence makes a checkout a precheck release.
PRECHECK_FILE = "governance/act_precheck.py"
PRECHECK_MODULE = "governance.act_precheck"
#: The pin's list of the precheck tree (spec 3.9 rev 11): data inside `df/`,
#: which every installer copies whole.
TREE_LIST = "df/precheck_tree.toml"
TREE_SCHEMA = "df/precheck-tree/v1"
TREE_LIST_CAP = 64 << 10
#: Bytes a module of the pin may have when its imports are read.
MODULE_CAP = 4 << 20
#: Why `precheck serve` cannot serve v2 on a tree (`doctor` names it).
INCOMPLETE_TREE = (
    "дерево предпроверки собрано установщиком старше перечня "
    f"{TREE_LIST} (запросы v2 отказаны) — переустановите релиз"
)
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
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
FETCH_DEADLINE = 600.0
SDIST_CAP = 64 << 20
STDERR_REPORT_BYTES = 4096

#: Flags of a new file in the admin tree or the cache: never an existing name.
NEW_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
#: Flags of a directory opened in the admin tree: never through a link.
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
#: Bytes per read and write of a copied file or a stream.
CHUNK = 1 << 20
#: Bytes a checkout's `uv.lock` may have.
LOCK_CAP = 16 << 20

Fetch = Callable[[str], bytes]


@dataclass(frozen=True)
class TreeList:
    """The precheck tree beyond `df/`: modules and repo-relative contract files.

    A listed module is a package (`<a>/<b>/__init__.py`) when another listed
    module lies inside it, else a plain module (`<a>/<b>.py`)."""

    modules: tuple[str, ...]
    contract_files: tuple[str, ...]

    def module_files(self) -> tuple[str, ...]:
        """Repo-relative files of `modules`, in their order."""
        return tuple(
            _module_rel(
                name, package=any(m.startswith(f"{name}.") for m in self.modules)
            )
            for name in self.modules
        )

    def files(self) -> tuple[str, ...]:
        """Every repo-relative file of the list: modules, then contracts."""
        return (*self.module_files(), *self.contract_files)


#: The list of every pin without `TREE_LIST` (2b): the AST import closure of
#: its `act_precheck`, frozen. A pin's list keeps all of it (spec 3.9 rev 11).
LEGACY_2B = TreeList(
    modules=(
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
    ),
    contract_files=("contracts/approval-branches/v1/patterns.env",),
)


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
    git_env: Mapping[str, str]


def _module_rel(name: str, *, package: bool) -> str:
    base = "/".join(name.split("."))
    return f"{base}/__init__.py" if package else f"{base}.py"


def load_tree_list(devtools_ref: Path) -> TreeList:
    """The `TREE_LIST` of the checkout (or tree) `devtools_ref`, read as data
    (`tomllib`, nothing of the pin executed); no file is a legacy pin:
    `LEGACY_2B`. Exactly the keys `schema`, `modules`, `contract_files`;
    modules are dotted names, contract files relative paths without `.` or
    `..`, neither repeated; anything else is `TreeError`."""
    path = devtools_ref / TREE_LIST
    if not os.path.lexists(path):
        return LEGACY_2B
    try:
        data = tomllib.loads(read_capped(path, TREE_LIST_CAP).decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TreeError(f"{path}: не разбирается: {exc}") from exc
    if set(data) != {"schema", "modules", "contract_files"}:
        raise TreeError(f"{path}: ключи {sorted(data)}")
    if data["schema"] != TREE_SCHEMA:
        raise TreeError(f"{path}: schema {data['schema']!r}, нужна {TREE_SCHEMA}")
    modules = _names(path, data["modules"], _MODULE_NAME.fullmatch)
    contracts = _names(path, data["contract_files"], _contract_rel)
    return TreeList(modules, contracts)


def _names(
    path: Path, value: object, valid: Callable[[str], object]
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TreeError(f"{path}: не список строк: {value!r}")
    bad = [v for v in value if not valid(v)]
    if bad or len(set(value)) != len(value):
        raise TreeError(f"{path}: недопустимые или повторённые имена: {value!r}")
    return tuple(value)


def _contract_rel(rel: str) -> bool:
    parts = rel.split("/")
    return (
        all(p not in ("", ".", "..") for p in parts)
        and "\\" not in rel
        and not forbidden_name(rel)
    )


def closure_problems(devtools_ref: Path, listing: TreeList) -> list[str]:
    """Why the AST import closure of the pin's `act_precheck.py` (every
    `import` anywhere in a module, deferred ones included; stdlib `ast`, no
    code of the pin executed) is not within `listing.modules` -- empty: it
    is. A module of the checkout outside the list, a listed one resolving
    to another file, a relative import or an unreadable module is named."""
    listed = dict(zip(listing.modules, listing.module_files(), strict=True))
    found, problems = _closure(devtools_ref)
    for name, rel in sorted(found.items()):
        if name not in listed:
            problems.append(f"{name} ({rel}) не в {TREE_LIST}")
        elif listed[name] != rel:
            problems.append(f"{name}: в {TREE_LIST} {listed[name]}, в пине {rel}")
    return problems


def _closure(root: Path) -> tuple[dict[str, str], list[str]]:
    """({module: repo-relative file}, problems) of `PRECHECK_MODULE` in
    `root`: a name is a module of the checkout when its file is there (a
    package's parent is part of the closure); other names are external."""
    found: dict[str, str] = {}
    problems: list[str] = []
    todo = [PRECHECK_MODULE]
    while todo:
        name = todo.pop()
        rel = _module_in(root, name)
        if name in found or rel is None:
            continue
        found[name] = rel
        if "." in name:
            todo.append(name.rsplit(".", 1)[0])
        try:
            tree = ast.parse(read_capped(root / rel, MODULE_CAP), filename=rel)
        except (TreeError, SyntaxError, ValueError) as exc:
            problems.append(f"{rel}: не разбирается: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                todo += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level != 0 or not node.module:
                    problems.append(f"{rel}: относительный импорт")
                    continue
                todo += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    return found, problems


def _module_in(root: Path, name: str) -> str | None:
    """The repo-relative file of module `name` in `root` (a plain module
    first, then a package), or `None`: not a module of the checkout."""
    for package in (False, True):
        rel = _module_rel(name, package=package)
        if os.path.lexists(root / rel):
            return rel
    return None


def tree_problems(tree: Path, listing: TreeList) -> list[str]:
    """The files of `listing` that `tree` (a checkout or an admin tree)
    lacks, repo-relative; empty: none."""
    return [rel for rel in listing.files() if not os.path.lexists(tree / rel)]


def release_has_precheck(devtools_ref: Path) -> bool:
    """Whether the checkout carries the precheck program (spec 3.9 rev 10).

    No `governance/act_precheck.py`: a legacy release (`False`), its admin
    tree is `df/` only and `precheck serve` refuses on it. With it, its list
    (`load_tree_list`) must keep every file of `LEGACY_2B` (v1 runs on
    them; `precheck serve` refuses a tree without them) and every listed
    file must be there (`True`); otherwise `TreeError` naming the files --
    never a silent fallback. Modules of the closure an older release has
    for other reasons do not make it a precheck release."""
    if not os.path.lexists(devtools_ref / PRECHECK_FILE):
        return False
    listing = load_tree_list(devtools_ref)
    unlisted = [rel for rel in LEGACY_2B.files() if rel not in listing.files()]
    if unlisted:
        raise TreeError(
            f"{devtools_ref}: {TREE_LIST} без файлов предпроверки 2b: "
            + ", ".join(unlisted)
        )
    missing = tree_problems(devtools_ref, listing)
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


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another `https` URL."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError(f"перенаправление не на https: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(
    url: str,
    *,
    context: ssl.SSLContext | None = None,
    deadline: float = FETCH_DEADLINE,
) -> bytes:
    """GET the `https` URL `url` as root: no proxy or other setting from the
    environment, redirects only to `https` (no other scheme handler), at most
    `SDIST_CAP` bytes and `deadline` seconds for the whole download (each
    socket operation `FETCH_TIMEOUT`); anything else is `TreeError`.
    `context` is the TLS context (default: the system's verification)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise TreeError(f"{url}: не https")
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _HttpsOnlyRedirect(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    end = time.monotonic() + deadline
    data = b""
    try:
        with opener.open(url, timeout=FETCH_TIMEOUT) as response:
            while len(data) <= SDIST_CAP and (chunk := response.read(CHUNK)):
                data += chunk
                if time.monotonic() > end:
                    raise TreeError(f"{url}: срок {deadline} с истёк")
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
    missing or bad artifact is `TreeError`, never fetched.

    Trust: the sdist is re-hashed on every use, but git does not re-verify
    the content of the objects of `steward.git` it reads -- those bytes are
    trusted because `admin-home` is root's `0700` (step 2, F6), so only root
    can have written them. Every git call names the repository by
    `--git-dir` (no discovery of a parent repository)."""
    cache = layout.admin_home / SITE_SRC
    _ensure_cache_dir(cache)
    sdist = _sdist(cache, pins, fetch)
    repo = cache / STEWARD_REPO
    env = git_env(layout, git)
    if not _has_commit(git, repo, pins.steward_commit, env):
        if fetch is None:
            raise TreeError(f"{repo}: нет коммита {pins.steward_commit} (без сети)")
        _fetch_steward(git, repo, pins, env)
    return SiteSources(sdist, repo, pins.steward_commit, env)


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
    """The cached sdist (`None`: absent); an oversize or unreadable one is
    `b""`, which no pin matches, so it is replaced."""
    if not os.path.lexists(path):
        return None
    try:
        return read_capped(path, SDIST_CAP)
    except TreeError:
        return b""


def _write_cache(path: Path, data: bytes) -> None:
    """Cache `data` at `path`: an `O_EXCL` temp, then `rename` (scratch: a
    lost write is fetched again, so no sync)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, NEW_FILE, 0o600)
    try:
        write_all(fd, data)
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
    argv = [str(git), f"--git-dir={repo}", "cat-file", "--batch-check"]
    query = f"{commit}^{{commit}}\n{commit}^{{tree}}\n".encode()
    result = plat.run_with_input(argv, query, timeout=GIT_TIMEOUT, env=env)
    if not _ok(result):
        return False
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    kinds = [ln.split()[1] if len(ln.split()) == 3 else "" for ln in lines]
    return kinds == ["commit", "tree"] and lines[0].split()[0] == commit


def _fetch_steward(git: Path, repo: Path, pins: SitePins, env: dict[str, str]) -> None:
    """`git init --bare` (idempotent), fetch exactly the pinned commit,
    `FETCH_HEAD` equal to it, then `_has_commit`."""
    where = f"--git-dir={repo}"
    url, commit = pins.steward_url, pins.steward_commit
    _checked(_git(git, ["init", "-q", "--bare", "--template=", str(repo)], env), "init")
    _checked(_git(git, [where, "fetch", "-q", "--depth=1", url, commit], env), "fetch")
    head = _checked(_git(git, [where, "rev-parse", "FETCH_HEAD"], env), "rev-parse")
    got = head.decode("utf-8", "replace").strip()
    if got != commit:
        raise TreeError(f"steward: FETCH_HEAD {got!r}, в uv.lock {commit!r}")
    if not _has_commit(git, repo, commit, env):
        raise TreeError(f"steward: нет дерева коммита {commit}")


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
    make_tree_dir(dest)
    make_tree_dir(dest / "yaml")
    try:
        with tarfile.open(fileobj=io.BytesIO(sources.sdist), mode="r:gz") as tar:
            _extract(tar, dest / "yaml", _sdist_rel)
        make_tree_dir(dest / "steward")
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
            make_tree_dir(current)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise TreeError(f"{current}: не каталог") from None


def make_tree_dir(path: Path) -> None:
    """A new directory of the admin tree: born `0700`, then `fchmod` `0755`
    (whatever the umask); no sync (the protocol syncs the tree)."""
    os.mkdir(path, 0o700)
    fd = os.open(path, DIR_FLAGS)
    try:
        os.fchmod(fd, 0o755)
    finally:
        os.close(fd)


def _write_member(path: Path, source: IO[bytes], mode: int) -> None:
    fd = os.open(path, NEW_FILE, 0o600)
    try:
        os.fchmod(fd, mode)
        while chunk := source.read(CHUNK):
            write_all(fd, chunk)
    finally:
        os.close(fd)


def write_all(fd: int, data: bytes) -> None:
    """Write all of `data` to `fd` (short writes continued)."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


class _TimedStream(io.RawIOBase):
    """The stdout of `git archive` under one deadline for the whole stream
    (`GIT_TIMEOUT`, like every other git call here): a read that would pass
    it kills the process group and is `TreeError`."""

    def __init__(self, proc: subprocess.Popen[bytes], deadline: float) -> None:
        super().__init__()
        assert proc.stdout is not None
        self._proc = proc
        self._fd = proc.stdout.fileno()
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:
        remaining = self._deadline - time.monotonic()
        ready = remaining > 0 and select.select([self._fd], [], [], remaining)[0]
        if not ready:
            _kill_group(self._proc)
            raise TreeError(f"git archive (steward): срок {GIT_TIMEOUT:g} с истёк")
        data = os.read(self._fd, len(buffer))
        buffer[: len(data)] = data
        return len(data)


@contextmanager
def _archive(git: Path, sources: SiteSources) -> Iterator[IO[bytes]]:
    """`git archive <commit> src/steward` of the cached repo as a stream
    bounded by `GIT_TIMEOUT` (`_TimedStream`); a non-zero exit (after the
    stream is read) is `TreeError`, an early exit of the reader or an
    expired deadline kills the process group (a partial `admin/<id>` is left
    to the protocol's recovery)."""
    argv = [
        str(git),
        f"--git-dir={sources.steward_repo}",
        "archive",
        "--format=tar",
        sources.steward_commit,
        STEWARD_SOURCE_DIR,
    ]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=dict(sources.git_env),
        cwd="/",
        start_new_session=True,
    )
    assert proc.stdout is not None
    stream = _TimedStream(proc, time.monotonic() + GIT_TIMEOUT)
    done = False
    try:
        yield stream
        while stream.read(CHUNK):  # drain the end-of-archive padding
            pass
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
