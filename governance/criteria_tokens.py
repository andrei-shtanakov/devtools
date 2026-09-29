"""criteria_tokens — квалифицированный токен CODE:ID и его владелец.

Спека §1.3–1.4, паритет с парсером spec-runner (devtools#491, D.1–D.7):

- индекс — определения модуля и классов; составные операторы (`if`, `try`,
  `with`, циклы, `match`) просматриваются насквозь — pytest собирает то, что
  они определяют, а их собственные строки на уровне модуля ничем не владеют;
- повторное имя: выживает последнее в порядке исходника вместе с потомками,
  прежнее и его методы из индекса уходят (как в Python);
- `def`, вложенный в функцию, — не определение: его строки вычитаются из
  области объемлющей функции;
- область определения — строки от первого декоратора до `end_lineno` минус
  вложенные определения (комментарии в AST не видны — считаем по строкам);
  метод получает ещё и области ВСЕХ объемлющих классов;
- строки режутся только по `\\r\\n|\\r|\\n` — как нумерует `ast`
  (`splitlines()` режет и по `\\x0c`, `\\x1c`, …); ведущий BOM срезается.

«Тестовость» решает сбор pytest (B.6 #491): `owned_definitions` отдаёт все
функции и методы — форма общих фикстур `criteria-closure/v1`.
`definition_tokens` — переходное статическое приближение сбора по умолчанию
(`test*` функции модуля и методы `Test*`-классов), пока ответ не несёт
`test_items`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

ANY_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z]{2,6}):((?:BEH|AC)-\d+[a-z]?)(?![A-Za-z0-9_])"
)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_BODIES = ("body", "handlers", "cases", "orelse", "finalbody")

_Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class OwnedDefinition:
    """Функция или метод и квалифицированные токены, которыми она владеет."""

    qualname: str
    line: int
    tokens: tuple[str, ...]


def token_re(code: str, cid: str) -> re.Pattern[str]:
    """Регэксп одного квалифицированного токена с границами."""
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(code)}:{re.escape(cid)}(?![A-Za-z0-9_])"
    )


def owned_definitions(source: str) -> list[OwnedDefinition]:
    """Все функции и методы индекса с их токенами, по `(line, qualname)`.

    `line` — строка первого декоратора, иначе `def` (то, что отдаёт
    `co_firstlineno` собранной функции). Неразбираемый исходник (синтаксис,
    NUL) — `SyntaxError`/`ValueError`.
    """
    text = source.removeprefix("﻿")
    lines = _LINE_BREAK.split(text)
    out = [
        OwnedDefinition(qn, _span(node)[0], _tokens(lines, node, enclosing))
        for qn, (node, enclosing) in _index(ast.parse(text)).items()
        if isinstance(node, _FUNCS)
    ]
    return sorted(out, key=lambda d: (d.line, d.qualname))


def definition_tokens(source: str) -> dict[str, frozenset[str]]:
    """qualname теста → токены; тест — по статическому приближению сбора."""
    text = source.removeprefix("﻿")
    lines = _LINE_BREAK.split(text)
    return {
        qn: frozenset(_tokens(lines, node, enclosing))
        for qn, (node, enclosing) in _index(ast.parse(text)).items()
        if isinstance(node, _FUNCS)
        and node.name.startswith("test")
        and all(c.name.startswith("Test") for c in enclosing)
    }


def _index(tree: ast.Module) -> dict[str, tuple[_Def, tuple[ast.ClassDef, ...]]]:
    """qualname → (определение, объемлющие классы снаружи внутрь)."""
    found: dict[str, tuple[_Def, tuple[ast.ClassDef, ...]]] = {}

    def visit(
        body: list[ast.stmt], prefix: str, enclosing: tuple[ast.ClassDef, ...]
    ) -> None:
        for node in body:
            if not isinstance(node, _DEFS):
                for inner in _statement_bodies(node):
                    visit(inner, prefix, enclosing)
                continue
            qn = f"{prefix}{node.name}"
            for stale in [k for k in found if k == qn or k.startswith(f"{qn}.")]:
                del found[stale]
            found[qn] = (node, enclosing)
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{qn}.", (*enclosing, node))

    visit(tree.body, "", ())
    return found


def _statement_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    """Списки операторов внутри составного оператора, в порядке исходника."""
    out: list[list[ast.stmt]] = []
    for name in _BODIES:
        value = getattr(node, name, None)
        if not isinstance(value, list):
            continue
        if name in ("handlers", "cases"):
            out.extend(item.body for item in value)
        else:
            out.append(value)
    return out


def _tokens(
    lines: list[str], node: _Def, enclosing: tuple[ast.ClassDef, ...]
) -> tuple[str, ...]:
    carrier = set(_region(node)).union(*(_region(c) for c in enclosing))
    return tuple(
        sorted(
            {
                m.group(0)
                for ln in carrier
                if 0 < ln <= len(lines)
                for m in ANY_TOKEN.finditer(lines[ln - 1])
            }
        )
    )


def _span(node: _Def) -> tuple[int, int]:
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    return start, node.end_lineno or node.lineno


def _region(node: _Def) -> set[int]:
    """Собственные строки: от первого декоратора до конца минус вложенные."""
    start, end = _span(node)
    lines = set(range(start, end + 1))
    for inner in ast.walk(node):
        if inner is not node and isinstance(inner, _DEFS):
            s, e = _span(inner)
            lines.difference_update(range(s, e + 1))
    return lines
