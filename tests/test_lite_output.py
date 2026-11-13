"""Блок отказа `dfl` (lite/output.py)."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest

from lite.output import (
    refusal,
    say,
    set_sink,
    set_verbosity,
    verbosity,
    verbosity_from_env,
)


@pytest.fixture(autouse=True)
def _restore_output_state() -> Iterator[None]:
    """Уровень и лог — состояние модуля: возвращаем как было."""
    before = verbosity()
    yield
    set_verbosity(before)
    set_sink(None)


def test_refusal_splits_cause_and_action() -> None:
    text = refusal("dfl init-repo", "нет origin/HEAD — выполните git fetch")
    assert text.splitlines() == [
        "",
        "dfl init-repo: отказ (код 2)",
        "  причина:     нет origin/HEAD",
        "  что сделать: выполните git fetch",
    ]


def test_refusal_without_action_is_cause_only() -> None:
    assert refusal("dfl run", "сломалось").splitlines()[2:] == [
        "  причина:     сломалось"
    ]


def test_refusal_splits_on_first_dash_only() -> None:
    lines = refusal("dfl run", "a — b — c").splitlines()
    assert lines[2:] == ["  причина:     a", "  что сделать: b — c"]


def test_refusal_multiline_message_stays_indented() -> None:
    lines = refusal("dfl run", "a — первая\nвторая").splitlines()
    assert lines[3:] == ["  что сделать: первая", "               вторая"]


def test_refusal_ignores_dash_inside_quoted_data() -> None:
    # тема пользователя с тире не режет причину (ревью #745)
    msg = "нет конвейера с темой 'оплата — этап 2' — проверьте dfl status"
    lines = refusal("dfl abandon", msg).splitlines()
    assert lines[2:] == [
        "  причина:     нет конвейера с темой 'оплата — этап 2'",
        "  что сделать: проверьте dfl status",
    ]
    only_quoted = refusal("dfl abandon", "нет темы 'a — b'").splitlines()
    assert only_quoted[2:] == ["  причина:     нет темы 'a — b'"]


def test_say_respects_level(capsys: pytest.CaptureFixture[str]) -> None:
    set_verbosity(0)
    set_sink(None)
    say("итог")
    say("подсказка", 1)
    assert capsys.readouterr().out == "итог\n"


def test_say_err_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    set_verbosity(1)
    say("ошибка", 1, err=True)
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "ошибка\n")


def test_say_always_writes_sink(capsys: pytest.CaptureFixture[str]) -> None:
    buf = io.StringIO()
    set_verbosity(0)
    set_sink(buf)
    say("скрыто", 3, err=True)
    assert capsys.readouterr().err == ""
    assert buf.getvalue() == "скрыто\n"


def test_set_verbosity_clamps() -> None:
    set_verbosity(7)
    assert verbosity() == 3
    set_verbosity(-1)
    assert verbosity() == 0


@pytest.mark.parametrize(
    ("raw", "level", "warned"),
    [
        ("", 0, False),
        ("2", 2, False),
        ("9", 3, False),
        ("abc", 0, True),
        ("-1", 0, True),
    ],
)
def test_verbosity_from_env(raw: str, level: int, warned: bool) -> None:
    got, warn = verbosity_from_env({"DFL_VERBOSITY": raw} if raw else {})
    assert got == level
    assert (warn is not None) == warned
    if warn is not None:
        assert "DFL_VERBOSITY" in warn
        assert raw in warn
