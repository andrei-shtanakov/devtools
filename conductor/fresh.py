"""Свежие чтения: шаг 8 §5.1 и поиск эффектов §5.3 (спека среза 1).

App-чтения идут через клиента App (класс service); git — `fetch` и
`origin/<default>` учётными данными чтения хоста. Ошибка чтения при
повторной проверке — «недействительно» (шаг снимается, а не пишется); при
поиске эффекта — «не найден» (действует задержка §5.3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from conductor.gh_write import PINNED_QUERY
from conductor.markers import classify, parse_body, render
from conductor.sources_gh import ci_state, red_checks

PAGES = 50
PR_FACTS = (
    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
    "{pullRequest(number:$k){state merged isDraft headRefOid updatedAt"
    " mergeCommit{oid}"
    " closingIssuesReferences(first:50){totalCount"
    " nodes{number repository{nameWithOwner}}}"
    " latestReviews(first:50){totalCount nodes{state commit{oid}}}"
    " commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100)"
    "{totalCount nodes{__typename ... on CheckRun{name conclusion status detailsUrl}"
    " ... on StatusContext{context state targetUrl}}}}}}}}}}"
)
GitRepo = Callable[[str], tuple[Path, str] | None]


def valid(m: Any) -> str | None:
    """revalidate без свежего чтения (тесты планировщиков без клиента)."""
    return None


def _comment(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "author": (c.get("user") or {}).get("login", ""),
        "body": c.get("body") or "",
        "created_at": c.get("created_at", ""),
        "updated_at": c.get("updated_at", ""),
    }


class FreshReader:
    """GET через клиента App; git-чтения — через git_repo(ключ репо).

    git_repo делает fetch и возвращает (каталог клона, ref по умолчанию);
    None — чтение не удалось. Без git_repo git-проверки недействительны.
    """

    def __init__(self, client: Any, git_repo: GitRepo | None = None) -> None:
        self._client = client
        self._git_repo = git_repo

    def _get(self, path: str) -> Any:
        result = self._client.call("service", "GET", path, auth="token")
        if result.outcome != "ok" or result.response is None:
            return None
        return result.response.json()

    def _pages(self, path: str) -> list[dict[str, Any]] | None:
        out: list[dict[str, Any]] = []
        for page in range(1, PAGES + 1):
            data = self._get(f"{path}?per_page=100&page={page}")
            if not isinstance(data, list):
                return None
            out += data
            if len(data) < 100:
                return out
        return None

    def comments(self, repo: str, number: int) -> list[dict[str, Any]] | None:
        """Все комментарии треда; сбой или усечение — None."""
        raw = self._pages(f"/repos/{repo}/issues/{number}/comments")
        return None if raw is None else [_comment(c) for c in raw]

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        """Issue или PR как issue; сбой — None."""
        data = self._get(f"/repos/{repo}/issues/{number}")
        return data if isinstance(data, dict) else None

    def pull(self, repo: str, number: int) -> dict[str, Any] | None:
        """PR (REST pulls); сбой — None."""
        data = self._get(f"/repos/{repo}/pulls/{number}")
        return data if isinstance(data, dict) else None

    def pr_facts(self, repo: str, number: int) -> dict[str, Any] | None:
        """Свежие основания потребности PR (GraphQL-чтение): состояние, head,
        проверки head, одобрение head, закрывающие ссылки. Усечение или
        сбой — None (основание не прочитано — не писать)."""
        owner, _, name = repo.partition("/")
        result = self._client.call(
            "service",
            "POST",
            "/graphql",
            auth="token",
            body={
                "query": PR_FACTS,
                "variables": {"o": owner, "n": name, "k": number},
            },
            graphql=True,
        )
        data = result.response.json() if result.response is not None else None
        try:
            pr = data["data"]["repository"]["pullRequest"]  # type: ignore[index]
            return _pr_facts(pr) if result.outcome == "ok" else None
        except (KeyError, TypeError, IndexError):
            return None

    def last_event(self, repo: str, number: int, kind: str) -> str | None:
        """node_id последнего события kind timeline; нет — "", сбой — None."""
        events = self._pages(f"/repos/{repo}/issues/{number}/timeline")
        if events is None:
            return None
        found = [e.get("node_id") or "" for e in events if e.get("event") == kind]
        return found[-1] if found else ""

    def pinned(self, repo: str, number: int) -> bool | None:
        """Закреплён ли issue (GraphQL-чтение); сбой — None."""
        issue = self.issue(repo, number)
        if issue is None:
            return None
        result = self._client.call(
            "service",
            "POST",
            "/graphql",
            auth="token",
            body={"query": PINNED_QUERY, "variables": {"id": issue.get("node_id")}},
            graphql=True,
        )
        data = result.response.json() if result.response is not None else None
        if result.outcome != "ok" or not isinstance(data, dict):
            return None
        node = (data.get("data") or {}).get("node")
        return node.get("isPinned") is True if isinstance(node, dict) else None

    def marker_found(
        self, repo: str, number: int, rendered: str, bot: str
    ) -> bool | None:
        """Есть ли действительный маркер события rendered; сбой — None."""
        comments = self.comments(repo, number)
        if comments is None:
            return None
        return any(
            kind == "event" and marker is not None and render(marker) == rendered
            for kind, marker in (classify(c, bot) for c in comments)
        )

    def marker_absent(self, repo: str, number: int, rendered: str, bot: str) -> bool:
        """Действительного маркера в треде нет (сбой чтения — False)."""
        return self.marker_found(repo, number, rendered, bot) is False

    def queue_found(self, repo: str, bot: str) -> bool | None:
        """Есть ли очередь бота (открытая или закрытая); сбой — None."""
        data = self._get(f"/search/issues?q=repo:{repo}+label:owner-queue+is:issue")
        if not isinstance(data, dict) or data.get("incomplete_results"):
            return None
        return any(
            (i.get("user") or {}).get("login") == bot for i in data.get("items", [])
        )

    def git(self, repo_key: str) -> tuple[Path, str] | None:
        """Свежий клон репо флота (после fetch); нет или сбой — None."""
        return self._git_repo(repo_key) if self._git_repo is not None else None


def _pr_facts(pr: dict[str, Any]) -> dict[str, Any] | None:
    refs, reviews = pr["closingIssuesReferences"], pr["latestReviews"]
    nodes = pr["commits"]["nodes"]
    rollup = (nodes[-1]["commit"]["statusCheckRollup"] or {}) if nodes else {}
    contexts = rollup.get("contexts") or {"totalCount": 0, "nodes": []}
    if (
        refs["totalCount"] > len(refs["nodes"])
        or reviews["totalCount"] > len(reviews["nodes"])
        or contexts["totalCount"] > len(contexts["nodes"])
    ):
        return None  # усечено — основание не доказано
    head = pr["headRefOid"]
    approved = any(
        r["state"] == "APPROVED" and (r.get("commit") or {}).get("oid") == head
        for r in reviews["nodes"]
    ) and not any(r["state"] == "CHANGES_REQUESTED" for r in reviews["nodes"])
    return {
        "open": pr["state"] == "OPEN",
        "merged": bool(pr["merged"]),
        "merge_sha": (pr.get("mergeCommit") or {}).get("oid"),
        "draft": bool(pr["isDraft"]),
        "head": head,
        "updated_at": pr["updatedAt"],
        "ci": ci_state(contexts["nodes"]),
        "red": red_checks(contexts["nodes"]),
        "approved": approved,
        "closing": {
            f"{r['repository']['nameWithOwner']}#{r['number']}" for r in refs["nodes"]
        },
    }


def pr_need(facts: dict[str, Any]) -> str:
    """Потребность PR по свежим основаниям — ветви policy._pr_need, от
    которых зависит текст пинка (мерж — одна ветвь: текст один)."""
    if facts["ci"] == "red":
        return "fix_pr"
    if not facts["approved"]:
        return "review"
    if facts["ci"] != "green":
        return "wait_ci"
    return "merge"


def comment_check(fresh: FreshReader, m: Any, bot: str) -> str | None:
    """Комментарий: маркера этого события в треде ещё нет (свежее чтение)."""
    marker = parse_body(m.text)
    if marker is None:
        return "в тексте нет маркера"
    found = fresh.marker_found(m.repo, m.number, render(marker), bot)
    if found is None:
        return "тред не прочитан"
    return "маркер уже есть" if found else None


def marker_check(fresh: FreshReader, m: Any, rendered: str, bot: str) -> str | None:
    """Шаг, опирающийся на подтверждение: действительный маркер события App
    с полным ключом rendered в треде есть (свежее чтение, без правок)."""
    found = fresh.marker_found(m.repo, m.number, rendered, bot)
    if found is None:
        return "тред не прочитан"
    return None if found else "подтверждения в треде нет"


def open_check(fresh: FreshReader, repo: str, number: int) -> str | None:
    """Субъект всё ещё открыт (свежее чтение)."""
    data = fresh.issue(repo, number)
    if data is None:
        return "субъект не прочитан"
    return None if data.get("state") == "open" else "субъект закрыт"


def first_reason(*checks: Callable[[], str | None]) -> str | None:
    """Первая причина снятия среди проверок (ленивых: чтения по порядку)."""
    for check in checks:
        if (reason := check()) is not None:
            return reason
    return None
