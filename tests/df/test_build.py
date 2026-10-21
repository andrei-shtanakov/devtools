"""Unit tests of df.build (stub runner; no root, no network)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from df import build
from df.build import BuildPlan, build_commands, build_env, python_path, run_build
from df.config import Account, dump_accounts
from df.journal import StepFailed
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.platform import BUILD_USER, RunResult

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
        "3.12.7",
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
    py = rel / "python" / "cpython-3.12.7-linux-x86_64-gnu" / "bin" / "python3.12"
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
        ("clean", ["/bin/rm", "-rf", "--", str(work)]),
        (
            "mkdir",
            [
                "/bin/mkdir", "-p", f"{work}/git",
                f"{work}/src/devtools", f"{work}/src/steward",
                f"{rel}/src/devtools", f"{rel}/src/steward",
            ],
        ),
        *repo_steps("devtools", "https://x/devtools.git", SHA_A),
        *repo_steps("steward", "https://x/steward.git", SHA_B),
        ("python", [uv, "python", "install", "--no-bin", "3.12.7"]),
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
        base / "cpython-3.12.7-linux-x86_64-gnu/bin/python3.12"
    )
    monkeypatch.setattr(build, "detect_platform", lambda: "macos-arm64")
    assert python_path(plan) == (
        base / "cpython-3.12.7-macos-aarch64-none/bin/python3.12"
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
    assert env["PATH"] == "/opt/tools:/usr/bin:/bin"
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
