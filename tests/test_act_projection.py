"""Проекции self-hash по версиям акта (спека §7)."""

from __future__ import annotations

import pytest

from governance import act_projection
from governance.act_projection import ProjectionError, self_hash_v
from governance.node_approval import self_hash

NODE = "---\nid: requirements\nstatus: draft\nversion: 2\n---\nТело узла.\n"
STAMPED = (
    "---\nid: requirements\nstatus: draft\nversion: 2\napproval_act: review/v1\n"
    "approval_review_id: '11'\ndelivered_by: df-reviewer[bot]\n"
    "delivered_at: '2026-10-12T10:00:00Z'\n---\nТело узла.\n"
)


def test_legacy_projection_unchanged() -> None:
    assert self_hash_v(NODE, None) == self_hash(NODE)


def test_review_v1_hash_same_before_and_after_stamp() -> None:
    assert self_hash_v(NODE, "review/v1") == self_hash_v(STAMPED, "review/v1")


@pytest.mark.parametrize("marker", ["review/v9", "merge"])
def test_foreign_marker_in_node_refused(marker: str) -> None:
    """Маркер во frontmatter неизвестен или противоречит манифесту — отказ."""
    text = STAMPED.replace("approval_act: review/v1", f"approval_act: {marker}")
    with pytest.raises(ProjectionError):
        self_hash_v(text, "review/v1")


def test_unknown_act_refused_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(text: str) -> tuple[dict, str]:
        raise AssertionError("текст не должен разбираться")

    monkeypatch.setattr(act_projection, "split_frontmatter", explode)
    with pytest.raises(ProjectionError, match="review/v9"):
        self_hash_v(NODE, "review/v9")


@pytest.mark.parametrize("value", ["[review/v1]", "{a: 1}", "", "null", "~", "5"])
def test_non_string_marker_refused(value: str) -> None:
    """Нестроковый или пустой маркер — ProjectionError, не TypeError/снятие."""
    text = STAMPED.replace("approval_act: review/v1", f"approval_act: {value}")
    with pytest.raises(ProjectionError):
        self_hash_v(text, "review/v1")


def test_legacy_mode_refuses_review_marker() -> None:
    with pytest.raises(ProjectionError):
        self_hash_v(STAMPED, None)
