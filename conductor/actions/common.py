"""Общие помощники планировщиков среза 1 (§5.7, §7.1, §7.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from conductor import facts
from conductor.analysis import dependency_adjacency
from conductor.fresh import FreshReader
from conductor.graph import FROM_ORIGIN, Graph, _gh_edges, normalizer
from conductor.inputs import Inputs
from conductor.markers import Marker, classify, h1
from conductor.opstate import parse_ts
from conductor.policy import position_level
from conductor.rank import _entry, _reverse
from conductor.roadmap import Roadmap
from conductor.snapshot import Result
from conductor.sources_git import GitError, git_lines
from conductor.waits import DATE_RE, EXISTS_RE, Wait


@dataclass(frozen=True)
class WaitRef:
    """Ребро ожидания: исходный src и ссылка (raw) — идентичность §7.3."""

    src: str
    raw: str
    consumer: str
    prereq: str | None


def wait_refs(graph: Graph, waits: list[Wait]) -> list[tuple[WaitRef, Wait]]:
    """Ожидания ядра с их рёбрами (та же канонизация, что evaluate_waits)."""
    by_pair = {(w.consumer, w.prereq): w for w in waits}
    out: list[tuple[WaitRef, Wait]] = []
    seen: set[tuple[str, str | None]] = set()
    for e in graph.edges:
        if e.type != "depends_on":
            continue
        raw = e.origin.removeprefix("todo:") if e.origin.startswith("todo:") else e.dst
        consumer = graph.resolve(e.src)
        prereq = e.dst if e.origin == FROM_ORIGIN else graph.resolve(e.dst)
        wait = by_pair.get((consumer, prereq))
        if wait is not None and (consumer, prereq) not in seen:
            seen.add((consumer, prereq))
            out.append((WaitRef(e.src, raw, consumer, prereq), wait))
    for w in waits:
        if w.prereq is None:
            text = graph.nodes[w.consumer].trigger or ""
            out.append((WaitRef(w.consumer, f"trigger:{text}", w.consumer, None), w))
    return out


def wait_id(ref: WaitRef) -> str:
    """Идентичность ожидания: (src, ссылка); правка заголовка её не меняет."""
    return h1("wait", ref.src, ref.raw)


def fact_of(graph: Graph, inputs: Inputs, ref: WaitRef) -> str | None:
    """Идентичность факта выполнения предпосылки (§7.1); неизвестно — None."""
    if ref.prereq is None:
        text = ref.raw.removeprefix("trigger:")
        if m := DATE_RE.match(text):
            return f"date:{m.group(1)}"
        if EXISTS_RE.match(text) and (sha := inputs.path_added.get(text)):
            return f"path:{sha}"
        return None
    node = graph.nodes.get(ref.prereq)
    if node is None:
        return None
    if node.kind == "item":
        sha = inputs.done_facts.get(node.node_id)
        return f"item:{sha}" if sha else None
    rec = graph.records.get(node.node_id) or {}
    if node.kind == "issue":
        # id события closed (решение владельца 2026-10-01), не closedAt:
        # время не обещает уникальности последовательных закрытий
        event = inputs.closed_events.get(node.node_id)
        return f"closed:{event}" if event else None
    return f"merged:{rec['merge_sha']}" if rec.get("merge_sha") else None


def threads(graph: Graph, node_id: str) -> list[str]:
    """Все треды узла (О §5.7), открытые и закрытые: issue/PR — свой;
    пункт — заявки, склеенные с ним или исходящие от него."""
    node = graph.nodes.get(node_id)
    if node is None:
        return []
    if node.kind in ("issue", "pr"):
        return [node_id]
    glued = {m for m in graph.members(node_id) if m != node_id}
    sent = {e.dst for e in graph.out(node_id, "depends_on") if e.origin == FROM_ORIGIN}
    return sorted(
        m
        for m in glued | sent
        if (n := graph.nodes.get(m)) is not None and n.kind == "issue"
    )


def addresses(graph: Graph, node_id: str) -> list[str]:
    """Адреса доставки — только открытые треды (решение владельца 5)."""
    return [m for m in threads(graph, node_id) if graph.nodes[m].is_open]


def no_address_reason(graph: Graph, node_id: str) -> str:
    """Почему адреса нет: тредов нет вовсе (TODO-only — канал среза 1b) или
    все закрыты («нет открытого адреса»: не доставка и не 1b)."""
    if threads(graph, node_id):
        return "нет открытого адреса доставки"
    return "канал доставки — срез 1b"


def edited_findings(
    graph: Graph, thread_ids: list[str], bot: str, action: str
) -> list[dict[str, Any]]:
    """MK-EDITED: отредактированный маркер — находка в выдаче (решение
    владельца 3), маркер недействителен, доставкой не считается."""
    return [
        {"finding": "MK-EDITED", "action": action, "thread": t}
        for t in thread_ids
        if thread_events(graph, t, bot)[1]
    ]


def thread_events(
    graph: Graph, node_id: str, bot: str
) -> tuple[list[tuple[Marker, dict[str, Any]]], int]:
    """Действительные маркеры треда и число отредактированных (MK-EDITED)."""
    events: list[tuple[Marker, dict[str, Any]]] = []
    edited = 0
    for c in (graph.records.get(node_id) or {}).get("comments", []):
        kind, marker = classify(c, bot)
        if kind == "event" and marker is not None:
            events.append((marker, c))
        elif kind == "edited":
            edited += 1
    return events, edited


def pr_activity(rec: dict[str, Any], bot: str) -> list[str]:
    """Движение PR не от App: коммиты, ready, ревью, комментарии (§7.4)."""
    stamps = [rec.get("last_commit_at") or "", rec.get("ready_at") or ""]
    stamps += [
        r["submitted_at"] for r in rec.get("reviews", []) if r.get("author") != bot
    ]
    stamps += [
        c["created_at"] for c in rec.get("comments", []) if c.get("author") != bot
    ]
    return [s for s in stamps if s]


def movement(graph: Graph, inputs: Inputs, node_id: str, bot: str) -> datetime | None:
    """Последнее движение по узлу работы не от App; updated_at — не движение."""
    stamps: list[str] = []
    for member in graph.members(node_id):
        stamps.append(inputs.movement.get(member) or "")
        rec = graph.records.get(member) or {}
        stamps += [
            c["created_at"] for c in rec.get("comments", []) if c.get("author") != bot
        ]
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is not None and pr.is_open:
                stamps += pr_activity(graph.records.get(edge.src) or {}, bot)
    values = [parse_ts(s) for s in stamps if s]
    return max(values) if values else None


def authority(
    result: Result, inputs: Inputs, node_id: str, ranked: bool = False
) -> Callable[[Roadmap, int], int]:
    """Уровень позиции по свежему роадмапу (О §2.4) для исполнителя.

    Граф — прогона; класс, ранг, фокус и `focus.autonomy` — из роадмапа,
    перечитанного перед шагом. ranked — действие требует ранга (§7.3–7.5):
    позиция без ранга по свежему роадмапу — уровень 0.
    """
    graph, waits = result.graph, result.waits
    rev = _reverse(dependency_adjacency(graph))

    def level(roadmap: Roadmap, run_level: int) -> int:
        if node_id not in graph.nodes:
            return 0
        entry = _entry(node_id, graph, waits, roadmap, rev, inputs.captured_at)
        if ranked and entry.rank is None:
            return 0
        return position_level(entry, roadmap, run_level)

    return level


def node_repo_item(node_id: str) -> tuple[str, str] | None:
    """todo://<repo>/<item> → (repo, item); иначе None."""
    if not node_id.startswith("todo://"):
        return None
    repo, _, item = node_id.removeprefix("todo://").partition("/")
    return repo, item


def fresh_item(
    fresh: FreshReader, node_id: str
) -> tuple[facts.ItemState | None, tuple[Any, str] | None, str | None]:
    """Пункт на свежем origin — каноническим разбором ядра (`facts.item_state`):
    (состояние | None — пункта нет, (каталог, ref), причина отказа: сбой
    чтения или неоднозначный `@id` — запись по нему не разрешается)."""
    parts = node_repo_item(node_id)
    if parts is None:
        return None, None, "не пункт TODO"
    clone = fresh.git(parts[0])
    if clone is None:
        return None, None, f"{parts[0]}: git не прочитан"
    repo_dir, ref = clone
    code, text, _ = facts.git(repo_dir, "show", f"{ref}:{facts.TODO}")
    if code != 0:
        return None, None, f"{parts[0]}: TODO.md не прочитан"
    # тот же текст, что у ядра (`read_todo`): иначе ожидание, которое граф
    # видит, здесь «снято», и мутация молча отменяется (#551)
    text = git_lines(text)
    try:
        return facts.item_state(text, parts[0], parts[1]), clone, None
    except facts.AmbiguousItem:
        return None, clone, "идентичность пункта неоднозначна (@id дважды)"


def fresh_fact(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> tuple[str | None, str | None]:
    """Факт выполнения предпосылки по свежим чтениям (как fact_of).

    (fact_id | None — не выполнена, причина сбоя чтения | None).
    """
    try:
        return _fresh_fact(fresh, graph, inputs, ref)
    except GitError as exc:
        return None, str(exc)


def _fresh_fact(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> tuple[str | None, str | None]:
    if ref.prereq is None:
        text = ref.raw.removeprefix("trigger:")
        if m := DATE_RE.match(text):
            return f"date:{m.group(1)}", None
        m = EXISTS_RE.match(text)
        clone = fresh.git(m.group(1)) if m else None
        if m is None or clone is None:
            return None, "условие не перепроверяется"
        sha = facts.path_added(clone[0], clone[1], m.group(2))
        return (f"path:{sha}" if sha else None), None
    node = graph.nodes.get(ref.prereq)
    if node is None:
        return None, "предпосылка вне графа"
    if node.kind == "item":
        state, clone, problem = fresh_item(fresh, node.node_id)
        if problem is not None or clone is None:
            return None, problem
        if state is None or not state.done:
            return None, None
        repo_key, item = node_repo_item(node.node_id) or ("", "")
        sha = facts.first_done_commit(clone[0], clone[1], repo_key, item)
        return (f"item:{sha}" if sha else None), None
    repo, number = target(inputs, node.node_id)
    if node.kind == "issue":
        issue = fresh.issue(repo, number)
        if issue is None:
            return None, "предпосылка не прочитана"
        if issue.get("state") != "closed":
            return None, None
        if issue.get("state_reason") != "completed":
            return "closed-not-completed", None
        event = fresh.last_event(repo, number, "closed")
        if event is None:
            return None, "timeline не прочитан"
        return (f"closed:{event}" if event else None), None
    pull = fresh.pull(repo, number)
    if pull is None:
        return None, "PR не прочитан"
    sha = pull.get("merge_commit_sha") if pull.get("merged") else None
    return (f"merged:{sha}" if sha else None), None


def fresh_accepted(
    fresh: FreshReader, graph: Graph, inputs: Inputs, subject: str
) -> tuple[str | None, str | None]:
    """Куда склеена заявка subject по СВЕЖЕЙ записи (метки, тело) — теми же
    правилами `accepted_as`, что у ядра (`graph._gh_edges`: метка inbox или
    распознанная шапка протокола). (пункт | None, причина сбоя чтения | None)."""
    repo, number = target(inputs, subject)
    issue = fresh.issue(repo, number)
    if issue is None:
        return None, "заявка не прочитана"
    rec = {
        "repo": subject.partition("#")[0],
        "number": number,
        "is_pr": False,
        "body": issue.get("body") or "",
        "labels": [lab.get("name") for lab in issue.get("labels") or []],
    }
    edges = _gh_edges(rec, graph.nodes, normalizer(inputs), [])
    accepted = [e.dst for e in edges if e.type == "accepted_as"]
    return (accepted[0] if accepted else None), None


def target(inputs: Inputs, node_id: str) -> tuple[str, int]:
    """repo#N / repo!N → (owner/github-name, N)."""
    sep = "#" if "#" in node_id else "!"
    key, _, number = node_id.partition(sep)
    names = {k: name for name, k in inputs.repo_names.items()}
    return f"{inputs.owner}/{names.get(key, key)}", int(number)


def entry_rank(result: Result, node_id: str) -> int | None:
    """Ранг позиции очереди (у PR — через узлы, которые он реализует)."""
    for e in result.queue + result.attention:
        if e.node_id == node_id:
            return e.rank
    return None


def due(
    now: datetime,
    age_start: datetime,
    silent_since: datetime,
    moved: datetime | None,
    prior: list[tuple[int, datetime]],
    stale: timedelta,
    renudge: timedelta,
) -> int | None:
    """Номер следующего пинка или None (§7.3: первый и две ветки повтора)."""
    if not prior:
        if now - age_start >= stale and now - silent_since >= stale:
            return 1
        return None
    last_n, last_at = max(prior)
    if now - last_at < renudge:
        return None
    if moved is not None and moved > last_at and now - moved < stale:
        return None
    return last_n + 1
