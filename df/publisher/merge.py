"""`merge` (spec 5.4; stage 2b, plan decision 8): the kind of a PR is routed by
facts, and a stop PR is merged. Candidate and finalize are refused until stage
3, a brief until part C. A stop PR the precheck (`df-precheck`) allows gets
the App's attestation, then the one `PUT .../merge` with the server-side
`sha`; every step past the intent is a durable phase, the PR is owned by the
request from its final read to its final result, and a repeat reconciles
from the phases (`recover_merge`) and never sends a second `PUT`.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from df import launch
from df.config import ConfigError, read_optional
from df.paths import Layout
from df.publisher import github, ops, precheck, store
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.ops import OpContext
from df.publisher.precheck import PrFacts
from df.publisher.request import Refused, Request, Response
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
#: No `df-trust` binding before stage 3 (spec 5.5, rev 10).
TRUST_ROOT: Mapping[str, str] = {"unavailable": "no_binding"}
#: HTTP answers of the `PUT` that settle the one attempt (decision 8).
DEFINITE_PUT = frozenset({200, 405, 409, 422})
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


# --- the merge of stop (stage 2b, plan decision 8) ----------------------------


@dataclass(frozen=True)
class MergeRun:
    """What one `merge` request works with past the precheck: the product's
    context, the bot and its review token, a read token, the request, its
    reservation (the phases are its files) and the release's patterns."""

    oc: OpContext
    auth: ops.Auth
    read: str = field(repr=False)
    req: Request
    res: store.Reservation
    patterns: Mapping[str, str]

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
        run = MergeRun(oc, ops.review_auth(oc), read, req, res, patterns)
        return recover_merge(run, ctx.intent, store.read_phases(res))
    if store.read_phases(res):  # the intent is gone, the phases are not
        raise OSError("merge: phases without an intent")
    number, head_sha = int(str(req.fields["pr"])), str(req.fields["head_sha"])
    pr, facts = _read_facts(oc, read, number, head_sha)
    _check_kind(route(facts.head_ref, facts.changes, patterns))
    if store.pr_busy(ctx.layout, oc.product.repository_id, number, res.key):
        raise Refused("pr_busy", evidence=_KIND)
    checked = _precheck(oc, pr, facts)
    run = MergeRun(oc, ops.review_auth(oc), read, req, res, patterns)
    intent = {
        "request_key": res.key,
        "head_sha": head_sha,
        "kind": "stop",
        "facts": _facts_record(facts),
    }
    ctx.record_intent(intent)
    return _attest(run, intent, {}, checked)


_KIND: Mapping[str, object] = {"kind": "stop", "trust_root": TRUST_ROOT}


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


def _check_kind(kind: Kind | Refused) -> None:
    """Only stop goes on in 2b (plan decision 2)."""
    if isinstance(kind, Refused):
        raise kind
    if kind == "brief":
        raise Refused("brief_precheck_unavailable", evidence={"kind": kind})
    if kind != "stop":
        reason = f"kind_unavailable_until_stage3: {kind}"
        raise Refused(reason, evidence={"kind": kind})


def _precheck(
    oc: OpContext, pr: Mapping[str, object], facts: PrFacts
) -> dict[str, object]:
    """Ask `df-precheck`; its metadata (with the base tip it judged) or the
    refusal. The publisher's own checks ran before and do not depend on it."""
    result = precheck.admin_precheck("stop", oc, pr, facts)
    checked = {**result.evidence(), "base_tip": facts.base_tip}
    if result.refused is not None:
        raise Refused(result.refused.reason, evidence={**_KIND, "precheck": checked})
    return checked


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
        return _settle(run, found)
    if "put_answer" in found:
        raise OSError("merge: put_answer without armed")
    if "attested" in found:
        return _arm(run, intent, found)
    if "attest" in found:
        return _confirmed(run, intent, found)
    return _resume(run, intent, found)


def _check_intent(run: MergeRun, intent: Mapping[str, object]) -> None:
    facts = intent.get("facts")
    ok = (
        intent.get("request_key") == run.res.key
        and intent.get("head_sha") == run.head_sha
        and intent.get("kind") == "stop"
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


def _verified(exc: Refused | str, phases: Phases) -> Refused:
    """A refusal past the intent: nothing was merged by this request (its one
    `PUT` was not sent, or was answered no), so it is final."""
    reason = exc if isinstance(exc, str) else exc.reason
    extra = {} if isinstance(exc, str) else exc.evidence
    return Refused(
        reason, verified_not_done=True, evidence={**_evidence(phases), **extra}
    )


def _resume(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> Response:
    """Only the intent: the PR must still be what it was; then the precheck
    again and the attestation."""
    try:
        pr, facts = _read_facts(run.oc, run.read, run.number, run.head_sha)
        if _facts_record(facts) != _facts_of(intent):
            raise Refused("pr_facts_changed")
        _check_kind(route(facts.head_ref, facts.changes, run.patterns))
        if store.pr_busy(run.oc.layout, run.repository_id, run.number, run.res.key):
            raise Refused("pr_busy")
        checked = _precheck(run.oc, pr, facts)
    except Refused as exc:
        raise _verified(exc, phases) from None
    except GitHubRefused as exc:
        raise _verified(ops.refusal_reason(exc), phases) from None
    return _attest(run, intent, phases, checked)


# --- the attestation ----------------------------------------------------------


def attestation_marker(run: MergeRun) -> str:
    """The whole body of this request's attestation."""
    return ATTESTATION_MARKER.format(kind="stop", key=run.res.key, sha=run.head_sha)


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
        raise _verified(ops.refusal_reason(exc), phases) from None
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
        return _unknown("reconcile_unavailable", phases)
    except GitHubRefused as exc:
        return _unknown(f"reconcile_refused: {ops.refusal_reason(exc)}", phases)
    mine = [
        r
        for r in reviews
        if ops.is_bot(r, run.auth.bot) and str(r.get("body") or "").endswith(marker)
    ]
    if not mine:
        return _unknown("attestation_not_confirmed", phases)
    if len(mine) > 1:
        return _unknown("duplicate_attestations", phases)
    ident = mine[0].get("id")
    if (
        mine[0].get("commit_id") != run.head_sha
        or mine[0].get("state") != "APPROVED"
        or not isinstance(ident, int)
        or not github._is_int(ident)
    ):
        return _unknown("attestation_mismatch", phases)
    return ident


# --- ownership, the final read, the one PUT -----------------------------------


def _arm(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> Response:
    """Own the PR, read it last, `armed`, then the one `PUT`; a definite
    answer is `put_answer`. Every refusal before the `PUT` is verified."""
    try:
        token = _prepare(run, intent, phases)
    except Refused as exc:
        raise _verified(exc, phases) from None
    except GitHubRefused as exc:
        raise _verified(ops.refusal_reason(exc), phases) from None
    status = _put(run, token)
    if status is not None:
        _write(run, phases, "put_answer", {"status": status})
    return _settle(run, phases)


def _prepare(run: MergeRun, intent: Mapping[str, object], phases: Phases) -> str:
    """The merge token, once the PR is this request's and its final read
    wrote `armed`."""
    layout = run.oc.layout
    if not store.own_pr(layout, run.repository_id, run.number, run.res.key):
        raise Refused("pr_busy")
    token = ops.authorize(run.oc, MERGE_PERMISSIONS).value
    _final_read(run, _facts_of(intent), phases)
    read_at = datetime.now(UTC).isoformat(timespec="seconds")
    _write(run, phases, "armed", {"read_at": read_at})
    return token


def _final_read(run: MergeRun, facts: Mapping[str, object], phases: Phases) -> None:
    """The PR is open with `head_sha` and the checked facts (r2 N4); a moved
    base tip is checked again (`rechecked.<n>`), the last check decides."""
    expected = _decisive(phases).get("base_tip")
    for n in itertools.count(len(store.rechecks(phases)) + 1):
        pr = _get_pr(run)
        _same_pr(run, pr, facts)
        tip = base_tip(run.oc, run.read, str(facts["base_ref"]))
        if tip == expected:
            return
        if n > MAX_RECHECKS:
            raise Refused("base_unstable")
        changes = pr_changes(run.oc, run.read, tip, run.head_sha)
        head_ref = str(facts["head_ref"])
        _check_kind(route(head_ref, changes, run.patterns))
        now = PrFacts(run.repository_id, head_ref, str(facts["base_ref"]), tip, changes)
        checked = _precheck(run.oc, pr, now)
        payload = {"precheck": checked, "changes_digest": changes_digest(changes)}
        _write(run, phases, f"rechecked.{n}", payload)
        expected = tip


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


def _settle(run: MergeRun, phases: Phases) -> Response:
    """After `armed`: the outcome by `put_answer` and the PR as it is. A PR
    merged by the App's bot at `head_sha` is `done`; open after 405/422 is a
    verified `not_mergeable`; 409 a verified `head_changed`; anything else
    `unknown: merge_not_confirmed` (no timer, no second `PUT`)."""
    answer = phases.get("put_answer")
    status = None if answer is None else answer.get("status")
    if answer is not None and status not in DEFINITE_PUT:
        raise OSError("merge: put_answer is not a settling status")
    if status == 409:
        raise _verified("head_changed", phases)
    try:
        pr = _get_pr(run)
    except (GitHubUnavailable, GitHubRefused):
        return _unknown("merge_not_confirmed", phases)
    merged = _merged_by_app(run, pr)
    if merged is not None:
        return _done(run, phases, merged, reconciled=status != 200)
    if status in (405, 422) and pr.get("state") == "open":
        raise _verified("not_mergeable", phases)
    return _unknown("merge_not_confirmed", phases)


def _merged_by_app(run: MergeRun, pr: Mapping[str, object]) -> str | None:
    """The merge commit if the PR is merged by the App's bot with `head_sha`
    into the product."""
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
        and isinstance(sha, str)
        and _SHA.fullmatch(sha) is not None
    )
    return sha if ok and isinstance(sha, str) else None


def _decisive(phases: Phases) -> Mapping[str, object]:
    """The metadata of the check that decides: the last `rechecked.<n>`, or
    `attest`'s."""
    names = store.rechecks(phases)
    source = phases[names[-1]] if names else phases.get("attest")
    checked = None if source is None else source.get("precheck")
    if not isinstance(checked, dict):
        raise OSError("merge: no precheck metadata in the phases")
    return checked


def _evidence(phases: Phases) -> dict[str, object]:
    """Spec 5.5 for `merge`, from the phases (never computed anew)."""
    evidence: dict[str, object] = dict(_KIND)
    if "attest" in phases:
        evidence["precheck"] = dict(_decisive(phases))
        if store.rechecks(phases):
            evidence["precheck_initial"] = phases["attest"].get("precheck")
    if "attested" in phases:
        evidence["attestation_review_id"] = phases["attested"].get("review_id")
    return evidence


def _unknown(reason: str, phases: Phases) -> Response:
    return Response("unknown", reason, evidence=_evidence(phases))


def _done(run: MergeRun, phases: Phases, commit: str, *, reconciled: bool) -> Response:
    evidence = {**_evidence(phases), "merge_commit": commit}
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
        return answer.response
    except GitHubUnavailable:
        return Response("unknown", "github_unavailable")
    except GitHubRefused as exc:
        return Response("refused", ops.refusal_reason(exc))
    except Refused as exc:
        if exc.verified_not_done:
            raise
        return Response("refused", exc.reason, evidence=exc.evidence)


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
