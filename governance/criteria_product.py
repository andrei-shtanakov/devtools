"""criteria_product — байты продукта на product_sha (контракт v1 §3.3, §3.4, §6.1).

devtools не принимает на веру корни, окружение и дайджест ответа: читает
конфиг продукта тем же правилом, что производитель, из git-объектов
`product_sha`, и пересчитывает сам (devtools#491, spec-runner#623).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILES = ("spec-runner.config.yaml", "spec/executor.config.yaml")
_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ProductError(ValueError):
    """Конфиг продукта на product_sha неприемлем — ответ так не мог быть верным."""


@dataclass(frozen=True)
class Tree:
    repo: Path
    sha: str

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, check=False
        )

    def blob(self, path: str) -> bytes | None:
        proc = self._git("show", f"{self.sha}:{path}")
        return proc.stdout if proc.returncode == 0 else None

    def entries(self) -> dict[str, tuple[str, str]]:
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
            out[rel.decode("utf-8", errors="surrogateescape")] = (mode, obj)
        return out


@dataclass(frozen=True)
class Declaration:
    roots: tuple[str, ...]
    groups: tuple[str, ...] | None
    extras: tuple[str, ...]


def _norm_name(raw: object) -> str:
    if not isinstance(raw, str) or not _NAME.match(raw):
        raise ProductError(f"имя группы/extra {raw!r} не по PEP 735/685")
    return re.sub(r"[-_.]+", "-", raw).lower()


def _names(raw: object, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ProductError(f"criteria.environment.{what} — не список")
    names = [_norm_name(n) for n in raw]
    if len(set(names)) != len(names):
        raise ProductError(f"criteria.environment.{what}: дубль после нормализации")
    return tuple(sorted(names))


def _norm_root(raw: object) -> str:
    if not isinstance(raw, str) or not raw or raw.startswith("/"):
        raise ProductError(f"product_root {raw!r}: пусто или абсолютный путь")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ProductError(f"product_root {raw!r}: вне репо")
    return "/".join(parts)


def read_declaration(tree: Tree) -> Declaration:
    for rel in CONFIG_FILES:
        raw = tree.blob(rel)
        if raw is not None:
            break
    else:
        raise ProductError("нет конфига spec-runner на product_sha")
    data = yaml.safe_load(raw.decode("utf-8")) or {}
    data = data.get("executor", data) if isinstance(data, dict) else {}
    crit = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(crit, dict) or not isinstance(crit.get("product_roots"), list):
        raise ProductError("criteria.product_roots не объявлен")
    roots = [_norm_root(r) for r in crit["product_roots"]]
    if not roots:
        raise ProductError("criteria.product_roots пуст")
    if len(set(roots)) != len(roots):
        raise ProductError("criteria.product_roots: дубль после нормализации")
    env = crit.get("environment") or {}
    if not isinstance(env, dict):
        raise ProductError("criteria.environment — не мэппинг")
    groups = _names(env["groups"], "groups") if "groups" in env else None
    extras = _names(env.get("extras", []), "extras")
    return Declaration(tuple(sorted(roots)), groups, extras)


def resolve_roots(tree: Tree, roots: tuple[str, ...]) -> tuple[str, ...]:
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
    return tuple(sorted(out))


def _under(entries: dict[str, tuple[str, str]], path: str) -> set[str]:
    return {
        p
        for p in entries
        if (p == path or p.startswith(path.rstrip("/") + "/")) and p.endswith(".py")
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
    """Строки тел функций/методов продукта (G0 rev 8) по байтам product_sha."""
    out: dict[str, set[int]] = {}
    for path in files:
        data = tree.blob(path)
        if data is None:
            continue
        try:
            parsed = ast.parse(data)
        except SyntaxError:
            out[path] = set()
            continue
        lines: set[int] = set()
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                lines.update(range(node.body[0].lineno, (node.end_lineno or 0) + 1))
        out[path] = lines
    return out
