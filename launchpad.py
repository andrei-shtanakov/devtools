#!/usr/bin/env python3
"""Read-only inventory for dispatcher Launchpad candidates.

The tool deliberately never edits a repository.  It answers the questions an
operator otherwise has to answer manually: which TODO item is registered,
where its DAG is, whether the DAG is usable, and which revision is checked out.

Examples::

    python launchpad.py list --root ~/labs/all_ai_orchestrators
    python launchpad.py check deployer --root ~/labs/all_ai_orchestrators
    python launchpad.py list --json --root ~/labs/all_ai_orchestrators
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ID_RE = re.compile(r"@id:([a-z0-9][a-z0-9._-]*)")
DAG_RE = re.compile(r"@dag:([^\s]+)")
VALID_DAG_RE = re.compile(r"^dags/[a-z0-9][a-z0-9._-]*\.yaml$")


@dataclass(frozen=True)
class Item:
    repository: str
    work_id: str
    line: int
    dag_path: str | None
    status: str
    reason: str
    revision: str | None


def git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def revision(repo: Path) -> str | None:
    return git(repo, "rev-parse", "HEAD")


def is_dirty(repo: Path, relative: str) -> bool:
    # Restrict the check to the DAG itself. Other local work should not hide a
    # usable registered plan from the inventory.
    result = git(repo, "status", "--porcelain", "--", relative)
    return result is not None and bool(result)


def parse_items(repo: Path) -> list[Item]:
    todo = repo / "TODO.md"
    if not todo.is_file():
        return []
    current_revision = revision(repo)
    items: list[Item] = []
    for number, raw in enumerate(todo.read_text(encoding="utf-8").splitlines(), 1):
        if not re.match(r"^\s*-\s*\[ \]", raw):
            continue
        id_match = ID_RE.search(raw)
        if not id_match:
            continue
        work_id = id_match.group(1)
        dag_match = DAG_RE.search(raw)
        dag_path = dag_match.group(1) if dag_match else None
        if dag_path is None:
            items.append(Item(repo.name, work_id, number, None, "missing-dag", "нет тега @dag", current_revision))
            continue
        if not VALID_DAG_RE.fullmatch(dag_path):
            items.append(Item(repo.name, work_id, number, dag_path, "invalid-dag", "недопустимый путь DAG", current_revision))
            continue
        expected = f"dags/{work_id}.yaml"
        if dag_path != expected:
            items.append(Item(repo.name, work_id, number, dag_path, "invalid-dag", f"ожидался {expected}", current_revision))
            continue
        dag = repo / dag_path
        if not dag.is_file() or dag.is_symlink():
            items.append(Item(repo.name, work_id, number, dag_path, "missing-dag", "файл не найден или является symlink", current_revision))
            continue
        if is_dirty(repo, dag_path):
            items.append(Item(repo.name, work_id, number, dag_path, "dirty-dag", "DAG не закоммичен", current_revision))
            continue
        items.append(Item(repo.name, work_id, number, dag_path, "ready", "готов к проверке Launchpad", current_revision))
    return items


def repositories(root: Path) -> list[Path]:
    result = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / ".git").exists() and child.name != ".git":
            result.append(child)
    return result


def inventory(root: Path) -> list[Item]:
    all_items: list[Item] = []
    for repo in repositories(root):
        all_items.extend(parse_items(repo))
    return all_items


def print_table(items: list[Item]) -> None:
    if not items:
        print("Открытых пунктов с @id не найдено.")
        return
    headers = ("Репозиторий", "Пункт", "DAG", "Статус", "Причина")
    rows = [
        (item.repository, item.work_id, item.dag_path or "—", item.status, item.reason)
        for item in items
    ]
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    print("  ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))
    print(f"\nВсего пунктов: {len(items)}; готовых: {sum(i.status == 'ready' for i in items)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Launchpad inventory")
    parser.add_argument("command", choices=("list", "check"), nargs="?", default="list")
    parser.add_argument("repository", nargs="?", help="имя каталога репозитория для check")
    parser.add_argument("--root", type=Path, help="корень workspace (по умолчанию определяется автоматически)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="вывести JSON")
    args = parser.parse_args(argv)
    if args.root is None:
        here = Path.cwd().resolve()
        root = here if (here / "devtools").is_dir() else here.parent
    else:
        root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"Ошибка: workspace не найден: {root}", file=sys.stderr)
        return 2
    items = inventory(root)
    if args.command == "check":
        if not args.repository:
            parser.error("для check нужен repository")
        items = [item for item in items if item.repository == args.repository]
        if not items:
            print(f"В {args.repository} нет открытых пунктов с @id.")
            return 1
    if args.as_json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
    else:
        print_table(items)
    return 0 if all(item.status == "ready" for item in items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
