"""Интервью без терминала: следующий вопрос человеку и применение reply (#766).

Та же последовательность, что у `lite.interview_chat.chat`, но по одному
вопросу на вызов и без агента: (1) текущий вопрос `discovery` (код 20);
(2) после неполного конца — добор непокрытых обязательных тем; (3) починка
находок проверки брифа в порядке `lite.gate_repair` (GC-08, GC-13, GC-05), во
фрейме заказчика — и контракта v1.3 (GC-18…GC-21) в порядке
`lite.gate_repair_v13`: категория `NFR`, `perf`, `cost`, владельцы, таблица
перевода (строки из `Preference` — `table:auto` без вопроса), допущения.
Раскладку ответа делает скил по `prompt` записи вопроса; пропуски и
исправления пунктов прохода — отметки в `interview-pass.json` каталога
прогона. Человек не видит ни JSON, ни YAML, ни id пунктов.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from governance import interview as iv
from lite import brief_v13 as bv
from lite import gate_repair as gr
from lite import gate_repair_v13 as gv
from lite import pending
from lite.interview_chat import TOP_UP_HINT, _holder_question, current_question, is_skip
from lite.interview_state import SessionView
from lite.interview_view import _finding_text
from lite.structure import TOPIC_LABELS, build_prompt, to_payload
from lite.structure import Question as Asked

Send = Callable[[str, str], iv.DiscoveryReply]

PASS_FILE = "interview-pass.json"
MARK_KINDS = ("skipped", "done")
HUMAN_PLACEHOLDER = "<<ответ человека>>"
CONTENT_ANSWER = (
    "dfl answer --items <файл> --id {qid} | dfl reply --id {qid} --text пропустить"
)
VERDICT_ANSWER = "dfl reply --id {qid} --text 'да' | '<что не так>' | пропустить"
CONTENT_ACCEPTS = "ответ своими словами (раскладка — dfl answer) или «пропустить»"
VERDICT_ACCEPTS = "«да», что не так словами или «пропустить»"
CRITERION_GOAL = "Как проверить, что выполнено: «{body}»?"
TRACES_TEXT = (
    gr.TRACES_INTRO.removesuffix(" предлагаю связи:") + " без связей: {bodies}"
)
TRACES_GOAL = "С какими системами или ограничениями связаны: {bodies}?"
NOT_CONTENT = "ответ по существу — через dfl answer --items"
TEXT_ANSWER = "dfl reply --id {qid} --text '<ответ словами>' | пропустить"
TEXT_ACCEPTS = "ответ словами или «пропустить»"
NUMERIC_ANSWER = CONTENT_ANSWER + " | dfl reply --id {qid} --text неизвестно"
NUMERIC_ACCEPTS = (
    "ответ своими словами (раскладка — dfl answer), «неизвестно» или «пропустить»"
)
CATEGORY_REFUSED = (
    "категория не узнана — ответьте словами ({words}) или «да» на предложенную"
)
TABLE_AUTO = "table:auto"
ASSUMPTIONS_KEY = "assumptions"
ASSUMPTIONS_LABEL = "допущения перевода"
NO_HOLDER = "нет ответа, к которому записать {what}"


def load_marks(run_dir: Path) -> dict[str, list[str]]:
    """Отметки прохода `{"skipped": [...], "done": [...]}` (нет — пустые)."""
    data = pending._read_json(run_dir / PASS_FILE) or {}
    return {how: [str(k) for k in data.get(how) or []] for how in MARK_KINDS}


def mark(run_dir: Path, key: str, how: str) -> None:
    """Отметить пункт прохода `key` как `how` (`skipped` или `done`)."""
    if how not in MARK_KINDS:
        raise ValueError(f"неизвестная отметка: {how}")
    marks = load_marks(run_dir)
    if key not in marks[how]:
        marks[how].append(key)
    pending._write_json(run_dir / PASS_FILE, marks)


def clear_marks(run_dir: Path) -> None:
    """Стереть отметки: следующий проход начинается заново."""
    (run_dir / PASS_FILE).unlink(missing_ok=True)


def coverage(view: SessionView) -> list[dict]:
    """Темы фрейма (обязательные, затем необязательные) с покрытием и пунктами."""
    covered = view.covered()
    topics = [(k, p, True) for k, p in view.required.items()]
    topics += [(k, p, False) for k, p in view.optional.items()]
    return [
        {
            "key": key,
            "label": TOPIC_LABELS.get(key, key),
            "required": required,
            "covered": key in covered,
            "entries": [
                {"id": e.id, "body": e.body, "fields": dict(e.fields)}
                for e in view.entries
                if prefix is not None and e.id.rsplit("-", 1)[0] == prefix
            ],
        }
        for key, prefix, required in topics
    ]


def findings(view: SessionView) -> list[dict]:
    """Находки готовности: как есть и простыми словами."""
    return [{"raw": f, "text": _finding_text(f, view)} for f in view.findings]


def next_question(
    reply: iv.DiscoveryReply,
    view: SessionView,
    marks: Mapping[str, list[str]],
    *,
    incomplete: bool,
    upstream: str,
) -> tuple[pending.Question, dict] | None:
    """Следующий вопрос человеку и данные для скила; пунктов нет — `None`.

    Добор и починка — только после неполного конца интервью (`incomplete`),
    как в терминале; пункты с отметкой из `marks` пропускаются."""
    found = _next(reply, view, marks, incomplete, upstream)
    if found is None:
        return None
    q, data = found
    return q, {**data, "coverage": coverage(view), "findings": findings(view)}


def _next(
    reply: iv.DiscoveryReply,
    view: SessionView,
    marks: Mapping[str, list[str]],
    incomplete: bool,
    upstream: str,
) -> tuple[pending.Question, dict] | None:
    """Вопрос и данные без покрытия: вопрос `discovery`, добор, починка."""
    q = current_question(reply)
    if q is not None:
        return _question(q, view)
    if not incomplete:
        return None
    seen = _seen(marks)
    candidates = [*_top_ups(view), *_repairs(reply, view, upstream)]
    return next(((q_, d) for q_, d in candidates if q_.key not in seen), None)


def _seen(marks: Mapping[str, list[str]]) -> set[str]:
    """Ключи с отметкой прохода любого вида."""
    return {*marks.get("skipped", ()), *marks.get("done", ())}


def table_rows_due(
    reply: iv.DiscoveryReply,
    view: SessionView,
    marks: Mapping[str, list[str]],
    *,
    incomplete: bool,
    upstream: str,
) -> bool:
    """Пора дописать строки таблицы из `Preference` (без вопроса): фрейм
    заказчика, находка GC-18/GC-19, в проходе ещё не дописывали и вопросов
    раньше таблицы не осталось — как в терминале, после категорий и
    владельцев."""
    if view.frame != gv.V13_FRAME or not incomplete or TABLE_AUTO in _seen(marks):
        return False
    if current_question(reply) is not None:
        return False
    if not bv.gaps(_lines(reply)).table_categories:
        return False
    found = _next(reply, view, marks, incomplete, upstream)
    if found is None:
        return True
    key = found[0].key
    return key.startswith("table:") or key == ASSUMPTIONS_KEY


def _lines(reply: iv.DiscoveryReply) -> list[str]:
    """Строки находок проверки брифа из конверта `discovery`."""
    return [str(f) for f in reply.envelope.get("findings") or []]


def _question(q: Asked, view: SessionView) -> tuple[pending.Question, dict]:
    """Вопрос `discovery` (код 20) — как `interview_chat._answer_question`."""
    label = TOPIC_LABELS.get(q.coverage_key, q.coverage_key)
    text = f"Вопрос {len(view.answers) + 1} ({label}): {q.text}"
    out = _content(pending.INTERVIEW_QUESTION, q.id, text)
    data = _data(q.coverage_key, None, [], build_prompt(q, HUMAN_PLACEHOLDER, view))
    return out, {**data, "answer": CONTENT_ANSWER.format(qid=out.id)}


def _top_ups(view: SessionView) -> list[tuple[pending.Question, dict]]:
    """Добор непокрытых обязательных тем с префиксом и держателем."""
    if view.readiness != "incomplete":
        return []
    out = []
    for key in view.uncovered_required():
        qid = _holder_question(view, key)
        if view.required[key] is None or qid is None:
            continue
        label = TOPIC_LABELS.get(key, key)
        asked = Asked(qid, key, f"Расскажите про тему «{label}».")
        q = _content(
            pending.INTERVIEW_TOPUP,
            f"topic:{key}",
            f"Не хватает: {label} — {TOP_UP_HINT}",
        )
        prompt = build_prompt(asked, HUMAN_PLACEHOLDER, view)
        data = _data(key, qid, [], prompt)
        out.append((q, {**data, "answer": CONTENT_ANSWER.format(qid=q.id)}))
    return out


def _repairs(
    reply: iv.DiscoveryReply, view: SessionView, upstream: str
) -> list[tuple[pending.Question, dict]]:
    """Находки проверки брифа в порядке `gate_repair.repair`."""
    lines = _lines(reply)
    out = [_criterion(eid, view) for eid in gr._ids(gr.NO_CRITERION_RE, lines)]
    out.append(_traces(gr._ids(gr.NO_SYSTEM_RE, lines), view))
    bodies = gr.must_bodies(upstream) if upstream else {}
    out += [_verdict(fr, bodies, view) for fr in gr._ids(gr.NO_VERDICT_RE, lines)]
    out += _v13_repairs(lines, view)
    return [item for item in out if item is not None]


def _v13_repairs(
    lines: list[str], view: SessionView
) -> list[tuple[pending.Question, dict] | None]:
    """GC-18…GC-21 (фрейм заказчика) в порядке `gate_repair_v13.repair_v13`."""
    if view.frame != gv.V13_FRAME or not any(map(gr.V13_FINDING_RE.match, lines)):
        return []
    found = bv.gaps(lines)
    out = [_category(eid, view) for eid in found.no_category]
    out += [_numeric(kind, view) for kind in found.numeric]
    out += [_owner(domain, view) for domain in found.owners]
    if found.table_categories:
        out += [_table(c, view) for c in gv.missing_categories(view)]
    out.append(_assumptions(view))
    return out


def _category(eid: str, view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-20: категория `NFR` — гипотеза в `data`, ответ — `dfl reply`."""
    qid = view.holders.get(eid)
    if qid is None:
        return None
    record = view.entry_record(eid)
    if record.get("category"):
        return None
    hypothesis = gv.record_hypothesis(record)
    text = gv.category_question(record["body"], hypothesis)
    q = _text(f"category:{eid}", text)
    data = _data(view.asked.get(qid, ""), qid, [record], None)
    return q, {**data, "hypothesis": hypothesis, "answer": TEXT_ANSWER.format(qid=q.id)}


def _numeric(kind: str, view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-20: ожидания `perf`/`cost` — раскладка скила или «неизвестно»."""
    qid = gr.topic_holder(view, "nfr")
    if gv.has_category(view, kind) or qid is None:
        return None
    q = pending.Question(
        pending.INTERVIEW_REPAIR,
        f"nfr:{kind}",
        gv.numeric_question(kind),
        accepts=NUMERIC_ACCEPTS,
    )
    asked = Asked(qid, "nfr", bv.NUMERIC_QUESTIONS[kind])
    data = _data("nfr", qid, [], build_prompt(asked, HUMAN_PLACEHOLDER, view))
    return q, {**data, "answer": NUMERIC_ANSWER.format(qid=q.id)}


def _owner(domain: str, view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-21: владелец домена — `dfl reply` именем/ролью или «неизвестно»."""
    qid = gr.topic_holder(view, "personas")
    if bv.has_owner(domain, view) or qid is None:
        return None
    q = _text(f"owner:{domain}", gv.owner_question(domain))
    data = _data("personas", qid, [], None)
    return q, {**data, "answer": TEXT_ANSWER.format(qid=q.id)}


def _table(category: str, view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-19: обязательная категория таблицы — раскладка в пункт и строку."""
    topic = gv.TABLE_TOPICS[category]
    qid = gr.topic_holder(view, topic)
    if qid is None:
        return None
    question = gv.table_question(category)
    q = _content(pending.INTERVIEW_REPAIR, f"table:{category}", question)
    asked = Asked(qid, topic, question)
    data = _data(topic, qid, [], build_prompt(asked, HUMAN_PLACEHOLDER, view))
    return q, {**data, "answer": CONTENT_ANSWER.format(qid=q.id)}


def _assumptions(view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-18: один вопрос о подтверждающем и сроке; строки — в `data.rows`."""
    found = gv.unconfirmed(view)
    if not found:
        return None
    rows = [
        {k: row.get(k) for k in ("words", "category", "constraint", "assumption")}
        for _qid, row in found
    ]
    shown = "; ".join(f"«{r['words']}» → {r['assumption']}" for r in rows)
    q = _text(ASSUMPTIONS_KEY, f"{gr.CONFIRM_INTRO} {shown}. {gr.CONFIRM_ASK}")
    data = _data(ASSUMPTIONS_KEY, None, [], None, ASSUMPTIONS_LABEL)
    return q, {**data, "rows": rows, "answer": TEXT_ANSWER.format(qid=q.id)}


def _criterion(eid: str, view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-08: пункт без критерия — гипотезу предложит скил."""
    qid = view.holders.get(eid)
    if qid is None:
        return None
    record = view.entry_record(eid)
    text = f"{gr.CRITERIA_INTRO}: «{record['body']}»"
    q = _content(pending.INTERVIEW_REPAIR, f"criterion:{eid}", text)
    goal = CRITERION_GOAL.format(body=record["body"])
    prompt = _holder_prompt(qid, goal, view)
    data = _data(view.asked.get(qid, ""), qid, [record], prompt)
    return q, {**data, "answer": CONTENT_ANSWER.format(qid=q.id)}


def _traces(eids: list[str], view: SessionView) -> tuple[pending.Question, dict] | None:
    """GC-13: один вопрос на все IF/AP без связей; текст называет пункты
    (без id), как вопрос GC-08 — свой пункт."""
    eids = [eid for eid in eids if eid in view.holders]
    if not eids:
        return None
    qid = view.holders[eids[0]]
    records = [view.entry_record(eid) for eid in eids]
    key = "traces:" + ",".join(eids)
    bodies = ", ".join(f"«{r['body']}»" for r in records)
    q = _content(pending.INTERVIEW_REPAIR, key, TRACES_TEXT.format(bodies=bodies))
    prompt = _holder_prompt(qid, TRACES_GOAL.format(bodies=bodies), view)
    data = _data(view.asked.get(qid, ""), qid, records, prompt)
    return q, {**data, "answer": CONTENT_ANSWER.format(qid=q.id)}


def _verdict(
    fr: str, bodies: Mapping[str, str], view: SessionView
) -> tuple[pending.Question, dict] | None:
    """GC-05: вердикт реализуемости по Must-функции; раскладки нет."""
    qid = gr._feasibility_holder(view)
    if qid is None:
        return None
    label = bodies.get(fr, fr)
    q = pending.Question(
        pending.INTERVIEW_REPAIR,
        f"verdict:{fr}",
        f"{fr} «{label}» — {gr.VERDICT_HINT}",
        accepts=VERDICT_ACCEPTS,
    )
    data = _data(gr.FEASIBILITY_KEY, qid, [], None, label)
    return q, {**data, "answer": VERDICT_ANSWER.format(qid=q.id)}


def _holder_prompt(qid: str, goal: str, view: SessionView) -> str:
    """Промпт раскладки по вопросу держателя с целью починки."""
    return build_prompt(
        Asked(qid, view.asked.get(qid, ""), goal), HUMAN_PLACEHOLDER, view
    )


def _content(kind: str, key: str, text: str) -> pending.Question:
    return pending.Question(kind, key, text, accepts=CONTENT_ACCEPTS)


def _text(key: str, text: str) -> pending.Question:
    """Вопрос починки с ответом-текстом (`dfl reply`)."""
    return pending.Question(pending.INTERVIEW_REPAIR, key, text, accepts=TEXT_ACCEPTS)


def _data(
    topic: str,
    holder: str | None,
    entries: list[dict[str, Any]],
    prompt: str | None,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "topic": topic,
        "label": label if label is not None else TOPIC_LABELS.get(topic, topic),
        "holder": holder,
        "entries": entries,
        "prompt": prompt,
    }


def apply_reply(
    rec: Mapping[str, Any], text: str, view: SessionView, *, send: Send, resend: Send
) -> str | None:
    """Применить reply `text` к записанному вопросу `rec`.

    Пропуск вопроса `discovery` — голый текст ответом; пропуск добора и
    починки — ничего (отметку ставит вызывающий); ответ-текст починки
    (`pending.takes_text`: вердикт GC-05, категория, владелец, допущения) и
    «неизвестно» на `nfr:*` — запись в ответ держателя (`resend`). Ответ,
    который не разобрать (слово вне словаря, плохая дата), — `ValueError` с
    причиной. Возвращает причину отказа `discovery` или `None`."""
    kind, key = str(rec.get("kind", "")), str(rec.get("key", ""))
    repair = kind == pending.INTERVIEW_REPAIR
    if repair and pending.takes_text(key):
        return None if is_skip(text) else _apply_text(key, text, view, resend)
    if repair and key.startswith(pending.NUMERIC_KEY_PREFIX) and bv.is_unknown(text):
        return _apply_unknown_numeric(key, text, view, resend)
    if not is_skip(text):
        raise ValueError(NOT_CONTENT)
    if kind == pending.INTERVIEW_QUESTION:
        return discovery_refusal(send(key, to_payload(text, [], view)))
    return None


def _apply_text(key: str, text: str, view: SessionView, resend: Send) -> str | None:
    """Ответ-текст на вопрос починки — по виду ключа."""
    if key == ASSUMPTIONS_KEY:
        return _apply_assumptions(text, view, resend)
    how, arg = key.split(":", 1)
    if how == "category":
        return _apply_category(arg, text, view, resend)
    if how == "owner":
        return _apply_owner(arg, text, view, resend)
    return _apply_verdict(arg, text, view, resend)


def _apply_verdict(fr: str, text: str, view: SessionView, resend: Send) -> str | None:
    """Вердикт по `verdict:FR-NN` — как `gate_repair._fill_verdicts`."""
    qid = gr._feasibility_holder(view)
    if qid is None:
        return NO_HOLDER.format(what="вердикт")
    number = int(view.next_id("X").rsplit("-", 1)[1])
    entry = gr.verdict_entry(fr, text.strip(), number)
    doc = {"text": entry["body"], "entries": [entry]}
    return _resend_doc(qid, doc, view, resend)


def _apply_category(eid: str, text: str, view: SessionView, resend: Send) -> str | None:
    """Категория `NFR` — как `gate_repair_v13._fill_category`."""
    qid = view.holders.get(eid)
    if qid is None:
        return NO_HOLDER.format(what="категорию")
    record = view.entry_record(eid)
    categories = bv.parse_categories(text, gv.record_hypothesis(record))
    if categories is None:
        words = ", ".join(gv.CATEGORY_LABELS.values())
        raise ValueError(CATEGORY_REFUSED.format(words=words))
    return _resend_doc(qid, gv.category_doc(record, categories), view, resend)


def _apply_owner(domain: str, text: str, view: SessionView, resend: Send) -> str | None:
    """Владелец домена — как `gate_repair_v13._fill_owner`; уже назван — ничего."""
    qid = gr.topic_holder(view, "personas")
    if qid is None:
        return NO_HOLDER.format(what="владельца")
    doc = gv.owner_doc(domain, text, view)
    return _resend_doc(qid, doc, view, resend) if doc["entries"] else None


def _apply_unknown_numeric(
    key: str, text: str, view: SessionView, resend: Send
) -> str | None:
    """«Неизвестно» на `nfr:*` — `NFR` с целью из нового `Q`, как в терминале."""
    qid = gr.topic_holder(view, "nfr")
    if qid is None:
        return NO_HOLDER.format(what="требование")
    doc = gv.unknown_numeric_doc(key.split(":", 1)[1], text, view)
    return _resend_doc(qid, doc, view, resend)


def _apply_assumptions(text: str, view: SessionView, resend: Send) -> str | None:
    """Подтверждающий и срок — во все строки с допущением без них, каждому
    держателю; ответ без даты или с прошедшей — `ValueError`."""
    parsed = bv.parse_confirmation(text, date.today())
    if isinstance(parsed, str):
        raise ValueError(parsed)
    who, due = parsed
    for qid in dict.fromkeys(q for q, _row in gv.unconfirmed(view)):
        doc = gv.confirmation_doc(view, qid, who, due)
        reason = _resend_doc(qid, doc, view, resend)
        if reason is not None:
            return reason
    return None


def _resend_doc(
    qid: str, doc: dict[str, Any], view: SessionView, resend: Send
) -> str | None:
    """Переслать ответ держателя `qid`, дополненный `doc`; итог — причина
    отказа `discovery` или `None`."""
    payload = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    return discovery_refusal(resend(qid, gr.appended(view.held_answer(qid), payload)))


def discovery_refusal(reply: iv.DiscoveryReply) -> str | None:
    """Отказ `discovery` (коды 1/2) — причина; иначе `None`."""
    if reply.code not in (1, 2):
        return None
    return reply.envelope.get("operation", {}).get("reason", "") or "отказ"
