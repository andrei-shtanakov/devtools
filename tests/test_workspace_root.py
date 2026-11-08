"""Раскладка workspace из конфига DarkFactory (`workspace.root`), план 2, Task 1.

Соседние репо ищутся через `workspace.root`, а не через родителя devtools.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from governance import accept_pr, author_launch, df_config, ops, spec_loop
from tests.df_config_fixture import REAL_HOME_ENV, write_config

ROOT = Path(__file__).resolve().parent.parent
GH_STUB = '#!/bin/sh\necho "gh $*" >> "$GH_STUB_LOG"\nexit 1\n'
SCRIPTS = ["review-pr.sh", "merge-pr.sh", "human-merge.sh", "attest-vendor.sh"]


def _cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ws: Path) -> dict[str, str]:
    xdg = tmp_path / "xdg"
    env = write_config(xdg, mode="test", workspace_root=ws)
    monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
    return env


def test_workspace_root_from_config(tmp_path, monkeypatch) -> None:
    _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    assert df_config.workspace_root() == tmp_path / "ws"


@pytest.mark.no_df_config
def test_workspace_root_without_config_raises() -> None:
    with pytest.raises(df_config.ConfigError, match="config.toml"):
        df_config.workspace_root()


def test_workspace_root_broken_config_raises(tmp_path, monkeypatch) -> None:
    env = _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    (Path(env["XDG_CONFIG_HOME"]) / "darkfactory" / "config.toml").write_text("[[")
    with pytest.raises(df_config.ConfigError, match="TOML"):
        df_config.workspace_root()


def test_workspace_root_dangling_symlink_raises(tmp_path, monkeypatch) -> None:
    env = _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    cfg = Path(env["XDG_CONFIG_HOME"]) / "darkfactory" / "config.toml"
    cfg.unlink()
    cfg.symlink_to(tmp_path / "nowhere.toml")
    with pytest.raises(df_config.ConfigError, match="висячая симлинка"):
        df_config.workspace_root()


def test_owner_layout_unchanged(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    base = home / "labs/all_ai_orchestrators"
    for name in ("discovery", "kapelle", "disputatio/.venv"):
        (base / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    example = ROOT / "templates/darkfactory/config.strict.example.toml"
    xdg = tmp_path / "xdg"
    (xdg / "darkfactory").mkdir(parents=True)
    (xdg / "darkfactory/config.toml").write_text(example.read_text())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert df_config.workspace_dir("discovery") == base / "discovery"
    assert spec_loop.workspace_root() / "kapelle" == base / "kapelle"
    ready = SimpleNamespace(
        target=tmp_path / "t",
        bundle=tmp_path,
        git_dir=tmp_path,
        common_dir=tmp_path,
        run_dir=tmp_path,
    )
    targets = author_launch._probe_targets(ready)  # noqa: SLF001
    assert base / "disputatio/.venv" in targets.extra
    # Значение, которое четыре скрипта читают через df_cfg.
    cli = subprocess.run(
        [
            "python3",
            "-I",
            str(ROOT / "governance/df_config.py"),
            "get",
            "workspace.root",
        ],
        env={
            "XDG_CONFIG_HOME": str(xdg),
            "HOME": str(home),
            "PATH": os.environ["PATH"],
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.stdout.strip() == str(base), cli.stderr


def test_workspace_dir_missing_names_path(tmp_path, monkeypatch) -> None:
    _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    with pytest.raises(df_config.ConfigError) as err:
        df_config.workspace_dir("discovery")
    assert f"workspace.root={tmp_path / 'ws'}: нет каталога discovery" in str(err.value)


def test_ops_discovery_argv_uses_workspace_root(tmp_path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    (ws / "discovery").mkdir(parents=True)
    _cfg(tmp_path, monkeypatch, ws)
    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(argv)
        raise RuntimeError("stop")

    monkeypatch.setattr(ops.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        ops.RealOps()._discovery(["x"], str(tmp_path))  # noqa: SLF001
    assert str(ws / "discovery") in seen[0]


def test_ops_discovery_missing_dir_refuses_before_subprocess(
    tmp_path, monkeypatch
) -> None:
    _cfg(tmp_path, monkeypatch, tmp_path / "ws")

    def boom(*_a, **_k):
        raise AssertionError("subprocess вызван")

    monkeypatch.setattr(ops.subprocess, "run", boom)
    with pytest.raises(df_config.ConfigError, match="нет каталога discovery"):
        ops.RealOps()._discovery(["x"], str(tmp_path))  # noqa: SLF001


def test_accept_pr_target_dir_missing(tmp_path, monkeypatch, capsys) -> None:
    _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    rc = accept_pr.main(["--repo", "kapelle", "--pr", "1"])
    out = capsys.readouterr().out
    assert rc == 2 and "конфиг DarkFactory" in out and "нет каталога kapelle" in out


def test_spec_loop_workspace_root_lazy(tmp_path, monkeypatch) -> None:
    _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    assert spec_loop.workspace_root() == tmp_path / "ws"
    assert spec_loop.manifest_path() == (
        tmp_path / "ws/ai-orchestrators-workspace/workspace-manifest.toml"
    )


def _run(
    script: str, env: dict[str, str], tmp_path: Path
) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    full = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GH_STUB_LOG": str(tmp_path / "gh.log"),
        # Якорь режима test для `python3 -I df_config.py` (шим в PATH выше).
        REAL_HOME_ENV: os.environ[REAL_HOME_ENV],
        **env,
    }
    return subprocess.run(
        ["sh", str(ROOT / script), "nosuchrepo", "1"],
        env=full,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_fleet_root_from_config(script, tmp_path, monkeypatch) -> None:
    ws = tmp_path / "separate-ws"
    ws.mkdir()
    env = _cfg(tmp_path, monkeypatch, ws)
    done = _run(script, env, tmp_path)
    assert done.returncode == 2, done.stderr
    assert f"не найдено в {ws}" in done.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_fleet_root_env_beats_config(script, tmp_path, monkeypatch) -> None:
    ws, other = tmp_path / "ws", tmp_path / "other"
    ws.mkdir()
    other.mkdir()
    env = _cfg(tmp_path, monkeypatch, ws)
    done = _run(script, {**env, "FLEET_ROOT": str(other)}, tmp_path)
    assert done.returncode == 2, done.stderr
    assert f"не найдено в {other}" in done.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_without_config_refuses(script, tmp_path) -> None:
    done = _run(script, {"XDG_CONFIG_HOME": str(tmp_path / "empty")}, tmp_path)
    assert done.returncode == 2
    assert "конфиг DarkFactory" in done.stderr and "config.toml" in done.stderr
    assert done.stderr.count("конфиг DarkFactory") == 1, done.stderr


@pytest.mark.no_df_config
def test_author_launch_probe_config_error_is_named_refusal(monkeypatch) -> None:
    from types import SimpleNamespace

    from governance import author_launch, sandbox

    monkeypatch.setattr(sandbox, "render_profile", lambda *_a, **_k: "p")
    monkeypatch.setattr(author_launch, "call_env", lambda *_a, **_k: {})

    def paid(*_a, **_k):
        raise AssertionError("платный вызов до проверки конфига")

    monkeypatch.setattr(author_launch, "_run_agent", paid)
    out = author_launch._launch_in(  # noqa: SLF001
        SimpleNamespace(), SimpleNamespace(token="t"), SimpleNamespace(), str, None
    )
    assert "конфиг DarkFactory" in str(out)


def test_accept_pr_target_dir_is_workspace_repo(tmp_path, monkeypatch) -> None:
    ws = tmp_path / "separate-ws"
    (ws / "kapelle").mkdir(parents=True)
    _cfg(tmp_path, monkeypatch, ws)
    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(accept_pr.subprocess, "run", fake_run)
    rc = accept_pr.main(["--repo", "kapelle", "--pr", "1"])
    assert rc == 2
    assert seen[0][:3] == ["git", "-C", str(ws / "kapelle")]


def test_no_sibling_lookup_via_devtools_parent() -> None:
    """Раскладка — только из конфига: ни `DEVTOOLS_ROOT.parent` в governance/,
    ни `dirname "$script_dir"` как умолчание FLEET_ROOT в скриптах."""
    for path in (ROOT / "governance").rglob("*.py"):
        assert "DEVTOOLS_ROOT.parent" not in path.read_text(), path
    for script in SCRIPTS:
        text = (ROOT / script).read_text()
        assert 'dirname "$script_dir"' not in text, script


def test_runner_main_names_config_error(tmp_path, monkeypatch, capsys) -> None:
    from governance import runner

    def boom(_argv):
        raise df_config.ConfigError("workspace.root=/w: нет каталога discovery")

    monkeypatch.setattr(runner, "_main", boom)
    assert runner.main(["resume", "--run-id", "r"]) == 2
    err = capsys.readouterr().err
    assert "runner: конфиг DarkFactory: workspace.root=/w" in err
    assert "Traceback" not in err


def test_brief_tools_approve_missing_discovery_publishes_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    from governance import brief_tools

    _cfg(tmp_path, monkeypatch, tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    target = tmp_path / "customer-brief.md"

    def approve(*_a, **_k):
        target.write_text("опубликовано")

    monkeypatch.setattr(brief_tools, "approve", approve)
    assert brief_tools.main(["approve", "--run", "r", "--pr", "1"]) == 2
    err = capsys.readouterr().err
    assert "brief-tools: конфиг DarkFactory: workspace.root=" in err
    assert "нет каталога discovery" in err and "Traceback" not in err
    assert not target.exists()


def test_brief_tools_main_names_config_error_from_ops(
    tmp_path, monkeypatch, capsys
) -> None:
    from governance import brief_tools

    def boom(*_a, **_k):
        raise df_config.ConfigError("x")

    monkeypatch.setattr(brief_tools, "propose", boom)
    assert brief_tools.main(["propose", "--run", "r"]) == 2
    assert "brief-tools: конфиг DarkFactory: x" in capsys.readouterr().err
