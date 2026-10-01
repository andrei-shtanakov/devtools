"""Репо-цели флота из текста workspace-manifest.toml плюс зонтик (§3.1).

Работает с ТЕКСТОМ манифеста: он сохраняется в Inputs, и replay не зависит от
файла на диске.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import plan_fields as pf

UMBRELLA = "ai-orchestrators-workspace"
_URL_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$")


@dataclass(frozen=True)
class FleetRepo:
    """Канонический ключ, каталог клона и имя репо на GitHub."""

    key: str
    git_dir: str
    github_name: str


def _entries(text: str) -> list[tuple[str, dict[str, Any]]]:
    data = tomllib.loads(text)
    return [
        (key, entry)
        for section in data.values()
        if isinstance(section, dict)
        for key, entry in section.items()
        if isinstance(entry, dict)
    ]


def fleet_repos(text: str, umbrella: str = UMBRELLA) -> list[FleetRepo]:
    """Не-member записи с repo_url, уникальные по git_dir, + зонтик профиля."""
    repos: dict[str, FleetRepo] = {}
    for key, entry in _entries(text):
        url, git_dir = entry.get("repo_url"), entry.get("git_dir")
        if not url or not git_dir or entry.get("member"):
            continue
        if match := _URL_RE.search(url):
            repos.setdefault(git_dir, FleetRepo(key, git_dir, match.group(2)))
    repos.setdefault(umbrella, FleetRepo(umbrella, umbrella, umbrella))
    return sorted(repos.values(), key=lambda r: r.key)


def github_owner(text: str) -> str:
    """Владелец флота на GitHub — из repo_url первой записи."""
    for _, entry in _entries(text):
        if match := _URL_RE.search(entry.get("repo_url", "")):
            return match.group(1)
    raise ValueError("в манифесте нет github repo_url")


def manifest_index(text: str) -> Any:
    """plan_fields.ManifestIndex из текста манифеста."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "workspace-manifest.toml"
        path.write_text(text, encoding="utf-8")
        return pf.manifest_index(path)
