"""Фейковый GitHub для исполнителя: мир issues и комментариев (тесты).

Мутации применяются к миру и отвечают как GitHub; чтения (GET и GraphQL-
запросы) отдают текущее состояние мира — verify и свежие сверки шага
проверяются против того же мира. `apply = False` — GitHub «принял», но
эффект не виден в чтении. `sent` — только мутации (не чтения).
"""

from __future__ import annotations

import json
import re
from typing import Any

from conductor.gh_app import Blocked, CallResult
from conductor.http import Response

ISSUE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)$")
COMMENTS_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)/comments(?:\?.*)?$")
COMMENT_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/comments/(\d+)$")
TIMELINE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues/(\d+)/timeline(?:\?.*)?$")
PULL_RE = re.compile(r"^/repos/([^/]+/[^/]+)/pulls/(\d+)$")
REPO_RE = re.compile(r"^/repos/([^/]+/[^/]+)$")
CREATE_RE = re.compile(r"^/repos/([^/]+/[^/]+)/issues$")
SEARCH_RE = re.compile(r"^/search/issues\?q=repo:([^+]+)\+label:owner-queue")
PAGE_RE = re.compile(r"[?&]page=(\d+)")
STAMP = "2026-10-01T12:00:00Z"


def ok(data: Any, status: int = 200) -> CallResult:
    """Успешный ответ с JSON-телом."""
    return CallResult("ok", Response(status, {}, json.dumps(data).encode()))


def _page(items: list[Any], path: str) -> list[Any]:
    match = PAGE_RE.search(path)
    page = int(match.group(1)) if match else 1
    return items[(page - 1) * 100 : page * 100]


class FakeClient:
    """Двойник AppClient над миром issues (repo, N)."""

    def __init__(self) -> None:
        self.bot_login = "conductor[bot]"
        self.key_ok = True
        self.cover: dict[str, bool | None] = {}
        self.blocked = False
        self.apply = True
        self.override: dict[tuple[str, str], CallResult] = {}
        self.sent: list[tuple[str, str, dict | None]] = []
        self.reads: list[str] = []
        self.covers_calls: list[str] = []
        self.issues: dict[tuple[str, int], dict[str, Any]] = {}
        self.pulls: dict[tuple[str, int], dict[str, Any]] = {}
        self._next_number = 100
        self._next_comment = 1000

    # --- мир ---------------------------------------------------------------

    def issue(self, repo: str, number: int, **fields: Any) -> dict[str, Any]:
        """Issue мира (создаётся открытым по умолчанию); fields — правка."""
        data = self.issues.setdefault(
            (repo, number),
            {
                "state": "open",
                "state_reason": None,
                "body": "",
                "title": "",
                "labels": [],
                "user": "own",
                "pinned": False,
                "comments": [],
                "timeline": [],
            },
        )
        data.update(fields)
        return data

    def add_comment(
        self, repo: str, number: int, author: str, body: str, **fields: Any
    ) -> dict[str, Any]:
        """Комментарий в тред мира; fields — created_at/updated_at."""
        self._next_comment += 1
        c = {
            "id": self._next_comment,
            "user": {"login": author},
            "body": body,
            "created_at": STAMP,
            "updated_at": STAMP,
            "issue_url": f"https://api.github.com/repos/{repo}/issues/{number}",
            **fields,
        }
        self.issue(repo, number)["comments"].append(c)
        return c

    def _issue_json(self, repo: str, number: int) -> dict[str, Any]:
        i = self.issue(repo, number)
        return {
            "number": number,
            "repository_url": f"https://api.github.com/repos/{repo}",
            "node_id": f"I_{number}",
            "state": i["state"],
            "state_reason": i["state_reason"],
            "body": i["body"],
            "title": i["title"],
            "labels": [{"name": n} for n in i["labels"]],
            "user": {"login": i["user"]},
        }

    # --- интерфейс клиента -------------------------------------------------

    def check_key(self) -> str | None:
        if self.blocked:
            raise Blocked("лимит")
        return self.bot_login if self.key_ok else None

    def check_installation(self, owner: str) -> bool:
        return True

    def covers(self, repo: str) -> bool | None:
        if self.blocked:
            raise Blocked("лимит")
        self.covers_calls.append(repo)
        return self.cover.get(repo, True)

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: str,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult:
        if self.blocked:
            raise Blocked("лимит")
        query = (body or {}).get("query", "") if graphql else ""
        read = method == "GET" or (graphql and not query.startswith("mutation"))
        if read:
            self.reads.append(path)
        else:
            self.sent.append((method, path, body))
        if (method, path) in self.override:
            return self.override[(method, path)]
        if graphql:
            return self._graphql(query, (body or {}).get("variables") or {})
        if method == "GET":
            return self._get(path)
        return self._mutate(method, path, body or {})

    def _get(self, path: str) -> CallResult:
        if m := ISSUE_RE.match(path):
            return ok(self._issue_json(m.group(1), int(m.group(2))))
        if m := COMMENTS_RE.match(path):
            comments = self.issue(m.group(1), int(m.group(2)))["comments"]
            return ok(_page(comments, path))
        if m := COMMENT_RE.match(path):
            cid = int(m.group(2))
            for i in self.issues.values():
                for c in i["comments"]:
                    if c["id"] == cid:
                        return ok(c)
            return CallResult("failed", Response(404, {}, b"{}"))
        if m := TIMELINE_RE.match(path):
            events = self.issue(m.group(1), int(m.group(2)))["timeline"]
            return ok(_page(events, path))
        if m := PULL_RE.match(path):
            pull = self.pulls.get((m.group(1), int(m.group(2))))
            if pull is None:
                return CallResult("failed", Response(404, {}, b"{}"))
            return ok(pull)
        if m := SEARCH_RE.match(path):
            repo = m.group(1)
            items = [
                {"number": n, **self._issue_json(r, n)}
                for (r, n), i in sorted(self.issues.items())
                if r == repo and "owner-queue" in i["labels"]
            ]
            return ok({"incomplete_results": False, "items": items})
        if m := REPO_RE.match(path):
            return ok({"full_name": m.group(1)})
        raise AssertionError(f"неожиданный GET {path}")

    def pull(self, repo: str, number: int, **fields: Any) -> dict[str, Any]:
        """PR мира (REST-поля + checks/reviews/closing для GraphQL)."""
        data = self.pulls.setdefault(
            (repo, number),
            {
                "state": "open",
                "merged": False,
                "merge_commit_sha": None,
                "draft": False,
                "head": {"sha": "h1"},
                "updated_at": STAMP,
                "checks": [],
                "reviews": [],
                "closing": [],
            },
        )
        data.update(fields)
        return data

    def _pr_graphql(self, variables: dict[str, Any]) -> CallResult:
        key = (f"{variables['o']}/{variables['n']}", int(variables["k"]))
        pr = self.pulls.get(key)
        if pr is None:
            return ok({"data": {"repository": {"pullRequest": None}}})
        checks, reviews = pr.get("checks", []), pr.get("reviews", [])
        closing = [
            {
                "number": int(r.rsplit("#", 1)[1]),
                "repository": {"nameWithOwner": r.rsplit("#", 1)[0]},
            }
            for r in pr.get("closing", [])
        ]
        node = {
            "state": "MERGED" if pr.get("merged") else pr.get("state", "open").upper(),
            "merged": bool(pr.get("merged")),
            "isDraft": bool(pr.get("draft")),
            "headRefOid": (pr.get("head") or {}).get("sha"),
            "updatedAt": pr.get("updated_at"),
            "mergeCommit": {"oid": pr["merge_commit_sha"]}
            if pr.get("merge_commit_sha")
            else None,
            "closingIssuesReferences": {"totalCount": len(closing), "nodes": closing},
            "latestReviews": {"totalCount": len(reviews), "nodes": reviews},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {
                                "contexts": {"totalCount": len(checks), "nodes": checks}
                            }
                        }
                    }
                ]
            },
        }
        return ok({"data": {"repository": {"pullRequest": node}}})

    def _graphql(self, query: str, variables: dict[str, Any]) -> CallResult:
        if "pullRequest" in query:
            return self._pr_graphql(variables)
        node = variables.get("id")
        found = next(
            (i for (_, n), i in sorted(self.issues.items()) if f"I_{n}" == node),
            None,
        )
        if query.startswith("mutation"):
            if found is not None and self.apply:
                found["pinned"] = True
            return ok({"data": {"pinIssue": {"issue": {"id": node}}}})
        pinned = bool(found and found["pinned"])
        return ok({"data": {"node": {"isPinned": pinned} if found else None}})

    def _mutate(self, method: str, path: str, body: dict) -> CallResult:
        if (m := COMMENTS_RE.match(path)) and method == "POST":
            repo, number = m.group(1), int(m.group(2))
            if self.apply:
                c = self.add_comment(repo, number, self.bot_login, body["body"])
            else:
                self._next_comment += 1
                c = {
                    "id": self._next_comment,
                    "body": body["body"],
                    "user": {"login": self.bot_login},
                }
            return ok(c, 201)
        if (m := CREATE_RE.match(path)) and method == "POST":
            self._next_number += 1
            repo, number = m.group(1), self._next_number
            if self.apply:
                self.issue(
                    repo,
                    number,
                    body=body["body"],
                    title=body.get("title", ""),
                    labels=list(body.get("labels", [])),
                    user=self.bot_login,
                )
            return ok({"number": number, "body": body["body"]}, 201)
        if (m := ISSUE_RE.match(path)) and method == "PATCH":
            repo, number = m.group(1), int(m.group(2))
            if self.apply:
                self.issue(repo, number, **body)
            return ok({**self._issue_json(repo, number), **body})
        raise AssertionError(f"неожиданная мутация {method} {path}")
