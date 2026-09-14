# E1: вход governance-конвейера из discovery-brief

Дата: 2026-09-13. Статус: draft, на ревью владельца.
Источник решения: принятый план
`prograph-vault/authored/notes/2026-09-13-pipeline-interview-to-implementation-plan.md`,
§3 E1; исполняемый пункт — `TODO.md`
`@id:spec-loop-brief-input`.

## 1. Цель и граница

`make spec-loop` получает новый вход `--brief <path>` и начинает обычный
governance-прогон не с одного `SUBJECT`, а с уже авторенного и проверяемого
discovery-brief. Бриф становится неизменяемым source-слоем того же bundle-PR;
charter и requirements обязаны выводиться из него и пиновать его байты.

Граница author ≠ execute сохраняется буквально:

- `discovery` ведёт интервью и авторит brief, но не открывает продуктовый PR;
- `devtools` принимает готовый brief, проверяет его, переносит в репо-цель и
  открывает governance-конвейер;
- E1 не запускает интервью. Вызов стадии Need и пауза `awaiting_input` — E2.

Обычный `spec-loop` без `--brief` сохраняет шестиузловой бандл и прежнее
поведение. E1 не превращает новый источник в обязательный для исторических и
ручных прогонов.

## 2. Разрешение двух фреймов

План требует живую приёмку engineer-фрейма, но записи `G-NN`/`FR-NN`
определены только customer-фреймом. Это не альтернативные трактовки:
engineer-brief по контракту обязан ссылаться через `traces_to` на approved
customer-brief и содержит системную реальность (`S/IF/CON/AP/RK`) плюс
feasibility-вердикты по Must-FR upstream.

Поэтому E1 принимает оба легальных входа:

| primary frame | источник G/J/FR/NFR | дополнительный контекст |
|---|---|---|
| `customer` | сам primary brief | нет |
| `engineer` | ровно один approved customer-brief из `traces_to` | primary engineer-brief |

Для engineer-входа недостаточно передать модели только primary-файл: тогда
FR пришлось бы пересочинить по feasibility-прозе. Недостаточно и прочитать
customer-файл только с машины оператора: bundle-PR не нёс бы источник, из
которого получены requirements. Оба файла поэтому входят в bundle-PR.

E1-адаптер намеренно уже общего GC-16: ссылка engineer → customer должна быть
ровно одним относительным `*.md`-путём без абсолютного пути и `..`. Несколько
разрешившихся customer-источников неоднозначны; непереносимая ссылка не может
стать durable source. Оба исхода — отказ до run-id, ветки и платного author.

## 3. Вендоренный контракт и входной гейт

В devtools появляется точная пиненая копия из discovery-toolkit:

```text
governance/discovery_contract/
  DISCOVERY-BRIEF-CONTRACT.md
  gate_check.py
  PINNED.txt
```

Начальный upstream pin — тот, который уже доказан runtime discovery:
`ee93092fdfe6195c28c7392d85b41c6b94b9fe0a`. Вендоренные Python-байты не
редактируются локально. Адаптация к spec-loop живёт в отдельном модуле
`governance/brief_input.py`.

Две раздельные гарантии повторяют discovery, не изображают одну другой:

1. PR-check: manifest покрывает весь ожидаемый surface; локальные digest
   совпадают с `PINNED.txt`; при доступном upstream те же байты читаются из
   дерева на pinned commit. Недоступный upstream = `unknown`, не `pass`.
2. Scheduled drift-check: default branch upstream всё ещё указывает на pin;
   уехал — failure, upstream недоступен — `unknown` и ненулевой exit.

`gate_check.check(text, base_dir=path.parent)` запускается до создания
прогона. Pass означает ноль findings уровня `error`; warning не превращается
в error, потому что это сменило бы контракт discovery. Поле
`validation: pass` отдельно проверяется самим GC-15.

Для engineer-входа после primary-гейта адаптер:

- извлекает frame и переносимый path-ref;
- разрешает customer-файл тем же правилом vendored gate;
- требует `interview.frame: customer` и `status: approved` у upstream;
- отдельно прогоняет gate_check на customer-файле;
- отказывает при любой ошибке или неоднозначности.

Диагностика перечисляет `GC-NN`, ref и message; «gate-check не смог
запуститься/прочитать YAML» никогда не читается как pass.

## 4. Размещение source-слоя

Primary-файл переносится побайтово в:

```text
workstreams/<ws-id>/spec/00-discovery/brief.md
```

Для engineer-входа customer-файл переносится побайтово под
`00-discovery/<исходный traces_to path>`. Ограничение §2 гарантирует, что
путь остаётся внутри `00-discovery/`; поэтому неизменённый primary brief
повторно проходит GC-12/GC-16 уже в целевом bundle. Коллизия с `brief.md`
или вторым source-файлом — fail-closed.

Source-слой материализуется новым write-ahead op `materialize-brief` после
создания ветки S1 и до первого author-вызова S2. Коммит S3 коммитит весь
`bundle_dir`, поэтому source и шесть authored-узлов едут одним bundle-PR.
Отдельного brief-PR нет. Source-файлы добавляются в индекс поштучно и
принудительно (`git add -f`): ignore-правила репо-цели (`workstreams/*/spec/*`
с carve-out только `!*.md`) про подкаталог `00-discovery/` не знают, и
обычный `git add -- <bundle_dir>` пропускал его молча (живой прогон
2026-09-14, spec-runner#490). После коммита раннер fail-closed сверяет blob
каждого source-файла в HEAD с descriptor: отсутствие или расхождение ⇒
`stopped_author` с именем файла, push не выполняется.

`RunState` получает обратносуместимое опциональное поле `brief`:

```json
{
  "frame": "customer|engineer",
  "primary": "00-discovery/brief.md",
  "requirements_source": "00-discovery/brief.md|00-discovery/<ref>",
  "source_paths": ["..."],
  "source_blobs": {"discovery-brief": "<git-blob>", "discovery-customer": "<git-blob>"}
}
```

Фактические байты, не переданные хэши, являются источником истины. На resume
каждый записанный blob пересчитывается до author/commit; расхождение — стоп.
После потери локального ledger E0.6a восстанавливает descriptor из файлов
MERGED bundle-PR и пересчитывает хэши, не выдумывая исходный путь машины.

Source-файлы не становятся узлами человеческого approval DAG §I12:
discovery-gate и человеческое одобрение governance-узла — разные акты.
Engineer-brief легально имеет `status: draft` при `validation: pass`, поэтому
принудительное `status: approved` либо автоматический §I12 approve были бы
ложной подписью. Approval DAG остаётся charter → … → decomposition.

## 5. Пины charter и честная одобренность

В brief-enabled прогоне charter несёт source-рёбра:

```yaml
traces_to: [discovery-brief]  # engineer: + discovery-customer
upstream_hashes:
  discovery-brief: "<blob 00-discovery/brief.md>"
  # только engineer:
  discovery-customer: "<blob resolved customer brief>"
```

Это supplemental upstream'ы charter, а не новые profile nodes. Чтобы они не
исчезли при `--approve-node charter`, определение прямых входных blob'ов
выносится в одну функцию `bundle_inputs.direct_blobs(...)`. Её используют:

- S4 prospective source-гейт;
- создание approval intent и candidate bytes;
- финальная сверка candidate/finalize;
- `read_dag_state`, то есть гейт доставки §I12.

Ни один из этих потребителей не сравнивает пины charter с пустым словарём и
не переписывает их только стандартными DAG upstream'ами. Для прогона без
brief функция возвращает прежний пустой набор charter upstream и старые байты
не меняются.

Терминальный anchor tasks-спеки уже транзитивно видит source: requirements
пинует blob charter, дальнейшая цепочка пинов доходит до decomposition.
`content_anchor` дополнительно включает canonical bytes всех `source_paths`;
иначе он вырезал бы `upstream_hashes` как provenance и первое supersede после
изменения brief ошибочно считало бы содержание неизменным.

## 6. Авторинг из source, не по памяти модели

`Ops.author` получает опциональный `BriefContext`; только prompts charter и
requirements используют его.

Customer-вход:

- charter читает primary и переносит G/J/CON/OUT/M/RK как продуктовую рамку;
- requirements читает primary, сохраняет все исходные `FR-NN`/`NFR-NN`, их
  Priority, Acceptance/Target и трассы на существующие G/J/CON.

Engineer-вход:

- обе стадии читают approved customer source как продуктовый источник;
- primary engineer source добавляет S/IF/CON/AP/RK и feasibility-ограничения,
  но не является лицензией придумать или перенумеровать FR.

Промпт называет абсолютные в рамках target repo пути, source blob'ы и правило
«source IDs are immutable». Текст brief не вставляется в argv целиком: агент
читает уже материализованные файлы в worktree, а command-line не становится
второй копией артефакта.

После появления либо обнаружения `10-requirements.md`, но до следующего
платного author-вызова, чистый `brief_guard` проверяет:

- каждый source `FR-NN` присутствует в requirements ровно один раз;
- каждый source Must-FR остаётся Must;
- каждый source `NFR-NN` присутствует ровно один раз;
- downstream не переиспользовал source ID для другого класса;
- requirements frontmatter несёт корректный pin charter (существующий гейт)
  и charter несёт source pins §5.

Точное семантическое равенство свободной прозы машинно не заявляется:
исполняемый гейт держит идентичность/полноту ID и priority, а терминальное
review — смысл переноса. Найденное расхождение пишет
`brief-findings.txt`, ставит `stopped_author` и не покупает behaviour/design/
acceptance/decomposition.

## 7. Изменения по файлам

| Файл | Изменение |
|---|---|
| `governance/discovery_contract/*` | точная vendored copy + pin |
| `tools/check_discovery_vendor.py`, `.github/workflows/*` | integrity и scheduled drift; unknown ненулевой |
| `governance/brief_input.py` | intake, frame/source resolution, portable layout, source descriptors, pure coverage guard |
| `governance/run_state.py` | optional `brief`, совместимый load старых ledger |
| `governance/spec_loop.py` | `--brief`; preflight до run-id; recovery descriptor из bundle-PR |
| `governance/runner.py` | `materialize-brief` между branch и author; source guard сразу после requirements |
| `governance/ops.py` | BriefContext в prompts charter/requirements |
| `governance/bundle_inputs.py` | единый вычислитель supplemental source blobs |
| `governance/approve_node.py`, `governance/task_bridge.py` | approval/delivery используют единый вычислитель; content_anchor включает source bytes |
| `README.md`, `Makefile`, `TODO.md` | CLI, границы E1, evidence живого прогона |

Профиль `team-exp` не меняется: source не является approval-узлом steward.
Vendored discovery brief с неизвестным этому профилю `spec_stage: discovery`
даёт штатный `GC-STAGE warn`, не error; обязательный pass самого brief держит
отдельный vendored gate до S1 и повторная source-сверка S4.

## 8. Тесты и приёмка

### Unit/contract

- customer pass; customer error; warning-only pass;
- engineer pass с ровно одним approved customer upstream;
- engineer без upstream, с draft/non-customer upstream, несколькими refs,
  absolute/`..` path — fail-closed до run-id;
- source bytes сохраняются точно и повторно проходят gate из bundle layout;
- Must-FR отсутствует/понижен/задвоен — named finding; все source FR/NFR
  перенесены — pass;
- vendored manifest нельзя сузить удалением строки; digest/provenance/drift;
  upstream unavailable = unknown, не pass.

### Runner/reconciliation

- branch → materialize-brief → author-charter по порядку;
- source mismatch на resume не вызывает author;
- requirements guard останавливает следующие платные узлы;
- commit включает весь source layer тем же bundle-PR;
- старый ledger без `brief` и обычный CLI без `--brief` байт-в-байт прежние;
- GitHub recovery восстанавливает source descriptor из MERGED bundle-PR;
- approval charter сохраняет и проверяет supplemental pins на proposal,
  finalize и delivery;
- изменение source при supersede меняет `content_anchor`.

### Live acceptance

1. `discovery` выпускает реальный ready/pass engineer-brief с approved
   customer upstream.
2. `make spec-loop ... ARGS='--brief <engineer.md>'` создаёт bundle-PR с
   обоими source-файлами и шестью governance-узлами.
3. После человеческого мержа и §I12 approve всех governance-узлов создаётся
   tasks-PR; source pins остаются в approved charter.
4. Tasks-спека проходит human approve/conform и исполняется spec-runner
   `run --strict` до terminal result.
5. Evidence фиксирует run-id, оба PR, source blob'ы, S8 verdict и итог
   spec-runner; ручного редактирования brief/governance-узлов между стадиями
   нет.

## 9. Вне объёма

Запуск discovery/цикл вопросов (`--need`, E2); customer-интервью внешнего
продукта и ProductProposal (`--proposal`, E3); хранение transcript; изменение
контракта discovery или steward; превращение source в §I12 approval-node;
семантический LLM-judge равенства prose; несколько customer upstream'ов;
кросс-репный продукт Mode-2; миграция исторических bundle/tasks-спек.

