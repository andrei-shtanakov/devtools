"""Очередь владельца и вопросы (спека среза 1, §5.4, §6.1, §7.5)."""

from conductor.actions.owner_queue import (
    op_failure_questions,
    plan_owner_queue,
    projection,
    queue_issue,
    queue_questions,
)
from conductor.actions.shipped import shipped_state
from conductor.markers import h1, make, with_marker
from conductor.opstate import Episode
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, comment, world

UMB = "own/ai-orchestrators-workspace"
Q1 = {
    "question_id": "11111111",
    "subject": "a#1",
    "reason": "cancelled",
    "evidence": "e",
    "question": "a#1: предпосылка отменена?",
    "options": ["drop-wait", "keep"],
    "default": "keep",
}


def _queue(
    state: str = "open", body: str = "", pinned: bool = True, comments=None, author=BOT
):
    return record(
        "ai-orchestrators-workspace",
        9,
        state=state,
        body=body,
        author=author,
        labels=["owner-queue"],
        comments=comments or [],
        pinned=pinned,
        repo_full=UMB,
    )


def _plan(questions, queue_records):
    _, inp = world(queue_records=queue_records)
    notes: list[dict] = []
    recs = plan_owner_queue(questions, [], inp, UMB, "own", BOT, None, notes)
    return recs, notes


def ops(recs) -> list[list[str]]:
    return [[s.mutation.op for s in r.steps] for r in recs]


def flags(recs) -> list[bool]:
    return [s.optional for r in recs for s in r.steps]


def test_no_queue_no_questions_nothing() -> None:
    assert _plan([], []) == ([], [])


def test_create_pin_and_question_comments() -> None:
    recs, _ = _plan([Q1], [])
    # одна запись: сбой создания блокирует вопросы, сбой закрепления — нет
    assert ops(recs) == [["create", "pin", "comment"]]
    assert flags(recs) == [False, True, False]
    create, pin, question = (s.mutation for s in recs[0].steps)
    assert create.labels == ("owner-queue",) and create.repo == UMB
    assert pin.number is None and question.number is None  # номер — из создания
    assert "Q-11111111" in question.text and "@own" in question.text
    assert recs[0].steps[2].revision == "11111111"


def test_open_queue_rewrites_body_pins_and_skips_known_questions() -> None:
    marker = make("q", id=h1("q", "11111111"))
    asked = comment(BOT, with_marker("старый", marker), "2026-10-01T00:00:00Z", 1)
    recs, _ = _plan([Q1], [_queue(body="старое", pinned=False, comments=[asked])])
    assert ops(recs) == [["body", "pin"]]


def test_body_equal_projection_no_write() -> None:
    text = projection([Q1])
    body = with_marker(text, make("queue", projection=h1("projection", text)))
    marker = make("q", id=h1("q", "11111111"))
    asked = comment(BOT, with_marker("x", marker), "2026-10-01T00:00:00Z", 1)
    recs, _ = _plan([Q1], [_queue(body=body, comments=[asked])])
    assert recs == []


def test_closed_queue_is_not_reopened() -> None:
    recs, notes = _plan([Q1], [_queue(state="closed")])
    assert recs == [] and notes == [{"finding": "GR-QUEUE-CLOSED"}]


def test_two_queues_are_ambiguous_and_foreign_queue_ignored() -> None:
    recs, notes = _plan([Q1], [_queue(), _queue()])
    assert recs == [] and notes == [{"finding": "GR-QUEUE-AMBIGUOUS"}]
    _, inp = world(queue_records=[_queue(author="someone")])
    assert queue_issue(inp, BOT) == (None, None)


def test_op_failure_question_is_stable_per_episode() -> None:
    ep = Episode(
        "m1",
        "r1",
        ("r1", "r2"),
        "500",
        ("t1", "t2"),
        {"op": "comment", "target": "own/a#1"},
    )
    longer = Episode(
        "m1", "r1", ("r1", "r2", "r3"), "500", ("t1", "t2", "t3"), ep.detail
    )
    [q1], [q2] = op_failure_questions([ep]), op_failure_questions([longer])
    assert q1["question_id"] == q2["question_id"] and q1["reason"] == "op-failure"
    new_episode = Episode("m1", "r7", ("r7", "r8"), "500", ("t7", "t8"), ep.detail)
    assert op_failure_questions([new_episode])[0]["question_id"] != q1["question_id"]


def test_shipped_question_carries_period_and_basis_a_drops_it() -> None:
    todos = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}
    issue = record("a", 1, title="a#1")
    pr = record("a", 5, is_pr=True, state="closed", merged=True, closing_refs=["a#1"])
    reopened = {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": "RE_1",
            "period_start": "2026-09-20T00:00:00Z",
            "closed_by": [
                {
                    "repo": "a",
                    "number": 5,
                    "merged": True,
                    "merged_at": "2026-09-10T00:00:00Z",
                    "merge_sha": "m5",
                    "base_is_default": True,
                }
            ],
        }
    }
    _, inp = world(todos, [issue, pr], issue_extras=reopened)
    state = shipped_state(inp, "a#1")
    assert (
        state is not None
        and state.basis_a is None
        and state.old_basis
        and state.period == "RE_1"
    )
    fresh = {
        "a#1": {
            **reopened["a#1"],
            "period": "0",
            "period_start": "2026-09-01T00:00:00Z",
        }
    }
    result2, inp2 = world(todos, [issue, pr], issue_extras=fresh)
    assert shipped_state(inp2, "a#1").basis_a is not None
    assert all(
        q["reason"] != "GR-SHIPPED-OPEN" for q in queue_questions(result2, inp2, [])
    )
