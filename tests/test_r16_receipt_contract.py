"""The receipt contract (contracts/r16-receipt/v1) against what the runner writes."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
import pytest

import r16_runner as run
from tests.test_r16_runner import cfg_for

V1 = Path(__file__).resolve().parents[1] / "contracts" / "r16-receipt" / "v1"
TBILISI = ZoneInfo("Asia/Tbilisi")


def schema() -> dict:
    return json.loads((V1 / "schema.json").read_text())


def validate(record: dict) -> None:
    jsonschema.Draft202012Validator(schema()).validate(record)


def example(name: str) -> dict:
    return json.loads((V1 / "examples" / f"{name}.json").read_text())


def test_schema_is_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(schema())


@pytest.mark.parametrize("name", ["completed-ok", "failed", "missed"])
def test_examples_are_valid(name: str) -> None:
    validate(example(name))


@pytest.mark.parametrize(
    ("audit_out", "delivery"),
    [
        ('{"summary": {"unchanged": 1, "with_evidence": 1, "notes": 3}}', "not-needed"),
        ("boom", "not-needed"),
    ],
)
def test_runner_receipts_are_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_out: str, delivery: str
) -> None:
    cfg = cfg_for(tmp_path)
    (cfg.receipts / "2026-09-01.json").write_text('{"ok": true, "attempt": 1}')
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(audit_out))
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery(delivery))
    monkeypatch.setattr(run, "head_sha", lambda *_: "a" * 40)
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    written = sorted(p for p in cfg.receipts.glob("*.json") if p.stem != "2026-09-01")
    assert [p.stem for p in written] == ["2026-09-08", "2026-09-15", "2026-09-22"]
    for path in written:
        validate(json.loads(path.read_text()))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate(example("completed-ok") | {"extra": 1})


def test_unknown_execution_is_rejected() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate(example("failed") | {"execution": "partial"})


def test_producer_host_is_required() -> None:
    record = example("completed-ok")
    record["producer"] = {"runner": "a" * 40, "auditor": "b" * 40}
    with pytest.raises(jsonschema.ValidationError):
        validate(record)


def test_pre_contract_mac_receipt_is_distinguishable_and_still_read(
    tmp_path: Path,
) -> None:
    """The real Mac receipt of cycle 2026-09-22 (pre-contract): no producer.host.

    It is not v1-valid — the README names that — yet the runner still reads it:
    the cycle counts as done.
    """
    mac = {
        "schema_version": 1,
        "check_id": "r16-kb-freshness",
        "started_at": "2026-09-23T19:01:41",
        "finished_at": "2026-09-23T19:02:10",
        "target": "published",
        "execution": "completed",
        "revisions": {"steward": "a" * 40},
        "coverage": {"notes": 10, "with_evidence": 1, "unparsed_frontmatter": 0},
        "statuses": {"unchanged": 1},
        "problems": {"claims": 0, "revisions": 0, "coverage": 0},
        "delivery": {"action": "not-needed", "issue": None, "issue_url": None,
                     "error": None},
        "audit_tail": None,
        "ok": True,
        "cycle_id": "2026-09-22",
        "attempt": 1,
        "producer": {"runner": "b" * 40, "auditor": "c" * 40},
    }
    with pytest.raises(jsonschema.ValidationError):
        validate(mac)
    cfg = cfg_for(tmp_path)
    (cfg.receipts / "2026-09-22.json").write_text(json.dumps(mac))
    assert run.decide(run.load_receipt(cfg.receipts, "2026-09-22")) == "done"


def test_pre_contract_rule_does_not_exempt_missed_receipts() -> None:
    """Every valid `missed` receipt lacks producer.host by schema (review #399).

    So the pre-contract discriminator must be "an executed attempt without
    producer.host", never "any file without producer.host".
    """
    readme = (V1 / "README.md").read_text()
    section = readme.split("## Квитанции до контракта", 1)[1].split("## ", 1)[0]
    assert "`execution: completed | failed`" in section
    assert "`missed`" in section and "проверяются схемой всегда" in section
    assert "от файлов без `producer.host`" not in section


def test_mac_missed_receipt_is_v1_valid() -> None:
    """The Mac runner's `missed` form (naive noted_at) already satisfies v1."""
    validate(run.missed_receipt("2026-09-15", "2026-09-23T17:00:00"))
