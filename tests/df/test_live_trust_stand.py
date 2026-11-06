"""Offline tests of the stage 3a live trust stand's helpers (Task 15).

The live phases of `tests/df/live_trust_stand.py` run only in
`.github/workflows/df-stage3-live.yml`; here: the request table of spec 6.1,
the measurement branch (its journal transitions, the one ref it may write,
the rewind target), the measurement record Task 16 reads, the request count
and the `ps` span, the bootstrap order, the App finding, the rows and the
workflow's shape. Nothing here reads the stand's variables or reaches the
network (`test_live_stand.test_no_test_reads_stand_variables` scans this
module too).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml

from df import setup_trust
from df.paths import Layout
from df.publisher import github, ops
from df.publisher.github import GitHubRefused, GitHubUnavailable
from df.publisher.trust_routes import BRANCH, binding_problem
from df.setup_trust import Refused
from df.stage2_config import Product, TrustBinding
from governance.act_epochs import Epoch, check_transition, parse_journal
from tests.df import live_stand, live_trust_stand, test_setup_trust
from tests.df.live_stand import SKIPPED, Stand, StandArgs, StandSecrets
from tests.df.live_trust_stand import (
    MEASURE_SCHEMA,
    NOT_VERIFIED,
    REQUEST_TABLE,
    ColdMeasure,
    TrustArgs,
    TrustContext,
    TrustSecrets,
    TrustStand,
)
from tests.df.roles_world import RolesWorld
from tests.df.root_world import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "df-stage3-live.yml"
SHA = "a" * 40
FACTS = {
    "trust_id": "11",
    "policy_id": "22",
    "trust": "owner/df-roles-probe-trust",
    "policy": "owner/df-roles-probe-policy",
    "trust_branch": "main",
    "policy_branch": "main",
    "trust_head": "b" * 40,
    "policy_head": "c" * 40,
}


# ---------- the request table (spec 6.1) ----------


def test_request_table_keys_and_credentials() -> None:
    """Every row: a unique key, a credential of the closed set, a named
    behaviour on refusal; both trust repositories are self-reported by each
    token their holder checks (history token, machine user's token)."""
    keys = [row.key for row in REQUEST_TABLE]
    assert len(keys) == len(set(keys))
    assert {row.credential for row in REQUEST_TABLE} <= live_trust_stand.CREDENTIALS
    assert all(row.on_refusal and row.reader for row in REQUEST_TABLE)
    reports = {
        (row.credential, row.key.split(".")[0])
        for row in REQUEST_TABLE
        if row.self_report
    }
    assert reports == {
        ("history", "trust"),
        ("history", "policy"),
        ("machine", "trust"),
        ("machine", "policy"),
    }


def test_request_table_fills_every_live_row() -> None:
    """A live row's request becomes an API path from the stand's facts with
    no placeholder left; a row not called live names why."""
    for row in REQUEST_TABLE:
        if not row.live:
            assert row.why_not_live
            continue
        path = live_trust_stand.fill(row, FACTS)
        assert "{" not in path and not path.startswith("/"), row.key
    commit = next(r for r in REQUEST_TABLE if r.key == "trust.commit")
    assert live_trust_stand.fill(commit, FACTS) == (
        f"repos/{FACTS['trust']}/commits/{FACTS['trust_head']}"
    )


def test_request_table_unknown_fact_is_an_error() -> None:
    row = next(r for r in REQUEST_TABLE if r.key == "trust.branch")
    with pytest.raises(KeyError):
        live_trust_stand.fill(row, {})


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"permissions": {"admin": False, "maintain": False, "push": False}}, None),
        ({"permissions": {"admin": False, "maintain": False, "push": True}}, "push"),
        ({"permissions": {"admin": False, "push": False}}, "maintain"),
        ({"permissions": {"admin": 0, "maintain": False, "push": False}}, "admin"),
        ({}, "permissions"),
        ([], "permissions"),
    ],
)
def test_self_report_needs_three_literal_false(body: object, problem: str) -> None:
    """Spec 6.1: a missing field or a non-bool is never `false`."""
    found = live_trust_stand.self_report_problem(body)
    if problem is None:
        assert found is None
    else:
        assert found is not None and problem in found


def test_judge_needs_200_and_the_self_report() -> None:
    report = next(r for r in REQUEST_TABLE if r.self_report)
    clean = {"permissions": {"admin": False, "maintain": False, "push": False}}
    assert live_trust_stand.judge(report, 200, clean) is None
    assert "HTTP 404" in str(live_trust_stand.judge(report, 404, clean))
    assert "HTTP нет" in str(live_trust_stand.judge(report, None, None))
    dirty = {"permissions": {"admin": True, "maintain": False, "push": False}}
    assert "admin" in str(live_trust_stand.judge(report, 200, dirty))
    plain = next(r for r in REQUEST_TABLE if r.key == "trust.branch")
    assert live_trust_stand.judge(plain, 200, {"commit": {}}) is None
    assert live_trust_stand.judge(plain, 200, None) is not None


# ---------- the measurement branch ----------


def test_measure_branch_is_a_valid_binding_branch() -> None:
    """`measure/<tag>` is a branch git fetches and the publisher binds."""
    branch = live_trust_stand.measure_branch("123-1-macos")
    assert branch == "measure/123-1-macos"
    assert BRANCH.fullmatch(branch)
    binding = TrustBinding("github.com", 11, FACTS["trust"], branch, 22, "public")
    assert binding_problem(binding) is None


@pytest.mark.parametrize(
    "branch", ["main", "measure/other", "measure/123-1-macos/x", "refs/heads/main"]
)
def test_ref_path_writes_only_the_runs_measure_branch(branch: str) -> None:
    """The write token touches one ref: `measure/<tag>` of this run."""
    with pytest.raises(ValueError, match="measure"):
        live_trust_stand.ref_path(FACTS["trust"], branch, "123-1-macos")
    ok = live_trust_stand.ref_path(FACTS["trust"], "measure/123-1-macos", "123-1-macos")
    assert ok == f"/repos/{FACTS['trust']}/git/refs/heads/measure/123-1-macos"


@pytest.mark.parametrize("links", range(6, 61))
def test_journal_steps_spread_the_changes(links: int) -> None:
    steps = live_trust_stand.journal_steps(links, 5)
    assert len(steps) == 5
    assert all(0 < s < links for s in steps)


def test_journal_steps_need_room() -> None:
    with pytest.raises(ValueError):
        live_trust_stand.journal_steps(5, 5)


def bootstrap(anchor: str = SHA) -> tuple[Epoch, ...]:
    text = (
        "schema: approval-policy-epochs/v1\n"
        "epochs:\n"
        "  - epoch: 1\n"
        f'    anchor: "{anchor}"\n'
    )
    return parse_journal(text)


def test_next_journal_is_an_allowed_transition() -> None:
    """Each of the five journal changes is the one allowed mutation (close
    the last open epoch, add the next open one), rendered as YAML the
    checker parses back to the same epochs."""
    epochs = bootstrap()
    for i in range(5):
        anchor = f"{i + 1:040x}"
        new = live_trust_stand.next_journal(epochs, anchor, "2026-11-06T10:00:00+00:00")
        check_transition(epochs, new)
        text = live_trust_stand.render_journal(new)
        assert parse_journal(text) == new
        assert yaml.safe_load(text)["schema"] == "approval-policy-epochs/v1"
        epochs = new
    assert len(epochs) == 6 and not epochs[-1].closed


def test_rewind_target_is_before_the_last_journal_change() -> None:
    """The rewind goes back to a SHA the cold pass already read: the parent
    of the last journal change (the base when that change is the first
    new commit)."""
    commits = [f"{i:040x}" for i in range(1, 11)]
    target = live_trust_stand.rewind_target(SHA, commits, frozenset({2, 5, 8}))
    assert target == commits[7]
    assert live_trust_stand.rewind_target(SHA, commits, frozenset({0})) == SHA


# ---------- the request count and the `ps` span ----------


def rate(used: int, reset: int) -> dict[str, object]:
    return {"resources": {"core": {"used": used, "reset": reset, "limit": 5000}}}


def test_requests_between_one_window() -> None:
    before = live_trust_stand.core_usage(rate(10, 100))
    after = live_trust_stand.core_usage(rate(185, 100))
    assert live_trust_stand.requests_between(before, after) == 175


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (rate(10, 100), rate(5, 200)),  # the hour reset in between
        (rate(10, 100), rate(5, 100)),  # fewer: not one window
        ({}, rate(5, 100)),
        (rate(10, 100), {"resources": {"core": {"used": "x", "reset": 100}}}),
    ],
)
def test_requests_between_unknown(before: object, after: object) -> None:
    got = live_trust_stand.requests_between(
        live_trust_stand.core_usage(before), live_trust_stand.core_usage(after)
    )
    assert got is None


def test_uids_of_reads_ps_lines() -> None:
    text = "    0\n  501\n\n  498\nfoo\n"
    assert live_trust_stand.uids_of(text) == {0, 501, 498}


def test_uid_span_measures_the_seen_window() -> None:
    """The span is first to last sighting plus one period; never seen is
    `None`."""
    span = live_trust_stand.UidSpan(7, period=0.1)
    assert span.seconds is None
    span.note({1, 7}, 10.0)
    span.note({1}, 10.1)
    span.note({7}, 12.0)
    span.note({1}, 12.1)
    assert span.seconds == pytest.approx(2.1)


# ---------- the measurement record (Task 16 reads it) ----------


def measure(**over: object) -> ColdMeasure:
    base = ColdMeasure(
        cell="linux",
        tag="1-1-linux",
        source_sha=SHA,
        admin_digest="sha256:" + "d" * 64,
        branch="measure/1-1-linux",
        tip="e" * 40,
        n_commits=50,
        journal_changes=5,
        base_links=4,
        completed=True,
        state="blocked",
        reason=None,
        last_epoch=6,
        findings=({"kind": "ruleset_missing", "role": "df-trust"},),
        op_trust_seconds=41.2,
        trust_status_seconds=44.9,
        requests=181,
        measured_at="2026-11-06T10:00:00+00:00",
    )
    return replace(base, **over)  # type: ignore[arg-type]


def test_measure_record_round_trip() -> None:
    record = measure()
    data = json.loads(record.to_json())
    assert data["schema"] == MEASURE_SCHEMA
    assert data["m_seconds"] == 44.9
    assert live_trust_stand.parse_measure(record.to_json()) == record


def test_measure_names_what_it_measured() -> None:
    """M4: the route, the kind of cold pass and the approximations are in
    every record; a record without them does not parse."""
    record = replace(measure(), notes=live_trust_stand.MEASURE_NOTES)
    data = json.loads(record.to_json())
    assert (data["route"], data["cold"]) == ("trust-status", "new_root")
    assert "150" in data["notes"] and "admin_digest" in data["notes"]
    del data["cold"]
    with pytest.raises(ValueError):
        live_trust_stand.parse_measure(json.dumps(data))


def test_measure_m_is_the_conservative_bound() -> None:
    """M is the whole `trust-status` call: it contains `op: trust`."""
    assert measure().m_seconds == 44.9
    assert measure(op_trust_seconds=None).m_seconds == 44.9
    assert measure(completed=False).m_seconds is None


@pytest.mark.parametrize(
    "broken",
    [
        {"schema": "x"},
        {"n_commits": "50"},
        {"completed": 1},
        {"trust_status_seconds": -1},
        {"extra": 1},
    ],
)
def test_parse_measure_refuses(broken: dict[str, object]) -> None:
    data = json.loads(measure().to_json())
    data.update(broken)
    with pytest.raises(ValueError):
        live_trust_stand.parse_measure(json.dumps(data))


# ---------- bootstrap order, App finding ----------


def test_bootstrap_order() -> None:
    """Spec 6.2 item 1/4: the initial base is no PR merge, every later link
    arrived by a merged PR."""
    ok = [("r" * 40, 0), ("s" * 40, 1), ("t" * 40, 2)]
    assert live_trust_stand.bootstrap_problems(ok) == []
    bad = [("r" * 40, 1), ("s" * 40, 0)]
    problems = live_trust_stand.bootstrap_problems(bad)
    assert len(problems) == 2
    assert live_trust_stand.bootstrap_problems([]) == ["пустая история"]


def test_trust_source_problems_filter() -> None:
    mine = ops.TRUST_SOURCE_PROBLEM.format(id=5, role="df-trust")
    problems = [mine, "app_installation_outside_products: 9"]
    assert live_trust_stand.trust_source_problems(problems) == [mine]


def test_app_fixture_binding_is_another_root() -> None:
    """The App row's fixture binding names the product as `df-trust`: its
    root is not the bound one, so nothing of the real root moves."""
    real = TrustBinding("github.com", 11, FACTS["trust"], "main", 22, "public")
    product = Product(33, "owner/df-roles-probe", "write", 1, 2, "bot", "/var/empty")
    fixture = live_trust_stand.app_fixture(real, product)
    assert fixture.df_trust_repository_id == 33
    assert fixture.root_id != real.root_id
    assert fixture.approval_policy_repository_id == 22


# ---------- secrets, arguments, rows ----------


def test_read_trust_secrets(tmp_path: Path) -> None:
    """Every value joins the guard; the write token is optional."""
    history = "github_pat_" + "h" * 40
    pat = "github_pat_" + "p" * 40
    (tmp_path / live_trust_stand.HISTORY_FILE).write_text(history + "\n")
    (tmp_path / live_trust_stand.PAT_FILE).write_text(pat + "\n")
    got = live_trust_stand.read_trust_secrets(tmp_path)
    assert got.write_token is None
    assert got.history_token == history and got.perimeter_pat == pat
    assert history not in repr(got) and pat not in repr(got)
    assert live_stand.GUARD.found(history) and live_stand.GUARD.found(pat)
    write = "github_pat_" + "w" * 40
    (tmp_path / live_trust_stand.WRITE_FILE).write_text(write)
    assert live_trust_stand.read_trust_secrets(tmp_path).write_token == write
    assert live_stand.GUARD.found(write)


def args() -> TrustArgs:
    return TrustArgs(
        Path("/out"), Path("/s"), SHA, 33, 1, 2, 22, 11, "main", "1-1-linux"
    )


def test_trust_args_give_the_2b_args() -> None:
    assert args().stand_args() == StandArgs(
        Path("/out"), Path("/s"), SHA, 33, 1, 2, "1-1-linux"
    )


def test_trust_stand_keeps_the_2b_stand() -> None:
    """The 2b rows (10-12, 9) take a `TrustStand` as their `Stand`."""
    base = Stand(
        RolesWorld.__new__(RolesWorld),
        args().stand_args(),
        StandSecrets(Path("/k"), Path("/t"), "x"),
        live_stand.MachineGitHub("x"),
        Product(33, "owner/df-roles-probe", "write", 1, 2, "bot", "/var/empty"),
        "main",
        "app[bot]",
    )
    ctx = TrustContext(args(), TrustSecrets(Path("/h"), "h", "p", None))
    st = live_trust_stand.trust_stand(base, ctx)
    assert isinstance(st, TrustStand) and isinstance(st, Stand)
    assert all(getattr(st, f.name) is getattr(base, f.name) for f in fields(Stand))
    assert live_trust_stand.ctx_of(st) is ctx
    with pytest.raises(TypeError):
        live_trust_stand.ctx_of(base)


def test_rows_reuse_the_2b_stop_rows() -> None:
    by_id = {row.id: row for row in live_trust_stand.ROWS}
    assert by_id["10"].fn is live_stand.row10_seed
    assert by_id["11"].fn is live_stand.row11_head_change
    assert by_id["12"].fn is live_stand.row12_dropped_answer
    assert by_id["10"].needs == ("Т3",) and by_id["12"].needs == ("Т3",)


def test_row_dependencies_name_existing_earlier_rows() -> None:
    seen: set[str] = set()
    for row in live_trust_stand.ROWS:
        assert set(row.needs) <= seen, row.name
        assert row.id not in seen
        seen.add(row.id)
    assert live_trust_stand.CLEANUP.id not in seen


@pytest.mark.parametrize("row_id", [row.id for row in live_trust_stand.ROWS])
def test_not_verified_of_any_row_is_never_a_pass(row_id: str) -> None:
    """Whichever row says `not_verified`: it is not `pass`, every row that
    needs it is skipped, and the run is not green."""
    rows = tuple(
        live_stand.Row(
            r.id,
            r.title,
            (lambda st: live_trust_stand.not_verified("нет"))
            if r.id == row_id
            else (lambda st: {}),
            r.needs,
        )
        for r in live_trust_stand.ROWS
    )
    cleanup = live_stand.Row("9", "c", lambda st: {})
    results = live_stand.run_rows(Stand.__new__(Stand), rows=rows, cleanup=cleanup)
    by_id = {r.id: results[r.name]["status"] for r in rows}
    assert by_id[row_id] == NOT_VERIFIED
    for r in rows:
        if row_id in r.needs:
            assert str(by_id[r.id]).startswith(SKIPPED)
    assert live_stand.verdict(results, expected=len(rows) + 1) == (
        live_stand.EXIT_FAILED
    )


def test_not_verified_is_never_a_pass() -> None:
    """A row without its credential says so, is not `pass`, and its
    dependents are skipped; the run is not green (spec 10)."""
    rows = (
        live_stand.Row("A", "a", lambda st: live_trust_stand.not_verified("нет")),
        live_stand.Row("B", "b", lambda st: {}, ("A",)),
    )
    cleanup = live_stand.Row("9", "c", lambda st: {})
    results = live_stand.run_rows(Stand.__new__(Stand), rows=rows, cleanup=cleanup)
    assert results["A a"]["status"] == NOT_VERIFIED
    assert str(results["B b"]["status"]) == SKIPPED
    assert live_stand.verdict(results, expected=3) == live_stand.EXIT_FAILED


def test_status_reading_of_a_trust_status() -> None:
    """The measurement reads the parsed answer only (no token, no path)."""
    assert live_trust_stand.status_record(Refused("нет ответа")) == {
        "state": None,
        "reason": "нет ответа",
    }


def test_phases_refuse_outside_ci_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit, match="R15-6"):
        live_trust_stand.main(["wipe", "--out", "/nonexistent", "--secrets", "/n"])


def test_stand_phase_checks_imports_before_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_gate(*args: object, **kwargs: object) -> object:
        raise AssertionError("the gate ran")

    monkeypatch.setattr(live_trust_stand, "require_ci_root", lambda: None)
    monkeypatch.setattr(live_trust_stand, "gate", no_gate)
    monkeypatch.setattr(live_trust_stand, "STAND_IMPORTS", ("df_absent_module",))
    argv = ["stand", "--source-sha", SHA, "--out", str(tmp_path)]
    argv += ["--secrets", str(tmp_path / "none"), "--repo-id", "1"]
    argv += ["--app-id", "2", "--installation-id", "3", "--policy-repo-id", "4"]
    argv += ["--trust-repo-id", "5", "--trust-branch", "main", "--tag", "t-1"]
    assert live_trust_stand.main(argv) == live_trust_stand.EXIT_INFRA


# ---------- the workflow ----------


def workflow() -> dict[object, object]:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(data, dict)
    return data


def test_workflow_is_owner_approved_master_only() -> None:
    """`workflow_dispatch` only, `master` only, the owner's environment,
    read-only token, one cell at a time (shared stand and quota)."""
    data = workflow()
    on = data.get("on", data.get(True))
    assert on == {"workflow_dispatch": None}
    assert data["permissions"] == {"contents": "read"}
    (job,) = data["jobs"].values()
    assert job["if"] == "github.ref == 'refs/heads/master'"
    assert job["environment"] == "df-stage3-live"
    assert job["strategy"]["max-parallel"] == 1
    oses = {cell["os"] for cell in job["strategy"]["matrix"]["include"]}
    assert oses == {"macos-15", "ubuntu-24.04"}


def test_workflow_pins_actions_and_drops_credentials() -> None:
    text = WORKFLOW.read_text()
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses)
    assert "persist-credentials: false" in text


def test_workflow_names_the_secrets_and_wipes_them() -> None:
    """Secrets by name only, written to files; the wipe and the upload run
    always; the measurement is its own named artifact."""
    text = WORKFLOW.read_text()
    for name in (
        "DF_STAND_APP_KEY",
        "DF_STAND_MACHINE_TOKEN",
        "DF_STAND_HISTORY_TOKEN",
        "DF_STAND_PERIMETER_PAT",
        "DF_STAND_TRUST_WRITE_TOKEN",
    ):
        assert f"secrets.{name}" in text
    (job,) = workflow()["jobs"].values()
    steps = {s.get("name", s.get("uses")): s for s in job["steps"]}
    assert steps["wipe the stand's secrets"]["if"] == "always()"
    uploads = [s for s in job["steps"] if "upload-artifact" in str(s.get("uses"))]
    names = {u["with"]["name"] for u in uploads}
    assert "df-stage3-cold-measure-${{ matrix.cell }}" in names
    assert all(u["if"] == "always()" for u in uploads)
    assert live_trust_stand.MEASURE_FILE in text


def test_workflow_runs_the_three_phases() -> None:
    text = WORKFLOW.read_text()
    for phase in ("install", "stand", "wipe"):
        assert f"-m tests.df.live_trust_stand {phase}" in text
    assert "uv sync --frozen --group governance" in text


# ---------- fix round 1: I1, I2, M1, M2, M5, M6 ----------

HISTORY = "github_pat_" + "h" * 40
WRITE = "github_pat_" + "w" * 40
CLEAN = {"permissions": {"admin": False, "maintain": False, "push": False}}
REAL = TrustBinding("github.com", 11, FACTS["trust"], "main", 22, "public")


def fake_stand(tmp_path: Path, **ctx: object) -> TrustStand:
    """A `TrustStand` whose evidence goes under `tmp_path` (no world)."""
    a = replace(args(), out=tmp_path)
    base = Stand(
        RolesWorld.__new__(RolesWorld),
        a.stand_args(),
        StandSecrets(Path("/k"), Path("/t"), "ghp_" + "m" * 36),
        live_stand.MachineGitHub("x"),
        Product(33, "owner/df-roles-probe", "write", 1, 2, "bot", "/var/empty"),
        "main",
        "app[bot]",
    )
    secrets = TrustSecrets(Path("/h"), HISTORY, "p" * 20, WRITE)
    context = TrustContext(a, secrets, binding=REAL)
    for key, value in ctx.items():
        setattr(context, key, value)
    return live_trust_stand.trust_stand(base, context)


def evidence(tmp_path: Path, name: str) -> Path:
    return tmp_path / "live" / live_stand.OS_DIR / name


APPS = tuple(
    f"{key}: установки сторонних App PAT не видит"
    for key in live_trust_stand.ALWAYS_UNVERIFIED
)


@pytest.mark.parametrize(
    ("calls", "unverified", "problem"),
    [
        ((), (), "нет"),
        ((("repos/o/p", 200),), (), None),
        ((("repos/o/p", 200),), APPS, None),
        ((("repos/o/p/protection", 404),), APPS, None),
        ((("repos/o/p", 200),), (*APPS, "hooks@df-trust: не проверено (403)"), "hooks"),
        ((("repos/o/p", 200),), ("allow_rebase_merge@df-trust",), "rebase"),
        ((("repos/o/p", 200),), ("apps@other: x",), "apps@other"),
    ],
)
def test_human_check_problem(
    calls: tuple[tuple[str, int | None], ...],
    unverified: tuple[str, ...],
    problem: str | None,
) -> None:
    """Fix round 2: the check's own `unverified` decides, not raw statuses
    (a 404 «Branch not protected» under a ruleset is no gap); only the
    closed `ALWAYS_UNVERIFIED` is exempt; no call at all is a gap."""
    got = live_trust_stand.human_check_problem(calls, unverified)
    if problem is None:
        assert got is None
    else:
        assert got is not None and problem in got


def table_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: object
) -> tuple[dict[str, object], dict[str, object]]:
    """Т2 over Т1's `report` (adopted as the stand does), every live
    request answering; its result and its evidence."""
    st = fake_stand(tmp_path)
    live_trust_stand.adopt_report(
        live_trust_stand.ctx_of(st), cast(setup_trust.PerimeterReport, report)
    )
    monkeypatch.setattr(live_trust_stand, "table_facts", lambda st: FACTS)
    monkeypatch.setattr(live_trust_stand, "get", lambda t, path: (200, CLEAN))
    result = live_trust_stand.row_request_table(st)
    recorded = json.loads(evidence(tmp_path, "t2-request-table.json").read_text())
    return result, recorded


def test_request_table_passes_on_a_good_perimeter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A perimeter set up as spec 6.2 asks (`good_github`: rulesets only, so
    `branches/<b>/protection` is 404) passes Т2."""
    report = test_setup_trust.check(test_setup_trust.good_github())
    assert report.ok and any(status == 404 for _, status in report.calls)
    result, recorded = table_row(tmp_path, monkeypatch, report)
    assert "status" not in result
    assert recorded["human_gaps"] == []


def test_request_table_is_not_verified_by_a_real_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check's report names a premise it could not verify (hooks 403):
    Т2 records its table, then is `not_verified`."""
    fake = test_setup_trust.good_github()
    fake.answers[f"repos/{test_setup_trust.TRUST}/hooks?per_page=100"] = (403, {})
    report = test_setup_trust.check(fake)
    result, recorded = table_row(tmp_path, monkeypatch, report)
    assert result["status"] == NOT_VERIFIED
    assert recorded["human_gaps"] and all("hooks" in g for g in recorded["human_gaps"])


def test_request_table_is_not_verified_without_human_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = fake_stand(tmp_path)
    monkeypatch.setattr(live_trust_stand, "table_facts", lambda st: FACTS)
    monkeypatch.setattr(live_trust_stand, "get", lambda t, path: (200, CLEAN))
    assert live_trust_stand.row_request_table(st)["status"] == NOT_VERIFIED


def test_request_table_live_failure_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = fake_stand(tmp_path, calls=(("repos/o/p", 200),))
    monkeypatch.setattr(live_trust_stand, "table_facts", lambda st: FACTS)
    monkeypatch.setattr(live_trust_stand, "get", lambda t, path: (404, None))
    with pytest.raises(live_stand.RowFailed, match="таблица запросов"):
        live_trust_stand.row_request_table(st)


RULES_OK = [{"type": "pull_request"}, {"type": "non_fast_forward"}, {"type": "x"}]


@pytest.mark.parametrize(
    ("policy", "rules", "count"),
    [
        ((200, CLEAN), (200, RULES_OK), 0),
        ((200, {"permissions": {"push": True}}), (200, RULES_OK), 1),
        ((200, {}), (200, RULES_OK), 1),
        ((404, None), (200, RULES_OK), 1),
        ((200, CLEAN), (200, [{"type": "pull_request"}]), 1),
        ((200, CLEAN), (403, None), 1),
        ((200, {"permissions": {"push": True}}), (200, []), 2),
    ],
)
def test_scope_problems(
    policy: tuple[int | None, object], rules: tuple[int | None, object], count: int
) -> None:
    """I2: the write token reaches `approval-policy` without push (its
    repository selection), and the trusted branch is PR-only and without
    force-push."""
    assert len(live_trust_stand.scope_problems(policy, rules)) == count


def test_cold_measure_writes_nothing_without_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: a scope probe that does not hold is `not_verified`, recorded,
    and no write is made."""
    st = fake_stand(tmp_path)
    monkeypatch.setattr(live_trust_stand, "get", lambda t, path: (200, CLEAN))

    def no_write(*args: object, **kwargs: object) -> object:
        raise AssertionError("a write was made")

    monkeypatch.setattr(github, "call", no_write)
    monkeypatch.setattr(live_trust_stand, "build_measure", no_write)
    result = live_trust_stand.row_cold_measure(st)
    assert result["status"] == NOT_VERIFIED
    scope = json.loads(evidence(tmp_path, "t5-write-scope.json").read_text())
    assert scope["problems"] and not live_trust_stand.ctx_of(st).created


def test_cold_measure_without_the_token(tmp_path: Path) -> None:
    st = fake_stand(tmp_path)
    ctx = live_trust_stand.ctx_of(st)
    ctx.secrets = replace(ctx.secrets, write_token=None)
    assert live_trust_stand.row_cold_measure(st)["status"] == NOT_VERIFIED


ND = ("not_descendant", "df-trust")
RM = ("ruleset_missing", "df-trust")


@pytest.mark.parametrize(
    ("before", "after", "ok"),
    [
        ({RM}, {RM, ND}, True),
        ({RM}, {RM}, False),
        ({RM, ND}, {RM, ND}, False),
        (set(), {("not_descendant", "approval-policy")}, False),
    ],
)
def test_rewritten_needs_a_new_not_descendant(
    before: set[tuple[str, str]], after: set[tuple[str, str]], ok: bool
) -> None:
    """M5: the real check of Т6 (Т5's `ruleset_missing` makes «not clear»
    vacuous)."""
    got = live_trust_stand.rewritten_problem(frozenset(before), frozenset(after))
    assert (got is None) is ok


def test_kinds_of() -> None:
    findings = ({"kind": "a", "role": "b", "detail": "x"},)
    assert live_trust_stand.kinds_of(findings) == {("a", "b")}


def fake_call(seen: list[tuple[str, str]], fail: Exception | None = None) -> object:
    def call(method: str, path: str, token: str, body: object = None) -> object:
        assert token == WRITE
        seen.append((method, path))
        if fail is not None:
            raise fail
        return 204, None

    return call


@pytest.mark.parametrize(("status", "left"), [(404, False), (200, True)])
def test_drop_measure_deletes_and_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, left: bool
) -> None:
    """M6: the measurement branch is deleted, then read back absent."""
    st = fake_stand(tmp_path, created=True)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(github, "call", fake_call(seen))
    monkeypatch.setattr(live_trust_stand, "get", lambda t, path: (status, None))
    got = live_trust_stand.drop_measure(st)
    assert seen == [
        ("DELETE", f"/repos/{FACTS['trust']}/git/refs/heads/measure/1-1-linux")
    ]
    assert got["left"] is left


def test_drop_measure_refused_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = fake_stand(tmp_path, created=True)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(github, "call", fake_call(seen, GitHubRefused("no", 403)))
    got = live_trust_stand.drop_measure(st)
    assert got == {"created": True, "left": True, "problem": "GitHubRefused"}


def test_drop_measure_without_a_branch_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = fake_stand(tmp_path, binding=None)
    monkeypatch.setattr(github, "call", fake_call([], AssertionError("write")))
    assert live_trust_stand.drop_measure(st) == {"created": False, "left": False}


@pytest.mark.parametrize(
    ("fail", "word"),
    [
        (None, "удалена"),
        (GitHubRefused("Reference does not exist", 422), "нет ветки"),
        (GitHubRefused("Not Found", 404), "нет ветки"),
        (GitHubRefused("Forbidden", 403), "не удалось: 403"),
        (GitHubUnavailable("down"), "не удалось: GitHubUnavailable"),
    ],
)
def test_sweep_measure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: Exception | None,
    word: str,
) -> None:
    """M6: the always-run sweep of `wipe`: one word, never exception text."""
    token = tmp_path / live_trust_stand.WRITE_FILE
    token.write_text(WRITE + "\n")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(github, "call", fake_call(seen, fail))
    monkeypatch.setattr(
        live_trust_stand, "repo_facts", lambda t, i: (FACTS["trust"], "main")
    )
    assert live_trust_stand.sweep_measure(token, 11, "1-1-linux") == word
    assert seen == [
        ("DELETE", f"/repos/{FACTS['trust']}/git/refs/heads/measure/1-1-linux")
    ]


def test_empty_secret_is_named(tmp_path: Path) -> None:
    """M1: an unset required secret is a named infrastructure failure."""
    (tmp_path / live_trust_stand.HISTORY_FILE).write_text("\n")
    (tmp_path / live_trust_stand.PAT_FILE).write_text("github_pat_" + "p" * 40)
    with pytest.raises(pytest.fail.Exception, match="history-token пуст"):
        live_trust_stand.read_trust_secrets(tmp_path)


class _Stop(Exception):
    pass


@pytest.mark.parametrize(
    ("private", "public", "refused"),
    [
        (True, False, False),
        (True, True, True),
        (False, False, True),
        (False, True, False),
    ],
)
def test_2b_set_up_checks_the_stand_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    private: bool,
    public: bool,
    refused: bool,
) -> None:
    """M6: `set_up` refuses a stand of the other visibility; the default is
    2b's private stand."""
    ran = subprocess.CompletedProcess(["x"], 0, b"", b"")
    monkeypatch.setattr(live_stand, "as_operator", lambda argv, **kw: ran)
    monkeypatch.setattr(live_stand, "check", lambda done, what: None)

    def call(self: object, method: str, path: str, body: object = None) -> object:
        if path == "/user":
            return {"login": "betta-tester"}
        return {"private": not public, "full_name": "owner/df-roles-probe"}

    def stop(*args: object, **kwargs: object) -> None:
        raise _Stop

    monkeypatch.setattr(live_stand.MachineGitHub, "call", call)
    monkeypatch.setattr(live_stand, "write_etc", stop)
    rw = cast(RolesWorld, SimpleNamespace(layout=Layout(tmp_path)))
    secrets = StandSecrets(Path("/k"), Path("/t"), "ghp_" + "m" * 36)
    kwargs = {} if private else {"private": False}
    expected = pytest.fail.Exception if refused else _Stop
    with pytest.raises(expected):
        live_stand.set_up(rw, args().stand_args(), secrets, **kwargs)


def test_governance_and_test_modules_load_lazily() -> None:
    """M2, M3: the stand's module imports neither the governance group nor a
    test module at its top, so `STAND_IMPORTS` can name a missing one."""
    tree = ast.parse(Path(live_trust_stand.__file__).read_text())
    top = [
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not [m for m in top if m.startswith("governance")]
    assert not [m for m in top if m.split(".")[-1].startswith("test_")]
    assert "governance.act_epochs" in live_trust_stand.STAND_IMPORTS
