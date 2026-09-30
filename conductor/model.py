"""Неизменяемые типы ядра conductor (спека §3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SourceState = Literal["read", "absent", "not_queried", "error"]
NodeKind = Literal["item", "issue", "pr"]
EdgeType = Literal["depends_on", "accepted_as", "implements", "mentions"]
Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Source:
    """Один источник и честное состояние его чтения (I6)."""

    name: str
    state: SourceState
    detail: str = ""
    sha: str | None = None


@dataclass(frozen=True)
class Node:
    """Пункт TODO, issue или PR."""

    node_id: str
    kind: NodeKind
    repo: str
    title: str
    is_open: bool
    closed_as: str | None = None
    epic: str | None = None
    owner_ref: tuple[tuple[str, Any], ...] | None = None
    trigger: str | None = None
    author: str | None = None
    updated_at: str | None = None
    body: str = ""
    labels: tuple[str, ...] = ()
    url: str = ""
    source_ref: str | None = None

    def owner(self) -> dict[str, Any] | None:
        """owner_ref как словарь (хранится кортежем ради хэшируемости)."""
        return dict(self.owner_ref) if self.owner_ref is not None else None


@dataclass(frozen=True)
class Edge:
    """Типизированное ребро: src ждёт / принят как / реализует / упоминает dst."""

    src: str
    dst: str
    type: EdgeType
    origin: str


@dataclass(frozen=True)
class Finding:
    """Находка с кодом из спеки (GR-*, RM-*, …)."""

    code: str
    severity: Severity
    subject: str
    detail: str = ""


def item_id(repo: str, item: str) -> str:
    """todo://<repo>/<id>."""
    return f"todo://{repo}/{item}"


def issue_id(repo: str, number: int) -> str:
    """<repo>#<N>."""
    return f"{repo}#{number}"


def pr_id(repo: str, number: int) -> str:
    """<repo>!<N>."""
    return f"{repo}!{number}"
