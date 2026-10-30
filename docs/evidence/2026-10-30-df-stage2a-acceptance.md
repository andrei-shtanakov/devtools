# Приёмка этапа 2a df-installer: живой стенд — 2026-10-30

Task 15 плана `docs/superpowers/plans/2026-10-26-df-installer-stage2a.md`; спека B rev 9
`docs/superpowers/specs/2026-10-13-df-installer-design.md` §4.5 «Приёмка этапа 2»; решения
R15-1…R15-6. Workflow — `.github/workflows/df-stage2-live.yml`; сценарий —
`tests/df/live_stand.py`; окно чтения→публикации — `tests/df/gate_proxy.py`. Root-строки §4.5
без GitHub — CI `df-stage1`, job `stage2a` (Task 14, `tests/df/test_acceptance_root_roles.py`).

**Источник.** Всё ниже взято из артефактов одного прогона — [41044119428](https://github.com/andrei-shtanakov/devtools/actions/runs/41044119428)
(`df-stage2-live-macos-15`, `df-stage2-live-ubuntu-24.04`), запущенного владельцем на `master`
после мержа #656 (R15-3: environment допускает только `master`). Факт не из артефактов помечен
**(не из артефактов)** с источником. Секретов, значений токенов и содержимого ключа в документе
нет: токен machine user назван только по типу.

**Итог.** Обе ячейки зелёные: все строки стенда (1–9) — `pass` на macOS arm64 и Ubuntu x86_64;
происхождение проверено в обеих фазах; находок против спеки B rev 9 нет. Три строки мержа §4.5
— этап 2b.

## 1. Прогон

| | |
|---|---|
| id прогона (`workflow_dispatch`, `master`) | [41044119428](https://github.com/andrei-shtanakov/devtools/actions/runs/41044119428), запущен 2026-10-30 10:54Z, итог `success` |
| голова `master` (`github.sha`) | `9da14cff73a8e2558d1f7f1b19599d50b5456bd7` (мерж #656) |
| одобрение environment `df-stage2-live` | `andrei-shtanakov`, `approved` (API approvals прогона, **не из артефактов**); ячейки стартовали 11:27Z |
| ячейка macOS arm64 (`macos-15`) | [job 124023850959](https://github.com/andrei-shtanakov/devtools/actions/runs/41044119428/job/124023850959), `success`, 11:27:48Z–11:29:56Z |
| ячейка Ubuntu x86_64 (`ubuntu-24.04`) | [job 124023850520](https://github.com/andrei-shtanakov/devtools/actions/runs/41044119428/job/124023850520), `success`, 11:27:47Z–11:29:46Z |
| метка прогона (`<run_id>-<attempt>-<ячейка>`) | `41044119428-1-macos`, `41044119428-1-linux` |

## 2. Происхождение проверяемой реализации (r3 N2)

Установка — фикстурный профиль `root_world` из одного члена `devtools`: bare-репо, собранное
из чекаута workflow (`FIXTURE_FROM_CHECKOUT = ("df",
"contracts/approval-branches/v1/patterns.env")`), один committed `bootstrap init` в `/`.
Две идентичности не приравниваются друг к другу; сверка — до записи секретов в job (фаза
`install`) и ещё раз до их чтения (фаза `stand`); расхождение — «инфраструктура».

| Проверка | macOS | Ubuntu |
|---|---|---|
| `source_sha` = `github.sha` = `HEAD` чекаута | `9da14cff73a8…` | `9da14cff73a8…` |
| `fixture_sha` (коммит фикстурного репо) | `7fc5405e3edb1910b52591cb19c864897b65f652` | `7fc5405e3edb1910b52591cb19c864897b65f652` |
| пары «путь, blob» всех путей `FIXTURE_FROM_CHECKOUT` равны | да, 50 пар | да, 50 пар |
| `release.json`: член `devtools` с `sha = fixture_sha` | да; релиз `7fc5405e3edb-macos-arm64-1` | да; релиз `7fc5405e3edb-linux-x86_64-1` |
| `admin_digest` записи `releases.toml` = `admin.expected_admin_digest(<чекаут>/df)` | `sha256:3953edc83cd5047bcc5ae331a289284256f84b340d8172ac8963bf355ab76acd` | тот же |

`fixture_sha` один на обе ячейки (фикстурный коммит с фиксированными автором и датой из тех
же байтов), `source_sha ≠ fixture_sha` — идентичности не приравнивались. `admin_digest`
совпадает между ОС: административный код — те же байты `df/`. Сверка прошла в фазе `install`
(до записи секретов в job) и повторно в фазе `stand` до их чтения.

Источник: `live/<os>/provenance.json` артефакта. Офлайн-контроль механизма —
`tests/df/test_live_stand.py::test_live_provenance` (изменённый файл `df/`, другой
`source_sha`, подменённый `fixture_sha`, пропавший путь, чужой digest → отказ до чтения
секретов).

## 3. Стенд и фикстуры

- Репо стенда — `DF_STAND_REPO_ID` (приватный; имя и ветка по умолчанию читаются из API
  `GET /repositories/{id}`, R15-4). **Стенд без ruleset (личный приватный репо на Free),
  контроль 405 — конфликт** (R15-1).
- Стенд по API: `andrei-shtanakov/df-roles-probe-private` (id 1439254596, приватный), ветка по
  умолчанию `master`.
- Machine user — `betta-tester`; тип токена: **classic PAT (ghp_)** (R15-2; значение не
  записывалось).
- App — бот `df-probe-reviewer-ashtanakov[bot]`, установка 170589886. `self-check` перед
  строками в обеих ячейках: проблем нет, ровно одна установка — 170589886 на репо 1439254596,
  права `contents: write`, `pull_requests: write`, `metadata: read` (лишней установки нет,
  шаг H2).
- Записи прогона (фикстура): `run-20261030T112820Z-3fe7ac5ba3fcf2e3` (macOS),
  `run-20261030T112825Z-644906137c6724bb` (Ubuntu).
- **Фикстура:** `products.toml` (продукт стенда из переменных environment, профиль `write`);
  запись прогона в `/var/lib/df/runs/` (как Task 14: штатный `run start` в v1 останавливается
  на пробах).
- Строка 4(б): `api_base` издателя на время строки — **нештатный API, прокси к
  api.github.com** (`tests/df/gate_proxy.py` на `127.0.0.1`, root-ключ `publisher.toml`),
  после строки возвращается к штатному.

## 4. Строки живого стенда

У каждой строки — механизм отказа и контроль выполнимости (r1 N12). Неудачный контроль —
«инфраструктура», не PASS.

| № | Строка | Механизм | Контроль выполнимости | macOS | Ubuntu |
|---|---|---|---|---|---|
| 1 | machine user (токен `df-exec`) открывает PR `brief/stand-<метка>` и `stand/<метка>` | — (подготовка) | оба PR открыты, автор `machine_user`, из стенда, на ветку по умолчанию | PR #1 `brief/…` (голова `41606a8`), #2 `stand/…` (`f93a9b1`): открыты, автор `betta-tester`, из репо 1439254596, база `master` | PR #3 `brief/…` (`0f5b6dd`), #4 `stand/…` (`1e2acd2`): так же |
| 2 | `df-review` → `publish-review` `comment`; идентичный повтор | сохранённый результат: тот же `review_id`, второго ревью нет | одно ревью бота App с маркером на `head_sha` | `done`, `review_id` 5711572428 на `41606a8`; повтор — `done`, тот же id, `replayed`; ревью с маркером: ровно одно | `done`, 5711572913 на `0f5b6dd`; повтор — тот же id, `replayed`; одно ревью |
| 3 | root удаляет `results/<key>.json`, повтор | сверка с GitHub по маркеру: `done`, тот же `review_id`, второго POST нет | intent и result были до удаления | intent и result были; после удаления result повтор — `done`, 5711572428, result записан заново; ревью с маркером одно | так же, 5711572913 |
| 4а | запрос с прежним `head_sha` после пуша | `refused: pr_state` | строка 2 (предпосылка): запрос с текущей головой — `done` | пуш `41606a8`→`db7f016`; запрос с `41606a8` — код 2, `refused: pr_state` | `0f5b6dd`→`3a0f717`; код 2, `refused: pr_state` |
| 4б | окно чтение→публикация через настоящий издатель | ответ издателя `commit_id = X`; в GitHub ревью бота с маркером запроса — на X, ревью бота на Y нет | POST удержан прокси; голова PR стала Y до отпуска | X = `db7f016`, Y = `b928d92`; прокси удержал `POST …/reviews` (единственный удержанный, не истёк), голова стала Y до отпуска; ответ `done`, `commit_id` X, `review_id` 5711575655; в GitHub ревью с маркером на X — [5711575655], на Y — нет | X = `3a0f717`, Y = `f23fe42`; удержание то же; `done`, `commit_id` X, 5711575692; на X — [5711575692], на Y — нет |
| 5а | `read-token`: clone приватного стенда от `df-review` | — (успех: репо и права, которые задал издатель) | — | `done`: репо [1439254596], права `contents: read`, `pull_requests: read` (+ `metadata: read`), срок 12:29Z; clone — код 0 | то же; clone — код 0 |
| 5б | push, `POST …/reviews`, `PATCH …/pulls/{n}`, `PUT …/merge` токеном чтения | отказ GitHub по правам: `403` «Resource not accessible by integration»; push — `403` GitHub в выводе git (не локальный «Permission denied»), ветки нет | push из того же клона от `df-review` с заголовком machine user проходит; PATCH токеном machine user проходит; ревью — строка 2; `PUT …/merge` полным токеном установки на тот же конфликтующий PR (`mergeable == false` проверен до PUT) — `405` | push — код 128, «Write access to repository not granted … returned error: 403», ветки нет; `POST …/reviews`, `PATCH …/pulls/1`, `PUT …/pulls/6/merge` — 403 «Resource not accessible by integration». Контроли: push из того же клона с заголовком machine user — код 0, ветка есть; PATCH machine user — прошёл; PR #6 `mergeable: false`, PUT полным токеном — **405** «Pull Request has merge conflicts» | то же на PR #3 и конфликтующем PR #5: push 403 (код 128, ветки нет), три вызова REST — 403; контроли: push 0, PATCH прошёл, PR #5 `mergeable: false`, PUT полным токеном — 405 |
| 6 | токен во время чтения | нет в снимках `ps` (каждые 100 мс), файлах `/var/lib/df/publisher/**`, I/O-журнале sudo, `.git/config` клона | процесс-маркер **от `df-review`** виден в `ps`; в окне чтения есть строка `df-publish` или `git … clone`; `ps` не падал и хотя бы один снимок — строго внутри окна чтения (от `read-token` до конца clone); `request_id` в журнале; remote в `.git/config` | снимков `ps` 36, в окне чтения (2,894 с) 18; ошибок `ps` нет; токен — ни в одном снимке, ни в файлах издателя, ни в `.git/config`; `/var/log/sudo-io` — каталога нет; в окне видны `sudo … df-publish read-token`, `git … clone`, `git-remote-https`; контроли: `request_id` в журнале, remote в `.git/config` | снимков 48, в окне (2,241 с) 20; остальное так же |
| 7 | `df-exec` → `publish-review`; `df-review` → `merge` | отказ sudo, издатель не запускался | строки 2 и 8 проходят sudo | оба — sudo код 1, «a password is required», признано «нет правила» (R14-10a): listing даёт контроль `perimeter-status` и не даёт `publish-review`/`merge`; командная форма на macOS не судится (sudo 1.9.13); журнал издателя без новой строки | так же; командная форма судится: контроль — 0, запрос — 1 |
| 8 | `merge` от `df-exec`: PR `brief/…`; PR `stand/…` | `refused: precheck_unavailable`; `refused: ambiguous_kind` | ответ от `df-exec` после проверки отправителя | `brief/…` — код 2, `refused: precheck_unavailable`; `stand/…` — код 2, `refused: ambiguous_kind`; отправитель — uid `df-exec` (402) | так же, uid `df-exec` 120 |
| 9 | уборка от machine user | PR закрыты, ветки удалены; провал — провал прогона | состояние перечитано из API | закрыто 3 PR (#1, #2, #6), удалено 6 веток, осталось 0, проблем 0 | закрыто 3 PR (#3, #4, #5), удалено 6 веток, осталось 0 |

Контрольный push строки 5б делается из того же клона от `df-review` с заголовком machine user: на время этого одного процесса git PAT `betta-tester` лежит в окружении процесса `df-review` на одноразовом раннере (не в argv, не в файлах; его значение и форма заголовка — в охране evidence).

Строка выполняется, если прошли строки, от которых она зависит (2←1, 3←2, 4а←1+2, 4б←4а,
5б←1+2+5а, 6←5а, 8←1, 7←1+2+8; контроль, который держит другая строка, — тоже зависимость); иначе — «не выполнялась (зависит от N)», это провал прогона, не
PASS. Уборка (9) — всегда.

Ответы издателя (без секретов), `review_id`, `ps`: число снимков, число внутри окна чтения и
строки `git`/`df-publish` из окна с токеном, заменённым на `<token>` (снимки целиком в evidence
не пишутся: они несут аргументы всех процессов раннера) — файлы `live/<os>/row*.json`,
`live/<os>/summary.json`; диагностика sudo — `sudo-diag/<os>/`. Отзыв токена чтения и полного
токена установки (`DELETE /installation/token`) — `summary.json` `tokens_revoked`: в обеих
ячейках оба «отозван». Прогон, прерванный до строки 9 (отмена, таймаут), убирает
шаг `wipe` (всегда): закрывает PR и удаляет ветки метки прогона, исход — в логе шага.

## 5. Итог по строкам §4.5

| Строка §4.5 | Где | Итог |
|---|---|---|
| `df-exec` читает ключ App, `~/.config/gh` человека, `~df-review` | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| `df-review` читает ключ App, токен machine user | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| `df-review` подменяет `libexec`, файлы релиза, `/etc/df` | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| `df-review` → `merge`; `df-exec` → `publish-review` (sudoers и издатель по отдельности) | root CI (Task 14); стенд, строка 7 | pass: Task 14 (оба рубежа); на стенде — отказ sudo, издатель не запускался (обе ОС) |
| лишние аргументы, `sudo -E` / сохранение окружения | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| подмена `SUDO_UID` | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| вызов отозванного релиза издателя | root CI (Task 14; отзыв — **фикстура**) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| `read-token`: чтение приватного продукта; запись, ревью, мерж этим токеном — отказ GitHub | стенд, строки 5а–6 | pass (обе ОС): clone прошёл; push, ревью, PATCH, мерж — 403; токена нет в `ps`, журналах, `.git/config` |
| смена head: ревью привязано к старому SHA | стенд, строки 4а–4б | pass (обе ОС): прежний head — `pr_state`; в окне ревью осталось на X |
| смена head: мерж — отказ (серверный `sha`) | **2b** | вне 2a |
| идентичный повтор: ревью | стенд, строка 2 | pass (обе ОС): тот же `review_id`, второго ревью нет |
| идентичный повтор: мерж | **2b** | вне 2a |
| идентичный повтор: `perimeter-status` вычисляется заново | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| тот же `request_id` с другим содержимым | root CI (Task 14) | pass (Task 14: job `stage2a` CI `df-stage1`, PR #654, обе ОС; здесь не перепроверялось) |
| неопределённый исход: сверка ревью | стенд, строка 3 | pass (обе ОС): `done` с тем же `review_id` без второго POST |
| неопределённый исход: сверка мержа после таймаута | **2b** | вне 2a |

Запись прогона и `products.toml` стенда — **фикстура** (раздел 3).

## 6. Что этот прогон не доказывает

- Строки мержа §4.5 (три строки «2b») — этап 2b, после части B.
- Ruleset и тариф репо продукта: стенд без ruleset; проверка «ruleset на тарифе» относится к
  репо доверия (этап 3).
- Отключение питания для SQLite периметра — вне v1.
- Находок прогона нет; решений не потребовалось.

## 7. Стенд после прогона

Проверено контроллером после прогона через API (**не из артефактов**): открытых PR — 0;
веток — только `master` на исходном коммите `44bc505`; шесть PR от `betta-tester` (#1–#6)
закрыты без мержа. Стенд без ruleset — контроль 405 дал конфликтующий PR (R15-1), мерж в
`master` стенда не выполнялся.
