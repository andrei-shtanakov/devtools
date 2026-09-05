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
- **TASK-018**: 7 раундов терминального ревью PR #366 — дизайн
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
| `governance/runner.py` | `_AUTHOR_STEPS` += `("author-design", "design", "20-design.md")` — после behaviour, до tasks-делегата; resume-семантика прежняя (шаг resumable) |
| `governance/ops.py` | `_AUTHOR_FILENAMES["design"] = "20-design.md"`; `_AUTHOR_DSL["design"]` — контракт содержимого (§4), включая канонический frontmatter (`spec_stage: design`, `owner_role: architects`, `traces_to: [requirements, behaviour-spec]`, `upstream_hashes` обоих апстримов) |
| `governance/bundle_state.py` | правок не ожидается: required-узлы читаются из профиля; отсутствие 20-design.md в бандле ⇒ `required_absent` на S4 автоматически. Проверить характеризационным тестом |
| `governance/task_bridge.py` | цепочка штампа/пинов удлиняется: charter → requirements → behaviour-spec → design; tasks-спека получает `traces_to: [design]` + `upstream_hashes.design`; секция «Решения открытых вопросов» **генерируется из 20-design.md** (перенос резолюций Q-*), а не пишется рукой — закрывает бонус-пункт devtools#123 |
| `templates/` | не трогаем (шаблон стадии живёт в DSL промпта, как у трёх существующих узлов) |

Гейты и аппрув — без новой механики: S4 gate-candidate / S8
gate-authoritative валидируют узел по пину steward; аппрув = мерж
бандл-PR (штамп `approved_by = mergedBy`), как у остальных узлов.

## 4. Контракт содержимого `20-design.md` (DSL авторинг-промпта)

Обязательные секции:

1. **Резолюции открытых вопросов** — каждый Q-* с
   `owner_role: architect` из requirements получает решение с
   обоснованием в границах продуктовых рамок; вопрос без решения =
   явный перенос с named-причиной (гейт считает непустоту).
2. **Механика** — точки врезки (модуль/функция), формы инвокаций,
   формы коммитов/эвиденции; то, что requirements сознательно не
   фиксируют («предмет стадии design»).
3. **Рамки red-дизайна** — для задач, чей предмет — замер/артефакт/
   граница (класс TASK-014): что red обязан проверять живьём и чего не
   смеет ассертить (существование файлов и т.п.). Урок PR #360,
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
