# Фикс-круг по итогам двух финальных ревью `feat/supersede-code`

Копия: `devtools-supersede-code`, ветка `feat/supersede-code`, старт с `0812f50`.
Прогон: `uv run --frozen --group governance pytest -q` (ожидание 986 passed).

Формат записи: находка → что сделал → как проверил (в т.ч. что тест краснел на
неисправленном коде).

## `_recover_commit` (§I3.1)

### C-2 (blocker) — `_recover_commit` объявлял чужой собственную ветку ревизии

**Сделал.** В `_recover_commit` (`governance/task_bridge.py`) ветка «коммита ещё
нет» расширена: `if local is None or local == op.get("base_sha"): return`.
Проверил рецепт ревьюера: альтернатива `if op.get("tasks_blob") is None` —
слабее и НЕ эквивалентна (между `before_commit` и `commit_paths` блоб в
намерении уже есть, а коммита ещё нет: ветка снова была бы объявлена чужой).
Взял сверку с `base_sha`; на неё же написан отдельный тест.

**Тесты.**
- `test_recover_commit_branch_at_base_means_no_commit_yet` — окно
  `ensure_branch … commit_paths`, `tasks_blob` пуст;
- `test_recover_commit_branch_at_base_after_blob_written` — то же окно ПОСЛЕ
  `before_commit` (различает мой фикс от предложенного ревьюером);
- `test_supersede_resumes_when_branch_stands_on_base` — прод-путь целиком:
  `deliver_superseded` доводит ревизию (RC-PR 77), v3 не заводится.

**Краснели на старом коде:** да, все три (`RuntimeError: чужой коммит в ветке
spec/WS-alpha-7-tasks-v2` при откате правки кода при сохранённых тестах).

### C-3 (minor) — сообщение обещало remote, сверяется локальный head

**Сделал.** Убрал «remote/» из текста отказа; рядом — комментарий, почему
remote тут не сверяется (non-ff push, осознанное ограничение (б)).

**Тест.** `test_recover_commit_moved_branch_message_claims_only_local_head` —
утверждает `"remote" not in str(exc.value)`. Краснел на старом коде.

### F-03 (minor) — возврат `_recover_commit` в проде мёртв

**Сделал.** Честный гвард: аннотация `-> None`, все не-fail-closed строки
`return` без значения, докстринг переписан. **Выбор в пользу гварда, а не
«использовать возврат»:** после C-2 возврат не становится нужным — все строки
§I3.1, кроме fail-closed, ведут в одну и ту же детерминированную доставку из
намерения. «Принять коммит» из §I3.1 исполняет она сама: `commit_paths` на
неизменившемся файле второго коммита не делает, а `after_commit` durable пишет
`head_sha` именно этого коммита. Push конкретного SHA нашим примитивом
(`push_branch` пушит ветку) невыразим — то есть возвращать SHA было некуда.

**Тест.** Оба существующих теста на принимающую ветку теперь утверждают
`is None` (`test_recover_commit_accepts_matching_local_commit`, хвост
`test_supersede_records_tasks_blob_before_commit`) — мутация M11
(`return "phantom"`) с этими утверждениями краснеет.

## §I7 / §I3 — провенанс и первая доставка

### C-5 (major) — `--approval-pr` не проверял, что PR менял файл анкера

**Сделал.** В ветке явного флага `_resolve_correction_pr` добавлена третья
проверка шага 4 §I7: `if anchor_rel not in ops.pr_files(state.repo_slug, number)`
→ отказ. Рецепт ревьюера принят как есть (примитив `pr_files` уже был в `Ops`);
текст отказа дописан до формулировки контракта «флаг заменяет поиск, но не
проверку».

**Тесты.** `test_provenance_explicit_flag_requires_anchor_change` (PR трогал
только `docs/README.md` → отказ) + обратная сторона
`test_provenance_explicit_flag_accepts_pr_touching_anchor` (иначе проверку можно
было бы «закрыть» отказом на всём подряд). Стаб `_ProvOps` получил честный
`pr_files` с настраиваемым составом.

**Краснел на старом коде:** да — `DID NOT RAISE RuntimeError`.

### C-1 (major) — §I3 «`completed` | OPEN → вернуть PR» была недостижима для v1

**Сделал.** Руллинг владельца прогона исполнен: чиню по контракту. Новая
функция `_reconcile_v1(state, ops, op)` разбирает легаси-запись по её
ЗАПИСАННОМУ номеру PR (`op["pr"]`, его пишет `op_complete(state,
"tasks-deliver", pr=pr)`): OPEN → вернуть номер (RC 0, ревизия не заводится,
леджер не тронут), CLOSED-unmerged → fail-closed строкой §I3 «любое |
CLOSED-unmerged», MERGED → как раньше. Номера PR в записи нет (древняя
запись) → как раньше. Вызов — в `deliver_superseded` сразу после
`prev = _last_delivery(state)` и ТОЛЬКО при `prev_n == 1`, то есть до
`_previous_dag`, `_resolve_correction_pr` и `_previous_tasks_version`.

Через `_reconcile_revision` легаси-запись не гоняется (у неё нет `branch` /
`base_sha` / `head_sha`) — как и предписал владелец прогона.

**Отступление от рецепта:** гейт именно `prev_n == 1`, а не «разобрать v1
всегда». Если последняя доставка — ревизия v≥2, висящий PR v1 к переизданию
отношения не имеет и блокировать его не должен; на это есть контрольный тест
`test_supersede_ignores_first_delivery_pr_when_revision_is_last` (он зелёный и
до, и после фикса — он ловит не находку, а моё возможное превышение).

**Тесты.** `test_supersede_returns_open_pr_of_first_delivery` (RC-PR 5, v2 не
заведена, `run.json` побайтово прежний) и
`test_supersede_refuses_when_first_delivery_pr_closed_unmerged`.

**Краснели на старом коде:** да, оба — старый код доходил до доставки и
возвращал 77 (в фикстуре спека в base есть, поэтому отказ про версию не
срабатывал, и дефект проявлялся ВТОРЫМ открытым PR на ту же спеку).

## CLI и запросы к GitHub

### C-6 (minor) — `--conform-approve` молча проигрывал

**Сделал.** Гвард `parser.error` доведён до конца: `--conform-approve` вместе с
`--supersede` или `--abandon-revision` — отказ с той же мотивировкой («прогон
делает ОДНО действие»).

**Тест.** `test_cli_conform_approve_with_other_action_refuses` (параметризован
обоими сочетаниями): `SystemExit.code == 2` и упоминание флага в stderr.
Краснел на старом коде (без гварда CLI шёл дальше и падал на `load`).

### C-7 (minor) — `--approval-pr` без `--supersede` принимался и игнорировался

**Сделал.** Отдельный `parser.error`: флаг осмыслен только внутри переиздания.

**Тест.** `test_cli_approval_pr_without_supersede_refuses` — `code == 2` +
`--approval-pr` в stderr. Краснел на старом коде.

### C-8 (minor) — четыре запроса к GitHub вместо одного

**Сделал.** `_reconcile_revision` больше НЕ ходит в `ops`: сигнатура стала
`(n, op, base_sha, pr, facts)` — цикл и так получил и номер PR, и его факты, и
передаёт их аргументом. Логика таблицы §I3 не тронута ни в одной ветке
(изменён только источник данных); побочно решение стало чистой функцией —
таблица §I3 теперь проверяется без стабов ops.

**Отступление от рецепта:** ревьюер предлагал «сохранить факты в переменную
внутри `_reconcile_revision`» — это убрало бы два запроса из трёх-четырёх, но
`find_pr`+`pr_facts` цикла всё равно дублировались бы. Убрал ввод-вывод из
функции целиком.

**Тесты.** Семь юнитов §I3 (M01–M07 у ревьюера) переписаны на новую сигнатуру —
ветвление то же; добавлен восьмой, `test_reconcile_started_no_pr_same_base_continues`
(без него «база сдвинулась» и «не сдвинулась» при отсутствующем PR
неразличимы). Счёт запросов закреплён отдельно:
`test_supersede_asks_github_once_per_revision` — ровно один `find_pr` и один
`pr_facts` на ревизию. Что реальный запрос вообще делается — держит
`test_supersede_returns_existing_pr_when_revision_completed` (в него добавлено
утверждение про `find_pr`).

**Краснел на старом поведении:** проверено мутацией — вернул в цикл повтор
`find_pr` + два `pr_facts` (ровно старый паттерн), тест упал; после отката —
зелёный.

## Общее

- **Линтеры/тайпчекер.** `ruff` и `pyrefly` в этом репо не подключены (нет ни в
  `pyproject.toml`, ни в `Makefile`, бинаря в среде нет). Прогонять их не стал —
  форматирование всего файла ruff'ом дало бы диф, не относящийся к фикс-кругу.
  Вместо этого проверил вручную: ни одна ДОБАВЛЕННАЯ строка не длиннее 88
  символов, аннотации и докстринги на месте, стиль — как в окружающем коде.
- **Осознанно принятые ограничения (а)–(д) не трогал.** `deliver_for_run` и
  обычный путь доставки не изменены ни на строку; `anchor` в `deliver_for_run`
  не пишется (отдельное решение владельца); `tests/test_governance_ops.py` не
  трогал.
- **Итоговый прогон:** `uv run --frozen --group governance pytest -q` →
  **1001 passed** (986 базовых + 12 моих новых + 3 от параллельного
  исполнителя в `tests/test_governance_ops.py`).

## Мелочь, найденная по ходу (не из ревью)

Гвард C-6 сначала проверял `args.abandon_revision` на истинность: номер `0`
argparse разбирает, и он falsy — сочетание, объявленное отказом, прошло бы
молча. Заменено на `is not None` (коммит `b008539`).
