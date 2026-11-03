"""`act_history`: узкий модуль чтения истории доставок и реэкспорт в `act_status`."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

pytest.importorskip("steward", reason="steward — без группы governance")

from governance import act_history, act_status  # noqa: E402
from governance.run_state import RunState  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MOVED = ("load_history", "_event", "_request_event", "pr_for_merge", "_pr_head")


def _governance_imports(module: str) -> set[str]:
    """Все модули `governance.*` из импортов модуля — и отложенных, и под
    `TYPE_CHECKING` (обход всего дерева AST)."""
    tree = ast.parse((ROOT / "governance" / f"{module}.py").read_text("utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "governance" and len(parts) > 1:
                if (ROOT / "governance" / f"{parts[1]}.py").is_file():
                    found.add(parts[1])
    return found


def _closure(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        module = todo.pop()
        if module not in seen:
            seen.add(module)
            todo.extend(_governance_imports(module))
    return seen


def test_act_history_closure_is_narrow() -> None:
    assert _closure("act_history") == {
        "act_history",
        "act_envelope",
        "act_request",
        "act_time",
        "act_git_rows",
    }


def test_act_status_reexports_history() -> None:
    for name in MOVED:
        assert getattr(act_status, name) is getattr(act_history, name), name


def test_run_state_satisfies_history_scope() -> None:
    fields = {f.name for f in dataclasses.fields(RunState)}
    assert {"ws_id", "repo_slug", "target_dir"} <= fields
    assert set(act_history.HistoryScope.__annotations__) == {
        "ws_id",
        "repo_slug",
        "target_dir",
    }
