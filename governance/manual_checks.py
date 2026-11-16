"""Ручные проверки в конце прогона dfl (devtools#771).

Общее ядро enterprise- и личного пути: собрать проверки `kind: manual` из
бандла прогона, тексты вопроса/issue/комментария/итога и запись ответов.
Вопрос человеку живёт в `lite.manual_ask` (governance не зависит от `lite`).

Запись (одна форма в обоих путях)::

    {"status": "started"|"completed", "checks": [ManualCheck как dict],
     "answers": {beh: {"verdict", "note", "at", "issue"}}, "commented": bool}

Ответ окончателен (решение владельца 16.11): запись не меняется, а
комментарий в PR — снимок на момент приёмки.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from governance.decomposition_guard import parse_dt_tasks
from governance.task_bridge import _BEH_HEADER, parse_behaviour

DONE, NOT_DONE, CAVEAT = "done", "not_done", "caveat"
VERDICT_WORDS = {DONE: "выполнено", NOT_DONE: "не выполнено", CAVEAT: "с оговоркой"}
OPTIONS = "1. выполнено · 2. не выполнено · 3. принять с оговоркой"

#: снятый сценарий — `status: waived` в строке checked_by (как criteria_graph)
_WAIVED = re.compile(r"\*\*checked_by\*\*.*`status:\s*waived`")


@dataclass(frozen=True)
class ManualCheck:
    """Одна ручная проверка: сценарий, его задача и документ шагов."""

    beh_id: str
    title: str
    task_id: str
    steps: str | None


def as_dict(check: ManualCheck) -> dict:
    """Проверка в форме записи прогона."""
    return asdict(check)


def checks_of(record: Mapping) -> list[ManualCheck]:
    """Проверки из записи прогона."""
    return [ManualCheck(**c) for c in record.get("checks") or []]


def _waived(behaviour_text: str) -> set[str]:
    found: set[str] = set()
    current = None
    for line in behaviour_text.splitlines():
        header = _BEH_HEADER.match(line)
        if header:
            current = header.group(1)
        elif current and _WAIVED.search(line):
            found.add(current)
    return found


def collect(behaviour_text: str, decomposition_text: str) -> list[ManualCheck]:
    """Ручные проверки бандла в порядке задач; BEH — один раз (первая DT).

    Номер задачи — `TASK-NNN` по порядку DT (как `task_bridge.render_tasks_dt`).
    Ошибка разбора — `ValueError` наружу: «бандл не прочитан» ≠ «проверок нет».
    """
    scenarios = {s.beh_id: s for s in parse_behaviour(behaviour_text)}
    dts, _errors = parse_dt_tasks(decomposition_text)
    waived = _waived(behaviour_text)
    seen: set[str] = set()
    out: list[ManualCheck] = []
    for idx, dt in enumerate(dts, start=1):
        for beh in dt.scenarios:
            sc = scenarios.get(beh)
            if sc is None or sc.checked_kind != "manual" or beh in waived:
                continue
            if beh in seen:
                continue
            seen.add(beh)
            out.append(ManualCheck(beh, sc.title, f"TASK-{idx:03d}", sc.checked_target))
    return out


def question(check: ManualCheck) -> str:
    """Вопрос по одной проверке (форма дизайна §3.3)."""
    lines = [f"Ручная проверка {check.beh_id} ({check.task_id}): «{check.title}»"]
    if check.steps:
        lines.append(f"шаги: {check.steps}")
    lines.append(OPTIONS)
    return "\n".join(lines)


def issue_marker(run_id: str, beh_id: str) -> str:
    """Первая строка issue: по ней `find_issue` узнаёт уже заведённый."""
    return f"<!-- dfl-manual-check run={run_id} beh={beh_id} -->"


def issue_title(check: ManualCheck) -> str:
    """Заголовок issue «не выполнено»."""
    return f"Ручная проверка не выполнена: {check.beh_id} — {check.title}"


def issue_body(check: ManualCheck, answer: Mapping, *, run_id: str, pr_url: str) -> str:
    """Тело issue «не выполнено»; первая строка — маркер."""
    note = str(answer.get("note") or "").strip() or "—"
    return "\n".join(
        [
            issue_marker(run_id, check.beh_id),
            f"Требование: **{check.beh_id}** — {check.title}",
            f"Задача: {check.task_id}",
            f"Шаги проверки: {check.steps or '—'}",
            f"Ответ владельца при приёмке ({answer.get('at', '')}): не выполнено",
            f"Комментарий: {note}",
            f"PR с кодом: {pr_url}",
            f"Прогон dfl: {run_id}",
            "",
            "Закрытие этого issue `dfl status` читает как «выполнено позже».",
        ]
    )


def comment_marker(run_id: str) -> str:
    """Первая строка комментария в PR: по ней повтор его узнаёт."""
    return f"<!-- dfl-manual-checks run={run_id} -->"


def pr_comment(
    checks: list[ManualCheck], answers: Mapping[str, Mapping], *, run_id: str
) -> str:
    """Один комментарий в PR с кодом со всеми ответами (снимок приёмки)."""
    rows = [
        comment_marker(run_id),
        "### Ручные проверки",
        "",
        "| BEH | задача | ответ | оговорка / комментарий | issue |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        a = answers.get(c.beh_id) or {}
        word = VERDICT_WORDS.get(str(a.get("verdict")), "—")
        note = str(a.get("note") or "").strip() or "—"
        issue = f"#{a['issue']}" if a.get("issue") else "—"
        rows.append(f"| {c.beh_id} | {c.task_id} | {word} | {note} | {issue} |")
    return "\n".join(rows)


def summary(
    checks: list[ManualCheck],
    answers: Mapping[str, Mapping],
    closed: frozenset[int] = frozenset(),
) -> str:
    """Хвост финальной строки; `""` — всё выполнено (строка прежняя)."""
    not_done: list[str] = []
    later: list[str] = []
    caveats = 0
    for c in checks:
        a = answers.get(c.beh_id) or {}
        verdict, issue = a.get("verdict"), a.get("issue")
        if verdict == CAVEAT:
            caveats += 1
        elif verdict == NOT_DONE and issue in closed:
            later.append(f"{c.beh_id}, issue #{issue} закрыт")
        elif verdict == NOT_DONE:
            ref = f" → issue #{issue}" if issue else ""
            not_done.append(f"{c.beh_id}{ref}")
    parts = []
    if not_done:
        parts.append(f"не выполнено: {len(not_done)} ({', '.join(not_done)})")
    if later:
        parts.append(f"выполнено позже: {len(later)} ({'; '.join(later)})")
    if caveats:
        parts.append(f"с оговоркой: {caveats}")
    return f"ручные проверки — {'; '.join(parts)}" if parts else ""


def pending(enabled: bool, record: Mapping | None) -> bool:
    """Шаг ручных проверок не пройден (у старого прогона его нет вовсе)."""
    return enabled and (record is None or record.get("status") != "completed")


def unanswered(record: Mapping) -> list[ManualCheck]:
    """Проверки записи без ответа — их и спрашивает повтор."""
    answers = record.get("answers") or {}
    return [c for c in checks_of(record) if c.beh_id not in answers]
