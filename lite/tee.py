"""Дублирование stdout/stderr `dfl` в кольцевой буфер строк — на уровне fd.

Подмена дескрипторов 1 и 2 (а не `sys.stdout`) ловит и вывод дочерних
процессов (`spec-runner`, `git`, `gh`). Байты идут на экран без изменений;
дочерний процесс видит pipe вместо tty — цвет по `isatty` может пропасть, это
принято (спека `2026-11-12-dfl-report-design.md` §5).
"""

from __future__ import annotations

import os
import sys
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

JOIN_TIMEOUT = 2.0  # с: дочитать вывод после восстановления fd


class OutputTail:
    """Последние строки вывода обоих потоков в порядке прихода."""

    def __init__(self, max_lines: int) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def add(self, line: bytes) -> None:
        """Добавить одну строку (без перевода строки)."""
        text = line.decode("utf-8", errors="replace").rstrip("\r")
        with self._lock:
            self._lines.append(text)

    def lines(self) -> list[str]:
        """Снимок буфера."""
        with self._lock:
            return list(self._lines)


@contextmanager
def tee_output(max_lines: int = 200) -> Iterator[OutputTail]:
    """Дублировать fd 1 и 2 в буфер на время блока; на выходе всё восстановить."""
    tail = OutputTail(max_lines)
    with ExitStack() as stack:
        for fd, stream in ((1, sys.stdout), (2, sys.stderr)):
            stack.enter_context(_tee_fd(fd, stream, tail))
        yield tail


@contextmanager
def _tee_fd(fd: int, stream, tail: OutputTail) -> Iterator[None]:
    stream.flush()
    saved = os.dup(fd)
    read_end, write_end = os.pipe()
    os.dup2(write_end, fd)
    os.close(write_end)
    reader = threading.Thread(target=_pump, args=(read_end, saved, tail), daemon=True)
    reader.start()
    try:
        yield
    finally:
        stream.flush()
        os.dup2(saved, fd)  # закрывает последний пишущий конец pipe у нас
        # Потомок, переживший вызов, держит свой пишущий конец — EOF не
        # придёт. Не ждём его: поток-демон умрёт с процессом, read_end
        # остаётся ему.
        reader.join(JOIN_TIMEOUT)
        if not reader.is_alive():
            os.close(read_end)
        os.close(saved)


def _pump(read_end: int, screen: int, tail: OutputTail) -> None:
    partial = b""
    while chunk := os.read(read_end, 65536):
        _write_all(screen, chunk)
        partial += chunk
        *done, partial = partial.split(b"\n")
        for line in done:
            tail.add(line)
    if partial:
        tail.add(partial)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]
