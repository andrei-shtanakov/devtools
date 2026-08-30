"""Behaviour console (Task 4): --json/plain путь без textual, tmux-команды.

Модель, не пиксели (правило tui.md): текстовые тесты покрывают то, что
`--json`/non-TTY plain работают без установленного textual (ленивый import
внутри `_run_tui`), и что resume/verify строят tmux-команды с `=`-таргетом и
`make behaviour-run ARGS=...` — сам `BehaviourConsoleApp` руками, без
автотестов на рендер.
"""

from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from governance import console
from governance import run_state as rs

try:
    from governance import console_model as cm
except ImportError:  # steward (группа governance) не установлен
    cm = None  # type: ignore[assignment]

# `console_model` тянет `steward` транзитивно через `bundle_state.py`
# (безусловный модульный import) — той же группой uv `governance`, что и
# `textual`. `console.py` импортирует его лениво (той же дисциплиной, что и
# textual), поэтому сам модуль `governance.console` собирается и без группы,
# но пути, которым реально нужен `cm.list_runs()` (--json/plain), без
# steward не выполнить — скипаем их отдельно, а не роняем сбор всего файла.
requires_console_model = pytest.mark.skipif(
    cm is None,
    reason="steward (группа governance) не установлен — cm.list_runs() недоступен",
)


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path)
    return tmp_path


def _mk(run_id: str, **overrides) -> rs.RunState:
    kwargs = dict(
        subject="тест", repo="alpha", repo_slug="owner/alpha", ws_id="WS-T1",
        target_dir="/tmp/alpha", bundle_dir="workstreams/WS-T1/spec",
        profile="profiles/team-exp.yaml", run_id=run_id,
    )
    kwargs.update(overrides)
    s = rs.new_run(**kwargs)
    rs.save(s)
    return s


@pytest.fixture()
def no_textual(monkeypatch):
    """Симулирует окружение без установленного textual (--frozen без группы

    governance): любой `import textual`/`import textual.xxx` должен упасть
    ImportError-ом — это гарантирует, что --json/plain пути его не трогают.
    """
    for name in list(sys.modules):
        if name == "textual" or name.startswith("textual."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "textual", None)


# --- --json path, no textual -------------------------------------------


@requires_console_model
def test_json_path_works_without_textual(runs_root, no_textual, capsys) -> None:
    _mk("r-0001")
    rc = console.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = cm.rows_to_json(cm.list_runs())
    assert out.strip() == payload.strip()
    assert '"run_id": "r-0001"' in out


@requires_console_model
def test_json_path_empty_runs(runs_root, no_textual, capsys) -> None:
    rc = console.main(["--json"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "[]"


# --- non-TTY plain path, no textual --------------------------------------


@requires_console_model
def test_plain_path_without_textual(runs_root, no_textual, capsys) -> None:
    """pytest's capsys уже делает stdin/stdout non-tty — плейн-ветка

    срабатывает без --json и без textual в sys.modules.
    """
    _mk("r-0002")
    rc = console.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "r-0002" in out
    assert "ws=WS-T1" in out
    assert "repo=alpha" in out


@requires_console_model
def test_plain_path_no_runs_says_so(runs_root, no_textual, capsys) -> None:
    rc = console.main([])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "no runs"


# --- tmux command construction: resume ------------------------------------


def test_launch_resume_starts_new_session(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    status = console.launch_resume("r-0001", tmp_path)
    assert status == "started beh-r-0001"

    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=beh-r-0001"

    new_session_call = next(c for c in calls if c[:2] == ["tmux", "new-session"])
    assert new_session_call[:6] == [
        "tmux", "new-session", "-d", "-s", "beh-r-0001", "-c",
    ]
    assert new_session_call[6] == str(tmp_path)
    shell_cmd = new_session_call[7]
    assert shell_cmd == (
        "make behaviour-run ARGS='resume --run-id r-0001'; exec $SHELL"
    )


def test_launch_resume_skips_existing_session(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    status = console.launch_resume("r-0002", tmp_path)
    assert status == "exists: tmux attach -t =beh-r-0002"
    assert not any(c[:2] == ["tmux", "new-session"] for c in calls)


def test_launch_resume_uses_exact_target_not_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    """has-session матчит по точному `=`-таргету — `beh-r-1` не должен

    ложно "находить" уже существующую `beh-r-10`.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    status = console.launch_resume("r-1", tmp_path)
    assert status == "started beh-r-1"
    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=beh-r-1"


def test_launch_resume_raises_on_tmux_failure(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        console.launch_resume("r-0003", tmp_path)


# --- tmux command construction: verify -------------------------------------


def test_launch_verify_builds_parent_and_run_id_args(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    status = console.launch_verify("r-0001", "r-0001-v2", tmp_path)
    assert status == "started beh-r-0001-v2"

    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=beh-r-0001-v2"

    new_session_call = next(c for c in calls if c[:2] == ["tmux", "new-session"])
    shell_cmd = new_session_call[-1]
    assert shell_cmd == (
        "make behaviour-run ARGS='verify --parent r-0001 --run-id r-0001-v2'; "
        "exec $SHELL"
    )
