"""История доставок базы: заявки и остановки по first-parent (§9.1, решение 10).

Узкий модуль: зависит только от `act_envelope`, `act_request`, `act_time`,
`act_git_rows` — его читает и `act_status` (реэкспорт прежних имён), и программа
проверяющего, у которой нет `ops`. `ops` — любой объект с методами `HistoryOps`
(`RealOps`, `SnapshotOps`, `act_history_snapshot.SnapshotHistory`).
"""

from __future__ import annotations

from typing import Protocol

from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    EnvelopeError,
    History,
    parse_stop,
    stop_path,
)
from governance.act_git_rows import FirstParentRows
from governance.act_request import RequestError, manifest_path, parse_manifest
from governance.act_time import OPS_FAILURES, Unavailable


class HistoryScope(Protocol):
    """Чья история: воркстрим, продукт и каталог его репозитория (`RunState`)."""

    ws_id: str
    repo_slug: str
    target_dir: str


class HistoryOps(Protocol):
    """Ровно те методы `Ops`, которые зовёт чтение истории."""

    def first_parent_changes(
        self, target_dir: str, ref: str
    ) -> FirstParentRows | None: ...

    def show_file(self, target_dir: str, ref: str, path: str) -> str | None: ...

    def rev_parse(self, target_dir: str, ref: str) -> str | None: ...

    def fetch_branch(self, target_dir: str, branch: str) -> bool: ...

    def prs_containing_commit(self, repo_slug: str, sha: str) -> list[dict]: ...

    def pr_facts(self, repo_slug: str, pr: int) -> dict: ...


def pr_for_merge(ops: HistoryOps, slug: str, merge_commit: str) -> int | Unavailable:
    """Единственный смёрженный PR, чей merge-коммит — `merge_commit` (merge и
    squash)."""
    try:
        prs = ops.prs_containing_commit(slug, merge_commit)
    except OPS_FAILURES as exc:
        return Unavailable(f"PR merge-коммита {merge_commit[:12]}: {exc}")
    found = [
        p["number"]
        for p in prs
        if p.get("state") == "MERGED" and p.get("mergeCommit") == merge_commit
    ]
    if len(found) != 1:
        return Unavailable(
            f"merge-коммит {merge_commit[:12]}: PR {found} (нужен ровно один)"
        )
    return int(found[0])


def _pr_head(ops: HistoryOps, state: HistoryScope, pr: int) -> str | Unavailable:
    try:
        head = ops.pr_facts(state.repo_slug, pr).get("headRefOid")
    except OPS_FAILURES as exc:
        return Unavailable(f"голова PR #{pr}: {exc}")
    if not isinstance(head, str) or len(head) != 40:
        return Unavailable(f"голова PR #{pr} не названа")
    if ops.rev_parse(state.target_dir, f"{head}^{{commit}}") is None:
        # squash: голова вне базы — догрузить её из refs/pull/N/head
        ops.fetch_branch(state.target_dir, f"refs/pull/{pr}/head")
        if ops.rev_parse(state.target_dir, f"{head}^{{commit}}") is None:
            return Unavailable(
                f"голова PR #{pr} {head[:12]} не догружена (refs/pull/{pr}/head)"
            )
    return head


def _request_event(
    ops: HistoryOps, state: HistoryScope, commit: str, path: str, text: str
) -> DeliveredRequest | Unavailable:
    where = f"{commit[:12]}:{path}"
    try:
        req = parse_manifest(text)
    except RequestError as exc:
        return Unavailable(f"{where}: {exc}")
    if req.ws_id != state.ws_id or manifest_path(req.ws_id, req.request_id) != path:
        return Unavailable(f"{where}: манифест лежит не по своему пути")
    # голова одобренного коммита — из фактов PR доставки (merge и squash, B1-9)
    pr = pr_for_merge(ops, state.repo_slug, commit)
    if isinstance(pr, Unavailable):
        return pr
    head = _pr_head(ops, state, pr)
    if isinstance(head, Unavailable):
        return head
    return DeliveredRequest(commit, head, req)


def _event(
    ops: HistoryOps, state: HistoryScope, commit: str, status: str, path: str
) -> DeliveredRequest | DeliveredStop | Unavailable | None:
    requests_dir = f"workstreams/{state.ws_id}/approvals/requests/"
    stops_dir = f"workstreams/{state.ws_id}/approvals/stops/"
    if not path.startswith((requests_dir, stops_dir)):
        return None  # конверты и прочее — не события истории
    where = f"{commit[:12]}:{path}"
    if status != "A":
        return Unavailable(
            f"{where}: запись истории изменена после доставки ({status})"
        )
    text = ops.show_file(state.target_dir, commit, path)
    if text is None:
        return Unavailable(f"{where}: не читается")
    if path.startswith(requests_dir):
        return _request_event(ops, state, commit, path, text)
    try:
        stop = parse_stop(text)
    except EnvelopeError as exc:
        return Unavailable(f"{where}: {exc}")
    if stop_path(state.ws_id, stop.node, stop.generation) != path:
        return Unavailable(f"{where}: остановка лежит не по своему пути")
    return DeliveredStop(commit, stop)


def load_history(
    ops: HistoryOps, scope: HistoryScope, base_ref: str
) -> History | Unavailable:
    """Доставленные заявки и остановки по first-parent базы, старые первыми.

    Заявка — коммит first-parent, ДОБАВИВШИЙ манифест; правка или удаление
    записи истории после доставки — `Unavailable` (Р-8, сужено b1)."""
    rows = ops.first_parent_changes(scope.target_dir, base_ref)
    if rows is None:
        return Unavailable(f"история {base_ref}: first-parent не прочитан")
    events: list[DeliveredRequest | DeliveredStop] = []
    for commit, _parents, changes in rows:
        for status, path in changes:
            event = _event(ops, scope, commit, status, path)
            if isinstance(event, Unavailable):
                return event
            if event is not None:
                events.append(event)
    return tuple(events)
