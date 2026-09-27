"""Corpus listing, the read-only copy and git reads (spec §1.3, §1.4)."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from selfcheck.roles import glob_match


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, env=env
    )


def shown(rel: str) -> str:
    """A path as the run carries it: surrogates of non-UTF-8 bytes become
    U+FFFD (the raw name is still what opens the file)."""
    return rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def list_corpus(repo: Path, exclude: Sequence[str] = ()) -> list[str]:
    """Tracked + untracked-not-ignored regular files, minus ``exclude``, by
    their shown names (#409): ``materialize`` copies each raw file under that
    name, so every probe, anchor and finding id sees valid UTF-8 only."""
    return sorted(corpus_names(repo, exclude))


def corpus_names(repo: Path, exclude: Sequence[str] = ()) -> dict[str, str]:
    """shown name → raw name of the corpus. Two raw names sharing one shown
    name fail the listing: dropping either would drop its references."""
    listing = _git(
        repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    ).stdout
    result: dict[str, str] = {}
    for raw in sorted({os.fsdecode(p) for p in listing.split(b"\0") if p}):
        rel = shown(raw)
        if any(glob_match(p, rel) for p in exclude):
            continue
        full = repo / raw
        if full.is_symlink() or not full.is_file():
            continue
        if rel in result:
            raise OSError(f"non-UTF-8 names collide as {rel!r}: rename one")
        result[rel] = raw
    return result


def _chmod_tree(root: Path, *, writable: bool) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        os.chmod(path, mode | stat.S_IWUSR if writable else mode & ~0o222)


def materialize(
    repo: Path, files: Sequence[str], dest: Path, extra_files: Mapping[str, str]
) -> None:
    """Copy ``files`` (shown names) and canaries into a fresh ``dest``, then make
    it read-only. A non-UTF-8 source is copied under its shown name."""
    if not dest.is_absolute() or not repo.is_absolute():
        raise ValueError(f"materialize needs absolute paths: {repo} → {dest}")
    raw = corpus_names(repo) if any("\ufffd" in rel for rel in files) else {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()  # never reuse (FileExistsError)
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / raw.get(rel, rel), target)
    for rel, text in extra_files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _chmod_tree(dest, writable=False)


def release(dest: Path) -> str | None:
    """Restore write bits and delete; return a warning instead of raising."""
    try:
        _chmod_tree(dest, writable=True)
        shutil.rmtree(dest)
    except OSError as exc:
        return f"cleanup failed: {dest}: {exc}"
    return None


def raw_path(repo: Path, rel: str) -> str:
    """The on-disk name of the shown corpus path ``rel`` (#420): a read of the
    source checkout must use it. Only a U+FFFD name needs the lookup."""
    if "\ufffd" not in rel:
        return rel
    return corpus_names(repo).get(rel, rel)


def last_commit_ts(repo: Path, rel: str) -> int | None:
    """Unix time of the last commit touching ``rel`` (a shown corpus path);
    None if never committed."""
    raw = raw_path(repo, rel)
    out = _git(repo, "log", "-1", "--format=%ct", "--", raw).stdout.strip()
    return int(out) if out else None


def repo_state(repo: Path) -> dict[str, Any]:
    """HEAD and dirtiness without ``git status`` (it may rewrite the index)."""
    # TODO: diff-index reports stat-only changes (touch, chmod) as dirty; S2 uses
    # `status --porcelain` with GIT_OPTIONAL_LOCKS=0, which neither lies nor writes
    # (selfcheck/fleet/reader.py). Kept as is here: it only feeds the report.
    head = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    changed = _git(repo, "diff-index", "--quiet", "HEAD", "--", check=False).returncode
    others = _git(repo, "ls-files", "-z", "--others", "--exclude-standard").stdout
    return {"head": head, "dirty": changed != 0 or bool(others)}


def snapshot_hashes(root: Path) -> dict[str, str]:
    """sha1 of every regular file under ``root`` (incl. ignored and .git)."""
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            result[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
    return result
