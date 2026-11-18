"""Ядро паузы «посмотреть этап» в `dfl` (#768).

Общее для личного и enterprise-путей: разбор флага, экран паузы и
подтверждение удалений. Журнал замечаний — `governance.review_remarks`
(здесь переэкспорт: governance не зависит от `lite`). Сам цикл «переписать →
проверить» живёт в вызывающем коде.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from lite import pending as pending_mod
from lite.interview_chat import STOP_WORD, Read, read_answer

PERSONAL_STAGES = ("brief", "design", "decomposition")
DONE_WORDS = pending_mod.REVIEW_DONE_WORDS
_YES_WORDS = frozenset({"1", "да"})
_NO_WORDS = frozenset({"2", "нет", "оставить", "оставить как было"})
UNCLEAR_ANSWER = "не понял ответ — номер или слово варианта"
#: вид правки → (вопрос, слово согласия): слово чужого вопроса — не согласие
REMOVE, DOWNGRADE, CHANGE = "remove", "downgrade", "change"
_QUESTIONS = {
    REMOVE: ("Удалить {named}? 1. удалить · 2. оставить", "удалить"),
    DOWNGRADE: ("Снять Must с {named}? 1. снять · 2. оставить", "снять"),
    CHANGE: ("Принять правку {named}? 1. принять · 2. оставить как было", "принять"),
}


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


def question(stage: str, stage_label: str, paths: list[Path]) -> pending_mod.Question:
    """Вопрос паузы для `dfl pending`; один и тот же в терминале и без него."""
    return pending_mod.Question(
        pending_mod.REVIEW,
        stage,
        f"этап «{stage_label}» готов и прошёл проверку — посмотрите",
        links=tuple(f"file://{p.resolve()}" for p in paths),
        accepts="«дальше»",
    )


def ask_remote(
    stage: str, stage_label: str, paths: list[Path], *, ask: pending_mod.Ask
) -> str | None:
    """Пауза без терминала: `""` — «дальше»; текст — замечания (`dfl reply`);
    `None` — ответа ещё нет (вопрос записан, прогон ждёт)."""
    answer = ask(question(stage, stage_label, paths))
    if answer is None:
        return None
    return "" if pending_mod.is_done(answer) else answer.strip()


def confirm_removals(
    ids: list[str],
    *,
    read: Read,
    titles: Mapping[str, str] | None = None,
    kinds: Mapping[str, str] | None = None,
) -> set[str] | None:
    """Спросить по каждому id, принять ли правку (вид — `kinds`, по
    умолчанию удаление): разрешённые id; `None` — `/стоп`/EOF (человек
    прервал — пауза у вызывающего, не «нет»). Номер — всегда, название — из
    `titles`."""
    allowed: set[str] = set()
    for item in ids:
        question, word = _QUESTIONS[(kinds or {}).get(item, REMOVE)]
        title = (titles or {}).get(item, "")
        notice(question.format(named=_named(item, title)))
        yes = _YES_WORDS | {word}
        answer = _choice(read, yes)
        while answer == "":
            notice(UNCLEAR_ANSWER)
            notice(question.format(named=_named(item, title)))
            answer = _choice(read, yes)
        if answer is None:
            return None
        if answer in yes:
            allowed.add(item)
    return allowed


def _choice(read: Read, yes: frozenset[str]) -> str | None:
    """Один ответ строкой: слово согласия/отказа (в нижнем регистре), `""` —
    непонятно (спросить снова), `None` — `/стоп`/EOF."""
    answer = read_answer(read, quick=lambda _s: True)
    if answer is None:
        return None
    word = answer.strip().casefold()
    return word if word in yes | _NO_WORDS else ""


def _named(item: str, title: str) -> str:
    return f"{item} — «{title}»" if title else item


def confirm_question(
    stage: str, item: str, kind: str, title: str
) -> pending_mod.Question:
    """Вопрос «Удалить …?» (вид — `kind`) для `dfl pending`; тот же текст,
    что в терминале."""
    question, word = _QUESTIONS[kind]
    return pending_mod.Question(
        pending_mod.REMARKS_CONFIRM,
        f"{stage}:{item}",
        question.format(named=_named(item, title)),
        options=(word, "оставить"),
    )


def confirm_answer(text: str, kind: str) -> bool | None:
    """Ответ на вопрос вида `kind`: `True` — согласие, `False` — оставить,
    `None` — не разобран (слово чужого вопроса — не согласие)."""
    word = text.strip().casefold()
    if word in _YES_WORDS | {_QUESTIONS[kind][1]}:
        return True
    if word in _NO_WORDS:
        return False
    return None


def confirm_remote(
    items: list[str],
    *,
    stage: str,
    titles: Mapping[str, str],
    kinds: Mapping[str, str],
    ask: pending_mod.Ask,
    answers: dict[str, bool],
) -> set[str] | None:
    """Подтверждение без терминала: по каждому id без ответа в `answers`
    спросить через `ask`; ответ — в `answers` (сохраняет вызывающий).
    `None` — вопрос записан, прогон ждёт; иначе разрешённые id."""
    for item in items:
        if item in answers:
            continue
        kind = kinds.get(item, REMOVE)
        q = confirm_question(stage, item, kind, titles.get(item, ""))
        reply = ask(q)
        parsed = None if reply is None else confirm_answer(reply, kind)
        if parsed is None:
            if reply is not None:
                ask(q)  # непонятный ответ — вопрос записывается снова
            return None
        answers[item] = parsed
    return {item for item in items if answers[item]}
