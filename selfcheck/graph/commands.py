"""Command lines → launch targets and mentioned files (spec §3.2.1)."""

from __future__ import annotations

import posixpath
import re
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field

_SEPARATORS = frozenset(
    {"&&", "||", ";", "|", "&", "then", "do", "else", "!", "(", "{"}
)
_WRAPPERS = frozenset(
    {
        "sudo",
        "exec",
        "nohup",
        "env",
        "time",
        "command",
        "sh",
        "bash",
        "zsh",
        "source",
        ".",
        "python",
        "python3",
        "uv",
        "run",
        "xargs",
    }
)
_OPTS_WITH_ARG = frozenset(
    {"--project", "--group", "--with", "--python", "--directory", "-u", "-C"}
)
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
# a launch through a variable: "$x", "${x}", "$x/sub/tool.sh" — not "${C}text"
_VAR_LAUNCH = re.compile(r"^\$\{?\w+\}?(?:/[\w./-]*)?$")


@dataclass(frozen=True)
class Index:
    """What a command token can resolve to."""

    files: frozenset[str]
    modules: dict[str, str]
    clis: dict[str, str]


def module_name(path: str) -> str | None:
    """``a/b/c.py`` → ``a.b.c``; ``src/`` stripped; ``__init__`` → package."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def build_index(files: Sequence[str], pyproject: str | None) -> Index:
    """Index corpus files, importable modules and console scripts."""
    modules: dict[str, str] = {}
    for path in files:
        name = module_name(path)
        if name is not None:
            modules[name] = path
    clis: dict[str, str] = {}
    if pyproject:
        try:
            scripts = tomllib.loads(pyproject).get("project", {}).get("scripts", {})
        except tomllib.TOMLDecodeError:
            scripts = {}
        for cli, ref in scripts.items():
            mod = ref.split(":", 1)[0]
            if mod in modules:
                clis[cli] = modules[mod]
    return Index(frozenset(files), modules, clis)


def module_files(module: str, index: Index) -> list[str]:
    """Files loaded for ``module``: every package prefix, plus ``__main__``."""
    parts = module.split(".")
    out = []
    for i in range(1, len(parts) + 1):
        path = index.modules.get(".".join(parts[:i]))
        if path is not None:
            out.append(path)
    main = index.modules.get(f"{module}.__main__")
    if main is not None:
        out.append(main)
    return out


@dataclass
class Scan:
    """Result of reading one command line."""

    targets: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _tokens(cmd: str, shell_vars: bool) -> list[str]:
    if shell_vars:  # command substitution starts a new command
        cmd = cmd.replace("$(", " ; ").replace("`", " ; ")
    try:
        return shlex.split(cmd, comments=True)
    except ValueError:
        return cmd.split()


def _resolve(token: str, base: str, index: Index) -> str | None:
    clean = token.strip("'\"")
    for cand in (posixpath.join(base, clean), clean) if base else (clean,):
        norm = posixpath.normpath(cand) if cand else ""
        if norm in index.files:
            return norm
    return None


def _looks_missing(token: str) -> bool:
    clean = token.strip("'\"")
    return (
        clean.endswith((".py", ".sh"))
        and not clean.startswith(("/", ".."))
        and "*" not in clean
    )


def scan_command(cmd: str, base: str, index: Index, *, shell_vars: bool) -> Scan:
    """Launch targets (command position) vs mentioned files (arguments).

    ``shell_vars``: shell ``$x`` in command position is unresolved (a zone);
    otherwise ``$(VAR)``/``${{ … }}`` tokens are skipped as configuration.
    """
    scan = Scan()
    position, first, skip_next, want_module = True, True, False, False
    for raw in _tokens(cmd, shell_vars):
        token = raw.lstrip("@+-") if first and raw[:1] in "@+-" else raw
        first = False
        if skip_next:
            skip_next = False
            continue
        if token in _SEPARATORS or token.endswith(";"):
            position, first = True, True
            continue
        if want_module:
            scan.targets += module_files(token, index)
            want_module, position = False, False
            continue
        if position:
            if _ASSIGN.match(token) or token in _WRAPPERS:
                continue
            if token == "-m":
                want_module = True
                continue
            if token in _OPTS_WITH_ARG:
                skip_next = True
                continue
            if token == "-":  # program read from stdin: the rest are arguments
                position = False
                continue
            if token.startswith("-"):
                continue
            if "$" in token:
                if shell_vars and _VAR_LAUNCH.match(token.strip("'\"")):
                    scan.unresolved.append(token)
                position = not shell_vars
                continue
            position = False
            clean = token.strip("'\"")
            if clean in index.clis:
                scan.targets.append(index.clis[clean])
                continue
            hit = _resolve(token, base, index)
            if hit is not None:
                scan.targets.append(hit)
            elif _looks_missing(token):
                scan.missing.append(clean)
            continue
        hit = _resolve(token, base, index) if "$" not in token else None
        if hit is not None and hit not in scan.mentions:
            scan.mentions.append(hit)
    scan.targets = list(dict.fromkeys(scan.targets))
    return scan
