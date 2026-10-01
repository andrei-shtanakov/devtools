"""Показатели ступени включения (спека среза 1, §10.2).

Полнота — partial / (complete + partial) по прогонам ТАЙМЕРА; доступность —
часовые слоты с прогоном таймера / слоты периода. Ручные прогоны не входят
ни туда, ни туда. Переход ступени решает владелец правкой роадмапа.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from conductor.opstate import parse_ts

MAX_PARTIAL = 0.05
MIN_AVAILABILITY = 0.90
REVIEW = frozenset(
    {"uncertain", "failed", "revoked_action", "skipped_dependent", "not_sent"}
)


def _snapshots(out: Path, since: datetime, until: datetime) -> list[dict[str, Any]]:
    snaps = []
    for path in sorted(out.glob("*/snapshot.json")) if out.is_dir() else []:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
            started = parse_ts(snap["started_at"])
        except (OSError, ValueError, KeyError):
            continue
        if since <= started < until:
            snaps.append(snap)
    return snaps


def _journal(snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        j
        for s in snaps
        for j in (s.get("actions") or {}).get("journal", [])
        if isinstance(j, dict) and "action" in j
    ]


def stage_report(out: Path, since: datetime, until: datetime) -> dict[str, Any]:
    """Показатели ступени за [since, until)."""
    snaps = _snapshots(out, since, until)
    graphs = [
        s
        for s in snaps
        if s.get("trigger") == "timer"
        and s.get("graph_state") in ("complete", "partial")
    ]
    slots = int((until - since).total_seconds() // 3600)
    covered = {
        parse_ts(s["started_at"]).replace(minute=0, second=0, microsecond=0)
        for s in graphs
    }
    partial = (
        sum(s["graph_state"] == "partial" for s in graphs) / len(graphs)
        if graphs
        else None
    )
    availability = len(covered) / slots if slots else None
    if partial is None or availability is None:
        verdict = "не проверено"
    elif partial > MAX_PARTIAL or availability < MIN_AVAILABILITY:
        verdict = "не пройдена"
    else:
        verdict = "показатели в норме"
    journal = _journal(snaps)
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "timer_runs": len(graphs),
        "partial_share": partial,
        "availability": availability,
        "verdict": verdict,
        "successes": dict(
            Counter(j["action"] for j in journal if j.get("outcome") == "success")
        ),
        "to_review": dict(
            Counter(j["outcome"] for j in journal if j.get("outcome") in REVIEW)
        ),
        "shadow_proposals": dict(
            Counter(j["action"] for j in journal if j.get("outcome") == "shadow")
        ),
    }
