"""Снимок удалённого состояния песочницы до/после сценария (спека среза 1, §9.4).

Запускает владелец своей учёткой gh: `python3 deploy/conductor/acceptance_dump.py
<owner>/conductor-sandbox > before.json`. Тела комментариев не сохраняются —
только автор, время и разобранный маркер; токенов в выводе нет.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from conductor.markers import parse_body  # noqa: E402

Runner = Callable[[list[str]], tuple[int, str, str]]


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """gh с учёткой владельца."""
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr


def _pages(runner: Runner, path: str) -> list[dict[str, Any]]:
    code, out, err = runner(["api", "--paginate", "--slurp", path])
    if code != 0:
        raise SystemExit(f"gh api {path}: {err.strip()}")
    return [item for page in json.loads(out) for item in page]


def dump(repo: str, runner: Runner = run_gh) -> dict[str, Any]:
    """Issues и PR в любом состоянии с комментариями и маркерами."""
    items = []
    for issue in _pages(runner, f"repos/{repo}/issues?state=all&per_page=100"):
        comments = []
        for c in _pages(
            runner, f"repos/{repo}/issues/{issue['number']}/comments?per_page=100"
        ):
            marker = parse_body(c.get("body") or "")
            comments.append(
                {
                    "author": (c.get("user") or {}).get("login", ""),
                    "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at"),
                    "marker": {"kind": marker.kind, "fields": dict(marker.fields)}
                    if marker
                    else None,
                }
            )
        items.append(
            {
                "number": issue["number"],
                "is_pr": bool(issue.get("pull_request")),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "state_reason": issue.get("state_reason"),
                "labels": [lab["name"] for lab in issue.get("labels", [])],
                "comments": comments,
            }
        )
    return {"repo": repo, "items": sorted(items, key=lambda i: i["number"])}


if __name__ == "__main__":
    print(json.dumps(dump(sys.argv[1]), ensure_ascii=False, indent=1))
