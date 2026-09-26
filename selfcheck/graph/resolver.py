"""Computed-launch resolution and unresolved zones (spec §3.2.3)."""

from __future__ import annotations

import ast
import fnmatch
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from selfcheck.graph.commands import Index, module_files, module_name, scan_command
from selfcheck.graph.model import EdgeKind, Graph, NodeKind, Zone
from selfcheck.model import Location
from selfcheck.roles import Role

UNKNOWN = "<?>"
RUN_FUNCS = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "system",
        "execv",
        "execvp",
        "execl",
        "execlp",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "spawnv",
    }
)
_IMPORT_FUNCS = frozenset({"import_module", "__import__"})
_LAUNCH_MODULES = frozenset({"subprocess", "os", "asyncio"})
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
_ARRAY_ASSIGN = re.compile(r"^\s*(?:local\s+|declare\s+-a\s+)?[A-Za-z_]\w*\+?=\(")
_PASSTHROUGH = frozenset(
    {
        "Path",
        "PurePath",
        "PosixPath",
        "str",
        "abspath",
        "realpath",
        "normpath",
        "expanduser",
        "fspath",
    }
)
_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+)?([A-Za-z_]\w*)=(.*)$")
_VAR = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
Value = str | list[str] | None


@dataclass
class Scope:
    """Names visible to an expression: last simple assignments."""

    rel: str
    names: dict[str, ast.expr] = field(default_factory=dict)
    depth: int = 0


def evaluate(expr: ast.expr, scope: Scope) -> Value:
    """Evaluate a path/argv expression; unknown parts become ``<?>``."""
    if scope.depth > 12:
        return None
    scope.depth += 1
    try:
        return _eval(expr, scope)
    finally:
        scope.depth -= 1


def as_text(value: Value) -> str:
    """Flatten an evaluated value to a string."""
    if isinstance(value, list):
        return " ".join(value)
    return value if value is not None else UNKNOWN


def _eval(e: ast.expr, s: Scope) -> Value:
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    if isinstance(e, ast.Name):
        if e.id == "__file__":
            return s.rel
        bound = s.names.get(e.id)
        return evaluate(bound, s) if bound is not None else None
    if isinstance(e, ast.Attribute):
        if (
            isinstance(e.value, ast.Name)
            and e.value.id == "sys"
            and e.attr == "executable"
        ):
            return "python"
        base = evaluate(e.value, s)
        if e.attr == "parent" and isinstance(base, str):
            return posixpath.dirname(base)
        return None
    if isinstance(e, ast.List | ast.Tuple):
        return [as_text(evaluate(x, s)) for x in e.elts]
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div | ast.Add):
        left, right = evaluate(e.left, s), evaluate(e.right, s)
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        if isinstance(e.op, ast.Div):
            return posixpath.join(as_text(left), as_text(right))
        return as_text(left) + as_text(right)
    if isinstance(e, ast.JoinedStr):
        parts = []
        for v in e.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                parts.append(as_text(evaluate(v.value, s)))
        return "".join(parts)
    if isinstance(e, ast.Call):
        return _eval_call(e, s)
    return None


@dataclass(frozen=True)
class Launchers:
    """How a module can reach subprocess/os/asyncio launch functions."""

    names: frozenset[str] = frozenset()  # from subprocess import run [as r]
    modules: frozenset[str] = frozenset(_LAUNCH_MODULES)  # import subprocess as sp
    star: bool = False  # from subprocess import *


def launch_names(tree: ast.Module) -> Launchers:
    """Collect every way this module can name a launch function."""
    names: set[str] = set()
    modules: set[str] = set(_LAUNCH_MODULES)
    star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _LAUNCH_MODULES:
            for alias in node.names:
                if alias.name == "*":
                    star = True
                elif alias.name in RUN_FUNCS:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _LAUNCH_MODULES:
                    modules.add(alias.asname or alias.name)
    return Launchers(frozenset(names), frozenset(modules), star)


def is_launch(call: ast.Call, launchers: Launchers) -> bool:
    """A real process launch — not any function that happens to be called ``run``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        return (
            func.attr in RUN_FUNCS
            and isinstance(owner, ast.Name)
            and owner.id in launchers.modules
        )
    if not isinstance(func, ast.Name):
        return False
    return func.id in launchers.names or (launchers.star and func.id in RUN_FUNCS)


def argv_expr(call: ast.Call) -> ast.expr | None:
    """The argv argument of a launch: first positional or ``args=``."""
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "args"), None)


def call_name(call: ast.Call) -> str:
    """Last component of the called name."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _eval_call(call: ast.Call, s: Scope) -> Value:
    name = call_name(call)
    func = call.func
    args = [evaluate(a, s) for a in call.args]
    if name in _PASSTHROUGH and args:
        return args[0]
    if name == "resolve" and isinstance(func, ast.Attribute):
        return evaluate(func.value, s)
    if name == "with_name" and isinstance(func, ast.Attribute) and args:
        owner = as_text(evaluate(func.value, s))
        return posixpath.join(posixpath.dirname(owner), as_text(args[0]))
    if name == "dirname" and args:
        return posixpath.dirname(as_text(args[0]))
    if name == "join" and isinstance(func, ast.Attribute):
        owner = func.value
        if isinstance(owner, ast.Constant) and isinstance(owner.value, str):
            items: Value = None
            if call.args and isinstance(call.args[0], ast.GeneratorExp):
                items = evaluate(call.args[0].generators[0].iter, s)
            elif args:
                items = args[0]
            return owner.value.join(items) if isinstance(items, list) else None
        return posixpath.join(*[as_text(a) for a in args]) if args else None
    return None


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _assignments(body: list[ast.stmt]) -> dict[str, ast.expr]:
    """Simple assignments of this scope only (nested functions/classes excluded)."""
    names: dict[str, ast.expr] = {}
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop(0)
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            names[node.targets[0].id] = node.value
        stack += [c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPES)]
    return names


@dataclass(frozen=True)
class _Site:
    call: ast.Call
    scope: Scope
    params: tuple[str, ...]
    func: str
    local: frozenset[str] = frozenset()


class _Collector(ast.NodeVisitor):
    """Every call with its evaluation scope and enclosing function."""

    def __init__(self, rel: str, module_names: dict[str, ast.expr]) -> None:
        self.rel = rel
        self.module_names = module_names
        self.sites: list[_Site] = []
        self.scope = Scope(rel, dict(module_names))
        self.params: tuple[str, ...] = ()
        self.func = ""
        self.local: frozenset[str] = frozenset()

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        outer = (self.scope, self.params, self.func, self.local)
        local = _assignments(node.body)
        args = node.args
        vararg = [args.vararg] if args.vararg else []
        self.params = tuple(
            a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, *vararg]
        )
        visible = {k: v for k, v in self.module_names.items() if k not in self.params}
        self.scope = Scope(self.rel, {**visible, **local})
        self.func = node.name
        self.local = frozenset(local)
        self.generic_visit(node)
        self.scope, self.params, self.func, self.local = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.sites.append(_Site(node, self.scope, self.params, self.func, self.local))
        self.generic_visit(node)


def _sites(tree: ast.Module, rel: str) -> list[_Site]:
    collector = _Collector(rel, _assignments(tree.body))
    collector.visit(tree)
    return collector.sites


def _forwarded(site: _Site) -> str | None:
    first = argv_expr(site.call)
    if (
        isinstance(first, ast.Name)
        and first.id in site.params
        and first.id not in site.local
    ):
        return first.id
    return None


def _wrappers(sites: list[_Site], launchers: Launchers) -> dict[str, int]:
    """Functions passing a parameter straight into a launch → parameter index."""
    out: dict[str, int] = {}
    for site in sites:
        param = _forwarded(site) if is_launch(site.call, launchers) else None
        if param is not None and site.func:
            index = site.params.index(param)
            method = site.params and site.params[0] in ("self", "cls")
            out[site.func] = index - 1 if method else index
    return out


def argv_at(source: str, rel: str, line: int) -> list[str] | None:
    """Evaluated argv of the launch call starting at ``line`` (for llm-sites)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    launchers = launch_names(tree)
    for site in _sites(tree, rel):
        call = site.call
        expr = argv_expr(call)
        if call.lineno == line and is_launch(call, launchers) and expr is not None:
            value = evaluate(expr, site.scope)
            return value if isinstance(value, list) else [as_text(value)]
    return None


@dataclass
class _Module:
    rel: str
    name: str | None
    sites: list[_Site]
    launchers: Launchers
    wrappers: dict[str, int]
    tree: ast.Module


def _modules(
    files: Sequence[str], texts: dict[str, str], roles: dict[str, Role]
) -> list[_Module]:
    out = []
    for rel in files:
        if not rel.endswith(".py") or roles[rel] not in (Role.SOURCE, Role.CANARY):
            continue
        try:
            tree = ast.parse(texts[rel])
        except SyntaxError:
            continue
        sites = _sites(tree, rel)
        launchers = launch_names(tree)
        out.append(
            _Module(
                rel,
                module_name(rel),
                sites,
                launchers,
                _wrappers(sites, launchers),
                tree,
            )
        )
    return out


def _imported_wrappers(
    mod: _Module, by_name: dict[str, dict[str, int]]
) -> dict[str, int]:
    """Wrapper functions of other modules, as this module names them."""
    local: dict[str, int] = dict(mod.wrappers)
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.ImportFrom) and node.module in by_name:
            for alias in node.names:
                if alias.name in by_name[node.module]:
                    local[alias.asname or alias.name] = by_name[node.module][alias.name]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for func, idx in by_name.get(alias.name, {}).items():
                    local[f"{alias.asname or alias.name}.{func}"] = idx
    return local


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return func.id if isinstance(func, ast.Name) else ""


def add_exec_edges(
    g: Graph,
    files: Sequence[str],
    texts: dict[str, str],
    roles: dict[str, Role],
    index: Index,
) -> None:
    """Add exec edges from Python and shell sources; record zones."""
    modules = _modules(files, texts, roles)
    by_name = {m.name: m.wrappers for m in modules if m.name and m.wrappers}
    visible = {m.rel: _imported_wrappers(m, by_name) for m in modules}
    called: set[tuple[str, str]] = (
        set()
    )  # (defining module rel, wrapper) with a call site
    owner = {(m.name, f): m.rel for m in modules if m.name for f in m.wrappers}
    for mod in modules:
        for site in mod.sites:
            callee = _callee(site.call)
            if callee not in visible[mod.rel]:
                continue
            func = callee.rsplit(".", 1)[-1]
            defining = mod.rel if func in mod.wrappers and "." not in callee else None
            for (mname, fname), rel in owner.items():
                if fname == func and (defining is None or rel == defining):
                    called.add((rel, fname))
            if defining:
                called.add((defining, func))
    for mod in modules:
        _python(g, mod, visible[mod.rel], called, index)
    for rel in files:
        if roles[rel] not in (Role.SOURCE, Role.CANARY) or rel.endswith(".py"):
            continue
        text = texts[rel]
        if rel.endswith((".sh", ".bash")) or (
            text.startswith("#!") and "sh" in text.splitlines()[0]
        ):
            _shell(g, rel, text, index)


def _python(
    g: Graph,
    mod: _Module,
    wrappers: dict[str, int],
    called: set[tuple[str, str]],
    index: Index,
) -> None:
    for site in mod.sites:
        call, name = site.call, call_name(site.call)
        where = Location(mod.rel, call.lineno)
        expr = argv_expr(call)
        if name in _IMPORT_FUNCS and call.args:
            module = evaluate(call.args[0], site.scope)
            if isinstance(module, str) and UNKNOWN not in module:
                for path in module_files(module, index):
                    g.add(path, EdgeKind.EXEC, where)
            else:
                _zone(g, where, "", "dynamic import")
        elif name == "getattr" and len(call.args) >= 2:
            if not isinstance(call.args[1], ast.Constant):
                _zone(g, where, "", "dynamic getattr")
        elif is_launch(call, mod.launchers) and expr is not None:
            forwarded = site.func in mod.wrappers and _forwarded(site) is not None
            if forwarded and (mod.rel, site.func) in called:
                continue  # resolved at the wrapper's call sites
            _launch(g, where, evaluate(expr, site.scope), index)
        elif _callee(call) in wrappers and len(call.args) > wrappers[_callee(call)]:
            idx = wrappers[_callee(call)]
            _launch(g, where, evaluate(call.args[idx], site.scope), index)


def _quote(token: str) -> str:
    return token if " " not in token else f"'{token}'"


def _launch(g: Graph, where: Location, argv: Value, index: Index) -> None:
    command = argv if isinstance(argv, list) else [as_text(argv)]
    _apply(g, where, " ".join(_quote(t) for t in command), index, "")
    for token in command[1:]:
        if " " in token:
            _apply(g, where, token, index, "")


_VAR_REF = re.compile(r"\$\{?\w+\}?")
_VAR_NAME = re.compile(r"^[\"']?\$\{?(\w+)\}?[\"']?$")


def zone_pattern(token: str) -> str:
    """Name pattern a non-literal launch can reach: literal tail of the last
    path segment, ``$…`` parts as ``*``; ``""`` when nothing literal is left."""
    last = token.strip("'\"").rsplit("/", 1)[-1]
    pattern = _VAR_REF.sub("*", last.replace("$UNKNOWN", "*"))
    return pattern if pattern.strip("*") else ""


def _apply(
    g: Graph,
    where: Location,
    cmd: str,
    index: Index,
    base: str,
    suffixes: dict[str, str] | None = None,
) -> None:
    scan = scan_command(cmd.replace(UNKNOWN, "$UNKNOWN"), base, index, shell_vars=True)
    for path in scan.targets:
        g.add(path, EdgeKind.EXEC, where)
    for path in scan.mentions:
        g.mention(f"file:{path}", where.path)
    g.external += [(path, EdgeKind.EXEC, where) for path in scan.external]
    for token in scan.unresolved:
        pattern = zone_pattern(token)
        var = _VAR_NAME.match(token)
        if not pattern and var and suffixes:
            pattern = suffixes.get(var.group(1), "")
        _zone(g, where, pattern, "unresolved launch")


def _zone(g: Graph, where: Location, pattern: str, reason: str) -> None:
    files = [n for n in g.nodes.values() if n.kind is NodeKind.FILE]
    if pattern:
        members = {n.anchor for n in files if fnmatch.fnmatchcase(n.name, pattern)}
    else:
        folder = posixpath.dirname(where.path)
        members = {n.anchor for n in files if posixpath.dirname(n.path) == folder}
    if members:
        g.zones.append(Zone(where, frozenset(members), reason))


def _open_quote(text: str) -> bool:
    """True when ``text`` ends inside a single- or double-quoted string."""
    quote = ""
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and not quote:
            break
    return bool(quote)


_RUNS_SHELL = re.compile(r"(?:^|[;&|(\s])(?:sh|bash|zsh|dash|ksh)\b[^<]*<<")


def _shell_statements(text: str) -> list[tuple[int, str]]:
    """Logical shell lines: ``\\`` and open quotes joined; heredoc bodies are
    dropped unless the heredoc feeds a shell (then its lines are statements)."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    heredoc: str | None = None
    executes = False
    for number, line in enumerate(text.splitlines(), 1):
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            elif executes:
                out.append((number, line))
            continue
        if not buf:
            start = number
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        buf += line
        if _open_quote(buf):
            buf += "\n"
            continue
        match = _HEREDOC.search(buf)
        if match:
            heredoc = match.group(1)
            executes = bool(_RUNS_SHELL.search(buf[: match.start() + 2]))
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


_CASE_OPEN = re.compile(r"^case\s.*\bin\s*$")
_CASE_PATTERN = re.compile(r"^\(?[^()]*?\)\s*")


def _assignment_counts(statements: list[tuple[int, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, stmt in statements:
        match = _ASSIGN.match(stmt.strip())
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def _shell(g: Graph, rel: str, text: str, index: Index) -> None:
    base = posixpath.dirname(rel)
    known: dict[str, str] = {}
    suffixes: dict[str, str] = {}  # var → name pattern of its single literal value
    statements = _shell_statements(text)
    counts = _assignment_counts(statements)
    case_depth = 0
    for start, logical in statements:
        stripped = logical.strip()
        if not stripped or stripped.startswith("#") or _ARRAY_ASSIGN.match(stripped):
            continue
        if _CASE_OPEN.match(stripped):
            case_depth += 1
            continue
        if stripped.startswith("esac"):
            case_depth = max(case_depth - 1, 0)
            continue
        if case_depth:
            stripped = _CASE_PATTERN.sub("", stripped, count=1)
            if not stripped or stripped == ";;":
                continue
        match = _ASSIGN.match(stripped)
        if (
            match
            and "dirname" in match.group(2)
            and ("$0" in match.group(2) or "BASH_SOURCE" in match.group(2))
        ):
            known[match.group(1)] = "."
            continue
        if match and counts.get(match.group(1)) == 1:
            pattern = zone_pattern(match.group(2).strip())
            if pattern and "/" in match.group(2):
                suffixes[match.group(1)] = pattern
        expanded = _VAR.sub(lambda m: known.get(m.group(1), m.group(0)), stripped)
        _apply(g, Location(rel, start), expanded, index, base, suffixes)
