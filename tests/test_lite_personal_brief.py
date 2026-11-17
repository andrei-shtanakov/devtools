"""Tests for the light brief of the personal path."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from lite.personal.brief import (
    Brief,
    Context,
    Function,
    Goal,
    brief_from_json,
    brief_problems,
    demote_uncovered,
    render_brief,
)
from lite.structure import FormError

CTX = Context("только я", "нет", "нет", "не трогает", "macOS")


def make_brief(**over: Any) -> Brief:
    fields: dict[str, Any] = {
        "goals": (Goal("G-01", "Быстро", "Стало быстро."),),
        "functions": (Function("FR-01", "Умеет", ("G-01",), ("а", "б"), "как сказал"),),
        "context": CTX,
    }
    fields.update(over)
    return Brief(**fields)


EXPECTED = """\
## Цели

### G-01: Быстро
Стало быстро.

## Функции

### FR-01: Умеет
**Цели:** G-01
**Критерии приёмки:**
- а
- б
**Слова владельца:** как сказал

## Контекст

- Пользователи: только я
- Авторизация: нет
- Сеть: нет
- Локальная система: не трогает
- Платформа: macOS
"""


def test_render_exact_format() -> None:
    assert render_brief(make_brief()) == EXPECTED


def test_render_other_and_open_items_section() -> None:
    ctx = dataclasses.replace(CTX, other="важно")
    out = render_brief(make_brief(context=ctx, open_items=("что-то", "ещё")))
    assert "- Прочее: важно\n" in out
    assert out.endswith("\n## Уточнить\n\n- что-то\n- ещё\n")


@pytest.mark.parametrize(
    ("brief", "needle"),
    [
        (make_brief(goals=()), "цел"),
        (make_brief(functions=()), "функци"),
        (
            make_brief(functions=(Function("FR-01", "t", ("G-01",), (), "w"),)),
            "FR-01",
        ),
        (
            make_brief(functions=(Function("FR-01", "t", ("G-09",), ("а",), "w"),)),
            "G-09",
        ),
        (
            make_brief(functions=(Function("FR-01", "t", (), ("а",), "w"),)),
            "FR-01",
        ),
        (
            make_brief(
                goals=(Goal("G-01", "a", "b"), Goal("G-01", "c", "d")),
            ),
            "G-01",
        ),
        (
            make_brief(goals=(Goal("G-02", "a", "b"),)),
            "G-02",
        ),
        (
            make_brief(
                functions=(
                    Function("FR-01", "t", ("G-01",), ("а",), "w"),
                    Function("FR-03", "t", ("G-01",), ("а",), "w"),
                )
            ),
            "FR-03",
        ),
        (make_brief(context=dataclasses.replace(CTX, network=" ")), "Сеть"),
        (make_brief(goals=(Goal("G-01", " ", "b"),)), "G-01"),
        (make_brief(goals=(Goal("G-01", "a", "  "),)), "G-01"),
        (
            make_brief(functions=(Function("FR-01", " ", ("G-01",), ("а",), "w"),)),
            "FR-01",
        ),
        (
            make_brief(functions=(Function("FR-01", "t", ("G-01",), (" ",), "w"),)),
            "FR-01",
        ),
        (
            make_brief(functions=(Function("FR-01", "t", ("G-01",), ("а", ""), "w"),)),
            "FR-01",
        ),
    ],
)
def test_problems_each_rule(brief: Brief, needle: str) -> None:
    problems = brief_problems(brief)
    assert problems
    assert any(needle in p for p in problems)


def test_problems_clean() -> None:
    assert brief_problems(make_brief()) == []


GOOD: dict[str, Any] = {
    "goals": [{"id": "G-01", "title": "Быстро", "text": "Стало быстро."}],
    "functions": [
        {
            "id": "FR-01",
            "title": "Умеет",
            "goals": ["G-01"],
            "criteria": ["а", "б"],
            "words": "как сказал",
        }
    ],
    "context": {
        "users": "только я",
        "auth": "нет",
        "network": "нет",
        "local": "не трогает",
        "platform": "macOS",
    },
}


def test_from_json_roundtrip() -> None:
    assert brief_from_json(GOOD) == make_brief()
    data = {**GOOD, "open_items": ["x"]}
    data["context"] = {**GOOD["context"], "other": "o"}
    brief = brief_from_json(data)
    assert brief.open_items == ("x",)
    assert brief.context.other == "o"


def _without(key: str) -> dict[str, Any]:
    return {k: v for k, v in GOOD.items() if k != key}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        _without("goals"),
        _without("functions"),
        _without("context"),
        {**GOOD, "goals": "x"},
        {**GOOD, "goals": [{"id": "G-01", "title": "t"}]},
        {**GOOD, "goals": [{"id": 1, "title": "t", "text": "x"}]},
        {**GOOD, "functions": [{**GOOD["functions"][0], "criteria": "а"}]},
        {**GOOD, "functions": [{**GOOD["functions"][0], "goals": [1]}]},
        {**GOOD, "context": {**GOOD["context"], "users": 5}},
        {**GOOD, "context": {k: v for k, v in GOOD["context"].items() if k != "auth"}},
        {**GOOD, "open_items": "x"},
    ],
)
def test_from_json_rejects_bad_shape(bad: object) -> None:
    with pytest.raises(FormError):
        brief_from_json(bad)


def test_demote_uncovered() -> None:
    covered = Function("FR-01", "ок", ("G-01",), ("а",), "w")
    bare = Function("FR-02", "голая", ("G-01",), (), "w")
    out = demote_uncovered(make_brief(functions=(covered, bare)))
    assert out.functions == (covered,)
    assert out.open_items == ("голая: нет критерия приёмки",)


def test_demote_uncovered_untitled_function_named() -> None:
    """Ревью #774: функция с пустым названием опознаётся в заметке."""
    bare = Function("FR-01", "  ", ("G-01",), (), "w")
    out = demote_uncovered(make_brief(functions=(bare,)))
    assert out.open_items == ("функция без названия: нет критерия приёмки",)


def test_demote_then_problems_has_no_order_issue() -> None:
    fs = (
        Function("FR-01", "a", ("G-01",), ("x",), "w"),
        Function("FR-02", "b", ("G-01",), (), "w"),
        Function("FR-03", "c", ("G-01",), ("y",), "w"),
    )
    out = demote_uncovered(make_brief(functions=fs))
    assert [f.id for f in out.functions] == ["FR-01", "FR-02"]
    assert out.functions[1].title == "c"
    # заметка — по названию: FR-02 теперь значит «c» (регрессия ревью #774)
    assert out.open_items == ("b: нет критерия приёмки",)
    assert not any("FR-" in note for note in out.open_items)
    assert brief_problems(out) == []
