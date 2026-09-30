"""Конвейер ядра и снимок conductor-snapshot/v1 (спека §7.1, §10)."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from conductor.analysis import dependency_adjacency, find_cycles, work_state
from conductor.analysis import findings as graph_findings
from conductor.graph import Graph, build_graph
from conductor.inputs import Inputs
from conductor.model import Finding
from conductor.policy import Assessment, assess
from conductor.rank import (
    Hint,
    QueueEntry,
    build_attention,
    build_hints,
    build_queue,
    rank_of,
)
from conductor.roadmap import Roadmap, parse_roadmap
from conductor.waits import Wait, evaluate_waits

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "conductor-snapshot"
    / "v1"
    / "schema.json"
)
DELEGATE = ("делегировать агенту?", ("delegate", "keep"))
WAIT_QUESTIONS = {
    "closed_unknown": (
        "закрыт без причины: считать выполненным или отменённым?",
        ("treat-done", "treat-cancelled", "keep"),
    ),
    "cancelled": (
        "предпосылка отменена: снять ожидание или найти замену?",
        ("drop-wait", "replace", "keep"),
    ),
    "missing": (
        "предпосылки нет ни в одном TODO: снять ожидание или завести запрос?",
        ("drop-wait", "request", "keep"),
    ),
    "version_mismatch": (
        "вышла другая версия, чем ждали: принять её?",
        ("accept-version", "keep"),
    ),
}
# Вопрос по находке — только там, где решать нечего кроме владельца. Для
# наследия inbox совпадение slug И ЕСТЬ связь принятия (ADR-ECO-006 D2, спека
# §6.4): GR-SLUG-MATCH остаётся информационной находкой — живой замер
# 2026-09-29 дал бы 8 вопросов «подтвердите принятие» на каждый legacy inbox.
FINDING_QUESTIONS = {
    "GR-SHIPPED-OPEN": ("похоже отгружено: закрыть issue?", ("close", "keep")),
}
PR_NEEDS = frozenset({"review", "wait_ci", "merge", "fix_pr"})


@dataclass
class Result:
    """Всё, что вычислило ядро за прогон."""

    graph: Graph
    waits: list[Wait]
    queue: list[QueueEntry]
    attention: list[QueueEntry]
    assessments: dict[str, Assessment]
    findings: list[Finding]
    roadmap: Roadmap
    cycles: list[list[str]]
    run_level: int
    graph_state: str
    blocks: dict[str, str]
    hints: list[Hint]


def block_of(entry: QueueEntry, a: Assessment, stale: set[str]) -> str:
    """§4.4: goal — путь к вехе; signal — ранг и подтверждённый сигнал."""
    if entry.rank is None:
        return "backlog"
    if entry.on_goal_path:
        return "goal"
    signal = (
        entry.unblocks > 0
        or entry.node_id in stale
        or a.need in PR_NEEDS
        or a.ask_owner
    )
    return "signal" if signal else "backlog"


def evaluate(inputs: Inputs, run_level: int) -> Result:
    """Детерминированный конвейер: граф → ожидания → очередь → политика."""
    roadmap = parse_roadmap(inputs.roadmap_text, inputs.epics)
    graph = build_graph(inputs)
    waits = evaluate_waits(
        graph, inputs, inputs.captured_at, roadmap.limits["stale_after_days"]
    )
    cycles = find_cycles(dependency_adjacency(graph))
    # §2.4: потолок прогона. plan — симуляция управляющего писателя: личность
    # хоста не проверяется (plan не пишет), но роадмап ограничивает всегда.
    # §2.1/§2.4: полномочия даёт только роадмап с origin зонтика — локальный
    # файл (`--roadmap`) проверяет разбор и порядок, но уровень держит 0
    trusted = inputs.roadmap_source == "origin"
    level = (
        min(run_level, roadmap.autonomy)
        if trusted and not graph.partial and roadmap.valid
        else 0
    )
    queue = build_queue(graph, waits, roadmap, inputs.captured_at)
    attention = build_attention(graph, waits, roadmap, inputs.captured_at)
    hints = build_hints(graph, roadmap)
    hinted = frozenset(h.target for h in hints)
    assessments = {
        e.node_id: assess(e, graph, waits, roadmap, level, inputs, hinted)
        for e in queue + attention
    }
    found = list(roadmap.findings) + graph_findings(graph, waits, cycles, roadmap)
    stale = {w.prereq for w in waits if w.reason == "stale" and w.prereq}
    return Result(
        graph,
        waits,
        queue,
        attention,
        assessments,
        found,
        roadmap,
        cycles,
        level,
        "partial" if graph.partial else "complete",
        {
            e.node_id: block_of(e, assessments[e.node_id], stale)
            for e in queue + attention
        },
        hints,
    )


def question_id(
    kind: str, subject: str, evidence: str, options: tuple[str, ...]
) -> str:
    """§5.7: sha256(kind, subject, evidence, варианты по порядку)[:8]."""
    raw = "\x1f".join((kind, subject, evidence, *options)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def _question(
    subject: str, reason: str, evidence: str, text: str, options: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "question_id": question_id(reason, subject, evidence, options),
        "subject": subject,
        "reason": reason,
        "evidence": evidence,
        "question": f"{subject}: {text}",
        "options": list(options),
        "default": "keep",
    }


def owner_questions(result: Result) -> list[dict[str, Any]]:
    """Вопрос по причине: у ожидания — своя, у делегируемости — своя (§5.7)."""
    out = []
    for a in result.assessments.values():
        if not a.ask_owner:
            continue
        waits = [
            w
            for w in result.waits
            if w.consumer == a.node_id and w.reason in WAIT_QUESTIONS
        ]
        for w in waits:
            text, options = WAIT_QUESTIONS[w.reason]
            evidence = f"{w.prereq}|{w.evidence}"
            out.append(_question(a.node_id, w.reason, evidence, text, options))
        if not waits:
            reason = a.block_reason or "unknown-wait"
            out.append(_question(a.node_id, reason, reason, *DELEGATE))
    for f in result.findings:
        if f.code not in FINDING_QUESTIONS:
            continue
        # то же правило ранга, что у позиций (§5.7 rev 10), с наследованием
        if rank_of(result.graph, result.roadmap, f.subject) is not None:
            out.append(
                _question(f.subject, f.code, f.detail, *FINDING_QUESTIONS[f.code])
            )
    return sorted(out, key=lambda q: (q["subject"], q["reason"]))


def _metrics(result: Result, questions: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(w.verdict for w in result.waits)
    assessed = list(result.assessments.values())
    return {
        "waits_satisfied": verdicts["satisfied"],
        "waits_pending": verdicts["pending"],
        "waits_stale": sum(w.reason == "stale" for w in result.waits),
        "waits_unknown": verdicts["unknown"],
        "actions_available": sum(a.action != "—" for a in assessed),
        "actions_executed": 0,
        "owner_questions": len(questions),
        "owner_questions_by_reason": dict(Counter(q["reason"] for q in questions)),
        "decide_unranked": sum(
            a.need == "decide" and not a.ask_owner for a in assessed
        ),
        # пометки позиций (признак решения, нет @owner, …) — не вопросы (§5.7)
        "position_flags": dict(
            Counter(
                a.block_reason
                for a in assessed
                if a.block_reason not in (None, "authority-root") and not a.ask_owner
            )
        ),
        "partial": result.graph_state == "partial",
    }


def _changes(result: Result, previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {"first_run": True}
    # прошлый снимок — только для отчёта (I7): чужая форма не роняет прогон
    before = {
        (w.get("consumer"), w.get("prereq")): w.get("verdict")
        for w in previous.get("waits", [])
        if isinstance(w, dict)
    }
    newly = [
        f"{w.consumer} ← {w.prereq}"
        for w in result.waits
        if w.verdict == "satisfied"
        and before.get((w.consumer, w.prereq)) not in (None, "satisfied")
    ]
    return {"first_run": False, "newly_satisfied": newly}


def _positions(entries: list[QueueEntry], result: Result) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        a = result.assessments[e.node_id]
        out.append(
            {
                **asdict(e),
                "block": result.blocks[e.node_id],
                "actor": a.actor,
                "need": a.need,
                "level": a.level,
                "delegable": a.delegable,
                "block_reason": a.block_reason,
                "action": a.action,
                "ask_owner": a.ask_owner,
            }
        )
    return out


def to_snapshot(
    result: Result, inputs: Inputs, run_id: str, previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Снимок по контракту conductor-snapshot/v1."""
    questions = owner_questions(result)
    graph = result.graph
    return {
        "contract": "conductor-snapshot/v1",
        "host": inputs.host,
        "run_id": run_id,
        "started_at": inputs.captured_at,
        "writer": {"is_writer": False, "reason": "срез 0: записей нет"},
        "roadmap": {
            "source": inputs.roadmap_source,
            "sha": inputs.roadmap_sha,
            "valid": result.roadmap.valid,
        },
        "run_level": result.run_level,
        "sources": [asdict(s) for s in graph.sources],
        "graph_state": result.graph_state,
        "nodes": [
            {**asdict(n), "work_state": work_state(n.node_id, graph)}
            for n in graph.nodes.values()
        ],
        "edges": [asdict(e) for e in graph.edges],
        "waits": [asdict(w) for w in result.waits],
        "queue": _positions(result.queue, result),
        "attention": _positions(result.attention, result),
        "hints": [asdict(h) for h in result.hints],
        "cycles": result.cycles,
        "findings": [asdict(f) for f in result.findings],
        "actions": {
            "plan": [
                {"node_id": a.node_id, "action": a.action}
                for a in result.assessments.values()
                if a.action != "—"
            ],
            "journal": [],
        },
        "owner_questions": questions,
        "metrics": _metrics(result, questions),
        "changes_since_previous": _changes(result, previous),
    }
