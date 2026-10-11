---
spec_stage: tasks
status: draft
owner_role: stream-owner
version: 2
generated_by: fleet-agent
generated_at: "2026-10-11T21:00:00+04:00"
source_prompt_version: ""
validation: ""
approved_by: ""
---

## Milestone 1: песочница авторского агента, v1 — claude-путь (дизайн rev 3)

Настоящая граница записи для авторского агента раннера на macOS: внешняя
обёртка `sandbox-exec` вокруг `ops.author` с claude. Дизайн —
`docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md`, **rev 3**
(одобрен и влит владельцем 2026-10-11, #594): §11 активы, необходимые записи и
модель противника; §12 модель доступа; §13 отказ запуска и Q1; §14 допуск
исполняемого файла; §15 безопасный ввод-вывод, проба и перенос; §16
сценарии; §17 пределы гарантий. Замеры — F14–F28. Из rev 2 действуют §3 (env,
учётные данные модели), §4 п.1–4, §5, §9 (трипвайр), §10 п.3–4.

**Это песочница с ограниченными гарантиями, не полная изоляция агента** (§17):
задачи не вправе обещать больше — ни в коде, ни в сообщениях, ни в
документации. Одобрение дизайна — не подтверждение защиты: защита считается
действующей только после TASK-007 (живая приёмка).

Уже влито до этой спеки: allowlist env (#483), изоляция конфигурации claude и
трипвайр конфиг-поверхности (#489). Вне v1 (§11, §13): disp, codex и любой
харнесс, кроме claude, — отказ до запуска агента.

Ревизия 2 tasks-спеки заменяет ревизию 1 (под rev 2 дизайна) целиком.

### Шаги оператора (вне spec-runner)

`harness_guard: strict` (`spec-runner.config.yaml`) валит попытку, тронувшую
`contracts/` или authority-root, до гейтов — поэтому правки контракта не
задачи spec-runner, а PR оператора, мерж — `human-merge.sh`. Задачи ниже
контракт только **читают** и в тестах используют его копию во временном
каталоге.

- **H1 — каркас контракта допуска** (до TASK-003): `contracts/author-sandbox/v1/admitted.toml`
  с пустым списком записей `(harness, version, realpath, sha256, macos_major,
  profile_sha256)` и README; префикс `contracts/author-sandbox/` в
  `AUTHORITY_ROOT_PREFIXES` (`contracts/authority-root/v1/paths.env`); тест в
  `tests/test_governance_authority_root.py` / `tests/test_merge_pr.py`, что
  `merge-pr.sh` отказывает агентскому PR, трогающему допуск.
- **H2 — первая запись допуска** (после TASK-006, до TASK-007): оператор
  запускает `make sandbox-admit`, проверяет протокол и вносит предложенную
  запись в `admitted.toml` тем же PR, что и протокол `docs/evidence/`.

**Переходный период.** Между мержем TASK-005 и H2 авторинг отказывает **для
всех харнессов, включая claude** («не проверен под песочницей»): допуск пуст.
Это безопасный останов, а не сбой; TASK-005 и H2 мержить подряд. До TASK-005
действует сегодняшнее положение (без песочницы), и ничто в коде не называет
его защищённым.

**Платформа тестов.** CI — Ubuntu (`.github/workflows/ci.yml`). Модули
импортируются на Linux без ошибок; поведение ядра Darwin (`O_NOFOLLOW_ANY`,
`sandbox-exec`, `sw_vers`, PTY + `TIOCSTI`) проверяется тестами с маркером
macOS и skip на другой ОС с названной причиной; macOS-прогон обязателен до
мержа каждой задачи, его вывод — в описании PR.

### TASK-001: Безопасный ввод-вывод раннера — примитив и контракт назначения
P1 | TODO   Est: 1d

Новый модуль `governance/safe_io.py` — единственный способ, которым раннер
касается каталога вызова и пишет файл узла в бандл (§15, F27). Чистая
библиотека: ни профиля, ни запуска агента.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §15, F27; §16 Z11, Z14, Z15

**Checklist:**
- [ ] корень канонизируется `realpath` один раз; все открытия — с `os.O_NOFOLLOW_ANY` (ссылка в любом компоненте — отказ); неканонизированный путь (`/var/…`) отказывает — тест фиксирует это как поведение
- [ ] на ОС без `O_NOFOLLOW_ANY` модуль импортируется, а любой вызов отказывает понятной ошибкой «только macOS» (переносимый тест)
- [ ] `read_regular(root, rel, limit, timeout)`: `O_RDONLY|O_NONBLOCK|O_NOFOLLOW_ANY`, `fstat` открытого дескриптора — только обычный файл с `st_nlink == 1`; читается не более `limit + 1` байт с ограничением времени; больше `limit` — отказ
- [ ] `list_tree(root, rel)` без следования ссылкам: ссылка и спецфайл — в перечне как есть, не раскрываются
- [ ] `ensure_dirs(root, rel)` и `publish(root, rel, data)`: родители — цепочкой дескрипторов (`O_DIRECTORY|O_NOFOLLOW_ANY`, `mkdir(…, dir_fd=)`); временный файл `O_CREAT|O_EXCL|O_NOFOLLOW` в закреплённом каталоге; `rename(…, src_dir_fd, dst_dir_fd)` поверх конечного имени; конечное имя — каталог — отказ
- [ ] `validate_bundle_dir(target_dir, bundle_dir, git_dirs)`: непустой относительный, без `..`, абсолютных и пустых компонентов, не `.`, без компонента `.git`; после канонизации строго внутри `target_dir`, не пересекается с gitdir/common dir — иначе отказ (Z15; переносимый тест)
- [ ] macOS-тесты источника (Z11): файл — symlink; компонент пути — symlink на каталог; hardlink (`st_nlink > 1`); FIFO — отказ **без зависания** (тест с таймаутом); сверх потолка; рост файла после открытия — читается не больше `limit + 1`
- [ ] macOS-тесты назначения (Z14): висячая ссылка на месте узла на `<tmp>/target/.claude/settings.local.json` — заменена самой ссылкой, цель НЕ создана; ссылка-каталог в пути бандла — отказ; hardlink на месте узла — новый inode, внешний двойник побайтно неизменен; конечное имя — каталог — отказ
- [ ] при любом отказе вне `root` ничего не создано и не изменено (снимок внешних файлов до/после)
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_safe_io.py` зелёный (на macOS — без skip); ruff и pyrefly чисты

**Touches:** governance/safe_io.py, tests/test_governance_safe_io.py

### TASK-002: Каталог вызова и генератор профиля
P1 | TODO   Est: 1d

`governance/sandbox.py`: создание свежего каталога вызова вне чекаута devtools
и профиль Seatbelt из его `realpath` (§11, §12). Генератор — чистая функция;
настоящим `sandbox-exec` его проверяет TASK-004.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §11, §12, F21, F22, F28; §16 Z6, Z7

**Checklist:**
- [ ] корень `<calls>` = `$XDG_STATE_HOME/devtools/agent-calls` (по умолчанию `~/.local/state/devtools/agent-calls`); `<run_id>/<call>` и `stage/`, `tmp/`, `claude-config/` создаются через `safe_io` (TASK-001); `<call>` — уникальный, существующий — отказ (Z6, macOS-тест)
- [ ] `realpath` каталога вызова не пересекается (ни предок, ни потомок) с `target_dir`, gitdir и common dir цели (`git rev-parse --path-format=absolute --git-dir --git-common-dir`), чекаутом devtools и `run_dir` — иначе отказ (Z7); тест на цель «сам devtools» проходит (корень вне чекаута)
- [ ] профиль ровно по §12: `(allow default)`, `(deny file-write*)`, разрешения только `stage/`, `tmp/`, `claude-config/`, `/dev/null`, `/dev/fd/*`; `(deny file-write* (regex #"(^|/)\.git(/|$)"))`; `(deny file-ioctl (regex #"^/dev/tty"))`; запрет чтения `~/.config/{review,gh,ai-prosto}`, `~/.ssh`; mach-запрет трёх сервисов связки и префиксов `com.apple.coreservices.`, `com.apple.lsd.`
- [ ] переносимые юнит-тесты генератора: каждый пункт присутствует; НЕТ разрешения записи на `/private/tmp`, `/private/var/folders`, `/dev/tty`, `/dev/ttys*`, `target_dir`; спецсимволы пути экранированы (путь с кавычкой и пробелом)
- [ ] `profile_template_sha256()` — хэш шаблона без подстановок, для записи допуска
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox.py` зелёный

**Depends on:** [TASK-001]

**Touches:** governance/sandbox.py, tests/test_governance_sandbox.py

### TASK-003: Проверка допуска исполняемого файла — без исполнения кандидата
P1 | TODO   Est: 0.5d

`admission_check()` по §14. Контракт только читается; предусловие — влит H1
(каркас `admitted.toml` и регистрация в authority-root).
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §14; §16 Z16

**Checklist:**
- [ ] `admission_check(contract_path)`: `shutil.which` в PATH раннера → `realpath` → sha256 через `safe_io.read_regular` → `sw_vers -productVersion` → сверка `(realpath, sha256, macos_major, profile_sha256)` с контрактом; версия — из записи; **кандидат не исполняется ни с каким аргументом**, включая `--version`
- [ ] нет файла контракта или он пуст — отказ «не проверен под песочницей» (это штатное состояние до H2)
- [ ] Z16 (macOS): подменённый в PATH исполняемый файл, оставляющий внешний маркер при любом запуске → отказ, маркера нет
- [ ] запись есть, но sha256/ОС/шаблон профиля не совпали — отказ с названным полем
- [ ] тесты на копии контракта во временном каталоге; `contracts/` не трогается
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox.py` зелёный

**Depends on:** [TASK-002]

**Touches:** governance/sandbox.py, tests/test_governance_sandbox.py

### TASK-004: Проба-канарейка — API и матрица запретов на настоящем `sandbox-exec`
P1 | TODO   Est: 1.5d

`run_probe(call, profile, env) -> ProbeResult` — самостоятельно проверяемый
API пробы (§4 п.1–4, §15) со структурированным результатом (пройдено / пункт
отказа). Подключение к авторскому вызову — TASK-005. Тесты — на настоящем
`sandbox-exec` (macOS; иначе skip с причиной); каждый запрет — с контролем без
песочницы там, где иначе не доказан, и с проверкой отсутствия файла-эффекта в
дополнение к коду возврата.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §4, §10 п.3, §15, F14–F21, F28; §16 Z1–Z10, Z18, Z19, N6

**Checklist:**
- [ ] Z1: обязаны НЕ удаться — часовые в `run_dir/edge-check/`, рядом с `ledger.jsonl`, в `$HOME`, в venv disputatio (§10 п.3); вне каталога вызова
- [ ] Z2 (часовые, неразрушающие): в `target_dir`, `target_dir/<bundle_dir>/` (в т.ч. рядом с соседним узлом), а при цели «сам devtools» — в `governance/` и рядом с `merge-pr.sh`
- [ ] Z3: часовые в gitdir и common dir настоящей цели; разрушающие пробы — на одноразовом стенде git-репо + worktree вне разрешённых корней: `unlink`, переименование, подмена файла `.git`, запись в `config`/`hooks`, в gitdir/common dir, во вложенный `.git`; стенд удаляется в `finally`
- [ ] для часовых: `O_CREAT|O_EXCL`, имя с UUID; каталоги бандла создаются ДО пробы (`safe_io.ensure_dirs`); засчитывается только `EPERM`, `ENOENT`/`EEXIST`/прочее — результат «сбой подготовки»; удавшийся часовой удаляется, результат «регрессия профиля»
- [ ] Z4: запись наружу через symlink, созданный заранее раннером, и через созданный изнутри — отказ, внешний файл неизменен
- [ ] Z5: `ln <внешний> <call>/stage/x` и `ln <.git-файл стенда> <call>/stage/y` — отказ; Z8: `mv <внешний> <call>/stage/` — отказ
- [ ] Z9: `launchctl submit` и `launchctl bootstrap gui/<uid>` пробного задания — ненулевой код, метки нет в `launchctl list`, файла-эффекта нет (контроль без песочницы — задание исполняется); `crontab` — отказ исполнения; `open -g` пробного `.app` — ненулевой код, `lsappinfo front` → NULL, файла-эффекта нет (контроль — эффект есть); Apple Events — контроль без песочницы обязан ответить, под песочницей ответа в пределах таймаута быть не должно, и результат пишется как «нет ответа», **не** как «доставка закрыта» (§17 п.7); код 0, таймаут или неоднозначность в остальных пунктах — результат «не действует»; задания и `.app` убираются в `finally`
- [ ] Z10: чтение приманки `~/.config/ai-prosto/.sandbox-bait` (раннер кладёт и снимает); связка ключей — 4 пробы §4 п.3; env внутри = allowlist вызова (равенство множеств)
- [ ] открытие `/dev/tty` — не удаётся (запуск пробы в новой сессии, F28)
- [ ] обязаны УДАТЬСЯ: запись в `<call>/stage/` (с созданием вложенных каталогов) и `<call>/tmp/`
- [ ] Z18 мутации, каждая краснеет своей пробой: (1) после запретов добавлено разрешение записи в gitdir/common dir стенда → часовой краснеет; (2) снят regex `.git` → запись во вложенный `.git` в заготовке краснеет; (3) обёртка убрана → пробы запретов краснеют; (4) env наследованием → проба env краснеет; метаданные настоящей цели неизменны
- [ ] Z19 на одноразовом PTY: контроль без песочницы подкладывает ввод `TIOCSTI`; под обёрткой — отказ отдельно каждым поясом (новая сессия; `file-ioctl`), отложенного ввода нет
- [ ] N6 (проба): цель без brief, бандла и его родителей — проба зелёная; отрицательный контроль с разрешённой записью в бандл — краснеет
- [ ] следы пробы убираются при любом исходе
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox_live.py` зелёный на macOS

**Depends on:** [TASK-002]

**Touches:** governance/sandbox.py, tests/test_governance_sandbox_live.py

### TASK-005: Обвязка авторского вызова — отказ, проба, запуск, перенос
P1 | TODO   Est: 2d

Включение v1: claude запускается только под песочницей, любой другой харнесс
и disp — отказ до запуска (§13, Q1). После мержа и до H2 авторинг отказывает
для всех харнессов (переходный период выше).
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §11, §12, §13, §15; §16 Z12, Z13, Z14, Z17, N1, N2, N6

**Checklist:**
- [ ] `authoring_preflight(state)`: ОС — macOS; `/usr/bin/sandbox-exec` есть; не вложенная песочница; харнесс резолвера (`AUTHOR_HARNESS` > `harness.env` > вшитый `codex`) = claude; `author_backend` ≠ disp; допуск (TASK-003); `validate_bundle_dir`; элемент связки токена claude
- [ ] **один предикат** `agent_launch_ahead(state, tree)`: в текущей волне `mode == author` И есть author-шаг, не `completed`, чей файл узла в дереве `tree` **отсутствует** — то же правило, по которому `_step_authoring` штатно пропускает готовый узел (`governance/runner.py:2627–2635`); ранний preflight вызывается только когда предикат истинен, поэтому ручной путь (узел положил оператор) агента не ждёт и проверкой не останавливается
- [ ] точки вызова — до любого изменения цели:
  1. `_step_branch`: **одна закреплённая ревизия базы** на всё решение — `fetch_branch(base)` (без изменения дерева; сбой — стоп без изменений) → `sha = head_sha(origin/<base>)` → `_decide_wave_mode` получает `sha` явным входом и читает узлы через `show_file(…, sha, …)` (сейчас он сам берёт `state.base_ref`, `governance/runner.py:567–574`) → если `mode == author` и предикат истинен по дереву `sha` — preflight → локальная `base`, которая не равна `sha` и не его предок (свои коммиты), — отказ «локальная база разошлась с origin» без изменения дерева → ветка волны создаётся `switch_to(target, branch, sha)` от **того же** `sha`; `mode` сохраняется вместе с `sha`; продвижение remote после `fetch` на решение и ветку не влияет; при `reapprove` preflight не вызывается — переодобрение агента не запускает и работает без допуска
  2. общий драйвер шагов (цикл по `_STEPS`, общий для `start` и `resume`): на `resume`, когда шаг ветки текущей волны уже `completed`, — preflight, если предикат истинен по рабочему дереву ветки волны, до следующего изменяющего шага (`_step_materialize_brief`)
  3. `reopen(manual=False)` (`governance/runner.py:1155`, `1234–1237`) — до `checkout_and_pull`, `switch_to` и `unlink` файла узла; `reopen(manual=True)` агента не запускает — preflight не вызывается ни на нём, ни на последующем `resume`, если оператор положил файл (предикат ложен)
  4. `_step_authoring` — непосредственно перед каждым реальным запуском агента
- [ ] тесты ревизии базы (`FakeOps.show_file` в тестах сейчас игнорирует `ref`, `tests/test_governance_runner.py:215–217` — фейк учит ref): local ≠ origin со своими коммитами → отказ без изменения дерева; remote продвинулся между `fetch` и созданием ветки → ветка от закреплённого `sha`, режим решён по нему; `fetch` не удался → стоп без изменений
- [ ] полный ручной цикл при `AUTHOR_HARNESS=codex` и пустом допуске (по образцу `tests/test_governance_runner.py:8067–8092`): `reopen(manual=True)` → файл положен → `resume` проходит без агента; файл не положен → `resume` отказывает до изменения цели
- [ ] отказ по харнессу — дословно по §13: «харнесс `<имя>` не поддерживается песочницей авторинга v1; поддерживается claude (`AUTHOR_HARNESS=claude`)»; disp — «disp вне v1»; нет флага, переменной или режима обхода; вшитое умолчание резолвера НЕ меняется
- [ ] парные тесты новой волны без сохранённого `branch-<w>.mode`: base со `stale`-узлами уровня → `reapprove` продолжает работу при пустом допуске и `AUTHOR_HARNESS=codex`; base без узлов уровня → `author` отказывает, рабочее дерево цели не переключено
- [ ] тесты отказов через настоящие входы `start`/`resume`/`reopen(manual=False)`/волну/повтор шага, с brief и без: не-macOS (подмена платформы), нет `sandbox-exec`, вложенная песочница, disp, codex (Z17: выбран env, `harness.env` и вшитым умолчанием), пустой допуск — для каждого: `stopped_author` с текстом причины, исполняемый файл-маркер агента не вызван, снимок цели и бандла **до входа** равен снимку после (brief не материализован)
- [ ] проба TASK-004 — перед **каждым** запуском агента, с тем же профилем и env; её отказ — `stopped_author` «песочница не действует: <пункт>», агент-маркер не вызван
- [ ] запуск: абсолютный путь из допуска, `sandbox-exec -f <профиль>`, `start_new_session=True`, `stdin=DEVNULL`, cwd = `target_dir`; env = `agent_env()` + `TMPDIR`/`CLAUDE_CODE_TMPDIR` = `<call>/tmp`, `CLAUDE_CONFIG_DIR` = `<call>/claude-config` (перенос `claude_isolation` из системного temp), токен, `SSL_CERT_FILE`
- [ ] промпт называет `bundle_dir` (чтение, `git hash-object`) и абсолютный `output_path` = `<call>/stage/<file>` (единственное место записи); DSL узлов не меняется
- [ ] таймаут — `SIGKILL` группе, `stopped_author` «таймаут», переноса нет (Z13); после штатного выхода — `SIGKILL` группе (гигиена)
- [ ] перенос: `safe_io.read_regular` файла узла; в заготовке ничего лишнего (`list_tree`), иначе стоп с перечнем в карантин; `safe_io.publish` в `target_dir/<bundle_dir>/<file>`; каталог вызова удаляется
- [ ] Z12 (интеграционно): отсоединённый потомок фейкового агента переписывает файл в заготовке после выхода — вход гейта (`_step_gate`) равен перенесённым байтам; гонки до/во время переноса (подмена родителя ссылкой, regular→FIFO, рост) — отказ или перенесённые байты, внешний файл неизменен
- [ ] Z14 (интеграционно): опасное назначение (висячая ссылка на `<tmp>/target/.claude/settings.local.json`; hardlink с внешним двойником) ставит **доверенный код теста** через тестовый шов между выходом агента и публикацией — заранее поставить нельзя: существующий узел раннер штатно пропускает (`governance/runner.py:2627–2635`), а сам агент в цель писать не может; фейковый агент пишет JSON-«хук» вместо узла; утверждения: агент вызван, публикация выполнена, у назначения новый inode, гейт прочёл опубликованные байты и отказал, после завершения конвейера внешние файлы побайтно неизменны, цель ссылки не создана
- [ ] отдельно: штатный пропуск уже существующего обычного файла узла сохраняется
- [ ] N1/N6 на фейковом агенте под настоящим профилем: первый запуск без brief и без родителей бандла — файл перенесён, `upstream_hashes` в промпте посчитаны по настоящему бандлу
- [ ] трипвайр конфиг-поверхности (#489) остаётся вокруг вызова как пояс
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_ops.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py` зелёный (на macOS — без skip)

**Depends on:** [TASK-001, TASK-003, TASK-004]

**Touches:** governance/ops.py, governance/runner.py, governance/sandbox.py, tests/test_governance_ops.py, tests/test_governance_runner.py, tests/test_governance_sandbox_live.py

### TASK-006: Процедура допуска `make sandbox-admit`
P1 | TODO   Est: 0.5d

Процедура §14, исполняет оператор с реальным харнессом. Результат —
протокол и **предложенная** запись допуска; в `admitted.toml` её вносит
оператор шагом H2.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §14, F22, F24–F26, F28; §16 N1, N2

**Checklist:**
- [ ] `make sandbox-admit` под профилем §12 с реальным харнессом (абсолютный путь кандидата): Bash `true`→0, `false`→1, `exit 3`→3; `cd` сохраняется; Write в `<call>/stage/a/b/node.md` с созданием родительских каталогов; отказ записи в дерево цели; снимки `$HOME`-поверхности и `/private/tmp` до/после — вне каталога вызова ничего; проба TASK-004 зелёная — по сырым `tool_result` из `stream-json`, не по пересказу модели
- [ ] не прошло — запись не предлагается; расширение профиля — изменение дизайна, не строка контракта
- [ ] вывод: протокол `docs/evidence/<дата>-author-sandbox-admit-<версия>.md` и строка для `admitted.toml` в stdout; `contracts/` процедура не пишет
- [ ] `make sandbox-admit` в справке Makefile; тест разбора stream-json на записанной фикстуре (переносимый)

**Depends on:** [TASK-005]

**Touches:** Makefile, governance/sandbox.py, tests/test_governance_sandbox.py

### TASK-007: Живая приёмка v1
P1 | TODO   Est: 1d

Позитивная половина на живых вызовах (§5, §16 N1–N6); предусловие — влит H2.
До этой задачи защита не считается действующей.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §5, §10 п.4, §16 N1–N6

**Checklist:**
- [ ] авторинг узла claude при **настоящем** расположении состояния раннера (`out/governance-runs`, `<calls>` по умолчанию) — для внешней цели И для цели «сам devtools»: файл перенесён, гейт зелёный, проба и проверки §15 зелёные
- [ ] N1: `upstream_hashes` узла совпадают с `git hash-object` настоящего бандла
- [ ] N3: в `stream-json` вызова — чтения соседних узлов и source-слоя и `git status/log/diff` с успешным результатом
- [ ] N4: успешный вызов модели и есть свидетельство доступа к её API — записать это соответствие явно
- [ ] N6: живой первый запуск на цели без brief, без бандла и его родителей — авторинг проходит
- [ ] сравнение качества (§10 п.4): контроль — **отдельная ручная процедура оператора вне пути авторинга раннера** (прямой вызов claude с тем же промптом в одноразовом клоне цели), без флага обхода в раннере; гейт и находки edge-check сопоставлены; если под обёрткой чего-то не хватает — в конфиг вызова явно из доверенного источника, НЕ открытием `~/.claude`
- [ ] отказ `AUTHOR_HARNESS=codex` на живом прогоне — текст §13, агент не запущен
- [ ] протокол (факты, sha, версии, стоимость) — в `docs/evidence/`

**Depends on:** [TASK-006]

**Touches:** docs/evidence/

### TASK-008: Документация и сверка обещаний
P2 | TODO   Est: 0.5d

После v1 описать, что закрыто и что нет, не обещая больше §17.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §9, §17; docs/superpowers/specs/2026-09-21-edge-check-design.md D12a

**Checklist:**
- [ ] D12a спеки edge-check: граница записи авторского вызова claude — песочница v1; непокрытое (disp, codex, исполнители spec-runner#600, прочие агенты, §17) — поимённо
- [ ] §9 дизайна: что из промежуточной защиты остаётся (трипвайр — пояс), что снято песочницей
- [ ] строка инструмента в `CLAUDE.md` devtools: песочница авторского вызова, требования к машине (macOS, `sandbox-exec`, элемент связки, допуск харнесса), «ограниченные гарантии, не полная изоляция»
- [ ] грепом по репо: ни одна строка документации и сообщений не называет песочницу «изоляцией» без оговорки §17
- [ ] пункт `@id:author-agent-sandbox` в `TODO.md` — статус; `make plan-check` без новых ошибок

**Depends on:** [TASK-007]

**Touches:** docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md, docs/superpowers/specs/2026-09-21-edge-check-design.md, CLAUDE.md, TODO.md
