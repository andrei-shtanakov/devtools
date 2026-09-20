# E2: стадия Need вызывается прогоном — `spec-loop --need`

Дата: 2026-09-15. Статус: accepted (владелец, 2026-09-15, после ревизии 3).
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
| D5 | Итоговый бриф остаётся `status: draft`, отдельного approval-стопа нет. Честно: мерж бандл-PR и §I12 одобряют governance-узлы, не discovery-источник; автоматически бриф в `approved` не превращается. Если customer-brief позже станет upstream для engineer — отдельный явный акт approval, отдельный TODO-контур. Контур заведён 2026-09-20: `TODO.md` `@id:discovery-brief-approval-act` (акт и место подписи — решение владельца) |
| D6 | Engineer-маршрут до discovery#49 fail-closed до run-id; customer-маршрут полностью рабочий. **С 2026-09-18 ожидание кончилось** (discovery#50 доставил приём upstream и caller-assigned session id): отказ остаётся верным, но его причина — «не реализован», а не «ждёт соседа»; текст `ENGINEER_BLOCKED` правится вместе с реализацией маршрута (`TODO.md` `@id:spec-loop-need-engineer-route`) |

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

`source_pin` в состоянии **нет**: публичный envelope его не несёт.

Статусы: `waiting_interview` и `stopped_interview` — оба персистентные.
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
| `status` (повтор) | 20 | из `waiting_interview`: состояние не меняется (`run.json` байт в байт); из `stopped_interview` (10/11 или 1/2 ранее): статус → `waiting_interview`, findings-файл удаляется; в обоих случаях печать `next_action` и команды ответа, spec-loop — код 0. Для любого `status`/`brief` → 20 `next_action.session_id` обязан совпасть с записанным; неполный `next_action` или чужой id — fail-closed стоп |
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

Сегодня `_report_state` для `stopped_*` не зовёт `resume`; для
`stopped_interview` это меняется явно — стоп интервью продолжаемый.

Строка «1 (findings, шаблон ответа)» — по факту два случая различаются
наличием findings-файла (`run_dir/interview-findings.txt`, §5.1: пишется
только при 10/11). Если файл существует, печатается его путь и шаблон
ответа. Если `stopped_interview` пришёл от кода 1/2 (findings-файл не
пишется — §5.1), findings-путь печатать нечего: вместо него печатается
recovery-подсказка «восстановите ту же сессию `<id>` и повторите либо
`--new-run --ws-id <fresh-id>`» — та же форма, что и для сироты выше.

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

### 5.4. `--new-run`

Существующий прогон с теми же (repo, subject) матчится всегда, поэтому
«создайте новый workstream с другим `--ws-id`» (нынешняя подсказка E1)
неисполнимо — заменяется на `--new-run --ws-id <fresh-id>`. Явный `--ws-id`
обязателен: без него снова вычислится прежний `<slug>-<date>` и сработает
существующий collision guard по `ws_id` (`spec_loop.py:738`). `--new-run`
взаимоисключающ с `--run-id` и `--session`. Разрешён, только если **все**
совпавшие прогоны стоят до S1 (`waiting_interview`/`stopped_interview`);
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
- crash: `interview-brief` `started` без tmp и без `brief.md` → повторный
  рендер и обработка по полной таблице; только `.brief.tmp` → повторный
  рендер и публикация; `brief.md` без дескриптора → повторный рендер,
  байты равны → op завершён без `replace`; байты не равны → стоп;
- recovery `--session`: разрешён только при `started` без `session_id`;
  H1/frame/traces_to/sessions сверяются; чужая роль — отказ; повторный
  `--session` при записанном id — отказ;
- engineer (за флагом готовности discovery#49): mismatch `upstream_blob`
  останавливает прогон до обращения к discovery.

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
