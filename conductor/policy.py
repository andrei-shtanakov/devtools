"""Уровень позиции, actor/need, делегируемость, выбор действия (§2.4, §5.2–5.3).

В срезе 0 результат только показывается. Путь authority-root из контекст-пака
(§5.2) подключается в срезе 3, поэтому лучший вердикт делегируемости здесь —
unverified, а действие — `launch?`, не `launch`. Вердикт `no` — причина
позиции; вопросом (`decide`) он становится только там, где ответ меняет
следующий шаг: позиция уже может получить запуск (§5.7 rev 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conductor.analysis import work_state
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.rank import QueueEntry
from conductor.roadmap import Roadmap
from conductor.waits import Wait, waits_of

DECISION_WORDS = (
    "sign-off",
    "подпис",
    "approve",
    "одобр",
    "approval-policy",
    "утверд",
    "новая версия контракта",
    "new contract version",
)
DECISION_PATH_PREFIXES = ("prograph-vault/authored/decisions/", ".github/")
OUT_OF_LOOP_REPOS = frozenset({"sdd-framework"})
# Неизвестно лишь условие, которое срез 0 не умеет вычислить: это ожидание,
# а не решение владельца (§5.2 rev 10) — вопроса нет.
CONDITION_REASONS = frozenset(
    {"prose_trigger", "fact_unread", "unread", "unresolvable"}
)
# Чужой владелец и репо вне контура — пометки позиции, не вопросы (§5.7):
# ответ владельца не меняет следующего шага и на уровне запуска.
NOT_QUESTION_REASONS = frozenset({"foreign-owner", "out-of-loop"})
Delegable = Literal["yes", "no", "unverified"]


@dataclass(frozen=True)
class Assessment:
    """Что conductor сделал бы с позицией на уровне level."""

    node_id: str
    level: int
    actor: str
    need: str
    delegable: Delegable
    block_reason: str | None
    action: str
    ask_owner: bool


def position_level(entry: QueueEntry, roadmap: Roadmap, run_level: int) -> int:
    """§2.4: свой класс задаёт потолок; ранг наследуется, полномочия — нет."""
    own = roadmap.focus_of(entry.own_focus)
    via = roadmap.focus_of(entry.via_focus)
    if entry.klass == "focus" and own is not None:
        return min(run_level, own.autonomy)
    if via is None:
        return min(run_level, 1) if entry.klass == "background" else 0
    if entry.klass == "background" and via.pull_prerequisites:
        return min(run_level, via.autonomy)
    return min(run_level, via.autonomy, 2)


def delegable(
    node_id: str, graph: Graph, inputs: Inputs
) -> tuple[Delegable, str | None]:
    """§5.2 (rev 9): владелец, признак решения, эпик, клон, контур."""
    node = graph.nodes[graph.resolve(node_id)]
    owner = node.owner()
    if owner is None or owner.get("kind") == "tbd":
        return "no", "owner-tbd"
    if owner["kind"] == "github_team" or (
        owner["kind"] == "github_user" and owner["id"] != inputs.owner
    ):
        return "no", "foreign-owner"
    text = "\n".join(
        f"{graph.nodes[m].title}\n{graph.nodes[m].body}"
        for m in graph.members(node_id)
        if m in graph.nodes
    ).lower()
    if any(w in text for w in DECISION_WORDS) or any(
        p in text for p in DECISION_PATH_PREFIXES
    ):
        # сигнал, не доказательство: блокирует запуск, вопросом не становится
        return "no", "decision-signal"
    status = inputs.epics.get(node.epic or "", {}).get("status")
    if status is None:
        return "no", "epic-unknown"
    if status != "active":
        return "no", "epic-not-active"
    todo = next((t for t in inputs.todos if t.repo == node.repo), None)
    if node.repo in OUT_OF_LOOP_REPOS or todo is None or todo.state == "error":
        return "no", "out-of-loop"
    return "unverified", "authority-root"


def _pr_need(graph: Graph, node_id: str, inputs: Inputs) -> tuple[str, str]:
    """§5.2: CI и одобрение именно head SHA; мерж человеку — по политике."""
    rec = graph.records.get(node_id, {})
    if rec.get("ci") == "red":
        return "fix_pr", rec.get("author") or "author"
    if not rec.get("approved_at_head"):
        return "review", "review-loop"
    if rec.get("ci") != "green":
        return "wait_ci", "ci"
    files = rec.get("files", [])
    touches = any(f.startswith(p) for f in files for p in inputs.authority_prefixes)
    if graph.nodes[node_id].repo in inputs.human_merge_repos or touches:
        return "merge", "owner"
    if not rec.get("complete") or not inputs.authority_prefixes:
        return "merge", "merge-contour?"
    return "merge", "merge-contour"


def _need(
    entry: QueueEntry, graph: Graph, waits: list[Wait], inputs: Inputs
) -> tuple[str, str]:
    node = graph.nodes[entry.node_id]
    unknown = [w for w in waits_of(waits, entry.node_id) if w.verdict == "unknown"]
    if any(w.reason not in CONDITION_REASONS for w in unknown):
        return "decide", "owner"
    if unknown:
        return "wait_condition", "condition"
    if node.kind == "pr":
        return _pr_need(graph, entry.node_id, inputs)
    if node.kind == "issue":
        if "inbox" in node.labels:
            return "intake", "conductor"
        return "triage", "owner"
    if work_state(entry.node_id, graph) == "in_review":
        return "review", "review-loop"
    return "implement", node.repo


def _stale(
    entry: QueueEntry, graph: Graph, waits: list[Wait], roadmap: Roadmap
) -> bool:
    if any(w.prereq == entry.node_id and w.reason == "stale" for w in waits):
        return True
    node = graph.nodes[entry.node_id]
    return (
        node.kind == "pr"
        and entry.line_age_days is not None
        and entry.line_age_days >= roadmap.limits["stale_after_days"]
    )


def _may_launch(
    entry: QueueEntry, roadmap: Roadmap, level: int, hinted: frozenset[str]
) -> bool:
    """Позиция может получить запуск: уровень 3, класс допускает, нет
    неподтверждённой подсказки §4.4 (слабое упоминание запуск не разрешает)."""
    via = roadmap.focus_of(entry.via_focus)
    return (
        level >= 3
        and entry.node_id not in hinted
        and (
            entry.klass == "focus"
            or (
                entry.klass == "background"
                and via is not None
                and via.pull_prerequisites
            )
        )
    )


def _action(
    need: str,
    level: int,
    entry: QueueEntry,
    roadmap: Roadmap,
    verdict: Delegable,
    stale: bool,
    hinted: frozenset[str],
) -> str:
    if level == 0:
        return "—"
    if need in ("decide", "triage"):
        return "owner_queue"
    if need == "intake":
        return "request_intake" if level >= 2 else "owner_queue"
    if need in ("review", "wait_ci", "merge", "fix_pr"):
        return "pr_nudge" if stale else "—"
    if (
        need == "implement"
        and _may_launch(entry, roadmap, level, hinted)
        and verdict != "no"
    ):
        return "launch" if verdict == "yes" else "launch?"
    return "nudge" if stale else "—"


def assess(
    entry: QueueEntry,
    graph: Graph,
    waits: list[Wait],
    roadmap: Roadmap,
    run_level: int,
    inputs: Inputs,
    hinted: frozenset[str] = frozenset(),
) -> Assessment:
    """Позиция → уровень, actor/need, делегируемость, действие (не исполняется)."""
    level = position_level(entry, roadmap, run_level)
    need, actor = _need(entry, graph, waits, inputs)
    verdict: Delegable = "unverified"
    reason: str | None = None
    if need == "implement":
        verdict, reason = delegable(entry.node_id, graph, inputs)
        if (
            verdict == "no"
            and reason not in NOT_QUESTION_REASONS
            and _may_launch(entry, roadmap, level, hinted)
        ):
            need, actor = "decide", "owner"
    stale = _stale(entry, graph, waits, roadmap)
    action = _action(need, level, entry, roadmap, verdict, stale, hinted)
    return Assessment(
        entry.node_id,
        level,
        actor,
        need,
        verdict,
        reason,
        action,
        need == "decide" and entry.rank is not None,
    )
