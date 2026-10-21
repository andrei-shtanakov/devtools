"""Canonical release digest, name validation and link resolution (spec 3.5, 3.3).

Every function here inspects trees that the builder can no longer write
(the final copy, administrative code, a trusted checkout). Effects: none.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

_CHUNK = 1 << 20
_MAX_HOPS = 40
_FORBIDDEN_CHARS = {"\n": "newline", "\0": "NUL", "/": "slash"}


class TreeError(Exception):
    """A tree or a stream is refused; the message names the problem."""


def check_name(name: bytes) -> str:
    """Return one path component as text, or refuse it.

    Refused: non-UTF-8, empty, `.`, `..`, and names with a newline, NUL or `/`.
    """
    try:
        text = name.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TreeError(f"name is not UTF-8: {name!r}") from exc
    if text in ("", ".", ".."):
        raise TreeError(f"forbidden name: {text!r}")
    for char, label in _FORBIDDEN_CHARS.items():
        if char in text:
            raise TreeError(f"name contains a {label}: {text!r}")
    return text


def nfc_key(name: str) -> str:
    """Key under which two names of one directory collide (APFS-style)."""
    return unicodedata.normalize("NFC", name)


def check_names(names: Iterable[bytes]) -> list[str]:
    """Check every name of one directory, including NFC-equal pairs."""
    seen: dict[str, str] = {}
    result: list[str] = []
    for raw in names:
        text = check_name(raw)
        key = nfc_key(text)
        if key in seen:
            raise TreeError(f"names equal after NFC: {seen[key]!r} and {text!r}")
        seen[key] = text
        result.append(text)
    return result


def tree_digest(
    root: Path, *, exclude: frozenset[str] = frozenset({"release.json"})
) -> str:
    """Return `sha256:<hex>` of the canonical tree encoding (spec 3.5).

    Normative encoding (a second implementation must match it byte for byte;
    `tests/df/test_digest.py::test_digest_golden_vector` pins it):

    - every object under `root` gets one record; `root` itself gets none.
      Links are never followed; types other than file, directory and symbolic
      link are refused, as are names failing `check_names` (non-UTF-8, `\n`,
      NUL, NFC-equal siblings) and link targets containing `\n`;
    - path: relative to `root`, components joined by `/`, no `./` prefix, no
      trailing `/`, names exactly as on disk (UTF-8, no NFC normalization);
    - record: `type \0 path \0 mode \0 payload \n` (no spaces), where `type` is
      `f`, `d` or `l`; `mode` is `x` for a file with any of `st_mode & 0o111`,
      else `-` (always `-` for `d` and `l`); `payload` is the lowercase hex
      SHA-256 of a file's content, the raw `readlink` bytes of a link, empty
      for a directory;
    - order: one global sort of all records by the UTF-8 bytes of the path
      (so `a` < `a-b` < `a/x`, since `-` is 0x2d and `/` is 0x2f), not a
      walk with sorted children;
    - result: `sha256:` + lowercase hex SHA-256 of the concatenated records.

    `exclude` holds exact relative paths whose records are skipped, whatever
    their type. Excluding a directory drops only its own record: its
    children are still digested (deliberately: exclusion is for single files
    such as `release.json`, never for hiding a subtree).
    """
    if not stat.S_ISDIR(_lstat(root).st_mode):
        raise TreeError(f"digest root is not a directory: {root}")
    records = sorted(_records(root, ""), key=lambda item: item[0])
    digest = hashlib.sha256()
    for rel, record in records:
        if rel.decode("utf-8") not in exclude:
            digest.update(record)
    return f"sha256:{digest.hexdigest()}"


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise TreeError(f"cannot stat {path}: {exc.strerror}") from exc


def _records(directory: Path, prefix: str) -> list[tuple[bytes, bytes]]:
    with os.scandir(directory) as entries:
        listing = list(entries)
    names = check_names(os.fsencode(entry.name) for entry in listing)
    out: list[tuple[bytes, bytes]] = []
    for entry, name in zip(listing, names, strict=True):
        rel = f"{prefix}{name}"
        out.append(_record(Path(entry.path), rel))
        if entry.is_dir(follow_symlinks=False):
            out.extend(_records(Path(entry.path), f"{rel}/"))
    return out


def _record(path: Path, rel: str) -> tuple[bytes, bytes]:
    st = _lstat(path)
    if stat.S_ISLNK(st.st_mode):
        kind, mode, payload = b"l", b"-", _link_text(path)
    elif stat.S_ISDIR(st.st_mode):
        kind, mode, payload = b"d", b"-", b""
    elif stat.S_ISREG(st.st_mode):
        kind = b"f"
        mode = b"x" if st.st_mode & 0o111 else b"-"
        payload = _file_sha256(path).encode()
    else:
        raise TreeError(f"unsupported file type: {rel}")
    encoded = rel.encode("utf-8")
    return encoded, b"\0".join((kind, encoded, mode, payload)) + b"\n"


def _link_text(path: Path) -> bytes:
    text = os.readlink(os.fsencode(path))
    if b"\n" in text:
        raise TreeError(f"link target contains a newline: {path}")
    return text


def _file_sha256(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TreeError(f"not a regular file: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


# --- link resolution (spec 3.3 item 4) -------------------------------------


def canonical_root(logical_root: PurePosixPath | str) -> tuple[str, ...]:
    """Components of a canonical absolute logical root, or a refusal.

    Canonical: absolute, at least one component, no `.`/`..`, no empty
    components (double or trailing `/`). Checked on the text, since
    `PurePosixPath` silently drops `.` and doubled slashes.
    """
    text = str(logical_root)
    parts = text.split("/")
    if (
        parts[0] != ""
        or len(parts) < 2
        or any(part in ("", ".", "..") for part in parts[1:])
    ):
        raise TreeError(f"logical root is not canonical: {text!r}")
    return tuple(parts[1:])


def resolve_link_in_tree(
    tree: Path, logical_root: PurePosixPath | str, link_rel: PurePosixPath
) -> PurePosixPath:
    """Resolve the link `tree/link_rel` lexically inside the tree.

    Absolute targets must lie under `logical_root`; `.`/`..` resolve
    lexically; intermediate links resolve by the same rule. Escape, a cycle
    (more than 40 hops) or a dangling target is a `TreeError`. Returns the
    final target relative to the tree.
    """
    root = canonical_root(logical_root)
    if link_rel.is_absolute() or any(p in ("", ".", "..") for p in link_rel.parts):
        raise TreeError(f"link path is not relative: {link_rel}")
    for depth in range(1, len(link_rel.parts)):
        prefix = tree.joinpath(*link_rel.parts[:depth])
        if not stat.S_ISDIR(_lstat(prefix).st_mode):
            raise TreeError(f"not a directory: {'/'.join(link_rel.parts[:depth])}")
    if not stat.S_ISLNK(_lstat(tree / link_rel).st_mode):
        raise TreeError(f"not a symbolic link: {link_rel}")
    return _Resolver(tree, root).run(list(link_rel.parts))


class _Resolver:
    """Stack-based lexical walk over one tree (no host realpath)."""

    def __init__(self, tree: Path, root: tuple[str, ...]) -> None:
        self.tree = tree
        self.root = root
        self.hops = 0

    def run(self, link: list[str]) -> PurePosixPath:
        stack = link[:-1]
        todo = self._substitute(stack, link)
        while todo:
            part = todo.pop(0)
            if part == ".":
                continue
            if part == "..":
                if not stack:
                    raise TreeError("link escapes the tree")
                stack.pop()
                continue
            stack.append(part)
            st = self._stat(stack)
            if stat.S_ISLNK(st.st_mode):
                link = list(stack)
                stack.pop()
                todo = self._substitute(stack, link) + todo
            elif todo and not stat.S_ISDIR(st.st_mode):
                raise TreeError(f"not a directory: {'/'.join(stack)}")
        return PurePosixPath(*stack) if stack else PurePosixPath(".")

    def _substitute(self, stack: list[str], link: list[str]) -> list[str]:
        """Components of the link text; absolute text resets `stack` in place."""
        self.hops += 1
        if self.hops > _MAX_HOPS:
            raise TreeError(f"too many link hops at {'/'.join(link)}")
        text = os.readlink(self.tree.joinpath(*link))
        parts = [p for p in text.split("/") if p]
        if not text.startswith("/"):
            return parts
        if tuple(parts[: len(self.root)]) != self.root:
            raise TreeError(f"absolute target outside the logical root: {text!r}")
        stack.clear()
        return parts[len(self.root) :]

    def _stat(self, stack: list[str]) -> os.stat_result:
        try:
            return os.lstat(self.tree.joinpath(*stack))
        except FileNotFoundError as exc:
            raise TreeError(f"dangling target: {'/'.join(stack)}") from exc
        except OSError as exc:
            raise TreeError(f"cannot resolve {'/'.join(stack)}: {exc}") from exc


def check_links(tree: Path, logical_root: PurePosixPath | str) -> list[str]:
    """Problems of every symbolic link in `tree`, as `"<rel>: <reason>"`.

    A non-canonical logical root is refused up front (`TreeError`).
    """
    canonical_root(logical_root)
    problems: list[str] = []
    for rel in _links(tree, PurePosixPath()):
        try:
            resolve_link_in_tree(tree, logical_root, rel)
        except TreeError as exc:
            problems.append(f"{rel}: {exc}")
    return problems


def _links(directory: Path, prefix: PurePosixPath) -> list[PurePosixPath]:
    with os.scandir(directory) as entries:
        listing = sorted(entries, key=lambda entry: entry.name)
    out: list[PurePosixPath] = []
    for entry in listing:
        rel = prefix / entry.name
        if entry.is_symlink():
            out.append(rel)
        elif entry.is_dir(follow_symlinks=False):
            out.extend(_links(Path(entry.path), rel))
    return out
