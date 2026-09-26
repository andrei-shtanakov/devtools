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
from selfcheck.graph.resolver import UNKNOWN, argv_at
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, rel_path, source_text
from selfcheck.probes.other_tools import shell_files
from selfcheck.probes.python_tools import python_files
from selfcheck.roles import Role, role_of

SEMGREP = "semgrep@1.178.0"
RULES_PATH = Path(__file__).parent / "rules" / "llm.yml"
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
_MECHANISM = {"py-launch": "A", "cli-shell": "A", "sdk-python": "B", "http-python": "C"}
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
    tree = ast.parse(source)
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


def _select(target: RepoTarget) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*python_files(target), *shell_files(target))))


def _argv(ctx: ProbeCtx) -> list[str]:
    return [
        SEMGREP,
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
        *copy_paths(ctx),
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
    if rel.endswith(".py"):
        invisible = False
        if rule == "py-launch":
            is_harness, invisible = _harness_launch(text, rel, line)
            if not is_harness:
                return None
        features, excluded = python_features(text, line)
        if invisible:
            features.append("prompt-invisible")
        anchor = python_anchor(text, rel, line)
        anchor = "llm:" + anchor.split(":", 1)[1]
    else:
        lines = text.splitlines()
        line_text = lines[line - 1] if 0 < line <= len(lines) else ""
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


def _parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    scanned = [rel_path(ctx, p) for p in data.get("paths", {}).get("scanned", [])]
    result = ParseResult([], processed_paths=scanned)
    for err in data.get("errors", []):
        result.diagnostics.append(str(err.get("message", err.get("type"))))
    for rel in ctx.inputs:
        if rel.endswith(".py"):
            try:
                ast.parse(source_text(ctx, rel))
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
    inventory: list[dict[str, Any]] = []
    for hit in data["results"]:
        rule = hit["check_id"].rsplit(".", 1)[-1]
        rel = rel_path(ctx, hit["path"])
        if rel in result.skipped or rule not in _MECHANISM:
            continue
        row = _site(ctx, rule, rel, hit["start"]["line"])
        if row is None:
            continue
        inventory.append(row)
        if row["candidate"]:
            result.findings.append(
                Finding(
                    rule="llm-sites/replaceable",
                    category="llm-replaceable",
                    severity="low",
                    confidence=Confidence.CANDIDATE,
                    owner_repo=ctx.target.name,
                    anchor=row["anchor"],
                    locations=[Location(rel, row["line"])],
                    evidence=[
                        {"kind": "feature", "detail": f} for f in row["features"]
                    ],
                    suggestion="скрипт / правила / дерево решений / малая модель",
                )
            )
    inventory += _configs(ctx)
    result.extra["inventory"] = [
        {k: v for k, v in r.items() if k != "anchor"}
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
    rules=("A", "B", "C", "D", "candidate:schema|loop"),
    logic_version=1,
    binary="uvx",
    version_args=(SEMGREP, "--version"),
    version_range=((1, 178), (1, 179)),
    version_timeout=600,
    normal_codes=frozenset({0}),
    argv=_argv,
    parse=_parse,
)
