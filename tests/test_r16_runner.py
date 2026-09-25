"""Tests for the R16 weekly runner (pure parts; gh and git stay at the edges).

Run: uv run --frozen pytest tests/test_r16_runner.py
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

import r16_runner as run

TODAY = date(2026, 9, 29)


def verdict(status: str, claim_id: str = "c") -> dict:
    """One verdict record as kb_freshness --json prints it."""
    return {
        "doc": "authored/rules/git-workflow.md",
        "id": claim_id,
        "repo": "steward",
        "path": "gate.sh",
        "status": status,
        "detail": f"{status} detail",
        "head": "a" * 40,
        "block": claim_id,
        "statement": f"Statement {claim_id}.",
        "target": "origin/master",
    }


def summary(**counts: int) -> dict:
    """A summary record with defaults for a healthy one-claim run."""
    base = dict.fromkeys(("changed", "missing", "unverified", "invalid"), 0)
    base |= {"unchanged": 1, "notes": 10, "with_evidence": 1}
    base |= {"unparsed_frontmatter": 0}
    return {"summary": base | counts}


def revision(repo: str = "steward", error: str | None = None) -> dict:
    """A revision record."""
    sha = None if error else "b" * 40
    return {
        "revision": {"repo": repo, "label": "origin/master", "sha": sha, "error": error}
    }


def lines(*records: dict) -> str:
    """JSONL stdout of the audit."""
    return "\n".join(json.dumps(r) for r in records) + "\n"


def test_clean_run_has_no_problems() -> None:
    audit = run.parse_audit(lines(verdict("unchanged"), revision(), summary()))
    assert audit.completed
    assert run.problems(audit) == run.Problems([], [], [])


def test_changed_claim_is_a_problem_but_the_run_completed() -> None:
    audit = run.parse_audit(
        lines(verdict("changed"), revision(), summary(unchanged=0, changed=1))
    )
    found = run.problems(audit)
    assert audit.completed
    assert [v["status"] for v in found.claims] == ["changed"]


def test_fetch_error_is_a_revision_problem() -> None:
    audit = run.parse_audit(
        lines(verdict("unverified"), revision(error="fetch failed"), summary())
    )
    assert run.problems(audit).revisions == ["steward: fetch failed"]


def test_no_evidence_at_all_is_a_coverage_problem() -> None:
    audit = run.parse_audit(lines(summary(unchanged=0, with_evidence=0)))
    assert run.problems(audit).coverage == ["no note declares evidence"]


def test_unparsed_frontmatter_alone_is_not_a_problem() -> None:
    audit = run.parse_audit(
        lines(verdict("unchanged"), summary(unparsed_frontmatter=6))
    )
    assert not run.problems(audit)


def test_missing_summary_means_the_run_did_not_complete() -> None:
    audit = run.parse_audit(lines(verdict("unchanged")))
    assert not audit.completed


def test_garbage_output_means_the_run_did_not_complete() -> None:
    assert not run.parse_audit("Traceback (most recent call last):\n").completed


def test_first_detected_survives_a_rerun() -> None:
    body = run.issue_body(
        run.Problems([verdict("changed")], [], []), date(2026, 9, 22), TODAY
    )
    assert run.first_detected(body) == date(2026, 9, 22)
    assert "2026-09-29" in body  # the deadline: first detection + 7 days


def test_first_detected_absent_in_foreign_body() -> None:
    assert run.first_detected("hand-written issue") is None


def test_issue_body_quotes_the_statement_and_the_triage_choices() -> None:
    found = run.Problems([verdict("changed", "gate")], [], [])
    body = run.issue_body(found, TODAY, TODAY)
    assert "Statement gate." in body
    assert "принять ограничение" in body
    assert "2026-10-06" in body


def test_receipt_separates_run_problems_and_delivery() -> None:
    audit = run.parse_audit(
        lines(verdict("changed"), revision(), summary(unchanged=0, changed=1))
    )
    delivery = run.Delivery("updated", 42, None)
    receipt = run.receipt(audit, run.problems(audit), delivery, "2026-09-29T09:30:00")
    assert receipt["delivery"]["issue_url"].endswith("/issues/42")
    assert receipt["coverage"] == {
        "notes": 10,
        "with_evidence": 1,
        "unparsed_frontmatter": 0,
    }
    assert receipt["execution"] == "completed"
    assert receipt["problems"] == {"claims": 1, "revisions": 0, "coverage": 0}
    assert receipt["delivery"]["action"] == "updated"
    assert receipt["revisions"] == {"steward": "b" * 40}
    assert receipt["ok"] is True


def test_failed_delivery_makes_the_receipt_not_ok() -> None:
    audit = run.parse_audit(lines(verdict("changed"), summary(changed=1)))
    delivery = run.Delivery("failed", None, "gh: HTTP 502")
    receipt = run.receipt(audit, run.problems(audit), delivery, "t")
    assert (receipt["execution"], receipt["ok"]) == ("completed", False)


def test_failed_run_makes_the_receipt_not_ok() -> None:
    audit = run.parse_audit("boom\n")
    receipt = run.receipt(audit, run.problems(audit), run.Delivery("skipped"), "t")
    assert (receipt["execution"], receipt["ok"]) == ("failed", False)


def test_last_issue_comes_from_the_newest_receipt(tmp_path: Path) -> None:
    def write(day: str, issue: int | None) -> None:
        record = {"delivery": {"action": "x", "issue": issue, "error": None}}
        (tmp_path / f"{day}.json").write_text(json.dumps(record))

    write("2026-09-15", 7)
    write("2026-09-22", 9)
    write("2026-09-29", None)  # a clean week names no issue
    (tmp_path / "2026-09-30.json").write_text("not json")
    assert run.last_issue(tmp_path) == 9


def test_no_receipts_no_known_issue(tmp_path: Path) -> None:
    assert run.last_issue(tmp_path) is None


@pytest.mark.parametrize(
    ("now", "start"),
    [
        (datetime(2026, 9, 22, 9, 30), datetime(2026, 9, 22, 9, 30)),  # on the dot
        (datetime(2026, 9, 22, 9, 29), datetime(2026, 9, 15, 9, 30)),  # just before
        (datetime(2026, 9, 23, 17, 0), datetime(2026, 9, 22, 9, 30)),  # Wednesday
        (datetime(2026, 9, 28, 23, 0), datetime(2026, 9, 22, 9, 30)),  # Monday
    ],
)
def test_cycle_starts_on_tuesday_morning(now: datetime, start: datetime) -> None:
    assert run.cycle_start(now) == start


def test_cycles_between_runs_are_listed_as_missed() -> None:
    assert run.missed_cycles("2026-09-01", "2026-09-22") == ["2026-09-08", "2026-09-15"]
    assert run.missed_cycles("2026-09-15", "2026-09-22") == []
    assert run.missed_cycles(None, "2026-09-22") == []


@pytest.mark.parametrize(
    ("existing", "action"),
    [
        (None, "run"),
        ({"ok": True, "attempt": 1}, "done"),
        ({"ok": False, "attempt": 1}, "run"),
        ({"ok": False, "attempt": 3}, "gave-up"),
        ({"ok": False, "execution": "missed", "attempt": 0}, "run"),
    ],
)
def test_decide_runs_the_current_cycle_once_with_bounded_retries(
    existing: dict | None, action: str
) -> None:
    assert run.decide(existing) == action


def test_missed_receipt_is_explicit_not_backfilled() -> None:
    record = run.missed_receipt("2026-09-15", "2026-09-23T17:00:00")
    assert (record["cycle_id"], record["execution"], record["ok"]) == (
        "2026-09-15",
        "missed",
        False,
    )
    assert record["revisions"] == {}  # no data pretends to describe that week


def test_run_cycle_marks_missed_cycles_and_caps_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end over the cycle logic, with audit and delivery stubbed."""
    monkeypatch.setattr(run, "RECEIPTS", tmp_path)
    monkeypatch.setattr(run, "producer", lambda: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery("not-needed"))
    (tmp_path / "2026-09-01.json").write_text(json.dumps({"ok": True, "attempt": 1}))
    broken = run.parse_audit("boom\n")
    monkeypatch.setattr(run, "run_audit", lambda: broken)
    now = datetime(2026, 9, 23, 17, 0)

    codes = [run.run_cycle(now, dry_run=False) for _ in range(4)]

    assert codes == [1, 1, 1, 0]  # three failed attempts, then gave-up quietly
    current = json.loads((tmp_path / "2026-09-22.json").read_text())
    assert (current["attempt"], current["execution"], current["ok"]) == (
        3,
        "failed",
        False,
    )
    for cid in ("2026-09-08", "2026-09-15"):
        missed = json.loads((tmp_path / f"{cid}.json").read_text())
        assert (missed["execution"], missed["ok"]) == ("missed", False)
