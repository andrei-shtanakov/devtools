"""lint-coverage: a repo language that no linter checks in CI (spec §12).

Static: reads workflow text and follows one level of wrappers (``make``,
``npm run``, ``just``, ``pre-commit``); target code never runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from selfcheck.manifest import MARKERS
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget

LANG_MARKER = {lang: marker for marker, lang in MARKERS.items()}
LINTERS = {
    "python": re.compile(
        r"\bruff(?:@\S+)?\s+check\b|ruff-action|\bflake8\b|\bpylint\b"
        r"|^\s*-\s*id:\s*ruff(?:-check)?\s*$",
        re.MULTILINE,
    ),
    "rust": re.compile(r"\bclippy\b"),
    "elixir": re.compile(r"\bcredo\b"),
    "ts": re.compile(r"\beslint\b|\bbiome\s+(?:check|lint|ci)\b|\bknip\b|\btsc\b"),
}
EXAMPLE = {
    "python": "`uv run ruff check .`",
    "rust": "`cargo clippy --all-targets -- -D warnings`",
    "elixir": "`mix credo`",
    "ts": "`eslint` или `tsc --noEmit`",
}
# Linting these needs target code executed (S4): an open gap here is the
# condition for returning to S4 (owner decision 2026-09-28).
S4_LANGUAGES = frozenset({"rust", "elixir", "ts"})
CANARY_ROOT = ".selfcheck-canary/lint-coverage/"
_CANARY_MARKER = f"{CANARY_ROOT}pyproject.toml"
_WORKFLOWS = ".github/workflows/"
_EXTERNAL = re.compile(r"uses:\s*([\w.-]+/[\w.-]+/\.github/workflows/[^@\s]+)@")
_MAKE = re.compile(r"\bmake\b([^;&|\n]*)")
_MAKE_ARG_FLAGS = frozenset({"-C", "-f", "-I", "-o", "-W", "--file", "--directory"})
_RULE = re.compile(r"^([^\s#:=][^:=]*?)\s*::?(?!=)(.*)$")
_NPM = re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:run(?:-script)?\s+)?([\w:.-]+)")
_JUST = re.compile(r"\bjust\s+([\w-]+)")


def _uncommented(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _read(copy: Path, files: set[str], path: str) -> str:
    if path not in files:
        return ""
    return _uncommented((copy / path).read_text(errors="replace"))


def _make_targets(args: str) -> list[str]:
    targets: list[str] = []
    skip = False
    for tok in args.split():
        if skip:
            skip = False
        elif tok in _MAKE_ARG_FLAGS:
            skip = True
        elif not tok.startswith("-") and "=" not in tok:
            targets.append(tok)
    return targets


def _make_recipes(makefile: str, wanted: set[str]) -> str:
    out: list[str] = []
    current = False
    for line in makefile.splitlines():
        if line.startswith("\t"):
            if current:
                out.append(line)
            continue
        rule = _RULE.match(line)
        if rule is None:
            continue
        current = bool(wanted & set(rule.group(1).split()))
        if current and ";" in rule.group(2):
            out.append(rule.group(2).split(";", 1)[1])
    return "\n".join(out)


def _just_recipes(justfile: str, wanted: set[str]) -> str:
    out: list[str] = []
    current = False
    for line in justfile.splitlines():
        if line[:1].isspace():
            if current:
                out.append(line)
            continue
        name = re.match(r"^@?([\w-]+)[^:=]*:(?!=)", line)
        current = name is not None and name.group(1) in wanted
    return "\n".join(out)


def _npm_scripts(package: str, wanted: set[str]) -> str:
    try:
        scripts = json.loads(package or "{}").get("scripts") or {}
    except (json.JSONDecodeError, AttributeError):
        return ""
    return "\n".join(str(scripts[n]) for n in sorted(wanted) if n in scripts)


def _expand(ci: str, copy: Path, files: set[str], root: str) -> str:
    """Workflow text plus the one level of wrappers it names."""
    parts = [ci]
    makes = {t for m in _MAKE.finditer(ci) for t in _make_targets(m.group(1))}
    if makes:
        for name in ("Makefile", "makefile", "GNUmakefile"):
            parts.append(_make_recipes(_read(copy, files, root + name), makes))
    npm = {m.group(1) for m in _NPM.finditer(ci)}
    if npm:
        parts.append(_npm_scripts(_read(copy, files, root + "package.json"), npm))
    just = {m.group(1) for m in _JUST.finditer(ci)}
    if just:
        for name in ("justfile", "Justfile"):
            parts.append(_just_recipes(_read(copy, files, root + name), just))
    if re.search(r"\bpre-commit\b", ci):
        parts.append(_read(copy, files, root + ".pre-commit-config.yaml"))
    return "\n".join(parts)


def _finding(ctx: ProbeCtx, root: str, lang: str, workflows: list[str]) -> Finding:
    marker = root + LANG_MARKER[lang]
    evidence = [{"kind": "workflows", "detail": ", ".join(workflows) or "нет"}]
    if lang in S4_LANGUAGES:
        evidence.append(
            {"kind": "s4-return", "detail": "открытая дыра — условие возврата к S4"}
        )
    return Finding(
        rule="lint-coverage/missing",
        category="ci",
        severity="medium",
        confidence=Confidence.LIKELY,
        owner_repo=ctx.target.name,
        anchor=f"file:{marker}",
        locations=[Location(marker, 1)],
        text_key=lang,
        evidence=evidence,
        suggestion=(
            f"добавить линтер в CI (например {EXAMPLE[lang]}) или завести "
            "issue владельцу и записать [[allow]] со ссылкой на него"
        ),
    )


def _check_root(
    ctx: ProbeCtx, files: set[str], root: str, langs: frozenset[str]
) -> ParseResult:
    result = ParseResult([])
    workflows = sorted(
        p
        for p in files
        if p.startswith(root + _WORKFLOWS) and p.endswith((".yml", ".yaml"))
    )
    ci = "\n".join(_read(ctx.target.copy, files, p) for p in workflows)
    for m in _EXTERNAL.finditer(ci):
        result.notes.append(f"{root or '.'}: не раскрыт внешний workflow {m.group(1)}")
    text = _expand(ci, ctx.target.copy, files, root)
    for lang in sorted(langs):
        if lang in LINTERS and not LINTERS[lang].search(text):
            result.findings.append(_finding(ctx, root, lang, workflows))
    return result


def _analyze(ctx: ProbeCtx) -> ParseResult:
    files = set(ctx.target.corpus) | set(ctx.inputs)
    canary_langs = frozenset(
        lang for lang, m in LANG_MARKER.items() if CANARY_ROOT + m in files
    )
    result = ParseResult([])
    for root, langs in (("", ctx.target.languages), (CANARY_ROOT, canary_langs)):
        part = _check_root(ctx, files, root, langs)
        result.findings += part.findings
        result.notes += sorted(set(part.notes))
    return result


def _select(target: RepoTarget) -> tuple[str, ...]:
    corpus = set(target.corpus)
    return tuple(
        LANG_MARKER[lang]
        for lang in sorted(target.languages)
        if lang in LANG_MARKER and LANG_MARKER[lang] in corpus
    )


LINT_COVERAGE = ProbeSpec(
    name="lint-coverage",
    languages=frozenset({"any"}),
    input_mode="files",
    select=_select,
    canary=Canary(
        _CANARY_MARKER,
        '[project]\nname = "selfcheck-canary"\n',
        "lint-coverage/missing",
        f"file:{_CANARY_MARKER}",
    ),
    rules=("missing",),
    logic_version=1,
    analyze=_analyze,
)
