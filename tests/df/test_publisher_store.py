"""Reservation of `request_id`, intent/result, journal (stage 2a, Task 7)."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from df import fsutil
from df.paths import Layout
from df.publisher import request, store
from df.publisher.request import Refused, Response
from tests.df.publisher_world import RID, SHA_B, make_req, tmp_layout

SENDER = 601
JOIN = 30


@pytest.fixture()
def lay(tmp_path: Path) -> Layout:
    return tmp_layout(tmp_path)


def ids_of(lay: Layout) -> list[str]:
    return sorted(p.name for p in (lay.publisher_dir / "ids").iterdir())


# --- the binding ----------------------------------------------------------------


def test_reserve_binds_the_id_privately(lay: Layout) -> None:
    req = make_req()
    with store.reserve(lay, SENDER, "merge", req) as res:
        assert res.request_id == RID
    record = lay.publisher_dir / "ids" / RID
    assert json.loads(record.read_text()) == {
        "sender": SENDER,
        "operation": "merge",
        "digest": request.digest(req),
    }
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert record.stat().st_nlink == 1
    assert ids_of(lay) == [RID]
    for name in ("ids", "keys", "intents", "results"):
        mode = (lay.publisher_dir / name).stat().st_mode
        assert stat.S_IMODE(mode) == 0o700


def test_reserve_same_digest_resumes(lay: Layout) -> None:
    req = make_req()
    with store.reserve(lay, SENDER, "merge", req) as first:
        key = first.key
    with store.reserve(lay, SENDER, "merge", make_req()) as again:
        assert again.key == key
    assert ids_of(lay) == [RID]


BINDINGS = [
    ("other sender", 602, "merge", {}),
    ("other operation", SENDER, "publish-review", {"decision": "comment", "body": ""}),
    ("other digest", SENDER, "merge", {"head_sha": SHA_B}),
]


@pytest.mark.parametrize(("name", "sender", "op", "over"), BINDINGS)
def test_reserve_conflict_refused(
    lay: Layout, name: str, sender: int, op: str, over: dict[str, object]
) -> None:
    store.reserve(lay, SENDER, "merge", make_req()).close()
    with pytest.raises(Refused) as info:
        store.reserve(lay, sender, op, make_req(op, **over))
    assert info.value.reason == "request_id_reused", name
    assert ids_of(lay) == [RID]


def test_reserve_unreadable_binding_is_reused(lay: Layout) -> None:
    store.reserve(lay, SENDER, "merge", make_req()).close()
    (lay.publisher_dir / "ids" / RID).write_text("not json")
    with pytest.raises(Refused) as info:
        store.reserve(lay, SENDER, "merge", make_req())
    assert info.value.reason == "request_id_reused"


def test_reserve_failure_releases_the_global_lock(lay: Layout) -> None:
    store.reserve(lay, SENDER, "merge", make_req()).close()
    with pytest.raises(Refused):
        store.reserve(lay, 602, "merge", make_req())
    store.reserve(lay, SENDER, "merge", make_req(request_id="req-00000002")).close()


def test_reserve_leaves_no_temporary_file(lay: Layout) -> None:
    with store.reserve(lay, SENDER, "merge", make_req()):
        pass
    with pytest.raises(Refused):
        store.reserve(lay, 602, "merge", make_req())
    assert [n for n in ids_of(lay) if n.startswith(".")] == []


# --- the order of §5.6 ----------------------------------------------------------


def test_reserve_order(lay: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = lay.publisher_dir / "ids"
    ids.mkdir()
    (ids / ".old.123.tmp").write_text("dead process")
    log: list[str] = []
    real_lock, real_unlock = store._lock_file, store._unlock  # noqa: SLF001
    real_link, real_remove = os.link, fsutil.remove_durably
    real_write, real_sync = fsutil.write_atomic, fsutil.sync_dir

    def lock(path: Path) -> int:
        fd = real_lock(path)
        log.append(f"lock:{path.name}")
        return fd

    def unlock(fd: int) -> None:
        log.append("unlock")
        real_unlock(fd)

    def link(src: object, dst: object, **kw: object) -> None:
        log.append("link")
        real_link(src, dst, **kw)  # type: ignore[arg-type]

    def remove(path: Path) -> None:
        log.append(f"unlink:{path.name}")
        real_remove(path)

    def write(path: Path, text: str, mode: int = 0o644) -> None:
        log.append(f"write:{path.name}")
        real_write(path, text, mode)

    def sync(target: int | Path) -> fsutil.SyncNote:
        if "link" in log and "sync" not in log:
            log.append("sync")
        return real_sync(target)

    monkeypatch.setattr(store, "_lock_file", lock)
    monkeypatch.setattr(store, "_unlock", unlock)
    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(fsutil, "remove_durably", remove)
    monkeypatch.setattr(fsutil, "write_atomic", write)
    monkeypatch.setattr(fsutil, "sync_dir", sync)
    req = make_req()
    store.reserve(lay, SENDER, "merge", req).close()
    tmp = f".{RID}.{os.getpid()}.tmp"
    key = store.idempotency_key(SENDER, "merge", request.digest(req))
    assert log == [
        "lock:ids.lock",
        "unlink:.old.123.tmp",
        f"write:{tmp}",
        "link",
        "sync",
        f"unlink:{tmp}",
        "unlock",
        f"lock:{key}.lock",
    ]


def test_idempotency_key_is_the_binding() -> None:
    base = store.idempotency_key(SENDER, "merge", "sha256:x")
    assert len(base) == 64
    assert base != store.idempotency_key(602, "merge", "sha256:x")
    assert base != store.idempotency_key(SENDER, "publish-review", "sha256:x")
    assert base != store.idempotency_key(SENDER, "merge", "sha256:y")


# --- processes: conflict, crash, cleanup under the lock ------------------------


def _ctx() -> mp.context.BaseContext:
    return mp.get_context("fork")


def run_children(targets: list[Callable[[], None]]) -> list[mp.Process]:
    ctx = _ctx()
    procs = [ctx.Process(target=t) for t in targets]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(JOIN)
    stuck = [p for p in procs if p.is_alive()]
    for proc in stuck:
        proc.terminate()
        proc.join(5)
    assert not stuck, "a child did not finish"
    return procs


def test_reserve_concurrent_conflict(lay: Layout) -> None:
    ctx = _ctx()
    barrier, out = ctx.Barrier(2, timeout=10), ctx.Queue()

    def racer(head: str) -> Callable[[], None]:
        def run() -> None:
            req = make_req(head_sha=head)
            barrier.wait()
            try:
                with store.reserve(lay, SENDER, "merge", req):
                    out.put("ok")
            except Refused as exc:
                out.put(exc.reason)

        return run

    run_children([racer("a" * 40), racer("c" * 40)])
    answers = sorted(out.get(timeout=5) for _ in range(2))
    assert answers == ["ok", "request_id_reused"]
    assert ids_of(lay) == [RID]


def test_reserve_concurrent_identical_both_pass(lay: Layout) -> None:
    ctx = _ctx()
    barrier, out = ctx.Barrier(2, timeout=10), ctx.Queue()

    def racer() -> None:
        barrier.wait()
        with store.reserve(lay, SENDER, "merge", make_req()) as res:
            out.put(res.key)
            time.sleep(0.2)  # the second waits on the key's lock

    run_children([racer, racer])
    keys = {out.get(timeout=5) for _ in range(2)}
    assert len(keys) == 1


def test_reserve_crash_leaves_no_partial(lay: Layout) -> None:
    def dies_between_fsync_and_link() -> None:
        store._before_link = lambda _tmp: os._exit(0)  # type: ignore[assignment]  # noqa: SLF001
        store.reserve(lay, SENDER, "merge", make_req())

    (proc,) = run_children([dies_between_fsync_and_link])
    left = lay.publisher_dir / "ids" / f".{RID}.{proc.pid}.tmp"
    assert left.exists()
    assert not (lay.publisher_dir / "ids" / RID).exists()
    # the next reserve sweeps it; a different content is not "reused"
    with store.reserve(lay, 602, "merge", make_req(head_sha=SHA_B)):
        pass
    assert ids_of(lay) == [RID]


def test_cleanup_does_not_race_active_reserve(lay: Layout) -> None:
    ctx = _ctx()
    a_waits, b_started, out = ctx.Event(), ctx.Event(), ctx.Queue()

    def process_a() -> None:
        def pause(_tmp: Path) -> None:
            a_waits.set()
            assert b_started.wait(10)
            time.sleep(0.5)  # B is at the lock, A's temporary file is on disk

        store._before_link = pause  # type: ignore[assignment]  # noqa: SLF001
        try:
            with store.reserve(lay, SENDER, "merge", make_req()):
                out.put(("A", "ok"))
        except Exception as exc:  # noqa: BLE001
            out.put(("A", f"{type(exc).__name__}: {exc}"))

    def process_b() -> None:
        assert a_waits.wait(10)
        b_started.set()
        try:
            with store.reserve(lay, 602, "merge", make_req(head_sha=SHA_B)):
                out.put(("B", "ok"))
        except Refused as exc:
            out.put(("B", exc.reason))

    run_children([process_a, process_b])
    answers = dict(out.get(timeout=5) for _ in range(2))
    assert answers == {"A": "ok", "B": "request_id_reused"}


def test_sweep_leaves_a_record(lay: Layout) -> None:
    store.reserve(lay, SENDER, "merge", make_req()).close()
    (lay.publisher_dir / "ids" / ".dead.99.tmp").write_text("x")
    store.reserve(lay, SENDER, "merge", make_req(request_id="req-00000002")).close()
    assert ids_of(lay) == [RID, "req-00000002"]


# --- intent, result, replay -----------------------------------------------------


def test_intent_roundtrip_is_durable_and_private(lay: Layout) -> None:
    with store.reserve(lay, SENDER, "merge", make_req()) as res:
        assert store.read_intent(res) is None
        store.intent(res, {"step": "merge", "head": "x"})
        assert store.read_intent(res) == {"step": "merge", "head": "x"}
        assert stat.S_IMODE(res.intent_path.stat().st_mode) == 0o600
    assert res.intent_path.name == f"{res.key}.json"


def test_result_replays_for_final_outcomes(lay: Layout) -> None:
    for outcome in ("done", "refused"):
        req = make_req(request_id=f"req-{outcome}-0001")
        with store.reserve(lay, SENDER, "merge", req) as res:
            assert store.replay(res) is None
            resp = Response(outcome, "r", {"n": 1}, {"e": 2})  # type: ignore[arg-type]
            assert store.result(res, resp) is True
        with store.reserve(lay, SENDER, "merge", req) as again:
            assert store.replay(again) == resp


@pytest.mark.parametrize("outcome", ["unknown", "stale_snapshot"])
def test_unknown_is_not_saved(lay: Layout, outcome: str) -> None:
    with store.reserve(lay, SENDER, "merge", make_req()) as res:
        assert store.result(res, Response(outcome)) is False  # type: ignore[arg-type]
        assert not res.result_path.exists()
        assert store.replay(res) is None


@pytest.mark.parametrize("op", sorted(store.NOT_REPLAYED))
def test_computed_operations_are_not_replayed(lay: Layout, op: str) -> None:
    req = make_req(op)
    with store.reserve(lay, SENDER, op, req) as res:
        store.result(res, Response("done", None, {"state": "clear"}))
        assert res.result_path.exists()
        assert store.replay(res) is None


def test_replay_of_a_damaged_result_raises(lay: Layout) -> None:
    with store.reserve(lay, SENDER, "merge", make_req()) as res:
        res.result_path.write_text("{}")
        with pytest.raises(request.BadResponse):
            store.replay(res)


def test_reservation_close_is_idempotent_and_unlocks(lay: Layout) -> None:
    res = store.reserve(lay, SENDER, "merge", make_req())
    res.close()
    res.close()
    store.reserve(lay, SENDER, "merge", make_req()).close()


# --- journal --------------------------------------------------------------------


def test_journal_line_redacts_the_token() -> None:
    resp = Response("done", None, {"token": "ghs_SECRET", "nested": [{"token": "t2"}]})
    line = store.journal_line(
        SENDER, "read-token", make_req("read-token"), "sha256:x", resp
    )
    assert "ghs_SECRET" not in line
    assert "t2" not in line
    assert json.loads(line)["result"]["token"] == "<redacted>"
    assert json.loads(line)["result"]["nested"] == [{"token": "<redacted>"}]


def test_journal_line_fields() -> None:
    req = make_req()
    line = store.journal_line(
        SENDER, "merge", req, "sha256:x", Response("refused", "r")
    )
    record = json.loads(line)
    assert set(record) == {
        "ts",
        "sender",
        "operation",
        "request_id",
        "run_id",
        "digest",
        "outcome",
        "reason",
        "result",
    }
    assert (record["sender"], record["operation"]) == (SENDER, "merge")
    assert (record["request_id"], record["run_id"]) == (RID, "run-1")
    assert (record["outcome"], record["reason"]) == ("refused", "r")
    assert line.endswith("\n")
    assert "response_nonce" not in line


def test_journal_appends_lines_privately(lay: Layout) -> None:
    store.journal(lay, "one\n")
    store.journal(lay, "two\n")
    path = lay.publisher_dir / "journal.jsonl"
    assert path.read_text() == "one\ntwo\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
