"""Безопасный ввод-вывод раннера для песочницы авторского агента (TASK-001).

Единственный способ, которым раннер касается каталога вызова агента и пишет
файл узла в бандл (дизайн `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
§15, замер F27). Чистая библиотека: ни профиля, ни запуска агента.

- Корень канонизирует вызывающий (`os.path.realpath`) ОДИН раз; каждое
  открытие идёт с `O_NOFOLLOW_ANY` — ядро Darwin отказывает на ссылке в
  ЛЮБОМ компоненте пути одним вызовом, без гонки «проверил компонент →
  подменили». Неканонический корень (`/var/…` → `/private/var/…`) поэтому
  тоже отказ.
- Чтение: `O_NONBLOCK` (FIFO не вешает открытие), `fstat` открытого
  дескриптора — только обычный файл с `st_nlink == 1`, не больше
  `limit + 1` байт и с ограничением времени.
- Запись: родители — цепочкой дескрипторов, временный файл `O_EXCL` в
  закреплённом каталоге, `rename` поверх конечного имени: висячая ссылка
  заменяется сама (её цель не создаётся), hardlink получает новый inode.

Модуль — в защищённой цепочке песочницы (authority-root, шаг H1). Граница
гарантий — дизайн §19 п.3.
"""

from __future__ import annotations

import errno
import functools
import os
import stat
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Флаг ядра Darwin; на другой ОС его нет — любой вызов отказывает.
NOFOLLOW_ANY: int | None = getattr(os, "O_NOFOLLOW_ANY", None)

_CHUNK = 65536
#: Режим опубликованного файла узла — не зависит от umask раннера.
_FILE_MODE = 0o644


class SafeIOError(RuntimeError):
    """Операция небезопасна или невозможна — вызывающий не продолжает.

    Единственный тип отказа модуля: любой `OSError` публичной функции
    (гонка с потомком агента, нет места, права) приходит как SafeIOError.
    """


def _guarded[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Сырой `OSError` из тела публичной функции → `SafeIOError`."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except SafeIOError:
            raise
        except OSError as exc:
            raise SafeIOError(f"{func.__name__}: {exc.strerror or exc}") from exc

    return wrapper


@dataclass(frozen=True)
class Entry:
    """Элемент обхода: путь относительно стартового каталога и тип."""

    path: str
    kind: str  # "dir" | "file" | "symlink" | "other"


def _flag() -> int:
    if NOFOLLOW_ANY is None:
        raise SafeIOError(
            "безопасный ввод-вывод песочницы — только macOS (нет O_NOFOLLOW_ANY)"
        )
    return NOFOLLOW_ANY


def _parts(rel: str | PurePosixPath) -> tuple[str, ...]:
    """Компоненты относительного пути; `.`, `..`, пустые и абсолютные — отказ."""
    text = str(rel)
    if text in ("", "."):
        return ()
    if text.startswith("/"):
        raise SafeIOError(f"путь {text!r} абсолютный")
    parts = tuple(text.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise SafeIOError(f"путь {text!r} с пустым, `.` или `..` компонентом")
    return parts


def _join(root: Path, parts: tuple[str, ...]) -> str:
    return os.path.join(str(root), *parts)


def _open(
    path: str,
    flags: int,
    dir_fd: int | None = None,
    *,
    any_component: bool = True,
    mode: int = 0o777,
) -> int:
    """`os.open` с `O_NOFOLLOW_ANY`; `any_component=False` — для создания
    одного имени в закреплённом каталоге (`O_NOFOLLOW_ANY` с `O_CREAT` ядро
    отвергает `EINVAL`, а единственный компонент закрывает `O_NOFOLLOW`)."""
    extra = _flag() if any_component else 0
    try:
        return os.open(path, flags | extra, mode, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SafeIOError(f"{path!r}: ссылка в пути") from exc
        raise SafeIOError(f"{path!r}: {exc.strerror}") from exc


@_guarded
def read_regular(
    root: Path, rel: str | PurePosixPath, *, limit: int, timeout: float = 10.0
) -> bytes:
    """Байты обычного файла `root/rel` не длиннее `limit`; иначе SafeIOError."""
    path = _join(root, _parts(rel))
    fd = _open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafeIOError(f"{path!r}: не обычный файл")
        if st.st_nlink != 1:
            raise SafeIOError(f"{path!r}: st_nlink={st.st_nlink}, ждали 1")
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            if time.monotonic() > deadline:
                raise SafeIOError(f"{path!r}: время чтения истекло ({timeout} с)")
            chunk = os.read(fd, min(_CHUNK, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise SafeIOError(f"{path!r}: больше потолка {limit} байт")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


@_guarded
def list_tree(root: Path, rel: str | PurePosixPath) -> list[Entry]:
    """Всё под `root/rel` без следования ссылкам: ссылка — элемент, не вход."""
    start = _parts(rel)
    out: list[Entry] = []

    def walk(dir_fd: int, prefix: tuple[str, ...]) -> None:
        for name in sorted(os.listdir(dir_fd)):
            mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
            kind = _kind(mode)
            out.append(Entry("/".join((*prefix, name)), kind))
            if kind == "dir":
                child = _open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
                try:
                    walk(child, (*prefix, name))
                finally:
                    os.close(child)

    fd = _open(_join(root, start), os.O_RDONLY | os.O_DIRECTORY)
    try:
        walk(fd, ())
    finally:
        os.close(fd)
    return out


def _dir_chain(root: Path, parts: tuple[str, ...]) -> int:
    """Дескриптор `root/parts`, недостающие каталоги создаются; ссылка — отказ."""
    fd = _open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    for part in parts:
        try:
            os.mkdir(part, 0o755, dir_fd=fd)
        except FileExistsError:
            pass
        except OSError as exc:
            os.close(fd)
            raise SafeIOError(f"{part!r}: {exc.strerror}") from exc
        try:
            child = _open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        finally:
            os.close(fd)
        fd = child
    return fd


@_guarded
def ensure_dirs(root: Path, rel: str | PurePosixPath) -> None:
    """Создать `root/rel` и родителей цепочкой дескрипторов."""
    os.close(_dir_chain(root, _parts(rel)))


@_guarded
def create_dir(root: Path, rel: str | PurePosixPath) -> None:
    """Создать НОВЫЙ каталог `root/rel`; существующее имя — SafeIOError.

    Родители создаются цепочкой дескрипторов (как `ensure_dirs`), последний
    компонент — `mkdir(dir_fd=)` без `exist_ok`: свежий каталог не содержит
    заранее положенных ссылок и hardlink по построению (дизайн §12 п.1).
    """
    parts = _parts(rel)
    if not parts:
        raise SafeIOError("создание каталога без имени")
    *parents, name = parts
    dir_fd = _dir_chain(root, tuple(parents))
    try:
        os.mkdir(name, 0o755, dir_fd=dir_fd)
    except FileExistsError as exc:
        raise SafeIOError(f"{'/'.join(parts)!r}: уже существует") from exc
    finally:
        os.close(dir_fd)


@_guarded
def publish(root: Path, rel: str | PurePosixPath, data: bytes) -> None:
    """Записать `data` в `root/rel` новым inode поверх конечного имени."""
    parts = _parts(rel)
    if not parts:
        raise SafeIOError("публикация без имени файла")
    *parents, name = parts
    dir_fd = _dir_chain(root, tuple(parents))
    tmp = f".{name}.tmp-{uuid.uuid4().hex}"
    try:
        try:
            leaf = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            leaf = None
        if leaf is not None and stat.S_ISDIR(leaf.st_mode):
            raise SafeIOError(f"{'/'.join(parts)!r}: на месте узла каталог")
        fd = _open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            dir_fd=dir_fd,
            any_component=False,
            mode=_FILE_MODE,
        )
        os.fchmod(fd, _FILE_MODE)  # umask не ослабляет и не усиливает режим
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError as exc:
            raise SafeIOError(f"{'/'.join(parts)!r}: {exc.strerror}") from exc
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(dir_fd)


def _fold(text: str) -> str:
    """Ключ сравнения имён на томе APFS: регистр и форма Unicode не важны."""
    return unicodedata.normalize("NFD", text).casefold()


def paths_overlap(a: PurePosixPath, b: PurePosixPath) -> bool:
    """`a` и `b` совпадают или один вложен в другой (без учёта регистра)."""
    fa, fb = PurePosixPath(_fold(str(a))), PurePosixPath(_fold(str(b)))
    return fa == fb or fa in fb.parents or fb in fa.parents


def validate_bundle_dir(
    target_dir: Path, bundle_dir: str, *, git_dirs: tuple[Path, ...]
) -> PurePosixPath:
    """Контракт назначения §15: `bundle_dir` относительно канонического корня.

    Непустой относительный, без `..`, пустых и абсолютных компонентов, не
    `.`, без компонента `.git` (в любом регистре и форме Unicode — том APFS
    по умолчанию их не различает); существующая часть пути — без ссылок
    (канонизация: `realpath` равен лексическому пути); строго внутри
    `target_dir` и не пересекается с gitdir/common dir цели ни по имени (без
    учёта регистра и формы Unicode). Иначе SafeIOError —
    до вызова агента.
    """
    root = str(target_dir)
    if not os.path.isabs(root) or os.path.realpath(root) != root:
        raise SafeIOError(f"корень цели {root!r} не канонический (realpath)")
    if bundle_dir in ("", "."):
        raise SafeIOError("bundle_dir пустой или `.`")
    parts = _parts(bundle_dir)
    if any(_fold(part) == ".git" for part in parts):
        raise SafeIOError(f"bundle_dir {bundle_dir!r} с компонентом .git")
    full = PurePosixPath(root, *parts)
    if _fold(os.path.realpath(str(full))) != _fold(str(full)):
        raise SafeIOError(
            f"bundle_dir {bundle_dir!r}: ссылка в существующей части пути"
        )
    for git_dir in git_dirs:
        g = PurePosixPath(os.path.realpath(git_dir))
        if paths_overlap(full, g):
            raise SafeIOError(f"bundle_dir {bundle_dir!r} пересекается с gitdir {g}")
    return PurePosixPath(*parts)
