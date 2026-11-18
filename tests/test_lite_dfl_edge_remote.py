"""Разбор замечаний edge-check через `dfl reply` (#766 срез 2, Task 4).

Enterprise-путь: стоп проверки → вопрос в `pending.json` (код 0) → `dfl
reply` → `dfl run` продолжает круг без начального шага (иначе resume из
`stopped_review` заново провёл бы платную проверку), сортировка — из кэша,
номер круга — из слота `edge-wait.json`. Агент сортировки и граница автора
подменены, шаг конвейера — `spec_loop.main`.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from governance import run_state as rs
from governance import spec_loop
from governance.author_launch import LaunchOutcome
from governance.runner import WAITING_OWNER_REVIEW, review_pause_passed
from lite import dfl, pending
from lite import edge_resolve as er
from lite import interview_chat as ic
from tests.test_lite_dfl import (  # noqa: F401
    REPO,
    URL,
    Env,
    _bind_streams,
    _read,
    _run,
    _tty,
    env,
)
from tests.test_lite_dfl_owner_review import _author
from tests.test_lite_edge_resolve import BEFORE, _decision_item

RUN_ID = "WS-1-a"
FILE = "10-requirements.md"
PAID = re.compile(
    r"платных вызовов: сортировка (\d+), переписывание (\d+), проверка (\d+)"
)


@pytest.fixture(autouse=True)
def _known_target(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Репо уже зарегистрировано: `dfl reply` его не регистрирует сам."""
    target = env.tmp / "target"
    monkeypatch.setattr(
        dfl.user_targets,
        "load",
        lambda: {REPO: dfl.user_targets.UserTarget(REPO, target.resolve(), URL)},
    )


@dataclass
class Fakes:
    """Подмены: агент сортировки, граница автора, шаги и круги разбора."""

    env: Env
    steps: list[str]
    prompts: list[str] = field(default_factory=list)
    revisions: list[str] = field(default_factory=list)
    rounds: list[int] = field(default_factory=list)

    @property
    def node(self) -> Path:
        return self.env.tmp / "target" / "workstreams" / "WS-1" / "spec" / FILE


def _stop(env: Env, rule: str) -> None:  # noqa: F811
    """Прогон в `stopped_review` волны 2 с блокирующей находкой `rule`."""
    state = _run(RUN_ID, "WS-1", "customer", "stopped_review", subject="t")
    state.target_dir = str(env.tmp / "target")
    state.wave = 2
    state.ops["edge-2"] = {"status": "started"}
    rs.save(state)
    rd = rs.run_dir(RUN_ID)
    statement = f"Цель {rule} не покрыта"
    line = (
        f"error EDGE-CHECK(requirements/requirements-vs-charter): {rule} major: "
        f"{statement} @ {FILE}:[1, 2]"
    )
    (rd / "edge-findings.txt").write_text(line + "\n", encoding="utf-8")
    out = rd / "edge-check" / "w2"
    out.mkdir(parents=True, exist_ok=True)
    record = {
        "node": "requirements",
        "edge": "requirements-vs-charter",
        "verdict": "FAIL",
        "findings": [
            {"rule_id": rule, "class": "major", "statement": statement,
             "location": {"path": FILE, "lines": [1, 2]}},
        ],
        "subject": [{"path": FILE, "sha256": "s-node"}],
    }  # fmt: skip
    (out / "requirements--requirements-vs-charter.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


def _passed(env: Env) -> None:  # noqa: F811
    state = _run(RUN_ID, "WS-1", "customer", "running", subject="t")
    state.target_dir = str(env.tmp / "target")
    state.wave = 2
    state.ops["edge-2"] = {"status": "completed", "verdict": "PASS"}
    rs.save(state)


def _fakes(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    steps: list[str],
) -> Fakes:
    """Шаги по очереди: `R1`/`R2` — стоп с этой находкой, `pass` — прошло."""
    f = Fakes(env, list(steps))
    f.node.parent.mkdir(parents=True, exist_ok=True)
    f.node.write_text(BEFORE, encoding="utf-8")

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        action = f.steps.pop(0)
        if action == "pass":
            _passed(env)
            return 0
        _stop(env, action)
        return 1

    def agent(prompt: str) -> str:
        f.prompts.append(prompt)
        return json.dumps({"items": _decision_item()}, ensure_ascii=False)

    def revise(self: object, call: Any, kind: str, *rest: str) -> LaunchOutcome:
        f.revisions.append(call.filename)
        f.node.write_text(
            f.node.read_text(encoding="utf-8") + f"\nG-0{len(f.revisions)}: да\n",
            encoding="utf-8",
        )
        return LaunchOutcome(0)

    original = er.resolve

    def resolve(state: rs.RunState, **kw: Any) -> er.Outcome:
        f.rounds.append(kw["round_no"])
        return original(state, **kw)

    monkeypatch.setattr(spec_loop, "main", spec_main)
    monkeypatch.setattr(dfl.st, "claude_agent", lambda model: agent)
    monkeypatch.setattr(dfl.RealOps, "revise", revise)
    monkeypatch.setattr(er, "resolve", resolve)
    return f


def _remote(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("без tty"))


def _terminal(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, True)
    feed = iter(answers)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: next(feed, None))


def _reply(env: Env, text: str) -> dict:  # noqa: F811
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None and rec["kind"] == pending.EDGE
    assert env.run("reply", "--id", str(rec["id"]), "--text", text) == 0
    return rec


def _paid(out: str) -> tuple[int, int, int]:
    """Счётчики последней строки расходов вызова (они накопительные)."""
    found = PAID.findall(out)
    if not found:
        return (0, 0, 0)
    a, b, c = found[-1]
    return (int(a), int(b), int(c))


def _slot() -> dict | None:
    return er.wait_load(rs.run_dir(RUN_ID), "wave-2")


def _cycles(
    env: Env,  # noqa: F811
    capfd: pytest.CaptureFixture[str],
    answers: list[str],
) -> tuple[int, int, int]:
    """`dfl run` → `dfl reply` по очереди ответов, затем последний `dfl run`;
    суммы счётчиков расходов всех вызовов."""
    total = [0, 0, 0]

    def one() -> None:
        assert env.run("run", "-v", "--subject", "t") == 0
        out, _ = _read(capfd)
        for i, n in enumerate(_paid(out)):
            total[i] += n

    for answer in answers:
        one()
        _reply(env, answer)
    one()
    return (total[0], total[1], total[2])


def test_edge_question_written_without_tty(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1"])
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None and rec["kind"] == pending.EDGE
    assert rec["options"][0] == "покрыть требованием (рекомендую)"
    assert "ждёт ответа: замечание R1 major" in out
    assert "дальше: поправьте" not in out and "разбор остановлен" not in out
    assert "платных вызовов: сортировка 1, переписывание 0, проверка 0" in out
    assert len(f.prompts) == 1 and f.revisions == []
    assert _slot() is not None and _slot()["round"] == 1  # type: ignore[index]


def test_edge_resume_no_repaid_triage_or_check(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1", "pass"])
    assert env.run("run", "--subject", "t") == 0
    _reply(env, "1")
    _read(capfd)
    assert env.run("run", "-v", "--subject", "t") == 0
    out, _ = _read(capfd)
    # без начального шага: только повторный после переписывания
    assert len(env.spec_calls) == 2
    assert "--run-id" in env.spec_calls[1]
    assert len(f.prompts) == 1  # сортировка из кэша
    assert f.revisions == [FILE]
    assert "платных вызовов: сортировка 0, переписывание 1, проверка 1" in out
    assert pending.load(rs.run_dir(RUN_ID)) is None and _slot() is None
    assert f.rounds == [1, 1]


def test_run_without_reply_keeps_waiting_unpaid(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Повторный `dfl run` до ответа: тот же вопрос, ни шага, ни сортировки."""
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1"])
    assert env.run("run", "--subject", "t") == 0
    first = pending.load(rs.run_dir(RUN_ID))
    _read(capfd)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert len(env.spec_calls) == 1 and len(f.prompts) == 1
    again = pending.load(rs.run_dir(RUN_ID))
    assert first is not None and again is not None and again["id"] == first["id"]
    assert "ждёт ответа:" in out and "платных вызовов" not in out


def test_waiting_on_second_round_keeps_round(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1", "R2", "pass"])
    assert env.run("run", "--subject", "t") == 0
    _reply(env, "1")
    assert env.run("run", "--subject", "t") == 0  # круг 1, затем вопрос круга 2
    assert f.rounds == [1, 1, 2]
    assert _slot() is not None and _slot()["round"] == 2  # type: ignore[index]
    rec = _reply(env, "3")
    assert rec["options"][-1] == er.OVERRIDE_OFFER
    _read(capfd)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert f.rounds == [1, 1, 2, 2]  # продолжение — круг 2, не 1
    assert "замечания приняты вопреки" in out and "не сошлось" not in out
    assert len(env.spec_calls) == 3 and f.revisions == [FILE]
    assert _slot() is None


def test_round_limit_clears_slot(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Ожидание на круге 2 и ответ «1» — круг 2 переписан, проверка снова
    не прошла: предел кругов назван, слот снят (новый `dfl run` — с 1)."""
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1", "R2", "R2"])
    assert env.run("run", "--subject", "t") == 0
    _reply(env, "1")
    assert env.run("run", "--subject", "t") == 0
    _reply(env, "1")
    _read(capfd)
    assert env.run("run", "--subject", "t") == 1
    out, _ = _read(capfd)
    assert "не сошлось за 2 круга" in out
    assert f.rounds == [1, 1, 2, 2] and _slot() is None


def test_terminal_takes_recorded_edge_reply(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Ответ записан `dfl reply`, `dfl run` — уже в терминале: ответ берётся,
    начального шага (платной перепроверки) нет, живой ввод не нужен."""
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1", "pass"])
    assert env.run("run", "--subject", "t") == 0
    _reply(env, "1")
    _terminal(monkeypatch, [])
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("ответ есть"))
    assert env.run("run", "--subject", "t") == 0
    assert len(env.spec_calls) == 2 and len(f.prompts) == 1
    assert f.revisions == [FILE]
    assert pending.load(rs.run_dir(RUN_ID)) is None and _slot() is None


def test_edge_remote_paid_calls_equal_terminal(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """§5.6 п.5: циклы `dfl run` → `dfl reply` без терминала платят столько
    же, сколько один терминальный проход с теми же ответами."""
    _terminal(monkeypatch, ["1", "", "3", ""])
    term = _fakes(env, monkeypatch, ["R1", "R2", "pass"])
    assert env.run("run", "-v", "--subject", "t") == 0
    out, _ = _read(capfd)
    terminal = (_paid(out), len(term.prompts), term.revisions, len(env.spec_calls))
    assert terminal[0] == (2, 1, 2)

    shutil.rmtree(rs.run_dir(RUN_ID))  # второй мир: тот же прогон заново
    env.spec_calls.clear()
    _remote(monkeypatch)
    remote = _fakes(env, monkeypatch, ["R1", "R2", "pass"])
    paid = _cycles(env, capfd, ["1", "3"])
    assert (paid, len(remote.prompts), remote.revisions, len(env.spec_calls)) == (
        terminal
    )


def test_new_edge_check_resets_saved_round(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Слот волны остался от круга 2, но шаг заново провёл проверку (ждущего
    вопроса нет — `dfl answer`, `chat=False`, правка): новые находки — с
    круга 1, без «принять вопреки»."""
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, ["R1"])
    _stop(env, "R1")
    er.wait_save(rs.run_dir(RUN_ID), "wave-2", {"round": 2, "node": None,
                                                "findings": [], "batch": "old"})  # fmt: skip
    assert env.run("run", "--subject", "t") == 0
    assert len(env.spec_calls) == 1 and f.rounds == [1]
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None and er.OVERRIDE_OFFER not in rec["options"]
    assert _slot() is not None and _slot()["round"] == 1  # type: ignore[index]


def test_owner_revision_edge_stop_resolved_without_tty(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Пауза просмотра без терминала → замечание через `dfl reply` → правка
    → повторная проверка волны с находками: вопрос разбора в `pending.json`
    (код 0, слот круга), не «поправьте»; ответ → `dfl run` сходится без
    лишней платной проверки (как в терминале)."""
    _remote(monkeypatch)
    f = _fakes(env, monkeypatch, [])
    owner = _author(monkeypatch, BEFORE + "\nуточнено\n")
    checks: list[str] = []

    def spec_main(argv: list[str]) -> int:
        env.spec_calls.append(list(argv))
        if not (rs.run_dir(RUN_ID) / "run.json").exists():
            state = _run(RUN_ID, "WS-1", "customer", WAITING_OWNER_REVIEW, subject="t")
            state.target_dir = str(env.tmp / "target")
            state.wave = 2
            state.review_pause = ["requirements"]
            state.ops["edge-2"] = {"status": "completed", "verdict": "PASS"}
            rs.save(state)
            return 0
        state = rs.load(RUN_ID)
        if state.status == WAITING_OWNER_REVIEW and not review_pause_passed(state):
            return 0
        if not checks:
            checks.append("R1")
            _stop(env, "R1")
            return 1
        checks.append("pass")
        _passed(env)
        return 0

    monkeypatch.setattr(spec_loop, "main", spec_main)
    rd = rs.run_dir(RUN_ID)
    assert env.run("run", "--subject", "t") == 0
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REVIEW
    assert env.run("reply", "--id", str(rec["id"]), "--text", "уточни FR-01") == 0
    _read(capfd)

    assert env.run("run", "--subject", "t") == 0  # правка, проверка, вопрос
    out, _ = _read(capfd)
    if (rec := pending.load(rd)) is not None and rec["kind"] != pending.EDGE:
        assert rec["kind"] == pending.REMARKS_CONFIRM
        assert env.run("reply", "--id", str(rec["id"]), "--text", "1") == 0
        assert env.run("run", "--subject", "t") == 0
        out, _ = _read(capfd)
    assert len(owner) == 1 and checks == ["R1"]
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.EDGE
    assert "ждёт ответа: замечание R1 major" in out
    assert "дальше: поправьте" not in out
    assert _slot() is not None and _slot()["round"] == 1  # type: ignore[index]
    assert len(f.prompts) == 1

    _reply(env, "1")
    assert env.run("run", "--subject", "t") == 0
    assert checks == ["R1", "pass"]  # только перепроверка после переписывания
    assert f.revisions == [FILE] and len(f.prompts) == 1
    assert rs.load(RUN_ID).status == "running"
    assert pending.load(rd) is None and _slot() is None


def test_edge_wait_line_names_subject_when_ambiguous(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Две незаконченные темы в репо: подсказка `dfl reply` называет тему —
    без неё `dfl pending`/`dfl reply` прогон не выберут (как `dfl status`)."""
    _remote(monkeypatch)
    _fakes(env, monkeypatch, ["R1"])
    assert env.run("run", "--subject", "t") == 0
    _read(capfd)
    _run("WS-2-a", "WS-2", "customer", "running", subject="другая")
    assert env.run("run", "--subject", "t") == 0  # тот же вопрос, без шага
    out, _ = _read(capfd)
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None
    assert f"dfl reply --subject 't' --id {rec['id']}" in out
