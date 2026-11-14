"""Правила ребра в промпте автора: блок текста по виду узла."""

from __future__ import annotations

from governance.edge_check.author_rules import rules_for


def test_rules_for_requirements_lists_items() -> None:
    text = rules_for("requirements")
    assert "R1" in text
    assert "Каждая цель, ограничение и метрика charter" in text
    assert "Reference only ids defined in: charter;" in text


def test_rules_for_unknown_kind_empty() -> None:
    assert rules_for("nope") == ""


def test_rules_for_charter_has_both_edges() -> None:
    text = rules_for("charter")
    assert "edge charter-vs-customer-brief" in text
    assert "edge charter-vs-engineer-brief" in text
