# Установщик `df` (команда `darkfactory`), этап 2a — роли и изоляция: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sudo darkfactory roles setup` создаёт роли ОС `df-exec`, `df-review`,
`df-publisher`, файл `/etc/sudoers.d/df` и неизменяемые обёртки `/opt/df/libexec/`.
Человек регистрирует прогон командой `darkfactory run start` (она вызывает
`sudo /opt/df/bin/df-run-start` из `/`, решение 12), агенты обращаются к
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
3. **Издатель, `df-run-start` и вход `df-launch` — из административной среды** (уточнение
   спеки rev 9 §4.1, §5.7, на подпись владельца). Все обёртки `libexec/` и `df-run-start`
   запускают **существующий вход команды** `/opt/df/bin/darkfactory-entry.py` на
   административном Python с фиксированными подкомандами (`publisher serve <op>`,
   `run start …`, `launch exec <role> <op>`): вход уже проверяет `admin/<id>` по
   `admin_digest` до импорта `df` (§3.9 п. 3), и замороженный договор входа (`df.cli.main`
   с `mode`, `read`, `lock_fd`, `layout`) не меняется. Новых входов нет (r1 N3). Основание:
   ключ App — самый ценный секрет установки, а интерпретатор релиза собран `df-build`, у
   которого были сеть и запись в дерево (§3.2); принцип §3.9 распространяется на держателя
   ключа App. Следствие — издатель только на stdlib: подпись JWT RS256 —
   `openssl dgst -sha256 -sign` утверждённым CLI `openssl` (необязательный ключ
   `install.toml`, обязателен для `roles setup`; политика доверенных путей как у `git`),
   данные — через stdin (`platform.run_with_input`, r1 N14). Код релиза исполняется только
   после `launch exec`: административный код от роли закрывает дескрипторы, сверяет UID,
   меняет `cwd` и окружение и лишь затем делает `execve` интерпретатора релиза (r1 N4).
4. **Записи этапа 2a — отдельная команда-писатель `darkfactory roles setup`**, а не маршрут
   установки: роли, `publisher.toml`, sudoers, обёртки `libexec/`, `df-run-start`, каталоги
   `/var/lib/df/{runs,publisher,perimeter}`. Обёртки не зависят от релиза (разрешают
   `current` при каждом вызове), поэтому маршрут активации их не трогает; `roles setup`
   повторяем и сверяет написанное с ожидаемым текстом. Автомат установки этапа 1 не меняется.
   Оговорка (r2): текст обёрток содержит путь административного Python релиза `current`.
   `darkfactory install` при смене этого пути печатает «обёртки ролей устарели:
   выполните darkfactory roles setup» (код 0 — установка прошла), а `doctor`
   (`roles.libexec`) показывает «нарушено» до повторного `roles setup`; тест
   `test_install_new_admin_python_marks_wrappers_stale`.
5. **`products.toml` в 2a не пишется командой.** Регистрация продукта — `setup-repo`
   (этап 3, §6.4 п. 1). Приёмка 2a пишет `products.toml` фикстурой от root; издатель и
   `doctor --product` его читают. Без `products.toml` издатель отказывает
   `refused: product_unknown`.
6. **Учётные данные — `darkfactory setup-agent`** (§1, имя из решений 12.10):
   `setup-agent app-key --app-id N --file PATH` кладёт ключ в
   `/etc/df/secrets/app-<N>.pem` (`df-publisher`, `0400`, каталог `0700`);
   `setup-agent machine-token --file PATH` — в `~df-exec/.config/df/github-token`
   (`df-exec`, `0600`); запись идёт через дескрипторы каталогов от закреплённого
   `role_home`, без следования ссылкам (r1 N9). Проверки: machine user — `GET /user` его
   токеном от `df-exec` через `df-launch check-github`; App — **административная операция
   издателя `self-check`**: root вызывает `sudo -n -u df-publisher /opt/df/libexec/df-publish
   self-check`, отправитель — `SUDO_UID = 0`, который издатель принимает **только** для
   `self-check` (константа кода, не строка `publisher.toml`); модельные роли её вызвать не
   могут — правила sudoers нет, а `SUDO_UID` ставит сам sudo (r1 N7).
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
   токеном machine user, только `df-exec`). `run` (`run_id` — stdin) разрешает релиз **из
   записи прогона**, а не из `current` (r1 N5), проверяет актуальность проб от роли и
   отказывает `конвейер — этап 3`: таблицы конвейеров в релизе 2a нет.
9. **Уровень 2 в v1 недостижим, пока харнессы не подтверждены** (следствие решения владельца
   24.10, §3.6 «Состояние v1»): для модельных ролей нет актуальной пробы. `doctor --product
   P` показывает уровень 2 «не достигнут: доступ к моделям не подтверждён», если остальные
   проверки уровня 2 зелёные. Приёмка 2a — таблица §4.5, а не достижение уровня 2.
10. **Проверка установок App в 2a**: установки App ⊆ репо из `products.toml`; иначе
    `refused: app_installation_outside_products`. Обход полный: все страницы установок и
    репозиториев; ошибка любой страницы — `unknown`/«данные недоступны», не успех (r1).
    Проверка «установка на `approval-policy` или `df-trust` — нарушение» (§6.1) требует
    привязки и в 2a — «не проверено (этап 3)» в evidence операции.
11. **Перенос из плана этапа 1** («Вне этого плана»): роли и проверки от их имени (Tasks 1,
    5, 13, 14); `fullfsync` SQLite (Task 11); minor r7 №2 — новый `request_id` после
    `stale_snapshot` (Task 11, клиент); пин моделей прогона и событие смены при `resume`
    (Task 6); `df-run-start` до сброса привилегий не исполняет код релиза (Task 6, тест с
    sentinel); рабочий каталог запуска от роли — вне дерева сборщика, обязанность
    `df-launch` (Task 5, тест с наблюдателем). Minor r7 №1 (полный состав candidate-PR) —
    2b (решение 7).
12. **Человеческие входы — непривилегированные команды `darkfactory`** (r1 N4). На macOS
    `sudo` открывает рабочий каталог вызывающего с `euid = 0` (evidence этапа 1 §9), и
    после старта `sudo` это не исправить. Поэтому штатные входы человека —
    `darkfactory launch <role> <op>` и `darkfactory run start|resume …`: команда-читатель от
    пользователя человека делает `chdir("/")`, закрывает дескрипторы > 2 и только затем
    `execve("/usr/bin/sudo", …)` с фиксированным argv. Прямой вызов `sudo …/df-launch` из
    произвольного каталога — неподдерживаемый путь (README), как прямой вызов кода релиза
    от root на этапе 1.
13. **Проверка `sudo -l`: человек и роли различаются** (r1 N1, N2). Для `df-build` —
    по-прежнему ни одного правила. Для `df-exec`/`df-review` — эффективный набор правил
    **равен** сгенерированному (лишняя команда, `SETENV`, расширенный `RunAs` — нарушение).
    Для `df-publisher` — ни одного правила. Для оператора — сгенерированные правила
    **присутствуют**; прочие его права (административные) не проверяются: человек и так
    держит `sudo` (§2.1). `platform.account_problems` теряет проверку sudo; её делает
    `sudoers.policy_problems(name)` по таблице выше.
14. **Схема ответа издателя** (r1 N15, правка спеки rev 9 §5.5): `outcome ∈ {done, refused,
    unknown, stale_snapshot}`; `perimeter-status` с исходом `done` несёт
    `state ∈ {blocked, clear, unavailable}` и `reason` (у `unavailable`: `no_binding`,
    `busy`, `integrity`, `epochs_unavailable`, `lost`, `not_initialized`,
    `restore_pending`; поля снимка — `null`). `unavailable` — состояние периметра, а не
    исход операции.
15. **Потеря питания SQLite — вне v1, как у установки** (r1 N16; **решение владельца
    26.10**; меняет требование спеки §6.1 «Платформенные предпосылки»). Приёмка 2a
    проверяет эффективные прагмы и восстановление после падения процесса; сохранность
    подтверждённого `COMMIT` при потере питания утверждается только по документации SQLite
    для выбранных прагм и не испытывается. Спека rev 9 вносит это в §0 «Что спека не
    обещает» и §6.1. Альтернатива — VM-сценарий с жёстким сбросом в Task 14 (стоимость:
    отдельная инфраструктура, на hosted-раннерах GitHub недоступно).

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

1. **`SUDO_UID` при подмене окружения.** `sudo -E` и лишний аргумент после операции —
   отказ sudo. `SUDO_UID=0 sudo …` от `df-exec` — sudo сам ставит `SUDO_UID` вызывающего,
   поэтому разрешённая операция **проходит**, а evidence издателя называет отправителем
   `df-exec`, не root; `self-check` такой вызов не получает. Если правило sudoers всё же
   сработало для чужой операции (испорченный файл), издатель отвергает сам. Тест — Task 7
   (`test_sender_*`), приёмка — Task 14.
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
| 5 | `launch exec` до `execve` релиза; операции роли; вход `darkfactory launch` | 4 |
| 6 | `df-run-start`: реестр прогонов, сброс привилегий, пин моделей | 2, 4, 5 |
| 7 | каркас издателя: запрос, отправитель, прогон, резервирование id, журнал | 2, 4, 6 |
| 8 | клиент GitHub App: JWT через `openssl`, токен установки, самопроверка | 7 |
| 9 | `publish-review`, `read-token`, сверка | 7, 8 |
| 10 | `merge` 2a: маршрутизация вида, интерфейс `Precheck` | 7, 8 |
| 11 | журнал периметра SQLite; `record-violation`, `perimeter-status`; клиент | 7 |
| 12 | `setup-agent`: ключ App, токен machine user (запись через дескрипторы), проверки | 4, 5, 7, 8 |
| 13 | `doctor --product P`: уровень 2 | 3–12 |
| 14 | root-приёмка §4.5 (CI, обе ОС, без GitHub) | 1–13 |
| 15 | живая приёмка на стенде, evidence, документация | 14, шаги H |

Шаги человека (H, параллельно Tasks 1–14, до Task 15):
- **H1.** Создать приватный `df-roles-probe-private`, ruleset как у `df-roles-probe`
  (id 24926051), `ai-prosto` — write.
- **H2.** App 5285716: выпустить новый ключ, старый удалить; установка App — **только**
  `df-roles-probe-private` (иначе `self-check` блокирует стенд, решение 10). Чеклист —
  `_cowork_output/2026-10-13-df-installer/stage2a-human-steps.md`.
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
  **Проверка sudo выносится из `account_problems`** (решение 13): функция проверяет только
  идентичность, группы, оболочку и домашний каталог; `verify_build_user` добавляет
  `sudoers.policy_problems("df-build")` (ни одного правила — как сейчас), роли — свою
  политику (Task 3). Тест: `test_account_problems_ignores_sudo` и
  `test_verify_build_user_still_refuses_rules` (фикстура `sudo-l/linux-some.out`).
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
  - `PublisherConfig(senders: Mapping[int, frozenset[str]], api_base: str)` — UID →
    операции; `api_base` — по умолчанию `"https://api.github.com"`; иное значение
    допустимо только вида `http://127.0.0.1:<порт>` (r2 N6: межпроцессный транспорт
    offline-приёмки), файл root `0644` — модельные роли его не меняют; каждый ответ
    издателя несёт `evidence.api_base`, а `doctor` при нештатном значении даёт
    `github.app` и `github.machine_user` «нарушено: нештатный API» — уровень 2 с ним
    недостижим;
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
  - `policy_problems(name: str, *, layout: Layout, operator: str) -> list[str]` — разбор
    `sudo -n -l -U <name>` (решение 13): `df-build`, `df-publisher` — ни одного правила;
    `df-exec`, `df-review` — множество (RunAs, теги, команда с аргументами) **равно**
    сгенерированному для этой роли; оператор — сгенерированные правила оператора
    **входят** в его множество, остальное не проверяется;
  - `io_log_problems(layout) -> list[str]` — **поддерживаемый контракт I/O-журнала**
    (r2 N2): выходные потоки команды `df-publish` не журналируются. Проверка по
    эффективным `Defaults`, которые `sudo -l -U <роль>` печатает для `df-exec` и
    `df-review` (разделы «Matching Defaults entries» и «Runas and Command-specific
    defaults»): для команды `df-publish` последним значением каждого из `log_output`,
    `log_stdout`, `log_stderr`, `log_ttyout` должно быть «выключено»; любое упоминание
    `log_servers`, а также `log_output`-семейства с неопределимой областью действия или
    нераспознанной строкой — «нарушено: конфигурация I/O-журнала не проверяема»
    (не успех). `log_input`/`log_stdin`/`log_ttyin` не мешают (stdin издателя — запрос,
    секретов в нём нет). Поведенческая проверка — в приёмке (Task 14), не в `doctor`.

Текст (имена путей — от `layout`; `<op>` — оператор, имя пользователя ОС человека):

```
# generated by darkfactory roles setup; do not edit
Defaults:df-exec, df-review env_reset, !setenv
Defaults!/opt/df/libexec/df-publish !log_output
<op> ALL=(df-exec) NOPASSWD: NOSETENV: /opt/df/libexec/df-launch df-exec check, \
    /opt/df/libexec/df-launch df-exec check-github
<op> ALL=(df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-launch df-review check
df-exec ALL=(df-review) NOPASSWD: NOSETENV: /opt/df/libexec/df-review-run ""
df-exec ALL=(df-publisher) NOPASSWD: NOSETENV: /opt/df/libexec/df-publish merge, \
    /opt/df/libexec/df-publish record-violation, \
    /opt/df/libexec/df-publish perimeter-status
df-review ALL=(df-publisher) NOPASSWD: NOSETENV: \
    /opt/df/libexec/df-publish publish-review, /opt/df/libexec/df-publish read-token, \
    /opt/df/libexec/df-publish record-violation, \
    /opt/df/libexec/df-publish perimeter-status
```

Команда с перечисленными аргументами разрешает ровно их; команда без аргументов — любые,
поэтому `df-review-run` записан с `""` (только без аргументов, r1 N13). `df-launch df-exec
run` в sudoers нет: его вызывает `df-run-start` после сброса привилегий, не через sudo.
`df-run-start` в sudoers не пишется: человек вызывает его обычным `sudo` (§4.1 последняя
строка) через `darkfactory run` (решение 12). `df-publish self-check` — только root,
правила нет.

- [ ] **Step 1: Тесты** — точный текст (`test_render_exact` со снимком
  `tests/df/fixtures/sudoers/expected.txt`, корень `/`); `<R>`-корень даёт пути `<R>/opt/df/…`;
  нет `*`, `ALL` справа только в позиции хоста; `operator` из `ROLE_USERS ∪ {"root",
  "df-build"}` → `ValueError`; недопустимое имя (пробел, `,`, `:`, `=`, `\`) → `ValueError`;
  `validate` на испорченном тексте (подменённый `run_command`) → проблемы; `install` при
  проблемах не вызывает `rename`; `policy_problems` на снимках вывода `sudo -l -U` обеих
  платформ (`tests/df/fixtures/sudoers/sudo-l-*.txt`): у роли лишний тег `SETENV`, лишняя
  команда, `RunAs` шире → проблема с именем; оператор с `(ALL) NOPASSWD: ALL` плюс
  сгенерированные правила — без проблем; оператор без правила `df-launch` — проблема;
  `df-publisher` с любым правилом — проблема.
- [ ] **Step 2: FAIL.** **Step 3: Реализация** (`run_command` с `_SYSTEM_ENV`; путь
  `visudo` — `/usr/sbin/visudo`). **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(df): sudoers rules for stage-2 roles`

---

### Task 4: Обёртки `libexec/`, подкоманды входа, `darkfactory roles setup`

**Files:**
- Create: `df/libexec.py` (тексты и проверка обёрток), `df/roles_setup.py`
- Modify: `df/paths.py` (`libexec`, `run_start`, `runs_dir`, `publisher_dir`,
  `perimeter_dir`, `secrets_dir`), `df/cli.py` (подкоманды `roles setup` — писатель;
  `publisher serve <op>`, `run start|resume`, `launch exec <role> <op>`,
  `launch <role> <op>` — читатели), `df/launcher_entry.py` (`WRITER_COMMANDS +=
  (("roles", "setup"), ("setup-agent",))`)
- Test: `tests/df/test_libexec.py`, `tests/df/test_roles_setup.py`, `tests/df/test_cli.py`

**Interfaces:**
- Produces:
  - `Layout.libexec = opt/"libexec"`, `Layout.run_start = bin_dir/"df-run-start"`,
    `Layout.runs_dir = var/"runs"`, `Layout.publisher_dir = var/"publisher"`,
    `Layout.perimeter_dir = var/"perimeter"`, `Layout.secrets_dir = etc/"secrets"`;
  - `libexec.WRAPPERS = ("libexec/df-launch", "libexec/df-review-run",
    "libexec/df-publish", "bin/df-run-start")` — пути относительно `opt`;
  - `libexec.wrapper_text(layout: Layout, name: str, admin_python: Path) -> str`;
  - `libexec.wrapper_problems(layout: Layout, admin_python: Path) -> list[str]` — каждый
    файл: обычный, root, `0755`, текст равен ожидаемому, путь проходит
    `platform.trusted_path_problems`;
  - `roles_setup.run(layout: Layout, *, operator: str, snap: Snapshot) -> None` — шаги
    под блокировкой писателя через `journal.run_steps`: `roles` (`ensure_roles`) →
    `dirs` → `publisher-config` (`expected_publisher` → `/etc/df/publisher.toml`) →
    `perimeter-init` (`create_db` от `df-publisher` через `run_as`, Task 11) →
    `libexec` → `sudoers` (`render`, `install`) → `verify` (`wrapper_problems`,
    `policy_problems` для пяти имён).

**Обёртки** (решение 3). Каждая — `sh`-скрипт root `0755` из одной строки
`exec <административный Python записи current> -I -B -S /opt/df/bin/darkfactory-entry.py
<подкоманда> "$@"`; вход — тот самый `darkfactory-entry.py` этапа 1, без изменений
договора. Путь административного Python — из записи `current` в момент `roles setup`;
`install` нового релиза с другим Python делает обёртки устаревшими, и `doctor`
(`roles.libexec`) это показывает — `roles setup` их переписывает.

| Обёртка | Подкоманда входа | От чьего имени исполняется |
|---|---|---|
| `libexec/df-launch` | `launch exec` | роль (sudo сменил UID) |
| `libexec/df-review-run` | `launch exec df-review review` | `df-review` |
| `libexec/df-publish` | `publisher serve` | `df-publisher` |
| `bin/df-run-start` | `run start` / `run resume` (по первому аргументу) | root |

Подкоманды входа — читатели (`mode_of` → `reader`), блокировку писателя не берут.
Разбор `df.cli` принимает ровно форму обёртки: `publisher serve` — один аргумент
операции из шести (`OPERATIONS` + `self-check`); `launch exec` — `<role> <op>`; лишний
аргумент — отказ 2 до чтения stdin.

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
  текст, ссылка вместо файла, устаревший путь Python); `test_roles_setup_steps_order`
  (при провале `sudoers` файл не установлен, `verify` не выполнялся);
  `test_roles_setup_idempotent`; `test_roles_setup_requires_openssl`;
  `test_cli_roles_setup_is_writer` (`mode_of(["roles", "setup"]) == "writer"`, без root —
  отказ до блокировки); `test_cli_service_commands_are_readers`;
  `test_publisher_serve_extra_arg_refused_before_stdin`.
- [ ] **Step 2: FAIL.** **Step 3: Реализация.** `install.toml` — необязательный ключ CLI
  `openssl` (в `config.REQUIRED_CLI` не входит; `roles setup` требует). `--operator`
  по умолчанию — `SUDO_USER`; без него и без флага — отказ «укажите --operator»; имя
  оператора из `ROLE_USERS ∪ {"root", "df-build"}` — отказ.
- [ ] **Step 4: PASS + `uv run pytest tests/df -q`.**
- [ ] **Step 5: Commit** `feat(df): roles setup and libexec wrappers`

---

### Task 5: `launch exec` (`df-launch`) и человеческий вход `darkfactory launch`

**Files:**
- Create: `df/launch.py` (административная часть: от роли до `execve` релиза),
  `df/launch_release.py` (код релиза: операции роли)
- Modify: `df/cli.py`
- Test: `tests/df/test_launch.py`, `tests/df/test_launch_release.py`

**Interfaces:**
- `launch.ENV_ALLOW = ("HOME", "PATH", "LANG", "TZ")` (+ переменные харнесса из таблицы
  релиза; в 2a — пусто);
- `launch.role_env(layout, role) -> dict[str, str]` — `HOME` = `role_home(role)`,
  `PATH` = `"/usr/bin:/bin"`, `LANG` = `"C.UTF-8"`, `TZ` = `"UTC"`;
- `launch.role_cwd(layout, role) -> Path` — `role_home(role)/"work"`;
- **Снимок несёт весь реестр** (r4 N1): `df/state.py` — поле `Snapshot.releases:
  ReleasesConfig | None` (разобранный `RawSnapshot.releases_text`, тот же текст, из
  которого уже берётся `entry`); `parse_snapshot` его сохраняет; договор входа
  `launcher_entry → cli.main` не меняется. Тест `test_service_commands_reread_nothing`
  (механизм `test_main_rereads_nothing` этапа 1 для `launch exec`, `publisher serve`,
  `run start|resume`): после получения снимка чтение `releases.toml`, `install.toml`,
  `current` запрещено; `current = B`, прогон на `allowed` A → A; A отозван в снимке →
  отказ; правка файлов после снимка не смешивает записи;
- `launch.release_for(layout, op, snap, stdin) -> tuple[str, Path, str | None]` — для
  `check`, `check-github` — `snap.release_id` (один снимок входа), `run_id = None`; для
  `run` и `review` — `run_id` из stdin (≤ 256 байт, `[A-Za-z0-9._-]+`) → запись
  `/var/lib/df/runs/<run_id>.json` (root, без ссылок) → её `release_id`; запись релиза в
  `snap.releases` `allowed` и digest совпадает, иначе отказ с причиной
  (`release_revoked`, `release_unknown`) (r1 N5);
- `launch.exec_main(argv, *, layout, snap) -> int` — `argv = [role, op]`, порядок:
  1. `os.closerange(3, …)` — до чтения stdin и любых файлов;
  2. фактический UID/EUID/GID == записи роли в `accounts.toml`, иначе отказ 2;
  3. `os.chdir(role_cwd)`; окружение — `role_env` (`os.environ` очищается);
  4. `release_for`;
  5. `os.execve(<release>/venv/devtools/bin/python, [python, "-I", "-B",
     <release>/src/devtools/df/launch_release.py, role, op, release_id, run_id or "-"],
     role_env)` — пути релиза этапа 1 (`venv/<repo>`, `src/<repo>`, r1 N3); `run_id`
     доходит до кода релиза (r2 N1). Под `-I` каталог скрипта не в `sys.path`:
     `launch_release.py` сам добавляет `<release>/src/devtools` (вычисленный из
     `__file__`) первой строкой;
- `launch.human_main(argv) -> int` — `darkfactory launch <role> <op>` (решение 12):
  `chdir("/")`, `closerange(3, …)`, затем `os.execv("/usr/bin/sudo", ["sudo", "-n",
  "-u", role, "<libexec>/df-launch", role, op])`;
- `launch_release.main(argv) -> int` — `argv = [role, op, release_id, run_id | "-"]`;
  `release_id` сверяется с каталогом файла (`Path(__file__).parents[3].name`), иначе
  отказ; операции 2a:
  - `check` → JSON `check_report()`;
  - `check-github` (только `df-exec`) → JSON `{login, products: {repository_id:
    {full_name, permissions}}}`: `GET /user` и `GET /repos/{full_name}` для каждого
    продукта `products.toml` (читается от роли; `/etc/df` — `0644`) с `machine_user`
    этой установки; токен — заголовком из `~/.config/df/github-token` (r2 N3);
  - `run` (`df-exec`) → `RunRecord` и события `runs/<run_id>.events.jsonl` (действующий
    пин — исходный плюс принятые `model_change`, не текущий `models.toml`) →
    `probe.pin_ready(layout, role, pin)` для модельных ролей **своего** контекста
    (`interview`, `author`) → отказ «конвейер — этап 3»;
  - `review` (`df-review`, через `df-review-run` со stdin `run_id`) → то же для ролей
    контекста `df-review` (`reviewer`, `executor-review`, `edge-check`, `judge`) → отказ
    «конвейер — этап 3». Свежесть фактов каждой роли проверяется от её UID (r2 N1);
- `probe.pin_ready(layout, role, pin: ModelPin) -> ProbeStatus` (новое в `df/probe.py`) —
  **допуск без запуска харнесса**: `executable_target` → SHA-256 → профиль адаптера
  (неизвестный или «изоляция не подтверждена» → «не поддерживается»), managed-контекст
  (`managed_context`), отпечаток авторизации роли; затем `probe_status` последней записи
  против этих фактов и против пина (`config_hash`, версия харнесса, model ID). В v1 —
  всегда «не поддерживается: изоляция не подтверждена»;
- `launch_release.check_report() -> dict[str, object]` — `nonce` (128 случайных бит),
  `uid`, `euid`, `gid`,
  `groups`, `env` (имена), `cwd`, `fds` (открытые дескрипторы > 2), `denied` (результат
  попыток открыть фиксированный список чужих путей: `errno` по каждому).

- [ ] **Step 1: Тесты** — `test_role_env_ignores_caller` (`SSH_AUTH_SOCK`, `GH_TOKEN`,
  `GITHUB_TOKEN`, `DYLD_INSERT_LIBRARIES`, `LD_PRELOAD`, `PYTHONPATH` — ни одной);
  `test_exec_main_order` (подменённые `os.*`: `closerange` → проверка UID → `chdir` →
  чтение stdin → `execve`, именно в этом порядке); `test_exec_main_refuses_uid_mismatch`;
  `test_release_for_run_uses_record_not_current` (запись A, `current` = B → A);
  `test_release_for_run_revoked` (запись A отозвана, `current` = B allowed → отказ);
  `test_execve_paths_match_stage1_layout` (пути от `build.py`: `venv/devtools`,
  `src/devtools`); `test_human_main_chdir_before_sudo`; `test_check_github_only_df_exec`;
  `test_launch_release_refuses_foreign_release_id`; `test_run_uses_record_pins_not_current_config`
  (после регистрации `models.toml` изменён — проверяется пин записи);
  `test_run_two_records_same_release_distinct` (две записи одного релиза с разными пинами —
  проверяется пин своей записи); `test_run_resume_accepted_change_applies`;
  `test_review_checks_only_df_review_roles` (устаревшая авторизация только у
  `reviewer` → отказ `review`, `run` её не видит); `test_pin_ready_unsupported_harness`
  (харнесс без подтверждённой изоляции → «не поддерживается», харнесс не исполняется).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): df-launch (admin pre-exec) and release role ops`

---

### Task 6: `df-run-start`

**Files:**
- Create: `df/run_start.py`
- Modify: `df/cli.py` (`run start|resume` за входом; человеческая форма
  `darkfactory run start|resume` — по решению 12)
- Test: `tests/df/test_run_start.py`

**Interfaces:**
- Consumes: `RunRecord`, `ModelPin` (Task 2), `models.resolve`, `models.config_hash`,
  `probe.read_record` (только как данные из `/var/lib/df/doctor/probes/`),
  `stage2_config.load_trust`.
- Produces:
  - `main(argv, *, layout, snap) -> int` — `run start --product ID` и
    `run resume RUN_ID [--accept-model-change]`; требует `euid == 0`;
  - `human_main(argv) -> int` — `darkfactory run …`: `chdir("/")`, `closerange`,
    `execv("/usr/bin/sudo", ["sudo", "<opt>/bin/df-run-start", *argv])` (пароль sudo
    допустим, `-n` нет);
  - `new_run_id() -> str` — `f"run-{utc:%Y%m%dT%H%M%SZ}-{secrets.token_hex(8)}"`;
  - `register(layout, rec: RunRecord) -> None` — `/var/lib/df/runs/<run_id>.json`,
    root `0644`, `O_EXCL | O_NOFOLLOW`, `fsync` файла и каталога;
  - `pins_from_data(layout, product, cfg) -> dict[str, ModelPin]` — выбор из
    `models.toml` и последняя запись пробы роли из `/var/lib/df/doctor/probes/`
    **как данные**; нет записи — `harness_version = "не проверено"`,
    `actual_model = "не сообщён"`;
  - `model_change(old: RunRecord, new: Mapping[str, ModelPin], step: int) -> dict | None`
    — событие `{run_id, role, before, after, from_step}`; `from_step` — аргумент
    `--from-step N` команды `resume` (обязателен вместе с `--accept-model-change`);
    пишется в `/var/lib/df/runs/<run_id>.events.jsonl` (root, `O_APPEND`, `fsync`).

**Актуальность проб (r1 N6).** `df-run-start` до сброса читает только `/etc/df`,
`releases.toml`, свой реестр и записи проб в `/var/lib/df/doctor/probes/` — всё root.
Актуальность пробы зависит от фактов в домашнем каталоге роли (отпечаток авторизации), а
его до сброса читать нельзя; поэтому её проверяет `launch_release` операции `run` **от
роли**, перед любым запуском харнесса: `probe.probe_status` с текущими фактами роли; не
`подтверждён` — отказ «доступ к моделям не подтверждён: <роль>: <причина>». В v1 харнессы
не поддерживаются (решение 9), поэтому прогон регистрируется и всегда останавливается на
этой проверке; запись пробы из фикстуры приёмки этого не меняет — `probe_status`
сверяет текущие факты, а не запись.

Порядок (§5.8): данные → запись прогона → сброс привилегий (`setgroups([gid])`,
`setgid`, `setuid` значениями `accounts.toml`; проверка `getuid() == geteuid() == uid`)
→ `os.execv(<libexec>/df-launch, ["df-launch", "df-exec", "run"])` с `run_id` в stdin
(канал `os.pipe`, записан до `exec`). `current` после регистрации не читается: релиз
`run` берёт из записи (Task 5). `resume` — запись прежняя (релиз прежний); отозванный
релиз записи — отказ с причиной даже при `allowed` `current`.

- [ ] **Step 1: Тесты** — `test_register_exclusive`; `test_no_role_home_read_before_drop`
  (подменённые `open`/`os.stat`/`os.listdir`: ни одного пути под `role_home`, ни одного
  файла продукта до `setuid`); `test_release_code_not_executed_before_drop` — **перенос
  этапа 1**: в фикстурном релизе `venv/devtools/bin/python` — sentinel, пишущий файл при
  `euid == 0`; `run start` до сброса его не запускает (root-тест Task 14 повторяет это
  настоящим `sudo`); `test_resume_model_change_needs_flag_and_step`;
  `test_resume_revoked_release_refused_even_if_current_allowed`;
  `test_revoked_release_refused_at_start`; `test_root_id_recorded`;
  `test_pins_from_data_never_reads_role_home`; `test_human_main_chdir_before_sudo`.
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
- `Outcome = Literal["done", "refused", "unknown", "stale_snapshot"]` (решение 14);
  `Response(schema="df/publisher-response/v1", outcome, reason: str | None,
  result: Mapping[str, object], evidence: Mapping[str, object])`; вывод — одна строка
  JSON; `evidence.response_nonce` — 128 случайных бит, порождается издателем на каждый
  ответ и есть **только** в stdout (не в запросе, не в журнале издателя) — маркер
  поведенческой проверки I/O-журнала sudo (r3 N3); `result` у `perimeter-status` — `{state: blocked|clear|unavailable, reason,
  snapshot_sha, last_epoch}`; `parse_response(bytes) -> Response` — для клиентов
  (round-trip-тест всех исходов и состояний);
- `main.ADMIN_OPERATIONS = frozenset({"self-check"})` — отправитель root (`SUDO_UID == 0`),
  константа кода, не строка `publisher.toml` (решение 6);
- `main.sender(publisher_cfg, op, *, uid=os.getuid(), env=os.environ) -> int` —
  фактический UID == `df-publisher` (`accounts.toml`); `SUDO_UID` — каноническое
  десятичное число (`0|[1-9][0-9]*`); `op ∈ ADMIN_OPERATIONS` ⇔ `SUDO_UID == 0`; иначе
  `SUDO_UID` из таблицы и `op` ∈ его операций; иначе `Refused` (причины:
  `not_publisher_uid`, `sender_unknown`, `operation_not_allowed`);
- `main.check_run(layout, snap: Snapshot, run_id) -> RunRecord` — запись есть, релиз
  записи `allowed` в **реестре снимка** `snap.releases` (отозванный → `refused:
  release_revoked: <причина>`), digest совпадает; `releases.toml` повторно не читается
  (r4 N1); `root_id` записи сверяется операциями периметра (Task 11);
- `store.reserve(layout, sender_uid, op, req) -> Reservation` — §5.6 дословно: глобальная
  `flock` на `publisher/ids.lock` → временный файл `ids/.<request_id>.<pid>.tmp` с
  `{sender, operation, digest}` → `fsync` → `os.link` в `ids/<request_id>` (`EEXIST` →
  сверка привязки) → `fsync` каталога → удаление своего временного файла → снятие
  глобальной блокировки → `flock` по ключу идемпотентности
  `keys/<sha256(sender|op|digest)>.lock`. Привязка не совпала — `refused:
  request_id_reused`. Уборка `ids/.*.tmp` погибших процессов — **под той же глобальной
  блокировкой**, в начале `reserve` (r1 N8): под блокировкой временный файл может
  принадлежать только процессу, который уже не держит её, то есть погибшему;
- `store.intent(res, payload) -> None` / `store.result(res, response) -> None` — файлы
  `intents/<key>.json`, `results/<key>.json`, запись `write_atomic` с `fsync` каталога;
  повтор с готовым `result` → прежний ответ (кроме `perimeter-status`, `read-token`,
  `self-check`); с `intent` без `result` → ветка сверки операции. **`result` пишется
  только для окончательных исходов** (`done`, `refused`); `unknown` не сохраняется, и
  повтор снова идёт в сверку — внешнее действие не повторяется никогда (r2 N4);
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
  следующий `reserve` убирает временный файл, `ids/<id>` нет),
  `test_cleanup_does_not_race_active_reserve` (барьер после `fsync` процесса A, процесс B
  входит в `reserve` → B ждёт блокировку; A завершает `link`; B с тем же id и другим
  содержимым → `request_id_reused`); `test_self_check_only_from_root_sender`
  (`SUDO_UID=601` + `self-check` → `operation_not_allowed`; `SUDO_UID=0` + `merge` →
  `operation_not_allowed`); `test_sudo_uid_noncanonical` (`" 601"`, `"0601"`, `"+601"`);
  `test_response_roundtrip`;
  `test_repeat_returns_previous_result`; `test_perimeter_status_and_read_token_not_replayed`;
  `test_extra_argv_refused_before_stdin`; `test_journal_has_no_token`.
Операция `self-check` (решение 6): запрос `{schema, request_id}`; **без** `run_id` —
порядок для неё: отправитель → разбор → резервирование → операция (`check_run` не
вызывается, r2 N3); ответ `done` с `result = {installations: [{id, account,
repository_ids, permissions}], problems}` — `SelfCheckResult.to_json()`; результат не
воспроизводится при повторе. Для модельных операций неизвестный `run_id` —
`refused: run_unknown`; этим пользуется `doctor` (проверка «издатель отвечает» без
зарегистрированных прогонов).

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
  подпись — `openssl dgst -sha256 -sign <key>` с данными на stdin через новый
  `platform.run_with_input(argv, data: bytes, *, timeout, env=_SYSTEM_ENV) -> RunResult`
  (как `run_command`, но `stdin` — канал с `data`, r1 N14); путь ключа в argv допустим
  (секрет — содержимое, не путь);
- `installation_token(jwt, installation_id, *, repository_ids: list[int] | None,
  permissions: Mapping[str, str] | None, transport) -> Token` — `POST
  /app/installations/{id}/access_tokens`; `Token(value, expires_at, permissions,
  repository_ids)`; ответ сверяется: при заданных `repository_ids`/`permissions`
  вернувшиеся равны запрошенным, иначе `GitHubRefused("token_scope_mismatch")`;
  `expires_at` разобран (RFC 3339) и лежит в `(now, now + 3600]`, иначе
  `token_expiry_invalid`;
- `installations(jwt, transport) -> list[Installation]` — `GET /app/installations`:
  ответ — **массив**; все страницы по `Link: rel="next"` (`per_page=100`, предел 50);
  `installation_repos(token, transport) -> list[int]` — `GET /installation/repositories`:
  ответ — **объект** `{total_count, repositories}`; все страницы; сумма длин
  `repositories` равна `total_count` последней страницы. Ошибка любой страницы,
  превышение предела, неожиданная форма — `GitHubUnavailable`, не частичный список
  (r2 N9);
- `SelfCheckResult(installations: tuple[Installation, ...], problems: tuple[str, ...],
  perimeter: PerimeterStats | None)`, `PerimeterStats(events: int, db_bytes: int,
  free_bytes: int)` (r4 N5: эксплуатационные пределы §6.1 — читает издатель, единственный,
  кто открывает базу; `None` с причиной, если база недоступна),
  `Installation(id: int, account: str, repository_ids: tuple[int, ...],
  permissions: Mapping[str, str])`; `to_json()`/`from_json()` — сериализация для ответа
  `self-check` и разбор в `doctor` (r3 N5);
- `self_check(products, app_id, key, *, openssl, transport) -> SelfCheckResult` — все
  установки App с их репозиториями и правами; проблема
  `app_installation_outside_products: <id>`, если репо установки не ⊆ `repository_id`
  продуктов с этим `app_id` (решение 10);
- `call(method, path, token, body=None, *, transport) -> tuple[int, object]` — ответы 5xx
  и сетевые ошибки → `GitHubUnavailable`; 4xx → `GitHubRefused(status, message)`.

- [ ] **Step 1: Тесты** — `test_jwt_claims` (декодированные заголовок и нагрузка, подпись
  проверяется `openssl dgst -verify` тестовым ключом из `tests/df/fixtures/app/test-key.pem`,
  сгенерированным для теста); `test_token_scope_mismatch`; `test_no_env_proxy`
  (`HTTPS_PROXY` в окружении не используется); `test_self_check_outside_products`;
  `test_5xx_is_unavailable`; `test_pagination_all_pages` и
  `test_pagination_error_is_unavailable` (вторая страница 502 → не успех);
  `test_total_count_mismatch` (только `installation_repos`);
  `test_self_check_result_roundtrip` (цепочка `self_check` → `Response` → `parse_response`
  → `SelfCheckResult.from_json` → проверка `github.app` Task 13: успех, нет установки
  продукта, недостаточные права — без заранее написанного JSON); `test_installations_is_array`
  (объект вместо массива → `GitHubUnavailable`); `test_token_expiry_bounds`; `test_run_with_input_signs`
  (изменение одного байта нагрузки ломает `openssl dgst -verify`).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): GitHub App client for the publisher`

---

### Task 9: `publish-review`, `read-token`, сверка

**Files:**
- Create: `df/publisher/ops.py`
- Test: `tests/df/test_publisher_ops.py`

**Interfaces:**
- `MARKER = "<!-- df-publisher:v1 request_key={key} head_sha={sha} -->"`;
- `escape_body(body: str) -> str` — **каждое** `<!--` в теле модели заменяется на
  `&lt;!--` (r1 N17: перечень префиксов не полон — например, `<!-- ai-prosto-edge-check
  … -->` из `governance/edge_check/publish.py`); маркер издателя добавляется последним
  абзацем после экранирования;
- `publish_review(ctx, req) -> Response` — §5.4 п. 1–3: `GET /repos/{full_name}/pulls/{pr}`
  токеном установки (`pull_requests: write`): открыт, `base.repo.id == repository_id`,
  `head.sha == head_sha`, иначе `refused: pr_state`; намерение → `POST …/reviews` с
  `commit_id = head_sha`, `event` из `decision`; ответ: `commit_id == head_sha`,
  автор — бот App, иначе `unknown`;
- **неоднозначный исход POST** (таймаут, обрыв соединения, 5xx после отправки тела) →
  сразу `reconcile_review`, без повтора POST (r2 N4); сверка недоступна → `unknown`;
- `reconcile_review(ctx, req, key) -> Response` — список ревью PR (все страницы), ровно одно с маркером
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
  `test_publish_review_head_moves_between_get_and_post` (барьер транспорта после `GET`:
  head сменился — `POST` идёт с `commit_id` = SHA запроса, повторного чтения нет, ответ
  сверяется по `commit_id`);
  `test_publish_review_escapes_markers` (тело с `<!-- df-publisher:v1 request_key=x -->`,
  `<!--APPROVAL`, `<!-- ai-prosto-edge-check head=… -->`, несколько маркеров подряд → все
  экранированы, ровно один настоящий маркер — издателя);
  `test_publish_review_reconciles_after_crash` (намерение есть, результата нет, на GitHub
  ревью с маркером → `done` без второго `POST`);
  `test_publish_review_timeout_reconciles` (транспорт создаёт ревью и отвечает таймаутом →
  `done` с тем же `review_id`, второго `POST` нет); `test_timeout_reconcile_unavailable`
  (сверка тоже недоступна → `unknown`, `results/` пуст, повтор снова сверяет); `test_reconcile_no_marker_unknown`;
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
  в 2a нет: после `precheck is None` — `refused: merge_not_in_stage_2a` (не исключение),
  а `UNAVAILABLE` не возвращает `None` никогда.

- [ ] **Step 1: Тесты** — маршрутизация: кандидат по шаблону, finalize (суффикс + только
  конверты), brief; файл остановки → `ambiguous_kind`; finalize-ветка с лишним путём или с
  `approvals/stops/…` → `ambiguous_kind`; ветка прежней арности
  `spec/ws-approve-1-2` → candidate (глоб формы, как в контракте);
  ветка вне шаблонов → `ambiguous_kind`; `test_path_templates_match_act_envelope` (сверка
  констант с `governance.act_envelope.envelope_path("ws", "n")` и `stop_path`);
  `test_merge_always_precheck_unavailable_in_2a`; `test_no_mutating_calls` (r1: транспорт
  записывает все вызовы; при `UNAVAILABLE` **и** при подставленном `precheck`, всегда
  возвращающем `None`, — вызовы к `/repos/…` только `GET`; единственный `POST` —
  выпуск токена установки `/app/installations/{id}/access_tokens`).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): merge kind routing (stage 2a, no merge)`

---

### Task 11: Журнал периметра SQLite, `record-violation`, `perimeter-status`, клиент

**Files:**
- Create: `df/publisher/perimeter.py`, `df/perimeter_client.py`
- Modify: `df/cli.py` (`perimeter restore`, писатель), `df/roles_setup.py`
  (`perimeter-init`)
- Test: `tests/df/test_perimeter.py`, `tests/df/test_perimeter_client.py`

**Interfaces:**
- `DB = layout.perimeter_dir / "events.sqlite"`; `MAX_EVENTS = 10_000`;
  `BUSY_TIMEOUT_MS = 5000`;
- **Жизненный цикл базы** (r2 N5): таблица `meta(key, value)` с `admission ∈ {ready,
  restored_pending}`; `/var/lib/df/perimeter/created` — отметка первичного создания
  (`df-publisher`, `0600`):
  - `create_db(layout)` — только шаг `roles setup` `perimeter-init`, только если нет ни
    базы, ни отметки; пишет схему, `admission = ready`, затем отметку;
  - `open_db` **никогда не создаёт**: нет базы при отметке → `unavailable: lost`
    (обращение к человеку); нет ни того ни другого → `unavailable: not_initialized`;
  - **барьер базы** (r3 N1): файл `perimeter/db.lock` (`df-publisher`, `0600`); каждый
    процесс издателя берёт `flock(LOCK_SH)` **до** `sqlite3.connect` и держит до
    закрытия соединения; `create_db` и восстановление — `LOCK_EX`;
  - `darkfactory perimeter restore --from PATH | --empty` (root, писатель) исполняет
    восстановление **от `df-publisher`** (`run_as`, данные копии — через stdin
    `run_with_input`, каталог `0700` роли) по протоколу:
    1. копия пишется во временный файл каталога и проверяется `integrity_check` и
       инвариантами, `admission` в ней ставится `restored_pending` — всё до барьера;
    2. `flock(LOCK_EX)` с ожиданием ≤ 30 с (иначе отказ «база занята», ничего не
       меняется) — после него ни одного открытого соединения издателя нет;
    3. если у прежней базы есть горячий журнал `events.sqlite-journal`, под тем же
       `LOCK_EX` прежняя база открывается с прагмами `open_db` и выполняется **чтение**
       (`BEGIN; SELECT count(*) FROM sqlite_master; COMMIT`) — SQLite откатывает горячий
       журнал при начале транзакции чтения (r4 N2); соединение закрывается; журнала
       быть не должно. Ошибка чтения (повреждённая пара база/журнал) — отказ с
       инструкцией: `darkfactory perimeter restore … --set-aside-old` переносит
       **оба** файла вместе в `perimeter/set-aside/<UTC-время>/` и только затем ставит
       новую базу; удалять один журнал нельзя никогда;
    4. `rename` временного файла на `events.sqlite`, `fsync` каталога, снятие
       `LOCK_EX`; сбой до шага 4 оставляет прежнюю базу, после — новую с
       `restored_pending`;
  - `restored_pending` при открытии: источник эпох доступен → в одной транзакции
    консервативное `registered(E)` для **каждого** корня из множества «корни строк
    `events` ∪ корень текущей привязки» (источник `db_restore`) и `admission = ready`;
    недоступен (в 2a — всегда) → `unavailable: restore_pending`;
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
- `perimeter_status(conn, req, epochs) -> Response` — нет привязки → `done` с
  `result.state = "unavailable"`, `reason = "no_binding"` (решение 14); `busy_timeout`
  исчерпан → `reason = "busy"`; провал целостности → `"integrity"`; голова ≠ `expected_snapshot_sha` → `stale_snapshot` с актуальным SHA;
  иначе в одной транзакции `pending` → `registered(E)` и вычисление `blocked`/`clear` по
  правилу блокировки; ответ несёт SHA снимка и последнюю эпоху;
- `restore_conservative(conn, roots, epochs) -> None` — консервативное `registered(E)`
  на каждый корень (источник `db_restore`);
- клиент `perimeter_client.status(run_id: str, root_id: RootId, trust: TrustView, *,
  reload: Callable[[], TrustView], call: Callable[[bytes], bytes] = sudo_call,
  attempts: int = 3) -> PerimeterState` — `TrustView(snapshot_sha, last_epoch)`; запрос
  `perimeter-status` через `sudo -n -u df-publisher <libexec>/df-publish
  perimeter-status` (stdin); `stale_snapshot` → `trust = reload()` и **новый**
  `request_id` с новым `expected_snapshot_sha`; сбой вызова (не ответ) — повтор того же
  запроса с **тем же** `request_id` (minor r7 №2); ответ `done` со `state =
  unavailable` → `PerimeterState("unavailable", <reason ответа>)` (поля снимка в таком
  ответе — `null`, r2 N10); со `state ∈ {blocked, clear}` — `snapshot_sha` и
  `last_epoch` обязаны равняться `trust`, иначе `PerimeterState("unavailable",
  "snapshot_mismatch")`; исчерпание попыток — `unavailable`.

- [ ] **Step 1: Тесты** — сценарии §6.1 «Тесты», возможные на поддельном
  `EpochSource`: обнаружение → эпохи недоступны → `pending` → эпохи доступны →
  `perimeter-status` → `registered(E)` → новая эпоха → `clear`; переход эпохи между
  обнаружением и регистрацией; повтор `record-violation` после сбоя между `COMMIT` и
  ответом (тот же `event_id`); два конкурентных разных события (процессы); два
  конкурентных `perimeter-status` с `pending` (один переход); аварийное завершение
  писателя (`os._exit` внутри транзакции) → следующее открытие → подтверждённое событие на
  месте; испорченный индекс (`writable_schema`) / дубль `UNIQUE` / неизвестное состояние
  → `unavailable`; `test_open_never_creates` (нет базы + отметка → `lost`; нет обоих →
  `not_initialized`; файл не появляется); `test_restore_stale_copy_blocks` (исправная
  устаревшая копия без последнего события → `restored_pending` → при доступных эпохах
  консервативное событие на каждый корень → `blocked` до следующей эпохи);
  `test_restore_interrupted` (сбой между вставками → после повторного открытия либо всё,
  либо ничего — одна транзакция); `test_restore_pending_without_epochs_unavailable`;
  `test_restore_waits_for_open_connection` (процесс A открыл базу и стоит на барьере;
  `restore` ждёт; A закрывается; `restore` завершается; новый процесс B видит только
  восстановленную базу с `restored_pending`); `test_restore_timeout_changes_nothing`;
  `test_restore_crash_points` (сбой на каждом шаге 1–4 → прежняя база либо новая с
  `restored_pending`, никогда `clear` без допуска; подтверждённые транзакции прежней базы
  на месте, если остался прежний файл); `test_restore_with_hot_journal` (настоящий горячий журнал: отдельный процесс-писатель
  SQLite завершается `os._exit` внутри транзакции записи; восстановление откатывает его,
  подтверждённые ранее события на месте); `test_restore_corrupt_pair_refused` (испорченный
  журнал → отказ, оба файла не тронуты; с `--set-aside-old` — перенесены вместе); клиентский номер
  эпохи — поля нет в схеме; 10 000 событий — `perimeter_status` укладывается в
  `2.0` с на раннере (замер пишется в вывод теста); `test_pragmas_effective`
  (на macOS `fullfsync == 1`); `test_no_binding_refuses`; `test_client_new_request_id_after_stale`;
  `test_client_same_request_id_on_retry`; `test_client_snapshot_mismatch_unavailable`
  (у `clear`); `test_client_keeps_unavailable_reason` (`no_binding`, `busy`, `integrity`,
  `lost`, `not_initialized`, `restore_pending` с `null`-снимком — причина сохраняется).
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): perimeter event store and status ops`

---

### Task 12: `darkfactory setup-agent`

**Files:**
- Create: `df/setup_agent.py`
- Modify: `df/cli.py` (подкоманды `setup-agent app-key`, `setup-agent machine-token`,
  писатели), `df/fsutil.py` (`write_under(dir_fd, …)`)
- Test: `tests/df/test_setup_agent.py`

**Interfaces:**
- `install_app_key(layout, app_id: int, source: Path) -> None` — источник root читает как
  данные (обычный файл, `O_NOFOLLOW`, ≤ 16 КиБ, PEM `-----BEGIN (RSA )?PRIVATE KEY-----`);
  `/etc/df/secrets` — каталог root-зоны (`df-publisher` `0700`, родители проходят политику
  доверенных путей); запись `secrets/app-<id>.pem` во временный файл того же каталога,
  `fchown(df-publisher)`, `fchmod(0400)`, `fsync`, `renameat`, `fsync` каталога;
  источник не удаляется (решение человека);
- `install_machine_token(layout, source: Path) -> None` — токен (`github_pat_…` или
  `ghp_…`, одна строка ≤ 255 байт) в `~df-exec/.config/df/github-token` (r1 N9):
  1. `role_home(df-exec)` открывается от `/` по компонентам `O_DIRECTORY|O_NOFOLLOW`;
     владелец последнего — `df-exec`, остальных — root;
  2. `.config` и `df` — `openat(O_DIRECTORY|O_NOFOLLOW)`; отсутствует — `mkdirat` +
     открытие тем же способом + `fchown(df-exec)` + `fchmod(0700)` **через дескриптор**;
     существует, но не каталог, ссылка или владелец не `df-exec` — отказ без изменений;
  3. файл — `openat(tmp, O_CREAT|O_EXCL|O_NOFOLLOW)`, `fchown`, `fchmod(0600)`, запись,
     `fsync`, `renameat(df_fd, tmp, df_fd, "github-token")`, `fsync(df_fd)`;
  владельцев найденных объектов root не меняет никогда;
- проверки после записи: `self-check` издателя от root (решение 6, вызов `sudo -n -u
  df-publisher <libexec>/df-publish self-check`) и `df-launch df-exec check-github` —
  логин machine user печатается человеку; несовпадение с `products.toml`
  `machine_user` зарегистрированного продукта — предупреждение (уровень 2 его проверяет
  как нарушение, Task 13).

- [ ] **Step 1: Тесты** — неверный PEM → отказ без записи; режимы и владельцы итоговых
  файлов (подменённый `os.fchown`); токен с переводом строки внутри → отказ; повтор —
  атомарная замена; `test_token_parent_symlink_refused` — ссылка на месте `.config`,
  `.config/df`, самого `role_home`: отказ, вне `role_home` ничего не создано и не
  изменено; `test_token_parent_swapped_during_write` (замена `df` на ссылку между
  `openat` и `renameat` — запись идёт в открытый дескриптор, путь ссылки не затронут);
  секрет не попадает в журнал установки и в вывод.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): setup-agent credentials`

---

### Task 13: `doctor --product P` — уровень 2

**Files:**
- Modify: `df/doctor.py`, `df/cli.py`
- Test: `tests/df/test_doctor.py` (дополнить)

Эксплуатационная сводка журнала периметра (r4 N5, §6.1) — отдельная строка `doctor`
(не уровень 2), из `self-check.perimeter`: число событий, размер базы, свободное место;
`events ≥ 9000` — предупреждение «журнал периметра приближается к пределу 10 000»;
`> 10 000` — «нарушено» (операции периметра отвечают `unavailable`); свободное место
`< 64 МиБ` — предупреждение; `perimeter = None` — «данные недоступны: <причина>».
Тест: норма, 9000, 10 001, мало места, недоступно; Task 11 — ошибка записи при нехватке
места (`SQLITE_FULL`) даёт `refused: storage_full`, не `done`, подтверждённые события
на месте (`test_disk_full_refuses`, файловая система ограниченного размера: `tmpfs`
`size=` на Linux, образ `hdiutil` на macOS — в root-ячейках).

Проверки уровня 2 (каждая — `Check` со статусами §8), порядок:
1. `roles.users` — `verify_service_user` для трёх ролей;
2. `roles.sudoers` — `validate` установленного файла + `policy_problems` для пяти имён
   (решение 13) + `io_log_problems`;
3. `roles.libexec` — `wrapper_problems` (включая путь административного Python текущего
   релиза);
4. `roles.isolation` — для `df-exec` и `df-review` через `df-launch <role> check` (§8):
   UID/GID равны `accounts.toml`, `fds` пуст, `env` ⊆ `ENV_ALLOW`, `denied` — `EACCES`
   по каждому пути списка роли (у `df-exec`: `secrets/`, `~df-review`, `~<оператор>/.config/gh`;
   у `df-review`: `secrets/`, токен machine user); для `df-publisher` — `self-check`
   отвечает и `secrets/app-<app_id>.pem` есть (через ответ, не чтением root);
5. `publisher.answers` — `perimeter-status` с `run_id = "doctor-probe"` от обеих
   модельных ролей через настоящий `sudo`: корректный `df/publisher-response/v1` с
   `refused: run_unknown` (r2 N3: «отвечает» не требует прогона);
6. `publisher.foreign` — `df-review` → `df-publish merge` отвергнут sudo;
7. `product.registered` — `P` есть в `products.toml`;
8. `github.machine_user` (r1 N10) — ответ `df-launch df-exec check-github` (Task 5):
   `login` равен `machine_user` продукта; `products[P].permissions` по профилю
   (`write` — `push = true`, `admin = false`; `fork` — `push = false`); 401 —
   «нарушено: токен отозван»; сеть/5xx — «данные недоступны»;
9. `github.app` — ответ `self-check` (Task 7): `problems` пуст; в `installations` есть
   `installation_id` продукта, его `repository_ids` содержат `P`, `permissions` ⊇
   `contents: write`, `pull_requests: write`; источники доверия — «не проверено
   (этап 3)», уровень 2 этим не блокируется;
10. `models.access` — модельные роли `P`: актуальная проба (§3.6), проверяемая от роли;
    в v1 — «не подтверждён» (решение 9).
Уровень 2 достигнут ⇔ уровень 1 и проверки 1–10 `ok` (9 — с оговоркой этапа 3).

- [ ] **Step 1: Тесты** — каждая проверка с подменёнными **ответами операций** (JSON
  `check-github`, `self-check`, `perimeter-status`, разобранные теми же парсерами, что
  в проде): ok / нарушено / данные недоступны / нужен sudo; свежая установка без
  прогонов — `publisher.answers` ok; PAT профиля `fork` у продукта `write`; неверный логин machine user, недостаточные права, App без
  продукта в установке, отозванный токен, недоступный GitHub — не `ok`; полный зелёный
  набор с фикстурной пробой — уровень 2 достигнут (положительный контроль механизма);
  `doctor` без `--product` уровень 2 не считает.
- [ ] **Step 2–4.** **Step 5: Commit** `feat(df): doctor level 2 per product`

---

### Task 14: Root-приёмка §4.5 (CI, обе ОС, без GitHub)

**Files:**
- Create: `tests/df/roles_world.py`, `tests/df/test_acceptance_root_roles.py`
- Modify: `.github/workflows/df-stage1.yml` (job `stage2a (<os>)` после `stage1`, та же
  матрица), `tests/df/conftest.py` (маркер `stage2a`)

Мир: `installed_world` этапа 1 → `darkfactory roles setup --operator runner` →
фикстурный `products.toml` (продукт `repository_id = 1`, App 1; фикстурный репо релиза
содержит `FIXTURE_FROM_CHECKOUT`, Task 15) → фикстурный ключ App
(тестовый PEM) → **локальный поддельный GitHub** (`tests/df/fake_github.py`: HTTP на
`127.0.0.1`, проверяет подпись JWT тестовым публичным ключом, отдаёт установки,
репозитории, токены, PR, ревью по сценарию теста) и `api_base` в `publisher.toml`
(r2 N6; отмечается в evidence; `check-github` роли ходит туда же — `launch_release`
берёт `api_base` из того же файла) → фикстурная запись прогона в `runs/` (root, помечена `"fixture": true` в
evidence, r1 N11) → для отзыва — фикстурная правка `releases.toml`. Teardown удаляет
роли, `/etc/sudoers.d/df`, `<R>`, проверяет, что записей нет. Сбой мира, запуска или
наблюдателя — `pytest.fail("инфраструктура: …")`, не PASS и не skip.

Каждая строка — тест с **названным механизмом отказа** и парным положительным контролем
(r1 N12). Отказы классифицируются по механизму (r4 N4), при `LC_ALL=C`, по снимкам
диагностик обеих ОС в `tests/df/fixtures/sudo-diag/`: «команда не разрешена»
(код 1, `is not allowed to execute` / `not allowed to run`), «сохранение окружения
запрещено» (код 1, `not allowed to preserve the environment`), «нужен пароль» —
**не** засчитывается как запрет, а валит тест как неверную настройку; отказ издателя —
код 2 и `outcome = refused` с причиной. При отказе sudo проверяется, что издатель не
запускался: в `publisher/journal.jsonl` нет новой строки.

| Попытка | Механизм отказа | Положительный контроль |
|---|---|---|
| `df-exec`: чтение `secrets/app-1.pem`, `~runner/.config/gh/hosts.yml` (фикстура), файла в `~df-review/work` | ОС (`EACCES`) | `df-exec` читает свой `work/` |
| `df-review`: чтение `secrets/app-1.pem`, токена machine user | ОС | `df-review` читает свой `work/` |
| **каждая** из `df-exec`, `df-review`: создание, запись, переименование, удаление, `chmod` контрольного объекта в `libexec/`, релизе, `/etc/df` | ОС | те же 5 операций в своём `work/` |
| `df-review` → `df-publish merge`; `df-exec` → `df-publish publish-review` | sudo | `df-exec` → `perimeter-status`: ответ |
| те же операции **мимо sudoers**: `df-publish` от `df-publisher` через `run_as` с `SUDO_UID` = UID чужой роли | издатель (`operation_not_allowed`) | с `SUDO_UID` своей роли — ответ |
| `sudo -E` / `--preserve-env` / лишний аргумент после операции | sudo | без них — ответ |
| `SUDO_UID=0 sudo -u df-publisher … perimeter-status` от `df-exec` | — (не отказ): ответ, evidence — отправитель `df-exec` | — |
| `df-exec` → `self-check` (через sudo) | sudo | root → `self-check` через настоящую цепочку и поддельный GitHub: `done`, `problems` пуст; сценарий «лишняя установка» — `problems` не пуст |
| `run_id` прогона на отозванном релизе | издатель (`release_revoked`) и `df-launch run` (`release_revoked`) | `run_id` на `allowed`: ответ |
| `run start` при отозванном `current` | `df-run-start` (код 2, причина) | при `allowed`: запись есть, затем отказ `df-launch run` «доступ к моделям не подтверждён» |
| повтор `perimeter-status` | — : каждый раз заново `unavailable/no_binding` (журнал — две строки) | — |
| тот же `request_id` с другим содержимым | издатель (`request_id_reused`) | первый запрос — ответ |
| предел одной UID (B §5.8): процесс `df-exec` прогона на отозванном релизе ссылается на `run_id` другого, `allowed` прогона | — (не отказ, а предел): издатель отвечает так же, как на запрос этого `allowed` прогона, — собственные проверки операции, ничего сверх них; штатный `df-launch run` прогона на отозванном релизе — отказ `release_revoked` | evidence фиксирует строку как предел гарантии, не как защиту |
| I/O-журнал sudo (r2 N2), четыре конфигурации во втором файле `sudoers.d` на время теста: (а) глобальный `log_output`; (б) `Defaults!<df-publish> log_output, iolog_dir=<другой>`; (в) `log_servers=127.0.0.1:30344`; (г) только `log_input` | (а), (г): `evidence.response_nonce` ответа `perimeter-status` от `df-exec` не найден в потоках `stdout`, `stderr`, `ttyout` ни одной сессии под `iolog_dir` (файлы сессии разбираются по типу потока; сжатые gzip — распаковываются; нераспознанный файл — провал «инфраструктура»), `io_log_problems` пуст; (б), (в): `io_log_problems` — «нарушено» | (а): маркер `check_report.nonce`, напечатанный `df-launch … check`, **найден** в `stdout` его сессии; (г): `request_id` запроса **найден** в потоке `stdin` сессии `df-publish` — журнал stdin работает, stdout — нет |

Переносы этапа 1:
- `test_run_start_no_release_code_before_drop` — sentinel-интерпретатор релиза
  (`venv/devtools/bin/python`, пишет файл при `euid == 0`) не исполнен до сброса при
  настоящем `sudo df-run-start`; положительный контроль — после сброса sentinel
  запускается от `df-exec` и это фиксирует;
- `test_launch_cwd_outside_builder_tree` — `darkfactory launch df-exec check` из каталога
  внутри дерева сборщика: наблюдатель этапа 1 (`tests/df/observe.py`) охватывает всю
  цепочку, включая `sudo`, и не видит открытия этого каталога с `euid = 0`;
  положительный контроль наблюдателя — прямой `sudo …/df-launch` из того же каталога это
  открытие **видит** (на macOS); `Observation.limits` переносятся в evidence;
- `test_launch_release_sees_clean_start` — sentinel в `launch_release.py` фикстурного
  релиза записывает UID, `cwd` и открытые дескрипторы **при старте**: `cwd = work/`,
  дескрипторов > 2 нет, вызов был с открытым fd 9 у вызывающего.

- [ ] **Step 1–3:** мир, тесты, job workflow.
- [ ] **Step 4:** прогон CI на PR — обе ячейки зелёные; расхождение со спекой —
  находка и стоп (не подгонять `df` и тесты).
- [ ] **Step 5: Commit** `test(df): stage-2a root acceptance (§4.5 without merge)`

---

### Task 15: Живая приёмка на стенде, evidence, документация

**Files:**
- Create: `.github/workflows/df-stage2-live.yml`, `tests/df/live_stand.py`,
  `docs/evidence/<дата>-df-stage2a-acceptance.md`
- Modify: `README.md` (раздел этапа 2a: `roles setup`, `setup-agent`, `darkfactory launch`,
  `darkfactory run`, неподдерживаемый прямой `sudo …/libexec`, ограничения), `CLAUDE.md`
  (строка `df/`), `TODO.md`

Workflow: `on: workflow_dispatch` (только), `environment: df-stage2-live`, матрица
macos-15 / ubuntu-24.04, `if: github.ref == 'refs/heads/master'`. Шаги: подготовка хоста
→ установка **проверяемой реализации** (r2 N7): фикстурный профиль `root_world` из одного
члена `devtools` — bare-репо, собранное из чекаута workflow на коммите `github.sha`;
в фикстурный коммит кроме `df/` копируется `contracts/approval-branches/v1/patterns.env`
(r4 N3: его читает маршрутизация `merge`; `build_fixture_repo` дополняется списком
`FIXTURE_FROM_CHECKOUT = ("df", "contracts/approval-branches/v1/patterns.env")`, тот же
список в Task 14), а проверка происхождения ниже сверяет blob-ы **всех** путей этого
списка
(тот же механизм, что `installed_world` этапа 1, но в раскладке `/`); до любых операций
с секретами сверка происхождения (r3 N2) — две идентичности, не приравниваемые друг к
другу: `source_sha = github.sha` (чекаут workflow) и `fixture_sha` (коммит фикстурного
репо, который ставит установка). Проверки: (1) `git -C <fixture> ls-tree -r
<fixture_sha> -- <пути FIXTURE_FROM_CHECKOUT>` и то же для `<checkout>` на `<source_sha>`
дают одинаковые пары «путь, blob»; отсутствующий путь — провал; (2) `release.json` называет член `devtools` с
`sha = fixture_sha`; (3) `admin_digest` записи `releases.toml` равен
`admin.expected_admin_digest(<checkout>/df)` — новая функция `df/admin.py`: копирует
`df/` во временный `<tmp>/df` теми же правилами, что `install_admin`, и считает
`tree_digest(<tmp>)` — та же область хеширования, что у `admin_digest`. Расхождение —
провал «инфраструктура»; обе идентичности и digest — в evidence; тест
`test_live_provenance`: изменённый файл `df/`, другой `source_sha`, подменённый
`fixture_sha` → провал до чтения секретов. Манифест зонтика здесь не
используется: его пин devtools старше 2a → `roles setup` → `products.toml` стенда из
переменных → `setup-agent app-key`/`machine-token` из секретов (файлы во `RUNNER_TEMP`,
`0600`, удаляются шагом `always()`) → фикстурная запись прогона (как Task 14, отмечена в
evidence: штатный `run start` в v1 останавливается на пробах) → `live_stand.py`. Перед
стартом `self-check` обязан пройти: лишняя установка App — провал «инфраструктура», не
обход (шаг H2).

Строки (живой GitHub; у каждой — механизм отказа и контроль выполнимости, r1 N12):
1. machine user (токен `df-exec`) открывает PR `brief/stand-<run_id>` (один файл) и
   PR `stand/<run_id>`;
2. `df-review` → `publish-review` `comment` на `brief/…` → ревью бота App с маркером на
   `head_sha`; повтор того же запроса → тот же `review_id`, второго ревью нет;
3. **сверка после потерянного ответа**: root удаляет `results/<key>.json` (имитация сбоя
   после действия, до записи результата), повтор → `done` с тем же `review_id`, второго
   `POST` нет;
4. **смена head** (r2 N8, r3 N4, B §4.5): (а) запрос с прежним `head_sha` после пуша →
   `refused: pr_state`; (б) **окно между чтением и публикацией — через настоящий
   издатель**: на время этой строки `api_base` стенда — локальный прокси
   `tests/df/gate_proxy.py` на `127.0.0.1` (тот же root-ключ `publisher.toml`, что в
   Task 14; в evidence — «нештатный API, прокси к api.github.com»), который передаёт
   запросы на `https://api.github.com` без изменений и **задерживает** первый
   `POST …/pulls/{n}/reviews` до сигнала теста. Порядок: `df-review` → `publish-review`
   (настоящий sudo) → издатель делает `GET` PR (head = X) → прокси держит `POST` → тест
   пушит коммит Y от machine user → прокси пропускает `POST` → ответ издателя `done`,
   ревью `commit_id = X`; проверка по `commit_id` (не по общему списку ревью PR): ревью
   с `commit_id = Y` от бота App нет. Затем `api_base` возвращается к штатному;
5. `df-review` → `read-token` → `git --config-env=http.extraHeader=DF_READ_AUTH clone`
   приватного стенда проходит. Тем же токеном: `git push` новой ветки, `POST …/reviews`
   (`COMMENT`), `PATCH …/pulls/{n}` (заголовок), `PUT …/pulls/{n}/merge` — отказ GitHub
   **по правам**: `403` с сообщением «Resource not accessible by integration» (r2 N8).
   Контроль выполнимости: `push` и `PATCH` — токеном machine user (проходят); `POST
   …/reviews` — через `publish-review` (проходит); `PUT …/merge` — полным токеном
   установки App на тот же PR возвращает **не** 403, а отказ состояния (`405`, PR не
   одобрен по ruleset) — то есть у полномочия есть право, и 403 `read-token` — именно
   нехватка прав, а не состояние PR;
6. во время чтения `ps -axww -o args` (снимок каждые 100 мс) не содержит токена; журнал
   издателя, `/var/lib/df/publisher/**`, каталог I/O-журнала sudo и `.git/config` клона —
   без токена;
7. `df-exec` → `publish-review` и `df-review` → `merge` — отказ sudo;
8. `merge` от `df-exec`: PR `brief/…` → `refused: precheck_unavailable`; PR `stand/…` →
   `refused: ambiguous_kind` (r1 N11);
9. уборка: PR закрыты, ветки удалены (от machine user); провал уборки — провал прогона.

Evidence: id прогонов, `review_id`, ответы издателя (без секретов), снимки `ps`, итог по
строкам §4.5 с пометкой «2b» у трёх строк мержа и пометкой «фикстура» у записи прогона и
`products.toml`.

- [ ] **Step 1–3:** workflow, скрипт, документация.
- [ ] **Step 4:** владелец запускает `workflow_dispatch` (одобрение environment) — обе
  ячейки зелёные; evidence заполняется по фактическому прогону.
- [ ] **Step 5: Commit** `test(df): stage-2a live stand acceptance; docs`

---

## Вне этого плана

- Этап 2b: строки мержа §4.5, `act_premerge` как `Precheck`, поверхность candidate D16,
  полный состав PR (r7 №1), вид stop — после части B (M0 steward).
- Этап 3: привязка `df-trust`, проверка установок App на источниках доверия, живой
  `perimeter-status`; **запрет PR-операций прогонам прежнего корня после `--rebind`**
  (B §5.8): в 2a `check_run` сверяет корень только у операций периметра; запись
  продукта командой (`setup-repo`).
- Таблица конвейеров `df-launch run` и `df-review-run` с реальным ревью — этап 3/4.
- Испытание потерей питания для SQLite — вне v1 (решение 15, владелец 26.10).
- `releases.toml` `revoked` пишет `darkfactory update` (этап 4); в 2a отзыв в приёмке
  делает фикстура.
