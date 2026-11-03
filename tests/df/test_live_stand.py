"""Offline tests of the live stand's helpers (Task 15, R15-6).

The live phases of `tests/df/live_stand.py` run only in
`.github/workflows/df-stage2-live.yml`; here: the provenance check before
any secret is read (`test_live_provenance`), the evidence guard, the row
verdicts, the rows' dependencies and the helpers of the merge rows (stage
2b, Task 11: the seed, the stop-PR, the proxy log, the verdicts of rows 11
and 12, the cleanup). The gate proxy has its own tests
(`tests/df/test_gate_proxy.py`). Nothing here reads the stand's variables
or reaches the network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from df.admin import expected_admin_digest
from df.admin_tree import CONTRACT_FILES, governance_files
from df.build import GIT_ISOLATION
from df.config import ReleaseEntry, ReleasesConfig, dump_releases
from df.paths import Layout
from df.publisher.merge import PREMISE, parse_patterns, route
from df.publisher.request import Response
from df.release import RELEASE_JSON
from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    StopRecord,
    parse_stop,
    stop_path,
)
from governance.act_request import NodeEntry, NodeRequest, manifest_path, parse_manifest
from governance.act_time import Unavailable
from governance.approval_branches import stop_branch
from tests.df import gate_proxy, live_stand
from tests.df.live_stand import (
    SKIPPED,
    ProvenanceError,
    RowFailed,
    StandSecrets,
)
from tests.df.roles_world import FIXTURE_FROM_CHECKOUT, OS_DIR, RolesWorld
from tests.df.root_world import INFRA, REPO_ROOT, SYSTEM_GIT, infra
from tests.df.site_world import prime_cache, write_checkout

#: The approval-branch patterns `merge` routing reads (one of `CONTRACT_FILES`).
PATTERNS = "contracts/approval-branches/v1/patterns.env"

RID = "r-live-1"
GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    **GIT_ISOLATION,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.invalid",
}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [str(SYSTEM_GIT), "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# ---------- provenance (r3 N2) ----------


@dataclass
class ProvWorld:
    """A checkout, a fixture repo built from it and an installed layout."""

    checkout: Path
    source_sha: str
    fixture: Path
    fixture_sha: str
    layout: Layout
    top: Path
    secrets: Path
    reads: list[Path] = field(default_factory=list)

    def state(self, fixture_sha: str) -> None:
        data = {"fixture_repo": str(self.fixture), "fixture_sha": fixture_sha}
        (self.top / live_stand.STATE_FILE).write_text(json.dumps(data))

    def release_names(self, sha: str) -> None:
        text = json.dumps({"repos": [{"name": "devtools", "sha": sha}]})
        (self.layout.release_dir(RID) / RELEASE_JSON).write_text(text)

    def registry(self, admin_digest: str) -> None:
        entry = ReleaseEntry(
            RID, "sha256:" + "1" * 64, admin_digest, "3.12.13", "allowed", None
        )
        text = dump_releases(ReleasesConfig((entry,)))
        (self.layout.etc / "releases.toml").write_text(text)

    def refit(self, message: str) -> str:
        """A new fixture commit, named by the state and `release.json`."""
        sha = commit_all(self.fixture, message)
        self.fixture_sha = sha
        self.state(sha)
        self.release_names(sha)
        return sha

    def read(self, directory: Path) -> StandSecrets:
        self.reads.append(directory)
        return StandSecrets(directory / "k", directory / "t", "ghp_notASecret")

    def gate(self, source_sha: str | None = None) -> live_stand.Provenance:
        prov, _secrets = live_stand.gate(
            self.source_sha if source_sha is None else source_sha,
            self.secrets,
            top=self.top,
            layout=self.layout,
            checkout=self.checkout,
            read=self.read,
        )
        return prov


@pytest.fixture()
def pw(tmp_path: Path) -> ProvWorld:
    """Install-time state as the install phase leaves it, all consistent."""
    base = Path(os.path.realpath(tmp_path))
    checkout = base / "checkout"
    (checkout / "df" / "sub").mkdir(parents=True)
    write_checkout(checkout)  # the rest of the admin tree (2b Task 5)
    (checkout / "df" / "__init__.py").write_text('"""df"""\n')
    (checkout / "df" / "sub" / "tool.py").write_text("print(1)\n")
    patterns = checkout / PATTERNS
    patterns.parent.mkdir(parents=True, exist_ok=True)
    patterns.write_text("APPROVAL_FINALIZE_SUFFIX=-final\n")
    (checkout / "README.md").write_text("not carried\n")
    git(checkout, "init", "-q", "--template=")
    source_sha = commit_all(checkout, "checkout")
    fixture = base / "fixture"
    fixture.mkdir()
    (fixture / "proj.txt").write_text("fixture project\n")
    for relative in FIXTURE_FROM_CHECKOUT:
        origin, target = checkout / relative, fixture / relative
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    git(fixture, "init", "-q", "--template=")
    fixture_sha = commit_all(fixture, "fixture")
    layout = Layout(base / "root")
    layout.release_dir(RID).mkdir(parents=True)
    layout.etc.mkdir(parents=True)
    os.symlink(f"releases/{RID}", layout.current)
    top = base / "top"
    top.mkdir()
    world = ProvWorld(
        checkout, source_sha, fixture, fixture_sha, layout, top, base / "secrets"
    )
    world.state(fixture_sha)
    world.release_names(fixture_sha)
    layout.admin_home.mkdir(mode=0o700, parents=True)
    prime_cache(layout)
    world.registry(expected_admin_digest(checkout, layout=layout, git=SYSTEM_GIT))
    return world


def change_fixture_df(pw: ProvWorld) -> None:
    (pw.fixture / "df" / "sub" / "tool.py").write_text("print(2)\n")
    pw.refit("df changed")


def drop_patterns(pw: ProvWorld) -> None:
    git(pw.fixture, "rm", "-q", PATTERNS)
    pw.refit("no patterns")


def substitute_fixture_sha(pw: ProvWorld) -> None:
    """Same blobs, another commit: only `release.json` tells them apart."""
    pw.state(commit_all(pw.fixture, "empty"))


def change_checkout_df(pw: ProvWorld) -> None:
    """The checkout's `df/` differs from what the install recorded."""
    (pw.checkout / "df" / "__init__.py").write_text('"""edited"""\n')


def other_registry_digest(pw: ProvWorld) -> None:
    pw.registry("sha256:" + "2" * 64)


def lose_state(pw: ProvWorld) -> None:
    (pw.top / live_stand.STATE_FILE).unlink()


@pytest.mark.parametrize(
    ("tamper", "why"),
    [
        (change_fixture_df, "расходятся"),
        (drop_patterns, "нет contracts/approval-branches/v1/patterns.env"),
        (substitute_fixture_sha, "release.json: devtools"),
        (change_checkout_df, "admin_digest"),
        (other_registry_digest, "admin_digest"),
        (lose_state, "state.json"),
    ],
)
def test_live_provenance(
    pw: ProvWorld, tamper: Callable[[ProvWorld], None], why: str
) -> None:
    """r3 N2: a changed `df/` file (in the fixture or the checkout), a
    missing path of `FIXTURE_FROM_CHECKOUT`, a substituted `fixture_sha`, a
    registry digest not of the checkout or a lost install state: the
    provenance check fails before any secret is read."""
    tamper(pw)
    with pytest.raises(ProvenanceError, match=re.escape(why)):
        pw.gate()
    assert pw.reads == []


def test_live_provenance_other_source_sha(pw: ProvWorld) -> None:
    """`source_sha` not the checkout's commit (an older one): refused before
    the secrets."""
    (pw.checkout / "README.md").write_text("moved on\n")
    commit_all(pw.checkout, "later")
    with pytest.raises(ProvenanceError, match="source_sha"):
        pw.gate()
    assert pw.reads == []


def test_live_provenance_control(pw: ProvWorld) -> None:
    """Control: the untampered install passes and only then are the secrets
    read; both identities and the digest are reported, not equated."""
    prov = pw.gate()
    assert pw.reads == [pw.secrets]
    assert (prov.source_sha, prov.fixture_sha) == (pw.source_sha, pw.fixture_sha)
    assert prov.source_sha != prov.fixture_sha
    expected = expected_admin_digest(pw.checkout, layout=pw.layout, git=SYSTEM_GIT)
    assert prov.admin_digest == expected
    assert set(prov.blobs) == {
        "df/__init__.py",
        "df/sub/tool.py",
        *governance_files(),
        *CONTRACT_FILES,
    }


def test_parse_ls_tree_refuses_unknown_lines() -> None:
    text = "100644 blob " + "a" * 40 + "\tdf/x.py\0" + "040000 tree " + "b" * 40
    assert live_stand.parse_ls_tree(text + "\tdf\0") == {"df/x.py": "a" * 40}
    with pytest.raises(ProvenanceError):
        live_stand.parse_ls_tree("garbage\0")


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"repos": []}),
        json.dumps({"repos": [{"name": "devtools"}]}),
        json.dumps({"repos": [{"name": "devtools", "sha": "a"}] * 2}),
    ],
)
def test_release_member_sha_refuses(text: str) -> None:
    with pytest.raises(ProvenanceError):
        live_stand.release_member_sha(text, "devtools")


# ---------- secrets and evidence ----------


def test_record_refuses_a_secret(tmp_path: Path) -> None:
    """Evidence holding a remembered secret (or its git header form) is not
    written; other evidence is."""
    token = f"ghs_{os.urandom(12).hex()}"
    live_stand.guard_token(token, "x-access-token")
    header = live_stand.auth_header("x-access-token", token)
    for leak in (token, header):
        with pytest.raises(pytest.fail.Exception, match=INFRA):
            live_stand.record(tmp_path, "leak.json", {"x": leak})
    assert not (tmp_path / "live" / OS_DIR / "leak.json").exists()
    live_stand.record(tmp_path, "ok.json", {"x": "clean"})
    assert (tmp_path / "live" / OS_DIR / "ok.json").is_file()


def test_scan_tree_and_key_needles(tmp_path: Path) -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nQUJD" + "x" * 60 + "\n-----END RSA"
    needles = live_stand.key_needles(pem)
    assert needles == ["QUJD" + "x" * 60]
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "leak.txt").write_text(f"..{needles[0]}..")
    (tmp_path / "clean.txt").write_text("nothing")
    assert live_stand.scan_tree(tmp_path, needles) == [tmp_path / "a" / "leak.txt"]
    assert live_stand.scan_tree(tmp_path / "absent", needles) == []


def test_token_type_names_the_kind_only() -> None:
    kinds = {
        "ghp_abc": "classic PAT (ghp_)",
        "github_pat_abc": "fine-grained PAT (github_pat_)",
        "xyz": "неизвестный",
    }
    for value, kind in kinds.items():
        secrets = StandSecrets(Path("/k"), Path("/t"), value)
        assert secrets.token_type == kind
        assert value not in repr(secrets)


# ---------- rows ----------


def test_run_row_classifies() -> None:
    """`pass`, `fail` for the mechanism, `инфраструктура` for a failed
    control or any other error (a script bug is never a pass)."""
    st = object()

    def passes(_st: object) -> dict[str, object]:
        return {"x": 1}

    def fails(_st: object) -> dict[str, object]:
        raise RowFailed("mechanism")

    def asserts(_st: object) -> dict[str, object]:
        raise AssertionError("assert")

    def control(_st: object) -> dict[str, object]:
        infra("control")
        return {}

    def crashes(_st: object) -> dict[str, object]:
        raise KeyError("bug")

    got = {
        fn.__name__: live_stand.run_row(st, fn)["status"]  # type: ignore[arg-type]
        for fn in (passes, fails, asserts, control, crashes)
    }
    assert got == {
        "passes": "pass",
        "fails": "fail",
        "asserts": "fail",
        "control": "инфраструктура",
        "crashes": "инфраструктура",
    }


def test_verdict_needs_every_row_and_the_cleanup() -> None:
    names = [r.name for r in live_stand.ROWS] + [live_stand.CLEANUP.name]
    ok = {n: {"status": "pass"} for n in names}
    assert live_stand.verdict(ok) == live_stand.EXIT_PASS
    assert live_stand.verdict(dict(list(ok.items())[:-1])) == live_stand.EXIT_FAILED
    failed = {**ok, names[3]: {"status": "fail"}}
    assert live_stand.verdict(failed) == live_stand.EXIT_FAILED
    broken = {**failed, names[0]: {"status": "инфраструктура"}}
    assert live_stand.verdict(broken) == live_stand.EXIT_INFRA
    skipped = {**ok, names[2]: {"status": live_stand.SKIPPED}}
    assert live_stand.verdict(skipped) == live_stand.EXIT_FAILED


def test_rows_skip_only_on_a_failed_prerequisite() -> None:
    """Finding 3: a row runs unless a row it needs did not pass; a skipped
    row is recorded, never a pass; independent rows still run; the cleanup
    always runs and the overall verdict is a failure."""
    calls: list[str] = []

    def ok(name: str) -> Callable[[object], dict[str, object]]:
        def fn(_st: object) -> dict[str, object]:
            calls.append(name)
            return {}

        return fn

    def bad(_st: object) -> dict[str, object]:
        calls.append("b")
        raise RowFailed("wording")

    Row = live_stand.Row
    rows = (
        Row("a", "a", ok("a")),  # type: ignore[arg-type]
        Row("b", "b", bad, ("a",)),  # type: ignore[arg-type]
        Row("c", "c", ok("c"), ("b",)),  # type: ignore[arg-type]
        Row("d", "d", ok("d"), ("c",)),  # type: ignore[arg-type]
        Row("e", "e", ok("e"), ("a",)),  # type: ignore[arg-type]
    )
    cleanup = Row("z", "z", ok("z"))  # type: ignore[arg-type]
    results = live_stand.run_rows(object(), rows, cleanup)  # type: ignore[arg-type]
    assert calls == ["a", "b", "e", "z"]
    statuses = {k: v["status"] for k, v in results.items()}
    assert statuses == {
        "a a": "pass",
        "b b": "fail",
        "c c": live_stand.SKIPPED,
        "d d": live_stand.SKIPPED,
        "e e": "pass",
        "z z": "pass",
    }
    assert "зависит от b" in str(results["c c"]["reason"])
    assert "зависит от c" in str(results["d d"]["reason"])
    assert live_stand.verdict(results, expected=6) == live_stand.EXIT_FAILED


def test_failed_control_row_skips_its_dependents() -> None:
    """R1-1: a row whose control is another row's result (5б and 7 need
    row 2) is skipped when that row fails, never recorded as a pass."""
    names = {r.id: r for r in live_stand.ROWS}
    assert "2" in names["5б"].needs and "2" in names["4а"].needs
    assert {"2", "8"} <= set(names["7"].needs)

    def fn(row_id: str) -> Callable[[object], dict[str, object]]:
        def run(_st: object) -> dict[str, object]:
            if row_id == "2":
                raise RowFailed("control row fails")
            return {}

        return run

    rows = tuple(
        live_stand.Row(r.id, r.title, fn(r.id), r.needs)  # type: ignore[arg-type]
        for r in live_stand.ROWS
    )
    cleanup = live_stand.Row("9", "z", fn("9"))  # type: ignore[arg-type]
    results = live_stand.run_rows(object(), rows, cleanup)  # type: ignore[arg-type]
    by_id = {name.split(" ", 1)[0]: v["status"] for name, v in results.items()}
    assert by_id["2"] == "fail"
    for dependent in ("3", "4а", "4б", "5б", "7"):
        assert by_id[dependent] == live_stand.SKIPPED, dependent
    assert by_id["8"] == "pass" and by_id["6"] == "pass"
    assert live_stand.verdict(results) == live_stand.EXIT_FAILED


def test_row_dependencies_name_existing_earlier_rows() -> None:
    seen: set[str] = set()
    for row in live_stand.ROWS:
        assert set(row.needs) <= seen, row.name
        seen.add(row.id)


@pytest.mark.parametrize(
    ("stderr", "refused"),
    [
        (
            (
                "remote: Write access to repository not granted.\n"
                "fatal: unable to access 'https://github.com/o/r.git/': "
                "The requested URL returned error: 403"
            ),
            True,
        ),
        ("remote: Permission to o/r.git denied to df-fixture-app[bot].", True),
        ("error: cannot open .git/FETCH_HEAD: Permission denied", False),
        ("fatal: could not read Username: terminal prompts disabled", False),
        ("error: The requested URL returned error: 404", False),
        ("", False),
    ],
)
def test_push_refusal_is_githubs_only(stderr: str, refused: bool) -> None:
    """Finding 2: only GitHub's own 403 counts, never a local «Permission
    denied» (the negative twin) or another error."""
    assert live_stand.github_refused_push(stderr) is refused


def test_window_needs_a_snapshot_inside_the_read() -> None:
    """Finding 1: the sampler covers the read only with a snapshot strictly
    inside the window and no failed run."""
    snaps = [(0.0, 0.1, "before"), (1.1, 1.2, "during"), (3.0, 3.1, "after")]
    assert live_stand.window_problem(None, snaps, 1.0, 2.0) is None
    assert live_stand.inside(snaps, 1.0, 2.0) == ["during"]
    edge = [(0.9, 1.05, "straddles start"), (1.95, 2.1, "straddles end")]
    assert live_stand.window_problem(None, edge, 1.0, 2.0) is not None
    assert live_stand.window_problem(None, snaps[:1], 1.0, 2.0) is not None
    assert live_stand.window_problem("код 1: x", snaps, 1.0, 2.0) is not None


def test_sampler_error_is_kept() -> None:
    """A failing `ps` run stops the sampler with its error (then row 6's
    control is infrastructure); a working one records timed snapshots."""
    with live_stand.PsSampler(["/bin/sh", "-c", "exit 3"], period=0.01) as ps:
        assert not ps.saw("x", 2.0)
    assert ps.error is not None and ps.error.startswith("код 3")
    assert ps.snapshots == []
    with live_stand.PsSampler(["/bin/echo", "mark"], period=0.01) as ok:
        assert ok.saw("mark", 5.0)
    began, ended, text = ok.snapshots[0]
    assert ok.error is None and began <= ended and "mark" in text


def test_role_lines_need_the_roles_processes() -> None:
    """Paid review minor 1: only `df-publish` or a `git … clone` line shows
    the roles' processes were visible; a runner line with «github» is not."""
    lines = [
        "/home/runner/actions-runner/bin/Runner.Worker github",
        "sudo -n -u df-publisher /opt/df/libexec/df-publish read-token",
        "/usr/bin/git --config-env=http.extraHeader=DF_READ_AUTH clone --quiet u d",
    ]
    assert live_stand.role_lines(lines) == lines[1:]
    assert live_stand.role_lines(lines[:1]) == []


def test_seen_lines_mask_the_token() -> None:
    text = "1 /usr/bin/git clone https://x\n2 sshd\n3 df-publish read-token SEKRET\n"
    assert live_stand.seen_lines([text, text], ["SEKRET"]) == [
        "1 /usr/bin/git clone https://x",
        "3 df-publish read-token <token>",
    ]


def test_wait_held_gives_up_when_the_publisher_ends() -> None:
    """Finding 9: no POST and the publisher gone: no 180 s wait."""
    held = threading.Event()
    began = time.monotonic()
    assert live_stand.wait_held(held, lambda: False, 30.0) is False
    assert time.monotonic() - began < 5.0
    held.set()
    assert live_stand.wait_held(held, lambda: True, 180.0) is True


def test_phases_refuse_outside_ci_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """R15-6: a phase never runs on a workstation (not root, or no Actions)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit, match="R15-6"):
        live_stand.main(["wipe", "--out", "/nonexistent", "--secrets", "/none"])


def test_no_test_reads_stand_variables() -> None:
    """R15-6: no module of tests/df reads the stand's variables from the
    environment (the workflow passes them as arguments to the script)."""
    prefix = "DF_" + "STAND_"
    pattern = re.compile(rf"(environ|getenv)[^\n]*{prefix}")
    here = Path(__file__).parent
    readers = [p.name for p in here.glob("*.py") if pattern.search(p.read_text())]
    assert readers == []


# ---------- the merge rows on the stop route (stage 2b, Task 11) ----------


def test_merge_rows_needs() -> None:
    """Row 11 needs the seed and the stop-PR of row 10; row 12 seeds its
    own workstream and needs no other row; both come after the 2a rows."""
    by_id = {r.id: r for r in live_stand.ROWS}
    assert by_id["10"].needs == ()
    assert by_id["11"].needs == ("10",)
    assert by_id["12"].needs == ()
    assert [r.id for r in live_stand.ROWS][-3:] == ["10", "11", "12"]


def test_failed_seed_skips_row11_not_row12() -> None:
    def fn(row_id: str) -> Callable[[object], dict[str, object]]:
        def run(_st: object) -> dict[str, object]:
            if row_id == "10":
                raise RowFailed("seed fails")
            return {}

        return run

    rows = tuple(
        live_stand.Row(r.id, r.title, fn(r.id), r.needs)  # type: ignore[arg-type]
        for r in live_stand.ROWS
    )
    cleanup = live_stand.Row("9", "z", fn("9"))  # type: ignore[arg-type]
    results = live_stand.run_rows(object(), rows, cleanup)  # type: ignore[arg-type]
    by_id = {name.split(" ", 1)[0]: v["status"] for name, v in results.items()}
    assert (by_id["10"], by_id["11"], by_id["12"]) == ("fail", SKIPPED, "pass")
    assert by_id["9"] == "pass"
    assert live_stand.verdict(results) == live_stand.EXIT_FAILED


def test_stand_workstreams_and_branches() -> None:
    """The two workstreams of a run and their branches: the stop branches
    are the contract's (`stop_branch`), every one is swept by `wipe`."""
    tag = "123-1-linux"
    assert live_stand.stand_ws(tag) == "stand-123-1-linux"
    assert live_stand.stand_ws(tag, "b") == "stand-123-1-linux-b"
    names = live_stand.tag_branches(tag)
    assert names["stop"] == stop_branch("stand-123-1-linux", "n1", 1)
    assert names["stop-b"] == stop_branch("stand-123-1-linux-b", "n1", 1)
    assert names["seed"] != names["seed-b"]
    assert len(set(names.values())) == len(names)


def test_seed_manifest_is_a_delivered_request_of_g1() -> None:
    """The seed's one file is a `review/v1` manifest of node `n1` g1 of the
    workstream, at its own path (what `load_history` reads)."""
    path, text = live_stand.seed_manifest("stand-t-b", "owner/stand")
    req = parse_manifest(text)
    assert path == manifest_path("stand-t-b", req.request_id)
    assert (req.ws_id, req.product) == ("stand-t-b", "owner/stand")
    assert {n: e.generation for n, e in req.nodes.items()} == {"n1": 1}


def test_stop_pr_is_routed_as_stop_by_the_publisher() -> None:
    """The stop-PR of the rows (one added record on the contract's branch)
    is the kind `stop` by the publisher's own routing and its precheck's
    path rule; its record names the node and generation of its path."""
    ws = "stand-123-1-macos-b"
    path, text = live_stand.stop_record(ws, "first")
    assert path == stop_path(ws, "n1", 1)
    assert parse_stop(text) == StopRecord("n1", 1, "first")
    patterns = parse_patterns((REPO_ROOT / PATTERNS).read_text())
    branch = live_stand.tag_branches("123-1-macos")["stop-b"]
    assert route(branch, [("added", path, None)], patterns) == "stop"
    assert route(branch, [("modified", path, None)], patterns) != "stop"


def _request(ws: str, gen: int) -> NodeRequest:
    _path, text = live_stand.seed_manifest(ws, "owner/stand")
    req = parse_manifest(text)
    entry = req.nodes["n1"]
    nodes = {"n1": NodeEntry(entry.path, entry.content_hash, gen)}
    return NodeRequest(
        req.request_id, ws, req.product, nodes, req.upstream_pins, req.policy
    )


def test_delivered_seed_judges_the_history() -> None:
    """Row 10's control: the current event of `n1` is the seed's merge
    commit and head at g1; anything else is named."""
    merge, head = "m" * 40, "h" * 40
    seed = DeliveredRequest(merge, head, _request("w", 1))
    assert live_stand.seed_problem((seed,), merge, head) is None
    assert "нет" in str(live_stand.seed_problem((), merge, head))
    other = DeliveredRequest("x" * 40, head, _request("w", 1))
    assert live_stand.seed_problem((other,), merge, head) is not None
    g2 = DeliveredRequest(merge, head, _request("w", 2))
    assert live_stand.seed_problem((g2,), merge, head) is not None
    stopped = (seed, DeliveredStop("s" * 40, StopRecord("n1", 1, "x")))
    assert live_stand.seed_problem(stopped, merge, head) is not None
    assert live_stand.seed_problem(Unavailable("boom"), merge, head) == "boom"


def _seen(
    method: str = "PUT",
    path: str = "/repos/o/r/pulls/7/merge",
    status: int = 409,
    *,
    held: bool = False,
    up: bool = True,
    down: bool = True,
) -> gate_proxy.Seen:
    return gate_proxy.Seen(method, path, status, held, False, up, down)


def test_merge_puts_reads_the_proxy_log() -> None:
    """Every `PUT` on the PR's merge path that reached the proxy, delivered
    upstream or not; other requests and other PRs are not counted."""
    log = [
        _seen("GET", status=200),
        _seen("POST", "/repos/o/r/pulls/7/reviews", 200),
        _seen(held=True),
        _seen(path="/repos/o/r/pulls/8/merge"),
        _seen(status=502, up=False),
    ]
    puts = live_stand.merge_puts(log, "o/r", 7)
    assert puts == [log[2], log[4]]
    assert live_stand.merge_put_re("o/r", 7).fullmatch("/repos/o/r/pulls/7/merge")
    assert not live_stand.merge_put_re("o/r", 7).fullmatch("/repos/o/r/pulls/70/merge")


def _answer(
    outcome: str, reason: str | None, *, replayed: bool = False, **result: object
) -> tuple[int, Response]:
    evidence: dict[str, object] = {"kind": "stop"}
    if replayed:
        evidence["replayed"] = True
    if outcome == "done" and not replayed:
        evidence["premise"] = PREMISE
    rc = {"done": 0, "refused": 2}.get(outcome, 3)
    return rc, Response(outcome, reason, result, evidence)  # type: ignore[arg-type]


def test_head_changed_verdict() -> None:
    """Row 11: `refused: head_changed`, the same on the repeat (replayed),
    the PR open and not merged, one `PUT` upstream answered 409."""
    first = _answer("refused", "head_changed")
    again = _answer("refused", "head_changed", replayed=True)
    pr: dict[str, object] = {"state": "open", "merged": False}
    puts = [_seen(held=True)]
    live_stand.check_head_changed(first, again, pr, puts)
    bad = {
        "merged": (first, again, {"state": "closed", "merged": True}, puts),
        "other reason": (_answer("refused", "pr_state"), again, pr, puts),
        "repeat not replayed": (first, _answer("refused", "head_changed"), pr, puts),
        "two puts": (first, again, pr, puts * 2),
        "put merged": (first, again, pr, [_seen(status=200)]),
        "no put": (first, again, pr, []),
    }
    for name, args in bad.items():
        with pytest.raises(RowFailed):
            live_stand.check_head_changed(*args)
        assert name


def test_reconciled_verdict() -> None:
    """Row 12: `done` by reconciliation (the premise named), the PR merged
    by the bot at the head with the answered merge commit; the repeat is the
    same `done`, replayed; one `PUT` upstream, its answer cut."""
    head, commit, bot = "h" * 40, "c" * 40, "df-app[bot]"
    result = {"merge_commit": commit, "head_sha": head}
    first = _answer("done", None, **result)
    again = _answer("done", None, replayed=True, **result)
    pr: dict[str, object] = {
        "merged": True,
        "merged_by": {"login": bot, "type": "Bot"},
        "head": {"sha": head},
        "merge_commit_sha": commit,
    }
    puts = [_seen(status=200, down=False)]
    live_stand.check_reconciled(first, again, pr, puts, bot=bot, head=head)
    human = {**pr, "merged_by": {"login": "someone", "type": "User"}}
    bad = {
        "unknown": (_answer("unknown", "merge_not_confirmed"), again, pr, puts),
        "by human": (first, again, human, puts),
        "other commit": (first, again, {**pr, "merge_commit_sha": "d" * 40}, puts),
        "not replayed": (first, _answer("done", None, **result), pr, puts),
        "two puts": (first, again, pr, puts * 2),
        "answered": (first, again, pr, [_seen(status=200)]),
    }
    for name, args in bad.items():
        with pytest.raises(RowFailed):
            live_stand.check_reconciled(*args, bot=bot, head=head)
        assert name
    no_premise = (0, Response("done", None, result, {"kind": "stop"}))
    with pytest.raises(RowFailed):
        live_stand.check_reconciled(no_premise, again, pr, puts, bot=bot, head=head)


@dataclass
class StubGitHub:
    """`MachineGitHub` of row 9: PR states and branches in memory."""

    states: dict[int, str]
    refs: set[str]
    patched: list[int] = field(default_factory=list)
    full_name: str = "o/r"

    def repo(self, rest: str) -> str:
        return f"/repos/{self.full_name}{rest}"

    def pull(self, number: int) -> dict[str, object]:
        return {"state": self.states[number]}

    def ref_sha(self, branch: str) -> str | None:
        return "a" * 40 if branch in self.refs else None

    def call(self, method: str, path: str, body: object | None = None) -> object:
        if method == "PATCH":
            number = int(path.rsplit("/", 1)[1])
            if self.states[number] != "open":
                raise live_stand.GitHubRefused("merged PR", 422)
            self.patched.append(number)
            self.states[number] = "closed"
        elif method == "DELETE":
            self.refs.discard(path.split("/git/refs/heads/", 1)[1])
        return None


def test_cleanup_closes_open_prs_and_keeps_the_merges(tmp_path: Path) -> None:
    """Row 9: only open PRs are closed (a merged seed or stop PR is left
    as it is), every branch deleted; the merges that stay on the default
    branch are named in the evidence."""
    gh = StubGitHub({1: "open", 2: "closed", 3: "open"}, {"a", "b"})
    args = live_stand.StandArgs(tmp_path, tmp_path, "s", 1, 2, 3, "t")
    st = live_stand.Stand(
        cast(RolesWorld, None),
        args,
        StandSecrets(tmp_path / "k", tmp_path / "t", "ghp_x"),
        cast(live_stand.MachineGitHub, gh),
        cast(live_stand.Product, None),
        "master",
        "bot[bot]",
        prs={"brief": 1, "seed": 2, "stop": 3},
        branches=["a", "b"],
        merges={"seed": "m" * 40},
    )
    found = live_stand.row9_cleanup(st)
    assert gh.patched == [1, 3] and gh.refs == set()
    assert found == {"closed": 3, "deleted": 2}
    evidence = json.loads(
        (tmp_path / "live" / OS_DIR / "row9-cleanup.json").read_text()
    )
    assert evidence["kept_on_default_branch"] == {"seed": "m" * 40}
