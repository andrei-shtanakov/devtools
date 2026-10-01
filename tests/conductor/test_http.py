"""HTTP-адаптер App: без перенаправлений, классификация исходов (§4.5)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conductor.http import (
    Response,
    TransportError,
    classify,
    rate_info,
    urllib_transport,
)


class _Handler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str]] = []

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        type(self).hits.append((self.command, self.path))
        if self.path.startswith("/redirect/"):
            self.send_response(int(self.path.rsplit("/", 1)[1]))
            self.send_header("Location", "/other")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body, code = (b'{"a": 1}', 200) if self.path == "/ok" else (b"{}", 404)
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _respond

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.mark.parametrize("code", [301, 302, 307, 308])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_redirect_is_never_followed(server: str, code: int, method: str) -> None:
    body = b"{}" if method != "GET" else None
    resp = urllib_transport(method, f"{server}/redirect/{code}", {}, body)
    assert resp.status == code
    assert classify(resp, graphql=False) == "moved"
    assert _Handler.hits == [(method, f"/redirect/{code}")]  # /other не запрошен


def test_ok_and_404(server: str) -> None:
    assert urllib_transport("GET", f"{server}/ok", {}, None).json() == {"a": 1}
    assert urllib_transport("GET", f"{server}/missing", {}, None).status == 404


def test_no_server_is_transport_error() -> None:
    with pytest.raises(TransportError):
        urllib_transport("GET", "http://127.0.0.1:9/x", {}, None)


def _r(status: int, body: bytes = b"{}", **headers: str) -> Response:
    return Response(status, {k.replace("_", "-"): v for k, v in headers.items()}, body)


@pytest.mark.parametrize(
    ("resp", "graphql", "outcome"),
    [
        (_r(200, b'{"id": 1}'), False, "ok"),
        (_r(201, b'{"id": 1}'), False, "ok"),
        (_r(200, b"{"), False, "uncertain"),
        (_r(200, b""), False, "uncertain"),
        (_r(404), False, "failed"),
        (_r(422), False, "failed"),
        (_r(403), False, "failed"),
        (_r(403, x_ratelimit_remaining="0"), False, "rate_limited"),
        (_r(429, retry_after="60"), False, "rate_limited"),
        (
            _r(403, b'{"message": "You have exceeded a secondary rate limit"}'),
            False,
            "rate_limited",
        ),
        (_r(500), False, "uncertain"),
        (_r(502), False, "uncertain"),
        (_r(408), False, "uncertain"),
        (_r(301), False, "moved"),
        (_r(200, b'{"data": {"x": {"id": 1}}}'), True, "ok"),
        (
            _r(200, b'{"data": null, "errors": [{"type": "RATE_LIMITED"}]}'),
            True,
            "rate_limited",
        ),
        (_r(200, b'{"data": null, "errors": [{"type": "NOT_FOUND"}]}'), True, "failed"),
        (
            _r(200, b'{"data": null, "errors": [{"type": "INTERNAL"}]}'),
            True,
            "uncertain",
        ),
        (_r(200, b'{"data": null}'), True, "uncertain"),
    ],
)
def test_classify(resp: Response, graphql: bool, outcome: str) -> None:
    assert classify(resp, graphql) == outcome


def test_rate_info() -> None:
    info = rate_info(
        {
            "retry-after": "30",
            "x-ratelimit-reset": "1700000000",
            "x-ratelimit-remaining": "0",
        }
    )
    assert (info.retry_after_s, info.reset, info.remaining) == (30, 1700000000, 0)
    assert rate_info({"retry-after": "x"}).retry_after_s is None
