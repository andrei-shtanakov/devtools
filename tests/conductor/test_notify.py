"""notify_satisfied (спека среза 1, §7.2)."""

from conductor.actions.common import wait_id, wait_refs
from conductor.actions.notify import plan_notify
from conductor.markers import h1, make, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, REQUESTS, TODOS, comment, world

DONE = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
SECOND = record("a", 6, labels=["inbox"], body="slug: goal\nfrom: devtools\n")


def _plan(todos=DONE, records=None, **extra):
    result, inp = world(todos, records if records is not None else REQUESTS, **extra)
    notes: list[dict] = []
    return plan_notify(result, inp, BOT, None, notes), notes, result


def test_one_record_per_address_with_stable_key() -> None:
    recs, _, _ = _plan(records=[*REQUESTS, SECOND], done_facts={"todo://b/b": "s1"})
    assert [r.subject for r in recs] == ["a#3", "a#6"]
    keys = {r.steps[0].event_key for r in recs}
    assert len(keys) == 1  # одна тройка (ожидание, доказательство) — разные адреса


def test_existing_marker_no_duplicate_and_new_fact_new_marker() -> None:
    _, _, result = _plan(done_facts={"todo://b/b": "s1"})
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    marker = make("sat", wait=wait_id(ref), evidence=h1("evidence", "item:s1"))
    delivered = record(
        "a",
        3,
        labels=["inbox"],
        body="slug: goal\nfrom: devtools\n",
        comments=[comment(BOT, with_marker("x", marker), "2026-10-01T00:00:00Z", 1)],
    )
    again, _, _ = _plan(
        records=[delivered, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    assert again == []
    renewed, _, _ = _plan(
        records=[delivered, REQUESTS[1]], done_facts={"todo://b/b": "s2"}
    )
    assert len(renewed) == 1


def test_edited_marker_is_not_delivery() -> None:
    recs, _, _ = _plan(done_facts={"todo://b/b": "s1"})
    marker_text = recs[0].steps[0].mutation.text
    edited = record(
        "a",
        3,
        labels=["inbox"],
        body="slug: goal\nfrom: devtools\n",
        comments=[
            comment(
                BOT,
                marker_text,
                "2026-10-01T00:00:00Z",
                1,
                updated="2026-10-02T00:00:00Z",
            )
        ],
    )
    again, notes, _ = _plan(
        records=[edited, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    assert (
        len(again) == 1
        and {
            "finding": "MK-EDITED",
            "action": "notify_satisfied",
            "thread": "a#3",
        }
        in notes
    )


def test_todo_only_consumer_reports_channel() -> None:
    recs, notes, _ = _plan(records=[REQUESTS[1]], done_facts={"todo://b/b": "s1"})
    assert recs == [] and any(
        n.get("reason") == "канал доставки — срез 1b" for n in notes
    )


def test_unknown_fact_no_write() -> None:
    recs, notes, _ = _plan()
    assert recs == [] and any(
        n.get("reason") == "факт выполнения неизвестен" for n in notes
    )


def test_closed_addresses_are_not_channel_1b() -> None:
    """Решение владельца 5: все заявки закрыты — «нет открытого адреса»."""
    closed = record(
        "a", 3, state="closed", labels=["inbox"], body="slug: goal\nfrom: devtools\n"
    )
    recs, notes, _ = _plan(
        records=[closed, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    reasons = {n.get("reason") for n in notes}
    assert recs == [] and "нет открытого адреса доставки" in reasons
    assert "канал доставки — срез 1b" not in reasons


def test_issue_prerequisite_fact_is_closed_event_id() -> None:
    """Решение владельца 2: fact_id issue — id события closed, не closedAt."""
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:b#7\n"
    }
    closed = record(
        "b",
        7,
        state="closed",
        state_reason="completed",
        closed_at="2026-10-01T00:00:00Z",
    )
    goal_req = record("a", 3, labels=["inbox"], body="slug: goal\nfrom: devtools\n")
    no_event, notes, _ = _plan(todos, [goal_req, closed])
    assert no_event == [] and any(
        n.get("reason") == "факт выполнения неизвестен" for n in notes
    )
    [rec] = _plan(todos, [goal_req, closed], closed_events={"b#7": "CE_1"})[0]
    [again] = _plan(todos, [goal_req, closed], closed_events={"b#7": "CE_2"})[0]
    assert rec.steps[0].event_key != again.steps[0].event_key  # новое закрытие
    assert "closed:CE_1" in rec.steps[0].mutation.text
