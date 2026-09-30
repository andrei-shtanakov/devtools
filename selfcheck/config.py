"""selfcheck.toml: allowlist, roles, corpus exclusions (spec §1.4, §2.4)."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import ROLE_NAMES, glob_match


class ConfigError(ValueError):
    """Invalid manifest or config — exit code 4."""


@dataclass(frozen=True)
class AllowEntry:
    """One allowlist record; reason and until are mandatory."""

    reason: str
    until: date
    id: str | None = None
    anchor: str | None = None
    repo: str | None = None  # limits the entry to one owner_repo (§10.8)
    # class form: a noise class, not one finding — `rule` glob over the rule
    # name, optional `path` glob that EVERY location must match (a clone
    # spanning tests and code is not wholly in a tests-only class)
    rule: str | None = None
    path: str | None = None

    @property
    def label(self) -> str:
        """What the entry names — the tail of its `allow:` anchor."""
        if self.rule is not None:
            return f"rule={self.rule}" + (f"@{self.path}" if self.path else "")
        return str(self.id or self.anchor)

    def matches(self, finding: Finding) -> bool:
        """id equality, anchor equality (``file:P`` covers anchors of P), or
        the class form: rule glob plus, if set, a path glob on every location."""
        if self.repo is not None and finding.owner_repo != self.repo:
            return False
        if self.rule is not None:
            if not glob_match(self.rule, finding.rule):
                return False
            if self.path is None:
                return True
            paths = [loc.path for loc in finding.locations]
            return bool(paths) and all(glob_match(self.path, p) for p in paths)
        if self.id is not None:
            return finding.id == self.id
        if self.anchor is None:
            return False
        if finding.anchor == self.anchor:
            return True
        if not self.anchor.startswith("file:"):
            return False
        path = self.anchor.removeprefix("file:")
        if finding.anchor == f"llm:{path}":  # a shell site has no qualname (#411)
            return True
        return any(
            finding.anchor.startswith(f"{kind}:{path}::") for kind in ("func", "llm")
        )


@dataclass(frozen=True)
class OperatorEntry:
    """A manual entry point (spec §3.2.5): a root of its repo."""

    repo: str
    path: str
    reason: str


@dataclass(frozen=True)
class Config:
    """Parsed selfcheck.toml."""

    allow: tuple[AllowEntry, ...] = ()
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    corpus_exclude: tuple[str, ...] = ()
    operator: tuple[OperatorEntry, ...] = ()
    sha1: str = hashlib.sha1(b"").hexdigest()


def _allow_entry(index: int, raw: dict[str, Any]) -> AllowEntry:
    missing = [k for k in ("reason", "until") if not raw.get(k)]
    forms = [k for k in ("id", "anchor", "rule") if raw.get(k)]
    if not forms:
        missing.append("id|anchor|rule")
    if missing:
        raise ConfigError(f"[[allow]] #{index}: missing {', '.join(missing)}")
    if len(forms) > 1:
        raise ConfigError(f"[[allow]] #{index}: one of id|anchor|rule, got {forms}")
    for key in ("rule", "path"):
        # an empty glob matches no real path — inert, like `repo = ""`
        # (#437.5); a non-string one would crash matching outside exit 4
        if key in raw and not (isinstance(raw[key], str) and raw[key]):
            raise ConfigError(f"[[allow]] #{index}: {key} must be a non-empty string")
    if raw.get("path") and not raw.get("rule"):
        raise ConfigError(f"[[allow]] #{index}: path needs rule (the class form)")
    if "repo" in raw and not (isinstance(raw["repo"], str) and raw["repo"]):
        # an empty repo limits the entry to no repo at all — inert, not a
        # wildcard (#437.5); omit the key to match every repo
        raise ConfigError(f"[[allow]] #{index}: repo must be a non-empty string")
    until = raw["until"]
    if not isinstance(until, date):
        raise ConfigError(f"[[allow]] #{index}: until must be a TOML date")
    return AllowEntry(
        reason=str(raw["reason"]),
        until=until,
        id=raw.get("id"),
        anchor=raw.get("anchor"),
        repo=raw.get("repo"),
        rule=raw.get("rule"),
        path=raw.get("path"),
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
    allow = tuple(_allow_entry(i, e) for i, e in enumerate(_tables(data, "allow")))
    exclude = tuple(data.get("corpus", {}).get("exclude", []))
    operator = tuple(
        _operator_entry(i, e) for i, e in enumerate(_tables(data, "operator"))
    )
    return Config(allow, roles, exclude, operator, hashlib.sha1(raw).hexdigest())


def _tables(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """``[[key]]`` entries; any other shape (``[key]``, a scalar) is exit 4."""
    raw = data.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(e, dict) for e in raw):
        raise ConfigError(f"{key}: expected an array of tables [[{key}]]")
    return raw


def _operator_entry(index: int, raw: dict[str, Any]) -> OperatorEntry:
    missing = [k for k in ("repo", "path", "reason") if not raw.get(k)]
    if missing:
        raise ConfigError(f"[[operator]] #{index}: missing {', '.join(missing)}")
    return OperatorEntry(str(raw["repo"]), str(raw["path"]), str(raw["reason"]))


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
            anchor=f"allow:{a.label}",
            locations=[Location("selfcheck.toml", 1)],
            evidence=[{"kind": "until", "detail": a.until.isoformat()}],
            suggestion="продлить с новой причиной или удалить запись",
        )
        for a in config.allow
        if a.until < today
    ]
    return AllowResult(kept, suppressed, expired)
