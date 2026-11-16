"""Ядро паузы на просмотр этапа (#768)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lite import node_guard
from lite import review_pause as rp


def scripted(*lines: str | None) -> Callable[[str], str | None]:
    it = iter(lines)
    return lambda _prompt: next(it, None)


def test_parse_flag_all_none_list() -> None:
    stages = rp.PERSONAL_STAGES
    assert rp.parse_flag("all", stages) == stages
    assert rp.parse_flag("none", stages) == ()
    assert rp.parse_flag("", stages) == ()
    assert rp.parse_flag("design, brief", stages) == ("design", "brief")


def test_parse_flag_unknown_lists_allowed() -> None:
    with pytest.raises(rp.ReviewError) as err:
        rp.parse_flag("charter", rp.PERSONAL_STAGES)
    assert "charter" in str(err.value)
    assert "brief, design, decomposition" in str(err.value)


def test_from_config_drops_foreign() -> None:
    names = ("brief", "charter", "design")
    assert rp.from_config(names, rp.ENTERPRISE_STAGES) == ("charter", "design")


def test_journal_pending_then_applied(tmp_path: Path) -> None:
    assert rp.pending(tmp_path, "design") == []
    rp.record(tmp_path, "design", "добавь X")
    rp.record(tmp_path, "brief", "другое")
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["добавь X"]
    rp.mark_applied(tmp_path, "design", 1)
    assert rp.pending(tmp_path, "design") == []
    assert [r.text for r in rp.pending(tmp_path, "brief")] == ["другое"]
    rp.record(tmp_path, "design", "ещё")
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["ещё"]
    flags = [(r.text, r.applied) for r in rp.all_remarks(tmp_path)]
    assert flags == [("добавь X", True), ("другое", False), ("ещё", False)]


def test_journal_survives_torn_line(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "a")
    with (tmp_path / rp.REMARKS).open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "rem')
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["a"]


@pytest.mark.parametrize("word", [*sorted(rp.DONE_WORDS), " Дальше ", "ОК"])
def test_ask_done_word_single_line(word: str, tmp_path: Path) -> None:
    assert rp.ask("дизайн", [tmp_path / "a.md"], read=scripted(word)) == ""


def test_ask_yes_but_is_remark(tmp_path: Path) -> None:
    got = rp.ask("дизайн", [], read=scripted("да, но добавь X", ""))
    assert got == "да, но добавь X"


def test_ask_prints_file_links(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = tmp_path / "20-design.md"
    rp.ask("дизайн", [doc], read=scripted("дальше"))
    out = capsys.readouterr().out
    assert f"file://{doc.resolve()}" in out
    assert "этап «дизайн» готов и прошёл проверку — посмотрите:" in out
    assert "/стоп" in out


def test_ask_stop_returns_none() -> None:
    assert rp.ask("дизайн", [], read=scripted("/стоп")) is None
    assert rp.ask("дизайн", [], read=scripted(None)) is None


def test_confirm_removals_yes_no() -> None:
    got = rp.confirm_removals(["D-01", "D-02"], read=scripted("1", "нет"))
    assert got == {"D-01"}


def test_confirm_removals_words() -> None:
    """Ответ словами варианта: «удалить»/«снять» — да, «оставить» — нет."""
    got = rp.confirm_removals(
        ["D-01", "D-02", "D-03"], read=scripted("Удалить", "оставить", "снять")
    )
    assert got == {"D-01", "D-03"}


def test_confirm_removals_shows_id_and_title(capsys) -> None:
    """Номер и название пункта: искать D-02 в прошлых ответах не нужно."""
    rp.confirm_removals(
        ["D-02"], read=scripted("2"), titles={"D-02": "Экспорт отчётов в CSV"}
    )
    assert (
        "Удалить D-02 — «Экспорт отчётов в CSV»? 1. удалить · 2. оставить"
        in capsys.readouterr().out
    )


def test_confirm_removals_downgrade_asks_about_must(capsys) -> None:
    """Пункт остался, но потерял Must — вопрос не «удалить»."""
    rp.confirm_removals(
        ["FR-01"], read=scripted("2"), titles={"FR-01": "Вход"}, downgraded={"FR-01"}
    )
    out = capsys.readouterr().out
    assert "Снять Must с FR-01 — «Вход»? 1. снять · 2. оставить" in out
    assert "Удалить" not in out


def test_confirm_removals_without_title_keeps_id(capsys) -> None:
    rp.confirm_removals(["D-09"], read=scripted("2"))
    assert "Удалить D-09? 1. удалить · 2. оставить" in capsys.readouterr().out


def test_item_titles_heading_or_body_start() -> None:
    text = (
        "#### D-01: Экспорт отчётов в CSV `traces: [FR-01]`\nтело\n\n"
        "#### D-02\n\n**Priority**: Must\nПоиск по названию без учёта регистра, "
        "с подсветкой совпадений и сортировкой по дате изменения\n"
        "#### D-03 — Кэш\n"
    )
    titles = node_guard.item_titles(text)
    assert titles["D-01"] == "Экспорт отчётов в CSV"
    assert titles["D-02"].startswith("Поиск по названию без учёта регистра")
    assert titles["D-02"].endswith("…") and len(titles["D-02"]) <= 61
    assert titles["D-03"] == "Кэш"


def test_confirm_removals_stop_is_none() -> None:
    assert rp.confirm_removals(["D-01", "D-02"], read=scripted("да", None)) is None


def test_pr_section_lists_applied(tmp_path: Path) -> None:
    assert rp.pr_section(tmp_path) == ""
    rp.record(tmp_path, "design", "добавь X")
    assert rp.pr_section(tmp_path) == ""
    rp.mark_applied(tmp_path, "design", 1)
    rp.record(tmp_path, "design", "не применено")
    section = rp.pr_section(tmp_path)
    assert section.startswith("## Замечания владельца")
    assert "- дизайн: добавь X" in section
    assert "не применено" not in section


def test_record_between_pending_and_mark_stays_pending(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "R1")
    snapshot = rp.pending(tmp_path, "design")
    rp.record(tmp_path, "design", "R2")
    rp.mark_applied(tmp_path, "design", len(snapshot))
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["R2"]
    rp.mark_applied(tmp_path, "design", 1)
    assert rp.pending(tmp_path, "design") == []


def test_append_after_torn_line_keeps_row(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "a")
    with (tmp_path / rp.REMARKS).open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "rem')
    rp.record(tmp_path, "design", "b")
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["a", "b"]


def test_dropped_not_pending_and_listed_separately(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "добавь X")
    rp.mark_applied(tmp_path, "design", 1)
    rp.record(tmp_path, "design", "убери Y")
    rp.mark_dropped(tmp_path, "design", 1)
    rp.record(tmp_path, "design", "ещё Z")
    assert [(r.text, r.status) for r in rp.all_remarks(tmp_path)] == [
        ("добавь X", "applied"),
        ("убери Y", "dropped"),
        ("ещё Z", "pending"),
    ]
    assert [r.text for r in rp.pending(tmp_path, "design")] == ["ещё Z"]
    section = rp.pr_section(tmp_path)
    assert section.startswith("## Замечания владельца")
    assert "- дизайн: добавь X" in section
    assert "не применены (правка удаляла пункты): дизайн: убери Y" in section
    assert "- дизайн: убери Y" not in section and "ещё Z" not in section


def test_pr_section_only_dropped(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "убери Y")
    rp.mark_dropped(tmp_path, "design", 1)
    section = rp.pr_section(tmp_path)
    assert section.startswith("## Замечания владельца")
    assert "не применены (правка удаляла пункты): дизайн: убери Y" in section
