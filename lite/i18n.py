"""Каталог строк `dfl`: язык прогона и перевод шаблонов до `.format()`."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from lite.i18n_en import EN

LANGS: tuple[str, ...] = ("ru", "en")
DEFAULT_LANG = "ru"
ENV_VAR = "DFL_LANG"

_CYRILLIC = re.compile("[А-Яа-яЁё]")
_state = {"lang": DEFAULT_LANG}
_misses: set[str] = set()


class LanguageError(ValueError):
    """Недопустимый язык во флаге или окружении."""


def set_language(lang: str) -> None:
    """Устанавливает текущий язык; недопустимый — `LanguageError`."""
    if lang not in LANGS:
        raise LanguageError(lang)
    _state["lang"] = lang


def current() -> str:
    """Текущий язык."""
    return _state["lang"]


@contextmanager
def language(lang: str) -> Iterator[None]:
    """Временно переключает язык, затем восстанавливает прежний."""
    previous = current()
    set_language(lang)
    try:
        yield
    finally:
        _state["lang"] = previous


def t(template: str) -> str:
    """Перевод шаблона для текущего языка (до `.format()`).

    В `en` шаблон с кириллицей без перевода возвращается как есть и
    записывается в промахи."""
    if current() != "en":
        return template
    if template in EN:
        return EN[template]
    if _CYRILLIC.search(template):
        _misses.add(template)
    return template


def misses() -> frozenset[str]:
    """Шаблоны, не нашедшие перевода в `en`."""
    return frozenset(_misses)


def reset_misses() -> None:
    """Очищает записанные промахи."""
    _misses.clear()


def _checked(value: str | None, source: str) -> str | None:
    if value is None or value in LANGS:
        return value
    raise LanguageError(f"{source}: {value!r}")


def resolve(flag: str | None, env: Mapping[str, str], stored: str | None) -> str:
    """Язык вызова: язык продолжаемого прогона, иначе `--lang`, `DFL_LANG`, `ru`.

    Плохой флаг/env — `LanguageError` (даже если побеждает сохранённый);
    плохой `stored` — `ru`. Предупреждение о расхождении печатает вызывающий."""
    flag_lang = _checked(flag, "--lang")
    env_lang = _checked(env.get(ENV_VAR), ENV_VAR)
    if stored is not None:
        return stored if stored in LANGS else DEFAULT_LANG
    return flag_lang or env_lang or DEFAULT_LANG


def stored_language(raw: object) -> tuple[str, bool]:
    """Язык из run.json: (язык, было ли значение недопустимым)."""
    if raw is None:
        return DEFAULT_LANG, False
    if isinstance(raw, str) and raw in LANGS:
        return raw, False
    return DEFAULT_LANG, True
