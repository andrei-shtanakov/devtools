"""Fleet composition, completeness, canary and findings (spec §9.2, §9.5, §9.6)."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selfcheck.fleet import reader
from selfcheck.fleet.reader import FleetRepo, git_env
from selfcheck.graph.model import EdgeKind, Graph
from selfcheck.manifest import ManifestInfo, RepoEntry, detect_languages
from selfcheck.model import Confidence, Finding, Location

CANARY_NODE = ".selfcheck-canary/usage-graph/fleet_canary.py"
CANARY_REPO = "fleet-canary"
CANARY_WORKFLOW = ".github/workflows/c.yml"
CANARY_NOTES = "notes.md"
_IDENTITY = {
    "GIT_AUTHOR_NAME": "selfcheck",
    "GIT_AUTHOR_EMAIL": "selfcheck@localhost",
    "GIT_COMMITTER_NAME": "selfcheck",
    "GIT_COMMITTER_EMAIL": "selfcheck@localhost",
}
_NO_SIDE_EFFECTS = ("-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")


def _canary_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_NO_SIDE_EFFECTS, "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={**git_env(), **_IDENTITY},
    )


def build_canary(
    root: Path, scope_name: str, forms: Sequence[str] = ("precise", "text")
) -> Path:
    """A one-commit git repo referencing CANARY_NODE of ``scope_name`` (§9.5)."""
    root.mkdir(parents=True)
    files = {"README.md": "selfcheck fleet canary\n"}
    if "precise" in forms:
        files[CANARY_WORKFLOW] = (
            "on: push\njobs:\n  canary:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: python3 {scope_name}/{CANARY_NODE}\n"
        )
    if "text" in forms:
        files[CANARY_NOTES] = "```zsh\nfleet_canary.py\n```\n"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _canary_git(root, "init", "-q", "-b", "main")
    _canary_git(root, "add", "--", *files)
    _canary_git(root, "commit", "-q", "-m", "selfcheck fleet canary")
    return root


def canary_misses(g: Graph) -> list[str]:
    """Which canary forms did not reach CANARY_NODE of ``g`` from the canary's
    own files: "precise" (workflow edge), "text" (notes mention)."""
    anchor = f"file:{CANARY_NODE}"
    edges = {e.where.path for e in g.incoming(anchor) if e.kind is EdgeKind.FLEET}
    misses = []
    if f"{CANARY_REPO}:{CANARY_WORKFLOW}" not in edges:
        misses.append("precise")
    if f"{CANARY_REPO}:{CANARY_NOTES}" not in g.mentions.get(anchor, []):
        misses.append("text")
    return misses


@dataclass
class FleetView:
    """The fleet of one scope repo R: U − {R} as read this run (spec §9.6)."""

    repos: list[FleetRepo]
    expected: tuple[str, ...]
    canary_misses: list[str]
    canary: FleetRepo | None

    def status(self) -> str:
        """``complete`` only with no canary miss, the expected composition and
        no problem in any repo; otherwise ``partial``."""
        if self.canary_misses or any(r.problems for r in self.repos):
            return "partial"
        if sorted(r.name for r in self.repos) != sorted(self.expected):
            return "partial"
        return "complete"


def manifest_repo(manifest: Path, workspace: Path) -> RepoEntry | None:
    """The git repo holding ``manifest`` — unless its root is the workspace
    root itself: that is the root umbrella, never a fleet repo (§9.2)."""
    proc = subprocess.run(
        ["git", "-C", str(manifest.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        env=git_env(),
        check=False,
    )
    if proc.returncode != 0:
        return None
    root = Path(proc.stdout.strip()).resolve()
    if root == workspace.resolve():
        return None
    return RepoEntry(root.name, root, detect_languages(root))


def fleet_names(
    info: ManifestInfo, mrepo: RepoEntry | None, scope: Sequence[str]
) -> tuple[str, ...]:
    """U − scope: unique manifest ``git_dir`` in order, then the manifest repo."""
    names = list(info.order)
    if mrepo is not None and mrepo.name not in names:
        names.append(mrepo.name)
    return tuple(n for n in names if n not in scope)


def load_fleet(
    workspace: Path,
    names: Sequence[str],
    run_dir: Path,
    *,
    scope_name: str,
    cache: dict[str, FleetRepo] | None = None,
    paths: Mapping[str, Path] | None = None,
) -> FleetView:
    """Read the fleet of ``scope_name`` and its canary neighbour."""
    cache = {} if cache is None else cache
    paths = paths or {}
    repos = []
    for name in names:
        if name not in cache:
            cache[name] = reader.read_repo(paths.get(name, workspace / name), name)
        repos.append(cache[name])
    root = build_canary(run_dir / CANARY_REPO / scope_name, scope_name)
    canary = reader.read_repo(root, CANARY_REPO)
    stale_ok = canary.state is not None and canary.state.stale == ("no-origin-head",)
    return FleetView(repos, tuple(names), [] if stale_ok else ["stale"], canary)


def _fleet_finding(
    scope_repo: str, key: str, locations: list[Location], evidence: list[dict[str, str]]
) -> Finding:
    return Finding(
        rule="selfcheck/fleet-partial",
        category="selfcheck",
        severity="medium",
        confidence=Confidence.CONFIRMED,
        owner_repo=scope_repo,
        anchor=f"probe:{scope_repo}#fleet",
        locations=locations,
        text_key=key,
        evidence=evidence,
        suggestion="синхронизируйте флот (./repos.sh pull) и повторите с --fleet",
    )


def fleet_findings(view: FleetView, scope_repo: str) -> list[Finding]:
    """One ``selfcheck/fleet-partial`` per (repo, cause), plus ``fleet:count``."""
    out: list[Finding] = []
    for repo in view.repos:
        grouped: dict[str, list[str]] = {}
        for cause, detail in repo.problems:
            grouped.setdefault(cause, []).append(detail)
        for cause, details in grouped.items():
            locations = [Location(f"{repo.name}:{d}", 1) for d in details if d] or [
                Location("workspace-manifest.toml", 1)
            ]
            out.append(
                _fleet_finding(scope_repo, f"{repo.name}:{cause}", locations, [])
            )
    actual = sorted(r.name for r in view.repos)
    if actual != sorted(view.expected):
        out.append(
            _fleet_finding(
                scope_repo,
                "fleet:count",
                [Location("workspace-manifest.toml", 1)],
                [
                    {"kind": "expected", "detail": ", ".join(sorted(view.expected))},
                    {"kind": "actual", "detail": ", ".join(actual)},
                ],
            )
        )
    return out


def surface_repos(view: FleetView) -> list[dict[str, Any]]:
    """``run.surface.fleet_repos`` rows (spec §9.6)."""
    rows = []
    for repo in view.repos:
        st = repo.state
        rows.append(
            {
                "name": repo.name,
                "head": st.head if st else None,
                "branch": st.branch if st else None,
                "default": st.default if st else None,
                "behind": st.behind if st else None,
                "ahead": st.ahead if st else None,
                "files": len(repo.texts),
                "binary": repo.binary,
                "dirty": st.dirty if st else None,
                "fetched_at": st.fetched_at if st else None,
            }
        )
    return rows
