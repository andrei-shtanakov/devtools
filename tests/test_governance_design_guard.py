"""Unit-тесты `governance.design_guard` (спека Task 4): чистые функции,
парсят DSL Q requirements/design и находят непокрытые архитектурные
вопросы. Никакого git/файловой системы — только строки."""

from __future__ import annotations

from governance.design_guard import (
    coverage_findings,
    parse_design_resolutions,
    parse_requirements_questions,
)

REQ = (
    "- **Q-03 · owner_role: architects · blocking: false.** Как?\n"
    "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
)
DSN_OK = (
    "#### Q-03 · owner_role: architects · resolution: resolved\nтекст\n"
)
DSN_DEF = (
    "#### Q-03 · owner_role: architects · resolution: deferred\n"
    "reason: ждём steward#147\n"
)


# --- parse_requirements_questions -------------------------------------


def test_parse_requirements_questions_reads_owner_role() -> None:
    assert parse_requirements_questions(REQ) == {
        "Q-03": "architects",
        "Q-05": "product",
    }


def test_parse_requirements_questions_empty_text() -> None:
    assert parse_requirements_questions("нет вопросов\n") == {}


# --- parse_design_resolutions -------------------------------------------


def test_parse_design_resolutions_resolved() -> None:
    assert parse_design_resolutions(DSN_OK) == {"Q-03": ("resolved", None)}


def test_parse_design_resolutions_deferred_with_reason() -> None:
    assert parse_design_resolutions(DSN_DEF) == {
        "Q-03": ("deferred", "ждём steward#147")
    }


def test_parse_design_resolutions_deferred_without_reason() -> None:
    bad = DSN_DEF.replace("reason: ждём steward#147\n", "")
    assert parse_design_resolutions(bad) == {"Q-03": ("deferred", None)}


# --- coverage_findings ---------------------------------------------------


def test_coverage_clean() -> None:
    assert coverage_findings(REQ, DSN_OK) == []


def test_uncovered_question_is_a_finding() -> None:
    assert any("Q-03" in f for f in coverage_findings(REQ, "## Механика\n"))


def test_deferred_without_reason_is_a_finding() -> None:
    bad = DSN_DEF.replace("reason: ждём steward#147\n", "")
    assert any("reason" in f for f in coverage_findings(REQ, bad))


def test_empty_input_set_needs_the_declaration_line() -> None:
    req = "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
    assert coverage_findings(req, "## Механика\n")  # нет декларации — finding
    ok = "Открытых архитектурных вопросов нет (входной набор пуст)\n"
    assert coverage_findings(req, ok) == []


def test_product_question_not_in_input_set() -> None:
    """product-Q не требует резолюции в design — не всплывает в findings."""
    req = "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
    ok = "Открытых архитектурных вопросов нет (входной набор пуст)\n"
    assert coverage_findings(req, ok) == []
    assert not any("Q-05" in f for f in coverage_findings(req, "## Механика\n"))
