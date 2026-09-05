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
   depends_on: [DT-…]            # может быть пуст; РЁБРА — ИСТОЧНИК ИСТИНЫ
   delivered_by: [DT-…]          # ОБЯЗАТЕЛЕН при type: verify, иначе запрещён
   parallel_group: <имя|solo>    # метка независимой ветви графа
   <проза: предмет, границы>
   ```

2. **Инварианты графа** (проверяются гейтом, уроки WS-341):
   - каждый BEH-* бандла покрыт ровно одной задачей (сюръекция без
     дублей);
   - **единственный владелец тест-файла**: множества checked_by-целей
     (файлы, выведенные из scenarios через биндинг §4) разных DT не
     пересекаются — перенос защиты `_merge_featureless_by_target_file`
     в гейт (иначе byte-lock ранней задачи стопит позднюю — класс
     TASK-014/015 WS-57);
   - `verify`-задача обязана называть `delivered_by: [DT-…]` И
     `delivered_by ⊆ транзитивное замыкание depends_on` — verify не
     может быть исполнена раньше того, что проверяет;
   - семантика verify — НЕ «red не нужен по слову автора» (доставку
     машинно на авторинге не доказать — истина в коде). Механизм —
     КРОСС-РЕПНЫЙ ШОВ **spec-runner#367 «verify-first»**
     (@blocked_by:spec-runner#367 для этой части): режим исполнения
     spec-runner, где задача начинается живым прогоном checked_by-группы
     с durable evidence (commit SHA, селектор, config hash, исход
     `green | test-failure | instrument-error`); только `green` разрешает
     green-only путь, `test-failure` переводит в обычный TDD,
     `instrument-error`/неизвестное — стоп. Мост генерирует verify-задачи
     с Mode/меткой этого режима; ДО доставки spec-runner#367 генерация
     verify-задач отключена fail-closed (verify в DT ⇒ отказ моста с
     текстом про блокер), implement-путь работает полностью;
   - граф ацикличен. `parallel_group` — метка ветви, НЕ запрет рёбер:
     явные межгрупповые рёбра (verify за своими delivered_by, сводные за
     хвостами групп) разрешены и обязательны, где того требуют
     инварианты; запрещена только ИСКУССТВЕННАЯ линейная цепочка от
     моста — рёбра tasks-спеки происходят ровно из depends_on;
   - сводные задачи (регрессии/док) зависят от хвостов всех групп,
     которые сводят (явными рёбрами depends_on).
3. **Порядок и параллельность** — какие группы могут исполняться
   одновременно (вход для будущего параллельного spec-runner-прогона;
   сегодня — документация для оператора).
4. **Вне объёма** — что сознательно не нарезано.

## 4. Изменения (devtools, по швам — те же, что спека 1 открыла)

| Файл | Правка |
|---|---|
| `profiles/team-exp.yaml` | +узел decomposition (форма §2, upstream `[design]`); `tasks.upstream: [decomposition]` — ПЕРЕХВАТ у design (спека 1 ставила `[design]` как временное) |
| `governance/runner.py` | `_AUTHOR_STEPS` += `("author-decomposition", "decomposition", "30-decomposition.md")` — ЯВНО после `author-design` и до `commit`; S4-гарды: ребро decomposition → design (GC-UNPINNED/GC-STALE через data-driven список), гард отсутствия required-узла, DSL-empty (`#### DT-\d`), локальный гард графа — ПОЛНЫЙ список инвариантов §3: сюръекция BEH (полное И однократное покрытие), single-owner checked_by-целей, ацикличность, `delivered_by` обязателен у verify и запрещён у implement, `delivered_by ⊆ транзитивного замыкания depends_on`, валидность parallel_group-меток и сводных задач (рёбра к хвостам групп) |
| `governance/ops.py` | `_AUTHOR_FILENAMES["decomposition"]`, `_AUTHOR_DSL["decomposition"]` — контракт §3 с грамматикой DT |
| `governance/console_model.py` | `PIPELINE_KEYS` += `author-decomposition` после `author-design`, до `commit`; тест-сверка спеки 1 сравнивает ПОРЯДОК (последовательность author-ключей), не множество — вставка не в то место краснеет |
| `governance/design_guard.py` → расширение или соседний `governance/decomposition_guard.py` | парсер DT-грамматики + проверки инвариантов графа (чистые функции; переиспользуются гейтом и мостом) |
| `governance/task_bridge.py` | `_BUNDLE_DAG` += `("30-decomposition.md", ("design",))`; генерация задач — ИЗ DT-грамматики (1 DT = 1 задача): заголовок; checklist из scenarios, где мост ДЖОЙНИТ BEH → checked_by из 15-behaviour-spec.md по id и сохраняет пары target+kind (последний пункт чеклиста — «проверка группы: <target> (kind: <kind>) зелёные на BEH-…», решение приёмки PR #100); Depends on из depends_on (рёбра — источник истины, искусственная линейная цепочка не добавляется); verify ⇒ Mode/метка режима verify-first (spec-runner#367; до его доставки — fail-closed отказ на verify-DT). `render_tasks`/`deliver` СРАЗУ пишут `traces_to: [decomposition]` и пин 30-decomposition.md — не ждут conform_approved. Эвристика `_merge_featureless_by_target_file` на DT-пути отключается — её инвариант ПЕРЕЕХАЛ в гейт (single-owner, §3), legacy-пути не трогаются. `conform_approved` ОБОБЩАЕТСЯ ЭТОЙ спекой: якорь — терминальный узел активной цепочки (спека 1 такого вывода НЕ делала — там якорь design захардкоживается; ложная атрибуция снята) |
| переходный режим | бандл со `20-design.md`, но без `30-decomposition.md` ⇒ тот же fail-closed RuntimeError с процедурой. Флаг ОБОБЩАЕТСЯ до параметризованного `--legacy-bundle=3|4` и ПРОВОДИТСЯ через все пути: `deliver`, `stamp_bundle_approved`, `conform_approved`, `deliver_conform` и CLI (оба режима). Проверка — ТОЧНОГО фактического состава: заявленный префикс обязан совпасть с существующим набором файлов бандла ровно (заявили 3 — есть ровно 00/10/15; заявили 4 — ровно +20; иное ⇒ отказ). «По самому длинному существующему» запрещён — красил бы недоавторенный бандл зелёным. Спека 1 определяла только 3-узловой вариант — атрибуция исправлена |

Гейты/аппрув: механика спеки 1 переиспользуется (data-driven рёбра,
локальные гарды в `_step_gate`); новый — только гард графа.

Rollout target-профилей — то же решение, что Task 8 плана спеки 1:
preflight (`_target_profile_declares(target_dir, profile,
"decomposition")`) fail-closed в `start`/`resume`/`deliver`; 4-узловые
профили соседей обновляются до 5-узловых отдельными PR (authority-root,
человеческий мерж) ДО первого прогона с decomposition.

## 5. Тесты

- Профиль: узел decomposition, `tasks.upstream: [decomposition]`.
- Грамматика DT: парс, покрытие BEH (непокрытый ⇒ finding; двойное
  покрытие ⇒ finding), цикл в depends_on ⇒ finding, verify без
  `delivered_by` ⇒ finding, `delivered_by ⊄ транзитивного замыкания
  depends_on` ⇒ finding, пересечение checked_by-целей двух DT
  (single-owner) ⇒ finding.
- S4: ребро decomposition → design — UNPINNED/STALE по отдельности;
  отсутствие узла — тест бьёт в ЛОКАЛЬНЫЙ гард `_step_gate` (как §5
  спеки 1), не в неохарактеризованный CLI-код.
- Мост: 1 DT = 1 задача; чеклист сохраняет пары target+kind из
  behaviour-spec (джойн по BEH-id); tasks-спека РОЖДАЕТСЯ с
  `traces_to: [decomposition]` и пином 30-decomposition.md (тест на
  `render_tasks`, не на conform); Depends on переведён ровно из
  depends_on — искусственной линейной цепочки нет, межгрупповые явные
  рёбра переведены; verify-DT до доставки spec-runner#367 ⇒ fail-closed
  отказ моста с текстом про блокер; после — verify-задача несёт
  Mode/метку verify-first.
- Сверка PIPELINE_KEYS ↔ _AUTHOR_STEPS зелёная после правки обоих.
- Переходный режим: 4-узловой бандл без decomposition ⇒ RuntimeError с
  процедурой; `--legacy-bundle=4` штампует по 4-узловому составу;
  `--legacy-bundle=3` на 4-узловом бандле ⇒ отказ (точное совпадение
  состава, не префикс); те же проверки на conform-путях
  (`conform_approved`/`deliver_conform`); флаг без значения ⇒ отказ
  парсера.
- Порядок шагов: тест сверки списков сравнивает ПОСЛЕДОВАТЕЛЬНОСТЬ
  author-ключей (design перед decomposition, оба до commit).
- Preflight decomposition: профиль target без узла ⇒ stopped_preflight/
  RuntimeError (переиспользование механики Task 8 спеки 1).
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

## 7a. Кросс-репный шов (блокер части verify)

`@blocked_by:spec-runner#367` — режим verify-first (живой прогон группы
до агентского вызова, durable evidence SHA/selector/config-hash, исходы
green|test-failure|instrument-error). До доставки: реализация спеки 3
полноценна для implement-DT; verify-DT отклоняются мостом fail-closed.
Чекбокс-ожидание заводится в TODO.md devtools при старте реализации.

## 8. Вне объёма

Steward-compile (project.yaml → Maestro); параллельное ИСПОЛНЕНИЕ групп
spec-runner'ом (сегодня группы — документация порядка; исполнение
последовательное); acceptance-узел; правки steward; миграция старых
бандлов.
