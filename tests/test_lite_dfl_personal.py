"""`dfl run` / `dfl status` на личном пути (lite/dfl.py, Task 9).

Вопрос о типе проекта, `--track`, продолжение активного личного прогона
(RF-4) и строка `dfl status`. Оркестратор `advance` подменён рекордером;
окружение — фикстура `env` из `tests/test_lite_dfl.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from governance import user_targets
from lite import dfl
from lite.personal import flow
from lite.personal import state as ps
from tests.test_lite_dfl import REPO, SLUG, URL, Env, _run, env  # noqa: F401

SUBJECT = "Учёт расходов"


@dataclass
class Advance:
    """Рекордер `flow.advance`: прогоны, с которыми его вызвали."""

    runs: list[ps.PersonalRun] = field(default_factory=list)
    code: int = 0

    def __call__(self, run: ps.PersonalRun, ops: object, **kwargs: object) -> int:
        assert {"read", "agent", "launch", "check"} <= set(kwargs)
        self.runs.append(run)
        return self.code


@pytest.fixture()
def personal(env: Env, monkeypatch: pytest.MonkeyPatch) -> Advance:  # noqa: F811
    """Личные прогоны — рядом с `RUNS_ROOT` в `tmp`, `advance` — рекордер."""
    monkeypatch.setattr(ps, "PERSONAL_ROOT", env.tmp / "personal-runs")
    rec = Advance()
    monkeypatch.setattr(flow, "advance", rec)
    monkeypatch.setattr(dfl, "_track_tty", lambda: True)
    return rec


def _answers(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    queue = list(lines)
    monkeypatch.setattr(dfl.ic, "stdin_read", lambda prompt: queue.pop(0))


def _no_step(monkeypatch: pytest.MonkeyPatch) -> None:
    def step(*args: object, **kwargs: object) -> int:
        pytest.fail("enterprise-шаг на личном пути")

    monkeypatch.setattr(dfl, "_step", step)


def _active(e: Env, step: str = "design", reason: str = "") -> ps.PersonalRun:
    run = ps.new_personal_run(SUBJECT, REPO, SLUG, str(e.tmp / "target"), date.today())
    run.step, run.stop_reason = step, reason
    ps.save(run)
    return run


def test_track_question_personal_starts_personal(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _answers(monkeypatch, "1")
    _no_step(monkeypatch)
    assert env.run("run", "--subject", SUBJECT) == 0
    out = capsys.readouterr().out
    assert dfl.TRACK_QUESTION in out
    for n, option in enumerate(dfl.TRACK_OPTIONS, start=1):
        assert f"{n}. {option}" in out
    [run] = personal.runs
    assert (run.subject, run.repo, run.repo_slug) == (SUBJECT, REPO, SLUG)
    assert run.target_dir == str(env.tmp / "target")
    assert ps.load(run.run_id).subject == SUBJECT  # сохранён до advance
    assert env.spec_calls == []


def test_track_flag_skips_question(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(dfl.ic, "stdin_read", lambda p: pytest.fail("вопрос"))
    _no_step(monkeypatch)
    assert env.run("run", "--subject", SUBJECT, "--track", "personal") == 0
    assert dfl.TRACK_QUESTION not in capsys.readouterr().out
    assert len(personal.runs) == 1


def test_enterprise_answer_keeps_old_path(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answers(monkeypatch, "2")
    assert env.run("run", "--subject", SUBJECT) == 0
    assert personal.runs == []
    assert env.spec_calls[0][:4] == ["--subject", SUBJECT, "--repo", REPO]
    assert "--need" in env.spec_calls[0]


def test_enterprise_flag_and_no_terminal_skip_question(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dfl.ic, "stdin_read", lambda p: pytest.fail("вопрос"))
    assert env.run("run", "--subject", "t1", "--track", "enterprise") == 0
    monkeypatch.setattr(dfl, "_track_tty", lambda: False)
    _run("WS-1-a", "WS-1", "customer", "completed", subject="t1")
    assert env.run("run", "--subject", "t1") == 0  # тема есть — без вопроса
    assert personal.runs == [] and len(env.spec_calls) == 2


def test_wrong_number_asked_again_stop_word_stops(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _answers(monkeypatch, "7", "/стоп")
    _no_step(monkeypatch)
    assert env.run("run", "--subject", SUBJECT) == 0
    out = capsys.readouterr().out
    assert out.count(dfl.TRACK_QUESTION) == 2 and dfl.TRACK_RETRY in out
    assert dfl.TRACK_STOPPED in out
    assert personal.runs == [] and not (env.tmp / "personal-runs").exists()


def test_resume_personal_without_subject(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RF-4: незаконченный enterprise-прогон в `RUNS_ROOT` и активный личный —
    `dfl run` без `--subject` продолжает личный; список enterprise цел."""
    _run("WS-1-a", "WS-1", "customer", "waiting_interview", subject="t1")
    live = _active(env)
    _no_step(monkeypatch)
    assert env.run("run") == 0
    assert [r.run_id for r in personal.runs] == [live.run_id]
    assert env.spec_calls == []
    assert [s.run_id for s in dfl._readable_runs(REPO)] == ["WS-1-a"]


def test_same_subject_resumes_personal(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _active(env)
    monkeypatch.setattr(dfl.ic, "stdin_read", lambda p: pytest.fail("вопрос"))
    _no_step(monkeypatch)
    assert env.run("run", "--subject", SUBJECT) == 0
    assert [r.run_id for r in personal.runs] == [live.run_id]


def test_personal_stop_code_returned(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _active(env)
    personal.code = 1
    assert env.run("run") == 1


def test_no_personal_run_keeps_old_path(
    env: Env,  # noqa: F811
    personal: Advance,
) -> None:
    _run("WS-1-a", "WS-1", "customer", "running")
    assert env.run("run") == 0
    assert personal.runs == []
    assert env.spec_calls == [["--subject", "тема", "--repo", REPO,
                               "--run-id", "WS-1-a"]]  # fmt: skip


def test_status_shows_personal_step(
    env: Env,  # noqa: F811
    personal: Advance,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = (env.tmp / "target").resolve()
    monkeypatch.setattr(
        user_targets, "load", lambda: {REPO: user_targets.UserTarget(REPO, target, URL)}
    )
    _run("WS-1-a", "WS-1", "customer", "brief_ready", subject="t1")
    run = _active(env, step="decomposition", reason="декомпозиция нарушает инварианты")
    assert env.run("status") == 0
    out = capsys.readouterr().out
    assert f"личный прогон {run.run_id}: шаг decomposition" in out
    assert "декомпозиция нарушает инварианты" in out
    assert "WS-1-a" in out


def test_quiet_wraps_effects_under_capture(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Эффекты (git/gh, автор, проверка, агент) — под `runlog.captured`;
    разговор (`say`, `read`) — нет: вопросы остаются на экране."""
    seen: list[str] = []

    class Ctx:
        def __init__(self, log: object, level: int) -> None:
            pass

        def __enter__(self) -> None:
            seen.append("in")

        def __exit__(self, *exc: object) -> None:
            seen.append("out")

    monkeypatch.setattr(dfl.runlog, "captured", Ctx)

    class Fake:
        name = "x"

        def head_sha(self, target: str, branch: str) -> str:
            seen.append("call")
            return "abc"

    ops = dfl._QuietOps(Fake())
    assert ops.head_sha("/t", "b") == "abc" and ops.name == "x"
    assert dfl._quiet(lambda a, *, b: a + b)(1, b=2) == 3
    assert seen == ["in", "call", "out", "in", "out"]
