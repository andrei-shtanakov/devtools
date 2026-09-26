"""Run directory, baseline lookup, JSON and Markdown report (spec §1, §4)."""

from __future__ import annotations

import json
import secrets
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


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


def find_baseline(out_root: Path, current: str) -> dict[str, Any] | None:
    """Latest previous run with a report, ordered by report write time."""
    if not out_root.is_dir():
        return None
    reports = [
        r / "report.json"
        for r in out_root.iterdir()
        if r.name != current and (r / "report.json").is_file()
    ]
    if not reports:
        return None
    latest = max(reports, key=lambda p: p.stat().st_mtime_ns)
    return json.loads(latest.read_text())


def write_report(run_dir: Path, doc: dict[str, Any]) -> None:
    """report.json + report.md."""
    (run_dir / "report.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    (run_dir / "report.md").write_text(render_markdown(doc))


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
        for f in sorted(items, key=lambda x: (x["rule"], x["anchor"]))[:200]:
            lines.append(
                f"| {f['rule']} | {f['confidence']} | `{f['anchor']}` | "
                f"{f['occurrences']} | {statuses.get(f['id'], '—')} |"
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
