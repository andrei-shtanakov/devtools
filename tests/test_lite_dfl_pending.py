"""`dfl pending` / `dfl reply` (#766, Task 2).

Окружение — фикстура `env` (`tests/test_lite_dfl.py`); вопрос в каталог прогона
кладёт `pending.asker`, как это делает место ожидания.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance import run_lock
from governance import run_state as rs
from lite import dfl, pending
from lite.personal import state as ps
from tests.test_lite_dfl import REPO, SLUG, URL, Env, _run, env  # noqa: F401

QUESTION = pending.Question(pending.MANUAL, "BEH-01", "Проверьте кнопку входа")


@pytest.fixture(autouse=True)
def _known_target(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Репо уже зарегистрировано: `pending` его не регистрирует сам."""
    target = env.tmp / "target"
    monkeypatch.setattr(
        dfl.user_targets,
        "load",
        lambda: {REPO: dfl.user_targets.UserTarget(REPO, target.resolve(), URL)},
    )


def _enterprise(text: str = "") -> Path:
    state = _run("20260101-000000-aaaa", "WS-1", "customer", "RUNNING")
    run_dir = rs.run_dir(state.run_id)
    if text:
        pending.asker(run_dir)(pending.Question(pending.MANUAL, "BEH-01", text))
    return run_dir


def _personal() -> Path:
    run = ps.new_personal_run("Учёт", REPO, SLUG, "/t", date.today())
    ps.save(run)
    run_dir = ps.run_dir(run.run_id)
    pending.asker(run_dir)(pending.Question(pending.MANUAL, "P-1", "личный вопрос"))
    return run_dir


def test_pending_none(env: Env, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    _enterprise()
    assert env.run("pending") == 0
    assert "вопросов нет" in capsys.readouterr().out
    assert env.run("pending", "--json") == 0
    assert capsys.readouterr().out.strip() == "null"


def test_pending_shows_question_and_reply_hint(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enterprise(QUESTION.text)
    assert env.run("pending") == 0
    out = capsys.readouterr().out
    assert QUESTION.text in out
    assert "dfl reply --id manual:BEH-01:" in out
    assert env.run("pending", "--json") == 0
    assert json.loads(capsys.readouterr().out)["id"] == QUESTION.id


def test_reply_records_and_hints_run(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _enterprise(QUESTION.text)
    assert env.run("reply", "--id", QUESTION.id, "--text", "1") == 0
    assert "продолжить: dfl run" in capsys.readouterr().out
    replies = json.loads((run_dir / pending.REPLIES_FILE).read_text("utf-8"))
    assert replies == {QUESTION.id: "1"}


def test_reply_stale_id_refused(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _enterprise(QUESTION.text)
    assert env.run("reply", "--id", "manual:BEH-01:00000000", "--text", "1") == 2
    assert "вопрос уже другой" in capsys.readouterr().err
    assert not (run_dir / pending.REPLIES_FILE).exists()


def test_reply_busy_lock_refused(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _enterprise(QUESTION.text)
    with run_lock.run_lock(run_dir.name):
        assert env.run("reply", "--id", QUESTION.id, "--text", "1") == 2
    assert not (run_dir / pending.REPLIES_FILE).exists()
    assert "Traceback" not in capsys.readouterr().err


def test_pending_prefers_personal_run(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enterprise(QUESTION.text)
    _personal()
    assert env.run("pending") == 0
    out = capsys.readouterr().out
    assert "личный вопрос" in out
    assert QUESTION.text not in out


def test_reply_text_from_file(env: Env, tmp_path: Path) -> None:  # noqa: F811
    run_dir = _enterprise(QUESTION.text)
    answer = tmp_path / "answer.txt"
    answer.write_text("3: первая строка\nвторая строка\n", encoding="utf-8")
    assert env.run("reply", "--id", QUESTION.id, "--file", str(answer)) == 0
    replies = json.loads((run_dir / pending.REPLIES_FILE).read_text("utf-8"))
    assert replies[QUESTION.id] == "3: первая строка\nвторая строка\n"


def test_status_shows_pending(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enterprise(QUESTION.text)
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "ждёт ответа:" in out and "dfl pending" in out
    assert env.registered == 0


def test_pending_record_has_run_id(env: Env) -> None:  # noqa: F811
    run_dir = _enterprise(QUESTION.text)
    assert pending.load(run_dir)["run_id"] == run_dir.name == "20260101-000000-aaaa"
    personal = _personal()
    assert pending.load(personal)["run_id"] == personal.name


def test_pending_does_not_register(env: Env) -> None:  # noqa: F811
    _enterprise(QUESTION.text)
    assert env.run("pending") == 0
    assert env.registered == 0


def _two_subjects() -> None:
    _run("20260101-000000-aaaa", "WS-1", "customer", "RUNNING", subject="раз")
    _run("20260102-000000-bbbb", "WS-2", "customer", "RUNNING", subject="два")


def test_pending_ambiguous_subjects_surface(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _two_subjects()
    assert env.run("pending") == 2
    assert "выберите" in capsys.readouterr().err
    assert env.run("reply", "--id", QUESTION.id, "--text", "1") == 2
    assert "выберите" in capsys.readouterr().err


def test_reply_to_personal_run(env: Env) -> None:  # noqa: F811
    run_dir = _personal()
    qid = str(pending.load(run_dir)["id"])
    assert env.run("reply", "--id", qid, "--text", "1") == 0
    replies = json.loads((run_dir / pending.REPLIES_FILE).read_text("utf-8"))
    assert replies == {qid: "1"}


def test_reply_unreadable_file(
    env: Env,  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _enterprise(QUESTION.text)
    missing = tmp_path / "нет.txt"
    assert env.run("reply", "--id", QUESTION.id, "--file", str(missing)) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert not (run_dir / pending.REPLIES_FILE).exists()


def test_reply_no_run_at_all(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert env.run("reply", "--id", QUESTION.id, "--text", "1") == 2
    assert "нет вопроса, ждущего ответа" in capsys.readouterr().err


def test_status_shows_personal_pending(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _personal()
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "ждёт ответа:" in out and "личный вопрос" in out


def test_pending_and_status_after_reply_show_answered(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """После `dfl reply` вопрос не приглашает ответить снова (ревью #803)."""
    _enterprise(QUESTION.text)
    assert env.run("reply", "--id", QUESTION.id, "--text", "1") == 0
    capsys.readouterr()
    assert env.run("pending") == 0
    out = capsys.readouterr().out
    assert pending.ANSWERED in out and "dfl reply --id" not in out
    assert env.run("pending", "--json") == 0
    assert json.loads(capsys.readouterr().out)["answered"] is True
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert pending.ANSWERED in out and "ждёт ответа:" not in out


def test_status_personal_answered_once(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal()
    qid = str(pending.load(run_dir)["id"])
    assert env.run("reply", "--id", qid, "--text", "1") == 0
    capsys.readouterr()
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert out.count(pending.ANSWERED) == 1 and "ждёт ответа:" not in out


def test_status_shows_pending_with_two_subjects(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Два незаконченных конвейера: строка ожидания не теряется (ревью #803)."""
    _two_subjects()
    q = pending.Question(pending.MANUAL, "BEH-02", "вопрос второй темы")
    pending.asker(rs.run_dir("20260102-000000-bbbb"))(q)
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert "ждёт ответа: вопрос второй темы" in out


def test_reply_abbreviated_flag_refused(env: Env) -> None:  # noqa: F811
    """Сокращение `--te` не принимается: текст ответа иначе обходил бы
    скрытие в записи сбоя."""
    _enterprise(QUESTION.text)
    with pytest.raises(SystemExit) as exc:
        env.run("reply", "--id", QUESTION.id, "--te", "1")
    assert exc.value.code == 2
