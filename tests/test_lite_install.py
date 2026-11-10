"""Комплектация и install.sh лёгкой установки DarkFactory (lite/install.py)."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from governance import df_config
from lite import install
from tests.df_config_fixture import use_test_real_home

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args: str, cwd: Path | None = None) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


class Recorder:
    """Runner: `uv` записывает, остальное (git) исполняет по-настоящему."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kw: object):
        self.calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        kw.setdefault("env", GIT_ENV)
        check = bool(kw.pop("check", False))
        return subprocess.run(cmd, check=check, **kw)  # type: ignore[call-overload]

    def verbs(self, word: str) -> list[list[str]]:
        return [c for c in self.calls if word in c]


class Remote:
    """Удалённый репо: bare + рабочая копия для новых коммитов."""

    def __init__(self, tmp: Path, name: str = "comp") -> None:
        self.bare = tmp / f"{name}-remote.git"
        self.work = tmp / f"{name}-work"
        git("init", "--bare", "-b", "main", str(self.bare))
        git("init", "-b", "main", str(self.work))
        git("remote", "add", "origin", self.url, cwd=self.work)

    @property
    def url(self) -> str:
        return f"file://{self.bare}"

    def commit(self, text: str) -> str:
        (self.work / "f.txt").write_text(text)
        (self.work / "uv.lock").write_text("")
        git("add", "-A", cwd=self.work)
        git("commit", "-m", text, cwd=self.work)
        git("push", "origin", "main", cwd=self.work)
        return git("rev-parse", "HEAD", cwd=self.work)


def comp(remote: Remote, sha: str, tool: bool = False, name: str = "comp"):
    return install.Component(name, remote.url, sha, tool)


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    return Remote(tmp_path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


def test_components_file_pins_full_shas() -> None:
    comps = {c.name: c for c in install.load_components()}
    assert set(comps) == {"discovery", "spec-runner"}
    assert all(re.fullmatch(r"[0-9a-f]{40}", c.sha) for c in comps.values())
    assert comps["spec-runner"].tool is True
    assert comps["discovery"].tool is False


def test_components_cover_workspace_dirs() -> None:
    names = {c.name for c in install.load_components()}
    used = set()
    for path in (ROOT / "governance").glob("*.py"):
        used |= set(re.findall(r'workspace_dir\("([\w.-]+)"', path.read_text()))
    assert used
    assert used <= names


@pytest.mark.parametrize(
    "body",
    [
        'schema = "x"\n',
        'schema = "darkfactory-components/v1"\n[repos.a]\nurl="u"\nsha="abc"\n',
    ],
)
def test_bad_components_refused(tmp_path: Path, body: str) -> None:
    path = tmp_path / "c.toml"
    path.write_text(body)
    with pytest.raises(install.InstallError):
        install.load_components(path)


def test_fresh_clone_checks_out_pin(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    remote.commit("two")
    rec = Recorder()
    install.sync_component(root, comp(remote, sha), rec)
    assert git("rev-parse", "HEAD", cwd=root / "comp") == sha
    probe = subprocess.run(
        ["git", "-C", str(root / "comp"), "symbolic-ref", "-q", "HEAD"],
        check=False,
        capture_output=True,
    )
    assert probe.returncode != 0


def test_rerun_moves_to_new_pin(remote: Remote, root: Path) -> None:
    first = remote.commit("one")
    rec = Recorder()
    install.sync_component(root, comp(remote, first), rec)
    second = remote.commit("two")
    install.sync_component(root, comp(remote, second), rec)
    assert git("rev-parse", "HEAD", cwd=root / "comp") == second
    status = subprocess.run(
        ["git", "-C", str(root / "comp"), "symbolic-ref", "-q", "HEAD"],
        check=False,
        capture_output=True,
    )
    assert status.returncode != 0


def test_dirty_clone_refused(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    install.sync_component(root, comp(remote, sha), Recorder())
    (root / "comp" / "f.txt").write_text("local edit")
    rec = Recorder()
    with pytest.raises(install.InstallError, match="локальные изменения"):
        install.sync_component(root, comp(remote, sha), rec)
    assert not rec.verbs("checkout")


def test_branch_clone_refused(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    install.sync_component(root, comp(remote, sha), Recorder())
    git("checkout", "main", cwd=root / "comp")
    with pytest.raises(install.InstallError, match="нештатная ветка"):
        install.sync_component(root, comp(remote, sha), Recorder())


def test_origin_mismatch_refused(remote: Remote, root: Path, tmp_path: Path) -> None:
    sha = remote.commit("one")
    install.sync_component(root, comp(remote, sha), Recorder())
    other = Remote(tmp_path, "other")
    rec = Recorder()
    with pytest.raises(install.InstallError, match="origin"):
        install.sync_component(root, comp(other, sha), rec)
    assert not rec.verbs("checkout")


def test_not_a_git_repo_refused(remote: Remote, root: Path) -> None:
    (root / "comp").mkdir()
    with pytest.raises(install.InstallError, match="не git"):
        install.sync_component(root, comp(remote, "a" * 40), Recorder())


def test_tool_component_installed_with_uv_tool(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    rec = Recorder()
    install.sync_component(root, comp(remote, sha, tool=True), rec)
    assert ["uv", "tool", "install", "--force", str(root / "comp")] in rec.calls


def test_plain_component_uv_sync(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    rec = Recorder()
    install.sync_component(root, comp(remote, sha), rec)
    uv = [c for c in rec.calls if c[0] == "uv"]
    assert uv == [["uv", "sync", "--frozen"]]


def test_plain_component_without_lock_uv_sync(remote: Remote, root: Path) -> None:
    sha = remote.commit("one")
    git("rm", "-q", "uv.lock", cwd=remote.work)
    git("commit", "-m", "nolock", cwd=remote.work)
    git("push", "origin", "main", cwd=remote.work)
    sha = git("rev-parse", "HEAD", cwd=remote.work)
    rec = Recorder()
    install.sync_component(root, comp(remote, sha), rec)
    assert [c for c in rec.calls if c[0] == "uv"] == [["uv", "sync"]]


def write_run(runs: Path, run_id: str, status: str | None) -> None:
    (runs / run_id).mkdir(parents=True)
    body = "{not json" if status is None else json.dumps({"status": status})
    (runs / run_id / "run.json").write_text(body)


def test_active_run_refuses_before_any_clone(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    write_run(runs, "r1", "waiting_human_merge")
    rec = Recorder()
    code = install.main(
        ["--login", "x", "--yes"],
        runs_root=runs,
        devtools=tmp_path / "dt",
        run=rec,
    )
    assert code == 2
    assert rec.calls == []
    assert "r1" in capsys.readouterr().err


def test_terminal_runs_not_active(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_run(runs, "a", "completed")
    write_run(runs, "b", "brief_ready")
    write_run(runs, "c", "merged_unverified")
    assert install.active_runs(runs) == []
    assert install.active_runs(tmp_path / "missing") == []


def test_unreadable_run_counts_active(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_run(runs, "bad", None)
    assert install.active_runs(runs) == ["bad"]


def test_existing_config_untouched(tmp_path: Path) -> None:
    path = tmp_path / "c" / "config.toml"
    path.parent.mkdir()
    path.write_bytes(b"keep me\n")
    assert install.write_config("x", tmp_path, path) is False
    assert path.read_bytes() == b"keep me\n"


def test_written_config_loads_as_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    use_test_real_home(monkeypatch, home)
    ws = tmp_path / "ws"
    ws.mkdir()
    path = home / ".config" / "darkfactory" / "config.toml"
    assert install.write_config("alice", ws, path) is True
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    cfg = df_config.load()
    assert cfg.mode == "test"
    assert cfg.accounts.human == cfg.accounts.agent == "alice"
    assert cfg.workspace_root == ws


def test_launcher_content_and_mode(tmp_path: Path) -> None:
    dt = tmp_path / "devtools"
    out = install.write_launcher(tmp_path / "bin", dt)
    assert out == tmp_path / "bin" / "dfl"
    assert stat.S_IMODE(out.stat().st_mode) == 0o755
    text = out.read_text()
    assert f'cd "{dt}"' in text
    assert 'python -m lite.dfl --cwd "$here"' in text
    assert text.startswith("#!/bin/sh\n")


def test_main_full_flow(tmp_path: Path, remote: Remote, capsys) -> None:
    sha = remote.commit("one")
    comps = tmp_path / "components.toml"
    comps.write_text(
        'schema = "darkfactory-components/v1"\n'
        f'[repos.comp]\nurl = "{remote.url}"\nsha = "{sha}"\n'
    )
    dt = tmp_path / "ws" / "devtools"
    dt.mkdir(parents=True)
    cfg = tmp_path / "cfg" / "config.toml"
    rec = Recorder()
    code = install.main(
        ["--login", "bob"],
        runs_root=tmp_path / "runs",
        devtools=dt,
        components=comps,
        config_path=cfg,
        bin_dir=tmp_path / "bin",
        run=rec,
    )
    assert code == 0
    assert (tmp_path / "ws" / "comp" / ".git").exists()
    assert 'root = "' + str(tmp_path / "ws") + '"' in cfg.read_text()
    assert (tmp_path / "bin" / "dfl").exists()


def test_existing_strict_config_warns(tmp_path: Path, remote: Remote, capsys) -> None:
    comps = tmp_path / "components.toml"
    comps.write_text('schema = "darkfactory-components/v1"\n')
    cfg = tmp_path / "config.toml"
    cfg.write_text('mode = "strict"\n')
    code = install.main(
        ["--login", "bob"],
        runs_root=tmp_path / "runs",
        devtools=tmp_path / "dt",
        components=comps,
        config_path=cfg,
        bin_dir=tmp_path / "bin",
        run=Recorder(),
    )
    assert code == 0
    assert "dfl работает только в режиме test" in capsys.readouterr().err


def test_install_sh_syntax() -> None:
    res = subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=False)
    assert res.returncode == 0


# --- финальное ревью: I2 и обязательные мелочи ---


def _write_json(runs: Path, run_id: str, data: dict) -> None:
    (runs / run_id).mkdir(parents=True)
    (runs / run_id / "run.json").write_text(json.dumps(data))


def test_completed_test_engineer_without_accept_is_active(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    eng = {"status": "completed", "mode": "test", "interview": {"frame": "engineer"}}
    _write_json(runs, "open", {**eng, "ops": {}})
    accepted = {"code-accept": {"status": "completed", "result": {"url": "u"}}}
    _write_json(runs, "done", {**eng, "ops": accepted})
    _write_json(runs, "strict", {**eng, "mode": "strict", "ops": {}})
    _write_json(runs, "cust", {"status": "completed", "mode": "test", "ops": {}})
    assert install.active_runs(runs) == ["open"]


def test_active_run_refusal_names_abandon(tmp_path: Path, capsys) -> None:
    runs = tmp_path / "runs"
    write_run(runs, "r1", "running")
    assert install.main(["--login", "x"], runs_root=runs, run=Recorder()) == 2
    err = capsys.readouterr().err
    assert "dfl abandon" in err and "Завершите их" not in err


def test_all_existing_clones_checked_before_any_effect(
    tmp_path: Path, root: Path
) -> None:
    good, bad = Remote(tmp_path, "good"), Remote(tmp_path, "bad")
    sha_good, sha_bad = good.commit("one"), bad.commit("one")
    install.sync_component(root, comp(good, sha_good, name="good"), Recorder())
    install.sync_component(root, comp(bad, sha_bad, name="bad"), Recorder())
    (root / "bad" / "f.txt").write_text("local edit")
    new_good = good.commit("two")
    comps = tmp_path / "components.toml"
    comps.write_text(
        'schema = "darkfactory-components/v1"\n'
        f'[repos.good]\nurl = "{good.url}"\nsha = "{new_good}"\n'
        f'[repos.bad]\nurl = "{bad.url}"\nsha = "{sha_bad}"\n'
    )
    rec = Recorder()
    code = install.main(
        ["--login", "x"],
        runs_root=tmp_path / "runs",
        devtools=root / "devtools",
        components=comps,
        config_path=tmp_path / "cfg.toml",
        bin_dir=tmp_path / "bin",
        run=rec,
    )
    assert code == 1
    assert not rec.verbs("fetch") and not rec.verbs("checkout")
    assert git("rev-parse", "HEAD", cwd=root / "good") == sha_good


def _no_login(cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, 1, "", "not logged in")


def test_yes_without_gh_login_names_login_flag() -> None:
    args = install._parse(["--yes"])
    with pytest.raises(install.InstallError, match="--login"):
        install._resolve_login(args, _no_login)


def test_non_tty_stdin_names_login_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(install.InstallError, match="--login"):
        install._resolve_login(install._parse([]), _no_login)


def test_failed_fetch_shows_git_stderr_and_next_step(
    remote: Remote, root: Path
) -> None:
    sha = remote.commit("one")
    install.sync_component(root, comp(remote, sha), Recorder())
    git("remote", "set-url", "origin", "file:///nonexistent/x.git", cwd=root / "comp")
    broken = install.Component("comp", "file:///nonexistent/x.git", sha, False)
    with pytest.raises(install.InstallError) as err:
        install.sync_component(root, broken, Recorder())
    text = str(err.value)
    assert "git fetch" in text and "nonexistent" in text
    assert "проверьте сеть" in text and "повторите install.sh" in text


def test_failed_fresh_checkout_removes_clone(remote: Remote, root: Path) -> None:
    remote.commit("one")
    missing = "f" * 40
    with pytest.raises(install.InstallError, match="git checkout"):
        install.sync_component(root, comp(remote, missing), Recorder())
    assert not (root / "comp").exists()
    sha = git("rev-parse", "HEAD", cwd=remote.work)
    install.sync_component(root, comp(remote, sha), Recorder())  # повтор проходит
    assert git("rev-parse", "HEAD", cwd=root / "comp") == sha


def test_failed_clone_named(tmp_path: Path, root: Path) -> None:
    bad = install.Component("comp", "file:///nonexistent/x.git", "a" * 40, False)
    with pytest.raises(install.InstallError, match="(?s)git clone .*проверьте сеть"):
        install.sync_component(root, bad, Recorder())
    assert not (root / "comp").exists()


def test_failed_uv_named_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def boom(root: Path, comps: list, run: object) -> None:
        raise subprocess.CalledProcessError(1, ["uv", "sync"])

    monkeypatch.setattr(install, "check_components", boom)
    comps = tmp_path / "components.toml"
    comps.write_text('schema = "darkfactory-components/v1"\n')
    code = install.main(
        ["--login", "x"],
        runs_root=tmp_path / "runs",
        devtools=tmp_path / "dt",
        components=comps,
        run=Recorder(),
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "uv sync завершилась с кодом 1" in err and "повторите install.sh" in err
