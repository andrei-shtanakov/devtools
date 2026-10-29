"""The root world of the df stage-1 acceptance (Task 13-B, R83/R88/R90/R91).

Runs only as root in the disposable CI runners (the root gate of
`tests/df/conftest.py` skips every test using it elsewhere).

- Layout (R83): `<base>/df-acc-<pid>/r<n>`, every level root `0755`, `<base>`
  = `/private/var/tmp-df` (macOS) or `/var/tmp-df` (Linux); `<R>/Users`
  (macOS) or `<R>/home` (Linux) exists root `0755`, as on a host.
- Approved CLI (R88): `/usr/bin/git`, `/usr/bin/tar` and `<R>/cli/uv`, a
  root `0755` copy of the pinned uv on `PATH`.
- Profile pin (R91): `install._profile` returns a profile of one repo named
  `devtools` (the admin code comes from its `df/`): the fixture project
  `tests/df/fixtures/proj/` plus this checkout's `df/`, committed with a
  fixed author and date into a root-owned bare repo. Served `file://` on
  Linux; on macOS git refuses a `file://` repo of another owner ("dubious
  ownership", Step 0), so a `git daemon` on 127.0.0.1 serves it (`git://`).
- `df-build` (R90): absent when a world starts (a leftover fails the test
  as infrastructure); the teardown kills its processes, deletes the user and
  the group, removes `<R>` and asserts the records are gone.
- `installed_world` (R90, 13-E): one committed install of a fresh world,
  with `<R>/usr/local/bin` root `0755` so `launcher` links the command
  there, shared read-only by the tests of one module (`install_once`).
- Positive controls (`check_controls`, spec 3.8): `whoami_as` equals the
  `accounts.toml` record, `df-build` writes in its home, and the control
  address (an HTTP server on the runner's external interface) answers
  `df-build` with `network=True`. A failure is `pytest.fail("инфраструктура:
  …")`, never a skip.
"""

from __future__ import annotations

import grp
import http.server
import itertools
import os
import pwd
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from df import cli, install, journal, launcher_entry, lockset, state
from df import platform as plat
from df.build import GIT_ISOLATION
from df.config import Account, InstallConfig, load_accounts
from df.journal import StepFailed, run_steps
from df.manifest import (
    ManifestError,
    Profile,
    RepoPin,
    fetch_manifest,
    parse_profile,
)
from df.paths import Layout
from tests.df.ci_step0 import BASES, make_base

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "proj"
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_TAR = Path("/usr/bin/tar")
#: The profile's exact Python (installed by uv 0.11.23 on both runners).
PYTHON = "3.12.13"
PROFILE_NAME = "p"
#: The pin's name: `install_admin` copies `<checkout of devtools>/df`.
PIN_NAME = "devtools"
MANIFEST_REPO = "fixture/manifest"
#: The umbrella manifest that pins profile `core` (ai-orchestrators-workspace#52,
#: merge commit on `main`): the one source of the `core` inventory and of the
#: live install.
CORE_MANIFEST = (
    "andrei-shtanakov/ai-orchestrators-workspace",
    "080f15e6b8fa3497a369f959e5466c1391bf2700",
)
CORE_PROFILE = "core"
FIXED_DATE = "2026-10-01T00:00:00+00:00"
INFRA = "инфраструктура"
ROLE_ENV: Mapping[str, str] = {"PATH": "/usr/bin:/bin", "LANG": "C"}
_SYSTEM_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C"}
_worlds = itertools.count(1)
#: Never part of the pin: bytecode, caches and editor leftovers (M7).
FIXTURE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".*.swp", "*~", ".DS_Store", ".pytest_cache", ".venv"
)


def infra(message: str) -> None:
    """Fail the test as an infrastructure failure (not a skip, not a PASS)."""
    pytest.fail(f"{INFRA}: {message}")


def run(argv: list[str], *, env: Mapping[str, str] | None = None) -> str:
    """Run a setup command as root; a failure is an infrastructure failure."""
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=dict(env if env is not None else _SYSTEM_ENV),
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        infra(f"{' '.join(argv)}: код {done.returncode}: {done.stderr.strip()}")
    return done.stdout


# ---------- the run directory and the fixture repo (session) ----------


@contextmanager
def acceptance_dir() -> Iterator[Path]:
    """`<base>/df-acc-<pid>`, root `0755` (R83); removed at the end."""
    base, created = make_base(BASES[plat.detect_platform()])
    problems = plat.trusted_path_problems(base)
    if problems:
        infra(f"база {base}: {problems}")
    top = base / f"df-acc-{os.getpid()}"
    os.mkdir(top, 0o755)
    os.chmod(top, 0o755)
    try:
        yield top
    finally:
        shutil.rmtree(top, ignore_errors=True)
        if created:
            shutil.rmtree(base, ignore_errors=True)


@dataclass(frozen=True)
class FixtureRepo:
    """The pinned repo: where the build fetches it and its commit."""

    url: str
    sha: str


def _git_env(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    return {
        **_SYSTEM_ENV,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_AUTHOR_NAME": "df-acceptance",
        "GIT_AUTHOR_EMAIL": "df@acceptance.invalid",
        "GIT_COMMITTER_NAME": "df-acceptance",
        "GIT_COMMITTER_EMAIL": "df@acceptance.invalid",
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_DATE": FIXED_DATE,
        **GIT_ISOLATION,
    }


def build_fixture_repo(
    top: Path, paths: Sequence[str] = ("df",), name: str = "fixture"
) -> tuple[Path, str]:
    """`<top>/<name>.git` (bare, root's) of the fixture project plus `paths`
    of this checkout (directories or files, relative to its root)."""
    src = top / f"{name}-src"
    shutil.copytree(FIXTURE_PROJECT, src, ignore=FIXTURE_IGNORE)
    for relative in paths:
        origin, target = REPO_ROOT / relative, src / relative
        if origin.is_dir():
            shutil.copytree(origin, target, ignore=FIXTURE_IGNORE)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    env = _git_env(top / "git-home")
    git = str(SYSTEM_GIT)
    run([git, "init", "-q", "--template=", str(src)], env=env)
    run([git, "-C", str(src), "add", "-A"], env=env)
    run([git, "-C", str(src), "commit", "-q", "-m", "fixture"], env=env)
    sha = run([git, "-C", str(src), "rev-parse", "HEAD"], env=env).strip()
    bare = top / f"{name}.git"
    run([git, "clone", "-q", "--bare", "--no-local", str(src), str(bare)], env=env)
    return bare, sha


@contextmanager
def serve_repo(top: Path, bare: Path) -> Iterator[str]:
    """The pin's URL: `file://` on Linux, a local `git daemon` on macOS."""
    if plat.detect_platform() == "linux-x86_64":
        yield f"file://{bare}"
        return
    port = _free_port()
    argv = [str(SYSTEM_GIT), "daemon", "--reuseaddr", "--export-all"]
    argv += [f"--base-path={top}", "--listen=127.0.0.1", f"--port={port}"]
    argv += ["--informative-errors", str(top)]
    proc = subprocess.Popen(
        argv,
        env=_git_env(top / "daemon-home"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("127.0.0.1", port, proc)
        yield f"git://127.0.0.1:{port}/{bare.name}"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            infra(f"git daemon завершился с кодом {proc.returncode}")
        try:
            socket.create_connection((host, port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    infra(f"git daemon не слушает {host}:{port}")


# ---------- the control address (session) ----------


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        body = b"df-control\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Quiet."""


def external_ip() -> str:
    """The runner's address on its outward interface (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        address = str(sock.getsockname()[0])
    if address.startswith("127."):
        infra(f"внешний адрес не найден: {address}")
    return address


@contextmanager
def control_server() -> Iterator[str]:
    """`http://<external ip>:<port>/`: the control address of spec 3.8."""
    server = http.server.ThreadingHTTPServer((external_ip(), 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host!s}:{port}/"
    finally:
        server.shutdown()
        server.server_close()


# ---------- one world ----------


@dataclass
class RootWorld:
    """A fresh `<R>` with the approved CLI and the fixture pin (no df yet)."""

    layout: Layout
    repo: FixtureRepo
    uv: Path
    top: Path
    control_url: str

    def cfg(self) -> InstallConfig:
        return InstallConfig(
            manifest_repo=MANIFEST_REPO,
            manifest_sha=self.repo.sha,
            profile=PROFILE_NAME,
            approved_cli={"git": SYSTEM_GIT, "tar": SYSTEM_TAR, "uv": self.uv},
        )

    def profile(self) -> Profile:
        pin = RepoPin(PIN_NAME, self.repo.url, self.repo.sha, ())
        return Profile(PROFILE_NAME, PYTHON, (pin,))

    def init_argv(self) -> list[str]:
        cli_args = [f"{k}={v}" for k, v in sorted(self.cfg().approved_cli.items())]
        return [
            "init",
            "--manifest-repo",
            MANIFEST_REPO,
            "--manifest-sha",
            self.repo.sha,
            "--profile",
            PROFILE_NAME,
            "--cli",
            *cli_args,
        ]

    def bootstrap(self, argv: list[str]) -> int:
        """`darkfactory` as bootstrap runs it, in process (R87)."""
        return cli.main(
            argv, mode="bootstrap", read=None, lock_fd=None, layout=self.layout
        )

    def entry(self, argv: list[str]) -> int:
        """`darkfactory <argv>` as the shipped entry's writer half runs it, in
        process: `acquire` (root, the lock, its own settle), `take_snapshot`,
        `typed_read`, then `main(mode="writer")` -- minus the import of the
        admin code (the pin is substituted in this process only)."""
        paths = launcher_entry.EntryPaths(str(self.layout.root))
        fd = launcher_entry.acquire(paths)
        try:
            snap = launcher_entry.take_snapshot(paths, "writer")
            read = launcher_entry.typed_read(state, snap)
            return cli.main(
                argv, mode="writer", read=read, lock_fd=fd, layout=self.layout
            )
        finally:
            os.close(fd)

    def command(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """The installed `<R>/opt/df/bin/darkfactory <argv>`, a real process
        of root (reader commands only: a writer would fetch the manifest)."""
        return subprocess.run(
            [str(self.layout.launcher), *argv],
            env=dict(ROLE_ENV),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    def prepare_build_user(self) -> Account:
        """Steps 1-3 of the install under the writer lock: the prep
        directories and a real `df-build` made by `create_build_user`."""
        prepared = install.Prepared()
        steps = install.install_steps(self.layout, self.cfg(), prepared)
        with journal.writer_lock(self.layout):
            run_steps(steps, self.layout.journal, run_id="root-world")
        return build_account(self.layout)

    def control_dir(self) -> Path:
        """R92: `<R>/observe-control`, `df-build`'s, with the file `f` it wrote."""
        path = self.layout.root / "observe-control"
        if path.is_dir():
            return path
        return self.builder_dir(path, "echo control > f")

    def builder_dir(self, path: Path, script: str) -> Path:
        """`path` (its parent root's, not writable by others: an observable
        source, I3) made by root, handed to `df-build`, filled by `script`
        run as `df-build` inside it."""
        account = build_account(self.layout)
        os.makedirs(path.parent, mode=0o755, exist_ok=True)
        os.mkdir(path, 0o755)
        os.chown(path, account.uid, account.gid)
        result = plat.run_as(
            plat.BUILD_USER,
            ["/bin/sh", "-c", script],
            env=ROLE_ENV,
            cwd=path,
            network=False,
            timeout=30,
        )
        if not result.launched or result.returncode != 0:
            infra(f"{path} от {plat.BUILD_USER}: {result}")
        return path

    def workdir(self) -> Path:
        """A root `0700` directory outside every source (observer scratch)."""
        path = self.top / f"observe-{self.layout.root.name}"
        path.mkdir(mode=0o700, exist_ok=True)
        return path


@dataclass(frozen=True)
class InstalledWorld:
    """`installed_world` (R90): a world after one committed install."""

    world: RootWorld
    release_id: str

    @property
    def layout(self) -> Layout:
        return self.world.layout

    @property
    def release(self) -> Path:
        return self.layout.release_dir(self.release_id)


def install_once(world: RootWorld, argv: list[str] | None = None) -> InstalledWorld:
    """`<R>/usr/local/bin` root `0755` (trusted, so `launcher` links the
    command there), then `bootstrap init` (`argv`, else the fixture pin's);
    a refusal fails the module."""
    local_bin = world.layout.local_bin
    for path in (local_bin.parent.parent, local_bin.parent, local_bin):
        os.mkdir(path, 0o755)
        os.chmod(path, 0o755)
    code = world.bootstrap(world.init_argv() if argv is None else argv)
    if code != cli.EXIT_OK:
        pytest.fail(f"установка installed_world: код {code}")
    assert_committed(world.layout)
    rid = current_id(world.layout)
    assert rid is not None
    return InstalledWorld(world, rid)


def core_profile() -> Profile:
    """Profile `core` of `CORE_MANIFEST`, fetched and resolved by df's own
    `fetch_manifest` and `parse_profile` (network). A network error or a
    refusal fails the test: an inventory is never recorded without inputs."""
    repo, sha = CORE_MANIFEST
    try:
        profile = parse_profile(fetch_manifest(repo, sha), CORE_PROFILE)
    except (ManifestError, OSError) as exc:
        pytest.fail(f"манифест {repo}@{sha}: {type(exc).__name__}: {exc}")
    if profile.python != PYTHON or not profile.repos:
        pytest.fail(f"манифест {repo}@{sha}: {profile}, нужен Python {PYTHON}")
    return profile


def current_id(layout: Layout) -> str | None:
    """The id `current` names, or `None`."""
    if not os.path.lexists(layout.current):
        return None
    return os.readlink(layout.current).removeprefix("releases/")


def write_evidence(out: Path | None, relative: str, text: str) -> None:
    """Keep a measured fact under the CI evidence directory `out` (none
    locally)."""
    if out is None:
        return
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build_account(layout: Layout) -> Account:
    account = load_accounts(layout.accounts).get(plat.BUILD_USER)
    if account is None or account.state != "ready":
        infra(f"нет готовой записи {plat.BUILD_USER}: {account}")
    assert account is not None
    return account


def make_world(top: Path, repo: FixtureRepo, control_url: str) -> RootWorld:
    """A fresh `<R>` (R83) with the root uv copy (R88); `df-build` absent."""
    leftover = build_user_records()
    if leftover:
        infra(f"{plat.BUILD_USER} остался от прошлого теста: {leftover}")
    root = top / f"r{next(_worlds)}"
    os.mkdir(root, 0o755)
    os.chmod(root, 0o755)
    layout = Layout(Path(os.path.realpath(root)))
    # The host's /Users (macOS) or /home (Linux): root 0755, as on a host.
    os.mkdir(layout.build_home.parent, 0o755)
    os.chmod(layout.build_home.parent, 0o755)
    return RootWorld(layout, repo, _uv_copy(layout), top, control_url)


def _uv_copy(layout: Layout) -> Path:
    found = shutil.which("uv")
    if found is None:
        infra("uv не найден на PATH")
    assert found is not None
    cli_dir = layout.root / "cli"
    copy = cli_dir / "uv"
    run(["/usr/bin/install", "-d", "-o", "root", "-m", "0755", str(cli_dir)])
    run(["/usr/bin/install", "-o", "root", "-m", "0755", found, str(copy)])
    version = run([str(copy), "--version"]).split()
    if version[1:2] != [lockset.UV_PINNED_VERSION]:
        infra(f"uv {version}: нужен {lockset.UV_PINNED_VERSION}")
    return copy


def build_user_records() -> dict[str, dict[str, str]]:
    """The OS records of `df-build` that exist (user/group)."""
    found: dict[str, dict[str, str]] = {}
    for kind in ("user", "group"):
        try:
            record = plat.read_record(kind, plat.BUILD_USER)
        except StepFailed as exc:
            infra(f"запись {kind} {plat.BUILD_USER} не читается: {exc}")
            continue
        if record is not None:
            found[kind] = record
    return found


def teardown_world(world: RootWorld, keep: Path | None) -> None:
    """R90: processes, user and group of `df-build` gone, `<R>` removed;
    the journal is kept in `keep` (evidence) first."""
    journal_file = world.layout.journal
    if keep is not None and journal_file.exists():
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(journal_file, keep)
    if _uid_of(plat.BUILD_USER) == 0:
        # `pkill -u df-build` would kill every root process (this run too):
        # a UID-0 df-build left by a test is removed unreaped and fails it.
        remove_build_user(force=True)
        shutil.rmtree(world.layout.root, ignore_errors=True)
        infra(f"{plat.BUILD_USER} с UID 0 остался после теста")
    plat.reap_user_processes(plat.BUILD_USER)
    remove_build_user()
    shutil.rmtree(world.layout.root, ignore_errors=True)
    if world.layout.root.exists():
        infra(f"{world.layout.root} не удалён")
    leftover = build_user_records()
    if leftover:
        infra(f"{plat.BUILD_USER} не удалён: {leftover}")


def remove_build_user(*, force: bool = False) -> None:
    """Delete the user and the group (absent ones are fine); `force` is
    `userdel -f` (Linux), which a UID-0 user needs: root runs processes."""
    remove_service_user(plat.BUILD_USER, force=force)


def remove_service_user(name: str, *, force: bool = False) -> None:
    """`remove_build_user` for any service account `name` (stage-2 roles)."""
    if plat.detect_platform() == "macos-arm64":
        argvs = [
            ["/usr/bin/dscl", ".", "-delete", f"/Users/{name}"],
            ["/usr/bin/dscl", ".", "-delete", f"/Groups/{name}"],
            ["/usr/bin/dscacheutil", "-flushcache"],
        ]
    else:
        userdel = ["/usr/sbin/userdel", *(["-f"] if force else []), name]
        argvs = [userdel, ["/usr/sbin/groupdel", name]]
    for argv in argvs:
        subprocess.run(
            argv, capture_output=True, env=_SYSTEM_ENV, timeout=60, check=False
        )
    _wait_forgotten(name)


#: libinfo's in-process cache listens to these (macOS); `dscacheutil
#: -flushcache` alone left a deleted user visible (CI run 40191989552).
_DS_INVALIDATE = (
    "com.apple.system.DirectoryService.InvalidateCache",
    "com.apple.system.DirectoryService.InvalidateCache.user",
    "com.apple.system.DirectoryService.InvalidateCache.group",
)


def _wait_forgotten(name: str) -> None:
    """Until the libc lookups forget `name` (the macOS directory cache kept a
    deleted user's home for the next world, CI run 40191801437)."""
    deadline = time.monotonic() + 30
    while _known(name):
        if time.monotonic() > deadline:
            infra(f"{name}: удалённая учётка всё ещё видна: {_lookup_facts(name)}")
        if plat.detect_platform() == "macos-arm64":
            _flush_ds_cache()
        time.sleep(0.5)


def _flush_ds_cache() -> None:
    argvs = [["/usr/bin/dscacheutil", "-flushcache"]]
    argvs += [["/usr/bin/notifyutil", "-p", key] for key in _DS_INVALIDATE]
    for argv in argvs:
        subprocess.run(argv, capture_output=True, timeout=60, check=False)


def _lookup_facts(name: str) -> dict[str, str]:
    """Who still sees `name`: this process, a new process, dscl, the cache."""
    facts: dict[str, str] = {}
    try:
        facts["pwd (в процессе)"] = repr(pwd.getpwnam(name))
    except KeyError:
        facts["pwd (в процессе)"] = "нет"
    probes = {
        "id (новый процесс)": ["/usr/bin/id", name],
        "dscl -read": ["/usr/bin/dscl", ".", "-read", f"/Users/{name}", "UniqueID"],
        "dscacheutil -q": ["/usr/bin/dscacheutil", "-q", "user", "-a", "name", name],
    }
    for label, argv in probes.items():
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        facts[label] = f"rc={done.returncode} {done.stdout.strip()[:200]}"
    return facts


def _uid_of(name: str) -> int | None:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def assert_committed(layout: Layout, *, since: int = 0) -> None:
    """No `pending.toml`, no failed step in the journal from row `since` on
    (a run after a deliberately refused one), and the last `done` of the run
    ended in `committed`."""
    assert not os.path.lexists(layout.pending)
    rows = journal.read_journal(layout.journal)[since:]
    assert [r for r in rows if r["event"] == "failed"] == []
    done = [r for r in rows if r["event"] == "done"]
    assert done and str(done[-1]["detail"]).endswith("→ committed"), done[-1:]


def _known(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        pass
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


# ---------- the positive controls ----------


@dataclass(frozen=True)
class Controls:
    """The controls passed: `df-build`'s record and the control address."""

    account: Account
    url: str


def check_controls(world: RootWorld) -> Controls:
    """Spec 3.8 positive controls, before any negative test (module doc)."""
    account = build_account(world.layout)
    try:
        ids = plat.whoami_as(plat.BUILD_USER)
    except StepFailed as exc:
        infra(f"whoami_as({plat.BUILD_USER}): {exc}")
        raise
    if ids != (account.uid, account.gid):
        infra(f"whoami_as {ids}, в accounts.toml {(account.uid, account.gid)}")
    _control_write(world.layout, account)
    _control_network(world.control_url)
    return Controls(account, world.control_url)


def _as_build(argv: list[str], cwd: Path, *, network: bool) -> plat.RunResult:
    return plat.run_as(
        plat.BUILD_USER, argv, env=ROLE_ENV, cwd=cwd, network=network, timeout=30
    )


def _control_write(layout: Layout, account: Account) -> None:
    home = layout.build_home
    probe = home / ".df-control-write"
    result = _as_build(["/usr/bin/touch", str(probe)], home, network=False)
    if not result.launched or result.returncode != 0:
        infra(f"запись в {home} от {plat.BUILD_USER}: {result}")
    try:
        owner = os.lstat(probe).st_uid
    except OSError as exc:
        infra(f"запись в {home} от {plat.BUILD_USER} не видна: {exc}")
        raise
    if owner != account.uid:
        infra(f"{probe}: владелец {owner}, нужен {account.uid}")
    removed = _as_build(["/bin/rm", "-f", str(probe)], home, network=False)
    if not removed.launched or removed.returncode != 0:
        infra(f"{probe}: не удалён от {plat.BUILD_USER}: {removed}")


def _control_network(url: str) -> None:
    argv = ["/usr/bin/curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}"]
    argv += ["--max-time", "10", url]
    result = _as_build(argv, Path("/"), network=True)
    code = result.stdout.decode("ascii", "replace").strip()
    if not result.launched or result.returncode != 0 or code != "200":
        infra(f"контрольный адрес {url} от {plat.BUILD_USER}: {code!r}, {result}")
