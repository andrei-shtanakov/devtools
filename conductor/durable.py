"""Устойчивая запись: атомарная замена и jsonl с fsync (спека среза 1, §4.6)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, text: str) -> None:
    """tmp в том же каталоге → fsync → replace → fsync каталога."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def append_line(path: Path, row: dict[str, Any]) -> None:
    """Дописать строку jsonl и дождаться fsync; сбой — OSError вызывающему."""
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


@dataclass
class JsonlRead:
    """Строки jsonl; truncated_tail — последняя строка оборвана записью."""

    rows: list[dict[str, Any]]
    truncated_tail: bool


def read_jsonl(path: Path) -> JsonlRead:
    """Битая строка не в конце — ValueError (файл повреждён)."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:  # нет завершающего \n — оборванная запись
                return JsonlRead(rows, True)
            raise
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}: строка {i + 1} не объект")
        rows.append(row)
    return JsonlRead(rows, False)


def move_to_corrupt(directory: Path, names: list[str], stamp: str) -> Path:
    """Перенести файлы в corrupt/<stamp>/ (не удалять — разбор владельцем)."""
    dest = directory / "corrupt" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        if (directory / name).exists():
            os.replace(directory / name, dest / name)
    _fsync_dir(directory)
    return dest
