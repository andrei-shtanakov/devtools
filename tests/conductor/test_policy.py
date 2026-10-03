from conductor.graph import build_graph
from conductor.inputs import RepoTodo
from conductor.policy import assess, delegable, position_level
from conductor.rank import build_attention, build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"
R3 = ROADMAP.replace("autonomy = 0", "autonomy = 3")


def _assess(todos, records=(), roadmap=R3, level=3, **extra):
    inp = inputs(todos, records, **extra)
    g = build_graph(inp)
    w = evaluate_waits(g, inp, NOW, 3)
    rm = parse_roadmap(roadmap, EPICS)
    entries = build_queue(g, w, rm, NOW) + build_attention(g, w, rm, NOW)
    return {e.node_id: assess(e, g, w, rm, level, inp) for e in entries}


def test_focus_item_at_level_3_is_unverified_launch() -> None:
    got = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})[
        "todo://a/x"
    ]
    assert (got.level, got.need, got.delegable, got.action) == (
        3,
        "implement",
        "unverified",
        "launch?",
    )


def test_level_0_is_output_only() -> None:
    got = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"}, level=0)[
        "todo://a/x"
    ]
    assert got.action == "—"


def test_own_focus_autonomy_caps_inherited_rank() -> None:
    rm = R3.replace('epic = "eco.focus2"', 'epic = "eco.focus2"\nautonomy = 0')
    got = _assess(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://c/f2\n",
            "c": "- [ ] f2 @owner:github:own @id:f2 @epic:eco.focus2\n",
        },
        roadmap=rm,
    )["todo://c/f2"]
    assert got.level == 0 and got.action == "—"


SIGNALS = {
    "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
    "- [ ] подпись формы @owner:github:own @id:y "
    "@epic:eco.focus1\n"
    "- [ ] z @owner:github:someone @id:z @epic:eco.focus1\n"
    "- [ ] n @owner:github:own @id:n @epic:eco.unknown\n"
}


def test_owner_and_decision_signals() -> None:
    a = _assess(SIGNALS)
    assert a["todo://a/x"].block_reason == "owner-tbd"
    assert a["todo://a/y"].block_reason == "decision-signal"
    assert a["todo://a/z"].block_reason == "foreign-owner"
    assert a["todo://a/n"].block_reason == "epic-unknown"
    # вопрос — только где ответ меняет шаг: кандидат фокуса на уровне запуска;
    # чужой владелец — не вопрос и там (§5.7)
    assert {n for n in a if a[n].need == "decide"} == {"todo://a/x", "todo://a/y"}
    assert a["todo://a/n"].need == "implement" and not a["todo://a/n"].ask_owner
    assert a["todo://a/z"].need == "implement" and not a["todo://a/z"].ask_owner


def test_out_of_loop_at_launch_level_is_a_flag_not_a_question() -> None:
    # #511: вне контура ответ владельца следующего шага не меняет (§5.7)
    inp = inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})
    g = build_graph(inp)
    w = evaluate_waits(g, inp, NOW, 3)
    rm = parse_roadmap(R3, EPICS)
    entry = next(e for e in build_queue(g, w, rm, NOW) if e.node_id == "todo://a/x")
    inp.todos = [
        RepoTodo("a", None, None, "error", "нет клона") if t.repo == "a" else t
        for t in inp.todos
    ]
    got = assess(entry, g, w, rm, 3, inp)
    assert (got.delegable, got.block_reason) == ("no", "out-of-loop")
    assert got.need == "implement" and not got.ask_owner


def test_signals_below_launch_level_are_flags_not_questions() -> None:
    a = _assess(SIGNALS, level=0)
    assert all(v.need == "implement" and not v.ask_owner for v in a.values())
    assert all(v.delegable == "no" and v.action == "—" for v in a.values())


def test_decision_signal_in_glued_request_text() -> None:
    a = _assess(
        {"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"},
        [record("a", 1, body="slug: x\nнужен sign-off владельца\n", labels=["inbox"])],
    )
    assert a["todo://a/x"].block_reason == "decision-signal"


def test_adr_mention_is_not_a_decision() -> None:
    inp = inputs(
        {
            "a": "- [ ] исправить тест по ADR-ECO-006 @owner:github:own "
            "@id:x @epic:eco.focus1\n"
        }
    )
    assert delegable("todo://a/x", build_graph(inp), inp) == (
        "unverified",
        "authority-root",
    )


def test_no_checkout_is_not_delegable() -> None:
    inp = inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})
    graph = build_graph(inp)
    inp.todos = [
        RepoTodo("a", None, None, "error", "нет клона") if t.repo == "a" else t
        for t in inp.todos
    ]
    assert delegable("todo://a/x", graph, inp)[0] == "no"


def test_parked_leaf_level_2_background_pull_launches() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://c/c\n",
        "c": "- [ ] c @owner:github:own @id:c @epic:eco.parked\n",
    }
    parked = _assess(todos)["todo://c/c"]
    assert parked.level == 2 and not parked.action.startswith("launch")
    bg = todos | {"c": "- [ ] c @owner:github:own @id:c @epic:eco.bg\n"}
    pull = R3.replace(
        'goal = "todo://a/goal"', 'goal = "todo://a/goal"\npull_prerequisites = true'
    )
    assert _assess(bg, roadmap=pull)["todo://c/c"].action == "launch?"
    assert _assess(bg)["todo://c/c"].action != "launch?"


def test_pr_table() -> None:
    a = _assess(
        {},
        [
            record("a", 1, is_pr=True, ci="red"),
            record("a", 2, is_pr=True, ci="green", review_decision="APPROVED"),
            record("a", 3, is_pr=True, ci="pending", approved_at_head=True),
            record("a", 4, is_pr=True, ci="green", approved_at_head=True),
            record(
                "a",
                5,
                is_pr=True,
                ci="green",
                approved_at_head=True,
                files=["merge-pr.sh"],
            ),
            record("b", 6, is_pr=True, ci="green", approved_at_head=True),
            record(
                "a",
                7,
                is_pr=True,
                ci="green",
                approved_at_head=True,
                complete=False,
            ),
        ],
        authority_prefixes=["merge-pr.sh", ".github/"],
        human_merge_repos=["b"],
    )
    got = {n: (a[n].need, a[n].actor) for n in a}
    assert got == {
        "a!1": ("fix_pr", "own"),
        "a!2": ("review", "review-loop"),  # одобрен не head SHA
        "a!3": ("wait_ci", "ci"),
        "a!4": ("merge", "merge-contour"),
        "a!5": ("merge", "owner"),  # authority-root
        "b!6": ("merge", "owner"),  # «Мерж: человек»
        "a!7": ("merge", "merge-contour?"),  # список файлов неполон
    }
    assert all(x.action in ("—", "pr_nudge") for x in a.values())


def test_questions_only_for_ranked() -> None:
    a = _assess(
        {
            "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
            "- [ ] y @owner:TBD @id:y @epic:eco.bg\n"
        }
    )
    assert a["todo://a/x"].ask_owner and not a["todo://a/y"].ask_owner


def test_prose_trigger_waits_without_question() -> None:
    a = _assess(
        {
            "a": "- [ ] t @owner:github:own @id:t @epic:eco.focus1 "
            '@trigger:"после появления X"\n'
            "- [ ] m @owner:github:own @id:m @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    t, m = a["todo://a/t"], a["todo://a/m"]
    assert (t.need, t.actor, t.ask_owner) == ("wait_condition", "condition", False)
    assert (m.need, m.ask_owner) == ("decide", True)


def test_position_level_formula() -> None:
    rm = parse_roadmap(R3, EPICS)
    inp = inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.bg\n"})
    g = build_graph(inp)
    entry = build_queue(g, evaluate_waits(g, inp, NOW, 3), rm, NOW)[0]
    assert position_level(entry, rm, run_level=3) == 1
