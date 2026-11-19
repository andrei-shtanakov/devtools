"""Починка брифа по находкам GC-18…GC-21 (контракт v1.3) в терминале (#813).

Только фрейм заказчика, после GC-08, GC-13, GC-05 (`gate_repair.repair`), по
порядку: категория у `NFR` → `NFR` `perf` → `NFR` `cost` → владельцы доменов
→ таблица перевода (строки из `Preference` без вопроса, затем вопрос по
недостающей обязательной категории) → один вопрос о подтверждающем и сроке
допущений. Каждый шаг перечитывает сессию перед правкой (новые id — от
`view.next_id`) и пересылает ответ держателя (`--supersede`). Пропуск — пункт
не исправлен, в общий счёт `GATE_LEFT`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml

from governance.discovery_contract.gate_check import (
    _ID_LIST_RE,
    _NUMBER_UNIT_RE,
    _Q_REF_RE,
    CONSTRAINT_PREFIXES,
    REQUIRED_CATEGORIES,
)
from lite import brief_v13 as bv
from lite import gate_repair as gr
from lite.interview_state import SessionView
from lite.output import say
from lite.structure import Agent, Failed, Question, Ready, structure_answer

V13_FRAME = "customer"
CATEGORY_LABELS = {
    "perf": "скорость",
    "cost": "стоимость",
    "security": "безопасность",
    "compliance": "регламент",
    "safety": "вред людям",
    "availability": "доступность",
    "quality": "качество",
}
NUMERIC_LABELS = {
    "perf": "скорость ответа и объём работы",
    "cost": "стоимость и бюджет",
}
# тема вопроса, в ответ на который ложится пункт строки таблицы категории
TABLE_TOPICS = {
    "делать": "functions",
    "не делать": "out_of_scope",
    "стоить": "nfr",
    "доказывать": "constraints",
}
CATEGORY_UNKNOWN = "категория не узнана — пункт не исправлен"
TABLE_DERIVED = "таблица перевода: строки из записанных предпочтений"
NO_CONSTRAINT = "в ответе нет пункта-ограничения — строка «{category}» не записана"
NO_REQUIREMENT = (
    "в ответе нет требования с числом и единицей (например «2 секунды», "
    "«100 000 ₽») — «{label}» не записано; значения не знаете — «неизвестно»"
)


def repair_v13(
    findings: list[str],
    session: str,
    *,
    ask: gr.Ask,
    agent: Agent,
    resend: gr.Send,
    load: gr.Load,
    today: date | None = None,
) -> int:
    """Починить GC-18…GC-21 по строкам находок; число неисправленных.
    Вне фрейма заказчика — ничего, 0."""
    if load(session).frame != V13_FRAME:
        return 0
    found = bv.gaps(findings)
    left = _fill_categories(found.no_category, session, ask, resend, load)
    left += sum(
        _fill_numeric(kind, session, ask, agent, resend, load) for kind in found.numeric
    )
    left += sum(
        _fill_owner(domain, session, ask, resend, load) for domain in found.owners
    )
    left += _fill_table(found.table_categories, session, ask, agent, resend, load)
    return left + _fill_confirmation(session, ask, resend, load, today or date.today())


def _send(qid: str, doc: dict[str, Any], view: SessionView, resend: gr.Send) -> bool:
    """Переслать ответ держателя `qid`, дополненный ответом `doc`."""
    payload = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    return gr.resent_payload(qid, payload, view, resend)


# --- GC-20: категория у NFR ---


def _fill_categories(
    eids: list[str], session: str, ask: gr.Ask, resend: gr.Send, load: gr.Load
) -> int:
    """Категория каждого `NFR` без неё: гипотеза — человеку на «да» или
    своими словами."""
    return [_fill_category(eid, session, ask, resend, load) for eid in eids].count(
        False
    )


def _fill_category(
    eid: str, session: str, ask: gr.Ask, resend: gr.Send, load: gr.Load
) -> bool:
    """Один пункт; уже с категорией (повторный прогон) — исправлен."""
    view = load(session)
    qid = view.holders.get(eid)
    if qid is None:
        return False
    record = view.entry_record(eid)
    if record.get("category"):
        return True
    hypothesis = record_hypothesis(record)
    answer = ask(category_question(record["body"], hypothesis))
    if answer is None:
        return False
    categories = bv.parse_categories(answer, hypothesis)
    if categories is None:
        say(CATEGORY_UNKNOWN)
        return False
    return _send(qid, category_doc(record, categories), view, resend)


def record_hypothesis(record: dict[str, Any]) -> list[str]:
    """Гипотеза категории пункта по его формулировке, словам и цели."""
    return bv.category_hypothesis(_record_text(record))


def category_doc(record: dict[str, Any], categories: list[str]) -> dict[str, Any]:
    """Ответ-дополнение: пункт `record` с категориями и заметка словами."""
    note = f"Категория «{record['body']}»: {_shown(categories)}"
    return {"text": note, "entries": [{**record, "category": categories}]}


def category_question(body: str, hypothesis: list[str]) -> str:
    """Вопрос о категории: с гипотезой — на «да», без неё — открытый."""
    if not hypothesis:
        return gr.CATEGORY_ASK_OPEN.format(body=body)
    return gr.CATEGORY_ASK.format(body=body, hypothesis=_shown(hypothesis))


def _shown(categories: list[str]) -> str:
    """Категории словами человека."""
    return ", ".join(CATEGORY_LABELS.get(c, c) for c in categories)


def _record_text(record: dict[str, Any]) -> str:
    """Текст пункта для гипотезы категории: формулировка, слова, цель."""
    keys = ("body", "Preference", "Acceptance", "Target")
    return " ".join(str(record[k]) for k in keys if record.get(k))


# --- GC-20: NFR perf / cost ---


def _fill_numeric(
    kind: str,
    session: str,
    ask: gr.Ask,
    agent: Agent,
    resend: gr.Send,
    load: gr.Load,
) -> int:
    """Ожидания по `kind`: ответ — раскладка в `NFR` этой категории;
    «неизвестно» — `NFR` с целью из нового `Q` исследования. 1 — не
    исправлено."""
    view = load(session)
    if has_category(view, kind):
        return 0
    qid = gr.topic_holder(view, "nfr")
    if qid is None:
        return 1
    q = Question(qid, "nfr", bv.NUMERIC_QUESTIONS[kind])
    while True:  # без числа — переспрос, как у срока допущений (ревью #820)
        answer = ask(numeric_question(kind))
        if answer is None:
            return 1
        if bv.is_unknown(answer):
            doc = unknown_numeric_doc(kind, answer, view)
            return 0 if _send(qid, doc, view, resend) else 1
        done = structure_answer(q, answer, view, agent, may_ask=False)
        if isinstance(done, Failed):
            say(done.message)
            return 1
        doc = yaml.safe_load(done.payload) if isinstance(done, Ready) else {}
        if with_category(doc, kind):
            return 0 if _send(qid, doc, view, resend) else 1
        say(NO_REQUIREMENT.format(label=NUMERIC_LABELS[kind]))


def numeric_question(kind: str) -> str:
    """Вопрос человеку об ожиданиях `kind` (`perf`/`cost`)."""
    return gr.NUMERIC_ASK.format(question=bv.NUMERIC_QUESTIONS[kind])


def unknown_numeric_doc(kind: str, answer: str, view: SessionView) -> dict[str, Any]:
    """Ответ-дополнение на «неизвестно» по `kind`: заметка и `NFR` + `Q`."""
    return {
        "text": f"{bv.NUMERIC_QUESTIONS[kind]} — {answer.strip()}",
        "entries": unknown_numeric_entries(kind, view),
    }


def has_category(view: SessionView, kind: str) -> bool:
    """В сессии есть `NFR` категории `kind`."""
    return any(
        e.id.startswith("NFR-") and kind in (e.fields.get("category") or [])
        for e in view.entries
    )


def with_category(doc: dict[str, Any], kind: str) -> bool:
    """Есть `NFR` категории `kind` с числовой целью (GC-20): агент поставил
    категорию — у всех таких `NFR` цель числом или `Q-NN`; не поставил —
    она дописывается первому `NFR` ответа с такой целью. Иначе `False`:
    категория без числа оставила бы GC-20 красным, а вопрос — закрытым."""
    nfrs = [e for e in doc.get("entries") or [] if str(e["id"]).startswith("NFR-")]
    kinded = [e for e in nfrs if kind in (e.get("category") or [])]
    if kinded:
        return all(numeric_target(e) for e in kinded)
    fit = next((e for e in nfrs if numeric_target(e)), None)
    if fit is None:
        return False
    fit["category"] = [*(fit.get("category") or []), kind]
    return True


def numeric_target(entry: dict[str, Any]) -> bool:
    """Цель записи — число с единицей или ссылка на `Q-NN` (как GC-20)."""
    target = " ".join(str(entry[k]) for k in ("Acceptance", "Target") if entry.get(k))
    numbers = _ID_LIST_RE.sub(" ", target)
    return bool(_NUMBER_UNIT_RE.search(numbers) or _Q_REF_RE.search(target))


def unknown_numeric_entries(kind: str, view: SessionView) -> list[dict[str, Any]]:
    """«Неизвестно» по `kind`: `NFR` с целью-ссылкой на новый `Q`
    исследования (значение ищет аналитик)."""
    q_id, label = view.next_id("Q"), NUMERIC_LABELS[kind]
    return [
        {
            "id": view.next_id("NFR"),
            "body": f"Ожидания заказчика: {label}",
            "category": [kind],
            "Target": f"значение из {q_id}",
        },
        {
            "id": q_id,
            "body": f"Найти ожидания заказчика: {label}",
            "owner_role": "analyst",
            "blocking": False,
        },
    ]


# --- GC-21: владельцы доменов ---


def _fill_owner(
    domain: str, session: str, ask: gr.Ask, resend: gr.Send, load: gr.Load
) -> int:
    """Владелец домена: имя/роль — `P`, «неизвестно» — открытый `Q`. 1 —
    не исправлено."""
    view = load(session)
    if bv.has_owner(domain, view):
        return 0
    qid = gr.topic_holder(view, "personas")
    if qid is None:
        return 1
    answer = ask(owner_question(domain))
    if answer is None:
        return 1
    return 0 if _send(qid, owner_doc(domain, answer, view), view, resend) else 1


def owner_question(domain: str) -> str:
    """Вопрос человеку о владельце домена."""
    return gr.OWNER_ASK.format(label=bv.DOMAIN_LABELS[domain])


def owner_doc(domain: str, answer: str, view: SessionView) -> dict[str, Any]:
    """Ответ-дополнение о владельце: заметка и `P`/`Q` с `owns`."""
    return {
        "text": f"Кто отвечает за {bv.DOMAIN_LABELS[domain]}: {answer.strip()}",
        "entries": bv.owner_entries(domain, answer, view),
    }


# --- GC-18/GC-19: строки таблицы перевода ---


def _fill_table(
    categories: list[str],
    session: str,
    ask: gr.Ask,
    agent: Agent,
    resend: gr.Send,
    load: gr.Load,
) -> int:
    """Строки из `Preference` — без вопроса; обязательная категория, которой
    всё ещё нет, — вопрос. Число недостающих категорий после шага."""
    if not categories:
        return 0
    send_derived(session, resend, load)
    for category in missing_categories(load(session)):
        _fill_table_category(category, session, ask, agent, resend, load)
    return len(missing_categories(load(session)))


def send_derived(session: str, resend: gr.Send, load: gr.Load) -> None:
    """Выведенные строки — держателям их пунктов (каждому — одной
    пересылкой, сессия перечитана)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for qid, row in bv.derived_rows(load(session)):
        groups.setdefault(qid, []).append(row)
    if groups:
        say(TABLE_DERIVED)
    for qid, rows in groups.items():
        for row in rows:
            say(f"  «{row['words']}» → {row['category']}")
        _send(qid, {"text": "", "translations": rows}, load(session), resend)


def missing_categories(view: SessionView) -> list[str]:
    """Обязательные категории без единой строки в ответах сессии."""
    present = {
        row.get("category")
        for answer in view.answers.values()
        for row in answer.get("translations") or []
        if isinstance(row, dict)
    }
    return [c for c in REQUIRED_CATEGORIES if c not in present]


def _fill_table_category(
    category: str,
    session: str,
    ask: gr.Ask,
    agent: Agent,
    resend: gr.Send,
    load: gr.Load,
) -> None:
    """Вопрос по категории; ответ — раскладка в пункт и строку таблицы."""
    view = load(session)
    topic = TABLE_TOPICS[category]
    qid = gr.topic_holder(view, topic)
    if qid is None:
        return
    question = table_question(category)
    answer = ask(question)
    if answer is None:
        return
    q = Question(qid, topic, question)
    done = structure_answer(q, answer, view, agent, may_ask=False)
    if isinstance(done, Failed):
        say(done.message)
        return
    doc = yaml.safe_load(done.payload) if isinstance(done, Ready) else {}
    if not with_row(doc, category, answer):
        say(NO_CONSTRAINT.format(category=category))
        return
    _send(qid, doc, view, resend)


def table_question(category: str) -> str:
    """Вопрос человеку по обязательной категории таблицы."""
    return gr.TABLE_ASK.format(category=category)


def with_row(doc: dict[str, Any], category: str, words: str) -> bool:
    """В ответе есть строка категории (агент не дал — по первому
    пункту-ограничению ответа, слова — ответ человека); нет пункта — `False`."""
    rows = doc.get("translations") or []
    if any(r.get("category") == category for r in rows):
        return True
    own = [
        e
        for e in doc.get("entries") or []
        if str(e["id"]).rsplit("-", 1)[0] in CONSTRAINT_PREFIXES
    ]
    if not own:
        return False
    target = own[0].get("Acceptance") or own[0].get("Target") or ""
    row = {
        "words": bv.cell(words.strip()),
        "category": category,
        "constraint": [own[0]["id"]],
        "assumption": bv.cell(str(target)),
    }
    doc["translations"] = [*rows, row]
    return True


# --- GC-18: подтверждающий и срок допущений ---


def _fill_confirmation(
    session: str, ask: gr.Ask, resend: gr.Send, load: gr.Load, today: date
) -> int:
    """Один вопрос на всю таблицу: кто подтвердит допущения и к какой дате;
    ответ — во все строки с допущением без них. 1 — не исправлено."""
    pending = unconfirmed(load(session))
    if not pending:
        return 0
    say(gr.CONFIRM_INTRO)
    for _qid, row in pending:
        say(f"  «{row['words']}» → {row['assumption']}")
    answer = _confirmer(ask, today)
    if answer is None:
        return 1
    who, due = answer
    sent = True
    for qid in dict.fromkeys(q for q, _row in pending):
        view = load(session)
        doc = confirmation_doc(view, qid, who, due)
        sent = _send(qid, doc, view, resend) and sent
    return 0 if sent else 1


def confirmation_doc(view: SessionView, qid: str, who: str, due: str) -> dict[str, Any]:
    """Ответ-дополнение держателю `qid`: его строки с подтверждающим и сроком."""
    held = view.held_answer(qid)
    rows = bv.confirmed_rows(list(held.get("translations") or []), who, due)
    return {"text": f"Допущения подтвердит: {who}, срок {due}", "translations": rows}


def unconfirmed(view: SessionView) -> list[tuple[str, dict[str, Any]]]:
    """`(держатель, строка)` с допущением, но без подтверждающего или срока."""
    return [
        (qid, row)
        for qid, answer in view.answers.items()
        for row in answer.get("translations") or []
        if isinstance(row, dict)
        and row.get("assumption")
        and not (row.get("confirmer") and row.get("due"))
    ]


def _confirmer(ask: gr.Ask, today: date) -> tuple[str, str] | None:
    """Ответ «кто, дата»: плохая дата или нет кого — причина и переспрос;
    пропуск — `None`."""
    while True:
        answer = ask(gr.CONFIRM_ASK)
        if answer is None:
            return None
        parsed = bv.parse_confirmation(answer, today)
        if isinstance(parsed, tuple):
            return parsed
        say(parsed)
