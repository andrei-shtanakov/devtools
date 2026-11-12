"""Памятка «DarkFactory для коллег» не расходится с кодом режима test."""

from __future__ import annotations

import tomllib
from pathlib import Path

from governance import df_config

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "darkfactory-for-colleagues.md"
LINK = "docs/darkfactory-for-colleagues.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_doc_quotes_every_test_relaxation() -> None:
    text = _doc()
    for item in df_config.TEST_RELAXATIONS:
        assert item in text, item


def test_doc_names_every_component() -> None:
    comps = tomllib.loads(
        (ROOT / "lite" / "components.toml").read_text(encoding="utf-8")
    )
    text = _doc()
    for name in comps["repos"]:
        assert name in text, name


def test_doc_has_commands_and_honest_sections() -> None:
    text = _doc()
    for needle in (
        "dfl run",
        "dfl status",
        "dfl init-repo",
        "install.sh",
        "проверок нет (test)",
        "песочниц",
        "criteria-close",
    ):
        assert needle in text, needle


def test_doc_answer_loop_abandon_and_stops() -> None:
    text = _doc()
    for needle in (
        "## Интервью: как отвечать",
        "dfl answer --file answer.yaml",
        "text: |",
        "## Бросить конвейер: `dfl abandon`",
        "dfl abandon",
        "governance-runs-abandoned",
        "конвейер после прогона остановился",
        "сначала dfl init-repo, смержите его",
        'поставьте `mode = "test"` в `~/.config/darkfactory/config.toml`',
    ):
        assert needle in text, needle
    assert "Завершите их" not in text
    assert "переустановите" not in text


def test_readme_links_doc() -> None:
    assert LINK in (ROOT / "README.md").read_text(encoding="utf-8")


def test_doc_quotes_dfl_hints_verbatim() -> None:
    from lite import dfl, pipeline

    flat = " ".join(_doc().split())
    for hint in (dfl.ANSWER_HINT, pipeline.CHAIN_STOPPED_HINT):
        assert " ".join(hint.split()) in flat, hint


def test_doc_names_non_actions_ci_limit() -> None:
    text = _doc()
    for needle in ("GitHub Actions", "2 минут"):
        assert needle in text, needle


def test_doc_interview_chat_final_review() -> None:
    """Финальное ревью: Ctrl-C, добор тем, обновление пунктов, отказ correct
    после конца интервью, честная цена и отказ без claude в терминале."""
    from lite import dfl

    flat = " ".join(_doc().split())
    for hint in (
        dfl.CHAT_STOPPED,
        dfl.CHAT_HARNESS_REFUSAL,
        dfl.INTERVIEW_COMPLETED_REFUSAL,
        "Не хватает:",
        "как минимум один платный вызов",
        "агент работает",
        "уже записанный пункт",
        "«прервано»",
    ):
        assert " ".join(hint.split()) in flat, hint
    paid = "один платный вызов"
    assert flat.count(paid) == flat.count(f"как минимум {paid}"), paid
    # без claude в терминале — отказ, а не только подсказка (как без tty)
    assert "или без `harness.author = claude` разговора нет" not in flat
