"""Authority-root пути (ADR-ECO-004 I2) — python-половина.

SSOT — не этот модуль, а `contracts/authority-root/v1/paths.env`: тот же
файл читает `merge-pr.sh`, категорически отказывающий на таких диффах. До
этого перечень жил в двух питоновских местах порознь (`accept_pr` кортежем,
`runner` литералом внутри выражения) — расхождение было вопросом времени, а
третье определение в shell его бы закрепило.

Fail-closed: недоступный файл или пустой перечень — исключение, а не пустой
кортеж. Пустой перечень означал бы «authority-root путей нет», то есть
снятие защиты молчанием.
"""

from __future__ import annotations

from pathlib import Path

from governance import ssot_env

#: SSOT-файл; путь относительно корня репо (родитель пакета `governance`).
PATHS_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "authority-root"
    / "v1"
    / "paths.env"
)

_KEY = "AUTHORITY_ROOT_PREFIXES"


def prefixes() -> tuple[str, ...]:
    """Префиксы authority-root путей из SSOT-файла.

    Разбор — общий (`ssot_env`, там же правила формата и поведение на битом
    входе). Отдельный разбор здесь брал ПЕРВОЕ вхождение ключа, тогда как
    shell-половина брала последнее: половины расходились на дубле — ровно на
    том, ради чего SSOT и заводился (ревью #183, круг 6).
    """
    value = ssot_env.read_key(PATHS_FILE, _KEY, "SSOT authority-root путей")
    return tuple(value.split())


def touched(files: list[str]) -> list[str]:
    """Пути из `files`, попадающие под authority-root (порядок сохранён)."""
    marked = prefixes()
    return [f for f in files if any(f.startswith(p) for p in marked)]
