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
    lay = Layout(Path(os.path.realpath(tmp_path)) / "root")
    lay.verify_dir.mkdir(parents=True, mode=0o700)
    return lay


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
    dest.mkdir()
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


def test_reference_fetch_failure_removes_dest_when_work_removal_fails(
    tmp_path: Path, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paid review minor: a failed removal of the scratch `work` must not
    leave the partial `dest`; the unpack error is what propagates."""
    assert GIT is not None
    pin = make_repo(tmp_path / "a", {"f": "1"}, {"evil": "/etc/passwd"})
    dest = layout.verify_dir / "core"
    real = inputs._remove

    def failing(path: Path) -> None:
        if path.name.endswith(".work") and path.exists():
            raise OSError(5, "EIO")
        real(path)

    monkeypatch.setattr(inputs, "_remove", failing)
    with pytest.raises(TreeError):
        fetch_reference(pin, dest, git=Path(GIT), layout=layout)
    assert not dest.exists()


def test_remove_takes_fifo_as_one_name(tmp_path: Path) -> None:
    fifo = tmp_path / "f"
    os.mkfifo(fifo)
    inputs._remove(fifo)
    inputs._remove(tmp_path / "missing")
    assert not os.path.lexists(fifo)


def test_reference_fetch_dest_must_be_under_verify_dir(
    tmp_path: Path, layout: Layout
) -> None:
    pin = RepoPin("core", "x", "a" * 40, ())
    for dest in (tmp_path / "out", layout.verify_dir, layout.verify_dir / ".." / "x"):
        with pytest.raises(StepFailed):
            fetch_reference(pin, dest, git=Path("/usr/bin/git"), layout=layout)


def test_reference_fetch_missing_or_linked_verify_dir_refused(
    tmp_path: Path, layout: Layout
) -> None:
    pin = RepoPin("core", "x", "a" * 40, ())
    shutil.rmtree(layout.verify_dir)
    with pytest.raises(StepFailed, match="verify"):
        fetch_reference(
            pin, layout.verify_dir / "core", git=Path("/usr/bin/git"), layout=layout
        )
    assert not layout.verify_dir.exists()
    real = tmp_path / "elsewhere"
    real.mkdir()
    layout.verify_dir.symlink_to(real)
    with pytest.raises(StepFailed, match="verify"):
        fetch_reference(
            pin, layout.verify_dir / "core", git=Path("/usr/bin/git"), layout=layout
        )
    assert list(real.iterdir()) == []


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


def site_of(venv: Path) -> Path:
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return site


def test_lib64_symlink_does_not_duplicate(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    site = site_of(venv)
    make_dist(site, "reg", None)
    (site / "evil.pth").write_text("import os")
    (venv / "lib64").symlink_to("lib")
    assert [d.name for d in installed_distributions(venv)] == ["reg"]
    assert len(pth_problems(venv)) == 1


def test_other_symlink_in_walk_refused(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    other = tmp_path / "other" / "python3.12" / "site-packages"
    other.mkdir(parents=True)
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "python3.12").symlink_to(other.parent)
    with pytest.raises(TreeError):
        installed_distributions(venv)
    with pytest.raises(TreeError):
        pth_problems(venv)


def test_names_are_pep503_normalised(tmp_path: Path) -> None:
    site = site_of(tmp_path / "venv")
    make_dist(site, "PyYAML", None)
    make_dist(site, "zope.interface", None)
    got = installed_distributions(tmp_path / "venv")
    assert [d.name for d in got] == ["pyyaml", "zope-interface"]


def test_dist_info_symlink_refused(tmp_path: Path) -> None:
    site = site_of(tmp_path / "venv")
    make_dist(tmp_path / "x", "real", None)
    (site / "real-1.0.dist-info").symlink_to(tmp_path / "x" / "real-1.0.dist-info")
    with pytest.raises(TreeError):
        installed_distributions(tmp_path / "venv")


def test_metadata_symlink_fifo_oversize_missing_refused(tmp_path: Path) -> None:
    site = site_of(tmp_path / "venv")
    info = site / "a-1.0.dist-info"
    info.mkdir()
    venv = tmp_path / "venv"
    with pytest.raises(TreeError):  # missing
        installed_distributions(venv)
    target = tmp_path / "foreign"
    target.write_text("Name: a\nVersion: 1\n")
    (info / "METADATA").symlink_to(target)
    with pytest.raises(TreeError):
        installed_distributions(venv)
    (info / "METADATA").unlink()
    os.mkfifo(info / "METADATA")
    with pytest.raises(TreeError):  # must not hang
        installed_distributions(venv)
    (info / "METADATA").unlink()
    (info / "METADATA").write_text("Name: a\nVersion: 1\n" + "x" * inputs.METADATA_CAP)
    with pytest.raises(TreeError, match="больше"):
        installed_distributions(venv)


def test_direct_url_symlink_and_oversize_refused(tmp_path: Path) -> None:
    site = site_of(tmp_path / "venv")
    make_dist(site, "a", None)
    du = site / "a-1.0.dist-info" / "direct_url.json"
    du.symlink_to(tmp_path / "nowhere")
    with pytest.raises(TreeError):
        installed_distributions(tmp_path / "venv")
    du.unlink()
    du.write_text(" " * (inputs.DIRECT_URL_CAP + 1))
    with pytest.raises(TreeError, match="больше"):
        installed_distributions(tmp_path / "venv")


def test_pth_symlink_fifo_oversize_are_problems(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    site = site_of(venv)
    (site / "_virtualenv.pth").symlink_to(tmp_path / "x")
    os.mkfifo(site / "f.pth")
    (site / "big.pth").write_text("x" * (inputs.PTH_CAP + 1))
    assert len(pth_problems(venv)) == 3


def evil_site(venv: Path) -> None:
    site = venv / "evil" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "x.pth").write_text("import os")
    make_dist(site, "hidden", None)


@pytest.mark.parametrize(
    "name,target",
    [("lib64", "evil"), ("lib64", "."), ("lib64", ".."), ("lib32", "lib")],
)
def test_other_lib_links_refused(tmp_path: Path, name: str, target: str) -> None:
    venv = tmp_path / "venv"
    site_of(venv)
    evil_site(venv)
    (venv / name).symlink_to(target)
    with pytest.raises(TreeError, match=name):
        pth_problems(venv)
    with pytest.raises(TreeError, match=name):
        installed_distributions(venv)


def test_lib64_to_evil_with_real_content_refused(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    site_of(venv)
    evil = venv / "evil" / "python3.12" / "site-packages"
    evil.mkdir(parents=True)
    (evil / "x.pth").write_text("import os")
    (venv / "lib64").symlink_to("evil")
    with pytest.raises(TreeError):
        pth_problems(venv)
    with pytest.raises(TreeError):
        installed_distributions(venv)


def test_lib64_link_when_lib_is_a_link_refused(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    real = tmp_path / "real" / "python3.12" / "site-packages"
    real.mkdir(parents=True)
    venv.mkdir()
    (venv / "lib").symlink_to(tmp_path / "real")
    (venv / "lib64").symlink_to("lib")
    with pytest.raises(TreeError):
        pth_problems(venv)


def lower_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venv"
    make_dist(site_of(venv), "ok", None)
    return venv


def test_lowercase_venv_unchanged(tmp_path: Path) -> None:
    venv = lower_venv(tmp_path)
    assert [d.name for d in installed_distributions(venv)] == ["ok"]
    assert pth_problems(venv) == []


@pytest.mark.parametrize(
    "old,new",
    [
        ("lib", "LIB"),
        ("python3.12", "Python3.12"),
        ("site-packages", "Site-Packages"),
        ("ok-1.0.dist-info", "ok-1.0.DIST-INFO"),
    ],
)
def test_case_variants_in_walk_refused(tmp_path: Path, old: str, new: str) -> None:
    venv = lower_venv(tmp_path)
    target = next(venv.rglob(old))
    target.rename(target.with_name(new))
    with pytest.raises(TreeError, match=new):
        installed_distributions(venv)
    if old != "ok-1.0.dist-info":
        with pytest.raises(TreeError, match=new):
            pth_problems(venv)


def test_metadata_case_variant_refused(tmp_path: Path) -> None:
    venv = lower_venv(tmp_path)
    meta = next(venv.rglob("METADATA"))
    meta.rename(meta.with_name("Metadata"))
    with pytest.raises(TreeError, match="нет METADATA|Metadata"):
        installed_distributions(venv)


@pytest.mark.parametrize(
    "old,new",
    [
        ("site-packages", "\u017fite-packages"),  # long s: casefolds to "s"
        ("site-packages", "site-pac\u212aages"),  # Kelvin sign: casefolds to "k"
        ("ok-1.0.dist-info", "ok-1.0.di\u017ft-info"),
    ],
)
def test_names_matching_only_after_casefold_refused(
    tmp_path: Path, old: str, new: str
) -> None:
    venv = lower_venv(tmp_path)
    target = next(venv.rglob(old))
    target.rename(target.with_name(new))
    assert new in os.listdir(target.parent)
    with pytest.raises(TreeError, match="регистре") as err:
        installed_distributions(venv)
    assert new in str(err.value)


@pytest.mark.parametrize(
    "name,is_dir",
    [
        ("evil-1.0.egg-info", True),
        ("evil2-1.0.egg-info", False),
        ("evil3-1.0.EGG-INFO", True),
        ("evil4-1.0-py3.12.egg", True),
        ("evil5.egg-link", False),
    ],
)
def test_egg_metadata_refused(tmp_path: Path, name: str, is_dir: bool) -> None:
    venv = lower_venv(tmp_path)
    site = next(venv.rglob("site-packages"))
    meta = "Metadata-Version: 2.1\nName: evil\nVersion: 1.0\n"
    if is_dir:
        (site / name).mkdir()
        (site / name / "PKG-INFO").write_text(meta)
    else:
        (site / name).write_text(meta)
    for fn in (installed_distributions, pth_problems):
        with pytest.raises(TreeError, match="egg") as err:
            fn(venv)
        assert name in str(err.value)


def test_upper_pth_is_reported(tmp_path: Path) -> None:
    venv = lower_venv(tmp_path)
    site = next(venv.rglob("site-packages"))
    (site / "EVIL.PTH").write_text("import os")
    (site / "_VirtualEnv.pth").write_text("import _virtualenv")
    problems = pth_problems(venv)
    assert len(problems) == 2 and any("EVIL.PTH" in p for p in problems)


def test_upper_lib_with_lib64_link_refused(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "LIB" / "python3.12" / "site-packages").mkdir(parents=True)
    (venv / "LIB" / "python3.12" / "site-packages" / "e.pth").write_text("x")
    (venv / "lib64").symlink_to("lib")
    with pytest.raises(TreeError):
        pth_problems(venv)


def test_names_equal_after_casefold_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = lower_venv(tmp_path)
    site = next(venv.rglob("site-packages"))
    real = os.listdir

    def listing(path: object) -> list[str]:
        names = real(path)  # type: ignore[call-overload]
        return [*names, "a.pth", "A.pth"] if Path(str(path)) == site else names

    monkeypatch.setattr(inputs.os, "listdir", listing)
    with pytest.raises(TreeError, match="A.pth"):
        pth_problems(venv)


def test_missing_venv_or_site_packages_refused(tmp_path: Path) -> None:
    for fn in (installed_distributions, pth_problems):
        with pytest.raises(TreeError):
            fn(tmp_path / "absent")
    (tmp_path / "empty" / "lib").mkdir(parents=True)
    for fn in (installed_distributions, pth_problems):
        with pytest.raises(TreeError):
            fn(tmp_path / "empty")
