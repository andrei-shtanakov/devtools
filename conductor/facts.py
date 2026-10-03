"""Идентичность фактов и периодов по first-parent истории (спека среза 1, §7.1, §7.3).

Факт выполнения пункта — последний коммит first-parent истории, в котором
пункт стал `[x]`; период ожидания — последний коммит, в котором ребро
появилось. Правка заголовка их не меняет. Требует git ≥ 2.31.

Пункт и его теги читаются ТЕМ ЖЕ каноническим парсером `plan-fields`, что
строит узлы ядра (`item_state`): упоминание `@id` в бэктиках или прозе — не
пункт, ни в истории, ни в свежей перепроверке перед записью.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import plan_fields as pf

from conductor.manifest import manifest_index
from conductor.sources_git import GitError, git, git_lines

TODO = "TODO.md"


@dataclass(frozen=True)
class ItemState:
    """Пункт TODO по каноническому разбору: выполнен, рёбра, триггер."""

    done: bool
    blocked_by: frozenset[str]
    trigger: str | None


def _checked(repo_dir: Path, *args: str) -> str:
    code, out, err = git(repo_dir, *args)
    if code != 0:
        raise GitError(f"{repo_dir.name}: git {args[0]}: {err.strip() or code}")
    return out


@cache
def _index(repo: str) -> Any:
    """Индекс манифеста из одного репо: разбору пунктов этого TODO хватает."""
    return manifest_index(
        f'[cores.{repo}]\nrepo_url = "git@github.com:x/{repo}.git"\n'
        f'git_dir = "{repo}"\n'
    )


class AmbiguousItem(Exception):
    """`@id` пункта встречается в TODO больше одного раза (PF-ID-DUPLICATE):
    состояние пункта не доказано — ни выполненным, ни ребром его не считать."""


AMBIGUOUS = object()


@cache
def _items(text: str, repo: str) -> dict[str, Any]:
    """Все пункты версии TODO каноническим разбором: node_id → ItemState или
    AMBIGUOUS (дубликат id). Кэш по тексту: одна версия разбирается один раз."""
    snapshot = pf.parse_fleet(
        [pf.RepoInput(repo, todo_text=text, commit="fresh", available=True)],
        _index(repo),
    )
    refs: dict[str, set[str]] = {}
    for r in snapshot["references"]:
        if r["kind"] == "blocked_by":
            refs.setdefault(r["source_node_id"], set()).add(r.get("raw_ref") or "")
    out: dict[str, Any] = {}
    for n in snapshot["nodes"]:
        nid = n["node_id"]
        state = ItemState(
            n["declared_status"] != "open",
            frozenset(refs.get(nid, ())),
            n.get("trigger"),
        )
        out[nid] = AMBIGUOUS if nid in out else state
    return out


def item_state(text: str | None, repo: str, item: str) -> ItemState | None:
    """Пункт `todo://<repo>/<item>` в тексте TODO.md — каноническим разбором
    `plan_fields` (как узлы ядра); нет такого пункта — None; дубликат id —
    AmbiguousItem (ядро взяло бы последний узел молча — писать по нему нельзя)."""
    state = _items(text or "", repo).get(f"todo://{repo}/{item}")
    if state is AMBIGUOUS:
        raise AmbiguousItem(f"todo://{repo}/{item}: @id встречается дважды")
    return state


def _state(text: str | None, repo: str, item: str) -> ItemState | None:
    """Для истории: неоднозначный пункт — как отсутствующий (то же правило,
    что у свежей проверки: ни выполненным, ни ребром не считается)."""
    try:
        return item_state(text, repo, item)
    except AmbiguousItem:
        return None


def _file_at(repo_dir: Path, rev: str) -> str | None:
    """TODO.md в ревизии; ревизии нет (корневой коммит) или файла нет — None."""
    if git(repo_dir, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}")[0] != 0:
        return None
    code, out, _ = git(repo_dir, "show", f"{rev}:{TODO}")
    return git_lines(out) if code == 0 else None


@cache
def _blob(repo_dir: Path, sha: str) -> str | None:
    """TODO.md в коммите sha (неизменяем — кэш по SHA); файла нет — None.

    Переносы нормализуются так же, как у ядра (`read_todo`, #511): иначе
    один коммит разбирался бы двумя разными текстами."""
    code, out, _ = git(repo_dir, "show", f"{sha}:{TODO}")
    return git_lines(out) if code == 0 else None


@cache
def _log(repo_dir: Path, head: str) -> tuple[tuple[str, str], ...]:
    """(sha, дата) всех first-parent коммитов, менявших TODO.md, от head
    (SHA — неизменяемый ключ кэша), новые первыми. Без отбора по тексту
    `@id`: отбор не полон относительно грамматики парсера (кавычки и т. п.)."""
    out = _checked(
        repo_dir, "log", "--first-parent", "--format=%H %cI", head, "--", TODO
    )
    return tuple(
        (sha, date) for sha, date in (ln.split(" ", 1) for ln in out.splitlines() if ln)
    )


def _transitions(
    repo_dir: Path, ref: str, holds: Callable[[str | None], bool]
) -> tuple[str, str] | None:
    """Последний коммит, в котором holds(текст TODO.md) стало истинным (в его
    первом родителе — ложно): (sha, дата) или None. Перебираются ВСЕ версии
    файла, но лениво — до найденного перехода; ref фиксируется в SHA, журнал и
    тексты версий кэшируются по SHA (повторный запрос к той же голове не
    повторяет чтений git)."""
    head = _checked(repo_dir, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    rows = _log(repo_dir, head)
    for k, (sha, date) in enumerate(rows):
        if k + 1 < len(rows):
            before = _blob(repo_dir, rows[k + 1][0])  # TODO.md первого родителя
        else:  # самая старая версия: в родителе файла не было (или корень)
            before = _file_at(repo_dir, f"{sha}^")
        if holds(_blob(repo_dir, sha)) and not holds(before):
            return sha, date
    return None


def first_done_commit(repo_dir: Path, ref: str, repo: str, item: str) -> str | None:
    """Последний коммит, где пункт стал [x]; None — переход не найден."""

    def done(text: str | None) -> bool:
        state = _state(text, repo, item)
        return state is not None and state.done

    found = _transitions(repo_dir, ref, done)
    return found[0] if found else None


def edge_period(
    repo_dir: Path, ref: str, repo: str, item: str, raw: str
) -> tuple[str, str] | None:
    """Период ожидания: последнее появление ребра (sha, дата коммита)."""

    def edge(text: str | None) -> bool:
        state = _state(text, repo, item)
        return state is not None and raw in state.blocked_by

    return _transitions(repo_dir, ref, edge)


def path_added(repo_dir: Path, ref: str, path: str) -> str | None:
    """Последний коммит first-parent истории, добавивший путь; нет — None."""
    out = _checked(
        repo_dir,
        "log",
        "--first-parent",
        "--diff-filter=A",
        "--format=%H",
        "-1",
        ref,
        "--",
        path,
    )
    return out.strip() or None
