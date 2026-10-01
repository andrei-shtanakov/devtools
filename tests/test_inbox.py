"""`inbox.render` принимает read-only отображение репо (ковариантно по значению)."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import inbox


def _issue(number: int, body: str, repo: str = "atp-platform") -> dict:
    return {
        "repository": {"name": repo},
        "number": number,
        "title": "t",
        "body": body,
    }


def test_render_accepts_read_only_mapping(tmp_path: Path) -> None:
    todo = tmp_path / "TODO.md"
    todo.write_text("- [ ] x @id:benchmark-2\n", encoding="utf-8")
    repos = MappingProxyType({"atp-platform": todo})
    lines, pending = inbox.render(
        [_issue(1, "slug: benchmark-2\nfrom: arbiter#x\n")], repos
    )
    assert pending == 0 and "принят" in lines[0]


def test_render_repo_without_todo_is_not_pending() -> None:
    lines, pending = inbox.render(
        [_issue(2, "slug: x\nfrom: arbiter#y\n")], {"atp-platform": None}
    )
    assert pending == 0
    assert len(lines) == 1
