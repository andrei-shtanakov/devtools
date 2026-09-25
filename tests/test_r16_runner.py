"""Tests for the R16 weekly runner (pure parts; gh and git stay at the edges).

Run: uv run --frozen pytest tests/test_r16_runner.py
"""

import fcntl
import json
import os
import subprocess as sp
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

import r16_runner as run

TBILISI = ZoneInfo("Asia/Tbilisi")

ENV_NAMES = {
    "workspace": "R16_WORKSPACE",
    "state_dir": "R16_STATE_DIR",
    "gh_config_dir": "R16_GH_CONFIG_DIR",
    "host_label": "R16_HOST_LABEL",
}


def layout(root: Path) -> dict[str, str]:
    """A complete on-disk layout; returns CLI values for resolve_config."""
    audit = root / "ws" / "prograph-vault" / "scripts" / "kb_freshness.py"
    audit.parent.mkdir(parents=True)
    audit.write_text("# auditor stub\n")
    (root / "state" / "receipts").mkdir(parents=True)
    (root / "gh").mkdir()
    (root / "gh" / "hosts.yml").write_text("github.com: {}\n")
    return {
        "workspace": str(root / "ws"),
        "state_dir": str(root / "state"),
        "gh_config_dir": str(root / "gh"),
        "host_label": "vps-test",
    }


def cfg_for(root: Path) -> "run.Config":
    return run.resolve_config(layout(root), {})


def argv_for(values: dict[str, str]) -> list[str]:
    out: list[str] = []
    for attr, value in values.items():
        out += [f"--{attr.replace('_', '-')}", value]
    return out


def lock_is_held(path: Path) -> bool:
    """True when another open file description holds the flock on `path`."""
    with path.open("r") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False

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
    receipt = run.receipt(audit, run.problems(audit), delivery, "2026-09-29T09:30:00", "2026-09-29T09:31:00+04:00")
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
    receipt = run.receipt(audit, run.problems(audit), delivery, "t", "t")
    assert (receipt["execution"], receipt["ok"]) == ("completed", False)


def test_failed_run_makes_the_receipt_not_ok() -> None:
    audit = run.parse_audit("boom\n")
    receipt = run.receipt(
        audit, run.problems(audit), run.Delivery("skipped"), "t", "t"
    )
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
        (datetime(2026, 9, 22, 9, 30, tzinfo=TBILISI), datetime(2026, 9, 22, 9, 30, tzinfo=TBILISI)),  # on the dot
        (datetime(2026, 9, 22, 9, 29, tzinfo=TBILISI), datetime(2026, 9, 15, 9, 30, tzinfo=TBILISI)),  # just before
        (datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), datetime(2026, 9, 22, 9, 30, tzinfo=TBILISI)),  # Wednesday
        (datetime(2026, 9, 28, 23, 0, tzinfo=TBILISI), datetime(2026, 9, 22, 9, 30, tzinfo=TBILISI)),  # Monday
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
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery("not-needed"))
    (cfg.receipts / "2026-09-01.json").write_text(
        json.dumps({"ok": True, "attempt": 1})
    )
    broken = run.parse_audit("boom\n")
    monkeypatch.setattr(run, "run_audit", lambda _cfg: broken)
    now = datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI)

    codes = [run.run_cycle(cfg, now, dry_run=False) for _ in range(4)]

    assert codes == [1, 1, 1, 0]  # three failed attempts, then gave-up quietly
    current = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert (current["attempt"], current["execution"], current["ok"]) == (
        3,
        "failed",
        False,
    )
    for cid in ("2026-09-08", "2026-09-15"):
        missed = json.loads((cfg.receipts / f"{cid}.json").read_text())
        assert (missed["execution"], missed["ok"]) == ("missed", False)


# --- configuration and lock (spec §1.2 п.1, п.6) ------------------------------


def test_cli_wins_over_environment(tmp_path: Path) -> None:
    values = layout(tmp_path)
    env = {name: "/nowhere" for name in ENV_NAMES.values()}
    cfg = run.resolve_config(values, env)
    assert cfg.workspace == Path(values["workspace"])
    assert cfg.receipts == Path(values["state_dir"]) / "receipts"
    assert cfg.lock == Path(values["state_dir"]) / "r16.lock"


def test_environment_fills_what_cli_leaves_out(tmp_path: Path) -> None:
    values = layout(tmp_path)
    env = {ENV_NAMES[k]: v for k, v in values.items()}
    assert run.resolve_config({}, env).host_label == "vps-test"


@pytest.mark.parametrize("missing", [*ENV_NAMES, "hosts.yml", "receipts", "auditor"])
def test_missing_setting_exits_2_before_lock(
    tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    values = layout(tmp_path)
    if missing in ENV_NAMES:
        del values[missing]
    elif missing == "hosts.yml":
        (tmp_path / "gh" / "hosts.yml").unlink()
    elif missing == "receipts":
        (tmp_path / "state" / "receipts").rmdir()
    else:
        (tmp_path / "ws" / "prograph-vault" / "scripts" / "kb_freshness.py").unlink()
    assert run.main(["--dry-run", *argv_for(values)]) == 2
    assert not (tmp_path / "state" / "r16.lock").exists()
    assert "r16:" in capsys.readouterr().err


def test_config_check_does_not_touch_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token validity is not a start-up check: no subprocess at all here."""

    def no_subprocess(*_a: object, **_k: object) -> None:
        raise AssertionError("resolve_config must stay local")

    monkeypatch.setattr(run.subprocess, "run", no_subprocess)
    cfg_for(tmp_path)


def test_lock_is_created_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "r16.lock"
    old = os.umask(0)
    try:
        with run.open_lock(path):
            pass
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600


def test_lock_is_held_through_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit and GitHub delivery both run under the lock, not after it."""
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    values = layout(tmp_path)
    lock = tmp_path / "state" / "r16.lock"
    seen: list[str] = []
    clean = json.dumps({"summary": {"unchanged": 1, "with_evidence": 1}})

    def audit(_cfg: "run.Config") -> "run.Audit":
        seen.append(f"audit:{lock_is_held(lock)}")
        return run.parse_audit(clean)

    def deliver(*_a: object) -> "run.Delivery":
        seen.append(f"deliver:{lock_is_held(lock)}")
        return run.Delivery("not-needed")

    monkeypatch.setattr(run, "run_audit", audit)
    monkeypatch.setattr(run, "deliver", deliver)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    assert run.main(argv_for(values)) == 0
    assert seen == ["audit:True", "deliver:True"]


def test_concurrent_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = layout(tmp_path)
    receipts = tmp_path / "state" / "receipts"
    (receipts / "2026-09-15.json").write_text('{"ok": true, "attempt": 1}\n')
    before = {p.name: p.stat().st_mtime_ns for p in receipts.iterdir()}

    def forbidden(*_a: object) -> None:
        raise AssertionError("a second run must not reach the audit")

    monkeypatch.setattr(run, "run_audit", forbidden)
    with run.open_lock(tmp_path / "state" / "r16.lock") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert run.main(argv_for(values)) == 0
    assert {p.name: p.stat().st_mtime_ns for p in receipts.iterdir()} == before


# --- cycle zone (spec §1.2 п.2) -------------------------------------------------


@pytest.fixture
def utc_system(monkeypatch: pytest.MonkeyPatch):
    """The VPS case: process zone is UTC."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("utc", "start"),
    [
        (datetime(2026, 9, 22, 5, 30, tzinfo=timezone.utc), "2026-09-22T09:30:00+04:00"),
        (datetime(2026, 9, 22, 5, 29, tzinfo=timezone.utc), "2026-09-15T09:30:00+04:00"),
    ],
)
def test_cycle_is_tbilisi_under_utc_system_zone(
    utc_system: None, utc: datetime, start: str
) -> None:
    assert run.cycle_start(utc).isoformat() == start


def test_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        run.cycle_start(datetime(2026, 9, 22, 9, 30))


def test_missing_zone_exits_2_before_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = layout(tmp_path)
    monkeypatch.setattr(run, "CYCLE_TZ", "Nowhere/Nothing")
    assert run.main(["--dry-run", *argv_for(values)]) == 2
    assert not (tmp_path / "state" / "r16.lock").exists()


def test_receipt_timestamps_carry_the_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    cfg = cfg_for(tmp_path)
    clean = json.dumps({"summary": {"unchanged": 1, "with_evidence": 1}})
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "deliver", lambda *_: run.Delivery("not-needed"))
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit(clean))
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert record["started_at"].endswith("+04:00")
    assert record["finished_at"].endswith("+04:00")


def test_legacy_naive_receipt_carries_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt copied from the Mac (no offset) still counts its attempts."""
    monkeypatch.setattr(run, "sync_vault", lambda _v: None)
    cfg = cfg_for(tmp_path)
    (cfg.receipts / "2026-09-22.json").write_text(
        json.dumps(
            {
                "cycle_id": "2026-09-22",
                "attempt": 2,
                "ok": False,
                "started_at": "2026-09-22T10:00:00",
                "execution": "failed",
            }
        )
    )
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    monkeypatch.setattr(run, "run_audit", lambda _cfg: run.parse_audit("boom\n"))
    run.run_cycle(cfg, datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI), dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert record["attempt"] == 3


# --- vault at the published branch (spec §1.2 п.3) ----------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo: Path, *args: str) -> str:
    return sp.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | GIT_ENV,
    ).stdout


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, Path]:
    """(origin bare repo, clone on its default branch `main`, clean)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "note.md").write_text("v1\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "v1")
    origin = tmp_path / "origin.git"
    sp.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    clone = tmp_path / "clone"
    sp.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


def advance_origin(tmp_path: Path, origin: Path) -> None:
    work = tmp_path / "pusher"
    sp.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "note.md").write_text("v2\n")
    git(work, "commit", "-qam", "v2")
    git(work, "push", "-q", "origin", "main")


def test_sync_vault_fast_forwards_to_origin(
    tmp_path: Path, published: tuple[Path, Path]
) -> None:
    origin, clone = published
    advance_origin(tmp_path, origin)
    assert run.sync_vault(clone) is None
    assert (clone / "note.md").read_text() == "v2\n"


@pytest.mark.parametrize(
    "case", ["unreachable", "wrong-branch", "ahead", "tracked-edit", "untracked"]
)
def test_sync_vault_refuses(
    tmp_path: Path, published: tuple[Path, Path], case: str
) -> None:
    _origin, clone = published
    if case == "unreachable":
        git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    elif case == "wrong-branch":
        git(clone, "checkout", "-qb", "feature")
    elif case == "ahead":
        (clone / "note.md").write_text("local\n")
        git(clone, "commit", "-qam", "local")
    elif case == "tracked-edit":
        (clone / "note.md").write_text("edited\n")
    else:
        (clone / "stray.md").write_text("not published\n")
    assert run.sync_vault(clone) is not None


def test_unpublished_vault_fails_the_attempt_without_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cfg_for(tmp_path)
    monkeypatch.setattr(run, "sync_vault", lambda _v: "HEAD ahead of origin/main")

    def forbidden(_cfg: object) -> None:
        raise AssertionError("audit must not run over an unpublished vault")

    monkeypatch.setattr(run, "run_audit", forbidden)
    monkeypatch.setattr(run, "producer", lambda _cfg: {"runner": "r", "auditor": "a"})
    now = datetime(2026, 9, 23, 17, 0, tzinfo=TBILISI)
    code = run.run_cycle(cfg, now, dry_run=False)
    record = json.loads((cfg.receipts / "2026-09-22.json").read_text())
    assert code == 1
    assert (record["execution"], record["delivery"]["action"]) == ("failed", "skipped")
    assert "HEAD ahead of origin/main" in record["audit_tail"]
