"""Offline tests of the loopback gate proxy (`tests/df/gate_proxy.py`).

The proxy runs against a local upstream on `127.0.0.1`: the stage-2a gate
(the first review POST held, R15-5) and the stage-2b gates of the live merge
rows (Task 11): `hold(method, path_re)` keeps a request until `release()`,
`drop_response(method, path_re)` passes it on, waits for the answer and cuts
the connection downstream. Nothing here reaches the network.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from df.publisher import github
from df.publisher.github import GitHubUnavailable
from df.stage2_config import PublisherConfig
from tests.df import gate_proxy

MERGE = "/repos/o/r/pulls/7/merge"
MERGE_RE = r"/repos/o/r/pulls/7/merge"


@dataclass
class Upstream:
    """A local stand-in for the API: records requests, answers 200 JSON."""

    url: str = ""
    seen: list[tuple[str, str, str | None, str | None, bytes]] = field(
        default_factory=list
    )

    def calls(self) -> list[tuple[str, str]]:
        """`(method, path)` of what reached the upstream, in order."""
        return [(m, p) for m, p, *_ in self.seen]


@pytest.fixture()
def upstream() -> Iterator[Upstream]:
    up = Upstream()

    class Handler(http.server.BaseHTTPRequestHandler):
        def answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            up.seen.append(
                (
                    self.command,
                    self.path,
                    self.headers.get("Host"),
                    self.headers.get("Authorization"),
                    body,
                )
            )
            data = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            link = f'<{up.url}/repos/o/r/pulls/1/reviews?page=2>; rel="next"'
            self.send_header("Link", link)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = answer  # noqa: N815 - http.server's names

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Quiet."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    up.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield up
    finally:
        server.shutdown()
        server.server_close()


def fetch(url: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url, data=body, method=method, headers={"Authorization": "Bearer t0k"}
    )
    with opener.open(req, timeout=10) as resp:
        return resp.status, dict(resp.headers)


# ---------- the stage-2a gate (R15-5) ----------


def test_gate_proxy_passes_requests_through(upstream: Upstream) -> None:
    """A request reaches the upstream unchanged but for `Host`; the answer's
    `Link` is on the proxy's base; the publisher admits the proxy's URL as
    its `api_base`."""
    with gate_proxy.serve_gate_proxy(upstream.url) as proxy:
        PublisherConfig({}, proxy.url)
        status, headers = fetch(f"{proxy.url}/repos/o/r/pulls/1?x=1")
        seen = proxy.requests()
    assert status == 200
    assert headers["Link"].startswith(f"<{proxy.url}/repos/o/r/pulls/1/reviews")
    method, path, host, auth, _body = upstream.seen[0]
    assert (method, path, auth) == ("GET", "/repos/o/r/pulls/1?x=1", "Bearer t0k")
    assert host == upstream.url.removeprefix("http://")
    assert [(s.method, s.path, s.status, s.held) for s in seen] == [
        ("GET", "/repos/o/r/pulls/1", 200, False)
    ]
    assert [(s.delivered_upstream, s.answered_downstream) for s in seen] == [
        (True, True)
    ]


def test_gate_proxy_holds_the_first_review_post(upstream: Upstream) -> None:
    """The first `POST …/pulls/{n}/reviews` waits for `release()` before it
    reaches the upstream; a second one is not held."""
    path = "/repos/o/r/pulls/7/reviews"
    results: list[int] = []
    with gate_proxy.serve_gate_proxy(upstream.url) as proxy:
        worker = threading.Thread(
            target=lambda: results.append(fetch(proxy.url + path, "POST", b"{}")[0])
        )
        worker.start()
        assert proxy.held.wait(10)
        assert upstream.seen == []
        proxy.release()
        worker.join(10)
        fetch(proxy.url + path, "POST", b"{}")
        seen = proxy.requests()
    assert results == [200]
    assert upstream.calls() == [("POST", path)] * 2
    assert [(s.held, s.expired) for s in seen] == [(True, False), (False, False)]


def test_gate_proxy_hold_expires(
    upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `release()` the held POST goes on after `HOLD_LIMIT`, noted
    `expired` (the row then fails as infrastructure)."""
    monkeypatch.setattr(gate_proxy, "HOLD_LIMIT", 0.2)
    with gate_proxy.serve_gate_proxy(upstream.url) as proxy:
        fetch(f"{proxy.url}/repos/o/r/pulls/7/reviews", "POST", b"{}")
        seen = proxy.requests()
    assert [(s.held, s.expired) for s in seen] == [(True, True)]


# ---------- the stage-2b gates (Task 11) ----------


def test_hold_put_orders_hold_push_release(upstream: Upstream) -> None:
    """Row 11's order: the `PUT` is held before the upstream sees it; a
    push made meanwhile (here a POST straight to the upstream) reaches the
    upstream first; after `release()` the `PUT` goes on, once."""
    gate = gate_proxy.hold("PUT", MERGE_RE)
    results: list[int] = []
    with gate_proxy.serve_gate_proxy(upstream.url, gate=gate) as proxy:
        worker = threading.Thread(
            target=lambda: results.append(fetch(proxy.url + MERGE, "PUT", b"{}")[0])
        )
        worker.start()
        assert proxy.held.wait(10)
        assert upstream.calls() == []
        fetch(f"{upstream.url}/repos/o/r/contents/stop.yaml", "POST", b"{}")
        assert upstream.calls() == [("POST", "/repos/o/r/contents/stop.yaml")]
        proxy.release()
        worker.join(10)
        seen = proxy.requests()
    assert results == [200]
    assert upstream.calls() == [
        ("POST", "/repos/o/r/contents/stop.yaml"),
        ("PUT", MERGE),
    ]
    assert [(s.method, s.held, s.expired) for s in seen] == [("PUT", True, False)]
    assert (seen[0].delivered_upstream, seen[0].answered_downstream) == (True, True)


def test_hold_put_lets_other_requests_pass(upstream: Upstream) -> None:
    """A `PUT` gate does not hold the attestation POST or a read of the PR
    (they reach the upstream while nothing is held)."""
    gate = gate_proxy.hold("PUT", MERGE_RE)
    with gate_proxy.serve_gate_proxy(upstream.url, gate=gate) as proxy:
        fetch(f"{proxy.url}/repos/o/r/pulls/7/reviews", "POST", b"{}")
        fetch(f"{proxy.url}/repos/o/r/pulls/7")
        seen = proxy.requests()
    assert not proxy.held.is_set()
    assert [(s.method, s.held) for s in seen] == [("POST", False), ("GET", False)]


def test_drop_response_executes_one_put_and_cuts_the_answer(
    upstream: Upstream,
) -> None:
    """Row 12: the publisher's `PUT` (its own client) reaches the upstream
    and is executed there once; its answer never comes back, the client sees
    a transport failure (`GitHubUnavailable`, no retry). The log says the
    request was delivered upstream (with the upstream's status) and the
    answer not delivered downstream; a later request passes normally."""
    gate = gate_proxy.drop_response("PUT", MERGE_RE)
    body = {"merge_method": "squash", "sha": "a" * 40}
    with gate_proxy.serve_gate_proxy(upstream.url, gate=gate) as proxy:
        with pytest.raises(GitHubUnavailable):
            github.call("PUT", MERGE, "t0k", body, api_base=proxy.url)
        assert proxy.dropped.is_set()
        status, _ = github.call("GET", "/repos/o/r/pulls/7", "t0k", api_base=proxy.url)
        seen = proxy.requests()
    assert status == 200
    assert upstream.calls() == [("PUT", MERGE), ("GET", "/repos/o/r/pulls/7")]
    assert [
        (s.method, s.status, s.delivered_upstream, s.answered_downstream) for s in seen
    ] == [("PUT", 200, True, False), ("GET", 200, True, True)]
    assert not proxy.held.is_set()


def test_failed_upstream_is_not_delivered(upstream: Upstream) -> None:
    """An upstream that does not answer: the request is logged as not
    delivered upstream (502 downstream), never as executed."""
    dead = f"http://127.0.0.1:{_free_port()}"
    with gate_proxy.serve_gate_proxy(dead) as proxy:
        with pytest.raises(GitHubUnavailable):
            github.call("GET", "/repos/o/r/pulls/7", "t0k", api_base=proxy.url)
        seen = proxy.requests()
    assert [(s.status, s.delivered_upstream, s.answered_downstream) for s in seen] == [
        (502, False, True)
    ]


def test_gate_matches_method_and_whole_path() -> None:
    gate = gate_proxy.hold("PUT", MERGE_RE)
    assert gate.matches("PUT", MERGE)
    assert not gate.matches("GET", MERGE)
    assert not gate.matches("PUT", MERGE + "/x")
    assert not gate.matches("PUT", "/repos/o/r/pulls/17/merge")
    assert gate_proxy.REVIEW_HOLD.matches("POST", "/repos/o/r/pulls/3/reviews")
    assert gate_proxy.drop_response("PUT", MERGE_RE).mode == "drop_response"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
