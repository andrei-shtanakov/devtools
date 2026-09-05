# Design-узел в behaviour-конвейере (спека 1 серии «полный team-exp»)

Дата: 2026-09-05. Статус: draft, на ревью владельца.
Серия: спека 1 из плана докрутки конвейера до полного team-exp
(диаграмма владельца 2026-09-05: interview → proposal → behaviour →
design → decomposition → реализация). Спека 2 (interview/proposal на
disputatio) — отдельно и позже; интервью-контур уже существует и здесь
не строится.

## 1. Мотивация (боевые доказательства WS-spec-runner-341)

Профиль конвейера сознательно урезан до charter → requirements →
behaviour-spec → tasks; архитектурные вопросы «переносятся в design»,
которого нет. Цена, измеренная одним workstream'ом:

- **Q-03/Q-04/Q-06** уехали «в design» и были закрыты вручную: оператор
  дописал секцию «Решения открытых вопросов» в преамбулу tasks-спеки
  после находки ревью tasks-PR (spec-runner#343) — артефакт стадии,
  которой нет, изготовлен рукой в чужом артефакте.
- **TASK-014**: три раунда непригодных red подряд; выходом стала «рамка
  red-дизайна» в чеклисте (решение владельца, spec-runner PR #360) —
  де-факто design-решение, принятое ad-hoc посреди исполнения, ценой
  ~4 оплаченных зондов.
- **TASK-018**: 7 раундов терминального ревью spec-runner#366 — дизайн
  пред-авторского усыновления (гейты применимости, принадлежность,
  предусловия чистоты) фактически происходил внутри приёмки, по цене
  платного ревью-раунда за итерацию.

Design-узел переносит эту работу туда, где она дешевле: в бандл, до
исполнения, под тот же гейтовый контур.

## 2. Форма узла (пин steward, ничего не изобретаем)

Источник формы — `steward/profiles/team-exp.yaml` (пин конвейера
steward@4a1c7c44; при реализации пин обновить и сверить форму):

```yaml
- {id: design, template: design.md, owner_role: architects,
   upstream: [requirements, behaviour-spec]}
```

Имя узла — `design` (rename design → architecture — открытый ADR-вопрос
steward, здесь не решается). Файл бандла — `20-design.md` (номерная
конвенция бандла: 00/10/15/20).

Отступление от полного профиля, документируемое в комментарии профиля:
`tasks.upstream` становится `[design]` (в полном — `[decomposition]`;
decomposition появится спекой 3 и тогда перехватит upstream). Это тот же
класс отступления, что нынешний `tasks.upstream: [behaviour-spec]`.

## 3. Изменения (devtools, по швам)

| Файл | Правка |
|---|---|
| `profiles/team-exp.yaml` | +узел design (форма §2); `tasks.upstream: [design]`; комментарий-отступление обновить (acceptance/decomposition всё ещё срезаны намеренно) |
| `governance/runner.py` | `_AUTHOR_STEPS` += `("author-design", "design", "20-design.md")` — после behaviour, до tasks-делегата; resume-семантика прежняя. Плюс НОВАЯ гейт-механика S4 (см. оговорку ниже): prospective-проверка ОБОИХ рёбер design → requirements и design → behaviour-spec; `GC-UNPINNED` и `GC-STALE` для КАЖДОГО из двух пинов; локальный гард содержимого design-узла (грамматика §4: покрытие Q, состояния, named-причины) |
| `governance/ops.py` | `_AUTHOR_FILENAMES["design"] = "20-design.md"`; `_AUTHOR_DSL["design"]` — контракт содержимого (§4), включая канонический frontmatter (`spec_stage: design`, `owner_role: architects`, `traces_to: [requirements, behaviour-spec]`, `upstream_hashes` обоих апстримов) |
| `governance/bundle_state.py` | правок не ожидается: required-узлы читаются из профиля; отсутствие 20-design.md в бандле ⇒ `required_absent` на S4 автоматически. Проверить характеризационным тестом |
| `governance/task_bridge.py` | линейная `_BUNDLE_CHAIN` становится DAG-ом upstream'ов: `charter: []`, `requirements: [charter]`, `behaviour-spec: [requirements]`, `design: [requirements, behaviour-spec]`. Порядок штампа обязан быть топологическим: сначала изменяются upstream-файлы, ЗАТЕМ пересчитываются и записываются ОБА пина design — иначе пин design → requirements протухает немедленно после штампа requirements. Tasks-спека получает `traces_to: [design]` + `upstream_hashes.design`; секция «Решения открытых вопросов» **генерируется из 20-design.md**, а не пишется рукой (закрывает бонус devtools#123). ОТДЕЛЬНО: `conform_approved` сегодня принудительно возвращает tasks-спеку к `traces_to: [behaviour-spec]` — после approve она обязана СОХРАНЯТЬ `traces_to: [design]` и пин текущего 20-design.md |
| `templates/` | не трогаем (шаблон стадии живёт в DSL промпта, как у трёх существующих узлов) |

Аппрув — без новой механики (мерж бандл-PR, штамп
`approved_by = mergedBy`). Гейты — С НОВОЙ механикой, и это надо назвать
честно: `gate-check --candidate` пропускает draft-пины, а локальные
гарды сегодня захардкожены ровно на два существующих ребра
(requirements → charter, behaviour-spec → requirements). Для design
добавляются: prospective-проверка обоих его рёбер, `GC-UNPINNED`/
`GC-STALE` на каждый пин по отдельности и локальный гард содержимого
(грамматика §4). S8 gate-authoritative валидирует по пину steward как
прежде.

## 4. Контракт содержимого `20-design.md` (DSL авторинг-промпта)

Обязательные секции:

1. **Резолюции открытых вопросов** — машинная грамматика, не только
   проза (локальный гард S4 парсит её и проверяет ПОКРЫТИЕ — одна
   резолюция не доказывает покрытие трёх входных вопросов):

   ```
   #### Q-NN · owner_role: architects · resolution: resolved|deferred
   <обоснование в границах продуктовых рамок requirements>
   deferred ⇒ обязательная строка `reason: <named-причина>`
   ```

   Гард сверяет множество Q-* в 20-design.md с множеством Q-*,
   переданных в design из requirements (`owner_role: architects`):
   каждый входной вопрос обязан присутствовать ровно один раз в
   состоянии `resolved` либо `deferred`+reason. Если архитектурных Q
   нет — design обязан нести явную строку
   `Открытых архитектурных вопросов нет (входной набор пуст)`, и гард
   принимает пустое покрытие только при ней.
2. **Механика** — точки врезки (модуль/функция), формы инвокаций,
   формы коммитов/эвиденции; то, что requirements сознательно не
   фиксируют («предмет стадии design»).
3. **Рамки red-дизайна** — для задач, чей предмет — замер/артефакт/
   граница (класс TASK-014): что red обязан проверять живьём и чего не
   смеет ассертить (существование файлов и т.п.). Урок spec-runner#360,
   генерализованный.
4. **Карта затрагиваемых модулей** — файлы/подсистемы, которых
   коснётся реализация; вход для будущей decomposition.
5. **Вне объёма** — что design сознательно оставляет исполнителю.

Запреты (в DSL явно): не переоткрывать продуктовые решения requirements;
не расписывать код построчно (это работа исполнителя под TDD); не
плодить новые Q-* без owner_role.

## 5. Тесты

- Характеризация S4: бандл без 20-design.md ⇒ `required_absent`
  (пинованный steward, как существующие S4-тесты).
- `bundle_state`: design в required, статусная модель draft→approved.
- `task_bridge`: цепочка пинов 4 узлов (перепин после штампа), traces_to
  tasks-спеки = [design], генерация секции резолюций из 20-design.md
  (fixture-бандл с Q-резолюциями).
- Смоук S2: author-design создаёт ровно 20-design.md с каноническим
  frontmatter (по образцу существующих author-смоуков).
- Гейты design-узла: отсутствие КАЖДОГО из двух пинов по отдельности ⇒
  `GC-UNPINNED`; протухание каждого пина по отдельности ⇒ `GC-STALE`.
- Штамп: после изменения обоих upstream-файлов записанные пины design
  актуальны (оба пересчитаны, топологический порядок).
- Локальный гард содержимого: неполное покрытие входных Q ⇒ отказ;
  пустой входной набор Q без явной строки-декларации ⇒ отказ, с ней ⇒
  проход.
- `conform_approved`: tasks-спека после approve сохраняет
  `traces_to: [design]` и актуальный пин 20-design.md (регрессия на
  сегодняшний принудительный откат к behaviour-spec).

## 6. Карта артефактов по этапам (сквозная, фиксация по требованию владельца)

| Этап | Владелец роли | Артефакт(ы) | Где живёт | Статус сегодня |
|---|---|---|---|---|
| interview | владелец + агент | диалог/ответы | disputatio-контур | существует (вне этой серии) |
| proposal | product | proposal-документ | disputatio doc-pipeline → PR | спека 2 (позже) |
| charter | product | `00-charter.md` | `workstreams/<ws>/spec/` | есть |
| requirements | product | `10-requirements.md` (+Q-резолюции продуктовые) | там же | есть |
| behaviour-spec | product (reviewer qa) | `15-behaviour-spec.md` (BEH-*, checked_by, матрицы) | там же | есть |
| **design** | **architects** | **`20-design.md` (резолюции Q-*, механика, red-рамки, карта модулей)** | **там же** | **эта спека** |
| acceptance | qa | `25-acceptance.md` | там же | спека 4 (позже) |
| decomposition | tech-lead | `30-decomposition.md`; вход steward-compile | там же | спека 3 (позже) |
| tasks | stream-owner | `spec/<ws>-tasks.md` (managed, draft→approved) | корневой `spec/` репо-цели | есть |
| gate-check | steward (пин) | `.steward/gate_verdicts.jsonl` (gate-verdicts/v1, hash-chain) → `s8-gate-verdicts.jsonl` в run_dir | devtools `out/governance-runs/` | есть |
| реализация | spec-runner | task-ветки, integration-PR, tdd-evidence (checkpoints/claims/waivers), measurements | репо-цель | есть |
| панель | dispatcher | read-only отображение вердиктов | dispatcher | не подключено (отдельный пункт) |

## 7. Независимость и параллельность спек серии

| Пара | Пересечение файлов | Вердикт |
|---|---|---|
| (1) design ↔ (2) interview/proposal | нет: (1) — devtools governance/profiles; (2) — disputatio + вход конвейера ДО charter | параллельны полностью |
| (1) design ↔ (3) decomposition | `profiles/team-exp.yaml` (tasks.upstream), `runner._AUTHOR_STEPS`, `task_bridge` цепочка | последовательны: (3) строится на (1); писать спеку (3) можно параллельно, реализация — после (1) |
| (1) design ↔ (4) acceptance | те же швы, но acceptance-узел ортогонален design (upstream оба из behaviour-spec) | спеки параллельны; реализация (4) может идти параллельно (1) при дисциплине правок профиля (разные строки, один файл — очередность мержа) |
| (2) ↔ (3)/(4) | нет | параллельны |

## 8. Вне объёма

Acceptance- и decomposition-узлы; steward-compile → project.yaml →
Maestro; dispatcher-панель вердиктов; rename design → architecture
(ADR steward); правки самого steward.
