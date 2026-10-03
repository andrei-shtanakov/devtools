"""Протекание приоритета, ключ сортировки, объяснение, подсказки (спека §4)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from conductor.analysis import (
    attention_nodes,
    dependency_adjacency,
    find_cycles,
    is_ready,
    work_state,
)
from conductor.graph import Graph
from conductor.roadmap import Roadmap
from conductor.waits import Wait


@dataclass(frozen=True)
class QueueEntry:
    """Позиция очереди: via_focus — источник ранга, own_focus — свой фокус.

    line_age_days — по дате строки ожидания (приближение, §3.4 rev 11);
    None — возраст неизвестен.
    """

    node_id: str
    rank: int | None
    via_focus: str | None
    own_focus: str | None
    klass: str
    on_goal_path: bool
    unblocks: int
    line_age_days: int | None
    why: str


@dataclass(frozen=True)
class Hint:
    """Подсказка §4.4: PR упоминает узел пути к вехе — связь не подтверждена."""

    pr: str
    target: str
    goal: str


def _reverse(adj: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {n: set() for n in adj}
    for node, prereqs in adj.items():
        for p in prereqs:
            rev.setdefault(p, set()).add(node)
    return rev


def _walk(starts: list[str], rev: dict[str, set[str]]) -> dict[str, str | None]:
    """BFS по зависящим: узел → родитель на пути от одного из starts."""
    parent: dict[str, str | None] = dict.fromkeys(starts)
    queue = deque(starts)
    while queue:
        node = queue.popleft()
        for up in sorted(rev.get(node, ())):
            if up not in parent:
                parent[up] = node
                queue.append(up)
    return parent


def _chain(parent: dict[str, str | None], end: str) -> str:
    path = [end]
    while (step := parent[path[-1]]) is not None:
        path.append(step)
    return " → ".join(path)


def _days(now: str, since: str) -> int:
    delta = datetime.fromisoformat(now) - datetime.fromisoformat(since)
    return max(delta.days, 0)


def _starts(node_id: str, graph: Graph) -> list[str]:
    """Откуда течёт ранг: сам узел; у PR — ещё узлы, которые он реализует."""
    if graph.nodes[node_id].kind != "pr":
        return [node_id]
    return [
        node_id,
        *sorted({graph.resolve(e.dst) for e in graph.out(node_id, "implements")}),
    ]


def _entry(
    node_id: str,
    graph: Graph,
    waits: list[Wait],
    roadmap: Roadmap,
    rev: dict[str, set[str]],
    now: str,
) -> QueueEntry:
    starts = _starts(node_id, graph)
    parent = _walk(starts, rev)
    best: tuple[int, str] | None = None
    if roadmap.valid:
        for other in parent:
            focus = roadmap.focus_of(graph.epic_of(other))
            if focus is not None and (best is None or focus.rank < best[0]):
                best = (focus.rank, other)
    epic = graph.epic_of(node_id)
    own = roadmap.focus_of(epic) if roadmap.valid else None
    klass = roadmap.klass(epic) if roadmap.valid else "background"
    dated = [
        _days(now, w.last_line_change_at)
        for w in waits
        if w.prereq == node_id and w.last_line_change_at is not None
    ]
    age = max(dated) if dated else None
    # объединение потребителей каждой точки старта: цель PR, от которой зависит
    # другая его цель, тоже разблокируется
    unblocks = len(
        set().union(*(set(_walk([s], rev)) - {s} for s in starts)) - {node_id}
    )
    if best is None:
        return QueueEntry(
            node_id,
            None,
            None,
            None,
            klass,
            False,
            unblocks,
            age,
            "без ранга: не связан с фокусом",
        )
    rank, source = best
    focus = roadmap.focus[rank - 1]
    on_goal = focus.goal is not None and focus.goal in parent
    why = (
        f"rank {rank} ({focus.epic}) via {_chain(parent, source)}; unblocks {unblocks}"
    )
    return QueueEntry(
        node_id,
        rank,
        focus.epic,
        own.epic if own else None,
        klass,
        on_goal,
        unblocks,
        age,
        why,
    )


def rank_of(graph: Graph, roadmap: Roadmap, node_id: str) -> int | None:
    """Ранг узла работы с наследованием (§4.2); None — не связан с фокусом."""
    if not roadmap.valid:
        return None
    rev = _reverse(dependency_adjacency(graph))
    ranks = [
        f.rank
        for other in _walk([graph.resolve(node_id)], rev)
        if (f := roadmap.focus_of(graph.epic_of(other))) is not None
    ]
    return min(ranks, default=None)


def _key(e: QueueEntry) -> tuple[bool, int, bool, int, int, str]:
    return (
        e.rank is None,
        e.rank or 0,
        not e.on_goal_path,
        -e.unblocks,
        -(e.line_age_days or 0),
        e.node_id,
    )


def goal_paths(graph: Graph, roadmap: Roadmap) -> dict[str, set[str]]:
    """Веха → она сама и все её транзитивные предпосылки (узлы работы)."""
    if not roadmap.valid:
        return {}
    adj = dependency_adjacency(graph)
    paths: dict[str, set[str]] = {}
    for focus in roadmap.focus:
        if focus.goal is None or focus.goal not in graph.nodes:
            continue
        seen, stack = set(), [graph.resolve(focus.goal)]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(adj.get(node, ()))
        paths[focus.goal] = seen
    return paths


def _implemented(graph: Graph, pr: str) -> set[str]:
    """Узлы работы, которые PR реализует структурно: связь уже подтверждена
    (концы — так же, как цель подсказки: представитель или слабая склейка)."""
    ends = [e.dst for e in graph.out(pr, "implements")]
    return {graph.resolve(d) for d in ends} | {
        graph.hint_canon[d] for d in ends if d in graph.hint_canon
    }


def build_hints(graph: Graph, roadmap: Roadmap) -> list[Hint]:
    """Открытый PR того же репо упоминает узел пути — подсказка, не ребро."""
    hints = set()
    for goal, path in goal_paths(graph, roadmap).items():
        for e in graph.edges:
            pr = graph.nodes.get(e.src)
            target = graph.resolve(e.dst)
            if target not in path:
                target = graph.hint_canon.get(e.dst, target)
            if (
                e.type == "mentions"
                and pr is not None
                and pr.kind == "pr"
                and pr.is_open
                and target in path
                and (node := graph.nodes.get(target)) is not None
                and node.is_open
                and pr.repo == (graph.nodes.get(e.dst) or pr).repo
                and target not in _implemented(graph, e.src)
            ):
                hints.add(Hint(e.src, target, goal))
    return sorted(hints, key=lambda h: (h.goal, h.target, h.pr))


def build_queue(
    graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str
) -> list[QueueEntry]:
    """Кандидаты §4.1 (готовые, вне циклов, idle; открытые PR) в порядке §4.3."""
    adj = dependency_adjacency(graph)
    in_cycle = {n for c in find_cycles(adj) for n in c}
    rev = _reverse(adj)
    entries = []
    for node_id, node in graph.nodes.items():
        weak = (graph.records.get(node_id) or {}).get("weak")
        if node_id in graph.canon or node_id in in_cycle or not node.is_open or weak:
            continue
        if node.kind != "pr" and (
            not is_ready(node_id, graph, waits) or work_state(node_id, graph) != "idle"
        ):
            continue
        entries.append(_entry(node_id, graph, waits, roadmap, rev, now))
    return sorted(entries, key=_key)


def build_attention(
    graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str
) -> list[QueueEntry]:
    """Узлы, которым нужно решение (unknown-ожидание), в том же порядке."""
    rev = _reverse(dependency_adjacency(graph))
    return sorted(
        (
            _entry(n, graph, waits, roadmap, rev, now)
            for n in attention_nodes(graph, waits)
        ),
        key=_key,
    )
