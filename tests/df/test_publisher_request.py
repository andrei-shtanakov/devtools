"""The publisher's wire: frame, schema, digest, response (stage 2a, Task 7)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from df.publisher import request
from df.publisher.request import Refused, Response
from tests.df.publisher_world import RID, SHA, VALID, make_req, raw_of

OPS = sorted(VALID)


def reason_of(op: str, raw: bytes) -> str:
    with pytest.raises(Refused) as info:
        request.parse(op, raw)
    return info.value.reason


# --- every field of every operation ---------------------------------------------


@pytest.mark.parametrize("op", OPS)
def test_valid_request_parses(op: str) -> None:
    req = request.parse(op, raw_of(op))
    assert req.operation == op
    assert req.request_id == RID
    assert req.run_id == (None if op == "self-check" else "run-1")


FIELDS = [(op, name) for op in OPS for name in VALID[op]]


@pytest.mark.parametrize(("op", "name"), FIELDS)
def test_missing_field_is_refused(op: str, name: str) -> None:
    assert "missing field" in reason_of(op, raw_of(op, **{name: None}))


@pytest.mark.parametrize(("op", "name"), FIELDS)
def test_wrong_type_is_refused(op: str, name: str) -> None:
    assert reason_of(op, raw_of(op, **{name: []})).startswith("bad_request")
    assert reason_of(op, raw_of(op, **{name: True})).startswith("bad_request")


@pytest.mark.parametrize("op", OPS)
def test_unknown_field_is_refused(op: str) -> None:
    assert "unknown field" in reason_of(op, raw_of(op, extra="x"))


@pytest.mark.parametrize("op", ["merge", "publish-review", "read-token"])
def test_field_of_another_operation_is_refused(op: str) -> None:
    other = {"root_id": VALID["perimeter-status"]["root_id"]}
    assert "unknown field" in reason_of(op, raw_of(op, **other))


def test_extra_operation_is_refused() -> None:
    assert reason_of("bogus", raw_of("merge")).startswith("unknown_operation")


BAD_VALUES = [
    ("merge", "schema", "df/publisher-request/v2"),
    ("merge", "request_id", "short"),
    ("merge", "request_id", "x" * 129),
    ("merge", "request_id", "has/slash-0001"),
    ("merge", "request_id", ".abcdefgh.tmp"),
    ("merge", "request_id", "has space 0001"),
    ("merge", "run_id", ""),
    ("merge", "run_id", "a/b"),
    ("merge", "run_id", "r" * 257),
    ("merge", "repository_id", 0),
    ("merge", "repository_id", "7"),
    ("merge", "pr", -1),
    ("merge", "head_sha", "A" * 40),
    ("merge", "head_sha", "a" * 39),
    ("merge", "head_sha", "a" * 41),
    ("publish-review", "decision", "merge"),
    ("publish-review", "body", "x" * 60001),
    ("record-violation", "source", "agent"),
    ("record-violation", "observed_at", "2026-10-27T10:00:00+00:00"),
    ("record-violation", "observed_at", "2026-13-27T10:00:00Z"),
    ("record-violation", "observed_at", "yesterday"),
    ("record-violation", "detail", "x" * 4001),
    ("record-violation", "root_id", {"host": "h", "repository_id": 5}),
    ("record-violation", "root_id", {"host": "", "repository_id": 5, "branch": "b"}),
    (
        "perimeter-status",
        "root_id",
        {"host": "h", "repository_id": 0, "branch": "b"},
    ),
    (
        "perimeter-status",
        "root_id",
        {"host": "h", "repository_id": 5, "branch": "b", "x": 1},
    ),
    ("perimeter-status", "expected_snapshot_sha", "abc"),
]


@pytest.mark.parametrize(("op", "name", "value"), BAD_VALUES)
def test_bad_value_is_refused(op: str, name: str, value: object) -> None:
    assert reason_of(op, raw_of(op, **{name: value})).startswith("bad_request")


BOUNDARY_VALUES = [
    ("merge", "request_id", "x" * 128),
    ("merge", "request_id", "abcdefgh"),
    ("merge", "run_id", "r" * 256),
    ("publish-review", "body", "x" * 60000),
    ("publish-review", "body", ""),
    ("record-violation", "detail", "x" * 4000),
    ("record-violation", "observed_at", "2026-10-27T10:00:00.123456Z"),
]


@pytest.mark.parametrize(("op", "name", "value"), BOUNDARY_VALUES)
def test_boundary_value_is_accepted(op: str, name: str, value: object) -> None:
    request.parse(op, raw_of(op, **{name: value}))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"\xff\xfe",
        b'{"schema": NaN}',
        b'{"schema": 1, "schema": 2}',
        b"[" * 20000,
        b"{" + b'"a":' * 5000,
    ],
)
def test_not_a_json_object_is_refused(raw: bytes) -> None:
    assert reason_of("merge", raw).startswith("bad_request")


def test_duplicate_key_is_refused() -> None:
    text = json.dumps(VALID["merge"])[:-1] + ', "pr": 13}'
    assert reason_of("merge", text.encode()).startswith("bad_request")


# --- the frame ------------------------------------------------------------------


def test_read_frame_reads_to_eof(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.write_bytes(b"x" * request.MAX_BYTES)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert request.read_frame(fd) == b"x" * request.MAX_BYTES
    finally:
        os.close(fd)


def test_read_frame_too_large(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.write_bytes(b"x" * (request.MAX_BYTES + 1))
    fd = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(Refused) as info:
            request.read_frame(fd)
    finally:
        os.close(fd)
    assert info.value.reason == "too_large"


def test_parse_too_large() -> None:
    assert reason_of("merge", b" " * (request.MAX_BYTES + 1)) == "too_large"


def test_read_frame_slow_stdin_times_out() -> None:
    rfd, wfd = os.pipe()
    started = time.monotonic()
    try:
        os.write(wfd, b'{"a":')  # no more, and no EOF
        with pytest.raises(Refused) as info:
            request.read_frame(rfd, timeout=0.3)
    finally:
        os.close(rfd)
        os.close(wfd)
    assert info.value.reason == "read_timeout"
    assert time.monotonic() - started < 5


def test_read_frame_trickle_is_bounded_in_total() -> None:
    """A byte at a time must not stretch the deadline."""
    rfd, wfd = os.pipe()
    stop = threading.Event()

    def trickle() -> None:
        while not stop.is_set():
            try:
                os.write(wfd, b" ")
            except OSError:
                return
            time.sleep(0.05)

    thread = threading.Thread(target=trickle, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(Refused) as info:
            request.read_frame(rfd, timeout=0.4)
    finally:
        stop.set()
        thread.join(timeout=5)
        os.close(rfd)
        os.close(wfd)
    assert info.value.reason == "read_timeout"
    assert time.monotonic() - started < 5


# --- canonical form and digest --------------------------------------------------


def test_canonical_is_sorted_and_compact() -> None:
    text = request.canonical(make_req("merge")).decode()
    assert text == json.dumps(VALID["merge"], sort_keys=True, separators=(",", ":"))


def test_digest_ignores_key_order_and_whitespace() -> None:
    shuffled = json.dumps(dict(reversed(list(VALID["merge"].items()))), indent=3)
    a = request.parse("merge", shuffled.encode())
    assert request.digest(a) == request.digest(make_req("merge"))
    assert request.digest(a).startswith("sha256:")


def test_digest_differs_with_content() -> None:
    assert request.digest(make_req("merge")) != request.digest(
        make_req("merge", head_sha="b" * 40)
    )


# --- response -------------------------------------------------------------------


def test_response_roundtrip_every_outcome() -> None:
    for outcome in request.OUTCOMES:
        resp = Response(outcome, "why", {"a": [1, None]}, {"e": "v"})  # type: ignore[arg-type]
        assert request.parse_response(request.dump_response(resp)) == resp
    assert request.OUTCOMES == ("done", "refused", "unknown", "stale_snapshot")


@pytest.mark.parametrize("state", request.PERIMETER_STATES)
def test_response_roundtrip_perimeter_states(state: str) -> None:
    result = request.perimeter_result(state, "busy", SHA, 3)
    resp = Response("done", None, result)
    back = request.parse_response(request.emit(resp))
    assert back.result == {
        "state": state,
        "reason": "busy",
        "snapshot_sha": SHA,
        "last_epoch": 3,
    }


def test_perimeter_state_is_checked() -> None:
    with pytest.raises(ValueError, match="state"):
        request.perimeter_result("open", None, None, None)


def test_response_is_one_line() -> None:
    line = request.emit(Response("done", None, {"body": "a\nb c"}))
    assert "\n" not in line
    assert request.parse_response(line).result == {"body": "a\nb c"}


def test_nonce_is_per_response_and_only_in_the_emitted_line() -> None:
    resp = Response("done")
    first, second = request.emit(resp), request.emit(resp)
    nonce_a = request.parse_response(first).evidence["response_nonce"]
    nonce_b = request.parse_response(second).evidence["response_nonce"]
    assert isinstance(nonce_a, str)
    assert len(nonce_a) == 32
    assert int(nonce_a, 16) != int(str(nonce_b), 16)
    assert "response_nonce" not in request.dump_response(resp)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "[]",
        json.dumps({"schema": "df/publisher-response/v1"}),
        json.dumps(
            {
                "schema": "x",
                "outcome": "done",
                "reason": None,
                "result": {},
                "evidence": {},
            }
        ),
        json.dumps(
            {
                "schema": request.RESPONSE_SCHEMA,
                "outcome": "maybe",
                "reason": None,
                "result": {},
                "evidence": {},
            }
        ),
    ],
)
def test_bad_response_is_rejected(line: str) -> None:
    with pytest.raises(request.BadResponse):
        request.parse_response(line)
