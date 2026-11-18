"""Ядро каталога строк `dfl` и язык прогона (#769)."""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path

import pytest

from governance import run_state as rs
from lite import i18n, i18n_en
from lite.personal import state

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean():
    i18n.set_language("ru")
    i18n.reset_misses()
    yield
    i18n.set_language("ru")
    i18n.reset_misses()


def test_t_returns_template_in_ru(monkeypatch):
    monkeypatch.setitem(i18n_en.EN, "Привет", "Hello")
    assert i18n.t("Привет") == "Привет"
    assert i18n.misses() == frozenset()


def test_t_translates_in_en(monkeypatch):
    monkeypatch.setitem(i18n_en.EN, "Привет, {name}", "Hello, {name}")
    with i18n.language("en"):
        assert i18n.t("Привет, {name}") == "Hello, {name}"
    assert i18n.misses() == frozenset()


def test_t_records_miss_in_en():
    with i18n.language("en"):
        assert i18n.t("Нет перевода") == "Нет перевода"
        assert i18n.t("plain ascii") == "plain ascii"
    assert i18n.misses() == frozenset({"Нет перевода"})
    i18n.reset_misses()
    assert i18n.misses() == frozenset()


def test_language_context_restores():
    with i18n.language("en"):
        assert i18n.current() == "en"
    assert i18n.current() == "ru"
    with pytest.raises(RuntimeError), i18n.language("en"):
        raise RuntimeError
    assert i18n.current() == "ru"


def test_resolve_order():
    assert i18n.resolve("en", {"DFL_LANG": "ru"}, None) == "en"
    assert i18n.resolve(None, {"DFL_LANG": "en"}, None) == "en"
    assert i18n.resolve(None, {}, "en") == "en"
    assert i18n.resolve(None, {}, None) == "ru"
    assert i18n.resolve("ru", {}, "en") == "en"


def test_resolve_rejects_bad_flag_and_env():
    with pytest.raises(i18n.LanguageError):
        i18n.resolve("de", {}, None)
    with pytest.raises(i18n.LanguageError):
        i18n.resolve(None, {"DFL_LANG": "xx"}, None)
    with pytest.raises(i18n.LanguageError):
        i18n.resolve("de", {}, "en")


def test_stored_language_bad_value():
    assert i18n.stored_language("de") == ("ru", True)
    assert i18n.stored_language(None) == ("ru", False)
    assert i18n.stored_language("en") == ("en", False)
    assert i18n.resolve(None, {}, "de") == "ru"


def _names(template: str) -> set[str]:
    return {n for _, n, _, _ in string.Formatter().parse(template) if n is not None}


def test_catalog_placeholders_match():
    for ru, en in i18n_en.EN.items():
        assert _names(ru) == _names(en), ru


def _code_literals() -> set[str]:
    found: set[str] = set()
    skip = ROOT / "lite" / "i18n_en.py"
    for folder in ("lite", "governance"):
        for path in (ROOT / folder).rglob("*.py"):
            if path == skip:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found.update(
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
    return found


def test_catalog_keys_in_code():
    literals = _code_literals()
    missing = [k for k in i18n_en.EN if k not in literals]
    assert not missing, missing


def test_old_run_json_loads_as_ru(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "PERSONAL_ROOT", tmp_path / "personal")
    run = state.new_personal_run("x", "o/r", "r", "/t", state.date(2026, 1, 1))
    state.save(run)
    path = state.run_dir(run.run_id) / "run.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["language"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert state.load(run.run_id).language == "ru"


def test_language_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(state, "PERSONAL_ROOT", tmp_path / "personal")
    run = state.new_personal_run(
        "x", "o/r", "r", "/t", state.date(2026, 1, 1), language="en"
    )
    state.save(run)
    assert state.load(run.run_id).language == "en"
