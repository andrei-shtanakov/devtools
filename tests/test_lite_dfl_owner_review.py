"""`dfl run` на паузе просмотра enterprise-волны (#768, Task 6).

Раннер встал `WAITING_OWNER_REVIEW` после PASS edge-check волны; `dfl`
показывает ссылки на выбранные узлы, читает замечания, автор переписывает,
затем `dfl` пишет op `review-pause-<w>` (`completed`, `revised`) и делает шаг —
раннер продолжает (без правок — без перепроверки, RF-3). Окружение —
фикстура `env` (`tests/test_lite_dfl.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import run_state as rs
from governance import runner, spec_loop
from governance.approve_node import _candidate_body
from governance.author_launch import AuthorCall, LaunchOutcome
from lite import dfl
from lite import interview_chat as ic
from lite import review_pause as rp
from tests.test_lite_dfl import (  # noqa: F401
    REPO,
    Env,
    _bind_streams,
    _read,
    _run,
    _tty,
    env,
)
from tests.test_lite_personal_flow import NODE_TEXT

RUN_ID = "WS-1-a"
DESIGN = NODE_TEXT["20-design.md"]
NO_D02 = DESIGN.split("#### D-02")[0]
ACCEPTANCE = "# Приёмка\n\nкритерии приёмки\n"
PAUSE_KEY = "review-pause-4"
RESUME_ARGV = ["--subject", "t", "--repo", REPO, "--run-id", RUN_ID]


def _paused(env: Env, pause: tuple[str, ...] = ("design", "acceptance")) -> None:  # noqa: F811
    """Прогон волны 4 (design + acceptance) на паузе просмотра."""
    state = _run(RUN_ID, "WS-1", "customer", runner.WAITING_OWNER_REVIEW, subject="t")
    state.wave = 4
    state.review_pause = list(pause)
    state.target_dir = str(env.tmp / "target")
    state.ops["edge-4"] = {"status": "completed", "verdict": "PASS"}
    rs.save(state)
    bundle = env.tmp / "target" / state.bundle_dir
    bundle.mkdir(parents=True, exist_ok=True)
    if not (bundle / "20-design.md").exists():
        (bundle / "20-design.md").write_text(DESIGN, encoding="utf-8")
        (bundle / "25-acceptance.md").write_text(ACCEPTANCE, encoding="utf-8")


def _design(env: Env) -> Path:  # noqa: F811
    return env.tmp / "target" / "workstreams/WS-1/spec/20-design.md"


def _steps(env: Env, monkeypatch: pytest.MonkeyPatch) -> list[dict | None]:  # noqa: F811
    """Первый шаг ставит паузу; пока пауза не пройдена, шаг ничего не меняет
    (как `spec_loop._dispatch`); пройдена — «раннер продолжил» (`running`),
    запомнив op паузы, с которым его позвали."""
    seen: list[dict | None] = []

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if not (rs.run_dir(RUN_ID) / "run.json").exists():
            _paused(env)
            return 0
        state = rs.load(RUN_ID)
        if not runner.review_pause_passed(state):
            return 0
        seen.append(state.ops.get(PAUSE_KEY))
        state.status = "running"
        rs.save(state)
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    return seen


def _lines(monkeypatch: pytest.MonkeyPatch, *lines: str) -> list[str]:
    """Терминал: строки по порядку, затем EOF; возвращает приглашения."""
    queue = list(lines)
    prompts: list[str] = []

    def read(prompt: str) -> str | None:
        prompts.append(prompt)
        return queue.pop(0) if queue else None

    monkeypatch.setattr(ic, "stdin_read", read)
    return queue


def _author(monkeypatch: pytest.MonkeyPatch, *texts: str) -> list[tuple]:
    """Автор: каждый вызов пишет следующий текст в файл узла."""
    queue = list(texts)
    calls: list[tuple] = []

    def revise(
        call: AuthorCall,
        kind: str,
        subject: str,
        current: str,
        findings: str,
        decisions: str,
    ) -> LaunchOutcome:
        calls.append((call.filename, kind, findings, decisions))
        path = Path(call.target_dir) / call.bundle_dir / call.filename
        path.write_text(queue.pop(0), encoding="utf-8")
        return LaunchOutcome(0)

    monkeypatch.setattr(dfl, "_revise", revise)
    return calls


def _setup(env: Env, monkeypatch: pytest.MonkeyPatch, *lines: str) -> list:  # noqa: F811
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    _lines(monkeypatch, *lines)
    return _steps(env, monkeypatch)


def test_waiting_owner_review_shows_wave_links(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """W4: оба выбранных файла показываются подряд, по одному."""
    _setup(env, monkeypatch, "дальше", "дальше")
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    bundle = (env.tmp / "target" / "workstreams/WS-1/spec").resolve()
    design = out.index("этап «дизайн» готов и прошёл проверку")
    acceptance = out.index("этап «приёмка» готов и прошёл проверку")
    assert design < out.index(f"file://{bundle / '20-design.md'}") < acceptance
    assert acceptance < out.index(f"file://{bundle / '25-acceptance.md'}")


def test_done_marks_op_not_revised_and_resumes(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """RF-3: «дальше» без замечаний — op `revised=False`, шаг (resume), автора нет."""
    seen = _setup(env, monkeypatch, "дальше", "дальше")
    calls = _author(monkeypatch)
    assert env.run("run", "--subject", "t") == 0
    assert seen == [{"status": "completed", "revised": False}]
    assert env.spec_calls[1] == RESUME_ARGV
    assert calls == []
    assert not (rs.run_dir(RUN_ID) / rp.REMARKS).exists()


def test_only_selected_node_shown(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    _lines(monkeypatch, "дальше")

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if len(env.spec_calls) == 1:
            _paused(env, ("acceptance",))
            return 0
        state = rs.load(RUN_ID)
        state.status = "running"
        rs.save(state)
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "«приёмка»" in out and "«дизайн»" not in out


def test_remark_revises_marks_revised(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    seen = _setup(env, monkeypatch, "уточнить D-01", "", "дальше")
    calls = _author(monkeypatch, DESIGN + "\nуточнено\n")
    assert env.run("run", "--subject", "t") == 0
    assert calls == [
        ("20-design.md", "design", "нет", "- замечание владельца: уточнить D-01")
    ]
    assert _design(env).read_text(encoding="utf-8").endswith("уточнено\n")
    assert seen == [{"status": "completed", "revised": True}]
    [remark] = rp.all_remarks(rs.run_dir(RUN_ID))
    assert remark.status == rp.APPLIED and remark.stage == "design"
    out, _ = _read(capfd)
    assert "переписывание 1" in out


def test_pending_applied_on_rerun(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """RF-1: недоигранное замечание применяется без вопроса (экран дизайна
    не показывается), затем пауза приёмки — как обычно."""
    seen = _setup(env, monkeypatch, "дальше")
    calls = _author(monkeypatch, DESIGN + "\nпо старому замечанию\n")
    _paused(env)  # прошлый `dfl run` встал на паузе и записал замечание
    rp.record(rs.run_dir(RUN_ID), "design", "старое замечание")
    assert env.run("run", "--subject", "t") == 0
    assert [c[3] for c in calls] == ["- замечание владельца: старое замечание"]
    out, _ = _read(capfd)
    assert "этап «дизайн» готов" not in out
    assert "этап «приёмка» готов" in out
    assert rp.pending(rs.run_dir(RUN_ID), "design") == []
    assert seen == [{"status": "completed", "revised": True}]


@pytest.mark.parametrize(
    ("answer", "final", "revisions"),
    [("1", NO_D02, 1), ("2", DESIGN + "\nбез удаления\n", 2)],
    ids=["yes", "no"],
)
def test_enterprise_removal_confirmed(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    answer: str,
    final: str,
    revisions: int,
) -> None:
    """RF-2: правка удаляет D-02 — вопрос; «да» — принято, «нет» — файл
    возвращён и один повтор с «D-02 не удалять»."""
    seen = _setup(env, monkeypatch, "убрать D-02", "", answer, "дальше")
    calls = _author(monkeypatch, NO_D02, DESIGN + "\nбез удаления\n")
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "удалить D-02? 1. да · 2. нет" in out
    assert _design(env).read_text(encoding="utf-8") == final
    assert len(calls) == revisions
    if revisions == 2:
        assert "- D-02 не удалять" in calls[1][3]
    assert seen == [{"status": "completed", "revised": True}]


def test_removal_still_unclear_drops_remark(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    seen = _setup(env, monkeypatch, "убрать D-02", "", "2")
    _author(monkeypatch, NO_D02, NO_D02)
    assert env.run("run", "--subject", "t") == 1
    out, _ = _read(capfd)
    assert "замечание не применено: правка удаляла D-02" in out
    assert _design(env).read_text(encoding="utf-8") == DESIGN
    [remark] = rp.all_remarks(rs.run_dir(RUN_ID))
    assert remark.status == rp.DROPPED
    assert seen == [] and rs.load(RUN_ID).status == runner.WAITING_OWNER_REVIEW


def test_stop_is_pause_code_0(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    seen = _setup(env, monkeypatch, "/стоп")
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert "просмотр этапа «дизайн» прерван — продолжить: dfl run" in out
    assert seen == [] and len(env.spec_calls) == 1
    state = rs.load(RUN_ID)
    assert state.status == runner.WAITING_OWNER_REVIEW and PAUSE_KEY not in state.ops


def test_revised_survives_pause(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """Дизайн поправлен, на приёмке `/стоп`; следующий `dfl run` с «дальше»
    обязан записать `revised=True` — иначе правка ушла бы без перепроверки."""
    seen = _setup(env, monkeypatch, "уточнить", "", "/стоп")
    _author(monkeypatch, DESIGN + "\nуточнено\n")
    assert env.run("run", "--subject", "t") == 0
    assert seen == []
    assert rs.load(RUN_ID).ops[PAUSE_KEY] == {"status": "started", "revised": True}
    _lines(monkeypatch, "дальше", "дальше")
    assert env.run("run", "--subject", "t") == 0
    assert seen == [{"status": "completed", "revised": True}]


def test_no_tty_stop_hint(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    seen = _steps(env, monkeypatch)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("без tty"))
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    bundle = (env.tmp / "target" / "workstreams/WS-1/spec").resolve()
    assert (
        "ждёт просмотра этапа «дизайн, приёмка» — откройте "
        f"file://{bundle / '20-design.md'}, file://{bundle / '25-acceptance.md'}; "
        "замечания — dfl run в терминале"
    ) in out
    assert seen == [] and len(env.spec_calls) == 1


def _candidate_state(tmp_path: Path) -> rs.RunState:
    state = rs.new_run(
        mode="test",
        subject="t",
        repo="o/alpha",
        repo_slug="o-alpha",
        ws_id="WS-1",
        target_dir="/t",
        bundle_dir="workstreams/WS-1/spec",
        profile="profiles/team-exp.yaml",
        run_id=RUN_ID,
    )
    rs.save(state)
    return state


@pytest.mark.parametrize(
    "op",
    [
        {"nodes": ["design", "acceptance"]},
        {
            "nodes": ["design"],
            "act": "review/v1",
            "policy": {"activation_commit": "abc"},
        },
    ],
    ids=["legacy", "review-v1"],
)
def test_candidate_body_has_owner_remarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: dict
) -> None:
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _candidate_state(tmp_path)
    plain = _candidate_body(state, op, None)
    assert "Замечания владельца" not in plain
    rd = rs.run_dir(RUN_ID)
    rp.record(rd, "design", "уточнить D-01")
    assert _candidate_body(state, op, None) == plain  # не применено — раздела нет
    rp.mark_applied(rd, "design", 1)
    body = _candidate_body(state, op, None)
    assert body == plain + "\n\n" + rp.pr_section(rd).rstrip("\n")
    assert "- design: уточнить D-01" in body


def test_revised_wave_paused_again(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """Правка → раннер перепроверил волну и снова встал на паузе → оба файла
    показаны снова; «дальше» — `revised=False`, прогон идёт дальше."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    _lines(monkeypatch, "уточнить", "", "дальше", "дальше", "дальше")
    _author(monkeypatch, DESIGN + "\nуточнено\n")
    seen: list[dict | None] = []

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if not (rs.run_dir(RUN_ID) / "run.json").exists():
            _paused(env)
            return 0
        state = rs.load(RUN_ID)
        op = state.ops.pop(PAUSE_KEY)
        seen.append(op)
        state.status = runner.WAITING_OWNER_REVIEW if op["revised"] else "running"
        rs.save(state)  # перепроверка волны прошла — пауза снова
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    assert env.run("run", "--subject", "t") == 0
    assert seen == [
        {"status": "completed", "revised": True},
        {"status": "completed", "revised": False},
    ]
    out, _ = _read(capfd)
    assert out.count("этап «дизайн» готов") == 2
    assert out.count("этап «приёмка» готов") == 2
    assert "переписывание 1, проверка 1" in out


def test_non_claude_author_at_terminal_hint(env: Env, monkeypatch, capfd) -> None:  # noqa: F811
    """Терминал есть, автор не claude: цикл замечаний не идёт — подсказка
    про harness.author со ссылками, код 0, пауза не пройдена."""
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    monkeypatch.setattr(dfl, "_author_is_claude", lambda: False)
    seen = _steps(env, monkeypatch)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("без claude"))
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    bundle = (env.tmp / "target" / "workstreams/WS-1/spec").resolve()
    assert (
        "замечания на паузе поддерживаются только с автором claude "
        "(harness.author = claude); откройте "
        f"file://{bundle / '20-design.md'}, file://{bundle / '25-acceptance.md'} "
        "и продолжите: dfl run"
    ) in out
    assert "dfl run в терминале" not in out
    assert seen == [] and rs.load(RUN_ID).status == runner.WAITING_OWNER_REVIEW
