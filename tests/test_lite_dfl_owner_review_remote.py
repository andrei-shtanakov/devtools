"""Замечания к узлу волны через `dfl reply` (#766 срез 2, Task 3).

Enterprise-путь без терминала: пауза → `dfl reply` с замечанием → `dfl run`
правит узел (один вызов автора), вопрос «Удалить …?» ждёт `dfl reply` →
`dfl run` без автора; «дальше» запоминается в `review-acked.json`, замечание —
нет. Фейки — как в `tests/test_lite_dfl_owner_review.py`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from governance import run_state as rs
from lite import dfl, pending
from lite import interview_chat as ic
from lite import owner_review as orv
from lite import review_pause as rp
from tests.test_lite_dfl import (  # noqa: F401
    REPO,
    URL,
    Env,
    _bind_streams,
    _read,
    _tty,
    env,
)
from tests.test_lite_dfl_owner_review import (
    DESIGN,
    NO_D02,
    PAUSE_KEY,
    RUN_ID,
    _author,
    _design,
    _lines,
    _paused,
    _steps,
)

REMARK = "убери D-02"
CONFIRM = "Удалить D-02 — «Поиск — построчный фильтр по подстроке»? 1. удалить"
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


def _remote(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch, False)
    monkeypatch.setattr(ic, "stdin_read", lambda prompt: pytest.fail("без tty"))


def _reply(env: Env, text: str) -> dict:  # noqa: F811
    """`dfl reply` на ждущий вопрос; возвращает сам вопрос."""
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None
    assert env.run("reply", "--id", str(rec["id"]), "--text", text) == 0
    return rec


def _paid(out: str) -> tuple[int, int, int]:
    total = [0, 0, 0]
    for match in PAID.finditer(out):
        for i, value in enumerate(match.groups()):
            total[i] += int(value)
    return (total[0], total[1], total[2])


def _acked() -> list[str]:
    path = rs.run_dir(RUN_ID) / dfl.REVIEW_ACKED
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8"))["stages"])


def test_remarks_by_reply_revise_and_continue(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _remote(monkeypatch)
    seen = _steps(env, monkeypatch)
    calls = _author(monkeypatch, NO_D02)
    rd = rs.run_dir(RUN_ID)

    assert env.run("run", "--subject", "t") == 0
    assert _reply(env, REMARK)["key"] == "design"
    assert _acked() == []

    assert env.run("run", "--subject", "t") == 0  # правка, вопрос об удалении
    out, _ = _read(capfd)
    assert len(calls) == 1
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REMARKS_CONFIRM
    assert rec["text"].startswith(CONFIRM)
    assert "ждёт ответа: Удалить D-02" in out
    assert "замечания — dfl run в терминале" not in out
    assert _design(env).read_text(encoding="utf-8") == DESIGN
    assert [r.text for r in rp.pending(rd, "design")] == [REMARK]
    assert _acked() == []  # замечание — не «дальше»
    assert seen == []
    _reply(env, "1")

    assert env.run("run", "--subject", "t") == 0  # без автора; правка принята
    assert len(calls) == 1
    assert _design(env).read_text(encoding="utf-8") == NO_D02
    assert rs.load(RUN_ID).ops[PAUSE_KEY] == {"status": "started", "revised": True}
    assert [(r.text, r.applied) for r in rp.all_remarks(rd)] == [(REMARK, True)]
    assert not (rd / orv.CANDIDATE_FILE).exists()
    assert _acked() == ["design"]  # доигранный узел W4 не спрашивается снова
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REVIEW
    assert rec["key"] == "acceptance"
    _reply(env, "дальше")

    assert env.run("run", "--subject", "t") == 0
    assert len(calls) == 1
    assert seen == [{"status": "completed", "revised": True}]
    assert rs.load(RUN_ID).status == "running"


def test_review_reply_consumed_before_revise(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ паузы снят до правки: `revise_by_owner` со `store` не видит
    чужого вопроса; в `pending.json` — свежий вопрос «Удалить …?»."""
    _remote(monkeypatch)
    _steps(env, monkeypatch)
    _author(monkeypatch, NO_D02)
    rd = rs.run_dir(RUN_ID)
    seen: list[dict | None] = []
    passed: list[dict[str, object]] = []
    real = orv.revise_by_owner

    def spy(*args: Any, **kwargs: Any) -> tuple[str, str]:
        seen.append(pending.load(rd))
        passed.append(kwargs)
        return real(*args, **kwargs)

    assert env.run("run", "--subject", "t") == 0
    review = _reply(env, REMARK)
    monkeypatch.setattr(orv, "revise_by_owner", spy)
    assert env.run("run", "--subject", "t") == 0
    assert seen == [None]
    [kwargs] = passed
    assert kwargs["store"] == rd and kwargs["stage"] == "design"
    assert kwargs["ask"] is not None
    rec = pending.load(rd)
    assert rec is not None and rec["kind"] == pending.REMARKS_CONFIRM
    assert rec["id"] != review["id"]
    assert not pending.answered(rd)


def test_remote_cycle_paid_calls_equal_terminal(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _bind_streams(monkeypatch)
    _tty(monkeypatch)
    _lines(monkeypatch, REMARK, "", "1", "дальше")
    seen = _steps(env, monkeypatch)
    term_calls = _author(monkeypatch, NO_D02)
    assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    term_paid = _paid(out)
    term_design = _design(env).read_text(encoding="utf-8")
    assert seen == [{"status": "completed", "revised": True}]

    # тот же прогон заново — без терминала, ответы через `dfl reply`
    rs.run_dir(RUN_ID).joinpath(rp.REMARKS).unlink()
    _design(env).write_text(DESIGN, encoding="utf-8")
    _paused(env)
    _remote(monkeypatch)
    seen.clear()
    rem_calls = _author(monkeypatch, NO_D02)
    assert env.run("run", "--subject", "t") == 0
    for answer in (REMARK, "1", "дальше"):
        _reply(env, answer)
        assert env.run("run", "--subject", "t") == 0
    out, _ = _read(capfd)
    assert seen == [{"status": "completed", "revised": True}]
    assert _paid(out) == term_paid
    assert term_paid[1] == 1
    assert len(rem_calls) == len(term_calls) == 1
    assert _design(env).read_text(encoding="utf-8") == term_design == NO_D02
    assert rs.load(RUN_ID).status == "running"
