#!/usr/bin/env python3
"""catalog-conformance-fixtures — reference validator + pin-surface manifest.

Owner-side QA for `contracts/catalog-conformance-fixtures/v1/` (single owner
path per PP-103 acceptance (b), accepted from inbox devtools#43): a stdlib
reference implementation of the catalog contract rules V1..V7 (arbiter
catalog-loader design §4; V2+V3 mirror check-agent-id-conformance.py Check 5)
is run against every fixture, so each expectation in `expectations.toml` is
executable HERE — a fixture whose negative case the reference would accept
turns this checker red before any consumer vendors the set.

The manifest (`manifest.json`) is the pin surface consumers verify their
vendored copy against: sha256 per file plus a tree_sha256 over the sorted
`<path> <sha256>` pairs. The manifest itself is excluded from the surface.

Usage:
    python3 check-catalog-fixtures.py --check           # validate everything
    python3 check-catalog-fixtures.py --write-manifest  # regenerate manifest

Exit 0 — fixtures match expectations and manifest matches disk.
Exit 1 — at least one expectation or the manifest is broken.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONTRACT_SUBDIR = Path("contracts") / "catalog-conformance-fixtures" / "v1"
MANIFEST_NAME = "manifest.json"
EXPECTATIONS_NAME = "expectations.toml"

KNOWN_STATUSES = {"active", "deprecated", "retired"}
KNOWN_KINDS = {"cli", "api-baseline", "local"}
ERROR_CODES = {"V1", "V2", "V3", "V4", "V5"}
FLAG_CODES = {"V6", "V7"}
PATHRES_EXPECT_BY_ENV = {
    "set": "loaded",
    "unset": "not-configured",
    "set-missing": "missing-file-error",
}


@dataclass(frozen=True)
class Issue:
    """One validation finding: rule code, severity and a human message."""

    code: str
    severity: str  # "error" | "warning"
    message: str


def validate_catalog(catalog: dict) -> list[Issue]:
    """Reference implementation of contract rules V1..V7 (collects all)."""
    models: dict[str, dict] = catalog.get("models", {})
    harnesses: dict[str, dict] = catalog.get("harnesses", {})
    agents: list[dict] = catalog.get("agents", [])
    issues: list[Issue] = []

    for name, spec in models.items():
        status = spec.get("status", "active")
        if status not in KNOWN_STATUSES:
            issues.append(
                Issue("V7", "warning", f"model {name!r} has unknown status {status!r}")
            )
    for name, spec in harnesses.items():
        kind = spec.get("kind")
        if kind is not None and kind not in KNOWN_KINDS:
            issues.append(
                Issue("V7", "warning", f"harness {name!r} has unknown kind {kind!r}")
            )

    seen_ids: set[str] = set()
    for row in agents:
        harness = str(row["harness"])
        model = str(row["model"])
        agent_id = f"{harness}@{model}"
        if agent_id in seen_ids:
            issues.append(Issue("V4", "error", f"duplicate agent_id {agent_id!r}"))
        seen_ids.add(agent_id)

        if harness not in harnesses:
            issues.append(
                Issue(
                    "V1",
                    "error",
                    f"agent {agent_id!r} references undeclared harness {harness!r}",
                )
            )
        elif row.get("routable") and not harnesses[harness].get("routable"):
            issues.append(
                Issue(
                    "V5",
                    "error",
                    f"agent {agent_id!r} is routable but harness {harness!r} is not",
                )
            )

        spec = models.get(model)
        if spec is None:
            issues.append(
                Issue(
                    "V2",
                    "error",
                    f"agent {agent_id!r} references undeclared model {model!r}",
                )
            )
        elif spec.get("status") == "retired":
            issues.append(
                Issue(
                    "V3",
                    "error",
                    f"agent {agent_id!r} references retired model {model!r}",
                )
            )
        elif spec.get("status") == "deprecated":
            issues.append(
                Issue(
                    "V6",
                    "warning",
                    f"agent {agent_id!r} references deprecated model {model!r}",
                )
            )
    return issues


def check_case(contract_dir: Path, case: dict) -> str | None:
    """Run one expectations [[case]] against the reference; None if it holds."""
    rel = str(case.get("file", ""))
    expect = case.get("expect")
    fixture = contract_dir / rel
    if not fixture.is_file():
        return f"{rel}: fixture file missing"

    try:
        catalog = tomllib.loads(fixture.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None if expect == "parse-error" else f"{rel}: unexpected parse error"
    if expect == "parse-error":
        return f"{rel}: expected parse-error, but the file parses"

    try:
        issues = validate_catalog(catalog)
    except (AttributeError, LookupError, TypeError) as exc:
        # Parseable TOML with a shape the contract does not describe (e.g.
        # `models` not a table, an agent row without "harness") is a broken
        # fixture, not a checker crash.
        return f"{rel}: catalog shape not validatable: {exc!r}"
    errors = sorted({i.code for i in issues if i.severity == "error"})
    warnings = sorted({i.code for i in issues if i.severity == "warning"})

    if expect == "valid":
        if errors or warnings:
            return f"{rel}: expected valid, got errors={errors} warnings={warnings}"
        return None
    code = case.get("code")
    if expect == "error":
        if code not in ERROR_CODES:
            return f"{rel}: error case must carry a code in {sorted(ERROR_CODES)}"
        if errors != [code] or warnings:
            return (
                f"{rel}: expected exactly one error {code}, "
                f"got errors={errors} warnings={warnings}"
            )
        return None
    if expect == "flag":
        if code not in FLAG_CODES:
            return f"{rel}: flag case must carry a code in {sorted(FLAG_CODES)}"
        if errors or warnings != [code]:
            return (
                f"{rel}: expected only warning {code}, "
                f"got errors={errors} warnings={warnings}"
            )
        return None
    return f"{rel}: unknown expect class {expect!r}"


def check_pathres(contract_dir: Path, scenarios: list[dict]) -> list[str]:
    """Sanity-check the [[pathres]] scenarios (declaration-level only)."""
    problems: list[str] = []
    ids = [str(s.get("id", "")) for s in scenarios]
    if len(ids) != len(set(ids)):
        problems.append("pathres: duplicate scenario ids")
    if set(PATHRES_EXPECT_BY_ENV) - {str(s.get("env")) for s in scenarios}:
        problems.append(
            f"pathres: env layers {sorted(PATHRES_EXPECT_BY_ENV)} must all be covered"
        )
    for s in scenarios:
        sid, env = s.get("id"), str(s.get("env"))
        expected = PATHRES_EXPECT_BY_ENV.get(env)
        if expected is None:
            problems.append(f"pathres {sid}: unknown env layer {env!r}")
            continue
        if s.get("expect") != expected:
            problems.append(f"pathres {sid}: env {env!r} must expect {expected!r}")
        if env == "set" and not (contract_dir / str(s.get("target", ""))).is_file():
            problems.append(f"pathres {sid}: target fixture missing")
    return problems


def surface_files(contract_dir: Path) -> list[Path]:
    """Every file in the pin surface (manifest itself excluded), sorted."""
    return sorted(
        p for p in contract_dir.rglob("*") if p.is_file() and p.name != MANIFEST_NAME
    )


def compute_manifest(contract_dir: Path) -> dict:
    """Manifest document: per-file sha256 + tree_sha256 over sorted pairs."""
    entries = [
        {
            "path": p.relative_to(contract_dir).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in surface_files(contract_dir)
    ]
    tree = "\n".join(f"{e['path']} {e['sha256']}" for e in entries) + "\n"
    return {
        "contract": "catalog-conformance-fixtures",
        "contract_version": "v1",
        "surface_note": (
            "sha256 of every file in v1/ except this manifest; consumers also "
            "exclude their own PIN file when verifying a vendored copy"
        ),
        "files": entries,
        "tree_sha256": hashlib.sha256(tree.encode("utf-8")).hexdigest(),
    }


def check_all(contract_dir: Path) -> list[str]:
    """All owner-side checks; returns human-readable failure lines."""
    problems: list[str] = []
    expectations_path = contract_dir / EXPECTATIONS_NAME
    if not expectations_path.is_file():
        return [f"expectations file missing: {expectations_path}"]
    expectations = tomllib.loads(expectations_path.read_text(encoding="utf-8"))
    cases: list[dict] = expectations.get("case", [])

    covered = {str(c.get("file")) for c in cases}
    on_disk = {
        p.relative_to(contract_dir).as_posix()
        for p in (contract_dir / "fixtures").rglob("*.toml")
    }
    for extra in sorted(on_disk - covered):
        problems.append(f"{extra}: fixture has no [[case]] in expectations")
    for ghost in sorted(covered - on_disk):
        problems.append(f"{ghost}: [[case]] points at a non-existent fixture")

    for case in cases:
        problem = check_case(contract_dir, case)
        if problem:
            problems.append(problem)

    problems.extend(check_pathres(contract_dir, expectations.get("pathres", [])))

    manifest_path = contract_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        problems.append("manifest.json missing — run --write-manifest")
    elif json.loads(manifest_path.read_text(encoding="utf-8")) != compute_manifest(
        contract_dir
    ):
        problems.append("manifest.json stale — run --write-manifest and review")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=None, help="devtools repo root (default: self)"
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--write-manifest", action="store_true", help="regenerate manifest.json"
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="validate expectations + manifest (the default action)",
    )
    args = parser.parse_args()
    root = args.root or Path(__file__).resolve().parent
    contract_dir = root / CONTRACT_SUBDIR

    if args.write_manifest:
        manifest = compute_manifest(contract_dir)
        out = contract_dir / MANIFEST_NAME
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"✓ wrote {out} ({len(manifest['files'])} files)")
        return 0

    problems = check_all(contract_dir)
    for line in problems:
        print(f"✗ {line}")
    if problems:
        print("\nResult: catalog fixtures BROKEN — fix before publishing.")
        return 1
    print("✓ catalog-conformance-fixtures v1: expectations + manifest hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
