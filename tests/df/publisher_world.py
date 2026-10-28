"""Shared pieces of the publisher tests: valid requests of every operation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from df.config import Account, dump_accounts
from df.paths import Layout
from df.publisher import main as pmain
from df.publisher import request
from df.publisher.request import Request, Response
from df.stage2_config import dump_publisher, expected_publisher
from df.state import Snapshot
from tests.df.conftest import FakeLayout
from tests.df.launch_world import entry, put_record, record, snapshot, trust_paths

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


EXEC, REVIEW = 601, 602
A = entry("rel-a")


class World:
    """A fake layout with the three role accounts and a registered run."""

    def __init__(self, fake: FakeLayout) -> None:
        self.fake = fake
        self.lay = fake.layout
        self.me = os.getuid()  # the publisher is this process in the tests
        self.snap = snapshot("rel-a", A)
        self.out: list[Response] = []

    def run(
        self,
        op: str,
        raw: bytes | None = None,
        *,
        sudo: str | None = None,
        snap: Snapshot | None = None,
        capsys: pytest.CaptureFixture[str],
    ) -> tuple[int, Response]:
        if op in pmain.ADMIN_OPERATIONS:
            who = "0"
        else:
            who = str(REVIEW if op in ("publish-review", "read-token") else EXEC)
        env = {} if sudo == "" else {"SUDO_UID": sudo or who}
        stdin = self.lay.root / "stdin"
        stdin.write_bytes(raw if raw is not None else raw_of(op))
        rfd = os.open(stdin, os.O_RDONLY)
        try:
            code = pmain.main(
                [op],
                layout=self.lay,
                snapshot=snap or self.snap,
                uid=self.me,
                env=env,
                stdin_fd=rfd,
            )
        finally:
            os.close(rfd)
        line = capsys.readouterr().out
        assert line.count("\n") == 1
        return code, request.parse_response(line)

    def files(self, name: str) -> list[str]:
        path = self.lay.publisher_dir / name
        return sorted(p.name for p in path.iterdir()) if path.is_dir() else []

    def journal(self) -> list[dict[str, object]]:
        text = (self.lay.publisher_dir / "journal.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines()]


def build_world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
    """A fake layout with the role accounts, a registered run and trust paths."""
    fake_layout.make_dirs()
    lay = fake_layout.layout
    lay.runs_dir.mkdir(parents=True)
    lay.publisher_dir.mkdir(parents=True, mode=0o700)
    me = os.getuid()
    rows = {
        "df-exec": Account("df-exec", EXEC, 1, "ready", "t"),
        "df-review": Account("df-review", REVIEW, 1, "ready", "t"),
        "df-publisher": Account("df-publisher", me, os.getgid(), "ready", "t"),
    }
    lay.accounts.write_text(dump_accounts(rows))  # type: ignore[arg-type]
    text = dump_publisher(expected_publisher(rows))  # type: ignore[arg-type]
    (lay.etc / "publisher.toml").write_text(text)
    put_record(fake_layout, record("run-1", "rel-a"))
    trust_paths(monkeypatch)
    return World(fake_layout)
