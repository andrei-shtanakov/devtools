"""Периметр (спека §6.2 б): самоотчёт прав credential."""

from __future__ import annotations

import pytest

from governance.act_perimeter import Violation, permissions_refusal
from governance.act_time import Unavailable


def test_write_permissions_is_violation() -> None:
    """Приёмка №29: установленное право записи -> Violation (событие)."""
    got = permissions_refusal({"push": True, "maintain": False, "admin": False})
    assert isinstance(got, Violation)
    assert "push" in got.detail


def test_missing_permissions_is_unavailable() -> None:
    """Приёмка №33: недоступное не нарушение и не подтверждение."""
    assert isinstance(permissions_refusal(None), Unavailable)
    got = permissions_refusal({"push": False})
    assert isinstance(got, Unavailable)
    assert "maintain" in got.reason
    assert "admin" in got.reason


@pytest.mark.parametrize("value", [None, 0, 1, "false", "", [], {}])
def test_non_bool_value_is_unavailable(value: object) -> None:
    perms = {"push": False, "maintain": False, "admin": False, "pull": True}
    got = permissions_refusal({**perms, "admin": value})
    assert isinstance(got, Unavailable)
    assert "admin" in got.reason
    assert "есть" not in got.reason


def test_true_wins_over_missing_field() -> None:
    """Литеральное True установлено, даже если другие поля недоступны."""
    got = permissions_refusal({"admin": True})
    assert isinstance(got, Violation)
    assert "admin" in got.detail


def test_true_wins_over_non_bool_field() -> None:
    got = permissions_refusal({"push": "x", "maintain": True, "admin": False})
    assert isinstance(got, Violation)


def test_read_only_passes() -> None:
    perms = {"push": False, "maintain": False, "admin": False}
    assert permissions_refusal(perms) is None
