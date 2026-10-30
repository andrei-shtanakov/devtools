"""Offline tests of the live stand's helpers (Task 15, R15-6).

The live phases of `tests/df/live_stand.py` run only in
`.github/workflows/df-stage2-live.yml`; here: the provenance check before
any secret is read (`test_live_provenance`), the evidence guard, the row
verdicts and the loopback gate proxy against a local upstream. Nothing here
reads the stand's variables or reaches the network.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df.admin import expected_admin_digest
from df.build import GIT_ISOLATION
from df.config import ReleaseEntry, ReleasesConfig, dump_releases
from df.paths import Layout
from df.release import RELEASE_JSON
from df.stage2_config import PublisherConfig
from tests.df import gate_proxy, live_stand
from tests.df.live_stand import (
    ProvenanceError,
    RowFailed,
    StandSecrets,
)
from tests.df.roles_world import FIXTURE_FROM_CHECKOUT, OS_DIR
from tests.df.root_world import INFRA, SYSTEM_GIT, infra

RID = "r-live-1"
GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    **GIT_ISOLATION,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.invalid",
}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [str(SYSTEM_GIT), "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# ---------- provenance (r3 N2) ----------


@dataclass
class ProvWorld:
    """A checkout, a fixture repo built from it and an installed layout."""

    checkout: Path
    source_sha: str
    fixture: Path
    fixture_sha: str
    layout: Layout
    top: Path
    secrets: Path
    reads: list[Path] = field(default_factory=list)

    def state(self, fixture_sha: str) -> None:
        data = {"fixture_repo": str(self.fixture), "fixture_sha": fixture_sha}
        (self.top / live_stand.STATE_FILE).write_text(json.dumps(data))

    def release_names(self, sha: str) -> None:
        text = json.dumps({"repos": [{"name": "devtools", "sha": sha}]})
        (self.layout.release_dir(RID) / RELEASE_JSON).write_text(text)

    def registry(self, admin_digest: str) -> None:
        entry = ReleaseEntry(
            RID, "sha256:" + "1" * 64, admin_digest, "3.12.13", "allowed", None
        )
        text = dump_releases(ReleasesConfig((entry,)))
        (self.layout.etc / "releases.toml").write_text(text)

    def refit(self, message: str) -> str:
        """A new fixture commit, named by the state and `release.json`."""
        sha = commit_all(self.fixture, message)
        self.fixture_sha = sha
        self.state(sha)
        self.release_names(sha)
        return sha

    def read(self, directory: Path) -> StandSecrets:
        self.reads.append(directory)
        return StandSecrets(directory / "k", directory / "t", "ghp_notASecret")

    def gate(self, source_sha: str | None = None) -> live_stand.Provenance:
        prov, _secrets = live_stand.gate(
            self.source_sha if source_sha is None else source_sha,
            self.secrets,
            top=self.top,
            layout=self.layout,
            checkout=self.checkout,
            read=self.read,
        )
        return prov


@pytest.fixture()
def pw(tmp_path: Path) -> ProvWorld:
    """Install-time state as the install phase leaves it, all consistent."""
    base = Path(os.path.realpath(tmp_path))
    checkout = base / "checkout"
    (checkout / "df" / "sub").mkdir(parents=True)
    (checkout / "df" / "__init__.py").write_text('"""df"""\n')
    (checkout / "df" / "sub" / "tool.py").write_text("print(1)\n")
    patterns = checkout / FIXTURE_FROM_CHECKOUT[1]
    patterns.parent.mkdir(parents=True)
    patterns.write_text("APPROVAL_FINALIZE_SUFFIX=-final\n")
    (checkout / "README.md").write_text("not carried\n")
    git(checkout, "init", "-q", "--template=")
    source_sha = commit_all(checkout, "checkout")
    fixture = base / "fixture"
    fixture.mkdir()
    (fixture / "proj.txt").write_text("fixture project\n")
    for relative in FIXTURE_FROM_CHECKOUT:
        origin, target = checkout / relative, fixture / relative
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    git(fixture, "init", "-q", "--template=")
    fixture_sha = commit_all(fixture, "fixture")
    layout = Layout(base / "root")
    layout.release_dir(RID).mkdir(parents=True)
    layout.etc.mkdir(parents=True)
    os.symlink(f"releases/{RID}", layout.current)
    top = base / "top"
    top.mkdir()
    world = ProvWorld(
        checkout, source_sha, fixture, fixture_sha, layout, top, base / "secrets"
    )
    world.state(fixture_sha)
    world.release_names(fixture_sha)
    world.registry(expected_admin_digest(checkout / "df"))
    return world


def change_fixture_df(pw: ProvWorld) -> None:
    (pw.fixture / "df" / "sub" / "tool.py").write_text("print(2)\n")
    pw.refit("df changed")


def drop_patterns(pw: ProvWorld) -> None:
    git(pw.fixture, "rm", "-q", FIXTURE_FROM_CHECKOUT[1])
    pw.refit("no patterns")


def substitute_fixture_sha(pw: ProvWorld) -> None:
    """Same blobs, another commit: only `release.json` tells them apart."""
    pw.state(commit_all(pw.fixture, "empty"))


def change_checkout_df(pw: ProvWorld) -> None:
    """The checkout's `df/` differs from what the install recorded."""
    (pw.checkout / "df" / "__init__.py").write_text('"""edited"""\n')


def other_registry_digest(pw: ProvWorld) -> None:
    pw.registry("sha256:" + "2" * 64)


def lose_state(pw: ProvWorld) -> None:
    (pw.top / live_stand.STATE_FILE).unlink()


@pytest.mark.parametrize(
    ("tamper", "why"),
    [
        (change_fixture_df, "расходятся"),
        (drop_patterns, "нет contracts/approval-branches/v1/patterns.env"),
        (substitute_fixture_sha, "release.json: devtools"),
        (change_checkout_df, "admin_digest"),
        (other_registry_digest, "admin_digest"),
        (lose_state, "state.json"),
    ],
)
def test_live_provenance(
    pw: ProvWorld, tamper: Callable[[ProvWorld], None], why: str
) -> None:
    """r3 N2: a changed `df/` file (in the fixture or the checkout), a
    missing path of `FIXTURE_FROM_CHECKOUT`, a substituted `fixture_sha`, a
    registry digest not of the checkout or a lost install state: the
    provenance check fails before any secret is read."""
    tamper(pw)
    with pytest.raises(ProvenanceError, match=re.escape(why)):
        pw.gate()
    assert pw.reads == []


def test_live_provenance_other_source_sha(pw: ProvWorld) -> None:
    """`source_sha` not the checkout's commit (an older one): refused before
    the secrets."""
    (pw.checkout / "README.md").write_text("moved on\n")
    commit_all(pw.checkout, "later")
    with pytest.raises(ProvenanceError, match="source_sha"):
        pw.gate()
    assert pw.reads == []


def test_live_provenance_control(pw: ProvWorld) -> None:
    """Control: the untampered install passes and only then are the secrets
    read; both identities and the digest are reported, not equated."""
    prov = pw.gate()
    assert pw.reads == [pw.secrets]
    assert (prov.source_sha, prov.fixture_sha) == (pw.source_sha, pw.fixture_sha)
    assert prov.source_sha != prov.fixture_sha
    assert prov.admin_digest == expected_admin_digest(pw.checkout / "df")
    assert set(prov.blobs) == {
        "df/__init__.py",
        "df/sub/tool.py",
        FIXTURE_FROM_CHECKOUT[1],
    }


def test_parse_ls_tree_refuses_unknown_lines() -> None:
    text = "100644 blob " + "a" * 40 + "\tdf/x.py\0" + "040000 tree " + "b" * 40
    assert live_stand.parse_ls_tree(text + "\tdf\0") == {"df/x.py": "a" * 40}
    with pytest.raises(ProvenanceError):
        live_stand.parse_ls_tree("garbage\0")


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"repos": []}),
        json.dumps({"repos": [{"name": "devtools"}]}),
        json.dumps({"repos": [{"name": "devtools", "sha": "a"}] * 2}),
    ],
)
def test_release_member_sha_refuses(text: str) -> None:
    with pytest.raises(ProvenanceError):
        live_stand.release_member_sha(text, "devtools")


# ---------- secrets and evidence ----------


def test_record_refuses_a_secret(tmp_path: Path) -> None:
    """Evidence holding a remembered secret (or its git header form) is not
    written; other evidence is."""
    token = f"ghs_{os.urandom(12).hex()}"
    live_stand.guard_token(token, "x-access-token")
    header = live_stand.auth_header("x-access-token", token)
    for leak in (token, header):
        with pytest.raises(pytest.fail.Exception, match=INFRA):
            live_stand.record(tmp_path, "leak.json", {"x": leak})
    assert not (tmp_path / "live" / OS_DIR / "leak.json").exists()
    live_stand.record(tmp_path, "ok.json", {"x": "clean"})
    assert (tmp_path / "live" / OS_DIR / "ok.json").is_file()


def test_scan_tree_and_key_needles(tmp_path: Path) -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nQUJD" + "x" * 60 + "\n-----END RSA"
    needles = live_stand.key_needles(pem)
    assert needles == ["QUJD" + "x" * 60]
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "leak.txt").write_text(f"..{needles[0]}..")
    (tmp_path / "clean.txt").write_text("nothing")
    assert live_stand.scan_tree(tmp_path, needles) == [tmp_path / "a" / "leak.txt"]
    assert live_stand.scan_tree(tmp_path / "absent", needles) == []


def test_token_type_names_the_kind_only() -> None:
    kinds = {
        "ghp_abc": "classic PAT (ghp_)",
        "github_pat_abc": "fine-grained PAT (github_pat_)",
        "xyz": "неизвестный",
    }
    for value, kind in kinds.items():
        secrets = StandSecrets(Path("/k"), Path("/t"), value)
        assert secrets.token_type == kind
        assert value not in repr(secrets)


# ---------- rows ----------


def test_run_row_classifies() -> None:
    """`pass`, `fail` for the mechanism, `инфраструктура` for a failed
    control or any other error (a script bug is never a pass)."""
    st = object()

    def passes(_st: object) -> dict[str, object]:
        return {"x": 1}

    def fails(_st: object) -> dict[str, object]:
        raise RowFailed("mechanism")

    def asserts(_st: object) -> dict[str, object]:
        raise AssertionError("assert")

    def control(_st: object) -> dict[str, object]:
        infra("control")
        return {}

    def crashes(_st: object) -> dict[str, object]:
        raise KeyError("bug")

    got = {
        fn.__name__: live_stand.run_row(st, fn.__name__, fn)["status"]  # type: ignore[arg-type]
        for fn in (passes, fails, asserts, control, crashes)
    }
    assert got == {
        "passes": "pass",
        "fails": "fail",
        "asserts": "fail",
        "control": "инфраструктура",
        "crashes": "инфраструктура",
    }


def test_verdict_needs_every_row_and_the_cleanup() -> None:
    names = [n for n, _fn in live_stand.ROWS] + [live_stand.CLEANUP[0]]
    ok = {n: {"status": "pass"} for n in names}
    assert live_stand.verdict(ok) == live_stand.EXIT_PASS
    assert live_stand.verdict(dict(list(ok.items())[:-1])) == live_stand.EXIT_FAILED
    failed = {**ok, names[3]: {"status": "fail"}}
    assert live_stand.verdict(failed) == live_stand.EXIT_FAILED
    broken = {**failed, names[0]: {"status": "инфраструктура"}}
    assert live_stand.verdict(broken) == live_stand.EXIT_INFRA


def test_phases_refuse_outside_ci_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """R15-6: a phase never runs on a workstation (not root, or no Actions)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit, match="R15-6"):
        live_stand.main(["wipe", "--out", "/nonexistent", "--secrets", "/none"])


def test_no_test_reads_stand_variables() -> None:
    """R15-6: no module of tests/df reads the stand's variables from the
    environment (the workflow passes them as arguments to the script)."""
    prefix = "DF_" + "STAND_"
    pattern = re.compile(rf"(environ|getenv)[^\n]*{prefix}")
    here = Path(__file__).parent
    readers = [p.name for p in here.glob("*.py") if pattern.search(p.read_text())]
    assert readers == []


# ---------- the gate proxy (R15-5) ----------


@dataclass
class Upstream:
    """A local stand-in for the API: records requests, answers 200 JSON."""

    url: str = ""
    seen: list[tuple[str, str, str | None, str | None, bytes]] = field(
        default_factory=list
    )


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

        do_GET = do_POST = answer  # noqa: N815 - http.server's names

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
    assert [(m, p) for m, p, *_ in upstream.seen] == [("POST", path)] * 2
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
