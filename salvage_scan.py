#!/usr/bin/env python3
"""salvage_scan — детерминированный read-only salvage-скан флота. devtools#67.

Четыре класса обломков, которые за 25–28.08 ловились руками:
  * orphan-worktree   — зарегистрированный worktree с удалённым каталогом
                        или веткой (git worktree list --porcelain);
  * branch-no-pr      — локальная ветка без открытого PR, не влитая в
                        default (типичный след после squash-merge);
  * unpushed-default  — default-ветка впереди своего origin;
  * stale-lock        — залежавшийся *.lock в git-dir / gc.pid мёртвого
                        процесса.

Вывод — таблица «репо · класс · объект · возраст»; нормальный пустой
результат МОЛЧИТ (stdout пуст, exit 0). Известные осознанные исключения
(WAIVERS) скан помечает `[waived: …]`, но не скрывает и не чинит; только
waived-находки → exit 0.

Fail-honest: gh недоступен → кандидаты branch-no-pr показываются с пометкой
«PR state unknown», а не пропадают; ненайденный на диске чекаут — заметка в
stderr (его судьба — забота check-release-drift, не этого скана).

Exit: 0 — чисто либо только waived; 1 — есть actionable-находки;
2 — не разобраны манифест/аргументы. Только stdlib; Python 3.11+.

Использование:
    ./salvage_scan.py --workspace .. [--manifest path] [--lock-age-min 60]
                      [--no-github]
    make salvage
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from time import time

from clone_fleet import manifest_set

# Осознанные исключения: (git_dir, класс, префикс объекта, причина).
# Пустой префикс матчит любой объект класса. Скан их ПОМЕЧАЕТ, не чинит
# и не скрывает; снимать запись — когда причина устранена.
Waiver = tuple[str, str, str, str]
WAIVERS: list[Waiver] = [
    # Снапшот-коммиты derived/ волта держатся на master намеренно.
    (
        "prograph-vault",
        "unpushed-default",
        "",
        "derived-snapshots волта — осознанно, ждёт dispatcher#199",
    ),
    # Delivery-ветка снапшотов: PR не предполагается by design
    # (резолюция ecosystem-kb#98, адаптация — dispatcher#199).
    (
        "prograph-vault",
        "branch-no-pr",
        "derived-snapshots",
        "delivery-ветка снапшотов без PR by design (ecosystem-kb#98)",
    ),
]

GH_TIMEOUT_S = 60


class GitError(RuntimeError):
    """git завершился с ошибкой — скану нельзя молча читать это как «чисто»."""


@dataclass
class Finding:
    """Одна строка таблицы: репо · класс · объект · возраст (+ waiver)."""

    repo: str
    klass: str
    obj: str
    age_seconds: float | None
    waived: str | None = None


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        raise GitError(
            f"git -C {repo} {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.rstrip("\n")


def _ref_exists(repo: Path, ref: str) -> bool:
    return _run_git(repo, "rev-parse", "--verify", "-q", ref).returncode == 0


def _is_ancestor(repo: Path, ref: str, of: str) -> bool:
    result = _run_git(repo, "merge-base", "--is-ancestor", ref, of)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise GitError(
        f"git -C {repo} merge-base --is-ancestor {ref} {of} failed "
        f"(exit {result.returncode}): {result.stderr.strip()}"
    )


def default_branch(repo: Path) -> str | None:
    """Имя default-ветки: origin/HEAD, иначе master/main, иначе None."""
    result = _run_git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    prefix = "refs/remotes/origin/"
    ref = result.stdout.strip()
    if result.returncode == 0 and ref.startswith(prefix):
        return ref[len(prefix) :]
    for name in ("master", "main"):
        if _ref_exists(repo, f"refs/heads/{name}"):
            return name
    return None


# ── класс 1: orphan worktrees ───────────────────────────────────────────


def _worktree_blocks(porcelain: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in porcelain.splitlines():
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        blocks.append(current)
    return blocks


def _worktree_admin_age(repo: Path, wt_path: str, now: float) -> float | None:
    common = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    admin = common / "worktrees" / Path(wt_path).name
    if not admin.exists():
        return None
    return now - admin.stat().st_mtime


def scan_worktrees(repo: Path, *, now: float) -> list[Finding]:
    """Orphan-worktree: prunable по git, удалённый каталог, снесённая ветка."""
    blocks = _worktree_blocks(git(repo, "worktree", "list", "--porcelain"))
    findings: list[Finding] = []
    for block in blocks[1:]:  # первый блок — главный worktree
        path = block.get("worktree", "?")
        reason: str | None = None
        if "prunable" in block:
            reason = block["prunable"] or "prunable"
        elif not Path(path).exists():
            reason = "каталог удалён"
        elif "branch" in block and not _ref_exists(repo, block["branch"]):
            reason = f"ветка снесена: {block['branch']}"
        if reason is None:
            continue
        findings.append(
            Finding(
                repo.name,
                "orphan-worktree",
                f"{path} ({reason})",
                _worktree_admin_age(repo, path, now),
            )
        )
    return findings


# ── класс 2: ветки без открытого PR и не влитые в default ───────────────


def scan_branches(
    repo: Path,
    default: str,
    *,
    now: float,
    pr_heads: set[str] | None,
) -> list[Finding]:
    """branch-no-pr; pr_heads=None (gh недоступен) — явная пометка, не молчание."""
    raw = git(
        repo,
        "for-each-ref",
        "refs/heads",
        "--format=%(refname:short)\x1f%(committerdate:unix)",
    )
    merge_targets = [default]
    if _ref_exists(repo, f"refs/remotes/origin/{default}"):
        merge_targets.append(f"origin/{default}")
    findings: list[Finding] = []
    for line in raw.splitlines():
        name, _, stamp = line.partition("\x1f")
        if name == default:
            continue
        if any(_is_ancestor(repo, name, target) for target in merge_targets):
            continue
        if pr_heads is not None and name in pr_heads:
            continue
        obj = name if pr_heads is not None else f"{name} (PR state unknown)"
        findings.append(
            Finding(repo.name, "branch-no-pr", obj, now - int(stamp))
        )
    return findings


# ── класс 3: unpushed-коммиты default-ветки ─────────────────────────────


def scan_unpushed(repo: Path, default: str, *, now: float) -> list[Finding]:
    """unpushed-default: возраст — самый СТАРЫЙ незапушенный коммит."""
    upstream = f"origin/{default}"
    if not _ref_exists(repo, f"refs/remotes/{upstream}"):
        return []  # набор-vs-диск и странные клоны — забота release-drift
    span = f"{upstream}..{default}"
    count = int(git(repo, "rev-list", "--count", span))
    if count == 0:
        return []
    stamps = git(repo, "log", "--format=%ct", span).splitlines()
    oldest = int(stamps[-1])
    return [
        Finding(
            repo.name,
            "unpushed-default",
            f"{default}: {count} unpushed commit(s)",
            now - oldest,
        )
    ]


# ── класс 4: stale locks ────────────────────────────────────────────────


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def scan_locks(repo: Path, *, now: float, lock_age_s: float) -> list[Finding]:
    """stale-lock: *.lock старше порога; gc.pid мёртвого процесса — сразу."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    findings: list[Finding] = []
    for lock in sorted(git_dir.glob("*.lock")):
        age = now - lock.stat().st_mtime
        if age > lock_age_s:
            findings.append(Finding(repo.name, "stale-lock", lock.name, age))
    gc_pid = git_dir / "gc.pid"
    if gc_pid.exists():
        age = now - gc_pid.stat().st_mtime
        try:
            pid = int(gc_pid.read_text().split()[0])
        except (ValueError, IndexError):
            pid = None
        stale = not _pid_alive(pid) if pid is not None else age > lock_age_s
        if stale:
            findings.append(
                Finding(repo.name, "stale-lock", "gc.pid (процесс мёртв)", age)
            )
    return findings


# ── сборка по репо и по флоту ───────────────────────────────────────────


def scan_repo(
    repo: Path,
    *,
    now: float,
    pr_heads: set[str] | None,
    lock_age_s: float,
) -> list[Finding]:
    """Все четыре класса для одного чекаута."""
    findings = scan_worktrees(repo, now=now)
    default = default_branch(repo)
    if default is not None:
        findings += scan_branches(repo, default, now=now, pr_heads=pr_heads)
        findings += scan_unpushed(repo, default, now=now)
    findings += scan_locks(repo, now=now, lock_age_s=lock_age_s)
    return findings


def _waiver_reason(finding: Finding, waivers: list[Waiver]) -> str | None:
    for repo, klass, obj_prefix, reason in waivers:
        if (
            finding.repo == repo
            and finding.klass == klass
            and finding.obj.startswith(obj_prefix)
        ):
            return reason
    return None


def apply_waivers(
    findings: list[Finding], waivers: list[Waiver]
) -> list[Finding]:
    """Пометить известные осознанные исключения — не скрывая находку."""
    return [
        dataclasses.replace(f, waived=_waiver_reason(f, waivers))
        for f in findings
    ]


# ── GitHub: open-PR heads ───────────────────────────────────────────────


def github_slug(repo_url: str) -> str | None:
    """`owner/repo` из GitHub-URL манифеста (ssh или https); прочее — None."""
    for prefix in ("git@github.com:", "https://github.com/"):
        if repo_url.startswith(prefix):
            slug = repo_url[len(prefix) :]
            return slug.removesuffix(".git") or None
    return None


def fetch_pr_heads(slug: str) -> set[str] | None:
    """headRefName открытых PR через gh; любая беда → None (unavailable)."""
    cmd = [
        "gh", "pr", "list", "-R", slug, "--state", "open",
        "--json", "headRefName", "--limit", "200",
    ]  # fmt: skip
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return {row["headRefName"] for row in json.loads(proc.stdout)}
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


# ── вывод ───────────────────────────────────────────────────────────────


def humanize_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    if seconds >= 86400:
        return f"{int(seconds // 86400)}d"
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h"
    if seconds >= 60:
        return f"{int(seconds // 60)}m"
    return "<1m"


def render_table(findings: list[Finding], *, host: str) -> str:
    """Таблица «репо · класс · объект · возраст» с host-строкой (инвариант 5)."""
    header = ("репо", "класс", "объект", "возраст")
    rows = [
        (f.repo, f.klass, f.obj, humanize_age(f.age_seconds))
        for f in findings
    ]
    widths = [
        max(len(cell) for cell in column)
        for column in zip(header, *rows)
    ]
    lines = [f"# salvage-scan · host={host}"]
    for row, finding in zip([header, *rows], [None, *findings]):
        line = "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
        if finding is not None and finding.waived:
            line += f"  [waived: {finding.waived}]"
        lines.append(line)
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="workspace-manifest.toml (по умолчанию — из зонтика workspace)",
    )
    parser.add_argument("--lock-age-min", type=float, default=60.0)
    parser.add_argument(
        "--no-github",
        action="store_true",
        help="не ходить в gh: ветки показываются с пометкой PR state unknown",
    )
    args = parser.parse_args(argv)
    manifest = args.manifest or (
        args.workspace / "ai-orchestrators-workspace" / "workspace-manifest.toml"
    )
    try:
        repos = manifest_set(manifest)
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"salvage-scan: манифест не прочитан/не разобран: {err}",
              file=sys.stderr)
        return 2

    now = time()
    findings: list[Finding] = []
    missing: list[str] = []
    for git_dir, repo_url in sorted(repos.items()):
        repo = args.workspace / git_dir
        if not (repo / ".git").exists():
            missing.append(git_dir)
            continue
        pr_heads: set[str] | None = None
        if not args.no_github:
            slug = github_slug(repo_url)
            pr_heads = fetch_pr_heads(slug) if slug else None
            if pr_heads is None:
                print(
                    f"[SALVAGE-GH-UNAVAILABLE] {git_dir}: ветки показаны "
                    "без PR-фильтра",
                    file=sys.stderr,
                )
        findings += scan_repo(
            repo,
            now=now,
            pr_heads=pr_heads,
            lock_age_s=args.lock_age_min * 60,
        )
    if missing:
        print(
            f"salvage-scan: не начекаучены (см. make release-drift): "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )

    findings = apply_waivers(findings, WAIVERS)
    if findings:
        print(render_table(findings, host=platform.node()))
    return 1 if any(f.waived is None for f in findings) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitError as err:
        print(f"salvage-scan: {err}", file=sys.stderr)
        sys.exit(2)
