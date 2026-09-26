"""Probe contract: statuses, canary, coverage, input forms (spec §4.1–4.2)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from selfcheck import __version__
from selfcheck.env import EnvInfo
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.common import config_hash
from selfcheck.roles import Role, glob_match, role_of


@dataclass(frozen=True)
class Canary:
    """A file with one known finding (rule + anchor), fed with the repo.

    ``expect_anchor`` is a glob (``*`` within a segment): a clone anchor is a
    hash of the fragment the tool cuts, unknown in advance.
    """

    relpath: str
    content: str
    expect_rule: str
    expect_anchor: str


@dataclass(frozen=True)
class RepoTarget:
    """One repo as the probes see it: the read-only copy plus metadata."""

    name: str
    source: Path
    copy: Path
    languages: frozenset[str]
    corpus: tuple[str, ...]
    env: EnvInfo
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    corpus_exclude: tuple[str, ...] = ()
    sched_dir: Path | None = None
    fleet: str = "absent"
    now: float = 0.0


@dataclass(frozen=True)
class ProbeCtx:
    """What one probe invocation works with."""

    target: RepoTarget
    work: Path
    inputs: tuple[str, ...]
    cwd: Path
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


@dataclass
class ParseResult:
    """Normalised output of a tool or analyzer."""

    findings: list[Finding]
    processed_paths: list[str] | None = None
    skipped: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class ProbeStatus(StrEnum):
    """Probe outcome (spec §4.2)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


@dataclass
class ProbeResult:
    """One row of the probe summary table."""

    probe: str
    repo: str
    status: ProbeStatus
    reason: str = ""
    tool_version: str | None = None
    argv: list[str] = field(default_factory=list)
    exit_code: int | None = None
    canary: str | None = None
    coverage: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    rules: tuple[str, ...] = ()
    duration: float = 0.0
    config_hash: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialise without findings (they are reported separately)."""
        return {
            "probe": self.probe,
            "repo": self.repo,
            "status": self.status.value,
            "reason": self.reason,
            "tool_version": self.tool_version,
            "argv": self.argv,
            "exit_code": self.exit_code,
            "canary": self.canary,
            "coverage": self.coverage,
            "diagnostics": self.diagnostics,
            "findings": len(self.findings),
            "rules": list(self.rules),
            "duration": self.duration,
            "config_hash": self.config_hash,
        }


def _never(ctx: ProbeCtx) -> bool:
    return False


@dataclass(frozen=True)
class ProbeSpec:
    """Registry entry for one probe (spec §4.1)."""

    name: str
    languages: frozenset[str]
    input_mode: Literal["files", "roots"]
    select: Callable[[RepoTarget], tuple[str, ...]]
    canary: Canary
    coverage: Literal["reported", "declared"] = "declared"
    executes_target_code: bool = False
    rules: tuple[str, ...] = ()
    logic_version: int = 0
    binary: str | None = None
    version_args: tuple[str, ...] = ("--version",)
    version_range: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    version_timeout: int = 60
    normal_codes: frozenset[int] = frozenset({0})
    cwd: Literal["work", "copy"] = "work"
    argv: Callable[[ProbeCtx], list[str]] | None = None
    parse: (
        Callable[[ProbeCtx, subprocess.CompletedProcess[str]], ParseResult] | None
    ) = None
    analyze: Callable[[ProbeCtx], ParseResult] | None = None
    config_suppresses: Callable[[ProbeCtx], bool] = _never
    expected_files: Callable[[RepoTarget], list[str]] | None = None
    config_files: tuple[str, ...] = ()
    timeout: int = 900


def canary_files(specs: Iterable[ProbeSpec]) -> dict[str, str]:
    """relpath → content of every canary, for materialisation."""
    return {s.canary.relpath: s.canary.content for s in specs}


def parse_version(text: str) -> tuple[int, ...] | None:
    """First dotted version number in ``text``."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if match is None:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


class _Stop(Exception):
    def __init__(self, status: ProbeStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def _external(
    spec: ProbeSpec, ctx: ProbeCtx, result: ProbeResult, runner: Runner, which: Which
) -> ParseResult:
    if not (spec.binary and spec.argv and spec.parse and spec.version_range):
        raise _Stop(ProbeStatus.FAILED, "registry: incomplete external probe")
    binary = which(spec.binary)
    if binary is None:
        raise _Stop(ProbeStatus.UNAVAILABLE, f"{spec.binary} not found")
    try:
        ver = runner(
            [binary, *spec.version_args],
            capture_output=True,
            text=True,
            timeout=spec.version_timeout,
            cwd=ctx.work,
        )
    except subprocess.TimeoutExpired as exc:
        raise _Stop(ProbeStatus.FAILED, "timeout") from exc
    version = parse_version(ver.stdout + ver.stderr)
    low, high = spec.version_range
    if version is None or not low <= version < high:
        shown = ".".join(map(str, version)) if version else "unknown"
        raise _Stop(ProbeStatus.UNAVAILABLE, f"version {shown} outside [{low}, {high})")
    result.tool_version = ".".join(map(str, version))
    result.argv = [binary, *spec.argv(ctx)]
    try:
        proc = runner(
            result.argv,
            cwd=ctx.cwd,
            capture_output=True,
            text=True,
            timeout=spec.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _Stop(ProbeStatus.FAILED, "timeout") from exc
    result.exit_code = proc.returncode
    if proc.returncode not in spec.normal_codes:
        wrote = "Permission denied" in proc.stderr or "EACCES" in proc.stderr
        kind = "write-to-corpus" if wrote else "exit-code"
        tail = proc.stderr.strip()[-300:]
        raise _Stop(ProbeStatus.FAILED, f"{kind}: {proc.returncode}: {tail}")
    try:
        return spec.parse(ctx, proc)
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        raise _Stop(ProbeStatus.FAILED, f"unparsable: {exc}") from exc


def _internal(spec: ProbeSpec, ctx: ProbeCtx, result: ProbeResult) -> ParseResult:
    assert spec.analyze is not None
    result.tool_version = f"selfcheck {__version__}/logic {spec.logic_version}"
    try:
        return spec.analyze(ctx)
    except Exception as exc:  # an analyzer bug is a probe failure
        raise _Stop(ProbeStatus.FAILED, f"analyzer-error: {exc!r}") from exc


def _analyzer_config_hash(target: RepoTarget) -> str:
    material = {"roles": target.roles, "corpus": list(target.corpus_exclude)}
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _is_canary(finding: Finding) -> bool:
    return any(role_of(loc.path) is Role.CANARY for loc in finding.locations)


def _judge(
    spec: ProbeSpec,
    ctx: ProbeCtx,
    result: ProbeResult,
    parsed: ParseResult,
    passed: list[str],
) -> None:
    result.diagnostics = parsed.diagnostics
    result.extra = parsed.extra
    result.coverage["notes"] = list(parsed.notes)
    canary = [f for f in parsed.findings if _is_canary(f)]
    result.findings = [f for f in parsed.findings if not _is_canary(f)]
    hit = any(
        f.rule == spec.canary.expect_rule
        and glob_match(spec.canary.expect_anchor, f.anchor)
        for f in canary
    )
    result.canary = "hit" if hit else "missed"
    if not hit:
        reason = (
            "canary-suppressed-by-config"
            if spec.config_suppresses(ctx)
            else "canary-missed"
        )
        raise _Stop(ProbeStatus.FAILED, reason)
    if spec.coverage == "reported":
        processed = set(parsed.processed_paths or [])
        result.coverage["processed"] = len(processed & set(passed))
        result.coverage["unprocessed"] = [p for p in passed if p not in processed]
        if passed and not processed & set(passed):
            raise _Stop(ProbeStatus.FAILED, "zero-processed")
    result.coverage["skipped"] = list(parsed.skipped)
    if parsed.skipped or parsed.diagnostics or result.coverage.get("unprocessed"):
        raise _Stop(ProbeStatus.PARTIAL, "per-file problems")
    result.status = ProbeStatus.OK


def run_probe(
    spec: ProbeSpec,
    target: RepoTarget,
    work_root: Path,
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> ProbeResult:
    """Run one probe on one repo and classify the outcome (spec §4.2).

    Relative paths violate a precondition (ValueError); every other failure
    becomes the probe's status.
    """
    if not target.copy.is_absolute() or not work_root.is_absolute():
        raise ValueError(f"run_probe needs absolute paths: {target.copy}, {work_root}")
    started = time.monotonic()
    result = ProbeResult(
        probe=spec.name, repo=target.name, status=ProbeStatus.SKIPPED, rules=spec.rules
    )
    try:
        if "any" not in spec.languages and not spec.languages & target.languages:
            raise _Stop(ProbeStatus.SKIPPED, "language")
        try:
            inputs = list(spec.select(target))
        except Exception as exc:  # a selection bug is a probe failure
            raise _Stop(ProbeStatus.FAILED, f"select-error: {exc!r}") from exc
        if not inputs:
            raise _Stop(ProbeStatus.SKIPPED, "no-inputs")
        work = work_root / spec.name / target.name
        work.mkdir(parents=True)
        canary = [spec.canary.relpath] if spec.input_mode == "files" else []
        cwd = target.copy if spec.cwd == "copy" else work
        ctx = ProbeCtx(target, work, (*inputs, *canary), cwd, runner)
        result.coverage = {
            "mode": spec.coverage,
            "input_mode": spec.input_mode,
            "passed": inputs,
        }
        if spec.expected_files is not None:
            result.coverage["expected_files"] = list(spec.expected_files(target))
        if spec.analyze is not None:
            result.config_hash = _analyzer_config_hash(target)
            parsed = _internal(spec, ctx, result)
        else:
            result.config_hash = config_hash(target.copy, spec.config_files)
            parsed = _external(spec, ctx, result, runner, which)
        _judge(spec, ctx, result, parsed, inputs)
    except _Stop as stop:
        result.status, result.reason = stop.status, stop.reason
    except Exception as exc:  # noqa: BLE001 — any adapter/tool failure is this probe's status
        result.status = ProbeStatus.FAILED
        result.reason = f"adapter-error: {exc!r}"[:500]
    result.duration = round(time.monotonic() - started, 3)
    return result


_INSTRUMENT = {
    ProbeStatus.FAILED: ("selfcheck/probe-failed", "high"),
    ProbeStatus.PARTIAL: ("selfcheck/probe-partial", "medium"),
    ProbeStatus.UNAVAILABLE: ("selfcheck/probe-unavailable", "medium"),
}


def instrument_findings(results: Iterable[ProbeResult]) -> list[Finding]:
    """Findings about the instrument itself (spec §4.2)."""
    out: list[Finding] = []
    for res in results:
        if res.status not in _INSTRUMENT:
            continue
        rule, severity = _INSTRUMENT[res.status]
        out.append(
            Finding(
                rule=rule,
                category="selfcheck",
                severity=severity,
                confidence=Confidence.CONFIRMED,
                owner_repo=res.repo,
                anchor=f"probe:{res.repo}#{res.probe}",
                locations=[Location(f"probe:{res.probe}", 1)],
                text_key=None,
                evidence=[{"kind": "reason", "detail": res.reason}],
                suggestion="починить прибор или окружение пробы",
            )
        )
    return out
