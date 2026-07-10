#!/usr/bin/env python3
"""recent_changes — что изменилось в workspace с момента X. READ-ONLY.

Темпоральный сенсор флота: коммиты за период ПЛЮС незакоммиченные файлы
(git log их не видит). Дополняет `github-checker snapshot` (текущее
состояние), не дублирует его — см. CLAUDE.md.

Использование:
    ./recent_changes.py                # с полуночи
    ./recent_changes.py "3 days ago"   # любой git-формат since
    make today                         # алиас

Выход: JSON-список репо, где что-то происходило. Пустой список — «изменений
не обнаружено с <since> в <workspace>» (не «изменений нет вообще»: сенсор
видит только git; предложение см. proposals/2026-07-10-robin-self-improvement.md).

Только stdlib; Python 3.9+.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAX_FILES = 20  # больше — усечение с явным truncated: true


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def discover_repos(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/.git"))


def recent_changes(root: Path, since: str = "midnight") -> list[dict]:
    out: list[dict] = []
    for repo in discover_repos(root):
        commits = git(
            repo,
            "log",
            f"--since={since}",
            "--pretty=%h|%ad|%an|%s",
            "--date=iso",
        ).splitlines()
        dirty = git(repo, "status", "--porcelain").splitlines()
        if commits or dirty:
            out.append(
                {
                    "repo": repo.name,
                    "commits": [
                        dict(
                            zip(
                                ("sha", "date", "author", "subject"),
                                c.split("|", 3),
                            )
                        )
                        for c in commits
                    ],
                    "uncommitted": len(dirty),
                    "uncommitted_files": [d[3:] for d in dirty[:MAX_FILES]],
                    "truncated": len(dirty) > MAX_FILES,
                }
            )
    return out


def main() -> None:
    root = Path(__file__).resolve().parent.parent  # devtools/.. = workspace
    since = sys.argv[1] if len(sys.argv) > 1 else "midnight"
    changes = recent_changes(root, since)
    print(
        json.dumps(
            {"workspace": str(root), "since": since, "repos": changes},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
