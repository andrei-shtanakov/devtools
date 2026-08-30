#!/usr/bin/env python3
"""Fleet issue console: list, group, select, and launch isolated workers.

The default worker mode is ``plan`` and is read-only.  Press ``x`` before
launching to opt into a workspace-writing Codex run for the selected issues.
Git publishing is deliberately not part of this first boundary.
"""

from __future__ import annotations

import argparse
import curses
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

try:
    from plan_fields import scrape_items
except ImportError:  # pragma: no cover - защита запуска вне uv-окружения
    scrape_items = None  # type: ignore[assignment]

import issue_classify

OUT_ROOT = Path(__file__).resolve().parent / "out"

KINDS = ("document", "research", "code", "fix", "unknown")
ACCEPTANCE = ("accepted", "not-accepted", "unverifiable", "n/a")
ACCEPTANCE_CHAR = {
    "accepted": "A",
    "not-accepted": "N",
    "unverifiable": "U",
    "n/a": "-",
}
GROUP_MODES = ("date", "repo", "author")
DEFAULT_INTERNAL = frozenset({"andrei-shtanakov", "ai-prosto"})
KIND_WORDS = {
    "fix": ("fix", "bug", "broken", "regression", "ошиб", "почин", "дефект"),
    "document": ("doc", "readme", "adr", "documentation", "документ", "описан"),
    "research": ("research", "discovery", "investigat", "исслед", "сравн", "explore"),
    "code": ("feature", "implement", "add ", "новый код", "реализ", "поддержк"),
}


def resolve_internal(flags: list[str]) -> set[str]:
    """--internal ЗАМЕНЯЕТ дефолтный набор целиком (спека), не дополняет."""
    return {x.lower() for x in flags} if flags else set(DEFAULT_INTERNAL)


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    author: str
    created_at: str
    updated_at: str
    url: str
    labels: tuple[str, ...]
    inbox: bool
    accepted: str
    kind: str
    internal: bool
    owner: str

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


def _field(body: str, name: str) -> str | None:
    match = re.search(rf"(?mi)^\s*{re.escape(name)}:\s*(\S+)\s*$", body)
    return match.group(1) if match else None


def classify(title: str, body: str, labels: tuple[str, ...]) -> str:
    """Conservative deterministic classification; ambiguity stays unknown."""
    label_text = " ".join(labels).lower()
    text = f"{label_text} {title} {body[:2000]}".lower()
    scores = {
        kind: sum(word in text for word in words)
        for kind, words in KIND_WORDS.items()
    }
    best = max(scores, key=scores.get)
    winners = [
        kind
        for kind, score in scores.items()
        if score == scores[best] and score > 0
    ]
    return winners[0] if len(winners) == 1 else "unknown"


def discover_repos(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for child in root.iterdir():
        if not (child / ".git").exists():
            continue
        done = subprocess.run(
            ["git", "-C", str(child), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        name = child.name
        if done.returncode == 0:
            match = re.search(r"[:/]([^/]+?)(?:\.git)?$", done.stdout.strip())
            if match:
                name = match.group(1)
        result[name.lower()] = child
    return result


def _acceptance(body: str, repo_path: Path | None) -> str:
    """Acceptance inbox-issue: derived от slug: в теле и чекбоксов TODO.md."""
    if scrape_items is None:
        raise RuntimeError(
            "пакет plan-fields недоступен — запускайте через `uv run --frozen` "
            "(make issues)"
        )
    slug = _field(body, "slug")
    if not slug or repo_path is None:
        return "unverifiable"
    todo = repo_path / "TODO.md"
    if not todo.is_file():
        return "unverifiable"
    items = scrape_items(todo.read_text(errors="ignore"))
    return (
        "accepted"
        if any(slug in item.raw_text for item in items)
        else "not-accepted"
    )


def parse_issues(
    raw: list[dict[str, Any]], root: Path, internal: set[str]
) -> list[Issue]:
    repos = discover_repos(root)
    issues: list[Issue] = []
    for item in raw:
        repo_obj = item.get("repository") or {}
        author_obj = item.get("author") or {}
        repo = str(
            repo_obj.get("name") or repo_obj.get("nameWithOwner") or "?"
        ).split("/")[-1]
        if repo.lower() not in repos:
            continue  # спека: таблица — только флот с локальным клоном
        author = str(author_obj.get("login") or author_obj.get("name") or "?")
        labels = tuple(str(x.get("name", "")) for x in item.get("labels") or [])
        body = str(item.get("body") or "")
        inbox = "inbox" in labels
        name_with_owner = str(repo_obj.get("nameWithOwner") or "")
        owner = (
            name_with_owner.split("/")[0]
            if "/" in name_with_owner
            else "?"
        )
        issues.append(Issue(
            repo=repo,
            number=int(item["number"]),
            title=str(item.get("title") or ""),
            body=body,
            author=author,
            created_at=str(item.get("createdAt") or ""),
            updated_at=str(item.get("updatedAt") or ""),
            url=str(item.get("url") or ""),
            labels=labels,
            inbox=inbox,
            accepted=(
                "n/a"
                if not inbox
                else _acceptance(body, repos.get(repo.lower()))
            ),
            kind=classify(str(item.get("title") or ""), body, labels),
            internal=author.lower() in internal,
            owner=owner,
        ))
    return issues


def fetch_issues(owner: str) -> list[dict[str, Any]]:
    # `gh search issues` excludes pull requests unless `--include-prs` is
    # passed.  Older gh releases do not have a `--type` flag here.
    cmd = [
        "gh",
        "search",
        "issues",
        "--owner",
        owner,
        "--state",
        "open",
        "--limit",
        "1000",
        "--json",
        "repository,number,title,body,author,createdAt,updatedAt,labels,url",
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "gh search issues failed")
    data = json.loads(done.stdout)
    if not isinstance(data, list):
        raise RuntimeError("gh returned a non-list response")
    return data


def sort_issues(issues: list[Issue], mode: str) -> list[Issue]:
    """"date" — новые сверху; "repo"/"author" — тем же вторичным ключом
    внутри группы (стабильная сортировка сохраняет порядок по дате)."""
    by_date = sorted(issues, key=lambda x: x.created_at, reverse=True)
    if mode == "repo":
        return sorted(by_date, key=lambda x: x.repo)
    if mode == "author":
        return sorted(by_date, key=lambda x: x.author.lower())
    return by_date


def group_key(issue: Issue, mode: str) -> str:
    if mode == "repo":
        return issue.repo
    if mode == "author":
        return issue.author
    return ""


def apply_kinds(issues: list[Issue], kinds: dict[str, str]) -> list[Issue]:
    """Заменить kind у issue.key из kinds; остальные issues не трогать."""
    return [
        replace(x, kind=kinds[x.key]) if x.key in kinds else x
        for x in issues
    ]


def launch(issue: Issue, root: Path, mode: str) -> str:
    """Одна tmux-сессия на issue; повторный запуск — подсказка attach."""
    repo_path = discover_repos(root).get(issue.repo.lower())
    if repo_path is None:
        raise RuntimeError(f"local clone for {issue.repo} not found")
    session = re.sub(r"[^a-zA-Z0-9_-]", "-", f"issue-{issue.repo}-{issue.number}")[:80]
    # "=" требует точного совпадения имени сессии; без него tmux матчит по
    # префиксу, и short-номер (issue-devtools-6) ложно "находит" issue-devtools-67.
    target = f"={session}"
    exists = subprocess.run(
        ["tmux", "has-session", "-t", target], capture_output=True, text=True
    )
    if exists.returncode == 0:
        return f"exists: tmux attach -t {target}"
    worker = Path(__file__).with_name("issue_worker.py")
    cmd = [
        sys.executable, str(worker),
        "--repo", issue.repo,
        "--number", str(issue.number),
        "--author", issue.author,
        "--kind", issue.kind,
        "--mode", mode,
        "--url", issue.url,
        "--internal", "yes" if issue.internal else "no",
        "--output-root", str(OUT_ROOT),
    ]
    shell_cmd = " ".join(shlex.quote(part) for part in cmd) + "; exec ${SHELL:-/bin/sh}"
    done = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(repo_path), shell_cmd],
        capture_output=True, text=True,
    )
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "tmux failed")
    return f"started {session}"


def _build_rows(
    ordered: list[Issue], mode_group: str
) -> list[tuple[str, Issue | str]]:
    """Строки экрана: группа-заголовки (кроме mode_group == "date") + issues.

    Заголовок занимает отдельную строку экрана; курсор по заголовкам не ходит.
    """
    rows: list[tuple[str, Issue | str]] = []
    last_key: str | None = None
    for issue in ordered:
        key = group_key(issue, mode_group)
        if mode_group != "date" and key != last_key:
            rows.append(("header", key))
            last_key = key
        rows.append(("issue", issue))
    return rows


def run_tui(stdscr: Any, issues: list[Issue], root: Path) -> None:
    curses.curs_set(0)
    selected: set[str] = set()
    cursor = 0
    mode_group = "date"
    mode = "plan"
    status = "space select · g group · x plan/execute · enter launch · q quit"
    while True:
        ordered = sort_issues(issues, mode_group)
        cursor = min(cursor, max(0, len(ordered) - 1))
        rows = _build_rows(ordered, mode_group)
        issue_rows = [i for i, (kind, _) in enumerate(rows) if kind == "issue"]
        cursor_row = issue_rows[cursor] if issue_rows else 0
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        group_label = (
            f"sort: {mode_group}"
            if mode_group == "date"
            else f"group: {mode_group}"
        )
        header = (
            f"Issues: {len(ordered)}  selected: {len(selected)}  mode: {mode}  "
            f"{group_label}"
        )
        stdscr.addnstr(0, 0, header, w - 1, curses.A_BOLD)
        visible = max(1, h - 2)
        offset = max(0, cursor_row - visible + 1)
        for screen_row, (kind, payload) in enumerate(
            rows[offset:offset + visible], start=1
        ):
            if kind == "header":
                stdscr.addnstr(screen_row, 0, f"── {payload} ──", w - 1)
                continue
            issue = payload
            assert isinstance(issue, Issue)
            mark = "[x]" if issue.key in selected else "[ ]"
            inbox = "I" if issue.inbox else "-"
            acc = ACCEPTANCE_CHAR[issue.accepted]
            author = issue.author + ("*" if issue.internal else "")
            line = (
                f"{mark} {issue.created_at[:10]} {inbox}/{acc} "
                f"{author:<18.18} {issue.kind:<8} {issue.key:<25.25} "
                f"{issue.title}"
            )
            stdscr.addnstr(
                screen_row, 0, line, w - 1,
                curses.A_REVERSE if offset + screen_row - 1 == cursor_row else 0,
            )
        stdscr.addnstr(h - 1, 0, status, w - 1)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return
        if key in (curses.KEY_DOWN, ord("j")) and cursor < len(ordered) - 1:
            cursor += 1
        elif key in (curses.KEY_UP, ord("k")) and cursor > 0:
            cursor -= 1
        elif key == ord("g"):
            next_idx = (GROUP_MODES.index(mode_group) + 1) % len(GROUP_MODES)
            mode_group = GROUP_MODES[next_idx]
        elif key == ord("x"):
            mode = "execute" if mode == "plan" else "plan"
        elif key == ord(" ") and ordered:
            key_name = ordered[cursor].key
            selected.symmetric_difference_update({key_name})
        elif key in (10, 13) and selected:
            launched, errors = [], []
            for issue in ordered:
                if issue.key not in selected:
                    continue
                try:
                    launched.append(launch(issue, root, mode))
                except Exception as exc:  # UI boundary: show other launches too
                    errors.append(f"{issue.key}: {exc}")
            error_suffix = f"; errors: {'; '.join(errors)}" if errors else ""
            status = f"launched: {', '.join(launched) or '-'}" + error_suffix
            selected.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner",
        default=os.environ.get("ISSUE_CONSOLE_OWNER", "andrei-shtanakov"),
    )
    parser.add_argument(
        "--internal",
        action="append",
        default=[],
        help="internal-логин; повтор флага; заменяет дефолтный набор целиком",
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--input", type=Path, help="offline gh JSON fixture")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print normalized data, do not start TUI",
    )
    parser.add_argument(
        "--classify-ai",
        action="store_true",
        help="доклассифицировать unknown через codex (кэш в out/)",
    )
    args = parser.parse_args()
    internal = resolve_internal(args.internal)
    try:
        raw = (
            json.loads(args.input.read_text())
            if args.input
            else fetch_issues(args.owner)
        )
        issues = parse_issues(raw, args.root, internal)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"issue-console: {exc}", file=sys.stderr)
        return 2
    if args.classify_ai:
        kinds = issue_classify.refine(issues, OUT_ROOT / "issue-kind-cache.json")
        issues = apply_kinds(issues, kinds)
    if args.json:
        print(json.dumps([asdict(x) for x in issues], ensure_ascii=False, indent=2))
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "issue-console: TUI requires a terminal (use --json for scripts)",
            file=sys.stderr,
        )
        return 2
    curses.wrapper(run_tui, issues, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
