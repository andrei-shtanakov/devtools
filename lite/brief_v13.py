"""Правила контракта брифа v1.3 (рамка заказчика) без ввода-вывода.

Разбор находок GC-18…GC-21, гипотезы категорий NFR, записи о владельцах,
строки таблицы перевода и подтверждение допущений. Словари — из vendored
`gate_check`; чистые функции — вопросы и пересылку делают вызывающие.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from governance.discovery_contract.gate_check import (
    NUMERIC_CATEGORIES,
    OWNER_DOMAINS,
    REQUIRED_CATEGORIES,
)
from lite.gate_repair import _agrees
from lite.interview_state import SessionView

DOMAIN_LABELS = {
    "process": "процесс",
    "data": "данные",
    "quality": "качество",
    "operations": "эксплуатация",
}
NUMERIC_QUESTIONS = {
    "perf": "Есть ли ожидания по скорости ответа и объёму работы?",
    "cost": "Есть ли ожидания по стоимости и бюджету?",
}
CATEGORY_STEMS = {
    "perf": ("скорост", "быстр", "секунд", "задержк", "ожидан", "объём", "нагрузк"),
    "cost": ("стоим", "бюджет", "цен", "расход", "денег", "дорог", "дороже", "₽"),
    "security": ("безопасн", "доступ", "парол", "шифр", "утечк"),
    "compliance": ("регламент", "закон", "аудит", "соответств"),
    "safety": ("вред", "опасн"),
    "availability": ("доступност", "простой", "отказ"),
}
# В слове побеждает самая длинная основа: «доступность» — availability, не
# security («доступ»); «безопасность» — security, не safety («опасн»).
UNKNOWN_WORDS = frozenset({
    "неизвестно", "никто", "нет", "не назначен", "не назначено",
    "пока нет", "пока не решили", "не решили",
})  # fmt: skip
NEED_DATE = "нужна дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ"
PAST_DATE = "дата уже прошла"
NEED_WHO = "кто подтверждает?"

_NO_CATEGORY_RE = re.compile(r"^GC-20 error \[((?:NFR)-\d+)\] нет \*\*Category\*\*")
_NUMERIC_RE = re.compile(r"^GC-20 error \[Category: (\w+)\]")
_OWNER_RE = re.compile(r"^GC-21 error \[owns: (\w+)\]")
_TABLE_CAT_RE = re.compile(r"^GC-19 error \[Translation table\] .*«(.+?)»")
_TABLE_EMPTY_RE = re.compile(r"^GC-18 error \[Translation table\] секции нет")
_UNCONFIRMED_RE = re.compile(
    r"^GC-18 error \[Translation table#\d+\] у допущения нет (?:подтверждающего|срока)"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}")
_SEPARATORS = " ,;—-"
_ROW_PREFIX_CATEGORY = {"FR": "делать", "OUT": "не делать"}


@dataclass(frozen=True)
class Gaps:
    """Что не хватает брифу по находкам GC-18…GC-21."""

    no_category: list[str]
    numeric: list[str]
    owners: list[str]
    table_empty: bool
    table_categories: list[str]
    unconfirmed: bool


def gaps(findings: list[str]) -> Gaps:
    """Разобрать строки находок GC-18…GC-21 в `Gaps`."""
    no_cat = [m.group(1) for f in findings if (m := _NO_CATEGORY_RE.match(f))]
    numeric = {m.group(1) for f in findings if (m := _NUMERIC_RE.match(f))}
    owners = {m.group(1) for f in findings if (m := _OWNER_RE.match(f))}
    empty = any(_TABLE_EMPTY_RE.match(f) for f in findings)
    named = {m.group(1) for f in findings if (m := _TABLE_CAT_RE.match(f))}
    return Gaps(
        no_category=list(dict.fromkeys(no_cat)),
        numeric=[c for c in ("perf", "cost") if c in numeric & NUMERIC_CATEGORIES],
        owners=[d for d in OWNER_DOMAINS if d in owners],
        table_empty=empty,
        table_categories=[c for c in REQUIRED_CATEGORIES if empty or c in named],
        unconfirmed=any(_UNCONFIRMED_RE.match(f) for f in findings),
    )


def category_hypothesis(body: str) -> list[str]:
    """Категории NFR по ключевым основам текста; ничего не узнано — `[]`.
    В каждом слове засчитывается только самая длинная найденная основа."""
    found: set[str] = set()
    for word in body.casefold().split():
        hits = [
            (len(stem), cat)
            for cat, stems in CATEGORY_STEMS.items()
            for stem in stems
            if stem in word
        ]
        longest = max((n for n, _cat in hits), default=0)
        found |= {cat for n, cat in hits if n == longest}
    return [cat for cat in CATEGORY_STEMS if cat in found]


def parse_categories(text: str, hypothesis: list[str]) -> list[str] | None:
    """Ответ человека → категории: согласие — гипотеза, иначе слова ответа;
    ничего не узнано — `None`."""
    if _agrees(text):
        return list(hypothesis) or None
    tokens = set(re.findall(r"[a-z]+", text.casefold()))
    by_stem = set(category_hypothesis(text))
    found = [c for c in CATEGORY_STEMS if c in tokens or c in by_stem]
    return found or None


def is_unknown(text: str) -> bool:
    """Ответ — «владельца нет/неизвестен» («не знаю» — это пропуск, не сюда)."""
    return text.strip().casefold().rstrip(".!?,;… ") in UNKNOWN_WORDS


def _is_open(entry: Any) -> bool:
    """P — всегда; Q — пока не `resolved`."""
    prefix = entry.id.split("-")[0]
    if prefix == "P":
        return True
    fields = entry.fields
    closed = fields.get("resolved") is True or fields.get("status") == "resolved"
    return prefix == "Q" and not closed


def has_owner(domain: str, view: SessionView) -> bool:
    """В сессии есть P или открытый Q с этим `owns`."""
    return any(
        _is_open(e) and domain in (e.fields.get("owns") or []) for e in view.entries
    )


def owner_entries(domain: str, text: str, view: SessionView) -> list[dict[str, Any]]:
    """Записи ответа о владельце домена: имя — `P`, «никто» — открытый `Q`;
    владелец уже назван — `[]`."""
    if has_owner(domain, view):
        return []
    label = DOMAIN_LABELS[domain]
    if is_unknown(text):
        return [
            {
                "id": view.next_id("Q"),
                "body": f"Кто владелец: {label}?",
                "owns": [domain],
                "owner_role": "analyst",
                "blocking": False,
            }
        ]
    body = f"{text.strip()} — владелец ({label})"
    return [{"id": view.next_id("P"), "body": body, "owns": [domain]}]


def _row_category(prefix: str, categories: list[str]) -> str | None:
    """Категория строки таблицы по префиксу пункта и категориям NFR/CON."""
    if prefix in _ROW_PREFIX_CATEGORY:
        return _ROW_PREFIX_CATEGORY[prefix]
    if prefix == "NFR" and set(categories) & NUMERIC_CATEGORIES:
        return "стоить"
    if prefix in ("NFR", "CON") and "compliance" in categories:
        return "доказывать"
    return None


def cell(text: str) -> str:
    """Ячейка таблицы: без `|` и переводов строк."""
    return re.sub(r"[|\r\n]", " ", text)


def _has_row(answer: dict[str, Any], words: str, eid: str) -> bool:
    """Ответ уже содержит строку с теми же словами и ограничением."""
    return any(
        r.get("words") == words and list(r.get("constraint") or []) == [eid]
        for r in answer.get("translations") or []
        if isinstance(r, dict)
    )


def derived_rows(view: SessionView) -> list[tuple[str, dict[str, Any]]]:
    """`(держатель, строка)` таблицы из `Preference` пунктов без вопроса."""
    out: list[tuple[str, dict[str, Any]]] = []
    for entry in view.entries:
        record = view.entry_record(entry.id)
        words = cell(str(record.get("Preference") or ""))
        holder = view.holders.get(entry.id)
        if not words or holder is None:
            continue
        category = _row_category(
            entry.id.split("-")[0], list(record.get("category") or [])
        )
        if category is None or _has_row(view.answers.get(holder, {}), words, entry.id):
            continue
        assumption = cell(str(record.get("Acceptance") or record.get("Target") or ""))
        out.append(
            (
                holder,
                {
                    "words": words,
                    "category": category,
                    "constraint": [entry.id],
                    "assumption": assumption,
                },
            )
        )
    return out


def _parse_date(raw: str) -> date | None:
    """`YYYY-MM-DD` или `ДД.ММ.ГГГГ` → дата; невозможная дата — `None`."""
    try:
        if "-" in raw:
            return date.fromisoformat(raw)
        day, month, year = raw.split(".")
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_confirmation(text: str, today: date) -> tuple[str, str] | str:
    """Ответ о подтверждающем и сроке → `(кто, "YYYY-MM-DD")` или причина."""
    match = _DATE_RE.search(text)
    due = _parse_date(match.group(0)) if match else None
    if match is None or due is None:
        return NEED_DATE
    if due < today:
        return PAST_DATE
    rest = (text[: match.start()] + " " + text[match.end() :]).strip(_SEPARATORS)
    confirmer = re.sub(r"\s+", " ", rest.strip(_SEPARATORS))
    return (confirmer, due.isoformat()) if confirmer else NEED_WHO


def confirmed_rows(
    rows: list[dict[str, Any]], confirmer: str, due: str
) -> list[dict[str, Any]]:
    """Строкам с `assumption` без `confirmer`/`due` дописать их."""
    out = []
    for row in rows:
        new = dict(row)
        if new.get("assumption"):
            new.setdefault("confirmer", confirmer)
            new.setdefault("due", due)
        out.append(new)
    return out
