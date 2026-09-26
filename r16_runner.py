#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""R16 weekly run: audit KB claims against published code (ADR-ECO-009 cadence, Tue).

Graduated from the umbrella's dev scratch (devtools#382); design —
docs/superpowers/specs/2026-09-25-r16-runner-graduation-design.md. Runs on the
VPS as r16-kb-freshness.service (deploy/r16/), hourly: it runs only when the
current cycle (Tuesday 09:30 → next Tuesday 09:30, Asia/Tbilisi, whatever the
host's zone) has no successful receipt yet, at most MAX_ATTEMPTS times per
cycle, one run at a time (the lock spans the whole run). Cycles nobody ran are
recorded as `missed` — today's data do not pretend to describe them.

Before the audit the vault clone is brought to origin's default branch and
proven to be there (branch, HEAD = origin, clean tree); otherwise the attempt
fails without auditing an unpublished tree.

Every attempt leaves a receipt in <state-dir>/receipts/<cycle_id>.json
(contracts/r16-receipt/v1): schema version, check id, attempt, start/finish
with offset, producer (runner and auditor SHAs, host label), target SHA per
repo, coverage, status counts, delivery. Kept apart:
  execution  completed (the audit printed a summary), failed, or missed
  problems   claims that are not unchanged, revisions that failed to resolve,
             coverage gaps (no evidence at all); a completed run may have them
  delivery   what happened to the single open `kb-freshness` issue: created,
             updated, closed, not-needed, skipped (run failed) or failed
`ok` in the receipt is true only for a completed run with a delivered result.

Triage (owner, one week from the first detection): confirm the change, schedule
a fix, or accept the limit. Re-runs update the issue but keep the original
deadline; the issue closes itself once a run finds nothing.

Usage: r16_runner.py [--dry-run] [--workspace DIR] [--state-dir DIR]
                     [--gh-config-dir DIR] [--host-label NAME]
Each flag falls back to R16_WORKSPACE / R16_STATE_DIR / R16_GH_CONFIG_DIR /
R16_HOST_LABEL; a missing one is exit 2 before anything runs.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = Path(__file__).resolve().parent
REPO = "andrei-shtanakov/prograph-vault"
LABEL = "kb-freshness"
TITLE = "R16: утверждения KB требуют разбора"
TRIAGE_DAYS = 7
SCHEMA_VERSION = 1
CHECK_ID = "r16-kb-freshness"
CYCLE_TZ = "Asia/Tbilisi"  # fixed by the owner 2026-09-25 — not an env setting
CYCLE_WEEKDAY = 1  # Tuesday (Monday is 0)
CYCLE_START = time(9, 30)
MAX_ATTEMPTS = 3
FIRST_DETECTED = re.compile(r"<!-- r16 first-detected=(\d{4}-\d{2}-\d{2}) -->")
VAULT_DIR = "prograph-vault"
# Pinned to `STATUSES` of prograph-vault/scripts/kb_freshness.py. Not imported:
# the runner works over whatever vault clone is present. A status the auditor
# adds later makes the attempt `failed` with the key named, not a clean week.
KNOWN_STATUSES = ("unchanged", "changed", "missing", "unverified", "invalid")
COVERAGE_KEYS = ("notes", "with_evidence", "unparsed_frontmatter")
DEFAULT_BRANCH = re.compile(r"^ref: refs/heads/(\S+)\tHEAD$", re.MULTILINE)
AUDIT_REL = Path("scripts") / "kb_freshness.py"
SETTINGS = (
    ("workspace", "R16_WORKSPACE"),
    ("state_dir", "R16_STATE_DIR"),
    ("gh_config_dir", "R16_GH_CONFIG_DIR"),
    ("host_label", "R16_HOST_LABEL"),
)


class ConfigError(Exception):
    """A setting is missing or wrong: exit 2, no receipt, no attempt spent."""


@dataclass(frozen=True)
class Config:
    """Where the runner reads and writes; every root comes from outside."""

    workspace: Path
    state_dir: Path
    gh_config_dir: Path
    host_label: str

    @property
    def vault(self) -> Path:
        return self.workspace / VAULT_DIR

    @property
    def audit(self) -> Path:
        return self.vault / AUDIT_REL

    @property
    def receipts(self) -> Path:
        return self.state_dir / "receipts"

    @property
    def lock(self) -> Path:
        return self.state_dir / "r16.lock"


@dataclass(frozen=True)
class Audit:
    """Parsed JSONL output of kb_freshness.py --json."""

    verdicts: list[dict]
    revisions: list[dict]
    summary: dict | None
    raw_tail: str = ""

    @property
    def completed(self) -> bool:
        """The audit ran to its summary line."""
        return self.summary is not None


@dataclass(frozen=True)
class Problems:
    """What needs the owner's triage."""

    claims: list[dict]
    revisions: list[str]
    coverage: list[str]

    def __bool__(self) -> bool:
        return bool(self.claims or self.revisions or self.coverage)


@dataclass(frozen=True)
class Delivery:
    """What happened to the tracking issue."""

    action: str
    issue: int | None = None
    error: str | None = field(default=None)


def main(argv: list[str] | None = None) -> int:
    """Run the current cycle if it still needs a run; write its receipt."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="no receipt, no issue")
    for attr, name in SETTINGS:
        parser.add_argument(
            f"--{attr.replace('_', '-')}", dest=attr, help=f"falls back to ${name}"
        )
    args = parser.parse_args(argv)
    try:
        cfg = resolve_config(vars(args), os.environ)
        zone = cycle_zone()
    except ConfigError as exc:
        print(f"r16: {exc}", file=sys.stderr)
        return 2
    # The lock spans the whole run — vault, audit, GitHub delivery, receipt:
    # decide() guards sequential re-runs, only the lock guards concurrent ones.
    with open_lock(cfg.lock) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run holds the lock")
            return 0
        return run_cycle(cfg, datetime.now(zone).replace(microsecond=0), args.dry_run)


def resolve_config(cli: Mapping[str, object], env: Mapping[str, str]) -> Config:
    """CLI over environment; every root checked locally before anything runs.

    Only presence is checked here — no network: a dead gh token is GitHub's
    state, not configuration, and surfaces as `delivery: failed`.
    """
    values: dict[str, str] = {}
    for attr, name in SETTINGS:
        value = cli.get(attr) or env.get(name)
        if not value:
            raise ConfigError(f"--{attr.replace('_', '-')} / ${name} не задан")
        values[attr] = str(value)
    cfg = Config(
        Path(values["workspace"]),
        Path(values["state_dir"]),
        Path(values["gh_config_dir"]),
        values["host_label"],
    )
    if not cfg.workspace.is_dir():
        raise ConfigError(f"нет каталога workspace {cfg.workspace}")
    if not cfg.receipts.is_dir():
        raise ConfigError(f"нет каталога квитанций {cfg.receipts}")
    if not cfg.audit.is_file():
        raise ConfigError(f"нет аудитора {cfg.audit}")
    hosts = cfg.gh_config_dir / "hosts.yml"
    if not (hosts.is_file() and os.access(hosts, os.R_OK)):
        raise ConfigError(f"профиль gh: нет читаемого {hosts}")
    return cfg


def open_lock(path: Path) -> IO[str]:
    """The lock file, opened without touching its mode (created 0600 if absent)."""
    return os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+")


def run_cycle(cfg: Config, now: datetime, dry_run: bool) -> int:
    """Decide, mark missed cycles, audit, deliver, write the receipt."""
    cid = cycle_start(now).date().isoformat()
    existing = load_receipt(cfg.receipts, cid)
    action = "run" if dry_run else decide(existing)
    if action != "run":
        print(f"cycle {cid}: {action}")
        return 0
    if not dry_run:
        for missed in missed_cycles(latest_cycle(cfg.receipts, before=cid), cid):
            write_receipt(cfg.receipts, missed_receipt(missed, now.isoformat()))
    try:
        audit, found, delivery = run_attempt(cfg, cid, now, dry_run)
    except Exception as exc:  # noqa: BLE001 — any failure must leave a receipt
        # Without a receipt the attempt is not counted (MAX_ATTEMPTS bypassed)
        # and the next cycle would record this one as `missed` — a run that did
        # happen. Delivery may have been under way: its state is unknown.
        cause = f"{type(exc).__name__}: {exc}"
        audit = Audit([], [], None, f"runner raised: {cause}")
        found = Problems([], [], [])
        delivery = Delivery(
            "failed",
            None,
            f"runner raised before the receipt: {cause}; delivery state unknown",
        )
    finished = datetime.now(now.tzinfo).replace(microsecond=0)
    record = receipt(audit, found, delivery, now.isoformat(), finished.isoformat())
    attempt = (existing or {}).get("attempt", 0) + 1
    record |= {"cycle_id": cid, "attempt": attempt, "producer": producer(cfg)}
    print(json.dumps(record, ensure_ascii=False))
    if dry_run:  # a trial run must not look like this cycle's receipt
        return 0 if audit.completed else 1
    write_receipt(cfg.receipts, record)
    return 0 if record["ok"] else 1


def run_attempt(
    cfg: Config, cid: str, now: datetime, dry_run: bool
) -> tuple[Audit, Problems, Delivery]:
    """One attempt: published vault, audit, consistency, delivery."""
    unpublished = sync_vault(cfg.vault)
    audit = (
        Audit([], [], None, f"vault not published: {unpublished}")
        if unpublished
        else run_audit(cfg)
    )
    inconsistent = audit_inconsistency(audit)
    if inconsistent:
        audit = Audit(
            [], audit.revisions, None, f"audit output inconsistent: {inconsistent}"
        )
    found = problems(audit)
    if not audit.completed:
        return audit, found, Delivery("skipped", None, "audit did not complete")
    if dry_run:
        return audit, found, Delivery("dry-run")
    delivery = deliver(
        cfg, found, now.date(), last_issue(cfg.receipts), receipt_pointer(cfg, cid)
    )
    return audit, found, delivery


def audit_inconsistency(audit: Audit) -> str | None:
    """Why the auditor's output cannot be trusted, or None.

    Findings are built from the verdict lines alone; a summary that disagrees
    with them (verdict lines lost, a renamed key, a new status) would otherwise
    read as a clean week and close the triage issue.
    """
    if audit.summary is None:
        return None
    unknown = sorted(set(audit.summary) - set(KNOWN_STATUSES) - set(COVERAGE_KEYS))
    if unknown:
        return f"unknown summary keys: {', '.join(unknown)}"
    counts = Counter(str(v.get("status")) for v in audit.verdicts)
    strange = sorted(set(counts) - set(KNOWN_STATUSES))
    if strange:
        return f"unknown verdict statuses: {', '.join(strange)}"
    diff = [
        f"{status}: summary {audit.summary.get(status, 0)} vs {counts[status]} verdicts"
        for status in KNOWN_STATUSES
        if audit.summary.get(status, 0) != counts[status]
    ]
    if diff:
        return "summary disagrees with verdicts — " + "; ".join(diff)
    return None


def cycle_zone() -> ZoneInfo:
    """The cycle's zone; a host without tzdata is a configuration error."""
    try:
        return ZoneInfo(CYCLE_TZ)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"зона цикла {CYCLE_TZ} не найдена — поставьте tzdata"
        ) from exc


def cycle_start(now: datetime) -> datetime:
    """Start of the cycle `now` falls in: the latest Tuesday 09:30 Tbilisi not after it.

    A naive `now` is refused: on a UTC host it would silently shift Tuesday.
    """
    if now.tzinfo is None:
        raise ValueError("cycle_start needs an aware datetime")
    now = now.astimezone(cycle_zone())
    days = (now.weekday() - CYCLE_WEEKDAY) % 7
    start = datetime.combine(
        now.date() - timedelta(days=days), CYCLE_START, tzinfo=now.tzinfo
    )
    return start if start <= now else start - timedelta(days=7)


def missed_cycles(last: str | None, current: str) -> list[str]:
    """Cycle ids strictly between the last one with a receipt and the current."""
    if last is None:
        return []
    day, end = date.fromisoformat(last) + timedelta(days=7), date.fromisoformat(current)
    missed = []
    while day < end:
        missed.append(day.isoformat())
        day += timedelta(days=7)
    return missed


def decide(existing: dict | None) -> str:
    """run, done (already succeeded) or gave-up (MAX_ATTEMPTS failed)."""
    if existing is None:
        return "run"
    if existing.get("ok"):
        return "done"
    return "gave-up" if existing.get("attempt", 0) >= MAX_ATTEMPTS else "run"


def missed_receipt(cid: str, noted_at: str) -> dict:
    """A cycle nobody ran: recorded as such, with no data claiming to describe it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": CHECK_ID,
        "cycle_id": cid,
        "attempt": 0,
        "noted_at": noted_at,
        "execution": "missed",
        "revisions": {},
        "delivery": {
            "action": "not-run",
            "issue": None,
            "issue_url": None,
            "error": None,
        },
        "ok": False,
    }


def load_receipt(receipts: Path, cid: str) -> dict | None:
    """This cycle's receipt, if any."""
    try:
        return json.loads((receipts / f"{cid}.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def latest_cycle(receipts: Path, before: str) -> str | None:
    """The newest cycle id with a receipt, older than `before`."""
    ids = sorted(p.stem for p in receipts.glob("????-??-??.json") if p.stem < before)
    return ids[-1] if ids else None


def write_receipt(receipts: Path, record: dict) -> None:
    """<receipts>/<cycle_id>.json, replaced atomically in the same directory."""
    path = receipts / f"{record['cycle_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def producer(cfg: Config) -> dict[str, str | None]:
    """Who produced the receipt: runner and auditor SHAs, and the host label."""
    return {
        "runner": head_sha(HERE, Path(__file__).resolve()),
        "auditor": head_sha(cfg.vault, cfg.audit),
        "host": cfg.host_label,
    }


def receipt_pointer(cfg: Config, cid: str) -> str:
    """Where the operator finds this cycle's receipt — a pointer, not a public link."""
    return f"{cfg.host_label}:{cfg.receipts / f'{cid}.json'}"


def head_sha(repo: Path, file: Path) -> str | None:
    """HEAD of `repo`, suffixed `+dirty` when `file` has local edits."""
    sha = (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or None
    )
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", str(file)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return f"{sha}+dirty" if sha and dirty else sha


def git_out(repo: Path, *args: str) -> str | None:
    """stdout of a git command in `repo`, or None when it failed."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return out.stdout if out.returncode == 0 else None


def sync_vault(vault: Path) -> str | None:
    """Bring the vault clone to origin's default branch and prove it got there.

    A successful `pull --ff-only` is not proof: local commits ahead of
    upstream and uncommitted or untracked files survive it, and the audit
    reads notes from disk. None means published and clean; otherwise the
    reason, and the audit must not run.
    """
    symref = git_out(vault, "ls-remote", "--symref", "origin", "HEAD")
    if symref is None:
        return "origin unreachable"
    match = DEFAULT_BRANCH.search(symref)
    if match is None:
        return "origin did not name its default branch"
    branch = match.group(1)
    current = (git_out(vault, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if current != branch:
        return f"clone is on {current or '?'}, origin publishes {branch}"
    if git_out(vault, "pull", "--ff-only", "--quiet", "origin", branch) is None:
        return f"pull --ff-only origin {branch} failed"
    head = (git_out(vault, "rev-parse", "HEAD") or "").strip()
    remote = (git_out(vault, "rev-parse", f"refs/remotes/origin/{branch}") or "").strip()
    if not head or head != remote:
        return f"HEAD {head[:12]} is not origin/{branch} {remote[:12]}"
    dirty = git_out(vault, "status", "--porcelain", "--untracked-files=all")
    if dirty is None or dirty.strip():
        first = (dirty or "").strip().splitlines()[:3]
        return "working tree not clean: " + "; ".join(first)
    return None


def run_audit(cfg: Config) -> Audit:
    """Run the published-target audit and parse what it printed."""
    out = subprocess.run(
        [
            "uv", "run", str(cfg.audit), "--json", "--target", "published",
            "--workspace", str(cfg.workspace),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_audit(out.stdout, out.stderr)


def parse_audit(stdout: str, stderr: str = "") -> Audit:
    """Split the JSONL stream into verdicts, revisions and the summary."""
    verdicts, revisions, summary = [], [], None
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "summary" in record:
            summary = record["summary"]
        elif "revision" in record:
            revisions.append(record["revision"])
        elif "status" in record:
            verdicts.append(record)
    tail = "\n".join((stdout + stderr).strip().splitlines()[-5:])
    return Audit(verdicts, revisions, summary, tail)


def problems(audit: Audit) -> Problems:
    """Claims, revisions and coverage that need a human look."""
    claims = [v for v in audit.verdicts if v["status"] != "unchanged"]
    revisions = [f"{r['repo']}: {r['error']}" for r in audit.revisions if r["error"]]
    coverage = []
    if audit.summary is not None and audit.summary.get("with_evidence", 0) == 0:
        coverage.append("no note declares evidence")
    return Problems(claims, revisions, coverage)


def deliver(
    cfg: Config,
    found: Problems,
    today: date,
    known: int | None,
    pointer: str,
) -> Delivery:
    """Create, update or close the single open tracking issue."""
    try:
        existing = open_issue(cfg, known)
        if not found:
            if existing is None:
                return Delivery("not-needed")
            number = existing["number"]
            note = f"Прогон {today}: все утверждения `unchanged`, закрываю."
            gh(cfg, "issue", "comment", str(number), "--body", note)
            gh(cfg, "issue", "close", str(number))
            return Delivery("closed", number)
        first = first_detected(existing["body"]) if existing else None
        body = issue_body(found, first or today, today, pointer)
        if existing is None:
            ensure_label(cfg)
            url = gh(
                cfg, "issue", "create", "--title", TITLE, "--label", LABEL, "--body", body
            )
            return Delivery("created", int(url.rstrip().rsplit("/", 1)[-1]))
        gh(cfg, "issue", "edit", str(existing["number"]), "--body", body)
        return Delivery("updated", existing["number"])
    except (subprocess.CalledProcessError, ValueError, KeyError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        return Delivery("failed", None, " ".join(str(detail).split())[:300])


def last_issue(receipts: Path) -> int | None:
    """Issue number from the newest receipt that names one.

    Read back directly, it beats the label listing, which is served from an index
    that lags seconds behind: a same-day re-run would otherwise open a duplicate.
    """
    for path in sorted(receipts.glob("*.json"), reverse=True):
        try:
            issue = json.loads(path.read_text())["delivery"]["issue"]
        except (ValueError, KeyError, TypeError):
            continue
        if issue:
            return int(issue)
    return None


def open_issue(cfg: Config, known: int | None = None) -> dict | None:
    """The open tracking issue, if any: the known number first, then by label.

    The label listing comes from an index that lags both ways (a just-created
    issue is missing, a just-closed one still shows as open), so it only supplies
    candidates; each one's state is read directly before it is used.
    """
    listing = gh(
        cfg,
        "issue",
        "list",
        "--label",
        LABEL,
        "--state",
        "open",
        "--json",
        "number",
        "--limit",
        "20",
    )
    listed = sorted(i["number"] for i in json.loads(listing or "[]"))
    candidates = ([known] if known is not None else []) + listed
    for number in dict.fromkeys(candidates):
        issue = json.loads(
            gh(cfg, "issue", "view", str(number), "--json", "number,body,state")
        )
        if issue["state"] == "OPEN":
            return issue
    return None


def ensure_label(cfg: Config) -> None:
    """Create the tracking label once; an existing label is fine."""
    subprocess.run(
        [
            "gh",
            "label",
            "create",
            LABEL,
            "-R",
            REPO,
            "--color",
            "C5DEF5",
            "--description",
            "R16 claim-level freshness: triage needed",
        ],
        env=gh_env(cfg),
        capture_output=True,
        text=True,
        check=False,
    )


def gh(cfg: Config, *args: str) -> str:
    """Run gh against the vault repo from the ai-prosto profile."""
    out = subprocess.run(
        ["gh", *args, "-R", REPO],
        env=gh_env(cfg),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def gh_env(cfg: Config) -> dict[str, str]:
    """Environment that points gh at the ai-prosto profile."""
    return os.environ | {"GH_CONFIG_DIR": str(cfg.gh_config_dir)}


def first_detected(body: str) -> date | None:
    """The first-detection date kept in the issue body, if this runner wrote it."""
    match = FIRST_DETECTED.search(body)
    return date.fromisoformat(match.group(1)) if match else None


def issue_body(found: Problems, first: date, today: date, pointer: str) -> str:
    """Issue text: what to triage, by when, and the evidence."""
    due = first + timedelta(days=TRIAGE_DAYS)
    parts = [
        f"<!-- r16 first-detected={first.isoformat()} -->",
        f"Еженедельный прогон R16 (`kb_freshness.py --target published`), "
        f"последний — {today}. Впервые обнаружено {first}.",
        "",
        f"**Владелец: Андрей. Первичный разбор до {due}** — по каждой строке одно из: "
        "подтвердить изменение, назначить исправление или принять ограничение "
        "(поднять `baseline` PR-ом после перепроверки). Исправление в неделю "
        "укладываться не обязано; повторные прогоны срок не сдвигают.",
    ]
    if found.claims:
        parts += [
            "",
            "## Утверждения",
            "",
            "| статус | заметка | claim | опора | детали |",
            "|---|---|---|---|---|",
        ]
        parts += [
            f"| `{v['status']}` | `{v['doc']}` | `{v['id']}` | "
            f"`{v['repo']}/{v['path']}` | {v['detail']} |"
            for v in found.claims
        ]
        parts += [
            "",
            *[
                f"- `^{v['block']}`: {v['statement']}"
                for v in found.claims
                if v.get("block")
            ],
        ]
    if found.revisions:
        parts += [
            "",
            "## Ревизии (проверка не выполнена)",
            "",
            *[f"- {r}" for r in found.revisions],
        ]
    if found.coverage:
        parts += ["", "## Покрытие", "", *[f"- {c}" for c in found.coverage]]
    parts += ["", f"Квитанции прогонов: `{pointer}`."]
    return "\n".join(parts) + "\n"


def receipt(
    audit: Audit, found: Problems, delivery: Delivery, run_at: str, finished_at: str
) -> dict:
    """The record every attempt leaves behind (cycle fields are added by the caller)."""
    completed = audit.completed
    delivered = delivery.action not in ("failed", "skipped")
    summary = audit.summary or {}
    url = (
        f"https://github.com/{REPO}/issues/{delivery.issue}" if delivery.issue else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": CHECK_ID,
        "started_at": run_at,
        "finished_at": finished_at,
        "target": "published",
        "execution": "completed" if completed else "failed",
        "revisions": {r["repo"]: r["sha"] or r["error"] for r in audit.revisions},
        "coverage": {k: summary[k] for k in COVERAGE_KEYS if k in summary},
        "statuses": {k: summary[k] for k in KNOWN_STATUSES if k in summary},
        "problems": {
            "claims": len(found.claims),
            "revisions": len(found.revisions),
            "coverage": len(found.coverage),
        },
        "delivery": {
            "action": delivery.action,
            "issue": delivery.issue,
            "issue_url": url,
            "error": delivery.error,
        },
        "audit_tail": None if completed else audit.raw_tail,
        "ok": completed and delivered,
    }


if __name__ == "__main__":
    sys.exit(main())
