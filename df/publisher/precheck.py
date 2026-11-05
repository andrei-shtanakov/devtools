"""The publisher's client of the precheck (stage 2b, Task 7; plan decisions 1, 5).

The publisher holds the App key and imports no third-party code, so the
history check runs as the OS role `df-precheck` through one fixed command,
`sudo -n -u df-precheck /opt/df/libexec/df-precheck` with no argument
(requirement 2). It gets a strict `df/precheck-request/v1` on stdin with a
read-only token of the product (requirement 1: no write token, no other
descriptor) and answers one `df/precheck-verdict/v1` line.

The publisher never signals the command (it runs as another UID): it reads at
most `LIMIT` bytes (more is drained and dropped) and stops waiting after
`PRECHECK_WAIT` seconds, the third of three rungs (the precheck's own
`DEADLINE` of 150 s, sudo's `command_timeout` of 180 s). A timeout, a non-zero
exit, oversized or malformed output, or a verdict not bound to this very
request (`nonce`, `request_digest`, `repository_id`, `pr`, `head_sha`, `kind`)
or to the publisher (`admin_digest` = its `current` record's, spec 5.4 p. 3
rev 11) is a refusal (requirement 3). The token is revoked in `finally`.

Candidate and finalize (stage 3a, Task 11) go through protocol v2, `op:
merge`, with the same command, wait and token: the request adds the
observation of the `merge` route (A = X, P pinned), the binding, the merger
and the bundle source (`trust_routes.merge_fields`); the verdict is parsed
and bound by `trust_routes.bind_merge` (X, the PR, the observation, the
root, `admin_digest`, the ranges up to P). Stop stays on v1.

The request digest and the verdict schema repeat `governance.act_precheck`
(the key holder does not import `governance`); tests hold them equal.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from df import platform
from df.paths import Layout
from df.publisher import github, ops
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.ops import OpContext
from df.publisher.request import Refused
from df.roles import PRECHECK_USER
from df.stage2_config import no_constant, no_duplicates

if TYPE_CHECKING:  # `merge` and `trust_routes` import this module
    from df.publisher.merge import Change, Kind
    from df.publisher.trust_routes import MergeTrust, Verdict

REQUEST_SCHEMA = "df/precheck-request/v1"
VERDICT_SCHEMA = "df/precheck-verdict/v1"
#: Kinds protocol v1 serves (decision 2); stage 3 brings v2 schemas.
KINDS: tuple[Kind, ...] = ("stop",)
#: Kinds protocol v2 serves (`op: merge`, stage 3a Task 11).
V2_KINDS: tuple[Kind, ...] = ("candidate", "finalize")
SUDO = "/usr/bin/sudo"
WRAPPER = "df-precheck"
PRECHECK_ENV: Mapping[str, str] = {"PATH": "/usr/bin:/bin", "LANG": "C"}
#: Bytes of a request and of a verdict.
LIMIT = 64 << 10
#: Seconds the publisher waits (rung 3); it never signals. The consumer's
#: call timeout of `perimeter-status` (`act_journal_wire`, 60 s) must cover
#: one v2 evaluation (`trust_routes.PERIMETER_STATUS_EVALUATIONS`): Task 16
#: sets this wait and that timeout together from the measurement.
PRECHECK_WAIT = 200.0
#: A failed exit this late is sudo's `command_timeout=180` (rung 2).
SUDO_TIMEOUT_AFTER = 175.0
#: The precheck's own reason cap plus its truncation marker.
MAX_REASON = 2048 + len("...[truncated]")
ALLOW, REFUSE, UNSET = "allow", "refuse", "unset"
#: The verdict fields that must equal the request's.
BOUND = ("nonce", "repository_id", "pr", "head_sha", "kind")
TOKEN_MASK = "[token]"

_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_NONCE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_ADMIN_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
#: `act_precheck.PRECHECK_VERSION` has no cap of its own: a short name.
_VERSION = re.compile(r"[A-Za-z0-9._/-]{1,64}", re.ASCII)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _matches(pattern: re.Pattern[str]) -> Callable[[object], bool]:
    return lambda v: isinstance(v, str) and pattern.fullmatch(v) is not None


_VERDICT_CHECKS: dict[str, Callable[[object], bool]] = {
    "schema": lambda v: v == VERDICT_SCHEMA,
    "nonce": _matches(_NONCE),
    "request_digest": _matches(_HEX64),
    "repository_id": _positive_int,
    "pr": _positive_int,
    "head_sha": _matches(_SHA),
    "kind": lambda v: v in KINDS,
    "outcome": lambda v: v in (ALLOW, REFUSE, UNSET),
    "reason": lambda v: isinstance(v, str) and len(v) <= MAX_REASON,
    "version": _matches(_VERSION),
    "admin_digest": _matches(_ADMIN_DIGEST),
}


@dataclass(frozen=True)
class PrFacts:
    """Facts of the PR the publisher checked itself (requirement 4)."""

    head_repo_id: int
    head_ref: str
    base_ref: str
    base_tip: str
    changes: tuple[Change, ...]


@dataclass(frozen=True)
class PrecheckResult:
    """The answer: `refused` is `None` for `allow`. `version`, `admin_digest`
    and `request_digest` are those of the bound verdict (`None` if none was
    bound); `revoke` is `"ok"` or the error text, never the token."""

    refused: Refused | None
    version: str | None
    admin_digest: str | None
    request_digest: str | None
    revoke: str
    #: The bound `op: merge` verdict (protocol v2; `None` otherwise).
    verdict: Verdict | None = None

    def evidence(self) -> dict[str, object]:
        """The metadata of this check for the evidence of `merge`."""
        return {
            "version": self.version,
            "admin_digest": self.admin_digest,
            "revoke": self.revoke,
            "request_digest": self.request_digest,
        }


def command(layout: Layout) -> tuple[str, ...]:
    """The one command the sudoers rule allows: the wrapper, no argument."""
    return (SUDO, "-n", "-u", PRECHECK_USER, str(layout.libexec / WRAPPER))


def admin_precheck(
    kind: Kind,
    oc: OpContext,
    pr: Mapping[str, object],
    facts: PrFacts,
    *,
    obs: MergeTrust | None = None,
) -> PrecheckResult:
    """Ask the precheck about the PR `pr` (GitHub's object) of `kind` with
    the publisher's own `facts`; candidate and finalize by protocol v2 with
    `obs`, the observation of the `merge` route (C = the PR's head, X =
    `obs.snapshot`), stop by v1 without it.

    A read token (`READ_PERMISSIONS`, the product only) goes into a request
    with a fresh nonce; the command runs supervised; the verdict is parsed
    strictly and checked against the request. `refuse` is `Refused(reason)`,
    `unset` `Refused("precheck_unset: ...")`, a protocol failure
    `Refused("precheck_failed: <timeout|exit|oversize|parse|binding_mismatch>")`
    (v2: `binding_mismatch: <rule>`); v2 keeps the bound verdict in
    `verdict` (the final admission completes the observation by it). The
    token is revoked in `finally`. A token that cannot be minted raises as
    `ops.authorize` does; a kind outside both protocols, or `obs` given for
    one protocol and not the other, is a `ValueError`."""
    if kind not in (*KINDS, *V2_KINDS):
        raise ValueError(f"the precheck does not serve kind {kind!r}")
    if (kind in V2_KINDS) != (obs is not None):
        raise ValueError("protocol v2 (candidate, finalize) and only it needs obs")
    number, head_sha = _pr_number(pr), _pr_head_sha(pr)
    if facts.head_repo_id != oc.product.repository_id:
        raise Refused("head_repo_foreign")
    token = ops.authorize(oc, ops.READ_PERMISSIONS).value
    bound: Verdict | None = None
    try:
        request = _request(kind, oc, number, head_sha, facts, token)
        if obs is None:
            refused, verdict = _ask(oc.layout, request, token, oc.admin_digest)
        else:
            refused, verdict, bound = _ask_v2(oc, request, token, obs)
    finally:
        revoke = _revoke(oc, token)
    if verdict is None:
        return PrecheckResult(refused, None, None, None, revoke)
    return PrecheckResult(
        refused,
        str(verdict["version"]),
        str(verdict["admin_digest"]),
        str(verdict["request_digest"]),
        revoke,
        bound,
    )


def _pr_number(pr: Mapping[str, object]) -> int:
    number = pr.get("number")
    if not _positive_int(number):
        raise GitHubUnavailable("pr: no number")
    assert isinstance(number, int)
    return number


def _pr_head_sha(pr: Mapping[str, object]) -> str:
    head = pr.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise GitHubUnavailable("pr: no head sha")
    return sha


def _request(
    kind: Kind,
    oc: OpContext,
    number: int,
    head_sha: str,
    facts: PrFacts,
    token: str,
) -> dict[str, object]:
    """`df/precheck-request/v1` (every key of `act_precheck.REQUEST_KEYS`)."""
    return {
        "schema": REQUEST_SCHEMA,
        "nonce": secrets.token_hex(16),
        "kind": kind,
        "repository_id": oc.product.repository_id,
        "full_name": oc.product.full_name,
        "pr": number,
        "head_sha": head_sha,
        "head_ref": facts.head_ref,
        "base_ref": facts.base_ref,
        "base_tip": facts.base_tip,
        "changes": [list(change) for change in facts.changes],
        "token": token,
        "api_base": oc.api_base,
        "git_base": oc.git_base,
    }


def request_digest(request: Mapping[str, object]) -> str:
    """sha256 hex of canonical JSON (sorted keys, no spaces, UTF-8) of the
    request without `token` (the rule of `act_precheck.request_digest`)."""
    body = {k: v for k, v in request.items() if k != "token"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ask(
    layout: Layout,
    request: Mapping[str, object],
    token: str,
    admin_digest: str | None,
) -> tuple[Refused | None, Mapping[str, object] | None]:
    """Run the command on `request`: the refusal (if any) and the verdict
    bound to it and to the publisher's `admin_digest` (`None` unless one was
    bound; no record of the publisher binds nothing)."""
    data = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(data) > LIMIT:
        return Refused("precheck_request_too_large"), None
    run = platform.run_supervised(
        command(layout), data, limit=LIMIT, wait=PRECHECK_WAIT, env=PRECHECK_ENV
    )
    failure = classify(run)
    if failure is not None:
        return _failed(failure), None
    verdict = parse_verdict(run.stdout)
    if verdict is None:
        return _failed("parse"), None
    if not _bound(verdict, request, admin_digest):
        return _failed("binding_mismatch"), None
    reason = _mask(str(verdict["reason"]), token)
    if verdict["outcome"] == REFUSE:
        return Refused(reason), verdict
    if verdict["outcome"] == UNSET:
        return Refused(f"precheck_unset: {reason}"), verdict
    return None, verdict


def _ask_v2(
    oc: OpContext, v1: Mapping[str, object], token: str, obs: MergeTrust
) -> tuple[Refused | None, Mapping[str, object] | None, Verdict | None]:
    """`_ask` for `op: merge`: the v1 request of the PR with the v2 keys, the
    verdict parsed and bound by `trust_routes` (`MERGE_KEYS`,
    `bind_merge`): the refusal (if any), the parsed and the bound verdict
    (`None` unless one was bound)."""
    from df.publisher import trust_routes  # it builds on this module

    request = {**v1, **trust_routes.merge_fields(obs, oc.product.dag_variant)}
    data = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(data) > LIMIT:
        return Refused("precheck_request_too_large"), None, None
    run = platform.run_supervised(
        command(oc.layout), data, limit=LIMIT, wait=PRECHECK_WAIT, env=PRECHECK_ENV
    )
    failure = classify(run)
    if failure is not None:
        return _failed(failure), None, None
    parsed = trust_routes.parse_verdict(run.stdout, trust_routes.MERGE_KEYS)
    if parsed is None:
        return _failed("parse"), None, None
    got = trust_routes.bind_merge(parsed, request, trust=obs)
    if not isinstance(got, trust_routes.Verdict):
        return Refused(got.reason), None, None
    reason = _mask(got.reason, token)
    if got.outcome == ALLOW:
        return None, parsed, got
    if got.outcome == UNSET:
        return Refused(f"precheck_unset: {reason}"), parsed, got
    return Refused(reason), parsed, got


def _failed(what: str) -> Refused:
    return Refused(f"precheck_failed: {what}")


def classify(run: platform.SupervisedResult) -> str | None:
    """Decision 5: still running at the wait, or ended by a signal, or a
    failed exit after sudo's `command_timeout` is `timeout`; more output than
    the limit is `oversize` whatever the code; another failed exit `exit`."""
    if run.status is None or run.signal is not None:
        return "timeout"
    if run.oversize:
        return "oversize"
    if run.status != 0:
        return "timeout" if run.elapsed >= SUDO_TIMEOUT_AFTER else "exit"
    return None


def parse_verdict(raw: bytes) -> Mapping[str, object] | None:
    """One `df/precheck-verdict/v1` object, exactly its keys, each of its
    form, the reason tied to the outcome (empty for `allow` only, as
    `act_precheck` writes it); anything else is `None`.

    Byte-exact: `raw` must be what `act_precheck.serve` writes for that
    object (`json.dumps` defaults, then one `\n`), so no whitespace, `\r` or
    other spelling passes."""
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or set(data) != set(_VERDICT_CHECKS):
        return None
    if not all(check(data[key]) for key, check in _VERDICT_CHECKS.items()):
        return None
    if (data["outcome"] == ALLOW) != (data["reason"] == ""):
        return None
    if raw != (json.dumps(data) + "\n").encode("utf-8"):
        return None
    return data


def _bound(
    verdict: Mapping[str, object],
    request: Mapping[str, object],
    admin_digest: str | None,
) -> bool:
    """The verdict answers this request (its bound fields and digest) from
    the publisher's admin tree (`admin_digest`)."""
    if verdict["request_digest"] != request_digest(request):
        return False
    if admin_digest is None or verdict["admin_digest"] != admin_digest:
        return False
    return all(verdict[key] == request[key] for key in BOUND)


def _revoke(oc: OpContext, token: str) -> str:
    """`DELETE /installation/token`: `"ok"` or the error, the token masked."""
    try:
        github.call(
            "DELETE",
            "/installation/token",
            token,
            transport=oc.transport,
            api_base=oc.api_base,
        )
    except GitHubRefused as exc:
        text = ops.refusal_reason(exc)
    except GitHubUnavailable as exc:
        text = f"github_unavailable: {exc}"
    else:
        return "ok"
    return _mask(text, token)


def _mask(text: str, token: str) -> str:
    """`text` with every occurrence of `token` masked (an empty `token`
    masks nothing: `str.split("")` would raise)."""
    return TOKEN_MASK.join(text.split(token)) if token else text


__all__ = [
    "KINDS",
    "LIMIT",
    "PRECHECK_WAIT",
    "V2_KINDS",
    "PrFacts",
    "PrecheckResult",
    "admin_precheck",
    "classify",
    "command",
    "parse_verdict",
    "request_digest",
]
