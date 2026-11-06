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
import grp
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import doctor, perimeter_client, sudoers
from df import install as install_mod
from df import platform as plat
from df.admin_tree import locked_pins
from df.config import load_install
from df.paths import Layout
from df.publisher import perimeter, request
from df.publisher.precheck import command as precheck_command
from df.publisher.request import TrustStatus
from df.roles import HISTORY_GROUP
from df.setup_trust import EPOCHS_EXIT, explain
from df.stage2_config import dump_publisher, parse_publisher
from tests.df.candidate_world import Candidate, build_candidate, build_finalize
from tests.df.conftest import stage1_out
from tests.df.precheck_world import (
    PrecheckWorld,
    TokenGitServer,
    precheck_world,
    processes,
    spawn_as,
)
from tests.df.roles_world import (
    BASE_ENV,
    FIXTURE_FROM_CHECKOUT,
    FULL_NAME,
    IOLOG_FILE,
    OPERATOR,
    PRODUCT_ID,
    PUBLISHER,
    SUDO,
    UV_DIR,
    IoSession,
    RolesWorld,
    admin_python,
    as_operator,
    as_user,
    evidence,
    found_in,
    impersonate_api,
    install_sudoers_file,
    io_sessions,
    new_request_id,
    real_site_pins,
    response_of,
    roles_world,
    shown,
    write_etc,
)
from tests.df.root_world import (
    FixtureRepo,
    InstalledWorld,
    RootWorld,
    build_fixture_repo,
    current_id,
    infra,
    serve_repo,
)
from tests.df.test_root_gh_shared_profile import _install_gh
from tests.df.trust_sources import (
    CHECKER_AGENT,
    HISTORY_TOKEN,
    PUBLISHER_AGENT,
    TrustSources,
    build_sources,
    new_trust_repo,
)
from tests.df.upgrade_world import (
    CODE_2B,
    candidate_v2_request,
    checkout_2b,
    install_2b,
    paths_2b,
    run_2b,
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
    of its report with the history token file; their outputs.

    The real-entry leg of the impersonation's positive control (review
    I2): each call must have reached the runner's `api.github.com` -- the
    check (the operator's `darkfactory`) and the write's `trust-status`
    (publisher → sudo → `df-precheck` → its entry, both readers). No
    request at all from a call is infrastructure; any other outcome is the
    row's own."""
    ids = ["--policy-repo-id", str(sources.policy.id)]
    ids += ["--trust-repo-id", str(sources.trust.id), "--trust-branch", "main"]
    flag = ["--rebind"] if rebind else []
    check = [str(rw.layout.launcher), "setup-trust", "check", *ids, *flag]
    start = len(sources.rest.calls)
    done = as_operator([*check, "--out", str(report)], stdin=(PAT + "\n").encode())
    out = done.stdout.decode("utf-8", "replace")
    if not sources.rest.reads(start, PUBLISHER_AGENT):
        infra(f"setup-trust check не дошёл до api.github.com раннера: {shown(done)}")
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
    start = len(sources.rest.calls)
    try:
        written = rw.darkfactory(write)
    finally:
        token.unlink(missing_ok=True)
    for agent in (PUBLISHER_AGENT, CHECKER_AGENT):
        if not sources.rest.reads(start, agent):
            infra(
                f"trust-status записи не дошёл до api.github.com раннера ({agent}): "
                f"{shown(written)}"
            )
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


# ---------- the 2b -> 3a upgrade (owner decision 7 (a)) ----------


def db_rows(db: Path, sql: str) -> list[tuple[object, ...]]:
    """Rows of `sql`, read by root without writing."""
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def schema_of(db: Path) -> str:
    return str(db_rows(db, "SELECT value FROM meta WHERE key = 'schema'")[0][0])


def ask_v2(uw: RolesWorld) -> dict[str, object]:
    """The candidate's v2 request through `sudo -u df-precheck` as the
    publisher calls it (`precheck.command`), as `df-publisher`."""
    data = candidate_v2_request(uw.layout, FULL_NAME, PRODUCT_ID)
    proc = spawn_as(
        PUBLISHER,
        list(precheck_command(uw.layout)),
        env=BASE_ENV,
        stdin=subprocess.PIPE,
    )
    out, err = proc.communicate(data, timeout=300)
    try:
        found = json.loads(out)
    except ValueError:
        infra(f"df-precheck: {proc.returncode} {out[-400:]!r} {err[-400:]!r}")
        raise
    return found


#: The schema-1 event `df-publisher` inserts into the inadmissible copy.
EVENT_V1 = (
    "INSERT INTO events (root_host, root_repository_id, root_branch, "
    "request_key, request_id, run_id, sender, violated_role, "
    "violated_repository_id, detected_by, observed_at, registered_at, state, "
    "epoch, provenance, detail) VALUES ('github.com', 5, 'main', 'acc-upgrade', "
    "NULL, NULL, NULL, NULL, NULL, 'df-trust', '2026-11-06T00:00:00Z', "
    "'2026-11-06T00:00:00Z', 'pending', NULL, "
    '\'{"snapshot_sha": null, "unavailable": "fixture"}\', \'fixture\')'
)


def insert_event_as_publisher(uw: RolesWorld, db: Path) -> None:
    """One event written by `df-publisher` itself into its schema-1 base."""
    python = str(admin_python(uw.layout))
    script = (
        "import sqlite3, sys\nc = sqlite3.connect(sys.argv[1])\n"
        "c.execute(sys.argv[2])\nc.commit()\nc.close()\n"
    )
    done = as_user(PUBLISHER, [python, "-I", "-S", "-c", script, str(db), EVENT_V1])
    if done.returncode != 0:
        infra(f"событие от df-publisher: {shown(done)}")


@dataclass(frozen=True)
class UpgradePins:
    """The 2b checkout and the served pins of the upgrade rows: the 2b code,
    this branch, and this branch once more (another commit)."""

    top: Path
    src: Path
    pin_2b: FixtureRepo
    pin_3a: FixtureRepo
    pin_3a_again: FixtureRepo


@pytest.fixture(scope="module")
def upgrade_pins(acceptance_top: Path) -> Iterator[UpgradePins]:
    top = acceptance_top / "upgrade"
    top.mkdir(mode=0o755)
    src = checkout_2b(top)
    site_2b = locked_pins((src / "uv.lock").read_bytes())
    built = [
        build_fixture_repo(
            top, paths_2b(src), name="fixture-2b", site=site_2b, source=src
        ),
        build_fixture_repo(
            top, FIXTURE_FROM_CHECKOUT, name="fixture-3a", site=real_site_pins()
        ),
        build_fixture_repo(
            top,
            FIXTURE_FROM_CHECKOUT,
            name="fixture-3a-again",
            site=real_site_pins(),
            extra={"UPGRADE.md": "the second 3a install\n"},
        ),
    ]
    with ExitStack() as stack:
        pins = [
            FixtureRepo(stack.enter_context(serve_repo(top, bare)), sha)
            for bare, sha in built
        ]
        yield UpgradePins(top, src, *pins)


@contextmanager
def world_2b(
    up: UpgradePins, control_url: str, name: str
) -> Iterator[tuple[RolesWorld, str]]:
    """A world installed by the 2b code (its roles, `setup-agent`, the
    fake GitHub), then the 2b installer's install of this branch's pin;
    the world (following `current`) and the first release id."""
    with roles_world(
        up.top, up.pin_2b, control_url, None, name=name, install=install_2b(up.src)
    ) as uw:
        first = uw.installed.release_id
        yield uw, first


def upgrade_to_3a_pin(uw: RolesWorld, up: UpgradePins, first: str) -> dict[str, object]:
    """The 2b installer installs this branch's pin; the world follows it."""
    pin = up.pin_3a
    code = run_2b(up.src, uw.installed.world, ["install"], pin.url, pin.sha)
    upgraded = current_id(uw.layout)
    if code != 0 or upgraded in (None, first):
        pytest.fail(f"установщик 2b не поставил пин этой ветки: {code} {upgraded}")
    assert upgraded is not None
    uw.installed = InstalledWorld(uw.installed.world, upgraded)
    return {"code": code, "id": upgraded}


def history_group() -> grp.struct_group | None:
    try:
        return grp.getgrnam(HISTORY_GROUP)
    except KeyError:
        return None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_state(lay: Layout, db: Path) -> dict[str, object]:
    """What `roles setup` writes, for the idempotence check (review I4)."""
    group = history_group()
    return {
        "events.sqlite": digest(db),
        "groups.toml": lay.groups.read_text(),
        "sudoers": digest(sudoers.SUDOERS_FILE),
        "publisher.toml": (lay.etc / "publisher.toml").read_text(),
        "libexec": {p.name: digest(p) for p in sorted(lay.libexec.iterdir())},
        "df-history": None
        if group is None
        else {"gid": group.gr_gid, "members": sorted(group.gr_mem)},
    }


def journal_tail(lay: Layout, start: int) -> list[dict[str, object]]:
    """The installer journal's lines from line `start` on."""
    lines = lay.journal.read_text().splitlines()[start:]
    return [json.loads(line) for line in lines if line.strip()]


def journal_len(lay: Layout) -> int:
    return len(lay.journal.read_text().splitlines())


def test_root_trust_upgrade_2b_to_3a(
    upgrade_pins: UpgradePins, control_url: str
) -> None:
    """Owner decision 7 (a): an install by the 2b code (`291cb30`), its
    roles, an admissible schema-"1" `events.sqlite` (no events, no
    `trust.toml`), no `df-history` -> the 2b installer installs a pin of
    this branch: the stop is `allow`, the candidate is `tree_incomplete`
    before import, still no `df-history` -> `roles setup` by the 3a code:
    the schema moves to "2", `transitions` and `admission` kept,
    `df-history` made -> again: idempotent (what roles setup writes is
    unchanged, every step settled) -> a 3a install: a new `<id>`, the
    candidate reaches the v2 program. The inadmissible base is the next
    row, in its own world."""
    up = upgrade_pins
    found: dict[str, object] = {"code_2b": CODE_2B}
    with ExitStack() as stack:
        uw, first_id = stack.enter_context(world_2b(up, control_url, "upgrade"))
        lay = uw.layout
        db = lay.perimeter_dir / "events.sqlite"
        found["2b"] = {"id": first_id, "schema": schema_of(db)}
        assert schema_of(db) == "1"
        assert db_rows(db, "SELECT count(*) FROM events") == [(0,)]
        assert not (lay.etc / "trust.toml").exists()
        assert history_group() is None, "df-history уже есть после установки 2b"
        found["2b_installs_3a"] = upgrade_to_3a_pin(uw, up, first_id)
        pw = stack.enter_context(precheck_world(uw))
        stop = pw.ask(pw.current)
        before_import = ask_v2(uw)
        found["before_import"] = {"stop": stop, "candidate": before_import}
        assert stop.get("refused", "?") is None, stop
        assert str(before_import["reason"]).startswith("tree_incomplete"), before_import
        assert history_group() is None, "df-history до roles setup кодом 3a"
        # `roles setup` by the 3a code migrates the admissible base
        keep = {
            "transitions": db_rows(db, "SELECT * FROM transitions ORDER BY 1"),
            "admission": db_rows(db, "SELECT value FROM meta WHERE key = 'admission'"),
        }
        found["kept"] = keep
        migrated = uw.darkfactory(["roles", "setup", "--operator", OPERATOR])
        found["migrated"] = shown(migrated)
        assert migrated.returncode == 0, shown(migrated)
        assert schema_of(db) == "2"
        assert len(keep["admission"]) == 1, keep
        assert {
            "transitions": db_rows(db, "SELECT * FROM transitions ORDER BY 1"),
            "admission": db_rows(db, "SELECT value FROM meta WHERE key = 'admission'"),
        } == keep
        group = history_group()
        assert group is not None and OPERATOR in group.gr_mem, group
        # the repeat: `current`, nothing rewritten
        state, start = host_state(lay, db), journal_len(lay)
        again = uw.darkfactory(["roles", "setup", "--operator", OPERATOR])
        steps = journal_tail(lay, start)
        found["again"] = {"run": shown(again), "journal": steps}
        assert again.returncode == 0, shown(again)
        assert host_state(lay, db) == state
        events = {str(e.get("event")) for e in steps}
        assert steps and not events & {"failed", "stopped"}, steps
        # every writing step is settled (`skip`); the base is only asked
        # again (`perimeter-init`: `current`, unchanged bytes above)
        ran = {str(e["step"]) for e in steps if e.get("event") == "start"}
        assert ran <= {"perimeter-init", "verify"}, steps
        # a 3a install: a new release, the candidate reaches the v2 program
        world = uw.installed.world
        third = RootWorld(lay, up.pin_3a_again, world.uv, world.top, control_url)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(install_mod, "_profile", lambda _cfg: third.profile())
            code = world.entry(["install"])
        final_id = current_id(lay)
        after_import = ask_v2(uw)
        found["3a_install"] = {"code": code, "id": final_id, "candidate": after_import}
        evidence("trust-upgrade.json", found)
        upgraded = found["2b_installs_3a"]["id"]  # type: ignore[index]
        assert code == 0 and final_id not in (None, first_id, upgraded), found
        assert after_import["version"] == "df-precheck/2", after_import
        assert "history_token_required" in str(after_import["reason"]), after_import
    evidence("trust-upgrade.json", found)


def test_root_trust_upgrade_inadmissible_2b_base(
    upgrade_pins: UpgradePins, control_url: str
) -> None:
    """Apart (owner decision 7 (a)): a 2b install whose base holds an
    event written by `df-publisher` itself; the 2b installer installs this
    branch's pin; `roles setup` by the 3a code refuses
    `migration_precondition` (its text names `restore --empty`) and the
    base is byte-for-byte what it was."""
    up = upgrade_pins
    with world_2b(up, control_url, "upgrade-bad") as (uw, first_id):
        db = uw.layout.perimeter_dir / "events.sqlite"
        insert_event_as_publisher(uw, db)
        planted = db.read_bytes()
        upgraded = upgrade_to_3a_pin(uw, up, first_id)
        refused = uw.darkfactory(["roles", "setup", "--operator", OPERATOR])
        evidence(
            "trust-upgrade-inadmissible.json",
            {"2b_installs_3a": upgraded, "roles_setup": shown(refused)},
        )
        assert refused.returncode != 0, shown(refused)
        text = refused.stderr.decode("utf-8", "replace")
        assert perimeter.MIGRATION_PRECONDITION_TEXT in text, shown(refused)
        assert "restore --empty" in text
        assert db.read_bytes() == planted
        assert schema_of(db) == "1"


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
    # the checker itself read the sources in both calls (its own reader,
    # told apart by `User-Agent`: the epochs journal by SHA)
    checker = sources.rest.reads(before, CHECKER_AGENT)
    late = sources.rest.reads(middle, CHECKER_AGENT)
    early = checker[: len(checker) - len(late)]
    assert any("epochs.yaml" in p for p in early), early
    assert any("epochs.yaml" in p for p in late), late


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


@dataclass
class TokenWatch:
    """What a watched call left: every `ps` command line, the sessions of
    sudo's I/O log, and the ones holding the history token."""

    commands: set[str] = field(default_factory=set)
    sessions: list[IoSession] = field(default_factory=list)

    def in_argv(self) -> list[str]:
        return sorted(c for c in self.commands if HISTORY_TOKEN in c)

    def in_log(self) -> list[str]:
        needle = HISTORY_TOKEN.encode()
        return [
            str(s.path)
            for s in self.sessions
            if needle in s.meta.encode() or any(needle in b for b in s.streams.values())
        ]


@contextmanager
def token_watch(rw: RolesWorld, name: str) -> Iterator[TokenWatch]:
    """The block under global `log_input, log_output` (a test `sudoers.d`
    file) and the `ps` sampler; the findings after it."""
    iolog = rw.work / f"iolog-{name}"
    install_sudoers_file(
        IOLOG_FILE, f"Defaults log_input, log_output, iolog_dir={iolog}\n"
    )
    watch = TokenWatch()
    try:
        with Sampler() as sampler:
            yield watch
    finally:
        os.unlink(IOLOG_FILE)
    watch.commands = sampler.seen
    watch.sessions = io_sessions(iolog)


def test_root_trust_history_token_not_exposed(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """With sudo's I/O log on for every command, a `trust-status` (the
    publisher's and the checker's history reads): the history token is in
    no argv `ps` showed, in no stream or record of the I/O log, in the
    publisher's answer (its evidence included) and in no evidence (the
    installer's, the publisher's journal, this run's). The log holds the
    root's session of `df-publish` (its stdin: the request); the product's
    own rules keep `df-publish`'s output and `df-precheck` out of the log,
    so those streams cannot carry it here -- the row proves the rules hold.
    Controls: the token did go to the sources (`Authorization`), the
    sampler saw the publisher's command, the log holds the request. The
    plaintext moment, `setup-trust write`, is watched the same way in the
    `--rebind` of the 65-epochs row."""
    rid = f"acc-trust-iolog-{os.getpid()}"
    data = json.dumps({"schema": request.REQUEST_SCHEMA, "request_id": rid})
    before = len(sources.rest.calls)
    with token_watch(rw, "trust") as watch:
        raw = perimeter_client.sudo_call(
            data.encode(), rw.layout, operation=request.TRUST_STATUS, timeout=720
        )
    needle = HISTORY_TOKEN.encode()
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
    publishes = [s for s in watch.sessions if "df-publish" in s.meta]
    found = {
        "answer": raw.decode("utf-8", "replace")[-1000:],
        "answer_with_token": needle in raw,
        "argv_with_token": len(watch.in_argv()),
        "commands_seen": len(watch.commands),
        "sessions": [str(s.path) for s in watch.sessions],
        "log_with_token": watch.in_log(),
        "evidence_with_token": in_evidence,
        "reads_with_token": len(used),
    }
    evidence("trust-token-exposure.json", found)
    assert used, "контроль: токен истории не ушёл к источникам"
    assert any("df-publish" in c for c in watch.commands), (
        "контроль: ps не видел издателя"
    )
    assert found_in(publishes, rid, ("stdin",)), "контроль: запроса нет в I/O-журнале"
    assert needle not in raw
    assert watch.in_argv() == []
    assert watch.in_log() == []
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


# ---------- op: merge, candidate and finalize (protocol v2) ----------


@contextmanager
def open_candidate(
    rw: RolesWorld, sources: TrustSources, name: str, pr: int
) -> Iterator[Candidate]:
    """A product repository with an open candidate PR `pr` (`build_candidate`)
    behind its own git server; `publisher.toml`'s `git_base` points there
    for the call (the precheck reads the product over it)."""
    cand = build_candidate(
        rw.work / name, FULL_NAME, PRODUCT_ID, sources, rw.fake.scenario, pr=pr
    )
    server = TokenGitServer(cand.repo.served_root, lambda t: t in rw.fake.tokens)
    text = (rw.layout.etc / "publisher.toml").read_text()
    cfg = dataclasses.replace(parse_publisher(text), git_base=server.base)
    write_etc(rw.layout, "publisher.toml", dump_publisher(cfg))
    try:
        yield cand
    finally:
        write_etc(rw.layout, "publisher.toml", text)
        server.close()


def merge_request(run_id: str, pr: int, head: str, tag: str) -> bytes:
    """A `merge` request of the product's PR."""
    data = {
        "schema": request.REQUEST_SCHEMA,
        "request_id": new_request_id(tag),
        "run_id": run_id,
        "repository_id": PRODUCT_ID,
        "pr": pr,
        "head_sha": head,
    }
    return json.dumps(data, sort_keys=True).encode("utf-8")


def bound_run(rw: RolesWorld, sources: TrustSources) -> str:
    """A run of `current` bound to the current root."""
    root = ("github.com", sources.trust.id, "main")
    return rw.register_run(rw.current_entry(), root_id=root)


def merged_pull(rw: RolesWorld, pr: int) -> dict[str, object]:
    return rw.fake.scenario.pulls[(FULL_NAME, pr)]


def test_root_trust_candidate_and_finalize_merged(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """The positive candidate and finalize through the whole chain: `df-exec`
    → sudo → `df-publish merge` → the `merge` observation at the accepted X
    → sudo → `df-precheck` → `act_precheck` v2 (`op: merge`: the policy
    history to P, the review facts, the manifest, the delivery) → `allow`,
    the admission, the one `PUT`. The candidate gets no attestation; the
    finalize (exactly the envelope the delivery decision implies) gets the
    App's attestation, then its `PUT`."""
    assert trust_status(rw, sources).state == "clear"
    run_id = bound_run(rw, sources)
    sender = rw.uid("df-exec")
    with open_candidate(rw, sources, "candidate", 21) as cand:
        before = len(rw.fake.paths())
        done = rw.publish(
            "df-exec", "merge", merge_request(run_id, 21, cand.head, "cand")
        )
        first = response_of(done)
        pr, head = build_finalize(cand, rw.fake.scenario, PRODUCT_ID)
        final = rw.publish("df-exec", "merge", merge_request(run_id, pr, head, "final"))
        second = response_of(final)
        seen = rw.fake.paths()[before:]
    evidence(
        "trust-candidate-finalize.json",
        {
            "candidate": [first.outcome, first.reason, dict(first.evidence)],
            "finalize": [second.outcome, second.reason, dict(second.evidence)],
            "github": seen,
        },
    )
    for resp, done_ in ((first, done), (second, final)):
        assert (resp.outcome, resp.reason) == ("done", None), shown(done_)
        assert resp.evidence.get("sender") == sender, resp.evidence
    bot = f"{rw.fake.scenario.app_slug}[bot]"
    for number in (21, pr):
        pull = merged_pull(rw, number)
        assert pull["merged"] is True and pull["merged_by"]["login"] == bot, pull
    puts = [p for p in seen if p.startswith("PUT ")]
    assert puts == [
        f"PUT /repos/{FULL_NAME}/pulls/21/merge",
        f"PUT /repos/{FULL_NAME}/pulls/{pr}/merge",
    ]
    posted = [p for p in seen if p.startswith("POST ") and p.endswith("/reviews")]
    assert posted == [f"POST /repos/{FULL_NAME}/pulls/{pr}/reviews"]


def test_root_trust_65_epochs_then_rebind(
    rw: RolesWorld, sources: TrustSources, bound: Path
) -> None:
    """65 epochs: `trust-status` stays `clear` (it has no ranges), the
    candidate's `op: merge` is `unsupported_size: epochs`, a conservative
    event -- the root `blocked`, `doctor` names the exit. The exit: a new
    `df-trust` with a journal of one epoch, `setup-trust check --rebind` and
    `write --rebind`; the new root is not blocked by the old one's events
    (`clear`), a new run of it is answered, and a run of the old root gets
    no operation over a PR (`root_mismatch`). The refusal carries the
    stable key `unsupported_size: epochs`, which the human texts
    (`setup_trust.explain`: `doctor`, `setup-trust`) turn into the exit.
    The `--rebind` write -- the token in plaintext -- runs under the token
    watch: the token is in no argv and no I/O-log session."""
    sources.add_epochs(65)
    assert trust_status(rw, sources).state == "clear"
    old_run = bound_run(rw, sources)
    with open_candidate(rw, sources, "candidate-65", 23) as cand:
        done = rw.publish(
            "df-exec", "merge", merge_request(old_run, 23, cand.head, "e65")
        )
        refused = response_of(done)
        blocked = trust_status(rw, sources)
        report = rw.darkfactory(["doctor", "--product", str(PRODUCT_ID), "--json"])
        new = new_trust_repo(sources, 23, "df-fixture/df-trust-2")
        with token_watch(rw, "rebind") as watch:
            lines = setup_trust(rw, sources, bound, rebind=True)
        fresh = trust_status(rw, sources)
        new_run = bound_run(rw, sources)
        answered = rw.publish(
            "df-exec",
            "perimeter-status",
            perimeter_client.payload(
                new_run,
                perimeter_client.RootId("github.com", new.id, "main"),
                perimeter_client.TrustView(
                    str(fresh.snapshot_sha), fresh.last_epoch or 0
                ),
                new_request_id("e65-new"),
            ),
        )
        old = rw.publish(
            "df-exec", "merge", merge_request(old_run, 23, cand.head, "e65-old")
        )
    doctor_text = report.stdout.decode("utf-8", "replace")
    status = response_of(answered)
    stale = response_of(old)
    evidence(
        "trust-65-epochs-rebind.json",
        {
            "merge": [refused.outcome, refused.reason],
            "blocked": dataclasses.asdict(blocked),
            "doctor": doctor_text[-6000:],
            "rebind": lines,
            "fresh": dataclasses.asdict(fresh),
            "new_run_status": dict(status.result),
            "old_run_merge": [stale.outcome, stale.reason],
            "rebind_watch": {
                "commands_seen": len(watch.commands),
                "sessions": [str(s.path) for s in watch.sessions],
                "argv_with_token": len(watch.in_argv()),
                "log_with_token": watch.in_log(),
            },
        },
    )
    assert refused.outcome == "refused", shown(done)
    assert "unsupported_size: epochs" in str(refused.reason), refused
    assert EPOCHS_EXIT in explain(str(refused.reason)), refused
    assert watch.sessions and watch.in_argv() == [] and watch.in_log() == []
    assert not merged_pull(rw, 23)["merged"]
    assert blocked.state == "blocked", blocked
    assert EPOCHS_EXIT in doctor_text, doctor_text[-2000:]
    assert (fresh.state, fresh.snapshot_sha) == ("clear", new.head()), fresh
    assert status.result["state"] == "clear", status
    assert (stale.outcome, stale.reason) == ("refused", "root_mismatch"), stale
