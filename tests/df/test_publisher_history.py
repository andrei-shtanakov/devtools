"""Unit tests of df.publisher.history (fake transport and fake `gh`, no network)."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from df import platform
from df.platform import RunResult
from df.publisher import github, history
from df.publisher.perimeter import Unavailable
from df.publisher.trust_types import Finding
from df.stage2_config import TrustBinding

API = github.API
TOKEN = "ghp_history0123456789"
REPO = "o/df-trust"
HEAD = "a" * 40
TREE = "b" * 40
POLICY_ID = 6
BINDING = TrustBinding("github.com", 5, REPO, "main", POLICY_ID, "public")
Reply = tuple[int, Mapping[str, str], bytes]


def _json(obj: object, status: int = 200) -> Reply:
    return status, {}, json.dumps(obj).encode()


class FakeSend:
    """The wire under `history_transport`: answers by URL, records headers."""

    def __init__(self, routes: Mapping[str, Reply]) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Reply:
        self.calls.append((method, url, dict(headers), body))
        return self.routes[url]


def _perms(push: object = False, maintain: object = False, admin: object = False):
    return _json({"permissions": {"push": push, "maintain": maintain, "admin": admin}})


def _report_routes(policy: Reply, trust: Reply) -> dict[str, Reply]:
    return {
        f"{API}/repositories/{POLICY_ID}": policy,
        f"{API}/repositories/{BINDING.df_trust_repository_id}": trust,
    }


def _oid(raw: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()


def _source_routes(raw: bytes, *, oid: str | None = None) -> dict[str, Reply]:
    blob = oid or _oid(raw)
    base = f"{API}/repos/{REPO}"
    content = base64.b64encode(raw).decode()
    return {
        f"{base}/commits/{HEAD}": _json(
            {"sha": HEAD, "commit": {"tree": {"sha": TREE}}}
        ),
        f"{base}/git/trees/{TREE}": _json(
            {
                "truncated": False,
                "tree": [
                    {"path": "README.md", "type": "blob", "sha": "c" * 40},
                    {"path": "source.env", "type": "blob", "sha": blob},
                ],
            }
        ),
        f"{base}/git/blobs/{blob}": _json(
            {
                "sha": blob,
                "encoding": "base64",
                "content": content[:8] + "\n" + content[8:],
            }
        ),
    }


# --- history_token -------------------------------------------------------------


class FakeGh:
    """Stands in for `platform.run_command`; records argv and env."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(
        self, argv: Sequence[str], *, timeout: float = 60.0, env: Mapping[str, str]
    ) -> RunResult:
        self.calls.append((list(argv), dict(env)))
        assert Path(env["HOME"]).is_dir()
        return self.result


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    path = tmp_path / "history-gh"
    path.mkdir()
    (path / "hosts.yml").write_text("github.com:\n  user: reader\n")
    return path


@pytest.fixture
def role_home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _ok(stdout: bytes = TOKEN.encode() + b"\n") -> RunResult:
    return RunResult(0, stdout, b"", launched=True)


def test_no_key_anonymous(monkeypatch: pytest.MonkeyPatch, role_home: Path) -> None:
    """No `history_gh_config_dir`: `None` (anonymous), `gh` never runs."""
    gh = FakeGh(_ok())
    monkeypatch.setattr(platform, "run_command", gh)
    assert (
        history.history_token(BINDING, gh=Path("/opt/gh"), role_home=role_home) is None
    )
    assert gh.calls == []
    send = FakeSend({f"{API}/repositories/1": _json({})})
    history.history_transport(None, send=send)("GET", f"{API}/repositories/1", {}, None)
    assert "Authorization" not in send.calls[0][2]


def test_history_token_launch_contract(
    monkeypatch: pytest.MonkeyPatch, profile: Path, role_home: Path
) -> None:
    """The approved `gh` by its absolute path, exactly `GH_ENV_KEYS`, a fresh
    `HOME` in `role_home` removed after the call."""
    gh = FakeGh(_ok())
    monkeypatch.setattr(platform, "run_command", gh)
    monkeypatch.setenv("GH_TOKEN", "ghp_leak")
    binding = replace(BINDING, history_gh_config_dir=str(profile))
    token = history.history_token(binding, gh=Path("/opt/gh/gh"), role_home=role_home)
    assert token == TOKEN
    [(argv, env)] = gh.calls
    assert argv == ["/opt/gh/gh", "auth", "token", "--hostname", "github.com"]
    assert tuple(env) == platform.GH_ENV_KEYS
    assert env["GH_CONFIG_DIR"] == str(profile)
    assert Path(env["HOME"]).parent == role_home
    assert list(role_home.iterdir()) == []


@pytest.mark.parametrize(
    ("result", "tail"),
    [
        (RunResult(127, b"", b"", launched=False), "not launched"),
        (RunResult(-9, b"", b"", launched=True, timed_out=True), "timed out"),
        (RunResult(1, TOKEN.encode(), b"no token", launched=True), "exit code 1"),
        (_ok(b"\n"), "empty output"),
        (_ok(b"not a token!\n"), "output is not a token"),
    ],
)
def test_unreadable_profile_unavailable_no_anonymous(
    monkeypatch: pytest.MonkeyPatch,
    profile: Path,
    role_home: Path,
    result: RunResult,
    tail: str,
) -> None:
    """Key present, token not obtained: `Unavailable` with a fixed reason that
    carries no output -- never `None` (no anonymous fallback)."""
    monkeypatch.setattr(platform, "run_command", FakeGh(result))
    binding = replace(BINDING, history_gh_config_dir=str(profile))
    got = history.history_token(binding, gh=Path("/opt/gh"), role_home=role_home)
    assert isinstance(got, Unavailable)
    assert got.reason == f"gh auth token (history): {tail}"
    assert TOKEN not in got.reason
    assert list(role_home.iterdir()) == []


def test_profile_without_hosts_unavailable_before_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, role_home: Path
) -> None:
    """A profile the publisher cannot read (no `hosts.yml` visible) is
    `Unavailable` before `gh` runs; a relative `gh` likewise."""
    gh = FakeGh(_ok())
    monkeypatch.setattr(platform, "run_command", gh)
    binding = replace(BINDING, history_gh_config_dir=str(tmp_path / "absent"))
    got = history.history_token(binding, gh=Path("/opt/gh"), role_home=role_home)
    assert isinstance(got, Unavailable) and "no hosts.yml" in got.reason
    got = history.history_token(binding, gh=Path("gh"), role_home=role_home)
    assert isinstance(got, Unavailable) and "not absolute" in got.reason
    assert gh.calls == []


# --- transport -----------------------------------------------------------------


def test_same_token_value_for_reads_and_report() -> None:
    """One transport carries the one token value to the self-report and the
    reads; a caller's own `Authorization` never replaces it."""
    raw = b"POLICY_REPO=o/policy\n"
    routes = {
        **_report_routes(_perms(), _perms()),
        **_source_routes(raw),
        f"{API}/repos/{REPO}/branches/main": _json({"commit": {"sha": HEAD}}),
    }
    send = FakeSend(routes)
    transport = history.history_transport(TOKEN, send=send)
    assert history.self_report(transport, BINDING, POLICY_ID) is None
    assert history.read_branch_head(transport, REPO, "main") == HEAD
    assert isinstance(history.read_source_digest(transport, REPO, HEAD), str)
    transport("GET", f"{API}/repositories/{POLICY_ID}", {"Authorization": "x"}, None)
    auths = {headers.get("Authorization") for _, _, headers, _ in send.calls}
    assert auths == {f"Bearer {TOKEN}"}
    assert len(send.calls) == 7


def test_transport_get_on_api_host_only() -> None:
    """No method but `GET`, no body, no URL off the API host (the token
    would go with it)."""
    send = FakeSend({})
    transport = history.history_transport(TOKEN, send=send)
    for method, url, body in (
        ("POST", f"{API}/repositories/1", None),
        ("GET", f"{API}/repositories/1", b"{}"),
        ("GET", "https://example.com/repositories/1", None),
        ("GET", f"{API}.evil.com/x", None),
    ):
        with pytest.raises(ValueError):
            transport(method, url, {}, body)
    assert send.calls == []


def test_transport_defaults_to_github_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `send` the wire is `github.default_transport` (no proxy, no
    redirect followed), looked up at call time."""
    send = FakeSend({f"{API}/repositories/1": _json({})})
    monkeypatch.setattr(github, "default_transport", send)
    history.history_transport(None)("GET", f"{API}/repositories/1", {}, None)
    assert len(send.calls) == 1


# --- self_report ---------------------------------------------------------------


@pytest.mark.parametrize("field", ["push", "maintain", "admin"])
@pytest.mark.parametrize("on_policy", [True, False])
def test_self_report_write_permission_finding(field: str, on_policy: bool) -> None:
    """A literal `true` in any write field on either source is `token_write`
    for that source's role."""
    granted = _perms(**{field: True})
    routes = (
        _report_routes(granted, _perms())
        if on_policy
        else _report_routes(_perms(), granted)
    )
    got = history.self_report(
        history.history_transport(TOKEN, send=FakeSend(routes)), BINDING, POLICY_ID
    )
    assert isinstance(got, Finding)
    assert got.kind == "token_write"
    assert got.role == ("approval-policy" if on_policy else "df-trust")
    assert field in got.detail and TOKEN not in got.detail


def test_self_report_finding_wins_over_gap() -> None:
    """A write right on one source is a finding even when the other source's
    report is unavailable."""
    routes = _report_routes(_json({}, 404), _perms(admin=True))
    got = history.self_report(
        history.history_transport(TOKEN, send=FakeSend(routes)), BINDING, POLICY_ID
    )
    assert isinstance(got, Finding) and got.role == "df-trust"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"permissions": None},
        {"permissions": {"push": False, "maintain": False}},
        {"permissions": {"push": False, "maintain": "false", "admin": False}},
        [],
    ],
)
def test_self_report_permissions_missing_unavailable(body: object) -> None:
    """Missing `permissions` or a field that is not a bool is `Unavailable`,
    never read as `false`."""
    routes = _report_routes(_perms(), _json(body))
    got = history.self_report(
        history.history_transport(TOKEN, send=FakeSend(routes)), BINDING, POLICY_ID
    )
    assert isinstance(got, Unavailable)
    assert "df-trust" in got.reason


@pytest.mark.parametrize("status", [403, 404, 301, 500])
def test_self_report_403_404_unavailable(status: int) -> None:
    """403, 404, a redirect, a 5xx: `Unavailable` naming the status."""
    routes = _report_routes(_json({"message": "nope"}, status), _perms())
    got = history.self_report(
        history.history_transport(TOKEN, send=FakeSend(routes)), BINDING, POLICY_ID
    )
    assert isinstance(got, Unavailable)
    assert f"HTTP {status}" in got.reason and "approval-policy" in got.reason


def test_self_report_network_failure_carries_no_text() -> None:
    """A wire failure is `Unavailable` with the exception type only."""

    def broken(*_args: object) -> Reply:
        raise OSError(f"https://x?token={TOKEN}")

    got = history.self_report(
        history.history_transport(TOKEN, send=broken), BINDING, POLICY_ID
    )
    assert isinstance(got, Unavailable)
    assert "OSError" in got.reason and TOKEN not in got.reason


# --- reads ---------------------------------------------------------------------


def test_read_source_digest_raw_bytes() -> None:
    """sha256 of the raw bytes (CRLF, trailing blanks, no parsing), bound to
    the commit through its tree's blob id."""
    raw = b"POLICY_REPO=o/policy\r\nPOLICY_ADMINS=a,b  \r\n\r\n# tail"
    transport = history.history_transport(TOKEN, send=FakeSend(_source_routes(raw)))
    got = history.read_source_digest(transport, REPO, HEAD)
    assert got == hashlib.sha256(raw).hexdigest()


def test_read_source_digest_bytes_must_match_tree_blob() -> None:
    """Bytes that do not hash to the tree's blob id are `Unavailable`."""
    routes = _source_routes(b"POLICY_REPO=o/policy\n", oid="d" * 40)
    transport = history.history_transport(TOKEN, send=FakeSend(routes))
    got = history.read_source_digest(transport, REPO, HEAD)
    assert isinstance(got, Unavailable) and "do not match" in got.reason


@pytest.mark.parametrize(
    ("key", "answer"),
    [
        ("commits", _json({"sha": "e" * 40, "commit": {"tree": {"sha": TREE}}})),
        ("trees", _json({"truncated": True, "tree": []})),
        ("trees", _json({"truncated": False, "tree": []})),
        ("blobs", _json({}, 404)),
    ],
)
def test_read_source_digest_unavailable(key: str, answer: Reply) -> None:
    """Wrong commit, incomplete tree, missing file, unreadable blob."""
    raw = b"X=1\n"
    routes = _source_routes(raw)
    url = next(u for u in routes if f"/{key}/" in u)
    routes[url] = answer
    transport = history.history_transport(TOKEN, send=FakeSend(routes))
    assert isinstance(history.read_source_digest(transport, REPO, HEAD), Unavailable)


def test_read_source_digest_refuses_bad_coordinates() -> None:
    """A malformed name or short SHA never reaches the wire."""
    send = FakeSend({})
    transport = history.history_transport(TOKEN, send=send)
    assert isinstance(history.read_source_digest(transport, "o/x/y", HEAD), Unavailable)
    assert isinstance(history.read_source_digest(transport, REPO, "abc"), Unavailable)
    assert send.calls == []


def test_read_branch_head() -> None:
    """The branch name is quoted; a head that is not a full SHA is `Unavailable`."""
    url = f"{API}/repos/{REPO}/branches/release%2F1"
    ok = history.history_transport(
        TOKEN, send=FakeSend({url: _json({"commit": {"sha": HEAD}})})
    )
    assert history.read_branch_head(ok, REPO, "release/1") == HEAD
    bad = history.history_transport(
        TOKEN, send=FakeSend({url: _json({"commit": {"sha": "abc"}})})
    )
    assert isinstance(history.read_branch_head(bad, REPO, "release/1"), Unavailable)
    gone = history.history_transport(TOKEN, send=FakeSend({url: _json({}, 404)}))
    got = history.read_branch_head(gone, REPO, "release/1")
    assert isinstance(got, Unavailable) and "HTTP 404" in got.reason
