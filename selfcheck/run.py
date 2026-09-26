"""Orchestrator and CLI: ``python -m selfcheck`` (spec §1, §4.3)."""

from __future__ import annotations

import argparse
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


def _scan_repo(
    repo: RepoEntry,
    args: argparse.Namespace,
    config: Config,
    specs: Sequence[ProbeSpec],
    run_dir: Path,
    acc: _Run,
) -> None:
    corpus = [
        p
        for p in list_corpus(repo.path, config.corpus_exclude)
        if not args.path or any(glob_match(g, p) for g in args.path)
    ]
    env = detect_env(repo.path)
    acc.repos[repo.name] = repo_state(repo.path)
    copy = run_dir / "src" / repo.name
    materialize(repo.path, corpus, copy, canary_files(specs))
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
            "absent",
            time.time(),
        )
        results = [run_probe(s, target, run_dir / "work") for s in specs]
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
            acc.surface.update(r.extra.get("surface", {}))
        acc.keys[f"{r.probe}@{repo.name}"] = _key(r, env.mode, acc.surface, run_dir)


def _document(
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
            "repos": acc.repos,
            "surface": acc.surface,
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
        "delta": {"statuses": delta[0], "gone": delta[1]},
        "inventory": {"llm": acc.inventory},
        "snapshot": snapshot.to_json(),
    }


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
    specs = [s for s in registry if not args.probe or s.name in args.probe]
    acc = _Run(
        surface={
            "fleet": "absent",
            "sched_dir": str(args.sched_dir) if args.sched_dir else None,
        }
    )
    missing = [name for name in wanted if name not in known]
    for name in wanted:
        if name in known:
            try:
                _scan_repo(known[name], args, config, specs, run_dir, acc)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"selfcheck: materialize {name}: {exc}", file=sys.stderr)
                return 4
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
    base_doc = find_baseline(args.out, run_id)
    base = RunSnapshot.from_json(base_doc["snapshot"]) if base_doc else None
    delta = compute_delta(base, snapshot)
    doc = _document(
        run_id, wanted, manifest, config, acc, final, allow.suppressed, snapshot, delta
    )
    try:
        write_report(run_dir, doc)
    except OSError as exc:
        print(f"selfcheck: write report: {exc}", file=sys.stderr)
        return 4
    print(run_dir / "report.md")
    code = exit_code(acc.results)
    return 2 if missing and code in (0, 3) else code
