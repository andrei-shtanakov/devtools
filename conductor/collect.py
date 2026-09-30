"""Сбор Inputs из git и GitHub — единственное место ввода-вывода чтения.

Сбой любого источника становится его состоянием, а не исключением (I6):
ошибки вспомогательных чтений (история, факты путей, политики репо) копятся в
источнике `history`, и граф с ними — partial.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import plan_fields as pf

from conductor.graph import local_refs, referenced_issues
from conductor.inputs import Inputs, RepoTodo
from conductor.manifest import (
    UMBRELLA,
    FleetRepo,
    fleet_repos,
    github_owner,
    manifest_index,
)
from conductor.model import SourceState
from conductor.sources_gh import Runner, collect_gh
from conductor.sources_git import (
    GitError,
    default_ref,
    ever_had,
    fetch,
    last_commit_mentioning,
    line_since,
    path_fact,
    read_file_at_origin,
    read_todo,
)
from conductor.waits import EXISTS_RE

TRIGGER_RE = re.compile(r'@trigger:"([^"]*)"')
HUMAN_MERGE_RE = re.compile(r"(?m)^\s*(?:[-*]\s*)?Мерж:\s*человек\s*$")
AUTHORITY_ROOT_ENV = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "authority-root"
    / "v1"
    / "paths.env"
)


def read_epics(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], SourceState, str, str | None]:
    """epics.toml зонтика с origin/<default>; ошибки реестра → error."""
    umbrella = root / UMBRELLA
    if not (umbrella / ".git").exists():
        return {}, "error", f"нет клона {umbrella}", None
    text, sha, state, detail = read_file_at_origin(umbrella, "epics.toml")
    if state != "read" or text is None:
        return {}, "error", detail, sha
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "epics.toml"
        path.write_text(text, encoding="utf-8")
        registry = pf.load_registry(path)
    errors = [
        d["message"] for d in registry.diagnostics if d.get("severity") == "error"
    ]
    if errors:
        return {}, "error", "; ".join(errors), sha
    return {k: dict(v) for k, v in registry.epics.items()}, "read", "", sha


def read_manifest(
    root: Path, do_fetch: bool
) -> tuple[str | None, tuple[str, str | None], list[str]]:
    """Манифест с origin зонтика; fetch — ДО чтения, иначе состав флота устарел.

    Сбой fetch — деградация (ошибка в источнике history), а не отказ: читается
    последний известный origin, и граф будет partial.
    """
    umbrella = root / UMBRELLA
    errors: list[str] = []
    if (
        do_fetch
        and (umbrella / ".git").exists()
        and (problem := fetch(umbrella)) is not None
    ):
        errors.append(f"{UMBRELLA}: fetch: {problem}")
    text, sha, state, detail = read_file_at_origin(umbrella, "workspace-manifest.toml")
    if state != "read":
        return None, ("origin", sha), [f"манифест не прочитан: {detail}"]
    return text, ("origin", sha), errors


def read_authority_prefixes() -> list[str] | None:
    """Префиксы authority-root из собственного контракта devtools."""
    if not AUTHORITY_ROOT_ENV.is_file():
        return None
    for line in AUTHORITY_ROOT_ENV.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "AUTHORITY_ROOT_PREFIXES":
            return value.split()
    return None


class _History:
    """movement, wait_since, history и ошибки вспомогательных чтений."""

    def __init__(self, root: Path, repos: dict[str, FleetRepo]) -> None:
        self.root, self.repos = root, repos
        self.movement: dict[str, str] = {}
        self.since: dict[str, str] = {}
        self.history: dict[str, str] = {}
        self.errors: list[str] = []
        self._refs: dict[str, str | None] = {}

    def ref(self, repo: str) -> tuple[Path, str | None]:
        repo_dir = self.root / self.repos[repo].git_dir
        if repo not in self._refs:
            self._refs[repo] = default_ref(repo_dir)
        return repo_dir, self._refs[repo]

    def collect(self, snapshot: dict[str, Any], readable: set[str]) -> None:
        nodes = {n["node_id"]: n for n in snapshot["nodes"]}
        for node in nodes.values():
            if node["declared_status"] == "open" and node["repo"] in readable:
                self._movement(node)
        for ref in snapshot["references"]:
            if ref["kind"] == "blocked_by" and ref["provenance"]["repo"] in readable:
                self._wait(ref, nodes)

    def _movement(self, node: dict[str, Any]) -> None:
        repo_dir, ref = self.ref(node["repo"])
        if ref is None:
            return
        try:
            when = last_commit_mentioning(repo_dir, ref, f"@id:{node['id']}")
        except GitError as exc:
            self.errors.append(str(exc))
            return
        if when:
            self.movement[node["node_id"]] = when

    def _wait(self, ref: dict[str, Any], nodes: dict[str, Any]) -> None:
        repo_dir, git_ref = self.ref(ref["provenance"]["repo"])
        raw = ref.get("raw_ref") or ""
        try:
            if git_ref is not None:
                line = ref["provenance"]["line"]
                self.since[f"{ref['source_node_id']}|{raw}"] = line_since(
                    repo_dir, git_ref, line
                )
            if ref.get("resolved_target") is None and raw.startswith("todo://"):
                self._history(raw, nodes)
        except GitError as exc:
            self.errors.append(str(exc))

    def _history(self, raw: str, nodes: dict[str, Any]) -> None:
        repo, _, target = raw.removeprefix("todo://").partition("/")
        if repo not in self.repos or raw in nodes:
            return
        repo_dir, git_ref = self.ref(repo)
        if git_ref is not None and (
            sha := ever_had(repo_dir, git_ref, f"@id:{target}")
        ):
            self.history[raw] = sha


def _trigger_facts(
    root: Path, repos: dict[str, FleetRepo], todos: list[RepoTodo], errors: list[str]
) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for todo in todos:
        for text in TRIGGER_RE.findall(todo.text or ""):
            if not (m := EXISTS_RE.match(text)):
                continue
            if m.group(1) not in repos:
                errors.append(f"exists: неизвестный репо в {text}")
                continue
            fact = path_fact(root / repos[m.group(1)].git_dir, m.group(2))
            if fact["exists"] is None:
                errors.append(f"факт не прочитан: {text}")
            facts[text] = fact
    return facts


def _human_merge(
    root: Path, repos: dict[str, FleetRepo], errors: list[str]
) -> list[str]:
    """Репо с объявленной строкой `Мерж: человек` в CLAUDE.md на origin."""
    found = []
    for repo in repos.values():
        repo_dir = root / repo.git_dir
        if not (repo_dir / ".git").exists():
            continue
        text, _, state, detail = read_file_at_origin(repo_dir, "CLAUDE.md")
        if state == "error":
            errors.append(f"{repo.key}: CLAUDE.md: {detail}")
        elif text is not None and HUMAN_MERGE_RE.search(text):
            found.append(repo.key)
    return sorted(found)


def _roadmap(
    root: Path, roadmap_path: Path | None
) -> tuple[str | None, str | None, SourceState, str]:
    if roadmap_path is not None:
        return roadmap_path.read_text(encoding="utf-8"), None, "read", str(roadmap_path)
    umbrella = root / UMBRELLA
    if not (umbrella / ".git").exists():
        return None, None, "error", "origin"
    text, sha, state, _ = read_file_at_origin(umbrella, "roadmap.toml")
    return text, sha, state, "origin"


def truncated_prs(records: list[dict[str, Any]]) -> list[str]:
    """Усечённые PR — сбой полноты; слабые записи (§3.1) не в счёт."""
    return [
        f"{r['repo']}!{r['number']}: список файлов или ревью усечён"
        for r in records
        if r["is_pr"] and not r.get("complete") and not r.get("weak")
    ]


def collect(
    root: Path,
    manifest_text: str,
    manifest_origin: tuple[str, str | None],
    roadmap_path: Path | None,
    do_fetch: bool,
    runner: Runner,
    host: str,
    now: str,
    prior_errors: list[str] | None = None,
) -> Inputs:
    """Прочитать флот; сбои — состояния источников, не исключения."""
    repos = {r.key: r for r in fleet_repos(manifest_text)}
    todos = [read_todo(r, root, do_fetch) for r in repos.values()]
    names = {r.github_name: r.key for r in repos.values()}
    norm = {**{k: k for k in repos}, **names}
    owner = github_owner(manifest_text)
    gh = collect_gh(
        owner,
        names,
        lambda recs: referenced_issues(recs, todos, norm),
        runner,
        weak_refs=lambda recs: local_refs(recs, norm),
    )
    rm_text, rm_sha, rm_state, rm_source = _roadmap(root, roadmap_path)
    epics, epics_state, epics_detail, epics_sha = read_epics(root)
    snapshot = pf.parse_fleet(
        [
            pf.RepoInput(
                t.repo,
                todo_text=t.text or "",
                commit=t.sha,
                available=t.state in ("read", "absent"),
            )
            for t in todos
        ],
        manifest_index(manifest_text),
    )
    hist = _History(root, repos)
    hist.errors += prior_errors or []
    hist.collect(snapshot, {t.repo for t in todos if t.state == "read"})
    facts = _trigger_facts(root, repos, todos, hist.errors)
    human = _human_merge(root, repos, hist.errors)
    prefixes = read_authority_prefixes()
    if prefixes is None:
        hist.errors.append(f"нет {AUTHORITY_ROOT_ENV}")
    hist.errors += truncated_prs(gh.records)
    return Inputs(
        captured_at=now,
        host=host,
        owner=owner,
        manifest_text=manifest_text,
        todos=todos,
        gh_records=gh.records,
        gh_state=gh.state,
        gh_detail=gh.detail,
        roadmap_text=rm_text,
        roadmap_state=rm_state,
        roadmap_source=rm_source,
        roadmap_sha=rm_sha,
        epics=epics,
        epics_state=epics_state,
        epics_detail=epics_detail,
        repo_names=names,
        movement=hist.movement,
        wait_since=hist.since,
        history=hist.history,
        trigger_facts=facts,
        epics_sha=epics_sha,
        aux_state="error" if hist.errors else "read",
        aux_detail="; ".join(hist.errors[:5]),
        human_merge_repos=human,
        authority_prefixes=prefixes or [],
        manifest_source=manifest_origin[0],
        manifest_sha=manifest_origin[1],
    )
