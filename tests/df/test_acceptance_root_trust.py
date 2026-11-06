"""Root acceptance of stage 3a: the trust rows of spec 10 that run in CI only
(Task 14, job `stage2a` of `df-stage1`, both OSes).

The world of `tests/df/roles_world.py` (the production layout, the roles,
the real `sudo` rules, the local fake GitHub of the product) and the
product repository of `tests/df/precheck_world.py`, plus the two trust
sources of `tests/df/trust_sources.py` -- real git repositories whose REST
answers the runner serves itself on `https://api.github.com/` (the host
every trust read of df is fixed to; `roles_world.impersonate_api`, a host
adaptation of the disposable runner with its own positive control).

The rows run in file order on one world: first the stop with neither an
approved `gh` nor a binding, then `setup-trust` binds the sources and the
rows of the bound root follow. No credential is real: the PAT and the
history token are fixture strings.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from df import doctor, perimeter_client, sudoers
from df import platform as plat
from df.config import load_install
from df.publisher import request
from df.publisher.request import TrustStatus
from tests.df.conftest import stage1_out
from tests.df.precheck_world import PrecheckWorld, precheck_world, processes
from tests.df.roles_world import (
    BASE_ENV,
    FIXTURE_FROM_CHECKOUT,
    IOLOG_FILE,
    OPERATOR,
    PRODUCT_ID,
    PUBLISHER,
    SUDO,
    UV_DIR,
    RolesWorld,
    admin_python,
    as_operator,
    evidence,
    found_in,
    impersonate_api,
    install_sudoers_file,
    io_sessions,
    real_site_pins,
    roles_world,
    shown,
)
from tests.df.root_world import (
    FixtureRepo,
    build_fixture_repo,
    infra,
    serve_repo,
)
from tests.df.test_root_gh_shared_profile import _install_gh
from tests.df.trust_sources import (
    HISTORY_TOKEN,
    TrustSources,
    build_sources,
)

#: `network`: the pinned official `gh` is downloaded (`_install_gh`).
pytestmark = [pytest.mark.root, pytest.mark.stage2a, pytest.mark.network]

PRECHECK = "df-precheck"
#: A fixture string of a fine-grained PAT's form; not a credential.
PAT = "github_pat_dfFixturePerimeterPatNotASecret000"
DONE_TEXT = "установка доверия завершена"


# ---------- the world ----------


@pytest.fixture(scope="module")
def trust_repo(acceptance_top: Path) -> Iterator[FixtureRepo]:
    """The fixture repo with `FIXTURE_FROM_CHECKOUT` and the real `site/`."""
    bare, sha = build_fixture_repo(
        acceptance_top,
        FIXTURE_FROM_CHECKOUT,
        name="trust-fixture",
        site=real_site_pins(),
    )
    with serve_repo(acceptance_top, bare) as url:
        yield FixtureRepo(url, sha)


@pytest.fixture(scope="module")
def rw(
    acceptance_top: Path, trust_repo: FixtureRepo, control_url: str
) -> Iterator[RolesWorld]:
    """The module's world (`roles_world`, work directory `trust`)."""
    out = stage1_out()
    keep = None if out is None else out / "journals" / "trust-installed.jsonl"
    with roles_world(acceptance_top, trust_repo, control_url, keep, name="trust") as w:
        yield w
        evidence("trust-publisher-journal.json", w.journal_lines())


@pytest.fixture(scope="module")
def pw(rw: RolesWorld) -> Iterator[PrecheckWorld]:
    """The product repository and its stop PRs (`precheck_world`)."""
    with precheck_world(rw) as world:
        yield world


@pytest.fixture(scope="module")
def sources(rw: RolesWorld) -> TrustSources:
    """`df-trust` and `approval-policy` as git repositories."""
    return build_sources(rw.work / "trust-sources")


@pytest.fixture(scope="module")
def api(rw: RolesWorld, sources: TrustSources) -> Iterator[None]:
    """`https://api.github.com/` answered by `sources` on this runner."""
    with impersonate_api(sources.rest, rw.work, admin_python(rw.layout)):
        yield


@pytest.fixture(scope="module")
def bound(rw: RolesWorld, api: None, sources: TrustSources) -> Iterator[Path]:
    """The pinned `gh` approved, then `setup-trust check` (the operator,
    the PAT on stdin) and `setup-trust write` (root): the profile, the
    binding and the root's own `trust-status`. The operator's report."""
    gh = UV_DIR / "gh"
    shutil.copyfile(_install_gh(rw.work), gh)
    os.chmod(gh, 0o755)
    check_ok(rw.darkfactory(["cli", "set", "gh", str(gh)]), "cli set gh")
    report = Path(os.path.expanduser(f"~{OPERATOR}")) / "df-acc-trust-report.json"
    try:
        lines = setup_trust(rw, sources, report)
        evidence("setup-trust.json", lines)
        yield report
    finally:
        report.unlink(missing_ok=True)


def check_ok(done: object, what: str) -> None:
    """A step of the world must succeed."""
    code = getattr(done, "returncode", None)
    if code != 0:
        infra(f"{what}: {shown(done)}")  # type: ignore[arg-type]


def setup_trust(
    rw: RolesWorld, sources: TrustSources, report: Path, *, rebind: bool = False
) -> dict[str, str]:
    """`setup-trust check` as the operator, then `sudo … setup-trust write`
    of its report with the history token file; their outputs."""
    ids = ["--policy-repo-id", str(sources.policy.id)]
    ids += ["--trust-repo-id", str(sources.trust.id), "--trust-branch", "main"]
    flag = ["--rebind"] if rebind else []
    check = [str(rw.layout.launcher), "setup-trust", "check", *ids, *flag]
    done = as_operator([*check, "--out", str(report)], stdin=(PAT + "\n").encode())
    out = done.stdout.decode("utf-8", "replace")
    check_ok(done, "setup-trust check")
    digest = next(
        (line.rsplit(" ", 1)[1] for line in out.splitlines() if ", digest " in line),
        None,
    )
    if digest is None:
        infra(f"setup-trust check: нет digest: {shown(done)}")
    token = rw.work / f"history-token-{sources.trust.id}"
    token.write_text(HISTORY_TOKEN + "\n")
    os.chmod(token, 0o600)
    write = ["setup-trust", "write", *ids, *flag, "--report", str(report)]
    write += ["--report-digest", str(digest), "--history-token-file", str(token)]
    try:
        written = rw.darkfactory(write)
    finally:
        token.unlink(missing_ok=True)
    text = written.stdout.decode("utf-8", "replace")
    if written.returncode != 0 or DONE_TEXT not in text:
        pytest.fail(f"setup-trust write: {shown(written)}")
    return {"check": out[-4000:], "write": text}


def trust_status(rw: RolesWorld, sources: TrustSources) -> TrustStatus:
    """Root's `trust-status` through the chain (`sudo -u df-publisher`)."""
    from df import setup_trust as st

    raw = st.trust_status_call(rw.layout)
    root = ("github.com", sources.trust.id, "main")
    try:
        return request.parse_trust_status(raw.decode().strip(), root)
    except request.BadResponse as exc:
        pytest.fail(f"trust-status: {exc}: {raw[-800:]!r}")
        raise


# ---------- before the binding ----------


def test_root_trust_stop_without_gh_or_binding(
    rw: RolesWorld, pw: PrecheckWorld
) -> None:
    """An install with neither an approved `gh` nor a binding (2b's): the
    positive stop through the whole chain is served (`allow`, protocol v1)
    and `perimeter-status` is `unavailable: no_binding`."""
    install = load_install(rw.layout.etc / "install.toml")
    assert "gh" not in install.approved_cli
    assert not (rw.layout.etc / "trust.toml").exists()
    allow = pw.ask(pw.current)
    status = perimeter_client.payload(
        rw.allowed_run,
        perimeter_client.RootId("github.com", PRODUCT_ID, "main"),
        perimeter_client.TrustView("0" * 40, 0),
        "acc-trust-nobinding-1",
    )
    done = rw.publish("df-exec", "perimeter-status", status)
    resp = request.parse_response(done.stdout.decode().strip())
    evidence("trust-stop-unbound.json", {"stop": allow, "status": resp.result})
    assert allow.get("refused", "?") is None, allow
    assert allow["version"] == "df-precheck/1", allow
    assert (resp.result["state"], resp.result["reason"]) == (
        "unavailable",
        "no_binding",
    )


# ---------- the binding ----------


def test_root_trust_setup_binds(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """`setup-trust` bound the sources: `trust.toml` names them and the
    profile, `/etc/df/history-gh` is `root:df-history 0750` with files
    `0640`, and the root is `clear` on the head of `df-trust`."""
    import grp

    from df.roles import HISTORY_GROUP
    from df.stage2_config import load_trust

    binding = load_trust(rw.layout.etc / "trust.toml")
    assert binding is not None
    assert binding.root_id == ("github.com", sources.trust.id, "main")
    assert binding.history_gh_config_dir == str(rw.layout.history_gh)
    gid = grp.getgrnam(HISTORY_GROUP).gr_gid
    profile = rw.layout.history_gh
    st = profile.stat()
    assert (st.st_uid, st.st_gid, st.st_mode & 0o7777) == (0, gid, 0o750)
    for path in profile.iterdir():
        st = path.stat()
        assert (st.st_uid, st.st_gid, st.st_mode & 0o7777) == (0, gid, 0o640)
    status = trust_status(rw, sources)
    assert (status.state, status.snapshot_sha) == ("clear", sources.trust.head())


# ---------- op: trust through the chain ----------


def precheck_home(rw: RolesWorld) -> Path:
    return rw.layout.role_home(PRECHECK)


def listing(home: Path) -> list[str]:
    """Every path under `home`, relative (root lists them)."""
    return sorted(str(p.relative_to(home)) for p in home.rglob("*"))


#: What may stay in `~df-precheck` after a call: its lock file (2b) and
#: the empty snapshot directory; nothing durable of a decision.
PRECHECK_LEFT = {"precheck.lock", "precheck-snapshots"}


def test_root_trust_op_trust_stateless(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """`op: trust` through publisher → sudo → entry → `act_precheck` v2
    (the checker reads `df-trust` and the policy itself: their paths reach
    the runner's `api.github.com`): after each call `~df-precheck` holds no
    durable state, and removing everything in it between two calls does
    not change the outcome."""
    home = precheck_home(rw)
    before = len(sources.rest.calls)
    first = trust_status(rw, sources)
    left = listing(home)
    for entry in home.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    middle = len(sources.rest.calls)
    second = trust_status(rw, sources)
    evidence(
        "trust-op-stateless.json",
        {
            "first": dataclasses.asdict(first),
            "second": dataclasses.asdict(second),
            "left_after_first": left,
            "after_second": listing(home),
            "reads": sources.rest.paths()[before:],
        },
    )
    assert first == second, (first, second)
    assert first.state == "clear"
    assert set(left) <= PRECHECK_LEFT, left
    assert set(listing(home)) <= PRECHECK_LEFT
    # the checker read the sources in both calls (epochs journal by SHA)
    reads = sources.rest.paths()
    assert any("epochs.yaml" in p for p in reads[before:middle])
    assert any("epochs.yaml" in p for p in reads[middle:])


# ---------- the history profile ----------

#: `errno` of opening `argv[1]` for reading (0: read).
READ_PROBE = """
import os, sys
try:
    os.close(os.open(sys.argv[1], os.O_RDONLY))
except OSError as exc:
    print(exc.errno)
else:
    print(0)
"""


def read_as(rw: RolesWorld, user: str, path: Path) -> int:
    """`errno` of `user` opening `path`, with the groups the host gives it
    (`sudo -u`, `initgroups`: the supplementary `df-history` included)."""
    python = str(admin_python(rw.layout))
    argv = [SUDO, "-n", "-u", user, python, "-I", "-S", "-c", READ_PROBE, str(path)]
    done = plat.run_command(argv, env=BASE_ENV)
    if done.returncode != 0:
        infra(f"проба чтения от {user}: {done.returncode} {done.stderr!r}")
    return int(done.stdout.decode().strip())


def test_root_trust_history_profile(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """The members of `df-history` (publisher, checker, operator) read the
    profile; another account does not (the OS refuses, `EACCES`)."""
    hosts = rw.layout.history_gh / "hosts.yml"
    members = (PUBLISHER, PRECHECK, OPERATOR)
    outsiders = ("df-exec", "df-review", "nobody")
    got = {user: read_as(rw, user, hosts) for user in (*members, *outsiders)}
    evidence("trust-history-profile.json", got)
    assert got == {
        **dict.fromkeys(members, 0),
        **dict.fromkeys(outsiders, errno.EACCES),
    }


class Sampler:
    """Every command line `ps` shows, sampled until stopped (the argv row)."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.seen.update(p.command for p in processes())
            time.sleep(0.02)

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=30)


def tree_texts(root: Path) -> dict[str, bytes]:
    """Every regular file under `root` (root reads them)."""
    if not root.exists():
        return {}
    return {
        str(p): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def test_root_trust_history_token_not_exposed(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """With sudo's I/O log on for every command, a `trust-status` (the
    publisher's and the checker's history reads): the history token is in
    no argv `ps` showed, in no stream or record of the I/O log, and in no
    evidence (the installer's, the publisher's journal, this run's).
    Controls: the token did go to the sources (`Authorization`), the
    sampler saw the publisher's command, the log holds the request."""
    iolog = rw.work / "iolog-trust"
    install_sudoers_file(
        IOLOG_FILE, f"Defaults log_input, log_output, iolog_dir={iolog}\n"
    )
    rid = f"acc-trust-iolog-{os.getpid()}"
    data = json.dumps({"schema": request.REQUEST_SCHEMA, "request_id": rid})
    before = len(sources.rest.calls)
    try:
        with Sampler() as sampler:
            raw = perimeter_client.sudo_call(
                data.encode(), rw.layout, operation=request.TRUST_STATUS, timeout=720
            )
    finally:
        os.unlink(IOLOG_FILE)
    sessions = io_sessions(iolog)
    needle = HISTORY_TOKEN.encode()
    in_argv = sorted(c for c in sampler.seen if HISTORY_TOKEN in c)
    in_log = [
        str(s.path)
        for s in sessions
        if needle in s.meta.encode() or any(needle in b for b in s.streams.values())
    ]
    places = {
        "var": rw.layout.var,
        "evidence_out": stage1_out() or Path("/nonexistent"),
    }
    in_evidence = [
        path
        for root in places.values()
        for path, text in tree_texts(root).items()
        if needle in text and not path.startswith(str(rw.layout.history_gh))
    ]
    used = [p for p, auth in sources.rest.calls[before:] if HISTORY_TOKEN in auth]
    publishes = [s for s in sessions if "df-publish" in s.meta]
    evidence(
        "trust-token-exposure.json",
        {
            "answer": raw.decode("utf-8", "replace")[-1000:],
            "argv_with_token": len(in_argv),
            "commands_seen": len(sampler.seen),
            "sessions": [str(s.path) for s in sessions],
            "log_with_token": in_log,
            "evidence_with_token": in_evidence,
            "reads_with_token": len(used),
        },
    )
    assert used, "контроль: токен истории не ушёл к источникам"
    assert any("df-publish" in c for c in sampler.seen), (
        "контроль: ps не видел издателя"
    )
    assert found_in(publishes, rid, ("stdin",)), "контроль: запроса нет в I/O-журнале"
    assert in_argv == []
    assert in_log == []
    assert in_evidence == []
    assert sudoers.io_log_problems(rw.layout) == []


# ---------- the operator's line ----------


def test_root_trust_operator_line(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """`sudo darkfactory doctor --product 1 --json` with the root bound:
    the operator's line is two results -- (i) the file and `sudo -l -U
    runner` give exactly the df rules: `ok`; (ii) «не проверено, принято
    исключение» (spec 4.5 (ii), O7), never `ok`. Level 3 is shown apart."""
    done = rw.darkfactory(["doctor", "--product", str(PRODUCT_ID), "--json"])
    evidence("trust-doctor.json", done.stdout.decode("utf-8", "replace"))
    try:
        report = json.loads(done.stdout)
    except ValueError:
        pytest.fail(f"doctor: не JSON; {shown(done)}")
    rights = [(c["id"], c["status"], c["reason"]) for c in report["operator_rights"]]
    assert rights[0][:2] == (doctor.OPERATOR_RIGHTS_ID, doctor.OK), rights
    assert rights[1] == (
        doctor.OPERATOR_ISOLATION_ID,
        doctor.NOT_CHECKED,
        sudoers.OPERATOR_EXCEPTION,
    ), rights
    assert [c["id"] for c in report["level3_checks"]] == list(doctor.LEVEL3_IDS)
