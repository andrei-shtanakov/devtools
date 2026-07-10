# devtools

Workspace-тулинг экосистемы AI-оркестраторов. Живёт соседним репозиторием в
корне `all_ai_orchestrators/`; все инструменты работают над **родительским**
каталогом (workspace) и строго READ-ONLY по отношению к остальным репо —
любые изменения уходят в них только через PR.

История: вынесен из `_cowork_output/devtools/` (2026-07-10), чтобы тулинг
версионировался и переезжал на другие машины вместе с проектами
(`_cowork_output/` — dev-scratch одной машины, у клонов его нет).

## Состав

| Инструмент | Назначение |
|---|---|
| `repos.sh` | polyrepo-обёртка: status / fetch / pull / dirty / branches / bootstrap / exec. Репо-список — автодискавери по `*/.git` |
| `Makefile` | алиасы: `make morning` (fetch+status), `make drift`, `make conformance` и др. |
| `check-contract-drift.sh` | diff вендоренных контрактов между репо (obs.py, report_benchmark schema) |
| `check-agent-id-conformance.py` | инварианты ADR-ECO-003: SSOT agents-catalog ↔ arbiter ↔ Maestro |
| `discover_models.py` | discovery моделей провайдеров (ADR-ECO-003a): отчёт + Plane-1 TOML для PR |
| `gen_agents_toml.py` | генерация секций agents.toml из benchmark_runs (arbiter.db) |
| `discovery/` | offline-манифесты observed-моделей |
| `all-orchestrators.code-workspace` | VSCode workspace |

## Быстрый старт

```bash
cd devtools
make morning     # fetch + сводка: ветка / ahead-behind / грязь по всем репо
make dirty       # только репо с незакоммиченным
make drift       # рассинхрон вендоренных контрактов
make conformance # agent-id каталог ↔ потребители
```

## Дальше

Кандидат на дом для fleet-агента (наблюдает состояние флота репо и действует
через спеки/PR) — см. обсуждение границ ролей: dispatcher смотрит, Robin
отвечает, steward гейтит спеки. Решение — отдельным ADR.
