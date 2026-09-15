# E2: стадия Need вызывается прогоном — `spec-loop --need`

Дата: 2026-09-15. Статус: draft, на ревью владельца (дизайн согласован по
секциям в сессии 2026-09-15; решения владельца зафиксированы ниже).
Источник: принятый план
`prograph-vault/authored/notes/2026-09-13-pipeline-interview-to-implementation-plan.md`,
§3 E2 и §4 (Need только с реальным стейкхолдером; публикуется только бриф;
окно до брифа — принятое локальное исключение). Исполняемые пункты —
`TODO.md` `@id:spec-loop-need-stage` (customer) и
`@id:spec-loop-need-engineer-route` (engineer, `@blocked_by` discovery#49).

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
| D5 | Итоговый бриф остаётся `status: draft`, отдельного approval-стопа нет. Честно: мерж бандл-PR и §I12 одобряют governance-узлы, не discovery-источник; автоматически бриф в `approved` не превращается. Если customer-brief позже станет upstream для engineer — отдельный явный акт approval, отдельный TODO-контур |
| D6 | Engineer-маршрут до discovery#49 fail-closed до run-id; customer-маршрут полностью рабочий |

## 3. Интерфейс

```
make spec-loop SUBJECT='…' REPO=… \
  ARGS='--need --frame customer|engineer --stakeholder <role> \
        [--traces-to <file>] [--session <id>] [--new-run]'
```

Preflight — весь до run-id, до леджера, до любого вызова соседа:

- `--need` и `--brief` взаимоисключающи.
- `--frame` обязателен, без дефолта.
- `--stakeholder` обязателен. Отказ без него называет правило владельца
  («стадия Need запускается только при наличии реального стейкхолдера») и
  маршрут через `--brief`. Значение только записывается — это декларация,
  не машинная проверка.
- **customer**: `--traces-to` запрещён (лишний вход — отказ, не игнор).
- **engineer**: ровно один `--traces-to <file>`. Preflight: gate pass;
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
- `--session <id>` — только recovery (§5.3). `--new-run` — §5.4.
- Повтор команды находит прогон по (repo, subject), как сейчас. Повтор с
  иными `--frame`/`--stakeholder`/`--traces-to` при леджере в
  `waiting_interview` или `stopped_interview` — отказ: координаты интервью
  зафиксированы стартом; сменить их — `--new-run`.

## 4. Состояние

`RunState.interview: dict | None`:

| поле | значение |
|---|---|
| `session_id` | id сессии discovery; `None` до успешного `start` |
| `frame` | `customer` \| `engineer` |
| `stakeholder_role` | декларация D3; подставляется в `--role` |
| `target` | `repo_slug` цели — то, что уходит в `discovery start --target` и стоит в H1 брифа |
| `traces_to` | переносимая ссылка (относительное имя) или `null` |
| `upstream_blob` | hash durable-копии upstream или `null` |
| `brief_rel` | `brief-input/00-discovery/brief.md` |
| `started_at`, `completed_at` | метки; `completed_at` = момент `state.brief` |

`source_pin` в состоянии **нет**: публичный envelope его не несёт.

Статусы: `waiting_interview` и `stopped_interview` — оба персистентные.
Операции: `interview-start`, `interview-brief` — write-ahead, как остальные.
До `completed_at` `_step_branch` не достигается: ни ветки, ни worktree, ни
авторинга.

## 5. Переходы

### 5.1. Таблица по кодам discovery

Коды — публичный контракт discovery (README «What a caller reads»):
`1 > 2 > 20 > 10 > 11 > 0`.

| Вызов | Код | Переход |
|---|---|---|
| `start` | 20 | `session_id` записан, `interview-start` → `completed`, run → `waiting_interview`; печать `next_action` и команды ответа |
| `start` | 1, 2 | `stopped_interview`; `interview-start` остаётся `started`, `session_id is None`; S1 не вызывается |
| `status` (повтор) | 20 | состояние не меняется (`run.json` байт в байт); печать `next_action` и команды ответа; spec-loop — код 0. `next_action.session_id` обязан совпасть с записанным; неполный `next_action` — fail-closed стоп |
| `status` | 0 | `interview-brief` → `started`; `discovery brief --out <run_dir>/brief-input/00-discovery/.brief.tmp`; **код `brief` — по этой же таблице**; при 0: `inspect_brief` полного source-слоя на tmp, сверка координат (§5.3), `os.replace` → `brief.md`, `state.brief`, `completed_at`, `running`; далее S1 по E1 |
| `status`/`brief` | 10, 11 | `stopped_interview`; `findings`/`readiness_findings` → `run_dir/interview-findings.txt`; печать шаблона `discovery answer --session <id> --role <role> --question <QUESTION_ID> --supersede --file <answer.yaml>` — `question_id` подставляет человек, из findings он не выводится; spec-loop — код 1 |
| любой | 1, 2 | `stopped_interview` с `operation.reason`; координаты не трогаются; код 1 |
| `brief` 0, но tmp не проходит `inspect_brief` или сверку координат | — | `stopped_interview`; tmp не становится `brief.md`; S1 не вызывается |
| сессия исчезла / tmp нечитаем | — | `stopped_interview`, fail-closed: сессию не пересоздаём и не ищем; подсказка `--session <id>` или `--new-run` |

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
| `stopped_interview` | сверка координат → `runner.resume` → остался `stopped` | 1 (findings, шаблон ответа) |
| `stopped_interview` | … → `waiting`/`running` | как выше |

Сегодня `_report_state` для `stopped_*` не зовёт `resume`; для
`stopped_interview` это меняется явно — стоп интервью продолжаемый.

Печатаемые команды строятся через `shlex.quote` (роль с пробелами,
пути с пробелами).

### 5.3. Recovery: `--session <id>`

Сиротство: `interview-start == started`, `session_id is None` (процесс умер
между вызовом `start` и записью). Только в этом состоянии разрешён
`--session <id>`; при записанном `session_id` — отказ, замена сессии
невозможна. Присоединение fail-closed по **фактической форме брифа**
(приватный `header.json` не читается): `discovery brief --out <tmp>` (пишет
артефакт в любой фазе), затем:

- точное равенство строки H1 с `# Discovery Brief — {target} ({frame}-фрейм)`
  (`render.py:371`);
- `interview.frame == frame`; `traces_to == [traces_to]` или `[]`;
- `interview.sessions` — список **уникальных ролей из ответов**, до первого
  ответа пуст: допустимо только `[]` или ровно `[{participant_role:
  <stakeholder>}]`; любая чужая или дополнительная роль — отказ.

При совпадении `session_id` записывается и `interview-start` → `completed`;
дальше обычный `status`.

Сиротство исчезнет по построению после discovery#49 п.2 (caller-assigned
session id, записанный write-ahead); `--session` останется аварийным входом.

### 5.4. `--new-run`

Существующий прогон с теми же (repo, subject) матчится всегда, поэтому
«создайте новый workstream с другим `--ws-id`» (нынешняя подсказка E1)
неисполнимо — заменяется на `--new-run`. Разрешён, только если **все**
совпавшие прогоны стоят до S1 (`waiting_interview`/`stopped_interview`);
если хотя бы один достиг S1 — отказ с перечнем и `--run-id` (дубль
workstream: ветка уже есть). Старые леджеры и сессии не меняются; их
`session_id` печатаются для ручной уборки.

### 5.5. Crash-recovery `interview-brief`

Итоговый бриф session id не несёт, поэтому существования `brief.md`
недостаточно. При `op == started` и `state.brief is None`:

- есть только `.brief.tmp` (гибель до `replace`) — повторный рендер в
  новый tmp, проверка, обычная публикация;
- есть `brief.md` — повторный `discovery_brief` записанного `session_id`
  во второй tmp, требуется код 0, **байты равны** durable `brief.md`,
  повтор сверки координат и `inspect_brief` полного source-слоя, и только
  затем op завершается **без повторного `replace`**; расхождение байтов —
  стоп: durable-файл не совпадает с сессией, выбирать сторону молча нельзя.

Это бесплатная reconciliation, не повтор интервью.

### 5.6. Engineer: `upstream_blob`

Durable-копия upstream кладётся в `brief-input/00-discovery/` до `discovery
start`; её hash — `interview.upstream_blob`. Пересверяется перед каждым
использованием engineer-сессии и перед финализацией дескриптора; mismatch
останавливает прогон **до** обращения к discovery. После доставки копия —
второй файл source-слоя, как в E1.

## 6. Порт discovery в `Ops`

```python
@dataclass(frozen=True)
class DiscoveryReply:
    code: int          # см. транспортный контракт
    envelope: dict     # разобранный JSON stdout ({} при синтетическом 1)
    stderr: str

def discovery_start(self, frame, target, traces_to, upstream_path: str | None) -> DiscoveryReply
def discovery_status(self, session_id) -> DiscoveryReply
def discovery_brief(self, session_id, out_path) -> DiscoveryReply
```

`upstream_path` — durable-копия из `run_dir` (не файл оператора); до
discovery#49 реализация отказывает на непустом значении тем же текстом, что
preflight, — порт менять после разблокировки не придётся.

Запуск: `uv run --frozen --project <workspace>/discovery discovery …`,
`cwd` — каталог прогона. `target` для `start` — `repo_slug`.

Транспортный контракт (проверка согласованности границы, **не** второй
вычислитель `protocol.exit_code`):

- валидный контракт ⇒ `code = process.returncode`;
- непарсимый JSON, не-object, отсутствие обязательных полей (`lifecycle`,
  `gate`, `readiness`, `next_action`, `findings`, `readiness_findings`,
  `operation`), неизвестный код, невозможная для кода форма envelope
  (например, 20 без `next_action.question_id`/`session_id`) ⇒ синтетический
  `code 1` с `operation.reason`, называющим дефект.

## 7. Передача в E1 и идемпотентность

- `brief.md` появляется только через `os.replace` после `inspect_brief`
  полного source-слоя; `state.brief` — только после `replace`. Дальше
  `_step_materialize_brief` E1 без изменений: он читает `run_dir/brief-input`
  и сверяет байты с дескриптором.
- Повторный `spec-loop --need` при существующем леджере **никогда** не зовёт
  `discovery start` — только `status`; `start` идёт ровно один раз на
  прогон.
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
- `brief` ≠ 0 — по таблице, tmp не становится `brief.md`;
- `brief` 0, но tmp не проходит `inspect_brief`/сверку координат →
  `stopped_interview`, без `replace` и S1;
- crash: только `.brief.tmp` → повторный рендер и публикация; `brief.md` без
  дескриптора → повторный рендер, байты равны → op завершён без `replace`;
  байты не равны → стоп;
- recovery `--session`: разрешён только при `started` без `session_id`;
  H1/frame/traces_to/sessions сверяются; чужая роль — отказ; повторный
  `--session` при записанном id — отказ;
- engineer (за флагом готовности discovery#49): mismatch `upstream_blob`
  останавливает прогон до обращения к discovery.

**spec_loop**: CLI-preflight (все отказы до run-id: без `--stakeholder`, без
`--frame`, `--need`+`--brief`, customer с `--traces-to`, engineer до inbox);
`--new-run` разрешён только при прогонах до S1 и **не меняет** старые
леджеры; координаты повторного вызова; печатаемые команды shell-safe (роль с
пробелами через `shlex.quote`); коды выхода 0/1 по §5.2.

**RealOps**: argv `start`/`status`/`brief` (в т.ч. `--frozen --project`,
`upstream_path` — отказ до inbox); JSON boundary: каждая форма негодного
envelope → синтетический 1, валидный контракт → `returncode` не
переопределяется.

**Интеграционный smoke**: настоящий `discovery` с временным
`DISCOVERY_HOME`: `start` → 20 с настоящим `next_action`, `answer` через
CLI, `status` → 0, `brief` → файл проходит `inspect_brief`. Opt-in (маркер
как у зондов к spec-runner), чтобы обычный pytest не зависел от соседа.

Negative controls обязательны для трёх гвардов: сверка роли при
присоединении, требование кода 0 у `brief`, сравнение байтов при recovery.

## 9. Живая приёмка

Один прогон `make spec-loop … ARGS='--need --frame customer --stakeholder
<role>'` на реальном предмете с реальным стейкхолдером: пауза, ответы через
`discovery answer` вне spec-loop, повтор команды до `waiting_human_merge`,
дальше как E1 до approved tasks-спеки. Evidence в `docs/evidence/`:

- SHA devtools и discovery; session id; код каждого вызова; SHA-256 брифа и
  SHA-256 journal/transcript сессии (сам транскрипт не публикуется — §2.4
  плана);
- факт отсутствия ветки/worktree в цели во время `waiting_interview`
  (`git branch --list spec/<ws-id>-behaviour` пуст, `git worktree list`);
- объявленная stakeholder role; итоговый descriptor source-слоя;
- номера bundle-/tasks-/approval-PR и approved tasks-спека.

Чекбокс `behaviour-document-runner-residuals` закрывается этим прогоном
только если `author_backend=disp` действительно использован и его ledger/pin
(`disp_slug`, `disp_anchor_dir`) отражён в evidence.

## 10. Вне объёма

- Обёртка ответов (B), интерактив (C), два интервью в одном прогоне.
- Approval брифа как отдельный стоп (D5); превращение customer-брифа в
  approved upstream для engineer — отдельный TODO-контур.
- Engineer-маршрут до discovery#49 (п.1); caller-assigned session id и
  метаданные в envelope (п.2–3) — улучшения, не блокеры.
- Публикация транскрипта; переносимое хранилище журнала (план §4).
