from conductor.graph import build_graph
from conductor.waits import evaluate_waits, waits_of
from tests.conductor.fixtures import inputs, record

NOW = "2026-09-29T12:00:00Z"


def _w(todos, records=(), **extra):
    inp = inputs(todos, records, **extra)
    return evaluate_waits(build_graph(inp), inp, NOW, stale_after_days=3)


def _one(waits, consumer):
    got = waits_of(waits, consumer)
    assert len(got) == 1, got
    return got[0]


def test_done_item_satisfies_with_sha_evidence() -> None:
    w = _one(
        _w(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                "b": "- [x] y @owner:TBD @id:y\n",
            }
        ),
        "todo://a/x",
    )
    assert (w.verdict, w.reason, w.evidence) == (
        "satisfied",
        "done",
        "todo://b/y@sha-b",
    )


def test_glued_issue_resolves_to_item() -> None:
    w = _one(
        _w(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:b#3\n",
                "b": "- [x] q @owner:TBD @id:q\n",
            },
            [record("b", 3, body="slug: q\n", labels=["inbox"])],
        ),
        "todo://a/x",
    )
    assert (w.prereq, w.verdict) == ("todo://b/q", "satisfied")


def test_cancelled_forms() -> None:
    w = _w(
        {
            "a": "- [ ] x @owner:TBD @id:x @blocked_by:b#3\n"
            "- [ ] y @owner:TBD @id:y @blocked_by:todo://b/gone\n"
            "- [ ] z @owner:TBD @id:z @blocked_by:todo://b/never\n"
        },
        [record("b", 3, state="closed", state_reason="not_planned")],
        history={"todo://b/gone": "deadbeef"},
    )
    assert _one(w, "todo://a/x").reason == "cancelled"
    assert _one(w, "todo://a/y").reason == "cancelled"
    assert _one(w, "todo://a/z").reason == "missing"
    assert all(x.verdict == "unknown" for x in w)


def test_stale_needs_old_wait_and_no_movement() -> None:
    todos = {
        "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }
    old_wait = {"todo://a/x|todo://b/y": "2026-09-01T00:00:00Z"}
    quiet = _one(
        _w(todos, movement={"todo://b/y": "2026-09-20T00:00:00Z"}, wait_since=old_wait),
        "todo://a/x",
    )
    assert quiet.reason == "stale"
    fresh_wait = _one(
        _w(
            todos,
            movement={"todo://b/y": "2026-09-20T00:00:00Z"},
            wait_since={"todo://a/x|todo://b/y": "2026-09-28T00:00:00Z"},
        ),
        "todo://a/x",
    )
    assert fresh_wait.reason == "open"
    busy = _one(
        _w(
            todos,
            [
                record(
                    "b", 9, is_pr=True, body="@id:y", updated_at="2026-09-29T00:00:00Z"
                )
            ],
            movement={"todo://b/y": "2026-09-20T00:00:00Z"},
            wait_since=old_wait,
        ),
        "todo://a/x",
    )
    assert busy.reason == "open"
    unknown = _one(_w(todos, wait_since=old_wait), "todo://a/x")
    assert unknown.reason == "open"
    no_since = _one(
        _w(todos, movement={"todo://b/y": "2026-09-01T00:00:00Z"}), "todo://a/x"
    )
    assert no_since.reason == "open"


def test_triggers() -> None:
    facts = {
        "exists:b:contracts/v1/x.json": {
            "exists": False,
            "sha": "s",
            "siblings": ["v1", "v2"],
        },
        "exists:b:docs/y.md": {"exists": True, "sha": "s", "siblings": []},
    }
    w = _w(
        {
            "a": '- [ ] p @owner:TBD @id:p @trigger:"когда-нибудь"\n'
            '- [ ] d @owner:TBD @id:d @trigger:"date>=2026-09-01"\n'
            '- [ ] f @owner:TBD @id:f @trigger:"date>=2027-01-01"\n'
            "- [ ] v @owner:TBD @id:v "
            '@trigger:"exists:b:contracts/v1/x.json"\n'
            '- [ ] e @owner:TBD @id:e @trigger:"exists:b:docs/y.md"\n'
        },
        trigger_facts=facts,
    )
    got = {x.consumer: (x.verdict, x.reason) for x in w}
    assert got["todo://a/p"] == ("unknown", "prose_trigger")
    assert got["todo://a/d"] == ("satisfied", "date")
    assert got["todo://a/f"] == ("pending", "date")
    assert got["todo://a/v"] == ("unknown", "version_mismatch")
    assert got["todo://a/e"] == ("satisfied", "exists")


def test_closed_consumer_has_no_waits() -> None:
    assert (
        waits_of(
            _w(
                {
                    "a": "- [x] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                    "b": "- [ ] y @owner:TBD @id:y\n",
                }
            ),
            "todo://a/x",
        )
        == []
    )
