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


# --- --run-id path, no textual (C-1/M-2) ----------------------------------


@requires_console_model
def test_run_id_flag_prints_detail_json_without_textual(
    runs_root, no_textual, capsys,
) -> None:
    """`--run-id <id>` печатает `detail_to_json(run_detail(<id>))` и не
    заходит в TUI-ветку — README документирует именно это (C-1), а
    `detail_to_json` до этой правки был мёртвым кодом в продакшене (M-2)."""
    _mk("r-0010")
    rc = console.main(["--run-id", "r-0010"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = cm.detail_to_json(cm.run_detail("r-0010"))
    assert out.strip() == payload.strip()
    assert '"run_id": "r-0010"' in out


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
        "make behaviour-run ARGS='resume --run-id r-0001'; echo; "
        "echo '=== завершено; Enter — закрыть (авто через 60с)'; "
        "read -t 60 _"
    )


def test_tmux_shell_cmd_self_terminates_not_exec_shell(
    tmp_path: Path, monkeypatch
) -> None:
    """codex-ревью круг 2: `; exec $SHELL` держал сессию живой навсегда —
    после первого «пустого» resume (PR ещё открыт) `=`-дедуп видел её как
    существующую бесконечно, и повторное `r` на том же ряду только
    подсказывало attach, а не запускало прогон дальше. Хвост команды
    обязан читаться и самозакрывать сессию (`read -t 60 _`), не держать
    интерактивный shell открытым."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    console.launch_resume("r-self-terminate", tmp_path)

    new_session_call = next(c for c in calls if c[:2] == ["tmux", "new-session"])
    shell_cmd = new_session_call[-1]
    assert "exec $SHELL" not in shell_cmd
    assert shell_cmd.endswith("read -t 60 _")
    assert shell_cmd.startswith(
        "make behaviour-run ARGS='resume --run-id r-self-terminate'; "
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
    """Сессия именуется от РОДИТЕЛЯ (`beh-verify-<parent>`), не от `run_id`
    потомка (I-7) — ARGS в make-команде при этом несёт свежий child-id."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    status = console.launch_verify("r-0001", "r-0001-v2", tmp_path)
    assert status == "started beh-verify-r-0001"

    has_session_call = next(c for c in calls if c[:2] == ["tmux", "has-session"])
    assert has_session_call[-1] == "=beh-verify-r-0001"

    new_session_call = next(c for c in calls if c[:2] == ["tmux", "new-session"])
    assert new_session_call[4] == "beh-verify-r-0001"
    shell_cmd = new_session_call[-1]
    assert shell_cmd == (
        "make behaviour-run ARGS='verify --parent r-0001 --run-id r-0001-v2'; "
        "echo; echo '=== завершено; Enter — закрыть (авто через 60с)'; "
        "read -t 60 _"
    )


def test_launch_verify_dedups_by_parent_across_distinct_child_ids(
    tmp_path: Path, monkeypatch
) -> None:
    """I-7 regression: `verify_plan` генерирует свежий child run_id на
    КАЖДЫЙ вызов (`os.urandom`) — дедуп именем от child никогда бы не
    совпал. Второе нажатие `v` на том же ряду (новый child, тот же parent)
    обязано найти уже поднятую сессию, а не стартовать вторую — иначе
    второй вызов `runner.verify()` создаёт ВТОРОЙ remediation-issue у уже
    помеченного родителя."""

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    first = console.launch_verify("r-0009", "r-0009-vaaaa", tmp_path)
    second = console.launch_verify("r-0009", "r-0009-vbbbb", tmp_path)
    assert first == second == "exists: tmux attach -t =beh-verify-r-0009"


# --- verify_plan: parent = выбранный merged_unverified ряд, свежий child --


def _row(status: str, run_id: str = "r-0001") -> "cm.RunRow":
    # `remediated_by` у `merged_unverified`-родителя всегда `None` (это
    # поле потомка, указывающее на родителя) — фикс-ревью бага, где
    # action_verify_selected путал их местами.
    return cm.RunRow(
        run_id=run_id, ws_id="WS-T1", repo="alpha", status=status,
        step="—", pr=None, remediated_by=None,
    )


@requires_console_model
def test_verify_plan_merged_unverified_returns_parent_and_new_child() -> None:
    row = _row("merged_unverified", run_id="r-0001")
    plan = console.verify_plan(row)
    assert not isinstance(plan, str)
    parent_run_id, child_run_id = plan
    assert parent_run_id == "r-0001"
    assert child_run_id != "r-0001"
    assert child_run_id.startswith("r-0001-v")
    rs.validate_id_component(child_run_id)  # не кидает -> валидный run_id


@requires_console_model
@pytest.mark.parametrize(
    "status", ["running", "waiting_human_merge", "stopped_author", "corrupt"]
)
def test_verify_plan_rejects_non_merged_unverified(status: str) -> None:
    row = _row(status, run_id="r-0002")
    plan = console.verify_plan(row)
    assert isinstance(plan, str)
    assert "r-0002" in plan
    assert "merged_unverified" in plan


@requires_console_model
def test_verify_plan_generates_distinct_child_ids_on_repeat_calls() -> None:
    row = _row("merged_unverified", run_id="r-0003")
    first = console.verify_plan(row)
    second = console.verify_plan(row)
    assert not isinstance(first, str)
    assert not isinstance(second, str)
    assert first[0] == second[0] == "r-0003"
    assert first[1] != second[1]


# --- _safe_run_detail: битый run.json не роняет TUI (I-4) -----------------


@requires_console_model
def test_safe_run_detail_returns_detail_on_healthy_run(runs_root) -> None:
    _mk("r-0011")
    result = console._safe_run_detail("r-0011")
    assert not isinstance(result, str)
    assert result.row.run_id == "r-0011"


@requires_console_model
def test_safe_run_detail_returns_error_string_on_corrupt_run_json(
    runs_root,
) -> None:
    """Enter на `status="corrupt"`-ряду (`list_runs` уже так его метит)
    раньше выбрасывал исключение прямо из `DetailScreen.on_mount` и ронял
    TUI. `_safe_run_detail` — чистая обёртка без textual: строка-ошибка
    вместо падения, тестируется напрямую."""
    (runs_root / "r-broken").mkdir()
    (runs_root / "r-broken" / "run.json").write_text("{not json")

    result = console._safe_run_detail("r-broken")
    assert isinstance(result, str)
    assert "r-broken" in result
    assert "битый run.json" in result
