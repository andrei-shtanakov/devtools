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
