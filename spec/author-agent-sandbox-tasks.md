---
spec_stage: tasks
status: draft
owner_role: stream-owner
version: 1
generated_by: fleet-agent
generated_at: "2026-09-29T13:30:00+04:00"
source_prompt_version: ""
validation: ""
approved_by: ""
---

## Milestone 1: песочница авторского агента, v1 — claude-путь

Настоящая граница записи и чтения для авторского агента раннера на macOS:
внешняя обёртка `sandbox-exec` вокруг `ops.author` (claude) и `ops.author_disp`
(claude-агенты). Дизайн — `docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`
(§3 модель, §4 проба, §5 живая приёмка, §8 результаты спайков, §9
промежуточная защита, §10 требования владельца). codex — вне v1 (§8, `~/.codex`).
Уже влито до этой спеки: allowlist env (#483), изоляция конфигурации claude и
трипвайр конфиг-поверхности (#489).

### TASK-001: Профиль Seatbelt авторского вызова — генератор и его границы
P1 | TODO   Est: 1d

Чистая функция: по координатам вызова (дерево узла, свой `TMPDIR`, каталог
конфигурации claude, у disp — каталог анкера P9) строит профиль §3 с добавками
§8 и требованиями §10. Сама функция ничего не запускает — её проверяют пробы
TASK-002 на настоящем `sandbox-exec`.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §3, §8, §10; devtools#469 п.2

**Checklist:**
- [ ] запись разрешена только в: дерево узла, свой `TMPDIR` (НЕ `/private/tmp` и НЕ `/private/var/folders` целиком — F11), каталог конфигурации claude вызова, каталог анкера P9 (только disp), путь трекинга cwd claude
- [ ] путь трекинга cwd **заякорен** (§10 п.2): регэксп `…/claude-<uid>/<slug(дерево узла)>` закрыт разделителем (`(/|$)`), не голым префиксом — узлы `w1-req`/`w1-req-x` и `node-1`/`node-10` не пишут в каталоги друг друга
- [ ] ВНУТРИ разрешённого дерева узла запись запрещена в `.git/hooks/` и `.claude/` (§10 п.5: deny после allow); `~/.claude` целиком и `.git/hooks/`, `.claude/` всех репо воркспейса — вне разрешённого, т.е. запрещены
- [ ] venv disputatio — только чтение (§10 п.3): вне разрешённой записи по построению, отдельного исключения нет
- [ ] чтение запрещено: `~/.config/{review,gh,ai-prosto}`, `~/.ssh`; `mach-lookup` к `com.apple.SecurityServer`, `com.apple.securityd.xpc`, `com.apple.security.agent` (F6)
- [ ] юнит-тесты генератора: каждый пункт выше присутствует в профиле, отсутствует `(allow file-write*)` на `/private/tmp` целиком
- [ ] `uv run --frozen --group governance pytest -q tests/test_governance_sandbox.py` зелёный

**Touches:** governance/sandbox.py, tests/test_governance_sandbox.py

### TASK-002: Проба-канарейка перед каждым вызовом — стоп, не предупреждение
P1 | TODO   Est: 1d

Перед каждым авторским вызовом раннер исполняет под той же обёрткой с тем же
профилем короткую пробу (§4). Любое расхождение — `stopped_author` с названным
пунктом ДО платного вызова; нет бинаря `sandbox-exec` или профиль не
применился — тот же стоп. Запуска «без обёртки с предупреждением» нет.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §4, §10 п.2–3

**Checklist:**
- [ ] обязаны НЕ удаться: запись в `run_dir/edge-check/` и в `$HOME`; чтение приманки `~/.config/ai-prosto/.sandbox-bait`; `security find-generic-password -s gh:github.com` с `-a ai-prosto`, с `-a <основной аккаунт>` и без `-a`; `security … -s "Claude Code-credentials"`
- [ ] обязаны НЕ удаться (§10): запись в соседний каталог трекинга с общим префиксом slug (`<slug>-x`); запись в venv disputatio; запись в `<дерево узла>/.git/hooks/` и `<дерево узла>/.claude/`
- [ ] окружение внутри обёртки: множество имён **равно** allowlist'у вызова (равенство множеств, не поиск трёх имён)
- [ ] обязана УДАТЬСЯ (базовая половина): запись в дерево узла
- [ ] следы пробы убираются; приманку раннер кладёт и снимает сам
- [ ] тесты на настоящем `sandbox-exec` (macOS; на другой ОС — skip с названной причиной): каждая проба отдельно; отказ пробы → `stopped_author` с названным пунктом, агент не вызван
- [ ] мутации приёмки, каждая обязана покраснеть: убрать обёртку (пробы запретов); передать env наследованием (проба env); профиль `deny default` без исключений (базовая проба); голый префикс в пути трекинга (проба соседа)
- [ ] `uv run --frozen --group governance pytest -q tests/test_governance_sandbox.py tests/test_governance_runner.py` зелёный

**Depends on:** [TASK-001]

**Touches:** governance/sandbox.py, governance/runner.py, tests/test_governance_sandbox.py, tests/test_governance_runner.py

### TASK-003: Обёртка на вызовах claude-пути; disp — из venv, таймаут вызова
P1 | TODO   Est: 1d

`ops.author` (AUTHOR_HARNESS=claude) и `ops.author_disp` (любая роль — claude)
исполняются под обёрткой TASK-001 после пробы TASK-002. disp запускается
бинарём venv disputatio напрямую, без `uv run` под обёрткой (§8/S3: `uv run`
пишет в общий `~/.cache/uv`). Вызов ограничен таймаутом (§8/S2: харнесс без
нужных путей зависает, а не падает).
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §8 (S1, S3), §9

**Checklist:**
- [ ] env вызова — `agent_env()` + свой `TMPDIR` + `SSL_CERT_FILE=/etc/ssl/cert.pem` + изоляция claude (#489: `CLAUDE_CONFIG_DIR`, токен из связки)
- [ ] `TMPDIR` и каталог конфигурации claude — внутри каталога прогона вызова, удаляются после вызова
- [ ] disp: `<disputatio>/.venv/bin/disp` напрямую; нет venv — стоп с процедурой (`uv sync` вне обёртки), не `uv run`
- [ ] таймаут вызова — стоп `stopped_author` с причиной «таймаут», не зависание прогона
- [ ] codex-путь без изменений (вне v1): тест, что `AUTHOR_HARNESS=codex` обёрткой не оборачивается и это названо в выводе
- [ ] `uv run --frozen --group governance pytest -q tests/test_governance_ops.py tests/test_governance_runner.py` зелёный

**Depends on:** [TASK-002]

**Touches:** governance/ops.py, governance/runner.py, tests/test_governance_ops.py, tests/test_governance_runner.py

### TASK-004: Живая приёмка v1 — узел под обёрткой, сравнение без неё, disp
P1 | TODO   Est: 1d

Позитивная половина (§5) и требования владельца §10 п.1 и п.4 — на живых
вызовах, не на фейках.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §5, §10 п.1, п.4

**Checklist:**
- [ ] авторинг одного узла claude под обёрткой: узел написан, гейт узла зелёный, пробы TASK-002 перед вызовом зелёные
- [ ] **сравнение качества** (§10 п.4): тот же узел, та же задача, без обёртки и без изоляции конфигурации — гейт и находки edge-check обоих сопоставлены; если под обёрткой чего-то не хватает — это кладётся в конфиг вызова явно из доверенного источника, НЕ открытием `~/.claude`
- [ ] **disp условно** (§10 п.1): сходящийся прогон disp с НАСТОЯЩЕЙ задачей раннера под обёрткой; не сходится из-за профиля — disp выходит из v1 (как codex), профиль не расширяется; итог записан в §8 дизайна
- [ ] протокол приёмки (факты, sha, стоимость) — в `docs/evidence/`

**Depends on:** [TASK-003]

**Touches:** docs/evidence/, docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md

### TASK-005: Контракт и документация
P2 | TODO   Est: 0.5d

Дизайн §9 и D12a спеки edge-check говорят «граница — песочница, её ещё нет»;
после v1 — описать, что закрыто и что нет.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §6, §9; docs/superpowers/specs/2026-09-21-edge-check-design.md D12a

**Checklist:**
- [ ] D12a спеки edge-check: граница записи для claude-пути — песочница v1; непокрытое (codex, исполнители spec-runner#600, чужие прогоны) — поимённо
- [ ] §9 дизайна: что из промежуточной защиты остаётся (трипвайр — как пояс), что снято песочницей
- [ ] строка инструмента в `CLAUDE.md` devtools (песочница авторского вызова, требования к машине: элемент связки, `sandbox-exec`)
- [ ] проверка ссылок: `make plan-check` без новых ошибок

**Depends on:** [TASK-004]

**Touches:** docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md, docs/superpowers/specs/2026-09-21-edge-check-design.md, CLAUDE.md
