"""enabled_actions роадмапа (спека среза 1, §2.1)."""

from conductor.roadmap import ACTIONS, parse_roadmap
from tests.conductor.fixtures import EPICS, ROADMAP


def _parse(extra: str):
    return parse_roadmap(
        ROADMAP.replace("autonomy = 0", f"autonomy = 1\n{extra}"), EPICS
    )


def test_absent_field_means_nothing_enabled() -> None:
    rm = parse_roadmap(ROADMAP, EPICS)
    assert rm.valid and rm.enabled_actions == frozenset()


def test_known_actions_enabled_separately() -> None:
    rm = _parse('enabled_actions = ["owner_queue", "nudge"]')
    assert rm.valid
    assert rm.enabled_actions == {"owner_queue", "nudge"}
    assert "pr_nudge" not in rm.enabled_actions


def test_unknown_name_is_rm_invalid() -> None:
    rm = _parse('enabled_actions = ["owner_queue", "todo_hygiene_pr"]')
    assert not rm.valid
    assert any("todo_hygiene_pr" in f.detail for f in rm.findings)


def test_duplicate_and_wrong_type_are_rm_invalid() -> None:
    assert not _parse('enabled_actions = ["nudge", "nudge"]').valid
    assert not _parse('enabled_actions = "nudge"').valid
    assert not _parse("enabled_actions = [1]").valid


def test_actions_constant_is_slice_scope() -> None:
    assert ACTIONS == (
        "owner_queue",
        "notify_satisfied",
        "nudge",
        "pr_nudge",
        "close_shipped",
    )
