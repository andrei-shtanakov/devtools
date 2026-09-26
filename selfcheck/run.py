"""Orchestrator and CLI: ``python -m selfcheck`` (spec §1, §4.3)."""

from __future__ import annotations

import argparse
import hashlib
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from selfcheck.config import Config, ConfigError, apply_allowlist, load_config
from selfcheck.corpus import list_corpus, materialize, release, repo_state
from selfcheck.delta import SKIPPED_KEY, RunSnapshot, comparability_key, compute_delta
from selfcheck.env import apply_env_policy, detect_env
from selfcheck.fleet.assemble import (
    CANARY_NODE,
    FleetView,
    fleet_findings,
    fleet_names,
    load_fleet,
    manifest_repo,
    surface_repos,
)
from selfcheck.fleet.reader import FleetRepo
from selfcheck.manifest import ManifestInfo, RepoEntry, load_manifest
from selfcheck.model import Confidence, Finding, Location, aggregate
from selfcheck.probes.base import (
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    instrument_findings,
    run_probe,
)
from selfcheck.registry import REGISTRY
from selfcheck.report import find_baseline, new_run_dir, write_report
from selfcheck.roles import glob_match

# a narrowed corpus (--path) would make these probes report false dead /
# false DEP002: they need the whole repo
NARROW_UNSAFE = frozenset({"usage-graph", "deptry"})


def exit_code(results: Sequence[ProbeResult]) -> int:
    """0 ok/skipped; 2 failed or partial; 3 only unavailable (spec §4.3)."""
    statuses = {r.status for r in results}
    if statuses & {ProbeStatus.FAILED, ProbeStatus.PARTIAL}:
        return 2
    if ProbeStatus.UNAVAILABLE in statuses:
        return 3
    return 0


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="selfcheck",
        description="Static self-diagnosis: bugs, dead code, duplicates, LLM sites.",
    )
    parser.add_argument("--workspace", type=Path, default=Path(".."))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--repo", action="append", default=[], help="git_dir to scan (default devtools)"
    )
    parser.add_argument("--sched-dir", type=Path, default=None)
    parser.add_argument(
        "--fleet", action="store_true", help="read every manifest repo (spec §9)"
    )
    parser.add_argument(
        "--path", action="append", default=[], help="glob limiting the corpus"
    )
    parser.add_argument(
        "--probe", action="append", default=[], help="run only these probes"
    )
    parser.add_argument("--out", type=Path, default=Path("out/selfcheck"))
    parser.add_argument("--config", type=Path, default=Path("selfcheck.toml"))
    args = parser.parse_args(argv)
    for name in ("workspace", "manifest", "out", "config", "sched_dir"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser().resolve())
    return args


@dataclass
class _Run:
    """Everything collected while scanning the repos of one run."""

    results: list[ProbeResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    no_env: dict[str, dict[str, int]] = field(default_factory=dict)
    inventory: list[dict[str, Any]] = field(default_factory=list)
    env: dict[str, dict[str, Any]] = field(default_factory=dict)
    repos: dict[str, dict[str, Any]] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)
    keys: dict[str, str | None] = field(default_factory=dict)
    corpora: dict[str, list[str]] = field(default_factory=dict)
    materialized: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    surface: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, list[str]] = field(default_factory=dict)
    vendored: dict[str, dict[str, Any]] = field(default_factory=dict)
    fleet: _Fleet | None = None


@dataclass
class _Fleet:
    """What --fleet reads once per run (spec §9.2)."""

    manifest: ManifestInfo
    mrepo: RepoEntry | None
    cache: dict[str, FleetRepo] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    fleet_only: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def _missing_repo(name: str) -> Finding:
    return Finding(
        rule="selfcheck/repo-missing",
        category="selfcheck",
        severity="high",
        confidence=Confidence.CONFIRMED,
        owner_repo=name,
        anchor=f"probe:{name}#materialize",
        locations=[Location("workspace-manifest.toml", 1)],
        suggestion="доклонировать репо (make install) или убрать из --repo",
    )


def _key(
    result: ProbeResult, env_mode: str, surface: dict[str, Any], run_dir: Path
) -> str | None:
    if result.status is ProbeStatus.SKIPPED:
        return SKIPPED_KEY
    return comparability_key(
        result,
        env_mode=env_mode,
        surface=surface if result.probe == "usage-graph" else None,
        run_dir=str(run_dir),
    )


def _fleet_view(
    repo: RepoEntry, args: argparse.Namespace, run_dir: Path, acc: _Run
) -> FleetView | None:
    fleet = acc.fleet
    if fleet is None:
        return None
    names = fleet_names(fleet.manifest, fleet.mrepo, [repo.name])
    paths = {fleet.mrepo.name: fleet.mrepo.path} if fleet.mrepo else {}
    return load_fleet(
        args.workspace,
        names,
        run_dir,
        scope_name=repo.name,
        cache=fleet.cache,
        paths=paths,
    )


def _usage_status(results: Sequence[ProbeResult]) -> ProbeStatus | None:
    return next((r.status for r in results if r.probe == "usage-graph"), None)


def _record_fleet(
    repo: RepoEntry, view: FleetView, results: Sequence[ProbeResult], acc: _Run
) -> None:
    assert acc.fleet is not None
    usage = _usage_status(results)
    ok = usage in (None, ProbeStatus.OK, ProbeStatus.PARTIAL)
    acc.fleet.status[repo.name] = view.status() if ok else "partial"
    if usage is ProbeStatus.OK:
        acc.keys[f"fleet@{repo.name}"] = "ok"
    acc.findings += fleet_findings(view, repo.name)
    graph = acc.graph.get(repo.name, {})
    acc.fleet.fleet_only[repo.name] = [
        {
            "node": anchor,
            "from": next(e["from"] for e in node["edges"] if e["kind"] == "fleet"),
        }
        for anchor, node in graph.items()
        if node.get("fleet_only")
    ]


def _scan_repo(
    repo: RepoEntry,
    args: argparse.Namespace,
    config: Config,
    specs: Sequence[ProbeSpec],
    run_dir: Path,
    acc: _Run,
) -> None:
    full = list_corpus(repo.path, config.corpus_exclude)
    corpus = [
        p for p in full if not args.path or any(glob_match(g, p) for g in args.path)
    ]
    if args.path and full and not corpus:
        acc.warnings.append(
            f"--path matched no files in {repo.name}: nothing was checked"
        )
    env = detect_env(repo.path)
    acc.repos[repo.name] = repo_state(repo.path)
    view = _fleet_view(repo, args, run_dir, acc)
    extra = canary_files(specs)
    if view is not None:
        extra[CANARY_NODE] = 'print("selfcheck fleet canary")\n'
    copy = run_dir / "src" / repo.name
    materialize(repo.path, corpus, copy, extra)
    acc.materialized.append(repo.name)
    acc.keys[f"materialize@{repo.name}"] = "ok"
    try:
        target = RepoTarget(
            repo.name,
            repo.path,
            copy,
            repo.languages,
            tuple(corpus),
            env,
            config.roles,
            config.corpus_exclude,
            args.sched_dir,
            view.status() if view is not None else "absent",
            time.time(),
            fleet_view=view,
        )
        results = [
            ProbeResult(
                s.name, repo.name, ProbeStatus.SKIPPED, "narrowed-corpus", rules=s.rules
            )
            if args.path and s.name in NARROW_UNSAFE
            else run_probe(s, target, run_dir / "work")
            for s in specs
        ]
    finally:
        warning = release(copy)
        if warning:
            acc.warnings.append(warning)
    acc.results += results
    kept, counts = apply_env_policy(
        aggregate(f for r in results for f in r.findings), env
    )
    acc.findings += kept
    acc.no_env[repo.name] = counts
    acc.env[repo.name] = {"mode": env.mode, "stale": env.stale}
    acc.corpora[repo.name] = corpus
    for r in results:
        acc.inventory += r.extra.get("inventory", [])
        if r.probe == "usage-graph" and r.extra:
            acc.graph[repo.name] = r.extra.get("graph", {})
            acc.vendored[repo.name] = r.extra.get("vendored", {})
            acc.surface.update(r.extra.get("surface", {}))
    # spec §4.3: for usage-graph the key carries surface.fleet and whether
    # --sched-dir was given — not the per-file history or the plist list;
    # with --fleet also the composition of R's fleet (§9.6)
    key_surface: dict[str, Any] = {
        "fleet": view.status() if view is not None else "absent",
        "sched_dir_given": args.sched_dir is not None,
    }
    if view is not None:
        key_surface["fleet_repos"] = sorted(view.expected)
    for r in results:
        key = _key(r, env.mode, key_surface, run_dir)
        acc.keys[f"{r.probe}@{repo.name}"] = key
        if r.probe == "usage-graph":
            # vendor-pin-* findings (rule prefix "selfcheck") follow usage-graph
            acc.keys[f"selfcheck@{repo.name}"] = key
    if view is not None:
        _record_fleet(repo, view, results, acc)


def _document(
    manifest_path: Path,
    run_id: str,
    wanted: list[str],
    manifest: ManifestInfo,
    config: Config,
    acc: _Run,
    final: list[Finding],
    suppressed: list[Finding],
    snapshot: RunSnapshot,
    delta: tuple[dict[str, str], list[dict[str, str]]],
) -> dict[str, Any]:
    return {
        "schema": 1,
        "run": {
            "run_id": run_id,
            "host": socket.gethostname(),
            "scope": wanted,
            "selection": acc.selection,
            "repos": acc.repos,
            "surface": acc.surface,
            "manifest_path": str(manifest_path),
            "manifest": {
                "entries_read": manifest.entries_read,
                "repos": [r.name for r in manifest.repos],
                "missing": list(manifest.missing),
            },
            "config_sha1": config.sha1,
            "env": acc.env,
            "warnings": acc.warnings,
        },
        "probes": [
            {**r.to_json(), "key": acc.keys.get(f"{r.probe}@{r.repo}")}
            for r in acc.results
        ],
        "findings": [f.to_json() for f in final],
        "suppressed": [f.to_json() for f in suppressed],
        "suppressed_no_env": acc.no_env,
        "graph": acc.graph,
        "vendored": acc.vendored,
        "fleet": {
            "enabled": acc.fleet is not None,
            "fleet_only": acc.fleet.fleet_only if acc.fleet else {},
        },
        "delta": {"statuses": delta[0], "gone": delta[1]},
        "inventory": {"llm": acc.inventory},
        "snapshot": snapshot.to_json(),
    }


def _fleet_surface(acc: _Run, scope: Sequence[str], manifest_path: Path) -> None:
    """``surface.fleet`` — the worst over scope; rows for U − scope (§9.2, §9.6)."""
    fleet = acc.fleet
    assert fleet is not None
    statuses = set(fleet.status.values())
    acc.surface["fleet"] = "partial" if "partial" in statuses else "complete"
    outside = fleet_names(fleet.manifest, fleet.mrepo, scope)
    rows = [fleet.cache[n] for n in outside if n in fleet.cache]
    acc.surface["fleet_repos"] = surface_repos(FleetView(rows, outside, [], None))
    acc.surface["manifest_sha1"] = hashlib.sha1(manifest_path.read_bytes()).hexdigest()


def main(
    argv: Sequence[str] | None = None, registry: Sequence[ProbeSpec] = REGISTRY
) -> int:
    """Run S1 and write the report; exit codes as in spec §4.3."""
    args = _args(argv)
    try:
        config = load_config(args.config)
        manifest = load_manifest(args.manifest, args.workspace)
        wanted = list(dict.fromkeys(args.repo or ["devtools"]))
        known = {r.name: r for r in manifest.repos}
        unknown = [r for r in wanted if r not in known and r not in manifest.missing]
        if unknown:
            raise ConfigError(f"repos not in manifest: {unknown}")
        run_id, run_dir = new_run_dir(args.out, datetime.now(UTC))
    except (ConfigError, OSError) as exc:
        print(f"selfcheck: {exc}", file=sys.stderr)
        return 4
    unknown_probes = sorted(set(args.probe) - {s.name for s in registry})
    if unknown_probes:
        print(f"selfcheck: unknown --probe {unknown_probes}", file=sys.stderr)
        return 4
    specs = [s for s in registry if not args.probe or s.name in args.probe]
    acc = _Run(
        surface={
            "fleet": "absent",
            "sched_dir": str(args.sched_dir) if args.sched_dir else None,
        }
    )
    if args.fleet:
        acc.fleet = _Fleet(manifest, manifest_repo(args.manifest))
    missing = [name for name in wanted if name not in known]
    for name in wanted:
        if name in known:
            try:
                _scan_repo(known[name], args, config, specs, run_dir, acc)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"selfcheck: materialize {name}: {exc}", file=sys.stderr)
                return 4
    if acc.fleet is not None:
        _fleet_surface(acc, wanted, args.manifest)
    extra = [*instrument_findings(acc.results), *(_missing_repo(n) for n in missing)]
    allow = apply_allowlist([*acc.findings, *extra], config, datetime.now(UTC).date())
    final = aggregate([*allow.kept, *allow.expired])
    snapshot = RunSnapshot(
        run_id=run_id,
        scope=wanted,
        materialized=acc.materialized,
        probe_keys=acc.keys,
        corpus=acc.corpora,
        sources={n: str(known[n].path) for n in wanted if n in known},
        findings={f.id: f.to_json() for f in final},
    )
    selection = {"path": sorted(args.path), "probe": sorted(args.probe)}
    base_doc, base_warnings = find_baseline(args.out, run_id, selection)
    acc.warnings += base_warnings
    acc.selection = selection
    base = RunSnapshot.from_json(base_doc["snapshot"]) if base_doc else None
    delta = compute_delta(base, snapshot)
    doc = _document(
        args.manifest,
        run_id,
        wanted,
        manifest,
        config,
        acc,
        final,
        allow.suppressed,
        snapshot,
        delta,
    )
    try:
        write_report(run_dir, doc)
    except OSError as exc:
        print(f"selfcheck: write report: {exc}", file=sys.stderr)
        return 4
    print(run_dir / "report.md")
    code = exit_code(acc.results)
    return 2 if missing and code in (0, 3) else code
