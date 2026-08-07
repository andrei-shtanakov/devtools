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
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from plan_fields import (
        ManifestIndex,
        RepoInput,
        ScrapedItem,
        check_fleet,
        check_legacy_fleet,
        parse_fleet,
        parse_owner,
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

# devtools severity policy — a thin projection of the package's stable codes.
# A canonical stale (stable @id identity) is the only build-failing error; every
# other canonical finding, and every legacy finding, is a warning.
_CANONICAL_ERROR = {"PF-BLOCKER-STALE"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_OWNERSHIP = (
    "human-owned",
    "repo-owned",
    "TBD",
    "missing",
    "invalid-owner",
    "unknown-repo-owner",
)
_MOVEMENT = (
    "actionable",
    "waiting-by-trigger",
    "waiting-by-blocker",
    "stale-condition",
    "malformed-condition",
)
_MALFORMED_BLOCKER_CODES = {
    "PF-ID-DANGLING",
    "PF-BLOCKER-DANGLING",
    "PF-BLOCKER-UNRESOLVABLE",
    "PF-BLOCKER-NO-TODO",
    "PF-BLOCKER-REPO-UNKNOWN",
    "PF-LEGACY-AMBIGUOUS",
}


def find_root(explicit: str | None) -> Path:
    """The workspace directory holding the repos (parent of devtools)."""
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent.parent


def default_manifest(root: Path) -> Path:
    """The umbrella's frozen fleet manifest — SSOT of which repos exist."""
    return root / "ai-orchestrators-workspace" / "workspace-manifest.toml"


def build_inputs(
    root: Path, index: ManifestIndex
) -> tuple[list[RepoInput], dict[str, Path]]:
    """Freeze one RepoInput per manifest repo from disk — devtools' discovery.

    Discovery/UX stays here; parsing and *identity* do not. Locating the
    checkouts is ``checkout_map``'s, so this script and the package agree on what
    a repo is called — they did not before, and the disagreement was invisible
    because it only showed on the one repo whose ``git_dir`` differs from its key.

    Two checkouts resolving to one name are the package's call too, and its rule
    is finer than refusing on sight: a bare second clone beside a real checkout
    has no wrong answer to prevent and must not abort the command, while two
    plan-bearing checkouts raise, because there the loser's TODO.md would vanish
    from the answer and which one loses would depend on directory order.

    A manifest repo with a checkout is ``available`` with its TODO text (or
    ``todo_text=None`` when it keeps none); a manifest repo with no checkout here
    is ``available=False``. Repos present on disk but absent from the manifest are
    still scanned (as sources), so their own plan claims are checked, but the
    manifest stays the authority on existence.
    """
    on_disk = _pf_fleet.checkout_map(root, index)
    inputs: list[RepoInput] = []
    planned: dict[str, Path] = {}
    for repo in sorted(set(index.canonical_keys) | set(on_disk)):
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


def resolve_graph(
    inputs: list[RepoInput], index: ManifestIndex, report: Report
) -> None:
    """Project the package's canonical + legacy diagnostics into the report."""
    snapshot = parse_fleet(inputs, index)
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
    for d in check_legacy_fleet(inputs, index, exclude=exclude):
        # transitional: always a warning, and marked as identity-less so a reader
        # never mistakes it for a stable-identity finding.
        report.warnings.append(f"{d.message}  [legacy source: no @id]")


def _canonical_line(diag: dict) -> str | None:
    """A one-line rendering of a canonical diagnostic devtools cares to surface.

    @id-coverage (PF-ID-MISSING) and the @id-only owner findings are handled as
    operational coverage/divergence notes instead, so they are skipped here.
    """
    code = diag["code"]
    if code == "PF-ID-MISSING" or code.startswith("PF-OWNER-"):
        return None
    return f"{diag['message']} [{code}]"


def _ownership_bucket(owner: str | None, index: ManifestIndex) -> str:
    """One ADR-ECO-005a ownership bucket, using only package grammar."""
    owner_ref, _owner_role, diagnostic = parse_owner(owner)
    if diagnostic == "PF-OWNER-MISSING":
        return "missing"
    if diagnostic is not None:
        return "invalid-owner"
    assert owner_ref is not None
    if owner_ref["kind"] in {"github_user", "github_team"}:
        return "human-owned"
    if owner_ref["kind"] == "tbd":
        return "TBD"
    if owner_ref["kind"] == "repository":
        return (
            "repo-owned"
            if owner_ref["id"] in index.canonical_keys
            else "unknown-repo-owner"
        )
    raise AssertionError(f"unexpected owner kind: {owner_ref['kind']}")


def _condition_codes(
    inputs: list[RepoInput], index: ManifestIndex
) -> dict[tuple[str, int], set[str]]:
    """Condition diagnostics by source item, canonical and transitional."""
    snapshot = parse_fleet(inputs, index)
    diagnostics = list(snapshot["diagnostics"]) + check_fleet(snapshot)
    by_item: dict[tuple[str, int], set[str]] = {}
    for diag in diagnostics:
        prov = diag.get("provenance") or {}
        repo, line = prov.get("repo"), prov.get("line")
        if isinstance(repo, str) and isinstance(line, int):
            by_item.setdefault((repo, line), set()).add(diag["code"])
    exclude = {
        (ref["provenance"]["repo"], ref["raw_ref"])
        for ref in snapshot["references"]
    }
    for diag in check_legacy_fleet(inputs, index, exclude=exclude):
        by_item.setdefault((diag.source_repo, diag.source_line), set()).add(diag.code)
    return by_item


def _movement_bucket(item: ScrapedItem, codes: set[str]) -> str:
    """One movement bucket; malformed beats stale when conditions conflict."""
    if codes & _MALFORMED_BLOCKER_CODES:
        return "malformed-condition"
    if "PF-BLOCKER-STALE" in codes:
        return "stale-condition"
    if item.values("blocked_by"):
        return "waiting-by-blocker"
    if item.tags.get("trigger"):
        return "waiting-by-trigger"
    return "actionable"


def check_reporting(
    inputs: list[RepoInput], index: ManifestIndex, report: Report
) -> None:
    """Publish independent ownership/movement totals and their full matrix."""
    ownership = {name: 0 for name in _OWNERSHIP}
    movement = {name: 0 for name in _MOVEMENT}
    matrix = {owner: {state: 0 for state in _MOVEMENT} for owner in _OWNERSHIP}
    condition_codes = _condition_codes(inputs, index)
    open_count = 0
    for inp in sorted(inputs, key=lambda i: i.repo):
        if inp.todo_text is None:
            continue
        opens = [i for i in scrape_items(inp.todo_text) if not i.checked]
        for item in opens:
            owner = _ownership_bucket(item.tags.get("owner"), index)
            state = _movement_bucket(
                item, condition_codes.get((inp.repo, item.line), set())
            )
            ownership[owner] += 1
            movement[state] += 1
            matrix[owner][state] += 1
            open_count += 1
    assert sum(ownership.values()) == open_count
    assert sum(movement.values()) == open_count
    assert sum(sum(row.values()) for row in matrix.values()) == open_count
    report.notes.append(
        "ownership: " + ", ".join(f"{key}={ownership[key]}" for key in _OWNERSHIP)
    )
    report.notes.append(
        "movement: " + ", ".join(f"{key}={movement[key]}" for key in _MOVEMENT)
    )
    for owner in _OWNERSHIP:
        report.notes.append(
            f"ownership×movement {owner}: "
            + ", ".join(f"{state}={matrix[owner][state]}" for state in _MOVEMENT)
        )


def _selftest() -> int:
    """Exercise the severity policy projection without a workspace."""
    # canonical stale -> error; canonical dangling -> warning; legacy -> warning.
    maestro = "- [x] done shipped @owner:o @id:done\n- [ ] r open @owner:o @id:r\n"
    proctor_canonical_stale = (
        "- [ ] x @owner:o @blocked_by:todo://maestro/done @id:x\n"
    )
    proctor_legacy_dangling = "- [ ] y @owner:o @blocked_by:maestro#gone\n"
    index = ManifestIndex(frozenset({"maestro", "proctor"}), {})
    inputs = [
        RepoInput("maestro", maestro),
        RepoInput(
            "proctor", proctor_canonical_stale + proctor_legacy_dangling
        ),
    ]
    report = Report()
    resolve_graph(inputs, index, report)
    assert any("PF-BLOCKER-STALE" in e for e in report.errors), report.errors
    assert any("[legacy source: no @id]" in w for w in report.warnings), report.warnings
    assert not any("[legacy source: no @id]" in e for e in report.errors), report.errors

    report = Report()
    reporting_inputs = [
        RepoInput(
            "m",
            "- [ ] human @owner:github:x @trigger:\"release\"\n"
            "- [ ] repo @owner:repo:m\n"
            "- [ ] undecided @owner:TBD\n"
            "- [ ] absent\n"
            "- [ ] legacy @owner:tech-lead\n",
        )
    ]
    check_reporting(reporting_inputs, ManifestIndex(frozenset({"m"}), {}), report)
    assert "human-owned=1" in report.notes[0], report.notes
    assert "repo-owned=1" in report.notes[0], report.notes
    assert "TBD=1" in report.notes[0], report.notes
    assert "missing=1" in report.notes[0], report.notes
    assert "invalid-owner=1" in report.notes[0], report.notes
    assert "waiting-by-trigger=1" in report.notes[1], report.notes
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
        try:
            index = _pf_fleet.manifest_index(manifest_path)
        except _pf_fleet.AmbiguousIdentityError as exc:
            # A manifest that names one checkout twice, or one that cannot be
            # told apart on disk, has no correct answer to give. Say which,
            # rather than dropping a traceback on a `make plan-check`.
            print(f"cannot resolve repo identity: {exc}", file=sys.stderr)
            return 1
    else:
        # No manifest here: fall back to disk presence as the repo set, and say
        # so — REPO-UNKNOWN cannot be told from a real clone without the SSOT.
        # With no manifest there are no locator aliases either, so identity
        # degrades to the origin-derived name `resolve_checkout` falls back to.
        index = ManifestIndex(frozenset(), {})
        report.notes.append(
            f"no manifest at {manifest_path}; using disk presence as the repo set "
            f"(REPO-UNKNOWN outcomes unavailable)"
        )

    try:
        inputs, planned = build_inputs(root, index)
    except _pf_fleet.AmbiguousIdentityError as exc:
        print(f"cannot resolve repo identity: {exc}", file=sys.stderr)
        return 1
    if not index.canonical_keys:
        index = ManifestIndex(
            frozenset(i.repo for i in inputs if i.available), {}
        )
    if not planned:
        print(f"no repo with a TODO.md under {root}", file=sys.stderr)
        return 1

    resolve_graph(inputs, index, report)
    check_reporting(inputs, index, report)

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
