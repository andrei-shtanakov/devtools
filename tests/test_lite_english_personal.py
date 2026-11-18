"""Английский режим личного пути `dfl` (#769 срез 1).

Язык вызова до первого вывода: `--lang`, `DFL_LANG`, язык продолжаемого
прогона, `ru`; баннер, `dfl status` по прогонам, `dfl abandon`. Окружение —
фикстура `env` из `tests/test_lite_dfl.py`, `advance` — рекордер.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from governance import df_config, user_targets
from governance.author_launch import LaunchOutcome
from lite import dfl, i18n, pending
from lite.personal import flow
from lite.personal import state as ps
from tests.personal_en import EN_BRIEF_TURN, EN_DECOMPOSITION, EN_DESIGN, cyrillic
from tests.test_lite_dfl import REPO, SLUG, URL, Env, env  # noqa: F401
from tests.test_lite_dfl_personal import Advance, personal  # noqa: F401
from tests.test_lite_personal_flow import FakeOps

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
    run = _saved(env, language="en")
    assert env.run("run", "--lang", "ru") == 0
    captured = capsys.readouterr()
    [warning] = captured.err.strip().splitlines()
    assert run.run_id in warning and "--lang/DFL_LANG" in warning
    assert "language en" in warning and "ru from" in warning
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


# --- сценарии демо-набора 1, 2, 3, 7 (Task 6): экран личного пути ----------

#: дизайн после правки «drop D-02» — автор убрал пункт (вопрос «Remove …?»)
EN_DESIGN_NO_D02 = EN_DESIGN.split("#### D-02")[0].rstrip() + "\n"
ASK_TURN = '{"ask": "Who will use it?", "options": ["only me", "my family"]}'
BAD_TURN = '{"question": "no protocol"}'


@dataclass
class World:
    """Фейковые англоязычные агенты, терминал и эффекты одного теста."""

    reads: list[str | None] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)
    tty: bool = False
    texts: dict[str, list[str]] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)

    def read(self, prompt: str = "") -> str | None:
        return self.reads.pop(0) if self.reads else None

    def agent(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.turns.pop(0)

    def launch(self, call: object, prompt: object) -> LaunchOutcome:
        c: Any = call
        queue = self.texts[c.filename]
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        path = Path(c.target_dir) / c.bundle_dir / c.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return LaunchOutcome(0)

    def check(self, edge_id: str, *args: object, **kw: object) -> dict:
        return {"edge": edge_id, "verdict": "PASS", "findings": [], "subject": []}


@pytest.fixture()
def world(
    env: Env,  # noqa: F811
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    """`dfl.main` с настоящим `flow.advance`: агенты, автор, edge-check и
    форджа — фейки; терминал — `World.reads`/`World.tty`."""
    w = World(
        texts={"20-design.md": [EN_DESIGN], "30-decomposition.md": [EN_DECOMPOSITION]}
    )

    def advance(run: ps.PersonalRun) -> int:
        return flow.advance(
            run,
            FakeOps(run),
            read=w.read,
            agent=w.agent,
            launch=w.launch,
            check=w.check,
            tty=lambda: w.tty,
        )

    monkeypatch.setattr(dfl, "_advance_personal", advance)
    monkeypatch.setattr(dfl, "_track_tty", lambda: True)
    monkeypatch.setattr(dfl.ic, "stdin_read", w.read)
    monkeypatch.setattr(dfl, "_author_is_claude", lambda: True)
    monkeypatch.setattr(flow, "config_mode", lambda: "test")
    monkeypatch.delenv("DFL_LANG", raising=False)
    i18n.reset_misses()
    return w


def _screen(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def _status(e: Env, capsys: pytest.CaptureFixture[str]) -> str:
    """`dfl status` без баннера: заголовок — язык вызова (без флага — ru)."""
    assert e.run("status") == 0
    return _screen(capsys).split("\n\n", 1)[1]


def _clean(out: str, *allowed: str) -> None:
    """Экран без кирилицы (кроме `allowed`) и без промахов каталога."""
    assert cyrillic(out, allowed=allowed) == [], out
    assert i18n.misses() == frozenset()


def _new_en(e: Env, *extra: str) -> int:
    args = ("run", "--subject", SUBJECT, "--track", "personal", "--lang", "en")
    return e.run(*args, *extra)


def _active() -> ps.PersonalRun:
    run = ps.active_for(SLUG)
    assert run is not None
    return run


def _pending_id() -> str:
    rec = pending.load(ps.run_dir(_active().run_id))
    assert rec is not None
    return str(rec["id"])


def test_s1_new_run_track_interview_brief(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world.turns = [ASK_TURN, BAD_TURN, EN_BRIEF_TURN, EN_BRIEF_TURN]
    world.reads = ["1", "1", "2", "keep it local", "", "yes", "", None]
    world.tty = True
    args = ("run", "--subject", SUBJECT, "--lang", "en", "--review", "design")
    assert env.run(*args) == 0
    out = _screen(capsys)
    assert "What kind of project is this?" in out
    assert "Who will use it?" in out and "1. only me" in out
    assert "Correct? 1. yes · 2. fix: <what>" in out
    assert "What to fix?" in out
    assert "paid agent calls: 4" in out
    assert "stage “design” is ready and passed the check" in out
    assert "review of stage “design” interrupted — continue: dfl run" in out
    _clean(out)
    # повтор после ответа не по форме — с английской причиной
    assert 'expected the key "ask" or "brief"' in world.prompts[2]
    assert cyrillic("\n".join(world.prompts)) == []
    run = _active()
    assert run.language == "en" and run.step == "design"
    brief = (Path(run.target_dir) / run.bundle_dir / "00-brief.md").read_text()
    assert cyrillic(brief) == []


def test_s2_pause_continue_separate_call(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world.turns = [EN_BRIEF_TURN]
    world.reads = ["1"]
    assert _new_en(env, "--review", "design,decomposition") == 0
    out = _screen(capsys)
    assert "awaiting an answer: stage “design” is ready" in out
    _clean(out)
    # отдельный вызов: без флага и DFL_LANG — язык из прогона
    world.tty = True
    world.reads = ["next", None]
    assert env.run("run") == 0
    out = _screen(capsys)
    assert out.startswith("test mode:")
    assert "stage “decomposition” is ready and passed the check" in out
    assert "review of stage “decomposition” interrupted" in out
    _clean(out)
    assert _active().step == "decomposition"
    out = _status(env, capsys)
    assert "step decomposition — review of stage “decomposition”" in out
    _clean(out)


def test_s3_reply_remarks_and_remove_confirm(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world.turns = [EN_BRIEF_TURN]
    world.reads = ["1"]
    world.texts["20-design.md"] = [EN_DESIGN, EN_DESIGN_NO_D02]
    assert _new_en(env, "--review", "design") == 0
    capsys.readouterr()
    assert env.run("pending") == 0
    out = _screen(capsys)
    assert "accepted: “next” or your remarks in your own words" in out
    assert "link: file://" in out
    _clean(out)
    assert env.run("reply", "--id", _pending_id(), "--text", "drop D-02") == 0
    out = _screen(capsys)
    assert "answer recorded — continue: dfl run" in out
    _clean(out)
    assert env.run("run") == 0
    out = _screen(capsys)
    title = "Search — a line filter by substring"
    assert f"awaiting an answer: Remove D-02 — “{title}”? 1. remove · 2. keep" in out
    _clean(out, title)
    out = _status(env, capsys)
    assert "awaiting an answer: Remove D-02" in out
    _clean(out)
    assert env.run("pending") == 0
    out = _screen(capsys)
    assert "  1. remove" in out and "  2. keep" in out
    _clean(out)
    qid = _pending_id()
    assert env.run("reply", "--id", qid, "--text", "maybe") == 2
    out = _screen(capsys)
    assert "dfl reply: refusal (code 2)" in out
    assert "answer: 1/remove or 2/keep" in out
    _clean(out)
    assert env.run("reply", "--id", qid, "--text", "remove") == 0
    capsys.readouterr()
    assert env.run("run") == 0
    out = _screen(capsys)
    assert "awaiting an answer: stage “design” is ready" in out
    _clean(out)
    run = _active()
    design = (Path(run.target_dir) / run.bundle_dir / "20-design.md").read_text()
    assert design == EN_DESIGN_NO_D02


def test_s7_stop_and_refusal(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # штатная остановка интервью (/stop) — пауза, код 0
    world.turns = [ASK_TURN]
    world.reads = ["/stop"]
    assert _new_en(env, "--review", "brief") == 0
    out = _screen(capsys)
    assert "interview paused — continue: dfl run" in out
    _clean(out)
    # продолжение без терминала: пауза брифа ждёт ответа
    world.turns = [EN_BRIEF_TURN]
    world.reads = ["1", "yes", ""]
    assert env.run("run") == 0
    out = _screen(capsys)
    assert "awaiting an answer: stage “brief” is ready" in out
    _clean(out)
    # замечания к брифу через reply — отказ, код 2
    qid = _pending_id()
    assert env.run("reply", "--id", qid, "--text", "more goals") == 2
    out = _screen(capsys)
    assert "dfl reply: refusal (code 2)" in out
    assert "  reason:" in out and "  what to do:" in out
    _clean(out)
    # «дальше» принят; повторный reply без вопроса — отказ, код 2
    assert env.run("reply", "--id", qid, "--text", "next") == 0
    capsys.readouterr()
    world.texts["20-design.md"] = [EN_DESIGN]
    pending.clear(ps.run_dir(_active().run_id))
    assert env.run("reply", "--id", qid, "--text", "next") == 2
    out = _screen(capsys)
    assert "no question awaits an answer" in out
    _clean(out)


def test_s7_refusal_before_run_in_call_language(
    env: Env,  # noqa: F811
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(user_targets, "load", lambda: {})
    monkeypatch.setenv("DFL_LANG", "en")
    assert env.run("status") == 2
    out = _screen(capsys)
    assert "dfl status: refusal (code 2)" in out
    assert "is not registered" in out
    _clean(out, str(env.tmp))


def test_wait_line_en_starts_with_wait_prefix() -> None:
    """`manual_ask.is_wait` узнаёт английскую строку ожидания по префиксу."""
    from lite import manual_ask
    from lite.i18n_en import EN

    assert EN[pending.WAIT_LINE].startswith(EN[manual_ask.WAIT_PREFIX])
    assert pending.WAIT_LINE.startswith(manual_ask.WAIT_PREFIX)
    with i18n.language("en"):
        line = pending.wait_line(pending.Question("review", "design", "Look"))
    assert manual_ask.is_wait(line)
