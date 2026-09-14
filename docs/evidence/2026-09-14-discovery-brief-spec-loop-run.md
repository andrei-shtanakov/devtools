# Живая приёмка E1 — `spec-loop --brief` на предмете spec-runner#485

Дата: 2026-09-14. Пункт плана: `TODO.md` `@id:spec-loop-brief-input` (E1 плана
«интервью → реализация», vault 2026-09-13). План: `docs/superpowers/plans/2026-09-13-discovery-brief-input.md`
(Task 8, live sequence). Код E1: devtools#219 (мерж `e2aea14`).

## Итог

Полный путь пройден без ручных артефактов конвейера (бриф, бандл, tasks-спека и код задач авторились агентами под гейтами); операторские фикс-коммиты по находкам терминального ревью на integration-PR #509 и #512 и форматирование red-файла TASK-001 зафиксированы в теле ниже — это вмешательства вне границ плана, и они названы: engineer-фрейм discovery → бриф →
`make spec-loop --brief` → bundle-PR с source-слоем → §I12 (6 узлов) → tasks-PR →
approve владельца → `run --all --strict` до terminal result (4/4 задач `success`,
integration-PR-ы #508, #509, #512, #513 приняты через `make accept-pr`).
Стоимость исполнения по леджеру spec-runner: $49.23. Все ручные акты — только
границы, объявленные планом (одобрение брифа, мерж бандла, §I12, approve
tasks-спеки, санкция правки `conftest.py`).

Найдено и закрыто по дороге (каждая находка — issue/PR, не обход):

| находка | класс | закрытие |
|---|---|---|
| source-слой `00-discovery/` не попал в коммит S3 (ignore-правило цели) | дефект E1 devtools | devtools#220 (force-add + fail-closed сверка blob-ов) |
| 12 человеческих мержей на бандл (§I12 candidate + finalize) | стоимость процесса | решение владельца → devtools#221 `@id:finalize-pr-agent-merge-default` |
| `--approve-node` без `AUTHORIZED_APPROVER_ACCOUNTS` инвалидирует заявку | операторская ошибка | память/ранбук; лишний candidate #492 |
| RED-absorption не гоняет `ruff format`; green red-файл трогать не вправе | класс spec-runner | spec-runner#507; лечение `tdd repair` |
| conftest-пояс подменял `Popen` функцией — ленивый импорт `mcp` в изоляции красный | предсуществующий дефект тестов | spec-runner#510 → #511 (санкция владельца) |
| review-таймаут 15 мин на диффе ~1000 строк | лимит инструмента | retry прошёл; наблюдение |
| `run_task` спавнил не тот argv, что проверял симулятор | major в коде задачи | фикс-коммит на #509 |
| E2E «потерянный stop»: инвертированный предикат, признак попытки по размеру DB | major в тестах задачи | фикс-коммит на #512, подсадка мутанта |
| текст ответа discovery не рендерится (только entries) | наблюдение рантайма discovery | кандидат в inbox discovery |

## Объявление прогона

| | |
|---|---|
| Предмет | spec-runner#485 «MCP: унифицировать launch scope для run_task, stop и read tools» (источник — план владельца 2026-09-14 + текст issue) |
| Репо-цель | `andrei-shtanakov/spec-runner`; чистый клон `devtools/out/live/spec-runner-e1` @ `0159103` (merge #489) |
| devtools | `master` @ `e2aea14` (merge #219 — код E1) |
| discovery | `master` @ `5c6e52f`, рабочее дерево чисто; vendored pin upstream `ee93092f` |
| Роли | интервьюер — fleet-агент (Claude, эта сессия); стейкхолдер — владелец репо; **ответы транскрибированы агентом из письменного плана владельца и issue #485**, без LLM-генерации; одобрение customer-брифа владельцем = проверка транскрипции |
| `DISCOVERY_HOME` | дефолт `~/.discovery` (боевой); репетиция engineer-фрейма — в scratch-home, в evidence не идёт |

## Фаза A — customer-фрейм (выполнено 2026-09-14)

| | |
|---|---|
| session | `s-95dad36c2db3` |
| вопросов выдано | 19 (весь банк, 9 тем) |
| событий журнала | 40: 19 `question_asked`, 20 `answer_recorded`, 1 `answer_superseded` (NFR-03 согласован с NFR-02: soak 20× — отдельный `slow`-тест) |
| конверт | `lifecycle: complete`, `gate: pass`, `readiness: ready`, exit 0 |
| бриф | `out/discovery-briefs/spec-runner-485/discovery-brief-customer.md` |
| `brief_sha256` | `fa9c9935ea34d2c5918715e54ee55dbc42ed6fd38454cb3b79b78fd314bf8e17` (status: draft) |
| `journal_sha256` | `acab9a5d6459d14e490472cf269054a308b97b11179e8f23b5e9dbb8db4c5143` |
| независимый линт (vendored gate devtools) | `PASS (0 errors, 0 warnings)` |
| содержание | G-01..03, P-01..03, J-01..03, FR-01..06 Must + FR-07/08 Should + FR-09 Could, NFR-01..03, CON-01..04, M-01/02, OUT-01..04, RK-01/02, Q-01/02 (architect, non-blocking), конфликтов нет |

Ответы (транскрипт) — `answers/customer/*.yaml`; драйвер интервью — scratch `interview.sh`
(status → answer по `next_action.question_id`, ветвление по коду выхода, не `&&`).

## Репетиция engineer-фрейма (scratch, не evidence)

Scratch `DISCOVERY_HOME`, копия customer-брифа с `status: approved` только для репетиции.
15 вопросов, 6 тем → `lifecycle: complete`, `gate: pass`, `readiness: ready`; vendored gate `PASS`;
`governance.brief_input.inspect_brief` принял пару: `frame=engineer`,
`source_paths=['00-discovery/brief.md', '00-discovery/discovery-brief-customer.md']`.

**Находка рантайма discovery (кандидат в inbox discovery):** свободный `text` ответа в бриф
не рендерится — только `entries`. Поэтому feasibility-вердикт «ок» по Must-FR (GC-05:
id упомянут в теле) невозможно дать прозой; в первом прогоне GC-05 упал по FR-01/02/05/06,
хотя вердикты были в `text`. Решение: вердикты живут в AP-записях («реализует FR-NN»).
Фрейм engineer обещает вердикт «ок / X / Q», а рантайм даёт только X/Q + чужие записи.



## Фаза B — engineer-фрейм и spec-loop (выполнено 2026-09-14)

Одобрение customer-брифа владельцем (сообщение в сессии) записано в frontmatter:
`status: approved`, `approved_by`/`approver: andrei-shtanakov`, `approved_at: '2026-09-14'`,
`owner_role: product`; vendored gate PASS; `brief_sha256` (approved) —
`6e1ea895a81cdc94ac0ba1edbda8b0e82867baee5894106b3733890172d660a0`.

| | |
|---|---|
| engineer session (боевой `~/.discovery`) | `s-3725ab6f59e1` |
| вопросов | 15 (6 тем), exit 0: `complete` / `pass` / `ready` |
| engineer brief | `discovery-brief-engineer.md`, sha256 `7c8affdc50b560fa23145eca5bf1fd27a84ca82595814e1a2923eaf70b060921` |
| `journal_sha256` | `d1cbb43ad8d90d61c8d305108c7bc84fcf350fbe85c62d14ebb8d18af672a849` |
| E1 intake | `inspect_brief`: frame engineer, `source_paths` `[00-discovery/brief.md, 00-discovery/discovery-brief-customer.md]`, blobs `39e8c625…` / `4d7900d7…` |
| target checkout | чистый клон `out/live/spec-runner-e1` @ `0159103` (merge #489) |
| команда | `make spec-loop SUBJECT="MCP launch scope (spec-runner#485)" REPO=spec-runner ARGS="--brief …/discovery-brief-engineer.md --target-dir …/out/live/spec-runner-e1"` |
| run-id / ws-id | `mcp-launch-scope-spec-runner-485-20260914-4712d0` / `mcp-launch-scope-spec-runner-485-20260914` |
| ops | branch → materialize-brief (blobs = descriptor) → author ×6 (charter/requirements с BriefContext) → commit → gate-candidate (0 err) → push → pr → ready → review |
| bundle-PR | spec-runner#490, head `81c07d7` → фикс `d6a1364` |
| ревью круг 1 (ai-prosto, claude-opus-5) | request-changes: 1 major + 4 minor (+2 Copilot) — см. ответ в PR |
| ревью круг 2 | **approve** на `d6a1364`; CI зелёный (lint, typecheck, test 3.11–3.13, governance/gate, review-kit-integrity) |
| статус прогона | `waiting_human_merge` — S7 по политике: мерж владельцем |

### Находка живой приёмки — дефект E1 (devtools), закрыт отдельным PR

Source-слой был материализован в рабочем дереве, но **не попал в коммит S3**: `.gitignore`
spec-runner держит `workstreams/*/spec/*` с carve-out только `!*.md`; подкаталог `00-discovery/`
под carve-out не попадает, `git add -- <bundle_dir>` пропустил его молча, bundle-PR уехал без
файлов, на blob-ы которых пинуется charter (ревьюер это и поймал как «источников в дереве нет»).
Фикс: `RealOps.commit_paths(force_paths=…)` — `git add -f` поштучно для source-файлов, и
fail-closed сверка blob-ов source-слоя в HEAD после коммита (`stopped_author`, push не идёт);
регрессии — подсадка ops без `-f` и настоящий git с ignore-правилом цели. В живом PR файлы
добавлены `git add -f` фикс-коммитом, blob-ы совпали с пинами charter.

### Прочие находки круга 1 (валидные, приняты)

- major: E2E FR-05 наблюдало «вторая задача не начата», что для `run --task` вакуумно
  (одна задача, проверка marker-а только до неё, `cli.py:1273`) — переопределено в
  детерминированный вариант + инвариант двух исходов.
- `spec_governance` представим run-only флагами `--strict/--no-strict`; `--profile` у `run`
  нет (ревьюер ошибся в этой половине).
- lock-файл: `.executor-state.lock`, текст `PID:/Started:`, не JSON; tracked `spec/.gitignore`
  в дереве нет; ссылка BEH-07(в)/DT-03 на чужой тест снята.


## Фаза C — мерж бандла, §I12, tasks-PR, approve, исполнение (2026-09-14)

| шаг | факт |
|---|---|
| мерж bundle-PR #490 | владелец → `1c910dd` |
| `make spec-loop` (повтор) | реконсиляция мержа, S8 gate на `1c910dd` (0 error, 2 warn GC-STAGE), доставка штатно отказала: DAG не одобрен |
| §I12, charter | candidate #492 (мерж без `AUTHORIZED_APPROVER_ACCOUNTS` — заявка invalidated, операторская ошибка) → candidate #493 → finalize #494 |
| §I12, requirements | #495 → #496 |
| §I12, behaviour-spec | #497 → #498 |
| §I12, design | #499 → #500 |
| §I12, acceptance | #501 → #502 |
| §I12, decomposition | #503 → #504 (`480f000`) |
| стоимость ритуала | 6 узлов × 2 PR = 12 человеческих мержей (+1 ошибочный) — решение владельца: finalize мержит агент по умолчанию, человеческий мерж finalize по настройке, триггер по команде — devtools#221, `@id:finalize-pr-agent-merge-default` |
| approved charter | `status: approved`, version 3, source-пины `discovery-brief`/`discovery-customer` сохранены через одобрение |
| `make spec-loop` (повтор) | `completed`; tasks-PR #505 (draft): `spec/mcp-launch-scope-spec-runner-485-20260914-tasks.md` (4 задачи = DT-01…04, пин `decomposition: 8c3f26c2…`) + `workstreams/…/evidence/s8-gate-verdicts.jsonl` одним коммитом |
| ревью #505 | approve, 1 minor (S8 snapshot pre-approval — контракт E0.6b, отвечено) → мерж обвязкой → `d15be92` |
| approve tasks | владелец в клоне `out/live/spec-runner-e1`: `spec approve tasks` → v2, approved_by andrei-shtanakov 07:33:04Z |
| conform-approve | мост → PR #506 (frontmatter: approved, validation pass) → ревью approve → мерж → `ddcf7dd` |
| preflight | клон: готов (основной чекаут: FAIL prefixless-db — пустая схема от 2026-09-12, не трогал) |
| запуск | `uv run --frozen spec-runner --spec-prefix=mcp-launch-scope-spec-runner-485-20260914- run --all --strict` в клоне @ `ddcf7dd`, detached (start_new_session + caffeinate), лог `out/live/run-485.log`, 11:37 local |

## Фаза D — исполнение spec-runner (2026-09-14)

| задача | факты |
|---|---|
| TASK-001 (DT-01) | run `559bb1c8`: попытки 1–3 → LINT_FAILURE (`ruff format --check` red-файла; RED-absorption без format — **spec-runner#507**), $12.99, `on_task_failure: stop`. Лечение: `ruff format` red + коммит `be0195d` → `tdd repair --commit be0195d` (линия `ef5fe283` → `f999517a`, red повторно подтверждён; коммит поверх без repair — `claim violated`). retry `e9b43e09` отказал до repair; retry `065613d1`: green `1642f7c` зелёный, ревью **timeout 15 мин** → «review not_run», blocked (частичные правки ревьюера сохранены патчем); retry `ed8c229f`: green, REVIEW_FIXED (`5c5742a`), verify, done — 567 с; по леджеру итого 6 попыток, $22.36 (retry `ed8c229f` — шестая). Интеграционная ветка не открылась (checkout base поверх незакоммиченных правок ревьюера) → PR с ветки задачи **#508** → `make accept-pr` |
| TASK-002 (DT-02) | run `cf4a1929`: 1 попытка, $11.06, RED → green → REVIEW_FIXED → done за 1298 с; влита в интеграционную ветку `spec-runner/run-20260914-152431` → integration-PR **#509** → `make accept-pr` |
| TASK-003 (DT-03, verify_first) | replay `tests/test_lazy_mcp_import.py` в worktree: exit 1 → классифицировано `test_failure` → RED-authoring без `TDD_SELECTOR` ($2.23) → stop. Причина **предсуществующая** (воспроизведена на `ddcf7dd` до TASK-001): conftest-пояс подменяет `subprocess.Popen` функцией, ленивый импорт `mcp` в изоляции падает `TypeError` — **spec-runner#510**. Detail evidence в state DB (`verify_evidence` id=1, commit `f60e32ad`) |
| #509 (TASK-002) ревью | круг 1: major — `run_task` проверял argv из `child_argv`, а в `Popen` передавал прежнюю укороченную команду (fail-open по представимым полям) + 3 minor + 2 Copilot; операторский фикс-коммит `93a9037` (spawn == validated argv; refinement Q-01 через serializer; `Irreproducible` без значений; сквозной BEH-14), mypy чист, 3171 passed; круг 2 — сетевой таймаут GitHub API; круг 3 — approve, мерж `2a5d61a` |
| #510 / #511 | владелец санкционировал правку `tests/conftest.py` вне workstream-а: `_belted_popen` → подкласс `Popen` с отказом в `__init__`, регрессии (подписываемость, негативный контроль, изолированный прогон `test_lazy_mcp_import.py`); ревью approve без находок, squash-мерж |
| TASK-004 (DT-04) | `run --task TASK-004 --strict` @ `2a5d61a`, run `f6a4ff7b`; попытка 1 — **timeout 30 мин** (`task_timeout_minutes: 30`) в green, 11 файлов зарескьюены в stash; попытка 2 стартовала 17:38 |
| TASK-004, попытка 2 | green за 18 мин, тесты/линт зелёные, ревью **REVIEW_FAILED** (`ready_file` без `spec_prefix` — реальный баг; пробел покрытия `publish_ready_file`/`clear_ready_file`) → stop |
| TASK-004, retry (`--timeout 60`) | run `597e8c32`: green с контекстом находок, тесты, REVIEW_PASSED, done за 652 с ($11.65 всего) → integration-PR **#512** |
| #512 ревью, круг 1 | major — предикат «потерянный stop» инвертирован, признак попытки = размер state DB (всегда непуст) + 2 minor (BEH-26 без отказных веток и претензия README; ложная ссылка на пояс в BEH-27). Операторский фикс-коммит `ed0a207`: `_outcome_after_stop` по леджеру, исходы (i)/(ii), soak по вердикту (12/8 на 20 итерациях), отказные ветки под `--change`, `_assert_fixture_agent`, README про `.executor-progress.txt`. Подсадка: безусловный `clear_stop_file` перед предзадачной проверкой → immediate/survives/soak красные; перестановка ready↔clear ненаблюдаема (окно микросекунды) |
| TASK-003, retry после #511 | run `2e426fd3`: verify-first всех трёх файлов группы зелёный (2/19/23 члена), green-подтверждение, REVIEW_PASSED, done за 500 с → integration-PR **#513** |
| #513 (TASK-003) | accept-pr: approve, merge `f770cac` |
| #512 (TASK-004), круги 2–3 | круг 2: approve на `ed0a207`, но CI red — та же расовая ветка (b) BEH-26 (`TASK-999` отвечает `started`, т.к. ready публикуется до поиска задачи); фикс `de6aa14` — ранний выход настоящим child через `child_entry` с `exit 3`; полный `-m "not slow"` 3195 passed rc=0; итог кругов 3–6 — строкой ниже |
| состояние state | все четыре задачи `success`; итог по леджеру $49.23 (TASK-001 $22.36 / TASK-002 $11.06 / TASK-003 $4.17 / TASK-004 $11.65) |
| #512 (TASK-004), круги 3–6 | круги 3–5: отказ «база уехала» при живом `origin/master` = базе вердикта — `merge-pr.sh` сверяет пин с GitHub `baseRefOid`, не обновляемым без update-branch (**devtools#223**); `PUT /pulls/512/update-branch` → head `7bff651`, круг 6: approve, merge `7d3def0` |
| терминальное состояние | spec-runner master `7d3def0`; `spec-runner status`: 4/4 `success`, tasks-спека 4/4 DONE, `sync OK`; клон чист (5 rescue-stash раннера — остатки попыток, работа влита) |

## Что показала приёмка про сам конвейер

- E1 работает end-to-end: source-слой едет бандлом, пины charter переживают §I12, tasks выводятся из approved DAG, S8-verdict едет в tasks-PR (E0.6b), прогон возобновляется той же кнопкой после каждой человеческой границы.
- Человеческих актов — 17 (одобрение брифа, мерж бандла, 12 мержей §I12 + 1 ошибочный, approve tasks, санкция правки conftest) — узкое место названо и записано решением (devtools#221).
- Инструментальные классы стоили денег: TASK-001 — 6 попыток ($22.36) из-за #507 и review-таймаута; TASK-003 — $4.17 на предсуществующем дефекте тестов (#510); TASK-004 — 3 попытки (timeout 30 мин, REVIEW_FAILED по реальному багу). Ни одна из остановок не потребовала waiver.
- Операторские ошибки сессии: `AUTHORIZED_APPROVER_ACCOUNTS` не выставлен (лишний candidate #492); пайп проглотил rc pytest перед пушем (дважды); расовая ветка (b) BEH-26 в моём фикс-коммите (поймана CI и полным прогоном).
