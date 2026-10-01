"""Устойчивая запись (спека среза 1, §4.6)."""

from pathlib import Path

import pytest

from conductor.durable import append_line, move_to_corrupt, read_jsonl, write_atomic


def test_write_atomic_replaces_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "f.json"
    write_atomic(path, "1")
    write_atomic(path, "2")
    assert path.read_text(encoding="utf-8") == "2"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.json"]


def test_append_and_read(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    append_line(path, {"a": 1})
    append_line(path, {"b": "ю"})
    got = read_jsonl(path)
    assert got.rows == [{"a": 1}, {"b": "ю"}] and not got.truncated_tail


def test_truncated_tail_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    append_line(path, {"a": 1})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"b": ')
    got = read_jsonl(path)
    assert got.rows == [{"a": 1}] and got.truncated_tail


def test_bad_middle_line_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_jsonl(path)


def test_move_to_corrupt_keeps_bytes(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("bad", encoding="utf-8")
    dest = move_to_corrupt(tmp_path, ["x.json", "absent.json"], "20261001T000000Z")
    assert (dest / "x.json").read_text(encoding="utf-8") == "bad"
    assert not (tmp_path / "x.json").exists()
