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
        {"number": 1},
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
        {"named": [{"path": "docs/plans/x.md", "exists": True, "bytes": 4000}],
         "mentions": []},
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
        {"path": "docs/plans/x.md", "exists": False, "bytes": None}]
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
