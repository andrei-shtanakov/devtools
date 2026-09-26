"""Anchors for line-level findings (spec §2.1)."""

from __future__ import annotations

import ast


def python_anchor(source: str, path: str, line: int) -> str:
    """Innermost function containing ``line`` → ``func:``, else ``file:``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"file:{path}"
    best: str | None = None

    def visit(node: ast.AST, prefix: str) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                end = child.end_lineno or child.lineno
                is_func = not isinstance(child, ast.ClassDef)
                if is_func and child.lineno <= line <= end:
                    best = qual
                visit(child, qual)
            else:
                visit(child, prefix)

    visit(tree, "")
    return f"func:{path}::{best}" if best else f"file:{path}"
