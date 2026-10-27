"""Fake system accounts shared by the df account tests (platform, roles)."""

from __future__ import annotations

import os
import plistlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from df import platform as plat
from df.platform import PlatformName, RunResult


class Interrupted(Exception):
    """Simulated process death inside `create_build_user`."""


def ok(stdout: bytes = b"", code: int = 0) -> RunResult:
    return RunResult(code, stdout, b"", launched=True)


# --- fake world of system accounts -------------------------------------------


POLICY_BLOB = plistlib.dumps({"creationTime": 1.0}).decode()
#: Attributes macOS stores as native (`dscl -plist . -read /Users/_sentinel`).
NATIVE_ATTRS = frozenset({"IsHidden"})


def _ds_prefix(attr: str) -> str:
    return "dsAttrTypeNative:" if attr in NATIVE_ATTRS else "dsAttrTypeStandard:"


@dataclass
class World:
    """System accounts as seen through `run_command` and pwd/grp."""

    platform: PlatformName
    users: dict[str, dict[str, str]] = field(default_factory=dict)
    groups: dict[str, dict[str, str]] = field(default_factory=dict)
    others: list[SimpleNamespace] = field(default_factory=list)
    other_groups: list[SimpleNamespace] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    sudo: tuple[int, bytes] = (0, b"User df-build is not allowed to run sudo on h.\n")
    extra_groups: tuple[int, ...] = ()
    stop_after_command: int | None = None

    # -- commands --------------------------------------------------------------
    def run(self, argv: Sequence[str], **_kw: object) -> RunResult:
        argv = list(argv)
        if argv[0] == "/usr/bin/sudo":
            return ok(self.sudo[1], self.sudo[0])
        if argv[:4] == ["/usr/bin/dscl", "-plist", ".", "-read"]:
            return self._dscl_read(argv[4])
        if argv[0] == "/usr/bin/getent":
            return self._getent(argv[1], argv[2])
        self.commands.append(argv)
        self._apply(argv)
        if self.stop_after_command == len(self.commands):
            raise Interrupted(f"after {argv}")
        return ok()

    def _table(self, path: str) -> dict[str, dict[str, str]]:
        return self.groups if path.startswith("/Groups/") else self.users

    def _dscl_read(self, path: str) -> RunResult:
        record = self._table(path).get(path.rsplit("/", 1)[1])
        if record is None:
            return RunResult(56, b"", b"DS Error: -14136 (eDSRecordNotFound)", True)
        data = {f"{_ds_prefix(k)}{k}": [v] for k, v in record.items()}
        data["dsAttrTypeNative:accountPolicyData"] = [POLICY_BLOB]
        return ok(plistlib.dumps(data))

    def _getent(self, db: str, name: str) -> RunResult:
        table = self.groups if db == "group" else self.users
        record = table.get(name)
        if record is None:
            return ok(code=2)
        if db == "group":
            return ok(f"{name}:x:{record['gid']}:\n".encode())
        r = record
        line = f"{name}:x:{r['uid']}:{r['gid']}::{r['home']}:{r['shell']}\n"
        return ok(line.encode())

    def _apply(self, argv: list[str]) -> None:
        if argv[0] == "/usr/bin/dscl":
            path = argv[3]
            record = self._table(path).setdefault(path.rsplit("/", 1)[1], {})
            name = path.rsplit("/", 1)[1]
            record.setdefault("RecordName", name)
            if len(argv) == 6:
                record[argv[4]] = argv[5]
            return
        if argv[0] == "/usr/sbin/groupadd":
            self.groups[argv[-1]] = {"gid": argv[3]}
            return
        opts = {a: argv[i + 1] for i, a in enumerate(argv[:-1]) if a.startswith("--")}
        gid = self.groups[opts["--gid"]]["gid"]
        self.users[argv[-1]] = {
            "uid": opts["--uid"],
            "gid": gid,
            "home": opts["--home-dir"],
            "shell": "/usr/sbin/nologin",
        }

    # -- pwd / grp -------------------------------------------------------------
    def _user_entry(self, name: str) -> SimpleNamespace | None:
        r = self.users.get(name)
        if r is None:
            return None
        if self.platform == "macos-arm64":
            if "UniqueID" not in r:
                return None
            return SimpleNamespace(
                pw_name=name,
                pw_uid=int(r["UniqueID"]),
                pw_gid=int(r.get("PrimaryGroupID", "-1")),
                pw_dir=r.get("NFSHomeDirectory", "/var/empty"),
                pw_shell=r.get("UserShell", "/bin/sh"),
            )
        return SimpleNamespace(
            pw_name=name,
            pw_uid=int(r["uid"]),
            pw_gid=int(r["gid"]),
            pw_dir=r["home"],
            pw_shell=r["shell"],
        )

    def _group_entries(self) -> list[SimpleNamespace]:
        key = "PrimaryGroupID" if self.platform == "macos-arm64" else "gid"
        own = [
            SimpleNamespace(gr_name=n, gr_gid=int(r[key]))
            for n, r in self.groups.items()
            if key in r
        ]
        return own + self.other_groups

    def getpwall(self) -> list[SimpleNamespace]:
        own = [e for n in self.users if (e := self._user_entry(n)) is not None]
        return own + self.others

    def getpwnam(self, name: str) -> SimpleNamespace:
        for entry in self.getpwall():
            if entry.pw_name == name:
                return entry
        raise KeyError(name)

    def getgrgid(self, gid: int) -> SimpleNamespace:
        for entry in self._group_entries():
            if entry.gr_gid == gid:
                return entry
        raise KeyError(gid)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(plat, "detect_platform", lambda: self.platform)
        monkeypatch.setattr(plat, "run_command", self.run)
        monkeypatch.setattr(
            plat,
            "pwd",
            SimpleNamespace(getpwnam=self.getpwnam, getpwall=self.getpwall),
        )
        monkeypatch.setattr(
            plat,
            "grp",
            SimpleNamespace(getgrgid=self.getgrgid, getgrall=self._group_entries),
        )
        monkeypatch.setattr(
            plat, "_getgrouplist", lambda _n, gid: [gid, *self.extra_groups]
        )


def account_world(
    monkeypatch: pytest.MonkeyPatch, platform: PlatformName = "macos-arm64"
) -> World:
    world = World(platform)
    world.install(monkeypatch)
    me = os.getuid()
    monkeypatch.setattr(plat, "ID_RANGE", {platform: range(me, me + 1)})
    return world
