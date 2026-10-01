"""Клиент App (спека среза 1, §4.3, §5.1 шаги 5 и 7, §5.5)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from conductor.app_calls import AppCalls, RateInfo, init_host
from conductor.gh_app import AppClient, Blocked
from conductor.host_config import HostConfig
from conductor.http import Response, TransportError

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
CFG = HostConfig(
    app_id=11,
    installation_id=22,
    private_key=Path("/k.pem"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)


class FakeTransport:
    """Скрипт ответов по (метод, путь); записывает запросы."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.routes: dict[tuple[str, str], Response] = {
            ("GET", "/app"): Response(
                200, {}, json.dumps({"id": 11, "slug": "conductor"}).encode()
            ),
            ("POST", "/app/installations/22/access_tokens"): Response(
                201,
                {},
                json.dumps(
                    {"token": "ghs_" + "a" * 36, "expires_at": "2026-10-01T13:00:00Z"}
                ).encode(),
            ),
            ("GET", "/app/installations/22"): Response(
                200, {}, json.dumps({"account": {"login": "own"}}).encode()
            ),
            ("GET", "/repos/own/a/installation"): Response(200, {}, b'{"id": 22}'),
            ("GET", "/repos/own/b/installation"): Response(404, {}, b"{}"),
            ("GET", "/repos/own/c/installation"): Response(200, {}, b'{"id": 99}'),
            ("GET", "/repos/own/d/installation"): Response(500, {}, b"{}"),
        }
        self.fail: set[str] = set()

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:
        path = url.removeprefix("https://api.github.com")
        self.requests.append((method, path, headers))
        if path in self.fail:
            raise TransportError("timeout")
        return self.routes[(method, path)]


@pytest.fixture
def env(tmp_path: Path):
    init_host(tmp_path, 11, 22, T0)
    calls, _ = AppCalls.open(tmp_path, 11, 22, T0)
    assert calls is not None
    transport, clock = FakeTransport(), [T0]
    client = AppClient(
        CFG, calls, transport, clock=lambda: clock[0], read_key=lambda _: PEM
    )
    return client, transport, calls, clock, tmp_path


def test_jwt_claims(env) -> None:
    client = env[0]
    claims = pyjwt.decode(
        client.jwt(),
        KEY.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
    )
    assert claims["iss"] == "11" and claims["exp"] - claims["iat"] == 600


def test_check_key_returns_bot_login(env) -> None:
    client, transport = env[0], env[1]
    assert client.check_key() == "conductor[bot]" == client.bot_login
    transport.routes[("GET", "/app")] = Response(200, {}, b'{"id": 99, "slug": "x"}')
    assert client.check_key() is None


def test_token_cached_and_refreshed(env) -> None:
    client, transport, _, clock, _ = env
    first = client.token()
    assert first and client.token() == first
    assert sum(p.endswith("access_tokens") for _, p, _ in transport.requests) == 1
    clock[0] = T0 + timedelta(minutes=51)  # до истечения < 10 мин
    client.token()
    assert sum(p.endswith("access_tokens") for _, p, _ in transport.requests) == 2


def test_installation_and_coverage(env) -> None:
    client = env[0]
    assert client.check_installation("own") is True
    assert client.check_installation("other") is False
    assert client.covers("own/a") is True
    assert client.covers("own/b") is False
    assert client.covers("own/c") is False
    assert client.covers("own/d") is None


def test_block_stops_call_before_transport(env) -> None:
    client, transport, calls, _, _ = env
    seq = calls.begin("create", T0)
    calls.end(seq, T0, "rate_limited", 429, RateInfo(retry_after_s=600), "create")
    with pytest.raises(Blocked):
        client.check_key()
    assert transport.requests == []


def test_begin_failure_stops_call(env, monkeypatch) -> None:
    client, transport, calls, _, _ = env

    def boom(*_: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(calls, "begin", boom)
    with pytest.raises(Blocked):
        client.check_key()
    assert transport.requests == []


def test_transport_error_is_uncertain_and_journaled(env) -> None:
    client, transport, calls, _, _ = env
    transport.fail.add("/app")
    assert client.call("service", "GET", "/app", auth="jwt").outcome == "uncertain"
    assert calls.rows[-1]["outcome"] == "uncertain"


def test_token_never_lands_in_journal(env) -> None:
    client, _, calls, _, _ = env
    token = client.token()
    assert token
    text = calls.path.read_text(encoding="utf-8")
    assert token not in text and "eyJ" not in text
