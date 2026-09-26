"""Target environment as data only (spec §1.5)."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from selfcheck.model import Confidence, Finding, cap

IMPORT_CLASS_RULES = frozenset({"deptry/DEP001", "pyrefly/missing-import"})
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SKIP_TOPS = frozenset({"__pycache__", "bin"})


@dataclass(frozen=True)
class EnvInfo:
    """How pyrefly/deptry see the target's third-party packages."""

    mode: Literal["checkout-venv", "no-env"]
    stale: bool = False
    site_packages: Path | None = None
    python_version: str | None = None


def detect_env(repo: Path) -> EnvInfo:
    """Existing ``.venv`` of the source checkout, read as files only."""
    cfg = repo / ".venv" / "pyvenv.cfg"
    sites = sorted((repo / ".venv" / "lib").glob("python*/site-packages"))
    if not cfg.is_file() or not sites:
        return EnvInfo("no-env")
    version = None
    for line in cfg.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("version_info", "version"):
            version = ".".join(value.strip().split(".")[:2])
    lock = repo / "uv.lock"
    stale = lock.is_file() and lock.stat().st_mtime > cfg.stat().st_mtime
    return EnvInfo("checkout-venv", stale, sites[0], version)


def canonical_name(name: str) -> str:
    """PEP 503 normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_requirements(pyproject: Path) -> list[str]:
    """Requirement names exactly as written, in declaration order."""
    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    specs: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs += group
    for group in data.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    names: list[str] = []
    for spec in specs:
        match = _REQ_NAME.match(spec)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _metadata_name(dist: Path) -> str | None:
    meta = dist / "METADATA"
    if not meta.is_file():
        return None
    for line in meta.read_text(errors="ignore").splitlines():
        if not line.strip():
            return None
        if line.startswith("Name:"):
            return line.split(":", 1)[1].strip()
    return None


def _modules(dist: Path) -> tuple[str, ...]:
    top = dist / "top_level.txt"
    if top.is_file():
        mods = {m.strip() for m in top.read_text().splitlines() if m.strip()}
    else:
        mods = set()
        record = dist / "RECORD"
        for line in record.read_text().splitlines() if record.is_file() else []:
            head = line.split(",", 1)[0].split("/", 1)[0].removesuffix(".py")
            if head.isidentifier() and head not in _SKIP_TOPS:
                mods.add(head)
    return tuple(sorted(mods))


def dist_modules(site_packages: Path) -> dict[str, tuple[str, ...]]:
    """canonical distribution name → top-level modules, from dist-info files."""
    result: dict[str, tuple[str, ...]] = {}
    for dist in sorted(site_packages.glob("*.dist-info")):
        name = _metadata_name(dist)
        mods = _modules(dist)
        if name and mods:
            result[canonical_name(name)] = mods
    return result


def package_module_map(site_packages: Path, pyproject: Path) -> str:
    """deptry ``--package-module-name-map`` keyed by declaration spelling."""
    available = dist_modules(site_packages)
    parts = []
    for req in declared_requirements(pyproject):
        mods = available.get(canonical_name(req))
        if mods:
            parts.append(f"{req}={'|'.join(mods)}")
    return ",".join(parts)


def apply_env_policy(
    findings: list[Finding], env: EnvInfo
) -> tuple[list[Finding], dict[str, int]]:
    """no-env: move import-class findings out; env-stale: cap them."""
    if env.mode == "no-env":
        counts = Counter(f.rule for f in findings if f.rule in IMPORT_CLASS_RULES)
        kept = [f for f in findings if f.rule not in IMPORT_CLASS_RULES]
        return kept, dict(counts)
    if env.stale:
        for item in findings:
            if item.rule in IMPORT_CLASS_RULES:
                item.confidence = cap(item.confidence, Confidence.CANDIDATE)
                item.evidence.append({"kind": "cap", "detail": "env-stale"})
    return findings, {}
