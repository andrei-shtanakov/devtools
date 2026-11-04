"""Precheck program of the df publisher (stage 2b plan decisions 1, 2, 6, 11).

The publisher runs it as the OS role `df-precheck` (no App key, no publisher state)
through a fixed command. It reads one strict `df/precheck-request/v1` JSON object
(at most `MAX_REQUEST` bytes) from stdin and writes exactly one
`df/precheck-verdict/v1` object, bound to that request (`nonce`, `request_digest`,
`repository_id`, `pr`, `head_sha`, `kind`), plus a newline to stdout. A request that
does not parse or violates the schema gets exit code 2 and no verdict; the reason
goes to stderr without the token.

Requests are dispatched by `schema` (stage 3a, spec 5.4 rev 11): v1 as below;
`df/precheck-request/v2` gets a `df/precheck-verdict/v2` refusal -- `tree_incomplete`
when the admin tree lacks files of its list (`complete_v2` false: `precheck serve`
checked it before this module was imported), else `v2_unavailable` until the v2
program lands. The module's own imports stay within the 2b closure, so a tree an
older installer built still serves v1; the v2 program is imported only after the
check.

In 2b only kind `stop` is served (decision 2): the PR adds exactly one stop record
(decision 11), its branch is the stop branch of that record (decision 6), the record
at the PR head matches its path, and the stopped generation is the current delivered
request of the node in the base history up to `base_tip`. Otherwise `refuse` with a
reason; a fact that could not be read is `unset`.

History is read from a temporary snapshot `home/precheck-snapshots/<nonce>`, removed
in `finally`. Every run holds `home/precheck.lock` shared; a run that gets it
exclusively first removes snapshots left by interrupted runs. `DEADLINE` seconds
after start the git children are killed and the verdict is `unset: deadline`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TextIO

from governance.act_envelope import (
    DeliveredStop,
    EnvelopeError,
    node_history,
    parse_stop,
    stop_path,
)
from governance.act_forge_http import TokenTransport
from governance.act_history import load_history
from governance.act_history_snapshot import (
    _FULL_NAME,
    DeadlineExceeded,
    Scope,
    SnapshotHistory,
    open_snapshot,
)
from governance.act_time import OPS_FAILURES, Unavailable
from governance.approval_branches import parse_stop_branch

REQUEST_SCHEMA = "df/precheck-request/v1"
VERDICT_SCHEMA = "df/precheck-verdict/v1"
PRECHECK_VERSION = "df-precheck/1"
REQUEST_SCHEMA_V2 = "df/precheck-request/v2"
VERDICT_SCHEMA_V2 = "df/precheck-verdict/v2"
PRECHECK_VERSION_V2 = "df-precheck/2"
TREE_INCOMPLETE = "tree_incomplete"
#: The v2 refusals of the minimal dispatcher (stage 3a Task 1; Task 7 serves v2).
REASON_TREE_INCOMPLETE = (
    "tree_incomplete: the admin tree was built by an installer older than its "
    "df/precheck_tree.toml; reinstall the release"
)
REASON_V2_UNAVAILABLE = "v2_unavailable: protocol v2 is not served yet"
#: Kinds served by protocol v1; stage 3 adds kinds in v2 schemas, not here.
KINDS = ("stop",)
MAX_REQUEST = 64 << 10
#: Seconds from the start of `serve` until git children are killed (rung 1).
DEADLINE = 150.0
EXIT_REQUEST = 2
ALLOW, REFUSE, UNSET = "allow", "refuse", "unset"
SNAPSHOTS = "precheck-snapshots"
LOCK = "precheck.lock"
#: Reason characters kept in the verdict; the rest is PR-controlled bulk.
MAX_REASON = 2048
TRUNCATED = "...[truncated]"
#: Seconds between attempts to take the lock shared.
LOCK_POLL = 0.05

_SHA = re.compile(r"[0-9a-f]{40}")
_NONCE = re.compile(r"[0-9a-f]{32}")
#: Branch name git accepts and `open_snapshot` fetches as `refs/heads/<name>`: its
#: characters only, no leading `-`/`.`/`/`, no `..`, `//`, `/.`, `.lock` component
#: end, no trailing `/` or `.`.
_BRANCH = re.compile(
    r"(?![-./])(?!.*(?:\.\.|//|/\.|\.lock/|\.lock$))[A-Za-z0-9_./-]*[A-Za-z0-9_-]"
)
_STOP_PATH = re.compile(
    r"workstreams/(?P<ws>[^/]+)/approvals/stops/(?P<node>[^/]+)-g(?P<gen>[0-9]{1,9})\.yaml"
)

Change = tuple[str, str, str | None]


class InvalidRequest(ValueError):
    """The request does not parse or violates the schema (exit 2, no verdict)."""


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _matches(pattern: re.Pattern[str]) -> Callable[[object], bool]:
    return lambda v: isinstance(v, str) and pattern.fullmatch(v) is not None


def _text(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _http_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def _change(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and isinstance(value[0], str)
        and isinstance(value[1], str)
        and (value[2] is None or isinstance(value[2], str))
    )


def _changes(value: object) -> bool:
    return isinstance(value, list) and all(_change(c) for c in value)


_CHECKS: dict[str, Callable[[object], bool]] = {
    "schema": lambda v: v == REQUEST_SCHEMA,
    "nonce": _matches(_NONCE),
    "kind": lambda v: v in KINDS,
    "repository_id": _positive_int,
    "full_name": _matches(_FULL_NAME),
    "pr": _positive_int,
    "head_sha": _matches(_SHA),
    "head_ref": _text,
    "base_ref": _matches(_BRANCH),
    "base_tip": _matches(_SHA),
    "changes": _changes,
    "token": _text,
    "api_base": _http_url,
    "git_base": _http_url,
}
REQUEST_KEYS = tuple(_CHECKS)


@dataclass(frozen=True)
class PrecheckRequest:
    """A request that passed the schema; `digest` — `request_digest` of it."""

    nonce: str
    kind: str
    repository_id: int
    full_name: str
    pr: int
    head_sha: str
    head_ref: str
    base_ref: str
    base_tip: str
    changes: tuple[Change, ...]
    token: str
    api_base: str
    git_base: str
    digest: str


@dataclass(frozen=True)
class RequestV2:
    """A `df/precheck-request/v2` as far as the minimal dispatcher reads it."""

    nonce: str
    digest: str


@dataclass(frozen=True)
class PrecheckDeps:
    """Seams for tests: REST transport, snapshot opener (`open_snapshot`), clock."""

    transport_for: Callable[[str, str], TokenTransport]
    snapshot_for: Callable[..., Path]
    now: Callable[[], float]


@dataclass(frozen=True)
class StopTarget:
    """The stop record a stop PR adds: workstream, node, generation, path."""

    ws_id: str
    node: str
    generation: int
    path: str


def _default_transport(token: str, api_base: str) -> TokenTransport:
    return TokenTransport(token, api_base=api_base)


DEFAULT_DEPS = PrecheckDeps(_default_transport, open_snapshot, time.monotonic)


def serve(
    stdin: BinaryIO,
    stdout: TextIO,
    *,
    admin_digest: str,
    home: Path,
    complete_v2: bool,
    deps: PrecheckDeps | None = None,
) -> int:
    """Answer one request: 0 with a verdict on stdout, 2 without one (bad request).

    `admin_digest` goes into the verdict verbatim; `home` is the role's existing
    home directory (snapshots and lock live there); `complete_v2` — the admin tree
    holds every file of its list (else v2 is refused `tree_incomplete`)."""
    deps = deps or DEFAULT_DEPS
    deadline = deps.now() + DEADLINE
    try:
        data = load_request(stdin.read(MAX_REQUEST + 1))
        is_v2 = data.get("schema") == REQUEST_SCHEMA_V2
        request = parse_request_v2(data) if is_v2 else request_v1(data)
    except InvalidRequest as exc:
        print(f"df-precheck: invalid request: {exc}", file=sys.stderr)
        return EXIT_REQUEST
    if isinstance(request, RequestV2):
        answer = verdict_v2(request, admin_digest, complete_v2=complete_v2)
    else:
        answer = _answer_v1(request, home, deps, deadline, admin_digest)
    stdout.write(json.dumps(answer) + "\n")
    stdout.flush()
    return 0


def _answer_v1(
    request: PrecheckRequest,
    home: Path,
    deps: PrecheckDeps,
    deadline: float,
    admin_digest: str,
) -> dict[str, object]:
    outcome, reason = _decide(request, home, deps, deadline)
    reason = reason.replace(request.token, "[token]")
    if len(reason) > MAX_REASON:
        reason = reason[:MAX_REASON] + TRUNCATED
    return verdict(request, outcome, reason, admin_digest)


def parse_request(raw: bytes) -> PrecheckRequest:
    """Strict `df/precheck-request/v1`; any deviation — `InvalidRequest` whose text
    names keys, never values."""
    return request_v1(load_request(raw))


def load_request(raw: bytes) -> dict[str, object]:
    """The request as a JSON object (at most `MAX_REQUEST` bytes, no duplicate
    keys, no `NaN`-like constants), any schema; else `InvalidRequest`."""
    if len(raw) > MAX_REQUEST:
        raise InvalidRequest(f"request larger than {MAX_REQUEST} bytes")
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequest(f"request is not JSON ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise InvalidRequest("request is not a JSON object")
    return data


def request_v1(data: dict[str, object]) -> PrecheckRequest:
    """Strict `df/precheck-request/v1` of a loaded request (`load_request`)."""
    if set(data) != set(REQUEST_KEYS):
        extra = sorted(set(data) - set(REQUEST_KEYS))
        missing = sorted(set(REQUEST_KEYS) - set(data))
        raise InvalidRequest(f"keys: unexpected {extra}, missing {missing}")
    bad = [k for k in REQUEST_KEYS if not _CHECKS[k](data[k])]
    if bad:
        raise InvalidRequest(f"invalid values: {bad}")
    changes = data["changes"]
    assert isinstance(changes, list)
    return PrecheckRequest(
        **{k: data[k] for k in REQUEST_KEYS if k not in ("schema", "changes")},
        changes=tuple((s, p, prev) for s, p, prev in changes),
        digest=request_digest(data),
    )


def parse_request_v2(data: dict[str, object]) -> RequestV2:
    """A loaded `df/precheck-request/v2`: until the v2 program (stage 3a Task 7)
    only `nonce` is checked, the verdict binds it and `request_digest`."""
    if not _matches(_NONCE)(data.get("nonce")):
        raise InvalidRequest("invalid values: ['nonce']")
    return RequestV2(str(data["nonce"]), request_digest(data))


def verdict_v2(
    request: RequestV2, admin_digest: str, *, complete_v2: bool
) -> dict[str, object]:
    """The `df/precheck-verdict/v2` refusal of the minimal dispatcher."""
    if complete_v2:
        # Stage 3a Task 7: the v2 program is imported here, after the check.
        outcome, reason = REFUSE, REASON_V2_UNAVAILABLE
    else:
        outcome, reason = TREE_INCOMPLETE, REASON_TREE_INCOMPLETE
    return {
        "schema": VERDICT_SCHEMA_V2,
        "nonce": request.nonce,
        "request_digest": request.digest,
        "outcome": outcome,
        "reason": reason,
        "version": PRECHECK_VERSION_V2,
        "admin_digest": admin_digest,
    }


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [k for k, _v in pairs]
    if len(set(keys)) != len(keys):
        dups = sorted({k for k in keys if keys.count(k) > 1})
        raise InvalidRequest(f"duplicate keys: {dups}")
    return dict(pairs)


def _no_constant(name: str) -> object:
    raise json.JSONDecodeError(f"constant {name}", name, 0)


def request_digest(request: dict[str, object]) -> str:
    """sha256 hex of canonical JSON (sorted keys, no spaces, UTF-8) of the request
    without `token`."""
    body = {k: v for k, v in request.items() if k != "token"}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verdict(
    request: PrecheckRequest, outcome: str, reason: str, admin_digest: str
) -> dict[str, object]:
    """`df/precheck-verdict/v1` bound to `request`."""
    return {
        "schema": VERDICT_SCHEMA,
        "nonce": request.nonce,
        "request_digest": request.digest,
        "repository_id": request.repository_id,
        "pr": request.pr,
        "head_sha": request.head_sha,
        "kind": request.kind,
        "outcome": outcome,
        "reason": reason,
        "version": PRECHECK_VERSION,
        "admin_digest": admin_digest,
    }


def _decide(
    request: PrecheckRequest, home: Path, deps: PrecheckDeps, deadline: float
) -> tuple[str, str]:
    """(outcome, reason) of the kind; read failures and the deadline — `unset`."""
    try:
        return _judge_stop(request, home, deps, deadline)
    except DeadlineExceeded:
        return UNSET, "deadline"
    except OPS_FAILURES as exc:
        return UNSET, f"read_failed: {exc}"


def _judge_stop(
    request: PrecheckRequest, home: Path, deps: PrecheckDeps, deadline: float
) -> tuple[str, str]:
    target = _stop_target(request.changes, request.head_ref)
    if isinstance(target, str):
        return REFUSE, target
    with _snapshot(request, home, deps, deadline) as ops:
        return _stop_in_history(ops, request, target)


def _stop_target(changes: tuple[Change, ...], head_ref: str) -> StopTarget | str:
    """The stop record of the PR from its changes and branch, or a refusal reason."""
    if len(changes) != 1:
        return f"stop_changes: expected one change, got {len(changes)}"
    status, path, previous = changes[0]
    if status != "added" or previous is not None:
        return f"stop_changes: {status} is not a plain addition"
    found = _STOP_PATH.fullmatch(path)
    if found is None:
        return f"stop_changes: {path!r} is not a stop record path"
    ws_id, node, generation = found["ws"], found["node"], int(found["gen"])
    if stop_path(ws_id, node, generation) != path:
        return f"stop_changes: {path!r} is not canonical"
    if parse_stop_branch(head_ref, ws_id) != (node, generation):
        return f"stop_branch_mismatch: {head_ref!r} is not the branch of {path!r}"
    return StopTarget(ws_id, node, generation, path)


def _stop_in_history(
    ops: SnapshotHistory, request: PrecheckRequest, target: StopTarget
) -> tuple[str, str]:
    """The record at the PR head matches its path and stops the current generation."""
    raw = ops.show_file_bytes("", request.head_sha, target.path)
    if raw is None:
        return UNSET, f"stop_unreadable: {target.path} at {request.head_sha[:12]}"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return REFUSE, "stop_record_invalid: not UTF-8"
    try:
        record = parse_stop(text)
    except EnvelopeError as exc:
        return REFUSE, f"stop_record_invalid: {exc}"
    if (record.node, record.generation) != (target.node, target.generation):
        return REFUSE, (
            f"stop_content_mismatch: record {record.node} g{record.generation} "
            f"at {target.path}"
        )
    scope = Scope(target.ws_id, request.full_name, "")
    history = load_history(ops, scope, request.base_tip)
    if isinstance(history, Unavailable):
        return UNSET, f"history_unavailable: {history.reason}"
    current = node_history(history, target.node).current
    if current is None:
        return REFUSE, f"no_delivered_request: node {target.node}"
    if isinstance(current, DeliveredStop):
        gen = current.stop.generation
        return REFUSE, f"already_stopped: node {target.node} g{gen}"
    delivered = current.request.nodes[target.node].generation
    if delivered != target.generation:
        return REFUSE, (
            f"stop_generation_not_current: stop g{target.generation}, "
            f"current g{delivered}"
        )
    return ALLOW, ""


@contextlib.contextmanager
def _snapshot(
    request: PrecheckRequest, home: Path, deps: PrecheckDeps, deadline: float
) -> Iterator[SnapshotHistory]:
    """`SnapshotHistory` over base and PR head in `home/precheck-snapshots/<nonce>`,
    removed on exit; the lock is held shared throughout."""
    root = home / SNAPSHOTS
    with _locked(home, root, deadline, deps.now):
        work = root / request.nonce
        work.mkdir(mode=0o700)
        try:
            refs = [f"refs/heads/{request.base_ref}", f"refs/pull/{request.pr}/head"]
            git_dir = deps.snapshot_for(
                work,
                request.full_name,
                refs,
                request.token,
                git_base=request.git_base,
                deadline=deadline,
                clock=deps.now,
            )
            transport = deps.transport_for(request.token, request.api_base)
            yield SnapshotHistory(
                git_dir,
                transport,
                request.token,
                git_base=request.git_base,
                deadline=deadline,
                clock=deps.now,
            )
        finally:
            _remove(work)


@contextlib.contextmanager
def _locked(
    home: Path, root: Path, deadline: float, clock: Callable[[], float]
) -> Iterator[None]:
    """Hold `home/precheck.lock` shared; if no run holds it, first clear `root`
    (snapshots of interrupted runs) under the exclusive lock. Waiting for the shared
    lock past `deadline` — `DeadlineExceeded`."""
    root.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(home / LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass  # a live run: its snapshot is not stale
        else:
            for stale in root.iterdir():
                _remove(stale)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if clock() >= deadline:
                    raise DeadlineExceeded("precheck lock: deadline passed") from None
                time.sleep(LOCK_POLL)
        yield
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    """Remove a snapshot directory; what could not be removed is named on stderr
    (snapshots hold no token)."""

    def report(_func: Callable[..., object], name: str, exc: BaseException) -> None:
        print(f"df-precheck: snapshot not removed: {name}: {exc}", file=sys.stderr)

    shutil.rmtree(path, onexc=report)
