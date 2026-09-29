"""closure_gate — потребитель стопа среза 1: [x] пункта плана требует закрытия.

Спека §7.1: пункт @id:X отмечен [x], и в репо есть charter схемы 2 с
plan_item todo://<repo>/X → рядом обязан лежать 90-acceptance-closure.md не
в состоянии blocked. Гейт живёт в CI репо-владельца; check-plan-fields.py
(сенсор без CI) может показывать то же только как предупреждение.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from governance import charter_guard, criteria_contract
from governance.frontmatter import split_frontmatter

_DONE = re.compile(r"^\s*- \[x\].*?@id:([A-Za-z0-9_.-]+)", re.M)


def gate_findings(repo: Path, *, is_vendored: bool) -> tuple[list[str], list[str]]:
    todo = repo / "TODO.md"
    done = set(_DONE.findall(todo.read_text())) if todo.exists() else set()
    errors: list[str] = []
    warns: list[str] = []
    for charter_path in sorted(repo.glob("workstreams/*/spec/00-charter.md")):
        ch = charter_guard.read_charter(charter_path.read_text())
        if ch.schema != 2 or not ch.plan_item:
            continue
        m = charter_guard.PLAN_ITEM_RE.match(ch.plan_item)
        if m is None or m.group(2) not in done:
            continue
        ws = charter_path.parent.parent.name
        closure_path = charter_path.parent / "90-acceptance-closure.md"
        if not closure_path.exists():
            errors.append(f"@id:{m.group(2)} [x], но у {ws} нет файла закрытия")
            continue
        meta, _ = split_frontmatter(closure_path.read_text())
        state = meta.get("closure")
        if state == "blocked":
            errors.append(f"@id:{m.group(2)} [x], но закрытие {ws} — blocked")
        elif state == "traced":
            if int(meta.get("human_pending", 0)) > 0:
                warns.append(f"{ws}: human_pending={meta['human_pending']} — подпись в срезе 2")
        elif state == "not-applicable":
            reason = meta.get("not_applicable_reason")
            where = f"spec-runner {meta.get('spec_runner_version')} на {meta.get('host')}"
            if reason == "spec-runner-version" and is_vendored:
                errors.append(f"{ws}: not-applicable spec-runner-version ({where}) при вендоренном контракте — перегнать закрытие на машине с spec-runner ≥ min")
            elif reason == "spec-runner-version":
                warns.append(f"{ws}: оракул не применим (spec-runner-version, {where})")
            else:
                warns.append(f"{ws}: оракул не применим ({reason})")
        else:
            errors.append(f"{ws}: closure {state!r} вне словаря traced|blocked|not-applicable")
    return errors, warns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="closure_gate")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    errors, warns = gate_findings(args.repo.resolve(), is_vendored=criteria_contract.vendored())
    for w in warns:
        print(f"closure_gate: warning: {w}")
    if warns:
        print(f"closure_gate: предупреждений {len(warns)}")
    for e in errors:
        print(f"closure_gate: error: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
