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
    """Префиксы authority-root путей из SSOT-файла."""
    try:
        text = PATHS_FILE.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: TRY003 — путь важнее классификации
        raise RuntimeError(
            f"SSOT authority-root путей недоступен: {PATHS_FILE} ({exc})"
        ) from exc
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith(f"{_KEY}="):
            continue
        found = tuple(stripped[len(_KEY) + 1:].split())
        if found:
            return found
        break
    raise RuntimeError(f"в {PATHS_FILE} нет непустого {_KEY}")


def touched(files: list[str]) -> list[str]:
    """Пути из `files`, попадающие под authority-root (порядок сохранён)."""
    marked = prefixes()
    return [f for f in files if any(f.startswith(p) for p in marked)]
