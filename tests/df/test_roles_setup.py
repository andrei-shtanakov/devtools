"""`darkfactory roles setup` (stage 2a, Task 4): steps, order, idempotence."""

from __future__ import annotations

import os
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
    monkeypatch.setattr(sudoers, "SUDOERS_FILE", sudoers_file)
    monkeypatch.setattr(sudoers, "install", install)
    monkeypatch.setattr(sudoers, "policy_problems", policy)
    monkeypatch.setattr(sudoers, "io_log_problems", io_log)
    return Setup(fake, calls, sudoers_file, snapshot())


def test_steps_order_without_perimeter_init(setup: Setup) -> None:
    setup.run()
    seen = list(dict.fromkeys(step for step, _event in setup.steps()))
    assert seen == ["roles", "dirs", "publisher-config", "libexec", "sudoers", "verify"]
    assert tuple(seen) == roles_setup.STEP_NAMES
    assert ("verify", "skip") in setup.steps()  # a pure check: nothing to do
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
    assert new == [(name, "skip") for name in roles_setup.STEP_NAMES]
    assert not [e for e in setup.fake.events if e[0] in ("created", "chmod")]


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
