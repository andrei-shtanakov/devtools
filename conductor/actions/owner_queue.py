"""Очередь владельца: вопросы и записи (спека среза 1, §5.4, §6.1).

Тело — проекция и ничего не доказывает; события — в треде. Закрытую
владельцем очередь не переоткрываем; больше одной — записей нет. Все шаги
очереди — ОДНА запись плана: сбой или неопределённость тела (создания)
блокирует вопросы этого плана; сбой закрепления — нет (§6.1 шаг 2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.common import authority, thread_events
from conductor.actions.shipped import reopened_with_old_basis, shipped_state
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import Episode
from conductor.roadmap import Roadmap
from conductor.snapshot import Result, owner_questions, question_id
from conductor.writer import PlanRecord, Step

QUEUE_TITLE = "Очередь владельца (conductor)"
SHIPPED = "GR-SHIPPED-OPEN"


def make_question(
    subject: str, reason: str, evidence: str, text: str, options: tuple[str, ...]
) -> dict[str, Any]:
    """Вопрос в форме owner_questions среза 0."""
    return {
        "question_id": question_id(reason, subject, evidence, options),
        "subject": subject,
        "reason": reason,
        "evidence": evidence,
        "question": f"{subject}: {text}",
        "options": list(options),
        "default": "keep",
    }


def op_failure_questions(episodes: list[Episode]) -> list[dict[str, Any]]:
    """Один вопрос на эпизод сбоя; id — ключ серии и первый прогон (§5.4)."""
    out = []
    for ep in episodes:
        raw = "\x1f".join(("op-failure", ep.mutation_id, ep.first_run, "retry", "keep"))
        target = ep.detail.get("target", "?")
        out.append(
            {
                "question_id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8],
                "subject": target,
                "reason": "op-failure",
                "evidence": ep.mutation_id,
                "question": (
                    f"{target}: операционный сбой {ep.detail.get('op', '?')} "
                    f"в прогонах {', '.join(ep.runs)} ({ep.error})"
                ),
                "options": ["retry", "keep"],
                "default": "keep",
            }
        )
    return out


def queue_questions(
    result: Result, inputs: Inputs, episodes: list[Episode]
) -> list[dict[str, Any]]:
    """Вопросы очереди: среза 0 (GR-SHIPPED-OPEN — с периодом, без (а)),
    переоткрытые с прежним основанием, эпизоды сбоев."""
    out: list[dict[str, Any]] = []
    for q in owner_questions(result):
        if q["reason"] != SHIPPED:
            out.append(q)
            continue
        state = shipped_state(inputs, q["subject"])
        if state is not None and state.basis_a is not None:
            continue  # (а): исполняется без вопроса
        period = state.period if state is not None else "0"
        text = q["question"].split(": ", 1)[-1]
        out.append(
            make_question(
                q["subject"],
                SHIPPED,
                f"{q['evidence']}|period:{period}",
                text,
                tuple(q["options"]),
            )
        )
    asked = {q["subject"] for q in out if q["reason"] == SHIPPED}
    for state in reopened_with_old_basis(result, inputs):
        if state.subject not in asked:
            out.append(
                make_question(
                    state.subject,
                    SHIPPED,
                    f"reopened|period:{state.period}",
                    "переоткрыт после закрытия влитым PR: закрыть снова?",
                    ("close", "keep"),
                )
            )
    out += op_failure_questions(episodes)
    return sorted(out, key=lambda q: (q["subject"], q["reason"], q["question_id"]))


def projection(questions: list[dict[str, Any]]) -> str:
    """Текст тела очереди (без маркера)."""
    lines = [f"# {QUEUE_TITLE}", "", "Отвечайте комментарием `Q-<id>: <вариант>`.", ""]
    if not questions:
        lines.append("Вопросов нет.")
    for q in questions:
        opts = ", ".join(q["options"])
        lines.append(
            f"- Q-{q['question_id']} — {q['question']} — варианты: {opts} "
            f"(по умолчанию {q['default']})"
        )
    return "\n".join(lines)


def queue_issue(inputs: Inputs, bot: str) -> tuple[dict[str, Any] | None, str | None]:
    """Запись очереди бота или находка (GR-QUEUE-CLOSED / GR-QUEUE-AMBIGUOUS)."""
    mine = [r for r in inputs.queue_records if r.get("author") == bot]
    if len(mine) > 1:
        return None, "GR-QUEUE-AMBIGUOUS"
    if not mine:
        return None, None
    if mine[0]["state"] != "open":
        return None, "GR-QUEUE-CLOSED"
    return mine[0], None


def _question_text(q: dict[str, Any], owner_login: str) -> str:
    opts = ", ".join(q["options"])
    return (
        f"@{owner_login} Q-{q['question_id']}: {q['question']}\n"
        f"Варианты: {opts} (по умолчанию {q['default']})."
    )


def queue_check(
    fresh: FreshReader,
    base: list[dict[str, Any]],
    derived_ids: set[str],
    owner_login: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8 очереди: создание — очереди всё ещё нет; прочее — очередь
    открыта, а открытые производные вопросы по свежим комментариям те же
    (ответ владельца изменился → проекция и вопросы устарели); комментарий —
    ещё и маркера вопроса нет."""

    def no_queue(m: Mutation) -> str | None:
        found = fresh.queue_found(m.repo, bot)
        if found is None:
            return "очередь не прочитана"
        return "очередь уже есть" if found else None

    def same_answers(m: Mutation) -> str | None:
        comments = fresh.comments(m.repo, m.number or 0)
        if comments is None:
            return "ответы не прочитаны"
        _, derived = answers_and_resolutions(comments, owner_login, base)
        ids = {d["question_id"] for d in derived}
        return None if ids == derived_ids else "ответы владельца изменились"

    def check(m: Mutation) -> str | None:
        if m.op == "create":
            return no_queue(m)
        steps: list[Callable[[], str | None]] = [
            lambda: open_check(fresh, m.repo, m.number or 0)
        ]
        if m.op in ("body", "comment"):
            steps.append(lambda: same_answers(m))
        if m.op == "comment":
            steps.append(lambda: comment_check(fresh, m, bot))
        return first_reason(*steps)

    return check


def plan_owner_queue(
    questions: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    inputs: Inputs,
    umbrella: str,
    owner_login: str,
    bot: str,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
    levels: dict[str, Callable[[Roadmap, int], int]] | None = None,
) -> list[PlanRecord]:
    """Запись очереди по таблице §6.1: тело (создание) → закрепление →
    новые вопросы, одной записью с зависимостями шагов.

    questions — вопросы ядра и эпизодов; derived — открытые производные.
    levels — уровень позиции субъекта вопроса (О §2.4) для его комментария.
    """
    queue, finding = queue_issue(inputs, bot)
    if finding is not None:
        notes.append({"finding": finding})
        return []
    every = questions + derived
    if queue is None and not every:
        return []
    text = projection(every)
    body = with_marker(text, make("queue", projection=h1("projection", text)))
    number = queue["number"] if queue is not None else None
    steps: list[Step] = []
    if queue is None:
        create = Mutation(
            "create",
            umbrella,
            None,
            text=body,
            title=QUEUE_TITLE,
            labels=("owner-queue",),
        )
        steps.append(Step(create, "owner-queue", revision=h1("rev", text)))
    elif (queue.get("body") or "").strip() != body.strip():
        steps.append(
            Step(
                Mutation("body", umbrella, number, text=body),
                "body",
                revision=h1("rev", text),
            )
        )
    if queue is None or not queue.get("pinned"):
        steps.append(
            Step(Mutation("pin", umbrella, number), "pin", "pin", optional=True)
        )
    asked: set[Any] = set()
    if queue is not None:
        events, edited = thread_events_of(queue, bot)
        asked = {m for m, _ in events}
        if edited:
            notes.append(
                {"finding": "MK-EDITED", "action": "owner_queue", "thread": umbrella}
            )
    for q in every:
        marker = make("q", id=h1("q", q["question_id"]))
        if marker in asked:
            continue
        m = Mutation(
            "comment",
            umbrella,
            number,
            text=with_marker(_question_text(q, owner_login), marker),
        )
        steps.append(
            Step(
                m,
                render(marker),
                revision=q["question_id"],
                authority=(levels or {}).get(q["subject"]),
            )
        )
    if not steps:
        return []
    check = (
        queue_check(
            fresh,
            questions,
            {d["question_id"] for d in derived},
            owner_login,
            bot,
        )
        if fresh
        else valid
    )
    return [
        PlanRecord("owner_queue", umbrella, h1("rev", text), 1, tuple(steps), check)
    ]


def question_levels(
    result: Result, inputs: Inputs, questions: list[dict[str, Any]]
) -> dict[str, Callable[[Roadmap, int], int]]:
    """Уровень позиции субъекта каждого вопроса, если субъект — узел графа."""
    return {
        q["subject"]: authority(result, inputs, q["subject"])
        for q in questions
        if q["subject"] in result.graph.nodes
    }


def thread_events_of(
    rec: dict[str, Any], bot: str
) -> tuple[list[tuple[Any, dict[str, Any]]], int]:
    """thread_events для записи вне графа (очередь читается отдельно)."""
    from conductor.graph import Graph

    graph = Graph(nodes={}, edges=[], records={"queue": rec})
    return thread_events(graph, "queue", bot)
