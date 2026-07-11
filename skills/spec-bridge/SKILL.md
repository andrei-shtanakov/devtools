---
name: spec-bridge
description: >
  Оформить задачу развития экосистемы как tasks.md-спеку для spec-runner:
  из кластера weekly self-review Robin, находки fleet-отчёта или явного запроса
  пользователя — в managed-спеку (draft) PR-ом в репо-владелец изменений.
  Запускать: «оформи спеку», «преврати кластер/находку в задачи», «заведи
  задачи по этому провалу», «spec bridge».
allowed-tools: Bash, Read, Grep, Glob, Write
---

# spec-bridge — из находки в исполняемую спеку

Мост «наблюдение → действие» fleet-агента. Конституция `devtools/CLAUDE.md`:
соседние репо read-only, доставка PR-ом; агент предлагает — человек утверждает.

## Шаг 1 — источник и владелец

Определи вход (один из):
- кластер из `robin-runtime/var/selfreview/<дата>.md` (у каждого кластера уже
  указан «Предлагаемый артефакт»);
- находка из `prograph-vault/derived/fleet/fleet-*.md` («Требует внимания»);
- прямой запрос пользователя.

Определи **репо-владелец** изменений (placement-правило экосистемы: спека
живёт в репо, чей код она меняет). Сомневаешься — спроси пользователя,
не угадывай.

## Шаг 2 — заполнить шаблон

Основа: `devtools/templates/tasks-spec-template.md`. Формат-авторитет:
`spec-runner/spec/FORMAT.md`. Требования сверх шаблона:

- `generated_at` — сейчас, ISO-8601; `status: draft` НЕ менять (approve —
  прерогатива человека, при spec_governance strict draft не исполняется).
- Provenance-строка `Source:` в каждой задаче — путь к кластеру/отчёту/записи.
- Чеклист: последний пункт каждой задачи — проверка (тест/прогон), не действие.
- Для изменений Robin добавь в чеклист: «robin_regression suite зелёная»
  (atp-platform examples/test_suites/robin_regression.yaml — гейт ступени 4).

## Шаг 3 — доставка PR-ом

```bash
cd ../<репо-владелец>
git checkout -b spec/<короткое-имя>
mkdir -p spec
# имя с префиксом, чтобы не столкнуться с основным tasks.md репо:
cp <заполненный шаблон> spec/<имя>-tasks.md
git add spec/ && git commit -m "spec: <имя> (draft, fleet-agent)"
gh pr create --fill 2>/dev/null || echo "gh недоступен — ветка готова, PR вручную"
```

В описании PR: источник (ссылка на кластер/отчёт), и что спека — draft,
исполнение после approve (`spec-runner run --strict --spec-prefix=<имя>-`).

## Шаг 4 — после approve (делает человек)

Человек переводит frontmatter в `status: approved` (или через
`spec-runner spec approve`), затем `spec-runner run --strict
--spec-prefix=<имя>-` в репо-владельце. Fleet-агент исполнение не запускает.
