"""Локальная настройка только останавливает, ничего не разрешает (§10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.act_settings import (
    DEFAULT_REVIEW_V1,
    ActSettings,
    SettingsError,
    load_settings,
)


def _write(home: Path, text: str | bytes) -> Path:
    (home / "darkfactory").mkdir(parents=True)
    path = home / "darkfactory" / "act.toml"
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def test_absent_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path) == ActSettings(review_v1=False, draining=False)
    assert DEFAULT_REVIEW_V1 is False


def test_reads_flags(tmp_path: Path) -> None:
    _write(tmp_path, 'review_v1 = true\nact_migration = "draining"\n')
    assert load_settings(tmp_path) == ActSettings(review_v1=True, draining=True)


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    _write(tmp_path, "")
    assert load_settings(tmp_path) == ActSettings(review_v1=False, draining=False)


def test_explicit_off(tmp_path: Path) -> None:
    _write(tmp_path, 'act_migration = "off"\nreview_v1 = false\n')
    assert load_settings(tmp_path) == ActSettings(review_v1=False, draining=False)


@pytest.mark.parametrize(
    "text",
    [
        'act_migration = "open"\n',
        'act_migration = ""\n',
        "act_migration = 1\n",
        "act_migration = true\n",
        "act_migration = []\n",
        "review_v1 = 1\n",
        'review_v1 = "true"\n',
        "x = 1\n",
        "review_v1 = \n",
        "not toml at all [\n",
    ],
)
def test_unknown_values_refused(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    with pytest.raises(SettingsError):
        load_settings(tmp_path)


def test_invalid_utf8_refused(tmp_path: Path) -> None:
    _write(tmp_path, b"review_v1 = true\n# \xff\xfe\n")
    with pytest.raises(SettingsError):
        load_settings(tmp_path)


def test_unreadable_path_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "")
    path.unlink()
    path.mkdir()  # a directory in place of the file: OSError on read
    with pytest.raises(SettingsError):
        load_settings(tmp_path)
