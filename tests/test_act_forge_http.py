"""`TokenTransport`: токен-транспорт REST/GraphQL (только локальные подделки)."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from typing import Any

import pytest

pytest.importorskip("steward", reason="steward отсутствует: без группы governance")

from steward.approvalfacts.v3 import Request  # noqa: E402

from governance import act_anon  # noqa: E402
from governance.act_forge_http import TokenTransport  # noqa: E402

BASE = "https://forge.test/api/v3/"
TOKEN = "tok-SECRET-123"


class _Answer(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, link: str | None = None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        if link:
            self.headers["Link"] = link

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class _Opener:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.reqs: list[Any] = []
        self.timeouts: list[float] = []

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        self.reqs.append(req)
        self.timeouts.append(timeout)
        ans = self.answers.pop(0)
        if isinstance(ans, BaseException):
            raise ans
        return ans


def _t(*answers: Any) -> tuple[TokenTransport, _Opener]:
    op = _Opener(*answers)
    return TokenTransport(TOKEN, api_base=BASE, opener=op), op


def test_get_sends_bearer() -> None:
    t, op = _t(_Answer(b'{"a": 1}', link='<x>; rel="last"'))
    resp = t.send(Request("GET", "repos/o/r/pulls/1"))
    assert (resp.status, resp.body, resp.link) == (200, {"a": 1}, '<x>; rel="last"')
    req = op.reqs[0]
    assert req.full_url == BASE + "repos/o/r/pulls/1"
    assert req.get_method() == "GET"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"


def test_graphql_query_only() -> None:
    t, op = _t(_Answer(b'{"data": {}}'))
    q = Request("POST", "graphql", op="Q", variables={"a": 1}, query="query Q{x}")
    assert t.send(q).status == 200
    req = op.reqs[0]
    assert req.full_url == BASE + "graphql" and req.get_method() == "POST"
    assert json.loads(req.data)["operationName"] == "Q"
    for bad in (
        Request("POST", "graphql", op="M", query="mutation M{x}"),
        Request("POST", "graphql", op="M"),
        Request("POST", "repos/o/r/issues"),
        Request("DELETE", "repos/o/r"),
        Request("GET", "/abs"),
        Request("GET", "https://evil/x"),
        Request("GET", ""),
    ):
        resp = t.send(bad)
        assert resp.status is None and resp.error
    assert len(op.reqs) == 1


def test_redirect_not_followed() -> None:
    err = urllib.error.HTTPError(BASE + "x", 302, "Found", Message(), io.BytesIO(b""))
    t, _ = _t(err)
    resp = t.send(Request("GET", "x"))
    assert resp.status == 302 and resp.body is None


def test_default_opener_does_not_follow_redirect() -> None:
    import http.server
    import threading

    hits: list[str] = []

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()

        def log_message(self, *a: object) -> None:
            return None

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t = TokenTransport(TOKEN, api_base=f"http://127.0.0.1:{srv.server_port}/")
        resp = t.send(Request("GET", "x"))
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status == 302 and hits == ["/x"]


def test_body_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(act_anon, "MAX_BODY", 8)
    t, _ = _t(_Answer(b"[" + b"1," * 10 + b"1]"))
    resp = t.send(Request("GET", "x"))
    assert resp.status is None and "больше" in resp.error


def test_env_proxy_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.server
    import threading

    seen: list[str] = []

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a: object) -> None:
            return None

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    # Прокси окружения указывает в закрытый порт: через него запрос не дошёл бы.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        t = TokenTransport(TOKEN, api_base=f"http://127.0.0.1:{srv.server_port}/")
        resp = t.send(Request("GET", "x"))
    finally:
        srv.shutdown()
        srv.server_close()
    assert resp.status == 200 and seen == [f"Bearer {TOKEN}"]


def test_token_absent_from_errors() -> None:
    t, _ = _t(OSError(f"boom {TOKEN}"))
    resp = t.send(Request("GET", "x"))
    assert resp.status is None and TOKEN not in resp.error and "***" in resp.error
    assert TOKEN not in repr(t) and TOKEN not in str(vars(resp))
    with pytest.raises(OSError) as ei:
        _t(OSError(f"boom {TOKEN}"))[0].get_json("x")
    assert TOKEN not in str(ei.value)
    with pytest.raises(ValueError):
        TokenTransport("", api_base=BASE)


def test_paging_complete() -> None:
    nxt = f'<{BASE}repos/o/r/commits/s/pulls?page=2>; rel="next"'
    t, op = _t(_Answer(b"[1,2]", link=nxt), _Answer(b"[3]"))
    assert t.get_json("repos/o/r/commits/s/pulls") == (200, [1, 2, 3])
    assert op.reqs[1].full_url == BASE + "repos/o/r/commits/s/pulls?page=2"


def test_paging_edges() -> None:
    t, _ = _t(_Answer(b'{"n": 1}'))
    assert t.get_json("repos/o/r/pulls/1") == (200, {"n": 1})
    t, _ = _t(_Answer(b'{"m": "x"}', 404))
    assert t.get_json("x") == (404, {"m": "x"})
    t, _ = _t(_Answer(b"[1]", link='<https://other/x>; rel="next"'))
    with pytest.raises(OSError):
        t.get_json("x")
    nxt = f'<{BASE}x?p=2>; rel="next"'
    for page in (_Answer(b"{}", 500), _Answer(b'{"o": 1}')):
        t, _ = _t(_Answer(b"[1]", link=nxt), page)
        with pytest.raises(OSError):
            t.get_json("x")


def test_init_refuses_empty_token() -> None:
    with pytest.raises(ValueError, match="forge: empty token"):
        TokenTransport("", api_base=BASE)


def test_send_refusal_is_status_none() -> None:
    t, op = _t()
    resp = t.send(Request("DELETE", "repos/o/r"))
    assert resp.status is None
    assert resp.error == "forge: read-only transport: DELETE repos/o/r"
    assert op.reqs == []


def test_refusal_unsafe_path() -> None:
    t, _ = _t()
    resp = t.send(Request("GET", "/abs"))
    assert resp.error == "forge: not a relative API path: '/abs'"


def test_refusal_graphql_not_query() -> None:
    t, _ = _t()
    resp = t.send(Request("POST", "graphql", op="M", query="mutation M{x}"))
    assert resp.error == "forge: GraphQL text is not a query"


def test_get_json_delivery_failure() -> None:
    t, _ = _t(OSError("down"))
    with pytest.raises(OSError, match="forge: OSError: down"):
        t.get_json("x")


def test_get_json_mid_walk_status() -> None:
    t, _ = _t(_Answer(b"[1]", link=f'<{BASE}x?p=2>; rel="next"'), _Answer(b"{}", 500))
    with pytest.raises(OSError, match="forge: HTTP 500 mid-walk"):
        t.get_json("x")


def test_get_json_mid_walk_shape() -> None:
    t, _ = _t(_Answer(b"[1]", link=f'<{BASE}x?p=2>; rel="next"'), _Answer(b"{}"))
    with pytest.raises(OSError, match="forge: page shape changed mid-walk"):
        t.get_json("x")


def test_get_json_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("governance.act_forge_http.MAX_PAGES", 2)
    nxt = f'<{BASE}x?p=2>; rel="next"'
    t, _ = _t(_Answer(b"[1]", link=nxt), _Answer(b"[2]", link=nxt))
    with pytest.raises(OSError, match="forge: more than 2 pages"):
        t.get_json("x")


def test_get_json_next_outside_base() -> None:
    t, _ = _t(_Answer(b"[1]", link='<https://other/x>; rel="next"'))
    with pytest.raises(OSError, match="forge: Link next outside api_base"):
        t.get_json("x")


def test_get_json_page_timeout_caps_each_page() -> None:
    nxt = f'<{BASE}x?p=2>; rel="next"'
    t, op = _t(_Answer(b"[1]", link=nxt), _Answer(b"[2]"))
    cap = lambda: 99.0 if op.reqs else 5.0  # noqa: E731
    assert t.get_json("x", page_timeout=cap) == (200, [1, 2])
    assert op.timeouts == [5.0, 60.0]  # min(транспорт 60, срок) на каждой странице


class _Stop(Exception):
    pass


def _stop() -> float:
    raise _Stop("time")


def test_get_json_page_timeout_signal_stops_before_page() -> None:
    t, op = _t(_Answer(b"[1]"))
    with pytest.raises(_Stop):
        t.get_json("x", page_timeout=_stop)
    assert op.reqs == []  # страница не запрошена


def test_get_json_page_timeout_signal_in_body_is_delivery_failure() -> None:
    """Срок перепроверяется перед каждой порцией тела: сигнал посреди страницы —
    сбой доставки, следующая страница не запрашивается."""
    nxt = f'<{BASE}x?p=2>; rel="next"'
    t, op = _t(_Answer(b"[1]", link=nxt), _Answer(b"[2]"))
    with pytest.raises(OSError, match="forge: _Stop: time"):
        t.get_json("x", page_timeout=lambda: _stop() if op.reqs else 5.0)
    assert len(op.reqs) == 1


def test_get_json_without_page_timeout_uses_transport_timeout() -> None:
    t, op = _t(_Answer(b"{}"))
    t.get_json("x")
    assert op.timeouts == [60.0]


def test_get_json_empty_first_page_is_still_first() -> None:
    nxt = f'<{BASE}x?p=2>; rel="next"'
    for page in (_Answer(b"{}", 500), _Answer(b'{"o": 1}')):
        t, _ = _t(_Answer(b"[]", link=nxt), page)
        with pytest.raises(OSError, match="mid-walk"):
            t.get_json("x")
