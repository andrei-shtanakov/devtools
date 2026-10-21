"""Minimal deterministic TOML emitter (stdlib has a reader only)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


def dumps(data: Mapping[str, object]) -> str:
    """Render `data` as TOML.

    Supports str, int, bool, list of str, nested tables and arrays of tables.
    Any other type (float, None, mixed lists, non-str keys) raises TypeError.
    """
    lines: list[str] = []
    _emit_table(data, (), lines)
    return "\n".join(lines).strip("\n") + "\n"


def _emit_table(
    table: Mapping[str, object], path: tuple[str, ...], lines: list[str]
) -> None:
    scalars = [(k, v) for k, v in table.items() if not _is_nested(k, v)]
    nested = [(k, v) for k, v in table.items() if _is_nested(k, v)]
    for key, value in scalars:
        lines.append(f"{_key(key)} = {_value(value)}")
    for key, value in nested:
        sub = (*path, key)
        if isinstance(value, Mapping):
            _begin(f"[{_dotted(sub)}]", lines)
            _emit_table(value, sub, lines)
            continue
        assert isinstance(value, Sequence)
        for item in value:
            _begin(f"[[{_dotted(sub)}]]", lines)
            _emit_table(item, sub, lines)


def _begin(header: str, lines: list[str]) -> None:
    if lines:
        lines.append("")
    lines.append(header)


def _is_nested(key: object, value: object) -> bool:
    _key(key)
    if isinstance(value, Mapping):
        return True
    if isinstance(value, list) and value:
        return all(isinstance(v, Mapping) for v in value)
    return False


def _dotted(path: tuple[str, ...]) -> str:
    return ".".join(_key(p) for p in path)


def _key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError(f"TOML key must be str, got {type(key).__name__}")
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(_string(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _string(text: str) -> str:
    return '"' + "".join(_escape(ch) for ch in text) + '"'


def _escape(ch: str) -> str:
    if ch in _ESCAPES:
        return _ESCAPES[ch]
    if ord(ch) < 0x20 or ord(ch) == 0x7F:
        return f"\\u{ord(ch):04x}"
    return ch
