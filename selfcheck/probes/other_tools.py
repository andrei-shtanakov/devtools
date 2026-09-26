"""Shell, workflow and text-clone probes (spec §3.1); all static."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

from selfcheck.model import Confidence, Finding, Location, normalize_line
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from selfcheck.roles import Role, role_of

ANY = frozenset({"any"})
JSCPD_VERSION = "4.3.0"
JSCPD_MIN_LINES = 5
_SHEBANG = re.compile(rb"^#!.*\b(sh|bash|dash|ksh)\b")
_CLONE_SUFFIXES = (".py", ".sh", ".bash", ".js", ".ts", ".yml", ".yaml", ".toml")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _is_shell(target: RepoTarget, rel: str) -> bool:
    if rel.endswith((".sh", ".bash")):
        return True
    if "." in rel.rsplit("/", 1)[-1]:
        return False
    try:
        with (target.copy / rel).open("rb") as handle:
            first = handle.readline(200)
    except OSError:
        return False
    return bool(_SHEBANG.match(first))


def shell_files(target: RepoTarget) -> tuple[str, ...]:
    """Shell scripts by suffix or by shebang (extensionless files)."""
    return tuple(
        p
        for p in target.corpus
        if role_of(p, target.roles) is not Role.CANARY and _is_shell(target, p)
    )


def workflow_files(target: RepoTarget) -> tuple[str, ...]:
    """GitHub workflow files."""
    return tuple(
        p
        for p in target.corpus
        if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
    )


def _skip(result: ParseResult, rel: str, why: str) -> None:
    result.diagnostics.append(f"{rel}: {why}")
    if rel not in result.skipped:
        result.skipped.append(rel)


# ---- shellcheck -----------------------------------------------------------


def _shellcheck_parse(
    ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]
) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout or '{"comments": []}')["comments"]:
        rel = rel_path(ctx, item["file"])
        if item["level"] == "error" and 1000 <= item["code"] < 1200:
            _skip(result, rel, f"SC{item['code']} {item['message']}")
            continue
        serious = item["level"] in ("error", "warning")
        result.findings.append(
            line_finding(
                ctx,
                f"shellcheck/SC{item['code']}",
                rel,
                item["line"],
                category="bug" if serious else "quality",
                severity="medium" if serious else "low",
                message=item["message"],
            )
        )
    return result


def _shellcheck_suppresses(ctx: ProbeCtx) -> bool:
    rc = ctx.target.copy / ".shellcheckrc"
    return rc.is_file() and "2086" in rc.read_text()


SHELLCHECK = ProbeSpec(
    name="shellcheck",
    languages=ANY,
    input_mode="files",
    select=shell_files,
    canary=Canary(
        ".selfcheck-canary/shellcheck/canary.sh",
        "#!/bin/sh\necho $1\n",
        "shellcheck/SC2086",
        "file:.selfcheck-canary/shellcheck/canary.sh",
    ),
    rules=("all",),
    binary="shellcheck",
    version_args=("-V",),
    version_range=((0, 11), (0, 12)),
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: ["-f", "json1", *copy_paths(ctx)],
    parse=_shellcheck_parse,
    config_suppresses=_shellcheck_suppresses,
    config_files=(".shellcheckrc",),
)

# ---- actionlint / zizmor ----------------------------------------------------

_INJECTION = """on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ github.event.head_commit.message }}
"""


def _actionlint_parse(
    ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]
) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout.strip() or "[]") or []:
        rel = rel_path(ctx, item["filepath"])
        if item["kind"] == "syntax-check":
            _skip(result, rel, item["message"])
            continue
        result.findings.append(
            line_finding(
                ctx,
                f"actionlint/{item['kind']}",
                rel,
                item["line"],
                category="bug",
                severity="medium",
                message=item["message"],
            )
        )
    return result


ACTIONLINT = ProbeSpec(
    name="actionlint",
    languages=ANY,
    input_mode="files",
    select=workflow_files,
    canary=Canary(
        ".selfcheck-canary/actionlint/.github/workflows/canary.yml",
        _INJECTION,
        "actionlint/expression",
        "file:.selfcheck-canary/actionlint/.github/workflows/canary.yml",
    ),
    rules=("all",),
    binary="actionlint",
    version_args=("-version",),
    version_range=((1, 7), (2, 0)),
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: ["-format", "{{json .}}", "-no-color", *copy_paths(ctx)],
    parse=_actionlint_parse,
)

_ZIZMOR_SEVERITY = {"High": "high", "Medium": "medium"}
_ZIZMOR_DONE = re.compile(r"completed (\S+)\s*$")


def _zizmor_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    done = [
        rel_path(ctx, m.group(1))
        for line in _ANSI.sub("", proc.stderr).splitlines()
        if (m := _ZIZMOR_DONE.search(line))
    ]
    result = ParseResult([], processed_paths=done)
    for line in proc.stderr.splitlines():
        if "failed to parse input" in line:
            result.diagnostics.append(_ANSI.sub("", line).strip())
    for item in json.loads(proc.stdout or "[]"):
        loc = next(x for x in item["locations"] if x["symbolic"]["kind"] == "Primary")
        local = loc["symbolic"]["key"]["Local"]
        raw = local.get("given_path") or local["verbatim_path"]
        line = loc["concrete"]["location"]["start_point"]["row"] + 1
        severity = _ZIZMOR_SEVERITY.get(item["determinations"]["severity"], "low")
        result.findings.append(
            line_finding(
                ctx,
                f"zizmor/{item['ident']}",
                rel_path(ctx, raw),
                line,
                category="bug",
                severity=severity,
                message=item["desc"],
            )
        )
    return result


ZIZMOR = ProbeSpec(
    name="zizmor",
    languages=ANY,
    input_mode="files",
    select=workflow_files,
    canary=Canary(
        ".selfcheck-canary/zizmor/.github/workflows/canary.yml",
        _INJECTION,
        "zizmor/template-injection",
        "file:.selfcheck-canary/zizmor/.github/workflows/canary.yml",
    ),
    coverage="reported",
    rules=("offline-audits",),
    binary="zizmor",
    version_range=((1, 30), (2, 0)),
    normal_codes=frozenset({0, 11, 12, 13, 14}),
    argv=lambda ctx: ["--offline", "--format", "json", *copy_paths(ctx)],
    parse=_zizmor_parse,
)

# ---- jscpd --------------------------------------------------------------------

_CLONE_BODY = "".join(f"    v{i} = a * {i} + 1\n" for i in range(10))
_JSCPD_CANARY = (
    f"def selfcheck_one(a):\n{_CLONE_BODY}    return a\n\n\n"
    f"def selfcheck_two(a):\n{_CLONE_BODY}    return a\n"
)


def _clone_files(target: RepoTarget) -> tuple[str, ...]:
    """Files jscpd reports on: known format and >= min-lines (spec §4.1)."""
    keep = (Role.SOURCE, Role.TEST, Role.SKILL_ROOT)
    out = []
    for p in target.corpus:
        if not p.endswith(_CLONE_SUFFIXES) or role_of(p, target.roles) not in keep:
            continue
        try:
            lines = (target.copy / p).read_text(errors="replace").count("\n")
        except OSError:
            continue
        if lines >= JSCPD_MIN_LINES:
            out.append(p)
    return tuple(out)


def _jscpd_argv(ctx: ProbeCtx) -> list[str]:
    return [
        "--yes",
        f"jscpd@{JSCPD_VERSION}",
        "--silent",
        "--absolute",
        "--min-lines",
        str(JSCPD_MIN_LINES),
        "--reporters",
        "json",
        "--output",
        str(ctx.work / "jscpd"),
        "--store-path",
        str(ctx.work / "jscpd-store"),
        *copy_paths(ctx),
    ]


def _jscpd_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    report = json.loads((ctx.work / "jscpd" / "jscpd-report.json").read_text())
    processed = [
        rel_path(ctx, path)
        for fmt in report["statistics"].get("formats", {}).values()
        for path in fmt.get("sources", {})
    ]
    result = ParseResult([], processed_paths=processed)
    for dup in report["duplicates"]:
        text = "\n".join(normalize_line(x) for x in dup["fragment"].splitlines())
        digest = hashlib.sha1(text.encode()).hexdigest()[:16]
        members = [
            (rel_path(ctx, dup[k]["name"]), int(dup[k]["start"]))
            for k in ("firstFile", "secondFile")
        ]
        result.findings.append(
            Finding(
                rule="jscpd/clone",
                category="duplicate",
                severity="medium",
                confidence=Confidence.LIKELY,
                owner_repo=ctx.target.name,
                anchor=f"dup:text:{digest}",
                locations=[Location(p, n) for p, n in members],
                related=[
                    {
                        "owner_repo": ctx.target.name,
                        "path": p,
                        "line": n,
                        "member": str(n),
                    }
                    for p, n in members
                ],
                evidence=[{"kind": "lines", "detail": str(dup["lines"])}],
                suggestion="вынести общий фрагмент",
            )
        )
    return result


JSCPD = ProbeSpec(
    name="jscpd",
    languages=ANY,
    input_mode="files",
    select=_clone_files,
    canary=Canary(
        ".selfcheck-canary/jscpd/canary.py",
        _JSCPD_CANARY,
        "jscpd/clone",
        "dup:text:*",
    ),
    coverage="reported",
    rules=(f"min-lines:{JSCPD_MIN_LINES}",),
    binary="npx",
    version_args=("--yes", f"jscpd@{JSCPD_VERSION}", "--version"),
    version_range=((4, 3), (4, 4)),
    normal_codes=frozenset({0, 1}),
    argv=_jscpd_argv,
    parse=_jscpd_parse,
    config_files=(".jscpd.json",),
)

OTHER_PROBES = (SHELLCHECK, ACTIONLINT, ZIZMOR, JSCPD)
