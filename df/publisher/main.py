"""The frame of one `df-publish <operation>` invocation (spec 5, rev 9).

In this order: the sender (the process's UID, then `SUDO_UID` against
`publisher.toml`), the request off stdin (bounded in size and time), the run
(the root's record, its release `allowed` in the snapshot's registry), the
reservation of `request_id` (`store.reserve`), the operation. The answer is
one JSON line on stdout; the exit code is 0 for `done`, 3 for `unknown` and 2
for every other outcome.

An operation is an entry of `OPERATION_TABLE`. Until Tasks 8-11 replace them,
the real ones refuse with `not_implemented` before any intent is written, so
no operation can leave an intent or an `unknown` state yet.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from df import launch
from df.config import ConfigError, load_accounts, read_optional
from df.journal import StepFailed
from df.paths import Layout
from df.publisher import github, ops, request, store
from df.publisher.request import InFlight, Refused, Request, Response
from df.stage2_config import PublisherConfig, RunRecord, parse_publisher
from df.state import Snapshot

#: Operations only root may ask for (decision 6): a constant of the code, not
#: a line of `publisher.toml`.
ADMIN_OPERATIONS = frozenset({request.SELF_CHECK})
PUBLISHER_USER = "df-publisher"
_SUDO_UID = re.compile(r"0|[1-9][0-9]*", re.ASCII)
_EXIT = {"done": 0, "unknown": 3}
_REFUSED_EXIT = 2
_RELEASE_REASONS = ("release_revoked", "release_unknown", "release_digest_mismatch")


@dataclass
class Context:
    """What an operation's handler is given.

    `record_intent` writes the intent durably before the external action;
    `intent` is an earlier attempt's, if one died after writing it (the
    handler then reconciles instead of acting)."""

    layout: Layout
    snapshot: Snapshot
    sender: int
    request: Request
    record: RunRecord | None
    reservation: store.Reservation
    intent: Mapping[str, object] | None
    #: `publisher.toml`'s `api_base`: every GitHub call goes there.
    api_base: str
    intent_written: bool = False

    def record_intent(self, payload: Mapping[str, object]) -> None:
        """Write the intent (durably) before acting outside."""
        store.intent(self.reservation, payload)
        self.intent_written = True


Handler = Callable[[Context], Response]


def _not_implemented(_ctx: Context) -> Response:
    return Response("refused", "not_implemented")


#: Tasks 10-11 replace their own entries; tests inject handlers here.
OPERATION_TABLE: dict[str, Handler] = {
    **{op: _not_implemented for op in request.ALL_OPERATIONS},
    "publish-review": ops.publish_review,
    "read-token": ops.read_token,
    request.SELF_CHECK: ops.self_check_op,
}


def sender(
    cfg: PublisherConfig,
    op: str,
    *,
    publisher_uid: int,
    uid: int | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """The UID that asked (`SUDO_UID`), or `Refused`.

    The process itself must be `df-publisher` (`not_publisher_uid`);
    `SUDO_UID` is a canonical decimal (`sender_unknown`); an administrative
    operation comes from root and only from root; any other from a UID of the
    table that may ask for it (`operation_not_allowed`)."""
    own = os.getuid() if uid is None else uid
    environ = os.environ if env is None else env
    if own != publisher_uid:
        raise Refused("not_publisher_uid")
    raw = environ.get("SUDO_UID")
    if raw is None or not _SUDO_UID.fullmatch(raw):
        raise Refused("sender_unknown")
    asked = int(raw)
    if (op in ADMIN_OPERATIONS) != (asked == 0):
        raise Refused("operation_not_allowed")
    if op in ADMIN_OPERATIONS:
        return asked
    allowed = cfg.senders.get(asked)
    if allowed is None:
        raise Refused("sender_unknown")
    if op not in allowed:
        raise Refused("operation_not_allowed")
    return asked


def check_run(layout: Layout, snap: Snapshot, run_id: str | None) -> RunRecord:
    """The run record `run_id` (spec 5.8), or `Refused`.

    The record is root's; its release must be `allowed` in the snapshot's
    registry (a revoked one: `release_revoked: <reason>`) with the record's
    digest. `releases.toml` is not read again; a client's release id is no
    evidence. Unknown run: `run_unknown`."""
    if run_id is None:
        raise Refused("run_unknown")
    try:
        return launch.release_of_record(layout, snap, run_id)
    except StepFailed as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise Refused("run_unknown") from exc
        if exc.detail.startswith(_RELEASE_REASONS):
            raise Refused(exc.detail) from exc
        print(f"publisher: {exc}", file=sys.stderr)
        raise Refused("run_record_invalid") from exc


def main(
    argv: Sequence[str],
    *,
    layout: Layout,
    snapshot: Snapshot,
    uid: int | None = None,
    gid: int | None = None,
    env: Mapping[str, str] | None = None,
    stdin_fd: int = 0,
) -> int:
    """`publisher serve <operation>`; `argv = [operation]`.

    A different argument list is refused (2) before stdin is touched. Every
    other outcome is one JSON line on stdout. A failure after the intent was
    written is `unknown`, never `refused`: the external action may have
    happened."""
    if len(argv) != 1 or argv[0] not in OPERATION_TABLE:
        print(
            f"publisher serve: нужна одна операция, получено {list(argv)}",
            file=sys.stderr,
        )
        return _REFUSED_EXIT
    op = argv[0]
    seen = _Seen()
    try:
        cfg = _load_config(layout)
        seen.api_base = cfg.api_base
        who = _authenticate(layout, cfg, op, uid, gid, env)
    except Refused as exc:
        _journal_sender_refusal(layout, op, env, exc.reason)
        evidence = {"operation": op, **_api_evidence(seen)}
        return _answer(Response("refused", exc.reason, evidence=evidence))
    try:
        resp = _handle(layout, snapshot, who, op, stdin_fd, seen)
    except Refused as exc:
        resp = Response("refused", exc.reason)
    except InFlight:
        resp = Response("unknown", "in_flight")
    except Exception as exc:  # noqa: BLE001 - R23 judges it by what is on disk
        print(f"publisher: {type(exc).__name__}: {exc}", file=sys.stderr)
        resp = Response("refused", "internal_error")
    resp = _never_refuse_with_state(layout, seen, resp)
    resp = _decorate(resp, who, op, seen, snapshot)
    _journal(layout, who, op, seen, resp)
    return _answer(resp)


class _Seen:
    """What the invocation got to know before it ended (for the journal)."""

    request: Request | None = None
    digest: str | None = None
    key: str | None = None
    #: `publisher.toml`'s `api_base`, once the file was read.
    api_base: str | None = None
    #: A handler proved the action was not done (or a kept result is replayed).
    verified: bool = False


#: Refusals of a sender the process could identify as called by `sudo`; others
#: (not the publisher, broken configuration) leave no file behind.
_JOURNALLED_SENDER = ("sender_unknown", "operation_not_allowed")


def _journal_sender_refusal(
    layout: Layout, op: str, env: Mapping[str, str] | None, reason: str
) -> None:
    if reason not in _JOURNALLED_SENDER:
        return
    environ = os.environ if env is None else env
    line = store.refusal_line(op, environ.get("SUDO_UID"), reason)
    try:
        store.journal(layout, line)
    except OSError as exc:
        print(f"publisher: journal not written: {exc}", file=sys.stderr)


def _load_config(layout: Layout) -> PublisherConfig:
    """`publisher.toml`: absent is `publisher_config_missing` (roles setup has
    not run), unreadable or malformed `publisher_config_invalid`."""
    try:
        text = read_optional(layout.etc / "publisher.toml")
        if text is None:
            raise Refused("publisher_config_missing")
        return parse_publisher(text)
    except ConfigError as exc:
        print(f"publisher: {exc}", file=sys.stderr)
        raise Refused("publisher_config_invalid") from exc


def _authenticate(
    layout: Layout,
    cfg: PublisherConfig,
    op: str,
    uid: int | None,
    gid: int | None,
    env: Mapping[str, str] | None,
) -> int:
    try:
        account = load_accounts(layout.accounts).get(PUBLISHER_USER)
    except ConfigError as exc:
        print(f"publisher: {exc}", file=sys.stderr)
        raise Refused("publisher_config_invalid") from exc
    if account is None or account.state != "ready":
        raise Refused("not_publisher_uid")
    if not _own_gid_is(account.gid, gid):
        raise Refused("not_publisher_uid")
    return sender(cfg, op, publisher_uid=account.uid, uid=_own_uid(uid), env=env)


def _own_gid_is(want: int, gid: int | None) -> bool:
    """Real and effective GID are the account's (as `launch._check_identity`)."""
    if gid is not None:
        return gid == want
    return os.getgid() == os.getegid() == want


def _own_uid(uid: int | None) -> int:
    """The process's UID; -1 (nobody's) when real and effective differ."""
    if uid is not None:
        return uid
    return os.getuid() if os.getuid() == os.geteuid() else -1


def _never_refuse_with_state(layout: Layout, seen: _Seen, resp: Response) -> Response:
    """R23, the one place: a request with an intent or a result on disk is
    never answered `refused` (the action may have been done, and a client takes
    `refused` as "not done") unless a handler verified that it was not."""
    if resp.outcome != "refused" or seen.verified or seen.key is None:
        return resp
    if not store.has_state(layout, seen.key):
        return resp
    reason = f"state_exists: {resp.reason}"
    return Response("unknown", reason, resp.result, resp.evidence)


def _handle(
    layout: Layout,
    snapshot: Snapshot,
    who: int,
    op: str,
    stdin_fd: int,
    seen: _Seen,
) -> Response:
    raw = request.read_frame(
        stdin_fd, limit=request.MAX_BYTES, timeout=request.READ_TIMEOUT
    )
    req = request.parse(op, raw)
    seen.request, seen.digest = req, request.digest(req)
    seen.key = store.idempotency_key(who, op, seen.digest)
    record = None if op in ADMIN_OPERATIONS else check_run(layout, snapshot, req.run_id)
    with store.reserve(layout, who, op, req) as res:
        return _under_key(layout, snapshot, who, op, req, record, res, seen)


def _under_key(
    layout: Layout,
    snapshot: Snapshot,
    who: int,
    op: str,
    req: Request,
    record: RunRecord | None,
    res: store.Reservation,
    seen: _Seen,
) -> Response:
    kept = store.replay(res)
    if kept is not None:
        seen.verified = True  # a kept result was final when it was kept
        evidence = {**kept.evidence, "replayed": True}
        return Response(kept.outcome, kept.reason, kept.result, evidence)
    replayed = op not in store.NOT_REPLAYED
    earlier = store.read_intent(res) if replayed else None
    base = seen.api_base if seen.api_base is not None else github.API
    ctx = Context(
        layout, snapshot, who, req, record, res, earlier, base, earlier is not None
    )
    resp = _run_handler(ctx, op, seen)
    resp = _decorate(resp, who, op, seen, snapshot)
    final = resp.outcome == "done" or (resp.outcome == "refused" and seen.verified)
    return _keep(res, resp) if replayed and final else resp


def _run_handler(ctx: Context, op: str, seen: _Seen) -> Response:
    try:
        return OPERATION_TABLE[op](ctx)
    except Refused as exc:
        seen.verified = exc.verified_not_done
        return Response("refused", exc.reason)
    except Exception as exc:  # noqa: BLE001 - R23 judges it by what is on disk
        print(f"publisher: {type(exc).__name__}: {exc}", file=sys.stderr)
        return Response("refused", "internal_error")


def _keep(res: store.Reservation, resp: Response) -> Response:
    """Save a final outcome; if that fails the action may still have been
    done, so the answer is `unknown` (a repeat reconciles from the intent)."""
    try:
        store.result(res, resp)
    except Exception as exc:  # noqa: BLE001 - the action may be done
        print(f"publisher: result not saved: {exc}", file=sys.stderr)
        return Response("unknown", "result_not_saved", evidence=resp.evidence)
    return resp


def _api_evidence(seen: _Seen) -> dict[str, object]:
    return {} if seen.api_base is None else {"api_base": seen.api_base}


def _decorate(
    resp: Response, who: int, op: str, seen: _Seen, snap: Snapshot
) -> Response:
    """Add the evidence every answer carries (spec 5.5, plan: `api_base`);
    the handler's own keys win."""
    req = seen.request
    base: dict[str, object] = {"sender": who, "operation": op, **_api_evidence(seen)}
    if req is not None:
        base["request_id"] = req.request_id
        base["run_id"] = req.run_id
    if snap.entry is not None:
        base["publisher_release"] = {"id": snap.entry.id, "digest": snap.entry.digest}
    return Response(resp.outcome, resp.reason, resp.result, {**base, **resp.evidence})


def _journal(layout: Layout, who: int, op: str, seen: _Seen, resp: Response) -> None:
    line = store.journal_line(who, op, seen.request, seen.digest, resp)
    try:
        store.journal(layout, line)
    except OSError as exc:
        print(f"publisher: journal not written: {exc}", file=sys.stderr)


def _answer(resp: Response) -> int:
    sys.stdout.write(request.emit(resp) + "\n")
    sys.stdout.flush()
    return _EXIT.get(resp.outcome, _REFUSED_EXIT)
