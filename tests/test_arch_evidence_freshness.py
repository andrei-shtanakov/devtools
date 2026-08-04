# tests/test_arch_evidence_freshness.py
from __future__ import annotations

import json
from datetime import datetime, timezone

from .arch_freshness_fixtures import (
    EVIDENCE_DIR, Workspace, git, make_workspace, upstream_change,
)

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def test_fixture_builds_polyrepo_workspace(tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    assert (ws.prograph / "contracts/intended-graph/v1/schema.json").is_file()
    assert (ws.steward / "contracts/prograph-intended-graph/v1/PIN").is_file()
    report = json.loads(
        (ws.steward / EVIDENCE_DIR / "conformance-report.json").read_text()
    )
    assert report["snapshot"]["indexed_at"] == "2026-08-04T11:00:00Z"
    upstream_change(
        ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "mutate"
    )
    # canon получил новый коммит, локальный клон — ещё нет
    assert git(ws.seed, "rev-parse", "HEAD") != git(ws.prograph, "rev-parse", "HEAD")


def test_parse_pin_reads_key_value_lines(sensor):
    pin = sensor.parse_pin(
        "source: prograph@8deb730 contracts/intended-graph/v1/schema.json\n"
        "sha256: abc\nvendored: 2026-08-03\npurpose: x\n"
    )
    assert pin["source"].startswith("prograph@8deb730")
    assert pin["sha256"] == "abc"


def test_parse_iso_handles_zulu(sensor):
    dt = sensor.parse_iso("2026-08-04T12:00:00Z")
    assert dt.tzinfo is not None
    assert sensor.iso(dt) == "2026-08-04T12:00:00Z"
