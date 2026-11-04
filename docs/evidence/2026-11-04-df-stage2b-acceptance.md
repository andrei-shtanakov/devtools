# Приёмка этапа 2b df-installer: мерж stop живьём — 2026-11-04

Task 12 плана `docs/superpowers/plans/2026-11-03-df-installer-stage2b.md`; спека B rev 10
`docs/superpowers/specs/2026-10-13-df-installer-design.md` §4.5 «Приёмка этапа 2», §5.4
(вид stop, состояние `merge`, «Граница», «Предел второго рубежа»). Workflow —
`.github/workflows/df-stage2-live.yml`; сценарий — `tests/df/live_stand.py` (строки 10–12 —
Task 11, #687); прокси — `tests/df/gate_proxy.py` (режимы `hold`, `drop_response`). Root-строки
`df-precheck` без GitHub — CI `df-stage1`, job `stage2a` (Task 10, #686,
`tests/df/test_acceptance_root_roles.py`).

**Источник.** Всё ниже взято из артефактов одного прогона —
[41784019134](https://github.com/andrei-shtanakov/devtools/actions/runs/41784019134)
(`df-stage2-live-macos-15`, `df-stage2-live-ubuntu-24.04`), запущенного владельцем на `master`
после мержа #689. Факт не из артефактов помечен **(не из артефактов)** с источником. Секретов,
значений токенов, содержимого ключа и `response_nonce` в документе нет: токен machine user
назван только по типу.

**Итог.** Обе ячейки зелёные: строки стенда 1–12 — `pass` на macOS arm64 и Ubuntu x86_64;
происхождение проверено в обеих фазах. Мерж вида **stop** исполнен издателем через настоящую
предпроверку `df-precheck` живьём: смена head при удержанном `PUT` — проверенный отказ
`head_changed` (строка 11); оборванный ответ `PUT` — `done` через сверку, `merged_by` — бот App
(строка 12); в обеих строках `PUT` выше ровно один. Candidate и finalize в 2b **не
реализованы** (этап 3), бриф — отказ до части C (раздел 6). Находок против спеки B rev 10 нет.
Предыдущий прогон 41773122578 упал на дефекте стенда (раздел 9).

## 1. Прогон

| | |
|---|---|
| id прогона (`workflow_dispatch`, `master`) | [41784019134](https://github.com/andrei-shtanakov/devtools/actions/runs/41784019134), попытка 1, запущен 2026-11-04 06:12:41Z, итог `success` |
| голова `master` (`github.sha`) | `4ab390d8f8b40b89421f11506bbd7ad765e9aa96` (мерж #689) |
| одобрение environment `df-stage2-live` | `andrei-shtanakov`, `approved` (API approvals прогона, **не из артефактов**); ячейки стартовали 06:13:00Z |
| ячейка macOS arm64 (`macos-15`) | [job 126421875255](https://github.com/andrei-shtanakov/devtools/actions/runs/41784019134/job/126421875255), `success`, 06:13:00Z–06:16:41Z |
| ячейка Ubuntu x86_64 (`ubuntu-24.04`) | [job 126421874999](https://github.com/andrei-shtanakov/devtools/actions/runs/41784019134/job/126421874999), `success`, 06:13:00Z–06:16:35Z |
| метка прогона (`<run_id>-<attempt>-<ячейка>`) | `41784019134-1-macos`, `41784019134-1-linux` |

Времена job — API прогона (**не из артефактов**).

## 2. Происхождение проверяемой реализации

Механизм тот же, что в 2a (evidence 2a §2): фикстурный профиль `root_world` из одного члена
`devtools`, bare-репо из чекаута workflow, сверка до записи секретов (фаза `install`) и
повторно до их чтения (фаза `stand`). В 2b `FIXTURE_FROM_CHECKOUT` дополнен деревом Task 5:
`df`, 12 модулей AST-замыкания `governance/act_precheck.py` и
`contracts/approval-branches/v1/patterns.env`.

| Проверка | macOS | Ubuntu |
|---|---|---|
| `source_sha` = `github.sha` = `HEAD` чекаута | `4ab390d8f8b4…` | `4ab390d8f8b4…` |
| `fixture_sha` (коммит фикстурного репо) | `d41f0e6367f97c52aff2822b2590bd523762a084` | `d41f0e6367f97c52aff2822b2590bd523762a084` |
| пары «путь, blob» всех путей `FIXTURE_FROM_CHECKOUT` равны | да, 65 пар | да, 65 пар (те же) |
| установленный релиз | `d41f0e6367f9-macos-arm64-1` | `d41f0e6367f9-linux-x86_64-1` |
| `admin_digest` административного дерева | `sha256:5a05e43b535214b1b55fa4cc5d52a6ee26bd15aa70c97f677b2c2367ad6404d2` | тот же |

`admin_digest` из `provenance.json` совпадает с `precheck.admin_digest` в evidence каждого
ответа `merge` строк 11–12: вердикт выдало то же административное дерево, что сверено до
секретов. Источник: `live/<os>/provenance.json`, `live/<os>/summary.json`.

## 3. Стенд и фикстуры

- Стенд по API: `andrei-shtanakov/df-roles-probe-private` (id 1439254596, приватный), ветка по
  умолчанию `master`. **Стенд без ruleset** (R15-1); squash-мерж разрешён (H7).
- Machine user — `betta-tester`; тип токена: **classic PAT (ghp_)** (H5; значение не
  записывалось).
- App — бот `df-probe-reviewer-ashtanakov[bot]`, установка 170589886 (ключ — H6). `self-check`
  в обеих ячейках: проблем нет, ровно одна установка на репо 1439254596, права
  `contents: write`, `pull_requests: write`, `metadata: read`.
- Записи прогона (фикстура): `run-20261104T061329Z-094543b73fdef8ef` (macOS),
  `run-20261104T061339Z-1be205c95e8d9505` (Ubuntu). `products.toml` — фикстура из переменных
  стенда.
- **Засев** (строки 10 и 12) — технический PR machine user с манифестом `review/v1`, который
  machine user сам мержит squash-ом. Это **не** приёмка candidate и не акт человека; он
  нужен, чтобы `load_history` проверяющего видел `DeliveredRequest` g1, на который ссылается
  stop-PR.
- Строки 4б, 11, 12: `api_base` издателя на время строки — **нештатный API, прокси к
  api.github.com** (`gate_proxy` на `127.0.0.1`, root-ключ `publisher.toml`), после строки
  возвращается к штатному.

## 4. Строки 1–9 (повтор 2a на дереве 2b)

Механизмы и контроли строк — как в evidence 2a §4. Здесь только числа прогона.

| № | Строка | macOS | Ubuntu |
|---|---|---|---|
| 1 | PR `brief/…`, `stand/…` от machine user | #27 (голова `f9401e7`), #28 (`dc5bbaa`): открыты, автор `betta-tester`, репо 1439254596, база `master` | #29 (`42831b6`), #30 (`bd2ecf4`): так же |
| 2 | `publish-review` и идентичный повтор | `done`, `review_id` 5764066474; повтор — тот же id, `replayed`; ревью с маркером одно | `done`, 5764067267; повтор `replayed`; одно |
| 3 | удалён `results/<key>.json`, повтор | `done`, 5764066474, result записан заново, ревью одно | `done`, 5764067267, так же |
| 4а | прежний `head_sha` после пуша | `f9401e7`→`ee1797a`; код 2, `refused: pr_state` | `42831b6`→`b5182fc`; код 2, `refused: pr_state` |
| 4б | окно чтение→публикация | X `ee1797a`, Y `d5a06fa`; прокси удержал `POST …/pulls/27/reviews` (не истёк), голова стала Y до отпуска; `done`, `commit_id` X, 5764068464; ревью с маркером на X — [5764068464], на Y — нет | X `b5182fc`, Y `52ce2c9`; `done`, X, 5764069206; на Y — нет |
| 5а | `read-token`, clone | репо [1439254596]; clone — код 0 | то же |
| 5б | запись токеном чтения | push — код 128, «Write access to repository not granted … 403», ветки нет; `POST …/reviews`, `PATCH`, `PUT …/merge` — 403 «Resource not accessible by integration»; контроли: push из того же клона с заголовком machine user — код 0; PATCH machine user прошёл; PR #31 `mergeable: false`, `PUT` полным токеном — **405** | то же; конфликтующий PR #32, `PUT` полным токеном — 405 |
| 6 | токен во время чтения | снимков `ps` 32, в окне чтения (2,192 с) 13; ошибок `ps` нет; токен — ни в снимках, ни в файлах издателя, ни в `.git/config`; `/var/log/sudo-io` — каталога нет; в окне видны `sudo … df-publish read-token`, `git … clone`, `git-remote-https`; контроли: `request_id` в журнале, remote в `.git/config` | снимков 48, в окне (2,249 с) 20; остальное так же |
| 7 | `df-exec` → `publish-review`; `df-review` → `merge` | оба — sudo код 1, «нет правила (запрос пароля под -n)» | так же; командная форма судится: контроль 0, запрос 1 |
| 8 | `merge` от `df-exec`: `brief/…`; `stand/…` | код 2, `refused: brief_precheck_unavailable`; код 2, `refused: ambiguous_kind`; отправитель uid 402 | так же, uid 120 |
| 9 | уборка | закрыто 7 PR, удалено 10 веток, осталось 0, проблем 0 | закрыто 7, удалено 10, осталось 0 |

Строка 9 в 2b закрывает и PR строк 10–12 (засевы, stop-PR). Коммиты засевов и мерж строки 12
остаются в истории `master` стенда: `kept_on_default_branch` в `row9-cleanup.json` (раздел 10).

## 5. Строки мержа 10–12

Зависимости: 10←1, 11←10, 12←1 (свой засев `stand-<tag>-b`). Неудачный контроль —
«инфраструктура», не PASS.

### 5.1. Строка 10 — засев и stop-PR

| | macOS | Ubuntu |
|---|---|---|
| ws | `stand-41784019134-1-macos` | `stand-41784019134-1-linux` |
| засев: PR, голова, merge commit (squash, machine user) | #33, `0b2ac47`, `5b46ca32c955` | #34, `57276fe`, `f38994113a72` |
| контроль: `load_history` проверяющего | `DeliveredRequest n1 g1` | `DeliveredRequest n1 g1` |
| stop-PR: номер, ветка | #35, `stop/stand-41784019134-1-macos-n1-g1` | #36, `stop/stand-41784019134-1-linux-n1-g1` |
| путь (ровно один добавленный файл) | `workstreams/stand-41784019134-1-macos/approvals/stops/n1-g1.yaml` | `workstreams/stand-41784019134-1-linux/approvals/stops/n1-g1.yaml` |
| контроль: открыт, база, голова | `open`, `master`, `6ad94da` | `open`, `master`, `10002a5` |

### 5.2. Строка 11 — смена head при удержанном `PUT`

Прокси в режиме `hold("PUT", …/pulls/<n>/merge)`. Пока `PUT` удержан, machine user пушит второй
коммит в stop-ветку; после того как голова PR стала новой, прокси отпускает `PUT`.

| | macOS | Ubuntu |
|---|---|---|
| голова при запросе → после пуша | `6ad94da` → `047b885` | `10002a5` → `7a87d00` |
| контроль: голова новая до отпуска | да | да |
| журнал прокси (20 записей) | 5 выпусков токена установки, 1 `compare` базы `f389941`…`6ad94da`, `POST …/pulls/35/reviews` (аттестация), чтение ревью, финальное чтение PR и `ref/heads/master`; `PUT …/pulls/35/merge` — **удержан**, не истёк, ответ GitHub **409** | то же для PR #36; `PUT` удержан, **409** |
| ответ издателя | код 2, `refused: head_changed`; аттестация 5764078368 | код 2, `refused: head_changed`; аттестация 5764078841 |
| идентичный повтор (через прокси) | код 2, `refused: head_changed`, `replayed: true` | так же |
| `PUT` выше за оба запроса | **1** | **1** |
| PR на GitHub | `open`, не смёржен | `open`, не смёржен |
| аттестаций бота на прежней голове | 1 (5764078368) | 1 (5764078841) |

Предпроверка в evidence ответа: `version` `df-precheck/1`, `admin_digest` раздела 2,
`revoke: ok`, `base_tip` `f38994113a72` (обе ячейки), `trust_root` — `unavailable: no_binding`
(этап 3). Аттестация на прежней голове пережила проверенный отказ — это поведение, названное в
«Граница» §5.4: она аттестует форму PR на той голове, не его мерж.

### 5.3. Строка 12 — оборванный ответ `PUT` и идентичный повтор

Новый засев и stop-PR (`ws = stand-<tag>-b`). Прокси в режиме `drop_response("PUT", …/merge)`:
пересылает `PUT` выше, дожидается ответа GitHub и обрывает соединение вниз. Повтор
идентичного запроса — `settle_merge` (не больше `RECONCILE_TRIES` = 4, пока ответ
`unknown: merge_not_confirmed`).

| | macOS | Ubuntu |
|---|---|---|
| засев: PR, голова, merge commit | #37, `7283fbe`, `1245da0cdd15` | #38, `34cbe96`, `1e21a1438283` |
| stop-PR, голова | #39 (`stop/stand-41784019134-1-macos-b-n1-g1`), `2195d02` | #40 (`stop/stand-41784019134-1-linux-b-n1-g1`), `9c10e0d` |
| журнал прокси | 21 запись; `PUT …/pulls/39/merge` — выше исполнен (**200**), ответ вниз **не доставлен**; затем чтение PR | 28 записей; два `compare` (см. ниже); `PUT …/pulls/40/merge` — **200** выше, вниз не доставлен; чтение PR |
| ответов `unknown` до сверки | 0 (сверка сошлась с первого запроса) | 0 |
| ответ издателя | код 0, `done`, `merge_commit` `4d904449e896`, `head_sha` `2195d02`, аттестация 5764083482; `premise: app_merges_only_through_this_publisher`; не `replayed` | код 0, `done`, `merge_commit` `aff5eb9384cb`, `head_sha` `9c10e0d`, аттестация 5764083914; `premise` та же |
| идентичный повтор | `done`, тот же `merge_commit`, `replayed: true` | так же |
| `PUT` выше за все запросы | **1** | **1** |
| PR на GitHub | `closed`, `merged: true`, `merged_by` = `df-probe-reviewer-ashtanakov[bot]` (Bot), голова `2195d02`, `merge_commit_sha` = ответ | `closed`, `merged`, `merged_by` — тот же бот, голова `9c10e0d`, `merge_commit_sha` = ответ |

`premise` в evidence — потому что `done` установлен сверкой, а не собственным ответом 200 на
`PUT` (предпосылка гарантии §5.4: App мержит только через этот издатель).

**Сдвиг вершины базы живьём (Ubuntu).** Ячейки делят `master` стенда. Между первой
предпроверкой строки 12 Ubuntu и её финальным чтением ячейка macOS смёржила свой stop-PR #39,
и вершина базы сдвинулась `1e21a1438283` → `4d904449e896`. Издатель прошёл фазу
`rechecked.1`: повторный `compare` `4d904449…9c10e0d` и повторная предпроверка; в evidence
ответа `precheck_initial.base_tip` = `1e21a1438283`, решающая `precheck.base_tip` =
`4d904449e896`. Решающей считается последняя (§5.4).

Источник: `live/<os>/row10-seed.json`, `row10-stop-pr.json`, `row11-head-change.json`,
`row12-seed.json`, `row12-dropped-answer.json`. Отзыв токенов (`summary.json`
`tokens_revoked`): в обеих ячейках токен чтения, полный токен установки и токены чтения обоих
ws — «отозван».

## 6. Виды мержа в 2b

| Вид | Где проверен | Итог |
|---|---|---|
| **stop** | живьём (строки 10–12, обе ОС) и root CI (Task 10: `test_precheck_stop_through_the_chain` — издатель → sudo → вход → `act_precheck`, `allow`; контроль — устаревшее поколение `refuse`) | pass |
| **candidate** | — | **не реализован в 2b** (этап 3): издатель отвечает `refused: kind_unavailable_until_stage3: candidate` |
| **finalize** | — | **не реализован в 2b** (этап 3): `refused: kind_unavailable_until_stage3: finalize` |
| **brief** | живьём (строка 8) | отказ до части C: `refused: brief_precheck_unavailable` |

Проверен только вид stop. Предпроверка candidate и finalize издателем (привязка, `EpochSource`,
контекст credentials производного токена, `bundle_dir`/DAG D16, идентичность мержера,
`review_id` из решения доставки) — пункт этапа 3 в `TODO.md`.

## 7. Итог по строкам §4.5

| Строка §4.5 | Где | Итог |
|---|---|---|
| строки 2a (отказы ОС, sudoers, `SUDO_UID`, отозванный релиз, `read-token`, ревью при смене head, повтор ревью, тот же `request_id`, сверка ревью) | root CI (Task 14 2a) и стенд, строки 1–9 | pass (стенд, обе ОС; root-строки 2a — job `stage2a`, зелёный в CI PR 2b, например #677, #681, #686; здесь не перепроверялось) |
| смена head: мерж — отказ (серверный `sha`) | стенд, строка 11 | pass (обе ОС): `PUT` с прежним `sha` — 409 GitHub, `refused: head_changed`, PR не смёржен |
| идентичный повтор: мерж | стенд, строки 11 и 12 | pass (обе ОС): прежний результат `replayed`, `PUT` выше один |
| неопределённый исход: сверка мержа | стенд, строка 12 | pass (обе ОС): оборванный ответ `PUT` → `done` сверкой, `merged_by` — бот App, голова = `head_sha` |
| `df-precheck` читает ключ App, файлы издателя, токен machine user | root CI (Task 10) | pass: отказ ОС, root-контроль читает |
| `df-precheck` вызывается не издателем или с аргументами | root CI (Task 10) | pass для `df-exec`, `df-review`, `df-publisher` с аргументом; **оператор — `not_verified`** (раздел 8) |
| вердикт не связан с запросом (head, nonce, digest), таймаут, битый вывод | офлайн (Task 7, `tests/df/test_publisher_precheck.py`) и root CI (Task 10: таймаут рубежа 3) | pass: офлайн — отказ на другой head, nonce, digest, прочие поля связи, битый и избыточный вывод, ненулевой код; root — `precheck_failed: timeout` |

## 8. Root-строки `df-precheck` (Task 10)

PR #686 (влит владельцем, `91ac40c`), CI `df-stage1` job `stage2a` на обеих ОС: прогон
[41711022785](https://github.com/andrei-shtanakov/devtools/actions/runs/41711022785) (`ce9293c`)
и последующие зелёные 41720227010 (`0399fa1`) и 41728142310 (`c3f8ed7`, голова PR). Junit —
43 случая на ОС, единственный не-pass — xfail оператора. Данные — из отчёта Task 10 и логов CI
(**не из артефактов** этого прогона).

| Строка | Ubuntu | macOS |
|---|---|---|
| чтение ключа App, `publisher/*`, `perimeter/*`, токена machine user | pass | pass |
| память издателя (`/proc/<pid>/mem`; lldb `task_for_pid`), root-контроль читает байты sentinel | pass | pass |
| `df-exec`, `df-review`, `df-publisher` с аргументом — отказ sudoers | pass | pass |
| оператор (`runner`) — отказ sudoers | **xfail `not_verified`** | **xfail `not_verified`** |
| `df-publisher` без аргументов проходит sudo | pass | pass |
| наследование fd без `CLOEXEC` и переменной | pass | pass |
| положительный stop через всю цепочку | pass | pass |
| вход не попадает в I/O-журнал sudo (+ контроль включённости) | pass | pass |
| зависание git, рубеж 1: `unset: deadline` | pass (151 с) | pass (152 с) |
| остановленный проверяющий, рубеж 3 и самоуборка (`test_precheck_stopped_reaped_by_next_call`) | pass: клиент 200,10 с; молодой вызов щадит; после 230 с убран | pass: 200,02 с; так же |

## 9. Известные пределы

- **Оператор — `not_verified`.** Образ hosted runner даёт `runner` правило `(ALL) NOPASSWD:
  ALL`, поэтому отказ sudoers оператору на нём не проверяется. Живой стенд тоже работает на
  hosted runner-ах — в 2b строка остаётся `not_verified`; проверить можно только на хосте без
  такого правила.
- **Предел второго рубежа** (спека §5.4, абзац «Предел второго рубежа», решение владельца
  03.11, вариант (в)). `command_timeout=180` настроен (захват `sudo -l`), но ни одна строка не
  показывает, что он срабатывает. Остановленную команду он не завершает: sudo
  приостанавливается вместе с ней (обе ОС, прогон 41673160363); git-потомков в своей сессии
  тоже. Гарантия издателя — рубеж 3 (`precheck_failed: timeout` через `PRECHECK_WAIT` = 200 с,
  без сигналов). Остатки роли старше `REAP_AFTER` = 230 с убирает следующий `precheck serve`
  (`SIGKILL`); `doctor` показывает их в `roles.precheck`. Остановленный root `sudo` и зомби под
  ним остаются до ручного `kill` или перезапуска хоста.
- **Остаточное окно вершины базы.** `PUT` закрепляет только `sha` головы; закрепить базу GitHub
  не даёт. Сдвиг вершины базы между финальным чтением (`armed`) и `PUT` неустраним: мерж может
  лечь на вершину, которую предпроверка не видела. Это ограничивают серверный `sha` и проверка
  фактов доставки по спеке A («Граница» §5.4; ревью Task 8, S2). Сдвиг **до** финального
  чтения ловится повторной предпроверкой — показано живьём (раздел 5.3, Ubuntu). Смена базовой
  ветки в том же окне ловится после мержа: `_merged_by_app` сверяет `base.ref`, мерж в другую
  базу — `unknown` (Task 8, M1).
- **Аттестация переживает проверенный отказ** («Граница» §5.4): после строки 11 на прежней
  голове остаётся ревью бота; оно аттестует форму PR на той голове, не мерж.
- **Предпосылка гарантии** (§5.4): `done` через сверку засчитывается, только если App мержит
  обслуживаемые репо исключительно через один издатель. Evidence такого `done` несёт
  `premise` (строка 12).
- **Стенд без ruleset** (R15-1): «ruleset на тарифе» — репо доверия, этап 3.
- **Корень доверия не привязан:** `trust_root` — `unavailable: no_binding` во всех ответах
  `merge`; production `EpochSource` — этап 3.
- Отключение питания для SQLite периметра — вне v1 (как в 2a).

## 10. Прогоны до зелёного

**41773122578, попытка 1** (`91ac40c`, мерж #686; 04:25:31Z–04:29:11Z, **не из артефактов** —
API прогона): обе ячейки `failure`, код 2. Строки 1–9 — `pass` в обеих ячейках; строки 10 и 12 —
**«инфраструктура»**: `ModuleNotFoundError: No module named 'steward'`; строка 11 — «не
выполнялась (зависит от 10)». Причина — дефект стенда, не продукта: контроль засева
(`load_history` → `act_forge_http` → `steward.approvalfacts.v3`) работает в процессе стенда, а
`.venv` раннера собирался `uv sync --frozen` без группы `governance`. Исправлено в PR #689
(`uv sync --group governance`, проверка импортов стенда до записи секретов, регресс-тесты).
Артефакты: метки `41773122578-1-macos`, `41773122578-1-linux`.

**41773122578, попытка 2** — случайный перезапуск на том же старом коммите `91ac40c`
(05:47:12Z–05:50:03Z, **не из артефактов**); упал тем же образом.

След на стенде (сверено через API контроллером, **не из артефактов**): засевы обеих попыток
смёржены machine user в `master` стенда — попытка 1: #13–#16, попытка 2: #22, #24, #25, #26.
Засев мержится до контроля `load_history`, поэтому это штатный след плана (уборка закрывает
PR и удаляет ветки, коммиты засева остаются). Прочие PR обеих попыток закрыты без мержа.

## 11. Инцидент с ключом App

При выполнении H6 ключ App сперва был сохранён как **переменная** environment
`df-stage2-live` (открытый текст), а не как секрет. Владелец перенёс его в Secrets
2026-11-04 04:23Z и удалил переменную. Значение в этот документ не попадало. Открытый ключ
отозван владельцем в составе H8 (раздел 12).

## 12. Стенд после прогона; H8

Проверено контроллером после прогона через API (**не из артефактов**): открытых PR — 0; веток —
только `master`. История `master` стенда: исходный коммит `44bc505`, восемь засевов прогона
41773122578 (раздел 10), засевы этого прогона `5b46ca32c955` (#33), `f38994113a72` (#34),
`1245da0cdd15` (#37), `1e21a1438283` (#38) и мержи строки 12 `4d904449e896` (#39),
`aff5eb9384cb` (#40) — оба `merged_by` бот App. Stop-PR #35, #36 строки 11 закрыты без мержа.

**H8 выполнен владельцем 04.11** (**не из артефактов**: сообщение владельца; сверка через API):
PAT `betta-tester` и ключи App 5285716 отозваны, включая ключ, побывавший в переменной
(раздел 11); секреты `DF_STAND_APP_KEY`, `DF_STAND_MACHINE_TOKEN` удалены — у environment
`df-stage2-live` 0 секретов, остались только переменные `DF_STAND_APP_ID`,
`DF_STAND_INSTALLATION_ID`, `DF_STAND_REPO_ID`. Пункт `@id:df-stage2b-stand-secrets-revoke` в
`TODO.md` закрыт. Для следующего живого прогона стенд заводится заново (H5–H7).

## 13. После прогона: финальное ревью ветки

Правки финального ревью ветки 2b — PR #688 (`fix(df): stage 2b final-review follow-ups`), влит
владельцем 2026-11-04 06:21Z, merge-коммит `5da55b5` (**не из артефактов**: API PR). Он влит
**после** прогона 41784019134 (06:12Z–06:16Z) и меняет `df/` (`doctor` — проба
`publisher.precheck`, потолок `ps` в уборке, одна копия strict-JSON): `df/doctor.py`,
`df/precheck_reap.py`, `df/publisher/precheck.py`, `df/publisher/request.py`,
`df/stage2_config.py`. Поэтому прогон доказывает реализацию на `4ab390d`, а не на `5da55b5`.
`df/publisher/merge.py` #688 не трогает, но клиент предпроверки (`precheck.py`) и разбор запроса
(`request.py`) — на пути мержа. Правки #688 проверены CI PR (`df-stage1`: stage1 и `stage2a` на
обеих ОС, `ci`, governance — зелёные); живой стенд на них не перезапускался.
