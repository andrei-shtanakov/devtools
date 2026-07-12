# Связанность рабочих проектов экосистемы AI-оркестраторов

> Дом артефакта: `devtools/reports/`. Дата: 2026-07-10.
> Метод: COWORK_CONTEXT.md (реестр + карта интеграций), паспорта `.prograph/projects/*`,
> точечные проверки кода/README (отмечены в примечаниях).
> Охват: 15 рабочих проектов; тестовые площадки и полигоны не включены.

## TL;DR

1. 8 из 15 проектов полностью самостоятельны; всё «только-в-связке» — слои
   наблюдения и управления (dispatcher, prograph, robin-runtime, steward).
2. Зависимости текут строго сверху вниз: ни один самостоятельный проект не
   знает о своих потребителях — связи это либо read-only чтение с диска,
   либо vendored-контракты с пином.
3. Жёстких runtime-связей «код-к-коду» всего две: robin-runtime → prograph-vault
   (KB-mount) и steward → spec-runner (vendored SpecMeta, пин). Остальные пары
   переживают отсутствие партнёра деградацией, а не падением.
4. Maestro формально запускается без экосистемы (arbiter-роутинг опционален,
   подтверждено README: advisory/authoritative vs static-routing без секции),
   но без внешних agent-CLI он ничего не исполняет.

## Таблица

Легенда: 🟢 самостоятельный · 🟡 самостоятельный сервис, ценность в связке ·
🔴 работает только в связке.

| Проект | Режим | Без кого не работает / бессмыслен | Опциональные связи · потребители | Внешние зависимости (не экосистема) |
|---|---|---|---|---|
| **atp-platform** | 🟢 | — | потребители: Maestro (validation/benchmark), участники через SDK | LLM API, Docker (часть адаптеров) |
| **spec-runner** | 🟢 | — | потребители: Maestro, steward, spec-runner-vscode | Claude CLI (обязателен) |
| **proctor** | 🟢 | — | потребитель: dispatcher (read-only) | NATS, Docker, Ollama (опц.) |
| **deployer** | 🟢 | — | — | Docker, LLM API |
| **open-prose** | 🟢 | — (спека, нет runtime) | — | — |
| **robin-toolkit** | 🟢 | — (методология/скилы) | downstream: robin-runtime | — |
| **git-checker** (github-checker) | 🟢 | — (любые GitHub-репо) | потребитель: скилл fleet-check в devtools | gh CLI (без него — git-only) |
| **prograph-vault** | 🟢* | — (технически Obsidian-vault) | читатели: robin-runtime, люди; писатель: prograph | — |
| **arbiter** | 🟡 | сам как MCP-сервер работает; продукт (маршрутизация) существует ради клиента | клиент: Maestro (vendored контракт 1.1.0); данные: benchmark_runs ← ATP | — |
| **Maestro** | 🔴 | спаунеры внешних coding-агентов (Claude Code / Codex / Aider CLI) | arbiter (опционален: advisory/authoritative; без секции — static routing), spec-runner (`plan --full`), ATP (validation/benchmark) | agent-CLI, git worktrees |
| **dispatcher** | 🔴 | хотя бы один наблюдаемый: atp / Maestro / arbiter / spec-runner / proctor | VSCode-ext поверх его API | — |
| **prograph** | 🔴 | workspace с проектами (его предмет) | писатель в prograph-vault/derived (план) | — |
| **robin-runtime** | 🔴 | prograph-vault (KB-mount, жёстко) + LLM API | ecosystem-репо read-only; Telegram-адаптер | Claude Agent SDK/API |
| **steward** | 🔴 | spec-runner (vendored SpecMeta, пин — жёстко) | Maestro (project.yaml, компиляция вниз), git/CODEOWNERS | gh |
| **spec-runner-vscode** | 🔴 | spec-runner (его CLI/JSON-контракты) | — | VSCode, npm |

\* prograph-vault самостоятелен технически, но по содержанию — знания *о*
других проектах, как и весь 🔴-слой.

## Наблюдения

1. **Расслоение здоровое.** Исполнители и платформы внизу стека (atp,
   spec-runner, proctor, deployer) автономны; «только-в-связке» — это
   надстройки наблюдения/управления. Направление зависимостей одно: сверху
   вниз. Это главный механизм, который позволяет polyrepo не рассыпаться.
2. **Классы связей.** (а) read-only чтение артефактов с диска — dispatcher,
   prograph, robin: партнёр не обязан быть запущен и даже установлен;
   (б) vendored-контракт с пином — Maestro↔arbiter, Maestro↔spec-runner,
   steward↔spec-runner: изменения ловятся drift-чекером, а не падением в
   runtime; (в) жёсткий mount — robin-runtime → prograph-vault (единственная
   связь, без которой партнёр не стартует по смыслу).
3. **Невидимость файловых связей для графа.** Связи класса (а) не
   детектируются prograph (он видит deps/контракты/MCP) — кейс proctor,
   2026-07-10. Контроль: `devtools/check-graph-registry-drift.py`
   (граф ↔ карта интеграций COWORK_CONTEXT).
4. **git-checker и repos.sh перекрываются частично** (локальный git-статус),
   но git-checker добавляет GitHub API-слой (PRs, issues, security alerts,
   rulesets) и headless JSON-снапшот — он и закрывает «PRs/issues»-часть
   fleet-гигиены; потребитель — fleet-check.

## Рекомендуемые действия

1. Держать этот файл источником для Robin (grounded-ответы на вопросы вида
   «может ли X работать без Y»); обновлять при изменении карты интеграций.
2. Проверку «каждая связь карты интеграций имеет ребро в графе prograph» —
   гонять регулярно (`check-graph-registry-drift.py`, кандидат в `make`-цель).
3. При появлении новой связи между проектами фиксировать её класс
   ((а)/(б)/(в) из наблюдения 2) — класс определяет, чем ловить её деградацию.

## Ссылки

- COWORK_CONTEXT.md — реестр, карта интеграций, контрактные точки
- .prograph/projects/*.md — паспорта (public surface, contracts, MCP tools)
- Maestro/README.md §Optional: Arbiter routing — опциональность роутинга
- dispatcher/dispatcher/core/collectors/ — файловые read-only связи
- devtools/check-graph-registry-drift.py — контроль граф ↔ реестр
