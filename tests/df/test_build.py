"""Unit tests of df.build (stub runner; no root). Only the `network` test
installs the pinned Pythons (df-stage1 runs it)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from df import build
from df.boundary import _NativeCheck
from df.build import (
    TCLTK_REMOVED,
    TCLTK_REMOVED_3_12_13,
    BuildPlan,
    build_commands,
    build_env,
    build_src_dir,
    build_work_dir,
    python_key,
    python_path,
    run_build,
    tcltk_strip_argv,
)
from df.config import Account, dump_accounts
from df.journal import StepFailed
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.platform import BUILD_USER, PlatformName, RunResult, detect_platform
from tests.df.test_admin import pinned_uv

#: Owner decision 25.10, measured 25.10 (uv 0.11.23, cpython-3.12.13): the
#: same components on both platforms; only the native file names differ.
TCLTK_SHARED = (
    "bin/idle3",
    "bin/idle3.12",
    "lib/itcl4.3.5",
    "lib/tcl9",
    "lib/tcl9.0",
    "lib/thread3.0.4",
    "lib/tk9.0",
    "lib/python3.12/idlelib",
    "lib/python3.12/tkinter",
    "lib/python3.12/turtle.py",
    "lib/python3.12/turtledemo",
)
TCLTK_LINUX = (
    *TCLTK_SHARED,
    "lib/libtcl9.0.so",
    "lib/libtcl9tk9.0.so",
    "lib/python3.12/lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so",
)
TCLTK_MACOS = (
    *TCLTK_SHARED,
    "lib/libtcl9.0.dylib",
    "lib/libtcl9tk9.0.dylib",
    "lib/python3.12/lib-dynload/_tkinter.cpython-312-darwin.so",
)
SHA_A = "a" * 40
SHA_B = "b" * 40
UID = 401


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    lay = Layout(Path(os.path.realpath(tmp_path)) / "root")
    lay.install_dir.mkdir(parents=True)
    account = Account(BUILD_USER, UID, UID, "ready", "2026-10-16T00:00:00+00:00")
    lay.accounts.write_text(dump_accounts({BUILD_USER: account}))
    return lay


@pytest.fixture
def plan(layout: Layout) -> BuildPlan:
    profile = Profile(
        "core",
        "3.12.13",
        (
            RepoPin("devtools", "https://x/devtools.git", SHA_A, ("governance",)),
            RepoPin("steward", "https://x/steward.git", SHA_B, ()),
        ),
    )
    return BuildPlan(
        "rel1",
        layout.releases / "rel1",
        profile,
        Path("/opt/tools/uv"),
        Path("/usr/bin/git"),
        Path("/usr/bin/tar"),
    )


@pytest.fixture(autouse=True)
def trusted_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "verify_build_user", lambda _layout: [])
    monkeypatch.setattr(build, "whoami_as", lambda _user: (UID, UID))
    monkeypatch.setattr(build, "detect_platform", lambda: "linux-x86_64")


class Stub:
    """Records every call; `reply(label)` overrides the default outcome."""

    def __init__(self, plan: BuildPlan, replies: dict[str, RunResult]) -> None:
        self.plan = plan
        self.replies = replies
        self.calls: list[dict[str, Any]] = []
        self.labels_by_argv: dict[tuple[str, ...], str] = {}

    def __call__(self, user: str, argv: Sequence[str], **kw: Any) -> RunResult:
        self.calls.append({"user": user, "argv": list(argv), **kw})
        label = self.label_of(list(argv))
        if label in self.replies:
            return self.replies[label]
        if label.startswith("rev-parse:"):
            name = label.removeprefix("rev-parse:")
            sha = next(r.sha for r in self.plan.profile.repos if r.name == name)
            return RunResult(0, f"{sha}\n".encode(), b"", launched=True)
        if label == "uv-version":
            return RunResult(0, b"uv 0.9.1\n", b"", launched=True)
        return RunResult(0, b"", b"", launched=True)

    def label_of(self, argv: list[str]) -> str:
        return self.labels_by_argv[tuple(argv)]


def make_stub(
    plan: BuildPlan, layout: Layout, replies: dict[str, RunResult] | None = None
) -> Stub:
    stub = Stub(plan, replies or {})
    stub.labels_by_argv = {
        tuple(argv): label for label, argv in build_commands(plan, layout)
    }
    return stub


def test_commands_exact_order_and_flags(plan: BuildPlan, layout: Layout) -> None:
    work = layout.build_home / "tmp" / "rel1"
    rel = plan.release_dir
    key = rel / "python" / "cpython-3.12.13-linux-x86_64-gnu"
    py = key / "bin" / "python3.12"
    git, tar, uv = "/usr/bin/git", "/usr/bin/tar", "/opt/tools/uv"

    def repo_steps(name: str, url: str, sha: str) -> list[tuple[str, list[str]]]:
        g = f"{work}/git/{name}"
        return [
            (f"init:{name}", [git, "init", "-q", g]),
            (f"fetch:{name}", [git, "-C", g, "fetch", "-q", "--depth=1", url, sha]),
            (f"rev-parse:{name}", [git, "-C", g, "rev-parse", "FETCH_HEAD"]),
            (
                f"archive:{name}",
                [git, "-C", g, "archive", "-o", f"{work}/{name}.tar", sha],
            ),
            (
                f"extract:{name}",
                [tar, "-xf", f"{work}/{name}.tar", "-C", f"{rel}/src/{name}"],
            ),
            (
                f"extract-build:{name}",
                [tar, "-xf", f"{work}/{name}.tar", "-C", f"{work}/src/{name}"],
            ),
        ]

    def sync(name: str, groups: list[str]) -> tuple[str, list[str]]:
        argv = [uv, "sync", "--locked", "--no-editable", "--no-default-groups"]
        for g in groups:
            argv += ["--group", g]
        return f"sync:{name}", [
            *argv,
            "--project",
            f"{work}/src/{name}",
            "--python",
            str(py),
        ]

    expected = [
        (
            "clean",
            ["/bin/rm", "-rf", "--", str(work), str(layout.build_home / "cache")],
        ),
        (
            "mkdir",
            [
                "/bin/mkdir", "-p", f"{work}/git", f"{work}/config",
                f"{work}/src/devtools", f"{work}/src/steward",
                f"{rel}/src/devtools", f"{rel}/src/steward",
            ],
        ),
        *repo_steps("devtools", "https://x/devtools.git", SHA_A),
        *repo_steps("steward", "https://x/steward.git", SHA_B),
        (
            "python",
            [uv, "python", "install", "--no-bin", "cpython-3.12.13-linux-x86_64-gnu"],
        ),
        ("strip-tcltk", ["/bin/rm", "-r", "--", *(f"{key}/{p}" for p in TCLTK_LINUX)]),
        sync("devtools", ["governance"]),
        sync("steward", []),
        ("uv-version", [uv, "--version"]),
    ]  # fmt: skip
    assert build_commands(plan, layout) == expected


def test_sync_has_locked_no_editable_explicit_groups(
    plan: BuildPlan, layout: Layout
) -> None:
    cmds = dict(build_commands(plan, layout))
    sync = cmds["sync:devtools"]
    assert {"--locked", "--no-editable", "--no-default-groups"} <= set(sync)
    assert sync[sync.index("--group") + 1] == "governance"
    assert "--group" not in cmds["sync:steward"]


def test_python_path_per_platform(
    plan: BuildPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = plan.release_dir / "python"
    assert python_path(plan) == (
        base / "cpython-3.12.13-linux-x86_64-gnu/bin/python3.12"
    )
    monkeypatch.setattr(build, "detect_platform", lambda: "macos-arm64")
    assert python_path(plan) == (
        base / "cpython-3.12.13-macos-aarch64-none/bin/python3.12"
    )


def test_all_commands_run_as_build_user_with_network(
    plan: BuildPlan, layout: Layout
) -> None:
    stub = make_stub(plan, layout)
    declared = run_build(plan, layout, runner=stub)
    assert declared == "uv 0.9.1"
    assert [c["argv"] for c in stub.calls] == [
        a for _l, a in build_commands(plan, layout)
    ]
    for call in stub.calls:
        assert call["user"] == BUILD_USER
        assert call["cwd"] == layout.build_home
        assert call["network"] is True
        assert call["timeout"] == 1800


def test_project_environment_only_on_sync(plan: BuildPlan, layout: Layout) -> None:
    stub = make_stub(plan, layout)
    run_build(plan, layout, runner=stub)
    for call in stub.calls:
        label = stub.label_of(call["argv"])
        key = "UV_PROJECT_ENVIRONMENT"
        if label.startswith("sync:"):
            name = label.removeprefix("sync:")
            assert call["env"][key] == str(plan.release_dir / "venv" / name)
        else:
            assert key not in call["env"]


def test_build_env_has_no_inherited_vars(
    plan: BuildPlan, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret")
    env = build_env(plan, layout)
    assert "GH_TOKEN" not in env
    assert env["HOME"] == str(layout.build_home)
    assert env["PATH"] == "/usr/bin:/opt/tools:/bin"
    assert env["XDG_CONFIG_HOME"] == str(layout.build_home / "tmp/rel1/config")
    assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert env["UV_CACHE_DIR"] == str(layout.build_home / "cache")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(plan.release_dir / "python")
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["UV_NO_CONFIG"] == "1"


def test_fetch_head_mismatch_fails(plan: BuildPlan, layout: Layout) -> None:
    other = RunResult(0, ("c" * 40 + "\n").encode(), b"", launched=True)
    stub = make_stub(plan, layout, {"rev-parse:devtools": other})
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "rev-parse:devtools"
    assert "c" * 40 in err.value.detail and SHA_A in err.value.detail
    assert not any("archive" in str(c["argv"]) for c in stub.calls)


def test_stale_lock_failure_names_repo(plan: BuildPlan, layout: Layout) -> None:
    bad = RunResult(2, b"", b"x" * 5000 + b"lockfile needs update", launched=True)
    stub = make_stub(plan, layout, {"sync:steward": bad})
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "sync:steward"
    assert "lockfile needs update" in err.value.detail
    assert "код 2" in err.value.detail
    assert len(err.value.detail) < 4096 + 100
    assert stub.label_of(stub.calls[-1]["argv"]) == "sync:steward"


@pytest.mark.parametrize(
    ("result", "text"),
    [
        (RunResult(-9, b"", b"", launched=True, timed_out=True), "срок"),
        (RunResult(0, b"", b"", launched=True, stdout_truncated=True), "stdout"),
        (RunResult(127, b"", b"sudo: boom", launched=False), "не стартовал"),
    ],
)
def test_abnormal_outcomes_stop_the_build(
    plan: BuildPlan, layout: Layout, result: RunResult, text: str
) -> None:
    stub = make_stub(plan, layout, {"mkdir": result})
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "mkdir" and text in err.value.detail
    assert len(stub.calls) == 2


def test_build_refused_before_any_command_if_user_unverified(
    plan: BuildPlan, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "verify_build_user", lambda _l: ["a", "b"])
    stub = make_stub(plan, layout)
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "build" and "a; b" in err.value.detail
    assert stub.calls == []


def test_build_refused_if_identity_differs(
    plan: BuildPlan, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "whoami_as", lambda _u: (UID + 1, UID))
    stub = make_stub(plan, layout)
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "build"
    assert stub.calls == []


def test_release_dir_mismatch_refused_before_any_command(
    plan: BuildPlan, layout: Layout
) -> None:
    bad = replace(plan, release_dir=layout.releases / "other")
    stub = make_stub(plan, layout)
    with pytest.raises(StepFailed) as err:
        run_build(bad, layout, runner=stub)
    assert err.value.step == "build"
    assert str(bad.release_dir) in err.value.detail
    assert str(plan.release_dir) in err.value.detail
    assert stub.calls == []
    with pytest.raises(StepFailed):
        build_commands(bad, layout)


def test_invalid_release_id_refused_before_any_command(
    plan: BuildPlan, layout: Layout
) -> None:
    bad = replace(plan, release_id="..")
    stub = make_stub(plan, layout)
    with pytest.raises(StepFailed) as err:
        run_build(bad, layout, runner=stub)
    assert err.value.step == "build"
    assert stub.calls == []
    with pytest.raises(StepFailed):
        build_commands(bad, layout)


@pytest.mark.parametrize("name", ["macos-arm64", "linux-x86_64"])
def test_python_install_argv_matches_python_path(
    plan: BuildPlan, layout: Layout, monkeypatch: pytest.MonkeyPatch, name: PlatformName
) -> None:
    monkeypatch.setattr(build, "detect_platform", lambda: name)
    install = dict(build_commands(plan, layout))
    key = install["python"][-1]
    assert key == python_key("3.12.13", name)
    assert python_path(plan).parent.parent.name == key


def test_every_call_gets_exactly_build_env(plan: BuildPlan, layout: Layout) -> None:
    stub = make_stub(plan, layout)
    run_build(plan, layout, runner=stub)
    base = build_env(plan, layout)
    for call in stub.calls:
        label = stub.label_of(call["argv"])
        extra = {}
        if label.startswith("sync:"):
            venv = plan.release_dir / "venv" / label.removeprefix("sync:")
            extra = {"UV_PROJECT_ENVIRONMENT": str(venv)}
        assert call["env"] == base | extra


def test_work_and_src_dir_helpers(plan: BuildPlan, layout: Layout) -> None:
    work = build_work_dir(layout, "rel1")
    assert work == layout.build_home / "tmp" / "rel1"
    assert build_src_dir(layout, "rel1", "steward") == work / "src" / "steward"


# ---------- Tcl/Tk removal (owner decision 25.10, spec 3.4) ----------


def test_tcltk_list_is_the_measured_one() -> None:
    """The constant is exactly the measured list, per platform and in order;
    only 3.12.13 is measured."""
    assert TCLTK_REMOVED == {"3.12.13": TCLTK_REMOVED_3_12_13}
    assert TCLTK_REMOVED_3_12_13 == {
        "linux-x86_64": TCLTK_LINUX,
        "macos-arm64": TCLTK_MACOS,
    }


def test_tcltk_list_names_the_same_components_on_both_platforms() -> None:
    """No blanket pattern: every entry is a literal path; the platforms differ
    only by the native suffix (`.so`/`.dylib`, the `_tkinter` ABI tag)."""

    def component(rel: str) -> str:
        for suffix in (".cpython-312-x86_64-linux-gnu.so", ".cpython-312-darwin.so"):
            rel = rel.removesuffix(suffix)
        return rel.removesuffix(".so").removesuffix(".dylib")

    lists = TCLTK_REMOVED_3_12_13.values()
    assert len({frozenset(map(component, entries)) for entries in lists}) == 1
    for entries in lists:
        assert len(set(entries)) == len(entries)
        assert not any(c in rel for rel in entries for c in "*?[]")


def test_strip_runs_as_build_user_after_python_before_any_sync(
    plan: BuildPlan, layout: Layout
) -> None:
    """Removal is the build's own step: `df-build`, after the Python install,
    before every `uv sync` (so before `seal`, which follows `run_build`)."""
    stub = make_stub(plan, layout)
    run_build(plan, layout, runner=stub)
    labels = [stub.label_of(c["argv"]) for c in stub.calls]
    at = labels.index("strip-tcltk")
    assert labels[at - 1] == "python"
    assert all(not label.startswith("sync:") for label in labels[:at])
    assert stub.calls[at]["user"] == BUILD_USER


@pytest.mark.parametrize("name", ["macos-arm64", "linux-x86_64"])
def test_strip_argv_is_rm_without_force_of_the_listed_paths(
    plan: BuildPlan, name: PlatformName
) -> None:
    key = plan.release_dir / "python" / python_key("3.12.13", name)
    argv = tcltk_strip_argv(plan, name)
    assert argv[:3] == ["/bin/rm", "-r", "--"]
    assert argv[3:] == [str(key / rel) for rel in TCLTK_REMOVED_3_12_13[name]]


def test_strip_failure_stops_the_build_before_sync(
    plan: BuildPlan, layout: Layout
) -> None:
    gone = b"rm: /x/lib/tk9.0: No such file or directory"
    stub = make_stub(
        plan, layout, {"strip-tcltk": RunResult(1, b"", gone, launched=True)}
    )
    with pytest.raises(StepFailed) as err:
        run_build(plan, layout, runner=stub)
    assert err.value.step == "strip-tcltk" and "tk9.0" in err.value.detail
    assert err.value.detail.endswith(build.STRIP_DRIFT_HINT)
    assert "перемерить перечень" in build.STRIP_DRIFT_HINT
    assert stub.label_of(stub.calls[-1]["argv"]) == "strip-tcltk"


def test_unmeasured_python_refused_before_any_command(
    plan: BuildPlan, layout: Layout
) -> None:
    bad = replace(plan, profile=replace(plan.profile, python="3.12.12"))
    stub = make_stub(plan, layout)
    with pytest.raises(StepFailed) as err:
        run_build(bad, layout, runner=stub)
    assert err.value.step == "build"
    assert "3.12.12" in err.value.detail and "Tcl/Tk" in err.value.detail
    assert "укажите в профиле" in err.value.detail and "3.12.13" in err.value.detail
    assert stub.calls == []
    with pytest.raises(StepFailed):
        build_commands(bad, layout)


def _stand_in_tree(base: Path, entries: Sequence[str]) -> None:
    for rel in (*entries, "lib/libpython3.12.so", "share/terminfo/t/tkterm"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("x")


@pytest.mark.parametrize("name", ["macos-arm64", "linux-x86_64"])
def test_strip_argv_removes_exactly_the_list(
    plan: BuildPlan, name: PlatformName
) -> None:
    """The real `rm` removes every listed entry and nothing else."""
    argv = tcltk_strip_argv(plan, name)
    base = plan.release_dir / "python" / python_key("3.12.13", name)
    _stand_in_tree(base, TCLTK_REMOVED_3_12_13[name])
    done = subprocess.run(argv, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr
    left = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
    assert left == ["lib/libpython3.12.so", "share/terminfo/t/tkterm"]


def test_strip_argv_refuses_list_drift(plan: BuildPlan) -> None:
    """A listed entry missing (list drift) fails the real `rm`, by name."""
    argv = tcltk_strip_argv(plan, "linux-x86_64")
    base = plan.release_dir / "python" / python_key("3.12.13", "linux-x86_64")
    _stand_in_tree(base, [e for e in TCLTK_LINUX if e != "lib/tk9.0"])
    done = subprocess.run(argv, capture_output=True, check=False)
    assert done.returncode != 0
    assert b"tk9.0" in done.stderr


@pytest.mark.network
@pytest.mark.parametrize("name", ["macos-arm64", "linux-x86_64"])
def test_tcltk_list_matches_the_pinned_python(
    tmp_path: Path, name: PlatformName
) -> None:
    """Re-measure (uv installs either key on any host): every listed entry
    exists, and no other Tcl/Tk-named entry is anywhere in the tree (whole
    walk). On the host platform the stripped tree passes rule (в)
    unchanged."""
    uv = pinned_uv()
    key = python_key("3.12.13", name)
    release = tmp_path / "rel"
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_INSTALL_DIR": str(release / "python"),
    }
    argv = [str(uv), "python", "install", "--no-bin", key]
    subprocess.run(argv, env=env, capture_output=True, check=True, timeout=600)
    base = release / "python" / key
    listed = TCLTK_REMOVED_3_12_13[name]
    assert [rel for rel in listed if not os.path.lexists(base / rel)] == []
    assert _tcltk_named(base) == set(listed)
    if name != detect_platform():
        return
    check = _NativeCheck(release, name, PurePosixPath(f"python/{key}/bin"))
    before = check.problems()
    prefixes = tuple(f"python/{key}/{rel}" for rel in listed)
    assert all(line.startswith(prefixes) for line in before), before
    if name == "linux-x86_64":
        assert before, "rule (в) must see the PBS Tcl/Tk rpath (13-B finding)"
    strip = tcltk_strip_argv(_plan_at(release), name)
    subprocess.run(strip, capture_output=True, check=True)
    assert check.problems() == []


#: Name fragments of the measurement (test only; df removes a literal list).
TCLTK_WORDS = ("tcl", "tk", "thread3", "idle", "turtle")
#: Tcl/Tk-named, but not Tcl/Tk: a terminal type of ncurses' terminfo.
TCLTK_NOT = frozenset({"share/terminfo/t/tkterm"})


def _tcltk_named(base: Path) -> set[str]:
    """Every Tcl/Tk-named entry of the whole tree, not descending into one
    (its contents go with it), minus `TCLTK_NOT`."""
    found: set[str] = set()
    for dirpath, dirs, files in os.walk(base):
        top = Path(dirpath).relative_to(base)
        for name in (*dirs, *files):
            if any(w in name.lower() for w in TCLTK_WORDS):
                found.add(str(top / name))
        dirs[:] = [d for d in dirs if str(top / d) not in found]
    return found - TCLTK_NOT


def test_tcltk_named_walks_the_whole_tree(tmp_path: Path) -> None:
    """The measurement sees a Tcl/Tk name at any depth, once per subtree."""
    for rel in ("include/tcl.h", "lib/tk9.0/x.tcl", "share/terminfo/t/tkterm", "a/b"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    assert _tcltk_named(tmp_path) == {"include/tcl.h", "lib/tk9.0"}


def _plan_at(release_dir: Path) -> BuildPlan:
    profile = Profile("core", "3.12.13", ())
    return BuildPlan("rel1", release_dir, profile, Path("/"), Path("/"), Path("/"))
