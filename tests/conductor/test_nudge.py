"""nudge и pr_nudge (спека среза 1, §7.3–7.4)."""

from datetime import timedelta

from conductor.actions.common import wait_id, wait_refs
from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.markers import h1, make, render, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, comment, world

PERIOD = {"todo://a/goal|todo://b/b": ["p1", "2026-09-30T00:00:00Z"]}
D = timedelta(days=1)


def _nudges(records=None, now=NOW, **extra):
    extra.setdefault("edge_periods", PERIOD)
    result, inp = world(records=records if records is not None else REQUESTS, **extra)
    notes: list[dict] = []
    return plan_nudges(result, inp, BOT, now, None, notes), notes, result


def _marker(result, n: int):
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    return make("nudge", wait=wait_id(ref), p=h1("period", "p1"), n=str(n))


def test_first_nudge_to_producer_address() -> None:
    recs, _, result = _nudges()
    [rec] = recs
    assert rec.subject == "b#4" and rec.steps[0].mutation.repo == "own/b"
    assert rec.steps[0].event_key == render(_marker(result, 1))
    assert render(_marker(result, 1)) in rec.steps[0].mutation.text


def test_no_age_no_nudge() -> None:
    recs, notes, _ = _nudges(edge_periods={})
    assert recs == [] and any(n.get("reason") == "возраст неизвестен" for n in notes)


def test_recent_movement_blocks_first_nudge() -> None:
    moving = record(
        "b",
        4,
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=[comment("dev", "делаю", (NOW - D).strftime("%Y-%m-%dT%H:%M:%SZ"), 2)],
    )
    recs, _, _ = _nudges([REQUESTS[0], moving])
    assert recs == []


def test_bot_comments_are_not_movement_and_repeat_after_renudge() -> None:
    _, _, result = _nudges()
    first = with_marker("пинок", _marker(result, 1))
    at = (NOW - 8 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    thread = record(
        "b",
        4,
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=[comment(BOT, first, at, 1)],
    )
    recs, _, _ = _nudges([REQUESTS[0], thread])
    [rec] = recs
    assert render(_marker(result, 2)) in rec.steps[0].mutation.text  # n=2


def test_no_rank_no_nudge() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.bg @blocked_by:todo://b/b\n",
        "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
    }
    recs, notes, _ = _nudges(todos=todos)
    assert recs == [] and any(n.get("reason") == "нет ранга" for n in notes)


PR_TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}


def _pr(**fields):
    base = {
        "is_pr": True,
        "body": "@id:goal",
        "created_at": "2026-09-20T00:00:00Z",
        "ready_at": None,
        "last_commit_at": "2026-09-21T00:00:00Z",
        "reviews": [],
        "is_draft": False,
        "red_checks": [{"name": "test", "url": "https://ci/1"}],
        "ci": "red",
    }
    base.update(fields)
    return record("a", 7, **base)


def _pr_nudges(pr):
    result, inp = world(PR_TODOS, [pr])
    notes: list[dict] = []
    return plan_pr_nudges(result, inp, BOT, NOW, None, notes), notes


def test_red_pr_nudge_names_checks_without_log() -> None:
    [rec], _ = _pr_nudges(_pr())
    text = rec.steps[0].mutation.text
    assert "test" in text and "https://ci/1" in text and "лог не прочитан" in text
    assert rec.steps[0].mutation.number == 7


def test_draft_and_fresh_red_pr_are_not_nudged() -> None:
    recs, notes = _pr_nudges(_pr(is_draft=True))
    assert recs == [] and any(n.get("reason") == "драфт" for n in notes)
    recent = (NOW - D).strftime("%Y-%m-%dT%H:%M:%SZ")
    recs, _ = _pr_nudges(_pr(ready_at=recent, last_commit_at=recent))
    assert recs == []  # красный CI сам пинка не даёт


SECOND_B = record("b", 5, labels=["inbox"], body="slug: b\nfrom: devtools\n")


def _closed_first(comments):
    return record(
        "b",
        4,
        state="closed",
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=comments,
    )


def test_prior_nudge_in_closed_address_keeps_interval() -> None:
    """Решение владельца 4: смена адреса не сбрасывает историю и интервал."""
    _, _, result = _nudges()
    at = (NOW - 2 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = comment(BOT, with_marker("пинок", _marker(result, 1)), at, 1)
    recs, _, _ = _nudges([REQUESTS[0], _closed_first([first]), SECOND_B])
    assert recs == []  # пинок был 2 дня назад в закрытом b#4: renudge 7 дней


def test_prior_nudge_numbering_continues_in_new_address() -> None:
    _, _, result = _nudges()
    at = (NOW - 8 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = comment(BOT, with_marker("пинок", _marker(result, 1)), at, 1)
    [rec] = _nudges([REQUESTS[0], _closed_first([first]), SECOND_B])[0]
    assert rec.subject == "b#5"
    assert render(_marker(result, 2)) in rec.steps[0].mutation.text


def test_all_addresses_closed_is_named() -> None:
    """Решение владельца 5: треды есть, но все закрыты — не «нет адреса»."""
    recs, notes, _ = _nudges([REQUESTS[0], _closed_first([])])
    assert recs == []
    assert any(n.get("reason") == "нет открытого адреса доставки" for n in notes)
