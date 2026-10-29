"""The world of the stage-2a root acceptance (Task 14; spec 4.5 without merge).

Runs only as root in the disposable CI runners (the root gate of
`tests/df/conftest.py` skips it elsewhere). One world per module, in the
production layout `/` (R14-8: the release's own code, `launch_release`, has
no other root, so a `<R>` world cannot run a role's operation):

1. the approved uv copied to the README's `UV_DIR`, then one committed
   `bootstrap init` (in process, as stage 1's `installed_world`) of the
   fixture repo, which holds `FIXTURE_FROM_CHECKOUT` of this checkout
   (R14-3: defined here once, Task 15 imports it);
2. as the operator `runner` through its own `sudo`: `darkfactory cli set
   openssl`, `darkfactory roles setup --operator runner`;
3. root writes the fixture `products.toml` (product 1, App 1) and points
   `publisher.toml`'s `api_base` at the local fake GitHub
   (`tests/df/fake_github.py`, r2 N6: a root key, noted in the evidence);
4. `sudo darkfactory setup-agent app-key` / `machine-token` (R14-4): the
   real chain, checked by the publisher's `self-check` and `df-exec`'s
   `check-github` against the fake;
5. `models set` of every role, a fixture registry entry of a revoked
   release and fixture run records in `runs/` (r1 N11: marked
   `"fixture": true` in the evidence).

Nothing of it may exist before (`require_clean_host`). The teardown deletes
the roles, `/etc/sudoers.d/df` (and the I/O-log file of the tests), the
operator's `gh` fixture if this world made it, `df-build`, `/opt/df`,
`/etc/df`, `/var/lib/df`, `UV_DIR`, the homes and the command's link, then
checks that nothing is left. A failure of the world, of a launch or of an
observer is `pytest.fail("инфраструктура: …")`, never a PASS or a skip.

Processes "of a role" are started by direct drop (`as_user`: `setgid`,
`setgroups`, `setuid` in the child, no `sudo` around them), so the `sudo`
each of them runs is the role's own and nothing else's: an outer `sudo`
session would hold a copy of the role's output in its I/O log. The
operator's processes go through `sudo -n -u runner` (its groups as the
host made them), except where a held descriptor must reach the call.
"""

from __future__ import annotations

import gzip
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from df import cli, fsutil, journal, models, perimeter_client, roles, run_start, sudoers
from df import platform as plat
from df.config import ReleaseEntry, dump_releases, load_accounts, parse_releases
from df.lockset import UV_PINNED_VERSION
from df.paths import Layout
from df.publisher import request
from df.publisher.request import BadResponse, Response
from df.stage2_config import (
    Product,
    RunRecord,
    dump_products,
    dump_publisher,
    parse_publisher,
)
from tests.df.conftest import stage1_out
from tests.df.fake_github import (
    FakeGitHub,
    Installation,
    Repo,
    Scenario,
    serve_fake_github,
)
from tests.df.root_world import (
    FixtureRepo,
    InstalledWorld,
    RootWorld,
    assert_committed,
    current_id,
    infra,
    remove_service_user,
    run,
    write_evidence,
)

#: What the fixture repo carries from this checkout (R14-3, plan Task 15):
#: `df/` and the approval-branch patterns `merge` routing reads.
FIXTURE_FROM_CHECKOUT = ("df", "contracts/approval-branches/v1/patterns.env")
OPERATOR = "runner"
PRODUCT_ID = 1
APP_ID = 1
INSTALLATION_ID = 11
FULL_NAME = "df-fixture/product"
MACHINE_USER = "df-fixture-machine"
#: A fixture string of the machine user's token form; not a credential.
MACHINE_TOKEN_TEXT = "ghp_dfFixtureTokenNotASecret0000"
APP_SLUG = "df-fixture-app"
REVOKED_ID = "fixture-revoked"
REVOKED_REASON = "fixture: Task 14"
ROOT_ID = perimeter_client.RootId("github.com", PRODUCT_ID, "main")
TRUST_VIEW = perimeter_client.TrustView("0" * 40, 0)
SUDO = "/usr/bin/sudo"
OPENSSL = Path("/usr/bin/openssl")
PUBLISHER = "df-publisher"
#: The environment of every process this module starts: `LC_ALL=C` for
#: sudo's diagnostics (r4 N4).
BASE_ENV: Mapping[str, str] = {
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "C",
    "LC_ALL": "C",
}
#: Host labels of the committed diagnostics (as `fixtures/sudo-l/`).
HOST = "mac-mini" if plat.detect_platform() == "macos-arm64" else "ubuntu-ci"
OS_DIR = "macos" if plat.detect_platform() == "macos-arm64" else "linux"
#: The world is the production layout (R14-8): the release's own code
#: (`launch_release`) knows no other root.
PRODUCTION = Layout(Path("/"))
#: README «Утверждённые CLI»: where the approved uv lives.
UV_DIR = Path("/usr/local/libexec/darkfactory/bin")
#: Accounts the world creates (absent before it, removed after it).
ACCOUNTS = (plat.BUILD_USER, *roles.ROLE_USERS)
#: The second `sudoers.d` file of the I/O-log rows; sorts after `df`.
IOLOG_FILE = sudoers.SUDOERS_FILE.with_name("zz-df-acc-iolog")
TIMEOUT = 300

SudoDiag = Literal["not-allowed", "preserve-env", "password"]
#: sudo's refusals by mechanism, at `LC_ALL=C` (r4 N4). Password first: a
#: refusal that asks for a password is a wrong setup, never a denial.
_DIAG: tuple[tuple[SudoDiag, re.Pattern[str]], ...] = (
    ("password", re.compile(r"a password is required|a terminal is required")),
    ("preserve-env", re.compile(r"not allowed to preserve the environment")),
    ("not-allowed", re.compile(r"is not allowed to execute|not allowed to run")),
)


def classify_sudo(returncode: int, stderr: str) -> SudoDiag | None:
    """The mechanism of a refusal of `sudo` (exit 1); `None`: not one of
    the known diagnostics."""
    if returncode != 1:
        return None
    return next((kind for kind, rx in _DIAG if rx.search(stderr)), None)


def fixture_form(text: str, root: Path, hostnames: Sequence[str]) -> str:
    """A captured diagnostic as committed: `<R>` paths as production ones,
    the runner's host name as `HOST`."""
    text = text.replace(f"{root}/", "/")
    for name in sorted({n for n in hostnames if n}, key=len, reverse=True):
        text = text.replace(name, HOST)
    return text


def host_names() -> list[str]:
    """The names sudo may print for this host (full and short)."""
    full = socket.gethostname()
    return [full, full.split(".")[0]]


# ---------- processes ----------


def as_user(
    name: str,
    argv: Sequence[str],
    *,
    stdin: bytes = b"",
    env: Mapping[str, str] | None = None,
    cwd: Path = Path("/"),
    pass_fds: Sequence[int] = (),
) -> subprocess.CompletedProcess[bytes]:
    """`argv` as `name` by direct drop (its own group only): the process is
    the account, and no `sudo` session wraps it."""
    pw = pwd.getpwnam(name)
    base = {**BASE_ENV, "HOME": pw.pw_dir}
    return _run(
        argv,
        stdin=stdin,
        env={**base, **(env or {})},
        cwd=cwd,
        pass_fds=pass_fds,
        user=pw.pw_uid,
        group=pw.pw_gid,
    )


def as_operator(
    argv: Sequence[str], *, stdin: bytes = b"", cwd: Path = Path("/")
) -> subprocess.CompletedProcess[bytes]:
    """`argv` as the operator through `sudo -n -u runner` (its groups), with
    `BASE_ENV` and its own `HOME`."""
    home = pwd.getpwnam(OPERATOR).pw_dir
    env = [f"{k}={v}" for k, v in {**BASE_ENV, "HOME": home}.items()]
    full = [SUDO, "-n", "-u", OPERATOR, "/usr/bin/env", "-i", *env, *argv]
    return _run(full, stdin=stdin, env=BASE_ENV, cwd=cwd)


def _run(
    argv: Sequence[str],
    *,
    stdin: bytes,
    env: Mapping[str, str],
    cwd: Path,
    pass_fds: Sequence[int] = (),
    user: int | None = None,
    group: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            env=dict(env),
            cwd=cwd,
            pass_fds=tuple(pass_fds),
            user=user,
            group=group,
            extra_groups=None if group is None else [group],
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        infra(f"{' '.join(argv)[:200]}: {type(exc).__name__}: {exc}")
        raise


def shown(done: subprocess.CompletedProcess[bytes]) -> str:
    """Exit code, stdout and stderr of a run, for a message."""
    out = done.stdout.decode("utf-8", "replace").strip()[-800:]
    err = done.stderr.decode("utf-8", "replace").strip()[-800:]
    return f"код {done.returncode}; stdout {out!r}; stderr {err!r}"


def response_of(done: subprocess.CompletedProcess[bytes]) -> Response:
    """The publisher's one response line; anything else fails the test."""
    lines = done.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        pytest.fail(f"ответа издателя нет: {shown(done)}")
    try:
        return request.parse_response(lines[0])
    except BadResponse as exc:
        pytest.fail(f"ответ издателя не разобран: {exc}; {shown(done)}")
        raise


# ---------- requests ----------


def new_request_id(tag: str) -> str:
    """A fresh request id (`request.REQUEST_ID`)."""
    return f"acc-{tag}-{uuid.uuid4().hex}"


def status_request(
    run_id: str, request_id: str, *, sha: str = TRUST_VIEW.snapshot_sha
) -> bytes:
    """A `perimeter-status` request of `run_id` for `ROOT_ID`."""
    trust = perimeter_client.TrustView(sha, TRUST_VIEW.last_epoch)
    return perimeter_client.payload(run_id, ROOT_ID, trust, request_id)


def pr_request(op: str, run_id: str, request_id: str) -> bytes:
    """A valid `merge` / `publish-review` request (schema of `request`)."""
    data: dict[str, object] = {
        "schema": request.REQUEST_SCHEMA,
        "request_id": request_id,
        "run_id": run_id,
        "repository_id": PRODUCT_ID,
        "pr": 1,
        "head_sha": "a" * 40,
    }
    if op == "publish-review":
        data |= {"decision": "comment", "body": "fixture"}
    return json.dumps(data, sort_keys=True).encode("utf-8")


# ---------- the I/O log of sudo ----------

IO_STREAMS = ("stdin", "stdout", "stderr", "ttyin", "ttyout")
_IO_META = frozenset({"log", "log.json", "timing"})


@dataclass(frozen=True)
class IoSession:
    """One session of sudo's I/O log: its record text and streams."""

    path: Path
    meta: str
    streams: Mapping[str, bytes]


def io_sessions(root: Path) -> list[IoSession]:
    """Every session under `iolog_dir` `root`; a file of unknown type is an
    infrastructure failure, a gzip stream is decompressed."""
    found: list[IoSession] = []
    if not root.exists():
        return found
    for dirpath, _dirs, files in os.walk(root):
        names = set(files)
        here = Path(dirpath)
        if not names & _IO_META:
            if names - {"seq"}:
                infra(f"I/O-журнал: нераспознанные файлы {sorted(names)} в {here}")
            continue
        unknown = names - _IO_META - set(IO_STREAMS)
        if unknown:
            infra(f"I/O-журнал: нераспознанные файлы {sorted(unknown)} в {here}")
        meta = "\n".join(
            _stream(here / n).decode("utf-8", "replace")
            for n in sorted(names & {"log", "log.json"})
        )
        streams = {n: _stream(here / n) for n in IO_STREAMS if n in names}
        found.append(IoSession(here, meta, streams))
    return found


def _stream(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except (OSError, EOFError) as exc:
            infra(f"I/O-журнал: {path}: gzip не распакован: {exc}")
    return data


def found_in(
    sessions: Sequence[IoSession], marker: str, streams: Sequence[str]
) -> list[Path]:
    """Sessions whose `streams` hold `marker`."""
    needle = marker.encode("ascii")
    return [
        s.path
        for s in sessions
        if any(needle in s.streams.get(name, b"") for name in streams)
    ]


# ---------- root writes of the world ----------


def write_etc(layout: Layout, name: str, text: str, mode: int = 0o644) -> None:
    """`/etc/df/<name>` as a writer does: inside a generation bracket."""
    journal.generation_begin(layout)
    try:
        fsutil.write_atomic(layout.etc / name, text, mode)
    finally:
        journal.generation_end(layout)


def remove_etc(layout: Layout, name: str) -> None:
    """Remove `/etc/df/<name>` inside a generation bracket (absent is fine)."""
    journal.generation_begin(layout)
    try:
        fsutil.remove_durably(layout.etc / name)
    except FileNotFoundError:
        pass
    finally:
        journal.generation_end(layout)


def registry_text(layout: Layout) -> str:
    """The text of `releases.toml`."""
    return (layout.etc / "releases.toml").read_text()


def with_entry(text: str, entry: ReleaseEntry) -> str:
    """`releases.toml` text with `entry` in place of the same id (or added)."""
    rows = [e for e in parse_releases(text).releases if e.id != entry.id]
    return dump_releases(replace(parse_releases(text), releases=(*rows, entry)))


def products_text() -> str:
    """The fixture `products.toml`: product 1 of App 1, profile `write`."""
    product = Product(
        PRODUCT_ID,
        FULL_NAME,
        "write",
        APP_ID,
        INSTALLATION_ID,
        MACHINE_USER,
        "/var/empty",
    )
    return dump_products({PRODUCT_ID: product})


def scenario() -> Scenario:
    """The fake GitHub of the world: one installation on product 1."""
    rights = {"contents": "write", "pull_requests": "write", "metadata": "read"}
    return Scenario(
        app_id=APP_ID,
        app_slug=APP_SLUG,
        installations=[Installation(INSTALLATION_ID, "df-fixture", (1,), rights)],
        machine_token=MACHINE_TOKEN_TEXT,
        login=MACHINE_USER,
        repos=[Repo(1, FULL_NAME, {"push": True, "admin": False, "pull": True})],
    )


# ---------- the world ----------


@dataclass
class RolesWorld:
    """An installed world with the roles, the fake GitHub and fixtures."""

    installed: InstalledWorld
    fake: FakeGitHub
    work: Path
    allowed_run: str = ""
    revoked_run: str = ""
    created: list[Path] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)
    runs: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def layout(self) -> Layout:
        return self.installed.layout

    def uid(self, name: str) -> int:
        """The UID of a role from `accounts.toml`."""
        account = load_accounts(self.layout.accounts).get(name)
        if account is None:
            infra(f"нет записи {name} в accounts.toml")
        assert account is not None
        return account.uid

    def wrapper(self, name: str) -> str:
        """`<libexec>/<name>`."""
        return str(self.layout.libexec / name)

    def darkfactory(self, argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        """`sudo darkfactory <argv>` as the operator."""
        return as_operator([SUDO, "-n", str(self.layout.launcher), *argv])

    def human(
        self, argv: Sequence[str], *, cwd: Path = Path("/")
    ) -> subprocess.CompletedProcess[bytes]:
        """`darkfactory <argv>` as the operator, without `sudo` (the human's
        unprivileged entries: `launch`, `run`)."""
        return as_operator([str(self.layout.launcher), *argv], cwd=cwd)

    def publish(
        self,
        role: str,
        op: str,
        data: bytes,
        *,
        flags: Sequence[str] = (),
        extra: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """`role` runs `sudo [flags] -n -u df-publisher df-publish <op>
        [extra]` with `data` on stdin."""
        argv = [SUDO, *flags, "-n", "-u", PUBLISHER, self.wrapper("df-publish"), op]
        return as_user(role, [*argv, *extra], stdin=data, env=env)

    def journal_lines(self) -> list[dict[str, object]]:
        """The publisher's journal (root reads it)."""
        path = self.layout.publisher_dir / "journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def register_run(self, release: ReleaseEntry, *, root: bool = False) -> str:
        """A fixture run record of `release` in `runs/` (root's, `0644`),
        through `run_start.register`; `root`: bound to the trust fixture's
        root (`TRUST_ROOT`)."""
        text = (self.layout.etc / "models.toml").read_text()
        pins = run_start.pins_from_data(
            self.layout, PRODUCT_ID, models.parse_models(text)
        )
        rec = RunRecord(
            run_start.new_run_id(),
            release.id,
            release.digest,
            release.admin_digest,
            PRODUCT_ID,
            pins,
            TRUST_ROOT if root else None,
            datetime.now(UTC).isoformat(),
            0,
        )
        run_start.register(self.layout, rec)
        self.runs[rec.run_id] = {
            "fixture": True,
            "release_id": release.id,
            "root_id": rec.root_id,
        }
        evidence("fixtures.json", {**self.notes, "fixture_runs": self.runs})
        return rec.run_id

    def current_entry(self) -> ReleaseEntry:
        """The registry entry of `current`."""
        rid = current_id(self.layout)
        entries = parse_releases(registry_text(self.layout)).releases
        found = next((e for e in entries if e.id == rid), None)
        if found is None:
            infra(f"нет записи current {rid} в releases.toml")
        assert found is not None
        return found

    def role_probe(self, role: str, steps: Sequence[tuple[str, str, str]]) -> list[int]:
        """`errno` of each `(op, path, arg)` done by `role` itself (0: done):
        `read`, `put`, `create`, `write`, `rename` (to `arg`), `delete`,
        `chmod`; the admin Python runs `PROBE` as the role."""
        python = str(admin_python(self.layout))
        done = as_user(role, [python, "-I", "-S", "-c", PROBE, json.dumps(steps)])
        if done.returncode != 0:
            infra(f"проба от {role}: {shown(done)}")
        try:
            found = json.loads(done.stdout)
        except ValueError:
            found = None
        if not isinstance(found, list) or len(found) != len(steps):
            infra(f"проба от {role}: {shown(done)}")
        assert isinstance(found, list)
        return [int(x) for x in found]


#: The trust fixture's root (`disk_full` writes `trust.toml` for it).
TRUST_ROOT = ("github.com", 5, "main")
TRUST_TEXT = (
    'schema = "df/trust/v1"\n'
    'host = "github.com"\n'
    "df_trust_repository_id = 5\n"
    'df_trust_full_name = "df-fixture/df-trust"\n'
    'df_trust_branch = "main"\n'
    "approval_policy_repository_id = 6\n"
    'transport = "public"\n'
)

#: The role's file operations, by the role itself (`RolesWorld.role_probe`).
PROBE = """
import json, os, sys
def step(op, path, arg):
    try:
        if op == "read":
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            os.read(fd, 1)
            os.close(fd)
        elif op == "put":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, b"own\\n")
            os.close(fd)
        elif op == "create":
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        elif op == "write":
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            os.write(fd, b"x")
            os.close(fd)
        elif op == "rename":
            os.rename(path, arg)
        elif op == "delete":
            os.unlink(path)
        elif op == "chmod":
            os.chmod(path, 0o600)
        else:
            return -1
        return 0
    except OSError as exc:
        return exc.errno
print(json.dumps([step(*s) for s in json.loads(sys.argv[1])]))
"""


def admin_python(layout: Layout) -> Path:
    """The admin Python the wrappers run (from `df-publish`'s text)."""
    text = (layout.libexec / "df-publish").read_text()
    match = re.search(r"exec (\S+) -I", text)
    if match is None:
        infra(f"df-publish: нет exec в тексте: {text!r}")
    assert match is not None
    return Path(match.group(1).strip("'"))


def evidence(name: str, data: object) -> None:
    """`roles/<name>` under the CI evidence directory (none locally)."""
    text = data if isinstance(data, str) else json.dumps(data, indent=2, default=str)
    write_evidence(stage1_out(), f"roles/{name}", text)


def require_clean_host() -> None:
    """A leftover of an install, a role or a sudoers file is someone else's:
    refuse, touch nothing."""
    for path in (sudoers.SUDOERS_FILE, IOLOG_FILE, *owned_paths(PRODUCTION)):
        if os.path.lexists(path):
            infra(f"{path} уже есть")
    leftover = [name for name in ACCOUNTS if _known(name)]
    if leftover:
        infra(f"учётки остались от прошлого прогона: {leftover}")
    if not _known(OPERATOR):
        infra(f"нет учётки оператора {OPERATOR}")


def _known(name: str) -> bool:
    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def check(done: subprocess.CompletedProcess[bytes], what: str) -> None:
    """A step of the world must succeed."""
    if done.returncode != 0:
        infra(f"{what}: {shown(done)}")


@contextmanager
def roles_world(
    top: Path, repo: FixtureRepo, control_url: str, keep: Path | None
) -> Iterator[RolesWorld]:
    """The module's world (module doc); torn down whatever happens."""
    require_clean_host()
    layout = PRODUCTION
    with ExitStack() as stack:
        stack.callback(teardown_install, layout, keep)
        stack.callback(teardown_roles, layout)
        world = RootWorld(layout, repo, approved_uv(), top, control_url)
        installed = install_production(world)
        work = top / "roles"
        work.mkdir(mode=0o755)
        os.chmod(work, 0o755)
        key, public = make_app_key(work)
        fake = stack.enter_context(
            serve_fake_github(scenario(), public_key=public, openssl=OPENSSL)
        )
        rw = RolesWorld(installed, fake, work)
        stack.callback(remove_created, rw)
        set_up(rw, key)
        yield rw


def approved_uv() -> Path:
    """The pinned uv (the runner's) copied root `0755` to `UV_DIR`, the
    README's place of the approved uv (R88's copy, in `/`)."""
    found = shutil.which("uv")
    if found is None:
        infra("uv не найден на PATH")
    assert found is not None
    copy = UV_DIR / "uv"
    run(["/usr/bin/install", "-d", "-o", "root", "-m", "0755", str(UV_DIR)])
    run(["/usr/bin/install", "-o", "root", "-m", "0755", found, str(copy)])
    version = run([str(copy), "--version"]).split()
    if version[1:2] != [UV_PINNED_VERSION]:
        infra(f"uv {version}: нужен {UV_PINNED_VERSION}")
    return copy


def install_production(world: RootWorld) -> InstalledWorld:
    """`bootstrap init` of the fixture pin (R91's substitution of the
    profile, for this install only) into `/`; a refusal fails the module."""
    from df import install

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(install, "_profile", lambda _cfg: world.profile())
        code = world.bootstrap(world.init_argv())
    if code != cli.EXIT_OK:
        infra(f"установка в /: код {code}")
    assert_committed(world.layout)
    rid = current_id(world.layout)
    if rid is None:
        infra("установка в /: нет current")
    assert rid is not None
    return InstalledWorld(world, rid)


def make_app_key(work: Path) -> tuple[Path, Path]:
    """A fresh RSA key of the fixture App (root `0600`) and its public half."""
    key, public = work / "app-1.pem", work / "app-1.pub"
    gen = [str(OPENSSL), "genpkey", "-algorithm", "RSA"]
    gen += ["-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)]
    for argv in (gen, [str(OPENSSL), "pkey", "-in", str(key), "-pubout"]):
        done = _run(argv, stdin=b"", env=BASE_ENV, cwd=work)
        check(done, " ".join(argv[:2]))
        if argv[1] == "pkey":
            public.write_bytes(done.stdout)
    os.chmod(key, 0o600)
    return key, public


def set_up(rw: RolesWorld, key: Path) -> None:
    """Steps 2-5 of the module doc."""
    lay = rw.layout
    check(rw.darkfactory(["cli", "set", "openssl", str(OPENSSL)]), "cli set openssl")
    check(rw.darkfactory(["roles", "setup", "--operator", OPERATOR]), "roles setup")
    cfg = parse_publisher((lay.etc / "publisher.toml").read_text())
    write_etc(lay, "publisher.toml", dump_publisher(replace(cfg, api_base=rw.fake.url)))
    write_etc(lay, "products.toml", products_text())
    rw.notes["api_base"] = {
        "value": rw.fake.url,
        "note": "нештатный API: локальный поддельный GitHub (root-ключ publisher.toml)",
    }
    token = rw.work / "machine-token"
    token.write_text(MACHINE_TOKEN_TEXT + "\n")
    os.chmod(token, 0o600)
    app_key = ["setup-agent", "app-key", "--app-id", str(APP_ID), "--file", str(key)]
    check(rw.darkfactory(app_key), "setup-agent app-key")
    machine = ["setup-agent", "machine-token", "--file", str(token)]
    check(rw.darkfactory(machine), "setup-agent machine-token")
    for i, role in enumerate(models.ROLES):
        choice = ["models", "set", role, "--harness", "claude"]
        check(rw.darkfactory([*choice, "--model", f"fixture-{i}"]), f"models {role}")
    current = rw.current_entry()
    revoked = ReleaseEntry(
        REVOKED_ID,
        "sha256:" + "0" * 64,
        current.admin_digest,
        current.admin_python,
        "revoked",
        REVOKED_REASON,
    )
    write_etc(lay, "releases.toml", with_entry(registry_text(lay), revoked))
    rw.notes["registry_fixture"] = {"id": REVOKED_ID, "reason": REVOKED_REASON}
    rw.allowed_run = rw.register_run(current)
    rw.revoked_run = rw.register_run(revoked)
    make_gh_fixture(rw)
    evidence("fixtures.json", {**rw.notes, "fixture_runs": rw.runs})


def make_gh_fixture(rw: RolesWorld) -> None:
    """`~runner/.config/gh/hosts.yml` (gh's own modes: `0600` in a `0700`
    directory), made by the operator only if absent; what is made is
    removed by the teardown."""
    home = Path(pwd.getpwnam(OPERATOR).pw_dir)
    config, gh = home / ".config", home / ".config" / "gh"
    hosts = gh / "hosts.yml"
    if hosts.exists():
        rw.notes["gh_fixture"] = f"{hosts}: уже был, не тронут"
        return
    rw.created += [p for p in (config, gh) if not p.exists()] + [hosts]
    script = (
        f"umask 077; mkdir -p {gh} && printf 'github.com:\\n  user: df\\n' > {hosts}"
    )
    check(as_operator(["/bin/sh", "-c", script]), "фикстура ~/.config/gh")
    rw.notes["gh_fixture"] = f"{hosts}: создан тестом"


def remove_created(rw: RolesWorld) -> None:
    """What `make_gh_fixture` made, newest first."""
    for path in reversed(rw.created):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def teardown_roles(layout: Layout) -> None:
    """The sudoers files and the roles (their processes first); a role
    record left is an infrastructure failure."""
    for path in (IOLOG_FILE, sudoers.SUDOERS_FILE):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    for name in roles.ROLE_USERS:
        if _known(name):
            plat.reap_user_processes(name)
        remove_service_user(name)
    left = [name for name in roles.ROLE_USERS if _known(name)]
    if left or any(os.path.lexists(p) for p in (IOLOG_FILE, sudoers.SUDOERS_FILE)):
        infra(f"после теста остались роли {left} или файлы sudoers ({layout.root})")


def owned_paths(layout: Layout) -> list[Path]:
    """What the world makes in `/` (absent before it, removed after it)."""
    homes = [layout.role_home(name) for name in ACCOUNTS]
    return [layout.opt, layout.etc, layout.var, UV_DIR.parent, *homes]


def teardown_install(layout: Layout, keep: Path | None) -> None:
    """The install in `/`: the journal kept as evidence, `df-build`'s
    processes and records, the trees of `owned_paths`, the command's link;
    anything left is an infrastructure failure."""
    if keep is not None and layout.journal.exists():
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(layout.journal, keep)
    if _known(plat.BUILD_USER):
        plat.reap_user_processes(plat.BUILD_USER)
    remove_service_user(plat.BUILD_USER)
    link = layout.local_bin / layout.launcher.name
    if os.path.islink(link) and os.readlink(link) == str(layout.launcher):
        os.unlink(link)
    for path in owned_paths(layout):
        shutil.rmtree(path, ignore_errors=True)
    left = [str(p) for p in owned_paths(layout) if os.path.lexists(p)]
    if left or _known(plat.BUILD_USER):
        infra(f"после теста остались {left} или {plat.BUILD_USER}")


def install_sudoers_file(path: Path, text: str) -> None:
    """A root `0440` file in `sudoers.d`, after `visudo -c` of its temp."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    done = _run(
        [sudoers.VISUDO, "-c", "-f", str(tmp)], stdin=b"", env=BASE_ENV, cwd=Path("/")
    )
    if done.returncode != 0:
        os.unlink(tmp)
        infra(f"visudo отверг {text!r}: {shown(done)}")
    os.rename(tmp, path)
