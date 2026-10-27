"""Service roles `df-exec` / `df-review` / `df-publisher` (stage 2a, Task 1)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from df import fsutil, models, roles
from df import platform as plat
from df.config import Account, ConfigError, dump_accounts, load_accounts
from df.journal import StepFailed
from df.paths import Layout
from tests.df.account_world import World, account_world

FIRST_ID = os.getuid()


@pytest.fixture()
def layout(tmp_path: Path) -> Layout:
    lay = Layout(Path(os.path.realpath(tmp_path)) / "root")
    lay.install_dir.mkdir(parents=True)
    lay.build_home.parent.mkdir(parents=True)
    lay.quarantine.mkdir(parents=True)
    return lay


@pytest.fixture()
def role_world(monkeypatch: pytest.MonkeyPatch) -> World:
    """Fake OS with ids for three roles; homes are owned by their account."""
    world = account_world(monkeypatch)
    monkeypatch.setattr(
        plat, "ID_RANGE", {"macos-arm64": range(FIRST_ID, FIRST_ID + 3)}
    )
    owners: dict[int, int] = {}

    def fchown(fd: int, uid: int, _gid: int) -> None:
        owners[os.fstat(fd).st_ino] = uid

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(
        fsutil, "owner_uid", lambda st: owners.get(st.st_ino, st.st_uid)
    )
    return world


def test_role_users_exact() -> None:
    assert roles.ROLE_USERS == ("df-exec", "df-review", "df-publisher")
    assert roles.MODEL_ROLE_USERS == {"df-exec", "df-review"}


def test_model_roles_map_into_model_role_users() -> None:
    assert set(models.ROLE_CONTEXT.values()) <= roles.MODEL_ROLE_USERS


@pytest.mark.parametrize("os_name,base", [("linux", "home"), ("darwin", "Users")])
def test_role_home_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str, base: str
) -> None:
    lay = Layout(Path(os.path.realpath(tmp_path)))
    monkeypatch.setattr(sys, "platform", os_name)
    assert lay.role_home("df-exec") == lay.root / base / "df-exec"
    assert lay.build_home == lay.role_home("df-build")


def test_create_service_user_refuses_foreign_record(
    layout: Layout, role_world: World
) -> None:
    role_world.users["df-exec"] = {"RecordName": "df-exec", "UniqueID": "600"}
    with pytest.raises(StepFailed, match="создан не darkfactory") as info:
        plat.create_service_user(layout, "df-exec")
    assert info.value.step == "user:df-exec"
    assert role_world.commands == []


def test_build_user_keeps_its_step(layout: Layout, role_world: World) -> None:
    role_world.users["df-build"] = {"RecordName": "df-build", "UniqueID": "600"}
    with pytest.raises(StepFailed) as info:
        plat.create_build_user(layout)
    assert info.value.step == "build-user"


def test_read_record_failure_names_the_role_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = plat.RunResult(70, b"", b"DS Error", True)
    monkeypatch.setattr(plat, "detect_platform", lambda: "macos-arm64")
    monkeypatch.setattr(plat, "run_command", lambda _a, **_k: failed)
    for name, step in (("df-review", "user:df-review"), ("df-build", "build-user")):
        with pytest.raises(StepFailed) as info:
            plat.read_record("user", name)
        assert info.value.step == step


def test_parsers_take_the_step_from_the_caller() -> None:
    with pytest.raises(StepFailed) as info:
        plat.parse_dscl(b"", "user:df-exec")
    assert info.value.step == "user:df-exec"
    with pytest.raises(StepFailed) as info:
        plat.parse_getent("user", "df-exec", b"")
    assert info.value.step == "user:df-exec"


def test_ensure_roles_creates_all_ready(layout: Layout, role_world: World) -> None:
    accounts = roles.ensure_roles(layout)
    assert list(accounts) == list(roles.ROLE_USERS)
    assert {a.state for a in accounts.values()} == {"ready"}
    for name in roles.ROLE_USERS:
        assert plat.verify_service_user(layout, name) == []
        assert layout.role_home(name).is_dir()
    assert set(load_accounts(layout.accounts)) == set(roles.ROLE_USERS)


def test_ensure_roles_distinct_ids(layout: Layout, role_world: World) -> None:
    accounts = roles.ensure_roles(layout)
    assert len({a.uid for a in accounts.values()}) == 3
    assert len({a.gid for a in accounts.values()}) == 3


def test_ensure_roles_is_idempotent(layout: Layout, role_world: World) -> None:
    first = roles.ensure_roles(layout)
    role_world.commands.clear()
    assert roles.ensure_roles(layout) == first
    assert role_world.commands == []


def test_ensure_roles_resumes_creating(layout: Layout, role_world: World) -> None:
    # df-exec ready; df-review recorded as `creating` with nothing in the OS.
    ready = plat.create_service_user(layout, "df-exec")
    accounts = load_accounts(layout.accounts)
    creating = Account("df-review", FIRST_ID + 1, FIRST_ID + 1, "creating", "t")
    layout.accounts.write_text(dump_accounts({**accounts, "df-review": creating}))
    result = roles.ensure_roles(layout)
    assert result["df-exec"] == ready
    assert result["df-review"].uid == FIRST_ID + 1
    assert {a.state for a in result.values()} == {"ready"}


def test_ensure_roles_does_not_check_sudo(layout: Layout, role_world: World) -> None:
    role_world.sudo = (1, b"sudo: a password is required\n")
    assert {a.state for a in roles.ensure_roles(layout).values()} == {"ready"}


def test_service_names_are_the_roles_and_builder() -> None:
    assert plat.SERVICE_USERS == {*roles.ROLE_USERS, "df-build"}


@pytest.mark.parametrize("name", ["root", "df-other", ""])
def test_service_user_refuses_foreign_name(layout: Layout, name: str) -> None:
    with pytest.raises(ValueError):
        plat.create_service_user(layout, name)
    with pytest.raises(ValueError):
        plat.verify_service_user(layout, name)


@pytest.mark.parametrize("name", ["", "a/b", ".hidden", "../x"])
def test_role_home_rejects_bad_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ConfigError):
        Layout(Path(os.path.realpath(tmp_path))).role_home(name)


def test_other_accounts_reserved_home_is_not_foreign(
    layout: Layout, role_world: World
) -> None:
    (layout.build_home.parent / plat.home_tmp_name("df-review")).mkdir()
    assert plat.create_service_user(layout, "df-exec").state == "ready"


def test_own_reserved_home_is_removed_on_resume(
    layout: Layout, role_world: World
) -> None:
    (layout.build_home.parent / plat.home_tmp_name("df-exec")).mkdir()
    creating = Account("df-exec", FIRST_ID, FIRST_ID, "creating", "t")
    layout.accounts.write_text(dump_accounts({"df-exec": creating}))
    assert plat.create_service_user(layout, "df-exec").state == "ready"


@pytest.mark.parametrize("partial", ["group-only", "user-without-home"])
def test_resume_from_partial_record(
    layout: Layout, role_world: World, partial: str
) -> None:
    uid = FIRST_ID + 1
    creating = Account("df-review", uid, uid, "creating", "t")
    layout.accounts.write_text(dump_accounts({"df-review": creating}))
    group = {"RecordName": "df-review", "PrimaryGroupID": str(uid)}
    role_world.groups["df-review"] = group
    if partial == "user-without-home":
        role_world.users["df-review"] = {
            "RecordName": "df-review",
            "UniqueID": str(uid),
        }
    account = plat.create_service_user(layout, "df-review")
    assert (account.state, account.uid, account.gid) == ("ready", uid, uid)
    assert layout.role_home("df-review").is_dir()
    assert plat.verify_service_user(layout, "df-review") == []


def test_verify_service_user_reports_missing_record(layout: Layout) -> None:
    assert plat.verify_service_user(layout, "df-exec") == [
        "нет записи df-exec в accounts.toml"
    ]
