"""criteria_product — байты продукта на product_sha (контракт v1 §3.3, §3.4, §6.1).

devtools не принимает на веру корни, окружение и дайджест ответа: читает
конфиг продукта тем же правилом, что производитель, из git-объектов
`product_sha`, и пересчитывает сам (devtools#491, spec-runner#623). Refusal
rules are checked for parity with the producer's own reader
(`spec-runner/src/spec_runner/criteria_config.py`): a divergence there would
let devtools accept a declaration the producer itself would have refused, or
the reverse (review round 1, 2026-09-30).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILES = ("spec-runner.config.yaml", "spec/executor.config.yaml")
_ENV_KEYS = frozenset({"groups", "extras"})
_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ProductError(ValueError):
    """Конфиг продукта на product_sha неприемлем — ответ так не мог быть верным."""


@dataclass(frozen=True)
class Tree:
    """Отслеживаемое дерево git на `sha`: байты только из git-объектов, не
    из рабочего дерева (§3.1). `_cache` держит один `ls-tree` на инстанс."""

    repo: Path
    sha: str
    _cache: dict[str, dict[str, tuple[str, str]]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        # Every inherited GIT_* is dropped: a hook's GIT_DIR/GIT_INDEX_FILE must
        # not redirect reads elsewhere. --literal-pathspecs: every path argument
        # is a literal path, never pathspec magic (":(top)", ":!x").
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["git", "-C", str(self.repo), "--literal-pathspecs", *args],
            capture_output=True,
            check=False,
            env=env,
        )

    def blob(self, path: str) -> bytes | None:
        """Содержимое `path` на `sha`; `None` — `git show` не нашёл путь
        (отсутствие пути не отличимо от сбоя git-объекта — решает вызывающий
        код, ревью m-2)."""
        proc = self._git("show", f"{self.sha}:{path}")
        return proc.stdout if proc.returncode == 0 else None

    def entries(self) -> dict[str, tuple[str, str]]:
        """Путь → (mode, blob-sha) для каждой записи `ls-tree -r` на `sha`,
        закэшировано на инстанс."""
        if "entries" in self._cache:
            return self._cache["entries"]
        proc = self._git("ls-tree", "-r", "-z", self.sha)
        if proc.returncode != 0:
            raise ProductError(
                f"ls-tree {self.sha[:12]}: {proc.stderr.decode().strip()}"
            )
        out: dict[str, tuple[str, str]] = {}
        for raw in proc.stdout.split(b"\0"):
            if not raw:
                continue
            meta, _, rel = raw.partition(b"\t")
            mode, _kind, obj = meta.decode().split()
            try:
                path = rel.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProductError(
                    f"ls-tree {self.sha[:12]}: путь не UTF-8: {exc}"
                ) from None
            out[path] = (mode, obj)
        self._cache["entries"] = out
        return out


@dataclass(frozen=True)
class Declaration:
    """Декларация `criteria.*` конфига продукта: корни и выбор окружения,
    нормализованные тем же правилом, что producer `criteria_config` (§3.3)."""

    roots: tuple[str, ...]
    groups: tuple[str, ...] | None
    extras: tuple[str, ...]


def _normalise(name: str) -> str:
    """PEP 503-style: lower-case, each run of `-`, `_`, `.` becomes one `-`."""
    return re.sub(r"[-_.]+", "-", name.lower())


def _norm_name(raw: object) -> str:
    if not isinstance(raw, str) or not _NAME.fullmatch(raw):
        raise ProductError(f"имя группы/extra {raw!r} не по PEP 735/685")
    return _normalise(raw)


def _names(raw: object, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ProductError(f"criteria.environment.{what} — не список")
    names = [_norm_name(n) for n in raw]
    if len(set(names)) != len(names):
        raise ProductError(f"criteria.environment.{what}: дубль после нормализации")
    return tuple(sorted(names))


def _norm_root(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProductError(f"product_root {raw!r}: не путь")
    stripped = raw.strip()
    if stripped.startswith("/"):
        raise ProductError(f"product_root {raw!r}: абсолютный путь")
    if stripped.startswith(":"):
        raise ProductError(f"product_root {raw!r}: git pathspec magic, не путь")
    # POSIX separators only — a backslash is part of the name, not rewritten,
    # so it fails later as "not tracked at product_sha" like the producer.
    parts = [p for p in stripped.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ProductError(f"product_root {raw!r}: вне репо")
    return "/".join(parts)


def _read_config_text(tree: Tree) -> str:
    for rel in CONFIG_FILES:
        raw = tree.blob(rel)
        if raw is None:
            continue
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductError(f"{rel}: не UTF-8: {exc}") from None
    raise ProductError("нет конфига spec-runner на product_sha")


def _parse_environment(raw: object) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    if raw is None:
        return None, ()
    if not isinstance(raw, dict) or not set(raw) <= _ENV_KEYS:
        raise ProductError("criteria.environment — не мэппинг с ключами groups/extras")
    groups = _names(raw["groups"], "groups") if "groups" in raw else None
    extras = _names(raw["extras"], "extras") if "extras" in raw else ()
    return groups, extras


def _table(parent: dict[str, object], key: str) -> dict[str, object]:
    """`parent[key]` as a table; absent is empty, a non-table is refused."""
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ProductError(f"pyproject.toml: `{key}` — не таблица")
    return value


def _check_selection(
    tree: Tree, groups: tuple[str, ...] | None, extras: tuple[str, ...]
) -> None:
    """§3.3 pre-check: every declared group/extra must be a key in pyproject.toml."""
    if groups is None and not extras:
        return
    raw = tree.blob("pyproject.toml")
    if raw is None:
        raise ProductError(
            "criteria.environment объявлено, но pyproject.toml нет на product_sha"
        )
    try:
        pyproject = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ProductError(f"pyproject.toml нечитаем: {exc}") from None
    groups_table = _table(pyproject, "dependency-groups")
    available_groups = {_normalise(k) for k in groups_table}
    if "dev-dependencies" in _table(_table(pyproject, "tool"), "uv"):
        available_groups.add("dev")
    extras_table = _table(_table(pyproject, "project"), "optional-dependencies")
    available_extras = {_normalise(k) for k in extras_table}
    missing = [f"group {g}" for g in groups or () if g not in available_groups]
    missing += [f"extra {e}" for e in extras if e not in available_extras]
    if missing:
        raise ProductError(f"не объявлено в pyproject.toml: {', '.join(missing)}")


def read_declaration(tree: Tree) -> Declaration:
    """Читает и проверяет `criteria.*` конфиг продукта на `tree.sha` тем же
    правилом, что `spec_runner.criteria_config` (§3.3); отказ — `ProductError`."""
    text = _read_config_text(tree)
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProductError(f"некорректный YAML: {exc}") from None
    data = data.get("executor", data) if isinstance(data, dict) else {}
    crit = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(crit, dict) or not isinstance(crit.get("product_roots"), list):
        raise ProductError("criteria.product_roots не объявлен")
    roots = [_norm_root(r) for r in crit["product_roots"]]
    if not roots:
        raise ProductError("criteria.product_roots пуст")
    if len(set(roots)) != len(roots):
        raise ProductError("criteria.product_roots: дубль после нормализации")
    groups, extras = _parse_environment(crit.get("environment"))
    _check_selection(tree, groups, extras)
    return Declaration(tuple(sorted(roots)), groups, extras)


def resolve_roots(tree: Tree, roots: tuple[str, ...]) -> tuple[str, ...]:
    """Развёртывает `roots` в отсортированный кортеж обычных `.py` файлов на
    `tree.sha`; симлинк-корень или корень без ни одного `.py` — `ProductError`
    (§3.4)."""
    entries = tree.entries()
    out: set[str] = set()
    for root in roots:
        hits = [p for p in entries if p == root or p.startswith(root + "/")]
        if not hits:
            raise ProductError(f"product_root {root!r} нет на product_sha")
        for p in hits:
            mode = entries[p][0]
            if mode == "120000":
                raise ProductError(f"product_root {root!r}: симлинк {p}")
            if mode.startswith("100") and p.endswith(".py"):
                out.add(p)
    if not out:
        raise ProductError(f"{', '.join(roots)}: ни одного .py файла")
    return tuple(sorted(out))


def _under(entries: dict[str, tuple[str, str]], path: str) -> set[str]:
    """Отслеживаемые `.py` под `path` (включительно), без фильтра по mode —
    паритет с producer `tracked_files`/`digest_paths` (spec-runner#603),
    у которого фильтра по типу записи нет: симлинк `.py` под skipped/ignored
    тоже входит в `files` §6.1 (review m-3)."""
    prefix = path.rstrip("/") + "/"
    return {
        p for p in entries if (p == path or p.startswith(prefix)) and p.endswith(".py")
    }


def content_sha256(
    tree: Tree,
    decl: Declaration,
    lock_sha256: str,
    test_files: list[str],
    excluded_paths: list[str],
) -> str:
    """§6.1: файлы = корни ∪ test_files ∪ pyproject.toml ∪ .py под skipped/ignored."""
    entries = tree.entries()
    files = set(resolve_roots(tree, decl.roots)) | set(test_files) | {"pyproject.toml"}
    for path in excluded_paths:
        files |= _under(entries, path)
    rows = []
    for path in sorted(files, key=lambda p: p.encode()):
        data = tree.blob(path)
        if data is None:
            raise ProductError(f"{path} нет на product_sha")
        rows.append([path, hashlib.sha256(data).hexdigest()])
    obj = {
        "v": 1,
        "product_roots": list(decl.roots),
        "lock": lock_sha256,
        "environment": {
            "groups": list(decl.groups) if decl.groups is not None else None,
            "extras": list(decl.extras),
        },
        "files": rows,
    }
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def tracked_py(tree: Tree) -> tuple[str, ...]:
    """Все отслеживаемые обычные .py — владельцев токена ищем по ним, а не по
    test_files ответа: исключённый или невидимый сбору файл тоже владелец."""
    return tuple(
        sorted(
            p
            for p, (mode, _) in tree.entries().items()
            if mode.startswith("100") and p.endswith(".py")
        )
    )


def function_body_lines(tree: Tree, files: list[str]) -> dict[str, set[int]]:
    """Строки тел функций/методов продукта (G0 rev 8) по байтам product_sha.

    Синтаксис, который текущий Python devtools не разбирает (3.13+/3.14 и
    т.п.), — именованный `ProductError` (ревью m-4), а не молчаливое пустое
    множество: producer парсит Python-ом продукта (R-B16) и может сказать
    `traced` там, где devtools тихо получил бы размытое «статус ≠
    пересчёту»."""
    out: dict[str, set[int]] = {}
    for path in files:
        data = tree.blob(path)
        if data is None:
            continue
        try:
            parsed = ast.parse(data)
        except SyntaxError as exc:
            raise ProductError(
                f"{path}: не разбирается текущим Python: {exc}"
            ) from None
        lines: set[int] = set()
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                lines.update(range(node.body[0].lineno, (node.end_lineno or 0) + 1))
        out[path] = lines
    return out
