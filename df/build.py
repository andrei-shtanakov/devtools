"""Build a release as the unprivileged `df-build` user (spec 3.2).

Every command runs one by one through `df.platform.run_as`; root opens
nothing inside `build_home`. The build happens at the release's final path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from df.config import ConfigError, load_accounts
from df.journal import StepFailed
from df.manifest import Profile
from df.paths import Layout
from df.platform import (
    BUILD_USER,
    PlatformName,
    RunResult,
    detect_platform,
    run_as,
    verify_build_user,
    whoami_as,
)

#: Seconds allowed for one build command.
COMMAND_TIMEOUT = 1800
#: Bytes of stderr quoted in a failure.
STDERR_REPORT_BYTES = 4096
PYTHON_TRIPLES: Mapping[PlatformName, str] = {
    "macos-arm64": "macos-aarch64-none",
    "linux-x86_64": "linux-x86_64-gnu",
}
_RM = "/bin/rm"
_MKDIR = "/bin/mkdir"
_SYSTEM_PATH = ("/usr/bin", "/bin")

Runner = Callable[..., RunResult]
Step = tuple[str, list[str], dict[str, str]]


@dataclass(frozen=True)
class BuildPlan:
    """What to build: the release, its profile and the trusted executables."""

    release_id: str
    release_dir: Path
    profile: Profile
    uv: Path
    git: Path
    tar: Path


def build_env(plan: BuildPlan, layout: Layout) -> dict[str, str]:
    """The whole environment of a build command; nothing is inherited."""
    tools = (str(p.parent) for p in (plan.git, plan.tar, plan.uv))
    dirs = dict.fromkeys([*tools, *_SYSTEM_PATH])
    return {
        "HOME": str(layout.build_home),
        "PATH": ":".join(dirs),
        "LANG": "C.UTF-8",
        "UV_CACHE_DIR": str(layout.build_home / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(plan.release_dir / "python"),
        "UV_LINK_MODE": "copy",
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_PREFERENCE": "only-managed",
        "XDG_CONFIG_HOME": str(build_work_dir(layout, plan.release_id) / "config"),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def build_work_dir(layout: Layout, release_id: str) -> Path:
    """The build scratch tree `build_home/tmp/<release_id>`."""
    return layout.build_home / "tmp" / release_id


def build_src_dir(layout: Layout, release_id: str, repo: str) -> Path:
    """The build copy of `repo`: `build_work_dir/src/<repo>`.

    Task 7/10 lock resolution reads the build copy through this function.
    """
    return build_work_dir(layout, release_id) / "src" / repo


def python_key(python: str, platform: PlatformName) -> str:
    """The exact managed-Python key `cpython-<python>-<triple>`.

    Passed to `uv python install`, so uv cannot pick another build (such as
    an x86_64_v3 one) and `python_path` always matches the install.
    """
    return f"cpython-{python}-{PYTHON_TRIPLES[platform]}"


def python_path(plan: BuildPlan) -> Path:
    """The interpreter `uv python install` places under the release."""
    version = plan.profile.python
    short = ".".join(version.split(".")[:2])
    key = python_key(version, detect_platform())
    return plan.release_dir / "python" / key / "bin" / f"python{short}"


def build_commands(plan: BuildPlan, layout: Layout) -> list[tuple[str, list[str]]]:
    """`(label, argv)` in execution order.

    Deterministic, but reads the host platform (`python_path` calls
    `detect_platform()`). A plan whose paths do not match `layout` is refused.
    """
    return [(label, argv) for label, argv, _env in _steps(plan, layout)]


def run_build(plan: BuildPlan, layout: Layout, *, runner: Runner = run_as) -> str:
    """Build the release, one command at a time, as `df-build`.

    Precondition: `verify_build_user` is empty and `whoami_as` matches the
    accounts record, otherwise `StepFailed("build", ...)` before any command.
    Returns the declared uv version (stripped `uv --version` stdout). Effects:
    `run_as(df-build)+` (`releases/<id>`, `build_home/tmp`, `build_home/cache`).
    """
    _check_plan(plan, layout)
    _check_user(layout)
    env = build_env(plan, layout)
    declared = ""
    for label, argv, extra in _steps(plan, layout):
        result = runner(
            BUILD_USER,
            argv,
            env=env | extra,
            cwd=layout.build_home,
            network=True,
            timeout=COMMAND_TIMEOUT,
        )
        _check_result(label, result)
        if label.startswith("rev-parse:"):
            _check_head(label, plan, result)
        if label == "uv-version":
            declared = result.stdout.decode("utf-8", "replace").strip()
    return declared


def _check_plan(plan: BuildPlan, layout: Layout) -> None:
    """The release id is valid and `release_dir` is its registered path."""
    try:
        expected = layout.release_dir(plan.release_id)
    except ConfigError as exc:
        raise StepFailed("build", str(exc)) from exc
    if plan.release_dir != expected:
        raise StepFailed(
            "build",
            f"release_dir {plan.release_dir}, для {plan.release_id} "
            f"ожидается {expected}",
        )


def _steps(plan: BuildPlan, layout: Layout) -> Iterator[Step]:
    _check_plan(plan, layout)
    work = build_work_dir(layout, plan.release_id)
    repos = plan.profile.repos
    git, tar, uv = str(plan.git), str(plan.tar), str(plan.uv)
    cache = str(layout.build_home / "cache")
    yield "clean", [_RM, "-rf", "--", str(work), cache], {}
    targets = [
        str(work / "git"),
        str(work / "config"),
        *(str(build_src_dir(layout, plan.release_id, r.name)) for r in repos),
        *(str(plan.release_dir / "src" / r.name) for r in repos),
    ]
    yield "mkdir", [_MKDIR, "-p", *targets], {}
    for r in repos:
        repo = str(work / "git" / r.name)
        archive = str(work / f"{r.name}.tar")
        yield f"init:{r.name}", [git, "init", "-q", repo], {}
        fetch = [git, "-C", repo, "fetch", "-q", "--depth=1", r.url, r.sha]
        yield f"fetch:{r.name}", fetch, {}
        rev = [git, "-C", repo, "rev-parse", "FETCH_HEAD"]
        yield f"rev-parse:{r.name}", rev, {}
        pack = [git, "-C", repo, "archive", "-o", archive, r.sha]
        yield f"archive:{r.name}", pack, {}
        final = str(plan.release_dir / "src" / r.name)
        yield f"extract:{r.name}", [tar, "-xf", archive, "-C", final], {}
        build = str(build_src_dir(layout, plan.release_id, r.name))
        yield f"extract-build:{r.name}", [tar, "-xf", archive, "-C", build], {}
    key = python_key(plan.profile.python, detect_platform())
    yield "python", [uv, "python", "install", "--no-bin", key], {}
    interpreter = str(python_path(plan))
    for r in repos:
        venv = {"UV_PROJECT_ENVIRONMENT": str(plan.release_dir / "venv" / r.name)}
        groups = [a for g in r.groups for a in ("--group", g)]
        sync = [uv, "sync", "--locked", "--no-editable", "--no-default-groups"]
        sync += [
            *groups,
            "--project",
            str(build_src_dir(layout, plan.release_id, r.name)),
        ]
        yield f"sync:{r.name}", [*sync, "--python", interpreter], venv
    yield "uv-version", [uv, "--version"], {}


def _check_user(layout: Layout) -> None:
    problems = verify_build_user(layout)
    if problems:
        raise StepFailed("build", "; ".join(problems))
    account = load_accounts(layout.accounts)[BUILD_USER]
    actual = whoami_as(BUILD_USER)
    if actual != (account.uid, account.gid):
        raise StepFailed(
            "build",
            f"{BUILD_USER} стартует как uid/gid {actual}, "
            f"в accounts.toml {(account.uid, account.gid)}",
        )


def _check_result(label: str, result: RunResult) -> None:
    if result.launched and not (
        result.timed_out or result.stdout_truncated or result.returncode != 0
    ):
        return
    if not result.launched:
        state = "не стартовал"
    elif result.timed_out:
        state = f"срок {COMMAND_TIMEOUT} с истёк"
    elif result.stdout_truncated:
        state = "stdout переполнен"
    else:
        state = f"код {result.returncode}"
    tail = result.stderr[-STDERR_REPORT_BYTES:].decode("utf-8", "replace").strip()
    raise StepFailed(label, f"{state}: {tail}" if tail else state)


def _check_head(label: str, plan: BuildPlan, result: RunResult) -> None:
    name = label.removeprefix("rev-parse:")
    want = next(r.sha for r in plan.profile.repos if r.name == name)
    have = result.stdout.decode("utf-8", "replace").strip()
    if have != want:
        raise StepFailed(label, f"FETCH_HEAD {have!r}, в профиле {want!r}")
