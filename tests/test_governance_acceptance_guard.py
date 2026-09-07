"""Unit-тесты governance.acceptance_guard: парсер AC и покрытие Must.
Никакого git/ФС — только строки."""

from __future__ import annotations

from governance.acceptance_guard import AcCriterion, parse_ac_criteria

AC_OK = (
    "#### AC-01: Прогон первым действием · verification: test\n"
    "traces: [FR-01, NFR-01]\n"
    "scenarios: [BEH-01]\n"
    "Наблюдаемый признак: живой прогон до платного вызова.\n"
    "\n"
    "#### AC-02: Ручная проверка консоли · verification: manual\n"
    "traces: [FR-02]\n"
    "Оператор видит стадию verify в статусе.\n"
)


def test_parse_two_criteria() -> None:
    crits, findings = parse_ac_criteria(AC_OK)
    assert findings == []
    assert [c.ac_id for c in crits] == ["AC-01", "AC-02"]
    assert crits[0] == AcCriterion(
        ac_id="AC-01", title="Прогон первым действием",
        verification="test", traces=("FR-01", "NFR-01"),
        scenarios=("BEH-01",),
    )
    assert crits[1].scenarios == ()


def test_near_miss_heading_is_a_finding() -> None:
    bad = "#### AC-03 Без двоеточия · verification: test\n"
    crits, findings = parse_ac_criteria(bad)
    assert crits == []
    assert any("AC-03" in f and "грамматик" in f for f in findings)


def test_unknown_suffix_form_is_a_finding() -> None:
    bad = AC_OK + "\n#### AC-02X: Мусорный суффикс · verification: test\n"
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-02X" in f for f in findings)


def test_duplicate_ac_id_is_a_finding() -> None:
    dup = AC_OK + (
        "\n#### AC-01: Дубль · verification: manual\ntraces: [FR-01]\n"
    )
    _crits, findings = parse_ac_criteria(dup)
    assert any("AC-01" in f and "раза" in f for f in findings)


def test_test_verification_requires_scenarios() -> None:
    bad = (
        "#### AC-05: Тестовый без сценариев · verification: test\n"
        "traces: [FR-01]\n"
    )
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-05" in f and "scenarios" in f for f in findings)


def test_empty_traces_is_a_finding() -> None:
    bad = (
        "#### AC-06: Без трасс · verification: manual\n"
        "traces: []\n"
    )
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-06" in f and "traces" in f for f in findings)


def test_block_ends_at_next_section() -> None:
    text = (
        "#### AC-01: Одинокий · verification: manual\n"
        "traces: [FR-01]\n"
        "\n## Порог приёмки\n\nscenarios: [BEH-99]\n"
    )
    crits, findings = parse_ac_criteria(text)
    assert findings == []
    assert crits[0].scenarios == ()


REQ = (
    "#### FR-01: Первое\n**Priority**: Must\nтекст\n\n"
    "#### FR-02: Второе\n**Priority**: Should\nтекст\n\n"
    "#### NFR-01: Бюджет\n**Priority**: Must\nтекст\n"
)
BEH = "#### BEH-01: Один\nтекст\n\n#### BEH-02: Два\nтекст\n"


def test_clean_coverage() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Функция · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\nпроза\n\n"
        "#### AC-02: Бюджет · verification: metric\n"
        "traces: [NFR-01]\nисточник числа — артефакт замера\n"
    )
    assert coverage_findings(REQ, BEH, acc) == []


def test_uncovered_must_fr_and_nfr_are_findings() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Только FR · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\n"
    )
    findings = coverage_findings(REQ, BEH, acc)
    assert any("NFR-01" in f and "не покрыт" in f for f in findings)
    assert not any("FR-02" in f for f in findings)  # Should — не находка


def test_unknown_references_are_findings() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Битые ссылки · verification: test\n"
        "traces: [FR-01, FR-99]\nscenarios: [BEH-01, BEH-99]\n"
    )
    findings = coverage_findings(REQ, BEH, acc)
    assert any("FR-99" in f for f in findings)
    assert any("BEH-99" in f for f in findings)


def test_near_miss_requirement_priority_is_a_finding() -> None:
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Без приоритета\nпроза без строки Priority\n"
    acc = "#### AC-01: X · verification: manual\ntraces: [FR-01]\n"
    findings = coverage_findings(req, BEH, acc)
    assert any(
        "FR-01" in f and "недостоверн" in f for f in findings
    )


def test_out_of_vocabulary_priority_value_is_a_finding() -> None:
    """Значение **Priority**, не входящее в словарь Must|Should (напр.,
    строчное `must`), не должно молча выпадать из множества Must —
    промах грамматики становится находкой «недостоверн», а не тихим
    зелёным при непокрытом требовании."""
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Строчный приоритет\n**Priority**: must\nтекст\n"
    acc = "#### AC-01: X · verification: manual\ntraces: []\n"
    findings = coverage_findings(req, BEH, acc)
    assert any(
        "FR-01" in f and "недостоверн" in f for f in findings
    )
    # Непокрытое FR-01 не должно тихо зеленеть: раз значение вне словаря,
    # оно не попадает в Must и не даёт "не покрыт" находку — единственная
    # находка про недостоверность входного множества.
    assert not any("не покрыт" in f for f in findings)


def test_empty_must_set_needs_declaration() -> None:
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Только Should\n**Priority**: Should\nтекст\n"
    acc_without = (
        "#### AC-01: X · verification: manual\ntraces: [FR-01]\n"
    )
    assert any(
        "деклара" in f for f in coverage_findings(req, BEH, acc_without)
    )
    acc_with = acc_without + "\nMust-требований во входном наборе нет\n"
    assert coverage_findings(req, BEH, acc_with) == []


def test_requirement_block_does_not_absorb_priority_past_section() -> None:
    """Требование без своей строки Priority не должно подхватывать первую
    строку Priority, найденную дальше в документе — даже в разделе за
    границей секции (напр., шаблон-приложение). Зеркалит
    `test_block_ends_at_next_section` для parse_ac_criteria."""
    from governance.acceptance_guard import coverage_findings
    req = (
        "#### FR-01: Покрыто\n**Priority**: Must\nпроза\n\n"
        "#### NFR-01: Бюджет прогона\nпроза без строки Priority\n\n"
        "## Приложение: шаблон требования\n\n"
        "#### <FR-NN>: <название>\n**Priority**: Should\n"
    )
    acc = "#### AC-01: x · verification: manual\ntraces: [FR-01]\nпроза\n"
    findings = coverage_findings(req, BEH, acc)
    assert any(
        "NFR-01" in f and "недостоверн" in f for f in findings
    )


def test_empty_must_declaration_prose_mention_is_not_the_declaration() -> None:
    """Упоминание строки-декларации в прозе (не как отдельная строка) не
    должно засчитываться как декларация — якорь по началу строки, не
    substring-проверка."""
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Только Should\n**Priority**: Should\nтекст\n"
    acc_prose = (
        "#### AC-01: X · verification: manual\ntraces: [FR-01]\n\n"
        "Неверно утверждать, что Must-требований во входном наборе нет.\n"
    )
    findings = coverage_findings(req, BEH, acc_prose)
    assert any("деклара" in f for f in findings)

    acc_anchored = (
        "#### AC-01: X · verification: manual\ntraces: [FR-01]\n\n"
        "Must-требований во входном наборе нет\n"
    )
    assert coverage_findings(req, BEH, acc_anchored) == []
