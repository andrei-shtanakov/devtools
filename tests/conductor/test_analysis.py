from conductor.analysis import (
    attention_nodes,
    dependency_adjacency,
    find_cycles,
    findings,
    is_ready,
    work_state,
)
from conductor.graph import build_graph
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _setup(todos, records=(), **extra):
    inp = inputs(todos, records, **extra)
    g = build_graph(inp)
    return g, evaluate_waits(g, inp, NOW, 3)


def test_chain_only_leaf_ready() -> None:
    g, w = _setup(
        {
            "a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://b/b\n",
            "b": "- [ ] b @owner:TBD @id:b @blocked_by:todo://c/c\n",
            "c": "- [ ] c @owner:TBD @id:c\n",
        }
    )
    assert [
        n for n in ("todo://a/a", "todo://b/b", "todo://c/c") if is_ready(n, g, w)
    ] == ["todo://c/c"]


def test_ping_pong_through_issue_plane_is_cycle() -> None:
    g, w = _setup(
        {
            "devtools": "- [ ] o @owner:TBD @id:oracle @blocked_by:spec-runner#603\n"
            "- [ ] q @owner:TBD @id:q "
            "@blocked_by:todo://spec-runner/verify\n",
            "spec-runner": "- [ ] v @owner:TBD @id:verify @blocked_by:devtools#491\n",
        },
        [
            record(
                "spec-runner",
                603,
                body="slug: verify\nfrom: devtools#oracle\n",
                labels=["inbox"],
            ),
            record(
                "devtools",
                491,
                body="slug: q\nfrom: spec-runner#verify\n",
                labels=["inbox"],
            ),
        ],
    )
    cycles = find_cycles(dependency_adjacency(g))
    assert any(
        {"todo://devtools/q", "todo://spec-runner/verify"} <= set(c) for c in cycles
    )
    assert "GR-CYCLE" in {f.code for f in findings(g, w, cycles, None)}


def test_self_loop_and_long_chain() -> None:
    g, _ = _setup({"a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://a/a\n"})
    assert find_cycles(dependency_adjacency(g)) == [["todo://a/a"]]
    assert find_cycles({f"n{i}": {f"n{i + 1}"} for i in range(5000)}) == []


def test_findings_catalogue() -> None:
    g, w = _setup(
        {
            "a": "- [x] s @owner:TBD @id:shipped\n"
            "- [ ] d @owner:TBD @id:d @blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        },
        [
            record("a", 1, body="slug: shipped\n", labels=["inbox"]),
            record("a", 2, body="упоминаю b#3"),
            record("b", 3),
        ],
    )
    rm = parse_roadmap(ROADMAP, EPICS)
    codes = {f.code for f in findings(g, w, [], rm)}
    assert {
        "GR-SHIPPED-OPEN",
        "GR-WEAK-EDGE",
        "GR-DANGLING-WAIT",
        "RM-GOAL-MISSING",
    } <= codes


def test_open_pr_on_glued_issue_puts_item_in_review() -> None:
    g, _ = _setup(
        {"a": "- [ ] x @owner:TBD @id:x\n"},
        [
            record("a", 1, body="slug: x\n", labels=["inbox"]),
            record("a", 5, is_pr=True, closing_refs=["a#1"]),
        ],
    )
    assert work_state("todo://a/x", g) == "in_review"


def test_attention_lists_unknown_waits() -> None:
    g, w = _setup(
        {
            "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/nope\n"
            '- [ ] t @owner:TBD @id:t @trigger:"когда-нибудь"\n',
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert attention_nodes(g, w) == ["todo://a/t", "todo://a/x"]
