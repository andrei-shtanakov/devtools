"""Step 0 of CI df-stage1 (Task 13-A): facts of the runner, recorded, not judged.

`.github/workflows/df-stage1.yml` runs it twice into one directory:

    python -m tests.df.ci_step0 runner --out DIR              # runner user
    sudo env ... python -m tests.df.ci_step0 root --out DIR --uv UV   # root

Each part writes `DIR/<part>.json` plus raw captures (`sudo-l/`, eslogger and
ausearch samples). A fact that is merely unavailable (eslogger without Full
Disk Access, no `auditctl`, a refused `file://` fetch) is a recorded value,
never a failure: the script exits 0 once its file is written. Only the
workflow's arch check and mechanism probes fail the job.

The probes call the production code where one exists (`approved_cli_problems`,
`trusted_path_problems`, `parse_sudo_rules`, `run_command`, the git argv and
isolation of the build), so the facts are the ones the root tests will meet.
Never run on a workstation: the runner part calls `sudo`, the root part
changes ownership under `/var`. Both parts refuse outside their setting.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from df import platform as plat
from df.build import GIT_ISOLATION, git_fetch_argv, git_rev_parse_argv
from df.lockset import UV_PINNED_VERSION

Facts = dict[str, object]
Probe = Callable[[], object]

TEXT_LIMIT = 64 << 10
#: R83: the bases of the root world's layouts (made root 0755).
BASES: Mapping[plat.PlatformName, Path] = {
    "macos-arm64": Path("/private/var/tmp-df"),
    "linux-x86_64": Path("/var/tmp-df"),
}
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_TAR = Path("/usr/bin/tar")
#: The stand-in for `df-build` (absent in Step 0) as "another user".
OTHER_USER = "nobody"
ESLOGGER = Path("/usr/bin/eslogger")
ESLOGGER_EVENTS = ("open", "readdir", "exec")
SBIN_PATH = "/usr/sbin:/sbin:/usr/bin:/bin"
#: x86_64 numbers of the syscalls the observer rule lists (spec 3.8 r5).
X86_64_SYSCALLS: Mapping[int, str] = {
    2: "open",
    59: "execve",
    217: "getdents64",
    257: "openat",
    437: "openat2",
}
#: R93 twins: how root reaches a source file; each runs as its own process.
AUDIT_TWINS: Mapping[str, str] = {
    "abs-open": "os.close(os.open(P + '/f', os.O_RDONLY))",
    "openat-relative": "d = os.open(P, D); os.close(os.open('f', os.O_RDONLY, dir_fd=d))",
    "path-listdir": "os.listdir(P)",
    "held-fd-getdents": "d = os.open(P, D); os.listdir(d)",
}
_FIXED_DATE = "2026-10-01T00:00:00+00:00"
_AUDIT_RECORD = re.compile(r"type=(\w+) msg=audit\([\d.]+:(\d+)\): (.*)")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one part and write `<out>/<part>.json`; 2 when refused."""
    parser = argparse.ArgumentParser(prog="ci_step0")
    parser.add_argument("part", choices=("runner", "root"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--uv", type=Path, help="the runner's uv (root part)")
    args = parser.parse_args(argv)
    refusal = refusal_reason(args.part, os.geteuid(), os.environ)
    if refusal is not None:
        print(f"ci_step0: {refusal}", file=sys.stderr)
        return 2
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    if args.part == "runner":
        facts = collect(runner_probes())
    else:
        facts = run_root(out, args.uv)
    path = out / f"{args.part}.json"
    path.write_text(
        json.dumps(facts, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    )
    print(f"ci_step0: {args.part}: {len(facts)} facts -> {path}")
    return 0


def refusal_reason(part: str, euid: int, env: Mapping[str, str]) -> str | None:
    """Why this part must not run here; `None` when it may."""
    if part == "runner" and env.get("GITHUB_ACTIONS") != "true":
        return "часть runner вызывает sudo — только в CI (GITHUB_ACTIONS=true)"
    if part == "root" and (euid != 0 or env.get("DF_ROOT_ACCEPTANCE") != "1"):
        return "часть root — только euid 0 с DF_ROOT_ACCEPTANCE=1 (CI df-stage1)"
    return None


def collect(probes: Mapping[str, Probe]) -> Facts:
    """Every probe's value; a raising probe becomes `{"error": ...}`."""
    facts: Facts = {}
    for name, probe in probes.items():
        try:
            facts[name] = probe()
        except Exception as exc:  # noqa: BLE001 - a fact, not a failure
            facts[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return facts


def cmd(
    argv: Sequence[str],
    *,
    timeout: float = 60.0,
    env: Mapping[str, str] | None = None,
) -> Facts:
    """Run `argv` and record its exit code and output (never raises)."""
    argv = [str(a) for a in argv]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=None if env is None else dict(env),
            cwd="/",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "argv": argv,
        "rc": done.returncode,
        "stdout": _text(done.stdout),
        "stderr": _text(done.stderr),
    }


def _text(data: bytes) -> str:
    return data[:TEXT_LIMIT].decode("utf-8", "replace")


# --- runner part --------------------------------------------------------------


def runner_probes() -> dict[str, Probe]:
    """Facts seen by the runner user: host, tools, sudo's HOME and PATH."""
    return {
        "uname": lambda: cmd(["/usr/bin/uname", "-a"]),
        "id": lambda: cmd(["/usr/bin/id"]),
        "os": _os_release,
        "uv": _uv_version,
        "tar": lambda: cmd([SYSTEM_TAR, "--version"]),
        "git": lambda: cmd([SYSTEM_GIT, "--version"]),
        "sudo": _sudo_env,
    }


def _os_release() -> object:
    if sys.platform == "darwin":
        return cmd(["/usr/bin/sw_vers"])
    return Path("/etc/os-release").read_text()


def _uv_version() -> Facts:
    found = shutil.which("uv")
    if found is None:
        return {"path": None}
    version = cmd([found, "--version"])
    words = str(version.get("stdout", "")).split()
    return {
        "path": found,
        "version": version,
        "pinned": UV_PINNED_VERSION,
        "matches_pin": words[1:2] == [UV_PINNED_VERSION],
    }


def _sudo_env() -> Facts:
    """R76: which HOME/PATH a plain `sudo` gives root, and with `-H`."""
    sudo = plat._SUDO
    return {
        "runner_HOME": os.environ.get("HOME"),
        "HOME": cmd([sudo, "-n", "/usr/bin/printenv", "HOME"]),
        "HOME_with_H": cmd([sudo, "-n", "-H", "/usr/bin/printenv", "HOME"]),
        "PATH": cmd([sudo, "-n", "/usr/bin/printenv", "PATH"]),
        "version": cmd([sudo, "-V"]),
    }


# --- root part ----------------------------------------------------------------


def run_root(out: Path, uv: Path | None) -> Facts:
    """Root facts; the ones needing a scratch tree live under the R83 base."""
    platform = plat.detect_platform()
    facts = collect(
        {
            "python": lambda: {"version": sys.version, "executable": sys.executable},
            "platform": lambda: platform,
            "ids": lambda: id_facts(platform),
            "sudo_l": lambda: sudo_l_facts(out),
        }
    )
    try:
        base, created = make_base(BASES[platform])
    except OSError as exc:
        facts["base"] = {"error": f"{type(exc).__name__}: {exc}"}
        return facts
    scratch = base / f"step0-{os.getpid()}"
    scratch.mkdir(mode=0o755)
    try:
        facts |= collect(_scratch_probes(platform, base, created, scratch, out, uv))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if created:
            _rmdir_quiet(base)
    return facts


def _scratch_probes(
    platform: plat.PlatformName,
    base: Path,
    created: bool,
    scratch: Path,
    out: Path,
    uv: Path | None,
) -> dict[str, Probe]:
    probes: dict[str, Probe] = {
        "base": lambda: base_facts(base, created),
        "approved_cli": lambda: approved_cli_facts(platform, scratch, uv),
        "git_file": lambda: git_file_facts(platform, scratch),
    }
    if platform == "macos-arm64":
        probes["eslogger"] = lambda: eslogger_facts(scratch, out)
    else:
        probes["audit"] = lambda: audit_facts(scratch, out)
    return probes


def _rmdir_quiet(path: Path) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def make_base(base: Path) -> tuple[Path, bool]:
    """The R83 base as root `0755`; `True` when this call created it."""
    if base.exists() or base.is_symlink():
        return base, False
    os.mkdir(base, 0o755)
    os.chown(base, 0, 0)
    os.chmod(base, 0o755)
    return base, True


def base_facts(base: Path, created: bool) -> Facts:
    """R83: owner/mode/ACL of the base and every parent; the policy's verdict."""
    chain = sorted({*base.parents, base, Path("/var")}, key=lambda p: len(p.parts))
    return {
        "path": str(base),
        "created_by_step0": created,
        "trusted_path_problems": plat.trusted_path_problems(base),
        "components": {str(p): _path_facts(p) for p in chain},
    }


def _path_facts(path: Path) -> Facts:
    st = os.lstat(path)
    facts: Facts = {"uid": st.st_uid, "gid": st.st_gid, "mode": oct(st.st_mode)}
    if path.is_symlink():
        facts["link"] = os.readlink(path)
    if sys.platform == "darwin":
        facts["acl"] = cmd(["/bin/ls", "-lde", path])
    elif (getfacl := shutil.which("getfacl", path=SBIN_PATH)) is not None:
        facts["acl"] = cmd([getfacl, "-p", path])
    else:
        facts["acl"] = "getfacl отсутствует"
    return facts


def approved_cli_facts(
    platform: plat.PlatformName, scratch: Path, uv: Path | None
) -> Facts:
    """R88: `approved_cli_problems` as root for git, tar and a root uv copy."""
    tools = {"git": SYSTEM_GIT, "tar": SYSTEM_TAR}
    facts: Facts = {}
    if uv is not None:
        cli = scratch / "cli"
        copy = cli / "uv"
        facts["install"] = [
            cmd(["/usr/bin/install", "-d", "-o", "root", "-m", "0755", cli]),
            cmd(["/usr/bin/install", "-o", "root", "-m", "0755", uv, copy]),
        ]
        facts["uv_copy_version"] = cmd([copy, "--version"])
        tools["uv"] = copy
    facts["realpaths"] = {n: os.path.realpath(p) for n, p in tools.items()}
    facts["each"] = {
        n: plat.approved_cli_problems({n: p}, platform) for n, p in tools.items()
    }
    facts["all"] = plat.approved_cli_problems(tools, platform)
    if platform == "macos-arm64":
        facts["xcrun_find_git"] = cmd(["/usr/bin/xcrun", "--find", "git"])
        facts["xcode_select"] = cmd(["/usr/bin/xcode-select", "-p"])
    return facts


def id_facts(platform: plat.PlatformName) -> Facts:
    """Used and free ids of the account range (`ID_RANGE`)."""
    uids = {p.pw_uid for p in pwd.getpwall()}
    gids = {g.gr_gid for g in grp.getgrall()}
    if platform == "macos-arm64":
        uids |= dscl_ids(_stdout(cmd([plat._DSCL, ".", "-list", "/Users", "UniqueID"])))
        gids |= dscl_ids(
            _stdout(cmd([plat._DSCL, ".", "-list", "/Groups", "PrimaryGroupID"]))
        )
    rng = plat.ID_RANGE[platform]
    free = free_ids(rng, uids, gids)
    return {
        "range": [rng.start, rng.stop - 1],
        "used_uids": sorted(u for u in uids if u in rng),
        "used_gids": sorted(g for g in gids if g in rng),
        "free_count": len(free),
        "first_free": free[0] if free else None,
        "build_user_present": _user_exists(plat.BUILD_USER),
    }


def _stdout(facts: Facts) -> str:
    return str(facts.get("stdout", ""))


def dscl_ids(text: str) -> set[int]:
    """Ids of `dscl . -list <dir> <attr>` lines (`name  id`)."""
    ids: set[int] = set()
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 2 and re.fullmatch(r"-?\d+", words[-1]):
            ids.add(int(words[-1]))
    return ids


def free_ids(rng: range, uids: set[int], gids: set[int]) -> list[int]:
    """Ids in `rng` used neither as a uid nor as a gid."""
    return [i for i in rng if i not in uids and i not in gids]


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def sudo_l_facts(out: Path) -> Facts:
    """R74: real `sudo -n -l -U <user>` texts, in the fixture file format."""
    target = out / "sudo-l"
    target.mkdir(exist_ok=True)
    users = [os.environ.get("SUDO_USER") or "runner", OTHER_USER, plat.BUILD_USER]
    facts: Facts = {"hostname": socket.gethostname()}
    for user in users:
        result = plat.run_command([plat._SUDO, "-n", "-l", "-U", user])
        (target / f"{user}.out").write_bytes(result.stdout)
        (target / f"{user}.err").write_bytes(result.stderr)
        (target / f"{user}.rc").write_text(f"{result.returncode}\n")
        facts[user] = {
            "launched": result.launched,
            "rc": result.returncode,
            "stdout": _text(result.stdout),
            "stderr": _text(result.stderr),
            "parsed": plat.parse_sudo_rules(user, result.returncode, result.stdout),
        }
    return facts


def git_file_facts(platform: plat.PlatformName, scratch: Path) -> Facts:
    """R91: a `file://` fetch of a sha, repo owner x fetcher in {root, other}."""
    work = scratch / "git"
    work.mkdir(mode=0o755)
    git = str(SYSTEM_GIT)
    env = _git_env(work / "author")
    src = work / "src"
    setup = [
        cmd([git, "init", "-q", "--template=", src], env=env),
        cmd(
            [git, "-C", src, "-c", "user.name=step0", "-c", "user.email=s@step0"]
            + ["commit", "-q", "--allow-empty", "-m", "step0"],
            env=env,
        ),
    ]
    head = cmd([git, "-C", src, "rev-parse", "HEAD"], env=env)
    sha = _stdout(head).strip()
    other = pwd.getpwnam(OTHER_USER)
    facts: Facts = {"setup": [*setup, head], "sha": sha}
    for owner in ("root", "other"):
        bare = work / f"bare-{owner}.git"
        facts[f"clone-{owner}"] = cmd(
            [git, "clone", "-q", "--bare", src, bare], env=env
        )
        if owner == "other":
            _chown_tree(bare, other.pw_uid, other.pw_gid)
        for fetcher in ("root", "other"):
            facts[f"{owner}-repo/{fetcher}-fetch"] = _fetch_as(
                platform, fetcher, f"file://{bare}", sha, work / f"w-{owner}-{fetcher}"
            )
    return facts


def _git_env(home: Path) -> dict[str, str]:
    for sub in ("home", "config"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "HOME": str(home / "home"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "GIT_AUTHOR_DATE": _FIXED_DATE,
        "GIT_COMMITTER_DATE": _FIXED_DATE,
        **GIT_ISOLATION,
    }


def _fetch_as(
    platform: plat.PlatformName, fetcher: str, url: str, sha: str, work: Path
) -> Facts:
    """init, fetch and rev-parse with the build's argv; stop at a failure."""
    env = _git_env(work)
    repo = str(work / "git")
    prefix: list[str] = []
    if fetcher == "other":
        entry = pwd.getpwnam(OTHER_USER)
        _chown_tree(work, entry.pw_uid, entry.pw_gid)
        prefix = [*_as_other(platform, entry), plat._ENV, "-i"]
        prefix += [f"{k}={v}" for k, v in env.items()]
    git = str(SYSTEM_GIT)
    steps = [
        ("init", [git, "init", "-q", "--template=", repo]),
        ("fetch", git_fetch_argv(git, repo, url, sha)),
        ("rev-parse", git_rev_parse_argv(git, repo)),
    ]
    facts: Facts = {}
    head = ""
    for label, argv in steps:
        done = cmd([*prefix, *argv], env=None if prefix else env)
        facts[label] = done
        if done.get("rc") != 0:
            facts["ok"] = False
            return facts
        head = _stdout(done).strip()
    facts["ok"] = head == sha
    return facts


def _as_other(platform: plat.PlatformName, entry: pwd.struct_passwd) -> list[str]:
    if platform == "macos-arm64":
        return [plat._SUDO, "-n", "-u", entry.pw_name]
    group = grp.getgrgid(entry.pw_gid).gr_name
    return [
        plat._SETPRIV,
        f"--reuid={entry.pw_name}",
        f"--regid={group}",
        "--init-groups",
        "--",
    ]


def _chown_tree(top: Path, uid: int, gid: int) -> None:
    # macOS `nobody` is -2 (signed) or 4294967294; `lchown` wants unsigned.
    uid, gid = uid % (1 << 32), gid % (1 << 32)
    os.lchown(top, uid, gid)
    for parent, dirs, files in os.walk(top):
        for name in [*dirs, *files]:
            os.lchown(os.path.join(parent, name), uid, gid)


def eslogger_facts(scratch: Path, out: Path) -> Facts:
    """macOS: event names of `eslogger`, and whether root may run it here."""
    if not ESLOGGER.exists():
        return {"present": False}
    listing = cmd([ESLOGGER, "--list-events"])
    (out / "eslogger-list-events.txt").write_text(_stdout(listing))
    names = set(_stdout(listing).split())
    return {
        "present": True,
        "list_events_rc": listing.get("rc"),
        "list_events_stderr": listing.get("stderr"),
        "has": {e: e in names for e in ESLOGGER_EVENTS},
        "run": _eslogger_run(scratch, out),
    }


def _eslogger_run(scratch: Path, out: Path) -> Facts:
    control = scratch / "es-control"
    control.mkdir()
    (control / "f").write_text("step0\n")
    raw, err = scratch / "es-raw.jsonl", scratch / "es-err.txt"
    with raw.open("wb") as stdout, err.open("wb") as stderr:
        proc = subprocess.Popen(
            [str(ESLOGGER), *ESLOGGER_EVENTS, "--format", "json"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        time.sleep(3.0)
        for _ in range(3):
            (control / "f").read_text()
            os.listdir(control)
            time.sleep(0.2)
        time.sleep(2.0)
        exited_early = proc.poll() is not None
        if not exited_early:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    lines = raw.read_bytes().decode("utf-8", "replace").splitlines()
    hits = [line for line in lines if str(control) in line]
    (out / "eslogger-sample.jsonl").write_text("\n".join([*lines[:20], *hits[:20]]))
    return {
        "exited_before_signal": exited_early,
        "rc": proc.returncode,
        "stderr": _text(err.read_bytes()),
        "events": len(lines),
        "control_events": len(hits),
        "permitted": bool(hits),
    }


def audit_facts(scratch: Path, out: Path) -> Facts:
    """Linux: audit status and the R93 `-F dir=` twins (held fd, openat)."""
    auditctl = shutil.which("auditctl", path=SBIN_PATH)
    ausearch = shutil.which("ausearch", path=SBIN_PATH)
    if auditctl is None or ausearch is None:
        return {"present": False, "auditctl": auditctl, "ausearch": ausearch}
    return {
        "present": True,
        "status": cmd([auditctl, "-s"]),
        "version": cmd([auditctl, "-v"]),
        "auditd_active": cmd(["/usr/bin/systemctl", "is-active", "auditd"]),
        "dir_twins": _audit_twins(auditctl, ausearch, scratch, out),
    }


def _audit_twins(auditctl: str, ausearch: str, scratch: Path, out: Path) -> Facts:
    source = scratch / "audit-src"
    source.mkdir()
    (source / "f").write_text("step0\n")
    key = f"df-step0-{os.getpid()}"
    rule = ["-F", "arch=b64", "-S", "open,openat,openat2,getdents64,execve"]
    rule += ["-F", f"dir={source}", "-F", "euid=0", "-k", key]
    facts: Facts = {"add": cmd([auditctl, "-a", "always,exit", *rule])}
    try:
        facts["rules"] = cmd([auditctl, "-l"])
        pids = {name: _run_twin(code, source) for name, code in AUDIT_TWINS.items()}
        pids["cat-child"] = _spawn_pid(["/bin/cat", str(source / "f")])
        time.sleep(2.0)
        search = cmd([ausearch, "-k", key, "--format", "raw"])
        raw = _stdout(search)
        (out / "ausearch-raw.txt").write_text(raw)
        seen = audit_events(raw)
        facts["ausearch_rc"] = search.get("rc")
        facts["twins"] = {
            name: {"pid": pid, "events": seen.get(pid, [])}
            for name, pid in pids.items()
        }
    finally:
        facts["delete"] = cmd([auditctl, "-d", "always,exit", *rule])
    return facts


def _run_twin(code: str, source: Path) -> int:
    prelude = "import os; P = os.environ['P']; D = os.O_RDONLY | os.O_DIRECTORY; "
    argv = [sys.executable, "-S", "-c", prelude + code]
    return _spawn_pid(argv, env={"P": str(source)})


def _spawn_pid(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=None if env is None else dict(env),
        cwd="/",
    )
    proc.wait(timeout=30)
    return proc.pid


def audit_events(raw: str) -> dict[int, list[dict[str, object]]]:
    """`ausearch --format raw` → per pid: each event's syscall and PATH names."""
    events: dict[str, dict[str, object]] = {}
    for line in raw.splitlines():
        match = _AUDIT_RECORD.match(line.strip())
        if match is None:
            continue
        kind, serial, body = match.groups()
        event = events.setdefault(serial, {"names": []})
        if kind == "SYSCALL":
            fields = dict(re.findall(r"\b(\w+)=(\S+)", body))
            number = int(fields.get("syscall", "-1"))
            event["syscall"] = X86_64_SYSCALLS.get(number, str(number))
            event["pid"] = int(fields.get("pid", "-1"))
        elif kind == "PATH":
            name = re.search(r'\bname=("[^"]*"|\S+)', body)
            if name is not None:
                names = event["names"]
                assert isinstance(names, list)
                names.append(name.group(1).strip('"'))
    by_pid: dict[int, list[dict[str, object]]] = {}
    for event in events.values():
        pid = event.pop("pid", None)
        if isinstance(pid, int):
            by_pid.setdefault(pid, []).append(event)
    return by_pid


if __name__ == "__main__":
    raise SystemExit(main())
