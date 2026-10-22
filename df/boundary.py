"""Execution-chain boundary, entrypoint inventory, transfer check (spec 3.4).

`static_problems` inspects a release as data (acceptance and `doctor`): what
runs from `venv/*/bin`, which interpreter and configuration it starts with,
what every native file loads, and what CPython reads at start. Effects: none
(`otool`/`readelf` run as root through `linked_libraries`, never the file).

`transfer_check` (spec 3.3 item 5) runs the release's own interpreters and
every expected entrypoint as an unprivileged role without network. It never
removes anything: leftovers of `build_home` are a refusal.

Every path is compared after in-tree resolution (`resolve_in_tree`): uv's
venvs name the managed Python through its minor-version alias link.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from df import platform as plat
from df.build import PYTHON_TRIPLES
from df.digest import (
    TreeError,
    canonical_root,
    check_links,
    check_name,
    open_regular,
    resolve_in_tree,
)
from df.inputs import pth_problems, read_capped, select_names
from df.journal import StepFailed
from df.paths import Layout
from df.platform import PlatformName, RunResult

BinClass = Literal["native", "script", "other"]

#: Seconds allowed for each run of the transfer check (find, sys.path, --help).
RUN_TIMEOUT = 60.0
#: The whole environment of every transfer-check run besides `HOME`.
RUN_ENV: Mapping[str, str] = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
#: Printed by each environment's interpreter, without and with `-I`.
SYS_PATH_PROBE = (
    "import json,site,sys;"
    "print(json.dumps([sys.path, site.ENABLE_USER_SITE, sys.version]))"
)
PYVENV_CAP = 1 << 16
PYPROJECT_CAP = 1 << 20
_FIND = "/usr/bin/find"
_SITECUSTOMIZE = "sitecustomize"
_PTH = "._pth"
_PYVENV = "pyvenv.cfg"
_KEY = re.compile(r"cpython-3\.(\d+)\.\d+-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")
_STDLIB = re.compile(r"python3\.\d+")
_ERRORS = (TreeError, StepFailed, OSError, ValueError)
#: Tokens naming the file's own directory (and, Mach-O, the main program's).
_MACHO_TOKENS = ("@loader_path", "@executable_path")
_ELF_TOKENS = ("$ORIGIN", "${ORIGIN}")


def expected_entrypoints(ref_dir: Path) -> frozenset[str]:
    """Names of `[project.scripts]` of a packaged member (spec 3.4).

    `ref_dir/pyproject.toml` of the trusted checkout is read as data. A
    member is a package if `tool.uv.package` is `true`, or it is unset and
    `[build-system]` exists; otherwise nothing is expected. Dynamic scripts,
    a non-bool `package` and a name unfit for a file are `ValueError`; an
    unreadable file is `TreeError`.
    """
    raw = read_capped(ref_dir / "pyproject.toml", PYPROJECT_CAP)
    data = tomllib.loads(raw.decode("utf-8"))
    project = _table(data, "project")
    package = _table(_table(data, "tool"), "uv").get("package")
    if package is not None and not isinstance(package, bool):
        raise ValueError(f"tool.uv.package не bool: {package!r}")
    if not (package if package is not None else "build-system" in data):
        return frozenset()
    if "scripts" in project.get("dynamic", []):
        raise ValueError("project.scripts объявлен dynamic: состав не задан данными")
    scripts = _table(project, "scripts")
    for name, value in scripts.items():
        check_name(name.encode("utf-8"))
        if not isinstance(value, str):
            raise ValueError(f"project.scripts.{name}: не строка")
    return frozenset(scripts)


def _table(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key}: не таблица")
    return value


def classify_bin(path: Path) -> BinClass:
    """Class of an executable by its first bytes: Mach-O/ELF magic, `#!`.

    The file must be regular (`open_regular`: a link is a `TreeError`).
    """
    head = _head(path, 4)
    if head in plat.MACHO_MAGIC or head == plat.ELF_MAGIC:
        return "native"
    return "script" if head.startswith(b"#!") else "other"


def _head(path: Path, size: int) -> bytes:
    fd = open_regular(path)
    try:
        data = b""
        while len(data) < size:
            chunk = os.read(fd, size - len(data))
            if not chunk:
                break
            data += chunk
        return data
    finally:
        os.close(fd)


# ---------- the environment's directories ----------


@dataclass(frozen=True)
class EnvPaths:
    """Resolved in-tree directories of one environment (relative to the release).

    `python` is the managed Python's real key directory `python/<key>`; the
    rest are what CPython puts on `sys.path` or reads at start. `zip_name` is
    `python3XY.zip`, which must be absent from `lib`.
    """

    repo: str
    python: PurePosixPath
    minor: str
    bin: PurePosixPath
    site: PurePosixPath

    @property
    def py_bin(self) -> PurePosixPath:
        return self.python / "bin"

    @property
    def lib(self) -> PurePosixPath:
        return self.python / "lib"

    @property
    def stdlib(self) -> PurePosixPath:
        return self.lib / f"python3.{self.minor}"

    @property
    def dynload(self) -> PurePosixPath:
        return self.stdlib / "lib-dynload"

    @property
    def zip_name(self) -> str:
        return f"python3{self.minor}.zip"

    @property
    def interpreter(self) -> PurePosixPath:
        return self.py_bin / f"python3.{self.minor}"


def env_paths(release_dir: Path, repo: str) -> EnvPaths:
    """Read `venv/<repo>/pyvenv.cfg` (rule (б)) and resolve the environment.

    Parsed as CPython does (`key.strip().lower()`, `value.strip()`); exactly
    one `home` and one `include-system-site-packages`, the latter `false`.
    `home` is canonical, under the release and resolves in-tree to
    `python/<key>/bin` with `<key>` = `cpython-3.X.Y-<triple>` holding
    exactly one `lib/python3.X`. Anything else is a `TreeError` that starts
    with `venv/<repo>: `.
    """
    venv = PurePosixPath("venv", repo)
    try:
        return _env_paths(release_dir, venv)
    except (TreeError, OSError, ValueError) as exc:
        raise TreeError(f"{venv}: {exc}") from exc


def _env_paths(release_dir: Path, venv: PurePosixPath) -> EnvPaths:
    if not _is_real_dir(release_dir / venv):
        raise TreeError("не каталог")
    if not _is_real_dir(release_dir / venv / "bin"):
        raise TreeError("bin: не каталог")
    cfg = _pyvenv_cfg(release_dir / venv)
    home = cfg.get("home", [])
    system = cfg.get("include-system-site-packages", [])
    if len(home) != 1 or len(system) != 1:
        raise TreeError(
            f"{_PYVENV}: home {len(home)} раз, "
            f"include-system-site-packages {len(system)} раз (нужно ровно по 1)"
        )
    if system[0].lower() != "false":
        raise TreeError(f"{_PYVENV}: include-system-site-packages = {system[0]}")
    python = _home_key(release_dir, home[0])
    minor = _minor(release_dir, python)
    site = PurePosixPath("lib", f"python3.{minor}", "site-packages")
    for path in (site.parent.parent, site.parent, site):
        if not _is_real_dir(release_dir / venv / path):
            raise TreeError(f"{path}: не каталог")
    return EnvPaths(venv.name, python, minor, venv / "bin", venv / site)


def _pyvenv_cfg(venv: Path) -> dict[str, list[str]]:
    found = select_names(venv, exact=_PYVENV)
    if not found:
        raise TreeError(f"нет {_PYVENV}")
    text = read_capped(found[0], PYVENV_CAP).decode("utf-8")
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values.setdefault(key.strip().lower(), []).append(value.strip())
    return values


def _home_key(release_dir: Path, home: str) -> PurePosixPath:
    """`python/<key>` that `home` resolves to; `home` itself canonical."""
    try:
        parts = canonical_root(home)
    except TreeError as exc:
        raise TreeError(f"{_PYVENV}: home не канонический: {home!r}") from exc
    root = canonical_root(str(release_dir))
    if parts[: len(root)] != root:
        raise TreeError(f"{_PYVENV}: home вне релиза: {home!r}")
    try:
        resolved = resolve_in_tree(release_dir, str(release_dir), home)
    except TreeError as exc:
        raise TreeError(f"{_PYVENV}: home {home!r}: {exc}") from exc
    if (
        len(resolved.parts) != 3
        or resolved.parts[0] != "python"
        or resolved.parts[2] != "bin"
        or not _KEY.fullmatch(resolved.parts[1])
    ):
        raise TreeError(f"{_PYVENV}: home {home!r} → {resolved}: не python/<ключ>/bin")
    return resolved.parent


def _minor(release_dir: Path, python: PurePosixPath) -> str:
    match = _KEY.fullmatch(python.name)
    if match is None:
        raise TreeError(f"{python}: не ключ Python")
    lib = release_dir / python / "lib"
    found = [p.name for p in select_names(lib, prefix="python3.")]
    stdlib = [n for n in found if _STDLIB.fullmatch(n)]
    if stdlib != [f"python3.{match.group(1)}"]:
        raise TreeError(f"{python}/lib: каталоги python3.X {found}, ключ {python.name}")
    return match.group(1)


def _is_real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


# ---------- static problems ----------


def static_problems(
    release_dir: Path, platform: PlatformName, expected: Mapping[str, frozenset[str]]
) -> list[str]:
    """Problems of the release as data (spec 3.4); empty means it passes.

    Never raises for the tree: every failure is a `"<rel>: <detail>"` line.
    The environments are exactly `expected`'s keys. Checks: (а) executables
    of `venv/*/bin` and their two exact script forms; (б) `pyvenv.cfg`, and
    none in `bin`; (в) every Mach-O/ELF; (г) links; (д) expected entrypoints;
    (е) `.pth`; (ё) `sitecustomize`, `._pth` and `python3XY.zip`. Not closed
    by them: code without distribution metadata (`DECLARED_LIMIT`).
    """
    problems = _guard("ссылки", lambda: check_links(release_dir, str(release_dir)))
    envs: dict[str, EnvPaths] = {}
    problems += _venv_set(release_dir, expected)
    for repo in sorted(expected):
        try:
            envs[repo] = env_paths(release_dir, repo)
        except TreeError as exc:
            problems.append(str(exc))
    python = _one_python(envs, platform, problems)
    for env in envs.values():
        problems += _env_problems(release_dir, env, expected[env.repo])
    if python is not None:
        problems += _python_tree_problems(release_dir, python)
    py_bin = None if python is None else python.py_bin
    problems += _NativeCheck(release_dir, platform, py_bin).problems()
    return problems


def _guard(label: str, check: Callable[[], list[str]]) -> list[str]:
    try:
        return check()
    except _ERRORS as exc:
        return [f"{label}: {exc}"]


def _venv_set(release_dir: Path, expected: Mapping[str, frozenset[str]]) -> list[str]:
    """`venv/` holds exactly the expected environments (F11)."""
    venv = release_dir / "venv"
    if not expected and not os.path.lexists(venv):
        return []
    try:
        names = set(os.listdir(venv))
    except OSError as exc:
        return [f"venv: {exc.strerror or exc}"]
    return [
        f"venv/{n}: окружение вне профиля не проверяется"
        for n in sorted(names - set(expected))
    ]


def _one_python(
    envs: Mapping[str, EnvPaths], platform: PlatformName, problems: list[str]
) -> EnvPaths | None:
    """The one managed Python every environment names (F19)."""
    keys = sorted({env.python for env in envs.values()})
    if len(keys) > 1:
        problems.append(f"окружения ссылаются на разные Python: {keys}")
        return None
    if not keys:
        return None
    triple = PYTHON_TRIPLES[platform]
    if not keys[0].name.endswith(f"-{triple}"):
        problems.append(f"{keys[0]}: Python не для платформы {platform}")
        return None
    return next(iter(envs.values()))


def _env_problems(release_dir: Path, env: EnvPaths, want: frozenset[str]) -> list[str]:
    problems = _guard(f"venv/{env.repo}", lambda: _bin_problems(release_dir, env))
    problems += _guard(
        f"{env.bin}", lambda: _entrypoint_problems(release_dir, env, want)
    )
    problems += _guard(
        f"venv/{env.repo}", lambda: pth_problems(release_dir / "venv" / env.repo)
    )
    problems += _guard(f"{env.site}", lambda: _sitecustomize(release_dir, env.site))
    problems += _guard(f"{env.bin}", lambda: _pth_files(release_dir, env.bin))
    return problems


def _python_tree_problems(release_dir: Path, env: EnvPaths) -> list[str]:
    """(ё) for the managed Python: its `sys.path` dirs and `._pth` places."""
    problems: list[str] = []
    for directory in (env.stdlib, env.dynload):
        problems += _guard(
            str(directory), lambda d=directory: _sitecustomize(release_dir, d)
        )
    for directory in (env.py_bin, env.lib):
        problems += _guard(
            str(directory), lambda d=directory: _pth_files(release_dir, d)
        )
    problems += _guard(
        str(env.lib),
        lambda: [
            f"{env.lib / p.name}: {p.name} читается из sys.path"
            for p in select_names(
                release_dir / env.lib, exact=env.zip_name, strict=False
            )
        ],
    )
    return problems


def _sitecustomize(release_dir: Path, directory: PurePosixPath) -> list[str]:
    """Any top-level `sitecustomize` module, extension or package (casefold)."""
    found = select_names(release_dir / directory, prefix=_SITECUSTOMIZE, strict=False)
    return [
        f"{directory / p.name}: sitecustomize исполняется при старте Python"
        for p in found
        if p.name.casefold() == _SITECUSTOMIZE
        or p.name.casefold().startswith(f"{_SITECUSTOMIZE}.")
    ]


def _pth_files(release_dir: Path, directory: PurePosixPath) -> list[str]:
    found = select_names(release_dir / directory, suffix=_PTH, strict=False)
    return [f"{directory / p.name}: ._pth заменяет вычисление sys.path" for p in found]


def _bin_problems(release_dir: Path, env: EnvPaths) -> list[str]:
    """(а) and F2/F4: every name of `venv/<r>/bin` that can run or configure."""
    problems = [
        f"{env.bin / p.name}: второй pyvenv.cfg читается первым"
        for p in select_names(release_dir / env.bin, exact=_PYVENV, strict=False)
    ]
    links: dict[str, PurePosixPath] = {}
    for name in sorted(os.listdir(release_dir / env.bin)):
        rel = env.bin / name
        st = os.lstat(release_dir / rel)
        if stat.S_ISLNK(st.st_mode):
            problems += _link_problem(release_dir, env, name, links)
        elif stat.S_ISREG(st.st_mode) and st.st_mode & 0o111:
            problems += _guard(
                str(rel), lambda r=rel: _exec_problems(release_dir, env, r)
            )
    if "python" not in links:
        problems.append(f"{env.bin}/python: нет интерпретатора окружения")
    return problems


def _link_names(env: EnvPaths) -> frozenset[str]:
    """Links uv writes into a venv's `bin`; each resolves to the interpreter."""
    return frozenset({"python", "python3", f"python3.{env.minor}"})


def _link_problem(
    release_dir: Path, env: EnvPaths, name: str, links: dict[str, PurePosixPath]
) -> list[str]:
    rel = env.bin / name
    if name not in _link_names(env):
        return [f"{rel}: посторонняя ссылка в bin"]
    try:
        target = resolve_in_tree(release_dir, str(release_dir), rel)
    except TreeError as exc:
        return [f"{rel}: {exc}"]
    if target != env.interpreter:
        return [f"{rel}: ведёт в {target}, а не в {env.interpreter}"]
    links[name] = target
    if classify_bin(release_dir / target) != "native":
        return [f"{rel}: {target} не нативный исполняемый файл"]
    return []


def script_heads(release_dir: Path, repo: str) -> tuple[bytes, bytes]:
    """The two exact heads uv writes for a script of `venv/<repo>` (F1):
    a direct `#!` and, above the kernel's shebang limit, the sh trampoline.
    Both name the environment's own `bin/python`."""
    python = os.fsencode(release_dir / "venv" / repo / "bin" / "python")
    direct = b"#!" + python + b"\n"
    trampoline = b"#!/bin/sh\n'''exec' '" + python + b"' \"$0\" \"$@\"\n' '''\n"
    return direct, trampoline


def _exec_problems(release_dir: Path, env: EnvPaths, rel: PurePosixPath) -> list[str]:
    kind = classify_bin(release_dir / rel)
    if kind == "native":
        return []  # rule (в) checks it with every Mach-O/ELF of the release
    if kind == "other":
        return [f"{rel}: исполняемый файл ни нативный, ни скрипт с #!"]
    heads = script_heads(release_dir, env.repo)
    head = _head(release_dir / rel, max(len(h) for h in heads))
    if any(head.startswith(h) for h in heads):
        return []
    line = head.split(b"\n", 1)[0][:200]
    return [f"{rel}: интерпретатор {line!r} не python своего окружения"]


def _entrypoint_problems(
    release_dir: Path, env: EnvPaths, want: frozenset[str]
) -> list[str]:
    """(д): each expected name, exact case, an executable `#!` script."""
    problems: list[str] = []
    for name in sorted(want):
        try:
            found = select_names(release_dir / env.bin, exact=name)
        except TreeError as exc:
            problems.append(f"{env.bin / name}: {exc}")
            continue
        if not found:
            problems.append(f"{env.bin / name}: нет ожидаемого entrypoint")
            continue
        st = os.lstat(found[0])
        if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o111:
            problems.append(f"{env.bin / name}: не исполняемый обычный файл")
        elif classify_bin(found[0]) != "script":
            problems.append(f"{env.bin / name}: entrypoint не скрипт с #!")
    return problems


# ---------- (в) native files ----------


@dataclass
class _NativeCheck:
    """Rule (в) over every regular Mach-O/ELF of the release (F9)."""

    tree: Path
    platform: PlatformName
    py_bin: PurePosixPath | None

    def problems(self) -> list[str]:
        try:
            files = list(_regular_files(self.tree, PurePosixPath()))
        except OSError as exc:
            return [f"обход релиза: {exc}"]
        problems: list[str] = []
        for rel in files:
            try:
                head = _head(self.tree / rel, 4)
                if head in plat.MACHO_MAGIC or head == plat.ELF_MAGIC:
                    problems += self.file(rel, head in plat.MACHO_MAGIC)
            except _ERRORS as exc:
                problems.append(f"{rel}: {exc}")
        return problems

    def file(self, rel: PurePosixPath, macho: bool) -> list[str]:
        deps = plat.linked_libraries(self.tree / rel)
        if deps is None:
            return [f"{rel}: не разобран как Mach-O/ELF"]
        out = [
            f"{rel}: LC_DYLD_ENVIRONMENT {e} запрещён" for e in deps.dyld_environment
        ]
        if deps.loader is not None and not plat.is_system_path(
            Path(deps.loader), self.platform
        ):
            out.append(f"{rel}: загрузчик {deps.loader} вне системных каталогов")
        inside: list[PurePosixPath] = []
        system: list[str] = []
        for entry in deps.rpaths:
            try:
                found = self.rpath(rel, entry, macho)
            except TreeError as exc:
                out.append(f"{rel}: путь поиска {entry!r}: {exc}")
                continue
            (inside if isinstance(found, PurePosixPath) else system).append(found)
        for dep in deps.needed:
            out += [
                f"{rel}: зависимость {dep}: {p}"
                for p in self.dependency(rel, dep, macho, inside, system)
            ]
        return out

    def rpath(self, rel: PurePosixPath, entry: str, macho: bool) -> PurePosixPath | str:
        """An in-tree directory, or a system directory as text; else `TreeError`."""
        text = self.substitute(rel, entry, macho)
        if text is None:
            if plat.is_system_path(Path(entry), self.platform):
                return entry.rstrip("/")
            raise TreeError("вне релиза и системных каталогов")
        resolved = self.resolve(text)
        if not _is_real_dir(self.tree / resolved):
            raise TreeError(f"{resolved}: не каталог")
        return resolved

    def dependency(
        self,
        rel: PurePosixPath,
        dep: str,
        macho: bool,
        inside: list[PurePosixPath],
        system: list[str],
    ) -> list[str]:
        if (macho and dep.startswith("@rpath/")) or (not macho and "/" not in dep):
            name = dep.removeprefix("@rpath/")
            return self.search(rel, dep, name, inside, system, loader=not macho)
        try:
            text = self.substitute(rel, dep, macho)
            if text is None:
                return _strip(
                    plat.dependency_problems(self.tree / rel, dep, self.platform)
                )
            self.regular(text)
        except TreeError as exc:
            return [str(exc)]
        return []

    def search(
        self,
        rel: PurePosixPath,
        dep: str,
        name: str,
        inside: list[PurePosixPath],
        system: list[str],
        *,
        loader: bool,
    ) -> list[str]:
        """`@rpath/x` or an ELF soname: the file's own rpaths, then (ELF) the
        loader's system directories."""
        for directory in inside:
            try:
                self.regular(str(directory / name))
                return []
            except TreeError:
                continue
        target = self.tree / rel
        for directory in system:
            if not plat.dependency_problems(
                target, f"{directory}/{name}", self.platform
            ):
                return []
        if loader:
            return _strip(plat.dependency_problems(target, dep, self.platform))
        return ["не найдена по своим путям поиска внутри релиза"]

    def substitute(self, rel: PurePosixPath, text: str, macho: bool) -> str | None:
        """`text` as an in-tree path (relative to the tree, or absolute under
        the release); `None` for an absolute path outside the release."""
        for token in _MACHO_TOKENS if macho else _ELF_TOKENS:
            if text == token or text.startswith(f"{token}/"):
                if token == "@executable_path" and rel.parent != self.py_bin:
                    raise TreeError("@executable_path только у python/<ключ>/bin")
                return f"{rel.parent}{text[len(token) :]}"
        if text.startswith("@") or "$" in text or not text.startswith("/"):
            raise TreeError("не поддерживается: подстановка или относительный путь")
        root = str(self.tree)
        return text if text == root or text.startswith(f"{root}/") else None

    def resolve(self, text: str) -> PurePosixPath:
        return resolve_in_tree(self.tree, str(self.tree), text)

    def regular(self, text: str) -> None:
        resolved = self.resolve(text)
        if not stat.S_ISREG(os.lstat(self.tree / resolved).st_mode):
            raise TreeError(f"{resolved}: не обычный файл")


def _strip(problems: list[str]) -> list[str]:
    """`dependency_problems` lines without their leading target path."""
    return [p.split(": ", 1)[-1] for p in problems]


def _regular_files(tree: Path, prefix: PurePosixPath) -> Iterator[PurePosixPath]:
    """Every regular file, links never followed."""
    with os.scandir(tree / prefix) as entries:
        listing = sorted(entries, key=lambda entry: entry.name)
    for entry in listing:
        rel = prefix / entry.name
        if entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            yield from _regular_files(tree, rel)
        elif entry.is_file(follow_symlinks=False):
            yield rel


# ---------- transfer check ----------


@dataclass(frozen=True)
class TransferResult:
    """Problems of the transfer check and each environment's `sys.version`
    (declared: the interpreter's own answer)."""

    problems: list[str]
    python_declared: dict[str, str]


def transfer_check(
    release_dir: Path,
    layout: Layout,
    *,
    user: str,
    expected: Mapping[str, frozenset[str]],
) -> TransferResult:
    """Run the release from `user` without network (spec 3.3 item 5).

    Effects: `run_as(df-build)+`; removes nothing. First `find` as `user`:
    any `build_home/tmp` or `build_home/cache` is a problem and nothing else
    runs. Then for each environment its `bin/python` without and with `-I`
    (rule (ж): every non-empty `sys.path` element resolves in-tree to its own
    `site-packages`, the stdlib or `lib-dynload`, or is the absent
    `python3XY.zip`; `ENABLE_USER_SITE` false), and every expected
    entrypoint with `--help` (rule (з): code 0 within the deadline).
    """
    runner = _Runner(release_dir, layout, user)
    problems = runner.leftovers()
    if problems:
        return TransferResult(problems, {})
    declared: dict[str, str] = {}
    for repo in sorted(expected):
        try:
            env = env_paths(release_dir, repo)
        except TreeError as exc:
            problems.append(str(exc))
            continue
        version = runner.sys_path(env, problems)
        if version is not None:
            declared[repo] = version
        for name in sorted(expected[repo]):
            runner.help(env.bin / name, problems)
    return TransferResult(problems, declared)


@dataclass
class _Runner:
    """The runs of one transfer check, all under one outcome rule (F16)."""

    release_dir: Path
    layout: Layout
    user: str

    def run(self, argv: list[str]) -> RunResult:
        home = self.layout.build_home
        return plat.run_as(
            self.user,
            argv,
            env={"HOME": str(home), **RUN_ENV},
            cwd=home,
            network=False,
            timeout=RUN_TIMEOUT,
        )

    def leftovers(self) -> list[str]:
        home = self.layout.build_home
        argv = [_FIND, str(home), "-mindepth", "1", "-maxdepth", "1"]
        argv += ["(", "-name", "tmp", "-o", "-name", "cache", ")", "-print"]
        result = self.run(argv)
        failed = _outcome(f"поиск остатков в {home}", result)
        if failed is not None:  # any failure of the find is infrastructure
            return [f"инфраструктура: {failed.removeprefix('инфраструктура: ')}"]
        if result.stdout:
            found = result.stdout.decode("utf-8", "replace").split()
            return [f"остатки build_home: {found}; проверка переноса недоказательна"]
        return []

    def sys_path(self, env: EnvPaths, problems: list[str]) -> str | None:
        """Rule (ж) for one environment; returns `sys.version` (both runs agree)."""
        python = str(self.release_dir / env.bin / "python")
        versions: set[str] = set()
        for flags in ([], ["-I"]):
            label = " ".join([python, *flags])
            result = self.run([python, *flags, "-c", SYS_PATH_PROBE])
            failed = _outcome(label, result)
            if failed is not None:
                problems.append(failed)
                continue
            try:
                path, user_site, version = _parse_probe(result.stdout)
            except ValueError as exc:
                problems.append(f"{label}: ответ не разобран: {exc}")
                continue
            if user_site is not False:
                problems.append(f"{label}: ENABLE_USER_SITE={user_site}")
            problems += [f"{label}: {p}" for p in self.path_problems(env, path)]
            versions.add(version)
        if len(versions) > 1:
            problems.append(f"{python}: разные sys.version с -I и без: {versions}")
            return None
        return versions.pop() if versions else None

    def path_problems(self, env: EnvPaths, path: list[str]) -> list[str]:
        allowed = {env.site, env.stdlib, env.dynload}
        problems: list[str] = []
        for item in path:
            if item == "":
                continue
            if not item.startswith("/"):
                problems.append(f"sys.path {item!r}: не абсолютный путь")
                continue
            try:
                resolved = resolve_in_tree(
                    self.release_dir, str(self.release_dir), item
                )
            except TreeError as exc:
                if not self.absent_zip(env, item):
                    problems.append(f"sys.path {item!r}: {exc}")
                continue
            if resolved not in allowed:
                problems.append(
                    f"sys.path {item!r} → {resolved}: не проверенный каталог"
                )
        return problems

    def absent_zip(self, env: EnvPaths, item: str) -> bool:
        """F5: `<lib>/python3XY.zip` that does not exist (its lib is checked)."""
        name = PurePosixPath(item)
        if name.name != env.zip_name:
            return False
        try:
            parent = resolve_in_tree(
                self.release_dir, str(self.release_dir), name.parent
            )
        except TreeError:
            return False
        return parent == env.lib and not os.path.lexists(
            self.release_dir / env.lib / env.zip_name
        )

    def help(self, script: PurePosixPath, problems: list[str]) -> None:
        """Rule (з): `--help` exits 0 within the deadline; stdout ignored."""
        path = str(self.release_dir / script)
        failed = _outcome(f"{path} --help", self.run([path, "--help"]))
        if failed is not None:
            problems.append(failed)


def _outcome(label: str, result: RunResult) -> str | None:
    """One rule for every run: `None` means it ran and exited 0."""
    tail = result.stderr[-2048:].decode("utf-8", "replace").strip()
    if not result.launched:
        return f"инфраструктура: {label}: запуск не состоялся: {tail}"
    if result.timed_out:
        return f"{label}: срок {RUN_TIMEOUT:g} с истёк"
    if result.stdout_truncated:
        return f"{label}: stdout переполнен"
    if result.returncode != 0:
        return f"{label}: код {result.returncode}: {tail}"
    return None


def _parse_probe(stdout: bytes) -> tuple[list[str], object, str]:
    """`[sys.path, ENABLE_USER_SITE, sys.version]` exactly, else `ValueError`."""
    data = json.loads(stdout.decode("utf-8"))
    if not isinstance(data, list) or len(data) != 3:
        raise ValueError("не список из трёх элементов")
    path, user_site, version = data
    if not isinstance(path, list) or not all(isinstance(p, str) for p in path):
        raise ValueError("sys.path не список строк")
    if not isinstance(user_site, bool) or not isinstance(version, str):
        raise ValueError("ENABLE_USER_SITE не bool или sys.version не строка")
    return path, user_site, version
