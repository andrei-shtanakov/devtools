"""Единый граф: узлы TODO/issue/PR, типизированные рёбра, склейка (§3.1–3.2).

Все концы рёбер — канонические ключи манифеста: GitHub-имя и прежнее имя
(`prograph-vault`) нормализуются к ключу (`ecosystem-kb`) одной таблицей для
всех источников; `repo#N` на номер PR — узел `repo!N` (§3.1 rev 11). Склейка
accepted_as делает issue и пункт одним узлом работы: resolve() даёт
представителя (пункт), members() — всё множество.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import plan_fields as pf

from conductor.inputs import Inputs, RepoTodo
from conductor.manifest import manifest_index
from conductor.model import (
    Edge,
    EdgeType,
    Finding,
    Node,
    Source,
    issue_id,
    item_id,
    pr_id,
)

REF_RE = re.compile(r"(?<![\w/.-])([a-z0-9][a-z0-9-]*)#(\d+)\b")
TODO_REF_RE = re.compile(r"todo://([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9._-]{0,63})")
TODO_URI_RE = re.compile(r"^todo://[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,63}$")
SELF_REF_RE = re.compile(r"(?<![\w/#.-])#(\d+)\b")
LEGACY_SLUG_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)#([a-z0-9][a-z0-9._-]{0,63})$")
# конец ребра для @blocked_by, который не распознан: ожидание остаётся видимым
UNRESOLVED = "unresolved:"
LEGACY_ISSUE_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)#(\d+)$")
# id, извлечённый из свободной прозы (тело/комментарии issue и PR): последний
# символ обязан быть буквой/цифрой, иначе пунктуация конца предложения
# (`@id:goal-a4.`, `todo://repo/x.`) входит в id — найдено живой приёмкой
# conductor slice-1 (sandbox PR #10, 2026-10-04). Внутренние `.`/`-`/`_`
# (`a.b`) — сохраняются; это не касается структурных полей (`@id:` в TODO,
# `todo://` URI с якорями `^...$`), у них формат гарантирован сеткой правил.
_PROSE_ID = r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?"
TODO_REF_PROSE_RE = re.compile(rf"todo://([a-z0-9][a-z0-9-]*)/({_PROSE_ID})")
PR_ITEM_RE = re.compile(rf"@id:({_PROSE_ID})")
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
FROM_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)(?:#([a-z0-9][a-z0-9._-]{0,63}))?(?:\s|$)")
FROM_ORIGIN = "inbox:from"
# адреса ссылок и якоря: `](#3)`, URL — не ссылки на issue
LINK_RE = re.compile(r"\]\([^)]*\)|https?://\S+")


@dataclass
class Graph:
    """Граф прогона; partial — хотя бы один источник не прочитан (I6)."""

    nodes: dict[str, Node]
    edges: list[Edge]
    canon: dict[str, str] = field(default_factory=dict)
    # склейка слабо дочитанных записей — только для подсказок §4.4, не для работы
    hint_canon: dict[str, str] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    todo_sha: dict[str, str | None] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    partial: bool = False
    # репо, чей TODO не прочитан, и прочитан ли GitHub: «не прочитано» ≠ «нет»
    unread_repos: frozenset[str] = frozenset()
    gh_read: bool = True
    # открытая заявка на ещё не заведённый пункт: todo://<repo>/<slug> → issue
    pending_requests: dict[str, str] = field(default_factory=dict)
    # отклонённая (not_planned) заявка на незаведённый пункт: он не появится
    declined_requests: dict[str, str] = field(default_factory=dict)

    def source_unread(self, node_id: str) -> bool:
        """Источник узла не прочитан — отсутствие узла ничего не доказывает."""
        if node_id.startswith("todo://"):
            return node_id.removeprefix("todo://").split("/")[0] in self.unread_repos
        return not self.gh_read

    def resolve(self, node_id: str) -> str:
        """Представитель узла работы (пункт для принятого issue)."""
        return self.canon.get(node_id, node_id)

    def members(self, node_id: str) -> set[str]:
        """Все узлы, склеенные с node_id в один узел работы."""
        rep = self.resolve(node_id)
        return {rep} | {k for k, v in self.canon.items() if v == rep}

    def out(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Исходящие рёбра типа kind."""
        return [e for e in self.edges if e.src == node_id and e.type == kind]

    def into(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Входящие рёбра типа kind."""
        return [e for e in self.edges if e.dst == node_id and e.type == kind]

    def target(self, edge: Edge) -> str:
        """Конец ребра в графе работ: открытый запрос — его склеенный пункт
        (цикл и ранг идут через работу), закрытый по from: — сам запрос."""
        node = self.nodes.get(edge.dst)
        if edge.origin == FROM_ORIGIN and node is not None and not node.is_open:
            return edge.dst
        if node is None and edge.dst in self.pending_requests:
            # пункта ещё нет, заявка на него в пути: ранг и цикл идут через неё
            return self.pending_requests[edge.dst]
        return self.resolve(edge.dst)

    def epic_of(self, node_id: str) -> str | None:
        """Эпик узла работы (у принятого issue — эпик пункта)."""
        node = self.nodes.get(self.resolve(node_id))
        return node.epic if node is not None else None


def field_value(body: str, name: str) -> str | None:
    """Значение `name:` в теле; бэктики, кавычки и \\r снимаются."""
    match = re.search(rf"(?im)^\s*{name}:\s*(.+?)\s*$", body.replace("\r", ""))
    return match.group(1).strip("`'\" ") if match else None


def normalizer(inputs: Inputs) -> dict[str, str]:
    """GitHub-имя и канонический ключ → канонический ключ."""
    norm = {todo.repo: todo.repo for todo in inputs.todos}
    norm.update(inputs.repo_names)
    return norm


def _norm_ref(ref: str, norm: dict[str, str]) -> str | None:
    repo, _, number = ref.partition("#")
    if repo in norm and number.isdigit():
        return issue_id(norm[repo], int(number))
    return None


def canonical_id(node_id: str, norm: dict[str, str]) -> str:
    """Идентификатор узла из ввода человека — в канонических ключах."""
    if node_id.startswith("todo://"):
        return _canon_uri(node_id, norm)
    for sep in ("#", "!"):
        repo, found, number = node_id.partition(sep)
        if found and repo in norm:
            return f"{norm[repo]}{sep}{number}"
    return node_id


def _canon_uri(uri: str, norm: dict[str, str]) -> str:
    """todo://<имя>/<id> → todo://<ключ>/<id>; незнакомое имя — как есть."""
    if match := TODO_REF_RE.fullmatch(uri):
        return item_id(norm.get(match.group(1), match.group(1)), match.group(2))
    return uri


def _pr_aware(node_id: str, nodes: dict[str, Node]) -> str:
    """repo#N, который на деле PR, — узел repo!N (один ряд номеров GitHub)."""
    repo, sep, number = node_id.partition("#")
    if sep and node_id not in nodes and (pr := f"{repo}!{number}") in nodes:
        return pr
    return node_id


def _item_nodes(snapshot: dict[str, Any]) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for raw in snapshot["nodes"]:
        closed = raw["declared_status"] != "open"
        owner = raw.get("owner_ref")
        nodes[raw["node_id"]] = Node(
            raw["node_id"],
            "item",
            raw["repo"],
            raw["title"],
            is_open=not closed,
            closed_as="completed" if closed else None,
            epic=raw.get("epic"),
            owner_ref=tuple(sorted(owner.items())) if owner else None,
            trigger=raw.get("trigger"),
            source_ref=(raw.get("freshness") or {}).get("source_ref"),
        )
    return nodes


def _todo_edges(
    snapshot: dict[str, Any], norm: dict[str, str], findings: list[Finding]
) -> list[Edge]:
    """depends_on из references: plan-fields не строит edges на несуществующий
    пункт (resolved_target = None), а висячее ожидание должно остаться видимым.
    Каждый @blocked_by даёт ребро: нераспознанный или в репо вне флота — на
    UNRESOLVED с находкой.
    Концы канонизируются; исходная запись — в origin."""
    edges: list[Edge] = []
    for ref in snapshot["references"]:
        if ref["kind"] != "blocked_by":
            continue
        raw = ref.get("raw_ref") or ""
        target = ref.get("resolved_target") or (raw if TODO_URI_RE.match(raw) else None)
        legacy = ref.get("legacy_blocker_ref") or ""
        if target is None and LEGACY_ISSUE_RE.match(legacy):
            target = _norm_ref(legacy, norm) or legacy
        elif target is None and (slug := LEGACY_SLUG_RE.match(legacy or raw)):
            target = item_id(norm.get(slug.group(1), slug.group(1)), slug.group(2))
        elif target is None:
            target = UNRESOLVED + raw
            findings.append(
                Finding(
                    "GR-BLOCKER-UNRESOLVABLE", "warning", ref["source_node_id"], raw
                )
            )
        if not target.startswith(UNRESOLVED) and _repo_of(target) not in norm:
            # чужой репо не дочитывается: «предпосылки нет» было бы ложью (§3.1)
            findings.append(
                Finding("GR-REF-OUT-OF-FLEET", "warning", ref["source_node_id"], raw)
            )
            target = UNRESOLVED + raw
        edges.append(
            Edge(
                ref["source_node_id"],
                _canon_uri(target, norm),
                "depends_on",
                f"todo:{raw}",
            )
        )
    return edges


def _repo_of(node_id: str) -> str:
    """Репо конца ребра: todo://<repo>/<id> или <repo>#<N>."""
    if node_id.startswith("todo://"):
        return node_id.removeprefix("todo://").split("/")[0]
    return node_id.partition("#")[0]


def _gh_id(rec: dict[str, Any]) -> str:
    return (pr_id if rec["is_pr"] else issue_id)(rec["repo"], rec["number"])


def _gh_node(rec: dict[str, Any]) -> Node:
    if rec["state"] == "open":
        closed_as = None
    elif rec["is_pr"]:
        closed_as = "merged" if rec["merged"] else "unmerged"
    else:
        # пустая причина не доказывает ни выполнения, ни отмены (§3.4)
        closed_as = rec.get("state_reason") or None
    return Node(
        _gh_id(rec),
        "pr" if rec["is_pr"] else "issue",
        rec["repo"],
        rec["title"],
        is_open=rec["state"] == "open",
        closed_as=closed_as,
        author=rec.get("author"),
        updated_at=rec.get("updated_at"),
        body=rec.get("body", ""),
        labels=tuple(rec.get("labels", [])),
        url=rec.get("url", ""),
    )


def _gh_edges(
    rec: dict[str, Any],
    nodes: dict[str, Node],
    norm: dict[str, str],
    findings: list[Finding],
    unread: frozenset[str] = frozenset(),
) -> list[Edge]:
    me = _gh_id(rec)
    edges: list[Edge] = []
    if rec["is_pr"]:
        edges += [
            Edge(me, target, "implements", "pr:closes")
            for ref in rec.get("closing_refs", [])
            if (target := _norm_ref(ref, norm)) is not None
        ]
        findings += [
            Finding("GR-REF-OUT-OF-FLEET", "info", me, ref)
            for ref in rec.get("closing_refs", [])
            if _norm_ref(ref, norm) is None
        ]
        edges += [
            Edge(me, item_id(rec["repo"], m), "implements", "pr:@id")
            for m in PR_ITEM_RE.findall(rec.get("body", ""))
        ]
        # PR — не заявка ADR-ECO-006 и с меткой inbox: склейка увела бы его
        # из очереди (accepted_as)
        return edges
    if "inbox" in rec.get("labels", []):
        edges += _inbox_edges(rec, me, nodes, norm, findings, unread=unread)
    elif (header := _legacy_protocol(rec, nodes, norm)) is not None:
        findings.append(Finding("GR-INBOX-NO-LABEL", "info", me, "нет метки inbox"))
        edges += _inbox_edges(rec, me, nodes, norm, findings, header, unread)
    elif field_value(rec.get("body", ""), "slug") and field_value(
        rec.get("body", ""), "from"
    ):
        # поля протокола есть, заявки нет: issue остаётся видимым, причина — тоже
        findings.append(
            Finding("GR-PROTOCOL-UNRECOGNIZED", "info", me, "нет шапки slug:/from:")
        )
    return edges


def _protocol_header(body: str) -> tuple[str, str] | None:
    """Шапка заявки ADR-ECO-006: первые две непустые строки — `slug:` и
    `from:` (в любом порядке). Пример протокола внутри текста шапкой не
    бывает — разбирать Markdown не нужно."""
    lines = [ln.strip() for ln in body.replace("\r", "").splitlines() if ln.strip()]
    fields = {
        m.group(1): m.group(2)
        for ln in lines[:2]
        if (m := re.fullmatch(r"(slug|from):\s*(.+)", ln)) is not None
    }
    if set(fields) != {"slug", "from"}:
        return None
    return fields["slug"].strip("`'\" "), fields["from"].strip("`'\" ")


def _legacy_protocol(
    rec: dict[str, Any], nodes: dict[str, Node], norm: dict[str, str]
) -> tuple[str, str] | None:
    """Заявка без метки — только шапка протокола целиком (§3.3 rev 11): slug по
    грамматике @id, from: с известного репо, пункт @id = slug есть."""
    header = None if rec["is_pr"] else _protocol_header(rec.get("body", ""))
    if header is None:
        return None
    slug, sender = header
    match = FROM_RE.match(sender)
    known = (
        ID_RE.fullmatch(slug) is not None
        and match is not None
        and match.group(1) in norm
        and item_id(rec["repo"], slug) in nodes
    )
    return header if known else None


def _inbox_edges(
    rec: dict[str, Any],
    me: str,
    nodes: dict[str, Node],
    norm: dict[str, str],
    findings: list[Finding],
    header: tuple[str, str] | None = None,
    unread: frozenset[str] = frozenset(),
) -> list[Edge]:
    """Наследие ADR-ECO-006: склейка по slug (D2), ожидание отправителя по from.
    header — поля распознанной шапки заявки без метки (иначе — из тела)."""
    edges: list[Edge] = []
    slug = header[0] if header else field_value(rec["body"], "slug")
    target = item_id(rec["repo"], slug) if slug else None
    if target is not None and target in nodes:
        edges.append(Edge(me, target, "accepted_as", "inbox:slug"))
        own = {
            f"{name}#{rec['number']}"
            for name, key in norm.items()
            if key == rec["repo"]
        }
        if nodes[target].source_ref not in own:
            findings.append(
                Finding(
                    "GR-SLUG-MATCH",
                    "info",
                    me,
                    f"{target}: принят по slug без @source-ref",
                )
            )
    sender = header[1] if header else field_value(rec["body"], "from") or ""
    match = FROM_RE.match(sender)
    repo, waiting = (match.group(1), match.group(2)) if match else ("", None)
    orphan = None
    if not sender:
        orphan = "нет from:"
    elif repo not in norm:
        orphan = f"from: {sender} — неизвестный репо"
    elif waiting and item_id(norm[repo], waiting) not in nodes:
        # TODO отправителя не прочитан — отсутствие пункта ничего не доказывает
        if norm[repo] not in unread:
            orphan = f"from: {sender} — ждущего пункта нет"
    elif waiting and nodes[me].closed_as != "completed":
        # закрытый как completed запрос ответил: ребро — история, не ожидание
        edges.append(Edge(item_id(norm[repo], waiting), me, "depends_on", FROM_ORIGIN))
    if orphan is not None:
        findings.append(Finding("GR-ORPHAN-REQUEST", "warning", me, orphan))
    return edges


def _text_of(rec: dict[str, Any]) -> str:
    comments = "\n".join(c["body"] for c in rec.get("comments", []))
    return "\n".join((rec.get("title", ""), rec.get("body", ""), comments))


def _issue_refs(
    rec: dict[str, Any], norm: dict[str, str]
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    """Один экстрактор для рёбер и дочитывания: (repo#N, голые #N своего репо).
    Голые номера — только из заголовка («docs(#603): …»): в теле это якоря,
    ссылочные ссылки Markdown и номера чужих репо; заголовок их не содержит."""
    text = _text_of(rec)
    qualified = {(norm[r], int(n)) for r, n in REF_RE.findall(text) if r in norm}
    title = LINK_RE.sub("", rec.get("title", ""))
    return qualified, {(rec["repo"], int(n)) for n in SELF_REF_RE.findall(title)}


def _mentions(
    rec: dict[str, Any],
    strong: set[tuple[str, str]],
    norm: dict[str, str],
    nodes: dict[str, Node],
) -> list[Edge]:
    me = _gh_id(rec)
    text = _text_of(rec)
    qualified, local = _issue_refs(rec, norm)
    targets = {issue_id(repo, n) for repo, n in qualified | local}
    targets |= {
        item_id(norm[r], i) for r, i in TODO_REF_PROSE_RE.findall(text) if r in norm
    }
    # `a#9` у PR a!9 — он сам: самоссылка и сверка со strong — по уже
    # переведённому в PR-форму узлу, иначе «a#9 ≠ a!9» проходило (#551)
    targets = {_pr_aware(t, nodes) for t in targets}
    return [
        Edge(me, t, "mentions", "body")
        for t in sorted(targets)
        if t != me and (me, t) not in strong and (t, me) not in strong
    ]


def referenced_issues(
    records: list[dict[str, Any]], todos: list[RepoTodo], norm: dict[str, str]
) -> set[tuple[str, int]]:
    """Строгие цели дочитывания — только структурные ссылки: @blocked_by TODO и
    closing_refs PR. Упоминания в телах на готовность не влияют (§3.1): их
    строгое дочитывание тянуло цепочку закрытых issue без конца (замер
    2026-09-30 — «ссылки не сошлись за 3 шага», вечный partial)."""
    refs: set[tuple[str, int]] = set()
    for todo in todos:
        refs |= {
            (norm[r], int(n))
            for r, n in re.findall(r"@blocked_by:([a-z0-9-]+)#(\d+)", todo.text or "")
            if r in norm
        }
    for rec in records:
        refs |= {
            (norm[r], int(n))
            for r, n in REF_RE.findall(" ".join(rec.get("closing_refs", [])))
            if r in norm
        }
    return refs


def local_refs(
    records: list[dict[str, Any]], norm: dict[str, str]
) -> set[tuple[str, int]]:
    """Слабые ссылки: голые #N своего репо в заголовках открытых PR — питают только
    подсказки §4.4, поэтому дочитываются без требования полноты (§3.1)."""
    return {
        ref
        for rec in records
        if rec["is_pr"] and rec["state"] == "open"
        for ref in _title_refs(rec, norm)
    }


def _title_refs(rec: dict[str, Any], norm: dict[str, str]) -> set[tuple[str, int]]:
    """repo#N и голые #N заголовка — цели подсказок («docs(spec-runner#603)»)."""
    title = LINK_RE.sub("", rec.get("title", ""))
    qualified = {(norm[r], int(n)) for r, n in REF_RE.findall(title) if r in norm}
    return qualified | {(rec["repo"], int(n)) for n in SELF_REF_RE.findall(title)}


def _sources(inputs: Inputs) -> list[Source]:
    sources = [Source(f"todo:{t.repo}", t.state, t.detail, t.sha) for t in inputs.todos]
    sources += [
        Source("github", inputs.gh_state, inputs.gh_detail),
        Source(
            "roadmap", inputs.roadmap_state, inputs.roadmap_source, inputs.roadmap_sha
        ),
        Source("epics", inputs.epics_state, inputs.epics_detail, inputs.epics_sha),
        Source("history", inputs.aux_state, inputs.aux_detail),
        Source("manifest", "read", inputs.manifest_source, inputs.manifest_sha),
    ]
    return sources


def build_graph(inputs: Inputs) -> Graph:
    """Граф из входов; ядро без ввода-вывода (индекс — из текста манифеста)."""
    repo_inputs = [
        pf.RepoInput(
            t.repo,
            todo_text=t.text or "",
            commit=t.sha,
            available=t.state in ("read", "absent"),
        )
        for t in inputs.todos
    ]
    snapshot = pf.parse_fleet(repo_inputs, manifest_index(inputs.manifest_text))
    norm = normalizer(inputs)
    nodes = _item_nodes(snapshot)
    findings: list[Finding] = []
    edges = _todo_edges(snapshot, norm, findings)
    unread = frozenset(
        t.repo for t in inputs.todos if t.state not in ("read", "absent")
    )
    records = {_gh_id(rec): rec for rec in inputs.gh_records}
    nodes.update({node_id: _gh_node(rec) for node_id, rec in records.items()})
    # слабо дочитанные записи (§3.1) — только узлы и склейка для подсказок:
    # ни ожиданий, ни implements, ни находок — готовность они не подтверждают
    solid = [rec for rec in inputs.gh_records if not rec.get("weak")]
    for rec in solid:
        edges += _gh_edges(rec, nodes, norm, findings, unread)
    hint_canon = {
        e.src: e.dst
        for rec in inputs.gh_records
        if rec.get("weak")
        for e in _gh_edges(rec, nodes, norm, [])
        if e.type == "accepted_as"
    }
    edges = [Edge(e.src, _pr_aware(e.dst, nodes), e.type, e.origin) for e in edges]
    strong = {(e.src, e.dst) for e in edges}
    for rec in solid:
        edges += _mentions(rec, strong, norm, nodes)
    edges = [Edge(e.src, _pr_aware(e.dst, nodes), e.type, e.origin) for e in edges]
    sources = _sources(inputs)
    return Graph(
        nodes=nodes,
        edges=sorted(set(edges), key=lambda e: (e.src, e.dst, e.type)),
        canon={e.src: e.dst for e in edges if e.type == "accepted_as"},
        hint_canon=hint_canon,
        records=records,
        todo_sha={t.repo: t.sha for t in inputs.todos},
        sources=sources,
        findings=findings,
        partial=any(s.state in ("error", "not_queried") for s in sources),
        unread_repos=unread,
        gh_read=inputs.gh_state == "read",
        pending_requests=_requests_for_missing(solid, nodes, norm, _is_open),
        declined_requests=_requests_for_missing(solid, nodes, norm, _is_declined),
    )


def _is_open(rec: dict[str, Any]) -> bool:
    return rec["state"] == "open"


def _is_declined(rec: dict[str, Any]) -> bool:
    return rec["state"] != "open" and rec.get("state_reason") == "not_planned"


def _requests_for_missing(
    records: list[dict[str, Any]],
    nodes: dict[str, Node],
    norm: dict[str, str],
    keep: Callable[[dict[str, Any]], bool],
) -> dict[str, str]:
    """Заявка (метка inbox или шапка) на пункт, которого ещё нет, в состоянии
    keep: открытая — рукопожатие в процессе, отклонённая — отмена; ни то, ни
    другое не «предпосылки нет»."""
    pending: dict[str, str] = {}
    for rec in records:
        if rec["is_pr"] or not keep(rec):
            continue
        header = _protocol_header(rec.get("body", ""))
        sender = FROM_RE.match(header[1]) if header else None
        # шапка с неизвестным отправителем — не заявка (как у _legacy_protocol)
        slug = header[0] if sender is not None and sender.group(1) in norm else None
        if slug is None and "inbox" in rec.get("labels", []):
            slug = field_value(rec.get("body", ""), "slug")
        target = item_id(rec["repo"], slug) if slug else None
        if target is not None and target not in nodes:
            pending[target] = _gh_id(rec)
    return pending
