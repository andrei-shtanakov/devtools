"""Сверка «узел не ослаблен» (lite/node_guard)."""

from lite.node_guard import node_items, weakened

_BEFORE = """# Req

#### FR-01: вход
**Priority**: Must
text

#### FR-02
**Priority**: Should
text

#### NFR-03: скорость
**Priority**: Must
"""


def test_node_items_ids_and_priority() -> None:
    assert node_items(_BEFORE) == {
        "FR-01": "Must",
        "FR-02": "Should",
        "NFR-03": "Must",
    }


def test_weakened_detects_removed_and_downgraded() -> None:
    after = _BEFORE.replace("#### FR-02\n**Priority**: Should\ntext\n", "")
    after = after.replace(
        "NFR-03: скорость\n**Priority**: Must", "NFR-03\n**Priority**: Could"
    )
    assert weakened(_BEFORE, after, set(), set()) == ["FR-02", "NFR-03"]


def test_weakened_allows_chosen_effects() -> None:
    after = _BEFORE.replace("#### FR-02\n**Priority**: Should\ntext\n", "")
    after = after.replace(
        "NFR-03: скорость\n**Priority**: Must", "NFR-03\n**Priority**: Could"
    )
    assert weakened(_BEFORE, after, {"FR-02"}, {"NFR-03"}) == []


def test_node_without_priority_checked_by_ids_only() -> None:
    before = "#### D-1\ntext\n#### D-2\ntext\n"
    assert node_items(before) == {"D-1": None, "D-2": None}
    assert weakened(before, "#### D-1\nnew\n", set(), set()) == ["D-2"]
    assert weakened(before, before + "#### D-3\n", set(), set()) == []


def test_priority_does_not_leak_to_next_heading() -> None:
    text = "#### FR-01\nno prio\n#### FR-02\n**Priority**: Must\n"
    assert node_items(text) == {"FR-01": None, "FR-02": "Must"}


def test_duplicate_heading_is_ambiguous() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    after = before + "#### FR-01\ntext\n"
    assert weakened(before, after, set(), set()) == ["FR-01"]
    assert node_items(after) == {"FR-01": "Must"}


def test_two_priority_lines_are_ambiguous() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    after = "#### FR-01\n**Priority**: Must\n**Priority**: Could\n"
    assert weakened(before, after, set(), set()) == ["FR-01"]


def test_priority_format_variants() -> None:
    text = (
        "#### A-1\n- **Priority**: must\n"
        "#### A-2\nPriority: **Must**\n"
        "#### A-3\n* Priority: Should\n"
    )
    assert node_items(text) == {"A-1": "Must", "A-2": "Must", "A-3": "Should"}
    assert weakened(text, text, set(), set()) == []


def test_unreadable_priority_counts_as_downgrade() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    after = "#### FR-01\nPriority: \n"
    assert weakened(before, after, set(), set()) == ["FR-01"]
    assert weakened(before, after, set(), {"FR-01"}) == []
    assert weakened(before, "#### FR-01\ntext\n", set(), set()) == ["FR-01"]


def test_fenced_blocks_and_comments_are_skipped() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    after = (
        "#### FR-01\n**Priority**: Must\n"
        "```\n#### FR-01\n**Priority**: Could\n```\n"
        "<!--\n#### FR-01\n-->\n"
    )
    assert weakened(before, after, set(), set()) == []
    assert node_items("```\n#### FR-09\n```\n<!-- #### FR-08 -->\n") == {}


def test_id_boundary() -> None:
    assert node_items("#### FR-01-A\n**Priority**: Must\n") == {}
    assert node_items("#### FR-01.\n") == {"FR-01": None}
    assert node_items("#### FR-01: t\n") == {"FR-01": None}
