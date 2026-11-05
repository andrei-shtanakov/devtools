"""Precheck program, protocol v2 (installer spec §5.4 «Протокол v2», §6.1; 3a Task 7).

`act_precheck.serve` hands a `df/precheck-request/v2` here only after the admin
tree passed its completeness check (spec §3.9 rev 11: the v2 program imports its
closure after the check). The request is parsed strictly (`parse`; a deviation is
`InvalidRequest`, exit 2, no verdict) and answered by exactly one
`df/precheck-verdict/v2`: the bytes `act_trust_findings.fit_verdict` returns
(canonical JSON, ``ensure_ascii=False``, at most 32 KiB), never re-serialised.

Two operations, both stateless (the checker keeps no state, writes nothing):

- ``op: trust`` — the trust snapshot check of an observation (spec §6.1): the pair
  (A, H) of `df-trust` read by SHA (`act_trust_pair.read_pair`), the settings at
  the moment of the call (`settings_findings`), and for the source route `merge`
  (its re-evaluation) the history of the policy source up to the pinned P with
  the ranges descriptor. It never opens the perimeter journal and never calls
  `load_trust`.
- ``op: merge`` (``kind`` candidate or finalize) — `load_trust` with a journal
  adapter over the request (`status` is the preliminary state only for the
  snapshot X, else ``Unavailable(stale_snapshot)``; `record_finding` keeps the
  findings for the verdict, the publisher registers them), the explicit mode of
  the derived product credential (N3), the snapshot read as the pair (A, X) by
  SHA; candidate — `act_premerge_core` with the merger of the request (N4) and the D16
  surface of the whole PR (`act_surface`); finalize — the expected envelopes of
  the delivered request, `review_id` from the final delivery decision
  (`act_context.delivery_verdict(..., legacy=None)`, N7).

`gh` and `git` run only at the approved paths `precheck serve` passes (spec §3.10
rev 11): the history token is read with
``partial(act_trust.history_token_at, gh=gh, role_home=home)``, the readers'
`gh api` with ``partial(act_trust.gh_get, gh=gh)``; a keyed binding without an
approved `gh` is refused, never a fallback to `gh` from `PATH`. `DEADLINE` of
`act_precheck` bounds the call (`unset: deadline`).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import re
import signal
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from types import FrameType
from typing import Any, cast

from governance import (
    act_context,
    act_facts,
    act_precheck,
    act_premerge_core,
    act_surface,
    act_trust,
    act_trust_pair,
    approval_branches,
    bundle_dag,
)
from governance.act_decision import Accepted, Rejected, WaitingDelivery
from governance.act_envelope import (
    DeliveredRequest,
    envelope_path,
    expected_envelope,
    node_history,
    render_envelope,
)
from governance.act_history import load_history, pr_for_merge
from governance.act_history_snapshot import (
    _FULL_NAME,
    DeadlineExceeded,
    Scope,
    SnapshotHistory,
)
from governance.act_identity import RequestIdentity
from governance.act_precheck import (
    _BRANCH,
    _NONCE,
    _SHA,
    PRECHECK_VERSION_V2,
    REQUEST_SCHEMA_V2,
    VERDICT_SCHEMA_V2,
    InvalidRequest,
    PrecheckDeps,
    RequestV2,
    _changes,
    _http_url,
    _matches,
    _text,
)
from governance.act_projection import ProjectionError, self_hash_v
from governance.act_request import ACT_REVIEW_V1, NodeRequest, manifest_path
from governance.act_time import OPS_FAILURES, Unavailable
from governance.act_trust_findings import (
    MAX_BRANCH_BYTES,
    MAX_FULL_NAME_BYTES,
    UnsupportedSize,
    fit_verdict,
    range_descriptor,
)
from governance.act_trust_types import (
    ROUTE_KINDS,
    AcceptedBase,
    Finding,
    PolicyPin,
    Role,
    Route,
    SourceDescriptor,
    clip_detail,
)

OPS = ("trust", "merge")
MERGE_KINDS = ("candidate", "finalize")
MERGER_TYPES = ("User", "Bot")
#: The only preliminary root state a merge request may carry: the publisher
#: refuses `blocked`/`unavailable` before calling (spec §5.4 п. 1).
PRELIMINARY = "clear"
#: Version of the trust rules; with `admin_digest` it is the validation version of
#: an accepted head (`AcceptedBase.validation`, spec §6.1 «Принятая голова»): the
#: publisher writes `validation_of(admin_digest)` when it accepts a head.
RULES_VERSION = "df-trust-rules/1"
#: DAG variants of a product record served in v1 (spec §5.4 rev 11).
DAG_VARIANTS: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "full": bundle_dag.BUNDLE_DAG
}
ALLOW, REFUSE, UNSET = "allow", "refuse", "unset"
UNSUPPORTED = "unsupported_size"
#: A permanent condition of a pinned input (review D-c of 3a Task 10): the
#: observation cannot be reproduced by any re-evaluation, so the publisher
#: completes it by K (`OUTCOME_OF_VERDICT`). Its reasons are a closed set,
#: bounded by construction (the detail goes to the diagnostics only); no
#: finding kind is added (the closed list of spec §6.1 is the owner's).
IRREPRODUCIBLE = "irreproducible"
#: The policy pin names another repository than `source.env` at H: the pair
#: (A, H) and the pinned P can never agree.
PIN_MISMATCH = "irreproducible: policy_pin_mismatch"
#: Objects of the policy history up to the pinned P are gone (GitHub answers
#: 404/422 for them), not merely unreadable now.
OBJECTS_MISSING = "irreproducible: policy_objects_missing"
IRREPRODUCIBLE_REASONS = frozenset({PIN_MISMATCH, OBJECTS_MISSING})
#: How steward names a missing object of a REST read (`http: <status> <path>`).
_GONE = re.compile(r"http: (?:404|422) ")
#: Seconds the alarm backstop waits past `deadline` (the snapshot's own deadline
#: kills its git process groups first).
ALARM_SLACK = 2.0
#: A restored timer that ran out meanwhile still fires (setitimer 0 disarms).
MIN_TIMER = 0.001
HISTORY_TOKEN_REQUIRED = act_trust_pair.HISTORY_TOKEN_REQUIRED
NO_APPROVED_GH = "history_token_unavailable: no approved gh for the history profile"
#: `Trust.unverified` of `load_trust` (checks `approval-policy` only) as codes.
UNVERIFIED_OF_TRUST: Mapping[str, str] = {
    act_trust.REBASE_PREMISE: act_trust_pair.UNVERIFIED_REBASE["approval-policy"]
}
#: Findings with which the pair has no `source.env` descriptor (review I6).
SOURCE_FINDINGS = frozenset({"source_env_invalid", "history_unverifiable"})

_DIGEST = re.compile(r"[0-9a-f]{64}")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_CHANGE_OK = ("added", "modified")


class DeadlineAlarm(BaseException):
    """The alarm backstop fired: a `BaseException`, so the `except Exception` of a
    transport (`act_anon.fetch_response`) cannot turn it into a failed read and
    let the call run on."""


def validation_of(admin_digest: str) -> str:
    """The validation version of this checker: the rules and the admin tree."""
    return f"{RULES_VERSION}:{admin_digest}"


# --- the request ---------------------------------------------------------------------


#: Upper bound (exclusive) of every int of the request: the echo stays bounded.
MAX_INT = 2**63


def _id(value: object) -> bool:
    return type(value) is int and 0 < value < MAX_INT


def _absolute_or_none(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.startswith("/"))


def _bytes_at_most(limit: int, pattern: re.Pattern[str]) -> Callable[[object], bool]:
    return lambda v: _matches(pattern)(v) and len(str(v).encode()) <= limit


def _relative_dir(value: object) -> bool:
    path = PurePosixPath(str(value))
    return _text(value) and not path.is_absolute() and ".." not in path.parts


Checks = dict[str, Callable[[object], bool]]

_BINDING: Checks = {
    "host": _matches(_HOST),
    "df_trust_repository_id": _id,
    "df_trust_full_name": _bytes_at_most(MAX_FULL_NAME_BYTES, _FULL_NAME),
    "branch": _bytes_at_most(MAX_BRANCH_BYTES, _BRANCH),
    "approval_policy_repository_id": _id,
    "history_gh_config_dir": _absolute_or_none,
}
_POLICY: Checks = {
    "full_name": _bytes_at_most(MAX_FULL_NAME_BYTES, _FULL_NAME),
    "branch": _bytes_at_most(MAX_BRANCH_BYTES, _BRANCH),
    "p_sha": _matches(_SHA),
}
_ACCEPTED: Checks = {
    "sha": _matches(_SHA),
    "last_epoch": _id,
    "source_digest": _matches(_DIGEST),
    "validation": _text,
}
_COMMON: Checks = {
    "schema": lambda v: v == REQUEST_SCHEMA_V2,
    "nonce": _matches(_NONCE),
    "op": lambda v: v in OPS,
    "origin_route": lambda v: v in ROUTE_KINDS,
    "ctx_id": _matches(_DIGEST),
    "observation_id": _id,
    "binding": lambda v: _object(v, _BINDING),
    "policy": lambda v: _object(v, _POLICY),
}
_TRUST: Checks = {
    "trust_accepted": lambda v: v is None or _object(v, _ACCEPTED),
    "trust_head": _matches(_SHA),
}
_MERGE: Checks = {
    "expected_snapshot_sha": _matches(_SHA),
    "trust_accepted": lambda v: _object(v, _ACCEPTED),
    "preliminary_state": lambda v: v == PRELIMINARY,
    "kind": lambda v: v in MERGE_KINDS,
    "repository_id": _id,
    "full_name": _matches(_FULL_NAME),
    "pr": _id,
    "head_sha": _matches(_SHA),
    "head_ref": _text,
    "base_ref": _matches(_BRANCH),
    "base_tip": _matches(_SHA),
    "changes": _changes,
    "token": _text,
    "merger_login": _text,
    "merger_type": lambda v: v in MERGER_TYPES,
    "bundle_dir": _relative_dir,
    "dag_variant": _text,
    "api_base": _http_url,
    "git_base": _http_url,
}


def _object(value: object, checks: Checks) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(checks)
        and all(check(value[k]) for k, check in checks.items())
    )


@dataclass(frozen=True)
class Common:
    """The keys every v2 request carries; `binding` — the root binding as
    `act_trust` reads it (from the request, not from `trust.toml`)."""

    nonce: str
    digest: str
    op: str
    origin_route: Route
    ctx_id: str
    observation_id: int
    binding: act_trust.Binding
    policy: PolicyPin


@dataclass(frozen=True)
class TrustRequest:
    """``op: trust``: the pair (A = `accepted`, H = `head`)."""

    common: Common
    accepted: AcceptedBase | None
    head: str


@dataclass(frozen=True)
class MergeRequest:
    """``op: merge``: X, the accepted record at X and the product PR (head C)."""

    common: Common
    policy: PolicyPin
    expected_snapshot_sha: str
    accepted: AcceptedBase
    kind: str
    repository_id: int
    full_name: str
    pr: int
    head_sha: str
    head_ref: str
    base_ref: str
    base_tip: str
    changes: tuple[act_precheck.Change, ...]
    token: str
    merger_login: str
    merger_type: str
    bundle_dir: str
    dag_variant: str
    api_base: str
    git_base: str

    @property
    def nonce(self) -> str:
        """The request nonce (`act_precheck._snapshot` names the snapshot by it)."""
        return self.common.nonce


Request = TrustRequest | MergeRequest


def parse(request: RequestV2) -> Request:
    """Strict `df/precheck-request/v2`: exactly the keys of its `op`, every value
    checked, the cross-field rules; any deviation — `InvalidRequest` naming keys,
    never values. `policy` is mandatory on every op (review D2: the publisher
    always resolves the policy source by id, С-2 needs its default branch).

    A binding or policy name over its limit (≤ 140 bytes, branch ≤ 255 bytes), a
    non-branch form or an int outside `0 < v < 2**63` is a bad request too (exit
    2, review D3): the publisher pre-validates these limits and types them itself
    (`unsupported_size: <field>`, Task 10), so the echo of the envelope stays
    bounded by construction. Names read from `source.env` keep the typed
    `unsupported_size` of the checker."""
    data: dict[str, Any] = dict(request.data)
    op = data.get("op")
    checks = {**_COMMON, **(_MERGE if op == "merge" else _TRUST)}
    if set(data) != set(checks):
        extra = sorted(set(data) - set(checks))
        missing = sorted(set(checks) - set(data))
        raise InvalidRequest(f"keys: unexpected {extra}, missing {missing}")
    bad = [k for k, check in checks.items() if not check(data[k])]
    if bad:
        raise InvalidRequest(f"invalid values: {bad}")
    common = _common(data, request.digest)
    if op == "trust":
        accepted = data["trust_accepted"]
        return TrustRequest(
            common,
            None if accepted is None else AcceptedBase(**accepted),
            str(data["trust_head"]),
        )
    return _merge_request(data, common)


def _common(data: Mapping[str, Any], digest: str) -> Common:
    b = data["binding"]
    home = b["history_gh_config_dir"]
    binding = act_trust.Binding(
        host=b["host"],
        df_trust_repository_id=b["df_trust_repository_id"],
        df_trust_full_name=b["df_trust_full_name"],
        df_trust_branch=b["branch"],
        approval_policy_repository_id=b["approval_policy_repository_id"],
        transport="public",
        history_gh_config_dir=None if home is None else Path(home),
    )
    policy = data["policy"]
    return Common(
        nonce=data["nonce"],
        digest=digest,
        op=data["op"],
        origin_route=data["origin_route"],
        ctx_id=data["ctx_id"],
        observation_id=data["observation_id"],
        binding=binding,
        policy=PolicyPin(**policy),
    )


def _merge_request(data: Mapping[str, Any], common: Common) -> MergeRequest:
    accepted = AcceptedBase(**data["trust_accepted"])
    if common.origin_route != "merge":
        raise InvalidRequest("cross-field: op merge needs origin_route merge")
    if accepted.sha != data["expected_snapshot_sha"]:
        raise InvalidRequest("cross-field: trust_accepted.sha != expected_snapshot_sha")
    fields = {
        k: data[k] for k in _MERGE if k not in ("trust_accepted", "preliminary_state")
    }
    fields["changes"] = tuple((s, p, prev) for s, p, prev in data["changes"])
    return MergeRequest(
        common=common, policy=common.policy, accepted=accepted, **fields
    )


# --- the answer ----------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """What the trust snapshot check established: the relation, the snapshot of
    H/X (`None` — its journal not verified), the `source.env` descriptor, the
    findings and the unverified premises (codes of `act_trust_pair.UNVERIFIED`)."""

    relation: str
    snapshot: act_trust.TrustSnapshot | None
    source: SourceDescriptor | None
    findings: tuple[Finding, ...]
    unverified: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    """Outcome, reason and the variable part of the verdict envelope."""

    outcome: str
    reason: str
    part: Part | None = None
    ranges: object = None
    findings: tuple[Finding, ...] = ()
    #: Unverified premises beyond the part's (`load_trust`'s `Trust.unverified`).
    unverified: tuple[str, ...] = ()
    #: What an `irreproducible` reason names (to the diagnostics, not the reason).
    detail: str = ""


@dataclass(frozen=True)
class Irreproducible:
    """A permanent condition of a pinned input: `code` of
    `IRREPRODUCIBLE_REASONS`, `detail` for the diagnostics."""

    code: str
    detail: str


def answer(
    request: RequestV2,
    *,
    admin_digest: str,
    home: Path,
    gh: Path | None,
    git: Path,
    deps: PrecheckDeps,
    deadline: float,
) -> bytes:
    """The wire bytes of the verdict for `request` (`InvalidRequest` — exit 2)."""
    req = parse(request)
    ctx = Context(admin_digest, home, gh, git, deps, deadline)
    try:
        with _alarm(deadline, deps.now):
            got = (
                _trust_op(req, ctx)
                if isinstance(req, TrustRequest)
                else _merge(req, ctx)
            )
    except (DeadlineExceeded, DeadlineAlarm):
        got = Answer(UNSET, "deadline")
    except UnsupportedSize as exc:
        got = Answer(UNSUPPORTED, str(exc))
    except ValueError as exc:
        # A broken invariant of the readers (a contract slip, not the request):
        # a named `unset` verdict instead of a traceback without one.
        got = Answer(UNSET, f"invariant_violated: {exc}")
    return _wire(req, got, admin_digest)


@dataclass(frozen=True)
class Context:
    """The launch of one call: the admin digest, the role home, approved `gh`/`git`,
    the seams and the deadline."""

    admin_digest: str
    home: Path
    gh: Path | None
    git: Path
    deps: PrecheckDeps
    deadline: float

    def token_source(self) -> Callable[[Path], str | Unavailable] | None:
        """The history token by the df launch contract; `None` without `gh`."""
        gh = self.gh
        return (
            None
            if gh is None
            else partial(act_trust.history_token_at, gh=gh, role_home=self.home)
        )


@contextlib.contextmanager
def _alarm(deadline: float, now: Callable[[], float]) -> Iterator[None]:
    """Backstop of the internal deadline (rung 1): `DeadlineAlarm` is raised in
    the main thread `ALARM_SLACK` after `deadline` (REST reads have no deadline of
    their own), or earlier when a previous timer is shorter (it is not postponed);
    the previous handler and timer are restored.

    Only the main thread can take signals: `precheck serve` runs one request per
    process in its main thread, so there it is always armed; elsewhere (an
    embedding thread) it is a no-op and only the snapshot's own deadline holds.
    Residue: an alarm that lands inside a `finally` (the `gh-call-*` directory of
    `history_token_at`, a snapshot) can leave it behind; the next run's exclusive
    lock clears snapshots (`act_precheck._locked`), call directories are empty."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def fire(_signum: int, _frame: FrameType | None) -> None:
        raise DeadlineAlarm("precheck v2: deadline passed")

    started = now()
    ours = max(deadline - started, 0.0) + ALARM_SLACK
    before = signal.getitimer(signal.ITIMER_REAL)
    handler = signal.signal(signal.SIGALRM, fire)
    previous = signal.setitimer(
        signal.ITIMER_REAL, min(ours, before[0]) if before[0] > 0 else ours
    )
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if previous[0] > 0:
            left = max(previous[0] - (now() - started), MIN_TIMER)
            signal.setitimer(signal.ITIMER_REAL, left, previous[1])


def _masked(text: str, req: Request) -> str:
    token = req.token if isinstance(req, MergeRequest) else ""
    return text.replace(token, "[token]") if token else text


def _reason(text: str, req: Request) -> str:
    """A bounded reason: the token masked, at most `MAX_DETAIL_BYTES` serialised
    (lone surrogates replaced, `clip_detail`)."""
    return clip_detail(_masked(text, req))


def _wire(req: Request, got: Answer, admin_digest: str) -> bytes:
    """The verdict bytes; a clipped reason goes whole (masked) to the diagnostics,
    which `fit_verdict` cuts to the budget left."""
    problem = _shape_problem(got)
    if problem is not None:
        got = Answer(UNSET, f"invariant_violated: {problem}")
    whole = _masked(got.reason, req)
    diagnostics = "" if clip_detail(whole) == whole else f"reason: {whole}"
    if got.detail:
        diagnostics = f"detail: {_masked(got.detail, req)}"
    try:
        envelope = _envelope(req, got, admin_digest)
        return fit_verdict(envelope, got.findings, diagnostics)
    except UnsupportedSize as exc:
        fixed = Answer(UNSUPPORTED, str(exc))
        return fit_verdict(_envelope(req, fixed, admin_digest), (), "")


def _envelope(req: Request, got: Answer, admin_digest: str) -> dict[str, object]:
    """The mandatory envelope (bounded by construction, spec §6.1)."""
    common = req.common
    binding = common.binding
    part = got.part
    snap = None if part is None else part.snapshot
    env: dict[str, object] = {
        "schema": VERDICT_SCHEMA_V2,
        "nonce": common.nonce,
        "request_digest": common.digest,
        "ctx_id": common.ctx_id,
        "observation_id": common.observation_id,
        "op": common.op,
        "origin_route": common.origin_route,
        "root": {
            "host": binding.host,
            "df_trust_repository_id": binding.df_trust_repository_id,
            "branch": binding.df_trust_branch,
        },
        "outcome": got.outcome,
        "reason": _reason(got.reason, req),
        "relation": None if part is None else part.relation,
        "last_epoch": None if snap is None else snap.epochs[-1].epoch,
        "source": None
        if part is None or part.source is None
        else dataclasses.asdict(part.source),
        "ranges": got.ranges,
        "policy": dataclasses.asdict(common.policy),
        "unverified": _unverified(req, got),
        "version": PRECHECK_VERSION_V2,
        "admin_digest": admin_digest,
    }
    if isinstance(req, TrustRequest):
        env["snapshot_sha"] = req.head
        env["accepted_sha"] = None if req.accepted is None else req.accepted.sha
        # Review D6: the publisher records the validation version verbatim.
        env["validation"] = validation_of(admin_digest)
    else:
        env["repository_id"] = req.repository_id
        env["pr"] = req.pr
        env["head_sha"] = req.head_sha
        env["kind"] = req.kind
        # The snapshot the verdict is built on (X by construction: read by SHA);
        # an echo only when nothing was read — `relation` says whether it was.
        read = None if snap is None else snap.root.snapshot_sha
        env["snapshot_sha"] = read or req.expected_snapshot_sha
        env["expected_snapshot_sha"] = req.expected_snapshot_sha
    return env


def _unverified(req: Request, got: Answer) -> list[str]:
    """The closed codes: the settings' and `load_trust`'s, and
    `history_token_required` of an unkeyed binding whatever the outcome (O14)."""
    codes = set(got.unverified)
    if got.part is not None:
        codes |= set(got.part.unverified)
    if req.common.binding.history_gh_config_dir is None:
        codes.add(HISTORY_TOKEN_REQUIRED)
    return sorted(codes)


def _shape_problem(got: Answer) -> str | None:
    """The verdict contract by `relation` (review I6): read pair — `source` null
    iff a source finding; no pair — no source, epoch, ranges or findings."""
    part = got.part
    if part is None:
        return "findings without a read pair" if got.findings else None
    kinds = {f.kind for f in (*part.findings, *got.findings)}
    null_source = part.source is None
    if null_source != bool(kinds & SOURCE_FINDINGS):
        return (
            f"source {'null' if null_source else 'set'} with findings {sorted(kinds)}"
        )
    return None


# --- the trust snapshot check (op: trust, and the snapshot of op: merge) -------------


def trust_part(
    transport: Any,
    binding: act_trust.Binding,
    accepted: AcceptedBase | None,
    head: str,
    policy: PolicyPin,
    validation: str,
) -> Part | Irreproducible | Unavailable:
    """The pair (A, H) by SHA and the settings at the moment of the call (spec
    §6.1). Both finding lists are merged: `not_public` comes only from the pair.
    `policy` — the coordinates the publisher resolved by id: its branch is the
    default branch С-2 compares with the `source.env` branch on H
    (`policy_branch_mismatch`). A pin naming another policy repository than
    `source.env` on H is `Irreproducible(PIN_MISMATCH)`: H's `source.env` is
    pinned by its SHA, so no re-evaluation of the pair can agree (D-c)."""
    pair = act_trust_pair.read_pair(
        transport, binding, accepted, head, validation=validation
    )
    if isinstance(pair, Unavailable):
        return pair
    if pair.snapshot is None:
        return Part(pair.relation, None, pair.source, pair.findings, ())
    source = pair.snapshot.source
    if policy.full_name != source.repo:
        return Irreproducible(
            PIN_MISMATCH,
            f"policy pin {policy.full_name!r} != source.env {source.repo!r} at H",
        )
    settings = act_trust_pair.settings_findings(
        transport, binding, pair.snapshot, policy_branch=policy.branch
    )
    if isinstance(settings, Unavailable):
        return settings
    return Part(
        pair.relation,
        pair.snapshot,
        pair.source,
        (*pair.findings, *settings.findings),
        settings.unverified,
    )


def _history(req: Request, ctx: Context) -> Any:
    """The trust-source transport of the binding (`history_transport`, the only
    factory): a keyed binding needs the approved `gh` (no `PATH` fallback)."""
    binding = req.common.binding
    source = ctx.token_source()
    if binding.history_gh_config_dir is not None and source is None:
        return Unavailable(NO_APPROVED_GH)
    return act_trust.history_transport(binding, token_source=source)


def _trust_op(req: TrustRequest, ctx: Context) -> Answer:
    """``op: trust``: never opens the journal, never calls `load_trust`."""
    common = req.common
    transport = _history(req, ctx)
    if isinstance(transport, Unavailable):
        return Answer(UNSET, transport.reason)
    part = trust_part(
        transport,
        common.binding,
        req.accepted,
        req.head,
        common.policy,
        validation_of(ctx.admin_digest),
    )
    if isinstance(part, Unavailable):
        return Answer(UNSET, part.reason)
    if isinstance(part, Irreproducible):
        return Answer(IRREPRODUCIBLE, part.code, detail=part.detail)
    findings = part.findings
    ranges = None
    if common.origin_route == "merge" and part.snapshot is not None:
        p_sha = common.policy.p_sha
        ranges = dataclasses.asdict(range_descriptor(part.snapshot.epochs, p_sha))
        broken = policy_history_break(transport, part.snapshot, p_sha)
        if isinstance(broken, Irreproducible):
            return Answer(IRREPRODUCIBLE, broken.code, part, detail=broken.detail)
        if isinstance(broken, Unavailable):
            return Answer(UNSET, broken.reason, part)
        findings = (*findings, *broken)
    return _decided(part, findings, ranges)


def policy_history_break(
    transport: Any, snap: act_trust.TrustSnapshot, p_sha: str
) -> tuple[Finding, ...] | Irreproducible | Unavailable:
    """The policy-source history of every epoch of the journal on X: closed
    `anchor…last_confirmed`, open `anchor…P` by SHA (spec §6.1, К). Every epoch
    is read, as `load_ranges` does: a broken form — the finding
    `policy_history_break` (one per role, the first epoch) — wins over another
    epoch's gap; then objects of the pinned history that GitHub says are gone
    (404/422) — `Irreproducible(OBJECTS_MISSING)`, permanent (D-c) — win over
    a gap that is only unreadable now (`Unavailable`)."""
    gap: Unavailable | None = None
    gone: Unavailable | None = None
    for epoch in snap.epochs:
        got = act_facts.history(
            transport, snap.source, epoch.anchor, epoch.last_confirmed, head=p_sha
        )
        if isinstance(got, act_facts.HistoryBreak):
            detail = clip_detail(f"epoch {epoch.epoch}: {got.reason}")
            return (Finding("policy_history_break", "approval-policy", detail),)
        if isinstance(got, Unavailable):
            if _GONE.match(got.reason):
                gone = gone or Unavailable(f"epoch {epoch.epoch}: {got.reason}")
            gap = gap or got
    if gone is not None:
        return Irreproducible(OBJECTS_MISSING, gone.reason)
    return () if gap is None else gap


def _decided(part: Part, findings: tuple[Finding, ...], ranges: object) -> Answer:
    if findings:
        return Answer(REFUSE, _findings_reason(findings), part, ranges, findings)
    return Answer(ALLOW, "", part, ranges)


def _findings_reason(findings: tuple[Finding, ...]) -> str:
    names = sorted({f"{f.kind}@{f.role}" for f in findings})
    return "perimeter_findings: " + ", ".join(names)


# --- op: merge -----------------------------------------------------------------------


class Adapter:
    """The perimeter journal of `load_trust` over the request (spec §5.4 «Протокол
    v2»): `status` — the publisher's preliminary state (its own observation not
    counted) only for the snapshot X, else `Unavailable(stale_snapshot)`;
    `record_finding` keeps one finding per (kind, role) for the verdict — the
    publisher registers them. A violation without its kind is not kept: it fails
    closed (`Unavailable`)."""

    def __init__(self, preliminary: str, expected_sha: str) -> None:
        self.preliminary = preliminary
        self.expected_sha = expected_sha
        self.findings: dict[tuple[str, str], Finding] = {}

    def status(self, snap: act_trust.TrustSnapshot) -> str | Unavailable:
        """Preliminary state of the root for X only."""
        if snap.root.snapshot_sha != self.expected_sha:
            return Unavailable(act_trust.STALE)
        return self.preliminary

    def record(
        self, snap: act_trust.TrustSnapshot, source: str, detail: str
    ) -> Unavailable | None:
        """Refused: the kind of the finding is unknown."""
        return Unavailable(f"journal adapter: a violation of {source} without kind")

    def record_finding(
        self, snap: act_trust.TrustSnapshot, kind: str, role: str, detail: str
    ) -> Unavailable | None:
        """Keep the finding (the first of its (kind, role))."""
        finding = Finding(kind, cast(Role, role), clip_detail(detail))
        self.findings.setdefault((kind, role), finding)
        return None

    def add(self, findings: tuple[Finding, ...]) -> None:
        """Findings established before `load_trust` (the pair and the settings)."""
        for f in findings:
            self.findings.setdefault((f.kind, f.role), f)


def _merge(req: MergeRequest, ctx: Context) -> Answer:
    """``op: merge``: `load_trust` through the adapter, then the kind's check."""
    binding = req.common.binding
    if binding.history_gh_config_dir is None:
        return Answer(REFUSE, f"{HISTORY_TOKEN_REQUIRED}: the binding has no key")
    if ctx.gh is None:
        return Answer(REFUSE, NO_APPROVED_GH)
    adapter = Adapter(PRELIMINARY, req.expected_snapshot_sha)
    seen: list[Part] = []
    trusted: list[act_trust.Trust] = []
    deps = _premerge_deps(req, ctx, adapter, seen, trusted)
    if req.kind == "candidate":
        code, reason = act_premerge_core.decide(_args(req), deps)
    else:
        code, reason = finalize(req, deps, ctx)
    part = seen[0] if seen else None
    findings = tuple(adapter.findings[k] for k in sorted(adapter.findings))
    ranges = None
    if part is not None and part.snapshot is not None:
        p_sha = req.policy.p_sha
        ranges = dataclasses.asdict(range_descriptor(part.snapshot.epochs, p_sha))
    # `load_trust`'s own unverified premise (`allow_rebase_merge` of the policy
    # source, read again there) joins the settings' codes (carry 9, review M3).
    extra = tuple(
        UNVERIFIED_OF_TRUST.get(name, name) for t in trusted for name in t.unverified
    )
    if findings:
        return Answer(REFUSE, _findings_reason(findings), part, ranges, findings, extra)
    outcome = {act_premerge_core.EXIT_OK: ALLOW, act_premerge_core.EXIT_REFUSED: REFUSE}
    return Answer(outcome.get(code, UNSET), reason, part, ranges, (), extra)


def _premerge_deps(
    req: MergeRequest,
    ctx: Context,
    adapter: Adapter,
    seen: list[Part],
    trusted: list[act_trust.Trust] | None = None,
) -> act_premerge_core.PremergeDeps:
    """`act_premerge_core` dependencies of the v2 path: approved `gh` only.
    `seen` gets the snapshot check, `trusted` the `Trust` of `load_trust`.

    The settings are read twice on this path — by `trust_part` and again by
    `load_trust` (`_rulesets`, `_rebase`, the history self-report): about six
    requests, accepted (review M14) and counted by the Task 15 measurement of the
    v2 `DEADLINE`."""
    if ctx.gh is None:
        raise ValueError("premerge deps without an approved gh")
    validation = validation_of(ctx.admin_digest)

    def snapshot(transport: Any, binding: act_trust.Binding) -> Any:
        part = trust_part(
            transport,
            binding,
            req.accepted,
            req.expected_snapshot_sha,
            req.policy,
            validation,
        )
        if isinstance(part, Irreproducible):  # `op: merge` keeps it unset
            return Unavailable(f"{part.code}: {part.detail}")
        if isinstance(part, Unavailable):
            return part
        seen[:] = [part]
        adapter.add(part.findings)
        if adapter.findings:
            return Unavailable(_findings_reason(part.findings))
        if part.snapshot is None:
            return Unavailable("invariant_violated: no snapshot and no finding")
        return part.snapshot

    return act_premerge_core.PremergeDeps(
        readers=act_trust.Readers(partial(act_trust.gh_get, gh=str(ctx.gh))),
        gh=ctx.deps.transport_for(req.token, req.api_base),
        binding=req.common.binding,
        journal=adapter,
        now=ctx.deps.wall,
        sleep=ctx.deps.sleep,
        creds=act_trust.COVERED_BY_KEY_HOLDER,
        token_source=ctx.token_source(),
        policy_head=req.policy.p_sha,
        snapshot=snapshot,
        surface=surface_check(req) if req.kind == "candidate" else None,
        on_trust=(trusted if trusted is not None else []).append,
    )


def _args(req: MergeRequest) -> argparse.Namespace:
    return argparse.Namespace(
        repo=req.full_name,
        pr=req.pr,
        head=req.head_sha,
        head_ref=req.head_ref,
        merger=req.merger_login,
        merger_type=req.merger_type,
    )


def surface_check(req: MergeRequest) -> act_premerge_core.Surface:
    """The D16 surface of the WHOLE PR (the diff to base the publisher computed):
    the manifest of the request changed by this PR, every path (both sides of a
    rename) inside `act_surface.surface_violations` of the bundle directory and the
    DAG variant of the product record (spec §5.4: the manifest is not the source
    of its own surface)."""

    def check(ident: RequestIdentity, manifest: NodeRequest) -> str | None:
        dag = DAG_VARIANTS.get(req.dag_variant)
        if dag is None:
            return f"dag variant {req.dag_variant!r} is not served in v1"
        own = manifest_path(ident.ws_id, ident.request_id)
        if not any(p == own and s in _CHANGE_OK for s, p, _prev in req.changes):
            return f"manifest {own} is not changed by this PR"
        paths = [p for _s, p, _prev in req.changes]
        paths += [prev for _s, _p, prev in req.changes if prev is not None]
        bad = act_surface.surface_violations(
            paths,
            ws_id=ident.ws_id,
            request_key=ident.request_id,
            nodes=tuple(manifest.nodes),
            bundle_dir=req.bundle_dir.replace("{ws}", ident.ws_id),
            dag=dag,
            act=ACT_REVIEW_V1,
        )
        return ", ".join(bad) if bad else None

    return check


# --- finalize ------------------------------------------------------------------------


def finalize(
    req: MergeRequest, deps: act_premerge_core.PremergeDeps, ctx: Context
) -> tuple[int, str]:
    """A finalize PR: its branch names the candidate's request; the delivered
    request is found in the base history; its delivery decision gives the act
    (`review_id`, N7); the PR changes exactly the expected envelopes, byte for
    byte at its head. `(EXIT_OK, "")` or the exit code and reason."""
    try:
        target = _finalize_target(req.head_ref)
        if isinstance(target, str):
            return act_premerge_core.EXIT_REFUSED, target
        trust = act_premerge_core._trust(deps)
        with act_precheck._snapshot(
            req, ctx.home, ctx.git, ctx.deps, ctx.deadline
        ) as ops:
            return _finalize_in_history(req, deps, trust, ops, target)
    except act_premerge_core._Stop as stop:
        return stop.code, str(stop)
    except OPS_FAILURES as exc:
        return act_premerge_core.EXIT_UNSET, f"read_failed: {exc}"


def _finalize_target(head_ref: str) -> tuple[str, str] | str:
    """(ws_id, request key) of the candidate branch a finalize branch extends,
    or a refusal reason."""
    suffix = approval_branches.finalize_suffix()
    if not head_ref.endswith(suffix):
        return f"finalize_branch: {head_ref!r} has no finalize suffix"
    found = act_premerge_core.request_of_branch(head_ref[: -len(suffix)])
    if found is None:
        return f"finalize_branch: {head_ref!r} is not a finalize branch"
    return found


def _finalize_in_history(
    req: MergeRequest,
    deps: act_premerge_core.PremergeDeps,
    trust: act_trust.Trust,
    ops: SnapshotHistory,
    target: tuple[str, str],
) -> tuple[int, str]:
    ws_id, key = target
    history = load_history(ops, Scope(ws_id, req.full_name, ""), req.base_tip)
    if isinstance(history, Unavailable):
        return act_premerge_core.EXIT_UNSET, f"history_unavailable: {history.reason}"
    found = [
        e
        for e in history
        if isinstance(e, DeliveredRequest) and e.request.request_id == key
    ]
    if len(found) != 1:
        return act_premerge_core.EXIT_REFUSED, f"no_delivered_request: {ws_id}/{key}"
    current = found[0]
    stale = [
        n for n in current.request.nodes if node_history(history, n).current != current
    ]
    if stale:
        return act_premerge_core.EXIT_REFUSED, f"request_not_current: nodes {stale}"
    pr = pr_for_merge(ops, req.full_name, current.merge_commit)
    if isinstance(pr, Unavailable):
        return act_premerge_core.EXIT_UNSET, f"candidate_pr: {pr.reason}"
    accepted = _delivery(deps, trust, ops, req.full_name, pr, current)
    if isinstance(accepted, tuple):
        return accepted
    return _envelopes(req, ops, ws_id, current, accepted)


def _delivery(
    deps: act_premerge_core.PremergeDeps,
    trust: act_trust.Trust,
    ops: SnapshotHistory,
    product: str,
    pr: int,
    current: DeliveredRequest,
) -> Accepted | tuple[int, str]:
    """The final delivery decision of the candidate PR (as `act_node._merged`)."""
    request = current.request
    outcome, _delivery_view = act_context.delivery_verdict(
        deps.readers,
        trust,
        deps.journal,
        product=product,
        pr=pr,
        paths=sorted(e.path for e in request.nodes.values()),
        request=request,
        request_act=ACT_REVIEW_V1,
        head_sha=current.head_sha,
        facts_transport=deps.gh,
        self_hashes=lambda: head_hashes(ops, request, current.head_sha),
        legacy=None,
    )
    if isinstance(outcome, Accepted):
        return outcome
    if isinstance(outcome, Rejected):
        return act_premerge_core.EXIT_REFUSED, (
            f"delivery_rejected: {outcome.code}: {outcome.detail}"
        )
    if isinstance(outcome, WaitingDelivery):
        return act_premerge_core.EXIT_UNSET, f"delivery_waiting: candidate PR #{pr}"
    why = getattr(outcome, "reason", type(outcome).__name__)
    return act_premerge_core.EXIT_UNSET, f"delivery_unset: {why}"


def head_hashes(
    ops: SnapshotHistory, request: NodeRequest, head: str
) -> dict[str, str] | Unavailable:
    """Self-hashes of the request's node files at the approved head (as
    `act_node._head_hashes`)."""
    hashes: dict[str, str] = {}
    for entry in request.nodes.values():
        text = ops.show_file("", head, entry.path)
        if text is None:
            return Unavailable(f"{entry.path} at {head[:12]} does not read")
        try:
            hashes[entry.path] = self_hash_v(text, ACT_REVIEW_V1)
        except ProjectionError as exc:
            return Unavailable(str(exc))
    return hashes


def _envelopes(
    req: MergeRequest,
    ops: SnapshotHistory,
    ws_id: str,
    current: DeliveredRequest,
    accepted: Accepted,
) -> tuple[int, str]:
    """The envelope commit form (`envelope_commit_defect`) on the whole PR."""
    if accepted.merge_commit != current.merge_commit:
        return act_premerge_core.EXIT_REFUSED, (
            f"merge_commit_mismatch: delivery decision {accepted.merge_commit[:12]}"
            f" != base history {current.merge_commit[:12]}"
        )
    delivered = DeliveredRequest(
        accepted.merge_commit, current.head_sha, current.request
    )
    expected = {
        envelope_path(ws_id, n): render_envelope(
            expected_envelope(delivered, n, accepted.review_id)
        )
        for n in current.request.nodes
    }
    touched = {p for _s, p, _prev in req.changes}
    plain = all(s in _CHANGE_OK and prev is None for s, _p, prev in req.changes)
    if touched != set(expected) or not plain:
        return act_premerge_core.EXIT_REFUSED, (
            f"envelope_set: the PR changes {sorted(touched)}, "
            f"expected exactly {sorted(expected)}"
        )
    for path, text in sorted(expected.items()):
        raw = ops.show_file_bytes("", req.head_sha, path)
        if raw is None:
            return act_premerge_core.EXIT_UNSET, f"envelope_unreadable: {path}"
        if raw != text.encode("utf-8"):
            return act_premerge_core.EXIT_REFUSED, f"envelope_content: {path}"
    return act_premerge_core.EXIT_OK, ""
