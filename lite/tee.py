"""Дублирование вывода `dfl` в кольцевой буфер строк — на уровне Python.

`sys.stdout`/`sys.stderr` подменяются обёрткой: запись идёт в настоящий поток
без изменений и попутно складывается в буфер. Дескрипторы 1 и 2 не трогаются,
потоков и pipe нет: дочерние процессы пишут на экран как прежде (живой вывод,
порядок, tty), но в буфер не попадают. Перехват на уровне fd через pipe и
поток отвергнут финальным ревью (зависание при закрытом экране, запись в
переиспользованный fd потомком, буферизация и порядок вывода) — спека
`2026-11-12-dfl-report-design.md` §5. Перенаправление fd в обычный файл лога
этих проблем не имеет и принято — `lite.runlog.captured`, спека
`2026-11-13-dfl-clean-output-design.md` §4.1.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO


class OutputTail:
    """Последние строки вывода обоих потоков в порядке записи."""

    def __init__(self, max_lines: int) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._partial = {"out": "", "err": ""}

    def feed(self, stream: str, text: str) -> None:
        """Добавить записанный в поток `stream` текст (строки — по `\\n`)."""
        *done, self._partial[stream] = (self._partial[stream] + text).split("\n")
        self._lines.extend(line.rstrip("\r") for line in done)

    def lines(self) -> list[str]:
        """Снимок буфера с недописанными строками в конце."""
        rest = [p for p in self._partial.values() if p]
        return [*self._lines, *rest][-(self._lines.maxlen or 0) :]


class _Tee:
    """Поток, пишущий в настоящий и в буфер; прочее — к настоящему."""

    def __init__(self, real: TextIO, tail: OutputTail, name: str) -> None:
        self._real = real
        self._tail = tail
        self._name = name

    def write(self, text: str) -> int:
        self._tail.feed(self._name, text)  # раньше записи: сбой экрана не теряет
        return self._real.write(text)

    def writelines(self, lines: list[str]) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, attr: str):
        return getattr(self._real, attr)


@contextmanager
def tee_output(max_lines: int = 200) -> Iterator[OutputTail]:
    """Дублировать `sys.stdout`/`sys.stderr` в буфер на время блока."""
    tail = OutputTail(max_lines)
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(real_out, tail, "out")  # type: ignore[assignment]
    sys.stderr = _Tee(real_err, tail, "err")  # type: ignore[assignment]
    try:
        yield tail
    finally:
        sys.stdout, sys.stderr = real_out, real_err
