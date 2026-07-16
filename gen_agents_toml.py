#!/usr/bin/env python3
"""Сгенерировать секции для arbiter/config/agents.toml из реальных данных прогона.

Читает таблицу `benchmark_runs` из arbiter.db (её заполняет ATP через
report_benchmark), отбирает ТОЛЬКО routable-агентов (harness со спаунером в
Maestro), засеивает cost/duration из реальных метрик и печатает готовый блок
секций TOML + отчёт о join-согласованности.

Режим READ-ONLY: ничего не пишет в репозитории. Сгенерированный TOML кладётся в
--out (по умолчанию рядом со скриптом), оттуда его вставляют в agents.toml руками.

Что засеивается из данных:  cost_per_hour, avg_duration_min, (справочно) score, n.
Что остаётся политикой:      display_name, supports_languages, supports_types,
                             max_concurrent  — из бенчмарка не выводятся. Для
                             известных агентов сохраняются из текущего agents.toml,
                             для новых — помечаются TODO.

Usage:
    python3 gen_agents_toml.py                         # автоопределение путей от корня репо
    python3 gen_agents_toml.py --db /path/arbiter.db --agents-toml /path/agents.toml
    python3 gen_agents_toml.py --out /tmp/agents.generated.toml
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# benchmark_id, которые arbiter РЕАЛЬНО использует в re-rank.
# Зеркалит arbiter/arbiter-mcp/src/tools/route_task.rs::benchmark_id_for
# (на 2026-06-19 маппится только Review -> "code-review"). Любой другой
# benchmark_id в данных НЕ влияет на роутинг, пока пару не добавят С ОБЕИХ
# сторон (arbiter route_task.rs + ATP taxonomy.py).
BENCHMARK_ID_TO_TASK_TYPE = {
    "code-review": "review",
}


def find_repo_root(start: Path) -> Path:
    """Подняться вверх до папки, где лежат arbiter/ и maestro/."""
    for p in [start, *start.parents]:
        if (p / "arbiter").is_dir() and (p / "maestro").is_dir():
            return p
    return start


def maestro_spawner_harnesses(repo: Path) -> set[str]:
    """Набор harness'ей, которых Maestro умеет спаунить (AgentType, кроме AUTO).

    Парсит maestro/maestro/models.py, чтобы оставаться в синхроне. Fallback —
    захардкоженный набор, если файл не найден/не распарсился.
    """
    fallback = {"claude_code", "codex_cli", "aider"}
    models = repo / "maestro" / "maestro" / "models.py"
    try:
        text = models.read_text()
    except OSError:
        return fallback
    m = re.search(r"class AgentType\b.*?(?=\nclass |\Z)", text, re.DOTALL)
    block = m.group(0) if m else text
    vals = set(re.findall(r'=\s*"([a-z0-9_]+)"', block))
    vals.discard("auto")
    return vals or fallback


def harness_of(agent_id: str) -> str:
    """`<harness>@<model>` -> harness. Id без '@' (напр. 'aider') -> сам id."""
    return agent_id.split("@", 1)[0]


def toml_key(agent_id: str) -> str:
    """Ключ секции: с кавычками, если содержит '@'/':'/'.' (bare-ключ TOML не может)."""
    return f'"{agent_id}"' if re.search(r"[@:.]", agent_id) else agent_id


def parse_existing_agents(path: Path) -> dict[str, dict]:
    """Минимальный парсер agents.toml: section -> {key: value}.

    Достаточно для сохранения policy-полей известных агентов (tomllib нет в py3.10).
    Понимает заголовки [bare] / ["quoted"], скаляры (строка/число) и плоские массивы.
    """
    out: dict[str, dict] = {}
    cur: str | None = None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        h = re.match(r"^\[\s*(\"[^\"]+\"|'[^']+'|[^\]]+?)\s*\]$", line)
        if h:
            cur = h.group(1).strip().strip("\"'")
            out[cur] = {}
            continue
        if cur is None or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val.startswith("["):
            items = re.findall(r'"([^"]*)"', val)
            out[cur][key] = items
        else:
            v = val.strip("\"'")
            try:
                out[cur][key] = float(v) if ("." in v) else int(v)
            except ValueError:
                out[cur][key] = v
    return out


def fmt_list(items) -> str:
    return "[" + ", ".join(f'"{x}"' for x in items) + "]"


def main() -> int:
    here = Path(__file__).resolve().parent
    repo = find_repo_root(here)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=repo / "arbiter" / "arbiter.db")
    ap.add_argument("--agents-toml", type=Path, default=repo / "arbiter" / "config" / "agents.toml")
    ap.add_argument("--out", type=Path, default=here / "agents.generated.toml")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"❌ БД не найдена: {args.db}", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    has_table = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='benchmark_runs'"
    ).fetchone()
    if not has_table:
        print("⚠  Таблицы benchmark_runs ещё нет — прогон не проводился. Запусти после sweep.")
        return 0

    rows = con.execute(
        """
        SELECT agent_id,
               benchmark_id,
               COUNT(*)                                          AS n,
               AVG(score)                                        AS avg_score,
               AVG(duration_seconds)                             AS avg_dur_s,
               SUM(total_cost_usd)                               AS sum_cost,
               SUM(duration_seconds)                             AS sum_dur_s
        FROM benchmark_runs
        GROUP BY agent_id, benchmark_id
        ORDER BY agent_id, benchmark_id
        """
    ).fetchall()

    if not rows:
        print("⚠  benchmark_runs пуста (0 строк) — данных ещё нет. Запусти после завтрашнего прогона.")
        return 0

    spawners = maestro_spawner_harnesses(repo)
    existing = parse_existing_agents(args.agents_toml)

    # Свести по агенту: метрики из данных + множество benchmark_id.
    agents: dict[str, dict] = {}
    for agent_id, bench_id, n, avg_score, avg_dur_s, sum_cost, sum_dur_s in rows:
        a = agents.setdefault(agent_id, {"benches": {}, "n": 0, "dur_s": 0.0, "cost": 0.0, "dur_sum": 0.0})
        a["benches"][bench_id] = {"n": n, "score": avg_score, "avg_dur_s": avg_dur_s}
        a["n"] += n
        a["cost"] += (sum_cost or 0.0)
        a["dur_sum"] += (sum_dur_s or 0.0)

    routable = {aid: a for aid, a in agents.items() if harness_of(aid) in spawners}
    non_routable = {aid: a for aid, a in agents.items() if harness_of(aid) not in spawners}

    # ---- генерация TOML ----
    out_lines: list[str] = [
        "# СГЕНЕРИРОВАНО gen_agents_toml.py из benchmark_runs — проверь и вставь в",
        "# arbiter/config/agents.toml. cost_per_hour/avg_duration_min засеяны из данных;",
        "# supports_*/max_concurrent — ПОЛИТИКА (для новых агентов помечены TODO).",
        "",
    ]
    for aid in sorted(routable):
        a = routable[aid]
        avg_dur_min = (a["dur_sum"] / a["n"] / 60.0) if a["n"] else 0.0
        cost_per_hour = (a["cost"] / (a["dur_sum"] / 3600.0)) if a["dur_sum"] else None
        prior = existing.get(aid, {})
        is_new = aid not in existing

        # policy-поля: сохранить из текущего toml, иначе TODO
        display = prior.get("display_name") or f"TODO {harness_of(aid)}"
        langs = prior.get("supports_languages")
        types = prior.get("supports_types")
        maxc = prior.get("max_concurrent")

        # для новых: засеять supports_types типами, под которые есть bench-данные
        seeded_types = sorted(
            {BENCHMARK_ID_TO_TASK_TYPE[b] for b in a["benches"] if b in BENCHMARK_ID_TO_TASK_TYPE}
        )

        tag = "НОВЫЙ" if is_new else "обновление cost/duration"
        bench_summary = ", ".join(f"{b}: score={v['score']:.3f} n={v['n']}" for b, v in a["benches"].items())
        out_lines.append(f"# [{tag}] {aid} — {bench_summary}")
        out_lines.append(f"[{toml_key(aid)}]")
        out_lines.append(f'display_name = "{display}"'
                         + ("" if not is_new else "   # TODO policy"))
        if langs:
            out_lines.append(f"supports_languages = {fmt_list(langs)}")
        else:
            out_lines.append('supports_languages = []   # TODO policy: из бенчмарка не выводится')
        if types:
            out_lines.append(f"supports_types = {fmt_list(types)}")
        elif seeded_types:
            out_lines.append(f"supports_types = {fmt_list(seeded_types)}   # засеяно по bench-данным; дополни политикой")
        else:
            out_lines.append('supports_types = []   # TODO policy')
        out_lines.append(f"max_concurrent = {maxc if maxc is not None else 1}"
                         + ("" if maxc is not None else "   # TODO policy"))
        if cost_per_hour is not None:
            out_lines.append(f"cost_per_hour = {cost_per_hour:.4f}   # из данных (n={a['n']})")
        else:
            out_lines.append(f"cost_per_hour = {prior.get('cost_per_hour', 0.0)}   # нет total_cost_usd в данных")
        out_lines.append(f"avg_duration_min = {avg_dur_min:.2f}   # из данных (n={a['n']})")
        out_lines.append("")

    args.out.write_text("\n".join(out_lines))

    # ---- отчёт о join-согласованности ----
    print("=" * 72)
    print("ОТЧЁТ join ATP↔arbiter")
    print("=" * 72)
    print(f"БД:            {args.db}")
    print(f"Maestro-спаунеры (routable harness): {sorted(spawners)}")
    print(f"Сгенерировано секций (routable c данными): {len(routable)} → {args.out}")
    print()

    print("✅ Routable-агенты С данными (попадут в agents.toml):")
    for aid in sorted(routable):
        new = " [НОВЫЙ]" if aid not in existing else ""
        print(f"   • {aid}{new}")
    print()

    # routable в toml, но БЕЗ данных → silent-None (re-rank no-op)
    routable_no_data = [
        aid for aid in existing
        if harness_of(aid) in spawners and aid not in routable
    ]
    if routable_no_data:
        print("⚠  Routable в agents.toml, но БЕЗ строк в benchmark_runs (re-rank = no-op для них):")
        for aid in routable_no_data:
            print(f"   • {aid}  ← проверь точное совпадение ключа с тем, что эмитит ATP")
        print()

    if non_routable:
        print("ℹ  Протестированы, но НЕ routable (нет спаунера в Maestro — НЕ добавлять в роутинг):")
        for aid in sorted(non_routable):
            print(f"   • {aid}")
        print()

    # benchmark_id в данных, которые arbiter НЕ использует
    seen_benches = {b for a in agents.values() for b in a["benches"]}
    unused = sorted(seen_benches - set(BENCHMARK_ID_TO_TASK_TYPE))
    if unused:
        print("⚠  benchmark_id есть в данных, но arbiter их НЕ использует в re-rank")
        print("   (нужно добавить пару в route_task.rs::benchmark_id_for И ATP taxonomy.py):")
        for b in unused:
            print(f"   • {b}")
        print()

    print("Дальше: проверь TODO-поля в", args.out, "и вставь секции в agents.toml.")
    print("Вес включать поэтапно: ARBITER_BENCH_WEIGHT=0 (проверка) → A/B 0.15.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
