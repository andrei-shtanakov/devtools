"""Правила контракта брифа v1.3 без ввода-вывода (devtools#813)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from governance.discovery_contract import gate_check
from lite import brief_v13 as bv
from lite.interview_state import Entry, SessionView

LIVE = [
    "GC-18 error [Translation table] секции нет или в ней нет строк",
    "GC-20 error [Category: cost] нет ни одной NFR категории cost",
    "GC-20 error [Category: perf] нет ни одной NFR категории perf",
    "GC-20 error [NFR-01] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-20 error [NFR-02] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-20 error [NFR-03] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-20 error [NFR-04] нет **Category**: без неё perf/cost-цель не проверить",
    "GC-21 error [owns: process] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: data] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: quality] владелец не назван: нет P или открытого Q с этим owns",
    "GC-21 error [owns: operations] владелец не назван: нет P или открытого Q с этим owns",
]  # fmt: skip


def _view(records: dict[str, list[dict[str, Any]]] | None = None) -> SessionView:
    records = records or {}
    entries = []
    for rs in records.values():
        for r in rs:
            fields = {k: v for k, v in r.items() if k not in ("id", "body")}
            entries.append(Entry(r["id"], r["body"], fields))
    return SessionView(
        frame="customer",
        required={},
        optional={},
        entries=entries,
        declared=[],
        holders={r["id"]: q for q, rs in records.items() for r in rs},
        answers={q: {"text": q, "entries": rs} for q, rs in records.items()},
        answered_keys=[],
        readiness="ready",
    )


def test_gaps_parses_live_findings() -> None:
    g = bv.gaps(LIVE)
    assert g.table_empty
    assert g.table_categories == list(gate_check.REQUIRED_CATEGORIES)
    assert g.numeric == ["perf", "cost"]
    assert g.no_category == ["NFR-01", "NFR-02", "NFR-03", "NFR-04"]
    assert g.owners == ["process", "data", "quality", "operations"]
    assert not g.unconfirmed


def test_gaps_table_categories_and_unconfirmed() -> None:
    g = bv.gaps([
        "GC-19 error [Translation table] нет ни одной строки категории «стоить»",
        "GC-19 error [Translation table] нет ни одной строки категории «делать»",
        "GC-18 error [Translation table#2] у допущения нет подтверждающего",
    ])  # fmt: skip
    assert not g.table_empty
    assert g.table_categories == ["делать", "стоить"]
    assert g.unconfirmed
    d = bv.gaps(["GC-18 error [Translation table#2] у допущения нет срока YYYY-MM-DD (получено '—')"])  # fmt: skip
    assert d.unconfirmed
    assert bv.gaps([]) == bv.Gaps([], [], [], False, [], False)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Экспорт 500 слов за 2 секунды", ["perf"]),
        ("Не дороже 300 ₽ в месяц", ["cost"]),
        ("Красивый интерфейс", []),
        ("Быстро и недорого, бюджет мал", ["perf", "cost"]),
    ],
)
def test_category_hypothesis_examples(body: str, expected: list[str]) -> None:
    assert bv.category_hypothesis(body) == expected


def test_parse_categories_agree_and_words() -> None:
    assert bv.parse_categories("да", ["perf"]) == ["perf"]
    assert bv.parse_categories("скорость и бюджет", []) == ["perf", "cost"]
    assert bv.parse_categories("security, compliance", ["perf"]) == [
        "security",
        "compliance",
    ]
    assert bv.parse_categories("что-то непонятное", ["perf"]) is None
    assert bv.parse_categories("да", []) is None


@pytest.mark.parametrize("text", ["неизвестно", "никто", "Пока не решили."])
def test_owner_unknown_is_open_q(text: str) -> None:
    out = bv.owner_entries("data", text, _view())
    assert out == [
        {
            "id": "Q-01",
            "body": "Кто владелец: данные?",
            "owns": ["data"],
            "owner_role": "analyst",
            "blocking": False,
        }
    ]


def test_dont_know_is_not_unknown() -> None:
    assert not bv.is_unknown("не знаю")
    assert bv.is_unknown("  НЕТ! ")


def test_owner_named_is_p() -> None:
    out = bv.owner_entries("quality", "Иван", _view())
    assert out == [
        {"id": "P-01", "body": "Иван — владелец (качество)", "owns": ["quality"]}
    ]


def test_owner_already_present_no_entries() -> None:
    p = {"id": "P-01", "body": "Иван", "owns": ["data"]}
    open_q = {"id": "Q-02", "body": "?", "owns": ["quality"]}
    closed_q = {"id": "Q-03", "body": "?", "owns": ["process"], "resolved": True}
    view = _view({"a": [p, open_q, closed_q]})
    assert bv.owner_entries("data", "Пётр", view) == []
    assert bv.owner_entries("quality", "никто", view) == []
    assert bv.owner_entries("process", "Пётр", view)[0]["id"] == "P-02"


def test_derived_rows_maps_prefixes_and_skips_existing() -> None:
    recs = {
        "q1": [
            {"id": "FR-01", "body": "x", "Preference": "Экспорт", "Acceptance": "ок"},
            {"id": "OUT-01", "body": "x", "Preference": "Мобильное", "Target": "нет"},
            {"id": "NFR-01", "body": "x", "Preference": "Быстро", "category": ["perf"]},
            {"id": "NFR-02", "body": "x", "Preference": "Закон", "category": ["compliance"]},
            {"id": "NFR-03", "body": "x", "Preference": "Дёшево", "category": ["cost"]},
            {"id": "NFR-04", "body": "x", "Preference": "Мило", "category": ["safety"]},
            {"id": "CON-01", "body": "x", "Preference": "Регламент", "category": ["compliance"]},
            {"id": "FR-02", "body": "x"},
            {"id": "G-01", "body": "x", "Preference": "цель"},
        ]
    }  # fmt: skip
    view = _view(recs)
    view.answers["q1"]["translations"] = [
        {"words": "Экспорт", "category": "делать", "constraint": ["FR-01"]}
    ]
    out = bv.derived_rows(view)
    got = {r["constraint"][0]: (h, r["category"], r["assumption"]) for h, r in out}
    assert got == {
        "OUT-01": ("q1", "не делать", "нет"),
        "NFR-01": ("q1", "стоить", ""),
        "NFR-02": ("q1", "доказывать", ""),
        "NFR-03": ("q1", "стоить", ""),
        "CON-01": ("q1", "доказывать", ""),
    }


def test_derived_rows_strip_pipe_and_newline() -> None:
    recs = {"q1": [{"id": "FR-01", "body": "x", "Preference": "а|б\nв"}]}
    ((_, row),) = bv.derived_rows(_view(recs))
    assert row["words"] == "а б в"
    assert row["category"] == "делать"


def test_parse_confirmation() -> None:
    today = date(2026, 11, 19)
    assert bv.parse_confirmation("владелец продукта, 2026-12-01", today) == (
        "владелец продукта",
        "2026-12-01",
    )
    assert bv.parse_confirmation("Иван, 01.12.2026", today) == ("Иван", "2026-12-01")
    assert bv.parse_confirmation("2026-12-01 — Иван", today) == ("Иван", "2026-12-01")
    assert bv.parse_confirmation("до конца месяца", today) == (
        "нужна дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"
    )
    assert bv.parse_confirmation("Иван, 01.12", today) == (
        "нужна дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"
    )
    assert bv.parse_confirmation("Иван, 2026-11-01", today) == "дата уже прошла"
    assert bv.parse_confirmation("2026-12-01", today) == "кто подтверждает?"
    assert bv.parse_confirmation("Иван, 2026-13-45", today) == (
        "нужна дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"
    )


def test_confirmed_rows_only_assumptions() -> None:
    rows = [
        {"words": "a", "category": "делать", "constraint": ["FR-01"], "assumption": ""},
        {"words": "b", "category": "стоить", "constraint": ["NFR-01"], "assumption": "x"},
        {"words": "c", "category": "стоить", "constraint": ["NFR-02"], "assumption": "y",
         "confirmer": "Пётр", "due": "2026-12-02"},
    ]  # fmt: skip
    out = bv.confirmed_rows(rows, "Иван", "2026-12-01")
    assert "confirmer" not in out[0]
    assert (out[1]["confirmer"], out[1]["due"]) == ("Иван", "2026-12-01")
    assert (out[2]["confirmer"], out[2]["due"]) == ("Пётр", "2026-12-02")
    assert "confirmer" not in rows[1]


def test_vocab_matches_gate_check() -> None:
    assert set(bv.DOMAIN_LABELS) == set(gate_check.OWNER_DOMAINS)
    assert set(bv.NUMERIC_QUESTIONS) == gate_check.NUMERIC_CATEGORIES
    assert tuple(bv.DOMAIN_LABELS) == gate_check.OWNER_DOMAINS
    for cat in bv.CATEGORY_STEMS:
        assert cat in {*gate_check.NUMERIC_CATEGORIES, "security", "compliance"} | {
            "safety",
            "availability",
        }
