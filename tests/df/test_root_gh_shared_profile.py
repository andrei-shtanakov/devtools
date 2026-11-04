"""Stage-3 experiment: one shared `gh` profile of the history token (CI only).

Stage-3 prep §5.2 proposes one profile directory `root:df-history 0750`
(files `0640`) that the publisher, the precheck and the operator read with
`gh auth token`. Before spec rev 11 fixes it, this root row measures on a
pinned `gh` (official release, sha256-checked) with explicit file storage
and a fake token (`gh auth token` needs no network):

- every member of the group reads the same token; a non-member does not;
- the profile is byte-for-byte and mtime-unchanged afterwards;
- a missing `hosts.yml`, or a missing token in it, is a refusal;
- what `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`,
  `GITHUB_ENTERPRISE_TOKEN`, `GH_HOST` and (macOS) a keychain item of the
  operator do when the profile is selected through `GH_CONFIG_DIR`.

Facts go to `$DF_STAGE1_OUT/gh-shared-profile/result.json` before any
assertion. Users and groups exist only in the CI root world and are removed
in `finally`; the keychain item lives only in the runner's keychain.
"""

from __future__ import annotations

import grp
import hashlib
import io
import json
import os
import pwd
import stat
import subprocess
import tarfile
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from df import platform as plat
from tests.df.conftest import stage1_out
from tests.df.root_world import infra, write_evidence

pytestmark = [pytest.mark.root, pytest.mark.stage2a]

GH_VERSION = "2.102.1"
#: From `gh_2.102.1_checksums.txt` of the official release.
GH_ASSETS = {
    "macos-arm64": (
        f"gh_{GH_VERSION}_macOS_arm64.zip",
        "08bfef98b50c553df122bb34ee8dcf415cae7d3b108891488964a0df07aa2aeb",
    ),
    "linux-x86_64": (
        f"gh_{GH_VERSION}_linux_amd64.tar.gz",
        "77b9e297e94c50336823d0f84de7ef1be60cba704601fa0d98f129f47a94987b",
    ),
}
GH_URL = f"https://github.com/cli/cli/releases/download/v{GH_VERSION}/"
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
    facts: dict[str, object] = {"platform": plat.detect_platform()}
    gh = _install_gh(acceptance_top)
    facts["gh_version"] = _run([str(gh), "--version"]).stdout.strip()
    facts["help"] = _run([str(gh), "auth", "token", "--help"]).stdout
    with _accounts(acceptance_top) as homes:
        facts["groups"] = {u: _run(["/usr/bin/id", "-Gn", u]).stdout for u in homes}
        profiles = _profiles(acceptance_top)
        facts["cases"] = _cases(gh, profiles, homes)
    write_evidence(stage1_out(), "gh-shared-profile/result.json", _dump(facts))
    print(_dump(facts))
    _judge(facts["cases"])


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
    cases["keychain"] = _keychain_cases(gh, profiles)
    cases["unchanged"] = {
        name: _snapshot(p) == before[name] for name, p in profiles.items()
    }
    cases["snapshot"] = {name: before[name] for name in profiles}
    cases["home-writes"] = {u: _listing(h) for u, h in homes.items()}
    return cases


def _keychain_cases(gh: Path, profiles: dict[str, Path]) -> dict[str, object]:
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
    finally:
        delete = [*security, "delete-generic-password", "-a", ACCOUNT]
        found["delete"] = _brief(_as(OPERATOR, home, [*delete, "-s", f"gh:{HOST}"]))
    return found


def _judge(cases: object) -> None:
    """The properties the shared-profile design needs."""
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


# --- the world ------------------------------------------------------------


def _install_gh(top: Path) -> Path:
    """The pinned official `gh`, sha256-checked, as root `0755`."""
    asset, digest = GH_ASSETS[plat.detect_platform()]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(GH_URL + asset, timeout=120) as resp:
            data = resp.read()
    except OSError as exc:
        infra(f"загрузка {asset}: {exc}")
    if hashlib.sha256(data).hexdigest() != digest:
        infra(f"{asset}: sha256 не совпал")
    member = f"{asset.removesuffix('.zip').removesuffix('.tar.gz')}/bin/gh"
    if asset.endswith(".zip"):
        binary = zipfile.ZipFile(io.BytesIO(data)).read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            extracted = tar.extractfile(member)
            assert extracted is not None
            binary = extracted.read()
    bin_dir = top / "gh-bin"
    bin_dir.mkdir(mode=0o755)
    gh = bin_dir / "gh"
    gh.write_bytes(binary)
    os.chmod(gh, 0o755)
    return gh


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
        f"GH_CONFIG_DIR={profile}",
        "GH_NO_UPDATE_NOTIFIER=1",
        *extra,
        str(gh),
        "auth",
        "token",
        "--hostname",
        HOST,
        *flags,
    ]
    return _brief(_as(user, home, argv))


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
