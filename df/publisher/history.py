"""The publisher's own history token and its reads (installer spec §6.1, §3.10).

Stdlib only, and `df` never imports `governance`: this is the publisher's
copy of `governance.act_trust.history_token_at` and of the history
self-report (`_history_self_report`), not a shared one. The token is
obtained by the publisher itself through the approved `gh` under the launch
contract (exactly `GH_ENV_KEYS`, a fresh `HOME` per call); a binding without
the key reads anonymously, a binding with the key and no token is
`Unavailable` -- never an anonymous fallback.

Every read goes through one `Transport` built by `history_transport`, so the
self-report and the reads carry the same token value. No reason text ever
carries the token, `gh` output or exception text.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from df import platform
from df.publisher import github
from df.publisher.github import Transport
from df.publisher.perimeter import Unavailable
from df.publisher.trust_types import Finding, Role, clip_detail
from df.stage2_config import TrustBinding

#: The only host v1 reads trust sources on.
HOST = "github.com"
#: The file whose raw bytes the fast path digests (spec A rev 18 §6.1).
SOURCE_FILE = "source.env"
#: Seconds `gh auth token` may take.
GH_TIMEOUT = 60.0
_TOKEN_FORM = re.compile(r"[A-Za-z0-9_]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_FULL_NAME = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_WRITE = ("push", "maintain", "admin")
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": github.USER_AGENT,
}
_GH_WHERE = "gh auth token (history)"


def history_token(
    binding: TrustBinding, *, gh: Path, role_home: Path
) -> str | None | Unavailable:
    """The history token the binding names, or `None` when it names none.

    `None` -- the binding has no `history_gh_config_dir` (anonymous reads).
    Otherwise the token comes from `<gh> auth token --hostname github.com`
    with `GH_CONFIG_DIR` set to the profile, exactly `GH_ENV_KEYS` and a
    fresh `HOME` directory in `role_home`, removed after the call. A missing
    `hosts.yml` is `Unavailable` before `gh` runs; any failure is
    `Unavailable` with a fixed reason, never a fallback to anonymous."""
    config = binding.history_gh_config_dir
    if config is None:
        return None
    if not gh.is_absolute():
        return Unavailable(f"{_GH_WHERE}: gh path is not absolute")
    if not _is_file(Path(config) / "hosts.yml"):
        return Unavailable(f"history token profile {config}: no hosts.yml")
    try:
        home = tempfile.mkdtemp(prefix="gh-call-", dir=role_home)
    except OSError as exc:
        return Unavailable(f"{_GH_WHERE}: call directory: {type(exc).__name__}")
    try:
        return _auth_token(gh, _gh_env(config, home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def history_transport(token: str | None, *, send: Transport | None = None) -> Transport:
    """The one transport of the history reads: `GET` on the API host only.

    With a token every request carries `Authorization: Bearer <token>`;
    without one no `Authorization` at all (a caller's header is dropped).
    `send` defaults to `github.default_transport` (no proxy from the
    environment, no redirect followed); tests pass a fake."""
    auth = {} if token is None else {"Authorization": f"Bearer {token}"}

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, Mapping[str, str], bytes]:
        if method != "GET" or body is not None:
            raise ValueError("history transport: GET without a body only")
        if not url.startswith(f"{github.API}/"):
            raise ValueError("history transport: API host only")
        extra = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        out = send or github.default_transport
        return out(method, url, {**extra, **_HEADERS, **auth}, None)

    return transport


def self_report(
    transport: Transport, binding: TrustBinding, policy_repository: int
) -> Finding | Unavailable | None:
    """Self-report of the history token on both trust sources (spec §6.1).

    `GET /repositories/{id}` of `approval-policy` (`policy_repository`) and
    `df-trust` (the binding's id) through `transport`, i.e. with the same
    token value as the reads. A literal `true` in `push`/`maintain`/`admin`
    is a `token_write` finding, whatever else failed; otherwise an error, a
    missing `permissions` or a non-bool field is `Unavailable` (never
    `false`); all three `false` on both is `None`."""
    gap: Unavailable | None = None
    targets: tuple[tuple[Role, int], ...] = (
        ("approval-policy", policy_repository),
        ("df-trust", binding.df_trust_repository_id),
    )
    for role, repo_id in targets:
        verdict = _permissions(transport, role, repo_id)
        if isinstance(verdict, Finding):
            return verdict
        if gap is None:
            gap = verdict
    return gap


def read_branch_head(
    transport: Transport, full_name: str, branch: str
) -> str | Unavailable:
    """The head SHA of `branch` of `full_name` (`GET …/branches/{branch}`)."""
    if not _FULL_NAME.fullmatch(full_name):
        return Unavailable(f"repository name {full_name!r} is not owner/name")
    path = f"{_repo_path(full_name)}/branches/{quote(branch, safe='')}"
    body = _get(transport, path)
    if isinstance(body, Unavailable):
        return body
    commit = body.get("commit") if isinstance(body, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return Unavailable(f"{full_name}: head of {branch!r} not read")
    return sha


def read_source_digest(
    transport: Transport, full_name: str, sha: str
) -> str | Unavailable:
    """Lowercase hex sha256 of the raw bytes of `source.env` at commit `sha`.

    The bytes are not parsed. They are bound to the commit by its tree: the
    commit gives the tree, the complete tree gives the blob id of
    `source.env`, the blob's bytes must hash to that id. A file missing from
    the tree is `Unavailable` too (the fast path only compares digests)."""
    where = f"{full_name}@{sha[:12]}:{SOURCE_FILE}"
    if not _FULL_NAME.fullmatch(full_name) or not _SHA.fullmatch(sha):
        return Unavailable(f"{where}: not owner/name and a full SHA")
    oid = _source_oid(transport, full_name, sha, where)
    if isinstance(oid, Unavailable):
        return oid
    raw = _blob(transport, full_name, oid, where)
    if isinstance(raw, Unavailable):
        return raw
    return hashlib.sha256(raw).hexdigest()


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _gh_env(config: str, home: str) -> dict[str, str]:
    """Exactly `platform.GH_ENV_KEYS` (spec 3.10 rev 11); a test holds them equal."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "LANG": "C",
        "GH_CONFIG_DIR": config,
        "GH_NO_UPDATE_NOTIFIER": "1",
    }


def _auth_token(gh: Path, env: Mapping[str, str]) -> str | Unavailable:
    """Run `gh auth token`; reasons are fixed strings (no output, no token)."""
    argv = [str(gh), "auth", "token", "--hostname", HOST]
    result = platform.run_command(argv, timeout=GH_TIMEOUT, env=env)
    if not result.launched:
        return Unavailable(f"{_GH_WHERE}: not launched")
    if result.timed_out:
        return Unavailable(f"{_GH_WHERE}: timed out")
    if result.returncode != 0:
        return Unavailable(f"{_GH_WHERE}: exit code {result.returncode}")
    token = result.stdout.decode("utf-8", "replace").strip()
    if not token:
        return Unavailable(f"{_GH_WHERE}: empty output")
    if result.stdout_truncated or not _TOKEN_FORM.fullmatch(token):
        return Unavailable(f"{_GH_WHERE}: output is not a token")
    return token


def _permissions(
    transport: Transport, role: Role, repo_id: int
) -> Finding | Unavailable | None:
    path = f"repositories/{repo_id}"
    body = _get(transport, path)
    if isinstance(body, Unavailable):
        return Unavailable(f"history self-report on {role}: {body.reason}")
    perms = body.get("permissions") if isinstance(body, dict) else None
    if not isinstance(perms, dict):
        return Unavailable(f"history self-report on {role}: no permissions")
    granted = [k for k in _WRITE if perms.get(k) is True]
    if granted:
        detail = f"history token on {role} ({path}): {', '.join(granted)}"
        return Finding("token_write", role, clip_detail(detail))
    unknown = [k for k in _WRITE if type(perms.get(k)) is not bool]
    if unknown:
        missing = ", ".join(unknown)
        return Unavailable(f"history self-report on {role}: {missing} not a bool")
    return None


def _source_oid(
    transport: Transport, full_name: str, sha: str, where: str
) -> str | Unavailable:
    commit = _get(transport, f"{_repo_path(full_name)}/commits/{sha}")
    if isinstance(commit, Unavailable):
        return commit
    inner = commit.get("commit") if isinstance(commit, dict) else None
    tree = inner.get("tree") if isinstance(inner, dict) else None
    tree_sha = tree.get("sha") if isinstance(tree, dict) else None
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != sha
        or not isinstance(tree_sha, str)
        or not _SHA.fullmatch(tree_sha)
    ):
        return Unavailable(f"{where}: commit not read")
    listing = _get(transport, f"{_repo_path(full_name)}/git/trees/{tree_sha}")
    if isinstance(listing, Unavailable):
        return listing
    entries = listing.get("tree") if isinstance(listing, dict) else None
    if (
        not isinstance(listing, dict)
        or listing.get("truncated") is not False
        or not isinstance(entries, list)
    ):
        return Unavailable(f"{where}: tree not read completely")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == SOURCE_FILE:
            oid = entry.get("sha")
            if entry.get("type") != "blob" or not isinstance(oid, str):
                return Unavailable(f"{where}: not a file")
            if not _SHA.fullmatch(oid):
                return Unavailable(f"{where}: blob id is not a SHA")
            return oid
    return Unavailable(f"{where}: no such file")


def _blob(
    transport: Transport, full_name: str, oid: str, where: str
) -> bytes | Unavailable:
    body = _get(transport, f"{_repo_path(full_name)}/git/blobs/{oid}")
    if isinstance(body, Unavailable):
        return body
    if not isinstance(body, dict) or body.get("encoding") != "base64":
        return Unavailable(f"{where}: blob is not base64")
    try:
        raw = base64.b64decode(
            str(body.get("content", "")).replace("\n", ""), validate=True
        )
    except (binascii.Error, ValueError):
        return Unavailable(f"{where}: blob is not base64")
    if hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest() != oid:
        return Unavailable(f"{where}: bytes do not match blob {oid[:12]}")
    return raw


def _repo_path(full_name: str) -> str:
    owner, _, name = full_name.partition("/")
    return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _get(transport: Transport, path: str) -> object | Unavailable:
    """`GET <API>/<path>`; only HTTP 200 with a JSON body is an answer.

    Any other status (redirects included), a delivery failure or a body that
    is not JSON is `Unavailable` naming the path and the status or the
    exception type -- never exception text, which could carry a URL."""
    try:
        status, _, raw = transport("GET", f"{github.API}/{path}", {}, None)
    except Exception as exc:  # noqa: BLE001 - only the type name is kept
        return Unavailable(f"GET {path}: {type(exc).__name__}")
    if status != 200:
        return Unavailable(f"GET {path}: HTTP {status}")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return Unavailable(f"GET {path}: answer is not JSON")
