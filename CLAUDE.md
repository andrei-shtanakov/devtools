# CLAUDE.md — devtools (дом fleet-агента)

## Роль

`devtools/` — дом **fleet-агента**: единой точки управления polyrepo-workspace
(экосистема ATP). Агент наблюдает состояние флота репозиториев и **действует
только косвенно** — PR-ами в другие репо и `tasks.md`-спеками для spec-runner.

Границы ролей (не дублировать):

- **dispatcher** — read-only дашборд runtime-артефактов; *смотрит*.
- **Robin (robin-runtime)** — отвечает на вопросы по KB; *объясняет*.
- **steward** — гейтит спеки; *проверяет*.
- **fleet-агент (здесь)** — сводит состояние флота и планирует действия; *управляет*.

## Инварианты (конституция)

1. **READ-ONLY к соседним репо.** Никаких прямых записей/коммитов в другие
   репозитории workspace. Изменения уходят только через PR (ветка → PR → ревью).
2. **Сенсоров два — намеренно, не дублирование.** Текущее состояние флота
   (ветки, ahead/behind, dirty, PRs/issues/alerts) — `github-checker snapshot`
   (см. `../github-checker/README.md`, headless-режим). Дельта за период
   («что изменилось с X») — `recent_changes.py` (stdlib-only: пригоден как
   tool для Robin без pydantic-зависимости). Не сливать и не «дедуплицировать».
   repos.sh — интерактивная обёртка для человека, не источник данных для отчётов.
3. **Память — prograph-vault.** Отчёты fleet-check предназначены для
   `../prograph-vault/derived/fleet/` и доставляются PR-ом. Регистрация
   писателя в конституции vault — отдельным ADR (до его принятия PR-ы
   помечать maintainer-у).
4. **Планирование — спеками.** Задачи развития экосистемы агент оформляет как
   `tasks.md`-спеки для spec-runner в репо-владельце изменений, не исполняет сам.
5. Отчёт всегда содержит `host` — чьи локальные клоны он описывает
   (ahead/behind/dirty — состояние конкретной машины).

## Инструменты

| Инструмент | Назначение |
|---|---|
| `repos.sh` | интерактив: status / fetch / pull / dirty / bootstrap / exec |
| `Makefile` | алиасы, в т.ч. `make snapshot`, `make fleet-report`, `make morning` |
| `fleet_report.py` | snapshot-JSON → markdown-отчёт для vault `derived/fleet/` |
| `recent_changes.py` | темпоральный сенсор: коммиты + незакоммиченное с момента X (`make today`) |
| `check-contract-drift.sh` | дрейф вендоренных контрактов |
| `check-agent-id-conformance.py` | инварианты ADR-ECO-003 |
| `.claude/skills/fleet-check` | скилл периодической проверки флота |

## Быстрый старт агента

```bash
make snapshot        # полный JSON состояния флота (git + GitHub, если gh готов)
make fleet-report    # markdown-отчёт в stdout
make morning         # человеческий ритуал: fetch + status
```
