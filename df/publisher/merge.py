"""`merge` (spec 5.4; stage 2b, plan decision 8; stage 3a, Task 11): the kind
of a PR is routed by facts; stop, candidate and finalize PRs are merged, a
brief is refused until part C. A stop or finalize PR the precheck
(`df-precheck`) allows gets the App's attestation, then the one `PUT
.../merge` with the server-side `sha`; a candidate (the human act is the
approval) gets none. Every step past the intent is a durable phase, the PR
is owned by the request from its final read to its final result, and a
repeat reconciles from the phases (`recover_merge`) and never sends a second
`PUT`.

Candidate and finalize (spec 5.4 «Исполнение предпроверки», 6.1 «Маршруты и
наблюдения»): the root must be `clear` at the accepted head X before the
call (`perimeter_blocked`, `perimeter_unavailable: ...` otherwise); P is
pinned and the observation (A = X, P) of the `merge` route committed; the
precheck answers protocol v2 (`op: merge`), bound to X and the PR. A
verdict is never the admission: after the final read, one write transaction
of the trust state (`trust_state.admit_merge`) completes the observation,
registers its findings and applies I anew; only after its `COMMIT` come
`armed` and the `PUT`. A repeat without `armed` checks anew (a new
observation). Stop stays on protocol v1.

The guarantee rests on a premise (owner decision 03.11): for the served
repositories the App merges only through this one publisher with its durable
journal; a merge by the App around it is outside the guarantee and may be
counted for a request.

The branch patterns are the active release's own copy of
`contracts/approval-branches/v1/patterns.env` (read from the release tree,
not from the admin tree the publisher's code is imported from; parsed as
`KEY=VALUE` by the rules of `governance/ssot_env`, never executed). `df`
does not import `governance`, so the glob rule
of the contract header is repeated here and a test holds it equal to
`governance.approval_branches`.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from df import launch
from df.config import ConfigError, read_optional
from df.paths import Layout
from df.publisher import (
    github,
    ops,
    perimeter,
    precheck,
    store,
    trust_routes,
    trust_state,
)
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.ops import OpContext
from df.publisher.perimeter import Unavailable
from df.publisher.precheck import V2_KINDS, PrFacts
from df.publisher.request import Refused, Request, Response
from df.publisher.trust_state import PERIMETER_BLOCKED
from df.publisher.trust_types import RULES_VERSION
from df.stage2_config import TrustBinding
from df.state import Snapshot

if TYPE_CHECKING:
    from df.publisher.main import Context

Kind = Literal["candidate", "brief", "finalize", "stop"]

#: One changed file of a PR: `(status, path, previous_filename)`, as GitHub's
#: compare lists it (`previous_filename` only for a rename).
Change = tuple[str, str, str | None]

#: Per-segment path templates (`*` never crosses `/`); a test holds them equal
#: to `governance.act_envelope.envelope_path` and `stop_path`.
ENVELOPE_PATH = ("workstreams", "*", "approvals", "*.yaml")
STOP_PATH = ("workstreams", "*", "approvals", "stops", "*-g*.yaml")
BRIEF_GLOB = "brief/*"
#: `compare` lists at most 300 files and does not say when it cut the list:
#: that many or more is refused (the rule of `merge-pr.sh`).
COMPARE_FILE_CAP = 300
_CANDIDATE_KEY = "APPROVAL_CANDIDATE_TEMPLATE"
_FINALIZE_KEY = "APPROVAL_FINALIZE_SUFFIX"
_STOP_KEY = "APPROVAL_STOP_TEMPLATE"
_STOP_PLACEHOLDERS = ("{ws_id}", "{node}", "{generation}")
#: The merge method of the one `PUT` (rev 10: rebase is forbidden by spec A,
#: a linear-history ruleset refuses a merge commit).
MERGE_METHOD = "squash"
#: The body of the App's attestation review; the publisher writes all of it.
ATTESTATION_MARKER = (
    "<!-- df-publisher:v1 attestation={kind} request_key={key} head_sha={sha} -->"
)
#: The token of the `PUT` only (the product's repository only).
MERGE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
#: `TrustRoot` without a `df-trust` binding (spec 5.5, rev 10); with one it
#: is the binding's identity (`trust_root`).
TRUST_ROOT: Mapping[str, str] = {"unavailable": "no_binding"}
#: The kinds of a merge request, as the intent records them.
KINDS: tuple[Kind, ...] = ("stop", *V2_KINDS)
#: HTTP answers of the `PUT` that settle the one attempt (decision 8): a 4xx
#: was not executed, so the only `PUT` of the request will not merge (spec
#: 5.4: a definite negative answer is a verified refusal). A transient 401,
#: 403 or 404 (a rate limit, a permission hiccup) therefore costs
#: availability, not safety: the request is refused for good and the operator
#: retries with a new `request_id`.
DEFINITE_PUT = frozenset({200, 401, 403, 404, 405, 409, 422})
#: Of those, the ones that mean "not merged" (with the PR still open).
_NOT_MERGEABLE = frozenset({405, 422})
_PUT_REFUSED = frozenset({401, 403, 404})
#: Base tip moves the final read follows before it gives up (`base_unstable`).
MAX_RECHECKS = 5
#: What a `done` found by observation (not by the `PUT`'s own 200) rests on.
PREMISE = "app_merges_only_through_this_publisher"
FACT_KEYS = ("head_repo_id", "head_ref", "base_ref", "base_tip", "changes_digest")
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_ADJACENT_STARS = re.compile(r"\*[-._]\*")


# --- patterns -----------------------------------------------------------------


PATTERNS_RELATIVE = ("contracts", "approval-branches", "v1", "patterns.env")


def patterns_path(layout: Layout, snapshot: Snapshot) -> Path:
    """`patterns.env` in the active release's source tree (`patterns_unavailable`
    if no release is active). Never relative to this module: a release is
    judged by its own copy of the contract, not by the copy the admin tree
    happens to carry (since 2b it holds `contracts/` too, for the precheck)."""
    if snapshot.release_id is None:
        raise Refused("patterns_unavailable")
    try:
        release = layout.release_dir(snapshot.release_id)
    except ConfigError:
        raise Refused("patterns_unavailable") from None
    return release / "src" / launch.RELEASE_REPO / Path(*PATTERNS_RELATIVE)


def read_patterns(layout: Layout, snapshot: Snapshot) -> dict[str, str]:
    """The three keys of `patterns.env`; anything else than a whole file with
    all three keys (and a stop template of the known shape) is
    `patterns_unavailable`."""
    try:
        text = read_optional(patterns_path(layout, snapshot))
    except ConfigError:
        raise Refused("patterns_unavailable") from None
    if text is None:
        raise Refused("patterns_unavailable")
    return parse_patterns(text)


def _definitions(text: str, key: str) -> list[str]:
    """The values of the lines that define `key`: the rules of
    `governance.ssot_env.definition_lines` (strip the line, skip blanks and
    comments, a definition starts with exactly `KEY=`, strip the value)."""
    prefix = f"{key}="
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.startswith(prefix):
            found.append(stripped[len(prefix) :].strip())
    return found


def parse_patterns(text: str) -> dict[str, str]:
    """All three keys; any other line is ignored, as in the contract's other two
    halves. A missing key, a duplicate definition or an empty value is
    `patterns_unavailable`."""
    found: dict[str, str] = {}
    for key in (_CANDIDATE_KEY, _FINALIZE_KEY, _STOP_KEY):
        values = _definitions(text, key)
        if len(values) != 1 or not values[0]:
            raise Refused("patterns_unavailable")
        found[key] = values[0]
    placeholders = _PLACEHOLDER.findall(found[_STOP_KEY])
    if sorted(placeholders) != sorted(_STOP_PLACEHOLDERS):
        raise Refused("patterns_unavailable")
    return found


def _template_glob(template: str) -> str:
    """The contract's rule: every `{...}` becomes `*`, then `*`s apart by one
    separator collapse into one, until nothing changes."""
    glob = _PLACEHOLDER.sub("*", template)
    while True:
        collapsed = _ADJACENT_STARS.sub("*", glob)
        if collapsed == glob:
            return glob
        glob = collapsed


def _stop_regex(template: str, ws_id: str) -> re.Pattern[str]:
    """The rule of `governance.approval_branches.parse_stop_branch`: the
    template with a known `ws_id`, a node of one non-empty path segment, a generation
    of digits."""
    groups = {"node": "(?P<node>[^/]+)", "generation": "(?P<generation>[0-9]+)"}
    out = []
    for part in re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", template):
        if _PLACEHOLDER.fullmatch(part) is None:
            out.append(re.escape(part))
        elif part == "{ws_id}":
            out.append(re.escape(ws_id))
        else:
            out.append(groups.get(part[1:-1], ".+"))
    return re.compile("".join(out))


def _stop_path_of_branch(head_ref: str, ws_id: str, template: str) -> str | None:
    """The record path the stop branch `head_ref` of workstream `ws_id` names
    (canonical forms only: the branch is rebuilt from what was parsed)."""
    found = _stop_regex(template, ws_id).fullmatch(head_ref)
    if found is None or "generation" not in found.groupdict():
        return None
    node, generation = found["node"], int(found["generation"])
    if (
        template.format_map({"ws_id": ws_id, "node": node, "generation": generation})
        != head_ref
    ):
        return None
    return f"workstreams/{ws_id}/approvals/stops/{node}-g{generation}.yaml"


# --- routing ------------------------------------------------------------------


def branch_kinds(head_ref: str, patterns: Mapping[str, str]) -> set[Kind]:
    """The kinds whose branch form `head_ref` has. A finalize branch is also
    of the candidate form; it is only a finalize one."""
    candidate = _template_glob(patterns[_CANDIDATE_KEY])
    finalize = candidate + patterns[_FINALIZE_KEY]
    kinds: set[Kind] = set()
    if fnmatchcase(head_ref, finalize):
        kinds.add("finalize")
    elif fnmatchcase(head_ref, candidate):
        kinds.add("candidate")
    if fnmatchcase(head_ref, BRIEF_GLOB):
        kinds.add("brief")
    if fnmatchcase(head_ref, _template_glob(patterns[_STOP_KEY])):
        kinds.add("stop")
    return kinds


def _matches(path: str, template: Sequence[str]) -> bool:
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return len(parts) == len(template) and all(
        fnmatchcase(part, pattern)
        for part, pattern in zip(parts, template, strict=True)
    )


def _is_stop_change(
    head_ref: str, changes: Sequence[Change], patterns: Mapping[str, str]
) -> bool:
    """A stop PR is exactly one added record whose path is under `STOP_PATH`
    and equals the path its branch names (the workstream is the path's)."""
    if len(changes) != 1:
        return False
    status, path, previous = changes[0]
    if status != "added" or previous is not None:
        return False
    if not _matches(path, STOP_PATH):
        return False
    named = _stop_path_of_branch(head_ref, path.split("/")[1], patterns[_STOP_KEY])
    return named == path


def route(
    head_ref: str, changes: Sequence[Change], patterns: Mapping[str, str]
) -> Kind | Refused:
    """The kind of the PR, or `ambiguous_kind`: a branch outside the forms or
    of two, a stop record among the paths of a PR that is not a stop one (the
    old path of a rename counts), a stop branch that is not exactly one added
    record it names, a finalize branch with any path but an envelope or with no
    path at all."""
    paths = [p for _, path, previous in changes for p in (path, previous) if p]
    kinds = branch_kinds(head_ref, patterns)
    if len(kinds) != 1:
        return Refused("ambiguous_kind")
    (kind,) = kinds
    if kind == "stop":
        return (
            kind
            if _is_stop_change(head_ref, changes, patterns)
            else Refused("ambiguous_kind")
        )
    if any(_matches(p, STOP_PATH) for p in paths):
        return Refused("ambiguous_kind")
    if kind == "brief" and any(_matches(p, ENVELOPE_PATH) for p in paths):
        return Refused("ambiguous_kind")
    if kind == "finalize" and not (
        paths and all(_matches(p, ENVELOPE_PATH) for p in paths)
    ):
        return Refused("ambiguous_kind")
    return kind


# --- the operation ------------------------------------------------------------


def _head_ref(pr: Mapping[str, object]) -> str:
    head = pr.get("head")
    ref = head.get("ref") if isinstance(head, dict) else None
    if not isinstance(ref, str) or not ref:
        raise GitHubUnavailable("pr: no head ref")
    return ref


def _check_head_repo(oc: OpContext, pr: Mapping[str, object]) -> None:
    """A fork picks its own branch names: only the product's own branches
    are routed by name (`head_repo_foreign`)."""
    head = pr.get("head")
    repo = head.get("repo") if isinstance(head, dict) else None
    ident = repo.get("id") if isinstance(repo, dict) else None
    if isinstance(ident, bool) or ident != oc.product.repository_id:
        raise Refused("head_repo_foreign")


def _change_of(item: object) -> Change:
    """The change a file entry of the compare describes (`status` is required);
    a rename keeps its old path, or a rename out of `approvals/stops/` would
    escape the stop check."""
    if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
        raise GitHubUnavailable("files: unexpected item")
    status = item.get("status")
    previous = item.get("previous_filename")
    if (
        not isinstance(status, str)
        or not status
        or not (previous is None or isinstance(previous, str))
    ):
        raise GitHubUnavailable("files: unexpected item")
    return (status, item["filename"], previous)


def _get(oc: OpContext, token: str, path: str) -> object:
    _, data = github.call(
        "GET", path, token, transport=oc.transport, api_base=oc.api_base
    )
    return data


def _base_ref(pr: Mapping[str, object]) -> str:
    base = pr.get("base")
    ref = base.get("ref") if isinstance(base, dict) else None
    if not isinstance(ref, str) or not ref:
        raise GitHubUnavailable("pr: no base ref")
    return ref


def base_tip(oc: OpContext, token: str, ref: str) -> str:
    """The live tip of the PR's base branch (`git/ref/heads/<ref>`, as in
    `merge-pr.sh`: the commit the compare starts at)."""
    name = quote(ref, safe="/")
    data = _get(oc, token, f"/repos/{oc.product.full_name}/git/ref/heads/{name}")
    obj = data.get("object") if isinstance(data, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise GitHubUnavailable("ref: no object sha")
    return sha


def pr_changes(
    oc: OpContext, token: str, tip: str, head_sha: str
) -> tuple[Change, ...]:
    """Every change of the diff `tip...head_sha`: two fixed commits, so the list
    cannot change under us (the PR's own files listing follows the live head).
    `COMPARE_FILE_CAP` files or more is `too_many_files`."""
    path = f"/repos/{oc.product.full_name}/compare/{tip}...{head_sha}"
    data = _get(oc, token, path)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        raise GitHubUnavailable("compare: no files")
    if len(files) >= COMPARE_FILE_CAP:
        raise Refused("too_many_files")
    return tuple(_change_of(item) for item in files)


# --- the merge (stage 2b, plan decision 8; stage 3a, Task 11) ----------------


@dataclass(frozen=True)
class Pending:
    """A bound positive `op: merge` verdict and its observation, waiting for
    the final admission (`_admit`)."""

    obs: trust_state.Observation
    verdict: trust_routes.Verdict


@dataclass
class TrustRun:
    """The trust side of a candidate or finalize merge: the session, the
    merger (the App's bot), the bundle directory of the PR's workstream and
    the positive verdict of this call waiting for the admission (`None`
    before a check, after the admission, and in a repeat: then the final
    read checks anew)."""

    sess: trust_routes.Session
    merger: str
    bundle_dir: str
    pending: Pending | None = None


@dataclass(frozen=True)
class MergeRun:
    """What one `merge` request works with past the precheck: the product's
    context, the bot and its review token, a read token, the request, its
    reservation (the phases are its files), the release's patterns, the
    PR's kind, the trust side (candidate and finalize) and the evidence of
    the kind and the root the intent recorded."""

    oc: OpContext
    auth: ops.Auth
    read: str = field(repr=False)
    req: Request
    res: store.Reservation
    patterns: Mapping[str, str]
    kind: Kind = "stop"
    trust: TrustRun | None = None
    base: Mapping[str, object] = field(
        default_factory=lambda: {"kind": "stop", "trust_root": TRUST_ROOT}
    )

    @property
    def number(self) -> int:
        """The PR's number."""
        return int(str(self.req.fields["pr"]))

    @property
    def head_sha(self) -> str:
        """The head the request names (and the `PUT`'s server-side `sha`)."""
        return str(self.req.fields["head_sha"])

    @property
    def repository_id(self) -> int:
        """The product's repository."""
        return self.oc.product.repository_id


Phases = dict[str, Mapping[str, object]]


def _decide(ctx: Context) -> Response:
    oc = ops.build_context(ctx)
    patterns = read_patterns(ctx.layout, ctx.snapshot)
    ops.self_check_first(oc)
    # compare and branches need Contents read; pull_requests read alone is a 403
    read = ops.authorize(oc, ops.READ_PERMISSIONS).value
    req, res = ctx.request, ctx.reservation
    if ctx.intent is not None:
        return _recover(ctx, oc, read, patterns, ctx.intent)
    if store.read_phases(res):  # the intent is gone, the phases are not
        raise OSError("merge: phases without an intent")
    number, head_sha = int(str(req.fields["pr"])), str(req.fields["head_sha"])
    pr, facts = _read_facts(oc, read, number, head_sha)
    kind = _check_kind(route(facts.head_ref, facts.changes, patterns))
    trust, auth = None, None
    if kind in V2_KINDS:
        trust, auth = _trust_run(
            ctx, oc, kind, facts.head_ref, patterns, lambda: ops.review_auth(oc)
        )
    if store.pr_busy(ctx.layout, oc.product.repository_id, number, res.key):
        raise Refused("pr_busy", evidence=_base_evidence(kind, oc.binding))

    def first() -> Response:
        checked = _precheck(oc, pr, facts, kind, trust)
        bot = auth or ops.review_auth(oc)
        x = checked.get("snapshot_sha")
        intent = {
            "request_key": res.key,
            "head_sha": head_sha,
            "kind": kind,
            "facts": _facts_record(facts),
            "trust_root": trust_root(oc.binding, None if x is None else str(x)),
        }
        ctx.record_intent(intent)
        base = _base_of(intent)
        run = MergeRun(oc, bot, read, req, res, patterns, kind, trust, base)
        return _after_check(run, intent, {}, checked)

    return _settling(oc, trust, first)


def _settling(
    oc: OpContext, trust: TrustRun | None, act: Callable[[], Response]
) -> Response:
    """`act`, then (review I1) a positive verdict of this call that did not
    reach the admission -- an `unknown`, a refusal, any exception -- completes
    its observation (`admit=False`: `accepted`/`superseded`, no `PUT`), so a
    repeat can check anew. Best effort: the answer is never masked. A crash
    (`BaseException`) leaves it to re-evaluation: a lost answer."""
    try:
        resp = act()
    except Exception:
        _leave(oc, trust)
        raise
    _leave(oc, trust)
    return resp


def _leave(oc: OpContext, trust: TrustRun | None) -> None:
    if trust is None or trust.pending is None:
        return
    try:
        _settle_pending(oc, trust)
    except Exception as exc:  # noqa: BLE001 - the answer stays the one given
        print(f"publisher: merge observation not settled: {exc}", file=sys.stderr)


def _recover(
    ctx: Context,
    oc: OpContext,
    read: str,
    patterns: Mapping[str, str],
    intent: Mapping[str, object],
) -> Response:
    """A repeat: the run of the recorded kind; past `armed` it only observes
    the PR, otherwise a candidate or finalize gets its trust side anew (its
    refusal is verified: no `PUT` was sent)."""
    req, res = ctx.request, ctx.reservation
    phases = store.read_phases(res)
    kind = intent.get("kind")
    if kind not in KINDS:
        raise OSError("merge: not an intent of this request")
    run = MergeRun(
        oc, ops.review_auth(oc), read, req, res, patterns, kind, None, _base_of(intent)
    )
    _check_intent(run, intent)
    if kind in V2_KINDS and "armed" not in phases:
        head_ref = str(_facts_of(intent)["head_ref"])
        try:
            trust, _ = _trust_run(ctx, oc, kind, head_ref, patterns, lambda: run.auth)
        except Refused as exc:
            raise _verified(run, exc, dict(phases)) from None
        run = replace(run, trust=trust)
    return _settling(oc, run.trust, lambda: recover_merge(run, intent, phases))


def trust_root(binding: TrustBinding | None, snapshot: str | None) -> dict[str, object]:
    """`TrustRoot` of the evidence (spec 5.5, rev 11): the binding's identity
    for every kind, with X (`snapshot_sha`) for candidate and finalize;
    `TRUST_ROOT` without a binding."""
    if binding is None:
        return dict(TRUST_ROOT)
    host, repository_id, branch = binding.root_id
    root: dict[str, object] = {
        "host": host,
        "repository_id": repository_id,
        "branch": branch,
    }
    return root if snapshot is None else {**root, "snapshot_sha": snapshot}


def _base_evidence(
    kind: Kind, binding: TrustBinding | None, snapshot: str | None = None
) -> dict[str, object]:
    return {"kind": kind, "trust_root": trust_root(binding, snapshot)}


def _base_of(intent: Mapping[str, object]) -> dict[str, object]:
    """The kind and the root as the intent recorded them (an intent of 2b
    has no `trust_root`: it had no binding)."""
    root = intent.get("trust_root", TRUST_ROOT)
    return {"kind": intent.get("kind"), "trust_root": root}


def _read_facts(
    oc: OpContext, read: str, number: int, head_sha: str
) -> tuple[dict[str, object], PrFacts]:
    """The PR read twice around the compare (head and base did not move),
    and the facts the publisher checked itself (requirement 4)."""
    first = ops.read_pr(oc, read, number, head_sha)
    head_ref = _head_ref(first)
    _check_head_repo(oc, first)
    tip = base_tip(oc, read, _base_ref(first))
    changes = pr_changes(oc, read, tip, head_sha)
    pr = ops.read_pr(oc, read, number, head_sha)
    if _base_ref(pr) != _base_ref(first) or _head_ref(pr) != head_ref:
        raise Refused("pr_state")
    facts = PrFacts(oc.product.repository_id, head_ref, _base_ref(pr), tip, changes)
    return pr, facts


def _check_kind(kind: Kind | Refused) -> Kind:
    """A brief waits for part C; every other kind goes on."""
    if isinstance(kind, Refused):
        raise kind
    if kind == "brief":
        raise Refused("brief_precheck_unavailable", evidence={"kind": kind})
    return kind


# --- the trust side of candidate and finalize (stage 3a, Task 11) -------------


def _trust_run(
    ctx: Context,
    oc: OpContext,
    kind: Kind,
    head_ref: str,
    patterns: Mapping[str, str],
    auth: Callable[[], ops.Auth],
) -> tuple[TrustRun, ops.Auth]:
    """The trust side and the bot's auth: a binding (`no_binding`) with a
    history key (`history_token_required`, O14: `op: merge` never reads
    anonymously), the bundle directory of the PR's workstream and the
    session (the history token and the publisher's self-report on the trust
    sources). `auth` is called last: a first call mints the review token
    only after these checks (a repeat has it already)."""
    binding = oc.binding
    if binding is None:
        raise Refused("no_binding")
    if binding.history_gh_config_dir is None:
        raise Refused(trust_routes.HISTORY_TOKEN_REQUIRED)
    bundle = bundle_dir(oc.product.bundle_dir, kind, head_ref, patterns)
    sess = trust_routes.session(ctx, binding)
    if isinstance(sess, Unavailable):
        raise Refused(f"perimeter_unavailable: {sess.reason}")
    got = auth()
    return TrustRun(sess, got.bot, bundle), got


def bundle_dir(
    template: str, kind: Kind, head_ref: str, patterns: Mapping[str, str]
) -> str:
    """The product record's bundle directory for the PR's workstream (the
    `ws_id` of its candidate branch): a template without `{ws}` would share
    one directory across workstreams (`bundle_dir_without_ws`); the result
    stays a relative path inside the product (`bundle_dir_invalid`)."""
    if "{ws}" not in template:
        raise Refused("bundle_dir_without_ws")
    ws = ws_of_branch(head_ref, kind, patterns)
    if ws is None:
        raise Refused("ambiguous_kind")
    # every `{ws}` (not `.replace`: `tests/df/test_fsutil.py` counts those)
    expanded = ws.join(template.split("{ws}"))
    path = PurePosixPath(expanded)
    if path.is_absolute() or ".." in path.parts:
        raise Refused("bundle_dir_invalid")
    return expanded


def ws_of_branch(head_ref: str, kind: Kind, patterns: Mapping[str, str]) -> str | None:
    """The `ws_id` of a candidate branch (a finalize one without its suffix),
    by the rule of `act_premerge_core.request_of_branch`: `{ws_id}` the
    shortest match, every other placeholder digits."""
    ref = head_ref
    if kind == "finalize":
        suffix = patterns[_FINALIZE_KEY]
        if not ref.endswith(suffix):
            return None
        ref = ref[: -len(suffix)]
    out = []
    for part in re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", patterns[_CANDIDATE_KEY]):
        if _PLACEHOLDER.fullmatch(part) is None:
            out.append(re.escape(part))
        else:
            out.append("(?P<ws_id>.+?)" if part == "{ws_id}" else "[0-9]+")
    found = re.fullmatch("".join(out), ref)
    if found is None or "ws_id" not in found.groupdict():
        return None
    return found["ws_id"]


def _precheck(
    oc: OpContext,
    pr: Mapping[str, object],
    facts: PrFacts,
    kind: Kind,
    trust: TrustRun | None,
) -> dict[str, object]:
    """Ask `df-precheck`; its metadata (with the base tip it judged, and X
    for candidate and finalize) or the refusal. The publisher's own checks
    ran before and do not depend on it."""
    if trust is None:
        result = precheck.admin_precheck("stop", oc, pr, facts)
        checked = {**result.evidence(), "base_tip": facts.base_tip}
        snapshot = None
    else:
        try:
            result, snapshot = _trust_precheck(oc, pr, facts, kind, trust)
        except Refused as exc:  # review M4: the kind and the root, as elsewhere
            evidence = {**_base_evidence(kind, oc.binding), **exc.evidence}
            raise Refused(
                exc.reason, verified_not_done=exc.verified_not_done, evidence=evidence
            ) from None
        checked = {**result.evidence(), "base_tip": facts.base_tip}
        checked["snapshot_sha"] = snapshot
    if result.refused is not None:
        evidence = {**_base_evidence(kind, oc.binding, snapshot), "precheck": checked}
        raise Refused(result.refused.reason, evidence=evidence)
    return checked


def _trust_precheck(
    oc: OpContext,
    pr: Mapping[str, object],
    facts: PrFacts,
    kind: Kind,
    trust: TrustRun,
) -> tuple[precheck.PrecheckResult, str]:
    """Protocol v2: the observation of the `merge` route opened durably (the
    root `clear` at X before it), the precheck asked, its bound verdict
    settled -- a positive one waits for the admission (`trust.pending`).
    The result and X."""
    _settle_pending(oc, trust)
    sess = trust.sess
    try:
        with perimeter.open_db(oc.layout) as db:
            obs = _merge_observation(sess, db)
            mt = trust_routes.MergeTrust(
                sess,
                obs,
                trust.merger,
                trust.bundle_dir,
                trust_state.ranges_digest(db.conn, obs.id),
                trust_routes.same_head(sess, db, obs),
            )
            result = precheck.admin_precheck(kind, oc, pr, facts, obs=mt)
            _after_verdict(trust, db, obs, result.verdict)
    except Unavailable as exc:
        raise Refused(f"perimeter_unavailable: {exc.reason}") from None
    return result, obs.head


def _merge_observation(
    sess: trust_routes.Session, db: perimeter.Db
) -> trust_state.Observation:
    """The preliminary state (I without this call's observation, spec 6.1):
    `clear` at the accepted head X, else refused before the checker; then
    P pinned (its limits first: `unsupported_size: <field>`, never a fake
    P) and the observation (A = X, X, P) of the `merge` route committed."""
    perimeter.admit(db, trust_routes.AcceptedEpochs(sess, db), sess.root)
    name, why, x, _epoch = trust_routes.root_state(sess, db).fields()
    if name == "blocked":
        raise Refused(PERIMETER_BLOCKED)
    if name != "clear" or x is None:
        raise Refused(f"perimeter_unavailable: {why}")
    problem = trust_routes.binding_problem(sess.binding)
    if problem is not None:
        raise Refused(f"unsupported_size: {problem}")
    policy = trust_routes.pin_policy(sess.transport, sess)
    if isinstance(policy, Unavailable):
        raise policy
    if isinstance(policy, trust_routes.OutOfLimits):
        raise Refused(f"unsupported_size: {policy.field}")
    binding, admin = sess.binding, sess.admin_digest
    ctx = trust_state.ctx_id("merge", binding, admin, RULES_VERSION, x, x, policy)
    obs = trust_state.open_observation(
        db,
        sess.root,
        ctx,
        route="merge",
        binding=binding,
        admin_digest=admin,
        rules=RULES_VERSION,
        accepted=x,
        head=x,
        policy=policy,
    )
    if sess.own is not None:  # the publisher's own self-report, as `observe`
        own = [sess.own]
        trust_state.register_findings(db, sess.root, obs.ctx, own, sess.epochs_of())
        raise Refused(PERIMETER_BLOCKED)
    return obs


def _after_verdict(
    trust: TrustRun,
    db: perimeter.Db,
    obs: trust_state.Observation,
    verdict: trust_routes.Verdict | None,
) -> None:
    """No bound verdict (timeout, failed exit, unbound): the observation
    stays unfinished, no K (its inputs are pinned). A binding changed during
    the check: K before any write (`perimeter_blocked`). Else the ranges
    digest is pinned at the first bound verdict; a positive verdict waits
    for the admission, any other completes the observation by it
    (`admit_merge` without step 3)."""
    sess = trust.sess
    if verdict is None:
        trust_state.unresolved(db, sess.root, obs.id)
        return
    if trust_routes.binding_moved(sess):
        trust_routes.conservative_of(db, sess.root, obs, sess.epochs_of())
        raise Refused(PERIMETER_BLOCKED)
    if verdict.ranges is not None:
        digest = verdict.ranges.digest
        if trust_state.pin_ranges(db, sess.root, obs.id, digest) != digest:
            trust_state.unresolved(db, sess.root, obs.id)
            raise Refused("precheck_failed: binding_mismatch: ranges")
    if verdict.outcome == trust_routes.ALLOW:
        trust.pending = Pending(obs, verdict)
        return
    _admission(trust.sess, db, Pending(obs, verdict), admit=False)


def _settle_pending(oc: OpContext, trust: TrustRun) -> None:
    """A positive verdict of this call that is not admitted (a new check
    replaces it, or the call ends otherwise): its observation is completed
    by it -- or by K when the binding moved since the check (Task 8 M7,
    re-review R1-I1: K before any write that follows the checker)."""
    pending, trust.pending = trust.pending, None
    if pending is None:
        return
    sess = trust.sess
    try:
        with perimeter.open_db(oc.layout) as db:
            if trust_routes.binding_moved(sess):
                trust_routes.conservative_of(
                    db, sess.root, pending.obs, sess.epochs_of()
                )
                return
            _admission(sess, db, pending, admit=False)
    except Unavailable as exc:
        raise Refused(f"perimeter_unavailable: {exc.reason}") from None


def _admission(
    sess: trust_routes.Session, db: perimeter.Db, pending: Pending, *, admit: bool
) -> None:
    refused = trust_state.admit_merge(
        db,
        sess.root,
        pending.obs.id,
        pending.verdict,
        head_read=sess.head,
        binding=sess.binding,
        admin_digest=sess.admin_digest,
        rules=RULES_VERSION,
        admit=admit,
    )
    if refused is not None:
        raise refused


def _admit(run: MergeRun) -> None:
    """The final admission of this call's positive verdict (spec 6.1, R4),
    after the final read and before `armed`: K first if the binding moved
    since the check; then `trust_state.admit_merge` in one transaction.
    Nothing to admit -- the final read did not check -- is a slip."""
    trust = run.trust
    if trust is None:
        return
    pending, trust.pending = trust.pending, None
    if pending is None:
        raise OSError("merge: no verdict to admit")
    sess = trust.sess
    try:
        with perimeter.open_db(run.oc.layout) as db:
            if trust_routes.binding_moved(sess):
                trust_routes.conservative_of(
                    db, sess.root, pending.obs, sess.epochs_of()
                )
                raise Refused(PERIMETER_BLOCKED)
            _admission(sess, db, pending, admit=True)
    except Unavailable as exc:
        trust.pending = pending  # rolled back: `_settling` completes it
        raise Refused(f"perimeter_unavailable: {exc.reason}") from None


def changes_digest(changes: Sequence[Change]) -> str:
    """sha256 of the canonical JSON of the PR's changes."""
    canonical = json.dumps([list(c) for c in changes], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _facts_record(facts: PrFacts) -> dict[str, object]:
    return {
        "head_repo_id": facts.head_repo_id,
        "head_ref": facts.head_ref,
        "base_ref": facts.base_ref,
        "base_tip": facts.base_tip,
        "changes_digest": changes_digest(facts.changes),
    }


# --- phases -------------------------------------------------------------------


def recover_merge(
    run: MergeRun, intent: Mapping[str, object], phases: Mapping[str, Mapping]
) -> Response:
    """Go on from the phases an earlier attempt left (the table of decision
    8): nothing is done again, only the next allowed step. After `armed` the
    PR is only observed: the one `PUT` is never sent again."""
    _check_intent(run, intent)
    found: Phases = dict(phases)
    if "armed" in found:
        return _settle(run, _facts_of(intent), found)
    if "put_answer" in found:
        raise OSError("merge: put_answer without armed")
    if "attested" in found or "checked" in found:
        return _arm(run, intent, found)
    if "attest" in found:
        return _confirmed(run, intent, found)
    return _resume(run, intent, found)


def _check_intent(run: MergeRun, intent: Mapping[str, object]) -> None:
    facts = intent.get("facts")
    ok = (
        intent.get("request_key") == run.res.key
        and intent.get("head_sha") == run.head_sha
        and intent.get("kind") in KINDS
        and isinstance(facts, dict)
        and sorted(facts) == sorted(FACT_KEYS)
    )
    if not ok:
        raise OSError("merge: not an intent of this request")


def _facts_of(intent: Mapping[str, object]) -> Mapping[str, object]:
    facts = intent["facts"]
    assert isinstance(facts, dict)
    return facts


def _write(run: MergeRun, phases: Phases, name: str, payload: Mapping) -> None:
    store.phase(run.res, name, payload)
    phases[name] = dict(payload)


def _verified(run: MergeRun, exc: Refused | str, phases: Phases) -> Refused:
    """A refusal past the intent: nothing was merged by this request (its one
    `PUT` was not sent, or was answered no), so it is final."""
    reason = exc if isinstance(exc, str) else exc.reason
    extra = {} if isinstance(exc, str) else exc.evidence
    evidence = {**_evidence(run, phases), **extra}
    return Refused(reason, verified_not_done=True, evidence=evidence)


#: Facts a moved base tip changes: they are checked again, not compared.
_TIP_FACTS = ("base_tip", "changes_digest")


def _stable(facts: Mapping[str, object]) -> dict[str, object]:
    return {k: v for k, v in facts.items() if k not in _TIP_FACTS}


def _resume(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> Response:
    """Only the intent: the PR must still be what it was (a moved base tip
    is a recheck, spec 5.4: routing and the precheck run on the current
    compare); then the attestation."""
    try:
        pr, facts = _read_facts(run.oc, run.read, run.number, run.head_sha)
        if _stable(_facts_record(facts)) != _stable(_facts_of(intent)):
            raise Refused("pr_facts_changed")
        _same_kind(run, route(facts.head_ref, facts.changes, run.patterns))
        if store.pr_busy(run.oc.layout, run.repository_id, run.number, run.res.key):
            raise Refused("pr_busy")
        checked = _precheck(run.oc, pr, facts, run.kind, run.trust)
    except Refused as exc:
        raise _verified(run, exc, phases) from None
    except GitHubRefused as exc:
        raise _verified(run, ops.refusal_reason(exc), phases) from None
    return _after_check(run, intent, phases, checked)


def _same_kind(run: MergeRun, kind: Kind | Refused) -> None:
    """The PR routes to the kind its intent recorded."""
    if _check_kind(kind) != run.kind:
        raise Refused("ambiguous_kind")


def _after_check(
    run: MergeRun,
    intent: Mapping[str, object],
    phases: Phases,
    checked: Mapping[str, object],
) -> Response:
    """Past a positive check: stop and finalize get the App's attestation; a
    candidate, approved by the human act, only records the check
    (`checked`) and goes on to the final read."""
    if run.kind != "candidate":
        return _attest(run, intent, phases, checked)
    _write(run, phases, "checked", {"precheck": dict(checked)})
    return _arm(run, intent, phases)


# --- the attestation ----------------------------------------------------------


def attestation_marker(run: MergeRun) -> str:
    """The whole body of this request's attestation."""
    return ATTESTATION_MARKER.format(kind=run.kind, key=run.res.key, sha=run.head_sha)


def _attest(
    run: MergeRun,
    intent: Mapping[str, object],
    phases: Phases,
    checked: Mapping[str, object],
) -> Response:
    """`attest` (with the precheck's metadata), then the one `POST`; whatever
    its answer, the listing confirms the review."""
    _write(run, phases, "attest", {"precheck": dict(checked)})
    body = {
        "commit_id": run.head_sha,
        "event": "APPROVE",
        "body": attestation_marker(run),
    }
    path = f"/repos/{run.oc.product.full_name}/pulls/{run.number}/reviews"
    try:
        github.call(
            "POST",
            path,
            run.auth.token,
            body,
            transport=run.oc.transport,
            api_base=run.oc.api_base,
        )
    except GitHubRefused as exc:
        raise _verified(run, ops.refusal_reason(exc), phases) from None
    except GitHubUnavailable:
        pass  # the review may exist all the same: the listing decides
    return _confirmed(run, intent, phases)


def _confirmed(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> Response:
    """After `attest`: exactly one own attestation is `attested`; none or
    several is `unknown`, and no second `POST` is made."""
    found = _find_attestation(run, phases)
    if isinstance(found, Response):
        return found
    _write(run, phases, "attested", {"review_id": found})
    return _arm(run, intent, phases)


def _find_attestation(run: MergeRun, phases: Phases) -> int | Response:
    marker = attestation_marker(run)
    try:
        reviews = ops.list_reviews(run.oc, run.auth, run.number)
    except GitHubUnavailable:
        return _unknown(run, "reconcile_unavailable", phases)
    except GitHubRefused as exc:
        return _unknown(run, f"reconcile_refused: {ops.refusal_reason(exc)}", phases)
    mine = [
        r
        for r in reviews
        if ops.is_bot(r, run.auth.bot) and str(r.get("body") or "").endswith(marker)
    ]
    if not mine:
        return _unknown(run, "attestation_not_confirmed", phases)
    if len(mine) > 1:
        return _unknown(run, "duplicate_attestations", phases)
    ident = mine[0].get("id")
    if (
        mine[0].get("commit_id") != run.head_sha
        or mine[0].get("state") != "APPROVED"
        or not isinstance(ident, int)
        or not github._is_int(ident)
    ):
        return _unknown(run, "attestation_mismatch", phases)
    return ident


# --- ownership, the final read, the one PUT -----------------------------------


def _arm(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> Response:
    """Own the PR, read it last, `armed`, then the one `PUT`; a definite
    answer is `put_answer`. Every refusal before the `PUT` is verified."""
    try:
        token = _prepare(run, intent, phases)
    except Refused as exc:
        raise _verified(run, exc, phases) from None
    except GitHubRefused as exc:
        raise _verified(run, ops.refusal_reason(exc), phases) from None
    status = _put(run, token)
    if status is not None:
        _write(run, phases, "put_answer", {"status": status})
    return _settle(run, _facts_of(intent), phases)


def _prepare(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> str:
    """The merge token, once the PR is this request's and its final read
    wrote `armed`."""
    layout = run.oc.layout
    if not store.own_pr(layout, run.repository_id, run.number, run.res.key):
        raise Refused("pr_busy")
    token = ops.authorize(run.oc, MERGE_PERMISSIONS).value
    _final_read(run, _facts_of(intent), phases)
    _admit(run)
    read_at = datetime.now(UTC).isoformat(timespec="seconds")
    _write(run, phases, "armed", {"read_at": read_at})
    return token


def _final_read(run: MergeRun, facts: Mapping[str, object], phases: Phases) -> None:
    """The PR is open with `head_sha` and the checked facts (r2 N4); a moved
    base tip is checked again (`rechecked.<n>`), the last check decides. A
    candidate or finalize without a verdict of this call waiting for the
    admission (a repeat) is checked again whatever the tip (spec 6.1: a
    repeat passes the precheck anew, with a new observation)."""
    expected = _decisive(phases).get("base_tip")
    for n in itertools.count(len(store.rechecks(phases)) + 1):
        pr = _get_pr(run)
        _same_pr(run, pr, facts)
        tip = base_tip(run.oc, run.read, str(facts["base_ref"]))
        if tip == expected and not _check_again(run):
            return
        if n > MAX_RECHECKS:
            raise Refused("base_unstable")
        changes = pr_changes(run.oc, run.read, tip, run.head_sha)
        head_ref = str(facts["head_ref"])
        _same_kind(run, route(head_ref, changes, run.patterns))
        now = PrFacts(run.repository_id, head_ref, str(facts["base_ref"]), tip, changes)
        checked = _precheck(run.oc, pr, now, run.kind, run.trust)
        payload = {"precheck": checked, "changes_digest": changes_digest(changes)}
        _write(run, phases, f"rechecked.{n}", payload)
        expected = tip


def _check_again(run: MergeRun) -> bool:
    return run.trust is not None and run.trust.pending is None


def _get_pr(run: MergeRun) -> dict[str, object]:
    """The PR as it is now, open or not."""
    data = _get(
        run.oc, run.read, f"/repos/{run.oc.product.full_name}/pulls/{run.number}"
    )
    if not isinstance(data, dict):
        raise GitHubUnavailable("pr: not an object")
    return data


def _same_pr(
    run: MergeRun, pr: Mapping[str, object], facts: Mapping[str, object]
) -> None:
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise GitHubUnavailable("pr: no base or head")
    repo = base.get("repo")
    if not isinstance(repo, dict) or repo.get("id") != run.repository_id:
        raise Refused("pr_state")
    if pr.get("state") != "open":
        raise Refused("pr_state")
    if _base_ref(pr) != facts["base_ref"]:
        raise Refused("base_changed")
    head_repo = head.get("repo")
    head_repo_id = head_repo.get("id") if isinstance(head_repo, dict) else None
    if (
        head.get("sha") != run.head_sha
        or head_repo_id != facts["head_repo_id"]
        or _head_ref(pr) != facts["head_ref"]
    ):
        raise Refused("head_changed")


def _put(run: MergeRun, token: str) -> int | None:
    """The one `PUT`: its status if it settles the attempt, else `None` (a
    timeout, a transport error, a 5xx, any other answer)."""
    path = f"/repos/{run.oc.product.full_name}/pulls/{run.number}/merge"
    body = {"merge_method": MERGE_METHOD, "sha": run.head_sha}
    try:
        status, _ = github.call(
            "PUT",
            path,
            token,
            body,
            transport=run.oc.transport,
            api_base=run.oc.api_base,
        )
    except GitHubRefused as exc:
        status = exc.status
    except GitHubUnavailable:
        return None
    return status if status in DEFINITE_PUT else None


# --- the outcome --------------------------------------------------------------


def _settle(run: MergeRun, facts: Mapping[str, object], phases: Phases) -> Response:
    """After `armed`: the outcome by `put_answer` and the PR as it is. A PR
    merged by the App's bot at `head_sha` into the checked base is `done`;
    open after 405/422 is a verified `not_mergeable`, after 401/403/404 a
    verified `put_refused: <status>`; 409 a verified `head_changed`;
    anything else `unknown: merge_not_confirmed` (no timer, no second
    `PUT`)."""
    answer = phases.get("put_answer")
    status = None if answer is None else answer.get("status")
    if answer is not None and status not in DEFINITE_PUT:
        raise OSError("merge: put_answer is not a settling status")
    if status == 409:
        raise _verified(run, "head_changed", phases)
    try:
        pr = _get_pr(run)
    except (GitHubUnavailable, GitHubRefused):
        return _unknown(run, "merge_not_confirmed", phases)
    merged = _merged_by_app(run, pr, facts)
    if merged is not None:
        return _done(run, phases, merged, reconciled=status != 200)
    if pr.get("state") == "open" and status in _NOT_MERGEABLE:
        raise _verified(run, "not_mergeable", phases)
    if pr.get("state") == "open" and status in _PUT_REFUSED:
        raise _verified(run, f"put_refused: {status}", phases)
    return _unknown(run, "merge_not_confirmed", phases)


def _merged_by_app(
    run: MergeRun, pr: Mapping[str, object], facts: Mapping[str, object]
) -> str | None:
    """The merge commit if the PR is merged by the App's bot with `head_sha`
    into the product's checked base branch (a retarget between the final
    read and the `PUT` is "merged otherwise")."""
    head, base = pr.get("head"), pr.get("base")
    repo = base.get("repo") if isinstance(base, dict) else None
    sha = pr.get("merge_commit_sha")
    ok = (
        pr.get("merged") is True
        and ops.is_bot({"user": pr.get("merged_by")}, run.auth.bot)
        and isinstance(head, dict)
        and head.get("sha") == run.head_sha
        and isinstance(repo, dict)
        and repo.get("id") == run.repository_id
        and isinstance(base, dict)
        and base.get("ref") == facts["base_ref"]
        and isinstance(sha, str)
        and _SHA.fullmatch(sha) is not None
    )
    return sha if ok and isinstance(sha, str) else None


def _decisive(phases: Phases) -> Mapping[str, object]:
    """The metadata of the check that decides: the last `rechecked.<n>`, or
    the first check's (`attest`, or `checked` of a candidate)."""
    names = store.rechecks(phases)
    source = phases[names[-1]] if names else _initial(phases)
    checked = None if source is None else source.get("precheck")
    if not isinstance(checked, dict):
        raise OSError("merge: no precheck metadata in the phases")
    return checked


def _initial(phases: Phases) -> Mapping[str, object] | None:
    return phases.get("attest") or phases.get("checked")


def _evidence(run: MergeRun, phases: Phases) -> dict[str, object]:
    return _evidence_of(run.base, phases)


def _evidence_of(base: Mapping[str, object], phases: Phases) -> dict[str, object]:
    """Spec 5.5 for `merge`, from the intent (`base`: the kind and the root)
    and the phases (never computed anew)."""
    evidence: dict[str, object] = dict(base)
    initial = _initial(phases)
    if initial is not None:
        decisive = dict(_decisive(phases))
        evidence["precheck"] = decisive
        root = evidence.get("trust_root")
        x = decisive.get("snapshot_sha")
        if isinstance(root, dict) and "snapshot_sha" in root and x is not None:
            # review M3: X of the check that decides (a repeat may check anew)
            evidence["trust_root"] = {**root, "snapshot_sha": x}
        if store.rechecks(phases):
            evidence["precheck_initial"] = initial.get("precheck")
    if "attested" in phases:
        evidence["attestation_review_id"] = phases["attested"].get("review_id")
    return evidence


def _unknown(run: MergeRun, reason: str, phases: Phases) -> Response:
    return Response("unknown", reason, evidence=_evidence(run, phases))


def _done(run: MergeRun, phases: Phases, commit: str, *, reconciled: bool) -> Response:
    evidence = {**_evidence(run, phases), "merge_commit": commit}
    if reconciled:
        evidence["premise"] = PREMISE
    result = {
        "merge_commit": commit,
        "head_sha": run.head_sha,
        "attestation_review_id": evidence.get("attestation_review_id"),
    }
    return Response("done", None, result, evidence)


# --- the handler --------------------------------------------------------------


def merge(ctx: Context) -> Response:
    """`merge`: route the PR; merge a stop PR (`_decide`). A refusal before
    the intent is plain; a verified one past it is raised on, so that the
    frame keeps it final (R23)."""
    try:
        return _decide(ctx)
    except ops.Answer as answer:
        resp = answer.response
        evidence = {**_state_evidence(ctx), **resp.evidence}
        return Response(resp.outcome, resp.reason, resp.result, evidence)
    except GitHubUnavailable:
        return Response("unknown", "github_unavailable", evidence=_state_evidence(ctx))
    except GitHubRefused as exc:
        reason = ops.refusal_reason(exc)
        return Response("refused", reason, evidence=_state_evidence(ctx))
    except Refused as exc:
        if exc.verified_not_done:
            raise
        evidence = {**_state_evidence(ctx), **exc.evidence}
        return Response("refused", exc.reason, evidence=evidence)


def _state_evidence(ctx: Context) -> dict[str, object]:
    """The evidence of the phases past the intent (spec 5.5), for an answer
    that did not come with it; nothing before the intent or if the phases
    cannot be read (the frame then judges by what is on disk)."""
    if not ctx.intent_written:
        return {}
    try:
        intent = store.read_intent(ctx.reservation) or {}
        return _evidence_of(_base_of(intent), store.read_phases(ctx.reservation))
    except (OSError, ValueError):
        return {}


def merge_op(ctx: Context) -> Response:
    """The `OPERATION_TABLE` entry of `merge`."""
    return merge(ctx)


def release_owner(layout: Layout, req: Request, key: str) -> None:
    """The frame's `AFTER_FINAL` of `merge`: release the PR if this request
    holds it (idempotent)."""
    repository_id = int(str(req.fields["repository_id"]))
    store.release_pr(layout, repository_id, int(str(req.fields["pr"])), key)


__all__ = [
    "ATTESTATION_MARKER",
    "ENVELOPE_PATH",
    "MERGE_METHOD",
    "MERGE_PERMISSIONS",
    "STOP_PATH",
    "Change",
    "Kind",
    "MergeRun",
    "branch_kinds",
    "changes_digest",
    "merge",
    "merge_op",
    "pr_changes",
    "recover_merge",
    "release_owner",
    "route",
]
