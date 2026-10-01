"""Показатели ступени (спека среза 1, §10.2)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conductor.stage_report import stage_report

T0 = datetime(2026, 10, 1, tzinfo=UTC)
H = timedelta(hours=1)


def _run(
    out: Path, at: datetime, state: str, trigger: str = "timer", journal=None
) -> None:
    d = out / at.strftime("%Y-%m-%dT%H%M%SZ")
    d.mkdir(parents=True)
    snap = {
        "started_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "graph_state": state,
        "trigger": trigger,
        "actions": {"plan": [], "journal": journal or []},
    }
    (d / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")


def test_owner_example_72_slots_fails(tmp_path: Path) -> None:
    for i in range(3):
        _run(tmp_path, T0 + i * H, "partial")
    _run(
        tmp_path,
        T0 + 3 * H,
        "complete",
        journal=[{"action": "owner_queue", "outcome": "success"}],
    )
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["partial_share"] == 0.75 and rep["availability"] == 4 / 72
    assert rep["verdict"] == "не пройдена"


def test_manual_runs_do_not_help(tmp_path: Path) -> None:
    _run(tmp_path, T0, "complete")
    for i in range(1, 72):
        _run(tmp_path, T0 + i * H + timedelta(minutes=5), "complete", trigger="manual")
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["availability"] == 1 / 72 and rep["verdict"] == "не пройдена"


def test_healthy_stage_and_counts(tmp_path: Path) -> None:
    for i in range(72):
        journal = [{"action": "nudge", "outcome": "success"}] if i == 5 else []
        if i == 6:
            journal = [{"action": "nudge", "outcome": "uncertain"}]
        _run(tmp_path, T0 + i * H, "complete", journal=journal)
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["verdict"] == "показатели в норме"
    assert rep["successes"] == {"nudge": 1} and rep["to_review"] == {"uncertain": 1}


def test_empty_is_not_verified(tmp_path: Path) -> None:
    assert stage_report(tmp_path, T0, T0 + 72 * H)["verdict"] == "не проверено"
