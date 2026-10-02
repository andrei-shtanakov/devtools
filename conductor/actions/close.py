"""close_shipped (спека среза 1, §7.5).

Основание действует только в своём периоде открытия. Маркер — с полным
ключом (node, period, evidence); маркер другого доказательства не считается.
Перед КАЖДЫМ шагом (шаг 8 §5.1) — свежая сверка: issue открыт, период тот
же, основание в силе — PR (а) влит с тем же merge SHA и всё ещё закрывает
issue, либо (б) ответ `close` по свежим комментариям очереди действителен И
основание самого вопроса пересчитано по свежим данным (склейка с пунктом и
его выполненность, влитый PR, переоткрытие) с той же идентичностью вопроса:
ответ разрешает закрытие, но не заменяет доказательство. Перед закрытием —
действительный маркер подтверждения с полным текущим ключом в треде.
Никогда: переоткрывать, закрывать not_planned, закрывать PR.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.common import (
    authority,
    edited_findings,
    fresh_accepted,
    fresh_item,
    target,
    thread_events,
)
from conductor.actions.shipped import shipped_state
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    marker_check,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.rank import rank_of
from conductor.snapshot import Result, question_id
from conductor.writer import PlanRecord, Step

SHIPPED = "GR-SHIPPED-OPEN"
DONE, MERGED, REOPENED = " выполнен", " влит", "reopened"


@dataclass(frozen=True)
class QueueRef:
    """Где читать ответы владельца: тред очереди и логин владельца."""

    repo: str
    number: int
    owner: str


def _same(
    fresh: FreshReader,
    subject: tuple[str, int],
    pr: tuple[str, int],
    merge_sha: str | None,
) -> str | None:
    """PR влит (тем же merge SHA, если он задан) и всё ещё закрывает issue."""
    facts = fresh.pr_facts(*pr)
    if facts is None:
        return "PR основания не прочитан"
    if not facts["merged"] or (merge_sha and facts["merge_sha"] != merge_sha):
        return "PR основания не влит"
    issue = f"{subject[0]}#{subject[1]}"
    return None if issue in facts["closing"] else "PR больше не закрывает issue"


def question_basis(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    subject: str,
    question: dict[str, Any],
    period: str,
) -> str | None:
    """Основание вопроса GR-SHIPPED-OPEN по свежим данным; его идентичность
    (question-id из свежего доказательства) должна совпасть с планом."""
    head = question["evidence"].rsplit("|period:", 1)[0]
    repo, number = target(inputs, subject)
    if head.endswith(DONE):  # склейка заявки с выполненным пунктом
        item = head.removesuffix(DONE)
        # пункт — каноническим разбором свежего origin ДО склейки: склейка
        # ядра опирается на существование пункта, а снимок прогона его помнит
        state, _, problem = fresh_item(fresh, item)
        if problem is not None:
            return problem
        if state is None:
            return "пункта больше нет"
        if not state.done:
            return "пункт больше не выполнен"
        accepted, problem = fresh_accepted(fresh, graph, inputs, subject)
        if problem is not None:
            return problem
        if accepted != item:
            return "склейка заявки с пунктом снята"
    elif head.endswith(MERGED):
        problem = _same(
            fresh, (repo, number), target(inputs, head.removesuffix(MERGED)), None
        )
        if problem is not None:
            return problem
    elif head != REOPENED:  # прежний период: влитый PR неизменен, период — ниже
        return "основание вопроса не распознано"
    evidence = f"{head}|period:{period}"
    fresh_id = question_id(SHIPPED, subject, evidence, tuple(question["options"]))
    return (
        None if fresh_id == question["question_id"] else "основание вопроса изменилось"
    )


def close_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    subject: str,
    period: str,
    basis: dict[str, Any],
    question: dict[str, Any] | None,
    queue: QueueRef | None,
    rendered: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8 для обоих шагов: открыт, период, основание; комментарий — маркера
    ещё нет; закрытие — подтверждение с полным ключом есть."""

    def same_period(m: Mutation) -> str | None:
        event = fresh.last_event(m.repo, m.number or 0, "reopened")
        if event is None:
            return "timeline не прочитан"
        return None if (event or "0") == period else "период изменился"

    def answer_holds() -> str | None:
        if queue is None or question is None:
            return "очередь не найдена"
        comments = fresh.comments(queue.repo, queue.number)
        if comments is None:
            return "ответы не прочитаны"
        results, _ = answers_and_resolutions(comments, queue.owner, [question])
        res = results[question["question_id"]]
        return None if res.chosen == "close" and not res.blocked else "ответ изменился"

    def basis_holds(m: Mutation) -> str | None:
        if question is None:
            problem = _same(
                fresh,
                (m.repo, m.number or 0),
                (basis["repo_full"], basis["number"]),
                basis["merge_sha"],
            )
            return None if problem is None else f"основание (а): {problem}"
        return first_reason(
            answer_holds,
            lambda: question_basis(fresh, graph, inputs, subject, question, period),
        )

    def check(m: Mutation) -> str | None:
        steps: list[Callable[[], str | None]] = [
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: same_period(m),
            lambda: basis_holds(m),
        ]
        if m.op == "comment":
            steps.append(lambda: comment_check(fresh, m, bot))
        else:
            steps.append(lambda: marker_check(fresh, m, rendered, bot))
        return first_reason(*steps)

    return check


def plan_close(
    result: Result,
    inputs: Inputs,
    bot: str,
    close_answers: dict[str, dict[str, Any]],
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
    queue: QueueRef | None = None,
) -> list[PlanRecord]:
    """Записи закрытия по основанию (а) текущего периода или ответу close."""
    graph = result.graph
    records: list[PlanRecord] = []
    for subject in sorted(set(inputs.issue_extras) | set(close_answers)):
        node = graph.nodes.get(subject)
        if node is None or node.kind != "issue" or not node.is_open:
            continue
        if (
            subject not in close_answers
            and rank_of(graph, result.roadmap, subject) is None
        ):
            continue
        state = shipped_state(inputs, subject)
        if state is None:
            notes.append(
                {
                    "action": "close_shipped",
                    "subject": subject,
                    "reason": "период не прочитан",
                }
            )
            continue
        question: dict[str, Any] | None = None
        basis: dict[str, Any] = {}
        if state.basis_a is not None:
            pr = state.basis_a
            fact = f"merged:{pr['merge_sha']}"
            how = (
                f"влит PR {pr['repo']}!{pr['number']} ({(pr['merge_sha'] or '')[:12]})"
            )
            basis = {
                "repo_full": f"{inputs.owner}/{pr['repo']}",
                "number": pr["number"],
                "merge_sha": pr["merge_sha"],
            }
        elif subject in close_answers:
            question = close_answers[subject]
            fact = f"answer:{question['question_id']}"
            how = (
                f"ответ владельца Q-{question['question_id']}: close "
                f"(основание: {question['evidence']})"
            )
        else:
            if state.old_basis:
                notes.append({"finding": "GR-REOPENED", "subject": subject})
            continue
        marker = make(
            "close",
            node=h1("node", subject),
            period=h1("period", state.period),
            evidence=h1("evidence", fact),
        )
        events, _ = thread_events(graph, subject, bot)
        notes += edited_findings(graph, [subject], bot, "close_shipped")
        repo, number = target(inputs, subject)
        steps: list[Step] = []
        if not any(m == marker for m, _ in events):
            text = f"Выполнение подтверждено: {how}. Закрываю как completed."
            steps.append(
                Step(
                    Mutation("comment", repo, number, text=with_marker(text, marker)),
                    render(marker),
                )
            )
        steps.append(Step(Mutation("close", repo, number), "close"))
        records.append(
            PlanRecord(
                "close_shipped",
                subject,
                h1("rev", state.period, fact),
                1,
                tuple(steps),
                close_check(
                    fresh,
                    graph,
                    inputs,
                    subject,
                    state.period,
                    basis,
                    question,
                    queue,
                    render(marker),
                    bot,
                )
                if fresh
                else valid,
                # ответ владельца — основание и без ранга (§7.5 (б)); иначе ранг
                authority(result, inputs, subject, ranked=question is None),
            )
        )
    return records
