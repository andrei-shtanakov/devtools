"""Сверка «узел не ослаблен» (lite/node_guard): разбор — парсерами гейта."""

import pytest

from lite.node_guard import node_items, weakened

_BEFORE = """# Req

#### FR-01: вход
**Priority**: Must
text

#### FR-02: выход
**Priority**: Should
text

#### NFR-03: скорость
**Priority**: Must
"""

_FR02 = "#### FR-02: выход\n**Priority**: Should\ntext\n"
_NFR03 = "NFR-03: скорость\n**Priority**: Must"


def test_node_items_ids_and_priority() -> None:
    assert node_items(_BEFORE) == {
        "FR-01": "Must",
        "FR-02": "Should",
        "NFR-03": "Must",
    }


def test_weakened_detects_removed_and_downgraded() -> None:
    after = _BEFORE.replace(_FR02, "").replace(_NFR03, "NFR-03: s\n**Priority**: Could")
    assert weakened(_BEFORE, after, set(), set()) == ["FR-02", "NFR-03"]


def test_weakened_allows_chosen_effects() -> None:
    after = _BEFORE.replace(_FR02, "").replace(_NFR03, "NFR-03: s\n**Priority**: Could")
    assert weakened(_BEFORE, after, {"FR-02"}, {"NFR-03"}) == []


def test_node_without_priority_checked_by_ids_only() -> None:
    before = "#### D-1\ntext\n#### D-2\ntext\n"
    assert node_items(before) == {"D-1": None, "D-2": None}
    assert weakened(before, "#### D-1\nnew\n", set(), set()) == ["D-2"]
    assert weakened(before, before + "#### D-3\n", set(), set()) == []


def test_duplicate_id_is_reported() -> None:
    before = "#### FR-01: t\n**Priority**: Must\n"
    assert weakened(before, before + "#### FR-01: t\ntext\n", set(), set()) == ["FR-01"]
    fenced = before + "```\n#### FR-01\n```\n"
    assert weakened(before, fenced, set(), set()) == ["FR-01"]


def test_pseudo_heading_with_could_is_reported() -> None:
    before = "#### FR-01: t\n**Priority**: Must\n"
    after = before + "#### FR-01.\n**Priority**: Could\n"
    assert weakened(before, after, set(), set()) == ["FR-01."]
    moved = "#### FR-01.\n**Priority**: Must\n"
    assert weakened(before, moved, set(), set()) == ["FR-01", "FR-01."]


@pytest.mark.parametrize(
    "line",
    [
        "**Priority**: must",
        "**Priority**: Must.",
        "**Priority**: **Must**",
        "**Priority**: Must-have",
        "**Priority**: Must/Should",
        "- **Priority**: Must",
        "Priority: Must",
        "text **Priority**: Must",
        "x\x0b**Priority**: Must",
        "**Priority**:",
    ],
)
def test_priority_rejected_by_gate_is_reported(line: str) -> None:
    before = "#### FR-01: t\n**Priority**: Must\n"
    after = f"#### FR-01: t\n{line}\n"
    assert weakened(before, after, set(), set()) == ["FR-01"]


def test_priority_read_as_gate_reads_it() -> None:
    # гейт режет блок только по заголовкам уровня 1–3: Priority под `#####`
    # остаётся у FR-01, и сверка не выдумывает ослабления
    before = "#### FR-01: t\n**Priority**: Must\n"
    after = "#### FR-01: t\n##### заметка\n**Priority**: Must\n"
    assert node_items(after) == {"FR-01": "Must"}
    assert weakened(before, after, set(), set()) == []


def test_preexisting_grammar_findings_are_not_new() -> None:
    text = "#### FR-01\n**Priority**: Must\n#### FR-02: t\n**Priority**: Bad\n"
    assert weakened(text, text, set(), set()) == []


@pytest.mark.parametrize(
    "before",
    [
        "#### FR-01\n**Priority**: Must\n",
        "#### FR-01: t\n**Priority**: must\n",
        "#### FR-01: t\n- **Priority**: Must\n",
    ],
)
def test_must_downgrade_behind_old_grammar_finding_is_reported(before: str) -> None:
    after = "#### FR-01: x\n**Priority**: Could\n"
    assert weakened(before, after, set(), set()) == ["FR-01"]
    assert weakened(before, after, set(), {"FR-01"}) == []
    fixed = "#### FR-01: x\n**Priority**: Must\n"
    assert weakened(before, fixed, set(), set()) == []
