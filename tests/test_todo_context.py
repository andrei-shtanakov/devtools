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
    )
    assert verdict["unknown_sources"] == []
    assert verdict["note"] is None


def test_origin_issue_matches_by_slug_on_the_item_line():
    issues = [
        {"repository": {"name": "maestro"}, "number": 7, "title": "t",
         "body": "slug: benchmark-2\nfrom: arbiter#gate\n"},
    ]
    item = {"source_line": "- [ ] Run the sweep benchmark-2 @owner:o @id:sweep"}
    found = tc.match_origin_issue(issues, item)
    assert found is not None and found["number"] == 7 and found["slug"] == "benchmark-2"


def test_origin_issue_not_matched_when_slug_is_elsewhere():
    issues = [{"repository": {"name": "maestro"}, "number": 7, "title": "t",
               "body": "slug: benchmark-3\n"}]
    item = {"source_line": "- [ ] Run the sweep benchmark-2 @id:sweep"}
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
