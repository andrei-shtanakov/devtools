"""Repo set from workspace-manifest.toml (spec §1.2)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from selfcheck.config import ConfigError

SECTIONS = ("cores", "apps", "tools")
MARKERS = {
    "pyproject.toml": "python",
    "Cargo.toml": "rust",
    "mix.exs": "elixir",
    "package.json": "ts",
}


@dataclass(frozen=True)
class RepoEntry:
    """One unique git_dir present on disk."""

    name: str
    path: Path
    languages: frozenset[str]


@dataclass(frozen=True)
class ManifestInfo:
    """What the manifest yielded; the report prints all three."""

    entries_read: int
    repos: tuple[RepoEntry, ...]
    missing: tuple[str, ...]
    order: tuple[str, ...] = ()  # every unique git_dir, manifest order (S2 §9.2)


def detect_languages(path: Path) -> frozenset[str]:
    """Languages by marker files at the repo root."""
    return frozenset(
        lang for marker, lang in MARKERS.items() if (path / marker).is_file()
    )


def load_manifest(manifest: Path, workspace: Path) -> ManifestInfo:
    """Dedupe cores/apps/tools entries by git_dir, keep manifest order."""
    try:
        data = tomllib.loads(manifest.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"manifest {manifest}: {exc}") from exc
    order: dict[str, None] = {}
    entries = 0
    for section in SECTIONS:
        for key, entry in data.get(section, {}).items():
            entries += 1
            git_dir = entry.get("git_dir")
            if not git_dir:
                raise ConfigError(f"manifest {section}.{key}: no git_dir")
            order.setdefault(git_dir, None)
    repos: list[RepoEntry] = []
    missing: list[str] = []
    for git_dir in order:
        path = (workspace / git_dir).resolve()
        if (path / ".git").exists():
            repos.append(RepoEntry(git_dir, path, detect_languages(path)))
        else:
            missing.append(git_dir)
    return ManifestInfo(entries, tuple(repos), tuple(missing), tuple(order))
