"""Tests for the minimal TOML emitter."""

from __future__ import annotations

import tomllib

import pytest

from df.toml_emit import dumps

DATA = {
    "schema": "df/x/v1",
    "name": 'quote " and \\ and\nnewline',
    "count": 7,
    "neg": -3,
    "flag": True,
    "off": False,
    "items": ["a", "b"],
    "empty": [],
    "unicode": "кириллица",
    "table": {"k": "v", "n": 1, "inner": {"deep": ["x"]}},
    "rows": [{"id": "1", "ok": True}, {"id": "2", "ok": False}],
}


def test_emit_roundtrip() -> None:
    assert tomllib.loads(dumps(DATA)) == DATA


def test_emit_rejects_float() -> None:
    with pytest.raises(TypeError):
        dumps({"x": 1.5})


def test_emit_rejects_none_and_mixed_list() -> None:
    with pytest.raises(TypeError):
        dumps({"x": None})
    with pytest.raises(TypeError):
        dumps({"x": ["a", 1]})


def test_emit_rejects_non_string_key() -> None:
    with pytest.raises(TypeError):
        dumps({1: "a"})  # type: ignore[dict-item]


def test_emit_quotes_odd_keys() -> None:
    data = {"t": {"a b": 1, "c.d": 2}}
    assert tomllib.loads(dumps(data)) == data


def test_emit_control_chars_roundtrip() -> None:
    data = {"s": "a\x00b\x1f\x7fc\t"}
    assert tomllib.loads(dumps(data)) == data
