"""Регрессия ручного разбора очереди 2026-09-30 (спека §10, сценарии 48–54)."""

from conductor.analysis import dependency_adjacency, find_cycles
from conductor.graph import build_graph
from conductor.render import render_status
from conductor.snapshot import evaluate, owner_questions, to_snapshot
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"
PRODUCER = {"b": "- [ ] y @owner:github:own @id:y @epic:eco.focus1\n"}


def _graph(todos, records):
    return build_graph(inputs(todos, records))


def _codes(graph):
    return {f.code for f in graph.findings}


# 48 — заявка без метки: только распознанный протокол целиком


def test_unlabelled_request_with_prose_after_sender_repo_is_glued() -> None:
    body = "slug: y\nfrom: a (DarkFactory E, «оракул»)\n"
    g = _graph(PRODUCER, [record("b", 5, body=body)])
    assert g.resolve("b#5") == "todo://b/y"
    assert "GR-INBOX-NO-LABEL" in _codes(g)


def test_unlabelled_request_sender_item_waits_on_the_request() -> None:
    todos = {**PRODUCER, "a": "- [ ] x @owner:github:own @id:x\n"}
    g = _graph(todos, [record("b", 5, body="slug: y\nfrom: a#x\n")])
    assert any(
        (e.src, e.dst, e.type) == ("todo://a/x", "b#5", "depends_on") for e in g.edges
    )


def test_partial_protocol_is_not_a_request() -> None:
    for body in (
        "slug: y\n",
        "slug: y\nfrom: zzz\n",
        "slug: nope\nfrom: a\n",
        "slug: Y Y\nfrom: a\n",
    ):
        g = _graph(PRODUCER, [record("b", 5, body=body)])
        assert g.resolve("b#5") == "b#5", body
        assert "GR-INBOX-NO-LABEL" not in _codes(g), body


# 49 — from: закрытого запроса — история, а не ожидание

PING = {
    "a": "- [ ] x @owner:github:own @id:x\n",
    "b": "- [ ] y @owner:github:own @id:y @blocked_by:todo://a/x\n",
}


def _request(**fields):
    return record("b", 5, body="slug: y\nfrom: a#x\n", labels=["inbox"], **fields)


def _cycles_and_waits(req):
    inp = inputs(PING, [req])
    g = build_graph(inp)
    waits = evaluate_waits(g, inp, NOW, 3)
    return find_cycles(dependency_adjacency(g)), [
        w for w in waits if w.consumer == "todo://a/x"
    ]


def test_closed_completed_request_leaves_no_wait_and_no_cycle() -> None:
    cycles, waits = _cycles_and_waits(
        _request(state="closed", state_reason="completed")
    )
    assert cycles == [] and waits == []


def test_open_request_is_a_live_wait_and_the_cycle_is_real() -> None:
    cycles, waits = _cycles_and_waits(_request())
    assert cycles == [["todo://a/x", "todo://b/y"]]
    assert [(w.prereq, w.verdict) for w in waits] == [("b#5", "pending")]


def test_declined_request_is_a_cancelled_prerequisite() -> None:
    cycles, waits = _cycles_and_waits(
        _request(state="closed", state_reason="not_planned")
    )
    assert cycles == []
    assert [(w.prereq, w.verdict, w.reason) for w in waits] == [
        ("b#5", "unknown", "cancelled")
    ]


# 50 — одна канонизация концов рёбер


def test_old_repo_name_in_own_blocked_by_resolves_to_canonical_item() -> None:
    # живая форма: репо ссылается на свой же пункт прежним именем —
    # plan-fields такую ссылку не резолвит (resolved_target = None)
    todos = {
        "ecosystem-kb": "- [ ] g @owner:github:own @id:g @epic:eco.focus1 "
        "@blocked_by:todo://prograph-vault/z\n"
        "- [ ] z @owner:github:own @id:z\n",
    }
    result = evaluate(inputs(todos), 0)
    wait = next(w for w in result.waits if w.consumer == "todo://ecosystem-kb/g")
    assert (wait.prereq, wait.reason) == ("todo://ecosystem-kb/z", "open")
    assert owner_questions(result) == []


def test_blocked_by_issue_number_that_is_a_pr_points_to_the_pr() -> None:
    todos = {"a": "- [ ] g @owner:github:own @id:goal @blocked_by:b#7\n"}
    g = _graph(todos, [record("b", 7, is_pr=True)])
    assert any(e.dst == "b!7" and e.type == "depends_on" for e in g.edges)


def test_mention_of_issue_number_that_is_a_pr_points_to_the_pr() -> None:
    g = _graph({}, [record("b", 7, is_pr=True), record("a", 1, body="см. b#7")])
    assert any(e.dst == "b!7" and e.type == "mentions" for e in g.edges)


# 51 — ранг PR только по implements; упоминание — подсказка

GOAL = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:b#3\n"}


def test_closing_pr_inherits_rank_and_goal_path() -> None:
    result = evaluate(
        inputs(
            GOAL, [record("b", 3), record("b", 4, is_pr=True, closing_refs=["b#3"])]
        ),
        0,
    )
    entry = next(e for e in result.queue if e.node_id == "b!4")
    assert (entry.rank, entry.on_goal_path, result.blocks["b!4"]) == (1, True, "goal")
    assert result.hints == []


def test_mentioning_pr_is_a_hint_without_rank() -> None:
    records = [
        record("b", 3),
        record("b", 9),
        record("b", 5, is_pr=True, title="docs(#3): plan"),
        record("b", 6, is_pr=True, title="docs(#9): other"),
    ]
    result = evaluate(inputs(GOAL, records), 0)
    assert [(h.pr, h.target) for h in result.hints] == [("b!5", "b#3")]
    assert next(e for e in result.queue if e.node_id == "b!5").rank is None
    assert "возможный следующий шаг: b!5 упоминает b#3" in render_status(result)


def test_goal_item_with_hint_is_not_called_ready_to_launch() -> None:
    goal = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://b/work\n",
        "b": "- [ ] w @owner:github:own @id:work\n",
    }
    plan_pr = record("b", 5, is_pr=True, title="docs: plan for todo://b/work")
    text = render_status(evaluate(inputs(goal, [plan_pr]), 0))
    assert "готовность к запуску не подтверждена" in text
    assert "рассмотрение b!5" in text
    assert "возможный следующий шаг: b!5" not in text


def test_approved_pr_outside_focus_is_one_compact_line() -> None:
    ready = record(
        "c", 8, is_pr=True, approved_at_head=True, ci="green", files=["x.py"]
    )
    result = evaluate(inputs({}, [ready]), 0)
    entry = next(e for e in result.queue if e.node_id == "c!8")
    assert entry.rank is None and result.blocks["c!8"] == "backlog"
    assert "вне фокуса: PR, готовые к рассмотрению мержа: c!8" in render_status(result)


# 53 — блоки выдачи: сворачивание не выбрасывает позиции


def test_goal_step_outranks_old_unsignalled_focus_item() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://b/step\n",
        "b": "- [ ] s @owner:github:own @id:step\n",
        "c": "- [ ] o @owner:github:own @id:old @epic:eco.focus1\n"
        "- [ ] p @owner:github:own @id:other @epic:eco.focus1\n",
    }
    inp = inputs(todos)
    result = evaluate(inp, 0)
    assert result.blocks["todo://b/step"] == "goal"
    assert result.blocks["todo://c/old"] == "backlog"
    text = render_status(result)
    assert text.index("todo://b/step") < text.index("остальной бэклог")
    assert "фокус 1 (eco.focus1): c 2" in text
    assert "todo://c/old" in render_status(result, repo="c")
    snap = to_snapshot(result, inp, "run-1", None)
    assert {p["node_id"] for p in snap["queue"]} >= {"todo://c/old", "todo://c/other"}


# 54 — нет даты строки → возраст неизвестен, а не 0


def test_unknown_line_age_is_not_zero() -> None:
    todos = {
        "a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n",
        "b": "- [ ] y @owner:github:own @id:y @blocked_by:todo://a/x\n",
    }
    result = evaluate(inputs(todos), 0)
    entry = next(e for e in result.queue if e.node_id == "todo://a/x")
    assert entry.line_age_days is None
    assert "≈" not in render_status(result)


# адресный круг ревью дельты (2026-09-30): контрпримеры Codex


def test_hint_blocks_launch_and_delegation_question() -> None:
    goal = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}
    r3 = ROADMAP.replace("autonomy = 0", "autonomy = 3")
    plan_pr = record("a", 5, is_pr=True, title="docs: plan todo://a/goal")
    result = evaluate(inputs(goal, [plan_pr], roadmap=r3), 3)
    a = result.assessments["todo://a/goal"]
    assert result.hints and a.action != "launch?" and not a.ask_owner
    no_owner = {"a": "- [ ] g @id:goal @epic:eco.focus1\n"}
    result = evaluate(inputs(no_owner, [plan_pr], roadmap=r3), 3)
    assert not result.assessments["todo://a/goal"].ask_owner


FENCE = "```"


def test_protocol_inside_code_comment_or_conflict_is_not_a_request() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal\n",
        "b": "- [ ] y @owner:github:own @id:y @blocked_by:todo://a/goal\n",
    }
    for body in (
        f"Пример:\n{FENCE}yaml\nslug: y\nfrom: a#goal\n{FENCE}\n",
        "<!--\nslug: y\nfrom: a#goal\n-->\n",
        "пример:\n\n    slug: y\n    from: a#goal\n",
        "slug: y\nslug: other\nfrom: a#goal\n",
    ):
        result = evaluate(inputs(todos, [record("b", 5, body=body)]), 0)
        assert result.graph.resolve("b#5") == "b#5", body
        assert result.cycles == [], body


def test_protocol_must_be_the_body_header() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal\n",
        "b": "- [ ] y @owner:github:own @id:y @blocked_by:todo://a/goal\n"
        "- [ ] z @owner:github:own @id:z\n",
    }
    example_first = f"{FENCE}yaml\nslug: y\nfrom: a#goal\n{FENCE}\nslug: z\nfrom: c\n"
    for body in (example_first, f"{FENCE}\nslug: y\nfrom: a#goal\n"):
        result = evaluate(inputs(todos, [record("b", 5, body=body)]), 0)
        assert result.graph.resolve("b#5") == "b#5" and result.cycles == [], body
    header = evaluate(inputs(todos, [record("b", 5, body="from: c\nslug: z\n")]), 0)
    assert header.graph.resolve("b#5") == "todo://b/z"


def test_truncated_weak_pr_does_not_break_completeness() -> None:
    from conductor.collect import truncated_prs

    weak = record("b", 3, is_pr=True, complete=False, weak=True)
    strong = record("b", 4, is_pr=True, complete=False)
    assert truncated_prs([weak, strong]) == ["b!4: список файлов или ревью усечён"]


def test_local_refs_of_open_prs_are_weak_fetch_targets() -> None:
    from conductor.graph import local_refs, normalizer, referenced_issues

    inp = inputs({}, [record("b", 5, is_pr=True, title="docs(#3): plan")])
    norm = normalizer(inp)
    assert ("b", 3) in local_refs(inp.gh_records, norm)
    assert ("b", 3) not in referenced_issues(inp.gh_records, inp.todos, norm)


def test_link_targets_and_urls_are_not_local_refs() -> None:
    for body in ("[section](#3)", "см. https://example.org/?#3", "[s][x]\n\n[x]: #3"):
        g = _graph({}, [record("b", 5, is_pr=True, body=body)])
        assert not [e for e in g.edges if e.dst == "b#3"], body
    title = _graph({}, [record("b", 5, is_pr=True, title="см. https://x.org/?#3")])
    assert not [e for e in title.edges if e.dst == "b#3"]


def test_adding_implements_never_lowers_unblocks() -> None:
    todos = {
        "a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"
        "- [ ] y @owner:github:own @id:y @epic:eco.focus1 @blocked_by:todo://a/x\n"
    }
    one = record("a", 5, is_pr=True, body="@id:x")
    both = record("a", 5, is_pr=True, body="@id:x @id:y")
    unblocks = [
        next(
            e for e in evaluate(inputs(todos, [pr]), 0).queue if e.node_id == "a!5"
        ).unblocks
        for pr in (one, both)
    ]
    assert unblocks == [1, 1]


# инвариант ревью 2026-09-30: слабые данные дают подсказки, но не готовность,
# выполненность, запуск или вопрос


def test_weak_request_creates_no_wait_and_no_question() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n",
        "b": "- [ ] y @owner:github:own @id:y\n",
    }
    weak = record(
        "b",
        3,
        body="slug: y\nfrom: a#goal\n",
        labels=["inbox"],
        state="closed",
        state_reason="not_planned",
        weak=True,
    )
    result = evaluate(inputs(todos, [weak]), 0)
    assert result.waits == [] and owner_questions(result) == []
    assert result.graph.resolve("b#3") == "b#3"


def test_weak_glue_still_feeds_the_hint() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://b/y\n",
        "b": "- [ ] y @owner:github:own @id:y\n",
    }
    records = [
        record("b", 5, is_pr=True, title="docs(#3): plan"),
        record(
            "b",
            3,
            body="slug: y\nfrom: a\n",
            labels=["inbox"],
            state="closed",
            state_reason="completed",
            weak=True,
        ),
    ]
    result = evaluate(inputs(todos, records), 0)
    assert [(h.pr, h.target) for h in result.hints] == [("b!5", "todo://b/y")]


def test_unrecognized_protocol_stays_a_visible_issue() -> None:
    body = "Контекст.\nslug: y\nfrom: a\n"
    g = _graph(PRODUCER, [record("b", 5, body=body)])
    assert g.resolve("b#5") == "b#5" and "b#5" in g.nodes
    assert "GR-PROTOCOL-UNRECOGNIZED" in _codes(g)
