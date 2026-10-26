"""Live install of profile `core` in the production layout `/` (CI df-stage1).

`.github/workflows/df-stage1.yml` runs it as the runner user, after the root
run (whose worlds need `df-build` absent; this install leaves it and `/opt/df`
for good, stage 1 has no uninstall):

    python -m tests.df.live_install --out DIR --work DIR

What README «Установка DarkFactory: этап 1» says, as processes:

1. No state of an earlier install (`df-build`, `/opt/df`, `/etc/df`,
   `/var/lib/df`, the uv directory, the command link); any is a finding.
2. «Подготовка»: on macOS the Command Line Tools selected (the workflow's R96
   step); the uv 0.11.23 release asset checked against its published
   `.sha256` (and the README's value), its version, then `sudo install` to
   `UV_DIR` root `0755`; the bootstrap Python (Ubuntu `/usr/bin/python3`;
   macOS the README recipe, owner decision 26.10: `sudo <uv> python install
   PYTHON --install-dir BOOTSTRAP_PYTHON_DIR`), which must pass
   `trusted_path_problems` (a problem is a finding). The `/opt` chain is the
   workflow's host-configuration step, before this script.
3. The README first-install command verbatim (`documented_command`, the
   placeholders filled), a process of root through `sudo`.
4. `sudo darkfactory doctor --json`: level 1 reached, every check `ok`.
5. Every declared console entrypoint of every member `--help` as this
   (unprivileged) user, exit 0; then `doctor` again, every check `ok`.

Writes `DIR/live/<platform>-production.json` (and the raw doctor reports)
whatever happens; exits 1 on any failure, 0 only when every step passed.
Refuses to run as root or outside CI (`GITHUB_ACTIONS`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from df import platform as plat
from df.boundary import expected_entrypoints
from df.lockset import UV_PINNED_VERSION
from df.manifest import fetch_manifest, parse_profile
from df.paths import Layout
from df.release import RELEASE_JSON
from tests.df.root_world import CORE_MANIFEST, CORE_PROFILE, PYTHON
from tests.df.test_cli import PYTHON_PLACEHOLDER, SHA_PLACEHOLDER, documented_command

#: README «Утверждённые CLI»: the approved copy of uv.
UV_DIR = Path("/usr/local/libexec/darkfactory/bin")
UV_RELEASE = f"https://github.com/astral-sh/uv/releases/download/{UV_PINNED_VERSION}"
#: README: release asset target and its published sha256 (26.10).
UV_TARGETS: Mapping[str, tuple[str, str]] = {
    "macos-arm64": (
        "aarch64-apple-darwin",
        "71ef9de85db820749b3b12b7585624ee279e9c5afcbc6f8236bc3d628c4305b0",
    ),
    "linux-x86_64": (
        "x86_64-unknown-linux-gnu",
        "e12c4cda2fe8c305510a78380a88f2c32a27e90cdcd123cefd2873388f0ebb5f",
    ),
}
#: README: how the human checks the archive.
SHA_CHECK: Mapping[str, list[str]] = {
    "macos-arm64": ["/usr/bin/shasum", "-a", "256", "-c"],
    "linux-x86_64": ["/usr/bin/sha256sum", "-c"],
}
CLT = "/Library/Developer/CommandLineTools"
LINUX_PYTHON = "/usr/bin/python3"
#: README «Подготовка» (owner decision 26.10): the macOS bootstrap Python,
#: installed by root with the approved uv, outside the df release tree.
BOOTSTRAP_PYTHON_DIR = Path("/usr/local/libexec/darkfactory-bootstrap/python")
CHECKOUT_PLACEHOLDER = "<чекаут-devtools>"
USER_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}
TAIL = 4000


class Failure(Exception):
    """A step failed; the message names it."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live install; write the facts; 0 only when all passed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.geteuid() == 0 or os.environ.get("GITHUB_ACTIONS") != "true":
        print("live_install: only as the CI runner user", file=sys.stderr)
        return 2
    platform = plat.detect_platform()
    facts: dict[str, Any] = {"platform": platform, "user": os.getuid()}
    live = args.out / "live"
    live.mkdir(parents=True, exist_ok=True)
    code = 1
    try:
        run(platform, args.work, live, facts)
        code = 0
    except Failure as exc:
        facts["failure"] = str(exc)
        print(f"live_install: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - recorded, and the step fails
        facts["failure"] = f"{type(exc).__name__}: {exc}"
        print(f"live_install: {facts['failure']}", file=sys.stderr)
    finally:
        facts["passed"] = code == 0
        text = json.dumps(facts, indent=2, ensure_ascii=False, sort_keys=True)
        (live / f"{platform}-production.json").write_text(text + "\n")
    return code


def run(platform: plat.PlatformName, work: Path, live: Path, facts: dict) -> None:
    """Steps 1-5 of the module doc."""
    layout = Layout(Path("/"))
    facts["leftovers"] = leftovers(layout)
    if facts["leftovers"]:
        raise Failure(f"находка: состояние до установки {facts['leftovers']}")
    if platform == "macos-arm64":
        selected = _out(["/usr/bin/xcode-select", "-p"]).strip()
        facts["xcode_select"] = selected
        if selected != CLT:
            raise Failure(f"xcode-select -p {selected!r}, нужен {CLT}")
    work.mkdir(parents=True, exist_ok=True)
    facts["uv"] = prepare_uv(platform, work)
    python = bootstrap_python(platform, work, facts)
    argv = install_argv(python)
    facts["init"] = _run(argv, timeout=3600)
    if facts["init"]["code"] != 0:
        raise Failure(f"init: код {facts['init']['code']}")
    doctor_argv = command_argv(layout, ["doctor", "--json"])
    facts["doctor"] = doctor(doctor_argv, live / f"{platform}-production-doctor.json")
    pins = core_pins()
    facts["pins"] = pins
    release = layout.current.resolve()
    facts["release"] = release_facts(release, pins)
    facts["smoke"] = smoke(release, list(pins), work)
    after = live / f"{platform}-production-doctor-after-smoke.json"
    facts["doctor_after_smoke"] = doctor(doctor_argv, after)


def leftovers(layout: Layout) -> dict[str, str]:
    """State an earlier install (or the root run) left behind."""
    found = {
        str(p): "exists"
        for p in (
            layout.opt,
            layout.etc,
            layout.var,
            UV_DIR.parent.parent,
            BOOTSTRAP_PYTHON_DIR.parent,
            layout.local_bin / "darkfactory",
            layout.build_home,
        )
        if os.path.lexists(p)
    }
    for kind in ("user", "group"):
        record = plat.read_record(kind, plat.BUILD_USER)
        if record is not None:
            found[f"{kind} {plat.BUILD_USER}"] = json.dumps(record, sort_keys=True)
    return found


def bootstrap_python(platform: plat.PlatformName, work: Path, facts: dict) -> str:
    """README «Подготовка» 1: Ubuntu's `/usr/bin/python3`; macOS the root
    install by the approved uv. Either must be trusted and >= 3.12."""
    if platform == "linux-x86_64":
        python = LINUX_PYTHON
    else:
        python = _install_python(work, facts)
    version = _out([python, "-I", "-c", "import sys; print(*sys.version_info[:3])"])
    problems = plat.trusted_path_problems(Path(python))
    facts["python"] = {
        "path": python,
        "real": os.path.realpath(python),
        "version": version.split(),
        "problems": problems,
    }
    if problems:
        raise Failure(f"находка: Python bootstrap не доверенный: {facts['python']}")
    if not _at_least_312(version):
        raise Failure(f"Python bootstrap {version!r}: нужен >= 3.12")
    return python


def _install_python(work: Path, facts: dict) -> str:
    """`sudo <uv> python install PYTHON --install-dir BOOTSTRAP_PYTHON_DIR`;
    the one `cpython-PYTHON-*/bin/python3.12` it made. Where the uv run left
    its executables links is recorded (`~/.local/bin` of the `sudo` HOME)."""
    argv = ["sudo", str(UV_DIR / "uv"), "python", "install", PYTHON]
    argv += ["--install-dir", str(BOOTSTRAP_PYTHON_DIR)]
    done = _run(argv, timeout=900, cwd=work)
    facts["python_install"] = done
    if done["code"] != 0:
        raise Failure(f"uv python install: {done}")
    found = sorted(BOOTSTRAP_PYTHON_DIR.glob(f"cpython-{PYTHON}-*/bin/python3.12"))
    facts["python_install"]["found"] = [str(p) for p in found]
    facts["python_install"]["links"] = _links_left()
    if len(found) != 1:
        raise Failure(f"uv python install: интерпретатор не один: {found}")
    return str(found[0])


def _links_left() -> dict[str, Any]:
    """The `sudo` HOME and its `~/.local/bin` listing (as root): where
    `uv python install` puts its executable links, if anywhere."""
    home = _run(["sudo", "/usr/bin/printenv", "HOME"], timeout=60)
    path = f"{str(home['stdout_tail']).strip()}/.local/bin"
    listing = _run(["sudo", "/bin/ls", "-la", path], timeout=60)
    return {"sudo_home": home["stdout_tail"].strip(), "bin": listing}


def _at_least_312(version: str) -> bool:
    parts = version.split()
    return len(parts) == 3 and (int(parts[0]), int(parts[1])) >= (3, 12)


def prepare_uv(platform: plat.PlatformName, work: Path) -> dict[str, Any]:
    """README «Утверждённые CLI»: download, check, version, `sudo install`."""
    target, readme_sha = UV_TARGETS[platform]
    archive = f"uv-{target}.tar.gz"
    for name in (archive, f"{archive}.sha256"):
        _check(["/usr/bin/curl", "-fsSLo", name, f"{UV_RELEASE}/{name}"], work)
    published = (work / f"{archive}.sha256").read_text().split()[0]
    if published != readme_sha:
        raise Failure(f"uv: опубликовано {published}, в README {readme_sha}")
    checked = _check([*SHA_CHECK[platform], f"{archive}.sha256"], work)
    _check(["/usr/bin/tar", "-xzf", archive], work)
    binary = work / f"uv-{target}" / "uv"
    version = _check([str(binary), "--version"], work)
    if version.split()[1:2] != [UV_PINNED_VERSION]:
        raise Failure(f"uv --version {version!r}, нужен {UV_PINNED_VERSION}")
    dest = UV_DIR / "uv"
    _check(["sudo", "install", "-d", "-o", "root", "-m", "0755", str(UV_DIR)], work)
    _check(
        ["sudo", "install", "-o", "root", "-m", "0755", str(binary), str(dest)], work
    )
    st = os.lstat(dest)
    facts = {
        "archive": archive,
        "sha256": published,
        "check": checked.strip(),
        "version": version.strip(),
        "path": str(dest),
        "owner": st.st_uid,
        "mode": oct(st.st_mode & 0o7777),
        "problems": plat.trusted_path_problems(dest),
    }
    if st.st_uid != 0 or st.st_mode & 0o7777 != 0o755 or facts["problems"]:
        raise Failure(f"uv по пути {dest}: {facts}")
    return facts


def install_argv(python: str) -> list[str]:
    """The README first-install command, placeholders filled."""
    checkout = str(Path(__file__).resolve().parents[2])
    fill = {
        PYTHON_PLACEHOLDER: python,
        SHA_PLACEHOLDER: CORE_MANIFEST[1],
    }
    out = [
        fill.get(t, t).replace(CHECKOUT_PLACEHOLDER, checkout)
        for t in documented_command()
    ]
    if [t for t in out if "<" in t] or out[0] != "sudo":
        raise Failure(f"команда README не заполнена: {out}")
    return out


def command_argv(layout: Layout, argv: list[str]) -> list[str]:
    """README: `sudo darkfactory …` through the link, else the full path."""
    link = layout.local_bin / "darkfactory"
    command = "darkfactory" if os.path.lexists(link) else str(layout.launcher)
    return ["sudo", command, *argv]


def doctor(argv: list[str], raw: Path) -> dict[str, Any]:
    """`doctor --json`: exit 0, empty stderr, level 1, every check `ok`."""
    done = subprocess.run(
        argv, capture_output=True, text=True, timeout=600, check=False
    )
    raw.write_text(done.stdout)
    facts: dict[str, Any] = {
        "argv": argv,
        "code": done.returncode,
        "stderr": done.stderr[-TAIL:],
    }
    if done.returncode != 0 or done.stderr:
        raise Failure(f"doctor: {facts}")
    report = json.loads(done.stdout)
    facts["level1"] = report["level1"]
    facts["checks"] = {c["id"]: c["status"] for c in report["checks"]}
    bad = {k: v for k, v in facts["checks"].items() if v != "ok"}
    if report["level1"] != "reached" or bad or not facts["checks"]:
        raise Failure(f"doctor: level1 {report['level1']}, не ok {bad}")
    return facts


def core_pins() -> dict[str, str]:
    """Profile `core` of `CORE_MANIFEST` as df resolves it."""
    profile = parse_profile(fetch_manifest(*CORE_MANIFEST), CORE_PROFILE)
    return {p.name: p.sha for p in profile.repos}


def release_facts(release: Path, pins: Mapping[str, str]) -> dict[str, Any]:
    """`release.json` names the manifest, the profile and every pin."""
    record = json.loads((release / RELEASE_JSON).read_text())
    facts = {
        "id": record["id"],
        "dir": str(release),
        "manifest": [record["manifest_repo"], record["manifest_sha"]],
        "profile": record["profile"],
        "repos": {r["name"]: r["sha"] for r in record["repos"]},
    }
    if (
        tuple(facts["manifest"]) != CORE_MANIFEST
        or facts["profile"] != CORE_PROFILE
        or facts["repos"] != dict(pins)
    ):
        raise Failure(f"release.json не совпал с манифестом: {facts}")
    return facts


def smoke(release: Path, names: list[str], work: Path) -> dict[str, Any]:
    """`--help` of every declared entrypoint, as this user, cwd outside the
    builder tree; a member without console commands is said so."""
    cwd = work / "smoke"
    cwd.mkdir(exist_ok=True)
    env = {**USER_ENV, "HOME": os.environ.get("HOME", str(cwd))}
    out: dict[str, Any] = {}
    failed = []
    for name in names:
        declared = sorted(expected_entrypoints(release / "src" / name))
        if not declared:
            out[name] = "нет консольных команд — smoke не применим"
            continue
        out[name] = {}
        for ep in declared:
            argv = [str(release / "venv" / name / "bin" / ep), "--help"]
            done = subprocess.run(
                argv, capture_output=True, env=env, cwd=cwd, timeout=120, check=False
            )
            out[name][ep] = {
                "code": done.returncode,
                "stdout_head": done.stdout.decode("utf-8", "replace")[:400],
                "stderr_tail": done.stderr.decode("utf-8", "replace")[-400:],
            }
            if done.returncode != 0:
                failed.append(f"{name}/{ep}")
    if failed or not any(isinstance(v, dict) for v in out.values()):
        raise Failure(f"smoke: {failed or 'нет ни одной точки входа'}")
    return out


def _run(argv: list[str], *, timeout: int, cwd: Path | None = None) -> dict[str, Any]:
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )
    return {
        "argv": argv,
        "code": done.returncode,
        "stdout_tail": done.stdout[-TAIL:],
        "stderr_tail": done.stderr[-TAIL:],
    }


def _check(argv: list[str], cwd: Path) -> str:
    done = _run(argv, timeout=600, cwd=cwd)
    if done["code"] != 0:
        raise Failure(f"{' '.join(argv)}: {done}")
    return str(done["stdout_tail"])


def _out(argv: list[str]) -> str:
    resolved = shutil.which(argv[0]) or argv[0]
    done = subprocess.run(
        [resolved, *argv[1:]], capture_output=True, text=True, timeout=60, check=False
    )
    return done.stdout


if __name__ == "__main__":
    sys.exit(main())
