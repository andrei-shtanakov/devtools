"""Планировщики действий среза 1 (спека среза 1, §6–7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.close import QueueRef, plan_close
from conductor.actions.notify import plan_notify
from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.actions.owner_queue import (
    SHIPPED,
    plan_owner_queue,
    question_levels,
    queue_issue,
    queue_questions,
)
from conductor.fresh import FreshReader, GitRepo
from conductor.host_config import HostConfig
from conductor.inputs import Inputs
from conductor.opstate import OpState
from conductor.snapshot import Result
from conductor.writer import PlanRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class PlanContext:
    """Что планировщикам нужно сверх результата ядра; notes — в снимок."""

    cfg: HostConfig
    umbrella: str
    bot_login: str
    state: OpState
    client: Any = None
    git_repo: GitRepo | None = None
    owner_login: str = ""
    now: datetime = field(default_factory=_utcnow)
    notes: list[dict[str, Any]] = field(default_factory=list)


def _close_answers(
    questions: list[dict[str, Any]],
    resolutions: dict[str, Any],
    notes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Исполняется только close на GR-SHIPPED-OPEN; прочие ответы — заметка."""
    close: dict[str, dict[str, Any]] = {}
    for q in questions:
        res = resolutions[q["question_id"]]
        if res.blocked:
            notes.append(
                {
                    "question": q["question_id"],
                    "status": "конфликт на подтверждении: оставьте один ответ",
                }
            )
        elif res.chosen is None or res.chosen == "keep":
            continue
        elif q["reason"] == SHIPPED and res.chosen == "close":
            close[q["subject"]] = q
        else:
            notes.append(
                {
                    "question": q["question_id"],
                    "answer": res.chosen,
                    "status": "ответ принят; исполнение недоступно в текущем срезе",
                }
            )
    return close


def plan_records(result: Result, inputs: Inputs, ctx: PlanContext) -> list[PlanRecord]:
    """Записи плана прогона; граф partial — ни одной (О §2.4)."""
    if result.graph_state == "partial":
        ctx.notes.append({"note": "граф partial — записей нет"})
        return []
    fresh = FreshReader(ctx.client, ctx.git_repo) if ctx.client is not None else None
    bot = ctx.bot_login
    owner = ctx.owner_login or ctx.umbrella.split("/")[0]
    questions = queue_questions(result, inputs, ctx.state.episodes())
    queue, _ = queue_issue(inputs, bot)
    resolutions, open_derived = answers_and_resolutions(
        queue["comments"] if queue else [], owner, questions
    )
    close_answers = _close_answers(questions, resolutions, ctx.notes)
    queue_ref = QueueRef(ctx.umbrella, queue["number"], owner) if queue else None
    levels = question_levels(result, inputs, questions + open_derived)
    return [
        *plan_owner_queue(
            questions,
            open_derived,
            inputs,
            ctx.umbrella,
            owner,
            bot,
            fresh,
            ctx.notes,
            levels,
        ),
        *plan_close(result, inputs, bot, close_answers, fresh, ctx.notes, queue_ref),
        *plan_notify(result, inputs, bot, fresh, ctx.notes),
        *plan_nudges(result, inputs, bot, ctx.now, fresh, ctx.notes),
        *plan_pr_nudges(result, inputs, bot, ctx.now, fresh, ctx.notes),
    ]
