"""Текст для status / why / plan."""

from __future__ import annotations

from collections import Counter

from conductor.rank import QueueEntry
from conductor.snapshot import Result, owner_questions

FLAGS = {
    "owner-tbd": "кандидат на делегирование: нет @owner, полномочия не проверены",
    "decision-signal": "возможно, требуется решение",
    "foreign-owner": "чужой владелец",
    "epic-unknown": "эпик неизвестен",
    "epic-not-active": "эпик не active",
    "out-of-loop": "репо вне контура",
}


def _header(result: Result) -> list[str]:
    bad = [s for s in result.graph.sources if s.state in ("error", "not_queried")]
    lines = [f"граф: {result.graph_state}; уровень прогона: {result.run_level}"]
    lines += [f"  источник {s.name}: {s.state} {s.detail}" for s in bad]
    if not result.roadmap.valid:
        lines.append("  роадмап: RM-INVALID — очередь без рангов")
    return lines


def _position(result: Result, e: QueueEntry) -> list[str]:
    a = result.assessments[e.node_id]
    flag = FLAGS.get(a.block_reason or "")
    age = "" if e.line_age_days is None else f"; строка ожидания ≈{e.line_age_days} дн."
    head = f"  {e.rank or '-'}  {e.node_id}  [{a.need} → {a.actor}]"
    return [head + (f"  ({flag})" if flag else ""), f"       {e.why}{age}"]


def _repo(node_id: str) -> str:
    return node_id.removeprefix("todo://").split("/")[0].split("#")[0].split("!")[0]


def _backlog(result: Result, entries: list[QueueEntry]) -> list[str]:
    """Сводка блока 3: по фокусу и репо; условия — отдельным счётом."""
    groups: dict[str, Counter[str]] = {}
    for e in entries:
        label = f"фокус {e.rank} ({e.via_focus})" if e.rank else "без ранга"
        need = result.assessments[e.node_id].need
        repo = _repo(e.node_id) + (" [условие]" if need == "wait_condition" else "")
        groups.setdefault(label, Counter())[repo] += 1
    return [
        f"  {label}: " + ", ".join(f"{r} {n}" for r, n in sorted(c.items()))
        for label, c in sorted(groups.items(), key=lambda kv: kv[0] == "без ранга")
    ]


def render_status(result: Result, top: int = 15, repo: str | None = None) -> str:
    """Три блока §4.4, циклы, ожидания, вопросы; repo — раскрыть одно репо."""
    lines = _header(result)
    lines.append("фокусы: " + ", ".join(f.epic for f in result.roadmap.focus))
    entries = result.queue + result.attention
    if repo is not None:
        lines.append(f"все позиции {repo}:")
        for e in (e for e in entries if _repo(e.node_id) == repo):
            lines += _position(result, e)
            lines.append(f"       блок: {result.blocks[e.node_id]}")
        return "\n".join(lines)
    goals = [f.goal for f in result.roadmap.focus if f.goal]
    lines.append("продвижение вехи (" + ", ".join(goals) + "):")
    shown: set[str] = set()
    for e in (e for e in entries if result.blocks[e.node_id] == "goal"):
        lines += _position(result, e)
        prs = [h.pr for h in result.hints if h.target == e.node_id]
        if prs and result.assessments[e.node_id].need == "implement":
            shown.add(e.node_id)
            lines.append(
                "       реализация — следующий вид работы; готовность к запуску не "
                "подтверждена; возможный предварительный шаг — рассмотрение "
                + ", ".join(prs)
            )
    lines += [
        f"  возможный следующий шаг: {h.pr} упоминает {h.target} — связь не "
        "подтверждена (оформить: Closes / @id: в теле PR / @blocked_by пункта)"
        for h in result.hints
        if h.target not in shown
    ]
    lines.append("другие действия фокуса:")
    for e in [e for e in entries if result.blocks[e.node_id] == "signal"][:top]:
        lines += _position(result, e)
    lines.append("остальной бэклог (status <repo> — раскрыть):")
    lines += _backlog(
        result, [e for e in entries if result.blocks[e.node_id] == "backlog"]
    )
    # дешёвое действие — не приоритет и не разрешение на мерж: ранг не растёт
    ready = [
        e.node_id
        for e in result.queue
        if e.rank is None and result.assessments[e.node_id].need == "merge"
    ]
    if ready:
        lines.append(
            "вне фокуса: PR, готовые к рассмотрению мержа: " + ", ".join(ready)
        )
    lines += ["цикл: " + " → ".join(c) for c in result.cycles]
    stale = sum(w.reason == "stale" for w in result.waits)
    done = sum(w.verdict == "satisfied" for w in result.waits)
    lines.append(f"ожидания: выполнено {done}, застой {stale}")
    questions = owner_questions(result)
    lines.append(f"вопросы владельцу: {len(questions)}")
    lines += [
        f"  Q-{q['question_id']} {q['subject']}: {q['reason']} — "
        f"{' / '.join(q['options'])}"
        for q in questions[:top]
    ]
    return "\n".join(lines)


def render_why(result: Result, node_id: str) -> str:
    """Цепочка ожиданий от узла до листьев с actor/need."""
    root = result.graph.resolve(node_id)
    lines, frontier, seen = [root], [(root, 0)], {root}
    while frontier:
        node, depth = frontier.pop()
        for w in sorted(result.waits, key=lambda w: w.prereq or ""):
            if w.consumer != node:
                continue
            label = w.prereq or f"@trigger {w.evidence}"
            lines.append("  " * (depth + 1) + f"ждёт {label} [{w.verdict}/{w.reason}]")
            if w.prereq and w.prereq not in seen:
                seen.add(w.prereq)
                frontier.append((w.prereq, depth + 1))
    for other in sorted(seen):
        if (a := result.assessments.get(other)) is not None:
            lines.append(f"{other}: need={a.need} actor={a.actor} action={a.action}")
    entry = next(
        (e for e in result.queue + result.attention if e.node_id == root), None
    )
    lines.append(entry.why if entry else "не кандидат (заблокирован или закрыт)")
    return "\n".join(lines)


def render_plan(result: Result) -> str:
    """Действия, которые были бы выполнены (ничего не исполняется)."""
    lines = _header(result)
    lines.append(f"план на уровне {result.run_level} (ничего не исполняется):")
    for a in result.assessments.values():
        if a.action != "—":
            lines.append(f"  {a.action:<16} {a.node_id}  ({a.need})")
    return "\n".join(lines)
