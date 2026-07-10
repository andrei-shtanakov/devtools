#!/usr/bin/env python3
"""Discovery-нотификатор моделей (ADR-ECO-003a, D2/D5).

Сверяет Plane 1 SSOT-каталога (`agents-catalog.toml`, секции `[models."…"]`) со
списком моделей, которые провайдеры предлагают СЕЙЧАС ("observed"), и печатает:

  1. Отчёт (markdown) — новые модели, кандидаты на deprecation, сводка.
  2. Готовый Plane-1 TOML-блок для новых моделей — вставляется в каталог PR'ом.

Строго по ADR-ECO-003a:
  * Владелец discovery — devtools (PM-tooling), НЕ arbiter/Maestro (D5).
  * Discovery трогает ТОЛЬКО Plane 1 (существование модели). Плоскости 2/3
    (harness / enrollment / routable) НЕ касается — промоушн в роутинг это
    отдельный benchmark-gated гейт.
  * Инструмент НИЧЕГО не пишет в репозитории (READ-ONLY). Патч кладётся в --out;
    его превращают в PR руками / ботом.
  * Авто-бамп agent_id ЗАПРЕЩЁН: новая модель = новая запись, не переименование.

Источник observed — pluggable:
  * `--observed FILE`  — офлайн-манифест `{vendor: [model_id, ...]}` (работает без
    сети/ключей; заполняется вручную из `claude --model` / доков провайдера или
    scheduled-таском). Это дефолтный источник прототипа.
  * live-адаптеры провайдеров (anthropic /v1/models, openai /v1/models) — СТАБЫ:
    задокументированы, но требуют ключа и явного включения. Прототип их не дёргает,
    чтобы оставаться детерминированным и оффлайн.

Exit-коды (для scheduled-таска / CI):
  0 — новых моделей нет, каталог актуален.
  2 — найдены новые модели (нотификация: пора открыть PR на Plane 1).
  1 — ошибка (каталог/манифест не прочитан).

Usage:
    python3 discover_models.py --observed discovery/observed-models.sample.json
    python3 discover_models.py --catalog /path/agents-catalog.toml --out /tmp/out
    python3 discover_models.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

try:                       # 3.11+ stdlib
    import tomllib
except ModuleNotFoundError:  # 3.10 fallback (pip install tomli)
    import tomli as tomllib
from pathlib import Path

# Вендоры, которые discovery МОЖЕТ сверять с provider-листами. Локальные
# baseline-модели (ollama/meta llama) намеренно исключены — у них нет "provider
# offering", их не депрекейтит уход из чужого меню.
CHECKABLE_VENDORS = {"anthropic", "openai", "deepseek", "xiaomi", "alibaba", "zhipu"}


@dataclass
class CatalogModel:
    model_id: str
    vendor: str
    status: str
    aliases: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Поиск путей / загрузка
# --------------------------------------------------------------------------- #
def find_repo_root(start: Path) -> Path:
    """Подняться вверх до папки, где лежат arbiter/ и atp-platform/."""
    for p in [start, *start.parents]:
        if (p / "arbiter").is_dir() and (p / "atp-platform").is_dir():
            return p
    return start


def default_catalog_path(start: Path) -> Path:
    """Канонический SSOT — `atp-platform/method/agents-catalog.toml` (owner ruling
    2026-07-03): канон обязан жить в дистрибутируемом репе. `_cowork_output` — dev-only
    коммуникационный workspace, у установивших проекты его НЕТ; contracts/-копия —
    зеркало для коммуникации, не источник. arbiter вендорит `config/`-копию."""
    root = find_repo_root(start)
    return root / "atp-platform" / "method" / "agents-catalog.toml"


def load_catalog_models(path: Path) -> dict[str, CatalogModel]:
    """Распарсить только Plane 1 (`[models."…"]`). Остальные плоскости игнорим."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    models: dict[str, CatalogModel] = {}
    for model_id, spec in (data.get("models") or {}).items():
        models[model_id] = CatalogModel(
            model_id=model_id,
            vendor=str(spec.get("vendor", "")),
            status=str(spec.get("status", "active")),
            aliases=list(spec.get("aliases") or []),
        )
    return models


def load_observed(path: Path) -> dict[str, list[str]]:
    """Офлайн-манифест observed-моделей: {vendor: [model_id, ...]}."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    # допускаем обёртку {"observed": {...}, "_meta": ...} или плоский dict
    obs = raw.get("observed", raw) if isinstance(raw, dict) else {}
    return {str(v): [str(m) for m in ids] for v, ids in obs.items()
            if v not in {"_meta", "observed"}}


# --------------------------------------------------------------------------- #
#  Ядро диффа
# --------------------------------------------------------------------------- #
def known_ids_for_vendor(models: dict[str, CatalogModel], vendor: str) -> set[str]:
    """Все id + алиасы каталога для данного вендора (что считаем 'уже известным')."""
    known: set[str] = set()
    for m in models.values():
        if m.vendor == vendor:
            known.add(m.model_id)
            known.update(m.aliases)
    return known


@dataclass
class Diff:
    new: dict[str, list[str]] = field(default_factory=dict)          # vendor -> new ids
    deprecation_candidates: dict[str, list[str]] = field(default_factory=dict)
    unchecked_vendors: list[str] = field(default_factory=list)       # в каталоге, но не в observed


def compute_diff(models: dict[str, CatalogModel],
                 observed: dict[str, list[str]]) -> Diff:
    diff = Diff()

    # 1. Новые модели: observed-id, которого нет среди id/алиасов каталога.
    for vendor, ids in observed.items():
        if vendor not in CHECKABLE_VENDORS:
            continue
        known = known_ids_for_vendor(models, vendor)
        fresh = [i for i in ids if i not in known]
        if fresh:
            diff.new[vendor] = sorted(fresh)

    # 2. Кандидаты на deprecation: active-модель каталога, которой НЕТ в observed
    #    того же вендора. ВНИМАНИЕ: это лишь кандидат — "пропал из меню" ≠ "снят с
    #    API" (ADR-003a Context). Требует ручной проверки доступности.
    observed_vendors = set(observed)
    for m in models.values():
        if m.status != "active" or m.vendor not in CHECKABLE_VENDORS:
            continue
        if m.vendor not in observed_vendors:
            continue  # вендор не проверялся — не делаем ложных выводов
        obs_ids = set(observed.get(m.vendor, []))
        if m.model_id not in obs_ids and not (set(m.aliases) & obs_ids):
            diff.deprecation_candidates.setdefault(m.vendor, []).append(m.model_id)

    # 3. Вендоры каталога, которых нет в observed вовсе — не проверены.
    catalog_vendors = {m.vendor for m in models.values() if m.vendor in CHECKABLE_VENDORS}
    diff.unchecked_vendors = sorted(catalog_vendors - observed_vendors)
    return diff


# --------------------------------------------------------------------------- #
#  Рендер
# --------------------------------------------------------------------------- #
def render_toml_patch(diff: Diff) -> str:
    """Готовый Plane-1 блок для вставки в каталог. status=active, aliases пусты —
    нормализацию alias'а под конвенцию делает человек в PR."""
    if not diff.new:
        return "# новых моделей нет — патч не требуется\n"
    lines = [
        "# GENERATED by discover_models.py — Plane 1 ONLY. Ревью перед вставкой в",
        "# agents-catalog.toml. НЕ добавляет [[agents]]/routable — это отдельный",
        "# benchmark-gated гейт (ADR-ECO-003a D2).",
        "",
    ]
    for vendor in sorted(diff.new):
        for model_id in diff.new[vendor]:
            lines += [
                f'[models."{model_id}"]',
                f'vendor  = "{vendor}"',
                'status  = "active"',
                "aliases = []   # TODO: нормализовать метку под конвенцию, если нужно",
                "",
            ]
    return "\n".join(lines)


def render_report(diff: Diff, catalog_path: Path, observed_path: Path | None) -> str:
    n_new = sum(len(v) for v in diff.new.values())
    lines = [
        "# Discovery-отчёт: модели провайдеров vs каталог",
        "",
        f"- Каталог: `{catalog_path}`",
        f"- Observed: `{observed_path}`" if observed_path else "- Observed: (нет)",
        f"- Новых моделей: **{n_new}**",
        "",
        "## TL;DR",
        "",
    ]
    if n_new:
        lines.append(f"1. Найдено новых моделей: {n_new}. Нужен PR на Plane 1 каталога "
                     "(блок ниже).")
    else:
        lines.append("1. Новых моделей нет — Plane 1 каталога актуален.")
    lines.append("2. Discovery трогает только существование модели. Промоушн в "
                 "роутинг — отдельный benchmark-gated гейт (не здесь).")
    lines.append("")

    lines.append("## Новые модели (кандидаты на добавление в Plane 1)")
    lines.append("")
    if diff.new:
        lines += ["| Vendor | Model id |", "|---|---|"]
        for vendor in sorted(diff.new):
            for mid in diff.new[vendor]:
                lines.append(f"| {vendor} | `{mid}` |")
    else:
        lines.append("_нет_")
    lines.append("")

    lines.append("## Кандидаты на deprecation (⚠️ требуют ручной проверки доступности)")
    lines.append("")
    lines.append("> «Пропал из меню/листа» ≠ «снят с API». Не ретайрить без проверки "
                 "`--model`/API (ADR-ECO-003a).")
    lines.append("")
    if diff.deprecation_candidates:
        lines += ["| Vendor | Model id |", "|---|---|"]
        for vendor in sorted(diff.deprecation_candidates):
            for mid in diff.deprecation_candidates[vendor]:
                lines.append(f"| {vendor} | `{mid}` |")
    else:
        lines.append("_нет_")
    lines.append("")

    if diff.unchecked_vendors:
        lines.append("## Не проверено (нет в observed-манифесте)")
        lines.append("")
        lines.append(", ".join(f"`{v}`" for v in diff.unchecked_vendors))
        lines.append("")

    lines.append("## Следующие шаги (ADR-ECO-003a)")
    lines.append("")
    lines.append("1. Ревью TOML-патча ниже → PR на Plane 1 (модель `active`, НЕ routable).")
    lines.append("2. pipe-check нового `agent_id` на пиннованном golden-`suite_id`.")
    lines.append("3. A/B vs предшественница по `rank_score` → человеческий гейт.")
    lines.append("4. Отдельный PR: флип в routable (Plane 2).")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Plane-1 TOML-патч")
    lines.append("")
    lines.append("```toml")
    lines.append(render_toml_patch(diff).rstrip("\n"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Selftest (без сети/файлов) — верификация ядра диффа
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    models = {
        "claude-sonnet-4-6": CatalogModel("claude-sonnet-4-6", "anthropic", "active", []),
        "gpt-5.5": CatalogModel("gpt-5.5", "openai", "active", []),
        "old-mini": CatalogModel("old-mini", "openai", "active", ["gpt-old"]),
    }
    observed = {
        "anthropic": ["claude-sonnet-4-6", "claude-sonnet-5"],  # sonnet-5 = новая
        "openai": ["gpt-5.5"],                                   # old-mini пропал
        "meta": ["llama3.2:1b"],                                 # не checkable
    }
    d = compute_diff(models, observed)
    assert d.new == {"anthropic": ["claude-sonnet-5"]}, d.new
    assert d.deprecation_candidates == {"openai": ["old-mini"]}, d.deprecation_candidates
    # alias-хит не должен ложно-срабатывать:
    observed2 = {"openai": ["gpt-old", "gpt-5.5"]}
    d2 = compute_diff(models, observed2)
    assert "openai" not in d2.deprecation_candidates, d2.deprecation_candidates
    assert d2.new == {}, d2.new  # gpt-old = alias old-mini → известен
    # meta исключён из checkable:
    assert "meta" not in d.new and "meta" not in d.deprecation_candidates
    patch = render_toml_patch(d)
    assert '[models."claude-sonnet-5"]' in patch
    # Plane 1 only: никаких настоящих agent-секций/routable-ключей (не в комментах).
    body = [ln.lstrip() for ln in patch.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("[[agents]]") for ln in body)
    assert not any(ln.startswith("routable") for ln in body)
    print("selftest OK")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Discovery-нотификатор моделей (ADR-ECO-003a)")
    ap.add_argument("--catalog", type=Path, help="путь к agents-catalog.toml")
    ap.add_argument("--observed", type=Path, help="JSON-манифест {vendor: [model_id]}")
    ap.add_argument("--out", type=Path, help="папка для отчёта и патча (по умолч. рядом)")
    ap.add_argument("--selftest", action="store_true", help="прогнать встроенные проверки")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    here = Path(__file__).resolve().parent
    catalog_path = args.catalog or default_catalog_path(here)
    if not catalog_path.is_file():
        print(f"ERROR: каталог не найден: {catalog_path}", file=sys.stderr)
        return 1
    if not args.observed or not args.observed.is_file():
        print("ERROR: нужен --observed <manifest.json> (офлайн-источник provider-моделей)",
              file=sys.stderr)
        return 1

    try:
        models = load_catalog_models(catalog_path)
        observed = load_observed(args.observed)
    except (OSError, tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    diff = compute_diff(models, observed)
    report = render_report(diff, catalog_path, args.observed)

    out_dir = args.out or here
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovery-report.md").write_text(report, encoding="utf-8")
    (out_dir / "catalog-plane1.patch.toml").write_text(render_toml_patch(diff), encoding="utf-8")

    n_new = sum(len(v) for v in diff.new.values())
    print(f"[discovery] catalog={catalog_path.name} new_models={n_new} "
          f"deprecation_candidates={sum(len(v) for v in diff.deprecation_candidates.values())}")
    print(f"[discovery] отчёт: {out_dir / 'discovery-report.md'}")
    return 2 if n_new else 0


if __name__ == "__main__":
    raise SystemExit(main())
