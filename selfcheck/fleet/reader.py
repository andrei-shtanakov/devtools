"""Read one fleet repo: texts, binaries, problems and git state (spec §9.2, §9.6).

Read-only by construction: no fetch, no index writes. Every git call runs
with the caller's ``GIT_*`` variables removed and ``GIT_OPTIONAL_LOCKS=0``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STALE = ("no-origin-head", "not-default-branch", "detached", "behind", "ahead", "dirty")
GITLINK = "160000"
SNIFF = 8192
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


@dataclass(frozen=True)
class RepoState:
    """Where a fleet checkout stands against its default branch."""

    head: str | None
    branch: str | None
    default: str | None
    behind: int | None
    ahead: int | None
    dirty: bool
    fetched_at: str | None
    stale: tuple[str, ...]


@dataclass
class FleetRepo:
    """One fleet repo as the fleet channels see it."""

    name: str
    path: Path
    texts: dict[str, str] = field(default_factory=dict)
    binary: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)
    state: RepoState | None = None


def git_env() -> dict[str, str]:
    """The caller's environment without ``GIT_*``, plus optional locks off."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_OPTIONAL_LOCKS": "0"}


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        env=git_env(),
        check=False,
    )


def _out(path: Path, *args: str) -> str | None:
    proc = _git(path, *args)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def decode(data: bytes) -> str | None:
    """Text of a file, or None when it is binary (NUL without a BOM)."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    if b"\x00" in data[:SNIFF]:
        return None
    return data.decode("utf-8", errors="replace")


def _fetched_at(path: Path) -> str | None:
    rel = _out(path, "rev-parse", "--git-path", "FETCH_HEAD")
    if not rel:
        return None
    fetch_head = path / rel
    if not fetch_head.is_file():
        return None
    return datetime.fromtimestamp(fetch_head.stat().st_mtime, UTC).isoformat()


def stale_reasons(path: Path) -> RepoState:
    """Branch, default branch, divergence and dirtiness — the one source of
    ``stale`` (spec §9.6 п.5)."""
    head = _out(path, "rev-parse", "HEAD")
    branch = _out(path, "symbolic-ref", "-q", "--short", "HEAD") or None
    origin_head = _out(path, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    default = origin_head.removeprefix("refs/remotes/origin/") if origin_head else None
    if default and _out(path, "rev-parse", "-q", "--verify", origin_head or "") is None:
        default = None  # origin/HEAD points at a ref that no longer exists
    behind = ahead = None
    if default:
        counts = _out(
            path, "rev-list", "--left-right", "--count", f"HEAD...origin/{default}"
        )
        if counts:
            left, right = counts.split()
            ahead, behind = int(left), int(right)
    status = _git(path, "status", "--porcelain", "-z")
    dirty = status.returncode != 0 or bool(status.stdout)
    reasons: list[str] = []
    if default is None:
        reasons.append("no-origin-head")
    elif branch is None:
        reasons.append("detached")
    elif branch != default:
        reasons.append("not-default-branch")
    if default is not None and behind:
        reasons.append("behind")
    if default is not None and ahead:
        reasons.append("ahead")
    if dirty:
        reasons.append("dirty")
    return RepoState(
        head, branch, default, behind, ahead, dirty, _fetched_at(path), tuple(reasons)
    )


def parse_entries(staged: bytes, others: bytes) -> dict[str, str]:
    """rel → git mode ('' for untracked) from ``ls-files -z`` output. Paths
    decode like the filesystem does (surrogates for non-UTF-8 bytes), so a
    Latin-1 name in a neighbour's index never raises."""
    modes: dict[str, str] = {}
    for record in staged.split(b"\0"):
        if record:
            meta, _, rel = record.partition(b"\t")
            modes[os.fsdecode(rel)] = meta.split()[0].decode()
    for rel in others.split(b"\0"):
        if rel:
            modes.setdefault(os.fsdecode(rel), "")
    return modes


def _entries(path: Path) -> tuple[dict[str, str], str | None]:
    """rel → git mode ('' for untracked), or an ls-files error."""
    staged = _git(path, "ls-files", "-z", "--stage")
    others = _git(path, "ls-files", "-z", "--others", "--exclude-standard")
    for proc in (staged, others):
        if proc.returncode != 0:
            return {}, proc.stderr.decode(errors="replace").strip()[:200]
    return parse_entries(staged.stdout, others.stdout), None


def _inside(target: str, root: str) -> bool:
    return target == root or target.startswith(root + os.sep)


def read_repo(path: Path, name: str) -> FleetRepo:
    """Read every tracked and untracked-not-ignored text file of ``path``."""
    repo = FleetRepo(name, path)
    if not (path / ".git").exists():
        repo.problems.append(("missing", ""))
        return repo
    modes, error = _entries(path)
    if error is not None:
        repo.problems.append(("ls-files", error))
        return repo
    root = os.path.realpath(path)
    for rel in sorted(modes):
        full = path / rel
        if modes[rel] == GITLINK:
            repo.problems.append(("submodule", rel))
            continue
        if os.path.islink(full):
            if not _inside(os.path.realpath(full), root):
                repo.problems.append(("symlink-out", rel))
            continue
        try:
            data = full.read_bytes()
        except OSError:  # incl. a sparse / skip-worktree file absent on disk
            repo.problems.append(("unreadable", rel))
            continue
        text = decode(data)
        if text is None:
            repo.binary += 1
        else:
            repo.texts[rel] = text
    if not repo.texts:
        repo.problems.append(("empty", ""))
    repo.state = stale_reasons(path)
    repo.problems += [("stale", reason) for reason in repo.state.stale]
    return repo
