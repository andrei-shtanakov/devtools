# Decomposition-узел в behaviour-конвейере (спека 3 серии «полный team-exp»)

Дата: 2026-09-05. Статус: draft, на ревью владельца.
Серия: спека 3; строится НА спеке 1 (design-узел,
`docs/superpowers/specs/2026-09-05-design-node-conveyor-design.md`,
влита devtools#142). Спеку можно ревьюить/утверждать параллельно
реализации спеки 1; РЕАЛИЗАЦИЯ — строго после (см. §7).

## 1. Мотивация (боевые доказательства)

Сегодня декомпозицию делает механика `task_bridge`: одна задача на
BEH-группу по файлу checked_by-цели. Измеренная цена одного workstream'а
(WS-spec-runner-341, 18 задач):

- **4 задачи из 18 выродились в waiver'ы** (TASK-007/011/012/013 —
  «поведение уже доставлено архитектурными задачами»): нарезка не
  отличает реализационную задачу от проверочной, каждая проверка стоила
  оплаченный red-зонд (~$1–2) и цикл прогона.
- **Линейный Depends on сквозь независимые половины** — находка ревью
  tasks-PR spec-runner#343: провал одной Feature-половины хоронил бы
  другую; чинилось рукой.
- **Баги генератора** собраны в devtools#123 (формат Traces, owner_role,
  граф зависимостей) — часть уже закрыта руками в спеке WS-341, но
  генератор не трогали.

Decomposition-узел переносит нарезку в артефакт под гейтом и аппрувом:
tech-lead-роль решает состав задач, типы (implement/verify), граф
зависимостей и параллельность — ДО генерации tasks-спеки; task_bridge
становится транслятором решённой декомпозиции, а не решателем.

## 2. Форма узла (пин steward)

Из `steward/profiles/team-exp.yaml` (тот же пин, что спека 1):

```yaml
- {id: decomposition, template: decomposition.md, owner_role: tech-lead,
   upstream: [design, acceptance]}
- {id: tasks, owner_role: stream-owner, upstream: [decomposition],
   delegate: spec-runner, per: workstream}
compile:
  decomposition: {to: maestro, artifact: project.yaml}
```

Отступления нашего урезанного профиля (документируются в комментарии):

- `upstream: [design]` — acceptance срезан (придёт спекой 4; тогда
  upstream расширится до формы steward).
- `compile: decomposition → maestro/project.yaml` — НЕ реализуется:
  наш лейн Mode-2 (прямые прогоны spec-runner без Maestro); строка
  остаётся в steward-форме, у нас компиляция decomposition отсутствует
  осознанно (см. §8).

Файл бандла — `30-decomposition.md` (номерная конвенция; 25-… уходит
acceptance).

## 3. Контракт содержимого `30-decomposition.md`

Frontmatter: `spec_stage: decomposition`, `status: draft`,
`owner_role: tech-lead`, `traces_to: [design]`,
`upstream_hashes: {design: "<blob 20-design.md>"}`.

Обязательные секции (машинная грамматика — источник генерации tasks):

1. **Задачи** — каждая:

   ```
   #### DT-NN: <название> · type: implement|verify · owner: <роль>
   scenarios: [BEH-…]            # покрываемые сценарии, ≥1
   depends_on: [DT-…]            # может быть пуст
   parallel_group: <имя|solo>    # метка независимой ветви графа
   <проза: предмет, границы>
   ```

2. **Инварианты графа** (проверяются гейтом, уроки WS-341):
   - каждый BEH-* бандла покрыт ровно одной задачей (сюръекция без
     дублей);
   - `verify`-задача обязана называть, ЧТО доставило поведение
     (`delivered_by: [DT-…]` — ссылка на implement-задачи) — это сигнал
     генератору «red-фаза не нужна, зелёный пин» (лекарство от
     4 waiver'ов);
   - граф ацикличен; независимые `parallel_group` не связаны рёбрами
     (урок «линейная цепочка сквозь половины»);
   - сводные задачи (регрессии/док) зависят от хвостов всех групп,
     которые сводят.
3. **Порядок и параллельность** — какие группы могут исполняться
   одновременно (вход для будущего параллельного spec-runner-прогона;
   сегодня — документация для оператора).
4. **Вне объёма** — что сознательно не нарезано.

## 4. Изменения (devtools, по швам — те же, что спека 1 открыла)

| Файл | Правка |
|---|---|
| `profiles/team-exp.yaml` | +узел decomposition (форма §2, upstream `[design]`); `tasks.upstream: [decomposition]` — ПЕРЕХВАТ у design (спека 1 ставила `[design]` как временное) |
| `governance/runner.py` | `_AUTHOR_STEPS` += `("author-decomposition", "decomposition", "30-decomposition.md")` после design; S4-гарды: ребро decomposition → design (GC-UNPINNED/GC-STALE через тот же data-driven список), гард отсутствия required-узла, DSL-empty (`#### DT-\d`), НОВЫЙ локальный гард графа: покрытие BEH (сюръекция), ацикличность, `delivered_by` у каждого verify |
| `governance/ops.py` | `_AUTHOR_FILENAMES["decomposition"]`, `_AUTHOR_DSL["decomposition"]` — контракт §3 с грамматикой DT |
| `governance/console_model.py` | `PIPELINE_KEYS` += `author-decomposition` (тест-сверка спеки 1 покраснеет сама и потребует этого — задуманный эффект) |
| `governance/design_guard.py` → расширение или соседний `governance/decomposition_guard.py` | парсер DT-грамматики + проверки инвариантов графа (чистые функции; переиспользуются гейтом и мостом) |
| `governance/task_bridge.py` | `_BUNDLE_DAG` += `("30-decomposition.md", ("design",))`; генерация задач tasks-спеки — ИЗ DT-грамматики (1 DT = 1 задача: заголовок, checklist из scenarios, Depends on из depends_on, тип verify ⇒ пометка «зелёный пин, red-фаза не требуется» в теле задачи); группировочная эвристика `_merge_featureless_by_target_file` для 4-узловых бандлов ОТКЛЮЧАЕТСЯ (остаётся только на legacy-путях); `conform_approved` нормализует к decomposition (якорь — последний узел DAG, спека 1 уже вывела его из `_BUNDLE_DAG[-1]` — правка сводится к данным) |
| переходный режим | бандл со `20-design.md`, но без `30-decomposition.md` ⇒ тот же fail-closed RuntimeError с процедурой + `--legacy-bundle` уже принимает и 3-, и 4-узловые префиксы DAG (обобщение флага спеки 1: штамп по самому длинному существующему префиксу, явно подтверждённому флагом) |

Гейты/аппрув: механика спеки 1 переиспользуется (data-driven рёбра,
локальные гарды в `_step_gate`); новый — только гард графа.

## 5. Тесты

- Профиль: узел decomposition, `tasks.upstream: [decomposition]`.
- Грамматика DT: парс, покрытие BEH (непокрытый ⇒ finding; двойное
  покрытие ⇒ finding), цикл в depends_on ⇒ finding, verify без
  `delivered_by` ⇒ finding.
- S4: ребро decomposition → design — UNPINNED/STALE по отдельности;
  отсутствие узла ⇒ GC-COMPLETENESS.
- Мост: 1 DT = 1 задача; verify-задача несёт пометку «без red-фазы»;
  Depends on переведён из depends_on (включая межгрупповые сводные);
  `parallel_group`-задачи не получают искусственных рёбер.
- Сверка PIPELINE_KEYS ↔ _AUTHOR_STEPS зелёная после правки обоих.
- Переходный режим: 4-узловой бандл без decomposition ⇒ RuntimeError с
  процедурой; `--legacy-bundle` штампует по 4-узловому префиксу.
- `conform_approved`: якорь decomposition (регрессия отката).

## 6. Карта артефактов — дельта к §6 спеки 1

| Этап | Владелец | Артефакт | Статус |
|---|---|---|---|
| decomposition | tech-lead | `30-decomposition.md` (DT-задачи, типы, граф, группы) | эта спека |
| tasks | stream-owner | генерируется ИЗ DT-грамматики (не из BEH-группировки) | правка моста этой спекой |
| steward-compile → project.yaml → Maestro | — | не реализуется (Mode-2), форма steward сохраняется | вне объёма |

## 7. Зависимости и параллельность (матрица спеки 1, уточнение)

- **Спека 3 ← спека 1 (реализация):** жёсткая — трогаем те же
  `_AUTHOR_STEPS`/`PIPELINE_KEYS`/`_BUNDLE_DAG`/data-driven рёбра,
  которые спека 1 СОЗДАЁТ. Ревью/утверждение спеки 3 — параллельно.
- **Спека 3 ⊥ спека 2 (interview/proposal, disputatio):** полная.
- **Спека 3 ↔ спека 4 (acceptance):** пересечение — upstream
  decomposition (`[design]` → `[design, acceptance]`) и та же таблица
  рёбер; порядок реализации 3↔4 безразличен, но вторая из них делает
  однострочную правку upstream'а первой.

## 8. Вне объёма

Steward-compile (project.yaml → Maestro); параллельное ИСПОЛНЕНИЕ групп
spec-runner'ом (сегодня группы — документация порядка; исполнение
последовательное); acceptance-узел; правки steward; миграция старых
бандлов.
