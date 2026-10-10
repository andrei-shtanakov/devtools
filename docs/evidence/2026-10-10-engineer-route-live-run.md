# Живая приёмка E2 — engineer-маршрут стадии Need (§11.8)

Даты: 2026-10-09/10. Пункт плана: `TODO.md` `@id:spec-loop-need-engineer-route`.
Спека: `docs/superpowers/specs/2026-09-15-need-stage-design.md` §11 (ревизия 7),
критерий — §11.8. Код: #571, #572, #573, #574 (маршрут), #575 и #578 (список целей
приёмки), #579 (дефект адаптера, найденный этой приёмкой).

## Объявление прогона

| | |
|---|---|
| предмет | минимальный CLI с `--version` (учебный предмет, выбран владельцем) |
| цель | `spec-loop-sandbox` → `andrei-shtanakov/spec-loop-sandbox` (публичная песочница; из `contracts/acceptance-targets/v1/targets.toml`, не из манифеста флота) |
| стейкхолдер | `owner` = владелец `andrei-shtanakov` (в чате) |
| SHA devtools | `ecf31ce` (master, #579 влит) |
| SHA discovery | `94c88cc` (master) |
| discovery home | `~/.discovery` (умолчание, `DISCOVERY_HOME` не задан) |
| политика подписи | `andrei-shtanakov/approval-policy@12d198fb4db9ed6fbb1c5b00af308eeb31e4ec22` (`policy/approvers.env`) |

Почему песочница, а не `DarkFactory-polygon`: polygon приватный в бесплатной
организации, листинг rulesets отвечает 403, и стоп-кран
(`governance/halt_gate.py`) честно отказывает `refuse_unknown`. Владелец не стал
менять ни приватность polygon, ни стоп-кран, и выбрал отдельную публичную
песочницу (#578).

**Ответы в интервью — в роли, на учебном предмете.** Предмет выбрал владелец,
чтобы проверить цепочку, а не ради потребности продукта. Содержание каждого
ответа — владелец: агент готовил черновик, владелец подтверждал его в чате
(«подтверждаю всё, отправляй»), и только после этого шёл `discovery answer`.
Транскрипция в YAML и вызовы — оператор (fleet-агент devtools). Это реальный
стейкхолдер, отвечающий в роли, а не самоопрос агента. Но и не потребность
настоящего заказчика.

## Цепочка §11.1.1

### 1. customer `--brief-only` → `brief_ready`

| | |
|---|---|
| run-id | `cli-version-20261009-9c3274` (ws `cli-version-20261009`) |
| сессия | `s-a12676b66515`; `journal.jsonl` — 38 событий, SHA-256 `e71e598ed9156f7712499803e455e3a1f597c9e916865ea8760baecc9b60f621` |
| ответы | 19, гейт pass на каждом шаге, readiness ready |
| записи брифа | G-01…02, P-01…02, J-01, FR-01…03 (FR-01/02 Must, FR-03 Should), NFR-01…03, CON-01…02, M-01, OUT-01…02 |
| итог | `status: brief_ready`, `completed_at 2026-10-10T06:02:13Z`; ветки, worktree и авторинга нет (терминальный статус §11.2) |
| бриф | blob `58ed5bcfba79783b73e7e160c3a7535a6291fbfc`, SHA-256 `53a32a46d49d3d0fc49eee841a4912c4ffce1133189b91994dafa0a308ed07d0` |

### 2. brief-PR — `make brief-propose`

PR `andrei-shtanakov/spec-loop-sandbox#1`, ветка `brief/cli-version-20261009`,
голова `4475e191620dfb10818fe4df272cdb5cf4654a2a`. Ровно два добавленных файла в
`workstreams/cli-version-20261009/spec/00-discovery/`: `brief.md` (тот же blob
`58ed5bc…`) и `approval-request.yaml` (`brief_self_hash
sha256:fc416eb1…`, пин политики `12d198fb…`, run/ws id).

Первые попытки `brief-propose` не прошли, и это оказалось дефектами:

- **#579 (влит):** отсутствующий файл GraphQL отдаёт rc 1 с `NOT_FOUND`. Адаптер
  `repo_file_fact` считал это недоступностью факта и навсегда выдавал «повторите».
  Исправлено: ABSENT только тогда, когда все ошибки — `NOT_FOUND` на
  `repository.object.file` и значение там null. Тест построен на записанном живом
  ответе.
- **#580 (открыт):** в цели нет метки `human-merge-required`, и `gh pr create`
  падает. brief-propose отвечает «повторите» вместо названного отказа. Метку
  создал агент учёткой владельца, по прямому указанию владельца; после этого
  PR создан.

**Ревью brief-PR.** Терминальное ревью `review-pr.sh` по brief-PR не
запускалось. На PR есть автоматический комментарий
`copilot-pull-request-reviewer` (COMMENTED): он пишет, что `brief_self_hash` «не
совпадает при стандартном хешировании». Это неверное прочтение: self-hash
discovery считается по брифу без поля `approved_content_hash`, а не по сырым
байтам файла. Совпадение подтвердили `brief-approve` и engineer-preflight —
оба сверяют хеш вендоренным `contracts/discovery-approval/v1`. Отдельный шаг
«ревью» §11.8 живьём не исполнен.

### 3. Мерж человеком

PR #1 смержен **через веб-интерфейс GitHub**, а не командой
`make human-merge ARGS='spec-loop-sandbox 1'`: `mergedBy andrei-shtanakov`,
`2026-10-10T09:17:10Z`, merge-коммит `8c1a226744012325e87ea1c9f961a358a28a62c1`.
Владелец сообщил, что сделал это нечаянно. По §11.3 п.6 это допустимый путь
подписи: доказательство — факт форджа (мержер из политики, состав PR, база), а не
инструмент мержа. `brief-approve` перепроверил акт целиком.

**Следствие для приёмки:** ветка `brief/*` в `human-merge.sh` (проверка заявки по
head SHA перед мержем, T50/T50a) **живьём не исполнена**. Она покрыта тестами на
стенде форджа и записанных ответах `gh`. Ветка `brief/cli-version-20261009` на
фордже после веб-мержа не удалена.

### 4. `make brief-approve RUN=cli-version-20261009-9c3274 PR=1`

```
{"at":"2026-10-10T09:17:10Z","by":"andrei-shtanakov","head":"4475e19","merge":"8c1a226","state":"MERGED"}
подписано; следующий шаг:
  make spec-loop SUBJECT='минимальный CLI с --version' REPO=spec-loop-sandbox ARGS='--need --frame engineer --new-run --ws-id cli-version-20261009-eng --stakeholder <role> --traces-to …/cli-version-20261009-9c3274/brief-approval/customer-brief.md --approval-pr 1'
```

Подписанный бриф лежит в
`out/governance-runs/cli-version-20261009-9c3274/brief-approval/customer-brief.md`:
`status: approved`, `approver: andrei-shtanakov`,
`approved_at 2026-10-10T09:17:10Z`, `approved_content_hash
sha256:fc416eb13b977b1c89fcd2db19c2f612d81c05823c4af6d83e253dd840c17d8b`, blob
`8dc05439bb60080d4c379b8326cae6e20a1ac14e`.

### 5. engineer `--need` — до публикации брифа и E1

Команда — та, что напечатал `brief-approve`, с `--stakeholder owner`.

| | |
|---|---|
| run-id | `cli-version-20261009-eng-c67061` (ws `cli-version-20261009-eng`) |
| сессия | `s-cli-version-20261009-eng-c67061-e` (write-ahead id §11.4.5); `journal.jsonl` — 30 событий, SHA-256 `2dfffd05b78aa666adacb4b4ecce764e3c628be7c50a3b3f6bba8959eafcb693` |
| `interview.approval` | pr 1, merge `8c1a226`, approver `andrei-shtanakov`, `act_policy_sha 12d198fb…`, `self_hash sha256:fc416eb1…` |
| `upstream_blob` | `8dc05439bb60080d4c379b8326cae6e20a1ac14e` (= blob подписанного брифа) |
| ответы | 15, гейт pass, ready; записи S-01, IF-01, CON-01…02, AP-01…02, RK-01 |
| бриф | `traces_to: [upstream.md]`; blob `da91226f616058cd0ce820a1a8777afece527bdc` |
| ops | `interview-start`, `interview-brief`, `branch-1`, `materialize-brief-1` — все `completed` |

**E1 выполнен.** `run.json` `brief`:
`source_paths [00-discovery/brief.md, 00-discovery/upstream.md]`,
`requirements_source 00-discovery/upstream.md`,
`source_blobs.discovery-customer = 8dc05439…` (совпадает с `upstream_blob`, сверка
§11.4.4), `discovery-brief = da91226f…`. Source-слой материализован в цели как
`workstreams/cli-version-20261009-eng/spec/00-discovery/{brief.md,upstream.md}`:
`git hash-object` даёт те же `da91226f…` и `8dc05439…`.

**Авторинг S1 не начат.** Прогон остановился так:

```
_step_authoring: …/spec-loop-sandbox/profiles/team-exp.yaml не декларирует узел 'design' — доставьте обновлённый профиль в target PR-ом; profiles/ — authority-root, мерж человеком
spec-loop: прогон 'cli-version-20261009-eng-c67061' в статусе 'stopped_preflight' — кнопка его не продолжает; без скрытых ретраев.
```

В песочнице нет `profiles/team-exp.yaml`. По решению владельца (2026-10-10)
профиль ради авторинга не доставляется: публикация engineer-брифа, E1 и
материализация source-слоя достаточны. **Это уточнение критерия §11.8, а не
утверждение, что S1 пройден целиком.**

## Негативные контроли

### К1 — ручная подпись без self-hash → отказ preflight

Вход — копия подписанного брифа без единственной строки
`approved_content_hash: …` (diff — одна удалённая строка). Остальное как в
боевом engineer-вызове: `--approval-pr 1`, ws `cli-version-20261009-neg1`. Запуск
шёл до engineer-прогона, когда с этой парой (repo, subject) был только
customer-прогон в `brief_ready`.

```
spec-loop: прежний прогон cli-version-20261009-9c3274 остаётся (brief_ready), сессия discovery: s-a12676b66515
spec-loop: operator_brief: подпись не честная (migration)
```

Код ненулевой, список `out/governance-runs` до и после совпадает.

(Первая попытка К1 дала diff из двух изменений: удалённая строка плюс лишний
финальный перевод строки. Она переделана, чтобы менялась ровно одна строка, и в
зачёт не идёт.)

### К2 — смёрженный PR недопустимого состава → отказ preflight `pr_files`

**Замена, утверждённая владельцем 2026-10-10.** §11.8 называл контроль
«`--approval-pr` на бандл-PR». Живьём проверен **смёрженный PR недопустимого
состава**, а не настоящий бандл-PR: бандл-PR в песочнице нет, потому что авторинг
S1 не начат. Смёрженный бандл-PR в `read_act` дошёл бы до той же проверки
состава (`brief_provenance.files_dir`): PR обязан менять ровно бриф и заявку в
одном `…/00-discovery`. Несмёрженный отказал бы раньше, `pr_not_merged`.

PR `andrei-shtanakov/spec-loop-sandbox#2`: меняет только `README.md` (modified),
база `master`, голова `ec6c67e91694fd80572950b275efa94e0fc9fba2`. Смержил
владелец, `2026-10-10T10:29:02Z`, merge `cbd2e21092861292edbd0e0d704a9363deee813d`.
Агентская учётка `ai-prosto` в песочнице имеет только `pull` и создать или
смержить PR не может.

**Попытка 1 — до engineer-preflight не дошла, не засчитана.** Вызов совпадал с
боевым engineer-вызовом во всём, кроме `--approval-pr 2` и нового ws-id
`cli-version-20261010-neg2`:

```
spec-loop: --new-run запрещён: прогон(ы) с этими (repo, subject) уже достигли S1: --run-id cli-version-20261009-eng-c67061 [stopped_preflight]
```

Код 2. Engineer-прогон этой пары уже прошёл S1, и запрет повторного workstream
(`spec_loop.main`, раньше engineer-preflight) остановил вызов. Список
`out/governance-runs` и листинг `~/.discovery/sessions` до и после совпадают.

**Попытка 2 — засчитана владельцем.** Отличия от попытки 1 — только
`SUBJECT='минимальный CLI с --version (neg2)'` (вместо `'минимальный CLI с
--version'`) и ws-id `cli-version-20261010-neg2`. Подписанный upstream тот же
(`…/cli-version-20261009-9c3274/brief-approval/customer-brief.md`),
`--stakeholder owner`, `--approval-pr 2`:

```
spec-loop: цель andrei-shtanakov/spec-loop-sandbox — из списка целей приёмки
spec-loop: pr_files: изменения PR [('README.md', 'modified')] ≠ бриф + заявка в одном …/00-discovery
make: *** [spec-loop] Error 1
```

Код 2. Что наблюдалось:

- список `out/governance-runs` до и после совпадает, новых или изменённых файлов
  под ним нет (`find -newer` от метки, поставленной перед запуском);
- листинг `ls -laT ~/.discovery/sessions` до и после совпадает, файлов новее
  метки под `~/.discovery` нет;
- в чекауте песочницы после метки изменился только `.git` (от `git status`
  оператора).

Отсутствие изменений в `~/.discovery` — **наблюдение**. Само по себе оно не
доказывает, что процесс discovery не вызывался. Что discovery не вызывался до
отказа, следует из кода (ниже).

**Почему смена subject и ws-id не влияет на проверку (по коду `ecf31ce`).**

- `engineer_preflight(path, approval_pr, repo_slug, ops)`
  (`governance/spec_loop.py:399`) принимает только путь к подписанному брифу,
  номер PR, `repo_slug` и порт фактов. `subject` и `ws_id` в него не передаются.
  `repo_slug` берётся из `resolve_repo_entry(…, args.repo, …)`, то есть по
  `REPO`, а не по subject.
- Внутри: чтение файла → `check_customer_upstream` →
  `brief_provenance.read_act(ops, repo_slug, approval_pr)`. Порядок `read_act`:
  «смержен» → база = ветка по умолчанию → `files_dir` (`pr_files`) → заявка →
  координаты → хеш → политика и мержеры. Отказ `pr_files` идёт раньше любых
  проверок мержера и политики.
- В `main` subject и ws-id до preflight используются только для выбора прогона:
  `find_runs(repo, subject)`, запрет `--new-run` и поиск волнового или прежнего
  бандл-PR по имени ветки (`recover_wave_run_from_github`,
  `recover_run_from_github`). Ни один из этих шагов не вызывает discovery. Если
  бы что-то нашлось, вызов ушёл бы в продолжение прогона, а не в preflight.
  Текст `pr_files` появляется только из `engineer_preflight`, значит preflight
  достигнут.
- `run_id` генерируется (`spec_loop.py:1333`) и леджер создаётся только после
  успешного preflight. Вызовы discovery идут через порт раннера уже созданного
  прогона.

Леджер для контроля не трогали.

## Оговорки §11.8

- **Объём гарантии (Q5).** Гарантия происхождения approval — только для
  engineer-входа через `--need`. Engineer-вход через E1 `--brief` её не даёт
  (`TODO.md` `@id:e1-brief-engineer-provenance`).
- **Ограничение восстановления (discovery#63).** До ответа соседа потеря ответа
  успешного `start` делает engineer-прогон невосстановимым: продолжение — через
  `--new-run` с повтором интервью (§11.4.5). Принято владельцем 2026-10-08. В
  этой приёмке не проявилось.

## Дефекты, найденные приёмкой

| | статус |
|---|---|
| #579 — `repo_file_fact`: NOT_FOUND отсутствующего файла давал вечное «повторите» | влит |
| #580 — brief-propose без метки цели: «повторите» вместо названного отказа | открыт |
| #576 — `HaltedError` стоп-крана в spec-loop даёт трейсбек вместо отказа с ненулевым кодом без прогона и леджера | открыт |
| #577 — устаревший манифест: fallback на список целей с вводящей в заблуждение причиной (minor ревью #575) | открыт |

## Итог

| критерий §11.8 | результат |
|---|---|
| customer `--brief-only` → `brief_ready` | выполнено |
| brief-PR (`make brief-propose`) | выполнено (после #579 и создания метки) |
| ревью brief-PR | живьём не исполнено (только автоматический комментарий Copilot) |
| мерж человеком | выполнено веб-интерфейсом; ветка `brief/*` в `human-merge.sh` живьём не исполнена |
| `make brief-approve` | выполнено |
| engineer до публикации брифа и E1 | **E1 выполнен; авторинг S1 не начат** (`stopped_preflight`, нет `profiles/team-exp.yaml`) — уточнение критерия, принятое владельцем |
| К1: подпись без self-hash | отказ `operator_brief`, прогона нет |
| К2: смёрженный PR недопустимого состава | отказ `pr_files`, прогона нет (попытка 2; попытка 1 не засчитана) |
