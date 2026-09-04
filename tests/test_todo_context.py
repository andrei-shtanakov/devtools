"""Offline tests for the todo-context pack.

Everything here runs without a workspace, without git and without gh: the
functions under test are the ones that decide what the pack SAYS, and the
distinction they exist to keep — `absent` vs `not_queried` vs `error` — is only
testable if a missing source and an unconsulted one take different paths.
"""

from __future__ import annotations

from pathlib import Path

import todo_context as tc


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "TODO.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_body_collects_continuation_and_stops_at_next_item(tmp_path):
    directory = _write(tmp_path, "\n".join([
        "# TODO",
        "",
        "- [ ] First @id:one @epic:eco.ops",
        "      Первая строка обоснования.",
        "      Вторая строка.",
        "- [ ] Second @id:two @epic:eco.ops",
        "      Чужое продолжение.",
    ]) + "\n")
    body, source = tc.read_body(directory, {"line": 3})
    assert source.state == "read"
    assert body["lines"] == 2
    assert "Первая строка обоснования." in body["text"]
    assert "Чужое" not in body["text"], "continuation leaked past the next item"


def test_body_stops_at_heading(tmp_path):
    directory = _write(tmp_path, "\n".join([
        "- [ ] Item @id:one",
        "      Своё.",
        "## Следующая секция",
        "      Не своё.",
    ]) + "\n")
    body, _ = tc.read_body(directory, {"line": 1})
    assert body["text"] == "Своё."


def test_body_absent_is_not_error(tmp_path):
    directory = _write(tmp_path, "- [ ] Item @id:one\n- [ ] Other @id:two\n")
    body, source = tc.read_body(directory, {"line": 1})
    assert body["text"] is None
    assert source.state == "absent", "a body that is not there is absent, not an error"


def test_body_unreadable_repo_is_error_not_absent():
    body, source = tc.read_body(None, {"line": 1})
    assert body["text"] is None
    assert source.state == "error", "an unread source must never look empty-but-read"


def test_named_doc_paths_reads_section_and_line_without_duplicates():
    item = {
        "section": "Waits graph "
                   "(спека docs/superpowers/specs/2026-08-26-waits-graph-design.md)",
        "source_line": "Сделать по "
                       "docs/superpowers/specs/2026-08-26-waits-graph-design.md "
                       "и docs/plans/x.md @id:w",
    }
    assert tc.named_doc_paths(item) == [
        "docs/superpowers/specs/2026-08-26-waits-graph-design.md",
        "docs/plans/x.md",
    ]


def test_named_doc_paths_empty_when_nothing_written():
    item = {"section": "Ops", "source_line": "- [ ] fix it @id:x"}
    assert tc.named_doc_paths(item) == []


def _sources(**states) -> list[tc.Source]:
    return [tc.Source(name, state) for name, state in states.items()]


def _epic(goal: str = "зачем существует поток") -> dict:
    """An epic block as `read_epic` builds it — only `goal` matters to the grade."""
    return {"id": "eco.x", "goal": goal, "notes": None}


def _docs(*paths: str, ref: bool = True) -> dict:
    """A docs block as `read_docs` builds it: `doc` means the path can hold a
    requirement AND the line refers to the item canonically."""
    return {"named": [], "mentions": [{"path": p, "line": "1", "text": "",
                                       "doc": tc.is_doc_mention(p) and ref}
                                      for p in paths]}


def test_grade_rich_on_substantial_body():
    verdict = tc.grade(
        _sources(item="read", body="read", epic="read", docs="absent",
                 origin_issue="not_queried"),
        {"text": "x" * (tc._BODY_SUBSTANTIAL + 1)},
        None,
    )
    assert verdict["grade"] == "rich"
    assert verdict["execute_allowed"] is True


def test_grade_thin_when_only_the_epic_is_known():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="absent",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        None,
        _epic(),
    )
    assert verdict["grade"] == "thin"
    assert verdict["execute_allowed"] is False, \
        "an epic goal is not a requirement for one item"


def test_grade_bare_without_epic():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="absent", docs="absent",
                 origin_issue="not_queried"),
        {"text": None},
        None,
    )
    assert verdict["grade"] == "bare"
    assert verdict["execute_allowed"] is False


def test_grade_short_body_does_not_count_as_a_requirement():
    verdict = tc.grade(
        _sources(item="read", body="read", epic="read", docs="absent",
                 origin_issue="not_queried"),
        {"text": "мелкая ремарка"},
        None,
        None,
        _epic(),
    )
    assert verdict["grade"] == "thin"


def test_grade_names_the_sources_it_did_not_read():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="error",
                 origin_issue="not_queried"),
        {"text": None},
        None,
    )
    assert verdict["unknown_sources"] == ["docs", "origin_issue"]
    assert verdict["note"], "unknown sources must be called out, not implied"


def test_grade_note_absent_when_everything_was_read():
    verdict = tc.grade(
        _sources(item="read", body="read", epic="read", docs="read",
                 origin_issue="read"),
        {"text": "x" * 200},
        {"number": 1, "body": "т" * 200},
        _docs("docs/plans/x.md"),
    )
    assert verdict["unknown_sources"] == []
    assert verdict["note"] is None


def test_origin_issue_matches_by_slug_on_the_item_line():
    issues = [
        {"repository": {"name": "maestro"}, "number": 7, "title": "t",
         "body": "slug: benchmark-2\nfrom: arbiter#gate\n"},
    ]
    item = {"repo": "maestro",
            "source_line": "- [ ] Run the sweep benchmark-2 @owner:o @id:sweep"}
    found = tc.match_origin_issue(issues, item)
    assert found is not None and found["number"] == 7 and found["slug"] == "benchmark-2"


def test_origin_issue_not_matched_when_slug_is_elsewhere():
    issues = [{"repository": {"name": "maestro"}, "number": 7, "title": "t",
               "body": "slug: benchmark-3\n"}]
    item = {"repo": "maestro",
            "source_line": "- [ ] Run the sweep benchmark-2 @id:sweep"}
    assert tc.match_origin_issue(issues, item) is None


def test_parse_uri_roundtrip_and_refusal():
    assert tc.parse_uri("todo://dispatcher/waits-graph-view") == (
        "dispatcher", "waits-graph-view")
    try:
        tc.parse_uri("dispatcher#waits-graph-view")
    except tc.ContextError:
        return
    raise AssertionError("a legacy <repo>#<slug> ref must not be read as a todo:// uri")


def test_source_refuses_a_state_outside_the_vocabulary():
    """`read`/`absent`/`not_queried`/`error` is the whole vocabulary the grade
    reads; a typo'd fifth state would silently grade as an unknown source."""
    for state in tc.SOURCE_STATES:
        assert tc.Source("docs", state).state == state
    try:
        tc.Source("docs", "missing")
    except ValueError:
        return
    raise AssertionError("an unknown source state must not be constructible")


# ─────────── регрессии первого круга ревью PR #125 ───────────


def test_doc_mention_is_only_a_mention_where_a_requirement_can_live():
    """A `git grep` hit is a bare substring match: the id turns up in branch
    names, CLI literals and tests as readily as in a design doc."""
    assert tc.is_doc_mention("docs/superpowers/specs/2026-08-26-waits-design.md")
    assert tc.is_doc_mention("workstreams/WS-SMOKE-001/spec/10-requirements.md")
    assert not tc.is_doc_mention("spec_run_preflight.py"), "a print() literal"
    assert not tc.is_doc_mention("tests/test_todo_context.py")
    assert not tc.is_doc_mention("README.md"), "outside a docs/spec/plan dir"


def test_grade_does_not_execute_on_a_mention_outside_the_docs():
    """The finding itself: an item with no body, no epic and no named doc used to
    reach `execute_allowed` because its id appeared in a branch name."""
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="absent", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        _docs("todo_context.py", "tests/test_todo_context.py"),
    )
    assert verdict["grade"] == "bare", "a code mention graded as a requirement"
    assert verdict["execute_allowed"] is False


def test_grade_executes_on_a_mention_inside_a_design_doc():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        _docs("docs/superpowers/specs/2026-08-26-waits-design.md"),
    )
    assert verdict["grade"] == "rich"
    assert verdict["execute_allowed"] is True


def test_grade_executes_on_a_named_doc_that_exists():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        {"named": [{"path": "docs/plans/x.md", "exists": True, "bytes": 4000,
                    "named_in": "line"}], "mentions": []},
    )
    assert verdict["execute_allowed"] is True


def test_grade_ignores_a_named_doc_that_is_not_there():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        {"named": [{"path": "docs/plans/gone.md", "exists": False, "bytes": None}],
         "mentions": []},
        _epic(),
    )
    assert verdict["grade"] == "thin", "a dead path is not a written requirement"


def test_origin_issue_ignores_an_issue_filed_in_another_repo():
    """The fleet writes OUTGOING requests into an item's own line ("заведён
    disputatio#52 (slug: …)"). Matched without the repo check, that outgoing wait
    came back as this item's own requirement, direction reversed."""
    issues = [{"repository": {"name": "disputatio"}, "number": 52, "title": "t",
               "body": "slug: single-document-polish-mode\n"}]
    item = {"repo": "devtools",
            "source_line": "- [x] inbox-issue в disputatio … заведён disputatio#52 "
                           "(slug: single-document-polish-mode) @id:disp-issue"}
    assert tc.match_origin_issue(issues, item) is None


def test_origin_issue_matches_within_the_same_repo():
    issues = [{"repository": {"name": "Maestro"}, "number": 7, "title": "t",
               "body": "slug: benchmark-2\n"}]
    item = {"repo": "maestro",
            "source_line": "- [ ] Run the sweep benchmark-2 @id:sweep"}
    found = tc.match_origin_issue(issues, item)
    assert found is not None and found["number"] == 7, "repo compared case-sensitively"


def test_rules_cap_is_measured_in_bytes(tmp_path):
    """`_RULES_CAP` is documented as bytes; these files are mostly Cyrillic, so
    cutting by character inlined ~1.4x the cap and misreported `truncated`."""
    (tmp_path / "CLAUDE.md").write_text("я" * tc._RULES_CAP, encoding="utf-8")
    rules, source = tc.read_rules(tmp_path)
    assert source.state == "read"
    assert rules[0]["bytes"] == tc._RULES_CAP * 2, "two bytes per Cyrillic char"
    assert rules[0]["truncated"] is True
    assert len(rules[0]["text"].encode("utf-8")) <= tc._RULES_CAP
    assert "\ufffd" not in rules[0]["text"], "a split codepoint must not leak"


# ─────────── регрессии второго круга ревью PR #125 ───────────


def test_item_reference_is_a_reference_not_a_substring():
    """`git grep --fixed-strings` matches inside a longer id, a component name,
    a branch name and a file name; only `@id:` and `todo://` point AT an item."""
    ref = tc.item_ref_re("behaviour-runner")
    assert ref.search("- [ ] TODO: `@id:behaviour-runner` → `[x]`")
    assert ref.search("ждёт todo://devtools/behaviour-runner")
    assert not ref.search("git switch -c feat/behaviour-runner-core"), "branch"
    assert not ref.search("Charter: Наблюдаемость прогонов behaviour-runner"), \
        "имя компонента"
    assert not ref.search("@id:behaviour-runner-core"), "id длиннее — другой пункт"
    assert not ref.search("docs/plans/2026-08-30-behaviour-runner-core.md"), \
        "имя файла"


def test_item_reference_reads_a_code_span_as_a_reference():
    """The fleet also points at an item by spelling its id in a code span:
    `## P1 — сократить промпт (`review-kit-prompt-diet`)` is a requirement, and
    the first cut of this rule dropped it."""
    ref = tc.item_ref_re("review-kit-prompt-diet")
    assert ref.search("## P1 — сократить промпт (`review-kit-prompt-diet`)")
    assert not ref.search("сократить промпт review-kit-prompt-diet"), "проза"
    assert not ref.search("`review-kit-prompt-diet-v2`"), "код-спан другого id"


def test_item_reference_reads_a_dot_as_punctuation_not_as_the_id():
    """A dot is legal inside an id AND ends a sentence: it continues the id only
    when something id-shaped follows."""
    ref = tc.item_ref_re("rd-007")
    assert ref.search("см. @id:rd-007."), "точка в конце фразы — не часть id"
    assert not ref.search("@id:rd-0071")
    assert not ref.search("@id:rd-007.2"), "точка перед id-символом продолжает id"


def test_docs_marks_only_canonical_references_in_doc_paths(tmp_path, monkeypatch):
    """The whole chain: a doc that merely contains the id must not grade."""
    hits = [
        {"path": "docs/plans/a.md", "line": "1", "text": "…",
         "full": "  ждёт @id:my-item — план"},
        {"path": "docs/plans/b.md", "line": "9", "text": "…",
         "full": "git switch -c feat/my-item"},
        {"path": "todo_context.py", "line": "3", "text": "…",
         "full": 'print("@id:my-item")'},
    ]
    for rel in ("docs/plans/a.md", "docs/plans/b.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("требование. " * 30, encoding="utf-8")
    monkeypatch.setattr(tc, "git_grep", lambda directory, needle: (hits, None))
    item = {"id": "my-item", "source_line": "", "section": ""}
    docs, source = tc.read_docs(tmp_path, item)
    assert source.state == "read"
    marked = {h["path"]: h["doc"] for h in docs["mentions"]}
    assert marked == {"docs/plans/a.md": True, "docs/plans/b.md": False,
                      "todo_context.py": False}
    assert all("full" not in h for h in docs["mentions"]), \
        "рабочее поле не течёт в pack"


def test_docs_error_branch_keeps_the_shape_grade_and_render_expect():
    """`named` was a list of strings in the error branch — grade raised
    AttributeError instead of degrading honestly."""
    item = {"id": "x", "section": "Ops (docs/plans/x.md)", "source_line": ""}
    docs, source = tc.read_docs(None, item)
    assert source.state == "error"
    assert docs["named"] == [
        {"path": "docs/plans/x.md", "exists": False, "bytes": None,
         "named_in": "section"}]
    verdict = tc.grade(_sources(item="read", docs="error"), {"text": None}, None, docs)
    assert verdict["execute_allowed"] is False


def test_graph_says_when_the_reverse_side_could_not_be_read():
    """A repo that is not cloned here is skipped whole by `parse_fleet`, so its
    `@blocked_by` on this item is neither an edge nor a diagnostic."""
    snapshot = {"nodes": [{"node_id": "todo://devtools/x", "id": "x",
                           "repo": "devtools", "title": "t",
                           "declared_status": "open"}],
                "edges": [], "references": [], "diagnostics": []}
    graph, source = tc.read_graph(snapshot, "todo://devtools/x", ["maestro", "arbiter"])
    assert graph["unread_repos"] == ["arbiter", "maestro"]
    assert source.state == "read"
    assert source.detail and "arbiter, maestro" in source.detail
    assert "нет рёбер" not in (source.detail or "")


def test_graph_stays_silent_when_the_whole_fleet_was_read():
    snapshot = {"nodes": [{"node_id": "todo://devtools/x", "id": "x",
                           "repo": "devtools", "title": "t",
                           "declared_status": "open"}],
                "edges": [], "references": [], "diagnostics": []}
    graph, source = tc.read_graph(snapshot, "todo://devtools/x", [])
    assert graph["unread_repos"] == []
    assert source.detail is None, "полный флот не нуждается в оговорке"


# ─────────── регрессии третьего круга ревью PR #125 ───────────


def test_grade_ignores_an_empty_named_doc():
    """A path committed ahead of the writing is normal; an empty stub is not a
    requirement. Reading `exists` alone put "пустое выглядит зелёным" back on
    the docs side, where `_BODY_SUBSTANTIAL` already guards the body."""
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        {"named": [{"path": "docs/plans/stub.md", "exists": True, "bytes": 0}],
         "mentions": []},
        _epic(),
    )
    assert verdict["grade"] == "thin"
    assert verdict["execute_allowed"] is False


def test_grade_accepts_a_named_doc_that_was_actually_written():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="read", docs="read",
                 origin_issue="not_queried"),
        {"text": None},
        None,
        {"named": [{"path": "docs/plans/x.md", "exists": True,
                    "bytes": tc._DOC_SUBSTANTIAL}], "mentions": []},
    )
    assert verdict["execute_allowed"] is True


class _FakeIndex:
    """Just the one thing `build_pack` asks of a manifest index."""

    canonical_keys = ("maestro",)

    def resolve_ref(self, ref):
        return "maestro" if ref.lower() in ("maestro", "maestro-wt") else None


def _fake_snapshot():
    node = {"node_id": "todo://maestro/x", "id": "x", "repo": "maestro",
            "title": "t", "declared_status": "open", "raw": {},
            "provenance": {"path": "TODO.md", "line": 1}}
    return ({"nodes": [node], "edges": [], "references": [], "diagnostics": []},
            {}, [], _FakeIndex())


def test_build_pack_normalises_the_repo_spelling(monkeypatch, tmp_path):
    """`parse_fleet` keys nodes canonically, so `todo://Maestro/x` used to be
    refused as "no such item" for an item that is right there."""
    monkeypatch.setattr(tc, "fleet_snapshot", lambda root, manifest: _fake_snapshot())
    monkeypatch.setattr(tc, "read_origin_issue",
                        lambda item, owner: (None, tc.Source("origin_issue",
                                                             "not_queried", "x")))
    pack = tc.build_pack(tmp_path, tmp_path, tmp_path, "Maestro", "x")
    assert pack["node_id"] == "todo://maestro/x"
    assert pack["item"]["repo"] == "maestro"


def test_build_pack_keeps_an_unknown_spelling_verbatim(monkeypatch, tmp_path):
    """An unresolvable name must fail as itself, not as a silently rewritten one."""
    monkeypatch.setattr(tc, "fleet_snapshot", lambda root, manifest: _fake_snapshot())
    try:
        tc.build_pack(tmp_path, tmp_path, tmp_path, "no-such-repo", "x")
    except tc.ContextError as exc:
        assert "todo://no-such-repo/x" in str(exc)
        return
    raise AssertionError("an unknown repo must not resolve to something else")


def test_render_survives_a_pack_with_nothing_in_it(monkeypatch, tmp_path):
    """`render` was untested, and the docs error branch would have crashed it."""
    monkeypatch.setattr(tc, "fleet_snapshot", lambda root, manifest: _fake_snapshot())
    monkeypatch.setattr(tc, "read_origin_issue",
                        lambda item, owner: (None, tc.Source("origin_issue",
                                                             "not_queried", "x")))
    text = tc.render(tc.build_pack(tmp_path, tmp_path, tmp_path, "maestro", "x"))
    assert "# todo://maestro/x" in text
    assert "## Completeness" in text
    assert "execute_allowed: **False**" in text


def test_identity_clash_is_a_message_not_a_traceback(monkeypatch, tmp_path):
    """`checkout_map` decides identity too, so it raises the same error as
    `manifest_index` — and only the latter was wrapped."""
    class _Boom(Exception):
        pass

    class _FakePf:
        AmbiguousIdentityError = _Boom

        @staticmethod
        def manifest_index(path):
            raise _Boom("two checkouts resolve to `maestro`")

    (tmp_path / "manifest.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(tc, "_pf", _FakePf)
    try:
        tc.fleet_snapshot(tmp_path, tmp_path / "manifest.toml")
    except tc.ContextError as exc:
        assert "cannot resolve repo identity" in str(exc)
        return
    raise AssertionError("an identity clash must not reach the user as a traceback")


# ─────────── регрессии четвёртого круга ревью PR #125 ───────────


def test_grade_ignores_an_issue_with_no_requirement_in_it():
    """`inbox` deliberately does not require a body, so `slug:` + `from:` is a
    valid request — valid, but not a written requirement."""
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="absent", docs="absent",
                 origin_issue="read"),
        {"text": None},
        {"number": 7, "body": "slug: foo-slug\nfrom: arbiter#gate\n"},
        None,
    )
    assert verdict["grade"] == "bare"
    assert verdict["execute_allowed"] is False


def test_grade_accepts_an_issue_that_states_a_requirement():
    verdict = tc.grade(
        _sources(item="read", body="absent", epic="absent", docs="absent",
                 origin_issue="read"),
        {"text": None},
        {"number": 7, "body": "slug: foo\n\n" + "требование. " * 20},
        None,
    )
    assert verdict["execute_allowed"] is True


def test_grade_needs_the_epic_to_actually_state_a_goal():
    """`epic: read` is presence; an epic whose goal is empty says nothing about
    the stream, so it cannot be the difference between thin and bare."""
    sources = _sources(item="read", body="absent", epic="read", docs="absent",
                       origin_issue="not_queried")
    assert tc.grade(sources, {"text": None}, None, None,
                    {"id": "e", "goal": None, "notes": None})["grade"] == "bare"
    assert tc.grade(sources, {"text": None}, None, None,
                    {"id": "e", "goal": "зачем поток"})["grade"] == "thin"


def test_unknown_epic_is_absent_not_error(tmp_path):
    """The registry WAS read: calling this `error` put it in unknown_sources
    under a note saying it was never read."""
    registry = tmp_path / "epics.toml"
    registry.write_text('[epics."eco.real"]\ngoal = "g"\n', encoding="utf-8")
    epic, source = tc.read_epic(registry, "eco.typo")
    assert epic is None
    assert source.state == "absent", "прочитанный реестр — не непрочитанный источник"
    assert "EP-UNKNOWN" in (source.detail or "")
    verdict = tc.grade([source], {"text": None}, None, None, None)
    assert "epic" not in verdict["unknown_sources"]


def test_unreadable_registry_stays_an_error(tmp_path):
    epic, source = tc.read_epic(tmp_path / "gone.toml", "eco.real")
    assert epic is None and source.state == "error"


def test_origin_issue_pair_follows_inbox_and_marks_a_prefix_collision():
    """The pair is one derived fact (ADR-ECO-006 D9) and `inbox.is_accepted` owns
    the test, so this must not answer differently — it may only refuse to call a
    prefix collision a requirement."""
    issues = [{"repository": {"name": "maestro"}, "number": 7, "title": "t",
               "body": "slug: benchmark-2\n" + "требование. " * 20}]
    item = {"repo": "maestro",
            "source_line": "- [ ] Прогнать benchmark-20 @id:sweep-20"}
    found = tc.match_origin_issue(issues, item)
    assert found is not None, "пара — общий факт с inbox, здесь её не переопределяют"
    assert found["exact"] is False
    verdict = tc.grade(_sources(item="read", origin_issue="read"),
                       {"text": None}, found, None, None)
    assert verdict["execute_allowed"] is False, "коллизия по префиксу — не требование"

    item["source_line"] = "- [ ] Прогнать benchmark-2 @id:sweep"
    exact = tc.match_origin_issue(issues, item)
    assert exact is not None and exact["exact"] is True
    assert tc.grade(_sources(item="read", origin_issue="read"),
                    {"text": None}, exact, None, None)["execute_allowed"] is True


def test_docs_keeps_every_reference_when_the_output_is_capped(monkeypatch, tmp_path):
    """The display cap used to be applied to raw stdout, so a canonical reference
    past hit 40 was invisible to the grade while docs still reported plain `read`."""
    noise = [{"path": f"src/f{i}.py", "line": "1", "text": "…",
              "full": "my-item"} for i in range(tc._GREP_CAP + 20)]
    ref = {"path": "docs/plans/late.md", "line": "1", "text": "…",
           "full": "ждёт @id:my-item"}
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "late.md").write_text(
        "требование. " * 30, encoding="utf-8")
    monkeypatch.setattr(tc, "git_grep",
                        lambda directory, needle: (noise + [ref], None))
    docs, source = tc.read_docs(tmp_path, {"id": "my-item", "source_line": "",
                                           "section": ""})
    assert len(docs["mentions"]) == tc._GREP_CAP
    assert docs["mentions"][0]["path"] == "docs/plans/late.md", "ссылка не выброшена"
    assert docs["hidden_mentions"] == 21
    assert source.detail and "не показаны" in source.detail
    verdict = tc.grade([source], {"text": None}, None, docs, None)
    assert verdict["execute_allowed"] is True


# ─────────── регрессии пятого круга ревью PR #125 ───────────


def test_several_matching_issues_are_not_silently_resolved_by_gh_order():
    """Which of two open requests states the requirement is unknown; picking by
    the order `gh` returned them is a coin toss printed as a fact."""
    body = "требование. " * 20
    issues = [
        {"repository": {"name": "maestro"}, "number": 7, "title": "a",
         "body": "slug: alpha\n" + body},
        {"repository": {"name": "maestro"}, "number": 9, "title": "b",
         "body": "slug: beta\n" + body},
    ]
    item = {"repo": "maestro", "source_line": "- [ ] alpha и beta @id:x"}
    found = tc.match_origin_issue(issues, item)
    assert found["rival_issues"] == [9]
    verdict = tc.grade(_sources(item="read", origin_issue="read"),
                       {"text": None}, found, None, None)
    assert verdict["execute_allowed"] is False


def test_a_stub_doc_mentioning_the_item_is_not_a_requirement(tmp_path, monkeypatch):
    """A doc holding one `@id:` line is a placeholder — the same floor a NAMED
    doc already answered to."""
    stub = tmp_path / "docs" / "plans"
    stub.mkdir(parents=True)
    (stub / "stub.md").write_text("@id:my-item\n", encoding="utf-8")
    (stub / "real.md").write_text("@id:my-item\n" + "требование. " * 30,
                                  encoding="utf-8")
    hits = [{"path": "docs/plans/stub.md", "line": "1", "text": "…",
             "full": "@id:my-item"}]
    monkeypatch.setattr(tc, "git_grep", lambda directory, needle: (hits, None))
    docs, _ = tc.read_docs(tmp_path, {"id": "my-item", "source_line": "",
                                      "section": ""})
    assert docs["mentions"][0]["doc"] is False

    hits[0]["path"] = "docs/plans/real.md"
    hits[0]["full"] = "@id:my-item"
    docs, _ = tc.read_docs(tmp_path, {"id": "my-item", "source_line": "",
                                      "section": ""})
    assert docs["mentions"][0]["doc"] is True


def test_git_grep_survives_non_utf8_in_a_sibling_repo(tmp_path):
    """One non-UTF-8 file in a neighbour must give `docs: error` at worst, never
    a UnicodeDecodeError out of the pack."""
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "bad.txt").write_bytes(b"my-item \xff\xfe binary\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    hits, error = tc.git_grep(tmp_path, "my-item")
    assert error is None, f"грep не должен падать на бинарной строке: {error}"
    assert isinstance(hits, list)


# ─────────── регрессии шестого круга ревью PR #125 ───────────


def test_named_doc_sources_say_where_the_path_was_written():
    item = {"source_line": "- [ ] сделать по docs/plans/own.md @id:x",
            "section": "Waits (спека docs/specs/section.md)"}
    assert tc.named_doc_sources(item) == [("docs/plans/own.md", "line"),
                                          ("docs/specs/section.md", "section")]
    assert tc.named_doc_paths(item) == ["docs/plans/own.md",
                                        "docs/specs/section.md"]


def test_a_section_doc_does_not_grant_execute_to_every_item_under_it():
    """A doc named in the section heading is about the SECTION: it was handing a
    written requirement to every item below, none of which named it."""
    section = {"named": [{"path": "docs/specs/s.md", "exists": True,
                          "bytes": 9000, "named_in": "section"}], "mentions": []}
    own = {"named": [{"path": "docs/plans/own.md", "exists": True,
                      "bytes": 9000, "named_in": "line"}], "mentions": []}
    sources = _sources(item="read", body="absent", epic="read", docs="read",
                       origin_issue="not_queried")
    assert tc.grade(sources, {"text": None}, None, section,
                    _epic())["execute_allowed"] is False
    assert tc.grade(sources, {"text": None}, None, own,
                    _epic())["execute_allowed"] is True


def test_the_cap_note_does_not_claim_every_reference_is_shown(monkeypatch, tmp_path):
    """With more references than the display cap, "все ссылки показаны" was false."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    hits = []
    for i in range(tc._GREP_CAP + 5):
        rel = f"docs/plans/d{i}.md"
        (tmp_path / rel).write_text("требование. " * 30, encoding="utf-8")
        hits.append({"path": rel, "line": "1", "text": "…", "full": "@id:my-item"})
    monkeypatch.setattr(tc, "git_grep", lambda directory, needle: (hits, None))
    docs, source = tc.read_docs(tmp_path, {"id": "my-item", "source_line": "",
                                           "section": ""})
    assert docs["hidden_references"] == 5
    assert "ссылок на пункт" in (source.detail or "")
    assert "показаны все" not in (source.detail or "")


# ─────────── регрессии седьмого круга ревью PR #125 ───────────


def test_a_checklist_line_quoting_the_id_states_no_requirement():
    """behaviour-console.md:156-159 — the item's future `TODO.md` line quoted
    inside the plan of a DIFFERENT item. It says nothing about this item."""
    lines = [
        "### Task 5: Обвязка и финал",
        "",
        "- [ ] TODO: `@id:behaviour-runner` → `[x]`;",
        "  подпункт про inbox-issue disputatio — новой",
        "  строкой `- [ ] inbox-issue в disputatio: режим полировки документа",
        "  (OQ-1) @owner:github:andrei-shtanakov @id:disp-document-mode-issue`.",
        "- [ ] Финальные проверки: py_compile всех governance/*.py и прочее.",
    ]
    assert tc.mention_states_a_requirement(lines, 6) is False, "продолжение чеклиста"
    assert tc.mention_states_a_requirement(lines, 3) is False, "сама чеклист-строка"


def test_a_section_naming_the_item_states_a_requirement():
    """The roadmap's "## P1 — сократить промпт (`review-kit-prompt-diet`)" is a
    requirement: a heading naming the item over a section that says something."""
    lines = [
        "## P1 — сократить промпт (`review-kit-prompt-diet`)",
        "",
        "Промпт кита разросся до полутора тысяч слов, из-за чего ревьюер теряет",
        "порядок разделов и путает пороги. Сократить до четырёх разделов и",
        "вынести пороги в отдельный блок, сверяемый тестом.",
    ]
    assert tc.mention_states_a_requirement(lines, 1) is True


def test_a_heading_over_an_empty_section_states_nothing():
    lines = ["## Заглушка (`my-item`)", "", "## Следующая секция", "текст" * 50]
    assert tc.mention_states_a_requirement(lines, 1) is False


def test_graph_names_legacy_waits_that_never_become_edges():
    """`<repo>#<slug>` never becomes an edge and raises no diagnostic when it
    matches exactly one item — so the wait is invisible to this slice entirely."""
    snapshot = {
        "nodes": [{"node_id": "todo://devtools/x", "id": "x", "repo": "devtools",
                   "title": "t", "declared_status": "open"}],
        "edges": [], "diagnostics": [],
        "references": [
            {"kind": "blocked_by", "source_node_id": "todo://steward/waits",
             "raw_ref": "devtools#x", "resolved_target": None,
             "legacy_blocker_ref": "devtools#x"},
            {"kind": "blocked_by", "source_node_id": "todo://maestro/other",
             "raw_ref": "disputatio#68", "resolved_target": None,
             "legacy_blocker_ref": "disputatio#68"},
        ],
    }
    graph, source = tc.read_graph(snapshot, "todo://devtools/x", [])
    assert [w["raw_ref"] for w in graph["legacy_waits"]] == ["devtools#x"], \
        "чужие переходные ожидания сюда не относятся"
    assert graph["legacy_waits"][0]["names_this_item"] is True
    assert graph["blocks"] == [], "переходная форма ребром не становится"
    assert source.detail and "переходные ожидания" in source.detail


def test_graph_reports_a_legacy_wait_to_the_repo_without_resolving_the_slug():
    """Pairing a slug with an item is the package's rule; a private one here
    would be the round-5 mistake again. So a same-repo legacy ref is reported as
    a candidate, and only an exact id match is called out as naming this item."""
    snapshot = {
        "nodes": [{"node_id": "todo://devtools/x", "id": "x", "repo": "devtools",
                   "title": "t", "declared_status": "open"}],
        "edges": [], "diagnostics": [],
        "references": [{"kind": "blocked_by", "source_node_id": "todo://steward/w",
                        "raw_ref": "devtools#some-other-slug",
                        "resolved_target": None,
                        "legacy_blocker_ref": "devtools#some-other-slug"}],
    }
    graph, source = tc.read_graph(snapshot, "todo://devtools/x", [])
    assert graph["legacy_waits"][0]["names_this_item"] is False
    assert source.detail, "неполнота названа, даже когда слаг не про этот пункт"


def test_pack_carries_the_checkout_directory(monkeypatch, tmp_path):
    """`checkout` is a cross-module contract: `todo_worker` runs the harness in
    that directory, and without it the run would inherit devtools' own cwd."""
    checkout = tmp_path / "maestro"
    checkout.mkdir()
    node = {"node_id": "todo://maestro/x", "id": "x", "repo": "maestro",
            "title": "t", "declared_status": "open", "raw": {},
            "provenance": {"path": "TODO.md", "line": 1}}
    snapshot = {"nodes": [node], "edges": [], "references": [], "diagnostics": []}

    class _Index:
        canonical_keys = ("maestro",)

        def resolve_ref(self, ref):
            return "maestro"

    monkeypatch.setattr(tc, "fleet_snapshot",
                        lambda root, manifest: (snapshot, {"maestro": checkout},
                                                [], _Index()))
    monkeypatch.setattr(tc, "read_origin_issue",
                        lambda item, owner: (None, tc.Source("origin_issue",
                                                             "not_queried", "x")))
    pack = tc.build_pack(tmp_path, tmp_path, tmp_path, "maestro", "x")
    assert pack["checkout"] == str(checkout)


def test_pack_says_none_when_the_repo_is_not_checked_out(monkeypatch, tmp_path):
    """A missing checkout must be `None`, not the caller's directory: the
    consumer refuses on `None` and would have silently used its own cwd."""
    node = {"node_id": "todo://maestro/x", "id": "x", "repo": "maestro",
            "title": "t", "declared_status": "open", "raw": {},
            "provenance": {"path": "TODO.md", "line": 1}}
    snapshot = {"nodes": [node], "edges": [], "references": [], "diagnostics": []}

    class _Index:
        canonical_keys = ("maestro",)

        def resolve_ref(self, ref):
            return "maestro"

    monkeypatch.setattr(tc, "fleet_snapshot",
                        lambda root, manifest: (snapshot, {}, [], _Index()))
    monkeypatch.setattr(tc, "read_origin_issue",
                        lambda item, owner: (None, tc.Source("origin_issue",
                                                             "not_queried", "x")))
    pack = tc.build_pack(tmp_path, tmp_path, tmp_path, "maestro", "x")
    assert pack["checkout"] is None
