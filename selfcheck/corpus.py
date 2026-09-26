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


def list_corpus(repo: Path, exclude: Sequence[str] = ()) -> list[str]:
    """Tracked + untracked-not-ignored regular files, minus ``exclude``."""
    raw = _git(
        repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    ).stdout
    names = sorted({p for p in raw.decode().split("\0") if p})
    result: list[str] = []
    for rel in names:
        if any(glob_match(p, rel) for p in exclude):
            continue
        full = repo / rel
        if full.is_symlink() or not full.is_file():
            continue
        result.append(rel)
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
    """Copy ``files`` and canaries into a fresh ``dest``, then make it read-only."""
    if not dest.is_absolute() or not repo.is_absolute():
        raise ValueError(f"materialize needs absolute paths: {repo} → {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()  # never reuse (FileExistsError)
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target)
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


def last_commit_ts(repo: Path, rel: str) -> int | None:
    """Unix time of the last commit touching ``rel``; None if never committed."""
    out = _git(repo, "log", "-1", "--format=%ct", "--", rel).stdout.strip()
    return int(out) if out else None


def repo_state(repo: Path) -> dict[str, Any]:
    """HEAD and dirtiness without ``git status`` (it may rewrite the index)."""
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
