"""Root acceptance of stage 2a: spec 4.5 without merge (Task 14).

Through the real launcher and `sudo`, against the local fake GitHub, in the
world of `tests/df/roles_world.py`. Each row of the brief's table is a test
with its named mechanism of refusal and a paired positive control (r1 N12):

- OS: the role's own `errno` (`role_probe`), `EACCES` for opening, making,
  renaming and removing, `EPERM` for `chmod` of root's object;
- sudo: exit 1 with a diagnostic classified at `LC_ALL=C` (`classify_sudo`,
  captured to `sudo-diag/` and committed as `tests/df/fixtures/sudo-diag/`,
  R14-2), and no new line in `publisher/journal.jsonl` (the publisher did not
  run); «a password is required» fails the test as a wrong setup;
- the publisher: exit 2 and `outcome = refused` with the reason.

The rows run in file order on one world: the registry edits that revoke
`current` restore it before the next test. Runs only in CI (`df-stage1`,
job `stage2a (<os>)`, R14-6).
"""

from __future__ import annotations

import errno
import json
import os
import pwd
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from df import doctor, launch, setup_agent, sudoers
from df import platform as plat
from df.config import load_accounts
from df.publisher import perimeter
from df.publisher.github import SelfCheckResult
from df.publisher.request import Response
from tests.df.conftest import stage1_out
from tests.df.fake_github import Installation
from tests.df.observe import observe_root_access
from tests.df.roles_world import (
    FIXTURE_FROM_CHECKOUT,
    INSTALLATION_ID,
    IOLOG_FILE,
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
    as_operator,
    as_user,
    classify_sudo,
    evidence,
    fixture_form,
    found_in,
    host_names,
    install_sudoers_file,
    io_sessions,
    new_request_id,
    pr_request,
    registry_text,
    remove_etc,
    response_of,
    roles_world,
    shown,
    status_request,
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
    """The fixture repo with `FIXTURE_FROM_CHECKOUT` (R14-3)."""
    bare, sha = build_fixture_repo(
        acceptance_top, FIXTURE_FROM_CHECKOUT, name="roles-fixture"
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


#: R14-10: the mechanism of «a password is required» that counts as a
#: refusal (every condition of `sudo_refused` holds).
NO_RULE = "sudo: нет правила (запрос пароля под -n)"


def sudo_refused(
    rw: RolesWorld,
    case: str,
    role: str,
    op: str,
    data: bytes,
    want: SudoDiag,
    *,
    flags: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> str:
    """`role` runs `sudo [flags] -n -u df-publisher df-publish <op> [extra]`
    and sudo itself refuses it, by mechanism `want`; the publisher never
    runs (its journal has no new line). The capture goes to
    `sudo-diag/<os>/<case>.{txt,rc}` (R14-2). For `not-allowed`, R14-10:
    «a password is required» (`password-required`) counts as the refusal
    `NO_RULE` only with exit 1, empty stdout and root's `sudo -n -l -U
    <role> -u df-publisher <the same command>` exiting 1 (sudo's own policy:
    no rule), its control (the role's permitted `perimeter-status`) exiting
    0; any other «password required» is a wrong setup. The mechanism found
    is returned and kept in `sudo-diag/<os>/<case>.mechanism`."""
    before = len(rw.journal_lines())
    done = rw.publish(role, op, data, flags=flags, extra=extra)
    text = done.stderr.decode("utf-8", "replace")
    out = stage1_out()
    form = fixture_form(text, rw.layout.root, host_names())
    write_evidence(out, f"sudo-diag/{OS_DIR}/{case}.txt", form)
    write_evidence(out, f"sudo-diag/{OS_DIR}/{case}.rc", f"{done.returncode}\n")
    kind = classify_sudo(done.returncode, text)
    if kind is None:
        infra(f"нераспознанная диагностика sudo ({case}): {shown(done)}")
    assert done.stdout == b"", shown(done)
    assert len(rw.journal_lines()) == before, "издатель запускался"
    mechanism = str(kind)
    if kind == "password-required":
        if want != "not-allowed" or not no_rule(rw, role, [op, *extra]):
            pytest.fail(
                f"неверная настройка: sudo требует пароль ({case}): {shown(done)}"
            )
        mechanism = NO_RULE
    else:
        assert kind == want, shown(done)
    write_evidence(out, f"sudo-diag/{OS_DIR}/{case}.mechanism", mechanism + "\n")
    return mechanism


def no_rule(rw: RolesWorld, role: str, words: list[str]) -> bool:
    """Root's `sudo -n -l -U <role> -u df-publisher df-publish <words>` says
    the command is not allowed (exit 1), while the same listing of the
    role's permitted `perimeter-status` says it is (exit 0)."""

    def listed(args: list[str]) -> int:
        argv = [SUDO, "-n", "-l", "-U", role, "-u", PUBLISHER]
        result = plat.run_command([*argv, rw.wrapper("df-publish"), *args])
        if not result.launched:
            infra(f"sudo -l -U {role}: не запустился")
        return result.returncode

    control = listed(["perimeter-status"])
    if control != 0:
        infra(f"sudo -l -U {role} perimeter-status: код {control}, ждали 0")
    return listed(words) == 1


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


def test_one_uid_limit(rw: RolesWorld) -> None:
    """Row 13 (a limit, not a protection, spec 5.8): a `df-exec` process of
    the revoked run naming the `allowed` run's `run_id` gets what that run
    gets — the publisher's own checks, nothing more; the normal `df-launch
    run` of the revoked run is refused."""
    as_allowed = status_of(rw, "df-exec", rw.allowed_run, new_request_id("row13a"))
    as_revoked = status_of(rw, "df-exec", rw.allowed_run, new_request_id("row13b"))
    assert (as_allowed.outcome, dict(as_allowed.result)) == (
        as_revoked.outcome,
        dict(as_revoked.result),
    )
    launched = launch_run(rw, rw.revoked_run)
    assert launched.returncode == REFUSED and b"release_revoked" in launched.stderr
    evidence(
        "row13-one-uid-limit.json",
        {
            "kind": "предел гарантии (одна UID), не защита",
            "allowed_run": rw.allowed_run,
            "revoked_run": rw.revoked_run,
            "answer": dict(as_revoked.result),
            "df-launch run (revoked)": shown(launched),
        },
    )


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
            pytest.xfail(f"наблюдатель: {obs.reason}")
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
