"""charter_guard — грамматика charter схемы 2 и реестр кодов воркстримов.

Спека `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md`
§1.1–1.2. Реестр только растёт: код закрытого воркстрима не достаётся новому
вместе с его тестами. Историю git не читаем — CI делает checkout depth 1.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from governance.frontmatter import (
    join_frontmatter,
    split_frontmatter,
    update_frontmatter,
)

CODE_RE = re.compile(r"^[A-Z]{2,6}$")
PLAN_ITEM_RE = re.compile(r"^todo://([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
REGISTRY_PATH = "workstreams/codes.toml"
_TODO_ID_RE = re.compile(r"@id:([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class Charter:
    schema: int
    code: str | None
    plan_item: str | None


def read_charter(text: str) -> Charter:
    """Charter из frontmatter; без `schema` (или без frontmatter) — схема 1."""
    try:
        meta, _ = split_frontmatter(text)
    except ValueError:
        return Charter(schema=1, code=None, plan_item=None)
    schema = meta.get("schema", 1)
    return Charter(
        schema=int(schema) if str(schema).isdigit() else 0,
        code=meta.get("code"),
        plan_item=meta.get("plan_item"),
    )


def load_registry(text: str) -> dict:
    """Реестр кодов; повторный ключ — ошибка разбора TOML (ValueError)."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{REGISTRY_PATH}: {exc}") from exc


def charter_findings(
    charter: Charter, *, ws_id: str, registry: dict, todo_ids: set[str], repo: str
) -> list[str]:
    """Находки по charter одного воркстрима; схема 1 — без находок."""
    if charter.schema == 1:
        return []
    if charter.schema != 2:
        return [f"{ws_id}: schema {charter.schema} вне словаря 1|2"]
    out: list[str] = []
    if not charter.code:
        out.append(f"{ws_id}: схема 2 требует code")
    elif not CODE_RE.match(str(charter.code)):
        out.append(
            f"{ws_id}: code {charter.code!r} не соответствует CODE ^[A-Z]{{2,6}}$"
        )
    else:
        entry = registry.get(charter.code)
        if entry is None:
            out.append(
                f"{ws_id}: code {charter.code} не зарегистрирован в {REGISTRY_PATH}"
            )
        elif entry.get("workstream") != ws_id:
            out.append(
                f"{ws_id}: code {charter.code} зарегистрирован за {entry.get('workstream')}"
            )
    if not charter.plan_item:
        out.append(f"{ws_id}: схема 2 требует plan_item")
    else:
        m = PLAN_ITEM_RE.match(str(charter.plan_item))
        if m is None:
            out.append(
                f"{ws_id}: plan_item {charter.plan_item!r} не todo://<repo>/<id>"
            )
        elif m.group(1) != repo:
            out.append(f"{ws_id}: plan_item указывает на чужой репо {m.group(1)}")
        elif m.group(2) not in todo_ids:
            out.append(f"{ws_id}: пункт @id:{m.group(2)} не найден в TODO.md")
    return out


def registry_findings(base: dict, head: dict) -> list[str]:
    """Реестр только растёт: ключи базы остаются с тем же воркстримом."""
    out: list[str] = []
    for code, entry in base.items():
        now = head.get(code)
        if now is None:
            out.append(f"{REGISTRY_PATH}: код {code} удалён (реестр только растёт)")
        elif now.get("workstream") != entry.get("workstream"):
            out.append(
                f"{REGISTRY_PATH}: код {code} переназначен "
                f"{entry.get('workstream')} → {now.get('workstream')}"
            )
    return out


def code_change_findings(base: Charter | None, head: Charter) -> list[str]:
    """Код неизменяем, в том числе при --reopen charter."""
    if base is None or base.schema != 2 or head.schema != 2:
        return []
    if base.code != head.code:
        return [f"code неизменяем: {base.code} → {head.code}"]
    return []


def stamp_charter(text: str, *, code: str, plan_item: str) -> str:
    """Вписать схему 2 в авторский charter (раннер, после авторинга)."""
    updates = {"schema": 2, "code": code, "plan_item": plan_item}
    try:
        split_frontmatter(text)
    except ValueError:
        return join_frontmatter(updates, text)
    return update_frontmatter(text, {"schema": 2, "code": code, "plan_item": plan_item})


def register_code(registry_text: str, *, code: str, ws_id: str, approved: str) -> str:
    """Дописать код в реестр; занятый код — ValueError."""
    if not CODE_RE.match(code):
        raise ValueError(f"code {code!r} не соответствует ^[A-Z]{{2,6}}$")
    reg = load_registry(registry_text)
    if code in reg:
        raise ValueError(
            f"code {code} уже занят воркстримом {reg[code].get('workstream')}"
        )
    block = f'[{code}]\nworkstream = "{ws_id}"\napproved = {approved}\n'
    sep = "" if not registry_text or registry_text.endswith("\n") else "\n"
    return f"{registry_text}{sep}{block}"


def _git_show(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def repo_findings(repo: Path, base_ref: str | None) -> list[str]:
    """Все находки по репо: charter'ы схемы 2, реестр, неизменяемость кода."""
    reg_text = (
        (repo / REGISTRY_PATH).read_text() if (repo / REGISTRY_PATH).exists() else ""
    )
    try:
        registry = load_registry(reg_text)
    except ValueError as exc:
        return [str(exc)]
    todo = repo / "TODO.md"
    todo_ids = set(_TODO_ID_RE.findall(todo.read_text())) if todo.exists() else set()
    out: list[str] = []
    for charter_path in sorted(repo.glob("workstreams/*/spec/00-charter.md")):
        ws_id = charter_path.parent.parent.name
        head = read_charter(charter_path.read_text())
        out += charter_findings(
            head, ws_id=ws_id, registry=registry, todo_ids=todo_ids, repo=repo.name
        )
        if base_ref:
            rel = charter_path.relative_to(repo).as_posix()
            base_text = _git_show(repo, base_ref, rel)
            out += code_change_findings(
                read_charter(base_text) if base_text else None, head
            )
    if base_ref:
        base_reg = _git_show(repo, base_ref, REGISTRY_PATH)
        if base_reg:
            out += registry_findings(load_registry(base_reg), registry)
    return out


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
