"""Publisher operations that talk to GitHub as the App: `publish-review`,
`read-token`, `self-check` (spec 4.4, 5.4; stage 2a, Task 9).

Every operation first builds its `OpContext` and runs the App `self_check`
(decision 10); both happen before any intent is written, so their refusals
are truthful. A handler writes its intent strictly before the external POST
and never saves a result itself: the frame does (R23, R24 of Task 7).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from df.config import ConfigError, read_optional
from df.paths import Layout
from df.publisher import github, perimeter
from df.publisher.github import (
    GitHubRefused,
    GitHubUnavailable,
    SelfCheckResult,
    SigningFailed,
    Transport,
)
from df.publisher.request import Refused, Request, Response
from df.stage2_config import Product, TrustBinding, parse_products

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
#: GitHub's limit of a review body, in characters.
MAX_REVIEW_BODY = 65536


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
    products: Mapping[int, Product]
    #: `publisher.toml`'s `git_base`, handed to the precheck.
    git_base: str
    #: `trust.toml` (`None`: no binding): its trust sources must carry no
    #: installation of the App (spec 6.1).
    binding: TrustBinding | None = None


@dataclass(frozen=True)
class Auth:
    """The App's bot login and an installation token for one repository."""

    bot: str
    token: str = field(repr=False)


class Answer(Exception):
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
        except Answer as answer:
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
    return layout.app_key(app_id)


def build_context(ctx: Context) -> OpContext:
    """The `OpContext` of the product the request names. The run must belong
    to that product (`run_product_mismatch`) and, with a binding, to its
    root (`root_mismatch`, spec 5.8: a run of the old root gets no operation
    over a PR after `--rebind`); `product_unknown`,
    `products_config_invalid`, `openssl_not_configured` otherwise."""
    repository_id = int(str(ctx.request.fields["repository_id"]))
    if ctx.record is None or ctx.record.product != repository_id:
        raise Refused("run_product_mismatch")
    binding = perimeter.binding_of(ctx)
    if binding is not None:
        perimeter.check_run_root(binding, ctx.record)
    products = _products(ctx.layout)
    product = products.get(repository_id)
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
        products,
        ctx.git_base,
        binding,
    )


def _unavailable() -> Answer:
    return Answer(Response("unknown", "github_unavailable"))


def _check_app(
    products: Mapping[int, Product],
    app_id: int,
    key: Path,
    openssl: Path,
    transport: Transport,
    now: float,
    api_base: str,
    binding: TrustBinding | None = None,
) -> SelfCheckResult:
    """`github.self_check` with the clock fixed at `now`; an installation
    that reaches a trust source of `binding` is a problem first
    (`TRUST_SOURCE_PROBLEM`)."""
    result = github.self_check(
        products,
        app_id,
        key,
        openssl=openssl,
        transport=transport,
        api_base=api_base,
        clock=lambda: now,
    )
    found = [
        TRUST_SOURCE_PROBLEM.format(id=inst, role=role)
        for inst, role, _repo in on_trust_sources(result, binding)
    ]
    if not found:
        return result
    problems = (*found, *result.problems)
    return SelfCheckResult(result.installations, problems, result.perimeter)


#: The problem of an App installation on `approval-policy` or `df-trust`.
TRUST_SOURCE_PROBLEM = "app_installation_on_trust_source: {id}: {role}"


def on_trust_sources(
    result: SelfCheckResult, binding: TrustBinding | None
) -> list[tuple[int, str, int]]:
    """`(installation id, role, repository id)` of every installation that
    reaches a trust source of `binding` (spec 6.1: the App key's holder
    checks it; such an installation is a violation)."""
    if binding is None:
        return []
    sources = {
        binding.approval_policy_repository_id: "approval-policy",
        binding.df_trust_repository_id: "df-trust",
    }
    return [
        (inst.id, sources[repo], repo)
        for inst in result.installations
        for repo in inst.repository_ids
        if repo in sources
    ]


def self_check_first(oc: OpContext) -> None:
    """Decision 10: the App is checked before every operation. An
    installation on a trust source is registered as a perimeter event of
    the bound root (`pending` until `perimeter-status` registers it at the
    verified epoch), then refused."""
    try:
        result = _check_app(
            oc.products,
            oc.product.app_id,
            oc.app_key,
            oc.openssl,
            oc.transport,
            oc.now,
            oc.api_base,
            oc.binding,
        )
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(refusal_reason(exc)) from None
    except SigningFailed:
        raise Refused("app_key_unusable") from None
    found = on_trust_sources(result, oc.binding)
    if found and oc.binding is not None:
        try:
            perimeter.record_app_installations(oc.layout, oc.binding, found)
        except perimeter.Unavailable as exc:
            print(f"publisher: event not registered: {exc.reason}", file=sys.stderr)
    if result.problems:
        raise Refused(result.problems[0])


def refusal_reason(exc: GitHubRefused) -> str:
    return exc.reason if exc.status is None else f"github_{exc.status}: {exc.reason}"


def authorize(oc: OpContext, permissions: Mapping[str, str]) -> github.Token:
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
        raise Refused(refusal_reason(exc)) from None


def _jwt(oc: OpContext) -> str:
    try:
        return github.app_jwt(
            oc.product.app_id, oc.app_key, openssl=oc.openssl, now=int(oc.now)
        )
    except SigningFailed:
        raise Refused("app_key_unusable") from None


def review_auth(oc: OpContext) -> Auth:
    """The bot's login and a review token (before any intent)."""
    jwt = _jwt(oc)
    try:
        slug = github.app_slug(jwt, transport=oc.transport, api_base=oc.api_base)
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(refusal_reason(exc)) from None
    token = authorize(oc, REVIEW_PERMISSIONS)
    return Auth(f"{slug}[bot]", token.value)


# --- publish-review -----------------------------------------------------------


def is_bot(review: Mapping[str, object], bot: str) -> bool:
    """`review` (or any object with a `user`) is authored by the bot `bot`."""
    user = review.get("user")
    if not isinstance(user, dict):
        return False
    return user.get("login") == bot and user.get("type") == "Bot"


def _check_pr(oc: OpContext, auth: Auth, pr: int, head_sha: str) -> None:
    """The PR is open, of this product, at `head_sha` (`pr_state`)."""
    read_pr(oc, auth.token, pr, head_sha)


def read_pr(oc: OpContext, token: str, pr: int, head_sha: str) -> dict[str, object]:
    """The PR as GitHub says it, once it is checked to be open, of this
    product and at `head_sha` (`pr_state` otherwise)."""
    path = f"/repos/{oc.product.full_name}/pulls/{pr}"
    try:
        _, data = github.call(
            "GET", path, token, transport=oc.transport, api_base=oc.api_base
        )
    except GitHubUnavailable:
        raise _unavailable() from None
    except GitHubRefused as exc:
        raise Refused(refusal_reason(exc)) from None
    if not _pr_matches(data, oc.product.repository_id, head_sha):
        raise Refused("pr_state")
    assert isinstance(data, dict)
    return data


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


def _marker(req: Request, key: str) -> str:
    return MARKER.format(key=key, sha=req.fields["head_sha"])


def _review_text(req: Request, key: str) -> str:
    """The body as published: escaped model text, then the one real marker."""
    return f"{escape_body(str(req.fields['body']))}\n\n{_marker(req, key)}"


def _post_review(
    oc: OpContext, auth: Auth, req: Request, key: str
) -> Mapping[str, object] | None:
    """POST the review; `None` if the outcome is ambiguous (reconcile)."""
    fields = req.fields
    body = {
        "commit_id": str(fields["head_sha"]),
        "event": EVENTS[str(fields["decision"])],
        "body": _review_text(req, key),
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
        raise Answer(Response("refused", refusal_reason(exc))) from None
    if not isinstance(data, dict):
        raise Answer(Response("unknown", "review_response_unexpected"))
    return data


def _done(ident: int, head_sha: str) -> Response:
    ids = {"review_id": ident, "commit_id": head_sha}
    return Response("done", None, ids, dict(ids))


def _posted(data: Mapping[str, object], auth: Auth, req: Request, key: str) -> Response:
    """The POST's answer is the review we asked for: same commit, decision's
    state, our marker last, authored by the bot."""
    ident = data.get("id")
    head_sha = str(req.fields["head_sha"])
    body = data.get("body")
    ok = (
        github._is_int(ident)
        and data.get("commit_id") == head_sha
        and data.get("state") == STATES[str(req.fields["decision"])]
        and isinstance(body, str)
        and body.endswith("\n\n" + _marker(req, key))
        and is_bot(data, auth.bot)
    )
    if not ok:
        return Response("unknown", "review_response_unexpected")
    assert isinstance(ident, int)
    return _done(ident, head_sha)


def list_reviews(oc: OpContext, auth: Auth, pr: object) -> list[dict[str, object]]:
    """Every review of PR `pr` (all pages); an item that is not an object is
    `GitHubUnavailable`."""
    path = f"/repos/{oc.product.full_name}/pulls/{pr}/reviews"
    items = github.paged_array(path, auth.token, oc.transport, api_base=oc.api_base)
    if not all(isinstance(item, dict) for item in items):
        raise GitHubUnavailable("reviews: unexpected item")
    return [item for item in items if isinstance(item, dict)]


def reconcile_review(oc: OpContext, auth: Auth, req: Request, key: str) -> Response:
    """Look for the review this request's marker names among the PR's reviews
    (every page): exactly one from the bot matching the decision is `done`;
    none or several is `unknown`."""
    head_sha = str(req.fields["head_sha"])
    suffix = "\n\n" + _marker(req, key)
    try:
        reviews = list_reviews(oc, auth, req.fields["pr"])
    except GitHubUnavailable:
        return Response("unknown", "reconcile_unavailable")
    except GitHubRefused as exc:
        return Response("unknown", f"reconcile_refused: {refusal_reason(exc)}")
    mine = [
        r
        for r in reviews
        if is_bot(r, auth.bot) and str(r.get("body") or "").endswith(suffix)
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
        or not github._is_int(ident)
    ):
        return Response("unknown", "review_mismatch")
    assert isinstance(ident, int)
    return _done(ident, head_sha)


@_answers
def publish_review(ctx: Context) -> Response:
    """`publish-review` (spec 5.4): read the PR itself, record the intent,
    publish the review at `head_sha`; an ambiguous POST or a repeated request
    is reconciled, never posted again."""
    oc = build_context(ctx)
    req, key = ctx.request, ctx.reservation.key
    if len(_review_text(req, key)) > MAX_REVIEW_BODY:
        raise Refused("body_too_long")
    self_check_first(oc)
    auth = review_auth(oc)
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
    return _posted(data, auth, req, key)


# --- read-token ---------------------------------------------------------------


@_answers
def read_token(ctx: Context) -> Response:
    """`read-token` (spec 4.4): a fresh read-only token of the product's
    repository. Nothing external changes, so no intent is written, and the
    token is in the answer only (the frame never stores or journals it)."""
    oc = build_context(ctx)
    self_check_first(oc)
    token = authorize(oc, READ_PERMISSIONS)
    result = {
        "token": token.value,
        "expires_at": token.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "permissions": dict(token.permissions),
        "repository_ids": list(token.repository_ids),
    }
    return Response("done", None, result)


# --- self-check ---------------------------------------------------------------


def _check_one(
    ctx: Context,
    products: Mapping[int, Product],
    app_id: int,
    openssl: Path,
    binding: TrustBinding | None,
) -> SelfCheckResult:
    """One App's check; a broken key or an unreadable App is a problem of
    that App, not the end of the report."""
    try:
        return _check_app(
            products,
            app_id,
            _key_of(ctx.layout, app_id),
            openssl,
            github.default_transport,
            time.time(),
            ctx.api_base,
            binding,
        )
    except SigningFailed:
        return SelfCheckResult((), (f"app_key_unusable: {app_id}",), None)
    except GitHubRefused as exc:
        problem = f"app_unreadable: {app_id}: {refusal_reason(exc)}"
        return SelfCheckResult((), (problem,), None)
    except GitHubUnavailable:
        raise _unavailable() from None


@_answers
def self_check_op(ctx: Context) -> Response:
    """`self-check` (root): every App of `products.toml` is checked (an
    installation on a trust source of `trust.toml` is a problem); the
    installations and problems are put together with the perimeter's limits
    (`None` and a problem if the database cannot be opened)."""
    products = _products(ctx.layout)
    openssl = _openssl(ctx) if products else Path()
    installations: list[github.Installation] = []
    problems: list[str] = []
    try:
        binding = perimeter.binding_of(ctx)
    except Refused as exc:
        binding = None
        problems.append(exc.reason)
    for app_id in sorted({p.app_id for p in products.values()}):
        result = _check_one(ctx, products, app_id, openssl, binding)
        installations.extend(result.installations)
        problems.extend(result.problems)
    stats = perimeter.stats(ctx.layout)
    if isinstance(stats, perimeter.Unavailable):
        problems.append(f"perimeter_unavailable: {stats.reason}")
        stats = None
    whole = SelfCheckResult(tuple(installations), tuple(problems), stats)
    return Response("done", None, whole.to_json())


__all__ = [
    "MARKER",
    "TRUST_SOURCE_PROBLEM",
    "Answer",
    "Auth",
    "OpContext",
    "authorize",
    "build_context",
    "escape_body",
    "is_bot",
    "list_reviews",
    "on_trust_sources",
    "publish_review",
    "read_pr",
    "read_token",
    "reconcile_review",
    "refusal_reason",
    "review_auth",
    "self_check_first",
    "self_check_op",
]
