"""Просмотр состояния интервью и поправки собранных пунктов текстом.

`render_view` печатает по-человечески тему вопроса, покрытие тем, пункты и то,
что мешает готовности. `correct` отдаёт агенту действующие пункты и поправку,
получает изменённые пункты (с id), пересобирает ответ каждого держателя
целиком и отправляет его `--supersede` через `send`. Человек не видит ни
YAML, ни JSON, ни трассировок.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import yaml

from governance import interview as iv
from lite import structure as st
from lite.interview_state import Entry, SessionView
from lite.structure import Agent, Ask, Question

Send = Callable[[str, str], iv.DiscoveryReply]
Load = Callable[[str], SessionView]

NOT_UNDERSTOOD = "не понял, что исправить — переформулируйте поправку"
NO_QUESTION = "вопросов больше нет"
_FIELD_LABELS = {"Priority": "Priority", "Acceptance": "Acceptance",
                 "Target": "Target", "traces": "Связь"}  # fmt: skip
_TOPIC_RE = re.compile(r"required topic '(.+)' is not covered")
_TRACE_RE = re.compile(r"(\S+) has no trace to an existing G/J entry")
_BLOCKING_RE = re.compile(r"(\S+) is a blocking open question")


class CorrectError(Exception):
    """Поправку не применить; текст — для человека."""


@dataclass(frozen=True)
class _Change:
    """Одна правка пункта: прежняя и новая запись ответа."""

    old: dict[str, Any]
    new: dict[str, Any]


def render_view(
    view: SessionView, current: Question | None, unavailable: str | None = None
) -> str:
    """Состояние интервью текстом: вопрос, темы с пунктами, помехи готовности.

    `unavailable` — текущий вопрос получить не удалось (сбой `discovery
    status`, причина или пусто): вместо «вопросов больше нет»."""
    head = _question_line(view, current)
    if unavailable is not None:
        head = "текущий вопрос не получен" + (
            f" ({unavailable})" if unavailable else ""
        )
    lines = [head, ""]
    covered = view.covered()
    for topics in (view.required, view.optional):
        for key, prefix in topics.items():
            mark = "x" if key in covered else " "
            lines.append(f"[{mark}] {st.TOPIC_LABELS.get(key, key)}")
            lines += [f"    {_entry_line(e)}" for e in _topic_entries(view, prefix)]
    findings = [_finding_text(f, view) for f in view.findings]
    if findings:
        lines += ["", "Мешает готовности:", *(f"  - {f}" for f in findings)]
    return "\n".join(lines)


def _question_line(view: SessionView, current: Question | None) -> str:
    if current is None:
        return NO_QUESTION
    label = st.TOPIC_LABELS.get(current.coverage_key, current.coverage_key)
    return f"Вопрос {len(view.answers) + 1} ({label}): {current.text}"


def _topic_entries(view: SessionView, prefix: str | None) -> list[Entry]:
    if prefix is None:
        return []
    return [e for e in view.entries if e.id.rsplit("-", 1)[0] == prefix]


def _entry_line(entry: Entry) -> str:
    """`FR-01 тело — Must; критерий: …; → G-01` (отсутствующее опущено)."""
    f = entry.fields
    parts = [str(f["Priority"])] if f.get("Priority") else []
    if f.get("Acceptance"):
        parts.append(f"критерий: {f['Acceptance']}")
    elif f.get("Target"):
        parts.append(f"цель: {f['Target']}")
    if f.get("traces"):
        parts.append("→ " + ", ".join(map(str, f["traces"])))
    tail = f" — {'; '.join(parts)}" if parts else ""
    return f"{entry.id} {entry.body}{tail}"


def _finding_text(finding: str, view: SessionView) -> str:
    """Находка готовности `discovery` простыми словами (неизвестная — как есть)."""
    bodies = {e.id: e.body for e in view.entries}
    if m := _TOPIC_RE.fullmatch(finding):
        return f"не раскрыта тема: {st.TOPIC_LABELS.get(m[1], m[1])}"
    if m := _TRACE_RE.fullmatch(finding):
        return f"«{bodies.get(m[1], m[1])}» не связано ни с целью, ни с задачей"
    if m := _BLOCKING_RE.fullmatch(finding):
        return f"открытый вопрос мешает: «{bodies.get(m[1], m[1])}»"
    return finding


def correct(
    state: Any, text: str, *, agent: Agent, load: Load, send: Send
) -> str | Ask:
    """Применить поправку `text`; вернуть строки «что изменено» или `Ask`.

    `Ask` — агент просит уточнение: ничего не отправлено. Непонятая
    поправка, дыра в обязательных полях, отказ `discovery` и недоступный
    агент — `CorrectError`. Пункты держателя пересобираются целиком и уходят
    `send(question_id, yaml)`; отправитель обязан слать с `--supersede`."""
    session = str((state.interview or {}).get("session_id"))
    view = load(session)
    try:
        items, ask = _changed_items(agent, _prompt(text, view), view)
    except st.AgentUnavailable as exc:
        raise CorrectError(f"агент недоступен: {exc}") from exc
    if ask:
        return Ask(ask)
    changes = _changes(items, view)
    _check_required(changes, view)
    _send_all(_by_holder(changes, view), send, changes, view)
    lines = [line for c in changes for line in _describe(c)]
    return "\n".join(lines + _remaining_hints(changes, view))


def _changed_items(
    agent: Agent, prompt: str, view: SessionView
) -> tuple[list[dict[str, Any]], str | None]:
    """Вызов агента; плохая форма или чужой id — один повтор, затем отказ."""
    try:
        return _ask_once(agent, prompt, view)
    except st.FormError as exc:
        note = f"Твой предыдущий ответ отклонён: {exc}. Верни исправленный JSON."
        try:
            return _ask_once(agent, f"{prompt}\n\n{note}", view)
        except st.FormError as exc2:
            raise CorrectError(NOT_UNDERSTOOD) from exc2


def _ask_once(
    agent: Agent, prompt: str, view: SessionView
) -> tuple[list[dict[str, Any]], str | None]:
    items, ask = st.parse_items(agent(prompt))
    if ask:
        return items, ask
    errors = st.form_errors(items, view) + _id_errors(items)
    if errors:
        raise st.FormError("; ".join(errors))
    return items, None


def _id_errors(items: list[dict[str, Any]]) -> list[str]:
    """Поправка — только пункты с id (действующие id и повторы проверяет
    `st.form_errors`)."""
    if not items:
        return ["нет ни одного изменённого пункта"]
    return [f"пункт {n}: нет id" for n, i in enumerate(items, 1) if "id" not in i]


def _changes(items: list[dict[str, Any]], view: SessionView) -> list[_Change]:
    """Правки с реальным отличием; ни одной — «не понял»."""
    changes = []
    for item in items:
        old = view.entry_record(item["id"])
        new = st.merge_entry(old, item)
        if new != old:
            changes.append(_Change(old, new))
    if not changes:
        raise CorrectError(NOT_UNDERSTOOD)
    return changes


def _gaps(entry: dict[str, Any], view: SessionView, body: str) -> list[str]:
    """Дыры обязательных полей пункта; формулировка — по `body`."""
    item = st.entry_as_item({**entry, "body": body})
    return st.validate([item], Question("", "", ""), view)


def _check_required(changes: list[_Change], view: SessionView) -> None:
    """Поправка не должна вносить дыры в обязательные поля пункта.

    Прежние дыры (пункт был неполным до поправки) не мешают: поправка
    сама по себе верна."""
    gaps = [g for c in changes for g in _introduced_gaps(c, view)]
    if gaps:
        raise CorrectError("поправка оставила пробелы:\n" + "\n".join(gaps))


def _introduced_gaps(change: _Change, view: SessionView) -> list[str]:
    """Дыры нового пункта, которых не было у прежнего (с тем же телом)."""
    body = change.new["body"]
    before = _gaps(change.old, view, body)
    return [g for g in _gaps(change.new, view, body) if g not in before]


def _remaining_hints(changes: list[_Change], view: SessionView) -> list[str]:
    """«у FR-01 по-прежнему нет: …» — прежние дыры, не закрытые поправкой."""
    return [
        f"у {c.new['id']} по-прежнему нет: {g} — дополните ответом или dfl correct"
        for c in changes
        for g in _gaps(c.new, view, c.new["body"])
    ]


def _by_holder(changes: list[_Change], view: SessionView) -> dict[str, dict[str, Any]]:
    """question_id → тело ответа, пересобранное целиком."""
    out: dict[str, dict[str, Any]] = {}
    for change in changes:
        eid = change.new["id"]
        qid = view.holders[eid]
        answer = out.setdefault(qid, view.held_answer(qid))
        entries = answer["entries"]
        for n, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("id") == eid:
                entries[n] = change.new
                break
        else:
            entries.append(change.new)
    return out


def _send_all(
    answers: dict[str, dict[str, Any]],
    send: Send,
    changes: list[_Change],
    view: SessionView,
) -> None:
    """Слать держателей по очереди; отказ называет применённое и нет."""
    ids: dict[str, list[str]] = {}
    for c in changes:
        ids.setdefault(view.holders[c.new["id"]], []).append(c.new["id"])
    applied: list[str] = []
    for n, (qid, answer) in enumerate(answers.items()):
        body = yaml.safe_dump(answer, allow_unicode=True, sort_keys=False)
        reply = send(qid, body)
        if reply.code not in (1, 2):
            applied += ids[qid]
            continue
        reason = reply.envelope.get("operation", {}).get("reason", "") or "отказ"
        if not applied:
            raise CorrectError(f"discovery не принял поправку ({reason})")
        rest = [i for q in list(answers)[n:] for i in ids[q]]
        raise CorrectError(
            f"применено: {', '.join(applied)}; "
            f"не применено: {', '.join(rest)} (discovery: {reason})"
        )


def _describe(change: _Change) -> list[str]:
    """Строки «что изменено»: тело `FR-01: «…» → «…»`, поля `FR-01 Поле: …`."""
    eid = change.new["id"]
    lines = []
    if change.old.get("body") != change.new["body"]:
        lines.append(f"{eid}: «{change.old.get('body')}» → «{change.new['body']}»")
    for key, label in _FIELD_LABELS.items():
        before, after = change.old.get(key), change.new.get(key)
        if before != after:
            lines.append(f"{eid} {label}: «{_plain(before)}» → «{_plain(after)}»")
    return lines


def _plain(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else str(value)


def _prompt(text: str, view: SessionView) -> str:
    """Промпт агента: действующие пункты, поправка, схема ответа."""
    prefixes = ", ".join(view.prefixes())
    existing = "\n".join(_prompt_line(e) for e in view.entries)
    return f"""\
Человек поправляет уже собранные пункты интервью о требованиях.

Действующие пункты:
{existing or "- (нет)"}

Поправка человека:
<<<
{text}
>>>

Верни только изменённые пункты: каждый с его "id" (из списка выше), тем же
"prefix", новым "body" и полями; не затронутые поправкой пункты не включай.
Меняй только то, что сказано в поправке, ничего не выдумывай. Поля как в
списке: traces — id существующих пунктов, priority — одно из Must, Should,
Could, Won't, acceptance, target. Допустимые префиксы: {prefixes}.
Если непонятно, какой пункт или что менять, — items пустой, а в "ask" один
короткий вопрос простыми словами, без id.

Верни только JSON, без пояснений:
{{"items": [{{"id": "FR-01", "prefix": "FR", "body": "...", "traces": ["G-01"],
"priority": "Must", "acceptance": "...", "target": null}}], "ask": null}}
"""


def _prompt_line(entry: Entry) -> str:
    fields = "; ".join(f"{k}: {_plain(v)}" for k, v in entry.fields.items())
    return f"- {entry.id}: {entry.body}" + (f" ({fields})" if fields else "")
