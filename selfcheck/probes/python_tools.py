"""Python static probes (spec §3.1, §1.5); none executes target code."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from fnmatch import fnmatch
from typing import Any

from selfcheck.env import package_module_map
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from selfcheck.roles import Role, role_of

PY = frozenset({"python"})
RUFF_SELECT = ("F", "B", "PL", "SIM", "ERA", "C90", "ARG", "RET")
_BUG_PREFIXES = ("F", "B", "PLE")


def python_files(target: RepoTarget) -> tuple[str, ...]:
    """Corpus .py files except canaries (the core appends a probe's own)."""
    return tuple(
        p
        for p in target.corpus
        if p.endswith(".py") and role_of(p, target.roles) is not Role.CANARY
    )


def _toml(ctx: ProbeCtx, name: str) -> dict[str, Any]:
    path = ctx.target.copy / name
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return {}


# ---- ruff -----------------------------------------------------------------


def _ruff_argv(ctx: ProbeCtx) -> list[str]:
    return [
        "check",
        "--no-cache",
        "--output-format",
        "json",
        "--extend-select",
        ",".join(RUFF_SELECT),
        *copy_paths(ctx),
    ]


def _ruff_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout or "[]"):
        rel = rel_path(ctx, item["filename"])
        code = item.get("code")
        if code is None or code == "invalid-syntax":
            result.diagnostics.append(f"{rel}: {item.get('message', '')}")
            if rel not in result.skipped:
                result.skipped.append(rel)
            continue
        bug = code.startswith(_BUG_PREFIXES)
        result.findings.append(
            line_finding(
                ctx,
                f"ruff/{code}",
                rel,
                item["location"]["row"],
                category="bug" if bug else "quality",
                severity="medium" if bug else "low",
                message=item["message"],
            )
        )
    return result


def _ruff_suppresses(ctx: ProbeCtx) -> bool:
    for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        data = _toml(ctx, name)
        root = (
            data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
        )
        ignores = {
            **root.get("per-file-ignores", {}),
            **root.get("lint", {}).get("per-file-ignores", {}),
        }
        for pattern, codes in ignores.items():
            if fnmatch(RUFF.canary.relpath, pattern) and any(
                c == "ALL" or "F401".startswith(c) for c in codes
            ):
                return True
    return False


RUFF = ProbeSpec(
    name="ruff",
    languages=PY,
    input_mode="files",
    select=python_files,
    canary=Canary(
        ".selfcheck-canary/ruff/canary.py",
        "import os\n",
        "ruff/F401",
        "file:.selfcheck-canary/ruff/canary.py",
    ),
    rules=RUFF_SELECT,
    binary="ruff",
    version_range=((0, 16), (0, 17)),
    normal_codes=frozenset({0, 1}),
    argv=_ruff_argv,
    parse=_ruff_parse,
    config_suppresses=_ruff_suppresses,
    config_files=("pyproject.toml", "ruff.toml", ".ruff.toml"),
)


# ---- pyrefly --------------------------------------------------------------


def _pyrefly_configured(ctx: ProbeCtx) -> bool:
    has_table = "pyrefly" in _toml(ctx, "pyproject.toml").get("tool", {})
    return (ctx.target.copy / "pyrefly.toml").is_file() or has_table


def _pyrefly_argv(ctx: ProbeCtx) -> list[str]:
    platform = "darwin" if sys.platform == "darwin" else "linux"
    args = [
        "check",
        "--output-format",
        "json",
        "--summary=none",
        "--skip-interpreter-query",
        "--python-platform",
        platform,
        "--error",
        "bad-return",
    ]
    if not _pyrefly_configured(ctx):
        args += ["--preset", "default"]
    env = ctx.target.env
    if env.mode == "checkout-venv" and env.site_packages is not None:
        args += ["--site-package-path", str(env.site_packages)]
        if env.python_version:
            args += ["--python-version", env.python_version]
    return args + copy_paths(ctx)


def _pyrefly_parse(
    ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]
) -> ParseResult:
    result = ParseResult([])
    for err in json.loads(proc.stdout)["errors"]:
        rel = rel_path(ctx, err["path"])
        if err["name"] == "parse-error":
            result.diagnostics.append(f"{rel}: {err['concise_description']}")
            if rel not in result.skipped:
                result.skipped.append(rel)
            continue
        is_error = err.get("severity", "error") == "error"
        result.findings.append(
            line_finding(
                ctx,
                f"pyrefly/{err['name']}",
                rel,
                err["line"],
                category="bug" if is_error else "quality",
                severity="medium" if is_error else "low",
                message=err["concise_description"],
            )
        )
    return result


def _pyrefly_suppresses(ctx: ProbeCtx) -> bool:
    paths = [ctx.target.copy / n for n in ("pyrefly.toml", "pyproject.toml")]
    return any(p.is_file() and "bad-return" in p.read_text() for p in paths)


PYREFLY = ProbeSpec(
    name="pyrefly",
    languages=PY,
    input_mode="files",
    select=python_files,
    canary=Canary(
        ".selfcheck-canary/pyrefly/canary.py",
        'def selfcheck_canary() -> int:\n    return "x"\n',
        "pyrefly/bad-return",
        "func:.selfcheck-canary/pyrefly/canary.py::selfcheck_canary",
    ),
    rules=("preset:default-unless-configured", "error:bad-return"),
    binary="pyrefly",
    version_range=((1, 3), (2, 0)),
    normal_codes=frozenset({0, 1}),
    argv=_pyrefly_argv,
    parse=_pyrefly_parse,
    config_suppresses=_pyrefly_suppresses,
    config_files=("pyproject.toml", "pyrefly.toml"),
)


# ---- vulture --------------------------------------------------------------

_VULTURE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<msg>.+?) "
    r"\((?P<conf>\d+)% confidence(?:, \d+ lines?)?\)$"
)


def _vulture_parse(
    ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]
) -> ParseResult:
    result = ParseResult([])
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if not line.strip():
            continue
        match = _VULTURE.match(line)
        if match:
            kind = "-".join(match["msg"].split(" '", 1)[0].split())
            conf = int(match["conf"])
            result.findings.append(
                line_finding(
                    ctx,
                    f"vulture/{kind}",
                    rel_path(ctx, match["path"]),
                    int(match["line"]),
                    category="dead",
                    severity="low",
                    confidence=(
                        Confidence.LIKELY if conf == 100 else Confidence.CANDIDATE
                    ),
                    message=match["msg"],
                )
            )
        elif "invalid syntax" in line or "SyntaxError" in line:
            rel = rel_path(ctx, line.split(":", 1)[0])
            result.diagnostics.append(line)
            if rel not in result.skipped:
                result.skipped.append(rel)
        elif line.startswith("Error:") or "Error:" in line:
            raise ValueError(line[-300:])
    return result


VULTURE = ProbeSpec(
    name="vulture",
    languages=PY,
    input_mode="files",
    select=python_files,
    canary=Canary(
        ".selfcheck-canary/vulture/canary.py",
        "def selfcheck_canary_unused() -> int:\n    return 1\n",
        "vulture/unused-function",
        "func:.selfcheck-canary/vulture/canary.py::selfcheck_canary_unused",
    ),
    rules=("min-confidence:60",),
    binary="vulture",
    version_range=((2, 16), (3, 0)),
    normal_codes=frozenset({0, 1, 3}),
    argv=lambda ctx: ["--min-confidence", "60", *copy_paths(ctx)],
    parse=_vulture_parse,
    config_files=("pyproject.toml",),
)


# ---- radon ----------------------------------------------------------------

_RADON_CANARY = (
    "def selfcheck_canary(x: int) -> int:\n"
    + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(22))
    + "    return -1\n"
)


def _radon_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for raw, blocks in json.loads(proc.stdout or "{}").items():
        rel = rel_path(ctx, raw)
        if isinstance(blocks, dict):
            result.diagnostics.append(f"{rel}: {blocks.get('error')}")
            result.skipped.append(rel)
            continue
        for block in blocks:
            result.findings.append(
                line_finding(
                    ctx,
                    f"radon/cc-{block['rank']}",
                    rel,
                    block["lineno"],
                    category="quality",
                    severity="low",
                    message=f"{block['name']}: complexity {block['complexity']}",
                )
            )
    mi = subprocess.run(
        [str(proc.args[0]), "mi", "-j", "--min", "C", *copy_paths(ctx)],
        cwd=ctx.cwd,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if mi.returncode != 0:
        raise ValueError(f"radon mi exit {mi.returncode}: {mi.stderr[-300:]}")
    for raw, entry in json.loads(mi.stdout or "{}").items():
        rel = rel_path(ctx, raw)
        if "error" in entry:
            if rel not in result.skipped:
                result.diagnostics.append(f"{rel}: {entry['error']}")
                result.skipped.append(rel)
            continue
        result.findings.append(
            Finding(
                rule=f"radon/mi-{entry['rank']}",
                category="quality",
                severity="low",
                confidence=Confidence.LIKELY,
                owner_repo=ctx.target.name,
                anchor=f"file:{rel}",
                locations=[Location(rel, 1)],
                group=f"file:{rel}",
                evidence=[{"kind": "mi", "detail": str(entry["mi"])}],
            )
        )
    return result


RADON = ProbeSpec(
    name="radon",
    languages=PY,
    input_mode="files",
    select=python_files,
    canary=Canary(
        ".selfcheck-canary/radon/canary.py",
        _RADON_CANARY,
        "radon/cc-D",
        "func:.selfcheck-canary/radon/canary.py::selfcheck_canary",
    ),
    rules=("cc>=D", "mi<=C"),
    binary="radon",
    version_range=((6, 0), (7, 0)),
    argv=lambda ctx: ["cc", "-j", "--min", "D", *copy_paths(ctx)],
    parse=_radon_parse,
)


# ---- deptry (roots, cwd = copy) --------------------------------------------

_DEPTRY_DEFAULT_EXCLUDE = (
    r"venv",
    r"\.venv",
    r"\.direnv",
    r"tests",
    r"\.git",
    r"setup\.py",
)
_DEPTRY_SKIP = re.compile(r"Skipping processing of (?P<path>\S+?) because")
DEP003_NOTE = "DEP003 off — tool-env packages not covered"


def _deptry_config(copy_toml: dict[str, Any]) -> tuple[list[str], list[str]]:
    section = copy_toml.get("tool", {}).get("deptry", {})
    return list(section.get("exclude", [])), list(section.get("extend_exclude", []))


def _deptry_select(target: RepoTarget) -> tuple[str, ...]:
    return (".",) if "pyproject.toml" in target.corpus else ()


def _deptry_expected(target: RepoTarget) -> list[str]:
    pyproject = target.copy / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text()) if pyproject.is_file() else {}
    exclude, extend = _deptry_config(data)
    patterns = [re.compile(p) for p in [*(exclude or _DEPTRY_DEFAULT_EXCLUDE), *extend]]
    return [p for p in python_files(target) if not any(rx.match(p) for rx in patterns)]


def _deptry_argv(ctx: ProbeCtx) -> list[str]:
    _, extend = _deptry_config(_toml(ctx, "pyproject.toml"))
    args = [
        ".",
        "--config",
        "pyproject.toml",
        "--json-output",
        str(ctx.work / "deptry.json"),
        "--ignore",
        "DEP003",
    ]
    for pattern in [*extend, ".selfcheck-canary"]:
        args += ["--extend-exclude", pattern]
    env = ctx.target.env
    if env.mode == "checkout-venv" and env.site_packages is not None:
        mapping = package_module_map(
            env.site_packages, ctx.target.copy / "pyproject.toml"
        )
        if mapping:
            args += ["--package-module-name-map", mapping]
    return args


def _deptry_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([], notes=[DEP003_NOTE])
    for match in _DEPTRY_SKIP.finditer(proc.stdout + "\n" + proc.stderr):
        rel = rel_path(ctx, match["path"])
        result.diagnostics.append(f"{rel}: skipped by deptry")
        result.skipped.append(rel)
    for item in json.loads((ctx.work / "deptry.json").read_text()):
        code = item["error"]["code"]
        rel = rel_path(ctx, item["location"]["file"])
        result.findings.append(
            line_finding(
                ctx,
                f"deptry/{code}",
                rel,
                item["location"].get("line") or 1,
                category="deps",
                severity="medium",
                message=item["error"]["message"],
                key_text=f"{code}:{item.get('module')}",
            )
        )
    return result


DEPTRY = ProbeSpec(
    name="deptry",
    languages=PY,
    input_mode="roots",
    select=_deptry_select,
    canary=Canary(
        "selfcheck_canary/__init__.py",
        "import selfcheck_canary_missing_dist\n",
        "deptry/DEP001",
        "file:selfcheck_canary/__init__.py",
    ),
    rules=("DEP001", "DEP002", "DEP004", "DEP005", "!DEP003"),
    binary="deptry",
    version_range=((0, 25), (0, 26)),
    normal_codes=frozenset({0, 1}),
    cwd="copy",
    argv=_deptry_argv,
    parse=_deptry_parse,
    expected_files=_deptry_expected,
    config_files=("pyproject.toml",),
)

PYTHON_PROBES = (RUFF, PYREFLY, VULTURE, RADON, DEPTRY)
