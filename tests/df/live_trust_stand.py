"""Live stand acceptance of stage 3a: the trust rows (installer spec 10, 3a;
plan 2026-11-04 Task 15).

Run only by `.github/workflows/df-stage3-live.yml`, as root, in the
disposable runners of the environment `df-stage3-live` (the owner's
approval, `master` only), one cell at a time, in three phases:

    python -m tests.df.live_trust_stand install --source-sha SHA --out DIR
    python -m tests.df.live_trust_stand stand --source-sha SHA --out DIR \\
        --secrets DIR --repo-id N --app-id N --installation-id N \\
        --policy-repo-id N --trust-repo-id N --trust-branch B --tag TAG
    python -m tests.df.live_trust_stand wipe --out DIR --secrets DIR \\
        [--repo-id N --trust-repo-id N --tag TAG]

The world is the 2b stand's (`tests/df/live_stand.py`): `install` is its
install with the provenance check, `stand` checks provenance again before
any secret is read, then its `set_up` on the public product stand
`df-roles-probe` (O8; the App's installation must be exactly that repo --
the H2 check), the pinned `gh` approved by `cli set gh`, then the rows:

- Т1 bootstrap order and the publishable content: `setup-trust check
  --templates` shows `setup_trust.TEMPLATES` byte for byte; both trust
  repos' histories are an initial base (no PR) followed by merged PRs only;
  `setup-trust check` (the operator, the owner's temporary read-only PAT,
  H6) has no stop; `sudo setup-trust write` (the history token, H5) ends
  `clear`; a run record of the bound root is registered (rows 10-12);
- Т2 the request table of spec 6.1 (`REQUEST_TABLE`): every live request
  with its credential against the stand, its status and judgement;
- Т3 `trust-status` with the binding: `clear` on the trusted head;
- Т4 the App on a trust repo is a violation: with a fixture `trust.toml`
  naming the product as `df-trust` (another root), the publisher's
  `self-check` reports `app_installation_on_trust_source`; the real binding
  is restored and its `self-check` has none (control);
- 10, 11, 12 the 2b merge rows on the stop route with the binding (the run
  record carries the bound root: `trust_root` is the identity);
- Т5 the cold path measurement (owner decision 3): the branch
  `measure/<tag>` of `df-roles-probe-trust` with N = 50 first-parent links
  (5 change the journal by allowed transitions), direct commits without a
  ruleset, bound by a fixture `trust.toml` (a new root: no accepted head,
  so `op: trust` takes the full pass, under this checkout's admin tree);
  the time of `trust-status`, the `ps` span of `df-precheck` and the
  history token's request count go to `cold-measure.json`
  (`MEASURE_SCHEMA`, read by Task 16 for `DEADLINE_v2`);
- Т6 the rewritten history: `measure/<tag>` moved back to a SHA the pass
  read; `trust-status` reports `not_descendant` with a cold pass;
- 9 the cleanup: the 2b row 9 and the deletion of `measure/<tag>`.

Rows Т5 and Т6 write one ref, `refs/heads/measure/<tag>` (`ref_path`),
with `DF_STAND_TRUST_WRITE_TOKEN`: the owner's fine-grained PAT, repository
access only `df-roles-probe-trust`, Contents: read/write, the shortest
expiry, revoked after every run (H8); the owner's step H6a, proposed, not
among H4-H6. The forge cannot limit it to one ref, so before any write Т5
probes its scope (`scope_problems`, recorded): the token self-reports
`push: false` on `df-roles-probe-policy` (the repository selection), and the
trusted branch's rules hold `pull_request` and `non_fast_forward` (its
ruleset still binds the branch; that no bypass actor pushes directly is
H6a's confirmation, invisible to a token). Without the token, or with a
probe that does not hold, Т5 is `not_verified` and Т6 is skipped: never a
pass, and nothing is written. Т2 is `not_verified` when a call of the
human's perimeter check was not `200` or there was none (spec 6.2: «не
проверено», не «ок»). The ceiling of a measured pass is the checker's own
`DEADLINE` (150 s, `governance.act_precheck`): a longer cold pass ends
`unavailable`, Т5 fails with `m_seconds: null`, and Task 16 has no M until
the owner chooses the fallback (`MEASURE_NOTES`). A row's status is `pass`, `fail`,
`инфраструктура`, `not_verified` or skipped; the run is green only when
every row passed. Exit codes as the 2b stand. No pytest test runs a phase or
reads `DF_STAND_*`; `tests/df/test_live_trust_stand.py` tests the helpers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

import pytest

from df import setup_agent, setup_trust
from df.publisher import github, history, ops, request
from df.publisher.github import GitHubRefused, GitHubUnavailable, Transport
from df.publisher.request import TrustStatus
from df.roles import PRECHECK_USER
from df.setup_trust import Refused
from df.stage2_config import Product, TrustBinding, dump_trust, load_trust
from tests.df import live_stand
from tests.df.fake_github import FakeGitHub
from tests.df.gh_release import install_gh
from tests.df.live_stand import (
    EXIT_INFRA,
    GUARD,
    ProvenanceError,
    Row,
    RowFailed,
    Stand,
    StandArgs,
    expect,
    gate,
    live_top,
    record,
    require_ci_root,
    revoke,
    run_rows,
    stand_imports_problem,
    verdict,
)
from tests.df.roles_world import (
    OPERATOR,
    OS_DIR,
    PRODUCTION,
    SUDO,
    RolesWorld,
    as_operator,
    check,
    shown,
    teardown_install,
    teardown_roles,
    write_etc,
)
from tests.df.root_world import (
    INFRA,
    FixtureRepo,
    InstalledWorld,
    RootWorld,
    current_id,
    infra,
)

if TYPE_CHECKING:
    from governance.act_epochs import Epoch

HISTORY_FILE = "history-token"
PAT_FILE = "perimeter-pat"
WRITE_FILE = "trust-write-token"
NOT_VERIFIED = "not_verified"
#: Owner decision 3 and spec 10 (3a): N first-parent links, CHANGES of them
#: change the journal.
N_COMMITS = 50
JOURNAL_CHANGES = 5
MEASURE_PREFIX = "measure/"
MEASURE_FILE = "cold-measure.json"
MEASURE_SCHEMA = "df/stage3-cold-measure/v1"
#: What the measured pass is and is not (named in every record).
MEASURE_NOTES = (
    "маршрут trust-status (op: trust без истории политики по эпохам: маршрут "
    "merge её добавляет, его покрывает запас 2× DEADLINE_v2); холодный путь "
    "нового корня, а не смены admin_digest (тот же полный проход §6.1); якоря "
    "новых эпох — SHA df-trust, не коммиты активации approval-policy; потолок "
    "прохода — DEADLINE проверяющего 150 с: дольше — unavailable, m_seconds "
    "null, запасной вариант выбирает владелец"
)
#: Seconds between the write calls that create content (GitHub's secondary
#: rate limit on content creation).
WRITE_PERIOD = 1.0
#: Seconds for a moved ref to read back, polled every `POLL`.
REF_WAIT = 60.0
POLL = 2.0
#: `ps` of every process's uid, every 100 ms (BSD and procps syntax).
PS_ARGV = ("/bin/ps", "-A", "-o", "uid=")
PS_PERIOD = 0.1
#: The checker's and the stand's own lazy imports (the governance group).
STAND_IMPORTS = (*live_stand.STAND_IMPORTS, "governance.act_epochs")
CREDENTIALS = frozenset({"history", "machine", "app", "human", "derived"})
TRUST_SOURCE = ops.TRUST_SOURCE_PROBLEM.split(":", 1)[0]
_MEASURE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", re.ASCII)


def not_verified(reason: str) -> dict[str, object]:
    """A row's result that is not `pass`: `run_row` keeps a row's own
    `status` over its `pass`, so the row is neither passed nor failed."""
    return {"status": NOT_VERIFIED, "reason": reason}


# ---------- the request table (spec 6.1) ----------


@dataclass(frozen=True)
class TableRow:
    """One required request: who reads it, with which credential, and what
    a refusal gives. `request` is an API path template over the stand's
    facts (`fill`); a row not called live says why."""

    key: str
    request: str
    credential: str
    reader: str
    on_refusal: str
    self_report: bool = False
    live: bool = True
    why_not_live: str = ""


UNAVAILABLE = "Unavailable, без анонимного отката"
REQUEST_TABLE: tuple[TableRow, ...] = (
    TableRow(
        "trust.repository",
        "repositories/{trust_id}",
        "history",
        "act_trust._named; df-precheck",
        UNAVAILABLE,
    ),
    TableRow(
        "policy.repository",
        "repositories/{policy_id}",
        "history",
        "act_trust._named; издатель pin_policy (P по id)",
        UNAVAILABLE,
    ),
    TableRow(
        "trust.branch",
        "repos/{trust}/branches/{trust_branch}",
        "history",
        "издатель: голова H; act_trust.read_trust_snapshot",
        UNAVAILABLE,
    ),
    TableRow(
        "trust.commit",
        "repos/{trust}/commits/{trust_head}",
        "history",
        "act_trust._chain (first-parent по SHA)",
        "404/422 — history_unverifiable; иначе Unavailable",
    ),
    TableRow(
        "trust.source_env",
        "repos/{trust}/contents/source.env?ref={trust_head}",
        "history",
        "act_trust._source_at",
        "нет файла — source_env_invalid; иначе Unavailable",
    ),
    TableRow(
        "trust.journal",
        "repos/{trust}/contents/epochs.yaml?ref={trust_head}",
        "history",
        "act_trust._journal_versions",
        "разбор — journal_parse; иначе Unavailable",
    ),
    TableRow(
        "trust.self_report.history",
        "repositories/{trust_id}",
        "history",
        "_history_self_report (df-precheck); history.self_report (издатель)",
        "нет permissions или не bool — Unavailable; true — token_write",
        self_report=True,
    ),
    TableRow(
        "policy.self_report.history",
        "repositories/{policy_id}",
        "history",
        "_history_self_report (df-precheck); history.self_report (издатель)",
        "нет permissions или не bool — Unavailable; true — token_write",
        self_report=True,
    ),
    TableRow(
        "trust.rules",
        "repos/{trust}/rules/branches/{trust_branch}",
        "history",
        "act_trust._rulesets",
        f"{UNAVAILABLE}; нет обязательных правил — ruleset_missing",
    ),
    TableRow(
        "policy.rules",
        "repos/{policy}/rules/branches/{policy_branch}",
        "history",
        "act_trust._rulesets",
        f"{UNAVAILABLE}; нет обязательных правил — ruleset_missing",
    ),
    TableRow(
        "trust.settings",
        "repos/{trust}",
        "history",
        "act_trust._rebase (allow_rebase_merge)",
        "поле скрыто — непроверенная предпосылка allow_rebase_merge@df-trust",
    ),
    TableRow(
        "policy.settings",
        "repos/{policy}",
        "history",
        "act_trust._rebase (allow_rebase_merge)",
        "поле скрыто — непроверенная предпосылка allow_rebase_merge@approval-policy",
    ),
    TableRow(
        "policy.branch",
        "repos/{policy}/branches/{policy_branch}",
        "history",
        "издатель pin_policy: голова P",
        UNAVAILABLE,
    ),
    TableRow(
        "policy.commit",
        "repos/{policy}/commits/{policy_head}",
        "history",
        "act_premerge: история политики до P по SHA",
        "объекты P недоступны — консервативное событие (К); иначе Unavailable",
    ),
    TableRow(
        "trust.self_report.machine",
        "repositories/{trust_id}",
        "machine",
        "df-exec load_trust (act_perimeter.permissions_refusal)",
        "нет permissions или не bool — Unavailable; право записи — отказ",
        self_report=True,
    ),
    TableRow(
        "policy.self_report.machine",
        "repositories/{policy_id}",
        "machine",
        "df-exec load_trust (act_perimeter.permissions_refusal)",
        "нет permissions или не bool — Unavailable; право записи — отказ",
        self_report=True,
    ),
    TableRow(
        "app.installations",
        "app/installations",
        "app",
        "издатель self-check (JWT, затем /installation/repositories)",
        "установка на источнике доверия — нарушение; ошибка — отказ операции",
        live=False,
        why_not_live="через операцию издателя self-check (Т4, set_up)",
    ),
    TableRow(
        "derived.read_token",
        "—",
        "derived",
        "df-review read-token; df-precheck предпроверка",
        "покрыт проверкой установок App издателем в той же операции",
        live=False,
        why_not_live="самоотчёт производным токеном не выполняется (спека 6.1)",
    ),
    TableRow(
        "human.perimeter",
        "все вызовы setup-trust check",
        "human",
        "setup-trust check (человек, временный PAT)",
        "403/404 — не проверено, не «ок»",
        live=False,
        why_not_live="вызовы и их коды — в отчёте Т1 (`calls`)",
    ),
)


def fill(row: TableRow, facts: Mapping[str, str]) -> str:
    """The API path of `row` (no leading `/`); a missing fact is `KeyError`."""
    return row.request.format_map(dict(facts))


def self_report_problem(body: object) -> str | None:
    """Spec 6.1: `permissions` with `push`, `maintain`, `admin` each a
    literal `false`; anything else is named (never read as `false`)."""
    perms = body.get("permissions") if isinstance(body, dict) else None
    if not isinstance(perms, dict):
        return "нет permissions"
    for name in ("admin", "maintain", "push"):
        if perms.get(name) is not False:
            return f"permissions.{name} = {perms.get(name)!r}"
    return None


def judge(row: TableRow, status: int | None, body: object) -> str | None:
    """A live row passes on `200` with a JSON body (and a clean self-report
    where it is one); otherwise the problem."""
    if status != 200:
        return f"HTTP {'нет' if status is None else status}"
    if body is None:
        return "тело не JSON"
    return self_report_problem(body) if row.self_report else None


def get(transport: Transport, path: str) -> tuple[int | None, object]:
    """`GET <API>/<path>`: the status (`None`: not delivered) and the JSON
    body (`None`: none). No exception text is kept (it may carry a URL)."""
    try:
        status, _, raw = transport("GET", f"{github.API}/{path}", {}, None)
    except Exception:  # noqa: BLE001 - not delivered
        return None, None
    try:
        return status, json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        return status, None


# ---------- the measurement branch ----------


def measure_branch(tag: str) -> str:
    """The run's measurement branch of `df-roles-probe-trust`."""
    if _MEASURE_TAG.fullmatch(tag) is None:
        raise ValueError(f"measure: тег {tag!r}")
    return f"{MEASURE_PREFIX}{tag}"


def ref_path(full_name: str, branch: str, tag: str) -> str:
    """The one ref path the write token may touch: `measure/<tag>`."""
    if branch != measure_branch(tag):
        raise ValueError(f"запись только в {measure_branch(tag)}, не в {branch}")
    return f"/repos/{full_name}/git/refs/heads/{branch}"


def journal_steps(links: int, changes: int) -> frozenset[int]:
    """Which of `links` new commits change the journal: `changes` spread
    evenly, never the first (it follows the trusted head)."""
    if links <= changes:
        raise ValueError(f"{links} звеньев на {changes} изменений журнала")
    return frozenset((i + 1) * links // (changes + 1) for i in range(changes))


def next_journal(epochs: Sequence[Epoch], anchor: str, at: str) -> tuple[Epoch, ...]:
    """The one allowed transition (`act_epochs.check_transition`): close the
    last open epoch at `anchor`, add the next one anchored there."""
    from governance.act_epochs import Epoch  # the governance group, lazily

    closed = replace(
        epochs[-1],
        last_confirmed=anchor,
        valid_until=at,
        closed_reason="df stage3 cold measure",
    )
    return (*epochs[:-1], closed, Epoch(len(epochs) + 1, anchor))


def render_journal(epochs: Sequence[Epoch]) -> str:
    """`epochs.yaml` of `epochs` (strings quoted, as the human writes it)."""
    lines = ["schema: approval-policy-epochs/v1", "epochs:"]
    for e in epochs:
        lines += [f"  - epoch: {e.epoch}", f'    anchor: "{e.anchor}"']
        for key in ("last_confirmed", "valid_until", "closed_reason"):
            value = getattr(e, key)
            if value is not None:
                lines.append(f'    {key}: "{value}"')
    return "\n".join(lines) + "\n"


def rewind_target(base: str, commits: Sequence[str], steps: frozenset[int]) -> str:
    """Where Т6 moves the branch back to: the parent of the last journal
    change (a SHA the cold pass read)."""
    last = max(steps)
    return base if last == 0 else commits[last - 1]


# ---------- the request count and the `ps` span ----------


def core_usage(body: object) -> tuple[int, int] | None:
    """`(used, reset)` of `GET /rate_limit`'s core quota, else `None`."""
    res = body.get("resources") if isinstance(body, dict) else None
    core = res.get("core") if isinstance(res, dict) else None
    if not isinstance(core, dict):
        return None
    used, reset = core.get("used"), core.get("reset")
    if type(used) is not int or type(reset) is not int:
        return None
    return used, reset


def requests_between(
    before: tuple[int, int] | None, after: tuple[int, int] | None
) -> int | None:
    """Requests counted against the quota between two readings of one
    window; `None` when the window reset or a reading is missing."""
    if before is None or after is None or before[1] != after[1]:
        return None
    count = after[0] - before[0]
    return count if count >= 0 else None


def uids_of(text: str) -> set[int]:
    """The uids of `ps -A -o uid=` output."""
    return {int(w) for w in text.split() if w.isascii() and w.isdigit()}


@dataclass
class UidSpan:
    """While running, `ps` every `period`: the first and the last time a
    process of `uid` was seen (the checker's run of `op: trust`)."""

    uid: int
    period: float = PS_PERIOD
    first: float | None = None
    last: float | None = None
    samples: int = 0
    error: str | None = None
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def note(self, uids: set[int], now: float) -> None:
        self.samples += 1
        if self.uid in uids:
            self.first = now if self.first is None else self.first
            self.last = now

    @property
    def seconds(self) -> float | None:
        """Last minus first sighting plus one period; `None` if never seen."""
        if self.first is None or self.last is None:
            return None
        return round(self.last - self.first + self.period, 3)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                done = subprocess.run(
                    PS_ARGV, capture_output=True, text=True, timeout=5, check=False
                )
                self.note(uids_of(done.stdout), time.monotonic())
            except (OSError, subprocess.SubprocessError) as exc:
                self.error = type(exc).__name__
            self._stop.wait(self.period)

    @contextmanager
    def running(self) -> Iterator[UidSpan]:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self._stop.set()
            thread.join(10)


# ---------- the measurement record (Task 16 reads it) ----------


@dataclass(frozen=True)
class ColdMeasure:
    """`cold-measure.json` of one cell. `m_seconds` is the conservative M of
    owner decision 1: the whole `trust-status` call, which contains `op:
    trust` (`op_trust_seconds`, the `ps` span of `df-precheck`, is for
    reference). `requests`: the history token's core quota used during the
    call (publisher and checker; the quota is per user, so an upper bound);
    `None` when the window reset."""

    cell: str
    tag: str
    source_sha: str
    admin_digest: str
    branch: str
    tip: str
    n_commits: int
    journal_changes: int
    base_links: int
    completed: bool
    state: str | None
    reason: str | None
    last_epoch: int | None
    findings: tuple[Mapping[str, str], ...]
    op_trust_seconds: float | None
    trust_status_seconds: float
    requests: int | None
    measured_at: str
    route: str = "trust-status"
    cold: str = "new_root"
    notes: str = ""

    @property
    def m_seconds(self) -> float | None:
        """M of this cell, `None` when the pass did not complete."""
        return self.trust_status_seconds if self.completed else None

    def to_json(self) -> str:
        data = {"schema": MEASURE_SCHEMA, **asdict(self), "m_seconds": self.m_seconds}
        data["findings"] = [dict(f) for f in self.findings]
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_MEASURE_TYPES: Mapping[str, tuple[type, ...]] = {
    "cell": (str,),
    "tag": (str,),
    "source_sha": (str,),
    "admin_digest": (str,),
    "branch": (str,),
    "tip": (str,),
    "n_commits": (int,),
    "journal_changes": (int,),
    "base_links": (int,),
    "completed": (bool,),
    "state": (str, type(None)),
    "reason": (str, type(None)),
    "last_epoch": (int, type(None)),
    "findings": (list,),
    "op_trust_seconds": (float, int, type(None)),
    "trust_status_seconds": (float, int),
    "requests": (int, type(None)),
    "measured_at": (str,),
    "route": (str,),
    "cold": (str,),
    "notes": (str,),
}


def parse_measure(text: str) -> ColdMeasure:
    """`cold-measure.json` back, every field typed; `ValueError` else."""
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("schema") != MEASURE_SCHEMA:
        raise ValueError(f"не {MEASURE_SCHEMA}")
    if set(data) != {"schema", "m_seconds", *_MEASURE_TYPES}:
        raise ValueError(f"поля {sorted(data)}")
    for key, kinds in _MEASURE_TYPES.items():
        value = data[key]
        strict_bool = isinstance(value, bool) and bool not in kinds
        if strict_bool or not isinstance(value, kinds):
            raise ValueError(f"{key}: {value!r}")
    if data["trust_status_seconds"] < 0:
        raise ValueError("trust_status_seconds < 0")
    findings = tuple(
        {str(k): str(v) for k, v in f.items()}
        for f in data["findings"]
        if isinstance(f, dict)
    )
    if len(findings) != len(data["findings"]):
        raise ValueError("findings: не объекты")
    values = {k: data[k] for k in _MEASURE_TYPES} | {"findings": findings}
    measure = ColdMeasure(**values)
    if data["m_seconds"] != measure.m_seconds:
        raise ValueError("m_seconds не по правилу")
    return measure


# ---------- bootstrap order, the App finding, the trust-status ----------


def bootstrap_problems(links: Sequence[tuple[str, int]]) -> list[str]:
    """Spec 6.2 items 1 and 4 over `(sha, merged PRs)` from the root: the
    initial base is no PR merge, every later link arrived by a merged PR."""
    if not links:
        return ["пустая история"]
    problems = [f"{sha[:12]}: начальная база — мерж PR" for sha, n in links[:1] if n]
    problems += [f"{sha[:12]}: не через PR" for sha, n in links[1:] if not n]
    return problems


def trust_source_problems(problems: Sequence[str]) -> list[str]:
    """`self-check`'s problems of an App installation on a trust source."""
    return [p for p in problems if p.startswith(f"{TRUST_SOURCE}:")]


def app_fixture(real: TrustBinding, product: Product) -> TrustBinding:
    """Т4's binding: the product stand (where the App is installed) named as
    `df-trust`; another root than `real`, so nothing of it moves."""
    return replace(
        real,
        df_trust_repository_id=product.repository_id,
        df_trust_full_name=product.full_name,
    )


def status_record(status: TrustStatus | Refused) -> dict[str, object]:
    """A `trust-status` reading for the evidence (parsed values only)."""
    if isinstance(status, Refused):
        return {"state": None, "reason": status.reason}
    return {
        "state": status.state,
        "reason": status.reason,
        "snapshot_sha": status.snapshot_sha,
        "last_epoch": status.last_epoch,
        "findings": [dict(f) for f in status.findings],
        "unverified": list(status.unverified),
    }


# ---------- secrets, arguments, the stand ----------


@dataclass(frozen=True)
class TrustSecrets:
    """The history token's file (root reads it for `setup-trust write`) and
    the values of the stage's own secrets (never in `repr`)."""

    history_file: Path
    history_token: str = field(repr=False)
    perimeter_pat: str = field(repr=False)
    write_token: str | None = field(repr=False)


def _secret(path: Path) -> str:
    try:
        value = path.read_text().strip()
    except OSError as exc:
        infra(f"секрет {path.name}: {type(exc).__name__}")
        raise
    if not value:
        infra(f"секрет {path.name} пуст")
    GUARD.add(value)
    return value


def read_trust_secrets(directory: Path) -> TrustSecrets:
    """The stage's secret files (after the gate); each value joins `GUARD`.
    The write token is optional (Т5, Т6 are `not_verified` without it)."""
    write = directory / WRITE_FILE
    return TrustSecrets(
        directory / HISTORY_FILE,
        _secret(directory / HISTORY_FILE),
        _secret(directory / PAT_FILE),
        _secret(write) if write.is_file() else None,
    )


@dataclass(frozen=True)
class TrustArgs:
    """The stand phase's arguments (the workflow's variables)."""

    out: Path
    secrets: Path
    source_sha: str
    repo_id: int
    app_id: int
    installation_id: int
    policy_repo_id: int
    trust_repo_id: int
    trust_branch: str
    tag: str

    def stand_args(self) -> StandArgs:
        """The 2b stand's arguments (the product stand)."""
        return StandArgs(
            self.out,
            self.secrets,
            self.source_sha,
            self.repo_id,
            self.app_id,
            self.installation_id,
            self.tag,
        )


@dataclass
class TrustContext:
    """What the trust rows share: the arguments, the secrets, the bound
    binding (Т1), the report of `setup-trust check`, the measurement
    branch's commits (Т5)."""

    args: TrustArgs
    secrets: TrustSecrets
    binding: TrustBinding | None = None
    calls: tuple[tuple[str, int | None], ...] = ()
    base: str = ""
    commits: list[str] = field(default_factory=list)
    steps: frozenset[int] = frozenset()
    created: bool = False
    admin_digest: str = ""
    t5_findings: frozenset[tuple[str | None, str | None]] = frozenset()
    t5_requests: int | None = None

    @property
    def history(self) -> Transport:
        return history.history_transport(self.secrets.history_token)

    def bound(self) -> TrustBinding:
        if self.binding is None:
            raise RowFailed("привязки нет (Т1)")
        return self.binding


@dataclass
class TrustStand(Stand):
    """The 2b stand with the trust rows' context (rows 9-12 take it as a
    `Stand`)."""

    trust: TrustContext | None = None


def trust_stand(base: Stand, ctx: TrustContext) -> TrustStand:
    """`base`'s fields, unchanged, and `ctx`."""
    values = {f.name: getattr(base, f.name) for f in fields(Stand)}
    return TrustStand(**values, trust=ctx)


def ctx_of(st: Stand) -> TrustContext:
    if not isinstance(st, TrustStand) or st.trust is None:
        raise TypeError("строка доверия на стенде без контекста доверия")
    return st.trust


def darkfactory(st: Stand) -> list[str]:
    """`sudo -n darkfactory` as the operator runs it."""
    return [SUDO, "-n", str(st.layout.launcher)]


def read_status(st: Stand, root: tuple[str, int, str]) -> TrustStatus | Refused:
    """Root's `trust-status` of `root` (the real call; a refusal named)."""
    try:
        return setup_trust.read_trust_status(st.layout, root, purpose="live-trust")
    except OSError as exc:
        return Refused(f"нет ответа: {type(exc).__name__}")
    except request.BadResponse as exc:
        return Refused(f"ответ не сверен: {exc}")


def bind(st: Stand, binding: TrustBinding) -> None:
    """A fixture `trust.toml` (as root writes it; Т4, Т5)."""
    write_etc(st.layout, "trust.toml", dump_trust(binding))


# ---------- Т1 bootstrap order and the publishable content ----------


def links_of(transport: Transport, full_name: str, head: str) -> list[str]:
    """First-parent links from the root to `head` (history token)."""
    chain: list[str] = []
    sha: str | None = head
    while sha is not None:
        if len(chain) > N_COMMITS * 4:
            raise RowFailed(f"{full_name}: история длиннее {N_COMMITS * 4}")
        status, body = get(transport, f"repos/{full_name}/commits/{sha}")
        parents = body.get("parents") if isinstance(body, dict) else None
        if status != 200 or not isinstance(parents, list):
            raise GitHubUnavailable(f"{full_name}@{sha[:12]}: HTTP {status}")
        chain.append(sha)
        first = parents[0] if parents else None
        sha = first.get("sha") if isinstance(first, dict) else None
    return chain[::-1]


def merged_prs(transport: Transport, full_name: str, sha: str) -> int:
    """How many merged PRs GitHub lists for commit `sha`."""
    status, body = get(transport, f"repos/{full_name}/commits/{sha}/pulls")
    if status != 200 or not isinstance(body, list):
        raise GitHubUnavailable(f"{full_name}@{sha[:12]}/pulls: HTTP {status}")
    return sum(1 for pr in body if isinstance(pr, dict) and pr.get("merged_at"))


def repo_facts(transport: Transport, repo_id: int) -> tuple[str, str]:
    """`(full_name, default_branch)` of a repository by id."""
    status, body = get(transport, f"repositories/{repo_id}")
    if status != 200 or not isinstance(body, dict):
        raise GitHubUnavailable(f"repositories/{repo_id}: HTTP {status}")
    return str(body["full_name"]), str(body["default_branch"])


def head_of_branch(transport: Transport, full_name: str, branch: str) -> str:
    got = history.read_branch_head(transport, full_name, branch)
    if not isinstance(got, str):
        raise GitHubUnavailable(got.reason)
    return got


def row_bootstrap(st: Stand) -> dict[str, object]:
    """Т1 (module doc)."""
    ctx = ctx_of(st)
    a = ctx.args
    templates = as_operator(
        [str(st.layout.launcher), "setup-trust", "check", "--templates"]
    )
    check(templates, "setup-trust check --templates")
    published = templates.stdout.decode("utf-8")
    st.record("t1-publishable.txt", published)
    expect(
        setup_trust.show_publishable() in published,
        "показ публикуемого не равен шаблонам setup_trust",
    )
    order: dict[str, object] = {}
    for role, repo_id, branch in (
        ("approval-policy", a.policy_repo_id, None),
        ("df-trust", a.trust_repo_id, a.trust_branch),
    ):
        full, default = repo_facts(ctx.history, repo_id)
        head = head_of_branch(ctx.history, full, branch or default)
        links = [
            (sha, merged_prs(ctx.history, full, sha))
            for sha in links_of(ctx.history, full, head)
        ]
        order[role] = {
            "repository": full,
            "branch": branch or default,
            "links": [[sha, n] for sha, n in links],
            "problems": bootstrap_problems(links),
        }
    st.record("t1-bootstrap-order.json", order)
    bad = {r: o["problems"] for r, o in order.items() if isinstance(o, dict)}
    expect(not any(bad.values()), f"bootstrap-порядок: {bad}")
    report = perimeter_check(st)
    write_trust(st, report)
    return {"bootstrap": "ok", "root": list(ctx.bound().root_id), "run": st.run_id}


def perimeter_check(st: Stand) -> Path:
    """`setup-trust check` as the operator with the owner's PAT on stdin:
    exit 0, no stop; the report (no token in it) recorded."""
    ctx = ctx_of(st)
    a = ctx.args
    human = st.rw.work / "human"
    human.mkdir(mode=0o700, exist_ok=True)
    os.chown(human, *_operator_ids())
    out = human / "report.json"
    argv = [str(st.layout.launcher), "setup-trust", "check"]
    argv += ["--policy-repo-id", str(a.policy_repo_id)]
    argv += ["--trust-repo-id", str(a.trust_repo_id)]
    argv += ["--trust-branch", a.trust_branch, "--out", str(out)]
    done = as_operator(argv, stdin=f"{ctx.secrets.perimeter_pat}\n".encode())
    text = out.read_text() if out.is_file() else ""
    st.record("t1-perimeter-report.json", text or {"absent": shown(done)})
    try:
        report = setup_trust.parse_report(text.encode()) if text else None
    except (ValueError, TypeError) as exc:
        raise RowFailed(f"отчёт setup-trust check не разобран: {exc}") from None
    ctx.calls = () if report is None else report.calls
    if done.returncode != 0 or report is None or not report.ok:
        stops = None if report is None else [asdict(s) for s in report.stops]
        raise RowFailed(f"setup-trust check: код {done.returncode}; стоп {stops}")
    return out


def _operator_ids() -> tuple[int, int]:
    pw = pwd.getpwnam(OPERATOR)
    return pw.pw_uid, pw.pw_gid


def write_trust(st: Stand, report: Path) -> None:
    """`sudo darkfactory setup-trust write` with the history token: done,
    the binding is the arguments' root; then the bound run record."""
    ctx = ctx_of(st)
    a = ctx.args
    digest = "sha256:" + _sha256(report)
    argv = [*darkfactory(st), "setup-trust", "write"]
    argv += ["--policy-repo-id", str(a.policy_repo_id)]
    argv += ["--trust-repo-id", str(a.trust_repo_id)]
    argv += ["--trust-branch", a.trust_branch, "--report", str(report)]
    argv += ["--report-digest", digest]
    argv += ["--history-token-file", str(ctx.secrets.history_file)]
    done = as_operator(argv)
    st.record("t1-setup-trust-write.txt", shown(done))
    expect(done.returncode == 0, f"setup-trust write: {shown(done)}")
    binding = load_trust(st.layout.etc / "trust.toml")
    expect(binding is not None, "trust.toml не записан")
    assert binding is not None
    root = ("github.com", a.trust_repo_id, a.trust_branch)
    expect(binding.root_id == root, f"корень {binding.root_id}, ожидался {root}")
    ctx.binding = binding
    st.run_id = live_stand.register_run(st, binding.root_id)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- Т2 the request table ----------


def table_facts(st: Stand) -> dict[str, str]:
    """The facts the table's requests are filled with (history token)."""
    ctx = ctx_of(st)
    b = ctx.bound()
    policy, policy_branch = repo_facts(ctx.history, b.approval_policy_repository_id)
    return {
        "trust_id": str(b.df_trust_repository_id),
        "policy_id": str(b.approval_policy_repository_id),
        "trust": b.df_trust_full_name,
        "policy": policy,
        "trust_branch": quote(b.df_trust_branch, safe=""),
        "policy_branch": quote(policy_branch, safe=""),
        "trust_head": head_of_branch(
            ctx.history, b.df_trust_full_name, b.df_trust_branch
        ),
        "policy_head": head_of_branch(ctx.history, policy, policy_branch),
    }


def row_request_table(st: Stand) -> dict[str, object]:
    """Т2: every live row of `REQUEST_TABLE` with its credential; the
    human's calls from Т1's report; the App's through `self-check`."""
    ctx = ctx_of(st)
    facts = table_facts(st)
    transports = {
        "history": ctx.history,
        "machine": history.history_transport(st.secrets.machine_token),
    }
    rows: list[dict[str, object]] = []
    for row in REQUEST_TABLE:
        entry: dict[str, object] = {
            "key": row.key,
            "request": row.request,
            "credential": row.credential,
            "reader": row.reader,
            "on_refusal": row.on_refusal,
        }
        if row.live:
            status, body = get(transports[row.credential], fill(row, facts))
            entry |= {"status": status, "problem": judge(row, status, body)}
        else:
            entry["live"] = row.why_not_live
        rows.append(entry)
    calls = [[path, status] for path, status in ctx.calls]
    unverified = [c for c in calls if c[1] != 200]
    found = {"rows": rows, "human_calls": calls, "human_not_200": unverified}
    st.record("t2-request-table.json", found)
    bad = {str(r["key"]): r["problem"] for r in rows if r.get("problem")}
    expect(bad == {}, f"таблица запросов: {bad}")
    problem = human_calls_problem(ctx.calls)
    if problem is not None:
        return not_verified(problem)
    return {"rows": len(rows), "human_calls": len(calls)}


def human_calls_problem(calls: Sequence[tuple[str, int | None]]) -> str | None:
    """Spec 6.2: a call of the human's check that was not `200` is «не
    проверено», never «ок»; no call at all verified nothing."""
    if not calls:
        return "вызовов проверки периметра человеком нет"
    missed = [[path, status] for path, status in calls if status != 200]
    return f"вызовы человека не 200: {missed}" if missed else None


# ---------- Т3 trust-status, Т4 App on a trust repo ----------


def row_trust_status(st: Stand) -> dict[str, object]:
    """Т3: `trust-status` of the bound root: `clear` on the trusted head."""
    ctx = ctx_of(st)
    b = ctx.bound()
    head = head_of_branch(ctx.history, b.df_trust_full_name, b.df_trust_branch)
    status = read_status(st, b.root_id)
    found = {"head": head, **status_record(status)}
    st.record("t3-trust-status.json", found)
    expect(isinstance(status, TrustStatus), f"trust-status: {found}")
    assert isinstance(status, TrustStatus)
    expect(status.state == "clear", f"не clear: {found}")
    expect(status.snapshot_sha == head, f"снимок {status.snapshot_sha} ≠ {head}")
    return {"state": status.state, "snapshot_sha": head}


def self_check_problems(st: Stand) -> list[str]:
    """The publisher's `self-check` problems (the real chain)."""
    raw = setup_agent.publisher_self_check(st.layout, purpose="live-trust")
    resp = request.parse_response(raw.decode("utf-8").strip())
    if resp.outcome != "done":
        raise RowFailed(f"self-check: {resp.outcome} {resp.reason}")
    problems = resp.result.get("problems")
    return [str(p) for p in problems] if isinstance(problems, list) else []


def row_app_on_trust(st: Stand) -> dict[str, object]:
    """Т4 (module doc): the violation with the fixture binding, none with
    the real one restored."""
    ctx = ctx_of(st)
    real = ctx.bound()
    original = (st.layout.etc / "trust.toml").read_text()
    try:
        bind(st, app_fixture(real, st.product))
        fixture = self_check_problems(st)
    finally:
        write_etc(st.layout, "trust.toml", original)
    control = self_check_problems(st)
    want = ops.TRUST_SOURCE_PROBLEM.format(id=ctx.args.installation_id, role="df-trust")
    found = {
        "fixture": "trust.toml: df-trust = репо продукта (другой корень)",
        "fixture_problems": fixture,
        "control_problems": control,
    }
    st.record("t4-app-on-trust.json", found)
    expect(want in fixture, f"нет {want}: {fixture}")
    expect(trust_source_problems(control) == [], f"контроль: {control}")
    return {"violation": want}


# ---------- Т5 the cold path measurement, Т6 the rewritten history ----------


@dataclass(frozen=True)
class Timed:
    """One measured `trust-status`."""

    status: TrustStatus | Refused
    seconds: float
    op_trust: float | None
    requests: int | None


def timed_status(st: Stand, root: tuple[str, int, str]) -> Timed:
    """`trust-status` of `root` with its wall time, the `ps` span of
    `df-precheck` and the history token's quota used meanwhile."""
    ctx = ctx_of(st)
    before = core_usage(get(ctx.history, "rate_limit")[1])
    span = UidSpan(pwd.getpwnam(PRECHECK_USER).pw_uid)
    with span.running():
        began = time.monotonic()
        status = read_status(st, root)
        seconds = round(time.monotonic() - began, 3)
    after = core_usage(get(ctx.history, "rate_limit")[1])
    return Timed(status, seconds, span.seconds, requests_between(before, after))


class TrustWriter:
    """The write token on `df-roles-probe-trust`: git objects, and the one
    ref `measure/<tag>` (`ref_path`)."""

    def __init__(self, token: str, full_name: str, tag: str) -> None:
        self.token = token
        self.full_name = full_name
        self.tag = tag

    def call(self, method: str, path: str, body: object | None = None) -> object:
        return github.call(method, path, self.token, body)[1]

    def obj(self, kind: str, body: object) -> str:
        """`POST /git/<kind>`; the new object's sha."""
        made = self.call("POST", f"/repos/{self.full_name}/git/{kind}", body)
        sha = made.get("sha") if isinstance(made, dict) else None
        if not isinstance(sha, str):
            raise GitHubUnavailable(f"git/{kind}: нет sha")
        time.sleep(WRITE_PERIOD)
        return sha

    def ref(self, method: str, sha: str | None = None) -> None:
        branch = measure_branch(self.tag)
        path = ref_path(self.full_name, branch, self.tag)
        if method == "POST":
            body: object = {"ref": f"refs/heads/{branch}", "sha": sha}
            self.call("POST", f"/repos/{self.full_name}/git/refs", body)
        elif method == "PATCH":
            self.call("PATCH", path, {"sha": sha, "force": True})
        else:
            self.call("DELETE", path)


def writer_of(st: Stand) -> TrustWriter | None:
    ctx = ctx_of(st)
    token = ctx.secrets.write_token
    if token is None:
        return None
    return TrustWriter(token, ctx.bound().df_trust_full_name, ctx.args.tag)


def tree_of(transport: Transport, full_name: str, sha: str) -> str:
    status, body = get(transport, f"repos/{full_name}/git/commits/{sha}")
    tree = body.get("tree") if isinstance(body, dict) else None
    got = tree.get("sha") if isinstance(tree, dict) else None
    if status != 200 or not isinstance(got, str):
        raise GitHubUnavailable(f"git/commits/{sha[:12]}: HTTP {status}")
    return got


def journal_at(transport: Transport, full_name: str, sha: str) -> tuple[Epoch, ...]:
    from governance.act_epochs import parse_journal  # the governance group

    status, body = get(transport, f"repos/{full_name}/contents/epochs.yaml?ref={sha}")
    content = body.get("content") if isinstance(body, dict) else None
    if status != 200 or not isinstance(content, str):
        raise GitHubUnavailable(f"epochs.yaml@{sha[:12]}: HTTP {status}")
    return parse_journal(base64.b64decode(content).decode("utf-8"))


def build_measure(st: Stand, w: TrustWriter) -> tuple[str, int]:
    """The measurement branch (module doc): `(tip, last epoch)`."""
    ctx = ctx_of(st)
    b = ctx.bound()
    base = head_of_branch(ctx.history, b.df_trust_full_name, b.df_trust_branch)
    have = len(links_of(ctx.history, b.df_trust_full_name, base))
    if have >= N_COMMITS - JOURNAL_CHANGES:
        infra(f"доверенная ветка уже {have} звеньев: N = {N_COMMITS} не построить")
    steps = journal_steps(N_COMMITS - have, JOURNAL_CHANGES)
    epochs = journal_at(ctx.history, b.df_trust_full_name, base)
    parent, tree = base, tree_of(ctx.history, b.df_trust_full_name, base)
    at = datetime.now(UTC).isoformat(timespec="seconds")
    ctx.base, ctx.steps = base, steps
    for i in range(N_COMMITS - have):
        if i in steps:
            epochs = next_journal(epochs, parent, at)
            path, text = "epochs.yaml", render_journal(epochs)
        else:
            path, text = "measure.txt", f"df stage3 cold measure {w.tag}: {i}\n"
        blob = {"path": path, "mode": "100644", "type": "blob", "content": text}
        tree = w.obj("trees", {"base_tree": tree, "tree": [blob]})
        message = f"df stage3 cold measure {w.tag}: {i + 1}"
        parent = w.obj(
            "commits", {"message": message, "tree": tree, "parents": [parent]}
        )
        ctx.commits.append(parent)
    w.ref("POST", parent)
    ctx.created = True
    return parent, epochs[-1].epoch


def wait_ref(st: Stand, full_name: str, branch: str, sha: str) -> None:
    ctx = ctx_of(st)
    deadline = time.monotonic() + REF_WAIT
    while history.read_branch_head(ctx.history, full_name, branch) != sha:
        if time.monotonic() >= deadline:
            infra(f"{branch} не стала {sha[:12]} за {REF_WAIT} с")
        time.sleep(POLL)


REQUIRED_TRUSTED_RULES = frozenset({"pull_request", "non_fast_forward"})


def scope_problems(
    policy: tuple[int | None, object], rules: tuple[int | None, object]
) -> list[str]:
    """The write token's scope (module doc): `(status, body)` of its own
    `GET /repositories/{policy_id}` and of the trusted branch's rules."""
    problems: list[str] = []
    status, body = policy
    perms = body.get("permissions") if isinstance(body, dict) else None
    push = perms.get("push") if isinstance(perms, dict) else None
    if status != 200 or push is not False:
        problems.append(f"токен записи: approval-policy push={push!r} (HTTP {status})")
    status, body = rules
    types = (
        {r.get("type") for r in body if isinstance(r, dict)}
        if isinstance(body, list)
        else set()
    )
    missing = sorted(REQUIRED_TRUSTED_RULES - types)
    if status != 200 or missing:
        problems.append(f"доверенная ветка: нет правил {missing} (HTTP {status})")
    return problems


def write_scope(st: Stand, w: TrustWriter) -> list[str]:
    """`scope_problems` read live, recorded before any write."""
    ctx = ctx_of(st)
    b = ctx.bound()
    token = history.history_transport(w.token)
    policy = get(token, f"repositories/{ctx.args.policy_repo_id}")
    branch = quote(b.df_trust_branch, safe="")
    rules = get(ctx.history, f"repos/{b.df_trust_full_name}/rules/branches/{branch}")
    problems = scope_problems(policy, rules)
    st.record(
        "t5-write-scope.json",
        {
            "policy_status": policy[0],
            "rules_status": rules[0],
            "rules": rules[1],
            "problems": problems,
        },
    )
    return problems


def row_cold_measure(st: Stand) -> dict[str, object]:
    """Т5 (module doc): the pass completes when `trust-status` answers with
    the tip as its snapshot and the journal's last epoch; its findings
    are the unprotected branch's (`ruleset_missing`)."""
    ctx = ctx_of(st)
    w = writer_of(st)
    if w is None:
        return not_verified(f"нет {WRITE_FILE}: ветку-замер записать нечем")
    scope = write_scope(st, w)
    if scope:
        return not_verified(f"область токена записи не доказана: {scope}")
    tip, last = build_measure(st, w)
    branch = measure_branch(ctx.args.tag)
    wait_ref(st, w.full_name, branch, tip)
    measured = replace(ctx.bound(), df_trust_branch=branch)
    bind(st, measured)
    t = timed_status(st, measured.root_id)
    status = status_record(t.status)
    completed = (
        isinstance(t.status, TrustStatus)
        and t.status.snapshot_sha == tip
        and t.status.last_epoch == last
    )
    result = ColdMeasure(
        cell=OS_DIR,
        tag=ctx.args.tag,
        source_sha=ctx.args.source_sha,
        admin_digest=ctx.admin_digest,
        branch=branch,
        tip=tip,
        n_commits=N_COMMITS,
        journal_changes=JOURNAL_CHANGES,
        base_links=N_COMMITS - len(ctx.commits),
        completed=completed,
        state=cast(str | None, status["state"]),
        reason=cast(str | None, status["reason"]),
        last_epoch=t.status.last_epoch if isinstance(t.status, TrustStatus) else None,
        findings=t.status.findings if isinstance(t.status, TrustStatus) else (),
        op_trust_seconds=t.op_trust,
        trust_status_seconds=t.seconds,
        requests=t.requests,
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        notes=MEASURE_NOTES,
    )
    ctx.t5_findings = kinds_of(result.findings)
    ctx.t5_requests = t.requests
    st.record(MEASURE_FILE, result.to_json())
    expect(completed, f"холодный проход не завершён: {status}")
    return {"m_seconds": result.m_seconds, "requests": t.requests}


def row_rewritten(st: Stand) -> dict[str, object]:
    """Т6 (module doc): `not_descendant@df-trust` on the rewound snapshot, a
    finding Т5 did not have (Т5's `ruleset_missing` already keeps the root
    from `clear`). The request counts of both passes are evidence only: an
    upper bound proves no lower one."""
    ctx = ctx_of(st)
    w = writer_of(st)
    if w is None:
        return not_verified(f"нет {WRITE_FILE}: историю переписать нечем")
    target = rewind_target(ctx.base, ctx.commits, ctx.steps)
    branch = measure_branch(ctx.args.tag)
    w.ref("PATCH", target)
    wait_ref(st, w.full_name, branch, target)
    root = replace(ctx.bound(), df_trust_branch=branch).root_id
    t = timed_status(st, root)
    links = len(links_of(ctx.history, w.full_name, target))
    found = {
        "target": target,
        "target_links": links,
        "seconds": t.seconds,
        "op_trust_seconds": t.op_trust,
        "requests": t.requests,
        "t5_requests": ctx.t5_requests,
        **status_record(t.status),
    }
    st.record("t6-rewritten.json", found)
    expect(isinstance(t.status, TrustStatus), f"trust-status: {found}")
    assert isinstance(t.status, TrustStatus)
    problem = rewritten_problem(ctx.t5_findings, kinds_of(t.status.findings))
    expect(problem is None, f"{problem}: {found}")
    expect(t.status.snapshot_sha == target, f"снимок не {target}: {found}")
    return {"finding": "not_descendant", "requests": t.requests}


NOT_DESCENDANT = ("not_descendant", "df-trust")


def kinds_of(
    findings: Sequence[Mapping[str, str]],
) -> frozenset[tuple[str | None, str | None]]:
    """`(kind, role)` of each finding."""
    return frozenset((f.get("kind"), f.get("role")) for f in findings)


def rewritten_problem(
    before: frozenset[tuple[str | None, str | None]],
    after: frozenset[tuple[str | None, str | None]],
) -> str | None:
    """Т6's verdict: `not_descendant@df-trust` now, and not already at Т5."""
    if NOT_DESCENDANT not in after:
        return "нет not_descendant@df-trust"
    if NOT_DESCENDANT in before:
        return "not_descendant@df-trust был уже на Т5: не новая находка"
    return None


def drop_measure(st: Stand) -> dict[str, object]:
    """The measurement branch deleted (owner decision 3) and read absent."""
    ctx = ctx_of(st)
    w = writer_of(st) if ctx.created else None
    if w is None:
        return {"created": False, "left": False}
    branch = quote(measure_branch(ctx.args.tag), safe="")
    try:
        w.ref("DELETE")
    except (GitHubRefused, GitHubUnavailable) as exc:
        return {"created": True, "left": True, "problem": type(exc).__name__}
    status, _ = get(ctx.history, f"repos/{w.full_name}/branches/{branch}")
    return {"created": True, "left": status != 404, "read": status}


def row_cleanup(st: Stand) -> dict[str, object]:
    """9: the measurement branch deleted first, then the 2b row 9."""
    measure = drop_measure(st)
    st.record("t9-measure-cleanup.json", measure)
    found = live_stand.row9_cleanup(st)
    expect(measure["left"] is False, f"ветка-замер осталась: {measure}")
    return {**found, "measure_branch": measure}


#: The rows (module doc) and what each needs. The stop rows of 2b need the
#: bound root `clear` (Т3); Т5 rebinds to the measurement root, so it runs
#: after every row of the bound root.
ROWS: tuple[Row, ...] = (
    Row("Т1", "bootstrap-порядок и показ публикуемого", row_bootstrap),
    Row("Т2", "таблица запросов §6.1", row_request_table, ("Т1",)),
    Row("Т3", "trust-status с привязкой", row_trust_status, ("Т1",)),
    Row("Т4", "App на репо доверия — нарушение", row_app_on_trust, ("Т1",)),
    Row("10", "засев и stop-PR (с привязкой)", live_stand.row10_seed, ("Т3",)),
    Row("11", "смена head при удержанном PUT", live_stand.row11_head_change, ("10",)),
    Row("12", "оборванный ответ PUT", live_stand.row12_dropped_answer, ("Т3",)),
    Row("Т5", "замер холодного пути", row_cold_measure, ("Т1",)),
    Row("Т6", "переписанная история df-trust", row_rewritten, ("Т5",)),
)
CLEANUP = Row("9", "уборка (PR, ветки, ветка-замер)", row_cleanup)


# ---------- the phases ----------


def stand_phase(args: TrustArgs) -> int:
    """`stand` (module doc). The evidence is scanned for every in-process
    secret whatever happens."""
    require_ci_root()
    problem = stand_imports_problem(STAND_IMPORTS)
    if problem is not None:
        record(args.out, "stand-imports-refused.json", {"refused": problem})
        infra(problem)
    top = live_top()
    prov, secrets = gate(args.source_sha, args.secrets, top=top, layout=PRODUCTION)
    trust = read_trust_secrets(args.secrets)
    rid = current_id(PRODUCTION)
    assert rid is not None
    installed = InstalledWorld(
        RootWorld(PRODUCTION, FixtureRepo("", prov.fixture_sha), Path(), top, ""), rid
    )
    work = top / "stand"
    work.mkdir(mode=0o755)
    rw = RolesWorld(installed, cast(FakeGitHub, None), work)
    code = EXIT_INFRA
    try:
        try:
            base = live_stand.set_up(rw, args.stand_args(), secrets, private=False)
            gh = install_gh(top)
            check(as_operator([*darkfactory(base), "cli", "set", "gh", str(gh)]), "gh")
            ctx = TrustContext(args, trust, admin_digest=prov.admin_digest)
            st = trust_stand(base, ctx)
            results = run_rows(st, ROWS, CLEANUP)
            revoked = {label: revoke(t) for label, t in st.revocable.items()}
            summary = {
                "tag": args.tag,
                "provenance": prov.to_json(),
                "token_type": secrets.token_type,
                "write_token": trust.write_token is not None,
                "run_record": {
                    "run_id": st.run_id,
                    "root": None if ctx.binding is None else list(ctx.binding.root_id),
                    "fixture": True,
                },
                "rows": results,
                "tokens_revoked": revoked,
            }
            record(args.out, "summary.json", summary)
            code = verdict(results, expected=len(ROWS) + 1)
        finally:
            teardown_roles(PRODUCTION)
            teardown_install(PRODUCTION, args.out / "journals" / "live-installed.jsonl")
            shutil.rmtree(top, ignore_errors=True)
    finally:
        code = scrub(args.out, code)
    return code


def scrub(out: Path, code: int) -> int:
    """Evidence holding a secret is deleted (exit 2)."""
    leaked = live_stand.scan_tree(out, GUARD.values())
    for path in leaked:
        path.unlink()
    if leaked:
        print(f"evidence с секретом удалено: {[str(p) for p in leaked]}", flush=True)
        return EXIT_INFRA
    return code


def wipe_phase(
    out: Path,
    secrets: Path,
    repo_id: int | None,
    trust_repo_id: int | None,
    tag: str | None,
) -> int:
    """`wipe` (always): the measurement branch deleted (best effort), the
    history profile removed, then the 2b wipe (the product stand swept,
    the evidence scanned, the installed copies and secret files removed)."""
    require_ci_root()
    write = secrets / WRITE_FILE
    if trust_repo_id is not None and tag and write.is_file():
        print(f"ветка-замер: {sweep_measure(write, trust_repo_id, tag)}", flush=True)
    shutil.rmtree(PRODUCTION.history_gh, ignore_errors=True)
    return live_stand.wipe_phase(out, secrets, repo_id, tag)


def sweep_measure(token_file: Path, trust_repo_id: int, tag: str) -> str:
    """Delete `measure/<tag>` if present; a word, nothing secret."""
    try:
        token = token_file.read_text().strip()
        full, _ = repo_facts(history.history_transport(token), trust_repo_id)
        w = TrustWriter(token, full, tag)
        w.ref("DELETE")
    except GitHubRefused as exc:
        return "нет ветки" if exc.status in (404, 422) else f"не удалось: {exc.status}"
    except (OSError, ValueError, KeyError, GitHubUnavailable) as exc:
        return f"не удалось: {type(exc).__name__}"
    return "удалена"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="live_trust_stand")
    sub = parser.add_subparsers(dest="phase", required=True)
    install = sub.add_parser("install")
    install.add_argument("--source-sha", required=True)
    install.add_argument("--out", type=Path, required=True)
    stand = sub.add_parser("stand")
    stand.add_argument("--source-sha", required=True)
    stand.add_argument("--out", type=Path, required=True)
    stand.add_argument("--secrets", type=Path, required=True)
    for name in (
        "--repo-id",
        "--app-id",
        "--installation-id",
        "--policy-repo-id",
        "--trust-repo-id",
    ):
        stand.add_argument(name, type=int, required=True)
    stand.add_argument("--trust-branch", required=True)
    stand.add_argument("--tag", required=True)
    wipe = sub.add_parser("wipe")
    wipe.add_argument("--out", type=Path, required=True)
    wipe.add_argument("--secrets", type=Path, required=True)
    wipe.add_argument("--repo-id", type=int)
    wipe.add_argument("--trust-repo-id", type=int)
    wipe.add_argument("--tag")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    """The phase of `argv`; an infrastructure failure is exit 2."""
    ns = parse_args(argv)
    try:
        if ns.phase == "install":
            return live_stand.install_phase(ns.source_sha, ns.out)
        if ns.phase == "wipe":
            return wipe_phase(ns.out, ns.secrets, ns.repo_id, ns.trust_repo_id, ns.tag)
        measure_branch(ns.tag)
        args = TrustArgs(
            ns.out,
            ns.secrets,
            ns.source_sha,
            ns.repo_id,
            ns.app_id,
            ns.installation_id,
            ns.policy_repo_id,
            ns.trust_repo_id,
            ns.trust_branch,
            ns.tag,
        )
        return stand_phase(args)
    except ProvenanceError as exc:
        message = f"{INFRA}: происхождение: {exc}"
        record(ns.out, f"{ns.phase}-provenance-refused.json", {"refused": message})
        print(message, flush=True)
        return EXIT_INFRA
    except pytest.fail.Exception as exc:
        print(str(exc), flush=True)
        return EXIT_INFRA
    except Exception:  # noqa: BLE001 - a crash of the script is infrastructure
        traceback.print_exc()
        return EXIT_INFRA


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
