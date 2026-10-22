"""The restricted environment-marker grammar of spec 3.5.

Comparisons `variable op 'string'` or `'string' op variable` over the eight
variables of `marker_env`, joined by `and`, `or` (`and` binds tighter) and
parentheses. Every other construct is `UnsupportedLock` naming the marker.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from df.platform import PlatformName
from df.sources import UnsupportedLock

_PYTHON_VARS = frozenset({"python_version", "python_full_version"})
_STRING_VARS = frozenset(
    {
        "sys_platform",
        "platform_system",
        "platform_machine",
        "os_name",
        "implementation_name",
        "platform_python_implementation",
    }
)
_PLATFORM_VARS: Mapping[PlatformName, tuple[str, str, str]] = {
    "macos-arm64": ("darwin", "Darwin", "arm64"),
    "linux-x86_64": ("linux", "Linux", "x86_64"),
}
_OPS: Mapping[str, Callable[[object, object], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_TOKEN = re.compile(
    r"\s*(?:(?P<str>'[^']*'|\"[^\"]*\")|(?P<op>===|~=|==|!=|<=|>=|<|>)"
    r"|(?P<paren>[()])|(?P<word>[A-Za-z_][A-Za-z0-9_.]*))"
)
_RELEASE = re.compile(r"\d+(\.\d+)*")


def marker_env(platform: PlatformName, python: str) -> Mapping[str, str]:
    """The eight marker variables for `platform` and the exact `python` X.Y.Z."""
    sys_platform, system, machine = _PLATFORM_VARS[platform]
    return {
        "sys_platform": sys_platform,
        "platform_system": system,
        "platform_machine": machine,
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "python_version": ".".join(python.split(".")[:2]),
        "python_full_version": python,
    }


def evaluate(marker: str, env: Mapping[str, str]) -> bool:
    """The value of `marker` in `env`.

    The whole marker is parsed before anything is evaluated, so an
    unsupported part is refused even where `and`/`or` would not reach it.
    Python variables compare as zero-padded integer tuples with all six
    operators; other variables support `==`/`!=` only.
    """
    try:
        return _Parser(marker).parse().value(env)
    except RecursionError as exc:
        raise UnsupportedLock(
            f"неподдерживаемый маркер {marker!r}: слишком глубокая вложенность"
        ) from exc


@dataclass(frozen=True)
class _Compare:
    left: tuple[str, str]  # (kind "var"/"str", text)
    op: str
    right: tuple[str, str]

    def value(self, env: Mapping[str, str]) -> bool:
        a, b = (env[t] if k == "var" else t for k, t in (self.left, self.right))
        var = self.left[1] if self.left[0] == "var" else self.right[1]
        if var not in _PYTHON_VARS:
            return _OPS[self.op](a, b)
        x, y = _release(a), _release(b)
        width = max(len(x), len(y))
        return _OPS[self.op](_pad(x, width), _pad(y, width))


@dataclass(frozen=True)
class _BoolOp:
    op: str  # "and" / "or"
    parts: tuple[_Node, ...]

    def value(self, env: Mapping[str, str]) -> bool:
        values = (p.value(env) for p in self.parts)
        return all(values) if self.op == "and" else any(values)


_Node = _Compare | _BoolOp


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _pad(release: tuple[int, ...], width: int) -> tuple[int, ...]:
    return release + (0,) * (width - len(release))


class _Parser:
    """Recursive descent over the tokens of one marker."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.tokens = self._tokenize(marker)
        self.pos = 0

    def fail(self, why: str) -> UnsupportedLock:
        return UnsupportedLock(f"неподдерживаемый маркер {self.marker!r}: {why}")

    def _tokenize(self, marker: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        pos = 0
        while marker[pos:].strip():
            match = _TOKEN.match(marker, pos)
            if match is None or match.lastgroup is None:
                raise self.fail(f"нераспознанный текст с позиции {pos}")
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
            pos = match.end()
        return tokens

    def parse(self) -> _Node:
        node = self._or()
        if self.pos != len(self.tokens):
            raise self.fail(f"лишний токен {self.tokens[self.pos][1]!r}")
        return node

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise self.fail("неожиданный конец")
        self.pos += 1
        return token

    def _joined(self, op: str, part: Callable[[], _Node]) -> _Node:
        parts = [part()]
        while self._peek() == ("word", op):
            self.pos += 1
            parts.append(part())
        return parts[0] if len(parts) == 1 else _BoolOp(op, tuple(parts))

    def _or(self) -> _Node:
        return self._joined("or", self._and)

    def _and(self) -> _Node:
        return self._joined("and", self._atom)

    def _atom(self) -> _Node:
        if self._peek() == ("paren", "("):
            self.pos += 1
            node = self._or()
            if self._next() != ("paren", ")"):
                raise self.fail("нет закрывающей скобки")
            return node
        return self._compare()

    def _compare(self) -> _Compare:
        left = self._operand()
        kind, op = self._next()
        if kind != "op" or op not in _OPS:
            raise self.fail(f"оператор {op!r}")
        right = self._operand()
        variables = [s[1] for s in (left, right) if s[0] == "var"]
        if len(variables) != 1:
            raise self.fail("сравнение не «переменная и строка»")
        literal = right[1] if left[0] == "var" else left[1]
        if variables[0] in _PYTHON_VARS:
            if not _RELEASE.fullmatch(literal):
                raise self.fail(f"версия {literal!r} не вида N(.N)*")
        elif op not in ("==", "!="):
            raise self.fail(f"порядковое сравнение {variables[0]}")
        return _Compare(left, op, right)

    def _operand(self) -> tuple[str, str]:
        kind, text = self._next()
        if kind == "str":
            return "str", text[1:-1]
        if kind == "word" and text in _PYTHON_VARS | _STRING_VARS:
            return "var", text
        raise self.fail(f"операнд {text!r}")
