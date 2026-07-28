#!/usr/bin/env python3
"""plan-fields — READ-ONLY cross-repo `TODO.md` check (thin wrapper, PF-7).

The cross-repo blocker graph is the claim no single repo's CI can verify — a
repo's CI has no siblings checked out, so `@blocked_by:<other-repo>#<slug>` is
exactly the claim its owner cannot check, and that is the claim that rots. The
workspace is the only place the graph is resolvable, so the check lives here.

This used to carry its own TODO parser and blocker resolver. It no longer does:
**one** implementation of the plan-fields contract now lives in the shared
`plan-fields` package (ADR-ECO-005 PF-7). This script keeps only what is genuinely
devtools' own — workspace discovery, severity policy, output format — and takes
all parsing, reference resolution and graph diagnostics from the package:

  * `parse_fleet` / `check_fleet` — the CANONICAL graph over `@id`'d items, with
    cross-repo `todo://` edges resolved and `PF-BLOCKER-STALE` on the resolved
    graph. A canonical stale is the one host-independent, stable-identity failure.
  * `check_legacy_fleet` — the TRANSITIONAL legacy `<repo>#<slug>` graph over the
    un-`@id`'d items the fleet still lives on (pre-PF-2B). Its findings are marked
    `[legacy source: no @id]` and are always warnings: without a stable identity a
    stale legacy blocker must not fail the build (it nudges @id migration instead).

Runtime: the `plan-fields` package needs **Python 3.12** and is a pinned
dependency, so THIS script runs under `uv` (`make plan-check` → `uv run
--frozen`). The other devtools scripts remain stdlib / Python 3.11 — only this
one moved. Run it directly under an interpreter without the package and it says
so and exits, rather than silently doing nothing.

Exit 0 — no canonical stale blocker. Exit 1 — at least one canonical (`@id`'d)
item waits on delivered work, or `--strict` and any warning. Nothing is written.

Usage (via uv, so the pinned package resolves):
    make plan-check
    uv run --frozen python check-plan-fields.py --root .. --manifest <manifest.toml>
    uv run --frozen python check-plan-fields.py --selftest   # policy self-check
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from plan_fields import (
        RepoInput,
        check_fleet,
        check_legacy_fleet,
        parse_fleet,
        scrape_items,
    )
    from plan_fields import fleet as _pf_fleet
except ImportError:  # pragma: no cover - exercised by humans, not the suite
    sys.stderr.write(
        "check-plan-fields.py needs the 'plan-fields' package (Python >=3.12).\n"
        "Run it through uv so the pinned dependency resolves:\n"
        "    make plan-check\n"
        "    uv run --frozen python check-plan-fields.py --root .. "
        "--manifest ../ai-orchestrators-workspace/workspace-manifest.toml\n"
        "The other devtools scripts stay stdlib/Python 3.11; only this one moved "
        "(ADR-ECO-005 PF-7).\n"
    )
    raise SystemExit(2)

# @owner strict grammar is devtools' OWN reporting policy, not the contract's —
# reported, never failed. Applied to owner values the shared scraper extracts;
# the contract's DEC-007 role-slug view (PF-OWNER-*) is a separate, @id-only
# concern we deliberately do not double-report here.
STRICT_OWNER = re.compile(
    r"^(?:github:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"|github-team:[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
    r"|TBD)[.,;:)]?$"
)

# devtools severity policy — a thin projection of the package's stable codes.
# A canonical stale (stable @id identity) is the only build-failing error; every
# other canonical finding, and every legacy finding, is a warning.
_CANONICAL_ERROR = {"PF-BLOCKER-STALE"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_root(explicit: str | None) -> Path:
    """The workspace directory holding the repos (parent of devtools)."""
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent.parent


def default_manifest(root: Path) -> Path:
    """The umbrella's frozen fleet manifest — SSOT of which repos exist."""
    return root / "ai-orchestrators-workspace" / "workspace-manifest.toml"


def build_inputs(
    root: Path, manifest: set[str]
) -> tuple[list[RepoInput], dict[str, Path]]:
    """Freeze one RepoInput per manifest repo from disk — devtools' discovery.

    Discovery/UX stays here; parsing does not. A manifest repo with a checkout is
    ``available`` with its TODO text (or ``todo_text=None`` when it keeps none);
    a manifest repo with no checkout here is ``available=False``. Repos present on
    disk but absent from the manifest are still scanned (as sources), so their own
    plan claims are checked, but the manifest stays the authority on existence.
    """
    on_disk: dict[str, Path] = {}
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / ".git").exists():
            on_disk[_pf_fleet.canonical_name(child).lower()] = child
    inputs: list[RepoInput] = []
    planned: dict[str, Path] = {}
    for repo in sorted(manifest | set(on_disk)):
        d = on_disk.get(repo)
        if d is None:
            inputs.append(RepoInput(repo, available=False))
            continue
        todo = d / "TODO.md"
        text = todo.read_text(encoding="utf-8", errors="ignore") if todo.is_file() else None
        if text is not None:
            planned[repo] = todo
        inputs.append(RepoInput(repo, todo_text=text, available=True))
    return inputs, planned


def resolve_graph(inputs: list[RepoInput], manifest: set[str], report: Report) -> None:
    """Project the package's canonical + legacy diagnostics into the report."""
    snapshot = parse_fleet(inputs, manifest)
    canonical = list(snapshot["diagnostics"]) + check_fleet(snapshot)
    edges = snapshot["edges"]
    if edges:
        report.notes.append(
            f"canonical: {len(edges)} resolved cross-repo @id edge(s)"
        )
    for d in canonical:
        line = _canonical_line(d)
        if line is None:
            continue
        bucket = report.errors if d["code"] in _CANONICAL_ERROR else report.warnings
        bucket.append(line)

    exclude = {
        (r["provenance"]["repo"], r["raw_ref"]) for r in snapshot["references"]
    }
    for d in check_legacy_fleet(inputs, manifest, exclude=exclude):
        # transitional: always a warning, and marked as identity-less so a reader
        # never mistakes it for a stable-identity finding.
        report.warnings.append(f"{d.message}  [legacy source: no @id]")


def _canonical_line(diag: dict) -> str | None:
    """A one-line rendering of a canonical diagnostic devtools cares to surface.

    @id-coverage (PF-ID-MISSING) and the @id-only owner findings are handled as
    operational coverage/divergence notes instead, so they are skipped here.
    """
    code = diag["code"]
    if code in {"PF-ID-MISSING", "PF-OWNER-MISSING", "PF-OWNER-GRAMMAR"}:
        return None
    return f"{diag['message']} [{code}]"


def check_coverage(inputs: list[RepoInput], report: Report) -> None:
    """Operational @owner + @id coverage per repo, from the shared scraper."""
    for inp in sorted(inputs, key=lambda i: i.repo):
        if inp.todo_text is None:
            continue
        opens = [i for i in scrape_items(inp.todo_text) if not i.checked]
        if not opens:
            continue
        owned = [i for i in opens if i.tags.get("owner")]
        ided = [i for i in opens if i.item_id]
        note = f"{inp.repo}: {len(owned)}/{len(opens)} open items carry @owner"
        if len(ided) < len(opens):
            note += f", {len(ided)}/{len(opens)} carry @id (PF-2B backlog)"
        report.notes.append(note)


def check_divergence(inputs: list[RepoInput], report: Report) -> None:
    """Report @owner values outside devtools' strict grammar, without failing."""
    for inp in sorted(inputs, key=lambda i: i.repo):
        if inp.todo_text is None:
            continue
        seen: set[str] = set()
        for item in scrape_items(inp.todo_text):
            for value in item.values("owner"):
                if not STRICT_OWNER.match(value):
                    seen.add(value)
        if seen:
            report.notes.append(
                f"{inp.repo}: @owner values outside the strict grammar — "
                f"{', '.join(sorted(seen))}"
            )


def _selftest() -> int:
    """Exercise the severity policy projection without a workspace."""
    # canonical stale -> error; canonical dangling -> warning; legacy -> warning.
    maestro = "- [x] done shipped @owner:o @id:done\n- [ ] r open @owner:o @id:r\n"
    proctor_canonical_stale = (
        "- [ ] x @owner:o @blocked_by:todo://maestro/done @id:x\n"
    )
    proctor_legacy_dangling = "- [ ] y @owner:o @blocked_by:maestro#gone\n"
    manifest = {"maestro", "proctor"}
    inputs = [
        RepoInput("maestro", maestro),
        RepoInput(
            "proctor", proctor_canonical_stale + proctor_legacy_dangling
        ),
    ]
    report = Report()
    resolve_graph(inputs, manifest, report)
    assert any("PF-BLOCKER-STALE" in e for e in report.errors), report.errors
    assert any("[legacy source: no @id]" in w for w in report.warnings), report.warnings
    assert not any("[legacy source: no @id]" in e for e in report.errors), report.errors

    report = Report()
    check_divergence([RepoInput("arbiter", "- [ ] a @owner:andrei\n")], report)
    assert report.notes and "andrei" in report.notes[0], report.notes

    report = Report()
    check_coverage([RepoInput("m", "- [ ] a @owner:o\n")], report)
    assert report.notes == ["m: 1/1 open items carry @owner, 0/1 carry @id (PF-2B backlog)"], (
        report.notes
    )
    print("selftest OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="workspace root")
    parser.add_argument("--manifest", default=None, help="workspace-manifest.toml")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="run the policy self-check and exit"
    )
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    root = find_root(args.root)
    if not root.is_dir():
        print(f"no such workspace directory: {root}", file=sys.stderr)
        return 1
    manifest_path = Path(args.manifest) if args.manifest else default_manifest(root)
    report = Report()
    if manifest_path.is_file():
        manifest = _pf_fleet.manifest_repos(manifest_path)
    else:
        # No manifest here: fall back to disk presence as the repo set, and say
        # so — REPO-UNKNOWN cannot be told from a real clone without the SSOT.
        manifest = set()
        report.notes.append(
            f"no manifest at {manifest_path}; using disk presence as the repo set "
            f"(REPO-UNKNOWN outcomes unavailable)"
        )

    inputs, planned = build_inputs(root, manifest)
    if not manifest:
        manifest = {i.repo for i in inputs if i.available}
    if not planned:
        print(f"no repo with a TODO.md under {root}", file=sys.stderr)
        return 1

    resolve_graph(inputs, manifest, report)
    check_coverage(inputs, report)
    check_divergence(inputs, report)

    for note in report.notes:
        print(f"       {note}")
    for warning in report.warnings:
        print(f"WARN:  {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    print(
        f"\nplan-fields on {socket.gethostname()}: {len(planned)} repo(s) with a "
        f"TODO, {len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
