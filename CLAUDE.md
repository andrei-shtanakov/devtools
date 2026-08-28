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
| `check-contract-drift.sh` | дрейф вендоренных контрактов |
| `check-agent-id-conformance.py` | инварианты ADR-ECO-003 |
| `check-catalog-fixtures.py` | owner-QA SSOT-набора conformance-фикстур каталога (`contracts/catalog-conformance-fixtures/v1/`): референс V1–V7 + manifest |
| `check-plan-fields.py` | кросс-репный граф `@blocked_by` — ловит пункт, ждущий уже отгруженного (режим отказа R-03) |
| `check-arch-evidence-freshness.py` | drift вендоренных prograph-схем steward + freshness evidence WS-005; `--read` — просрочка ⇒ unknown |
| `review-pr.sh` | терминальный прогон codex-ревью PR через review-kit целевого репо + публикация вердикта как PR review от ai-prosto (профиль `~/.config/review`); `--dry-run` — показать, не постить |
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
  codex-review (где есть) — advisory-фолбэк по лейблу `codex-review`, его
  красноту/зависание не перегонять. **Copilot по умолчанию не запрашивать** —
  только по явной просьбе владельца. SSOT: `../prograph-vault/authored/rules/git-workflow.md`.
- **Не мержить.** Мерж делает пользователь.
- После мержа пользователем: `git switch master && git pull --ff-only`, затем удалить
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
