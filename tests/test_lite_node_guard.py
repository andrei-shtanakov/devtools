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
        "#### A-2\n**Priority**: **Must** (core)\n"
        "#### A-3\n* **Priority**: Should\n"
        "#### A-4\n**Priority**: Must.\n"
        "#### A-5\n**Priority**: must \u2014 always\n"
    )
    assert node_items(text) == {
        "A-1": "Must",
        "A-2": "Must",
        "A-3": "Should",
        "A-4": "Must",
        "A-5": "Must",
    }
    assert weakened(text, text, set(), set()) == []


def test_plain_priority_prose_does_not_count() -> None:
    text = "#### A-1\n**Priority**: Must\nPriority: Could (prose)\n"
    assert node_items(text) == {"A-1": "Must"}
    assert weakened(text, text, set(), set()) == []


def test_unreadable_priority_value_is_reported() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    for bad in ("**Priority**:", "**Priority**: ***", "**Priority**: (Must)"):
        after = f"#### FR-01\n{bad}\n"
        assert weakened(before, after, set(), set()) == ["FR-01"], bad
    after = "#### FR-01\n**Priority**:\n"
    assert weakened(before, after, set(), {"FR-01"}) == []
    assert weakened(before, "#### FR-01\ntext\n", set(), set()) == ["FR-01"]


def test_fenced_and_commented_lines_fail_closed() -> None:
    before = "#### FR-01\n**Priority**: Must\n"
    fenced = before + "```\n**Priority**: Could\n```\n"
    commented = before + "<!-- **Priority**: Could -->\n"
    assert weakened(before, fenced, set(), set()) == ["FR-01"]
    assert weakened(before, commented, set(), set()) == ["FR-01"]
    hidden = before + "```\n#### FR-01\n```\n"
    assert weakened(before, hidden, set(), set()) == ["FR-01"]


def test_ids_follow_canonical_counter() -> None:
    assert node_items("#### FR-01a\n**Priority**: Must\n") == {"FR-01a": "Must"}
    assert node_items("#### AB2-7: t\n") == {"AB2-7": None}
    before = "#### FR-01\n**Priority**: Must\n"
    after = "#### FR-01,\n**Priority**: Must\n"
    assert weakened(before, after, set(), set()) == ["FR-01"]
    after2 = "#### FR-01)\n**Priority**: Must\n#### FR-02\n"
    assert weakened(before, after2, set(), set()) == ["FR-01"]


def test_id_boundary() -> None:
    assert node_items("#### FR-01-A\n**Priority**: Must\n") == {}
    assert node_items("#### FR-01 .\n") == {"FR-01": None}
    assert node_items("#### FR-01: t\n") == {"FR-01": None}


def test_loose_heading_boundary_is_not_dropped() -> None:
    text = "#### FR-01\n**Priority**: Must\n#### FR-01.\n**Priority**: Could\n"
    assert weakened(text, text, set(), set()) == ["FR-01"]


def test_heading_forms_parity_with_counter() -> None:
    base = "#### FR-01\n**Priority**: Must\n"
    forms = {
        "#### FR-01": True,
        "#### FR-01: t": True,
        "#### FR-01.": False,
        "#### FR-01,": False,
        "#### FR-01)": False,
        "#### FR-01-A": None,
    }
    for heading, counted in forms.items():
        text = f"{base}{heading}\n**Priority**: Could\n"
        flagged = weakened(base, text, set(), set()) == ["FR-01"]
        if counted is None:
            # не id вовсе: счётчик и разделитель оба его игнорируют
            assert node_items(text) == {"FR-01": "Must"}, heading
            continue
        # и счётный дубль, и расхождение границ обязаны быть замечены
        assert flagged, heading
