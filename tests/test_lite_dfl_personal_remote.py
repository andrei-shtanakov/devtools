"""`dfl note`, `dfl brief`, личный `dfl interview` (#766 срез 4, Task 3).

Окружение — фикстура `env`; шаг интервью ставит вопрос `remote_interview.step`
(агент не зовётся)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from governance import user_targets
from governance.author_launch import LaunchOutcome
from lite import dfl, i18n, pending
from lite.personal import flow, interviewer
from lite.personal import remote_interview as ri
from lite.personal import state as ps
from lite.personal.brief import brief_from_json, render_brief
from lite.personal.flow import FILENAMES
from tests.test_lite_dfl import REPO, SLUG, URL, Env, env  # noqa: F401
from tests.test_lite_personal_flow import FakeOps, checker
from tests.test_lite_personal_remote_interview import BRIEF, _bare
from tests.test_lite_personal_review import NODE_TEXT

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


def test_note_after_confirm_posted_is_stale(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _personal()
    qid = _qid(run_dir)
    assert env.run("brief", "--id", qid, "--file", _file(env, BRIEF)) == 0
    confirm = _qid(run_dir)
    capsys.readouterr()
    before = (run_dir / interviewer.TRANSCRIPT).read_text("utf-8")
    for stale in (qid, confirm):  # старый id и id brief-confirm (kind guard)
        assert env.run("note", "--id", stale, "--role", "human", "--text", "x") == 2
        assert pending.STALE.split(" — ")[0] in capsys.readouterr().err
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


def test_pending_text_personal_invites_note_not_reply(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Minor 2: `dfl pending` на «нужен бриф» не зовёт в `dfl reply`."""
    run_dir = _personal()
    capsys.readouterr()
    assert env.run("pending") == 0
    out = capsys.readouterr().out
    assert "dfl reply" not in out
    assert f"dfl note --id {_qid(run_dir)}" in out and "dfl brief" in out


def test_interview_json_quiet_on_broken_foreign_run(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Minor 7: предупреждение о битом чужом прогоне — не в stdout `--json`."""
    _personal()
    broken = ps.personal_root() / "broken-run"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{oops", encoding="utf-8")
    capsys.readouterr()
    assert env.run("interview", "--json") == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["track"] == "personal"
    assert "broken-run" in captured.err


def test_note_brief_use_run_language(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Minor 6: сводка и «Верно?» — на языке прогона, не окружения."""
    run_dir = _personal("en")
    with i18n.language("ru"):
        assert (
            dfl.brief_cmd(
                env.tmp / "target",
                None,
                _qid(run_dir),
                Path(_file(env, BRIEF)),
                accept_uncovered=False,
            )
            == 0
        )
    rec = pending.load(run_dir)
    assert rec is not None
    assert rec["text"].endswith(i18n.EN[interviewer.CONFIRM])
    assert render_brief(brief_from_json(BRIEF), lang="en") in rec["text"]


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


def test_personal_path_to_design_without_terminal(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`run → pending → note → brief → reply «да» → run`, пауза на брифе с
    замечанием через `reply`, дальше — до дизайна; агент интервьюера не зовётся."""
    authored: list[str] = []

    def author(call: object, prompt: object) -> LaunchOutcome:
        name = call.filename  # type: ignore[attr-defined]
        authored.append(name)
        path = Path(call.target_dir) / call.bundle_dir / name  # type: ignore[attr-defined]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NODE_TEXT[name], encoding="utf-8")
        return LaunchOutcome(0)

    def no_agent(prompt: str) -> str:
        pytest.fail("платный вызов интервью без терминала")

    def advance(run: ps.PersonalRun, ops: object, **kwargs: object) -> int:
        return real_advance(
            run,
            FakeOps(run),
            read=lambda p: pytest.fail("чтение терминала"),
            agent=no_agent,
            launch=author,
            check=checker("PASS"),
            tty=lambda: False,
            remote=kwargs["remote"],  # type: ignore[arg-type]
        )

    real_advance = flow.advance
    monkeypatch.setattr(flow, "advance", advance)
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    monkeypatch.setattr(dfl, "_track_tty", lambda: False)

    def run_dfl() -> int:
        return env.run(
            "run", "--subject", SUBJECT, "--track", "personal", "--review", "brief"
        )

    assert run_dfl() == 0
    [rd] = [p for p in ps.personal_root().iterdir() if p.is_dir()]
    qid = _qid(rd)
    assert pending.load(rd)["kind"] == pending.INTERVIEW_PERSONAL  # type: ignore[index]
    note = ("note", "--id", qid, "--role")
    assert env.run(*note, "agent", "--text", "Что должно получиться?") == 0
    assert env.run(*note, "human", "--text", "чтоб печатал") == 0
    assert env.run("brief", "--id", qid, "--file", _file(env, BRIEF)) == 0
    confirm = pending.load(rd)
    assert confirm is not None and confirm["kind"] == pending.BRIEF_CONFIRM
    assert env.run("reply", "--id", confirm["id"], "--text", "да") == 0
    assert run_dfl() == 0  # бриф записан, пауза на брифе
    review = pending.load(rd)
    assert review is not None and review["kind"] == pending.REVIEW
    assert review["key"] == "brief" and authored == []
    # замечание к брифу без терминала: заново интервью, бриф и подтверждение
    assert env.run("reply", "--id", review["id"], "--text", "цель шире") == 0
    assert run_dfl() == 0
    again = pending.load(rd)
    assert again is not None and again["kind"] == pending.INTERVIEW_PERSONAL
    assert env.run("brief", "--id", again["id"], "--file", _file(env, BRIEF)) == 0
    confirm = pending.load(rd)
    assert confirm is not None and confirm["kind"] == pending.BRIEF_CONFIRM
    assert env.run("reply", "--id", confirm["id"], "--text", "1") == 0
    assert run_dfl() == 0
    review = pending.load(rd)
    assert review is not None and review["key"] == "brief"
    assert env.run("reply", "--id", review["id"], "--text", "дальше") == 0
    assert run_dfl() == 0
    run = ps.load(rd.name)
    assert run.step != "interview" and "20-design.md" in authored
    bundle = Path(run.target_dir) / run.bundle_dir
    expected = render_brief(brief_from_json(BRIEF))
    assert (bundle / FILENAMES["brief"]).read_text(encoding="utf-8") == expected
    capsys.readouterr()
