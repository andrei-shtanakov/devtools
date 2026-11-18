"""Английский режим личного пути `dfl` (#769 срез 1).

Язык вызова до первого вывода: `--lang`, `DFL_LANG`, язык продолжаемого
прогона, `ru`; баннер, `dfl status` по прогонам, `dfl abandon`. Окружение —
фикстура `env` из `tests/test_lite_dfl.py`, `advance` — рекордер.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from governance import df_config, user_targets
from lite import dfl, i18n
from lite.personal import state as ps
from tests.personal_en import cyrillic
from tests.test_lite_dfl import REPO, SLUG, URL, Env, env  # noqa: F401
from tests.test_lite_dfl_personal import Advance, personal  # noqa: F401

SUBJECT = "Expense log"


@pytest.fixture(autouse=True)
def _ru_after() -> object:
    yield
    i18n.set_language("ru")


@pytest.fixture()
def registered(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Репо зарегистрировано: `_registered_target` находит его без `register`."""
    target = (env.tmp / "target").resolve()
    monkeypatch.setattr(
        user_targets, "load", lambda: {REPO: user_targets.UserTarget(REPO, target, URL)}
    )


def _saved(
    e: Env, *, language: str | None, step: str = "design", subject: str = SUBJECT
) -> ps.PersonalRun:
    """Личный прогон в журнале; `language=None` — run.json без поля (старый)."""
    run = ps.new_personal_run(
        subject, REPO, SLUG, str(e.tmp / "target"), date.today(), language="ru"
    )
    run.step = step
    ps.save(run)
    path = ps.run_dir(run.run_id) / "run.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if language is None:
        del raw["language"]
    else:
        raw["language"] = language
    path.write_text(json.dumps(raw), encoding="utf-8")
    return ps.load(run.run_id)


def _no_personal_runs() -> bool:
    return not ps.personal_root().exists() or not any(
        ps.personal_root().glob("*/run.json")
    )


def test_lang_flag_unknown_refused_before_banner(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = env.run("run", "--subject", SUBJECT, "--track", "personal", "--lang", "de")
    assert code == 2
    captured = capsys.readouterr()
    assert "режим test" not in captured.out + captured.err
    assert "dfl run: отказ (код 2)" in captured.err
    assert "'de'" in captured.err
    assert personal.runs == [] and _no_personal_runs()


def test_env_lang_unknown_refused(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DFL_LANG", "xx")
    assert env.run("status") == 2
    captured = capsys.readouterr()
    assert "режим test" not in captured.out
    assert "DFL_LANG" in captured.err and "отказ (код 2)" in captured.err


def test_new_run_records_language(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    args = ("run", "--subject", SUBJECT, "--track", "personal", "--lang", "en")
    assert env.run(*args) == 0
    [run] = personal.runs
    assert run.language == "en"
    assert ps.load(run.run_id).language == "en"
    assert cyrillic(capsys.readouterr().out) == []


def test_track_question_in_call_language(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    queue = ["3", "1"]
    monkeypatch.setattr(dfl.ic, "stdin_read", lambda prompt: queue.pop(0))
    assert env.run("run", "--subject", SUBJECT, "--lang", "en") == 0
    out = capsys.readouterr().out
    assert "What kind of project is this?" in out
    assert cyrillic(out) == []
    assert personal.runs[0].language == "en"


def test_continue_without_flag_uses_run_language(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    run = _saved(env, language="en")
    assert env.run("run") == 0
    captured = capsys.readouterr()
    assert [r.run_id for r in personal.runs] == [run.run_id]
    assert captured.out.startswith("test mode:")
    assert cyrillic(captured.out) == [] and cyrillic(captured.err) == []


def test_stored_language_beats_flag_with_warning(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DFL_LANG", "ru")
    _saved(env, language="en")
    assert env.run("run", "--lang", "ru") == 0
    captured = capsys.readouterr()
    warnings = captured.err.strip().splitlines()
    assert len(warnings) == 1 and "en" in warnings[0]
    assert cyrillic(captured.out) == [] and cyrillic(captured.err) == []


def test_invalid_stored_language_warns_and_stays_russian(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    _saved(env, language="de")
    assert env.run("run") == 0
    captured = capsys.readouterr()
    [warning] = captured.err.strip().splitlines()
    assert "'de'" in warning and cyrillic(warning)
    assert captured.out.startswith("режим test:")


def test_old_run_stays_russian(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    run = _saved(env, language=None)
    assert run.language == "ru"
    assert env.run("run") == 0
    captured = capsys.readouterr()
    assert captured.out == f"{df_config.test_banner_line(df_config.load())}\n\n"
    assert "режим test: ослаблено" in captured.out and captured.err == ""
    assert [r.run_id for r in personal.runs] == [run.run_id]


def test_status_mixed_runs(
    env: Env,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    done = _saved(env, language="ru", step="done", subject="Старое")
    done.manual = {
        "checks": [
            {"beh_id": "BEH-01", "title": "t", "task_id": "TASK-001", "steps": None}
        ],
        "answers": {"BEH-01": {"verdict": "caveat"}},
    }
    ps.save(done)
    active = _saved(env, language="en")
    assert env.run("status") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("режим test:")  # заголовок — язык вызова
    [en_line] = [ln for ln in lines if active.run_id in ln]
    assert en_line == f"personal run {active.run_id}: step design"
    [ru_line] = [ln for ln in lines if done.run_id in ln]
    assert ru_line.startswith(f"личный прогон {done.run_id}: закончен; ")
    assert ru_line.endswith("с оговоркой: 1")
    assert i18n.current() == "ru"


def test_status_header_in_flag_language(
    env: Env,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DFL_LANG", "en")
    run = _saved(env, language="ru", step="design")
    assert env.run("-v", "status") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "test mode: compared with strict, disabled:"
    assert cyrillic("\n".join(lines[: lines.index("")])) == []
    assert f"личный прогон {run.run_id}: шаг design" in lines


def test_banner_en_translates_every_relaxation() -> None:
    i18n.reset_misses()
    with i18n.language("en"):
        for item in df_config.TEST_RELAXATIONS:
            assert cyrillic(i18n.t(item)) == [], item
        assert cyrillic(i18n.t(df_config.TEST_BANNER_HEADER)) == []
    assert i18n.misses() == frozenset()


def test_abandon_english_run(
    env: Env,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    run = _saved(env, language="en")
    assert env.run("abandon") == 0
    out = capsys.readouterr().out
    assert f"abandoned: {run.run_id}" in out
    assert cyrillic(out) == []
    assert ps.active_for(SLUG) is None
