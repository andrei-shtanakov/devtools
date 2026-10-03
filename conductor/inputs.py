"""Inputs — всё, что ядро знает о мире; тот же JSON служит replay (§10, §11)."""

from __future__ import annotations

import json
import types
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from conductor.durable import write_atomic
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
    """Записать входы атомарно (UTF-8, стабильный порядок ключей).

    Оборванная запись не оставляет полуфайла под именем replay-входа (#511).
    """
    payload = {"version": INPUTS_VERSION, **asdict(inputs)}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(
        path, json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    )


def _fits(value: Any, hint: Any) -> bool:
    """value соответствует аннотации поля (JSON-формы: str, None, list, dict)."""
    origin, args = get_origin(hint), get_args(hint)
    if hint is Any:
        return True
    if hint is type(None):
        return value is None
    if origin is Literal:
        return value in args
    if origin in (Union, types.UnionType):
        return any(_fits(value, a) for a in args)
    if origin is list:
        return isinstance(value, list) and all(_fits(v, args[0]) for v in value)
    if origin is dict:
        return isinstance(value, dict) and all(
            _fits(k, args[0]) and _fits(v, args[1]) for k, v in value.items()
        )
    return isinstance(value, hint) and not isinstance(value, bool)


def _record[T](cls: type[T], data: Any, where: str) -> T:
    """Экземпляр dataclass из словаря: состав ключей и типы — или ValueError."""
    if not isinstance(data, dict):
        raise ValueError(f"{where}: ожидался объект")
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    required = {
        f.name
        for f in fields(cls)
        if f.default is MISSING and f.default_factory is MISSING
    }
    if extra := sorted(set(data) - known):
        raise ValueError(f"{where}: неизвестные ключи: {', '.join(extra)}")
    if missing := sorted(required - set(data)):
        raise ValueError(f"{where}: нет ключей: {', '.join(missing)}")
    for key, value in data.items():
        if not _fits(value, hints[key]):
            raise ValueError(f"{where}: {key}: тип не {hints[key]}")
    return cls(**data)


def load_inputs(path: Path) -> Inputs:
    """Прочитать входы; чужая версия или форма файла — ValueError с причиной."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.pop("version", None) != INPUTS_VERSION:
        raise ValueError(f"inputs version != {INPUTS_VERSION}: {path}")
    todos = data.get("todos")
    if "todos" in data:
        if not isinstance(todos, list):
            raise ValueError(f"{path}: todos: ожидался список")
        data["todos"] = [
            _record(RepoTodo, t, f"{path}: todos[{i}]") for i, t in enumerate(todos)
        ]
    return _record(Inputs, data, str(path))
