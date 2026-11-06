"""The launch contract of `gh` on a real pinned `gh` (CI root only).

Spec 3.10 rev 11 and 4.1 rest on facts first measured by draft devtools#693;
this root row keeps them true on both OSes, on the pinned official `gh`
(`GH_PINNED_VERSION`, sha256-checked against the release's checksums) with
explicit file storage and a fake token (`gh auth token` needs no network):

- the history profile `root:<history> 0750` (files `0640`): every member of
  the group reads the same token, a non-member does not; the profile is
  byte-for-byte and mtime-unchanged afterwards; a missing `hosts.yml`, or a
  missing token in it, is a refusal;
- `GH_TOKEN` and `GITHUB_TOKEN` override the profile (so the allowlist of
  `GH_ENV_KEYS` keeps them from `gh`), the enterprise tokens and `GH_HOST`
  do not; on macOS a keychain item of the operator is read with the login
  `HOME` but not with a separate one (so the call `HOME` is its own); `gh`
  writes nothing to `HOME` but `.local/state/gh/device-id`, and a read-only
  `HOME` works;
- df's own code on that `gh`: `approved_cli_problems` and
  `gh_version_problems` (what `cli set gh` runs) pass it, and
  `act_trust.history_token_at` returns the profile's token with both
  override tokens in the process environment, refuses a profile without
  one, and leaves the role home empty.

Facts go to `$DF_STAGE1_OUT/gh-shared-profile/result.json` before any
assertion. Users and groups exist only in the CI root world and are removed
in `finally`; the keychain item lives only in the runner's keychain.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from df import platform as plat
from governance import act_trust
from governance.act_time import Unavailable
from tests.df.conftest import stage1_out
from tests.df.gh_release import GH_VERSION, install_gh
from tests.df.root_world import infra, write_evidence

#: `network`: `install_gh` downloads the release (deselected without egress).
pytestmark = [pytest.mark.root, pytest.mark.stage2a, pytest.mark.network]

HOST = "github.com"
ACCOUNT = "df-history"
FAKE = "gho_dfSharedProfileFakeToken000000000001"
ENV_FAKE = "gho_dfEnvironmentFakeToken0000000000002"
KEYCHAIN_FAKE = "gho_dfKeychainFakeToken00000000000000003"
HISTORY = "dfghx-hist"
OWN = "dfghx-own"
MEMBERS = ("dfghx-a", "dfghx-b")
OUTSIDER = "dfghx-x"
OPERATOR = "runner"
ENV_VARS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)
MACOS = plat.detect_platform() == "macos-arm64"
#: What `gh` may leave in a `HOME` (measured on both OSes, devtools#693).
HOME_WRITES = {".local", ".local/state", ".local/state/gh", ".local/state/gh/device-id"}

HOSTS = f"""{HOST}:
    users:
        {ACCOUNT}:
            oauth_token: {FAKE}
    git_protocol: https
    oauth_token: {FAKE}
    user: {ACCOUNT}
"""
HOSTS_NO_TOKEN = f"""{HOST}:
    users:
        {ACCOUNT}:
    git_protocol: https
    user: {ACCOUNT}
"""
CONFIG = 'version: "1"\ngit_protocol: https\n'


def test_gh_shared_profile(acceptance_top: Path) -> None:
    """See the module docstring; every fact is recorded first."""
    platform = plat.detect_platform()
    facts: dict[str, object] = {"platform": platform}
    gh = install_gh(acceptance_top)
    facts["gh_version"] = _run([str(gh), "--version"]).stdout.strip()
    facts["help"] = _run([str(gh), "auth", "token", "--help"]).stdout
    facts["df_policy"] = plat.approved_cli_problems({"gh": gh}, platform)
    facts["df_version"] = plat.gh_version_problems(gh)
    with _accounts(acceptance_top) as homes:
        facts["groups"] = {u: _run(["/usr/bin/id", "-Gn", u]).stdout for u in homes}
        profiles = _profiles(acceptance_top)
        facts["cases"] = _cases(gh, profiles, homes)
        facts["contract"] = _contract(gh, profiles, acceptance_top)
    write_evidence(stage1_out(), "gh-shared-profile/result.json", _dump(facts))
    print(_dump(facts))
    _judge(facts["cases"])
    _judge_df(facts)


# --- the cases ------------------------------------------------------------


def _cases(
    gh: Path, profiles: dict[str, Path], homes: dict[str, Path]
) -> dict[str, object]:
    base = profiles["base"]
    before = {name: _snapshot(p) for name, p in profiles.items()}
    cases: dict[str, object] = {}
    for user in (*MEMBERS, OPERATOR, OUTSIDER):
        cases[f"read/{user}"] = _token(gh, user, homes[user], base)
        cases[f"read-no-refresh/{user}"] = _token(
            gh, user, homes[user], base, flags=("--no-refresh",)
        )
    for var in ENV_VARS:
        cases[f"env/{var}"] = _token(
            gh, MEMBERS[0], homes[MEMBERS[0]], base, env={var: ENV_FAKE}
        )
    cases["env/GH_HOST"] = _token(
        gh, MEMBERS[0], homes[MEMBERS[0]], base, env={"GH_HOST": "ghe.example.com"}
    )
    for name in ("no-hosts", "no-token", "no-config"):
        cases[f"{name}/{MEMBERS[0]}"] = _token(
            gh, MEMBERS[0], homes[MEMBERS[0]], profiles[name]
        )
    ro_home = base.parent / "home-read-only"
    ro_home.mkdir(mode=0o755)
    cases[f"read-only-home/{MEMBERS[0]}"] = _token(gh, MEMBERS[0], ro_home, base)
    cases["keychain"] = _keychain_cases(gh, profiles, homes[OPERATOR])
    cases["unchanged"] = {
        name: _snapshot(p) == before[name] for name, p in profiles.items()
    }
    cases["snapshot"] = {name: before[name] for name in profiles}
    cases["home-writes"] = {u: _listing(h) for u, h in homes.items()}
    return cases


def _keychain_cases(
    gh: Path, profiles: dict[str, Path], isolated: Path
) -> dict[str, object]:
    """macOS: an item `gh:github.com`/`df-history` in the operator's keychain."""
    if not MACOS:
        return {"skipped": "linux: no Secret Service on the runner"}
    home = Path(pwd.getpwnam(OPERATOR).pw_dir)
    security = ["/usr/bin/security"]
    add = [*security, "add-generic-password", "-U", "-a", ACCOUNT]
    added = _as(OPERATOR, home, [*add, "-s", f"gh:{HOST}", "-w", KEYCHAIN_FAKE])
    found: dict[str, object] = {"add": _brief(added)}
    try:
        for name in ("base", "no-token"):
            found[name] = _token(gh, OPERATOR, home, profiles[name])
        # The same lookup with HOME away from the login keychain (env reset).
        found["no-token/isolated-home"] = _token(
            gh, OPERATOR, isolated, profiles["no-token"]
        )
    finally:
        delete = [*security, "delete-generic-password", "-a", ACCOUNT]
        found["delete"] = _brief(_as(OPERATOR, home, [*delete, "-s", f"gh:{HOST}"]))
    return found


def _contract(gh: Path, profiles: dict[str, Path], top: Path) -> dict[str, object]:
    """`act_trust.history_token_at` (as root, who reads the profile) with
    both override tokens in this process's environment."""
    role_home = top / "contract-home"
    role_home.mkdir(mode=0o700)
    saved = {k: os.environ.get(k) for k in ("GH_TOKEN", "GITHUB_TOKEN")}
    os.environ.update({k: ENV_FAKE for k in saved})
    try:
        found = {
            name: _outcome(
                act_trust.history_token_at(profiles[name], gh=gh, role_home=role_home)
            )
            for name in ("base", "no-token")
        }
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    found["role-home-after"] = _listing(role_home)
    return found


def _outcome(got: str | Unavailable) -> str:
    return f"unavailable: {got.reason}" if isinstance(got, Unavailable) else got


def _judge(cases: object) -> None:
    """The properties the shared-profile design needs (spec 4.1 rev 11)."""
    assert isinstance(cases, dict)
    for user in (*MEMBERS, OPERATOR):
        got = cases[f"read/{user}"]
        assert (got["rc"], got["stdout"]) == (0, FAKE), f"member {user}: {got}"
    outsider = cases[f"read/{OUTSIDER}"]
    assert outsider["rc"] != 0 and FAKE not in outsider["stdout"], outsider
    assert all(cases["unchanged"].values()), cases["unchanged"]
    for name in ("no-hosts", "no-token"):
        got = cases[f"{name}/{MEMBERS[0]}"]
        assert got["rc"] != 0 and got["stdout"] == "", f"{name}: {got}"
    _judge_launch(cases)


def _judge_launch(cases: dict[str, Any]) -> None:
    """The facts the launch contract rests on (spec 3.10 rev 11)."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        assert cases[f"env/{var}"]["stdout"] == ENV_FAKE, cases[f"env/{var}"]
    for var in ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST"):
        assert cases[f"env/{var}"]["stdout"] == FAKE, cases[f"env/{var}"]
    read_only = cases[f"read-only-home/{MEMBERS[0]}"]
    assert (read_only["rc"], read_only["stdout"]) == (0, FAKE), read_only
    for user, listing in cases["home-writes"].items():
        assert set(listing) <= HOME_WRITES, f"{user}: {listing}"
    keychain = cases["keychain"]
    if MACOS:
        assert keychain["no-token"]["stdout"] == KEYCHAIN_FAKE, keychain
        isolated = keychain["no-token/isolated-home"]
        assert isolated["rc"] != 0 and isolated["stdout"] == "", isolated


def _judge_df(facts: dict[str, object]) -> None:
    """df's own code on the real pinned `gh`."""
    assert GH_VERSION in str(facts["gh_version"]).splitlines()[0]
    assert facts["df_policy"] == [], facts["df_policy"]
    assert facts["df_version"] == [], facts["df_version"]
    contract = facts["contract"]
    assert isinstance(contract, dict)
    assert contract["base"] == FAKE, contract
    assert str(contract["no-token"]).startswith("unavailable: "), contract
    assert contract["role-home-after"] == [], contract


# --- the world ------------------------------------------------------------


def _profiles(top: Path) -> dict[str, Path]:
    """Profile variants, each `root:<history> 0750` with files `0640`."""
    files = {
        "base": {"config.yml": CONFIG, "hosts.yml": HOSTS},
        "no-hosts": {"config.yml": CONFIG},
        "no-token": {"config.yml": CONFIG, "hosts.yml": HOSTS_NO_TOKEN},
        "no-config": {"hosts.yml": HOSTS},
    }
    gid = grp.getgrnam(HISTORY).gr_gid
    out: dict[str, Path] = {}
    for name, content in files.items():
        directory = top / f"history-gh-{name}"
        directory.mkdir(mode=0o750)
        os.chown(directory, 0, gid)
        os.chmod(directory, 0o750)
        for file, text in content.items():
            path = directory / file
            path.write_text(text)
            os.chown(path, 0, gid)
            os.chmod(path, 0o640)
        out[name] = directory
    return out


class _accounts:
    """The test users and groups, removed on exit (also after a failure)."""

    def __init__(self, top: Path) -> None:
        self.top = top

    def __enter__(self) -> dict[str, Path]:
        for name in (*MEMBERS, OUTSIDER, HISTORY, OWN):
            if _exists(name):
                infra(f"{name} уже есть на хосте")
        try:
            return _create(self.top)
        except BaseException:
            _remove()
            raise

    def __exit__(self, *exc: object) -> None:
        _remove()


def _create(top: Path) -> dict[str, Path]:
    users = (*MEMBERS, OUTSIDER)
    homes = {u: top / f"home-{u}" for u in (*users, OPERATOR)}
    if MACOS:
        ids = iter(_free_ids(len(users) + 2))
        own, history = next(ids), next(ids)
        for name, gid in ((OWN, own), (HISTORY, history)):
            _ok(["/usr/bin/dscl", ".", "-create", f"/Groups/{name}"])
            _ok(["/usr/bin/dscl", ".", "-create", f"/Groups/{name}",
                 "PrimaryGroupID", str(gid)])  # fmt: skip
        for user in users:
            path = f"/Users/{user}"
            _ok(["/usr/bin/dscl", ".", "-create", path])
            for key, value in (
                ("UniqueID", str(next(ids))),
                ("PrimaryGroupID", str(own)),
                ("UserShell", "/usr/bin/false"),
                ("NFSHomeDirectory", str(homes[user])),
            ):
                _ok(["/usr/bin/dscl", ".", "-create", path, key, value])
        for user in (*MEMBERS, OPERATOR):
            _ok(["/usr/bin/dscl", ".", "-append", f"/Groups/{HISTORY}",
                 "GroupMembership", user])  # fmt: skip
        _ok(["/usr/bin/dscacheutil", "-flushcache"])
        _ok(["/usr/bin/dsmemberutil", "flushcache"])
    else:
        _ok(["/usr/sbin/groupadd", "-r", OWN])
        _ok(["/usr/sbin/groupadd", "-r", HISTORY])
        for user in users:
            groups = ["-G", HISTORY] if user in MEMBERS else []
            _ok(["/usr/sbin/useradd", "-r", "-M", "-g", OWN, *groups,
                 "-s", "/usr/sbin/nologin", "-d", str(homes[user]), user])  # fmt: skip
        _ok(["/usr/sbin/usermod", "-aG", HISTORY, OPERATOR])
    for user, home in homes.items():
        home.mkdir(mode=0o700)
        entry = pwd.getpwnam(user)
        os.chown(home, entry.pw_uid, entry.pw_gid)
    return homes


def _remove() -> None:
    """Best effort: a failed removal is printed, never raised."""
    try:
        _remove_all()
    except OSError as exc:
        print(f"cleanup: {exc}")


def _remove_all() -> None:
    if MACOS:
        for path in (*(f"/Users/{u}" for u in (*MEMBERS, OUTSIDER)),
                     f"/Groups/{HISTORY}", f"/Groups/{OWN}"):  # fmt: skip
            subprocess.run(["/usr/bin/dscl", ".", "-delete", path], check=False)
        subprocess.run(["/usr/bin/dscacheutil", "-flushcache"], check=False)
    else:
        for user in (*MEMBERS, OUTSIDER):
            subprocess.run(["/usr/sbin/userdel", "-f", user], check=False)
        subprocess.run(["/usr/bin/gpasswd", "-d", OPERATOR, HISTORY], check=False)
        for group in (HISTORY, OWN):
            subprocess.run(["/usr/sbin/groupdel", group], check=False)


def _exists(name: str) -> bool:
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup(name)
            return True
        except KeyError:
            pass
    return False


def _free_ids(count: int) -> list[int]:
    used = {p.pw_uid for p in pwd.getpwall()} | {g.gr_gid for g in grp.getgrall()}
    free = [n for n in range(470, 500) if n not in used]
    if len(free) < count:
        infra("нет свободных UID/GID в 470–499")
    return free[:count]


# --- running and observing ------------------------------------------------


def _token(
    gh: Path,
    user: str,
    home: Path,
    profile: Path,
    *,
    flags: Sequence[str] = (),
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    extra = [f"{k}={v}" for k, v in (env or {}).items()]
    argv = [
        *_contract_env_tail(profile),
        *extra,
        str(gh),
        "auth",
        "token",
        "--hostname",
        HOST,
        *flags,
    ]
    return _brief(_as(user, home, argv))


def _contract_env_tail(profile: Path) -> list[str]:
    """The keys of `GH_ENV_KEYS` after `PATH`, `HOME`, `LANG` (`_as`)."""
    tail = {"GH_CONFIG_DIR": str(profile), "GH_NO_UPDATE_NOTIFIER": "1"}
    assert ("PATH", "HOME", "LANG", *tail) == plat.GH_ENV_KEYS
    return [f"{k}={v}" for k, v in tail.items()]


def _as(user: str, home: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """`argv` as `user` with a reset environment (the role launch shape)."""
    return _run(
        [
            "/usr/bin/sudo",
            "-n",
            "-u",
            user,
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            f"HOME={home}",
            "LANG=C",
            *argv,
        ]  # fmt: skip
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=60, check=False, cwd="/"
    )


def _ok(argv: list[str]) -> None:
    done = _run(argv)
    if done.returncode != 0:
        infra(f"{' '.join(argv)}: код {done.returncode}: {done.stderr.strip()}")


def _brief(done: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "rc": done.returncode,
        "stdout": done.stdout.strip(),
        "stderr": done.stderr.strip()[:500],
    }


def _snapshot(directory: Path) -> list[list[object]]:
    """Every entry with type, mode, owner, size, mtime and content digest."""
    rows: list[list[object]] = []
    for path in sorted([directory, *directory.rglob("*")]):
        st = os.lstat(path)
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(st.st_mode)
            else ""
        )
        rel = str(path.relative_to(directory)) or "."
        rows.append(
            [
                rel,
                oct(st.st_mode),
                st.st_uid,
                st.st_gid,
                st.st_size,
                st.st_mtime_ns,
                digest,
            ]  # fmt: skip
        )
    return rows


def _listing(home: Path) -> list[str]:
    return sorted(str(p.relative_to(home)) for p in home.rglob("*"))


def _dump(facts: object) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
