# Установщик `df` (команда `darkfactory`), этап 2b — мерж издателя: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** операция издателя `merge` действительно мержит. Предпроверку исполняет отдельная
роль ОС `df-precheck` без ключа App, тем же кодом `governance/act_*`, что конвейер. Издатель
сам держит свои ограничения: продукт, отправитель, операцию, точный SHA. Он публикует
аттестацию для finalize и stop, мержит с серверным `sha` и после неопределённого исхода
сверяется с GitHub. Приёмка — три строки мержа §4.5 живьём на стенде, на маршруте stop.

**Architecture:** издатель (`df-publisher`, ключ App) остаётся на stdlib (§4.1). Для
предпроверки он выпускает суженный токен чтения продукта и вызывает
`sudo -n -u df-precheck /opt/df/libexec/df-precheck` по точной команде. Запрос уходит в stdin,
вердикт возвращается в stdout. `df-precheck` исполняет административное дерево
`/opt/df/admin/<id>`: к пакету `df/` добавляются нужные модули `governance/` и каталог
`site/` с чистым Python steward и pyyaml. Дерево ставит root, сверка — по `admin_digest`.
Код `df-build` не исполняет ни один из двух процессов.

**Tech Stack:** Python ≥ 3.12. В `df/` только stdlib. `governance/act_*` использует
steward `68f46a9` и pyyaml, оба без нативных расширений. Системные `sudo`, `git`, `openssl`.
Тесты: pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-13-df-installer-design.md` **rev 10** (этот PR:
§2.5, §3.9, §4.1, §4.5, §5.4, §5.5, §10) и спека A **rev 16**. Этап 2b — это §2.5, строка 2b;
§4.5, три строки мержа; §5.4, исполнение `merge`; §5.6 в части `merge`; §10, строка 2b.
План спорит со спекой, а не заменяет её: исполнитель читает обе.

## Решения (03.11)

1. **Предпроверка — отдельная роль ОС `df-precheck`** (вариант Б, решение владельца 03.11).
   Держатель ключа App не загружает сторонних пакетов. Предпроверка исполняется от роли без
   ключа, из административного дерева, а не из релиза `df-build`. Четыре требования
   (Codex, приняты владельцем 03.11):
   1. `df-precheck` не читает ключ App, память и закрытые файлы издателя (`/etc/df/secrets`,
      `/var/lib/df/publisher`, `/var/lib/df/perimeter`). Лишних дескрипторов он не наследует
      (sudo `closefrom`, `close_fds`), токенов записи не получает. Токен чтения выпускается
      на один продукт с `contents: read`, `pull_requests: read`.
   2. Проверяющий запускается только фиксированной командой
      `/opt/df/libexec/df-precheck` без аргументов. Ни отправитель, ни запрос не выбирают
      программу, путь, модуль или вид проверки вне таблицы.
   3. Вердикт связан с запросом: `schema`, `nonce`, `repository_id`, `pr`, `head_sha`,
      `kind`, `request_digest`. Таймаут, ненулевой код, вывод больше предела, разбор с
      ошибкой или несовпадение любого поля дают отказ
      `refused: precheck_failed: <причина>`.
   4. Издатель сам проверяет продукт по `products.toml`, отправителя, операцию,
      `run_id`, `head_sha`, вид и полный состав PR. Мержит он только с `sha = head_sha`.
      Ответ проверяющего разрешает **ровно этот** мерж и не открывает никакого другого
      запроса к GitHub.

   **Граница (записать в спеку §5.4 явно):** изоляция защищает секреты издателя. Правильность
   вердикта, если сама предпроверка скомпрометирована (подмена steward или pyyaml в
   административном дереве), она не гарантирует. Такой код может выдать «разрешено». Это
   ограничивают пины и хеши административного дерева (решение 3), серверный `sha` и проверка
   фактов доставки по спеке A, которая окончательно устанавливает действительность акта.
2. **Периметр и журнал — у издателя** (следствие требования 1). Для candidate и brief
   издатель сам вычисляет состояние корня тем же кодом, что `perimeter-status` (§5.2).
   При `blocked` или `unavailable` он отказывает до вызова проверяющего. Состояние уходит
   проверяющему полем запроса `perimeter`. В проверяющем `act_trust.Journal` — адаптер
   только для чтения над этим полем. Нарушения, которые проверяющий обнаружил
   (самоотчёт установок, форма истории), возвращаются в вердикте списком `violations`.
   Издатель регистрирует их своей логикой `record-violation`, источник обнаружения —
   `precheck`, и отказывает. Файлов журнала проверяющий не читает. Установки App издатель
   проверяет сам, как в 2a (решение 10 плана 2a), поэтому `PremergeDeps.installations` у
   проверяющего — `None`.
3. **Состав административного дерева.** `/opt/df/admin/<id>/` содержит:
   - `df/`, как раньше;
   - `governance/` — ровно модули замыкания импорта `governance/act_precheck.py`. Список
     хранится константой `df/admin_tree.py:GOVERNANCE_MODULES`, тест сверяет его с
     AST-замыканием;
   - `contracts/` — ровно файлы, которые эти модули читают относительно `__file__`
     (`contracts/approval-branches/v1/patterns.env` и то, что найдёт тест Task 4);
   - `site/` — только чистый Python: пакет `yaml/` из колеса pyyaml (версия и sha256 колеса
     из `uv.lock`, нативный `_yaml*.so` не копируется) и пакет `steward/` из `src/steward`
     коммита, закреплённого в `uv.lock` (`68f46a9…`). Root забирает этот коммит git'ом
     в `admin-home`: `git init` + `fetch <url> <sha>`, затем сверяет, что `FETCH_HEAD` равен
     пину.

   Замер 03.11: при импорте `act_premerge`, `act_status`, `act_envelope`,
   `steward.gatecheck.approval` и `steward.approvalfacts.v3` сторонние модули — только
   `steward` и `yaml`. В `governance/` `CSafeLoader` нигде не используется, поэтому
   достаточно чистого pyyaml. Сборки колёс нет, бэкенд сборки не исполняется,
   `uv pip install` не используется. Проверка дерева (`admin_code_problems`) дополнительно
   отвергает в `admin/<id>` любой `*.so`, `*.dylib`, `*.pyd` и `*.pth`. `admin_digest`
   покрывает всё дерево.
4. **Вход проверяющего — существующий вход команды** (как решение 3 плана 2a). Обёртка
   `/opt/df/libexec/df-precheck` запускает `<административный Python> -I -B -S
   /opt/df/bin/darkfactory-entry.py precheck serve`. Вход сверяет `admin/<id>` по
   `admin_digest` до импорта. Подкоманда `precheck serve` добавляет `admin/<id>/site` в
   `sys.path` после `admin/<id>` и импортирует `governance.act_precheck`. Замороженный договор
   входа не меняется.
5. **Новая роль и одно правило sudoers.** `df-precheck` входит в `platform.SERVICE_USERS`
   и в `roles.ROLE_USERS`. Домашний каталог `0700`, оболочка nologin, без
   административных групп, без секретов. Правило:
   `df-publisher ALL=(df-precheck) NOPASSWD: NOSETENV: /opt/df/libexec/df-precheck ""`
   (`""` запрещает аргументы). У `df-precheck` своих правил нет (`_NO_RULES`).
   `!log_output` действует и здесь: в запросе есть токен чтения.
6. **Вид stop — шаблон контракта** (решение владельца 03.11).
   `contracts/approval-branches/v1/patterns.env` получает
   `APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}`. Производитель
   (`act_node._stop_pr`) и проверяющий (`approval_branches`, издатель) берут правило оттуда.
   Это путь обвязки и authority-root, мерж владельца.
7. **Вид brief в 2b отказывает** `refused: brief_precheck_unavailable`. Предпроверка
   брифа — часть C (Task 17 плана approval-act), её ещё нет.
8. **Способ мержа — `squash`**, константа издателя. Rebase-мерж запрещён спекой A, а
   линейная история ruleset'а merge-коммит отвергает. Отказ GitHub (squash выключен в
   продукте) — `refused: <причина GitHub>`.
9. **Аттестация finalize и stop** (§5.4): после положительного вердикта издатель публикует
   ревью App `APPROVE` с `commit_id = head_sha` и маркером
   `<!-- df-publisher:v1 attestation=<finalize|stop> request_key=<key> head_sha=<sha> -->`.
   Потом пишет намерение мержа и мержит. Повтор после сбоя сначала ищет свою аттестацию по
   маркеру (как `reconcile_review`), второй не публикует.
10. **Сверка мержа** (§5.6). Намерение `{"request_key","head_sha","kind","intent_at"}`
    записывается до `PUT`. После таймаута, сбоя транспорта или 5xx издатель читает
    `GET /pulls/{n}`. Результат `done` с `merge_commit`, если `merged` истинно,
    `merged_by.login == <slug>[bot]`, `merged_by.type == "Bot"`, `head.sha == head_sha` и
    `merged_at >= intent_at`. Иначе `unknown: merge_not_confirmed`. Слепого повтора нет.
    Если PR смёржен кем-то другим или с другой головой, результатом запроса он не
    объявляется.
11. **История доставок у проверяющего** читается из временного снимка git, а не через
    API. Проверяющий делает `git init --bare` во временном каталоге своего HOME и
    `fetch --no-tags --filter=blob:none origin <base_tip> <head_sha>`. Заголовок
    авторизации передаётся через `--config-env`, токена нет ни в argv, ни в конфиге.
    Дальше работает класс `act_precheck.SnapshotGit` с методами `first_parent_changes`,
    `show_file`, `show_file_bytes`, `commit_files`. Формат вывода тот же, что у
    `governance/ops.py:_FIRST_PARENT_LOG` / `_parse_first_parent`, переиспользуется
    импортом. После проверки снимок удаляется.
12. **Живая приёмка 2b — маршрут stop** (решение владельца 03.11). Строки §4.5 «смена head»,
    «идентичный повтор», «сверка после таймаута» исполняются на stop-PR стенда. Стенд
    засевается доставленной заявкой `review/v1` поколения 1 (коммит в `master`). Живые
    candidate и finalize **не закрыты** до их приёмки: candidate требует привязки
    `df-trust` и стендов политики (этап 3), finalize — акта на candidate. Evidence и §10 их
    учитывают отдельно, без заявления «все виды мержа проверены».
13. **Стенд заводится заново** (TODO `df-stage2a-stand-secrets-revoke`). Шаги человека
    H5–H8 (ниже). Репозиторий, App и environment те же, что в 2a: `df-roles-probe-private`,
    App 5285716, `df-stage2-live`. Machine user — `betta-tester` с новым classic PAT (R15-2).
    Ruleset'а на стенде нет (R15-1), поэтому живой мерж без одобрения GitHub пропустил бы.
    Аттестацию строки проверяют по факту ревью, а не по отказу ruleset.
14. **Хвосты части B для App-режима `merge-pr.sh`** (TODO `df-installer-stage2b`, п. 9a/9c/9d)
    — Task 9:
    - 9c: путь ключа не должен проходить через символические ссылки, ни сам файл, ни
      каталоги после `apps/`;
    - 9d: токен установки отзывается после `PUT` (`DELETE /installation/token`) при любом
      исходе;
    - 9a остаётся как есть, с ruling в TODO: факты PR и стоп-кран от логина не зависят,
      сбой доступа даёт отказ.

## Global Constraints

- Пакет `df/` — только stdlib (`tests/df/test_launcher_entry.py:340`, шаблон проверки
  `names - {"__future__"} <= set(sys.stdlib_module_names)`). Это касается и
  `df/publisher/precheck.py`.
- `governance/act_precheck.py` импортирует только `governance/act_*`, `governance/facts`,
  `governance/ssot_env`, `governance/ops` (только `_FIRST_PARENT_LOG`, `_parse_first_parent`,
  `FirstParentRows`), `steward`, `yaml` и stdlib. Импорт `df` запрещён.
- Код не читает `_cowork_output/`. Разговор по-русски, код и имена файлов по-английски.
- Тесты не трогают `~/.claude`, `~/.codex`, `~/.config/gh` и настоящих credentials. Сеть в
  тестах — только локальный фейк (`tests/df/fake_github.py`).
- Тесты запускаются на переднем плане:
  `perl -e 'alarm 590; exec @ARGV' -- uv run --group governance pytest <path> -q
  -p no:cacheprovider`.
- Все файлы плана, кроме `governance/edge_check/publish.py` и
  `tests/test_edge_check_*.py`, — authority-root (`df/`, `tests/df/`, `governance/act_`,
  `contracts/approval-branches/`, `merge-pr.sh`, `.github/`). PR этапа сливает **владелец**.
- Пределы: запрос к проверяющему ≤ 64 КиБ, вердикт ≤ 64 КиБ, `PRECHECK_TIMEOUT = 180.0` с
  (две секунды на границу акта + снимок git), `COMPARE_FILE_CAP = 300` (без изменений).
- Строки ответа — схема `df/publisher-response/v1` без новых верхних ключей. Новые причины
  — только в `reason`.

## Review Focus

1. **Проверяющий завис или ответил чужим вердиктом.** Ожидание: отказ
   `precheck_failed: timeout` / `binding_mismatch`, процесс убит, мержа нет. Тест — Task 7,
   `test_precheck_timeout_kills_and_refuses`, `test_precheck_verdict_for_other_head_refused`.
2. **Голова сменилась между вердиктом и мержем.** Ожидание: GitHub отвечает 409 на `sha`,
   исход `refused: head_changed`, намерение остаётся, повтор того же запроса даёт
   `refused` через сверку, а не второй `PUT`. Тест — Task 8, `test_head_change_after_precheck`.
3. **Ответ на `PUT` потерян, а PR смёржен.** Ожидание: сверка находит мерж ботом с этой
   головой после `intent_at` и возвращает `done`. Смёржен человеком — `unknown`. Тест —
   Task 8, `test_lost_put_reconciles_done` / `test_merged_by_human_is_unknown`.
4. **Посторонний файл в раннем коммите candidate при чистом последнем evidence-коммите.**
   Ожидание: отказ D16 по полному составу PR. Тест — Task 2, `test_foreign_file_in_early_commit`.
5. **Stop на устаревшее поколение или с расхождением пути и содержимого.** Ожидание: отказ.
   Тест — Task 4, `test_stop_for_superseded_generation_refused`,
   `test_stop_path_content_mismatch_refused`.

---

## Файлы

| Файл | Роль | Task |
|---|---|---|
| `contracts/approval-branches/v1/patterns.env` | `APPROVAL_STOP_TEMPLATE` | 1 |
| `governance/approval_branches.py`, `governance/act_node.py` | `stop_branch(ws, node, gen)` из контракта | 1 |
| `governance/act_surface.py` (новый) | общая функция поверхности D16 по набору путей | 2 |
| `governance/edge_check/publish.py` | `surface_violations` через `act_surface` | 2 |
| `governance/act_forge_http.py` (новый) | `Transport` steward поверх HTTPS с токеном | 3 |
| `governance/act_precheck.py` (новый) | программа проверяющего: схемы, виды, снимок git | 4 |
| `df/admin_tree.py` (новый), `df/admin.py` | состав и установка административного дерева, `site/` | 5 |
| `df/roles*.py`, `df/sudoers.py`, `df/libexec.py`, `df/platform.py`, `df/cli.py`, `df/doctor.py` | роль `df-precheck`, правило, обёртка, `precheck serve` | 6 |
| `df/publisher/precheck.py` (новый) | клиент издателя: токен, вызов, связывание вердикта | 7 |
| `df/publisher/merge.py`, `df/publisher/ops.py` | stop, аттестация, намерение, `PUT`, сверка, evidence | 8 |
| `merge-pr.sh`, `governance/act_app_token.py` | хвосты 9c/9d | 9 |
| `tests/df/test_acceptance_root_roles.py`, `.github/workflows/df-stage1.yml` | root-строки изоляции `df-precheck` | 10 |
| `tests/df/live_stand.py`, `.github/workflows/df-stage2-live.yml` | живые строки 10–12 | 11 |
| `docs/evidence/2026-11-XX-df-stage2b-acceptance.md`, `TODO.md` | evidence и учёт | 12 |

---

### Task 1: шаблон ветки stop в контракте

**Files:**
- Modify: `contracts/approval-branches/v1/patterns.env` (после строки 42)
- Modify: `governance/approval_branches.py`, `governance/act_node.py:1062-1071`
- Modify: `df/publisher/merge.py:141-154` (распознавание ветки stop)
- Test: `tests/test_approval_branches.py`, `tests/test_act_node.py` (или файл, где тест
  `_stop_pr`), `tests/df/test_publisher_merge.py`

**Interfaces:**
- Produces: строка `APPROVAL_STOP_TEMPLATE=stop/{ws_id}-{node}-g{generation}`;
  `approval_branches.stop_branch(ws_id: str, node: str, generation: int) -> str`;
  `approval_branches.parse_stop_branch(ref: str) -> tuple[str, str, int] | None`;
  в `df/publisher/merge.py` — `_STOP_KEY = "APPROVAL_STOP_TEMPLATE"` и `"stop"` в
  `branch_kinds`.

- [ ] **Step 1: Тесты.** `test_stop_branch_renders_template`:
  `stop_branch("ws1", "design", 3) == "stop/ws1-design-g3"`.
  `test_parse_stop_branch_roundtrip` для пар (ws, node) с дефисами в `node`:
  `parse_stop_branch("stop/ws1-acc-test-g12")` даёт кортеж, если `ws_id` берётся из
  известного `ws` вызова. Если разбор двусмысленный, функция принимает `ws_id` аргументом:
  `parse_stop_branch(ref, ws_id) -> tuple[str, int] | None`. Выберите вторую форму, если
  двусмысленность подтвердится на `ws_id` с дефисом. Тест с `ws_id="a-b"` и
  `node="c"` обязателен. `test_act_node_stop_pr_uses_contract`: ветка `_stop_pr` равна
  `stop_branch(...)`. В `df`: `test_stop_branch_routes_stop` — ветка `stop/ws-n-g2` плюс один
  путь `workstreams/ws/approvals/stops/n-g2.yaml` дают `route(...) == "stop"`;
  `test_stop_glob_equals_the_contract` по образцу `test_glob_equals_the_contract` (`:114`).
- [ ] **Step 2:** прогнать тесты, убедиться, что они падают.
- [ ] **Step 3:** добавить строку в `patterns.env` с комментарием «stop-PR — запись
  остановки поколения (спека A §9.1); мержит агент (D5) или издатель (спека B §5.4)».
  `approval_branches.py` и `approval_branches.sh` читают ключ тем же способом, что
  `APPROVAL_CANDIDATE_TEMPLATE`. В `route()` (`merge.py:167-185`) stop требует ровно один
  путь `STOP_PATH`, а `node` и `generation` пути совпадают с веткой. Иначе
  `ambiguous_kind`.
- [ ] **Step 4:** прогнать `tests/test_approval_branches.py tests/df/test_publisher_merge.py`
  и тесты `act_node` — PASS. Отдельно `tests/test_harness_trust.py`: путь обвязки изменён,
  проверка хешей обвязки должна быть согласована.
- [ ] **Step 5:** коммит `feat(approval-branches): stop branch template in the contract`.

### Task 2: общая поверхность D16 по полному составу PR

**Files:**
- Create: `governance/act_surface.py`
- Modify: `governance/edge_check/publish.py:61-95`
- Test: `tests/test_act_surface.py`, `tests/test_act_wave_runner.py` (регрессия I-1)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Surface:
      ws_id: str
      bundle_dir: str          # "workstreams/<ws>/bundle" relative to the repo root
      nodes: tuple[str, ...]
      dag: tuple[tuple[str, tuple[str, ...]], ...]
      manifest: str | None     # manifest path of the request (review/v1) or None
  def allowed(surface: Surface, path: str) -> bool
  def surface_refusal(surface: Surface, paths: Iterable[str]) -> str | None
  ```
  `surface_refusal` возвращает первую причину вида `"вне поверхности D16: <path>"` или
  `None`. Разрешено: `manifest`; файлы `nodes` и их `_downstream_closure` через
  `bundle_dag.node_filenames(dag)` под `bundle_dir`; префикс
  `<parent(bundle_dir)>/evidence/edge-check/` только для `*.json`; префикс
  `<bundle_dir>/00-discovery/`, если `"charter"` в `nodes` (W1).

- [ ] **Step 1: Тесты** (`tests/test_act_surface.py`):
  `test_real_candidate_after_edge_check_allowed` (узлы, downstream, evidence, манифест);
  `test_w1_discovery_allowed_only_with_charter`; `test_stale_downstream_allowed`;
  `test_foreign_file_refused`; `test_evidence_of_other_ws_refused`;
  `test_foreign_file_in_early_commit` — набор путей всего PR (дифф к базе) содержит
  посторонний файл, последний коммит чистый → отказ; `test_manifest_of_other_request_refused`.
  Регрессия: существующие тесты I-1 в `tests/test_act_wave_runner.py` зелёные без правок.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать `act_surface`. `surface_violations` строит `Surface` из
  `state`, `op`, `key` и вызывает `allowed` для путей коммита. Сигнатура и поведение не
  меняются: одна логика, два вызова (коммит у конвейера, PR у проверяющего).
- [ ] **Step 4:** PASS для обоих файлов и `tests/test_edge_check_*`.
- [ ] **Step 5:** коммит `refactor(act): shared D16 surface over a path set`.

### Task 3: транспорт steward поверх HTTPS с токеном

**Files:**
- Create: `governance/act_forge_http.py`
- Test: `tests/test_act_forge_http.py`

**Interfaces:**
- Consumes: `steward.approvalfacts.v3.transport.Transport`, `Request`, `Response`.
- Produces: `class TokenTransport: def __init__(self, token: str, *, api_base: str =
  "https://api.github.com", opener=None, timeout: float = 60.0)`;
  `def send(self, request: Request) -> Response`.
  GET и GraphQL-query — как у `GhTransport`. Opener:
  `build_opener(ProxyHandler({}), _NoRedirect)` (образец `act_anon`), тело читается
  до `act_anon.MAX_BODY + 1` с отказом при превышении. Заголовок
  `Authorization: Bearer <token>`. Сбой → `Response(status=None, error=<без токена>)`.

- [ ] **Step 1: Тесты:** `test_get_sends_bearer_and_parses_link`,
  `test_graphql_query_posts_json`, `test_mutation_refused` (не query → `Response(None)` без
  запроса), `test_redirect_not_followed`, `test_body_over_limit_is_failure`,
  `test_env_proxy_ignored` (`https_proxy` в окружении не используется),
  `test_token_absent_from_error_text`. Источник формы запроса — `Request` steward и его
  `ReplayTransport`. Новых форм не выдумывать: свериться со снимками steward.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** коммит `feat(act): token HTTPS transport for approval facts`.

### Task 4: программа проверяющего `governance/act_precheck.py`

**Files:**
- Create: `governance/act_precheck.py`
- Modify (минимально, только если нужно для инъекции): `governance/act_premerge.py`
- Test: `tests/test_act_precheck.py`

**Interfaces:**
- Consumes: `act_surface.Surface/surface_refusal` (Task 2), `act_forge_http.TokenTransport`
  (Task 3), `act_premerge.PremergeDeps`, `act_premerge` для candidate, `act_envelope`
  (`stop_path`, `parse_stop`, `node_history`, `expected_envelope`, `envelope_path`,
  `render_envelope`), `act_status.load_history`, `act_request.parse_manifest/manifest_path`,
  `approval_branches.parse_stop_branch` (Task 1).
- Produces (тот же договор читает Task 7):
  - запрос `df/precheck-request/v1`, JSON, строгие ключи:
    `schema, nonce, kind, repository_id, full_name, pr, head_sha, head_ref, base_ref,
    base_tip, paths, token, perimeter, api_base`. `kind ∈ {candidate, finalize, stop}`,
    `paths` — отсортированный список полного состава PR, `perimeter` —
    `{"state": "clear", "snapshot_sha": str, "trust_root": {...}} | null` (null для
    finalize и stop);
  - вердикт `df/precheck-verdict/v1`:
    `schema, nonce, request_digest, repository_id, pr, head_sha, kind, outcome, reason,
    violations, version`. `outcome ∈ {allow, refuse, unset}`,
    `request_digest = sha256(canonical(request без token))`, `version` —
    `PRECHECK_VERSION = "df-precheck/1"` и `admin_digest` активного релиза (Task 6 передаёт
    его через `df.cli`);
  - `def serve(stdin: BinaryIO, stdout: TextIO, *, admin_digest: str,
    deps: PrecheckDeps | None = None) -> int`
    — код 0 при любом вердикте, 2 при неразбираемом запросе (вердикта нет);
  - `class SnapshotGit` (решение 11): методы `first_parent_changes(target_dir, ref)`,
    `show_file`, `show_file_bytes`, `commit_files` в форме `governance/ops.Ops`;
  - `@dataclass(frozen=True) class PrecheckDeps`: `transport_for: Callable[[str, str],
    Transport]`, `snapshot: Callable[[Request], SnapshotGit]`, `binding`, `now`, `sleep`.

Логика по видам:
- **candidate.** Манифест — единственный изменённый PR файл под `.../approvals/requests/`
  (`act_request.manifest_path`). Он должен быть в `paths` с `ws` и ключом, совпадающими с
  веткой (`act_premerge.request_of_branch(head_ref)`). DAG и узлы — из манифеста и
  `bundle_dag` на `head_sha` снимка. Затем `surface_refusal(Surface(...), paths)`. Затем
  ядро `act_premerge._check` с `PremergeDeps`: `gh = TokenTransport(token)`, `binding` из
  `/etc/df/trust.toml` (`act_trust.load_binding`; без него — `refuse`
  `"заявка review/v1 без корня доверия"`, как сейчас), `journal = RequestJournal(perimeter)`,
  `creds = ()`, `installations = None`, `merger = <slug>[bot]`, `merger_type = "Bot"`.
  Если `_check` не вызывается без argparse, выделить в `act_premerge` функцию
  `check(fields: PremergeFields, deps: PremergeDeps) -> tuple[int, str]`; CLI вызывает её же.
  Коды 0/2/3 переходят в `allow`/`unset`/`refuse`.
- **finalize.** Доставленная заявка — по ветке кандидата (без суффикса) в истории
  `base_tip` (`load_history` над `SnapshotGit`). `review_id` акта —
  `act_facts.open_review`/`find_act` на смёрженном candidate-PR через `TokenTransport`.
  Ожидаемые конверты — `expected_envelope` по узлам заявки. `paths` должен в точности
  совпасть с `envelope_path` узлов, содержимое на `head_sha` — с `render_envelope` (форма
  `envelope_commit_defect`, но по полному составу PR).
- **stop.** `paths == [stop_path(ws, node, gen)]`, ветка — `stop_branch(ws, node, gen)`.
  `parse_stop(content@head)` даёт `StopRecord(node, gen, ...)`, совпадающий с путём.
  `node_history(load_history(base_tip), node)`: `current` — `DeliveredRequest` поколения
  `gen` (спека A §9.1). Остановка уже действующего поколения или устаревшего — `refuse`.

- [ ] **Step 1: Тесты** (фейковый транспорт `ReplayTransport` steward и настоящий
  `SnapshotGit` над временным репо из `git` в `tmp_path`):
  - candidate: `test_candidate_allowed_with_act_on_final_head`,
    `test_approve_before_evidence_commit_not_counted` (D1),
    `test_foreign_file_in_pr_refused`, `test_no_binding_refused`,
    `test_violation_returned_not_recorded` (самоотчёт истории → `violations` не пуст,
    `outcome = refuse`, файлов журнала нет);
  - finalize: `test_finalize_exact_envelopes_allowed`,
    `test_finalize_extra_path_refused`, `test_finalize_content_mismatch_refused`,
    `test_finalize_without_delivered_candidate_refused`;
  - stop: `test_stop_current_generation_allowed`,
    `test_stop_for_superseded_generation_refused`, `test_stop_path_content_mismatch_refused`,
    `test_stop_branch_mismatch_refused`;
  - протокол: `test_unknown_request_key_exit_2`, `test_kind_brief_is_not_accepted` (схема
    отвергает), `test_verdict_binds_request` (nonce, digest, pr, head_sha, kind),
    `test_token_not_in_verdict_or_stderr`, `test_snapshot_removed_after_run`,
    `test_snapshot_git_argv_has_no_token` (наблюдатель argv над подменённым `git`);
  - замыкание: `test_import_closure_is_declared` — AST-замыкание `act_precheck` равно
    `df.admin_tree.GOVERNANCE_MODULES` (Task 5 добавит константу; до неё тест в этом файле
    сверяет с явным списком и переносится в Task 5).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS, плюс
  `tests/test_act_premerge.py`: CLI ведёт себя как раньше.
- [ ] **Step 5:** коммит `feat(act): precheck program for the df publisher`.

### Task 5: административное дерево с `governance/`, `contracts/` и `site/`

**Files:**
- Create: `df/admin_tree.py`
- Modify: `df/admin.py` (`install_admin`, `admin_code_problems`, `expected_admin_digest`)
- Test: `tests/df/test_admin_tree.py`, `tests/df/test_admin.py`

**Interfaces:**
- Produces: `GOVERNANCE_MODULES: tuple[str, ...]` (имена файлов под `governance/`),
  `CONTRACT_FILES: tuple[str, ...]`, `SITE_PACKAGES = ("yaml", "steward")`,
  `FORBIDDEN_SUFFIXES = (".so", ".dylib", ".pyd", ".pth")`;
  `def locked_pins(uv_lock: bytes) -> SitePins` (pyyaml: версия, имя и sha256 чистого колеса
  `py3-none-any` или sdist; steward: url и коммит); `def install_site(layout, release_id, *,
  devtools_ref: Path, pins: SitePins, fetch: Fetcher) -> None`.
  Если в `uv.lock` нет чистого колеса pyyaml, `yaml/` берётся из **sdist** по sha256 из
  `uv.lock`: каталог `lib/yaml` архива без сборки. Это решение Step 1 по замеру. Выдумывать
  колесо нельзя.

- [ ] **Step 1: Замер** (в отчёт задачи): какие артефакты pyyaml есть в `uv.lock`, и какое
  дерево `src/steward` у закреплённого коммита.
- [ ] **Step 2: Тесты:** `test_governance_modules_equal_import_closure` (перенос из Task 4);
  `test_contract_files_cover_file_relative_reads`; `test_install_admin_copies_exact_tree`
  (`df/`, только перечисленные `governance/*`, `contracts/*`, `site/yaml`, `site/steward`,
  больше ничего); `test_native_extension_refused` (`.so` в `site` → `admin_code_problems`);
  `test_pth_refused`; `test_digest_covers_site` (правка байта в `site/yaml` меняет digest и
  ломает `admin_ready`); `test_pin_mismatch_refused` (sha256 артефакта или `FETCH_HEAD` не
  равны пину → `StepFailed("admin")`, дерева нет); `test_site_imports_under_dash_S`
  (административный Python `-I -B -S` с `sys.path = [admin/<id>, admin/<id>/site]`
  импортирует `governance.act_precheck`).
- [ ] **Step 3:** FAIL. **Step 4:** реализовать. Скачивание и `git fetch` идут от root в
  `admin-home` с `admin_env`, без `uv`. Протокол прежний: staging → `sync_tree` → `rename`.
  `expected_admin_digest` строит то же дерево.
- [ ] **Step 5:** PASS, плюс root-тест установки `tests/df/test_acceptance_root*.py` для
  activate (локально пропускается, в CI — job stage1).
- [ ] **Step 6:** коммит `feat(df): admin tree carries the precheck closure and pure site`.

### Task 6: роль `df-precheck`, правило, обёртка, `precheck serve`

**Files:**
- Modify: `df/platform.py` (`SERVICE_USERS`), `df/roles.py`, `df/roles_setup.py`,
  `df/sudoers.py` (`_rules`, `_NO_RULES`), `df/libexec.py` (`_SUBCOMMANDS`), `df/cli.py`
  (`precheck serve`), `df/doctor.py` (проверки роли)
- Test: `tests/df/test_roles_setup.py`, `tests/df/test_sudoers.py`,
  `tests/df/test_libexec.py`, `tests/df/test_cli.py`, `tests/df/test_doctor*.py`

**Interfaces:**
- Produces: обёртка `/opt/df/libexec/df-precheck` (вход `precheck serve`);
  правило `("df-publisher", "df-precheck", ((f'{precheck} ""',),))`; в `df.cli` —
  подкоманда `precheck serve`. Она проверяет `getuid() == uid(df-precheck)`, ставит
  `sys.path` (решение 4), импортирует `governance.act_precheck` и вызывает
  `serve(sys.stdin.buffer, sys.stdout, admin_digest=<admin_digest записи current>)`.

- [ ] **Step 1: Тесты:** `test_render_has_precheck_rule_without_args`;
  `test_precheck_has_no_rules`; `test_roles_setup_creates_precheck_role` (nologin, `0700`,
  не в `ADMIN_GROUPS`, не в группах `df-publisher`); `test_wrapper_text_precheck`;
  `test_precheck_serve_refuses_other_uid`; `test_doctor_reports_precheck_role`.
  Повторный `roles setup` на установке 2a добавляет роль, правило и обёртку, текст sudoers
  сверяется с ожидаемым.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** коммит `feat(df): df-precheck role, sudoers rule and wrapper`.

### Task 7: клиент издателя `df/publisher/precheck.py`

**Files:**
- Create: `df/publisher/precheck.py`
- Modify: `df/publisher/merge.py` (`Precheck` получает факты PR)
- Test: `tests/df/test_publisher_precheck.py`

**Interfaces:**
- Consumes: `ops.authorize(oc, ops.READ_PERMISSIONS)`, `platform.run_with_input`, договор
  запроса и вердикта Task 4.
- Produces:
  ```python
  @dataclass(frozen=True)
  class PrFacts:
      head_ref: str
      base_ref: str
      base_tip: str
      paths: tuple[str, ...]
  class Precheck(Protocol):
      def __call__(self, kind: Kind, ctx: OpContext, pr: Mapping[str, object],
                   facts: PrFacts, perimeter: Mapping[str, object] | None) -> Refused | None: ...
  def admin_precheck(kind, ctx, pr, facts, perimeter) -> Refused | None
  PRECHECK_TIMEOUT = 180.0
  PRECHECK_COMMAND = ("/usr/bin/sudo", "-n", "-u", "df-precheck", "<layout.opt>/libexec/df-precheck")
  ```
  `admin_precheck`: токен чтения (один продукт, `READ_PERMISSIONS`) → запрос с
  `nonce = secrets.token_hex(16)` → запуск с окружением `{"PATH": "/usr/bin:/bin", "LANG": "C"}`,
  `cwd="/"`, `close_fds=True`, stdin = запрос, предел вывода 64 КиБ, таймаут → строгий
  разбор вердикта и сверка связи (требование 3) → после вызова отзыв токена чтения
  (`DELETE /installation/token`, ошибка отзыва в evidence, не в исход). `unset` →
  `refused: precheck_unset: <reason>`. `refuse` → `refused: <reason>` (+ регистрация
  `violations`, решение 2).

- [ ] **Step 1: Тесты** (подменённая команда — исполняемый скрипт в `tmp_path`, отвечающий
  заданным вердиктом): `test_allow_bound_verdict_passes`;
  `test_precheck_verdict_for_other_head_refused`; `test_nonce_mismatch_refused`;
  `test_digest_mismatch_refused`; `test_extra_key_refused`;
  `test_precheck_timeout_kills_and_refuses` (процесс-группа убита, исход
  `precheck_failed: timeout`); `test_oversized_output_refused`; `test_nonzero_exit_refused`;
  `test_read_token_only` (запрос токена несёт ровно `READ_PERMISSIONS` и один
  `repository_id`); `test_token_revoked_after_call`; `test_no_write_token_passed`;
  `test_violations_registered_then_refused`; `test_df_has_no_governance_import` (stdlib-only
  проверка файла).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** коммит `feat(publisher): precheck client bound to the request`.

### Task 8: исполнение `merge`: stop, аттестация, намерение, `PUT`, сверка, evidence

**Files:**
- Modify: `df/publisher/merge.py` (`_decide`, `merge`, `merge_op`), `df/publisher/ops.py`
  (аттестация, сверка мержа)
- Test: `tests/df/test_publisher_merge.py` (пересмотреть `test_no_mutating_calls`)

**Interfaces:**
- Consumes: `admin_precheck` (Task 7), `perimeter` (вычисление состояния корня — та же
  функция, что у `perimeter_status_op`; выделить `perimeter.root_state(ctx) -> Mapping`),
  `ops._auth`, `ops._is_bot`, `ctx.record_intent`.
- Produces: `MERGE_METHOD = "squash"`; `ATTESTATION_MARKER`;
  `def reconcile_merge(oc, token, pr, intent) -> Response`; `PRECHECK_VERSION` в evidence.
  Evidence `merge`: `kind`, `precheck` (`version`, `admin_digest`), `merge_commit` |
  `review_id` (аттестации), `trust_root` (для candidate — из `perimeter`, иначе
  `{"state": "not_applicable"}`).

Порядок `_decide` (после шагов 1–10 2a):
1. `kind == "brief"` → `refused: brief_precheck_unavailable` (решение 7).
2. candidate: `perimeter.root_state` → `blocked` → `refused: perimeter_blocked`,
   `unavailable:<r>` → `refused: perimeter_unavailable: <r>`.
3. `admin_precheck(kind, oc, pr, facts, perimeter)`.
4. Если `ctx.intent` есть → `reconcile_merge` (никаких новых действий до сверки).
5. finalize и stop: найти свою аттестацию по маркеру; если её нет — опубликовать
   (решение 9).
6. Третье чтение PR: голова всё ещё `head_sha`, иначе `refused: head_changed`.
7. `record_intent({"request_key","head_sha","kind","intent_at"})`.
8. `PUT /repos/{full}/pulls/{n}/merge` `{"merge_method": "squash", "sha": head_sha}` токеном
   `{"contents": "write", "pull_requests": "write"}`. 200 + `merged` → `done` с
   `merge_commit`. 405/409 → сверка, затем `refused: head_changed` или
   `refused: not_mergeable: <msg>`, с `verified_not_done=True`, только если сверка
   подтвердила, что PR не смёржен. Таймаут, 5xx, транспорт → `reconcile_merge`.

- [ ] **Step 1: Тесты** (`MergeGitHub` с обработчиком `PUT`):
  `test_stop_merged_with_server_sha` (тело `PUT` ровно `{"merge_method":"squash","sha":…}`);
  `test_finalize_attested_then_merged` (ревью `APPROVE`, `commit_id`, маркер, затем `PUT`);
  `test_retry_finds_own_attestation` (второй прогон после падения между ревью и намерением
  второго ревью не публикует); `test_head_change_after_precheck` (409 → `refused:
  head_changed`, повтор → тот же исход без второго `PUT`); `test_identical_retry_replays`
  (`replayed: True`, `PUT` один); `test_lost_put_reconciles_done`;
  `test_merged_by_human_is_unknown`; `test_merged_before_intent_is_unknown`;
  `test_merged_other_head_is_unknown`; `test_candidate_blocked_perimeter_refused_before_precheck`
  (проверяющий не вызван); `test_brief_refused`; `test_evidence_has_kind_precheck_trust_root`;
  `test_precheck_refusal_writes_no_intent`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS всего `tests/df/`.
- [ ] **Step 5:** коммит `feat(publisher): merge with attestation, intent and reconcile`.

### Task 9: хвосты App-режима `merge-pr.sh` (9c, 9d)

**Files:**
- Modify: `merge-pr.sh:310-325` (ключ), `merge-pr.sh:494-543` (отзыв токена),
  `governance/act_app_token.py` (`revoke(token)`)
- Test: `tests/test_merge_pr.py`, `tests/test_act_app_token.py`

**Interfaces:**
- Produces: `act_app_token.py --revoke` читает токен из stdin и делает
  `DELETE /installation/token`, код 0/2. Путь ключа: каждый компонент после
  `${XDG_CONFIG_HOME:-$HOME/.config}/darkfactory/apps/` и сам файл — не ссылка (`[ -L ]`
  по цепочке), иначе код 3.

- [ ] **Step 1: Тесты:** `test_app_key_symlink_refused` (файл-ссылка и каталог-ссылка внутри
  `apps/`); `test_app_token_revoked_after_merge` (при успехе, при отказе `PUT`, при
  `die`); `test_revoke_reads_token_from_stdin_only`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать (`trap` на выход после выдачи токена).
  **Step 4:** PASS `tests/test_merge_pr.py tests/test_act_*`.
- [ ] **Step 5:** коммит `fix(merge-pr): app key without links, revoke installation token`.
  TODO: закрыть 9c/9d, а для 9a записать ruling.

### Task 10: root-строки изоляции `df-precheck`

**Files:**
- Modify: `tests/df/test_acceptance_root_roles.py`, `tests/df/roles_world.py`
  (`FIXTURE_FROM_CHECKOUT` дополнить деревом Task 5), `.github/workflows/df-stage1.yml` (job
  `stage2a` → без переименования, новые строки)
- Test: тот же файл (в CI на обеих ОС)

Строки (через настоящий `sudo` и обёртки):

| Попытка | Ожидание |
|---|---|
| `df-precheck` читает `/etc/df/secrets/app-<N>.pem`, `/var/lib/df/publisher/*`, `/var/lib/df/perimeter/*`, `~df-exec/.config/df/github-token` | отказ ОС |
| `df-exec`, `df-review`, человек вызывают `df-precheck` | отказ sudoers |
| `df-publisher` вызывает `df-precheck` с аргументом | отказ sudoers |
| самоотчёт `df-precheck` (вход `precheck serve` с запросом-самоотчётом теста) | UID роли, окружение ровно `PATH`, `LANG`, дескрипторы только 0–2 |
| `df-precheck` пишет в `admin/<id>` | отказ ОС |

- [ ] **Step 1:** тесты строк (`test_precheck_cannot_read_publisher_secrets`, …). Для
  самоотчёта ввести в `act_precheck` тестовый вид запроса? **Нет**: самоотчёт снимает
  наблюдатель процесса (`/proc/<pid>` на Linux, `lsof -p` на macOS) во время вызова с
  зависшим транспортом (`api_base` → локальный порт, который не отвечает). Так в коде
  проверяющего не появляется вида «только для тестов».
- [ ] **Step 2:** локально пропуск (не root), в CI — job `stage2a`.
- [ ] **Step 3:** коммит `test(df): root rows for df-precheck isolation`.

### Task 11: живые строки мержа на стенде

**Files:**
- Modify: `tests/df/live_stand.py` (строки 10–12, засев, уборка), `tests/df/test_live_stand.py`,
  `.github/workflows/df-stage2-live.yml`

Строки:
- **10 «засев и stop-PR»:** машинный пользователь коммитит в `master` стенда манифест
  `review/v1` для `ws = stand-<tag>`, узел `n1`, поколение 1 (доставленная заявка). Затем
  создаёт ветку `stop_branch("stand-<tag>", "n1", 1)` с одним файлом
  `stop_path(...)` (`render_stop`) и PR.
- **11 «смена head»:** запрос `merge` идёт через `gate_proxy`, который держит `PUT` (образец
  строки 4б). Пока `PUT` удержан, машинный пользователь пушит в ветку второй коммит. Затем
  прокси отпускает `PUT`. Ожидание: `refused: head_changed`, PR не смёржен. Повтор того же
  запроса — тот же исход без второго `PUT` (журнал прокси).
- **12 «идентичный повтор» и «сверка после таймаута»:** новый stop-PR на новое
  `ws = stand-<tag>-b`. Прокси пропускает `PUT`, но рвёт ответ. Ожидание: исход `done`
  через сверку (`merged_by == <slug>[bot]`, голова). Повтор того же запроса — `done`,
  `replayed: True`, `PUT` в журнале прокси один.
- **Уборка (9):** закрыть открытые PR и удалить ветки. Коммиты засева и мержи остаются в
  истории `master` стенда (уникальные `ws` по `tag`), это записывается в evidence.

- [ ] **Step 1:** офлайн-тесты помощников в `tests/df/test_live_stand.py` (порядок
  зависимостей строк, разбор журнала прокси, отсутствие `DF_STAND_*` в чтении).
- [ ] **Step 2:** workflow: прежние секреты и переменные, добавить строки в `ROWS`, `needs`.
- [ ] **Step 3:** коммит `test(df): live merge rows on the stop route`.

### Task 12: живой прогон, evidence, учёт

- [ ] **Step 1:** владелец выполняет H5–H8 и запускает `df-stage2-live` на `master`
  (после мержа PR задач 1–11).
- [ ] **Step 2:** `docs/evidence/2026-11-XX-df-stage2b-acceptance.md`: прогон, ячейки,
  строки 10–12, журнал прокси, `merged_by`, SHA. Отдельная таблица «виды мержа»: stop —
  живьём; candidate, finalize — офлайн и root, живьём **не закрыты** (этап 3 / M3); brief —
  отказ (часть C).
- [ ] **Step 3:** TODO: закрыть `df-installer-stage2b` с указанием на evidence; добавить
  пункты «живой мерж candidate издателем» (`@blocked_by` этапа 3) и «живой finalize»;
  снять блокировку `df-trust-history-token`.
- [ ] **Step 4:** PR evidence (owner-merge).

## Шаги человека

- **H5.** Новый classic PAT `betta-tester`, `repo`, 30 дней → секрет
  `DF_STAND_MACHINE_TOKEN` environment `df-stage2-live`.
- **H6.** Новый ключ App 5285716 → секрет `DF_STAND_APP_KEY`. Убедиться, что установка
  170589886 охватывает `df-roles-probe-private` с `contents: write`, `pull_requests: write`.
- **H7.** В настройках `df-roles-probe-private` разрешён squash-мерж.
- **H8.** После прогона отозвать PAT и ключ, удалить секреты (как
  `df-stage2a-stand-secrets-revoke`).

## Вне этого плана

- Живой мерж candidate и finalize издателем — после привязки `df-trust` и стендов политики
  (этап 3, приёмка §10 строки 3) и M3 approval-act.
- Предпроверка брифа — часть C (Task 17 approval-act).
- `setup-trust`, `history_gh_config_dir`, `--rebind` — этап 3.
- Отзыв токена установки внутри издателя для `publish-review` — уже не нужен (токены
  издателя живут час и не покидают процесс); не трогаем.
