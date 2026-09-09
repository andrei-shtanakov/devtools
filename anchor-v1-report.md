# anchor у v1 — отчёт по ходу работы

Задача: `deliver_for_run` завершает op первой доставки без `anchor`, из-за чего
первое переиздание ЛЮБОГО воркстрима уходит в compatibility-случай §6 вместо
сверки §I5. Чинить в обоих завершениях v1.

## Разбор кода (что нашёл до правки)

- `deliver()` уже несёт хук `after_commit`, который вызывается РОВНО между
  `commit_paths` и `push_branch` и получает `anchor_blob` — «ФАКТИЧЕСКИЙ штамп
  терминального узла, тот же, что ушёл в пин спеки» (комментарий в `deliver`,
  строка ~1102). Это ровно то, что §I2 называет anchor'ом: blob терминального
  узла ПОСЛЕ штампа. Пересчитывать нечего — значение уже вычислено внутри
  доставки (`design_blob`, читается после `stamp_bundle_approved`).
- `_prospective_anchor` считает то же значение на теневой копии НЕштампованного
  бандла — годится для сверки ДО эффектов, но в `deliver_for_run` на момент до
  `deliver()` рабочее дерево ещё не синхронизировано с `base_ref`
  (`checkout_and_pull` живёт ВНУТРИ `deliver`), значит считать его там —
  считать по случайному состоянию чекаута.
- Точка 2 (реконсиляция существующего PR): `approved_by/at` в области видимости
  нет и поднимать их нельзя. Но байты, ушедшие в PR, уже существуют — они лежат
  в head-коммите этого PR. `pr_facts` уже отдаёт `headRefOid`, а `ops` несёт
  примитив `blob_in_commit(target_dir, sha, rel_path)`. Прецедент сравнимости
  `blob_in_commit` и `blob_sha1` уже есть в коде: `_recover_commit` сверяет
  `ops.blob_in_commit(...)` с `op["tasks_blob"]`, записанным через `blob_sha1`.

## Решение

1. **Новая доставка** — `after_commit`-хук ловит `anchor_blob`, `op_complete`
   пишет его как `anchor`. Никаких новых обращений к gh; хук добавляет ровно
   один локальный `git rev-parse HEAD` (его делает сам `deliver`, формируя
   payload) — не отказ ни при каком исходе (`RealOps.rev_parse` без `check=True`).
2. **Реконсиляция** — anchor ЧИТАЕТСЯ из head-коммита принятого PR:
   `blob_in_commit(target_dir, facts["headRefOid"], <bundle>/<терминальный узел>)`.
   Обоснование — см. ниже «Почему именно чтение head-коммита».
   Факт недоступен → `anchor: None`, §6-путь, доставка не падает (fail-open).

## Почему именно чтение head-коммита (точка 2)

- §I2 определяет anchor как байты, которые уходят в PR и после мержа лягут в
  base. На пути реконсиляции эти байты уже СУЩЕСТВУЮТ — в head-коммите PR-а.
  Чтение их — не приближение штампа, а сам доставленный штамп.
- Пересчёт проспективного штампа здесь был бы ХУЖЕ, чем None: он считался бы по
  ТЕКУЩЕМУ состоянию апстрима, а оно могло уже уехать вперёд доставленного
  (реконсилируется чужая, более ранняя доставка). Записанный так anchor заставил
  бы §I5 сказать «апстрим не менялся» там, где он менялся, — fail-open ровно в
  том гейте, ради которого anchor и вводится.
- Пересчёт вдобавок требует `approved_by/at`, поднимать которые запрещено:
  это добавило бы отказ «у PR нет mergedBy/mergedAt» на путь, который сегодня
  успешно реконсилируется.
- Один дополнительный запрос к gh не появляется: `pr_facts` на этом пути уже
  вызывается (для `state`), правка лишь перестаёт выбрасывать остальные поля.

## Журнал

### Правка реализации (`governance/task_bridge.py`)

- новый `_delivered_anchor(state, ops, facts, legacy_bundle)` — anchor уже
  доставленного PR из его head-коммита; `None`, если `headRefOid` не отдан;
- в ветке `if existing is not None` факты PR берутся целиком (`existing_facts`),
  тем же ЕДИНСТВЕННЫМ запросом, что и раньше (минор C-8 не нарушен);
- `op_complete(..., pr=existing, anchor=_delivered_anchor(...))`;
- в новой доставке — локальный хук `_capture_anchor` (`after_commit`), затем
  `op_complete(..., pr=pr, anchor=stamped.get("anchor"))`;
- докстринги обновлены: почему anchor обязателен и почему источники разные.

Проверка: `uvx ruff check --isolated --select E,F,W --line-length 88` по обоим
файлам — чисто (два E501/F841, которые ruff показывает в тестовом файле, живут
на HEAD ветки и к правке отношения не имеют: проверено `git stash`).

### Тесты (`tests/test_governance_task_bridge.py`)

Новых — 5 функций (6 прогонов, один параметризован):

1. `test_deliver_for_run_records_anchor_of_stamp` — новая v1 хранит `pr` И
   `anchor`; ожидание считается ДО доставки через `_prospective_anchor` с той же
   подписью, что отдаёт `pr_facts` бандл-PR (т.е. сверяется не «что-то
   непустое», а именно тот blob, который §I5 будет сравнивать).
2. `test_deliver_for_run_reconciled_pr_records_anchor_from_head` — принятый
   существующий PR даёт anchor, прочитанный из `headRefOid` по пути анкера
   активного DAG (утверждается и сам запрос: `("head-91", ".../30-decomposition.md")`).
3. `test_deliver_for_run_reconciled_pr_without_head_stays_open` — факта нет →
   `anchor: None`, доставка не падает (fail-open, §6-путь сохранён).
4. `test_supersede_unavailable_for_missing_and_null_anchor[ключа-нет|anchor-null]`
   — §6 одинаково срабатывает и на легаси-записи без ключа, и на честном
   `anchor: None` от новой реконсиляции.
5. `test_supersede_right_after_delivery_is_traceless_noop` — сквозной случай:
   `deliver_for_run` → `deliver_superseded` возвращает `None`, `run.json`
   побайтово прежний.

### Мутационная проверка (каждая: применил → тест покраснел → откатил)

| Мутация | Красное |
|---|---|
| `op_complete(..., pr=pr)` без `anchor` | `records_anchor_of_stamp`, `right_after_delivery_is_traceless_noop` |
| убран аргумент `after_commit=_capture_anchor` | те же два |
| `anchor=None` вместо `_delivered_anchor(...)` | `reconciled_pr_records_anchor_from_head` |
| `dag[-1][0]` → `dag[0][0]` (не тот узел DAG) | `reconciled_pr_records_anchor_from_head` |
| убран гвард `if not head: return None` | `reconciled_pr_without_head_stays_open` + два ШТАТНЫХ теста реконсиляции (AttributeError) — гвард держит именно неизменность обычной доставки |
| `if recorded_anchor is None:` → `if "anchor" not in prev_op:` | только `[anchor-null]` |
| `if recorded_anchor is None:` → `if False:` | оба параметра |

Прогоны — с `PYTHONDONTWRITEBYTECODE=1` и чисткой `__pycache__` перед каждым.
После отката файл побайтово сверен с бэкапом (`diff` пуст).

### Тронутые существующие тесты (все три — ассерты формы записи op)

- `test_deliver_for_run_write_ahead_op_and_completion` утверждал
  `ops["tasks-deliver"] == {"status": "completed", "pr": 77}` — ПОЛНЫМ равенством
  словаря. Предмет теста — write-ahead (`started` до `create_draft_pr`) и номер
  PR; полное равенство было побочным способом это записать. Сведён к
  `(status, pr)`; полная форма записи теперь утверждается в новом тесте №1.
- `test_deliver_for_run_adopts_existing_pr_by_branch` и
  `test_deliver_for_run_adopts_merged_pr_not_only_open` — то же полное равенство
  с `{"status": "completed", "pr": 91}`; предмет — «принят существующий PR,
  новый не создаётся». Тоже сведены к `(status, pr)`, anchor этого пути
  утверждают новые тесты №2 и №3.

Ни один существующий тест не утверждал ОТСУТСТВИЕ anchor'а как свойство —
все три падали ровно на «в словаре появился лишний ключ».

### Граница «меняется только полнота durable-метаданных»

- файлы: `deliver()` вызывается с прежними аргументами плюс `after_commit`;
  колбэк ничего не пишет на диск (кладёт строку в локальный dict);
- ветка и PR: `branch`/`version` не передаются — те же дефолты
  (`spec/<ws-id>-tasks`, `version: 1`); `create_draft_pr` не тронут;
- новые пути отказа: ни одного. В новой доставке хук не может бросить; его
  наличие добавляет ровно один локальный `git rev-parse HEAD` (payload собирает
  сам `deliver`), а `RealOps.rev_parse` без `check=True` не бросает и отдаёт
  `None`. В реконсиляции `headRefOid` берётся из уже сделанного запроса, а
  `RealOps.blob_in_commit` при отсутствии объекта отдаёт `None` (тоже без
  `check=True`) — оба случая ведут к `anchor: None`, а не к отказу;
- запросов к gh не прибавилось: `pr_facts` на пути реконсиляции как вызывался
  один раз, так и вызывается.

Итог: в `run.json` у v1 появилось одно поле `anchor`; всё остальное поведение
обычной доставки — прежнее.
