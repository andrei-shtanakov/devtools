"""Inputs — всё, что ядро знает о мире; тот же JSON служит replay (§10, §11)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from conductor.model import SourceState

INPUTS_VERSION = 1


@dataclass
class RepoTodo:
    """TODO.md одного репо с origin/<default>."""

    repo: str
    text: str | None
    sha: str | None
    state: SourceState
    detail: str = ""


@dataclass
class Inputs:
    """Входы прогона; ядро не делает ввода-вывода сверх этого."""

    captured_at: str
    host: str
    owner: str
    manifest_text: str
    todos: list[RepoTodo]
    gh_records: list[dict[str, Any]]
    gh_state: SourceState
    gh_detail: str
    roadmap_text: str | None
    roadmap_state: SourceState
    roadmap_source: str
    roadmap_sha: str | None
    epics: dict[str, dict[str, Any]]
    epics_state: SourceState
    epics_detail: str
    repo_names: dict[str, str] = field(default_factory=dict)
    movement: dict[str, str] = field(default_factory=dict)
    wait_since: dict[str, str] = field(default_factory=dict)
    history: dict[str, str] = field(default_factory=dict)
    trigger_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    epics_sha: str | None = None
    aux_state: SourceState = "read"
    aux_detail: str = ""
    human_merge_repos: list[str] = field(default_factory=list)
    authority_prefixes: list[str] = field(default_factory=list)
    manifest_source: str = "file"
    manifest_sha: str | None = None
    done_facts: dict[str, str] = field(default_factory=dict)
    edge_periods: dict[str, list[str]] = field(default_factory=dict)
    path_added: dict[str, str] = field(default_factory=dict)
    issue_extras: dict[str, dict[str, Any]] = field(default_factory=dict)
    closed_events: dict[str, str] = field(default_factory=dict)
    queue_records: list[dict[str, Any]] = field(default_factory=list)
    umbrella_full: str = ""


def save_inputs(inputs: Inputs, path: Path) -> None:
    """Записать входы (UTF-8, стабильный порядок ключей)."""
    payload = {"version": INPUTS_VERSION, **asdict(inputs)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def load_inputs(path: Path) -> Inputs:
    """Прочитать входы; чужая версия формата — ValueError."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.pop("version", None) != INPUTS_VERSION:
        raise ValueError(f"inputs version != {INPUTS_VERSION}: {path}")
    data["todos"] = [RepoTodo(**t) for t in data["todos"]]
    return Inputs(**data)
