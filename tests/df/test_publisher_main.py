"""`df-publish`: sender, run check and the frame (stage 2a, Task 7)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from df import cli
from df.config import Account, dump_accounts
from df.journal import Stable
from df.paths import Layout
from df.publisher import main as pmain
from df.publisher import request, store
from df.publisher.main import Context, Refused
from df.publisher.request import Response
from df.stage2_config import (
    OPERATIONS,
    PublisherConfig,
    dump_publisher,
    expected_publisher,
)
from df.state import RawSnapshot
from tests.df.conftest import FakeLayout
from tests.df.launch_world import (
    Spy,
    entry,
    put_record,
    record,
    snapshot,
    trust_paths,
)
from tests.df.publisher_world import RID, SHA_B, VALID, raw_of

EXEC, REVIEW = 601, 602
A = entry("rel-a")
EXEC_OPS = {"merge", "record-violation", "perimeter-status"}
REVIEW_OPS = {"publish-review", "read-token", "record-violation", "perimeter-status"}


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
        snap: object = None,
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
                snapshot=snap or self.snap,  # type: ignore[arg-type]
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


@pytest.fixture()
def world(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch) -> World:
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
    (lay.etc / "publisher.toml").write_text(dump_publisher(expected_publisher(rows)))  # type: ignore[arg-type]
    put_record(fake_layout, record("run-1", "rel-a"))
    trust_paths(monkeypatch)
    return World(fake_layout)


@pytest.fixture()
def table(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Context]]:
    """Fresh stubs; `calls[op]` collects the contexts of a fake handler."""
    monkeypatch.setattr(pmain, "OPERATION_TABLE", dict(pmain.OPERATION_TABLE))
    return {}


def install(
    table: dict[str, list[Context]],
    op: str,
    handler: object,
) -> list[Context]:
    calls: list[Context] = []
    table[op] = calls

    def run(ctx: Context) -> Response:
        calls.append(ctx)
        return handler(ctx)  # type: ignore[operator]

    pmain.OPERATION_TABLE[op] = run
    return calls


def acting(ctx: Context) -> Response:
    ctx.record_intent({"step": "act"})
    return Response("done", None, {"n": 1}, {"id": 42})


# --- sender -------------------------------------------------------------------


PUB = 900
CFG = PublisherConfig({EXEC: frozenset(EXEC_OPS), REVIEW: frozenset(REVIEW_OPS)})


def who(op: str, sudo: str | None, uid: int = PUB) -> int:
    env = {} if sudo is None else {"SUDO_UID": sudo}
    return pmain.sender(CFG, op, publisher_uid=PUB, uid=uid, env=env)


def reason_of(op: str, sudo: str | None, uid: int = PUB) -> str:
    with pytest.raises(Refused) as info:
        who(op, sudo, uid)
    return info.value.reason


def test_sender_resolves_by_table() -> None:
    assert who("merge", "601") == EXEC
    assert who("publish-review", "602") == REVIEW
    assert who("self-check", "0") == 0


def test_sender_table_is_the_spec_table() -> None:
    for op in OPERATIONS:
        assert (op in EXEC_OPS) == (reason_of_or_none(op, str(EXEC)) is None), op


def reason_of_or_none(op: str, sudo: str) -> str | None:
    try:
        who(op, sudo)
    except Refused as exc:
        return exc.reason
    return None


def test_sender_uid_must_be_the_publisher() -> None:
    assert reason_of("merge", "601", uid=PUB + 1) == "not_publisher_uid"
    assert reason_of("merge", "601", uid=0) == "not_publisher_uid"
    assert reason_of("self-check", "0", uid=0) == "not_publisher_uid"


@pytest.mark.parametrize("sudo", [None, " 601", "601 ", "0601", "+601", "-1", "6.0"])
def test_sudo_uid_noncanonical(sudo: str | None) -> None:
    assert reason_of("merge", sudo) == "sender_unknown"


@pytest.mark.parametrize("sudo", ["", "٦٠١", "601\n", "0x259", "00"])
def test_sudo_uid_not_ascii_decimal(sudo: str) -> None:
    assert reason_of("merge", sudo) == "sender_unknown"


def test_sender_not_in_table() -> None:
    assert reason_of("merge", "777") == "sender_unknown"


def test_sender_operation_not_allowed() -> None:
    assert reason_of("publish-review", "601") == "operation_not_allowed"
    assert reason_of("merge", "602") == "operation_not_allowed"
    assert reason_of("read-token", "601") == "operation_not_allowed"


def test_self_check_only_from_root_sender() -> None:
    assert reason_of("self-check", "601") == "operation_not_allowed"
    assert reason_of("self-check", "602") == "operation_not_allowed"
    assert reason_of("self-check", "777") == "operation_not_allowed"
    assert reason_of("merge", "0") == "operation_not_allowed"
    assert reason_of("perimeter-status", "0") == "operation_not_allowed"
    assert reason_of("self-check", None) == "sender_unknown"


def test_root_is_not_a_table_sender_even_if_listed() -> None:
    cfg = PublisherConfig({0: frozenset({"merge"})})
    with pytest.raises(Refused) as info:
        pmain.sender(cfg, "merge", publisher_uid=PUB, uid=PUB, env={"SUDO_UID": "0"})
    assert info.value.reason == "operation_not_allowed"


def test_admin_operations_is_a_constant() -> None:
    assert pmain.ADMIN_OPERATIONS == frozenset({"self-check"})


# --- the run --------------------------------------------------------------------


def test_check_run_returns_the_record(world: World) -> None:
    rec = pmain.check_run(world.lay, world.snap, "run-1")
    assert (rec.run_id, rec.release_id) == ("run-1", "rel-a")


def test_check_run_unknown(world: World) -> None:
    with pytest.raises(Refused) as info:
        pmain.check_run(world.lay, world.snap, "run-404")
    assert info.value.reason == "run_unknown"


def test_check_run_revoked_names_the_reason(world: World) -> None:
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    with pytest.raises(Refused) as info:
        pmain.check_run(world.lay, revoked, "run-1")
    assert info.value.reason.startswith("release_revoked: ")
    assert "bad" in info.value.reason


def test_check_run_release_missing_from_registry(world: World) -> None:
    with pytest.raises(Refused) as info:
        pmain.check_run(world.lay, snapshot("rel-b", entry("rel-b")), "run-1")
    assert info.value.reason.startswith("release_unknown")


def test_check_run_digest_mismatch(world: World) -> None:
    other = entry("rel-a", digest="sha256:" + "e" * 64)
    with pytest.raises(Refused) as info:
        pmain.check_run(world.lay, snapshot("rel-a", other), "run-1")
    assert info.value.reason.startswith("release_digest_mismatch")


def test_check_run_is_judged_by_the_snapshot_not_the_file(world: World) -> None:
    from df.config import ReleasesConfig, dump_releases

    text = dump_releases(ReleasesConfig((entry("rel-a", "revoked"),)))
    (world.lay.etc / "releases.toml").write_text(text)  # edited after the snapshot
    assert pmain.check_run(world.lay, world.snap, "run-1").run_id == "run-1"


def test_check_run_record_unreadable(world: World) -> None:
    (world.lay.runs_dir / "run-2.json").write_text("{")
    with pytest.raises(Refused) as info:
        pmain.check_run(world.lay, world.snap, "run-2")
    assert info.value.reason == "run_record_invalid"


# --- the frame ------------------------------------------------------------------


def test_stubs_refuse_before_any_intent(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    for op in sorted(VALID):
        raw = raw_of(op, request_id=f"req-{op}-0001")
        code, resp = world.run(op, raw, capsys=capsys)
        assert (code, resp.outcome, resp.reason) == (2, "refused", "not_implemented")
    assert world.files("intents") == []
    assert world.files("results") == []
    assert len(world.journal()) == len(VALID)


def test_intent_then_result_then_replay(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", acting)
    code, first = world.run("merge", capsys=capsys)
    assert (code, first.outcome, first.result) == (0, "done", {"n": 1})
    assert first.evidence["id"] == 42
    assert first.evidence["sender"] == EXEC
    assert first.evidence["request_id"] == RID
    assert first.evidence["operation"] == "merge"
    assert first.evidence["api_base"] == "https://api.github.com"
    assert first.evidence["publisher_release"] == {
        "id": "rel-a",
        "digest": A.digest,
    }
    assert len(world.files("intents")) == len(world.files("results")) == 1
    code, again = world.run("merge", capsys=capsys)
    assert (code, again.outcome, again.result) == (0, "done", {"n": 1})
    assert again.evidence["replayed"] is True
    assert len(calls) == 1  # no second action
    assert first.evidence["response_nonce"] != again.evidence["response_nonce"]
    stored = (world.lay.publisher_dir / "results").iterdir()
    assert all("response_nonce" not in p.read_text() for p in stored)


def test_refused_after_intent_is_kept_only_if_verified(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def refuses(ctx: Context) -> Response:
        ctx.record_intent({"step": "act"})
        raise Refused("github said no", verified_not_done=True)

    calls = install(table, "merge", refuses)
    world.run("merge", capsys=capsys)
    code, again = world.run("merge", capsys=capsys)
    assert (code, again.outcome, again.reason) == (2, "refused", "github said no")
    assert len(calls) == 1


def test_refused_before_intent_is_recomputed(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", lambda _c: Response("refused", "pr_closed"))
    world.run("merge", capsys=capsys)
    world.run("merge", capsys=capsys)
    assert len(calls) == 2
    assert world.files("results") == []


def test_unexpected_exception_after_intent_is_unknown_and_not_saved(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def dies(ctx: Context) -> Response:
        ctx.record_intent({"step": "act"})
        raise RuntimeError("boom")

    install(table, "merge", dies)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (
        3,
        "unknown",
        "state_exists: internal_error",
    )
    assert world.files("results") == []
    assert len(world.files("intents")) == 1
    # the repeat goes to the reconciliation: the handler sees the earlier intent
    seen: list[object] = []

    def reconciles(ctx: Context) -> Response:
        seen.append(ctx.intent)
        return Response("done", None, {"confirmed": True})

    install(table, "merge", reconciles)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome) == (0, "done")
    assert seen == [{"step": "act"}]
    assert len(world.files("results")) == 1


def test_handler_unknown_is_not_saved(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def not_sure(ctx: Context) -> Response:
        ctx.record_intent({"step": "act"})
        return Response("unknown", "not_confirmed")

    calls = install(table, "merge", not_sure)
    code, _ = world.run("merge", capsys=capsys)
    world.run("merge", capsys=capsys)
    assert code == 3
    assert len(calls) == 2
    assert world.files("results") == []


def test_exception_before_intent_is_refused(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def fails(_ctx: Context) -> Response:
        raise RuntimeError("boom")

    install(table, "merge", fails)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "internal_error")
    assert world.files("results") == []


def test_result_not_saved_is_unknown(
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(table, "merge", acting)

    def broken(*_a: object) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(store, "result", broken)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (3, "unknown", "result_not_saved")


@pytest.mark.parametrize("op", sorted(store.NOT_REPLAYED))
def test_computed_operations_are_not_replayed(
    op: str,
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = install(table, op, lambda _c: Response("done", None, {"state": "clear"}))
    world.run(op, capsys=capsys)
    world.run(op, capsys=capsys)
    assert len(calls) == 2
    assert world.files("results") == []
    assert world.files("intents") == []


def test_perimeter_status_states_and_stale_snapshot_round_trip(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(
        [
            Response("done", None, request.perimeter_result("blocked", "r", None, 1)),
            Response("stale_snapshot", "snapshot_moved"),
        ]
    )
    install(table, "perimeter-status", lambda _c: next(answers))
    code, resp = world.run("perimeter-status", capsys=capsys)
    assert (code, resp.result["state"]) == (0, "blocked")
    code, resp = world.run("perimeter-status", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "stale_snapshot", "snapshot_moved")
    assert world.files("results") == []


def test_self_check_skips_the_run_check(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "self-check", lambda _c: Response("done", None, {"p": []}))
    raw = raw_of("self-check")
    code, resp = world.run("self-check", raw, snap=snapshot(None), capsys=capsys)
    assert (code, resp.outcome) == (0, "done")
    assert calls[0].record is None
    assert calls[0].request.run_id is None
    assert world.files("results") == []


def test_unknown_run_is_refused_before_reservation(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", acting)
    code, resp = world.run("merge", raw_of("merge", run_id="run-404"), capsys=capsys)
    assert (code, resp.reason) == (2, "run_unknown")
    assert calls == []
    assert world.files("ids") == []


def test_revoked_release_is_refused_before_reservation(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", acting)
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    code, resp = world.run("merge", snap=revoked, capsys=capsys)
    assert code == 2
    assert (resp.reason or "").startswith("release_revoked: ")
    assert calls == []
    assert world.files("ids") == []


def test_request_id_reused_never_reaches_the_operation(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", acting)
    world.run("merge", capsys=capsys)
    code, resp = world.run("merge", raw_of("merge", head_sha=SHA_B), capsys=capsys)
    assert (code, resp.reason) == (2, "request_id_reused")
    assert len(calls) == 1


def test_sender_refusal_is_a_response(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, resp = world.run("publish-review", sudo=str(EXEC), capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "operation_not_allowed")
    code, resp = world.run("merge", sudo="", capsys=capsys)
    assert resp.reason == "sender_unknown"
    assert world.files("ids") == []


def test_bad_request_is_refused_and_journalled(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    code, resp = world.run("merge", b'{"schema": 1}', capsys=capsys)
    assert code == 2
    assert (resp.reason or "").startswith("bad_request")
    (line,) = world.journal()
    assert line["request_id"] is None
    assert line["outcome"] == "refused"
    assert world.files("ids") == []


def test_too_large_and_slow_stdin_through_main(
    world: World,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, resp = world.run("merge", b" " * (request.MAX_BYTES + 1), capsys=capsys)
    assert (code, resp.reason) == (2, "too_large")
    monkeypatch.setattr(request, "READ_TIMEOUT", 0.3)
    rfd, wfd = os.pipe()  # never written, never closed
    try:
        code = pmain.main(
            ["merge"],
            layout=world.lay,
            snapshot=world.snap,
            uid=world.me,
            env={"SUDO_UID": str(EXEC)},
            stdin_fd=rfd,
        )
    finally:
        os.close(rfd)
        os.close(wfd)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "read_timeout"


@pytest.fixture()
def no_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: object, **_k: object) -> bytes:
        raise AssertionError("stdin was read")

    monkeypatch.setattr(request, "read_frame", forbidden)


def test_extra_argv_refused_before_stdin(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (["merge", "extra"], [], ["bogus"], ["merge", ""]):
        code = pmain.main(
            argv,
            layout=world.lay,
            snapshot=world.snap,
            uid=world.me,
            env={"SUDO_UID": str(EXEC)},
        )
        assert code == 2
    assert capsys.readouterr().out == ""


def test_sender_refused_before_stdin(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = pmain.main(
        ["merge"],
        layout=world.lay,
        snapshot=world.snap,
        uid=world.me,
        env={"SUDO_UID": "777"},
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "sender_unknown"


def test_process_uid_must_be_the_publisher(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = pmain.main(
        ["merge"],
        layout=world.lay,
        snapshot=world.snap,
        uid=world.me + 1,
        env={"SUDO_UID": str(EXEC)},
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "not_publisher_uid"


def test_missing_publisher_account_refuses(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    world.lay.accounts.write_text(dump_accounts({}))
    code = pmain.main(
        ["merge"],
        layout=world.lay,
        snapshot=world.snap,
        uid=world.me,
        env={"SUDO_UID": str(EXEC)},
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "not_publisher_uid"


# --- the journal ----------------------------------------------------------------


def test_journal_has_no_token(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "ghs_SECRETSECRETSECRET"
    install(table, "read-token", lambda _c: Response("done", None, {"token": secret}))
    code, resp = world.run("read-token", capsys=capsys)
    assert (code, resp.result["token"]) == (0, secret)  # the client gets it
    text = (world.lay.publisher_dir / "journal.jsonl").read_text()
    assert secret not in text
    assert "<redacted>" in text
    nonce = str(resp.evidence["response_nonce"])
    assert nonce not in text
    for sub in ("ids", "keys", "intents", "results"):
        for path in (world.lay.publisher_dir / sub).iterdir():
            assert secret not in path.read_text()


def test_journal_line_per_request(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    install(table, "merge", acting)
    world.run("merge", capsys=capsys)
    world.run("merge", capsys=capsys)
    first, second = world.journal()
    assert first["request_id"] == second["request_id"] == RID
    assert (first["sender"], first["operation"], first["run_id"]) == (
        EXEC,
        "merge",
        "run-1",
    )
    assert first["digest"] == second["digest"]
    assert first["outcome"] == second["outcome"] == "done"


# --- the snapshot is the only source --------------------------------------------


def test_publisher_serve_rereads_nothing(
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the snapshot, `releases.toml`, `install.toml`, `models.toml` and
    `current` are not read (carried from Task 5): a file edited afterwards
    mixes nothing in, whatever the operation."""
    from df.config import ReleasesConfig, dump_releases

    install(table, "merge", acting)
    install(table, "self-check", lambda _c: Response("done"))
    text = dump_releases(ReleasesConfig((entry("rel-a", "revoked"),)))
    (world.lay.etc / "releases.toml").write_text(text)
    (world.lay.etc / "install.toml").write_text("garbage")
    (world.lay.etc / "models.toml").write_text("garbage")
    spy = Spy(monkeypatch)
    for op in sorted(VALID):
        world.run(op, raw_of(op, request_id=f"req-{op}-0001"), capsys=capsys)
    world.run("merge", raw_of("merge", request_id="req-merge-0001"), capsys=capsys)
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    _, resp = world.run("merge", raw_of("merge", request_id="req-00000009"),
                        snap=revoked, capsys=capsys)  # fmt: skip
    assert (resp.reason or "").startswith("release_revoked")
    assert spy.hits == []


# --- through cli.main -----------------------------------------------------------


def test_cli_publisher_serve_runs_the_frame(
    world: World, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from df.config import ReleasesConfig, dump_releases

    raw = RawSnapshot(
        1, "rel-a", dump_releases(ReleasesConfig((A,))).encode(), None, None, False
    )
    monkeypatch.setattr(os, "environ", {"SUDO_UID": str(EXEC)})
    monkeypatch.setattr(pmain, "_own_uid", lambda _u: world.me)
    stdin = world.lay.root / "stdin"
    stdin.write_bytes(raw_of("merge"))
    rfd = os.open(stdin, os.O_RDONLY)
    monkeypatch.setattr(request, "read_frame", _frame_of(rfd))
    code = cli.main(
        ["publisher", "serve", "merge"],
        mode="reader",
        read=Stable(raw, 0),
        lock_fd=None,
        layout=world.lay,
    )
    os.close(rfd)
    assert code == 2
    assert request.parse_response(capsys.readouterr().out).reason == "not_implemented"


def _frame_of(fd: int):  # noqa: ANN202
    real = request.read_frame

    def read(_fd: int = 0, **kw: object) -> bytes:
        return real(fd, **kw)  # type: ignore[arg-type]

    return read


def test_cli_publisher_serve_without_accounts_is_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lay = Layout(Path(os.path.realpath(tmp_path)))
    raw = RawSnapshot(1, None, None, None, None, False)
    code = cli.main(
        ["publisher", "serve", "merge"],
        mode="reader",
        read=Stable(raw, 0),
        lock_fd=None,
        layout=lay,
    )
    assert code == 2
    assert request.parse_response(capsys.readouterr().out).outcome == "refused"


# --- fix round 1 ----------------------------------------------------------------


def crash_after_intent(payload: dict[str, object]):  # noqa: ANN201
    def dies(ctx: Context) -> Response:
        ctx.record_intent(payload)
        raise RuntimeError("boom")

    return dies


def test_large_intent_then_crash_then_repeat_reaches_reconciliation(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"body": "é" * 50000}  # well over 4 KiB once escaped
    install(table, "merge", crash_after_intent(payload))
    assert world.run("merge", capsys=capsys)[1].outcome == "unknown"
    seen: list[object] = []

    def reconciles(ctx: Context) -> Response:
        seen.append(ctx.intent)
        return Response("done")

    install(table, "merge", reconciles)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome) == (0, "done")
    assert seen == [payload]


def _intent_file(world: World) -> Path:
    (path,) = (world.lay.publisher_dir / "intents").iterdir()
    return path


@pytest.mark.parametrize("damage", ["garbage", "not-an-intent", "hardlink"])
def test_damaged_intent_is_unknown_not_refused(
    damage: str,
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = install(table, "merge", crash_after_intent({"step": 1}))
    world.run("merge", capsys=capsys)
    path = _intent_file(world)
    if damage == "garbage":
        path.write_text("{")
    elif damage == "not-an-intent":
        path.write_text("[]")
    else:
        os.link(path, world.lay.root / "other-name")
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (
        3,
        "unknown",
        "state_exists: internal_error",
    )
    assert len(calls) == 1  # the handler never ran again


def test_damaged_result_is_unknown_not_refused(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = install(table, "merge", acting)
    world.run("merge", capsys=capsys)
    (path,) = (world.lay.publisher_dir / "results").iterdir()
    path.write_text("{}")
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (
        3,
        "unknown",
        "state_exists: internal_error",
    )
    assert len(calls) == 1


def test_result_that_cannot_be_serialised_after_intent_is_unknown(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def odd(ctx: Context) -> Response:
        ctx.record_intent({"step": 1})
        return Response("done", None, {"x": object()})

    install(table, "merge", odd)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (3, "unknown", "result_not_saved")
    assert world.files("results") == []


def test_sender_refusals_are_journalled_without_the_raw_string(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = [("777", "777"), (" 601", "<noncanonical>"), ("6\nx", "<noncanonical>")]
    for sudo, _ in cases:
        pmain.main(
            ["merge"],
            layout=world.lay,
            snapshot=world.snap,
            uid=world.me,
            env={"SUDO_UID": sudo},
        )
    pmain.main(
        ["publish-review"],
        layout=world.lay,
        snapshot=world.snap,
        uid=world.me,
        env={"SUDO_UID": str(EXEC)},
    )
    pmain.main(["merge"], layout=world.lay, snapshot=world.snap, uid=world.me, env={})
    lines = world.journal()
    assert [(x["sudo_uid"], x["reason"]) for x in lines] == [
        ("777", "sender_unknown"),
        ("<noncanonical>", "sender_unknown"),
        ("<noncanonical>", "sender_unknown"),
        ("601", "operation_not_allowed"),
        (None, "sender_unknown"),
    ]
    assert all(x["sender"] is None and x["request_id"] is None for x in lines)
    assert all(x["outcome"] == "refused" for x in lines)
    assert "6\\nx" not in (world.lay.publisher_dir / "journal.jsonl").read_text()
    assert world.files("ids") == []


def test_not_publisher_and_broken_config_are_not_journalled(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"SUDO_UID": str(EXEC)}
    pmain.main(
        ["merge"], layout=world.lay, snapshot=world.snap, uid=world.me + 1, env=env
    )
    (world.lay.etc / "publisher.toml").write_text("garbage = ")
    pmain.main(["merge"], layout=world.lay, snapshot=world.snap, uid=world.me, env=env)
    assert not (world.lay.publisher_dir / "journal.jsonl").exists()
    out = capsys.readouterr().out.splitlines()
    assert [json.loads(x)["reason"] for x in out] == [
        "not_publisher_uid",
        "publisher_config_invalid",
    ]


def test_missing_config_has_its_own_reason(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `publisher.toml` (roles setup has not run) is not a broken one."""
    (world.lay.etc / "publisher.toml").unlink()
    env = {"SUDO_UID": str(EXEC)}
    code = pmain.main(
        ["merge"], layout=world.lay, snapshot=world.snap, uid=world.me, env=env
    )
    resp = json.loads(capsys.readouterr().out)
    assert (code, resp["reason"]) == (2, "publisher_config_missing")
    assert "api_base" not in resp["evidence"]
    assert not (world.lay.publisher_dir / "journal.jsonl").exists()


def test_sender_refusal_carries_api_base(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"SUDO_UID": "4242"}
    pmain.main(["merge"], layout=world.lay, snapshot=world.snap, uid=world.me, env=env)
    resp = json.loads(capsys.readouterr().out)
    assert resp["reason"] == "sender_unknown"
    assert resp["evidence"]["api_base"] == "https://api.github.com"


def test_gid_must_be_the_publishers(
    world: World, no_stdin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"SUDO_UID": str(EXEC)}
    code = pmain.main(
        ["merge"],
        layout=world.lay,
        snapshot=world.snap,
        uid=world.me,
        gid=os.getgid() + 1,
        env=env,
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "not_publisher_uid"


def test_key_lock_is_held_for_the_whole_handler(
    world: World, table: dict[str, list[Context]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two identical requests at once: one handler call, the second answer is
    the first's result, replayed."""
    import multiprocessing as mp
    import time

    ctx = mp.get_context("fork")
    barrier, answers, count = ctx.Barrier(2, timeout=10), ctx.Queue(), ctx.Value("i", 0)

    def slow(c: Context) -> Response:
        c.record_intent({"step": 1})
        with count.get_lock():
            count.value += 1
        time.sleep(1.0)
        return Response("done", None, {"n": 1})

    install(table, "merge", slow)
    monkeypatch.setattr(
        pmain,
        "_answer",
        lambda resp: answers.put((resp.outcome, resp.evidence.get("replayed"))) or 0,
    )
    stdin = world.lay.root / "stdin"
    stdin.write_bytes(raw_of("merge"))

    def child() -> None:
        fd = os.open(stdin, os.O_RDONLY)
        barrier.wait()
        pmain.main(
            ["merge"],
            layout=world.lay,
            snapshot=world.snap,
            uid=world.me,
            env={"SUDO_UID": str(EXEC)},
            stdin_fd=fd,
        )

    procs = [ctx.Process(target=child) for _ in range(2)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    stuck = [p for p in procs if p.is_alive()]
    for proc in stuck:
        proc.terminate()
    assert not stuck
    got = sorted(str(answers.get(timeout=5)) for _ in range(2))
    assert count.value == 1
    assert got == ["('done', None)", "('done', True)"]


# --- fix round 2: R23, never `refused` with state -------------------------------


def crash_once(table: dict[str, list[Context]], world: World, capsys) -> None:  # noqa: ANN001
    install(table, "merge", crash_after_intent({"step": 1}))
    assert world.run("merge", capsys=capsys)[1].outcome == "unknown"


def test_busy_after_an_intent_is_unknown(
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crash_once(table, world, capsys)
    monkeypatch.setattr(store, "LOCK_WAIT", 0.2)
    from tests.df.publisher_world import make_req

    with store.reserve(world.lay, EXEC, "merge", make_req()):  # a live holder
        code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (3, "unknown", "in_flight")


def test_busy_without_state_is_unknown_in_flight(
    world: World,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.df.publisher_world import make_req

    monkeypatch.setattr(store, "LOCK_WAIT", 0.2)
    with store.reserve(world.lay, EXEC, "merge", make_req()):
        code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (3, "unknown", "in_flight")


def test_revoked_release_after_an_intent_is_unknown(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    crash_once(table, world, capsys)
    revoked = snapshot("rel-a", entry("rel-a", "revoked"))
    code, resp = world.run("merge", snap=revoked, capsys=capsys)
    assert code == 3
    assert resp.outcome == "unknown"
    assert (resp.reason or "").startswith("state_exists: release_revoked")


def test_unknown_run_after_an_intent_is_unknown(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    crash_once(table, world, capsys)
    (world.lay.runs_dir / "run-1.json").unlink()
    _, resp = world.run("merge", capsys=capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "state_exists: run_unknown")


def test_handler_refusal_after_intent_needs_the_flag(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    flag = {"verified": True}

    def refuses(ctx: Context) -> Response:
        ctx.record_intent({"step": 1})
        raise Refused("pr_closed", verified_not_done=flag["verified"])

    install(table, "merge", refuses)
    code, resp = world.run("merge", capsys=capsys)
    assert (code, resp.outcome, resp.reason) == (2, "refused", "pr_closed")
    flag["verified"] = False
    code, resp = world.run(
        "merge", raw_of("merge", request_id="req-00000077"), capsys=capsys
    )
    assert (code, resp.outcome, resp.reason) == (
        3,
        "unknown",
        "state_exists: pr_closed",
    )


def test_returned_refusal_after_intent_is_unknown(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    def refuses(ctx: Context) -> Response:
        ctx.record_intent({"step": 1})
        return Response("refused", "pr_closed")

    install(table, "merge", refuses)
    _, resp = world.run("merge", capsys=capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "state_exists: pr_closed")
    assert world.files("results") == []


def test_holder_without_an_intent_yet_gives_in_flight(
    world: World,
    table: dict[str, list[Context]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P4: the holder has not written its intent when the twin times out."""
    from tests.df.publisher_world import make_req

    calls = install(table, "merge", acting)
    monkeypatch.setattr(store, "LOCK_WAIT", 0.2)
    with store.reserve(world.lay, EXEC, "merge", make_req()) as held:
        code, resp = world.run("merge", capsys=capsys)
        assert not held.intent_path.exists()
    assert (code, resp.outcome, resp.reason) == (3, "unknown", "in_flight")
    assert calls == []


def test_unreadable_intents_directory_keeps_a_refusal_unknown(
    world: World, table: dict[str, list[Context]], capsys: pytest.CaptureFixture[str]
) -> None:
    crash_once(table, world, capsys)
    intents = world.lay.publisher_dir / "intents"
    intents.chmod(0)
    try:
        revoked = snapshot("rel-a", entry("rel-a", "revoked"))
        _, resp = world.run("merge", snap=revoked, capsys=capsys)
    finally:
        intents.chmod(0o700)
    assert resp.outcome == "unknown"
