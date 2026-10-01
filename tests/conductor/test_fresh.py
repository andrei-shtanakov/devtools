"""Свежие чтения (спека среза 1, §5.1 шаг 8, §5.3)."""

from conductor.fresh import FreshReader, comment_check, open_check, pr_need
from conductor.gh_write import Mutation
from conductor.markers import h1, make, render, with_marker
from tests.conductor.fake_app import FakeClient, ok

BOT = "conductor[bot]"
M = make("q", id=h1("q", "a"))
COMMENT = Mutation("comment", "o/r", 1, text=with_marker("x", M))


def test_marker_found_absent_and_unreadable() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert fresh.marker_found("o/r", 1, render(M), BOT) is False
    assert comment_check(fresh, COMMENT, BOT) is None
    client.add_comment("o/r", 1, BOT, with_marker("x", M))
    assert fresh.marker_found("o/r", 1, render(M), BOT) is True
    assert comment_check(fresh, COMMENT, BOT) == "маркер уже есть"
    client.override[("GET", "/repos/o/r/issues/1/comments?per_page=100&page=1")] = ok(
        {"x": 1}
    )
    assert fresh.marker_found("o/r", 1, render(M), BOT) is None
    assert comment_check(fresh, COMMENT, BOT) == "тред не прочитан"


def test_foreign_or_edited_marker_is_not_found() -> None:
    client = FakeClient()
    client.add_comment("o/r", 1, "own", with_marker("x", M))
    client.add_comment("o/r", 1, BOT, with_marker("x", M), updated_at="2027-01-01")
    assert FreshReader(client).marker_found("o/r", 1, render(M), BOT) is False


def test_open_check_and_last_event_and_pinned() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert open_check(fresh, "o/r", 1) is None
    client.issue("o/r", 1, state="closed")
    assert open_check(fresh, "o/r", 1) == "субъект закрыт"
    assert fresh.last_event("o/r", 1, "reopened") == ""
    client.issue("o/r", 1, timeline=[{"event": "reopened", "node_id": "RE_1"}])
    assert fresh.last_event("o/r", 1, "reopened") == "RE_1"
    assert fresh.pinned("o/r", 1) is False
    client.issue("o/r", 1, pinned=True)
    assert fresh.pinned("o/r", 1) is True
    assert client.sent == []  # чтения GraphQL — не мутации


def test_queue_found_and_git_without_reader() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert fresh.queue_found("o/ws", BOT) is False
    client.issue("o/ws", 7, labels=["owner-queue"], user=BOT, state="closed")
    assert fresh.queue_found("o/ws", BOT) is True
    assert fresh.git("a") is None


def test_pr_facts_and_need() -> None:
    client = FakeClient()
    red = {
        "__typename": "CheckRun",
        "name": "t",
        "conclusion": "FAILURE",
        "detailsUrl": "u",
    }
    client.pull("o/r", 7, head={"sha": "h"}, checks=[red], closing=["o/r#1"])
    fresh = FreshReader(client)
    facts = fresh.pr_facts("o/r", 7)
    assert facts is not None and pr_need(facts) == "fix_pr"
    assert facts["red"] == [{"name": "t", "url": "u"}] and facts["closing"] == {"o/r#1"}
    client.pull("o/r", 7, checks=[{**red, "conclusion": "SUCCESS"}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "review"
    client.pull("o/r", 7, reviews=[{"state": "APPROVED", "commit": {"oid": "h"}}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "merge"
    client.pull("o/r", 7, checks=[{**red, "conclusion": None, "status": "IN_PROGRESS"}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "wait_ci"
    assert fresh.pr_facts("o/r", 8) is None  # PR нет
    assert client.sent == []


def test_pr_facts_truncated_contexts_unread() -> None:
    client = FakeClient()
    client.pull("o/r", 7)
    result = client._pr_graphql({"o": "o", "n": "r", "k": 7})
    data = result.response.json() if result.response else {}
    pr = data["data"]["repository"]["pullRequest"]
    contexts = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
    contexts["totalCount"] = 101
    client.override[("POST", "/graphql")] = ok(data)
    assert FreshReader(client).pr_facts("o/r", 7) is None
