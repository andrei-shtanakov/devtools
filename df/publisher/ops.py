"""Publisher operations that talk to GitHub as the App: `publish-review`,
`read-token`, `self-check` (spec 4.4, 5.4; stage 2a, Task 9).

Every operation first builds its `OpContext` and runs the App `self_check`
(decision 10); both happen before any intent is written, so their refusals
are truthful. A handler writes its intent strictly before the external POST
and never saves a result itself: the frame does (R23, R24 of Task 7).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from df.config import ConfigError, read_optional
from df.paths import Layout
from df.publisher import github, store
from df.publisher.github import (
    GitHubRefused,
    GitHubUnavailable,
    SelfCheckResult,
    SigningFailed,
    Transport,
)
from df.publisher.request import Refused, Request, Response
from df.stage2_config import Product, parse_products

if TYPE_CHECKING:
    from df.publisher.main import Context

MARKER = "<!-- df-publisher:v1 request_key={key} head_sha={sha} -->"
EVENTS = {
    "approve": "APPROVE",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}
STATES = {
    "approve": "APPROVED",
    "request_changes": "CHANGES_REQUESTED",
    "comment": "COMMENTED",
}
#: The review token only writes reviews; reading the PR and its reviews needs
#: no more (`pull_requests: write` includes read, `contents` is not touched).
REVIEW_PERMISSIONS = {"pull_requests": "write"}
#: What `read-token` hands the reviewer (spec 4.4).
READ_PERMISSIONS = {"contents": "read", "pull_requests": "read"}
_ESCAPED = "&lt;!--"


@dataclass(frozen=True)
class OpContext:
    """What an operation on one product needs, resolved before any intent."""

    layout: Layout
    product: Product
    app_key: Path
    openssl: Path
    transport: Transport
    now: float
    api_base: str


@dataclass(frozen=True)
class _Auth:
    """The App's bot login and an installation token for one repository."""

    bot: str
    token: str


class _Answer(Exception):
    """An answer decided by a helper; the handler returns it."""

    def __init__(self, response: Response) -> None:
        super().__init__(response.reason)
        self.response = response


def escape_body(body: str) -> str:
    """`body` with every `<!--` neutralised: the publisher's marker, added
    after, is the only real one."""
    return _ESCAPED.join(body.split("<!--"))


def _answers(fn: Callable[[Context], Response]) -> Callable[[Context], Response]:
    def run(ctx: Context) -> Response:
        try:
            return fn(ctx)
        except _Answer as answer:
            return answer.response

    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


# --- context ------------------------------------------------------------------


def _products(layout: Layout) -> dict[int, Product]:
    try:
        text = read_optional(layout.etc / "products.toml")
        return {} if text is None else parse_products(text)
    except ConfigError as exc:
        raise Refused("products_config_invalid") from exc


def _openssl(ctx: Context) -> Path:
    install = ctx.snapshot.install
    path = None if install is None else install.approved_cli.get("openssl")
    if path is None:
        raise Refused("openssl_not_configured")
    return path


def _key_of(layout: Layout, app_id: int) -> Path:
    return layout.secrets_dir / f"app-{app_id}.pem"


def build_context(ctx: Context) -> OpContext:
    """The `OpContext` of the product the request names (`product_unknown`,
    `products_config_invalid`, `openssl_not_configured` otherwise)."""
    product = _products(ctx.layout).get(int(str(ctx.request.fields["repository_id"])))
    if product is None:
        raise Refused("product_unknown")
    return OpContext(
        ctx.layout,
        product,
        _key_of(ctx.layout, product.app_id),
        _openssl(ctx),
        github.default_transport,
        time.time(),
        ctx.api_base,
    )


def _unavailable() -> _Answer:
    return _Answer(Response("unknown", "github_unavailable"))


def _check_app(
    products: Mapping[int, Product],
    app_id: int,
    key: Path,
    openssl: Path,
    transport: Transport,
    now: float,
    api_base: str,
) -> SelfCheckResult:
    """`github.self_check` with its failures as answers."""
    try:
        return github.self_check(
            products,
            app_id,
            key,
            openssl=openssl,
            transport=transport,
            api_base=api_base,
            clock=lambda: now,
        )
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(_reason(exc)) from None
    except SigningFailed:
        raise Refused("app_key_unusable") from None


def _self_check_first(ctx: Context, oc: OpContext) -> None:
    """Decision 10: the App is checked before every operation."""
    result = _check_app(
        _products(ctx.layout),
        oc.product.app_id,
        oc.app_key,
        oc.openssl,
        oc.transport,
        oc.now,
        oc.api_base,
    )
    if result.problems:
        raise Refused(result.problems[0])


def _reason(exc: GitHubRefused) -> str:
    return exc.reason if exc.status is None else f"github_{exc.status}: {exc.reason}"


def _authorize(oc: OpContext, permissions: Mapping[str, str]) -> github.Token:
    """An installation token for the product's repository."""
    jwt = _jwt(oc)
    try:
        return github.installation_token(
            jwt,
            oc.product.installation_id,
            repository_ids=[oc.product.repository_id],
            permissions=permissions,
            transport=oc.transport,
            api_base=oc.api_base,
            clock=lambda: oc.now,
        )
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(_reason(exc)) from None


def _jwt(oc: OpContext) -> str:
    try:
        return github.app_jwt(
            oc.product.app_id, oc.app_key, openssl=oc.openssl, now=int(oc.now)
        )
    except SigningFailed:
        raise Refused("app_key_unusable") from None


def _auth(oc: OpContext) -> _Auth:
    """The bot's login and a review token (before any intent)."""
    jwt = _jwt(oc)
    try:
        slug = github.app_slug(jwt, transport=oc.transport, api_base=oc.api_base)
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(_reason(exc)) from None
    token = _authorize(oc, REVIEW_PERMISSIONS)
    return _Auth(f"{slug}[bot]", token.value)


# --- publish-review -----------------------------------------------------------


def _is_bot(review: Mapping[str, object], bot: str) -> bool:
    user = review.get("user")
    if not isinstance(user, dict):
        return False
    return user.get("login") == bot and user.get("type") == "Bot"


def _check_pr(oc: OpContext, auth: _Auth, pr: int, head_sha: str) -> None:
    """The PR is open, of this product, at `head_sha` (`pr_state`)."""
    path = f"/repos/{oc.product.full_name}/pulls/{pr}"
    try:
        _, data = github.call(
            "GET", path, auth.token, transport=oc.transport, api_base=oc.api_base
        )
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(_reason(exc)) from None
    if not _pr_matches(data, oc.product.repository_id, head_sha):
        raise Refused("pr_state")


def _pr_matches(data: object, repository_id: int, head_sha: str) -> bool:
    if not isinstance(data, dict) or data.get("state") != "open":
        return False
    base, head = data.get("base"), data.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        return False
    repo = base.get("repo")
    if not isinstance(repo, dict) or repo.get("id") != repository_id:
        return False
    return head.get("sha") == head_sha


def _post_review(
    oc: OpContext, auth: _Auth, req: Request, key: str
) -> Mapping[str, object] | None:
    """POST the review; `None` if the outcome is ambiguous (reconcile)."""
    fields = req.fields
    head_sha = str(fields["head_sha"])
    marker = MARKER.format(key=key, sha=head_sha)
    body = {
        "commit_id": head_sha,
        "event": EVENTS[str(fields["decision"])],
        "body": f"{escape_body(str(fields['body']))}\n\n{marker}",
    }
    path = f"/repos/{oc.product.full_name}/pulls/{fields['pr']}/reviews"
    try:
        _, data = github.call(
            "POST",
            path,
            auth.token,
            body,
            transport=oc.transport,
            api_base=oc.api_base,
        )
    except GitHubUnavailable:
        return None
    except GitHubRefused as exc:
        raise _Answer(Response("refused", _reason(exc))) from None
    if not isinstance(data, dict):
        raise _Answer(Response("unknown", "review_response_unexpected"))
    return data


def _posted(data: Mapping[str, object], auth: _Auth, head_sha: str) -> Response:
    ident = data.get("id")
    ok = (
        isinstance(ident, int)
        and not isinstance(ident, bool)
        and data.get("commit_id") == head_sha
        and _is_bot(data, auth.bot)
    )
    if not ok:
        return Response("unknown", "review_response_unexpected")
    return Response("done", None, {"review_id": ident, "commit_id": head_sha})


def _list_reviews(oc: OpContext, auth: _Auth, pr: object) -> list[dict[str, object]]:
    path = f"/repos/{oc.product.full_name}/pulls/{pr}/reviews"
    items = github.paged_array(path, auth.token, oc.transport, api_base=oc.api_base)
    if not all(isinstance(item, dict) for item in items):
        raise GitHubUnavailable("reviews: unexpected item")
    return [item for item in items if isinstance(item, dict)]


def reconcile_review(oc: OpContext, auth: _Auth, req: Request, key: str) -> Response:
    """Look for the review this request's marker names among the PR's reviews
    (every page): exactly one from the bot matching the decision is `done`;
    none or several is `unknown`."""
    head_sha = str(req.fields["head_sha"])
    marker = MARKER.format(key=key, sha=head_sha)
    try:
        reviews = _list_reviews(oc, auth, req.fields["pr"])
    except GitHubUnavailable:
        return Response("unknown", "reconcile_unavailable")
    except GitHubRefused as exc:
        return Response("unknown", f"reconcile_refused: {_reason(exc)}")
    mine = [
        r
        for r in reviews
        if _is_bot(r, auth.bot) and marker in str(r.get("body") or "")
    ]
    if not mine:
        return Response("unknown", "review_not_found")
    if len(mine) > 1:
        return Response("unknown", "duplicate_reviews")
    found = mine[0]
    state = STATES[str(req.fields["decision"])]
    ident = found.get("id")
    if (
        found.get("commit_id") != head_sha
        or found.get("state") != state
        or not isinstance(ident, int)
    ):
        return Response("unknown", "review_mismatch")
    return Response("done", None, {"review_id": ident, "commit_id": head_sha})


@_answers
def publish_review(ctx: Context) -> Response:
    """`publish-review` (spec 5.4): read the PR itself, record the intent,
    publish the review at `head_sha`; an ambiguous POST or a repeated request
    is reconciled, never posted again."""
    oc = build_context(ctx)
    _self_check_first(ctx, oc)
    auth = _auth(oc)
    req, key = ctx.request, ctx.reservation.key
    if ctx.intent is not None:
        return reconcile_review(oc, auth, req, key)
    head_sha = str(req.fields["head_sha"])
    _check_pr(oc, auth, int(str(req.fields["pr"])), head_sha)
    ctx.record_intent(
        {"request_key": key, "head_sha": head_sha, "decision": req.fields["decision"]}
    )
    data = _post_review(oc, auth, req, key)
    if data is None:
        return reconcile_review(oc, auth, req, key)
    return _posted(data, auth, head_sha)


# --- read-token ---------------------------------------------------------------


@_answers
def read_token(ctx: Context) -> Response:
    """`read-token` (spec 4.4): a fresh read-only token of the product's
    repository. Nothing external changes, so no intent is written, and the
    token is in the answer only (the frame never stores or journals it)."""
    oc = build_context(ctx)
    _self_check_first(ctx, oc)
    token = _authorize(oc, READ_PERMISSIONS)
    result = {
        "token": token.value,
        "expires_at": token.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "permissions": dict(token.permissions),
        "repository_ids": list(token.repository_ids),
    }
    return Response("done", None, result)


# --- self-check ---------------------------------------------------------------


@_answers
def self_check_op(ctx: Context) -> Response:
    """`self-check` (root): every App of `products.toml` is checked; the
    installations and problems are put together."""
    products = _products(ctx.layout)
    openssl = _openssl(ctx) if products else Path()
    installations: list[github.Installation] = []
    problems: list[str] = []
    for app_id in sorted({p.app_id for p in products.values()}):
        result = _check_app(
            products,
            app_id,
            _key_of(ctx.layout, app_id),
            openssl,
            github.default_transport,
            time.time(),
            ctx.api_base,
        )
        installations.extend(result.installations)
        problems.extend(result.problems)
    whole = SelfCheckResult(tuple(installations), tuple(problems), None)
    return Response("done", None, whole.to_json())


__all__ = [
    "MARKER",
    "OpContext",
    "build_context",
    "escape_body",
    "publish_review",
    "read_token",
    "reconcile_review",
    "self_check_op",
    "store",
]
