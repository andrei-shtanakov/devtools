# Живая приёмка E2 — `spec-loop --need` на предмете spec-runner#480

Дата: 2026-09-15. Пункт плана: `TODO.md` `@id:spec-loop-need-stage` (E2 плана
«интервью → реализация», prograph-vault 2026-09-13). Спека:
`docs/superpowers/specs/2026-09-15-need-stage-design.md` §9. Код E2: devtools#246
(мерж `72ca635`).

## Объявление прогона

| | |
|---|---|
| subject | Durable continuation checkpoint и evidence для run/call/attempt (spec-runner#480) |
| repo / target | spec-runner, `andrei-shtanakov/spec-runner` |
| run-id | `durable-continuation-checkpoint-evidence-20260915-4c2a3e` |
| ws-id | `durable-continuation-checkpoint-evidence-20260915` |
| frame / stakeholder | customer / `spec-runner-owner` (декларация оператора, D3) |
| author_backend | disp (авторинг behaviour-узла через document-пайплайн disputatio) |
| SHA devtools | `72ca635` (master) |
| SHA discovery | `9baf5d6` (ветка `docs/refresh-baseline-grounding-decision`, от master отличается только `TODO.md`) |
| SHA spec-runner | `6bd1dc3` (master, чистый, = origin) |
| discovery session | `s-63347ea83272`, `$DISCOVERY_HOME` = `~/.discovery` (умолчание) |

Роли в интервью: содержание всех 19 ответов — стейкхолдер `spec-runner-owner`
(владелец, в чате); транскрипция в YAML и вызовы `discovery answer` — оператор
(fleet-агент devtools). Транскрипт не публикуется (§2.4 плана); хеш — ниже.

## Стадия Need — коды вызовов

| # | вызов | код | lifecycle / gate / readiness | next_action |
|---|---|---|---|---|
| 0 | `start --frame customer --target andrei-shtanakov/spec-runner` | 20 | awaiting_input / — / — | customer.goals.01 |
| 1–17 | `answer` ×17 (goals.01–03, personas.01–02, jobs.01–02, functions.01–03, nfr.01–02, constraints.01–02, success_metrics.01–02, out_of_scope.01) | 20 | awaiting_input / pass / incomplete → **ready** после out_of_scope.01 | следующий вопрос банка |
| 18 | `answer` (out_of_scope.02) | 20 | awaiting_input / pass / ready | risks.01 |
| 19 | `answer` (risks.01) | **0** | complete / pass / ready | {} |
| — | `status` (повтор кнопки) | 0 | complete / pass / ready | — |
| — | `brief --out …/.brief.tmp` | 0 | complete / pass / ready | — |

Записи брифа: G-01…03, P-01…04, J-01…03, FR-01…09 (8 Must, 1 Should),
NFR-01…07, CON-01…08, M-01…07, OUT-01…10; findings гейта — 0 на каждом шаге.

## Пауза `waiting_interview` — отсутствие ветки и worktree

Между `start` (код 20) и повтором кнопки после кода 0:

```
$ git -C spec-runner branch --list 'spec/*' | wc -l   → 0
$ git -C spec-runner worktree list | wc -l            → 1   (только основной чекаут)
$ ls out/governance-runs/durable-…-4c2a3e/            → run.json (status waiting_interview)
```

Проверено дважды: сразу после `start` и непосредственно перед повтором кнопки.
Ветка `spec/durable-continuation-checkpoint-evidence-20260915-behaviour`
появилась только после публикации брифа (S1).

## Публикация брифа и descriptor source-слоя

| | |
|---|---|
| SHA-256 transcript (`journal.jsonl`, 38 событий) | `e24b6b2effdf3e4c256ba88ef11ec56dafcb3c81e727e48657ce0b99ae74d0d5` |
| SHA-256 `brief.md` | `bd826c29d31d4a186f11f2fa1c8944eac46bf5dfc12a84caa61328a686cf2285` |
| git-blob `discovery-brief` (descriptor) | `3f6e98075ebcf037371445458cc3a2cd752dd830` |
| `interview.completed_at` | `2026-09-15T13:55:51+00:00` |
| descriptor | `frame: customer`, `primary: 00-discovery/brief.md`, `source_paths: [00-discovery/brief.md]` |
| ops | `interview-start` completed (session_id), `interview-brief` completed (brief_blob), `branch` completed, `materialize-brief` completed (те же blobs) |

Бриф остаётся `status: draft` (D5); `generated_at` = `created_at` сессии.

## Ход прогона после публикации брифа

S1 (`branch`) и materialize-brief завершились в том же вызове кнопки,
что и публикация брифа (ops в таблице выше); дальше — авторинг узлов.

## Авторинг: charter, requirements (codex-путь) и behaviour-узел через disp

Charter и requirements авторятся штатным путём (`author-charter`,
`author-requirements` — completed, exit=0, skipped=False, оба с
`brief_context`). Первый вызов disp на behaviour-узле дал два дефекта
нашей стороны — оба в коде, который до этого прогона живым не проверялся
(PR #203/#242, чекбокс `behaviour-document-runner-residuals` намеренно
ждал живого прогона):

| # | стоп | причина | исправление |
|---|---|---|---|
| D-1 | `stopped_author`, disp код 2: `[agents]/[limits] непригодны: нет обязательного ключа agents.author.adapter` | `_write_disp_doc_config` писал пустые `[agents.author]`/`[agents.reviewer]`/`[limits]`, считая их «дефолтами соседа»; по SPEC-002 §3.2 `adapter`/`model` обязательны у обеих ролей, `[limits]` — четыре обязательных целых | devtools PR #248 (agent-merge `fc07867`): `ops.disp_agent(role)` — адаптер/модель из харнесс-слоя оператора (author ← `AUTHOR_*`, reviewer ← `REVIEW_*`; claude → `claude_code`, codex → `codex`; codex без модели — стоп с причиной до вызова соседа), `[limits]` — значения примера `docs/document-pipeline.md` |
| D-2 | `stopped_author`, disp код 2: `рабочее дерево не готово к run: … untracked-пути вне .disputatio/` (`00-charter.md`, `10-requirements.md`) | раннер отдавал соседу бандл незакоммиченным; document-pipeline соседа (§2) на первом PROPOSING делает `reset --hard` + `clean` и fail-closed отказывает на любом постороннем untracked-пути | devtools PR #249 (agent-merge `b29d797`): перед первым `run` (не перед `resume`) раннер коммитит бандл и source-слой E1 той же процедурой, что S3 (`_commit_bundle`) |

Оба стопа — честные: статус `stopped_author`, причина в логе, пины
`disp_slug`/`disp_anchor_dir` в run.json записаны до первого вызова
соседа (`beh-durable-continuation-checkpoint-evidence-20260915`,
`<run-dir>/disp-anchors`), каталог пайплайна сосед не создал ⇒ повтор —
снова `run`, не `resume` (ровно один путь retry, #204 п.3). Кнопка
`spec-loop` на `stopped_author` отказала (продолжает только
`waiting_human_merge`/`completed`), продолжение — `make behaviour-run
ARGS='resume --run-id …'` после каждой правки.

После D-2 (resume на master `b29d797`): коммит `4fcee1d` на ветке
`spec/durable-continuation-checkpoint-evidence-20260915-behaviour` — три
файла (`00-charter.md`, `00-discovery/brief.md` с `-f`, `10-requirements.md`,
1717 строк); каталог `.disputatio/pipelines/beh-durable-continuation-checkpoint-evidence-20260915`
создан соседом — пайплайн пошёл.

### Behaviour-узел через disp — факты пайплайна

| | |
|---|---|
| slug / каталог | `beh-durable-continuation-checkpoint-evidence-20260915`, `spec-runner/.disputatio/pipelines/<slug>/` (у соседа untracked, в бандл не входит) |
| adapters | author `claude_code` / `claude-opus-5`, reviewer `claude_code` / `claude-opus-5` (харнесс-слой оператора, PR #248) |
| анкер P9 | `<run-dir>/disp-anchors/a5323a965ad7679c` — вне дерева цели, пин `disp_anchor_dir` в run.json |
| фазы | IDLE → DOC_LOOP (14:43:38Z) → EXPORTING (`document_converged`, 15:21:25Z) → DONE; `exported partial=false` |
| doc-сессия | `doc-r1`, outcome `converged`; 2 раунда (анти-сикофантия: раунд 1 approve'ом не закрывается) |
| коммиты соседа на ветке | `95d54f4` «disputatio: round 001» (+1105), `c8c0473` «disputatio: round 002» (+72/−17) |
| узел | `15-behaviour-spec.md`, 1160 строк, blob `5cb4398372028daa2c0d9f0823f06c09b84e0c9c` |
| runner op | `author-behaviour` completed, exit=0, skipped=False |

Дальше раннер пошёл штатно: `author-design` completed (exit=0; upstream-пины
на requirements/behaviour сверены `git hash-object`), `author-acceptance`
started.

## S3–S6: коммит, гейты, bundle-PR, ревью

| | |
|---|---|
| S3 `commit` | completed; source-слой `00-discovery/brief.md` в HEAD сверен по blob (`3f6e9807…`) |
| S4 `gate-candidate` | completed, exit=0 (профиль `profiles/team-exp.yaml`) |
| S5 `push`/`pr`/`ready` | completed; **bundle-PR spec-runner#522**, голова `a39e77cc262a8b163f1e32bd9ceeae093b899750`, база `6bd1dc3`, 7 файлов, +5348 |
| S6 `review` | **stopped_review** — «прибор не отработал» (D-3) |

| # | стоп | причина | исправление |
|---|---|---|---|
| D-3 | `stopped_review`, кит код 2: `диф больше поддерживаемого одним прогоном: 7 файл(ов), 447260 байт (потолки 30 / 400000)` | бандл из семи узлов превысил умолчание кита по байтам; кит предлагает поднять потолок явно (`local.sh --max-diff-bytes`), но `review-pr.sh` флаг не пробрасывал — пути восстановления не было; S6 раннера (`RealOps.review` без параметров) тем более | devtools PR #250 (харнесс-путь, **мерж — человек**): `--max-diff-bytes N`/`--max-diff-files N`, env-фолбэк `REVIEW_MAX_DIFF_BYTES`/`REVIEW_MAX_DIFF_FILES` для S6 раннера; один набор в оба вызова кита (отпечаток и полный прогон); источник потолка — в шапке вердикта. Ревью #250 — из доверенного дерева (драйвер origin/master, head через `FLEET_ROOT`), 2 круга, «находок нет» |

Стоп честный: комментарий «прибор не отработал» на PR #522, статус
`stopped_review`, ops `gate-candidate`/`push`/`ready` сохранены (голова не
уехала). Продолжение после мержа #250:
`REVIEW_MAX_DIFF_BYTES=500000 make behaviour-run ARGS='resume --run-id durable-continuation-checkpoint-evidence-20260915-4c2a3e'`.

### S6, круг 1 (dry-run с поднятым потолком, до мержа #250)

Dry-run ревью #522 драйвером из головы PR #250 (`--max-diff-bytes 500000`,
ничего не опубликовано): **request-changes** — 2 major, 3 minor, все с
evidence по коду spec-runner, оба major сверены с деревом до правки
(`_acquire_run_lock` — только `cmd_run` без `--force`, `cli.py:217`;
три платных сайта `cli_plan.py:170/660/797`). Фикс бандла на PR-ветке —
коммит `a90bcf2` (5 узлов, +369/−130; цепочка `upstream_hashes` пересчитана
и сверена; `gate-check --candidate` 0 error(s)). Решения, вынесенные в
бандл и требующие взгляда владельца: точка run-start в диспетчере по
множеству платящих подкоманд (incl. `retry`/`watch`/`run --force`);
`plan:interactive` — третий сайт; три новых stop reason и девятый closure
kind `idle_timeout` (аддитивно, с CHANGELOG-нотой); `run_claude_async`
предложен к удалению вместе с публичным экспортом (production-вызовов нет).
Продолжение после мержа #250: resume из `stopped_review` сбрасывает диапазон
commit→review — S4 на новом содержимом, push, свежее ревью.

### S6, круг 2 (после мержа #250, голова `a90bcf2`)

Resume из `stopped_review` на master `0898d88` с `REVIEW_MAX_DIFF_BYTES=500000`:
диапазон commit→review сброшен, S4 `gate-candidate` completed (exit=0) на
новом содержимом, S5 push — голова #522 `a90bcf2`. Ревью S6 отработало,
но публикация сорвалась на финальной сверке головы (`gh` GraphQL —
`dial tcp … i/o timeout`) → `stopped_review` «прибор не отработал», вердикт
платного вызова потерян (сетевой инцидент, не дефект контура; повтор через
`--dry-run --write-verdict` → `--use-verdict`, чтобы второй сбой сети не
потерял вызов). Вердикт круга 2 опубликован от ai-prosto:
**request-changes** — 1 major (Q-12: `spec-runner reset` удаляет DB с
индексом open calls без audit → open call повторяется молча; сверено:
`cli_info.py:568` `cmd_reset` → `state_file.unlink()`), 4 minor (инвентарь
выходов `run`, AC-30 и lock, ярлык BEH-07 в charter/requirements,
task-history/audit-log против SSOT #478). Пять находок круга 1 закрыты и
не повторяются. Шапка вердикта несёт факт поднятого потолка
(`--max-diff-bytes 500000 (флаг)`). Фикс круга 2 — на PR-ветке (субагент).

Фикс круга 2 — коммит `59a45a8` (6 узлов, +398/−172; цепочка
`upstream_hashes` 9/9 сверена; `gate-check --candidate` rc=0), запушен
вручную (S5 на resume — no-op). Решения в бандле, требующие взгляда
владельца: Q-12 — двуветочная процедура старта (без `last_run_id` в DB open
calls поднимаются из store по новому индексу workstream-а; `reset` платящей
подкомандой не становится); инвентарь выходов `run` — таблица на 16 строк,
`RUN_STOP_REASONS` — шесть значений, governance-гейт — четвёртый гард
старта; `dry_run` — отдельный kind closure; семейство `error_<kind>`
(сверх находок: без него правило «stop-reason вне словаря — отказ
сериализации» роняло бы closure на рядовом проваленном прогоне);
task-history/audit-log — в bundle срезами прогона через BEH-23 → AC-21 →
DT-13. Круг 3 — dry-run с `--write-verdict`, публикация `--use-verdict`.

### S6, круг 3 (голова `59a45a8`)

**request-changes** — 1 major: kind closure не определён для платящих
подкоманд вне `_run_tasks_inner` (`retry`, `watch`, `doctor`, `plan`,
`review-pr`, `tdd *`, `budget authorize`, `restore`) — прямое следствие
решения круга 1 (run-start в диспетчере для всех платящих подкоманд) и
инвентаря круга 2, построенного только для `run`. Опись росла по кругу —
круг 4 делает её полной сразу: все выходы всех подкоманд
`PAYING_SUBCOMMANDS` плюс общее правило вывода kind из исхода handler-а.
Пять находок круга 2 (1 major + 4 minor) закрыты и не повторяются.

Фикс круга 3 — коммит `bef9887` (5 узлов, +376/−41; цепочка 9/9; gate
rc=0), запушен. Инвентарь пересобран для всех десяти платящих подкоманд
(у `run` — три сайта вне прежней границы; у остальных девяти — 56 сайтов
с файлом, строкой и исходом; `restore` — из design, подкоманды в дереве
ещё нет). Design §6.3 — общее правило вывода kind при несообщённой
причине: `completed` даёт единственная клетка «код 0 и работа выполнена»;
`RUN_STOP_REASONS` не растёт, растёт `CLOSURE_KINDS` (двенадцать
closure-only reason'ов). Новый BEH-46 (46 сценариев), AC-27, DT-10.
Сомнение для владельца: `watch --tui` — сайты в daemon-треде сообщают
причину process-wide `RunContext` (новая проводка, симметрично `run --tui`).

### S6, круг 4 (голова `bef9887`)

Диф вырос до 534 863 байт — кит отказал на моём же потолке 500 000 ещё до
платного вызова (fp-режим), повтор с `--max-diff-bytes 700000`.
**request-changes** — 1 major (restore проверяет open calls только по
восстанавливаемому `run_id`, тогда как FR-02 требует namespace-wide:
restore более раннего run повторил бы open call позднего run молча), 3 minor
(классификация rc 2 у `plan --gated` как infrastructure_error; `evidence
close-call`/`purge` меняют continuation-state, но вне `PAYING_SUBCOMMANDS`;
рецепт BEH-05 с настоящим именем `claude` блокируется существующим гвардом
`_no_real_agent_calls`). Инвентарь выходов круга 3 принят.

Фикс круга 4 — коммит `95ebf36` (цепочка 9/9; gate rc=0), запушен; диф
PR — 7 файлов, +6539. Решения: проверка restore namespace-wide по индексу
workstream-а, различитель ветвей Q-12 — meta `continuation_index`
(`local`/`restored`) вместо `last_run_id`; таблица путей open calls /
continuation-state — design §2.6 (15 строк, одно место); rc 2 у `plan
--gated` → `spec_governance → policy_refusal`, интерактивный цикл без
стадии → `no_ready`; `evidence close-call`/`purge` — в `PAYING_SUBCOMMANDS`;
гвард `_no_real_agent_calls` переезжает на одно имя `paid_call._spawn`.
Сомнения для владельца: restore более раннего `run_id` отказывает и без
open call у позднего прогона (откат на ранний checkpoint закрыт как
операция — «иное решение владельца — один флаг и одна ветка», §7.2); два
одновременно живых каталога с одним `workstream_key` — ограничение FR-02,
не закрыто; переезд гварда меняет поведение harness-а тестов.

### S6, круг 5 (голова `95ebf36`)

Первый запуск убит системой по нехватке памяти до вердикта (платный вызов
не потерян — verdict-файла нет); повтор detached. **request-changes** —
2 major high (инвентарь `run` не видит путь «задача не выполнена, стоп не
сработал» → дефолт `completed` при exit≠0; семейство `error_<kind>`
ключуется на несуществующем kind `infrastructure` — в `ERROR_KINDS` дерева
есть `instrument`), 1 major medium (reason'ы closure у DT-09/DT-12 без ребра
к DT-10), 3 minor (kind vs reason в BEH-11/42, AC-08/38; счёт платящих
подкоманд; DT-02 правит файл владельца DT-14). Пять кругов S6: каждый фикс
закрывает находки круга и открывает 1–2 major следующего в той же зоне —
таксономия closure §6.3. Решение о продолжении — владельцу.

Решение владельца (2026-09-16): один круг с мандатом на упрощение, жёсткий
стоп после него. Фикс круга 5 — коммит `57fe973` (597/−810; полный диф PR
6539 → 6326 вставок; цепочка 9/9; gate rc=0), запушен. Модель closure:
пять kind'ов (`completed`, `refused`, `failed`, `interrupted`, `crashed`),
одна функция из кода выхода и исхода работы в `finally` диспетчера;
`reason` — свободная строка; `note_stop`, `error_<kind>`, `CLOSURE_KINDS`
по stop-reason'ам, closure-only причины и оба инвентаря сайтов удалены;
`RUN_STOP_REASONS` не растёт. Утрачены три различения (названы в design,
FR-07, BEH-46): отказ правила vs поломка инструмента у подкоманд без
attempt'ов; код 1 vs 2 (остаётся в `exit_code`); таймер vs «нечего
делать». Неправдивый `status` на ранних остановках — «Вне объёма» явной
строкой.

### S6, круг 6 (голова `57fe973`) — жёсткий стоп

Первый запуск: ревьюер (`harness-claude`) завершился кодом 1 без вердикта
(лимит расходов подписки; владелец поднял лимит), повтор. **request-changes**
— 1 major (kind `interrupted` не выводим из «как handler ушёл»: в дереве
SIGINT/SIGTERM не завершают handler — `_signal_handler` в `main()` лишь
поднимает флаг), 3 minor (`error_kind` gate-отказа — `policy`, не
`hook_failure`; BEH-46 пропускает `watch` с красной pre-run validation;
прозаические ссылки на blob upstream-узлов устарели). Пять major-зон
предыдущих кругов закрыты: упрощение сработало — остался один узкий
механический вопрос. По решению владельца — стоп; PR #522 в
request-changes, run `stopped_review`.

Решение владельца: один точечный микро-круг (четыре находки, без
редизайна). Первый запуск субагента-фиксера упал на месячном лимите
расходов подписки (владелец поднял); повтор — коммит `3907996` (цепочка
9/9; gate rc=0), запушен. Решения: `interrupted` — вариант (a), диспетчер
читает `executor._shutdown_requested` как третий факт (оговорка:
stop-marker в него не попадает; FR-07 и п. (3) §6.3 сужены до «таймер
или stop-marker»); `error_kind` gate-отказа — `policy`; `watch` с красной
pre-run validation — в перечне `completed` BEH-46; blob-ссылки в прозе
заменены ссылкой на frontmatter. Круг 7 — dry-run с `--write-verdict`.

### S6, круг 7 (голова `3907996`) — стоп

**request-changes** — 2 major (оба в зоне open calls / Q-12, не closure:
маркер `continuation_index: local` пишется `RunContext.start()` до того,
как его читает процедура open calls, — ветка (2) после restore
недостижима; процедура объявлена для `retry`/`watch`, но единственный
названный сайт — `_run_tasks_inner`, через который они не проходят), 1
minor (acceptance ссылается на снятое замечание к upstream). Четыре
находки круга 6 закрыты; зона closure (§6.3) больше не даёт находок.
Решение владельца 2026-09-16 — остановка и передача (см. «Итог» ниже).

### S6, круг 8 (после возобновления сессии 2026-09-17, голова `2fec3f8`)

Владелец решил продолжить точечным фиксом ровно трёх находок круга 7.
Фикс — коммит `2fec3f8` (решения шире буквы двух находок: `start()`
маркер `continuation_index` больше не пишет, ставит сама процедура на
успешной ветке (2); новый именованный рубеж `cli._run_start_gate` — общая
точка для `run`/`retry`/`watch`, три сайта). Цепочка 9/9, gate rc=0,
запушен. Три находки круга 7 закрыты фиксом (обе major зоны open calls
и minor устаревшую ссылку в acceptance); ревью вернулось в ту же зону
новой находкой: **request-changes** — 1 major high
confidence (правило «любой более поздний прогон workstream-а без open
calls → отказ `needs-human`» из круга 4 делает документированный путь
восстановления недостижимым: сама дверь `evidence close-call`, закрывающая
open call, создаёт более поздний прогон без open calls и тем самым
навсегда блокирует restore — BEH-09/AC-08 при этом утверждают, что после
close-call restore применяется; внутреннее противоречие трёх узлов), 1
major medium confidence (`restore` пишет свой run-start в индекс до
собственных проверок — попадает под своё же правило отказа), 2 minor
medium (`doctor` в эфемерном scratch-проекте; перенос гварда не называет
тип отказа). Итого восемь кругов S6, три зоны major по очереди: сайты/лок
(1–2), closure (3–6), open calls (2, 4, 7, 8). Решение о продолжении —
снова за владельцем.

### S6, круги 9–10 (2026-09-17) — правки владельца и ручной мерж #522

После круга 8 владелец сам отработал находку правила круга 4 (коммиты
spec-runner `4104711` «исключение evidence close-call/purge из правила
«более поздний прогон без open calls блокирует restore»» и `d6f11c5`
«пересчёт upstream_hashes») и смержил **spec-runner#522** напрямую из
GitHub (`973081b`, `mergedBy: andrei-shtanakov`) — минуя S7 раннера.

### D-4: resume из `stopped_review` не реконсилировал мерж вне S7

Раннер стоял в `stopped_review` (S6 red, op `review` = `started`).
Reconciliation «PR уже `MERGED`» существовала только для
`waiting_human_merge`/`stopped_merge_refused` — из `stopped_review` `resume`
сбросил бы `commit`→`review` и попытался `push_branch` на ветку, которую
GitHub уже удалил (`--delete-branch` при мерже) — неперехваченная ошибка.

| # | причина | исправление |
|---|---|---|
| D-4 | реконсиляция «PR MERGED» не покрывала `stopped_gate`/`stopped_review`/`stopped_author` | devtools PR #253 (agent-merge `ce24cb8`, 4 круга ревью): общий `_reconcile_pr_merged_out_of_band`, короткое замыкание в `advance()` на `op "merge" == completed` (покрывает и нетерминальный отказ S8), `state.base_ref` из фактов PR, гард грязного дерева перед переходом на S8 (тот же, что `task_bridge.deliver_for_run`) |

Resume после мержа #253 (`make behaviour-run ARGS='resume --run-id
durable-continuation-checkpoint-evidence-20260915-4c2a3e'`, master
`ce24cb8`): реконсиляция сработала на первом же заходе — `merge`
зафиксирован (`merged=True`), `sync-default`/`gate-authoritative`
отработали (exit=0) без переигрывания `review`/`push`/`commit`. **Run
`durable-continuation-checkpoint-evidence-20260915-4c2a3e` — `completed`.**

## Итог

| Требование §9 спеки | Факт |
|---|---|
| один прогон `spec-loop --need --frame customer --stakeholder <role>` на реальном предмете с реальным стейкхолдером | выполнен: spec-runner#480, стейкхолдер `spec-runner-owner`, 19 ответов через `discovery answer` вне spec-loop |
| пауза `waiting_interview` без ветки/worktree/авторинга; повтор кнопки → `brief` → S1 по E1 | выполнено (разделы выше: 0 `spec/*` веток, 1 worktree; descriptor, blobs) |
| SHA devtools/discovery, session id, коды вызовов, SHA-256 брифа и transcript | записаны |
| stakeholder role, descriptor source-слоя | записаны |
| номера bundle-/tasks-/approval-PR, approved tasks-спека | **bundle-PR spec-runner#522** смержен (`973081b`); **run `completed`** (S8 authoritative gate exit=0 на master); tasks-/approval-PR — **ещё не заведены**, отдельный шаг `make behaviour-tasks` (E1-путь) |
| `behaviour-document-runner-residuals`: disp использован, `disp_slug`/`disp_anchor_dir` в evidence | выполнено (раздел «Behaviour-узел через disp»): пайплайн сошёлся за 2 раунда, узел экспортирован, пины в run.json |

Стадия Need, путь до S5 и итоговый мерж бандла отработали до конца.
Терминальное ревью бандла заняло восемь кругов (major-зоны сменяли друг
друга: сайты и lock → таксономия closure → процедура open calls) — все
закрыты фиксами субагентов на PR-ветке (гейт `--candidate` зелёный,
цепочка `upstream_hashes` сверена каждый раз: коммиты `a90bcf2`,
`59a45a8`, `bef9887`, `95ebf36`, `57fe973`, `3907996`, `2fec3f8`,
`4104711`, `d6f11c5`) плюс правки владельца на последнем круге и ручной
мерж. Четыре дефекта devtools, найденные живым прогоном, исправлены и
влиты: #248, #249 — agent-merge; #250 — харнесс, мерж владельца; #253
(reconciliation мержа вне S7) — agent-merge, 4 круга ревью.

Что остаётся: `make behaviour-tasks ARGS='--run-id
durable-continuation-checkpoint-evidence-20260915-4c2a3e'` — черновик
tasks.md-спеки из смерженного бандла (approve — человек, §I12, узел за
узлом). Чекбокс `spec-loop-need-stage` закрывается по этому evidence —
Need-стадия и путь до completed run доказаны живым прогоном; approved
tasks-спека и сама реализация spec-runner#480 — отдельный, последующий
шаг E1, не блокирующий закрытие E2.

Уроки для конвейера (не для этого прогона): бандл в 6.3k строк за один
disp-проход — за пределами того, что терминальное ревью подтверждает за
разумное число кругов; потолок кита пришлось поднимать трижды (умолчание
400k → 500k → 700k в ходе прогона → 800k для продолжения). Кандидат в
план: ограничение размера behaviour-узла или дробление на несколько PR —
отдельным пунктом TODO по решению владельца. Второй урок — из D-4:
reconciliation «мерж вне контролируемого пути» была реализована только
для тех стопов, где она была впервые нужна (`waiting_human_merge`), а не
как общее свойство раннера — обобщить такие гварды на весь класс входа,
не на конкретный наблюдённый случай.
