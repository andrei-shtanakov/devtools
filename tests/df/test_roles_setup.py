"""`darkfactory roles setup` (stage 2a, Task 4): steps, order, idempotence."""

from __future__ import annotations

import dataclasses
import os
import pwd
import stat
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from df import fsutil, journal, libexec, roles, roles_setup, sudoers
from df import platform as plat
from df.config import (
    Account,
    InstallConfig,
    ReleaseEntry,
    dump_accounts,
    load_accounts,
)
from df.journal import StepFailed
from df.publisher import perimeter
from df.roles import ROLE_USERS
from df.stage2_config import dump_publisher, expected_publisher
from df.state import Snapshot
from tests.df.conftest import FakeLayout

OPERATOR = "alice"
ROLE_UID = {"df-exec": 70001, "df-review": 70002, "df-publisher": 70003}
SHA = "0" * 40


@dataclass
class Calls:
    ensure: int = 0
    installed: list[str] = field(default_factory=list)
    policy: list[str] = field(default_factory=list)
    io_log: int = 0
    policy_result: dict[str, list[str]] = field(default_factory=dict)
    run_as: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class Setup:
    fake: FakeLayout
    calls: Calls
    sudoers_file: Path
    snap: Snapshot

    @property
    def lay(self):  # noqa: ANN201
        return self.fake.layout

    def run(self, operator: str = OPERATOR) -> None:
        roles_setup.run(self.lay, operator=operator, snap=self.snap)

    def steps(self) -> list[tuple[str, str]]:
        rows = journal.read_journal(self.lay.journal)
        return [(str(r["step"]), str(r["event"])) for r in rows]


def snapshot(*, openssl: bool = True) -> Snapshot:
    cli = {n: Path(f"/usr/bin/{n}") for n in ("git", "uv", "tar")}
    if openssl:
        cli["openssl"] = Path("/usr/bin/openssl")
    entry = ReleaseEntry("r1", "d", "a", "3.12.13", "allowed", None)
    return Snapshot("r1", entry, InstallConfig("o/r", SHA, "core", cli), None, False, 0)


@pytest.fixture()
def setup(
    fake_layout: FakeLayout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Setup:
    fake = fake_layout
    fake.make_dirs()
    lay = fake.layout
    lay.libexec.parent.mkdir(parents=True, exist_ok=True)
    for name in ("df-exec", "df-review"):
        lay.role_home(name).mkdir(mode=0o700, parents=True)
    calls = Calls()
    sudoers_file = tmp_path / "sudoers-df"

    def ensure_roles(layout):  # noqa: ANN001, ANN202
        calls.ensure += 1
        accounts = {
            n: Account(n, ROLE_UID[n], ROLE_UID[n], "ready", "2026-10-27T00:00:00Z")
            for n in ROLE_USERS
        }
        fsutil.write_atomic(layout.accounts, dump_accounts(accounts))
        return accounts

    def install(text: str) -> None:
        calls.installed.append(text)
        fsutil.write_atomic(sudoers_file, text, 0o440)

    def policy(name: str, *, layout, operator):  # noqa: ANN001, ANN202
        calls.policy.append(name)
        return calls.policy_result.get(name, [])

    def io_log(_layout):  # noqa: ANN001, ANN202
        calls.io_log += 1
        return []

    monkeypatch.setattr(roles, "ensure_roles", ensure_roles)

    def verify(layout, name):  # noqa: ANN001, ANN202
        known = load_accounts(layout.accounts)
        return [] if name in known else [f"нет записи {name}"]

    monkeypatch.setattr(plat, "verify_service_user", verify)
    monkeypatch.setattr(plat, "approved_cli_problems", lambda _cli, _plat: [])
    monkeypatch.setattr(plat, "trusted_path_problems", lambda _path: [])
    real_getpwnam = pwd.getpwnam

    def getpwnam(name: str):  # noqa: ANN202
        return real_getpwnam(
            pwd.getpwuid(os.getuid()).pw_name if name == OPERATOR else name
        )

    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(sudoers, "SUDOERS_FILE", sudoers_file)
    monkeypatch.setattr(sudoers, "install", install)
    monkeypatch.setattr(sudoers, "policy_problems", policy)
    monkeypatch.setattr(sudoers, "io_log_problems", io_log)

    def run_as(user: str, argv: list[str], **kw: object) -> plat.RunResult:
        """`perimeter-init` as the role: `create_db` in this process with the
        role's euid (the directory's virtual owner)."""
        calls.run_as.append((user, list(argv)))
        assert kw["network"] is False and kw["cwd"] == Path("/")
        real = os.geteuid
        os.geteuid = lambda: ROLE_UID[user]  # type: ignore[assignment]
        try:
            outcome = perimeter.create_db(fake.layout)
        except perimeter.Unavailable as exc:
            return plat.RunResult(2, b"", exc.reason.encode(), True)
        finally:
            os.geteuid = real  # type: ignore[assignment]
        return plat.RunResult(0, outcome.encode(), b"", True)

    monkeypatch.setattr(plat, "run_as", run_as)
    return Setup(fake, calls, sudoers_file, snapshot())


def test_steps_order(setup: Setup) -> None:
    setup.run()
    seen = list(dict.fromkeys(step for step, _event in setup.steps()))
    assert seen == [
        "roles",
        "dirs",
        "publisher-config",
        "perimeter-init",
        "libexec",
        "sudoers",
        "verify",
    ]
    assert tuple(seen) == roles_setup.STEP_NAMES
    assert ("verify", "done") in setup.steps()
    assert ("sudoers", "done") in setup.steps()


def test_dirs_owners_and_modes(setup: Setup) -> None:
    setup.run()
    lay = setup.lay
    want = {
        lay.libexec: (0, 0o755),
        lay.runs_dir: (0, 0o755),
        lay.publisher_dir: (ROLE_UID["df-publisher"], 0o700),
        lay.perimeter_dir: (ROLE_UID["df-publisher"], 0o700),
        lay.secrets_dir: (ROLE_UID["df-publisher"], 0o700),
        lay.role_home("df-exec") / "work": (ROLE_UID["df-exec"], 0o700),
        lay.role_home("df-review") / "work": (ROLE_UID["df-review"], 0o700),
    }
    for path, (uid, mode) in want.items():
        st = os.lstat(path)
        assert stat.S_ISDIR(st.st_mode), path
        assert stat.S_IMODE(st.st_mode) == mode, path
        owner = fsutil.owner_uid(st)
        assert owner == (os.getuid() if uid == 0 else uid), path


def test_publisher_config_is_the_expected_table(setup: Setup) -> None:
    setup.run()
    accounts = load_accounts(setup.lay.accounts)
    path = setup.lay.etc / "publisher.toml"
    assert path.read_text(encoding="utf-8") == dump_publisher(
        expected_publisher(accounts)
    )


def test_libexec_and_sudoers_are_installed(setup: Setup) -> None:
    setup.run()
    python = libexec.admin_python_of(setup.lay, setup.snap)
    assert libexec.wrapper_problems(setup.lay, python) == []
    assert setup.calls.installed == [sudoers.render(setup.lay, OPERATOR)]


def test_verify_asks_five_names(setup: Setup) -> None:
    setup.run()
    assert sorted(setup.calls.policy) == sorted(
        [*ROLE_USERS, plat.BUILD_USER, OPERATOR]
    )
    assert setup.calls.io_log == 1


def test_idempotent(setup: Setup) -> None:
    setup.run()
    setup.fake.events.clear()
    before = len(setup.steps())
    setup.run()
    assert setup.calls.ensure == 1
    assert len(setup.calls.installed) == 1
    new = setup.steps()[before:]
    # verify has nothing to settle but always probes, once
    want = [(name, "skip") for name in roles_setup.STEP_NAMES[:-1]]
    assert new == [*want, ("verify", "start"), ("verify", "done")]
    assert len(setup.calls.policy) == 10
    assert not [e for e in setup.fake.events if e[0] in ("created", "chmod")]
    assert len(setup.calls.run_as) == 1


# --- perimeter-init ----------------------------------------------------------


def test_perimeter_init_runs_create_db_as_the_publisher(setup: Setup) -> None:
    setup.run()
    python = libexec.admin_python_of(setup.lay, setup.snap)
    assert setup.calls.run_as == [
        (
            "df-publisher",
            [str(python), "-I", "-B", "-S", str(setup.lay.entry), "publisher",
             "perimeter-init"],
        )
    ]  # fmt: skip
    base = setup.lay.perimeter_dir
    assert sorted(os.listdir(base)) == sorted(
        [perimeter.DB_NAME, perimeter.LOCK, perimeter.MARKER]
    )
    perimeter.open_db(setup.lay).close()


def test_perimeter_init_never_recreates_a_lost_database(setup: Setup) -> None:
    setup.run()
    (setup.lay.perimeter_dir / perimeter.DB_NAME).unlink()
    setup.calls.run_as.clear()
    with pytest.raises(StepFailed, match="потеряна") as info:
        setup.run()
    assert info.value.step == "perimeter-init"
    assert "нужен человек" in str(info.value)
    assert setup.calls.run_as == []
    assert not (setup.lay.perimeter_dir / perimeter.DB_NAME).exists()
    assert ("libexec", "start") not in setup.steps()[-3:]


def test_perimeter_init_finishes_a_missing_marker(setup: Setup) -> None:
    setup.run()
    (setup.lay.perimeter_dir / perimeter.MARKER).unlink()
    setup.calls.run_as.clear()
    setup.run()
    assert len(setup.calls.run_as) == 1
    assert (setup.lay.perimeter_dir / perimeter.MARKER).exists()


def test_perimeter_init_failure_names_the_retry(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_user: str, _argv: list[str], **_kw: object) -> plat.RunResult:
        return plat.RunResult(2, b"", b"darkfactory: denied", True)

    monkeypatch.setattr(plat, "run_as", broken)
    with pytest.raises(StepFailed, match="darkfactory: denied") as info:
        setup.run()
    assert info.value.step == "perimeter-init"
    assert str(info.value).endswith("исправьте и повторите darkfactory roles setup")


def test_sudoers_failure_leaves_no_file_and_skips_verify(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_text: str) -> None:
        raise StepFailed("sudoers", "visudo отказал")

    monkeypatch.setattr(sudoers, "install", refuse)
    with pytest.raises(StepFailed, match="visudo отказал"):
        setup.run()
    assert not setup.sudoers_file.exists()
    assert setup.calls.policy == [] and setup.calls.io_log == 0
    assert ("verify", "start") not in setup.steps()
    assert ("sudoers", "failed") in setup.steps()


def test_verify_failure_is_a_refusal_naming_the_problem(setup: Setup) -> None:
    setup.calls.policy_result["df-exec"] = ["sudo -l -U df-exec: лишнее правило"]
    with pytest.raises(StepFailed, match="лишнее правило") as info:
        setup.run()
    assert info.value.step == "verify"
    assert str(info.value).endswith("исправьте и повторите darkfactory roles setup")
    assert len(setup.calls.policy) == 5  # one probe, not three


def test_stale_wrapper_is_rewritten(setup: Setup) -> None:
    setup.run()
    stale = setup.lay.opt / "libexec" / "df-launch"
    stale.write_text("#!/bin/sh\nexit 0\n")
    setup.run()
    python = libexec.admin_python_of(setup.lay, setup.snap)
    assert libexec.wrapper_problems(setup.lay, python) == []


def test_requires_openssl(setup: Setup) -> None:
    setup.snap = snapshot(openssl=False)
    with pytest.raises(StepFailed, match="openssl") as info:
        setup.run()
    assert info.value.step == "openssl"
    assert setup.calls.ensure == 0 and not setup.lay.journal.exists()


def test_untrusted_openssl_is_refused(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plat, "approved_cli_problems", lambda _c, _p: ["плохой"])
    with pytest.raises(StepFailed, match="плохой"):
        setup.run()
    assert setup.calls.ensure == 0


@pytest.mark.parametrize(
    "operator", [*ROLE_USERS, "root", "df-build", "", "Bad Name", "ALL"]
)
def test_operator_refusals_change_nothing(setup: Setup, operator: str) -> None:
    with pytest.raises(StepFailed) as info:
        setup.run(operator)
    assert info.value.step == "operator"
    assert setup.calls.ensure == 0 and not setup.lay.journal.exists()


def test_no_active_release_is_refused(setup: Setup) -> None:
    setup.snap = Snapshot(None, None, setup.snap.install, None, False, 0)
    with pytest.raises(StepFailed, match="релиз") as info:
        setup.run()
    assert info.value.step == "release"
    assert setup.calls.ensure == 0


def test_roles_are_created_only_through_ensure_roles(setup: Setup) -> None:
    """Task 1 carry (M4): `_free_id` ignores `creating` intents, so roles are
    created one after another under the lock, only via `ensure_roles`."""
    setup.run()
    assert setup.calls.ensure == 1


# --- fail-closed and crash-recovery branches of `dirs` (review I2) -------------


def refused_dirs(setup: Setup) -> str:
    with pytest.raises(StepFailed) as info:
        setup.run()
    assert info.value.step == "dirs"
    return str(info.value)


def test_wrong_mode_dir_refused_with_the_way_out(setup: Setup) -> None:
    setup.lay.publisher_dir.mkdir(mode=0o755)
    os.chmod(setup.lay.publisher_dir, 0o755)
    text = refused_dirs(setup)
    assert "0755" in text and "нужны 0700" in text
    assert f"chmod 0700 {setup.lay.publisher_dir}" in text
    assert not [e for e in setup.fake.events if e[0] == "fchown"]


def test_foreign_owner_dir_refused(setup: Setup) -> None:
    setup.lay.secrets_dir.mkdir(mode=0o700)
    setup.fake.set_owner(setup.lay.secrets_dir, 4242)
    assert "владелец uid 4242" in refused_dirs(setup)


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_non_directory_refused(setup: Setup, kind: str) -> None:
    path = setup.lay.runs_dir
    if kind == "file":
        path.write_text("x")
    else:
        path.symlink_to(setup.lay.var)
    assert "не каталог" in refused_dirs(setup)


def test_crash_between_mkdir_and_fchown_is_finished(setup: Setup) -> None:
    work = setup.lay.role_home("df-exec") / "work"
    work.mkdir(mode=0o700)  # born, not yet handed over
    setup.run()
    assert fsutil.owner_uid(os.lstat(work)) == ROLE_UID["df-exec"]


def test_non_empty_unowned_role_dir_refused(setup: Setup) -> None:
    work = setup.lay.role_home("df-exec") / "work"
    work.mkdir(mode=0o700)
    (work / "stray").write_text("x")
    assert "владелец" in refused_dirs(setup)
    assert fsutil.owner_uid(os.lstat(work)) == os.getuid()


def test_inherited_setgid_is_repaired(setup: Setup) -> None:
    setup.lay.libexec.mkdir(mode=0o755)
    os.chmod(setup.lay.libexec, 0o2755)
    setup.run()
    assert stat.S_IMODE(os.lstat(setup.lay.libexec).st_mode) == 0o755


def test_role_dir_repair_needs_it_empty(setup: Setup) -> None:
    """m3: a root-owned narrower role dir with content is not handed over."""
    pub = setup.lay.publisher_dir
    pub.mkdir(mode=0o700)
    (pub / "root-file").write_text("x")
    os.chmod(pub, 0o500)
    assert "права 0500" in refused_dirs(setup)
    assert fsutil.owner_uid(os.lstat(pub)) == os.getuid()


def test_role_dir_repair_when_empty(setup: Setup) -> None:
    pub = setup.lay.publisher_dir
    pub.mkdir(mode=0o700)
    os.chmod(pub, 0o500)
    setup.run()
    st = os.lstat(pub)
    assert stat.S_IMODE(st.st_mode) == 0o700
    assert fsutil.owner_uid(st) == ROLE_UID["df-publisher"]


def test_sudoers_file_with_wrong_mode_is_reinstalled(setup: Setup) -> None:
    setup.run()
    os.chmod(setup.sudoers_file, 0o640)
    setup.run()
    assert len(setup.calls.installed) == 2
    assert stat.S_IMODE(os.lstat(setup.sudoers_file).st_mode) == 0o440


def test_operator_account_must_exist(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str):  # noqa: ANN202
        raise KeyError(name)

    monkeypatch.setattr(pwd, "getpwnam", missing)
    with pytest.raises(StepFailed, match="нет учётной записи") as info:
        setup.run()
    assert info.value.step == "operator"
    assert setup.calls.ensure == 0 and not setup.lay.journal.exists()


# --- PR #641: the /etc/df write is bracketed ----------------------------------


def test_publisher_write_is_inside_a_generation_bracket(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fsutil.write_atomic
    seen: list[int] = []

    def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
        if path.name == "publisher.toml":
            seen.append(journal.read_generation(setup.lay))
        real(path, text, mode)

    monkeypatch.setattr(fsutil, "write_atomic", write_atomic)
    before = journal.read_generation(setup.lay)
    setup.run()
    assert seen == [before + 1]
    assert journal.read_generation(setup.lay) == before + 2


def test_refused_publisher_write_closes_the_bracket(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fsutil.write_atomic

    def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
        if path.name == "publisher.toml":
            raise OSError("disk full")
        real(path, text, mode)

    monkeypatch.setattr(fsutil, "write_atomic", write_atomic)
    with pytest.raises(StepFailed, match="disk full"):
        setup.run()
    assert journal.read_generation(setup.lay) % 2 == 0


# --- PR #643: a stale odd generation is closed even with nothing to change ----


def test_rerun_with_nothing_to_change_closes_a_stale_odd_generation(
    setup: Setup,
) -> None:
    """A writer killed inside its bracket left the generation odd; a re-run
    of `roles setup` that changes nothing still closes it (M9, a `warn`)."""
    setup.run()
    journal.generation_begin(setup.lay)
    gen = journal.read_generation(setup.lay)
    setup.snap = dataclasses.replace(setup.snap, generation=gen)
    setup.run()
    assert journal.read_generation(setup.lay) == gen + 1
    assert len(setup.calls.installed) == 1  # nothing else was written
    assert ("generation", "warn") in setup.steps()


def test_odd_generation_close_waits_for_the_preconditions(setup: Setup) -> None:
    """A refused `roles setup` changes nothing, the odd generation included."""
    journal.generation_begin(setup.lay)
    gen = journal.read_generation(setup.lay)
    setup.snap = dataclasses.replace(snapshot(openssl=False), generation=gen)
    with pytest.raises(StepFailed):
        setup.run()
    assert journal.read_generation(setup.lay) == gen
