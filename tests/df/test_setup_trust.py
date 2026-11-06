"""`darkfactory setup-trust` (stage 3a, Task 12; installer spec 6.2 rev 11).

The read phase runs against a fake GitHub (`FakeGitHub`, a transport): no
network, fake tokens only. The write phase runs as the fake root of
`fake_layout`; the group `df-history` is a substituted GID and
`trust-status` is the seam `setup_trust.trust_status_call` -- real `sudo`
and real groups exist only in the CI root cells.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from df import cli, journal, launcher_entry, perimeter_client, setup_trust
from df import platform as plat
from df.journal import NeedsHuman, Stable, StepFailed
from df.paths import Layout
from df.publisher import github, history, request
from df.roles import HISTORY_GROUP
from df.stage2_config import load_trust, parse_trust
from df.state import RawSnapshot
from tests.df.conftest import FakeLayout

POLICY_ID = 101
TRUST_ID = 202
POLICY = "Owner/approval-policy"
TRUST = "Owner/df-trust"
HEAD = "a" * 40
ACTIVATION = "b" * 40
SNAPSHOT = "c" * 40
PAT = "github_pat_11FAKEpatFORtests0000000000000_readonly"
HISTORY_TOKEN = "github_pat_11FAKEhistoryTOKEN000000000000_public"
GID = 4321
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
RAW = RawSnapshot(0, None, None, None, None, False)
SOURCE = (
    "APPROVAL_POLICY_REPO=owner/approval-policy\n"
    "APPROVAL_POLICY_BRANCH=main\n"
    f"BOOTSTRAP_ACTIVATION={ACTIVATION}\n"
    "POLICY_ADMINS=alice\n"
)
RULES = [{"type": t} for t in ("deletion", "non_fast_forward", "pull_request")]
UPGRADE = {
    "message": "Upgrade to GitHub Pro or make this repository public to enable "
    "this feature."
}


def _repo(repo_id: int, name: str, **extra: object) -> dict[str, object]:
    return {
        "id": repo_id,
        "full_name": name,
        "default_branch": "main",
        "private": False,
        "allow_rebase_merge": False,
        **extra,
    }


def _content(text: str) -> dict[str, object]:
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


@dataclass
class FakeGitHub:
    """A transport over `answers` (path -> (status, body)); 404 otherwise."""

    answers: dict[str, tuple[int, object]] = field(default_factory=dict)
    seen: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, dict[str, str], bytes]:
        assert method == "GET" and body is None
        path = url.removeprefix(f"{github.API}/")
        self.seen.append((path, dict(headers)))
        status, data = self.answers.get(path, (404, {"message": "Not Found"}))
        return status, {}, json.dumps(data).encode()


def good_github() -> FakeGitHub:
    """Both trust sources set up as spec 6.2 asks."""
    fake = FakeGitHub()
    a = fake.answers
    a[f"repositories/{POLICY_ID}"] = (200, _repo(POLICY_ID, POLICY))
    a[f"repositories/{TRUST_ID}"] = (200, _repo(TRUST_ID, TRUST))
    a["user"] = (200, {"login": "alice", "plan": {"name": "pro"}})
    a[f"repos/{TRUST}/branches/main"] = (200, {"commit": {"sha": HEAD}})
    a[f"repos/{TRUST}/contents/source.env?ref=main"] = (200, _content(SOURCE))
    for name in (POLICY, TRUST):
        base = f"repos/{name}"
        a[f"{base}/rules/branches/main"] = (200, RULES)
        a[f"{base}/rulesets?includes_parents=true&per_page=100"] = (200, [{"id": 7}])
        a[f"{base}/rulesets/7"] = (
            200,
            {"id": 7, "name": "trust", "target": "branch", "bypass_actors": []},
        )
        a[f"{base}/branches/main/protection"] = (
            404,
            {"message": "Branch not protected"},
        )
        a[f"{base}/keys?per_page=100"] = (200, [])
        a[f"{base}/actions/permissions"] = (200, {"enabled": False})
        a[f"{base}/collaborators?affiliation=all&per_page=100"] = (
            200,
            [{"login": "alice", "role_name": "admin"}],
        )
        a[f"{base}/teams?per_page=100"] = (200, [])
        a[f"{base}/hooks?per_page=100"] = (200, [])
        a[f"{base}/environments?per_page=100"] = (200, {"environments": []})
    return fake


def check(
    fake: FakeGitHub, *, branch: str = "main", rebind: bool = False
) -> setup_trust.PerimeterReport:
    return setup_trust.check_perimeter(
        fake, POLICY_ID, TRUST_ID, trust_branch=branch, rebind=rebind, now=lambda: NOW
    )


def kinds(report: setup_trust.PerimeterReport) -> list[tuple[str, str]]:
    return [(s.kind, s.role) for s in report.stops]


def status_line(
    state: str = "clear",
    reason: str | None = None,
    *,
    root: tuple[str, int, str] = ("github.com", TRUST_ID, "main"),
    findings: list[dict[str, str]] | None = None,
) -> bytes:
    clear = state == "clear"
    result = {
        "root": {"host": root[0], "repository_id": root[1], "branch": root[2]},
        "snapshot_sha": SNAPSHOT if clear else None,
        "last_epoch": 1 if clear else None,
        "state": state,
        "reason": reason,
        "unverified": [],
        "findings": findings or [],
    }
    line = {
        "schema": request.RESPONSE_SCHEMA,
        "outcome": "done",
        "reason": None,
        "result": result,
        "evidence": {},
    }
    return (json.dumps(line) + "\n").encode()


# --- the templates ---------------------------------------------------------------


def _filled(text: str) -> str:
    values = {
        "<OWNER>": "owner",
        "<POLICY_REPO>": "approval-policy",
        "<POLICY_BRANCH>": "main",
        "<BOOTSTRAP_ACTIVATION>": ACTIVATION,
        "<POLICY_ADMINS>": "alice",
        "<APPROVERS>": "alice",
        "<MERGERS>": "alice",
        "<HUMAN>": "alice",
        "<AGENT>": "df-bot",
    }
    for key, value in values.items():
        text = text.replace(key, value)
    assert "<" not in text.replace('"<owner>/<repo>"', "")
    return text


def test_templates_exact_bytes_shown(capsys: pytest.CaptureFixture[str]) -> None:
    """Every template's exact bytes are in the shown text with their size
    and sha256; filled in, each parses with the checker's own parser; `check
    --templates` shows them without a PAT."""
    from governance.act_epochs import parse_journal
    from governance.act_policy import parse_policy_set
    from governance.act_source import parse_source

    shown = setup_trust.show_publishable()
    for name, text in setup_trust.TEMPLATES.items():
        data = text.encode()
        digest = hashlib.sha256(data).hexdigest()
        head = f"----- {name} ({len(data)} байт, sha256 {digest}) -----\n"
        assert f"{head}{text}----- конец {name} -----\n" in shown
    t = {k: _filled(v) for k, v in setup_trust.TEMPLATES.items()}
    source = parse_source(t["df-trust/source.env"])
    assert source.bootstrap_activation == ACTIVATION
    assert setup_trust.parse_source_env(t["df-trust/source.env"])["POLICY_ADMINS"]
    (epoch,) = parse_journal(t["df-trust/epochs.yaml"])
    assert epoch.anchor == source.bootstrap_activation
    files = {
        k.removeprefix("approval-policy/"): v.encode()
        for k, v in t.items()
        if k.startswith("approval-policy/")
    }
    policy = parse_policy_set(ACTIVATION, files)
    assert policy.approvers == {"alice"} and dict(policy.modes) == {}

    code = cli.main(
        ["setup-trust", "check", "--templates"],
        mode="reader",
        read=Stable(RAW, 0),
        lock_fd=None,
        layout=Layout(Path("/nonexistent-df-root")),
    )
    assert code == cli.EXIT_OK
    assert capsys.readouterr().out == shown


def test_required_rules_and_stop_names_mirror_the_checker() -> None:
    """`REQUIRED_RULES` is `act_trust`'s; a stop the checker also finds
    carries its name and one of its roles."""
    from governance import act_trust, act_trust_types

    assert setup_trust.REQUIRED_RULES == act_trust.REQUIRED_RULES
    shared = {"ruleset_missing", "rebase_allowed", "policy_branch_mismatch"}
    shared |= {"not_public", "source_env_invalid"}
    assert shared <= setup_trust.STOP_KINDS
    assert shared <= set(act_trust_types.KINDS)
    fake = good_github()
    fake.answers[f"repositories/{POLICY_ID}"] = (
        200,
        _repo(POLICY_ID, POLICY, private=True, allow_rebase_merge=True),
    )
    fake.answers[f"repos/{TRUST}/rules/branches/main"] = (200, RULES[:1])
    for kind, role in kinds(check(fake)):
        assert role in act_trust_types.KINDS[kind], (kind, role)


# --- the read phase ------------------------------------------------------------------


def test_good_perimeter_has_no_stop() -> None:
    fake = good_github()
    report = check(fake)
    assert report.ok and report.stops == ()
    assert report.trust_full_name == TRUST and report.policy_full_name == POLICY
    assert report.trust_head == HEAD and report.checker == "alice"
    assert report.source is not None
    assert report.source["bootstrap_activation"] == ACTIVATION
    assert all(path for path, _status in report.calls)
    assert {u.split(":")[0] for u in report.unverified} == {
        "apps@approval-policy",
        "apps@df-trust",
    }


def test_tariff_without_rulesets_stops() -> None:
    """A plan without rulesets (spec 6.2 item 2): a stop of its own, no
    automatic change of visibility or plan."""
    fake = good_github()
    for name in (POLICY, TRUST):
        fake.answers[f"repos/{name}/rules/branches/main"] = (403, UPGRADE)
    fake.answers[f"repos/{TRUST}/rulesets?includes_parents=true&per_page=100"] = (
        403,
        UPGRADE,
    )
    report = check(fake)
    assert not report.ok
    assert ("tariff_without_rulesets", "approval-policy") in kinds(report)
    assert kinds(report).count(("tariff_without_rulesets", "df-trust")) == 1
    fake = good_github()
    fake.answers[f"repos/{POLICY}/rules/branches/main"] = (200, RULES[1:])
    assert kinds(check(fake)) == [("ruleset_missing", "approval-policy")]


def test_rebase_allowed_stops() -> None:
    """`allow_rebase_merge = true` is a stop; a hidden value is the closed
    unverified premise of the publisher, not a stop."""
    fake = good_github()
    fake.answers[f"repositories/{TRUST_ID}"] = (
        200,
        _repo(TRUST_ID, TRUST, allow_rebase_merge=True),
    )
    assert kinds(check(fake)) == [("rebase_allowed", "df-trust")]
    fake.answers[f"repositories/{TRUST_ID}"] = (
        200,
        _repo(TRUST_ID, TRUST, allow_rebase_merge=None),
    )
    report = check(fake)
    assert report.ok
    assert "allow_rebase_merge@df-trust" in report.unverified
    from df.publisher.trust_routes import UNVERIFIED

    assert "allow_rebase_merge@df-trust" in UNVERIFIED


def test_policy_branch_not_default_stops() -> None:
    """С-2: the branch `source.env` pins must be the default branch of the
    policy repository (read by id); the name must be that repository."""
    fake = good_github()
    fake.answers[f"repos/{TRUST}/contents/source.env?ref=main"] = (
        200,
        _content(SOURCE.replace("BRANCH=main", "BRANCH=release")),
    )
    fake.answers[f"repos/{POLICY}/rules/branches/release"] = (200, RULES)
    assert kinds(check(fake)) == [("policy_branch_mismatch", "approval-policy")]
    fake.answers[f"repos/{TRUST}/contents/source.env?ref=main"] = (
        200,
        _content(SOURCE.replace("owner/approval-policy", "owner/other")),
    )
    assert kinds(check(fake)) == [("policy_repository_mismatch", "approval-policy")]
    fake.answers[f"repos/{TRUST}/contents/source.env?ref=main"] = (
        200,
        _content(SOURCE.replace("POLICY_ADMINS=alice", "POLICY_ADMINS= ,")),
    )
    found = kinds(check(fake))  # no allowlist: every principal is outside
    assert found[0] == ("source_env_invalid", "df-trust")
    assert set(found[1:]) == {("perimeter_principal", r) for r in setup_trust.ROLES}


def test_pat_403_is_not_verified() -> None:
    """A 403/404 (or no answer) of a read is «не проверено», never «ок»:
    named with its status, its value absent; the codes go to the report."""
    fake = good_github()
    fake.answers[f"repos/{TRUST}/hooks?per_page=100"] = (403, {"message": "x"})
    fake.answers[f"repos/{POLICY}/collaborators?affiliation=all&per_page=100"] = (
        404,
        {},
    )
    fake.answers["user"] = (200, {"login": "alice"})
    report = check(fake)
    assert report.ok
    assert "hooks@df-trust: не проверено (HTTP 403)" in report.unverified
    assert "collaborators@approval-policy: не проверено (HTTP 404)" in report.unverified
    assert any(u.startswith("plan:") for u in report.unverified)
    hooks = report.facts["df-trust"]["hooks"]  # type: ignore[index]
    assert hooks == {"status": 403, "value": None, "truncated": False}
    assert (f"repos/{TRUST}/hooks?per_page=100", 403) in report.calls
    fake.answers[f"repositories/{POLICY_ID}"] = (403, {})
    report = check(fake)
    assert ("repository_unreadable", "approval-policy") in kinds(report)
    assert not report.ok


def test_pat_is_only_the_authorization(tmp_path: Path) -> None:
    """The PAT goes only into `Authorization` of `GET`s to the API host; it
    is in no report byte."""
    fake = good_github()
    transport = history.history_transport(PAT, send=fake)
    report = setup_trust.check_perimeter(
        transport, POLICY_ID, TRUST_ID, trust_branch="main", now=lambda: NOW
    )
    assert {h["Authorization"] for _p, h in fake.seen} == {f"Bearer {PAT}"}
    out = tmp_path / "report.json"
    setup_trust.save_report(report, out)
    assert PAT not in out.read_text()
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


# --- the write phase -------------------------------------------------------------


@dataclass
class W:
    fake: FakeLayout
    mp: pytest.MonkeyPatch
    human: Path
    status: list[bytes | Exception]
    gids: list[int]

    @property
    def lay(self) -> Layout:
        return self.fake.layout

    def report(
        self, rep: setup_trust.PerimeterReport, name: str = "r.json"
    ) -> tuple[Path, str]:
        out = self.human / name
        return out, setup_trust.save_report(rep, out)

    def token_file(self, token: str = HISTORY_TOKEN) -> Path:
        path = self.human / "history-token"
        path.write_text(token + "\n")
        path.chmod(0o600)
        return path

    def write(
        self,
        rep: setup_trust.PerimeterReport | None = None,
        *,
        token: Path | None | bool = True,
        rebind: bool = False,
        digest: str | None = None,
        now: datetime = NOW + timedelta(minutes=5),
        **args: object,
    ) -> list[str]:
        rep = check(good_github()) if rep is None else rep
        path, got = self.report(rep)
        kw: dict[str, object] = {
            "policy_repo": POLICY_ID,
            "trust_repo": TRUST_ID,
            "trust_branch": "main",
            **args,
        }
        return setup_trust.run_write(
            self.lay,
            report_path=path,
            report_digest=got if digest is None else digest,
            token_file=self.token_file() if token is True else token or None,  # type: ignore[arg-type]
            rebind=rebind,
            now=lambda: now,
            **kw,  # type: ignore[arg-type]
        )

    def rows(self) -> list[tuple[str, str]]:
        if not self.lay.journal.exists():
            return []
        rows = journal.read_journal(self.lay.journal)
        return [(str(r["step"]), str(r["event"])) for r in rows]


@pytest.fixture()
def w(fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> W:
    fake_layout.make_dirs()
    human = tmp_path / "human"
    human.mkdir()
    world = W(fake_layout, monkeypatch, human, [status_line()], [])
    monkeypatch.setattr(
        plat, "group_gid", lambda name: GID if name == HISTORY_GROUP else None
    )
    real_fchown = os.fchown

    def fchown(fd: int, uid: int, gid: int) -> None:
        world.gids.append(gid)
        real_fchown(fd, uid, gid)

    monkeypatch.setattr(os, "fchown", fchown)

    def trust_status(_layout: Layout, *, purpose: str) -> bytes:
        assert purpose == setup_trust.STEP
        answer = world.status.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(setup_trust, "trust_status_call", trust_status)
    return world


def _tree(path: Path) -> list[str]:
    return sorted(str(p) for p in path.rglob("*")) if path.exists() else []


def test_write_finished_on_clear(w: W) -> None:
    lines = w.write()
    binding = load_trust(w.lay.etc / "trust.toml")
    assert binding is not None
    assert binding.root_id == ("github.com", TRUST_ID, "main")
    assert binding.df_trust_full_name == TRUST  # GitHub's name, by id
    assert binding.history_gh_config_dir == str(w.lay.history_gh)
    assert any("trust-status: clear" in line for line in lines)
    assert w.rows() == [("setup-trust", "done")]
    (evidence,) = w.lay.evidence_dir.iterdir()
    data = json.loads(evidence.read_text())
    assert data["trust_status"]["snapshot_sha"] == SNAPSHOT
    assert data["activation_commit"] == ACTIVATION
    assert data["trust_head"] == HEAD and data["checker"] == "alice"
    assert stat.S_IMODE(os.stat(evidence).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(w.lay.evidence_dir).st_mode) == 0o700
    assert journal.read_generation(w.lay) % 2 == 0
    for path in (*w.lay.evidence_dir.iterdir(), w.lay.journal):
        assert HISTORY_TOKEN not in path.read_text()


def test_no_history_profile_not_finished(w: W) -> None:
    """O14: without a history token profile nothing is written; the reason
    is named."""
    with pytest.raises(StepFailed) as exc:
        w.write(token=None)
    assert exc.value.detail == setup_trust.NO_HISTORY_PROFILE
    assert "нет профиля токена истории" in str(exc.value)
    assert not (w.lay.etc / "trust.toml").exists()
    assert not w.lay.history_gh.exists()
    assert w.status == [status_line()]  # trust-status not asked
    bad = w.human / "bad-token"
    bad.write_text("not a token\n")
    bad.chmod(0o600)
    with pytest.raises(StepFailed, match="не токен"):
        w.write(token=bad)
    assert not (w.lay.etc / "trust.toml").exists()


def test_profile_written_root_group_modes(
    w: W, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/etc/df/history-gh` is root's, group `df-history`, `0750`, its files
    `0640`; the path is exactly what `history.history_token` reads, and the
    token it gets through `gh` is the one written."""
    w.write()
    profile = w.lay.history_gh
    assert stat.S_IMODE(os.lstat(profile).st_mode) == 0o750
    assert w.fake.owner_of(os.lstat(profile)) == plat.ROOT_UID
    files = sorted(p.name for p in profile.iterdir())
    assert files == ["config.yml", "hosts.yml"]
    for name in files:
        st = os.lstat(profile / name)
        assert stat.S_IMODE(st.st_mode) == 0o640 and stat.S_ISREG(st.st_mode)
        assert w.fake.owner_of(st) == plat.ROOT_UID
    assert len(w.gids) >= 3 and set(w.gids) == {GID}  # the directory, both files
    assert stat.S_IMODE(os.lstat(w.lay.etc / "trust.toml").st_mode) == 0o644
    hosts = (profile / "hosts.yml").read_text()
    assert hosts == setup_trust.profile_files(HISTORY_TOKEN)["hosts.yml"]

    binding = load_trust(w.lay.etc / "trust.toml")
    assert binding is not None
    seen: list[dict[str, str]] = []

    def gh(argv: list[str], **kw: object) -> plat.RunResult:
        env = dict(kw["env"])  # type: ignore[call-overload]
        seen.append(env)
        text = (Path(env["GH_CONFIG_DIR"]) / "hosts.yml").read_text()
        token = [
            x.split(": ")[1]
            for x in text.splitlines()
            if x.startswith("    oauth_token")
        ]
        return plat.RunResult(0, token[0].encode() + b"\n", b"", True)

    monkeypatch.setattr(plat, "run_command", gh)
    home = w.lay.root / "publisher-home"
    home.mkdir()
    got = history.history_token(binding, gh=Path("/usr/bin/gh"), role_home=home)
    assert got == HISTORY_TOKEN
    assert seen[0]["GH_CONFIG_DIR"] == str(profile)


def test_profile_dir_of_another_mode_refused(w: W) -> None:
    w.lay.history_gh.mkdir(mode=0o777)
    os.chmod(w.lay.history_gh, 0o777)
    with pytest.raises(StepFailed, match="0777"):
        w.write()
    assert not (w.lay.etc / "trust.toml").exists()
    assert journal.read_generation(w.lay) % 2 == 0


def test_binding_from_args_parsed_strictly(w: W) -> None:
    """Ids are canonical decimals (argparse) within the publisher's limits;
    the branch has the git branch form; the name is GitHub's for the id."""
    base = ["setup-trust", "write", "--trust-branch", "main", "--report", "/r"]
    base += ["--report-digest", "sha256:" + "0" * 64]
    for bad in ("012", "1.0", "-5", "", "１"):
        argv = [*base, "--policy-repo-id", bad, "--trust-repo-id", "2"]
        with pytest.raises(cli.UsageError):
            cli._parse(argv)  # noqa: SLF001
    good = cli._parse([*base, "--policy-repo-id", "1", "--trust-repo-id", "2"])  # noqa: SLF001
    assert (good.policy_repo_id, good.trust_repo_id) == (1, 2)
    report = check(good_github())
    for field_name, value, problem in (
        ("trust_branch", "../x", "binding.branch"),
        ("trust_branch", "a" * 256, "binding.branch"),
        ("trust_repository_id", 2**63, "binding.df_trust_repository_id"),
        ("trust_full_name", "x" * 141 + "/y", "binding.df_trust_full_name"),
    ):
        bad_report = replace(report, **{field_name: value})
        with pytest.raises(StepFailed, match=problem.replace(".", r"\.")):
            setup_trust.binding_from(bad_report, w.lay)
    with pytest.raises(StepFailed, match="другого вызова"):
        w.write(trust_branch="Main")
    assert not (w.lay.etc / "trust.toml").exists()
    lower = replace(report, trust_full_name=TRUST.lower())
    assert setup_trust.binding_from(lower, w.lay).df_trust_full_name == TRUST.lower()


def test_write_refuses_report_digest_mismatch(w: W) -> None:
    rep = check(good_github())
    path, digest = w.report(rep)
    path.write_text(path.read_text().replace('"main"', '"main" '))
    for wrong in (digest, "sha256:" + "0" * 64):
        with pytest.raises(StepFailed, match="digest"):
            setup_trust.run_write(
                w.lay,
                policy_repo=POLICY_ID,
                trust_repo=TRUST_ID,
                trust_branch="main",
                report_path=path,
                report_digest=wrong,
                token_file=w.token_file(),
                rebind=False,
                now=lambda: NOW,
            )
    with pytest.raises(StepFailed, match="sha256:<64 hex>"):
        w.write(digest="md5:x")
    with pytest.raises(StepFailed, match="устарел"):
        w.write(now=NOW + timedelta(hours=2))
    stopped = check(good_github())
    stopped = replace(
        stopped, stops=(setup_trust.Stop("rebase_allowed", "df-trust", "x"),)
    )
    with pytest.raises(StepFailed, match="не пройдена: rebase_allowed"):
        w.write(stopped)
    assert _tree(w.lay.history_gh) == [] and not (w.lay.etc / "trust.toml").exists()
    assert w.rows() == []


def test_verify_after_write_unavailable_not_finished(w: W) -> None:
    """`unavailable` (or no answer, or another root) after the write: the
    installation is not finished (exit 3), journalled, evidence written."""
    w.status[:] = [status_line("unavailable", "history_token_unavailable: x")]
    with pytest.raises(NeedsHuman, match=setup_trust.NOT_FINISHED):
        w.write()
    assert (w.lay.etc / "trust.toml").exists()
    assert w.rows() == [("setup-trust", "needs-human")]
    assert len(list(w.lay.evidence_dir.iterdir())) == 1
    w.status[:] = [status_line(root=("github.com", 999, "main"))]
    assert isinstance(setup_trust.verify_after_write(w.lay), setup_trust.Refused)
    w.status[:] = [OSError("sudo failed")]
    got = setup_trust.verify_after_write(w.lay)
    assert isinstance(got, setup_trust.Refused) and "нет ответа" in got.reason
    w.status[:] = [status_line("blocked")]
    with pytest.raises(NeedsHuman):
        w.write()
    w.status[:] = [status_line()]
    w.write()  # the repeat of the same binding finishes it
    assert w.rows()[-1] == ("setup-trust", "done")


def test_rebind_full_check(w: W) -> None:
    """A changed root needs `--rebind` on both phases (the full check made
    for it); `--rebind` needs a binding; the same binding is a repeat."""
    with pytest.raises(StepFailed, match="--rebind без привязки"):
        w.write(check(good_github(), rebind=True), rebind=True)
    w.write()
    other = good_github()
    other.answers[f"repos/{TRUST}/branches/next"] = (200, {"commit": {"sha": HEAD}})
    other.answers[f"repos/{TRUST}/contents/source.env?ref=next"] = (
        200,
        _content(SOURCE),
    )
    other.answers[f"repos/{TRUST}/rules/branches/next"] = (200, RULES)
    moved = check(other, branch="next")
    w.status[:] = [status_line(root=("github.com", TRUST_ID, "next"))]
    with pytest.raises(StepFailed, match="привязка уже есть"):
        w.write(moved, trust_branch="next")
    with pytest.raises(StepFailed, match="другого вызова"):
        w.write(moved, trust_branch="next", rebind=True)  # not a --rebind check
    w.write(check(other, branch="next", rebind=True), trust_branch="next", rebind=True)
    binding = load_trust(w.lay.etc / "trust.toml")
    assert binding is not None and binding.df_trust_branch == "next"
    assert "--rebind" in setup_trust.next_command(
        check(other, branch="next", rebind=True), Path("/r"), "sha256:x"
    )


def test_epochs_exhausted_text_names_exit() -> None:
    """`unsupported_size: epochs` names the exit of spec 6.1 verbatim, only
    for a reason the publisher typed (re-review N-5): the answer's own
    reason, a К event's cause -- never a finding's detail or a role's
    record-violation."""
    text = setup_trust.explain("unavailable: …", "unsupported_size: epochs")
    for part in (
        "новый репо df-trust с начальным журналом из одной эпохи",
        "setup-trust check --rebind",
        "setup-trust write --rebind",
        "полная проверка периметра",
        "прогоны прежнего корня операций над PR не получают",
    ):
        assert part in text
    assert setup_trust.explain("unavailable: x", "x") == "unavailable: x"
    assert setup_trust.explain("a unsupported_size: epochs", None) == (
        "a unsupported_size: epochs"
    )
    assert setup_trust.explain("t", "why: unsupported_size: epochs") == "t"
    status = request.TrustStatus(
        None, None, "unavailable", "unsupported_size: epochs", (), ()
    )
    assert setup_trust.EPOCHS_EXIT in setup_trust.status_lines(status)[0]
    finding = {"kind": "k", "role": "df-trust", "detail": "unsupported_size: epochs"}
    status = request.TrustStatus(None, None, "blocked", None, (), (finding,))
    assert setup_trust.EPOCHS_EXIT not in "".join(setup_trust.status_lines(status))


@pytest.mark.parametrize("kind", ["record_violation", "finding", "unknown"])
def test_role_text_never_names_the_epochs_exit(kind: str) -> None:
    """Re-review N-5: a reason another party wrote, exactly `unsupported_size:
    epochs`, never prints the exit; the publisher's К with that cause does."""
    epochs = "unsupported_size: epochs"
    forged = request.BlockingRow(ROOT_ID, 1, kind, "registered", 1, epochs)
    status = request.TrustStatus(
        None, None, "blocked", None, (), (), request.Blocking((forged,), 0)
    )
    assert setup_trust.EPOCHS_EXIT not in "".join(
        setup_trust.status_lines(status, ROOT_ID)
    )
    genuine = dataclasses.replace(forged, kind="conservative")
    status = dataclasses.replace(status, blocking=request.Blocking((genuine,), 0))
    assert setup_trust.EPOCHS_EXIT in setup_trust.status_lines(status, ROOT_ID)[1]


EVIL_NAME = "eve\n  СТОП нет\x1b[2K\u202eko"


def test_check_report_names_are_inert() -> None:
    """Round 3: names GitHub returns -- a collaborator's login, a deploy key's
    title -- print as one inert line each in `setup-trust check`."""
    fake = good_github()
    base = f"repos/{TRUST}"
    fake.answers[f"{base}/collaborators?affiliation=all&per_page=100"] = (
        200,
        [
            {"login": "alice", "role_name": "admin"},
            {"login": EVIL_NAME, "role_name": "admin"},
        ],
    )
    fake.answers[f"{base}/keys?per_page=100"] = (
        200,
        [{"id": 9, "title": EVIL_NAME, "read_only": False}],
    )
    text = setup_trust.report_text(check(fake))
    assert not any(c in text for c in "\r\x1b\u202e")
    lines = text.splitlines()
    assert not any(ln.startswith("  СТОП") for ln in lines)
    trust_line = next(ln for ln in lines if ln.startswith("df-trust: запись"))
    assert "eve\\x0a  СТОП нет\\x1b[2K\\u202eko" in trust_line
    assert "deploy_key:9" in trust_line


def test_trust_toml_round_trip() -> None:
    from df.stage2_config import TrustBinding, dump_trust

    binding = TrustBinding("github.com", 2, "o/t", "main", 1, "public", "/etc/df/h")
    assert parse_trust(dump_trust(binding)) == binding
    bare = replace(binding, history_gh_config_dir=None)
    assert parse_trust(dump_trust(bare)) == bare


# --- the command's modes -----------------------------------------------------------

CHECK = [
    "setup-trust",
    "check",
    "--policy-repo-id",
    str(POLICY_ID),
    "--trust-repo-id",
    str(TRUST_ID),
    "--trust-branch",
    "main",
]


@pytest.fixture()
def human(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    fake = good_github()
    monkeypatch.setattr(setup_trust, "read_pat", lambda: PAT)
    monkeypatch.setattr(
        setup_trust,
        "pat_transport",
        lambda token: history.history_transport(token, send=fake),
    )
    yield fake


def test_setup_trust_check_runs_as_non_root_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    human: FakeGitHub,
) -> None:
    """`check` is a reader and refuses root; as the human it writes the
    report and prints its digest and the write command; the PAT is in no
    output and no file."""
    out = tmp_path / "report.json"
    argv = [*CHECK, "--out", str(out)]
    assert launcher_entry.mode_of(argv) == "reader"
    assert cli.parsed_mode(cli._parse(argv)) == "reader"  # noqa: SLF001
    lay = Layout(Path(os.path.realpath(tmp_path)) / "root")

    def run() -> int:
        return cli.main(
            argv, mode="reader", read=Stable(RAW, 0), lock_fd=None, layout=lay
        )

    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    assert run() == cli.EXIT_REFUSED
    assert setup_trust.CHECK_AS_ROOT in capsys.readouterr().err
    assert not out.exists() and human.seen == []
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    assert run() == cli.EXIT_OK
    shown = capsys.readouterr()
    digest = "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest()
    assert digest in shown.out
    assert f"--report-digest {digest}" in shown.out
    assert PAT not in shown.out + shown.err + out.read_text()
    human.answers[f"repositories/{TRUST_ID}"] = (
        200,
        _repo(TRUST_ID, TRUST, allow_rebase_merge=True),
    )
    assert run() == cli.EXIT_REFUSED
    shown = capsys.readouterr()
    assert "СТОП rebase_allowed (df-trust)" in shown.out
    assert json.loads(out.read_text())["stops"][0]["kind"] == "rebase_allowed"
    with pytest.raises(cli.UsageError):
        cli._parse([*CHECK[:2], "--write"])  # noqa: SLF001
    assert (
        cli.main(CHECK, mode="reader", read=Stable(RAW, 0), lock_fd=None, layout=lay)
        == 2
    )


WRITE = [
    "setup-trust",
    "write",
    "--policy-repo-id",
    str(POLICY_ID),
    "--trust-repo-id",
    str(TRUST_ID),
    "--trust-branch",
    "main",
    "--report",
    "/r.json",
    "--report-digest",
    "sha256:" + "0" * 64,
]


def test_setup_trust_write_requires_root_and_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`write` is a writer: root only, and only with the writer lock (the
    entry's mode); without root the entry creates nothing."""
    for argv in (WRITE, [*WRITE, "--rebind", "--history-token-file", "/t"]):
        assert launcher_entry.mode_of(argv) == "writer"
        assert cli.parsed_mode(cli._parse(argv)) == "writer"  # noqa: SLF001
    assert ("setup-trust", "write") in launcher_entry.WRITER_COMMANDS
    assert launcher_entry.mode_of(["setup-trust"]) == "reader"
    lay = Layout(Path(os.path.realpath(tmp_path)))
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid() + 1)
    code = cli.main(WRITE, mode="writer", read=Stable(RAW, 0), lock_fd=99, layout=lay)
    assert code == 2 and "требует root" in capsys.readouterr().err
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    code = cli.main(WRITE, mode="reader", read=Stable(RAW, 0), lock_fd=None, layout=lay)
    assert code == 2 and "без блокировки" in capsys.readouterr().err
    monkeypatch.setattr(launcher_entry, "ROOT_UID", os.geteuid() + 1)
    assert launcher_entry.run(WRITE, root=str(tmp_path)) == 2
    assert "требует root" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_setup_trust_write_through_cli(
    w: W, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The writer's dispatch: exit 0 on `clear`, exit 3 when not finished,
    exit 2 (named) without the token file."""
    monkeypatch.setattr(cli, "ROOT_UID", os.geteuid())
    path, digest = w.report(check(good_github(), rebind=False))
    monkeypatch.setattr(setup_trust, "run_write", _now_shifted(setup_trust.run_write))
    argv = [*WRITE[:8], "--report", str(path), "--report-digest", digest]

    def main(extra: list[str]) -> int:
        return cli.main(
            [*argv, *extra],
            mode="writer",
            read=Stable(RAW, 0),
            lock_fd=99,
            layout=w.lay,
        )

    assert main([]) == cli.EXIT_REFUSED
    assert "нет профиля токена истории" in capsys.readouterr().err
    token = ["--history-token-file", str(w.token_file())]
    w.status[:] = [status_line("unavailable", "no_active_release")]
    assert main(token) == cli.EXIT_HUMAN
    assert setup_trust.NOT_FINISHED in capsys.readouterr().err
    w.status[:] = [status_line()]
    assert main(token) == cli.EXIT_OK
    assert "установка доверия завершена" in capsys.readouterr().out


def _now_shifted(real):  # noqa: ANN001, ANN202
    """`run_write` with the report's clock (the report is made at `NOW`)."""

    def run(*args, **kw):  # noqa: ANN002, ANN003, ANN202
        return real(*args, **kw, now=lambda: NOW + timedelta(minutes=1))

    return run


def test_profile_and_evidence_paths_of_the_layout() -> None:
    lay = Layout(Path("/"))
    assert lay.history_gh == Path("/etc/df/history-gh")
    assert lay.evidence_dir == Path("/var/lib/df/evidence")


# --- fix round 1: the allowlist of spec A 6.2 (а), pagination ----------------------


def _with(fake: FakeGitHub, name: str, what: str, body: object) -> FakeGitHub:
    paths = {
        "collaborators": "collaborators?affiliation=all&per_page=100",
        "teams": "teams?per_page=100",
        "keys": "keys?per_page=100",
        "ruleset": "rulesets/7",
    }
    fake.answers[f"repos/{name}/{paths[what]}"] = (200, body)
    return fake


def principal_stops(report: setup_trust.PerimeterReport) -> list[tuple[str, str]]:
    return [(s.role, s.detail) for s in report.stops if s.kind == "perimeter_principal"]


def test_collaborators_outside_policy_admins_stop() -> None:
    """Write, maintain or admin (by role or permission) outside
    `POLICY_ADMINS` is a stop; an admin in it (case-folded) and a reader are
    not."""
    people = [
        {"login": "Alice", "role_name": "admin"},
        {"login": "ai-prosto", "role_name": "write"},
        {"login": "mo", "role_name": "maintain"},
        {"login": "cu", "role_name": "custom", "permissions": {"push": True}},
        {"login": "reader", "role_name": "read", "permissions": {"pull": True}},
    ]
    report = check(_with(good_github(), POLICY, "collaborators", people))
    assert not report.ok
    assert principal_stops(report) == [
        ("approval-policy", f"collaborator:{n}: не в POLICY_ADMINS")
        for n in ("ai-prosto", "mo", "cu")
    ]
    text = setup_trust.report_text(report)
    assert "collaborator:Alice" in text
    assert "collaborator:ai-prosto — НЕ в POLICY_ADMINS" in text
    assert "reader" not in text


def test_teams_with_push_stop() -> None:
    teams = [
        {"slug": "core", "permission": "push"},
        {"slug": "ro", "permission": "pull"},
    ]
    report = check(_with(good_github(), TRUST, "teams", teams))
    assert principal_stops(report) == [
        ("df-trust", "team:core (push): не в POLICY_ADMINS")
    ]


def test_writable_deploy_keys_stop() -> None:
    keys = [
        {"id": 1, "title": "ci", "read_only": False},
        {"id": 2, "title": "mirror", "read_only": True},
        {"id": 3, "title": "odd"},
    ]
    report = check(_with(good_github(), TRUST, "keys", keys))
    assert [d for _r, d in principal_stops(report)] == [
        "deploy_key:1 'ci': не в POLICY_ADMINS",
        "deploy_key:3 'odd': не в POLICY_ADMINS",
    ]


def test_bypass_actors_integration_deploykey_stop_others_premise() -> None:
    """`Integration` and `DeployKey` are never a login: a stop. A type that
    maps to no login, or a hidden list, is a named premise."""
    actors = [
        {"actor_type": "Integration", "actor_id": 9, "bypass_mode": "always"},
        {"actor_type": "DeployKey", "actor_id": None},
        {"actor_type": "RepositoryRole", "actor_id": 5},
        {"actor_type": "OrganizationAdmin", "actor_id": 1},
    ]
    ruleset = {"id": 7, "name": "trust", "bypass_actors": actors}
    report = check(_with(good_github(), POLICY, "ruleset", ruleset))
    assert [d for _r, d in principal_stops(report)] == [
        "bypass:Integration:9 (ruleset 7): не в POLICY_ADMINS",
        "bypass:DeployKey:None (ruleset 7): не в POLICY_ADMINS",
    ]
    premises = [u for u in report.unverified if u.startswith("bypass_actors@")]
    assert len(premises) == 2
    assert "RepositoryRole 5 не сводится к логину" in premises[0]
    hidden = check(_with(good_github(), TRUST, "ruleset", {"id": 7}))
    assert hidden.ok
    assert any("ruleset 7 — список не прочитан" in u for u in hidden.unverified)
    assert "не проверено: bypass_actors@df-trust" in setup_trust.report_text(hidden)


def test_full_page_is_truncated_not_verified() -> None:
    """A full page (or a `Link` to the next) is «не проверено (truncated)»."""
    readers = [{"login": f"u{i}", "role_name": "read"} for i in range(100)]
    report = check(_with(good_github(), POLICY, "collaborators", readers))
    assert "collaborators@approval-policy: не проверено (truncated: больше 100)" in (
        report.unverified
    )
    entry = report.facts["approval-policy"]["collaborators"]  # type: ignore[index]
    assert entry["truncated"] is True
    fake = good_github()

    def linked(method: str, url: str, headers: dict[str, str], body: bytes | None):  # noqa: ANN202
        status, hdrs, raw = fake(method, url, headers, body)
        if url.endswith("hooks?per_page=100"):
            return status, {"Link": '<https://x/2>; rel="next"'}, raw
        return status, hdrs, raw

    report = setup_trust.check_perimeter(
        linked, POLICY_ID, TRUST_ID, trust_branch="main", now=lambda: NOW
    )
    assert "hooks@df-trust: не проверено (truncated: больше 100)" in report.unverified


def test_source_env_parser_agrees_with_the_checker() -> None:
    """Every form the checker refuses, the installer refuses (and the good
    ones both accept)."""
    from governance.act_source import SourceError, parse_source

    cases = [
        SOURCE,
        SOURCE + "POLICY_ADMINS=bob\n",
        SOURCE.replace(ACTIVATION, "b" * 39),
        SOURCE.replace(ACTIVATION, "B" * 40),
        SOURCE.replace("POLICY_ADMINS=alice", "POLICY_ADMINS="),
        SOURCE.replace("POLICY_ADMINS=alice", "POLICY_ADMINS= , ,"),
        "# APPROVAL_POLICY_REPO=x/y\n" + SOURCE,
        SOURCE.replace(
            "APPROVAL_POLICY_BRANCH=main", "  APPROVAL_POLICY_BRANCH = main"
        ),
        SOURCE.replace(
            "APPROVAL_POLICY_BRANCH=main", "  APPROVAL_POLICY_BRANCH=main  "
        ),
        SOURCE.replace("APPROVAL_POLICY_REPO=owner/approval-policy\n", ""),
    ]
    for text in cases:
        try:
            parse_source(text)
            theirs = True
        except SourceError:
            theirs = False
        try:
            setup_trust.parse_source_env(text)
            ours = True
        except ValueError:
            ours = False
        assert ours == theirs, text


def test_profile_format_is_the_measured_one() -> None:
    """The profile is byte for byte the form devtools#693 measured."""
    from tests.df.test_root_gh_shared_profile import CONFIG, FAKE, HOSTS

    assert setup_trust.profile_files(FAKE) == {"config.yml": CONFIG, "hosts.yml": HOSTS}
    assert setup_trust.PROFILE_LOGIN == "df-history"


def test_history_token_file_owner_and_mode(
    w: W, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token file is the human's (`SUDO_UID`) or root's, without group or
    other bits; anything else is refused before any write."""
    token = w.token_file()
    token.chmod(0o640)
    with pytest.raises(StepFailed, match="права 0640"):
        w.write(token=token)
    token.chmod(0o600)
    other = os.getuid() + 77
    w.fake.set_owner(token, other)
    monkeypatch.delenv("SUDO_UID", raising=False)
    with pytest.raises(StepFailed, match=f"владелец uid {other}"):
        w.write(token=token)
    assert not w.lay.history_gh.exists() and not (w.lay.etc / "trust.toml").exists()
    monkeypatch.setenv("SUDO_UID", str(other))
    w.write(token=token)
    assert (w.lay.etc / "trust.toml").exists()


def test_rebind_message_names_the_field(w: W) -> None:
    """The same triple with another parameter needs `--rebind` too, and the
    refusal names what differs."""
    from df.stage2_config import dump_trust

    w.write()
    binding = load_trust(w.lay.etc / "trust.toml")
    assert binding is not None
    (w.lay.etc / "trust.toml").write_text(
        dump_trust(replace(binding, history_gh_config_dir=None))
    )
    with pytest.raises(StepFailed) as exc:
        w.write()
    assert "та же тройка" in exc.value.detail
    assert "history_gh_config_dir: None →" in exc.value.detail


def test_evidence_dir_of_another_mode_not_finished(w: W) -> None:
    """After the write, an evidence directory that is not root's `0700` is
    no crash: «не завершена» (exit 3), journalled."""
    w.lay.evidence_dir.mkdir(mode=0o755)
    os.chmod(w.lay.evidence_dir, 0o755)
    with pytest.raises(NeedsHuman, match="evidence не записан"):
        w.write()
    assert w.rows() == [("setup-trust", "needs-human")]
    assert list(w.lay.evidence_dir.iterdir()) == []


def test_check_templates_takes_no_other_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lay = Layout(Path("/nonexistent-df-root"))
    for extra in (["--out", "/x"], ["--policy-repo-id", "1"], ["--rebind"]):
        code = cli.main(
            ["setup-trust", "check", "--templates", *extra],
            mode="reader",
            read=Stable(RAW, 0),
            lock_fd=None,
            layout=lay,
        )
        assert code == cli.EXIT_REFUSED
        assert "--templates: без других аргументов" in capsys.readouterr().err


def test_system_mutation_guard() -> None:
    """The autouse guard of `tests/df/conftest.py`: a non-root test that
    reaches a real account or group mutation fails at `_execute`."""
    from tests.df.conftest import ForbiddenMutation, system_mutation

    for argv in (
        ["/usr/bin/dscl", ".", "-create", "/Groups/x"],
        ["/usr/bin/dscl", ".", "-append", "/Groups/x", "GroupMembership", "a"],
        ["/usr/sbin/groupadd", "x"],
        ["/usr/bin/gpasswd", "-M", "a", "x"],
        ["/usr/bin/sudo", "-n", "/usr/sbin/useradd", "x"],
        ["/usr/sbin/dseditgroup", "-o", "edit", "x"],
    ):
        assert system_mutation(argv), argv
    assert not system_mutation(["/usr/bin/dscl", "-plist", ".", "-read", "/Groups/x"])
    assert not system_mutation(["/usr/bin/getent", "group", "x"])
    if os.geteuid() != 0:
        with pytest.raises(ForbiddenMutation):
            plat.run_command(["/usr/sbin/groupadd", "df-guard-probe"])


# --- cleanup round (re-review N-1..N-9) ---------------------------------------------


def test_unexpected_list_shape_is_not_verified() -> None:
    """N-2: a 200 whose body is not a list is «не проверено», never an empty
    list that would pass the allowlist."""
    fake = _with(good_github(), POLICY, "collaborators", {"login": "ai-prosto"})
    report = check(fake)
    assert "collaborators@approval-policy: не проверено (ответ не список)" in (
        report.unverified
    )
    entry = report.facts["approval-policy"]["collaborators"]  # type: ignore[index]
    assert entry["value"] is None


def test_teams_by_owner_type() -> None:
    """N-3: a `User`-owned repository has no teams: `n/a`, no read, no
    premise; an `Organization` one keeps a 404 as a named premise."""
    fake = good_github()
    for repo_id, name in ((POLICY_ID, POLICY), (TRUST_ID, TRUST)):
        fake.answers[f"repositories/{repo_id}"] = (
            200,
            _repo(repo_id, name, owner={"login": "Owner", "type": "User"}),
        )
        del fake.answers[f"repos/{name}/teams?per_page=100"]
    report = check(fake)
    assert not any(u.startswith("teams@") for u in report.unverified)
    assert not any("teams" in path for path, _s in report.calls)
    teams = report.facts["df-trust"]["teams"]  # type: ignore[index]
    assert teams["status"] == "n/a"
    fake.answers[f"repositories/{TRUST_ID}"] = (
        200,
        _repo(TRUST_ID, TRUST, owner={"login": "Owner", "type": "Organization"}),
    )
    report = check(fake)
    assert "teams@df-trust: не проверено (HTTP 404)" in report.unverified


def test_custom_role_is_a_named_premise() -> None:
    """N-4: a role outside the standard set may grant more than its
    `permissions` show: named, with its login."""
    people = [
        {"login": "alice", "role_name": "admin"},
        {"login": "bot", "role_name": "rules-editor", "permissions": {"pull": True}},
    ]
    report = check(_with(good_github(), POLICY, "collaborators", people))
    assert report.ok
    premise = [u for u in report.unverified if "rules-editor" in u]
    assert premise and "bot" in premise[0]


def test_free_id_skips_recorded_ids(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-6: an id held by a recorded intent (account or group) is never
    handed out again, even when the OS has no record of it."""
    from df.config import Account, GroupRecord, dump_accounts, dump_groups

    lay = fake_layout.layout
    lay.install_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plat.pwd, "getpwall", lambda: [])
    monkeypatch.setattr(plat.grp, "getgrall", lambda: [])
    platform = "linux-x86_64"
    first = plat.ID_RANGE[platform][0]
    assert plat._free_id(platform, "x", lay) == first  # noqa: SLF001
    lay.groups.write_text(
        dump_groups({"df-history": GroupRecord("df-history", first, "creating", "t")})
    )
    account = Account("df-exec", first + 1, first + 1, "creating", "t")
    lay.accounts.write_text(dump_accounts({"df-exec": account}))
    assert plat._free_id(platform, "x", lay) == first + 2  # noqa: SLF001


def test_system_mutation_guard_closed_forms() -> None:
    """N-7: the remaining forms, a shell's `-c` string included."""
    from tests.df.conftest import system_mutation

    for argv in (
        ["/usr/bin/dscl", ".", "-passwd", "/Users/x", "p"],
        ["/usr/sbin/sysadminctl", "-addUser", "x"],
        ["/usr/bin/dsimport", "f", "/Local/Default", "M"],
        ["/usr/sbin/chpasswd"],
        ["/usr/sbin/pw", "groupadd", "x"],
        ["/bin/sh", "-c", "useradd x && true"],
        ["/bin/sh", "-c", "/usr/bin/dscl . -create /Groups/x"],
    ):
        assert system_mutation(argv), argv
    assert not system_mutation(["/bin/sh", "-c", "echo pwd"])


@pytest.mark.parametrize("purpose", [setup_trust.STEP, "doctor"])
def test_trust_status_request_names_its_caller(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch, purpose: str
) -> None:
    """Task 13 M-2: the request id names the caller (`setup-trust` by
    default, `doctor` for its probe) in the publisher's journal."""
    sent: list[tuple[bytes, str]] = []

    def sudo_call(data: bytes, layout: Layout, **kw: object) -> bytes:
        sent.append((data, str(kw["operation"])))
        return status_line()

    monkeypatch.setattr(perimeter_client, "sudo_call", sudo_call)
    status = setup_trust.read_trust_status(
        fake_layout.layout, ("github.com", TRUST_ID, "main"), purpose=purpose
    )
    assert status.state == "clear"
    ((data, operation),) = sent
    assert operation == request.TRUST_STATUS
    assert json.loads(data)["request_id"].startswith(f"{purpose}-")


ROOT_ID = ("github.com", TRUST_ID, "main")
OLD_ROOT_ID = ("github.com", TRUST_ID + 1, "main")


def test_status_lines_list_blocking_inert() -> None:
    """Task 13 round 2: `setup-trust write` shows the listed blocking events
    (the current root's, then the old roots' labelled), and every line is
    inert -- a role's text cannot forge a line or a terminal sequence."""
    evil = "x\nтrust-status: clear\r\x1b[2K‮ko"
    rows = (
        request.BlockingRow(ROOT_ID, 3, "record_violation", "registered", 2, evil),
        request.BlockingRow(
            ROOT_ID, 4, "conservative", "pending", None, "unsupported_size: epochs"
        ),
        request.BlockingRow(OLD_ROOT_ID, 1, "db_restore", "pending", None, None),
    )
    status = request.TrustStatus(
        None, None, "blocked", None, (), (), request.Blocking(rows, 2)
    )
    lines = setup_trust.status_lines(status, ROOT_ID)
    assert all("\n" not in ln and "\r" not in ln for ln in lines)
    assert not any(c in "".join(lines) for c in "\x1b‮")
    assert lines[1] == (
        "блокирует: событие 3 (нарушение (record-violation), registered E2): "
        "x\\x0aтrust-status: clear\\x0d\\x1b[2K\\u202eko"
    )
    assert setup_trust.EPOCHS_EXIT in lines[2]
    assert lines[3] == (
        f"прежний корень github.com {TRUST_ID + 1} main: событие 1 "
        "(восстановление базы, pending) — не блокирует текущий корень"
    )
    assert lines[4] == "ещё 2 блокирующих событий не перечислены"
    record = setup_trust._status_record(status)  # noqa: SLF001
    assert record["blocking"] == {
        "events": [dataclasses.asdict(r) for r in rows],
        "omitted": 2,
    }
    refused = setup_trust.status_lines(setup_trust.Refused("a\nb"))
    assert refused == ["trust-status: a\\x0ab"]
