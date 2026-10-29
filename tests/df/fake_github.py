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
  the repositories of its installation;
- the machine user's token reads `/user` and `/repos/<full_name>`;
- `GET /repos/<full_name>/pulls/<n>` and `.../reviews` answer the
  scenario's `pulls` and `reviews` (Task 15 reuses them).

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
from collections.abc import Iterator, Mapping
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

    def _serve(self, method: str) -> None:
        fake = self.server.fake
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization", "").removeprefix("Bearer ")
        path = urlsplit(self.path).path
        kind, status, answer = _answer(fake, method, path, auth, body)
        with fake.lock:
            fake.seen.append(Seen(method, path, kind, status))
        data = json.dumps(answer).encode("utf-8")
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
    fake: FakeGitHub, method: str, path: str, auth: str, body: bytes
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
        return ("installation", *_installation(sc, installation, method, path))
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
    expires = datetime.fromtimestamp(time.time() + TOKEN_LIFE - 60, UTC)
    return 201, {
        "token": token,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "permissions": asked.get("permissions", dict(found.permissions)),
        "repositories": [{"id": r} for r in repos],
    }


def _installation(
    sc: Scenario, ident: int, method: str, path: str
) -> tuple[int, object]:
    found = next((i for i in sc.installations if i.id == ident), None)
    if found is None or method != "GET":
        return 404, {"message": "Not Found"}
    if path == "/installation/repositories":
        repos = [{"id": r} for r in found.repository_ids]
        return 200, {"total_count": len(repos), "repositories": repos}
    return _repo_paths(sc, path, found.repository_ids)


def _machine(sc: Scenario, method: str, path: str) -> tuple[int, object]:
    if method != "GET":
        return 404, {"message": "Not Found"}
    if path == "/user":
        return 200, {"login": sc.login}
    return _repo_paths(sc, path, tuple(r.id for r in sc.repos))


def _repo_paths(
    sc: Scenario, path: str, visible: tuple[int, ...]
) -> tuple[int, object]:
    """`/repos/<owner>/<name>[/pulls/<n>[/reviews]]` of a visible repo."""
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
