"""Finding model and stable identity (spec §2, §2.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class Confidence(StrEnum):
    """Confidence scale, lowest first (spec §2.3)."""

    CANDIDATE = "candidate"
    LIKELY = "likely"
    CONFIRMED = "confirmed"


_RANK = {Confidence.CANDIDATE: 0, Confidence.LIKELY: 1, Confidence.CONFIRMED: 2}


def cap(value: Confidence, limit: Confidence) -> Confidence:
    """Return the lower of ``value`` and ``limit``."""
    return value if _RANK[value] <= _RANK[limit] else limit


@dataclass(frozen=True, order=True)
class Location:
    """A path relative to the repo root and a 1-based line."""

    path: str
    line: int


@dataclass
class Finding:
    """One finding: one probe, one rule, one key (spec §2.1)."""

    rule: str
    category: str
    severity: str
    confidence: Confidence
    owner_repo: str
    anchor: str
    locations: list[Location]
    text_key: str | None = None
    group: str | None = None
    related: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    suggestion: str = ""
    judge: dict[str, Any] | None = None

    @property
    def probe(self) -> str:
        """Probe name: the rule prefix."""
        return self.rule.split("/", 1)[0]

    @property
    def occurrences(self) -> int:
        """Number of aggregated locations."""
        return len(self.locations)

    @property
    def id(self) -> str:
        """Stable identity (spec §2.1)."""
        return finding_id(self.rule, self.owner_repo, self.anchor, self.text_key)

    def to_json(self) -> dict[str, Any]:
        """Serialise for report.json."""
        return {
            "id": self.id,
            "rule": self.rule,
            "probe": self.probe,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence.value,
            "owner_repo": self.owner_repo,
            "anchor": self.anchor,
            "text_key": self.text_key,
            "group": self.group or self.anchor,
            "occurrences": self.occurrences,
            "locations": [{"path": x.path, "line": x.line} for x in self.locations],
            "related": self.related,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "judge": self.judge,
        }


def normalize_line(text: str) -> str:
    """Strip and collapse whitespace runs."""
    return " ".join(text.split())


def make_text_key(line_text: str) -> str:
    """sha1 of the normalised violating line (or key token)."""
    return hashlib.sha1(normalize_line(line_text).encode()).hexdigest()


def finding_id(rule: str, owner_repo: str, anchor: str, text_key: str | None) -> str:
    """Stable id; duplicate anchors exclude the (derived) owner."""
    if anchor.startswith("dup:"):
        raw = f"{rule}|{anchor}"
    else:
        raw = f"{rule}|{owner_repo}|{anchor}|{text_key or ''}"
    return "sc-" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def aggregate(findings: Iterable[Finding]) -> list[Finding]:
    """Merge findings sharing an id into one with all their locations."""
    merged: dict[str, Finding] = {}
    for item in findings:
        current = merged.get(item.id)
        if current is None:
            merged[item.id] = replace(
                item,
                locations=sorted(set(item.locations)),
                evidence=list(item.evidence),
                related=list(item.related),
            )
            continue
        current.locations = sorted(set(current.locations) | set(item.locations))
        current.evidence += [e for e in item.evidence if e not in current.evidence]
        current.related += [r for r in item.related if r not in current.related]
    return sorted(merged.values(), key=lambda f: f.id)
