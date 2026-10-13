"""Локальная настройка установки акта (спека §10, «барьер миграции» п. 1).

`review_v1` — создавать новые заявки в режиме `review/v1` там, где
`ACT_MODE[P] = review`; `act_migration = "draining"` — не создавать новых
заявок без маркера. Обе только сужают: режим допуска определяет политика.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REVIEW_V1 = False
_KEYS = {"review_v1", "act_migration"}
_MIGRATION_VALUES = ("off", "draining")


class SettingsError(ValueError):
    """Настройка не читается — отказ конфигурации."""


@dataclass(frozen=True)
class ActSettings:
    """Флаги установки."""

    review_v1: bool
    draining: bool


def load_settings(config_home: Path) -> ActSettings:
    """`$XDG_CONFIG_HOME/darkfactory/act.toml`; файла нет — умолчания."""
    path = config_home / "darkfactory" / "act.toml"
    data = _read(path)
    if data is None:
        return ActSettings(DEFAULT_REVIEW_V1, False)
    if unknown := set(data) - _KEYS:
        raise SettingsError(f"{path}: неизвестные ключи {sorted(unknown)}")
    review = data.get("review_v1", DEFAULT_REVIEW_V1)
    migration = data.get("act_migration", "off")
    if type(review) is not bool:
        raise SettingsError(f"{path}: review_v1 — bool")
    if type(migration) is not str or migration not in _MIGRATION_VALUES:
        raise SettingsError(f"{path}: act_migration — off|draining")
    return ActSettings(review, migration == "draining")


def _read(path: Path) -> dict[str, object] | None:
    """Разобрать файл; `None` — файла нет; любая ошибка чтения — отказ."""
    try:
        if not path.exists():
            return None
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"{path}: не читается ({exc})") from exc
