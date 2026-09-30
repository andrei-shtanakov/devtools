from conductor.model import Edge, Finding, Node, Source, issue_id, item_id, pr_id


def test_ids_are_canonical() -> None:
    assert item_id("devtools", "conductor") == "todo://devtools/conductor"
    assert issue_id("spec-runner", 603) == "spec-runner#603"
    assert pr_id("devtools", 7) == "devtools!7"


def test_types_are_frozen_and_hashable() -> None:
    node = Node(
        "todo://a/x",
        "item",
        "a",
        "x",
        is_open=True,
        owner_ref=(("id", "own"), ("kind", "github_user")),
    )
    edge = Edge("todo://a/x", "todo://b/y", "depends_on", "todo")
    assert {node, node} == {node}
    assert node.owner() == {"id": "own", "kind": "github_user"}
    assert {edge} == {Edge("todo://a/x", "todo://b/y", "depends_on", "todo")}
    assert Source("github", "error", "offline").state == "error"
    assert Finding("GR-CYCLE", "error", "todo://a/x").severity == "error"
