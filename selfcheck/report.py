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
    multi = len(run["scope"]) > 1
    rows_v = [
        (repo, path, d)
        for repo in run["scope"]
        for path, decls in vendored.get(repo, {}).items()
        for d in decls
    ]
    if rows_v:
        head = "| репо | путь |" if multi else "| путь |"
        lines += [
            "",
            "### вендор-копии",
            "",
            f"{head} владелец | ref | декларация |",
            "|" + "---|" * (5 if multi else 4),
        ]
        lines += [
            f"{'| ' + repo + ' ' if multi else ''}| {path} | {d['owner']} | "
            f"{d['ref']} | {d['declaration']} |"
            for repo, path, d in rows_v
        ]
    vendor_dups = doc.get("vendor_dups", [])
    if vendor_dups:
        lines += ["", f"### вендор-дубли ({len(vendor_dups)})", ""]
        lines += [
            f"- `{g['anchor']}`: "
            + ", ".join(
                f"{m['owner_repo']}:{m['path']}::{m['member']}" for m in g["members"]
            )
            for g in vendor_dups
        ]
    return [*lines, ""]


def _repo_rows(doc: dict[str, Any]) -> list[str]:
    """Per-repo summary when the run covers two repos or more (§10.2)."""
    scope = doc["run"]["scope"]
    if len(scope) < 2:
        return []
    cats = sorted({f["category"] for f in doc["findings"]})
    lines = [
        "## Репо",
        "",
        "| репо | пробы не ok | dead c/l/cand | " + " | ".join(cats) + " |",
        "|" + "---|" * (3 + len(cats)),
    ]
    for repo in scope:
        mine = [f for f in doc["findings"] if f.get("owner_repo") == repo]
        bad = [
            f"{p['probe']}:{p['status']} ({(p.get('reason') or '')[:60]})"
            for p in doc["probes"]
            if p["repo"] == repo and p["status"] not in ("ok", "skipped")
        ]
        dead = [
            f["confidence"] for f in mine if f["rule"].startswith("usage-graph/dead")
        ]
        dc = "/".join(str(dead.count(c)) for c in ("confirmed", "likely", "candidate"))
        counts = " | ".join(
            str(sum(1 for f in mine if f["category"] == c)) for c in cats
        )
        lines.append(f"| {repo} | {', '.join(bad) or '—'} | {dc} | {counts} |")
    return [*lines, ""]


# launchd completeness cannot be proven: the report names the plists (§3.2.4)
_LISTED = frozenset({"plists"})


def _surface_line(surface: dict[str, Any]) -> str:
    """Scalars as they are, the plists by name, other maps and lists as counts
    (#410): the per-file ``history`` lives in report.json."""

    def shown(key: str, value: Any) -> Any:
        if key in _LISTED and isinstance(value, list):
            return ", ".join(map(str, value)) or "—"
        return len(value) if isinstance(value, dict | list) else value

    return ", ".join(f"{k}: {shown(k, v)}" for k, v in surface.items())


def _judge_lines(doc: dict[str, Any]) -> list[str]:
    """Judge summary, the «keep» section and what was not judged (§11.2, §11.6)."""
    j = doc.get("judge") or {}
    if not j:
        return []
    judged = [f for f in doc["findings"] if f.get("judge")]
    valid = [f for f in judged if f["judge"].get("verdict") != "error"]
    lines = [
        "## Судья",
        "",
        (
            f"модель {j['model']}: вердиктов {len(valid)} (из кэша {j['cached']}, "
            f"новых вызовов {j['calls']}, ошибок {j['errors']}); "
            f"не судились {len(j['not_judged'])} из {j['candidates']} кандидатов"
        ),
    ]
    for title, verdict in (
        ("Судья: заменить", "replace"),
        ("Судья: оставить", "keep"),
        ("Судья: не уверен", "unsure"),
    ):
        rows = [f for f in valid if f["judge"]["verdict"] == verdict]
        if not rows:
            continue
        lines += [
            "",
            f"### {title} ({len(rows)})",
            "",
            "| репо | правило | якорь | замена | обоснование |",
            "|---|---|---|---|---|",
        ]
        for f in rows[:MD_ROWS]:
            why = f["judge"].get("rationale", "")[:160]
            why = why.replace("|", "/").replace("\r", " ").replace("\n", " ")
            lines.append(
                f"| {f.get('owner_repo', '')} | {f['rule']} | `{f['anchor']}` | "
                f"{f['judge'].get('replacement', '')} | {why} |"
            )
    if j["not_judged"]:
        by_rule = dict(Counter(x["rule"] for x in j["not_judged"]))
        lines += ["", f"не судились по правилам: {by_rule}; первые 20:"]
        lines += [f"- {x['rule']} `{x['id']}`" for x in j["not_judged"][:20]]
    return [*lines, ""]


def _s4_lines(doc: dict[str, Any]) -> list[str]:
    """S4 return condition from lint-coverage (owner decision 2026-09-28): an
    open gap in a language whose linting executes target code."""
    rows = [p for p in doc["probes"] if p["probe"] == "lint-coverage"]
    if not rows:
        return []
    gaps = sorted(
        f"{f['owner_repo']} ({f['text_key']})"
        for f in doc["findings"]
        if f["rule"] == "lint-coverage/missing"
        and any(e["kind"] == "s4-return" for e in f.get("evidence", []))
    )
    bad = sorted(p["repo"] for p in rows if p["status"] not in ("ok", "skipped"))
    # measured = a probe row that looked at the repo (#462 review, round 2):
    # scope membership is not measurement — a missing dir is in --all scope,
    # and a narrowed-corpus skip read nothing
    measured = {
        p["repo"]
        for p in rows
        if p["status"] == "ok"
        or (p["status"] == "skipped" and p["reason"] != "narrowed-corpus")
    }
    manifest = doc["run"]["manifest"]
    outside = sorted({*manifest["repos"], *manifest["missing"]} - measured - set(bad))
    if gaps:
        line = f"S4: условие возврата выполнено — {', '.join(gaps)}"
    elif bad:
        line = f"S4: условие возврата не установлено — lint-coverage: {', '.join(bad)}"
    elif outside:  # «no gaps» is a claim about the whole fleet (#462 review)
        line = f"S4: условие возврата не измерено — вне прогона: {', '.join(outside)}"
    else:
        line = "S4: условие возврата не выполнено (открытых дыр rust/elixir/ts нет)"
    return [line, ""]


def render_markdown(doc: dict[str, Any]) -> str:
    """Human report: probes first, then findings by category and rule."""
    run = doc["run"]
    manifest = run["manifest"]
    multi = len(run["scope"]) > 1
    lines = [
        f"# selfcheck {run['run_id']}",
        "",
        (
            f"Хост: {run['host']}. Репо: {', '.join(run['scope'])}. Манифест: записей "
            f"{manifest['entries_read']}, каталогов {len(manifest['repos'])}, "
            f"отсутствуют: {', '.join(manifest['missing']) or 'нет'}."
        ),
        f"Окружение: {run['env']}. Поверхность: {_surface_line(run['surface'])}.",
        "",
        *_repo_rows(doc),
        "## Пробы",
        "",
        *_probe_rows(doc),
        "",
        *_s4_lines(doc),
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
    for category, all_items in sorted(by_cat.items()):
        # a judge `keep` is shown under «Судья: оставить», not here (§11.6)
        items = [
            f for f in all_items if (f.get("judge") or {}).get("verdict") != "keep"
        ]
        if not items:
            continue
        head = "| репо | правило |" if multi else "| правило |"
        lines += [
            f"## {category} ({len(items)})",
            "",
            f"{head} уверенность | якорь | мест | статус |",
            "|" + "---|" * (6 if multi else 5),
        ]
        ordered = sorted(
            items, key=lambda x: (x.get("owner_repo", ""), x["rule"], x["anchor"])
        )
        for f in ordered[:MD_ROWS]:
            repo = f"| {f.get('owner_repo', '')} " if multi else ""
            lines.append(
                f"{repo}| {f['rule']} | {f['confidence']} | `{f['anchor']}` | "
                f"{f['occurrences']} | {statuses.get(f['id'], '—')} |"
            )
        if len(items) > MD_ROWS:
            lines.append(
                f"\n_показаны {MD_ROWS} из {len(items)} — остальное в report.json_"
            )
        lines.append("")
    lines += _judge_lines(doc)
    lines += [
        "## Подавлено",
        "",
        f"allowlist: {len(doc['suppressed'])}; no-env: {doc['suppressed_no_env']}",
        "",
        "## Инвентарь LLM-вызовов",
        "",
        (
            f"{'| репо | путь |' if multi else '| путь |'} строка | механизм | "
            "кандидат | признаки |"
        ),
        "|" + "---|" * (6 if multi else 5),
    ]
    for item in doc["inventory"]["llm"]:
        repo = f"| {item.get('repo', '')} " if multi else ""
        lines.append(
            f"{repo}| {item['path']} | {item['line']} | {item['mechanism']} | "
            f"{'да' if item['candidate'] else 'нет'} | {', '.join(item['features'])} |"
        )
    if run["warnings"]:
        lines += ["", "## Предупреждения", "", *[f"- {w}" for w in run["warnings"]]]
    return "\n".join(lines) + "\n"
