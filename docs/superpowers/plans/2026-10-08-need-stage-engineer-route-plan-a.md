# Engineer-маршрут стадии Need — план реализации, часть A (основа)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** основа engineer-маршрута: вендоренный self-hash соседа, строгая заявка на
одобрение, факты форджа, предикаты акта и политики, блокировка прогона и порт
discovery. Часть B (`2026-10-08-need-stage-engineer-route-plan-b.md`) строит на ней
интеграцию в spec-loop/раннер, обёртки и `human-merge.sh`.

**Architecture:** чистые модули (`discovery_approval`, `approval_request`,
`brief_provenance`) поверх расширенного порта `Ops` (миксин фактов форджа
`brief_facts`); блокировка прогона — одна на вход процесса, функции раннера требуют
токен `RunLock`.

**Tech Stack:** Python 3.12 (как CI devtools, `requires-python = ">=3.12"`), `uv`, pytest, PyYAML, `gh` (GraphQL/REST), `fcntl.flock`.

**Spec:** `docs/superpowers/specs/2026-09-15-need-stage-design.md`, ревизия 7, **§11**
(ссылки «§11.x» — туда).

**Проверено исполнением.** Каждая задача этой части исполнена в одноразовом worktree
от `36107f3`; код и тесты ниже вставлены из проверенных файлов генератором (не
переписаны руками). Команды, результаты и ревизии — в журнале исполнения
(`_cowork_output/2026-10-08-e2-engineer-route/plan-exec/LOG.md`, dev-only), там же
патчи для повторения. Полный набор тестов гонять с группой `governance`:
`GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q` — без неё тесты
раннера пропускаются молча.

## Global Constraints

- Только `uv` (`uv run --frozen …`), никогда `pip`.
- Тесты: `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q`.
- Линт: `uv run --frozen --group selfcheck ruff format .` и `… ruff check .` — чисто.
- pyrefly (`uv run --frozen --group selfcheck pyrefly check`): новых ошибок нет, кроме
  `missing-import` (а) у вендоренных копий соседа (байты не правятся) и (б) у импорта
  соседнего модуля `tools/` из `tools/`/`tests/` — тот же шаблон, что у существующего
  `check_discovery_vendor`. `[tool.pyrefly]` в `pyproject.toml` НЕ заводить: он
  отключает пресет basic (замер: 28 → 1028 ошибок).
- Длина строки — 88.
- Соседние репо (discovery, approval-policy) — только чтение; заявки соседу —
  discovery#63.
- Доказательства происхождения — только факты форджа; `run.json` и локальный git
  источником не служат (§11.1.2).
- Историческая политика — `repo_file_fact` по SHA + `policy_rule.policy_accounts`;
  `policy_snapshot(pinned_sha=…)` для исторических версий не используется (он
  отказывает `superseded` до чтения содержимого).
- Строка подтверждения политики — ровно `policy-reconfirm: <repo>@<sha>`.
- Схема заявки — `discovery-brief-approval-request/v1`, файл `approval-request.yaml`.
- Все SHA в тестах — 40 hex (строгий разбор заявки иначе отвергает весь мир).
- В zsh переменная перед `:` берётся в фигурные скобки (`"${REV}:path"`): `$REV:s…` —
  модификатор подстановки.

## Review Focus

- **Неполная страница ответа форджа** (нет `pageInfo`, курсор стоит, голова PR
  сменилась между страницами): ожидание — `UNAVAILABLE`, не «что успели прочитать» —
  Task 3, `test_incomplete_pages_unavailable`.
- **Комментарий-подтверждение отредактирован или удалён после продолжения**:
  следующая перепроверка видит `W` прежним — Task 4,
  `test_edited_or_deleted_reconfirm_stops_counting`.
- **Сбой форджа при разборе подтверждения**: «повторите», а не «подтверждения нет»
  (иначе ложный дрейф либо продолжение по прежнему `W`) — Task 4,
  `test_unavailable_while_judging_reconfirm_is_retry`.
- **Две `make spec-loop` одного прогона из разных терминалов**: вторая выходит кодом 1,
  не прочитав `run.json` — Task 5, `test_second_entry_exits_before_reading_state`.
- **Родитель убит, дочерний процесс соседа жив**: блокировка держится до выхода
  дочернего — Task 5, `test_lock_survives_parent_death_via_pass_fds`.

---

## File Structure (часть A)

| Файл | Ответственность |
|---|---|
| `contracts/discovery-approval/v1/{approval.py,hashing.py,gate_check.py,PINNED.txt}` | байт-в-байт копии discovery + пин |
| `governance/discovery_approval.py` | загрузчик копии: `self_hash`, `verify`, константы |
| `tools/check_discovery_approval_vendor.py` | consistency / provenance / drift копии |
| `tools/gen_discovery_approval_fixture.py` | dev: подписать фикстуру настоящим discovery |
| `governance/approval_request.py` | строгий разбор и рендер заявки + CLI `check` |
| `governance/brief_facts.py` | типы фактов и миксин `BriefFactsMixin` для `RealOps` |
| `governance/ops.py` | `RealOps(BriefFactsMixin)`, сигнатуры Protocol, порт discovery |
| `governance/brief_provenance.py` | предикаты акта (§11.4.2) и политики (§11.4.3) |
| `governance/run_lock.py` | `run_lock(run_id)`, токен `RunLock`, `require` |
| `governance/runner.py`, `governance/spec_loop.py` | токен у функций раннера, блокировка на входе |
| `tests/forge_fake.py` | стенд форджа и согласованный мир |
| `tests/approval_request_cases.py` | общий набор случаев заявки (все потребители) |
| `tests/locked_runner.py` | вызовы раннера под блокировкой для тестов |

---

### Task 1: Вендоренный self-hash соседа

**Files:**
- Create: `contracts/discovery-approval/v1/approval.py`, `hashing.py`, `gate_check.py`,
  `PINNED.txt`
- Create: `governance/discovery_approval.py`, `tools/check_discovery_approval_vendor.py`,
  `tools/gen_discovery_approval_fixture.py`
- Create: `tests/fixtures/discovery_approval/{draft-brief.md,signed-brief.md,signed-brief.json}`
- Modify: `pyproject.toml` (`[tool.ruff] extend-exclude`)
- Modify: `.github/workflows/discovery-contract-integrity.yml` — обе гарантии копии в CI
  по образцу `check_discovery_vendor`: `consistency` и `provenance` в PR, `drift` по
  расписанию (добавлено при реализации: план исполнения это упустил)
- Test: `tests/test_discovery_approval.py`

**Interfaces:**
- Produces: `discovery_approval.self_hash(text: str) -> str`;
  `discovery_approval.verify(text: str) -> str | None`; `NotABrief`; `VendorError`;
  `SELF_HASH_KEY`, `DEBT_STATUS`, `DEBT_UNSIGNED`, `DEBT_MIGRATION`, `DEBT_SELF_HASH`;
  `load_module(path, expected_digest)`; `VENDOR`.

- [ ] **Step 1: Скопировать файлы соседа и записать пин** (из корня devtools)

```bash
# Пин — полный проверенный SHA; соседний чекаут только читается (`git show`).
SRC=../discovery; REV=94c88cc55ae29aa005855bbebf6f4ae6244a6f3b; V=contracts/discovery-approval/v1
mkdir -p "$V" tests/fixtures/discovery_approval
git -C "$SRC" show "${REV}:src/discovery/approval.py" > "$V/approval.py"
git -C "$SRC" show "${REV}:src/discovery/hashing.py" > "$V/hashing.py"
git -C "$SRC" show "${REV}:src/discovery/contract/gate_check.py" > "$V/gate_check.py"
{ echo "upstream: andrei-shtanakov/discovery"; echo "commit: ${REV}"
  for f in approval.py hashing.py gate_check.py; do
    echo "$f $(shasum -a 256 "$V/$f" | cut -d' ' -f1)"; done; } > "$V/PINNED.txt"
```

Проверенный пин (`discovery@94c88cc`):

```text
upstream: andrei-shtanakov/discovery
commit: 94c88cc55ae29aa005855bbebf6f4ae6244a6f3b
approval.py 4e8e919920e95858b95edfe7bb7a3ffa47ff6cc2109626a6505d718335e7d1fd
hashing.py fff45f4a1865b8652b037612c08582fa22a3e555afc4b81a1e098a0c50c09dfb
gate_check.py f226ab93f6f4b54f161c459746b589fe9a49c8d04f91a53eb55cb185a8c54524
```

В `pyproject.toml` (`[tool.ruff] extend-exclude`) добавить
`"contracts/discovery-approval/v1",` — копии не правятся линтером.

- [ ] **Step 2: Фикстура, подписанная настоящей командой `discovery approve`**

Черновик брифа вендорится в devtools как есть — `tests/fixtures/discovery_approval/draft-brief.md`:

```markdown
---
schema: discovery-brief
schema_version: 1
spec_stage: discovery
status: draft
generated_by: discovery-runtime
generated_at: '2026-09-15T10:55:06Z'
validation: pass
interview:
  frame: customer
  sessions:
  - participant_role: spec-runner-owner
coverage:
  goals: covered
  personas: covered
  jobs: covered
  functions: covered
  nfr: covered
  constraints: covered
  success_metrics: covered
  out_of_scope: covered
  risks: missing
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: []
---

# Discovery Brief — andrei-shtanakov/spec-runner (customer-фрейм)

## Goals

- **G-01** Позволить безопасно продолжить или проверить прерванный прогон spec-runner с другой машины без повторения уже оплаченных вызовов и без утраты фактов, определяющих допустимый следующий шаг
- **G-02** Обеспечить адресуемый вне машины evidence-след каждого платного вызова и каждого завершения прогона, включая failed, blocked, early-stop и crash-unknown пути
- **G-03** Сделать восстановление и независимый аудит прогона по run_id штатной и однозначной операцией после полной потери исходного рабочего каталога

## Personas

- **P-01** Оператор spec-runner, запускающий, наблюдающий и возобновляющий длительные прогоны
- **P-02** Владелец репозитория или workstream-а, оплачивающий вызовы и утверждающий бюджетные, waiver и remedy-решения
- **P-03** Независимый ревьюер или аудитор, проверяющий ход и результат прогона без доступа к исходной машине
- **P-04** Владелец платформы и безопасности, отвечающий за доступ, redaction и retention приватных checkpoint и evidence-артефактов

## Jobs-to-be-done

- **J-01** Когда длительный прогон прерывается из-за завершения машины, терминала или CI-runner, я хочу восстановить его с последней подтверждённой границы, чтобы продолжить без молчаливого повтора уже оплаченного вызова
  traces: [G-01, G-03]
- **J-02** Когда workstream передаётся другому оператору или переносится в новый рабочий каталог, я хочу восстановить continuation-state и следующий допустимый шаг по run_id, чтобы не реконструировать claims и authority-решения вручную
  traces: [G-01, G-03]
- **J-03** Когда я проверяю расходы и исходы прогонов за неделю, я хочу получить полный evidence-след success, failed, blocked, refusal и crash-unknown путей, чтобы подтвердить стоимость и обоснованность каждого продолжения
  traces: [G-02, G-03]

## Functional Requirements

- **FR-01** Назначать каждому CLI-прогону один глобально уникальный run_id и связывать с ним все вызовы, attempts, checkpoints, evidence и завершение
  **Priority**: Must
  **Acceptance**: Один full UUIDv4 создаётся ровно один раз на invocation и без изменения присутствует в structlog/OTel, audit, call records, checkpoint manifest, evidence bundle и run closure; pipeline_id хранится отдельно
  traces: [J-01, J-02, J-03]
- **FR-02** Записывать durable-намерение перед каждым платным subprocess и запрещать запуск без подтверждения записи
  **Priority**: Must
  **Acceptance**: Перед стартом процесса существует подтверждённый call-start с run_id, call_id, stage/provenance, policy identity и optional task/attempt; сбой после call-start без результата оставляет open call и не допускает silent retry
  traces: [J-01, J-03]
- **FR-03** Публиковать согласованный continuation checkpoint после каждого изменения состояния, необходимого для продолжения
  **Priority**: Must
  **Acceptance**: После attempts, checkpoints, claims, verify-evidence, waiver, remedy и budget authorization доступен новый checkpoint; тест с WAL-only записью восстанавливает её из checkpoint и краснеет при простой копии main DB
  traces: [J-01, J-02]
- **FR-04** Включать в continuation checkpoint Git-материал и незавершённую работу, без которых следующий шаг нельзя воспроизвести
  **Priority**: Must
  **Acceptance**: После удаления исходного каталога восстанавливаются опубликованные commits/refs, local-only commits, dirty или untracked work и runner-created rescue stash; lock, stop-файлы и временные worktrees не восстанавливаются
  traces: [J-01, J-02]
- **FR-05** Восстанавливать прогон по run_id в новом рабочем каталоге и вычислять следующий безопасный шаг
  **Priority**: Must
  **Acceptance**: Восстановление в другом абсолютном пути возвращает active claims, budget authority, effective TDD namespace и WIP; несовпадение repository, config или namespace отказывает до paid call и claims gate
  traces: [J-01, J-02, G-03]
- **FR-06** Публиковать immutable evidence для каждого платного вызова и terminal task attempt независимо от исхода
  **Priority**: Must
  **Acceptance**: Task execution, review, plan --full и gated planning создают адресуемые call records; success, failed, blocked, timeout и infrastructure error содержат identity, стоимость, bounded/redacted prompt и result либо их digests
  traces: [J-03, G-02]
- **FR-07** Создавать отдельную immutable run-closure запись для каждого штатного завершения или ранней остановки
  **Priority**: Must
  **Acceptance**: completed, no-ready, validation failure, budget или policy refusal, session timeout, infrastructure error и operator stop создают closure с причиной и ids последних checkpoint/evidence; run-start без closure трактуется как crash/unknown
  traces: [J-01, J-03]
- **FR-08** Сохранять continuation-relevant mutation в аварийный durable spool при отказе основной state DB
  **Priority**: Must
  **Acceptance**: Ошибка SQLite при записи attempt сохраняет payload и ordering/join keys в spool и восстанавливается новым процессом; одновременный отказ DB и spool останавливает выполнение до следующего платного вызова
  traces: [J-01, J-03]
- **FR-09** Давать оператору и аудитору read-surface по run_id без необходимости восстанавливать весь рабочий каталог
  **Priority**: Should
  **Acceptance**: По run_id можно получить последний checkpoint, closure, открытые calls, список attempts и evidence-ссылки с исходами и стоимостью; данные читаются с другой машины без доступа к исходному клону
  traces: [J-02, J-03]

## Non-Functional

- **NFR-01** Не терять ни одной подтверждённой continuation-relevant mutation
  **Target**: RPO 0 для записей, подтверждённых DB, spool или artifact store; 1000 fault-injection падений в каждой write-ahead границе дают 0 потерянных подтверждённых mutations и 0 silent retries
  traces: [FR-02, FR-03, FR-08]
- **NFR-02** Не добавлять существенную задержку перед платными вызовами и между шагами
  **Target**: Durable call-start acknowledgement p95 <= 1 s и p99 <= 3 s; новый checkpoint для reference workload доступен вне машины p95 <= 60 s при здоровом хранилище
  traces: [FR-02, FR-03]
- **NFR-03** Восстанавливать прогон достаточно быстро для штатного операторского handoff
  **Target**: Полное восстановление набора до 1 GiB в новом каталоге занимает <= 15 min при сети >= 100 Mbit/s; после загрузки валидация и вычисление следующего безопасного шага занимают <= 60 s
  traces: [FR-04, FR-05]
- **NFR-04** Обнаруживать повреждение, неполноту и подмену любого опубликованного артефакта
  **Target**: 100% файлов manifest, checkpoint, WIP и evidence имеют SHA-256; изменение любого байта или отсутствие обязательного файла даёт fail-closed до restore, paid call или claims gate
  traces: [FR-03, FR-04, FR-05, FR-06, FR-07]
- **NFR-05** Защищать приватный source, prompts и provider output
  **Target**: Шифрование TLS 1.2+ при передаче и AES-256 либо эквивалентное managed encryption в покое; доступ только явно назначенным ролям; тестовый корпус из 100 секретов даёт 0 известных секретов в опубликованных bounded logs
  traces: [FR-06, FR-09]
- **NFR-06** Ограничивать объём публикуемых prompt и result logs без потери доказательства исходных байтов
  **Target**: Не более 1 MiB redacted prompt и 4 MiB redacted result на call; при усечении сохраняются SHA-256 полного исходного содержимого, исходный размер и явный marker truncated
  traces: [FR-06]
- **NFR-07** Иметь предсказуемую retention и своевременное удаление приватных артефактов
  **Target**: По умолчанию checkpoints хранятся до closure плюс 30 дней, evidence и closure — 180 дней; политика настраивается в диапазоне 7–365 дней, санкционированное удаление завершается в течение 24 h и оставляет audit-запись без удалённого содержимого
  traces: [FR-03, FR-06, FR-07]

## Constraints

- **CON-01** До выполнения и приёмки всего набора FR-01–FR-08 функция считается экспериментальной; частичный срез нельзя обозначать как безопасное продолжение прогона.

- **CON-02** Реализация должна встраиваться в существующий контур spec-runner: SQLite с WAL, tasks.md, Git-состояние workstream-а, текущие subprocess-вызовы провайдеров, claims, TDD namespace, waivers и бюджетные санкции.

- **CON-03** Нельзя менять контракт платного провайдера или рассчитывать на его идемпотентность. Если подтверждение результата вызова потеряно, такой вызов остаётся open/unknown и требует решения человека.

- **CON-04** Checkpoint, evidence, prompts, provider output, SQLite и WIP нельзя автоматически помещать в продуктовый Git. Для них допустимо только приватное, явно авторизованное хранилище с контролем доступа, редактированием секретов и retention-политикой.

- **CON-05** Первая версия не должна требовать отдельного always-on control-plane или дежурной команды. Публикация, восстановление и аудит должны выполняться существующим оператором через конечные команды с однозначным результатом.

- **CON-06** Формат checkpoint и evidence обязан быть версионированным и переносимым между абсолютными путями и машинами; локальные пути, временные worktree, lock-файлы и process identity не могут быть частью переносимого контракта.

- **CON-07** Существующие незавершённые прогоны без нового evidence-контракта нельзя автоматически объявлять восстановимыми. Для них допустим только явный legacy-режим или fail-closed с объяснением недостающих гарантий.

- **CON-08** Любые новые расходы на внешний artifact store требуют отдельного одобрения владельца. Стоимость хранения и передачи должна быть атрибутируема по run_id и доступна для аудита вместе со стоимостью платных вызовов.


## Success Metrics

- **M-01** В матрице не менее чем из 20 ежемесячных restore-drill после полного удаления исходного каталога 100% прогонов, остановленных на закрытой durable-границе, восстанавливаются в другом пути и продолжаются с точного следующего шага; потерянных подтверждённых mutations и повторных платных вызовов — 0.

  traces: [G-01, G-03]
- **M-02** В 100% restore-drill с намеренно оставленным open call восстановление выдаёт unknown/needs-human до любого повтора вызова; автоматических или скрытых повторов — 0.

  traces: [G-01, G-03]
- **M-03** Для 100% платных вызовов в новых прогонах по run_id находится ровно один durable call-start и однозначный исход: closure либо явно открытое unknown-состояние; вызовов без классификации — 0.

  traces: [G-02]
- **M-04** Для 100% штатно завершившихся, failed, blocked, refused и early-stop прогонов опубликована неизменяемая closure; отсутствие closure всегда обнаруживается и классифицируется как crash/unknown.

  traces: [G-02]
- **M-05** Не менее трёх реальных активных прогонов переданы в другой checkout или на другую машину и продолжены без ручного восстановления claims, checkpoints, verify-evidence, waivers, budget authority или Git/WIP.

  traces: [G-01, G-03]
- **M-06** В ежемесячной независимой выборке не менее десяти прогонов аудитор без доступа к исходному рабочему каталогу в 100% случаев определяет результат, стоимость, использованные санкции и следующий безопасный шаг либо причину, почему он не доказуем.

  traces: [G-02, G-03]
- **M-07** За три месяца зафиксировано 0 случаев публикации checkpoint или evidence в продуктовый Git, 0 известных утечек секретов и 0 восстановлений, продолжившихся после несовпадения identity, digest или authority.

  traces: [G-01, G-02, G-03]

## Out of Scope

- **OUT-01** Не строить новый scheduler, распределённый оркестратор, always-on control-plane, очередь работников или механизм автоматического failover между машинами.

- **OUT-02** Не обещать exactly-once исполнение со стороны платного провайдера, не отменять и не дедуплицировать уже отправленные ему запросы. Spec-runner фиксирует durable call-start и останавливает open call как unknown; решение о повторе принимает человек.

- **OUT-03** Не реализовывать собственное объектное хранилище, IAM, шифрование ключей или retention-service. Эти функции предоставляет выбранная инфраструктура; spec-runner отвечает за корректную интеграцию, manifest, hashes, доступ и удаление по её публичному контракту.

- **OUT-04** Не заменять Git и не становиться универсальным backup-инструментом репозитория или машины. Git продолжает хранить продуктовую историю, а checkpoint захватывает только материал и состояние, необходимые для продолжения конкретного run_id.

- **OUT-05** Не сохранять полные образы ОС, virtualenv, tool caches, временные worktrees, locks, процессы или произвольные файлы вне границы прогона. Версии и identity зависимостей можно зафиксировать для проверки, но их установка остаётся обязанностью существующего bootstrap/tooling.

- **OUT-06** Не придумывать заново правила claims, TDD namespace, waiver, remedy, budget approval или review. Их определяют существующие контракты spec-runner и governance; checkpoint только сохраняет и при restore пересверяет их эффективное состояние.

- **OUT-07** Не выполнять автоматическую миграцию старых прогонов без нового write-ahead и evidence-контракта и не объявлять их восстановимыми задним числом. Для legacy допустимы диагностика и ручной путь, но не выдуманная гарантия.

- **OUT-08** Не строить в этом workstream межпроектные dashboards, тренды стоимости, capacity planning, billing или аналитику портфеля. В scope остаётся адресуемое по одному run_id чтение состояния, evidence и стоимости, предусмотренное FR-09.

- **OUT-09** Не угадывать и не чинить автоматически повреждённый checkpoint, отсутствующий evidence, несовпадающую identity или неизвестный следующий шаг. Корректный результат таких случаев — stopped/needs-human с точной причиной.

- **OUT-10** Не гарантировать бессрочное хранение и не обходить утверждённые правила удаления, доступа или legal hold. Жизненный цикл артефактов ограничен принятой retention-политикой и её аудитируемыми процедурами.
```

Генератор `tools/gen_discovery_approval_fixture.py` исполняет `discovery.cli.main(["approve", …])`
целиком; подменена только точка композиции форджа `cli.build_forge` — так же, как в
тестах самого discovery (`tests/test_approve_cli.py`); факты мержа и политика заданы
в скрипте; HEAD discovery обязан совпасть с полным SHA из `PINNED.txt`:

```python
"""Dev-only: подписать фикстуру T40 настоящей командой `discovery approve`.

Запуск — из чекаута discovery на пиненом коммите (`PINNED.txt` вендоренной копии):

    cd ../discovery
    uv run --frozen python -I <devtools>/tools/gen_discovery_approval_fixture.py \\
        <devtools>/tests/fixtures/discovery_approval <pinned-sha>

`approve` исполняется целиком (`discovery.cli.main`), подменена только точка
композиции форджа `cli.build_forge` — так же, как в тестах самого discovery:
факты мержа и политика заданы ниже явно. Черновик берётся из
`<fixtures>/draft-brief.md` (вендорен в devtools вместе с фикстурой).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from discovery.forge import PullRequest

from discovery import approval, cli, policy

TARGET_REPO = "owner/alpha"
BRIEF_PATH = "workstreams/WS-1/spec/00-discovery/brief.md"
HUMAN = "andrei-shtanakov"
MERGED_AT = "2026-10-08T10:00:00Z"
MERGE_SHA = "d1" * 20
POLICY_SHA = "a1" * 20
POLICY_REPO = "andrei-shtanakov/approval-policy"


@dataclass
class _Forge:
    """Факты форджа для одного акта; `calls` — что спросил `approve`."""

    draft: str
    calls: list[tuple] = field(default_factory=list)

    def pull_request(self, repo: str, number: int) -> PullRequest:
        self.calls.append(("pull_request", repo, number))
        return PullRequest("MERGED", HUMAN, MERGED_AT, MERGE_SHA)

    def pull_request_files(self, repo: str, number: int) -> list[str]:
        self.calls.append(("pull_request_files", repo, number))
        return [BRIEF_PATH]

    def file_at(self, repo: str, commit: str, path: str) -> str | None:
        self.calls.append(("file_at", repo, commit, path))
        if repo == POLICY_REPO:
            return f"{policy.ALLOWLIST_KEY}={HUMAN}\n"
        return self.draft

    def latest_commit_touching(self, repo: str, ref: str, path: str) -> str | None:
        self.calls.append(("latest_commit_touching", repo, ref, path))
        return POLICY_SHA


def main() -> int:
    fixtures, pinned = Path(sys.argv[1]), sys.argv[2]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != pinned:
        print(f"discovery HEAD {head} ≠ пина {pinned}", file=sys.stderr)
        return 2
    os.environ.pop(policy.ALLOWLIST_KEY, None)
    draft = (fixtures / "draft-brief.md").read_text(encoding="utf-8")
    forge = _Forge(draft)
    cli.build_forge = lambda: forge  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as tmp:
        brief = Path(tmp) / BRIEF_PATH
        brief.parent.mkdir(parents=True)
        brief.write_text(draft, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(
                [
                    "approve",
                    str(brief),
                    "--repo",
                    TARGET_REPO,
                    "--pr",
                    "7",
                    "--path",
                    BRIEF_PATH,
                ]
            )
        if code != 0:
            print(f"approve вернул {code}: {out.getvalue()}", file=sys.stderr)
            return 2
        signed = brief.read_text(encoding="utf-8")
    (fixtures / "signed-brief.md").write_text(signed, encoding="utf-8")
    meta = {
        "discovery_commit": pinned,
        "approve_exit": code,
        "self_hash": approval.self_hash(draft),
        "verify_signed": approval.verify(signed),
        "verify_draft": approval.verify(draft),
        "merge_event": {"login": HUMAN, "merged_at": MERGED_AT, "commit": MERGE_SHA},
        "forge_calls": [list(c) for c in forge.calls],
    }
    (fixtures / "signed-brief.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Генератор запускается из ОДНОРАЗОВОГО клона discovery на пиненом SHA — соседний
чекаут не меняется (ни `checkout`, ни `worktree`):

```bash
PIN=94c88cc55ae29aa005855bbebf6f4ae6244a6f3b
TMP=$(mktemp -d)
git clone -q --no-checkout ../discovery "$TMP/discovery"
git -C "$TMP/discovery" checkout -q "$PIN"
(cd "$TMP/discovery" && uv run --frozen python -I \
   "$OLDPWD/tools/gen_discovery_approval_fixture.py" "$OLDPWD/tests/fixtures/discovery_approval" "$PIN")
rm -rf "$TMP"
```

Ожидаемый `signed-brief.json` (проверено: `approve` вернул 0; подписанные байты
совпали с `approval.stamp` того же события):

```json
{
  "discovery_commit": "94c88cc55ae29aa005855bbebf6f4ae6244a6f3b",
  "approve_exit": 0,
  "self_hash": "sha256:b7bd55ff95fe7dfe79faf1d2ea4501b4f2df4299034f7f428ca4b333ad271571",
  "verify_signed": null,
  "verify_draft": "debt_status",
  "merge_event": {
    "login": "andrei-shtanakov",
    "merged_at": "2026-10-08T10:00:00Z",
    "commit": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"
  },
  "forge_calls": [
    [
      "pull_request",
      "owner/alpha",
      7
    ],
    [
      "latest_commit_touching",
      "andrei-shtanakov/approval-policy",
      "main",
      "policy/approvers.env"
    ],
    [
      "file_at",
      "andrei-shtanakov/approval-policy",
      "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
      "policy/approvers.env"
    ],
    [
      "pull_request_files",
      "owner/alpha",
      7
    ],
    [
      "file_at",
      "owner/alpha",
      "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1",
      "workstreams/WS-1/spec/00-discovery/brief.md"
    ]
  ]
}
```

- [ ] **Step 3: Тесты**

```python
"""Вендоренный self-hash discovery (спека need-stage §11.4.2, T40)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from governance import discovery_approval as da

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_discovery_approval_vendor as vendor  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "discovery_approval"


def _load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_self_hash_matches_producer_on_signed_fixture() -> None:
    expected = json.loads(_load("signed-brief.json"))
    assert da.self_hash(_load("signed-brief.md")) == expected["self_hash"]
    assert da.self_hash(_load("draft-brief.md")) == expected["self_hash"]


def test_crlf_variant_has_the_same_hash() -> None:
    signed = _load("signed-brief.md")
    assert da.self_hash(signed.replace("\n", "\r\n")) == da.self_hash(signed)


def test_verify_agrees_with_producer() -> None:
    expected = json.loads(_load("signed-brief.json"))
    assert da.verify(_load("signed-brief.md")) == expected["verify_signed"]
    assert da.verify(_load("draft-brief.md")) == expected["verify_draft"]


def test_not_a_brief_is_raised() -> None:
    with pytest.raises(da.NotABrief):
        da.self_hash("no frontmatter")


def test_loader_refuses_unknown_discovery_import(tmp_path: Path) -> None:
    src = (da.VENDOR / "approval.py").read_bytes()
    bad = tmp_path / "approval.py"
    bad.write_bytes(src + b"\nfrom discovery.render import render_brief\n")
    import hashlib

    with pytest.raises(da.VendorError, match="import"):
        da.load_module(bad, hashlib.sha256(bad.read_bytes()).hexdigest())


def test_loader_refuses_digest_mismatch(tmp_path: Path) -> None:
    bad = tmp_path / "approval.py"
    bad.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(da.VendorError, match="sha256"):
        da.load_module(bad, "0" * 64)


def test_vendor_consistency_ok() -> None:
    assert vendor.verify("consistency").status == "ok"


def test_vendor_consistency_detects_dropped_pin(tmp_path: Path, monkeypatch) -> None:
    for name in ("approval.py", "hashing.py", "gate_check.py"):
        (tmp_path / name).write_bytes((vendor.CONTRACT / name).read_bytes())
    pinned = (vendor.CONTRACT / "PINNED.txt").read_text(encoding="utf-8")
    kept = [ln for ln in pinned.splitlines() if not ln.startswith("hashing.py")]
    (tmp_path / "PINNED.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
    monkeypatch.setattr(vendor, "CONTRACT", tmp_path)
    verdict = vendor.verify("consistency")
    assert verdict.status == "failed" and "hashing.py" in verdict.detail


def test_vendor_provenance_matching_copy_passes() -> None:
    def fetch(_commit: str, rel: str) -> bytes:
        return (vendor.CONTRACT / rel).read_bytes()

    assert vendor.verify("provenance", fetch=fetch).status == "ok"


def test_vendor_provenance_unreachable_is_unknown() -> None:
    assert vendor.verify("provenance", fetch=lambda *_: None).status == "unknown"


def test_vendor_provenance_mismatch_fails() -> None:
    assert vendor.verify("provenance", fetch=lambda *_: b"x").status == "failed"


def test_vendor_drift_equal_moved_unknown() -> None:
    commit, _ = vendor.read_pinned()
    assert vendor.drift(fetch=lambda *_: commit.encode()).status == "ok"
    assert vendor.drift(fetch=lambda *_: b"b" * 40).status == "failed"
    assert vendor.drift(fetch=lambda *_: None).status == "unknown"
```

- [ ] **Step 4: Run — FAIL** — `uv run --frozen pytest tests/test_discovery_approval.py -q`:
`ModuleNotFoundError: governance.discovery_approval`.

- [ ] **Step 5: Загрузчик**

```python
"""Self-hash discovery-брифа — пиненая копия кода соседа (спека §11.4.2).

Копии в `contracts/discovery-approval/v1/` байт в байт равны файлам discovery
на коммите из `PINNED.txt`. Пакета `discovery` у devtools нет и не будет,
поэтому копии не импортируются как пакет: загрузчик сверяет sha256 каждой
копии с пином, подменяет РОВНО три известные строки импорта на модули-копии
и исполняет исходник в изолированном модуле. Любая иная строка
`from discovery…`/`import discovery…` — отказ: новый импорт у соседа значит,
что копия больше не замкнута, и считать хэш молча нельзя.
"""

from __future__ import annotations

import hashlib
import re
import sys
import types
from pathlib import Path
from typing import Any

VENDOR = (
    Path(__file__).resolve().parent.parent / "contracts" / "discovery-approval" / "v1"
)
#: Значение `discovery.render.GENERATED_BY`; `render.py` не вендорится (тянет
#: весь рендер), расхождение ловит тест на фикстуре, подписанной соседом.
GENERATED_BY = "discovery-runtime"
_IMPORTS = {
    "from discovery.contract.gate_check import split_frontmatter": "gate_check",
    "from discovery.hashing import canonical_answer_bytes": "hashing",
    "from discovery.render import GENERATED_BY": "render",
}
_DISCOVERY_IMPORT = re.compile(r"^\s*(from|import)\s+discovery\b", re.MULTILINE)


class VendorError(RuntimeError):
    """Копия не совпала с пином либо перестала быть замкнутой."""


def _alias(name: str) -> str:
    return f"governance._discovery_vendor_{name}"


def pins() -> dict[str, str]:
    """Пин sha256 по имени файла копии (из `PINNED.txt`)."""
    found: dict[str, str] = {}
    for line in (VENDOR / "PINNED.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].endswith(".py"):
            found[parts[0]] = parts[1]
    return found


def load_module(path: Path, expected_digest: str) -> types.ModuleType:
    """Исполнить копию `path` после сверки sha256 и подмены известных импортов."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_digest:
        raise VendorError(f"{path.name}: sha256 {digest} ≠ пина {expected_digest}")
    source = data.decode("utf-8")
    for line, name in _IMPORTS.items():
        imported = line.rsplit(" ", 1)[-1]
        source = source.replace(line, f"from {_alias(name)} import {imported}")
    if _DISCOVERY_IMPORT.search(source):
        raise VendorError(
            f"{path.name}: неизвестный import discovery — копия не замкнута"
        )
    module = types.ModuleType(_alias(path.stem))
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _bootstrap() -> Any:
    expected = pins()
    for name in ("approval.py", "hashing.py", "gate_check.py"):
        if name not in expected:
            raise VendorError(f"PINNED.txt не пинует {name}")
    render = types.ModuleType(_alias("render"))
    setattr(render, "GENERATED_BY", GENERATED_BY)
    sys.modules[render.__name__] = render
    load_module(VENDOR / "hashing.py", expected["hashing.py"])
    load_module(VENDOR / "gate_check.py", expected["gate_check.py"])
    return load_module(VENDOR / "approval.py", expected["approval.py"])


_approval: Any = _bootstrap()
NotABrief: type[Exception] = _approval.NotABrief
SELF_HASH_KEY: str = _approval.SELF_HASH_KEY
DEBT_STATUS: str = _approval.DEBT_STATUS
DEBT_UNSIGNED: str = _approval.DEBT_UNSIGNED
DEBT_MIGRATION: str = _approval.DEBT_MIGRATION
DEBT_SELF_HASH: str = _approval.DEBT_SELF_HASH


def self_hash(text: str) -> str:
    """Self-hash соседа (`approval.self_hash`) над `text`; `NotABrief` без frontmatter."""
    return str(_approval.self_hash(text))


def verify(text: str) -> str | None:
    """Почему `text` не честно одобрен по правилу соседа; `None` — одобрен."""
    result = _approval.verify(text)
    return None if result is None else str(result)
```

- [ ] **Step 6: Проверяльщик копии**

```python
"""Verify the vendored discovery approval copy (`contracts/discovery-approval/v1`).

consistency — files match the digests recorded in PINNED.txt (NOT their origin).
provenance  — files are the bytes of upstream discovery at the pinned commit;
              unreachable upstream is unknown, never ok.
drift       — has upstream discovery moved past the pin?

Exit: 0 ok · 1 failed · 3 unknown. Same contract as check_discovery_vendor.py;
the network layer is reused from it, only the surface and the paths differ.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_discovery_vendor as base  # noqa: E402

REPO = "andrei-shtanakov/discovery"
#: Manifest key → path inside upstream discovery.
UPSTREAM_PATH = {
    "approval.py": "src/discovery/approval.py",
    "hashing.py": "src/discovery/hashing.py",
    "gate_check.py": "src/discovery/contract/gate_check.py",
}
EXPECTED_SURFACE = frozenset(UPSTREAM_PATH)


def resolve_dest() -> Path:
    """Configured destination (DISCOVERY_APPROVAL_VENDOR_DEST) or devtools' copy."""
    env_dest = os.environ.get("DISCOVERY_APPROVAL_VENDOR_DEST")
    if env_dest:
        return Path(env_dest)
    root = Path(__file__).resolve().parent.parent
    return root / "contracts" / "discovery-approval" / "v1"


CONTRACT = resolve_dest()


def read_pinned() -> tuple[str, dict[str, str]]:
    """Commit and per-file digests from PINNED.txt."""
    commit, manifest = "", {}
    for line in (CONTRACT / "PINNED.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("commit:"):
            commit = line.split(":", 1)[1].strip()
        elif line and not line.startswith("upstream:"):
            rel, digest = line.rsplit(" ", 1)
            manifest[rel] = digest
    return commit, manifest


def github_fetch(commit: str, rel: str) -> bytes | None:
    """Upstream blob of manifest key `rel` at `commit`."""
    saved = base.CONTENTS_API
    base.CONTENTS_API = (
        f"https://api.github.com/repos/{REPO}/contents/{{rel}}?ref={{commit}}"
    )
    try:
        return base.github_fetch(commit, UPSTREAM_PATH[rel])
    finally:
        base.CONTENTS_API = saved


def github_head_fetch(commit: str, rel: str) -> bytes | None:
    """Upstream discovery default-branch HEAD sha."""
    saved = (base.REPO_API, base.COMMITS_API)
    base.REPO_API = f"https://api.github.com/repos/{REPO}"
    base.COMMITS_API = f"https://api.github.com/repos/{REPO}/commits/{{ref}}"
    try:
        return base.github_head_fetch(commit, rel)
    finally:
        base.REPO_API, base.COMMITS_API = saved


def verify(
    mode: Literal["consistency", "provenance"], fetch: base.Fetcher | None = None
) -> base.Verdict:
    """Consistency against PINNED.txt or provenance against upstream."""
    commit, manifest = read_pinned()
    missing = EXPECTED_SURFACE - set(manifest)
    if missing:
        return base.Verdict(
            "failed", "PINNED.txt does not cover: " + ", ".join(sorted(missing))
        )
    if mode == "consistency":
        drifted = [
            rel
            for rel, digest in manifest.items()
            if hashlib.sha256((CONTRACT / rel).read_bytes()).hexdigest() != digest
        ]
        if drifted:
            return base.Verdict("failed", f"files differ from PINNED.txt: {drifted}")
        return base.Verdict("ok", f"{len(manifest)} files match their digests")
    fetch = fetch or github_fetch
    mismatched: list[str] = []
    for rel in manifest:
        blob = fetch(commit, rel)
        if blob is None:
            return base.Verdict("unknown", f"upstream unreachable while reading {rel}")
        if blob != (CONTRACT / rel).read_bytes():
            mismatched.append(rel)
    if mismatched:
        return base.Verdict(
            "failed", f"differ from upstream@{commit[:8]}: {mismatched}"
        )
    return base.Verdict("ok", f"bytes identical to upstream@{commit[:8]}")


def drift(fetch: base.Fetcher | None = None) -> base.Verdict:
    """Has upstream discovery moved past the pin?"""
    commit, _ = read_pinned()
    head = (fetch or github_head_fetch)("HEAD", "HEAD")
    if head is None:
        return base.Verdict("unknown", "upstream HEAD unreachable")
    head_sha = head.decode("utf-8").strip()
    if head_sha == commit:
        return base.Verdict("ok", f"pin matches upstream HEAD {head_sha[:8]}")
    return base.Verdict("failed", f"pin {commit[:8]} is behind upstream {head_sha[:8]}")


def main() -> int:
    """CLI: `check_discovery_approval_vendor.py {consistency|provenance|drift}`."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["consistency", "provenance", "drift"])
    args = parser.parse_args()
    try:
        verdict = drift() if args.mode == "drift" else verify(args.mode)
    except OSError as exc:
        verdict = base.Verdict("failed", f"cannot read vendored copy: {exc}")
    print(f"{verdict.status}: {verdict.detail}")
    return {"ok": 0, "failed": 1}.get(verdict.status, 3)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run — PASS** — `uv run --frozen pytest tests/test_discovery_approval.py tests/test_discovery_vendor.py -q` → 20 passed (проверено).

- [ ] **Step 8: Commit** — `feat(need): вендоренный self-hash discovery (§11.4.2)`.

---

### Task 2: Строгая заявка на одобрение

**Files:**
- Create: `governance/approval_request.py`, `tests/approval_request_cases.py`
- Test: `tests/test_approval_request.py`

**Interfaces:**
- Produces: `ApprovalRequest` (frozen: `brief_self_hash`, `policy_repo`, `policy_ref`,
  `policy_path`, `policy_sha`, `run_id`, `ws_id`); `render(req) -> str`;
  `parse(text) -> ApprovalRequest` (`RequestError`); `FILE_NAME`, `SCHEMA`, `PURPOSE`,
  `BRIEF`; CLI `python -m governance.approval_request check <file>` (0 / 3).
- Produces (тесты): `tests/approval_request_cases.py` — `GOOD`, `DEFECTS` (id, порча,
  фрагмент причины), `POLICY_SHA`. Тот же набор гоняют все потребители заявки
  (T21b): Task 4 (`read_act`), часть B — `brief-approve` и `check-merge`.

- [ ] **Step 1: Общий набор случаев**

```python
"""Общий набор случаев заявки (T21a/T21b): каждый потребитель гоняет ВСЕ.

`GOOD` — корректная заявка; `DEFECTS` — (id, функция порчи текста, фрагмент
причины). Двойник каждой порчи — сам `GOOD`: он обязан проходить там же.
"""

from __future__ import annotations

from collections.abc import Callable

from governance import approval_request as ar

POLICY_SHA = "a1" * 20

GOOD = ar.ApprovalRequest(
    brief_self_hash="sha256:" + "b" * 64,
    policy_repo="andrei-shtanakov/approval-policy",
    policy_ref="main",
    policy_path="policy/approvers.env",
    policy_sha=POLICY_SHA,
    run_id="WS-1-abc123",
    ws_id="WS-1",
)

Mutation = Callable[[str], str]

DEFECTS: list[tuple[str, Mutation, str]] = [
    ("dup-top", lambda t: t + "purpose: discovery-brief-approval\n", "дубл"),
    ("dup-policy", lambda t: t.replace("  sha:", "  ref: main\n  sha:"), "дубл"),
    ("second-doc", lambda t: t + "---\nschema: x\n", "документ"),
    ("extra-key", lambda t: t + "extra: '1'\n", "ключ"),
    ("missing-key", lambda t: t.replace('run_id: "WS-1-abc123"\n', ""), "ключ"),
    ("short-sha", lambda t: t.replace(POLICY_SHA, POLICY_SHA[:39]), "40 hex"),
    ("crlf", lambda t: t.replace("\n", "\r\n"), "CR"),
    (
        "purpose",
        lambda t: t.replace("purpose: discovery-brief-approval", "purpose: x"),
        "purpose",
    ),
    ("brief", lambda t: t.replace("brief: brief.md", "brief: x.md"), "brief"),
    (
        "schema",
        lambda t: t.replace("discovery-brief-approval-request/v1", "v0"),
        "schema",
    ),
    (
        "list-value",
        lambda t: t.replace('run_id: "WS-1-abc123"', "run_id: [1]"),
        "строк",
    ),
    ("non-str-key", lambda t: t + "? [a, b]\n: x\n", "скаляр"),
    ("int-key", lambda t: t + "1: x\n", "строка"),
    ("not-yaml", lambda t: t + "policy: [\n", "YAML"),
]
```

- [ ] **Step 2: Тесты**

```python
"""Заявка на одобрение discovery-брифа: один строгий разбор (§11.3 п.3a)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from governance import approval_request as ar
from tests.approval_request_cases import DEFECTS, GOOD


def test_render_parse_roundtrip_is_exact() -> None:
    text = ar.render(GOOD)
    assert ar.parse(text) == GOOD
    assert ar.render(ar.parse(text)) == text


def test_render_key_order_is_fixed() -> None:
    keys = [
        ln.split(":")[0]
        for ln in ar.render(GOOD).splitlines()
        if not ln.startswith(" ")
    ]
    assert keys == [
        "schema",
        "purpose",
        "brief",
        "brief_self_hash",
        "policy",
        "run_id",
        "ws_id",
    ]


@pytest.mark.parametrize(
    ("case", "mutate", "match"), DEFECTS, ids=[d[0] for d in DEFECTS]
)
def test_each_defect_refuses_twin_passes(case, mutate, match) -> None:
    good = ar.render(GOOD)
    assert ar.parse(good) == GOOD
    with pytest.raises(ar.RequestError, match=match):
        ar.parse(mutate(good))


def test_cli_check_codes(tmp_path) -> None:
    good = tmp_path / "ok.yaml"
    good.write_text(ar.render(GOOD), encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text(ar.render(GOOD) + "1: x\n", encoding="utf-8")
    run = [sys.executable, "-m", "governance.approval_request", "check"]
    assert subprocess.run([*run, str(good)], check=False).returncode == 0
    done = subprocess.run([*run, str(bad)], check=False, capture_output=True, text=True)
    assert done.returncode == 3 and "отклонена" in done.stderr


@pytest.mark.parametrize(
    "value",
    ["123", "yes", "null", "true", "1e3", "~", "a: b", "it's", 'say "hi"', "кириллица"],
)
def test_yaml_like_ids_roundtrip_as_strings(value) -> None:  # ревью части A, A5
    from dataclasses import replace

    req = replace(GOOD, run_id=value, ws_id=value)
    assert ar.parse(ar.render(req)) == req


@pytest.mark.parametrize("newline", ["\r\n", "\r"], ids=["crlf", "cr"])
def test_cli_refuses_cr_at_file_boundary(tmp_path, newline) -> None:  # A6
    path = tmp_path / "crlf.yaml"
    path.write_bytes(ar.render(GOOD).replace("\n", newline).encode("utf-8"))
    run = [sys.executable, "-m", "governance.approval_request", "check", str(path)]
    done = subprocess.run(run, check=False, capture_output=True, text=True)
    assert done.returncode == 3 and "CR" in done.stderr
```

- [ ] **Step 3: Run — FAIL** (нет модуля).

- [ ] **Step 4: Реализация**

```python
"""Заявка на одобрение discovery-брифа (спека need-stage §11.3 п.3–3a).

Один нормативный разбор для всех потребителей: brief-propose, human-merge.sh
(через `brief_tools check-merge`), brief-approve и engineer-preflight.
Неоднозначная заявка не доказывает назначения акта, поэтому разбор строже
YAML: ровно один документ, только строковые ключи, дубли ключей запрещены на
всех уровнях (`safe_load` молча берёт последний), набор ключей — ровно схема v1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

FILE_NAME = "approval-request.yaml"
SCHEMA = "discovery-brief-approval-request/v1"
PURPOSE = "discovery-brief-approval"
BRIEF = "brief.md"
_TOP = ("schema", "purpose", "brief", "brief_self_hash", "policy", "run_id", "ws_id")
_POLICY = ("repo", "ref", "path", "sha")
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


class RequestError(ValueError):
    """Заявка некорректна или неоднозначна."""


@dataclass(frozen=True)
class ApprovalRequest:
    """Разобранная заявка; `policy_*` — координаты и пин политики подписи."""

    brief_self_hash: str
    policy_repo: str
    policy_ref: str
    policy_path: str
    policy_sha: str
    run_id: str
    ws_id: str


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader: в любом mapping ключ — строка и встречается один раз."""


def _strict_mapping(loader: _StrictLoader, node: yaml.MappingNode) -> dict:
    seen: set[str] = set()
    for key_node, _ in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            raise RequestError("ключ — не скаляр")
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise RequestError(f"ключ {key!r} — не строка")
        if key in seen:
            raise RequestError(f"дублирующийся ключ {key!r}")
        seen.add(key)
    return loader.construct_mapping(node)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_mapping,  # type: ignore[arg-type]
)


def _q(value: str) -> str:
    """Строка как YAML-скаляр в двойных кавычках (JSON-строка — валидный YAML):
    `"123"`, `"yes"`, `"null"` остаются строками, а не числом/bool/None."""
    return json.dumps(value, ensure_ascii=False)


def render(req: ApprovalRequest) -> str:
    """Детерминированные байты заявки (фиксированный порядок ключей)."""
    return (
        f"schema: {SCHEMA}\n"
        f"purpose: {PURPOSE}\n"
        f"brief: {BRIEF}\n"
        f"brief_self_hash: {_q(req.brief_self_hash)}\n"
        "policy:\n"
        f"  repo: {_q(req.policy_repo)}\n"
        f"  ref: {_q(req.policy_ref)}\n"
        f"  path: {_q(req.policy_path)}\n"
        f"  sha: {_q(req.policy_sha)}\n"
        f"run_id: {_q(req.run_id)}\n"
        f"ws_id: {_q(req.ws_id)}\n"
    )


def _string(mapping: dict, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RequestError(f"{key}: ожидалась непустая строка, получено {value!r}")
    return value


def _exact_keys(mapping: object, keys: tuple[str, ...], where: str) -> dict:
    if not isinstance(mapping, dict):
        raise RequestError(f"{where}: ожидался mapping")
    if set(mapping) != set(keys):
        raise RequestError(f"{where}: ключи {sorted(mapping)} ≠ схеме {sorted(keys)}")
    return mapping


def parse(text: str) -> ApprovalRequest:
    """Строгий разбор; `RequestError` на любой некорректности или неоднозначности."""
    if "\r" in text:
        raise RequestError("CR в заявке: допускается только LF")
    try:
        docs = list(yaml.load_all(text, Loader=_StrictLoader))  # noqa: S506
    except yaml.YAMLError as exc:
        raise RequestError(f"YAML не разбирается: {exc}") from exc
    if len(docs) != 1:
        raise RequestError(f"ожидался ровно один YAML-документ, получено {len(docs)}")
    top = _exact_keys(docs[0], _TOP, "заявка")
    policy = _exact_keys(top["policy"], _POLICY, "policy")
    for key, expected in (("schema", SCHEMA), ("purpose", PURPOSE), ("brief", BRIEF)):
        if _string(top, key) != expected:
            raise RequestError(f"{key} {top[key]!r} ≠ {expected!r}")
    sha = _string(policy, "sha")
    if not _SHA.match(sha):
        raise RequestError(f"policy.sha {sha!r}: нужно 40 hex")
    return ApprovalRequest(
        brief_self_hash=_string(top, "brief_self_hash"),
        policy_repo=_string(policy, "repo"),
        policy_ref=_string(policy, "ref"),
        policy_path=_string(policy, "path"),
        policy_sha=sha,
        run_id=_string(top, "run_id"),
        ws_id=_string(top, "ws_id"),
    )


def main(argv: list[str] | None = None) -> int:
    """`check <file>`: 0 — заявка корректна, 3 — отказ (причина в stderr)."""
    parser = argparse.ArgumentParser(prog="approval_request")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("file")
    args = parser.parse_args(argv)
    try:
        # Байты, не `read_text`: тот нормализует CRLF/CR до `parse`, и запрет
        # CR обходился бы именно на файловой границе.
        parse(Path(args.file).read_bytes().decode("utf-8"))
    except (RequestError, OSError, UnicodeError) as exc:
        print(f"заявка отклонена: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run — PASS** — 29 passed (проверено; в т.ч. ID вида `"123"`/`"yes"`/`"null"`
сериализуются строками, CLI отказывает на CRLF и одиночном CR на файловой границе).

- [ ] **Step 6: Commit** — `feat(need): строгая заявка на одобрение (§11.3 п.3a)`.

---

### Task 3: Факты форджа и стенд форджа

**Files:**
- Create: `governance/brief_facts.py`
- Modify: `governance/ops.py` (импорт, `class RealOps(BriefFactsMixin)`, сигнатуры
  Protocol после `repo_file_fact`)
- Create: `tests/forge_fake.py`
- Test: `tests/test_governance_ops_brief_facts.py`, `tests/test_governance_ops_policy_facts.py`
  (полнота ответа существующего `repo_file_fact`)

**Interfaces:**
- Produces (`governance/brief_facts.py`): `BriefPrFacts`, `PrComment`,
  `DefaultBranch(name, sha)`; методы `BriefFactsMixin` (они же — Protocol `Ops`):

```python
def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]
def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]
def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]
def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]
def policy_version_fact_at(self, repo_slug: str, ref: str, path: str, sha: str) -> Fact[bool]
def compare_files_fact(self, repo_slug: str, base: str, head: str) -> Fact[tuple[tuple[str, str], ...]]
```

- Produces (`tests/forge_fake.py`): `FakeForge` (те же методы + `repo_file_fact`,
  `policy_version_fact`; удобства `add_policy`, `reconfirm`, `set_pr`);
  `consistent_world(monkeypatch)`; `request(**changes)`; константы `DRAFT`, `SIGNED`,
  `HUMAN`, `MERGED_AT`, `REPO`, `DIR`, `POLICY_REPO`, `P`, `C1`, `C2`, `MERGE`, `HEAD`,
  `BASE`, `PR`.

- [ ] **Step 1: Тесты адаптера на записанных ответах `gh`**

```python
"""Факты форджа brief-маршрута (§11.4.6): адаптер RealOps на записанных ответах gh.

Ответы — ровно то, что печатает `gh` (для `--jq` — уже отжатый вывод).
Неполнота любого рода — UNAVAILABLE, а не «что успели прочитать».
"""

from __future__ import annotations

import json
import subprocess

import pytest

from governance.facts import Outcome
from governance.ops import RealOps

REPO = "o/target"
HEAD = "1" * 40
MERGE = "2" * 40
SHA = "3" * 40


def _gh(monkeypatch, responses: list[tuple[int, object]]) -> list[list[str]]:
    calls: list[list[str]] = []
    queue = list(responses)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        assert queue, f"лишний вызов {argv}"
        rc, payload = queue.pop(0)
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _page(nodes, has_next=False, cursor=None, key="files", **pr):
    node = {
        "state": "MERGED",
        "baseRefName": "main",
        "headRefName": "brief/WS-1",
        "headRefOid": HEAD,
        "mergedBy": {"login": "human"},
        "mergedAt": "2026-10-08T10:00:00Z",
        "mergeCommit": {"oid": MERGE},
        key: {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        },
    }
    node.update(pr)
    return {"data": {"repository": {"pullRequest": node}}}


def _file(path, change="ADDED"):
    return {"path": path, "changeType": change}


def test_merged_pr_found(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_file("d/brief.md")]))])
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND
    assert fact.value.files == (("d/brief.md", "added"),)
    assert fact.value.merge_commit == MERGE and fact.value.head_sha == HEAD


def test_files_pagination_followed(monkeypatch) -> None:
    calls = _gh(
        monkeypatch,
        [
            (0, _page([_file("d/brief.md")], has_next=True, cursor="c1")),
            (0, _page([_file("d/approval-request.yaml")])),
        ],
    )
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert [f for f, _ in fact.value.files] == ["d/brief.md", "d/approval-request.yaml"]
    assert "c=c1" in calls[1]


@pytest.mark.parametrize(
    "pages",
    [
        [_page([_file("a")], has_next=True, cursor=None)],
        [
            _page([_file("a")], has_next=True, cursor="c1"),
            _page([], has_next=True, cursor="c1"),
        ],
        [
            _page([_file("a")], has_next=True, cursor="c1"),
            _page([], headRefOid="9" * 40),
        ],
    ],
    ids=["no-cursor", "cursor-stuck", "head-moved"],
)
def test_incomplete_pages_unavailable(monkeypatch, pages) -> None:
    _gh(monkeypatch, [(0, p) for p in pages])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_page_without_pageinfo_unavailable(monkeypatch) -> None:
    page = _page([_file("a")])
    del page["data"]["repository"]["pullRequest"]["files"]["pageInfo"]
    _gh(monkeypatch, [(0, page)])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "over",
    [
        {"state": "OPEN"},
        {"mergedBy": None},
        {"state": "WEIRD"},
        {"headRefOid": None},
    ],
    ids=["open-with-merge", "merged-no-login", "unknown-state", "no-head"],
)
def test_impossible_shapes_unavailable(monkeypatch, over) -> None:  # T48a
    _gh(monkeypatch, [(0, _page([_file("a")], **over))])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_open_pr_without_merge_fields_found(monkeypatch) -> None:
    page = _page(
        [_file("a")], state="OPEN", mergedBy=None, mergedAt=None, mergeCommit=None
    )
    _gh(monkeypatch, [(0, page)])
    fact = RealOps().brief_pr_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND and fact.value.merge_commit is None


def test_rc_failure_unavailable(monkeypatch) -> None:
    _gh(monkeypatch, [(1, "")])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def _comment(**over):
    node = {
        "id": "C1",
        "author": {"login": "human"},
        "body": "policy-reconfirm: o/p@" + SHA,
        "createdAt": "2026-10-09T00:00:00Z",
        "lastEditedAt": None,
    }
    node.update(over)
    return node


def test_comments_found_with_null_edit(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_comment()], key="comments"))])
    fact = RealOps().pr_comments_fact(REPO, 7)
    assert fact.value[0].last_edited_at is None and fact.value[0].author == "human"


def test_comment_without_edit_field_unavailable(monkeypatch) -> None:
    node = _comment()
    del node["lastEditedAt"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    assert RealOps().pr_comments_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_find_brief_pr_all_states_and_limit(monkeypatch) -> None:
    calls = _gh(monkeypatch, [(0, [{"number": 9}, {"number": 3}])])
    fact = RealOps().find_brief_pr_fact(REPO, "brief/WS-1")
    assert fact.value == [3, 9]
    assert calls[0][calls[0].index("--state") + 1] == "all"
    _gh(monkeypatch, [(0, [{"number": n} for n in range(100)])])
    assert RealOps().find_brief_pr_fact(REPO, "b").outcome is Outcome.UNAVAILABLE


def test_default_branch_name_and_sha(monkeypatch) -> None:
    payload = {
        "data": {
            "repository": {
                "defaultBranchRef": {"name": "main", "target": {"oid": HEAD}}
            }
        }
    }
    _gh(monkeypatch, [(0, payload)])
    fact = RealOps().default_branch_fact(REPO)
    assert (fact.value.name, fact.value.sha) == ("main", HEAD)


def _touched(oid):
    return {
        "data": {
            "repository": {
                "object": {"history": {"nodes": [{"oid": oid}] if oid else []}}
            }
        }
    }


@pytest.mark.parametrize(
    ("status", "touched_oid", "expected"),
    [
        ("ahead", SHA, True),
        ("identical", SHA, True),
        ("ahead", "4" * 40, False),
        ("ahead", None, False),
    ],
    ids=["ancestor", "equal", "did-not-touch", "never-touched"],
)
def test_policy_version_at_found(
    monkeypatch, status, touched_oid, expected
) -> None:  # T21c
    _gh(monkeypatch, [(0, f"{status}\n"), (0, _touched(touched_oid))])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.FOUND and fact.value is expected


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_policy_version_at_not_in_history(monkeypatch, status) -> None:  # T21c
    calls = _gh(monkeypatch, [(0, f"{status}\n")])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.FOUND and fact.value is False
    assert len(calls) == 1
    assert calls[0][2] == f"repos/o/policy/compare/{SHA}...main"


@pytest.mark.parametrize(
    "responses", [[(1, "")], [(0, "weird\n")], [(0, "ahead\n"), (0, {"data": None})]]
)
def test_policy_version_at_unavailable(monkeypatch, responses) -> None:
    _gh(monkeypatch, responses)
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.UNAVAILABLE


def test_compare_files_and_cap(monkeypatch) -> None:
    _gh(monkeypatch, [(0, json.dumps([["d/brief.md", "added"]]))])
    assert RealOps().compare_files_fact(REPO, "a", "b").value == (
        ("d/brief.md", "added"),
    )
    _gh(monkeypatch, [(0, json.dumps([[f"f{i}", "added"] for i in range(300)]))])
    assert RealOps().compare_files_fact(REPO, "a", "b").outcome is Outcome.UNAVAILABLE


# --- неполный ответ ≠ отрицательный факт (ревью части A, A4) ---


def test_history_node_without_oid_is_unavailable(monkeypatch) -> None:
    payload = {"data": {"repository": {"object": {"history": {"nodes": [{}]}}}}}
    _gh(monkeypatch, [(0, "ahead\n"), (0, payload)])
    fact = RealOps().policy_version_fact_at("o/policy", "main", "p.env", SHA)
    assert fact.outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "payload", [{}, [{"number": "7"}], [{}]], ids=["obj", "str", "empty"]
)
def test_find_brief_pr_malformed_is_unavailable(monkeypatch, payload) -> None:
    _gh(monkeypatch, [(0, payload)])
    assert RealOps().find_brief_pr_fact(REPO, "b").outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize(
    "payload", [{}, [["only-one"]], [["", "added"]]], ids=["obj", "short", "empty"]
)
def test_compare_malformed_is_unavailable(monkeypatch, payload) -> None:
    _gh(monkeypatch, [(0, json.dumps(payload))])
    assert RealOps().compare_files_fact(REPO, "a", "b").outcome is Outcome.UNAVAILABLE


def test_comment_without_author_field_is_unavailable(monkeypatch) -> None:
    node = _comment()
    del node["author"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    assert RealOps().pr_comments_fact(REPO, 7).outcome is Outcome.UNAVAILABLE


def test_comment_with_deleted_author_is_found_unknown(monkeypatch) -> None:
    _gh(monkeypatch, [(0, _page([_comment(author=None)], key="comments"))])
    fact = RealOps().pr_comments_fact(REPO, 7)
    assert fact.outcome is Outcome.FOUND and fact.value[0].author == ""


@pytest.mark.parametrize("over", [{"mergedBy": "bad"}, {"mergeCommit": ["x"]}])
def test_merge_fields_of_wrong_type_are_unavailable(monkeypatch, over) -> None:
    _gh(monkeypatch, [(0, _page([_file("a")], **over))])
    assert RealOps().brief_pr_fact(REPO, 7).outcome is Outcome.UNAVAILABLE
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: 'RealOps' object has no attribute 'brief_pr_fact'`).

- [ ] **Step 3: Типы и миксин**

```python
"""Факты форджа для brief-PR, комментариев и версий политики (спека §11.4.6).

Каждый факт — `Fact`: неполнота (страница без `pageInfo`, курсор без
продвижения, голова PR сменилась между страницами, список упёрся в лимит)
даёт `UNAVAILABLE`, а не «то, что успели прочитать». Поля по состояниям PR:
`merged_*` обязательны у `MERGED` и пусты у `OPEN`/`CLOSED`.

Методы — миксин `BriefFactsMixin` к `RealOps`: им нужны только
`_graphql_repository` и `subprocess` (`gh`).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from governance.facts import Fact, Outcome, unavailable

PR_STATES = ("OPEN", "CLOSED", "MERGED")
#: Лимит `gh pr list`: ответ ровно такой длины не доказывает полноты.
PR_LIST_LIMIT = 100
#: Потолок файлов в REST compare: ответ такой длины не доказывает полноты.
COMPARE_FILES_CAP = 300

_BRIEF_PR_QUERY = (
    "query($o:String!,$n:String!,$p:Int!,$c:String){"
    "repository(owner:$o,name:$n){pullRequest(number:$p){"
    "state baseRefName headRefName headRefOid mergedAt "
    "mergedBy{login} mergeCommit{oid} "
    "files(first:100,after:$c){nodes{path changeType} "
    "pageInfo{hasNextPage endCursor}}}}}"
)
_PR_COMMENTS_QUERY = (
    "query($o:String!,$n:String!,$p:Int!,$c:String){"
    "repository(owner:$o,name:$n){pullRequest(number:$p){headRefOid "
    "comments(first:100,after:$c){nodes{id author{login} body createdAt "
    "lastEditedAt} pageInfo{hasNextPage endCursor}}}}}"
)
_DEFAULT_BRANCH_QUERY = (
    "query($o:String!,$n:String!){repository(owner:$o,name:$n){"
    "defaultBranchRef{name target{oid}}}}"
)
#: Последний коммит, тронувший `path`, в истории от `sha` включительно.
_TOUCHED_QUERY = (
    "query($o:String!,$n:String!,$s:GitObjectID!,$p:String!){"
    "repository(owner:$o,name:$n){object(oid:$s){"
    "... on Commit{history(first:1,path:$p){nodes{oid}}}}}}"
)


@dataclass(frozen=True)
class BriefPrFacts:
    """Факты PR, которые судят brief-PR (§11.4.2 п.3, §11.3 п.4)."""

    number: int
    state: str
    base_ref: str
    head_ref: str
    head_sha: str
    files: tuple[tuple[str, str], ...]
    merged_by: str | None
    merged_at: str | None
    merge_commit: str | None


@dataclass(frozen=True)
class PrComment:
    """Комментарий PR; `last_edited_at` — `None`, только если правок не было."""

    id: str
    author: str
    body: str
    created_at: str
    last_edited_at: str | None


@dataclass(frozen=True)
class DefaultBranch:
    """Ветка по умолчанию и её неизменяемая голова на момент чтения."""

    name: str
    sha: str


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _gh(args: list[str]) -> str | None:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


class BriefFactsMixin:
    """Факты форджа brief-маршрута поверх `_graphql_repository` хоста."""

    def _graphql_repository(self, query: str, **variables: str) -> dict | None:
        raise NotImplementedError  # реализует RealOps

    def _pr_pages(
        self, query: str, key: str, repo_slug: str, pr: int
    ) -> tuple[dict, list[Any]] | str:
        """Все страницы списка `key` PR; строка — причина неполноты."""
        owner, name = repo_slug.split("/", 1)
        nodes: list[Any] = []
        first: dict | None = None
        cursor: str | None = None
        while True:
            variables = {"o": owner, "n": name, "p": str(pr)}
            if cursor is not None:
                variables["c"] = cursor
            repository = self._graphql_repository(query, **variables)
            node = (repository or {}).get("pullRequest")
            block = node.get(key) if isinstance(node, dict) else None
            if not isinstance(block, dict) or not isinstance(block.get("nodes"), list):
                return "запрос не удался или ответ без списка"
            info = block.get("pageInfo")
            if not isinstance(info, dict) or not isinstance(
                info.get("hasNextPage"), bool
            ):
                return "страница без pageInfo — полнота не доказана"
            if first is None:
                first = node
            elif node.get("headRefOid") != first.get("headRefOid"):
                return "голова PR сменилась между страницами"
            nodes.extend(block["nodes"])
            if not info["hasNextPage"]:
                return first, nodes
            nxt = info.get("endCursor")
            if not _nonempty(nxt) or nxt == cursor:
                return "курсор не продвигается"
            cursor = nxt

    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]:
        """Факты PR по состояниям; форма, которой у состояния нет, — UNAVAILABLE."""
        what = f"PR {repo_slug}#{pr}"
        got = self._pr_pages(_BRIEF_PR_QUERY, "files", repo_slug, pr)
        if isinstance(got, str):
            return unavailable(f"{what}: {got}")
        node, files = got
        state = node.get("state")
        heads = (
            node.get("baseRefName"),
            node.get("headRefName"),
            node.get("headRefOid"),
        )
        merged_by, merge_commit = node.get("mergedBy"), node.get("mergeCommit")
        if not all(v is None or isinstance(v, dict) for v in (merged_by, merge_commit)):
            return unavailable(f"{what}: mergedBy/mergeCommit не объект")
        merged = (
            (merged_by or {}).get("login"),
            node.get("mergedAt"),
            (merge_commit or {}).get("oid"),
        )
        if state not in PR_STATES or not all(_nonempty(v) for v in heads):
            return unavailable(f"{what}: неожиданная форма (state={state!r})")
        if state == "MERGED" and not all(_nonempty(v) for v in merged):
            return unavailable(f"{what}: MERGED без полного события мержа")
        if state != "MERGED" and any(v is not None for v in merged):
            return unavailable(f"{what}: {state} с полями мержа")
        pairs: list[tuple[str, str]] = []
        for entry in files:
            path = entry.get("path") if isinstance(entry, dict) else None
            change = entry.get("changeType") if isinstance(entry, dict) else None
            if not _nonempty(path) or not _nonempty(change):
                return unavailable(f"{what}: битый узел файла")
            pairs.append((str(path), str(change).lower()))
        facts = BriefPrFacts(
            number=pr,
            state=str(state),
            base_ref=str(heads[0]),
            head_ref=str(heads[1]),
            head_sha=str(heads[2]),
            files=tuple(pairs),
            merged_by=merged[0],
            merged_at=merged[1],
            merge_commit=merged[2],
        )
        return Fact(Outcome.FOUND, facts, f"{what} прочитан")

    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]:
        """Все комментарии PR; узел без поля `lastEditedAt` — UNAVAILABLE."""
        what = f"комментарии {repo_slug}#{pr}"
        got = self._pr_pages(_PR_COMMENTS_QUERY, "comments", repo_slug, pr)
        if isinstance(got, str):
            return unavailable(f"{what}: {got}")
        comments: list[PrComment] = []
        for c in got[1]:
            # Отсутствующее поле — неполный ответ (UNAVAILABLE); явный `null`
            # — установленный факт: `lastEditedAt: null` — правок не было,
            # `author: null` — учётка удалена (автор неизвестен, такое
            # подтверждение недействительно, но факт о нём установлен).
            if not isinstance(c, dict) or not {"lastEditedAt", "author"} <= set(c):
                return unavailable(f"{what}: узел без lastEditedAt/author")
            raw_author = c["author"]
            if raw_author is None:
                author = ""
            elif isinstance(raw_author, dict) and _nonempty(raw_author.get("login")):
                author = str(raw_author["login"])
            else:
                return unavailable(f"{what}: автор комментария не прочитан")
            edited = c["lastEditedAt"]
            if not all(_nonempty(c.get(k)) for k in ("id", "body", "createdAt")) or (
                edited is not None and not _nonempty(edited)
            ):
                return unavailable(f"{what}: битый узел комментария")
            comments.append(
                PrComment(
                    id=c["id"],
                    author=author,
                    body=c["body"],
                    created_at=c["createdAt"],
                    last_edited_at=edited,
                )
            )
        return Fact(Outcome.FOUND, comments, f"{len(comments)} комментариев")

    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]:
        """Номера PR (любое состояние) с головой `head_ref`."""
        out = _gh(
            [
                "pr",
                "list",
                "--repo",
                repo_slug,
                "--head",
                head_ref,
                "--state",
                "all",
                "--json",
                "number",
                "--limit",
                str(PR_LIST_LIMIT),
            ]
        )
        what = f"PR ветки {head_ref}"
        try:
            items = json.loads(out or "x")
        except json.JSONDecodeError:
            return unavailable(f"{what}: запрос не удался")
        if not isinstance(items, list) or not all(
            isinstance(i, dict) and type(i.get("number")) is int for i in items
        ):
            return unavailable(f"{what}: неожиданная форма ответа")
        numbers = sorted(int(i["number"]) for i in items)
        if len(numbers) >= PR_LIST_LIMIT:
            return unavailable(f"{what}: упёрлись в лимит {PR_LIST_LIMIT}")
        return Fact(Outcome.FOUND, numbers, f"{len(numbers)} PR")

    def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]:
        """Ветка по умолчанию и SHA её головы."""
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql_repository(_DEFAULT_BRANCH_QUERY, o=owner, n=name)
        ref = (repository or {}).get("defaultBranchRef") or {}
        branch, sha = ref.get("name"), (ref.get("target") or {}).get("oid")
        if not _nonempty(branch) or not _nonempty(sha):
            return unavailable(f"ветка по умолчанию {repo_slug} не прочитана")
        return Fact(Outcome.FOUND, DefaultBranch(str(branch), str(sha)), "прочитана")

    def policy_version_fact_at(
        self, repo_slug: str, ref: str, path: str, sha: str
    ) -> Fact[bool]:
        """`sha` — версия политики: в истории `ref` (или равен голове) и менял `path`.

        `compare <sha>...<ref>`: `sha` — base, голова `ref` — head; base-предок
        даёт `ahead`, равенство — `identical`; `behind`/`diverged` — не версия.
        «Менял `path`»: последний коммит, тронувший `path`, в истории от `sha`
        включительно — сам `sha`.
        """
        out = _gh(
            ["api", f"repos/{repo_slug}/compare/{sha}...{ref}", "--jq", ".status"]
        )
        status = (out or "").strip()
        if status not in ("ahead", "identical", "behind", "diverged"):
            return unavailable(f"compare {sha}...{ref}: не установлено")
        if status in ("behind", "diverged"):
            return Fact(Outcome.FOUND, False, f"{sha} не в истории {ref} ({status})")
        owner, name = repo_slug.split("/", 1)
        repository = self._graphql_repository(
            _TOUCHED_QUERY, o=owner, n=name, s=sha, p=path
        )
        commit = (repository or {}).get("object")
        try:
            nodes = commit["history"]["nodes"]
        except (KeyError, TypeError):
            return unavailable(f"история {path} от {sha}: не прочитана")
        if not isinstance(nodes, list) or (
            nodes
            and not (isinstance(nodes[0], dict) and _nonempty(nodes[0].get("oid")))
        ):
            return unavailable(f"история {path} от {sha}: неожиданная форма")
        touched = (
            bool(nodes) and isinstance(nodes[0], dict) and nodes[0].get("oid") == sha
        )
        return Fact(
            Outcome.FOUND, touched, f"{sha} {'менял' if touched else 'не менял'} {path}"
        )

    def compare_files_fact(
        self, repo_slug: str, base: str, head: str
    ) -> Fact[tuple[tuple[str, str], ...]]:
        """Файлы диапазона `base...head` (REST compare); у потолка — UNAVAILABLE."""
        out = _gh(
            [
                "api",
                f"repos/{repo_slug}/compare/{base}...{head}",
                "--jq",
                "[.files[] | [.filename, .status]]",
            ]
        )
        try:
            rows = json.loads(out or "x")
        except json.JSONDecodeError:
            return unavailable(f"compare {base}...{head}: не прочитан")
        if not isinstance(rows, list) or not all(
            isinstance(row, list) and len(row) == 2 and all(_nonempty(v) for v in row)
            for row in rows
        ):
            return unavailable(f"compare {base}...{head}: неожиданная форма")
        pairs = tuple((str(f), str(s)) for f, s in rows)
        if len(pairs) >= COMPARE_FILES_CAP:
            return unavailable(f"compare {base}...{head}: потолок {COMPARE_FILES_CAP}")
        return Fact(Outcome.FOUND, pairs, f"{len(pairs)} файлов")
```

- [ ] **Step 4: Подключить к `ops.py`**

```diff
diff --git a/governance/ops.py b/governance/ops.py
index d2b8229..e6738fc 100644
--- a/governance/ops.py
+++ b/governance/ops.py
@@ -24,6 +24,12 @@ from typing import Protocol
 from urllib.parse import quote
 
 from governance import interview as _interview
+from governance.brief_facts import (
+    BriefFactsMixin,
+    BriefPrFacts,
+    DefaultBranch,
+    PrComment,
+)
 from governance.brief_input import descriptor_source_blobs, descriptor_source_paths
 from governance.decomposition_guard import DELIVERABLE_KINDS
 from governance.facts import Fact, Outcome, unavailable
@@ -224,6 +230,23 @@ class Ops(Protocol):
 
     def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]: ...
 
+    # Факты brief-маршрута (спека need-stage §11.4.6, `governance/brief_facts.py`).
+    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]: ...
+
+    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]: ...
+
+    def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]: ...
+
+    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]: ...
+
+    def policy_version_fact_at(
+        self, repo_slug: str, ref: str, path: str, sha: str
+    ) -> Fact[bool]: ...
+
+    def compare_files_fact(
+        self, repo_slug: str, base: str, head: str
+    ) -> Fact[tuple[tuple[str, str], ...]]: ...
+
     def delete_remote_branch(self, repo_slug: str, branch: str) -> bool: ...
 
     def delete_local_branch(self, target_dir: str, branch: str) -> bool: ...
@@ -917,7 +940,7 @@ _AUTHOR_DSL = {
 }
 
 
-class RealOps:
+class RealOps(BriefFactsMixin):
     """RealOps: точные команды внешних эффектов (спека §5/§8)."""
 
     def ensure_branch(self, target_dir: str, branch: str) -> None:
@@ -1961,15 +1984,26 @@ class RealOps:
         commit = repository["object"]
         if commit is None:
             return Fact(Outcome.ABSENT, None, f"коммита {sha} в {repo_slug} нет")
-        entry = commit.get("file") if isinstance(commit, dict) else None
+        # Отсутствующее поле `file` — неполный ответ (или объект не коммит),
+        # а не «файла нет»: только явный `file: null` — установленное
+        # отсутствие (ревью плана engineer-маршрута, A8).
+        if not isinstance(commit, dict) or "file" not in commit:
+            return unavailable(f"{what}: ответ без поля file")
+        entry = commit["file"]
         if entry is None:
             return Fact(Outcome.ABSENT, None, f"в {repo_slug}@{sha} нет {path}")
         blob = entry.get("object") if isinstance(entry, dict) else None
-        text = blob.get("text") if isinstance(blob, dict) else None
-        if not isinstance(text, str) or (
-            isinstance(blob, dict) and (blob.get("isBinary") or blob.get("isTruncated"))
+        # Полный текст доказан только явными `isBinary: false` и
+        # `isTruncated: false` при строковом `text`: отсутствие поля, `null`
+        # и иной тип — неполный ответ, не «текст целиком» (ревью плана, A10).
+        if (
+            not isinstance(blob, dict)
+            or not isinstance(blob.get("text"), str)
+            or blob.get("isBinary") is not False
+            or blob.get("isTruncated") is not False
         ):
             return unavailable(f"{what}: содержимое не прочитано")
+        text = blob["text"]
         return Fact(Outcome.FOUND, text, f"{path}@{sha} прочитан")
 
     def local_branch_head_fact(self, target_dir: str, branch: str) -> Fact[str]:
```

Тесты полноты `repo_file_fact` — полный перебор форм полей блоба (отсутствует /
`null` / не тот тип / строка `"false"` / `true` / `false`): `FOUND` только при строковом
`text` и явных `isBinary: false`, `isTruncated: false`; повреждённая запись `file` —
`UNAVAILABLE`:

```diff
diff --git a/tests/test_governance_ops_policy_facts.py b/tests/test_governance_ops_policy_facts.py
index b961b0d..e2fec8b 100644
--- a/tests/test_governance_ops_policy_facts.py
+++ b/tests/test_governance_ops_policy_facts.py
@@ -12,6 +12,8 @@ from __future__ import annotations
 import json
 import subprocess
 
+import pytest
+
 from governance.facts import Outcome
 from governance.ops import RealOps
 
@@ -116,3 +118,57 @@ def test_file_found_absent_unavailable(monkeypatch) -> None:
     assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
     _gh(monkeypatch, None, rc=1)
     assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
+
+
+def test_file_field_missing_is_unavailable_not_absent(monkeypatch) -> None:
+    """Неполный ответ (`object: {}`) — UNAVAILABLE; ABSENT — только явный null."""
+    _gh(monkeypatch, {"data": {"repository": {"object": {}}}})
+    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
+
+
+_MISSING = object()
+_FORMS = {
+    "text": [_MISSING, None, 1, ["x"], "K=v\n"],
+    "isBinary": [_MISSING, None, 0, "false", True, False],
+    "isTruncated": [_MISSING, None, 0, "false", True, False],
+}
+
+
+def _blob(text, is_binary, is_truncated) -> dict:
+    blob = {}
+    for key, value in (
+        ("text", text),
+        ("isBinary", is_binary),
+        ("isTruncated", is_truncated),
+    ):
+        if value is not _MISSING:
+            blob[key] = value
+    return {"data": {"repository": {"object": {"file": {"object": blob}}}}}
+
+
+@pytest.mark.parametrize("text", _FORMS["text"])
+@pytest.mark.parametrize("is_binary", _FORMS["isBinary"])
+@pytest.mark.parametrize("is_truncated", _FORMS["isTruncated"])
+def test_file_fact_found_only_for_complete_text(
+    monkeypatch, text, is_binary, is_truncated
+) -> None:
+    """Перебор форм (ревью плана A8→A10): FOUND — только строковый text при
+    явных `isBinary: false` и `isTruncated: false`; всё прочее — UNAVAILABLE."""
+    _gh(monkeypatch, _blob(text, is_binary, is_truncated))
+    fact = RealOps().repo_file_fact(REPO, SHA, PATH)
+    complete = text == "K=v\n" and is_binary is False and is_truncated is False
+    assert fact.outcome is (Outcome.FOUND if complete else Outcome.UNAVAILABLE)
+
+
+@pytest.mark.parametrize(
+    "obj",
+    [
+        {"file": {}},
+        {"file": {"object": None}},
+        {"file": "x"},
+        {"file": {"object": "x"}},
+    ],
+)
+def test_file_fact_malformed_entry_is_unavailable(monkeypatch, obj) -> None:
+    _gh(monkeypatch, {"data": {"repository": {"object": obj}}})
+    assert RealOps().repo_file_fact(REPO, SHA, PATH).outcome is Outcome.UNAVAILABLE
```

- [ ] **Step 5: Стенд форджа**

```python
"""Стенд форджа brief-маршрута (§11.4.2–§11.4.3) и согласованный «мир».

`FakeForge` реализует факты форджа, которые читают предикаты акта и
политики; `consistent_world()` строит мир, где всё сходится: brief-PR #7
смержен человеком из политики `P`, в merge-коммите бриф и заявка, политика
с тех пор не менялась. Тест портит ровно один факт (правило пар §11.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from governance import approval_request as ar
from governance import discovery_approval as da
from governance.brief_facts import BriefPrFacts, DefaultBranch, PrComment
from governance.facts import Fact, Outcome, unavailable

FIX = Path(__file__).parent / "fixtures" / "discovery_approval"
DRAFT = (FIX / "draft-brief.md").read_text(encoding="utf-8")
SIGNED = (FIX / "signed-brief.md").read_text(encoding="utf-8")
HUMAN = "andrei-shtanakov"
MERGED_AT = "2026-10-08T10:00:00Z"
REPO = "owner/alpha"
DIR = "workstreams/WS-1/spec/00-discovery"
POLICY_REPO = "andrei-shtanakov/approval-policy"
POLICY_REF = "main"
POLICY_PATH = "policy/approvers.env"
P = "a1" * 20
C1 = "c1" * 20
C2 = "c2" * 20
MERGE = "d1" * 20
HEAD = "e1" * 20
BASE = "f1" * 20
PR = 7


@dataclass
class FakeForge:
    """Факты форджа по полям; `unavailable_facts` роняет выбранные факты."""

    prs: dict[int, BriefPrFacts] = field(default_factory=dict)
    files: dict[tuple[str, str], str] = field(default_factory=dict)
    comments: dict[int, list[PrComment]] = field(default_factory=dict)
    default_branch: DefaultBranch = field(
        default_factory=lambda: DefaultBranch("main", BASE)
    )
    policy_head: str = P
    #: sha → (в истории ref, менял файл политики)
    policy_versions: dict[str, bool] = field(default_factory=dict)
    compare: dict[tuple[str, str], tuple[tuple[str, str], ...]] = field(
        default_factory=dict
    )
    unavailable_facts: set[str] = field(default_factory=set)

    def _down(self, name: str) -> bool:
        return name in self.unavailable_facts

    def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]:
        if self._down("pr") or pr not in self.prs:
            return unavailable(f"PR #{pr}")
        return Fact(Outcome.FOUND, self.prs[pr], "pr")

    def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]:
        if self._down("find"):
            return unavailable("find")
        found = sorted(n for n, p in self.prs.items() if p.head_ref == head_ref)
        return Fact(Outcome.FOUND, found, "find")

    def default_branch_fact(self, repo_slug: str) -> Fact[DefaultBranch]:
        if self._down("default"):
            return unavailable("default")
        return Fact(Outcome.FOUND, self.default_branch, "default")

    def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]:
        if self._down("comments"):
            return unavailable("comments")
        return Fact(Outcome.FOUND, list(self.comments.get(pr, [])), "comments")

    def repo_file_fact(self, repo_slug: str, sha: str, path: str) -> Fact[str]:
        if self._down("file"):
            return unavailable("file")
        text = self.files.get((sha, path))
        if text is None:
            return Fact(Outcome.ABSENT, None, f"нет {path}@{sha}")
        return Fact(Outcome.FOUND, text, "file")

    def policy_version_fact(self, repo_slug: str, branch: str, path: str) -> Fact[str]:
        if self._down("policy"):
            return unavailable("policy")
        return Fact(Outcome.FOUND, self.policy_head, "head")

    def policy_version_fact_at(
        self, repo_slug: str, ref: str, path: str, sha: str
    ) -> Fact[bool]:
        if self._down("history"):
            return unavailable("history")
        return Fact(Outcome.FOUND, self.policy_versions.get(sha, False), "history")

    def compare_files_fact(
        self, repo_slug: str, base: str, head: str
    ) -> Fact[tuple[tuple[str, str], ...]]:
        if self._down("compare") or (base, head) not in self.compare:
            return unavailable("compare")
        return Fact(Outcome.FOUND, self.compare[(base, head)], "compare")

    # Удобства мира — не часть контракта Ops.
    def add_policy(self, sha: str, accounts: str = HUMAN, *, head: bool = True) -> None:
        """Новая версия политики `sha` с составом `accounts`."""
        self.policy_versions[sha] = True
        self.files[(sha, POLICY_PATH)] = f"AUTHORIZED_APPROVER_ACCOUNTS={accounts}\n"
        if head:
            self.policy_head = sha

    def reconfirm(
        self,
        sha: str,
        *,
        author: str = HUMAN,
        created: str = "2026-10-09T00:00:00Z",
        edited: str | None = None,
        body: str | None = None,
    ) -> None:
        """Комментарий-подтверждение политики в brief-PR."""
        text = body if body is not None else f"policy-reconfirm: {POLICY_REPO}@{sha}"
        self.comments.setdefault(PR, []).append(
            PrComment(
                f"C{len(self.comments.get(PR, []))}", author, text, created, edited
            )
        )

    def set_pr(self, **changes: object) -> None:
        """Заменить поля brief-PR #7."""
        self.prs[PR] = replace(self.prs[PR], **changes)  # type: ignore[arg-type]


def request(**changes: str) -> ar.ApprovalRequest:
    """Заявка мира (по умолчанию — согласованная)."""
    base = ar.ApprovalRequest(
        brief_self_hash=da.self_hash(DRAFT),
        policy_repo=POLICY_REPO,
        policy_ref=POLICY_REF,
        policy_path=POLICY_PATH,
        policy_sha=P,
        run_id="WS-1-abc123",
        ws_id="WS-1",
    )
    return replace(base, **changes)


def consistent_world(monkeypatch=None) -> FakeForge:
    """Мир, где brief-PR #7 — действительный акт одобрения брифа фикстуры."""
    if monkeypatch is not None:
        monkeypatch.delenv("AUTHORIZED_APPROVER_ACCOUNTS", raising=False)
    forge = FakeForge()
    forge.prs[PR] = BriefPrFacts(
        number=PR,
        state="MERGED",
        base_ref="main",
        head_ref="brief/WS-1",
        head_sha=HEAD,
        files=((f"{DIR}/brief.md", "added"), (f"{DIR}/{ar.FILE_NAME}", "added")),
        merged_by=HUMAN,
        merged_at=MERGED_AT,
        merge_commit=MERGE,
    )
    for sha in (MERGE, HEAD):
        forge.files[(sha, f"{DIR}/brief.md")] = DRAFT
        forge.files[(sha, f"{DIR}/{ar.FILE_NAME}")] = ar.render(request())
    forge.add_policy(P)
    return forge
```

- [ ] **Step 6: Run — PASS** — `uv run --frozen pytest tests/test_governance_ops_brief_facts.py tests/test_governance_ops_policy_facts.py -q` → 226 passed (проверено на ревизии задачи). Неполный ответ любого вида —
`UNAVAILABLE`: нет `pageInfo`/курсор стоит/голова сменилась между страницами; узел
истории без `oid`; не-список у поиска PR и compare; узел комментария без поля
`author`/`lastEditedAt` (явный `null` — установленный факт: учётка удалена / правок
не было). Существующий `repo_file_fact` (его читают и предикаты политики): ответ
без поля `file` (`object: {}`) — `UNAVAILABLE`, не «файла нет»; `ABSENT` — только явный
`file: null`; `text` не строка, `isBinary`/`isTruncated` не ровно `false` (в т.ч. поле
отсутствует) — `UNAVAILABLE`. `mergedBy`/`mergeCommit` не объект — `UNAVAILABLE`.

- [ ] **Step 7: Commit** — `feat(need): факты форджа brief-маршрута и стенд форджа (§11.4.6)`.

---

### Task 4: Предикаты акта и политики

**Files:**
- Create: `governance/brief_provenance.py`
- Create: `tests/provenance_cases.py` (общие наборы дефектов: их же гоняет вход spec-loop
  в части B, Task 8)
- Test: `tests/test_brief_provenance.py`

**Interfaces:**
- Consumes: Task 1 (`self_hash`, `verify`), Task 2 (`parse`, `FILE_NAME`, `BRIEF`),
  Task 3 (факты), `policy_rule.policy_source()`, `policy_rule.policy_accounts()`,
  `approval_facts.policy_snapshot(ops, pinned_sha=None)`.
- Produces:

```python
@dataclass(frozen=True)
class Act: repo; pr; dir; merge_commit; merged_by; merged_at; brief_self_hash; act_policy_sha; request
    def as_record(self) -> dict[str, object]   # interview.approval (отчёт)
@dataclass(frozen=True)
class Refusal: reason: str; detail: str; retry: bool = False
@dataclass(frozen=True)
class PolicyOk: current: str; working: str
def files_dir(facts: BriefPrFacts) -> str | Refusal
def read_act(ops, repo: str, pr: int) -> Act | Refusal
def check_operator_brief(act: Act, text: str) -> Refusal | None
def working_version(ops, act: Act) -> str | Refusal
def check_policy(ops, act: Act) -> PolicyOk | Refusal
def reconfirm_line(policy_repo: str, sha: str) -> str
```

Причины `Refusal.reason`: `pr_not_merged`, `pr_wrong_base`, `pr_files`,
`request_invalid`, `request_coordinates`, `brief_hash`, `not_policy_version`,
`merger_not_in_act_policy`, `operator_brief`, `envelope_mismatch`, `policy_forbidden`,
`upstream_policy_drift`, `merger_not_in_current_policy`, `forge_unavailable`
(`retry=True`).

- [ ] **Step 1: Наборы дефектов** — один источник для предикатов (здесь) и для полного
входа spec-loop (часть B): `ACT_DEFECTS` (T19–T22, T25), `OPERATOR_DEFECTS` (T14–T18,
T22 — в т.ч. честно подписанный ДРУГОЙ бриф в буфере и согласованные «бриф + заявка» в
коммите, отличные от буфера), `POLICY_DEFECTS` (T26, T27 — каждый двойник по одному
дефекту), `ACT_UNAVAILABLE`/`POLICY_UNAVAILABLE` (T31):

```python
"""Общие наборы дефектов акта и политики (спека need-stage §11.7, T14–T27, T31).

Каждый случай портит РОВНО один факт согласованного мира `consistent_world()`.
Один набор гоняют предикаты (`tests/test_brief_provenance.py`, Task 4) и полный
вход spec-loop (`tests/test_governance_spec_loop.py`, Task 8 части B): новая
форма дефекта, добавленная сюда, проверяется на обоих уровнях сразу.
"""

from __future__ import annotations

from collections.abc import Callable

from governance import approval_request as ar
from governance import discovery_approval as da
from tests.forge_fake import (
    C1,
    DIR,
    DRAFT,
    HUMAN,
    MERGE,
    MERGED_AT,
    POLICY_PATH,
    POLICY_REPO,
    PR,
    SIGNED,
    FakeForge,
    P,
    request,
)

Spoil = Callable[[FakeForge], None]

BRIEF = f"{DIR}/brief.md"
REQUEST = f"{DIR}/{ar.FILE_NAME}"
#: Другой бриф того же формата, честно подписанный тем же событием мержа.
OTHER_DRAFT = DRAFT.replace("## Goals", "## Goals\n\nиной бриф\n", 1)
OTHER_SIGNED = da._approval.stamp(
    OTHER_DRAFT, da._approval.MergeEvent(HUMAN, MERGED_AT, MERGE)
)


def _request_file(forge: FakeForge, **changes: str) -> None:
    forge.files[(MERGE, REQUEST)] = ar.render(request(**changes))


def _commit_other_brief(forge: FakeForge) -> None:
    """Бриф и заявка в merge-коммите согласованы между собой, но бриф иной."""
    forge.files[(MERGE, BRIEF)] = OTHER_DRAFT
    _request_file(forge, brief_self_hash=da.self_hash(OTHER_DRAFT))


def _files(*files: tuple[str, str]) -> Spoil:
    return lambda f: f.set_pr(files=files)


#: (id, порча, причина `read_act`) — T19–T22, T25.
ACT_DEFECTS: list[tuple[str, Spoil, str]] = [
    (
        "not-merged",
        lambda f: f.set_pr(
            state="OPEN", merged_by=None, merged_at=None, merge_commit=None
        ),
        "pr_not_merged",
    ),
    ("wrong-base", lambda f: f.set_pr(base_ref="side"), "pr_wrong_base"),
    ("only-brief", _files((BRIEF, "added")), "pr_files"),
    (
        "extra-file",
        lambda f: f.set_pr(files=f.prs[PR].files + (("x.txt", "added"),)),
        "pr_files",
    ),
    (
        "bundle-pr",
        lambda f: f.set_pr(
            files=f.prs[PR].files + (("x/30-decomposition.md", "added"),)
        ),
        "pr_files",
    ),
    ("modified", _files((BRIEF, "modified"), (REQUEST, "added")), "pr_files"),
    (
        "two-dirs",
        _files(
            ("a/00-discovery/brief.md", "added"),
            (f"b/00-discovery/{ar.FILE_NAME}", "added"),
        ),
        "pr_files",
    ),
    (
        "not-00-discovery",
        _files(("a/brief.md", "added"), (f"a/{ar.FILE_NAME}", "added")),
        "pr_files",
    ),
    (
        "coords-repo",
        lambda f: _request_file(f, policy_repo="o/other"),
        "request_coordinates",
    ),
    ("coords-ref", lambda f: _request_file(f, policy_ref="dev"), "request_coordinates"),
    (
        "coords-path",
        lambda f: _request_file(f, policy_path="x.env"),
        "request_coordinates",
    ),
    (
        "brief-hash",
        lambda f: f.files.__setitem__((MERGE, BRIEF), DRAFT + "\nправка\n"),
        "brief_hash",
    ),
    (
        "p-not-version",
        lambda f: f.policy_versions.__setitem__(P, False),
        "not_policy_version",
    ),
    (
        "merger-out-of-p",
        lambda f: f.files.__setitem__(
            (P, POLICY_PATH), "AUTHORIZED_APPROVER_ACCOUNTS=other\n"
        ),
        "merger_not_in_act_policy",
    ),
]


def _none(forge: FakeForge) -> None:
    return None


#: (id, порча мира, буфер оператора, причина `check_operator_brief`) — T14–T18, T22.
OPERATOR_DEFECTS: list[tuple[str, Spoil, str, str]] = [
    ("draft", _none, DRAFT, "operator_brief"),
    (
        "no-hash",
        _none,
        SIGNED.replace("approved_content_hash", "x_hash"),
        "operator_brief",
    ),
    ("edited", _none, SIGNED + "\nправка\n", "operator_brief"),
    ("crlf", _none, SIGNED.replace("\n", "\r\n"), "operator_brief"),
    (
        "approver",
        _none,
        SIGNED.replace(f"approver: {HUMAN}", "approver: someone"),
        "envelope_mismatch",
    ),
    (
        "approved-at",
        _none,
        SIGNED.replace(
            "approved_at: '2026-10-08T10:00:00Z'",
            "approved_at: '2026-10-08T11:00:00Z'",
        ),
        "envelope_mismatch",
    ),
    # T22, вторая пара: честно подписанный, но ДРУГОЙ бриф в буфере.
    ("buffer-other-brief", _none, OTHER_SIGNED, "operator_brief"),
    # T22: бриф и заявка в коммите согласованы, но это не бриф буфера.
    ("commit-other-brief", _commit_other_brief, SIGNED, "operator_brief"),
]


def _reconfirm(sha: str = C1, **kwargs: str) -> Spoil:
    def spoil(forge: FakeForge) -> None:
        forge.add_policy(C1, f"{HUMAN},helper")
        forge.reconfirm(sha, **kwargs)

    return spoil


def _drift(forge: FakeForge) -> None:
    forge.add_policy(C1)


def _merger_out_of_c(forge: FakeForge) -> None:
    forge.add_policy(C1, "helper")
    forge.reconfirm(C1, author="helper")


#: (id, порча, причина `check_policy`) — T26, T27 (каждый двойник по одному дефекту).
POLICY_DEFECTS: list[tuple[str, Spoil, str]] = [
    ("drift-no-reconfirm", _drift, "upstream_policy_drift"),
    ("author-not-in-policy", _reconfirm(author="stranger"), "upstream_policy_drift"),
    ("ai-prosto", _reconfirm(author="ai-prosto"), "upstream_policy_drift"),
    ("edited", _reconfirm(edited="2026-10-09T01:00:00Z"), "upstream_policy_drift"),
    (
        "before-merge",
        _reconfirm(created="2026-10-07T00:00:00Z"),
        "upstream_policy_drift",
    ),
    ("not-a-version", _reconfirm("9" * 40), "upstream_policy_drift"),
    ("points-to-p", _reconfirm(P), "upstream_policy_drift"),
    (
        "other-coords",
        _reconfirm(body=f"policy-reconfirm: o/other@{C1}"),
        "upstream_policy_drift",
    ),
    (
        "extra-text",
        _reconfirm(body=f"policy-reconfirm: {POLICY_REPO}@{C1} спасибо"),
        "upstream_policy_drift",
    ),
    ("merger-out-of-c", _merger_out_of_c, "merger_not_in_current_policy"),
]

#: Факты, недоступность которых — «повторите» (T31): акт и политика.
ACT_UNAVAILABLE = ["pr", "default", "file", "history"]
POLICY_UNAVAILABLE = ["comments", "policy"]


def ids(cases: list) -> list[str]:
    """Идентификаторы параметризации — первый элемент случая."""
    return [case[0] for case in cases]
```

- [ ] **Step 2: Тесты предикатов** (каждый портит ровно один факт согласованного мира)

```python
"""Предикаты акта (§11.4.2) и политики (§11.4.3) на стенде форджа.

Каждый тест портит РОВНО один факт согласованного мира; сам мир — двойник
(`test_consistent_world_passes`).
"""

from __future__ import annotations

import pytest

from governance import approval_request as ar
from governance import brief_provenance as bp
from tests.approval_request_cases import DEFECTS
from tests.forge_fake import (
    C1,
    C2,
    MERGE,
    POLICY_REPO,
    PR,
    REPO,
    SIGNED,
    P,
    consistent_world,
    request,
)
from tests.provenance_cases import (
    ACT_DEFECTS,
    ACT_UNAVAILABLE,
    OPERATOR_DEFECTS,
    POLICY_DEFECTS,
    POLICY_UNAVAILABLE,
    REQUEST,
    ids,
)


@pytest.fixture()
def world(monkeypatch):
    return consistent_world(monkeypatch)


def _act(forge) -> bp.Act:
    act = bp.read_act(forge, REPO, PR)
    assert isinstance(act, bp.Act), act
    return act


def _refused(result, reason: str) -> None:
    assert isinstance(result, bp.Refusal), result
    assert result.reason == reason, result


def test_consistent_world_passes(world) -> None:
    act = _act(world)
    assert bp.check_operator_brief(act, SIGNED) is None
    ok = bp.check_policy(world, act)
    assert isinstance(ok, bp.PolicyOk) and ok.current == ok.working == P


def test_act_record_has_spec_fields(world) -> None:
    record = _act(world).as_record()
    assert set(record) == {
        "repo",
        "pr",
        "dir",
        "merge_commit",
        "approver",
        "approved_at",
        "self_hash",
        "act_policy_sha",
    }


def test_manual_envelope_mirroring_the_merge_is_accepted(world) -> None:  # T17
    # Конверт фикстуры подписан `stamp` соседа, но ничем не отличим от
    # вписанного руками точного зеркала: авторство записи не проверяется.
    assert bp.check_operator_brief(_act(world), SIGNED) is None


@pytest.mark.parametrize(("_id", "spoil", "reason"), ACT_DEFECTS, ids=ids(ACT_DEFECTS))
def test_each_act_defect_refuses(world, _id, spoil, reason) -> None:  # T19–T22, T25
    spoil(world)
    _refused(bp.read_act(world, REPO, PR), reason)


@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_merged_request_defects_refuse(world, case, mutate, _m) -> None:  # T21a/T21b
    world.files[(MERGE, REQUEST)] = mutate(ar.render(request()))
    _refused(bp.read_act(world, REPO, PR), "request_invalid")


@pytest.mark.parametrize("fact", ACT_UNAVAILABLE)
def test_unavailable_is_retry_not_rejection(world, fact) -> None:  # T31
    world.unavailable_facts.add(fact)
    got = bp.read_act(world, REPO, PR)
    _refused(got, "forge_unavailable")
    assert got.retry and "не одобрено" not in got.detail


@pytest.mark.parametrize(
    ("_id", "spoil", "text", "reason"), OPERATOR_DEFECTS, ids=ids(OPERATOR_DEFECTS)
)
def test_operator_brief_defects(
    world, _id, spoil, text, reason
) -> None:  # T14–T18, T22
    spoil(world)
    _refused(bp.check_operator_brief(_act(world), text), reason)


def test_drift_without_reconfirm_stops(world) -> None:  # T26
    world.add_policy(C1)
    got = bp.check_policy(world, _act(world))
    _refused(got, "upstream_policy_drift")
    assert bp.reconfirm_line(POLICY_REPO, C1) in got.detail


def test_valid_reconfirm_continues(world) -> None:  # T27
    world.add_policy(C1)
    world.reconfirm(C1)
    got = bp.check_policy(world, _act(world))
    assert isinstance(got, bp.PolicyOk) and got.working == C1


@pytest.mark.parametrize(
    ("_id", "spoil", "reason"), POLICY_DEFECTS, ids=ids(POLICY_DEFECTS)
)
def test_each_policy_defect_refuses(world, _id, spoil, reason) -> None:  # T26, T27
    spoil(world)
    _refused(bp.check_policy(world, _act(world)), reason)


def test_reconfirm_does_not_replace_merger_membership(world) -> None:  # T27
    world.add_policy(C1, "helper")
    world.reconfirm(C1, author="helper")
    _refused(bp.check_policy(world, _act(world)), "merger_not_in_current_policy")


def test_edited_or_deleted_reconfirm_stops_counting(world) -> None:  # Review Focus
    world.add_policy(C1)
    world.reconfirm(C1)
    assert isinstance(bp.check_policy(world, _act(world)), bp.PolicyOk)
    world.comments[PR] = []
    _refused(bp.check_policy(world, _act(world)), "upstream_policy_drift")


def test_latest_reconfirm_wins(world) -> None:  # T28 (логика)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2)
    _refused(bp.check_policy(world, _act(world)), "upstream_policy_drift")
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    assert bp.check_policy(world, _act(world)).working == C2


@pytest.mark.parametrize("fact", POLICY_UNAVAILABLE)
def test_policy_unavailable_is_retry(world, fact) -> None:
    act = _act(world)
    world.unavailable_facts.add(fact)
    got = bp.check_policy(world, act)
    _refused(got, "forge_unavailable")
    assert got.retry


def test_unavailable_while_judging_reconfirm_is_retry(world) -> None:  # P5
    world.add_policy(C1)
    world.reconfirm(C1)
    act = _act(world)
    world.unavailable_facts.add("history")
    got = bp.check_policy(world, act)
    _refused(got, "forge_unavailable")
    assert got.retry


def test_malformed_comments_stop_policy_with_retry(monkeypatch) -> None:
    """Цепочка адаптер → политика: неполный ответ о подтверждениях не даёт
    продолжить по прежнему W, а требует повтора."""
    from governance.ops import RealOps
    from tests.test_governance_ops_brief_facts import _comment, _gh, _page

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1)
    act = bp.read_act(world, "owner/alpha", 7)
    assert isinstance(act, bp.Act)
    node = _comment()
    del node["author"]
    _gh(monkeypatch, [(0, _page([node], key="comments"))])
    world.pr_comments_fact = RealOps().pr_comments_fact  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry


def test_incomplete_read_of_later_reconfirm_policy_is_retry(monkeypatch) -> None:
    """A8: неполное чтение состава позднего подтверждения не позволяет
    продолжить по прежнему W — это retry, а не «подтверждение недействительно»."""
    from governance.facts import unavailable

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2, head=False)
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    act = bp.read_act(world, REPO, PR)
    assert isinstance(act, bp.Act)
    real = world.repo_file_fact

    def flaky(repo_slug, sha, path):
        if sha == C2:
            return unavailable("ответ без поля file")
        return real(repo_slug, sha, path)

    world.repo_file_fact = flaky  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry


@pytest.mark.parametrize(
    "blob",
    [
        {"text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n"},
        {"text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n", "isBinary": False},
        {
            "text": "AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov\n",
            "isBinary": None,
            "isTruncated": False,
        },
    ],
    ids=["no-flags", "no-truncated", "binary-null"],
)
def test_incomplete_policy_blob_of_later_reconfirm_is_retry(monkeypatch, blob) -> None:
    """A10, цепочка адаптер RealOps.repo_file_fact → check_policy: неполный ответ о
    составе позднего подтверждения — retry, а не продолжение по прежнему W."""
    import json
    import subprocess

    from governance.ops import RealOps

    world = consistent_world(monkeypatch)
    world.add_policy(C1)
    world.reconfirm(C1, created="2026-10-09T00:00:00Z")
    world.add_policy(C2, head=False)
    world.reconfirm(C2, created="2026-10-10T00:00:00Z")
    act = bp.read_act(world, REPO, PR)
    assert isinstance(act, bp.Act)
    real = world.repo_file_fact
    payload = {"data": {"repository": {"object": {"file": {"object": blob}}}}}

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(payload), stderr=""
        )

    def via_adapter(repo_slug, sha, path):
        if sha != C2:
            return real(repo_slug, sha, path)
        monkeypatch.setattr(subprocess, "run", fake_run)
        return RealOps().repo_file_fact(repo_slug, sha, path)

    world.repo_file_fact = via_adapter  # type: ignore[method-assign]
    got = bp.check_policy(world, act)
    assert isinstance(got, bp.Refusal) and got.retry
```

- [ ] **Step 3: Run — FAIL** (нет модуля).

- [ ] **Step 4: Реализация**

```python
"""Происхождение upstream engineer-маршрута — только факты форджа (§11.4.2–§11.4.3).

`read_act` выводит акт из brief-PR: форма изменений, заявка и бриф из
merge-коммита, пин политики акта `P`. `check_operator_brief` сверяет байты
файла (оператора или durable-копии) с актом. `check_policy` вычисляет рабочую
версию `W` из человеческих комментариев-подтверждений и сравнивает её с
актуальной `C`. Ни `run.json`, ни локальный git источником не служат (§11.1.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

from governance import approval_facts, approval_request, discovery_approval, policy_rule
from governance.approval_request import ApprovalRequest
from governance.brief_facts import BriefPrFacts
from governance.facts import Outcome

RECONFIRM_PREFIX = "policy-reconfirm: "
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class Act:
    """Акт одобрения брифа, выведенный из форджа."""

    repo: str
    pr: int
    dir: str
    merge_commit: str
    merged_by: str
    merged_at: str
    brief_self_hash: str
    act_policy_sha: str
    request: ApprovalRequest

    def as_record(self) -> dict[str, object]:
        """Запись для `interview.approval` — отчёт, не источник доверия."""
        return {
            "repo": self.repo,
            "pr": self.pr,
            "dir": self.dir,
            "merge_commit": self.merge_commit,
            "approver": self.merged_by,
            "approved_at": self.merged_at,
            "self_hash": self.brief_self_hash,
            "act_policy_sha": self.act_policy_sha,
        }


@dataclass(frozen=True)
class Refusal:
    """Отказ предиката; `retry` — факт не установлен («повторите»)."""

    reason: str
    detail: str
    retry: bool = False


@dataclass(frozen=True)
class PolicyOk:
    """Политика допускает продолжение: актуальная `current` == рабочей `working`."""

    current: str
    working: str


def _unavailable(detail: str) -> Refusal:
    return Refusal(
        "forge_unavailable", f"факт форджа не установлен — повторите: {detail}", True
    )


def reconfirm_line(policy_repo: str, sha: str) -> str:
    """Ровно та строка, которую человек оставляет комментарием (§11.4.3)."""
    return f"{RECONFIRM_PREFIX}{policy_repo}@{sha}"


def files_dir(facts: BriefPrFacts) -> str | Refusal:
    """Каталог `…/00-discovery`, если изменения PR — ровно бриф и заявка (`added`)."""
    paths = [PurePosixPath(p) for p, _ in facts.files]
    dirs = {str(p.parent) for p in paths}
    if (
        len(facts.files) != 2
        or {s for _, s in facts.files} != {"added"}
        or len(dirs) != 1
        or {p.name for p in paths}
        != {approval_request.BRIEF, approval_request.FILE_NAME}
        or PurePosixPath(next(iter(dirs))).name != "00-discovery"
    ):
        return Refusal(
            "pr_files",
            f"изменения PR {list(facts.files)} ≠ бриф + заявка в одном …/00-discovery",
        )
    return next(iter(dirs))


def _version_refusal(ops, sha: str) -> Refusal | None:
    """`sha` — версия политики из SSOT-источника (история ref, менял файл)."""
    repo, ref, path = policy_rule.policy_source()
    fact = ops.policy_version_fact_at(repo, ref, path, sha)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    if fact.value is not True:
        return Refusal("not_policy_version", f"{sha} — не версия {repo}:{path}@{ref}")
    return None


def _accounts(ops, sha: str) -> frozenset[str] | Refusal:
    """Состав версии `sha` (историческое чтение по SHA, §11.4.3)."""
    repo, _, path = policy_rule.policy_source()
    fact = ops.repo_file_fact(repo, sha, path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    accounts = (
        policy_rule.policy_accounts(fact.value)
        if fact.outcome is Outcome.FOUND and isinstance(fact.value, str)
        else None
    )
    if accounts is None:
        return Refusal("not_policy_version", f"политика {sha}: состав не читается")
    return accounts


def _read_merged(ops, repo: str, sha: str, path: str) -> str | Refusal:
    fact = ops.repo_file_fact(repo, sha, path)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    if fact.outcome is not Outcome.FOUND or not isinstance(fact.value, str):
        return Refusal("pr_files", f"нет {path}@{sha}")
    return fact.value


def read_act(ops, repo: str, pr: int) -> Act | Refusal:
    """§11.4.2 п.3–5 и п.7: акт из brief-PR, без файла оператора."""
    pr_fact = ops.brief_pr_fact(repo, pr)
    if pr_fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(pr_fact.detail)
    facts: BriefPrFacts = pr_fact.value
    if facts.state != "MERGED" or not facts.merge_commit:
        return Refusal("pr_not_merged", f"PR #{pr}: {facts.state}")
    default = ops.default_branch_fact(repo)
    if default.outcome is Outcome.UNAVAILABLE:
        return _unavailable(default.detail)
    if facts.base_ref != default.value.name:
        return Refusal(
            "pr_wrong_base",
            f"PR #{pr} смержен в {facts.base_ref}, не в {default.value.name}",
        )
    dir_ = files_dir(facts)
    if isinstance(dir_, Refusal):
        return dir_
    req_text = _read_merged(
        ops, repo, facts.merge_commit, f"{dir_}/{approval_request.FILE_NAME}"
    )
    if isinstance(req_text, Refusal):
        return req_text
    brief_text = _read_merged(
        ops, repo, facts.merge_commit, f"{dir_}/{approval_request.BRIEF}"
    )
    if isinstance(brief_text, Refusal):
        return brief_text
    try:
        req = approval_request.parse(req_text)
    except approval_request.RequestError as exc:
        return Refusal("request_invalid", str(exc))
    if (
        req.policy_repo,
        req.policy_ref,
        req.policy_path,
    ) != policy_rule.policy_source():
        return Refusal("request_coordinates", "координаты политики заявки ≠ SSOT")
    try:
        merged_hash = discovery_approval.self_hash(brief_text)
    except discovery_approval.NotABrief as exc:
        return Refusal("brief_hash", f"бриф в merge-коммите не бриф: {exc}")
    if merged_hash != req.brief_self_hash:
        return Refusal("brief_hash", "self-hash брифа в коммите ≠ заявке")
    refusal = _version_refusal(ops, req.policy_sha)
    if refusal is not None:
        return refusal
    accounts = _accounts(ops, req.policy_sha)
    if isinstance(accounts, Refusal):
        return accounts
    if facts.merged_by not in accounts:
        return Refusal(
            "merger_not_in_act_policy",
            f"{facts.merged_by} ∉ политики акта {req.policy_sha}",
        )
    return Act(
        repo=repo,
        pr=pr,
        dir=dir_,
        merge_commit=facts.merge_commit,
        merged_by=str(facts.merged_by),
        merged_at=str(facts.merged_at),
        brief_self_hash=merged_hash,
        act_policy_sha=req.policy_sha,
        request=req,
    )


def _frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    try:
        meta = yaml.safe_load(text[4:end]) if end != -1 else None
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def check_operator_brief(act: Act, text: str) -> Refusal | None:
    """§11.4.2 п.1 (CR), п.2 (честный конверт) и п.5–6 (связь с актом)."""
    if "\r" in text:
        return Refusal("operator_brief", "CR в файле: нужен LF")
    try:
        own = discovery_approval.self_hash(text)
    except discovery_approval.NotABrief as exc:
        return Refusal("operator_brief", f"не бриф: {exc}")
    debt = discovery_approval.verify(text)
    if debt is not None:
        return Refusal("operator_brief", f"подпись не честная ({debt})")
    if own != act.brief_self_hash:
        return Refusal("operator_brief", "self-hash файла ≠ смерженному брифу")
    meta = _frontmatter(text)
    if (
        meta.get("approver") != act.merged_by
        or meta.get("approved_at") != act.merged_at
    ):
        return Refusal("envelope_mismatch", "конверт не зеркалит событие мержа")
    return None


def _candidate_sha(body: str, policy_repo: str) -> str | None:
    """SHA из тела, если тело — ровно строка подтверждения; иначе None."""
    prefix = f"{RECONFIRM_PREFIX}{policy_repo}@"
    sha = body[len(prefix) :] if body.startswith(prefix) else ""
    if _SHA.match(sha) and body == reconfirm_line(policy_repo, sha):
        return sha
    return None


def working_version(ops, act: Act) -> str | Refusal:
    """`W`: SHA последнего действительного подтверждения, иначе `P` (§11.4.3).

    Недействительное подтверждение (чужая строка, правка, до мержа, не
    версия, автор вне подтверждаемой политики) пропускается; неустановленный
    факт о нём — `Refusal(retry=True)`, а не «подтверждения нет».
    """
    fact = ops.pr_comments_fact(act.repo, act.pr)
    if fact.outcome is Outcome.UNAVAILABLE:
        return _unavailable(fact.detail)
    policy_repo = policy_rule.policy_source()[0]
    working = act.act_policy_sha
    for comment in sorted(fact.value, key=lambda c: c.created_at):
        sha = _candidate_sha(comment.body, policy_repo)
        if (
            sha is None
            or comment.last_edited_at is not None
            or comment.created_at <= act.merged_at
        ):
            continue
        refusal = _version_refusal(ops, sha)
        if refusal is not None:
            if refusal.retry:
                return refusal
            continue
        accounts = _accounts(ops, sha)
        if isinstance(accounts, Refusal):
            if accounts.retry:
                return accounts
            continue
        if comment.author in accounts:
            working = sha
    return working


def check_policy(ops, act: Act) -> PolicyOk | Refusal:
    """§11.4.3 п.2–5: `W` из комментариев, `C` — актуальная, мержер в `C`."""
    snapshot = approval_facts.policy_snapshot(ops, pinned_sha=None)
    if snapshot.outcome is Outcome.UNAVAILABLE:
        return _unavailable(snapshot.detail)
    if snapshot.outcome is not Outcome.FOUND:
        return Refusal(
            "policy_forbidden", f"политика не установлена: {snapshot.detail}"
        )
    current = snapshot.value.sha
    working = working_version(ops, act)
    if isinstance(working, Refusal):
        return working
    if current != working:
        policy_repo = policy_rule.policy_source()[0]
        return Refusal(
            "upstream_policy_drift",
            f"политика сменилась: акт {act.act_policy_sha}, рабочая {working}, "
            f"актуальная {current}; подтверждение — комментарий человека из "
            f"политики в PR #{act.pr}: {reconfirm_line(policy_repo, current)}",
        )
    if act.merged_by not in snapshot.value.accounts:
        return Refusal(
            "merger_not_in_current_policy", f"{act.merged_by} ∉ политики {current}"
        )
    return PolicyOk(current, working)
```

- [ ] **Step 5: Run — PASS** — `uv run --frozen pytest tests/test_brief_provenance.py -q` → 66 passed (проверено; включая цепочку «адаптер RealOps →
`check_policy`»: неполный ответ о подтверждениях — retry, не продолжение по старому `W`).

- [ ] **Step 6: Мутационная проверка** — каждая мутация снимает одну проверку, тест
обязан упасть (скрипт `mutate_task4.sh` в журнале исполнения): `edited`,
`before-merge`, `author`, `base`, `retry-propagate`, `envelope`, `brief-hash`,
`act-policy`, `current-policy`, `drift` — 10/10 пойманы (проверено).

- [ ] **Step 7: Commit** — `feat(need): предикаты акта и политики (§11.4.2–§11.4.3)`.

---

### Task 5: Блокировка прогона на входе процесса

**Files:**
- Create: `governance/run_lock.py`, `tests/locked_runner.py`
- Modify: `governance/runner.py` — `lock` (keyword-only, обязательный) у `start`,
  `advance`, `resume`, `reopen`, `verify`, `attach_session` и внутренних
  `_resume_wave`, `_finalize_wave`, `_next_wave`, `_reconcile_pr_merged_out_of_band`;
  `rl.require(lock, …)` первой строкой публичных функций; CLI `_main` → `_run_command`
  под блокировкой
- Modify: `governance/spec_loop.py` — `main` (блокировка сразу после выбора прогона и
  после восстановления из GitHub, `run.json` перечитывается под ней; новый прогон —
  блокировка вокруг `runner.start`), `_dispatch(state, ops, lock)`
- Modify (механически): `tests/test_governance_runner.py`,
  `tests/test_governance_waves_e2e.py`, `tests/test_halt_gate.py`,
  `tests/test_governance_historical_ledgers.py`, `tests/test_governance_approve_node.py`,
  `tests/test_governance_spec_loop.py`
- Test: `tests/test_run_lock.py`

**Interfaces:**
- Produces: `RunLock(run_id, fd)`, `LockBusy`, `run_lock(run_id)` (context manager),
  `lock_path(run_id)` (`<RUNS_ROOT>-locks/<run_id>.lock` — §11.4.5 в уточнении ревизии 7,
  решение владельца), `require(lock, run_id)` (`TypeError` без токена, `ValueError` —
  чужой прогон). Гарантии (§11.4.5): путь вычисляет одна функция; файл при
  освобождении не удаляется (один inode); крах между захватом и резервированием не
  создаёт каталога прогона; два запуска одного `run_id` берут один файл.
- Точки входа в `spec_loop.main`: `--run-id` — блокировка до первого `load`;
  поиск — КАЖДЫЙ найденный прогон блокируется и перечитывается до решений по статусам
  (в т.ч. `--new-run`); восстановление из GitHub получает колбэк `acquire` и берёт
  блокировку сразу, как только известен `run_id`, — до чтения и записи леджера.
- `verify` — единственная функция, порождающая новый прогон: блокировку ПОТОМКА она
  берёт сама сразу после выбора его id и держит до конца S8 (родителя держит вход).
- Граф вызовов, получающих токен (проверено `grep` по `governance/`): spec-loop
  (`runner.start`, `runner.resume` ×2, `runner.attach_session`), CLI раннера
  (`start`/`resume`/`reopen`/`verify`), внутри раннера — все пути к `advance`
  (`start`, `resume`, `_resume_wave`, `_finalize_wave`, `reopen`, `_next_wave`,
  `_reconcile_pr_merged_out_of_band`). `verify` блокирует РОДИТЕЛЯ (потомок — новый
  `run_id`, S8 без `advance`). `console.py` зовёт раннер только CLI-подпроцессом —
  блокировку берёт CLI.

- [ ] **Step 1: Модуль блокировки**

```python
"""Блокировка прогона (спека need-stage §11.4.5).

Одна на вход процесса: точки входа (spec-loop, CLI раннера, brief-tools)
берут её сразу после выбора прогона и перечитывают `run.json` уже под ней;
функции раннера её не берут, а требуют токен `RunLock`. Файл блокировки
лежит рядом с `RUNS_ROOT`, не внутри каталога прогона: пустой каталог без
`run.json` `find_runs` счёл бы битым леджером.

Дескриптор не наследуется (`O_CLOEXEC` по умолчанию); соседу его передают
явно — `subprocess(..., pass_fds=(lock.fd,))`: пока жив процесс, держащий
описание файла, прогон занят, даже если родитель уже умер.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from governance import run_state as rs


@dataclass(frozen=True)
class RunLock:
    """Токен: процесс держит блокировку прогона `run_id` дескриптором `fd`."""

    run_id: str
    fd: int


class LockBusy(RuntimeError):
    """Прогон исполняется другим процессом."""


def lock_path(run_id: str) -> Path:
    """Файл блокировки прогона: `<RUNS_ROOT>-locks/<run_id>.lock`."""
    rs.validate_id_component(run_id, label="run_id")
    root = rs.RUNS_ROOT.with_name(rs.RUNS_ROOT.name + "-locks")
    return root / f"{run_id}.lock"


@contextmanager
def run_lock(run_id: str) -> Iterator[RunLock]:
    """Эксклюзивная неблокирующая блокировка прогона; занято — `LockBusy`."""
    path = lock_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(f"прогон {run_id} исполняется другим процессом") from exc
        yield RunLock(run_id, fd)
    finally:
        os.close(fd)


def require(lock: RunLock, run_id: str) -> None:
    """Функция раннера исполняется только под блокировкой своего прогона."""
    if not isinstance(lock, RunLock):
        raise TypeError("нужен RunLock: функция раннера — только под блокировкой")
    if lock.run_id != run_id:
        raise ValueError(f"блокировка {lock.run_id!r} не от прогона {run_id!r}")
```

- [ ] **Step 2: Токен в раннере** (сигнатуры, проброс, `require`, CLI)

```diff
diff --git a/governance/runner.py b/governance/runner.py
index ffec8a2..93e423c 100644
--- a/governance/runner.py
+++ b/governance/runner.py
@@ -47,6 +47,7 @@ from governance import (
 from governance import approval_ledger as al
 from governance import approve_node as an
 from governance import interview as iv
+from governance import run_lock as rl
 from governance.edge_check import coordinator as edge_coordinator
 from governance.edge_check import publish as edge_publish
 from governance.edge_check.rules import EdgeCheckError
@@ -338,9 +339,13 @@ def start(
     authoring: str = "waves",
     code: str | None = None,
     plan_item: str | None = None,
+    *,
+    lock: rl.RunLock,
 ) -> RunState:
     """S0: новый прогон, затем сразу `advance()` до стопа/завершения.
 
+    `lock` — блокировка ЭТОГО `run_id`, взятая точкой входа (§11.4.5).
+
     `merge_authority`/`author_backend` валидируются ПЕРВЫМИ, ДО
     резервирования `run_id` (B2 follow-up приёмки B1, minor из #88 —
     `author_backend` внесла заново Task 2, финальное ревью I-3): чистая
@@ -361,6 +366,7 @@ def start(
     WS-lock не оставляет пустой `run.json`-заглушку под несостоявшимся
     `run_id`.
     """
+    rl.require(lock, run_id)
     validate_merge_authority(merge_authority)
     validate_author_backend(author_backend)
     validate_authoring(authoring)
@@ -427,10 +433,10 @@ def start(
         plan_item=plan_item,
     )
     save(state)
-    return advance(state, ops)
+    return advance(state, ops, lock=lock)
 
 
-def advance(state: RunState, ops: Ops) -> RunState:
+def advance(state: RunState, ops: Ops, *, lock: rl.RunLock) -> RunState:
     """Выполняет шаги S1..S8 до стопа (не-``running`` статус) либо конца.
 
     Каждая шаг-функция сама решает, продолжать ли (``True``) или прервать
@@ -452,6 +458,7 @@ def advance(state: RunState, ops: Ops) -> RunState:
     пред-мержевыми op'ами (`review`, `author-*`) — следующий resume падал
     бы в общий шаговый цикл и переигрывал их вхолостую на смерженном PR.
     """
+    rl.require(lock, state.run_id)
     if state.status == "merged_unverified":
         raise ValueError(
             f"run {state.run_id!r} — merged_unverified навсегда; создайте "
@@ -836,7 +843,7 @@ def _refuse_legacy_resume(state: RunState) -> None:
     )
 
 
-def resume(run_id: str, ops: Ops) -> RunState:
+def resume(run_id: str, ops: Ops, *, lock: rl.RunLock) -> RunState:
     """Явный подхват сохранённого run'а (спека §5).
 
     ``merged_unverified`` — отказ (навсегда, см. `advance`). Из
@@ -882,6 +889,7 @@ def resume(run_id: str, ops: Ops) -> RunState:
     только статус обратно в ``running``. Любой другой статус — обычный
     `advance()`.
     """
+    rl.require(lock, run_id)
     state = load(run_id)
     if state.status == "merged_unverified":
         raise ValueError(
@@ -900,11 +908,11 @@ def resume(run_id: str, ops: Ops) -> RunState:
     else:
         _refuse_legacy_resume(state)
     if state.status != "completed":
-        resumed = _resume_wave(state, ops)
+        resumed = _resume_wave(state, ops, lock=lock)
         if resumed is not None:
             return resumed
     if state.status == "waiting_human_merge":
-        _reconcile_pr_merged_out_of_band(state, ops)
+        _reconcile_pr_merged_out_of_band(state, ops, lock=lock)
         return state
     if state.status in ("waiting_interview", "stopped_interview"):
         if state.interview and state.interview.get("session_id") is None:
@@ -918,7 +926,7 @@ def resume(run_id: str, ops: Ops) -> RunState:
             return state
         state.status = "running"
         save(state)
-        return advance(state, ops)
+        return advance(state, ops, lock=lock)
     if state.status == "stopped_author":
         # Ревью #253: `stopped_author` НЕ гарантирует отсутствие PR — brief-
         # coverage внутри `_step_authoring` (E1) выполняется на каждом
@@ -927,14 +935,14 @@ def resume(run_id: str, ops: Ops) -> RunState:
         # (resume из stopped_review/stopped_gate доходит сюда повторно).
         # `state.pr is None` внутри реконсиляции делает вызов безопасным и
         # для «настоящего» stopped_author без PR.
-        if _reconcile_pr_merged_out_of_band(state, ops):
+        if _reconcile_pr_merged_out_of_band(state, ops, lock=lock):
             return state
         _reset_stopped_author(state)
         state.status = "running"
         save(state)
-        return advance(state, ops)
+        return advance(state, ops, lock=lock)
     if state.status in _STOPPED_RESET_OPS:
-        if _reconcile_pr_merged_out_of_band(state, ops):
+        if _reconcile_pr_merged_out_of_band(state, ops, lock=lock):
             return state
         keys = reset_ops_for(state)
         _drop_findings_of(state, keys)
@@ -942,11 +950,11 @@ def resume(run_id: str, ops: Ops) -> RunState:
             state.ops.pop(key, None)
         state.status = "running"
         save(state)
-        return advance(state, ops)
-    return advance(state, ops)
+        return advance(state, ops, lock=lock)
+    return advance(state, ops, lock=lock)
 
 
-def _resume_wave(state: RunState, ops: Ops) -> RunState | None:
+def _resume_wave(state: RunState, ops: Ops, *, lock: rl.RunLock) -> RunState | None:
     """Resume волнового прогона по СОХРАНЁННОМУ ключу заявки (ревью #343, R3).
 
     Заявка волны читается из `candidate-<w>` (`request`, write-ahead в
@@ -987,7 +995,7 @@ def _resume_wave(state: RunState, ops: Ops) -> RunState | None:
         return state
     status = op.get("status")
     if status == al.STATUS_COMPLETED:
-        return _next_wave(state, ops)
+        return _next_wave(state, ops, lock=lock)
     if status in al.TERMINAL_STATUSES:
         _stop_with_comment(
             state,
@@ -1004,7 +1012,7 @@ def _resume_wave(state: RunState, ops: Ops) -> RunState | None:
         # доигрывает тот же шаг — он идемпотентен и знает свою заявку.
         state.status = "running"
         save(state)
-        return advance(state, ops)
+        return advance(state, ops, lock=lock)
     if not op.get("merged_by"):
         facts = ops.pr_facts(state.repo_slug, pr)
         if facts.get("state") == "OPEN":
@@ -1016,10 +1024,12 @@ def _resume_wave(state: RunState, ops: Ops) -> RunState | None:
                 state.status = "waiting_human_merge"
                 save(state)
             return state
-    return _finalize_wave(state, ops, request)
+    return _finalize_wave(state, ops, request, lock=lock)
 
 
-def _finalize_wave(state: RunState, ops: Ops, request: str) -> RunState:
+def _finalize_wave(
+    state: RunState, ops: Ops, request: str, *, lock: rl.RunLock
+) -> RunState:
     """Finalize волны агентом на resume (S8): фаза 2 §I12 → scope-аттестация
     finalize-PR → повторный approve-node (агентский мерж) → следующая волна.
 
@@ -1062,7 +1072,7 @@ def _finalize_wave(state: RunState, ops: Ops, request: str) -> RunState:
         return state
     op = state.ops[request]
     if op["status"] == al.STATUS_COMPLETED:
-        return _next_wave(state, ops)
+        return _next_wave(state, ops, lock=lock)
     finalize_pr = op.get("finalize_pr")
     if finalize_pr is None:
         # Акта мержа candidate ещё нет — ждём человека.
@@ -1088,7 +1098,7 @@ def _finalize_wave(state: RunState, ops: Ops, request: str) -> RunState:
         return state
     op = state.ops[request]
     if op["status"] == al.STATUS_COMPLETED:
-        return _next_wave(state, ops)
+        return _next_wave(state, ops, lock=lock)
     _wave_pause(
         state,
         ops,
@@ -1099,7 +1109,9 @@ def _finalize_wave(state: RunState, ops: Ops, request: str) -> RunState:
     return state
 
 
-def reopen(run_id: str, node: str, ops: Ops, *, manual: bool = False) -> RunState:
+def reopen(
+    run_id: str, node: str, ops: Ops, *, manual: bool = False, lock: rl.RunLock
+) -> RunState:
     """`--reopen <node>` (S11, D2/D3): явное переоткрытие одобренного узла.
 
     `state.wave` = уровень узла + 1; ветка волны — НОВЫМ именем
@@ -1117,6 +1129,7 @@ def reopen(run_id: str, node: str, ops: Ops, *, manual: bool = False) -> RunStat
     закрытие), прогон после последней волны (op `merge` — переоткрытие
     через новый прогон). Грязное дерево — `stopped_dirty`.
     """
+    rl.require(lock, run_id)
     state = load(run_id)
     if _is_legacy_ledger(state):
         raise ValueError("--reopen — только для прогона с authoring=waves")
@@ -1197,7 +1210,7 @@ def reopen(run_id: str, node: str, ops: Ops, *, manual: bool = False) -> RunStat
         return state
     state.status = "running"
     save(state)
-    return advance(state, ops)
+    return advance(state, ops, lock=lock)
 
 
 def _verified_result_sha(state: RunState) -> str | None:
@@ -1224,7 +1237,7 @@ def _verified_result_sha(state: RunState) -> str | None:
     return str(commit) if commit else None
 
 
-def _next_wave(state: RunState, ops: Ops) -> RunState:
+def _next_wave(state: RunState, ops: Ops, *, lock: rl.RunLock) -> RunState:
     """Ровно один переход: следующая волна либо S8 после последней.
 
     После последней волны фиксируется op `merge` — та же метка «весь
@@ -1239,14 +1252,16 @@ def _next_wave(state: RunState, ops: Ops) -> RunState:
             op_complete(state, "merge", merged=True, waves=state.wave)
         state.status = "running"
         save(state)
-        return advance(state, ops)
+        return advance(state, ops, lock=lock)
     state.wave += 1
     state.status = "running"
     save(state)
-    return advance(state, ops)
+    return advance(state, ops, lock=lock)
 
 
-def _reconcile_pr_merged_out_of_band(state: RunState, ops: Ops) -> bool:
+def _reconcile_pr_merged_out_of_band(
+    state: RunState, ops: Ops, *, lock: rl.RunLock
+) -> bool:
     """PR уже ``MERGED`` — человек смержил напрямую, минуя раннер.
 
     Общая реконсиляция для ``waiting_human_merge`` и для любого
@@ -1311,7 +1326,7 @@ def _reconcile_pr_merged_out_of_band(state: RunState, ops: Ops) -> bool:
         op_complete(state, "merge", merged=True)
     state.status = "running"
     save(state)
-    advance(state, ops)
+    advance(state, ops, lock=lock)
     return True
 
 
@@ -1354,7 +1369,9 @@ def _next_verify_run_id(parent_run_id: str) -> str:
     return f"{parent_run_id}-v{attempt}"
 
 
-def verify(parent_run_id: str, ops: Ops, run_id: str | None = None) -> RunState:
+def verify(
+    parent_run_id: str, ops: Ops, run_id: str | None = None, *, lock: rl.RunLock
+) -> RunState:
     """Дочерний verification-run для `merged_unverified`-родителя (спека §5).
 
     Новый ``RunState`` с теми же координатами (repo/ws_id/target_dir/
@@ -1387,6 +1404,7 @@ def verify(parent_run_id: str, ops: Ops, run_id: str | None = None) -> RunState:
     валидируется как раньше (`_reserve_run_id` → `run_dir()` →
     `validate_id_component`).
     """
+    rl.require(lock, parent_run_id)
     parent = load(parent_run_id)
     if parent.status != "merged_unverified":
         raise ValueError(
@@ -1433,7 +1451,19 @@ def verify(parent_run_id: str, ops: Ops, run_id: str | None = None) -> RunState:
         )
     if run_id is None:
         run_id = _next_verify_run_id(parent_run_id)
+    # Потомок — НОВЫЙ прогон: его блокировку берёт сам `verify`, сразу после
+    # выбора id и до публикации леджера, и держит до конца S8 — иначе
+    # `resume(<child>)` из другого процесса исполнял бы тот же S8
+    # параллельно (ревью части A, A2). Блокировку родителя держит вход.
     _refuse_if_halted(parent.repo_slug)  # D2: последний отказ до резервирования
+    with rl.run_lock(run_id):
+        return _verify_child(parent, parent_run_id, run_id, ops)
+
+
+def _verify_child(
+    parent: RunState, parent_run_id: str, run_id: str, ops: Ops
+) -> RunState:
+    """Тело `verify` под блокировкой потомка: резерв, леджер, перенос мержа, S8."""
     _reserve_run_id(run_id)
     child = new_run(
         subject=parent.subject,
@@ -1816,7 +1846,9 @@ def _interview_reconcile_published(
     return True
 
 
-def attach_session(run_id: str, session_id: str, ops: Ops) -> RunState:
+def attach_session(
+    run_id: str, session_id: str, ops: Ops, *, lock: rl.RunLock
+) -> RunState:
     """§5.3: присоединение сироты — сессия, созданная без записи `session_id`.
 
     Допустимо только когда `interview-start` остался ``started`` и
@@ -1831,6 +1863,7 @@ def attach_session(run_id: str, session_id: str, ops: Ops) -> RunState:
     ``waiting_interview``, `interview-start` завершается с
     ``attached=True``.
     """
+    rl.require(lock, run_id)
     state = load(run_id)
     if state.interview is None:
         raise ValueError("у прогона нет стадии Need")
@@ -3958,7 +3991,6 @@ def _main(argv: list[str] | None = None) -> int:
 
     ops = RealOps()
     if args.command == "start":
-        bundle_dir = args.bundle_dir or f"workstreams/{args.ws_id}/spec"
         # Дефолтный run_id строится из ws_id — валидируем ws_id ДО генерации
         # (круг 12): битый ws_id иначе протащил бы `../`/`/` дальше в
         # автосгенерированный run_id (там его тоже поймает `run_dir()`, но
@@ -3966,7 +3998,26 @@ def _main(argv: list[str] | None = None) -> int:
         if args.run_id is None:
             validate_id_component(args.ws_id, label="ws_id")
         run_id = args.run_id or f"{args.ws_id}-{os.urandom(3).hex()}"
-        state = start(
+        lock_id = run_id
+    elif args.command == "verify":
+        lock_id = args.parent
+    else:
+        lock_id = args.run_id
+    try:
+        with rl.run_lock(lock_id) as lock:
+            state = _run_command(args, ops, lock)
+    except rl.LockBusy as exc:
+        print(f"runner: {exc}", file=sys.stderr)
+        return 1
+    _print_status(state)
+    return 0
+
+
+def _run_command(args: argparse.Namespace, ops: Ops, lock: rl.RunLock) -> RunState:
+    """Изменяющая подкоманда CLI под блокировкой точки входа (§11.4.5)."""
+    if args.command == "start":
+        bundle_dir = args.bundle_dir or f"workstreams/{args.ws_id}/spec"
+        return start(
             subject=args.subject,
             repo=args.repo,
             repo_slug=args.repo_slug,
@@ -3974,22 +4025,19 @@ def _main(argv: list[str] | None = None) -> int:
             target_dir=args.target_dir,
             bundle_dir=bundle_dir,
             profile=args.profile,
-            run_id=run_id,
+            run_id=lock.run_id,
             ops=ops,
             merge_authority=args.merge_authority,
             author_backend=args.author_backend,
             allow_legacy_dt=args.allow_legacy_dt,
             authoring=args.authoring,
+            lock=lock,
         )
-    elif args.command == "resume":
-        state = resume(args.run_id, ops)
-    elif args.command == "reopen":
-        state = reopen(args.run_id, args.node, ops, manual=args.manual)
-    else:
-        state = verify(args.parent, ops, args.run_id)
-
-    _print_status(state)
-    return 0
+    if args.command == "resume":
+        return resume(args.run_id, ops, lock=lock)
+    if args.command == "reopen":
+        return reopen(args.run_id, args.node, ops, manual=args.manual, lock=lock)
+    return verify(args.parent, ops, args.run_id, lock=lock)
 
 
 if __name__ == "__main__":
```

- [ ] **Step 3: Блокировка на входе spec-loop**

```diff
diff --git a/governance/spec_loop.py b/governance/spec_loop.py
index 3740e1d..05ade23 100644
--- a/governance/spec_loop.py
+++ b/governance/spec_loop.py
@@ -52,12 +52,14 @@
 from __future__ import annotations
 
 import argparse
+import contextlib
 import os
 import re
 import shlex
 import subprocess
 import sys
 import tomllib
+from collections.abc import Callable
 from dataclasses import dataclass
 from datetime import date
 from pathlib import Path
@@ -67,6 +69,7 @@ from governance import (
     brief_input,
     bundle_dag,
     charter_guard,
+    run_lock,
     runner,
     task_bridge,
 )
@@ -366,9 +369,14 @@ def recover_wave_run_from_github(
     requested_ws_id: str | None,
     requested_bundle_dir: str | None,
     ops,
+    acquire: Callable[[str], None],
 ) -> rs.RunState | None:
     """Восстановить леджер ВОЛНОВОГО прогона по candidate-PR (S13).
 
+    `acquire(run_id)` — блокировка точки входа (§11.4.5): берётся сразу,
+    как только `run_id` известен, до чтения существующего леджера и до
+    записи восстановленного.
+
     Бандл-PR у волнового прогона нет; durable-факты — PR веток заявок
     `(W, K, A)` и их финализирующих форм по шаблону `patterns.env`.
     Последняя волна = максимальный `(W, K, A)` среди MERGED candidate;
@@ -477,6 +485,7 @@ def recover_wave_run_from_github(
             "восстановите леджер вручную"
         )
     run_id = run_ids.pop()
+    acquire(run_id)
     ws_id, _w, last_step, _a = max(merged)
     if run_id in rs.all_run_ids():
         raw = (rs.run_dir(run_id) / "run.json").read_text(encoding="utf-8")
@@ -800,10 +809,10 @@ def _report_state(state: rs.RunState) -> int:
     return 1
 
 
-def _dispatch(state: rs.RunState, ops) -> int:
+def _dispatch(state: rs.RunState, ops, lock: run_lock.RunLock) -> int:
     """Действие по фактическому статусу найденного прогона."""
     if state.status == "waiting_human_merge":
-        after = runner.resume(state.run_id, ops)
+        after = runner.resume(state.run_id, ops, lock=lock)
         if after.status == "waiting_human_merge":
             print(_pause_message(after))
             return 0
@@ -818,7 +827,7 @@ def _dispatch(state: rs.RunState, ops) -> int:
             # диагностика orphan-состояния не должна зависеть от того,
             # дошёл ли вызов до runner.
             return _report_interview_stop(state)
-        after = runner.resume(state.run_id, ops)
+        after = runner.resume(state.run_id, ops, lock=lock)
         if after.status == "waiting_interview":
             return 0
         if after.status == "stopped_interview":
@@ -910,6 +919,25 @@ def main(argv: list[str] | None = None) -> int:
         )
         return 2
 
+    locks = contextlib.ExitStack()
+    held: dict[str, run_lock.RunLock] = {}
+
+    def acquire(run_id: str) -> None:
+        """Блокировка прогона на входе (§11.4.5); повторный вызов — no-op."""
+        if run_id not in held:
+            held[run_id] = locks.enter_context(run_lock.run_lock(run_id))
+
+    def hold(run_id: str) -> rs.RunState:
+        """Блокировка и свежее состояние под ней.
+
+        Поиск кандидатов (`find_runs`) читает леджеры без блокировки — иначе
+        не узнать, какие брать; КАЖДЫЙ найденный прогон затем блокируется и
+        перечитывается, и все решения по его статусу и координатам идут
+        только по этому перечитанному `run.json`.
+        """
+        acquire(run_id)
+        return rs.load(run_id)
+
     try:
         # Intake обязан завершиться до генерации run-id, GitHub-вызовов и
         # любых runner effects. В ledger уйдёт descriptor, не этот Path.
@@ -926,7 +954,7 @@ def main(argv: list[str] | None = None) -> int:
             brief_input.inspect_brief(Path(args.brief)) if args.brief else None
         )
         if args.run_id:
-            state = rs.load(args.run_id)
+            state = hold(args.run_id)
             if (state.repo, state.subject) != (args.repo, args.subject):
                 print(
                     f"spec-loop: run {args.run_id!r} несёт "
@@ -937,7 +965,7 @@ def main(argv: list[str] | None = None) -> int:
                 return 1
             matches: list[rs.RunState] = [state]
         else:
-            matches = find_runs(args.repo, args.subject)
+            matches = [hold(st.run_id) for st in find_runs(args.repo, args.subject)]
 
         if args.new_run:
             # --new-run отменяет гвард неоднозначности «--run-id» ниже:
@@ -1017,6 +1045,7 @@ def main(argv: list[str] | None = None) -> int:
                 requested_ws_id=args.ws_id,
                 requested_bundle_dir=args.bundle_dir,
                 ops=ops,
+                acquire=acquire,
             )
             if state is None:
                 # Волнового кандидата нет. Если найдётся бандл-PR прежнего
@@ -1034,6 +1063,8 @@ def main(argv: list[str] | None = None) -> int:
                     ops=ops,
                 )
             recovered = state is not None
+            if state is not None:
+                state = hold(state.run_id)
         if recovered and supplied_brief is not None:
             raise SpecLoopError(
                 "восстановленный из GitHub прогон не принимает --brief "
@@ -1074,7 +1105,9 @@ def main(argv: list[str] | None = None) -> int:
                     "прогону, а прогон с этими (repo, subject) не найден"
                 )
             try:
-                state = runner.attach_session(state.run_id, args.session, ops)
+                state = runner.attach_session(
+                    state.run_id, args.session, ops, lock=held[state.run_id]
+                )
             except ValueError as exc:
                 raise SpecLoopError(str(exc)) from exc
 
@@ -1103,7 +1136,7 @@ def main(argv: list[str] | None = None) -> int:
                 "действие": f"продолжение ({state.status})",
             }
             _print_values(values)
-            return _dispatch(state, ops)
+            return _dispatch(state, ops, held[state.run_id])
         ws_id = args.ws_id or ws_id_for(args.subject, date.today())
         rs.validate_id_component(ws_id, label="ws_id")
         collisions = []
@@ -1146,6 +1179,7 @@ def main(argv: list[str] | None = None) -> int:
             "действие": "start (новый прогон)",
         }
         _print_values(values)
+        acquire(run_id)
         started = runner.start(
             subject=args.subject,
             repo=args.repo,
@@ -1163,6 +1197,7 @@ def main(argv: list[str] | None = None) -> int:
             authoring="legacy" if args.legacy else "waves",
             code=args.code,
             plan_item=args.plan_item,
+            lock=held[run_id],
         )
         print(f"статус прогона: {started.status}")
         if started.status == "waiting_interview":
@@ -1185,6 +1220,11 @@ def main(argv: list[str] | None = None) -> int:
     except (SpecLoopError, brief_input.BriefInputError, FileNotFoundError) as exc:
         print(f"spec-loop: {exc}")
         return 1
+    except run_lock.LockBusy as exc:
+        print(f"spec-loop: {exc}")
+        return 1
+    finally:
+        locks.close()
 
 
 if __name__ == "__main__":
```

- [ ] **Step 4: Хелпер тестов и механическая правка вызовов**

```python
"""Вызовы раннера под блокировкой прогона — как у точек входа (§11.4.5).

Функции раннера требуют токен `RunLock`; тесты, которые зовут их напрямую,
берут блокировку здесь, ровно как spec-loop и CLI раннера.
"""

from __future__ import annotations

from typing import Any

from governance import run_lock as rl
from governance import runner


def start(**kwargs: Any) -> Any:
    with rl.run_lock(kwargs["run_id"]) as lock:
        return runner.start(**kwargs, lock=lock)


def resume(run_id: str, ops: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.resume(run_id, ops, lock=lock)


def advance(state: Any, ops: Any) -> Any:
    with rl.run_lock(state.run_id) as lock:
        return runner.advance(state, ops, lock=lock)


def verify(parent_run_id: str, ops: Any, run_id: str | None = None) -> Any:
    with rl.run_lock(parent_run_id) as lock:
        return runner.verify(parent_run_id, ops, run_id, lock=lock)


def reopen(run_id: str, node: str, ops: Any, **kwargs: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.reopen(run_id, node, ops, lock=lock, **kwargs)


def attach_session(run_id: str, session_id: str, ops: Any) -> Any:
    with rl.run_lock(run_id) as lock:
        return runner.attach_session(run_id, session_id, ops, lock=lock)
```

Вызовы `runner.{start,resume,advance,verify,reopen,attach_session}(` в тест-модулях
заменяются на `locked_runner.<fn>(` (строки с обратными кавычками и комментарии не
трогаются), в модуль добавляется `from tests import locked_runner`:

```bash
python3 - <<'PY'
import re, pathlib
pat = re.compile(r"\brunner\.(start|resume|advance|verify|reopen|attach_session)\(")
for f in sorted(pathlib.Path("tests").glob("test_*.py")):
    lines = f.read_text(encoding="utf-8").split("\n")
    n = 0
    for i, line in enumerate(lines):
        if pat.search(line) and "`" not in line and not line.lstrip().startswith("#"):
            lines[i], k = pat.subn(lambda m: f"locked_runner.{m.group(1)}(", line)
            n += k
    if n:
        idx = max(i for i, l in enumerate(lines)
                  if l.startswith(("import ", "from ")) and "__future__" not in l
                  and i < next(j for j, l2 in enumerate(lines) if l2.startswith(("def ", "class ", "@"))))
        lines.insert(idx + 1, "from tests import locked_runner")
        f.write_text("\n".join(lines), encoding="utf-8")
        print(f, n)
PY
uv run --frozen --group selfcheck ruff check --fix tests && uv run --frozen --group selfcheck ruff format tests
```

Проверено: 183 вызова в пяти модулях. Хелперы живут в отдельном модуле — правка не
задевает их тела (рекурсии нет). Фейки, подменяющие функции раннера в
`tests/test_governance_spec_loop.py`, принимают `*, lock` и проверяют, что это
`RunLock` нужного прогона; прямой вызов `runner._finalize_wave` в
`tests/test_governance_approve_node.py` обёрнут в `run_lock.run_lock(...)`:

В `tests/test_governance_runner.py` — `run_lock` в блок `from governance import (…)` и
тест удержания блокировки потомка в `verify` (конкурент пытается войти в S8 потомка
РОВНО один раз — иначе рекурсия маскирует отсутствие блокировки; проверено мутацией):

```python
def test_verify_holds_child_lock_through_s8(  # ревью части A, A2
    tmp_path: Path, runs_root, monkeypatch
) -> None:
    ops = FakeOps(
        review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES, s8_exit=1
    )
    parent_id = "r-s8-lockp"
    parent = _wave_run_at_s8(tmp_path, parent_id, ops)
    runner._step_s8(parent, ops)
    assert parent.status == "merged_unverified"
    ops.s8_exit = 0
    seen: list[str] = []
    real_s8 = runner._step_s8

    def s8_with_competitor(state, ops_):
        # Конкурент пытается войти РОВНО один раз; если он вошёл (блокировки
        # потомка нет), он сам исполнит S8 — и это видно по `seen`.
        if state.remediated_by and not seen:
            seen.append("tried")
            try:
                locked_runner.resume(state.run_id, ops_)
            except run_lock.LockBusy:
                seen.append("busy")
            else:
                seen.append("entered")
        return real_s8(state, ops_)

    monkeypatch.setattr(runner, "_step_s8", s8_with_competitor)
    child = locked_runner.verify(parent_id, ops, "r-s8-lockc")
    assert seen == ["tried", "busy"]  # конкурент не вошёл в S8 потомка
    assert child.status == "completed"
```

Тесты путей `spec_loop.main` (A1) и фейки:

```diff
diff --git a/tests/test_governance_spec_loop.py b/tests/test_governance_spec_loop.py
index df82b4c..3bac3cc 100644
--- a/tests/test_governance_spec_loop.py
+++ b/tests/test_governance_spec_loop.py
@@ -13,7 +13,7 @@ from pathlib import Path
 
 import pytest
 
-from governance import brief_input, spec_loop
+from governance import brief_input, run_lock, spec_loop
 from governance import interview as iv
 from governance import run_state as rs
 
@@ -478,6 +478,7 @@ class _LoopEnv:
         monkeypatch.setattr(spec_loop, "_real_ops", _NoRemoteRuns)
 
     def _start(self, **kwargs):
+        assert isinstance(kwargs.pop("lock"), run_lock.RunLock)
         self.calls.append(("start", kwargs))
         interview = (
             kwargs["interview_spec"].as_state()
@@ -511,7 +512,8 @@ class _LoopEnv:
                 state.interview["session_id"] = session_id
         return state
 
-    def _resume(self, run_id, ops):
+    def _resume(self, run_id, ops, *, lock):
+        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
         self.calls.append(("resume", run_id))
         return self.resume_result
 
@@ -1089,7 +1091,8 @@ def test_session_attach_calls_attach_then_resume(
     st = _make_need_run(env, status="stopped_interview", session=None)
     attached = []
 
-    def _attach(run_id, session_id, ops):
+    def _attach(run_id, session_id, ops, *, lock):
+        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
         attached.append((run_id, session_id))
         st.interview["session_id"] = session_id
         st.status = "waiting_interview"
@@ -1319,7 +1322,8 @@ def test_historical_bundle_pr_does_not_block_wave_recovery(
     _wave_recovery_env(tmp_path, monkeypatch, ops)
     seen: list[str] = []
 
-    def _resume(run_id, passed_ops):
+    def _resume(run_id, passed_ops, *, lock):
+        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
         state = rs.load(run_id)
         seen.append(run_id)
         assert state.authoring == "waves", "восстановлен волновой прогон, а не прежний"
@@ -1351,7 +1355,8 @@ def test_missing_ledger_recovers_wave_run_from_candidate_prs(
     _wave_recovery_env(tmp_path, monkeypatch, ops)
     seen: list[str] = []
 
-    def _resume(run_id, passed_ops):
+    def _resume(run_id, passed_ops, *, lock):
+        assert isinstance(lock, run_lock.RunLock) and lock.run_id == run_id
         state = rs.load(run_id)
         seen.append(run_id)
         assert state.authoring == "waves" and state.wave == 2
@@ -1560,3 +1565,67 @@ def test_bad_code_or_plan_item_refuses_before_runner(
     rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", *extra])
     assert rc == 1
     assert env.calls == []
+
+
+# --- блокировка на входе: настоящие пути main (ревью части A, A1) ---
+
+
+def _spy_loads(monkeypatch) -> list[str]:
+    real = rs.load
+    seen: list[str] = []
+
+    def spy(run_id: str) -> rs.RunState:
+        seen.append(run_id)
+        return real(run_id)
+
+    monkeypatch.setattr(rs, "load", spy)
+    return seen
+
+
+def test_run_id_path_locks_before_first_load(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _make_need_run(env)
+    loads = _spy_loads(monkeypatch)
+    with run_lock.run_lock("r-a"):
+        assert spec_loop.main(_need("--run-id", "r-a")) == 1
+    assert loads == [] and env.calls == []
+    assert "другим процессом" in capsys.readouterr().out
+
+
+def test_search_path_decides_only_after_locking_every_match(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _make_need_run(env, status="stopped_interview")
+    _make_need_run(env, run_id_suffix="2", status="waiting_interview")
+    with run_lock.run_lock("r-a2"):  # второй совпавший занят другим процессом
+        assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
+    assert env.calls == []  # решение «все до S1» не принято без блокировки
+    assert "другим процессом" in capsys.readouterr().out
+
+
+def test_new_run_check_uses_state_reread_under_lock(
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    st = _make_need_run(env, status="stopped_interview")
+    stale = rs.load("r-a")
+    st.status = "waiting_human_merge"  # другой процесс успел дойти до S1
+    st.branch = "spec/ws-a-behaviour"
+    rs.save(st)
+    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [stale])
+    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
+    assert env.calls == []
+
+
+def test_wave_recovery_locks_before_reading_or_writing_ledger(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    ops = _WaveRecoveryOps([_wave_pr(10, 1, 0, 1), _wave_pr(11, 1, 0, 1, final=True)])
+    _wave_recovery_env(tmp_path, monkeypatch, ops)
+    with run_lock.run_lock("fleet-inbox-20260901-a1b2c3"):
+        assert spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"]) == 1
+    assert rs.all_run_ids() == []  # леджер не записан мимо блокировки
+    assert "другим процессом" in capsys.readouterr().out
```

```diff
diff --git a/tests/test_governance_approve_node.py b/tests/test_governance_approve_node.py
index 956878e..5604a6e 100644
--- a/tests/test_governance_approve_node.py
+++ b/tests/test_governance_approve_node.py
@@ -26,13 +26,14 @@ import pytest
 from governance import approval_facts as af
 from governance import approval_ledger as al
 from governance import approve_node as an
-from governance import bundle_dag, bundle_inputs
+from governance import bundle_dag, bundle_inputs, run_lock
 from governance import merge_gate as mg
 from governance import node_approval as na
 from governance import run_state as rs
 from governance.facts import Fact, Outcome, unavailable
 from governance.frontmatter import join_frontmatter, split_frontmatter
 from governance.ops import RealOps
+from tests import locked_runner
 
 BUNDLE = "spec"
 WS_ID = "WS-T1"
@@ -3666,7 +3667,8 @@ def test_wave_finalize_resumes_from_await_finalize_merge(
     assert al.next_step(op) is al.Step.AWAIT_FINALIZE_MERGE
     finalize_pr = op["finalize_pr"]
     # resume волны: аттестация публикует одобрение, повтор мержит агентом.
-    resumed = runner._finalize_wave(w.state, w.ops, key)
+    with run_lock.run_lock(w.state.run_id) as lock:
+        resumed = runner._finalize_wave(w.state, w.ops, key, lock=lock)
     assert w.forge.review_calls == [finalize_pr]
     assert w.state.ops[key]["status"] == al.STATUS_COMPLETED
     assert resumed.wave == 2, "переход к следующей волне — ровно один"
@@ -3681,14 +3683,13 @@ def test_reopen_makes_a_new_branch_name_and_stale_is_reapproved_by_levels(
     `reopen` создаёт `…-w2-r1` от base без файла узла и её push не даёт
     non-fast-forward; candidate над новым текстом ставит `stale`
     behaviour-spec; `stale_below_top_level` ведёт переодобрение."""
-    from governance import runner
 
     w = waves_world
     for wave, files in enumerate(_WAVE_FILES[:3], 1):
         drive_wave(w, wave, files)
     assert w.forge.head_of("spec/WS-T1-behaviour-w2") is not None
 
-    stopped = runner.reopen(w.state.run_id, "requirements", w.ops, manual=True)
+    stopped = locked_runner.reopen(w.state.run_id, "requirements", w.ops, manual=True)
     assert stopped.status == "stopped_author" and stopped.wave == 2
     assert stopped.branch == "spec/WS-T1-behaviour-w2-r1"
     assert _git(w.target, "rev-parse", "--abbrev-ref", "HEAD") == stopped.branch
```

- [ ] **Step 5: Тесты блокировки** (T37 — второй вход spec-loop и CLI `runner resume` при
занятой блокировке: код 1, `run.json` не читался; T37a, T37b, наследование через `pass_fds`)

```python
"""Блокировка прогона (§11.4.5): одна на вход, токен обязателен, наследуется соседом."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from governance import run_lock, runner, spec_loop
from governance import run_state as rs
from tests import locked_runner


@pytest.fixture()
def runs_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "runs"
    monkeypatch.setattr(rs, "RUNS_ROOT", root)
    return root


def test_second_lock_on_same_run_is_busy(runs_root) -> None:
    with run_lock.run_lock("r-1"):
        with pytest.raises(run_lock.LockBusy):
            with run_lock.run_lock("r-1"):
                pass
    with run_lock.run_lock("r-1"):  # отпущена — снова свободна
        pass


def test_other_run_is_independent(runs_root) -> None:
    with run_lock.run_lock("r-1"), run_lock.run_lock("r-2"):
        pass


def test_lock_file_lives_outside_runs_root(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        assert not (runs_root / "r-1").exists()
        assert run_lock.lock_path("r-1").parent.name == "runs-locks"
        assert lock.run_id == "r-1"


def test_require_checks_token_and_run(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        run_lock.require(lock, "r-1")
        with pytest.raises(ValueError):
            run_lock.require(lock, "r-2")
    with pytest.raises(TypeError):
        run_lock.require(None, "r-1")  # type: ignore[arg-type]


def test_runner_functions_refuse_without_token(runs_root) -> None:  # T37a
    with pytest.raises(TypeError):
        runner.resume("r-1", object())  # type: ignore[call-arg]
    with run_lock.run_lock("r-2") as other:
        with pytest.raises(ValueError):
            runner.resume("r-1", object(), lock=other)  # type: ignore[arg-type]


_HOLDER = """
import sys, time, subprocess
from pathlib import Path
from governance import run_lock, run_state as rs
rs.RUNS_ROOT = Path(sys.argv[1])
with run_lock.run_lock("r-1") as lock:
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; print('ready', flush=True); time.sleep(30)"],
        pass_fds=(lock.fd,), stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "ready"
    print(child.pid, flush=True)
    time.sleep(30)
"""


def test_lock_survives_parent_death_via_pass_fds(runs_root, tmp_path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(runs_root)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert holder.stdout is not None
    child_pid = int(holder.stdout.readline().strip())
    holder.kill()
    holder.wait()
    try:
        with pytest.raises(run_lock.LockBusy):  # ребёнок унаследовал описание
            with run_lock.run_lock("r-1"):
                pass
    finally:
        os.kill(child_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while True:
        try:
            with run_lock.run_lock("r-1"):
                break
        except run_lock.LockBusy:
            assert time.monotonic() < deadline, "блокировка не отпущена"
            time.sleep(0.05)


def test_other_children_do_not_inherit(runs_root) -> None:
    with run_lock.run_lock("r-1") as lock:
        done = subprocess.run(
            [sys.executable, "-c", f"import os; os.fstat({lock.fd})"],
            capture_output=True,
            check=False,
        )
    assert done.returncode != 0  # без pass_fds дескриптор у потомка закрыт


def _waiting_run(tmp_path: Path) -> str:
    from tests.test_governance_runner import FakeOps, _need_spec, _reply, _start_kwargs

    ops = FakeOps(discovery=[("start", _reply(20))])
    locked_runner.start(
        **_start_kwargs(tmp_path, "r-busy", ops), interview_spec=_need_spec()
    )
    return "r-busy"


def test_second_entry_exits_before_reading_state(  # T37, Review Focus
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    run_id = _waiting_run(tmp_path)
    state = rs.load(run_id)
    real_load = rs.load
    loads: list[str] = []

    def spy(rid: str) -> rs.RunState:
        loads.append(rid)
        return real_load(rid)

    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [state])
    monkeypatch.setattr(rs, "load", spy)
    monkeypatch.setattr(spec_loop, "build_interview_spec", lambda a, s: None)
    monkeypatch.setattr(
        spec_loop,
        "manifest_repo_entry",
        lambda text, repo: type(
            "E", (), {"repo_slug": "owner/alpha", "repo": "alpha"}
        )(),
    )
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "m.toml")
    (tmp_path / "m.toml").write_text("", encoding="utf-8")
    with run_lock.run_lock(run_id):
        code = spec_loop.main(["--subject", state.subject, "--repo", "alpha"])
    assert code == 1
    assert "другим процессом" in capsys.readouterr().out
    assert loads == []  # под чужой блокировкой run.json не читался


def test_runner_cli_resume_exits_before_reading_state(  # T37 (CLI runner resume)
    tmp_path, runs_root, monkeypatch, capsys
) -> None:
    run_id = _waiting_run(tmp_path)
    before = (rs.run_dir(run_id) / "run.json").read_bytes()

    def no_load(rid: str) -> rs.RunState:
        raise AssertionError("run.json прочитан при занятой блокировке")

    monkeypatch.setattr(rs, "load", no_load)
    monkeypatch.setattr(runner, "load", no_load)
    with run_lock.run_lock(run_id):
        assert runner.main(["resume", "--run-id", run_id]) == 1
    assert "другим процессом" in capsys.readouterr().err
    assert (rs.run_dir(run_id) / "run.json").read_bytes() == before


def test_state_is_reread_under_lock(tmp_path, runs_root, monkeypatch) -> None:  # T37b
    run_id = _waiting_run(tmp_path)
    stale = rs.load(run_id)
    fresh = rs.load(run_id)
    fresh.status = "stopped_interview"
    rs.save(fresh)
    seen: list[str] = []
    monkeypatch.setattr(spec_loop, "find_runs", lambda repo, subject: [stale])
    monkeypatch.setattr(spec_loop, "build_interview_spec", lambda a, s: None)
    monkeypatch.setattr(
        spec_loop,
        "manifest_repo_entry",
        lambda text, repo: type(
            "E", (), {"repo_slug": "owner/alpha", "repo": "alpha"}
        )(),
    )
    monkeypatch.setattr(spec_loop, "MANIFEST_PATH", tmp_path / "m.toml")
    (tmp_path / "m.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        spec_loop, "_origin_url", lambda d: "git@github.com:owner/alpha.git"
    )
    monkeypatch.setattr(spec_loop, "_real_ops", lambda: object())
    monkeypatch.setattr(
        spec_loop, "_dispatch", lambda st, ops, lock: seen.append(st.status) or 0
    )
    assert spec_loop.main(["--subject", stale.subject, "--repo", "alpha"]) == 0
    assert seen == ["stopped_interview"]


# --- гарантии файла блокировки (§11.4.5, уточнение ревизии 7) ---


def test_lock_file_is_not_removed_and_keeps_its_inode(runs_root) -> None:
    with run_lock.run_lock("r-1"):
        inode = run_lock.lock_path("r-1").stat().st_ino
    assert run_lock.lock_path("r-1").exists()  # освобождение файл не удаляет
    with run_lock.run_lock("r-1"):
        assert run_lock.lock_path("r-1").stat().st_ino == inode


def test_crash_between_lock_and_reservation_leaves_no_ledger(runs_root) -> None:
    with pytest.raises(RuntimeError):
        with run_lock.run_lock("r-crash"):
            raise RuntimeError("гибель до _reserve_run_id")
    assert rs.all_run_ids() == []  # каталога прогона нет — битого леджера нет
    assert spec_loop.find_runs("alpha", "s") == []
    with run_lock.run_lock("r-crash") as lock:  # повторный запуск проходит
        assert lock.run_id == "r-crash"


def test_every_entry_uses_the_one_lock_path(runs_root, monkeypatch, tmp_path) -> None:
    opened: list[str] = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(run_lock.os, "open", spy)
    with run_lock.run_lock("r-path"):
        pass
    assert opened == [str(run_lock.lock_path("r-path"))]
    for module in (spec_loop, runner):
        assert "lock_path" not in vars(module)  # своих путей у входов нет
```

- [ ] **Step 6: Run — PASS** — `uv run --frozen --group governance pytest tests/test_run_lock.py -q` → 13 passed (мутация «CLI раннера читает `run.json` до блокировки» поймана); тесты путей `main` и `verify` — в
`tests/test_governance_spec_loop.py` и `tests/test_governance_runner.py` (диффы выше);
мутационно проверены: снятие блокировки на пути `--run-id`, на поиске, в
восстановлении и в `verify` — каждый раз падает тест; полный набор — журнал.

- [ ] **Step 7: Commit** — `feat(need): блокировка прогона на входе, токен RunLock у раннера (§11.4.5)`.

---

### Task 6: Порт discovery — `session_id`, `lock_fd`, `approve`

**Files:**
- Modify: `governance/ops.py` — Protocol и `RealOps`: `_discovery` (`pass_fds`),
  `discovery_start`, `discovery_status`, `discovery_brief`, новый `discovery_approve`
- Modify: `tests/test_governance_runner.py` — сигнатуры `FakeOps.discovery_*`
- Test: `tests/test_governance_ops.py` (тест прежнего отказа порта на `upstream_path`
  заменён)

**Interfaces:**
- Produces:

```python
def discovery_start(self, frame: str, target: str, traces_to: str | None,
                    upstream_path: str | None, cwd: str, *,
                    session_id: str | None = None, lock_fd: int | None = None) -> DiscoveryReply
def discovery_status(self, session_id: str, cwd: str, *, lock_fd: int | None = None) -> DiscoveryReply
def discovery_brief(self, session_id: str, out_path: str, cwd: str, *, lock_fd: int | None = None) -> DiscoveryReply
def discovery_approve(self, brief_path: str, repo: str, pr: int, path: str, cwd: str, *,
                      lock_fd: int | None = None) -> DiscoveryReply
```

Набор параметров — спека §11.4.6; `session_id` и `lock_fd` — keyword-only с
умолчанием `None`: существующие вызовы (customer-путь раннера, smoke, тесты) остаются
корректными, ни одна промежуточная задача не оставляет набор красным. Константа
`ENGINEER_BLOCKED` остаётся до части B (её ещё читают `spec_loop` и `runner`): эта
задача снимает только отказ ПОРТА на непустом `upstream_path` — сам engineer-маршрут
по-прежнему отказывает в preflight spec-loop до задачи 8 части B.

- [ ] **Step 1: Тесты** (T1–T3, argv `approve`, `pass_fds`) — заменяют
`test_discovery_start_refuses_upstream_until_inbox`:

```diff
diff --git a/tests/test_governance_ops.py b/tests/test_governance_ops.py
index 05b91eb..10a024d 100644
--- a/tests/test_governance_ops.py
+++ b/tests/test_governance_ops.py
@@ -2289,17 +2289,97 @@ def test_discovery_start_engineer_traces_to_argv(monkeypatch, tmp_path):
     assert seen[0]["argv"][-2:] == ["--traces-to", "customer.md"]
 
 
-def test_discovery_start_refuses_upstream_until_inbox(monkeypatch, tmp_path):
+def test_discovery_start_engineer_upstream_and_session_id(monkeypatch, tmp_path):
+    """T1 (§11.4.6): upstream уходит `--upstream`, без `--traces-to`; id — write-ahead."""
+    seen = _capture(monkeypatch)
+    RealOps().discovery_start(
+        "engineer",
+        "o/alpha",
+        None,
+        str(tmp_path / "upstream.md"),
+        str(tmp_path),
+        session_id="s-r-e",
+        lock_fd=7,
+    )
+    argv = seen[0]["argv"]
+    assert argv[argv.index("start") :] == [
+        "start",
+        "--frame",
+        "engineer",
+        "--target",
+        "o/alpha",
+        "--upstream",
+        str(tmp_path / "upstream.md"),
+        "--session-id",
+        "s-r-e",
+    ]
+    assert "--traces-to" not in argv
+    assert seen[0]["pass_fds"] == (7,)
+
+
+def test_discovery_without_lock_fd_passes_no_fds(monkeypatch, tmp_path):
+    seen = _capture(monkeypatch)
+    RealOps().discovery_start("customer", "o/alpha", None, None, str(tmp_path))
+    assert seen[0]["pass_fds"] == ()
+
+
+def test_discovery_start_upstream_with_traces_to_is_synthetic_1(monkeypatch, tmp_path):
+    """T2: заданы оба — синтетический 1 ДО subprocess (инвариант порта)."""
     seen = _capture(monkeypatch)
     reply = RealOps().discovery_start(
         "engineer",
         "o/alpha",
         "customer.md",
-        str(tmp_path / "customer.md"),
+        str(tmp_path / "upstream.md"),
         str(tmp_path),
     )
-    assert seen == []  # сосед не вызван
-    assert reply.code == 1 and "discovery#49" in reply.envelope["operation"]["reason"]
+    assert seen == []
+    assert reply.code == 1 and "--upstream" in reply.envelope["operation"]["reason"]
+
+
+@pytest.mark.parametrize(
+    ("code", "stdout"),
+    [(20, None), (0, "not json"), (2, "")],
+    ids=["approve-20", "not-json", "no-envelope"],
+)
+def test_discovery_approve_impossible_forms_are_synthetic_1(
+    monkeypatch, tmp_path, code, stdout
+):
+    """T3: 20 у approve — невозможная форма; без envelope — граница."""
+    _capture(monkeypatch, returncode=code, stdout=stdout)
+    reply = RealOps().discovery_approve(
+        str(tmp_path / "b.md"), "o/alpha", 7, "d/brief.md", str(tmp_path)
+    )
+    assert reply.code == 1
+
+
+def test_discovery_approve_argv_and_refusal_code(monkeypatch, tmp_path):
+    envelope = {
+        "lifecycle": "complete",
+        "gate": "pass",
+        "readiness": "ready",
+        "next_action": {},
+        "findings": [],
+        "readiness_findings": [],
+        "operation": {"status": "refused", "reason": "brief_bytes_diverged"},
+    }
+    seen = _capture(monkeypatch, returncode=2, stdout=json.dumps(envelope))
+    reply = RealOps().discovery_approve(
+        str(tmp_path / "b.md"), "o/alpha", 7, "d/brief.md", str(tmp_path), lock_fd=5
+    )
+    argv = seen[0]["argv"]
+    assert argv[argv.index("approve") :] == [
+        "approve",
+        str(tmp_path / "b.md"),
+        "--repo",
+        "o/alpha",
+        "--pr",
+        "7",
+        "--path",
+        "d/brief.md",
+    ]
+    assert seen[0]["pass_fds"] == (5,)
+    assert reply.code == 2
 
 
 def test_discovery_status_and_brief_argv(monkeypatch, tmp_path):
```

- [ ] **Step 2: Run — FAIL** (`TypeError: discovery_start() got an unexpected keyword argument 'session_id'`).

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/ops.py b/governance/ops.py
index e6738fc..5a4a69e 100644
--- a/governance/ops.py
+++ b/governance/ops.py
@@ -278,14 +278,28 @@ class Ops(Protocol):
         traces_to: str | None,
         upstream_path: str | None,
         cwd: str,
+        *,
+        session_id: str | None = None,
+        lock_fd: int | None = None,
     ) -> _interview.DiscoveryReply: ...
 
     def discovery_status(
-        self, session_id: str, cwd: str
+        self, session_id: str, cwd: str, *, lock_fd: int | None = None
     ) -> _interview.DiscoveryReply: ...
 
     def discovery_brief(
-        self, session_id: str, out_path: str, cwd: str
+        self, session_id: str, out_path: str, cwd: str, *, lock_fd: int | None = None
+    ) -> _interview.DiscoveryReply: ...
+
+    def discovery_approve(
+        self,
+        brief_path: str,
+        repo: str,
+        pr: int,
+        path: str,
+        cwd: str,
+        *,
+        lock_fd: int | None = None,
     ) -> _interview.DiscoveryReply: ...
 
     def commit_paths(
@@ -2244,12 +2258,16 @@ class RealOps(BriefFactsMixin):
         claude = any(_harness_for(p)[0] == "claude" for p in ("AUTHOR", "REVIEW"))
         return _run_agent(argv, target_dir, claude=claude)
 
-    def _discovery(self, args: list[str], cwd: str) -> _interview.DiscoveryReply:
+    def _discovery(
+        self, args: list[str], cwd: str, lock_fd: int | None = None
+    ) -> _interview.DiscoveryReply:
         """Один вызов discovery CLI соседа + проверка границы (спека §6).
 
         `--frozen --project`: тот же способ, что у disputatio (`author_disp`).
         stdout захватывается целиком — envelope один на вызов; stderr
-        сохраняется для диагностики, но в контракт не входит.
+        сохраняется для диагностики, но в контракт не входит. `lock_fd` —
+        дескриптор блокировки прогона (§11.4.5): передаётся соседу
+        (`pass_fds`), и блокировка держится, пока жив его процесс.
         """
         argv = [
             "uv",
@@ -2261,7 +2279,12 @@ class RealOps(BriefFactsMixin):
             *args,
         ]
         done = subprocess.run(
-            argv, cwd=cwd, capture_output=True, text=True, check=False
+            argv,
+            cwd=cwd,
+            capture_output=True,
+            text=True,
+            check=False,
+            pass_fds=(lock_fd,) if lock_fd is not None else (),
         )
         return _interview.parse_reply(done.returncode, done.stdout, done.stderr)
 
@@ -2272,28 +2295,70 @@ class RealOps(BriefFactsMixin):
         traces_to: str | None,
         upstream_path: str | None,
         cwd: str,
+        *,
+        session_id: str | None = None,
+        lock_fd: int | None = None,
     ) -> _interview.DiscoveryReply:
-        """`discovery start`. `upstream_path` — durable-копия из run_dir; до
-        discovery#49 сосед upstream не принимает — отказ ДО вызова, тем же
-        текстом, что preflight spec-loop (порт после разблокировки не меняется)."""
-        if upstream_path is not None:
+        """`discovery start` (§11.4.6). С `upstream_path` (durable-копия из
+        run_dir) — `--upstream` и НИКАКОГО `--traces-to`: сосед сам ставит
+        `upstream.md` первым в `traces_to` и отказывает на дубле. Заданы оба —
+        синтетический 1 до subprocess (инвариант порта)."""
+        if upstream_path is not None and traces_to is not None:
             return _interview.DiscoveryReply(
-                1, _interview.synthetic_envelope(ENGINEER_BLOCKED), ""
+                1,
+                _interview.synthetic_envelope(
+                    "upstream_path и traces_to вместе — upstream уходит только "
+                    "через --upstream"
+                ),
+                "",
             )
         args = ["start", "--frame", frame, "--target", target]
-        if traces_to:
+        if upstream_path is not None:
+            args += ["--upstream", upstream_path]
+        elif traces_to:
             args += ["--traces-to", traces_to]
-        return self._discovery(args, cwd)
+        if session_id is not None:
+            args += ["--session-id", session_id]
+        return self._discovery(args, cwd, lock_fd)
 
-    def discovery_status(self, session_id: str, cwd: str) -> _interview.DiscoveryReply:
-        return self._discovery(["status", "--session", session_id], cwd)
+    def discovery_status(
+        self, session_id: str, cwd: str, *, lock_fd: int | None = None
+    ) -> _interview.DiscoveryReply:
+        return self._discovery(["status", "--session", session_id], cwd, lock_fd)
 
     def discovery_brief(
-        self, session_id: str, out_path: str, cwd: str
+        self, session_id: str, out_path: str, cwd: str, *, lock_fd: int | None = None
     ) -> _interview.DiscoveryReply:
         return self._discovery(
-            ["brief", "--session", session_id, "--out", out_path], cwd
+            ["brief", "--session", session_id, "--out", out_path], cwd, lock_fd
+        )
+
+    def discovery_approve(
+        self,
+        brief_path: str,
+        repo: str,
+        pr: int,
+        path: str,
+        cwd: str,
+        *,
+        lock_fd: int | None = None,
+    ) -> _interview.DiscoveryReply:
+        """`discovery approve` (§11.5): зеркало человеческого мержа в конверт.
+
+        Код 20 у `approve` — невозможная форма (интервью не идёт) ⇒
+        синтетический 1; код без envelope ловит `parse_reply`."""
+        reply = self._discovery(
+            ["approve", brief_path, "--repo", repo, "--pr", str(pr), "--path", path],
+            cwd,
+            lock_fd,
         )
+        if reply.code == 20:
+            return _interview.DiscoveryReply(
+                1,
+                _interview.synthetic_envelope("approve вернул 20 — невозможная форма"),
+                reply.stderr,
+            )
+        return reply
 
     @staticmethod
     def _ignored_files(target_dir: str, paths: list[str]) -> list[str]:
```

`FakeOps` раннера принимает новые keyword-аргументы (журнал вызовов не меняется):

```diff
diff --git a/tests/test_governance_runner.py b/tests/test_governance_runner.py
index c2153e5..12a8caa 100644
--- a/tests/test_governance_runner.py
+++ b/tests/test_governance_runner.py
@@ -564,15 +564,25 @@ class FakeOps:
         )
         return self.discovery.pop(0)[1]
 
-    def discovery_start(self, frame, target, traces_to, upstream_path, cwd):
+    def discovery_start(
+        self,
+        frame,
+        target,
+        traces_to,
+        upstream_path,
+        cwd,
+        *,
+        session_id=None,
+        lock_fd=None,
+    ):
         self.discovery_calls.append(("start", frame, target, traces_to, upstream_path))
         return self._discovery_reply("start")
 
-    def discovery_status(self, session_id, cwd):
+    def discovery_status(self, session_id, cwd, *, lock_fd=None):
         self.discovery_calls.append(("status", session_id))
         return self._discovery_reply("status")
 
-    def discovery_brief(self, session_id, out_path, cwd):
+    def discovery_brief(self, session_id, out_path, cwd, *, lock_fd=None):
         self.discovery_calls.append(("brief", session_id, out_path))
         reply = self._discovery_reply("brief")
         # Стенд пишет артефакт при кодах 0/10/11/20, как сосед.
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_governance_ops.py -q -k discovery` → 11 passed; полный набор — журнал (проверено).

- [ ] **Step 5: Commit** — `feat(need): порт discovery — session_id, lock_fd, approve (§11.4.6)`.

---

## Self-Review части A

- **Покрытие спеки:** §11.4.2 → Task 1, 2, 4; §11.4.3 → Task 4; §11.4.5 (блокировка) →
  Task 5; §11.4.6 → Task 3, 6; §11.3 п.3a (строгая заявка) → Task 2. Остальное §11 —
  часть B.
- **Тесты §11.7 в части A:** T1–T4 (Task 3, 6), T14–T27 и T31 на уровне предикатов
  (Task 4; уровень spec-loop — часть B, задача 8), T21a/T21b (Task 2 + Task 4 на общем
  наборе `DEFECTS`), T21c (Task 3 адаптер + Task 4), T37/T37a/T37b (Task 5), T40 (Task 1),
  T48a (Task 3). T38 через настоящий discovery — часть B (smoke, opt-in).
- **Неисполненное в части A:** живой вызов `gh`/GitHub не выполнялся — адаптер проверен
  на записанных ответах; T38 через реальную цепочку `uv → discovery` — в части B.
