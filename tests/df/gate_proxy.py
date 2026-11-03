"""A loopback proxy to the GitHub API with one gate (Task 15 row 4(b), R15-5;
stage 2b Task 11 rows 11 and 12).

`serve_gate_proxy()` listens on `http://127.0.0.1:<port>`, the form the
publisher's `api_base` admits (`stage2_config._check_base`), and passes
every request to `upstream` (`https://api.github.com`) unchanged: method,
path with query, body and headers, minus `Host`, `Accept-Encoding` and the
hop-by-hop ones. The answer comes back the same way; the API's own URLs in a
`Link` header are rewritten to the proxy's base, so the publisher's paging
(`github._next_url`) stays on its `api_base`.

The gate acts on the first request of its method whose path (without the
query) matches its pattern whole; later ones pass:

- `hold(method, path_re)` (the default `REVIEW_HOLD`: the first `POST
  /repos/<owner>/<name>/pulls/<n>/reviews`, stage 2a) holds the request
  before it is passed on. `held` is set when it arrives; it goes on at
  `release()`, or after `HOLD_LIMIT` seconds (noted as `expired`: the
  publisher waits `github.TIMEOUT` = 20 s for an answer, so a longer hold
  would turn the row into a timeout);
- `drop_response(method, path_re)` passes the request on, waits for the
  upstream's answer and closes the connection downstream without a byte of
  it (`dropped` is set): the request was executed, its answer is lost.

Requests are recorded as method, path, status, whether held, and two facts
kept apart: `upstream_answered` (the upstream answered it, `status` is its
answer) and `answered_downstream` (the answer was written to the client) --
never headers or bodies, which carry tokens. Neither is more than that: a
request the upstream did not answer (a read timeout) may still have been
executed there, and a write that did not fail may reach a client that has
already given up.

Used only by `tests/df/live_stand.py`; its offline tests
(`tests/df/test_gate_proxy.py`) run it against a local upstream.
"""

from __future__ import annotations

import http.client
import http.server
import re
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Literal
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

Mode = Literal["hold", "drop_response"]


@dataclass(frozen=True)
class Gate:
    """What the proxy does to the first `method` request on a path that
    matches `path_re` whole."""

    mode: Mode
    method: str
    path_re: re.Pattern[str]

    def matches(self, method: str, path: str) -> bool:
        """Whether a request (`path` without the query) is of this gate."""
        return method == self.method and self.path_re.fullmatch(path) is not None


def hold(method: str, path_re: str | re.Pattern[str]) -> Gate:
    """Hold the request until `release()` (or `HOLD_LIMIT`), then pass it on."""
    return Gate("hold", method, re.compile(path_re))


def drop_response(method: str, path_re: str | re.Pattern[str]) -> Gate:
    """Pass the request on, wait for the answer, close the connection
    downstream without it."""
    return Gate("drop_response", method, re.compile(path_re))


#: The stage-2a gate (row 4(b)): the first review POST is held.
REVIEW_HOLD = hold("POST", REVIEWS_POST)


@dataclass(frozen=True)
class Seen:
    """One request the proxy took (no header, no body): `status` is the
    upstream's answer when `upstream_answered`, else the proxy's 502 (the
    request may still have been executed upstream); `answered_downstream`
    is whether writing that answer to the client did not fail."""

    method: str
    path: str
    status: int
    held: bool
    expired: bool = False
    upstream_answered: bool = True
    answered_downstream: bool = True


@dataclass
class GateProxy:
    """The running proxy: its base URL, the gate and what it saw."""

    url: str
    upstream: str
    gate: Gate = REVIEW_HOLD
    held: threading.Event = field(default_factory=threading.Event)
    released: threading.Event = field(default_factory=threading.Event)
    dropped: threading.Event = field(default_factory=threading.Event)
    seen: list[Seen] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _gate_taken: bool = False

    def release(self) -> None:
        """Let the held request go on (idempotent)."""
        self.released.set()

    def take_gate(self, method: str, path: str) -> bool:
        """Whether this request is the one the gate acts on (its first)."""
        if not self.gate.matches(method, path):
            return False
        with self._lock:
            if self._gate_taken:
                return False
            self._gate_taken = True
            return True

    def record(self, item: Seen) -> int:
        """Append `item`; its index (for `undelivered`)."""
        with self._lock:
            self.seen.append(item)
            return len(self.seen) - 1

    def undelivered(self, index: int) -> None:
        """The answer of request `index` could not be written downstream."""
        with self._lock:
            self.seen[index] = replace(self.seen[index], answered_downstream=False)

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
            gated = proxy.take_gate(self.command, path)
            held = gated and proxy.gate.mode == "hold"
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
                seen = Seen(
                    self.command, path, 502, held, expired, upstream_answered=False
                )
                self.write(proxy.record(seen), lambda: self.send_error(502))
                return
            finally:
                conn.close()
            if gated and proxy.gate.mode == "drop_response":
                dropped = Seen(
                    self.command, path, status, False, answered_downstream=False
                )
                proxy.record(dropped)
                self.drop()
                return
            index = proxy.record(Seen(self.command, path, status, held, expired))
            self.write(index, lambda: self.answer(status, answer, data))

        def answer(
            self, status: int, answer: list[tuple[str, str]], data: bytes
        ) -> None:
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
            self.wfile.flush()

        def write(self, index: int, send: Callable[[], None]) -> None:
            """`send` the answer of request `index` downstream; a failed
            write is noted (`answered_downstream=False`)."""
            try:
                send()
            except OSError:
                proxy.undelivered(index)
                self.close_connection = True

        def drop(self) -> None:
            """Close the connection downstream without a byte of the answer."""
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            proxy.dropped.set()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Quiet: a request line may carry nothing secret, but stay terse."""

    return Handler


@contextmanager
def serve_gate_proxy(
    upstream: str = UPSTREAM, *, gate: Gate = REVIEW_HOLD
) -> Iterator[GateProxy]:
    """The proxy on `127.0.0.1` with `gate` (module doc); a held request is
    released and the server stopped on exit."""
    proxy = GateProxy("", upstream, gate)
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
