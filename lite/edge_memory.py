"""Что журнал решений значит для текущих находок edge-check.

Одно место, где решения человека (`edge-decisions.jsonl` волны) переносятся
между кругами и вызовами `dfl run`: сортировка (`lite.edge_triage`) не
задаёт вопрос по находке с решением, разбор (`lite.edge_resolve`) решает,
спросить ли снова и предложить ли принятие вопреки, а сверка до/после
(`lite.node_guard`) берёт разрешённые удаления и понижения.

Решение привязано к тексту узла через digest находки на момент ответа
(`override.finding_digest`). Несогласие или принятие на прежнем тексте и
изменившийся digest — замечание вернулось после переписывания; тот же
digest — переписывания ещё не было, спрашивать снова незачем.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from lite.edge_journal import Decision, Finding

Key = tuple[str, str, str]

RETURNED = "Замечание вернулось после вашего несогласия. Что делаем?"
STALE = (
    "Замечание снова блокирует: текст узла изменился после вашего принятия, "
    "принятие больше не действует. Что делаем?"
)
AGAIN = "По этому замечанию уже было ваше решение, оно снова блокирует. Что делаем?"


@dataclass(frozen=True)
class Recall:
    """Решения журнала по одной находке.

    `decided` — решение есть (сортировка не спрашивает: `fix`); `ask` —
    спросить снова, `question` — с какой формулировкой ("" — вопрос
    сортировки); `offer_override` — предложить принять узел вопреки
    (только `test`); `removes`/`downgrades` — что решения разрешают снять.
    """

    decided: bool = False
    ask: bool = False
    question: str = ""
    offer_override: bool = False
    removes: frozenset[str] = frozenset()
    downgrades: frozenset[str] = frozenset()


def recall(
    decisions: list[Decision],
    findings: Iterable[Finding],
    *,
    test: bool = False,
    last_round: bool = False,
) -> dict[Key, Recall]:
    """Находка → что из журнала волны к ней относится (по ключу находки).

    Повторный вопрос и разрешения решает последнее решение по ключу (оно
    могло быть до остановки или в прошлом вызове); разрешения — только пока
    digest находки тот же, что при ответе.
    """
    return {f.key: _recall(decisions, f, test, last_round) for f in findings}


def _allows(last: Decision, f: Finding) -> dict[str, frozenset[str]]:
    """Разрешения последнего решения — пока текст узла тот же (digest равен
    и не пуст): переписывание состоялось или ответ сменился — разрешение
    израсходовано."""
    if not last.digest or last.digest != f.digest:
        return {}
    return {
        "removes": frozenset(last.allows.get("removes", [])),
        "downgrades": frozenset(last.allows.get("downgrades", [])),
    }


def _recall(
    decisions: list[Decision], f: Finding, test: bool, last_round: bool
) -> Recall:
    mine = [d for d in decisions if d.key == f.key]
    allows = _allows(mine[-1], f) if mine else {}
    bindable = test and bool(f.digest)  # без digest принятие не к чему привязать
    if not mine:
        return Recall(offer_override=bindable and last_round, **allows)
    last = mine[-1]
    if last.kind == "override":
        if last.digest and last.digest == f.digest:
            return Recall(decided=True, **allows)
        if last.digest:
            return Recall(True, True, STALE, bindable, **allows)
        return Recall(True, True, AGAIN, bindable and last_round, **allows)
    if last.kind == "disagree" and test:
        if not last.digest:  # старая строка: менялся ли текст — неизвестно
            return Recall(True, True, AGAIN, bindable and last_round, **allows)
        if last.digest != f.digest:
            return Recall(True, True, RETURNED, bindable, **allows)
    return Recall(decided=True, **allows)
