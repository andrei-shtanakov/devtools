"""Тесты authority_root — SSOT перечня защищённых путей (ADR-ECO-004 I2).

Перечень читают трое: `accept_pr` (гард приёмки), `runner`
(`touches_authority_root` для merge_gate) и `merge-pr.sh` (категорический
отказ обвязки мержа). До круга 4 ревью #183 питоновских определений было
два, и разойтись они могли молча.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance import authority_root


def test_prefixes_read_from_the_ssot_file() -> None:
    assert set(authority_root.prefixes()) == {
        ".github/",
        "profiles/",
        # Перечень защищает сам себя: менять границу полномочий — тоже
        # authority-акт, иначе агент вынес бы путь из-под защиты и следом
        # смержил правку в нём.
        "contracts/authority-root/",
    }


def test_touched_returns_matching_paths_in_order() -> None:
    files = ["lib/x.ex", "profiles/steward.yaml", "docs/a.md", ".github/ci.yml"]
    assert authority_root.touched(files) == [
        "profiles/steward.yaml",
        ".github/ci.yml",
    ]


def test_touched_is_a_literal_prefix_not_a_pattern() -> None:
    """`.github/` — префикс, а не regexp: `xgithub/` под него не попадает."""
    assert authority_root.touched(["xgithub/ci.yml", "myprofiles/a"]) == []


def test_missing_ssot_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступный файл — исключение, а не пустой кортеж.

    Пустой кортеж означал бы «authority-root путей нет», то есть снятие
    защиты молчанием.
    """
    monkeypatch.setattr(
        authority_root, "PATHS_FILE", Path("/no/such/paths.env")
    )
    with pytest.raises(RuntimeError, match="недоступен"):
        authority_root.prefixes()


def test_authority_root_prefixes_empty_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустое значение ключа — тоже отказ, а не «путей нет»."""
    broken = tmp_path / "paths.env"
    broken.write_text("# только комментарий\nAUTHORITY_ROOT_PREFIXES=\n")
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="AUTHORITY_ROOT_PREFIXES"):
        authority_root.prefixes()


def test_missing_key_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "paths.env"
    broken.write_text("SOMETHING_ELSE=x\n")
    monkeypatch.setattr(authority_root, "PATHS_FILE", broken)
    with pytest.raises(RuntimeError, match="AUTHORITY_ROOT_PREFIXES"):
        authority_root.prefixes()
