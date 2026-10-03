from conductor.graph import build_graph
from conductor.rank import build_attention, build_hints, build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _q(todos, records=(), roadmap=ROADMAP):
    inp = inputs(todos, records)
    g = build_graph(inp)
    w = evaluate_waits(g, inp, NOW, 3)
    rm = parse_roadmap(roadmap, EPICS)
    return build_queue(g, w, rm, NOW), build_attention(g, w, rm, NOW)


def test_leaf_of_parked_epic_inherits_rank_1() -> None:
    queue, _ = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
            "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg @blocked_by:todo://c/c\n",
            "c": "- [ ] c @owner:TBD @id:c @epic:eco.parked\n"
            "- [ ] f2 @owner:TBD @id:f2 @epic:eco.focus2\n",
        }
    )
    assert [e.node_id for e in queue][:2] == ["todo://c/c", "todo://c/f2"]
    leaf = queue[0]
    assert (leaf.rank, leaf.klass, leaf.via_focus, leaf.own_focus) == (
        1,
        "parked",
        "eco.focus1",
        None,
    )
    assert leaf.on_goal_path and leaf.unblocks == 2
    assert "todo://a/goal" in leaf.why and "todo://b/b" in leaf.why


def test_own_focus_is_kept_when_rank_is_inherited() -> None:
    queue, _ = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://c/f2\n",
            "c": "- [ ] f2 @owner:TBD @id:f2 @epic:eco.focus2\n",
        }
    )
    entry = next(e for e in queue if e.node_id == "todo://c/f2")
    assert (entry.rank, entry.via_focus, entry.own_focus) == (
        1,
        "eco.focus1",
        "eco.focus2",
    )


def test_glued_issue_is_represented_by_item() -> None:
    queue, _ = _q(
        {"a": "- [ ] x @owner:TBD @id:x @epic:eco.focus2\n"},
        [record("a", 1, body="slug: x\n", labels=["inbox"])],
    )
    assert [e.node_id for e in queue] == ["todo://a/x"]


def test_unranked_last_invalid_roadmap_unranked_cycles_excluded() -> None:
    todos = {
        "a": "- [ ] x @owner:TBD @id:x @epic:eco.bg\n"
        "- [ ] y @owner:TBD @id:y @epic:eco.focus2\n"
    }
    assert [e.node_id for e in _q(todos)[0]] == ["todo://a/y", "todo://a/x"]
    assert all(e.rank is None for e in _q(todos, roadmap="bad = [")[0])
    cyc = {
        "a": "- [ ] a @owner:TBD @id:a @epic:eco.focus1 @blocked_by:todo://a/b\n"
        "- [ ] b @owner:TBD @id:b @epic:eco.focus1 @blocked_by:todo://a/a\n"
    }
    assert _q(cyc)[0] == []


def test_attention_is_ranked_too() -> None:
    _, attention = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert [(e.node_id, e.rank) for e in attention] == [("todo://a/goal", 1)]


def test_mention_is_not_a_hint_when_the_pr_implements_the_same_node() -> None:
    # #511: связь подтверждена (@id: в теле), упоминание склеенного запроса
    # в заголовке не даёт «связь не подтверждена»
    todos = {
        "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://b/y\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }
    records = [
        record("b", 3, body="slug: y\nfrom: a\n", labels=["inbox"]),
        record("b", 5, is_pr=True, title="docs(#3): plan", body="@id:y"),
    ]
    g = build_graph(inputs(todos, records))
    assert any(e.src == "b!5" and e.type == "mentions" for e in g.edges)
    assert build_hints(g, parse_roadmap(ROADMAP, EPICS)) == []


def test_glued_item_inherits_the_age_of_a_from_wait() -> None:
    # #511: ожидание по from: стоит на запросе; возраст получает его пункт
    request = record(
        "b",
        5,
        body="slug: y\nfrom: a#x\n",
        labels=["inbox"],
        created_at="2026-09-20T00:00:00Z",
    )
    queue, _ = _q(
        {"a": "- [ ] x @owner:TBD @id:x\n", "b": "- [ ] y @owner:TBD @id:y\n"},
        [request],
    )
    assert next(e for e in queue if e.node_id == "todo://b/y").line_age_days == 9
