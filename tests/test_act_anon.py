"""Анонимный транспорт: протокол steward без credentials (спека §6.2)."""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import threading
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.approvalfacts import v3  # noqa: E402

from governance import act_anon  # noqa: E402
from governance.act_time import Unavailable  # noqa: E402

VECTORS = Path(__file__).resolve().parent.parent / "contracts/approval-facts/v3/vectors"


class _Answer(io.BytesIO):
    def __init__(self, status: int, body: object, link: str | None) -> None:
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status
        self.headers = Message()
        if link is not None:
            self.headers["Link"] = link


class _Fake:
    """Подмена `opener.open`: ответы по полному URL; журнал заголовков запросов."""

    def __init__(self, routes: dict[str, tuple[int, object, str | None]]) -> None:
        self.routes = routes
        self.headers: list[dict[str, str]] = []

    def __call__(self, req, timeout: float):  # noqa: ANN001
        self.headers.append(dict(req.header_items()))
        status, body, link = self.routes[req.full_url]
        if status >= 300:
            hdrs = Message()
            if link is not None:
                hdrs["Link"] = link
            raise urllib.error.HTTPError(
                req.full_url, status, "x", hdrs, io.BytesIO(json.dumps(body).encode())
            )
        return _Answer(status, body, link)


def _get(path: str) -> v3.Request:
    return v3.Request("GET", path)


def test_no_credentials_ever() -> None:
    fake = _Fake({act_anon.API + "repos/o/r": (200, {"default_branch": "main"}, None)})
    resp = act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r"))
    assert resp.status == 200 and resp.body == {"default_branch": "main"}
    assert all("Authorization" not in h for h in fake.headers)


def test_non_2xx_is_a_response_not_an_exception() -> None:
    fake = _Fake(
        {act_anon.API + "repos/o/r/commits/" + "a" * 40: (404, {"m": "x"}, None)}
    )
    resp = act_anon.AnonTransport(open_url=fake).send(
        _get("repos/o/r/commits/" + "a" * 40)
    )
    assert (resp.status, resp.body) == (404, {"m": "x"})


def test_redirect_is_not_followed() -> None:
    fake = _Fake({act_anon.API + "repos/o/r": (301, {"url": "elsewhere"}, None)})
    assert act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r")).status == 301


def test_oversized_response_refused(monkeypatch) -> None:
    """b5 (`_response`): ответ больше предела — `Response(None, error=…)`, тело не читается."""
    monkeypatch.setattr(act_anon, "MAX_BODY", 8)
    fake = _Fake({act_anon.API + "repos/o/r": (200, {"x": "0123456789"}, None)})
    resp = act_anon.AnonTransport(open_url=fake).send(_get("repos/o/r"))
    assert resp.status is None and resp.error == "anon: ответ больше 8 байт"


def test_transport_failure_is_status_none() -> None:
    def broken(req, timeout):  # noqa: ANN001
        raise TimeoutError("slow")

    resp = act_anon.AnonTransport(open_url=broken).send(_get("repos/o/r"))
    assert resp.status is None and "TimeoutError" in resp.error


@pytest.mark.parametrize(
    "request_",
    [
        v3.Request("POST", "graphql", op="x", query="query{}"),
        v3.Request("GET", "/repos/o/r"),
        v3.Request("GET", "https://evil/x"),
        v3.Request("GET", "repos/o/r x"),
    ],
)
def test_only_relative_get(request_: v3.Request) -> None:
    resp = act_anon.AnonTransport(open_url=_Fake({})).send(request_)
    assert resp.status is None and "только GET относительного пути" in resp.error


def test_link_and_query_passed_through() -> None:
    path = "repos/o/r/commits/" + "b" * 40 + "/pulls?per_page=100"
    fake = _Fake(
        {
            act_anon.API + path: (
                200,
                [],
                '<https://api.github.com/x?page=2>; rel="next"',
            )
        }
    )
    resp = act_anon.AnonTransport(open_url=fake).send(_get(path))
    assert resp.link == '<https://api.github.com/x?page=2>; rel="next"'


def test_memo_only_for_sha_addressed_paths() -> None:
    commit = "repos/o/r/commits/" + "c" * 40
    pulls = commit + "/pulls?per_page=100"
    fake = _Fake(
        {act_anon.API + commit: (200, {}, None), act_anon.API + pulls: (200, [], None)}
    )
    t = act_anon.AnonTransport(open_url=fake)
    for _ in range(2):
        t.send(_get(commit))
        t.send(_get(pulls))
    assert t.sent == [commit, pulls, pulls], "_IMMUTABLE: память — только пути по SHA"


def test_library_reads_activation_through_it() -> None:
    """Протокол совместим: steward читает активацию вектора через наш транспорт."""
    spec = json.loads((VECTORS / "activation/ok.json").read_text("utf-8"))
    routes = {
        act_anon.API + ex["request"]["path"]: (
            ex["response"]["status"],
            ex["response"].get("body"),
            ex["response"].get("link"),
        )
        for ex in spec["transport"]["exchanges"]
    }
    got = v3.read_activation(
        act_anon.AnonTransport(open_url=_Fake(routes)),
        spec["repo"],
        spec["merge_commit"],
        files=tuple(spec["files"]),
    )
    assert not isinstance(got, v3.Unavailable), got
    assert got.merge_commit == spec["merge_commit"]


def _contents(raw: bytes, *, sha: str | None = None) -> dict:
    oid = hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
        "sha": sha or oid,
    }


MODES = b"modes: {}\n"
MODES_OID = hashlib.sha1(b"blob %d\x00" % len(MODES) + MODES).hexdigest()
REF = "d" * 40
ROOT = "1" * 40
SUB = "2" * 40


def _tree(*entries: tuple[str, str, str], truncated: bool = False) -> dict:
    return {
        "truncated": truncated,
        "tree": [{"path": n, "type": k, "sha": s} for n, k, s in entries],
    }


def _world(*, file_path: str = "policy/modes.yaml", **over: tuple) -> dict:
    """Маршруты: коммит -> корень {policy/, epochs? нет} -> policy/{modes.yaml}."""
    base = act_anon.API + "repos/o/r/"
    routes = {
        base + f"commits/{REF}": (200, {"commit": {"tree": {"sha": ROOT}}}, None),
        base + f"git/trees/{ROOT}": (200, _tree(("policy", "tree", SUB)), None),
        base + f"git/trees/{SUB}": (
            200,
            _tree(("modes.yaml", "blob", MODES_OID)),
            None,
        ),
        base + f"contents/{file_path}?ref={REF}": (
            200,
            _contents(b"modes: {}\n"),
            None,
        ),
    }
    routes.update({base + k: v for k, v in over.items()})
    return routes


def _anon(routes: dict) -> tuple[act_anon.AnonTransport, _Fake]:
    fake = _Fake(routes)
    return act_anon.AnonTransport(open_url=fake), fake


def test_contents_bytes_bound_to_tree_blob_oid() -> None:
    """Самосогласованный ответ contents с чужим blob — не байты пути в коммите."""
    other = b"other\n"
    t, _ = _anon(
        _world(
            **{f"contents/policy/modes.yaml?ref={REF}": (200, _contents(other), None)}
        )
    )
    got = act_anon.contents_bytes(t, "o/r", "policy/modes.yaml", REF)
    assert isinstance(got, Unavailable)
    assert "blob дерева" in got.reason


def test_contents_bytes_checked_against_blob_oid() -> None:
    t, _ = _anon(_world())
    assert act_anon.contents_bytes(t, "o/r", "policy/modes.yaml", REF) == b"modes: {}\n"
    bad, _ = _anon(
        _world(
            **{
                f"contents/policy/modes.yaml?ref={REF}": (
                    200,
                    _contents(b"x", sha="e" * 40),
                    None,
                )
            }
        )
    )
    got = act_anon.contents_bytes(bad, "o/r", "policy/modes.yaml", REF)
    assert isinstance(got, Unavailable) and "содержимое не равно blob" in got.reason


def test_absent_only_from_full_tree() -> None:
    t, _ = _anon(_world())
    assert act_anon.contents_bytes(t, "o/r", "epochs.yaml", REF) is None
    assert act_anon.contents_bytes(t, "o/r", "policy/other.yaml", REF) is None
    assert act_anon.contents_bytes(t, "o/r", "policy/modes.yaml/x", REF) is None


def test_contents_404_with_path_in_tree_is_unavailable() -> None:
    t, _ = _anon(_world(**{f"contents/policy/modes.yaml?ref={REF}": (404, {}, None)}))
    got = act_anon.contents_bytes(t, "o/r", "policy/modes.yaml", REF)
    assert isinstance(got, Unavailable) and "HTTP 404" in got.reason


def test_truncated_tree_is_unavailable_not_absent() -> None:
    t, _ = _anon(_world(**{f"git/trees/{ROOT}": (200, _tree(truncated=True), None)}))
    got = act_anon.contents_bytes(t, "o/r", "epochs.yaml", REF)
    assert isinstance(got, Unavailable) and "не полностью" in got.reason


@pytest.mark.parametrize("status", [403, 404, 500])
def test_unreadable_commit_is_unavailable(status: int) -> None:
    t, _ = _anon(_world(**{f"commits/{REF}": (status, {"message": "x"}, None)}))
    got = act_anon.contents_bytes(t, "o/r", "epochs.yaml", REF)
    assert isinstance(got, Unavailable) and f"HTTP {status}" in got.reason


@pytest.mark.parametrize("repo", ["approval-policy", "o/", "/r", "o/r/x", "o/r?x"])
def test_bad_repo_slug_sends_nothing(repo: str) -> None:
    t, fake = _anon({})
    got = act_anon.contents_bytes(t, repo, "a", REF)
    assert isinstance(got, Unavailable) and t.sent == [] and fake.headers == []


class _Redirector(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        code = int(self.path.rsplit("/", 1)[-1])
        self.send_response(code)
        self.send_header("Location", "/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_real_opener_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Настоящий opener/_NoRedirect против loopback-сервера."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(act_anon, "API", f"http://127.0.0.1:{server.server_port}/")
        resp = act_anon.AnonTransport().send(_get(f"repos/o/r/{code}"))
    finally:
        server.shutdown()
        server.server_close()
    assert resp.status == code


def test_env_proxy_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(act_anon, "API", f"http://127.0.0.1:{server.server_port}/")
        monkeypatch.setenv("NO_PROXY", "")
        resp = act_anon.AnonTransport().send(_get("repos/o/r/301"))
    finally:
        server.shutdown()
        server.server_close()
    assert resp.status == 301, "запрос ушёл на сервер, а не в прокси из окружения"


@pytest.mark.parametrize(
    "path",
    [
        "repos/o/r/branches/" + "a" * 40,
        "repos/o/r/contents/x?ref=main",
        "repos/o/r/git/refs/heads/main",
    ],
)
def test_memo_negatives(path: str) -> None:
    fake = _Fake({act_anon.API + path: (200, {}, None)})
    t = act_anon.AnonTransport(open_url=fake)
    t.send(_get(path))
    t.send(_get(path))
    assert t.sent == [path, path]


def test_memo_returns_copies() -> None:
    path = "repos/o/r/commits/" + "c" * 40
    t = act_anon.AnonTransport(
        open_url=_Fake({act_anon.API + path: (200, {"k": [1]}, None)})
    )
    t.send(_get(path)).body["k"].append(2)
    t.send(_get(path)).body["k"].append(3)
    assert t.send(_get(path)).body == {"k": [1]}


def test_contents_bytes_rejects_non_sha_ref() -> None:
    t = act_anon.AnonTransport(open_url=_Fake({}))
    got = act_anon.contents_bytes(t, "o/r", "a", "main&x=1")
    assert isinstance(got, Unavailable) and t.sent == []
