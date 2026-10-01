"""Мутации App: закрытый белый список и фактическая цель (спека среза 1, §4.5).

Цель — существующий объект: свежее чтение без перенаправлений, репо и номер
совпадают с планом; иначе TARGET-MOVED (снято). Цель — создание в репо:
каноническое имя репо совпадает. Endpoint строится только из сверенных
репо и номера. verify (О §5.0 шаг 3) — НЕЗАВИСИМОЕ чтение цели после
мутации: ответ самой мутации эффекта не доказывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from conductor.gh_app import CallResult
from conductor.http import Response

Op = Literal["comment", "create", "body", "pin", "close"]
KLASS: dict[str, str] = {
    "comment": "create",
    "create": "create",
    "body": "update",
    "close": "update",
    "pin": "update",
}
PIN_QUERY = "mutation($id:ID!){pinIssue(input:{issueId:$id}){issue{id}}}"
PINNED_QUERY = "query($id:ID!){node(id:$id){... on Issue{isPinned}}}"


class Caller(Protocol):
    """То, что нужно от клиента App для проверки цели."""

    def call(
        self,
        klass: str,
        method: str,
        path: str,
        *,
        auth: Any,
        body: dict | None = None,
        graphql: bool = False,
    ) -> CallResult: ...


@dataclass(frozen=True)
class Mutation:
    """Одна мутация: операция, репо owner/name, номер (None — создание)."""

    op: Op
    repo: str
    number: int | None = None
    text: str = ""
    title: str = ""
    labels: tuple[str, ...] = ()

    def target(self) -> str:
        """Каноническая цель: repo#N или repo для создания."""
        return self.repo if self.number is None else f"{self.repo}#{self.number}"


@dataclass(frozen=True)
class TargetCheck:
    """Итог сверки цели; node_id нужен для pinIssue."""

    ok: bool
    reason: str = ""
    node_id: str | None = None


def _data(result: CallResult) -> dict[str, Any] | None:
    if result.outcome != "ok" or result.response is None:
        return None
    data = result.response.json()
    return data if isinstance(data, dict) else None


def check_target(client: Caller, m: Mutation) -> TargetCheck:
    """Шаг 8 §5.1: фактическая идентичность цели перед мутацией."""
    path = (
        f"/repos/{m.repo}" if m.op == "create" else f"/repos/{m.repo}/issues/{m.number}"
    )
    result = client.call("service", "GET", path, auth="token")
    if result.outcome == "moved":
        return TargetCheck(False, "TARGET-MOVED")
    data = _data(result)
    if data is None:
        return TargetCheck(False, "TARGET-UNREAD")
    if m.op == "create":
        # имена репо на GitHub регистронезависимы — как у ветки issue ниже
        same = str(data.get("full_name", "")).lower() == m.repo.lower()
        return TargetCheck(same, "" if same else "TARGET-MOVED")
    url = str(data.get("repository_url", "")).lower()
    if data.get("number") != m.number or not url.endswith(f"/repos/{m.repo.lower()}"):
        return TargetCheck(False, "TARGET-MOVED")
    return TargetCheck(True, node_id=data.get("node_id"))


def request_of(m: Mutation, node_id: str | None) -> tuple[str, str, dict, bool]:
    """(метод, путь, тело, graphql) — единственный источник endpoint'ов."""
    base = f"/repos/{m.repo}/issues"
    if m.op == "comment":
        return "POST", f"{base}/{m.number}/comments", {"body": m.text}, False
    if m.op == "create":
        payload = {"title": m.title, "body": m.text, "labels": list(m.labels)}
        return "POST", base, payload, False
    if m.op == "body":
        return "PATCH", f"{base}/{m.number}", {"body": m.text}, False
    if m.op == "close":
        payload = {"state": "closed", "state_reason": "completed"}
        return "PATCH", f"{base}/{m.number}", payload, False
    return "POST", "/graphql", {"query": PIN_QUERY, "variables": {"id": node_id}}, True


def _read(client: Caller, path: str) -> dict[str, Any] | None:
    return _data(client.call("service", "GET", path, auth="token"))


def _same_issue(data: dict[str, Any], repo: str, number: int) -> bool:
    url = str(data.get("repository_url", "")).lower()
    return data.get("number") == number and url.endswith(f"/repos/{repo.lower()}")


def _pinned(client: Caller, node_id: str | None) -> bool:
    result = client.call(
        "service",
        "POST",
        "/graphql",
        auth="token",
        body={"query": PINNED_QUERY, "variables": {"id": node_id}},
        graphql=True,
    )
    node = ((_data(result) or {}).get("data") or {}).get("node") or {}
    return node.get("isPinned") is True


def verify(
    client: Caller,
    m: Mutation,
    resp: Response | None,
    bot_login: str,
    node_id: str | None = None,
) -> str | None:
    """verify О §5.0 шаг 3: перечитать цель и сверить с ожидаемым.

    None — эффект подтверждён чтением; иначе причина неопределённости
    (ответ мутации не разобран, контрольное чтение не удалось или не совпало).
    """
    data = resp.json() if resp is not None else None
    if not isinstance(data, dict):
        return "ответ мутации не разобран"
    if m.op == "pin":
        return None if _pinned(client, node_id) else "закрепление не подтверждено"
    if m.op == "comment":
        cid = data.get("id")
        if not isinstance(cid, int):
            return "в ответе нет id комментария"
        c = _read(client, f"/repos/{m.repo}/issues/comments/{cid}")
        url = str((c or {}).get("issue_url", "")).lower()
        same = c is not None and url.endswith(
            f"/repos/{m.repo.lower()}/issues/{m.number}"
        )
        author = ((c or {}).get("user") or {}).get("login")
        if not same or (c or {}).get("body") != m.text or author != bot_login:
            return "комментарий не подтверждён чтением"
        return None
    number = data.get("number") if m.op == "create" else m.number
    if not isinstance(number, int):
        return "в ответе нет номера"
    issue = _read(client, f"/repos/{m.repo}/issues/{number}")
    if issue is None or not _same_issue(issue, m.repo, number):
        return "цель не прочитана после записи"
    if m.op == "close":
        done = issue.get("state") == "closed"
        if not done or issue.get("state_reason") != "completed":
            return "закрытие не подтверждено чтением"
        return None
    if issue.get("body") != m.text:
        return "тело не подтверждено чтением"
    if m.op == "create":
        labels = {lab.get("name") for lab in issue.get("labels") or []}
        author = (issue.get("user") or {}).get("login")
        if author != bot_login or not set(m.labels) <= labels:
            return "созданная очередь не подтверждена чтением"
    return None
