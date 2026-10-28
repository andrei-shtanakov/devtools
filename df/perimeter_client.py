"""The consumer's adapter of `perimeter-status` (spec 6.1; stage 2a, Task 11).

Before each decision a consumer (`df-exec`, `df-review`) asks the publisher
through `sudo -n -u df-publisher <libexec>/df-publish perimeter-status`, the
request on stdin. A failed call (no answer: the call raised, or its output is
not one response line) is repeated with the same `request_id`; a
`stale_snapshot` makes the consumer reload its trust and ask again with a
new `request_id` and the new `expected_snapshot_sha`. Every other answer
ends the call; anything but a `blocked`/`clear` matching the consumer's own
snapshot is `unavailable`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NamedTuple

from df import platform as plat
from df.launch import SUDO
from df.paths import default_layout
from df.publisher import request
from df.publisher.request import BadResponse, Response

PUBLISHER = "df-publisher"
OPERATION = "perimeter-status"
CALL_TIMEOUT = 60.0

State = Literal["blocked", "clear", "unavailable"]


class RootId(NamedTuple):
    """The root of trust a run is bound to."""

    host: str
    repository_id: int
    branch: str


@dataclass(frozen=True)
class TrustView:
    """What the consumer built its `Trust` on: the snapshot and its epoch."""

    snapshot_sha: str
    last_epoch: int


@dataclass(frozen=True)
class PerimeterState:
    """The root's state for one decision; `reason` of `unavailable`."""

    state: State
    reason: str | None = None


class _NoAnswer(Exception):
    """The call gave no response (repeated with the same request id)."""


def sudo_call(data: bytes) -> bytes:
    """`df-publish perimeter-status` through `sudo`; its stdout.

    A response comes with any exit code (`refused` is 2); a call that did not
    start, timed out or overflowed is `OSError`."""
    wrapper = default_layout().libexec / "df-publish"
    argv = [SUDO, "-n", "-u", PUBLISHER, str(wrapper), OPERATION]
    result = plat.run_with_input(argv, data, timeout=CALL_TIMEOUT)
    if not result.launched or result.timed_out or result.stdout_truncated:
        raise OSError(f"{OPERATION}: the call failed (code {result.returncode})")
    return result.stdout


def status(
    run_id: str,
    root_id: RootId,
    trust: TrustView,
    *,
    reload: Callable[[], TrustView],
    call: Callable[[bytes], bytes] = sudo_call,
    attempts: int = 3,
) -> PerimeterState:
    """The perimeter state of `root_id` for the run, at most `attempts`
    calls; exhausted is `unavailable: attempts_exhausted`."""
    request_id = _new_id()
    for _ in range(attempts):
        try:
            resp = _ask(call, _payload(run_id, root_id, trust, request_id))
        except _NoAnswer:
            continue  # the same request again
        if resp.outcome == "stale_snapshot":
            trust = reload()
            request_id = _new_id()
            continue
        try:
            return _state_of(resp, trust)
        except _NoAnswer:
            continue
    return PerimeterState("unavailable", "attempts_exhausted")


def _new_id() -> str:
    return f"perimeter-{uuid.uuid4().hex}"


def _payload(run_id: str, root_id: RootId, trust: TrustView, request_id: str) -> bytes:
    data = {
        "schema": request.REQUEST_SCHEMA,
        "request_id": request_id,
        "run_id": run_id,
        "root_id": root_id._asdict(),
        "expected_snapshot_sha": trust.snapshot_sha,
    }
    return json.dumps(data, sort_keys=True).encode("utf-8")


def _ask(call: Callable[[bytes], bytes], payload: bytes) -> Response:
    try:
        raw = call(payload)
    except OSError as exc:
        raise _NoAnswer(str(exc)) from exc
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        raise _NoAnswer(f"{len(lines)} lines")
    try:
        return request.parse_response(lines[0])
    except BadResponse as exc:
        raise _NoAnswer(str(exc)) from exc


def _state_of(resp: Response, trust: TrustView) -> PerimeterState:
    if resp.outcome != "done":
        return PerimeterState("unavailable", f"{resp.outcome}: {resp.reason}")
    result = resp.result
    state, reason = result.get("state"), result.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise _NoAnswer("reason")
    if state == "unavailable":
        return PerimeterState("unavailable", reason)
    if state not in ("blocked", "clear"):
        raise _NoAnswer(f"state {state!r}")
    epoch = result.get("last_epoch")
    same = (
        result.get("snapshot_sha") == trust.snapshot_sha
        and type(epoch) is int
        and epoch == trust.last_epoch
    )
    if not same:
        return PerimeterState("unavailable", "snapshot_mismatch")
    return PerimeterState("blocked" if state == "blocked" else "clear", reason)


__all__ = [
    "PerimeterState",
    "RootId",
    "TrustView",
    "status",
    "sudo_call",
]
