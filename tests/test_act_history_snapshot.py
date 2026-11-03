"""`act_history_snapshot`: история доставок без `gh` — настоящий `git`, локальный
dumb-HTTP git-сервер на 127.0.0.1 и replay REST-ответов форджи."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Iterator
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from governance.act_envelope import DeliveredRequest
from governance.act_forge_http import TokenTransport
from governance.act_history import load_history, pr_for_merge
from governance.act_history_snapshot import (
    DeadlineExceeded,
    Scope,
    SnapshotHistory,
    git_auth_env,
    open_snapshot,
)
from governance.act_projection import self_hash_v
from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    render_manifest,
)
from governance.act_time import Unavailable

#: Чтение REST (`TokenTransport.get_json`) тянет steward; git-строки — нет и
#: идут и в CI без группы governance.
needs_steward = pytest.mark.skipif(
    importlib.util.find_spec("steward") is None,
    reason="steward — без группы governance",
)
WS, SLUG = "ws-7", "owner/p"
H_REQ = self_hash_v("---\nspec_stage: requirements\n---\nтело\n", "review/v1")
PIN = PolicyPin(
    "owner/approval-policy", "main", "p" * 40, {"policy/modes.yaml": "0" * 64}
)
TOKEN = "ghs_SECRET-token-123"
BASIC = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
B64 = BASIC.removeprefix("Basic ")
API = "https://api.forge.test/"
MASTER = "refs/heads/master"
ALLOWED = {
    "first_parent_changes",
    "show_file",
    "show_file_bytes",
    "rev_parse",
    "fetch_branch",
    "prs_containing_commit",
    "pr_facts",
}


# --- стенд: репозиторий продукта, git-сервер, форджа -----------------------------


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.st"]
        + list(args),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


class Product:
    """Рабочий репозиторий продукта и его bare-копия, которую отдаёт сервер."""

    def __init__(self, root: Path) -> None:
        self.work, self.served_root = root / "work", root / "served"
        self.served = self.served_root / f"{SLUG}.git"
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "master")
        (self.work / "README.md").write_text("x\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", "init")
        entry = NodeEntry("spec/10-requirements.md", H_REQ, 1)
        self.req = NodeRequest(
            "approve-1-0-1",
            WS,
            SLUG,
            {"requirements": entry},
            {"requirements": {}},
            PIN,
        )
        self.path = manifest_path(WS, self.req.request_id)

    def branch(self, name: str) -> str:
        _git(self.work, "switch", "-qc", name)
        file = self.work / self.path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(render_manifest(self.req))
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", name)
        head = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "switch", "-q", "master")
        return head

    def merge(self, name: str) -> str:
        _git(self.work, "merge", "-q", "--no-ff", name, "-m", f"Merge {name}")
        return _git(self.work, "rev-parse", "HEAD")

    def squash(self, name: str) -> str:
        _git(self.work, "merge", "-q", "--squash", name)
        _git(self.work, "commit", "-qm", f"{name} (squash)")
        return _git(self.work, "rev-parse", "HEAD")

    def publish(self, pulls: dict[int, str], drop: tuple[str, ...] = ()) -> None:
        """Bare-копия для сервера: `refs/pull/N/head`, без веток `drop`."""
        self.served_root.joinpath(SLUG).parent.mkdir(parents=True, exist_ok=True)
        _git(self.work, "clone", "-q", "--bare", str(self.work), str(self.served))
        for n, sha in pulls.items():
            _git(self.served, "update-ref", f"refs/pull/{n}/head", sha)
        for name in drop:
            _git(self.served, "branch", "-D", name)
        _git(self.served, "update-server-info")


class GitServer:
    """Dumb HTTP: статические файлы bare-репозиториев; только `Basic x-access-token`."""

    def __init__(self, root: Path) -> None:
        self.log: list[tuple[str, str | None]] = []
        log = self.log

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — имя метода http.server
                auth = self.headers.get("Authorization")
                log.append((self.path, auth))
                rel = urllib.parse.urlsplit(self.path).path.lstrip("/")
                file = (root / rel).resolve()
                if auth != BASIC:
                    self._send(401, b"", {"WWW-Authenticate": 'Basic realm="t"'})
                elif not file.is_relative_to(root.resolve()) or not file.is_file():
                    self._send(404, b"")
                else:
                    self._send(200, file.read_bytes())

            def _send(self, code: int, data: bytes, extra: dict | None = None) -> None:
                self.send_response(code)
                for k, v in {**(extra or {}), "Content-Type": "text/plain"}.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


class _Answer(io.BytesIO):
    def __init__(self, body: object, status: int = 200, link: str = "") -> None:
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, Message()
        if link:
            self.headers["Link"] = link

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class Forge:
    """Replay REST: путь → (статус, тело); каждый запрос — с Bearer токена."""

    def __init__(self, answers: dict[str, tuple]) -> None:
        self.answers, self.paths = answers, []

    def __call__(self, req: Any, timeout: float = 0) -> _Answer:
        assert req.get_header("Authorization") == f"Bearer {TOKEN}"
        path = req.full_url.removeprefix(API)
        self.paths.append(path)
        status, body, *link = self.answers[path]
        return _Answer(body, status, *link)


def _merged(n: int, merge_commit: str) -> dict:
    return {
        "number": n,
        "state": "closed",
        "merged_at": "2026-11-03T10:00:00Z",
        "merge_commit_sha": merge_commit,
    }


def _forge(merge_commit: str, head: str, pulls: list[dict] | None = None) -> Forge:
    return Forge(
        {
            f"repos/{SLUG}/commits/{merge_commit}/pulls": (
                200,
                [_merged(7, merge_commit)] if pulls is None else pulls,
            ),
            f"repos/{SLUG}/pulls/7": (200, {"number": 7, "head": {"sha": head}}),
        }
    )


@pytest.fixture()
def product(tmp_path: Path) -> Product:
    return Product(tmp_path)


@pytest.fixture()
def server(product: Product) -> Iterator[GitServer]:
    srv = GitServer(product.served_root)
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _snapshot(
    tmp_path: Path, server: GitServer, forge: Any, clock: Clock | None = None
) -> SnapshotHistory:
    """Снимок через сервер; с `clock` — поддельные часы и срок 100.0."""
    home = tmp_path / "home"
    home.mkdir()
    git_dir = open_snapshot(
        home, SLUG, [MASTER], TOKEN, git_base=server.base, deadline=_soon()
    )
    transport = TokenTransport(TOKEN, api_base=API, opener=forge)
    if clock is None:
        return SnapshotHistory(
            git_dir, transport, TOKEN, git_base=server.base, deadline=_soon()
        )
    return SnapshotHistory(
        git_dir, transport, TOKEN, git_base=server.base, deadline=100.0, clock=clock
    )


class Clock:
    """Поддельные часы: `deadline=100.0`; `now` двигает тест или подделка форджи."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _soon(seconds: float = 60.0) -> float:
    return time.monotonic() + seconds


SCOPE = Scope(WS, SLUG, "/nonexistent-target-dir")


# --- история через снимок ----------------------------------------------------------


@needs_steward
def test_production_load_history_sees_delivered_request(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    head = product.branch("cand")
    m1 = product.merge("cand")
    product.publish({7: head})
    forge = _forge(m1, head)
    got = load_history(_snapshot(tmp_path, server, forge), SCOPE, MASTER)
    assert got == (DeliveredRequest(m1, head, product.req),)
    assert forge.paths == [f"repos/{SLUG}/commits/{m1}/pulls", f"repos/{SLUG}/pulls/7"]


@needs_steward
def test_squash_delivery_head_fetched_from_pull_ref(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    head = product.branch("cand")
    sq = product.squash("cand")
    product.publish({7: head}, drop=("cand",))
    snap = _snapshot(tmp_path, server, _forge(sq, head))
    assert snap.rev_parse("", f"{head}^{{commit}}") is None  # в снимке головы нет
    got = load_history(snap, SCOPE, MASTER)
    assert got == (DeliveredRequest(sq, head, product.req),)
    assert snap.rev_parse("", "refs/pull/7/head") == head


@needs_steward
def test_squash_head_without_pull_ref_unavailable(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    head = product.branch("cand")
    sq = product.squash("cand")
    product.publish({}, drop=("cand",))
    got = load_history(_snapshot(tmp_path, server, _forge(sq, head)), SCOPE, MASTER)
    assert isinstance(got, Unavailable) and "не догружена" in got.reason


@needs_steward
@pytest.mark.parametrize("pulls", ["none", "open"])
def test_direct_commit_is_not_delivery(
    tmp_path: Path, product: Product, server: GitServer, pulls: str
) -> None:
    """Манифест, добавленный прямым коммитом в базу (без смёрженного PR), — не
    доставка: ни пустой ответ форджи, ни открытый PR с тем же коммитом."""
    file = product.work / product.path
    file.parent.mkdir(parents=True)
    file.write_text(render_manifest(product.req))
    _git(product.work, "add", "-A")
    _git(product.work, "commit", "-qm", "direct")
    direct = _git(product.work, "rev-parse", "HEAD")
    product.publish({})
    open_pr = {"number": 7, "state": "open", "merged_at": None}
    listed = [] if pulls == "none" else [open_pr | {"merge_commit_sha": direct}]
    got = load_history(
        _snapshot(tmp_path, server, _forge(direct, direct, listed)), SCOPE, MASTER
    )
    assert isinstance(got, Unavailable) and "нужен ровно один" in got.reason


def test_unlisted_method_raises(tmp_path: Path) -> None:
    transport = TokenTransport(TOKEN, api_base=API, opener=Forge({}))
    snap = SnapshotHistory(
        tmp_path, transport, TOKEN, git_base="http://x", deadline=_soon()
    )
    public = {n for n in dir(snap) if not n.startswith("_")}
    assert public == ALLOWED
    for name in ("merge", "pr_body_fact", "snapshot_open", "show_file_for_carry"):
        with pytest.raises(AttributeError):
            getattr(snap, name)


def test_history_unreadable_base_is_unavailable(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    product.publish({})
    got = load_history(_snapshot(tmp_path, server, Forge({})), SCOPE, "refs/heads/x")
    assert got == Unavailable("история refs/heads/x: first-parent не прочитан")


# --- авторизация git и токен ------------------------------------------------------


def test_git_auth_env_exact() -> None:
    assert git_auth_env(TOKEN) == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: {BASIC}",
    }


@needs_steward
def test_server_accepts_basic_rejects_bearer(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    head = product.branch("cand")
    sq = product.squash("cand")
    product.publish({7: head}, drop=("cand",))
    bearer = tmp_path / "bearer.git"
    _git(tmp_path, "init", "-q", "--bare", str(bearer))
    refused = subprocess.run(
        ["git", f"--git-dir={bearer}", "-c"]
        + [f"http.extraHeader=Authorization: Bearer {TOKEN}"]
        + ["fetch", "-q", f"{server.base}/{SLUG}.git", f"+{MASTER}:{MASTER}"],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
        check=False,
    )
    assert refused.returncode != 0
    assert server.log and {a for _p, a in server.log} == {f"Bearer {TOKEN}"}
    server.log.clear()
    snap = _snapshot(tmp_path, server, _forge(sq, head))
    assert load_history(snap, SCOPE, MASTER) == (
        DeliveredRequest(sq, head, product.req),
    )
    assert {a for _p, a in server.log} == {BASIC}
    assert any(p.endswith("/info/refs?service=git-upload-pack") for p, _a in server.log)
    assert len([p for p, _a in server.log if "info/refs" in p]) == 2  # open + pull ref


@needs_steward
def test_git_argv_and_config_have_no_token(
    tmp_path: Path,
    product: Product,
    server: GitServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = product.branch("cand")
    sq = product.squash("cand")
    product.publish({7: head}, drop=("cand",))
    argvs: list[list[str]] = []
    real = subprocess.Popen

    def spy(args: list[str], **kw: Any) -> subprocess.Popen:
        argvs.append(list(args))
        assert set(kw["env"]) <= {
            "PATH",
            "HOME",
            "LC_ALL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_TERMINAL_PROMPT",
            "GIT_ALLOW_PROTOCOL",
            *git_auth_env(TOKEN),
        }
        # заголовок авторизации — только у fetch; log/show/rev-parse/config без него
        assert ("GIT_CONFIG_VALUE_0" in kw["env"]) == ("fetch" in args)
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    snap = _snapshot(tmp_path, server, _forge(sq, head))
    assert load_history(snap, SCOPE, MASTER) == (
        DeliveredRequest(sq, head, product.req),
    )
    home2 = tmp_path / "home2"
    home2.mkdir()
    with pytest.raises(OSError) as failed:
        open_snapshot(
            home2,
            SLUG,
            ["refs/heads/absent"],
            TOKEN,
            git_base=server.base,
            deadline=_soon(),
        )
    monkeypatch.setattr(subprocess, "Popen", real)
    assert any("fetch" in a for a in argvs) and len(argvs) >= 8
    for secret in (TOKEN, B64):
        assert not [a for a in argvs if secret in " ".join(a)]
        assert secret not in str(failed.value)
        listed = subprocess.run(
            ["git", f"--git-dir={tmp_path / 'home' / 'repo.git'}", "config", "--list"],
            capture_output=True,
            text=True,
            check=True,
            env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
            | {"GIT_CONFIG_GLOBAL": os.devnull, "HOME": str(tmp_path)},
        ).stdout
        assert "df.fullname=" in listed and secret not in listed
        for file in (tmp_path / "home").rglob("*"):
            if file.is_file():
                assert secret.encode() not in file.read_bytes(), file
    assert not (home2 / "repo.git").exists()  # неудачный снимок убран


def test_fetch_respects_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git-base принимает соединение и молчит: снимок прерывается по сроку, группа
    процессов git убита, каталога снимка нет."""
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    pids: list[int] = []
    real = subprocess.Popen

    def spy(args: list[str], **kw: Any) -> subprocess.Popen:
        proc = real(args, **kw)
        pids.append(proc.pid)
        return proc

    monkeypatch.setattr(subprocess, "Popen", spy)
    home = tmp_path / "home"
    home.mkdir()
    started = time.monotonic()
    try:
        with pytest.raises(
            (DeadlineExceeded, OSError), match="git: deadline passed"
        ) as failed:
            open_snapshot(
                home,
                SLUG,
                [MASTER],
                TOKEN,
                git_base=f"http://127.0.0.1:{silent.getsockname()[1]}",
                deadline=_soon(1.5),
            )
    finally:
        silent.close()
    assert failed.type is DeadlineExceeded
    assert time.monotonic() - started < 10
    assert not (home / "repo.git").exists()
    assert len(pids) == 3  # init, config, fetch
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)


def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))


def test_expired_deadline_spawns_no_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spawn(monkeypatch)
    with pytest.raises(DeadlineExceeded, match="git: deadline passed"):
        open_snapshot(
            tmp_path, SLUG, [MASTER], TOKEN, git_base="http://x", deadline=_soon(-1)
        )
    assert not (tmp_path / "repo.git").exists()


def test_open_snapshot_refuses_bad_full_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spawn(monkeypatch)
    with pytest.raises(ValueError, match="full_name 'owner' is not owner/repo"):
        open_snapshot(
            tmp_path, "owner", [MASTER], TOKEN, git_base="http://x", deadline=_soon()
        )
    assert not (tmp_path / "repo.git").exists()


@pytest.mark.parametrize(
    "refs",
    [["master"], ["refs/heads/a..b"], ["refs/heads/a b"], []],
    ids=["branch", "dotdot", "space", "empty"],
)
def test_open_snapshot_refuses_bad_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refs: list[str]
) -> None:
    _no_spawn(monkeypatch)
    with pytest.raises(ValueError, match="are not full ref names"):
        open_snapshot(
            tmp_path, SLUG, refs, TOKEN, git_base="http://x", deadline=_soon()
        )
    assert not (tmp_path / "repo.git").exists()


def test_fetch_failure_is_os_error(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    product.publish({})
    with pytest.raises(OSError, match="snapshot: fetch owner/p: exit 128") as failed:
        open_snapshot(
            tmp_path,
            SLUG,
            ["refs/heads/absent"],
            TOKEN,
            git_base=server.base,
            deadline=_soon(),
        )
    assert failed.type is OSError
    assert not (tmp_path / "repo.git").exists()


# --- форджа: REST → форма `gh` -----------------------------------------------------


def _rest_snap(tmp_path: Path, forge: Forge) -> SnapshotHistory:
    transport = TokenTransport(TOKEN, api_base=API, opener=forge)
    return SnapshotHistory(
        tmp_path, transport, TOKEN, git_base="http://x", deadline=_soon()
    )


@needs_steward
def test_prs_containing_commit_normalized_from_rest(tmp_path: Path) -> None:
    sha = "a" * 40
    forge = Forge(
        {
            f"repos/{SLUG}/commits/{sha}/pulls": (
                200,
                [
                    _merged(1, sha),
                    {"number": 2, "state": "closed", "merged_at": None},
                    {"number": 3, "state": "open", "merge_commit_sha": "b" * 40},
                ],
            ),
            f"repos/{SLUG}/pulls/1": (200, {"head": {"sha": "c" * 40}}),
            f"repos/{SLUG}/pulls/2": (200, {"head": None}),
        }
    )
    snap = _rest_snap(tmp_path, forge)
    assert snap.prs_containing_commit(SLUG, sha) == [
        {"number": 1, "state": "MERGED", "mergeCommit": sha},
        {"number": 2, "state": "CLOSED", "mergeCommit": None},
        {"number": 3, "state": "OPEN", "mergeCommit": "b" * 40},
    ]
    assert snap.pr_facts(SLUG, 1) == {"headRefOid": "c" * 40}
    assert snap.pr_facts(SLUG, 2) == {"headRefOid": None}


@needs_steward
@pytest.mark.parametrize(
    "body", [{"message": "x"}, [{"number": "7"}], [{"number": True}], ["x"]]
)
def test_prs_containing_commit_shape_refused(tmp_path: Path, body: object) -> None:
    sha = "a" * 40
    snap = _rest_snap(
        tmp_path, Forge({f"repos/{SLUG}/commits/{sha}/pulls": (200, body)})
    )
    with pytest.raises(
        ValueError, match="forge: commits/aaaaaaaaaaaa/pulls: unexpected"
    ):
        snap.prs_containing_commit(SLUG, sha)


@needs_steward
def test_rest_non_200_is_os_error(tmp_path: Path) -> None:
    snap = _rest_snap(tmp_path, Forge({f"repos/{SLUG}/pulls/7": (404, {})}))
    with pytest.raises(OSError, match="forge: HTTP 404: repos/owner/p/pulls/7"):
        snap.pr_facts(SLUG, 7)


def test_rest_refuses_bad_slug(tmp_path: Path) -> None:
    forge = Forge({})
    with pytest.raises(
        (ValueError, OSError), match="forge: repo_slug '../x' is not owner/repo"
    ) as failed:
        _rest_snap(tmp_path, forge).pr_facts("../x", 7)
    assert failed.type is ValueError
    assert forge.paths == []


@needs_steward
def test_prs_containing_commit_reads_every_page(tmp_path: Path) -> None:
    """Список PR не усекается первой страницей: смёрженный PR со второй страницы
    виден, и «ровно один» `pr_for_merge` судит полный список."""
    sha = "a" * 40
    first = f"repos/{SLUG}/commits/{sha}/pulls"
    nxt = f'<{API}{first}?page=2>; rel="next"'
    forge = Forge(
        {
            first: (200, [{"number": 3, "state": "open", "merged_at": None}], nxt),
            f"{first}?page=2": (200, [_merged(7, sha)]),
        }
    )
    snap = _rest_snap(tmp_path, forge)
    assert [p["number"] for p in snap.prs_containing_commit(SLUG, sha)] == [3, 7]
    assert forge.paths == [first, f"{first}?page=2"]
    assert pr_for_merge(snap, SLUG, sha) == 7


def test_fetch_branch_refuses_bad_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spawn(monkeypatch)
    transport = TokenTransport(TOKEN, api_base=API, opener=Forge({}))
    snap = SnapshotHistory(
        tmp_path, transport, TOKEN, git_base="http://x", deadline=_soon()
    )
    assert snap.fetch_branch("", "refs/heads/a..b") is False
    assert snap.fetch_branch("", "--upload-pack=x") is False


# --- срок: DeadlineExceeded выходит из `load_history`, а не становится Unavailable ---


class SlowForge(Forge):
    """Ответ по пути `slow` «идёт» за срок: часы уходят за него, транспорт падает
    таймаутом (то, что `fetch_response` превращает в `OSError`)."""

    def __init__(self, answers: dict, clock: Clock, slow: str) -> None:
        super().__init__(answers)
        self.clock, self.slow, self.timeouts = clock, slow, []

    def __call__(self, req: Any, timeout: float = 0) -> _Answer:
        self.timeouts.append(timeout)
        if req.full_url.removeprefix(API) == self.slow:
            self.clock.now = 101.0
            raise TimeoutError("timed out")
        return super().__call__(req, timeout)


def test_deadline_propagates_from_git_path(
    tmp_path: Path, product: Product, server: GitServer
) -> None:
    product.publish({})
    clock = Clock()
    snap = _snapshot(tmp_path, server, Forge({}), clock)
    clock.now = 100.0
    with pytest.raises(DeadlineExceeded, match="git: deadline passed"):
        load_history(snap, SCOPE, MASTER)


@needs_steward
@pytest.mark.parametrize("slow", ["pulls", "head"], ids=["pr_for_merge", "pr_head"])
def test_deadline_propagates_from_forge_path(
    tmp_path: Path, product: Product, server: GitServer, slow: str
) -> None:
    """Сбой транспорта после срока — сам срок: `pr_for_merge` и `_pr_head` его не
    глотают (`DeadlineExceeded` вне `OPS_FAILURES`)."""
    head = product.branch("cand")
    m1 = product.merge("cand")
    product.publish({7: head})
    clock = Clock()
    path = (
        f"repos/{SLUG}/commits/{m1}/pulls"
        if slow == "pulls"
        else f"repos/{SLUG}/pulls/7"
    )
    forge = SlowForge(_forge(m1, head).answers, clock, path)
    snap = _snapshot(tmp_path, server, forge, clock)
    with pytest.raises(DeadlineExceeded, match="forge: deadline passed"):
        load_history(snap, SCOPE, MASTER)


@needs_steward
def test_rest_timeout_capped_by_deadline(tmp_path: Path) -> None:
    clock = Clock()
    clock.now = 95.0
    forge = SlowForge({f"repos/{SLUG}/pulls/7": (200, {"head": None})}, clock, "-")
    transport = TokenTransport(TOKEN, api_base=API, opener=forge, timeout=60.0)
    snap = SnapshotHistory(
        tmp_path, transport, TOKEN, git_base="http://x", deadline=100.0, clock=clock
    )
    assert snap.pr_facts(SLUG, 7) == {"headRefOid": None}
    assert forge.timeouts == [5.0]
    assert transport._timeout == 60.0  # восстановлен после вызова


def test_rest_past_deadline_sends_nothing(tmp_path: Path) -> None:
    clock = Clock()
    clock.now = 100.0
    forge = Forge({})
    transport = TokenTransport(TOKEN, api_base=API, opener=forge)
    snap = SnapshotHistory(
        tmp_path, transport, TOKEN, git_base="http://x", deadline=100.0, clock=clock
    )
    with pytest.raises(DeadlineExceeded, match="forge: deadline passed"):
        snap.pr_facts(SLUG, 7)
    assert forge.paths == []
