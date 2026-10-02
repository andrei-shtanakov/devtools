"""close_shipped (спека среза 1, §7.5)."""

from conductor.actions.close import plan_close
from conductor.markers import h1, make, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, comment, world

TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:a#1\n"}


def _extras(
    period: str = "0",
    start: str = "2026-09-01T00:00:00Z",
    merged_at: str = "2026-09-10T00:00:00Z",
):
    return {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": period,
            "period_start": start,
            "closed_by": [
                {
                    "repo": "a",
                    "number": 5,
                    "merged": True,
                    "merged_at": merged_at,
                    "merge_sha": "m5",
                    "base_is_default": True,
                }
            ],
        }
    }


def _plan(issue=None, answers=None, **extra):
    result, inp = world(TODOS, [issue or record("a", 1)], **extra)
    notes: list[dict] = []
    return plan_close(result, inp, BOT, answers or {}, None, notes), notes


def ops(recs) -> list[list[str]]:
    return [[s.mutation.op for s in r.steps] for r in recs]


def test_basis_a_comment_then_close() -> None:
    recs, _ = _plan(issue_extras=_extras())
    assert ops(recs) == [["comment", "close"]]
    assert "m5" in recs[0].steps[0].mutation.text


def test_marker_present_only_close() -> None:
    marker = make(
        "close",
        node=h1("node", "a#1"),
        period=h1("period", "0"),
        evidence=h1("evidence", "merged:m5"),
    )
    issue = record(
        "a",
        1,
        comments=[comment(BOT, with_marker("x", marker), "2026-09-11T00:00:00Z", 1)],
    )
    recs, _ = _plan(issue, issue_extras=_extras())
    assert ops(recs) == [["close"]]


def test_marker_of_other_evidence_does_not_count() -> None:
    other = make(
        "close",
        node=h1("node", "a#1"),
        period=h1("period", "0"),
        evidence=h1("evidence", "merged:mX"),
    )
    issue = record(
        "a",
        1,
        comments=[comment(BOT, with_marker("x", other), "2026-09-11T00:00:00Z", 1)],
    )
    recs, _ = _plan(issue, issue_extras=_extras())
    assert ops(recs) == [["comment", "close"]]


def test_reopened_after_any_close_needs_answer() -> None:
    reopened = _extras(period="RE_1", start="2026-09-20T00:00:00Z")
    recs, notes = _plan(issue_extras=reopened)
    assert recs == [] and {"finding": "GR-REOPENED", "subject": "a#1"} in notes
    q = {"question_id": "abcd1234", "evidence": "reopened|period:RE_1"}
    recs, _ = _plan(issue_extras=reopened, answers={"a#1": q})
    assert (
        ops(recs) == [["comment", "close"]]
        and "Q-abcd1234" in recs[0].steps[0].mutation.text
    )


def test_pr_merged_after_reopen_is_new_basis() -> None:
    later = _extras(
        period="RE_1", start="2026-09-20T00:00:00Z", merged_at="2026-09-25T00:00:00Z"
    )
    recs, _ = _plan(issue_extras=later)
    assert ops(recs) == [["comment", "close"]]


def test_unranked_issue_is_not_closed() -> None:
    result, inp = world(
        {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.bg @blocked_by:a#1\n"},
        [record("a", 1)],
        issue_extras=_extras(),
    )
    assert plan_close(result, inp, BOT, {}, None, []) == []
