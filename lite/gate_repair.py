"""Починка брифа по находкам проверки (gate) после конца интервью.

Интервью кончилось, темы покрыты, а проверка брифа не прошла — `dfl` чинит
то, что можно спросить у человека или вывести из уже записанного:

- GC-08 «нет Acceptance» (FR, NFR) — вопрос «как проверить?», ответ
  дословно в `Acceptance` пункта (devtools#753);
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
from lite.structure import Agent, AgentUnavailable, FormError, first_json_object

Ask = Callable[[str], str | None]  # ответ человека; `None` — пропуск
Send = Callable[[str, str], iv.DiscoveryReply]
Load = Callable[[str], SessionView]

NO_CRITERION_RE = re.compile(r"^GC-08 error \[([A-Z]+-\d+)\] нет \*\*Acceptance\*\*")
NO_SYSTEM_RE = re.compile(r"^GC-13 error \[((?:IF|AP)-\d+)\]")
NO_VERDICT_RE = re.compile(
    r"^GC-05 error \[coverage\.feasibility_review\] Must-требование (FR-\d+)"
)
YES_WORDS = frozenset({"да", "ок", "ok", "y", "yes", "верно"})
FEASIBILITY_KEY = "feasibility_review"

CRITERIA_INTRO = (
    "интервью окончено, но бриф не прошёл проверку: у пунктов ниже нет "
    "способа проверить, что они выполнены"
)
CRITERIA_HINT = (
    "что проверить или какое значение приемлемо, хоть грубо "
    "(«пропустить» — оставить так)"
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
    left = _fill_criteria(_ids(NO_CRITERION_RE, lines), session, ask, resend, load)
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
    eids: list[str], session: str, ask: Ask, resend: Send, load: Load
) -> int:
    """Пункты без критерия: вопрос по каждому, ответ дословно в `Acceptance`.
    Возвращает число неисправленных."""
    if not eids:
        return 0
    say(CRITERIA_INTRO)
    left = 0
    for eid in eids:
        view = load(session)
        qid = view.holders.get(eid)
        if qid is None:
            left += 1
            continue
        record = view.entry_record(eid)
        text = ask(f"Как проверить «{record['body']}»? {CRITERIA_HINT}")
        if text is None:
            left += 1
            continue
        note = f"Критерий для «{record['body']}»: {text}"
        entry = {**record, "Acceptance": text}
        if not _resent(qid, note, [entry], view, resend):
            left += 1
    return left


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
    if answer is None or answer.strip().casefold() not in YES_WORDS:
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
        ok = text.strip().casefold() in YES_WORDS
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
