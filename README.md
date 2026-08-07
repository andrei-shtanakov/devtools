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
| `Makefile` | алиасы: `make morning` (fetch+status+inbox), `make drift`, `make conformance` и др. |
| `check-contract-drift.sh` | diff вендоренных контрактов между репо (obs.py, report_benchmark schema) |
| `check-agent-id-conformance.py` | инварианты ADR-ECO-003: SSOT agents-catalog ↔ arbiter ↔ Maestro |
| `check-graph-registry-drift.py` | граф prograph (derived) ↔ карта интеграций registry (authored); allowlist для файловых/runtime-связей |
| `check-plan-fields.py` | граф `@blocked_by` между `TODO.md` всех репо + ownership/movement totals и матрица (`make plan-check`). **Тонкая обёртка над пакетом `plan-fields`** — grammar/парсинг/резолюция из пакета; **требует `uv` + Python 3.12** (см. ниже) |
| `inbox.py` | входящие кросс-репные запросы: открытые issues с лейблом `inbox` + вывод принятия по `TODO.md` целевого репо (ADR-ECO-006); разбор пунктов — общий пакет `plan-fields`, поэтому `uv` + Python 3.12, как у `check-plan-fields.py`; `make inbox` |
| `discover_models.py` | discovery моделей провайдеров (ADR-ECO-003a): отчёт + Plane-1 TOML для PR |
| `gen_agents_toml.py` | генерация секций agents.toml из benchmark_runs (arbiter.db) |
| `discovery/` | offline-манифесты observed-моделей |
| `all-orchestrators.code-workspace` | VSCode workspace |

## Быстрый старт

```bash
cd devtools
make morning     # fetch + сводка по всем репо + inbox-скан (утренний ритуал)
make dirty       # только репо с незакоммиченным
make drift       # рассинхрон вендоренных контрактов
make conformance # agent-id каталог ↔ потребители
make graph-drift # граф prograph ↔ карта интеграций
make inbox       # кросс-репные запросы: что пришло и что ещё не принято
```

## `plan-check`: общий парсер, Python 3.12

`check-plan-fields.py` больше не несёт собственный парсер `TODO.md` — единственная
реализация контракта plan-fields живёт в пакете **`plan-fields`** (ADR-ECO-005 PF-7).
Скрипт оставляет за собой только своё: дискавери воркспейса, severity-политику и
формат вывода; парсинг, резолюцию ссылок и graph-диагностику берёт из пакета
(`parse_fleet`/`check_fleet` — канонический граф по `@id`; `check_legacy_fleet` —
переходный legacy `<repo>#<slug>` граф по ещё-не-`@id`-нутым пунктам, помечается
`[legacy source: no @id]`).

**Compat-gate (важно):** пакет требует **Python 3.12**, поэтому запинен как
зависимость, и ЭТОТ скрипт запускается только через `uv`:

```bash
make plan-check                      # = uv run --frozen python check-plan-fields.py ...
uv run --frozen python check-plan-fields.py --root .. \
    --manifest ../ai-orchestrators-workspace/workspace-manifest.toml
```

Остальные скрипты devtools остаются **stdlib / Python 3.11** и env пакета не
трогают — переехал только `plan-check`. `pyproject.toml` + `uv.lock` пинят пакет
на **неизменяемый коммит dispatcher** (git+subdirectory, не workspace-путь), так
что сборка воспроизводима и `uv run --frozen --offline` работает после первого
`uv sync`. Бамп пина — отдельным PR. Если запустить скрипт интерпретатором без
пакета — он честно скажет об этом и выйдет (код 2), а не отработает вхолостую.

Изменения поведения относительно прежней версии (тот же набор из 17 warnings и
exit-код на текущем флоте): legacy-находки помечаются `[legacy source: no @id]`
и всегда warning (stale legacy-блокер больше не валит билд — без стабильной
identity это не error; канонический stale по `@id` — валит); manifest теперь
авторитет существования репо, поэтому «no TODO / not cloned» уточняются до
`REPO-UNKNOWN` (plan defect) / `UNRESOLVABLE` / `NO-TODO`; в покрытие добавлен
счётчик `@id` (backlog PF-2B), плюс строка `canonical: N resolved @id edge(s)`.

ADR-ECO-005a разделяет две независимые оси. Ownership публикуется как
`human-owned | repo-owned | TBD | missing | invalid-owner |
unknown-repo-owner`; movement — как `actionable | waiting-by-trigger |
waiting-by-blocker | stale-condition | malformed-condition`. `plan-check`
печатает обе суммы и полную матрицу ownership × movement. Каждая открытая
строка входит ровно в одну ячейку, включая строки без `@id`; trigger или blocker
никогда не превращается в owner. При конфликте условий fail-closed приоритет:
`malformed-condition` → `stale-condition` → blocker → trigger → actionable.
Owner grammar не копируется в devtools: даже для строк без `@id` используется
публичный `plan_fields.parse_owner()` из immutable pin.

## Fleet-агент

Этот репо — дом fleet-агента (наблюдает состояние флота репо и действует
только через спеки/PR); конституция роли — в `CLAUDE.md`. Границы: dispatcher
смотрит, Robin отвечает, steward гейтит спеки, fleet-агент управляет.
Формальное закрепление роли и писателя `prograph-vault/derived/fleet/` —
отдельным ADR (в работе).

Конвейер проверки флота:

    make snapshot      # github-checker snapshot → JSON (git + PRs/issues/alerts)
    make fleet-report  # то же + fleet_report.py → markdown-отчёт

Скилл `skills/fleet-check/` (установка: скопировать в `.claude/skills/`)
ведёт полный цикл: снапшот → отчёт → PR в `prograph-vault/derived/fleet/`.
