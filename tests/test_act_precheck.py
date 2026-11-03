"""`act_precheck`: программа проверяющего издателя df (вид stop, этап 2b).

Стенд — настоящий `git`, dumb-HTTP git-сервер на 127.0.0.1 и replay REST форджи
(`tests/test_act_history_snapshot.py`); запрос — байты в stdin `serve`, вердикт —
одна строка JSON в stdout."""

from __future__ import annotations

import ast
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from governance import act_precheck
from governance.act_envelope import StopRecord, render_stop, stop_path
from governance.act_forge_http import TokenTransport
from governance.act_history_snapshot import open_snapshot
from governance.act_precheck import (
    PRECHECK_VERSION,
    REQUEST_KEYS,
    PrecheckDeps,
    request_digest,
    serve,
)
from governance.act_request import (
    NodeEntry,
    NodeRequest,
    manifest_path,
    render_manifest,
)
from tests.test_act_history_snapshot import (
    API,
    H_REQ,
    PIN,
    SLUG,
    TOKEN,
    WS,
    Forge,
    GitServer,
    _git,
    _merged,
)

#: История читает REST (`TokenTransport.get_json`) — это тянет steward; разбор
#: запроса, уборка снимков, срок и замыкание импортов — нет.
needs_steward = pytest.mark.skipif(
    importlib.util.find_spec("steward") is None,
    reason="steward — без группы governance",
)
ROOT = Path(__file__).resolve().parent.parent
NODE = "requirements"
NONCE = "0123456789abcdef0123456789abcdef"
ADMIN = "a" * 64
STOP_PR = 8
STOP1 = stop_path(WS, NODE, 1)


# --- стенд: репозиторий продукта с доставками ------------------------------------


def _request(request_id: str, generation: int) -> NodeRequest:
    entry = NodeEntry("spec/10-requirements.md", H_REQ, generation)
    return NodeRequest(request_id, WS, SLUG, {NODE: entry}, {NODE: {}}, PIN)


class Repo:
    """Рабочий репозиторий продукта: ветки с файлами, доставки merge-коммитом."""

    def __init__(self, root: Path) -> None:
        self.work, self.served_root = root / "work", root / "served"
        self.served = self.served_root / f"{SLUG}.git"
        self.pulls: dict[int, str] = {}
        self.answers: dict[str, tuple] = {}
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "master")
        (self.work / "README.md").write_text("x\n")
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", "init")

    def branch(self, name: str, files: dict[str, str]) -> str:
        _git(self.work, "switch", "-qc", name, "master")
        for rel, text in files.items():
            file = self.work / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", name)
        head = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "switch", "-q", "master")
        return head

    def deliver(self, name: str, files: dict[str, str], pr: int) -> str:
        """Ветка `name` смёржена PR `pr` (merge-коммит); REST знает этот PR."""
        head = self.branch(name, files)
        _git(self.work, "merge", "-q", "--no-ff", name, "-m", f"Merge {name}")
        merge = _git(self.work, "rev-parse", "HEAD")
        self.pulls[pr] = head
        self.answers[f"repos/{SLUG}/commits/{merge}/pulls"] = (
            200,
            [_merged(pr, merge)],
        )
        self.answers[f"repos/{SLUG}/pulls/{pr}"] = (
            200,
            {"number": pr, "head": {"sha": head}},
        )
        return merge

    def deliver_request(self, request_id: str, generation: int, pr: int) -> str:
        req = _request(request_id, generation)
        path = manifest_path(WS, req.request_id)
        return self.deliver(f"cand-{pr}", {path: render_manifest(req)}, pr)

    def stop_pr(
        self,
        generation: int = 1,
        *,
        record: StopRecord | None = None,
        path: str | None = None,
    ) -> str:
        """Открытый stop-PR `STOP_PR`: голова с записью остановки."""
        rel = path or stop_path(WS, NODE, generation)
        text = render_stop(record or StopRecord(NODE, generation, "отозвано"))
        head = self.branch(f"stop-{generation}-{len(self.pulls)}", {rel: text})
        self.pulls[STOP_PR] = head
        return head

    def publish(self) -> None:
        self.served.parent.mkdir(parents=True, exist_ok=True)
        _git(self.work, "clone", "-q", "--bare", str(self.work), str(self.served))
        for n, sha in self.pulls.items():
            _git(self.served, "update-ref", f"refs/pull/{n}/head", sha)
        _git(self.served, "update-server-info")

    def tip(self) -> str:
        return _git(self.work, "rev-parse", "master")


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


@pytest.fixture()
def server(repo: Repo) -> Iterator[GitServer]:
    srv = GitServer(repo.served_root)
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _req(**over: object) -> dict[str, object]:
    """Корректный запрос stop поколения 1 (поля — поверх)."""
    base: dict[str, object] = {
        "schema": "df/precheck-request/v1",
        "nonce": NONCE,
        "kind": "stop",
        "repository_id": 42,
        "full_name": SLUG,
        "pr": STOP_PR,
        "head_sha": "b" * 40,
        "head_ref": f"stop/{WS}-{NODE}-g1",
        "base_ref": "master",
        "base_tip": "c" * 40,
        "changes": [["added", stop_path(WS, NODE, 1), None]],
        "token": TOKEN,
        "api_base": API,
        "git_base": "http://127.0.0.1:9",
    }
    return base | over


def _deps(forge: Any = None) -> PrecheckDeps:
    def transport_for(token: str, api_base: str) -> TokenTransport:
        return TokenTransport(token, api_base=api_base, opener=forge)

    return PrecheckDeps(transport_for, open_snapshot, time.monotonic)


def _serve(
    home: Path,
    payload: bytes | dict[str, object],
    deps: PrecheckDeps | None = None,
) -> tuple[int, dict[str, object] | None, str]:
    """`serve` с байтами запроса: (код, вердикт или None, сырой stdout)."""
    home.mkdir(exist_ok=True)
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    out = io.StringIO()
    code = serve(io.BytesIO(raw), out, admin_digest=ADMIN, home=home, deps=deps)
    text = out.getvalue()
    return code, (json.loads(text) if text else None), text


def _stop_request(repo: Repo, server: GitServer, head: str, **over: object) -> dict:
    return _req(head_sha=head, base_tip=repo.tip(), git_base=server.base) | over


def _run_stop(
    tmp_path: Path, repo: Repo, server: GitServer, head: str, **over: object
) -> dict[str, object]:
    code, verdict, _ = _serve(
        tmp_path / "home",
        _stop_request(repo, server, head, **over),
        _deps(Forge(repo.answers)),
    )
    assert code == 0 and verdict is not None
    return verdict


# --- вид stop: решения ------------------------------------------------------------


@needs_steward
def test_stop_current_generation_allowed(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert (verdict["outcome"], verdict["reason"]) == ("allow", "")


@needs_steward
def test_stop_for_superseded_generation_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    repo.deliver_request("approve-1-0-2", 2, 9)
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "stop_generation_not_current: stop g1, current g2"


@needs_steward
def test_stop_already_stopped_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    path = stop_path(WS, NODE, 1)
    repo.deliver("stop-delivered", {path: render_stop(StopRecord(NODE, 1, "r"))}, 10)
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "already_stopped: node requirements g1"


@needs_steward
def test_stop_without_delivered_request_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "no_delivered_request: node requirements"


@needs_steward
@pytest.mark.parametrize(
    "record",
    [StopRecord(NODE, 2, "r"), StopRecord("design", 1, "r")],
    ids=["generation", "node"],
)
def test_stop_path_content_mismatch_refused(
    tmp_path: Path, repo: Repo, server: GitServer, record: StopRecord
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1, record=record)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "refuse"
    assert str(verdict["reason"]).startswith("stop_content_mismatch: ")


@needs_steward
def test_stop_record_invalid_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    path = stop_path(WS, NODE, 1)
    head = repo.branch("stop-bad", {path: "schema: other/v1\n"})
    repo.pulls[STOP_PR] = head
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "refuse"
    assert str(verdict["reason"]).startswith("stop_record_invalid: ")


@needs_steward
def test_stop_record_absent_at_head_is_unset(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    """Голова PR без файла по пути из `changes` — ошибка чтения, не решение."""
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.branch("stop-empty", {"other.txt": "x\n"})
    repo.pulls[STOP_PR] = head
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "unset"
    assert str(verdict["reason"]).startswith("stop_unreadable: ")


@needs_steward
def test_stop_noncanonical_path_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    """`-g01` — не путь, который построил бы `stop_path`; содержимое при этом
    корректно (поколение 1), так что обход проверки дал бы `allow`."""
    repo.deliver_request("approve-1-0-1", 1, 7)
    path = f"workstreams/{WS}/approvals/stops/{NODE}-g01.yaml"
    head = repo.stop_pr(1, path=path)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head, changes=[["added", path, None]])
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == f"stop_changes: {path!r} is not canonical"


@needs_steward
@pytest.mark.parametrize(
    "head_ref",
    [f"stop/{WS}-{NODE}-g2", f"stop/{WS}-design-g1", "feature/x"],
    ids=["generation", "node", "foreign"],
)
def test_stop_branch_mismatch_refused(
    tmp_path: Path, repo: Repo, server: GitServer, head_ref: str
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head, head_ref=head_ref)
    assert verdict["outcome"] == "refuse"
    assert str(verdict["reason"]).startswith("stop_branch_mismatch: ")


@needs_steward
@pytest.mark.parametrize(
    "change",
    [
        ["modified", stop_path(WS, NODE, 1), None],
        ["renamed", stop_path(WS, NODE, 1), "old.yaml"],
        ["added", stop_path(WS, NODE, 1), "old.yaml"],
    ],
    ids=["modified", "renamed", "previous"],
)
def test_stop_modifying_delivered_stop_refused(
    tmp_path: Path, repo: Repo, server: GitServer, change: list
) -> None:
    """Изменение файла остановки (в т.ч. уже доставленного) и переименование —
    не stop: ровно одна запись `added` без прежнего имени."""
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1)
    repo.publish()
    verdict = _run_stop(tmp_path, repo, server, head, changes=[change])
    assert verdict["outcome"] == "refuse"
    assert str(verdict["reason"]).endswith(" is not a plain addition")


@needs_steward
def test_stop_with_extra_change_refused(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1)
    repo.publish()
    changes = [["added", stop_path(WS, NODE, 1), None], ["added", "x.txt", None]]
    verdict = _run_stop(tmp_path, repo, server, head, changes=changes)
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == "stop_changes: expected one change, got 2"


@pytest.mark.parametrize(
    "path",
    [
        f"workstreams/{WS}/approvals/{NODE}.yaml",
        f"workstreams/{WS}/approvals/stops/{NODE}-g{'1' * 10}.yaml",
    ],
    ids=["envelope", "huge_generation"],
)
def test_stop_foreign_path_refused_before_io(tmp_path: Path, path: str) -> None:
    code, verdict, _ = _serve(
        tmp_path / "home", _req(changes=[["added", path, None]]), _deps()
    )
    assert code == 0 and verdict is not None
    assert verdict["outcome"] == "refuse"
    assert verdict["reason"] == f"stop_changes: {path!r} is not a stop record path"
    assert not (tmp_path / "home" / "precheck-snapshots" / NONCE).exists()


@needs_steward
def test_history_unavailable_is_unset(
    tmp_path: Path, repo: Repo, server: GitServer
) -> None:
    merge = repo.deliver_request("approve-1-0-1", 1, 7)
    head = repo.stop_pr(1)
    repo.publish()
    repo.answers[f"repos/{SLUG}/commits/{merge}/pulls"] = (500, {"message": "x"})
    verdict = _run_stop(tmp_path, repo, server, head)
    assert verdict["outcome"] == "unset"
    assert str(verdict["reason"]).startswith("history_unavailable: ")


def test_snapshot_failure_is_unset(tmp_path: Path) -> None:
    """git-base не отвечает (порт закрыт) — ошибка чтения: `unset`, не отказ."""
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    code, verdict, _ = _serve(
        tmp_path / "home", _req(git_base=f"http://127.0.0.1:{port}"), _deps()
    )
    assert code == 0 and verdict is not None
    assert verdict["outcome"] == "unset"
    assert str(verdict["reason"]).startswith("read_failed: snapshot: fetch owner/p")


# --- запрос: строгая схема ---------------------------------------------------------


def _invalid(tmp_path: Path, payload: bytes | dict, capsys: Any) -> str:
    code, verdict, text = _serve(tmp_path / "home", payload, _deps())
    err = capsys.readouterr().err
    assert (code, verdict, text) == (2, None, "")
    assert TOKEN not in err
    return err


def test_unknown_request_key_exit_2(tmp_path: Path, capsys: Any) -> None:
    err = _invalid(tmp_path, _req(changes=[], extra=1), capsys)
    assert "keys: unexpected ['extra'], missing []" in err


def test_missing_request_key_exit_2(tmp_path: Path, capsys: Any) -> None:
    payload = _req()
    del payload["git_base"]
    err = _invalid(tmp_path, payload, capsys)
    assert "keys: unexpected [], missing ['git_base']" in err


def test_kind_candidate_rejected_by_schema(tmp_path: Path, capsys: Any) -> None:
    err = _invalid(tmp_path, _req(kind="candidate", changes=[]), capsys)
    assert "invalid values: ['kind']" in err


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "df/precheck-request/v2"),
        ("nonce", "../x"),
        ("nonce", NONCE.upper()),
        ("repository_id", True),
        ("repository_id", 0),
        ("full_name", "owner"),
        ("pr", "8"),
        ("head_sha", "B" * 40),
        ("head_ref", ""),
        ("base_ref", 1),
        ("base_tip", "c" * 39),
        ("changes", [["added", "x"]]),
        ("changes", [["added", "x", 1]]),
        ("changes", "x"),
        ("token", ""),
        ("api_base", "file:///etc"),
        ("git_base", "ftp://x"),
    ],
)
def test_invalid_value_exit_2(
    tmp_path: Path, capsys: Any, key: str, value: object
) -> None:
    err = _invalid(tmp_path, _req(**{key: value}), capsys)
    assert f"invalid values: [{key!r}]" in err


def test_request_not_object_exit_2(tmp_path: Path, capsys: Any) -> None:
    err = _invalid(tmp_path, b"[1]", capsys)
    assert "request is not a JSON object" in err


@pytest.mark.parametrize("raw", [b"{", b"\xff\xfe", b'{"a": NaN}'])
def test_request_not_json_exit_2(tmp_path: Path, capsys: Any, raw: bytes) -> None:
    err = _invalid(tmp_path, raw, capsys)
    assert "request is not JSON" in err


def test_duplicate_request_key_exit_2(tmp_path: Path, capsys: Any) -> None:
    raw = json.dumps(_req(changes=[]))[:-1] + f', "nonce": "{NONCE}"}}'
    err = _invalid(tmp_path, raw.encode(), capsys)
    assert "duplicate keys: ['nonce']" in err


def test_oversize_request_exit_2(tmp_path: Path, capsys: Any) -> None:
    raw = json.dumps(_req(changes=[])) + " " * (64 << 10)
    err = _invalid(tmp_path, raw.encode(), capsys)
    assert "request larger than 65536 bytes" in err


# --- вердикт: связь с запросом, токен ---------------------------------------------


def test_verdict_binds_request(tmp_path: Path) -> None:
    payload = _req(changes=[])
    code, verdict, text = _serve(tmp_path / "home", payload, _deps())
    without = {k: v for k, v in payload.items() if k != "token"}
    canonical = json.dumps(
        without, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert code == 0
    assert text.endswith("\n") and text.count("\n") == 1
    assert verdict == {
        "schema": "df/precheck-verdict/v1",
        "nonce": NONCE,
        "request_digest": digest,
        "repository_id": 42,
        "pr": STOP_PR,
        "head_sha": "b" * 40,
        "kind": "stop",
        "outcome": "refuse",
        "reason": "stop_changes: expected one change, got 0",
        "version": PRECHECK_VERSION,
        "admin_digest": ADMIN,
    }
    assert PRECHECK_VERSION == "df-precheck/1"


def test_request_digest_ignores_token_only() -> None:
    a, b = _req(), _req(token="ghs_other")
    assert request_digest(a) == request_digest(b)
    assert request_digest(a) != request_digest(_req(pr=9))
    assert set(REQUEST_KEYS) == set(_req())


def test_token_not_in_verdict_or_stderr(tmp_path: Path, capsys: Any) -> None:
    """Токен, попавший в поле запроса, которое цитирует причина (`head_ref`), в
    вердикт не выходит; в stderr его нет и при отказе схемы."""
    code, verdict, text = _serve(
        tmp_path / "home", _req(head_ref=f"stop/{TOKEN}"), _deps()
    )
    assert code == 0 and verdict is not None
    assert TOKEN not in text
    assert verdict["reason"] == (
        f"stop_branch_mismatch: 'stop/[token]' is not the branch of {STOP1!r}"
    )
    _invalid(tmp_path, _req(kind=TOKEN), capsys)


def test_reason_truncated(tmp_path: Path) -> None:
    """Причина цитирует поле, которое задаёт автор PR (здесь — длинная ветка):
    в вердикте не больше `MAX_REASON` символов и метка усечения."""
    code, verdict, text = _serve(
        tmp_path / "home", _req(head_ref="x" * 50_000), _deps()
    )
    assert code == 0 and verdict is not None
    reason = str(verdict["reason"])
    assert reason.endswith("...[truncated]")
    assert len(reason) == 2048 + len("...[truncated]")
    assert len(text) < 8 << 10


# --- снимок: каталог, уборка, срок -------------------------------------------------


def _spy_snapshot(seen: list[tuple[Path, list[str]]]) -> PrecheckDeps:
    def snapshot_for(home: Path, *a: Any, **kw: Any) -> Path:
        seen.append((home, sorted(p.name for p in home.parent.iterdir())))
        return open_snapshot(home, *a, **kw)

    return PrecheckDeps(_deps().transport_for, snapshot_for, time.monotonic)


def test_snapshot_removed_after_run_and_stale_cleared(tmp_path: Path) -> None:
    home = tmp_path / "home"
    stale = home / "precheck-snapshots" / ("f" * 32)
    (stale / "repo.git").mkdir(parents=True)
    seen: list[tuple[Path, list[str]]] = []
    code, verdict, _ = _serve(home, _req(), _spy_snapshot(seen))
    assert code == 0 and verdict is not None and verdict["outcome"] == "unset"
    assert seen == [(home / "precheck-snapshots" / NONCE, [NONCE])]
    assert list((home / "precheck-snapshots").iterdir()) == []


def test_stale_kept_while_another_run_holds_lock(tmp_path: Path) -> None:
    """Живой вызов держит `precheck.lock` (разделяемо): чужой каталог снимков
    не убирается — он может быть живым."""
    home = tmp_path / "home"
    live = home / "precheck-snapshots" / ("e" * 32)
    live.mkdir(parents=True)
    fd = os.open(home / "precheck.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        code, _verdict, _ = _serve(home, _req(), _deps())
    finally:
        os.close(fd)
    assert code == 0
    assert live.is_dir()
    assert not (home / "precheck-snapshots" / NONCE).exists()


def test_lock_wait_bounded_by_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чужая исключительная блокировка не держит вызов дольше `DEADLINE`: ответ
    `unset: deadline`, до снимка дело не дошло."""
    monkeypatch.setattr(act_precheck, "DEADLINE", 0.3)
    seen: list[tuple[Path, list[str]]] = []
    home = tmp_path / "home"
    home.mkdir()
    fd = os.open(home / "precheck.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        started = time.monotonic()
        code, verdict, _ = _serve(home, _req(), _spy_snapshot(seen))
    finally:
        os.close(fd)
    assert code == 0 and verdict is not None
    assert (verdict["outcome"], verdict["reason"]) == ("unset", "deadline")
    assert seen == []
    assert time.monotonic() - started < 5
    assert not (home / "precheck-snapshots" / NONCE).exists()


def test_unremovable_snapshot_named_on_stderr(tmp_path: Path, capsys: Any) -> None:
    home = tmp_path / "home"
    locked = home / "precheck-snapshots" / ("d" * 32) / "sub"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    locked.chmod(0o500)
    try:
        code, _verdict, _ = _serve(home, _req(), _deps())
    finally:
        locked.chmod(0o700)
    err = capsys.readouterr().err
    assert code == 0
    assert f"df-precheck: snapshot not removed: {locked / 'f'}" in err
    assert TOKEN not in err


def test_deadline_kills_git_and_answers_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git-base принимает соединение и молчит: по `DEADLINE` git-потомки убиты,
    снимок убран, вердикт `unset: deadline`."""
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
    monkeypatch.setattr(act_precheck, "DEADLINE", 1.5)
    home = tmp_path / "home"
    started = time.monotonic()
    try:
        code, verdict, _ = _serve(
            home,
            _req(git_base=f"http://127.0.0.1:{silent.getsockname()[1]}"),
            _deps(),
        )
    finally:
        silent.close()
    assert code == 0 and verdict is not None
    assert (verdict["outcome"], verdict["reason"]) == ("unset", "deadline")
    assert time.monotonic() - started < 10
    assert pids  # git ran; every process group it started is gone
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)
    assert list((home / "precheck-snapshots").iterdir()) == []


# --- замыкание импортов ------------------------------------------------------------

#: Замыкание `act_precheck` по AST (включая отложенные импорты); Task 5 замораживает
#: его как `GOVERNANCE_MODULES`.
CLOSURE = (
    "governance",
    "governance.act_anon",
    "governance.act_envelope",
    "governance.act_forge_http",
    "governance.act_git_rows",
    "governance.act_history",
    "governance.act_history_snapshot",
    "governance.act_precheck",
    "governance.act_request",
    "governance.act_time",
    "governance.approval_branches",
    "governance.ssot_env",
)
FORBIDDEN = (
    "governance.ops",
    "governance.runner",
    "governance.act_status",
)


def _module_file(name: str) -> Path | None:
    base = ROOT.joinpath(*name.split("."))
    for file in (base.with_suffix(".py"), base / "__init__.py"):
        if file.is_file():
            return file
    return None


def _closure(start: str) -> tuple[set[str], set[str]]:
    """(модули репо, внешние верхние имена) по всем `import` модуля и его
    зависимостей — в любом месте тела, не только в шапке."""
    local, external, todo = set(), set(), [start]
    while todo:
        name = todo.pop()
        if name in local:
            continue
        local.add(name)
        if "." in name:
            todo.append(name.rsplit(".", 1)[0])  # пакет исполняется первым
        file = _module_file(name)
        assert file is not None, name
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0 and node.module, f"{name}: relative import"
                names = [node.module]
                names += [f"{node.module}.{a.name}" for a in node.names]
            for got in names:
                if _module_file(got) is not None:
                    todo.append(got)
                elif _module_file(got.rsplit(".", 1)[0]) is None:
                    external.add(got.split(".")[0])
    return local, external


def test_closure_excludes_act_status_ops_df() -> None:
    local, external = _closure("governance.act_precheck")
    assert sorted(local) == sorted(CLOSURE)
    assert [m for m in FORBIDDEN if m in local] == []
    assert not any(m == "df" or m.startswith("df.") for m in local)
    allowed = set(sys.stdlib_module_names) | {"steward", "yaml"}
    assert sorted(external - allowed) == []
