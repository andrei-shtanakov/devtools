"""selfcheck.toml: allowlist, roles, corpus exclusions (spec §1.4, §2.4)."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import ROLE_NAMES


class ConfigError(ValueError):
    """Invalid manifest or config — exit code 4."""


@dataclass(frozen=True)
class AllowEntry:
    """One allowlist record; reason and until are mandatory."""

    reason: str
    until: date
    id: str | None = None
    anchor: str | None = None

    def matches(self, finding: Finding) -> bool:
        """id equality, or anchor equality; ``file:P`` covers anchors of P."""
        if self.id is not None:
            return finding.id == self.id
        if self.anchor is None:
            return False
        if finding.anchor == self.anchor:
            return True
        if not self.anchor.startswith("file:"):
            return False
        path = self.anchor.removeprefix("file:")
        return any(
            finding.anchor.startswith(f"{kind}:{path}::") for kind in ("func", "llm")
        )


@dataclass(frozen=True)
class Config:
    """Parsed selfcheck.toml."""

    allow: tuple[AllowEntry, ...] = ()
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    corpus_exclude: tuple[str, ...] = ()
    sha1: str = hashlib.sha1(b"").hexdigest()


def _allow_entry(index: int, raw: dict[str, Any]) -> AllowEntry:
    missing = [k for k in ("reason", "until") if not raw.get(k)]
    if not (raw.get("id") or raw.get("anchor")):
        missing.append("id|anchor")
    if missing:
        raise ConfigError(f"[[allow]] #{index}: missing {', '.join(missing)}")
    until = raw["until"]
    if not isinstance(until, date):
        raise ConfigError(f"[[allow]] #{index}: until must be a TOML date")
    return AllowEntry(
        reason=str(raw["reason"]),
        until=until,
        id=raw.get("id"),
        anchor=raw.get("anchor"),
    )


def load_config(path: Path) -> Config:
    """Load ``path``; a missing file is an empty config (sha1 of b"")."""
    if not path.exists():
        return Config()
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    roles = {k: tuple(v) for k, v in data.get("roles", {}).items()}
    unknown = set(roles) - ROLE_NAMES
    if unknown:
        raise ConfigError(f"[roles]: unknown roles {sorted(unknown)}")
    allow = tuple(_allow_entry(i, e) for i, e in enumerate(data.get("allow", [])))
    exclude = tuple(data.get("corpus", {}).get("exclude", []))
    return Config(allow, roles, exclude, hashlib.sha1(raw).hexdigest())


@dataclass
class AllowResult:
    """Findings split by the allowlist, plus expired-entry findings."""

    kept: list[Finding]
    suppressed: list[Finding]
    expired: list[Finding]


def apply_allowlist(
    findings: list[Finding], config: Config, today: date
) -> AllowResult:
    """Suppress allowed findings; expired entries become findings."""
    active = [a for a in config.allow if a.until >= today]
    kept: list[Finding] = []
    suppressed: list[Finding] = []
    for item in findings:
        target = suppressed if any(a.matches(item) for a in active) else kept
        target.append(item)
    expired = [
        Finding(
            rule="selfcheck/allow-expired",
            category="selfcheck",
            severity="medium",
            confidence=Confidence.CONFIRMED,
            owner_repo="devtools",
            anchor=f"allow:{a.id or a.anchor}",
            locations=[Location("selfcheck.toml", 1)],
            evidence=[{"kind": "until", "detail": a.until.isoformat()}],
            suggestion="продлить с новой причиной или удалить запись",
        )
        for a in config.allow
        if a.until < today
    ]
    return AllowResult(kept, suppressed, expired)
