"""Fleet composition, completeness, canary and findings (spec §9.2, §9.5, §9.6)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from selfcheck.fleet.reader import git_env
from selfcheck.graph.model import EdgeKind, Graph

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
