"""git-источник: файлы с origin/<default>, история, факты путей (I6).

Любой сбой git (ненулевой код, таймаут, нет бинаря) — состояние error, не
absent: absent означает только «пути на опубликованной ветке нет».
"""

from __future__ import annotations

import posixpath
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.inputs import RepoTodo
from conductor.manifest import FleetRepo
from conductor.model import SourceState

GIT_TIMEOUT = 120


def git(repo_dir: Path, *args: str) -> tuple[int, str, str]:
    """(код, stdout, stderr); таймаут → 124, нет бинаря → 127."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "git timeout"
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _verifies(repo_dir: Path, ref: str) -> bool:
    return git(repo_dir, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")[0] == 0


def default_ref(repo_dir: Path) -> str | None:
    """origin/HEAD, если указывает на коммит; иначе origin/master, origin/main."""
    code, out, _ = git(
        repo_dir, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"
    )
    candidates = [out.strip()] if code == 0 and out.strip() else []
    for ref in [*candidates, "origin/master", "origin/main"]:
        if _verifies(repo_dir, ref):
            return ref
    return None


def fetch(repo_dir: Path) -> str | None:
    """git fetch origin; None — успех, иначе текст ошибки."""
    code, _, err = git(repo_dir, "fetch", "-q", "origin")
    return None if code == 0 else (err.strip() or f"fetch exit {code}")


def read_file_at_origin(
    repo_dir: Path, path: str
) -> tuple[str | None, str | None, SourceState, str]:
    """(text, sha, state, detail) файла на origin/<default>."""
    ref = default_ref(repo_dir)
    if ref is None:
        return None, None, "error", "нет origin/<default>"
    code, out, err = git(repo_dir, "rev-parse", ref)
    if code != 0:
        return None, None, "error", err.strip()
    sha = out.strip()
    code, listed, err = git(repo_dir, "ls-tree", "--name-only", ref, "--", path)
    if code != 0:
        return None, sha, "error", err.strip() or "ls-tree failed"
    if not listed.strip():
        return None, sha, "absent", f"{path} нет на {ref}"
    code, text, err = git(repo_dir, "show", f"{ref}:{path}")
    if code != 0:
        return None, sha, "error", err.strip() or f"show exit {code}"
    return text, sha, "read", ref


def read_todo(repo: FleetRepo, root: Path, do_fetch: bool) -> RepoTodo:
    """TODO.md репо с origin; нет клона / сбой fetch — error."""
    repo_dir = root / repo.git_dir
    if not (repo_dir / ".git").exists():
        return RepoTodo(repo.key, None, None, "error", f"нет клона {repo_dir}")
    if do_fetch and (problem := fetch(repo_dir)) is not None:
        return RepoTodo(repo.key, None, None, "error", problem)
    text, sha, state, detail = read_file_at_origin(repo_dir, "TODO.md")
    return RepoTodo(repo.key, text, sha, state, detail)


class GitError(Exception):
    """git не ответил: результат неизвестен, а не «события не было» (I6)."""


def _checked(repo_dir: Path, *args: str) -> str:
    code, out, err = git(repo_dir, *args)
    if code != 0:
        raise GitError(f"{repo_dir.name}: git {args[0]}: {err.strip() or code}")
    return out


def last_commit_mentioning(repo_dir: Path, ref: str, token: str) -> str | None:
    """ISO-дата последнего коммита на ref с token в сообщении; сбой — GitError."""
    out = _checked(repo_dir, "log", "-1", "--format=%cI", "-F", f"--grep={token}", ref)
    return out.strip() or None


def line_since(repo_dir: Path, ref: str, line: int, path: str = "TODO.md") -> str:
    """ISO-дата коммита, последним менявшего строку line (git blame).

    Нижняя граница начала текущего ожидания конкретного пункта: правка строки
    «молодит» ожидание — это безопасная сторона (меньше ложных пинков).
    """
    out = _checked(
        repo_dir, "blame", "--porcelain", "-L", f"{line},{line}", ref, "--", path
    )
    for row in out.splitlines():
        if row.startswith("committer-time "):
            stamp = datetime.fromtimestamp(int(row.split()[1]), UTC)
            return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise GitError(f"{repo_dir.name}: blame без committer-time")


def ever_had(repo_dir: Path, ref: str, text: str, path: str = "TODO.md") -> str | None:
    """SHA последнего коммита, менявшего вхождения text в path; сбой — GitError."""
    out = _checked(repo_dir, "log", "-1", "--format=%H", "-S", text, ref, "--", path)
    return out.strip() or None


def path_fact(repo_dir: Path, path: str) -> dict[str, Any]:
    """{exists, sha, siblings} пути на origin/<default>; exists=None — ошибка."""
    ref = default_ref(repo_dir)
    if ref is None:
        return {"exists": None, "sha": None, "siblings": []}
    sha = git(repo_dir, "rev-parse", ref)[1].strip() or None
    code, listed, _ = git(repo_dir, "ls-tree", "--name-only", ref, "--", path)
    if code != 0:
        return {"exists": None, "sha": sha, "siblings": []}
    parts = path.split("/")
    versioned = next(
        (i for i, p in enumerate(parts) if p[:1] == "v" and p[1:].isdigit()), None
    )
    siblings: list[str] = []
    if versioned is not None:
        parent = "/".join(parts[:versioned])
        tree = f"{ref}:{parent}" if parent else ref
        code, names, _ = git(repo_dir, "ls-tree", "--name-only", tree)
        if code != 0:
            return {"exists": None, "sha": sha, "siblings": []}
        siblings = [posixpath.basename(n) for n in names.split()]
    return {"exists": bool(listed.strip()), "sha": sha, "siblings": siblings}
