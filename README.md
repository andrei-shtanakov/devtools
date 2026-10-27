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
| `todo_context.py` | context-pack по ОДНОМУ пункту `TODO.md`: строка и её отступное тело, `goal`/`notes` эпика из `epics.toml`, соседи по `@blocked_by`, предупреждения о пути, на котором строит один открытый пункт и который удаляет другой, доки, названные пунктом или ссылающиеся на его `@id`, правила репо и (по `--issues`) исходный inbox-issue. Каждый источник отчитывается `read`/`absent`/`not_queried`/`error`, непрочитанные названы в `unknown_sources`; `execute_allowed` считается по НАЙДЕННОМУ содержимому (написанное требование, с порогом), а не по состоянию источника — состояние отвечает «смотрели ли», не «есть ли требование». Парсинг и граф — пакет `plan-fields`, поэтому `uv` + Python 3.12; `make todo-context ARGS='--repo <r> --id <id>'` |
| `todo_worker.py` | прогон ОДНОГО пункта `TODO.md` поверх пака `todo_context.py`: `--mode plan` читает и предлагает, `--mode execute` правит чекаут и гоняет тесты — и только при `execute_allowed` пака, иначе отказ с кодом 4 и печатью причины. Publish-фаз нет by design (ни commit/push/PR/merge), результат — структурированный JSON в `out/todo/<repo>/<id>/`, не в целевом репо. Харнесс — вшитый `codex`, как в `issue_worker.py`; `make todo-work ARGS='--repo <r> --id <id>'` |
| `issue_console.py` | TUI всех открытых issues: дата, inbox/acceptance, инициатор, тип, группировка и запуск выбранных issues в отдельных `tmux` sessions. Acceptance — через пакет `plan-fields`, поэтому `uv` + Python 3.12, как у `check-plan-fields.py`; `make issues` |
| `discover_models.py` | discovery моделей провайдеров (ADR-ECO-003a): отчёт + Plane-1 TOML для PR |
| `discovery/` | offline-манифесты observed-моделей |
| `governance/` | конвейер behaviour-spec: этап A — пин steward + characterization, merge_gate (оси ADR-ECO-011 × safety steward), prospective stale-адаптер, bundle_state; этап B — `runner.py` (шаговая машина S0–S8), `console_model.py` (read-only view-model), `console.py` (behaviour console TUI, см. ниже); `design_guard.py` — чистый парсер DSL Q requirements/design (резолюции архитектурных вопросов) и покрытие, без git/файловой системы; `acceptance_guard.py` — чистый парсер AC-DSL acceptance-узла (критерии приёмки qa) и покрытие Must-требований FR/NFR входного набора behaviour/requirements, без git/файловой системы; `decomposition_guard.py` — чистый парсер DT-грамматики decomposition-узла (tech-lead-разбивка design на задачи) и инварианты графа (сюръекция BEH, single-owner, verify-контракт, ацикличность, стоки групп), без git/файловой системы; `spec_loop.py` — операторская кнопка `make spec-loop SUBJECT='…' REPO=…`: одна команда ведёт workstream через весь конвейер с остановкой на каждой человеческой границе (мерж бандл-PR, approve tasks-спеки); merge-authority жёстко human, повтор ищет прогон по (repo, subject), неоднозначности — fail-closed; `task_bridge.py` — draft tasks.md-спека из вмерженного бандла PR-ом в репо-владелец (`make behaviour-tasks ARGS='--run-id …'`; approve — человек), на полном DAG (с acceptance и decomposition) — 1 DT = 1 задача через `render_tasks_dt`; легаси-бандлы доставляются через `--legacy-bundle=3` (charter→requirements→behaviour-spec, без design/acceptance/decomposition), `--legacy-bundle=4` (+ design, без acceptance/decomposition) или `--legacy-bundle=5` (+ decomposition, но без acceptance — бандл до раскатки acceptance-узла, decomposition пинует только design) с ТОЧНЫМ фактическим составом бандла; `--approve-node <node-id>` выносит узел активного DAG на одобрение candidate-PR-ом, а после человеческого мержа этого PR записывает подпись финализирующим PR-ом — это ЕДИНСТВЕННАЯ дорога узла в `approved` (§I12); волновой режим (`make spec-loop … ARGS='--waves'`, `make behaviour-run ARGS='start --authoring waves …'`; спека `docs/superpowers/specs/2026-09-22-sequential-node-approval-design.md`): узлы одобряются по ходу авторинга — пять candidate-PR по уровням DAG (W1 charter … W4 design+acceptance одной заявкой, W5 decomposition), заявка волны несёт байты ветки волны (`source_sha`), edge-check (`governance/edge_check/`, координатор + адаптер публикации) обязателен на каждой волне и публикуется в candidate evidence-коммитом и одобряющим ревью, finalize мержит агент на resume, `make behaviour-run ARGS='reopen --run-id … --node <node>'` переоткрывает узел и ведёт `stale` нижние уровни через переодобрение по base; консоль показывает волну и per-wave op'ы; последовательность на узел: candidate-PR → его мерж человеком из allowlist (это и есть акт подписи; ОГОВОРКА: пока источник allowlist контролирует исполнитель — значение читается из окружения запускающего процесса, — независимая авторизация НЕ гарантируется, проверка есть defense-in-depth, `@id:approver-policy-trusted-source`) → `--approve-node` снова, и если у finalize-PR ещё нет действующего одобряющего ревью — отказ с названным шагом (`review-pr.sh <pr>`, бесплатная scope-аттестация для подтверждённо прозаического PR) и повтор `--approve-node`, продолжающий ТОТ ЖЕ finalize-PR, и порядок работы над новым воркстримом теперь такой: сгенерировать DAG → одобрить узлы топологически (`--approve-node`) → доставить; доставка одобренность только ПРОВЕРЯЕТ и подписей не ставит, поэтому её отказ на свежем бандле штатен; `--supersede` переиздаёт tasks-спеку новой ревизией (`tasks-deliver-v<N>`) после correction'а апстрима вместо мутации доставленной v1, `--abandon-revision <n>` (вместе с обязательным `--reason`) переводит незавершённую ревизию переиздания в терминальный `abandoned`, а `--replace-revision <n>` (только вместе с `--supersede` и тем же `--reason`) отзывает НЕЗАМЕРЖЕННОЕ предложение завершённой ревизии: новая ревизия несёт `replaces_revision`/`replaces_pr`/`replacement_reason`, PR отозванной закрывается механикой внутри crash-safe порядка, её ветка удаляется после создания нового PR — запись отозванной ревизии при этом не мутируется, а вмерженный PR не отзывается никогда. Спека: docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md. Тесты: `uv run --frozen --group governance pytest tests/test_governance_*.py` |
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
make issues      # fleet issue TUI (space — выбрать, g — группировать, enter — запустить)
```

### Issue console (первый TUI-срез)

`make issues` требует авторизованный `gh`, `tmux` и `uv` (acceptance-колонка
резолвится через пакет `plan-fields`, как у `check-plan-fields.py` — см. ниже).
По умолчанию выбранные issues запускаются в безопасном режиме `plan`:
отдельный Codex worker (`issue_worker.py`) только анализирует issue и пишет
структурированный результат по JSON Schema в
`out/issues/<repo>/<number>/result.json` (в `devtools/`, не в целевом репо).
Клавиша `x` переключает режим на `execute`; в нём worker может менять файлы и
запускать тесты, но намеренно не делает commit, push, PR или merge. Эти
publish-фазы будут добавлены после фиксации критериев принятия и review для
каждого типа. Decision (`accept`/`reject`) — детерминированная policy-политика
до вызова Codex: внутренний инициатор → accept, внешний → reject; модель может
только поднять `needs_human`, а не перевернуть политику.

Клавиши: `space` — выбор, `g` — цикл `date → repo → author → date`, `j/k` или
стрелки — навигация, `enter` — отдельная tmux-сессия на issue (повтор на уже
запущенном — подсказка attach), `b` — отправить выбранные issues в
behaviour-конвейер (`governance/runner.py` S0–S8: tmux-сессия
`beh-issue-<repo>-<n>`, subject из заголовка + `repo#N`, ws-id
`WS-<repo>-<N>`, target — локальный клон; только internal-инициатор,
внешний — сперва человеческий triage), `q` — выход. Колонка acceptance в списке:
`A`/`N`/`U`/`-` = accepted / not-accepted / unverifiable / n/a (n/a — issue без
лейбла `inbox`). `*` после логина инициатора = internal (влияет на policy-гейт
worker'а: internal → accept, external → reject).
Для CI и диагностики есть нетерминальный режим:

```bash
uv run --frozen python issue_console.py --json
uv run --frozen python issue_console.py --input issues.json --json  # полностью offline
```

Тип сначала определяется без AI по labels/title/body. Неоднозначность остаётся
`unknown`, а не угадывается; при запуске отдельный `issue_worker.py` возвращает
структурированный результат (`decision`, `kind`, `todo`, `next_step`,
`changed_files`), а не свободный текст.

Флаг `--classify-ai` доклассифицирует оставшиеся `unknown` батчем через codex
(`issue_classify.py`): ответы кэшируются в `out/issue-kind-cache.json` по ключу
`owner/repo#number@updatedAt`, применяются только при confidence ≥ 0.75, кэш
эволюционирует по факту обновления issue. Без флага Codex не нужен — консоль
и `--json`/`--input` полностью офлайн. `--internal <login>` (повторяемый флаг)
**заменяет** дефолтный набор внутренних инициаторов `{andrei-shtanakov,
ai-prosto}` целиком, а не дополняет его.

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

**`DT-BLOCKER-CYCLE` (error, только fleet-режим).** Цикл в графе `@blocked_by` —
дедлок: каждый участник ждёт другого, начать не может никто, и валидного прочтения
у такой конфигурации нет. Правило живёт в devtools, а не в общем пакете, потому что
цикл — свойство **собранного графа флота**: половина цикла из своего репо не видна,
и 2026-08-22 взаимное ожидание atp-platform ↔ maestro прошло обе собственные
проверки и было поймано руками в открытом PR, за один мерж до main. Единица находки —
компонента сильной связности целиком, а не отдельное обратное ребро: внутри неё
каждый ждёт каждого транзитивно, и назвать одно ребро значило бы преуменьшить объём
застрявшего. Участники сортируются, поэтому один и тот же граф всегда рендерится
одинаково.

ADR-ECO-005a разделяет две независимые оси. Ownership публикуется как
`human-owned | repo-owned | self-owned | TBD | missing | invalid-owner |
unknown-repo-owner`; movement — как `actionable | waiting-by-trigger |
waiting-by-blocker | stale-condition | malformed-condition`. `plan-check`
печатает обе суммы и полную матрицу ownership × movement. Каждая открытая
строка входит ровно в одну ячейку, включая строки без `@id`; trigger или blocker
никогда не превращается в owner. При конфликте условий fail-closed приоритет:
`malformed-condition` → `stale-condition` → blocker → trigger → actionable.
`self-owned` — `@owner:repo:<свой репо>` (devtools#381): пункт не передан
никакому внешнему принципалу, и каждый такой пункт виден ещё и warning'ом
`PF-OWNER-REPO-SELF` с адресом `todo://`. Self vs external решает пакет
(`repo_owner_verdicts`) — поэтому только для пунктов с `@id`: у пункта без
`@id` вердикта нет, и он классифицируется по ключу манифеста, то есть свой
репо там считается `repo-owned`. Недосчёт принят, а не скрыт: такой пункт и
так несёт `PF-ID-MISSING`, а вторая нормализация identity в devtools —
ровно то, от чего заявка просила уйти.
Owner grammar не копируется в devtools: даже для строк без `@id` используется
публичный `plan_fields.parse_owner()` из immutable pin.

## Behaviour runner (governance)

`governance/runner.py` — шаговая машина S0–S8 конвейера behaviour-spec (этап B,
этап A — governance-ядро выше в таблице). Полная цепочка авторимых узлов —
шесть: charter → requirements → behaviour-spec → design → acceptance →
decomposition; за S4-гейт каждого узла отвечает свой чистый гард
(`design_guard.py`, `acceptance_guard.py`, `decomposition_guard.py` — все три
выше в таблице). CLI-обвязка строит `RealOps`
(единственная точка внешних эффектов: git/gh/codex/gate-check) и печатает
человекочитаемый дамп `RunState` после каждой команды:

```bash
make behaviour-run ARGS="start --subject '...' --repo alpha --repo-slug owner/alpha \
    --ws-id WS-1 --target-dir /path/to/alpha"
make behaviour-run ARGS="resume --run-id WS-1-a1b2c3"
make behaviour-run ARGS="verify --parent WS-1-a1b2c3 --run-id WS-1-a1b2c3-fix"
make behaviour-run ARGS="status --run-id WS-1-a1b2c3"
# = uv run --frozen --group governance python -m governance.runner ...
```

`--bundle-dir` по умолчанию `workstreams/<ws-id>/spec`, `--run-id` по
умолчанию `<ws-id>-<3 случайных байта hex>`. Прогоны живут вне worktree
целевого репо, под `devtools/out/governance-runs/<run-id>/run.json`
(write-ahead журнал, спека §4) — не коммитятся, не публикуются.

`--author-backend codex|disp` (дефолт `codex`) выбирает, чем авторится
behaviour-spec узел: `codex` (дефолт, `ops.author`) или `disp` (opt-in,
`ops.author_disp`, полируемый документ) — единственная точка входа в
disp-цикл. charter/requirements авторятся `codex` независимо от значения;
переключение затрагивает только behaviour-spec (B2 Task 2).

`REPO` — `git_dir` из манифеста флота (`ai-orchestrators-workspace/
workspace-manifest.toml`). Цели живой приёмки вне флота (сейчас — `spec-loop-sandbox`)
перечислены в `contracts/acceptance-targets/v1/targets.toml` (authority-root;
цель обязана быть публичной и проходить стоп-кран — opt-in проба
`DEVTOOLS_TARGETS_PROBE=1`) и берутся, только
если репо нет в манифесте, а сам манифест прочитан без ошибок; origin
целевого чекаута сверяется так же, как для флота.

Готовый discovery-brief можно сделать source-входом нового workstream:

```bash
make spec-loop SUBJECT="Published workflow app" REPO=alpha \
  ARGS="--brief /path/to/brief.md"
```

`customer` brief сам является источником FR/NFR. Для `engineer` brief его
`traces_to` обязан называть ровно один переносимый относительный путь к
`approved customer` brief; в bundle побайтово попадут оба файла. Source-файлы
принимаются только как UTF-8 с LF: CR/CRLF отсекаются до авторинга, чтобы eol-
нормализация целевого Git не могла изменить immutable blob. Source layer
лежит под `workstreams/<ws-id>/spec/00-discovery/` (в коммит S3 добавляется
`git add -f` поштучно и сверяется по blob-ам в HEAD — ignore-правила цели его
не теряют), пинуется charter и входит
в `content_anchor`. До author-вызовов вход проходит вендоренный discovery
gate; после requirements отдельный гард проверяет точный перенос каждого
source FR/NFR и сохранение `Must`.

E1 принимает уже выпущенный brief и не запускает discovery-интервью. Стадию
Need (E2) запускает `--need`, когда готового брифа ещё нет — вместо brief-
входа, взаимоисключающе с `--brief`:

```bash
make spec-loop SUBJECT="Published workflow app" REPO=alpha \
  ARGS="--need --frame customer --stakeholder po"
```

`--frame customer|engineer` обязателен вместе с `--need`, `--stakeholder
<role>` — декларация реального стейкхолдера (не проверка, но без неё стадия
не стартует). `engineer`-фрейм требует `--traces-to <customer-brief.md>` —
бриф, подписанный человеческим мержем brief-PR (`make brief-propose` →
`make human-merge` → `make brief-approve`, последняя печатает готовую
команду), и `--approval-pr <brief-PR>`; происхождение и политика подписи
проверяются по фактам форджа (спека need-stage §11). Команда запускает discovery-интервью, прогон паркуется в
постоянном статусе `waiting_interview` — bundle-ветка и worktree целевого
репо ещё не создаются — и печатает точную команду ответа вида
`discovery answer --session <id> --role <role> --file answer.yaml`.
Стейкхолдер отвечает ЭТОЙ командой вне spec-loop, затем оператор повторяет
кнопку с тем же `--run-id`:

```bash
make spec-loop SUBJECT="Published workflow app" REPO=alpha \
  ARGS="--need --frame customer --stakeholder po --run-id <run-id>"
```

Стадия останавливается статусом `stopped_interview` двумя способами: если
discovery отверг ответ — файлом findings рядом с прогоном и подсказкой
повторного ответа с `--supersede`; если это операционный отказ discovery —
подсказкой восстановить ту же сессию. Восстановление доступно двумя
флагами, каждый только в своей ситуации: `--session <id>` присоединяет
сессию к сироте (interview начат, но `session_id` ещё не записан);
`--new-run --ws-id <fresh-id>` начинает новый прогон и допустим только
до стадии S1 (пока стадия Need не продвинулась дальше). Без
`--brief`/`--need` CLI и исторические ledger работают по прежнему
classic-пути.

Сегодня S7 (merge_gate) у любого прогона уходит в `waiting_human_merge`: по
данным вендоренной копии steward-политики (`contracts/steward-actor-policy/v1/`)
`agent_merge_allowed=false`, а `ai-prosto` не входит в `agent_identities` —
это факт политики, не баг раннера. Включение агентского мержа — решение
steward-а (правка политики + bump пина вендоренной копии в этом репо), не
правка `runner.py`.

### Behaviour console (TUI)

`make behaviour-console` запускает `governance/console.py` — read-only TUI
поверх готовых run-журналов из `devtools/out/governance-runs/`
(`governance.console_model`, Task 3): сам модуль не пишет `run.json` и не
запускает пайплайн.

Клавиши: `enter` — открыть деталь выбранного прогона отдельным экраном
(ops-журнал, findings, verdict reason, срез бандла; `q`/`escape` — назад);
`r` — resume выбранного прогона; `v` — verify (только для ряда в статусе
`merged_unverified` — на любом другом статусная строка объясняет отказ,
TUI не падает); `q` — выход из консоли.

**Без TUI:** non-TTY (stdin/stdout не терминал — CI, pipe) без `--json`
даёт обычную plain-таблицу; `--json` печатает список прогонов JSON'ом на
ЛЮБОМ терминале (TTY или нет); `--run-id <id>` печатает деталь одного
прогона (`detail_to_json(run_detail(<id>))`) и тоже не запускает TUI:

```bash
make behaviour-console ARGS="--json"          # все прогоны JSON
make behaviour-console ARGS="--run-id <id>"   # деталь одного прогона
```

`textual` — тяжёлая опциональная зависимость (uv-группа `governance`),
импортируется лениво только внутри интерактивной TUI-ветки — обе ветки
выше работают и без установленного `textual`.

**Tmux-интеграция:** `r`/`v` НЕ безэффектны — они поднимают `make
behaviour-run` в отдельной фоновой tmux-сессии (`tmux new-session -d`):
`r` — сессию `beh-<run_id>`, `v` — `beh-verify-<parent_run_id>` (имя от
РОДИТЕЛЯ, не от свежесгенерированного id потомка — иначе повторное
нажатие `v` на том же ряду поднимало бы вторую сессию и создавало второй
remediation-issue у уже помеченного родителя). Повторный вызов на уже
запущенную цель находит существующую сессию (точный `=`-таргет, не
префиксный матч) и подсказывает `tmux attach -t =<session>` вместо нового
запуска. Сессия самозакрывается сразу после `make behaviour-run` (Enter —
закрыть немедленно, автоматически — не позже чем через 60с простоя): она
не висит вечно, поэтому дедуп по имени защищает только РЕАЛЬНО идущий
прогон, а не любой когда-либо запущенный — повторное `r`/`v` после
завершения прогона поднимает новую сессию, а не только даёт attach.

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

## Установка DarkFactory: этап 1

Пакет `df/` (спека `docs/superpowers/specs/2026-10-13-df-installer-design.md` §3, план
`docs/superpowers/plans/2026-10-16-df-installer-stage1.md`) ставит на хост команду
`darkfactory` и релиз профиля из манифеста зонтика. Этап 1: проверка манифеста, lock-файлов
членов и утверждённых CLI; учётка сборщика `df-build`; сборка релиза от её имени в
`/opt/df/releases/<id>`; приём копией root, проверка и активация; административный Python и
код команды отдельно от релиза (`/opt/df/admin-python/`, `/opt/df/admin/<id>/`); выбор
моделей ролей (`models`). Рабочих ролей (`df-exec`, `df-review`, `df-publisher`), sudoers и
запуска агентов здесь нет — это этап 2.

Измерено (CI `df-stage1`) на macOS 15 arm64 и Ubuntu 24.04 x86_64; иные хосты, в том числе
другие выпуски Linux, не измерялись.
Этап 2 требует классический sudo 1.9 (sudo.ws): sudo-rs (замер: раннер CI Ubuntu 26.04)
отклоняется с инструкцией переключить альтернативу sudo на sudo.ws
(`sudo update-alternatives --config sudo`); измерено на Ubuntu 24.04.

### Подготовка

1. Цепочки путей к `/opt/df`, `/etc/df` и `/usr/local/libexec/darkfactory` (каждый каталог
   от `/`, в том числе `/opt`, `/etc`, `/usr/local/libexec`): владелец root, без записи для
   группы и всех, без ACL. Иначе `darkfactory` отказывает и называет путь. На штатном образе
   GitHub Ubuntu 24.04 `/opt` открыт на запись группе и всем и несёт ACL — CI `df-stage1`
   перед установкой выполняет `sudo chmod go-w /opt` и `sudo setfacl -b /opt`.
2. `git`: на Ubuntu — `sudo apt-get install git` (в минимальном образе `/usr/bin/git` нет);
   на macOS — Command Line Tools, выбранные root (ниже, «macOS: каталог разработчика»).
3. `uv` 0.11.23 — копия в каталог root (ниже, «Утверждённые CLI»).
4. Python ≥ 3.12 для bootstrap. На Ubuntu 24.04 — системный `/usr/bin/python3` (3.12). На
   macOS системный `/usr/bin/python3` — 3.9; поддерживаемый вариант — Python, поставленный
   root утверждённым uv из п. 3 в каталог root вне дерева релиза:

   ```sh
   cd / && sudo -H /usr/local/libexec/darkfactory/bin/uv --no-config python install 3.12.13 \
     --no-bin --install-dir /usr/local/libexec/darkfactory-bootstrap/python
   ```

   Интерпретатор для bootstrap:
   `/usr/local/libexec/darkfactory-bootstrap/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12`.

   `sudo` на macOS сохраняет `HOME` человека, а root-овский uv читал бы его настройки. Каждая
   часть рецепта закрывает своё (тот же класс, что `UV_NO_CONFIG` установщика, решение
   владельца 22.10):
   - `--no-config` — не читаются ни пользовательская конфигурация человека
     (`~/.config/uv/uv.toml`), ни конфигурация проекта (`uv.toml`, `[tool.uv]` в
     `pyproject.toml`) в каталогах над текущим; любая из них могла бы подменить источник
     загрузки Python (`python-install-mirror`, `python-downloads-json-url`), и root поставил
     бы чужой интерпретатор;
   - `cd /` — текущий каталог вне любого проекта;
   - `sudo -H` — `HOME` root-а: в кеше человека (`~/.cache/uv`) не появляются файлы root;
   - `--no-bin` — uv не кладёт ссылку `python3.12` в `~/.local/bin`.
5. Чекаут этого репо и полный SHA коммита манифеста зонтика.

### Первая установка

```sh
sudo <python-3.12+> -I -B -S <чекаут-devtools>/df/bootstrap.py init \
  --manifest-repo andrei-shtanakov/ai-orchestrators-workspace \
  --manifest-sha <sha-манифеста> \
  --profile core \
  --cli git=/usr/bin/git \
  --cli tar=/usr/bin/tar \
  --cli uv=/usr/local/libexec/darkfactory/bin/uv
```

- `<python-3.12+>` — интерпретатор из п. 4 подготовки (на Ubuntu 24.04 — `/usr/bin/python3`);
  `<sha-манифеста>` — полный SHA коммита манифеста зонтика, 40 **строчных** hex (заглавные —
  отказ манифеста); `<чекаут-devtools>` — чекаут этого репо.
- Root исполняет код из чекаута человека: доверие к этому чекауту и к интерпретатору — на
  совести человека (он и так держит `sudo`). Bootstrap находит корень чекаута по пути
  самого файла, а не по `cwd` или `PATH`.
- Флаги `-I -B -S` обязательны, без них bootstrap отказывает; Python < 3.12 — тоже отказ.
- Python профиля — только `3.12.13`: для иной версии нет измеренного списка удаляемых
  компонентов Tcl/Tk, и шаг `manifest` отказывает до любого изменения хоста.
- Профиль `core` описан в манифесте зонтика (`[df.profiles.core]`, коммит
  `080f15e6b8fa3497a369f959e5466c1391bf2700`); его живая установка этой командой — в CI
  `df-stage1` (раздел «Приёмка»).

Дальше — команда (ссылка `/usr/local/bin/darkfactory`, если `/usr/local/bin` проходит
политику доверенных путей; иначе установщик печатает полный путь `/opt/df/bin/darkfactory`):

```sh
sudo darkfactory doctor
sudo darkfactory models set <роль> --harness claude|codex --model <модель>
darkfactory models list
sudo darkfactory install    # переустановка по /etc/df/install.toml
```

Bootstrap нужен только до первой активации и для восстановления, когда команда отказывает;
при исправной команде он отказывает сам: «используйте `darkfactory`».

### Утверждённые CLI

`git`, `tar`, `uv` — только пути root (спека §3.10): каждый компонент пути от `/` — владелец
root, без записи для группы и всех, без ACL; нативные зависимости — только из системных
каталогов. CLI, которым владеет человек (Homebrew в `/opt/homebrew` и т. п.), политику не
проходит, и `darkfactory` не меняет владельцев чужих каталогов. Поддерживаются:

- системные `/usr/bin/git` и `/usr/bin/tar` (на обеих ОС);
- `uv` — копия бинарника `uv` 0.11.23 (закреплённая версия; иная — отказ) в каталог root.
  Homebrew, `pipx` и скрипт установки без версии ставят последнюю версию — брать архив
  официального релиза [0.11.23](https://github.com/astral-sh/uv/releases/tag/0.11.23):
  `uv-aarch64-apple-darwin.tar.gz` (macOS arm64) или `uv-x86_64-unknown-linux-gnu.tar.gz`
  (Linux x86_64). Сверить с опубликованной рядом контрольной суммой (`<архив>.sha256`; на
  26.10 — macOS `71ef9de85db820749b3b12b7585624ee279e9c5afcbc6f8236bc3d628c4305b0`, Linux
  `e12c4cda2fe8c305510a78380a88f2c32a27e90cdcd123cefd2873388f0ebb5f`), затем версию:

  ```sh
  shasum -a 256 -c uv-<цель>.tar.gz.sha256     # Linux: sha256sum -c …
  tar -xzf uv-<цель>.tar.gz
  ./uv-<цель>/uv --version                       # uv 0.11.23
  sudo install -d -o root -m 0755 /usr/local/libexec/darkfactory/bin
  sudo install -o root -m 0755 ./uv-<цель>/uv /usr/local/libexec/darkfactory/bin/uv
  ```

Пути закрепляются в `/etc/df/install.toml`.

**macOS: каталог разработчика.** `/usr/bin/git` на macOS — обёртка `xcode-select`: она
передаёт запуск инструменту из выбранного каталога разработчика, и `darkfactory` проверяет
по той же политике доверия и этот инструмент, и цепочку путей к нему. Отказ значит:
выбранный каталог разработчика или его цепочка путей не проходит политику доверия
(например, каталог принадлежит пользователю или родитель открыт на запись группе). Причина —
политика пути, а не имя приложения.

Поддерживаемый вариант — отдельно поставленные Command Line Tools
(`/Library/Developer/CommandLineTools`, каталог root), выбранные root:

```sh
xcode-select --install
sudo xcode-select -s /Library/Developer/CommandLineTools
```

(так настроены runner-ы CI `df-stage1`). Выбор каталога разработчика — общий для всей
машины: после него `git`, `make`, `xcodebuild` всех пользователей идут через Command Line
Tools. Возврат — `sudo xcode-select -s <путь к Xcode>/Contents/Developer`, и тогда
`darkfactory` снова откажет.

### Правила запуска

- **Python релиза не запускать от root.** Прежде всего это граница доверия: интерпретатор и
  код релиза — артефакт сборщика (`df-build` писал это дерево), а root не исполняет ничего,
  к чему сборщик имел доступ на запись (спека §3.9); у команды свой административный Python.
  Запись байт-кода — дополнительное последствие: `-I` игнорирует
  `PYTHONDONTWRITEBYTECODE`, байт-код ложится в `__pycache__` релиза, и `doctor` покажет
  digest «нарушено»; `-B` не превращает такой запуск в допустимый.
- **Рабочий каталог запуска от роли — вне дерева сборщика.** На macOS `sudo` открывает свой
  рабочий каталог с `euid = 0`, поэтому запуск от роли с рабочим каталогом в дереве сборщика
  нарушил бы правило «root не открывает источник». Это обеспечивает запускатель, а не
  оператор: на этапе 1 рабочий каталог запусков от ролей выбирает сам `darkfactory` (у
  `df-build` — его домашний каталог, родитель источника), на этапе 2 — требование к
  `df-launch` (спека §3.8).

### Ограничения v1

- **Повреждение под незавершённой установкой** (спека §3.8): если незавершённая установка
  уже переключила `current` на повреждённый релиз, `init`, `install` и bootstrap отказывают с
  шагом `damaged` до любого изменения. Восстановление — вручную, шагами из текста отказа
  (сохранить копии `pending.toml` и журнала вне `/opt/df`, `/etc/df`, `/var/lib/df`;
  удалить `pending.toml`; ссылку `current` — только если отказ это называет; затем
  bootstrap). `current` вручную на прежний релиз не возвращать.
- **Снятие блокировки переустановки удалением `pending.toml`** (R53, §3.8) допускается после
  сохранения диагностических данных, но **не подтверждает** завершённость прежней установки:
  восстановление — новая установка с новым id, повреждённый релиз повторно не принимается.
  Второе ручное снятие (`current` удалён раньше `pending.toml`, затем `init` с другой
  конфигурацией) закрывает состояние с уликами — сохранить копии до него.
- **Root-владелец в карантине** (§3.3): после редкого сочетания сбоев дерево
  `/opt/df/staging/.quarantine/<id>.<n>` может остаться с владельцем root; каждая установка
  пишет его имя предупреждением `quarantine` в журнал. Безвредно, но само не исчезает:
  `sudo rm -rf /opt/df/staging/.quarantine/<id>.<n>` — имя из предупреждения, без `/` на конце
  и без шаблонов.
- **Python релиза без Tcl/Tk** (решение владельца 25.10, спека §3.4): `tkinter`, `turtle`,
  `idlelib` инструментам релиза недоступны.
- **Загрузчик Linux (R26, принято владельцем 22.10 как ограничение v1):** модель доверия к
  нативным зависимостям утверждённых CLI не охватывает подкаталоги `glibc-hwcaps`/`tls` и
  записи `ld.so.cache` вне `ld.so.conf`. Linux: измерено на Ubuntu 24.04 (штатный образ, там
  их нет); хост с пакетами hwcaps или своим кешем загрузчика — вне измеренного.
- На пути к `/opt/df` не должно быть символических ссылок (хост с `/opt` — ссылкой не
  поддерживается, §3.10).

### Почему не `df`

Имя `df` занято стандартной командой ОС (свободное место на диске), поэтому команда —
`darkfactory`, в `PATH` нет `df` и псевдонима нет. `df` остаётся внутренним именем пакета,
путей (`/opt/df`, `/etc/df`) и схем.

### Приёмка

CI `df-stage1` (`.github/workflows/df-stage1.yml`): macOS arm64 и Ubuntu x86_64, root-тесты
только в одноразовых runner-ах GitHub. Evidence —
`docs/evidence/2026-10-26-df-stage1-acceptance.md`. Живая установка профиля `core` по
манифесту зонтика (`080f15e`) — в двух формах: в root-мире тестов (`<R>`, в процессе,
`tests/df/test_acceptance_root_live.py`) и в раскладке `/` командой выше как процесс
(`tests/df/live_install.py`: подготовка по разделу «Подготовка», `sudo darkfactory doctor
--json` — уровень 1, `--help` каждой точки входа от непривилегированного пользователя);
результаты — evidence §9.

## Установка DarkFactory: этап 2

Роли и обёртки (`df-exec`, `df-review`, `df-publisher`, `/opt/df/libexec`, sudoers) ставит
`darkfactory roles setup` поверх исправной установки этапа 1. Ему нужен утверждённый
`openssl` (подпись JWT издателем, спека §3.7): на установке этапа 1 его нет, и `roles setup`
отказывает до любого изменения. Сначала задать путь, затем настроить роли:

```sh
sudo darkfactory cli set openssl /usr/bin/openssl
sudo darkfactory roles setup
```

Путь должен пройти политику утверждённых CLI (спека §3.10, как `git` и `tar` выше) на этом
хосте: проверка делается при `cli set`, в `doctor` и при каждой установке; не прошёл — отказ
с причиной, `install.toml` не меняется. `cli set` задаёт только `openssl`; повтор с тем же
путём ничего не пишет. Оператор `roles setup` — `SUDO_USER`, иначе `--operator <имя>`.
