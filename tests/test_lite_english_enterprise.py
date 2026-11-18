"""`DFL_LANG`/`--lang` не доходит до enterprise-пути (#769, ревью I1).

Вопрос enterprise-паузы — тот же текст и id с `DFL_LANG=en` и без него, а
ответ, записанный `dfl reply` в одном окружении, доживает до `dfl run` в
другом (id — sha256 текста). Фикстуры — как в
`tests/test_lite_dfl_owner_review_remote.py`.
"""

from __future__ import annotations

import shutil

import pytest

from governance import run_state as rs
from lite import dfl, i18n, pending
from tests.test_lite_dfl import REPO, URL, Env, _read, env  # noqa: F401
from tests.test_lite_dfl_owner_review import RUN_ID, _author, _steps
from tests.test_lite_dfl_owner_review_remote import _acked, _remote


@pytest.fixture(autouse=True)
def _known_target(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Репо уже зарегистрировано: `dfl reply` его не регистрирует сам."""
    target = env.tmp / "target"
    monkeypatch.setattr(
        dfl.user_targets,
        "load",
        lambda: {REPO: dfl.user_targets.UserTarget(REPO, target.resolve(), URL)},
    )


def _paused_question(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    lang: str | None,
) -> dict:
    """`dfl run` до паузы просмотра (с `DFL_LANG=lang`); записанный вопрос."""
    _remote(monkeypatch)
    _steps(env, monkeypatch)
    _author(monkeypatch)
    if lang is not None:
        monkeypatch.setenv(i18n.ENV_VAR, lang)
    assert env.run("run", "--subject", "t") == 0
    rec = pending.load(rs.run_dir(RUN_ID))
    assert rec is not None
    return rec


def _question_ru(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    return _paused_question(env, monkeypatch, None)


def test_enterprise_question_same_with_dfl_lang_en(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _paused_question(env, monkeypatch, "en")
    assert rec["text"].startswith("этап «дизайн» готов"), rec["text"]
    expected = pending.Question(
        rec["kind"], rec["key"], rec["text"], tuple(rec.get("options") or ())
    )
    assert rec["id"] == expected.id
    assert "stage" not in rec["text"]


def test_enterprise_question_identical_ru_and_en(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ru = _question_ru(env, monkeypatch)
    shutil.rmtree(rs.run_dir(RUN_ID))  # тот же прогон заново, уже с en
    en = _paused_question(env, monkeypatch, "en")
    assert (en["id"], en["text"]) == (ru["id"], ru["text"])


def test_enterprise_reply_survives_language_switch(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вопрос записан с `DFL_LANG=en`, ответ — тоже; `dfl run` без
    `DFL_LANG` ответ принимает (не теряет как устаревший)."""
    rec = _paused_question(env, monkeypatch, "en")
    assert env.run("reply", "--id", str(rec["id"]), "--text", "next") == 0
    monkeypatch.delenv(i18n.ENV_VAR)
    assert env.run("run", "--subject", "t") == 0
    assert _acked() == ["design"]


def test_enterprise_reply_survives_dfl_lang_en_run(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вопрос записан без `DFL_LANG`; ответ и `dfl run` — с `DFL_LANG=en`."""
    rec = _question_ru(env, monkeypatch)
    monkeypatch.setenv(i18n.ENV_VAR, "en")
    assert env.run("reply", "--id", str(rec["id"]), "--text", "дальше") == 0
    assert env.run("run", "--subject", "t") == 0
    assert _acked() == ["design"]


def test_enterprise_pending_output_ignores_dfl_lang(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`dfl pending` enterprise-прогона — тот же вывод с `DFL_LANG=en`."""
    _question_ru(env, monkeypatch)
    _read(capfd)
    assert env.run("pending") == 0
    ru, _ = _read(capfd)
    monkeypatch.setenv(i18n.ENV_VAR, "en")
    assert env.run("pending") == 0
    en, _ = _read(capfd)
    assert en.splitlines()[-4:] == ru.splitlines()[-4:]  # шапка режима — до
    assert ru.splitlines()[-4].startswith("этап «дизайн» готов")
