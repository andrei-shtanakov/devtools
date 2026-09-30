"""Находки независимого ревью рубежей 1–2 реализации (2026-09-30)."""

from conductor.inputs import RepoTodo
from conductor.snapshot import evaluate, owner_questions
from tests.conductor.fixtures import inputs, record

GOAL = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "


def _codes(result):
    return {f.code for f in result.findings}


def _wait(result, consumer):
    return next(w for w in result.waits if w.consumer == consumer)


# недочитанный источник — не «предпосылки нет» (рубеж 1 C1, рубеж 2 I2)


def test_prerequisite_in_unread_todo_is_unread_not_missing() -> None:
    inp = inputs({"a": GOAL + "@blocked_by:todo://b/y\n"})
    inp.todos = [
        t if t.repo != "b" else RepoTodo("b", None, None, "error", "fetch failed")
        for t in inp.todos
    ]
    result = evaluate(inp, 0)
    assert _wait(result, "todo://a/goal").reason == "unread"
    assert owner_questions(result) == []
    assert "GR-DANGLING-WAIT" not in _codes(result)


def test_issue_prerequisite_with_github_unread_is_unread_not_missing() -> None:
    result = evaluate(inputs({"a": GOAL + "@blocked_by:b#5\n"}, gh_state="error"), 0)
    assert _wait(result, "todo://a/goal").reason == "unread"
    assert owner_questions(result) == []


def test_request_from_unread_sender_repo_is_not_an_orphan() -> None:
    inp = inputs(
        {"b": "- [ ] y @owner:github:own @id:y\n"},
        [record("b", 5, body="slug: y\nfrom: a#x\n", labels=["inbox"])],
    )
    inp.todos = [
        t if t.repo != "a" else RepoTodo("a", None, None, "error", "no clone")
        for t in inp.todos
    ]
    assert "GR-ORPHAN-REQUEST" not in _codes(evaluate(inp, 0))


# каждый @blocked_by даёт ожидание (рубеж 2 C1)


def test_legacy_slug_blocker_resolves_to_the_item() -> None:
    todos = {
        "a": GOAL + "@blocked_by:b#need-policy\n",
        "b": "- [ ] n @owner:github:own @id:need-policy\n",
    }
    wait = _wait(evaluate(inputs(todos), 0), "todo://a/goal")
    assert (wait.prereq, wait.reason) == ("todo://b/need-policy", "open")


def test_unresolvable_blocker_keeps_the_item_waiting_without_a_question() -> None:
    result = evaluate(inputs({"a": GOAL + "@blocked_by:spec-runner-release\n"}), 0)
    wait = _wait(result, "todo://a/goal")
    assert (wait.verdict, wait.reason) == ("unknown", "unresolvable")
    assert "todo://a/goal" not in {e.node_id for e in result.queue}
    assert "GR-BLOCKER-UNRESOLVABLE" in _codes(result)
    assert owner_questions(result) == []


# закрытый потребитель не ждёт (рубеж 2 I1)


def test_done_consumer_makes_no_cycle_and_gives_no_rank() -> None:
    todos = {
        "a": "- [x] x @owner:github:own @id:x @epic:eco.focus1 "
        "@blocked_by:todo://b/y\n",
        "b": "- [ ] y @owner:github:own @id:y @blocked_by:todo://a/x\n",
    }
    result = evaluate(inputs(todos, roadmap=None), 0)
    assert result.cycles == []
    assert "todo://b/y" in {e.node_id for e in result.queue}
    ranked = evaluate(inputs(todos), 0)
    entry = next(e for e in ranked.queue if e.node_id == "todo://b/y")
    assert entry.rank is None and entry.unblocks == 0


# отправленная, ещё не принятая заявка — не «missing» (рубеж 2 I3)


def test_sent_request_not_yet_accepted_is_pending() -> None:
    todos = {"a": GOAL + "@blocked_by:todo://b/need\n"}
    request = record("b", 5, body="slug: need\nfrom: a#goal\n", labels=["inbox"])
    result = evaluate(inputs(todos, [request]), 0)
    wait = next(w for w in result.waits if w.prereq == "todo://b/need")
    assert (wait.verdict, wait.reason) == ("pending", "request_open")
    assert owner_questions(result) == []
    assert "GR-DANGLING-WAIT" not in _codes(result)
