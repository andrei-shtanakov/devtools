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


def test_readme_links_doc() -> None:
    assert LINK in (ROOT / "README.md").read_text(encoding="utf-8")
