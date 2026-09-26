"""Run directory, baseline lookup, JSON and Markdown report (spec §1, §4)."""

from __future__ import annotations

import json
import os
import secrets
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from selfcheck.delta import RunSnapshot


def _token() -> str:
    return secrets.token_hex(3)


def new_run_dir(
    out_root: Path, now: datetime, token: Callable[[], str] = _token
) -> tuple[str, Path]:
    """Atomically create ``<out>/<YYYYMMDDTHHMMSSZ>-<hex6>``; never reuse."""
    out_root.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        run_id = f"{now:%Y%m%dT%H%M%SZ}-{token()}"
        path = out_root / run_id
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return run_id, path
    raise OSError(f"could not allocate a unique run directory in {out_root}")


MD_ROWS = 200
NO_SELECTION: dict[str, list[str]] = {"path": [], "probe": []}


def find_baseline(
    out_root: Path, current: str, selection: dict[str, list[str]] | None = None
) -> tuple[dict[str, Any] | None, list[str]]:
    """Newest previous readable report of the same selection, plus warnings.

    Reports are ordered by write time (two runs in one second differ only by
    the random suffix). An unreadable or incompatible report is skipped with a
    warning instead of breaking every later run; a run narrowed by
    ``--path``/``--probe`` is only a baseline for the same narrowing.
    """
    wanted = selection or NO_SELECTION
    warnings: list[str] = []
    if not out_root.is_dir():
        return None, warnings
    reports = [
        r / "report.json"
        for r in out_root.iterdir()
        if r.name != current and (r / "report.json").is_file()
    ]
    for path in sorted(reports, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            doc = json.loads(path.read_text())
            RunSnapshot.from_json(doc["snapshot"])
            used = doc["run"].get("selection", NO_SELECTION)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append(f"baseline skipped: {path.parent.name}: {exc!r}"[:300])
            continue
        if used == wanted:
            return doc, warnings
    return None, warnings


def write_report(run_dir: Path, doc: dict[str, Any]) -> None:
    """report.md, then report.json atomically (a baseline is never half-written)."""
    (run_dir / "report.md").write_text(render_markdown(doc))
    tmp = run_dir / "report.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    os.replace(tmp, run_dir / "report.json")


def _coverage(cov: dict[str, Any]) -> str:
    processed = cov.get("processed")
    passed = cov.get("passed")
    count = len(passed) if isinstance(passed, list) else "-"
    shown = processed if processed is not None else "-"
    return f"{cov.get('mode', '-')}/{cov.get('input_mode', '-')} {shown}/{count}"


def _probe_rows(doc: dict[str, Any]) -> list[str]:
    rows = [
        "| проба | репо | статус | причина | версия | код | канарейка | покрытие | находок |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in doc["probes"]:
        code = p["exit_code"] if p["exit_code"] is not None else "—"
        reason = (p["reason"] or "—").replace("|", "/").replace("\n", " ")[:120]
        rows.append(
            f"| {p['probe']} | {p['repo']} | {p['status']} | {reason} | "
            f"{p['tool_version'] or '—'} | {code} | {p['canary'] or '—'} | "
            f"{_coverage(p['coverage'])} | {p['findings']} |"
        )
    return rows


def _refs_age(rows: list[dict[str, Any]]) -> str:
    unknown = [r["name"] for r in rows if not r.get("fetched_at")]
    if unknown:
        return f"давность refs неизвестна для: {', '.join(unknown)}"
    oldest = min(r["fetched_at"] for r in rows) if rows else "—"
    return f"относительно локальных refs не старше {oldest}"


def _fleet_lines(doc: dict[str, Any]) -> list[str]:
    """Completeness, fleet-only and vendored copies (spec §9.3, §9.6, §9.7)."""
    run = doc["run"]
    fleet = doc.get("fleet", {})
    lines = ["## Флот", ""]
    if fleet.get("enabled"):
        surface = run["surface"]
        rows = surface.get("fleet_repos", [])
        lines.append(
            f"{surface['fleet']} относительно манифеста `{run['manifest_path']}` "
            f"(sha1 `{surface.get('manifest_sha1', '')}`): {len(rows)} репо; "
            f"{_refs_age(rows)}; не покрыто: корневой зонтик, `~/.claude`"
        )
        for repo in run["scope"]:
            only = fleet.get("fleet_only", {}).get(repo, [])
            lines += ["", f"fleet-only: {len(only)} ({repo})"]
            if only:
                lines += ["", "| узел | источник |", "|---|---|"]
                lines += [f"| `{o['node']}` | {o['from']} |" for o in only]
    else:
        lines.append("fleet-only: — (без --fleet)")
    vendored = doc.get("vendored", {})
    rows_v = [
        (path, d)
        for repo in run["scope"]
        for path, decls in vendored.get(repo, {}).items()
        for d in decls
    ]
    if rows_v:
        lines += [
            "",
            "### вендор-копии",
            "",
            "| путь | владелец | ref | декларация |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {path} | {d['owner']} | {d['ref']} | {d['declaration']} |"
            for path, d in rows_v
        ]
    return [*lines, ""]


def render_markdown(doc: dict[str, Any]) -> str:
    """Human report: probes first, then findings by category and rule."""
    run = doc["run"]
    manifest = run["manifest"]
    lines = [
        f"# selfcheck {run['run_id']}",
        "",
        (
            f"Хост: {run['host']}. Репо: {', '.join(run['scope'])}. Манифест: записей "
            f"{manifest['entries_read']}, каталогов {len(manifest['repos'])}, "
            f"отсутствуют: {', '.join(manifest['missing']) or 'нет'}."
        ),
        f"Окружение: {run['env']}. Поверхность: {run['surface']}.",
        "",
        "## Пробы",
        "",
        *_probe_rows(doc),
        "",
    ]
    lines += _fleet_lines(doc)
    statuses = doc["delta"]["statuses"]
    gone = doc["delta"]["gone"]
    lines += [
        "## Дельта",
        "",
        (
            f"текущие: {dict(Counter(statuses.values()))}; "
            f"ушедшие: {dict(Counter(g['status'] for g in gone))}"
        ),
        "",
    ]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for f in doc["findings"]:
        by_cat.setdefault(f["category"], []).append(f)
    for category, items in sorted(by_cat.items()):
        lines += [
            f"## {category} ({len(items)})",
            "",
            "| правило | уверенность | якорь | мест | статус |",
            "|---|---|---|---|---|",
        ]
        for f in sorted(items, key=lambda x: (x["rule"], x["anchor"]))[:MD_ROWS]:
            lines.append(
                f"| {f['rule']} | {f['confidence']} | `{f['anchor']}` | "
                f"{f['occurrences']} | {statuses.get(f['id'], '—')} |"
            )
        if len(items) > MD_ROWS:
            lines.append(
                f"\n_показаны {MD_ROWS} из {len(items)} — остальное в report.json_"
            )
        lines.append("")
    lines += [
        "## Подавлено",
        "",
        f"allowlist: {len(doc['suppressed'])}; no-env: {doc['suppressed_no_env']}",
        "",
        "## Инвентарь LLM-вызовов",
        "",
        "| путь | строка | механизм | кандидат | признаки |",
        "|---|---|---|---|---|",
    ]
    for item in doc["inventory"]["llm"]:
        lines.append(
            f"| {item['path']} | {item['line']} | {item['mechanism']} | "
            f"{'да' if item['candidate'] else 'нет'} | {', '.join(item['features'])} |"
        )
    if run["warnings"]:
        lines += ["", "## Предупреждения", "", *[f"- {w}" for w in run["warnings"]]]
    return "\n".join(lines) + "\n"
