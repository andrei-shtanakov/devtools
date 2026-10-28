"""Shared pieces of the publisher tests: valid requests of every operation."""

from __future__ import annotations

import json
from pathlib import Path

from df.paths import Layout
from df.publisher import request
from df.publisher.request import Request

SHA = "a" * 40
SHA_B = "b" * 40
RID = "req-00000001"
ROOT = {"host": "github.com", "repository_id": 5, "branch": "main"}
COMMON = {
    "schema": request.REQUEST_SCHEMA,
    "request_id": RID,
    "run_id": "run-1",
    "repository_id": 7,
    "pr": 12,
    "head_sha": SHA,
}
VALID: dict[str, dict[str, object]] = {
    "publish-review": {**COMMON, "decision": "approve", "body": "looks right"},
    "merge": dict(COMMON),
    "read-token": {
        k: COMMON[k] for k in ("schema", "request_id", "run_id", "repository_id")
    },
    "record-violation": {
        "schema": request.REQUEST_SCHEMA,
        "request_id": RID,
        "run_id": "run-1",
        "root_id": ROOT,
        "source": "df-trust",
        "observed_at": "2026-10-27T10:00:00Z",
        "detail": "the head moved",
    },
    "perimeter-status": {
        "schema": request.REQUEST_SCHEMA,
        "request_id": RID,
        "run_id": "run-1",
        "root_id": ROOT,
        "expected_snapshot_sha": SHA,
    },
    "self-check": {"schema": request.REQUEST_SCHEMA, "request_id": RID},
}


def raw_of(op: str, **over: object) -> bytes:
    """The bytes of a valid request of `op` with fields replaced (`None`
    removes a field)."""
    data = {**VALID[op], **over}
    return json.dumps({k: v for k, v in data.items() if v is not None}).encode()


def make_req(op: str = "merge", **over: object) -> Request:
    """A parsed valid request of `op` with fields replaced."""
    return request.parse(op, raw_of(op, **over))


def tmp_layout(tmp_path: Path) -> Layout:
    """A layout whose publisher directory exists (no fake owners)."""
    import os

    lay = Layout(Path(os.path.realpath(tmp_path)))
    lay.publisher_dir.mkdir(parents=True, mode=0o700)
    return lay
