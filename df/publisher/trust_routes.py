"""The perimeter routes over the trust state: `perimeter-status` and
`trust-status` (installer spec 5.2, 6.1 rev 11; stage 3a, Task 10).

Each route reads the binding (`trust.toml`), obtains the history token
itself (`history`, O14: no key -- anonymous with a warning; a key and no
token or an unclean self-report -- `unavailable`, never anonymous), reads the
head H of the pinned `df-trust` branch and computes the root's state from
the trust state of `events.sqlite` (`trust_state`).

- `perimeter-status`, fast path: H = A, the check of A still valid, the
  digest of `source.env` on H = the recorded one, no unfinished observation,
  a clean self-report -- no checker, no observation (nothing to detect).
  Otherwise, like `trust-status` always: an observation (A, H) is opened
  durably first (invariant I), then the checker is asked (`op: trust`,
  protocol v2) and its verdict is parsed and bound (`run_trust`).
- Unfinished observations of the root are evaluated in the order of their
  creation; the first that stays `unresolved` stops the rest (the checker is
  not answering). A changed context, or a pinned input that is gone, is K
  (a conservative event), never `clear`.

The verdict is bound to the request (`nonce`, `request_digest`), the
observation (`ctx_id`, `observation_id`, `origin_route`, H, A), the binding
(`root`), the publisher (`admin_digest`, `validation`) and its own contract
(the shape rule by `relation`, review I6; the closed sets of findings and
unverified premises; the ranges of the `merge` route). Anything else is
unbound: the observation stays unfinished (`unresolved`), and the root is
not `clear`. The checker's constants are mirrored here (`df` never imports
`governance`); `tests/test_act_trust_types.py` pins each to its source.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from df import platform
from df.config import ConfigError
from df.publisher import history, perimeter, precheck, trust_state
from df.publisher.github import Transport
from df.publisher.perimeter import Db, Root, Snapshot, Unavailable
from df.publisher.request import SHA, Response, perimeter_result
from df.publisher.trust_state import OUTCOME_OF_VERDICT, EpochOf, Observation, Outcome
from df.publisher.trust_types import (
    MAX_DETAIL_BYTES,
    ROUTE_KINDS,
    AcceptedBase,
    Finding,
    PolicyPin,
    RangeDescriptor,
    Route,
    SourceDescriptor,
    detail_size,
)
from df.stage2_config import TrustBinding, load_trust, no_constant, no_duplicates
from df.state import Snapshot as Installed

if TYPE_CHECKING:
    from df.publisher.main import Context

# --- the checker's contract, mirrored (pinned by tests/test_act_trust_types.py) ---

REQUEST_SCHEMA_V2 = "df/precheck-request/v2"
VERDICT_SCHEMA_V2 = "df/precheck-verdict/v2"
PRECHECK_VERSION_V2 = "df-precheck/2"
TREE_INCOMPLETE = "tree_incomplete"
#: `act_precheck_v2.RULES_VERSION`: with `admin_digest`, the validation
#: version of an accepted head (`validation` = `<rules>:<admin_digest>`).
RULES_VERSION = "df-trust-rules/1"
#: `act_trust_findings.VERDICT_LIMIT`: the serialised verdict, bytes.
VERDICT_LIMIT = 32 * 1024
MAX_FULL_NAME_BYTES = 140
MAX_BRANCH_BYTES = 255
#: Upper bound (exclusive) of every int of a v2 request (`act_precheck_v2`).
MAX_INT = 2**63
HISTORY_TOKEN_REQUIRED = "history_token_required"
#: `act_trust_pair.UNVERIFIED`: the closed codes of unverified premises.
UNVERIFIED = frozenset(
    {
        "allow_rebase_merge@approval-policy",
        "allow_rebase_merge@df-trust",
        HISTORY_TOKEN_REQUIRED,
    }
)
#: Findings with which a read pair has no `source.env` descriptor (I6).
SOURCE_FINDINGS = frozenset({"source_env_invalid", "history_unverifiable"})
RELATIONS = ("descendant", "not_descendant", "first")
ALLOW, REFUSE, UNSET, UNSUPPORTED = "allow", "refuse", "unset", "unsupported_size"
#: `act_precheck._BRANCH`: a branch name git fetches as `refs/heads/<name>`.
BRANCH = re.compile(
    r"(?![-./])(?!.*(?:\.\.|//|/\.|\.lock/|\.lock$))[A-Za-z0-9_./-]*[A-Za-z0-9_-]"
)
#: `act_history_snapshot._FULL_NAME`.
FULL_NAME = re.compile(r"[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9_.-]+")
#: `act_precheck_v2._HOST`.
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")

# --- the publisher's own ------------------------------------------------------------

#: The account whose home holds the `gh` call directory (`history_token`).
PUBLISHER_USER = "df-publisher"
NO_APPROVED_GH = "history_token_unavailable: no approved gh for the history profile"
UNFINISHED = "trust_observation_unfinished"
NOT_ACCEPTED = "trust_head_not_accepted"
#: The wire under the history transport; tests put a fake here.
HISTORY_SEND: Transport | None = None

_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TREE_KEYS = frozenset(
    {
        "schema",
        "nonce",
        "request_digest",
        "outcome",
        "reason",
        "version",
        "admin_digest",
    }
)
_TRUST_KEYS = frozenset(
    {
        *_TREE_KEYS,
        "ctx_id",
        "observation_id",
        "op",
        "origin_route",
        "root",
        "relation",
        "last_epoch",
        "source",
        "ranges",
        "policy",
        "unverified",
        "snapshot_sha",
        "accepted_sha",
        "validation",
        "findings",
        "diagnostics",
    }
)
_SOURCE_KEYS = frozenset(f.name for f in dataclasses.fields(SourceDescriptor))
_FINDING_KEYS = frozenset({"kind", "role", "detail"})


# --- the session of one call ---------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """One route call: the binding, the publisher's `admin_digest`, the one
    history transport (the token's value for every read) and the finding of
    the publisher's own self-report, if any."""

    ctx: Context
    binding: TrustBinding
    admin_digest: str
    transport: Transport
    own: Finding | None

    @property
    def root(self) -> Root:
        """The bound root."""
        return self.binding.root_id

    @property
    def keyed(self) -> bool:
        """The binding names a history token profile (O14)."""
        return self.binding.history_gh_config_dir is not None

    @property
    def warnings(self) -> list[str]:
        """O14: an unkeyed binding reads anonymously, said in every answer."""
        return [] if self.keyed else [HISTORY_TOKEN_REQUIRED]

    def head(self) -> str:
        """H: the head of the pinned branch (`Unavailable` raised)."""
        got = history.read_branch_head(
            self.transport,
            self.binding.df_trust_full_name,
            self.binding.df_trust_branch,
        )
        if isinstance(got, Unavailable):
            raise got
        return got

    def epochs_of(self) -> EpochOf:
        """The registration rule over this call's head reads."""
        return trust_state.current_epoch(
            self.head, self.binding, self.admin_digest, RULES_VERSION
        )


def session(ctx: Context, binding: TrustBinding) -> Session | Unavailable:
    """The session of `binding`: the token by the launch contract (approved
    `gh`, the publisher's home), the transport, the self-report on both
    trust sources with that token. A keyed binding never reads anonymously."""
    entry = ctx.snapshot.entry
    if entry is None:
        return Unavailable("no_active_release")
    token: str | None = None
    if binding.history_gh_config_dir is not None:
        gh = approved_gh(ctx.snapshot)
        if isinstance(gh, Unavailable):
            return gh
        got = history.history_token(
            binding, gh=gh, role_home=ctx.layout.role_home(PUBLISHER_USER)
        )
        if isinstance(got, Unavailable):
            return got
        token = got
    transport = history.history_transport(token, send=HISTORY_SEND)
    own: Finding | None = None
    if token is not None:
        report = history.self_report(
            transport, binding, binding.approval_policy_repository_id
        )
        if isinstance(report, Unavailable):
            return report
        own = report
    return Session(ctx, binding, entry.admin_digest, transport, own)


def approved_gh(snapshot: Installed) -> Path | Unavailable:
    """The approved `gh` of `install.toml`, checked as `precheck serve`
    checks it (trusted path, recorded sha256); never `gh` from `PATH`."""
    install = snapshot.install
    gh = None if install is None else install.approved_cli.get("gh")
    if install is None or gh is None:
        return Unavailable(NO_APPROVED_GH)
    if platform.trusted_path_problems(gh):
        return Unavailable("history_token_unavailable: gh path is not trusted")
    try:
        digest = platform.file_sha256(gh)
    except OSError as exc:
        return Unavailable(f"history_token_unavailable: gh: {type(exc).__name__}")
    if digest != install.approved_cli_sha256.get("gh"):
        return Unavailable("history_token_unavailable: gh sha256 differs")
    return gh


# --- the routes ----------------------------------------------------------------------


def route_perimeter_status(ctx: Context) -> Response:
    """`perimeter-status` (spec 6.1): the fast path, or an observation and
    `op: trust`; then the state, computed anew in one write transaction
    with the head read under its lock. A head other than
    `expected_snapshot_sha` is `stale_snapshot`."""
    binding = perimeter.binding_of(ctx)
    if binding is None:
        return perimeter.status_unavailable("no_binding")
    root = perimeter.check_root(ctx, binding)
    sess = session(ctx, binding)
    if isinstance(sess, Unavailable):
        return perimeter.status_unavailable(sess.reason)
    expected = str(ctx.request.fields["expected_snapshot_sha"])
    try:
        with perimeter.open_db(ctx.layout) as db:
            perimeter.admit(db, AcceptedEpochs(sess, db), root)
            head = sess.head()
            if head != expected:
                return perimeter.stale_snapshot(head)
            if not fast_path(sess, db, head):
                observe(sess, db, "perimeter-status", head)
            state = root_state(sess, db)
    except Unavailable as exc:
        return _warned(perimeter.status_unavailable(exc.reason), sess)
    if state.head != expected:
        return perimeter.stale_snapshot(state.head)
    result = {**perimeter_result(*state.fields()), "events": state.events}
    return _warned(Response("done", None, result), sess)


def route_trust_status(ctx: Context) -> Response:
    """`trust-status` (root only, spec 5.2): always an observation (A, H) and
    `op: trust` with fresh settings findings, then the same state as
    `perimeter-status`, with the root, the unverified premises and the
    findings of this call."""
    binding = perimeter.binding_of(ctx)
    if binding is None:
        return Response("done", None, trust_status_result(None, "no_binding"))
    sess = session(ctx, binding)
    if isinstance(sess, Unavailable):
        return Response("done", None, trust_status_result(binding, sess.reason))
    try:
        with perimeter.open_db(ctx.layout) as db:
            perimeter.admit(db, AcceptedEpochs(sess, db), sess.root)
            mine = observe(sess, db, "trust-status", sess.head())
            state = root_state(sess, db)
    except Unavailable as exc:
        result = trust_status_result(binding, exc.reason, sess.warnings)
        return _warned(Response("done", None, result), sess)
    verdict = None if mine is None else mine.verdict
    findings = _with_own(sess, () if verdict is None else verdict.findings)
    unverified = sess.warnings if verdict is None else list(verdict.unverified)
    result = trust_status_result(binding, None, unverified, findings, state)
    return _warned(Response("done", None, result), sess)


def trust_status_result(
    binding: TrustBinding | None,
    reason: str | None,
    unverified: Sequence[str] = (),
    findings: Sequence[Finding] = (),
    state: RootState | None = None,
) -> dict[str, object]:
    """The `result` of `trust-status`: the root, the verified snapshot and
    its last epoch, the state (and its reason), the unverified premises and
    the findings (`df/publisher-response/v1`, no new top-level key)."""
    root = None
    if binding is not None:
        host, repo, branch = binding.root_id
        root = {"host": host, "repository_id": repo, "branch": branch}
    found = state.fields() if state is not None else ("unavailable", reason, None, None)
    name, why, sha, epoch = found
    return {
        "root": root,
        "snapshot_sha": sha,
        "last_epoch": epoch,
        "state": name,
        "reason": why,
        "unverified": sorted(set(unverified)),
        "findings": [dataclasses.asdict(f) for f in findings],
    }


def _warned(resp: Response, sess: Session) -> Response:
    """O14: the warning of an unkeyed binding goes into the evidence."""
    if not sess.warnings:
        return resp
    evidence = {**resp.evidence, "warnings": sess.warnings}
    return Response(resp.outcome, resp.reason, resp.result, evidence)


def _with_own(sess: Session, findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """`findings` and the self-report's, one per (kind, role)."""
    own = sess.own
    if own is None or any((f.kind, f.role) == (own.kind, own.role) for f in findings):
        return tuple(findings)
    return (*findings, own)


# --- the fast path and the state -----------------------------------------------------


def fast_path(sess: Session, db: Db, head: str) -> bool:
    """H = A, the check of A valid (`head_valid`), no unfinished observation,
    a clean self-report and the raw digest of `source.env` on H equal to the
    recorded one: nothing to detect, the checker is not asked."""
    if sess.own is not None:
        return False
    acc = trust_state.accepted_head(db.conn, sess.root)
    if acc is None or acc.sha != head:
        return False
    if not trust_state.head_valid(acc, sess.binding, sess.admin_digest, RULES_VERSION):
        return False
    if trust_state.unfinished(db.conn, sess.root):
        return False
    binding = sess.binding
    digest = history.read_source_digest(
        sess.transport, binding.df_trust_full_name, head
    )
    if isinstance(digest, Unavailable):
        raise digest
    return digest == acc.source_digest


@dataclass(frozen=True)
class RootState:
    """The root's state read in one write transaction: the head, the
    accepted head when it is H and current, the blocking events and
    whether an observation is unfinished (invariant I)."""

    head: str
    accepted: trust_state.Head | None
    events: list[int]
    unfinished: bool

    def fields(self) -> tuple[str, str | None, str | None, int | None]:
        """`(state, reason, snapshot_sha, last_epoch)` of the answer."""
        sha = None if self.accepted is None else self.accepted.sha
        epoch = None if self.accepted is None else self.accepted.last_epoch
        if self.events:
            return "blocked", None, sha, epoch
        if self.unfinished:
            return "unavailable", UNFINISHED, None, None
        if self.accepted is None:
            return "unavailable", NOT_ACCEPTED, None, None
        return "clear", None, sha, epoch


def root_state(sess: Session, db: Db) -> RootState:
    """One write transaction: the head read with the lock held; when it is
    the accepted head and its check is current, every `pending` event is
    registered at its last epoch; the blocking events and I computed."""
    conn, root = db.conn, sess.root
    with perimeter.write(conn):
        head = sess.head()
        acc = trust_state.accepted_head(conn, root)
        current = (
            acc is not None
            and acc.sha == head
            and trust_state.head_valid(
                acc, sess.binding, sess.admin_digest, RULES_VERSION
            )
        )
        if current:
            assert acc is not None
            perimeter.register_pending(conn, root, Snapshot(head, acc.last_epoch))
        last = None if acc is None else acc.last_epoch
        events = perimeter.blocking_events(conn, root, last)
        open_ = bool(trust_state.unfinished(conn, root))
    return RootState(head, acc if current else None, events, open_)


class AcceptedEpochs:
    """The epoch source of `perimeter.admit` (a restored database): the bound
    root's last epoch is its accepted head's when the branch head, read
    now, equals it and its check is current; any other root's is unknown
    (the copy stays `restore_pending`)."""

    def __init__(self, sess: Session, db: Db) -> None:
        self.sess = sess
        self.db = db

    def head(self, root: Root) -> Snapshot | Unavailable:
        """The verified snapshot of `root`, or why there is none."""
        sess = self.sess
        if root != sess.root:
            return Unavailable("root_not_bound")
        acc = trust_state.accepted_head(self.db.conn, root)
        if acc is None or not trust_state.head_valid(
            acc, sess.binding, sess.admin_digest, RULES_VERSION
        ):
            return Unavailable(NOT_ACCEPTED)
        try:
            head = sess.head()
        except Unavailable as exc:
            return exc
        if head != acc.sha:
            return Unavailable(NOT_ACCEPTED)
        return Snapshot(head, acc.last_epoch)


# --- observations --------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluation:
    """The outcome of one observation after its evaluation, and the bound
    verdict it was completed by (`None` without one)."""

    outcome: Outcome
    verdict: Verdict | None


def observe(sess: Session, db: Db, route: Route, head: str) -> Evaluation | None:
    """Open the route's observation (A, H) durably, register the publisher's
    own finding in it, then evaluate the root's unfinished observations in
    the order of creation -- this one included -- until one stays
    `unresolved`. The evaluation of this call's observation, if reached."""
    own = _open(sess, db, route, head)
    epochs_of = sess.epochs_of()
    if sess.own is not None:
        trust_state.register_findings(db, sess.root, own.ctx, [sess.own], epochs_of)
    queue = trust_state.unfinished(db.conn, sess.root)
    if all(obs.id != own.id for obs in queue):
        queue.append(own)  # completed by the own finding; the checker still asked
    mine: Evaluation | None = None
    for obs in queue:
        got = evaluate(sess, db, obs)
        if obs.id == own.id:
            mine = got
        if got.outcome == "unresolved":
            break
    return mine


def _open(sess: Session, db: Db, route: Route, head: str) -> Observation:
    """`open_observation` of (A, H); a head accepted meanwhile is read again
    once (`trust_head_moved`)."""
    for attempt in (1, 2):
        acc = trust_state.accepted_head(db.conn, sess.root)
        accepted = None if acc is None else acc.sha
        ctx = trust_state.ctx_id(
            route, sess.binding, sess.admin_digest, RULES_VERSION, accepted, head, None
        )
        try:
            return trust_state.open_observation(
                db,
                sess.root,
                ctx,
                route=route,
                binding=sess.binding,
                admin_digest=sess.admin_digest,
                rules=RULES_VERSION,
                accepted=accepted,
                head=head,
                policy=None,
            )
        except Unavailable as exc:
            if exc.reason != "trust_head_moved" or attempt == 2:
                raise
    raise AssertionError("unreachable")


def evaluate(sess: Session, db: Db, obs: Observation) -> Evaluation:
    """One observation through its life cycle (spec 6.1): K when its context
    is no longer current or a pinned input is gone; `op: trust`; an unbound
    or lost answer keeps it `unresolved`; a typed status by
    `OUTCOME_OF_VERDICT`; otherwise its findings registered, then H
    advanced when its journal is verified. K is applied before any write
    that follows the checker if the binding changed meanwhile."""
    root, epochs_of = sess.root, sess.epochs_of()
    if trust_state.stale_context(obs, sess.binding, sess.admin_digest, RULES_VERSION):
        return Evaluation(_conservative(db, root, obs, epochs_of), None)
    base = _base(db, obs)
    if isinstance(base, Unavailable):
        return Evaluation(_conservative(db, root, obs, epochs_of), None)
    verdict = run_trust(sess, obs, base=base, db=db)
    if isinstance(verdict, Unavailable):
        return Evaluation(trust_state.unresolved(db, root, obs.id), None)
    if _binding_moved(sess):
        return Evaluation(_conservative(db, root, obs, epochs_of), None)
    if verdict.outcome in OUTCOME_OF_VERDICT:
        if not _is_unfinished(db, obs):
            return Evaluation(trust_state.outcome_of(db.conn, obs.id), verdict)
        out = trust_state.apply_status(
            db, root, obs.id, verdict.outcome, epochs_of=epochs_of
        )
        return Evaluation(out, verdict)
    return Evaluation(_complete(sess, db, obs, verdict, epochs_of), verdict)


def _complete(
    sess: Session, db: Db, obs: Observation, verdict: Verdict, epochs_of: EpochOf
) -> Outcome:
    """A bound `allow`/`refuse`: the ranges digest pinned (`merge`), the
    findings registered, then H advanced when its journal was verified. The
    publisher's own finding is registered already (`observe`)."""
    root = sess.root
    if verdict.ranges is not None:
        trust_state.pin_ranges(db, root, obs.id, verdict.ranges.digest)
    if verdict.findings:
        trust_state.register_findings(db, root, obs.ctx, verdict.findings, epochs_of)
    if not verdict.verified:
        return trust_state.outcome_of(db.conn, obs.id)
    source = verdict.source
    assert source is not None and verdict.last_epoch is not None
    try:
        return trust_state.advance_head(
            db,
            root,
            obs.id,
            sess.head,
            last_epoch=verdict.last_epoch,
            source_digest=source.digest,
        )
    except Unavailable:
        return trust_state.unresolved(db, root, obs.id)


def _base(db: Db, obs: Observation) -> AcceptedBase | None | Unavailable:
    """The accepted record of A as the checker needs it; `Unavailable` when
    A's row is no longer there (an input gone for good: K)."""
    if obs.accepted is None:
        return None
    acc = trust_state.accepted_head(db.conn, obs.root)
    if acc is None or acc.sha != obs.accepted:
        return Unavailable("accepted_head_gone")
    return AcceptedBase(
        acc.sha,
        acc.last_epoch,
        acc.source_digest,
        validation(acc.rules, acc.admin_digest),
    )


def validation(rules: str, admin_digest: str) -> str:
    """The checker's validation version of a check (`<rules>:<admin_digest>`,
    `act_precheck_v2.validation_of`): bound in every `op: trust` verdict, so
    the value sent back is the one the bound verdict carried (D6, ruling R1)."""
    return f"{rules}:{admin_digest}"


def _binding_moved(sess: Session) -> bool:
    """`trust.toml` now names another binding than this call read (K before
    the write, review M7); an unreadable file counts as moved."""
    try:
        now = load_trust(sess.ctx.layout.etc / "trust.toml")
    except ConfigError:
        return True
    return now != sess.binding


def _is_unfinished(db: Db, obs: Observation) -> bool:
    return any(o.id == obs.id for o in trust_state.unfinished(db.conn, obs.root))


def _conservative(db: Db, root: Root, obs: Observation, epochs_of: EpochOf) -> Outcome:
    """K for `obs`, unless it is completed already (then its outcome stays)."""
    if not _is_unfinished(db, obs):
        return trust_state.outcome_of(db.conn, obs.id)
    trust_state.conservative(db, root, obs.ctx, obs.id, epochs_of=epochs_of)
    return "conservative"


# --- op: trust -----------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """A parsed and bound `df/precheck-verdict/v2` of `op: trust` (the
    binding fields are checked, not kept). `tree_incomplete` carries only
    its outcome and reason; a publisher-made `unsupported_size` (review D3)
    has no other field either."""

    outcome: str
    reason: str
    relation: str | None = None
    last_epoch: int | None = None
    source: SourceDescriptor | None = None
    ranges: RangeDescriptor | None = None
    findings: tuple[Finding, ...] = ()
    unverified: tuple[str, ...] = ()
    validation: str | None = None

    @property
    def verified(self) -> bool:
        """The journal of H was read and verified: H may be accepted."""
        return (
            self.relation is not None
            and self.last_epoch is not None
            and self.source is not None
        )


def run_trust(
    sess: Session, obs: Observation, *, base: AcceptedBase | None, db: Db
) -> Verdict | Unavailable:
    """`op: trust` of `obs` through the fixed precheck command (protocol v2):
    the request from the observation's pinned inputs (A = `base`, H, for
    `merge` its P; else P pinned now, `pin_policy`), the verdict parsed and
    bound. A timeout, a failed exit (a traceback is never parsed), a
    malformed or unbound verdict is `Unavailable` (`precheck_failed: ...`).
    An input outside the checker's limits is the typed `unsupported_size:
    <field>` made here (review D3), without asking the checker."""
    policy = obs.policy if obs.route == "merge" else pin_policy(sess.transport, sess)
    if isinstance(policy, Unavailable):
        return policy
    problem = limits_problem(sess.binding, policy)
    if problem is not None:
        return Verdict(UNSUPPORTED, f"unsupported_size: {problem}")
    request = trust_request(sess, obs, base, policy)
    data = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(data) > precheck.LIMIT:
        return Unavailable("precheck_failed: request_too_large")
    run = platform.run_supervised(
        precheck.command(sess.ctx.layout),
        data,
        limit=precheck.LIMIT,
        wait=precheck.PRECHECK_WAIT,
        env=precheck.PRECHECK_ENV,
    )
    failure = precheck.classify(run)
    if failure is not None:
        return Unavailable(f"precheck_failed: {failure}")
    parsed = parse_verdict(run.stdout)
    if parsed is None:
        return Unavailable("precheck_failed: parse")
    stored = trust_state.ranges_digest(db.conn, obs.id)
    return bind(parsed, request, obs=obs, sess=sess, stored_ranges=stored)


def pin_policy(transport: Transport, sess: Session) -> PolicyPin | Unavailable:
    """P by the binding's anchor (spec 6.1 «Закрепление P»):
    `GET /repositories/{approval_policy_repository_id}` gives the full name
    and the default branch, the head of that branch is P."""
    repo_id = sess.binding.approval_policy_repository_id
    body = history.read_repository(transport, repo_id)
    if isinstance(body, Unavailable):
        return body
    name, branch = body.get("full_name"), body.get("default_branch")
    if body.get("id") != repo_id or not isinstance(name, str):
        return Unavailable(f"repositories/{repo_id}: not the repository or no name")
    if not isinstance(branch, str) or not branch:
        return Unavailable(f"repositories/{repo_id}: no default branch")
    if not (_name(name) and _branch(branch)):
        # outside the checker's limits: `limits_problem` names the field; no
        # read is made with such coordinates
        return PolicyPin(name, branch, "0" * 40)
    p_sha = history.read_branch_head(transport, name, branch)
    if isinstance(p_sha, Unavailable):
        return p_sha
    return PolicyPin(name, branch, p_sha)


def limits_problem(binding: TrustBinding, policy: PolicyPin) -> str | None:
    """The first input outside the checker's request limits (review D3):
    names <= 140 bytes of the owner/name form, branches <= 255 bytes of the
    git branch form, ints `0 < v < 2**63`, the host's form. Its field name,
    or `None`."""
    checks = (
        ("binding.host", HOST.fullmatch(binding.host) is not None),
        ("binding.df_trust_repository_id", _id(binding.df_trust_repository_id)),
        (
            "binding.approval_policy_repository_id",
            _id(binding.approval_policy_repository_id),
        ),
        ("binding.df_trust_full_name", _name(binding.df_trust_full_name)),
        ("binding.branch", _branch(binding.df_trust_branch)),
        ("policy.full_name", _name(policy.full_name)),
        ("policy.branch", _branch(policy.branch)),
    )
    return next((name for name, ok in checks if not ok), None)


def _id(value: int) -> bool:
    return type(value) is int and 0 < value < MAX_INT


def _name(value: str) -> bool:
    fits = len(value.encode("utf-8", "surrogatepass")) <= MAX_FULL_NAME_BYTES
    return fits and FULL_NAME.fullmatch(value) is not None


def _branch(value: str) -> bool:
    fits = len(value.encode("utf-8", "surrogatepass")) <= MAX_BRANCH_BYTES
    return fits and BRANCH.fullmatch(value) is not None


def trust_request(
    sess: Session, obs: Observation, base: AcceptedBase | None, policy: PolicyPin
) -> dict[str, object]:
    """`df/precheck-request/v2`, `op: trust`, of `obs` (every key of
    `act_precheck_v2._COMMON` and `_TRUST`; `policy` on every op, D2)."""
    binding = sess.binding
    return {
        "schema": REQUEST_SCHEMA_V2,
        "nonce": secrets.token_hex(16),
        "op": "trust",
        "origin_route": obs.route,
        "ctx_id": obs.ctx,
        "observation_id": obs.id,
        "binding": {
            "host": binding.host,
            "df_trust_repository_id": binding.df_trust_repository_id,
            "df_trust_full_name": binding.df_trust_full_name,
            "branch": binding.df_trust_branch,
            "approval_policy_repository_id": binding.approval_policy_repository_id,
            "history_gh_config_dir": binding.history_gh_config_dir,
        },
        "policy": dataclasses.asdict(policy),
        "trust_accepted": None if base is None else dataclasses.asdict(base),
        "trust_head": obs.head,
    }


def canonical(value: object) -> bytes:
    """The checker's wire form: sorted keys, no spaces, UTF-8, not ASCII."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def parse_verdict(raw: bytes) -> Mapping[str, object] | None:
    """One v2 verdict object, or `None`. `tree_incomplete` is classified by
    its outcome first and only parsed (T10-1: its bytes are not canonical);
    any other verdict must be exactly the canonical bytes of its object and
    one newline, at most `VERDICT_LIMIT` bytes, with exactly the keys of the
    `op: trust` envelope."""
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("outcome") == TREE_INCOMPLETE:
        return data if set(data) == _TREE_KEYS else None
    if set(data) != _TRUST_KEYS or len(raw) > VERDICT_LIMIT + 1:
        return None
    if raw != canonical(data) + b"\n":
        return None
    return data


class _Unbound(Exception):
    """The verdict does not answer this request (`what` names the rule)."""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def bind(
    data: Mapping[str, object],
    request: Mapping[str, object],
    *,
    obs: Observation,
    sess: Session,
    stored_ranges: str | None,
) -> Verdict | Unavailable:
    """The parsed verdict checked against the request, the observation, the
    binding and the publisher; `Unavailable("precheck_failed:
    binding_mismatch: <what>")` names the first rule it breaks."""
    try:
        _bind_common(data, request, sess)
        if data["outcome"] == TREE_INCOMPLETE:
            return Verdict(TREE_INCOMPLETE, _text(data["reason"], "reason"))
        _bind_trust(data, request, obs, sess)
        verdict = _verdict(data, obs.route)
        _check_contract(verdict, obs, sess, request, stored_ranges)
    except _Unbound as exc:
        return Unavailable(f"precheck_failed: binding_mismatch: {exc.what}")
    return verdict


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise _Unbound(what)


def _bind_common(
    data: Mapping[str, object], request: Mapping[str, object], sess: Session
) -> None:
    _need(data["schema"] == VERDICT_SCHEMA_V2, "schema")
    _need(data["nonce"] == request["nonce"], "nonce")
    _need(data["request_digest"] == precheck.request_digest(request), "request_digest")
    _need(data["version"] == PRECHECK_VERSION_V2, "version")
    _need(data["admin_digest"] == sess.admin_digest, "admin_digest")


def _bind_trust(
    data: Mapping[str, object],
    request: Mapping[str, object],
    obs: Observation,
    sess: Session,
) -> None:
    host, repo, branch = sess.root
    root = {"host": host, "df_trust_repository_id": repo, "branch": branch}
    bound = (
        ("ctx_id", obs.ctx),
        ("observation_id", obs.id),
        ("op", "trust"),
        ("origin_route", obs.route),
        ("root", root),
        ("snapshot_sha", obs.head),
        ("accepted_sha", obs.accepted),
        ("policy", request["policy"]),
        ("validation", validation(RULES_VERSION, sess.admin_digest)),
    )
    for key, want in bound:
        got = data[key]
        _need(type(got) is type(want) and got == want, key)


def _verdict(data: Mapping[str, object], route: Route) -> Verdict:
    """The variable part of the verdict, each value of its form."""
    outcome = data["outcome"]
    _need(outcome in (ALLOW, REFUSE, UNSET, UNSUPPORTED), "outcome")
    relation = data["relation"]
    _need(relation is None or relation in RELATIONS, "relation")
    epoch = data["last_epoch"]
    _need(epoch is None or (type(epoch) is int and 0 < epoch < MAX_INT), "last_epoch")
    _need(isinstance(data["diagnostics"], str), "diagnostics")
    reason = _text(data["reason"], "reason")
    _need(detail_size(reason) <= MAX_DETAIL_BYTES, "reason")
    return Verdict(
        outcome=str(outcome),
        reason=reason,
        relation=None if relation is None else str(relation),
        last_epoch=cast(int | None, epoch),
        source=_source(data["source"]),
        ranges=_ranges(data["ranges"]),
        findings=_findings(data["findings"], route),
        unverified=_unverified(data["unverified"]),
        validation=str(data["validation"]),
    )


def _text(value: object, what: str) -> str:
    _need(isinstance(value, str), what)
    return str(value)


def _source(value: object) -> SourceDescriptor | None:
    if value is None:
        return None
    _need(isinstance(value, dict) and set(value) == _SOURCE_KEYS, "source")
    assert isinstance(value, dict)
    count = value["admins_count"]
    ok = (
        _hex64(value["digest"])
        and _hex64(value["admins_digest"])
        and isinstance(value["bootstrap_activation"], str)
        and SHA.fullmatch(value["bootstrap_activation"]) is not None
        and isinstance(value["policy_full_name"], str)
        and isinstance(value["policy_branch"], str)
        and type(count) is int
        and count >= 0
    )
    _need(ok, "source")
    return SourceDescriptor(**value)


def _hex64(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _ranges(value: object) -> RangeDescriptor | None:
    if value is None:
        return None
    _need(isinstance(value, dict) and set(value) == {"ranges", "digest"}, "ranges")
    assert isinstance(value, dict)
    rows, digest = value["ranges"], value["digest"]
    _need(isinstance(rows, list) and _hex64(digest), "ranges")
    assert isinstance(rows, list)
    parsed: list[tuple[int, str, str]] = []
    for row in rows:
        ok = (
            isinstance(row, list)
            and len(row) == 3
            and type(row[0]) is int
            and all(isinstance(s, str) and SHA.fullmatch(s) for s in row[1:])
        )
        _need(ok, "ranges")
        parsed.append((row[0], row[1], row[2]))
    try:
        return RangeDescriptor(tuple(parsed), str(digest))
    except ValueError:
        raise _Unbound("ranges") from None


def _findings(value: object, route: Route) -> tuple[Finding, ...]:
    """At most one finding per (kind, role), each of the source route's
    kinds (spec 6.1 «Размер вердикта»)."""
    _need(isinstance(value, list), "findings")
    assert isinstance(value, list)
    found: list[Finding] = []
    for row in value:
        _need(isinstance(row, dict) and set(row) == _FINDING_KEYS, "findings")
        assert isinstance(row, dict)
        try:
            found.append(Finding(row["kind"], row["role"], row["detail"]))
        except (ValueError, TypeError):
            raise _Unbound("findings") from None
    pairs = [(f.kind, f.role) for f in found]
    _need(len(set(pairs)) == len(pairs), "findings: duplicate (kind, role)")
    _need(all(f.kind in ROUTE_KINDS[route] for f in found), "findings: route kinds")
    return tuple(found)


def _unverified(value: object) -> tuple[str, ...]:
    _need(isinstance(value, list), "unverified")
    assert isinstance(value, list)
    ok = all(isinstance(c, str) and c in UNVERIFIED for c in value)
    _need(ok and value == sorted(set(value)), "unverified")
    return tuple(value)


def _check_contract(
    verdict: Verdict,
    obs: Observation,
    sess: Session,
    request: Mapping[str, object],
    stored_ranges: str | None,
) -> None:
    """The rules that tie the fields together: O14, the shape by `relation`
    (review I6), the outcome and its findings, the ranges of `merge`."""
    keyed_warning = HISTORY_TOKEN_REQUIRED in verdict.unverified
    _need(keyed_warning != sess.keyed, "unverified: history_token_required")
    kinds = {f.kind for f in verdict.findings}
    if verdict.relation is None:
        empty = (verdict.source, verdict.last_epoch, verdict.ranges) == (None,) * 3
        _need(empty and not verdict.findings, "shape: no pair")
        _need(verdict.outcome in (UNSET, UNSUPPORTED, REFUSE), "shape: no pair")
    else:
        _need(
            (verdict.source is None) == bool(kinds & SOURCE_FINDINGS), "shape: source"
        )
    if verdict.outcome == ALLOW:
        _need(verdict.verified and not verdict.findings, "outcome: allow")
    elif verdict.outcome == REFUSE:
        _need(bool(verdict.findings), "outcome: refuse")
    else:
        _need(not verdict.findings, f"outcome: {verdict.outcome}")
    _check_ranges_of(verdict, obs, request, stored_ranges)


def _check_ranges_of(
    verdict: Verdict,
    obs: Observation,
    request: Mapping[str, object],
    stored_ranges: str | None,
) -> None:
    """`ranges` only for the `merge` route with a verified snapshot (T11-3),
    and then `check_ranges` against P and the pinned digest."""
    want = (
        obs.route == "merge"
        and verdict.outcome in (ALLOW, REFUSE)
        and verdict.last_epoch is not None
    )
    if not want:
        _need(verdict.ranges is None, "ranges")
        return
    _need(verdict.ranges is not None and verdict.last_epoch is not None, "ranges")
    assert verdict.ranges is not None and verdict.last_epoch is not None
    policy = request["policy"]
    assert isinstance(policy, dict)
    ok = check_ranges(
        verdict.ranges,
        last_epoch=verdict.last_epoch,
        p_sha=str(policy["p_sha"]),
        stored_digest=stored_ranges,
    )
    _need(ok, "ranges")


def range_digest(ranges: Sequence[tuple[int, str, str]]) -> str:
    """The digest of a ranges descriptor, as `act_trust_findings.
    range_descriptor` computes it: sha256 of the canonical JSON list of
    `[epoch, anchor, end]` rows."""
    return hashlib.sha256(canonical([list(r) for r in ranges])).hexdigest()


def check_ranges(
    desc: RangeDescriptor, *, last_epoch: int, p_sha: str, stored_digest: str | None
) -> bool:
    """The expected policy ranges (spec 6.1), checked without YAML: epochs
    1…`last_epoch` in order, each end a SHA, the last (open) range ending at
    P, the digest that of the rows, and on re-evaluation the digest the
    observation pinned at its first bound verdict."""
    rows = desc.ranges
    if [r[0] for r in rows] != list(range(1, last_epoch + 1)):
        return False
    if any(type(r[0]) is not int for r in rows) or rows[-1][2] != p_sha:
        return False
    if desc.digest != range_digest(rows):
        return False
    return stored_digest is None or desc.digest == stored_digest


__all__ = [
    "BRANCH",
    "FULL_NAME",
    "HISTORY_TOKEN_REQUIRED",
    "HOST",
    "MAX_BRANCH_BYTES",
    "MAX_FULL_NAME_BYTES",
    "MAX_INT",
    "PRECHECK_VERSION_V2",
    "REQUEST_SCHEMA_V2",
    "RULES_VERSION",
    "SOURCE_FINDINGS",
    "TREE_INCOMPLETE",
    "UNVERIFIED",
    "VERDICT_LIMIT",
    "VERDICT_SCHEMA_V2",
    "AcceptedEpochs",
    "Evaluation",
    "RootState",
    "Session",
    "Verdict",
    "approved_gh",
    "bind",
    "canonical",
    "check_ranges",
    "evaluate",
    "fast_path",
    "limits_problem",
    "observe",
    "parse_verdict",
    "pin_policy",
    "range_digest",
    "root_state",
    "route_perimeter_status",
    "route_trust_status",
    "run_trust",
    "session",
    "trust_request",
    "trust_status_result",
    "validation",
]
