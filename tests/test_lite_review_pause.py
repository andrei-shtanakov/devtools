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


def test_confirm_removals_words_bound_to_question() -> None:
    """Слово варианта своего вопроса: «удалить» — да; «снять» на вопрос
    «Удалить?» — не согласие (такого варианта не было)."""
    kinds = {"D-01": "remove", "D-02": "remove", "FR-01": "downgrade"}
    got = rp.confirm_removals(
        ["D-01", "D-02", "FR-01"],
        read=scripted("Удалить", "снять", "2", "снять"),
        kinds=kinds,
    )
    assert got == {"D-01", "FR-01"}


def test_confirm_removals_shows_id_and_title(capsys) -> None:
    """Номер и название пункта: искать D-02 в прошлых ответах не нужно."""
    rp.confirm_removals(
        ["D-02"], read=scripted("2"), titles={"D-02": "Экспорт отчётов в CSV"}
    )
    assert (
        "Удалить D-02 — «Экспорт отчётов в CSV»? 1. удалить · 2. оставить"
        in capsys.readouterr().out
    )


def test_confirm_removals_printed_no_option_is_one_line_answer() -> None:
    """Вариант отказа так, как он напечатан, — ответ одной строкой."""
    kinds = {"D-01": "remove", "FR-01": "downgrade", "FR-02": "change"}
    got = rp.confirm_removals(
        ["D-01", "FR-01", "FR-02"],
        read=scripted("оставить", "Оставить", "оставить как было"),
        kinds=kinds,
    )
    assert got == set()


def test_confirm_removals_question_per_kind(capsys) -> None:
    """Пункт остался: потерял Must — «Снять Must», иначе — «Принять правку»."""
    rp.confirm_removals(
        ["FR-01", "FR-02"],
        read=scripted("2", "2"),
        titles={"FR-01": "Вход", "FR-02": "Выход"},
        kinds={"FR-01": "downgrade", "FR-02": "change"},
    )
    out = capsys.readouterr().out
    assert "Снять Must с FR-01 — «Вход»? 1. снять · 2. оставить" in out
    assert "Принять правку FR-02 — «Выход»? 1. принять · 2. оставить как было" in out
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


def test_item_titles_skip_machine_fields_keep_inline_code() -> None:
    """Поля `· key: value` — не название (Q, DT); обычный inline-код — часть
    названия, вырезается только служебный `traces: […]`."""
    text = (
        "#### Q-01 · owner_role: architects · resolution: resolved\n"
        "Где хранить токены?\n"
        "#### DT-01: Команда add · type: implement · owner: dev\n"
        "#### D-04: Хранение — строка в `config.toml` `traces: [FR-01]`\n"
    )
    titles = node_guard.item_titles(text)
    assert titles["Q-01"] == "Где хранить токены?"
    assert titles["DT-01"] == "Команда add"
    assert titles["D-04"] == "Хранение — строка в config.toml"


def test_item_titles_colon_in_title_and_field_lines_in_body() -> None:
    """Двоеточие в названии — не служебное поле (поля — закрытый перечень
    ключей); строки-поля тела не выдаются за название (ревью #781)."""
    text = (
        "#### DT-05: dfl: добавить флаг --review · type: implement · owner: dev\n"
        "#### DT-06\nscenarios: [BEH-07]\ntraces: [D-01]\nКоманда удаления\n"
    )
    titles = node_guard.item_titles(text)
    assert titles["DT-05"] == "dfl: добавить флаг --review"
    assert titles["DT-06"] == "Команда удаления"


def test_item_titles_skip_reason_and_parallel_group() -> None:
    """`reason:` (Q в 20-design.md) и `parallel_group:` (DT) — поля (#778)."""
    text = (
        "#### Q-01\nreason: продуктовая граница\nГде хранить токены?\n"
        "#### DT-07\nparallel_group: A\nСборка индекса\n"
    )
    titles = node_guard.item_titles(text)
    assert titles == {"Q-01": "Где хранить токены?", "DT-07": "Сборка индекса"}


def test_confirm_removals_unclear_answer_asks_again(capsys) -> None:
    """Непонятный ответ — вопрос повторяется с подсказкой, а не молчаливое
    «нет» после пустой строки (#778)."""
    got = rp.confirm_removals(["D-01"], read=scripted("а что это?", "1"))
    assert got == {"D-01"}
    out = capsys.readouterr().out
    assert out.count("Удалить D-01?") == 2
    assert rp.UNCLEAR_ANSWER in out


def test_change_kinds() -> None:
    before = (
        "#### FR-01: вход\n**Priority**: Must\n"
        "#### FR-02: выход\n**Priority**: Should\n"
        "#### FR-03: справка\n**Priority**: Should\n"
    )
    after = "#### FR-01: вход\n**Priority**: Should\n#### FR-02: выход\n"
    kinds = node_guard.change_kinds(before, after, ["FR-01", "FR-02", "FR-03"])
    assert kinds == {"FR-01": "downgrade", "FR-02": "change", "FR-03": "remove"}


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
    assert (
        "не применены (правка удаляла или ослабляла пункты): дизайн: убери Y" in section
    )
    assert "- дизайн: убери Y" not in section and "ещё Z" not in section


def test_pr_section_only_dropped(tmp_path: Path) -> None:
    rp.record(tmp_path, "design", "убери Y")
    rp.mark_dropped(tmp_path, "design", 1)
    section = rp.pr_section(tmp_path)
    assert section.startswith("## Замечания владельца")
    assert (
        "не применены (правка удаляла или ослабляла пункты): дизайн: убери Y" in section
    )


def test_keep_downgraded_sends_keep_must_note(tmp_path: Path, capsys) -> None:
    """«оставить» на «Снять Must?» — автору «сохранить Priority Must», не
    «не удалять» (пункт никто не удалял)."""
    from lite import owner_review as orv

    before = "#### FR-01: вход\n**Priority**: Must\n"
    path = tmp_path / "10-requirements.md"
    path.write_text(before, encoding="utf-8")
    prompts: list[str] = []
    outputs = iter(["#### FR-01: вход\n**Priority**: Should\n", before])

    def revise(current: str, text: str) -> str:
        prompts.append(text)
        path.write_text(next(outputs), encoding="utf-8")
        return ""

    outcome, _ = orv.revise_by_owner(
        path, "сделай вход необязательным", revise=revise, read=scripted("2")
    )
    assert outcome == orv.ACCEPTED
    assert "Снять Must с FR-01 — «вход»?" in capsys.readouterr().out
    assert "- FR-01: сохранить Priority Must" in prompts[1]
    assert "не удалять" not in prompts[1]
