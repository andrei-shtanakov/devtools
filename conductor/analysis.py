"""Готовность, циклы, находки, состояние работы, внимание (спека §3.3, §3.5).

В срезе 0 поколений доставки нет (ветки conductor не создаются), поэтому
состояние работы — только in_review / idle.
"""

from __future__ import annotations

from typing import Literal

from conductor.graph import Graph
from conductor.model import Finding
from conductor.roadmap import Roadmap
from conductor.waits import Wait, waits_of


def dependency_adjacency(graph: Graph) -> dict[str, set[str]]:
    """depends_on по представителям узлов работы: узел → предпосылки."""
    adj: dict[str, set[str]] = {}
    for e in graph.edges:
        consumer = graph.nodes.get(graph.resolve(e.src))
        # закрытый потребитель не ждёт: ни цикла, ни ранга через него
        if e.type == "depends_on" and (consumer is None or consumer.is_open):
            src, dst = graph.resolve(e.src), graph.target(e)
            adj.setdefault(src, set()).add(dst)
            adj.setdefault(dst, set())
    return adj


def find_cycles(adj: dict[str, set[str]]) -> list[list[str]]:
    """SCC размера > 1 и петли; итеративный Tarjan (без рекурсии)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)

    for root in sorted(adj):
        if root in index:
            continue
        visit(root)
        work = [(root, iter(sorted(adj.get(root, ()))))]
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    visit(child)
                    work.append((child, iter(sorted(adj.get(child, ())))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1 or node in adj.get(node, ()):
                    result.append(sorted(component))
    return sorted(result)


def is_ready(node_id: str, graph: Graph, waits: list[Wait]) -> bool:
    """Открыт и все ожидания узла работы satisfied (§3.3.1)."""
    rep = graph.resolve(node_id)
    node = graph.nodes.get(rep)
    if node is None or not node.is_open:
        return False
    return all(w.verdict == "satisfied" for w in waits_of(waits, rep))


def work_state(node_id: str, graph: Graph) -> Literal["in_review", "idle"]:
    """in_review — открытый PR реализует любой член узла работы (§3.5)."""
    for member in graph.members(node_id):
        for e in graph.into(member, "implements"):
            pr = graph.nodes.get(e.src)
            if pr is not None and pr.is_open:
                return "in_review"
    return "idle"


def attention_nodes(graph: Graph, waits: list[Wait]) -> list[str]:
    """Открытые узлы работы, чьё ожидание unknown — нужны решения (§5.2)."""
    return sorted(
        {
            w.consumer
            for w in waits
            if w.verdict == "unknown"
            and (n := graph.nodes.get(w.consumer)) is not None
            and n.is_open
        }
    )


def findings(
    graph: Graph, waits: list[Wait], cycles: list[list[str]], roadmap: Roadmap | None
) -> list[Finding]:
    """Находки графа (§3.3.2–3.3.5) и RM-GOAL-MISSING (§2.2)."""
    found = list(graph.findings)
    found += [Finding("GR-CYCLE", "error", c[0], " → ".join(c)) for c in cycles]
    for e in graph.edges:
        src, dst = graph.nodes.get(e.src), graph.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if e.type == "accepted_as" and src.is_open and dst.closed_as == "completed":
            found.append(
                Finding("GR-SHIPPED-OPEN", "warning", e.src, f"{e.dst} выполнен")
            )
        if (
            e.type == "implements"
            and src.closed_as == "merged"
            and dst.kind == "issue"
            and dst.is_open
        ):
            found.append(Finding("GR-SHIPPED-OPEN", "warning", e.dst, f"{e.src} влит"))
        if e.type == "mentions":
            found.append(Finding("GR-WEAK-EDGE", "info", e.src, e.dst))
    for w in waits:
        if w.reason == "missing" and (w.prereq or "").startswith("todo://"):
            found.append(
                Finding("GR-DANGLING-WAIT", "warning", w.consumer, w.prereq or "")
            )
    if roadmap is not None:
        for focus in roadmap.focus:
            if focus.goal is not None and focus.goal not in graph.nodes:
                found.append(
                    Finding("RM-GOAL-MISSING", "warning", focus.goal, focus.epic)
                )
    return sorted(set(found), key=lambda f: (f.code, f.subject, f.detail))
