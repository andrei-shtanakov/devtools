"""The publisher's import closure is stdlib and `df` only (installer spec §6.1).

Walked over the AST, deferred and `TYPE_CHECKING` imports included, from the
roots `df.publisher.main` and `df.publisher.history`: the publisher runs on
the stdlib of the admin tree and never imports `governance`, `steward` or
`yaml`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("df.publisher.main", "df.publisher.history")


def _module_file(name: str) -> Path | None:
    base = ROOT.joinpath(*name.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path}: relative import"
            assert node.module is not None
            names.add(node.module)
            # `from df.publisher import merge` names a submodule.
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def _closure(roots: tuple[str, ...]) -> tuple[set[str], set[str]]:
    local: set[str] = set()
    external: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in local:
            continue
        top = name.split(".")[0]
        if top != "df":
            external.add(top)
            continue
        path = _module_file(name)
        if path is None:
            continue  # an attribute imported from a df module, not a module
        local.add(name)
        parts = name.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]
        todo += sorted(_imports(path))
    return local, external


def test_publisher_closure_stdlib() -> None:
    """Every module the two roots reach is stdlib or `df`."""
    local, external = _closure(ROOTS)
    assert set(ROOTS) <= local
    assert sorted(external - {"__future__"} - set(sys.stdlib_module_names)) == []
