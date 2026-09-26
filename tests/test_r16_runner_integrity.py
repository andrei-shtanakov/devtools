"""Output integrity of the R16 runner (TODO @id:r16-runner-output-integrity).

Three holes carried over verbatim from the dev-scratch runner (review #399):
an auditor summary that disagrees with its verdict lines was read as a clean
week; an exception after `decide() == run` left no receipt; `statuses` passed
unknown summary keys into the closed v1 schema.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import r16_runner as run
from tests.test_r16_receipt_contract import validate
from tests.test_r16_runner import TBILISI, cfg_for, verdict

NOW = datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI)
CYCLE = "2026-09-22"


def summary_record(**counts: int) -> dict:
    """A summary line as kb_freshness --json prints it: five statuses + coverage."""
    base = dict.fromkeys(("unchanged", "changed", "missing", "unverified", "invalid"), 0)
    base |= {"notes": 214, "with_evidence": 4, "unparsed_frontmatter": 5}
    return {"summary": base | counts}


def jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


# The live auditor's shape on 2026-09-26: one verdict per claim, counts agree.
LIVE = jsonl(
    *[verdict("unchanged", f"u{i}") for i in range(9)],
    verdict("changed", "c1"),
    verdict("missing", "m1"),
    verdict("missing", "m2"),
    summary_record(unchanged=9, changed=1, missing=2),
)


def run_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audit_out: str,
    now: datetime = NOW,
) -> tuple[int, dict, list[str]]:
    """One real attempt with the audit output given; returns (code, receipt, calls)."""
    cfg = cfg_for(tmp_path)
    calls: list[str] = []

    def deliver(*_a: object) -> "run.Delivery":
        calls.append("deliver")
        return run.Delivery("closed", 7)

    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(audit_out))
    monkeypatch.setattr(run, "deliver", deliver)
    monkeypatch.setattr(run, "head_sha", lambda *_: "a" * 40)
    code = run.run_cycle(cfg, now, dry_run=False)
    cid = run.cycle_start(now).date().isoformat()
    record = json.loads((cfg.receipts / f"{cid}.json").read_text())
    return code, record, calls


# --- 1. inconsistent auditor output is `failed`, not a clean week -------------


def test_live_shape_is_consistent_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run.audit_inconsistency(run.parse_audit(LIVE)) is None
    code, record, calls = run_once(tmp_path, monkeypatch, LIVE)
    assert (record["execution"], calls) == ("completed", ["deliver"])
    validate(record)


def test_summary_with_findings_but_no_verdicts_does_not_close_the_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review scenario: summary says changed=2, no verdict line arrived."""
    out = jsonl(summary_record(unchanged=0, changed=2))
    code, record, calls = run_once(tmp_path, monkeypatch, out)
    assert code == 1
    assert calls == []  # the open issue is not touched, let alone closed
    assert (record["execution"], record["delivery"]["action"]) == ("failed", "skipped")
    assert "changed: summary 2 vs 0 verdicts" in record["audit_tail"]
    validate(record)


@pytest.mark.parametrize(
    ("out", "reason"),
    [
        (jsonl(summary_record(stale=1)), "unknown summary keys: stale"),
        (
            jsonl(verdict("drifted", "d"), summary_record(unchanged=0)),
            "unknown verdict statuses: drifted",
        ),
    ],
    ids=["unknown-summary-key", "unknown-verdict-status"],
)
def test_unknown_status_is_a_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, out: str, reason: str
) -> None:
    code, record, calls = run_once(tmp_path, monkeypatch, out)
    assert (code, calls, record["execution"]) == (1, [], "failed")
    assert reason in record["audit_tail"]
    validate(record)


# --- 2. an exception after decide()==run still leaves a receipt ---------------


@pytest.mark.parametrize(
    ("where", "action", "says"),
    [
        ("sync_vault", "skipped", "runner raised before delivery"),
        ("run_audit", "skipped", "runner raised before delivery"),
        ("deliver", "failed", "delivery state unknown"),
    ],
)
def test_exception_leaves_a_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    action: str,
    says: str,
) -> None:
    """Only a crash inside delivery leaves the issue's state unknown (review #401)."""
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(LIVE))
    monkeypatch.setattr(run, "deliver", lambda *_a: run.Delivery("updated", 7))
    monkeypatch.setattr(run, "head_sha", lambda *_: "a" * 40)

    def boom(*_a: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(run, where, boom)
    code = run.run_cycle(cfg, NOW, dry_run=False)
    record = json.loads((cfg.receipts / f"{CYCLE}.json").read_text())
    assert code == 1
    assert (record["execution"], record["attempt"], record["ok"]) == ("failed", 1, False)
    assert record["delivery"]["action"] == action
    assert "RuntimeError: boom" in record["delivery"]["error"]
    assert says in record["delivery"]["error"]
    validate(record)


def test_repeated_exceptions_hit_the_attempt_cap_and_never_read_as_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    monkeypatch.setattr(run, "head_sha", lambda *_: "a" * 40)

    def boom(_cfg: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(run, "run_audit", boom)
    codes = [run.run_cycle(cfg, NOW, dry_run=False) for _ in range(4)]
    assert codes == [1, 1, 1, 0]  # three counted attempts, then gave-up
    record = json.loads((cfg.receipts / f"{CYCLE}.json").read_text())
    assert (record["execution"], record["attempt"]) == ("failed", 3)

    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(LIVE))
    monkeypatch.setattr(run, "deliver", lambda *_a: run.Delivery("updated", 7))
    run.run_cycle(cfg, NOW + timedelta(days=7), dry_run=False)
    after = json.loads((cfg.receipts / f"{CYCLE}.json").read_text())
    assert after["execution"] == "failed"  # not rewritten as `missed`


# --- 3. statuses carry only the known set --------------------------------------


def test_receipt_statuses_carry_only_known_statuses() -> None:
    audit = run.parse_audit(jsonl(summary_record(stale=3)))
    record = run.receipt(audit, run.problems(audit), run.Delivery("skipped"), "t", "t")
    assert set(record["statuses"]) == set(run.KNOWN_STATUSES)
