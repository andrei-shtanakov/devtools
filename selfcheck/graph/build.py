"""Nodes and edges from structured sources (spec §3.2.1–3.2.2)."""

from __future__ import annotations

import ast
import plistlib
import posixpath
import re
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from selfcheck.graph import resolver
from selfcheck.graph.commands import (
    Index,
    Scan,
    build_index,
    module_files,
    scan_command,
)
from selfcheck.graph.model import EdgeKind, Graph, Node, NodeKind
from selfcheck.model import Location
from selfcheck.roles import Role

_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?![=:])(.*)$")
_FENCE = re.compile(r"^```\s*([\w-]*)\s*$")
_RUNBOOK_LANGS = frozenset({"sh", "bash", "console", "shell"})
_WORD = re.compile(r"[\w./-]+")
_MAKE_CONDITIONALS = frozenset({"ifeq", "ifneq", "ifdef", "ifndef", "else", "endif"})
_MAKE_CALL = re.compile(r"(?:\$\(MAKE\)|\bmake)\s+(?:-\S+\s+)*([A-Za-z0-9_.-]+)")


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(errors="replace")
    except OSError:
        return ""


def _is_script(text: str, rel: str) -> bool:
    return (
        text.startswith("#!")
        or rel.endswith(".sh")
        or '__name__ == "__main__"' in text
        or "__name__ == '__main__'" in text
    )


def _is_code(text: str, rel: str) -> bool:
    return rel.endswith((".py", ".sh", ".bash")) or text.startswith("#!")


def build_graph(
    files: Sequence[str],
    root: Path,
    role: Callable[[str], Role],
    *,
    repo_name: str,
    sched_dir: Path | None,
) -> Graph:
    """Build the usage graph of one repo (or of one canary set)."""
    texts = {rel: _read(root, rel) for rel in files}
    roles = {rel: role(rel) for rel in files}
    index = build_index(list(files), texts.get("pyproject.toml"))
    g = Graph()
    _nodes(g, files, texts, roles)
    _entry_points(g, texts.get("pyproject.toml"), index)
    for rel in files:
        text, kind = texts[rel], roles[rel]
        if kind is Role.DIAGNOSTIC_OUTPUT:
            continue
        name = posixpath.basename(rel)
        if kind is Role.SOURCE and (name == "Makefile" or name.endswith(".mk")):
            _makefile(g, rel, text, index)
        if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
            _workflow(g, rel, text, index, texts)
        if kind is Role.SOURCE and rel.endswith((".service", ".timer")):
            _unit(g, rel, text, index)
        if rel.endswith(".py") and kind in (Role.SOURCE, Role.TEST, Role.CANARY):
            _python(g, rel, text, index, test=kind is Role.TEST)
        if kind is Role.SKILL_ROOT:
            _markdown(g, rel, text, index, fenced=EdgeKind.SKILL, prose=EdgeKind.SKILL)
        if kind is Role.DOCUMENTATION and rel.endswith(".md"):
            _markdown(g, rel, text, index, fenced=EdgeKind.RUNBOOK, prose=EdgeKind.DOC)
    if sched_dir is not None:
        _launchd(g, sched_dir, repo_name, index)
    resolver.add_exec_edges(g, files, texts, roles, index)
    _mentions(g, files, texts, roles)
    return g


def _nodes(
    g: Graph, files: Sequence[str], texts: dict[str, str], roles: dict[str, Role]
) -> None:
    for rel in files:
        text, kind = texts[rel], roles[rel]
        name = posixpath.basename(rel)
        if kind in (Role.SOURCE, Role.CANARY) and _is_code(text, rel):
            g.nodes[f"file:{rel}"] = Node(
                f"file:{rel}",
                NodeKind.FILE,
                rel,
                name,
                executable=_is_script(text, rel),
            )
        if kind is Role.SKILL_ROOT:
            g.nodes[f"skill:{rel}"] = Node(
                f"skill:{rel}", NodeKind.SKILL, rel, rel, root=True
            )
        if kind is Role.SOURCE and rel.endswith((".service", ".timer")):
            g.nodes[f"unit:{rel}"] = Node(
                f"unit:{rel}", NodeKind.UNIT, rel, name, root=True
            )


def _entry_points(g: Graph, pyproject: str | None, index: Index) -> None:
    if not pyproject:
        return
    try:
        project = tomllib.loads(pyproject).get("project", {})
    except tomllib.TOMLDecodeError as exc:
        g.errors.append(f"pyproject.toml: {exc}")
        return
    where = Location("pyproject.toml", 1)
    for cli, ref in project.get("scripts", {}).items():
        anchor = f"cli:{cli}"
        g.nodes[anchor] = Node(anchor, NodeKind.CLI, "pyproject.toml", cli, root=True)
        module = ref.split(":", 1)[0]
        paths = module_files(module, index)
        if module not in index.modules:
            g.broken.append((anchor, where, module))
        for path in paths:
            g.add(path, EdgeKind.ENTRY, where)
    for group in project.get("entry-points", {}).values():
        for ref in group.values():
            for path in module_files(ref.split(":", 1)[0], index):
                g.add(path, EdgeKind.ENTRY, where)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = number
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((start, buf + line))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def make_recipes(text: str) -> dict[str, list[tuple[int, str]]]:
    """Makefile target → [(line, command)], incl. ``target: ; cmd`` and ``\\``."""
    recipes: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for number, line in _logical_lines(text):
        if line.startswith("\t") and current is not None:
            recipes[current].append((number, line.strip()))
            continue
        stripped = line.strip()
        if current is not None and (
            not stripped
            or stripped.startswith("#")
            or stripped.split(maxsplit=1)[0] in _MAKE_CONDITIONALS
        ):
            continue  # comments, blank lines and conditionals keep the recipe open
        match = _TARGET.match(line)
        if match and not line.startswith(".PHONY"):
            current = match.group(1)
            recipes.setdefault(current, [])
            rest = match.group(2)
            if ";" in rest:
                recipes[current].append((number, rest.split(";", 1)[1].strip()))
        elif line.strip():
            current = None
    return recipes


def _record(
    g: Graph,
    scan: Scan,
    kind: EdgeKind,
    where: Location,
    root_anchor: str | None,
) -> None:
    for path in scan.targets:
        g.add(path, kind, where)
    for path in scan.mentions:
        g.mention(f"file:{path}", where.path)
    if root_anchor is not None:
        g.broken += [(root_anchor, where, tok) for tok in scan.missing]


def _makefile(g: Graph, rel: str, text: str, index: Index) -> None:
    base = posixpath.dirname(rel)
    recipes = make_recipes(text)
    help_text = " ".join(cmd for _, cmd in recipes.get("help", []))
    for target in recipes:
        root = (
            target == "help"
            or re.search(rf"\bmake {re.escape(target)}\b", help_text) is not None
        )
        anchor = f"make:{rel}#{target}"
        g.nodes[anchor] = Node(anchor, NodeKind.MAKE, rel, target, root=root)
    for target, lines in recipes.items():
        anchor = f"make:{rel}#{target}"
        for number, cmd in lines:
            where = Location(rel, number)
            scan = scan_command(cmd, base, index, shell_vars=False)
            _record(
                g, scan, EdgeKind.MAKE, where, anchor if g.nodes[anchor].root else None
            )
            for called in _MAKE_CALL.findall(cmd):
                g.add_anchor(f"make:{rel}#{called}", EdgeKind.MAKE, where)


def _load_yaml(g: Graph, rel: str, text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        g.errors.append(f"{rel}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _run_steps(
    g: Graph,
    steps: list[Any],
    where: Location,
    index: Index,
    anchor: str | None,
    texts: dict[str, str],
    default_dir: str = "",
) -> None:
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        workdir = str(step.get("working-directory") or default_dir)
        base_known = "${{" not in workdir
        base = posixpath.normpath(workdir) if workdir and base_known else ""
        uses = str(step.get("uses", ""))
        if uses.startswith("./"):
            for name in ("action.yml", "action.yaml"):
                action = posixpath.normpath(posixpath.join(uses[2:], name))
                if action in texts:
                    data = _load_yaml(g, action, texts[action])
                    runs = data.get("runs") or {}
                    _run_steps(
                        g,
                        runs.get("steps") or [],
                        Location(action, 1),
                        index,
                        None,
                        texts,
                    )
        for line in str(step.get("run", "")).splitlines():
            scan = scan_command(
                line, base, index, shell_vars=False, base_known=base_known
            )
            _record(g, scan, EdgeKind.CI, where, anchor)


def _workflow(
    g: Graph, rel: str, text: str, index: Index, texts: dict[str, str]
) -> None:
    data = _load_yaml(g, rel, text)
    top_dir = _run_default_dir(data)
    for job, spec in (data.get("jobs") or {}).items():
        anchor = f"workflow:{rel}#{job}"
        g.nodes[anchor] = Node(anchor, NodeKind.WORKFLOW, rel, str(job), root=True)
        spec = spec if isinstance(spec, dict) else {}
        steps = spec.get("steps") or []
        job_dir = _run_default_dir(spec) or top_dir
        _run_steps(g, steps, Location(rel, 1), index, anchor, texts, job_dir)


def _run_default_dir(data: dict[str, Any]) -> str:
    defaults = data.get("defaults") or {}
    run = defaults.get("run") if isinstance(defaults, dict) else None
    return str(run.get("working-directory") or "") if isinstance(run, dict) else ""


def _unit(g: Graph, rel: str, text: str, index: Index) -> None:
    folder = posixpath.dirname(rel)
    unit_target = None
    for number, line in enumerate(text.splitlines(), 1):
        key, _, value = line.partition("=")
        where = Location(rel, number)
        if key.strip() in ("ExecStart", "ExecStartPre", "ExecStartPost"):
            for word in _WORD.findall(value):
                for path in index.files:
                    if word == path or word.endswith("/" + path):
                        g.add(path, EdgeKind.SCHED, where)
        if key.strip() == "Unit":
            unit_target = value.strip()
    if rel.endswith(".timer"):
        stem = posixpath.basename(rel).removesuffix(".timer")
        service = unit_target or f"{stem}.service"
        g.add_anchor(
            f"unit:{posixpath.join(folder, service)}", EdgeKind.SCHED, Location(rel, 1)
        )


def _launchd(g: Graph, sched_dir: Path, repo_name: str, index: Index) -> None:
    """Edges from machine-local launchd plists (spec §3.2.1, sched)."""
    marker = f"/{repo_name}/"
    for plist in sorted(sched_dir.glob("*.plist")):
        try:
            with plist.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            g.errors.append(f"{plist}: {exc}")
            continue
        g.plists.append(plist.name)
        args = [str(data.get("Program", ""))]
        args += [str(a) for a in data.get("ProgramArguments", [])]
        where = Location(f"launchd:{plist.name}", 1)
        cwd: str | None = None
        for chunk in re.split(r"&&|;", " ".join(args)):
            words = chunk.split()
            if "cd" in words and words.index("cd") + 1 < len(words):
                path = words[words.index("cd") + 1].rstrip("/")
                if marker in path + "/":
                    cwd = (path + "/").split(marker, 1)[1].rstrip("/")
                else:
                    cwd = None
                continue
            for word in _WORD.findall(chunk):
                if marker in word:
                    g.add(
                        posixpath.normpath(word.split(marker)[-1]),
                        EdgeKind.SCHED,
                        where,
                    )
                elif cwd is not None and word.startswith("./"):
                    g.add(
                        posixpath.normpath(posixpath.join(cwd, word)),
                        EdgeKind.SCHED,
                        where,
                    )


def _imports(tree: ast.AST, rel: str) -> list[str]:
    package = rel.rsplit("/", 1)[0].split("/") if "/" in rel else []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                parts = package[: max(keep, 0)]
                base = ".".join([*parts, node.module] if node.module else parts)
            else:
                base = node.module or ""
            if base:
                found.append(base)
            found += [f"{base}.{a.name}" if base else a.name for a in node.names]
    return found


def _python(g: Graph, rel: str, text: str, index: Index, *, test: bool) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        if not test:
            g.errors.append(f"{rel}: {exc.msg} (line {exc.lineno})")
        return
    kind = EdgeKind.TEST if test else EdgeKind.IMPORT
    folder = posixpath.dirname(rel)
    for module in _imports(tree, rel):
        path = index.modules.get(module)
        if path is not None:
            for loaded in module_files(module, index):
                g.add(loaded, kind, Location(rel, 1))
            continue
        # a script's own directory is on sys.path: `import helper` next to it
        sibling = posixpath.join(folder, *module.split("."))
        for cand in (f"{sibling}.py", f"{sibling}/__init__.py"):
            if cand in index.files:
                g.add(cand, kind, Location(rel, 1))
    if test:
        base = posixpath.dirname(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for cand in (node.value, posixpath.join(base, node.value)):
                    norm = posixpath.normpath(cand) if cand else ""
                    if norm in index.files:
                        g.add(norm, EdgeKind.TEST, Location(rel, node.lineno))


def _markdown(
    g: Graph, rel: str, text: str, index: Index, *, fenced: EdgeKind, prose: EdgeKind
) -> None:
    base = posixpath.dirname(rel)
    root_anchor = f"skill:{rel}" if fenced is EdgeKind.SKILL else None
    lang: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        fence = _FENCE.match(line.strip())
        if fence:
            lang = None if lang is not None else fence.group(1).lower()
            continue
        where = Location(rel, number)
        runnable = lang is not None and (
            fenced is EdgeKind.SKILL or lang in _RUNBOOK_LANGS
        )
        if runnable:
            command = line.strip().removeprefix("$ ")
            _record(
                g,
                scan_command(command, "", index, shell_vars=False),
                fenced,
                where,
                root_anchor,
            )
            if base:
                scan = scan_command(command, base, index, shell_vars=False)
                for path in scan.targets:
                    g.add(path, fenced, where)
            continue
        if lang is not None:
            continue
        if fenced is EdgeKind.SKILL:
            for snippet in re.findall(r"`([^`]+)`", line):
                scan = scan_command(snippet, "", index, shell_vars=False)
                for path in scan.targets:
                    g.add(path, fenced, where)
        for word in re.findall(r"\]\(([^)]+)\)", line) + _WORD.findall(line):
            for cand in (word, posixpath.join(base, word)):
                norm = posixpath.normpath(cand.removeprefix("./")) if cand else ""
                if norm in index.files:
                    g.add(norm, prose, where)


def _mentions(
    g: Graph, files: Sequence[str], texts: dict[str, str], roles: dict[str, Role]
) -> None:
    sources = [f for f in files if roles[f] in (Role.SOURCE, Role.SKILL_ROOT)]
    g.root_texts = {f: texts[f] for f in sources}
    for anchor, node in g.nodes.items():
        if node.kind is not NodeKind.FILE:
            continue
        pattern = re.compile(rf"(?<![\w.-]){re.escape(node.name)}(?![\w-])")
        for f in sources:
            if f != node.path and node.name in texts[f] and pattern.search(texts[f]):
                g.mention(anchor, f)
