"""Semantic duplicates: normalised AST hashes and CLI overlap (spec §3.3)."""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from selfcheck.graph.build import make_recipes
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import source_text
from selfcheck.roles import Role, role_of

MIN_LINES = 8
OVERLAP = 0.8
_MAKE_VAR = re.compile(r"\$\([^)]*\)")
FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class FuncHash:
    """Hashes of one function (spec §3.3)."""

    path: str
    qualname: str
    line: int
    exact: str
    structural: str
    literals: tuple[str, ...]


class _Normalizer(ast.NodeTransformer):
    """Rename locals/arguments; optionally erase str/int/float literals."""

    def __init__(self, *, erase_literals: bool) -> None:
        self.names: dict[str, str] = {}
        self.erase = erase_literals

    def _rename(self, name: str) -> str:
        return self.names.setdefault(name, f"v{len(self.names)}")

    def visit_arg(self, node: ast.arg) -> ast.arg:
        self.generic_visit(node)  # annotations stay (they are significant)
        node.arg = self._rename(node.arg)
        return node

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if isinstance(node.ctx, ast.Store) or node.id in self.names:
            node.id = self._rename(node.id)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        value = node.value
        if (
            self.erase
            and isinstance(value, str | int | float)
            and not isinstance(value, bool)
        ):
            node.value = "S" if isinstance(value, str) else 0
        return node


def _digest(fn: FuncNode, *, erase: bool) -> str:
    clone = copy.deepcopy(fn)
    clone.name = "_"  # the name is not part of the body's identity
    body = clone.body
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        clone.body = body[1:] or [ast.Pass()]
    normalized = _Normalizer(erase_literals=erase).visit(clone)
    return hashlib.sha1(ast.dump(normalized).encode()).hexdigest()


def _functions(tree: ast.AST) -> list[tuple[str, FuncNode]]:
    out: list[tuple[str, FuncNode]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if not isinstance(child, ast.ClassDef):
                    out.append((qual, child))
                visit(child, qual)

    visit(tree, "")
    return out


def function_hashes(source: str, path: str) -> list[FuncHash]:
    """Hashes of functions of at least MIN_LINES lines (SyntaxError propagates)."""
    result = []
    for qual, fn in _functions(ast.parse(source)):
        if (fn.end_lineno or fn.lineno) - fn.lineno + 1 < MIN_LINES:
            continue
        literals = tuple(
            sorted(
                {
                    repr(n.value)
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str | int | float)
                }
            )
        )
        result.append(
            FuncHash(
                path,
                qual,
                fn.lineno,
                _digest(fn, erase=False),
                _digest(fn, erase=True),
                literals,
            )
        )
    return result


def _dup(
    rule: str,
    kind: str,
    key: str,
    members: list[FuncHash],
    repo: str,
    confidence: Confidence,
    evidence: list[dict[str, str]],
) -> Finding:
    ordered = sorted(members, key=lambda m: (m.path, m.line))
    return Finding(
        rule=rule,
        category="duplicate",
        severity="medium",
        confidence=confidence,
        owner_repo=repo,
        anchor=f"dup:{kind}:{key[:16]}",
        locations=[Location(m.path, m.line) for m in ordered],
        related=[
            {"owner_repo": repo, "path": m.path, "line": m.line, "member": m.qualname}
            for m in ordered
        ],
        evidence=evidence,
        suggestion="вынести в общую функцию или модуль",
    )


def dup_findings(hashes: list[FuncHash], repo: str) -> list[Finding]:
    """exact groups → confirmed; structural-only groups → candidate."""
    by_exact: dict[str, list[FuncHash]] = defaultdict(list)
    by_struct: dict[str, list[FuncHash]] = defaultdict(list)
    for item in hashes:
        by_exact[item.exact].append(item)
        by_struct[item.structural].append(item)
    found = [
        _dup("ast-dup/exact", "exact", key, group, repo, Confidence.CONFIRMED, [])
        for key, group in by_exact.items()
        if len(group) >= 2
    ]
    for key, group in by_struct.items():
        if len(group) < 2 or len({g.exact for g in group}) == 1:
            continue
        evidence = [
            {"kind": "literals", "detail": f"{g.path}:{g.line}: {list(g.literals)}"}
            for g in group
        ]
        found.append(
            _dup(
                "ast-dup/structural",
                "structural",
                key,
                group,
                repo,
                Confidence.CANDIDATE,
                evidence,
            )
        )
    return found


def _source_py(target: RepoTarget) -> tuple[str, ...]:
    return tuple(
        p
        for p in target.corpus
        if p.endswith(".py") and role_of(p, target.roles) is Role.SOURCE
    )


def _ast_dup(ctx: ProbeCtx) -> ParseResult:
    prefix = ".selfcheck-canary/ast-dup/"
    result = ParseResult([], processed_paths=[])
    for files in (
        list(_source_py(ctx.target)),
        [p for p in ctx.inputs if p.startswith(prefix)],
    ):
        hashes: list[FuncHash] = []
        for rel in files:
            try:
                hashes += function_hashes(source_text(ctx, rel), rel)
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
                continue
            assert result.processed_paths is not None
            result.processed_paths.append(rel)
        result.findings += dup_findings(hashes, ctx.target.name)
    return result


_DUP_BODY = "".join(f"    r{i} = seed * {i} + {i}\n" for i in range(8))
_DUP_CANARY = (
    f"def selfcheck_left(seed):\n{_DUP_BODY}    return seed\n\n\n"
    f"def selfcheck_right(seed):\n{_DUP_BODY}    return seed\n"
)

AST_DUP = ProbeSpec(
    name="ast-dup",
    languages=frozenset({"python"}),
    input_mode="files",
    select=_source_py,
    canary=Canary(
        ".selfcheck-canary/ast-dup/canary.py",
        _DUP_CANARY,
        "ast-dup/exact",
        "dup:exact:*",
    ),
    coverage="reported",
    rules=("exact", "structural", f"min-lines:{MIN_LINES}"),
    logic_version=1,
    analyze=_ast_dup,
)


# ---- cli-overlap ---------------------------------------------------------------


def overlaps(a: frozenset[str], b: frozenset[str]) -> bool:
    """Jaccard ≥ 0.8 with at least two common flags (spec §3.3)."""
    common = a & b
    return len(common) >= 2 and len(common) / len(a | b) >= OVERLAP


def parser_flags(source: str, path: str) -> list[tuple[str, int, frozenset[str]]]:
    """Per function: long options added with ``add_argument``."""
    out = []
    for qual, fn in _functions(ast.parse(source)):
        flags = {
            c.args[0].value
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "add_argument"
            and c.args
            and isinstance(c.args[0], ast.Constant)
            and isinstance(c.args[0].value, str)
            and c.args[0].value.startswith("--")
        }
        if len(flags) >= 2:
            out.append((qual, fn.lineno, frozenset(flags)))
    return out


def _is_makefile(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] == "Makefile" or rel.endswith(".mk")


def _cli_overlap_files(ctx: ProbeCtx, files: list[str], result: ParseResult) -> None:
    parsers: list[tuple[str, str, int, frozenset[str]]] = []
    recipes: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for rel in files:
        text = source_text(ctx, rel)
        if rel.endswith(".py"):
            try:
                parsers += [(rel, q, n, f) for q, n, f in parser_flags(text, rel)]
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
        elif _is_makefile(rel):
            for target, lines in make_recipes(text).items():
                if lines:
                    norm = " ; ".join(
                        " ".join(_MAKE_VAR.sub("$V", c).split()) for _, c in lines
                    )
                    recipes[norm].append((rel, target, lines[0][0]))
    repo = ctx.target.name
    for i, (a_path, a_q, a_line, a) in enumerate(parsers):
        for b_path, b_q, b_line, b in parsers[i + 1 :]:
            if not overlaps(a, b):
                continue
            common = sorted(a & b)
            key = hashlib.sha1(" ".join(common).encode()).hexdigest()
            members = [(a_path, a_q, a_line), (b_path, b_q, b_line)]
            result.findings.append(
                Finding(
                    rule="cli-overlap/argparse",
                    category="duplicate",
                    severity="medium",
                    confidence=Confidence.CANDIDATE,
                    owner_repo=repo,
                    anchor=f"dup:cli:{key[:16]}",
                    locations=[Location(p, n) for p, _, n in members],
                    related=[
                        {"owner_repo": repo, "path": p, "line": n, "member": q}
                        for p, q, n in members
                    ],
                    evidence=[{"kind": "common-flags", "detail": " ".join(common)}],
                    suggestion="объединить CLI или вынести общий парсер",
                )
            )
    for norm, users in recipes.items():
        if len(users) < 2:
            continue
        key = hashlib.sha1(norm.encode()).hexdigest()
        result.findings.append(
            Finding(
                rule="cli-overlap/make-recipe",
                category="duplicate",
                severity="medium",
                confidence=Confidence.CANDIDATE,
                owner_repo=repo,
                anchor=f"dup:make:{key[:16]}",
                locations=[Location(p, n) for p, _, n in users],
                related=[
                    {"owner_repo": repo, "path": p, "line": n, "member": t}
                    for p, t, n in users
                ],
                evidence=[{"kind": "recipe", "detail": norm}],
                suggestion="оставить одну цель",
            )
        )


def _cli_select(target: RepoTarget) -> tuple[str, ...]:
    return tuple(
        p
        for p in target.corpus
        if role_of(p, target.roles) is Role.SOURCE
        and (p.endswith(".py") or _is_makefile(p))
    )


def _cli_overlap(ctx: ProbeCtx) -> ParseResult:
    result = ParseResult([])
    prefix = ".selfcheck-canary/cli-overlap/"
    _cli_overlap_files(ctx, list(_cli_select(ctx.target)), result)
    _cli_overlap_files(ctx, [p for p in ctx.inputs if p.startswith(prefix)], result)
    return result


_CLI_CANARY = "import argparse\n\n\n" + "".join(
    f"def selfcheck_{n}():\n    p = argparse.ArgumentParser()\n"
    "    p.add_argument('--alpha')\n    p.add_argument('--beta')\n"
    "    p.add_argument('--gamma')\n    return p\n\n\n"
    for n in ("left", "right")
)

CLI_OVERLAP = ProbeSpec(
    name="cli-overlap",
    languages=frozenset({"any"}),
    input_mode="files",
    select=_cli_select,
    canary=Canary(
        ".selfcheck-canary/cli-overlap/canary.py",
        _CLI_CANARY,
        "cli-overlap/argparse",
        "dup:cli:*",
    ),
    rules=("argparse-jaccard>=0.8", "make-recipe"),
    logic_version=1,
    analyze=_cli_overlap,
)
