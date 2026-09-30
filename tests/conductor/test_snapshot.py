import json

import jsonschema
import pytest

from conductor.snapshot import SCHEMA_PATH, evaluate, question_id, to_snapshot
from tests.conductor.fixtures import ROADMAP, inputs, record

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _snap(todos, **kw):
    inp = inputs(todos, **kw)
    return to_snapshot(evaluate(inp, 0), inp, "run-1", None)


def test_snapshot_matches_schema_and_carries_work_state() -> None:
    snap = _snap(
        {
            "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
            "- [ ] y @owner:TBD @id:y @epic:eco.bg\n"
        }
    )
    jsonschema.validate(snap, SCHEMA)
    assert snap["graph_state"] == "complete" and snap["run_level"] == 0
    node = next(n for n in snap["nodes"] if n["node_id"] == "todo://a/x")
    assert node["work_state"] == "idle"
    # нет @owner на уровне 0 — пометка позиции, не вопрос (§5.7 rev 11)
    assert snap["metrics"]["owner_questions"] == 0
    assert snap["metrics"]["position_flags"] == {"owner-tbd": 2}


def test_attention_reaches_owner_questions() -> None:
    snap = _snap(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert [q["subject"] for q in snap["owner_questions"]] == ["todo://a/goal"]


def test_partial_and_invalid_roadmap() -> None:
    snap = _snap(
        {"a": "- [ ] x @owner:TBD @id:x\n"}, gh_state="error", roadmap="nope = ["
    )
    jsonschema.validate(snap, SCHEMA)
    assert snap["graph_state"] == "partial"
    assert "RM-INVALID" in {f["code"] for f in snap["findings"]}
    assert all(q["rank"] is None for q in snap["queue"])


def test_schema_rejects_node_without_work_state() -> None:
    snap = _snap({"a": "- [ ] x @owner:TBD @id:x\n"})
    del snap["nodes"][0]["work_state"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(snap, SCHEMA)


def test_questions_by_reason() -> None:
    snap = _snap(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/gone\n"
            "- [x] s @owner:TBD @id:shipped @epic:eco.focus1\n"
            "- [x] z @owner:TBD @id:bgshipped @epic:eco.bg\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        },
        records=[
            record("a", 1, body="slug: shipped\n", labels=["inbox"]),
            record("a", 2, body="slug: bgshipped\n", labels=["inbox"]),
        ],
        history={"todo://b/gone": "deadbeef"},
    )
    got = {(q["subject"], q["reason"]): q for q in snap["owner_questions"]}
    cancelled = got[("todo://a/goal", "cancelled")]
    assert cancelled["options"] == ["drop-wait", "replace", "keep"]
    assert "todo://b/gone" in cancelled["evidence"]
    assert got[("a#1", "GR-SHIPPED-OPEN")]["options"] == ["close", "keep"]
    assert ("a#2", "GR-SHIPPED-OPEN") not in got  # фон: находка есть, вопроса нет


def test_finding_questions_follow_inherited_rank() -> None:
    snap = _snap(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/pre\n",
            "b": "- [x] p @owner:TBD @id:pre @epic:eco.bg\n"
            "- [ ] m @owner:TBD @id:m @epic:eco.focus2\n",
        },
        records=[
            record("b", 1, body="slug: pre\n", labels=["inbox"]),
            record("b", 2, body="slug: m\nfrom: a#goal\n", labels=["inbox"]),
        ],
    )
    got = {(q["subject"], q["reason"]) for q in snap["owner_questions"]}
    assert ("b#1", "GR-SHIPPED-OPEN") in got  # фон, но ранг 1 через веху
    assert ("b#2", "GR-SLUG-MATCH") not in got  # наследие: slug — связь по D2
    assert "GR-SLUG-MATCH" in {f["code"] for f in snap["findings"]}
    assert "GR-SHIPPED-OPEN" in {f["code"] for f in snap["findings"]}


def test_plan_ceiling_respects_roadmap_autonomy() -> None:
    rm = ROADMAP.replace('epic = "eco.focus1"', 'epic = "eco.focus1"\nautonomy = 3')
    inp = inputs(
        {"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"}, roadmap=rm
    )
    result = evaluate(inp, 3)
    assert result.run_level == 0
    assert all(a.action == "—" for a in result.assessments.values())


def test_question_id_depends_on_options_order() -> None:
    a = question_id("owner-tbd", "todo://a/x", "ev", ("delegate", "keep"))
    b = question_id("owner-tbd", "todo://a/x", "ev", ("keep", "delegate"))
    assert a != b and len(a) == 8
