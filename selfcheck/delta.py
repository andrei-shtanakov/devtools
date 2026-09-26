"""Delta against a baseline run: fates and statuses (spec §4.3)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from selfcheck.probes.base import ProbeResult, ProbeStatus

SKIPPED_KEY = "skipped"


@dataclass
class RunSnapshot:
    """What a later run needs to judge disappearances."""

    run_id: str
    scope: list[str]
    materialized: list[str]
    probe_keys: dict[str, str | None]
    corpus: dict[str, list[str]]
    sources: dict[str, str]
    findings: dict[str, dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        """Serialise for report.json."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunSnapshot:
        """Restore from report.json."""
        return cls(**data)


def comparability_key(
    result: ProbeResult,
    *,
    env_mode: str,
    surface: dict[str, Any] | None,
    run_dir: str,
) -> str | None:
    """Key of an ``ok`` probe run; ``None`` otherwise (spec §4.3)."""
    if result.status is not ProbeStatus.OK:
        return None
    options = [a for a in result.argv[1:] if not a.startswith(run_dir)]
    material = [
        result.probe,
        result.tool_version,
        result.config_hash,
        list(result.rules),
        options,
        env_mode,
        surface,
    ]
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()


class Fate(StrEnum):
    """What happened to a baseline defining file / participant."""

    CHECKED = "checked"
    DELETED = "deleted"
    EXCLUDED = "excluded"
    OUT_OF_SCOPE = "out-of-scope"
    UNVERIFIED = "unverified"


def fate(cur: RunSnapshot, base: RunSnapshot, repo: str, path: str, probe: str) -> Fate:
    """Fate of a baseline file in the current run (order matters, spec §4.3)."""
    if repo not in cur.scope:
        return Fate.OUT_OF_SCOPE
    if repo not in cur.materialized:
        return Fate.UNVERIFIED
    in_corpus = path in cur.corpus.get(repo, [])
    source = cur.sources.get(repo)
    if not in_corpus and source and not (Path(source) / path).exists():
        return Fate.DELETED
    if not in_corpus:
        return Fate.EXCLUDED
    key = f"{probe}@{repo}"
    current = cur.probe_keys.get(key)
    if current in (None, SKIPPED_KEY) or current != base.probe_keys.get(key):
        return Fate.UNVERIFIED
    return Fate.CHECKED


def _defining_path(anchor: str) -> str:
    body = anchor.split(":", 1)[1]
    return body.split("::", 1)[0].split("#", 1)[0]


def _member(r: dict[str, Any]) -> tuple[str, str, str]:
    return (r["owner_repo"], r["path"], str(r.get("member", r.get("line", ""))))


def _changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    def paths(item: dict[str, Any]) -> set[str]:
        return {loc["path"] for loc in item["locations"]}

    def members(item: dict[str, Any]) -> set[tuple[str, str, str]]:
        return {_member(r) for r in item.get("related", []) if not r.get("unverified")}

    return (
        old["occurrences"] != new["occurrences"]
        or paths(old) != paths(new)
        or members(old) != members(new)
    )


def _instrument_status(cur: RunSnapshot, anchor: str) -> str:
    repo, _, probe = anchor.removeprefix("probe:").partition("#")
    ran = cur.probe_keys.get(f"{probe}@{repo}") is not None
    return "resolved" if repo in cur.scope and ran else "not-rechecked"


def _gone_status(base: RunSnapshot, cur: RunSnapshot, item: dict[str, Any]) -> str:
    anchor = item["anchor"]
    if anchor.startswith("probe:"):
        return _instrument_status(cur, anchor)
    probe = item["probe"]
    if anchor.startswith("dup:"):
        fates = [
            fate(cur, base, r["owner_repo"], r["path"], probe)
            for r in item.get("related", [])
        ]
    else:
        fates = [fate(cur, base, item["owner_repo"], _defining_path(anchor), probe)]
    if fates and all(f is Fate.DELETED for f in fates):
        return "resolved: file-removed"
    if fates and all(f in (Fate.CHECKED, Fate.DELETED) for f in fates):
        return "resolved"
    return "not-rechecked"


def _mark_unverified(
    base: RunSnapshot, cur: RunSnapshot, old: dict[str, Any], new: dict[str, Any]
) -> None:
    present = {_member(r) for r in new.get("related", [])}
    for r in old.get("related", []):
        if _member(r) in present:
            continue
        if fate(cur, base, r["owner_repo"], r["path"], old["probe"]) in (
            Fate.CHECKED,
            Fate.DELETED,
        ):
            continue
        new.setdefault("related", []).append({**r, "unverified": True})


def compute_delta(
    base: RunSnapshot | None, cur: RunSnapshot
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Statuses of current findings and of disappeared baseline findings."""
    if base is None:
        return {fid: "new" for fid in cur.findings}, []
    statuses: dict[str, str] = {}
    for fid, item in cur.findings.items():
        old = base.findings.get(fid)
        if old is None:
            statuses[fid] = "new"
            continue
        statuses[fid] = "changed" if _changed(old, item) else "persisting"
        if item["anchor"].startswith("dup:"):
            _mark_unverified(base, cur, old, item)
    gone = [
        {"id": fid, "status": _gone_status(base, cur, item), "anchor": item["anchor"]}
        for fid, item in base.findings.items()
        if fid not in cur.findings
    ]
    return statuses, gone
