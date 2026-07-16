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
FIELD_SEP = "\x1f"  # unit separator: не встречается в subject/author, в отличие от "|"


class GitError(RuntimeError):
    """git завершился с ошибкой — сенсору нельзя молча превращать это в '(нет коммитов)'."""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise GitError(
            f"git -C {repo} {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.rstrip("\n")


def discover_repos(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/.git"))


def _head_exists(repo: Path) -> bool:
    # Unborn HEAD (свежий репо без коммитов) — валидное состояние «коммитов нет»,
    # а не ошибка git; git log на нём падает с exit 128.
    # С -q неверифицируемый ref — это тихий exit 1; всё прочее (битый репо,
    # права и т.п.) — реальная ошибка, которую нельзя превращать в commits=[].
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1 and not result.stderr.strip():
        return False
    raise GitError(
        f"git -C {repo} rev-parse --verify -q HEAD failed "
        f"(exit {result.returncode}): {result.stderr.strip()}"
    )


def _status_path(entry: str) -> str:
    # porcelain: "XY path" либо для rename/copy "XY orig -> new"
    path = entry[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path


def recent_changes(root: Path, since: str = "midnight") -> list[dict]:
    out: list[dict] = []
    for repo in discover_repos(root):
        if _head_exists(repo):
            commits = git(
                repo,
                "log",
                f"--since={since}",
                f"--pretty=%h{FIELD_SEP}%ad{FIELD_SEP}%an{FIELD_SEP}%s",
                "--date=iso",
            ).splitlines()
        else:
            commits = []
        dirty = git(repo, "status", "--porcelain").splitlines()
        if commits or dirty:
            out.append(
                {
                    "repo": repo.name,
                    "commits": [
                        dict(
                            zip(
                                ("sha", "date", "author", "subject"),
                                c.split(FIELD_SEP, 3),
                            )
                        )
                        for c in commits
                    ],
                    "uncommitted": len(dirty),
                    "uncommitted_files": [_status_path(d) for d in dirty[:MAX_FILES]],
                    "truncated": len(dirty) > MAX_FILES,
                }
            )
    return out


def main() -> None:
    root = Path(__file__).resolve().parent.parent  # devtools/.. = workspace
    since = sys.argv[1] if len(sys.argv) > 1 else "midnight"
    try:
        changes = recent_changes(root, since)
    except (GitError, OSError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {"workspace": str(root), "since": since, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(1)
    print(
        json.dumps(
            {"workspace": str(root), "since": since, "repos": changes},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
