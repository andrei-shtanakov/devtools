"""A local fake of the GitHub API (stage 2a, Task 14, ruling R14-1).

HTTP on `127.0.0.1` in the test process: `publisher.toml`'s `api_base`
points the publisher and `check-github` here (r2 N6). Every request is
recorded (`FakeGitHub.seen`). Answers come from a `Scenario` the test may
change between calls (an extra installation, say):

- App requests (`/app`, `/app/installations`, `.../access_tokens`) carry an
  RS256 JWT; it is checked with the test public key by `openssl dgst
  -verify` (header, `iss` = the App, `iat`/`exp` around now, the signature);
  anything else is `401`;
- an installation token minted here reads `/installation/repositories` and
  the repositories of its installation, and `DELETE /installation/token`
  revokes it (2b Task 10: the precheck client's `finally`);
- the machine user's token reads `/user` and `/repos/<full_name>`;
- `GET /repos/<full_name>/pulls/<n>` and `.../reviews` answer the
  scenario's `pulls` and `reviews` (Task 15 reuses them), and
  `GET /repos/<full_name>/commits/<sha>/pulls` its `commit_pulls` (the
  delivery facts the precheck reads, 2b Task 10);
- stage 3a (Task 14): every other read of a repository of `rest` (a
  `trust_sources.GitRest` over the product's git objects: commits, trees,
  contents, refs, compare), steward's GraphQL review facts of `pulls` and
  `reviews` (`POST /graphql`), the App's attestation (`POST .../reviews`)
  and the one `PUT .../merge`, which `on_merge` performs (a squash).

Nothing here is a real credential: the key is generated per run, the
tokens are fixture strings.
"""

from __future__ import annotations

import base64
import http.server
import json
import secrets
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

#: The life of a minted installation token (GitHub's: one hour).
TOKEN_LIFE = 3600
#: Seconds of clock slack on the JWT's `iat` and `exp`.
CLOCK_SLACK = 120


@dataclass
class Installation:
    """One installation of the App: its account, repositories and rights."""

    id: int
    account: str
    repository_ids: tuple[int, ...]
    permissions: Mapping[str, str]


@dataclass
class Repo:
    """A repository as `/repos/<full_name>` shows it to the machine user."""

    id: int
    full_name: str
    permissions: Mapping[str, bool]


@dataclass
class Scenario:
    """What the fake answers; tests change it between calls."""

    app_id: int
    app_slug: str
    installations: list[Installation]
    machine_token: str
    login: str
    repos: list[Repo]
    pulls: dict[tuple[str, int], dict[str, object]] = field(default_factory=dict)
    reviews: dict[tuple[str, int], list[dict[str, object]]] = field(
        default_factory=dict
    )
    commit_pulls: dict[tuple[str, str], list[dict[str, object]]] = field(
        default_factory=dict
    )
    #: The product repositories' git objects (`trust_sources.GitRest`).
    rest: object | None = None
    #: `(full_name, pr) -> merge commit` of an accepted `PUT .../merge`.
    on_merge: Callable[[str, int], str] | None = None


@dataclass(frozen=True)
class Seen:
    """One request: method, path (no query) and who it was from."""

    method: str
    path: str
    auth: str
    status: int


@dataclass
class FakeGitHub:
    """A running fake: its `url` (the `api_base`), scenario and requests."""

    url: str
    scenario: Scenario
    public_key: Path
    openssl: Path
    seen: list[Seen] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    #: Every installation token minted, revoked or not, in order.
    minted: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def paths(self, auth: str | None = None) -> list[str]:
        """`METHOD path` of the requests seen (of one `auth` kind)."""
        with self.lock:
            return [f"{s.method} {s.path}" for s in self.seen if auth in (None, s.auth)]


def b64url(text: str) -> bytes:
    """Decode unpadded base64url."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwt_valid(
    token: str, *, app_id: int, public_key: Path, openssl: Path, now: float
) -> bool:
    """An RS256 App JWT of `app_id`, valid at `now`, signed by the key whose
    public half is `public_key`."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        head = json.loads(b64url(parts[0]))
        claims = json.loads(b64url(parts[1]))
        signature = b64url(parts[2])
    except ValueError:
        return False
    if head != {"alg": "RS256", "typ": "JWT"} or not isinstance(claims, dict):
        return False
    iat, exp = claims.get("iat"), claims.get("exp")
    if claims.get("iss") != app_id or not all(isinstance(v, int) for v in (iat, exp)):
        return False
    assert isinstance(iat, int) and isinstance(exp, int)
    if not iat - CLOCK_SLACK <= now < exp + CLOCK_SLACK:
        return False
    return _signature_valid(f"{parts[0]}.{parts[1]}", signature, public_key, openssl)


def _signature_valid(
    signed: str, signature: bytes, public_key: Path, openssl: Path
) -> bool:
    with tempfile.NamedTemporaryFile(prefix="df-fake-sig.") as sig:
        sig.write(signature)
        sig.flush()
        argv = [str(openssl), "dgst", "-sha256", "-verify", str(public_key)]
        done = subprocess.run(
            [*argv, "-signature", sig.name],
            input=signed.encode("ascii"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    return done.returncode == 0


class _Handler(http.server.BaseHTTPRequestHandler):
    """Dispatches to `_answer`; the server's `fake` is the state."""

    server: _Server

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        self._serve("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - http.server's name
        self._serve("DELETE")

    def do_PUT(self) -> None:  # noqa: N802 - http.server's name
        self._serve("PUT")

    def _serve(self, method: str) -> None:
        fake = self.server.fake
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization", "").removeprefix("Bearer ")
        parts = urlsplit(self.path)
        path = parts.path
        query = f"?{parts.query}" if parts.query else ""
        kind, status, answer = _answer(fake, method, path, auth, body, query)
        with fake.lock:
            fake.seen.append(Seen(method, path, kind, status))
        # `None` is an empty body (`204 No Content`)
        data = b"" if answer is None else json.dumps(answer).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Quiet."""


class _Server(http.server.ThreadingHTTPServer):
    fake: FakeGitHub


def _answer(
    fake: FakeGitHub, method: str, path: str, auth: str, body: bytes, query: str = ""
) -> tuple[str, int, object]:
    """(who asked, status, JSON answer) of one request."""
    sc = fake.scenario
    if path.startswith("/app"):
        valid = jwt_valid(
            auth,
            app_id=sc.app_id,
            public_key=fake.public_key,
            openssl=fake.openssl,
            now=time.time(),
        )
        if not valid:
            return "bad", 401, {"message": "A JSON web token could not be decoded"}
        return ("jwt", *_app(fake, method, path, body))
    with fake.lock:
        installation = fake.tokens.get(auth)
    if installation is not None:
        if (method, path) == ("DELETE", "/installation/token"):
            with fake.lock:
                fake.tokens.pop(auth, None)
            return "installation", 204, None
        if method in ("POST", "PUT") and installation is not None:
            return ("installation", *_write(fake, installation, method, path, body))
        return ("installation", *_installation(sc, installation, method, path, query))
    if auth == sc.machine_token:
        return ("machine", *_machine(sc, method, path))
    return "bad", 401, {"message": "Bad credentials"}


def _app(fake: FakeGitHub, method: str, path: str, body: bytes) -> tuple[int, object]:
    sc = fake.scenario
    if (method, path) == ("GET", "/app"):
        return 200, {"id": sc.app_id, "slug": sc.app_slug}
    if (method, path) == ("GET", "/app/installations"):
        return 200, [
            {
                "id": i.id,
                "account": {"login": i.account},
                "permissions": dict(i.permissions),
            }
            for i in sc.installations
        ]
    words = path.strip("/").split("/")
    if method == "POST" and len(words) == 4 and words[3] == "access_tokens":
        return _mint(fake, words[2], body)
    return 404, {"message": "Not Found"}


def _mint(fake: FakeGitHub, ident: str, body: bytes) -> tuple[int, object]:
    """`POST /app/installations/<id>/access_tokens`: exactly what was asked."""
    found = next((i for i in fake.scenario.installations if str(i.id) == ident), None)
    if found is None:
        return 404, {"message": "Not Found"}
    asked = json.loads(body) if body else {}
    repos = asked.get("repository_ids", list(found.repository_ids))
    if not set(repos) <= set(found.repository_ids):
        return 422, {"message": "repository not in installation"}
    token = f"ghs_fixture{secrets.token_hex(16)}"
    with fake.lock:
        fake.tokens[token] = found.id
        fake.minted.append(token)
    expires = datetime.fromtimestamp(time.time() + TOKEN_LIFE - 60, UTC)
    return 201, {
        "token": token,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "permissions": asked.get("permissions", dict(found.permissions)),
        "repositories": [{"id": r} for r in repos],
    }


def _installation(
    sc: Scenario, ident: int, method: str, path: str, query: str = ""
) -> tuple[int, object]:
    found = next((i for i in sc.installations if i.id == ident), None)
    if found is None or method != "GET":
        return 404, {"message": "Not Found"}
    if path == "/installation/repositories":
        repos = [{"id": r} for r in found.repository_ids]
        return 200, {"total_count": len(repos), "repositories": repos}
    status, answer = _repo_paths(sc, path, found.repository_ids)
    if status == 404 and sc.rest is not None and _visible(sc, path, found):
        return sc.rest.answer(path.lstrip("/") + query, "")  # type: ignore[attr-defined]
    return status, answer


def _visible(sc: Scenario, path: str, found: Installation) -> bool:
    words = path.strip("/").split("/")
    full_name = "/".join(words[1:3])
    repo = next((r for r in sc.repos if r.full_name == full_name), None)
    return (
        words[:1] == ["repos"] and repo is not None and repo.id in found.repository_ids
    )


# ---------- stage 3a: the writes and GraphQL of an installation ----------


def _write(
    fake: FakeGitHub, ident: int, method: str, path: str, body: bytes
) -> tuple[int, object]:
    """`POST /graphql`, `POST .../pulls/<n>/reviews`, `PUT .../pulls/<n>/merge`."""
    sc = fake.scenario
    asked = json.loads(body) if body else {}
    if (method, path) == ("POST", "/graphql"):
        return _graphql(sc, asked)
    words = path.strip("/").split("/")
    if len(words) != 6 or words[0] != "repos" or words[3] != "pulls":
        return 404, {"message": "Not Found"}
    key = ("/".join(words[1:3]), int(words[4]) if words[4].isdigit() else -1)
    pull = sc.pulls.get(key)
    if pull is None:
        return 404, {"message": "Not Found"}
    bot = f"{sc.app_slug}[bot]"
    if (method, words[5]) == ("POST", "reviews"):
        with fake.lock:
            rows = sc.reviews.setdefault(key, [])
            review = {
                "id": 9000 + len(rows),
                "user": {"login": bot, "type": "Bot"},
                "state": "APPROVED" if asked.get("event") == "APPROVE" else "COMMENTED",
                "commit_id": asked.get("commit_id"),
                "submitted_at": _now(),
                "body": asked.get("body", ""),
            }
            rows.append(review)
        return 200, review
    if (method, words[5]) == ("PUT", "merge"):
        return _merge(fake, key, pull, asked, bot)
    return 404, {"message": "Not Found"}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge(
    fake: FakeGitHub,
    key: tuple[str, int],
    pull: dict[str, object],
    asked: Mapping[str, object],
    bot: str,
) -> tuple[int, object]:
    """The one `PUT`: the head the request pins, a squash by `on_merge`."""
    head = pull.get("head")
    if pull.get("state") != "open" or fake.scenario.on_merge is None:
        return 405, {"message": "Pull Request is not mergeable"}
    if not isinstance(head, dict) or asked.get("sha") != head.get("sha"):
        return 409, {"message": "Head branch was modified"}
    sha = fake.scenario.on_merge(*key)
    pull.update(
        state="closed",
        merged=True,
        merged_at=_now(),
        merge_commit_sha=sha,
        merged_by={"login": bot, "type": "Bot"},
    )
    return 200, {"sha": sha, "merged": True, "message": "Pull Request merged"}


_PAGE = {"hasNextPage": False, "endCursor": None}


def _graphql(sc: Scenario, asked: Mapping[str, object]) -> tuple[int, object]:
    """Steward's review facts (`StewardV3Reviews`, `StewardV3Timeline`) of
    `pulls` and `reviews`; any other query is an error."""
    query = str(asked.get("query", ""))
    variables = asked.get("variables")
    if not isinstance(variables, dict):
        return 400, {"message": "no variables"}
    if query.startswith("query StewardV3Blobs"):
        return _blobs(sc, variables)
    key = (f"{variables.get('owner')}/{variables.get('name')}", variables.get("number"))
    pull = sc.pulls.get(key)  # type: ignore[arg-type]
    if pull is None:
        return 200, {"data": {"repository": {"pullRequest": None}}}
    head, base = pull.get("head"), pull.get("base")
    state = "MERGED" if pull.get("merged") else str(pull.get("state", "")).upper()
    if query.startswith("query StewardV3Reviews"):
        rows = sc.reviews.get(key, [])  # type: ignore[arg-type]
        nodes = [_review_node(r) for r in rows]
        found = {
            "state": state,
            "headRefOid": head.get("sha") if isinstance(head, dict) else None,
            "baseRefName": base.get("ref") if isinstance(base, dict) else None,
            "reviews": {"nodes": nodes, "pageInfo": _PAGE},
        }
        return 200, {"data": {"repository": {"pullRequest": found}}}
    if query.startswith("query StewardV3Timeline"):
        items: list[dict[str, object]] = []
        if pull.get("merged"):
            items.append(
                {
                    "__typename": "MergedEvent",
                    "createdAt": pull.get("merged_at"),
                    "commit": {"oid": pull.get("merge_commit_sha")},
                }
            )
        found = {"timelineItems": {"nodes": items, "pageInfo": _PAGE}}
        return 200, {"data": {"repository": {"pullRequest": found}}}
    if query.startswith("query StewardV3Delivery"):
        merge = pull.get("merge_commit_sha")
        by = pull.get("merged_by")
        found = {
            "number": pull.get("number"),
            "state": state,
            "merged": bool(pull.get("merged")),
            "mergedAt": pull.get("merged_at"),
            "mergeCommit": {"oid": merge} if merge else None,
            "mergedBy": _actor(by) if isinstance(by, dict) else None,
            "headRefOid": head.get("sha") if isinstance(head, dict) else None,
            "baseRefName": base.get("ref") if isinstance(base, dict) else None,
        }
        return 200, {"data": {"repository": {"pullRequest": found}}}
    return 200, {"errors": [{"message": f"fake: unknown query {query[:40]!r}"}]}


def _actor(user: Mapping[str, object]) -> dict[str, object]:
    login = str(user.get("login", ""))
    return {"login": login.removesuffix("[bot]"), "__typename": user.get("type")}


def _blobs(sc: Scenario, variables: Mapping[str, object]) -> tuple[int, object]:
    """`StewardV3Blobs`: the commits `mc`/`hc` and each `m<i>`/`h<i>`
    expression's blob, from the product's git objects (`null`: absent)."""
    from tests.df.trust_sources import git

    full_name = f"{variables.get('owner')}/{variables.get('name')}"
    repos = getattr(sc.rest, "repos", [])
    repo = next((r for r in repos if r.full_name == full_name), None)
    if repo is None:
        return 200, {"data": {"repository": None}}

    def obj(expr: object) -> dict[str, object] | None:
        try:
            oid = git(repo.work, "rev-parse", "--verify", f"{expr}").strip()
            kind = git(repo.work, "cat-file", "-t", oid).strip()
        except OSError:
            return None
        return {"__typename": kind.capitalize(), "oid": oid}

    found: dict[str, object] = {
        "mc": obj(f"{variables.get('merge')}^{{commit}}"),
        "hc": obj(f"{variables.get('head')}^{{commit}}"),
    }
    for key, value in variables.items():
        if key[:1] in ("m", "h") and key[1:].isdigit():
            found[key] = obj(value)
    return 200, {"data": {"repository": found}}


def _review_node(review: Mapping[str, object]) -> dict[str, object]:
    """A REST review as steward's GraphQL node (a bot's login without `[bot]`)."""
    user = review.get("user")
    login = str(user.get("login", "")) if isinstance(user, dict) else ""
    kind = str(user.get("type", "User")) if isinstance(user, dict) else "User"
    return {
        "databaseId": review.get("id"),
        "state": review.get("state"),
        "submittedAt": review.get("submitted_at"),
        "commit": {"oid": review.get("commit_id")},
        "author": {"login": login.removesuffix("[bot]"), "__typename": kind},
    }


def _machine(sc: Scenario, method: str, path: str) -> tuple[int, object]:
    if method != "GET":
        return 404, {"message": "Not Found"}
    if path == "/user":
        return 200, {"login": sc.login}
    return _repo_paths(sc, path, tuple(r.id for r in sc.repos))


def _repo_paths(
    sc: Scenario, path: str, visible: tuple[int, ...]
) -> tuple[int, object]:
    """`/repos/<owner>/<name>[/pulls/<n>[/reviews] | /commits/<sha>/pulls]` of
    a visible repo."""
    words = path.strip("/").split("/")
    if words[:1] != ["repos"] or len(words) < 3:
        return 404, {"message": "Not Found"}
    full_name = f"{words[1]}/{words[2]}"
    repo = next((r for r in sc.repos if r.full_name == full_name), None)
    if repo is None or repo.id not in visible:
        return 404, {"message": "Not Found"}
    rest = words[3:]
    if not rest:
        return 200, {
            "id": repo.id,
            "full_name": repo.full_name,
            "permissions": dict(repo.permissions),
        }
    if len(rest) >= 2 and rest[0] == "pulls" and rest[1].isdigit():
        key = (full_name, int(rest[1]))
        if len(rest) == 2 and key in sc.pulls:
            return 200, sc.pulls[key]
        if rest[2:] == ["reviews"] and key in sc.pulls:
            return 200, sc.reviews.get(key, [])
    if len(rest) == 3 and rest[0] == "commits" and rest[2] == "pulls":
        pulls = sc.commit_pulls.get((full_name, rest[1]))
        if pulls is not None:
            return 200, pulls
    return 404, {"message": "Not Found"}


@contextmanager
def serve_fake_github(
    scenario: Scenario, *, public_key: Path, openssl: Path
) -> Iterator[FakeGitHub]:
    """The fake on `127.0.0.1:<free port>` until the block ends."""
    server = _Server(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[:2]
    fake = FakeGitHub(f"http://{host!s}:{port}", scenario, public_key, openssl)
    server.fake = fake
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
