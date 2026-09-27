"""The judge: tool-less LLM verdicts over candidates (spec §5, §11)."""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from selfcheck.corpus import raw_path
from selfcheck.graph.model import parse_python
from selfcheck.model import Confidence, Finding

JUDGE_RULES = ("llm-sites", "ast-dup", "cli-overlap", "jscpd")
_LEVEL = {Confidence.LIKELY: 0, Confidence.CANDIDATE: 1}


def is_candidate(f: Finding) -> bool:
    """``llm-replaceable`` and ``duplicate`` at candidate/likely (§5)."""
    return f.category in ("llm-replaceable", "duplicate") and f.confidence in _LEVEL


def clone_lines(f: Finding) -> int:
    """Length of a jscpd clone (0 for other rules)."""
    for e in f.evidence:
        if e.get("kind") == "lines":
            return int(e["detail"])
    return 0


def _repos(f: Finding) -> set[str]:
    return {r["owner_repo"] for r in f.related} or {f.owner_repo}


def order_key(f: Finding) -> tuple[int, int, int, int, str, str, int]:
    """Normative judge order under the cap: kind before level (§11.2)."""
    prefix = f.rule.split("/", 1)[0]
    loc = f.locations[0] if f.locations else None
    return (
        0 if len(_repos(f)) > 1 else 1,
        JUDGE_RULES.index(prefix) if prefix in JUDGE_RULES else len(JUDGE_RULES),
        _LEVEL.get(f.confidence, 2),
        -clone_lines(f),
        f.owner_repo,
        loc.path if loc else "",
        loc.line if loc else 0,
    )


MAX_SLICE = 48 * 1024
LLM_CONTEXT = 40
DUP_CONTEXT = 5
MAKE_SPAN = 30
LLM_PROMPT = (
    "You judge whether an LLM call site can be replaced by a script, rules, a "
    "decision tree, an embedding classifier or a small model. Reason first in "
    "`rationale`, then give `verdict` (replace|keep|unsure) and `replacement` "
    "(none when keep/unsure). Answer only via the JSON schema.\n"
)
DUP_PROMPT = (
    "You judge a reported duplicate. Decide whether the fragments should be merged "
    "into one shared function/module/fixture (verdict replace, replacement merge), "
    "kept as they are (keep, none) or you cannot tell (unsure, none). Consider test "
    "readability, intentional parallel structure and different evolution paths. "
    "Reason first in `rationale` (2-3 sentences). Answer only via the JSON schema.\n"
)


@dataclass(frozen=True)
class JudgeSlice:
    """What the judge sees on stdin (§11.5)."""

    text: str
    truncated: bool


def _lines(sources: Mapping[str, Path], repo: str, rel: str) -> list[str] | None:
    """The file as lines split on ``\\n`` only (like the tools), read by raw name."""
    root = sources.get(repo)
    if root is None:
        return None
    try:
        path = root / raw_path(root, rel)
        if path.is_symlink() or not path.is_file():  # no symlinks (§1.3)
            return None
        return path.read_bytes().decode(errors="replace").split("\n")
    except (OSError, subprocess.CalledProcessError):  # raw_path runs git
        return None


def _span(lines: list[str], line: int, rule: str, clone: int) -> tuple[int, int]:
    if rule.startswith("llm-sites"):
        return max(1, line - LLM_CONTEXT), min(len(lines), line + LLM_CONTEXT)
    if rule == "cli-overlap/make-recipe":
        return line, min(len(lines), line + MAKE_SPAN)
    end = line + max(clone, 1) - 1  # jscpd `lines` is inclusive
    if rule.startswith(("ast-dup", "cli-overlap")):
        try:
            tree = parse_python("\n".join(lines))
        except SyntaxError:
            tree = None
        for node in ast.walk(tree) if tree is not None else ():
            if getattr(node, "lineno", None) == line and getattr(node, "end_lineno", 0):
                end = node.end_lineno  # type: ignore[attr-defined]
                break
    return max(1, line - DUP_CONTEXT), min(len(lines), end + DUP_CONTEXT)


def _parts(f: Finding) -> list[tuple[str, str, int]]:
    if f.related:
        return [(r["owner_repo"], r["path"], int(r["line"])) for r in f.related]
    return [(f.owner_repo, loc.path, loc.line) for loc in f.locations[:1]]


def build_slice(f: Finding, sources: Mapping[str, Path]) -> JudgeSlice | None:
    """Serialised judge input (§11.5); ``None`` when a source file is missing."""
    head = LLM_PROMPT if f.category == "llm-replaceable" else DUP_PROMPT
    feats = ", ".join(e["detail"] for e in f.evidence if e.get("kind") == "feature")
    blocks = [head, f"Finding: {f.rule} {f.anchor}" + (f" ({feats})" if feats else "")]
    for repo, rel, line in _parts(f):
        lines = _lines(sources, repo, rel)
        if lines is None:
            return None
        a, b = _span(lines, line, f.rule, clone_lines(f))
        body = "\n".join(lines[a - 1 : b])
        fence = "`" * 3
        blocks.append(f"## {repo}:{rel} lines {a}-{b}\n{fence}\n{body}\n{fence}")
    text = "\n".join(blocks)
    raw = text.encode()
    if len(raw) <= MAX_SLICE:
        return JudgeSlice(text, False)
    return JudgeSlice(raw[:MAX_SLICE].decode(errors="ignore") + "\n[truncated]", True)
