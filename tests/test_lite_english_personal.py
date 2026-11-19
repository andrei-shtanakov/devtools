"""Английский режим личного пути `dfl` (#769 срез 1).

Язык вызова до первого вывода: `--lang`, `DFL_LANG`, язык продолжаемого
прогона, `ru`; баннер, `dfl status` по прогонам, `dfl abandon`. Окружение —
фикстура `env` из `tests/test_lite_dfl.py`, `advance` — рекордер.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
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
from tests.test_lite_personal_flow import CODE_PR, FakeOps, ManualOps

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


# сценарий 6 демо-набора (смешанные прогоны в `status`)
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
EN_FINDINGS = {
    "design": "D-02 does not name the filter mechanism for FR-02",
    "decomposition": "DT-02 mixes reading the file and filtering",
}
BAD_TURN = '{"question": "no protocol"}'


@dataclass
class World:
    """Фейковые англоязычные агенты, терминал и эффекты одного теста."""

    reads: list[str | None] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)
    tty: bool = False
    texts: dict[str, list[str]] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)
    #: вердикты edge-check по узлу (`design`/`decomposition`), последний
    #: повторяется; FAIL — с одной английской находкой на файле узла
    verdicts: dict[str, list[str]] = field(default_factory=dict)
    #: промпты авторской границы (авторинг, правка по замечаниям/инвариантам)
    launched: list[str] = field(default_factory=list)
    ops: Callable[[ps.PersonalRun], Any] = FakeOps

    def read(self, prompt: str = "") -> str | None:
        return self.reads.pop(0) if self.reads else None

    def agent(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.turns.pop(0)

    def launch(self, call: object, prompt: object) -> LaunchOutcome:
        c: Any = call
        if callable(prompt):
            self.launched.append(prompt("/tmp/out.md"))
        queue = self.texts[c.filename]
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        path = Path(c.target_dir) / c.bundle_dir / c.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return LaunchOutcome(0)

    def check(self, edge_id: str, *args: object, **kw: object) -> dict:
        node = "decomposition" if "decomposition" in edge_id else "design"
        queue = self.verdicts.get(node) or ["PASS"]
        verdict = queue.pop(0) if len(queue) > 1 else queue[0]
        findings = []
        if verdict == "FAIL":
            path = {"design": "20-design.md", "decomposition": "30-decomposition.md"}
            location = {"path": path[node], "lines": "1-3"}
            findings = [
                {
                    "rule_id": "R1",
                    "class": "major",
                    "statement": EN_FINDINGS[node],
                    "location": location,
                }
            ]
        return {
            "edge": edge_id,
            "verdict": verdict,
            "findings": findings,
            "subject": [],
        }


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
            w.ops(run),
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
    # замечания к брифу через reply — записаны, код 0 (R6, #766 срез 4)
    qid = _pending_id()
    assert env.run("reply", "--id", qid, "--text", "more goals") == 0
    out = _screen(capsys)
    assert "answer recorded — continue: dfl run" in out
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


def test_no_unfinished_run_refusal_in_call_language(
    env: Env,  # noqa: F811
    world: World,
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Голый `dfl run` без прогонов: отказ на языке вызова, а не на языке
    enterprise-продолжения (живой прогон 18.11)."""
    monkeypatch.setenv("DFL_LANG", "en")
    assert env.run("run") == 2
    out = _screen(capsys)
    assert "dfl run: refusal (code 2)" in out
    assert "no unfinished run" in out
    assert "dfl run --subject '<subject>'" in out
    _clean(out, str(env.tmp))


def test_no_unfinished_run_refusal_ru_unchanged(
    env: Env,  # noqa: F811
    world: World,
    registered: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DFL_LANG", raising=False)
    assert env.run("run") == 2
    out = _screen(capsys)
    assert "незаконченного прогона нет" in out
    assert "первый запуск: dfl run --subject '<тема>'" in out


def test_wait_line_en_starts_with_wait_prefix() -> None:
    """`manual_ask.is_wait` узнаёт английскую строку ожидания по префиксу."""
    from lite import manual_ask
    from lite.i18n_en import EN

    assert EN[pending.WAIT_LINE].startswith(EN[manual_ask.WAIT_PREFIX])
    assert pending.WAIT_LINE.startswith(manual_ask.WAIT_PREFIX)
    with i18n.language("en"):
        line = pending.wait_line(pending.Question("review", "design", "Look"))
    assert manual_ask.is_wait(line)


# --- сценарии 3-edge, 4, 5 и фаза кода (Task 7) ----------------------------


def _triage(question: str, *labels: str) -> str:
    """Ответ сортировки: одна находка — вопрос по существу с вариантами."""
    options = [
        {"label": label, "recommended": no == 0, "removes": [], "downgrades": []}
        for no, label in enumerate(labels)
    ]
    item = {"finding": 1, "kind": "decision", "question": question, "options": options}
    return json.dumps({"items": [item]})


DESIGN_TRIAGE = _triage(
    "Name the filter mechanism in D-02?",
    "name a substring filter",
    "keep D-02 as is",
)
DECOMP_TRIAGE = _triage(
    "How to split DT-02?", "split reading and filtering", "keep DT-02 whole"
)
#: декомпозиция с нарушением графа: DT-02 зависит от несуществующего DT-03
EN_DECOMPOSITION_BAD = EN_DECOMPOSITION.replace(
    "depends_on: [DT-01]", "depends_on: [DT-01, DT-03]"
)


def test_s3_edge_question_via_reply(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world.turns = [EN_BRIEF_TURN, DESIGN_TRIAGE]
    world.reads = ["1"]
    world.verdicts = {"design": ["FAIL", "PASS"]}
    assert _new_en(env, "--review", "design") == 0
    out = _screen(capsys)
    assert "I will fix myself: 0; questions for you: up to 1" in out
    assert "awaiting an answer: finding R1 major (20-design.md:1-3)" in out
    _clean(out)
    assert env.run("pending") == 0
    out = _screen(capsys)
    assert f"finding R1 major (20-design.md:1-3): {EN_FINDINGS['design']}" in out
    assert "Name the filter mechanism in D-02?" in out
    assert "name a substring filter (recommended)" in out
    _clean(out)
    reason = "disagree: D-02 already says it filters by substring"
    assert env.run("reply", "--id", _pending_id(), "--text", reason) == 0
    _clean(_screen(capsys))
    assert env.run("run") == 0
    out = _screen(capsys)
    assert "node rewritten (20-design.md): findings 1; checking again" in out
    assert "awaiting an answer: stage “design” is ready" in out
    _clean(out)
    [rewrite] = world.launched[1:]  # после авторинга дизайна — правка
    assert "→ disagree: D-02 already says it filters by substring" in rewrite
    assert cyrillic("\n".join(world.prompts + world.launched)) == []


def test_s4_edge_resolution_terminal(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world.tty = True
    world.turns = [EN_BRIEF_TURN, DESIGN_TRIAGE, DECOMP_TRIAGE]
    world.reads = ["1", "1", "own: read once, then filter", ""]
    world.verdicts = {"design": ["FAIL", "PASS"], "decomposition": ["FAIL", "PASS"]}
    world.texts["30-decomposition.md"] = [
        EN_DECOMPOSITION_BAD,  # автор
        EN_DECOMPOSITION_BAD,  # правка по замечанию edge-check
        EN_DECOMPOSITION,  # правка инвариантов
    ]
    assert _new_en(env, "--review", "decomposition") == 0
    out = _screen(capsys)
    for finding in EN_FINDINGS.values():
        assert finding in out
    assert "  1. name a substring filter (recommended)" in out
    assert "  2. keep D-02 as is" in out
    assert "node rewritten (30-decomposition.md): findings 1; checking again" in out
    assert "review of stage “decomposition” interrupted" in out
    _clean(out)
    _author, design_fix, _decomp, decomp_fix, invariant_fix = world.launched
    assert "→ name a substring filter" in design_fix
    assert "→ own: read once, then filter" in decomp_fix
    assert "DT-02: reference to a non-existent DT-03" in invariant_fix
    assert cyrillic("\n".join(world.prompts + world.launched)) == []
    assert _active().step == "decomposition"


def test_decomposition_guard_findings_en() -> None:
    """Находки графа и формы DT — на языке прогона; без `lang` — русские."""
    from governance import decomposition_guard as dg

    checks, whole = EN_DECOMPOSITION_BAD.split("## Tasks")[0], EN_DECOMPOSITION_BAD
    broken = (
        whole + "\n#### DT-3a: broken\n#### DT-02: dup · type: implement · owner: dev\n"
    )
    en = dg.graph_findings(checks, broken, lang="en")
    ru = dg.graph_findings(checks, broken)
    assert len(en) == len(ru) >= 3
    assert cyrillic("\n".join(en)) == []
    assert "DT-02: reference to a non-existent DT-03" in en
    assert "DT-02: ссылка на несуществующий DT-03" in ru


def test_decomposition_guard_catalog_complete() -> None:
    """Каждый шаблон `_finding(lang, "<шаблон>", …)` есть в английском
    каталоге гарда, плейсхолдеры совпадают."""
    import ast
    import string

    from governance import decomposition_guard as dg

    tree = ast.parse(Path(dg.__file__).read_text(encoding="utf-8"))
    templates = [
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_finding"
        and isinstance(node.args[1], ast.Constant)
    ]
    assert len(templates) >= 30
    names = string.Formatter()
    for ru in templates:
        assert ru in dg._FINDINGS_EN, ru
        en = dg._FINDINGS_EN[ru]
        assert cyrillic(en) == [], en
        assert {f for _, f, _, _ in names.parse(ru) if f} == {
            f for _, f, _, _ in names.parse(en) if f
        }


#: декомпозиция с ручной проверкой (как `MANUAL_DECOMP` русских тестов)
EN_MANUAL_DECOMP = """#### BEH-01: reliability threshold ≥ 9/10
- **checked_by**: `kind: manual` `target: docs/reliability.md`

#### DT-01: Reliability · type: verify · owner: qa
scenarios: [BEH-01]
depends_on: []
parallel_group: solo
"""


def _code_pr_run(e: Env) -> ps.PersonalRun:
    run = _saved(e, language="en", step="code-pr")
    bundle = Path(run.target_dir) / run.bundle_dir
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "30-decomposition.md").write_text(EN_MANUAL_DECOMP, encoding="utf-8")
    run.manual_checks, run.code_pr = True, CODE_PR
    ps.save(run)
    return run


def test_s5_manual_check(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _code_pr_run(env)
    published: list[str] = []

    class Ops(ManualOps):
        def create_issue(self, slug, title, body, **kw):
            published.append(f"{title}\n{body}")
            return 24

        def comment(self, slug, pr, body):
            published.append(body)

    world.ops = Ops
    assert env.run("run") == 0
    out = _screen(capsys)
    assert "awaiting an answer: Manual check BEH-01" in out
    _clean(out, "≥")
    assert env.run("pending") == 0
    out = _screen(capsys)
    assert f"check the code from PR #{CODE_PR}: gh pr checkout {CODE_PR}" in out
    _clean(out)
    assert env.run("reply", "--id", _pending_id(), "--text", "passed with caveat") == 2
    out = _screen(capsys)
    assert "dfl reply: refusal (code 2)" in out
    _clean(out)
    caveat = "passed with caveat: slow on the first run"
    assert env.run("reply", "--id", _pending_id(), "--text", caveat) == 0
    _clean(_screen(capsys))

    class Merged(Ops):
        def __init__(self, r: ps.PersonalRun) -> None:
            super().__init__(r)
            self.code_state = "MERGED"

    world.ops = Merged
    assert env.run("run") == 0
    out = _screen(capsys)
    assert f"integration PR #{CODE_PR} merged — the pipeline is finished; " in out
    _clean(out)
    assert published and cyrillic("\n".join(published)) == []
    assert "slow on the first run" in "\n".join(published)
    assert ps.load(run.run_id).step == "done"


def test_manual_terminal_prompts_en(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """В терминале: подсказка про код PR, вопрос, «текст оговорки»."""
    run = _code_pr_run(env)
    world.ops = ManualOps
    world.tty = True
    world.reads = ["passed with caveat", "slow on the first run", "", None]
    assert env.run("run") == 0
    out = _screen(capsys)
    assert f"check the code from PR #{CODE_PR}" in out
    assert "caveat text:" in out
    slug = run.repo_slug
    assert f"merge https://github.com/{slug}/pull/{CODE_PR} — then dfl run" in out
    _clean(out, "≥")


def test_code_run_progress_en(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Фаза кода: подсказка TUI, итог прогресса, плашка возобновления и стоп."""
    run = _saved(env, language="en", step="code-run")

    class Partial(FakeOps):
        def __init__(self, r: ps.PersonalRun) -> None:
            super().__init__(r)
            self.tasks_md = (
                "### TASK-001: a\nP1 | ✅ DONE\n\n### TASK-002: b\nP1 | ⬜ TODO\n"
            )

    world.ops = Partial
    assert env.run("run") == 1  # стоп: продолжить ветку нельзя
    out = _screen(capsys)
    assert "spec-loop (test): task implementation started" in out
    assert "implementation: 0 of 0 done · 00:00" in out
    assert f"integration PR #{CODE_PR} opened" in out
    assert f"▶ continuing project “{SUBJECT}” · {run.repo_slug}" in out
    assert "branch spec-runner/run-1 · 1 of 2 tasks done" in out
    assert "cannot be continued yet" in out
    _clean(out)
    assert ps.load(run.run_id).step == "code-run"


def test_progress_lines_en() -> None:
    """Строки прогресса и плашки на английском; русские — прежние."""
    from governance import run_progress as rpg
    from governance import run_resume as rr

    lines: list[str] = []
    live: list[str] = []
    watcher = rpg.Watcher(
        Path("/nonexistent/state.db"),
        [("TASK-001", "a")],
        emit=lines.append,
        live=live.append,
        lang="en",
    )
    watcher.begin(0.0)
    watcher.tick(rpg.UNAVAILABLE_AFTER + 1)
    lines.append(watcher.summary(61.0))
    for kind in ("started", "done", "retry", "failed"):
        lines.append(
            rpg.event_line(kind, "TASK-001", "a", seconds=5, attempt=2, lang="en")
        )
    lines.append(rpg.live_line("TASK-001", 1, 2, 3, lang="en"))
    lines.append(rr.banner("s", "o/r", "b", None, lang="en"))
    lines.append(rr.unread_stop("b", 7, lang="en"))
    lines.append(rr.several_stop([7, 8], lang="en"))
    text = "\n".join(lines)
    assert "progress unavailable: no /nonexistent/state.db" in text
    assert "implementation: 0 of 1 done · 01:01" in text
    assert "✗ TASK-001 failed: reason not recorded" in text
    assert " · 1 of 2 · " in text
    assert cyrillic(text) == []
    assert rpg.event_line("started", "T", "a") == "▶ T «a» — начата"
    assert rr.banner("s", "o/r", "b", (1, 2)).endswith("сделано 1 из 2 задач")


def test_thinking_line_in_call_language(monkeypatch: pytest.MonkeyPatch) -> None:
    from lite import structure

    seen: list[str] = []

    def thinking(call: Callable[[], Any], *, line: str) -> Any:
        seen.append(line)
        return type("Done", (), {"returncode": 1, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(structure.run_progress, "thinking", thinking)
    i18n.reset_misses()
    with i18n.language("en"), pytest.raises(structure.AgentUnavailable) as exc:
        structure.claude_agent("m")("prompt")
    assert seen == ["… thinking · {elapsed}"]
    assert str(exc.value) == "code 1"
    assert i18n.misses() == frozenset()


def test_lock_busy_en(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from governance import run_lock

    run = _saved(env, language="en")
    rd = ps.run_dir(run.run_id)
    pending.asker(rd)(pending.Question("review", "design", "Look"))
    with run_lock.run_lock(run.run_id):
        assert env.run("reply", "--id", _pending_id(), "--text", "next") == 2
    out = _screen(capsys)
    assert f"run {run.run_id} is being executed by another process" in out
    _clean(out)


def test_test_mode_only_refusal_en(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.test_lite_dfl import _STRICT_GH_DIRS, write_config

    write_config(env.tmp / "xdg", mode="strict", **_STRICT_GH_DIRS)
    monkeypatch.setenv("DFL_LANG", "en")
    i18n.reset_misses()
    assert env.run("run", "--subject", SUBJECT) == 2
    out = _screen(capsys)
    assert "dfl run: refusal (code 2)" in out
    assert "dfl works only in test mode (mode = 'strict')" in out
    _clean(out, str(env.tmp))


def test_edge_stop_reasons_en() -> None:
    """Стопы edge-check без разбора (нет находок; находки на основании)."""
    from lite.edge_journal import Finding
    from lite.personal.check import NodeCheck

    brief = Finding(
        "personal-design", "e", "R2", "major", "goal G-02 vague", "00-brief.md", "3"
    )
    on_node = Finding("personal-design", "e", "R1", "major", "x", "20-design.md", "1")
    i18n.reset_misses()
    with i18n.language("en"):
        unmet = flow._no_findings_reason("20-design.md", NodeCheck("FAIL", [], ""))
        foreign = flow._foreign_findings_reason("30-decomposition.md", [brief, on_node])
    assert "verdict FAIL without blocking findings" in unmet
    assert "the record names no criteria" in unmet
    assert "00-brief.md was confirmed in the conversation" in foreign
    assert "20-design.md can be fixed by hand" in foreign
    assert "dfl abandon" in foreign and "dfl abandon" in unmet
    _clean(unmet + "\n" + foreign)


def _fresh_scenario(e: Env, w: World) -> None:  # noqa: F811
    """Чистый старт сценария: без прогонов, пакета и очередей фейкового мира."""
    shutil.rmtree(ps.personal_root(), ignore_errors=True)
    shutil.rmtree(e.tmp / "target", ignore_errors=True)
    (e.tmp / "target").mkdir()
    w.reads, w.turns, w.launched, w.prompts = [], [], [], []
    w.verdicts, w.tty, w.ops = {}, False, FakeOps
    w.texts = {
        "20-design.md": [EN_DESIGN],
        "30-decomposition.md": [EN_DECOMPOSITION],
    }


def test_all_scenarios_no_misses(
    env: Env,  # noqa: F811
    world: World,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Сценарии 1-7 подряд в одном процессе: промахов каталога нет."""
    i18n.reset_misses()
    scenarios = (
        test_s1_new_run_track_interview_brief,
        test_s2_pause_continue_separate_call,
        test_s3_reply_remarks_and_remove_confirm,
        test_s3_edge_question_via_reply,
        test_s4_edge_resolution_terminal,
        test_s5_manual_check,
        test_s7_stop_and_refusal,
    )
    for scenario in scenarios:
        _fresh_scenario(env, world)
        scenario(env, world, capsys)
        i18n.set_language("ru")
    assert i18n.misses() == frozenset()


def test_active_run_language_pinned_when_continued_run_missed(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Репо не в targets.toml (клон под другим именем): `_continued_run`
    прогон не находит, язык вызова — `ru`; `_personal` находит английский
    прогон по slug и исполняет его на языке прогона (ревью #809)."""
    monkeypatch.delenv("DFL_LANG", raising=False)
    run = _saved(env, language="en")
    seen: list[str] = []

    def advance(r: ps.PersonalRun) -> int:
        seen.append(i18n.current())
        return 0

    monkeypatch.setattr(dfl, "_advance_personal", advance)
    assert env.run("run") == 0
    assert ps.load(run.run_id).language == "en"
    assert seen == ["en"]
