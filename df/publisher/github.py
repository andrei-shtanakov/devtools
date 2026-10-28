"""GitHub App client of the publisher (spec stage 2a, Task 8).

The publisher signs an App JWT with the key it holds (the signature is made
by `openssl dgst`, the key path is in argv, the secret is its content), mints
installation tokens and reads the installations of the App. Everything goes
through a `Transport`, so tests never touch the network. No exception text
carries a token or a JWT.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import IO

from df import platform
from df.stage2_config import Product

API = "https://api.github.com"
#: Seconds per socket operation (connect, each read), not for a whole answer.
TIMEOUT = 20.0
#: Largest answer body read; more is `GitHubUnavailable`.
MAX_ANSWER = 8 << 20
USER_AGENT = "darkfactory-publisher"
#: Seconds the JWT is backdated (clock drift) and lives.
JWT_BACKDATE = 60
JWT_LIFETIME = 540
#: Longest life of an installation token.
TOKEN_LIFETIME = 3600
#: Seconds of clock difference tolerated on the token expiry.
CLOCK_SLACK = 300
PER_PAGE = 100
MAX_PAGES = 50
_MESSAGE_LIMIT = 200
_UTC_SUFFIX = {True: "+00:00", False: ""}
_LINK_VALUE = re.compile(r"<([^>]*)>([^<]*)")
_BARE_URL = re.compile(r"<[^>]*>")
_NEXT_WORD = re.compile(r"\bnext\b", re.IGNORECASE)

Transport = Callable[
    [str, str, Mapping[str, str], bytes | None],
    tuple[int, Mapping[str, str], bytes],
]
Clock = Callable[[], float]


class GitHubUnavailable(Exception):
    """GitHub did not answer usefully (5xx, network, wrong shape): retry later."""


class SigningFailed(Exception):
    """The App JWT could not be signed: a local fault (key, openssl), not GitHub's."""


class TooManyItems(Exception):
    """A listing is longer than the caller allows."""


class GitHubRefused(Exception):
    """GitHub, or the client checking its answer, said no."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Token:
    """An installation token; `value` never appears in `repr`."""

    value: str = field(repr=False)
    expires_at: datetime
    permissions: Mapping[str, str]
    repository_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "permissions", MappingProxyType(dict(self.permissions))
        )


@dataclass(frozen=True)
class Installation:
    """One installation of the App with its repositories and rights."""

    id: int
    account: str
    repository_ids: tuple[int, ...]
    permissions: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "permissions", MappingProxyType(dict(self.permissions))
        )


@dataclass(frozen=True)
class PerimeterStats:
    """Operational limits of the perimeter database (spec 6.1)."""

    events: int
    db_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class SelfCheckResult:
    """What `self-check` answers: installations, problems, perimeter limits."""

    installations: tuple[Installation, ...]
    problems: tuple[str, ...]
    perimeter: PerimeterStats | None

    def to_json(self) -> dict[str, object]:
        """The `result` object of the `self-check` response."""
        perimeter = self.perimeter
        return {
            "installations": [
                {
                    "id": i.id,
                    "account": i.account,
                    "repository_ids": list(i.repository_ids),
                    "permissions": dict(i.permissions),
                }
                for i in self.installations
            ],
            "problems": list(self.problems),
            "perimeter": None
            if perimeter is None
            else {
                "events": perimeter.events,
                "db_bytes": perimeter.db_bytes,
                "free_bytes": perimeter.free_bytes,
            },
        }

    @classmethod
    def from_json(cls, data: object) -> SelfCheckResult:
        """Parse a `to_json` object; anything else is `ValueError`."""
        obj = _mapping(data, "result", {"installations", "problems", "perimeter"})
        installations = tuple(
            _installation_json(x) for x in _list(obj, "installations")
        )
        problems = tuple(_text(x, "problem") for x in _list(obj, "problems"))
        raw = obj["perimeter"]
        perimeter = None
        if raw is not None:
            p = _mapping(raw, "perimeter", {"events", "db_bytes", "free_bytes"})
            perimeter = PerimeterStats(
                _int(p["events"], "events"),
                _int(p["db_bytes"], "db_bytes"),
                _int(p["free_bytes"], "free_bytes"),
            )
        return cls(installations, problems, perimeter)


# --- JSON shape helpers -------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _mapping(value: object, name: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} must be an object with keys {sorted(keys)}")
    return value


def _list(obj: Mapping[str, object], name: str) -> list[object]:
    value = obj[name]
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _installation_json(value: object) -> Installation:
    keys = {"id", "account", "repository_ids", "permissions"}
    obj = _mapping(value, "installation", keys)
    perms = obj["permissions"]
    if not isinstance(perms, dict):
        raise ValueError("permissions must be an object")
    return Installation(
        _int(obj["id"], "id"),
        _text(obj["account"], "account"),
        tuple(_int(x, "repository id") for x in _list(obj, "repository_ids")),
        {
            _text(k, "permission"): _text(v, "permission level")
            for k, v in perms.items()
        },
    )


# --- transport ----------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not a hop: the token must not follow it."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def build_opener() -> urllib.request.OpenerDirector:
    """The default opener: no proxy from the environment, no redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _read_limited(stream: IO[bytes]) -> bytes:
    data = stream.read(MAX_ANSWER + 1)
    if len(data) > MAX_ANSWER:
        raise GitHubUnavailable("answer too large")
    return data


def default_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> tuple[int, Mapping[str, str], bytes]:
    """`urllib` without env proxies; 4xx/5xx are answers, network errors raise."""
    req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with build_opener().open(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers), _read_limited(resp)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, dict(exc.headers), _read_limited(exc)
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubUnavailable(f"network: {type(exc).__name__}") from exc


def _headers(auth: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {auth}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }


def _message(body: bytes) -> str:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return "no message"
    text = data.get("message") if isinstance(data, dict) else None
    return text[:_MESSAGE_LIMIT] if isinstance(text, str) else "no message"


def _send(
    transport: Transport,
    method: str,
    url: str,
    auth: str,
    body: object | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    payload = None if body is None else json.dumps(body).encode()
    headers = _headers(auth)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, resp_headers, raw = transport(method, url, headers, payload)
    except (GitHubUnavailable, GitHubRefused):
        raise
    except Exception as exc:  # noqa: BLE001 - only the type name is kept
        raise GitHubUnavailable(f"network: {type(exc).__name__}") from None
    if 400 <= status < 500:
        raise GitHubRefused(_message(raw), status)
    if not 200 <= status < 300:
        raise GitHubUnavailable(f"http {status}")
    return status, resp_headers, raw


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise GitHubUnavailable("answer is not JSON") from exc


def call(
    method: str,
    path: str,
    token: str,
    body: object | None = None,
    *,
    transport: Transport = default_transport,
    api_base: str = API,
) -> tuple[int, object]:
    """One API call; only 2xx succeeds. 4xx is `GitHubRefused(message, status)`;
    5xx, redirects, other statuses and any transport error are
    `GitHubUnavailable`. An empty answer body is `None`."""
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    url = f"{api_base.rstrip('/')}{path}"
    status, _, raw = _send(transport, method, url, token, body)
    return status, (_json(raw) if raw.strip() else None)


# --- JWT ----------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _compact(obj: Mapping[str, object]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def app_jwt(app_id: int, key: Path, *, openssl: Path, now: int) -> str:
    """An RS256 App JWT (`iat = now - 60`, `exp = now + 540`, `iss = app_id`)."""
    head = _b64(_compact({"alg": "RS256", "typ": "JWT"}))
    claims = {"iat": now - JWT_BACKDATE, "exp": now + JWT_LIFETIME, "iss": app_id}
    signing_input = f"{head}.{_b64(_compact(claims))}"
    argv = [str(openssl), "dgst", "-sha256", "-sign", str(key)]
    result = platform.run_with_input(argv, signing_input.encode(), timeout=TIMEOUT)
    failed = result.timed_out or result.stdout_truncated or not result.launched
    if failed or result.returncode != 0 or not result.stdout:
        raise SigningFailed("jwt signing failed")
    return f"{signing_input}.{_b64(result.stdout)}"


# --- tokens and listings ------------------------------------------------------


def _expires_at(value: object, now: float) -> datetime:
    if not isinstance(value, str):
        raise GitHubRefused("token_expiry_invalid")
    try:
        parsed = datetime.fromisoformat(
            value.removesuffix("Z") + _UTC_SUFFIX[value.endswith("Z")]
        )
    except ValueError as exc:
        raise GitHubRefused("token_expiry_invalid") from exc
    if parsed.tzinfo is None:
        raise GitHubRefused("token_expiry_invalid")
    try:
        at = parsed.astimezone(UTC)
    except OverflowError as exc:
        raise GitHubRefused("token_expiry_invalid") from exc
    if not now < at.timestamp() <= now + TOKEN_LIFETIME + CLOCK_SLACK:
        raise GitHubRefused("token_expiry_invalid")
    return at


def _id_list(items: object, what: str) -> tuple[int, ...]:
    if not isinstance(items, list):
        raise GitHubUnavailable(f"{what}: not an array")
    ids: list[int] = []
    for item in items:
        if not isinstance(item, dict) or not _is_int(item.get("id")):
            raise GitHubUnavailable(f"{what}: unexpected item")
        ids.append(item["id"])
    return tuple(ids)


def _levels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise GitHubUnavailable("permissions: not an object")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise GitHubUnavailable("permissions: unexpected item")
    return dict(value)


def _scope_ok(granted: Mapping[str, str], asked: Mapping[str, str]) -> bool:
    """Equal to what was asked, or plus exactly `metadata: read` (GitHub adds it)."""
    if "metadata" in asked:
        return dict(granted) == dict(asked)
    return dict(granted) in (dict(asked), {**asked, "metadata": "read"})


def installation_token(
    jwt: str,
    installation_id: int,
    *,
    repository_ids: list[int] | None,
    permissions: Mapping[str, str] | None,
    transport: Transport = default_transport,
    api_base: str = API,
    clock: Clock = time.time,
) -> Token:
    """Mint an installation token; what GitHub granted must equal what was
    asked (`token_scope_mismatch`) and expire within the hour."""
    if repository_ids is not None and not repository_ids:
        raise ValueError("repository_ids must not be empty")
    request: dict[str, object] = {}
    if repository_ids is not None:
        request["repository_ids"] = list(repository_ids)
    if permissions is not None:
        request["permissions"] = dict(permissions)
    path = f"/app/installations/{installation_id}/access_tokens"
    now = clock()
    _, data = call(
        "POST", path, jwt, request or None, transport=transport, api_base=api_base
    )
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        raise GitHubUnavailable("access_tokens: unexpected answer")
    if repository_ids is not None and "repositories" not in data:
        raise GitHubRefused("token_scope_mismatch")
    granted_repos = _id_list(data.get("repositories", []), "repositories")
    granted = _levels(data.get("permissions", {}))
    if repository_ids is not None and set(granted_repos) != set(repository_ids):
        raise GitHubRefused("token_scope_mismatch")
    if permissions is not None and not _scope_ok(granted, permissions):
        raise GitHubRefused("token_scope_mismatch")
    expires = _expires_at(data.get("expires_at"), now)
    return Token(data["token"], expires, granted, granted_repos)


def _is_next(params: str) -> bool:
    for param in params.split(";"):
        key, _, value = param.partition("=")
        if key.strip().lower() == "rel":
            return "next" in value.strip().strip('"').lower().split()
    return False


def _next_url(headers: Mapping[str, str], api_base: str) -> str | None:
    link = next((v for k, v in headers.items() if k.lower() == "link"), None)
    if not link:
        return None
    urls = [
        u
        for u, params in _LINK_VALUE.findall(link)
        if _is_next(params.partition(",")[0])
    ]
    if not urls:
        if _NEXT_WORD.search(_BARE_URL.sub("", link)):
            raise GitHubUnavailable("unreadable Link header")
        return None
    if not urls[0].startswith(f"{api_base.rstrip('/')}/"):
        raise GitHubUnavailable("next page is off the API host")
    return urls[0]


def _pages(
    path: str,
    token: str,
    transport: Transport,
    api_base: str,
    max_items: int | None = None,
) -> list[object]:
    """Every page of a listing, in order; any failure is not a partial list.
    Past `max_items` array items it stops at once with `TooManyItems`."""
    base = api_base.rstrip("/")
    url: str | None = f"{base}{path}?per_page={PER_PAGE}"
    pages: list[object] = []
    seen = 0
    while url is not None:
        if len(pages) >= MAX_PAGES:
            raise GitHubUnavailable("too many pages")
        _, headers, raw = _send(transport, "GET", url, token)
        page = _json(raw)
        pages.append(page)
        seen += len(page) if isinstance(page, list) else 0
        if max_items is not None and seen > max_items:
            raise TooManyItems(path)
        url = _next_url(headers, base)
    return pages


def paged_array(
    path: str,
    token: str,
    transport: Transport = default_transport,
    *,
    api_base: str = API,
    max_items: int | None = None,
) -> list[object]:
    """Every item of a listing whose pages are arrays, in order; a page that
    is not an array is `GitHubUnavailable`; more than `max_items` items is
    `TooManyItems`, raised before the next page is fetched."""
    items: list[object] = []
    for page in _pages(path, token, transport, api_base, max_items):
        if not isinstance(page, list):
            raise GitHubUnavailable(f"{path}: not an array")
        items.extend(page)
    return items


def app_slug(
    jwt: str,
    *,
    transport: Transport = default_transport,
    api_base: str = API,
) -> str:
    """The App's slug (`GET /app`); its bot is `<slug>[bot]`."""
    _, data = call("GET", "/app", jwt, transport=transport, api_base=api_base)
    slug = data.get("slug") if isinstance(data, dict) else None
    if not isinstance(slug, str) or not slug:
        raise GitHubUnavailable("app: unexpected answer")
    return slug


def _installation_row(item: object) -> tuple[int, str, dict[str, str]]:
    if not isinstance(item, dict) or not _is_int(item.get("id")):
        raise GitHubUnavailable("installations: unexpected item")
    account = item.get("account")
    login = account.get("login") if isinstance(account, dict) else None
    if not isinstance(login, str):
        raise GitHubUnavailable("installations: unexpected item")
    return item["id"], login, _levels(item.get("permissions", {}))


def installations(
    jwt: str,
    transport: Transport = default_transport,
    *,
    api_base: str = API,
) -> list[Installation]:
    """All installations of the App (`repository_ids` empty: see
    `installation_repos`). The answer is an array on every page."""
    found: list[Installation] = []
    for page in _pages("/app/installations", jwt, transport, api_base):
        if not isinstance(page, list):
            raise GitHubUnavailable("installations: not an array")
        for item in page:
            ident, login, perms = _installation_row(item)
            found.append(Installation(ident, login, (), perms))
    return found


def installation_repos(
    token: str,
    transport: Transport = default_transport,
    *,
    api_base: str = API,
) -> list[int]:
    """Ids of every repository the token sees; the sum of the pages must
    equal `total_count` of the last page."""
    ids: list[int] = []
    total = -1
    for page in _pages("/installation/repositories", token, transport, api_base):
        if not isinstance(page, dict) or not _is_int(page.get("total_count")):
            raise GitHubUnavailable("repositories: unexpected answer")
        ids.extend(_id_list(page.get("repositories"), "repositories"))
        total = page["total_count"]
    if len(ids) != total:
        raise GitHubUnavailable("repositories: total_count mismatch")
    return ids


# --- self-check ---------------------------------------------------------------


def _problems(
    found: list[Installation], products: Mapping[int, Product], app_id: int
) -> tuple[str, ...]:
    allowed = {p.repository_id for p in products.values() if p.app_id == app_id}
    return tuple(
        f"app_installation_outside_products: {i.id}"
        for i in found
        if not set(i.repository_ids) <= allowed
    )


def _repos_of(
    jwt: str, ident: int, transport: Transport, api_base: str, clock: Clock
) -> list[int]:
    """Repositories of one installation, read with a metadata-only token."""
    token = installation_token(
        jwt,
        ident,
        repository_ids=None,
        permissions={"metadata": "read"},
        transport=transport,
        api_base=api_base,
        clock=clock,
    )
    return installation_repos(token.value, transport, api_base=api_base)


def self_check(
    products: Mapping[int, Product],
    app_id: int,
    key: Path,
    *,
    openssl: Path,
    transport: Transport = default_transport,
    api_base: str = API,
    clock: Clock = time.time,
) -> SelfCheckResult:
    """All installations of the App with their repositories and rights; an
    installation reaching a repository outside the products of this App is a
    problem. `perimeter` is the publisher's to fill (`None` here)."""
    jwt = app_jwt(app_id, key, openssl=openssl, now=int(clock()))
    found: list[Installation] = []
    unreadable: list[str] = []
    for inst in installations(jwt, transport, api_base=api_base):
        try:
            repos = _repos_of(jwt, inst.id, transport, api_base, clock)
        except GitHubRefused:
            unreadable.append(f"app_installation_unreadable: {inst.id}")
            repos = []
        found.append(
            Installation(inst.id, inst.account, tuple(repos), inst.permissions)
        )
    problems = (*unreadable, *_problems(found, products, app_id))
    return SelfCheckResult(tuple(found), problems, None)
