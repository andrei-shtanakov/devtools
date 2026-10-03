"""closure_gate — потребитель стопа среза 1: [x] пункта плана требует закрытия.

Спека §7.1: пункт @id:X отмечен [x], и в репо есть charter схемы 2 с
plan_item todo://<repo>/X → рядом обязан лежать 90-acceptance-closure.md не
в состоянии blocked. Гейт живёт в CI репо-владельца; check-plan-fields.py
(сенсор без CI) может показывать то же только как предупреждение.

С 2a `traced` зелёный только со штампом, доказанным актом
(`acceptance_provenance`, спека §7.2a п.6); файл среза 1 без `status` —
зелёный с предупреждением лишь при графе на пине без человеческих критериев.
Нужны история git и чтение форджи (в CI — Task 9).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from plan_fields.parser import parse_todo

from governance import acceptance_provenance, charter_guard
from governance.frontmatter import split_frontmatter


def _closed_ids(repo: Path) -> set[str]:
    """Закрытые пункты TODO.md — тем же разбором, что dispatcher/fleet-граф
    (`declared_status: closed`), а не своим регэкспом."""
    todo = repo / "TODO.md"
    if not todo.exists():
        return set()
    doc = parse_todo(todo.read_text(encoding="utf-8"), repo=repo.name)
    return {
        n["id"]
        for n in doc.get("nodes", [])
        # fail-closed: неизвестный declared_status — не open, значит done и
        # гейт его проверяет (unknown-as-green был бы дырой); тем же правилом,
        # что conductor/facts.py, conductor/graph.py, conductor/collect.py,
        # todo_context.py (review PR #545).
        if n.get("declared_status") != "open"
    }


def gate_findings(
    repo: Path,
    *,
    slug: str | None = None,
    forge: acceptance_provenance.Forge | None = None,
) -> tuple[list[str], list[str]]:
    """(errors, warns) гейта `[x]`; `slug`/`forge` — для происхождения штампа."""
    done = _closed_ids(repo)
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
        try:
            meta, _ = split_frontmatter(closure_path.read_text())
        except ValueError as exc:
            # #481 M-2: названная находка вместо трейсбека из `main`
            errors.append(f"{ws}: frontmatter закрытия не разбирается: {exc}")
            continue
        state = meta.get("closure")
        if state == "blocked":
            errors.append(f"@id:{m.group(2)} [x], но закрытие {ws} — blocked")
        elif state == "traced":
            status = meta.get("status")
            spec_dir = charter_path.parent.relative_to(repo).as_posix()
            if status == "accepted":
                errors += [
                    f"{ws}: {why}"
                    for why in acceptance_provenance.stamp_findings(
                        repo,
                        spec_dir,
                        closure_path.read_text(),
                        slug=slug,
                        forge=forge,
                    )
                ]
            elif status is not None:
                errors.append(
                    f"@id:{m.group(2)} [x], но приёмка {ws} не завершена "
                    f"(status: {status!r} — нет штампа accepted); "
                    "снимите [x] пункта до штампа accepted и верните после — "
                    "иначе обязательная oracle-gates не пустит мерж самого "
                    "PR предложения"
                )
            else:
                pin = meta.get("bundle_pin")
                current = acceptance_provenance.pin_current(repo, spec_dir, pin)
                need = (
                    acceptance_provenance.human_needed(repo, spec_dir, pin)
                    if current
                    else None
                )
                if current is False:
                    errors.append(
                        f"{ws}: закрытие не для текущего бандла "
                        "(узлы на пине ≠ ревизии)"
                    )
                elif need is None:
                    errors.append(
                        f"{ws}: граф бандла на пине не прочитан — "
                        "закрытие среза 1 без штампа"
                    )
                elif need:
                    errors.append(
                        f"@id:{m.group(2)} [x], но у {ws} есть человеческие критерии — "
                        "подпись человека не получена (закрытие среза 1 без штампа)"
                    )
                else:
                    warns.append(
                        f"{ws}: закрытие среза 1 без штампа (только test-критерии)"
                    )
        elif state == "not-applicable":
            reason = meta.get("not_applicable_reason")
            where = (
                f"spec-runner {meta.get('spec_runner_version')} на {meta.get('host')}"
            )
            spec_dir = charter_path.parent.relative_to(repo).as_posix()
            if reason == "spec-runner-version":
                # оракул выпущен с spec-runner v4.5.0 — всегда перегнать
                errors.append(
                    f"{ws}: not-applicable spec-runner-version ({where}) — перегнать "
                    "закрытие на машине с spec-runner ≥ min"
                )
            elif reason == "schema-1":
                errors.append(
                    f"{ws}: charter схемы 2, а закрытие — schema-1: перегнать закрытие"
                )
            elif reason == "language":
                pin = meta.get("bundle_pin")
                current = acceptance_provenance.pin_current(repo, spec_dir, pin)
                need = (
                    acceptance_provenance.human_needed(repo, spec_dir, pin)
                    if current
                    else None
                )
                if current is False:
                    errors.append(f"{ws}: закрытие не для текущего бандла")
                elif need is None:
                    errors.append(f"{ws}: граф бандла на пине не прочитан")
                elif need:
                    errors.append(
                        f"@id:{m.group(2)} [x], но у {ws} есть человеческие критерии, "
                        "а оракул не применим (language) — путь подписи для n/a в 2b"
                    )
                else:
                    warns.append(f"{ws}: оракул не применим (language)")
            else:
                errors.append(
                    f"{ws}: not_applicable_reason {reason!r} вне словаря "
                    "language|schema-1|spec-runner-version"
                )
        else:
            errors.append(
                f"{ws}: closure {state!r} вне словаря traced|blocked|not-applicable"
            )
    return errors, warns


def _origin_slug(repo: Path) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", done.stdout.strip())
    return m.group(1) if done.returncode == 0 and m else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="closure_gate")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--repo-slug")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    slug = args.repo_slug or os.environ.get("GITHUB_REPOSITORY") or _origin_slug(repo)
    errors, warns = gate_findings(
        repo, slug=slug, forge=acceptance_provenance.RealForge()
    )
    for w in warns:
        print(f"closure_gate: warning: {w}")
    if warns:
        print(f"closure_gate: предупреждений {len(warns)}")
    for e in errors:
        print(f"closure_gate: error: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
