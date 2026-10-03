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


def _candidates(text: str) -> list[tuple[str, str, str]]:
    """(key, repo_url, git_dir) не-member записей с repo_url и git_dir."""
    return [
        (key, url, git_dir)
        for key, entry in _entries(text)
        if (url := entry.get("repo_url"))
        and (git_dir := entry.get("git_dir"))
        and not entry.get("member")
    ]


def fleet_repos(text: str, umbrella: str = UMBRELLA) -> list[FleetRepo]:
    """Не-member записи с repo_url, уникальные по git_dir, + зонтик профиля.

    Запись с не-GitHub repo_url сюда не входит — её называет unmatched_urls.
    """
    repos: dict[str, FleetRepo] = {}
    for key, url, git_dir in _candidates(text):
        if match := _URL_RE.search(url):
            repos.setdefault(git_dir, FleetRepo(key, git_dir, match.group(2)))
    repos.setdefault(umbrella, FleetRepo(umbrella, umbrella, umbrella))
    return sorted(repos.values(), key=lambda r: r.key)


def unmatched_urls(text: str) -> list[str]:
    """Записи флота, выпавшие из fleet_repos: repo_url не разобран как GitHub.

    Без этого репо исчезал бы из графа молча — сбой источника, а не тишина
    (I6, #511).
    """
    return [
        f"манифест: {key}: repo_url не GitHub ({url})"
        for key, url, _ in _candidates(text)
        if not _URL_RE.search(url)
    ]


def foreign_owner_urls(text: str) -> list[str]:
    """Записи флота под другим владельцем GitHub, чем первая запись.

    Поиск GitHub идёт по одному владельцу (`github_owner`), поэтому такое
    репо в обнаружение не попадает — сбой источника, а не тишина (#551).
    Поиск по нескольким владельцам — отдельный дизайн, не этот фикс.
    """
    try:
        owner = github_owner(text)
    except ValueError:
        return []
    return [
        f"манифест: {key}: владелец {m.group(1)} ≠ {owner} — вне поиска GitHub"
        for key, url, _ in _candidates(text)
        if (m := _URL_RE.search(url)) and m.group(1).lower() != owner.lower()
    ]


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
