"""Manifest profile parsing with strict SHA pins (spec 3.2).

`parse_profile` turns the umbrella `workspace-manifest.toml` plus a
`[df.profiles.<name>]` section into a build plan.  Every bad entry is
collected and reported in one `ManifestError`, never just the first.
"""

from __future__ import annotations

import re
import tomllib
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

MAX_MANIFEST_BYTES = 1024 * 1024
FETCH_TIMEOUT = 30
MEMBER_SECTIONS = ("cores", "apps", "tools")
PROFILE_KEYS = frozenset({"python", "members", "extends", "groups"})

_SHA = re.compile(r"[0-9a-f]{40}")
_PY_VERSION = re.compile(r"\d+\.\d+\.\d+")
_NAME = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_REPO = re.compile(rf"({_NAME})/({_NAME})")
_URL_FORMS = (
    re.compile(rf"git@github\.com:(?P<o>{_NAME})/(?P<r>{_NAME}?)(?:\.git)?"),
    re.compile(rf"https://github\.com/(?P<o>{_NAME})/(?P<r>{_NAME}?)(?:\.git)?"),
)


class ManifestError(Exception):
    """The manifest or a profile in it is invalid (or a fetch was refused)."""

    def __init__(self, problems: list[str] | tuple[str, ...] | str) -> None:
        items = (problems,) if isinstance(problems, str) else tuple(problems)
        self.problems: tuple[str, ...] = items
        super().__init__("; ".join(items))


@dataclass(frozen=True)
class RepoPin:
    """One repo to fetch, pinned to an exact 40-hex commit."""

    name: str
    url: str
    sha: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    """A resolved profile: exact Python version and ordered repo pins."""

    name: str
    python: str
    repos: tuple[RepoPin, ...]


def manifest_url(repo: str, sha: str) -> str:
    """Return the raw URL of `workspace-manifest.toml` at commit `sha`."""
    return f"https://raw.githubusercontent.com/{repo}/{sha}/workspace-manifest.toml"


def _https_get(url: str) -> bytes:
    """GET `url`, reading at most MAX_MANIFEST_BYTES + 1 bytes."""
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310
        return resp.read(MAX_MANIFEST_BYTES + 1)


def fetch_manifest(
    repo: str, sha: str, *, opener: Callable[[str], bytes] = _https_get
) -> bytes:
    """Fetch the manifest of `owner/name` at the 40-hex `sha`.

    Refusals raise `ManifestError`; network errors propagate as `OSError`.
    """
    if not _REPO.fullmatch(repo) or ".." in repo:
        raise ManifestError(f"repo {repo!r}: expected owner/name")
    if not _SHA.fullmatch(sha):
        raise ManifestError(f"sha {sha!r}: expected 40 lowercase hex")
    body = opener(manifest_url(repo, sha))
    if len(body) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"manifest larger than {MAX_MANIFEST_BYTES} bytes")
    return body


def _github_url(repo_url: object) -> str | None:
    if not isinstance(repo_url, str):
        return None
    for form in _URL_FORMS:
        if m := form.fullmatch(repo_url):
            return f"https://github.com/{m['o']}/{m['r']}.git"
    return None


def _table(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _locate(data: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    return [
        _table(_table(data.get(sec)).get(name))
        for sec in MEMBER_SECTIONS
        if name in _table(data.get(sec))
    ]


def _pin(
    data: Mapping[str, object],
    name: str,
    groups: tuple[str, ...],
    problems: list[str],
) -> RepoPin | None:
    found = _locate(data, name)
    if not found:
        problems.append(f"member {name!r}: not in [cores], [apps] or [tools]")
        return None
    if len(found) > 1:
        problems.append(f"member {name!r}: key found in more than one section")
        return None
    entry = found[0]
    url = _github_url(entry.get("repo_url"))
    sha = entry.get("sha")
    ok = True
    if url is None:
        problems.append(f"member {name!r}: repo_url must be a github.com repo")
        ok = False
    if not (isinstance(sha, str) and _SHA.fullmatch(sha)):
        problems.append(f"member {name!r}: sha must be 40 lowercase hex, got {sha!r}")
        ok = False
    if not ok or url is None or not isinstance(sha, str):
        return None
    return RepoPin(name, url, sha, groups)


def _str_list(value: object, where: str, problems: list[str]) -> list[str]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    problems.append(f"{where}: must be a list of strings")
    return []


def _groups_of(
    sect: Mapping[str, object], where: str, problems: list[str]
) -> dict[str, tuple[str, ...]]:
    raw = sect.get("groups", {})
    if not isinstance(raw, dict):
        problems.append(f"{where}.groups: must be a table")
        return {}
    return {
        k: tuple(_str_list(v, f"{where}.groups.{k}", problems)) for k, v in raw.items()
    }


def _base_of(
    profiles: Mapping[str, object],
    name: str,
    sect: Mapping[str, object],
    problems: list[str],
) -> Mapping[str, object] | None:
    if "extends" not in sect:
        return None
    base_name = sect["extends"]
    base = profiles.get(base_name) if isinstance(base_name, str) else None
    if not isinstance(base, dict) or base_name == name:
        problems.append(f"profile {name!r}: extends {base_name!r} is not a valid base")
        return None
    if "extends" in base:
        problems.append(
            f"profile {name!r}: extends {base_name!r} which itself extends "
            "(only one level, no cycles)"
        )
        return None
    return base


def _python_of(
    name: str,
    sect: Mapping[str, object],
    base: Mapping[str, object] | None,
    problems: list[str],
) -> str:
    own, inherited = sect.get("python"), (base or {}).get("python")
    for label, value in (("python", own), ("base python", inherited)):
        if value is not None and not (
            isinstance(value, str) and _PY_VERSION.fullmatch(value)
        ):
            problems.append(f"profile {name!r}: {label} must be exact X.Y.Z")
    if own is not None and inherited is not None and own != inherited:
        problems.append(f"profile {name!r}: python {own!r} differs from base")
    chosen = inherited if inherited is not None else own
    if chosen is None:
        problems.append(f"profile {name!r}: python is required")
    return chosen if isinstance(chosen, str) else ""


def _collect_members(
    name: str,
    sect: Mapping[str, object],
    base: Mapping[str, object] | None,
    problems: list[str],
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    where = f"profile {name!r}"
    members = _str_list((base or {}).get("members", []), where, problems)
    own = _str_list(sect.get("members", []), f"{where}.members", problems)
    groups = _groups_of(base or {}, where, problems)
    own_groups = _groups_of(sect, where, problems)
    for key in own_groups:
        if key not in own:
            problems.append(f"{where}: groups for {key!r}, not an own member")
    groups.update(own_groups)
    seen: set[str] = set()
    for member in [*members, *own]:
        if member in seen:
            problems.append(f"{where}: duplicate member {member!r}")
        seen.add(member)
    all_members = [*members, *own]
    for key in groups:
        if key not in all_members:
            problems.append(f"{where}: groups for {key!r}, not a member")
    return list(dict.fromkeys(all_members)), groups


def _load(data: bytes) -> dict[str, object]:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"invalid TOML: {exc}") from exc


def parse_profile(data: bytes, profile: str) -> Profile:
    """Resolve `profile` from manifest bytes; raise ManifestError listing all
    problems (bad pins, unknown members, inheritance errors, ...)."""
    manifest = _load(data)
    profiles = _table(_table(manifest.get("df")).get("profiles"))
    if not profiles:
        raise ManifestError("manifest has no [df.profiles]")
    sect = profiles.get(profile)
    if not isinstance(sect, dict):
        raise ManifestError(f"unknown profile {profile!r}")
    problems: list[str] = []
    for key in sorted(set(sect) - PROFILE_KEYS):
        problems.append(f"profile {profile!r}: unknown key {key!r}")
    base = _base_of(profiles, profile, sect, problems)
    python = _python_of(profile, sect, base, problems)
    members, groups = _collect_members(profile, sect, base, problems)
    pins = [_pin(manifest, m, groups.get(m, ()), problems) for m in members]
    if problems:
        raise ManifestError(problems)
    return Profile(profile, python, tuple(p for p in pins if p is not None))
