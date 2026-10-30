"""A loopback proxy to the GitHub API with one gate (Task 15 row 4(b), R15-5).

`serve_gate_proxy()` listens on `http://127.0.0.1:<port>`, the form the
publisher's `api_base` admits (`stage2_config._check_api_base`), and passes
every request to `upstream` (`https://api.github.com`) unchanged: method,
path with query, body and headers, minus `Host`, `Accept-Encoding` and the
hop-by-hop ones. The answer comes back the same way; the API's own URLs in a
`Link` header are rewritten to the proxy's base, so the publisher's paging
(`github._next_url`) stays on its `api_base`.

The gate: the first `POST /repos/<owner>/<name>/pulls/<n>/reviews` is held
before it is passed on. `held` is set when it arrives; it goes on at
`release()`, or after `HOLD_LIMIT` seconds (noted as `expired`: the publisher
waits `github.TIMEOUT` = 20 s for an answer, so a longer hold would turn the
row into a timeout). Requests are recorded as method, path, status and
whether held -- never headers or bodies, which carry tokens.

Used only by `tests/df/live_stand.py`; its offline tests run it against a
local upstream.
"""

from __future__ import annotations

import http.client
import http.server
import re
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

UPSTREAM = "https://api.github.com"
#: Below the publisher's 20 s socket timeout (`df.publisher.github.TIMEOUT`).
HOLD_LIMIT = 15.0
UPSTREAM_TIMEOUT = 30.0
REVIEWS_POST = re.compile(r"/repos/[^/]+/[^/]+/pulls/[0-9]+/reviews")
_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)
_DROP_REQUEST = _HOP | {"host", "accept-encoding"}


@dataclass(frozen=True)
class Seen:
    """One request the proxy passed on (no header, no body)."""

    method: str
    path: str
    status: int
    held: bool
    expired: bool = False


@dataclass
class GateProxy:
    """The running proxy: its base URL, the gate and what it saw."""

    url: str
    upstream: str
    held: threading.Event = field(default_factory=threading.Event)
    released: threading.Event = field(default_factory=threading.Event)
    seen: list[Seen] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _gate_taken: bool = False

    def release(self) -> None:
        """Let the held request go on (idempotent)."""
        self.released.set()

    def take_gate(self, method: str, path: str) -> bool:
        """Whether this request is the one to hold (the first reviews POST)."""
        if method != "POST" or REVIEWS_POST.fullmatch(path) is None:
            return False
        with self._lock:
            if self._gate_taken:
                return False
            self._gate_taken = True
            return True

    def record(self, item: Seen) -> None:
        with self._lock:
            self.seen.append(item)

    def requests(self) -> list[Seen]:
        """What was passed on so far, in order."""
        with self._lock:
            return list(self.seen)


def rewrite_link(value: str, upstream: str, base: str) -> str:
    """`value` of a `Link` header with the upstream's URLs on `base`."""
    return value.replace(f"<{upstream.rstrip('/')}/", f"<{base.rstrip('/')}/")


def request_headers(items: list[tuple[str, str]]) -> dict[str, str]:
    """The headers passed upstream: all but `Host`, `Accept-Encoding` and the
    hop-by-hop ones."""
    return {k: v for k, v in items if k.lower() not in _DROP_REQUEST}


def _connection(upstream: str) -> http.client.HTTPConnection:
    parts = urlsplit(upstream)
    host = parts.hostname or ""
    if parts.scheme == "https":
        return http.client.HTTPSConnection(
            host,
            parts.port,
            timeout=UPSTREAM_TIMEOUT,
            context=ssl.create_default_context(),
        )
    return http.client.HTTPConnection(host, parts.port, timeout=UPSTREAM_TIMEOUT)


def _handler(proxy: GateProxy) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self.forward()

        def do_POST(self) -> None:  # noqa: N802
            self.forward()

        def do_PUT(self) -> None:  # noqa: N802
            self.forward()

        def do_PATCH(self) -> None:  # noqa: N802
            self.forward()

        def do_DELETE(self) -> None:  # noqa: N802
            self.forward()

        def forward(self) -> None:
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            held = proxy.take_gate(self.command, path)
            expired = False
            if held:
                proxy.held.set()
                expired = not proxy.released.wait(HOLD_LIMIT)
            conn = _connection(proxy.upstream)
            try:
                headers = request_headers(list(self.headers.items()))
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                answer = list(resp.getheaders())
                status = resp.status
            except OSError:
                proxy.record(Seen(self.command, path, 502, held, expired))
                self.send_error(502)
                return
            finally:
                conn.close()
            proxy.record(Seen(self.command, path, status, held, expired))
            self.send_response(status)
            for name, value in answer:
                if name.lower() in _HOP:
                    continue
                link = name.lower() == "link"
                shown = (
                    rewrite_link(value, proxy.upstream, proxy.url) if link else value
                )
                self.send_header(name, shown)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Quiet: a request line may carry nothing secret, but stay terse."""

    return Handler


@contextmanager
def serve_gate_proxy(upstream: str = UPSTREAM) -> Iterator[GateProxy]:
    """The proxy on `127.0.0.1` (module doc); a held request is released and
    the server stopped on exit."""
    proxy = GateProxy("", upstream)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(proxy))
    server.daemon_threads = True
    proxy.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield proxy
    finally:
        proxy.release()
        server.shutdown()
        server.server_close()
