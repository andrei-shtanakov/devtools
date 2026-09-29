"""criteria_tokens — квалифицированный токен CODE:ID и его владелец.

Спека §1.3–1.4: токен принадлежит наиболее вложенному определению; область
определения — строки от первого декоратора до end_lineno минус вложенные
def/class (комментарии в AST не видны — считаем по строкам). Токен области
класса достаётся всем его тест-методам; токен метода в класс не поднимается.
"""
from __future__ import annotations

import ast
import re

ANY_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z]{2,6}):((?:BEH|AC)-\d+[a-z]?)(?![A-Za-z0-9_])"
)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def token_re(code: str, cid: str) -> re.Pattern[str]:
    """Регэксп одного квалифицированного токена с границами."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(code)}:{re.escape(cid)}(?![A-Za-z0-9_])")


def _span(node: ast.AST) -> tuple[int, int]:
    decos = getattr(node, "decorator_list", [])
    start = decos[0].lineno if decos else node.lineno
    return start, node.end_lineno or node.lineno


def _walk(body: list[ast.stmt], prefix: str, out: list[tuple[str, ast.AST]]) -> None:
    for node in body:
        if isinstance(node, _DEFS):
            qn = f"{prefix}{node.name}"
            out.append((qn, node))
            _walk(node.body, f"{qn}.", out)


def definition_tokens(source: str) -> dict[str, frozenset[str]]:
    """qualname тест-функции/метода → токены, которыми она владеет."""
    tree = ast.parse(source)
    defs: list[tuple[str, ast.AST]] = []
    _walk(tree.body, "", defs)
    lines = source.splitlines()
    owner: dict[int, str] = {}
    # наиболее вложенный владелец: более поздние (вложенные) перезаписывают строки
    for qn, node in defs:
        start, end = _span(node)
        for ln in range(start, end + 1):
            owner[ln] = qn
    owned: dict[str, set[str]] = {qn: set() for qn, _ in defs}
    for ln, text in enumerate(lines, start=1):
        qn = owner.get(ln)
        if qn is None:
            continue
        for m in ANY_TOKEN.finditer(text):
            owned[qn].add(f"{m.group(1)}:{m.group(2)}")
    kinds = {qn: node for qn, node in defs}
    result: dict[str, frozenset[str]] = {}
    for qn, node in defs:
        if isinstance(node, ast.ClassDef) or not node.name.startswith("test"):
            continue
        parent = qn.rpartition(".")[0]
        tokens = set(owned[qn])
        if parent and isinstance(kinds.get(parent), ast.ClassDef):
            tokens |= owned[parent]
        result[qn] = frozenset(tokens)
    return result
