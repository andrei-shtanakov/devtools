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
