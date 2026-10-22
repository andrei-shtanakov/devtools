"""Canonical identity of a package source (spec 3.5).

The lock side (`source_from_lock`) and the environment side
(`source_from_direct_url`) reduce a source to the same `SourceId`, so that the
expected and the installed distributions can be compared by equality.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from df.digest import TreeError

_COMMIT = re.compile(r"[0-9a-f]{40}")
#: Selectors of uv that are dropped: the commit in the fragment is the truth.
_SELECTORS = frozenset({"rev", "tag", "branch"})
_QUERY_KEYS = _SELECTORS | {"subdirectory"}
_FILE_HOSTS = frozenset({"", "localhost"})


class UnsupportedLock(Exception):
    """A lock source outside the supported kinds (spec 3.5)."""


@dataclass(frozen=True)
class SourceId:
    """The canonical source identity; fields not used by `kind` are `None`."""

    kind: Literal["registry", "git", "directory"]
    url: str | None = None
    subdirectory: str | None = None
    commit: str | None = None
    path: str | None = None
    editable: bool | None = None


def canonical_repo_url(url: str) -> str:
    """Return `https://host/path` without query, fragment, `/` and `.git`.

    Only `https` is accepted and the host is lower-cased; anything else is a
    `TreeError`.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise TreeError(f"репозиторий не https-URL: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise TreeError(f"в URL репозитория учётные данные: {url!r}")
    path = parts.path.rstrip("/").removesuffix(".git").rstrip("/")
    host = parts.netloc.lower()
    return f"https://{host}{path}"


def source_from_lock(source: Mapping[str, str], *, root_dir: Path) -> SourceId:
    """Reduce a `uv.lock` `source` table to its canonical identity.

    `root_dir` is the build copy of the repo: the lock's project root is
    installed from there with `--no-editable`.
    """
    if "git" in source:
        return _git_from_lock(source["git"])
    if source.get("editable") == "." or source.get("directory") == ".":
        return SourceId("directory", path=str(root_dir), editable=False)
    if "registry" in source and len(source) == 1:
        return SourceId("registry", url=source["registry"])
    raise UnsupportedLock(f"неподдерживаемый источник в lock: {dict(source)!r}")


def source_from_direct_url(data: Mapping[str, object] | None) -> SourceId:
    """Reduce an installed `direct_url.json` (`None`: no file) to identity."""
    if data is None:
        return SourceId("registry")
    if "archive_info" in data:
        raise TreeError("direct_url.json: archive_info не поддерживается")
    vcs, info = data.get("vcs_info"), data.get("dir_info")
    if isinstance(vcs, dict) and info is None:
        return _git_from_direct_url(data, vcs)
    if isinstance(info, dict) and vcs is None:
        return _directory_from_direct_url(data, info)
    raise TreeError("direct_url.json: нет vcs_info/dir_info или оба сразу")


def _git_from_lock(value: str) -> SourceId:
    """Parse `<url>?subdirectory=…&rev=…#<commit>`."""
    base, _, commit = value.partition("#")
    if not _COMMIT.fullmatch(commit):
        raise UnsupportedLock(f"commit в lock не 40 hex: {value!r}")
    url, _, query = base.partition("?")
    try:
        pairs = urllib.parse.parse_qsl(query, strict_parsing=bool(query))
    except ValueError as exc:
        raise UnsupportedLock(f"запрос git-источника: {value!r}") from exc
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys) or not set(keys) <= _QUERY_KEYS:
        raise UnsupportedLock(f"ключи запроса git-источника: {keys!r}")
    subdirectory = dict(pairs).get("subdirectory")
    return SourceId(
        "git",
        url=canonical_repo_url(url),
        subdirectory=subdirectory,
        commit=commit,
    )


def _git_from_direct_url(
    data: Mapping[str, object], vcs: dict[str, object]
) -> SourceId:
    url, commit = data.get("url"), vcs.get("commit_id")
    subdirectory = data.get("subdirectory")
    if vcs.get("vcs") != "git" or not isinstance(url, str):
        raise TreeError("direct_url.json: не git или нет url")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise TreeError(f"direct_url.json: commit_id {commit!r}")
    if subdirectory is not None and not isinstance(subdirectory, str):
        raise TreeError("direct_url.json: subdirectory не строка")
    return SourceId(
        "git",
        url=canonical_repo_url(url),
        subdirectory=subdirectory,
        commit=commit,
    )


def _directory_from_direct_url(
    data: Mapping[str, object], info: dict[str, object]
) -> SourceId:
    url, editable = data.get("url"), info.get("editable", False)
    if not isinstance(url, str) or not isinstance(editable, bool):
        raise TreeError("direct_url.json: dir_info без url или editable не bool")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in _FILE_HOSTS:
        raise TreeError(f"direct_url.json: не file-URL без хоста: {url!r}")
    path = urllib.parse.unquote(parts.path)
    names = path.split("/")
    if not path.startswith("/") or any(n in ("", ".", "..") for n in names[1:]):
        raise TreeError(f"direct_url.json: путь не нормален: {path!r}")
    return SourceId("directory", path=path, editable=editable)
