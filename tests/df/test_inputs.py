"""Unit tests of df.inputs (real git on local repos; no network, no root)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from df import inputs
from df.build import BuildPlan, build_env
from df.digest import TreeError
from df.inputs import (
    fetch_reference,
    installed_distributions,
    pth_problems,
    verify_sources,
)
from df.journal import StepFailed
from df.manifest import Profile, RepoPin
from df.paths import Layout
from df.platform import RunResult
from df.sources import SourceId

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git not installed")


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(Path(os.path.realpath(tmp_path)) / "root")


def git(repo: Path, *args: str) -> str:
    assert GIT is not None
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(repo.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    cmd = [GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def make_repo(root: Path, files: dict[str, str], links: dict[str, str]) -> RepoPin:
    repo = root / "origin"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for name, text in files.items():
        (repo / name).write_text(text)
    for name, target in links.items():
        os.symlink(target, repo / name)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    sha = git(repo, "rev-parse", "HEAD")
    return RepoPin("core", str(repo), sha, ())


def test_reference_fetch_unpacks_pinned_commit(tmp_path: Path, layout: Layout) -> None:
    pin = make_repo(tmp_path, {"uv.lock": "x\n", "a.txt": "a"}, {"ok": "a.txt"})
    dest = layout.verify_dir / "core"
    dest.mkdir(parents=True)
    (dest / "stale").write_text("old")
    assert GIT is not None
    got = fetch_reference(pin, dest, git=Path(GIT), layout=layout)
    assert got == dest
    assert sorted(p.name for p in dest.iterdir()) == ["a.txt", "ok", "uv.lock"]
    assert [p.name for p in layout.verify_dir.iterdir()] == ["core"]


def test_reference_fetch_checks_head_and_uses_data_filter(
    tmp_path: Path, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert GIT is not None
    pin = make_repo(tmp_path / "a", {"f": "1"}, {"evil": "/etc/passwd"})
    dest = layout.verify_dir / "core"
    with pytest.raises(TreeError):  # filter="data": absolute link target
        fetch_reference(pin, dest, git=Path(GIT), layout=layout)
    assert not dest.exists()

    clean = make_repo(tmp_path / "b", {"f": "1"}, {})
    real = inputs.run_command

    def lying(argv: list[str], **kw: object) -> RunResult:
        result = real(argv, **kw)  # type: ignore[arg-type]
        if "rev-parse" in argv:
            return replace(result, stdout=b"0" * 40 + b"\n")
        return result

    monkeypatch.setattr(inputs, "run_command", lying)
    with pytest.raises(StepFailed, match="rev-parse:core"):
        fetch_reference(clean, dest, git=Path(GIT), layout=layout)
    assert not dest.exists()


def test_reference_fetch_dest_must_be_under_verify_dir(
    tmp_path: Path, layout: Layout
) -> None:
    pin = RepoPin("core", "x", "a" * 40, ())
    for dest in (tmp_path / "out", layout.verify_dir, layout.verify_dir / ".." / "x"):
        with pytest.raises(StepFailed):
            fetch_reference(pin, dest, git=Path("/usr/bin/git"), layout=layout)


def test_reference_fetch_env_isolated_like_build(
    tmp_path: Path, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert GIT is not None
    pin = make_repo(tmp_path, {"f": "1"}, {})
    seen: list[tuple[list[str], dict[str, str]]] = []
    real = inputs.run_command

    def spy(argv: list[str], **kw: object) -> RunResult:
        seen.append((list(argv), dict(kw["env"])))  # type: ignore[call-overload]
        return real(argv, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(inputs, "run_command", spy)
    fetch_reference(pin, layout.verify_dir / "core", git=Path(GIT), layout=layout)
    plan = BuildPlan(
        "id",
        layout.release_dir("id"),
        Profile("p", "3.12.1", ()),
        Path(GIT),
        Path("/usr/bin/tar"),
        Path("/usr/bin/uv"),
    )
    ref_env = build_env(plan, layout)
    assert len(seen) == 4
    assert "--template=" in seen[0][0]
    for _argv, env in seen:
        assert set(env) == {
            "PATH", "LANG", "HOME", "XDG_CONFIG_HOME", "GIT_ATTR_NOSYSTEM",
            "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT",
        }  # fmt: skip
        git_keys = {k for k in env if k.startswith("GIT_")}
        assert {k: env[k] for k in git_keys} == {
            k: v for k, v in ref_env.items() if k.startswith("GIT_")
        }
        assert env["HOME"] != env["XDG_CONFIG_HOME"]


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_sources_mismatch_names_repo(tmp_path: Path) -> None:
    profile = Profile(
        "p",
        "3.12.1",
        (RepoPin("core", "u", "a" * 40, ()), RepoPin("x", "u", "b" * 40, ())),
    )
    release = tmp_path / "rel"
    refs = {}
    for name in ("core", "x"):
        files = {"uv.lock": f"lock-{name}", "release.json": "{}"}
        write_tree(release / "src" / name, files)
        refs[name] = write_tree(tmp_path / "ref" / name, files)
    got = verify_sources(release, profile, refs)
    assert set(got) == {"core", "x"} and got["core"] != got["x"]
    assert got["core"].startswith("sha256:")
    # a top-level release.json counts (nothing is excluded)
    (release / "src" / "x" / "release.json").write_text('{"a": 1}')
    with pytest.raises(StepFailed, match="x:") as err:
        verify_sources(release, profile, refs)
    assert err.value.step == "verify-sources"
    with pytest.raises(StepFailed, match="core"):
        verify_sources(release, profile, {"x": refs["x"]})


def make_dist(site: Path, name: str, direct_url: object | None) -> None:
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
    )
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))


def test_installed_distributions_read_metadata_and_direct_url(tmp_path: Path) -> None:
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    make_dist(site, "reg", None)
    make_dist(site, "loc", {"url": "file:///a/b", "dir_info": {}})
    got = installed_distributions(tmp_path / "venv")
    assert [(d.name, d.version) for d in got] == [("loc", "1.0"), ("reg", "1.0")]
    assert got[0].source == SourceId("directory", path="/a/b", editable=False)
    assert got[1].source == SourceId("registry")


def test_installed_distribution_without_name_refused(tmp_path: Path) -> None:
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    (site / "x-1.dist-info").mkdir(parents=True)
    (site / "x-1.dist-info" / "METADATA").write_text("Version: 1\n")
    with pytest.raises(TreeError):
        installed_distributions(tmp_path / "venv")


def test_foreign_pth_refused_virtualenv_pth_allowed(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "_virtualenv.pth").write_text("import _virtualenv")
    assert pth_problems(venv) == []
    (site / "evil.pth").write_text("import os")
    (site / "_virtualenv.pth").write_text("import _virtualenv; import os")
    problems = pth_problems(venv)
    assert len(problems) == 2
    assert any("evil.pth" in p for p in problems)
    assert any("_virtualenv.pth" in p for p in problems)
