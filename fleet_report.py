#!/usr/bin/env python3
"""fleet_report.py — markdown-отчёт о состоянии флота из snapshot-JSON.

Вход:  JSON от `github-checker snapshot` (stdin или --input file.json).
Выход: markdown в stdout или в файл (--out FILE | --out DIR, имя
       fleet-<host>-<YYYY-MM-DD-HHMM>.md).

Только stdlib; Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _local(repo: dict[str, Any]) -> dict[str, Any]:
    return repo.get("local") or {}


def _gh(repo: dict[str, Any]) -> dict[str, Any] | None:
    return repo.get("github")


def _ahead_behind(repo: dict[str, Any]) -> str:
    local = _local(repo)
    ahead, behind = local.get("ahead"), local.get("behind")
    if ahead is None and behind is None:
        return "нет upstream"
    return f"↑{ahead or 0} ↓{behind or 0}"


def _needs_attention(repo: dict[str, Any]) -> list[str]:
    """Причины, по которым репо попадает в раздел «требует внимания»."""
    reasons: list[str] = []
    local = _local(repo)
    if local.get("error"):
        reasons.append(f"git: {local['error']}")
    if local.get("dirty"):
        reasons.append("незакоммиченные изменения")
    if local.get("ahead"):
        reasons.append(f"впереди upstream на {local['ahead']}")
    if local.get("behind"):
        reasons.append(f"отстаёт от upstream на {local['behind']}")
    gh = _gh(repo)
    if gh:
        if gh.get("error"):
            reasons.append(f"github: {gh['error']}")
        pulls = gh.get("pulls") or []
        if pulls:
            reasons.append(f"открытых PR: {len(pulls)}")
        issues = gh.get("issues")
        if issues:
            reasons.append(f"открытых issues: {len(issues)}")
        alerts = gh.get("alerts")
        if alerts:
            reasons.append(f"security alerts: {alerts}")
    return reasons


def render(snapshot: dict[str, Any]) -> str:
    host = snapshot.get("host", "?")
    generated = snapshot.get("generated_at", "?")
    workspace = snapshot.get("workspace", "?")
    gh_error = snapshot.get("gh_error")
    repos = snapshot.get("repos", [])

    lines: list[str] = [
        "---",
        f"title: Fleet report — {host}",
        "type: fleet-report",
        f"host: {host}",
        f"generated: {generated}",
        "source: github-checker snapshot",
        "writer: devtools/fleet_report.py",
        "---",
        "",
        f"# Флот: {len(repos)} репо · host `{host}`",
        "",
        f"> Workspace: `{workspace}` · Снято: {generated}",
    ]
    if gh_error:
        lines += ["", f"> ⚠️ GitHub-данные недоступны: {gh_error} (git-only отчёт)"]

    attention = [(r, _needs_attention(r)) for r in repos]
    attention = [(r, why) for r, why in attention if why]
    lines += ["", "## Требует внимания", ""]
    if attention:
        for repo, why in attention:
            lines.append(f"- **{repo['dir']}** — {'; '.join(why)}")
    else:
        lines.append("Всё чисто: рабочие деревья без изменений, ahead/behind 0.")

    lines += [
        "",
        "## Сводка",
        "",
        "| Репо | Ветка | ↑↓ | Dirty | PRs | Issues | Alerts |",
        "|---|---|---|---|---|---|---|",
    ]
    for repo in repos:
        local = _local(repo)
        gh = _gh(repo)
        branch = local.get("branch") or "—"
        dirty = "да" if local.get("dirty") else "нет"
        if gh:
            prs = str(len(gh.get("pulls") or []))
            issues_val = gh.get("issues")
            issues = "?" if issues_val is None else str(len(issues_val))
            alerts_val = gh.get("alerts")
            alerts = "?" if alerts_val is None else str(alerts_val)
        else:
            prs = issues = alerts = "—"
        lines.append(
            f"| {repo['dir']} | {branch} | {_ahead_behind(repo)}"
            f" | {dirty} | {prs} | {issues} | {alerts} |"
        )

    no_remote = [r["dir"] for r in repos if not r.get("remote")]
    if no_remote:
        lines += ["", f"Без GitHub-remote: {', '.join(no_remote)}."]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None, help="snapshot JSON (default: stdin)")
    parser.add_argument("--out", type=Path, default=None, help="файл или каталог (default: stdout)")
    args = parser.parse_args()

    raw = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError as err:
        print(f"Некорректный snapshot-JSON: {err}", file=sys.stderr)
        raise SystemExit(1) from err

    report = render(snapshot)
    if args.out is None:
        print(report)
        return
    out = args.out
    if out.is_dir():
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        out = out / f"fleet-{snapshot.get('host', 'unknown')}-{stamp}.md"
    out.write_text(report, encoding="utf-8")
    print(str(out))


if __name__ == "__main__":
    main()
