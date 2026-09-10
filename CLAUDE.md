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
   Шаблон — `templates/tasks-spec-template.md`, процесс — скилл
   `skills/spec-bridge/` (managed-спека, status: draft; approve — человек).
5. Отчёт всегда содержит `host` — чьи локальные клоны он описывает
   (ahead/behind/dirty — состояние конкретной машины).

## Инструменты

| Инструмент | Назначение |
|---|---|
| `repos.sh` | интерактив: status / fetch / pull / dirty / bootstrap / exec |
| `Makefile` | алиасы, в т.ч. `make snapshot`, `make fleet-report`, `make morning` |
| `fleet_report.py` | snapshot-JSON → markdown-отчёт для vault `derived/fleet/` |
| `recent_changes.py` | темпоральный сенсор: коммиты + незакоммиченное с момента X (`make today`) |
| `salvage_scan.py` | salvage-скан флота (`make salvage`): orphan worktrees, ветки без PR, unpushed default, stale locks; пустой результат молчит, осознанные исключения помечает `[waived]` (devtools#67) |
| `spec_run_preflight.py` | преflight перед прогоном spec-runner в соседе (`make preflight ARGS='--repo <r>'`): конфиг-по-эталону, insteadOf https, беспрефиксная state-DB, live-smoke-среда как в CI (ретроспектива 2026-09-02, уроки 4–5 devtools#110) |
| `check-contract-drift.sh` | дрейф вендоренных контрактов |
| `check-agent-id-conformance.py` | инварианты ADR-ECO-003 |
| `check-catalog-fixtures.py` | owner-QA SSOT-набора conformance-фикстур каталога (`contracts/catalog-conformance-fixtures/v1/`): референс V1–V7 + manifest |
| `check-plan-fields.py` | кросс-репный граф `@blocked_by` — ловит пункт, ждущий уже отгруженного (режим отказа R-03) |
| `check-arch-evidence-freshness.py` | drift вендоренных prograph-схем steward + freshness evidence WS-005; `--read` — просрочка ⇒ unknown |
| `merge-pr.sh` | единственный разрешённый путь агентского мержа (ADR-ECO-011), в т.ч. для `accept-pr` и S7 раннера через `Ops.merge`; сам скрипт и его SSOT `contracts/approval-branches/` — харнесс-пути (`_HARNESS_PREFIXES`): PR, который их трогает, агентом не ревьюится и не мержится, иначе контур исполнил бы гвард из проверяемого дерева. PR из форка с `--delete-branch` — отказ до мержа (ветка форка живёт в чужом репо); четвёртый категорический отказ — дифф, трогающий authority-root пути (перечень — SSOT `contracts/authority-root/v1/paths.env`, его же читают `accept_pr` и раннер): сверяет профиль `ai-prosto`, отказывает на candidate/finalize-ветках заявки одобрения §I12 и на лейбле `human-merge-required`, мержит прямым `PUT /pulls/{n}/merge` с пином головы `sha=` (не `gh pr merge` — решение дизайна 2026-08-30 §8: тот при `BLOCKED` отказывает сам, не проверив bypass актора); `--expect-head`/`--expect-base` — пины вызывающего, база без `--expect-base` НЕ проверяется и об этом говорится вслух; стратегии — закрытый allowlist `--squash\|--merge\|--rebase` (+`--delete-branch`), свободного passthrough нет. Формы веток одобрения читаются из `contracts/approval-branches/v1/patterns.env` — того же файла, из которого их строит `governance/approval_branches.py` |
| `review-pr.sh` | терминальный прогон ревью PR через review-kit целевого репо + публикация вердикта как PR review от ai-prosto (профиль `~/.config/review`); харнесс ревьюера настраиваем: `--harness claude\|codex` / env / `~/.config/ai-prosto/harness.env` (свойство машины/подписки, вшитый дефолт codex; claude — через `scripts/harness/claude-review`, судьба переходника — steward#147); `--dry-run` — показать, не постить; opt-in `--dry-run --write-verdict <file>` → `--use-verdict <file>` переносит тот же проверенный результат без второго вызова ревьюера только при точных `head + fp`. Литерал маркера `codex-terminal-review` — имя протокола, НЕ бинаря: не переименовывать |
| `.claude/skills/fleet-check` | скилл периодической проверки флота |
| `skills/spec-bridge` | скилл: находка/кластер → tasks.md-спека PR-ом в репо-владелец |

## Быстрый старт агента

```bash
make snapshot        # полный JSON состояния флота (git + GitHub, если gh готов)
make fleet-report    # markdown-отчёт в stdout
make morning         # человеческий ритуал: fetch + status
```

## Входящие запросы (inbox)

В начале работы проверь входящие: `gh issue list --label inbox --state open`.
Issue с лейблом `inbox` — запрос от соседнего репо, ещё **не** пункт плана.
Принять = завести пункт в `TODO.md` с указанным `slug:`; принял под другим
именем — поправь `slug:` в теле issue.
Отказать = `gh issue close --reason "not planned"`.
Нужна работа в соседнем репо — не редактируй его: заведи там issue
(`slug:` + `from:` + проза). Правило: ADR-ECO-006 — канон в `ecosystem-kb`
(каталог `prograph-vault/` в корне воркспейса),
`authored/decisions/2026-07-28-adr-eco-006-cross-repo-issue-inbox.md`.

Исходящее ожидание — вторая половина того же ритуала: «ждём соседа» существует
**только** как чекбокс `TODO.md` с `@blocked_by:todo://<repo>/<id>` (переходно —
`<repo>#<номер>`); память сессий, заметки и handoff-доки — лишь зеркало. Находка
PF-BLOCKER-STALE по этому репо = «ожидание доставлено — действуй или переставь тег».
Правило (SSOT): `../prograph-vault/authored/rules/cross-repo-waits.md`.

## Repo scope & boundaries

- **Этот репо:** `devtools` — git-корень `all_ai_orchestrators/devtools/`, remote `git@github.com:andrei-shtanakov/devtools.git`.
- **Соседи (READ-ONLY reference):** все остальные подпроекты воркспейса — их код не
  редактировать. Состав флота — `ai-orchestrators-workspace/workspace-manifest.toml`
  (SSOT); рукописные списки соседей в CLAUDE.md не ведём — они дрейфуют.
- **Канон имени репо = имя каталога после обычного `git clone`** (`maestro`, `libretto`).
- Нужна правка у соседа → **стоп**: запиши handoff в `../prograph-vault/authored/notes/`
  (кросс-проектное) или `../_cowork_output/` (черновик), не трогай его файлы.
- Кросс-репные контракты — **вендорить пиненой копией внутрь**, не ссылаться наружу.
- Полное правило (SSOT): `../prograph-vault/authored/rules/repo-boundaries.md`.

## Git workflow (у репо есть remote)

- Ветка `<type>/<slug>` → push → `gh pr create`. **Прямые коммиты в `master`
  запрещены**, как и локальный мерж ветки в `master` в обход PR.
- **Ревью PR — терминальный прогон от ai-prosto** (дефолт с 2026-08-28):
  `sh review-pr.sh <repo> <pr> --dry-run`, затем без `--dry-run` — вердикт публикуется
  PR-ревью. Находки отрабатывать как обычно: валидное — фикс-коммитом,
  невалидное — ответить с обоснованием, не применять вслепую. CI-гейт
  codex-review СНЯТ по флоту (решение владельца 2026-08-31: платный API
  поверх Actions-лимита); лейбл `codex-review` больше не вешать — ревью
  только терминальным прогоном. **Copilot по умолчанию не запрашивать** —
  только по явной просьбе владельца. SSOT: `../prograph-vault/authored/rules/git-workflow.md`.
- **Мерж — агент по умолчанию** (ADR-ECO-011 «DarkFactory», 2026-08-30): при
  approve от ревью-контура и зелёных обязательных проверках агент мержит сам и
  выполняет хвост чистки. Агентский мерж выполняется **только через
  `sh merge-pr.sh <repo> <pr>`**; любой прямой вызов мержа (`gh pr merge`,
  `gh api -X PUT …/merge`) для агента запрещён. Живых путей ровно три, и все
  три ведут сюда: руки оператора, `make accept-pr` и S7 раннера — последние
  два через `Ops.merge`, который вызывает эту же обвязку. Она сверяет логин
  профиля (`~/.config/review` → `ai-prosto`), отказывает на PR, которые обязан
  мержить человек (candidate/finalize-ветки §I12, лейбл `human-merge-required`),
  мержит с пином проверенной головы (`sha=`) и, если вызывающий передал
  `--expect-base`, с пином базы вердикта. Мерж от основного аккаунта записал бы
  агентский мерж человеческим, обнулив `merged_by` — наблюдаемый различитель
  agent/human (аудит `gh pr list --json mergedBy`), а гварды обошёл бы целиком.
  Request-changes или неприбывшее ревью = `unknown` ⇒
  мерж не выполняется, PR остаётся человеку. Человеческий мерж — opt-in: строка
  `Мерж: человек` в этой секции (здесь НЕ объявлена) либо `merge_policy`
  экосистемного конфига. Объявление прогона (`merge_authority: human`,
  ADR-ECO-008 D5) — третий, самый узкий уровень: прогон может ужесточить политику
  до человеческого мержа, ослабить репо-оверрайд — нет.
  Всегда человеку: authority-root пути (ADR-ECO-004 I2)
  и PR без предъявленного evidence.
- После мержа (кем бы то ни было): `git switch master && git pull --ff-only`, затем удалить
  влитую ветку в **обеих половинах**: локально `git branch -d <ветка>` (после squash-мержа
  `-d` откажется — сверить, что `git diff master <ветка>` пуст, и удалить
  `git branch -D <ветка>`) и на origin
  `git push origin --delete <ветка>`, если GitHub не удалил сам; затем `git fetch --prune`.
- Никогда не делать force-push в общие ветки; не трогать другие репо (см. scope выше).
- Полное правило (SSOT): `../prograph-vault/authored/rules/git-workflow.md`.

## `../_cowork_output/` — dev-only

Координационный dev-scratch воркспейса; у пользователей и клонов проекта его НЕТ.
Shipped/runtime-код никогда не читает и не резолвит пути под ним; кросс-репные
контракты вендорятся пиненой копией внутрь, не ссылкой наружу. Ссылаться на него
могут только dev-тулинг самого воркспейса и документация. Канонические факты живут
в репо-владельце (пример: SSOT agents-catalog — `atp-platform/method/agents-catalog.toml`,
ADR-ECO-003). Полное правило (SSOT): `../prograph-vault/authored/rules/cowork-output.md`.
