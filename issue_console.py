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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


KINDS = ("document", "research", "code", "fix", "unknown")
KIND_WORDS = {
    "fix": ("fix", "bug", "broken", "regression", "ошиб", "почин", "дефект"),
    "document": ("doc", "readme", "adr", "documentation", "документ", "описан"),
    "research": ("research", "discovery", "investigat", "исслед", "сравн", "explore"),
    "code": ("feature", "implement", "add ", "новый код", "реализ", "поддержк"),
}


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    author: str
    created_at: str
    url: str
    labels: tuple[str, ...]
    inbox: bool
    accepted: bool | None
    kind: str
    internal: bool

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
    scores = {kind: sum(word in text for word in words) for kind, words in KIND_WORDS.items()}
    best = max(scores, key=scores.get)
    winners = [kind for kind, score in scores.items() if score == scores[best] and score > 0]
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


def _accepted(body: str, repo_path: Path | None) -> bool | None:
    slug = _field(body, "slug")
    if not slug or repo_path is None:
        return None
    todo = repo_path / "TODO.md"
    if not todo.is_file():
        return None
    return any(slug in line for line in todo.read_text(errors="ignore").splitlines()
               if re.match(r"^\s*[-*]\s+\[[ xX]\]", line))


def parse_issues(raw: list[dict[str, Any]], root: Path, internal: set[str]) -> list[Issue]:
    repos = discover_repos(root)
    issues: list[Issue] = []
    for item in raw:
        repo_obj = item.get("repository") or {}
        author_obj = item.get("author") or {}
        repo = str(repo_obj.get("name") or repo_obj.get("nameWithOwner") or "?").split("/")[-1]
        author = str(author_obj.get("login") or author_obj.get("name") or "?")
        labels = tuple(str(x.get("name", "")) for x in item.get("labels") or [])
        body = str(item.get("body") or "")
        issues.append(Issue(
            repo=repo, number=int(item["number"]), title=str(item.get("title") or ""),
            body=body, author=author, created_at=str(item.get("createdAt") or ""),
            url=str(item.get("url") or ""), labels=labels, inbox="inbox" in labels,
            accepted=_accepted(body, repos.get(repo.lower())),
            kind=classify(str(item.get("title") or ""), body, labels),
            internal=author.lower() in internal,
        ))
    return issues


def fetch_issues(owner: str) -> list[dict[str, Any]]:
    # `gh search issues` excludes pull requests unless `--include-prs` is
    # passed.  Older gh releases do not have a `--type` flag here.
    cmd = ["gh", "search", "issues", "--owner", owner, "--state", "open",
           "--limit", "1000", "--json",
           "repository,number,title,body,author,createdAt,labels,url"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "gh search issues failed")
    data = json.loads(done.stdout)
    if not isinstance(data, list):
        raise RuntimeError("gh returned a non-list response")
    return data


def launch(issue: Issue, root: Path, mode: str) -> str:
    repo_path = discover_repos(root).get(issue.repo.lower())
    if repo_path is None:
        raise RuntimeError(f"local clone for {issue.repo} not found")
    session = re.sub(r"[^a-zA-Z0-9_-]", "-", f"issue-{issue.repo}-{issue.number}")[:80]
    worker = Path(__file__).with_name("issue_worker.py")
    cmd = [sys.executable, str(worker), "--repo", issue.repo, "--number", str(issue.number),
           "--author", issue.author, "--kind", issue.kind, "--mode", mode, "--url", issue.url,
           "--internal", "yes" if issue.internal else "no"]
    shell_cmd = " ".join(shlex.quote(part) for part in cmd) + "; exec ${SHELL:-/bin/sh}"
    done = subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", str(repo_path), shell_cmd],
                          capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "tmux failed")
    return session


def run_tui(stdscr: Any, issues: list[Issue], root: Path) -> None:
    curses.curs_set(0)
    selected: set[str] = set()
    cursor = 0
    grouped = False
    mode = "plan"
    status = "space select · g group · x plan/execute · enter launch · q quit"
    while True:
        ordered = sorted(issues, key=(lambda x: (x.author.lower(), x.repo, x.number)) if grouped
                         else (lambda x: (x.repo, x.number)))
        cursor = min(cursor, max(0, len(ordered) - 1))
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, f"Issues: {len(ordered)}  selected: {len(selected)}  mode: {mode}  group: {'author' if grouped else 'repo'}", w - 1, curses.A_BOLD)
        visible = max(1, h - 2)
        offset = max(0, cursor - visible + 1)
        for row, issue in enumerate(ordered[offset:offset + visible], start=1):
            mark = "[x]" if issue.key in selected else "[ ]"
            inbox = "I" if issue.inbox else "-"
            accepted = "Y" if issue.accepted is True else ("N" if issue.accepted is False else "?")
            line = f"{mark} {issue.created_at[:10]} {inbox}/{accepted} {issue.author:<18.18} {issue.kind:<8} {issue.key:<25.25} {issue.title}"
            stdscr.addnstr(row, 0, line, w - 1,
                           curses.A_REVERSE if offset + row - 1 == cursor else 0)
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
            grouped = not grouped
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
            status = f"launched: {', '.join(launched) or '-'}" + (f"; errors: {'; '.join(errors)}" if errors else "")
            selected.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default=os.environ.get("ISSUE_CONSOLE_OWNER", "andrei-shtanakov"))
    parser.add_argument("--internal", action="append", default=[])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--input", type=Path, help="offline gh JSON fixture")
    parser.add_argument("--json", action="store_true", help="print normalized data, do not start TUI")
    args = parser.parse_args()
    internal = {args.owner.lower(), *(x.lower() for x in args.internal)}
    try:
        raw = json.loads(args.input.read_text()) if args.input else fetch_issues(args.owner)
        issues = parse_issues(raw, args.root, internal)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"issue-console: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(x) for x in issues], ensure_ascii=False, indent=2))
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("issue-console: TUI requires a terminal (use --json for scripts)", file=sys.stderr)
        return 2
    curses.wrapper(run_tui, issues, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
