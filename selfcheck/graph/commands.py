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
UNRESOLVED_DIR = "$CD"  # prefix for a path relative to a non-literal `cd`


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
    # literal programs that resolved to no file of this repo, normalised against
    # the known base — the fleet's "external targets" (spec §9.3)
    external: list[str] = field(default_factory=list)


def _tokens(cmd: str, shell_vars: bool) -> list[str]:
    if shell_vars:  # command substitution starts (and closes) a command
        cmd = cmd.replace("$(", " ; ").replace("`", " ; ")
    try:
        tokens = shlex.split(cmd, comments=True)
    except ValueError:
        tokens = cmd.split()
    if not shell_vars:
        return tokens
    out: list[str] = []
    for token in tokens:
        stripped = token.rstrip(")")
        if stripped:
            out.append(stripped)
        if stripped != token:
            out.append(";")
    return out


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


@dataclass
class _State:
    base: str
    base_known: bool = True
    position: bool = True
    first: bool = True
    skip_next: bool = False
    want_module: bool = False
    want_dir: bool = False


def _command_position(
    token: str, st: _State, index: Index, scan: Scan, shell_vars: bool
) -> None:
    """Handle a token while no program has been chosen for this command yet."""
    if _ASSIGN.match(token) or token in _WRAPPERS:
        return
    if token == "cd":
        st.want_dir = True
        st.position = False
        return
    if token == "-m":
        st.want_module = True
        return
    if token in _OPTS_WITH_ARG:
        st.skip_next = True
        return
    if token == "-":  # program read from stdin: the rest are arguments
        st.position = False
        return
    if token.startswith("-"):
        return
    if "$" in token:
        # fail closed: a program chosen through anything non-literal is a zone
        if shell_vars and "${{" not in token:
            scan.unresolved.append(token)
        st.position = not shell_vars
        return
    st.position = False
    clean = token.strip("'\"")
    if clean in index.clis:
        scan.targets.append(index.clis[clean])
        return
    hit = _resolve(token, st.base, index)
    if hit is not None:
        scan.targets.append(hit)
    elif not st.base_known and ("/" in clean or clean.startswith(".")):
        scan.unresolved.append(f"{UNRESOLVED_DIR}/{clean}")
    elif st.base_known and _looks_missing(token):
        scan.missing.append(clean)
    if hit is None and st.base_known and "$" not in clean:
        joined = clean if clean.startswith("/") else posixpath.join(st.base, clean)
        scan.external.append(posixpath.normpath(joined))


def scan_command(
    cmd: str,
    base: str,
    index: Index,
    *,
    shell_vars: bool,
    base_known: bool = True,
) -> Scan:
    """Launch targets (command position) vs mentioned files (arguments).

    ``shell_vars``: a shell program chosen through ``$…`` is unresolved (a
    zone); otherwise ``$(VAR)``/``${{ … }}`` tokens are skipped as
    configuration. A literal ``cd dir`` moves the base for the rest of the
    line; a non-literal one makes later relative launches unresolved.
    """
    scan = Scan()
    st = _State(base, base_known)
    for raw in _tokens(cmd, shell_vars):
        token = raw.lstrip("@+-") if st.first and raw[:1] in "@+-" else raw
        st.first = False
        if st.skip_next:
            st.skip_next = False
            continue
        if token in _SEPARATORS or token.endswith(";"):
            st.position, st.first = True, True
            continue
        if st.want_dir:
            st.want_dir = False
            target = token.strip("'\"")
            if "$" in target or target.startswith(("/", "~")):
                st.base_known = False
            else:
                st.base = posixpath.normpath(posixpath.join(st.base, target))
            continue
        if st.want_module:
            scan.targets += module_files(token, index)
            st.want_module, st.position = False, False
            continue
        if st.position:
            _command_position(token, st, index, scan, shell_vars)
            continue
        hit = _resolve(token, st.base, index) if "$" not in token else None
        if hit is not None and hit not in scan.mentions:
            scan.mentions.append(hit)
    scan.targets = list(dict.fromkeys(scan.targets))
    return scan
