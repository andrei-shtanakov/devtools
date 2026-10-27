"""The publisher's wire: request frame and schema, response (spec 5.3, 5.5).

A request is JSON on stdin, at most `MAX_BYTES`, read within `READ_TIMEOUT`;
its schema is per operation and strict (every field required, an unknown one
refused). A response is one JSON line on stdout; its `evidence.response_nonce`
is made per response by `emit` and exists nowhere else (not in a request, not
in a stored result, not in the journal) -- the marker of the behavioural check
of sudo's I/O log (r3 N3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import select
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, get_args

from df.launch import RUN_ID
from df.stage2_config import OPERATIONS

MAX_BYTES = 65536
READ_TIMEOUT = 5.0
REQUEST_SCHEMA = "df/publisher-request/v1"
RESPONSE_SCHEMA = "df/publisher-response/v1"
SELF_CHECK = "self-check"
ALL_OPERATIONS = (*OPERATIONS, SELF_CHECK)
MAX_BODY = 60000
MAX_DETAIL = 4000
MAX_RUN_ID = 256
#: A leading alphanumeric keeps every `ids/` name that starts with a dot a
#: temporary one: a request id can never look like `.<id>.<pid>.tmp`.
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z",
    re.ASCII,
)
DECISIONS = ("approve", "request_changes", "comment")
SOURCES = ("approval-policy", "df-trust")
PERIMETER_STATES = ("blocked", "clear", "unavailable")
_CHUNK = 65536

Outcome = Literal["done", "refused", "unknown", "stale_snapshot"]
OUTCOMES: tuple[str, ...] = get_args(Outcome)
#: Outcomes a repeated request may be answered with (`store.result`).
FINAL_OUTCOMES = frozenset({"done", "refused"})


class Refused(Exception):
    """The request is refused with a reason (`refused: <reason>`).

    `verified_not_done` is for a handler that, after its reconciliation,
    proved the external action did not happen: only then may a request that
    has an intent be answered `refused` (R23)."""

    def __init__(self, reason: str, *, verified_not_done: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.verified_not_done = verified_not_done


class BadResponse(ValueError):
    """A response line that is not `df/publisher-response/v1`."""


@dataclass(frozen=True)
class Request:
    """A validated request: the operation and its fields (all of them)."""

    operation: str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    @property
    def request_id(self) -> str:
        """The id the caller chose (unique over the installation)."""
        return str(self.fields["request_id"])

    @property
    def run_id(self) -> str | None:
        """The run the request belongs to (`None` for `self-check`)."""
        value = self.fields.get("run_id")
        return None if value is None else str(value)


@dataclass(frozen=True)
class Response:
    """`df/publisher-response/v1`."""

    outcome: Outcome
    reason: str | None = None
    result: Mapping[str, object] = field(default_factory=dict)
    evidence: Mapping[str, object] = field(default_factory=dict)
    schema: str = RESPONSE_SCHEMA


# --- reading ------------------------------------------------------------------


def read_frame(
    fd: int = 0,
    *,
    limit: int = MAX_BYTES,
    timeout: float = READ_TIMEOUT,
    wait: Callable[..., tuple[list[int], list[int], list[int]]] = select.select,
    read: Callable[[int, int], bytes] = os.read,
) -> bytes:
    """The whole of `fd` up to EOF; `too_large` past `limit` bytes,
    `read_timeout` if EOF does not come within `timeout` seconds in all."""
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    total = 0
    while True:
        left = deadline - time.monotonic()
        if left <= 0 or not wait([fd], [], [], left)[0]:
            raise Refused("read_timeout")
        chunk = read(fd, min(_CHUNK, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise Refused("too_large")
        chunks.append(chunk)


# --- field checks -------------------------------------------------------------


def _bad(name: str, what: str) -> Refused:
    return Refused(f"bad_request: {name} {what}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(name: str, value: object, *, limit: int, empty: bool = False) -> None:
    if not isinstance(value, str):
        raise _bad(name, "must be a string")
    if not value and not empty:
        raise _bad(name, "must not be empty")
    if len(value) > limit:
        raise _bad(name, f"is longer than {limit}")


def _schema(value: object) -> None:
    if value != REQUEST_SCHEMA:
        raise _bad("schema", f"must be {REQUEST_SCHEMA!r}")


def _request_id(value: object) -> None:
    ok = isinstance(value, str) and REQUEST_ID.fullmatch(value)
    if not ok:
        raise _bad("request_id", f"must match {REQUEST_ID.pattern}")


def _run_id(value: object) -> None:
    ok = isinstance(value, str) and len(value) <= MAX_RUN_ID and RUN_ID.fullmatch(value)
    if not ok:
        raise _bad("run_id", "must be [A-Za-z0-9._-]{1,256}")


def _positive(name: str) -> Callable[[object], None]:
    def check(value: object) -> None:
        if not _is_int(value) or value <= 0:  # type: ignore[operator]
            raise _bad(name, "must be a positive integer")

    return check


def _sha(name: str) -> Callable[[object], None]:
    def check(value: object) -> None:
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise _bad(name, "must be 40 lowercase hex digits")

    return check


def _choice(name: str, allowed: tuple[str, ...]) -> Callable[[object], None]:
    def check(value: object) -> None:
        if value not in allowed:
            raise _bad(name, f"must be one of {', '.join(allowed)}")

    return check


def _body(value: object) -> None:
    _text("body", value, limit=MAX_BODY, empty=True)


def _detail(value: object) -> None:
    _text("detail", value, limit=MAX_DETAIL, empty=True)


def _observed_at(value: object) -> None:
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise _bad("observed_at", "must be an RFC 3339 UTC instant (...Z)")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise _bad("observed_at", f"is not a time: {exc}") from exc


def _root_id(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"host", "repository_id", "branch"}:
        raise _bad("root_id", "must be {host, repository_id, branch}")
    _text("root_id.host", value["host"], limit=255)
    _text("root_id.branch", value["branch"], limit=255)
    _positive("root_id.repository_id")(value["repository_id"])


Check = Callable[[object], None]
_COMMON: dict[str, Check] = {
    "schema": _schema,
    "request_id": _request_id,
    "run_id": _run_id,
    "repository_id": _positive("repository_id"),
    "pr": _positive("pr"),
    "head_sha": _sha("head_sha"),
}
_SCHEMAS: Mapping[str, Mapping[str, Check]] = MappingProxyType(
    {
        "publish-review": {
            **_COMMON,
            "decision": _choice("decision", DECISIONS),
            "body": _body,
        },
        "merge": dict(_COMMON),
        "read-token": {
            k: _COMMON[k] for k in ("schema", "request_id", "run_id", "repository_id")
        },
        "record-violation": {
            "schema": _schema,
            "request_id": _request_id,
            "run_id": _run_id,
            "root_id": _root_id,
            "source": _choice("source", SOURCES),
            "observed_at": _observed_at,
            "detail": _detail,
        },
        "perimeter-status": {
            "schema": _schema,
            "request_id": _request_id,
            "run_id": _run_id,
            "root_id": _root_id,
            "expected_snapshot_sha": _sha("expected_snapshot_sha"),
        },
        SELF_CHECK: {"schema": _schema, "request_id": _request_id},
    }
)


# --- parsing ------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _no_constant(name: str) -> object:
    raise ValueError(f"constant {name} is not JSON")


def _load(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise Refused(f"bad_request: not JSON ({type(exc).__name__})") from exc


def parse(op: str, raw: bytes) -> Request:
    """The request `raw` for operation `op`; anything else is `Refused`.

    Every field of the operation is required; an unknown field, a wrong type
    or an operation of another schema is refused."""
    schema = _SCHEMAS.get(op)
    if schema is None:
        raise Refused(f"unknown_operation: {op!r}")
    if len(raw) > MAX_BYTES:
        raise Refused("too_large")
    data = _load(raw)
    if not isinstance(data, dict):
        raise Refused("bad_request: the request must be a JSON object")
    unknown = sorted(set(data) - set(schema))
    if unknown:
        raise Refused(f"bad_request: unknown field {unknown[0]!r}")
    for name, check in schema.items():
        if name not in data:
            raise Refused(f"bad_request: missing field {name!r}")
        check(data[name])
    return Request(op, data)


def canonical(req: Request) -> bytes:
    """The request as JSON: sorted keys, no whitespace, ASCII."""
    return json.dumps(
        dict(req.fields), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def digest(req: Request) -> str:
    """`sha256:<hex>` of the canonical content (the idempotency key's part)."""
    return "sha256:" + hashlib.sha256(canonical(req)).hexdigest()


# --- response -----------------------------------------------------------------


def perimeter_result(
    state: str, reason: str | None, snapshot_sha: str | None, last_epoch: int | None
) -> dict[str, object]:
    """The `result` of `perimeter-status` (spec 5.2); `state` is checked."""
    if state not in PERIMETER_STATES:
        raise ValueError(f"perimeter state {state!r}")
    return {
        "state": state,
        "reason": reason,
        "snapshot_sha": snapshot_sha,
        "last_epoch": last_epoch,
    }


def _response_data(resp: Response) -> dict[str, object]:
    return {
        "schema": resp.schema,
        "outcome": resp.outcome,
        "reason": resp.reason,
        "result": dict(resp.result),
        "evidence": dict(resp.evidence),
    }


def dump_response(resp: Response) -> str:
    """One JSON line, no newline (what `store` keeps; no nonce)."""
    return json.dumps(_response_data(resp), sort_keys=True, separators=(",", ":"))


def emit(resp: Response) -> str:
    """The line the publisher prints: `dump_response` with a fresh
    `evidence.response_nonce` (128 random bits), made here and nowhere else."""
    evidence = {**resp.evidence, "response_nonce": secrets.token_hex(16)}
    return dump_response(
        Response(resp.outcome, resp.reason, resp.result, evidence, resp.schema)
    )


def parse_response(raw: bytes | str) -> Response:
    """A client's reading of a response line; anything else is `BadResponse`."""
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        raise BadResponse(f"not JSON: {exc}") from exc
    keys = {"schema", "outcome", "reason", "result", "evidence"}
    if not isinstance(data, dict) or set(data) != keys:
        raise BadResponse(f"keys must be {sorted(keys)}")
    if data["schema"] != RESPONSE_SCHEMA:
        raise BadResponse(f"schema must be {RESPONSE_SCHEMA!r}")
    if data["outcome"] not in OUTCOMES:
        raise BadResponse(f"outcome {data['outcome']!r}")
    reason = data["reason"]
    if reason is not None and not isinstance(reason, str):
        raise BadResponse("reason must be a string or null")
    for name in ("result", "evidence"):
        if not isinstance(data[name], dict):
            raise BadResponse(f"{name} must be an object")
    return Response(data["outcome"], reason, data["result"], data["evidence"])
