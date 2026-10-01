"""GitHub в два шага: поиск с проверкой полноты, адресное дочитывание (§3.1).

Только чтение: ни одна команда здесь не мутирует GitHub (срез 0). Поиск идёт
через REST `search/issues`, потому что только он отдаёт `incomplete_results`
и `total_count` — `gh search --json` их теряет. Обнаруживаются только
открытые узлы: закрытые, на которые кто-то ссылается, приходят адресным
дочитыванием независимо от возраста (окно закрытых упиралось бы в потолок
поиска в 1000 — замер 2026-09-29: 1310 закрытых за 30 дней).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from conductor.model import SourceState

GH_TIMEOUT = 120
Runner = Callable[[list[str]], tuple[int, str, str]]
ISSUE_FIELDS = "title,body,state,stateReason,author,labels,updatedAt,url,closedAt"
PR_FIELDS = (
    "title,body,state,mergedAt,author,labels,updatedAt,url,"
    "closingIssuesReferences,headRefOid,reviewDecision,statusCheckRollup,"
    "mergeCommit,isDraft,createdAt"
)
PR_EXTRA = (
    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
    "{pullRequest(number:$k){headRefOid latestReviews(first:50)"
    "{totalCount nodes{state submittedAt author{login} commit{oid}}}"
    " files(first:100){totalCount nodes{path}}"
    " commits(last:1){nodes{commit{committedDate}}}"
    " timelineItems(itemTypes:[READY_FOR_REVIEW_EVENT],last:1)"
    "{nodes{... on ReadyForReviewEvent{createdAt}}}}}}"
)
ISSUE_EXTRAS = (
    "query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n)"
    "{defaultBranchRef{name} issues(states:OPEN,first:50,after:$c)"
    "{pageInfo{hasNextPage endCursor} nodes{number createdAt"
    " closedByPullRequestsReferences(first:20,includeClosedPrs:true)"
    "{totalCount nodes{number merged mergedAt mergeCommit{oid} baseRefName"
    " baseRepository{defaultBranchRef{name}} repository{name}}}"
    " timelineItems(itemTypes:[REOPENED_EVENT],last:1)"
    "{nodes{... on ReopenedEvent{id createdAt}}}}}}}"
)
PINNED = (
    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
    "{issue(number:$k){isPinned}}}"
)
RED = {
    "FAILURE",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """Настоящий вызов gh; нет бинаря/таймаут — код 127."""
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


@dataclass
class GhResult:
    """Записи GitHub и честное состояние источника."""

    records: list[dict[str, Any]]
    state: SourceState
    detail: str


def ci_state(rollup: list[dict[str, Any]] | None) -> str:
    """Сводка проверок head SHA: red > pending > green; пусто — unknown."""
    if not rollup:
        return "unknown"
    states = [
        (
            (
                c.get("state")
                if c.get("__typename") == "StatusContext"
                else c.get("conclusion") or c.get("status")
            )
            or ""
        ).upper()
        for c in rollup
    ]
    if any(s in RED for s in states):
        return "red"
    return "green" if all(s in GREEN for s in states) else "pending"


def red_checks(rollup: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Упавшие проверки head SHA: имя и ссылка (лог не читается, §7.4)."""
    out = []
    for c in rollup or []:
        state = (
            (
                c.get("state")
                if c.get("__typename") == "StatusContext"
                else c.get("conclusion") or c.get("status")
            )
            or ""
        ).upper()
        if state in RED:
            name = c.get("name") or c.get("context") or "?"
            out.append(
                {"name": name, "url": c.get("detailsUrl") or c.get("targetUrl") or ""}
            )
    return out


def _json(out: str | None, default: str = "null") -> Any:
    """JSON ответа gh; битый ответ при коде 0 — None (сбой чтения, не падение)."""
    try:
        return json.loads(out or default)
    except ValueError:
        return None


def _search(
    owner: str, qualifier: str, runner: Runner
) -> tuple[list[dict[str, Any]] | None, str]:
    code, out, err = runner(
        [
            "api",
            "-X",
            "GET",
            "search/issues",
            "-f",
            f"q=user:{owner} {qualifier}",
            "-f",
            "per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    if code != 0:
        return None, err.strip() or f"gh api exit {code}"
    pages = _json(out, "[]")
    if not isinstance(pages, list):
        return None, f"поиск «{qualifier}»: битый ответ"
    items = [item for page in pages for item in page.get("items", [])]
    total = pages[0].get("total_count", 0) if pages else 0
    if any(page.get("incomplete_results") for page in pages):
        return None, f"поиск «{qualifier}»: incomplete_results"
    if total > len(items):
        return None, f"поиск «{qualifier}»: получено {len(items)} из {total}"
    return items, ""


def discover(
    owner: str, fleet_names: set[str], runner: Runner
) -> tuple[list[tuple[str, int, bool]], SourceState, str]:
    """Открытые issues/PR репо флота (закрытые — адресным дочитыванием)."""
    items, problem = _search(owner, "is:open", runner)
    if items is None:
        return [], "error", problem
    hits = [
        (name, item["number"], "pull_request" in item)
        for item in items
        if (name := item["repository_url"].rsplit("/", 1)[-1]) in fleet_names
    ]
    return hits, "read", ""


def _comments(
    owner: str, name: str, number: int, runner: Runner
) -> list[dict[str, str]] | None:
    code, out, _ = runner(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{name}/issues/{number}/comments",
        ]
    )
    pages = _json(out, "[]") if code == 0 else None
    if not isinstance(pages, list):
        return None
    return [
        {
            "id": c.get("id"),
            "author": (c.get("user") or {}).get("login", ""),
            "body": c.get("body") or "",
            "created_at": c.get("created_at", ""),
            "updated_at": c.get("updated_at", ""),
        }
        for page in pages
        for c in page
    ]


def _pr_extra(
    owner: str, name: str, number: int, runner: Runner
) -> dict[str, Any] | None:
    """Одобрение именно head SHA и изменённые файлы (gh pr view их не отдаёт)."""
    code, out, _ = runner(
        [
            "api",
            "graphql",
            "-f",
            f"query={PR_EXTRA}",
            "-f",
            f"o={owner}",
            "-f",
            f"n={name}",
            "-F",
            f"k={number}",
        ]
    )
    try:
        pr = _json(out)["data"]["repository"]["pullRequest"] if code == 0 else None
    except (KeyError, TypeError):
        pr = None
    if not isinstance(pr, dict) or "headRefOid" not in pr:
        return None
    head = pr["headRefOid"]
    reviews = pr["latestReviews"]["nodes"]
    approved = any(
        r["state"] == "APPROVED" and (r.get("commit") or {}).get("oid") == head
        for r in reviews
    ) and not any(r["state"] == "CHANGES_REQUESTED" for r in reviews)
    files = [f["path"] for f in pr["files"]["nodes"]]
    commits = (pr.get("commits") or {}).get("nodes") or []
    ready = (pr.get("timelineItems") or {}).get("nodes") or []
    return {
        "extra_head": head,
        "approved_at_head": approved,
        "files": files,
        "complete": pr["files"]["totalCount"] <= len(files)
        and pr["latestReviews"]["totalCount"] <= len(reviews),
        "last_commit_at": commits[-1]["commit"]["committedDate"] if commits else None,
        "ready_at": ready[-1].get("createdAt") if ready else None,
        "reviews": [
            {
                "author": (r.get("author") or {}).get("login", ""),
                "submitted_at": r.get("submittedAt") or "",
            }
            for r in reviews
        ],
    }


def fetch_record(
    owner: str, name: str, number: int, is_pr: bool, runner: Runner
) -> dict[str, Any] | None:
    """Одна запись (без поля repo) или None при любом сбое чтения."""
    kind, fields = ("pr", PR_FIELDS) if is_pr else ("issue", ISSUE_FIELDS)
    code, out, _ = runner(
        [kind, "view", str(number), "-R", f"{owner}/{name}", "--json", fields]
    )
    raw = _json(out) if code == 0 else None
    # `gh issue view` отвечает и на номер PR (url …/pull/N): такую запись
    # читаем как PR, иначе закрытый без мержа PR выглядел бы выполненным issue
    if not isinstance(raw, dict) or (not is_pr and "/pull/" in raw.get("url", "")):
        return None
    comments = _comments(owner, name, number, runner)
    if comments is None:
        return None
    record: dict[str, Any] = {
        "number": number,
        "is_pr": is_pr,
        "title": raw.get("title", ""),
        "body": raw.get("body") or "",
        "state": "open" if raw.get("state") == "OPEN" else "closed",
        "state_reason": (raw.get("stateReason") or "").lower() or None,
        "merged": bool(raw.get("mergedAt")),
        "author": (raw.get("author") or {}).get("login", ""),
        "labels": [lab["name"] for lab in raw.get("labels", [])],
        "updated_at": raw.get("updatedAt", ""),
        "url": raw.get("url", ""),
        "closed_at": raw.get("closedAt"),
        "comments": comments,
        "closing_refs": [
            f"{r['repository']['name']}#{r['number']}"
            for r in raw.get("closingIssuesReferences") or []
        ],
    }
    if is_pr:
        extra = _pr_extra(owner, name, number, runner)
        # CI (из pr view) и одобрение (из GraphQL) — об одном и том же SHA,
        # иначе готовность к мержу не доказана: чтение считается сбоем.
        if extra is None or extra.pop("extra_head") != raw.get("headRefOid"):
            return None
        record.update(
            head_sha=raw.get("headRefOid"),
            review_decision=raw.get("reviewDecision"),
            ci=ci_state(raw.get("statusCheckRollup")),
            merge_sha=(raw.get("mergeCommit") or {}).get("oid"),
            is_draft=bool(raw.get("isDraft")),
            created_at=raw.get("createdAt", ""),
            red_checks=red_checks(raw.get("statusCheckRollup")),
            **extra,
        )
    return record


def _weak_pass(
    owner: str,
    names_to_keys: dict[str, str],
    records: dict[tuple[str, int], dict[str, Any]],
    weak_refs: Callable[[list[dict[str, Any]]], set[tuple[str, int]]] | None,
    runner: Runner,
) -> int:
    """Дочитать слабые ссылки без продолжения цепочки; вернуть число сбоев."""
    if weak_refs is None:
        return 0
    key_to_name = {key: name for name, key in names_to_keys.items()}
    wanted = {
        (key_to_name.get(key, key), number)
        for key, number in weak_refs(list(records.values()))
    }
    missed = 0
    for name, number in sorted(wanted - set(records)):
        record = fetch_record(owner, name, number, False, runner) or fetch_record(
            owner, name, number, True, runner
        )
        if record is None:
            missed += 1
            continue
        # слабая запись: её полнота (файлы/ревью PR) не требуется
        record.update(repo=names_to_keys.get(name, name), weak=True)
        records[(name, number)] = record
    return missed


def collect_gh(
    owner: str,
    names_to_keys: dict[str, str],
    extra_refs: Callable[[list[dict[str, Any]]], set[tuple[str, int]]],
    runner: Runner,
    max_hops: int = 3,
    weak_refs: Callable[[list[dict[str, Any]]], set[tuple[str, int]]] | None = None,
) -> GhResult:
    """Обнаружение + дочитывание ссылок до неподвижной точки (≤ max_hops);
    затем один проход слабых ссылок — сбой их чтения полноту не портит."""
    hits, state, detail = discover(owner, set(names_to_keys), runner)
    if state != "read":
        return GhResult([], state, detail)
    key_to_name = {key: name for name, key in names_to_keys.items()}
    records: dict[tuple[str, int], dict[str, Any]] = {}
    queue = {(name, number): is_pr for name, number, is_pr in hits}
    failed: set[tuple[str, int]] = set()
    for _ in range(max_hops + 1):
        for (name, number), is_pr in sorted(queue.items()):
            record = fetch_record(owner, name, number, is_pr, runner)
            if record is None and not is_pr:
                record = fetch_record(owner, name, number, True, runner)
            if record is None:
                failed.add((name, number))  # остальные ссылки всё равно читаем
                continue
            record["repo"] = names_to_keys.get(name, name)
            records[(name, number)] = record
        wanted = {
            (key_to_name.get(key, key), number)
            for key, number in extra_refs(list(records.values()))
        }
        queue = {
            ref: False
            for ref in wanted - set(records) - failed
            if ref[0] in names_to_keys
        }
        if not queue:
            missed = _weak_pass(owner, names_to_keys, records, weak_refs, runner)
            if failed:
                names = ", ".join(f"{n}#{k}" for n, k in sorted(failed)[:10])
                more = f" и ещё {len(failed) - 10}" if len(failed) > 10 else ""
                return GhResult(
                    list(records.values()), "error", f"не дочитан {names}{more}"
                )
            detail = f"слабых ссылок не дочитано: {missed}" if missed else ""
            return GhResult(list(records.values()), "read", detail)
    return GhResult(
        list(records.values()), "error", f"ссылки не сошлись за {max_hops} шага"
    )


def _extras_node(node: dict[str, Any]) -> dict[str, Any] | None:
    refs = node["closedByPullRequestsReferences"]
    if refs["totalCount"] > len(refs["nodes"]):
        return None  # усечено — основание не доказано (§7.5)
    reopened = node["timelineItems"]["nodes"]
    return {
        "created_at": node["createdAt"],
        "period": reopened[-1]["id"] if reopened else "0",
        "period_start": reopened[-1]["createdAt"] if reopened else node["createdAt"],
        "closed_by": [
            {
                "repo": r["repository"]["name"],
                "number": r["number"],
                "merged": bool(r["merged"]),
                "merged_at": r.get("mergedAt"),
                "merge_sha": (r.get("mergeCommit") or {}).get("oid"),
                "base_is_default": r["baseRefName"]
                == ((r.get("baseRepository") or {}).get("defaultBranchRef") or {}).get(
                    "name"
                ),
            }
            for r in refs["nodes"]
        ],
    }


def issue_extras(
    owner: str, name: str, runner: Runner
) -> dict[int, dict[str, Any]] | None:
    """Открытые issues репо: период открытия и закрывающие PR (§7.5); сбой — None."""
    extras: dict[int, dict[str, Any]] = {}
    cursor: str | None = None
    for _ in range(50):
        args = ["api", "graphql", "-f", f"query={ISSUE_EXTRAS}", "-f", f"o={owner}"]
        args += ["-f", f"n={name}"] + (["-f", f"c={cursor}"] if cursor else [])
        code, out, _ = runner(args)
        try:
            page = (_json(out) if code == 0 else None)["data"]["repository"]["issues"]
        except (KeyError, TypeError):
            return None
        for node in page["nodes"]:
            extra = _extras_node(node)
            if extra is None:
                return None
            extras[node["number"]] = extra
        if not page["pageInfo"]["hasNextPage"]:
            return extras
        cursor = page["pageInfo"]["endCursor"]
    return None


def queue_records(owner: str, name: str, runner: Runner) -> list[dict[str, Any]] | None:
    """Issues очереди владельца в любом состоянии (§6.1); сбой — None."""
    items, _ = _search(owner, f"repo:{owner}/{name} label:owner-queue is:issue", runner)
    if items is None:
        return None
    out = []
    for item in items:
        rec = fetch_record(owner, name, item["number"], False, runner)
        if rec is None:
            return None
        code, raw, _ = runner(
            ["api", "graphql", "-f", f"query={PINNED}", "-f", f"o={owner}"]
            + ["-f", f"n={name}", "-F", f"k={item['number']}"]
        )
        try:
            rec["pinned"] = bool(
                (_json(raw) if code == 0 else None)["data"]["repository"]["issue"][
                    "isPinned"
                ]
            )
        except (KeyError, TypeError):
            return None
        rec["repo_full"] = f"{owner}/{name}"
        out.append(rec)
    return out


def closed_event(owner: str, name: str, number: int, runner: Runner) -> str | None:
    """id (node_id) последнего события `closed` timeline issue — fact_id
    закрытия (§7.1, решение владельца 2026-10-01: не `closedAt`); нет
    события — "", сбой — None."""
    code, out, _ = runner(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{name}/issues/{number}/timeline?per_page=100",
        ]
    )
    pages = _json(out, "[]") if code == 0 else None
    if not isinstance(pages, list):
        return None
    ids = [
        e.get("node_id") or ""
        for page in pages
        for e in page
        if isinstance(e, dict) and e.get("event") == "closed"
    ]
    return ids[-1] if ids else ""
