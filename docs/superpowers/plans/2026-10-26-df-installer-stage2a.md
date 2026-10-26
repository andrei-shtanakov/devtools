# Установщик `df` (команда `darkfactory`), этап 2a — роли и изоляция: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sudo darkfactory roles setup` создаёт роли ОС `df-exec`, `df-review`,
`df-publisher`, файл `/etc/sudoers.d/df` и неизменяемые обёртки `/opt/df/libexec/`.
Человек регистрирует прогон через `sudo /opt/df/bin/df-run-start`, агенты обращаются к
издателю `df-publish` только через `sudo` по точным командам. Издатель исполняет пять
операций протокола §5. Мерж в 2a доходит до маршрутизации вида и отказывает
`precheck_unavailable`: живой мерж — этап 2b, после части B. Приёмка — таблица §4.5 без
трёх строк мержа, на обеих ОС, через настоящий запускатель и `sudo`.

**Architecture:** код этапа — модули пакета `df/`, только stdlib. Издатель и
`df-run-start` исполняются из **административной среды** (административный Python +
`/opt/df/admin/<id>`, сверенный по `admin_digest`): ни root, ни держатель ключа App не
исполняют кода, к которому `df-build` имел доступ на запись. Запускатели модельных ролей
(`df-launch`, `df-review-run`) исполняют код релиза от роли. Состояние издателя — каталог
`/var/lib/df/publisher/` (резервирование id, намерения, результаты) и транзакционная SQLite
журнала периметра. GitHub — HTTPS через `urllib`, подпись JWT App — утверждённым `openssl`.

**Tech Stack:** Python ≥ 3.12, stdlib (`sqlite3`, `urllib`, `json`, `fcntl`, `secrets`);
системные `sudo`, `visudo`, `openssl`, `dscl`/`sysadminctl` (macOS), `useradd`/`groupadd`
(Linux); pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-13-df-installer-design.md` **rev 9** (этот PR:
разделение этапа 2 на 2a/2b и решения ниже) и спека A **rev 16**. Этап 2a = §2.5 строка 2a,
§4 (кроме строк мержа §4.5), §5 (кроме исполнения мержа §5.4), §5.8, §6.1 в объёме
хранилища и операций издателя без привязки корня доверия, §10 строка 2a. План спорит со
спекой, а не заменяет её: исполнитель читает обе.

## Решения (26.10)

1. **Этап 2 делится на 2a и 2b** (решение владельца 26.10). Операция `merge` перед мержем
   вызывает `act_premerge` (часть B, Task 15 плана `2026-10-12-approval-act-review.md`), а
   часть B ждёт выпуска M0 steward (steward#211). 2a — всё, что от M0 не зависит; в `merge`
   — разбор запроса, маршрутизация вида и отказы, предпроверка — через интерфейс
   `Precheck`, который до части B отвечает `refused: precheck_unavailable`. 2b — три строки
   мержа §4.5 (смена head, идемпотентный повтор мержа, сверка после таймаута) живьём, плюс
   поверхность candidate D16 и полный состав PR (minor r7 №1 из плана этапа 1). Спека rev 9
   вносит 2a/2b в §2.5 и §10.
2. **Стенд живых строк** (решение владельца 26.10):
   - продукт — новый **приватный** репо `andrei-shtanakov/df-roles-probe-private`
     (учётка владельца), ruleset как на спайке: 1 approve, last-push approval, без bypass;
   - App — App спайка `df-probe-reviewer-ashtanakov` (id 5285716) с **новым** ключом,
     установлен и на приватный репо;
   - machine user — `ai-prosto` с отдельным fine-grained PAT только на стенд
     (`contents: write`, `pull_requests: write` на один репо);
   - секреты — GitHub environment `df-stage2-live` в devtools с обязательным одобрением
     владельца; живой job — только `workflow_dispatch` с `master`; CI на PR секретов не
     видит;
   - периметр в 2a — **без привязки** `df-trust`: нет `trust.toml` → `perimeter-status`
     отвечает `unavailable: no_binding`, `record-violation` — `refused: no_binding`;
     хранилище SQLite и автомат событий проверяются на поддельном источнике эпох. Живой
     `df-trust` — этап 3.
3. **Издатель и `df-run-start` — из административной среды** (уточнение спеки rev 9 §4.1,
   §5.7, на подпись владельца). Обёртка `/opt/df/libexec/df-publish` запускает
   `<административный Python> -I -B -S /opt/df/libexec/df-publish-entry.py`, который
   проверяет `admin/<id>` так же, как вход `darkfactory` (§3.9 п. 3), и импортирует
   `df.publisher`. Основание: ключ App — самый ценный секрет установки, а интерпретатор
   релиза собран `df-build`, который имел сеть и запись в дерево (§3.2). Принцип §3.9 «root
   не исполняет ничего, к чему сборщик имел доступ на запись» распространяется на держателя
   ключа App. Следствие — издатель пользуется только stdlib: подпись JWT RS256 —
   `openssl dgst -sha256 -sign` утверждённым CLI `openssl` (необязательный ключ
   `install.toml`, обязателен для `roles setup`; политика доверенных путей как у `git`).
4. **Записи этапа 2a — отдельная команда-писатель `darkfactory roles setup`**, а не маршрут
   установки: роли, `publisher.toml`, sudoers, обёртки `libexec/`, `df-run-start`, каталоги
   `/var/lib/df/{runs,publisher,perimeter}`. Обёртки не зависят от релиза (разрешают
   `current` при каждом вызове), поэтому маршрут активации их не трогает; `roles setup`
   повторяем и сверяет написанное с ожидаемым текстом. Автомат установки этапа 1 не меняется.
5. **`products.toml` в 2a не пишется командой.** Регистрация продукта — `setup-repo`
   (этап 3, §6.4 п. 1). Приёмка 2a пишет `products.toml` фикстурой от root; издатель и
   `doctor --product` его читают. Без `products.toml` издатель отказывает
   `refused: product_unknown`.
6. **Учётные данные — `darkfactory setup-agent`** (§1, имя из решений 12.10):
   `setup-agent app-key --app-id N --file PATH` кладёт ключ в
   `/etc/df/secrets/app-<N>.pem` (`df-publisher`, `0400`, каталог `0700`);
   `setup-agent machine-token --file PATH` — в `~df-exec/.config/df/github-token`
   (`df-exec`, `0600`). Проверки: machine user — `GET /user` его токеном от `df-exec` через
   `df-launch check-github`; App — JWT → `GET /app/installations` от издателя
   (`df-publish self-check` — операция только для root, §4.1 строка «человек → root»).
7. **Маршрутизация `merge` в 2a** — по шаблонам веток `contracts/approval-branches/v1` и
   форме изменённых путей (finalize — только конверты `review/v1`; stop — ровно один файл
   остановки). Признаки candidate (поверхность D16) и brief требуют кода `governance/`,
   которого в административной среде нет; их проверка — 2b вместе с `act_premerge`. В 2a
   вид candidate/brief устанавливается **только по ветке**, после чего любой вид получает
   `refused: precheck_unavailable`. Шаблона ветки остановки в контракте ещё нет (его
   добавляет часть B), поэтому stop в 2a — всегда `ambiguous_kind`. Ошибки в 2a не могут
   привести к мержу: вызова мержа в коде 2a нет.
8. **Операции `df-launch` в 2a:** `check` (самоотчёт роли: UID, GID, группы, окружение,
   `cwd`, открытые дескрипторы — для `doctor` и приёмки) и `check-github` (`GET /user`
   токеном machine user, только `df-exec`). `run <run_id>` разобран и отказывает
   `конвейер — этап 3`: таблицы конвейеров в релизе 2a нет.
9. **Уровень 2 в v1 недостижим, пока харнессы не подтверждены** (следствие решения владельца
   24.10, §3.6 «Состояние v1»): для модельных ролей нет актуальной пробы. `doctor --product
   P` показывает уровень 2 «не достигнут: доступ к моделям не подтверждён», если остальные
   проверки уровня 2 зелёные. Приёмка 2a — таблица §4.5, а не достижение уровня 2.
10. **Проверка установок App в 2a**: установки App ⊆ репо из `products.toml`; иначе
    `refused: app_installation_outside_products`. Проверка «установка на `approval-policy`
    или `df-trust` — нарушение» (§6.1) требует привязки и в 2a — «не проверено (этап 3)»
    в evidence операции.
11. **Перенос из плана этапа 1** («Вне этого плана»): роли и проверки от их имени (Tasks 1,
    5, 13, 14); `fullfsync` SQLite (Task 11); minor r7 №2 — новый `request_id` после
    `stale_snapshot` (Task 11, клиент); пин моделей прогона и событие смены при `resume`
    (Task 6); `df-run-start` до сброса привилегий не исполняет код релиза (Task 6, тест с
    sentinel); рабочий каталог запуска от роли — вне дерева сборщика, обязанность
    `df-launch` (Task 5, тест с наблюдателем). Minor r7 №1 (полный состав candidate-PR) —
    2b (решение 7).

## Global Constraints

- Пакет `df/` — только stdlib; Python ≥ 3.12; `ruff`, `pyrefly`, строка ≤ 88.
- Пути — только через `df.paths.Layout`; один корень на процесс (D2). Абсолютный путь вне
  `Layout` допустим только для системных объектов: `/etc/sudoers.d/df`, системных CLI.
- Root исполняет только административную среду (§3.9). Держатель ключа App — тоже (решение 3).
- Роли: `df-exec`, `df-review`, `df-publisher`; без административных групп
  (`platform.ADMIN_GROUPS`), shell — из `platform.NOLOGIN_SHELLS`, UID/GID — диапазон
  `platform.ID_RANGE`.
- Sudoers: `NOPASSWD`, `NOSETENV`, `env_reset`, ограниченный `RunAs`, без wildcard в
  аргументах, `!log_output` для команд издателя (§4.1). Файл проверяется `visudo -c -f`
  до установки; установка атомарна (`0440`, root).
- Запрос издателя — JSON со stdin, ≤ 64 КиБ (`65536` байт), строгая схема по операции,
  неизвестные поля — отказ (§5.3). Ответ — JSON в stdout, исход `done | refused | unknown`
  (+ `stale_snapshot` у `perimeter-status`) (§5.5, §6.1).
- Секрет (`read-token`, ключ App, токен machine user) не попадает в argv, журналы издателя,
  evidence, сохранённые git remote и I/O-журнал sudo (§4.4).
- Отказ всегда с причиной; сбой инфраструктуры в приёмке — провал, а не skip (§3.8).
- Сообщения пользователю — по-русски; код, идентификаторы и имена файлов — по-английски.
- Тесты, требующие root, — только в CI-раннерах (гейт `tests/df/conftest.py`).
- PR, трогающие `df/` или `tests/df/`, — метка `human-merge-required`.

## Review Focus

1. **`SUDO_UID` при подмене окружения.** `sudo -E`, `SUDO_UID=0 sudo …`, лишний аргумент
   после операции — sudoers отвергает, а если правило всё же сработало (испорченный файл),
   издатель отвергает сам: фактический UID ≠ `df-publisher` или `SUDO_UID` не в таблице.
   Тест — Task 7 (`test_sender_*`), приёмка — Task 14.
2. **Повтор после сбоя между намерением и ответом.** Издатель упал после записи намерения
   `publish-review`, но до записи результата; повтор того же запроса сверяет GitHub по
   маркеру с ключом запроса, а не публикует второй раз. Тест — Task 9
   (`test_publish_review_reconciles_after_crash`).
3. **Конкурентные одинаковые `request_id` с разным содержимым** — ровно один получает отказ
   (§5.6). Тест — Task 7 (`test_reserve_concurrent_conflict`), процессами, не потоками.
4. **Сломанная SQLite** (обрезанный файл, рассогласованный индекс, неизвестное состояние)
   → `unavailable`, база не пересоздаётся молча. Тест — Task 11.
5. **Утечка токена `read-token`.** Токен есть только в stdout ответа; журнал издателя,
   evidence, `ps` во время чтения, конфиг git после чтения — без него. Тест — Task 9
   (`test_read_token_not_journaled`), приёмка — Task 14/15.

---

## Порядок и зависимости

| Task | Содержание | Зависит от |
|---|---|---|
| 1 | роли ОС и учётки | — |
| 2 | схемы `publisher.toml`, `products.toml`, `trust.toml`, запись прогона | 1 |
| 3 | sudoers: генерация, проверка, установка, эффективные настройки | 1, 2 |
| 4 | обёртки `libexec/`, входы административной среды, `roles setup` | 1–3 |
| 5 | `df-launch`: окружение, дескрипторы, `cwd`, операции 2a | 4 |
| 6 | `df-run-start`: реестр прогонов, сброс привилегий, пин моделей | 2, 4, 5 |
| 7 | каркас издателя: запрос, отправитель, прогон, резервирование id, журнал | 2, 4, 6 |
| 8 | клиент GitHub App: JWT через `openssl`, токен установки, самопроверка | 7 |
| 9 | `publish-review`, `read-token`, сверка | 7, 8 |
| 10 | `merge` 2a: маршрутизация вида, интерфейс `Precheck` | 7, 8 |
| 11 | журнал периметра SQLite; `record-violation`, `perimeter-status`; клиент | 7 |
| 12 | `setup-agent`: ключ App, токен machine user, проверки | 4, 5, 8 |
| 13 | `doctor --product P`: уровень 2 | 3–12 |
| 14 | root-приёмка §4.5 (CI, обе ОС, без GitHub) | 1–13 |
| 15 | живая приёмка на стенде, evidence, документация | 14, шаги H |

Шаги человека (H, параллельно Tasks 1–14, до Task 15):
- **H1.** Создать приватный `df-roles-probe-private`, ruleset как у `df-roles-probe`
  (id 24926051), `ai-prosto` — write.
- **H2.** App 5285716: установить на `df-roles-probe-private`, выпустить новый ключ, старый
  отозвать.
- **H3.** Fine-grained PAT `ai-prosto` только на `df-roles-probe-private`
  (`contents: write`, `pull_requests: write`, `metadata: read`), срок ≤ 30 дней.
- **H4.** Environment `df-stage2-live` в devtools: required reviewer — владелец; секреты
  `DF_STAND_APP_KEY`, `DF_STAND_MACHINE_TOKEN`; переменные `DF_STAND_REPO_ID`,
  `DF_STAND_APP_ID`, `DF_STAND_INSTALLATION_ID`.

---

### Task 1: Роли ОС и учётки

**Files:**
- Create: `df/roles.py`
- Modify: `df/platform.py` (обобщить создание учётки), `df/paths.py` (домашние каталоги ролей)
- Test: `tests/df/test_roles.py`, `tests/df/test_platform.py` (дополнить)

**Interfaces:**
- Consumes: `platform.create_build_user`, `ACCOUNT_ATTRS`, `account_problems`,
  `config.Account`, `load_accounts`/`dump_accounts`.
- Produces:
  - `roles.ROLE_USERS: tuple[str, ...] = ("df-exec", "df-review", "df-publisher")`;
  - `roles.MODEL_ROLE_USERS = frozenset({"df-exec", "df-review"})` (роли, которые
    исполняют модели; `models.ROLE_CONTEXT` ссылается только на них);
  - `Layout.role_home(name: str) -> Path` — `<root>/Users/<name>` (macOS) или
    `<root>/home/<name>` (Linux); `Layout.build_home` = `role_home("df-build")`;
  - `platform.create_service_user(layout: Layout, name: str) -> Account` — то, что сейчас
    делает `create_build_user`, для любого имени из `ROLE_USERS ∪ {"df-build"}`; шаг
    отказа — `f"user:{name}"`; `create_build_user(layout)` остаётся тонкой обёрткой;
  - `platform.verify_service_user(layout: Layout, name: str) -> list[str]`;
  - `roles.ensure_roles(layout: Layout) -> dict[str, Account]` — создаёт недостающие
    роли по намерению в порядке `ROLE_USERS`, возвращает все три записи `ready`.

- [ ] **Step 1: Тесты**

```python
def test_role_users_exact():
    assert roles.ROLE_USERS == ("df-exec", "df-review", "df-publisher")
    assert roles.MODEL_ROLE_USERS == {"df-exec", "df-review"}

def test_model_roles_map_into_model_role_users():
    assert set(models.ROLE_CONTEXT.values()) <= roles.MODEL_ROLE_USERS

def test_role_home_platform(tmp_path, monkeypatch):
    lay = Layout(tmp_path.resolve())
    monkeypatch.setattr(sys, "platform", "linux")
    assert lay.role_home("df-exec") == lay.root / "home" / "df-exec"

def test_create_service_user_refuses_foreign_record(fake_records):
    fake_records.user("df-exec", uid=600)          # существует, не наш
    with pytest.raises(StepFailed, match="user:df-exec.*создан не darkfactory"):
        platform.create_service_user(layout, "df-exec")

def test_ensure_roles_resumes_creating(fake_os):
    # запись `creating` для df-review — повтор дописывает недостающие атрибуты
    ...
    assert {a.state for a in roles.ensure_roles(layout).values()} == {"ready"}

def test_ensure_roles_distinct_ids(fake_os):
    accs = roles.ensure_roles(layout)
    assert len({a.uid for a in accs.values()}) == 3
```

`fake_records`/`fake_os` — существующие фикстуры `tests/df/test_platform.py` для
`read_record`, `run_command`, `pwd`/`grp`; дополнить параметром имени.

- [ ] **Step 2: Запустить — FAIL** (`uv run pytest tests/df/test_roles.py -q`).
- [ ] **Step 3: Реализация.** Константа `BUILD_USER` и литералы `"build-user"` в
  `create_build_user` и вспомогательных функциях заменяются параметром `name`; шаг —
  `f"user:{name}"` (для `df-build` сохранить прежний шаг `build-user` — его ждут тесты
  этапа 1 и `install_table`). `ID_RANGE` общий: `_free_id` ищет первое свободное
  значение, занятые уже созданными ролями исключаются естественно.
- [ ] **Step 4: Тесты этапа 1 зелёные** — `uv run pytest tests/df -q` (без root-тестов).
- [ ] **Step 5: Commit** `feat(df): service roles df-exec/df-review/df-publisher`

---

### Task 2: Схемы `publisher.toml`, `products.toml`, `trust.toml`, запись прогона

**Files:**
- Create: `df/stage2_config.py`
- Modify: `df/config.py` (`STAGE_OF`, `STATE_SCHEMAS`), `df/doctor.py` (`_later_stage_files`)
- Test: `tests/df/test_stage2_config.py`

**Interfaces:**
- Produces:
  - `PublisherConfig(senders: Mapping[int, frozenset[str]])` — UID → операции;
    `parse_publisher(text) -> PublisherConfig`, `dump_publisher(cfg) -> str`;
    `expected_publisher(accounts: Mapping[str, Account]) -> PublisherConfig` —
    `df-exec`: `{"merge", "record-violation", "perimeter-status"}`;
    `df-review`: `{"publish-review", "read-token", "record-violation", "perimeter-status"}`
    (таблица §5.2);
  - `Product(repository_id: int, full_name: str, profile: Literal["fork", "write"],
    app_id: int, installation_id: int, machine_user: str, checkout: str)`;
    `parse_products(text) -> dict[int, Product]` (ключи — `repository_id`, дубликат —
    отказ), `load_products(path) -> dict[int, Product]` (нет файла → `{}`);
  - `TrustBinding(host: str, df_trust_repository_id: int, df_trust_full_name: str,
    df_trust_branch: str, approval_policy_repository_id: int, transport: Literal["public"])`;
    `load_trust(path) -> TrustBinding | None`;
    `TrustBinding.root_id -> tuple[str, int, str]` = `(host, df_trust_repository_id,
    df_trust_branch)`;
  - `RunRecord(run_id: str, release_id: str, release_digest: str, admin_digest: str,
    product: int, models: Mapping[str, ModelPin], root_id: tuple[str, int, str] | None,
    started_at: str, started_by_uid: int)`; схема `df/run/v1` (JSON);
    `dump_run(rec) -> str`, `parse_run(text) -> RunRecord`;
  - `ModelPin(harness: str, harness_version: str, model: str, actual_model: str,
    effort: str, params: Mapping[str, str], config_hash: str)`.
- `STAGE_OF` после Task 2: `{"trust.toml": 3, "products.toml": 3}` — `publisher.toml`
  разбирается проверкой `etc.schemas` (этап 2a); `products.toml` и `trust.toml` остаются
  «не проверено (этап 3)» на уровне 1, а `doctor --product` (Task 13) их разбирает.

- [ ] **Step 1: Тесты**

```python
def test_publisher_expected_table():
    accs = {n: Account(n, uid, uid, "ready", "t") for n, uid in
            (("df-exec", 601), ("df-review", 602), ("df-publisher", 603))}
    cfg = expected_publisher(accs)
    assert cfg.senders[601] == {"merge", "record-violation", "perimeter-status"}
    assert cfg.senders[602] == {"publish-review", "read-token",
                                "record-violation", "perimeter-status"}
    assert 603 not in cfg.senders            # издатель сам себе не отправитель

def test_publisher_roundtrip_and_unknown_key():
    text = dump_publisher(cfg)
    assert parse_publisher(text) == cfg
    with pytest.raises(ConfigError, match="unknown key"):
        parse_publisher(text + 'extra = 1\n')

def test_publisher_unknown_operation_refused():
    with pytest.raises(ConfigError, match="operation"):
        parse_publisher('schema = "df/publisher/v1"\n[[sender]]\nuid = 601\n'
                        'operations = ["shell"]\n')

def test_products_duplicate_repository_id():
    with pytest.raises(ConfigError, match="duplicate"):
        parse_products(two_rows_same_id)

def test_trust_transport_public_only():
    with pytest.raises(ConfigError, match="transport"):
        parse_trust(text_with_transport("app"))

def test_run_record_roundtrip():
    assert parse_run(dump_run(rec)) == rec

def test_run_record_rejects_unknown_field():
    with pytest.raises(ConfigError):
        parse_run(json.dumps({**json.loads(dump_run(rec)), "x": 1}))
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Реализация** — тем же приёмом, что `config.py` (`_parse_toml`,
  `_check_keys`); `publisher.toml`: `schema`, массив `[[sender]]` с `uid` и
  `operations` (отсортированный список строк из `OPERATIONS = ("publish-review",
  "read-token", "merge", "record-violation", "perimeter-status")`); `products.toml`:
  массив `[[product]]` с полями `Product`; `run` — JSON с `schema = "df/run/v1"`,
  канонический вывод `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
- [ ] **Step 4: PASS + тесты `doctor` этапа 1** (`etc.publisher.toml` больше не «этап 2»).
- [ ] **Step 5: Commit** `feat(df): stage-2 config schemas and run record`

---

### Task 3: Sudoers

**Files:**
- Create: `df/sudoers.py`
- Test: `tests/df/test_sudoers.py`

**Interfaces:**
- Consumes: `Layout`, `roles.ROLE_USERS`, `Account`.
- Produces:
  - `SUDOERS_FILE = Path("/etc/sudoers.d/df")` (системный путь; в мире `<R>` приёмки — тот
    же файл: роли и sudo — общесистемные);
  - `render(layout: Layout, operator: str) -> str` — точный текст (ниже);
  - `validate(text: str) -> list[str]` — `visudo -c -f <tmp>`; проблемы — строки вывода;
  - `install(text: str) -> None` — запись во временный файл того же каталога (`0440`,
    root), `validate`, `rename`; иначе отказ шага `sudoers`;
  - `effective_problems(layout: Layout, operator: str) -> list[str]` — разбор
    `sudo -l -U <user>` для `operator`, `df-exec`, `df-review`: набор команд и тегов равен
    ожидаемому; у команд `df-publish` эффективен `!log_output`; посторонних правил для
    ролей нет (`platform.sudo_rules` уже есть для `df-build`).

Текст (имена путей — от `layout`; `<op>` — оператор, имя пользователя ОС человека):

```
# generated by darkfactory roles setup; do not edit
Defaults:df-exec, df-review env_reset, !setenv
Defaults!/opt/df/libexec/df-publish !log_output
<op> ALL=(df-exec) NOPASSWD: NOSETENV: /opt/df/libexec/df-launch df-exec check, \
    /opt/df/libexec/df-launch df-exec check-github, \
    /opt/df/libexec/df-launch df-exec run
<op> ALL=(df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-launch df-review check
df-exec ALL=(df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-review-run
df-exec ALL=(df-publisher) NOPASSWD: NOSETENV: /opt/df/libexec/df-publish merge, \
    /opt/df/libexec/df-publish record-violation, \
    /opt/df/libexec/df-publish perimeter-status
df-review ALL=(df-publisher) NOPASSWD: NOSETENV: \
    /opt/df/libexec/df-publish publish-review, /opt/df/libexec/df-publish read-token, \
    /opt/df/libexec/df-publish record-violation, \
    /opt/df/libexec/df-publish perimeter-status
```

`df-launch df-exec run` без аргументов в sudoers означает «ровно эти аргументы»: `run_id`
идёт через stdin, как запрос издателя (§5.3). `df-run-start` в sudoers не пишется: человек
вызывает его обычным `sudo` (§4.1 последняя строка). `df-publish self-check` — только root,
правила нет.

- [ ] **Step 1: Тесты** — точный текст (`test_render_exact` со снимком
  `tests/df/fixtures/sudoers/expected.txt`, корень `/`); `<R>`-корень даёт пути `<R>/opt/df/…`;
  нет `*`, `ALL` справа только в позиции хоста; `operator` из `ROLE_USERS ∪ {"root",
  "df-build"}` → `ValueError`; недопустимое имя (пробел, `,`, `:`, `=`, `\`) → `ValueError`;
  `validate` на испорченном тексте (подменённый `run_command`) → проблемы; `install` при
  проблемах не вызывает `rename`; `effective_problems` на снимках вывода `sudo -l -U` обеих
  платформ (`tests/df/fixtures/sudoers/sudo-l-*.txt`): лишний тег `SETENV`, отсутствие
  `!log_output`, лишняя команда → проблема с именем.
- [ ] **Step 2: FAIL.** **Step 3: Реализация** (`run_command` с `_SYSTEM_ENV`; путь
  `visudo` — `/usr/sbin/visudo`). **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(df): sudoers rules for stage-2 roles`

---

### Task 4: Обёртки `libexec/`, входы административной среды, `darkfactory roles setup`

**Files:**
- Create: `df/libexec.py` (тексты и проверка обёрток), `df/admin_entry.py` (общий вход
  административной среды для `df-publish` и `df-run-start`), `df/roles_setup.py`
- Modify: `df/paths.py` (`libexec`, `run_start`, `runs_dir`, `publisher_dir`,
  `perimeter_dir`, `secrets_dir`), `df/cli.py` (подкоманда `roles setup`, писатель),
  `df/launcher_entry.py` (`WRITER_COMMANDS += (("roles", "setup"),)`)
- Test: `tests/df/test_libexec.py`, `tests/df/test_roles_setup.py`, `tests/df/test_cli.py`

**Interfaces:**
- Produces:
  - `Layout.libexec = opt/"libexec"`, `Layout.run_start = bin_dir/"df-run-start"`,
    `Layout.runs_dir = var/"runs"`, `Layout.publisher_dir = var/"publisher"`,
    `Layout.perimeter_dir = var/"perimeter"`, `Layout.secrets_dir = etc/"secrets"`;
  - `libexec.WRAPPERS: Mapping[str, str]` — имя → текст для `df-launch`,
    `df-review-run`, `df-publish`, `df-publish-entry.py`, `df-run-start` (в `bin/`),
    `df-run-start-entry.py`;
  - `libexec.wrapper_text(layout: Layout, name: str) -> str`;
  - `libexec.wrapper_problems(layout: Layout) -> list[str]` — каждый файл: обычный, root,
    `0755` (`*-entry.py` — `0644`), текст равен ожидаемому, путь проходит
    `platform.trusted_path_problems`;
  - `admin_entry.main(argv: list[str], *, program: Literal["publish", "run-start"],
    root: str = "/") -> int` — повторяет шаги 2–4 входа `darkfactory` (§3.9: снимок,
    запись `allowed`, доверие к `admin/<id>`, `admin_digest`, свой интерпретатор),
    затем импортирует `df.publisher` или `df.run_start` и вызывает `main(argv, layout=…,
    snapshot=…)`. Код шагов не дублируется: вход использует функции
    `launcher_entry` (`take_snapshot`, `verified_admin_dir`,
    `_require_trusted_interpreter`), режим — читатель;
  - `roles_setup.run(layout: Layout, *, operator: str, snap: Snapshot) -> None` — шаги
    под блокировкой писателя через `journal.run_steps`: `roles` (`ensure_roles`) →
    `dirs` → `publisher-config` (`expected_publisher` → `/etc/df/publisher.toml`) →
    `libexec` → `sudoers` (`render`, `install`) → `verify` (`wrapper_problems`,
    `effective_problems`).

Тексты обёрток (корень `/`; в мире `<R>` пути от `layout`). Каждая обёртка — `sh`-скрипт
root `0755` из одной `exec`-строки, запускающей административный Python со своим входом
`*-entry.py` (root `0644`, текст stdlib, как `darkfactory-entry.py`). Обёртки исполняются
уже **от роли** (sudo сменил UID); административный Python роль только читает.

- `df-launch`, `df-review-run` — вход `df-launch-entry.py` один раз читает `current`
  (`os.readlink`, только форма `releases/<id>`), проверяет, что запись `allowed` в
  `releases.toml`, и делает `os.execve` в
  `<release>/env/devtools/bin/python -I -B <release>/devtools/df/launch.py
  --release <release> <role> <operation>` с окружением роли (Task 5). Код запускателя
  — в релизе и исполняется от роли: это код модельных ролей, ему доверие к
  административной среде не нужно. `df-review-run` — то же с ролью `df-review` и
  операцией `review` (в 2a — отказ «конвейер — этап 3»).
- `df-publish` — `#!<административный Python> -I -B -S /opt/df/libexec/df-publish-entry.py`
  по форме `darkfactory` (§3.9): скрипт `sh` из одной `exec`-строки; вход
  `df-publish-entry.py` — `admin_entry.main(sys.argv[1:], program="publish")`.
- `df-run-start` — то же с `program="run-start"`.

Каталоги шага `dirs` (создание по правилу §3.3 «Создаваемые объекты», точный режим):

| Путь | Владелец | Режим |
|---|---|---|
| `/opt/df/libexec` | root | `0755` |
| `/var/lib/df/runs` | root | `0755` (запись — root; чтение — издатель и роли) |
| `/var/lib/df/publisher` | `df-publisher` | `0700` |
| `/var/lib/df/perimeter` | `df-publisher` | `0700` |
| `/etc/df/secrets` | `df-publisher` | `0700` |
| `~df-exec/work`, `~df-review/work` | своя роль | `0700` |

- [ ] **Step 1: Тесты** — `test_wrapper_text_exact` (снимки в
  `tests/df/fixtures/libexec/`); `test_wrapper_problems_*` (чужой владелец, `0775`, другой
  текст, ссылка вместо файла); `test_roles_setup_steps_order` (порядок шагов и что при
  провале `sudoers` файл не установлен, а `verify` не выполнялся); `test_roles_setup_idempotent`
  (второй прогон — без изменений, сверка проходит); `test_roles_setup_requires_openssl`
  (нет `openssl` в `install.toml` → отказ шага `roles` с инструкцией); `test_cli_roles_setup_is_writer`
  (`mode_of(["roles", "setup"]) == "writer"`, без root — отказ до блокировки);
  `test_admin_entry_refuses_untrusted_admin_dir` (переиспользует фикстуры
  `test_launcher_entry.py`).
- [ ] **Step 2: FAIL.** **Step 3: Реализация.** `install.toml` — необязательный ключ CLI
  `openssl` (в `config.REQUIRED_CLI` не входит; `roles setup` требует). `--operator`
  по умолчанию — `SUDO_USER`; без него и без флага — отказ «укажите --operator».
- [ ] **Step 4: PASS + `uv run pytest tests/df -q`.**
- [ ] **Step 5: Commit** `feat(df): roles setup, libexec wrappers, admin entries`

---

### Task 5: `df-launch`

**Files:**
- Create: `df/launch.py`
- Test: `tests/df/test_launch.py`

**Interfaces:**
- Produces:
  - `ENV_ALLOW = ("HOME", "PATH", "LANG", "TZ")` + переменные харнесса из таблицы релиза
    (в 2a — пусто);
  - `role_env(layout: Layout, role: str) -> dict[str, str]` — `HOME` = `role_home(role)`,
    `PATH` = `"/usr/bin:/bin"`, `LANG` = `"C.UTF-8"`, `TZ` = `"UTC"`; ничего из окружения
    вызывающего;
  - `role_cwd(layout: Layout, role: str) -> Path` — `role_home(role)/"work"`;
  - `main(argv: list[str], *, layout: Layout) -> int` — `argv = [role, operation]`; роль
    сверяется с фактическим UID (`pwd.getpwuid(os.getuid()).pw_name == role`, иначе
    отказ 2); операции 2a: `check`, `check-github` (только `df-exec`), `run` (отказ
    «конвейер — этап 3»);
  - `check_report() -> dict[str, object]` — `uid`, `gid`, `groups`, `env` (имена
    переменных), `cwd`, `fds` (открытые дескрипторы > 2), `euid`.

Обязанности (§4.2 + перенос из этапа 1):
1. до любого действия — `os.closerange(3, maxfd)` кроме собственного;
2. `os.chdir(role_cwd)` **до** чтения чего-либо ещё; рабочий каталог вызывающего не
   используется никогда (на macOS `sudo` открыл его с `euid = 0` — граница §3.8);
3. окружение процесса заменяется на `role_env` (`os.environ.clear()` + `update`) до
   запуска дочерних процессов;
4. `current` разрешён обёрткой один раз; путь релиза приходит аргументом обёртки и
   сверяется с `__file__` (код запускателя лежит в этом релизе).

- [ ] **Step 1: Тесты** — `test_role_env_ignores_caller` (в окружении вызывающего
  `SSH_AUTH_SOCK`, `GH_TOKEN`, `GITHUB_TOKEN`, `DYLD_INSERT_LIBRARIES`, `LD_PRELOAD`,
  `PYTHONPATH` → ни одной в `role_env`); `test_main_refuses_role_mismatch`;
  `test_check_github_only_df_exec`; `test_run_refused_stage3`; `test_closes_inherited_fds`
  (подпроцесс с открытым fd 7 → в отчёте `check` нет 7); `test_cwd_is_role_work`
  (вызов из `tmp_path` → `check_report()["cwd"] == role_cwd`).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): df-launch role launcher`

---

### Task 6: `df-run-start`

**Files:**
- Create: `df/run_start.py`
- Test: `tests/df/test_run_start.py`

**Interfaces:**
- Consumes: `RunRecord`, `ModelPin` (Task 2), `models.resolve`, `models.config_hash`,
  `probe.read_record` (актуальная проба — источник `harness_version`, `actual_model`),
  `stage2_config.load_trust`, `libexec` (путь `df-launch`).
- Produces:
  - `main(argv, *, layout, snapshot) -> int` — `df-run-start --product ID` и
    `df-run-start --resume RUN_ID [--accept-model-change]`;
  - `new_run_id() -> str` — `f"run-{utc:%Y%m%dT%H%M%SZ}-{secrets.token_hex(8)}"`;
  - `register(layout, rec: RunRecord) -> None` — `/var/lib/df/runs/<run_id>.json`,
    root `0644`, `O_EXCL`, `fsync` файла и каталога;
  - `model_change(old: RunRecord, new_pins) -> dict | None` — событие смены пина
    (`run_id`, роль, было/стало, шаг «с которого действует» — номер следующего шага
    прогона из записи `resume`), пишется в `/var/lib/df/runs/<run_id>.events.jsonl`.

Порядок (§5.8): только `/etc/df` и релиз **как данные** → запись прогона → сброс
привилегий → `os.execv` обёртки `df-launch df-exec run` с `run_id` в stdin. Сброс —
`os.setgroups([gid])`, `os.setgid`, `os.setuid` значениями из `accounts.toml`; проверка
`os.getuid() == uid and os.geteuid() == uid` до `exec`. Харнесс, конфигурация продукта,
код продукта и домашние каталоги ролей до сброса не открываются.

Пин моделей: для каждой модельной роли продукта — `models.resolve`; актуальная проба
(Task 11 этапа 1) даёт версию харнесса и фактический model ID; без неё — пин с
`harness_version = "не проверено"`, `actual_model = "не сообщён"`, и прогон **не**
стартует: «доступ к моделям не подтверждён: <роль>» (код 3, нужен человек). В v1 это
значит, что штатный прогон не стартует ни для одного продукта (решение 9); приёмка
проверяет механизм на фикстурной пробе.

`--resume`: пины пересчитываются; несовпадение без `--accept-model-change` — отказ с
разницей; с флагом — событие `model_change` и продолжение.

- [ ] **Step 1: Тесты** — `test_register_exclusive` (повтор того же id → отказ);
  `test_no_role_home_read_before_drop` (наблюдение `open`/`os.stat` через подменённые
  функции: ни одного пути под `role_home`, ни одного файла продукта до `setuid`);
  `test_release_code_not_executed_before_drop` — **перенос этапа 1**: в фикстурном релизе
  `env/devtools/bin/python` — скрипт-sentinel, который пишет файл, если `euid == 0`;
  `df-run-start` до сброса его не запускает (root-тест Task 14 повторяет это настоящим
  `sudo`); `test_resume_model_change_needs_flag`; `test_resume_event_records_step`;
  `test_revoked_release_refused` (`releases.toml` `status = "revoked"` → отказ с
  причиной до записи); `test_root_id_recorded` (`trust.toml` есть → `root_id`; нет →
  `None`).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): df-run-start run registry and privilege drop`

---

### Task 7: Каркас издателя

**Files:**
- Create: `df/publisher/__init__.py`, `df/publisher/request.py`, `df/publisher/store.py`,
  `df/publisher/main.py`
- Test: `tests/df/test_publisher_request.py`, `tests/df/test_publisher_store.py`,
  `tests/df/test_publisher_main.py`

**Interfaces:**
- `request.MAX_BYTES = 65536`, `request.READ_TIMEOUT = 5.0`;
- `request.parse(op: str, raw: bytes) -> Request` — схема `df/publisher-request/v1`; общие
  поля PR-операций: `schema`, `request_id` (`[A-Za-z0-9._-]{8,128}`), `run_id`,
  `repository_id`, `pr`, `head_sha` (40 hex); `publish-review`: + `decision`
  (`approve | request_changes | comment`), `body` (≤ 60000 символов); `merge`: ничего
  сверх общих; `read-token`: `request_id`, `run_id`, `repository_id`;
  `record-violation`: `request_id`, `run_id`, `root_id` (`{host, repository_id,
  branch}`), `source` (`approval-policy | df-trust`), `observed_at` (RFC 3339 UTC),
  `detail` (≤ 4000); `perimeter-status`: `request_id`, `run_id`, `root_id`,
  `expected_snapshot_sha`. Неизвестное поле, неверный тип, лишняя операция — `Refused`;
- `request.canonical(req) -> bytes` (JSON, `sort_keys`, без пробелов) и
  `request.digest(req) -> str` (`sha256:`);
- `Outcome = Literal["done", "refused", "unknown", "stale_snapshot"]`;
  `Response(outcome, reason: str | None, evidence: Mapping[str, object])`; вывод —
  одна строка JSON;
- `main.sender(publisher_cfg, *, uid=os.getuid(), env=os.environ) -> tuple[int, str]` —
  фактический UID == `df-publisher` (`accounts.toml`), `SUDO_UID` — десятичное число из
  таблицы; операция ∈ разрешённых отправителю; иначе `Refused` (причины:
  `not_publisher_uid`, `sender_unknown`, `operation_not_allowed`);
- `main.check_run(layout, run_id) -> RunRecord` — запись есть, релиз записи `allowed`
  в `releases.toml` (отозванный → `refused: release_revoked: <причина>`), `root_id` записи
  сверяется операциями периметра (Task 11);
- `store.reserve(layout, sender_uid, op, req) -> Reservation` — §5.6 дословно: глобальная
  `flock` на `publisher/ids.lock` → временный файл `ids/.<request_id>.<pid>.tmp` с
  `{sender, operation, digest}` → `fsync` → `os.link` в `ids/<request_id>` (`EEXIST` →
  сверка привязки) → `fsync` каталога → снятие глобальной блокировки → `flock` по ключу
  идемпотентности `keys/<sha256(sender|op|digest)>.lock`. Привязка не совпала —
  `refused: request_id_reused`. При старте `store.open` удаляет `ids/.*.tmp`;
- `store.intent(res, payload) -> None` / `store.result(res, response) -> None` — файлы
  `intents/<key>.json`, `results/<key>.json`, запись `write_atomic` с `fsync` каталога;
  повтор с готовым `result` → прежний ответ (кроме `perimeter-status` и `read-token`);
  с `intent` без `result` → ветка сверки операции;
- журнал `publisher/journal.jsonl` — одна строка на запрос: время, отправитель, операция,
  `request_id`, `run_id`, digest, исход; секреты не пишутся никогда (поля ответа
  `read-token` заменяются `"token": "<redacted>"`).

`main.main(argv, *, layout, snapshot) -> int`: `argv = [operation]` (лишний аргумент →
отказ 2 без чтения stdin); stdin читается с ограничением размера и времени
(`select` + `read`); порядок: отправитель → разбор → прогон → резервирование →
операция. Неожиданное исключение после записи намерения → `unknown` (не `refused`).

- [ ] **Step 1: Тесты** — разбор: каждое поле каждой операции (таблица параметризации),
  65537 байт → `refused: too_large`, медленный stdin → `refused: read_timeout`;
  `test_sender_*`: `uid` не издателя, `SUDO_UID` отсутствует / `"0"` / `" 601"` / не из
  таблицы, операция не разрешена; `test_reserve_same_digest_resumes`,
  `test_reserve_conflict_refused`, `test_reserve_concurrent_conflict` (два процесса
  `multiprocessing` со стартовым барьером, один id, разное содержимое → ровно один
  `refused`), `test_reserve_crash_leaves_no_partial` (точка сбоя между `fsync` и `link` →
  при следующем `open` временный файл удалён, `ids/<id>` нет);
  `test_repeat_returns_previous_result`; `test_perimeter_status_and_read_token_not_replayed`;
  `test_extra_argv_refused_before_stdin`; `test_journal_has_no_token`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): publisher request frame, sender, id reservation`

---

### Task 8: Клиент GitHub App

**Files:**
- Create: `df/publisher/github.py`
- Test: `tests/df/test_publisher_github.py`

**Interfaces:**
- `API = "https://api.github.com"`, `TIMEOUT = 20.0`, `USER_AGENT = "darkfactory-publisher"`;
- `Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int,
  Mapping[str, str], bytes]]` — (method, url, headers, body) → (status, headers, body);
  по умолчанию `urllib`, без прокси из окружения (`ProxyHandler({})`); тесты подставляют;
- `app_jwt(app_id: int, key: Path, *, openssl: Path, now: int) -> str` — заголовок и
  полезная нагрузка (`iat = now - 60`, `exp = now + 540`, `iss = app_id`) в base64url;
  подпись — `openssl dgst -sha256 -sign <key>` с данными на stdin (`run_command`, окружение
  `_SYSTEM_ENV`); путь ключа в argv допустим (секрет — содержимое, не путь);
- `installation_token(jwt, installation_id, *, repository_ids: list[int] | None,
  permissions: Mapping[str, str] | None, transport) -> Token` — `POST
  /app/installations/{id}/access_tokens`; `Token(value, expires_at, permissions,
  repository_ids)`; ответ сверяется: при заданных `repository_ids`/`permissions`
  вернувшиеся равны запрошенным, иначе `GitHubRefused("token_scope_mismatch")`;
- `installations(jwt, transport) -> list[Installation]` и
  `installation_repos(token, transport) -> list[int]`;
- `self_check(products, app_id, key, *, openssl, transport) -> list[str]` — репо каждой
  установки App ⊆ `repository_id` продуктов с этим `app_id`; иначе проблема
  `app_installation_outside_products: <id>` (решение 10);
- `call(method, path, token, body=None, *, transport) -> tuple[int, object]` — ответы 5xx
  и сетевые ошибки → `GitHubUnavailable`; 4xx → `GitHubRefused(status, message)`.

- [ ] **Step 1: Тесты** — `test_jwt_claims` (декодированные заголовок и нагрузка, подпись
  проверяется `openssl dgst -verify` тестовым ключом из `tests/df/fixtures/app/test-key.pem`,
  сгенерированным для теста); `test_token_scope_mismatch`; `test_no_env_proxy`
  (`HTTPS_PROXY` в окружении не используется); `test_self_check_outside_products`;
  `test_5xx_is_unavailable`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): GitHub App client for the publisher`

---

### Task 9: `publish-review`, `read-token`, сверка

**Files:**
- Create: `df/publisher/ops.py`
- Test: `tests/df/test_publisher_ops.py`

**Interfaces:**
- `MARKER = "<!-- df-publisher:v1 request_key={key} head_sha={sha} -->"`;
- `escape_body(body: str) -> str` — любое `<!--`, за которым (без учёта регистра и
  пробелов) следует одно из `df-publisher`, `approval`, `review/v1`, `envelope`, `stop`,
  `finalize`, заменяется на `&lt;!--`; маркер издателя добавляется последним абзацем;
- `publish_review(ctx, req) -> Response` — §5.4 п. 1–3: `GET /repos/{full_name}/pulls/{pr}`
  токеном установки (`pull_requests: write`): открыт, `base.repo.id == repository_id`,
  `head.sha == head_sha`, иначе `refused: pr_state`; намерение → `POST …/reviews` с
  `commit_id = head_sha`, `event` из `decision`; ответ: `commit_id == head_sha`,
  автор — бот App, иначе `unknown`;
- `reconcile_review(ctx, req, key) -> Response` — список ревью PR, ровно одно с маркером
  `request_key=key` от бота App: `commit_id`, решение совпадают → `done`; ни одного →
  `unknown`; больше одного → `unknown` с причиной;
- `read_token(ctx, req) -> Response` — `installation_token(repository_ids=[repository_id],
  permissions={"contents": "read", "pull_requests": "read"})`; ответ: `token`,
  `expires_at`, `permissions`, `repository_ids`; результат **не** сохраняется в `results/`.

Контекст `ctx` — `OpContext(layout, product: Product, app_key: Path, openssl: Path,
transport, now)`; `product` — из `products.toml` по `repository_id` (нет → `refused:
product_unknown`); перед каждой операцией — `self_check` (решение 10).

- [ ] **Step 1: Тесты** (фальшивый транспорт с записью вызовов):
  `test_publish_review_head_changed_refused`; `test_publish_review_base_repo_mismatch`;
  `test_publish_review_escapes_markers` (тело с `<!-- df-publisher:v1 request_key=x -->` и
  `<!--APPROVAL` → экранированы, ровно один настоящий маркер);
  `test_publish_review_reconciles_after_crash` (намерение есть, результата нет, на GitHub
  ревью с маркером → `done` без второго `POST`); `test_reconcile_no_marker_unknown`;
  `test_read_token_scope` (запрос с лишним репо невозможен по схеме; ответ GitHub с
  `contents: write` → `refused: token_scope_mismatch`); `test_read_token_not_journaled`
  (журнал, `intents/`, `results/` — без значения токена); `test_self_check_runs_first`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): publish-review and read-token`

---

### Task 10: `merge` 2a — маршрутизация вида и `Precheck`

**Files:**
- Create: `df/publisher/merge.py`
- Test: `tests/df/test_publisher_merge.py`

**Interfaces:**
- `Kind = Literal["candidate", "brief", "finalize", "stop"]`;
- `branch_kinds(head_ref: str, patterns: Mapping[str, str]) -> set[Kind]` — шаблоны из
  `contracts/approval-branches/v1/patterns.env` релиза: `APPROVAL_CANDIDATE_TEMPLATE`
  (`spec/{ws_id}-approve-{wave}-{step}-{attempt}`) и он же с
  `APPROVAL_FINALIZE_SUFFIX` (`-final`); вывод глоба — правило из шапки контракта (`{…}` →
  `*`, схлопывание по одному разделителю); `brief/*` → `brief`. Шаблона остановки в
  контракте нет — его добавляет часть B, поэтому в 2a вид `stop` не распознаётся (2b);
- `ENVELOPE_PATH = ("workstreams", "*", "approvals", "*.yaml")` и
  `STOP_PATH = ("workstreams", "*", "approvals", "stops", "*-g*.yaml")` — посегментные
  шаблоны (`*` не переходит `/`), сверяемые тестом с `governance.act_envelope.envelope_path`
  и `stop_path`;
- `route(head_ref, paths, patterns) -> Kind | Refused`:
  - candidate — ветка кандидата без суффикса finalize;
  - finalize — ветка с суффиксом **и** каждый путь PR — `ENVELOPE_PATH`;
  - brief — `brief/*`;
  - ветка вне шаблонов, finalize с иным путём, путь формы `STOP_PATH` при любой ветке —
    `refused: ambiguous_kind` (в 2a stop всегда так);
- `class Precheck(Protocol): def __call__(self, kind: Kind, ctx, pr) -> Refused | None`;
  `UNAVAILABLE: Precheck` — всегда `Refused("precheck_unavailable")`;
- `merge(ctx, req, precheck=UNAVAILABLE) -> Response` — читает PR (открыт, база,
  `head_sha`), полный список файлов PR (`GET …/pulls/{pr}/files`, постранично, предел
  3000 файлов → `refused: too_many_files`), `route`, `precheck`. Вызова `PUT …/merge`
  в 2a в коде нет: после `precheck is None` — `raise NotImplementedError("merge — этап 2b")`,
  а `UNAVAILABLE` не возвращает `None` никогда; тест сверяет, что строки `/merge` в модуле
  нет.

- [ ] **Step 1: Тесты** — маршрутизация: кандидат по шаблону, finalize (суффикс + только
  конверты), brief; файл остановки → `ambiguous_kind`; finalize-ветка с лишним путём или с
  `approvals/stops/…` → `ambiguous_kind`; ветка прежней арности
  `spec/ws-approve-1-2` → candidate (глоб формы, как в контракте);
  ветка вне шаблонов → `ambiguous_kind`; `test_path_templates_match_act_envelope` (сверка
  констант с `governance.act_envelope.envelope_path("ws", "n")` и `stop_path`);
  `test_merge_always_precheck_unavailable_in_2a`; `test_no_merge_call_in_module`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): merge kind routing (stage 2a, no merge)`

---

### Task 11: Журнал периметра SQLite, `record-violation`, `perimeter-status`, клиент

**Files:**
- Create: `df/publisher/perimeter.py`, `df/perimeter_client.py`
- Test: `tests/df/test_perimeter.py`, `tests/df/test_perimeter_client.py`

**Interfaces:**
- `DB = layout.perimeter_dir / "events.sqlite"`; `MAX_EVENTS = 10_000`;
  `BUSY_TIMEOUT_MS = 5000`;
- `open_db(layout) -> sqlite3.Connection` — `journal_mode=DELETE`,
  `synchronous=EXTRA`, `fullfsync=ON` и `checkpoint_fullfsync=ON` на macOS,
  `busy_timeout`; эффективные значения читаются `PRAGMA` обратно и сверяются (иначе
  `Unavailable("pragma: <имя>")`); `PRAGMA integrity_check` == `ok`; инварианты: известные
  состояния, каждая `transitions.event_id` существует, `registered` имеет `epoch`;
  число событий ≤ `MAX_EVENTS`; любое нарушение → `Unavailable`, файл не трогается;
- схема — таблицы §6.1 (`events`, `transitions`) с `UNIQUE(request_key)` и индексами
  `(root_host, root_repository_id, root_branch, state)`;
- `class EpochSource(Protocol): def head(self, root) -> Snapshot | Unavailable` —
  `Snapshot(sha: str, last_epoch: int)`; в 2a единственная реализация в коде —
  `NO_BINDING`, всегда `Unavailable("no_binding")`; поддельная — в тестах;
- `record_violation(conn, sender, req, key, epochs) -> Response` — нет `trust.toml` →
  `refused: no_binding`; `root_id` ≠ привязке или записи прогона → `refused:
  root_mismatch`; иначе вставка (`pending`, если журнал эпох недоступен; `registered(E)`
  с последней эпохой — если доступен); повтор по `request_key` → прежние `event_id` и
  состояние без новой эпохи;
- `perimeter_status(conn, req, epochs) -> Response` — нет привязки → `unavailable:
  no_binding`; голова ≠ `expected_snapshot_sha` → `stale_snapshot` с актуальным SHA;
  иначе в одной транзакции `pending` → `registered(E)` и вычисление `blocked`/`clear` по
  правилу блокировки; ответ несёт SHA снимка и последнюю эпоху;
- `restore_conservative(conn, roots, epochs) -> None` — консервативное `registered(E)`
  на каждый корень (источник `db_restore`);
- клиент `perimeter_client.status(run_id, root_id, trust_snapshot, *, attempts=3) ->
  Status` — вызов `sudo -n -u df-publisher /opt/df/libexec/df-publish perimeter-status`
  с запросом на stdin; на `stale_snapshot` — перезагрузка доверенного контекста
  (callback) и **новый** `request_id` для запроса с новым `expected_snapshot_sha`; повтор
  неизменённого запроса (сетевой сбой sudo) — тот же `request_id` (minor r7 №2).

- [ ] **Step 1: Тесты** — сценарии §6.1 «Тесты», возможные на поддельном
  `EpochSource`: обнаружение → эпохи недоступны → `pending` → эпохи доступны →
  `perimeter-status` → `registered(E)` → новая эпоха → `clear`; переход эпохи между
  обнаружением и регистрацией; повтор `record-violation` после сбоя между `COMMIT` и
  ответом (тот же `event_id`); два конкурентных разных события (процессы); два
  конкурентных `perimeter-status` с `pending` (один переход); аварийное завершение
  писателя (`os._exit` внутри транзакции) → следующее открытие → подтверждённое событие на
  месте; испорченный индекс (`writable_schema`) / дубль `UNIQUE` / неизвестное состояние
  → `unavailable`; восстановление из копии → консервативное событие; клиентский номер
  эпохи — поля нет в схеме; 10 000 событий — `perimeter_status` укладывается в
  `2.0` с на раннере (замер пишется в вывод теста); `test_pragmas_effective`
  (на macOS `fullfsync == 1`); `test_no_binding_refuses`; `test_client_new_request_id_after_stale`;
  `test_client_same_request_id_on_retry`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): perimeter event store and status ops`

---

### Task 12: `darkfactory setup-agent`

**Files:**
- Create: `df/setup_agent.py`
- Modify: `df/cli.py` (подкоманды `setup-agent app-key`, `setup-agent machine-token`,
  писатели), `df/launcher_entry.py` (`WRITER_COMMANDS`)
- Test: `tests/df/test_setup_agent.py`

**Interfaces:**
- `install_app_key(layout, app_id: int, source: Path) -> None` — чтение источника root
  как данных (≤ 16 КиБ, PEM `-----BEGIN (RSA )?PRIVATE KEY-----`), запись
  `secrets/app-<id>.pem` во временный файл `df-publisher` `0400` в том же каталоге,
  `fsync`, `rename`; источник не удаляется (решение человека);
- `install_machine_token(layout, source: Path) -> None` — токен (`github_pat_…` или
  `ghp_…`, одна строка ≤ 255 байт) в `~df-exec/.config/df/github-token`, `df-exec`,
  `0600`, каталоги `0700`;
- проверки после записи: `df-publish self-check` от root (решение 6) и
  `sudo -u df-exec /opt/df/libexec/df-launch df-exec check-github` — логин machine user
  печатается человеку; несовпадение с `products.toml` `machine_user` (если продукт
  зарегистрирован) — предупреждение.

- [ ] **Step 1: Тесты** — неверный PEM → отказ без записи; режимы и владельцы итоговых
  файлов (через подменённый `os.chown`); токен с переводом строки внутри → отказ; повтор —
  замена атомарная; секрет не попадает в журнал установки (`journal.jsonl`) и в вывод.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): setup-agent credentials`

---

### Task 13: `doctor --product P` — уровень 2

**Files:**
- Modify: `df/doctor.py`, `df/cli.py`
- Test: `tests/df/test_doctor.py` (дополнить)

Проверки уровня 2 (каждая — `Check` со статусами §8), порядок:
1. `roles.users` — `verify_service_user` для трёх ролей;
2. `roles.sudoers` — `validate` установленного файла + `effective_problems`;
3. `roles.libexec` — `wrapper_problems`;
4. `roles.isolation` — от каждой роли через `df-launch <role> check` (§8: проверки от
   имени ролей — через запускатель): UID/GID равны `accounts.toml`, `fds` пуст, `env` ⊆
   `ENV_ALLOW`; чтение `secrets/` и `~df-review` от `df-exec`, ключа App и токена от
   `df-review` — отказ ОС (`check` пробует открыть пути из фиксированного списка и
   сообщает `errno`);
5. `publisher.answers` — `perimeter-status` от обеих ролей через настоящий `sudo`
   (в 2a ожидаемый ответ — `unavailable: no_binding`; «отвечает» = корректный JSON
   ответа);
6. `publisher.foreign` — `df-review` → `df-publish merge` отвергнут sudo (код sudo, а не
   издателя);
7. `product.registered` — `P` есть в `products.toml`;
8. `models.access` — модельные роли `P`: актуальная проба (§3.6); в v1 —
   «не подтверждён» (решение 9).
Уровень 2 достигнут ⇔ уровень 1 и все 8 `ok`.

- [ ] **Step 1: Тесты** — каждая проверка с подменёнными фактами: ok / нарушено / нужен
  sudo; `doctor` без `--product` уровень 2 не считает; вывод «первая блокирующая причина».
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): doctor level 2 per product`

---

### Task 14: Root-приёмка §4.5 (CI, обе ОС, без GitHub)

**Files:**
- Create: `tests/df/roles_world.py`, `tests/df/test_acceptance_root_roles.py`
- Modify: `.github/workflows/df-stage1.yml` → переименовать в `df-acceptance.yml` **нет**:
  добавить шаг `stage2a` в тот же workflow после `stage1` (матрица та же), имя job
  `stage2a (<os>)`; `tests/df/conftest.py` (маркер `stage2a`)

Мир: `installed_world` этапа 1 → `darkfactory roles setup --operator runner` → фикстурный
`products.toml` (продукт `repository_id = 1`, App 1) → фикстурный ключ App (тестовый
PEM) → фикстурная проба для модельных ролей (для `df-run-start`). Teardown удаляет роли,
`/etc/sudoers.d/df`, `<R>`, проверяет, что записей нет.

Строки §4.5, которые закрывает Task 14 (каждая — отдельный тест с парным положительным
контролем):

| Попытка | Ожидание | Положительный контроль |
|---|---|---|
| `df-exec` читает ключ App, `~/.config/gh` оператора, `~df-review/` | `EACCES` | `df-exec` читает свой `work/` |
| `df-review` читает ключ App, токен machine user | `EACCES` | `df-review` читает свой `work/` |
| `df-review` пишет в `libexec/`, релиз, `/etc/df` (создание, запись, переименование, удаление, смена прав) | отказ ОС | то же в своём `work/` проходит |
| `df-review` → `df-publish merge`; `df-exec` → `publish-review` | отказ sudo (код 1, «not allowed»); то же **мимо sudoers** (запуск `df-publish` от `df-publisher` через `run_as` с подложным `SUDO_UID`) — отказ издателя `operation_not_allowed` | `df-exec` → `perimeter-status` — ответ |
| `sudo -E`, `SUDO_UID=0 sudo …`, лишний аргумент | отказ sudo / издателя | — |
| прогон на отозванном релизе | `df-run-start` — отказ с причиной; запрос с этим `run_id` — `refused: release_revoked` | прогон на `allowed` — запись есть |
| идентичный повтор `record-violation` / `perimeter-status` | без привязки: тот же отказ / заново вычисленный `unavailable` | — |
| тот же `request_id` с другим содержимым | `refused: request_id_reused` | первый запрос принят |

Плюс переносы этапа 1:
- `test_run_start_no_release_code_before_drop` — sentinel-интерпретатор релиза (UID-зависимый)
  не исполнен до сброса (настоящий `sudo df-run-start`);
- `test_launch_cwd_outside_builder_tree` — вызов `df-launch` из каталога внутри дерева
  сборщика (`<R>/opt/df/staging/...`, существующего в мире) идёт из `work/` роли, и
  наблюдатель этапа 1 (`tests/df/observe.py`) не видит открытия источника с `euid = 0`
  (в пределах `Observation.limits`, они переносятся в evidence).

- [ ] **Step 1–3:** мир, тесты, шаг workflow.
- [ ] **Step 4:** прогон CI на PR — обе ячейки зелёные; расхождение со спекой —
  находка и стоп (не подгонять `df` и тесты).
- [ ] **Step 5: Commit** `test(df): stage-2a root acceptance (§4.5 without merge)`

---

### Task 15: Живая приёмка на стенде, evidence, документация

**Files:**
- Create: `.github/workflows/df-stage2-live.yml`, `tests/df/live_stand.py`,
  `docs/evidence/<дата>-df-stage2a-acceptance.md`
- Modify: `README.md` (раздел этапа 2a: `roles setup`, `setup-agent`, `df-run-start`,
  ограничения), `CLAUDE.md` (строка `df/`), `TODO.md`

Workflow: `on: workflow_dispatch` (только), `environment: df-stage2-live`, матрица
macos-15 / ubuntu-24.04, `if: github.ref == 'refs/heads/master'`. Шаги: подготовка хоста
и установка этапа 1 (как `live_install.py`) → `roles setup` → `products.toml` стенда из
переменных → `setup-agent app-key`/`machine-token` из секретов (файлы во `RUNNER_TEMP`,
`0600`, удаляются шагом `always()`) → `live_stand.py`.

Строки, которые закрывает Task 15 (живой GitHub):
1. machine user (`df-exec`, его токен) открывает PR на стенде (ветка
   `stand/<run_id>`, один файл);
2. `df-review` → `publish-review` `comment` на этот PR → ревью от бота App с маркером на
   `head_sha`; повтор того же запроса → тот же `review_id`, второго ревью нет;
3. `df-review` → `read-token` → `git --config-env=http.extraHeader=DF_READ_AUTH clone`
   приватного стенда проходит; этим токеном `git push`, `POST …/reviews`, `PUT …/merge` —
   отказ GitHub (403/404);
4. во время чтения `ps -axww -o args` (снимок каждые 100 мс) не содержит токена;
   журнал издателя, `/var/lib/df/publisher/**`, `/var/log/sudo-io` (если есть) и
   `.git/config` клона — без токена;
5. `df-exec` → `publish-review` — отказ sudo; `df-review` → `merge` — отказ sudo;
6. `merge` от `df-exec` на PR стенда → `refused: precheck_unavailable` (решение 1;
   строка закрывает «маршрутизация работает живьём», не мерж);
7. уборка: PR закрыт, ветка удалена (от machine user).

Evidence: id прогонов, `review_id`, ответы издателя (без секретов), снимки `ps`, итог по
строкам §4.5 с пометкой «2b» у трёх строк мержа.

- [ ] **Step 1–3:** workflow, скрипт, документация.
- [ ] **Step 4:** владелец запускает `workflow_dispatch` (одобрение environment) — обе
  ячейки зелёные; evidence заполняется по фактическому прогону.
- [ ] **Step 5: Commit** `test(df): stage-2a live stand acceptance; docs`

---

## Вне этого плана

- Этап 2b: строки мержа §4.5, `act_premerge` как `Precheck`, поверхность candidate D16,
  полный состав PR (r7 №1) — после части B (M0 steward).
- Привязка `df-trust`, проверка установок App на источниках доверия, живой
  `perimeter-status` — этап 3.
- Таблица конвейеров `df-launch run` и `df-review-run` с реальным ревью — этап 3/4.
- Имитация потери питания для SQLite (§6.1 «Платформенные предпосылки») — вне v1, как
  и для установки (§3.3); приёмка различает только падение процесса.
- `releases.toml` `revoked` пишет `darkfactory update` (этап 4); в 2a отзыв в приёмке
  делает фикстура.
