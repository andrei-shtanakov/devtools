"""Починка брифа по находкам проверки (gate) после конца интервью.

Интервью кончилось, темы покрыты, а проверка брифа не прошла — `dfl` чинит
то, что можно спросить у человека или вывести из уже записанного:

- GC-08 «нет Acceptance» (FR, NFR) — гипотезу критерия (и `Q` исследования
  для недостающего числа) предлагает агент, человек подтверждает; иные слова
  человека — в `Preference` пункта, гипотеза по ним записывается без второго
  вопроса (контракт брифа v1.2; было — ответ дословно, devtools#753);
- GC-13 (IF без связи с системой S, AP без связи с S/CON) — связи
  предлагает агент по записанным пунктам, человек подтверждает (#755);
- GC-05 (Must-FR customer-брифа без вердикта реализуемости) — вопрос по
  каждой функции, вердикт — запись `X` с `target: FR-NN`: `resolved` —
  реализуемо как есть, `open` — расхождение словами инженера (#755).

Каждая правка — пересылка ответа держателя (`--supersede`) с прежними
записями. Что не починено (пропуск, отказ) — человеку число и подсказка.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import yaml

from governance import interview as iv
from governance.discovery_contract import gate_check
from lite.interview_state import SessionView
from lite.output import say
from lite.structure import (
    Agent,
    AgentUnavailable,
    FormError,
    criterion_errors,
    first_json_object,
    form_errors,
    parse_items,
    retry_note,
    to_payload,
)

Ask = Callable[[str], str | None]  # ответ человека; `None` — пропуск
Send = Callable[[str, str], iv.DiscoveryReply]
Load = Callable[[str], SessionView]

NO_CRITERION_RE = re.compile(r"^GC-08 error \[([A-Z]+-\d+)\] нет \*\*Acceptance\*\*")
NO_SYSTEM_RE = re.compile(r"^GC-13 error \[((?:IF|AP)-\d+)\]")
NO_VERDICT_RE = re.compile(
    r"^GC-05 error \[coverage\.feasibility_review\] Must-требование (FR-\d+)"
)
# Согласие: первое слово — из YES_WORDS, остальные — из YES_TAIL («ок, давай»,
# «да, всё верно», «согласна, так и запишите»); регистр, пунктуация и «ё» —
# не важны. Иные слова — уточнение человека.
YES_WORDS = frozenset({
    "да", "ок", "окей", "ok", "okay", "y", "yes", "верно", "согласен",
    "согласна", "согласны", "подходит", "годится", "ага", "угу", "конечно",
    "хорошо", "точно", "именно",
})  # fmt: skip
YES_TAIL = YES_WORDS | {
    "так", "все", "давай", "давайте", "и", "запиши", "запишите", "записывай",
    "записывайте", "пиши", "пишите", "можно",
}  # fmt: skip
# Голое отрицание или отмахивание («нет», «не так», «нет, не то») — как «нет».
NO_WORDS = frozenset({"нет", "не", "no", "n"})
NO_TAIL = NO_WORDS | {"так", "то", "это"}
Q_ID_RE = re.compile(r"\bQ-\d+\b")
FEASIBILITY_KEY = "feasibility_review"

CRITERIA_INTRO = (
    "интервью окончено, но бриф не прошёл проверку: у пунктов ниже нет "
    "способа проверить, что они выполнены — предлагаю, как проверять"
)
CRITERIA_CONFIRM = (
    "«{body}»: проверка — {hypothesis}. Так? («да» — записать; иначе скажите "
    "своими словами, что имелось в виду; «пропустить»)"
)
TRACES_INTRO = (
    "бриф не прошёл проверку: интерфейсы и архитектурные предпочтения должны "
    "быть связаны с системами (или ограничениями); предлагаю связи:"
)
TRACES_CONFIRM = "Записать так? («да» — записать, «пропустить» — оставить)"
VERDICT_INTRO = (
    "бриф не прошёл проверку: по каждой обязательной функции из брифа "
    "заказчика нужен вердикт инженера"
)
VERDICT_HINT = (
    "реализуемо как есть? «да» — да; иначе — что не так и как реалистично "
    "(«пропустить» — оставить)"
)
WORDS_ASK = "скажите своими словами, что имелось в виду («пропустить» — оставить)"
WORDS_KEPT = "ваши слова записаны, критерий предложит следующий dfl run"
GATE_LEFT = (
    "не исправлено пунктов: {n} — бриф не пройдёт проверку; dfl run спросит снова"
)


def repair(
    findings: list[Any],
    session: str,
    *,
    ask: Ask,
    agent: Agent,
    resend: Send,
    load: Load,
    upstream: Callable[[], str],
) -> None:
    """Починить по находкам `findings` всё, что умеем; итог — человеку."""
    lines = [str(f) for f in findings]
    left = _fill_criteria(
        _ids(NO_CRITERION_RE, lines), session, ask, agent, resend, load
    )
    left += _fill_traces(_ids(NO_SYSTEM_RE, lines), session, ask, agent, resend, load)
    left += _fill_verdicts(
        _ids(NO_VERDICT_RE, lines), session, ask, resend, load, upstream
    )
    if left:
        say(GATE_LEFT.format(n=left))


def appended(held: dict[str, Any], payload: str) -> str:
    """Ответ держателя + новый: текст через пустую строку, записи — прежние
    (с заменой по id) и новые."""
    new = yaml.safe_load(payload)
    out = dict(held)
    out["text"] = "\n\n".join(t for t in (held.get("text"), new["text"]) if t)
    fresh = {e["id"]: e for e in new.get("entries") or []}
    entries = [fresh.pop(e.get("id"), e) for e in held.get("entries") or []]
    entries += fresh.values()
    out.pop("entries", None)
    if entries:
        out["entries"] = entries
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


def _ids(pattern: re.Pattern[str], findings: list[str]) -> list[str]:
    """id из находок по образцу, без повторов, в порядке находок."""
    found = (pattern.match(f) for f in findings)
    return list(dict.fromkeys(m.group(1) for m in found if m))


def _resent(
    qid: str, text: str, entries: list[dict[str, Any]], view: SessionView, resend: Send
) -> bool:
    """Переслать ответ держателя `qid` с записями `entries`; отказ — причина
    человеку, `False`."""
    payload = yaml.safe_dump(
        {"text": text, "entries": entries}, allow_unicode=True, sort_keys=False
    )
    reply = resend(qid, appended(view.held_answer(qid), payload))
    if reply.code not in (1, 2):
        return True
    reason = reply.envelope.get("operation", {}).get("reason", "") or "отказ"
    say(f"не записано: {reason}")
    return False


# --- GC-08: критерий проверки ---


def _fill_criteria(
    eids: list[str], session: str, ask: Ask, agent: Agent, resend: Send, load: Load
) -> int:
    """Пункты без критерия: гипотезу предлагает агент, человек подтверждает.
    Возвращает число неисправленных."""
    if not eids:
        return 0
    say(CRITERIA_INTRO)
    fixed = [_fill_criterion(eid, load(session), ask, agent, resend) for eid in eids]
    return fixed.count(False)


def _fill_criterion(
    eid: str, view: SessionView, ask: Ask, agent: Agent, resend: Send
) -> bool:
    """Один пункт: «да» — гипотеза записана; иные слова — в `Preference`,
    гипотеза по ним записана без второго вопроса (не вышла — записаны только
    слова, пункт не исправлен); пропуск или сбой агента — не исправлен
    (вопроса «как проверить» нет: критерий — перевод агента)."""
    qid = view.holders.get(eid)
    if qid is None:
        return False
    record = view.entry_record(eid)
    first = _proposed_criterion(eid, qid, view, agent, None)
    if first is None:
        return False
    entries, shown, research = first
    question = CRITERIA_CONFIRM.format(body=record["body"], hypothesis=shown)
    answer = _confirmation(ask(question), ask)
    if answer is None:
        return False
    if answer is True:
        note = f"Критерий для «{record['body']}»: {shown}"
        return _resent(qid, note, entries, view, resend)
    words = _without_ids(answer, research)
    note = f"Уточнение к «{record['body']}»: {words}"
    second = _proposed_criterion(eid, qid, view, agent, words)
    if second is None:
        kept = {**record, "Preference": _preference(record, words)}
        if _resent(qid, note, [kept], view, resend):
            say(WORDS_KEPT)
        return False
    entries, shown, _research = second
    if not _resent(qid, note, entries, view, resend):
        return False
    say(f"записано: «{record['body']}»: проверка — {shown}")
    return True


def _confirmation(
    answer: str | None, ask: Ask, again: bool = True
) -> bool | str | None:
    """Ответ на гипотезу: `True` — согласие («Да.», «согласен», «ок, давай»),
    строка — слова человека, `None` — пропуск. Голое «нет», «не так», ответ
    без букв — один раз попросить слов (сам ответ в `Preference` не пишется)."""
    if answer is None:
        return None
    if _agrees(answer):
        return True
    tokens = _tokens(answer)
    if not tokens or (tokens[0] in NO_WORDS and set(tokens) <= NO_TAIL):
        return _confirmation(ask(WORDS_ASK), ask, False) if again else None
    return answer.strip()


def _tokens(answer: str) -> list[str]:
    """Слова ответа: без регистра и пунктуации, «ё» как «е» (цифры — слова)."""
    return re.findall(r"[^\W_]+", answer.casefold().replace("ё", "е"))


def _agrees(answer: str) -> bool:
    """Обычное согласие («да», «согласен», «ок, давай», «да, всё верно»)."""
    tokens = _tokens(answer)
    return bool(tokens) and tokens[0] in YES_WORDS and set(tokens) <= YES_TAIL


def _without_ids(words: str, research: dict[str, str]) -> str:
    """Id `Q-NN` в словах человека → «текст исследования» (неизвестный —
    выброшен): в `Preference` висячий id завалил бы GC-17."""
    out = Q_ID_RE.sub(
        lambda m: f"«{research[m.group(0)]}»" if m.group(0) in research else "",
        words,
    )
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _preference(record: dict[str, Any], words: str) -> str:
    """Прежнее предпочтение пункта и новые слова человека."""
    return "; ".join(p for p in (record.get("Preference"), words) if p)


def _proposed_criterion(
    eid: str, qid: str, view: SessionView, agent: Agent, words: str | None
) -> tuple[list[dict[str, Any]], str, dict[str, str]] | None:
    """Гипотеза агента → (записи: пункт и новые `Q`; гипотеза для человека —
    без id; {id: текст} исследований). Слова человека `words` дописываются в
    `Preference`. Нарушение формы — один повтор агента; сбой — причина
    человеку, `None`."""
    record = view.entry_record(eid)
    if words is not None:
        record["Preference"] = _preference(record, words)
    said = view.held_answer(qid).get("text", "")
    prompt = _criterion_prompt(record, view, said, words)
    try:
        try:
            items = _criterion_items(eid, record, view, agent(prompt))
        except FormError as exc:
            retry = f"{prompt}\n\n{retry_note(str(exc))}"
            items = _criterion_items(eid, record, view, agent(retry))
    except (AgentUnavailable, FormError) as exc:
        say(f"критерий для «{record['body']}» не предложен: {exc}")
        return None
    own, *fresh = yaml.safe_load(to_payload("", items, view))["entries"]
    research = {e.id: e.body for e in view.entries if _prefix(e.id) == "Q"}
    research.update((q["id"], q["body"]) for q in fresh)
    hypothesis = "; ".join(own[k] for k in ("Acceptance", "Target") if own.get(k))
    for q in fresh:
        say(f"  найти: {q['body']}")
    shown = Q_ID_RE.sub(
        lambda m: f"«{research.get(m.group(0), 'значение исследования')}»",
        hypothesis,
    )
    return [own, *fresh], shown, research


def _criterion_items(
    eid: str, record: dict[str, Any], view: SessionView, raw: str
) -> list[dict[str, Any]]:
    """Ответ агента → пункты раскладки: обновление `eid` (только критерий;
    body, важность — прежние, предпочтение — из `record`) и новые `Q`, на
    которые критерий ссылается. Иное — `FormError`."""
    items, _ask = parse_items(raw)
    own = [i for i in items if i.get("id") == eid]
    fresh = [i for i in items if i.get("id") != eid]
    if len(own) != 1 or any(i["prefix"] != "Q" or "id" in i for i in fresh):
        raise FormError(f"нужен ровно пункт {eid} и, при нужде, новые Q")
    kept = {k: own[0][k] for k in ("acceptance", "target") if k in own[0]}
    if record.get("Preference"):
        kept["preference"] = record["Preference"]
    item = {"id": eid, "prefix": own[0]["prefix"], "body": record["body"],
            "traces": [], **kept}  # fmt: skip
    errors = form_errors([item, *fresh], view) or criterion_errors([item], view)
    criterion = " ".join(kept.get(k, "") for k in ("acceptance", "target"))
    errors += [
        f"Q «{q['body']}» не упомянут в критерии — сошлись на его ref или убери"
        for q in fresh
        if not _mentions(criterion, q.get("ref"))
    ]
    if errors:
        raise FormError("; ".join(errors))
    return [item, *fresh]


def _mentions(text: str, ref: str | None) -> bool:
    """Отдельно стоящий `ref` в тексте."""
    if not ref:
        return False
    return re.search(rf"(?<![\w-]){re.escape(ref)}(?![\w-])", text) is not None


def _criterion_prompt(
    record: dict[str, Any], view: SessionView, said: str, words: str | None
) -> str:
    """Промпт агента: пункт, слова держащего ответа и уточнение человека,
    известные `Q`."""
    eid = str(record["id"])
    fields = "; ".join(
        f"{k}: {v}" for k, v in record.items() if k not in ("id", "body")
    )
    redo = (
        f"\nЧеловек отклонил прежнюю гипотезу и уточнил: «{words}» — переведи "
        "именно это.\n"
        if words
        else ""
    )
    research = "\n".join(
        f"- {e.id}: {e.body}" for e in view.entries if _prefix(e.id) == "Q"
    )
    field = "acceptance" if _prefix(eid) == "FR" else "acceptance или target"
    return f"""\
Ты аналитик-переводчик: у пункта брифа нет критерия проверки. Переведи
предпочтение человека в проверяемое ограничение — гипотезу, которую человек
подтвердит. Не выдумывай сути: только из пункта и слов человека.

Пункт {eid}: {record["body"]}
Поля пункта: {fields or "(нет)"}

Слова человека (ответ, в котором записан пункт):
<<<
{said}
>>>
{redo}
Правила:
- верни пункт с "id": "{eid}", "prefix": "{_prefix(eid)}", его body и полем
  {field} — твоей гипотезой (у человека не спрашивай);
- если для гипотезы нужно число (порог, baseline), а человек его не назвал, —
  не выдумывай: заведи в этом же ответе пункт Q (body — какое значение найти,
  owner_role — analyst или engineer, "ref": "q-1") и сошлись на него в
  критерии этим ref: «… не хуже значения из q-1»;
- ссылаться на Q-NN можно только на уже записанные:
{research or "- (нет)"}
- других пунктов не возвращай.

Верни только JSON, без пояснений:
{{"items": [{{"id": "{eid}", "prefix": "{_prefix(eid)}", "body": "...",
"acceptance": "..."}}, {{"prefix": "Q", "body": "...", "ref": "q-1",
"owner_role": "analyst"}}]}}
"""


# --- GC-13: связи IF → S, AP → S/CON ---

_TRACE_TARGETS = {"IF": ("S",), "AP": ("S", "CON")}


def _fill_traces(
    eids: list[str], session: str, ask: Ask, agent: Agent, resend: Send, load: Load
) -> int:
    """Связи пунктов с системами: предлагает агент, подтверждает человек."""
    if not eids:
        return 0
    view = load(session)
    try:
        proposed = _proposed_traces(eids, view, agent)
    except (AgentUnavailable, FormError) as exc:
        say(f"связи не предложены: {exc}")
        return len(eids)
    bodies = {e.id: e.body for e in view.entries}
    say(TRACES_INTRO)
    for eid, traces in proposed.items():
        say(f"  «{bodies[eid]}» → " + ", ".join(f"«{bodies[t]}»" for t in traces))
    answer = ask(TRACES_CONFIRM)
    if answer is None or not _agrees(answer):
        return len(eids)
    left = len(eids) - len(proposed)
    for qid, group in _by_holder(proposed, view).items():
        fresh = load(session)
        entries = [{**fresh.entry_record(e), "traces": proposed[e]} for e in group]
        if not _resent(qid, "Связи с системами уточнены.", entries, fresh, resend):
            left += len(group)
    return left


def _proposed_traces(
    eids: list[str], view: SessionView, agent: Agent
) -> dict[str, list[str]]:
    """Ответ агента → {id: [цели]}; только допустимые цели, пустые выпадают.
    Агент не ответил JSON-объектом — `FormError`."""
    by_id = {e.id: e for e in view.entries}
    targets = [e for e in view.entries if _prefix(e.id) in ("S", "CON")]
    items = [by_id[eid] for eid in eids if eid in by_id]
    raw = first_json_object(agent(_traces_prompt(items, targets)))
    links = raw.get("traces")
    if not isinstance(links, dict):
        raise FormError('нет объекта "traces"')
    out: dict[str, list[str]] = {}
    for item in items:
        allowed = _TRACE_TARGETS[_prefix(item.id)]
        got = links.get(item.id)
        picked = [
            t
            for t in (got if isinstance(got, list) else [])
            if isinstance(t, str) and t in by_id and _prefix(t) in allowed
        ]
        if picked:
            out[item.id] = list(dict.fromkeys(picked))
    return out


def _traces_prompt(items: list[Any], targets: list[Any]) -> str:
    """Промпт агента: пункты IF/AP и кандидаты-цели S/CON."""
    listed = "\n".join(f"- {e.id}: {e.body}" for e in items)
    known = "\n".join(f"- {e.id}: {e.body}" for e in targets)
    return f"""\
Ты связываешь пункты брифа инженера с системами и ограничениями.

Правила: интерфейс (IF) связывается с системами (S), к которым он относится;
архитектурное предпочтение (AP) — с системами (S) или ограничениями (CON),
которые оно затрагивает. Только id из списка целей ниже; ничего не выдумывай.
Если пункт ни к чему не относится — пустой список.

Пункты:
{listed}

Цели:
{known or "- (нет)"}

Верни только JSON, без пояснений:
{{"traces": {{"IF-01": ["S-01"], "AP-01": ["S-01", "CON-02"]}}}}
"""


def _by_holder(
    proposed: dict[str, list[str]], view: SessionView
) -> dict[str, list[str]]:
    """Пункты, сгруппированные по держащему их ответу (порядок сохранён)."""
    groups: dict[str, list[str]] = {}
    for eid in proposed:
        qid = view.holders.get(eid)
        if qid is not None:
            groups.setdefault(qid, []).append(eid)
    return groups


# --- GC-05: вердикт реализуемости по Must-FR ---


def _fill_verdicts(
    frs: list[str],
    session: str,
    ask: Ask,
    resend: Send,
    load: Load,
    upstream: Callable[[], str],
) -> int:
    """Вердикт по каждой Must-функции → запись `X` (`target`, `status`) в
    ответе на вопрос реализуемости (иначе — в последнем отвеченном)."""
    if not frs:
        return 0
    view = load(session)
    qid = _feasibility_holder(view)
    if qid is None:
        return len(frs)
    bodies = must_bodies(upstream())
    say(VERDICT_INTRO)
    number = int(view.next_id("X").rsplit("-", 1)[1])
    entries, notes = [], []
    for fr in frs:
        label = bodies.get(fr, fr)
        text = ask(f"{fr} «{label}» — {VERDICT_HINT}")
        if text is None:
            continue
        ok = _agrees(text)
        verdict = "реализуемо как есть" if ok else text
        entries.append({
            "id": f"X-{number:02d}",
            "body": f"{fr}: {verdict}",
            "target": fr,
            "status": "resolved" if ok else "open",
        })  # fmt: skip
        notes.append(f"{fr}: {verdict}")
        number += 1
    if not entries:
        return len(frs)
    if not _resent(qid, "\n".join(notes), entries, view, resend):
        return len(frs)
    return len(frs) - len(entries)


def _feasibility_holder(view: SessionView) -> str | None:
    """Последний отвеченный вопрос реализуемости, иначе последний отвеченный."""
    answered = list(view.answers)
    own = [q for q in answered if view.asked.get(q) == FEASIBILITY_KEY]
    return (own or answered or [None])[-1]


def must_bodies(text: str) -> dict[str, str]:
    """Текст customer-брифа → {FR-NN: формулировка} Must-функций."""
    _meta, body = gate_check.split_frontmatter(text)
    out = {}
    for entry in gate_check.parse_body(body):
        if entry.prefix == "FR" and entry.priority() == "Must":
            head = entry.lines[0] if entry.lines else ""
            out[entry.eid] = re.sub(r"^-\s*\*\*[A-Z]+-\d+\*\*\s*", "", head).strip()
    return out


def _prefix(eid: str) -> str:
    return eid.rsplit("-", 1)[0]
