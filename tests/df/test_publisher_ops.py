"""`publish-review`, `read-token`, `self-check` and the reconciliation
(stage 2a, Task 9), through the publisher frame with a fake GitHub."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from df.config import InstallConfig
from df.publisher import github, ops, perimeter
from df.publisher.request import Response
from df.stage2_config import Product, dump_products
from tests.df.launch_world import put_record, record
from tests.df.publisher_world import SHA, SHA_B, World, raw_of

OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(OPENSSL is None, reason="openssl not found")
BOT = "df-app[bot]"
REPO = "acme/p7"
PR_URL = f"/repos/{REPO}/pulls/12"
PRODUCT = Product(7, REPO, "write", 42, 9, "bot", "/srv/7")
Reply = tuple[int, Mapping[str, str], bytes]


def _reply(obj: object, status: int = 200, **headers: str) -> Reply:
    return status, headers, json.dumps(obj).encode()


class FakeGitHub:
    """A GitHub with one PR; records every call and can fail on demand."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.calls: list[tuple[str, str, object]] = []
        self.tokens: list[dict[str, object]] = []
        self.pr_head = SHA
        self.pr_state = "open"
        self.pr_repo = 7
        self.reviews: list[dict[str, object]] = []
        self.post_mode = "ok"  # ok | timeout_created | timeout_lost | 422 | other
        self.list_down = False
        self.list_pages: list[list[dict[str, object]]] | None = None
        self.grant: dict[str, str] | None = None
        self.extra_repos = [7]
        self.slug_down = False
        self.intent_seen: list[bool] = []
        self.after_pr_get = lambda: None
        self.denied_apps: set[int] = set()
        self.apps_repos = {9: self.extra_repos, 10: [8]}

    def paths(self, method: str) -> list[str]:
        return [p for m, p, _ in self.calls if m == method]

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Reply:
        parts = urlsplit(url)
        path = parts.path
        data = json.loads(body) if body else None
        self.calls.append(
            (method, path + (f"?{parts.query}" if parts.query else ""), data)
        )
        return self.route(method, path, parts.query, headers, data)

    def route(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        data: dict[str, object] | None,
    ) -> Reply:
        if (method, path) == ("GET", "/app"):
            if self.slug_down:
                return 502, {}, b""
            return _reply({"slug": "df-app"})
        if (method, path) == ("GET", "/app/installations"):
            return self.installations(headers)
        if method == "POST" and path.endswith("/access_tokens"):
            return self.mint(path, data or {})
        if (method, path) == ("GET", "/installation/repositories"):
            token = headers["Authorization"].removeprefix("Bearer ghs_SECRETVALUE")
            ident = int(token) if token else 9
            repos = [{"id": r} for r in self.repos_of(ident)]
            return _reply({"total_count": len(repos), "repositories": repos})
        if (method, path) == ("GET", PR_URL):
            reply = self.pr()
            self.after_pr_get()
            return reply
        if (method, path) == ("POST", f"{PR_URL}/reviews"):
            return self.post(data or {})
        if (method, path) == ("GET", f"{PR_URL}/reviews"):
            return self.listing(query)
        raise AssertionError(f"unexpected call {method} {path}")

    def repos_of(self, ident: int) -> list[int]:
        return self.extra_repos if ident == 9 else self.apps_repos[ident]

    def installations(self, headers: Mapping[str, str]) -> Reply:
        claims = headers["Authorization"].split(".")[1]
        iss = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        if iss["iss"] in self.denied_apps:
            return _reply({"message": "denied"}, 401)
        ident = 9 if iss["iss"] == 42 else 10
        row = {"id": ident, "account": {"login": "acme"}, "permissions": {}}
        return _reply([row])

    def mint(self, path: str, data: dict[str, object]) -> Reply:
        self.tokens.append(data)
        ident = int(path.split("/")[3])
        asked = data.get("repository_ids")
        scoped = self.grant if asked is not None else None
        perms = dict(scoped or cast(dict[str, str], data.get("permissions") or {}))
        repos = [{"id": r} for r in asked] if isinstance(asked, list) else []
        expires = datetime.fromtimestamp(time.time() + 3000, UTC)
        return _reply(
            {
                "token": "ghs_SECRETVALUE" + ("" if ident == 9 else str(ident)),
                "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "permissions": perms,
                "repositories": repos,
            },
            201,
        )

    def pr(self) -> Reply:
        return _reply(
            {
                "state": self.pr_state,
                "base": {"repo": {"id": self.pr_repo}},
                "head": {"sha": self.pr_head},
            }
        )

    def post(self, data: dict[str, object]) -> Reply:
        self.intent_seen.append(bool(self.world.files("intents")))
        if self.post_mode == "422":
            return _reply({"message": "Unprocessable"}, 422)
        if self.post_mode == "timeout_lost":
            raise TimeoutError("lost")
        answer: dict[str, object] = {}
        if self.post_mode == "rewrite_commit":
            answer = {"commit_id": SHA_B}
        if self.post_mode == "pending":
            answer = {"state": "PENDING"}
        if self.post_mode == "no_marker":
            answer = {"body": "text only"}
        if self.post_mode == "marker_not_last":
            answer = {"body": f"{data['body']}\n\nmore"}
        review = {
            "id": 100 + len(self.reviews),
            "user": {"login": BOT, "type": "Bot"},
            "body": data["body"],
            "commit_id": data["commit_id"],
            "state": {"APPROVE": "APPROVED", "COMMENT": "COMMENTED"}.get(
                str(data["event"]), "CHANGES_REQUESTED"
            ),
        }
        self.reviews.append(review)
        if self.post_mode == "timeout_created":
            raise TimeoutError("created, then lost")
        if self.post_mode == "other":
            answer = {"user": {"login": "human", "type": "User"}}
        return _reply({**review, **answer}, 200)

    def listing(self, query: str) -> Reply:
        if self.list_down:
            return 503, {}, b""
        if self.list_pages is None:
            return _reply(self.reviews)
        page = int(parse_qs(query).get("page", ["1"])[0])
        link = {}
        if page < len(self.list_pages):
            nxt = f"{github.API}{PR_URL}/reviews?per_page=100&page={page + 1}"
            link = {"Link": f'<{nxt}>; rel="next"'}
        return _reply(self.list_pages[page - 1], **link)


@pytest.fixture(scope="session")
def key_text(tmp_path_factory: pytest.TempPathFactory) -> str:
    assert OPENSSL is not None
    path = tmp_path_factory.mktemp("ops-key") / "k.pem"
    subprocess.run(
        [OPENSSL, "genrsa", "-out", str(path), "2048"], check=True, capture_output=True
    )
    return path.read_text()


@pytest.fixture()
def gh(
    world: World,
    key_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> FakeGitHub:
    assert OPENSSL is not None
    lay = world.lay
    (lay.etc / "products.toml").write_text(dump_products({7: PRODUCT}))
    lay.secrets_dir.mkdir(parents=True, mode=0o700)
    (lay.secrets_dir / "app-42.pem").write_text(key_text)
    cli = {n: Path(f"/bin/{n}") for n in ("git", "uv", "tar")}
    cli["openssl"] = Path(OPENSSL)
    install = InstallConfig("o/m", "a" * 40, "core", cli)
    world.snap = replace(world.snap, install=install)
    lay.perimeter_dir.mkdir(mode=0o700)
    perimeter.create_db(lay)  # what roles setup's perimeter-init leaves
    fake = FakeGitHub(world)
    monkeypatch.setattr(github, "default_transport", fake)
    return fake


def review(
    world: World,
    capsys: pytest.CaptureFixture[str],
    **over: object,
) -> Response:
    return world.run("publish-review", raw_of("publish-review", **over), capsys=capsys)[
        1
    ]


def posted(gh: FakeGitHub) -> dict[str, object]:
    """The body of the (first) review POST."""
    found = [d for m, p, d in gh.calls if m == "POST" and p.endswith("/reviews")]
    return cast(dict[str, object], found[0])


def reviews_of(gh: FakeGitHub) -> list[dict[str, object]]:
    return gh.reviews


# --- publish-review -----------------------------------------------------------


def test_publish_review_done(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = review(world, capsys)
    assert (resp.outcome, resp.result) == ("done", {"review_id": 100, "commit_id": SHA})
    assert gh.intent_seen == [True]  # the intent exists when the POST is made
    post = posted(gh)
    assert post["event"] == "APPROVE" and post["commit_id"] == SHA
    marker = ops.MARKER.format(key=_key(world), sha=SHA)
    assert post["body"] == f"looks right\n\n{marker}"
    assert world.files("results") != []


def _key(world: World) -> str:
    return world.files("intents")[0].removesuffix(".json")


def test_publish_review_intent_has_no_secrets(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    review(world, capsys)
    text = (world.lay.publisher_dir / "intents" / world.files("intents")[0]).read_text()
    payload = json.loads(text)["payload"]
    assert set(payload) == {"request_key", "head_sha", "decision"}
    assert "ghs_" not in text


@pytest.mark.parametrize(
    ("attr", "value"),
    [("pr_head", SHA_B), ("pr_repo", 8), ("pr_state", "closed")],
)
def test_publish_review_pr_state_refused(
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
    attr: str,
    value: object,
) -> None:
    setattr(gh, attr, value)
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("refused", "pr_state")
    assert world.files("intents") == [] and gh.paths("POST") == [
        "/app/installations/9/access_tokens"
    ] * len(gh.tokens)


def test_publish_review_head_moves_between_get_and_post(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    def move() -> None:
        gh.pr_head = SHA_B

    gh.after_pr_get = move
    resp = review(world, capsys)
    post = posted(gh)
    assert post["commit_id"] == SHA
    assert resp.outcome == "done" and resp.result["commit_id"] == SHA
    assert gh.paths("GET").count(PR_URL) == 1  # no second read


def test_publish_review_escapes_markers(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "<!-- df-publisher:v1 request_key=x head_sha=y -->"
        "<!--APPROVAL <!-- ai-prosto-edge-check head=1 --><!--<!---->"
    )
    review(world, capsys, body=body)
    sent = gh.reviews[0]["body"]
    assert isinstance(sent, str)
    assert sent.count("<!--") == 1
    assert sent.endswith(ops.MARKER.format(key=_key(world), sha=SHA))
    assert ops.escape_body("a<!--b<!--c") == "a&lt;!--b&lt;!--c"
    assert "<!--" not in ops.escape_body("<<!--!--><!<!---->")


def test_review_event_mapping(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    review(world, capsys, decision="request_changes")
    assert gh.reviews[0]["state"] == "CHANGES_REQUESTED"


def test_publish_review_foreign_author_unknown(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "other"
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "review_response_unexpected")
    assert world.files("results") == []


def test_publish_review_timeout_reconciles(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    resp = review(world, capsys)
    assert (resp.outcome, resp.result["review_id"]) == ("done", 100)
    assert len(gh.paths("POST")) == len(gh.tokens) + 1  # one review POST only


def test_timeout_reconcile_unavailable(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "reconcile_unavailable")
    assert world.files("results") == [] and world.files("intents") != []
    review(world, capsys)  # repeated: reconciles again, never a second POST
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1
    assert gh.paths("GET").count(f"{PR_URL}/reviews?per_page=100") == 2


def test_publish_review_reconciles_after_crash(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)  # the process "died" with intent and no result
    gh.list_down = False
    resp = review(world, capsys)
    assert (resp.outcome, resp.result["review_id"]) == ("done", 100)
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1
    assert world.files("results") != []


def test_reconcile_no_marker_unknown(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_lost"
    for _ in range(2):
        resp = review(world, capsys)
        assert (resp.outcome, resp.reason) == ("unknown", "review_not_found")
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1  # the repeat reconciles only
    assert world.files("results") == []
    fresh = review(world, capsys, request_id="req-00000002")
    assert fresh.reason == "review_not_found"  # still lost; the post is new


def test_reconcile_duplicates_unknown(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.reviews.append({**gh.reviews[0], "id": 555})
    gh.list_down = False
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "duplicate_reviews")


def test_reconcile_state_mismatch_unknown(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    review(world, capsys)
    # done above; now a repeated request whose only review has another state
    world.files("results")
    gh.reviews[0]["state"] = "DISMISSED"
    key = _key(world)
    result = world.lay.publisher_dir / "results" / f"{key}.json"
    result.unlink()
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "review_mismatch")


def test_reconcile_ignores_foreign_marker_and_author(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.list_down = False
    gh.reviews[0]["user"] = {"login": "human", "type": "User"}
    resp = review(world, capsys)
    assert resp.reason == "review_not_found"


def test_reconcile_reads_every_page(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.list_down = False
    noise = [{"id": i, "user": {"login": "x", "type": "User"}} for i in range(3)]
    gh.list_pages = [noise, gh.reviews]
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("done", None)


def test_post_4xx_is_not_marked_not_done(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.post_mode = "422"
    resp = review(world, capsys)
    assert resp.outcome == "unknown"
    assert str(resp.reason).startswith("state_exists")
    assert "422" in str(resp.reason)


# --- self-check first, context ------------------------------------------------


def test_self_check_runs_first(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    review(world, capsys)
    paths = [p for _, p, _ in gh.calls]
    assert paths[0] == "/app/installations?per_page=100"
    assert paths.index(PR_URL) > paths.index("/installation/repositories?per_page=100")


def test_self_check_problem_refuses_before_intent(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.extra_repos = [7, 8]
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == (
        "refused",
        "app_installation_outside_products: 9",
    )
    assert world.files("intents") == [] and PR_URL not in gh.paths("GET")


def test_self_check_github_down_is_unknown(
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(*_a: object) -> Reply:
        raise OSError("down")

    monkeypatch.setattr(github, "default_transport", down)
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")
    assert world.files("intents") == []


def test_bad_key_refused(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    (world.lay.secrets_dir / "app-42.pem").write_text("not a key")
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("refused", "app_key_unusable")
    assert gh.calls == []


def test_product_unknown(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    put_record(world.fake, replace(record("run-3", "rel-a"), product=99))
    raw = raw_of("read-token", repository_id=99, run_id="run-3")
    resp = world.run("read-token", raw, capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("refused", "product_unknown")


def test_products_missing_or_invalid(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    path = world.lay.etc / "products.toml"
    path.write_text("not = [toml")
    assert review(world, capsys).reason == "products_config_invalid"
    path.unlink()
    assert review(world, capsys).reason == "product_unknown"


def test_openssl_not_configured(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    world.snap = replace(world.snap, install=None)
    assert review(world, capsys).reason == "openssl_not_configured"
    cfg = InstallConfig(
        "o/m", "a" * 40, "core", {n: Path(f"/{n}") for n in ("git", "uv", "tar")}
    )
    world.snap = replace(world.snap, install=cfg)
    assert review(world, capsys).reason == "openssl_not_configured"


def test_review_token_asks_pull_requests_write_only(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    review(world, capsys)
    scoped = [t for t in gh.tokens if "repository_ids" in t]
    assert scoped == [
        {"repository_ids": [7], "permissions": {"pull_requests": "write"}}
    ]


def test_app_slug_is_fetched_with_jwt(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.slug_down = True
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")
    assert world.files("intents") == []


# --- read-token ---------------------------------------------------------------


def test_read_token_result(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = world.run("read-token", raw_of("read-token"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["token"] == "ghs_SECRETVALUE"
    assert resp.result["repository_ids"] == [7]
    assert resp.result["permissions"] == {"contents": "read", "pull_requests": "read"}
    assert str(resp.result["expires_at"]).endswith("Z")
    assert [t for t in gh.tokens if "repository_ids" in t] == [
        {
            "repository_ids": [7],
            "permissions": {"contents": "read", "pull_requests": "read"},
        }
    ]


def test_read_token_scope(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.grant = {"contents": "write", "pull_requests": "read"}
    resp = world.run("read-token", raw_of("read-token"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("refused", "token_scope_mismatch")


def test_read_token_not_journaled(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    world.run("read-token", raw_of("read-token"), capsys=capsys)
    world.run("read-token", raw_of("read-token"), capsys=capsys)  # new issue
    assert world.files("intents") == [] and world.files("results") == []
    base = world.lay.publisher_dir
    for path in (p for p in base.rglob("*") if p.is_file()):
        assert "ghs_SECRETVALUE" not in path.read_text(), path
    last = world.journal()[-1]
    assert last["result"]["token"] == "<redacted>"
    assert last["outcome"] == "done"


# --- self-check operation -----------------------------------------------------


def test_self_check_operation(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == []
    found = cast(list[dict[str, object]], resp.result["installations"])
    assert found[0]["repository_ids"] == [7]


def test_self_check_operation_problems_and_failures(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.extra_repos = [7, 8]
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == ["app_installation_outside_products: 9"]
    (world.lay.secrets_dir / "app-42.pem").write_text("junk")
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == ["app_key_unusable: 42"]


def test_self_check_operation_without_products(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    (world.lay.etc / "products.toml").unlink()
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["installations"] == [] and resp.result["problems"] == []
    stats = cast(dict[str, int], resp.result["perimeter"])
    assert stats["events"] == 0 and stats["db_bytes"] > 0 and stats["free_bytes"] > 0
    assert gh.calls == []


def test_self_check_perimeter_unavailable(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    (world.lay.perimeter_dir / perimeter.DB_NAME).unlink()
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["perimeter"] is None
    assert resp.result["problems"] == ["perimeter_unavailable: lost"]


def test_self_check_perimeter_io_error(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    os.chmod(world.lay.perimeter_dir, 0)
    try:
        resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    finally:
        os.chmod(world.lay.perimeter_dir, 0o700)
    assert resp.outcome == "done"
    assert resp.result["perimeter"] is None
    assert resp.result["problems"] == ["perimeter_unavailable: io: Permission denied"]


def test_self_check_operation_github_down(
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(*_a: object) -> Reply:
        raise OSError("down")

    monkeypatch.setattr(github, "default_transport", down)
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")


# --- round 1: run binding, body size ------------------------------------------


def other_run(world: World) -> None:
    """Register `run-2` of product 8 (the requests below name product 7)."""
    rec = replace(record("run-2", "rel-a"), product=8)
    put_record(world.fake, rec)


@pytest.mark.parametrize("op", ["publish-review", "read-token"])
def test_run_of_another_product_is_refused(
    op: str, world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    other_run(world)
    resp = world.run(op, raw_of(op, run_id="run-2"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("refused", "run_product_mismatch")
    assert gh.calls == [] and world.files("intents") == []


def body_for(total: int) -> str:
    """A body (at most 60000 characters) whose published text is `total`
    characters long: only `<!--` grows (by 3) when escaped."""
    fixed = len(ops.MARKER.format(key="0" * 64, sha=SHA)) + 2
    rest = total - fixed
    raw = 59998 if (rest - 59998) % 3 == 0 else 59999
    k = (rest - raw) // 3
    assert 0 <= raw <= 60000 and 4 * k <= raw
    return "<!--" * k + "a" * (raw - 4 * k)


def test_body_limit_boundary(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    exact = review(world, capsys, body=body_for(65536))
    assert exact.outcome == "done"
    assert len(str(gh.reviews[0]["body"])) == 65536
    over = review(world, capsys, request_id="req-00000002", body=body_for(65537))
    assert (over.outcome, over.reason) == ("refused", "body_too_long")
    assert len(gh.reviews) == 1 and len(world.files("intents")) == 1


def test_body_too_long_comes_before_everything(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = review(world, capsys, body=body_for(65537))
    assert resp.reason == "body_too_long"
    assert gh.calls == [] and world.files("intents") == []


# --- round 1: safety predicates -----------------------------------------------


def lost_with(world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]) -> str:
    """The POST was lost for good; the request has an intent. Returns its key."""
    gh.post_mode = "timeout_lost"
    assert review(world, capsys).reason == "review_not_found"
    return _key(world)


def bot_review(key: str, sha: str = SHA, **over: object) -> dict[str, object]:
    return {
        "id": 900,
        "user": {"login": BOT, "type": "Bot"},
        "body": "x\n\n" + ops.MARKER.format(key=key, sha=sha),
        "commit_id": sha,
        "state": "APPROVED",
        **over,
    }


def test_reconcile_accepts_the_exact_review(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    key = lost_with(world, gh, capsys)
    gh.reviews.append(bot_review(key))
    resp = review(world, capsys)
    assert (resp.outcome, resp.result) == ("done", {"review_id": 900, "commit_id": SHA})
    assert resp.evidence["review_id"] == 900  # also in the evidence


@pytest.mark.parametrize(
    "mutate",
    [
        lambda k: bot_review("f" * 64),
        lambda k: bot_review(k[:-1]),
        lambda k: bot_review(k + "0"),
        lambda k: bot_review(k, user={"login": BOT, "type": "User"}),
        lambda k: bot_review(k, user={"login": "github-actions[bot]", "type": "Bot"}),
        lambda k: bot_review(k, user=None),
        lambda k: bot_review(
            k, body="x\n\n" + ops.MARKER.format(key=k, sha=SHA) + "\n\nmore"
        ),
    ],
    ids=[
        "other-key",
        "key-prefix",
        "key-extension",
        "user-type",
        "other-bot",
        "no-user",
        "marker-not-last",
    ],
)
def test_reconcile_ignores_what_is_not_ours(
    mutate: object,
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = lost_with(world, gh, capsys)
    gh.reviews.append(mutate(key))  # type: ignore[operator]
    assert review(world, capsys).reason == "review_not_found"


@pytest.mark.parametrize("ident", [True, "900", None])
def test_reconcile_review_id_must_be_an_int(
    ident: object,
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = lost_with(world, gh, capsys)
    gh.reviews.append(bot_review(key, id=ident))
    assert review(world, capsys).reason == "review_mismatch"


def test_reconcile_commit_mismatch(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    key = lost_with(world, gh, capsys)
    gh.reviews.append(bot_review(key, commit_id=SHA_B))
    assert review(world, capsys).reason == "review_mismatch"


@pytest.mark.parametrize(
    "mode", ["rewrite_commit", "pending", "no_marker", "marker_not_last", "other"]
)
def test_post_answer_is_checked(
    mode: str,
    world: World,
    gh: FakeGitHub,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh.post_mode = mode
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "review_response_unexpected")
    assert world.files("results") == []


def test_done_evidence_has_the_review(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = review(world, capsys)
    assert resp.evidence["review_id"] == 100 and resp.evidence["commit_id"] == SHA


def test_auth_repr_hides_the_token() -> None:
    assert "ghs_" not in repr(ops._Auth(BOT, "ghs_SECRET"))


# --- round 1: read-token self-check, two Apps ---------------------------------


def test_read_token_self_check_first(
    world: World, gh: FakeGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    gh.extra_repos = [7, 8]
    resp = world.run("read-token", raw_of("read-token"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == (
        "refused",
        "app_installation_outside_products: 9",
    )
    assert [t for t in gh.tokens if "repository_ids" in t] == []


def two_apps(world: World, key_text: str) -> None:
    second = Product(8, "acme/p8", "write", 43, 10, "bot", "/srv/8")
    path = world.lay.etc / "products.toml"
    path.write_text(dump_products({7: PRODUCT, 8: second}))
    (world.lay.secrets_dir / "app-43.pem").write_text(key_text)


def test_self_check_two_apps(
    world: World,
    gh: FakeGitHub,
    key_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    two_apps(world, key_text)
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done" and resp.result["problems"] == []
    found = resp.result["installations"]
    assert [i["id"] for i in found] == [9, 10]


def test_self_check_one_app_broken(
    world: World,
    gh: FakeGitHub,
    key_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    two_apps(world, key_text)
    (world.lay.secrets_dir / "app-43.pem").write_text("junk")
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == ["app_key_unusable: 43"]
    assert [i["id"] for i in resp.result["installations"]] == [9]
    gh.denied_apps = {43}
    (world.lay.secrets_dir / "app-43.pem").write_text(key_text)
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == ["app_unreadable: 43: github_401: denied"]
