"""The judge: tool-less LLM verdicts over candidates (spec §5, §11)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from selfcheck.corpus import raw_path
from selfcheck.graph.model import parse_python
from selfcheck.model import Confidence, Finding
from selfcheck.probes.base import run_group

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


DEFAULT_MODEL = "claude-opus-5-5"
TIMEOUT = 120
MAX_OUTPUT = 64 * 1024
SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rationale", "verdict", "replacement"],
    "properties": {
        "rationale": {"type": "string"},
        "verdict": {"enum": ["replace", "keep", "unsure"]},
        "replacement": {
            "enum": [
                "regex",
                "rules",
                "decision-tree",
                "embedding-classifier",
                "small-model",
                "merge",
                "none",
            ]
        },
    },
}
_ENV_BASE = ("PATH", "HOME")
_ENV_PLATFORM = {
    "darwin": ("USER",),  # keychain lookup (§11.4)
    "linux": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
}
Runner = Callable[..., subprocess.CompletedProcess[str]]


def judge_env(platform: str, environ: Mapping[str, str]) -> dict[str, str]:
    """The allowlisted environment of the judge process (§11.4)."""
    keys = (*_ENV_BASE, *_ENV_PLATFORM.get(platform, ()))
    return {k: environ[k] for k in keys if k in environ}


def judge_argv(binary: str, model: str) -> list[str]:
    """Tool-less, stdin-only claude invocation (§5)."""
    return [
        binary,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
        "--model",
        model,
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts",
        "none",
        "--tools",
        "",
        "--max-budget-usd",
        "0.2",
    ]


def _error(detail: str) -> dict[str, str]:
    return {"verdict": "error", "detail": detail[:200]}


def valid_verdict(out: object) -> dict[str, str] | None:
    """The verdict fields when ``out`` matches SCHEMA exactly, else None."""
    if not isinstance(out, dict) or set(out) != set(SCHEMA["required"]):
        return None
    props = SCHEMA["properties"]
    if out["verdict"] not in props["verdict"]["enum"]:
        return None
    if out["replacement"] not in props["replacement"]["enum"]:
        return None
    if not isinstance(out["rationale"], str):
        return None
    return {k: str(out[k]) for k in SCHEMA["required"]}


def call_judge(
    binary: str,
    text: str,
    model: str,
    *,
    runner: Runner = run_group,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """One judge call; any failure is ``{"verdict": "error"}`` (§5, §11.4)."""
    try:
        with tempfile.TemporaryDirectory(prefix="selfcheck-judge-") as cwd:
            stdin_path = Path(cwd) / "stdin.txt"
            stdin_path.write_text(text, encoding="utf-8")
            with stdin_path.open(encoding="utf-8") as stdin:
                proc = runner(
                    judge_argv(binary, model),
                    stdin=stdin,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=cwd,
                    env=judge_env(platform, environ),
                    timeout=TIMEOUT,
                )
    except subprocess.TimeoutExpired:
        return _error("timeout")
    except Exception as exc:  # noqa: BLE001 — the adapter never kills the run (r1 P6)
        return _error(f"adapter: {exc!r}")
    if len(proc.stdout.encode()) > MAX_OUTPUT:
        return _error("output-too-large")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _error(f"unparsable (rc {proc.returncode})")
    if not isinstance(data, dict) or data.get("is_error"):
        return _error(str(data.get("result") if isinstance(data, dict) else data))
    verdict = valid_verdict(data.get("structured_output"))
    return verdict if verdict is not None else _error("outside-schema")


JUDGE_VERSION = 1  # bump on any prompt/schema change (§11.3)


def cache_key(text: str, model: str) -> str:
    """``v<version>:<model>:<sha256 of the slice>`` (§11.3)."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    return f"v{JUDGE_VERSION}:{model}:{digest}"


def _current(key: str) -> bool:
    return key.startswith(f"v{JUDGE_VERSION}:")


def load_cache(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Valid current-version entries; anything else is a warning, never exit 4."""
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {}, [f"judge-cache {path.name} unreadable, starting empty: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"judge-cache {path.name} is not an object, starting empty"]
    cache: dict[str, dict] = {}
    bad = 0
    for key, entry in data.items():
        if not _current(key):
            continue
        verdict = valid_verdict(
            {k: v for k, v in entry.items() if k != "at"}
            if isinstance(entry, dict)
            else None
        )
        if verdict is None:
            bad += 1
            continue
        cache[key] = {**verdict, "at": str(entry.get("at", ""))}
    warnings = [f"judge-cache: {bad} entries off the schema skipped"] if bad else []
    return cache, warnings


def save_cache(path: Path, cache: Mapping[str, dict]) -> str | None:
    """Atomic write via a unique temp file; old versions dropped (§11.3)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in cache.items() if _current(k)}
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, prefix=".judge-cache-", delete=False
        ) as tmp:
            json.dump(keep, tmp, ensure_ascii=False, sort_keys=True)
        os.replace(tmp.name, path)
    except OSError as exc:
        return f"judge-cache {path.name} not saved: {exc}"
    return None
