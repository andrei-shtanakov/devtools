from conductor.graph import build_graph, field_value, normalizer, referenced_issues
from tests.conductor.fixtures import inputs, record


def test_todo_edges_including_missing_and_legacy_issue() -> None:
    g = build_graph(
        inputs(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n"
                "- [ ] z @owner:TBD @id:z @blocked_by:spec-runner#603\n"
                "- [ ] m @owner:TBD @id:m @blocked_by:todo://b/nope\n",
                "b": "- [x] y @owner:TBD @id:y\n",
            },
            [record("spec-runner", 603)],
        )
    )
    deps = {(e.src, e.dst) for e in g.edges if e.type == "depends_on"}
    assert {
        ("todo://a/x", "todo://b/y"),
        ("todo://a/z", "spec-runner#603"),
        ("todo://a/m", "todo://b/nope"),
    } <= deps
    assert g.nodes["todo://b/y"].closed_as == "completed"
    assert not g.partial


def test_inbox_glue_from_edge_backticks_crlf_and_orphan() -> None:
    body = "slug: `deploy-action-decision-tool`\r\nfrom: `deployer#need-policy`\r\n"
    g = build_graph(
        inputs(
            {
                "arbiter": "- [ ] t @owner:TBD @id:deploy-action-decision-tool "
                "@epic:eco.focus1\n",
                "deployer": "- [ ] n @owner:TBD @id:need-policy\n",
            },
            [
                record("arbiter", 104, body=body, labels=["inbox"]),
                record(
                    "arbiter",
                    105,
                    body="slug: q\nfrom: deployer#gone\n",
                    labels=["inbox"],
                ),
            ],
        )
    )
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    target = "todo://arbiter/deploy-action-decision-tool"
    assert ("arbiter#104", target, "accepted_as") in edges
    assert ("todo://deployer/need-policy", "arbiter#104", "depends_on") in edges
    assert g.resolve("arbiter#104") == target
    assert g.members(target) == {target, "arbiter#104"}
    assert g.epic_of("arbiter#104") == "eco.focus1"
    assert "GR-ORPHAN-REQUEST" in {f.code for f in g.findings}
    assert "GR-SLUG-MATCH" in {f.code for f in g.findings}


def test_orphan_forms_and_source_ref_link() -> None:
    g = build_graph(
        inputs(
            {"arbiter": "- [ ] t @owner:TBD @id:t @source-ref:arbiter#1\n"},
            [
                record(
                    "arbiter", 1, body="slug: t\nfrom: deployer\n", labels=["inbox"]
                ),
                record("arbiter", 2, body="slug: q\n", labels=["inbox"]),
                record("arbiter", 3, body="slug: q\nfrom: ghost#x\n", labels=["inbox"]),
            ],
        )
    )
    by = {(f.code, f.subject) for f in g.findings}
    assert ("GR-ORPHAN-REQUEST", "arbiter#2") in by
    assert ("GR-ORPHAN-REQUEST", "arbiter#3") in by
    assert ("GR-ORPHAN-REQUEST", "arbiter#1") not in by  # report без пункта
    assert ("GR-SLUG-MATCH", "arbiter#1") not in by  # связь через @source-ref


def test_pr_implements_mentions_and_github_name_normalization() -> None:
    g = build_graph(
        inputs(
            {"a": "- [ ] x @owner:TBD @id:x\n"},
            [
                record(
                    "a",
                    5,
                    is_pr=True,
                    body="делает @id:x",
                    closing_refs=["prograph-vault#3"],
                ),
                record(
                    "a",
                    6,
                    comments=[
                        {
                            "author": "u",
                            "body": "см. prograph-vault#3",
                            "created_at": "",
                        }
                    ],
                ),
                record("ecosystem-kb", 3),
            ],
        )
    )
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    assert ("a!5", "todo://a/x", "implements") in edges
    assert ("a!5", "ecosystem-kb#3", "implements") in edges
    assert ("a#6", "ecosystem-kb#3", "mentions") in edges


def test_field_value_strips_quotes() -> None:
    assert field_value("from: `deployer`\r\n", "from") == "deployer"
    assert field_value("no fields", "slug") is None


def test_referenced_issues_normalizes() -> None:
    inp = inputs({"a": "- [ ] z @owner:TBD @id:z @blocked_by:prograph-vault#3\n"})
    recs = [record("a", 1, closing_refs=["prograph-vault#5"])]
    got = referenced_issues(recs, inp.todos, normalizer(inp))
    assert {("ecosystem-kb", 3), ("ecosystem-kb", 5)} <= got


def test_body_mentions_are_not_strong_fetch_targets() -> None:
    # живой прогон 2026-09-30: упоминания в телах закрытых issue тянут цепочку
    # дочитывания глубже трёх шагов → вечный partial; на готовность они не
    # влияют (§3.1) — строго дочитываются только структурные ссылки
    from conductor.graph import local_refs

    inp = inputs({})
    recs = [
        record("a", 1, state="closed", body="see b#4"),
        record("a", 2, is_pr=True, title="docs(b#6): plan", body="see b#7"),
    ]
    norm = normalizer(inp)
    assert referenced_issues(recs, inp.todos, norm) == set()
    assert local_refs(recs, norm) == {("b", 6)}


def test_any_unread_source_makes_graph_partial() -> None:
    todos = {"a": "- [ ] x @owner:TBD @id:x\n"}
    assert build_graph(inputs(todos, gh_state="error")).partial
    assert build_graph(inputs(todos, epics_state="error")).partial
    assert build_graph(inputs(todos, roadmap_state="error")).partial
    assert build_graph(inputs(todos, aux_state="error")).partial


def test_inbox_labelled_pr_is_not_glued_to_an_item() -> None:
    # #511: метка inbox на PR не делает его заявкой — PR остаётся в очереди
    pr = record("a", 7, is_pr=True, body="slug: x\nfrom: b#y\n", labels=["inbox"])
    g = build_graph(
        inputs(
            {"a": "- [ ] x @owner:TBD @id:x\n", "b": "- [ ] y @owner:TBD @id:y\n"},
            [pr],
        )
    )
    assert "a!7" not in g.canon
    assert not any(e.dst == "a!7" and e.type == "depends_on" for e in g.edges)


def test_strict_refs_outside_the_fleet_are_findings_not_silence() -> None:
    # #511: чужой репо не дочитывается — ни «предпосылки нет», ни тишины
    g = build_graph(
        inputs(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:other#5\n"
                "- [ ] y @owner:TBD @id:y @blocked_by:todo://other/z\n"
            },
            [record("a", 9, is_pr=True, closing_refs=["other#6"])],
        )
    )
    out = {(f.subject, f.detail) for f in g.findings if f.code == "GR-REF-OUT-OF-FLEET"}
    assert out == {
        ("todo://a/x", "other#5"),
        ("todo://a/y", "todo://other/z"),
        ("a!9", "other#6"),
    }
    deps = {e.src: e.dst for e in g.edges if e.type == "depends_on"}
    assert deps == {
        "todo://a/x": "unresolved:other#5",
        "todo://a/y": "unresolved:todo://other/z",
    }
