"""Мир для планировщиков среза 1: цель фокуса ждёт фоновый пункт."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from conductor.inputs import Inputs
from conductor.snapshot import Result, evaluate
from tests.conductor.fixtures import inputs, record

BOT = "conductor[bot]"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
    "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
}
# заявка a#3 склеена с целью (адрес потребителя), b#4 — с предпосылкой (адрес продюсера)
REQUESTS = [
    record("a", 3, labels=["inbox"], body="slug: goal\nfrom: devtools\n"),
    record("b", 4, labels=["inbox"], body="slug: b\nfrom: devtools\n"),
]


def comment(
    author: str, body: str, created: str, cid: int, updated: str | None = None
) -> dict[str, Any]:
    """Комментарий в форме записи gh_records."""
    return {
        "id": cid,
        "author": author,
        "body": body,
        "created_at": created,
        "updated_at": updated or created,
    }


def world(
    todos: dict[str, str] | None = None,
    records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> tuple[Result, Inputs]:
    """Result ядра и Inputs с полями среза 1 (extra)."""
    inp = inputs(todos or TODOS, records if records is not None else REQUESTS, **extra)
    return evaluate(inp, 0), inp
