"""Ядро паузы «посмотреть этап» в `dfl` (#768).

Общее для личного и enterprise-путей: разбор флага, экран паузы и
подтверждение удалений. Журнал замечаний — `governance.review_remarks`
(здесь переэкспорт: governance не зависит от `lite`). Сам цикл «переписать →
проверить» живёт в вызывающем коде.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from governance.auto_tasks import notice
from governance.review_remarks import (  # noqa: F401 — публичные имена `lite`
    APPLIED,
    DROPPED,
    ENTERPRISE_STAGES,
    PENDING,
    REMARKS,
    STAGE_LABELS,
    Remark,
    all_remarks,
    mark_applied,
    mark_dropped,
    pending,
    pr_section,
    record,
)
from lite.interview_chat import STOP_WORD, Read, read_answer

PERSONAL_STAGES = ("brief", "design", "decomposition")
DONE_WORDS = frozenset({"дальше", "да", "ок", "1"})
_YES_WORDS = frozenset({"1", "да", "удалить", "снять"})
_NO_WORDS = frozenset({"2", "нет", "оставить"})


class ReviewError(Exception):
    """Неверная настройка паузы (например, неизвестное имя этапа)."""


def parse_flag(value: str, stages: tuple[str, ...]) -> tuple[str, ...]:
    """`--review`: `all` — все этапы пути, `none`/пусто — без пауз, иначе список."""
    text = value.strip()
    if text.casefold() == "all":
        return stages
    if text.casefold() in ("", "none"):
        return ()
    names = tuple(n.strip() for n in text.split(",") if n.strip())
    unknown = [n for n in names if n not in stages]
    if unknown:
        raise ReviewError(
            f"неизвестный этап: {', '.join(unknown)}; допустимы: {', '.join(stages)}"
        )
    return names


def from_config(names: tuple[str, ...], stages: tuple[str, ...]) -> tuple[str, ...]:
    """Этапы из конфига, относящиеся к этому пути; чужие молча отбрасываются."""
    return tuple(n for n in names if n in stages)


def ask(stage_label: str, paths: list[Path], *, read: Read) -> str | None:
    """Экран паузы. `""` — «дальше»; текст — замечания; `None` — `/стоп`/EOF."""
    notice(f"этап «{stage_label}» готов и прошёл проверку — посмотрите:")
    for path in paths:
        notice(f"  file://{path.resolve()}")
    notice(
        "замечания своими словами (пустая строка — отправить); "
        f"«дальше» — без замечаний; {STOP_WORD} — прервать"
    )
    answer = read_answer(read, quick=lambda s: s.strip().casefold() in DONE_WORDS)
    if answer is None:
        return None
    return "" if answer.casefold() in DONE_WORDS else answer


def confirm_removals(
    ids: list[str],
    *,
    read: Read,
    titles: Mapping[str, str] | None = None,
    downgraded: Collection[str] = (),
) -> set[str] | None:
    """Спросить по каждому id, можно ли удалить (или снять Must у пункта из
    `downgraded`): разрешённые id; `None` — `/стоп`/EOF (человек прервал —
    пауза у вызывающего, не «нет»). Номер — всегда, название — из `titles`."""
    allowed: set[str] = set()
    for item in ids:
        notice(_removal_question(item, (titles or {}).get(item, ""), downgraded))
        answer = read_answer(
            read, quick=lambda s: s.strip().casefold() in _YES_WORDS | _NO_WORDS
        )
        if answer is None:
            return None
        if answer.strip().casefold() in _YES_WORDS:
            allowed.add(item)
    return allowed


def _removal_question(item: str, title: str, downgraded: Collection[str]) -> str:
    """«Удалить D-02 — «Название»? 1. удалить · 2. оставить»."""
    named = f"{item} — «{title}»" if title else item
    if item in downgraded:
        return f"Снять Must с {named}? 1. снять · 2. оставить"
    return f"Удалить {named}? 1. удалить · 2. оставить"
