# Установщик `df` (команда `darkfactory`), этап 2b — мерж издателя: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** операция издателя `merge` действительно мержит PR вида **stop**. Предпроверку
исполняет отдельная роль ОС `df-precheck` без ключа App, кодом `governance/act_*`
административного дерева. Издатель сам держит свои ограничения. Он публикует аттестацию,
мержит с серверным `sha` и восстанавливается после сбоя по долговечным фазам. Приёмка —
три строки мержа §4.5 живьём на стенде (маршрут stop) и строки изоляции `df-precheck` в
root CI.

**Architecture:** издатель (`df-publisher`, ключ App) остаётся на stdlib (§4.1). Для
предпроверки он выпускает суженный токен чтения одного продукта и вызывает
`sudo -n -u df-precheck /opt/df/libexec/df-precheck` по точной команде. Запрос с токеном
уходит в stdin, связанный вердикт возвращается в stdout. `df-precheck` исполняет
административное дерево `/opt/df/admin/<id>`: `df/`, модули замыкания `governance/`,
`contracts/` и `site/`. В `site/` лежит только чистый Python (steward по коммиту, pyyaml из
sdist), всё сверено с `uv.lock` и покрыто `admin_digest`. Историю доставок проверяющий
читает из временного git-снимка своим адаптером. Код `df-build` не исполняет ни один из
двух процессов.

**Tech Stack:** Python ≥ 3.12. В `df/` только stdlib. В `governance/act_*` — steward
`68f46a9` и pyyaml 6.0.3 в чистом Python. Системные `sudo` (классический 1.9), `git`,
`openssl`. Тесты: pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-13-df-installer-design.md` **rev 10** (этот PR:
§2.5, §3.9, §4.1, §4.5, §5.4, §5.5, §10) и спека A (rev 16 по `depends_on` спеки B; текущий файл — rev 18, читать текущий). Этап 2b — §2.5, строка 2b;
§4.5, три строки мержа и строки `df-precheck`; §5.4, исполнение `merge` для stop; §5.6 в
части `merge`; §10, строка 2b. План спорит со спекой, а не заменяет её.

## Решения (03.11)

1. **Предпроверка — отдельная роль ОС `df-precheck`** (вариант Б, решение владельца 03.11).
   Держатель ключа App не загружает сторонних пакетов. Четыре требования (Codex, приняты
   владельцем 03.11):
   1. `df-precheck` не читает ключ App, память и закрытые файлы издателя (`/etc/df/secrets`,
      `/var/lib/df/publisher`, `/var/lib/df/perimeter`). Лишних дескрипторов не
      наследует, токенов записи не получает.
   2. Проверяющий запускается только фиксированной командой
      `/opt/df/libexec/df-precheck` без аргументов.
   3. Вердикт связан с запросом (`nonce`, `request_digest`, `repository_id`, `pr`,
      `head_sha`, `kind`). Таймаут, ненулевой код, вывод больше предела, разбор с ошибкой
      или несовпадение любого поля дают отказ.
   4. Издатель сам проверяет продукт, отправителя, операцию, `run_id`, `head_sha`, ветку,
      вид и полный состав PR со статусами. Мержит ровно этот PR с `sha = head_sha`.

   **Граница** (спека §5.4): изоляция защищает секреты издателя, но не правильность
   вердикта, если предпроверка скомпрометирована.
2. **В 2b исполняется только вид stop. Candidate и finalize переходят в этап 3** (r1 N3,
   N5, N7, N13; **на подпись владельца** — это сужает строку 2b таблицы §2.5).
   Основание проверено по коду:
   - предпроверка candidate (`act_premerge._trust` → `act_trust.load_trust`) требует
     привязки `/etc/df/trust.toml` и непустого самоотчёта credentials;
   - решение по доставке для finalize (`act_node._delivery_outcome`) требует того же
     `trust`;
   - единственный production `EpochSource` издателя — `NO_BINDING` (`perimeter.py:236`).

   Привязка, источник эпох, контекст credentials производного токена, источник
   `bundle_dir`/DAG для D16, идентичность App-мержера и `review_id` из окончательного
   решения доставки проектируются вместе в плане этапа 3. Там же общая функция поверхности
   D16 по полному составу PR. В 2b издатель отвечает на candidate и finalize
   `refused: kind_unavailable_until_stage3: <kind>`, на brief —
   `refused: brief_precheck_unavailable` (часть C), и никого не зовёт. Роль, протокол,
   административное дерево, фазы мержа и сверка строятся в 2b и исполняются живьём на stop.
   Этап 3 переиспользует ОС-изоляцию, фиксированный запуск, транспорт, фазы и сверку, но
   **расширяет протокол**: контекст корня, адаптация credentials и журнала, нарушения
   периметра. Это новая версия схем `df/precheck-request/v2` и `df/precheck-verdict/v2`, а
   не правка v1 (r2, оценка решения 2). Приёмка candidate и finalize вписана в строку 3 §10
   спеки (r2 N9).
3. **Состав административного дерева** (r1, оценка решения 3). `/opt/df/admin/<id>/`:
   - `df/`, как раньше;
   - `governance/` — модули **AST-замыкания** `governance/act_precheck.py`, включая
     условные и отложенные импорты. Константа `df/admin_tree.py:GOVERNANCE_MODULES`, тест
     сверяет её с замыканием. Чтобы замыкание было узким, построчный разбор first-parent
     выносится из `governance/ops.py` в новый `governance/act_git_rows.py` (Task 2), а
     `ops.py` импортирует его оттуда;
   - `contracts/` — ровно файлы, которые эти модули читают относительно `__file__`.
     Перечень `CONTRACT_FILES` сверяет тест;
   - `site/yaml/` — каталог `lib/yaml` из **sdist** PyYAML 6.0.3. Колеса `none-any` в
     `uv.lock` нет (замер r1, `uv.lock:481–484`, sha256
     `d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f`). Архив
     проверяется по sha256 до распаковки. При извлечении нет ссылок, абсолютных путей и
     `..`, сборки нет;
   - `site/steward/` — `src/steward` коммита
     `68f46a9b2e5cfe9ecd1f09524e986c3f687f4155`. Root делает `git init` и
     `git fetch <url из uv.lock> <sha>` в `admin-home`, сверяет `FETCH_HEAD` с пином и
     извлекает дерево через `git archive`.

   Проверка дерева отвергает `*.so`, `*.dylib`, `*.pyd`, `*.pth`, ссылки и байт-код.
   `admin_digest` покрывает всё. Вход команды (root, читатели) и издатель `site/` не
   импортируют: `site` добавляет только подкоманда `precheck serve`, после проверки UID.
   Добавляет она его **в конец** `sys.path`, после `admin/<id>` и путей stdlib.
4. **Вход проверяющего — существующий вход команды.** Обёртка `df-precheck` запускает
   `<административный Python> -I -B -S /opt/df/bin/darkfactory-entry.py precheck serve`.
   Вход сверяет `admin/<id>` по `admin_digest` до импорта. Договор входа не меняется.
5. **Роль и правило sudoers** (r1 N10, N11). `df-precheck` входит в
   `platform.SERVICE_USERS` и `roles.ROLE_USERS`: nologin, домашний каталог `0700`, без
   административных групп и секретов, своих правил нет (`_NO_RULES`). Правило и его
   Defaults:
   ```
   Defaults!/opt/df/libexec/df-precheck command_timeout=180, !log_input, !log_output, !log_stdin, !log_stdout, !log_stderr, !log_ttyin, !log_ttyout
   df-publisher ALL=(df-precheck) NOPASSWD: NOSETENV: /opt/df/libexec/df-precheck ""
   ```
   - **Надзор за жизненным циклом** (r2 N6). Издатель никогда не посылает сигналов
     процессу sudo и его потомкам: SIGKILL из UID издателя не доходит до команды другой
     UID. Клиент запускает команду новым `platform.run_supervised(argv, data, *, limit,
     wait)`. Он пишет запрос, читает stdout до `limit` (64 КиБ), всё сверх лимита читает
     и отбрасывает (признак `oversize`) и ждёт выхода до `wait`. Три рубежа:
     1. внутренний срок `act_precheck.DEADLINE = 150.0` с: проверяющий сам прерывает
        git-потомков (свои процессы, своя UID), убирает снимок в `finally` и отвечает
        `unset: deadline`;
     2. `command_timeout=180` в sudo (root) — завершение команды и её группы, если
        проверяющий завис сам;
     3. `PRECHECK_WAIT = 200.0` в клиенте — только перестать ждать. Ответ
        `refused: precheck_failed: timeout`, процесс доживает до рубежа 2.

     Классификация в клиенте: выход по сигналу или по коду sudo после `command_timeout`
     (время ≥ 175 с) — `timeout`; `oversize` — при любом коде; иначе ненулевой код —
     `exit`. Снимки, оставшиеся после рубежа 2, убирает следующий вызов под
     `home/precheck.lock` до своей работы. Root-строка проверяет уборку следующим вызовом,
     а не мгновенную;
   - журналирование входа и выхода выключено. В stdin идёт токен чтения, а `log_input`
     и `log_stdin` его иначе записали бы. `sudoers.LOG_KEYS` расширяется ключами входа,
     а `doctor` проверяет **эффективные** значения для этой команды по `sudo -l -U
     df-publisher` (разбор как в 2a). Непроверяемая настройка — «нарушено».
6. **Вид stop — шаблон контракта** (решение владельца 03.11):
   `APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}`. Ветка разбирается **при
   известном `ws_id`**, который берётся из пути файла остановки:
   `parse_stop_branch(ref, ws_id) -> tuple[str, int] | None`. Производитель
   (`act_node._stop_pr`), проверяющий и издатель пользуются одним правилом.
7. **Способ мержа — `squash`**, константа издателя.
8. **Фазы операции `merge`** (r1 N1, N2, N16; r2 N1–N4). Единый договор с R23:
   - **Начало состояния — обычный intent** (`ctx.record_intent`, существующий файл
     `intents/<key>.json`), записанный до первой фазы: `{request_key, head_sha, kind,
     facts}`. В `facts` лежат проверенные факты PR: `head_repo_id`, `head_ref`, `base_ref`,
     `base_tip`, `changes_digest`. С этого момента `has_state` истинен, и R23 превращает
     любой последующий отказ без `verified_not_done` в `unknown: state_exists`.
     `has_state` дополнительно видит файлы фаз и владения. Нечитаемый или повреждённый
     файл тоже считается состоянием;
   - **фазы** — файлы `<intent>.<name>`, каждый пишется один раз (`store.phase`,
     `fsync` файла и каталога):
     - `attest` — до `POST` аттестации. В payload — метаданные предпроверки;
     - `attested` — `{review_id}` после ответа, подтверждённого чтением;
     - `armed` — `{read_at}` после финального чтения. Пишется под владением PR
       (ниже), чтение сделано после записи владения, `PUT` идёт только после `armed`;
     - `put_answer` — `{status}` для **определённого** HTTP-ответа на `PUT`
       (200/405/409/422). При таймауте, транспортной ошибке или 5xx фаза не пишется.
       **`PUT` в операции отправляется не больше одного раза** (r4 N1): только для
       единственной попытки определённый отрицательный ответ доказывает, что мержа этой
       попыткой не будет;
   - **владение PR** (r2 N1) — долговечный файл
     `/var/lib/df/publisher/owners/<repository_id>-<pr>.json` с `request_key`, созданный
     `O_EXCL` + `fsync` перед финальным чтением. Он переживает процесс и снимается только
     вместе с финальным результатом (`done` или проверенный `refused`) этого запроса.
     Запрос с другим ключом при чужом владении получает `refused: pr_busy` до любой своей
     фазы. Нового состояния он не создаёт, поэтому отказ правдив. **Завершение владения
     входит в crash-протокол** (r3 N2): (а) replay окончательного результата `merge` в
     кадре `main` идемпотентно снимает владение, если файл владения называет ключ этого
     запроса; (б) приобретение при существующем файле читает его `request_key`, и если у
     того запроса есть сохранённый окончательный результат, снимает устаревшее владение и
     берёт своё под блокировкой каталога `owners/`. Владение запроса без окончательного
     результата (в том числе с `unknown`) не снимается никем;
   - **предпосылка гарантии — исключительное использование App для мержа через
     издатель этой установки** (r3 N3). Мерж тем же App другим инструментом или другой
     установкой — **вне гарантии**: такой мерж с той же головой в окне владения может быть
     засчитан запросу. Мерж другим актором (не бот App) всегда даёт `unknown`. Спека §5.6
     формулирует это как ограничение, а не обещание распознавания;
   - **финальное чтение** (r2 N4) перед `armed`: PR открыт, `head.sha == head_sha`,
     `head.repo.id`, `head.ref` и `base.ref` равны `facts`. Изменилась база (`base.ref`) —
     `refused: base_changed` с `verified_not_done=True`. Сдвинулась вершина базы
     (`base_tip`) — пересчитать `changes` (compare новой вершины с головой) и повторить
     предпроверку. Отказ — `refused` с `verified_not_done=True`, `PUT` не было; новые
     метаданные пишутся фазой `rechecked.<n>` (`n` = 1, 2, … при последовательных
     сдвигах; каждая со своим `base_tip`, `changes_digest`, digest запроса и метаданными).
     Решающая проверка — последняя `rechecked.<n>`, иначе `attest`. Неатомарность между `armed` и `PUT` остаётся,
     как и согласованная неатомарность предпроверки и доставки (§5.4);
   - **результат** — как в 2a (`store.result`), затем снимается владение.

   **Восстановление идёт первым** (N1): если есть intent, после проверок идентичности и
   полномочий (продукт, `run_id`, self-check, токен) издатель сверяет префикс фаз и ничего
   нового не делает, кроме допустимого продолжения:

   | Префикс | Сверка | Исход |
   |---|---|---|
   | intent | PR открыт, факты равны → продолжить с предпроверки; иначе | `refused` проверенный (ничего внешнего не было) |
   | `attest` | полный обход ревью: ровно одна своя аттестация → записать `attested`, продолжить; ни одной | `unknown: attestation_not_confirmed` (без нового `POST`; клиент — новый `request_id`) |
   | `attest` | больше одной | `unknown: duplicate_attestations` |
   | `attested` | PR открыт, факты равны → финальное чтение и `armed` | иначе `refused: head_changed`/`base_changed` проверенный |
   | `rechecked.<n>` | как `attested`, решающая проверка — последняя | — |
   | `armed` без `put_answer` | PR смёржен ботом App с `head_sha` | `done` |
   | `armed` без `put_answer` | смёржен иначе | `unknown: merge_not_confirmed` |
   | `armed` без `put_answer` | открыт (любая голова) или сбой чтения | `unknown: merge_not_confirmed` (r4 N1). Повторного `PUT` нет: отрицательное наблюдение не доказывает, что потерянный `PUT` не исполнится, ведь голова может вернуться к `head_sha`. Владение остаётся; продолжение после закрытия PR производителем — новым PR |
   | `put_answer` 409 | — | `refused: head_changed` проверенный |
   | `put_answer` 405/422, PR открыт | — | `refused: not_mergeable` проверенный |
   | `put_answer` 200, PR смёржен ботом App с `head_sha` | — | `done` |
   | `put_answer` 405/422, PR смёржен иначе | — | `unknown: merge_not_confirmed` |

   Отказ с `verified_not_done=True` проходит через `merge()` до `main._run_handler`
   (сейчас `merge.py:300–301` теряет признак). Отказ до intent — обычный `refused`.
9. **Аттестация** (§5.4): ревью App `APPROVE` на `commit_id = head_sha` с маркером
   `<!-- df-publisher:v1 attestation=stop request_key=<key> head_sha=<sha> -->`. Тело
   формирует издатель, недоверенного текста нет.
10. **История доставок у проверяющего** (r1 N6; r2 N5, N7, N8). Чтение истории
    выносится из `act_status` в новый модуль `governance/act_history.py`: `load_history`,
    `_event`, `_request_event`, `pr_for_merge`, `_pr_head`, протокол `HistoryScope`
    (`ws_id`, `repo_slug`, `target_dir`). Его зависимости — только `act_envelope`,
    `act_request`, `act_time`, `act_git_rows` (замер AST r2). `act_status` реэкспортирует
    прежние имена, `RunState` удовлетворяет `HistoryScope`. Адаптер
    `governance/act_history_snapshot.py:SnapshotHistory` реализует ровно методы `Ops`,
    которые вызывает `act_history`. Остальные дают `AttributeError` (deny-by-default):
    - git-методы над временным bare-репо в HOME роли: `first_parent_changes`,
      `show_file`, `show_file_bytes`, `rev_parse`, `fetch_branch`. Fetch идёт без
      `--filter`. Авторизация — переменные окружения процесса git `GIT_CONFIG_COUNT=1`,
      `GIT_CONFIG_KEY_0=http.extraHeader`,
      `GIT_CONFIG_VALUE_0=Authorization: Basic base64("x-access-token:<token>")`.
      Это документированная GitHub форма для git по HTTPS с токеном установки (r2 N7);
      Bearer остаётся только в REST. В argv и в файлах конфига заголовка нет. Догрузка
      squash-головы — `refs/pull/<N>/head`;
    - forge-методы через `TokenTransport` поверх REST:
      `prs_containing_commit(slug, sha)` → `GET /repos/{slug}/commits/{sha}/pulls`,
      нормализуется в `{"number", "state": "MERGED"|…, "mergeCommit"}`;
      `pr_facts(slug, pr)` → `GET /repos/{slug}/pulls/{pr}` → `{"headRefOid"}`.
    - `git_base` — новый ключ `publisher.toml` (умолчание `https://github.com`), r2 N8.
11. **Статусы изменений — часть фактов PR** (r1 N8). Издатель передаёт `changes`:
    список `[status, path, previous_filename|None]` из compare. Stop требует ровно одну
    запись `added` по пути `stop_path(...)`, rename отвергается. Ту же форму издатель
    проверяет сам при маршрутизации (требование 4), проверяющий — повторно.
12. **Evidence `merge`** (r1 N14, r3 N5): `kind`; `precheck` — **решающая** проверка
    (`version`, `admin_digest`, `revoke`: `ok|<ошибка>`, `base_tip`, `request_digest`), а при
    повторных — ещё `precheck_initial` (из `attest`); `attestation_review_id`; `merge_commit`;
    `trust_root` — `{"unavailable": "no_binding"}` в 2b, по форме §5.5. Метаданные
    каждой проверки записываются в её фазу (`attest`, `rechecked.<n>`) и при
    восстановлении берутся оттуда, а не вычисляются заново.
13. **Живая приёмка 2b — маршрут stop** (решение владельца 03.11; r1 N9, N15). Засев —
    техническим PR. Машинный пользователь открывает PR с манифестом `review/v1` для
    `ws = stand-<tag>`, узел `n1`, и мержит его через API (squash). Связь merge commit ↔
    PR ↔ head — настоящая, `load_history` видит `DeliveredRequest` поколения 1. Засев не
    является приёмкой candidate и не изображает человеческий акт. Это записывается в
    evidence. `gate_proxy` получает режимы «держать `PUT`» и «выполнить `PUT` выше,
    оборвать ответ вниз».
14. **Стенд заводится заново** (TODO `df-stage2a-stand-secrets-revoke`). Репо, App и
    environment те же, что в 2a. Machine user `betta-tester` получает новый classic PAT
    (R15-2). Ruleset'а нет (R15-1): аттестация доказывается фактом ревью, серверное
    принуждение ruleset 2b не доказывает. Шаги человека — H5–H8.
15. **Хвосты части B для App-режима `merge-pr.sh`** (п. 9a/9c/9d) — Task 9:
    - 9c: путь ключа без ссылок, ни сам файл, ни каталоги после `apps/`;
    - 9d: токен установки отзывается через `trap` сразу после выдачи, при любом выходе;
      исходный код выхода сохраняется;
    - 9a: ruling в TODO.

## Global Constraints

- Пакет `df/` — только stdlib, включая `df/publisher/precheck.py` (проверка
  `tests/df/test_launcher_entry.py:340`).
- `governance/act_precheck.py` и его замыкание: `governance/act_*`, `governance/facts`,
  `governance/ssot_env`, `governance/approval_branches`, `steward`, `yaml`, stdlib. Ни
  `df`, ни `governance/ops`, ни `governance/runner`, ни `governance/act_status` в замыкании
  быть не должно (r2 N5). Замыкание считается от настоящей точки входа, включая условные и
  отложенные импорты, без исключений в анализаторе.
- `_cowork_output/` не читается. Разговор по-русски, код по-английски.
- Тесты не трогают `~/.claude`, `~/.codex`, `~/.config/gh` и настоящих credentials.
  Сеть — только локальные фейки.
- Тесты на переднем плане:
  `perl -e 'alarm 590; exec @ARGV' -- uv run --group governance pytest <path> -q -p no:cacheprovider`.
- Все файлы плана, кроме `governance/ops.py`, — authority-root (`df/`, `tests/df/`,
  префикс `governance/act_`, `contracts/approval-branches/`, `merge-pr.sh`,
  `approval_branches.sh`, `.github/`). PR этапа сливает **владелец**.
- Пределы: запрос ≤ 64 КиБ, вердикт ≤ 64 КиБ (параметр `limit` у `run_with_input`,
  `run_supervised`); `DEADLINE = 150.0`, `command_timeout=180`, `PRECHECK_WAIT = 200.0`,
  `PUT` повторяется только по таблице решения 8;
  `COMPARE_FILE_CAP = 300`.
- Ответ издателя — схема `df/publisher-response/v1` без новых верхних ключей.

## Review Focus

1. **Проверяющий завис сам или вместе с потомком git.** Ожидание: рубеж 1 (`DEADLINE`)
   или рубеж 2 (`command_timeout`) завершает процессы роли, издатель отвечает отказом в
   пределах `PRECHECK_WAIT`, снимок убран сразу или следующим вызовом. Тест — Task 10, root,
   обе ОС.
2. **Голова сменилась между вердиктом и мержем.** Ожидание: `armed` не пишется или `PUT`
   получает 409; исход `refused: head_changed`; повтор — тот же исход без второго `PUT`.
   Тест — Task 8, `test_head_change_after_precheck`.
3. **Ответ на `PUT` потерян; PR смёржен или ещё нет.** Ожидание: смёржен ботом — `done`;
   открыт — `unknown`, владение остаётся, второго `PUT` нет; смёржен человеком — `unknown`.
   Окончательный отказ по таймеру или по отрицательному наблюдению не ставится. Тест —
   Task 8.
4. **Ответ на `POST` аттестации потерян.** Ожидание: восстановление находит ровно одну
   свою аттестацию и продолжает. Не находит — `unknown`, второго `POST` нет. Тест — Task 8,
   `test_lost_attestation_post_*`.
5. **Stop на устаревшее поколение, изменение уже доставленного файла остановки,
   расхождение пути, ветки и содержимого.** Ожидание: отказ до аттестации. Тест — Task 4.

---

## Файлы

| Файл | Роль | Task |
|---|---|---|
| `contracts/approval-branches/v1/patterns.env`, `approval_branches.{py,sh}`, `governance/act_node.py` | шаблон stop | 1 |
| `governance/act_git_rows.py` (новый), `governance/ops.py`, `governance/act_forge_http.py` (новый) | разбор first-parent, HTTPS-транспорт с токеном | 2 |
| `governance/act_history_snapshot.py` (новый), `governance/act_status.py` | адаптер истории, `HistoryScope` | 3 |
| `governance/act_precheck.py` (новый) | программа проверяющего (stop) | 4 |
| `df/admin_tree.py` (новый), `df/admin.py` | административное дерево | 5 |
| `df/platform.py`, `df/roles*.py`, `df/sudoers.py`, `df/libexec.py`, `df/cli.py`, `df/doctor.py` | роль, правило, обёртка, `precheck serve` | 6 |
| `df/platform.py` (`limit`), `df/publisher/precheck.py` (новый) | клиент издателя | 7 |
| `df/publisher/{merge,ops,store,main}.py` | маршрут stop, фазы, аттестация, `PUT`, сверка, evidence | 8 |
| `merge-pr.sh`, `governance/act_app_token.py` | 9c/9d | 9 |
| `tests/df/test_acceptance_root_roles.py`, `tests/df/roles_world.py`, `.github/workflows/df-stage1.yml` | root-строки | 10 |
| `tests/df/gate_proxy.py`, `tests/df/live_stand.py`, `.github/workflows/df-stage2-live.yml` | живые строки | 11 |
| evidence, `TODO.md` | учёт | 12 |

---

### Task 1: шаблон ветки stop в контракте

**Files:** `contracts/approval-branches/v1/patterns.env` (после строки 42),
`governance/approval_branches.py`, `approval_branches.sh`, `governance/act_node.py:1062-1071`,
`df/publisher/merge.py:141-185`; тесты `tests/test_approval_branches.py`, тест `_stop_pr`,
`tests/df/test_publisher_merge.py`, `tests/test_harness_trust.py`.

**Interfaces:**
- Produces: `approval_branches.stop_branch(ws_id: str, node: str, generation: int) -> str`;
  `approval_branches.parse_stop_branch(ref: str, ws_id: str) -> tuple[str, int] | None`;
  `df/publisher/merge.py`: `_STOP_KEY = "APPROVAL_STOP_TEMPLATE"`, вид `stop` в
  `branch_kinds`; `route(head_ref, changes, patterns) -> Kind | Refused`, где
  `changes: Sequence[Change]`, `Change = tuple[str, str, str | None]`.

- [ ] **Step 1: Тесты:** `test_stop_branch_renders_template`
  (`stop_branch("ws1","design",3) == "stop/ws1-design-g3"`);
  `test_parse_stop_branch_with_dashed_ws_and_node` (`ws_id="a-b"`, `node="c-d"`);
  `test_parse_stop_branch_rejects_other_ws`; `test_act_node_stop_pr_uses_contract`;
  `test_shell_reader_reads_stop_template`; `test_stop_routes_on_one_added_file`;
  `test_stop_modified_or_renamed_file_ambiguous`; `test_stop_branch_path_mismatch_ambiguous`;
  `test_stop_glob_equals_the_contract`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. В `route` stop — ровно одна запись
  `("added", path, None)` под `STOP_PATH`, а `node`/`generation` пути равны разбору ветки
  при `ws_id` из пути. Иначе `ambiguous_kind`.
- [ ] **Step 4:** PASS. **Step 5:** коммит `feat(approval-branches): stop branch template`.

### Task 2: `act_git_rows` и `TokenTransport`

**Files:** создать `governance/act_git_rows.py`, `governance/act_forge_http.py`; изменить
`governance/ops.py` (импорт из `act_git_rows`); тесты `tests/test_act_git_rows.py`,
`tests/test_act_forge_http.py`.

**Interfaces:**
- Produces: `act_git_rows.FIRST_PARENT_LOG`, `act_git_rows.parse_first_parent(stdout) ->
  FirstParentRows`, `FirstParentRows`. `ops._FIRST_PARENT_LOG`/`_parse_first_parent`
  остаются именами-синонимами. `act_forge_http.TokenTransport(token, *, api_base,
  opener=None, timeout=60.0)` с методом `send(request) -> Response` (протокол
  `steward.approvalfacts.v3.transport.Transport`) и `get_json(path) -> tuple[int, object]`
  для REST-методов адаптера. Opener — `build_opener(ProxyHandler({}), _NoRedirect)`, тело
  до `act_anon.MAX_BODY + 1`, `Authorization: Bearer`. Только GET и GraphQL-query,
  полный обход страниц по `Link`.
- [ ] **Step 1: Тесты:** `test_ops_reexports_rows` (тождество объектов);
  `test_parse_first_parent_unchanged` (прежние фикстуры); `test_get_sends_bearer`;
  `test_graphql_query_only`; `test_redirect_not_followed`; `test_body_over_limit`;
  `test_env_proxy_ignored`; `test_token_absent_from_errors`; `test_paging_complete`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ все тесты, импортирующие `ops`).
- [ ] **Step 5:** коммит `refactor(act): git row parser module and token transport`.

### Task 3: `act_history` и адаптер истории

**Files:** создать `governance/act_history.py`, `governance/act_history_snapshot.py`;
изменить `governance/act_status.py` (перенос и реэкспорт); тесты
`tests/test_act_history.py`, `tests/test_act_history_snapshot.py`.

**Interfaces:**
- Consumes: Task 2.
- Produces:
  ```python
  # governance/act_history.py
  class HistoryScope(Protocol):
      ws_id: str
      repo_slug: str
      target_dir: str
  def load_history(ops, scope: HistoryScope, base_ref: str) -> History | Unavailable
  # governance/act_history_snapshot.py
  @dataclass(frozen=True)
  class Scope:
      ws_id: str
      repo_slug: str
      target_dir: str
  class SnapshotHistory:  # deny-by-default Ops for act_history
      def __init__(self, git_dir: Path, transport: TokenTransport, token: str,
                   *, git_base: str) -> None
  def open_snapshot(home: Path, full_name: str, refs: Sequence[str], token: str,
                    *, git_base: str, deadline: float) -> Path   # bare repo; caller removes
  def git_auth_env(token: str) -> dict[str, str]   # GIT_CONFIG_* with Basic x-access-token
  ```
- [ ] **Step 1: Тесты** (настоящий `git`, локальный git HTTP-сервер на 127.0.0.1, replay
  forge-ответов): `test_act_history_closure_is_narrow` (AST-замыкание = `act_history`,
  `act_envelope`, `act_request`, `act_time`, `act_git_rows`);
  `test_act_status_reexports_history` (тождество объектов, прежние тесты `act_status`
  зелёные); `test_squash_delivery_head_fetched_from_pull_ref`;
  `test_production_load_history_sees_delivered_request`;
  `test_direct_commit_is_not_delivery`; `test_unlisted_method_raises`;
  `test_server_accepts_basic_rejects_bearer` (сервер принимает только
  `Basic x-access-token`, все fetch включая `refs/pull/N/head`);
  `test_git_argv_and_config_have_no_token` (ни токена, ни его base64 в argv, `config
  --list`, ошибках); `test_fetch_respects_deadline`;
  `test_prs_containing_commit_normalized_from_rest`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ `tests/test_act_status*.py`,
  `tests/test_act_node*.py`).
- [ ] **Step 5:** коммит `feat(act): narrow history reader and snapshot adapter`.

### Task 4: программа проверяющего `governance/act_precheck.py`

**Files:** создать `governance/act_precheck.py`; тест `tests/test_act_precheck.py`.

**Interfaces:**
- Consumes: Tasks 1–3; `act_envelope.stop_path/parse_stop/node_history/DeliveredRequest`;
  `act_history.load_history`.
- Produces (договор, который читает Task 7):
  - запрос `df/precheck-request/v1` со строгими ключами: `schema, nonce, kind,
    repository_id, full_name, pr, head_sha, head_ref, base_ref, base_tip, changes, token,
    api_base, git_base`. `kind ∈ {"stop"}` (этап 3 расширит перечень);
  - вердикт `df/precheck-verdict/v1`: `schema, nonce, request_digest, repository_id, pr,
    head_sha, kind, outcome, reason, version, admin_digest`. `outcome ∈ {allow, refuse,
    unset}`, `request_digest = sha256(canonical_json(request без "token"))`,
    `version = PRECHECK_VERSION = "df-precheck/1"`;
  - `def serve(stdin: BinaryIO, stdout: TextIO, *, admin_digest: str, home: Path,
    deps: PrecheckDeps | None = None) -> int` — 0 при любом вердикте, 2 при запросе,
    который не разобран (вердикта нет);
  - `PrecheckDeps(transport_for, snapshot_for, now)` для тестов.
  Stop: `changes == [["added", stop_path(ws,node,gen), None]]`; ветка разбирается при `ws`
  из пути; `parse_stop(show_file(head_sha, path))` даёт `StopRecord(node, gen, …)`, равный
  пути; `node_history(load_history(SnapshotHistory, Scope(ws,…), base_tip), node).current`
  — `DeliveredRequest` поколения `gen`. Иначе `refuse` с причиной. Ошибка чтения —
  `unset`. Снимок открывается в `home/precheck-snapshots/<nonce>` и удаляется в
  `finally`. На старте, под `flock` на `home/precheck.lock`, удаляются снимки,
  оставшиеся от прерванных вызовов. Внутренний срок `DEADLINE = 150.0` с: по нему
  проверяющий прерывает своих git-потомков, убирает снимок и отвечает `unset: deadline`.
- [ ] **Step 1: Тесты:** `test_stop_current_generation_allowed`;
  `test_stop_for_superseded_generation_refused`; `test_stop_already_stopped_refused`;
  `test_stop_path_content_mismatch_refused`; `test_stop_branch_mismatch_refused`;
  `test_stop_modifying_delivered_stop_refused`; `test_history_unavailable_is_unset`;
  `test_unknown_request_key_exit_2`; `test_kind_candidate_rejected_by_schema`;
  `test_verdict_binds_request`; `test_token_not_in_verdict_or_stderr`;
  `test_snapshot_removed_after_run_and_stale_cleared`; `test_deadline_kills_git_and_answers_unset`;
  `test_closure_excludes_act_status_ops_df`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** коммит `feat(act): precheck program (stop) for the df publisher`.

### Task 5: административное дерево

**Files:** создать `df/admin_tree.py`; изменить `df/admin.py` (`install_admin`,
`admin_code_problems`, `expected_admin_digest`); тесты `tests/df/test_admin_tree.py`,
`tests/df/test_admin.py`.

**Interfaces:**
- Produces: `GOVERNANCE_MODULES`, `CONTRACT_FILES`, `FORBIDDEN_SUFFIXES = (".so",
  ".dylib", ".pyd", ".pth", ".pyc")`;
  `@dataclass(frozen=True) class SitePins: yaml_url: str; yaml_sha256: str;
  steward_url: str; steward_commit: str`;
  `def locked_pins(uv_lock: bytes) -> SitePins` (`tomllib`, sdist pyyaml, git-источник
  steward);
  `def install_site(dest: Path, pins: SitePins, *, layout: Layout,
  fetch: Callable[[str], bytes], git: Path) -> None`.
- [ ] **Step 1: Тесты:** `test_governance_modules_equal_ast_closure` (включая отложенные
  импорты; `ops`, `runner`, `df` вне замыкания); `test_contract_files_cover_file_relative_reads`;
  `test_locked_pins_from_uv_lock` (значения из `uv.lock` этого дерева);
  `test_sdist_sha_mismatch_refused`; `test_sdist_link_or_dotdot_refused`;
  `test_steward_fetch_head_mismatch_refused`; `test_install_admin_exact_tree`;
  `test_native_extension_refused`; `test_pth_refused`; `test_digest_covers_site`;
  `test_entry_does_not_import_site` (вход команды и `publisher serve` не видят `site` в
  `sys.path`); `test_precheck_imports_under_dash_S` (административный Python `-I -B -S`,
  `sys.path` = stdlib + `admin/<id>` + `admin/<id>/site`, импорт
  `governance.act_precheck`).
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Протокол `install_admin` сохраняется**
  (r3 N4): `created` (частичный) → `created` (полный) → `sync_tree(admin/<id>)` →
  `sync_dir(admin)`, только расширяется содержимое копии: `df/`, `governance/*`,
  `contracts/*` из `devtools_ref`, `site/*` из проверенных исходников (r4 N2).
  **Распакованного кэша нет.** Кэшируются только исходные артефакты: архив sdist
  (`admin-home/site-src/pyyaml-<sha256>.tar.gz`, при каждом использовании сверяется по
  sha256, при несовпадении удаляется и скачивается заново) и git-репозиторий steward
  (`admin-home/site-src/steward.git`, при каждом использовании проверяется, что коммит пина
  есть и `git cat-file -e <sha>^{tree}`). Файлы `site/` пишутся в `admin/<id>/site`
  **внутри** шага копирования протокола прямо из потока: `tarfile` над проверенным
  архивом (только `lib/yaml/**`, только обычные файлы, без `..` и абсолютных путей) и
  `git archive <sha> src/steward` через pipe в `tarfile`. Частичная копия — это обычная
  частичная `admin/<id>`, которую маршрут уже умеет убирать. Новых промежуточных деревьев
  в модели сбоев нет. Тест `test_interrupted_site_copy_recovers_to_clean_install`
  (обрыв после нескольких файлов каждого источника, повтор даёт дерево, равное чистой
  установке). `fetch` — `urllib` от root с `admin_env`, без `uv`.
  Тест `test_install_admin_protocol_events_unchanged` сверяет события с нормативным
  протоколом (`tests/df/test_admin.py:438–447`).
- [ ] **Step 5:** коммит `feat(df): admin tree with the precheck closure and pure site`.

### Task 6: роль `df-precheck`, правило, обёртка, `precheck serve`

**Files:** `df/platform.py`, `df/roles.py`, `df/roles_setup.py`, `df/sudoers.py`
(`_rules`, `_NO_RULES`, `LOG_KEYS`, Defaults команды), `df/libexec.py`, `df/cli.py`,
`df/doctor.py`; соответствующие тесты в `tests/df/`.

**Interfaces:**
- Produces: обёртка `/opt/df/libexec/df-precheck`; текст правила и Defaults из решения 5;
  `sudoers.INPUT_LOG_KEYS = ("log_input", "log_stdin", "log_ttyin")`; подкоманда
  `precheck serve`. Порядок: `getuid() == uid(df-precheck)`, иначе код 2; затем окружение
  процесса заменяется на `{"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": <home роли>}`;
  `site` добавляется в конец `sys.path`; импорт и вызов `serve(...)`.
- [ ] **Step 1: Тесты:** `test_render_precheck_rule_and_defaults`;
  `test_visudo_accepts_rendered_text` (как в 2a); `test_precheck_has_no_rules`;
  `test_doctor_flags_effective_input_logging`; `test_roles_setup_creates_precheck_role`;
  `test_wrapper_text_precheck`; `test_precheck_serve_refuses_other_uid`;
  `test_precheck_serve_resets_environment`; `test_roles_setup_rerun_on_2a_install`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** коммит `feat(df): df-precheck role, sudoers rule and wrapper`.

### Task 7: клиент издателя `df/publisher/precheck.py`

**Files:** создать `df/publisher/precheck.py`; изменить `df/platform.py`
(`run_supervised`), `df/stage2_config.py` (`git_base` в `publisher.toml`: умолчание,
проверка, разбор, запись), `df/publisher/main.py`/`ops.py` (передача `git_base` в
`OpContext`); тесты `tests/df/test_publisher_precheck.py`, `tests/df/test_platform.py`,
`tests/df/test_stage2_config.py`.

**Interfaces:**
- Produces:
  ```python
  # df/platform.py
  def run_supervised(argv: Sequence[str], data: bytes, *, limit: int, wait: float,
                     env: Mapping[str, str]) -> SupervisedResult
  @dataclass(frozen=True)
  class SupervisedResult:
      status: int | None      # None: still running when `wait` passed
      signal: int | None
      stdout: bytes           # at most `limit`
      oversize: bool
      elapsed: float
  # df/publisher/precheck.py
  @dataclass(frozen=True)
  class PrFacts:
      head_repo_id: int
      head_ref: str
      base_ref: str
      base_tip: str
      changes: tuple[Change, ...]
  @dataclass(frozen=True)
  class PrecheckResult:
      refused: Refused | None
      version: str | None
      admin_digest: str | None
      request_digest: str | None   # of the bound verdict; None if none was bound
      revoke: str            # "ok" or the error text, never the token
  def admin_precheck(kind: Kind, oc: OpContext, pr: Mapping[str, object],
                     facts: PrFacts) -> PrecheckResult
  PRECHECK_WAIT = 200.0
  ```
  `run_supervised` никому не посылает сигналов. Сверх `limit` читает и отбрасывает до EOF,
  ждёт выхода до `wait`. Шаги `admin_precheck`: токен чтения (`READ_PERMISSIONS`,
  `repository_ids = [product]`) → запрос с `nonce = secrets.token_hex(16)` и
  `git_base = oc.git_base` → `run_supervised(("/usr/bin/sudo", "-n", "-u", "df-precheck",
  <opt>/libexec/df-precheck), data, limit=64 << 10, wait=PRECHECK_WAIT,
  env={"PATH": "/usr/bin:/bin", "LANG": "C"})` → классификация по решению 5 → строгий
  разбор и сверка связи → `DELETE /installation/token` в `finally`. `refuse` →
  `Refused(reason)`, `unset` → `Refused("precheck_unset: …")`, нарушение протокола →
  `Refused("precheck_failed: <timeout|exit|oversize|parse|binding_mismatch>")`.
- [ ] **Step 1: Тесты** (исполняемый скрипт в `tmp_path` вместо команды):
  `test_allow_bound_verdict_passes`; `test_verdict_for_other_head_refused`;
  `test_nonce_mismatch_refused`; `test_digest_mismatch_refused`; `test_extra_key_refused`;
  `test_oversize_drained_not_killed` (скрипт пишет 1 МиБ и выходит сам; результат
  `oversize`, сигналов не было); `test_wait_expiry_is_timeout_without_signal`;
  `test_signal_exit_is_timeout`; `test_nonzero_exit_refused`; `test_read_token_only`;
  `test_token_revoked_after_call_even_on_failure`;
  `test_token_absent_from_result_and_evidence`; `test_publisher_precheck_stdlib_only`;
  `test_request_digest_matches_sent_request` (захват stdin, независимый расчёт digest
  без токена);
  `test_git_base_default_and_custom_reach_request`; `test_git_base_invalid_refused`;
  `test_2a_publisher_toml_still_parses`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** коммит `feat(publisher): supervised precheck client bound to the request`.

### Task 8: исполнение `merge` для stop

**Files:** `df/publisher/merge.py`, `df/publisher/ops.py`, `df/publisher/store.py`
(`phase`, `read_phases`, владение PR, `has_state` видит фазы и владение),
`df/publisher/main.py` (передача `verified_not_done`); тест
`tests/df/test_publisher_merge.py` (`test_no_mutating_calls` пересматривается: мутирующие
вызовы — только для stop после положительного вердикта).

**Interfaces:**
- Consumes: Task 1 (`route` по `changes`), Task 7.
- Produces: `MERGE_METHOD = "squash"`; `ATTESTATION_MARKER`;
  `store.phase(res, name: str, payload: Mapping) -> None`,
  `store.read_phases(res) -> dict[str, Mapping]`,
  `store.own_pr(layout, repository_id: int, pr: int, request_key: str) -> bool` (`False` —
  чужое владение), `store.release_pr(...)`;
  `def recover_merge(oc, auth, req, intent, phases) -> Response` (таблица решения 8);
  `pr_changes(oc, token, tip, head_sha) -> tuple[Change, ...]` (вместо `pr_paths`).

Порядок (без фаз):
1. Шаги 2a: контекст, шаблоны, self-check, токен чтения.
2. Есть intent → `recover_merge`.
3. Чтения PR, ветка, `head_repo`, `tip`, `changes` — как в 2a.
4. `route` → candidate/finalize → `refused: kind_unavailable_until_stage3: <kind>`;
   brief → `refused: brief_precheck_unavailable`.
5. stop: владение PR занято другим ключом → `refused: pr_busy`.
6. `admin_precheck`. Отказ → `refused`, состояния нет.
7. intent (`facts`) → фаза `attest` → `POST` аттестации → подтверждение → `attested`.
   Неопределённый `POST` → `recover_merge`.
8. Владение PR → финальное чтение (решение 8) → `armed` → `PUT` с
   `{"merge_method": "squash", "sha": head_sha}` токеном
   `{"contents": "write", "pull_requests": "write"}`. Определённый ответ → `put_answer` →
   `recover_merge`. Таймаут, 5xx, транспорт → `recover_merge` без `put_answer`.
9. Финальный результат → снять владение.

- [ ] **Step 1: Тесты** — через настоящий `main`, файловое хранилище с пустого состояния,
  `MergeGitHub` с `PUT` и ревью; падения — исключением в точке (`fault_point`) и новым
  вызовом `main`:
  `test_stop_attested_then_merged_with_server_sha`; `test_identical_retry_replays`;
  `test_crash_after_post_before_attested_recovers` (никакого ручного intent в фикстуре);
  `test_exception_after_post_is_unknown_not_internal_refusal`;
  `test_revoked_run_with_state_is_unknown`; `test_corrupt_phase_counts_as_state`;
  `test_attested_without_armed_continues`;
  `test_crash_after_armed_before_put_then_other_request_busy` (B получает `pr_busy`;
  повтор A видит открытый PR → `unknown`, `PUT` не отправлен);
  `test_held_put_head_moves_and_returns_then_lands` (r4 N1: удержанный `PUT₁` → таймаут →
  голова H₂ → восстановление `unknown` → голова снова H → `PUT₁` исполнен → повтор `done`;
  окончательного отказа и снятия владения не было ни на одном шаге);
  `test_single_put_ever` (журнал фейка: в любой последовательности сбоев `PUT` ≤ 1);
  `test_single_attempt_409_is_verified_refusal`; `test_clock_jump_does_not_change_outcome`;
  `test_crash_after_result_before_release_replay_releases` (r3 N2: повтор A снимает
  владение, B проходит); `test_stale_owner_with_final_result_taken_over`;
  `test_owner_with_unknown_never_released`; `test_other_tool_same_app_is_outside_guarantee`
  (документирующий тест договора r3 N3: засчитывается, evidence помечает предпосылку);
  `test_rechecked_twice_evidence_shows_decisive_and_initial` (r3 N5); `test_put_409_head_changed`; `test_put_405_open_not_mergeable`;
  `test_lost_put_reconciles_done`; `test_merged_by_human_is_unknown`;
  `test_retarget_between_precheck_and_final_read_refused` (r2 N4);
  `test_base_tip_moved_rechecks`; `test_lost_attestation_post_found_once_continues`;
  `test_lost_attestation_post_not_found_unknown_no_repost`;
  `test_duplicate_attestations_unknown`; `test_verified_refusal_kept_through_merge_wrapper`;
  `test_candidate_finalize_refused_stage3`; `test_brief_refused`;
  `test_precheck_refusal_writes_no_state`; `test_owner_released_after_final_result`;
  `test_evidence_complete_and_recovered_from_phase`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS всего `tests/df/`.
- [ ] **Step 5:** коммит `feat(publisher): stop merge with phases, ownership, reconcile`.

### Task 9: хвосты App-режима `merge-pr.sh` (9c, 9d)

**Files:** `merge-pr.sh:310-325`, `merge-pr.sh:494-543`, `governance/act_app_token.py`
(`--revoke`, токен из stdin); тесты `tests/test_merge_pr.py`, `tests/test_act_app_token.py`.

- [ ] **Step 1: Тесты:** `test_app_key_file_symlink_refused`,
  `test_app_key_dir_symlink_under_apps_refused`; `test_token_revoked_on_success`,
  `test_token_revoked_on_put_refusal`, `test_token_revoked_on_die_after_issue`,
  `test_exit_status_preserved_when_revoke_fails`; `test_revoke_reads_token_from_stdin_only`.
- [ ] **Step 2–4:** FAIL → реализовать (`trap` сразу после выдачи) → PASS.
- [ ] **Step 5:** коммит `fix(merge-pr): app key without links, revoke installation token`.
  В TODO закрыть 9c/9d и записать ruling 9a.

### Task 10: root-строки `df-precheck`

**Files:** `tests/df/test_acceptance_root_roles.py`, `tests/df/roles_world.py`
(`FIXTURE_FROM_CHECKOUT` дополняется деревом Task 5), `.github/workflows/df-stage1.yml`
(job `stage2a`).

Строки (через настоящие `sudo` и обёртки, обе ОС):

| Строка | Механизм | Положительный контроль |
|---|---|---|
| `df-precheck` читает `/etc/df/secrets/app-*.pem`, `/var/lib/df/publisher/*`, `/var/lib/df/perimeter/*`, `~df-exec/.config/df/github-token` | отказ ОС | root читает те же файлы |
| память издателя: процесс `df-publisher` с sentinel в памяти; `df-precheck` читает `/proc/<pid>/mem` (Linux) / `task_for_pid` через `lldb -p` или `vmmap` (macOS) | отказ ОС | root видит sentinel тем же наблюдателем |
| `df-exec`, `df-review`, оператор вызывают `df-precheck`; `df-publisher` — с аргументом | отказ sudoers | `df-publisher` без аргументов — проходит |
| унаследованное: издатель открывает sentinel-fd без `CLOEXEC` и держит sentinel-переменную; проверяющий заблокирован на чтении stdin (запрос ещё не записан) | root смотрит `/proc/<pid>/fd` и `environ` (Linux), `lsof -p` и `ps eww` (macOS): sentinel-fd и sentinel-переменной нет, UID = `df-precheck` | тот же наблюдатель видит sentinel у издателя |
| зависание git: `git_base` — порт, который принимает соединение и молчит | рубеж 1: `unset: deadline` за ≤ 155 с, git-потомков нет, снимка нет, lock свободен | — |
| зависание самого проверяющего: он получает `SIGSTOP` от root-наблюдателя после старта | рубеж 2 (`command_timeout`): клиент возвращает `precheck_failed: timeout` за ≤ 200 с, через ≤ 185 с от старта процессов UID `df-precheck` нет; следующий вызов убирает оставшийся снимок | без Defaults команды процесс жив после 185 с (контроль, что убил именно sudo) |
| переполнение вывода: в `api_base` фейка ответ, ради которого проверяющий пишет вердикт > 64 КиБ (поле `reason` из недоверенного содержимого PR) | `precheck_failed: oversize`; сигналов издатель не слал; процесс завершился сам | — |
| журналирование входа: глобальные `Defaults log_input, log_output` во временном sudoers-фрагменте теста | sentinel-токен запроса отсутствует в I/O-журнале sudo; `doctor` зелёный | без Defaults команды sentinel в журнале есть (контроль включённости) |
| положительный stop: локальный фейк GitHub (`fake_github.py`) + dumb-HTTP git-репо со squash-доставкой | вердикт `allow` через всю цепочку издатель → sudo → вход → `act_precheck` | устаревшее поколение → `refuse` |

- [ ] **Step 1:** тесты строк (имена `test_precheck_*`). Тестового вида запроса в
  production-протоколе нет.
- [ ] **Step 2:** локально пропуск (не root), в CI — job `stage2a` на обеих ОС.
- [ ] **Step 3:** коммит `test(df): root rows for df-precheck`.

### Task 11: живые строки мержа на стенде

**Files:** `tests/df/gate_proxy.py` (режимы), `tests/df/live_stand.py`,
`tests/df/test_live_stand.py`, `tests/df/test_gate_proxy.py`,
`.github/workflows/df-stage2-live.yml`.

**Interfaces:**
- Produces: `gate_proxy` с режимами `hold(method, path_re)` (держать до `release()`) и
  `drop_response(method, path_re)` (переслать выше, дождаться ответа, оборвать соединение
  вниз). Журнал различает «запрос доставлен выше» и «ответ доставлен вниз». Прежний режим
  POST ревью сохраняется.

Строки:
- **10 «засев»:** технический PR с манифестом `review/v1` для `ws = stand-<tag>`, узел `n1`;
  машинный пользователь мержит его (squash). Evidence: номер PR, head, merge commit.
  Контроль: `load_history` проверяющего видит `DeliveredRequest` g1. Затем stop-PR на
  `stop_branch("stand-<tag>","n1",1)` с одним файлом `stop_path(...)`.
- **11 «смена head»:** `hold("PUT", …/merge)`. Пока `PUT` удержан, машинный пользователь
  пушит второй коммит. `release()`. Ожидание: `refused: head_changed`, PR не смёржен.
  Повтор того же запроса — тот же исход, `PUT` выше один.
- **12 «оборванный ответ и идентичный повтор»** (случай «`PUT` ещё исполняется» проверяется
  офлайн в Task 8, `test_put_in_flight_open_is_unknown_then_done`): новый засев и stop-PR (`ws = stand-<tag>-b`),
  `drop_response("PUT", …/merge)`. Ожидание: `done` через сверку (`merged_by` = бот,
  голова). Повтор — `done`, `replayed: True`, `PUT` выше один.
- **9 «уборка»:** закрыть открытые PR, удалить ветки. Коммиты засева и мержи остаются в
  истории `master` стенда, это записывается в evidence.

- [ ] **Step 1:** офлайн-тесты `gate_proxy` (порядок hold → push → release; один
  выполненный `PUT` при оборванном ответе) и помощников `live_stand` (зависимости строк,
  разбор журнала, отсутствие чтения `DF_STAND_*` вне живого прогона).
- [ ] **Step 2:** workflow: строки в `ROWS`, `needs`.
- [ ] **Step 3:** коммит `test(df): live merge rows on the stop route`.

### Task 12: живой прогон, evidence, учёт

- [ ] **Step 1:** владелец выполняет H5–H7 и запускает `df-stage2-live` на `master`.
- [ ] **Step 2:** `docs/evidence/2026-11-XX-df-stage2b-acceptance.md`: прогон, ячейки,
  строки 10–12, журнал прокси, `merged_by`, SHA, засев. Таблица «виды мержа»: stop — живьём
  и root; candidate, finalize — **не реализованы в 2b** (этап 3); brief — отказ (часть C).
  Ни одной формулировки «все виды мержа проверены».
- [ ] **Step 3:** TODO: закрыть `df-installer-stage2b`. Добавить пункт этапа 3
  «предпроверка candidate и finalize издателем: привязка, EpochSource, контекст
  credentials производного токена, `bundle_dir`/DAG D16, идентичность мержера, `review_id`
  из решения доставки» с переносом находок r1 N3–N5, N7, N13. H8.
- [ ] **Step 4:** PR evidence (owner-merge).

## Шаги человека

- **H5.** Новый classic PAT `betta-tester`, `repo`, 30 дней → секрет
  `DF_STAND_MACHINE_TOKEN` environment `df-stage2-live`.
- **H6.** Новый ключ App 5285716 → секрет `DF_STAND_APP_KEY`. Установка 170589886
  охватывает `df-roles-probe-private` с `contents: write`, `pull_requests: write`.
- **H7.** В `df-roles-probe-private` разрешён squash-мерж.
- **H8.** После прогона, в том числе аварийного, отозвать PAT и ключ, удалить секреты.

## Вне этого плана

- Предпроверка candidate и finalize издателем, общая функция поверхности D16 по полному
  составу PR — этап 3 (решение 2).
- Предпроверка брифа — часть C (Task 17 approval-act).
- `setup-trust`, `history_gh_config_dir`, `--rebind`, production `EpochSource` — этап 3.
