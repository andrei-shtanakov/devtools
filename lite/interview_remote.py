"""Интервью без терминала: следующий вопрос человеку и применение reply (#766).

Та же последовательность, что у `lite.interview_chat.chat`, но по одному
вопросу на вызов и без агента: (1) текущий вопрос `discovery` (код 20);
(2) после неполного конца — добор непокрытых обязательных тем; (3) починка
находок проверки брифа в порядке `lite.gate_repair` (GC-08, GC-13, GC-05).
Раскладку ответа делает скил по `prompt` записи вопроса; пропуски и
исправления пунктов прохода — отметки в `interview-pass.json` каталога
прогона. Человек не видит ни JSON, ни YAML, ни id пунктов.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from governance import interview as iv
from lite import gate_repair as gr
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
TRACES_GOAL = "С какими системами или ограничениями связаны: {bodies}?"
NOT_CONTENT = "ответ по существу — через dfl answer --items"


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
    q = current_question(reply)
    if q is not None:
        return _question(q, view)
    if not incomplete:
        return None
    seen = {*marks.get("skipped", ()), *marks.get("done", ())}
    candidates = [*_top_ups(view), *_repairs(reply, view, upstream)]
    for q_, data in candidates:
        if q_.key not in seen:
            return q_, {**data, "coverage": coverage(view), "findings": findings(view)}
    return None


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
    lines = [str(f) for f in reply.envelope.get("findings") or []]
    out = [_criterion(eid, view) for eid in gr._ids(gr.NO_CRITERION_RE, lines)]
    out.append(_traces(gr._ids(gr.NO_SYSTEM_RE, lines), view))
    bodies = gr.must_bodies(upstream) if upstream else {}
    out += [_verdict(fr, bodies, view) for fr in gr._ids(gr.NO_VERDICT_RE, lines)]
    return [item for item in out if item is not None]


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
    """GC-13: один вопрос на все IF/AP без связей."""
    eids = [eid for eid in eids if eid in view.holders]
    if not eids:
        return None
    qid = view.holders[eids[0]]
    records = [view.entry_record(eid) for eid in eids]
    key = "traces:" + ",".join(eids)
    q = _content(pending.INTERVIEW_REPAIR, key, gr.TRACES_INTRO)
    bodies = ", ".join(f"«{r['body']}»" for r in records)
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
    починки — ничего (отметку ставит вызывающий); вердикт GC-05 — запись `X`
    в ответ держателя реализуемости (`resend`). Возвращает причину отказа
    `discovery` или `None`."""
    kind, key = str(rec.get("kind", "")), str(rec.get("key", ""))
    if kind == pending.INTERVIEW_REPAIR and key.startswith("verdict:"):
        return None if is_skip(text) else _apply_verdict(key, text, view, resend)
    if not is_skip(text):
        raise ValueError(NOT_CONTENT)
    if kind == pending.INTERVIEW_QUESTION:
        return _refusal(send(key, to_payload(text, [], view)))
    return None


def _apply_verdict(key: str, text: str, view: SessionView, resend: Send) -> str | None:
    """Вердикт по `verdict:FR-NN` — как `gate_repair._fill_verdicts`."""
    fr = key.split(":", 1)[1]
    qid = gr._feasibility_holder(view)
    if qid is None:
        return "нет ответа, к которому записать вердикт"
    number = int(view.next_id("X").rsplit("-", 1)[1])
    entry = gr.verdict_entry(fr, text.strip(), number)
    payload = yaml.safe_dump(
        {"text": entry["body"], "entries": [entry]}, allow_unicode=True, sort_keys=False
    )
    return _refusal(resend(qid, gr.appended(view.held_answer(qid), payload)))


def _refusal(reply: iv.DiscoveryReply) -> str | None:
    """Отказ `discovery` (коды 1/2) — причина; иначе `None`."""
    if reply.code not in (1, 2):
        return None
    return reply.envelope.get("operation", {}).get("reason", "") or "отказ"
