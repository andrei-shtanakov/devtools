"""Unit tests of df.publisher.github (fake transport, no network)."""

from __future__ import annotations

import base64
import email.message
import http.client
import io
import json
import shutil
import subprocess
import urllib.request
import urllib.response
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from df.publisher import github, request
from df.publisher.github import (
    GitHubRefused,
    GitHubUnavailable,
    Installation,
    PerimeterStats,
    SelfCheckResult,
    SigningFailed,
)
from df.stage2_config import Product

NOW = 1_800_000_000
OPENSSL = shutil.which("openssl")
needs_openssl = pytest.mark.skipif(OPENSSL is None, reason="openssl not found")
Reply = tuple[int, Mapping[str, str], bytes]


def _json_reply(obj: object, status: int = 200, **headers: str) -> Reply:
    return status, headers, json.dumps(obj).encode()


class FakeTransport:
    """Answers by `(method, url)`; a list is consumed in order."""

    def __init__(self, routes: Mapping[tuple[str, str], Reply | list[Reply]]) -> None:
        self.routes = {k: v for k, v in routes.items()}
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Reply:
        self.calls.append((method, url, headers, body))
        reply = self.routes[(method, url)]
        if isinstance(reply, list):
            return reply.pop(0)
        return reply


@pytest.fixture(scope="session")
def app_key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway RSA key made by the system openssl; never committed."""
    if OPENSSL is None:
        pytest.skip("openssl not found")
    path = tmp_path_factory.mktemp("app-key") / "test-key.pem"
    subprocess.run(
        [OPENSSL, "genrsa", "-out", str(path), "2048"],
        check=True,
        capture_output=True,
    )
    return path


def _expiry(seconds: int) -> str:
    when = datetime.fromtimestamp(NOW + seconds, UTC)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clock() -> float:
    return float(NOW)


def _token_reply(repos: list[int], perms: Mapping[str, str], secs: int = 3000) -> Reply:
    return _json_reply(
        {
            "token": "ghs_SECRET",
            "expires_at": _expiry(secs),
            "permissions": dict(perms),
            "repositories": [{"id": r} for r in repos],
        },
        201,
    )


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# --- JWT ----------------------------------------------------------------------


@needs_openssl
def test_jwt_claims(app_key: Path, tmp_path: Path) -> None:
    assert OPENSSL is not None
    token = github.app_jwt(42, app_key, openssl=Path(OPENSSL), now=NOW)
    head, claims, sig = token.split(".")
    assert json.loads(_decode(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(_decode(claims)) == {
        "iat": NOW - 60,
        "exp": NOW + 540,
        "iss": 42,
    }
    assert "=" not in token
    assert _verify(app_key, tmp_path, f"{head}.{claims}".encode(), _decode(sig))


def _verify(key: Path, tmp: Path, data: bytes, sig: bytes) -> bool:
    assert OPENSSL is not None
    pub = tmp / "pub.pem"
    subprocess.run(
        [OPENSSL, "rsa", "-in", str(key), "-pubout", "-out", str(pub)],
        check=True,
        capture_output=True,
    )
    sig_file = tmp / "sig.bin"
    sig_file.write_bytes(sig)
    done = subprocess.run(
        [OPENSSL, "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig_file)],
        input=data,
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


@needs_openssl
def test_run_with_input_signs(app_key: Path, tmp_path: Path) -> None:
    from df import platform

    assert OPENSSL is not None
    data = b"payload-bytes"
    argv = [OPENSSL, "dgst", "-sha256", "-sign", str(app_key)]
    done = platform.run_with_input(argv, data, timeout=30.0)
    assert done.launched and done.returncode == 0
    assert _verify(app_key, tmp_path, data, done.stdout)
    assert not _verify(app_key, tmp_path, b"payload-byteX", done.stdout)


@needs_openssl
def test_jwt_signing_failure_is_signing_failed(tmp_path: Path) -> None:
    assert OPENSSL is not None
    bad = tmp_path / "missing.pem"
    with pytest.raises(SigningFailed):
        github.app_jwt(1, bad, openssl=Path(OPENSSL), now=NOW)


# --- tokens -------------------------------------------------------------------

TOKEN_URL = f"{github.API}/app/installations/9/access_tokens"
PERMS = {"contents": "write", "pull_requests": "write"}


def _mint(
    reply: Reply,
    *,
    repos: list[int] | None = (7,),  # type: ignore[assignment]
    perms: Mapping[str, str] | None = None,
) -> github.Token:
    fake = FakeTransport({("POST", TOKEN_URL): reply})
    return github.installation_token(
        "jwt.jwt.jwt",
        9,
        repository_ids=None if repos is None else list(repos),
        permissions=perms,
        transport=fake,
        clock=_clock,
    )


def test_token_ok_and_secret_not_in_repr() -> None:
    token = _mint(_token_reply([7], PERMS), perms=PERMS)
    assert token.value == "ghs_SECRET"
    assert token.repository_ids == (7,)
    assert "ghs_SECRET" not in repr(token)


def test_token_request_body() -> None:
    fake = FakeTransport({("POST", TOKEN_URL): _token_reply([7], PERMS)})
    github.installation_token(
        "j", 9, repository_ids=[7], permissions=PERMS, transport=fake, clock=_clock
    )
    _, _, headers, body = fake.calls[0]
    assert json.loads(body or b"") == {"repository_ids": [7], "permissions": PERMS}
    assert headers["Authorization"] == "Bearer j"


def test_token_scope_mismatch() -> None:
    with pytest.raises(GitHubRefused, match="token_scope_mismatch") as err:
        _mint(_token_reply([7, 8], PERMS), perms=PERMS)
    assert "ghs_SECRET" not in str(err.value)
    with pytest.raises(GitHubRefused, match="token_scope_mismatch"):
        _mint(_token_reply([7], {"contents": "read"}), perms=PERMS)
    with pytest.raises(GitHubRefused, match="token_scope_mismatch"):
        _mint(_token_reply([9], PERMS), perms=PERMS)


def test_token_metadata_read_is_tolerated() -> None:
    plus = {**PERMS, "metadata": "read"}
    assert _mint(_token_reply([7], plus), perms=PERMS).permissions == plus


@pytest.mark.parametrize(
    "extra", [{"metadata": "write"}, {"issues": "read"}, {"metadata": "admin"}]
)
def test_token_extra_permission_is_mismatch(extra: Mapping[str, str]) -> None:
    with pytest.raises(GitHubRefused, match="token_scope_mismatch"):
        _mint(_token_reply([7], {**PERMS, **extra}), perms=PERMS)


def test_token_requested_metadata_must_match_exactly() -> None:
    asked = {**PERMS, "metadata": "read"}
    _mint(_token_reply([7], asked), perms=asked)
    with pytest.raises(GitHubRefused, match="token_scope_mismatch"):
        _mint(_token_reply([7], PERMS), perms=asked)


def test_token_expiry_bounds() -> None:
    _mint(_token_reply([7], PERMS, secs=3600), perms=PERMS)
    _mint(_token_reply([7], PERMS, secs=3600 + github.CLOCK_SLACK), perms=PERMS)
    for secs in (0, -5, 3600 + github.CLOCK_SLACK + 1):
        with pytest.raises(GitHubRefused, match="token_expiry_invalid"):
            _mint(_token_reply([7], PERMS, secs=secs), perms=PERMS)
    junk = _json_reply(
        {"token": "t", "expires_at": "tomorrow", "permissions": PERMS}, 201
    )
    with pytest.raises(GitHubRefused, match="token_expiry_invalid"):
        _mint(junk, repos=None, perms=PERMS)
    naive = _json_reply(
        {"token": "t", "expires_at": "2027-01-15T08:00:00", "permissions": PERMS}, 201
    )
    with pytest.raises(GitHubRefused, match="token_expiry_invalid"):
        _mint(naive, repos=None, perms=PERMS)
    far = _json_reply(
        {"token": "t", "expires_at": "9999-12-31T23:59:59-23:59", "permissions": PERMS},
        201,
    )
    with pytest.raises(GitHubRefused, match="token_expiry_invalid"):
        _mint(far, repos=None, perms=PERMS)


def test_token_without_requested_scope_is_not_compared() -> None:
    token = _mint(_token_reply([1, 2], PERMS), repos=None, perms=None)
    assert token.repository_ids == (1, 2)


# --- transport ----------------------------------------------------------------


def test_no_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    assert urllib.request.getproxies().get("https")
    stock = urllib.request.build_opener()
    assert any(_proxy_of(h) for h in stock.handlers)  # the test sees a proxy
    assert not any(_proxy_of(h) for h in github.build_opener().handlers)


def _proxy_of(handler: object) -> object:
    if isinstance(handler, urllib.request.ProxyHandler):
        return handler.proxies
    return None


def test_5xx_is_unavailable() -> None:
    fake = FakeTransport({("GET", f"{github.API}/x"): (503, {}, b"{}")})
    with pytest.raises(GitHubUnavailable):
        github.call("GET", "/x", "tok", transport=fake)


def test_deeply_nested_answer_is_unavailable() -> None:
    deep = b"[" * 200_000 + b"]" * 200_000
    fake = FakeTransport({("GET", f"{github.API}/x"): (200, {}, deep)})
    with pytest.raises(GitHubUnavailable, match="not JSON"):
        github.call("GET", "/x", "tok", transport=fake)
    fake = FakeTransport({("GET", f"{github.API}/x"): (404, {}, deep)})
    with pytest.raises(GitHubRefused, match="no message"):
        github.call("GET", "/x", "tok", transport=fake)


def test_4xx_is_refused_with_status_and_message() -> None:
    reply = _json_reply({"message": "Bad credentials"}, 401)
    fake = FakeTransport({("GET", f"{github.API}/x"): reply})
    with pytest.raises(GitHubRefused) as err:
        github.call("GET", "/x", "tok-secret", transport=fake)
    assert err.value.status == 401
    assert str(err.value) == "Bad credentials"
    assert "tok-secret" not in str(err.value)


def test_network_error_is_unavailable() -> None:
    def boom(*_args: object) -> Reply:
        raise ConnectionResetError("reset")

    with pytest.raises(GitHubUnavailable):
        github.call("GET", "/x", "tok", transport=boom)


def test_call_returns_status_and_parsed_body() -> None:
    fake = FakeTransport({("PUT", f"{github.API}/x"): _json_reply({"a": 1}, 200)})
    assert github.call("PUT", "/x", "t", {"k": "v"}, transport=fake) == (200, {"a": 1})
    empty = FakeTransport({("DELETE", f"{github.API}/x"): (204, {}, b"")})
    assert github.call("DELETE", "/x", "t", transport=empty) == (204, None)


def test_api_base_is_used() -> None:
    base = "http://127.0.0.1:5555"
    fake = FakeTransport({("GET", f"{base}/x"): _json_reply({})})
    github.call("GET", "/x", "t", transport=fake, api_base=base)
    assert fake.calls[0][1] == f"{base}/x"


# --- pagination ---------------------------------------------------------------

INSTS = f"{github.API}/app/installations?per_page=100"
INSTS_2 = f"{github.API}/app/installations?per_page=100&page=2"


def _inst(ident: int, login: str = "acme") -> dict[str, object]:
    return {"id": ident, "account": {"login": login}, "permissions": PERMS}


def _link(url: str) -> dict[str, str]:
    return {"Link": f'<{url}>; rel="next", <{url}>; rel="last"'}


def test_pagination_all_pages() -> None:
    fake = FakeTransport(
        {
            ("GET", INSTS): _json_reply([_inst(1)], **_link(INSTS_2)),
            ("GET", INSTS_2): _json_reply([_inst(2, "beta")]),
        }
    )
    found = github.installations("jwt", fake)
    assert [(i.id, i.account) for i in found] == [(1, "acme"), (2, "beta")]
    assert found[0].permissions == PERMS


def test_pagination_error_is_unavailable() -> None:
    fake = FakeTransport(
        {
            ("GET", INSTS): _json_reply([_inst(1)], **_link(INSTS_2)),
            ("GET", INSTS_2): (502, {}, b"bad gateway"),
        }
    )
    with pytest.raises(GitHubUnavailable):
        github.installations("jwt", fake)


def test_pagination_off_host_link_is_unavailable() -> None:
    evil = "https://evil.example/app/installations?page=2"
    fake = FakeTransport({("GET", INSTS): _json_reply([_inst(1)], **_link(evil))})
    with pytest.raises(GitHubUnavailable):
        github.installations("jwt", fake)
    assert len(fake.calls) == 1


def test_pagination_has_a_limit() -> None:
    fake = FakeTransport({("GET", INSTS): _json_reply([], **_link(INSTS))})
    with pytest.raises(GitHubUnavailable):
        github.installations("jwt", fake)
    assert len(fake.calls) == github.MAX_PAGES


def test_installations_is_array() -> None:
    fake = FakeTransport({("GET", INSTS): _json_reply({"installations": []})})
    with pytest.raises(GitHubUnavailable):
        github.installations("jwt", fake)


REPOS = f"{github.API}/installation/repositories?per_page=100"
REPOS_2 = f"{github.API}/installation/repositories?per_page=100&page=2"


def _repos(ids: list[int], total: int) -> object:
    return {"total_count": total, "repositories": [{"id": i} for i in ids]}


def test_installation_repos_all_pages() -> None:
    fake = FakeTransport(
        {
            ("GET", REPOS): _json_reply(_repos([1, 2], 3), **_link(REPOS_2)),
            ("GET", REPOS_2): _json_reply(_repos([3], 3)),
        }
    )
    assert github.installation_repos("tok", fake) == [1, 2, 3]


def test_installation_repos_is_object() -> None:
    fake = FakeTransport({("GET", REPOS): _json_reply([{"id": 1}])})
    with pytest.raises(GitHubUnavailable):
        github.installation_repos("tok", fake)


def test_total_count_mismatch() -> None:
    fake = FakeTransport({("GET", REPOS): _json_reply(_repos([1, 2], 3))})
    with pytest.raises(GitHubUnavailable):
        github.installation_repos("tok", fake)


# --- self-check ---------------------------------------------------------------


def _product(repo: int, app: int = 42, inst: int = 9) -> Product:
    return Product(repo, f"acme/p{repo}", "write", app, inst, "bot", f"/srv/{repo}")


def _world(repos: list[int], perms: Mapping[str, str] = PERMS) -> FakeTransport:
    listing = [{"id": 9, "account": {"login": "acme"}, "permissions": dict(perms)}]
    return FakeTransport(
        {
            ("GET", INSTS): _json_reply(listing),
            ("POST", TOKEN_URL): _token_reply([], {"metadata": "read"}),
            ("GET", REPOS): _json_reply(_repos(repos, len(repos))),
        }
    )


def _check(
    fake: FakeTransport, products: Mapping[int, Product], key: Path
) -> SelfCheckResult:
    assert OPENSSL is not None
    return github.self_check(
        products,
        42,
        key,
        openssl=Path(OPENSSL),
        transport=fake,
        clock=_clock,
    )


@needs_openssl
def test_self_check_outside_products(app_key: Path) -> None:
    products = {7: _product(7), 8: _product(8, app=43)}
    result = _check(_world([7, 8]), products, app_key)
    assert result.problems == ("app_installation_outside_products: 9",)
    assert result.installations[0].repository_ids == (7, 8)
    assert result.perimeter is None
    clean = _check(_world([7]), products, app_key)
    assert clean.problems == ()


@needs_openssl
def test_self_check_sends_jwt_and_token_separately(app_key: Path) -> None:
    fake = _world([7])
    _check(fake, {7: _product(7)}, app_key)
    auth = {url: h["Authorization"] for _, url, h, _ in fake.calls}
    assert auth[INSTS].count(".") == 2
    assert auth[REPOS] == "Bearer ghs_SECRET"


def test_perimeter_roundtrip() -> None:
    inst = Installation(9, "acme", (7, 8), PERMS)
    result = SelfCheckResult((inst,), ("p",), PerimeterStats(3, 4096, 10**9))
    again = SelfCheckResult.from_json(json.loads(json.dumps(result.to_json())))
    assert again == result


def test_from_json_rejects_bad_shapes() -> None:
    good = SelfCheckResult((), (), None).to_json()
    assert SelfCheckResult.from_json(good) == SelfCheckResult((), (), None)
    for bad in (
        [],
        {**good, "extra": 1},
        {**good, "installations": {}},
        {**good, "perimeter": {"events": 1}},
        {**good, "problems": [1]},
        {
            **good,
            "installations": [
                {"id": True, "account": "a", "repository_ids": [], "permissions": {}}
            ],
        },
    ):
        with pytest.raises(ValueError):
            SelfCheckResult.from_json(bad)


# The predicate of doctor item 9 (`github.app`), local to the test.
def _app_problems(result: SelfCheckResult, product: Product) -> list[str]:
    found = [i for i in result.installations if i.id == product.installation_id]
    if not found:
        return ["installation missing"]
    inst = found[0]
    out = []
    if product.repository_id not in inst.repository_ids:
        out.append("repository missing")
    for name in ("contents", "pull_requests"):
        if inst.permissions.get(name) != "write":
            out.append(f"permission {name}")
    return out


@needs_openssl
@pytest.mark.parametrize(
    ("installation_id", "perms", "expected"),
    [
        (9, PERMS, []),
        (10, PERMS, ["installation missing"]),
        (9, {"contents": "read", "pull_requests": "write"}, ["permission contents"]),
    ],
)
def test_self_check_result_roundtrip(
    app_key: Path,
    installation_id: int,
    perms: Mapping[str, str],
    expected: list[str],
) -> None:
    product = _product(7, inst=installation_id)
    result = _check(_world([7], perms), {7: product}, app_key)
    line = request.emit(request.Response("done", None, result=result.to_json()))
    response = request.parse_response(line)
    again = SelfCheckResult.from_json(response.result)
    assert again == result
    assert _app_problems(again, product) == expected


# --- review round 1 -----------------------------------------------------------


def test_incomplete_read_is_unavailable() -> None:
    def boom(*_args: object) -> Reply:
        raise http.client.IncompleteRead(b"")

    with pytest.raises(GitHubUnavailable, match="network: IncompleteRead"):
        github.call("GET", "/x", "tok", transport=boom)


def test_any_transport_exception_is_unavailable_without_its_text() -> None:
    def boom(*_args: object) -> Reply:
        raise ValueError("secret-token-text")

    with pytest.raises(GitHubUnavailable) as err:
        github.call("GET", "/x", "tok", transport=boom)
    assert str(err.value) == "network: ValueError"


@pytest.mark.parametrize("status", [100, 301, 307, 399])
def test_non_2xx_answers_are_not_success(status: int) -> None:
    fake = FakeTransport({("PUT", f"{github.API}/x"): (status, {}, b"")})
    with pytest.raises(GitHubUnavailable, match=f"http {status}"):
        github.call("PUT", "/x", "t", {"k": 1}, transport=fake)


def test_answer_size_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    class Big:
        def read(self, n: int) -> bytes:
            return b"x" * n

    with pytest.raises(GitHubUnavailable, match="answer too large"):
        github._read_limited(Big())  # type: ignore[arg-type]
    assert github._read_limited(io.BytesIO(b"ok")) == b"ok"
    monkeypatch.setattr(github, "MAX_ANSWER", 4)
    assert github._read_limited(io.BytesIO(b"1234")) == b"1234"
    with pytest.raises(GitHubUnavailable, match="answer too large"):
        github._read_limited(io.BytesIO(b"12345"))


def test_empty_repository_ids_is_rejected_before_any_call() -> None:
    fake = FakeTransport({})
    with pytest.raises(ValueError):
        github.installation_token(
            "j", 9, repository_ids=[], permissions=None, transport=fake
        )
    assert fake.calls == []


def test_answer_without_repositories_key_is_mismatch() -> None:
    reply = _json_reply(
        {"token": "t", "expires_at": _expiry(3000), "permissions": PERMS}, 201
    )
    with pytest.raises(GitHubRefused, match="token_scope_mismatch"):
        _mint(reply, repos=[7], perms=PERMS)


@needs_openssl
def test_self_check_asks_for_metadata_only(app_key: Path) -> None:
    fake = _world([7])
    _check(fake, {7: _product(7)}, app_key)
    bodies = [b for m, u, _, b in fake.calls if m == "POST"]
    assert [json.loads(b or b"") for b in bodies] == [
        {"permissions": {"metadata": "read"}}
    ]


@needs_openssl
def test_self_check_refused_installation_is_a_problem(app_key: Path) -> None:
    fake = _world([7])
    fake.routes[("POST", TOKEN_URL)] = _json_reply({"message": "suspended"}, 403)
    result = _check(fake, {7: _product(7)}, app_key)
    assert result.problems == ("app_installation_unreadable: 9",)
    assert result.installations[0].repository_ids == ()
    fake.routes[("POST", TOKEN_URL)] = (502, {}, b"")
    with pytest.raises(GitHubUnavailable):
        _check(fake, {7: _product(7)}, app_key)


NEXT = f"{github.API}/next"


@pytest.mark.parametrize(
    "link",
    [
        f'<{NEXT}>; foo=bar; rel="next"',
        f"<{NEXT}>; rel=next",
        f'<{NEXT}>; rel="next last"',
        f'<{NEXT}>; REL="Next"',
        f'<{github.API}/last>; rel="last", <{NEXT}>; rel="next"',
    ],
)
def test_link_header_forms(link: str) -> None:
    assert github._next_url({"link": link}, github.API) == NEXT


def test_link_header_without_next() -> None:
    assert github._next_url({}, github.API) is None
    last = f'<{github.API}/next?x=1>; rel="last"'
    assert github._next_url({"Link": last}, github.API) is None


@pytest.mark.parametrize("link", ['rel="next"', "<broken; rel=next", 'x; rel="next"'])
def test_garbled_link_header_is_unavailable(link: str) -> None:
    with pytest.raises(GitHubUnavailable):
        github._next_url({"Link": link}, github.API)


def test_signing_failure_has_its_own_type(tmp_path: Path) -> None:
    assert OPENSSL is not None
    with pytest.raises(SigningFailed):
        github.app_jwt(1, tmp_path / "none.pem", openssl=Path(OPENSSL), now=NOW)
    with pytest.raises(SigningFailed):
        github.app_jwt(1, tmp_path, openssl=Path("/nonexistent/openssl"), now=NOW)


class _FakeOpener:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def open(self, req: object, timeout: float) -> object:
        self.timeouts.append(timeout)
        return urllib.response.addinfourl(io.BytesIO(b"{}"), {}, "u", 200)


def test_default_transport_uses_the_proxy_free_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeOpener()
    monkeypatch.setattr(github, "build_opener", lambda: fake)
    status, _, body = github.default_transport("GET", "http://h/x", {}, None)
    assert (status, body) == (200, b"{}")
    assert fake.timeouts == [github.TIMEOUT]


class _Redirecting(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self) -> None:
        self.seen: list[str] = []

    def http_open(self, req: urllib.request.Request) -> object:
        self.seen.append(req.full_url)
        headers = email.message.Message()
        headers["Location"] = "http://elsewhere.invalid/x"
        resp = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 307)
        resp.msg = "Temporary Redirect"  # type: ignore[attr-defined]
        return resp


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = github.build_opener()
    handler = _Redirecting()
    opener.add_handler(handler)
    monkeypatch.setattr(github, "build_opener", lambda: opener)
    status, headers, _ = github.default_transport("PUT", "http://h/x", {}, b"{}")
    assert status == 307 and headers["Location"].startswith("http://elsewhere")
    assert handler.seen == ["http://h/x"]
    with pytest.raises(GitHubUnavailable, match="http 307"):
        github.call(
            "PUT",
            "/x",
            "t",
            {},
            transport=github.default_transport,
            api_base="http://h",
        )


def test_permissions_are_read_only() -> None:
    inst = Installation(9, "acme", (7,), {"contents": "write"})
    with pytest.raises(TypeError):
        inst.permissions["contents"] = "read"  # type: ignore[index]
    token = _mint(_token_reply([7], PERMS), perms=PERMS)
    with pytest.raises(TypeError):
        token.permissions["x"] = "y"  # type: ignore[index]
    assert inst == Installation(9, "acme", (7,), {"contents": "write"})


# --- app slug and paged arrays ------------------------------------------------


def test_app_slug() -> None:
    fake = FakeTransport({("GET", f"{github.API}/app"): _json_reply({"slug": "df"})})
    assert github.app_slug("jwt", transport=fake) == "df"
    assert fake.calls[0][2]["Authorization"] == "Bearer jwt"


@pytest.mark.parametrize("answer", [{}, {"slug": 3}, {"slug": ""}, [1]])
def test_app_slug_wrong_shape(answer: object) -> None:
    fake = FakeTransport({("GET", f"{github.API}/app"): _json_reply(answer)})
    with pytest.raises(GitHubUnavailable):
        github.app_slug("jwt", transport=fake)


def test_paged_array_follows_links_and_rejects_objects() -> None:
    url = f"{github.API}/x"
    nxt = f"{url}?per_page=100&page=2"
    fake = FakeTransport(
        {
            ("GET", f"{url}?per_page=100"): [_json_reply([1], **_link(nxt))],
            ("GET", nxt): [_json_reply([2, 3])],
        }
    )
    assert github.paged_array("/x", "t", fake) == [1, 2, 3]
    bad = FakeTransport({("GET", f"{url}?per_page=100"): _json_reply({"a": 1})})
    with pytest.raises(GitHubUnavailable):
        github.paged_array("/x", "t", bad)


def test_paged_array_max_items_stops_before_the_next_page() -> None:
    url = f"{github.API}/x"
    nxt = f"{url}?per_page=100&page=2"
    fake = FakeTransport(
        {
            ("GET", f"{url}?per_page=100"): [_json_reply([1, 2], **_link(nxt))],
            # a second page is not served: fetching it would fail the test
        }
    )
    with pytest.raises(github.TooManyItems):
        github.paged_array("/x", "t", fake, max_items=1)
