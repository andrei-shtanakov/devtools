"""notify_satisfied (спека среза 1, §7.2).

Идентичность — (ожидание, доказательство, адрес); маркер ищется в треде
этого адреса. TODO-only потребитель тредов не имеет — канал среза 1b; треды
есть, но все закрыты — «нет открытого адреса» (решение владельца 5).
Перед отправкой (шаг 8 §5.1) — свежая сверка: адрес открыт и маркера нет,
потребитель держит ребро, факт выполнения тот же.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from conductor.actions.common import (
    WaitRef,
    addresses,
    authority,
    edited_findings,
    fact_of,
    fresh_fact,
    fresh_item,
    no_address_reason,
    target,
    thread_events,
    threads,
    wait_id,
    wait_refs,
)
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.snapshot import Result
from conductor.writer import PlanRecord, Step


def holds_edge(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> str | None:
    """Потребитель открыт и всё ещё держит тег ожидания (свежее чтение)."""
    node = graph.nodes.get(ref.src)
    if node is not None and node.kind != "item":
        repo, number = target(inputs, ref.src)
        return open_check(fresh, repo, number)
    state, _, problem = fresh_item(fresh, ref.src)
    if problem is not None:
        return problem
    if state is None or state.done:
        return "потребитель закрыт"
    if ref.prereq is None:
        held = state.trigger == ref.raw.removeprefix("trigger:")
    else:
        held = ref.raw in state.blocked_by
    return None if held else "тег ожидания снят"


def notify_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    ref: WaitRef,
    fact: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8: адрес открыт, маркера нет, ребро на месте, факт тот же."""

    def same_fact() -> str | None:
        now, problem = fresh_fact(fresh, graph, inputs, ref)
        if problem is not None:
            return problem
        return None if now == fact else "доказательство изменилось"

    def check(m: Mutation) -> str | None:
        return first_reason(
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: comment_check(fresh, m, bot),
            lambda: holds_edge(fresh, graph, inputs, ref),
            same_fact,
        )

    return check


def plan_notify(
    result: Result,
    inputs: Inputs,
    bot: str,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Уведомления о выполненных предпосылках по каждому открытому адресу."""
    graph = result.graph
    records: list[PlanRecord] = []
    for ref, wait in wait_refs(graph, result.waits):
        consumer = graph.nodes.get(ref.consumer)
        if wait.verdict != "satisfied" or consumer is None or not consumer.is_open:
            continue
        where = f"{ref.src}|{ref.raw}"
        addrs = addresses(graph, ref.consumer)
        if not addrs:
            reason = no_address_reason(graph, ref.consumer)
            notes.append(
                {"action": "notify_satisfied", "wait": where, "reason": reason}
            )
            continue
        fact = fact_of(graph, inputs, ref)
        if fact is None:
            notes.append(
                {
                    "action": "notify_satisfied",
                    "wait": where,
                    "reason": "факт выполнения неизвестен",
                }
            )
            continue
        wid = wait_id(ref)
        marker = make("sat", wait=wid, evidence=h1("evidence", fact))
        text = (
            f"Предпосылка выполнена: {ref.prereq or ref.raw} ({fact}). "
            f"Ждал: {ref.consumer}."
        )
        notes += edited_findings(
            graph, threads(graph, ref.consumer), bot, "notify_satisfied"
        )
        level = authority(result, inputs, ref.consumer)
        for addr in addrs:
            events, _ = thread_events(graph, addr, bot)
            if any(m == marker for m, _ in events):
                continue
            repo, number = target(inputs, addr)
            m = Mutation("comment", repo, number, text=with_marker(text, marker))
            records.append(
                PlanRecord(
                    "notify_satisfied",
                    addr,
                    h1("rev", wid, fact),
                    1,
                    (Step(m, render(marker)),),
                    notify_check(fresh, graph, inputs, ref, fact, bot)
                    if fresh
                    else valid,
                    level,
                )
            )
    return records
