"""`dfl run --review` (#768, Task 3): выбор этапов с паузой на просмотр.

Флаг сильнее конфига; выбор пишется в состояние прогона при старте
(личный — `PersonalRun.review_pause`, enterprise — `--review-pause` в argv
`spec_loop` → `RunState.review_pause`) и на возобновлении не меняется.
Окружение — фикстуры `env` (`tests/test_lite_dfl.py`) и `personal`
(`tests/test_lite_dfl_personal.py`).
"""

from __future__ import annotations

from typing import Any

import pytest

from governance import brief_tools as bt
from governance import df_config, runner, spec_loop
from governance import run_state as rs
from lite import dfl
from lite import interview_chat as ic
from lite import review_pause as rp
from lite.personal import state as ps
from tests.test_governance_spec_loop import _LoopEnv
from tests.test_lite_dfl import Env, _approved_file, _run, env  # noqa: F401
from tests.test_lite_dfl_personal import (  # noqa: F401
    SUBJECT,
    Advance,
    _active,
    _no_step,
    personal,
)


class _WithReview:
    """Конфиг DarkFactory с полем `review_pause_after` (поле — PR #775)."""

    def __init__(self, cfg: object, names: tuple[str, ...]) -> None:
        self._cfg = cfg
        self.review_pause_after = names

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cfg, name)


def _config_review(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    real = df_config.load
    monkeypatch.setattr(
        df_config, "load", lambda *a, **k: _WithReview(real(*a, **k), names)
    )


# --- resolve_review: флаг сильнее конфига ---


def test_flag_overrides_config(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _config_review(monkeypatch, "brief", "design")
    stages = rp.PERSONAL_STAGES
    assert dfl.resolve_review("decomposition", stages) == ("decomposition",)
    assert dfl.resolve_review("none", stages) == ()
    assert dfl.resolve_review("all", stages) == stages


def test_config_default_used(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """RF-5: имя конфига, чужое пути (`charter` на личном), молча не действует."""
    _config_review(monkeypatch, "charter", "design")
    assert dfl.resolve_review(None, rp.PERSONAL_STAGES) == ("design",)
    assert dfl.resolve_review(None, rp.ENTERPRISE_STAGES) == ("charter", "design")
    _config_review(monkeypatch, "design", "charter", "design")
    assert dfl.resolve_review(None, rp.ENTERPRISE_STAGES) == ("charter", "design")


def test_config_without_field_means_no_pause(env: Env) -> None:  # noqa: F811
    assert dfl.resolve_review(None, rp.PERSONAL_STAGES) == ()


def test_flag_unknown_stage_refused(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RF-5: имя флага, неизвестное пути, — отказ с перечнем допустимых."""
    _no_step(monkeypatch)
    code = env.run(
        "run", "--subject", SUBJECT, "--track", "personal", "--review", "charter"
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "charter" in err and "brief, design, decomposition" in err
    assert personal.runs == [] and not (env.tmp / "personal-runs").exists()


# --- личный путь ---


def test_personal_run_stores_review(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_step(monkeypatch)
    code = env.run(
        "run", "--subject", SUBJECT, "--track", "personal", "--review", "design,brief"
    )
    assert code == 0
    [run] = personal.runs
    assert run.review_pause == ("brief", "design")  # порядок этапов пути
    loaded = ps.load(run.run_id)
    assert loaded.review_pause == ("brief", "design")
    raw = (ps.run_dir(run.run_id) / "run.json").read_text(encoding="utf-8")
    assert '"review_pause": [' in raw


def test_personal_run_takes_config_default(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config_review(monkeypatch, "decomposition", "charter")
    _no_step(monkeypatch)
    assert env.run("run", "--subject", SUBJECT, "--track", "personal") == 0
    assert personal.runs[0].review_pause == ("decomposition",)


def test_personal_old_run_json_without_field_loads(
    env: Env,  # noqa: F811
) -> None:
    run = _active(env)
    path = ps.run_dir(run.run_id) / "run.json"
    text = path.read_text(encoding="utf-8").replace('"review_pause": [],', "")
    path.write_text(text, encoding="utf-8")
    assert "review_pause" not in text
    assert ps.load(run.run_id).review_pause == ()


def test_resume_with_different_review_refused(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _active(env)
    run.review_pause = ("design",)
    ps.save(run)
    _no_step(monkeypatch)
    assert env.run("run", "--review", "brief") == 2
    err = capsys.readouterr().err
    assert "design" in err and "dfl run без --review" in err
    assert personal.runs == []
    assert env.run("run", "--review", "design") == 0  # совпадает — продолжение
    assert env.run("run") == 0  # без флага — продолжение
    assert len(personal.runs) == 2


# --- enterprise ---


def test_enterprise_argv_carries_review_pause(env: Env) -> None:  # noqa: F811
    code = env.run(
        "run", "--subject", "t1", "--track", "enterprise", "--review", "design"
    )
    assert code == 0
    [argv] = env.spec_calls
    i = argv.index("--review-pause")
    assert argv[i + 1] == "design"


def test_enterprise_without_pause_argv_unchanged(env: Env) -> None:  # noqa: F811
    assert env.run("run", "--subject", "t1", "--track", "enterprise") == 0
    assert "--review-pause" not in env.spec_calls[0]


def test_enterprise_unknown_stage_refused(env: Env, capsys) -> None:  # noqa: F811
    code = env.run(
        "run", "--subject", "t1", "--track", "enterprise", "--review", "brief"
    )
    assert code == 2
    assert "charter, requirements" in capsys.readouterr().err
    assert env.spec_calls == []


def test_enterprise_engineer_inherits_customer_choice(env: Env) -> None:  # noqa: F811
    cust = _run("WS-1-a", "WS-1", "customer", "brief_ready")
    cust.review_pause = ["design", "acceptance"]
    rs.save(cust)
    _approved_file("WS-1-a")
    env.forge.branches["brief/WS-1"] = [5]
    env.forge.states[5] = "MERGED"
    assert env.run("run") == 0
    assert env.spec_calls == [
        [*bt.engineer_args(cust, 5, "customer"), "--review-pause", "design,acceptance"]
    ]


def test_enterprise_resume_with_different_review_refused(
    env: Env,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    cust = _run("WS-1-a", "WS-1", "customer", "running")
    cust.review_pause = ["design"]
    rs.save(cust)
    assert env.run("run", "--review", "charter") == 2
    assert "dfl run без --review" in capsys.readouterr().err
    assert env.spec_calls == []
    assert env.run("run", "--review", "design") == 0
    assert "--review-pause" not in env.spec_calls[0]  # resume не несёт флаг


# --- spec_loop ---


def test_spec_loop_passes_review_pause_to_start(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    loop = _LoopEnv(monkeypatch, tmp_path)
    argv = ["--subject", "S", "--repo", "alpha", "--review-pause", "design,charter"]
    assert spec_loop.main(argv) == 0
    assert loop.calls[0][1]["review_pause"] == ("design", "charter")


def _waiting(state: rs.RunState, ops: object, *, lock: object) -> rs.RunState:
    """`advance`-заглушка: прогон ждёт интервью (кнопка выходит с кодом 0)."""
    state.status = "waiting_interview"
    rs.save(state)
    return state


def test_spec_loop_direct_run_has_no_pause(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RF-4: прогон через `spec_loop` без `--review-pause` (настоящий
    `runner.start`) — `review_pause == []`, пауз нет."""
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    real_start = spec_loop.runner.start
    _LoopEnv(monkeypatch, tmp_path)
    monkeypatch.setattr(spec_loop.runner, "start", real_start)
    monkeypatch.setattr(runner, "_HALT_GATE", lambda slug: None)
    monkeypatch.setattr(runner, "advance", _waiting)
    assert spec_loop.main(["--subject", "S", "--repo", "alpha"]) == 0
    [run_id] = rs.all_run_ids()
    assert rs.load(run_id).review_pause == []


def test_resume_same_stages_other_order_ok(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _active(env)
    run.review_pause = ("brief", "design")
    ps.save(run)
    _no_step(monkeypatch)
    assert env.run("run", "--review", "design,brief") == 0
    assert env.run("run", "--review", "design, brief,design") == 0
    assert len(personal.runs) == 2


def test_duplicate_stage_recorded_once(
    env: Env,  # noqa: F811
    personal: Advance,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_step(monkeypatch)
    code = env.run(
        "run", "--subject", SUBJECT, "--track", "personal", "--review", "design,design"
    )
    assert code == 0
    assert ps.load(personal.runs[0].run_id).review_pause == ("design",)


def test_spec_loop_review_pause_on_resume_refused(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    loop = _LoopEnv(monkeypatch, tmp_path)
    state = rs.new_run(
        mode="test",
        subject="S",
        repo="alpha",
        repo_slug="owner/alpha",
        ws_id="WS-R",
        target_dir=str(loop.target),
        bundle_dir="workstreams/WS-R/spec",
        profile="profiles/team-exp.yaml",
        run_id="WS-R-a",
        review_pause=("design",),
    )
    rs.save(state)
    argv = ["--subject", "S", "--repo", "alpha", "--review-pause", "design"]
    assert spec_loop.main(argv) == 1
    out = capsys.readouterr().out
    assert "--review-pause задаётся только новому прогону" in out
    assert loop.calls == []  # ни start, ни resume


def test_chatting_with_review_resumes_without_refusal(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Разговорный режим: `_step` зовётся дважды с тем же `--review` —
    второй вызов находит записанный выбор и не отказывает."""
    monkeypatch.setattr(dfl, "_is_tty", lambda: True)
    monkeypatch.setattr(dfl, "_author_is_claude", lambda: True)
    monkeypatch.setattr(dfl, "_track_tty", lambda: False)

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if not rs.all_run_ids():
            pause = argv[argv.index("--review-pause") + 1].split(",")
            cust = _run("WS-1-a", "WS-1", "customer", "waiting_interview")
            cust.review_pause = pause
            rs.save(cust)
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    waits = iter([True])
    monkeypatch.setattr(
        dfl,
        "_waiting_run",
        lambda repo, subject, incomplete=False: (
            rs.load("WS-1-a") if next(waits, False) else None
        ),
    )
    monkeypatch.setattr(dfl, "_converse", lambda state: ic.ChatResult.FINISHED)
    code = env.run("run", "--subject", "тема", "--review", "acceptance,design")
    assert code == 0
    assert len(env.spec_calls) == 2
    assert env.spec_calls[0][-2:] == ["--review-pause", "design,acceptance"]
    assert "--review-pause" not in env.spec_calls[1]
