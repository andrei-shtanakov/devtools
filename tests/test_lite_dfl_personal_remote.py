"""`dfl note`, `dfl brief`, личный `dfl interview` (#766 срез 4, Task 3).

Окружение — фикстура `env`; шаг интервью ставит вопрос `remote_interview.step`
(агент не зовётся)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance import user_targets
from lite import dfl, i18n, pending
from lite.personal import interviewer
from lite.personal import remote_interview as ri
from lite.personal import state as ps
from tests.test_lite_dfl import REPO, SLUG, URL, Env, env  # noqa: F401
from tests.test_lite_personal_remote_interview import BRIEF, _bare

SUBJECT = "заметки"


@pytest.fixture(autouse=True)
def _known_target(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    target = env.tmp / "target"
    monkeypatch.setattr(
        dfl.user_targets,
        "load",
        lambda: {REPO: user_targets.UserTarget(REPO, target.resolve(), URL)},
    )


def _personal(lang: str = "ru") -> Path:
    run = ps.new_personal_run(SUBJECT, REPO, SLUG, "/t", date.today(), language=lang)
    ps.save(run)
    run_dir = ps.run_dir(run.run_id)
    with i18n.language(lang):
        assert ri.step(run_dir, SUBJECT, lang=lang) is None
    return run_dir


def _qid(run_dir: Path) -> str:
    rec = pending.load(run_dir)
    assert rec is not None
    return str(rec["id"])


def _file(env: Env, data: object) -> str:  # noqa: F811
    path = env.tmp / "brief.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_note_and_brief_cli_round_trip(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal()
    qid = _qid(run_dir)
    ask = ("note", "--id", qid, "--role", "agent", "--text", "Что должно получиться?")
    assert env.run(*ask, "--option", "a", "--option", "b") == 0
    assert env.run("note", "--id", qid, "--role", "human", "--text", "список") == 0
    assert env.run("brief", "--id", qid, "--file", _file(env, _bare())) == 1
    out = capsys.readouterr().out
    assert ri.BRIEF_PROBLEMS in out and "FR-02" in out
    assert env.run("brief", "--id", qid, "--file", _file(env, BRIEF)) == 0
    out = capsys.readouterr().out
    assert "Умеет" in out and "dfl reply" in out
    rec = pending.load(run_dir)
    assert rec is not None and rec["kind"] == pending.BRIEF_CONFIRM
    assert env.run("reply", "--id", rec["id"], "--text", "да") == 0
    entries = [
        json.loads(ln)
        for ln in (run_dir / interviewer.TRANSCRIPT).read_text("utf-8").splitlines()
    ]
    assert entries[0]["options"] == ["a", "b"]
    assert entries[1] == {"role": "human", "text": "список"}


def test_note_after_confirm_posted_is_stale(env: Env) -> None:  # noqa: F811
    run_dir = _personal()
    qid = _qid(run_dir)
    assert env.run("brief", "--id", qid, "--file", _file(env, BRIEF)) == 0
    before = (run_dir / interviewer.TRANSCRIPT).read_text("utf-8")
    assert env.run("note", "--id", qid, "--role", "human", "--text", "x") == 2
    assert (run_dir / interviewer.TRANSCRIPT).read_text("utf-8") == before


def test_brief_bad_json_refused(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal()
    bad = env.tmp / "bad.json"
    bad.write_text("{не json", encoding="utf-8")
    assert env.run("brief", "--id", _qid(run_dir), "--file", str(bad)) == 2
    assert "не прочитать бриф" in capsys.readouterr().err
    wrong = _file(env, {"brief": BRIEF})
    assert env.run("brief", "--id", _qid(run_dir), "--file", wrong) == 2


def test_interview_json_personal(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal()
    assert env.run("interview", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["track"] == "personal" and data["subject"] == SUBJECT
    assert data["draft"] is None and data["uncovered"] == []
    assert data["max_questions"] == interviewer.MAX_QUESTIONS
    assert env.run("brief", "--id", _qid(run_dir), "--file", _file(env, BRIEF)) == 0
    capsys.readouterr()
    assert env.run("interview", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["draft"]["goals"][0]["id"] == "G-01"
    assert env.run("interview") == 0
    assert "Тема" in capsys.readouterr().out


def test_note_brief_in_english_run(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal("en")
    qid = _qid(run_dir)
    assert env.run("note", "--id", qid, "--role", "human", "--text", "x") == 0
    assert "note recorded" in capsys.readouterr().out
    assert env.run("brief", "--id", qid, "--file", _file(env, _bare())) == 1
    assert i18n.EN[ri.BRIEF_PROBLEMS] in capsys.readouterr().out


def test_note_without_personal_run_refused(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ("note", "--id", "x", "--role", "human", "--text", "x")
    assert env.run(*args) == 2
    assert "нет личного прогона, ждущего бриф" in capsys.readouterr().err
