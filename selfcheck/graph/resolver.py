"""Computed-launch resolution and unresolved zones (spec §3.2.3)."""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from selfcheck.graph.commands import Index, module_files, scan_command
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


def launch_names(tree: ast.Module) -> frozenset[str]:
    """Bare names imported from subprocess/os/asyncio (``from subprocess import run``)."""
    names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in _LAUNCH_MODULES
        for alias in node.names
    }
    return frozenset(names & RUN_FUNCS | {n for n in names if n in RUN_FUNCS})


def is_launch(call: ast.Call, imported: frozenset[str]) -> bool:
    """A real process launch: ``subprocess.run(...)``, ``os.system(...)`` or an
    imported ``run(...)`` — not any function that happens to be called ``run``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        return (
            func.attr in RUN_FUNCS
            and isinstance(owner, ast.Name)
            and owner.id in _LAUNCH_MODULES
        )
    return isinstance(func, ast.Name) and func.id in imported


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
    first = site.call.args[0] if site.call.args else None
    if (
        isinstance(first, ast.Name)
        and first.id in site.params
        and first.id not in site.local
    ):
        return first.id
    return None


def _wrappers(sites: list[_Site], imported: frozenset[str]) -> dict[str, int]:
    """Same-module functions passing a parameter straight into a launch."""
    out: dict[str, int] = {}
    for site in sites:
        param = _forwarded(site) if is_launch(site.call, imported) else None
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
    imported = launch_names(tree)
    for site in _sites(tree, rel):
        call = site.call
        if call.lineno == line and is_launch(call, imported) and call.args:
            value = evaluate(call.args[0], site.scope)
            return value if isinstance(value, list) else [as_text(value)]
    return None


def add_exec_edges(
    g: Graph,
    files: Sequence[str],
    texts: dict[str, str],
    roles: dict[str, Role],
    index: Index,
) -> None:
    """Add exec edges from Python and shell sources; record zones."""
    for rel in files:
        if roles[rel] not in (Role.SOURCE, Role.CANARY):
            continue
        text = texts[rel]
        if rel.endswith(".py"):
            _python(g, rel, text, index)
        elif rel.endswith((".sh", ".bash")) or (
            text.startswith("#!") and "sh" in text.splitlines()[0]
        ):
            _shell(g, rel, text, index)


def _python(g: Graph, rel: str, text: str, index: Index) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    sites = _sites(tree, rel)
    imported = launch_names(tree)
    wrappers = _wrappers(sites, imported)
    for site in sites:
        call, name = site.call, call_name(site.call)
        where = Location(rel, call.lineno)
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
        elif is_launch(call, imported) and call.args:
            if site.func in wrappers and _forwarded(site) is not None:
                continue  # resolved at the wrapper's call sites
            _launch(g, where, evaluate(call.args[0], site.scope), index)
        elif name in wrappers and len(call.args) > wrappers[name]:
            _launch(g, where, evaluate(call.args[wrappers[name]], site.scope), index)


def _quote(token: str) -> str:
    return token if " " not in token else f"'{token}'"


def _launch(g: Graph, where: Location, argv: Value, index: Index) -> None:
    command = argv if isinstance(argv, list) else [as_text(argv)]
    _apply(g, where, " ".join(_quote(t) for t in command), index, "")
    for token in command[1:]:
        if " " in token:
            _apply(g, where, token, index, "")


_LITERAL_TAIL = re.compile(r"/([\w.-]+)[\"']?\s*$")
_VAR_NAME = re.compile(r"^[\"']?\$\{?(\w+)")


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
    for token in scan.unresolved:
        suffix = token.rsplit("/", 1)[-1] if "/" in token else ""
        if not suffix and suffixes:
            name = _VAR_NAME.match(token)
            suffix = suffixes.get(name.group(1), "") if name else ""
        _zone(
            g, where, "" if "$" in suffix else suffix.strip("'\""), "unresolved launch"
        )


def _zone(g: Graph, where: Location, suffix: str, reason: str) -> None:
    files = [n for n in g.nodes.values() if n.kind is NodeKind.FILE]
    if suffix:
        members = {n.anchor for n in files if n.name == suffix}
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


def _shell_statements(text: str) -> list[tuple[int, str]]:
    """Logical shell lines: ``\\`` and open quotes joined, heredoc bodies dropped."""
    out: list[tuple[int, str]] = []
    buf, start, heredoc = "", 0, None
    for number, line in enumerate(text.splitlines(), 1):
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
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
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def _shell(g: Graph, rel: str, text: str, index: Index) -> None:
    base = posixpath.dirname(rel)
    known: dict[str, str] = {}
    suffixes: dict[str, str] = {}  # var → literal file name its value ends with
    for start, logical in _shell_statements(text):
        stripped = logical.strip()
        if not stripped or stripped.startswith("#") or _ARRAY_ASSIGN.match(stripped):
            continue
        match = _ASSIGN.match(stripped)
        if (
            match
            and "dirname" in match.group(2)
            and ("$0" in match.group(2) or "BASH_SOURCE" in match.group(2))
        ):
            known[match.group(1)] = "."
            continue
        if match:
            tail = _LITERAL_TAIL.search(match.group(2))
            if tail and "$" not in tail.group(1):
                suffixes[match.group(1)] = tail.group(1)
        expanded = _VAR.sub(lambda m: known.get(m.group(1), m.group(0)), stripped)
        _apply(g, Location(rel, start), expanded, index, base, suffixes)
