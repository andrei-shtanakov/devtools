# ruff: noqa: ANN001, F811, F401
"""`publish-review`, `read-token`, `self-check` and the reconciliation
(stage 2a, Task 9), through the publisher frame with a fake GitHub."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from df.config import InstallConfig
from df.publisher import github, ops
from df.publisher.request import Response
from df.stage2_config import Product, dump_products
from tests.df.publisher_world import SHA, SHA_B, raw_of
from tests.df.test_publisher_main import World, world

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
            return _reply([{"id": 9, "account": {"login": "acme"}, "permissions": {}}])
        if method == "POST" and path.endswith("/access_tokens"):
            return self.mint(data or {})
        if (method, path) == ("GET", "/installation/repositories"):
            repos = [{"id": r} for r in self.extra_repos]
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

    def mint(self, data: dict[str, object]) -> Reply:
        self.tokens.append(data)
        asked = data.get("repository_ids")
        scoped = self.grant if asked is not None else None
        perms = dict(scoped or data.get("permissions") or {})  # type: ignore[call-overload]
        repos = [{"id": r} for r in asked] if isinstance(asked, list) else []
        expires = datetime.fromtimestamp(time.time() + 3000, UTC)
        return _reply(
            {
                "token": "ghs_SECRETVALUE",
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
            review = {**review, "user": {"login": "human", "type": "User"}}
        return _reply(review, 200)

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


def reviews_of(gh: FakeGitHub) -> list[dict[str, object]]:
    return gh.reviews


# --- publish-review -----------------------------------------------------------


def test_publish_review_done(world, gh, capsys) -> None:
    resp = review(world, capsys)
    assert (resp.outcome, resp.result) == ("done", {"review_id": 100, "commit_id": SHA})
    assert gh.intent_seen == [True]  # the intent exists when the POST is made
    post = next(d for m, p, d in gh.calls if m == "POST" and p.endswith("/reviews"))
    assert post["event"] == "APPROVE" and post["commit_id"] == SHA  # type: ignore[index]
    marker = ops.MARKER.format(key=_key(world), sha=SHA)
    assert post["body"] == f"looks right\n\n{marker}"  # type: ignore[index]
    assert world.files("results") != []


def _key(world: World) -> str:
    return world.files("intents")[0].removesuffix(".json")


def test_publish_review_intent_has_no_secrets(world, gh, capsys) -> None:
    review(world, capsys)
    text = (world.lay.publisher_dir / "intents" / world.files("intents")[0]).read_text()
    payload = json.loads(text)["payload"]
    assert set(payload) == {"request_key", "head_sha", "decision"}
    assert "ghs_" not in text


@pytest.mark.parametrize(
    ("attr", "value"),
    [("pr_head", SHA_B), ("pr_repo", 8), ("pr_state", "closed")],
)
def test_publish_review_pr_state_refused(world, gh, capsys, attr, value) -> None:
    setattr(gh, attr, value)
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("refused", "pr_state")
    assert world.files("intents") == [] and gh.paths("POST") == [
        "/app/installations/9/access_tokens"
    ] * len(gh.tokens)


def test_publish_review_head_moves_between_get_and_post(world, gh, capsys) -> None:
    def move() -> None:
        gh.pr_head = SHA_B

    gh.after_pr_get = move
    resp = review(world, capsys)
    post = next(d for m, p, d in gh.calls if m == "POST" and p.endswith("/reviews"))
    assert post["commit_id"] == SHA  # type: ignore[index]
    assert resp.outcome == "done" and resp.result["commit_id"] == SHA
    assert gh.paths("GET").count(PR_URL) == 1  # no second read


def test_publish_review_escapes_markers(world, gh, capsys) -> None:
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


def test_review_event_mapping(world, gh, capsys) -> None:
    review(world, capsys, decision="request_changes")
    assert gh.reviews[0]["state"] == "CHANGES_REQUESTED"


def test_publish_review_foreign_author_unknown(world, gh, capsys) -> None:
    gh.post_mode = "other"
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "review_response_unexpected")
    assert world.files("results") == []


def test_publish_review_timeout_reconciles(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    resp = review(world, capsys)
    assert (resp.outcome, resp.result["review_id"]) == ("done", 100)
    assert len(gh.paths("POST")) == len(gh.tokens) + 1  # one review POST only


def test_timeout_reconcile_unavailable(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "reconcile_unavailable")
    assert world.files("results") == [] and world.files("intents") != []
    review(world, capsys)  # repeated: reconciles again, never a second POST
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1
    assert gh.paths("GET").count(f"{PR_URL}/reviews?per_page=100") == 2


def test_publish_review_reconciles_after_crash(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)  # the process "died" with intent and no result
    gh.list_down = False
    resp = review(world, capsys)
    assert (resp.outcome, resp.result["review_id"]) == ("done", 100)
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1
    assert world.files("results") != []


def test_reconcile_no_marker_unknown(world, gh, capsys) -> None:
    gh.post_mode = "timeout_lost"
    for _ in range(2):
        resp = review(world, capsys)
        assert (resp.outcome, resp.reason) == ("unknown", "review_not_found")
    posts = [p for m, p, _ in gh.calls if m == "POST" and p.endswith("/reviews")]
    assert len(posts) == 1  # the repeat reconciles only
    assert world.files("results") == []
    fresh = review(world, capsys, request_id="req-00000002")
    assert fresh.reason == "review_not_found"  # still lost; the post is new


def test_reconcile_duplicates_unknown(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.reviews.append({**gh.reviews[0], "id": 555})
    gh.list_down = False
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "duplicate_reviews")


def test_reconcile_state_mismatch_unknown(world, gh, capsys) -> None:
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


def test_reconcile_ignores_foreign_marker_and_author(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.list_down = False
    gh.reviews[0]["user"] = {"login": "human", "type": "User"}
    resp = review(world, capsys)
    assert resp.reason == "review_not_found"


def test_reconcile_reads_every_page(world, gh, capsys) -> None:
    gh.post_mode = "timeout_created"
    gh.list_down = True
    review(world, capsys)
    gh.list_down = False
    noise = [{"id": i, "user": {"login": "x", "type": "User"}} for i in range(3)]
    gh.list_pages = [noise, gh.reviews]
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("done", None)


def test_post_4xx_is_not_marked_not_done(world, gh, capsys) -> None:
    gh.post_mode = "422"
    resp = review(world, capsys)
    assert resp.outcome == "unknown" and resp.reason.startswith("state_exists")  # type: ignore[union-attr]
    assert "422" in str(resp.reason)


# --- self-check first, context ------------------------------------------------


def test_self_check_runs_first(world, gh, capsys) -> None:
    review(world, capsys)
    paths = [p for _, p, _ in gh.calls]
    assert paths[0] == "/app/installations?per_page=100"
    assert paths.index(PR_URL) > paths.index("/installation/repositories?per_page=100")


def test_self_check_problem_refuses_before_intent(world, gh, capsys) -> None:
    gh.extra_repos = [7, 8]
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == (
        "refused",
        "app_installation_outside_products: 9",
    )
    assert world.files("intents") == [] and PR_URL not in gh.paths("GET")


def test_self_check_github_down_is_unknown(world, gh, capsys, monkeypatch) -> None:
    def down(*_a: object) -> Reply:
        raise OSError("down")

    monkeypatch.setattr(github, "default_transport", down)
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")
    assert world.files("intents") == []


def test_bad_key_refused(world, gh, capsys) -> None:
    (world.lay.secrets_dir / "app-42.pem").write_text("not a key")
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("refused", "app_key_unusable")
    assert gh.calls == []


def test_product_unknown(world, gh, capsys) -> None:
    resp = world.run(
        "read-token", raw_of("read-token", repository_id=99), capsys=capsys
    )[1]
    assert (resp.outcome, resp.reason) == ("refused", "product_unknown")


def test_products_missing_or_invalid(world, gh, capsys) -> None:
    path = world.lay.etc / "products.toml"
    path.write_text("not = [toml")
    assert review(world, capsys).reason == "products_config_invalid"
    path.unlink()
    assert review(world, capsys).reason == "product_unknown"


def test_openssl_not_configured(world, gh, capsys) -> None:
    world.snap = replace(world.snap, install=None)
    assert review(world, capsys).reason == "openssl_not_configured"
    cfg = InstallConfig(
        "o/m", "a" * 40, "core", {n: Path(f"/{n}") for n in ("git", "uv", "tar")}
    )
    world.snap = replace(world.snap, install=cfg)
    assert review(world, capsys).reason == "openssl_not_configured"


def test_review_token_asks_pull_requests_write_only(world, gh, capsys) -> None:
    review(world, capsys)
    scoped = [t for t in gh.tokens if "repository_ids" in t]
    assert scoped == [
        {"repository_ids": [7], "permissions": {"pull_requests": "write"}}
    ]


def test_app_slug_is_fetched_with_jwt(world, gh, capsys) -> None:
    gh.slug_down = True
    resp = review(world, capsys)
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")
    assert world.files("intents") == []


# --- read-token ---------------------------------------------------------------


def test_read_token_result(world, gh, capsys) -> None:
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


def test_read_token_scope(world, gh, capsys) -> None:
    gh.grant = {"contents": "write", "pull_requests": "read"}
    resp = world.run("read-token", raw_of("read-token"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("refused", "token_scope_mismatch")


def test_read_token_not_journaled(world, gh, capsys) -> None:
    world.run("read-token", raw_of("read-token"), capsys=capsys)
    world.run("read-token", raw_of("read-token"), capsys=capsys)  # new issue
    assert world.files("intents") == [] and world.files("results") == []
    base = world.lay.publisher_dir
    for path in (p for p in base.rglob("*") if p.is_file()):
        assert "ghs_SECRETVALUE" not in path.read_text(), path
    last = world.journal()[-1]
    assert last["result"]["token"] == "<redacted>"  # type: ignore[index]
    assert last["outcome"] == "done"


# --- self-check operation -----------------------------------------------------


def test_self_check_operation(world, gh, capsys) -> None:
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == []
    assert resp.result["installations"][0]["repository_ids"] == [7]  # type: ignore[index]


def test_self_check_operation_problems_and_failures(world, gh, capsys) -> None:
    gh.extra_repos = [7, 8]
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result["problems"] == ["app_installation_outside_products: 9"]
    (world.lay.secrets_dir / "app-42.pem").write_text("junk")
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("refused", "app_key_unusable")


def test_self_check_operation_without_products(world, gh, capsys) -> None:
    (world.lay.etc / "products.toml").unlink()
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert resp.outcome == "done"
    assert resp.result == {"installations": [], "problems": [], "perimeter": None}
    assert gh.calls == []


def test_self_check_operation_github_down(world, gh, capsys, monkeypatch) -> None:
    def down(*_a: object) -> Reply:
        raise OSError("down")

    monkeypatch.setattr(github, "default_transport", down)
    resp = world.run("self-check", raw_of("self-check"), capsys=capsys)[1]
    assert (resp.outcome, resp.reason) == ("unknown", "github_unavailable")
