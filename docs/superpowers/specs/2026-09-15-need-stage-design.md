# E2: стадия Need вызывается прогоном — `spec-loop --need`

Дата: 2026-09-15. Статус: accepted (владелец, 2026-09-15, после ревизии 3).
Ревизия 7 (2026-10-08) — **engineer-маршрут** (§11): вариант А владельца — engineer
отдельным прогоном, D2/D5 сохраняются, новой машины состояний нет; customer
`--brief-only` → терминальный `brief_ready`; акт одобрения customer-брифа — мерж
brief-PR (бриф + заявка на одобрение) учёткой человека, зеркало — `discovery approve`
(discovery#57); доказательства происхождения — только факты форджа. Одобрена
владельцем 2026-10-08 как основа для планирования. История проверки: пять кругов
локального ревью Codex; пятый круг — одна находка; исправлена в rev 6, отдельного
повторного круга не было. При реализации T21c (§11.7) проверяет настоящий адаптер на
ответах форджа. Заявка соседу — discovery#63 (§11.9).
Уточнение ревизии 7 (2026-10-08, решение владельца по ревью плана, находка A3): файл
блокировки прогона — `<RUNS_ROOT>-locks/<run_id>.lock`, а не `run_dir/.lock`
(§11.4.5): блокировка берётся до создания каталога прогона, и крах между захватом
и резервированием не оставляет каталога без `run.json`, который `find_runs`
обязан считать битым леджером. Правило «нечитаемый леджер — отказ» не меняется.
Ревизия 6 (2026-09-20) — §9 разделена на три критерия по решению владельца:
доказанная половина больше не держится незасчитанной из-за недоказанной.
Основание — `prograph-vault/authored/notes/2026-09-20-pipeline-and-polygon-decisions.md`,
решение 1.
Ревизия 5 — уточнения из ревью PR #244 внесены, customer-маршрут реализован
(PR #246, ветка feat/need-stage).
Ревизия 4 — по терминальному ревью #244: роль — декларация (сверка ролей только
при attach), повтор после `--new-run` с `--run-id`, `--need` против прогона без
`interview` — отказ, детерминизм рендера подтверждён (`generated_at = created_at`).
Ревизия 3 — `status` 20 из `stopped_interview` возвращает в `waiting_interview`;
crash-окно без tmp и без `brief.md`; уточнение §7 про `start`/`status`/`brief`.
Ревизия 2 — по ревью владельца спеки (четыре
разрыва: stop без сессии, тотальность таблицы, `--new-run --ws-id`, условность
need-флагов; минорные: канонический synthetic envelope, подсказка при исчезнувшей
сессии, smoke по всему банку, алгоритм `upstream_blob`). Дизайн согласован по
секциям в сессии 2026-09-15; решения владельца зафиксированы ниже.
Источник: принятый план
`prograph-vault/authored/notes/2026-09-13-pipeline-interview-to-implementation-plan.md`,
§3 E2 и §4 (Need только с реальным стейкхолдером; публикуется только бриф;
окно до брифа — принятое локальное исключение). Исполняемые пункты —
`TODO.md` `@id:spec-loop-need-stage` (customer) и
`@id:spec-loop-need-engineer-route` (engineer; ожидание discovery#49 снято
2026-09-18 — см. поправку §5.6).

## 1. Цель и граница

`make spec-loop` получает вход `--need`: стадия Need (интервью discovery)
запускается **прогоном**, прогон умеет остановиться в ожидании человека и
продолжиться повтором той же команды, а полученный бриф входит в конвейер
ровно путём E1. Тем самым закрывается триггер, ради которого discovery
получил runtime: «вызываемость стадии прогоном».

Граница author ≠ execute сохраняется: discovery ведёт интервью и авторит
бриф; devtools вызывает его публичный CLI, хранит координаты сессии, забирает
бриф и открывает конвейер. Протокол ответов spec-loop **не оборачивает**
(решение владельца, вариант A): на паузе он печатает точную команду
`discovery answer …` и завершается, ответ даёт человек в своём терминале.
Прямая запись в `$DISCOVERY_HOME` и чтение приватного `header.json`
исключены.

Два маршрута плана: внутренний (engineer-фрейм) и внешний (customer). Фрейм
— параметр вызова, не ветвление логики: одна машина состояний, различаются
argv `start` и preflight входа.

## 2. Решения владельца (2026-09-15)

| # | Решение |
|---|---|
| D1 | Вариант A: spec-loop водит `start → status → brief`, ответы — человек через `discovery answer`; B (обёртка ответов) не вводим, C (интерактив) исключён |
| D2 | `--frame customer\|engineer` явный, без дефолта; customer без `--traces-to`, engineer требует `--traces-to <approved customer-brief>`; C (два интервью в одном прогоне) не вводим |
| D3 | `--stakeholder <role>` обязателен: декларация оператора в `run.json`, подставляется в команду ответа; без него отказ до изменения состояния с подсказкой `--brief` |
| D4 | Подход 1: стадия внутри раннера, `waiting_interview` — полноценное персистентное состояние; до готовности брифа ветка, worktree и авторинг не создаются |
| D5 | Итоговый бриф остаётся `status: draft`, отдельного approval-стопа нет. Честно: мерж бандл-PR и §I12 одобряют governance-узлы, не discovery-источник; автоматически бриф в `approved` не превращается. Если customer-brief позже станет upstream для engineer — отдельный явный акт approval, отдельный TODO-контур. Контур заведён 2026-09-20: `TODO.md` `@id:discovery-brief-approval-act` (акт и место подписи — решение владельца). **Ревизия 7:** акт — мерж brief-PR формы §11.3 (бриф + `approval-request.yaml`) учёткой человека; зеркало — `discovery approve` через `make brief-approve` (§11.5) |
| D6 | Engineer-маршрут до discovery#49 fail-closed до run-id; customer-маршрут полностью рабочий. **С 2026-09-18 ожидание кончилось** (discovery#50 доставил приём upstream и caller-assigned session id): отказ остаётся верным, но его причина — «не реализован», а не «ждёт соседа»; текст `ENGINEER_BLOCKED` правится вместе с реализацией маршрута (`TODO.md` `@id:spec-loop-need-engineer-route`). **Ревизия 7:** маршрут — по §11; `ENGINEER_BLOCKED` и отказ порта на непустом `upstream_path` удаляются |

## 3. Интерфейс

```
make spec-loop SUBJECT='…' REPO=… \
  ARGS='--need --frame customer|engineer --stakeholder <role> \
        [--traces-to <file>] [--session <id>] [--new-run]'
```

Preflight — весь до run-id, до леджера, до любого вызова соседа. Все
need-специфичные флаги (`--frame`, `--stakeholder`, `--traces-to`,
`--session`, `--new-run`) **существуют только вместе с `--need`**: без него
любой из них — отказ; существующие запуски (`--brief`, legacy без источника)
не меняются.

- `--need` и `--brief` взаимоисключающи.
- При `--need`: `--frame` обязателен, без дефолта.
- При `--need`: `--stakeholder` обязателен. Отказ без него называет правило владельца
  («стадия Need запускается только при наличии реального стейкхолдера») и
  маршрут через `--brief`. Значение только записывается — это декларация,
  не машинная проверка.
- **customer**: `--traces-to` запрещён (лишний вход — отказ, не игнор).
- **customer, ревизия 7**: `--brief-only` (только с `--need --frame customer`) —
  после публикации брифа терминальный `brief_ready` вместо S1 (§11.2).
- **engineer, ревизия 7**: интерфейс и preflight — §11.4.1–§11.4.2 (`--traces-to`
  оператора уходит соседу как `--upstream <durable upstream.md>` без `--traces-to`;
  обязателен `--approval-pr <n>`; запуск рядом с customer-прогоном —
  `--new-run --ws-id`). Абзац ниже — история до ревизии 7.
- **engineer** (до ревизии 7): ровно один `--traces-to <file>`. Preflight: gate pass;
  `interview.frame: customer`; **явная проверка `status: approved`** (у
  `inspect_brief` для customer-брифа её нет — она проверяется только при
  разборе engineer-брифа, `brief_input.py:158`); переносимость ссылки (та же
  проверка, что у E1). Три вещи разводятся явно: исходный файл оператора;
  durable-копия `run_dir/brief-input/00-discovery/<basename>` с hash в
  `interview.upstream_blob`; переносимый `traces_to`, который discovery
  запишет в итоговый бриф и который E1 разрешит относительно самого брифа в
  том же каталоге. **До discovery#49 маршрут отказывает до run-id** с
  текстом «engineer-маршрут ждёт discovery#49» (D6): discovery разрешает
  `traces_to` только внутри каталога сессии (`_resolve_ref` в вендоренном
  `gate_check`), публичного способа принять upstream у него нет.
- `--session <id>` — только recovery (§5.3). `--new-run --ws-id <fresh-id>` —
  §5.4; `--new-run` взаимоисключающ с `--run-id` и `--session`.
- Повтор команды находит прогон по (repo, subject), как сейчас. Повтор с
  иными `--frame`/`--stakeholder`/`--traces-to` при леджере в
  `waiting_interview` или `stopped_interview` — отказ: координаты интервью
  зафиксированы стартом; сменить их — `--new-run`.
- `--need` при найденном прогоне с `interview is None` (создан через
  `--brief`, legacy без источника, либо восстановлен из GitHub по
  bundle-PR) — отказ с подсказкой `--new-run --ws-id <fresh-id>`: у такого
  прогона стадии Need не было и быть не может.

## 4. Состояние

`RunState.interview: dict | None`:

| поле | значение |
|---|---|
| `session_id` | id сессии discovery; `None` до успешного `start` |
| `frame` | `customer` \| `engineer` |
| `stakeholder_role` | декларация D3; подставляется в `--role` |
| `target` | `repo_slug` цели — то, что уходит в `discovery start --target` и стоит в H1 брифа |
| `traces_to` | переносимая ссылка (относительное имя) или `null` |
| `upstream_blob` | git-blob SHA-1 durable-копии upstream (`blob_sha1_bytes`, тот же алгоритм, что у `source_blobs` E1) или `null` |
| `brief_rel` | `brief-input/00-discovery/brief.md` |
| `started_at`, `completed_at` | метки; `completed_at` = момент `state.brief` |
| `brief_only` | ревизия 7: customer `--brief-only` (§11.2); координата интервью |
| `brief_blob` | ревизия 7: git-blob опубликованного `brief.md` customer-прогона в `brief_ready` |
| `approval` | ревизия 7, engineer: `{repo, pr, dir, merge_commit, approver, approved_at, self_hash, act_policy_sha}` — для отчёта; при перепроверках выводится из форджа (§11.4.2) |

`source_pin` в состоянии **нет**: публичный envelope его не несёт.

Статусы: `waiting_interview` и `stopped_interview` — оба персистентные.
Ревизия 7: `brief_ready` — терминальный статус customer `--brief-only` (§11.2); у
engineer `session_id` = `s-<run_id>-e` пишется **до** `start` (§11.4.5).
Операции: `interview-start`, `interview-brief` — write-ahead, как остальные.
До `completed_at` `_step_branch` не достигается: ни ветки, ни worktree, ни
авторинга.

## 5. Переходы

### 5.1. Таблица по кодам discovery

Коды — публичный контракт discovery (README «What a caller reads»):
`1 > 2 > 20 > 10 > 11 > 0`.

Таблица тотальна: у каждого вызова определён исход для каждого из шести
кодов; синтетический 1 (§6) обрабатывается как 1.

| Вызов | Код | Переход |
|---|---|---|
| `start` | 20 | `session_id` (из `next_action.session_id`) записан, `interview-start` → `completed`, run → `waiting_interview`; печать `next_action` и команды ответа |
| `start` | 0, 10, 11 | невозможная для `start` форма (пустая сессия не бывает `complete`); envelope без `next_action` не несёт `session_id` ⇒ как 1: `stopped_interview`, `interview-start` остаётся `started`, `session_id is None` |
| `start` | 1, 2 | `stopped_interview`; `interview-start` остаётся `started`, `session_id is None`; S1 не вызывается |
| `status` (повтор) | 20 | из `waiting_interview`: состояние не меняется (`run.json` байт в байт); из `stopped_interview` (10/11 или 1/2 ранее): статус → `waiting_interview`; в обоих случаях печать `next_action` и команды ответа, spec-loop — код 0. Для любого `status`/`brief` → 20 `next_action.session_id` обязан совпасть с записанным; неполный `next_action` или чужой id — fail-closed стоп |
| `status` | 0 | `interview-brief` → `started`; `discovery brief --out <run_dir>/brief-input/00-discovery/.brief.tmp`; **код `brief` — по строкам `brief` ниже** |
| `brief` | 0 | `inspect_brief` полного source-слоя на tmp, сверка координат брифа — **только** H1, `interview.frame`, `traces_to` (критерий — как в E1 и §5.3: customer — без путевых элементов, engineer — ровно один путевой элемент, равный записанному `interview.traces_to`; роли участников не сверяются: `--stakeholder` — декларация, D3; второй легитимный участник интервью законен), `os.replace` → `brief.md`, `state.brief`, `completed_at`, `running`; далее S1 по E1 |
| `brief` | 20 | сосед снова ждёт ответа (между `status` и `brief` появился вопрос): run → `waiting_interview`, tmp удаляется, публикации нет; `interview-brief` сбрасывается; печать команды ответа |
| `status`/`brief` | 10, 11 | `stopped_interview`; `findings`/`readiness_findings` → `run_dir/interview-findings.txt`; tmp удаляется; печать шаблона `discovery answer --session <id> --role <role> --question <QUESTION_ID> --supersede --file <answer.yaml>` — `question_id` подставляет человек, из findings он не выводится; spec-loop — код 1 |
| `status`/`brief` | 1, 2 | `stopped_interview` с `operation.reason`; координаты и `session_id` не трогаются; tmp удаляется; код 1 |
| `brief` 0, но tmp не проходит `inspect_brief` или сверку координат | — | `stopped_interview`; tmp не становится `brief.md`; S1 не вызывается |
| записанная сессия исчезла (`status` → 1 с reason о сессии) / tmp нечитаем | — | `stopped_interview`, fail-closed: сессию не пересоздаём и не ищем; замена id запрещена (§5.3), поэтому подсказка — «восстановите ту же сессию `<id>` в `$DISCOVERY_HOME` и повторите» либо `--new-run --ws-id <fresh-id>` |

Инвариант `status`: **replay-safe, не read-only**. `status` зовёт
`_issue_if_needed` и может дописать `question_asked` в журнал, но при уже
выданном pending-вопросе повтор событий не плодит (проверка
`question_id not in events`). Поэтому `status` можно звать сколько угодно,
а наш переход делается только на смене кода.

### 5.2. Диспетчер spec-loop

| статус леджера | действие | код выхода |
|---|---|---|
| `waiting_interview` | сверка координат → `runner.resume` → остался `waiting` | 0 (печать команды ответа) |
| `waiting_interview` | … → перешёл в `running` | продолжается как сейчас |
| `stopped_interview`, `session_id` записан | сверка координат → `runner.resume` (обычный `status`) → остался `stopped` | 1 (findings, шаблон ответа) |
| `stopped_interview`, `session_id` записан | … → `waiting`/`running` | как выше |
| `stopped_interview`, `session_id is None` (сирота после `start` ≠ 20) | discovery **не вызывается**; печать recovery-команды `… --session <id>` и `--new-run --ws-id <fresh-id>`; после успешного attach — обычный `status` тем же вызовом | 1 |

Ревизия 7: `brief_ready` — без `resume`, печать следующего шага (`make
brief-propose`), код 0; занятая блокировка прогона (§11.4.5) — код 1. Новые причины
`stopped_interview` engineer-маршрута — §11.4.3–§11.4.5.

Сегодня `_report_state` для `stopped_*` не зовёт `resume`; для
`stopped_interview` это меняется явно — стоп интервью продолжаемый.

Строка «1 (findings, шаблон ответа)» — по факту два случая различаются
наличием findings-файла (`run_dir/interview-findings.txt`, §5.1: пишется
только при 10/11). Если файл существует, печатается его путь и шаблон
ответа. Если `stopped_interview` пришёл от кода 1/2 (findings-файл не
пишется — §5.1), findings-путь печатать нечего: вместо него печатается
recovery-подсказка «восстановите ту же сессию `<id>` и повторите либо
`--new-run --ws-id <fresh-id>`» — та же форма, что и для сироты выше.

Различение работает только пока файл описывает стоп ТЕКУЩЕГО захода,
поэтому удаление привязано не к переходу 20, как говорила первая редакция
таблицы, а к входу в стадию: `_step_interview` снимает файл до любого
обращения к discovery (devtools#247). Перечня обесценивающих переходов
здесь нет намеренно — он неполон по построению: шорткат
`_interview_poll` → `_interview_publish` (после brief 10/11 op
`interview-brief` уже не `new`) вообще не проходит через разбор ответа.
Сирота проверяется раньше findings: пока сессии нет, отвечать на них
некому.

Печатаемые команды строятся через `shlex.quote` (роль с пробелами,
пути с пробелами).

### 5.3. Recovery: `--session <id>`

Сиротство: `interview-start == started`, `session_id is None` (процесс умер
между вызовом `start` и записью). Только в этом состоянии разрешён
`--session <id>`; при записанном `session_id` — отказ, замена сессии
невозможна. Присоединение fail-closed по **фактической форме брифа**
(приватный `header.json` не читается): `discovery brief --out <tmp>` — артефакт
законно пишется при кодах 0, 10, 11 и 20, все четыре допустимы для сверки;
1 и 2 — отказ присоединения. Затем:

- точное равенство строки H1 с `# Discovery Brief — {target} ({frame}-фрейм)`
  (`render.py:371`);
- `interview.frame == frame`; `traces_to` — как в E1 (customer: без
  путевых элементов среди записей `traces_to`, т.е. без строк вида `*.md`,
  не начинающихся с `[[`; отказ, если хоть одна путевая ссылка есть —
  равенство с `[]` не проверяется буквально, критерий именно в отсутствии
  путевых элементов; engineer: ровно один путевой элемент, равный
  записанному `interview.traces_to`);
- `interview.sessions` — критерий по **множеству** `participant_role` среди
  записей: множество должно быть подмножеством `{<stakeholder>}` (пустой
  список — тоже подмножество, значит допустим); остальные ключи записи
  (`date`, `medium`, …) не сверяются; любая роль вне `{<stakeholder>}` —
  отказ. Эта сверка ролей применяется **только при присоединении**
  (`--session`): здесь роль — единственный признак, что сессия наша, и
  аварийный вход вправе быть строже декларации. В штатном пути (`brief` 0)
  роли не сверяются (D3).

При совпадении `session_id` записывается и `interview-start` → `completed`;
дальше обычный `status`.

Сиротство исчезнет по построению после discovery#49 п.2 (caller-assigned
session id, записанный write-ahead); `--session` останется аварийным входом.
Ревизия 7: engineer пишет id до вызова, `--session` для него запрещён;
восстановление engineer — §11.4.5. Customer остаётся на этом разделе (перевод на
write-ahead id — отдельная задача, §11.10 Q3).

### 5.4. `--new-run`

Существующий прогон с теми же (repo, subject) матчится всегда, поэтому
«создайте новый workstream с другим `--ws-id`» (нынешняя подсказка E1)
неисполнимо — заменяется на `--new-run --ws-id <fresh-id>`. Явный `--ws-id`
обязателен: без него снова вычислится прежний `<slug>-<date>` и сработает
существующий collision guard по `ws_id` (`spec_loop.py:738`). `--new-run`
взаимоисключающ с `--run-id` и `--session`. Разрешён, только если **все**
совпавшие прогоны стоят до S1 (`waiting_interview`/`stopped_interview`; ревизия 7 —
и `brief_ready`, §11.2);
если хотя бы один достиг S1 — отказ с перечнем и `--run-id` (дубль
workstream: ветка уже есть). Старые леджеры и сессии не меняются; их
`session_id` печатаются для ручной уборки. После `--new-run` старый леджер
продолжает матчиться по (repo, subject), поэтому spec-loop печатает команду
повтора **с `--run-id <новый>`**, и `--run-id` вместе с `--need` на повторе
разрешён явно (координаты сверяются с указанным прогоном).

При `--new-run` обычный гвард неоднозначности «несколько прогонов — выберите
явно через `--run-id`» (для случая без `--new-run`, когда совпавших прогонов
больше одного) **не применяется**: явный `--new-run` уже выбор — печатать
и просить выбрать было бы противоречием собственному флагу. Вместо гварда
неоднозначности проверяются **все** совпавшие прогоны на «до S1» (как
выше), и все перечисляются построчно (`run_id`, статус, сессия discovery)
как остающиеся нетронутыми.

### 5.5. Crash-recovery `interview-brief`

Итоговый бриф session id не несёт, поэтому существования `brief.md`
недостаточно. При `op == started` и `state.brief is None`:

- нет ни `.brief.tmp`, ни `brief.md` (гибель сразу после write-ahead, до
  вызова discovery) — повторный рендер в новый tmp, дальше по полной
  таблице §5.1 (в т.ч. 20 → обратно в `waiting_interview`);
- есть только `.brief.tmp` (гибель до `replace`) — старый tmp удаляется,
  повторный рендер в новый tmp, проверка, обычная публикация;
- есть `brief.md` — повторный `discovery_brief` записанного `session_id`
  во второй tmp, требуется код 0 (публикация состоялась только при 0, иной
  код означает, что сессия ушла от опубликованного состояния — стоп),
  **байты равны** durable `brief.md` (рендер детерминирован при
  неизменной сессии: `generated_at` — это `created_at` сессии,
  `render.py:355`, а не момент рендера; smoke это подтверждает двумя
  рендерами подряд),
  повтор сверки координат и `inspect_brief` полного source-слоя, и только
  затем op завершается **без повторного `replace`**; расхождение байтов —
  стоп: durable-файл не совпадает с сессией, выбирать сторону молча нельзя.

Это бесплатная reconciliation, не повтор интервью.

### 5.6. Engineer: `upstream_blob`

> **Ревизия 7:** раздел заменён §11.4.4 (копия `upstream.md` из проверенного буфера,
> blob после approve). Текст ниже — история.

Durable-копия upstream кладётся в `brief-input/00-discovery/` до `discovery
start`; её hash — `interview.upstream_blob`. Пересверяется перед каждым
использованием engineer-сессии и перед финализацией дескриптора; mismatch
останавливает прогон **до** обращения к discovery. После доставки копия —
второй файл source-слоя, как в E1.

> **Поправка 2026-09-18 (discovery#50).** Дизайн предполагал, что discovery
> примет копию под именем `<basename>` источника (§3, строка durable-копии).
> Сосед назвал имя внутри сессии **фиксированным — `upstream.md`** и обосновал:
> при совпадении basename с именем итогового брифа `traces_to` разрешился бы в
> сам бриф, GC-16 остался бы зелёным, а GC-05(engineer) сверил бы документ сам
> с собой — молчаливый ложный pass дороже потери информативности имени.
> Провенанс источника живёт у вызывающего (`interview.upstream_blob`), так что
> для devtools меняется только имя, передаваемое в `--upstream`; `brief --out`
> в `upstream.md` такой сессии сосед отклоняет. Второе: сосед валидирует
> источник (`schema`, `interview.frame: customer`, `status: approved`, ноль
> error-findings `gate_check`) **до** создания сессии — preflight здесь не
> отменяется, но единственным он больше не является.

## 6. Порт discovery в `Ops`

> **Ревизия 7:** порт расширен по §11.4.6 (`session_id`, `lock_fd`,
> `discovery_approve`, факты brief-PR, комментариев и версий политики).

```python
@dataclass(frozen=True)
class DiscoveryReply:
    code: int          # см. транспортный контракт
    envelope: dict     # разобранный JSON stdout; при синтетическом 1 —
                       # канонический synthetic envelope (ниже)
    stderr: str

def discovery_start(self, frame, target, traces_to, upstream_path: str | None) -> DiscoveryReply
def discovery_status(self, session_id) -> DiscoveryReply
def discovery_brief(self, session_id, out_path) -> DiscoveryReply
```

`upstream_path` — durable-копия из `run_dir` (не файл оператора); до
discovery#49 реализация отказывает на непустом значении тем же текстом, что
preflight, — порт менять после разблокировки не придётся. Разблокировка
случилась 2026-09-18 (discovery#50): форма порта подтвердилась, отказ на
непустом значении держится до реализации маршрута, имя копии — `upstream.md`
(поправка §5.6).

Запуск: `uv run --frozen --project <workspace>/discovery discovery …`,
`cwd` — каталог прогона. `target` для `start` — `repo_slug`.

Транспортный контракт (проверка согласованности границы, **не** второй
вычислитель `protocol.exit_code`):

- валидный контракт ⇒ `code = process.returncode`;
- непарсимый JSON, не-object, отсутствие обязательных полей (`lifecycle`,
  `gate`, `readiness`, `next_action`, `findings`, `readiness_findings`,
  `operation`), неизвестный код, невозможная для кода форма envelope
  (например, 20 без `next_action.question_id`/`session_id`) ⇒ синтетический
  `code 1` с **каноническим synthetic envelope** формы протокола:
  `{"lifecycle": "unknown", "gate": "unknown", "readiness": "unknown",
  "next_action": {}, "findings": [], "readiness_findings": [],
  "operation": {"status": "unknown", "reason": "<дефект границы>"}}` —
  потребитель всегда читает одну форму.

## 7. Передача в E1 и идемпотентность

- `brief.md` появляется только через `os.replace` после `inspect_brief`
  полного source-слоя; `state.brief` — только после `replace`. Дальше
  `_step_materialize_brief` E1 без изменений: он читает `run_dir/brief-input`
  и сверяет байты с дескриптором.
- `discovery start` идёт ровно один раз на прогон и при существующем
  леджере **никогда** не повторяется. Обычный повтор вызывает `status`;
  orphan-recovery (`--session`) сначала вызывает `brief` во временный файл
  для сверки присоединения (§5.3) и только затем `status`.
- Преамбула charter/requirements (E1) не меняется: source-слой тот же.

## 8. Тестовая матрица

По фактическим слоям:

**runner + FakeOps** (последовательность `DiscoveryReply`):
- `start` 20 → `waiting_interview`, `session_id` записан, `interview-start`
  `completed`, `_step_branch` не вызван (`ops.ensure_branch` отсутствует в
  вызовах);
- `start` 1/2 → `stopped_interview`, `interview-start` остаётся `started`,
  `session_id is None`, S1 не вызывается;
- `status` 20 повторно: `run.json` байт в байт неизменен; `next_action.
  session_id` ≠ записанному — fail-closed стоп; неполный `next_action` — стоп;
- `status` 10/11 → `stopped_interview`, findings в файле; повтор из
  `stopped_interview` снова зовёт `status`;
- `status` 0 → `brief` 0 → `inspect_brief` → `replace` → `state.brief` → S1
  по E1 (`materialize-brief` op выполняется, charter получает
  `brief_context`);
- `brief` 20 → `waiting_interview`, tmp удалён, публикации нет; `brief`
  10/11/1/2 — по таблице, tmp не становится `brief.md`;
- `start` 0/10/11 — как 1: `stopped_interview` без `session_id`;
- `stopped_interview` без `session_id`: повтор не вызывает discovery,
  печатает recovery-команду; после `--session` — обычный `status`;
- `brief` 0, но tmp не проходит `inspect_brief`/сверку координат →
  `stopped_interview`, без `replace` и S1;
- `stopped_interview` (после 10/11) → `status` 20 → `waiting_interview`,
  findings-файл удалён, spec-loop код 0;
- findings прошлого захода не переживают стоп по другой причине
  (devtools#247): 10 → 1/2; 10 → `status` 0 → отказ координат brief; и
  шорткат `brief` 11 → повтор → `brief` 0 с плохими координатами, где
  разбор ответа не вызывается вовсе. Негативная половина — две строки выше:
  файл всё ещё пишется при 10/11 и всё ещё исчезает на 20, иначе инвариант
  удовлетворялся бы «никогда не писать»;
- crash: `interview-brief` `started` без tmp и без `brief.md` → повторный
  рендер и обработка по полной таблице; только `.brief.tmp` → повторный
  рендер и публикация; `brief.md` без дескриптора → повторный рендер,
  байты равны → op завершён без `replace`; байты не равны → стоп;
- recovery `--session`: разрешён только при `started` без `session_id`;
  H1/frame/traces_to/sessions сверяются; чужая роль — отказ; повторный
  `--session` при записанном id — отказ;
- engineer: матрица ревизии 7 — §11.7 (T1–T52 с подпунктами); здесь
  остаётся только историческая строка: mismatch `upstream_blob` останавливает
  прогон до обращения к discovery.

**spec_loop**: CLI-preflight (все отказы до run-id: без `--stakeholder`, без
`--frame`, `--need`+`--brief`, customer с `--traces-to`, engineer до inbox);
need-флаги без `--need` — отказ; `--need` против прогона с `interview is
None` — отказ с подсказкой; повтор после `--new-run` проходит по
напечатанному `--run-id` без ручного выбора; `--new-run` требует `--ws-id`, взаимоисключающ
с `--run-id`/`--session`, разрешён только при прогонах до S1 и **не
меняет** старые леджеры и сессии; координаты повторного вызова; печатаемые команды shell-safe (роль с
пробелами через `shlex.quote`); коды выхода 0/1 по §5.2.

**RealOps**: argv `start`/`status`/`brief` (в т.ч. `--frozen --project`,
`upstream_path` — отказ до inbox); JSON boundary: каждая форма негодного
envelope → синтетический 1, валидный контракт → `returncode` не
переопределяется.

**Интеграционный smoke**: настоящий `discovery` с временным
`DISCOVERY_HOME`: `start` → 20 с настоящим `next_action`, затем **цикл по
всему реальному банку вопросов** фрейма (`answer` через CLI на каждый
`next_action`, синтетические ответы с покрытием required-ключей) до
`status` → 0, `brief` → файл проходит `inspect_brief`. Opt-in (маркер как у
зондов к spec-runner), чтобы обычный pytest не зависел от соседа.

Negative controls обязательны для трёх гвардов: сверка роли при
присоединении, требование кода 0 у `brief`, сравнение байтов при recovery.

## 9. Живая приёмка

До ревизии 6 §9 была **одним** критерием, связывавшим в одном прогоне два
независимых утверждения. Из-за этого доказанная половина (стадия Need) не
засчитывалась, пока не доказана вторая (хвост прогона), хотя код S7 общий и
стадии Need не специфичен. Ревизия 6 разводит их; сквозной критерий не
вычёркивается, а получает триггер (§9.3).

### 9.1. Стадия Need работает — ВЫПОЛНЕНО 2026-09-15/17

Один прогон `make spec-loop … ARGS='--need --frame customer --stakeholder
<role>'` на реальном предмете с реальным стейкхолдером: пауза, ответы через
`discovery answer` вне spec-loop, повтор команды продолжает прогон.
Evidence в `docs/evidence/`:

- SHA devtools и discovery; session id; код каждого вызова; SHA-256 брифа и
  SHA-256 journal/transcript сессии (сам транскрипт не публикуется — §2.4
  плана);
- факт отсутствия ветки/worktree в цели во время `waiting_interview`
  (`git branch --list spec/<ws-id>-behaviour` пуст, `git worktree list`);
- объявленная stakeholder role; итоговый descriptor source-слоя.

Закрыто живым прогоном на spec-runner#480,
`docs/evidence/2026-09-15-need-stage-live-run.md`: стадия Need и путь до S5
отработали без ручных вмешательств.

Чекбокс `behaviour-document-runner-residuals` закрывается этим прогоном
только если `author_backend=disp` действительно использован и его ledger/pin
(`disp_slug`, `disp_anchor_dir`) отражён в evidence.

### 9.2. Хвост прогона: S7 → S8 → tasks → approval

Доказывается **отдельным** прогоном, и вход не обязан быть `--need`: шаги
`_step_verdict`/`_step_merge`/`_step_s8` и доставка tasks общие для обоих
входов, поэтому дешёвый `spec-loop --brief` на крошечном предмете доказывает
ровно то же, что дорогой `--need`. Требуется показать:

- раннер **сам** дошёл до `waiting_human_merge` (а не был застигнут в
  `stopped_review`);
- мерж бандл-PR выполнен санкционированным путём — `make human-merge`
  (ADR-ECO-011 D6: учётка человека из политики подписи — с 2026-09-22
  источник не переменная, а репозиторий `approval-policy`, см. спеку
  `2026-09-22-approver-policy-trusted-source-design.md`; сверка логина), а не
  голым `gh pr merge`;
- `resume` подтвердил факт мержа и перевёл прогон на S8; S8 завершился
  `exit=0` (не `merged_unverified`);
- заведён draft tasks-PR, получена approved tasks-спека; номера
  bundle-/tasks-/approval-PR — в evidence.

Негативный контроль: мерж, сделанный в обход раннера, должен быть
реконсилирован `resume` (`_reconcile_pr_merged_out_of_band`), а не потерян.
Он и есть причина этого критерия: на прогоне 9.1 бандл-PR spec-runner#522
смержили вручную из `stopped_review`, то есть до выполнения S7, и дыру в
реконсиляции пришлось закрывать отдельным дефектом (devtools#253).

### 9.3. Сквозной прогон без ручных артефактов — ОТЛОЖЕН, с триггером

Формулировка §3 E2 плана — «прогон, где ни один артефакт не создан руками:
от `spec-loop --need` до integration-PR» — остаётся обязательной и НЕ
заменяется суммой 9.1 + 9.2.

**Триггер:** первый внешний проект в `DarkFactory-polygon`. Там реальный
стейкхолдер появляется по построению, тогда как синтетическая замена
запрещена самим планом (§4: «Режим Need запускается только при наличии
реального стейкхолдера… без реальных стейкхолдеров интервью вырождается в
самоопрос»).

### 9.4. Engineer-маршрут — ревизия 7

Критерий — §11.8: цепочка customer `--brief-only` → brief-PR → `make human-merge` →
`make brief-approve` → engineer-прогон до E1 с реальным стейкхолдером. Гарантия
происхождения approval — **только для engineer `--need`**; невосстановимость после
потери ответа успешного `start` до discovery#63 — принятое ограничение. Обе оговорки —
в evidence.

## 10. Вне объёма

- Обёртка ответов (B), интерактив (C), два интервью в одном прогоне.
- Approval брифа как отдельный стоп (D5). Превращение customer-брифа в
  approved upstream для engineer — ревизия 7, §11 (отдельными обёртками, не стопом
  прогона).
- Вне объёма ревизии 7 — §11.11.
- Публикация транскрипта; переносимое хранилище журнала (план §4).

## 11. Engineer-маршрут (ревизия 7, 2026-10-08)

### 11.0. Факты (замер по коду)

| # | Факт | Где |
|---|---|---|
| F1 | `start --upstream <file>` — только engineer; копия в сессии всегда `upstream.md`, ставится **первой** в `traces_to`; `--traces-to upstream.md` вместе с `--upstream` — отказ | `discovery/src/discovery/cli.py:157-176,185-196` |
| F2 | `admit` до создания сессии: `.md`, frontmatter, `schema: discovery-brief`, `interview.frame: customer`, `status: approved`, self-hash не расходится, ноль error-findings линтера. Бриф **без** self-hash допускается (миграционный долг); readiness не проверяется | `discovery/src/discovery/upstream.py:36-87` |
| F3 | `--session-id`: занятый закоммиченный id (`header.json` есть) — отказ; каталог без `header.json` достраивается тем же `start` — **не эксклюзивно** и без проверки остатков (журнала) в каталоге | `discovery/src/discovery/session.py:101-135` |
| F4 | Перехваченные исключения `start`/`status`/`approve` (`CallRefused`, `UpstreamRejected`, `PolicyRefused`, `SessionUnreadable`, `JournalUnreadable`, `ForgeUnavailable`, `OSError`, …) дают **код 1** (`protocol.unknown`); ошибка `argparse` — до `try`, код 2 **без** envelope. Код 1 у `status` не различает «сессии нет», «резервация», «не читается», «discovery не запустился». Код 2 с envelope у `approve` — только `pr_not_merged`, `approver_not_authorized`, `brief_not_in_pr`, `brief_bytes_diverged` | `cli.py:578-599`, `session.py:138-147`, `protocol.py:23-29` |
| F5 | `approve <file> --repo --pr [--path]`: PR смержен; мержер ∈ allowlist актуальной на момент вызова версии политики; PR менял `path`; self-hash файла в merge-коммите == self-hash локального. Успех — **пишет** конверт (`status: approved`, `approved_by` = runtime, `approved_at` = время мержа, `approver` = мержер, `approved_content_hash`); `brief_bytes_diverged` — **тоже пишет** (откат в `draft`); прочие отказы — не трогает. Код: 2 — отказ; иначе по осям файла — 10 (`gate=fail`), 11 (`gate=pass`, `readiness=incomplete`), 0 | `cli.py:398-417,499-536`, `approval.py:118-145`, `protocol.py:133-170` |
| F6 | self-hash (`sha256:` без конверта, LF-нормализация, `yaml.safe_dump(sort_keys=False, allow_unicode=True)`) одинаков у draft и подписанного файла; CRLF-вариант даёт тот же хэш | `approval.py:62-90` |
| F7 | Подписанный файл не несёт координат акта и версии политики; использованную `approve` версию сосед наружу не отдаёт | `approval.py:118-135`, `policy.py` |
| F8 | devtools: `discovery_start(frame, target, traces_to, upstream_path, cwd)` — без `session_id`; непустой `upstream_path` → синтетический 1; `spec_loop` отказывает engineer текстом `ENGINEER_BLOCKED` | `governance/ops.py:32,2234-2252`, `spec_loop.py:304-308` |
| F9 | Факты форджа devtools: `pr_facts`/`read_pr` (`headRefOid` есть; имени головы, файлов, меток, тела нет), `policy_version_fact` (последний коммит, тронувший путь), `repo_file_fact(repo, sha, path)` (GraphQL; бинарное/усечённое — отказ). `read_blob_text` — **локальный** `git show` | `governance/ops.py:47-50,1649-1652,1914-1975,2613`, `approval_facts.py:111-125,419-446` |
| F10 | E1 (`inspect_brief`): engineer-бриф — одна путевая ссылка `*.md` ≠ `brief.md`; у upstream — gate, frame, `coverage`/блокеры, путевые `traces_to`, `status: approved`; CRLF отвергается. Происхождение подписи не проверяет | `governance/brief_input.py:77-85,100-245` |
| F11 | Customer-прогон `--need` после публикации брифа **в том же вызове** идёт в S1 | `governance/runner.py:1873-1924` |
| F12 | Поиск прогона — по (repo, subject); `--new-run` — только если все совпавшие в `waiting_interview`/`stopped_interview`; повтор с иным frame — отказ | `governance/spec_loop.py:940-959,1045-1063` |
| F13 | `human-merge.sh` мержит учёткой человека; наличие `human-merge-required` сам не проверяет; у candidate сверяет актуальную версию политики с пином `policy: <repo>@<sha>` из тела PR, у прочих — судит актуальную; защиту ветки не обходит | `devtools/human-merge.sh:1-40,112-170` |
| F14 | Ни рендер брифа, ни envelope `status`/`brief` не несут отпечатка принятого upstream | `discovery/src/discovery/render.py:355-370`, `cli.py:205-215` |
| F15 | Межпроцессной блокировки прогона нет; `runner.resume` вызывается и не из spec-loop; дочерний процесс соседа переживает смерть родителя | `governance/runner.py:839,3925`, `ops.py:2229-2231` |

### 11.1. Цепочка и модель доверия

#### 11.1.1. Сквозная цепочка (вариант А)

```
[1] spec-loop --need --frame customer --brief-only --stakeholder <role> …
                                         → customer-бриф (draft), прогон в brief_ready
[2] make brief-propose RUN=<customer-run>
                                         → brief-PR: бриф + заявка на одобрение (§11.3)
[3] ревью brief-PR (ai-prosto, review-pr.sh) — если ruleset цели требует ревью
[4] make human-merge ARGS='<repo> <pr>'  → АКТ: мерж учёткой человека из allowlist;
                                           для brief-PR сверяет пин политики заявки
[5] make brief-approve RUN=<customer-run> PR=<pr>
                                         → discovery approve; печатает команду [6]
[6] spec-loop --need --frame engineer --new-run --ws-id <fresh> --stakeholder <role> \
              --traces-to <подписанный бриф> --approval-pr <pr> …
                                         → preflight по форджу → engineer → E1
```

[2] и [5] — обёртки devtools без леджера, идемпотентные и восстанавливаемые чтением
форджа. [4] — единственный человеческий акт, он же подпись (решение владельца
2026-09-20).

#### 11.1.2. Что доказывает engineer-preflight — и чего не доказывает

**Доказывает (факты форджа; записи агента недоступны):** в репо цели PR `<pr>`
смержен учёткой `merged_by` в ветку по умолчанию; **изменения PR** — ровно два
добавленных файла: бриф и заявка на одобрение (§11.3), их содержимое читается из
merge-коммита; заявка объявляет назначение «одобрение discovery-брифа»,
self-hash брифа и пин политики `P`; self-hash брифа в merge-коммите совпадает с
заявленным и с файлом оператора; `merged_by ∈ accounts(P)`; конверт файла оператора
зеркалит это событие (`approver == merged_by`, `approved_at == merged_at`,
`approved_content_hash` == self-hash). Содержимое коммита неизменяемо — в отличие от
тела и меток PR, которые можно поменять после мержа; назначение акта и пин
политики поэтому читаются только из него, метки и тело — подсказки.

**Не доказывает:**
- что конверт записала команда `discovery approve` (F7). Конверт — зеркало, акт — мерж:
  ручной конверт, **в точности** зеркалящий проверенный мерж, принимается; ручная
  подпись **без** `approved_content_hash` отклоняется — её нечем сверить с актом;
- что `approve` судил по версии `P`: сосед версию не отдаёт. Доказуемо иное — мержер
  входит в `P`, и (§11.4.3) политика с тех пор не менялась либо её смена подтверждена
  человеком комментарием в том же PR;
- что человек мержил именно через `make human-merge`: проверка пина в [4] —
  предохранитель от сгоревшего акта, не часть доказательства.

**Граница локального доверия.** `run.json`, durable-копии и файл оператора пишет любой
процесс с правом записи в каталог прогона, включая авторского агента. Ни одно решение
о происхождении и политике на них не опирается: пин акта, подтверждения смены
политики и координаты акта при каждой перепроверке выводятся заново из форджа
(§11.4.3); `interview.approval.pr` — лишь адрес, по которому их читать, и подмена его на
другой PR проходит все те же предикаты заново. Защита локальных данных от
намеренной подмены — граница песочницы авторского агента (`@id:author-agent-sandbox`),
не E2.

### 11.2. Customer-прогон: `--brief-only` → `brief_ready` (Q1, решение владельца)

- `--brief-only` допустим **только** с `--need --frame customer`. Без `--need`, с
  `--frame engineer`, с `--brief`, с `--session` — отказ **до** создания прогона и до
  любого вызова соседа.
- Флаг — координата интервью: `interview.brief_only = true` при старте. Повтор с иным
  значением при существующем прогоне — отказ (как смена frame); сменить — `--new-run`.
- Стадия Need — без изменений (§5). Публикация брифа (`os.replace` → `state.brief`
  → `completed_at`) завершается переходом в **`brief_ready`** вместо `running`, в той же
  записи `run.json`; `brief.md` прошёл `inspect_brief` и сверку координат, его blob — в
  `interview.brief_blob`.
- `brief_ready` — **терминальный**: `advance`/`resume` из него не исполняют шагов (S1 и
  далее недостижимы из этого прогона), не зовут соседа и не создают, не меняют и не
  удаляют файлов в каталоге прогона и в цели (`run.json` и дерево `run_dir` — байт в
  байт). Повтор печатает следующий шаг (`make brief-propose RUN=<run-id>`) и даёт код 0.
  Флаги, ведущие дальше (`--session`, `--run-id` с иными координатами), — отказ.
- Crash-окно между `replace` и записью статуса: повтор по §5.5, затем
  `brief_ready`, не S1.
- `--new-run` разрешён и при совпавших прогонах в `brief_ready` (§5.4: S1 они не
  достигали и не достигнут).
- Без флага поведение прежнее (customer → S1).

### 11.3. `brief-propose` — носитель акта и заявка на одобрение

`make brief-propose RUN=<run-id>` (под блокировкой customer-прогона, §11.4.5):

1. Прогон: `status == brief_ready`; `blob(brief.md) == interview.brief_blob`; бриф
   проходит `inspect_brief`; `status: draft`.
2. Пин политики: `P = policy_snapshot(pinned_sha=None)` — `FOUND`, иначе отказ.
3. Содержимое предложения — **два файла** в `<bundle_dir>/00-discovery/`:
   - `brief.md` — байты `brief.md` прогона;
   - `approval-request.yaml` — `schema: discovery-brief-approval-request/v1`,
     `purpose: discovery-brief-approval`, `brief: brief.md`, `brief_self_hash:
     <self-hash>`, `policy: {repo, ref, path, sha: P.sha}`, `run_id`, `ws_id`.
     Сериализация детерминирована (фиксированный порядок ключей), байты заявки
     однозначно выводятся из прогона и `P`.
3a. **Строгий разбор заявки — один нормативный** (`approval_request.parse`), его
   используют все четыре потребителя: `brief-propose` (сверка найденного), `human-merge.sh`
   (через тот же модуль), `brief-approve` и engineer-preflight. Правила: UTF-8 без CR;
   ровно один YAML-документ; дубли ключей запрещены **на всех уровнях** (загрузчик с
   отказом на повторный ключ, а не `safe_load`, который молча берёт последний); только
   скаляры-строки и mapping `policy`; набор ключей — ровно схема v1, лишний или
   недостающий ключ — отказ; `policy.sha` — 40 hex. Любой отказ разбора — заявка
   **неоднозначна или некорректна** и не доказывает назначения акта, в том числе
   если PR с ней уже смержен в браузере.
4. **Сначала — поиск по форджу во всех состояниях** ветки `brief/<ws_id>` и её PR,
   затем действия. Для любого найденного PR/ветки сравнивается **содержимое** по
   immutable SHA (`head_sha` PR либо голова ветки; у смерженного — merge-коммит):
   список файлов по форджу == ровно два файла выше со статусом `added`; байты
   `brief.md` == байтам прогона (blob); заявка разбирается по схеме v1 и **все** её
   поля, кроме `policy.sha`, равны ожидаемым (`purpose`, `brief`, `brief_self_hash`,
   координаты источника политики, `run_id`, `ws_id`); `policy.sha` — версия,
   существующая в истории источника (`policy_version_fact_at`, §11.4.6). Ожидаемая заявка **не** пересобирается с
   текущим `P`: у найденного предложения его исходный пин сохраняется, а расхождение
   с актуальной версией сообщается отдельно как дрейф. Совпадение «по форме» без
   совпадения полей — отказ.
   - PR **смержен** — содержимое совпало: печать номера (результат уже есть);
   - PR **открыт** — совпало: печать номера; пин заявки ≠ актуальной версии — печать
     предупреждения: [4] откажет до мержа (§11.3 п.6), нужен `--repropose` (вне E2, Q4);
   - PR **закрыт без мержа** — отказ; новое предложение — `--repropose`;
   - PR нет, удалённая ветка есть — её **дерево** сверяется по путям: два файла с
     нашими байтами, остальное дерево == дереву её merge-base с веткой по умолчанию;
     совпало — создаётся только PR (окно «push есть, PR нет»); не совпало — отказ.
     Продвижение base после push не делает ветку чужой;
   - ничего нет — в base по одному из путей уже есть файл: отказ; иначе локальная
     ветка `git switch -C brief/<ws_id> <свежий base>` (локальный остаток прошлой
     попытки отбрасывается — источник истины фордж), коммит двух файлов, push,
     PR. Отказ push/создания PR из-за гонки — повтор с п.4, без принудительных
     операций.
5. Метаданные PR: база — ветка по умолчанию цели; метка `human-merge-required`; тело
   называет мерж **актом одобрения discovery-брифа** (D5), строка `policy:
   <repo>@<P.sha>` — тем же форматом, что у candidate. Это подсказки человеку и
   [4]; доказательство — только содержимое коммита.
6. **`human-merge.sh` для brief-PR** (правка devtools в объёме E2, решение владельца
   2026-10-08). Для PR с головой `brief/*`:
   - фиксируется `head_sha` PR; список файлов и заявка читаются форджем **по этому
     SHA**; мерж выполняется с пином головы `sha=<head_sha>` (механизм уже есть, F13) —
     только для проверенного SHA. Голова сменилась между проверкой и мержем — фордж
     отказывает мержу; повторный запуск проверяет новую голову заново;
   - отказ **до мержа**, каждый — отдельным текстом: заявки нет; файлов не ровно два
     `added` в одном `…/00-discovery`; заявка не разбирается по схеме v1 либо
     неоднозначна (дублирующиеся ключи, лишние документы YAML); `purpose`/`brief` не
     те; координаты источника политики ≠ SSOT; `brief_self_hash` ≠ self-hash брифа
     того же SHA; `policy.sha` ≠ актуальной версии; любой факт `UNAVAILABLE`;
   - тело (`policy:`) и метки PR заявку не заменяют и в решении не участвуют;
   - маршрут candidate и прочих PR — без изменений (регрессия — T50);
   - проверку `human-merge.sh` исполняет с полномочиями человека, поэтому она — узкий
     модуль `governance/brief_merge_check.py` без зависимости от `ops.py`/раннера; он,
     вся цепочка его локальных импортов (вкл. `__init__`, вендоренный self-hash) и
     `pyproject.toml`/`uv.lock` — под authority-root и харнесс-гвардом; запуск —
     `uv run --frozen --exact --no-config --no-env-file python -I` в собственном
     окружении проверки. Цепочку сверяет инвариант; нераспознанная форма запуска —
     отказ инварианта (ревью #573, решение владельца 2026-10-09);
   - проверка перед мержем не исключает последующего дрейфа политики, поэтому
     проверки §11.5 и §11.4.3 остаются обязательными. Мерж в браузере эту проверку
     обходит — тогда расхождение ловит `brief-approve` и preflight.

Почему два файла, а не один и не бандл-PR: `discovery approve` требует лишь, чтобы PR
**менял** путь брифа; без заявки подписью брифа стал бы любой мерж, затронувший путь, а
назначение акта держалось бы на изменяемых метках. Заявка в содержимом делает
назначение и пин частью того, что человек смержил.

Ревью brief-PR: защиту ветки `human-merge.sh` не обходит; где ruleset требует ревью,
перед [4] нужен `review-pr.sh` (ai-prosto) — `brief-propose` печатает это в подсказке.

### 11.4. Engineer-маршрут

#### 11.4.1. Интерфейс

```
make spec-loop SUBJECT='…' REPO=… \
  ARGS='--need --frame engineer --stakeholder <role> \
        --traces-to <подписанный customer-бриф> --approval-pr <n> \
        [--new-run --ws-id <fresh-id>]'
```

- `--traces-to` — путь к файлу оператора. Соседу он как `--traces-to` **не уходит**
  (F1): вызов — `start --frame engineer --target <repo_slug> --upstream <durable
  upstream.md> --session-id <id>`. Внутренний переносимый `interview.traces_to` =
  `upstream.md`.
- `--approval-pr <n>` — brief-PR в репо цели. Обязателен с engineer; без engineer —
  отказ.
- Поиск прогона (F12): команда шага [6], которую печатает `brief-approve`, содержит
  `--new-run --ws-id <fresh-id>`; без него при совпавшем customer-прогоне — отказ с
  этой подсказкой. Повтор engineer-прогона — `--run-id <новый>` (§5.4).

#### 11.4.2. Preflight (до run-id, до леджера, до вызова соседа)

Файл оператора читается **один раз** в буфер; проверки — над буфером; durable-копия
пишется из него же (§11.4.4). Дешёвое раньше; первый отказ останавливает.

1. Буфер: UTF-8, **без CR** (CRLF/CR — отказ, как в E1, F10: иначе прогон прошёл бы
   preflight и интервью, а упал бы в E1); frontmatter; `schema: discovery-brief`;
   `interview.frame: customer`; путевых `traces_to` нет; gate pass, ноль
   error-findings, `coverage.gate_passed: true`, блокеров нет (как `_gate` E1).
2. Конверт: `status: approved`; `approved_content_hash` **присутствует** и == self-hash
   буфера (вендоренная функция, ниже); `approved_by`, `approved_at`, `approver` непусты.
3. Факты PR из форджа (контракт §11.4.6): PR смержен; `merge_event` полон; `base_ref` ==
   ветке по умолчанию цели (`default_branch_fact`); список файлов — ровно `<dir>/brief.md` и `<dir>/approval-request.yaml` для одного `<dir>` вида
   `…/00-discovery`, оба `added`.
4. Заявка из merge-коммита (`repo_file_fact`): строгий разбор §11.3 п.3a;
   `purpose: discovery-brief-approval`; `brief: brief.md`; `policy.repo/ref/path` ==
   координатам SSOT источника политики devtools.
5. Бриф из merge-коммита (`repo_file_fact`, не локальный git — F9): self-hash ==
   `brief_self_hash` заявки == self-hash буфера.
6. `approver == merged_by`, `approved_at == merged_at`.
7. Пин акта `P = policy.sha` заявки: `policy_version_fact_at(repo, ref, path, P)` —
   `P` принадлежит истории SSOT-ref и тронул файл политики (иначе отказ: читаемый
   SHA боковой ветки — не версия источника); состав версии `P` читается
   `repo_file_fact(repo, P, path)` и разбирается правилом `policy_accounts` (не `policy_snapshot(pinned_sha)`
   — тот отказывает `superseded` до чтения, §11.4.3); `merged_by ∈ accounts(P)`; дальше —
   §11.4.3.
8. Любой факт форджа `UNAVAILABLE` — отказ «повторите»; текст не говорит «не одобрено».

Успех фиксирует в `interview.approval` = `{repo, pr, dir, merge_commit, approver,
approved_at, self_hash, act_policy_sha: P}` — для отчёта; при перепроверках эти
значения выводятся из форджа заново (§11.1.2).

**Одна реализация self-hash.** П.2 и п.5 считают self-hash алгоритмом соседа (F6);
функция вендорится пиненой копией (`contracts/discovery-approval/v1/`: `self_hash` +
`canonical_answer_bytes`, SOURCE-ревизия discovery, sha256 копии) с проверкой
copy-integrity и upstream-drift, как у прочих вендоренных контрактов, и тестом на
фикстуре, подписанной настоящим `discovery approve` (T40).

#### 11.4.3. Политика: пин акта, повторное подтверждение, дрейф

Оба значения, от которых зависит продолжение, — **факты форджа**; локального пина нет.

- **Пин акта** `P` — `policy.sha` заявки из merge-коммита (§11.4.2 п.7). Неизменяем.
- **Повторное подтверждение** — комментарий в **том же brief-PR**, тело которого —
  ровно строка `policy-reconfirm: <repo>@<sha>` (координаты — SSOT источника
  политики). Действителен, если: автор ∈ `accounts(<sha>)` (человеческая учётка из
  политики, которую он подтверждает); комментарий **не редактировался**
  (`lastEditedAt` пуст — иначе текст мог смениться после того, как его увидели);
  создан после `merged_at`; `<sha>` — версия политики по
  `policy_version_fact_at` (в истории SSOT-ref, тронул файл политики), а не любой
  читаемый SHA. Агентская учётка (ai-prosto) в
  политику не входит, поэтому такой комментарий — человеческий акт того же класса, что
  мерж.
- **Рабочая версия** `W` — `<sha>` последнего (по `createdAt`) действительного
  подтверждения, иначе `P`. Выводится из форджа при каждой перепроверке.

**Чтение версий**: состав исторической версии (`P` или `<sha>`) читается
`repo_file_fact(repo, sha, path)` и разбирается тем же правилом, что
`policy_snapshot`; сам `policy_snapshot(pinned_sha=…)` для исторических версий не
используется — он отказывает `superseded` до чтения содержимого. Актуальная версия
`C` — существующий `policy_snapshot(pinned_sha=None)`. Семантика общего API не
меняется.

**Перепроверка** — в preflight, перед каждым обращением к соседу на стадии Need
(`start`, `status`, `brief`), перед публикацией `brief.md` и в `brief-approve` (§11.5):

1. `P` и подтверждения перевычисляются из форджа по `interview.approval.pr`
   (§11.4.2 п.3–7); не сошлось — стоп с причиной предиката.
2. `merged_by ∈ accounts(P)` — акт действителен по политике, под которой совершён.
3. `C == W` и `merged_by ∈ accounts(C)` — продолжение.
4. `C ≠ W` — **дрейф**: отказ preflight либо `stopped_interview`
   `upstream_policy_drift` с `P`, `W`, `C` и готовой строкой подтверждения
   `policy-reconfirm: <repo>@<C>` для человека. Сосед не вызывается.
5. `UNAVAILABLE` — стоп «политику прочитать не удалось», повтор той же командой;
   прочие `FORBIDDEN` — стоп с текстом источника.

Подтверждение мержера не заменяет: если `merged_by ∉ accounts(C)`, подтверждение не
помогает — п.3 отказывает (подтверждать нечего, нужен новый акт — `--repropose`, вне
E2). Комментарий, отредактированный или удалённый, перестаёт действовать — следующая
перепроверка увидит `W` прежним и, при `C ≠ W`, остановит прогон.

Флаг `--reconfirm-policy` **удалён**: подтверждение не локальное решение, а
человеческий комментарий; повтор команды после него проходит сам.

**Граница (Q2, решение владельца):** перепроверка политики — до `completed_at`. После
публикации сохраняются доказательства approval (`interview.approval`, координаты для
перепроверки) и неизменность source-слоя: blob `upstream.md` сверяется с
`upstream_blob` в E1 и при каждой материализации source-слоя бандла.

#### 11.4.4. Durable-копия и `upstream_blob`

После preflight и создания прогона буфер §11.4.2 пишется в
`run_dir/brief-input/00-discovery/upstream.md` атомарно; `interview.upstream_blob` =
git-blob SHA-1 буфера (над байтами, прошедшими preflight, после approve). Файл
оператора больше не открывается. `upstream_blob` и `approved_content_hash` не
сравниваются (разные алгоритмы и объекты).

Пересверка `blob(upstream.md) == upstream_blob` — перед каждым обращением к соседу,
перед публикацией и в E1; mismatch — стоп `upstream_blob_mismatch` до вызова соседа;
эталон не переустанавливается.

#### 11.4.5. Блокировка, session ID, принадлежность, восстановление

**Блокировка прогона — одна на вход**. Её берут **точки входа
процесса**, а не функции раннера: `spec_loop.main` (после выбора прогона, до чтения
его `run.json`), CLI `runner` (`resume` и прочие изменяющие подкоманды), `brief-propose`,
`brief-approve`. Контекст `run_lock(run_id)` — эксклюзивный неблокирующий `flock` на
`<RUNS_ROOT>-locks/<run_id>.lock` (уточнение ревизии 7; не внутри каталога прогона);
занято — выход кодом 1, «прогон исполняется другим процессом», без чтения состояния.
Гарантии файла блокировки:

- путь вычисляет ОДНА функция (`run_lock.lock_path`), и ею пользуются все входы;
- файл при освобождении **не удаляется**: удаление между двумя захватами дало бы два
  разных inode, и два процесса держали бы «одну» блокировку одновременно;
- крах после захвата, но до резервирования прогона не создаёт каталога прогона и
  тем более битого леджера — повторный запуск проходит;
- два конкурентных запуска одного `run_id` берут один и тот же файл, второй получает
  отказ «занято».

`find_runs` не меняется: каталог прогона без читаемого `run.json` по-прежнему битый
леджер и отказ. Под блокировкой `run.json` читается **заново** (состояние,
прочитанное до неё, не используется). `runner.resume`/`advance` и шаги блокировку не
берут, а **требуют**: принимают токен `RunLock` (без него — `TypeError`/assert в
тестах), так что цепочка `resume → advance` идёт под одной блокировкой без
самоотказа, а вызов мимо точки входа не компилируется в обход.

Дескриптор блокировки передаётся дочернему процессу соседа (`pass_fds`, без
`close_fds` для него): блокировка держится, пока жив хотя бы один процесс,
держащий описание файла. Цепочка `uv run --frozen --project … discovery` порождает
процессы `uv` → python; наследование дескриптора через неё проверяется **живым**
тестом (T38: родитель убит, `flock` из третьего процесса занят до выхода
discovery), не только argv-тестом. Не наследуемые иными дочерними процессами
(git, gh) дескрипторы закрываются (`O_CLOEXEC` по умолчанию в Python) — блокировку
держит только сосед.

**Id.** `s-<run_id>-e`, детерминирован от прогона; пишется в `interview.session_id`
**до** вызова `start`, той же записью `run.json`, что `interview-start → started`.
Перед каждым использованием сверяется с вычисленным от `run_id`; расхождение — стоп
`session_id_mismatch`.

**Штатный путь.** `start --session-id <id>` → 20, `next_action.session_id == id` →
`interview-start → completed`. Принадлежность доказана построением: сессию создал
этот вызов из этих байтов (`admit` скопировал durable-копию, блокировка исключает наш
параллельный вызов). Иное `next_action` — стоп.

**Восстановление** — `interview-start == started`, id записан, записи об ответе
`start` нет:

1. `status --session <id>` (повтор `start` не делается).
2. `status` → 20/0/10/11 — сессия с нашим id существует, но с каким upstream она
   создана, через публичный интерфейс не установить (F14). Стоп
   `session_unverifiable`; сосед больше не вызывается; id не меняется; подсказка —
   `--new-run --ws-id <fresh-id>`. Когда сосед отдаст отпечаток принятого upstream
   (заявка §11.9, п.1), правило: отпечаток == self-hash `upstream.md` прогона —
   присоединение, иначе `session_not_owned`.
3. `status` → 1 — **unknown** (F4): сессии может не быть, она может быть резервацией
   или не читаться, discovery может не запускаться. Пробный `start` не делается
   (достройка резервации у соседа не проверяет остатки журнала, F3, и код 20 не
   доказал бы, что интервью новое). Стоп `session_state_unknown` с
   `operation.reason` соседа; подсказка — проверить запуск discovery и
   `$DISCOVERY_HOME`, повторить команду (восстановление снова с п.1), либо `--new-run
   --ws-id <fresh-id>`.
4. Код без envelope (`argparse`, F4) — синтетический 1 по §6, далее как п.3.

**Цена fail-closed** (явно, в приёмку): потеря ответа успешного `start` (смерть
процесса в окне между ответом соседа и записью `completed`) делает прогон
невосстановимым до §11.9 — продолжение только `--new-run` с повтором интервью. Окно —
одна запись `run.json` после возврата subprocess.

`--session <id>` (§5.3) для engineer запрещён: id записан до вызова.

#### 11.4.6. Порт `Ops` и новые факты

```python
def discovery_start(self, frame: str, target: str, traces_to: str | None,
                    upstream_path: str | None, session_id: str | None,
                    cwd: str, lock_fd: int | None) -> DiscoveryReply
def discovery_approve(self, brief_path: str, repo: str, pr: int,
                      path: str, cwd: str, lock_fd: int | None) -> DiscoveryReply
def brief_pr_fact(self, repo_slug: str, pr: int) -> Fact[BriefPrFacts]
def find_brief_pr_fact(self, repo_slug: str, head_ref: str) -> Fact[list[int]]
def default_branch_fact(self, repo_slug: str) -> Fact[str]
def pr_comments_fact(self, repo_slug: str, pr: int) -> Fact[list[PrComment]]
def policy_version_fact_at(self, repo_slug: str, ref: str, path: str,
                           sha: str) -> Fact[bool]
```

- `start`: `upstream_path` ⇒ `--upstream` и **никакого** `--traces-to` (инвариант порта
  до subprocess: оба — синтетический 1); `session_id` ⇒ `--session-id`. Отказ на
  непустом `upstream_path` и `ENGINEER_BLOCKED` удаляются. `discovery_status`/`brief`
  тоже получают `lock_fd`. `approve` → 20 или код без envelope — синтетический 1.
- `BriefPrFacts` — поля **по состояниям**:
  - всегда обязательны: `state` (`OPEN`/`CLOSED`/`MERGED`), `base_ref`, `head_ref`,
    `head_sha`, `files: [(filename, status)]` — полный список с пагинацией;
  - `MERGED` — дополнительно обязательны `merged_by`, `merged_at`, `merge_commit`;
  - `OPEN`/`CLOSED` — эти три обязаны быть пусты (иное — `UNAVAILABLE`: форма,
    которой у состояния не бывает);
  - отсутствие обязательного поля, неизвестное `state`, неполный список файлов —
    `UNAVAILABLE`.
  Содержимое OPEN-предложения читается по `head_sha`, смерженного — по
  `merge_commit`; факт и чтение содержимого идут по одному SHA, смена головы между
  ними видна как расхождение SHA и ведёт к повторному чтению, а не к смешанному
  результату. `labels`/`body` в контракт **не** входят: доказательством они не
  служат; подсказки печатаются отдельным необязательным чтением. Существующий
  `pr_facts` не меняется.
- `PrComment`: `id`, `author`, `body`, `created_at`, `last_edited_at` (может быть
  пусто); полный список с пагинацией, неполный — `UNAVAILABLE`.
- `policy_version_fact_at` — `FOUND(True)`, если (а) `sha` — предок головы `ref`
  либо равен ей (`compare <sha>...<ref>`: `sha` — base, голова `ref` — head; предок даёт `ahead`,
  равенство — `identical`; `behind` и `diverged` — `FOUND(False)`) и (б) коммит `sha`
  менял `path` (список файлов коммита по форджу). `FOUND(False)` — установлено
  обратное; любая неполнота — `UNAVAILABLE`. Координаты `repo/ref/path` — только из
  SSOT источника политики. Используется для `P` заявки и `<sha>` подтверждения.
- `find_brief_pr_fact` — все PR (любое состояние) с головой `brief/<ws_id>`; больше
  одного — отказ «неоднозначно» в `brief-propose`.
- Чтение содержимого — существующий `repo_file_fact(repo, sha, path)`; локальный git
  для доказательств не используется.

### 11.5. `brief-approve` — зеркало акта

`make brief-approve RUN=<customer-run> PR=<n>` (под блокировкой customer-прогона):

1. По форджу — предикаты §11.4.2 п.3–7 (кроме сравнения с буфером); `<dir>` ==
   `<bundle_dir>/00-discovery` прогона; `brief_self_hash` заявки == self-hash
   `brief.md` прогона; перепроверка политики §11.4.3 (`merged_by ∈ accounts(P)`,
   `C == W`, `merged_by ∈ accounts(C)`). Дрейф — отказ с готовой строкой
   подтверждения: человек оставляет в brief-PR комментарий `policy-reconfirm:
   <repo>@<C>`, после чего повтор `brief-approve` проходит — путь одинаков для дрейфа
   до первого approve и после него. Отказ — без вызова соседа.
2. Бриф из merge-коммита (`repo_file_fact`) → `run_dir/brief-approval/customer-brief.md`
   атомарно. Файл есть: self-hash совпадает — не перезаписывается; иначе отказ.
3. `discovery approve <файл> --repo --pr --path <dir>/brief.md`.
4. **После вызова** — актуальная политика снова == `W` п.1 (`FOUND` той же версии).
   `UNAVAILABLE`/`FORBIDDEN`/иная версия — отказ «итог не подтверждён: подпись могла
   быть записана под сменившейся/неизвестной политикой».
5. **Успех требует всего вместе**: код вызова **0**, п.4, и перечитанный файл по §11.4.2
   п.1–2 и п.6. Иначе отказ, команда [6] не печатается:
   - 1 (в т.ч. синтетический) — «итог неизвестен»; подпись прежнего вызова не делает
     успешным текущий;
   - 2 `brief_bytes_diverged` — файл откатан в `draft`; прочие 2 — не тронут;
   - 10 — подпись **записана**, линтер отклоняет бриф (`gate=fail`);
   - 11 — подпись **записана**, `readiness=incomplete`: engineer-preflight такой
     upstream не примет (§11.4.2 п.1), хотя `admit` соседа readiness не проверяет (F2).
6. Успех печатает команду [6] дословно.

Повтор после успеха: п.2 не перезаписывает, `approve` над подписанным файлом даёт те же
байты (`stamp` детерминирован), код 0 — тот же итог.

### 11.6. Изменения базовых разделов

Внесены в базовые разделы §2–§10 выше (пометки «Ревизия 7»).

### 11.7. Тестовая матрица

Слои: `spec_loop`, `runner`, `RealOps`, `brief_tools` (обёртки и `human-merge.sh`),
`contract`, opt-in smoke с настоящим discovery. **Правило пар:** каждый отказ-предикат —
пара «один дефект → отказ» / «тот же вход без дефекта → успех»; в таблице двойник
указан, где он не очевиден. «Без эффектов» — `run.json`, дерево `run_dir` и цель байт в
байт, сосед не вызван.

| # | Слой | Случай | Ожидание |
|---|---|---|---|
| T1 | ops | engineer: `upstream_path` + `session_id` | argv `start --frame engineer --target … --upstream <p> --session-id <id>`, без `--traces-to`; `lock_fd` в `pass_fds` |
| T2 | ops | заданы `upstream_path` и `traces_to` | синтетический 1 до subprocess |
| T3 | ops | `approve` → 20 / непарсимый envelope / код без envelope | синтетический 1 |
| T4 | ops | `brief_pr_fact`: пагинация файлов; нет поля / неполный список | полный / `UNAVAILABLE` |
| T5 | spec_loop | `--brief-only` без `--need` / с engineer / с `--brief` / с `--session` | отказ до создания прогона; двойник — `--need --frame customer --brief-only` |
| T6 | spec_loop | повтор customer-прогона с иным значением `--brief-only` | отказ, без эффектов |
| T7 | runner | customer `--brief-only`: публикация | `brief_ready`, `completed_at`, `brief_blob`; ветки/worktree нет; S1 не вызван |
| T8 | runner | повтор/resume из `brief_ready` | без эффектов, печать следующего шага, код 0 |
| T9 | runner | crash между `replace` и записью статуса | §5.5 → `brief_ready`, не S1 |
| T10 | runner | customer **без** `--brief-only` | прежнее поведение: S1 (регрессия) |
| T11 | spec_loop | `--new-run` при совпавшем `brief_ready` | разрешён; двойник — совпавший после S1 → отказ |
| T12 | spec_loop | engineer без `--new-run` при совпавшем customer-прогоне | отказ с подсказкой |
| T13 | spec_loop | engineer без `--approval-pr`; `--approval-pr` с customer | отказ |
| T14 | spec_loop | draft-бриф | отказ; двойник — подписанный |
| T15 | spec_loop | подпись без `approved_content_hash` | отказ; двойник — с хэшем |
| T16 | spec_loop | правка тела после подписи | отказ (self-hash) |
| T17 | spec_loop | ручной конверт, точно зеркалящий проверенный мерж | **принят** |
| T18 | spec_loop | `approver ≠ merged_by`; `approved_at ≠ merged_at` | отказ (две пары) |
| T19 | spec_loop | PR не смержен | отказ |
| T20 | spec_loop | PR: только бриф без заявки; бриф + заявка + лишний файл; бандл-PR; файлы в разных каталогах; статус `modified`; тот же двухфайловый PR, смерженный в постороннюю ветку (`base_ref` ≠ ветке по умолчанию) | отказ (каждый — пара; двойник последнего — тот же PR в ветку по умолчанию) |
| T21 | spec_loop | заявка: битая схема / `purpose` иной / `brief` иной / `policy.repo|ref|path` ≠ SSOT | отказ (каждый — пара) |
| T21a | spec_loop + brief_tools | **уже смерженная через браузер** заявка: дубль ключа верхнего уровня; дубль внутри `policy`; второй YAML-документ; лишний ключ; `policy.sha` не 40 hex | отказ preflight и `brief-approve` (каждый — пара: та же заявка без дефекта → допуск) |
| T21b | contract | один модуль разбора у propose, human-merge, approve, preflight | все четыре дают одинаковый вердикт на общем наборе фикстур |
| T21c | ops/spec_loop | `P` — читаемый SHA боковой ветки репо политики с тем же путём; `P` — коммит SSOT-ref, не трогавший файл | отказ (каждый — пара: действительная историческая версия → допуск); то же для `<sha>` подтверждения. Через настоящий адаптер на записанных ответах compare: предок (`ahead`) → true; равный (`identical`) → true; потомок (`behind`) и `diverged` → false; неполный ответ → `UNAVAILABLE` |
| T22 | spec_loop | `brief_self_hash` заявки ≠ self-hash брифа в коммите; бриф в коммите ≠ буферу | отказ (две пары) |
| T23 | spec_loop | метки/тело PR изменены после мержа (сняли метку, переписали `policy:`) | решение не меняется: доказательство — содержимое коммита |
| T24 | spec_loop | локальный `.git` цели подменён, фордж подлинный | решение по форджу |
| T25 | spec_loop | мержер вне `accounts(P)` | отказ; двойник — внутри |
| T26 | spec_loop | `C ≠ P`, подтверждений нет | отказ дрейфа с `P`, `C` и строкой подтверждения; двойник — `C == P` |
| T27 | spec_loop | комментарий `policy-reconfirm: <repo>@C` от учётки ∈ `accounts(C)`, не редактирован, после `merged_at`; мержер ∈ `accounts(C)` | принят, `W = C`; двойники — каждый по одному дефекту → отказ: автор ∉ `accounts(C)` (в т.ч. ai-prosto); комментарий редактирован; создан до `merged_at`; `<sha>` не существует в истории; `<sha>` ≠ `C`; координаты ≠ SSOT; тело с лишним текстом; мержер ∉ `accounts(C)` |
| T28 | runner | подтверждение → resume (`C` не менялась) → новый коммит политики → resume → второе подтверждение → resume | продолжение; стоп дрейфа `W ≠ C`; продолжение (`W` — последнее по `createdAt`) |
| T29 | runner | дрейф `C ≠ P`, в `run.json` вписано любое поле «подтверждено», мержер ∈ `accounts(C)` | **стоп дрейфа**: `W` выводится только из комментариев форджа, локальная запись игнорируется |
| T30 | runner | подмена `interview.approval` в `run.json` (другой PR/коммит); отдельно — удаление/редактирование комментария-подтверждения после продолжения | перепроверка по форджу: предикаты заново, расхождение — стоп; без действующего подтверждения при `C ≠ P` — стоп дрейфа |
| T31 | spec_loop | любой факт форджа `UNAVAILABLE` | отказ «повторите», без «не одобрено» |
| T32 | spec_loop | upstream: `frame: engineer` / путевые `traces_to` / gate fail / `gate_passed` не `true` / CRLF | отказ (каждый — пара) |
| T33 | spec_loop | файл оператора заменён после чтения в буфер | копия и `upstream_blob` — из буфера |
| T34 | runner | durable `upstream.md` подменён между заходами | стоп `upstream_blob_mismatch` до вызова соседа |
| T35 | runner | после `completed_at` политика сменилась | E1 и далее идут: проверки политики нет (Q2); blob-сверка — есть |
| T36 | runner | `interview.session_id` ≠ `s-<run_id>-e` | стоп `session_id_mismatch` |
| T37 | runner | второй процесс того же прогона: spec-loop; CLI `runner resume`; `brief-propose`/`brief-approve` при занятом | выход по блокировке кодом 1, `run.json` не читался, без эффектов |
| T37a | runner | цепочка `resume → advance` под одной блокировкой | без самоотказа; вызов `advance` без токена `RunLock` — ошибка |
| T37b | runner | состояние, прочитанное до взятия блокировки, изменено другим процессом | под блокировкой читается заново, используется свежее |
| T38 | smoke (живой) | родитель убит при живом дочернем discovery через реальную цепочку `uv run … discovery` | `flock` из третьего процесса занят до выхода discovery; git/gh-потомки дескриптор не держат |
| T39 | runner | восстановление: `status` → 20 / 0 / 10 / 11 | стоп `session_unverifiable` (каждый), `start` не вызван |
| T40 | contract | вендоренный `self_hash` на фикстуре, подписанной `approve`; CRLF-вариант | == `approved_content_hash`; CRLF — тот же |
| T41 | runner | восстановление: `status` → 1; код без envelope | стоп `session_state_unknown`, `start` не вызван |
| T42 | runner | итоговый engineer-бриф: `traces_to` ≠ `["upstream.md"]` | стоп, бриф не публикуется |
| T43 | runner/E1 | source-слой `brief.md` + `upstream.md` | E1 проходит; двойник — подмена → отказ |
| T44 | brief_tools | `brief-approve`: код 0, политика до и после == `W`, файл сошёлся | печать команды [6] |
| T44a | brief_tools | дрейф между мержем и **первым** `brief-approve` | отказ без вызова соседа со строкой подтверждения; после комментария — повтор проходит |
| T44b | brief_tools | дрейф **после** успешного `brief-approve`, до engineer | engineer-preflight отказывает; после комментария — проходит |
| T45 | brief_tools | `approve` → 1 / каждая причина 2 / 10 / 11; код 0, но конверт не зеркалит мерж | отказ, без команды [6]; при 10/11 — «подпись записана» |
| T46 | brief_tools | политика сменилась во время `approve`; недоступна после | отказ «итог не подтверждён» |
| T47 | brief_tools | `brief-propose`: смержен / открыт / открыт с дрейфнувшим пином (исходный пин сохранён, дрейф сообщён) / закрыт / ветка без PR / ветка без PR после продвижения base / ничего / чужой файл в base / два PR одной ветки | как §11.3 п.4; два PR — отказ «неоднозначно» |
| T48 | brief_tools | найденный PR/ветка с той же формой, но иными байтами брифа; заявка с иным `run_id` / `ws_id` / `purpose` / координатами политики / несуществующим `policy.sha` при том же брифе | отказ (каждое поле — пара) |
| T48a | ops | `brief_pr_fact`: `OPEN` с непустым `merge_commit`; `MERGED` без `merged_by`; неизвестный `state`; голова сменилась между фактом и чтением содержимого | `UNAVAILABLE` / повторное чтение по новому SHA, без смешанного результата |
| T49 | brief_tools | `brief-propose` из прогона не в `brief_ready` / бриф `approved` / `brief_blob` расходится | отказ |
| T50 | brief_tools | `human-merge.sh` на `brief/*`: всё сошлось → мерж с `sha=<head_sha>`; отказы до мержа (каждый — пара): заявки нет; файлов не два / не `added` / в разных каталогах; заявка не по схеме; заявка неоднозначна (дубль ключа, второй YAML-документ); `purpose`/`brief` иные; координаты политики ≠ SSOT; `brief_self_hash` ≠ брифу того же SHA; пин ≠ актуальной; факт `UNAVAILABLE`; тело/метки «правильные», заявка неверна → отказ; голова сменилась между проверкой и мержем → фордж отказывает, повтор проверяет новую голову | как сказано |
| T50a | brief_tools | регрессия `human-merge.sh`: candidate §I12 (пин из тела, сверка версии), прочий PR с `human-merge-required`, `AUTHORIZED_APPROVER_ACCOUNTS` выставлена → отказ | поведение прежнее |
| T51 | smoke (opt-in) | настоящий discovery: подписанный бриф → `start --upstream --session-id` → `brief` | `traces_to: [upstream.md]`, E1 проходит |
| T52 | smoke (opt-in) | два одновременных `start` с одним id и разными upstream, без нашей блокировки | фиксирует поведение соседа (F3) для заявки §11.9; не гейт |

### 11.8. Живая приёмка (критерий чекбокса)

Цепочка §11.1.1 целиком на крошечном предмете с реальным стейкхолдером: customer
`--brief-only` → `brief_ready` → brief-PR → ревью → `make human-merge` → `make
brief-approve` → engineer-прогон до публикации брифа и E1 (S1). Негативные контроли
живьём: ручная подпись без self-hash — отказ preflight; `--approval-pr` на бандл-PR —
отказ preflight.

**Уточнение критерия (решение владельца 2026-10-10, приёмка
`docs/evidence/2026-10-10-engineer-route-live-run.md`):**

- Шаг engineer-прогона засчитывается публикацией engineer-брифа, E1 и материализацией
  source-слоя. Авторинг S1 для этого не требуется: в той приёмке **E1 выполнен,
  авторинг S1 не начат** (`stopped_preflight`, в цели нет `profiles/team-exp.yaml`).
  Это не утверждение, что S1 пройден целиком.
- Второй негативный контроль заменён: `--approval-pr` на **смёрженный PR недопустимого
  состава** (меняет только посторонний файл, смержен в ветку по умолчанию; upstream —
  тот же корректный подписанный бриф). Ожидание — отказ preflight `pr_files` до
  создания прогона и вызова discovery. Настоящий бандл-PR этим контролем **не
  проверен**.

**Объём гарантии (Q5):** гарантия происхождения approval — **только для engineer-входа
через `--need`**; engineer-вход через E1 `--brief` её не даёт (F10) — отдельный пункт
TODO. **Известное ограничение восстановления:** до заявки §11.9 п.1 потеря ответа
успешного `start` делает engineer-прогон невосстановимым — продолжение через
`--new-run` с повтором интервью (§11.4.5); ограничение принято владельцем 2026-10-08.
Обе оговорки — в тексте evidence.

Evidence — `docs/evidence/<дата>-engineer-route-live-run.md`.

### 11.9. Заявка соседу (discovery, inbox по ADR-ECO-006)

Своими руками у соседа не правим. Заявка заведена 2026-10-08 — **discovery#63**
(`slug: session-upstream-provenance`):

1. Публичный отпечаток принятого upstream у сессии (envelope `status`/`brief` или
   рендер брифа): self-hash `upstream.md` сессии. Снимает `session_unverifiable`.
2. Типизированное состояние у `status`: «сессии нет» / «резервация» / «не читается».
   Позволит безопасно повторять `start` после unknown.
3. Эксклюзивная и проверяющая достройка резервации: два одновременных `start` с одним
   id не пишут header оба; достройка отказывает при остатках журнала.

До ответа E2 работает fail-closed (§11.4.5); штатный путь от заявки не зависит.

### 11.10. Решения владельца (2026-10-08)

| Вопрос | Решение |
|---|---|
| Q1 | `--brief-only` + терминальный `brief_ready` для customer — **да** (§11.2) |
| Q2 | Проверка политики — до `completed_at`; далее — доказательства approval и неизменность source-слоя (§11.4.3) |
| Q3 | Write-ahead id для customer — отдельная задача |
| Q4 | `--repropose` — вне E2 |
| Q5 | Ужесточение E1 `--brief` — отдельный TODO; гарантия — только для engineer `--need`, явно в приёмке (§11.8) |
| Расширение | `human-merge.sh` для `brief/*` — в объёме E2: заявка по head SHA, мерж только проверенного SHA, отказы до мержа, тело/метки не заменяют заявку, candidate без изменений (§11.3 п.6, T50/T50a) |
| Восстановление | Невосстановимость после потери ответа успешного `start` до §11.9 — принята для E2, явно в приёмке (§11.8) |

### 11.11. Вне объёма

- Вариант Б; ужесточение E1 `--brief` (Q5); `--repropose` (Q4); write-ahead id для
  customer (Q3).
- Защита локальных артефактов от намеренной подмены — песочница авторского агента.
- Долг соседа `admit-hash-total` — у нас закрыт строже (§11.4.2 п.2), у соседа остаётся.
