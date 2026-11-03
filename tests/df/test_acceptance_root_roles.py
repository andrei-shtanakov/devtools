"""Root acceptance of stage 2a: spec 4.5 without merge (Task 14).

Through the real launcher and `sudo`, against the local fake GitHub, in the
world of `tests/df/roles_world.py`. Each row of the brief's table is a test
with its named mechanism of refusal and a paired positive control (r1 N12):

- OS: the role's own `errno` (`role_probe`), `EACCES` for opening, making,
  renaming and removing, `EPERM` for `chmod` of root's object;
- sudo: exit 1 with a diagnostic classified at `LC_ALL=C` (`classify_sudo`,
  captured to `sudo-diag/` and committed as `tests/df/fixtures/sudo-diag/`,
  R14-2), and no new line in `publisher/journal.jsonl` (the publisher did not
  run); «a password is required» counts as the refusal «sudo: нет правила
  (запрос пароля под -n)» only under R14-10a (`sudo_refused`, `no_rule`:
  sudo's parsed listing grants no rule for the command and does grant the
  control, the command form also exits 1 on every OS but macOS), else it
  fails the test as a wrong setup;
- the publisher: exit 2 and `outcome = refused` with the reason.

The rows run in file order on one world: the registry edits that revoke
`current` restore it before the next test. Runs only in CI (`df-stage1`,
job `stage2a (<os>)`, R14-6).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pwd
import secrets
import signal
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from df import doctor, launch, precheck_reap, setup_agent, sudoers
from df import platform as plat
from df.config import load_accounts
from df.publisher import perimeter
from df.publisher.github import SelfCheckResult
from df.publisher.precheck import PRECHECK_WAIT
from df.publisher.precheck import command as precheck_command
from df.publisher.request import Response
from tests.df.conftest import stage1_out
from tests.df.fake_github import Installation
from tests.df.observe import observe_root_access
from tests.df.precheck_world import (
    INHERIT_HOLDER,
    MEMORY_HOLDER,
    PROC_MEM_READER,
    Observed,
    PrecheckWorld,
    Proc,
    driver_result,
    environment,
    fd_names,
    live_of,
    lldb_argv,
    lldb_bytes,
    observe,
    precheck_world,
    processes,
    silent_server,
    spawn_as,
    wait_for,
)
from tests.df.roles_world import (
    APP_ID,
    BASE_ENV,
    FIXTURE_FROM_CHECKOUT,
    INSTALLATION_ID,
    IO_STREAMS,
    IOLOG_FILE,
    NO_RULE,
    OPERATOR,
    OS_DIR,
    PRODUCT_ID,
    PUBLISHER,
    REVOKED_REASON,
    SUDO,
    TRUST_ROOT,
    TRUST_TEXT,
    RolesWorld,
    SudoDiag,
    UnjudgedGrant,
    admin_python,
    as_operator,
    as_user,
    classify_sudo,
    evidence,
    fixture_form,
    found_in,
    grants_cover,
    host_names,
    install_sudoers_file,
    io_sessions,
    new_request_id,
    pr_request,
    real_site_pins,
    registry_text,
    remove_etc,
    response_of,
    roles_world,
    shown,
    status_request,
    sudo_refused,
    with_entry,
    write_etc,
)
from tests.df.root_world import (
    FixtureRepo,
    build_fixture_repo,
    infra,
    run,
    serve_repo,
    write_evidence,
)

pytestmark = [pytest.mark.root, pytest.mark.stage2a]

MODEL_ROLES = ("df-exec", "df-review")
REFUSED = 2
#: Reasons of the publisher's sender layer: a positive control must pass it.
SENDER_REFUSALS = ("not_publisher_uid", "sender_unknown", "operation_not_allowed")


# ---------- the world ----------


@pytest.fixture(scope="module")
def roles_repo(acceptance_top: Path) -> Iterator[FixtureRepo]:
    """The fixture repo with `FIXTURE_FROM_CHECKOUT` (R14-3) and the real
    `site/` pins (`real_site_pins`, 2b Task 10)."""
    bare, sha = build_fixture_repo(
        acceptance_top,
        FIXTURE_FROM_CHECKOUT,
        name="roles-fixture",
        site=real_site_pins(),
    )
    with serve_repo(acceptance_top, bare) as url:
        yield FixtureRepo(url, sha)


@pytest.fixture(scope="module")
def rw(
    acceptance_top: Path, roles_repo: FixtureRepo, control_url: str
) -> Iterator[RolesWorld]:
    """The module's world (`roles_world`)."""
    out = stage1_out()
    keep = None if out is None else out / "journals" / "roles-installed.jsonl"
    with roles_world(acceptance_top, roles_repo, control_url, keep) as world:
        yield world
        keep_publisher_journal(world)


def keep_publisher_journal(rw: RolesWorld) -> None:
    """The publisher's journal as evidence (it holds no secret)."""
    evidence("publisher-journal.json", rw.journal_lines())


# ---------- shared checks ----------


def answered(done: subprocess.CompletedProcess[bytes], sender: int) -> Response:
    """The publisher answered past its sender check, from `sender`."""
    resp = response_of(done)
    assert resp.reason not in SENDER_REFUSALS, shown(done)
    assert resp.evidence.get("sender") == sender, resp.evidence
    return resp


def no_binding(resp: Response) -> None:
    """`done` with `unavailable: no_binding` (stage 2a: no epoch journal)."""
    assert resp.outcome == "done", resp
    assert (resp.result["state"], resp.result["reason"]) == (
        "unavailable",
        "no_binding",
    )


def status_of(rw: RolesWorld, role: str, run_id: str, tag: str) -> Response:
    """`perimeter-status` of `run_id` from `role` through its sudo rule."""
    done = rw.publish(role, "perimeter-status", status_request(run_id, tag))
    return answered(done, rw.uid(role))


# ---------- positive controls of the world ----------


def test_world_controls(rw: RolesWorld) -> None:
    """The roles are their `accounts.toml` records; the setup reached the
    fake GitHub with a verified JWT and the machine token (R14-4)."""
    for role in (*MODEL_ROLES, PUBLISHER):
        done = as_user(role, ["/usr/bin/id", "-u"])
        assert done.stdout.decode().strip() == str(rw.uid(role)), shown(done)
    paths = rw.fake.paths()
    evidence("fake-github-setup.json", paths)
    assert "GET /app/installations" in rw.fake.paths("jwt")
    assert f"POST /app/installations/{INSTALLATION_ID}/access_tokens" in paths
    assert "GET /user" in rw.fake.paths("machine")
    assert rw.fake.paths("bad") == []


# ---------- rows 1-3: the OS ----------


def own_file(rw: RolesWorld, role: str) -> Path:
    """`<home>/work/df-acc-own` written by `role` itself."""
    path = launch.role_cwd(rw.layout, role) / "df-acc-own"
    assert rw.role_probe(role, [("put", str(path), "")]) == [0]
    return path


def test_exec_reads_denied(rw: RolesWorld) -> None:
    """Row 1: `df-exec` cannot read the App key, the operator's `gh`
    fixture or a file of `df-review`'s work; it reads its own work."""
    theirs = own_file(rw, "df-review")
    mine = own_file(rw, "df-exec")
    gh = Path(os.path.expanduser(f"~{OPERATOR}")) / ".config" / "gh" / "hosts.yml"
    targets = [str(rw.layout.app_key(1)), str(gh), str(theirs)]
    got = rw.role_probe("df-exec", [("read", t, "") for t in [*targets, str(mine)]])
    evidence("row1-exec-reads.json", dict(zip([*targets, str(mine)], got, strict=True)))
    assert got == [errno.EACCES] * 3 + [0]


def test_review_reads_denied(rw: RolesWorld) -> None:
    """Row 2: `df-review` cannot read the App key or the machine user's
    token; it reads its own work."""
    mine = own_file(rw, "df-review")
    token = rw.layout.role_home("df-exec").joinpath(".config", "df", "github-token")
    targets = [str(rw.layout.app_key(1)), str(token), str(mine)]
    got = rw.role_probe("df-review", [("read", t, "") for t in targets])
    evidence("row2-review-reads.json", dict(zip(targets, got, strict=True)))
    assert got == [errno.EACCES, errno.EACCES, 0]


#: The five operations on a control object and the refusal of each.
CHANGES = (
    ("create", errno.EACCES),
    ("write", errno.EACCES),
    ("rename", errno.EACCES),
    ("delete", errno.EACCES),
    ("chmod", errno.EPERM),
)


def change_steps(where: Path, role: str) -> list[tuple[str, str, str]]:
    """The five operations of `CHANGES` on `<where>/df-acc-control-<role>`."""
    control = str(where / f"df-acc-control-{role}")
    return [
        ("create", str(where / f"df-acc-new-{role}"), ""),
        ("write", control, ""),
        ("rename", control, str(where / f"df-acc-renamed-{role}")),
        ("delete", control, ""),
        ("chmod", control, ""),
    ]


@pytest.mark.parametrize("role", MODEL_ROLES)
def test_role_cannot_change_installed_objects(rw: RolesWorld, role: str) -> None:
    """Row 3: neither role makes, writes, renames, removes or `chmod`s a
    control object in `libexec/`, the release or `/etc/df`; the same five
    operations in its own `work/` succeed."""
    places = {
        "libexec": rw.layout.libexec,
        "release": rw.installed.release,
        "etc": rw.layout.etc,
    }
    controls = [where / f"df-acc-control-{role}" for where in places.values()]
    for path in controls:
        path.write_text("control\n")
        os.chmod(path, 0o644)
    try:
        found = {
            name: rw.role_probe(role, change_steps(where, role))
            for name, where in places.items()
        }
        own = launch.role_cwd(rw.layout, role)
        mine = [
            ("create", str(own / "df-acc-c"), ""),
            ("write", str(own / "df-acc-c"), ""),
            ("rename", str(own / "df-acc-c"), str(own / "df-acc-d")),
            ("chmod", str(own / "df-acc-d"), ""),
            ("delete", str(own / "df-acc-d"), ""),
        ]
        found["own work"] = rw.role_probe(role, mine)
    finally:
        for where in places.values():
            for name in ("control", "new", "renamed"):
                (where / f"df-acc-{name}-{role}").unlink(missing_ok=True)
    evidence(f"row3-{role}-changes.json", found)
    want = [code for _op, code in CHANGES]
    assert {k: v for k, v in found.items() if k != "own work"} == dict.fromkeys(
        places, want
    )
    assert found["own work"] == [0] * 5


# ---------- rows 4-8: sudo and the publisher ----------


@pytest.mark.parametrize(
    ("role", "op"), [("df-review", "merge"), ("df-exec", "publish-review")]
)
def test_foreign_operation_refused_by_sudo(rw: RolesWorld, role: str, op: str) -> None:
    """Row 4: sudo refuses a role the other role's operation; the
    publisher never runs. Control: `df-exec` → `perimeter-status` answers."""
    data = pr_request(op, rw.allowed_run, new_request_id("foreign"))
    sudo_refused(rw, f"{role}-{op}", role, op, data, "not-allowed")
    before = len(rw.journal_lines())
    no_binding(status_of(rw, "df-exec", rw.allowed_run, new_request_id("row4")))
    assert len(rw.journal_lines()) == before + 1


@pytest.mark.parametrize(
    ("op", "foreign", "own"),
    [("merge", "df-review", "df-exec"), ("publish-review", "df-exec", "df-review")],
)
def test_foreign_operation_refused_by_publisher(
    rw: RolesWorld, op: str, foreign: str, own: str
) -> None:
    """Row 5: past sudoers (`run_as` as `df-publisher`, `SUDO_UID` of the
    other role) the publisher refuses `operation_not_allowed`. Control: the
    `SUDO_UID` of the operation's own role passes the sender check (the
    request then names an unknown run)."""
    found = {}
    for sender, want in ((foreign, "operation_not_allowed"), (own, "run_unknown")):
        data = pr_request(op, f"run-unknown-{sender}", new_request_id("row5"))
        env = {"PATH": "/usr/bin:/bin", "LANG": "C", "SUDO_UID": str(rw.uid(sender))}
        result = plat.run_as(
            PUBLISHER,
            [rw.wrapper("df-publish"), op],
            env=env,
            cwd=Path("/"),
            network=False,
            timeout=120,
            stdin=data,
        )
        done = subprocess.CompletedProcess(
            [], result.returncode, result.stdout, result.stderr
        )
        resp = response_of(done)
        found[sender] = [done.returncode, resp.outcome, resp.reason]
        assert (done.returncode, resp.outcome, resp.reason) == (
            REFUSED,
            "refused",
            want,
        ), shown(done)
    evidence(f"row5-{op}.json", found)


@pytest.mark.parametrize(
    ("case", "flags", "extra", "want"),
    [
        ("sudo-E", ("-E",), (), "preserve-env"),
        ("preserve-env", ("--preserve-env",), (), "preserve-env"),
        ("extra-argument", (), ("extra",), "not-allowed"),
    ],
)
def test_sudo_forms_refused(
    rw: RolesWorld,
    case: str,
    flags: tuple[str, ...],
    extra: tuple[str, ...],
    want: SudoDiag,
) -> None:
    """Row 6: `sudo -E`, `--preserve-env`, an argument after the operation:
    sudo refuses, the publisher never runs. Control: the plain form."""
    data = status_request(rw.allowed_run, new_request_id("row6"))
    op = "perimeter-status"
    case = f"df-exec-{case}"
    sudo_refused(rw, case, "df-exec", op, data, want, flags=flags, extra=extra)
    no_binding(status_of(rw, "df-exec", rw.allowed_run, new_request_id("row6c")))


def test_sudo_uid_spoof_ignored(rw: RolesWorld) -> None:
    """Row 7: `SUDO_UID=0 sudo …` from `df-exec`: sudo sets the caller's
    `SUDO_UID`; the publisher answers `df-exec` (not a refusal)."""
    data = status_request(rw.allowed_run, new_request_id("row7"))
    done = rw.publish("df-exec", "perimeter-status", data, env={"SUDO_UID": "0"})
    resp = answered(done, rw.uid("df-exec"))
    no_binding(resp)
    last = rw.journal_lines()[-1]
    evidence("row7-sudo-uid.json", {"evidence": dict(resp.evidence), "journal": last})
    assert last["sender"] == rw.uid("df-exec")


def self_check(rw: RolesWorld) -> SelfCheckResult:
    """Root's `self-check` through the real chain (`sudo -u df-publisher`)."""
    rw.require_fake_api()
    raw = setup_agent.publisher_self_check(rw.layout, purpose="acceptance")
    done = subprocess.CompletedProcess([], 0, raw, b"")
    resp = response_of(done)
    assert resp.outcome == "done", resp
    return SelfCheckResult.from_json(dict(resp.result))


def test_self_check_only_root(rw: RolesWorld) -> None:
    """Row 8: `df-exec` → `self-check` is refused by sudo. Control: root's
    `self-check` against the fake is `done` with no problem; with an extra
    installation of the App, `problems` names it."""
    data = json.dumps({"schema": "df/publisher-request/v1", "request_id": "acc-sc-1"})
    op = "self-check"
    sudo_refused(rw, "df-exec-self-check", "df-exec", op, data.encode(), "not-allowed")
    clean = self_check(rw)
    extra = Installation(12, "df-other", (99,), {"contents": "write"})
    rw.fake.scenario.installations.append(extra)
    try:
        dirty = self_check(rw)
    finally:
        rw.fake.scenario.installations.remove(extra)
    evidence(
        "row8-self-check.json", {"clean": clean.to_json(), "extra": dirty.to_json()}
    )
    assert clean.problems == () and clean.perimeter is not None
    assert [i.id for i in clean.installations] == [INSTALLATION_ID]
    assert "app_installation_outside_products: 12" in dirty.problems


# ---------- R14-5: doctor level 2 on the real host ----------


def test_doctor_level2(rw: RolesWorld) -> None:
    """R14-5: `sudo darkfactory doctor --product 1 --json`: checks 1-9 `ok`
    (`github.app` with its stage-3 note), `models.access` «не проверено» (no
    probe), level 2 not reached — Task 13's real-host assumptions (nested
    sudo, `/bin/test`, `sudo -l`) hold."""
    done = rw.darkfactory(["doctor", "--product", str(PRODUCT_ID), "--json"])
    evidence("doctor-level2.json", done.stdout.decode("utf-8", "replace"))
    try:
        report = json.loads(done.stdout)
    except ValueError:
        pytest.fail(f"doctor: не JSON; {shown(done)}")
    statuses = {c["id"]: (c["status"], c["reason"]) for c in report["level2_checks"]}
    assert report["level1"] == "reached", report["checks"]
    assert list(statuses) == list(doctor.LEVEL2_IDS)
    first_nine = {k: v[0] for k, v in statuses.items() if k != "models.access"}
    assert first_nine == dict.fromkeys(first_nine, doctor.OK), statuses
    assert statuses["github.app"][1].startswith(doctor.TRUST_STAGE3)
    assert statuses["models.access"][0] == doctor.NOT_CHECKED
    assert report["level2"] == "not reached"
    assert done.returncode == doctor.EXIT_NOT_REACHED


# ---------- rows 9-13: runs and releases ----------


def launch_run(rw: RolesWorld, run_id: str) -> subprocess.CompletedProcess[bytes]:
    """`df-launch df-exec run` as `df-run-start` hands over: as `df-exec`,
    `run_id` on stdin, the role's environment."""
    argv = [rw.wrapper("df-launch"), "df-exec", "run"]
    env = launch.role_env(rw.layout, "df-exec")
    return as_user("df-exec", argv, stdin=f"{run_id}\n".encode(), env=env)


def test_revoked_run_refused(rw: RolesWorld) -> None:
    """Row 9: a run on a revoked release is refused by the publisher and by
    `df-launch run` (`release_revoked`). Control: a run on `allowed`."""
    data = status_request(rw.revoked_run, new_request_id("row9"))
    done = rw.publish("df-exec", "perimeter-status", data)
    resp = response_of(done)
    assert (done.returncode, resp.outcome) == (REFUSED, "refused"), shown(done)
    assert str(resp.reason).startswith("release_revoked"), resp
    launched = launch_run(rw, rw.revoked_run)
    assert launched.returncode == REFUSED, shown(launched)
    assert b"release_revoked" in launched.stderr, shown(launched)
    no_binding(status_of(rw, "df-exec", rw.allowed_run, new_request_id("row9c")))
    evidence(
        "row9-revoked.json",
        {"publisher": resp.reason, "df-launch": shown(launched)},
    )


def run_records(rw: RolesWorld) -> set[str]:
    """Names of the run records in `runs/`."""
    return {p.name for p in rw.layout.runs_dir.glob("*.json")}


def test_run_start_allowed_registers_then_stops(rw: RolesWorld) -> None:
    """Row 10 (control): `darkfactory run start` on an `allowed` `current`
    writes the record, then `df-launch run` refuses: «доступ к моделям не
    подтверждён» (plan, decision 9)."""
    before = run_records(rw)
    done = rw.human(["run", "start", "--product", str(PRODUCT_ID)])
    new = run_records(rw) - before
    evidence("row10-run-start.json", {"records": sorted(new), "run": shown(done)})
    assert len(new) == 1, shown(done)
    record = json.loads((rw.layout.runs_dir / new.pop()).read_text())
    assert record["release_id"] == rw.installed.release_id
    assert record["started_by_uid"] == pwd.getpwnam(OPERATOR).pw_uid
    assert done.returncode == REFUSED, shown(done)
    err = done.stderr.decode("utf-8", "replace")
    assert "изоляция не подтверждена" in err, shown(done)
    assert "доступ к моделям не подтверждён" in err, shown(done)


def test_run_start_revoked_current_refused(rw: RolesWorld) -> None:
    """Row 10: with `current` revoked, `df-run-start` refuses (exit 2, the
    reason) and writes no record; so does the human entry."""
    text = registry_text(rw.layout)
    current = rw.current_entry()
    revoked = type(current)(
        current.id,
        current.digest,
        current.admin_digest,
        current.admin_python,
        "revoked",
        REVOKED_REASON,
    )
    before = run_records(rw)
    write_etc(rw.layout, "releases.toml", with_entry(text, revoked))
    try:
        start = [str(rw.layout.run_start), "start", "--product", str(PRODUCT_ID)]
        wrapper = as_operator([SUDO, "-n", *start])
        human = rw.human(["run", "start", "--product", str(PRODUCT_ID)])
    finally:
        write_etc(rw.layout, "releases.toml", text)
    evidence(
        "row10-revoked-current.json", {"wrapper": shown(wrapper), "human": shown(human)}
    )
    for done in (wrapper, human):
        assert done.returncode == REFUSED, shown(done)
        assert "отозван" in done.stderr.decode("utf-8", "replace"), shown(done)
    assert run_records(rw) == before


def test_perimeter_status_computed_each_time(rw: RolesWorld) -> None:
    """Row 11: the same `perimeter-status` twice is computed anew each time
    (`unavailable: no_binding`, not a replay) and journalled twice."""
    before = len(rw.journal_lines())
    data = status_request(rw.allowed_run, new_request_id("row11"))
    answers = [
        answered(rw.publish("df-exec", "perimeter-status", data), rw.uid("df-exec"))
        for _ in range(2)
    ]
    for resp in answers:
        no_binding(resp)
        assert "replayed" not in resp.evidence
    assert len(rw.journal_lines()) == before + 2


def test_request_id_reused_refused(rw: RolesWorld) -> None:
    """Row 12: the same `request_id` with other content is refused
    (`request_id_reused`). Control: the first request is answered."""
    rid = new_request_id("row12")
    first = status_of(rw, "df-exec", rw.allowed_run, rid)
    no_binding(first)
    other = status_request(rw.allowed_run, rid, sha="1" * 40)
    done = rw.publish("df-exec", "perimeter-status", other)
    resp = response_of(done)
    assert (done.returncode, resp.outcome, resp.reason) == (
        REFUSED,
        "refused",
        "request_id_reused",
    ), shown(done)


#: A `df-exec` process of a run (row 13): it takes its `run_id` on stdin, as
#: `df-run-start` hands it to `df-launch run`, then asks `perimeter-status`
#: through its own `sudo` for each `(named run, request)` of argv[1], called
#: as `perimeter_client.sudo_call` calls it (`cwd` `/`, a timeout); prints
#: what it was and what it got.
RUN_CONTEXT = """
import json, os, subprocess, sys
own = sys.stdin.readline().strip()
answers = []
for named, data in json.loads(sys.argv[1]):
    argv = ["/usr/bin/sudo", "-n", "-u", "df-publisher", sys.argv[2], "perimeter-status"]
    done = subprocess.run(argv, input=data.encode(), capture_output=True,
                          cwd="/", timeout=60)
    answers.append({"named": named, "rc": done.returncode,
                    "stdout": done.stdout.decode(), "stderr": done.stderr.decode()})
print(json.dumps({"run_id": own, "uid": os.getuid(), "cwd": os.getcwd(),
                  "env": sorted(os.environ), "answers": answers}))
"""


def run_context(rw: RolesWorld, own: str, named: list[str]) -> dict[str, object]:
    """A `df-exec` process in the context of run `own`: the role's UID, the
    environment and working directory `launch.role_env` / `launch.role_cwd`
    give its pipeline, `own` on stdin; the release interpreter of `current`
    runs it (a revoked fixture release has no tree). The real `sudo` →
    `df-publish` path for each request naming a run of `named`."""
    python = rw.installed.release / "venv" / "devtools" / "bin" / "python"
    asks = [[n, status_request(n, new_request_id("row13")).decode()] for n in named]
    done = as_user(
        "df-exec",
        [
            str(python),
            "-I",
            "-c",
            RUN_CONTEXT,
            json.dumps(asks),
            rw.wrapper("df-publish"),
        ],
        stdin=f"{own}\n".encode(),
        env=launch.role_env(rw.layout, "df-exec"),
        cwd=launch.role_cwd(rw.layout, "df-exec"),
        exact_env=True,
    )
    if done.returncode != 0:
        infra(f"процесс прогона {own}: {shown(done)}")
    found = json.loads(done.stdout)
    assert found["run_id"] == own and found["uid"] == rw.uid("df-exec"), found
    assert found["cwd"] == str(launch.role_cwd(rw.layout, "df-exec")), found
    return found


def answer_of(found: dict[str, object], i: int) -> Response:
    """The `i`-th answer of a run context, parsed as the clients do."""
    answers = found["answers"]
    assert isinstance(answers, list)
    item = answers[i]
    out = subprocess.CompletedProcess(
        [], item["rc"], item["stdout"].encode(), item["stderr"].encode()
    )
    return response_of(out)


def test_one_uid_limit(rw: RolesWorld) -> None:
    """Row 13 (a limit, not a protection, spec 5.8): a `df-exec` process in
    the context of the run on the revoked release (its run id on stdin, the
    pipeline's env and cwd; built by the test, since `df-launch run` refuses
    that run) naming the `allowed` run's `run_id` gets exactly what a
    process of the allowed run gets — the publisher's own checks, nothing
    more (1). Control: the same revoked-run process naming its own run is
    refused `release_revoked` (2) — the publisher judges by the `run_id` of
    the request, not by the caller. The normal `df-launch run` of the
    revoked run is refused `release_revoked` (3)."""
    allowed_ctx = run_context(rw, rw.allowed_run, [rw.allowed_run])
    revoked_ctx = run_context(rw, rw.revoked_run, [rw.allowed_run, rw.revoked_run])
    from_allowed = answer_of(allowed_ctx, 0)
    from_revoked = answer_of(revoked_ctx, 0)
    control = answer_of(revoked_ctx, 1)
    launched = launch_run(rw, rw.revoked_run)
    evidence(
        "row13-one-uid-limit.json",
        {
            "kind": "предел гарантии (одна UID), не защита",
            "contexts": {"allowed": allowed_ctx, "revoked": revoked_ctx},
            "requests": [
                {
                    "sender_context": "allowed",
                    "context_run": rw.allowed_run,
                    "named_run": rw.allowed_run,
                    "answer": [
                        from_allowed.outcome,
                        from_allowed.reason,
                        dict(from_allowed.result),
                    ],
                },
                {
                    "sender_context": "revoked",
                    "context_run": rw.revoked_run,
                    "named_run": rw.allowed_run,
                    "answer": [
                        from_revoked.outcome,
                        from_revoked.reason,
                        dict(from_revoked.result),
                    ],
                },
                {
                    "sender_context": "revoked",
                    "context_run": rw.revoked_run,
                    "named_run": rw.revoked_run,
                    "answer": [control.outcome, control.reason],
                },
            ],
            "df-launch run (revoked)": shown(launched),
        },
    )
    sender = rw.uid("df-exec")
    for resp in (from_allowed, from_revoked, control):
        assert resp.evidence.get("sender") == sender, resp.evidence
    no_binding(from_allowed)
    assert (from_revoked.outcome, dict(from_revoked.result)) == (
        from_allowed.outcome,
        dict(from_allowed.result),
    )
    assert control.outcome == "refused", control
    assert str(control.reason).startswith("release_revoked"), control
    assert launched.returncode == REFUSED, shown(launched)
    assert b"release_revoked" in launched.stderr, shown(launched)


# ---------- row 14: sudo's I/O log ----------

IOLOG_CONFIGS = {
    "a-global-log-output": "Defaults log_output, iolog_dir={dir}\n",
    "b-publish-log-output": "Defaults!{publish} log_output, iolog_dir={dir}\n",
    "c-log-servers": "Defaults log_servers=127.0.0.1:30344\n",
    "d-log-input": "Defaults log_input, iolog_dir={dir}\n",
}


@pytest.mark.parametrize("case", sorted(IOLOG_CONFIGS))
def test_io_log(rw: RolesWorld, case: str) -> None:
    """Row 14 (r2 N2): a second `sudoers.d` file for the test. (a), (d):
    the `response_nonce` of `df-exec`'s `perimeter-status` is in no
    `stdout`/`stderr`/`ttyout` of any session and `io_log_problems` is
    empty; control (a): the `check_report.nonce` of `df-launch … check` is
    in its session's `stdout`; (d): the request id is in the `stdin` of the
    `df-publish` session. (b), (c): `io_log_problems` says «нарушено»."""
    iolog = rw.work / f"iolog-{case}"
    text = IOLOG_CONFIGS[case].format(dir=iolog, publish=rw.wrapper("df-publish"))
    install_sudoers_file(IOLOG_FILE, text)
    try:
        problems = sudoers.io_log_problems(rw.layout)
        found = None if case[0] in "bc" else exercise_io_log(rw, iolog, case)
    finally:
        os.unlink(IOLOG_FILE)
    evidence(
        f"row14-{case}.json", {"config": text, "problems": problems, "found": found}
    )
    want = {"b": "log_output включён", "c": sudoers.UNVERIFIABLE}.get(case[0])
    if want is None:
        assert problems == []
        return
    assert problems and all(want in p for p in problems), problems


def exercise_io_log(rw: RolesWorld, iolog: Path, case: str) -> dict[str, object]:
    """The nonce and its control under configuration (a) or (d)."""
    rid = new_request_id("row14")
    resp = status_of(rw, "df-exec", rw.allowed_run, rid)
    nonce = str(resp.evidence["response_nonce"])
    check = rw.human(["launch", "df-exec", "check"])
    if check.returncode != 0:
        infra(f"darkfactory launch df-exec check: {shown(check)}")
    marker = str(json.loads(check.stdout)["nonce"])
    sessions = io_sessions(iolog)
    leaked = found_in(sessions, nonce, ("stdout", "stderr", "ttyout"))
    result: dict[str, object] = {
        "sessions": [str(s.path) for s in sessions],
        "nonce_in_output": [str(p) for p in leaked],
    }
    assert sessions, "сессий I/O-журнала нет"
    assert leaked == []
    if case.startswith("a"):
        launches = [s for s in sessions if "df-launch" in s.meta]
        seen = found_in(launches, marker, ("stdout",))
        result["check_marker_in_stdout"] = [str(p) for p in seen]
        assert seen, "контроль: маркер check не найден в stdout сессии df-launch"
    else:
        publishes = [s for s in sessions if "df-publish" in s.meta]
        seen = found_in(publishes, rid, ("stdin",))
        result["request_id_in_stdin"] = [str(p) for p in seen]
        assert seen, "контроль: request_id не найден в stdin сессии df-publish"
    return result


# ---------- carried over from stage 1 ----------


def sentinel_dir(rw: RolesWorld, name: str) -> Path:
    """A `1777` directory under the world's work dir for a sentinel's log."""
    path = rw.work / name
    path.mkdir()
    os.chmod(path, 0o1777)
    return path


def test_run_start_no_release_code_before_drop(rw: RolesWorld) -> None:
    """Stage-1 carry-over: a sentinel in place of the release interpreter
    (`venv/devtools/bin/python`, logging its UID) is not run while
    `df-run-start` is root, through the real `sudo`; after the drop it runs
    as `df-exec`, which is the positive control."""
    python = rw.installed.release / "venv" / "devtools" / "bin" / "python"
    real = python.with_name("python.df-acc-real")
    log = sentinel_dir(rw, "sentinel-run") / "uids"
    os.rename(python, real)
    try:
        python.write_text(f'#!/bin/sh\n/usr/bin/id -u >> {log}\nexec {real} "$@"\n')
        os.chmod(python, 0o755)
        done = rw.human(["run", "start", "--product", str(PRODUCT_ID)])
    finally:
        python.unlink(missing_ok=True)
        os.rename(real, python)
    uids = log.read_text().split() if log.exists() else []
    evidence("carry-run-start-sentinel.json", {"uids": uids, "run": shown(done)})
    assert "0" not in uids
    assert uids == [str(rw.uid("df-exec"))], shown(done)


#: Inserted after `from __future__ import annotations` of the release's
#: `launch_release.py` (`phase` "start": what the process is at its first
#: statement) and before its first class (`phase` "imports", evidence only:
#: the descriptors its imports opened, with their targets where `/proc`
#: names them — finding F3).
START_SENTINEL = """
import json as _df_acc_json
import os as _df_acc_os

_df_acc_fds = []
for _df_acc_fd in range(3, 1024):
    try:
        _df_acc_os.fstat(_df_acc_fd)
    except OSError:
        continue
    _df_acc_fds.append(_df_acc_fd)
_df_acc_targets = {{}}
for _df_acc_fd in _df_acc_fds:
    try:
        _df_acc_targets[_df_acc_fd] = _df_acc_os.readlink(f"/proc/self/fd/{{_df_acc_fd}}")
    except OSError:
        _df_acc_targets[_df_acc_fd] = None
with open({log!r}, "a") as _df_acc_out:
    _df_acc_out.write(
        _df_acc_json.dumps(
            {{
                "phase": {phase!r},
                "targets": _df_acc_targets,
                "uid": _df_acc_os.getuid(),
                "euid": _df_acc_os.geteuid(),
                "cwd": _df_acc_os.getcwd(),
                "fds": _df_acc_fds,
                "env": sorted(_df_acc_os.environ),
            }}
        )
        + "\\n"
    )
"""
_FUTURE = "from __future__ import annotations\n"
_FIRST_CLASS = "\nclass Refused(Exception):"


def test_launch_release_sees_clean_start(rw: RolesWorld) -> None:
    """Stage-1 carry-over: a sentinel in the fixture release's
    `launch_release.py` records UID, `cwd` and open descriptors at its
    start: `cwd = work/`, nothing above 2 — for a call whose caller held
    fd 9 open (the operator's `darkfactory launch df-exec check`)."""
    script = rw.installed.release / "src" / "devtools" / "df" / "launch_release.py"
    original = script.read_bytes()
    log = sentinel_dir(rw, "sentinel-start") / "start.jsonl"
    text = original.decode("utf-8")
    if _FUTURE not in text:
        infra(f"{script}: нет {_FUTURE.strip()!r}")
    if _FIRST_CLASS not in text:
        infra(f"{script}: нет {_FIRST_CLASS.strip()!r}")
    start = START_SENTINEL.format(log=str(log), phase="start")
    after = START_SENTINEL.format(log=str(log), phase="imports")
    patched = text.replace(_FUTURE, _FUTURE + start, 1)
    patched = patched.replace(_FIRST_CLASS, after + _FIRST_CLASS, 1)
    # the caller (the operator's shell) opens fd 9 itself and keeps it
    # across its `exec` of the command
    shell = "exec 9</dev/null; [ -e /dev/fd/9 ] && echo fd9-open >&2; "
    shell += 'exec "$0" "$@"'
    argv = ["/bin/sh", "-c", shell, str(rw.layout.launcher)]
    try:
        script.write_text(patched)
        done = as_user(OPERATOR, [*argv, "launch", "df-exec", "check"])
    finally:
        script.write_bytes(original)
    starts = [json.loads(line) for line in log.read_text().splitlines()]
    evidence("carry-launch-release-start.json", {"starts": starts, "run": shown(done)})
    assert b"fd9-open" in done.stderr, shown(done)
    assert done.returncode == 0, shown(done)
    uid = rw.uid("df-exec")
    work = str(launch.role_cwd(rw.layout, "df-exec"))
    # `env` at the first statement is evidence only (doctor's isolation
    # check judges the report's, finding F3)
    first = [s for s in starts if s.get("phase") == "start"]
    keep = ("uid", "euid", "cwd", "fds")
    seen = [{k: s[k] for k in keep} for s in first]
    assert seen == [{"uid": uid, "euid": uid, "cwd": work, "fds": []}]


def test_launch_cwd_outside_builder_tree(rw: RolesWorld) -> None:
    """Stage-1 carry-over: `darkfactory launch df-exec check` from a
    directory inside the builder's tree: the stage-1 observer, over the
    whole chain with `sudo`, sees no open of it with `euid = 0`. Positive
    control (macOS): a direct `sudo …/df-launch` from the same directory —
    the unsupported form — is seen opening it."""
    world = rw.installed.world
    builder = world.builder_dir(world.top / "builder-cwd", "true")
    control = world.builder_dir(world.top / "observe-control", "echo c > f")
    out = stage1_out()
    launcher = str(rw.layout.launcher)
    calls = {
        "human": [launcher, "launch", "df-exec", "check"],
        "direct": [SUDO, "-n", "-u", "df-exec", rw.wrapper("df-launch")]
        + ["df-exec", "check"],
    }
    seen = {}
    for phase, argv in calls.items():
        record = None if out is None else out / "observe" / f"roles-cwd-{phase}.json"
        with observe_root_access(
            [builder],
            control=control,
            workdir=world.workdir(),
            scope=world.top,
            record=record,
        ) as obs:
            # the operator enters the directory itself: no root `chdir`
            enter = ["/bin/sh", "-c", 'cd "$0" && exec "$@"', str(builder)]
            done = as_user(OPERATOR, [*enter, *argv])
        seen[phase] = (obs, done)
    evidence(
        "carry-launch-cwd.json",
        {
            phase: {
                "status": obs.status,
                "reason": obs.reason,
                "violations": [v.path for v in obs.violations],
                "run": shown(done),
                "limits": obs.limits,
            }
            for phase, (obs, done) in seen.items()
        },
    )
    for obs, done in seen.values():
        if obs.status != "observed":
            infra(f"наблюдатель: {obs.reason}")
        assert done.returncode == 0, shown(done)
    assert seen["human"][0].violations == []
    if plat.detect_platform() == "macos-arm64":
        assert seen["direct"][0].violations, "контроль: открытие cwd от root не видно"


# ---------- the perimeter on a full file system (from Task 11) ----------


@contextmanager
def small_fs(rw: RolesWorld, mountpoint: Path, uid: int, gid: int) -> Iterator[None]:
    """A size-bounded file system on `mountpoint`, `df-publisher`'s `0700`:
    `tmpfs size=` (Linux) or an `hdiutil` image (macOS); unmounted after."""
    if plat.detect_platform() == "linux-x86_64":
        opts = f"size=1m,mode=0700,uid={uid},gid={gid}"
        run(["/usr/bin/mount", "-t", "tmpfs", "-o", opts, "df-acc", str(mountpoint)])
        unmount = ["/usr/bin/umount", str(mountpoint)]
    else:
        image = rw.work / "perimeter.dmg"
        hdiutil = "/usr/bin/hdiutil"
        # APFS: the file system `/var/lib/df` is on in a macOS install
        run(
            [hdiutil, "create", "-size", "8m", "-fs", "APFS", "-volname", "dfacc"]
            + [str(image)]
        )
        run(
            [hdiutil, "attach", "-nobrowse", "-noautoopen", "-owners", "on"]
            + ["-mountpoint", str(mountpoint), str(image)]
        )
        os.chown(mountpoint, uid, gid)
        os.chmod(mountpoint, 0o700)
        unmount = [hdiutil, "detach", "-force", str(mountpoint)]
    try:
        yield
    finally:
        run(unmount)


def fill(directory: Path) -> int:
    """Write a filler file under `directory` until not one more byte fits
    (`ENOSPC` at every chunk size down to 1); its size."""
    fd = os.open(directory / "df-acc-filler", os.O_WRONLY | os.O_CREAT, 0o600)
    total, size = 0, 1 << 16
    try:
        while size:
            try:
                total += os.write(fd, b"\0" * size)
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                size //= 2
    finally:
        os.close(fd)
    return total


def violation(rw: RolesWorld, run_id: str, rid: str, detail: str) -> Response:
    """`record-violation` from `df-exec` through its sudo rule."""
    root = dict(zip(("host", "repository_id", "branch"), TRUST_ROOT, strict=True))
    data = {
        "schema": "df/publisher-request/v1",
        "request_id": rid,
        "run_id": run_id,
        "root_id": root,
        "source": "df-trust",
        "observed_at": "2026-10-29T10:00:00Z",
        "detail": detail,
    }
    done = rw.publish("df-exec", "record-violation", json.dumps(data).encode())
    return response_of(done)


def test_disk_full_refuses(rw: RolesWorld) -> None:
    """From Task 11: on a size-bounded file system `record-violation` past
    the free space is `refused: storage_full`; the confirmed event stays.
    Control: the first event on the same file system is `done`."""
    lay = rw.layout
    base = lay.perimeter_dir
    pub = load_accounts(lay.accounts)[PUBLISHER]
    write_etc(lay, "trust.toml", TRUST_TEXT)
    try:
        run_id = rw.register_run(rw.current_entry(), root=True)
        names = (perimeter.DB_NAME, perimeter.MARKER, perimeter.LOCK)
        saved = {n: (base / n).read_bytes() for n in names}
        with small_fs(rw, base, pub.uid, pub.gid):
            for name, data in saved.items():
                (base / name).write_bytes(data)
                os.chown(base / name, pub.uid, pub.gid)
                os.chmod(base / name, 0o600)
            first = violation(rw, run_id, new_request_id("disk1"), "first")
            filler = fill(base)
            second = violation(rw, run_id, new_request_id("disk2"), "x" * 4000)
            states = events(base / perimeter.DB_NAME)
    finally:
        remove_etc(lay, "trust.toml")
    evidence(
        "disk-full.json",
        {
            "first": [first.outcome, first.reason, dict(first.result)],
            "second": [second.outcome, second.reason],
            "filler_bytes": filler,
            "events": states,
        },
    )
    assert (first.outcome, first.result.get("state")) == ("done", "pending"), first
    assert (second.outcome, second.reason) == ("refused", "storage_full"), second
    assert states == ["pending"]


def events(db: Path) -> list[str]:
    """The `state` of every event, read by root without writing."""
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        return [str(r[0]) for r in conn.execute("SELECT state FROM events")]
    finally:
        conn.close()


# ---------- stage 2b: the role df-precheck (Task 10) ----------

PRECHECK = "df-precheck"
#: The bounds of the rungs (plan decision 5): the precheck's `DEADLINE`
#: (150 s) plus slack, the client's wait, sudo's `command_timeout` (180 s)
#: plus its kill.
RUNG1_BOUND = 155.0
CLIENT_BOUND = 200.0
RUNG2_BOUND = 185.0
NOT_VERIFIED = "not_verified"
LINUX = plat.detect_platform() == "linux-x86_64"


@pytest.fixture(scope="module")
def pw(rw: RolesWorld) -> Iterator[PrecheckWorld]:
    """The product repository and its stop PRs (`precheck_world`)."""
    with precheck_world(rw) as world:
        yield world


def regular_files(directory: Path) -> list[Path]:
    """Regular files under `directory` (root lists them)."""
    return sorted(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink())


def root_read(path: Path) -> int:
    """`errno` of root opening and reading `path` (0: read)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            os.read(fd, 1)
        finally:
            os.close(fd)
    except OSError as exc:
        return exc.errno or -1
    return 0


def test_precheck_reads_denied(rw: RolesWorld) -> None:
    """`df-precheck` cannot read the App key, the publisher's files, the
    perimeter's files or the machine user's token (the OS refuses,
    `EACCES`). Control: root reads every one of them."""
    lay = rw.layout
    token = lay.role_home("df-exec").joinpath(".config", "df", "github-token")
    groups = {
        "app_key": [lay.app_key(APP_ID)],
        "publisher": regular_files(lay.publisher_dir),
        "perimeter": regular_files(lay.perimeter_dir),
        "machine_token": [token],
    }
    empty = [name for name, files in groups.items() if not files]
    if empty:
        infra(f"нет файлов для строки чтения: {empty}")
    files = [f for group in groups.values() for f in group]
    got = rw.role_probe(PRECHECK, [("read", str(f), "") for f in files])
    control = [root_read(f) for f in files]
    evidence(
        "precheck-reads.json",
        {
            str(f): {PRECHECK: g, "root": c}
            for f, g, c in zip(files, got, control, strict=True)
        },
    )
    assert control == [0] * len(files)
    assert got == [errno.EACCES] * len(files)


def memory_observer(pid: int, address: int, length: int, python: str) -> list[str]:
    """The observer of the memory row: `/proc/<pid>/mem` (Linux) or `lldb`
    (`task_for_pid`, macOS)."""
    if LINUX:
        return [
            python,
            "-I",
            "-S",
            "-c",
            PROC_MEM_READER,
            str(pid),
            str(address),
            str(length),
        ]
    return lldb_argv(pid, address, length)


def read_bytes(seen: Observed) -> bytes:
    """What the memory observer read (`b""`: nothing)."""
    if not LINUX:
        return lldb_bytes(seen.stdout)
    try:
        found = json.loads(seen.stdout)
    except ValueError:
        return b""
    return (
        bytes.fromhex(found["hex"])
        if isinstance(found, dict) and "hex" in found
        else b""
    )


def test_precheck_cannot_read_publisher_memory(rw: RolesWorld) -> None:
    """A `df-publisher` process holds a sentinel at a known address and
    length; `df-precheck` cannot read it through `/proc/<pid>/mem` (Linux)
    or `lldb -p` (`task_for_pid`, macOS). Control: root, by the same
    observer, reads the sentinel's bytes. A root control that does not work
    on this host is `not_verified` (xfail), never a pass (r5 N2)."""
    sentinel = f"df-acc-mem-{secrets.token_hex(10)}".encode()
    python = str(admin_python(rw.layout))
    holder = spawn_as(
        PUBLISHER,
        [python, "-I", "-S", "-c", MEMORY_HOLDER, sentinel.hex()],
        env=BASE_ENV,
        stdin=subprocess.PIPE,
    )
    try:
        line = holder.stdout.readline() if holder.stdout else b""
        words = line.split()
        if len(words) != 3:
            infra(f"процесс с sentinel не запустился: {line!r}")
        pid, address, length = (int(w) for w in words)
        argv = memory_observer(pid, address, length, python)
        role = observe(argv, user=PRECHECK, timeout=90)
        plat.reap_user_processes(PRECHECK)
        root = observe(argv, user=None, timeout=90)
    finally:
        stop_holder(holder)
    role_bytes, root_bytes = read_bytes(role), read_bytes(root)
    status = "pass" if root_bytes == sentinel else NOT_VERIFIED
    evidence(
        f"precheck-memory-{OS_DIR}.json",
        {
            "status": status,
            "observer": argv,
            PRECHECK: {**role.shown(), "read_sentinel": sentinel in role_bytes},
            "root": {**root.shown(), "read_sentinel": root_bytes == sentinel},
        },
    )
    assert sentinel not in role_bytes and role_bytes == b"", role.shown()
    if LINUX:
        found = json.loads(role.stdout)
        assert found.get("errno") in (errno.EACCES, errno.EPERM), found
    else:
        assert role.code != 0, role.shown()
    if root_bytes != sentinel:
        if LINUX:
            infra(f"root не прочитал sentinel через /proc/<pid>/mem: {root.shown()}")
        pytest.xfail(f"{NOT_VERIFIED}: root lldb не прочитал sentinel: {root.shown()}")


def stop_holder(holder: subprocess.Popen[bytes]) -> None:
    """End a holder process: a line on its stdin, then a kill if needed."""
    try:
        if holder.stdin is not None:
            holder.stdin.write(b"\n")
            holder.stdin.close()
        holder.wait(timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        holder.kill()
        holder.wait(timeout=30)


@pytest.mark.parametrize(
    ("case", "caller", "extra"),
    [
        ("df-exec", "df-exec", ()),
        ("df-review", "df-review", ()),
        ("operator", OPERATOR, ()),
        ("df-publisher-argument", PUBLISHER, ("extra",)),
    ],
)
def test_precheck_callers_refused_by_sudo(
    rw: RolesWorld, case: str, caller: str, extra: tuple[str, ...]
) -> None:
    """`df-exec`, `df-review` and the operator calling `df-precheck`, and
    `df-publisher` calling it with an argument: sudo refuses (exit 1, its
    diagnostic at `LC_ALL=C`; «a password is required» counts only where
    sudo's own listing grants no rule for the command, R14-10). The
    operator of a host whose own rules let it run anything as anyone (the
    hosted runner's `(ALL) NOPASSWD: ALL`) is `not_verified` there.
    Control: `test_precheck_publisher_passes_sudo`."""
    wrapper = rw.wrapper(PRECHECK)
    done = as_user(caller, [*precheck_command(rw.layout), *extra])
    text = done.stderr.decode("utf-8", "replace")
    out = stage1_out()
    name = f"precheck-{case}"
    write_evidence(
        out,
        f"sudo-diag/{OS_DIR}/{name}.txt",
        fixture_form(text, rw.layout.root, host_names()),
    )
    write_evidence(out, f"sudo-diag/{OS_DIR}/{name}.rc", f"{done.returncode}\n")
    covered = precheck_granted(caller, " ".join([wrapper, *extra]))
    if caller == OPERATOR and covered:
        reason = f"{NOT_VERIFIED}: правила образа дают {caller} любую команду от любого"
        write_evidence(out, f"sudo-diag/{OS_DIR}/{name}.mechanism", reason + "\n")
        pytest.xfail(f"{reason}; {shown(done)}")
    kind = classify_sudo(done.returncode, text)
    if kind is None:
        infra(f"нераспознанная диагностика sudo ({case}): {shown(done)}")
    assert done.stdout == b"", shown(done)
    assert not covered, f"политика sudo разрешает {caller}: {shown(done)}"
    mechanism = NO_RULE if kind == "password-required" else str(kind)
    assert kind in ("not-allowed", "password-required"), shown(done)
    write_evidence(out, f"sudo-diag/{OS_DIR}/{name}.mechanism", mechanism + "\n")


def precheck_granted(role: str, command: str) -> bool:
    """Whether sudo's own policy for `role` (`sudo -n -l -U`) lets it run
    `command` as `df-precheck`; a grant not judged exactly is infra."""
    try:
        listing = sudoers.read_listing(role)
        wrapper = command.split(" ", 1)[0]
        return grants_cover(
            listing.grants | listing.carried, PRECHECK, wrapper, command
        )
    except (sudoers.ListingError, UnjudgedGrant) as exc:
        infra(f"sudo -l -U {role}: {exc}")
        raise


def test_precheck_publisher_passes_sudo(rw: RolesWorld) -> None:
    """Control of the sudo rows: `df-publisher` with no argument passes
    sudo and reaches `precheck serve`, which refuses the empty request
    itself (exit 2, its own diagnostic)."""
    done = as_user(PUBLISHER, list(precheck_command(rw.layout)))
    evidence("precheck-publisher-passes.json", shown(done))
    assert done.returncode == 2, shown(done)
    assert b"df-precheck: invalid request" in done.stderr, shown(done)
    assert classify_sudo(1, done.stderr.decode("utf-8", "replace")) is None


def observe_process(pid: int) -> dict[str, object]:
    """Descriptors, environment and UID of `pid`, as root."""
    proc = next((p for p in processes() if p.pid == pid), None)
    return {
        "uid": None if proc is None else proc.uid,
        "fds": fd_names(pid),
        "environment": environment(pid),
    }


def test_precheck_inherits_nothing(rw: RolesWorld) -> None:
    """A publisher opens a sentinel descriptor without `CLOEXEC`, holds a
    sentinel variable and starts the precheck command, which blocks reading
    its stdin (no request written yet). Root's observer (`/proc/<pid>/fd`
    and `environ` on Linux, `lsof -p` and `ps -E` on macOS) sees neither in
    the precheck process, whose UID is `df-precheck`. Control: the same
    observer sees both in the publisher."""
    marker = secrets.token_hex(12)
    sentinel = rw.work / f"precheck-sentinel-{marker}"
    sentinel.write_text("sentinel\n")
    os.chmod(sentinel, 0o644)
    real = os.path.realpath(sentinel)
    python = str(admin_python(rw.layout))
    argv = [python, "-I", "-S", "-c", INHERIT_HOLDER, str(sentinel)]
    argv.append(json.dumps(list(precheck_command(rw.layout))))
    env = {**BASE_ENV, "DF_ACC_SENTINEL": marker}
    holder = spawn_as(PUBLISHER, argv, env=env, stdin=subprocess.PIPE)
    uid = rw.uid(PRECHECK)
    try:
        first = json.loads(holder.stdout.readline() if holder.stdout else b"null")
        found = wait_for(
            lambda: [p for p in live_of(uid) if "precheck serve" in p.command], 60
        )
        if not found or not isinstance(first, dict):
            infra(f"процесс проверяющего не появился: {first!r}")
        assert isinstance(found, list) and isinstance(first, dict)
        time.sleep(3)  # past its imports: blocked reading stdin
        target = found[0]
        alive = [p.pid for p in live_of(uid)]
        seen = {
            PRECHECK: observe_process(target.pid),
            PUBLISHER: observe_process(int(first["pid"])),
        }
    finally:
        stop_holder(holder)
    tail = holder.stdout.read() if holder.stdout else b""
    evidence(
        "precheck-inherit.json",
        {
            "sentinel": real,
            "marker": marker,
            "seen": seen,
            "alive": alive,
            "holder": first,
            "end": tail.decode("utf-8", "replace"),
        },
    )
    publisher, precheck = seen[PUBLISHER], seen[PRECHECK]
    assert any(n.endswith(f":{real}") for n in publisher["fds"]), publisher
    assert f"DF_ACC_SENTINEL={marker}" in str(publisher["environment"]), publisher
    assert target.pid in alive, "проверяющий не ждал запроса"
    assert precheck["uid"] == uid, precheck
    assert not any(real in n for n in precheck["fds"]), precheck
    assert marker not in str(precheck["environment"]), precheck


def test_precheck_stop_through_the_chain(rw: RolesWorld, pw: PrecheckWorld) -> None:
    """The positive stop: the publisher's client (the fake GitHub's read
    token) → sudo → the entry → `act_precheck` over the squash-delivered
    history on the dumb-HTTP git server → `allow`, bound and with the admin
    digest of `current`, the token revoked. Control: the stop of the stale
    generation is `refuse`."""
    before = len(rw.fake.paths())
    allow = pw.ask(pw.current)
    stale = pw.ask(pw.stale)
    seen = rw.fake.paths()[before:]
    evidence(
        "precheck-stop.json",
        {"allow": allow, "stale": stale, "github": seen, "git": pw.git.log[-40:]},
    )
    assert allow.get("refused", "?") is None, allow
    assert allow["version"] == "df-precheck/1", allow
    assert allow["admin_digest"] == rw.current_entry().admin_digest, allow
    assert allow["revoke"] == "ok", allow
    assert stale.get("refused") == "stop_generation_not_current: stop g1, current g2"
    assert seen.count("DELETE /installation/token") == 2, seen
    assert pw.git.log and all(ok for _path, ok in pw.git.log), pw.git.log
    assert pw.snapshots() == []


@pytest.mark.parametrize("case", ["global", "control"])
def test_precheck_input_not_logged(
    rw: RolesWorld, pw: PrecheckWorld, case: str
) -> None:
    """Global `Defaults log_input, log_output` in a test `sudoers.d`
    fragment: the read token of the request is in no stream of sudo's I/O
    log and `io_log_problems` (doctor's check) is empty. Control: with the
    command's own logging back on (`Defaults!<df-precheck> log_input,
    log_output` after it), the token is in the log and the check flags it."""
    iolog = rw.work / f"iolog-precheck-{case}"
    text = f"Defaults log_input, log_output, iolog_dir={iolog}\n"
    if case == "control":
        text += f"Defaults!{rw.wrapper(PRECHECK)} log_input, log_output\n"
    install_sudoers_file(IOLOG_FILE, text)
    try:
        problems = sudoers.io_log_problems(rw.layout)
        before = len(rw.fake.minted)
        result = pw.ask(pw.current)
        tokens = rw.fake.minted[before:]
    finally:
        os.unlink(IOLOG_FILE)
    sessions = io_sessions(iolog)
    found = [str(p) for t in tokens for p in found_in(sessions, t, IO_STREAMS)]
    evidence(
        f"precheck-iolog-{case}.json",
        {
            "config": text,
            "problems": problems,
            "result": result,
            "sessions": [str(s.path) for s in sessions],
            "token_in": found,
        },
    )
    assert len(tokens) == 1 and result.get("refused", "?") is None, result
    if case == "global":
        assert problems == []
        assert found == []
        return
    assert any(PRECHECK in p for p in problems), problems
    assert found, "контроль: токен не найден в I/O-журнале"


def lock_free(pw: PrecheckWorld) -> bool:
    """Root takes `<home>/precheck.lock` exclusively without waiting."""
    fd = os.open(pw.home / "precheck.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(fd)
    return True


def test_precheck_git_hang_rung1(rw: RolesWorld, pw: PrecheckWorld) -> None:
    """`git_base` is a port that accepts and never answers: rung 1, the
    precheck's own deadline — `unset: deadline` within 155 s, no process of
    `df-precheck` left (its git children killed), no snapshot, the lock
    free."""
    with silent_server() as url:
        result = pw.ask(pw.current, git_base=url)
    uid = rw.uid(PRECHECK)
    left = [] if wait_for(lambda: live_of(uid) == [], 10) else live_of(uid)
    record = {
        "result": result,
        "left": [p.command for p in left],
        "snapshots": pw.snapshots(),
        "lock_free": lock_free(pw),
    }
    evidence("precheck-rung1.json", record)
    assert result.get("refused") == "precheck_unset: deadline", result
    assert float(result["end"]) - float(result["start"]) <= RUNG1_BOUND, result
    assert left == [] and record["snapshots"] == [] and record["lock_free"]


#: The real process lister, before `conftest._no_real_reap` replaces it
#: in this process (the root rows read real processes).
REAL_PROCESSES = precheck_reap.list_processes
#: Seconds over `PRECHECK_WAIT` the supervised call may take to return:
#: its wait ends at `PRECHECK_WAIT`, then the pipe is closed.
RETURN_SLACK = 1.0


@dataclass
class StoppedRun:
    """A run of the positive stop whose precheck root stopped: the client's
    result, the precheck's and its sudo's PIDs, samples of the processes
    (`sample`) until the client returned, the snapshots then."""

    result: dict[str, object]
    target: int
    sudo: int
    stopped_at: float
    samples: list[dict[str, object]]
    snapshots: list[str]


def stopped_precheck(rw: RolesWorld, pw: PrecheckWorld) -> StoppedRun:
    """Run the positive stop with a slow git server; root `SIGSTOP`s the
    precheck process once its snapshot exists; sample the processes of
    `df-precheck` (and its sudo) every second until the client returns.
    The stopped processes stay (the caller ends them, `end_stopped`); on a
    failure here they end at once."""
    uid = rw.uid(PRECHECK)
    pw.git.delay = 2.0
    proc = pw.start(pw.current)
    target: Proc | None = None
    try:
        if not wait_for(pw.snapshots, 60):
            infra("снимок проверяющего не появился")
        found = wait_for(
            lambda: [p for p in live_of(uid) if "precheck serve" in p.command], 10
        )
        if not isinstance(found, list):
            infra("процесс проверяющего не найден")
        assert isinstance(found, list)
        target = found[0]
        os.kill(target.pid, signal.SIGSTOP)
        stopped_at = time.monotonic()
        pw.git.delay = 0.0
        samples: list[dict[str, object]] = []
        while proc.poll() is None:
            samples.append(sample(uid, target.ppid))
            time.sleep(1.0)
        result = driver_result(proc, 60)
    except BaseException:
        end_stopped(target)
        raise
    finally:
        pw.git.delay = 0.0
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
    return StoppedRun(
        result, target.pid, target.ppid, stopped_at, samples, pw.snapshots()
    )


def sample(uid: int, sudo_pid: int) -> dict[str, object]:
    """The live processes of `uid` and the state of the sudo above them."""
    everyone = processes()
    mine = [p for p in everyone if p.uid == uid and not p.stat.startswith("Z")]
    sudo = next((p for p in everyone if p.pid == sudo_pid), None)
    return {
        "t": time.monotonic(),
        "precheck": [[p.pid, p.stat, p.command[:120]] for p in mine],
        "sudo": None if sudo is None else [sudo.pid, sudo.stat, sudo.command[:120]],
    }


def end_stopped(target: Proc | None) -> None:
    """The precheck and its sudo (both possibly stopped) are gone: the
    sudo is root's, which no precheck reaps (spec 5.x, «Предел второго
    рубежа»)."""
    for pid in () if target is None else (target.pid, target.ppid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    plat.reap_user_processes(PRECHECK)


def age_of(pid: int) -> float | None:
    """The age of `pid` by `precheck_reap`'s own reading (`None`: gone or
    a zombie)."""
    found = next((p for p in REAL_PROCESSES() if p.pid == pid), None)
    return None if found is None or found.zombie else found.age


def test_precheck_command_timeout_reaped(rw: RolesWorld, pw: PrecheckWorld) -> None:
    """The precheck itself hangs (root's `SIGSTOP` after its snapshot
    exists). sudo stops itself with it, so rung 2 never fires (spec,
    «Предел второго рубежа»): the client answers `precheck_failed: timeout`
    at `PRECHECK_WAIT` from its sudo's start (rung 3), and the stopped
    processes are still there. A call while they are younger than
    `REAP_AFTER` spares them and their snapshot (a live run's lock); the
    first call past it reaps them: no process of the first call is left,
    its snapshot is gone, and that call's verdict is the normal `allow`."""
    run = stopped_precheck(rw, pw)
    target = run.target
    try:
        left = sample(rw.uid(PRECHECK), run.sudo)
        young = pw.ask(pw.current)
        young_age = age_of(target)
        young_snapshots = pw.snapshots()
        waited = wait_for(
            lambda: (age_of(target) or 0.0) > precheck_reap.REAP_AFTER + 1, 90, 0.5
        )
        reaped = pw.ask(pw.current)
        after = {"age": age_of(target), "sample": sample(rw.uid(PRECHECK), run.sudo)}
    finally:
        end_stopped(next((p for p in processes() if p.pid == target), None))
    result = run.result
    supervised = float(str(result["sudo_end"])) - float(str(result["sudo_start"]))
    evidence(
        "precheck-rung2.json",
        {
            **asdict(run),
            "supervised_seconds": supervised,
            "left_after_client": left,
            "young": {"result": young, "age": young_age, "snapshots": young_snapshots},
            "waited_past_threshold": bool(waited),
            "reaped": {"result": reaped, **after, "snapshots": pw.snapshots()},
        },
    )
    assert result.get("refused") == "precheck_failed: timeout", result
    assert supervised <= PRECHECK_WAIT + RETURN_SLACK, supervised
    assert any(p[0] == target for p in left["precheck"]), left
    assert young.get("refused", "?") is None, young
    assert young_age is not None and set(run.snapshots) <= set(young_snapshots)
    assert waited, "процесс не дожил до порога (убит раньше?)"
    assert reaped.get("refused", "?") is None, reaped
    assert after["age"] is None, after
    assert not after["sample"]["precheck"], after
    assert pw.snapshots() == []
