"""The expected distribution set of a lock and the uv-config check (spec 3.5).

`expected_distributions` computes from `uv.lock` alone which distributions
an environment built with `uv sync --locked --no-editable --no-default-groups
--group …` must hold; `distribution_problems` compares it with what the
environment's metadata declares. `uv_config_problems` refuses uv settings
that `UV_NO_CONFIG=1` would silently drop from the build. Effects: none.
"""

from __future__ import annotations

import tomllib
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from df.inputs import InstalledDist, normalize_name
from df.markers import evaluate
from df.sources import SourceId, UnsupportedLock, source_from_lock

#: The uv version the allowlist below was probed against; a uv bump re-probes.
UV_PINNED_VERSION = "0.11.23"
#: `[tool.uv]` keys of uv 0.11.23 that describe the project and survive
#: `UV_NO_CONFIG=1`. Everything else is a setting the build would drop
#: silently, an install-time one (`compile-bytecode`, `no-binary`, …) without
#: any trace in the lock. Refused although they are project metadata:
#: `conflicts` (the lock refuses them anyway), `workspace` (v1 is
#: single-project), `managed` (changes how `uv sync` treats the project),
#: `dependency-groups` and `build-backend` (unprobed, unused).
UV_PROJECT_KEYS_0_11_23 = frozenset(
    {
        "package",
        "default-groups",
        "sources",
        "index",
        "dev-dependencies",
        "constraint-dependencies",
        "override-dependencies",
        "exclude-dependencies",
        "build-constraint-dependencies",
        "environments",
        "required-environments",
    }
)
#: Top-level `uv.lock` keys understood here; `[options]` (lock-time settings)
#: and any other key is refused. Shared by both checks of this module.
LOCK_TOP_KEYS = frozenset(
    {
        "version",
        "revision",
        "requires-python",
        "resolution-markers",
        "supported-markers",
        "required-markers",
        "manifest",
        "package",
    }
)
#: `[manifest]` keys that record project metadata (constraints, overrides).
LOCK_MANIFEST_KEYS = frozenset(
    {"constraints", "overrides", "excludes", "build-constraints"}
)
#: The named limit of the comparison (owner decision 22.10).
DECLARED_LIMIT = (
    "метаданные распределений согласованы с lock (заявлено); не доказывает "
    "отсутствия лишнего кода и происхождения файлов релиза"
)

_DEP_KEYS = frozenset({"name", "version", "source", "marker", "extra"})
#: `[[package]]` keys of a revision-3 lock; another key is a refusal.
_PACKAGE_KEYS = frozenset(
    {
        "name",
        "version",
        "source",
        "dependencies",
        "optional-dependencies",
        "dev-dependencies",
        "sdist",
        "wheels",
        "metadata",
        "resolution-markers",
    }
)
_ROOT_SOURCES = frozenset({("virtual", "."), ("editable", "."), ("directory", ".")})


@dataclass(frozen=True)
class ExpectedDist:
    """One expected distribution; `name` is PEP 503 normalised."""

    name: str
    version: str
    source: SourceId


def expected_distributions(
    lock: bytes,
    *,
    groups: tuple[str, ...],
    env: Mapping[str, str],
    root_dir: Path,
) -> frozenset[ExpectedDist]:
    """The distributions `lock` puts into an environment (spec 3.5).

    The lock must be `version = 1`, `revision = 3`, with no key outside
    `LOCK_TOP_KEYS`/`LOCK_MANIFEST_KEYS`. Every dependency record and every
    package source of the whole lock is checked first, reached or not, so a
    refusal does not depend on the platform: package keys (`_PACKAGE_KEYS`),
    record keys, markers, sources, each record's single target and the
    extras it asks for. Then a breadth-first walk:

    - from the root's dependencies and its `dev-dependencies[g]` for each
      `g` in `groups`, names compared normalised like uv's `--group` (a
      missing group is a refusal); root extras are not
      selected — the build passes no `--extra`;
    - group entries, like dependencies, are taken when their marker is true
      in `env` and carry their `extra`;
    - the visited key is (package, extra): a package reached plain and later
      with `[e]` still gets `optional-dependencies[e]`;
    - the target is chosen by name and the record's raw `version`/`source`
      (before projection); zero or several candidates are a refusal.

    The virtual root is not in the result; an `editable`/`directory` root is,
    as the build copy `root_dir` (`--no-editable`). One name reached twice
    (uv installs one distribution per name) and anything else is
    `UnsupportedLock`.

    `root_dir` must be the exact `--project` path of the build
    (`build_src_dir`), absolute and without `..`: uv 0.11.23 records it in
    `direct_url.json` normalised only lexically (a trailing `/` and `..` are
    folded) and never resolves links, so it is compared as given, not
    resolved (probe 22.10: a symlinked parent and `/tmp` stay as spelled).
    """
    if not root_dir.is_absolute() or ".." in root_dir.parts:
        raise ValueError(f"root_dir не абсолютный нормальный путь: {root_dir}")
    data = _load_lock(lock)
    _check_header(data)
    packages = _packages(data)
    root = _root(packages)
    by_name = _index(packages)
    _check_all(packages, by_name, root, env, root_dir)
    return _Walk(packages, by_name, root, env, root_dir).run(groups)


def distribution_problems(
    installed: Sequence[InstalledDist], expected: frozenset[ExpectedDist]
) -> list[str]:
    """Differences between installed and expected `(name, version, source)`.

    Both directions are reported (missing and extra), one sorted line each.
    `git` and `directory` sources compare by every field of `SourceId`; for
    `registry` the installed side declares only the kind, so the expected
    index URL is projected to `None` (`index: not declared`).

    Named limit (`DECLARED_LIMIT`): the distribution metadata agree with the
    lock as declared; this proves neither the absence of extra code nor the
    origin of the release files. Static checks of the tree (`.pth`,
    `sitecustomize`) are `pth_problems`/`static_problems`. Effects: none.
    """
    names = Counter(d.name for d in installed)
    problems = [f"{n}: установлено дважды" for n, c in names.items() if c > 1]
    have = {(d.name, d.version, d.source) for d in installed}
    want = {(e.name, e.version, _declared(e.source)) for e in expected}
    extra, missing = have - want, want - have
    by_key: dict[tuple[str, str], tuple[list[SourceId], list[SourceId]]] = {}
    for side, triples in enumerate((extra, missing)):
        for name, version, source in triples:
            by_key.setdefault((name, version), ([], []))[side].append(source)
    for (name, version), (got, need) in by_key.items():
        if len(got) == 1 and len(need) == 1:
            problems.append(
                f"{name} {version}: заявлено {_describe(got[0])}, "
                f"ожидается {_describe(need[0])}"
            )
            continue
        problems += [
            f"лишнее: {name} {version} {_describe(s)} (заявлено метаданными)"
            for s in got
        ]
        problems += [
            f"нет: {name} {version} {_describe(s)} (не заявлено метаданными)"
            for s in need
        ]
    return sorted(problems)


def uv_config_problems(
    pyproject: bytes, uv_toml: bytes | None, lock: bytes
) -> list[str]:
    """uv settings of a repo that `UV_NO_CONFIG=1` would drop (uv 0.11.23).

    Inputs are the trusted checkout's `pyproject.toml`, `uv.toml` (`None`:
    absent) and `uv.lock`. A `uv.toml` is a problem whatever it holds (it
    holds only settings); a `[tool.uv]` key outside `UV_PROJECT_KEYS_0_11_23`,
    a lock `[options]` key and a lock key outside `LOCK_TOP_KEYS`/
    `LOCK_MANIFEST_KEYS` are one problem each, naming file and key. An
    unreadable input is a problem, not an exception. `.python-version` is not
    significant: the build passes `--python` explicitly. Effects: none.
    """
    problems: list[str] = []
    if uv_toml is not None:
        problems.append("uv.toml: настройки uv в репо не поддерживаются")
    problems += _tool_uv_problems(pyproject)
    try:
        data = _toml(lock)
    except ValueError as exc:
        return [*problems, f"uv.lock: не читается как TOML UTF-8: {exc}"]
    return problems + lock_layout_problems(data)


def lock_layout_problems(data: Mapping[str, Any]) -> list[str]:
    """Lock keys outside `LOCK_TOP_KEYS`/`LOCK_MANIFEST_KEYS`, and every
    `[options]` key: one line each, naming the key."""
    problems: list[str] = []
    for key in sorted(data):
        value = data[key]
        if key == "options":
            problems += _table_problems(value, "uv.lock [options]", frozenset())
        elif key == "manifest":
            problems += _table_problems(value, "uv.lock [manifest]", LOCK_MANIFEST_KEYS)
        elif key not in LOCK_TOP_KEYS:
            problems.append(f"uv.lock {key}: ключ вне поддерживаемых")
    return problems


def _table_problems(value: object, where: str, allowed: frozenset[str]) -> list[str]:
    """One problem per key outside `allowed`; an empty table (`[options]`
    with no key) has none, in both checks alike."""
    if not isinstance(value, dict):
        return [f"{where}: не таблица"]
    return [
        f"{where} {k}: ключ вне поддерживаемых"
        for k in sorted(value)
        if k not in allowed
    ]


def _tool_uv_problems(pyproject: bytes) -> list[str]:
    try:
        tool = _toml(pyproject).get("tool", {})
    except ValueError as exc:
        return [f"pyproject.toml: не читается как TOML UTF-8: {exc}"]
    if not isinstance(tool, dict):
        return ["pyproject.toml [tool]: не таблица"]
    uv = tool.get("uv", {})
    if not isinstance(uv, dict):
        return ["pyproject.toml [tool.uv]: не таблица"]
    return [
        f"pyproject.toml [tool.uv] {key}: настройка uv вне списка поддерживаемых "
        f"для uv {UV_PINNED_VERSION}"
        for key in sorted(uv)
        if key not in UV_PROJECT_KEYS_0_11_23
    ]


def _toml(data: bytes) -> dict[str, Any]:
    """Parse UTF-8 TOML; a decode, syntax or nesting error is a `ValueError`."""
    try:
        return tomllib.loads(data.decode("utf-8"))
    except RecursionError as exc:
        raise ValueError("слишком глубокая вложенность") from exc


def _declared(source: SourceId) -> SourceId:
    """What the installed side can declare: a registry without its index."""
    return SourceId("registry") if source.kind == "registry" else source


def _describe(source: SourceId) -> str:
    if source.kind == "git":
        sub = f" subdirectory={source.subdirectory}" if source.subdirectory else ""
        return f"git {source.url}{sub} @{source.commit}"
    if source.kind == "directory":
        editable = "true" if source.editable else "false"
        return f"directory {source.path} editable={editable}"
    return "registry (index: not declared)"


# ---------- the lock ----------


@dataclass(frozen=True)
class _Package:
    name: str  # normalised
    version: str | None
    source: Mapping[str, Any]
    table: Mapping[str, Any]


def _refuse(why: str) -> UnsupportedLock:
    return UnsupportedLock(f"неподдерживаемый lock: {why}")


def _load_lock(lock: bytes) -> dict[str, Any]:
    try:
        return _toml(lock)
    except ValueError as exc:
        raise _refuse(f"не читается как TOML UTF-8: {exc}") from exc


def _is_int(value: object, want: int) -> bool:
    return type(value) is int and value == want


def _check_header(data: Mapping[str, Any]) -> None:
    if not (_is_int(data.get("version"), 1) and _is_int(data.get("revision"), 3)):
        raise _refuse(
            f"version {data.get('version')!r}, revision {data.get('revision')!r}; "
            "поддерживается version = 1, revision = 3"
        )
    problems = lock_layout_problems(data)
    if problems:
        raise _refuse("; ".join(problems))


def _packages(data: Mapping[str, Any]) -> list[_Package]:
    raw = data.get("package")
    if not isinstance(raw, list):
        raise _refuse("нет [[package]]")
    packages: list[_Package] = []
    for table in raw:
        if not isinstance(table, dict):
            raise _refuse("[[package]] не таблица")
        unknown = sorted(set(table) - _PACKAGE_KEYS)
        if unknown:
            raise _refuse(f"{table.get('name')!r}: ключи пакета {unknown}")
        name, version, source = (table.get(k) for k in ("name", "version", "source"))
        if not isinstance(name, str) or not isinstance(source, dict):
            raise _refuse(f"пакет без name/source: {name!r}")
        if version is not None and not isinstance(version, str):
            raise _refuse(f"{name}: version не строка")
        packages.append(_Package(normalize_name(name), version, source, table))
    return packages


def _is_root(package: _Package) -> bool:
    return (
        len(package.source) == 1 and next(iter(package.source.items())) in _ROOT_SOURCES
    )


def _root(packages: list[_Package]) -> int:
    roots = [i for i, p in enumerate(packages) if _is_root(p)]
    if len(roots) != 1:
        names = [packages[i].name for i in roots]
        raise _refuse(f"корней проекта {len(roots)}, нужен один: {names}")
    return roots[0]


def _entry_lists(package: _Package) -> list[tuple[str, object]]:
    """(label, list of records) of `dependencies` and of every extra/group."""
    lists: list[tuple[str, object]] = [
        ("dependencies", package.table.get("dependencies", []))
    ]
    for table in ("optional-dependencies", "dev-dependencies"):
        groups = package.table.get(table, {})
        if not isinstance(groups, dict):
            raise _refuse(f"{package.name}: {table} не таблица")
        lists += [(f"{table}.{g}", entries) for g, entries in groups.items()]
    return lists


def _check_entry(entry: object, where: str, env: Mapping[str, str]) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        raise _refuse(f"{where}: запись без name")
    unknown = sorted(set(entry) - _DEP_KEYS)
    if unknown:
        raise _refuse(f"{where}: ключи записи {unknown}")
    marker, extra = entry.get("marker"), entry.get("extra", [])
    if marker is not None:
        if not isinstance(marker, str):
            raise _refuse(f"{where}: marker не строка")
        evaluate(marker, env)
    if not isinstance(extra, list) or not all(isinstance(e, str) for e in extra):
        raise _refuse(f"{where}: extra не список строк")
    if not isinstance(entry.get("version", ""), str):
        raise _refuse(f"{where}: version не строка")
    if not isinstance(entry.get("source", {}), dict):
        raise _refuse(f"{where}: source не таблица")


def _check_all(
    packages: list[_Package],
    by_name: Mapping[str, list[int]],
    root: int,
    env: Mapping[str, str],
    root_dir: Path,
) -> None:
    """F5: every record and every source of the lock, reached or not.

    Each record must resolve to one target by the walk's own rule
    (`_select`), and each extra it asks for must exist there.
    """
    for i, package in enumerate(packages):
        if i != root:
            source_from_lock(package.source, root_dir=root_dir)
        for label, entries in _entry_lists(package):
            if not isinstance(entries, list):
                raise _refuse(f"{package.name}: {label} не список")
            for entry in entries:
                _check_entry(entry, f"{package.name} {label}", env)
                target = packages[_select(packages, by_name, entry)]
                optional = target.table.get("optional-dependencies", {})
                for extra in entry.get("extra", []):
                    if extra not in optional:
                        raise _refuse(f"{target.name}: нет extra {extra!r}")


def _index(packages: list[_Package]) -> dict[str, list[int]]:
    by_name: dict[str, list[int]] = {}
    for i, package in enumerate(packages):
        by_name.setdefault(package.name, []).append(i)
    return by_name


def _select(
    packages: list[_Package], by_name: Mapping[str, list[int]], entry: Mapping[str, Any]
) -> int:
    """N2: by name, then the record's raw `version`/`source`, if given."""
    found = [
        i
        for i in by_name.get(normalize_name(entry["name"]), [])
        if entry.get("version", packages[i].version) == packages[i].version
        and entry.get("source", packages[i].source) == packages[i].source
    ]
    if len(found) != 1:
        raise _refuse(f"запись {dict(entry)!r}: кандидатов {len(found)}")
    return found[0]


class _Walk:
    """The breadth-first walk of `expected_distributions` over checked data."""

    def __init__(
        self,
        packages: list[_Package],
        by_name: Mapping[str, list[int]],
        root: int,
        env: Mapping[str, str],
        root_dir: Path,
    ) -> None:
        self.packages, self.by_name, self.root = packages, by_name, root
        self.env, self.root_dir = env, root_dir

    def run(self, groups: tuple[str, ...]) -> frozenset[ExpectedDist]:
        root = self.packages[self.root]
        raw_groups = root.table.get("dev-dependencies", {})
        dev = {normalize_name(g): e for g, e in raw_groups.items()}
        if len(dev) != len(raw_groups):
            raise _refuse(f"{root.name}: группы совпадают после нормализации")
        start = list(root.table.get("dependencies", []))
        for group in groups:
            if normalize_name(group) not in dev:
                raise _refuse(f"{root.name}: нет группы {group!r}")
            start += dev[normalize_name(group)]
        visited = {(self.root, None)}
        queue: deque[tuple[int, str | None]] = deque(self._targets(start))
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            queue.extend(self._targets(self._entries(*node)))
        dists = [
            self._dist(i)
            for i, extra in visited
            if extra is None and not (i == self.root and "virtual" in root.source)
        ]
        twice = sorted(n for n, c in Counter(d.name for d in dists).items() if c > 1)
        if twice:
            raise _refuse(f"одно имя в составе дважды: {twice}")
        return frozenset(dists)

    def _entries(self, index: int, extra: str | None) -> list[Any]:
        """Records of a node; `_check_all` proved every asked extra exists."""
        table = self.packages[index].table
        if extra is None:
            return list(table.get("dependencies", []))
        return list(table["optional-dependencies"][extra])

    def _targets(self, entries: list[Any]) -> list[tuple[int, str | None]]:
        nodes: list[tuple[int, str | None]] = []
        for entry in entries:
            marker = entry.get("marker")
            if marker is not None and not evaluate(marker, self.env):
                continue
            target = _select(self.packages, self.by_name, entry)
            nodes.append((target, None))
            nodes += [(target, e) for e in entry.get("extra", [])]
        return nodes

    def _dist(self, index: int) -> ExpectedDist:
        package = self.packages[index]
        if package.version is None:
            raise _refuse(f"{package.name}: нет version")
        source = source_from_lock(package.source, root_dir=self.root_dir)
        return ExpectedDist(package.name, package.version, source)
