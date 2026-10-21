"""Manifest profile parsing with strict SHA pins (spec 3.2).

`parse_profile` turns the umbrella `workspace-manifest.toml` plus a
`[df.profiles.<name>]` section into a build plan.  Every bad entry is
collected and reported in one `ManifestError`, never just the first.
"""

from __future__ import annotations

import http.client
import re
import tomllib
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from df.config import PY_VERSION, SHA1, TOML_ERRORS

MAX_MANIFEST_BYTES = 1024 * 1024
FETCH_TIMEOUT = 30
RAW_PREFIX = "https://raw.githubusercontent.com/"
MEMBER_SECTIONS = ("cores", "apps", "tools")
PROFILE_KEYS = frozenset({"python", "members", "extends", "groups"})

_NAME = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_IDENT = re.compile(_NAME)
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
    return f"{RAW_PREFIX}{repo}/{sha}/workspace-manifest.toml"


def _https_get(url: str) -> bytes:
    """GET `url`, reading at most MAX_MANIFEST_BYTES + 1 bytes.

    A final URL outside raw.githubusercontent.com (redirect) is refused;
    protocol-level errors surface as `OSError`.
    """
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310
            final = resp.geturl()
            if not final.startswith(RAW_PREFIX):
                raise ManifestError(f"redirected outside {RAW_PREFIX}: {final!r}")
            return resp.read(MAX_MANIFEST_BYTES + 1)
    except http.client.HTTPException as exc:
        raise OSError(f"HTTP error fetching {url}: {exc}") from exc


def fetch_manifest(
    repo: str, sha: str, *, opener: Callable[[str], bytes] = _https_get
) -> bytes:
    """Fetch the manifest of `owner/name` at the 40-hex `sha`.

    Refusals raise `ManifestError`; network errors propagate as `OSError`.
    """
    if not _REPO.fullmatch(repo) or ".." in repo:
        raise ManifestError(f"repo {repo!r}: expected owner/name")
    if not SHA1.fullmatch(sha):
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


def _layout_problem(name: str, entry: Mapping[str, object]) -> str | None:
    """The build assumes a standalone uv project at the repo root."""
    if entry.get("member") is True:
        return f"member {name!r}: workspace-member entries are not buildable"
    path = entry.get("pyproject_path")
    git_dir = entry.get("git_dir", name)
    if path is not None and path != f"{git_dir}/pyproject.toml":
        return f"member {name!r}: pyproject_path {path!r} is not at the repo root"
    return None


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
    if bad := _layout_problem(name, entry):
        problems.append(bad)
        return None
    url = _github_url(entry.get("repo_url"))
    sha = entry.get("sha")
    ok = True
    if url is None:
        problems.append(f"member {name!r}: repo_url must be a github.com repo")
        ok = False
    if not (isinstance(sha, str) and SHA1.fullmatch(sha)):
        problems.append(f"member {name!r}: sha must be 40 lowercase hex, got {sha!r}")
        ok = False
    if not ok or url is None or not isinstance(sha, str):
        return None
    return RepoPin(name, url, sha, groups)


def _ident_problem(kind: str, value: str) -> str | None:
    """Shared strict check for names that become path segments or argv."""
    if _IDENT.fullmatch(value) and ".." not in value:
        return None
    return f"{kind} {value!r}: must match [A-Za-z0-9_][A-Za-z0-9_.-]*, no '..'"


def _idents(kind: str, values: list[str], problems: list[str]) -> list[str]:
    """Return the safe values, reporting each unsafe one as a problem."""
    good: list[str] = []
    for value in values:
        if bad := _ident_problem(kind, value):
            problems.append(bad)
        else:
            good.append(value)
    return good


def _str_list(value: object, where: str, problems: list[str]) -> list[str]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    problems.append(f"{where}: must be a list of strings")
    return []


def _groups_of(
    sect: Mapping[str, object], members: list[str], where: str, problems: list[str]
) -> dict[str, tuple[str, ...]]:
    """Validated `groups` table; keys must be in `members` (reported once)."""
    raw = sect.get("groups", {})
    if not isinstance(raw, dict):
        problems.append(f"{where}.groups: must be a table")
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if bad := _ident_problem(f"{where} group member", key):
            problems.append(bad)
            continue
        if key not in members:
            problems.append(f"{where}: groups for {key!r}, not a member")
            continue
        names = _str_list(value, f"{where}.groups.{key}", problems)
        out[key] = tuple(_idents(f"{where} group", names, problems))
    return out


def _body(
    name: str, sect: Mapping[str, object], problems: list[str]
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Validate one profile section alone: keys, members, groups."""
    where = f"profile {name!r}"
    for key in sorted(set(sect) - PROFILE_KEYS):
        problems.append(f"{where}: unknown key {key!r}")
    if "members" not in sect and "extends" not in sect:
        problems.append(f"{where}: needs members or extends")
    raw = _str_list(sect.get("members", []), f"{where}.members", problems)
    members = _idents(f"{where} member", raw, problems)
    seen: set[str] = set()
    for member in members:
        if member in seen:
            problems.append(f"{where}: duplicate member {member!r}")
        seen.add(member)
    return members, _groups_of(sect, members, where, problems)


def _extends_target(
    profiles: Mapping[str, object],
    name: str,
    sect: Mapping[str, object],
    problems: list[str],
) -> tuple[str, Mapping[str, object]] | None:
    if "extends" not in sect:
        return None
    target = sect["extends"]
    base = profiles.get(target) if isinstance(target, str) else None
    if isinstance(target, str) and (bad := _ident_problem("extends", target)):
        problems.append(f"profile {name!r}: {bad}")
        return None
    if not isinstance(target, str) or not isinstance(base, dict) or target == name:
        problems.append(f"profile {name!r}: extends {target!r} is not a valid base")
        return None
    if "extends" in base:
        problems.append(
            f"profile {name!r}: extends {target!r} which itself extends "
            "(only one level, no cycles)"
        )
        return None
    return target, base


def _python_of(
    name: str,
    sect: Mapping[str, object],
    base: Mapping[str, object] | None,
    problems: list[str],
) -> str:
    own = sect.get("python")
    inherited = None if base is None else base.get("python")
    if base is not None and inherited is None:
        problems.append(f"profile {name!r}: base has no python to inherit")
    for label, value in (("python", own), ("base python", inherited)):
        if value is not None and not (
            isinstance(value, str) and PY_VERSION.fullmatch(value)
        ):
            problems.append(f"profile {name!r}: {label} must be exact X.Y.Z")
    if own is not None and inherited is not None and own != inherited:
        problems.append(f"profile {name!r}: python {own!r} differs from base")
    chosen = inherited if inherited is not None else own
    if chosen is None and base is None and "extends" not in sect:
        problems.append(f"profile {name!r}: python is required")
    return chosen if isinstance(chosen, str) else ""


def _resolve(
    profiles: Mapping[str, object],
    name: str,
    sect: Mapping[str, object],
    problems: list[str],
) -> tuple[str, list[str], dict[str, tuple[str, ...]]]:
    members, groups = _body(name, sect, problems)
    target = _extends_target(profiles, name, sect, problems)
    base = None if target is None else target[1]
    python = _python_of(name, sect, base, problems)
    if target is None:
        return python, members, groups
    base_members, base_groups = _body(target[0], base or {}, problems)
    for member in members:
        if member in base_members:
            problems.append(f"profile {name!r}: duplicate member {member!r}")
    return python, [*base_members, *members], {**base_groups, **groups}


def _load(data: bytes) -> dict[str, object]:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except TOML_ERRORS as exc:
        raise ManifestError(f"invalid TOML: {exc}") from exc


def _check_case_collisions(members: list[str], problems: list[str]) -> None:
    seen: dict[str, str] = {}
    for member in members:
        other = seen.setdefault(member.casefold(), member)
        if other != member:
            problems.append(f"members {other!r} and {member!r} differ only in case")


def parse_profile(data: bytes, profile: str) -> Profile:
    """Resolve `profile` from manifest bytes; raise ManifestError listing all
    problems (bad pins, unknown members, inheritance errors, ...)."""
    if bad := _ident_problem("profile", profile):
        raise ManifestError(bad)
    manifest = _load(data)
    profiles = _table(_table(manifest.get("df")).get("profiles"))
    if not profiles:
        raise ManifestError("manifest has no [df.profiles]")
    sect = profiles.get(profile)
    if not isinstance(sect, dict):
        raise ManifestError(f"unknown profile {profile!r}")
    problems: list[str] = []
    python, members, groups = _resolve(profiles, profile, sect, problems)
    members = list(dict.fromkeys(members))
    _check_case_collisions(members, problems)
    pins = [_pin(manifest, m, groups.get(m, ()), problems) for m in members]
    if problems:
        raise ManifestError(problems)
    return Profile(profile, python, tuple(p for p in pins if p is not None))
