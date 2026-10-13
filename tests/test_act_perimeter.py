"""Периметр (спека §6.2 б, §6.3): маркер и самоотчёт прав."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.act_epochs import Epoch
from governance.act_perimeter import (
    PerimeterError,
    Violation,
    blocked,
    marker_path,
    permissions_refusal,
    record_violation,
)
from governance.act_time import Unavailable

SRC = "owner/approval-policy"
E1 = (Epoch(1, "a" * 40),)
E2 = (Epoch(1, "a" * 40, "c" * 40, "2026-10-20T10:00:00Z", "x"), Epoch(2, "b" * 40))


def _write_raw(tmp_path: Path, text: str) -> Path:
    path = marker_path(SRC, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_no_marker_not_blocked(tmp_path: Path) -> None:
    assert blocked(SRC, E1, tmp_path) is None


def test_marker_blocks_until_newer_epoch(tmp_path: Path) -> None:
    """Приёмка №36: перезапуск сохраняет блокировку до эпохи с большим номером."""
    record_violation(SRC, 1, "push=true у токена", tmp_path)
    assert blocked(SRC, E1, tmp_path)
    assert blocked(SRC, E2, tmp_path) is None


def test_unreadable_marker_blocks(tmp_path: Path) -> None:
    record_violation(SRC, 1, "x", tmp_path)
    next(tmp_path.rglob("*.json")).write_text("{", encoding="utf-8")
    assert blocked(SRC, E2, tmp_path)


@pytest.mark.parametrize("epoch", [0, -1, True, 1.5, "1", None, "broken"])
def test_malformed_marker_epoch_blocks(tmp_path: Path, epoch: object) -> None:
    """Битый номер эпохи в маркере блокирует, а не снимает блокировку."""
    _write_raw(tmp_path, json.dumps({"epoch": epoch}))
    assert blocked(SRC, E2, tmp_path)


@pytest.mark.parametrize("text", ["[]", "null", "1", '"x"', "{}", ""])
def test_non_object_marker_blocks(tmp_path: Path, text: str) -> None:
    _write_raw(tmp_path, text)
    assert blocked(SRC, E2, tmp_path)


def test_non_utf8_marker_blocks(tmp_path: Path) -> None:
    path = _write_raw(tmp_path, "")
    path.write_bytes(b"\xff\xfe\x00")
    assert blocked(SRC, E2, tmp_path)


def test_marker_is_directory_blocks(tmp_path: Path) -> None:
    marker_path(SRC, tmp_path).mkdir(parents=True)
    assert blocked(SRC, E2, tmp_path)


def test_empty_journal_with_marker_blocks(tmp_path: Path) -> None:
    record_violation(SRC, 1, "x", tmp_path)
    assert blocked(SRC, (), tmp_path)


def test_late_writer_does_not_lower_marker(tmp_path: Path) -> None:
    """P4-2: E2 обнаружил, запоздалый писатель E1 не снимает блокировку E2."""
    record_violation(SRC, 2, "обнаружено в E2", tmp_path)
    record_violation(SRC, 1, "запоздалый E1", tmp_path)
    assert blocked(SRC, E2, tmp_path)


def test_higher_epoch_raises_marker(tmp_path: Path) -> None:
    record_violation(SRC, 1, "a", tmp_path)
    record_violation(SRC, 2, "b", tmp_path)
    data = json.loads(marker_path(SRC, tmp_path).read_text(encoding="utf-8"))
    assert data["epoch"] == 2


def test_corrupt_marker_not_overwritten(tmp_path: Path) -> None:
    record_violation(SRC, 1, "x", tmp_path)
    next(tmp_path.rglob("*.json")).write_text("{", encoding="utf-8")
    record_violation(SRC, 1, "повтор", tmp_path)
    assert marker_path(SRC, tmp_path).read_text(encoding="utf-8") == "{"
    assert blocked(SRC, E2, tmp_path)


@pytest.mark.parametrize("epoch_no", [0, -1, True, 1.5, "1", None])
def test_record_rejects_bad_epoch_no(tmp_path: Path, epoch_no: object) -> None:
    with pytest.raises(PerimeterError):
        record_violation(SRC, epoch_no, "x", tmp_path)  # type: ignore[arg-type]
    assert not marker_path(SRC, tmp_path).exists()


def test_record_write_failure_is_perimeter_error(tmp_path: Path) -> None:
    (tmp_path / "darkfactory").write_text("file", encoding="utf-8")
    with pytest.raises(PerimeterError):
        record_violation(SRC, 1, "x", tmp_path)


def test_write_permissions_is_violation() -> None:
    """Приёмка №29: установленное право записи -> Violation (маркер)."""
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


def test_exists_oserror_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path) -> bool:
        raise OSError("denied")

    monkeypatch.setattr(Path, "exists", boom)
    assert blocked(SRC, E2, tmp_path)


def test_corrupt_marker_text_names_recovery(tmp_path: Path) -> None:
    _write_raw(tmp_path, "{")
    assert "вручную" in (blocked(SRC, E2, tmp_path) or "")


def test_read_only_passes() -> None:
    perms = {"push": False, "maintain": False, "admin": False}
    assert permissions_refusal(perms) is None
