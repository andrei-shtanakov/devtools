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
    """DSN_OK несёт абзац-текст ПОСЛЕ заголовка (без `reason:` — DSL для
    `resolved` требует именно так) — MAJOR-2: он и есть justification."""
    assert parse_design_resolutions(DSN_OK) == {"Q-03": ("resolved", "текст")}


def test_parse_design_resolutions_resolved_bare_heading_has_no_justification() -> None:
    """MAJOR-2: заголовок без абзаца И без `reason:` — justification `None`
    (нечего рендерить), не путать с «абзац есть, но пуст»."""
    bare = "#### Q-03 · owner_role: architects · resolution: resolved\n"
    assert parse_design_resolutions(bare) == {"Q-03": ("resolved", None)}


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


def test_deferred_without_reason_with_tail_section_is_still_a_finding() -> None:
    """PR-ревью #145: fallback на первый абзац НЕ применяется к deferred —
    иначе заголовок следующей секции («## Механика») сходил бы за причину
    и S4 красил deferred-без-reason зелёным."""
    bad = (
        DSN_DEF.replace("reason: ждём steward#147\n", "")
        + "\n## Механика\n\nвнутренности узла.\n"
    )
    assert parse_design_resolutions(bad)["Q-03"] == ("deferred", None)
    assert any("reason" in f for f in coverage_findings(REQ, bad))


def test_last_resolved_q_does_not_swallow_next_section() -> None:
    """Блок последнего Q кончается на следующем заголовке уровня 1–3, а не
    в конце документа: resolved без абзаца-обоснования даёт None, а не
    текст чужой секции."""
    text = (
        "#### Q-03 · owner_role: architects · resolution: resolved\n"
        "\n## Механика\n\nвнутренности узла.\n"
    )
    assert parse_design_resolutions(text)["Q-03"] == ("resolved", None)


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


# --- закалка по minor'ам PR-ревью #145 -----------------------------------


def test_deferred_without_reason_outside_input_set_is_a_finding() -> None:
    """Q, объявленный только в design (входной набор architects пуст),
    с deferred без reason: — находка, а не тихий провал в рендер
    «reason: None»."""
    req = "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
    design = (
        "Открытых архитектурных вопросов нет (входной набор пуст)\n\n"
        "#### Q-07 · owner_role: architects · resolution: deferred\n"
    )
    assert any(
        "Q-07" in f and "reason" in f for f in coverage_findings(req, design)
    )


def test_duplicate_design_q_blocks_are_a_finding() -> None:
    """Дубль #### Q-NN не схлопывается молча «последний побеждает» —
    спека §4: каждый вопрос присутствует ровно один раз."""
    dup = (
        "#### Q-03 · owner_role: architects · resolution: deferred\n\n"
        "#### Q-03 · owner_role: architects · resolution: resolved\nтекст\n"
    )
    assert any(
        "Q-03" in f and "раза" in f for f in coverage_findings(REQ, dup)
    )


def test_near_miss_requirements_bullet_is_a_finding() -> None:
    """Буллет, похожий на Q, но мимо строгой грамматики (нет точки перед
    закрывающими звёздочками), — находка о недостоверном входном
    множестве, а не тихое исключение вопроса из набора."""
    req = (
        "- **Q-03 · owner_role: architects · blocking: false.** Как?\n"
        "- **Q-04 · owner_role: architects · blocking: true** Чем шардировать?\n"
    )
    findings = coverage_findings(req, DSN_OK)
    assert any("Q-04" in f and "грамматик" in f for f in findings)
