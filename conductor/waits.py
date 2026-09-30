"""Предпосылки, условия @trigger и застой (спека §3.4, срез 0 без модели)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from conductor.graph import FROM_ORIGIN, UNRESOLVED, Graph
from conductor.inputs import Inputs
from conductor.model import Edge

PrereqState = Literal[
    "done", "cancelled", "open", "missing", "unread", "unresolvable", "request_open"
]
Verdict = Literal["satisfied", "pending", "unknown"]
DATE_RE = re.compile(r"^date>=(\d{4}-\d{2}-\d{2})$")
EXISTS_RE = re.compile(r"^exists:([a-z0-9][a-z0-9-]*):(\S+)$")
VERSION_RE = re.compile(r"^v(\d+)$")


@dataclass(frozen=True)
class Wait:
    """Ожидание consumer → prereq (prereq=None — условие @trigger).

    last_line_change_at — дата последней правки строки ожидания (git blame):
    приближение возраста, не момент начала ожидания (§3.4 rev 11).
    """

    consumer: str
    prereq: str | None
    verdict: Verdict
    reason: str
    evidence: str
    last_line_change_at: str | None
    moved: str | None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _days(now: str, then: str) -> int:
    return (_ts(now) - _ts(then)).days


def prereq_state(graph: Graph, inputs: Inputs, prereq_id: str) -> PrereqState:
    """Таблица §3.4 для представителя узла работы."""
    node = graph.nodes.get(prereq_id)
    if node is None:
        if prereq_id.startswith(UNRESOLVED):
            return "unresolvable"
        if graph.source_unread(prereq_id):
            return "unread"  # не прочитано — не «нет»: вопроса не будет
        if prereq_id in graph.pending_requests:
            return "request_open"
        return "cancelled" if inputs.history.get(prereq_id) else "missing"
    if node.is_open:
        return "open"
    return "done" if node.closed_as in ("completed", "merged") else "cancelled"


def last_movement(graph: Graph, inputs: Inputs, node_id: str) -> str | None:
    """Последнее движение по узлу работы: коммит с @id, треды, открытые PR."""
    stamps: list[str] = []
    for member in graph.members(node_id):
        stamps.append(inputs.movement.get(member) or "")
        rec = graph.records.get(member)
        if rec is not None:
            stamps.append(rec.get("updated_at") or "")
            stamps += [c.get("created_at") or "" for c in rec.get("comments", [])]
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is not None and pr.is_open:
                stamps.append(pr.updated_at or "")
    stamps = [s for s in stamps if s]
    return max(stamps, key=_ts) if stamps else None


def _evidence(graph: Graph, prereq_id: str) -> str:
    node = graph.nodes[prereq_id]
    if node.kind == "item":
        return f"{prereq_id}@{graph.todo_sha.get(node.repo)}"
    return node.url or prereq_id


def _dependency_wait(
    graph: Graph,
    inputs: Inputs,
    edge: Edge,
    raw: str,
    now: str,
    stale_days: int,
) -> Wait:
    # ожидание по from: — ожидание самого запроса, не склеенного пункта (§3.4)
    consumer = graph.resolve(edge.src)
    prereq = edge.dst if edge.origin == FROM_ORIGIN else graph.resolve(edge.dst)
    state = prereq_state(graph, inputs, prereq)
    since = inputs.wait_since.get(f"{edge.src}|{raw}")
    moved = last_movement(graph, inputs, prereq)
    if state == "done":
        return Wait(
            consumer,
            prereq,
            "satisfied",
            "done",
            _evidence(graph, prereq),
            since,
            moved,
        )
    if state == "open":
        stale = (
            moved is not None
            and _days(now, moved) >= stale_days
            and since is not None
            and _days(now, since) >= stale_days
        )
        return Wait(
            consumer,
            prereq,
            "pending",
            "stale" if stale else "open",
            prereq,
            since,
            moved,
        )
    if state == "request_open":  # заявка отправлена, пункт ещё не заведён
        evidence = graph.pending_requests[prereq]
        return Wait(consumer, prereq, "pending", state, evidence, since, moved)
    return Wait(consumer, prereq, "unknown", state, prereq, since, moved)


def _version_mismatch(path: str, siblings: list[str]) -> bool:
    for part in path.split("/"):
        if m := VERSION_RE.match(part):
            wanted = int(m.group(1))
            return any(
                (v := VERSION_RE.match(s)) is not None and int(v.group(1)) > wanted
                for s in siblings
            )
    return False


def _trigger_wait(consumer: str, text: str, inputs: Inputs, now: str) -> Wait:
    if m := DATE_RE.match(text):
        ok = now[:10] >= m.group(1)
        return Wait(
            consumer, None, "satisfied" if ok else "pending", "date", text, None, None
        )
    if m := EXISTS_RE.match(text):
        fact = inputs.trigger_facts.get(text)
        if fact is None or fact.get("exists") is None:
            return Wait(consumer, None, "unknown", "fact_unread", text, None, None)
        if fact["exists"]:
            return Wait(
                consumer,
                None,
                "satisfied",
                "exists",
                f"{text}@{fact.get('sha')}",
                None,
                None,
            )
        if _version_mismatch(m.group(2), fact.get("siblings", [])):
            return Wait(consumer, None, "unknown", "version_mismatch", text, None, None)
        return Wait(consumer, None, "pending", "absent_path", text, None, None)
    return Wait(consumer, None, "unknown", "prose_trigger", text, None, None)


def evaluate_waits(
    graph: Graph, inputs: Inputs, now: str, stale_after_days: int
) -> list[Wait]:
    """Все ожидания открытых узлов работы; вычисляются заново каждым прогоном."""
    waits: dict[tuple[str, str | None], Wait] = {}
    for edge in graph.edges:
        consumer = graph.nodes.get(graph.resolve(edge.src))
        if edge.type != "depends_on" or consumer is None or not consumer.is_open:
            continue
        raw = (
            edge.origin.removeprefix("todo:")
            if edge.origin.startswith("todo:")
            else edge.dst
        )
        wait = _dependency_wait(graph, inputs, edge, raw, now, stale_after_days)
        waits.setdefault((wait.consumer, wait.prereq), wait)
    for node in graph.nodes.values():
        if node.kind == "item" and node.is_open and node.trigger:
            waits[(node.node_id, None)] = _trigger_wait(
                node.node_id, node.trigger, inputs, now
            )
    return sorted(waits.values(), key=lambda w: (w.consumer, w.prereq or ""))


def waits_of(waits: list[Wait], consumer: str) -> list[Wait]:
    """Ожидания одного потребителя."""
    return [w for w in waits if w.consumer == consumer]
