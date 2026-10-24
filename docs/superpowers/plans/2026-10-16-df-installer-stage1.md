# Установщик `df` (команда `darkfactory`), этап 1 — конфигурация, установка, модели: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sudo … df/bootstrap.py init` ставит неизменяемый проверенный релиз DarkFactory из
пиненого профиля манифеста зонтика в `/opt/df`, вместе с независимой административной средой
и командой `darkfactory`. `darkfactory doctor` подтверждает уровень 1 «окружение»;
`darkfactory models list/set` и `darkfactory doctor --probe-models` управляют выбором моделей
по ролям.

**Architecture:** новый пакет `df/` в devtools, только stdlib. От root он исполняется лишь из
двух источников: bootstrap из чекаута человека или административная среда
`/opt/df/admin/<id>` на административном Python. И то и другое никогда не было доступно
`df-build` на запись. Сборка — вызовы утверждённых системных CLI от `df-build`. Приём:
источник читает `tar` от `df-build`, root разбирает поток собственным ограниченным читателем
в закрытое назначение и проверяет итоговую копию; объектов источника root не открывает.
Деревья сборщика чистит сам `df-build`. Установку ведёт цикл автомата (фаза → действие →
обязательная фаза «после») под исключительной блокировкой писателя. Читатели берут снимок
по счётчику поколений без блокировки. Транзакция «`current` + `install.toml`» идёт через
`pending.toml`. Платформенные различия собраны в `df/platform.py`.

**Tech Stack:** Python ≥ 3.12, только stdlib (`tomllib`, `hashlib`, `fcntl`, `ctypes`,
`subprocess`, `urllib`); системные `git`, `tar`, `uv`, `otool`/`readelf`, `sandbox-exec`
(macOS), `unshare`/`setpriv` (Linux); pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-13-df-installer-design.md` **rev 8** (рабочий
черновик — `design-b.rev8.md`) и спека A **rev 17** (только публичное имя команды). Этап 1 =
§2.5 строка 1, §3 (включая §3.9–§3.10), §8 (уровень 1), §10 строка 1. План спорит со
спекой, а не заменяет её: исполнитель читает обе.

## Решения (16.10)

Техническое заключение Codex — `stage1-decisions-codex-2026-10-16.md`. Решения круга r1 —
`stage1-r1-triage-decisions.md`. Подпись владельца ставится на пару «rev 8 + план» после
схождения ревью.

1. **Сборка по окончательному пути** — спека §3.2–§3.3. Основание — замер 16.10 на uv
   0.11.23: окружение хранит абсолютные пути, а `--relocatable` касается только скриптов.
   После r1 (Р1) безопасность приёма держится на итоговой копии root:
   - остановка сборщика — уборка;
   - копия рассчитана на живого писателя;
   - digest staging и блокировка учётки убраны.

   Реализуется в Tasks 3, 5, 7, 9, 13.
2. **Профиль в манифесте зонтика** (Tasks 2, 14). Секция `[df.profiles.<name>]` ссылается на
   записи `[cores]/[apps]/[tools]` по ключу, поэтому пины не дублируются. Полный `sha` (40 hex)
   обязателен только у **членов выбранного профиля**. Сейчас он не выполнен ни у одного:
   - имеющиеся SHA сокращены;
   - у `spec-runner` SHA нет вовсе, только тег;
   - пин `devtools` старше пакета `df`.

   Тесты и CI-приёмка идут на фикстурных репо. Живая установка `core` — отдельный незакрытый
   пункт: после обновления манифеста нужен реальный прогон, проверяющий в том числе
   совместимость пинов и lock-файлов.
3. **Модельная роль → контекст ОС** (Task 10) — одна таблица `ROLE_CONTEXT` для проб и будущих
   запускателей:
   - `interview`, `author` → `df-exec`;
   - `reviewer`, `executor-review`, `edge-check`, `judge` → `df-review`.

   Нет пользователя — «не проверено», без подстановки root или пользователя человека.
4. **Проба и актуальность авторизации** (Task 11, §3.6, §2.4). `outcome` — результат вызова,
   `auth_tracking` — наблюдаемость **закреплённого** источника авторизации. Успешная проба без
   наблюдаемого отпечатка показывается как «успешная проба <time>; актуальность авторизации
   не установлена» и для уровня 2 недостаточна. Значимые параметры харнесса — только те, что
   передаёт адаптер; в v1 таких нет. Харнесс версии без подтверждённой изоляции не
   запускается вовсе.
5. **Команда `darkfactory`** (Р2, Task 8a, §3.9) — без псевдонима `df`. Root исполняет только
   административный код и Python, к которым `df-build` никогда не имел доступа на запись.
6. **Модель сбоя «процесс + ОС»** (Р3, Tasks 1, 3, 7, 9). Отключение питания не испытывается,
   и evidence говорит это явно.
7. **Сверка пакетов** (Р4, Task 7a, §3.5) — «метаданные установленных пакетов согласованы с
   ожидаемым составом lock». Это не доказательство происхождения байтов. Независимые хеши
   wheel не сверяются, и это сужение гарантии только для непривилегированного runtime.
8. **Круг stage1-r2** (`stage1-r2-codex.md`, ответ — `stage1-r2-response.md`):
   - источник читает `df-build`, root принимает поток (Tasks 3, 5, 7);
   - один нормативный автомат с монотонной отметкой (Tasks 7, 9);
   - учётка по намерению (Task 5); снимок под блокировкой (Tasks 4, 8a, 9);
   - идентичность источника пакетов (Tasks 7, 7a);
   - допуск пробы по SHA-256 исполняемого файла и managed-контексту (Task 11).
9. **Круг stage1-r3** (`stage1-r3-codex.md`, ответ — `stage1-r3-response.md`). Классы приёма
   и автомата — третий круг подряд, поэтому механизмы заменены и проверены локально
   (`r3-local-check/`, описание — в ответе):
   - собственный ограниченный читатель потока вместо `tarfile` (Task 3);
   - очиститель `df-build` обходит один корень сам; повтор карантина (Task 5);
   - цикл автомата `drive` вместо `step_done` по рангу; закрепление фактов (Task 7, 9);
   - «соответствие намерению» отдельно от «готовности» учётки (Task 5);
   - счётчик поколений и полный снимок с явным режимом (Tasks 4, 8a, 9);
   - канонический вид источника по видам (Tasks 7, 7a); managed-исключение — элементы argv/env
     (Task 11).
10. **Круг stage1-r4** (`stage1-r4-codex.md`, ответ — `stage1-r4-response.md`). Модель r3
    проверяла переписанную таблицу, поэтому сменён способ проверки (`r4-local-check/`):
    - таблица автомата — данные `df/install_table.py`; спека — их отображение, тест сверяет;
      модель и непересекаемость читают те же данные (Task 7);
    - примитивы держат свои предусловия; повторная проверка не понижает отметку; `settle`
      закрепляет `admin/` и `accept/` (Tasks 3, 7, 9);
    - поток дочитывается до EOF; производитель — вся цепочка (`Producer`, Tasks 3, 5);
    - очиститель открывает корень отдельной командой (Task 5);
    - типизированный результат чтения до `doctor` (Tasks 4, 8a, 9, 12);
    - проба — единственный вызов харнесса, без `--version` (Task 11).
11. **Круг stage1-r5** (`stage1-r5-codex.md`, ответ — `stage1-r5-response.md`). Третья форма
    класса «закрепление фактов», поэтому набор закрепления выведен из данных
    (`r5-local-check/`):
    - `FACT_SOURCES` в `df/install_table.py`, `settle` — их объединение; точки сбоя внутри
      `write_atomic` и `remove_durably` (Tasks 1, 7, 9);
    - домашний каталог `df-build` — под зарезервированным временным именем, `rename` без
      замены (Task 5);
    - части снимка — значениями: `admin_ready` по записи реестра, `parse_models` по тексту
      (Tasks 8a, 10, 12);
    - наблюдение root-доступов на уровне ОС, без наблюдателя — «не подтверждено» (Task 13);
      пустоту дерева сборщика определяет `df-build` (Task 7);
    - строгий разбор записи PAX; обязательные CLI `git`, `uv`, `tar`; фикстура
      `LocalProducer` (Tasks 1, 3, 14).
12. **Круг stage1-r6** (`stage1-r6-codex.md`, ответ — `stage1-r6-response.md`). Модель
    закрепляла там, где план не закреплял, поэтому закрепление стало предпосылкой каждого
    решения повтора, а модель сверяется с планом (`r6-local-check/`):
    - `settle_on_acquire` сразу после `flock`, до снимка, `recover_pending` и
      `create_build_user`; источники учётки — данные `ACCOUNT_SOURCES` (Tasks 7, 8a, 9);
    - запись пользователя и группы — атрибуты `ACCOUNT_ATTRS`, повтор дописывает
      недостающие, конфликт — отказ (Tasks 5, 13);
    - PAX: множество встреченных ключей и признак предшествующего `x` — отдельно от
      значений (Task 3).
13. **Круг stage1-r7** (`stage1-r7-codex.md`, ответ — `stage1-r7-response.md`). Вторая форма
    «модель сильнее плана» — синхронизации внутри примитивов, поэтому протоколы примитивов
    стали данными (`r7-local-check/`):
    - `PROTOCOLS` в `df/install_table.py`: контракт каждого примитива ссылается на свой
      протокол, `test_primitive_protocols_match_table` сверяет события `fsutil`;
      `remove_own_tree` закрепляет родителя, `prepare_dir` — новый каталог,
      `receive_tree` — `sync_tree` копии (Tasks 1, 3, 5, 7, 8a, 9);
    - `tar_bytes` — буквальный генератор без неявных родителей; примеры приёмника сведены
      к нормативным правилам (Task 3);
    - `init` при `pending.toml` — только без `install.toml` (Task 9).
14. **Круг stage1-r8** (`stage1-r8-codex.md`, ответ — `stage1-r8-response.md`). Третья форма
    «модель сильнее плана» — в композиции, поэтому маршруты действий и отката стали данными
    (`r8-local-check/`):
    - `ROUTES` и `REQUIRES` в `df/install_table.py`; действия — исполнители маршрутов
      через общий `run_route`, условия — `lstat` через дескриптор родителя;
      `test_routes_match_table` сверяет настоящую последовательность вызовов (Tasks 3, 7, 9);
    - `prepare_dir` передаёт каталог `fchown` через дескриптор, взятый до передачи; после
      неё — только `fsync` и `close` (Tasks 7, 13).
15. **Круг stage1-r9** (`stage1-r9-codex.md`, ответ — `stage1-r9-response.md`). Четвёртая
    форма — внутри листа маршрута: `transfer_check` сам чистил `build_home`, `install_admin`
    сам ставил Python, удалял дерево и писал отметку. Механизм (`r9-local-check/`):
    - у каждого листа — протокол или вид (`CALLS`: проверка / процесс / запуск от
      `df-build`) с полным набором эффектов; лист не вызывает других листьев; обёртки
      одного протокола — `WRAPS`; успех на отсутствующем объекте — только `ABSENT_OK`
      (Tasks 5, 7, 8, 8a, 9);
    - `cleanup_build_home` и `install_admin_python` — листья маршрутов `verify` и `admin`
      со своими протоколами; `receive_tree` при отказе ничего не удаляет; ссылка команды —
      условие маршрута `launcher`, а не ветка внутри примитива (Tasks 3, 5, 8, 8a, 9);
    - `test_routes_match_table` сверяет развёрнутые события (файловая система, запуски от
      `df-build`), а не подменённые функции целиком (Task 7).
16. **Круг stage1-r10** (`stage1-r10-codex.md`, ответ — `stage1-r10-response.md`). Сверка r9
    сравнивала множества имён и теряла порядок, цели и кратность: `receive_tree` и
    `accept_copy` вдвоём синхронизировали `releases/`, завершение производителя скрыто
    вызывало `reap_user_processes`, в таблице Task 9 потерялось второе удаление `cleanup`.
    Механизм (`r10-local-check/`):
    - у каждого листа, обёртки и вызываемой ими вспомогательной функции — строка
      «Эффекты:» в договоре; её транзитивная развёртка равна `leaf_effects(лист)` из
      данных — порядок, кратность, цели; у каждой синхронизации один владелец (Tasks 3, 5,
      6, 7, 8, 8a, 9);
    - завершение производителя — объявленный шаг протокола `accept_copy`; обе его
      синхронизации — у `receive_tree` (Tasks 3, 5, 7);
    - таблица Task 9 — отображение `render_route_calls()`: полная развёртка маршрутов с
      условиями и объектами (Task 9);
    - `A` и `intact` — через один предикат `admin_ready`, включающий Python профиля;
      `settle_paths` отображает каждое имя `DIRS`, в том числе `admin_python` (Tasks 7, 8a);
    - фикстуры подменяют только нижние операции (запуски, `fchown`), листья настоящие;
      ожидания после смены договоров сверены (частичная копия, двойник `launcher`)
      (Tasks 3, 7, 9, 13).

17. **Круг stage1-r11** (`stage1-r11-codex.md`, ответ — `stage1-r11-response.md`). Одна
    последовательность эффектов не описывала исходы `cleanup_tree`: ветвь ссылки
    возвращалась без синхронизации, карантин синхронизировал два каталога, а сверка
    исключала это фразой; потребители изменённых договоров (выбор синхронизаций, общий
    тест протоколов, правило частичной копии в спеке) отставали. Механизм
    (`r11-local-check/`):
    - у примитива с разными исходами — варианты протокола, у каждого своя строка
      «Эффекты (`вариант`):» и свой тест событий; синхронизацию в прозе нельзя исключить
      (Tasks 5, 7);
    - операции шагов — `OPS`, синхронизации выбирает только `SYNC_OPS`/`sync_steps()` —
      в модели, двойниках и тестах плана (Tasks 7, 9);
    - версия административного Python хранится рядом с `admin_digest` — в отметке и в
      записи реестра — и передаётся в `admin_ready` из того же источника (Tasks 1, 7, 8a,
      9, 12);
    - итоговый оракул модели не зависит от переключателей фактов;
    - удаление частичной копии в любом предложении плана и спеки называет момент
      (следующий маршрут) — проверка по понятию, а не по фразе (спека, Task 3).

18. **Круг stage1-r12** (`stage1-r12-codex.md`, ответ — `stage1-r12-response.md`). Повтор
    `seal` пропускал создание контейнера без отдельного варианта; вариант `failed` очистки
    обещал «изменений нет» после работы очистителя; равенство вариантов выводилось из формы
    шагов, теряющей порядок изменения относительно закреплений; глубина двойника Python
    была названа, но не исполнялась. Механизм (`r12-local-check/`):
    - вариант выбирает условие `VARIANT_WHEN`; шаг по условию не пропускается; модель
      исполняет каждый вариант, кроме `NOT_MODELED` с причиной, и проверяет это (Tasks 5,
      7, 9);
    - граница наблюдения очистителя — изменения и закрепления root и запуски от `df-build`
      (`run_as(df-build)+` — шаг `builder`) (Tasks 5, 7);
    - модель проверяет постусловие каждого вызова протокола и потерю дерева из родителя и
      карантина; найденная так форма — `settle` закрепляет карантин первым (Task 7);
    - глубина двойника Python исполняется и сверяется с текстом спеки.

19. **Круг stage1-r13** (`stage1-r13-codex.md`, ответ — `stage1-r13-response.md`). Условие
    карантина не называло `ENOTEMPTY` от `rmdir` после успешного очистителя; оракул потери
    карантина смотрел на поле, а не на дерево; план терял родителя в ключах двойников
    синхронизаций. Механизм (`r13-local-check/`):
    - исход «очистка не удалась» — одно определение для `quarantine` и `failed`; договор
      повторяет условие из данных дословно, тест берёт ожидаемый вариант из функции выбора
      `cleanup_tree_variant` по всем входам `CLEANUP_TREE_CASES` (Task 5);
    - потеря дерева в модели — по его идентичности (Task 9 `test_crash_matrix`);
    - случаи двойников синхронизаций и нужные из них — данные (`sync_twin_cases`,
      `SYNC_NEEDED`), общие для модели и плана (Tasks 7, 9).

20. **Круг stage1-r14** (`stage1-r14-codex.md`, ответ — `stage1-r14-response.md`). Оба
    замечания — в проверочных механизмах: модель переиспользовала номер уничтоженного
    дерева вместе с признаком «было закреплено»; проверка теста выбора защищала
    декоратор, а не вычисление ожидания. Механизм (`r14-local-check/`):
    - номер дерева — одна жизнь (`new_tree`: номер, которого нет ни в одном возможном
      состоянии; история невозвратимых номеров отбрасывается) (Task 9 `test_crash_matrix`);
    - `test_cleanup_tree_variant_events` вычисляет `expected_variant =
      cleanup_tree_variant(*case)` и сравнивает с ним события в коде теста; проверка
      разбирает этот код (`ast`), а не комментарий (Task 5).

21. **Круг stage1-r15** (`stage1-r15-codex.md`, ответ — `stage1-r15-response.md`). Проверка
    теста выбора принимала отдельные фрагменты кода за проверенную связь: подмена ожидания
    после верного вычисления и константа в сравнении исхода проходили. Механизм
    (`r15-local-check/`): тест сверяется целиком с единственной допустимой структурой
    (`check_plan.SELECTION_TEST`: декоратор, параметры, каждая инструкция до конца тела,
    оба сравнения); код теста в Task 5 не меняется.

22. **Круг stage1-r16** (`stage1-r16-codex.md`, ответ — `stage1-r16-response.md`). Тест
    выбора выделялся из текста по признакам строк: комментарий без отступа обрывал чтение
    тела, а второе определение с тем же именем оставалось непроверенным. Механизм
    (`r16-local-check/`): Python-блок, где тест назван, разбирает парсер целиком;
    определение `test_cleanup_tree_variant_events` в плане ровно одно; мутации теста выбора
    обязана отвергать сама эта проверка. Код теста в Task 5 не меняется.

23. **Круг stage1-r17** (`stage1-r17-codex.md`, ответ — `stage1-r17-response.md`). Равенство
    определения эталону не закрепляло действующий тест: имя можно было связать с другой
    функцией ниже, а определение — обернуть в замыкание с подменённой функцией выбора.
    Механизм (`r17-local-check/`): тест вынесен в отдельный блок `python selection-test` и
    сверяется как модуль целиком (одно определение верхнего уровня, ничего вокруг); каждый
    Python-блок плана обязан разбираться (заглушка `test_emit_rejects_float` в Task 1
    переписана в две строки); ни один другой блок не называет тест, не связывает имён,
    которые он читает, и не вызывает `globals`/`setattr`/`exec` и т. п. Код теста не менялся.

24. **Круг stage1-r18** (`stage1-r18-codex.md`, ответ — `stage1-r18-response.md`). Запрет
    связывания был списком видов узлов (пропущены `case {**name}` и `except … as name`) и
    не видел записи через атрибут (`pytest.mark.parametrize = …`). Механизм
    (`r18-local-check/`): связывания — из таблицы символов компилятора (`symtable`); вне
    блока `selection-test` имена теста используются только по договору: данные
    (`CLEANUP_OUTCOME`, `CLEANUP_TREE_CASES`) — никак, функции — только вызовом `f(...)`,
    `pytest` — только вызовом `pytest.a.b(...)`. Код теста не менялся.

25. **Рекомендация к реализации (21.10)** — `stage1-implementation-decision.md`. Codex
    рекомендует принять пару без r19 с честным статусом: r18 — not converged, две minor
    закрыты автором локально без независимой перепроверки. **Владелец подтвердил 21.10 три
    решения:** принять пару без r19; исполнение subagent-driven, Task 1 первой с его
    личным мержем; root-приёмка в одноразовых окружениях, предпочтительно CI. Бумажный цикл завершён; ограничения проверки
    переходят в ревью реализации и приёмку. Это не завершение этапа 1. Порядок исполнения,
    маршрут PR Task 0 и порядок root-приёмки (Task 13) — ниже. Матрица CI: `macos-14`
    снимается GitHub 2 ноября 2026 → `macos-15` с явной проверкой архитектуры.

## Global Constraints

- Платформы: macOS arm64 и Ubuntu LTS x86_64; иные — отказ `unsupported platform` (§1).
- Публичная команда — `darkfactory`; `df` в `PATH` не ставится (§3.9). Внутренние имена
  (`df/`, `/opt/df`, `df/<name>/v1`) не меняются.
- Раскладка (§2.2):
  - `/opt/df/{releases/<id>,current,staging,admin/<id>,admin-python,bin}`;
  - `/etc/df/`;
  - `/var/lib/df/{install,doctor}/`.

  Все пути задаёт `df.paths.Layout`, тесты подставляют корень.
- **Root не исполняет ничего, к чему `df-build` имел доступ на запись** (§3.9): ни код и
  интерпретатор релиза, ни файлы staging. Исполнение кода релиза — только от `df-build` без
  сети.
- **Root не открывает ни одного объекта источника** (§3.3). Дерево сборщика читает
  `tar` от `df-build`, root принимает поток через `receive_tree` (Task 3) — собственный
  ограниченный читатель, без `tarfile`. Деревья сборщика удаляет `cleanup_tree`: обход — от
  `df-build`, root выполняет только `fstatat`/`unlinkat`/`renameat` относительно закреплённого
  родителя (Task 5). Root удаляет только свои закрытые деревья (`remove_own_tree`).
- Цель ссылки проверяется **покомпонентно** относительно логического корня релиза: без
  `startswith`, без host-`realpath` (§3.3 п. 4).
- Один нормативный автомат (§3.3): цикл `drive` (Task 7) — закрепить факты → `classify` →
  действие строки → фаза обязана войти в `post` строки. Правила — данные
  `df/install_table.py`; таблица спеки — их отображение (тест сверяет). Действие проверяет своё предусловие;
  сравнения рангов фаз нет. Зарегистрированный релиз повтор не удаляет. Журнал — только
  аудит: ни автомат, ни `doctor` его не читают.
- Писатели (`init`, `install`, `models set`) берут `writer_lock` исключительно **до** чтения
  общего состояния, сразу после захвата выполняют `settle_on_acquire` (Task 7; rev 8 r6 —
  решения повтора вне `drive` используют только закреплённые факты) и держат блокировку до
  конца; меняют общее состояние только между
  `generation_begin`/`generation_end` (Task 4). Читатели, с root и без, блокировку не
  открывают: `read_consistent` по счётчику поколений даёт `Stable` или `Transitional`.
  Команда работает только с одним полным `Snapshot` и явным режимом (Tasks 8a, 9), а
  переходное состояние доходит до `doctor` (Task 12). `install.toml` меняется только действием
  `commit-config`, и `pending.toml` удаляется последним.
- Долговечная запись — через `df.fsutil` (`sync_file`, `sync_dir`, `write_atomic`); ошибка
  синхронизации — отказ (§3.3, «Порядок долговечной записи»).
- Доверенные пути (§3.10) проверяет `trusted_path_problems` (Task 5); невозможность
  установить факт (ACL, `sudo -l`) — отказ, не «нет».
- Пины — **строго SHA** (40 hex) из манифеста; тег, ветка, сокращённый SHA, запись без `sha` —
  отказ установки с именем записи (§3.1).
- Идентификатор релиза `<manifest_sha[:12]>-<platform>-<n>`, `platform ∈ {macos-arm64,
  linux-x86_64}`. Номер записи из `releases.toml` повторно не используется (§3.5, §3.8).
- Digest — канонический обход §3.5; `release.json` в digest не входит.
- Сборка: `uv python install` с `UV_PYTHON_INSTALL_DIR=<release>/python`;
  `uv sync --locked --no-editable --no-default-groups --group …` (группы явно, из профиля)
  (§3.2). Никакого `uv sync`, установки пакетов и самообновления в рантайме (§3.5).
- Каждый файл `/etc/df/*.toml` и служебный файл `/var/lib/df/install/*.toml` несёт `schema =
  "df/<name>/v1"`; неизвестная схема или ключ — отказ (§3.7).
- Выбор моделей — только `/etc/df/models.toml`; env и флаги запускателя его не
  переопределяют (§3.6).
- Сеть, недоступная при пробе модели, — «доступ не подтверждён», никогда «модель запрещена»
  (§3.6).
- Статусы проверок doctor: `ok` / `нарушено` / `не проверено` / `данные недоступны` (§8);
  недоступный факт не даёт `ok`.
- `df` не создаёт GitHub-аккаунтов и не вводит паролей; внешнее действие останавливает `init`
  с инструкцией (§3.3).
- `df/` и `tests/df/` — под authority-root (Task 1): исполняются от root, все PR плана мержит
  человек.
- `_cowork_output/` код не читает (правило воркспейса).
- Строки ≤ 88; type hints; docstrings публичных функций. После каждой задачи:
  - `uv run --frozen --group selfcheck ruff check .`
  - `uv run --frozen --group selfcheck ruff format --check .`
  - `uv run --frozen --group selfcheck pyrefly check`

## Review Focus

1. **Прерванная сборка оставила `/opt/df/releases/<id>`.** Пустой подготовленный каталог —
   фаза `prepared` (сборка идёт в нём), непустой без действительной отметки «собрано» —
   `building` (очистка и сборка заново). Отметка из прошлой загрузки ОС недействительна.
   `current` никогда не указывает на релиз вне `releases.toml` (тесты в Tasks 7 и 9).
2. **Манифест в нынешнем виде** (12-символьные SHA, `spec-runner` только с тегом). Отказ
   называет каждую неподходящую запись, а не первую (тест в Task 2).
3. **Устаревший `uv.lock` на пиненом SHA** (pyproject изменён, lock нет). `uv sync --locked`
   падает; шаг `build` проваливается с именем репо и хвостом stderr; повтор падает так же, а не
   «досборкой» (тест в Task 6).
4. **Неоднозначные имена файлов в артефакте** — перевод строки или NUL-подобные байты, не-UTF-8,
   путь, совпадающий с другим после нормализации. Digest не должен склеивать два разных дерева
   в одно: такие имена — отказ, разделители — `\0` (тест в Task 3).
5. **`/var` → `/private/var` на macOS и неканонические корни.** Логический корень релиза
   обязан быть каноническим (без `.`, `..`, двойных `/`, без ссылок в `Layout`-корне).
   Неканонический корень — отказ на старте, а не ложный «выход за дерево» на полпути (тесты в
   Tasks 1 и 3).

---

## Порядок исполнения

- Задач 17: Task 0 (документы) и 16 задач кода — Tasks 1–14 с 7a и 8a.
- Subagent-driven по зависимостям: у каждой задачи отдельный исполнитель и независимое
  ревью; координатор отвечает за интерфейсы между задачами (блоки «Interfaces») и
  интеграцию. Связанные задачи — последовательно, параллельно — только независимые.
- **Task 1 первой и с человеческим мержем:** она ставит `df/` и `tests/df/` под
  authority-root. Остальные задачи кода начинаются после её мержа; их PR мержит человек.

### Task 0: Поправка спек B rev 8 и A rev 17, пункт плана

**Files:**
- Modify: `docs/superpowers/specs/2026-10-13-df-installer-design.md` — перенос сошедшегося
  черновика `design-b.rev8.md` побайтово.
- Modify: `docs/superpowers/specs/2026-10-12-approval-act-review-design.md` — перенос
  `design-a.rev17.md` побайтово (только имя команды).
- Modify: `TODO.md` — пункт `@id:df-installer-stage1`

- [ ] **Step 1: Правка спек.** Сверка: `diff` черновиков и канона пуст.
- [ ] **Step 2: Пункт TODO.md**

```markdown
- [ ] Установщик df (команда darkfactory), этап 1 (конфигурация, установка, модели): план `docs/superpowers/plans/2026-10-16-df-installer-stage1.md` @owner:github:andrei-shtanakov @id:df-installer-stage1 @epic:eco.dark-factory
```

- [ ] **Step 3: Commit** `docs(df): spec B rev 8 (acceptance, admin env, darkfactory), spec A rev 17; stage-1 plan item`
- [ ] **Step 4: PR.** Один draft PR пары. Агентский мерж допустим, если фактический diff не
  затрагивает authority-root и required checks зелёные. Прозаический PR получает
  scope-аттестацию ai-prosto без вызова модели (`devtools/CLAUDE.md`, «Проза модельному
  ревьюеру не отдаётся»); платное ревью не обещается.

### Task 1: Каркас пакета, раскладка, схемы, долговечная запись

**Files:**
- Create: `df/__init__.py`, `df/paths.py`, `df/config.py`, `df/toml_emit.py`, `df/fsutil.py`
- Modify: `contracts/authority-root/v1/paths.env` (добавить `df/ tests/df/`)
- Test: `tests/df/__init__.py`, `tests/df/test_config.py`, `tests/df/test_toml_emit.py`,
  `tests/df/test_fsutil.py`

**Interfaces:**
- Produces:
  - `Layout(root: Path)` (frozen dataclass). Свойства:
    - `opt`, `releases`, `staging`, `current`, `admin`, `admin_python`, `bin_dir`, `launcher`
      (`opt/bin/darkfactory`), `entry` (`opt/bin/darkfactory-entry.py`);
    - `etc`, `var`, `install_dir`, `journal`, `lock`, `generation` (`install_dir/generation`),
      `pending` (`install_dir/pending.toml`),
      `accounts` (`install_dir/accounts.toml`), `marks_dir` (`install_dir/accept`),
      `verify_dir`, `doctor_dir`, `probes_dir`, `probe_cwd`, `build_home`;
      `local_bin` (`usr/local/bin` под корнем; добавлено в Task 7, R33.F23);
    - `release_dir(id) -> Path`, `admin_dir(id) -> Path`, `staging_dir(id) -> Path`
      (`staging/<id>`), `staging_tree(id) -> Path` (`staging/<id>/tree`), `quarantine`
      (`staging/.quarantine`); `mark_file(id) -> Path` (`marks_dir/<id>.toml`, Task 7).

    `default_layout()` = `Layout(Path("/"))`. Конструктор отказывает (`ConfigError`), если
    `root != Path(os.path.realpath(root))`.
  - `class ConfigError(Exception)`.
  - `InstallConfig(manifest_repo: str, manifest_sha: str, profile: str,
    approved_cli: Mapping[str, Path])`; `REQUIRED_CLI = frozenset({"git", "uv", "tar"})`
    (§3.7, rev 8 r5): конструктор и разбор отказывают `ConfigError` с именами недостающих
    ключей, поэтому `approved_cli["tar"]` в Task 7 всегда определён; `load_install(path:
    Path) -> InstallConfig`; `parse_install(text: str) -> InstallConfig` — то же из текста
    снимка;
    `dump_install(cfg: InstallConfig) -> str`; `config_digest(cfg) -> str` (SHA-256
    `dump_install`).
  - `ReleaseEntry(id: str, digest: str, admin_digest: str, admin_python: str, status:
    Literal["allowed", "revoked"], reason: str | None)` — `admin_python` — точная версия
    `X.Y.Z` административного Python релиза (rev 8, r11); без ключа — `ConfigError`; `ReleasesConfig(releases: tuple[ReleaseEntry, ...])`;
    `load_releases(path) -> ReleasesConfig`; `dump_releases(cfg) -> str`.
  - `KNOWN_SCHEMAS: Mapping[str, str]` — имя файла `/etc/df` → схема, для шести файлов §3.7
    плюс `releases.toml`; `STAGE_OF: Mapping[str, int]` (`trust`, `products`, `publisher` →
    2/3).
  - `STATE_SCHEMAS: Mapping[str, str]` — служебные файлы `/var/lib/df/install`:
    `pending.toml` → `df/pending/v1`, `accounts.toml` → `df/accounts/v1`, `accept/<id>.toml`
    → `df/accept/v1`. Их разбор так же строг: неизвестная схема или ключ — `ConfigError`.
  - `Pending(run_id: str, release_id: str, install_text: str, install_digest: str,
    previous_current: str | None)`; `load_pending(path) -> Pending | None`;
    `dump_pending(p) -> str`.
  - `Account(name: str, uid: int, gid: int, state: Literal["creating", "ready"],
    created_at: str)`; `load_accounts(path) ->
    dict[str, Account]`; `dump_accounts(...) -> str`.
  - `read_optional(path: Path) -> str | None` — общий защищённый читатель служебных
    файлов (обычный файл, без ссылок; нет — `None`); открыт в Task 7 для `load_mark`.
  - В `fsutil`:
    - `sync_file(fd: int) -> None`: macOS — `fcntl(fd, F_FULLFSYNC)`, Linux — `os.fsync`.
      Ошибка поднимается;
    - `sync_dir(fd_or_path: int | Path) -> SyncNote`: `os.fsync` дескриптора каталога. На
      macOS затем пробуется `F_FULLFSYNC`; `EINVAL`/`ENOTSUP` дают
      `SyncNote.FULLFSYNC_UNSUPPORTED` (уходит в evidence), иная ошибка поднимается.
      Как построено (Task 7, R33.F10, решение (а)): каждый такой отказ ещё и копится в
      процессном наборе — `sync_notes() -> frozenset[int]` (`st_dev` каталогов),
      `reset_sync_notes()`; примитивы `SyncNote` наверх не передают, набор пишет в журнал
      `drive` (`sync-note`), в evidence его переносит Task 13;
    - `write_atomic(path: Path, text: str, mode: int = 0o644) -> None` — протокол
      `write_atomic` (Task 7, rev 8 r7): временный файл в том же каталоге (`O_EXCL`),
      `sync_file`, `rename`, `fault_point("renamed", path)`, `sync_dir`. Эффекты: `rename` →
      `sync_dir(parent)`;
    - `remove_durably(path: Path) -> None` — протокол `remove_durably`: `unlink`,
      `fault_point("unlinked", path)`, затем `sync_dir` родителя. Эффекты: `unlink` →
      `sync_dir(parent)`;
    - `fault_point(kind: Literal["renamed", "unlinked", "created", "chown", "chmod"], path:
      Path) ->
      None` — пустая функция модуля; окно «видимо, не закреплено» (§3.3 «Синхронизация»).
      Её вызывают все изменения общего состояния между самим изменением и синхронизацией
      родителя — каждый шаг «изменение» таблицы `PROTOCOLS` (Task 7). Тесты сбоев (Task 9) и
      `test_primitive_protocols_match_table` подменяют её;
    - `sync_tree(root: Path) -> None` — `sync_file` каждого файла и `sync_dir` каждого
      каталога дерева root снизу вверх; ссылки не разыменовываются;
    - `boot_id() -> str`: Linux — `/proc/sys/kernel/random/boot_id`, macOS — `sysctl -n
      kern.bootsessionuuid`. Пустое значение — ошибка;
    - `owner_uid(st: os.stat_result) -> int` — владелец объекта для каждой проверки
      «root или нет» (`receive`, `platform`, `journal`, `accept`): единственный шов,
      который подменяют тесты (`fake_layout`); добавлено в Task 7 (R33.F1).
  - `dumps(data: Mapping[str, object]) -> str` в `toml_emit` — str/int/bool/list[str]/вложенные
    таблицы/массив таблиц; иные типы — `TypeError`.

- [ ] **Step 1: Failing tests**

```python
def test_install_roundtrip(tmp_path):
    cfg = InstallConfig("andrei-shtanakov/ai-orchestrators-workspace", "a" * 40, "core",
                        {"git": Path("/usr/bin/git"), "uv": Path("/usr/local/libexec/"
                         "darkfactory/bin/uv"), "tar": Path("/usr/bin/tar")})
    p = tmp_path / "install.toml"
    write_atomic(p, dump_install(cfg))
    assert load_install(p) == cfg
    assert tomllib.loads(p.read_text())["schema"] == "df/install/v1"

@pytest.mark.parametrize("patch", [{"schema": "df/install/v2"}, {"extra": 1},
                                   {"manifest_sha": "abc"}, {"profile": ""}])
def test_install_strict(tmp_path, patch): ...  # → ConfigError, сообщение называет ключ

@pytest.mark.parametrize("missing", ["git", "uv", "tar"])
def test_install_requires_git_uv_tar(missing): ...  # без ключа → ConfigError с его именем
def test_releases_status_closed_set(tmp_path): ...  # status="maybe" → ConfigError
def test_pending_and_accounts_strict(tmp_path): ...  # лишний ключ, чужая схема → ConfigError

def test_layout_rejects_noncanonical(tmp_path):
    link = tmp_path / "l"; link.symlink_to(tmp_path)
    with pytest.raises(ConfigError): Layout(link)

def test_write_atomic_syncs_file_then_dir(monkeypatch, tmp_path): ...  # порядок вызовов: sync_file → rename → fault_point → sync_dir
def test_fault_point_sees_visible_unsynced_file(monkeypatch, tmp_path): ...
    # fault_point("renamed") поднимает исключение: новый файл уже виден, sync_dir не вызван
def test_sync_error_propagates(monkeypatch, tmp_path): ...             # os.fsync → OSError(EIO) → поднимается
def test_dir_fullfsync_unsupported_is_reported(monkeypatch, tmp_path): ...  # только macOS-ветка
def test_boot_id_nonempty(): assert boot_id()
def test_emit_roundtrip(): assert tomllib.loads(dumps(D)) == D   # D со всеми типами
def test_emit_rejects_float():
    with pytest.raises(TypeError): dumps({"x": 1.5})
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/df -q` → FAIL (`ModuleNotFoundError: df`)
- [ ] **Step 3: Implement** модули по Interfaces. `approved_cli` в TOML — таблица
  `[approved_cli]`, значения — абсолютные пути.
- [ ] **Step 4: Run** → PASS; ruff, pyrefly.
- [ ] **Step 5: Commit** `feat(df): package skeleton, layout, schemas, durable writes`

### Task 2: Профиль манифеста и пины

**Files:**
- Create: `df/manifest.py`
- Test: `tests/df/test_manifest.py`, `tests/df/fixtures/manifest-profiles.toml`,
  `tests/df/fixtures/manifest-current.toml` (копия живого манифеста зонтика на 16.10 — для
  теста отказа)

**Interfaces:**
- Produces:
  - `RepoPin(name: str, url: str, sha: str, groups: tuple[str, ...])` — `url` всегда
    `https://github.com/<owner>/<repo>.git`.
  - `Profile(name: str, python: str, repos: tuple[RepoPin, ...])` — `python` точный `X.Y.Z`.
  - `class ManifestError(Exception)` с полем `problems: tuple[str, ...]`.
  - `manifest_url(repo: str, sha: str) -> str` =
    `https://raw.githubusercontent.com/<repo>/<sha>/workspace-manifest.toml`.
  - `fetch_manifest(repo: str, sha: str, *, opener: Callable[[str], bytes] = _https_get)
    -> bytes` — таймаут 30 с, лимит 1 МиБ.
  - `parse_profile(data: bytes, profile: str) -> Profile`.

Схема секции (предложение в зонтик, решение №2):

```toml
[df.profiles.core]
python  = "3.12.13"
members = ["devtools", "discovery", "discovery-toolkit", "disputatio", "steward", "spec-runner"]
[df.profiles.core.groups]          # группы uv по члену; нет записи — без групп
devtools = ["governance"]
[df.profiles.impresario]
extends = "core"
members = ["impresario"]
```

`members` — ключи записей `[cores]`, `[apps]` или `[tools]`. Политика отказов
(`ManifestError`):
- ключ, найденный в двух секциях;
- член отсутствует в манифесте;
- дубль члена — в своём списке или между базой и наследником;
- ключ в `groups`, не являющийся членом профиля (с учётом базы);
- `extends` глубже одного уровня или циклический.

Правила наследования:
- наследник получает `python` и `groups` базы;
- `python` наследника может отсутствовать или совпадать с базовым, иное — отказ;
- `groups` наследника задаются только для его собственных членов.

`repo_url` — вида `git@github.com:o/r.git` или `https://github.com/o/r(.git)`; иной хост —
отказ.

- [ ] **Step 1: Failing tests**

```python
def test_core_profile_resolves():
    p = parse_profile(FIX.read_bytes(), "core")
    assert [r.name for r in p.repos] == ["devtools", "discovery", "discovery-toolkit",
                                         "disputatio", "steward", "spec-runner"]
    assert p.repos[0].url == "https://github.com/andrei-shtanakov/devtools.git"
    assert p.repos[0].groups == ("governance",) and p.python == "3.12.13"

def test_impresario_extends_core(): ...   # 7 репо, impresario последним; python и groups базы
def test_non_member_entry_without_sha_ignored(): ...   # запись вне профиля с тегом — не отказ

def test_current_manifest_names_every_bad_entry():
    with pytest.raises(ManifestError) as e:
        parse_profile(CURRENT.read_bytes(), "core")   # после дописывания секции профиля
    msgs = "\n".join(e.value.problems)
    for name in ("devtools", "discovery", "discovery-toolkit", "disputatio",
                 "steward", "spec-runner"):
        assert name in msgs                           # Review Focus 2: все, не первая

@pytest.mark.parametrize("case", ["tag-only", "short-sha", "branch-in-sha", "no-profile",
                                  "unknown-member", "dup-key", "extends-cycle",
                                  "extends-two-levels", "dup-member-base-child",
                                  "groups-for-non-member", "child-python-differs",
                                  "gitlab-host", "python-minor-only"])
def test_profile_refusals(case): ...      # → ManifestError

def test_fetch_uses_raw_sha_url_and_caps_size(): ...  # opener-заглушка: URL, >1 МиБ → отказ
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/df/test_manifest.py -q` → FAIL
- [ ] **Step 3: Implement**; проблемы копятся списком, отказ — одним исключением.
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(df): manifest profile parsing with strict SHA pins`

### Task 3: Канонический digest, ACL и приём дерева потоком

**Files:**
- Create: `df/digest.py`, `df/acl.py`, `df/receive.py`
- Test: `tests/df/test_digest.py`, `tests/df/test_acl.py`, `tests/df/test_receive.py`

**Interfaces:**
- Consumes: `sync_file`, `sync_dir` (Task 1).
- Produces:
  - `class TreeError(Exception)`.
  - `tree_digest(root: Path, *, exclude: frozenset[str] = frozenset({"release.json"}))
    -> str` — `"sha256:<hex>"`. Применяется только к деревьям root, недоступным сборщику
    (итоговая копия, административный код, доверенный чекаут). Ошибки чтения (как
    построено в Task 7): `TreeError` — только `ENOENT`/`ENOTDIR` (дерево изменилось при
    обходе); иной `OSError` поднимается как есть — `read_facts` делает из него
    `StepFailed("facts")`, а не очистку. Эффекты: нет.
  - `open_regular(path: Path) -> int` — общий открыватель файла недоверенного дерева
    (`O_NOFOLLOW|O_NONBLOCK`, `fstat` — обычный файл, иначе `TreeError`); его же
    используют читатели `df/inputs.py` (Task 7).
  - `check_name(name: bytes) -> str` — общий валидатор имён для digest и приёма.
  - В `acl.py`:
    - `acl_present_fd(fd: int) -> bool`, `acl_present_path(path: Path) -> bool`. macOS:
      `acl_get_fd_np` / `acl_get_link_np` через `ctypes` (libSystem), `ACL_TYPE_EXTENDED`;
      `ENOENT` от функции означает «ACL нет». Linux: `os.listxattr(..., follow_symlinks=
      False)` содержит `system.posix_acl_access` или `system.posix_acl_default`. Любая
      другая ошибка — `OSError` наружу. Вызывающий трактует её как отказ (§3.10).
  - `CopyLimits(max_bytes: int = 8 << 30, max_stream_bytes: int = 9 << 30, max_objects: int
    = 500_000, max_depth: int = 64, max_path: int = 4096, max_pax: int = 64 << 10,
    max_seconds: float = 1800.0, reap_seconds: float = 10.0)`.
  - `PAX_USED = frozenset({"path", "linkpath", "size"})`; `PAX_IGNORED = frozenset({"mtime",
    "atime", "ctime", "uid", "gid", "uname", "gname", "LIBARCHIVE.creationtime"})`.
  - `BoundedReader(fd: int, *, deadline: float, max_bytes: int)`; `read_exact(n: int) ->
    bytes` — неблокирующий `fd`, ожидание через `select.select` не дольше `deadline -
    time.monotonic()`; срок, конец потока, превышение `max_bytes` — `TreeError`.
    `read_some() -> bytes` — одно чтение до 64 КиБ с тем же сроком и счётчиком; `b""` — EOF.
    Единственное место чтения потока.
  - `class Producer(Protocol)` — вся цепочка запуска производителя (§3.3 п. 3, «Жизненный
    цикл»): `fd: int` — его stdout; `wait(timeout: float) -> int` — код непосредственного
    потомка, не дождался — `TreeError`; `end() -> None` — завершить всю цепочку и убедиться,
    что от неё ничего не осталось, иначе `TreeError`. Реализация для `df-build` —
    `SpawnedProducer` (Task 5); тесты этой задачи используют `LocalProducer` (тот же
    пользователь, `start_new_session=True`, `end` — `killpg` + `wait`).
  - `receive_tree(producer: Producer, dst_parent: Path, name: str, *, limits: CopyLimits =
    CopyLimits(), _hook: Callable[[str, str], None] = _noop) -> None` — приёмная сторона §3.3
    п. 3. `producer.fd` читается только через `BoundedReader`. `dst_parent` — доверенный
    каталог root; `dst_parent/name` не должен существовать; по возврату он root `0700`.
    **Жизненный цикл производителя принадлежит функции:** после EOF — `producer.wait(<остаток
    срока>)`, код ≠ 0 — `TreeError`; в `finally` — `producer.end()` при любом исходе. При
    отказе поднимается `TreeError`; частичная копия (root `0700`) остаётся — функция ничего не
    удаляет, остаток убирает маршрут `copy-partial` или `cleanup` (rev 8, r9: лист без
    скрытых эффектов). **Ни одного пути источника функция не получает**, `tarfile` не
    импортирует. Эффекты: `created` → `SpawnedProducer.end()` → `created` → `sync_tree(R)`
    → `sync_dir(parent)` — частичная копия; `producer.end()` в `finally` вокруг чтения
    потока (в установке `producer` — `SpawnedProducer`); каталоги `0755`, копия полна; п. 5.
    Обе синхронизации протокола `accept_copy` принадлежат этой функции, вызывающий своих не
    добавляет (rev 8, r10).
  - `remove_own_tree(parent: Path, name: str) -> None` — удаление **закрытого дерева root**
    (частичная копия — на следующем маршруте `copy-partial` или `cleanup`;
    незарегистрированный релиз; `admin/<id>`), протокол `remove_own_tree`
    (rev 8 r7): `shutil.rmtree(name, dir_fd=<дескриптор parent>)`, `fault_point("unlinked",
    parent/name)`, `sync_dir(<дескриптор parent>)` — **до возврата**: откат следом
    долговечно удаляет `pending.toml`, и без этого сбой ОС вернул бы дерево без него
    (S1-R7-N1). Предусловие — владелец корня root, иначе `TreeError`; режим
    корня не важен (откат после `chmod 0755` шага `verify`, §3.3). Что id не записан в
    `releases.toml`, гарантирует вызывающий (`cleanup` при записи отказывает). Деревья
    сборщика эта функция не удаляет (их чистит `cleanup_tree`, Task 5). Эффекты: `unlink` →
    `sync_dir(parent)`.
  - `remove_empty_dir(parent: Path, name: str) -> None` — протокол `remove_empty_dir` (rev 8
    r8): `lstat(name, dir_fd=<parent>)` — каталог root, иначе `TreeError`; `os.rmdir(name,
    dir_fd=…)` (непустой — `TreeError`), `fault_point("unlinked")`, `sync_dir(<parent>)`.
    Отсутствующий объект — `TreeError`: пропуск задаёт условие маршрута, а не примитив.
    Эффекты: `unlink` → `sync_dir(parent)`.
  - `resolve_link_in_tree(tree: Path, logical_root: PurePosixPath, link_rel: PurePosixPath)
    -> PurePosixPath` — относительный путь итоговой цели внутри дерева или `TreeError`.
    Вызывается только по итоговой копии. Алгоритм:
    1. Логический путь цели строится из текста ссылки: абсолютный текст берётся как есть,
       относительный — от логического каталога ссылки.
    2. Путь разбирается по компонентам слева направо со стеком: `.` пропускается, `..`
       снимает элемент. Попытка подняться выше `logical_root` или первые компоненты
       абсолютного пути, не совпадающие с `logical_root`, — отказ.
    3. Каждый промежуточный и конечный компонент проверяется по `os.lstat` в `tree`. Если
       компонент — ссылка, её текст подставляется по тому же правилу и разбор продолжается.
    4. Счётчик переходов > 40 — отказ (цикл); отсутствующий компонент — отказ (висячая цель).
    5. Host-`realpath` и `startswith` не используются.
  - `check_links(tree: Path, logical_root: PurePosixPath) -> list[str]` — все ссылки дерева
    через `resolve_link_in_tree`. Эффекты: нет.

Алгоритм digest (нормативно, §3.5):
- обход `os.scandir` без следования ссылкам;
- элементы сортируются по байтам UTF-8 относительного пути (`/`-разделитель);
- запись элемента: `type \0 path \0 mode \0 payload \n`, где `type ∈ {f, d, l}`;
  `mode = "x"`, если у файла есть любой бит исполнения, иначе `"-"` (для `d` и `l` — `"-"`);
  `payload` — hex SHA-256 содержимого для `f`, цель ссылки (байты как есть) для `l`, пусто для
  `d`;
- digest = SHA-256 конкатенации записей.

`check_name` отказывает (`TreeError`) на:
- имени не в UTF-8 или с `\n`;
- двух путях одного каталога, равных после NFC-нормализации (на APFS они совпали бы).

`receive_tree` (нормативно, §3.3 п. 3; прототип формата сверен с bsdtar 3.5.3 —
`r3-local-check/rtar.py`, в репо не переносится):
1. `os.mkdir(name, 0o700, dir_fd=<parent>)` — до первого байта потока; корень открыт как
   `root_fd`.
2. Заголовки по 512 байт через `read_exact`:
   - нулевой блок — конец; второй нулевой блок обязателен. Затем **дочитывание до EOF**
     через `read_some` (тот же срок и `max_stream_bytes`): допустимы только NUL, иначе
     `TreeError("data after end of archive")`. Только после EOF — `producer.wait` и код 0;
   - контрольная сумма сверяется; числовое поле — только `[0-7]` с пробелами/NUL в конце;
     иначе `TreeError` (никаких `ValueError` наружу);
   - тип `x`: `size ≤ max_pax` проверяется **до** чтения тела; два `x` подряд — отказ.
     `parse_pax(body: bytes) -> dict[str, bytes]` — запись `<длина> <ключ>=<значение>\n`:
     длина `[1-9][0-9]*` считает всю запись и больше своего префикса на ≥ 2; запись целиком в
     теле и кончается `\n`; ключ непуст, ASCII, не повторяется; позиция строго растёт
     (§3.3 п. 3, rev 8 r5). Повтор проверяется по отдельному множеству `seen` всех
     встреченных ключей, включая пропускаемые; «перед записью уже был `x`» — отдельный флаг
     читателя, а не непустота результата `parse_pax` (rev 8 r6). `size` — только `[0-9]{1,20}`. Иное — `TreeError`. Ключи
     из `PAX_USED` применяются к следующей записи, из `PAX_IGNORED` пропускаются, прочие —
     `TreeError` с именем ключа. Тип `g` — отказ;
   - тело обычного файла читается кусками ≤ 1 МиБ в пределах `max_bytes`; выравнивание до
     512 — тоже через `read_exact`.
3. Нормализация имени — одна функция `member_path(raw: bytes, is_dir: bool) -> tuple[str,
   ...]` для имён и целей жёстких ссылок: снять одно `./`, у каталога одно завершающее `/`;
   длина ≤ `max_path`. Пустой результат — **запись корня**: допустима только типом `5` и только
   первой; права не применяются. Для каждой записи:
   - имя: компоненты проходят `check_name`, без пустых, `.`, `..`; глубина ≤ `max_depth`;
   - родитель: путь компонентов проходится от `root_fd` открытием `O_RDONLY|O_DIRECTORY|
     O_NOFOLLOW` относительно предыдущего дескриптора. Каждый компонент обязан быть
     каталогом, уже созданным этим приёмом (множество принятых каталогов в памяти);
   - `DIRTYPE` — `os.mkdir(leaf, 0o700, dir_fd=parent_fd)`; права `0o755` выставляются после
     последнего члена, чтобы каталог не стал проходимым раньше времени;
   - `REGTYPE` — `os.open(leaf, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600, dir_fd=…)`,
     тело — ровно `size` байт через `read_exact` кусками ≤ 1 МиБ, затем выравнивание до 512;
     затем `fchmod` (`0o755`, если в поле `mode` заголовка есть бит исполнения, иначе
     `0o644`) и `sync_file`;
   - `SYMTYPE` — `os.symlink(<linkpath>, leaf, dir_fd=…)`, текст как данные;
   - `LNKTYPE` — цель (через `member_path`) обязана быть ранее принятым обычным файлом этого
     потока. Содержимое копируется из назначения в новый inode (через дескрипторы
     назначения); тело записи, если `size > 0`, читается в пределах и отбрасывается;
   - иной тип (`3`, `4`, `6`, `7`, GNU `L`/`K`/`S`) — отказ;
   - владельцы, `uname`/`gname`, xattr и ACL не применяются.
4. Пределы `max_objects`, `max_bytes`, `max_stream_bytes` и срок действуют внутри
   `read_exact` и счётчиков, а не после выдачи записи.
5. После потока и `producer.end()` каталоги получают `0o755` (кроме корня); затем
   `sync_tree(dst_parent/name)` (корень включён — его режим) — шаг `sync_tree` протокола
   `accept_copy` (rev 8 r7); затем `sync_dir(dst_parent)` — его последний шаг. Других
   синхронизаций копия не получает — ни здесь, ни у вызывающего (rev 8, r10).

Точки `_hook(point, rel)` (только тесты): `member`, `mid-copy`.

- [ ] **Step 1: Failing tests**

```python
def test_digest_stable_and_order_independent(tmp_path): ...  # два дерева, разный порядок создания
def test_digest_sensitive_to_exec_bit_content_link_target(tmp_path): ...
def test_digest_excludes_release_json(tmp_path): ...
def test_digest_refuses_newline_name_fifo_nfc_collision(tmp_path): ...   # Review Focus 4
def test_acl_absent_on_fresh_file(tmp_path): assert not acl_present_path(tmp_path / "f")
def test_acl_detected(tmp_path): ...   # macOS: chmod +a "everyone allow write"; Linux: setfacl, если есть, иначе skip с причиной

LR = PurePosixPath("/opt/df/releases/abc-macos-arm64-1")   # логический корень, не существует
def producer(tmp_path, data: bytes | None = None, *, argv=None) -> LocalProducer: ...
    # фикстура: LocalProducer над процессом текущего пользователя, отдающим data или
    # настоящий tar по argv
def tar_bytes(members: Sequence[Member], *, pax: Mapping[int, bytes] = {}) -> bytes: ...
    # tests/df/tarlit.py (rev 8 r7, S1-R7-N2): БУКВАЛЬНЫЙ генератор ustar без tarfile —
    # ровно перечисленные записи в данном порядке, без неявной записи корня и родителей;
    # pax[i] — сырое тело x-заголовка перед записью i (любое, в т. ч. неверное);
    # контрольные суммы верны, в конце два нулевых блока. Member = (name, type|mode,
    # data|target). Положительные примеры перечисляют каждый родительский каталог сами
def test_tar_bytes_is_literal(tmp_path): ...
    # системный tar -tvf над tar_bytes([...]) == перечисленные имена и типы, без добавлений
def test_receive_requires_producer_protocol(tmp_path): ...
    # строгий fake без end() или fd → TypeError до чтения; обычный Popen не принимается
def test_receive_normalizes_modes_and_keeps_root_closed(tmp_path):
    receive_tree(producer(tmp_path, tar_bytes([("a", 0o4777, b"x")])), rel, "r")
    assert stat.S_IMODE((rel / "r/a").stat().st_mode) == 0o755
    assert stat.S_IMODE((rel / "r").stat().st_mode) == 0o700
@pytest.mark.parametrize("member", ["../x", "/abs", "a/../../x", "./..", "a//b"])
def test_bad_names_refused(tmp_path, member): ...
@pytest.mark.parametrize("tree", ["empty", "full"])
def test_real_tar_stream_accepted(tmp_path, tree): ...
    # настоящий tar с TAR_FLAGS[detect_platform()]: запись корня "./", каталоги "d/",
    # длинное (>100 байт) и не-ASCII имя, жёсткая и символическая ссылка; digest = источнику
@pytest.mark.parametrize("case", ["root-not-dir", "root-not-first", "root-twice"])
def test_root_record_rules(tmp_path, case): ...
@pytest.mark.parametrize("key", ["SCHILY.xattr.user.k", "GNU.sparse.size", "hdrcharset"])
def test_unknown_pax_key_refused(tmp_path, key): ...
def test_symlink_as_intermediate_dir_refused(tmp_path): ...
    # члены: "d" SYMTYPE -> /tmp/outside; "d/f" REGTYPE → отказ; в /tmp/outside ничего не создано
def test_device_fifo_sparse_refused(tmp_path): ...
def test_hardlink_to_unaccepted_target_refused(tmp_path): ...
def test_hardlink_becomes_independent_file(tmp_path): ...
def test_parent_must_come_from_stream(tmp_path): ...   # "a/b" без члена "a" → отказ
@pytest.mark.parametrize("limit", ["max_objects", "max_bytes", "max_stream_bytes",
                                   "max_depth", "max_path"])
def test_limits_refuse_and_reap_producer(tmp_path, limit): ...
    # TreeError; producer.end() вызван, производитель не жив; частичная копия root 0700 на
    # месте — её убирает маршрут (S1-R10-N3)
def test_silent_producer_with_open_stdout_hits_deadline(tmp_path): ...
    # производитель пишет 10 байт и спит; отказ за max_seconds+ε, процесс убит
def test_oversized_pax_refused_before_body_read(tmp_path): ...
    # x-заголовок size=8 ГиБ с верной контрольной суммой, за ним 16 МиБ данных: отказ
    # быстрее 1 с, прочитано ≤ 1024 байт, причина — предел размера pax, а не checksum
@pytest.mark.parametrize("case", ["bad-checksum", "non-octal-size", "truncated-body",
                                  "missing-second-zero-block", "producer-exit-1"])
def test_malformed_stream_is_tree_error(tmp_path, case): ...
@pytest.mark.parametrize("body", [b"0 mtime=x\n", b"99 a=b\n", b"9 mtime=x", b"6 =xy\n",
                                  b"010 mtime=1\n", b"11 size=-1\n", b"12 path=a\n12 path=b\n",
                                  b"11 mtime=1\n11 mtime=2\n"])
def test_malformed_pax_record_refused_fast(tmp_path, body): ...
    # отдельный процесс с timeout=5: TreeError быстрее 1 с, producer.end() вызван,
    # частичная копия на месте
    # (S1-R5-N5: нулевая длина зацикливала разбор вне срока читателя)
def test_two_pax_headers_with_ignored_keys_only_refused(tmp_path): ...
    # x{mtime} + x{mtime} + обычный файл → TreeError("two pax headers") (S1-R6-N3);
    # положительный контроль: один x{mtime} + обычный файл → принят
def test_receiver_never_imports_tarfile(): ...   # "tarfile" not in sys.modules после импорта df.receive в чистом процессе
def test_pax_ignored_owner_keys_not_applied(tmp_path): ...
    # x{uid=0, gid=0, uname=root, gname=wheel, mtime} + файл → принят; владелец — текущий
    # пользователь, режим нормализован (PAX_IGNORED)
@pytest.mark.parametrize("key", ["SCHILY.xattr.user.x", "LIBARCHIVE.xattr.user.x",
                                 "SCHILY.acl.access"])
def test_pax_xattr_acl_keys_refused(tmp_path, key): ...   # TreeError с именем ключа; ничего не удалено
def test_refused_receive_removes_nothing(monkeypatch, tmp_path): ...
    # S1-R9-N1: отказ на середине потока → TreeError, частичная копия root 0700 на месте;
    # подмена fsutil/shutil: ни одного unlink/rmtree/rmdir от receive_tree
def test_existing_destination_refused(tmp_path): ...
def test_remove_own_tree_refuses_foreign_owner(tmp_path): ...  # владелец не root → TreeError
def test_remove_own_tree_accepts_0755_root(tmp_path): ...      # откат после chmod 0755 (S1-R4-N2)
def test_end_padding_drained_before_wait(tmp_path): ...
    # настоящий вывод tar + 1 МиБ NUL после конца (больше буфера канала): принят, без
    # отказа по сроку; prototype r4-local-check/rtar.py
def test_data_after_end_refused(tmp_path): ...       # ненулевой байт после двух нулевых блоков
def test_padding_counts_toward_stream_cap(tmp_path): ...   # выравнивание сверх max_stream_bytes → отказ
def test_forked_producer_ended(tmp_path): ...
    # обёртка порождает производителя и не ждёт его: после возврата receive_tree (отказ по
    # сроку) производитель не жив; двойник с kill() одного потомка оставляет его живым
@pytest.mark.parametrize("target", [
    "/etc/passwd",
    "../../outside",
    "/opt/df/releases/other/x",
    f"{LR}/../../../../etc/passwd",          # строковый префикс совпадает — всё равно отказ
    f"{LR}/a/../../x",
    f"{LR}x/y",                              # префикс без разделителя
])
def test_escaping_link_refused(tmp_path, target): ...
def test_escape_via_link_chain_refused(tmp_path): ...  # a -> b (внутри), b -> /etc: отказ на a и b
def test_escape_via_intermediate_dir_link_refused(tmp_path): ...  # d -> /tmp; ссылка f -> d/x
def test_link_cycle_refused(tmp_path): ...             # a -> b, b -> a
def test_dangling_link_refused(tmp_path): ...
def test_internal_absolute_link_kept_verbatim(tmp_path):
    members = [("python", DIR), ("python/cpython-3.12.13-x", DIR),
               ("python/cpython-3.12.13-x/bin", DIR),
               ("python/cpython-3.12-x", SYM, f"{LR}/python/cpython-3.12.13-x")]
    receive_tree(producer(tmp_path, tar_bytes(members)), rel, "r")
    assert os.readlink(rel / "r/python/cpython-3.12-x") == f"{LR}/python/cpython-3.12.13-x"
    assert check_links(rel / "r", LR) == []
def test_member_without_received_parent_refused(tmp_path): ...
    # тот же архив без записи "python" → TreeError (родитель не создан этим приёмом)
def test_receive_then_digest_equals_source_digest(tmp_path): ...
def test_noncanonical_logical_root_refused(tmp_path): ...   # Review Focus 5: "/opt/df/./r", "/opt//df/r"
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** по алгоритмам выше.
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(df): canonical digest, ACL probe, tree receive from stream`

### Task 4: Журнал установки, блокировка писателей, счётчик поколений

**Files:**
- Create: `df/journal.py`
- Test: `tests/df/test_journal.py`

**Interfaces:**
- Consumes: `write_atomic`, `Layout` (Task 1).
- Produces:
  - `writer_lock(layout: Layout) -> ContextManager[int]` — `fcntl.flock(LOCK_EX|LOCK_NB)` на
    `layout.lock` (root, `0600`, создаётся при первой установке); отдаёт дескриптор. Занято —
    `StepFailed("lock", "идёт другая административная операция")`, без ожидания. Берут только
    писатели (`init`, `install`, `models set`), **до** чтения общего состояния и до конца
    команды (§3.3 «Согласованное чтение»). Разделяемого режима нет.
  - `generation_begin(layout) -> None` / `generation_end(layout) -> None` — под
    `writer_lock`: число в `layout.generation` (root, `0644`, нет файла = 0) становится
    нечётным / чётным, `write_atomic`. `begin` при уже нечётном числе (прерванный писатель)
    оставляет его нечётным, `end` при чётном — ошибка программы.
  - `Stable(value: T, generation: int)`, `Transitional(generation: int)` (frozen, generic
    по `T`) — типизированный результат чтения §3.3 «Согласованное чтение».
  - `read_consistent(layout, read: Callable[[], T], *, attempts: int = 3) -> Stable[T] |
    Transitional` — для читателей: `g1` → `read()` → `g2`. Нечётное `g1` — `Transitional(g1)`
    без вызова `read`; `g1 != g2` — повтор, после `attempts` — `StepFailed("snapshot",
    "состояние меняется")`. Блокировку не открывает. Решает потребитель: `doctor` выводит
    переходное состояние (Task 12), остальные команды отказывают (Task 9).
  - `Step(name: str, check: Callable[[], bool], do: Callable[[], None])` — `check()` = «факт
    уже достигнут».
  - `class StepFailed(Exception)` (поля `step`, `detail`); `class NeedsHuman(Exception)` (поле
    `instruction`) — остановка `init` до внешнего действия (§3.3).
  - `run_steps(steps: Sequence[Step], journal: Path, *, run_id: str,
    fail_after: str | None = None) -> None` — вызывающий уже держит `writer_lock`. Только для
    подготовительных шагов 1–3 Task 9; приём ведёт `drive` (Task 7).
  - `journal_event(journal: Path, *, run_id: str, step: str, event: str, detail: str = "")
    -> None` — та же дозапись для `drive`.
  - `read_journal(journal: Path) -> list[dict[str, object]]` — только для показа аудита.

Семантика:
- Для каждого шага: если `check()` → запись `skip`; иначе запись `start` → `do()` → `check()`
  обязан стать истинным, иначе `StepFailed`. Затем запись `done`.
- Записи — JSON-строки (`ts`, `run_id`, `step`, `event`, `detail`), дозапись с `sync_file`.
  Журнал `0600` root.
- Решение «делать ли шаг» принимает **только `check()`**; журнал — аудит.
- `fail_after` — только для тестов: исключение `_Interrupted` после `do()` шага с этим именем,
  до `done`. В CLI не выводится.

- [ ] **Step 1: Failing tests**

```python
def test_rerun_after_interrupt_resumes_by_fact(tmp_path): ...  # fail_after="b" → повтор: a skip, b skip (факт есть), c done
def test_journal_record_alone_does_not_skip(tmp_path): ...     # done в журнале, check()=False → шаг делается
def test_postcheck_false_fails(tmp_path): ...
def test_writer_lock_second_process_refused(tmp_path): ...     # второй процесс держит flock
def test_generation_parity(tmp_path): ...                      # 0 → begin 1 → end 2; begin при 1 → 1
def test_reader_rejects_changed_generation(tmp_path): ...
    # read() внутри меняет поколение (имитация писателя): повтор; трижды → «состояние меняется»
def test_reader_aba_not_accepted(tmp_path): ...
    # писатель A→B→A между чтениями: числа различаются, снимок не принят
def test_reader_odd_generation_is_transitional(tmp_path): ...   # Transitional(g), read() не вызван
def test_reader_never_opens_lock(tmp_path): ...   # audit hook: нет события open для layout.lock
def test_needs_human_stops_without_done(tmp_path): ...
```

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): install journal (audit only), writer lock, generation counter`

### Task 5: Платформа, учётка `df-build`, доверенные пути, запуск от роли

**Files:**
- Create: `df/platform.py`
- Test: `tests/df/test_platform.py` (unit: argv, разбор выводов), `tests/df/fixtures/sudo-l/`
  (выводы `sudo -l -U` обеих ОС, снятые при реализации), `tests/df/test_platform_root.py`
  (маркер `root`, Task 13)

**Interfaces:**
- Consumes: `Account`, `load_accounts`, `dump_accounts`, `write_atomic`, `sync_dir` (Task 1);
  `acl_present_path` (Task 3).
- Produces:
  - `PlatformName = Literal["macos-arm64", "linux-x86_64"]`;
    `detect_platform() -> PlatformName` (иначе `ConfigError`).
  - `create_build_user(layout: Layout) -> Account` — §3.2 и §3.3 «Создание учётки — по
    намерению».
    - Нет записи в `accounts.toml`, а имя `df-build` в системе есть → `StepFailed(
      "build-user", "пользователь df-build существует, но создан не darkfactory: удалите или
      переименуйте")`.
    - Нет записи и нет имени → выбрать свободные UID/GID и **сначала** записать
      `Account(state="creating", uid, gid)` через `write_atomic`. Затем команды ОС — по
      данным `ACCOUNT_ATTRS` (ниже), по одной команде на элемент.
  - `ACCOUNT_ATTRS: Mapping[PlatformName, tuple[AttrOp, ...]]`, `AttrOp(kind:
    Literal["group", "user"], attr: str, value: Callable[[Account, Layout], str])` (rev 8
    r6, §3.3 «Запись пользователя и группы — набор атрибутов»). Порядок — порядок команд:
    - macOS (UID/GID в 400–499): группа — `RecordName` (`dscl . -create /Groups/df-build`),
      `PrimaryGroupID=<gid>`; пользователь — `RecordName` (`dscl . -create
      /Users/df-build`), `Password='*'`, `UserShell=/usr/bin/false`, `IsHidden=1`,
      `PrimaryGroupID=<gid>`, `NFSHomeDirectory=<home>`, `UniqueID=<uid>`; каждый атрибут —
      отдельный `dscl . -create /Users/df-build <attr> <value>`;
    - Linux: группа — одна строка `groupadd --system --gid <gid> df-build`; пользователь —
      одна строка `useradd --system --uid <uid> --gid df-build --home-dir <home>
      --no-create-home --shell /usr/sbin/nologin df-build`. Атрибуты строки (`uid`, `gid`,
      `home`, `shell`) читаются `getent`; строка `shadow` в намерение не входит.
  - `read_record(kind, name) -> dict[str, str] | None` — `dscl . -read` (macOS), `getent
    passwd|group` (Linux); нет записи — `None`; ошибка чтения — `StepFailed`, а не «нет».

      Перед записью намерения домашний путь и `home.parent / HOME_TMP_NAME` обязаны
      отсутствовать, иначе `StepFailed("build-user", "каталог <путь> существует, создан не
      darkfactory")`.
    - Домашний каталог (§3.3 «Домашний каталог создаётся целиком под временным именем», rev
      8 r5): `HOME_TMP_NAME = ".df-build-home.creating"`; `mkdir(tmp, 0o700)` относительно
      дескриптора родителя (`O_EXCL`-семантика `mkdir`), `fchown(uid, gid)`, `fchmod(0o700)`,
      `sync_dir(tmp)`, `rename_noreplace(parent_fd, HOME_TMP_NAME, home.name)`,
      `sync_dir(parent)`.
    - Запись `creating` → группа, затем пользователь (rev 8 r6): записи нет — все команды
      `ACCOUNT_ATTRS` по порядку; запись есть и каждый **присутствующий** атрибут равен
      намерению — выполнить команды только для отсутствующих; присутствующий атрибут с
      другим значением — `StepFailed("build-user", "<kind> df-build: <attr> = <факт>, в
      намерении <значение>")`, ничего не меняется. Частичную запись своей делает намерение:
      при его записи имени не было (проверено выше), записи создаёт только root. Домашний каталог: по `HOME_TMP_NAME` есть **пустой** каталог (не ссылка,
      владелец root или записанный UID) — `rmdir` и создать заново; непустой — `StepFailed`.
      Домашний путь есть — принимается, только если не ссылка, владелец — записанный UID,
      `0700`; иначе `StepFailed` (чужой каталог, в том числе принадлежащий root, не
      присваивается).
  - `rename_noreplace(dir_fd: int, src: str, dst: str) -> None` — macOS
    `renamex_np(..., RENAME_EXCL)`, Linux `renameat2(..., RENAME_NOREPLACE)` через `ctypes`;
    `EEXIST` и отсутствие вызова — `StepFailed`, а не обычный `rename`.

    Затем `account_problems(account)`; пусто → запись `state="ready"` (`write_atomic`). Иначе
    — `StepFailed` со списком, запись остаётся `creating`. Принятый домашний каталог перед
    `ready` закреплён: созданный — `sync_dir(parent)` после `rename_noreplace`, найденный
    повтором — `settle_on_acquire` при захвате (Task 7, источник `home_parent`). Своего
    `sync` перед `ready` функция не делает: модель `r18-local-check/account.py` закрепляет
    только там же (rev 8 r6).
  - `ADMIN_GROUPS = frozenset({"root", "wheel", "admin", "sudo", "adm"})`.
  - `account_problems(account: Account) -> list[str]` — **соответствие намерению**: факты ОС
    против записи, **независимо от `state`** (§3.3 «Создание учётки»). Проверки §3.2:
    - `pwd`: UID = записанному и ≠ 0; ни одно другое имя не имеет этого UID;
    - основная группа — `df-build` с записанным GID;
    - `os.getgrouplist` не пересекается с `ADMIN_GROUPS` и GID 0;
    - оболочка ∈ {`/usr/bin/false`, `/usr/sbin/nologin`, `/sbin/nologin`};
    - домашний каталог: владелец `df-build`, `0700`;
    - `sudo_rules("df-build") == "none"`.
  - `verify_build_user(layout: Layout) -> list[str]` — **готовность**: запись есть, `state =
    "ready"`, и `account_problems` пуст. Пустой список = годен; единственное условие первого
    процесса от `df-build`.
  - `sudo_rules(name: str) -> Literal["none", "some", "unknown"]` — `LC_ALL=C sudo -n -l -U
    <name>`. `"none"` — только при коде 0 и точной фразе «is not allowed to run sudo»
    (сверено с фикстурами обеих ОС). Иначе — `"unknown"`, и это проблема, а не «прав нет».
  - `whoami_as(user: str) -> tuple[int, int]` — положительный контроль: `run_as(user,
    ["/usr/bin/id", "-u"], env={"PATH": "/usr/bin:/bin", "LANG": "C"}, cwd=Path("/"),
    network=False, timeout=10.0)` и то же с `-g`. Ненулевой код, `launched=False` или пустой
    вывод — `StepFailed`.
  - `spawn_as(user: str, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path,
    network: bool, reap_seconds: float = 10.0) -> SpawnedProducer` — тот же механизм
    запуска, что `run_as`, но stdout отдаётся потоком (для `receive_tree`). `Popen` с
    `start_new_session=True`, `stdin=DEVNULL`: без управляющего терминала `sudo` не создаёт
    псевдотерминал. Проверка запуска: процесс не завершился кодом механизма
    (`sudo`/`setpriv`/`sandbox-exec`) в первые 2 с.
  - `SpawnedProducer` реализует `Producer` (Task 3): `fd` — stdout; `wait(timeout)` —
    `Popen.wait`; `end` — ниже.
  - `SpawnedProducer.end() -> None` — шаг `process(df-build)` протокола `accept_copy` (rev 8,
    r10): `os.killpg(pid, SIGKILL)` (нет группы — не ошибка), затем
    `reap_user_processes(user)` для потомков, покинувших группу, затем `Popen.wait(
    reap_seconds)`; остался процесс с UID `user` — `TreeError`. Эффекты:
    `reap_user_processes()`.
  - `TAR_FLAGS: Mapping[PlatformName, tuple[str, ...]]` — §3.3 п. 3: macOS `("--format",
    "pax", "--no-mac-metadata", "--no-xattrs", "--no-acls", "--no-fflags",
    "--no-read-sparse")`, Linux
    `("--format=pax", "--no-xattrs", "--no-acls", "--no-selinux")`.
  - `CLEANER_ARGV(root: Path) -> list[list[str]]` = `[["/bin/chmod", "u+rwx", str(root)],
    ["/usr/bin/find", str(root), "-mindepth", "1", "-type", "d", "!", "-perm", "-u+rwx",
    "-exec", "/bin/chmod", "u+rwx", "{}", ";"], ["/usr/bin/find", str(root), "-mindepth",
    "1", "-delete"]]` — §3.3 «Очистка» (rev 8, r4): корень открывает отдельная команда,
    вложенные каталоги — немедленная форма `;`. Сверено с BSD find
    (`r4-local-check/cleaner.sh`); GNU find сверяет CI (Task 13).
  - `cleanup_tree(layout: Layout, parent: Path, name: str, *, quarantine_label: str | None,
    budget: float = 300.0) -> Literal["removed", "quarantined", "failed"]`:
    1. root открывает `parent` (`O_RDONLY|O_DIRECTORY|O_NOFOLLOW`) и держит `pfd`;
    2. `os.stat(name, dir_fd=pfd, follow_symlinks=False)`: нет — `"removed"` (вариант
       `absent`); ссылка или не каталог — `os.unlink(name, dir_fd=pfd)`,
       `fault_point("unlinked", …)`, `sync_dir(pfd)` — **до возврата**, `"removed"`
       (вариант `link`, rev 8 r11);
    3. `run_as("df-build", argv, env={"PATH": "/usr/bin:/bin", "LANG": "C"},
       cwd=layout.build_home, network=False, timeout=<остаток бюджета>)` для всех трёх
       команд `CLEANER_ARGV(parent / name)` по очереди;
    4. `os.rmdir(name, dir_fd=pfd)`, `fault_point("unlinked", …)`; успех — `sync_dir(pfd)`,
       `"removed"` (вариант `tree`);
    5. иначе (таймаут, код ≠ 0, `ENOTEMPTY`): при `quarantine_label` — `os.rename(name,
       f"{quarantine_label}.{n}", src_dir_fd=pfd, dst_dir_fd=<fd quarantine>)`,
       `fault_point("renamed", …)`, `sync_dir(<fd quarantine>)`, затем `sync_dir(pfd)` —
       сначала приёмник: имя долговечно в карантине раньше, чем уходит из `parent`;
       `"quarantined"` (вариант `quarantine`); без него — `"failed"` (вариант `failed`):
       имя дерева в `parent` на месте, root его не менял, но очиститель уже мог изменить
       режимы и удалить часть содержимого — это дерево `df-build`, root ему не доверяет;
       вызывающий (`retry_quarantine`) оставляет имя до следующего раза.
    Протокол `cleanup_tree` — с вариантами (rev 8, r11); вариант выбирает его условие
    (`VARIANT_WHEN`, rev 8 r12; дословно, r13): вариант `absent` — имени нет; вариант
    `link` — ссылка или не каталог; вариант `tree` — каталог; все команды очистителя — код
    0 в срок, и `rmdir` успешен (среда); вариант `quarantine` — каталог; очистка не удалась
    (команда очистителя не уложилась в срок или вернула код ≠ 0, либо `rmdir` вернул
    `ENOTEMPTY`; среда), есть `quarantine_label`; вариант `failed` — каталог; очистка не
    удалась (команда очистителя не уложилась в срок или вернула код ≠ 0, либо `rmdir` вернул
    `ENOTEMPTY`; среда), `quarantine_label=None`. Выбор как функция —
    `cleanup_tree_variant(obj, cleaner, rmdir, label)` в `install_table.py`; после неудачной
    команды очистителя `rmdir` не вызывается. Граница наблюдения — изменения и синхронизации root и запуски от `df-build`
    (`run_as(df-build)+`: всё, что очиститель меняет внутри дерева). Модель исполняет
    `tree` и `quarantine` (исход очистителя — ветвление); `link`, `absent`, `failed` —
    `NOT_MODELED` с причиной.
    Эффекты (`tree`): `run_as(df-build)+` → `unlink` → `sync_dir(parent)`.
    Эффекты (`link`): `unlink` → `sync_dir(parent)`.
    Эффекты (`quarantine`): `run_as(df-build)+` → `renamed` → `sync_dir(quarantine)` → `sync_dir(parent)`.
    Эффекты (`absent`): нет.
    Эффекты (`failed`): `run_as(df-build)+`.

    Других root-операций над деревом нет: функция не открывает, не перечисляет и не удаляет
    ничего внутри `parent/name`.
  - `cleanup_build_home(layout: Layout, rel: str, *, budget: float = 300.0) ->
    Literal["removed", "absent"]` — протокол `cleanup_build_home` (rev 8, r9), лист маршрута
    `verify`; `rel` — одно имя (`tmp`, `cache`) внутри `layout.build_home` (каталог
    `df-build`). Всё — от `df-build` через `run_as`, root над этими путями не делает ничего:
    1. `["/usr/bin/find", str(build_home), "-mindepth", "1", "-maxdepth", "1", "-name", rel,
       "-print"]`: код 0 и пустой вывод — `"absent"` (успех, `ABSENT_OK`); код 0 и ровно
       строка `build_home/rel` — дальше; иначе (код ≠ 0, ошибка доступа, иной вывод,
       `launched=False`) — `StepFailed("verify", …)`: ошибка не равна отсутствию;
    2. три команды `CLEANER_ARGV(build_home / rel)`, третья без `-mindepth 1` — очиститель
       удаляет и сам корень; код ≠ 0 или срок — `StepFailed`; иначе `"removed"`.
       Варианты протокола (rev 8, r11, r12): вариант `tree` — `find` от `df-build` нашёл
       имя (очиститель удаляет дерево); вариант `absent` — `find` от `df-build` имени не
       нашёл (п. 1). Граница наблюдения —
       та же, что у примитива очистки дерева: всё здесь — запуски от `df-build`,
       root-событий нет.
       Эффекты (`tree`): `run_as(df-build)+`. Эффекты (`absent`): `run_as(df-build)+`.

    Синхронизации нет: содержимое `build_home` не источник фактов и никогда не доверено;
    потерянное сбоем ОС удаление повторит следующий `verify`.
  - `retry_quarantine(layout: Layout) -> list[str]` — перечисляет **свой** каталог
    `layout.quarantine` (root, `0711`) и для каждого имени вызывает `cleanup_tree(layout,
    layout.quarantine, entry, quarantine_label=None)`; возвращает оставшиеся имена (для
    журнала и `doctor`). Неудача установку не останавливает.
  - `reap_user_processes(name: str, *, timeout: float = 5.0) -> list[int]` — уборка §3.3 п. 1:
    `pkill -KILL -u <name>`, затем опрос `ps -A -o pid=,uid=,stat=` до пустого (зомби не в
    счёт) или таймаута. Возвращает оставшиеся PID; не отказывает. Эффекты:
    `process(df-build)`.
  - `run_as(user: str, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path,
    network: bool, timeout: float, stdout_limit: int = 1 << 20) -> RunResult`;
    `RunResult(returncode: int, stdout: bytes, stderr: bytes, launched: bool)`. Если сам
    механизм запуска (`sudo`, `setpriv`, `sandbox-exec`) не стартовал, `launched=False`:
    отличает сбой инфраструктуры от отказа проверяемой операции. Окружение строится
    **только** из `env` (`env -i`). Сеть запрещается так:
    - macOS: `sudo -n -u <user> /usr/bin/sandbox-exec -p NO_NETWORK_PROFILE …`;
    - Linux: `unshare --net -- setpriv --reuid=<user> --regid=<user> --init-groups -- …`.
  - `NO_NETWORK_PROFILE = "(version 1)(allow default)(deny network*)"`.
  - `LibDeps(needed: tuple[str, ...], rpaths: tuple[str, ...])`;
    `linked_libraries(path: Path) -> LibDeps | None`. Не Mach-O и не ELF — `None`. macOS —
    `otool -L` и `otool -l` (`LC_RPATH`), Linux — `readelf -d` (`NEEDED`,
    `RPATH`/`RUNPATH`). **Не `ldd`**: glibc-`ldd` может исполнить файл.
  - `SYSTEM_LIB_DIRS: Mapping[PlatformName, tuple[str, ...]]`. macOS: `/usr/lib/`,
    `/System/Library/`. Linux: `/lib/`, `/lib64/`, `/usr/lib/`, `/usr/lib64/`.
  - `trusted_path_problems(path: Path) -> list[str]` — §3.10. Для каждого компонента от `/`
    (по `lstat`):
    - владелец 0;
    - у не-ссылки нет `S_IWGRP|S_IWOTH`;
    - `acl_present_path` ложен; `OSError` — проблема «ACL не установлен».

    Компонент-ссылка разрешается по своему тексту, и цепочка цели проверяется так же; больше
    40 переходов — проблема.
  - `approved_cli_problems(cli: Mapping[str, Path], platform: PlatformName) -> list[str]`:
    - путь абсолютен;
    - `trusted_path_problems` пуст;
    - итоговая цель — обычный файл;
    - зависимости `linked_libraries` лежат в `SYSTEM_LIB_DIRS` или проходят
      `trusted_path_problems`;
      сужено при реализации (R26, §3.10): нативные зависимости допускаются только из системных
      каталогов; путей поиска (rpath/runpath, `@rpath`, `@loader_path`, `$ORIGIN`) у CLI быть не
      должно — установщик не моделирует загрузчик;
    - у скрипта с `#!` интерпретатор абсолютен и сам проходит те же проверки; `#!/usr/bin/env`
      — проблема.

    Сообщение отказа для пути вне политики называет поддерживаемый способ (§3.10):
    `sudo install -o root -m 0755 <src> /usr/local/libexec/darkfactory/bin/<name>`.

- [ ] **Step 1: Failing tests (unit)**

```python
def test_run_as_argv_macos_no_network(monkeypatch): ...  # argv начинается с sudo -n -u df-build /usr/bin/sandbox-exec -p
def test_run_as_argv_linux_no_network(monkeypatch): ...  # unshare --net -- setpriv --reuid=df-build
def test_run_as_env_is_exactly_given(): ...              # env -i + только ключи из env
def test_run_as_reports_launch_failure(monkeypatch): ...  # sudo отсутствует → launched=False
def test_cleaner_argv_removes_000_tree(tmp_path): ...
    # CLEANER_ARGV от текущего пользователя (без sudo): корень 000 и вложенные каталоги 000
    # с файлами → после трёх команд и rmdir корня дерева нет; двойник с argv r3 (без
    # отдельного chmod корня, `-exec … +`) дерево не удаляет
def test_spawn_as_new_session_no_stdin(monkeypatch): ...  # Popen: start_new_session=True, stdin=DEVNULL
def test_spawned_producer_end_kills_group_and_user(monkeypatch): ...
    # порядок: killpg → reap_user_processes(user) → wait; оставшийся PID → TreeError
def test_parse_otool_and_readelf_samples(): ...          # фикстурные выводы → LibDeps
@pytest.mark.parametrize("fixture,expected", [("macos-none", "none"), ("linux-none", "none"),
                                              ("linux-some", "some"), ("garbage", "unknown"),
                                              ("exit-1", "unknown")])
def test_sudo_rules_parsing(fixture, expected): ...
@pytest.mark.parametrize("case", ["uid0", "uid-shared", "admin-group", "wrong-primary-group",
                                  "login-shell", "home-0755", "home-foreign-owner",
                                  "sudo-unknown", "not-in-accounts", "state-creating"])
def test_verify_build_user_problems(monkeypatch, case): ...   # подменённые pwd/grp/stat → проблема названа
def test_account_problems_ignores_state(monkeypatch): ...      # creating + верные факты → []; verify_build_user → ["state"]
def test_create_absent_to_ready_without_stubbing_checks(monkeypatch, tmp_path): ...
    # подменены только команды ОС и pwd/grp; account_problems и verify_build_user настоящие:
    # нет → creating → ready, verify_build_user == []
def test_create_refuses_preexisting_foreign_user(monkeypatch, tmp_path): ...
@pytest.mark.parametrize("platform", ["macos-arm64", "linux-x86_64"])
@pytest.mark.parametrize("stop_after", ["intent", "attr:*", "home-mkdir", "home-chown",
                                        "home-rename", "home-parent-sync"])
def test_create_resumes_own_partial_creation(monkeypatch, tmp_path, platform, stop_after): ...
    # attr:* — после КАЖДОЙ команды ACCOUNT_ATTRS[platform] (в т. ч. после RecordName без
    # атрибутов, S1-R6-N2); повтор выполняет только недостающие команды
    # прерывание после каждой операции → повтор достраивает ту же учётку, запись ready;
    # home-mkdir — контрпример S1-R5-N3 (каталог root по временному имени); модель —
    # r18-local-check/account.py
@pytest.mark.parametrize("where", ["home-foreign-owner", "home-root-owned", "home-link",
                                   "reserved-name-before-intent", "reserved-name-not-empty"])
def test_create_never_adopts_foreign_home(monkeypatch, tmp_path, where): ...  # → StepFailed, объект не тронут
def test_rename_noreplace_refuses_existing_empty_dir(tmp_path): ...   # обычный rename заменил бы его
def test_create_refuses_mismatch_with_intent(monkeypatch, tmp_path): ...   # пользователь есть с другим UID
@pytest.mark.parametrize("case", ["record-before-intent", "partial-with-conflicting-gid",
                                  "partial-with-login-shell", "other-name-same-uid"])
def test_create_never_adopts_conflicting_record(monkeypatch, tmp_path, case): ...
    # → StepFailed с именем атрибута; ни одной команды dscl/useradd не выполнено
# test_home_found_by_retry_is_settled_before_ready — в Task 9 (нужен fake_world; R21,
# R33.F22)
def test_account_attrs_match_spec_order(): ...   # ACCOUNT_ATTRS = порядок §3.3 и модели
def test_tar_flags_exact_per_platform(): ...
def test_cleaner_argv_exact(): ...
```

```python selection-test
@pytest.mark.parametrize("case", [inputs for inputs, _ in CLEANUP_TREE_CASES])
def test_cleanup_tree_variant_events(monkeypatch, tmp_path, case):
    expected_variant = cleanup_tree_variant(*case)
    events, outcome = run_cleanup_tree_case(monkeypatch, tmp_path, case)
    assert events == list(leaf_variants("cleanup_tree")[expected_variant])
    assert outcome == CLEANUP_OUTCOME[expected_variant]
    # S1-R11-N1, S1-R12-N1, S1-R13-N1, S1-R14-N2: case = (obj, cleaner, rmdir, label) —
    # входы выбора; ожидание вычисляется из данных в самом тесте (строки выше);
    # подготовка строит их (obj: нет имени / ссылка или файл / каталог; cleaner: подменённый
    # run_as возвращает 0, код 1 или срок, причём первая команда действительно меняет режим
    # корня; rmdir: ENOTEMPTY — после трёх успешных команд в дереве появляется файл; label:
    # quarantine_label=id или None). run_cleanup_tree_case — фикстура: настоящий
    # cleanup_tree, подменены только run_as и fsutil-наблюдатель; CLEANUP_OUTCOME =
    # {"tree": "removed", "link": "removed", "absent": "removed", "quarantine":
    # "quarantined", "failed": "failed"}. События — порядок, кратность, цели
    # (sync_dir(quarantine) — по fd карантина, sync_dir(parent) — по pfd); при failed имя
    # в parent на месте, root-событий нет, изменённый режим допустим. Двойники — провал:
    # link без sync_dir(parent); quarantine без sync_dir(quarantine); quarantine с parent
    # раньше quarantine; sync_dir(parent) до rename (S1-R12-N2); ENOTEMPTY после успешного
    # очистителя как tree (S1-R13-N1)
```

```python
def test_cleanup_tree_root_ops_audited(monkeypatch, tmp_path): ...
    # sys.addaudithook: события open/os.listdir/os.scandir/os.remove/os.rmdir/os.rename
    # с путём внутри parent/name отсутствуют; есть только stat/rmdir/rename имени в pfd
def test_cleanup_tree_link_in_place_of_tree_unlinked(tmp_path): ...   # цель ссылки не тронута
def test_retry_quarantine_uses_cleaner_per_entry(monkeypatch, tmp_path): ...
def test_trusted_path_owner_write_acl(monkeypatch): ...   # подменённые lstat/acl: каталог df-build 0755 → проблема; g+w → проблема; ACL → проблема; ошибка ACL → проблема
def test_trusted_path_follows_intermediate_link(monkeypatch): ...  # /a -> /b (b у df-build) → проблема
def test_approved_cli_env_shebang_refused(tmp_path): ...
def test_reap_returns_leftovers_without_raising(monkeypatch): ...
def test_detect_platform_refuses_linux_arm(monkeypatch): ...
```

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): platform layer — build user identity, trusted paths, isolated run`

### Task 6: Сборка от `df-build`

**Files:**
- Create: `df/build.py`
- Test: `tests/df/test_build.py` (unit с подменой `run_as`), сквозной — Task 13

**Interfaces:**
- Consumes: `Profile`, `RepoPin` (Task 2); `run_as`, `RunResult`, `verify_build_user`,
  `whoami_as` (Task 5); `Layout` (Task 1).
- Produces:
  - `BuildPlan(release_id: str, release_dir: Path, profile: Profile, uv: Path, git: Path,
    tar: Path)`.
  - `build_env(plan: BuildPlan, layout: Layout) -> dict[str, str]`:
    - `HOME = layout.build_home`, `PATH` = каталоги `uv`/`git`/`tar` + `/usr/bin:/bin`,
      `LANG = C.UTF-8`;
    - `UV_CACHE_DIR = build_home/cache`, `UV_PYTHON_INSTALL_DIR = release_dir/python`,
      `UV_LINK_MODE = copy`, `UV_NO_CONFIG = 1`, `UV_PYTHON_PREFERENCE = only-managed`;
    - `GIT_CONFIG_NOSYSTEM = 1`, `GIT_CONFIG_GLOBAL = /dev/null`, `GIT_TERMINAL_PROMPT = 0`.
  - `build_commands(plan: BuildPlan, layout: Layout) -> list[tuple[str, list[str]]]` —
    `(метка, argv)` в порядке исполнения (чистая функция; тест сверяет её целиком).
  - `run_build(plan: BuildPlan, layout: Layout, *, runner=run_as) -> None`. Предусловие
    (§3.2): `verify_build_user` пуст и `whoami_as("df-build")` равен записи — иначе
    `StepFailed` до первой команды. Команды идут по одной от `df-build`, сеть разрешена,
    таймаут 1800 с на команду. Ненулевой код — `StepFailed`
    с меткой и последними 4 КиБ stderr. Эффекты: `run_as(df-build)+` (`releases/<id>`,
    `build_home/tmp`, `build_home/cache`).
  - `python_path(plan: BuildPlan) -> Path` =
    `release_dir/python/cpython-<python>-<triple>/bin/python<X.Y>`. Тройка платформы —
    `macos-aarch64-none` / `linux-x86_64-gnu`.

Порядок команд. Ниже `W = build_home/tmp/<release_id>`.

1. Для каждого репо:
   - `git init -q W/git/<r>`;
   - `git -C … fetch -q --depth=1 <url> <sha>`;
   - `git -C … rev-parse FETCH_HEAD`: stdout обязан быть равен `sha`, иначе `StepFailed`;
   - `git -C … archive -o W/<r>.tar <sha>`;
   - `tar -xf W/<r>.tar -C release_dir/src/<r>` — поставляемая копия;
   - `tar -xf W/<r>.tar -C W/src/<r>` — копия для сборки, чтобы артефакты build-backend не
     попадали в поставляемое дерево.
2. `uv python install <python>`.
3. Для каждого репо: `uv sync --locked --no-editable --no-default-groups [--group g]...
   --project W/src/<r> --python <python_path>` с
   `UV_PROJECT_ENVIRONMENT = release_dir/venv/<r>` (переменная — на эту команду).
4. `uv --version` → stdout записывается как заявленная версия (`uv_version_declared`).

- [ ] **Step 1: Failing tests**

```python
def test_commands_exact_order_and_flags(): ...   # снимок списка для профиля из 2 репо
def test_sync_has_locked_no_editable_explicit_groups():
    cmds = dict(build_commands(PLAN, LAYOUT))
    sync = cmds["sync:devtools"]
    assert {"--locked", "--no-editable", "--no-default-groups"} <= set(sync)
    assert sync[sync.index("--group") + 1] == "governance"
def test_fetch_head_mismatch_fails(): ...         # runner-заглушка отдаёт иной SHA
def test_stale_lock_failure_names_repo(): ...     # Review Focus 3: код 2 у sync:steward → StepFailed("sync:steward", …stderr)
def test_build_env_has_no_inherited_vars(monkeypatch): ...  # GH_TOKEN в os.environ не попадает
def test_build_refused_before_any_command_if_user_unverified(): ...  # runner не вызван
```

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): build release as df-build at final path`

### Task 7: Нормативный автомат приёма и доверенный чекаут

**Files:**
- Create: `df/accept.py`, `df/install_table.py`, `df/inputs.py`, `df/sources.py`,
  `df/routes.py`, `df/leaves.py` (R35); `tests/df/conftest.py` (`fake_layout`, R33.F19)
- Modify (как построено): `df/fsutil.py` (`owner_uid`, `sync_notes`), `df/paths.py`
  (`local_bin`, `mark_file`), `df/config.py` (`read_optional`), `df/digest.py`
  (`open_regular`, ошибки чтения), `df/build.py` (argv `git` и `GIT_ISOLATION` — общие с
  `fetch_reference`), `df/receive.py`, `df/platform.py`, `df/journal.py` (шов владельца)
- Test: `tests/df/test_accept.py`, `tests/df/test_install_table.py`, `tests/df/test_drive.py`,
  `tests/df/test_inputs.py`, `tests/df/test_sources.py`, `tests/df/test_routes.py`,
  `tests/df/test_leaves.py`, `tests/df/test_scenarios.py`, `tests/df/test_platform.py`
  (тест выбора, R20)

Размещение (как построено, R35, R33.F18): `df/accept.py` — отметки, факты, `settle`,
`drive`; `df/routes.py` — условия, интерпретатор, `route_action`, `TARGETS`,
`acceptance_calls`; `df/leaves.py` — листья-протоколы `prepare_dir`, `verify_chmod`,
`seal`, `accept_copy` (`release_digest` — в `df/digest.py`, один вызов у отметок и
проверок); `df/sources.py` — `SourceId`, `UnsupportedLock`
и правила источника; `df/inputs.py` — `fetch_reference`, `verify_sources`, `InstalledDist`,
`installed_distributions`, `pth_problems`.

**Interfaces:**
- Consumes: `tree_digest`, `receive_tree`, `remove_own_tree`, `TreeError` (Task 3);
  `journal_event`, `StepFailed` (Task 4); `reap_user_processes`, `spawn_as`, `run_as`,
  `cleanup_tree`, `TAR_FLAGS` (Task 5); `RepoPin`, `Profile` (Task 2); `write_atomic`, `remove_durably`,
  `sync_dir`, `boot_id`, `Account`, `load_pending`, `load_releases`, `InstallConfig` (Task 1).
  Как построено также: `check_links`, `remove_empty_dir` (Task 3); `cleanup_build_home`,
  `trusted_path_problems` (Task 5); `run_build`, `BuildPlan`, `python_key` (Task 6);
  `load_accounts` (Task 1).
- Produces:
  - `Stage = Literal["built", "copied", "verified", "admin"]`;
    `STAGE_ORDER = ("built", "copied", "verified", "admin")`.
  - `AcceptMark(install_digest: str, stage: Stage, built_boot_id: str, tree_digest: str |
    None, admin_digest: str | None, admin_python: str | None, uv_version_declared: str)`
    — `admin_digest` и
    `admin_python` (точная версия `X.Y.Z` из `Profile.python` прогона) пишутся вместе
    отметкой `admin` (rev 8, r11); TOML `layout.marks_dir/<id>.toml` (`df/accept/v1`),
    root `0600`, только `write_atomic`. `uv_version_declared` (R29, R33.F11) — версия `uv`,
    которую вернул `run_build`: её пишет отметка `built`, поздние стадии переносят её и
    `built_boot_id` из записанной отметки без изменения (`write_mark` сверяет); Task 9
    берёт её для `ReleaseRecord` из отметки `admin`. Форматы: `install_digest` — 64 hex
    (как в `Pending`), `tree_digest` и `admin_digest` — `sha256:<hex>`.
  - `write_mark(layout, id, mark: AcceptMark) -> None` — обёртка протокола `write_atomic`
    (`WRAPS`): отказывает (`StepFailed("mark")`), если новая `stage` ниже записанной
    (записанная — `load_mark(…, install_digest=<новой>)`: чужая конфигурация и `built`
    прошлой загрузки не мешают, R33.F32) или нет `marks_dir` (R33.F31). Эффекты:
    `write_atomic()`.
  - `drop_mark(layout, id) -> None` — обёртка протокола `remove_durably` (только маршрут
    `cleanup`). Эффекты: `remove_durably()`.
  - `load_mark(layout, id, *, install_digest: str) -> AcceptMark | None`: отметка
    другой конфигурации и `built`-отметка из прошлой загрузки (`built_boot_id != boot_id()`)
    — `None`; битая, не обычный файл или ссылка — тоже `None` (R33.F32, читатель —
    `read_optional`; сам файл убирает шаг `m=present` маршрута `cleanup`).
  - `Facts` (frozen) — ровно факты таблицы §3.3, имена ключей таблицы:
    - `P: bool`; `reg: bool`; `cur: bool`; `L: bool`;
    - `intact: bool` — только при `reg`: корень у root, `0755`, `tree_digest` = `digest`
      записи и `admin_ready(layout, id, expected_admin_digest=<admin_digest записи>,
      expected_python=<admin_python записи>)` (авторитетна запись реестра);
    - `m: Literal["none", "built", "copied", "verified", "admin"]` — стадия `load_mark`, иначе
      `"none"`; сама отметка — `mark: AcceptMark | None` (не ключ таблицы);
    - `r: Literal["absent", "b-empty", "b-full", "root-700", "root-755", "root-x"]` —
      владелец и режим корня — `os.stat(..., follow_symlinks=False)`; пустоту дерева
      `df-build` определяет **он сам** (§3.3, rev 8 r5): `builder_tree_nonempty(layout,
      path) -> bool` = `run_as("df-build", ["/usr/bin/find", str(path), "-mindepth", "1",
      "-maxdepth", "1", "-print", "-quit"], env={"PATH": "/usr/bin:/bin", "LANG": "C"},
      cwd=layout.build_home, network=False, timeout=10.0)`; непустой вывод, код ≠ 0,
      `launched=False` или срок → `True` (`b-full`). Root каталог сборщика не открывает.
      Владелец — через `fsutil.owner_uid`; не каталог или владелец ни root, ни
      `build_uid` — вне домена: `StepFailed("facts")`, решает человек (R33.F20);
    - `full: bool` — `r` у root и `tree_digest` на диске = `mark.tree_digest` (только при
      `m ≥ copied`);
    - `A: bool` — `admin_ready(layout, id, expected_admin_digest=mark.admin_digest,
      expected_python=mark.admin_python)` (только при `m = admin`). Готовность
      административной среды — один предикат для `A` и `intact` (Task 8a): код с нужным
      digest и Python точной версии; различается только авторитет обоих значений —
      отметка до регистрации, запись после (rev 8, r10, r11);
    - `S: bool` — есть `staging/<id>/tree`.
  - `settle(layout, release_id) -> None` — п. 1 цикла §3.3 (rev 8, r5): `sync_dir` для
    каждого пути `settle_paths(layout, release_id)`; отсутствующий пропускается. Содержимое
    деревьев не трогает. `settle_paths` отображает имена `settle_dirs()` из
    `install_table.py` на пути `Layout` словарём `SETTLE_PATHS` с ключами ровно `DIRS`
    (rev 8, r10); первым `quarantine` → `quarantine` (`SETTLE_FIRST`, rev 8 r12: процесс,
    прерванный между `rename` в карантин и его `sync_dir`, оставляет оба каталога
    незакреплёнными; закрепи `settle` родителя раньше карантина — сбой ОС потерял бы
    дерево в обоих), затем `install` → `install_dir`, `accept` → `marks_dir`, `etc_df` → `etc`,
    `opt_df` → `opt`, `releases` → `releases`, `release_root` → корень `releases/<id>`
    (только если он у root), `staging` → `staging` и `staging_dir(id)`, `admin` → `admin`,
    `admin_python` → `admin_python`, `opt_bin` → `bin_dir`, `local_bin` → `layout.local_bin`
    (только при `local_bin_trusted(layout)` — то же определение, что у условия
    `local_bin=trusted`, R33.F23), `home_parent` → `build_home.parent`. Ручного списка
    каталогов в `accept.py` нет.
  - `acquire_paths(layout, pending_id: str | None) -> tuple[Path, ...]` — отображение
    `acquire_dirs()` теми же правилами, `layout.quarantine` первым (как в `settle_paths`);
    при `pending_id is None` id-зависимые пути (корень
    `releases/<id>`, `staging_dir(id)`) не входят.
  - `settle_on_acquire(layout) -> None` — §3.3 «Закрепление при захвате блокировки» (rev 8,
    r6). Вызывающий держит `writer_lock`. Порядок: `sync_dir(install_dir)` → id из
    `pending.toml` (есть) → `sync_dir` каждого пути `acquire_paths(layout, id)`;
    отсутствующий пропускается. Вызывают: вход в режиме `writer` (своя stdlib-копия, Task
    8a) и `main` в режиме `bootstrap` (Task 9) — до снимка, `recover_pending` и
    `create_build_user`. Больше никто; `drive` закрепляет своё через `settle`. Битый
    `pending.toml` или id — только id-независимые пути, без отказа (R33.F33); `install_dir`
    закрепляется один раз.
  - `read_facts(layout, release_id, *, install_digest: str, build_uid: int,
    launcher_ready: Callable[[], bool], admin_ready: AdminReady) -> Facts`. Оба предиката
    Task 8a — параметры (R33.F5): `AdminReady` — протокол с её сигнатурой `(layout, id, *,
    expected_admin_digest, expected_python) -> bool`; Task 9 связывает `df.admin`, тесты
    Task 7 — заглушкой. `cur` — `readlink(current) == "releases/<id>"`; `current` не
    ссылка, битый `releases.toml` или `OSError` при digest дерева — `StepFailed("facts")`
    (без очистки); `TreeError` digest — «не равно». Запись реестра — `registry_entry(layout,
    id)` (тот же читатель у условия `not reg` и у проверки `cleanup`). Версию
    административного Python
    `read_facts` берёт там же, где digest: `mark.admin_python` для `A`, `admin_python`
    записи реестра для `intact` (rev 8, r11) — ни профиль, ни манифест не читаются.
  - В `df/install_table.py` — **данные таблицы §3.3, единственный источник правил** (rev 8,
    r4): `DOMAINS: Mapping[str, tuple[object, ...]]`; `Cube = Mapping[str, frozenset[object]]`
    (отсутствующий ключ — любое значение); `Row(phase: str, cubes: tuple[Cube, ...], action:
    str, post: tuple[str, ...])` (`post` — в порядке таблицы: рендер байт в байт, R33.F2);
    `ROWS: tuple[Row, ...]`; `UNKNOWN_ROW = Row("unknown", (), "cleanup", ("none",))` и
    `row_of(phase) -> Row` (R33.F9). Значения переносятся при реализации
    из `r18-local-check/table.py` (артефакт разработки; рантайм на него не ссылается). У
    `damaged` действие `"stop"`, у `committed` — `""`. Там же — **источники
    долговечности** (rev 8, r5): `DIRS: Mapping[str, str]` (имя → текст для спеки),
    `FACT_SOURCES: Mapping[str, tuple[str, ...]]` (ключ — каждый ключ `DOMAINS`),
    `settle_dirs() -> tuple[str, ...]` — объединение в порядке `DIRS`; **протоколы
    примитивов** (rev 8, r7): `PROTOCOLS: Mapping[str, tuple[tuple[str, str, str], ...]]` —
    шаги `(op, arg, text)`, `op ∈ {"change", "sync_dir", "sync_tree", "process", "builder"}` —
    ровно ключи `OPS` (`builder` — запуск очистителя от `df-build`, rev 8 r12); `SYNC_OPS = ("sync_dir", "sync_tree")` — единственный выбор
    синхронизаций (rev 8, r11: не «всё, кроме `change`»); `arg` для `sync_dir` — имя `DIRS`,
    `NON_FACT_DIRS` (`quarantine`) или `"parent"`; у примитива с разными исходами —
    варианты `{вариант: шаги}` (`cleanup_tree`, `seal`, `cleanup_build_home`) с условием
    выбора каждого `VARIANT_WHEN: Mapping[str, Mapping[str, str]]`; модель исполняет каждый
    вариант, кроме `NOT_MODELED: Mapping[tuple[str, str], str]` (вариант → причина), и
    проверяет, что исполнила их все (rev 8, r12); `SETTLE_FIRST = ("quarantine",)`;
    `render_protocols() -> str` — таблица §3.3
    «Протоколы примитивов» между `<!-- install-protocols:begin -->` и `<!--
    install-protocols:end -->`; **маршруты** (rev 8, r8): `ROUTES: Mapping[str,
    tuple[tuple[str | None, str, str], ...]]` — шаги `(guard, call, target)`,
    `GUARD_NAMES: frozenset[str]`, `REQUIRES: Mapping[str, str]`, `render_routes() -> str`
    — §3.3 «Маршруты» между `<!-- install-routes:begin -->` и `<!-- install-routes:end -->`;
    **листья** (rev 8, r9): `CALL_KINDS: Mapping[str, str]`, `CALLS: Mapping[str, tuple[str,
    str]]` (лист без протокола → вид, эффекты), `WRAPS: Mapping[str, str]` (обёртка → её
    протокол), `ABSENT_OK: Mapping[str, str]`, `GUARD_SOURCES: Mapping[str, tuple[str,
    ...]]`, `route_leaves() -> list[str]`, `check_data() -> None` (при импорте: у каждого
    листа ровно одно из протокола и вида; источники каждого условия ⊆ `settle_dirs()`;
    как построено (7-A): у каждого факта готовности `ADMIN_READY_FACTS = ("A", "intact")`
    среди источников все `ADMIN_READY_DIRS = ("admin", "admin_python")`, `CLEANUP_OUTCOME`
    — исход `cleanup_tree` по варианту; ключ `fact_sources` — для двойников);
    **эффекты** (rev 8, r10, r11): `leaf_variants(name: str) -> dict[str | None,
    tuple[str, ...]]` — ожидаемые последовательности событий листа по вариантам (`None` —
    единственный у листа без вариантов), `leaf_effects(name)` — она же для листа без
    вариантов, `sync_steps() -> list[tuple[str, str | None, int, str]]` — шаги `SYNC_OPS`
    (протокол, вариант, индекс, аргумент) всех вариантов, которые исполняет модель;
    `SYNC_PARENTS: Mapping[str, tuple[str, ...]]` — родители, с которыми вызывается
    протокол (модель сверяет с вызовами), `sync_twin_cases() -> list[tuple]` — случаи
    `(протокол, вариант, шаг[, родитель])`, `SYNC_NEEDED: frozenset[tuple]` — случаи с
    контрпримером (rev 8, r13); `cleanup_tree_variant(obj, cleaner, rmdir, label) -> str`
    и `CLEANUP_TREE_CASES` — выбор варианта очистки как функция и все её входы (двойники `twins.py` и `test_crash_matrix_protocol_twins`); каждая
    последовательность (шаги протокола: изменение — его вид,
    `sync_dir(<имя DIRS | parent>)`, `sync_tree(<объект>)`, `process(df-build)`; вид
    `builder` — `run_as(df-build)+`, `process` — `process(df-build)`, `check` — пусто),
    `unfold_route(name) -> list[tuple[tuple[str, ...], str, str]]` — полная развёртка
    маршрута (вложенные маршруты подставлены, у шага — все его условия),
    `render_route_calls() -> str` — таблица Task 9.
    Значения — из `r18-local-check/table.py`; `ACCOUNT_SOURCES:
    Mapping[str, tuple[str, ...]]` (`account-intent` → `install`, `account-home` →
    `home_parent`, rev 8 r6) и `acquire_dirs() -> tuple[str, ...]` — объединение
    `FACT_SOURCES` и `ACCOUNT_SOURCES` в порядке `DIRS`; `render_settle() -> str` — таблица
    §3.3 п. 1 (столбец «Когда») между `<!-- install-settle:begin -->` и `<!--
    install-settle:end -->`.
  - `overlaps(rows) -> list[tuple[str, str]]` — пары строк с пересекающимися кубами (точно,
    по множествам значений); `render_table() -> str` — таблица §3.3 в Markdown, байт в байт как
    в спеке между `<!-- install-table:begin -->` и `<!-- install-table:end -->`.
  - `classify(f: Mapping[str, object]) -> str` (как построено: в `install_table`;
    `accept.classify(facts: Facts)` передаёт `Facts.table()` — ровно ключи `DOMAINS`) —
    фаза единственной строки, у которой хоть один куб содержит
    точку `f`; ни одной — `"unknown"`; больше одной — `AssertionError` (ошибка программы,
    ловится тестом `test_rows_disjoint_by_cubes`). Порядка строк и «наибольшей подходящей»
    нет.
  - `drive(layout, release_id, *, actions: Mapping[str, Callable[[Facts], None]], read:
    Callable[[], Facts], journal: Path, run_id: str, max_iter: int = 40) -> None` — цикл
    §3.3: `settle` → `read()` → `classify` → `committed` — возврат; `damaged` —
    `StepFailed("damaged", …)`; иначе `journal_event(start)` → `actions[row.action](facts)` →
    `settle` → новая фаза ∈ `row.post`, иначе `StepFailed(row.action, "<было> → <стало>")` →
    `journal_event(done)`. `max_iter` исчерпан — `StepFailed("drive", "нет продвижения")`.
    `drive` — единственный исполнитель действий приёма. Как построено: `unknown` —
    строка `UNKNOWN_ROW` (действие `cleanup`, R33.F9); любой отказ (действие, `settle`,
    чтение фактов, фаза вне `post`, `damaged`, нет продвижения) пишется
    `journal_event(failed)` и поднимается дальше (R33.F29); `max_iter < 0` — `ValueError`;
    не больше `max_iter` действий, затем последняя классификация; при выходе (успех или
    отказ) — одна строка `sync-note` со `st_dev` из `fsutil.sync_notes()`, если набор не
    пуст (R33.F10).
  - **Действия — исполнители маршрутов** (rev 8, r8, §3.3 «Маршруты»). `run_route(layout,
    id, name: str, *, calls: Mapping[str, Callable[[str], None]], python_key: str | None =
    None, build_uid: int | None = None) -> None` (`df/routes.py`, R35) — единственный
    интерпретатор `ROUTES[name]`: для каждого шага `(guard, call, target)` — если `guard` и
    `route_guard(layout, id, guard)` ложно, шаг пропускается; иначе
    `calls[implementation(call, target)](target)` (обёртка `WRAPS` — по цели из `TARGETS`,
    R33.F7), а вложенный маршрут — рекурсивно. Маршрут `cleanup` перед первым шагом читает
    реестр (`ENTRY_CHECKS`): id записан — `StepFailed("cleanup")` (R33.F13).
    `route_guard(layout, id, guard: str, *, python_key: str | None = None, build_uid: int |
    None = None) -> bool` — `lstat` имени через дескриптор родителя, принадлежащего root
    (`releases/`, `staging/`,
    `staging/<id>`, `admin/`, `admin-python/`, `accept/`; `not reg` — запись реестра под
    блокировкой; `local_bin=trusted` — `local_bin_trusted(layout)`, R33.F23); объект не
    открывается. `r=builder` — каталог с владельцем `build_uid`, иной владелец —
    `StepFailed("route")`; `py=absent` — `admin-python/<python_key>` (`python_key` —
    `build.python_key(profile.python, platform)`, R33.F6; без ключа — ошибка программы);
    нет родителя — нет и имени; родитель — ссылка, не каталог или не root —
    `StepFailed("route")`. **Реализации вызовов — листья** (rev 8, r9, §3.3 «Листья
    маршрутов»): ни одна не вызывает другую реализацию из `calls` и не делает изменений вне шагов своего
    протокола; вид `check` — ни одного события `fsutil` и ни одного `run_as`; `builder` —
    только `run_as("df-build", …)` над объектами из `CALLS`; отсутствующий объект — успех
    только для `ABSENT_OK`. **Эффекты каждой реализации** (rev 8, r10) — строка «Эффекты:»
    её договора; транзитивная развёртка через вспомогательные функции (`receive_tree`,
    `SpawnedProducer.end`) равна `leaf_effects(лист)` — порядок, кратность и объекты; у
    примитива с вариантами — по строке «Эффекты (`вариант`):» на каждый вариант, равной
    `leaf_variants(лист)[вариант]` (rev 8, r11); синхронизация в прозе договора — всегда
    вызов, исключений для неё нет; у каждой синхронизации один владелец (сверяет
    `r18-local-check/check_plan.py`). Ни одно действие не содержит своей цепочки вызовов: маршрут `build-dir`,
    маршрут `build`, маршрут `seal`, маршрут `copy`, маршрут `copy-partial`, маршрут
    `verify`, маршрут `admin`, маршрут `register`, маршрут `activate`, маршрут `launcher`,
    маршрут `commit-config`, маршрут `cleanup` (и вложенный маршрут `cleanup_staging`) —
    строки таблицы; откат — маршрут `rollback` (Task 9). Каждое действие первым делом
    проверяет, что `classify` свежих фактов — его строка, иначе `StepFailed(<действие>, "не
    в своей фазе")`: `route_action(layout, id, action, *, calls, python_key, build_uid) ->
    Callable[[Facts], None]` (R33.F14) — проверка, затем `run_route`; Task 9 отображает в
    него каждое `Row.action`. Реализации вызовов (`calls`):
    - `prepare_dir(layout, id)` — протокол `prepare_dir` (rev 8, r8): `os.mkdir(id, 0o700,
      dir_fd=<releases>)`, `fd = os.open(id, O_RDONLY|O_DIRECTORY|O_NOFOLLOW,
      dir_fd=<releases>)` — каталог ещё root и пуст; `fault_point("created")`;
      `os.fchown(fd, uid, gid)` — передача, `fault_point("chown")`; `sync_dir(fd)`,
      `os.close(fd)`; `sync_dir(<releases>)`. После `fchown` путь `releases/<id>` не
      открывается (§3.8). Как построено: `uid`/`gid` — из записи `df-build` в
      `accounts.toml` (`load_accounts`, состояние `ready`, иначе `StepFailed("build-dir")`
      до изменения); режим `0700` ставит `os.fchmod(fd, 0o700)` до `fault_point` (не
      зависит от umask, не событие); нет `releases/` — `StepFailed`, каталог не создаётся
      (R33.F31). Эффекты: `created` → `chown` → `sync_dir(release_root)` →
      `sync_dir(releases)`;
    - `run_build` (Task 6, `runner=plat.run_as`) и `write_mark` по цели шага (`TARGETS`):
      `built` — `boot_id()` и версия `uv` от `run_build`; `copied` — digest копии;
      `max(стадия, verified)` высшую отметку оставляет как есть (`admin` с прежними
      `admin_digest`/`admin_python`); `admin` — `tree_digest(admin_dir(id),
      exclude=frozenset())` (R33.F25) и `Profile.python`. Значения предыдущего шага
      маршрута идут через `RouteContext` и берутся один раз;
    - `verify_chmod(layout, id) -> None` — протокол `verify_chmod`: `fstatat` имени через
      дескриптор `releases/` — каталог root, не ссылка; `open(O_RDONLY|O_DIRECTORY|
      O_NOFOLLOW, dir_fd)` и те же `st_dev`/`st_ino`; `os.fchmod(fd, 0o755)`,
      `fault_point("chmod")`, `sync_dir` корня. Как построено: `os.chmod(…, dir_fd=…,
      follow_symlinks=False)` на Linux до Python 3.13 не поддержан, а корень всё равно
      открывается для синхронизации; ссылка не разыменовывается, открывается только
      каталог root. Эффекты: `chmod` → `sync_dir(release_root)`;
    - `seal(layout, id)` — протокол `seal` с вариантами (rev 8, r12): вариант `new` — нет
      `staging/<id>`: `mkdir staging/<id>` root `0711`, `fault_point("created")`; вариант
      `reuse` — есть пустой `staging/<id>` (повтор после сбоя за `mkdir`): `mkdir` не
      вызывается. Затем в обоих `os.rename(releases/<id>, staging/<id>/tree)`; `sync_dir`
      для `releases/`, `staging/`, `staging/<id>/`. Процессы `df-build` завершает предыдущий
      шаг маршрута `seal`. Последний `sync_dir(staging)` — оба каталога `staging/` и
      `staging/<id>/`. Как построено: оба варианта ставят контейнеру `0711` (`fchmod`, не
      событие: сбой между `mkdir` и `fchmod` под umask 077 не оставит `0700`); `reuse`
      требует пустой контейнер root (иначе `StepFailed("seal")`, контейнер не меняется);
      контейнер открывается после `fstatat` с `O_NOFOLLOW` и теми же `st_dev`/`st_ino`,
      `releases/<id>` — только `fstatat` и `renameat`.
      Эффекты (`new`): `created` → `renamed` → `sync_dir(releases)` → `sync_dir(staging)`.
      Эффекты (`reuse`): `renamed` → `sync_dir(releases)` → `sync_dir(staging)`.
    - `accept_copy(layout, id, *, install: InstallConfig, platform: PlatformName,
      limits: CopyLimits = CopyLimits(), _hook=_noop) -> str` (R33.F21):
      - `producer = spawn_as("df-build", [str(install.approved_cli["tar"]),
        *TAR_FLAGS[platform], "-cf", "-", "-C", str(layout.staging_tree(id)), "."],
        env={"PATH": "/usr/bin:/bin", "LANG": "C"}, cwd=layout.build_home, network=False,
        reap_seconds=limits.reap_seconds)`;
      - `receive_tree(producer, layout.releases, id, limits=limits, _hook=_hook)` —
        производитель,
        частичная копия и обе синхронизации протокола `accept_copy` — забота `receive_tree`
        (Task 3, п. 5); своих синхронизаций вызывающий не делает (rev 8, r10);
      - отказ потока (`TreeError`) — `StepFailed("copy", "<ошибка>; stderr: <последние 4096
        байт stderr tar>")`; частичная копия остаётся до маршрута `copy-partial`;
      - `release_digest` (`df/digest.py`) = `tree_digest(…, exclude={"release.json"})` —
        тот же вызов у проверки `tree_digest` маршрута `verify` и у `read_facts` (отметки
        `copied` и записи реестра).
        Возвращает digest. Вместе — протокол
        `accept_copy`. Эффекты: `receive_tree()` → `tree_digest()`;
    - `remove_own_tree`, `cleanup_tree` (с `quarantine_label=id`), `remove_empty_dir`,
      `drop_mark` — для шагов маршрутов `cleanup` и `cleanup_staging`. `cleanup` при записи
      id в `releases.toml` — `StepFailed` до первого шага (никогда не удаляет
      зарегистрированное). Как построено (R33.F13): проверяют и вход маршрута `cleanup`,
      и обёртка `remove_own_tree` для `releases/<id>` и `admin/<id>` — откат вне `drive`
      тоже не трогает записанное; исход `cleanup_tree` `"failed"` — `StepFailed("cleanup")`.
      Оставшиеся процессы шага `reap_user_processes` маршрута `seal` — предупреждение в
      журнале, приём продолжается (R33.F28). `TreeError` обёрток `remove_own_tree` и
      `remove_empty_dir` идёт без имени шага (вне `drive` имя даёт Task 9).
  - `acceptance_calls(layout, id, *, install, profile, platform, plan, limits, refs,
    journal, run_id, python_key) -> dict[str, Call]` (`df/routes.py`, R33.F7) —
    реализации вызовов Task 7 (листья и обёртки `write_mark`, `drop_mark`) над общим
    `RouteContext` (версия `uv` от `run_build`, digest копии от `accept_copy`).
    `TARGETS: Mapping[str, Target]` — каждый текст цели `ROUTES` → объект и обёртка
    (`implementation(call, target)`; неизвестный текст — `KeyError`, сверка при импорте).
    `LATER_LEAVES: Mapping[str, str]` — лист → задача-владелец: `distribution_problems` —
    7a (как построено: перешёл в `acceptance_calls`, R36/F2); `static_problems`,
    `transfer_check` — 8 (как построено: перешли в `acceptance_calls`, R40/F6);
    `install_admin_python`, `install_admin`,
    `link_command` — 8a; `activate`, `write_release_json`, `register` и `write_atomic`
    без обёртки — 9. Ключи `acceptance_calls` и `LATER_LEAVES` вместе — ровно
    `route_leaves()` и `WRAPS`. `python_key` — тот же, что вызывающий передаёт
    `run_route`/`route_action`.
  - `fetch_reference(pin: RepoPin, dest: Path, *, git: Path, layout: Layout) -> Path` —
    доверенный чекаут root; `dest` — прямой потомок `layout.verify_dir`, который уже есть
    настоящим каталогом (не создаётся, R33.F31); прежний `dest` (повтор) удаляется
    (R33.F34):
    - `git init --template=` / `fetch --depth=1` / `rev-parse FETCH_HEAD` (= `pin.sha`) /
      `archive -o` — argv сборки (`git_fetch_argv`, `git_rev_parse_argv`,
      `git_archive_argv`, Task 6) с её `GIT_ISOLATION` (`GIT_CONFIG_NOSYSTEM=1`,
      `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_ATTR_NOSYSTEM=1`) и
      свежими пустыми `HOME`, `XDG_CONFIG_HOME` (R33.F12);
    - распаковка — `tarfile` с `filter="data"` (это архив, созданный root из своего
      чекаута, а не поток сборщика).

    Код из чекаута не исполняется. Чекаут — единственный источник для сверки исходников, для
    lock-файлов (Task 7a) и для административного кода (Task 8a). Чекауты получает Task 9
    до `drive`; лист `verify_sources` только сравнивает (R33.F34).
  - `verify_sources(release_dir: Path, profile: Profile, refs: Mapping[str, Path]) ->
    dict[str, str]` — `tree_digest(release_dir/src/<r>) == tree_digest(refs[r])`, обе
    стороны с `exclude=frozenset()` (R33.F25); расхождение — `StepFailed("verify-sources")`
    с именем репо; возвращает `{repo: "sha256:<hex>" от refs[r]/uv.lock}` (формат
    `tree_digest`). Эффекты: нет.
  - В `sources.py` — канонический вид источника §3.5, по видам; там же
    `class UnsupportedLock(Exception)` (R33.F18):
    - `SourceId(kind: Literal["registry", "git", "directory"], url: str | None,
      subdirectory: str | None, commit: str | None, path: str | None, editable: bool |
      None)`;
    - `canonical_repo_url(url: str) -> str` — только `https`; хост в нижнем регистре; без
      запроса, фрагмента, завершающего `/` и `.git`. Иное — `TreeError`;
    - `source_from_lock(source: Mapping[str, str], *, root_dir: Path) -> SourceId`:
      - `git`: фрагмент — commit (40 hex, иначе отказ); запрос разбирается
        `urllib.parse.parse_qsl(strict_parsing=True)`; `subdirectory` — percent-декодирован;
        `rev`/`tag`/`branch` отбрасываются; иной ключ или повтор ключа — `UnsupportedLock`;
      - `editable == "."` или `directory == "."` → `SourceId("directory", path=str(root_dir),
        editable=False)`; иное `editable`/`directory`/`path` — `UnsupportedLock`;
      - `registry` → `SourceId("registry", url=<индекс>)`;
    - `source_from_direct_url(data: Mapping[str, object] | None) -> SourceId`:
      - нет файла → `registry` с `url=None`;
      - `vcs_info.vcs == "git"` → `url` через `canonical_repo_url`, поле `subdirectory` (нет —
        `None`), `commit_id`;
      - `dir_info` → `url` обязан быть `file:` без хоста или с `localhost`; путь —
        `urllib.parse.unquote`, без `.`/`..`/`//`; `editable = dir_info.get("editable",
        False)`;
      - иное, `archive_info` или нехватка поля — `TreeError`.
  - `InstalledDist(name: str, version: str, source: SourceId)`;
    `installed_distributions(venv: Path) -> list[InstalledDist]` — `METADATA` (`Name`,
    `Version`) и `direct_url.json` каждого `*.dist-info` итоговой копии через
    `source_from_direct_url`. Только чтение данных. Как построено (`df/inputs.py`): `Name`
    нормализуется по PEP 503 (7a сравнивает нормализованные имена с обеих сторон);
    обходятся только настоящие каталоги (`lstat`), единственная допустимая ссылка —
    `lib64 -> lib` при настоящем `lib`; имена сравниваются через `casefold`, имя, совпавшее
    лишь после него, или два совпавших — `TreeError`; файлы читаются `open_regular` с
    пределом размера; `*.egg-info`, `*.egg`, `*.egg-link` — `TreeError` (uv их не
    создаёт); нет окружения или `site-packages` — `TreeError`.
  - `pth_problems(venv: Path) -> list[str]` — `.pth` в `site-packages`, кроме
    `_virtualenv.pth` с содержимым ровно `import _virtualenv`, — проблема. Обход тот же,
    что у `installed_distributions`; недоверенный обход — `TreeError`, а не «проблем нет».

- [ ] **Step 1: Failing tests**

```python
def test_rows_disjoint_by_cubes():
    assert overlaps(ROWS) == []
def test_every_point_at_most_one_row_and_unknown_is_pre_registration():
    for f in product_of(DOMAINS):              # 7680 точек
        hits = [r for r in ROWS if any(matches(c, f) for c in r.cubes)]
        assert len(hits) <= 1
        if not hits:
            assert f["P"] and not f["reg"] and not f["cur"]
def test_spec_table_is_rendering_of_data():
    spec = Path("docs/superpowers/specs/2026-10-13-df-installer-design.md").read_text()
    assert block(spec, "install-table") == render_table()
@pytest.mark.parametrize("mutate", ["drop-intact-from-registered", "drop-not-cur-from-pre"])
def test_table_twins_overlap(mutate): assert overlaps(mutated(ROWS, mutate)) != []
ROW_EXAMPLES = [...]   # по точке на каждый куб каждой строки + точки вне таблицы → unknown
@pytest.mark.parametrize("facts,phase", ROW_EXAMPLES)
def test_classify_rows(facts, phase): assert classify(facts) == phase
@pytest.mark.parametrize("phase", [r.phase for r in ROWS if r.action not in ("", "stop")])
def test_drive_calls_row_action_and_checks_post(fake_layout, phase): ...
    # facts строки → вызвано ровно действие строки; подменённое действие, дающее фазу вне
    # post → StepFailed с обеими фазами
def test_action_refuses_outside_its_phase(fake_layout): ...
    # seal при building (непустое дерево сборщика, отметки нет) → StepFailed, staging не создан
def test_building_cleans_then_rebuilds(fake_layout): ...       # building → cleanup → none → build-dir …
def test_partial_copy_removes_then_copies(fake_layout): ...    # не «copy уже сделан»
def test_drive_settles_before_classify(monkeypatch, fake_layout): ...  # порядок: settle → read
def test_spec_route_table_is_rendering_of_data():
    spec = Path("docs/superpowers/specs/2026-10-13-df-installer-design.md").read_text()
    assert block(spec, "install-routes") == render_routes()
@pytest.mark.parametrize("name", list(ROUTES))
def test_routes_match_table(monkeypatch, fake_layout, name): ...
    # S1-R8-N1, S1-R9-N1: настоящие реализации calls; записываются события fsutil
    # (fault_point, sync_dir, sync_tree) и запуски run_as/spawn_as. Для КАЖДОГО сочетания
    # значений условий маршрута (подготовленные объекты в fake_layout) события == развёртка
    # ROUTES[name] до шагов PROTOCOLS (WRAPS — как их протокол; у примитива с вариантами —
    # leaf_variants того варианта, который задаёт подготовка, S1-R11-N1); лист вида check — ни
    # одного события и запуска; builder — только run_as("df-build", …). Сравнение —
    # последовательностей: порядок, кратность и объекты (S1-R10-N1), без схлопывания
    # повторов. Подмена функции целиком не допускается. Двойники — провал: откат в обход
    # cleanup_staging; лишний sync_dir(releases) в accept_copy; cleanup без второго
    # remove_own_tree; remove_own_tree с неверной целью; снятое условие шага.
    # R33.F3: листья LATER_LEAVES (7a/8/8a/9) — заглушки из leaf_variants; двойники
    # «install_admin со скрытой write_mark» и «transfer_check со скрытой очисткой
    # build_home» — в Tasks 8a и 8; Task 9 повторяет тест со всеми настоящими calls
def test_accept_copy_events_exact(monkeypatch, fake_layout): ...
    # S1-R10-N1: настоящий accept_copy → receive_tree → SpawnedProducer.end: события ==
    # leaf_effects("accept_copy") — created, process(df-build), created,
    # sync_tree(releases/<id>), sync_dir(releases) ровно один раз; при отказе потока —
    # created, process(df-build), TreeError, частичная копия на месте
def test_cleanup_removes_both_trees_in_route_order(monkeypatch, fake_layout): ...
    # S1-R10-N1: незарегистрированные releases/<id> (root) и admin/<id> есть одновременно →
    # события == unfold_route("cleanup") для этих условий: remove_own_tree(releases/<id>),
    # затем remove_own_tree(admin/<id>); оба дерева удалены
def test_route_leaves_have_one_kind(): ...
    # check_data(): каждый лист — ровно протокол или вид CALLS; ABSENT_OK ⊆ PROTOCOLS;
    # op каждого шага ∈ OPS; у протокола с вариантами условие каждого варианта —
    # VARIANT_WHEN, ключи NOT_MODELED — существующие варианты (S1-R11-N1, S1-R12-N1); у
    # ABSENT_OK вариант absent без изменений и синхронизаций root;
    # GUARD_SOURCES каждого условия ⊆ settle_dirs(); двойник без admin_python в A — провал
def test_plan_route_calls_is_rendering_of_data(): ...
    # R33.F4: блок install-route-calls плана (Task 9) == render_route_calls();
    # test_action_table_is_route_unfolding (install_calls, docstring) — в Task 9
@pytest.mark.parametrize("guard", sorted(GUARD_NAMES - {"not reg", "local_bin=trusted"}))
def test_route_guard_uses_lstat_via_parent_fd(monkeypatch, fake_layout, guard): ...
    # подмена os: ни одного open/opendir объекта, только fstatat через дескриптор родителя;
    # R33.F15: not reg (только releases.toml) и local_bin=trusted (один вызов политики) —
    # отдельные тесты
@pytest.mark.parametrize("stop", ["created", "chown"])
def test_prepare_dir_transfers_by_fd(monkeypatch, fake_layout, stop): ...
    # S1-R8-N2: open до fchown, fchown/fsync/close по дескриптору; после fchown ни одного
    # open пути releases/<id>; остановка в точке stop → повтор: root-пустой каталог —
    # unknown → remove_own_tree; переданный — prepared
def test_spec_protocol_table_is_rendering_of_data():
    spec = Path("docs/superpowers/specs/2026-10-13-df-installer-design.md").read_text()
    assert block(spec, "install-protocols") == render_protocols()
@pytest.mark.parametrize("name,variant", [(n, v) for n in PROTOCOLS
                                          for v in leaf_variants(n)])
def test_primitive_protocols_match_table(monkeypatch, fake_layout, name, variant): ...
    # S1-R7-N1, S1-R11-N4: подмена fsutil и нижних операций записывает события настоящего
    # примитива в варианте variant (подготовка fake_layout задаёт исход): fault_point →
    # вид изменения, sync_dir(путь) → sync_dir(имя DIRS | NON_FACT_DIRS | parent — те же
    # правила, что settle_paths), sync_tree → sync_tree(объект), завершение процессов
    # df-build (SpawnedProducer.end) → process(df-build), запуски run_as от df-build
    # подряд → run_as(df-build)+ (S1-R12-N1). Последовательность ==
    # leaf_variants(name)[variant]; виды событий — ровно OPS; лишний, пропущенный или
    # переставленный шаг — провал
def test_settle_on_acquire_syncs_acquire_paths(monkeypatch, fake_layout): ...
    # подмена sync_dir: install_dir первым, затем ровно acquire_paths(layout, pending id);
    # без pending.toml — без id-зависимых путей; home_parent входит (S1-R6-N1)
def test_acquire_dirs_cover_table_and_account(): ...
    # set(acquire_dirs()) == set(settle_dirs()) | {d for ds in ACCOUNT_SOURCES.values() for d in ds}
def test_no_progress_bounded(fake_layout): ...                 # действие ничего не меняет → StepFailed("drive"…) за max_iter
def test_mark_monotonic_and_drop(fake_layout): ...
def test_mark_of_other_config_ignored(fake_layout): ...
def test_built_mark_from_previous_boot_invalid(monkeypatch, fake_layout): ...  # → building
def test_root_mode_regressed_reverifies(fake_layout): ...   # stage=verified, корень 0700 → copied
def test_reverify_keeps_admin_mark(fake_layout): ...
    # stage=admin, digest и admin верны, корень 0700 → copied → verify → отметка остаётся
    # admin с прежним admin_digest → admin-ready (S1-R4-N2); write_mark не понижает
def test_rollback_removes_unregistered_0755_tree(fake_layout): ...
    # прерывание после chmod 0755 в verify, затем cleanup → remove_own_tree удаляет дерево
def test_registered_unhealthy_is_damaged(fake_layout): ...
    # reg, cur, L, но digest дерева ≠ записи реестра → damaged (не launched); то же при
    # расхождении отметки с записью: авторитетна запись
def test_current_without_registration_is_damaged(fake_layout): ...
def test_every_fact_has_durability_sources():
    assert set(FACT_SOURCES) == set(DOMAINS)
    assert all(FACT_SOURCES[f] and set(FACT_SOURCES[f]) <= set(DIRS) for f in DOMAINS)
def test_spec_settle_table_is_rendering_of_data():
    assert block(spec, "install-settle") == render_settle()
def test_settle_paths_keys_are_dirs(fake_layout): ...
    # S1-R10-N2: set(SETTLE_PATHS) == set(DIRS); settle_paths и acquire_paths содержат
    # layout.admin_python; двойник без admin_python — провал
@pytest.mark.parametrize("python", ["present", "absent", "policy-fail", "other-version"])
@pytest.mark.parametrize("registered", [False, True])
def test_read_facts_admin_python_matches_model(fake_layout, python, registered): ...
    # S1-R11-N2: other-version — на диске два административных Python (3.12.7 и 3.13.1),
    # отметка / запись называет 3.13.1, а есть только 3.12.7 → A / intact ложны; есть
    # оба → истинны по версии отметки / записи. R33.F8: activate и release.admin
    # проверяют Tasks 9, 12; запись без admin_python — ConfigError разбора (Task 1)
    # S1-R10-N2: ожидания — литеральная таблица в тесте (происхождение — facts модели
    # r18, тест её не читает): Python есть → A (до регистрации) / intact (после)
    # истинны; нет или не проходит trusted_path_problems → ложны (verified → admin;
    # registered → damaged)
def test_settle_syncs_exactly_settle_paths(monkeypatch, fake_layout): ...
    # S1-R12: первым — layout.quarantine (SETTLE_FIRST), затем пути фактов
    # множество путей sync_dir == settle_paths(layout, id) для существующих; в их числе etc,
    # bin_dir, admin, marks_dir (формы r4 и r5); отсутствующий staging_dir(id) пропущен
def test_builder_emptiness_decided_by_build_user(monkeypatch, fake_layout): ...
    # run_as-заглушка: вывод "x" → b-full; "" → b-empty; код 1 / таймаут → b-full;
    # root не вызывает os.listdir/scandir для releases/<id>
def test_admin_tree_lost_falls_back_to_verified(fake_layout): ...
def test_empty_prepared_dir_is_prepared_not_building(fake_layout): ...
def test_tar_failure_refuses_and_keeps_partial_for_route(fake_layout): ...
    # tar отказывает (нечитаемый root-only файл; bsdtar рвёт поток, GNU tar по полному
    # пути — ненулевой код, 2) → StepFailed, хвост stderr содержит «tar»; частичная копия root 0700 на
    # месте, производитель завершён (S1-R10-N3); повтор: partial-copy → маршрут
    # copy-partial удаляет остаток и продолжает до copied; при euid 0 — пропуск
    # (R33.F35, root-вариант — Task 13); через drive — tests/df/test_scenarios.py
def test_registered_release_never_removed(fake_layout): ...       # cleanup при записи → StepFailed
def test_partial_tree_not_accepted_by_journal(fake_layout): ...   # журнал «copy: done», отметки нет → не copied
def test_stray_tmp_before_registration_rebuilds(fake_layout): ...
    # R33.F24 (FM2): .release.json.*.tmp в releases/<id> до записи реестра → full=0 →
    # unknown → cleanup → сборка заново; ни registered, ни damaged
def test_seal_syncs_three_dirs(monkeypatch, fake_layout): ...
@pytest.mark.parametrize("variant", ["new", "reuse"])
def test_seal_variant_events(monkeypatch, fake_layout, variant): ...
    # S1-R12-N1: reuse — подготовлен пустой staging/<id> (прерывание сразу за mkdir):
    # события == leaf_variants("seal")[variant], без фиктивного created; new — с ним
def test_call_sites_match_signatures(fake_layout): ...
    # spawn_as, cleanup_tree, receive_tree подменены create_autospec(настоящая функция):
    # неверный вызов падает; argv[0] = install.approved_cli["tar"], заданы env и cwd
def test_reference_fetch_checks_head_and_uses_data_filter(monkeypatch, tmp_path): ...
def test_sources_mismatch_names_repo(tmp_path): ...
def test_plan_fields_lock_and_direct_url_equal():
    lock = {"git": "https://github.com/andrei-shtanakov/dispatcher?subdirectory=packages%2F"
                   "plan-fields&rev=f7f2cbcbbffc8d83870848317228c0a3487780ec"
                   "#f7f2cbcbbffc8d83870848317228c0a3487780ec"}
    du = {"url": "https://github.com/andrei-shtanakov/dispatcher", "subdirectory":
          "packages/plan-fields", "vcs_info": {"vcs": "git", "commit_id":
          "f7f2cbcbbffc8d83870848317228c0a3487780ec"}}
    assert source_from_lock(lock, root_dir=R) == source_from_direct_url(du)
def test_git_without_subdirectory_equal(): ...    # steward в devtools/uv.lock
def test_root_directory_equal_with_editable_absent(): ...
    # lock {"editable": "."} и {"url": "file:///…/src/devtools", "dir_info": {}}
@pytest.mark.parametrize("change", ["url", "subdirectory", "commit", "path",
                                    "editable-true", "unknown-query-key", "short-commit",
                                    "file-url-with-host", "non-root-directory"])
def test_source_negative_twins(change): ...   # каждое изменение → неравенство или отказ
def test_foreign_pth_refused_virtualenv_pth_allowed(tmp_path): ...
```

`fake_layout` (`tests/df/conftest.py`, создан в Task 7, R33.F19) работает от текущего
пользователя: `ROOT_UID` модулей — текущий uid, а у `df-build` отдельный фиктивный
`build_uid = os.getuid() + 100000` (R33.F1); владельцев его объектов хранит виртуальная
карта, которую читает `fsutil.owner_uid` (без неё `b-empty` и `root-700` неразличимы).
`pwd.getpwnam("df-build")` — текущий пользователь (R33.F27); `sync_tree` — одно событие
без `sync_dir` (R33.F16). Сценарии через `drive` — `tests/df/test_scenarios.py`:
настоящие `read_facts`, `route_action`, `run_route`, `acceptance_calls`; заглушки —
`LATER_LEAVES` и `admin_ready`; сбой — исключение в `fault_point`. Подменяются
только нижние операции (rev 8, r10, S1-R10-N1), композиция листьев — настоящая:
`os.fchown`/`os.chown` (записываются), механизм запуска `run_as`/`spawn_as` (`sudo`,
`setpriv`, `sandbox-exec` не вызываются; команда исполняется от текущего пользователя,
`tar` — локальный), `pkill`/`ps` внутри `reap_user_processes` (запуск записывается как
`process(df-build)`). `cleanup_tree`, `cleanup_build_home`, `receive_tree`,
`SpawnedProducer.end` — настоящие. Подмена функции целиком — только в
`test_call_sites_match_signatures` (сверка сигнатур, не событий).

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): install automaton driver, streamed acceptance, canonical sources`

### Task 7a: Ожидаемый состав из lock

Как построено (22.10): предполётная проверка `task-7a-preflight.md`, решения R36–R39
(R36 — находки F1 в редакции дополнения A2/A4, F2–F8, F10, N1–N5, A, B; R37 — F9 к
Task 14, закрыт решением владельца 22.10 — см. Task 14; R38 — три части 7a-golden / 7a-impl / 7a-paper; R39 — Linux-эталон в локальном
контейнере podman), решения владельца 22.10 о `UV_NO_CONFIG` и о раскладке окружения
(спека §3.2, §3.4, §3.5).

**Files:**
- Create: `df/markers.py`, `df/lockset.py`
- Modify: `df/routes.py` (лист `distribution_problems` в `acceptance_calls`, R36/F2)
- Test: `tests/df/test_markers.py`, `tests/df/test_lockset.py`, `tests/df/test_routes.py`
  (листовые тесты), `tests/df/fixtures/locks/<member>.uv.lock` (шесть членов `core`),
  `tests/df/fixtures/locks/<case>.<platform>.expected.json` (8 случаев × 2 платформы),
  `tests/df/fixtures/locks/exclude-newer/`, `tests/df/fixtures/locks/stale/`,
  `tests/df/fixtures/locks/SOURCES.md`

**Interfaces:**
- Consumes: `PlatformName` (Task 5); `InstalledDist`, `installed_distributions`,
  `normalize_name` (Task 7, `df/inputs.py`); `SourceId`, `source_from_lock`,
  `UnsupportedLock` (Task 7, `df/sources.py`); `build_src_dir` (Task 6, `df/build.py`;
  сверено по истории — введена исправлением Task 6 e12864e).
- Produces:
  - `marker_env(platform: PlatformName, python: str) -> Mapping[str, str]` — восемь
    переменных §3.5. macOS: `sys_platform=darwin`, `platform_system=Darwin`,
    `platform_machine=arm64`. Linux: `linux`, `Linux`, `x86_64`. Общие: `os_name=posix`,
    `implementation_name=cpython`, `platform_python_implementation=CPython`,
    `python_version=X.Y`, `python_full_version=X.Y.Z`.
  - `evaluate(marker: str, env: Mapping[str, str]) -> bool` — грамматика §3.5; маркер
    разбирается **целиком** до вычисления (неподдерживаемый хвост за `or`/`and` — отказ, F5):
    - сравнения `переменная оп 'строка'` или `'строка' оп переменная`;
    - `and`, `or` (`and` связывает сильнее), скобки;
    - переменные Python — все шесть операторов, кортежи целых с дополнением нулями
      (`python_version == '3.12.0'` истинно при `3.12`); литерал не вида `N(.N)*` — отказ;
      строковые переменные — только `==`/`!=` (F6).

    Иное (`extra`, `group`, `platform_release`, `in`, `not`, `~=`, `===`, две переменные или
    две строки, лишние токены, слишком глубокая вложенность) — `UnsupportedLock` с текстом
    маркера.
  - `ExpectedDist(name: str, version: str, source: SourceId)`; `name` нормализован
    `normalize_name` (N1) — той же функцией, что у `installed_distributions`.
  - `LOCK_TOP_KEYS`, `LOCK_MANIFEST_KEYS` и `lock_layout_problems(data) -> list[str]` —
    **одно** множество ключей lock для проверки заголовка и для `uv_config_problems`
    (дополнение A4): верхний уровень — `version`, `revision`, `requires-python`,
    `resolution-markers`, `supported-markers`, `required-markers`, `manifest`, `package`;
    `[manifest]` — `constraints`, `overrides`, `excludes`, `build-constraints`; каждый ключ
    `[options]` и любой иной ключ — проблема с его именем.
  - `expected_distributions(lock: bytes, *, groups: tuple[str, ...], env: Mapping[str,
    str], root_dir: Path) -> frozenset[ExpectedDist]` — алгоритм §3.5:
    - ошибка UTF-8/TOML/вложенности — `UnsupportedLock` (N5);
    - заголовок: `version` и `revision` — целые 1 и 3 (`true`, `3.0` — отказ); ключи —
      `lock_layout_problems` пуст;
    - ключи `[[package]]` — из разрешающего списка (`name`, `version`, `source`,
      `dependencies`, `optional-dependencies`, `dev-dependencies`, `sdist`, `wheels`,
      `metadata`, `resolution-markers`), ключи записи зависимости — `name`, `version`,
      `source`, `marker`, `extra`; иное — отказ с именем ключа;
    - корень — ровно один пакет с источником `virtual`/`editable`/`directory`, равным `"."`;
    - **до обхода** (F5, ревью M1) проверяется весь lock, достижимый или нет: источник каждого
      пакета (`source_from_lock`), каждый маркер, каждая запись разрешается ровно в одну цель
      и каждый запрошенный ею `extra` у цели есть — отказ не зависит от платформы;
    - обход в ширину: зависимости корня и `dev-dependencies[g]` для каждой `g ∈ groups`;
      имена групп сравниваются нормализованными, как `--group` у `uv` (`my_group` =
      `my-group`); отсутствующая группа — `UnsupportedLock`; записи групп вычисляются как
      зависимости (маркер, `extra`); `extra` корня не выбираются — сборка не передаёт `--extra`; ключ
      посещения — (пакет, extra) (F7);
    - цель — по нормализованному `name` и сырым `version`/`source` записи, до проекции
      (N2); ноль или больше одного кандидата — `UnsupportedLock`;
    - одно имя в результате дважды — `UnsupportedLock` (uv ставит одно распределение на имя,
      ревью M5);
    - источник — `source_from_lock(source, root_dir=root_dir)`.

    Виртуальный корень в результат не входит; корень `editable`/`directory` входит
    (`--no-editable`). **Контракт `root_dir`** (замер `uv` 0.11.23, 22.10, таблица в
    `SOURCES.md`): `uv` записывает в `direct_url.json` путь `--project` как задан —
    нормализуя только лексически (`/` в конце и `..` сворачиваются), ссылки не разрешаются.
    Поэтому `root_dir` — ровно путь `--project` сборки (`build_src_dir`), абсолютный и без
    `..` (иначе `ValueError`), и не разрешается.
  - `distribution_problems(installed: Sequence[InstalledDist], expected:
    frozenset[ExpectedDist]) -> list[str]` — сравнение троек в обе стороны, строка на
    различие, отсортировано (N4): `«<имя> <версия>: заявлено X, ожидается Y»`, `«лишнее: …»`,
    `«нет: …»`; имя, установленное дважды, — проблема. `git` и `directory` — по всем полям
    `SourceId`; `registry` ожидаемой стороны проецируется в `SourceId("registry")` (`index:
    not declared`). Эффекты: нет. **Названное ограничение** (решение владельца 22.10,
    дополнение B) — константа `DECLARED_LIMIT`: «метаданные распределений согласованы с lock
    (заявлено); не доказывает отсутствия лишнего кода и происхождения файлов релиза».
  - `UV_PINNED_VERSION = "0.11.23"`, `UV_PROJECT_KEYS_0_11_23` — разрешающий список
    `[tool.uv]` (спека §3.2; все 11 ключей подтверждены замером `uv` 0.11.23 — таблица в
    `SOURCES.md`; `conflicts`, `workspace`, `managed`, `dependency-groups`,
    `build-backend` в него намеренно не входят, дополнение A2). Имя константы несёт версию:
    смена `uv` заставляет перепроверить список замером.
  - `uv_config_problems(pyproject: bytes, uv_toml: bytes | None, lock: bytes) -> list[str]`
    — по доверенному чекауту: `uv.toml` есть (`not None`) — проблема независимо от
    содержимого; ключ `[tool.uv]` вне `UV_PROJECT_KEYS_0_11_23` — `pyproject.toml [tool.uv]
    <ключ>`; lock — `lock_layout_problems` (`uv.lock [options] <ключ>`, `uv.lock [manifest]
    <ключ>`). Нечитаемый вход — строка-проблема, не исключение. Эффекты: нет. Потребитель —
    Task 9, до сборки.
  - `df/routes.py`: лист `distribution_problems` — в `acceptance_calls` рядом с
    `verify_sources`, ключ снят с `LATER_LEAVES` (R36/F2). Для каждого репо профиля:
    `installed_distributions(releases/<id>/venv/<r>)` против
    `expected_distributions(refs[r]/uv.lock, groups=pin.groups, env=marker_env(platform,
    profile.python), root_dir=build_src_dir(layout, id, r))`. Проблемы всех репо (в том
    числе нет чекаута, `TreeError`, `UnsupportedLock`) — один `StepFailed("verify", …)` с
    `DECLARED_LIMIT` в начале. Успех — `RouteContext.installed = {repo: [InstalledDist]}` и
    событие журнала `distributions-note` с `DECLARED_LIMIT` (вид `<тема>-note`, как
    `sync-note`). `installed` не забирается `_take`: его читает вызывающий после маршрута;
    для этого `acceptance_calls(..., ctx: RouteContext | None = None)` принимает контекст
    вызывающего (Task 9).

- [x] **Step 1: Failing tests** (как построено)

```python
def test_evaluate_macos_312(marker, expected): ...          # + F6: дополнение нулями, обратная форма, приоритет
def test_unsupported_marker(marker): ...                    # + F5: «… or extra == 'x'», лишние токены
def test_deep_marker_nesting_is_refused(): ...              # ревью M9
@pytest.mark.parametrize("platform", ["macos-arm64", "linux-x86_64"])
@pytest.mark.parametrize("case,member", GOLDEN_CASES)      # 6 членов + spec-runner.dev
def test_real_locks_match_golden(case, member, platform): ...  # + devtools.governance-conductor
    # expected_distributions == эталон через source_from_direct_url сырого direct_url.json;
    # root_dir — из поля JSON (argv захвата), не из direct_url.json (F3); эталон как
    # InstalledDist → distribution_problems == []
def test_goldens_exercise_root_git_and_platform(): ...      # не пусто: корень, git, Linux − macOS
def test_lock_refusals(case): ...   # каждый случай — со своей причиной: revision-4, version-2,
    # version-bool, revision-float, conflicts, options-present, manifest-dependency-metadata,
    # manifest-members, unknown-top-key, unknown-package-key, unknown-dependency-key,
    # dep-extra-marker, ambiguous-duplicate, zero-candidates, version-mismatch,
    # missing-group, missing-extra, two-roots, no-root, name-twice-in-result,
    # unreached-bad-marker/-source/-optional, unreached-ambiguous, unreached-missing-target,
    # unreached-missing-extra, deep-nesting, not-toml, not-utf8
def test_virtual_root_excluded_editable_root_included(): ...
def test_extra_pulls_optional_dependencies(): ...   # pyjwt[crypto] → cryptography
def test_extra_after_plain(): ...                   # F7a
def test_root_extra_not_selected(): ...             # F7b
def test_group_entry_marker_and_extra(): ...        # F7c
def test_root_dir_is_taken_as_spelled_not_resolved(tmp_path): ...   # родитель-ссылка
def test_missing_and_extra_installed_both_reported(): ...
def test_git_commit_mismatch_reported_as_declared(): ...
@pytest.mark.parametrize("change", ["url", "subdirectory", "commit", "editable-true",
                                    "registry-has-direct-url"])
def test_source_identity_mismatch_reported(change): ...
def test_same_name_version_different_source_distinguished(): ...
def test_exclude_newer_fixture_two_problems(): ...   # pyproject [tool.uv] + uv.lock [options]
def test_stale_fixture_is_not_a_config_problem(): ...  # устаревший lock — дело --locked
def test_setting_keys_refused(key, line): ...   # compile-bytecode, exclude-newer, no-binary, …
def test_uv_toml_present_is_a_problem_whatever_it_holds(): ...
def test_lock_only_options_are_a_problem_per_key(): ...   # флаг CLI при uv lock
def test_lock_keys_shared_with_expected_distributions(case): ...
def test_devtools_own_pyproject_and_lock_pass(): ...
# tests/df/test_routes.py
def test_distribution_problems_leaf_accepts_and_keeps_installed(fl): ...
def test_distribution_problems_leaf_passes_groups_env_and_root_as_spelled(fl, tmp_path): ...  # ревью M2
def test_distribution_problems_root_is_the_builds_project_path(fl): ...  # == --project из build_commands
def test_distribution_problems_leaf_lists_every_repo(fl): ...
```

- [x] **Step 2: Эталоны** (R38: часть 7a-golden; провенанс — `fixtures/locks/SOURCES.md`).
  Для каждого члена на его SHA (origin HEAD 22.10, 40 hex; к будущим пинам `core` отношения
  не имеет — их сверяет Task 14, F4) из копии `git archive` по каноническому пути выполнена
  команда сборки с её окружением (`UV_NO_CONFIG=1`, `UV_LINK_MODE=copy`,
  `UV_PYTHON_PREFERENCE=only-managed`, свой `UV_CACHE_DIR`): `uv sync --locked
  --no-editable --no-default-groups --group … --project <root_dir> --python 3.12.13`,
  и записано множество установленных распределений с сырым `direct_url.json` и
  `root_dir` из argv (F3):
  - macOS — на машине разработчика, `uv` 0.11.23;
  - Linux — **в локальном одноразовом контейнере podman** (`ubuntu:24.04` по digest,
    `linux/amd64`, uid 1000, проверены `x86_64`/`linux`) вместо разового CI-прогона (R39,
    ревью M7): тот же факт `uv` с провенансом по digest образа; Task 13 повторяет чистый
    golden-тест на Linux в CI.

  Фикстуры `exclude-newer/` (настоящий lock `uv` 0.11.23 с `[options] exclude-newer`) и
  `stale/` (устаревший lock без настроек) отличают неподдерживаемую настройку от
  устаревшего lock. Скрипт захвата не поставляется. Эталон — факт `uv`, а не вывод нашего
  алгоритма.
- [x] **Step 3–5:** FAIL → implement → PASS
- [x] **Step 6: Commit** `feat(df): expected distribution set from uv.lock (restricted
  markers)`; затем `test(df): uv.lock goldens and exclude-newer fixture for Task 7a`,
  `test(df): real-lock goldens, exclude-newer fixture, root path contract`,
  `fix(df): Task 7a review minors`, `docs(df): Task 7a as built; owner decisions on uv
  settings and sitecustomize/_pth`, `fix(df): Task 7a final review minors`.

### Task 8: Граница цепочки исполнения, инвентарь entrypoints и проверка переноса

Как построено (22.10): предполётная проверка `task-8-preflight.md`, решение R40 (все
строки Ruling F1–F20, N1–N5 приняты как обязательные; где Ruling меняет текст брифа —
действует Ruling). Замеры — uv 0.11.23, macOS arm64, реалистичный релиз `core` (§2
предполётной проверки); Linux не замерялся — замеры отданы Task 13.

**Files:**
- Create: `df/boundary.py`
- Modify: `df/digest.py` (`resolve_in_tree`, F3); `df/platform.py` (публичные имена
  `MACHO_MAGIC`, `ELF_MAGIC`, `dependency_problems`, `is_system_path` — только
  переименования, F9/F10); `df/inputs.py` (публичные имена `select_names`, `read_capped` —
  только переименования); `df/routes.py` (листья `static_problems` и `transfer_check` в
  `acceptance_calls`, `RouteContext.python_declared`, F6); `df/install_table.py` (текст
  `CALLS["transfer_check"]`, перерисован в спеке, F8)
- Test: `tests/df/test_boundary.py`, `tests/df/test_digest.py` (`resolve_in_tree`),
  `tests/df/test_routes.py` (листовые тесты и двойник)

**Interfaces:**
- Consumes: `linked_libraries`, `LibDeps`, `SYSTEM_LIB_DIRS`, `run_as`, `RunResult`,
  `BUILD_USER` (Task 5; `NO_NETWORK_PROFILE` применяется внутри `run_as(..., network=False)`,
  N4); `check_links`, `resolve_in_tree`, `open_regular`, `canonical_root`, `check_name`
  (Task 3, `df/digest.py`); `pth_problems`, `select_names`, `read_capped` (Task 7,
  `df/inputs.py`); `PYTHON_TRIPLES` (Task 6).
- Produces:
  - `resolve_in_tree(tree, logical_root, path) -> PurePosixPath` (`df/digest.py`) — любой
    путь (абсолютный под логическим корнем или относительный к дереву) разрешается
    покомпонентно тем же лексическим обходом, что `resolve_link_in_tree`; выход, висячий
    компонент, не-каталог в середине, больше 40 переходов — `TreeError`.
  - `expected_entrypoints(ref_dir: Path) -> frozenset[str]` — §3.4. `pyproject.toml` с
    потолком, `tomllib`. Пакет: `tool.uv.package` равен `true`, либо не задан и есть
    `[build-system]`; не-bool `package` — `ValueError`. Для пакета — ключи
    `[project.scripts]` (`"scripts"` в `project.dynamic` — `ValueError`; имя, негодное для
    файла, — `TreeError`); `[project.gui-scripts]` не входит. Иначе — пустое множество.
  - `BinClass`, `classify_bin(path)` — первые 4 байта: любая из 8 магий Mach-O
    (`MACHO_MAGIC`) или `ELF_MAGIC` → `native`; `#!` → `script`; иное → `other`. Чтение
    через `open_regular`: ссылка или не обычный файл — `TreeError` (F10).
  - `EnvPaths`, `env_paths(release_dir, repo)` — правило (б) и разрешённые каталоги
    окружения (F3, F4, F19): `venv/<r>/pyvenv.cfg` обязателен, разбирается как у CPython:
    строки — по `\n` (один хвостовой `\r` снимается), `key.strip().lower()`,
    `value.strip()`. getpath делит по `\n`, `site.py` — по `\r`/`\n`/`\r\n` и берёт
    последнее значение, поэтому `\r` вне `\r\n` (ревью круг 1, F1: подменял
    `include-system-site-packages`), иной разрыв строки `str.splitlines` (`\v`, `\f`,
    `\x1c`–`\x1e`, `\x85`, U+2028/2029) и управляющий символ кроме `\t`/`\r`/`\n` —
    отказ; после этого строки обоих читателей и наши совпадают; ровно один `home` и один
    `include-system-site-packages` (getpath берёт первый `home`, `site.py` — последний),
    значение `.lower() == "false"`; `home` канонический, под релизом и разрешается внутри
    дерева в `python/<ключ>/bin`, `<ключ>` = `cpython-3.X.Y-<тройка>` с ровно одним
    `lib/python3.X`; `venv/<r>/lib/python3.X/site-packages` — настоящие каталоги. Отказ —
    `TreeError` с префиксом `venv/<r>: `.
  - `script_heads(release_dir, repo) -> tuple[bytes, bytes]` — две точные формы uv (F1):
    `#!P\n` и `#!/bin/sh\n'''exec' 'P' "$0" "$@"\n' '''\n`, `P` = `release_dir/venv/<r>/bin/python`.
  - `static_problems(release_dir, platform, expected) -> list[str]` — только данные,
    используется приёмом **и** `doctor`. Эффекты: нет (`otool`/`readelf` — от root через
    `run_command`, не `run_as`). Никогда не бросает для дерева (F11): каждый отказ —
    строка `"<rel>: <причина>"`. Окружения — ровно ключи `expected`; лишнее в `venv/` —
    проблема. Проверки:
    - **(а)** каждый исполняемый обычный файл в `venv/<r>/bin`: `native` (проверяет (в)) или
      `script` с одной из двух голов `script_heads` побайтно (F1, F2: интерпретатор —
      ровно `bin/python` своего окружения); иное — отказ. Ссылки в `bin` — только `python`,
      `python3`, `python3.X`, каждая разрешается внутри дерева в `python/<ключ>/bin/python3.X`
      (обычный `native`); нет `python` — отказ; иная ссылка — отказ (F2);
    - **(б)** `env_paths` для каждого окружения; любое имя `pyvenv.cfg` (по `casefold`) в
      `venv/<r>/bin` — отказ: `site.py` читает его первым (F4); все окружения называют один
      и тот же `python/<ключ>`, ключ — с тройкой платформы (F19);
    - **(в)** каждый обычный Mach-O/ELF релиза (F9): `LC_DYLD_ENVIRONMENT` — отказ;
      загрузчик вне `SYSTEM_LIB_DIRS` — отказ; каждый путь поиска после подстановки
      `@loader_path` / `$ORIGIN` / `${ORIGIN}` своим каталогом — внутри дерева (каталог) или
      под `SYSTEM_LIB_DIRS`; пустой, иная подстановка, относительный, иной — отказ;
      `@executable_path` — только у файлов в `python/<ключ>/bin`; зависимость `@rpath/x`
      ищется по своим путям поиска; `@loader_path/x`, `$ORIGIN/x`, абсолютная внутри
      релиза — существующий обычный файл внутри дерева; ELF-soname — свои пути, затем
      `dependency_problems` (`LOADER_DIRS`); иное — `dependency_problems`;
    - **(г)** `check_links(release_dir, release_dir)` пуст;
    - **(д)** каждое имя `expected[r]` — `select_names(bin, exact=имя)` строго (совпадение
      только по регистру — отказ, F12), обычный файл с битом исполнения, класс `script`;
    - **(е)** `pth_problems` каждого окружения пуст (`TreeError` обхода — строка);
    - **(ё)** решение владельца 22.10: `sitecustomize` (имя `casefold` равно
      `sitecustomize` или начинается с `sitecustomize.`; только верхний уровень каталога) в
      `site-packages` окружения, `python/<ключ>/lib/python3.X` и его `lib-dynload`; любой
      `*._pth` (`casefold`) в `venv/<r>/bin`, `python/<ключ>/bin`, `python/<ключ>/lib`;
      существующий `python/<ключ>/lib/python3XY.zip` (F5).
    - **(ё), класс файлов старта** (ревью круг 1, F2): `STARTUP_FILES` — одна таблица
      `StartupFile(where, name, reader, rule)` всех путей, которые читают getpath и `site.py`
      CPython 3.12 при старте: `pyvenv.cfg` ×3 (`venv/<r>` — обязателен, (б); `venv/<r>/bin`
      и `python/<ключ>` — запрещены), `*._pth` у трёх мест, `python3XY.zip` (только
      отсутствующий), у `home` = `python/<ключ>/bin` — `pybuilddir.txt`, `Modules`
      (`Setup.local`) и `lib` (поиск prefix) — запрещены (пробы 22.10: оба маркера сборки
      подменяют `lib-dynload`/zip даже под `-I`, `sitecustomize` из подменённого каталога
      исполнился), ориентиры `lib/python3.X/os.py` и `lib-dynload` — обязательны; `.pth` и
      `sitecustomize` — ссылкой на правила (е)/(ё). Проверка идёт по таблице.
      `base_bin_names(minor)` — белый список `python/<ключ>/bin` (замер uv 0.11.23:
      `2to3`, `2to3-3.X`, `idle3`, `idle3.X`, `pip`, `pip3`, `pip3.X`, `pydoc3`, `pydoc3.X`,
      `python`, `python3`, `python3-config`, `python3.X`, `python3.X-config`); иное имя —
      отказ. Вне таблицы файлы интерпретатора не сверяются (§3.5): (ж) не доказывает
      отсутствия кода старта в них.
    - **(ё), структурное правило** (ревью круг 2, N1–N3; третья находка класса — меняется
      механизм, не список): `chain_dirs(env)` — белый список с типом (`file`/`dir`/`link`/
      `entry`) каждого каталога цепочки `search_up` getpath от `home` до корня релиза:
      `python/<ключ>/bin` (14 имён, `entry`), `python/<ключ>` (`bin`, `BUILD`, `include`,
      `lib`, `share`), `python/` (каталог ключа, ссылка на минор → каталог ключа, `.lock`,
      `.temp`, `.gitignore`), корень (`src`, `venv`, `python`, `release.json`). Иное имя
      (в том числе регистровый двойник) или тип — отказ. Строки таблицы для обхода:
      `python/lib`, `<релиз>/lib`, `python/<ключ>/lib/lib` (известная libpython). Ориентиры
      `required` проверяются по типу через `lstat` (`os.py` — файл, `lib-dynload` —
      каталог, N2). `usercustomize` — строка-ссылка на (б)/(ж) (N3). Выше корня релиза —
      каталоги root (предпосылка §3.10).
    - **(б)/(а), закреплённые старты обходов** (ревью круг 3, N4/N5; пробы: prefix — от
      реального каталога базового исполняемого файла, exec_prefix — от лексического
      каталога цели `venv/<r>/bin/python`; при копии оба обхода идут от `home`):
      `venv/<r>/bin/python` — ссылка с текстом ровно `<релиз>/python/<алиас>/bin/python3.X`;
      `python3`, `python3.X` — ссылки с текстом `python`; копия под любым из трёх имён и
      иное имя `python*` (`casefold`) в `venv/<r>/bin` — отказ (ревью круг 3, N6); `home` — ровно
      `<релиз>/python/<алиас>/bin`; ссылка-алиас — с текстом `<релиз>/python/<ключ>`.
      Сверка по тексту, не по разрешению (замер: `uv` пишет форму с алиасом).

    Названное ограничение (Task 7a, `DECLARED_LIMIT`): код без метаданных распределения
    `installed_distributions` не видит; (а)–(ё) его не закрывают, `RECORD` в v1 не сверяется.
  - `TransferResult(problems: list[str], python_declared: dict[str, str])`.
  - `transfer_check(release_dir, layout, *, user, expected) -> TransferResult` — §3.3 п. 5;
    вид `builder` (`CALLS`, F8): ничего не удаляет. Эффекты: `run_as(df-build)+`. Все
    запуски — `run_as(user, network=False, timeout=60)` с окружением ровно `HOME =
    layout.build_home`, `PATH = /usr/bin:/bin`, `LANG = C.UTF-8` и `cwd = build_home`
    (F16). Одно правило исхода: `launched=False` — «инфраструктура: …», `timed_out`,
    `stdout_truncated`, код ≠ 0 — проблема с хвостом stderr. Сначала `find build_home
    -mindepth 1 -maxdepth 1 ( -name tmp -o -name cache ) -print`: непустой вывод —
    «остатки build_home» и ни одного запуска дальше; любой сбой `find` — проблема
    инфраструктуры. Затем по каждому репо `expected`:
    - **(ж)** `venv/<r>/bin/python` без `-I` (но с `-P`) и с `-I`, `-c` печатает
      `[sys.path, site.ENABLE_USER_SITE, sys.version]`; ответ — ровно такой список, иначе
      проблема. `-P` (CPython ≥ 3.11) не ставит cwd (`build_home`, запись `df-build`) в
      `sys.path`: иначе модуль оттуда (`json.py`) подделал бы ответ пробы; запуск без `-I`
      по-прежнему измеряет поведение `pyvenv.cfg`/`site` (ревью безопасности Task 8).
      Пустой элемент `sys.path` — проблема. Каждый элемент абсолютный и разрешается внутри дерева в
      **свои** каталоги (ё) этого репо (`site-packages` своего окружения, stdlib,
      `lib-dynload`; F13); единственное исключение — отсутствующий `python3XY.zip` в
      `python/<ключ>/lib` (F5). Элемент, на котором разрешение бросает (NUL,
      ENAMETOOLONG), — строка-проблема (ревью круг 1, F3). `ENABLE_USER_SITE` — ровно `False`. `sys.version` обоих
      запусков совпадает и записывается в `python_declared[r]`;
    - **(з)** каждый скрипт `expected[r]` запускается с `--help`, независимо от вида
      `shebang`: код 0 за 60 с; stdout не читается.
  - `df/routes.py` (F6): листья `static_problems` и `transfer_check` в `acceptance_calls`,
    ключи сняты с `LATER_LEAVES`. Ожидаемый инвентарь — `expected_entrypoints(refs[r])` по
    каждому репо профиля (нет чекаута или нечитаемый `pyproject.toml` — проблема).
    `static_problems` — проблемы одним `StepFailed("verify", …)`; `transfer_check` — от
    `BUILD_USER`, проблемы одним `StepFailed("verify", …)`, успех —
    `RouteContext.python_declared = {repo: sys.version}` (не забирается `_take`, как
    `installed`; его читает Task 9).

- [x] **Step 1: Failing tests** (как построено)

```python
# tests/df/test_boundary.py — синтетическая раскладка по замеру uv 0.11.23: python/<ключ>,
# ссылка uv на минорную версию, venv через неё, скрипты с точными головами;
# linked_libraries и run_as — заглушки
def test_measured_layout_passes(tmp_path, deps): ...
def test_classify_bin(tmp_path): ...                         # все 8 магий Mach-O, ELF, #!, иное
@pytest.mark.parametrize("shebang", ["#!/usr/bin/env python3", "#!/usr/local/bin/python3"])
def test_foreign_shebang_refused(tmp_path, deps, shebang): ...
def test_uv_trampoline_accepted(tmp_path, deps): ...         # точные байты замера (F1)
def test_other_script_forms_refused(tmp_path, deps, head): ...  # dirname/realpath PBS,
    # /usr/bin/python3 в трамплине, bin/python3, python другого окружения,
    # <release>/src/x/run.sh (F2), лишний аргумент
def test_native_without_shebang_accepted(tmp_path, deps): ...
def test_other_executable_refused(tmp_path, deps): ...
@pytest.mark.parametrize("name", ["python", "python3", "python3.12"])
def test_foreign_interpreter_link_refused(tmp_path, deps, name): ...   # F2
def test_stray_link_in_bin_refused(tmp_path, deps): ...
def test_missing_python_link_refused(tmp_path, deps): ...
def test_pyvenv_home_outside_refused(tmp_path, deps): ...
def test_pyvenv_home_must_be_canonical_python_bin(tmp_path, deps, home): ...  # ., //, lib, ..
@pytest.mark.parametrize("defect", ["missing", "no-home", "no-include-system-site-packages"])
def test_pyvenv_cfg_is_mandatory(tmp_path, deps, defect): ...   # (б) решение владельца 22.10
def test_pyvenv_system_site_value(tmp_path, deps, line, ok): ...  # False/false — да; true/yes — нет
def test_pyvenv_cfg_line_break_differential_refused(tmp_path, deps, joint): ...
    # «home = <внутри>\x0bhome = /elsewhere», \f, \x1c–\x1e, \x85, U+2028/2029, NUL
def test_pyvenv_cfg_crlf_accepted(tmp_path, deps): ...
def test_pyvenv_cfg_lone_cr_refused(tmp_path, deps): ...   # ревью круг 1, F1
def test_pyvenv_duplicate_key_refused(tmp_path, deps, key): ...
def test_pyvenv_cfg_in_bin_refused(tmp_path, deps, name): ...   # F4, и по casefold
def test_environments_on_different_pythons_refused(tmp_path, deps): ...   # F19
def test_python_of_another_platform_refused(tmp_path, deps): ...
def test_native_dep_outside_refused(tmp_path, deps): ...     # /opt/homebrew/lib/x.dylib
def test_rpath_origin_inside_accepted(tmp_path, deps): ...   # ELF $ORIGIN, soname в .libs
def test_macho_rpath_and_loader_path_accepted(tmp_path, deps): ...
def test_executable_path_in_python_bin_accepted(tmp_path, deps): ...
def test_executable_path_outside_python_bin_refused(tmp_path, deps, libdeps): ...  # F9
def test_native_rules_refuse(tmp_path, deps, libdeps, needle): ...  # LC_DYLD_ENVIRONMENT,
    # загрузчик вне системы, путь поиска вне релиза, пустой, $LIB, относительный,
    # @rpath не найден, выход через @loader_path, голое имя на macOS
def test_rpath_inside_release_by_absolute_path_accepted(tmp_path, deps): ...
def test_inspection_failure_is_a_problem(monkeypatch, tmp_path): ...   # F11
def test_escaping_link_refused(tmp_path, deps): ...          # (г)
def test_missing_expected_entrypoint_refused(tmp_path, deps): ...
def test_entrypoint_in_wrong_case_refused(tmp_path, deps): ...   # F12
def test_entrypoint_must_be_an_executable_script(tmp_path, deps): ...
def test_stray_pth_refused(tmp_path, deps): ...              # (е)
def test_untrusted_pth_walk_is_a_problem(tmp_path, deps): ...   # TreeError → строка
def test_unexpected_environment_refused(tmp_path, deps): ...
def test_missing_environment_refused(tmp_path, deps): ...
@pytest.mark.parametrize("name", ["sitecustomize.py", "sitecustomize.pyc",
                                  "sitecustomize.cpython-312-darwin.so", "sitecustomize/",
                                  "SiteCustomize.py"])
def test_sitecustomize_refused(tmp_path, deps, name): ...    # (ё); регистр — по casefold
def test_sitecustomize_lookalikes_allowed(tmp_path, deps): ...
@pytest.mark.parametrize("where", ["venv-site-packages", "python-stdlib",
                                   "python-lib-dynload"])
def test_sitecustomize_refused_everywhere_on_sys_path(tmp_path, deps, where): ...
@pytest.mark.parametrize("where", ["venv-bin", "python-bin", "python-lib"])
def test_pth_underscore_file_refused(tmp_path, deps, where): ...  # <exe>._pth (N5)
def test_python_zip_present_refused(tmp_path, deps, name): ...   # F5
def test_every_refused_startup_file_is_refused(tmp_path, deps, entry, kind): ...
    # каждая строка STARTUP_FILES с rule refused/absent, файлом и каталогом
def test_startup_table_names_the_probed_build_markers(): ...
def test_required_startup_landmark_missing_refused(tmp_path, deps, name): ...  # os.py, lib-dynload
def test_base_bin_allowlist(tmp_path, deps): ...   # все замеренные имена — да; иное — нет
def test_chain_layout_as_measured_passes(tmp_path, deps): ...
def test_chain_listing_refuses_anything_else(tmp_path, deps, where, form): ...
    # 4 каталога цепочки × файл / каталог / ссылка / регистровый двойник
def test_chain_listing_fixes_each_type(tmp_path, deps, path, form): ...
def test_prefix_walk_out_of_the_key_dir_refused(tmp_path, deps, where): ...  # как в пробе
def test_required_landmark_type(tmp_path, deps, landmark, form): ...   # N2
def test_usercustomize_is_a_reference_row(): ...                       # N3
def test_minor_alias_text_is_pinned(tmp_path, deps, text): ...   # круг 3
def test_link_target_through_unlisted_dir_refused(tmp_path, deps, where): ...
    # N4 как в пробе: python/.temp/a/b, venv/r1/a/b, src/a/b → ключ bin
def test_interpreter_link_text_is_pinned(tmp_path, deps, text): ...  # относит., 2 хопа, ключ
def test_home_text_is_pinned(tmp_path, deps, home): ...   # через ссылку, форма ключа, лишнее
def test_base_interpreter_as_link_refused(tmp_path, deps): ...
def test_python_copy_instead_of_link_refused(tmp_path, deps, name): ...  # три имени uv
def test_other_python_names_in_venv_bin_refused(tmp_path, deps, name, form): ...
    # python3.13, PYTHON3, pythonw, Python; нативный и текст; чужой нативный бинарь колеса — да
def test_expected_entrypoints_package_rules(tmp_path, text, want): ...
    # package=false → ∅; без build-system → ∅; scripts → имена; gui-scripts не входят
def test_expected_entrypoints_refusals(tmp_path, text): ...  # dynamic, package="yes", "../x"
def test_expected_entrypoints_missing_file(tmp_path): ...
def test_transfer_check_passes_and_records_python_declared(runner, tmp_path): ...
    # argv: find, python -P, python -I, tool --help; env/cwd/network/timeout; sys.version
def test_direct_shebang_script_is_run(runner, tmp_path): ...   # --help код 1 → проблема
def test_trampoline_script_is_run(runner, tmp_path): ...
def test_help_outcomes_are_problems(runner, tmp_path, outcome, needle): ...  # срок, stdout, запуск
def test_launch_failure_is_problem_not_pass(runner, tmp_path): ...   # launched=False
def test_runtime_sys_path_outside_refused(runner, tmp_path): ...     # /Users/x/site
def test_runtime_sys_path_outside_checked_dirs_refused(runner, tmp_path, extra): ...
    # (ж): site-packages базового Python, чужого окружения (F13), bin, обход через ..
def test_runtime_sys_path_unresolvable_refused(runner, tmp_path, item): ...
def test_present_zip_on_sys_path_refused(runner, tmp_path): ...      # F5
def test_empty_sys_path_element_refused(runner, tmp_path, isolated): ...  # '' = cwd
def test_hostile_sys_path_element_is_a_problem(runner, tmp_path, item): ...  # NUL, длинное
def test_enable_user_site_refused(runner, tmp_path): ...
def test_unparsable_probe_refused(runner, tmp_path, stdout): ...
def test_find_failure_is_infrastructure_and_nothing_runs(runner, tmp_path): ...
def test_leftovers_stop_before_any_run(runner, tmp_path): ...
def test_transfer_check_refuses_build_home_leftovers(fake_layout): ...
    # S1-R9-N1: настоящий find через подменённый run_as; build_home/cache остаётся, ни одного
    # запуска кроме find; двойник «со скрытой очисткой build_home» этот тест не проходит
# tests/df/test_digest.py
def test_resolve_in_tree_resolves_every_component(tmp_path, path): ...   # ссылка uv на минор
def test_resolve_in_tree_root_and_dirs(tmp_path): ...
def test_resolve_in_tree_refusals(tmp_path, path): ...
def test_resolve_in_tree_cycle_refused(tmp_path): ...
def test_resolve_in_tree_refuses_noncanonical_root(tmp_path): ...
# tests/df/test_routes.py
def test_boundary_leaves_pass_the_trusted_inventory(monkeypatch, fl, leaf): ...
def test_boundary_leaves_list_every_problem(monkeypatch, fl, leaf): ...
def test_transfer_check_in_route_only_finds(monkeypatch, fl): ...
def test_twin_transfer_check_with_hidden_build_home_cleanup(monkeypatch, fl): ...
    # R33.F3: события маршрута verify сравниваются и по argv запусков внутри transfer_check —
    # токен run_as(df-build)+ один не отличает скрытое удаление от find
```

- [x] **Step 2–4:** FAIL → implement → PASS. Мутационная проверка правил F1–F5, F9 —
  каждое отключённое правило валит тест (`task-8-impl-report.md`).
- [x] **Step 5: Commit** `feat(df): execution-chain boundary, entrypoint inventory, transfer
  check`; `docs(df): Task 8 as built; boundary rules in spec 3.4, Task 9/12/13/14 items`;
  `fix(df): pyvenv.cfg split as CPython does; (ж) probe with -P` и `docs(df): …` к нему;
  `fix(df): Task 8 review round 1 — startup-file table, lone CR, hostile sys.path` и
  `docs(df): …` к нему; `fix(df): Task 8 review round 2 — allowlist getpath's search_up
  chain` и `docs(df): …` к нему; `fix(df): Task 8 review round 3 — pin the start points of
  getpath's walks` и `docs(df): …` к нему; `fix(df): Task 8 round 3 minor — python* names
  in venv bin`.

### Task 8a: Административная среда, команда `darkfactory`, bootstrap

Как построено (22.10): предполётная проверка `task-8a-preflight.md`, все строки Ruling
F1–F16 приняты как обязательные (R44; где Ruling меняет текст брифа — действует Ruling).
Решения контроллера по части 1: листья 8a (`install_admin_python`, `install_admin`,
`link_command`) связаны в `acceptance_calls` уже в 8a, а не в Task 9;
`install_admin_python` принимает `platform: PlatformName`; `acceptance_calls` проверяет
`python_key == build.python_key(profile.python, platform)` (иначе `AssertionError`, F1 —
один источник ключа); половина `test_usr_local_link_only_when_trusted` про напечатанный
путь — в Task 9; тест хэша настоящим `uv` в CI пропускается, пока CI не закрепит `uv`
0.11.23 (Task 13/14). Замеры — uv 0.11.23, macOS arm64; Linux (вариант сборки, хэш,
перенос) не замерялся — живая приёмка Task 13.

**Files:**
- Create: `df/admin.py`, `df/bootstrap.py`, `df/launcher_entry.py` (вход команды; модуль
  импортирует только stdlib, `df.paths` — лишь под `TYPE_CHECKING`)
- Modify: `df/paths.py` (`Layout.admin_home`, F6); `df/routes.py` (листья 8a в
  `acceptance_calls`, `_admin_digest` → `admin.admin_digest`, F12; проверка ключа Python);
  `df/install_table.py` (текст `REQUIRES["install_admin_python"]`, перерисован в спеке)
- Test: `tests/df/test_admin.py`, `tests/df/test_launcher_entry.py`; Modify:
  `tests/df/test_leaves.py` (`PRIMITIVES`: настоящие листья 8a, F5), `tests/df/test_routes.py`,
  `tests/df/test_scenarios.py` (настоящий `admin_ready`, фикстурный `uv`),
  `tests/df/test_journal.py` (матрица поколения — общие константы, FM5),
  `tests/df/test_accept.py` (страж `st_uid` допускает шов входа `owner_uid`)

**Interfaces:**
- Consumes: `fetch_reference` (Task 7, `df/inputs.py`: ключ `layout`, `dest` — прямой
  потомок `layout.verify_dir`); `tree_digest`, `check_links`, `open_regular` (Task 3);
  `trusted_path_problems`, `approved_cli_problems`, `run_command` (Task 5); `write_atomic`,
  `Layout` (Task 1); `build.python_key`, `COMMAND_TIMEOUT` (Task 6); `UV_PINNED_VERSION`
  (Task 7a). Вход команды и bootstrap вызывают `df.cli.main` (Task 9) по имени; тесты этой
  задачи подкладывают заглушки `df/cli.py` и `df/state.py` во временный административный
  каталог (F4).
- Produces:
  - `admin_python_path(layout: Layout, python: str, platform: PlatformName) -> Path` =
    `admin_python/<build.python_key(python, platform)>/bin/python<X.Y>` — ключ тот же, что
    у сборки релиза (F1).
  - `admin_digest(layout, release_id) -> str` = `tree_digest(admin_dir(id),
    exclude=frozenset())` — одно определение для отметки `admin` и для `admin_ready` (F12).
  - `install_admin_python(layout, python: str, *, uv: Path, platform: PlatformName) -> Path`
    — протокол `install_admin_python` (rev 8, r9), лист маршрута `admin` (условие — нет
    `admin-python/cpython-…`; остаток `.staging` удаляет соседний шаг маршрута
    `remove_own_tree` — до установки, если его оставила прерванная, и после неё: `uv`
    оставляет там служебные файлы, rev 8 r10). Предусловия (иначе `StepFailed("admin")`,
    до запуска `uv`): Python ≥ 3.12 (F11, PEP 695 в `df/`); нет ни
    `admin_python/.staging`, ни цели; `admin-home` (`Layout.admin_home`) — создаётся root
    `0700` (`fchmod`), существующий обязан быть каталогом root ровно `0700` (F6: `uv`
    создаёт `.lock` с `0666`); `uv --version` ∈ `UV_ADMIN_CONFIRMED =
    {UV_PINNED_VERSION}` (F8), иначе «uv <ver>: сверка загрузки Python не подтверждена;
    перенос Python не подтверждён». Root запускает `[uv, "python", "install", "--no-bin",
    build.python_key(python, platform)]` (F1) под `umask 022` (сохраняется и
    восстанавливается, F7), `timeout=COMMAND_TIMEOUT`, с окружением ровно
    `{UV_PYTHON_INSTALL_DIR: admin_python/.staging, UV_NO_CONFIG: 1, UV_PYTHON_DOWNLOADS:
    automatic, HOME: install_dir/admin-home, UV_CACHE_DIR: install_dir/admin-home/cache,
    PATH: /usr/bin:/bin}`, `fault_point("created")`; проверка всего дерева
    `.staging/cpython-…` (F7/F8: владелец root; только файлы, каталоги и ссылки; нет записи
    группы и всех у не-ссылок; каждая ссылка относительная и разрешается внутри дерева,
    `check_links` с логическим корнем — итоговым путём); `sync_tree(.staging/cpython-…)`;
    `os.rename` в `admin_python/cpython-…`, `fault_point("renamed")`;
    `sync_dir(admin_python)`; `trusted_path_problems` по результату. `uv` — утверждённый
    CLI. Эффекты: `created` → `sync_tree(py)` → `renamed` → `sync_dir(admin_python)`.
    Предпосылки §3.9 (Step 1): `uv` сверяет контрольную сумму загрузки, и перенесённый
    Python отвечает `sys.prefix` = новому пути без символических ссылок в `.staging`;
    текстовые упоминания `.staging` в `_sysconfigdata` и `LC_ID_DYLIB` `libpython` при
    исполнении не читаются (замер и ссылка на исходники — в docstring).
  - `install_admin(layout, release_id: str, *, devtools_ref: Path) -> None` — протокол
    `install_admin`, лист маршрута `admin` (rev 8, r9: Python, удаление прежнего дерева и
    отметка — соседние шаги маршрута, не этой функции). Предусловие: `admin_dir(id)` нет,
    иначе `StepFailed`, дерево не тронуто. `devtools_ref/df/` — настоящий каталог;
    `__pycache__` и `*.pyc` не копируются (ревью, n-5);
    копируются только каталоги и обычные файлы (`O_NOFOLLOW`), ссылка, FIFO и иной тип —
    `StepFailed` (F13); права ставит `fchmod`: каталоги и файлы с битом `x` — `0755`, иные —
    `0644` (не `umask`). Копия — в `admin_dir(id)/df/` (root), `fault_point("created")`;
    `sync_tree(admin_dir(id))`, `sync_dir(admin)`.
    Эффекты: `created` → `created` → `sync_tree(admin)` → `sync_dir(admin)` (копия частичная,
    затем полна).
    Отметку `admin` с `admin_digest = tree_digest(admin_dir(id))` пишет шаг маршрута
    `write_mark`; её `admin_python` — `Profile.python` прогона — та же версия, что ставит
    соседний шаг маршрута (rev 8, r11). Источник — только доверенный чекаут Task 7, не дерево
    релиза. Профиль без `devtools` в `refs` — `StepFailed("admin", "профиль без devtools: нет
    административного кода")` в `acceptance_calls`.
  - `admin_ready(layout: Layout, release_id: str, *, expected_admin_digest: str,
    expected_python: str) -> bool` — **единственное определение готовности
    административной среды** (rev 8, r10): `admin_dir(id)` есть и `admin_digest(layout,
    id)` равен `expected_admin_digest` (rev 8, r5: значение, а не чтение реестра; Task 7,
    R33.F25); Python `admin_python_path(layout, expected_python, detect_platform())` — по
    `lstat` обычный файл, и `trusted_path_problems` для него пуст; `admin_code_problems(layout,
    id)` пуст (ревью, I-1: `trusted_path_problems(admin_dir)` и по каждому объекту — владелец
    root, нет записи группы и всех, setuid/setgid, ACL, ссылок и байт-кода, n-5);
    `admin_python_problems(layout, python, platform)` пуст (M-4: всё дерево
    `admin-python/<ключ>` по правилу установки — замер uv 0.11.23: 9 относительных ссылок-
    псевдонимов внутри дерева из 1804 объектов); `chain_link_problems` — ни одной ссылки на
    пути к `admin/<id>` и к дереву Python (M-5: ограничение v1, вход их тоже отвергает). Каталога нет или
    `TreeError` — `False`; иная `OSError` — `StepFailed("facts")` (F12). Оба ожидаемых
    значения передаются из одного источника (rev 8, r11): `mark.admin_digest`/`mark.admin_python`
    до регистрации (Task 7), запись реестра — `admin_digest`/`admin_python` — после
    (`read_facts`, `activate` под блокировкой, читатели Task 12 из `snap.entry`). Профиль и
    манифест предикат и вызывающие не читают; `python_declared` релиза — не источник.
    Долговечность содержимого Python предикат не читает: её обеспечивает синхронизация
    дерева до `rename` (протокол `install_admin_python`); в модели это отдельный признак, а
    не факт.
  - `launcher_text(layout, python: str, platform: PlatformName) -> str` —
    `#!/bin/sh\nexec <admin python> -I -B -S <entry> "$@"\n`, пути `Layout` через
    `shlex.quote` (F2, F14; бывший `LAUNCHER_TEXT`: текст зависит от раскладки и Python
    релиза, константой быть не может). `entry_text(layout) -> str` (`df/launcher_entry.py`)
    — собственный текст модуля входа, в котором единственная строка `_ROOT = "/"` заменена
    корнем раскладки; не ровно одна такая строка — `AssertionError`.
  - Маршрут `launcher` (rev 8, r9: отдельной составной функции нет): `write_atomic` входа
    (`entry_text(layout)`, `0644`), `write_atomic` команды (`launcher_text(...)`, `0755`) —
    своя синхронизация `bin_dir` у каждого; затем, если `/usr/local/bin` доверен (условие
    `local_bin=trusted`: `local_bin_trusted(layout)` —
    `trusted_path_problems(layout.local_bin)` пуст, Task 7 R33.F23), — шаг
    `link_command`; иначе шаг пропущен, и установка сообщает полный путь команды (Task 9).
  - `link_command(layout) -> None` — протокол `link_command`: временная ссылка,
    `rename` поверх `/usr/local/bin/darkfactory` через дескриптор `local_bin`,
    `fault_point("renamed", …)`, `sync_dir(/usr/local/bin)`. Ветвления и проверки политики
    внутри нет: решение о ссылке — условие маршрута. Эффекты: `renamed` → `sync_dir(local_bin)`.
    Ссылку и файл с именем `df` не создаёт никогда.
  - `launcher_ready(layout, *, python: str) -> bool` (F2): вход и команда — обычные файлы
    root, `0644` и `0755`, с текстом ровно `entry_text(layout)` и `launcher_text(layout,
    python, detect_platform())`, оба проходят `trusted_path_problems` (ревью, M-3); если
    `local_bin_trusted(layout)`, то и
    `local_bin/darkfactory` — ссылка с текстом `str(layout.launcher)` (иначе сбой между
    записью команды и `link_command` оставил бы `L` истинным без ссылки). Нет или другое —
    `False`; `ELOOP` — `False`; иная `OSError` — `StepFailed("facts")`. Оба предиката
    `read_facts` получает параметрами (Task 7, R33.F5); связывает их Task 9
    (`launcher_ready=lambda: launcher_ready(layout, python=<версия Python записи>)`).
  - `RAW_FIELDS = ("generation", "current_id", "releases_text", "install_text",
    "models_text", "pending_present")` — части, которые вход читает до импорта `df`.
    Тест этой задачи сверяет их с заглушкой `RawSnapshot` и со списком полей Task 9;
    сверка с настоящим `df.state.RawSnapshot` — тест Task 9 (F4).
    Типизированные объекты вход строит после проверки digest и импорта `df`.
  - Входной файл `darkfactory-entry.py` (нормативно, §3.9). Части `df`, нужные до импорта,
    скопированы в модуль на stdlib, и тест гоняет каждую копию и её оригинал на одних
    входах, враждебные включительно: `tree_digest` без исключений и `check_names`,
    разбор поколения и `read_consistent`, открытие блокировки (`writer_lock` без создания
    каталогов), `settle_on_acquire`/`acquire_paths`/`_pending_id`, чтение файлов
    (`read_optional`, байты) и правила `parse_releases`. Владелец читается одним швом
    `owner_uid`. Сам файл запускается только как `-I -B -S` (иначе отказ):
    0. режим: подкоманда ∈ {`init`, `install`, `models set`} → `writer` (требует root;
       открытие `layout.lock` с проверками Task 4 — каталоги по пути доверены, `df` и
       `install` без перехода по ссылке, файл обычный, свой, одна жёсткая ссылка, ровно
       `0600`, без блокировки на FIFO; отсутствующий каталог — отказ, вход каталогов не
       создаёт; `flock(LOCK_EX|LOCK_NB)`, занято — выход 2, дескриптор до конца
       процесса; **сразу затем** — своя stdlib-копия `settle_on_acquire` (rev 8 r6) с
       `acquire_paths(paths, pending_id)` входа: она равна `accept.acquire_paths` при
       доверенном `/usr/local/bin`, а иначе шире ровно на `local_bin` — вход синхронизирует
       его всегда, когда это настоящий каталог, и не копирует политику доверенных путей
       (F3)); иначе → `reader` (блокировку **не открывает**, с root и без; не закрепляет);
    1. полный снимок: `writer` — одно чтение под блокировкой (нечётное поколение и
       `pending.toml` — не переходное состояние для него); `reader` — `g1`, чтение, `g2` по
       правилам `read_consistent` (Task 4): нечётное `g1` или `pending_present` —
       **переходное состояние**; изменившееся поколение — повтор, после трёх попыток выход 2.
       Чтение: `os.readlink(current)` один раз → id (`releases/<id>`, иное — выход 2),
       байты `releases.toml`, `install.toml`, `models.toml`, наличие `pending.toml`. В
       переходном состоянии при нечётном `g1` вход читает только `current` и
       `releases.toml` — чтобы найти код (§3.3);
    2. по прочитанному: `current` есть (иначе выход 2 с указанием на bootstrap), запись id
       есть и `allowed`, иначе выход 2. Отказ из-за `pending` или переходного состояния — не
       здесь, а в команде (Task 9), чтобы `doctor` его показал;
    3. `admin_tree_problems(paths, id)` пуст (ревью, I-1: каталоги цепочки `root … admin/<id>`
       — настоящие, root, без записи группы и всех; объекты `admin/<id>` — каталоги и обычные
       файлы root без записи группы и всех, ссылок и байт-кода нет; ACL не читается — см.
       §3.9) и `interpreter_problems(paths, sys.executable)` пуст (M-4: интерпретатор в
       `admin-python/`, путь — настоящие каталоги root без записи группы и всех, файл —
       обычный, root, без записи группы и всех), затем
       `tree_digest(admin_dir(id))` = `admin_digest`, иначе выход 2 с указанием на
       bootstrap. У читателя отказ при изменившемся поколении — «состояние меняется» (n-2);
    4. `df` ещё не загружен (иначе выход 2); `sys.path.insert(0, str(admin_dir(id)))` —
       пути stdlib не трогаются;
    5. импорт `df.state` и `df.cli` (`df.cli` обязан быть из `admin_dir(id)/df`; любая
       ошибка импорта, в том числе `SystemExit`, — выход 2 с указанием на bootstrap, M-2,
       n-7);
       `read = Transitional(g)` либо `Stable(RawSnapshot(generation=g, **parts), g)`;
       `sys.exit(main(argv, mode=mode, read=read, lock_fd=fd))`. Дальше никто снимок не
       перечитывает. `mode_of(argv)` и `WRITER_COMMANDS` — публичные: `main` обязана
       определять подкоманду так же и отказывать писателю в режиме `reader` (Task 9).
  - `df/bootstrap.py` (F10) — исполняемый как скрипт; разбирается Python 3.9 (системный
    `python3` macOS), чтобы сказать, что нужно: Python < 3.12 — выход 2 «нужен Python ≥
    3.12»; запуск не как `-I -B -S` — выход 2; принимаются только `init` и `install`, иное
    — выход 2. Затем `sys.path.insert(0, <каталог над df/ по realpath(__file__)>)` и
    `df.cli.main(argv, mode="bootstrap", read=None, lock_fd=None)`; `main` в этом режиме
    сама берёт `writer_lock` и читает снимок. Правило «активный `current` и исправная
    команда — отказ „используйте `darkfactory`“» — в `main` (Task 9), после снимка;
    `install --recover` нет.

- [x] **Step 1: Проверка предпосылки `uv`** (как построено, F8/F9). uv 0.11.23 сверяет
  SHA-256 загрузки (исходники `crates/uv-python/src/downloads.rs`, ссылка и замер — в
  docstring `install_admin_python`). `test_uv_refuses_corrupt_download` — локальное
  зеркало `UV_PYTHON_INSTALL_MIRROR` на 127.0.0.1 с испорченным архивом, без маркера и без
  внешней сети; пропускается, если `uv` на `PATH` не 0.11.23 (в CI — до закрепления
  версии, Task 13/14). `test_admin_python_relocation_precondition` — настоящая загрузка,
  opt-in `DF_NETWORK_TESTS=1`, пока Task 13 не заведёт маркер `network`: после `rename`
  `sys.prefix == sys.base_prefix == admin_python/cpython-…`, ни одна ссылка не указывает в
  `.staging`. Версия `uv` вне `UV_ADMIN_CONFIRMED` — отказ до запуска.
- [x] **Step 2: Failing tests** (как построено; F4: `test_doctor_transitional_via_public_entry`
  — в Task 12, сверка `RAW_FIELDS` с настоящим `df.state`, «доходит до committed без
  ссылки» и «`main` ничего не перечитывает» — в Task 9)

```python
# tests/df/test_admin.py
def test_admin_code_comes_from_reference_not_release(fl): ...   # подмена df/ в релизе не попадает в admin
def test_install_admin_python_argv_and_env_exact(fl): ...       # --no-bin, точный ключ, ровно 6 переменных
def test_umask_is_set_not_inherited(fl): ...
def test_admin_tree_check_refuses(fl, spoil): ...               # g+w, o+w, abs, escaping, dangling, fifo
def test_unconfirmed_uv_refused(fl): ...
def test_python_below_floor_refused_before_uv(fl, python): ...
def test_admin_home_refused_unless_root_0700(fl, spoil): ...
def test_admin_tree_synced_before_mark(fl): ...    # маршрут admin: sync_tree → sync_dir(admin) → write_mark
def test_install_admin_is_leaf(fl): ...
    # S1-R9-N1: события install_admin == PROTOCOLS["install_admin"]: ни write_mark, ни
    # remove_own_tree, ни запуска uv; admin_dir(id) уже есть → StepFailed, дерево не тронуто
@pytest.mark.parametrize("stop", ["created", "renamed"])
def test_install_admin_python_staging_then_rename(fl, stop): ...
    # настоящий лист в настоящем маршруте, сбой в stop, повтор маршрута admin: created —
    # remove_own_tree(.staging), затем установка; renamed — условие py=absent ложно
def test_install_admin_copies_only_files_and_dirs(fl, kind): ...   # link, dir-link, fifo
def test_install_admin_modes_by_fchmod(fl, umask): ...
def test_admin_ready_uses_passed_digest_only(fl): ...
    # audit hook: admin_ready не открывает releases.toml и отметки; другой digest → False
def test_admin_ready_digest_excludes_nothing(fl): ...
def test_launcher_ready(fl, spoil): ...     # no-link, чужой текст, права, владелец
def test_launcher_never_named_df(fl): ...
@pytest.mark.parametrize("trusted", [False, True])
def test_usr_local_link_only_when_trusted(fl, trusted): ...
    # условие маршрута ложно → link_command не вызван, L истинен без ссылки;
    # link_command сам trusted_path_problems не вызывает (S1-R9-N1)
def test_uv_refuses_corrupt_download(tmp_path): ...             # локальное зеркало, без маркера
def test_admin_python_relocation_precondition(tmp_path): ...    # opt-in DF_NETWORK_TESTS
# tests/df/test_leaves.py — PRIMITIVES: настоящие install_admin_python, install_admin,
# link_command в test_primitive_protocols_match_table (7-D2 M4)
# tests/df/test_launcher_entry.py
def test_entry_imports_only_stdlib_at_runtime(): ...
def test_entry_paths_match_layout(tmp_path): ...
def test_entry_raw_fields_match_df_state(): ...   # заглушка RawSnapshot и список полей Task 9
def test_entry_digest_matches_df_digest(tmp_path, name): ...
    # ссылки, FIFO, близнецы NFC и регистра, длинное имя, перевод строки, нечитаемый файл
def test_entry_digest_golden_vector(tmp_path): ...       # общий вектор test_digest.py
def test_entry_check_names_matches_df(names): ...         # NUL, /, \n, не UTF-8, 300 байт
def test_entry_generation_parsing_matches_journal(tmp_path, content): ...
    # общая матрица test_journal.py + NUL, 64/65 байт, ссылка, каталог, FIFO
def test_entry_read_consistent_matches_journal(tmp_path, name, start, attempts): ...
def test_entry_lock_open_matches_writer_lock(tmp_path, monkeypatch, case): ...
    # ссылка, FIFO, каталог, 0644, две жёсткие ссылки, чужой владелец файла и каталога,
    # g+w/o+w каталогов, ссылка вместо df/ и install/
def test_entry_lock_creates_no_directory(tmp_path, missing): ...
def test_entry_pending_id_matches_accept(tmp_path, case): ...
def test_entry_acquire_paths_copy_matches(tmp_path, monkeypatch, pending_id, trusted, root_owned): ...
    # F3: равенство при доверенном local_bin, иначе ровно на local_bin больше
def test_entry_settle_syncs_like_settle_on_acquire(tmp_path, monkeypatch): ...
def test_entry_releases_parse_matches_config(case): ...
def test_entry_modes(tmp_path, monkeypatch, rec, as_root): ...
    # models set → writer (EX: даже SH занят); doctor от root → reader без open(lock); занято → код 2
def test_entry_writer_settles_before_snapshot(tmp_path, monkeypatch, rec, as_root): ...
    # writer: flock → sync по acquire_paths → первое чтение current; reader: ни одного sync (S1-R6-N1)
def test_entry_resolves_current_once(tmp_path, monkeypatch, rec, as_root): ...
def test_entry_refuses_revoked_and_pending(tmp_path, rec, as_root): ...
    # отозван, не зарегистрирован, нет current, чужая цель → код 2; pending → main
def test_entry_reader_generation(tmp_path, monkeypatch, rec): ...
    # поколение меняется → три попытки, код 2; нечётное → Transitional(g) с найденным кодом;
    # pending_present при чётном → Transitional
def test_entry_passes_full_snapshot(tmp_path, rec, as_root): ...
    # пауза после чтения; установка меняет current и install.toml; main получает прежний raw
def test_entry_reader_transitional_reaches_main(tmp_path): ...   # подпроцесс, нечётное поколение
def test_admin_digest_mismatch_blocks_entry(tmp_path): ...       # правка admin/df/cli.py → код 2 до импорта
def test_entry_refuses_without_isolation_flags(tmp_path): ...
def test_entry_writes_no_bytecode(tmp_path): ...                 # два запуска: digest admin_dir не меняется
def test_entry_ignores_foreign_df_and_pth(tmp_path, foreign_python): ...
    # чужой df в site-packages и .pth с import: не исполняются (-I -S); без -S .pth исполняется
def test_bootstrap_allows_only_init_install(tmp_path, argv): ...
def test_bootstrap_requires_isolation_flags(tmp_path, flags): ...
def test_bootstrap_root_from_own_path_through_link(tmp_path): ...
def test_bootstrap_under_system_python_says_what_is_needed(tmp_path): ...
```

- [x] **Step 3–4:** FAIL → implement → PASS. Мутационная проверка: каждое отключённое
  правило (F1, F6–F8, F11–F13, F2; на стороне входа — режим, root, блокировка, закрепление,
  `acquire_paths`, `_pending_id`, поколение, `read_consistent`, digest, запись `allowed`,
  `current`, импорт, флаги; bootstrap) валит тест (`task-8a-admin-report.md`,
  `task-8a-entry-report.md`).
- [x] **Step 5: Commit** `feat(df): administrative environment (Task 8a part 1)`;
  `feat(df): darkfactory entry and bootstrap (Task 8a part 2)`; `docs(df): Task 8a as
  built; entry and admin rules in spec 3.9, Task 9/12/13/14 items`.

### Task 9: Транзакция установки, регистрация, активация, команды `init` / `install`

Как построено (24.10): предполётная проверка `task-9-preflight.md`, строки Ruling I1–I8,
I10, M1–M14, M16–M20, N1–N4 приняты как обязательные (R46; где Ruling меняет текст брифа —
действует Ruling). Исполнено четырьмя частями: 9-A (релиз и настоящие вызовы маршрутов),
9-B (снимок, CLI, восстановление), 9-C (модель сбоя ОС, `fake_world`, именованные окна;
найден и исправлен дефект долговечности режима созданных каталогов — механизм
`fsutil.make_dir`/`create_file`/`exact_mode`, спека §3.3 «Создаваемые объекты»), 9-D1 (I9 —
решение владельца 23.10, R51–R53: охранники G1–G4 и исправленная инструкция, спека §3.8
«Повреждение под незавершённой установкой»), 9-D2 (матрица сбоев и семейства двойников,
бюджет R49 → R55), 9-D3 (этот текст). Вариант (б) I9 (возврат `current` на прежний релиз)
не реализован. Замеры — macOS arm64; Linux-ветки (наследование `S_ISGID`, учётка через
`main`, матрица на настоящей ФС Linux) — Task 13.

**Files:**
- Create: `df/release.py`, `df/state.py`, `df/cli.py`, `df/install.py`
- Modify: `df/accept.py` (`AcceptMark.python_declared`, `entry_intact`, публичная
  `current_is`, журнал M4/M5 `drive`), `df/routes.py` (`LATER_LEAVES = {}`,
  `removal_calls`, `_root_kind(build_uid=None)`, `RouteContext.python_declared`),
  `df/admin.py` (`entry_source`, `release_entry_text`, `launcher_ready(…, python)`,
  `LINK_TMP`), `df/launcher_entry.py` (`entry_text(layout, source=None)`, файл блокировки
  входа — в итоговом режиме), `df/inputs.py` (публичная `lock_digest`), `df/journal.py`
  (публичная `read_generation`; цепочка блокировки через `fsutil.make_dir`, файлы — через
  `create_file`), `df/fsutil.py` (`exact_mode`, `make_dir`, `create_file`, `repairable`),
  `df/bootstrap.py` (снят `# type: ignore`), `pyproject.toml` (маркер `slow`, `addopts =
  "-m 'not slow'"`)
- Test (new): `tests/df/test_release.py`, `tests/df/test_state.py`,
  `tests/df/test_install_steps.py`, `tests/df/test_install_i9.py`,
  `tests/df/test_crash_model.py`, `tests/df/test_crash_windows.py`,
  `tests/df/test_crash_create.py`, `tests/df/test_crash_matrix.py`; помощники
  `tests/df/route_world.py` (из `test_routes.py`, 7-final M3), `tests/df/install_world.py`
  (`World` и заглушки установки), `tests/df/crash_model.py` (модель сбоя ОС),
  `tests/df/crash_matrix.py` (движок матрицы)
- Modify (tests): `tests/df/conftest.py` (`fake_world`, `make_fake_layout`,
  `make_fake_world`; `umask 022` autouse, M18), `tests/df/test_routes.py`,
  `tests/df/test_leaves.py`, `tests/df/test_scenarios.py`, `tests/df/test_admin.py`,
  `tests/df/test_accept.py`, `tests/df/test_drive.py`, `tests/df/test_launcher_entry.py`
  (настоящий `df.state`), `tests/df/test_journal.py`, `tests/df/test_fsutil.py` (страж
  «каждое изменение ФС и каждый запуск процесса в `df/` учтены»)

**Interfaces:**
- Consumes: всё из Tasks 1–8; `install_admin_python`, `install_admin`, `admin_ready`,
  `link_command`, `launcher_text`, `entry_text`, `launcher_ready` (Task 8a; бывший
  `LAUNCHER_TEXT` — функция `launcher_text(layout, python, platform)`, F2). Листья
  `install_admin_python`, `install_admin`, `link_command` уже связаны в
  `acceptance_calls` в Task 8a (решение контроллера); `refs` без `devtools` — уже
  `StepFailed("admin", …)` там же (F13).
- Produces:
  - `next_release_id(layout: Layout, manifest_sha: str, platform: PlatformName) -> str` —
    наименьший `n ≥ 1`, для которого `<sha12>-<platform>-<n>` нет ни в `releases.toml`
    (любой статус), ни среди каталогов `releases/`, `staging/`, `admin/` (точное имя), ни
    в `staging/.quarantine/` (имя равно id или начинается с `<id>.`), ни среди отметок
    `accept/<id>.toml` (M1: иначе id 1 занят при наличии id 10). Префикс —
    `id_prefix(manifest_sha, platform) = f"{manifest_sha[:12]}-{platform}-"` (тот же
    префикс сверяет «согласован» I9).
  - `ReleaseRecord` (поля `release.json`, §3.5):
    - `schema = "df/release/v1"`, `id`, `digest`, `admin_digest`, `admin_python` (оба — из
      отметки `admin`, rev 8 r11);
    - `manifest_repo`, `manifest_sha`, `profile`, `platform`, `created_at`;
    - `repos: [{name, url, sha, lock_sha256, groups}]`;
    - `python_declared: {repo: str}`, `uv_version_declared` (из отметки `admin`; записан
      шагом `build`, R29, R33.F11). `python_declared` — `ctx.python_declared`
      (`RouteContext`, Task 8): `sys.version` каждого окружения, принятый листом
      `transfer_check`;
    - решено (I4, R40/F7): `ctx.python_declared` и `ctx.installed` живут только в
      процессе, а после отметки `verified` новый процесс `verify` не повторяет. Поэтому
      `AcceptMark.python_declared: str | None` — канонический JSON `{repo: version}`
      (`dump_declared`/`parse_declared`), обязателен с `verified`, у `admin` переносится
      без изменений (`write_mark` сверяет перенос, как `uv_version_declared`); без ответов
      `transfer_check` в этом процессе отметка `verified` — `StepFailed("mark")`. Отметка
      без поля для `verified`/`admin` невалидна, то есть считается отсутствующей, и релиз
      пересобирается (fail-closed). Гарантий не меняет: значения заявленные;
    - `distributions_declared: {repo: [{name, version, source}]}`. `source` — поля
      `SourceId`; для registry `index: "not declared"`. Читается заново на `register`
      (`release_record(run)`): `installed_distributions(releases/<id>/venv/<repo>)` после
      проверки, что `release_digest` копии равен `mark.tree_digest`, — те же списки, что
      принял лист `distribution_problems`, и после повтора новым процессом. Читает
      вызывающий, не `write_release_json`. `lock_sha256` — `inputs.lock_digest` по
      доверенному чекауту (M12).

  - `write_release_json(release_dir: Path, rec: ReleaseRecord) -> None` — обёртка
    протокола `write_atomic` (`WRAPS`), `0644`. Эффекты: `write_atomic()`.
  - `register(layout: Layout, rec: ReleaseRecord) -> None` — `ReleaseEntry(status="allowed")`
    в `releases.toml`; обёртка протокола `write_atomic` (`WRAPS`). Эффекты: `write_atomic()`.
  - `activate(layout: Layout, release_id: str) -> None`. Предусловие: запись `allowed`,
    `tree_digest` = записи, `admin_ready(layout, id, expected_admin_digest=<запись>.admin_digest,
    expected_python=<запись>.admin_python)`
    истинен — `accept.entry_intact` с `admin.admin_ready`; запись читает сам `activate`
    через `registry_entry` под блокировкой (N4). Протокол `activate`: ссылка
    `current` — временная относительная `releases/<id>` под одним именем `CURRENT_TMP =
    ".current.tmp"` (остаток удаляется первым; каталог открыт `O_NOFOLLOW`), `rename`
    поверх `current`,
    `fault_point("renamed")`, `sync_dir(opt)`. Эффекты: `renamed` → `sync_dir(opt_df)`.
  - В `state.py`: `RawSnapshot(generation: int, current_id: str | None, releases_text: bytes
    | None, install_text: bytes | None, models_text: bytes | None, pending_present: bool)`
    (поля = `RAW_FIELDS`, Task 8a; реестра нет при первой установке bootstrap, M2);
    `Stable`, `Transitional` — реэкспорт из Task 4; `ReadResult = Stable[RawSnapshot] |
    Transitional`; `read_raw(layout) -> RawSnapshot` — одно чтение всех частей
    (поколение — публичная `journal.read_generation`; `current` — один `readlink`, цель —
    относительная `releases/<id>` с допустимым id; файлы — через `config.read_optional`,
    не-UTF-8 — отказ; любая ошибка — `StepFailed("snapshot")`); `Mode = Literal["writer",
    "reader", "bootstrap"]`; `Snapshot(release_id: str | None, entry: ReleaseEntry | None,
    install: InstallConfig | None, models_text: str | None, pending_present: bool,
    generation: int)` — **без** поля `pending` (I2); `parse_snapshot(raw: RawSnapshot,
    layout: Layout, *, mode: Mode) -> Snapshot` — снимок `reader` с `pending_present`
    отвергается. Текст `pending.toml` писатель читает сам ровно один раз сразу после снимка:
    `install.read_pending(layout) -> Pending | None` (I2, M8: `load_pending`, допустимый
    id, `parse_install(install_text)`, чей digest и заново отрисованный текст обязаны
    совпасть с файлом, иначе `StepFailed("pending")`); расхождение с `pending_present` —
    `StepFailed("snapshot")`. Команды работают только со снимком; части снимка после `main`
    читают только чтения автомата под блокировкой и собственные записи писателя — перечень
    (белый список `test_main_rereads_nothing`, R46 I2 и его расширения D5 9-B и 9-D1):
    `accept.read_facts`, `accept.settle`, `accept.settle_on_acquire`, `routes.route_guard`,
    `routes._refuse_registered` (F13), `release.register`, `release.activate`,
    `release.next_release_id`, `install._registered_python`, `install.read_pending`
    (ровно один раз), `install._refuse_damaged_pending` (G2), `install._backstop` (G4), а
    `fsutil.write_atomic` и `remove_durably` — как записи (они проверяют свою цель);
    bootstrap добавляет `state.read_raw`. Читатель не читает ни одной части (§3.3).
  - `recover_pending(layout: Layout, snap: Snapshot, requested: InstallConfig, pending:
    Pending | None) -> Literal["none", "resume", "rollback"]` — §3.3 «Незавершённая
    установка», под `writer_lock` первым делом; `pending` — результат `read_pending` (I2).
    `snap` прочитан **после** `settle_on_acquire` (вход или bootstrap), поэтому `reg`,
    `current` и дерево, на которых основано решение, уже закреплены (rev 8 r6); своего
    закрепления функция не делает. Новый `pending.toml` (`PENDING_MODE = 0o644`) пишется
    после `generation_begin`; `generation_end` — тот, кто удалил `pending.toml` (`run_install`
    после `drive` или ветка отката, M6):
    - `pending` нет → `none`;
    - `current` = `pending.release_id` → `resume` с тем же id, независимо от `requested`
      (иная конфигурация — `warn` в журнале, I8);
    - `current` = `previous_current` и `config_digest(requested) == install_digest` →
      `resume`; если релиз `pending.toml` зарегистрирован и не `intact` —
      `StepFailed("damaged", <текст G2>)` здесь же, до шагов 1–3 и сети (G2, I9);
    - `current` = `previous_current`, другая конфигурация → `rollback(layout, pending)`
      выполняется здесь, затем `generation_end`, и только потом возвращается `"rollback"`
      (M11). Порядок `rollback` (I7, I5): `policy_problems` для `opt`, `releases`,
      `staging`, `admin` (`ROLLBACK_PARENTS`; отсутствующий — по ближайшему существующему
      предку) пусты, иначе `StepFailed("trusted-paths")` до любого удаления;
      `build_account` (запись `df-build` в любом состоянии или `None`); `rollback_calls`;
      `generation_begin`; журнал `rollback start`; маршрут `rollback` через `run_route`
      (Task 7) с `python_key=None` и `build_uid` записи; журнал `done`; `generation_end`.
      Каждый вызов обёрнут: `TreeError`/`OSError` → `StepFailed("rollback", "<маршрут>[<i>]
      <вызов> <путь>: …")` по `(route, index)` из `ROUTES` (M7); отказ журналируется
      `failed`. Откат не требует сети, профиля и учётки; очиститель `cleanup_tree` от
      `df-build` — только при пустом `verify_build_user`, иначе `StepFailed("rollback")` до
      запуска очистителя (9-A I-1); шаги маршрута до него (шаг 0 `cleanup` —
      `remove_own_tree` корня root `releases/<id>`) могли уже выполниться, повтор
      идемпотентно повторяет их (docstring `rollback_calls`);
    - иной `current` → `StepFailed("pending", "current = …: ни <id>, ни прежний <id> —
      решает человек")` (M10; без ссылки на `doctor`).
  - `install_steps(layout: Layout, cfg: InstallConfig, prepared: Prepared) -> list[Step]` —
    только подготовительные шаги 1–3 для `run_steps`; `Prepared(profile: Profile | None)`
    несёт профиль из шага 1 (I1); их `check()` — собственные факты:

    | # | Шаг | `check()` | `do()` |
    |---|---|---|---|
    | 1 | `manifest` | `fetch_manifest` (только HTTPS-чтение) и `parse_profile` в `prepared`; `ManifestError`/`OSError` — `StepFailed("manifest", <проблемы>)` из самого `check()` | — |
    | 2 | `trusted-paths` | сначала `approved_cli_problems`; затем закрепление найденных каталогов набора и их предков (`_settle_prep_dirs`); для каждого каталога `prep_dirs` — `policy_problems` (отсутствующий — по ближайшему существующему предку), не каталог — проблема, точный режим у `EXACT_MODES`; любая проблема — `StepFailed("trusted-paths", <пути и причины>)`; ложь — только отсутствие или исправимый свой каталог | создать недостающие (`fsutil.make_dir`, рождён в итоговом режиме, закреплён сам и в родителе; недостающие предки — `0755`) и расширить свой каталог root с режимом уже нужного или с унаследованным `S_ISGID` (`fsutil.repairable`) — для **любого** каталога набора; чужой владелец, запись группы и всех, ACL, расхождение режима у точного — отказ; режим шире без записи группы и всех у неточного не исправляется и не отказывает; запись группы и всех не добавляется никогда |
    | 3 | `build-user` | `verify_build_user` пуст и только затем `whoami_as` = записи (M4: ни одного `run_as` при `creating`) | `create_build_user` |

    Набор шага 2 и режимы (`prep_dirs`, порядок создания; M3, 9-C): `opt`, `releases` —
    `0755`; `staging`, `quarantine` — `0711`; `admin`, `admin_python`, `bin_dir`, `etc`,
    `var` — `0755`; `marks_dir`, `verify_dir`, `admin_home` — `0700` (примитивы Task 7 их не
    создают, R33.F31; `admin_home` — каталог шага 2 с 9-C, `install_admin_python` проверяет
    его и создаёт через `fsutil.make_dir` (`0700`), если его нет). `EXACT_MODES = (staging, quarantine, marks_dir, verify_dir,
    admin_home)`. `install_dir` создаёт `writer_lock`. Решение M3 «`do()` не исправляет
    существующее» сужено (9-C RC-C5): не исправляет **чужой или более широкий**; созданное
    закрепляется синхронизацией самого каталога и родителя (не только родителя).

    Затем (порядок I6, проверяет тест-шпион): `fetch_references` (`inputs.fetch_reference`
    для каждого репо в `verify/<repo>`), `uv_config_refusal`, `retry_quarantine` (Task 5;
    остаток — `warn` в журнале), сборка `InstallRun` и **`drive`** (Task 7). Ни одного
    запуска `git`/`uv`/`tar` до успешного шага 2.

    `InstallRun` (I1) — один неизменяемый носитель: `layout`, `cfg` (из
    `pending.install_text`), `pending`, `profile`, `platform` (= `detect_platform()`),
    `plan: BuildPlan`, `limits`, `refs`, `journal`, `run_id` (= `pending.run_id`: возобновлённая
    установка журналирует под исходным `run_id`), `python_key` (= `build.python_key` профиля),
    `build_uid` (запись `df-build`), `ctx: RouteContext`; `release_id` = `pending.release_id`,
    `install_digest` = `config_digest(cfg)`. Конструктор отвергает `config_digest(cfg) !=
    pending.install_digest` и `dump_install(cfg) != pending.install_text`
    (`StepFailed("pending")`). Собирается после шагов 1–3, чекаутов и `build_uid`.

    Таблица действий строк §3.3 — `install_actions(run: InstallRun) -> Mapping[str,
    Callable[[Facts], None]]`. Каждое значение — `route_action(layout, id,
    <Row.action>, calls=install_calls(run), python_key=run.python_key,
    build_uid=run.build_uid)` (Task 7, R33.F14: проверка своей
    фазы, затем `run_route`; rev 8 r8); `install_calls(run) -> dict[str, Call]` =
    `acceptance_calls(...)` (Task 7, тот же `python_key`, свой `ctx: RouteContext`) и
    вызовы релиза (`write_release_json`, `register`, `activate`, `write_atomic` входа,
    команды и `install.toml`); `LATER_LEAVES = {}` (листья 7a, 8 и 8a уже в
    `acceptance_calls`), R33.F7 — реализации
    вызовов маршрутов:

    <!-- install-route-calls:begin -->
    | Действие | Вызовы по порядку (с условиями и объектами) |
    |---|---|
    | `build-dir` | `prepare_dir` releases/<id> |
    | `build` | `run_build` от `df-build` → `write_atomic` accept/<id>.toml: built |
    | `seal` | `reap_user_processes` df-build → `seal` releases/<id> |
    | `copy` | `accept_copy` releases/<id> → `write_atomic` accept/<id>.toml: copied |
    | `copy-partial` | `remove_own_tree` releases/<id> → `accept_copy` releases/<id> → `write_atomic` accept/<id>.toml: copied |
    | `verify` | если есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → `check_links` releases/<id> → `verify_chmod` releases/<id> → `verify_sources` releases/<id> → `distribution_problems` releases/<id> → `static_problems` releases/<id> → `cleanup_build_home` build_home/tmp → `cleanup_build_home` build_home/cache → `transfer_check` releases/<id> → `tree_digest` releases/<id> = отметке → `write_atomic` accept/<id>.toml: max(стадия, verified) |
    | `admin` | если есть `admin-python/.staging`: `remove_own_tree` admin-python/.staging → если нет `admin-python/cpython-…`: `install_admin_python` admin-python/cpython-… → если есть `admin-python/.staging`: `remove_own_tree` admin-python/.staging → если есть `admin/<id>`: `remove_own_tree` admin/<id> → `install_admin` admin/<id> → `write_atomic` accept/<id>.toml: admin |
    | `register` | `write_atomic` releases/<id>/release.json → `write_atomic` /etc/df/releases.toml |
    | `activate` | `activate` /opt/df/current |
    | `launcher` | `write_atomic` bin/darkfactory-entry.py → `write_atomic` bin/darkfactory → если `/usr/local/bin` проходит политику доверенных путей: `link_command` /usr/local/bin/darkfactory |
    | `commit-config` | `write_atomic` /etc/df/install.toml → `remove_durably` pending.toml |
    | `cleanup` | если корень `releases/<id>` у root: `remove_own_tree` releases/<id> → если корень у `df-build`: `cleanup_tree` releases/<id> → если есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → если есть `admin/<id>`: `remove_own_tree` admin/<id> → если есть отметка: `remove_durably` accept/<id>.toml |
    | `rollback` | если id не в `releases.toml` и корень `releases/<id>` у root: `remove_own_tree` releases/<id> → если id не в `releases.toml` и корень у `df-build`: `cleanup_tree` releases/<id> → если id не в `releases.toml` и есть `staging/<id>/tree`: `cleanup_tree` staging/<id>/tree → если id не в `releases.toml` и есть `staging/<id>` без `tree`: `remove_empty_dir` staging/<id> → если id не в `releases.toml` и есть `admin/<id>`: `remove_own_tree` admin/<id> → если id не в `releases.toml` и есть отметка: `remove_durably` accept/<id>.toml → `remove_durably` pending.toml |
    <!-- install-route-calls:end -->

    Таблица — отображение `render_route_calls()` (rev 8, r10): полная развёртка
    `ROUTES` действия с условиями и объектами каждого шага, повторы не схлопываются;
    обёртки `WRAPS` реализуют `write_atomic` (`write_mark` — отметка,
    `write_release_json`, `register` — реестр) и `remove_durably` (`drop_mark`).
    Сверяют `r18-local-check/check_plan.py` и тест `test_action_table_is_route_unfolding`.

    Имена действий и функций сверяет тест: ключи `install_actions` = множество `Row.action`
    без `""` и `"stop"` (`ACTIONS`). Чтение автомата — `read_for(run) -> Callable[[],
    Facts]`: `read_facts` с `df.admin.admin_ready` (Task 8a) и `L`, привязку сверяет тест
    (R33.F5, I3): `L` спрашивается только при `intact` (m3), поэтому `read_for` не
    выводит `intact` заново — `launcher_ready` = `admin.launcher_ready(layout, id,
    python=<запись>.admin_python)` по записи реестра (ревью PR #628: без третьего дайджеста
    дерева и обхода admin-Python на каждое чтение). `command_ready(layout, release_id) -> bool` (=
    `command_ready_of(layout, id, registry_entry(...))`): нет записи реестра или не
    `intact` (`accept.entry_intact`) → `False`, иначе `admin.launcher_ready(layout, id,
    python=<запись>.admin_python)` — до регистрации ничего не читается из непроверенного
    `admin/<id>`. Той же функцией bootstrap проверяет исправную команду (по записи снимка,
    без чтения реестра), а маршрут `launcher` пишет `release_entry_text(layout, id)` (`0644`)
    и `launcher_text(layout, <Python записи реестра>, platform)` (`0755`); нет записи —
    `StepFailed("launcher")`. Текст входа порождается из `admin.entry_source(layout, id)` =
    `admin_dir(id)/df/launcher_entry.py` активируемого id (`entry_text(layout, source)`:
    `O_NOFOLLOW|O_NONBLOCK`, только обычный файл, ≤ 1 МиБ, UTF-8, ровно одна строка
    `_ROOT` — иначе `Refused("launcher")`, у `release_entry_text` — `StepFailed("launcher")`;
    строка `_ROOT = "/"` — замороженный межверсионный договор, M13), и `launcher_ready`
    сравнивает с тем же источником (ревью Task 8a, M-1 — исправлено, спека §3.9).
    `commit-config` пишет `pending.install_text` в `/etc/df/install.toml` (`0644`), затем
    `remove_durably(pending.toml)`. Если файлы команды прежнего релиза побайтно равны новым
    (тот же источник входа и тот же Python), `L` истинен уже после `activate`, и автомат
    идёт `registered → launched`, пропуская `launcher` (9-A C3; в v1 так всегда при
    переустановке — та же конфигурация).
    Команда называет Python одного релиза, поэтому откат или возобновление к релизу с
    другим `admin_python` оставляют `L` ложным для этого id: маршрут отката/возобновления
    обязан дойти до `launcher` для активного id, когда `L` ложен (F16; вход
    `sys.executable` с записью не сверяет — иначе восстановление через `darkfactory` было
    бы закрыто). Без доверенного `/usr/local/bin` установка печатает полный путь команды. Доверенные чекауты
    (`fetch_reference`, Task 7) — до `drive`, в `layout.verify_dir/<repo>` (R33.F34).

    Настройки `uv` (спека §3.2, решение владельца 22.10, Task 7a): до `run_build` — для
    каждого репо профиля `uv_config_problems(refs[r]/pyproject.toml,
    refs[r]/uv.toml или None, refs[r]/uv.lock)` по доверенному чекауту. Проблемы **всех**
    репо — один `StepFailed("build", …)` с префиксом `<repo>:` у каждой строки (файл и
    ключ), а не первая (образец Task 2); `run_build` при этом не начинается. Файлы чекаута
    читаются как данные; нечитаемый — проблема того же списка.

    Действие `verify` по итоговой копии — маршрут `verify`: маршрут `cleanup_staging`
    (условия — есть ли `staging/<id>/tree` и `staging/<id>`; уже удалённый staging — оба
    шага пропущены, rev 8 r8), `check_links`, протокол `verify_chmod`, проверки
    `verify_sources`, `distribution_problems` против `expected_distributions` из lock
    доверенного чекаута, `static_problems`; затем `cleanup_build_home` для `tmp` и для
    `cache` — отдельные шаги (rev 8, r9: уже удалённое — `"absent"`, успех), затем
    `transfer_check` (ничего не удаляет).

    Затем `tree_digest` обязан совпасть с `mark.tree_digest`, и пишется отметка
    `max(mark.stage, "verified")`: при `admin` — с прежними `admin_digest` и
    `admin_python` (отметка не понижается, §3.3). Любая проблема — `StepFailed` со списком.
  - `run_install(layout, requested: InstallConfig, snap: Snapshot, pending: Pending |
    None, *, fail_after: str | None = None) -> str` — одна установка под блокировкой,
    возвращает id. Порядок фиксирован (I6, M5, M6): `recover_pending`; `generation_begin`
    (на нечётном поколении — без записи); кроме `resume` — `next_release_id`, затем
    `new_pending(requested, id, snap.release_id)` (свежий `run_id`, `install_text =
    dump_install(cfg)`, `install_digest = config_digest(cfg)`) пишется атомарно; шаги 1–3
    (`run_steps`, `fail_after` — шов тестов); чекауты; `uv_config_refusal`;
    `retry_quarantine`; `InstallRun`; `drive`; `generation_end`. **G4** (I9, 9-D1): отказ
    `drive` шагом `damaged` **или** любой отказ действия, после которого релиз прогона
    классифицируется `damaged` (`_now_damaged`: проверка после действия останавливает
    прогон именем действия, не `damaged`; ошибка чтения фактов оставляет исходный отказ),
    становится `StepFailed("damaged", <текст> + " (остановлено: <шаг>: <подробность>)")`;
    `current` и запись реестра читаются заново как собственные изменения писателя
    (`accept.current_is(layout, id)`, публичная): `current` на релизе прогона — текст G1(a),
    иначе — текст G2.
  - I9 (решение владельца 23.10, R51–R53; дизайн `task-9-i9-design.md`), всё в
    `df/install.py`:
    - «согласованное состояние» — нет `pending.toml`, и `current` отсутствует, `intact`
      или зарегистрирован и начинается с `release.id_prefix(install.manifest_sha,
      platform)` (спека §3.8);
    - **G1** `unfinished_damage(layout, snap, pending) -> str | None` — в `cli._writer`
      сразу после сверки `read_pending` со снимком, до фильтра `pending`, `_later`,
      `_requested`, отказа исправной команды, M9 и `run_install`, то есть и до `models
      set`: (a) `pending.toml` есть, `current` = его id, релиз не `intact` (A1–A5 дизайна);
      (b) `pending.toml` нет, `current` не `intact` и не согласован (H1 первой установки,
      ручная правка `install.toml`, `cur` без `reg`). `pending.toml` есть, а `current` не
      его id — G1 молчит (обычный путь §3.8: повтор при повреждённом прежнем). Значения
      снимка плюс `accept.entry_intact` (дерево и `admin/`), вычисляется только когда (a)
      или (b) возможны; отказ ничего не меняет и не журналируется;
    - **G2** — в `recover_pending`, ветка «прежний `current`, та же конфигурация» (выше);
    - **G3** — только порядок: M9 (`_close_odd_generation`) вызывается после G1, значит
      поколение закрывается только в согласованном состоянии; `detail` его `warn` кончается
      `current = <id|нет>`;
    - **G4** — в `run_install` (выше);
    - `Damage(kind: Literal["switched", "unclosed", "pending"], release_id, run_id,
      current, registered, install, agrees)`, `damage_of(...)` и
      `damaged_refusal(layout, damage) -> str` — один источник текста для G1(a)
      (`switched`), G1(b) (`unclosed`) и G2 (`pending`); точные тексты закреплены тестом
      `test_damaged_refusal_text`. Текст называет id, состояние и только оставшиеся шаги:
      копии (`pending.toml`, если есть, и журнал) вне `/opt/df`, `/etc/df`, `/var/lib/df`;
      удалить `pending.toml`; `unlink current` — только когда после удаления состояние не
      станет согласованным (G1(a) не `agrees`; G1(b) всегда); затем команда:
      `BOOTSTRAP init …` без `install.toml` (с подсказкой «конфигурация прежней попытки —
      install_text в копии pending.toml», если `pending.toml` был), `darkfactory install
      (если команда отказывает — BOOTSTRAP install)` для G2 при `current` на прежнем
      (bootstrap отказывает при исправной команде, M16), иначе `BOOTSTRAP install`; затем
      «current не переключайте, поколение не правьте, releases/, admin/ и releases.toml не
      трогайте». `BOOTSTRAP = "sudo <python ≥ 3.12> -I -B -S <чекаут
      devtools>/df/bootstrap.py"`. Ни один текст не советует `doctor` (в этом состоянии
      недоступен), возврат `current` или правку поколения. Отказ M10 тоже без `doctor`.
  - `main(argv: Sequence[str], *, mode: Mode, read: ReadResult | None, lock_fd: int | None,
    layout: Layout | None = None) -> int` в `cli.py` (`layout` — шов тестов; вход и
    bootstrap его не передают):
    - `mode`, `read`, `lock_fd` передаёт вход команды (Task 8a). `writer` требует `lock_fd` и
      `Stable`, `reader` — `lock_fd is None`, `bootstrap` — ни того, ни другого; иное —
      ошибка программы, код 2;
    - разбор (`_Parser`: `exit_on_error=False`, `error()` бросает, N1; `allow_abbrev=False`
      у каждого парсера). Режим, выведенный из разобранных аргументов (`parsed_mode`:
      `writer` для `WRITER_COMMANDS` по `(command, rest[0])`), обязан совпасть с
      `launcher_entry.mode_of(argv)`, а `--` где-либо в `argv` — отказ (9-B I-2;
      `UsageError`, код 2) — до проверки `euid` и любой диспетчеризации. Подкоманда-писатель
      в режиме `reader` — код 2 (вход не взял блокировку); bootstrap принимает только
      `init`/`install`;
    - `euid == 0` (шов `cli.ROOT_UID`) для каждой подкоманды-писателя — до `writer_lock`
      (M14); конфигурация `init` (`--cli NAME=PATH`, каждое имя один раз) проверяется
      там же, до блокировки;
    - `reader` с `Transitional`: `doctor` — переходная диагностика (Task 12), остальные — код
      2, «идёт или прервана административная операция: дождитесь её конца; прерванную повторите от root (darkfactory install или darkfactory models set)»;
    - `bootstrap` — только из `df/bootstrap.py`, `read is None`: `main` сама берёт
      `writer_lock`, выполняет `settle_on_acquire` и читает `read_raw`. Отсутствие блокировки
      режим не определяет. Python ≥ 3.12, флаги `-I -B -S` и подкоманды `init`/`install`
      проверяет сам `bootstrap.py`;
    - путь писателя (`_writer`): `parse_snapshot`; `read_pending` ровно один раз и сверка с
      `pending_present`; **G1**; пока есть `pending.toml` — только `init`/`install`/`doctor`
      (`PENDING_ALLOWED`, M19), иначе «есть незавершённая установка: завершите darkfactory
      install»; `models`/`doctor` — `_later` («ещё не реализовано (Task 10/12)», неизвестное
      имя — отказ, а не `KeyError`); `_requested`; в bootstrap — отказ исправной команды
      (M16: нет `pending.toml`, есть `current`, запись `allowed`, `command_ready_of` по
      записи снимка — код 2, «команда исправна (релиз <id>): используйте darkfactory»; Task
      8a F10, флага `--recover` нет); M9 — нечётное поколение без `pending.toml` закрывается
      `generation_end` с `warn` (`run_id="generation"`); `run_install`; печать «установлен
      релиз <id>; команда: <путь>» — ссылка `/usr/local/bin`, только если она указывает на
      нашу команду, иначе полный `/opt/df/bin/darkfactory`.

    Подкоманды:
    - `init --manifest-repo R --manifest-sha S --profile P --cli NAME=PATH...` — требует
      `euid == 0`; при существующем `install.toml` — код 2, «установка уже есть: darkfactory
      install». Порядок: снимок от входа (или `settle_on_acquire` и `read_raw` в
      bootstrap), `recover_pending`,
      новый `Pending` (если нужно),
      шаги;
    - `install` — то же с конфигурацией из `pending.toml`, если он есть, иначе из
      `install.toml`; ни того, ни другого — «нет /etc/df/install.toml: начните с bootstrap
      init»;
    - `doctor` (Task 12);
    - `models` (Task 10).

    Коды выхода (M7): 0 — успех; 2 — `Refusal`, `UsageError`, `StepFailed`, `TreeError`,
    `ConfigError`, `ManifestError`, `UnsupportedLock`, `OSError`: `darkfactory: <шаг>:
    <подробность>` для `StepFailed`; `darkfactory: <сообщение>` для `Refusal` и
    `UsageError`; `darkfactory: <Тип>: <сообщение>` для `TreeError`, `ConfigError`,
    `ManifestError`, `UnsupportedLock`, `OSError` (9-B m3); 3 — `NeedsHuman` (инструкция в stderr); 1 — иное, с трассировкой (ошибка
    программы, в том числе `AssertionError`).

- [ ] **Step 1: Failing tests**

```python
def test_next_id_skips_registered_quarantined_and_leftovers(tmp_path): ...
def test_activate_refuses_unregistered_revoked_or_admin_missing(tmp_path): ...
def test_activate_is_atomic_relative_symlink_and_syncs(monkeypatch, tmp_path): ...
def test_clean_pass_walks_rows_in_table_order(fake_world): ...
    # без сбоев: журнал действий = build-dir, build, seal, copy, verify, admin, register,
    # activate, launcher, commit-config
@pytest.mark.parametrize("step", ["manifest", "trusted-paths", "build-user"])
def test_interrupt_prep_step_then_rerun_completes(fake_world, step): ...   # настоящий run_steps
def test_crash_matrix(fake_world): ...
    # перенос r18-local-check/model.py на настоящий код (классификация — те же ROWS, settle —
    # те же FACT_SOURCES): модель сбоя ОС из fake_world нумерует эффекты fsutil, и **каждый
    # вызов fault_point** — точка сбоя (окно «видимо, не закреплено»); сбой ОС откатывает
    # всё незакреплённое. Начала — первая установка и обновление (ссылка уже есть). Для
    # каждой точки каждого действия — сбой процесса и сбой ОС, до двух подряд; повтор —
    # настоящий путь вход → settle_on_acquire → main → recover_pending → drive (не drive
    # отдельно, S1-R6-N1) доходит до committed, current, install.toml и команда
    # согласованы; damaged не достигается. Исход очистителя — ветвление (S1-R12-N1):
    # fake_world проходит и tree, и quarantine; исполнены все варианты, кроме NOT_MODELED
    # (иначе провал); каждый примитив возвращается со своим изменением закреплённым; дерево
    # не теряется из родителя и карантина одновременно — по идентичности дерева, а не по
    # полю (S1-R13-N2: второе дерево по тому же пути не скрывает потерю первого).
    # Как построено (9-D2, R49 → R55): движок tests/df/crash_matrix.py (Matrix). Точка
    # сбоя — каждый fault_point и каждая синхронизация настоящего прохода (F5); POINTS
    # fault_point чистого прохода закреплены: первая установка 49, обновление 26
    # (обновление — с изменённым кодом входа, чтобы launcher исполнялся). Каждая точка —
    # снимок State, ветви идут со снимка, префикс не повторяется. CI (386 прогонов,
    # MATRIX_RUNS): глубина 1 — каждый fault_point × {сбой процесса, сбой ОС} × оба
    # начала; глубина 2 — «сбой процесса, затем сбой ОС на первой синхронизации после
    # settle_on_acquire повтора» (F3); другая конфигурация — только где current после
    # сбоя нет (I8); проходы с неудачным очистителем (исход quarantine) × 2. Ожидаемый id
    # повтора назван явно (9-C C7: id прогона; после commit — следующий; SECOND — для
    # другой конфигурации). Точка, где под pending повреждён релиз, — провал (i9 == []).
    # Исполнены все моделируемые варианты, все родители SYNC_PARENTS и все случаи
    # sync_twin_cases(). Глубина 1 во всех точках синхронизации, полный перебор
    # (FULL_CHUNKS покрывают ровно range(POINTS[start])) — только slow; глубина 2 в
    # точках синхронизации повтора не перебирается нигде
@pytest.mark.parametrize("dropped", ["etc_df", "opt_df", "releases", "release_root",
                                     "staging", "admin", "opt_bin", "local_bin"])
def test_crash_matrix_settle_twins(fake_world, dropped): ...
    # settle без каталога dropped → test_crash_matrix находит контрпример (отсутствие —
    # провал теста); install и accept не обязательны для безопасности (§3.3 «Локальная
    # проверка»), но входят в settle. Как построено (R55): каждый двойник, ждущий
    # контрпример, в CI — закреплённое расписание в обе стороны (правило на месте —
    # контрпримера нет; снято — есть), PINNED_RUNS. REDUNDANT на настоящем коде (находка
    # 9-D2, правило остаётся): release_root (единственный факт — режим корня; маскируют
    # verify_chmod и родительская синхронизация release.json при register —
    # test_crash_matrix_release_root_masking) и admin (маскируют собственные синхронизации
    # install_admin и remove_own_tree — test_crash_matrix_settle_admin_masking); их
    # перебор — slow, test_crash_matrix_redundant_twins
@pytest.mark.parametrize("start", ["first-install", "upgrade"])
@pytest.mark.parametrize("window", ["releases.toml", "bin/darkfactory", "usr-local-link"])
def test_visible_unsynced_then_retry_then_os_crash(fake_world, start, window): ...
    # S1-R5-N1: сбой процесса после rename, до sync_dir родителя; повтор; сбой ОС до
    # следующего закрепления → current → зарегистрированный intact-релиз, команда есть,
    # pending.toml удалён только после неё
@pytest.mark.parametrize("mode", ["writer", "bootstrap"])
@pytest.mark.parametrize("window", ["releases.toml", "tree-removal"])
def test_rollback_with_unsynced_fact_then_os_crash(fake_world, mode, window): ...
    # I8: writer — main(mode="writer") напрямую, изолированная проверка recover_pending
    # (через вход другая конфигурация при pending недостижима: init отказывает при
    # install.toml, install берёт конфигурацию из pending.toml).
    # S1-R6-N1, настоящий путь вход/bootstrap → main → recover_pending (не drive):
    # releases.toml — сбой процесса после rename, до sync /etc/df; tree-removal — после
    # удаления дерева, до sync releases/. Повтор с другой конфигурацией → откат → сбой ОС →
    # нет незарегистрированного дерева без pending.toml
@pytest.mark.parametrize("trees", ["release", "admin", "release+admin"])
def test_rollback_durable_before_pending_removed(fake_world, trees): ...
    # S1-R7-N1, путь вход/bootstrap → settle_on_acquire → recover_pending → cleanup →
    # remove_durably(pending): деревья долговечны до начала отката (в т. ч. корень 0755);
    # сбой ОС сразу после удаления pending.toml, до следующего drive → ни одного
    # root-дерева id без pending.toml. Двойники: протокол remove_own_tree без sync_dir
    # родителя (releases/, затем admin/) → контрпример обязан быть
    # (test_rollback_durable_twin_without_parent_sync[releases|admin])
@pytest.mark.parametrize("case", ["after-tmp", "after-cache", "both-absent", "root-0700"])
def test_verify_retry_after_build_home_cleanup(fake_world, case): ...
    # S1-R9-N1, настоящий путь drive → verify → cleanup_build_home → transfer_check:
    # прерывание после удаления tmp (после cache; оба отсутствуют с начала; корень
    # releases/<id> возвращён к 0700) → повтор до committed; cleanup_build_home даёт
    # "absent"; наблюдатель: ни одного root-доступа внутрь build_home
def test_cleanup_build_home_absent_differs_from_error(fake_world): ...
    # find: код 0 и пусто → "absent"; код ≠ 0 (нет доступа к build_home) → StepFailed;
    # launched=False → StepFailed; ни один из них не "absent"
def test_admin_python_rename_then_os_crash(fake_world): ...
    # S1-R10-N2: прерывание после rename Python до sync_dir(admin_python), затем сбой ОС →
    # повтор: settle → Python нет → маршрут admin: remove_own_tree(.staging), установка,
    # remove_own_tree(.staging) → committed; A не истинен ни в одной итерации без Python
def test_upgrade_keeps_shared_admin_python(fake_world): ...
    # S1-R10-N2: Python профиля уже установлен прежним релизом → маршрут admin пропускает
    # install_admin_python; прежний релиз и его admin/<id> не тронуты; A истинен
@pytest.mark.parametrize("prim", sorted(ABSENT_OK))
def test_crash_matrix_absent_ok_twins(fake_world, prim): ...
    # prim отвергает отсутствующий объект → для cleanup_build_home test_crash_matrix даёт
    # отказ повтора verify (форма r9); для cleanup_tree — проходит (все вызовы под
    # условиями), как в r18-local-check/twins.py; иное — провал. Как построено (R55):
    # cleanup_build_home — закреплённое расписание в CI в обе стороны; проходящий
    # cleanup_tree — slow. Форма повтора r9 в мире без репо совпадает с первым verify
    # (build_home/cache нет), её саму держит test_verify_retry_after_build_home_cleanup
def test_verify_retry_after_staging_removed(fake_world): ...
    # S1-R8-N1: verify удалил staging/<id>, chmod 0755 выполнен, процесс упал до отметки;
    # повтор настоящим путём вход/bootstrap → copied → verify: шаги cleanup_staging
    # пропущены условиями, установка завершается
def test_rollback_with_staging_container_then_os_crash(fake_world): ...
    # S1-R8-N1: долговечный staging/<id> (с tree и без), повтор с другой конфигурацией →
    # маршрут rollback → сбой ОС сразу после удаления pending.toml → остатка id нет
@pytest.mark.parametrize("step", [(r, i) for r, s in ROUTES.items()
                                  for i, (g, *_) in enumerate(s) if g])
def test_crash_matrix_route_guard_twins(fake_world, step): ...
    # условие шага step считается истинным → test_crash_matrix даёт отказ примитива на
    # отсутствующем объекте; исключения модели — ровно GUARD_NOT_NEEDED из
    # r18-local-check/twins.py (S1-R10-N3): ("cleanup", 1) — после шага r=root объекта
    # нет, cleanup_tree отсутствующего — "removed"; ("launcher", 2) — в матрице
    # /usr/local/bin доверен, принудительная истина ничего не меняет; ложную ветку
    # проверяет test_usr_local_link_only_when_trusted (Task 8a). На настоящем коде
    # (9-D2 F1, R55) литерал теста — только второй: cleanup_tree отката сначала требует
    # готовую учётку df-build и для отсутствующего дерева, поэтому первое условие нужно и
    # его двойник ждёт контрпример. CI — 11 закреплённых расписаний в обе стороны
@pytest.mark.parametrize("case", sync_twin_cases())
def test_crash_matrix_protocol_twins(fake_world, case): ...
    # case = (протокол, вариант, шаг[, родитель]) — общий источник с twins.py (S1-R13-N3);
    # sync-шаг подменён пустым (у протокола из SYNC_PARENTS — только при вызове с этим
    # родителем) → test_crash_matrix даёт контрпример ровно при case ∈ SYNC_NEEDED; иначе
    # проходит (покрывает settle после действия); например, ("cleanup_tree", "tree", 2,
    # "releases") — контрпример, ("cleanup_tree", "tree", 2, "staging") — нет.
    # Как построено (R55): ждущие контрпример — 12 закреплённых расписаний в CI в обе
    # стороны; два из них ловятся только сбоем ОС в точке синхронизации (F5) —
    # remove_own_tree родителя admin_python и remove_empty_dir staging; их расписания
    # закреплены по имени (Schedule.anchor: первая синхронизация staging/.quarantine после
    # 3-й или 4-й записи accept/<id>.toml), ответ unreachable означает «перезакрепить», а
    # не дефект. Проходящие (покрыты settle после действия, 9-C C2) — slow
def test_crash_matrix_acquire_twin(fake_world): ...
    # settle_on_acquire подменён пустым → test_crash_matrix находит контрпример (отсутствие
    # — провал теста); модель — twins.py «no settle_on_acquire». Как построено: форма
    # no-op — закреплённое расписание в CI в обе стороны (то же, что у settle releases);
    # форма без id-зависимых каталогов pending.toml (9-C C1) — REDUNDANT на настоящем коде:
    # до drive их читают только условия маршрута отката, а примитив каждого шага закрепляет
    # свой итог до удаления pending.toml (test_crash_matrix_acquire_twin_id_paths, slow)
@pytest.mark.parametrize("point", ["member", "mid-copy"])
def test_crash_inside_copy_then_rerun(fake_world, point): ...
@pytest.mark.parametrize("after", ["activate", "launcher", "commit-config:install.toml"])
def test_crash_after_activation_completes_same_id(fake_world, after): ...
    # повтор: тот же id, команда установлена, install.toml = pending, pending удалён последним
@pytest.mark.parametrize("mark", ["verified", "admin"])
@pytest.mark.parametrize("crash", ["process", "os"])
def test_os_crash_model_keeps_only_synced_effects(fake_world, mark, crash): ...
    # подмена fsutil: эффекты без sync откатываются → повтор не пропускает transfer-check и admin
@pytest.mark.parametrize("action", ["activate", "commit-config"])
def test_crash_after_activate_before_journal_done(fake_world, action): ...
    # M19: в Task 9 — по фактам (committed, без записи done в журнале); половина
    # «doctor: уровень 1 по фактам» — Task 12 (test_level1_ignores_journal)
def test_resume_with_other_config_rolls_back(fake_world): ...
    # прерывание после build; повтор init с другим manifest_sha → дерево очищено, новый id
def test_registered_unactivated_survives_rollback(fake_world): ...
def test_rollback_after_unsynced_admin_removal_then_os_crash(fake_world): ...
    # контрпример модели r4: сбой процесса после удаления admin/<id> без sync_dir(admin);
    # повтор с другой конфигурацией (откат); сбой ОС → admin/<id> не вернулся (settle)
def test_rollback_after_verify_chmod(fake_world): ...
    # прерывание после chmod 0755 в verify; повтор с другой конфигурацией → дерево удалено
def test_reader_interleaved_with_install_commit(fake_world): ...
    # читатель между чтением current и install.toml; установка полностью переходит на B →
    # читатель получает снимок A целиком или отказ, но не смесь; установка не ждёт читателя
def test_process_crash_after_current_rename_then_os_crash(fake_world): ...
    # сбой процесса между rename current и sync_dir(opt); повтор; затем сбой ОС модели
    # после commit-config → current и install.toml согласованы (контрпример r3-local-check)
def test_install_actions_cover_rows(): ...
def test_action_table_is_route_unfolding(): ...
    # из Task 7 (R33.F4): ключи install_calls(...) == route_leaves() ∪ WRAPS; блок
    # install-route-calls в docstring install_actions == render_route_calls()
@pytest.mark.parametrize("name", sorted(ROUTES))
def test_routes_match_table_with_real_calls(fake_world, name): ...
def test_uv_config_refused_before_build_lists_all_repos(fake_world): ...
    # Task 7a/§3.2: два репо — [tool.uv] exclude-newer у одного, uv.toml у другого →
    # StepFailed("build") с обоими файлами и ключами; ни одного запуска run_as(df-build)
def test_release_json_distributions_from_the_accepted_tree(fake_world): ...
    # I4 (переименован): distributions_declared == спискам, которые distribution_problems
    # принял на том же дереве, прочитанным в свежем контексте прогона; registry —
    # index: "not declared"; lock_sha256 — из доверенного чекаута (M12)
    # R33.F3: test_routes_match_table Task 7 со всеми настоящими calls (install_calls),
    # без заглушек LATER_LEAVES; тот же python_key, что у install_actions
def test_read_facts_binds_admin_predicates(fake_world): ...
    # R33.F5: drive читает факты с df.admin.admin_ready и launcher_ready
def test_install_digest_is_one_value(fake_world): ...
    # из финального ревью Task 7 (M1): install_digest, с которым read_facts читает
    # отметки, == config_digest(install), под которым их пишет acceptance_calls; одно
    # значение передаётся обоим явно; двойник с разными значениями — drive стоит на
    # первой post-проверке (fail-closed)
def test_home_found_by_retry_is_settled_before_ready(fake_world): ...
    # из Task 5 (R21, R33.F22). S1-R6-N1: rename_noreplace → сбой процесса → повтор
    # (settle_on_acquire) → ready → сбой ОС → домашний каталог на месте; двойник без
    # settle_on_acquire — каталога нет
def test_rollback_failure_names_its_step(fake_world): ...
    # 7-D2: TreeError обёрток remove_own_tree / remove_empty_dir вне drive (rollback)
    # приходит без имени шага — recover_pending называет шаг отказа
def test_generation_even_after_success_and_rollback(fake_world): ...
def test_init_lock_loser_changes_nothing(fake_world): ...   # второй init при занятом lock: файлы /etc/df и /var/lib/df побайтово прежние
def test_init_refuses_existing_install(fake_world): ...
@pytest.mark.parametrize("entry", ["bootstrap", "darkfactory"])
@pytest.mark.parametrize("config", ["same", "other"])
def test_init_with_pending_first_install(fake_world, entry, config): ...
    # S1-R7-N3: первая установка прервана до commit-config (install.toml нет, pending.toml
    # есть): init с той же конфигурацией продолжает тот же id; с другой — откат и новый id.
    # I8: entry=bootstrap — так; entry=darkfactory (прервано после activate) — same и other
    # продолжают тот же id, запрошенная конфигурация не применяется, warn в журнале
def test_init_with_pending_and_install_toml_refused(fake_world): ...
    # pending.toml и install.toml есть → init код 2, «установка уже есть: darkfactory install»;
    # файлы побайтово прежние
def test_commands_refuse_while_pending(fake_world): ...     # models set → код 2
    # M19: общим фильтром до диспетчеризации; models — заглушка до Task 10
def test_init_requires_root(monkeypatch): ...               # euid≠0 → код 2, «нужен sudo»
# перенесены из Task 8a (F4, F10, F16)
def test_entry_raw_fields_match_df_state(): ...
    # launcher_entry.RAW_FIELDS == поля настоящего df.state.RawSnapshot
@pytest.mark.parametrize("run", ["bootstrap-init", "writer-upgrade", "writer-resume"])
def test_main_rereads_nothing(fake_world, run): ...
    # снимок от входа; шпион os.open/os.readlink/os.stat/os.lstat: части снимка в main
    # читаются только из белого списка I2 (Interfaces, state.py), pending.toml —
    # read_pending ровно один раз; читатель — ни одной
def test_install_committed_without_local_link(fake_world, capsys): ...
    # local_bin=trusted ложно → link_command не вызван, L истинен без ссылки, установка
    # доходит до committed и печатает полный путь /opt/df/bin/darkfactory
def test_bootstrap_refuses_healthy_command(fake_world): ...
    # current, allowed, intact, L → код 2 «используйте darkfactory»; сломанная команда
    # (L ложен) или pending → bootstrap продолжает
def test_reader_mode_refuses_writer_subcommand(fake_world): ...
    # main(["install"], mode="reader", ...) → код 2
def test_rollback_to_other_admin_python_rewrites_launcher(fake_world): ...
    # F16: откат к релизу с другим admin_python → маршрут доходит до launcher, L истинен
def test_entry_text_from_activated_admin_code(fake_world): ...
    # ревью Task 8a, M-1: вход пишется из admin/<id>/df/launcher_entry.py активируемого id;
    # launcher_ready сравнивает с ним же — bootstrap-чекаут и код прежнего релиза не источник

# Как построено — добавлено частями 9-A…9-D2 (сверх списка выше)
# test_release.py (9-A): next_id_names_are_exact (M1), next_id_broken_registry_refused,
# activate_crash_after_rename_leaves_only_current, activate_reuses_one_temporary_name,
# release_json_schema_fields_and_mode, release_json_replaces_the_builders_file (N3),
# release_json_needs_the_release_dir, release_json_refused_unless_the_copy_is_the_marks,
# register_appends_an_allowed_entry, register_refuses, register_wrappers_name_their_step,
# rollback_calls_are_the_route_unfolding,
# rollback_without_build_uid_refuses_a_foreign_tree (I5),
# rollback_never_runs_as_build_user_before_it_is_ready и его положительный двойник
# rollback_runs_the_cleaner_when_the_account_is_ready (9-A I-1),
# clean_pass_routes_match_table_with_real_calls (D3: вместе с test_routes_match_table
# Task 7 на всех настоящих вызовах), command_ready_reads_nothing_before_registration,
# command_ready_uses_the_entry_python, read_facts_asks_l_only_when_intact (m3),
# launcher_without_registry_entry_refused, install_run_guards_its_invariants,
# install_run_platform_is_the_hosts, python_declared_survives_a_new_process_after_verified
# (I4). Рядом: test_admin.py (launcher_ready против источника релиза, плохой источник),
# test_launcher_entry.py (entry_text из источника), test_accept.py (python_declared
# отметки, entry_intact), test_drive.py (M4/M5 журнала).
# test_state.py (9-B): части снимка, плохой current, ссылка вместо части, снимок
# читателя с pending — отказ.
def test_trust_order_before_any_execution(fake_world): ...
    # I6, тест-шпион: шаг 1 → шаг 2 → шаг 3 → fetch_reference → uv_config_refusal →
    # retry_quarantine → drive; ни одного git/uv/tar до успешного шага 2; положительный
    # контроль — test_commands_run_as_df_build_only_after_step2
def test_rollback_needs_trusted_parents(fake_world): ...
    # I7: недоверенный /opt → StepFailed("trusted-paths") до любого удаления
def test_rollback_judges_an_absent_parent_by_its_ancestor(fake_world): ...
    # 9-B I-1: /opt/df нет, /opt недоверен → отказ, pending.toml цел, строки rollback нет
@pytest.mark.parametrize("side", ["opt", "etc"])
def test_step2_judges_an_absent_dir_by_its_ancestor(fake_world, side): ...
def test_step2_refuses_a_linked_dir(fake_world): ...                # «не каталог»
def test_build_user_step_runs_nothing_as_df_build_before_ready(fake_world): ...   # M4
def test_odd_generation_without_pending_is_closed(fake_world): ...  # M9
def test_third_current_is_refused(fake_world): ...                  # M10, без doctor
def test_pending_text_and_digest_are_one_value(fake_world): ...     # M8
def test_pending_must_match_the_snapshot(fake_world): ...           # I2
def test_argv_forms_never_split_mode_and_dispatch(fake_world): ...
    # 9-B I-2: 11 форм × 3 режима — `--` до и после подкоманды, опция до подкоманды,
    # сокращение, префикс подкоманды, повтор, пустой argv, models set: код 2 без
    # трассировки, run_install не вызван, var не создан
def test_check_mode_refuses_any_disagreement(): ...
def test_exit_codes(fake_world): ...           # M7; «<Тип>: сообщение» для отказа без шага
def test_bootstrap_proceeds_over_a_revoked_release(fake_world): ...
def test_damaged_first_install_names_pending_then_current(fake_world): ...
    # переименован (I9 §5.6): «удалите pending» раньше «unlink current»
# test_crash_model.py (9-C): правила R1–R9 модели сбоя ОС и её пределы L1–L4.
# test_crash_windows.py (9-C), сверх списка выше:
# rollback_durable_twin_without_parent_sync, created_home_is_durable_before_ready,
# fsutil_is_the_models, prep_dirs_keep_their_modes_across_os_crash,
# lock_dirs_keep_their_modes_across_os_crash,
# account_created_through_main[process|os] (9-C Minor 8: настоящие create_build_user и
# verify_build_user через main; только путь платформы хоста — Linux в Task 13).
# test_crash_create.py (9-C, класс «созданное — в итоговом виде»):
@pytest.mark.parametrize("crash", ["process", "os"])
def test_lock_and_step2_every_crash_position(fake_world, crash): ...
    # каждая позиция фазы блокировки и шага 2 (106 точек модели, 27 вызовов df, число
    # закреплено): нет видимого имени с неитоговым режимом; повтор доходит до drive; после
    # ещё одного сбоя ОС каталоги, блокировка и pending.toml — в итоговых режимах
@pytest.mark.parametrize("crash", ["none", "process", "os"])
def test_setgid_parents_never_leave_a_setgid_dir(fake_world, crash): ...
    # R9 модели (Linux, наследование S_ISGID): /opt и /var/lib — 2755; повтор доходит до
    # drive, каталоги точно в своих режимах, каталоги оператора не тронуты
def test_step2_widens_roots_narrower_dirs_only(fake_world): ...
def test_pending_written_then_entry_retry_then_os_crash(fake_world): ...
# test_fsutil.py (9-C, 9-D1): двойники make_dir/create_file в каждой позиции;
# test_every_fs_mutation_is_accounted_for — каждое изменение ФС в df/*.py (флаги os.open
# по содержимому) учтено в FS_SITES с причиной; test_every_process_start_is_named —
# каждый запуск процесса учтён в SUBPROCESS_CREATORS (предел стража: что делают сами
# процессы, код вне df/*.py и вызовы из другого модуля он не видит — docstring).
# test_install_i9.py (9-D1; дизайн task-9-i9-design.md §5; каждый отказ: код 2,
# «darkfactory: damaged:», id релиза, без doctor; файлы /etc/df, /var/lib/df и current
# до и после равны; нет fetch_references, run_as и шагов 1–3):
@pytest.mark.parametrize("state", ["first-A2", "first-A4", "first-A5", "reinstall-A4",
                                   "reinstall-A5"])
def test_i9_switched_damaged_refused(fake_world, state): ...
    # G1(a) на четырёх путях одного состояния: writer install, bootstrap install, bootstrap
    # init той же и другой конфигурацией; unlink — ровно у первой установки до A5
def test_i9_switched_damaged_admin_entry_then_bootstrap(fake_world): ...
@pytest.mark.parametrize("pending", [True, False])
def test_i9_switched_unregistered(fake_world, pending): ...
def test_i9_bare_pending_deletion_first_install_refused(fake_world): ...
    # G1(b); поколение осталось нечётным, строки generation warn нет (G3)
def test_i9_bare_deletion_and_even_generation_first_install_refused(fake_world): ...
def test_i9_bare_deletion_first_install_other_config_refused(fake_world): ...
    # ручная правка install.toml на другой sha — «не согласован с ним», unlink
def test_i9_switch_back_only_refused_early(fake_world): ...   # G2 до шагов и сети
def test_i9_registered_damaged_before_activate_refused_early(fake_world): ...
def test_i9_models_set_refused_first(fake_world): ...   # G1 раньше фильтра pending и _later
def test_i9_backstop_uses_fresh_current(fake_world): ...
def test_i9_backstop_damage_during_resume(fake_world): ...
    # «(остановлено: launcher: active → damaged)»
def test_i9_backstop_before_activate_names_g2(fake_world): ...
@pytest.mark.parametrize("unreadable", [False, True])
def test_i9_backstop_keeps_other_failures(fake_world, unreadable): ...
    # G4 не шире нужного: отказ действия над целым релизом остаётся своим
def test_i9_procedure_reinstall(fake_world): ...
@pytest.mark.parametrize("state", ["first-A2", "first-A5"])
def test_i9_procedure_first_install(fake_world, state): ...
def test_i9_procedure_after_switch_back(fake_world): ...
def test_i9_procedure_reentrant(fake_world): ...
def test_i9_procedure_step_undone_by_os_crash(fake_world): ...
@pytest.mark.parametrize("kind", ["tree", "admin"])
def test_i9_normal_path_damaged_after_commit(fake_world, kind): ...
def test_i9_normal_path_retry_between_generation_and_pending(fake_world): ...
def test_i9_normal_path_retry_before_activate(fake_world): ...
def test_i9_committed_odd_generation_damaged(fake_world): ...
def test_i9_bare_deletion_reinstall_proceeds(fake_world): ...
    # R53: код 0, новый id <sha12>-<plat>-3 ≠ R; activate и drive — только с новым id;
    # запись R в реестре не изменилась (зарегистрирован, неактивен)
def test_i9_resume_intact_unaffected(fake_world): ...
def test_i9_rollback_other_config_unaffected(fake_world): ...
def test_damaged_refusal_text(): ...            # точные тексты G1(a)/G1(b)/G2, 9 случаев
def test_damaged_refusal_never_advises_doctor(): ...
# test_crash_matrix.py (9-D2), сверх шести тестов выше (CI, кроме помеченных slow):
def test_crash_matrix_settle_admin_masking(fake_world): ...
@pytest.mark.parametrize("pair", ["activate", "install_admin/3", "link_command",
                                  "accept_copy/4"])
def test_crash_matrix_drive_settle_masking(fake_world, pair): ...
    # settle итерации drive: всё на месте — нет; снята только синхронизация пары — нет
    # (её покрывает settle drive); сняты обе — контрпример. verify_chmod/1 и
    # install_admin_python/3 он не покрывает (их маскируют register и
    # install_admin_python/1)
def test_crash_matrix_release_root_masking(fake_world): ...
    # verify_chmod/1 и release.json сняты — нет; ещё и settle release_root — контрпример
def test_crash_matrix_acquire_twin_id_paths(fake_world): ...          # slow, REDUNDANT
def test_crash_matrix_twin_hunts(fake_world, twin): ...               # slow
def test_crash_matrix_redundant_twins(fake_world, twin, phase): ...   # slow
def test_crash_matrix_full(fake_world, chunk): ...                    # slow, 1 704 прогона
def test_crash_matrix_sync_points(fake_world, start): ...             # slow, F5
def test_crash_matrix_quarantine_branches(fake_world, start): ...     # slow
def test_crash_matrix_all_cleaners(fake_world, start): ...            # slow
    # CI ветвит повтор с неудачным очистителем только для releases; slow — все
```

`fake_world` — фикстура `tests/df/conftest.py`. Подменяет только нижние операции (rev 8,
r10, S1-R10-N1): механизм запуска `run_as`/`spawn_as` (как `fake_layout`), `pkill`/`ps`
внутри `reap_user_processes`, `os.fchown`/`os.chown`, `uv` (фикстурный исполняемый файл
для `install_admin_python`), а также внешние для приёма `fetch_reference`,
`create_build_user`, `verify_build_user`, `whoami_as`. Листья маршрутов (`cleanup_tree`,
`cleanup_build_home`, `install_admin_python`, `receive_tree`) — настоящие. Фикстура
раскладывает фикстурное дерево релиза под `Layout(tmp_path)` поверх `fake_layout` (Task 7):
фиктивный `build_uid` и шов `fsutil.owner_uid` (R33.F1). Модель сбоя ОС
— обёртка над `fsutil`: она помнит несинхронизированные изменения и откатывает их при
«сбое».

Как построено (9-C, 9-D2): `fake_world` = `make_fake_world(make_fake_layout(…))` —
`World` (`tests/df/install_world.py`: заглушки установки, `writer` — половина писателя
входа, `entry` — настоящий путь входа `launcher_entry.acquire → take_snapshot → main`,
`bootstrap`, `crash_at`, `kill_at`, `killed`, `retry`; функции `interrupt`,
`committed_problems` — успешный прогон закреплён целиком, ничего незакреплённого, —
`at_rest_problems`) плюс `CrashModel` (`tests/df/crash_model.py`). Правила модели (её docstring; спека §3.3
«Синхронизация»): R1 синхронизация файла закрепляет содержимое, режим и владельца, не имя
(голый `fsync` файла на macOS не считается); R2 синхронизация каталога — его записи и его
режим/владелец, не потомков (`chmod`/`chown` закрепляет только синхронизация самого
объекта); R3 `sync_tree`; R4 сбой ОС оставляет закреплённое, закреплённое имя без
синхронизации объекта возвращается «как родилось»; R5 имя, закреплённое в двух каталогах,
возвращается в обоих; R6 сбой ОС — новая загрузка (`boot_id`); R7 сбой процесса оставляет
файлы как есть и отменяет то, что сделали обработчики умирающего кода; R8 моделируется
только корень мира; R9 (по выбору) Linux-наследование `S_ISGID`. Пределы L1–L4 названы
там же. Повтор после сбоя идёт входом, когда `current` есть. API для матрицы: `points`,
`trap`/`untrap`, `drop_syncs`, `snapshot`/`restore`, `os_crash`, `unsynced`,
`is_durable`, `identity`, `durable_names`. В `test_crash_matrix.py` мир — на модуль (базовые проходы общие, каждый
тест сбрасывает счётчики `Matrix.reset`). Медленные тесты (`@pytest.mark.slow`) по
умолчанию не выбираются (`addopts = "-m 'not slow'"` в `pyproject.toml`); прогон —
`uv run --frozen pytest -q -m slow tests/df` (около 90 минут, кусками ≤ 590 с); медленный
тест, названный только node id без `-m slow`, отбирается (код 5). Каждый тест печатает и
утверждает своё число прогонов (литерал). Литералы модели `r18-local-check`
(`GUARD_NOT_NEEDED`, `ABSENT_NOT_NEEDED`) скопированы с указанием происхождения и не
импортируются (M20).

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): install transaction, registration, activation, init/install`
  (как построено: коммиты частей 9-A…9-D3, один PR на всю Task 9, R48).

### Task 10: Модели — `models.toml`, `darkfactory models list|set`

**Files:**
- Create: `df/models.py`
- Modify: `df/cli.py` (подкоманда `models`)
- Test: `tests/df/test_models.py`

**Interfaces:**
- Consumes: `writer_lock`, `generation_begin`, `generation_end` (Task 4); `Snapshot`
  (Task 9) — `models list` берёт `models.toml` из переданного снимка и файл сам не читает;
  `write_atomic` (Task 1).
- Из Task 9 (решено, 9-B I-2, 9-D1): `models set` достигается только через разобранный
  режим — `cli.parsed_mode` обязан совпасть с `launcher_entry.mode_of(argv)`, `--` в
  `argv` уже отвергнут, поэтому `rest` подкоманды `models` никогда не начинается с `--`.
  Диспетчеризация заменяет заглушку `cli._later("models")` (`LATER["models"]`), но идёт
  **после** охранника I9 G1 (`install.unfinished_damage` в `cli._writer`) и фильтра
  `pending.toml` (`PENDING_ALLOWED`): `models set` при повреждённом релизе под
  незавершённой установкой отказывает `damaged`, при `pending.toml` — «завершите
  darkfactory install» (тесты `test_i9_models_set_refused_first`,
  `test_commands_refuse_while_pending` остаются зелёными с настоящей подкомандой).
- Produces:
  - `ROLES: tuple[str, ...] = ("interview", "author", "reviewer", "executor-review",
    "edge-check", "judge")`.
  - `ROLE_CONTEXT: Mapping[str, str]` (решение №3) — единственная таблица; будущие
    запускатели (этап 2) импортируют её отсюда.
  - `HARNESSES = ("claude", "codex")`.
  - `SUPPORTED_PARAMS: Mapping[str, frozenset[str]] = {"claude": frozenset(), "codex":
    frozenset()}` — параметры, которые адаптер (Task 11) умеет передать. Параметр вне набора
    — `ConfigError` при `parse_models` и `models set` (rev 8 §3.6).
  - `ModelChoice(harness: str, model: str, effort: str | None, params: Mapping[str, str])`.
  - `ModelsConfig(default: Mapping[str, ModelChoice], products: Mapping[int,
    Mapping[str, ModelChoice]])` — ключ продукта — `repository_id`.
  - `parse_models(text: str) -> ModelsConfig` — чистый разбор (rev 8, r5): читатели
    передают `snap.models_text`; `load_models(path) -> ModelsConfig` =
    `parse_models(path.read_text())` — только в пути писателя `models set` под блокировкой;
    `dump_models(cfg) -> str`. Схема `df/models/v1`;
    таблицы `[roles.<role>]` и `[products."<repository_id>".roles.<role>]`; неизвестная роль,
    харнесс или ключ — `ConfigError`.
  - `resolve(cfg: ModelsConfig, role: str, product: int | None) -> tuple[ModelChoice, str]`
    — `(выбор, источник)`, источник — `"общий"` или `"продукт <id>"`; роли без выбора —
    `ConfigError`.
  - `config_hash(choice: ModelChoice) -> str` — SHA-256 канонического JSON (ключи
    отсортированы).
  - CLI:
    - `darkfactory models set <role> --harness H --model M [--effort E] [--param k=v]...
      [--product ID]` — `euid == 0`. Чтение, изменение и `write_atomic` идут под
      `writer_lock` и между `generation_begin`/`generation_end` (Task 4), остальные записи
      не меняются;
    - `darkfactory models list [--product ID] [--json]` — колонки: роль, harness, model, effort,
      источник, состояние проверки (из Task 11; до неё — `не проверено`).

    `--product` неизвестного продукта: если `products.toml` отсутствует — «продукт не
    зарегистрирован (этап 3)», код 2.

- [ ] **Step 1: Failing tests**

```python
def test_role_context_covers_all_roles():
    assert set(ROLE_CONTEXT) == set(ROLES)
    assert set(ROLE_CONTEXT.values()) == {"df-exec", "df-review"}
    assert {r for r, u in ROLE_CONTEXT.items() if u == "df-exec"} == {"interview", "author"}
def test_unsupported_param_refused(tmp_path): ...   # params={"temperature": "0"} → ConfigError, в load и в set
def test_product_override_wins_and_names_source(): ...
def test_unknown_role_harness_key_refused(tmp_path): ...
def test_set_preserves_other_entries_and_is_atomic(tmp_path): ...
def test_concurrent_sets_of_two_roles_both_persist(tmp_path): ...  # два процесса; проигравший lock повторяет или получает код 2, ни одна запись не потеряна
def test_env_does_not_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DF_MODEL_AUTHOR", "x"); monkeypatch.setenv("ANTHROPIC_MODEL", "y")
    assert resolve(parse_models(P.read_text()), "author", None)[0].model == "claude-opus-5-5"
def test_list_columns_text_and_json(tmp_path, capsys): ...
def test_list_uses_snapshot_text_only(tmp_path, capsys): ...
    # models list получает снимок A; models.toml заменён на B; audit hook: нет open
    # models.toml; вывод — по A
def test_set_requires_root(monkeypatch): ...
```

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): models.toml and models list/set`

### Task 11: Пробы моделей

**Files:**
- Create: `df/probe.py`
- Modify: `df/models.py` (состояние проверки в `list`)
- Test: `tests/df/test_probe.py`, `tests/df/test_probe_isolation.py` (маркер `harness`),
  `tests/df/fixtures/probe/{claude-ok.json,claude-error.json,claude-http500.json,
  codex-ok.jsonl,codex-neterr.jsonl,codex-http500.jsonl}` (настоящие выводы),
  `tests/df/harness_world.py` (sentinel-деревья hooks, MCP, конфигов строятся во время
  теста: в них абсолютные tmp-пути, поэтому статического `fixtures/probe/sentinel/` нет),
  `tests/df/harness_stub.py` (локальная заглушка провайдера)
- Create (по факту Step 3): `docs/evidence/2026-10-24-df-probe-isolation.md`

**Interfaces:**
- Consumes: `ModelChoice`, `ROLE_CONTEXT`, `config_hash`, `resolve` (Task 10); `run_as`
  (Task 5); `InstallConfig.approved_cli` (харнессы `claude`, `codex` — утверждённые CLI,
  §3.10).
- Produces:
  - `ADAPTER_VERSION = "2"`; `PROBE_PROMPT = "Reply with the single word OK."`.
  - `Exclusion(argv: tuple[str, ...], env: Mapping[str, str])` — элементы закреплённого
    вызова, которые по Step 3 исключают ненаблюдаемый источник (§3.6). Других видов условий
    нет: условие, зависящее от способа авторизации, учётной записи или настроек организации,
    не представимо, и такой профиль не поддерживается.
  - `ManagedSource(kind: Literal["file", "mdm", "server"], locator: str, observable: bool,
    allowed_digests: frozenset[str], excluded_by: Exclusion | None)`.
  - `exclusion_holds(ex: Exclusion, argv: Sequence[str], env: Mapping[str, str]) -> bool` —
    `ex.argv` входит в `argv` как непрерывная подпоследовательность, и каждая пара `ex.env`
    есть в `env` с тем же значением.
  - `HarnessProfile(version: str, argv: Callable[[Path, ModelChoice], list[str]],
    auth_files: tuple[str, ...] | None, managed: tuple[ManagedSource, ...],
    isolation_verified: bool)`. `auth_files = None` означает `backend_unknown`.
  - `HARNESS_PROFILES: Mapping[tuple[str, PlatformName, str], HarnessProfile]` — ключ
    `(harness, платформа, SHA-256 исполняемого файла)` (§3.6 «Допуск»). Хеши и списки
    `managed` заполняет Step 3; до него таблица пуста, и пробы не поддерживаются. Кандидаты
    для Step 3, обе версии с `isolation_verified=False`:
    - `("claude", "2.1.301")` — кандидат argv: `[h, "-p", "--model", m, "--output-format",
      "json", "--max-turns", "1", "--setting-sources", "<значение по Step 3>",
      "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "",
      *(["--effort", e] if e else []), PROMPT]`;
      `auth_files`: Linux — `(".claude/.credentials.json",)`, macOS — `None` (связка
      ключей);
    - `("codex", "0.163.0")` — кандидат argv: `[h, "exec", "--ignore-user-config",
      "--ignore-rules", "-c", 'cli_auth_credentials_store="file"', "-c", "mcp_servers={}",
      "-m", m, "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
      *(["-c", f'model_reasoning_effort="{e}"'] if e else []), PROMPT]`;
      `auth_files = (".codex/auth.json",)` на обеих платформах — хранилище закреплено
      флагом.
  - `admit(harness: str, path: Path, platform: PlatformName, *, argv: Sequence[str], env:
    Mapping[str, str]) -> HarnessProfile | str` — допуск §3.6, **без исполнения
    чего-либо**. `argv` и `env` — ровно те, что будут переданы запуску пробы (сформированы до
    допуска по `ModelChoice` и роли):
    1. `path` — утверждённый CLI; итоговая цель — обычный файл с магией Mach-O/ELF (скрипт →
       «не поддерживается: установка-скрипт»);
    2. SHA-256 цели → профиль; нет профиля → «не поддерживается: неизвестный исполняемый
       файл <sha>»;
    3. `isolation_verified` ложен → «не поддерживается: изоляция не подтверждена»;
    4. `managed_context(profile)`: каждый наблюдаемый источник отсутствует или его хеш ∈
       `allowed_digests`; присутствующий неподтверждённый или нечитаемый источник — отказ; у
       ненаблюдаемого источника `excluded_by` обязателен и `exclusion_holds(excluded_by,
       argv, env)` истинно, иначе отказ.

    Строка — причина отказа; харнесс при этом не исполняется ни разу. **Отдельного запуска
    `--version` нет** (§3.6, rev 8, r4): `harness_version` записи — `profile.version`
    профиля, закреплённого за SHA-256.
  - `managed_context(profile) -> tuple[str | None, list[str]]` — `(digest наблюдаемых
    источников, проблемы)`.
  - `PARAM_FLAGS: Mapping[str, Mapping[str, Callable[[str], list[str]]]]` — тот же набор
    ключей, что `SUPPORTED_PARAMS` (Task 10; в v1 пуст). Параметр без записи — `ConfigError`
    до запуска.
  - `probe_cwd_problems(layout: Layout) -> list[str]` — `layout.probe_cwd` существует,
    принадлежит root, пуст. Ни в нём, ни в родителях нет `.claude`, `CLAUDE.md`,
    `AGENTS.md`, `.codex`, `.git`.
  - `ProbeParse(ok: bool, actual_model: str | None, error: str | None)`;
    `parse_output(harness: str, stdout: bytes) -> ProbeParse`:
    - claude — один JSON-объект; `is_error` ложен → `ok`; `actual_model` — единственный ключ
      `modelUsage`, иначе `None`;
    - codex — JSONL; событие `turn.completed` → `ok`, `actual_model = None`;
    - неразбираемый вывод — `ok=False`, `error="ответ не разобран"`. Вывод роли за «проверено»
      не принимается.
  - `AuthObservation(tracking: Literal["tracked", "unavailable"], reason: Literal["no_file",
    "unreadable", "backend_unknown"] | None, files: tuple[dict[str, object], ...])`;
    `observe_auth(profile: HarnessProfile, home: Path) -> AuthObservation`. Состояния:
    - `backend_unknown` — `profile.auth_files is None`; файлы не читаются вовсе;
    - `tracked` — хотя бы один закреплённый файл прочитан; `files = [{path, mtime_ns,
      sha256}]`;
    - `no_file` — ни одного закреплённого файла нет;
    - `unreadable` — файл есть, но чтение не удалось.

    Содержимое файлов не сохраняется.
  - `ProbeRecord` → JSON `schema = "df/probe/v1"`. Поля:
    - `role`, `product`, `time`;
    - `harness`, `harness_version`, `adapter_version`, `isolation = "verified"`;
    - `requested_model`, `actual_model` (`null` = «не сообщён»), `effort` (`"default"`,
      если не задан), `params`, `config_hash`, `executable_sha256`, `managed_digest`;
    - `auth_tracking` (`"tracked"` или `"unavailable: <reason>"`), `auth_files`;
    - `outcome ∈ {confirmed, not_confirmed}` (только результат вызова), `reason`.
  - `run_probe(role: str, product: int | None, cfg: ModelsConfig, install: InstallConfig,
    layout: Layout, *, runner=run_as) -> ProbeRecord | str`. Строка — «не проверено:
    <причина>» или «не поддерживается: <причина>»; запуска харнесса при этом нет.
    Порядок:
    1. роль ОС существует (до этапа 2 — нет → «не проверено: роль df-exec не создана (этап
       2)»);
    2. харнесс утверждён;
    3. `probe_cwd_problems` пуст;
    4. формирование `argv = profile.argv(path, choice)` и `env = {HOME: home роли, PATH:
       каталог харнесса + /usr/bin:/bin, LANG: C.UTF-8}` — профиль до допуска берётся только
       как данные по SHA;
    5. `admit(..., argv=argv, env=env)` — допуск без исполнения; строка → «не
       поддерживается: <причина>»;
    6. запуск: `runner(ROLE_CONTEXT[role], argv, env=env, cwd=layout.probe_cwd,
       network=True, timeout=120)` — те же `argv` и `env`, что прошли допуск. Это
       единственный вызов харнесса; `harness_version = profile.version`.

    Таймаут или сетевая ошибка → `not_confirmed`, `reason` начинается с «доступ не
    подтверждён». Запись — `layout.probes_dir/<role>.json` или
    `<role>@<repository_id>.json`, `0644` root.
  - `ProbeStatus(text: str, current: bool)`; `probe_status(rec: ProbeRecord | None, choice:
    ModelChoice, *, harness_version: str | None, auth: AuthObservation | None,
    executable_sha256: str | None,
    managed_digest: str | None) -> ProbeStatus`. Первое подходящее правило:
    1. нет записи → `не проверено`, `current=False`;
    2. `harness_version` или `auth` равны `None` → `данные недоступны: нужен sudo`,
       `current=False`;
    3. изменились `config_hash`, `adapter_version`, `harness_version` или
       `executable_sha256`, `managed_digest` → `устарела: <поле>`, `current=False`;
    4. изменилась наблюдаемость: `tracking` или `reason` записи ≠ текущим, в любую сторону →
       `устарела: auth`, `current=False`;
    5. обе стороны `tracked`, файлы различаются → `устарела: auth`, `current=False`;
    6. `outcome = not_confirmed` → `не подтверждён: <reason>`, `current=False`;
    7. `outcome = confirmed`, `tracking = unavailable` → `успешная проба <time>; актуальность
       авторизации не установлена`, `current=False`;
    8. `outcome = confirmed`, `tracking = tracked` → `подтверждён <time>`, `current=True`.

    В JSON-выводе `models list` и `doctor` — поля `status`, `current`, `auth_tracking`, а не
    только текст.
  - CLI: `darkfactory doctor --probe-models [--product ID]` — `euid == 0`; пробы по всем
    ролям (для продукта — по ролям с его выбором).

- [ ] **Step 1: Failing tests (unit)**

```python
def test_argv_claude_and_codex_exact(): ...
def test_unverified_version_never_launches(monkeypatch): ...
    # isolation_verified=False → runner не вызван ни разу, результат «не поддерживается: …»
def test_unknown_executable_never_launched(monkeypatch, tmp_path): ...
    # неизвестный SHA-256 → runner не вызван ни разу
def test_single_launch_without_version_call(monkeypatch): ...
    # spy-runner: ровно один вызов, его argv/env — объекты, прошедшие admit; ни одного
    # вызова с --version; harness_version записи = profile.version
def test_script_install_unsupported(tmp_path): ...       # #!/usr/bin/env node → «установка-скрипт»
@pytest.mark.parametrize("case", ["present-unapproved", "unreadable", "unobservable-not-excluded"])
def test_managed_context_blocks_before_launch(monkeypatch, case): ...
@pytest.mark.parametrize("formed,launched", [("with-exclusion", True),
                                             ("without-exclusion", False),
                                             ("env-differs", False)])
def test_exclusion_checked_on_actual_invocation(monkeypatch, formed, launched): ...
    # один SHA и профиль; runner вызван ровно один раз (проба) только при launched
def test_launch_uses_admitted_argv_env(monkeypatch): ...   # runner получил те же объекты
def test_exclusion_type_has_no_free_text(): ...   # поля Exclusion — только argv и env
def test_probe_cwd_with_project_file_in_parent_refused(tmp_path): ...
def test_parse_claude_reports_actual_model(): ...
def test_parse_codex_model_not_reported(): ...
def test_unparsable_output_not_confirmed(): ...
def test_network_error_is_not_confirmed_not_forbidden(): ...   # reason.startswith("доступ не подтверждён")
def test_role_user_missing_is_not_checked(monkeypatch): ...      # «не проверено: роль df-exec не создана (этап 2)»
def test_backend_unknown_ignores_readable_file(tmp_path): ...    # macOS-профиль claude: файл есть → всё равно unavailable: backend_unknown
@pytest.mark.parametrize("field", ["config_hash", "adapter_version", "harness_version",
                                   "executable_sha256", "managed_digest", "auth"])
def test_status_stale_on_change(field): ...
def test_confirmed_without_auth_file_is_not_current(): ...
@pytest.mark.parametrize("before,after", [("tracked", "unavailable: no_file"),
                                          ("unavailable: no_file", "tracked"),
                                          ("unavailable: no_file", "unavailable: unreadable")])
def test_tracking_transition_makes_stale(before, after): ...
def test_confirmed_tracked_is_current(): ...
def test_param_without_adapter_flag_refused(): ...
def test_auth_observation_has_no_content(tmp_path): ...
```

- [ ] **Step 2: Run** → FAIL; **implement** → PASS.
- [ ] **Step 3: Проверка изоляции на настоящих харнессах** (`tests/df/test_probe_isolation.py`,
  маркер `harness`; локально и в CI с установленными харнессами нужных версий). Без
  платных вызовов: запрос уходит на локальный HTTP-заглушку. Для claude — `ANTHROPIC_BASE_URL`
  с тестовым ключом в env только этого теста; для codex — провайдер через `-c` на
  заглушку. Сценарии для каждой версии:
  - в `HOME` роли лежат sentinel-hooks, пользовательский конфиг с иной моделью, effort и
    провайдером, MCP-сервер — ни один не срабатывает (sentinel-файлы не созданы);
  - заглушка получила запрос с моделью из `ModelChoice`;
  - проектный конфиг в родителе `cwd` не влияет; при нейтральном `probe_cwd` проверка
    `probe_cwd_problems` пуста;
  - codex: подмена `auth.json` меняет отпечаток, а keyring не используется (закреплено
    `file`).

  - managed-источники: для каждого из списка `managed` проверить, что он действительно
    читается до запуска (файл, профиль MDM через `defaults read`/plist на macOS). Для
    серверной доставки — найти элементы argv/env, которые её исключают **при любом способе
    авторизации**, и записать их как `Exclusion`; evidence ссылается на тот же `Exclusion`.
    Не нашлось — профиль не поддерживается.

  Окончательный argv, SHA-256 исполняемого файла каждой платформы и список `managed`
  записываются в `HARNESS_PROFILES`.
  `isolation_verified=True` ставится отдельным коммитом **только** для прошедших версий, со
  ссылкой на evidence. Не прошла — остаётся `False`, и пробы этой версии «не поддерживаются».
- [ ] **Step 4: Commit** `feat(df): explicit model probes with verified harness isolation`

**Итог Task 11 (решение владельца 24.10, дословно):** «Task 11 принимается как реализация механизма допуска, выполнения и учёта проб. Claude 2.1.309 и Codex 0.163.0 остаются неподдерживаемыми; харнессы не запускаются. Живая приёмка модельных ролей не пройдена. Изменение гарантии для серверной политики рассматривается отдельно, для каждого харнесса». Профили claude 2.1.309 и codex
0.163.0 (macOS arm64) закреплены с `isolation_verified=False`; флип не делался. Тесты на
настоящих харнессах — маркер `harness`, только по запросу (`pytest -m harness`). Переносы:
Linux-профили и живая приёмка проб от ролей — Task 13 и этап 2; README — ограничение (Task 14).

### Task 12: `darkfactory doctor` — уровень 1

**Files:**
- Create: `df/doctor.py`
- Modify: `df/cli.py`
- Test: `tests/df/test_doctor.py`

**Interfaces:**
- Consumes: `Snapshot`, `ReadResult`, `Transitional`, `parse_snapshot` (Task 9);
  `KNOWN_SCHEMAS`, `STAGE_OF` (Task 1); `tree_digest` (Task 3); `trusted_path_problems`, `approved_cli_problems`,
  `verify_build_user` (Task 5); `static_problems`, `expected_entrypoints` (Task 8);
  `admin_ready` (Task 8a); `parse_models`, `probe_status` (Tasks 10–11).
- Из Task 9 (решено, R52, 9-C):
  - `doctor` заменяет заглушку `cli._later("doctor")`; в режиме читателя с
    `Transitional` и при `pending.toml` он показывает переходное состояние.
  - При повреждённом **административном** коде или `current` без записи реестра вход
    отказывает до импорта кода, поэтому `doctor` там недоступен (спека §3.8 «Повреждение
    под незавершённой установкой»). Отказы I9 и M10 на `doctor` не ссылаются и ссылаться
    не будут: Task 12 не меняет их тексты (`test_damaged_refusal_never_advises_doctor`).
    При повреждённом дереве релиза (`admin/<id>` цел) `doctor` запускается и обязан
    сообщить `release.digest: нарушено`, а при открытой транзакции — переходное
    состояние, не уровень 1.
  - Половина «`doctor`: уровень 1 по фактам» теста Task 9
    `test_crash_after_activate_before_journal_done` — здесь (`test_level1_ignores_journal`).
- Produces:
  - `Check(id: str, status: Literal["ok", "нарушено", "не проверено", "данные недоступны"],
    reason: str, time: str)`.
  - `doctor_main(read: ReadResult, layout: Layout, *, euid: int, as_json: bool) -> int` —
    `Transitional(g)` → «уровень 1: не достигнут — install.transitional: <текст
    `cli._TRANSITIONAL`>» (R69: текст Task 10 — «идёт или прервана административная
    операция: дождитесь её конца; прерванную повторите от root (darkfactory install или
    darkfactory models set)»), в JSON `{"level1": "not reached",
    "state": "transitional", "generation": g}`; части снимка не используются. `Stable` →
    `parse_snapshot(..., mode="reader")` → `level1_checks`. Код выхода — тот же, что у
    недостигнутого уровня 1 (R68: 0 — достигнут, 2 — не достигнут, 1 — ошибка программы).
  - `level1_checks(snap: Snapshot, layout: Layout, *, euid: int) -> list[Check]` — §3.8,
    **только факты**, журнал не читается; конфигурация и реестр — только из `snap`. Набор id:
    - `install.pending` — `pending.toml` нет; есть → `нарушено: переходное состояние,
      завершите darkfactory install`;
    - `etc.schemas` — тексты снимка (`install`, `models`, `releases`) разобраны; прочие
      имена каталога `/etc/df` — по листингу каталога, без открытия файлов снимка: файл схемы
      этапа > 1 — `не проверено (этап N)`; неизвестный файл — `нарушено`;
    - `release.active` — `current` → запись `allowed`;
    - `release.digest` — пересчёт (`release_digest`, Task 7: исключён только
      `release.json`) = `release.json` = `releases.toml`; иначе `нарушено: релиз
      повреждён`;
    - `release.admin` — `admin_ready(layout, snap.release_id,
      expected_admin_digest=snap.entry.admin_digest,
      expected_python=snap.entry.admin_python)` — обе версии из записи снимка (rev 8, r11);
    - `release.inputs` — `manifest_sha`/`profile` релиза = `install.toml`;
    - `release.perms` — дерево root, без записи для группы и всех, без setuid, без ACL
      (`acl_present_path`; ошибка → `данные недоступны`);
    - `release.boundary` — `static_problems(release_dir, platform, expected)` пуст, где
      `expected = {r: expected_entrypoints(release_dir/"src"/r)}` для каждого репо релиза,
      и только после того, как `release.digest` в норме: `src/<r>` равен доверенному
      чекауту (доказано `verify_sources` при приёме и закреплено digest, R40/F14);
    - `trusted.paths` — `trusted_path_problems` для `/opt/df`, `/etc/df`, `/var/lib/df`,
      административного Python и команды;
    - `cli.approved`;
    - `build.user` — `verify_build_user` пуст.

    Отдельно, без влияния на уровень: `staging.quarantine` — предупреждение, если
    `staging/.quarantine` не пуст. Все проверки читают один `Snapshot` (Task 9), полученный
    входом команды в режиме читателя (Task 8a): без блокировки, по счётчику поколений.

    Без root (`euid != 0`) проверки, требующие чтения закрытых файлов, дают `данные
    недоступны: нужен sudo`.
  - `level1_reached(checks) -> bool` — все `ok`, кроме `не проверено (этап N)` у файлов
    поздних этапов.
  - `render_brief(checks) -> str` — «уровень 1: достигнут» или «уровень 1: не достигнут —
    <id>: <reason>» (первая блокирующая); `render_json(checks, probes) -> str`.
  - Раздел моделей выводится отдельно: роль → `probe_status`. Без root — `данные
    недоступны: нужен sudo`. В уровень 1 модели не входят (§2.4: пробы — уровень 2).
  - Предупреждение: совпадение `(harness, model)` у `author` и `reviewer` — строка
    «предупреждение: модели автора и ревьюера совпадают»; на уровень не влияет (§3.6).

- [ ] **Step 1: Failing tests**

```python
def test_clean_install_level1_reached(fake_installed): ...
def test_tampered_file_breaks_digest(fake_installed):   # §3.8 «подмена файла»
    (rel / "src/devtools/README.md").write_text("x")
    c = {x.id: x for x in level1_checks(snap(L), L, euid=0)}
    assert c["release.digest"].status == "нарушено"
    assert render_brief(...).startswith("уровень 1: не достигнут — release.digest")
def test_doctor_transitional(fake_installed): ...      # Transitional(7) → JSON state=transitional
def test_level1_uses_passed_snapshot_only(fake_installed): ...
    # снимок A передан; затем install.toml, models.toml, releases.toml и current меняются на
    # B; вызывается настоящая цепочка main(read=Stable(A)) → doctor_main → level1_checks и
    # раздел моделей, со всеми вложенными helpers (admin_ready, parse_models); audit hook:
    # нет open install.toml/releases.toml/models.toml/pending.toml и readlink current;
    # результат — по A (S1-R5-N2)
def test_level1_ignores_journal(fake_installed): ...
    # журнал пуст или без activate: done → уровень 1 достигнут; поддельный done без current → не достигнут
def test_pending_blocks_level1(fake_installed): ...
def test_quarantine_warns_without_blocking(fake_installed): ...
def test_admin_tamper_breaks_level1(fake_installed): ...
def test_acl_on_release_file_broken(fake_installed, monkeypatch): ...
def test_missing_file_is_unavailable_not_ok(fake_installed): ...
def test_later_stage_files_not_checked(fake_installed): ...
def test_unknown_etc_file_broken(fake_installed): ...
def test_same_model_author_reviewer_warns_only(fake_installed): ...
def test_json_lists_all_problems(fake_installed): ...
def test_doctor_transitional_via_public_entry(tmp_path): ...
    # из Task 8a (F4): настоящий вход `darkfactory-entry.py` (подпроцесс, -I -B -S) при
    # нечётном поколении → код «уровень 1 не достигнут», JSON {"level1": "not reached",
    # "state": "transitional", "generation": g}
```

- [ ] **Step 2–4:** FAIL → implement → PASS
- [ ] **Step 5: Commit** `feat(df): doctor level 1 from facts and model probe display`

### Task 13: Приёмка этапа 1 — root-тесты и CI на обеих платформах

**Files:**
- Create: `tests/df/observe.py` (наблюдатель root-доступов уровня ОС, §3.8 rev 8 r5),
  `tests/df/test_acceptance_root.py`, `tests/df/fixtures/proj/` (минимальный
  uv-проект: `[project.scripts] dfprobe = "dfprobe:main"`, одна зависимость с маркером
  платформы, build-backend `hatchling`, `uv.lock`), `.github/workflows/df-stage1.yml`
- Modify: `tests/conftest.py` (маркеры `root`, `harness`, `network`)
- Create: `docs/evidence/2026-10-XX-df-stage1-acceptance.md` (по факту прогона)

**Interfaces:**
- Consumes: `install_steps`, `run_steps`, `drive`, `install_actions`, `level1_checks`,
  `seal`, `accept_copy(_hook=…)`, `receive_tree(_hook=…)`, `cleanup_tree`,
  `retry_quarantine`, `classify`, `run_as`, `spawn_as`, `whoami_as`, `main`; строки
  `sync-note` журнала `drive` (Task 7, R33.F10) и `distributions-note` шага `verify`
  (Task 7a).

Root-тесты (`@pytest.mark.root`) пропускаются, если не выполнены `os.geteuid() == 0` и
`DF_ROOT_ACCEPTANCE == "1"`. Раскладка — `Layout(<канонический tmp>)`, родители проходят
политику §3.10 (tmp-корень создаётся root `0755`). Пин профиля указывает на локальный
bare-репо с фикстурным проектом (`file://`) и подставляется в обход `parse_profile` —
правило «только github.com» проверено в Task 2. Пользователь `df-build` — настоящий,
созданный `create_build_user`.

**Положительные контроли — общий фикстур `controls`** (§3.8). До любого отрицательного
теста:
- `whoami_as("df-build")` = записи `accounts.toml`;
- запись в `build_home` от `df-build` проходит;
- контрольный адрес (локальный HTTP-сервер на внешнем интерфейсе runner-а) доступен от
  `df-build` с `network=True`.

Провал контроля — `pytest.fail("инфраструктура: …")`, не skip и не PASS. Каждый
отрицательный тест дополнительно требует `RunResult.launched`.

**Наблюдатель root-доступов** (`tests/df/observe.py`, §3.8 «Наблюдение», rev 8 r5):
- `observe_root_access(sources: Sequence[Path]) -> ContextManager[Observation]`;
  `Observation.status: Literal["observed", "unobserved"]`, `violations: list[Access]`,
  `Access(pid: int, euid: int, op: str, path: str, exe: str)`;
- Linux — `auditctl` с ключом прогона: `-a always,exit -F arch=b64 -S
  open,openat,openat2,getdents64,execve -F dir=<источник> -F euid=0 -k df-r5-<run>`;
  события — `ausearch -k … --format raw` после выхода из контекста; правила снимаются в
  `finally`;
- macOS — `eslogger open readdir exec --format json` (Endpoint Security) на время
  контекста; нарушение — путь под источником и `audit_token.euid == 0`;
- нет `auditctl`/`eslogger`, нет прав (TCC Full Disk Access для `eslogger`) или
  наблюдатель не увидел ни одного события положительного контроля (`open` файла источника
  от root внутри контекста перед тестом) → `status="unobserved"`;
- разрешённые операции над закреплённым родителем (`fstatat`, `renameat`, `unlinkat` по
  имени дерева) этими событиями не являются.

Step 0 фиксирует имена событий `eslogger --list-events` и поддержку `-F dir` для перечня
системных вызовов на версиях ОС CI. Тест с `status="unobserved"` — `pytest.xfail(
"наблюдатель недоступен: гарантия §3.3 не подтверждена")`, а evidence пишет «не
подтверждено» для этой гарантии; это не PASS. Аудит-хук Python остаётся дополнительным
локальным контролем (Task 5).

- [ ] **Step 1: Тесты по §3.8, по одному на строку**

```python
def test_clean_install_reaches_level1(root_world): ...
@pytest.mark.parametrize("action", ["build-dir", "build", "seal", "copy", "verify", "admin",
                                    "register", "activate", "launcher", "commit-config"])
def test_rerun_after_interrupt(root_world, action): ...   # прерывание внутри действия, повтор drive
def test_real_tar_from_build_user_accepted(root_world): ...
    # tar от df-build через spawn_as → receive_tree: запись корня, длинные/не-ASCII имена,
    # обе ссылки; digest итога = digest источника
def test_clean_pass_walks_automaton_in_order(root_world): ...
@pytest.mark.parametrize("point", ["member", "mid-copy"])
def test_crash_inside_accept_real_fs(root_world, point): ...
@pytest.mark.parametrize("after", ["activate", "launcher"])
def test_crash_after_activation_real_fs(root_world, after): ...   # повтор: тот же id, команда есть, pending удалён
@pytest.mark.parametrize("stop_after", ["intent", "attr:*", "home-mkdir", "home-chown",
                                        "home-rename"])
def test_partial_account_creation_resumed(root_world, stop_after): ...
    # повтор до ready; затем whoami_as("df-build") = записанным UID/GID (S1-R5-N3).
    # attr:* — настоящая остановка после каждой команды ACCOUNT_ATTRS этой ОС (macOS:
    # после RecordName и каждого dscl-атрибута; S1-R6-N2) — проверка границы примитивов
    # ОС §3.3: частичная запись видна, повтор дописывает только недостающее
@pytest.mark.parametrize("case", ["record-before-intent", "partial-with-conflicting-gid"])
def test_conflicting_record_not_adopted(root_world, case): ...
    # предсуществующая чужая запись / частичная запись с чужим PrimaryGroupID → StepFailed,
    # запись не тронута
def test_foreign_home_dir_not_adopted(root_world): ...
    # предсуществующий каталог root на домашнем пути → StepFailed, каталог не тронут
def test_tamper_after_install_doctor_broken(root_world): ...
@pytest.mark.parametrize("race", ["dir-to-link", "link-retarget", "file-to-fifo",
                                  "hardlink-root-only-file", "saved-fd-writer",
                                  "growing-file", "growing-tree"])
def test_live_writer_during_copy(root_world, race): ...
    # писатель — настоящий процесс df-build, синхронизация через _hook.
    # Ожидание: TreeError либо итог как обычное дерево; root-only sentinel в итог не попал
    # (tar от df-build его не прочитал); observe_root_access(источники) на всём пути —
    # запуск, классификация, seal, приём, очистка: violations пусты, status="observed";
    # пределы соблюдены; производитель завершён; open назначения от df-build → EACCES
@pytest.mark.parametrize("twin", ["root-child-cat", "root-ctypes-open", "prepare-dir-reopen"])
def test_root_access_observer_twins(root_world, twin): ...
    # prepare-dir-reopen (S1-R8-N2): prepare_dir, повторно открывающий releases/<id> по
    # пути после fchown, даёт violation; настоящий prepare_dir в охвате наблюдения (граница
    # — fchown) — violations пусты, status="observed"
    # двойники S1-R5-N4 на обеих ОС: root-потомок /bin/cat читает файл источника и
    # отбрасывает данные; root-процесс читает файл через ctypes libc.open — оба дают
    # violation; sentinel в итоге при этом отсутствует (аудит-хук Python оба пропускает)
def test_tar_failure_as_root(root_world): ...
    # R33.F35: root-вариант теста отказа tar Task 7 — файл, нечитаемый для df-build;
    # bsdtar обрывает поток со stderr «tar: (null)» (отказ верен, текст беден —
    # записать в evidence), GNU tar — ненулевой код (2)
def test_cleanup_of_000_tree(root_world): ...
    # df-build создаёт непустое дерево, ставит корню и вложенным каталогам 000 и
    # останавливается: первичная очистка в releases/, повтор карантина и очистка в
    # build_home удаляют дерево; observe_root_access — как выше (S1-R4-N4, S1-R5-N4);
    # GNU find на Ubuntu
def test_forked_producer_reaped_via_spawn_as(root_world): ...
    # tar от df-build через настоящий spawn_as подменён обёрткой, которая порождает
    # производителя и не ждёт его: после receive_tree нет процессов с UID df-build,
    # частичная копия root 0700 осталась (S1-R10-N3), writer_lock свободен — на обеих ОС
    # (S1-R4-N3); следующий darkfactory install убирает её маршрутом copy-partial
def test_gnu_padding_with_small_pipe(root_world): ...
    # Ubuntu: F_SETPIPE_SZ = 4096, GNU tar -b 64 (record 32 КиБ): поток принят без отказа
    # по сроку; выравнивание входит в max_stream_bytes
def test_silent_producer_released(root_world): ...
    # tar от df-build подменён на «пишет 1 КиБ и спит»: отказ за срок, процесс df-build убит,
    # частичная копия root 0700 до следующего запуска; после выхода команды writer_lock
    # свободен; повтор с исправным tar — copy-partial удаляет её и доходит до committed
def test_cleanup_of_growing_tree_quarantined_within_budget(root_world): ...
    # писатель df-build создаёт файлы быстрее find -delete: за budget+ε остаток в
    # .quarantine, установка идёт дальше; аудит root-операций — как выше
def test_quarantine_removed_by_next_install(root_world): ...
    # писатель остановлен → следующая установка (retry_quarantine) удаляет остаток
def test_build_user_after_seal_cannot_touch_destination(root_world): ...
    # процесс df-build стартует после seal: читает и меняет staging (так и задумано), но не
    # может открыть releases/<id> на запись; итог принят как обычное дерево или отказ
@pytest.mark.parametrize("target", ["/etc", "../../..", "{LR}/../../../../etc/passwd", "chain"])
def test_dangerous_link_in_artifact_refused(root_world, target): ...
def test_internal_absolute_links_survive_rename_and_copy(root_world): ...
def test_missing_entrypoint_and_broken_help_refused(root_world): ...
def test_runs_after_build_dir_and_cache_removed(root_world): ...      # dfprobe --help от df-build без сети
def test_boundary_on_real_release(root_world): ...
    # R40/F9, §2 предполётной проверки Task 8: настоящие static_problems и transfer_check
    # по релизу, собранному build.py, на обеих ОС; на Ubuntu записать то, что на macOS
    # измерено, а на Linux нет: линковку PBS-интерпретатора с libpython3.X.so.1.0 (RUNPATH
    # $ORIGIN/../lib) и читается ли тогда lib/libpython3.X.so.1.0._pth; RPATH/RUNPATH
    # колёс core (manylinux .libs, soname без RPATH); порог перехода uv на sh-трамплин;
    # sys.path обоих запусков (ж); состав python/<ключ>/bin у PBS на Linux — замерен
    # контроллером 22.10 (одноразовая VM podman amd64, uid 1000, uv 0.11.23, `uv python
    # install 3.12.13`): те же 14 имён, что на macOS; там же python/ = {.gitignore, .lock,
    # .temp, ссылка-алиас, каталог ключа}, python/<ключ> = {BUILD, bin, include, lib,
    # share} — как на macOS, python/<ключ>/lib/lib на Linux нет; CI подтверждает повторно;
    # формы текста ссылок venv и home: контроллер замерил на Linux amd64 22.10 (podman,
    # uid 1000, uv 0.11.23) с argv сборки (`uv python install --no-bin <ключ>`, `uv sync
    # --locked --no-editable --no-default-groups --project … --python
    # <rel>/python/<ключ>/bin/python3.12`) — те же формы с алиасом и тот же home, что на
    # macOS: закрепления замерены на обеих ОС, CI подтверждает; $ORIGIN у библиотеки,
    # загруженной через ссылку-файл в другом каталоге (glibc берёт каталог пути загрузки,
    # (в) — реальный каталог; ревью Task 8, круг 1, nit 5). Расхождение с правилами
    # (в)/(ж) — находка, не правка теста
@pytest.mark.parametrize("case", ["uid0", "admin-group", "foreign-home", "not-in-accounts"])
def test_preexisting_build_user_refused_before_build(root_world, case): ...
    # отдельная учётка-приманка; ни одного процесса сборки не запущено
@pytest.mark.parametrize("case", ["builder-owned-parent", "intermediate-link", "acl-write"])
def test_untrusted_cli_refused_before_exec(root_world, case): ...
@pytest.mark.parametrize("target", ["release", "journal", "etc", "admin"])
@pytest.mark.parametrize("op", ["create", "write", "rename", "delete", "chmod"])
def test_build_user_cannot_modify(root_world, controls, target, op): ...
    # на контрольных объектах, созданных тестом; парная операция в build_home проходит
def test_entrypoint_has_no_network(root_world, controls): ...
    # контрольный адрес: с network=True доступен, с network=False — ошибка соединения
def test_broken_run_as_fails_suite(root_world, monkeypatch): ...
    # run_as всегда возвращает 1 → controls падает «инфраструктура», а не PASS
def test_darkfactory_command_works_and_is_immutable(root_world): ...
    # /opt/df/bin/darkfactory doctor и models list работают; два запуска не меняют digest admin и релиза
def test_no_df_in_path(root_world): ...   # ни /opt/df/bin/df, ни /usr/local/bin/df не созданы
def test_admin_python_on_real_host(root_world): ...
    # Task 8a, F1/F8: на обеих ОС настоящий install_admin_python от root — точный ключ
    # build.python_key (на Linux — вариант сборки, который выбрал uv: ключ совпал, A
    # истинен, повтор маршрута не зацикливается), проверка всего дерева проходит; после
    # rename sys.prefix == admin_python/<ключ>, ни одна ссылка не указывает в .staging;
    # испорченная загрузка через локальное зеркало отвергнута. Linux до этого прогона не
    # замерялся — расхождение с правилами F1/F7/F8 — находка, не правка теста
def test_step2_and_lock_under_setgid_parents_real_linux(root_world): ...
    # из Task 9 (9-C I5, RC2-C1): Ubuntu, настоящая ФС — /opt и /var/lib с 2755; bootstrap
    # init доходит до committed; каталоги шага 2 и цепочки блокировки — точно в своих
    # режимах (без S_ISGID), каталоги оператора не тронуты. В Task 9 проверено только
    # эмуляцией (R9 модели); расхождение — находка
@pytest.mark.parametrize("crash", ["process", "os"])
def test_account_created_through_main_linux(root_world, crash): ...
    # из Task 9 (9-C Minor 8, 9-D1 concern 5): настоящие create_build_user/useradd и
    # verify_build_user через bootstrap init (main) на Ubuntu; прерывание после rename
    # домашнего каталога, повтор через main — committed; в Task 9 исполнен только путь
    # платформы хоста (macOS)
def test_linux_loader_residuals(root_world): ...
    # из Task 5 (R26, решение владельца 22.10): Linux-гарантия approved_cli_problems не
    # подтверждена до этого прогона — замерить остатки модели загрузчика: подкаталоги
    # glibc-hwcaps/tls системных каталогов и записи ld.so.cache вне ld.so.conf (сам
    # ld.so.conf с include код уже обходит);
    # утверждённый CLI на Ubuntu не тянет библиотеку мимо SYSTEM_LIB_DIRS; расхождение —
    # находка, не правка теста
```

- [ ] **Step 2: Где идёт root-прогон.** Только в одноразовых выделенных окружениях macOS
  arm64 и Ubuntu x86_64, предпочтительно CI (Step 3); на рабочей машине человека — нет.
  Команда прогона:
  `sudo env "PATH=$PATH" DF_ROOT_ACCEPTANCE=1 uv run --frozen pytest -q -m root tests/df`.
  Два исхода не смешиваются:
  - **положительные контроли** (`controls`, выше) идут до любого отрицательного теста; их
    провал — `pytest.fail("инфраструктура: …")`, прогон провален целиком, гарантии не
    выводятся;
  - **наблюдатель ОС недоступен** (`Observation.status == "unobserved"`: нет
    `auditctl`/`eslogger` или прав) — прогон не провален, но гарантия «root не обращается к
    объектам источника» в evidence — «не подтверждено»; для неё нужна отдельная одноразовая
    тестовая машина, PASS остальных тестов её не заменяет.
- [ ] **Step 3: Workflow `df-stage1.yml`.** Матрица `macos-15` (arm64; `macos-14` снимается
  GitHub 2 ноября 2026) и `ubuntu-24.04` (x86_64). Первый шаг каждой ячейки — явная
  проверка архитектуры: `test "$(uname -m)" = arm64` / `test "$(uname -m)" = x86_64`;
  несовпадение — провал, не skip.
  - Действия запинены теми же SHA, что в `ci.yml`; `permissions: contents: read`;
    `persist-credentials: false`.
  - Шаги: `uv python install 3.12` → `uv sync --frozen` → обычный
    `uv run --frozen pytest -q tests/df` → root-прогон из Step 2 (контроли — внутри него,
    до отрицательных тестов).
  - `uv` закреплён на `UV_PINNED_VERSION` (0.11.23) и здесь, и в `ci.yml` (Task 8a): иначе
    `test_uv_refuses_corrupt_download` в CI пропускается, а `install_admin_python`
    отказывает по `UV_ADMIN_CONFIRMED`. Маркер `network` регистрируется в
    `tests/conftest.py`; opt-in `DF_NETWORK_TESTS` теста
    `test_admin_python_relocation_precondition` (Task 8a, F9) заменяется этим маркером, и
    тест идёт в обеих ячейках матрицы.
  - До прогона — **проба механизмов** тем же путём, что `run_as`/`spawn_as` (под `sudo`
    runner-а, без пароля). Неуспех — провал workflow с именем механизма, не skip:
    - **ubuntu-24.04**:
      - `sudo unshare --net -- setpriv --reuid=nobody --regid=nogroup --init-groups --
        /usr/bin/id -u` → UID `nobody`;
      - `sudo unshare --net -- /usr/bin/true`;
      - `readelf -d /bin/sh`;
      - `tar --version` (GNU);
    - **macos-15**:
      - `sudo -n -u nobody /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny
        network*)' /usr/bin/id -u` → UID `nobody`;
      - `otool -L /bin/sh`.

    Совместимость утверждённого `tar` с приёмником проверяют не эти пробы, а
    `test_real_tar_stream_accepted` (Task 3, обычный прогон) и
    `test_real_tar_from_build_user_accepted` (root-прогон) на обеих ячейках матрицы.
  - Linux-ветки Task 7 (`verify_chmod` — `fchmod` по дескриптору, обход окружения с
    `lib64 -> lib`, путь дескриптора через `/proc/self/fd` в `fake_layout`, отказ GNU tar
    ненулевым кодом в тесте отказа tar Task 7) исполняет уже CI PR (`tests/df` на ubuntu);
    обычный прогон Task 13 повторяет их на обеих ячейках матрицы.
  - Матрица сбоев Task 9 на **настоящей ФС Linux** (из Task 9, 9-D2 M-6): обычный
    прогон ячейки ubuntu исполняет `test_crash_matrix` и закреплённые двойники
    (`tests/df/test_crash_matrix.py`) — числа прогонов закреплены литералами, а
    расписания синхронизаций — по имени (`Schedule.anchor`); ответ `unreachable` на Linux
    — перезакрепить, не дефект. Медленный набор (`uv run --frozen pytest -q -m slow
    tests/df`, около 90 минут) в CI не входит; был ли он прогнан на Linux — строка
    evidence.
- [ ] **Step 4: Run** — push ветки; обе ячейки матрицы зелёные. Ссылку на прогон записать в
  evidence. Evidence разделяет:
  - «фикстурная приёмка механизма: pass (macOS arm64, Ubuntu x86_64)»;
  - «наблюдатель ОС: observed / unobserved — не подтверждено, нужна отдельная машина» — по
    ячейке (провал контролей сюда не попадает: это провал прогона);
  - «проверено внедрением сбоев: …» и «спроектировано, не испытано: отключение питания»
    (§3.3);
  - «FULLFSYNC: каталоги без `F_FULLFSYNC` — строки `sync-note` журнала `drive`» по
    ячейке macOS (решение (а), Task 7, R33.F10); строк нет — «все каталоги приняли
    F_FULLFSYNC»;
  - рядом — строка `distributions-note` шага `verify` по ячейке (Task 7a): сверка состава
    названа как есть — «метаданные распределений согласованы с lock (заявлено); не
    доказывает отсутствия лишнего кода и происхождения файлов релиза»;
  - версия `uv` прогона = версии в имени `UV_PROJECT_KEYS_<версия>`; иная — «список настроек
    uv не перепроверен для <версия>», а не pass;
  - остаточный риск (§3.2, R33.F28): процессы `df-build`, пережившие сбой установщика во
    время `run_build`, могут писать в будущий `releases/<id>`;
  - «изоляция харнессов: <версии> verified / not verified» (Task 11 Step 3);
  - «`core` live: blocked / not run (ждёт `df-manifest-profiles`)».

  Этап 1 по §10 при этом **не закрыт**.
- [ ] **Step 5: Commit** `test(df): stage-1 acceptance on macOS arm64 and Ubuntu`

### Task 14: Манифест зонтика, документация, хвост

**Files:**
- Modify: `CLAUDE.md` (строка таблицы инструментов `df/`), `README.md` (раздел «Установка
  DarkFactory: этап 1»), `TODO.md`

- [ ] **Step 1: Inbox-issue в зонтик** — `gh issue create -R
  andrei-shtanakov/ai-orchestrators-workspace --label inbox`, `slug: df-manifest-profiles`,
  `from: devtools`. Тело:
  - схема секции из Task 2;
  - требование: у членов профиля полный `sha` (40 hex), включая `sha` у `spec-runner`;
  - пин `devtools` — на коммит с пакетом `df`;
  - lock-файлы членов в пределах поддерживаемых конструкций §3.5 (иначе установка откажет
    «неподдерживаемый lock»), а `[tool.uv]` членов — в пределах `UV_PROJECT_KEYS_0_11_23`,
    без `uv.toml` (§3.2; иначе отказ до сборки);
  - **решение владельца 22.10** (R37 закрыт, R40/F20): группы `core` для `devtools` —
    только `governance`. `conductor` (группа службы в `default-groups`
    `devtools/pyproject.toml`) — отдельный профиль позже, не в v1; сборка идёт с
    `--no-default-groups`, поэтому его зависимости (`pyjwt[crypto]`, `jsonschema`) в `core`
    намеренно не попадают. Тело issue называет решение, а не вопрос. Эталон
    `devtools.governance-conductor` в `test_lockset.py` остаётся: он проверяет обход lock, не
    профиль;
  - бывший блокер живой установки `core` (R40/F17): `disp --help` (disputatio fc402fe)
    падал `ModuleNotFoundError: anyio` (`anyio` был только в `[dependency-groups]`, сборка
    идёт с `--no-default-groups`). Исправлено disputatio#160 (master 920b63f: `anyio` в
    зависимостях `[project]`; disputatio#159 закрыт; контроллер проверил на 920b63f
    `uv sync --locked --no-editable --no-default-groups` и `disp --help` → код 0). Пин
    disputatio в профиле `core` — 920b63f или позже.

  В `TODO.md` — пункт `@id:df-installer-stage1-live`: «живая установка профиля `core` на
  macOS arm64 и Ubuntu x86_64; проверка совместимости пинов и lock-файлов (`uv sync
  --locked` каждого члена на его SHA); закрывает приёмку этапа 1 по §10»,
  `@blocked_by:todo://ai-orchestrators-workspace/df-manifest-profiles` (disputatio#159
  решён disputatio#160, 920b63f; пин disputatio в `core` — 920b63f или позже). Там же — пункт
  с `@trigger` на смену закреплённой версии `uv`: перепроверить замером
  `UV_PROJECT_KEYS_<версия>` (дополнение A1/A2 предполётной проверки Task 7a) и таблицу
  записи пути корня в `tests/df/fixtures/locks/SOURCES.md`, переименовать константу под
  новую версию, перезахватить эталоны.
- [ ] **Step 2: Документация** — строка `df/` в таблице CLAUDE.md и раздел README:
  - что делает этап 1;
  - первая установка: `sudo /usr/bin/python3 -I -B -S <чекаут devtools>/df/bootstrap.py init
    --manifest-repo … --manifest-sha … --profile core --cli git=/usr/bin/git --cli
    tar=/usr/bin/tar --cli uv=/usr/local/libexec/darkfactory/bin/uv`. На
    macOS вместо `/usr/bin/python3` — любой Python ≥ 3.12 по выбору человека. Root исполняет
    код из чекаута человека — доверие к этому чекауту на совести человека;
  - дальше: `sudo darkfactory doctor`, `sudo darkfactory models set …`;
  - ограничения v1 из Task 9 (спека §3.8, §3.3): отказ `damaged` под незавершённой
    установкой и ручное восстановление (шаги из текста отказа; `current` вручную не
    возвращать); снятие блокировки переустановки удалением `pending.toml` (R53, §3.8:
    не подтверждает завершённость прежней установки) и второе ручное снятие (§3.8);
    root-владелец в карантине и его ручное снятие;
  - почему не `df`: имя занято стандартной командой;
  - утверждённые CLI: только пути root (§3.10), способ подготовки `uv`;
  - приёмка — CI `df-stage1`; живая установка ждёт манифест.
  Тест `test_documented_bootstrap_argv_parses` (в `tests/df/test_cli.py`) берёт argv из
  README, разбирает его парсером `init` и строит `InstallConfig` без исполнения установки;
  тот же argv без `--cli tar=…` → `ConfigError` до первого изменения (S1-R5-N6).
- [ ] **Step 3: Полный прогон** `uv run --frozen pytest -q`, `uv run --frozen --group
  governance pytest -q` (с `GOVERNANCE_REQUIRED=1`), ruff (обе команды), pyrefly — зелёные.
- [ ] **Step 4: Commit** `docs(df): stage-1 usage (darkfactory); umbrella manifest request`

---

## Вне этого плана

- Роли `df-exec`/`df-review`/`df-publisher`, sudoers, `df-launch`, издатель, `df-run-start`,
  проверки от имени ролей и пробы с настоящими ролями — **этап 2**. Туда же платформенные
  предпосылки SQLite (§6.1, `fullfsync`) и minor r7 №2 (новый `request_id` после смены снимка
  `perimeter-status`).
- minor r7 №1 (полный состав candidate-PR — дифф к базе, тест с посторонним файлом в раннем
  коммите) — план этапа 2, раздел издателя (§5.4).
- Пин моделей прогона и событие смены при `resume` (§3.6) — пишет `df-run-start`, **этап 2**;
  этап 1 даёт для него `resolve` и `config_hash`.
- `darkfactory update` (§7.4), `releases.toml` `revoked` в рантайме — этапы 2 и 4. Здесь только
  схема и проверка `allowed` при активации и во входе команды.
- Независимая сверка содержимого пакетов против хешей wheel (§3.5) — вне v1, сужение гарантии
  непривилегированного runtime.
- Испытание отключением питания (§3.3) — вне v1.
- `df-run-start` (этап 2) исполняется из административной среды до сброса привилегий (§5.8,
  rev 8 r2). Тест этапа 2: подменённый запускатель или интерпретатор релиза с UID-зависимым
  sentinel до сброса не исполняется.
- Живая установка реального профиля `core` — после inbox `df-manifest-profiles` (Task 14).
