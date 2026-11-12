"""Блок отказа `dfl` (lite/output.py)."""

from __future__ import annotations

from lite.output import refusal


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
