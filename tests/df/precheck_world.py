"""The world of the `df-precheck` root rows (stage 2b Task 10).

On top of `tests/df/roles_world.py`'s world (the roles, the real `sudo`
rules and wrappers, the fake GitHub): the product repository the precheck
reads, served over dumb HTTP on `127.0.0.1` (`TokenGitServer`: only an
installation token the fake GitHub minted and has not revoked opens it),
with two squash deliveries of node `NODE` (generations 1 and 2) and two open
stop PRs (the current generation and the stale one); the fake GitHub learns
the delivery facts (`commit_pulls`, `pulls`).

The publisher's side is `DRIVER`: a process of `df-publisher` (direct drop,
the admin Python of `current`, `-I -B -S`, the admin tree first on
`sys.path`, as `publisher serve` runs) that calls the real client
`df.publisher.precheck.admin_precheck` -- the read token from the fake, the
fixed command `sudo -n -u df-precheck <libexec>/df-precheck`, the verdict
bound to the request, the revoke. Stage 2b's `merge` handler (Task 8) is the
only other caller of it and is not part of this checkout.

The observers read processes as root: `ps` for UIDs and states; `/proc`
(Linux) or `lsof` / `ps -E` (macOS) for descriptors and environment;
`/proc/<pid>/mem` (Linux) or `lldb` (macOS) for memory.
"""

from __future__ import annotations

import base64
import json
import os
import pwd
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from df import platform as plat
from governance.act_envelope import StopRecord, render_stop, stop_path
from governance.act_projection import self_hash_v
from governance.act_request import (
    NodeEntry,
    NodeRequest,
    PolicyPin,
    manifest_path,
    render_manifest,
)
from governance.approval_branches import stop_branch
from tests.df.roles_world import (
    BASE_ENV,
    FULL_NAME,
    OPENSSL,
    PRODUCT_ID,
    PUBLISHER,
    RolesWorld,
    admin_python,
)

WS = "ws-acc"
NODE = "requirements"
H_REQ = self_hash_v("---\nspec_stage: requirements\n---\nтело\n", "review/v1")
PIN = PolicyPin(
    "df-fixture/approval-policy", "main", "p" * 40, {"policy/modes.yaml": "0" * 64}
)
#: `(pr, request_id, generation)` of the two squash deliveries.
DELIVERIES = ((7, "approve-1-0-1", 1), (9, "approve-1-0-2", 2))
#: Open stop PRs: of the current generation (2) and of the stale one (1).
STOP_CURRENT = (8, 2)
STOP_STALE = (10, 1)
SYSTEM_GIT = "/usr/bin/git"
GIT_ENV: Mapping[str, str] = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
}
PS = "/bin/ps"
LSOF = "/usr/sbin/lsof"
LLDB = "/usr/bin/lldb"

Change = tuple[str, str, str | None]


# ---------- the product repository ----------


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [SYSTEM_GIT, "-C", str(cwd), *args],
        env=dict(GIT_ENV),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise OSError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


@dataclass
class ProductRepo:
    """The product's work tree and the bare copy `TokenGitServer` serves at
    `<served_root>/<full_name>.git`; `pulls` are the `refs/pull/<n>/head`."""

    work: Path
    served_root: Path
    full_name: str
    pulls: dict[int, str] = field(default_factory=dict)
    #: `(squash commit, pr, head)` of each delivery.
    deliveries: list[tuple[str, int, str]] = field(default_factory=list)

    @classmethod
    def create(cls, root: Path, full_name: str) -> ProductRepo:
        """An empty product with one commit on `master`."""
        repo = cls(root / "work", root / "served", full_name)
        repo.work.mkdir(parents=True)
        _git(repo.work, "init", "-q", "--template=", "-b", "master")
        (repo.work / "README.md").write_text("product\n")
        _git(repo.work, "add", "-A")
        _git(repo.work, "commit", "-qm", "init")
        return repo

    def branch(self, name: str, files: Mapping[str, str]) -> str:
        """Branch `name` off `master` with `files` added; its head."""
        _git(self.work, "switch", "-qc", name, "master")
        for rel, text in files.items():
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-qm", name)
        head = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "switch", "-q", "master")
        return head

    def deliver(self, pr: int, request_id: str, generation: int) -> str:
        """The request manifest of `NODE` at `generation`, delivered by PR
        `pr` with a squash merge (spec 4.4 rev 10); the squash commit."""
        entry = NodeEntry("spec/10-requirements.md", H_REQ, generation)
        req = NodeRequest(
            request_id, WS, self.full_name, {NODE: entry}, {NODE: {}}, PIN
        )
        name = f"cand-{pr}"
        head = self.branch(name, {manifest_path(WS, request_id): render_manifest(req)})
        _git(self.work, "merge", "-q", "--squash", name)
        _git(self.work, "commit", "-qm", f"{name} (squash)")
        _git(self.work, "branch", "-q", "-D", name)
        squash = _git(self.work, "rev-parse", "HEAD")
        self.pulls[pr] = head
        self.deliveries.append((squash, pr, head))
        return squash

    def stop_pr(self, pr: int, generation: int) -> Ask:
        """An open stop PR `pr` adding the stop record of `generation`."""
        path = stop_path(WS, NODE, generation)
        text = render_stop(StopRecord(NODE, generation, "fixture: Task 10"))
        head_ref = stop_branch(WS, NODE, generation)
        head = self.branch(head_ref, {path: text})
        self.pulls[pr] = head
        return Ask(pr, head, head_ref, "", (("added", path, None),))

    def publish(self) -> None:
        """The bare copy with `master` and the PR refs (pushed by sha: the
        delivered branches are gone), packed, for dumb HTTP."""
        served = self.served_root / f"{self.full_name}.git"
        served.mkdir(parents=True)
        _git(served, "init", "-q", "--bare", "--template=")
        refs = [f"{sha}:refs/pull/{n}/head" for n, sha in self.pulls.items()]
        _git(self.work, "push", "-q", str(served), "master:refs/heads/master", *refs)
        _git(served, "symbolic-ref", "HEAD", "refs/heads/master")
        _git(served, "repack", "-a", "-d", "-q")
        _git(served, "update-server-info")

    def tip(self) -> str:
        """The head of `master`."""
        return _git(self.work, "rev-parse", "master")


@dataclass(frozen=True)
class Ask:
    """What the publisher has established about a stop PR (`PrFacts`)."""

    pr: int
    head_sha: str
    head_ref: str
    base_tip: str
    changes: tuple[Change, ...]


def delivery_facts(repo: ProductRepo) -> tuple[dict, dict]:
    """`(commit_pulls, pulls)` of the fake GitHub for `repo`'s deliveries."""
    commit_pulls: dict[tuple[str, str], list[dict[str, object]]] = {}
    pulls: dict[tuple[str, int], dict[str, object]] = {}
    for squash, pr, head in repo.deliveries:
        commit_pulls[(repo.full_name, squash)] = [
            {
                "number": pr,
                "state": "closed",
                "merged_at": "2026-11-03T10:00:00Z",
                "merge_commit_sha": squash,
            }
        ]
        pulls[(repo.full_name, pr)] = {"number": pr, "head": {"sha": head}}
    return commit_pulls, pulls


# ---------- git over dumb HTTP ----------


class TokenGitServer:
    """Dumb HTTP over the files under `root`: only `Basic x-access-token:<t>`
    with `accept(t)` (else `401`); each answer waits `delay` seconds first
    (the window of the `command_timeout` row)."""

    def __init__(self, root: Path, accept: Callable[[str], bool]) -> None:
        self.delay = 0.0
        self.log: list[tuple[str, bool]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                ok = accept(_basic_token(self.headers.get("Authorization", "")))
                server.log.append((self.path, ok))
                time.sleep(server.delay)
                rel = urllib.parse.urlsplit(self.path).path.lstrip("/")
                file = (root / rel).resolve()
                if not ok:
                    self._send(401, b"", {"WWW-Authenticate": 'Basic realm="df"'})
                elif not file.is_relative_to(root.resolve()) or not file.is_file():
                    self._send(404, b"")
                else:
                    self._send(200, file.read_bytes())

            def _send(
                self, code: int, data: bytes, extra: Mapping[str, str] | None = None
            ) -> None:
                self.send_response(code)
                for key, value in {
                    **(extra or {}),
                    "Content-Type": "text/plain",
                }.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                """Quiet."""

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        """Stop serving."""
        self.httpd.shutdown()
        self.httpd.server_close()


def _basic_token(header: str) -> str:
    """The password of `Basic x-access-token:<password>`, else `""`."""
    if not header.startswith("Basic "):
        return ""
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return ""
    return password if user == "x-access-token" else ""


@contextmanager
def silent_server() -> Iterator[str]:
    """`http://127.0.0.1:<port>` that accepts every connection and never
    answers (the git-hang row); connections close at the end."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    held: list[socket.socket] = []
    stop = threading.Event()

    def hold() -> None:
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except OSError:
                continue
            held.append(conn)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        stop.set()
        thread.join(timeout=5)
        for conn in held:
            conn.close()
        sock.close()


# ---------- the publisher's side ----------

#: The publisher's call of the precheck: `admin_precheck` of the admin tree
#: (`argv[1]`: the tree, the product and the PR's facts), its result and
#: the monotonic instants around the call (`start`, `end`) and around its
#: supervised sudo command (`sudo_start`, `sudo_end`) as one JSON line.
DRIVER = """
import json, sys, time
from pathlib import Path
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["tree"])
from df.paths import Layout
from df.publisher import github, precheck
from df.publisher.ops import OpContext
from df.stage2_config import load_products
lay = Layout(Path("/"))
products = load_products(lay.etc / "products.toml")
product = products[spec["product"]]
oc = OpContext(lay, product, lay.app_key(product.app_id), Path(spec["openssl"]),
               github.default_transport, time.time(), spec["api_base"], products,
               spec["git_base"])
facts = precheck.PrFacts(spec["product"], spec["head_ref"], spec["base_ref"],
                         spec["base_tip"], tuple(tuple(c) for c in spec["changes"]))
pr = {"number": spec["pr"], "head": {"sha": spec["head_sha"]}}
out = {"start": time.monotonic()}
from df import platform as _plat
_supervised = _plat.run_supervised
def _timed(*args, **kwargs):
    out["sudo_start"] = time.monotonic()
    try:
        return _supervised(*args, **kwargs)
    finally:
        out["sudo_end"] = time.monotonic()
_plat.run_supervised = _timed
try:
    res = precheck.admin_precheck("stop", oc, pr, facts)
except Exception as exc:
    out["raised"] = f"{type(exc).__name__}: {exc}"
else:
    out["refused"] = None if res.refused is None else res.refused.reason
    out.update(res.evidence())
out["end"] = time.monotonic()
print(json.dumps(out), flush=True)
"""


def driver_spec(
    ask: Ask,
    *,
    tree: Path,
    product: int,
    api_base: str,
    git_base: str,
    openssl: Path,
) -> dict[str, object]:
    """The `argv[1]` of `DRIVER` for `ask`."""
    return {
        "tree": str(tree),
        "product": product,
        "openssl": str(openssl),
        "api_base": api_base,
        "git_base": git_base,
        "pr": ask.pr,
        "head_sha": ask.head_sha,
        "head_ref": ask.head_ref,
        "base_ref": "master",
        "base_tip": ask.base_tip,
        "changes": [list(c) for c in ask.changes],
    }


def spawn_as(
    name: str,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdin: int = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """`argv` as `name` by direct drop (as `roles_world.as_user`), running:
    stdout a pipe, stderr a pipe."""
    pw = pwd.getpwnam(name)
    return subprocess.Popen(
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**env, "HOME": pw.pw_dir},
        cwd="/",
        user=pw.pw_uid,
        group=pw.pw_gid,
        extra_groups=[pw.pw_gid],
    )


def driver_result(proc: subprocess.Popen[bytes], timeout: float) -> dict[str, object]:
    """The one JSON line `DRIVER` printed (`ValueError` otherwise)."""
    out, err = proc.communicate(timeout=timeout)
    lines = out.decode("utf-8", "replace").splitlines()
    if proc.returncode != 0 or len(lines) != 1:
        raise ValueError(
            f"driver: code {proc.returncode}; stdout {out[-800:]!r}; "
            f"stderr {err[-800:]!r}"
        )
    found = json.loads(lines[0])
    if not isinstance(found, dict):
        raise ValueError(f"driver: {lines[0]!r}")
    return found


# ---------- processes, as root ----------


@dataclass(frozen=True)
class Proc:
    """One line of `ps`."""

    pid: int
    ppid: int
    uid: int
    stat: str
    command: str


def processes() -> list[Proc]:
    """Every process `ps -A` shows."""
    argv = [PS, "-A", "-ww", "-o", "pid=,ppid=,uid=,stat=,command="]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    if done.returncode != 0:
        raise OSError(f"ps: code {done.returncode}: {done.stderr.strip()}")
    found = []
    for line in done.stdout.splitlines():
        words = line.split(None, 4)
        if len(words) >= 4 and all(w.isdigit() for w in words[:3]):
            command = words[4] if len(words) == 5 else ""
            found.append(
                Proc(int(words[0]), int(words[1]), int(words[2]), words[3], command)
            )
    return found


def live_of(uid: int) -> list[Proc]:
    """The processes of `uid` that are not zombies."""
    return [p for p in processes() if p.uid == uid and not p.stat.startswith("Z")]


def wait_for(probe: Callable[[], object], timeout: float, step: float = 0.05) -> object:
    """The first true value of `probe()` within `timeout` seconds, else
    `None`."""
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(step)


def fd_names(pid: int) -> list[str]:
    """What the open descriptors of `pid` name: `/proc/<pid>/fd` targets
    (Linux) or `lsof -p` names (macOS)."""
    if plat.detect_platform() == "linux-x86_64":
        base = Path(f"/proc/{pid}/fd")
        return sorted(f"{fd.name}:{os.readlink(fd)}" for fd in base.iterdir())
    done = subprocess.run(
        [LSOF, "-n", "-P", "-p", str(pid), "-F", "fn"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    names, fd = [], ""
    for line in done.stdout.splitlines():
        if line.startswith("f"):
            fd = line[1:]
        elif line.startswith("n"):
            names.append(f"{fd}:{line[1:]}")
    if not names:
        raise OSError(f"lsof -p {pid}: code {done.returncode}: {done.stderr.strip()}")
    return names


def environment(pid: int) -> str:
    """The environment of `pid` as text: `/proc/<pid>/environ` (Linux) or
    `ps -E` (macOS, the command line with the environment after it)."""
    if plat.detect_platform() == "linux-x86_64":
        return Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    done = subprocess.run(
        [PS, "-E", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode != 0:
        raise OSError(f"ps -E -p {pid}: code {done.returncode}")
    return done.stdout


# ---------- the publisher's memory ----------

#: A process holding `bytes.fromhex(argv[1])` in a ctypes buffer: prints
#: `pid address length`, then waits for a line on stdin.
MEMORY_HOLDER = """
import ctypes, os, sys
data = bytes.fromhex(sys.argv[1])
buf = ctypes.create_string_buffer(data, len(data))
print(os.getpid(), ctypes.addressof(buf), len(data), flush=True)
sys.stdin.readline()
"""

#: Linux: `pread` of `/proc/<pid>/mem` at `address` (`argv[1:4]`): the
#: bytes in hex or the errno, as JSON.
PROC_MEM_READER = """
import json, os, sys
pid, address, length = (int(x) for x in sys.argv[1:4])
try:
    fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    try:
        data = os.pread(fd, length, address)
    finally:
        os.close(fd)
except OSError as exc:
    print(json.dumps({"errno": exc.errno, "error": str(exc)}))
else:
    print(json.dumps({"hex": data.hex()}))
"""

_LLDB_ROW = re.compile(r"^0x[0-9a-f]+: ((?:0x[0-9a-f]{2}[ \t]*)+)$", re.MULTILINE)


def lldb_argv(pid: int, address: int, length: int) -> list[str]:
    """macOS: `lldb -p <pid> --batch`, one `memory read` of `length` bytes
    at `address`, then detach (`task_for_pid` decides the attach)."""
    read = f"memory read --format x --size 1 --count {length} {address}"
    return [LLDB, "-p", str(pid), "--batch", "-o", read, "-o", "process detach"]


def lldb_bytes(text: str) -> bytes:
    """The bytes of every `memory read` row in lldb's output."""
    found = bytearray()
    for row in _LLDB_ROW.finditer(text):
        found += bytes(int(b, 16) for b in row.group(1).split())
    return bytes(found)


#: A publisher holding a sentinel descriptor without `CLOEXEC` (`argv[1]`,
#: opened inheritable) and the sentinel variable in its environment, which
#: starts `argv[2]` (the precheck command) with `close_fds=False` and stdin a
#: pipe it does not write to; prints its own and the command's PIDs, then
#: waits for a line on stdin, closes the command's stdin and prints its code.
INHERIT_HOLDER = """
import json, os, subprocess, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
os.set_inheritable(fd, True)
proc = subprocess.Popen(json.loads(sys.argv[2]), stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=False)
print(json.dumps({"pid": os.getpid(), "fd": fd, "sudo": proc.pid}), flush=True)
sys.stdin.readline()
proc.stdin.close()
print(json.dumps({"code": proc.wait()}), flush=True)
"""


# ---------- the world ----------


@dataclass
class PrecheckWorld:
    """The product repository behind its git server, and the two stop PRs."""

    rw: RolesWorld
    repo: ProductRepo
    git: TokenGitServer
    current: Ask
    stale: Ask

    @property
    def home(self) -> Path:
        """`df-precheck`'s home: its snapshots and lock."""
        return self.rw.layout.role_home("df-precheck")

    def snapshots(self) -> list[str]:
        """Names under `<home>/precheck-snapshots` (none: `[]`)."""
        root = self.home / "precheck-snapshots"
        return sorted(p.name for p in root.iterdir()) if root.exists() else []

    def start(
        self, ask: Ask, *, git_base: str | None = None
    ) -> subprocess.Popen[bytes]:
        """`DRIVER` for `ask` as `df-publisher` (git at `git_base`, default
        this world's server), running."""
        spec = driver_spec(
            ask,
            tree=self.rw.layout.admin_dir(self.rw.installed.release_id),
            product=PRODUCT_ID,
            api_base=self.rw.fake.url,
            git_base=git_base or self.git.base,
            openssl=OPENSSL,
        )
        python = str(admin_python(self.rw.layout))
        argv = [python, "-I", "-B", "-S", "-c", DRIVER, json.dumps(spec)]
        return spawn_as(PUBLISHER, argv, env=BASE_ENV)

    def ask(
        self, ask: Ask, *, git_base: str | None = None, timeout: float = 300
    ) -> dict[str, object]:
        """`DRIVER`'s result for `ask`."""
        return driver_result(self.start(ask, git_base=git_base), timeout)


@contextmanager
def precheck_world(rw: RolesWorld) -> Iterator[PrecheckWorld]:
    """The product `FULL_NAME`: deliveries `DELIVERIES` (squash), stop PRs
    `STOP_CURRENT` and `STOP_STALE`, served to the fake GitHub's live
    installation tokens; the fake GitHub learns the delivery facts."""
    repo = ProductRepo.create(rw.work / "precheck", FULL_NAME)
    for pr, request_id, generation in DELIVERIES:
        repo.deliver(pr, request_id, generation)
    current, stale = repo.stop_pr(*STOP_CURRENT), repo.stop_pr(*STOP_STALE)
    tip = repo.tip()
    repo.publish()
    commit_pulls, pulls = delivery_facts(repo)
    rw.fake.scenario.commit_pulls.update(commit_pulls)
    rw.fake.scenario.pulls.update(pulls)
    git = TokenGitServer(repo.served_root, lambda token: token in rw.fake.tokens)
    try:
        yield PrecheckWorld(
            rw,
            repo,
            git,
            replace(current, base_tip=tip),
            replace(stale, base_tip=tip),
        )
    finally:
        git.close()


@dataclass(frozen=True)
class Observed:
    """One observer run: its code (`None`: timed out), stdout and stderr."""

    code: int | None
    stdout: str
    stderr: str

    def shown(self) -> dict[str, object]:
        """For the evidence."""
        return {
            "code": self.code,
            "stdout": self.stdout[-1500:],
            "stderr": self.stderr[-800:],
        }


def observe(argv: Sequence[str], *, user: str | None, timeout: float) -> Observed:
    """`argv` as `user` (direct drop) or as root (`None`), stdin closed,
    within `timeout` seconds. Only root runs an observer: off root (a
    workstation) it is a `PermissionError` before anything starts -- a
    debugger attach there would ask the human for Developer Tools access."""
    if os.geteuid() != 0:
        raise PermissionError(f"observer only as root: {argv[0]}")
    extra: dict[str, object] = {}
    env = dict(BASE_ENV)
    if user is not None:
        pw = pwd.getpwnam(user)
        extra = {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": [pw.pw_gid]}
        env["HOME"] = pw.pw_dir
    else:
        env["HOME"] = pwd.getpwuid(0).pw_dir
    try:
        done = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=env,
            cwd="/",
            timeout=timeout,
            check=False,
            **extra,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        err = (exc.stderr or b"").decode("utf-8", "replace")
        return Observed(None, out, err)
    return Observed(
        done.returncode,
        done.stdout.decode("utf-8", "replace"),
        done.stderr.decode("utf-8", "replace"),
    )
