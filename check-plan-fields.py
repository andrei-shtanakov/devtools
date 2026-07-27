#!/usr/bin/env python3
"""plan-fields — READ-ONLY fleet check over every repo's `TODO.md`.

Each repo can already validate its *own* plan file: `atp-platform` runs
`scripts/ci/check_plan_citations.py` as a pre-commit hook and in CI. But the
checks that matter most are the ones no single repo can run. A repo's CI has
no siblings checked out, so `@blocked_by:<other-repo>#<slug>` is exactly the
claim its owner cannot verify — and that is the claim that rots. In 2026-07
`atp-platform` and its `CLAUDE.md` both said R-06b was blocked on Maestro
R-03 for about three months after R-03 shipped, because each source cited the
other instead of the owner.

The workspace is the only place where the graph is resolvable, so the
cross-repo half lives here.

Checks:
  1. **Dangling blocker** (warning) — `@blocked_by:<repo>#<slug>` naming a
     repo not cloned on this host. A warning rather than an error: a workspace
     is one machine's set of clones, so the same plan file would pass or fail
     depending on who ran the check.
  2. **Stale blocker** (error) — the slug appears in the target repo's TODO
     only on completed (`[x]`) lines. This is the R-03 failure mode: an item
     waiting on something already delivered.
  3. **Unresolvable blocker** (warning) — the slug is nowhere in the target's
     TODO. Renamed, or never recorded there; either way nobody is tracking it.
  4. **Coverage** (report) — open items per repo, and how many carry no
     `@owner`. Measured rather than asserted, so "N items without an owner" is
     a number instead of an impression.
  5. **Tag-form divergence** (report) — `@owner:` values that are not
     `github:<login>` / `github-team:<org>/<team>` / `TBD`. Reported, never
     failed: the ecosystem contract
     (`_cowork_output/2026-07-26-plan-fields-and-todo-coverage-handoff.md` §3)
     specifies only "a handle without spaces", and §5.3 leaves the strict form
     an open question for the owner. Four repos currently put *repo names*
     here (`@owner:atp`, `@owner:arbiter`), which the strict grammar forbids.
     Failing on that would be this tool deciding a question that is not its
     to decide.

Exit 0 — no stale blockers. Exit 1 — at least one waits on
delivered work. That is the only host-independent failure here.
Nothing is written; the script only reads.

Usage:
    python3 check-plan-fields.py            # autodetect workspace root
    python3 check-plan-fields.py --root /path/to/all_ai_orchestrators
    python3 check-plan-fields.py --strict   # warnings fail too
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHECKBOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(\S.*)$")
BLOCKED_BY = re.compile(r"@blocked_by:([A-Za-z0-9._-]+)#([A-Za-z0-9._/-]+)")
OWNER_TAG = re.compile(r"@owner:(\S+)")
STRICT_OWNER = re.compile(
    r"^(?:github:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"|github-team:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
    r"|TBD)[.,;:)]?$"
)


@dataclass
class Item:
    """One checkbox line in some repo's TODO."""

    repo: str
    lineno: int
    text: str
    is_open: bool


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_root(explicit: str | None) -> Path:
    """The workspace directory holding the repos, as `repos.sh` finds it."""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    return here.parent


def discover(root: Path) -> tuple[dict[str, Path], set[str]]:
    """Repos with a TODO.md, and the names of those without one.

    The two are kept apart because a blocker naming a repo that exists but
    keeps no TODO is *unverifiable*, while one naming a directory that is not
    there at all is *wrong*. Collapsing them reported four real repos —
    `prograph-vault`, `github-checker`, `ai-orchestrators-workspace` — as
    missing, which is both false and the more alarming of the two messages.
    Whether those repos should have a TODO at all is an open question for the
    ecosystem (handoff §5.1), not something this check gets to prejudge.
    """
    planned: dict[str, Path] = {}
    unplanned: set[str] = set()
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / ".git").exists():
            continue
        todo = child / "TODO.md"
        if todo.is_file():
            planned[child.name] = todo
        else:
            unplanned.add(normalize(child.name))
    return planned, unplanned


def read_items(repo: str, todo: Path) -> list[Item]:
    """Checkbox lines only. A tag on a continuation line belongs to its item.

    Blockers and owners are read from the checkbox line itself, which is where
    the ecosystem contract puts them ("в хвост первой строки пункта"). Reading
    whole indented blocks would be more permissive, but it would also attribute
    a nested item's tag to its parent — and the blocker graph is exactly where
    that misattribution would be invisible.
    """
    items: list[Item] = []
    for lineno, line in enumerate(
        todo.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = CHECKBOX.match(line)
        if match:
            items.append(
                Item(repo, lineno, match.group(2), match.group(1) == " ")
            )
    return items


def normalize(name: str) -> str:
    """Repo names are cited inconsistently; compare case-insensitively.

    `@blocked_by:Maestro#...` and the directory `maestro` are the same repo.
    The canonical name is the directory a plain `git clone` produces, but the
    plan files predate that ruling and still carry the old capitalization.
    """
    return name.lower()


def check_blockers(
    items: list[Item],
    repos: dict[str, Path],
    unplanned: set[str],
    report: Report,
) -> None:
    """Resolve every cross-repo blocker against the repo that owns it."""
    by_name = {normalize(name): todo for name, todo in repos.items()}
    cache: dict[str, list[Item]] = {}

    for item in items:
        if not item.is_open:
            continue
        for target, slug in BLOCKED_BY.findall(item.text):
            where = f"{item.repo}/TODO.md:{item.lineno}"
            key = normalize(target)
            if key == normalize(item.repo):
                continue  # self-blocker: the repo's own check covers it
            if key in unplanned:
                report.warnings.append(
                    f"{where} — blocked on '{target}', which keeps no TODO.md; "
                    f"the claim cannot be verified from here"
                )
                continue
            if key not in by_name:
                # Not an error: a workspace is one machine's set of clones
                # (invariant 5), so a repo absent here may be present for
                # someone else. Failing would make the exit code a property
                # of the host rather than of the plan files.
                report.warnings.append(
                    f"{where} — @blocked_by names '{target}', which is not "
                    f"cloned on this host; unresolvable here"
                )
                continue
            if key not in cache:
                cache[key] = read_items(target, by_name[key])
            hits = [i for i in cache[key] if slug in i.text]
            if not hits:
                report.warnings.append(
                    f"{where} — '{slug}' not found in {target}/TODO.md; "
                    f"renamed, or never tracked there"
                )
            elif all(not i.is_open for i in hits):
                lines = ", ".join(f":{i.lineno}" for i in hits)
                report.errors.append(
                    f"{where} — blocked on '{target}#{slug}', which {target} "
                    f"has already completed ({lines}); the wait is over"
                )


def check_coverage(items: list[Item], repos: dict[str, Path], report: Report) -> None:
    """Count, per repo, the open items nobody is named for."""
    for repo in sorted(repos):
        mine = [i for i in items if i.repo == repo and i.is_open]
        unowned = [i for i in mine if not OWNER_TAG.search(i.text)]
        if not mine:
            continue
        share = f"{len(mine) - len(unowned)}/{len(mine)}"
        note = f"{repo}: {share} open items carry @owner"
        if unowned:
            note += f" (first unowned at :{unowned[0].lineno})"
        report.notes.append(note)


def check_divergence(items: list[Item], report: Report) -> None:
    """Report owner values outside the strict grammar, without failing them."""
    seen: dict[str, set[str]] = {}
    for item in items:
        for value in OWNER_TAG.findall(item.text):
            if not STRICT_OWNER.match(value):
                seen.setdefault(item.repo, set()).add(value)
    for repo in sorted(seen):
        values = ", ".join(sorted(seen[repo]))
        report.notes.append(
            f"{repo}: @owner values outside the strict grammar — {values}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="workspace root")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures"
    )
    args = parser.parse_args()

    root = find_root(args.root)
    repos, unplanned = discover(root)
    if not repos:
        print(f"no repo with a TODO.md under {root}", file=sys.stderr)
        return 1

    items: list[Item] = []
    for name, todo in repos.items():
        items.extend(read_items(name, todo))

    report = Report()
    check_blockers(items, repos, unplanned, report)
    check_coverage(items, repos, report)
    check_divergence(items, report)

    for note in report.notes:
        print(f"       {note}")
    for warning in report.warnings:
        print(f"WARN:  {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")

    failed = bool(report.errors) or (args.strict and report.warnings)
    # Invariant 5: a report names the host whose clones it describes.
    print(
        f"\nplan-fields on {socket.gethostname()}: {len(repos)} repo(s) with a "
        f"TODO, {len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
