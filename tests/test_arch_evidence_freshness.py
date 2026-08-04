# tests/test_arch_evidence_freshness.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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


def test_resolve_upstream_reports_moving_default_branch(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "move")
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert finding is None
    # видит именно canon-HEAD, а не отставший локальный чекаут
    assert up["head_sha"] == git(ws.seed, "rev-parse", "HEAD")
    assert up["default_branch"] == "master"
    assert sensor.upstream_bytes(
        ws.prograph, up["head_sha"], "contracts/intended-graph/v1/schema.json"
    ) == b"{}\n"
    assert sensor.upstream_ls(
        ws.prograph, up["head_sha"], "contracts/intended-graph/v1"
    ) == ["schema.json"]


def test_resolve_upstream_unavailable_when_remote_gone(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    import shutil
    shutil.rmtree(ws.canon)
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert up is None
    assert finding.cls == "unavailable"
    assert "prograph" in finding.check


def _resolved(sensor, ws):
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert finding is None
    return up


def test_vendored_clean_when_copies_match_upstream(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    findings, pins = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert findings == []
    assert set(pins) == {"intended-graph", "conformance-report"}
    assert pins["intended-graph"]["source"].startswith("prograph@")


def test_vendored_drift_when_upstream_schema_changed(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(
        ws, "contracts/intended-graph/v1/schema.json", b'{"v": 2}\n', "evolve"
    )
    findings, _ = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert [f.cls for f in findings] == ["drift"]
    assert findings[0].check == "schema-drift:intended-graph"


def test_vendored_drift_when_upstream_adds_file_to_surface(sensor, tmp_path):
    # added-under-excluded-name: файл сверх нашей копии не выпадает молча
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(
        ws, "contracts/conformance-report/v1/examples.json", b"[]\n", "add file"
    )
    findings, _ = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert [f.cls for f in findings] == ["drift"]
    assert findings[0].check == "surface:conformance-report"
    assert "examples.json" in findings[0].detail


def test_evidence_fresh_is_clean(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=1)
    assert sensor.check_evidence(ws.root, NOW, 30) == []


def test_evidence_stale_by_age(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=31 * 24)
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "evidence-age:conformance-report"


def test_evidence_stale_when_manifest_newer_than_report(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=1)
    manifest = ws.steward / EVIDENCE_DIR / "intended-graph.yaml"
    manifest.write_text("components: [{id: new}]\n")
    git(ws.steward, "add", "-A")
    git(ws.steward, "commit", "-m", "manifest evolves")
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "manifest-newer:intended-graph.yaml"


def test_evidence_missing_report_is_stale(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    (ws.steward / EVIDENCE_DIR / "conformance-report.json").unlink()
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "evidence-missing:conformance-report"
