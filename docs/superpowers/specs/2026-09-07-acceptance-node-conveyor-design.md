# Acceptance-узел в behaviour-конвейере (спека 4 серии «полный team-exp»)

Дата: 2026-09-07. Статус: draft, на ревью владельца.
Серия: спека 4; строится на спеках 1 (design, влита devtools#142,
реализация #145) и 3 (decomposition, влита #144, реализация #147).
Завершает основной контур профиля team-exp: после неё конвейер несёт все
узлы steward-пина, кроме сознательно не реализуемого compile.

## 1. Мотивация

Сегодня приёмка живёт в двух местах и ни одно не является артефактом под
гейтом:

- критерии приёмки размазаны по requirements (`*Критерий приёмки*:` —
  проза внутри FR, машинно не извлекается) и чартеру (AC-нумерация есть
  только там, и то как список);
- решение «что считать доставленным» принимает исполнитель задачи в
  момент зелёного теста — QA-роль в конвейере не представлена вовсе,
  различие «тест прошёл» и «критерий приёмки выполнен» не оформлено.

Боевые доказательства из WS-367: AC-11 чартера разошёлся с NFR-01
requirements на базовой точке ($10.26 против $5.80) и был пойман только
терминальным ревьюером на круге 3 tasks-спеки — гейта, сверяющего
приёмочные утверждения между узлами, не существует. Acceptance-узел
делает приёмку отдельным QA-артефактом с машинной грамматикой и
гейтом покрытия.

## 2. Форма узла (пин steward)

Из `steward/profiles/team-exp.yaml` (тот же пин, что спеки 1/3):

```yaml
- {id: acceptance,    template: acceptance.md,    owner_role: qa,
   upstream: [requirements, behaviour-spec]}
- {id: decomposition, template: decomposition.md, owner_role: tech-lead,
   upstream: [design, acceptance]}
```

Отступлений от steward-формы для самого узла НЕТ — upstream'ы acceptance
берутся полными (оба ребра, как у design). Единственное изменение
соседа: `decomposition.upstream` расширяется `[design]` →
`[design, acceptance]` — это та самая однострочная правка, которую §7
спеки 3 заранее отдал «второй из спек 3/4».

Файл бандла — `25-acceptance.md` (номерная конвенция: между 20-design и
30-decomposition; порядок ФАЙЛОВ не диктует порядок авторинга — acceptance
не зависит от design, см. §4 про топологию).

## 3. Контракт содержимого `25-acceptance.md`

Frontmatter: `spec_stage: acceptance`, `status: draft`,
`owner_role: qa`, `traces_to: [requirements, behaviour-spec]`,
`upstream_hashes: {requirements: "<blob 10-requirements.md>",
behaviour-spec: "<blob 15-behaviour-spec.md>"}`.

Обязательные секции (машинная грамматика — источник гейта и справочной
секции tasks-спеки):

1. **Критерии приёмки** — каждый:

   ```
   #### AC-NN: <название> · verification: test|manual|metric
   traces: [FR-…|NFR-…]          # покрываемые требования (оба класса
                                 # id requirements), ≥1
   scenarios: [BEH-…]            # опорные сценарии; ОБЯЗАТЕЛЕН при
                                 # verification: test, иначе опционален
   <проза: наблюдаемый признак выполнения, границы>
   ```

   - `verification: test` — критерий доказывается зелёными сценариями из
     `scenarios` (те уже несут checked_by-биндинги в behaviour-spec);
   - `verification: manual` — критерий проверяет человек по названному
     наблюдаемому признаку (проза обязана называть, ЧТО наблюдать);
   - `verification: metric` — критерий сравнивает измеренную величину с
     объявленной базой; проза обязана называть источник числа
     (артефакт/константу), а не зашивать число в критерий.

2. **Инварианты покрытия** (проверяются гейтом):
   - каждое `Must`-требование requirements ОБОИХ классов (FR и NFR)
     покрыто хотя бы одним AC (`Should` и ниже — по решению qa,
     непокрытость — не находка); NFR — полноправный объект приёмки:
     мотивация узла (§1) — ровно расхождение по NFR-01, и
     `verification: metric` получает легальный upstream (major круга 2);
   - каждый AC трассируется только к существующим FR/NFR; `scenarios` —
     только к существующим BEH;
   - достоверность входного набора (major круга 2, канон design_guard):
     near-miss на стороне requirements — заголовок `#### FR-NN`/
     `#### NFR-NN` без распознанной строки `**Priority**: …` — находка
     («входное множество недостоверно»); ПУСТОЕ множество
     Must-требований — находка, если в 25-acceptance.md нет явной
     строки-декларации `Must-требований во входном наборе нет` (тихое
     «нечего проверять» неотличимо от промаха грамматики);
   - AC-id уникальны; near-miss заголовок — находка (уроки PR #145/#148:
     границы блока — следующая секция уровня 1–3; суффикс id — не более
     одной строчной буквы, `[a-z]?`);
   - `verification: test` без непустого `scenarios` — находка (тестовый
     критерий обязан называть свои сценарии).
3. **Порог приёмки** — какие AC обязаны быть выполнены до объявления
   workstream'а доставленным (по умолчанию: все с `verification: test`;
   manual/metric — перечислением). Секция прозы, машинно не судится —
   потребитель человек на финальной приёмке.
4. **Вне объёма** — что сознательно не является критерием приёмки.

Чартерная AC-нумерация НЕ мигрирует автоматически: acceptance-узел
авторится заново от requirements/behaviour (чартера в upstream нет —
устаревшие чартерные цифры не тянутся в приёмку, класс расхождения
AC-11/NFR-01 из §1 закрывается тем, что единственный источник приёмки —
этот узел).

## 4. Изменения (devtools, по швам спек 1/3 — все data-driven)

Топология: acceptance НЕ зависит от design (upstream — requirements и
behaviour-spec, как у design), поэтому в порядке авторинга/штампа он
может стоять до или после design; фиксируем ПОСЛЕ design (порядок файлов
= порядок авторинга, меньше сюрпризов оператору), а рёбра говорят правду
о независимости.

| Файл | Правка |
|---|---|
| `profiles/team-exp.yaml` | +узел acceptance (форма §2, ПОЛНЫЕ upstream'ы `[requirements, behaviour-spec]`); `decomposition.upstream: [design, acceptance]`; комментарий-отступление сужается до «compile не реализуется» |
| `governance/runner.py` | `_AUTHOR_STEPS` += `("author-acceptance", "acceptance", "25-acceptance.md")` МЕЖДУ author-design и author-decomposition; preflight-цикл `("design", "decomposition")` += `"acceptance"` (И В runner._step_authoring, И ВТОРОЙ кортеж в task_bridge.deliver — minor круга 2: deliver-preflight держит свой список узлов активного DAG); `_GATE_EDGES` += ТРИ required-ребра: `("25-acceptance.md", "requirements", …, True)`, `("25-acceptance.md", "behaviour-spec", …, True)` И `("30-decomposition.md", "acceptance", "25-acceptance.md", True)` — тест-деривация из `_BUNDLE_DAG` строит ожидание по ВСЕМ парам (файл, upstream), включая новый upstream decomposition (major круга 1 ревью спеки); GC-COMPLETENESS-цикл node_paths += `("acceptance", "25-acceptance.md")`; DSL-EMPTY-кортеж += (`^####\s+AC-\d+`, форма `#### AC-NN: <название> · verification: …`); новый локальный гард `GC-AC-COVERAGE` — инварианты §3 через `acceptance_guard.coverage_findings(requirements_text, behaviour_text, acceptance_text)` |
| `governance/ops.py` | `_AUTHOR_FILENAMES["acceptance"]`, `_AUTHOR_DSL["acceptance"]` — контракт §3 с грамматикой AC (канон записей design/decomposition; языковой стиль промпта — англ.); `_AUTHOR_DSL["decomposition"]` ОБНОВЛЯЕТСЯ под второй upstream: `traces_to: [design, acceptance]`, `upstream_hashes: {design: "<hash20>", acceptance: "<hash25>"}` (канон двухпинового design) — иначе авторенный 30-decomposition.md стопил бы S4 на GC-UNPINNED по новому required-ребру после всех оплаченных вызовов (minor круга 1) |
| `governance/console_model.py` | `PIPELINE_KEYS` += `author-acceptance` между author-design и commit-цепочкой decomposition; двусторонний тест порядка уже держит вставку |
| `governance/acceptance_guard.py` (новый) | чистый модуль (канон design_guard/decomposition_guard): `parse_ac_criteria(text) -> tuple[list[AcCriterion], list[str]]` (frozen dataclass: ac_id, title, verification, traces, scenarios) + `coverage_findings(req_text, beh_text, acc_text) -> list[str]`; Must-парсер — по грамматике `#### FR-NN:`/`#### NFR-NN:` + `**Priority**: Must` requirements, near-miss заголовок без распознанного Priority — находка, пустое множество Must — находка без явной декларации; near-miss/дубли/границы блока AC — с первого дня (уроки PR #145/#148) |
| `governance/task_bridge.py` | `_BUNDLE_DAG`: += `("25-acceptance.md", ("requirements", "behaviour-spec"))` ПЕРЕД decomposition; узел decomposition — `("30-decomposition.md", ("design", "acceptance"))` (второй пин; штамп-механика двух пинов уже есть у design). `--legacy-bundle` расширяется до `3|4|5` (3 = до design; 4 = +design; 5 = +decomposition БЕЗ acceptance — текущая эра, все существующие 5-узловые бандлы; полный DAG = 6 узлов) — проверка ТОЧНОГО состава та же; ВНИМАНИЕ: legacy=5 — состав `00/10/15/20/30` (без 25), НЕ префикс DAG по порядку файлов — `_dag_for` для 5 обязан вернуть текущий 5-узловой DAG (decomposition c upstream `("design",)`), а не срез нового. ОБА DT-специфичных ветвления `deliver` (валидация `graph_findings` + выбор `render_tasks_dt` vs `render_tasks`) переводятся с литерала `legacy_bundle is None` на признак «узел decomposition входит в активный DAG» (канон уже в файле — так ветвится design_text): legacy=5 несёт решённую декомпозицию и ОБЯЗАН идти DT-путём с валидацией графа — иначе тихая регрессия: невалидный граф доставляется молча, а tasks рендерится BEH-группировкой с _merge_featureless вместо 1 DT = 1 задача и без Mode: verify_first (major круга 1 ревью спеки); справочная секция tasks-спеки: после «Решений открытых вопросов» рендерится «Критерии приёмки (уровень acceptance)» из 25-acceptance.md (id, verification, названия; полный текст живёт в бандле) — на legacy-путях секции нет |
| переходный режим | бандл без 25-acceptance.md ⇒ тот же fail-closed RuntimeError с процедурой (доавторить либо `--legacy-bundle=5`); словарь `3|4` зашит в ЧЕТЫРЁХ операторских текстах — обновить все (minor круга 2): текст RuntimeError `_check_bundle_composition`, `ValueError` в `_dag_for`, help `--legacy-bundle` CLI, строка `make help` в Makefile |

Rollout target-профилей — решение Task 8 спеки 1: preflight fail-closed;
профили соседей обновляются до 6-узловых отдельными PR (authority-root,
человеческий мерж) до первого прогона с acceptance.

Влияние на decomposition-гейт: GC-DT-GRAPH не меняется (DT-грамматика
сценариев — BEH-ось); связь DT↔AC не вводится этой спекой (см. §8).

## 5. Тесты

- Профиль: узел acceptance, оба upstream'а; `decomposition.upstream ==
  [design, acceptance]`; real-steward topo_order из 7 узлов.
- Грамматика AC: парс всех трёх verification; near-miss/дубль/границы
  блока; test без scenarios ⇒ находка формы.
- Покрытие: непокрытый Must-FR ⇒ находка; непокрытый Must-NFR ⇒
  находка; AC c `traces: [NFR-…]` легален; покрытый Should не требуется;
  ссылка на несуществующий FR/NFR/BEH ⇒ находка; near-miss FR/NFR без
  распознанного Priority ⇒ находка; пустое множество Must без
  строки-декларации ⇒ находка, с декларацией ⇒ чисто.
- S4: рёбра acceptance→requirements и →behaviour-spec — UNPINNED/STALE/
  undeclared по отдельности (локальный гард, канон спеки 1); отсутствие
  узла ⇒ GC-COMPLETENESS(acceptance); DSL-EMPTY; GC-AC-COVERAGE
  интеграционно.
- Мост: decomposition с двумя пинами (design + acceptance) штампуется
  топологически; tasks-спека несёт справочную секцию AC; `--legacy-bundle=5`
  принимает ровно состав `00/10/15/20/30` и НЕ принимает 6-узловой;
  `--legacy-bundle=5` идёт DT-путём: невалидный граф DT ⇒ RuntimeError
  из graph_findings, валидный ⇒ render_tasks_dt (DT-провенанс в задачах,
  Mode: verify_first у verify-DT) — не легаси-рендер;
  `=4`/`=3` не регрессируют (без 30 — прежний render_tasks); полный DAG
  без 25-acceptance.md ⇒ RuntimeError с процедурой.
- S4: ребро decomposition→acceptance — UNPINNED/STALE/undeclared наравне
  с рёбрами acceptance→requirements/behaviour-spec.
- Сверка PIPELINE_KEYS ↔ _AUTHOR_STEPS и `_GATE_EDGES` ↔ `_BUNDLE_DAG`
  зелёные после правки всех сторон (производная required: `_node_id in
  {"design", "decomposition", "acceptance"}` — рёбра acceptance обязательные).
- Preflight acceptance: профиль target без узла ⇒ stopped_preflight до
  первого оплаченного вызова; deliver-preflight — узлы активного DAG.
- Сквозной смоук: 6-узловой прогон start→completed→deliver, tasks-спека
  с секцией AC; негативный полукруг — непокрытый Must-FR ⇒ stopped_gate
  с GC-AC-COVERAGE.

## 6. Карта артефактов — дельта к §6 спек 1/3

| Этап | Владелец | Артефакт | Статус |
|---|---|---|---|
| acceptance | qa | `25-acceptance.md` (AC-критерии, verification, порог) | эта спека |
| decomposition | tech-lead | получает второй upstream-пин (acceptance) | правка этой спекой |
| tasks | stream-owner | + справочная секция «Критерии приёмки» | правка моста этой спекой |

## 7. Зависимости и параллельность

- **Спека 4 ← спеки 1+3 (реализация):** жёсткая — те же data-driven
  списки (`_AUTHOR_STEPS`/`_GATE_EDGES`/`_BUNDLE_DAG`/preflight-циклы),
  которые они создали; всё сводится к добавлению строк в существующие
  структуры + новый guard-модуль.
- **Спека 4 ⊥ спека 2 (interview/proposal, disputatio):** полная; спека 2
  авторится/ревьюится параллельно, интеграция в операторскую кнопку —
  после фиксации основного контура (решение владельца 2026-09-07).
- **После спеки 4:** полный смоук цепочки charter→requirements→behaviour→
  design→acceptance→decomposition→tasks, затем `make spec-loop` + запуск
  по кнопке как тонкая операторская обвязка (порядок владельца).

## 8. Вне объёма

Steward-compile; миграция чартерных AC-списков старых бандлов; связь
DT↔AC в decomposition-грамматике (возможное будущее ребро «DT доставляет
AC-NN» — отдельным решением после первого боевого прогона); финальная
машинная приёмка workstream'а по порогу §3.3 (сегодня порог — документ
для человека); правки steward; спека 2.
