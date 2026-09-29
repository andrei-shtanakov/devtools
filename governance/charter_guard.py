"""charter_guard — charter схемы 2 и уникальность кода воркстрима.

Спека `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md`
§1.1–1.2 (rev 10, решение владельца 2026-09-29): реестр кодов — сами charter'ы
схемы 2. Отдельного файла нет: он не доезжал до base (candidate-PR переносит
только узлы). Цель прежнего реестра — код закрытого воркстрима не
переиспользуется — держит запрет удалять/переносить charter схемы 2
(надгробие). Коллизия одного кода у двух charter'ов разрешается порядком
first-parent default-ветки: нарушитель — позже влитый; только он вправе сменить
код через --reopen charter.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from governance.frontmatter import (
    join_frontmatter,
    split_frontmatter,
    update_frontmatter,
)

CODE_RE = re.compile(r"^[A-Z]{2,6}$")
PLAN_ITEM_RE = re.compile(r"^todo://([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
CHARTER_GLOB = "workstreams/*/spec/00-charter.md"
_TODO_ID_RE = re.compile(r"@id:([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class Charter:
    schema: int
    code: str | None
    plan_item: str | None
    malformed: bool = False


def _has_frontmatter(text: str) -> bool:
    return text.startswith("---")


def read_charter(text: str) -> Charter:
    """Charter из frontmatter.

    Нет frontmatter — схема 1. Frontmatter есть, но не разбирается — не
    схема 1, а `malformed` (находка): иначе опечатка в YAML charter'а схемы 2
    тихо выключала бы оракул.
    """
    if not _has_frontmatter(text):
        return Charter(schema=1, code=None, plan_item=None)
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return Charter(schema=0, code=None, plan_item=None, malformed=True)
    schema = meta.get("schema", 1)
    return Charter(
        schema=int(schema) if str(schema).isdigit() else 0,
        code=meta.get("code"),
        plan_item=meta.get("plan_item"),
    )


def charter_findings(
    charter: Charter, *, ws_id: str, todo_ids: set[str], repo: str
) -> list[str]:
    """Грамматика одного charter'а; схема 1 — без находок."""
    if charter.malformed:
        return [f"{ws_id}: frontmatter charter не разбирается"]
    if charter.schema == 1:
        return []
    if charter.schema != 2:
        return [f"{ws_id}: schema {charter.schema} вне словаря 1|2"]
    out: list[str] = []
    if not charter.code:
        out.append(f"{ws_id}: схема 2 требует code")
    elif not CODE_RE.match(str(charter.code)):
        out.append(f"{ws_id}: code {charter.code!r} не соответствует CODE ^[A-Z]{{2,6}}$")
    if not charter.plan_item:
        out.append(f"{ws_id}: схема 2 требует plan_item")
    else:
        m = PLAN_ITEM_RE.match(str(charter.plan_item))
        if m is None:
            out.append(f"{ws_id}: plan_item {charter.plan_item!r} не todo://<repo>/<id>")
        elif m.group(1) != repo:
            out.append(f"{ws_id}: plan_item указывает на чужой репо {m.group(1)}")
        elif m.group(2) not in todo_ids:
            out.append(f"{ws_id}: пункт @id:{m.group(2)} не найден в TODO.md")
    return out


def collision_findings(charters: dict[str, Charter], *, order: dict[str, int]) -> list[str]:
    """Один код у двух charter'ов: нарушитель — позже влитый по first-parent.

    `order` — позиция влития charter'а в first-parent истории (меньше —
    раньше); не влитый (нет в `order`) считается самым поздним.
    """
    by_code: dict[str, list[str]] = {}
    for ws, ch in charters.items():
        if ch.schema == 2 and ch.code:
            by_code.setdefault(str(ch.code), []).append(ws)
    out: list[str] = []
    late = 10**9
    for code, owners in sorted(by_code.items()):
        if len(owners) < 2:
            continue
        ranked = sorted(owners, key=lambda w: (order.get(w, late), w))
        first = ranked[0]
        out += [
            f"{ws}: code {code} уже у {first} (влит раньше) — нарушитель {ws}; "
            "смените код через --reopen charter"
            for ws in ranked[1:]
        ]
    return out


def deletion_findings(base: dict[str, Charter], head: dict[str, Charter]) -> list[str]:
    """Charter схемы 2 — надгробие кода: удалять и переносить нельзя."""
    return [
        f"{path}: charter схемы 2 удалён или перенесён (код {ch.code} — надгробие)"
        for path, ch in sorted(base.items())
        if ch.schema == 2 and (path not in head or head[path].schema != 2)
    ]


def code_change_findings(
    base: Charter | None, head: Charter, *, violator_in_base: bool
) -> list[str]:
    """Код неизменяем; единственное исключение — нарушитель коллизии в base."""
    if base is None or base.schema != 2 or head.schema != 2 or base.code == head.code:
        return []
    if violator_in_base:
        return []
    return [f"code неизменяем: {base.code} → {head.code}"]


def stamp_charter(text: str, *, code: str, plan_item: str) -> str:
    """Вписать схему 2 (идемпотентно); битый frontmatter — ValueError."""
    updates = {"schema": 2, "code": code, "plan_item": plan_item}
    if not _has_frontmatter(text):
        return join_frontmatter(updates, text)
    if read_charter(text).malformed:
        raise ValueError("frontmatter charter не разбирается — штамп невозможен")
    if read_charter(text) == Charter(2, code, plan_item):
        return text
    return update_frontmatter(text, updates)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def charters_at(repo: Path, ref: str) -> dict[str, Charter]:
    """path → Charter на ревизии (git-объекты, не рабочее дерево)."""
    proc = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", "workstreams")
    out: dict[str, Charter] = {}
    for path in proc.stdout.splitlines():
        if Path(path).match(CHARTER_GLOB):
            shown = _git(repo, "show", f"{ref}:{path}")
            if shown.returncode == 0:
                out[path] = read_charter(shown.stdout)
    return out


def merge_order(repo: Path, ref: str, paths: list[str]) -> dict[str, int]:
    """path → позиция первого появления в first-parent истории `ref`."""
    chain = _git(repo, "rev-list", "--first-parent", "--reverse", ref).stdout.split()
    index = {sha: i for i, sha in enumerate(chain)}
    out: dict[str, int] = {}
    for path in paths:
        hist = _git(repo, "log", "--first-parent", "--format=%H", ref, "--", path)
        shas = hist.stdout.split()
        if shas and shas[-1] in index:
            out[path] = index[shas[-1]]
    return out


def _ws(path: str) -> str:
    return Path(path).parent.parent.name


def repo_findings(repo: Path, base_ref: str | None) -> list[str]:
    """Все находки по репо на рабочем дереве против base (если задана)."""
    todo = repo / "TODO.md"
    todo_ids = set(_TODO_ID_RE.findall(todo.read_text())) if todo.exists() else set()
    head = {
        p.relative_to(repo).as_posix(): read_charter(p.read_text())
        for p in sorted(repo.glob(CHARTER_GLOB))
    }
    out: list[str] = []
    for path, ch in head.items():
        out += charter_findings(ch, ws_id=_ws(path), todo_ids=todo_ids, repo=repo.name)
    ref = base_ref or "HEAD"
    order_by_path = merge_order(repo, ref, list(head))
    collisions = collision_findings(
        {_ws(p): c for p, c in head.items()},
        order={_ws(p): i for p, i in order_by_path.items()},
    )
    if base_ref:
        base = charters_at(repo, base_ref)
        out += deletion_findings(base, head)
        base_violators = {
            f.split(":", 1)[0]
            for f in collision_findings(
                {_ws(p): c for p, c in base.items()},
                order={_ws(p): i for p, i in merge_order(repo, base_ref, list(base)).items()},
            )
        }
        for path, ch in head.items():
            out += code_change_findings(
                base.get(path), ch, violator_in_base=_ws(path) in base_violators
            )
    return out + collisions


def main(argv: list[str] | None = None) -> int:
    """CLI: exit 1 при находках (PR и push в default)."""
    parser = argparse.ArgumentParser(prog="charter_guard")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default=None)
    args = parser.parse_args(argv)
    findings = repo_findings(args.repo.resolve(), args.base)
    for f in findings:
        print(f"charter_guard: {f}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
