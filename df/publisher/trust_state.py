"""The publisher's trust state (installer spec 6.1, rev 11; stage 3a, Task 8).

The publisher alone holds durable trust state, in the perimeter database
next to its events: the accepted head of each root (`trust_heads`) and the
observations (`trust_observations`). The checker (`op: trust`) keeps none.
Stdlib only; `df` never imports `governance`.

Invariant I: while a root has an unfinished observation (`open`,
`unresolved`) or a blocking event, it is not `clear` on any route
(`blocked`). Every route that can detect a violation first opens an
observation durably (`open_observation`, its own `COMMIT`), then asks the
checker.

Meta-invariant K: an observation that cannot be completed by reproducing
its route's findings in the context it was created in is completed by a
conservative event (`conservative`), never `clear`. The context is every
immutable input the route reads, digested into `ctx_id`; one `ctx_id` is
used for creation, deduplication, re-evaluation and the registration of
findings.

Every change is one write transaction (`perimeter.write`, `BEGIN
IMMEDIATE`): opening an observation, registering findings (independent of
the head), advancing the head (a CAS on `row_version`, the branch head read
with the lock held), a conservative completion. `TRUST_STEP` is called
inside each before its `COMMIT` (tests crash there).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol, cast

from df.publisher import perimeter
from df.publisher.perimeter import (
    Db,
    Root,
    Snapshot,
    Unavailable,
    known_roots,
    mark_restored,
    restored,
)
from df.publisher.request import Refused
from df.publisher.trust_types import (
    ROUTE_KINDS,
    Finding,
    PolicyPin,
    Route,
    clip_detail,
)
from df.stage2_config import TrustBinding

Outcome = Literal[
    "open", "unresolved", "accepted", "finding_registered", "superseded", "conservative"
]
UNFINISHED: frozenset[Outcome] = frozenset({"open", "unresolved"})
#: The checker's typed statuses that settle an observation without a
#: verdict; every other status follows the life cycle.
OUTCOME_OF_VERDICT: Mapping[str, Outcome] = MappingProxyType(
    {
        "tree_incomplete": "unresolved",
        "unset": "unresolved",
        "unsupported_size": "conservative",
        # a permanent condition of a pinned input (review D-c)
        "irreproducible": "conservative",
    }
)
#: `detected_by` of a conservative completion (K).
CONSERVATIVE_SOURCE = "conservative_completion"
#: The epoch of a registration, read inside the write transaction: the last
#: epoch of the root's verified current snapshot, else `None` (`pending`).
EpochOf = Callable[[sqlite3.Connection, Root], int | None]

_ROOT = "root_host = ? AND root_repository_id = ? AND root_branch = ?"
_OBSERVATION = (
    "SELECT observation_id, root_host, root_repository_id, root_branch, ctx_id, "
    "route, accepted_sha, head_sha, policy_full_name, policy_branch, policy_sha, "
    "row_version, outcome, event_ids, binding, accepted_last_epoch, "
    "accepted_source_digest, accepted_rules, accepted_admin_digest "
    "FROM trust_observations"
)


def _no_step(_name: str) -> None:
    """The default `TRUST_STEP`: nothing."""


#: Called inside each write transaction before its `COMMIT` with the
#: operation's name: `observation`, `findings`, `advance`, `conservative`,
#: `ranges`, `admission`.
TRUST_STEP: Callable[[str], None] = _no_step


# --- the context identity --------------------------------------------------------


def ctx_id(
    route: Route,
    binding: TrustBinding,
    admin_digest: str,
    rules: str,
    accepted: str | None,
    head: str,
    policy: PolicyPin | None,
) -> str:
    """The identity of an observation's context: a digest of its route, all
    parameters of the binding (`history_gh_config_dir` included),
    `admin_digest`, the rules' version and every pinned SHA (A, H or X, and
    P with its coordinates for `merge`)."""
    return _digest(
        {
            "route": route,
            "binding": dataclasses.asdict(binding),
            "admin_digest": admin_digest,
            "rules": rules,
            "accepted": accepted,
            "head": head,
            "policy": None if policy is None else dataclasses.asdict(policy),
        }
    )


def binding_digest(binding: TrustBinding) -> str:
    """The digest of every parameter of the binding."""
    return _digest(dataclasses.asdict(binding))


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- rows ------------------------------------------------------------------------


@dataclass(frozen=True)
class Pinned:
    """A's record as the observation pinned it at creation (review D-a): the
    base the checker needs, reproducible whatever `trust_heads` holds later."""

    sha: str
    last_epoch: int
    source_digest: str
    rules: str
    admin_digest: str


@dataclass(frozen=True)
class Observation:
    """An observation: its root, context, route and pinned inputs (A's
    record in `pinned`, `None` without A)."""

    id: int
    root: Root
    ctx: str
    route: Route
    accepted: str | None
    head: str
    policy: PolicyPin | None
    row_version: int
    pinned: Pinned | None = None


@dataclass(frozen=True)
class Head:
    """A root's accepted head A and the version of the check that accepted
    it (`admin_digest`, rules, binding parameters)."""

    sha: str
    last_epoch: int
    source_digest: str
    admin_digest: str
    rules: str
    binding_digest: str
    row_version: int
    finding_event_id: int | None


@dataclass(frozen=True)
class _Row:
    obs: Observation
    outcome: Outcome
    events: list[int]
    binding: dict[str, object]


def _row(found: tuple) -> _Row:
    (oid, host, repo, branch, ctx, route, accepted, head, name, ref, p_sha,
     version, outcome, events, binding, a_epoch, a_source, a_rules,
     a_admin) = found  # fmt: skip
    policy = None if p_sha is None else PolicyPin(str(name), str(ref), str(p_sha))
    pinned = None
    if accepted is not None:
        pinned = Pinned(
            str(accepted), int(a_epoch), str(a_source), str(a_rules), str(a_admin)
        )
    obs = Observation(
        int(oid),
        (str(host), int(repo), str(branch)),
        str(ctx),
        cast(Route, str(route)),
        None if accepted is None else str(accepted),
        str(head),
        policy,
        int(version),
        pinned,
    )
    return _Row(
        obs, cast(Outcome, str(outcome)), json.loads(events), json.loads(binding)
    )


def _by_id(conn: sqlite3.Connection, root: Root, obs: int) -> _Row:
    found = conn.execute(f"{_OBSERVATION} WHERE observation_id = ?", (obs,)).fetchone()
    if found is None:
        raise ValueError(f"no observation {obs}")
    row = _row(found)
    if row.obs.root != root:
        raise ValueError(f"observation {obs} is not of root {root}")
    return row


def _of_ctx(
    conn: sqlite3.Connection, root: Root, ctx: str, *, latest: bool
) -> _Row | None:
    """The unfinished observation of (`root`, `ctx`); with `latest`, else
    the last one of any outcome."""
    where = f"{_OBSERVATION} WHERE {_ROOT} AND ctx_id = ?"
    found = conn.execute(
        f"{where} AND outcome IN ('open', 'unresolved')", (*root, ctx)
    ).fetchone()
    if found is None and latest:
        found = conn.execute(
            f"{where} ORDER BY observation_id DESC LIMIT 1", (*root, ctx)
        ).fetchone()
    return None if found is None else _row(found)


def outcome_of(conn: sqlite3.Connection, obs: int) -> Outcome:
    """The observation's outcome now."""
    found = conn.execute(
        "SELECT outcome FROM trust_observations WHERE observation_id = ?", (obs,)
    ).fetchone()
    if found is None:
        raise ValueError(f"no observation {obs}")
    return cast(Outcome, str(found[0]))


def unfinished(conn: sqlite3.Connection, root: Root) -> list[Observation]:
    """The root's unfinished observations, in the order of creation."""
    found = conn.execute(
        f"{_OBSERVATION} WHERE {_ROOT} AND outcome IN ('open', 'unresolved') "
        "ORDER BY observation_id",
        root,
    ).fetchall()
    return [_row(f).obs for f in found]


def accepted_head(conn: sqlite3.Connection, root: Root) -> Head | None:
    """The root's `trust_heads` row, if any."""
    found = conn.execute(
        "SELECT sha, last_epoch, source_digest, admin_digest, rules, "
        f"binding_digest, row_version, finding_event_id FROM trust_heads WHERE {_ROOT}",
        root,
    ).fetchone()
    if found is None:
        return None
    sha, epoch, source, admin, rules, binding, version, finding = found
    return Head(
        str(sha), int(epoch), str(source), str(admin), str(rules), str(binding),
        int(version), None if finding is None else int(finding),
    )  # fmt: skip


def head_valid(
    head: Head, binding: TrustBinding, admin_digest: str, rules: str
) -> bool:
    """The check of A still holds: same `admin_digest`, rules and binding
    parameters. Otherwise the next check is cold; A is not erased."""
    return (head.admin_digest, head.rules, head.binding_digest) == (
        admin_digest,
        rules,
        binding_digest(binding),
    )


def stale_context(
    obs: Observation, binding: TrustBinding, admin_digest: str, rules: str
) -> bool:
    """The observation's context is no longer the current one (binding or
    checker version changed): its inputs cannot be reproduced -- K."""
    current = ctx_id(
        obs.route, binding, admin_digest, rules, obs.accepted, obs.head, obs.policy
    )
    return current != obs.ctx


# --- the life cycle --------------------------------------------------------------


def open_observation(
    db: Db,
    root: Root,
    ctx: str,
    *,
    route: Route,
    binding: TrustBinding,
    admin_digest: str,
    rules: str,
    accepted: str | None,
    head: str,
    policy: PolicyPin | None,
) -> Observation:
    """Create the observation of `ctx` before the checker is called (its own
    `COMMIT`), or return the unfinished one of equal `ctx` (dedup).

    `ctx` must be the `ctx_id` of the other arguments and `accepted` the
    root's accepted SHA in the transaction (else `trust_head_moved`: the
    caller reads A again). A's record (`last_epoch`, `source_digest`, check
    version) is pinned in the row with it (`Observation.pinned`, review
    D-a): re-evaluation never needs `trust_heads` again. A root seen for the first time after a restore
    (`restored`) is admitted by K: a conservative event comes first."""
    if root != binding.root_id:
        raise ValueError("the root is not the binding's")
    if (route == "merge") != (policy is not None):
        raise ValueError("P is pinned for `merge` and only for it")
    if ctx != ctx_id(route, binding, admin_digest, rules, accepted, head, policy):
        raise ValueError("ctx is not the identity of the inputs")
    conn = db.conn
    with perimeter.write(conn):
        found = _of_ctx(conn, root, ctx, latest=False)
        if found is not None:
            return found.obs
        row = accepted_head(conn, root)
        if (None if row is None else row.sha) != accepted:
            raise Unavailable("trust_head_moved")
        version = 0 if row is None else row.row_version
        pinned = None if row is None else _pin(row)
        if restored(conn) and not _known(conn, root):
            _event(conn, root, _key(root, ctx, "conservative"), None,
                   _provenance(accepted, head, ctx), CONSERVATIVE_SOURCE,
                   "root first seen after a restore of the database")  # fmt: skip
        cur = conn.execute(
            "INSERT INTO trust_observations (root_host, root_repository_id, "
            "root_branch, ctx_id, route, binding, admin_digest, rules, "
            "accepted_sha, accepted_last_epoch, accepted_source_digest, "
            "accepted_rules, accepted_admin_digest, row_version, head_sha, "
            "policy_full_name, policy_branch, policy_sha, created_at, outcome, "
            "event_ids) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', '[]')",
            (
                *root,
                ctx,
                route,
                json.dumps(dataclasses.asdict(binding), sort_keys=True),
                admin_digest,
                rules,
                accepted,
                None if pinned is None else pinned.last_epoch,
                None if pinned is None else pinned.source_digest,
                None if pinned is None else pinned.rules,
                None if pinned is None else pinned.admin_digest,
                version,
                head,
                None if policy is None else policy.full_name,
                None if policy is None else policy.branch,
                None if policy is None else policy.p_sha,
                perimeter.now(),
            ),
        )
        TRUST_STEP("observation")
    assert cur.lastrowid is not None
    return Observation(
        cur.lastrowid, root, ctx, route, accepted, head, policy, version, pinned
    )


def _pin(row: Head) -> Pinned:
    return Pinned(
        row.sha, row.last_epoch, row.source_digest, row.rules, row.admin_digest
    )


def _known(conn: sqlite3.Connection, root: Root) -> bool:
    """The root has a row in `events`, `trust_heads` or `trust_observations`."""
    return any(
        conn.execute(f"SELECT 1 FROM {table} WHERE {_ROOT} LIMIT 1", root).fetchone()
        for table in ("events", "trust_heads", "trust_observations")
    )


def apply_status(
    db: Db,
    root: Root,
    obs: int,
    status: str,
    *,
    epochs_of: EpochOf,
    reason: str | None = None,
) -> Outcome:
    """A typed status of the checker (`OUTCOME_OF_VERDICT`; `KeyError` for
    any other): `unresolved` keeps the observation unfinished, K completes
    it conservatively (its event's epoch by `epochs_of`, `reason` named in
    its detail)."""
    if OUTCOME_OF_VERDICT[status] == "unresolved":
        return unresolved(db, root, obs)
    ctx = _by_id(db.conn, root, obs).obs.ctx
    conservative(db, root, ctx, obs, epochs_of=epochs_of, reason=reason)
    return "conservative"


def unresolved(db: Db, root: Root, obs: int) -> Outcome:
    """The journal of H is not verified yet, the context unchanged: an open
    observation stays unfinished as `unresolved` (I keeps the root from
    `clear`). The outcome after the call."""
    with perimeter.write(db.conn):
        row = _by_id(db.conn, root, obs)
        if row.outcome != "open":
            return row.outcome
        _settle(db.conn, obs, "unresolved", [], done=False)
    return "unresolved"


def register_findings(
    db: Db,
    root: Root,
    ctx: str,
    findings: Sequence[Finding],
    epochs_of: EpochOf,
) -> list[int]:
    """One event per finding (own transaction, no condition on the head):
    `request_key` = (root, `ctx`, kind, role), so a repeat finds its event
    (no new epoch). The epoch is `epochs_of`'s, read with the write lock
    held (`None`: `pending`). A finding outside the source route's kinds is
    `verdict_unbound`, nothing written. The unfinished observation of
    `ctx` is completed `finding_registered` in the same transaction."""
    conn = db.conn
    with perimeter.write(conn):
        row = _of_ctx(conn, root, ctx, latest=True)
        if row is None:
            raise ValueError(f"no observation of {ctx}")
        if not findings:
            return []
        ids = _register(conn, row, findings, epochs_of(conn, root))
        TRUST_STEP("findings")
    return ids


def _register(
    conn: sqlite3.Connection, row: _Row, findings: Sequence[Finding], epoch: int | None
) -> list[int]:
    """The events of `findings` in the caller's transaction, keyed by
    (root, `ctx`, kind, role), at `epoch`; an unfinished observation is
    completed `finding_registered`. A kind outside the route's is
    `verdict_unbound`."""
    obs = row.obs
    allowed = ROUTE_KINDS[obs.route]
    if any(f.kind not in allowed for f in findings):
        raise Unavailable("verdict_unbound")
    if not findings:
        return []
    provenance = _provenance(obs.accepted, obs.head, obs.ctx)
    ids = [
        _event(
            conn,
            obs.root,
            _key(obs.root, obs.ctx, f.kind, f.role),
            epoch,
            provenance,
            f"trust:{obs.route}",
            f.detail,
            (f.role, _repository_of(row.binding, f.role)),
        )  # fmt: skip
        for f in findings
    ]
    if row.outcome in UNFINISHED:
        _settle(conn, obs.id, "finding_registered", ids)
    return ids


def advance_head(
    db: Db,
    root: Root,
    obs: int,
    head_read: Callable[[], str],
    *,
    last_epoch: int,
    source_digest: str,
    keep: bool = False,
) -> Outcome:
    """Accept H of a verified observation: a CAS in one write transaction.

    The `trust_heads` row and the branch head (`head_read`) are read anew
    with the lock held; the row unchanged since the observation pinned it
    (`row_version`, SHA) and the head = H -- `trust_heads` := H
    (`row_version + 1`, the observation's check version, `last_epoch`,
    `source_digest`, the event of its findings). Otherwise the head is not
    accepted. The observation's outcome after the call: `accepted` or
    `superseded`; one with findings stays `finding_registered` either way
    (register first: `register_findings`); a terminal one is unchanged. A
    head that cannot be read is `Unavailable`, nothing changed.

    `keep` (review D-d): H is A again under the current check -- the CAS is
    made, but the row is not written: no `row_version` bump, the epoch and
    the digest of A stay (the caller bound the verdict to them)."""
    conn = db.conn
    with perimeter.write(conn):
        row = _by_id(conn, root, obs)
        if row.outcome not in UNFINISHED and row.outcome != "finding_registered":
            return row.outcome
        current = accepted_head(conn, root)
        version = 0 if current is None else current.row_version
        moved = (
            version != row.obs.row_version
            or (None if current is None else current.sha) != row.obs.accepted
            or head_read() != row.obs.head
        )
        if not moved and not keep:
            finding = row.events[0] if row.events else None
            _put_head(conn, root, obs, version + 1, last_epoch, source_digest, finding)
        result: Outcome = "superseded" if moved else "accepted"
        if row.outcome == "finding_registered":
            result = "finding_registered"
        else:
            _settle(conn, obs, result, [])
        TRUST_STEP("advance")
    return result


def _put_head(
    conn: sqlite3.Connection,
    root: Root,
    obs: int,
    version: int,
    last_epoch: int,
    source_digest: str,
    finding: int | None,
) -> None:
    head, admin, rules, binding = conn.execute(
        "SELECT head_sha, admin_digest, rules, binding FROM trust_observations "
        "WHERE observation_id = ?",
        (obs,),
    ).fetchone()
    conn.execute(
        "INSERT INTO trust_heads (root_host, root_repository_id, root_branch, sha, "
        "last_epoch, source_digest, admin_digest, rules, binding_digest, "
        "row_version, at, finding_event_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (root_host, root_repository_id, root_branch) DO UPDATE SET "
        "sha = excluded.sha, last_epoch = excluded.last_epoch, "
        "source_digest = excluded.source_digest, "
        "admin_digest = excluded.admin_digest, rules = excluded.rules, "
        "binding_digest = excluded.binding_digest, "
        "row_version = excluded.row_version, at = excluded.at, "
        "finding_event_id = excluded.finding_event_id",
        (
            *root, head, last_epoch, source_digest, admin, rules,
            _digest(json.loads(binding)), version, perimeter.now(), finding,
        ),
    )  # fmt: skip


def conservative(
    db: Db,
    root: Root,
    ctx: str,
    obs: int,
    *,
    epochs_of: EpochOf,
    reason: str | None = None,
) -> int:
    """K: the conservative event of (root, `ctx`) -- `request_key` = (root,
    `ctx`, `conservative`), idempotent -- and the observation's terminal
    `conservative`, in one transaction. The epoch is `epochs_of`'s (the
    registration rule, read inside the transaction; `None` is `pending`);
    the caller always chooses it; `reason` (a typed status of the checker)
    goes into the event's detail. An observation already completed
    otherwise is `ValueError`."""
    conn = db.conn
    with perimeter.write(conn):
        row = _by_id(conn, root, obs)
        if row.obs.ctx != ctx:
            raise ValueError(f"observation {obs} is not of {ctx}")
        if row.outcome not in UNFINISHED and row.outcome != "conservative":
            raise ValueError(f"observation {obs} is already {row.outcome}")
        event = _conclude(conn, row, epochs_of(conn, root), reason)
        TRUST_STEP("conservative")
    return event


def conclude_unfinished(
    conn: sqlite3.Connection, root: Root, epoch: int | None
) -> None:
    """K for the admission of a restored database (inside its transaction):
    every unfinished observation of the copy is completed conservatively
    at `epoch` (`None`: `pending`, no verified head, review D-b) -- the
    context of its creation cannot be reproduced."""
    for obs in unfinished(conn, root):
        _conclude(conn, _by_id(conn, root, obs.id), epoch)


def _conclude(
    conn: sqlite3.Connection, row: _Row, epoch: int | None, reason: str | None = None
) -> int:
    obs = row.obs
    detail = "conservative completion of an observation (K)"
    if reason is not None:
        detail = clip_detail(f"{detail}: {reason}")
    event = _event(
        conn, obs.root, _key(obs.root, obs.ctx, "conservative"), epoch,
        _provenance(obs.accepted, obs.head, obs.ctx), CONSERVATIVE_SOURCE, detail,
    )  # fmt: skip
    if row.outcome in UNFINISHED:
        _settle(conn, obs.id, "conservative", [event])
    return event


def _settle(
    conn: sqlite3.Connection,
    obs: int,
    outcome: Outcome,
    events: list[int],
    *,
    done: bool = True,
) -> None:
    conn.execute(
        "UPDATE trust_observations SET outcome = ?, event_ids = ?, completed_at = ? "
        "WHERE observation_id = ?",
        (outcome, json.dumps(events), perimeter.now() if done else None, obs),
    )


def _key(root: Root, ctx: str, *parts: str) -> str:
    return json.dumps([*root, ctx, *parts], separators=(",", ":"))


def _provenance(accepted: str | None, head: str, ctx: str) -> dict[str, object]:
    return {"accepted_sha": accepted, "snapshot_sha": head, "ctx_id": ctx}


def _repository_of(binding: Mapping[str, object], role: str) -> int:
    key = (
        "approval_policy_repository_id"
        if role == "approval-policy"
        else "df_trust_repository_id"
    )
    return int(cast(int, binding[key]))


def _event(
    conn: sqlite3.Connection,
    root: Root,
    key: str,
    epoch: int | None,
    provenance: dict[str, object],
    detected_by: str,
    detail: str,
    violated: tuple[str, int] | None = None,
) -> int:
    """The event of `key`: the existing one (no new epoch), else a new one."""
    found = conn.execute(
        "SELECT event_id FROM events WHERE request_key = ?", (key,)
    ).fetchone()
    if found is not None:
        return int(found[0])
    return perimeter.insert_event(
        conn,
        root,
        key=key,
        epoch=epoch,
        provenance=provenance,
        detected_by=detected_by,
        detail=detail,
        violated=violated,
    )


# --- the final admission of `merge` ----------------------------------------------


class MergeVerdict(Protocol):
    """What the admission reads of a bound `op: merge` verdict
    (`trust_routes.Verdict`)."""

    @property
    def outcome(self) -> str:
        """The checker's outcome."""
        ...

    @property
    def reason(self) -> str:
        """Its reason."""
        ...

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Its findings, one per (kind, role)."""
        ...

    @property
    def verified(self) -> bool:
        """The journal of X was read and verified."""
        ...


#: The refusal of the final admission (spec 6.1; named apart from
#: `perimeter.admit`, the admission of a restored database).
PERIMETER_BLOCKED = "perimeter_blocked"


def admit_merge(
    db: Db,
    root: Root,
    obs: int,
    verdict: MergeVerdict,
    *,
    head_read: Callable[[], str],
    binding: TrustBinding,
    admin_digest: str,
    rules: str,
    admit: bool = True,
) -> Refused | None:
    """The final admission of a `merge` (spec 6.1 «Маршруты и наблюдения»):
    one write transaction, the branch head read with the lock held.

    (1) The own observation is completed by its bound verdict: with findings
    `finding_registered`; verified without findings `accepted` when the
    accepted row is still the one it pinned and the head is X, else
    `superseded` -- the row is never written (X = A, review D-d); `unset`
    and `tree_incomplete` keep it unfinished (`unresolved`, re-evaluated by
    `op: trust`); `unsupported_size` is K. One completed by another route
    meanwhile keeps its outcome. (2) Its findings are registered by the
    registration rule under its `ctx_id` (the same keys, no new epoch).
    (3) For `allow` (and `admit`): I applied anew -- the head = X = the
    accepted row under the current check, the own observation `accepted`,
    no other unfinished observation, no blocking event (`pending` events
    registered first at the verified epoch); else `Refused(PERIMETER_BLOCKED)`.
    The caller writes `armed` and sends the `PUT` only after `None`, after
    the `COMMIT`."""
    conn = db.conn
    with perimeter.write(conn):
        row = _by_id(conn, root, obs)
        head = head_read()
        acc = accepted_head(conn, root)
        current = acc is not None and head_valid(acc, binding, admin_digest, rules)
        verified = None if acc is None or not current or head != acc.sha else acc
        _complete_merge(conn, row, verdict, verified, head)
        ok = verdict.outcome != "allow" or not admit
        if not ok and verified is not None:
            perimeter.register_pending(conn, root, Snapshot(head, verified.last_epoch))
            ok = _admissible(conn, row.obs, verified)
        TRUST_STEP("admission")
    return None if ok else Refused(PERIMETER_BLOCKED)


def _complete_merge(
    conn: sqlite3.Connection,
    row: _Row,
    verdict: MergeVerdict,
    verified: Head | None,
    head: str,
) -> None:
    """Steps (1) and (2) of `admit_merge`; `verified` is the accepted row
    when the head read now is its SHA under the current check."""
    epoch = None if verified is None else verified.last_epoch
    if verdict.findings:
        _register(conn, row, verdict.findings, epoch)
        return
    if row.outcome not in UNFINISHED:
        return
    mapped = OUTCOME_OF_VERDICT.get(verdict.outcome)
    if mapped == "conservative":
        _conclude(conn, row, epoch, verdict.reason)
    elif mapped is None and verdict.verified:
        obs = row.obs
        same = (
            verified is not None
            and verified.row_version == obs.row_version
            and verified.sha == obs.accepted == obs.head == head
        )
        _settle(conn, obs.id, "accepted" if same else "superseded", [])
    elif row.outcome == "open":
        _settle(conn, row.obs.id, "unresolved", [], done=False)


def _admissible(conn: sqlite3.Connection, obs: Observation, verified: Head) -> bool:
    """Step (3) of `admit_merge`: X is the accepted head read now, the own
    observation accepted, nothing else unfinished, no blocking event."""
    if verified.sha != obs.head or outcome_of(conn, obs.id) != "accepted":
        return False
    if any(o.id != obs.id for o in unfinished(conn, obs.root)):
        return False
    return not perimeter.blocking_events(conn, obs.root, verified.last_epoch)


def ranges_digest(conn: sqlite3.Connection, obs: int) -> str | None:
    """The digest of the ranges descriptor the observation pinned at its
    first bound verdict (`merge` only), or `None`."""
    found = conn.execute(
        "SELECT ranges_digest FROM trust_observations WHERE observation_id = ?",
        (obs,),
    ).fetchone()
    if found is None:
        raise ValueError(f"no observation {obs}")
    return None if found[0] is None else str(found[0])


def pin_ranges(db: Db, root: Root, obs: int, digest: str) -> str:
    """Record `digest` as the observation's ranges digest unless one is
    already pinned (own transaction); the pinned digest after the call.
    Only a `merge` observation pins ranges (its inputs include P)."""
    conn = db.conn
    with perimeter.write(conn):
        row = _by_id(conn, root, obs)
        if row.obs.route != "merge":
            raise ValueError(f"observation {obs} is not of the merge route")
        stored = ranges_digest(conn, obs)
        if stored is not None:
            return stored
        conn.execute(
            "UPDATE trust_observations SET ranges_digest = ? WHERE observation_id = ?",
            (digest, obs),
        )
        TRUST_STEP("ranges")
    return digest


# --- invariant I and the epoch rule ----------------------------------------------


def blocked(conn: sqlite3.Connection, root: Root) -> bool:
    """I: the root has an unfinished observation, or a blocking event --
    `pending`, or `registered(E)` with `E >=` the last epoch of its accepted
    head (any `registered` one while it has none)."""
    if conn.execute(
        f"SELECT 1 FROM trust_observations WHERE {_ROOT} "
        "AND outcome IN ('open', 'unresolved') LIMIT 1",
        root,
    ).fetchone():
        return True
    head = accepted_head(conn, root)
    last = None if head is None else head.last_epoch
    found = conn.execute(
        f"SELECT 1 FROM events WHERE {_ROOT} AND (state = 'pending' OR "
        "(state = 'registered' AND (? IS NULL OR epoch >= ?))) LIMIT 1",
        (*root, last, last),
    ).fetchone()
    return found is not None


def current_epoch(
    head_read: Callable[[], str],
    binding: TrustBinding,
    admin_digest: str,
    rules: str,
) -> EpochOf:
    """The registration rule: the last epoch of the root's `trust_heads`
    row when the branch head, read in the same transaction, equals its SHA
    and its check is current; else `None` (`pending`) -- epochs of stale A
    or H are no source. An unreadable head is `None` too."""

    def epoch(conn: sqlite3.Connection, root: Root) -> int | None:
        row = accepted_head(conn, root)
        if row is None or not head_valid(row, binding, admin_digest, rules):
            return None
        try:
            head = head_read()
        except Unavailable:
            return None
        return row.last_epoch if head == row.sha else None

    return epoch


__all__ = [
    "CONSERVATIVE_SOURCE",
    "OUTCOME_OF_VERDICT",
    "PERIMETER_BLOCKED",
    "TRUST_STEP",
    "UNFINISHED",
    "EpochOf",
    "Head",
    "MergeVerdict",
    "Observation",
    "Outcome",
    "Pinned",
    "accepted_head",
    "admit_merge",
    "advance_head",
    "apply_status",
    "binding_digest",
    "blocked",
    "conclude_unfinished",
    "conservative",
    "ctx_id",
    "current_epoch",
    "head_valid",
    "known_roots",
    "mark_restored",
    "open_observation",
    "outcome_of",
    "pin_ranges",
    "ranges_digest",
    "register_findings",
    "restored",
    "stale_context",
    "unfinished",
    "unresolved",
]
