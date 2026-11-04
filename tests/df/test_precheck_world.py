"""The world of the `df-precheck` root rows, without root (2b Task 10).

The product repository, its token-gated git server and the fake GitHub's
delivery facts are the root rows' inputs; here the real `act_precheck`
judges them in process (no sudo, no role), so a broken world shows up
before CI. The observers' parsers run on captured shapes.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.df.fake_github import (
    FakeGitHub,
    Installation,
    Repo,
    Scenario,
    serve_fake_github,
)
from tests.df.precheck_world import (
    DELIVERIES,
    STOP_CURRENT,
    STOP_STALE,
    Ask,
    ProductRepo,
    TokenGitServer,
    delivery_facts,
    lldb_argv,
    lldb_bytes,
    observe,
    processes,
    silent_server,
)

FULL_NAME = "df-fixture/product"
TOKEN = "ghs_fixtureLocalTokenNotASecret"


@pytest.fixture()
def fake(tmp_path: Path) -> Iterator[FakeGitHub]:
    """The fake GitHub with one live installation token (`TOKEN`)."""
    scenario = Scenario(
        app_id=1,
        app_slug="df-fixture-app",
        installations=[Installation(11, "df-fixture", (1,), {"contents": "read"})],
        machine_token="ghp_unused",
        login="df-fixture-machine",
        repos=[Repo(1, FULL_NAME, {"pull": True})],
    )
    key = tmp_path / "unused.pub"
    with serve_fake_github(
        scenario, public_key=key, openssl=Path("/usr/bin/openssl")
    ) as f:
        f.tokens[TOKEN] = 11
        yield f


@pytest.fixture()
def product(
    tmp_path: Path, fake: FakeGitHub
) -> Iterator[tuple[ProductRepo, Ask, Ask, TokenGitServer]]:
    """The product of `precheck_world`, served to `fake`'s live tokens."""
    repo = ProductRepo.create(tmp_path / "precheck", FULL_NAME)
    for pr, request_id, generation in DELIVERIES:
        repo.deliver(pr, request_id, generation)
    current, stale = repo.stop_pr(*STOP_CURRENT), repo.stop_pr(*STOP_STALE)
    tip = repo.tip()
    repo.publish()
    commit_pulls, pulls = delivery_facts(repo)
    fake.scenario.commit_pulls.update(commit_pulls)
    fake.scenario.pulls.update(pulls)
    git = TokenGitServer(repo.served_root, lambda token: token in fake.tokens)
    try:
        yield repo, replace(current, base_tip=tip), replace(stale, base_tip=tip), git
    finally:
        git.close()


def _verdict(
    home: Path, ask: Ask, fake: FakeGitHub, git_base: str
) -> dict[str, object]:
    """The real `act_precheck.serve` on the request the client would send."""
    pytest.importorskip("steward")
    from governance import act_precheck

    request = {
        "schema": "df/precheck-request/v1",
        "nonce": "0123456789abcdef0123456789abcdef",
        "kind": "stop",
        "repository_id": 1,
        "full_name": FULL_NAME,
        "pr": ask.pr,
        "head_sha": ask.head_sha,
        "head_ref": ask.head_ref,
        "base_ref": "master",
        "base_tip": ask.base_tip,
        "changes": [list(c) for c in ask.changes],
        "token": TOKEN,
        "api_base": fake.url,
        "git_base": git_base,
    }
    home.mkdir(exist_ok=True)
    out = io.StringIO()
    raw = io.BytesIO(json.dumps(request).encode())
    code = act_precheck.serve(
        raw, out, admin_digest="sha256:" + "a" * 64, home=home, complete_v2=True
    )
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue())


def test_world_current_stop_allowed_stale_refused(
    tmp_path: Path,
    fake: FakeGitHub,
    product: tuple[ProductRepo, Ask, Ask, TokenGitServer],
) -> None:
    """The squash-delivered history: the stop of generation 2 is `allow`,
    of generation 1 `refuse` (stale); the git server saw only the token."""
    _repo, current, stale, git = product
    allow = _verdict(tmp_path / "home", current, fake, git.base)
    refuse = _verdict(tmp_path / "home", stale, fake, git.base)
    assert (allow["outcome"], allow["reason"]) == ("allow", "")
    assert (refuse["outcome"], refuse["reason"]) == (
        "refuse",
        "stop_generation_not_current: stop g1, current g2",
    )
    assert git.log and all(ok for _path, ok in git.log)
    assert any("/commits/" in s.path for s in fake.seen)


def test_world_git_refuses_a_revoked_token(
    tmp_path: Path,
    fake: FakeGitHub,
    product: tuple[ProductRepo, Ask, Ask, TokenGitServer],
) -> None:
    """After `DELETE /installation/token` the git server answers `401`:
    the verdict is `unset` (the history could not be read)."""
    _repo, current, _stale, git = product
    req = urllib.request.Request(
        f"{fake.url}/installation/token",
        method="DELETE",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=10) as answer:
        assert answer.status == 204 and answer.read() == b""
    assert TOKEN not in fake.tokens and fake.paths()[-1] == "DELETE /installation/token"
    verdict = _verdict(tmp_path / "home", current, fake, git.base)
    assert verdict["outcome"] == "unset"
    assert git.log and not any(ok for _path, ok in git.log)


def test_silent_server_accepts_and_never_answers() -> None:
    """The git-hang row's port: the connection opens, no byte comes."""
    with silent_server() as url:
        port = int(url.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(b"GET /info/refs HTTP/1.1\r\nHost: x\r\n\r\n")
            conn.settimeout(1.0)
            with pytest.raises(TimeoutError):
                conn.recv(1)


def test_git_server_without_token_is_401(
    product: tuple[ProductRepo, Ask, Ask, TokenGitServer],
) -> None:
    """No `Basic x-access-token` (or another user): `401`."""
    _repo, _current, _stale, git = product
    url = f"{git.base}/{FULL_NAME}.git/info/refs"
    for header in ({}, {"Authorization": "Basic " + "eDp5"}):
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                urllib.request.Request(url, headers=header), timeout=10
            )
        assert caught.value.code == 401


#: lldb 17's `memory read --format x --size 1` rows (macOS 15 CLT).
LLDB_OUT = """(lldb) process attach --pid 4242
Process 4242 stopped
(lldb) memory read --format x --size 1 --count 20 4365418512
0x1043276d0: 0x64 0x66 0x2d 0x61 0x63 0x63 0x2d 0x6d
0x1043276d8: 0x65 0x6d 0x2d 0x30 0x31 0x32 0x33 0x34
0x1043276e0: 0x35 0x36 0x37 0x38
(lldb) process detach
"""


def test_lldb_bytes_reads_memory_rows() -> None:
    """The bytes of every row, in order; nothing from other lines."""
    assert lldb_bytes(LLDB_OUT) == b"df-acc-mem-012345678"
    assert lldb_bytes("error: attach failed: Not allowed to attach\n") == b""


def test_processes_sees_this_process() -> None:
    """`ps` parsing: this process with its UID and parent."""
    mine = [p for p in processes() if p.pid == os.getpid()]
    assert len(mine) == 1
    assert (mine[0].uid, mine[0].ppid) == (os.getuid(), os.getppid())


def test_driver_compiles() -> None:
    """The publisher's driver and the holders are valid Python."""
    from tests.df import precheck_world as pwm

    for text in (
        pwm.DRIVER,
        pwm.MEMORY_HOLDER,
        pwm.PROC_MEM_READER,
        pwm.INHERIT_HOLDER,
    ):
        compile(text, "<script>", "exec")
    done = subprocess.run(
        [sys.executable, "-I", "-c", pwm.PROC_MEM_READER, str(os.getpid()), "0", "1"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0 and "errno" in json.loads(done.stdout)


def test_observer_refuses_off_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """No debugger attach off root: `observe` refuses before any process
    starts (a local `lldb -p` asks the human for Developer Tools access)."""
    if os.geteuid() == 0:
        pytest.skip("as root the observer runs (CI)")

    def no_process(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"started a process: {args}")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    for user in (None, "nobody"):
        with pytest.raises(PermissionError):
            observe(lldb_argv(os.getpid(), 0, 1), user=user, timeout=1)


def test_memory_row_skips_before_the_observer() -> None:
    """Off root the memory row (and every precheck root row) is skipped by
    the root gate at collection, even with `DF_ROOT_ACCEPTANCE=1`: no
    fixture and no observer runs."""
    if os.geteuid() == 0:
        pytest.skip("as root the gate opens (CI)")
    env = {**os.environ, "DF_ROOT_ACCEPTANCE": "1"}
    env.pop("DF_STAGE1_OUT", None)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            "-m",
            "root",
            "-k",
            "precheck",
            "tests/df/test_acceptance_root_roles.py",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-2000:]
    assert "passed" not in done.stdout and "failed" not in done.stdout
    assert "root-приёмка: нужны euid 0" in done.stdout
    assert " skipped" in done.stdout
