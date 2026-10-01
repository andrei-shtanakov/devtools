"""nudge и pr_nudge (спека среза 1, §7.3–7.4).

Возраст — от периода P (ребро) или от последнего ready_for_review (PR);
тишина — от max(начало возраста, последнее движение не от App). Пинок —
только по позициям с рангом; красный CI пинка сам не даёт. Пинок идёт в
первый открытый адрес, а нумерация и интервал — по ожиданию и периоду во
ВСЕХ тредах продюсера (решение владельца 4): смена адреса их не сбрасывает.
Перед отправкой — свежая сверка ожидания, периода и движения (шаг 8 §5.1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from conductor import facts
from conductor.actions.common import (
    WaitRef,
    addresses,
    authority,
    due,
    edited_findings,
    entry_rank,
    fresh_fact,
    fresh_item,
    movement,
    node_repo_item,
    pr_activity,
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
    pr_need,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import parse_ts
from conductor.rank import rank_of
from conductor.snapshot import PR_NEEDS, Result
from conductor.sources_git import GitError, last_commit_mentioning
from conductor.writer import PlanRecord, Step


def _limits(result: Result) -> tuple[timedelta, timedelta]:
    limits = result.roadmap.limits
    return timedelta(days=limits["stale_after_days"]), timedelta(
        days=limits["renudge_after_days"]
    )


def _prior(
    graph: Any, addrs: list[str], bot: str, kind: str, **match: str
) -> list[tuple[int, datetime]]:
    """Действительные маркеры пинка во всех тредах addrs (открытых и закрытых)."""
    return [
        (int(m.get("n")), parse_ts(c["created_at"]))
        for addr in addrs
        for m, c in thread_events(graph, addr, bot)[0]
        if m.kind == kind and all(m.get(k) == v for k, v in match.items())
    ]


def _newer(stamp: str | None, since: datetime | None) -> bool:
    return bool(stamp) and (since is None or parse_ts(stamp or "") > since)


def moved_since(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    node_id: str,
    since: datetime | None,
    bot: str,
) -> str | None:
    """Новое движение по узлу работы после since (свежие чтения, §7.3).

    Комментарии не от App во всех тредах, коммиты с `@id`, открытые PR с
    `implements` (по `updated_at` PR — консервативно: изменение снимает пинок
    до следующего прогона, который посчитает движение заново).
    """
    for member in sorted(graph.members(node_id)):
        node = graph.nodes.get(member)
        if node is None:
            continue
        if node.kind == "item":
            parts = node_repo_item(member)
            clone = fresh.git(parts[0]) if parts else None
            if parts is None or clone is None:
                return "история не прочитана"
            try:
                when = last_commit_mentioning(clone[0], clone[1], f"@id:{parts[1]}")
            except GitError:
                return "история не прочитана"
            if _newer(when, since):
                return "новое движение"
            continue
        repo, number = target(inputs, member)
        comments = fresh.comments(repo, number)
        if comments is None:
            return "тред не прочитан"
        if any(c["author"] != bot and _newer(c["created_at"], since) for c in comments):
            return "новое движение"
    for member in sorted(graph.members(node_id)):
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is None or not pr.is_open:
                continue
            repo, number = target(inputs, edge.src)
            pull = fresh.pull(repo, number)
            planned = (graph.records.get(edge.src) or {}).get("updated_at")
            if pull is None or pull.get("updated_at") != planned:
                return "новое движение по PR"
    return None


def nudge_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    ref: WaitRef,
    p_sha: str,
    moved: datetime | None,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8: адрес открыт, маркера нет, ожидание pending, период тот же,
    движения после планирования нет."""

    def pending() -> str | None:
        fact, problem = fresh_fact(fresh, graph, inputs, ref)
        if problem is not None:
            return problem
        return None if fact is None else "ожидание больше не pending"

    def same_period() -> str | None:
        state, clone, problem = fresh_item(fresh, ref.src)
        if problem is not None or clone is None:
            return problem or "период не прочитан"
        if state is None or ref.raw not in state.blocked_by:
            return "тег ожидания снят"
        repo_key, item = node_repo_item(ref.src) or ("", "")
        try:
            period = facts.edge_period(clone[0], clone[1], repo_key, item, ref.raw)
        except GitError:
            return "период не прочитан"
        return None if period and period[0] == p_sha else "период изменился"

    def check(m: Mutation) -> str | None:
        return first_reason(
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: comment_check(fresh, m, bot),
            pending,
            same_period,
            lambda: moved_since(fresh, graph, inputs, ref.prereq or "", moved, bot),
        )

    return check


def plan_nudges(
    result: Result,
    inputs: Inputs,
    bot: str,
    now: datetime,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Пинки продюсерам по ожиданиям pending (§7.3)."""
    graph = result.graph
    stale, renudge = _limits(result)
    records: list[PlanRecord] = []
    for ref, wait in wait_refs(graph, result.waits):
        if wait.verdict != "pending" or ref.prereq is None:
            continue
        where = f"{ref.src}|{ref.raw}"
        if rank_of(graph, result.roadmap, ref.prereq) is None:
            notes.append({"action": "nudge", "wait": where, "reason": "нет ранга"})
            continue
        addrs, every = addresses(graph, ref.prereq), threads(graph, ref.prereq)
        if not addrs:
            reason = "нет открытого адреса доставки" if every else "нет адреса"
            notes.append({"action": "nudge", "wait": where, "reason": reason})
            continue
        period = inputs.edge_periods.get(where)
        if period is None:
            notes.append(
                {"action": "nudge", "wait": where, "reason": "возраст неизвестен"}
            )
            continue
        p_sha, p_date = period
        age_start = parse_ts(p_date)
        moved = movement(graph, inputs, ref.prereq, bot)
        silent_since = max(age_start, moved) if moved else age_start
        addr, wid, p = addrs[0], wait_id(ref), h1("period", p_sha)
        notes += edited_findings(graph, every, bot, "nudge")
        prior = _prior(graph, every, bot, "nudge", wait=wid, p=p)
        n = due(now, age_start, silent_since, moved, prior, stale, renudge)
        if n is None:
            continue
        marker = make("nudge", wait=wid, p=p, n=str(n))
        text = (
            f"Ждём {ref.prereq}: от него зависит {ref.consumer} с {p_date[:10]}. "
            f"Следующий шаг — довести {ref.prereq} или ответить в этом треде."
        )
        repo, number = target(inputs, addr)
        m = Mutation("comment", repo, number, text=with_marker(text, marker))
        records.append(
            PlanRecord(
                "nudge",
                addr,
                h1("rev", wid, p, str(n)),
                1,
                (Step(m, render(marker)),),
                nudge_check(fresh, graph, inputs, ref, p_sha, moved, bot)
                if fresh
                else valid,
                authority(result, inputs, ref.prereq, ranked=True),
            )
        )
    return records


def pr_text(need: str, rec: dict[str, Any]) -> str:
    """Текст пинка PR: что ждёт и следующий шаг; для красного — имена проверок."""
    if need == "fix_pr":
        checks = "; ".join(
            f"{c['name']} — {c['url']}" for c in rec.get("red_checks", [])
        )
        return (
            f"CI красный на head: {checks or 'проверки не названы'}; "
            "лог не прочитан conductor — нужен разбор автором."
        )
    if need == "review":
        return "PR ждёт ревью на текущем head."
    if need == "wait_ci":
        return "PR ждёт завершения проверок на текущем head."
    return (
        "PR готов к мержу по данным conductor; мерж — за контуром мержа или владельцем."
    )


def _red(checks: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(c["name"], c["url"]) for c in checks}


def pr_nudge_check(
    fresh: FreshReader, rec: dict[str, Any], need: str, bot: str
) -> Callable[[Mutation], str | None]:
    """Шаг 8: свежие основания потребности PR на head (проверки, одобрение)
    дают ту же потребность и, для красного CI, те же упавшие проверки, что в
    тексте плана; PR открыт, не драфт, head прежний; маркера нет.
    `updated_at` — дополнительный консервативный запрет, не замена чтения."""

    def same_need(m: Mutation) -> str | None:
        facts = fresh.pr_facts(m.repo, m.number or 0)
        if facts is None:
            return "основания PR не прочитаны"
        if not facts["open"] or facts["draft"]:
            return "PR закрыт или драфт"
        if facts["head"] != rec.get("head_sha"):
            return "новая голова PR"
        if pr_need(facts) != need:
            return "потребность PR изменилась"
        if need == "fix_pr" and _red(facts["red"]) != _red(rec.get("red_checks", [])):
            return "набор упавших проверок изменился"
        if facts["updated_at"] != rec.get("updated_at"):
            return "новое движение по PR"
        return None

    def check(m: Mutation) -> str | None:
        return first_reason(lambda: comment_check(fresh, m, bot), lambda: same_need(m))

    return check


def plan_pr_nudges(
    result: Result,
    inputs: Inputs,
    bot: str,
    now: datetime,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Пинки по застоявшимся PR позиций с рангом (§7.4)."""
    graph = result.graph
    stale, renudge = _limits(result)
    records: list[PlanRecord] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.node_id):
        if node.kind != "pr" or not node.is_open:
            continue
        assessment = result.assessments.get(node.node_id)
        if assessment is None or assessment.need not in PR_NEEDS:
            continue
        if entry_rank(result, node.node_id) is None:
            notes.append(
                {"action": "pr_nudge", "pr": node.node_id, "reason": "нет ранга"}
            )
            continue
        rec = graph.records.get(node.node_id) or {}
        if rec.get("is_draft"):
            notes.append({"action": "pr_nudge", "pr": node.node_id, "reason": "драфт"})
            continue
        start_raw = rec.get("ready_at") or rec.get("created_at")
        if not start_raw:
            notes.append(
                {
                    "action": "pr_nudge",
                    "pr": node.node_id,
                    "reason": "возраст неизвестен",
                }
            )
            continue
        age_start = parse_ts(start_raw)
        activity = [parse_ts(s) for s in pr_activity(rec, bot)]
        moved = max(activity) if activity else None
        silent_since = max(age_start, moved) if moved else age_start
        pr = h1("pr", node.node_id)
        notes += edited_findings(graph, [node.node_id], bot, "pr_nudge")
        prior = _prior(graph, [node.node_id], bot, "prnudge", pr=pr)
        n = due(now, age_start, silent_since, moved, prior, stale, renudge)
        if n is None:
            continue
        marker = make("prnudge", pr=pr, n=str(n))
        repo, number = target(inputs, node.node_id)
        m = Mutation(
            "comment",
            repo,
            number,
            text=with_marker(pr_text(assessment.need, rec), marker),
        )
        records.append(
            PlanRecord(
                "pr_nudge",
                node.node_id,
                h1("rev", pr, str(n)),
                1,
                (Step(m, render(marker)),),
                pr_nudge_check(fresh, rec, assessment.need, bot) if fresh else valid,
                authority(result, inputs, node.node_id, ranked=True),
            )
        )
    return records
