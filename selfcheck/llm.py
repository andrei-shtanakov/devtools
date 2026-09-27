"""llm-sites: LLM call sites and replaceability heuristics (spec §3.4)."""

from __future__ import annotations

import ast
import json
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Any

from selfcheck.anchors import python_anchor
from selfcheck.graph.model import parse_python
from selfcheck.graph.resolver import UNKNOWN, argv_at
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, rel_path, source_text
from selfcheck.probes.other_tools import shell_files
from selfcheck.probes.python_tools import python_files
from selfcheck.roles import Role, role_of

SEMGREP = "semgrep@1.178.0"
RULES_PATH = Path(__file__).parent / "rules" / "llm.yml"
# the whole uvx environment pinned `==` (#408); regeneration — in the file.
# The name must match the review-scope CODE_OVERRIDE (`*constraints*.txt`):
# a pin bump is code, not prose (review of #424)
ENV_PATH = Path(__file__).parent / "rules" / "semgrep-constraints.txt"
UVX_SEMGREP = ("-c", str(ENV_PATH), SEMGREP)
HARNESSES = frozenset(
    {
        "claude",
        "codex",
        "opencode",
        "aider",
        "pi",
        "qwen",
        "ollama",
        "llama-cli",
        "copilot",
    }
)
MAX_TARGET_BYTES = 1_000_000  # semgrep's default --max-target-bytes (§10.7)
_MECHANISM = {
    "py-launch": "A",
    "cli-shell": "A",
    "argv-literal": "A",
    "argv-literal-ts": "A",
    "harness-resolve": "A",
    "spawn-ts": "A",
    "sdk-python": "B",
    "sdk-ts": "B",
    "http-python": "C",
    "endpoint": "C",
}
_TS_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")
_PATHS = r"(?:/v1/messages|/chat/completions|/api/chat|/api/generate|/completion)\b"
_ENDPOINT = re.compile(rf"^\S*?{_PATHS}")
# a literal that starts at its quote and has no whitespace before the path
_QUOTED_ENDPOINT = re.compile(rf"[\"'`][^\s\"'`]*{_PATHS}")
# shell: also a bare word that is a URL or starts with a variable
_BARE_ENDPOINT = re.compile(
    rf"(?:^|[\s=(])(?:[a-z][a-z0-9+.-]*://|\$\{{?\w+\}}?)[^\s\"'`]*{_PATHS}"
)
_TS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/")
_TRAILING_TS = re.compile(r"(?:^|\s)//")
_TRAILING_SH = re.compile(r"(?:^|\s)#")


def _code_endpoint_line(rel: str, line_text: str) -> bool:
    """TS/shell: an endpoint literal in code, comments cut off (§10.7, review I1)."""
    if rel.endswith(_TS_SUFFIXES):
        code = _TRAILING_TS.split(_TS_BLOCK_COMMENT.sub("", line_text), maxsplit=1)[0]
        return bool(_QUOTED_ENDPOINT.search(code))
    code = _TRAILING_SH.split(line_text, maxsplit=1)[0]
    return bool(_QUOTED_ENDPOINT.search(code) or _BARE_ENDPOINT.search(code))


_EXCLUDE = re.compile(r"diff|read_text\(|\.read\(\)|git (?:show|diff)")
_FIXED_KEY = re.compile(r"\[\s*['\"]\w+['\"]\s*\]")
_HUMAN = re.compile(r"print\(|\.write\(|comment|post")
_CONFIG_SUFFIXES = (".toml", ".yaml", ".yml", ".json")
_CONFIG_HIT = re.compile(
    r"\b(?:claude|codex|opencode|aider|ollama|qwen)\b|claude-[a-z0-9.-]+|gpt-\d"
)
_INVISIBLE_PROMPT = re.compile(r'(?:^|\s)(?:"?\$[@1-9]"?|-)(?:\s|$)')


def _enclosing(tree: ast.Module, line: int) -> ast.AST:
    """Innermost function containing ``line`` (the module when none)."""
    funcs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.lineno <= line <= (n.end_lineno or n.lineno)
    ]
    return max(funcs, key=lambda n: n.lineno) if funcs else tree


def _in_loop(func: ast.AST, line: int) -> bool:
    loops = (
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    return any(
        isinstance(n, loops) and n.lineno <= line <= (n.end_lineno or n.lineno)
        for n in ast.walk(func)
    )


def python_features(source: str, line: int) -> tuple[list[str], bool]:
    """Heuristic features of a Python call site and the exclusion flag."""
    tree = parse_python(source)
    func = _enclosing(tree, line)
    text = source
    if func is not tree:
        text = ast.get_source_segment(source, func) or source
    features = []
    if ("json.loads" in text and _FIXED_KEY.search(text)) or "--json-schema" in text:
        features.append("fixed-schema")
    if _in_loop(func, line):
        features.append("loop")
    if re.search(r"f[\"'][^\"']*\{\w+\}", text) or ".format(" in text:
        features.append("template-prompt")
    if not _HUMAN.search(text):
        features.append("no-human-text")
    return features, bool(_EXCLUDE.search(text))


def _shell_features(text: str, line_text: str) -> tuple[list[str], bool]:
    features = ["fixed-schema"] if "--json-schema" in text or "jq " in text else []
    invisible = bool(_INVISIBLE_PROMPT.search(line_text))
    if invisible:
        features.append("prompt-invisible")
    return features, "diff" in text or invisible


def ts_files(target: RepoTarget) -> tuple[str, ...]:
    """Corpus TS/JS files except canaries (spec §10.7)."""
    return tuple(
        p
        for p in target.corpus
        if p.endswith(_TS_SUFFIXES) and role_of(p, target.roles) is not Role.CANARY
    )


def _skipped_nodes(tree: ast.AST) -> set[int]:
    """Docstrings and the constant pieces of f-strings (checked joined)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            ids |= {id(v) for v in node.values}
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
        )
    return None


def code_endpoint(text: str, line: int) -> bool:
    """A code string on ``line`` names an endpoint with no whitespace before it."""
    try:
        tree = parse_python(text)
    except SyntaxError:
        return False
    skip = _skipped_nodes(tree)
    for node in ast.walk(tree):
        value = _literal(node)
        if value is None or id(node) in skip:
            continue
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", None) or start
        if start <= line <= end and _ENDPOINT.search(value):
            return True
    return False


def _literal_argv_invisible(text: str, line: int) -> bool:
    """A literal argv on ``line`` whose non-flag elements after the binary are
    all non-literal: the prompt is not visible statically (§3.4, §10.7)."""
    try:
        tree = parse_python(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and node.lineno == line:
            rest = [
                e
                for e in node.elts[1:]
                if not (
                    isinstance(e, ast.Constant)
                    and isinstance(e.value, str)
                    and e.value.startswith("-")
                )
            ]
            return bool(rest) and not any(
                isinstance(e, ast.Constant | ast.JoinedStr) for e in rest
            )
    return False


def _select(target: RepoTarget) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys((*python_files(target), *shell_files(target), *ts_files(target)))
    )


def _argv(ctx: ProbeCtx) -> list[str]:
    return [
        *UVX_SEMGREP,
        "scan",
        "--config",
        str(RULES_PATH),
        "--json",
        "--metrics",
        "off",
        "--disable-version-check",
        "--no-git-ignore",
        "--scan-unknown-extensions",
        "--quiet",
        *[p for p in copy_paths(ctx) if Path(p).stat().st_size <= MAX_TARGET_BYTES],
    ]


def _harness_launch(text: str, rel: str, line: int) -> tuple[bool, bool]:
    """(is a harness launch, prompt statically invisible)."""
    argv = argv_at(text, rel, line)
    if not argv:
        return False, False
    first = posixpath.basename(argv[0].strip("'\""))
    if first not in HARNESSES:
        return False, False
    rest = [a for a in argv[1:] if not a.startswith("-")]
    return True, bool(rest) and all(a == UNKNOWN for a in rest)


def _site(ctx: ProbeCtx, rule: str, rel: str, line: int) -> dict[str, Any] | None:
    text = source_text(ctx, rel)
    lines = text.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    if rule == "endpoint":
        if rel.endswith(".py"):
            if not code_endpoint(text, line):
                return None
        elif line_text.lstrip().startswith("*") or not _code_endpoint_line(
            rel, line_text
        ):
            return None
    if rel.endswith(_TS_SUFFIXES):
        # TS: file-level anchor, inventory only (§10.7, named cost)
        return {
            "path": rel,
            "line": line,
            "mechanism": _MECHANISM[rule],
            "rule": rule,
            "candidate": False,
            "features": [],
            "anchor": f"llm:{rel}",
        }
    if rel.endswith(".py"):
        invisible = False
        if rule == "py-launch":
            is_harness, invisible = _harness_launch(text, rel, line)
            if not is_harness:
                return None
        elif rule == "argv-literal":
            invisible = _literal_argv_invisible(text, line)
        features, excluded = python_features(text, line)
        if invisible:
            features.append("prompt-invisible")
        anchor = python_anchor(text, rel, line)
        anchor = "llm:" + anchor.split(":", 1)[1]
    else:
        features, excluded = _shell_features(text, line_text)
        invisible = "prompt-invisible" in features
        anchor = f"llm:{rel}"
    strong = {"fixed-schema", "loop"} & set(features)
    return {
        "path": rel,
        "line": line,
        "mechanism": _MECHANISM[rule],
        "rule": rule,
        "candidate": bool(strong) and not excluded and not invisible,
        "features": features,
        "anchor": anchor,
        "excluded": excluded,
    }


def _configs(ctx: ProbeCtx) -> list[dict[str, Any]]:
    rows = []
    for rel in ctx.target.corpus:
        if not rel.endswith(_CONFIG_SUFFIXES) or role_of(rel) is Role.CANARY:
            continue
        for number, line in enumerate(source_text(ctx, rel).splitlines(), 1):
            if _CONFIG_HIT.search(line):
                rows.append(
                    {
                        "path": rel,
                        "line": number,
                        "mechanism": "D",
                        "rule": "config",
                        "candidate": False,
                        "features": [],
                    }
                )
                break
    return rows


def _unparsed(ctx: ProbeCtx, err: dict[str, Any]) -> str:
    """``<file>: … lines a-b unparsed`` for a PartialParsing error."""
    spans = [f"{s['start']['line']}-{s['end']['line']}" for s in err.get("spans", [])]
    where = rel_path(ctx, err["path"]) if err.get("path") else "?"
    return f"{where}: semgrep PartialParsing, lines {', '.join(spans) or '?'} unparsed"


def _parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    scanned = [rel_path(ctx, p) for p in data.get("paths", {}).get("scanned", [])]
    result = ParseResult([], processed_paths=scanned)
    for err in data.get("errors", []):
        kind = err.get("type")
        name = kind[0] if isinstance(kind, list) and kind else str(kind)
        text = f"semgrep {err.get('level')} {name}: {str(err.get('message', ''))[:200]}"
        if name == "PartialParsing":  # a span is unparsed: partial (§4.2, #408)
            result.diagnostics.append(_unparsed(ctx, err))
        else:
            (result.notes if err.get("level") == "warn" else result.diagnostics).append(
                text
            )
    for rel in ctx.inputs:
        if rel.endswith(".py"):
            try:
                parse_python(source_text(ctx, rel))
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
    big = sorted(
        r for r in ctx.inputs if (ctx.target.copy / r).stat().st_size > MAX_TARGET_BYTES
    )
    if big:
        result.notes.append(f"larger than {MAX_TARGET_BYTES} bytes, not scanned: {big}")
        # counted as processed: the skip is named in the note, not a lost input
        assert result.processed_paths is not None
        result.processed_paths += big
    inventory: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for hit in data["results"]:
        rule = hit["check_id"].rsplit(".", 1)[-1]
        rel = rel_path(ctx, hit["path"])
        if rel in result.skipped or rule not in _MECHANISM:
            continue
        row = _site(ctx, rule, rel, hit["start"]["line"])
        if row is None or (row["path"], row["line"]) in seen:
            continue
        seen.add((row["path"], row["line"]))
        inventory.append(row)
        by_anchor.setdefault(row["anchor"], []).append(row)
    # heuristics span the function (§3.4): features of all rows of a point
    # are pooled; an exclusion or an invisible prompt on any row vetoes it
    for anchor, rows in by_anchor.items():
        features = sorted({f for r in rows for f in r["features"]})
        strong = {"fixed-schema", "loop"} & set(features)
        veto = "prompt-invisible" in features or any(r.get("excluded") for r in rows)
        is_candidate = bool(strong) and not veto  # TS rows carry no features
        for r in rows:
            r["candidate"] = is_candidate
        if not is_candidate:
            continue
        result.findings.append(
            Finding(
                rule="llm-sites/replaceable",
                category="llm-replaceable",
                severity="low",
                confidence=Confidence.CANDIDATE,
                owner_repo=ctx.target.name,
                anchor=anchor,
                locations=[Location(r["path"], r["line"]) for r in rows],
                evidence=[{"kind": "feature", "detail": f} for f in features],
                suggestion="скрипт / правила / дерево решений / малая модель",
            )
        )
    inventory += _configs(ctx)
    result.extra["inventory"] = [
        {k: v for k, v in r.items() if k not in ("anchor", "excluded")}
        for r in inventory
        if role_of(r["path"]) is not Role.CANARY
    ]
    return result


_CANARY = """import json
import subprocess


def selfcheck_classify(items):
    labels = []
    for item in items:
        raw = subprocess.run(["claude", "-p", f"label {item}"],
                             capture_output=True, text=True).stdout
        labels.append(json.loads(raw)["label"])
    return labels
"""

LLM_SITES = ProbeSpec(
    name="llm-sites",
    languages=frozenset({"any"}),
    input_mode="files",
    select=_select,
    canary=Canary(
        ".selfcheck-canary/llm-sites/canary.py",
        _CANARY,
        "llm-sites/replaceable",
        "llm:.selfcheck-canary/llm-sites/canary.py::selfcheck_classify",
    ),
    coverage="reported",
    rules=("A", "B", "C", "D", "candidate:schema|loop", "construction"),
    logic_version=2,
    binary="uvx",
    version_args=(*UVX_SEMGREP, "--version"),
    version_range=((1, 178), (1, 179)),
    version_timeout=600,
    normal_codes=frozenset({0}),
    argv=_argv,
    parse=_parse,
)
