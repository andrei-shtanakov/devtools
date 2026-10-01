"""Общие помощники планировщиков (спека среза 1, §5.7, §7.1, §7.3)."""

from datetime import timedelta

from conductor.actions.common import (
    addresses,
    due,
    fact_of,
    movement,
    target,
    wait_id,
    wait_refs,
)
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, TODOS, comment, world

D = timedelta(days=1)


def test_wait_refs_and_ids() -> None:
    result, _ = world()
    [(ref, wait)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    assert (ref.src, ref.raw, ref.consumer) == (
        "todo://a/goal",
        "todo://b/b",
        "todo://a/goal",
    )
    assert wait.verdict == "pending"
    assert wait_id(ref) == wait_id(ref) and wait_id(ref).startswith("h1-")


def test_addresses_glued_requests() -> None:
    result, _ = world()
    assert addresses(result.graph, "todo://a/goal") == ["a#3"]
    assert addresses(result.graph, "todo://b/b") == ["b#4"]


def test_fact_of_item_issue_trigger() -> None:
    todos = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
    result, inp = world(todos, done_facts={"todo://b/b": "sha1"})
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    assert fact_of(result.graph, inp, ref) == "item:sha1"
    trig = {
        "a": (
            "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            '@trigger:"date>=2026-01-01"\n'
        )
    }
    result, inp = world(trig, [])
    [(ref, _)] = wait_refs(result.graph, result.waits)
    assert ref.prereq is None and fact_of(result.graph, inp, ref) == "date:2026-01-01"


def test_movement_excludes_bot_and_updated_at() -> None:
    recs = [
        REQUESTS[0],
        record(
            "b",
            4,
            labels=["inbox"],
            body="slug: b\nfrom: devtools\n",
            updated_at="2026-10-09T00:00:00Z",
            comments=[
                comment(BOT, "пинок", "2026-10-08T00:00:00Z", 1),
                comment("dev", "работаю", "2026-10-05T00:00:00Z", 2),
            ],
        ),
    ]
    result, inp = world(records=recs)
    moved = movement(result.graph, inp, "todo://b/b", BOT)
    assert moved is not None and moved.isoformat().startswith("2026-10-05")


def test_target_maps_github_name() -> None:
    _, inp = world()
    assert target(inp, "ecosystem-kb#7") == ("own/prograph-vault", 7)
    assert target(inp, "a!9") == ("own/a", 9)


def test_due_branches() -> None:
    stale, renudge = 3 * D, 7 * D
    start = NOW - 10 * D
    assert due(NOW, start, NOW - 4 * D, None, [], stale, renudge) == 1
    assert (
        due(NOW, NOW - 2 * D, NOW - 2 * D, None, [], stale, renudge) is None
    )  # молодо
    assert (
        due(NOW, start, NOW - 1 * D, NOW - 1 * D, [], stale, renudge) is None
    )  # тишины мало
    prior = [(1, NOW - 8 * D)]
    assert (
        due(NOW, start, NOW - 9 * D, None, prior, stale, renudge) == 2
    )  # движения не было
    assert (
        due(NOW, start, NOW - 2 * D, NOW - 2 * D, prior, stale, renudge) is None
    )  # было, тишина < stale
    assert due(NOW, start, NOW - 4 * D, NOW - 4 * D, prior, stale, renudge) == 2
    assert (
        due(NOW, start, NOW - 9 * D, None, [(1, NOW - 6 * D)], stale, renudge) is None
    )
