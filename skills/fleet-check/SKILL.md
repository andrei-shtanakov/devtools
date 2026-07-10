---
name: fleet-check
description: >
  Периодическая проверка флота репозиториев ATP-экосистемы: незакоммиченные
  файлы, ahead/behind относительно upstream, открытые PRs, issues, security
  alerts по каждому репо. Формирует markdown-отчёт для prograph-vault
  derived/fleet/ и доставляет его PR-ом. Запускать: по просьбе «проверь флот /
  состояние репозиториев / fleet check», при старте рабочей сессии и по
  расписанию (например, ежедневно утром).
allowed-tools: Bash, Read, Grep, Glob, Write
---

# fleet-check — проверка состояния флота репо

Работает из `devtools/`; workspace — родительский каталог. Соблюдай
конституцию `devtools/CLAUDE.md`: соседние репо read-only, доставка — PR.

> Установка скилла в проект: скопировать/засимлинкать этот каталог в
> `.claude/skills/fleet-check` (тот же паттерн, что `install-skills.sh`
> в prograph-vault).

## Шаг 1 — снапшот

```bash
cd devtools/
uv run --project ../github-checker github-checker snapshot --workspace .. > /tmp/fleet-snapshot.json
```

- Если `gh` не авторизован — снапшот сам деградирует до git-only и запишет
  причину в `gh_error`; это нормально, отчёт будет частичным.
- Если `uv` недоступен — fallback: из каталога `../github-checker` запустить
  `python3 -m github_checker.main snapshot --workspace ..` любым python ≥3.11
  с установленным pydantic.

## Шаг 2 — отчёт

```bash
python3 fleet_report.py --input /tmp/fleet-snapshot.json           # в stdout
python3 fleet_report.py --input /tmp/fleet-snapshot.json --out /tmp # в файл
```

Покажи пользователю раздел «Требует внимания» текстом в чате.

## Шаг 3 — доставка в KB (PR, не прямой коммит)

Целевое место: `../prograph-vault/derived/fleet/fleet-<host>-<дата>.md`.

```bash
cd ../prograph-vault
git checkout -b fleet-report/$(date +%Y-%m-%d)
mkdir -p derived/fleet
python3 ../devtools/fleet_report.py --input /tmp/fleet-snapshot.json --out derived/fleet/
git add derived/fleet && git commit -m "fleet: report $(date +%Y-%m-%d)"
gh pr create --fill 2>/dev/null || echo "gh недоступен — ветка готова, PR создай вручную"
git checkout -
```

Важно: писатель `derived/fleet/` ещё не зарегистрирован в конституции vault —
пока ADR не принят, укажи это в описании PR и не мержь без maintainer-а.

## Шаг 4 — действия из находок

- Незакоммиченное / рассинхрон — сообщи пользователю, ничего не чини сам.
- Системные проблемы (дрейф контрактов — `make drift`, конформанс —
  `make conformance`) — предложи оформить `tasks.md`-спеку для spec-runner
  в репо-владельце; создание спеки — тоже через PR.
