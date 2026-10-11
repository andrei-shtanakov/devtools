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
сценарии; §17 пределы гарантий. Замеры — F14–F28.

**Это песочница с ограниченными гарантиями, не полная изоляция агента** (§17):
задачи не вправе обещать больше, ни в коде, ни в сообщениях, ни в
документации. Одобрение дизайна — не подтверждение защиты: защита считается
действующей только после TASK-007 (живая приёмка).

Уже влито до этой спеки: allowlist env (#483), изоляция конфигурации claude и
трипвайр конфиг-поверхности (#489). Вне v1 (§11, §13): disp, codex и любой
харнесс, кроме claude, — отказ до запуска агента.

Ревизия 2 tasks-спеки заменяет ревизию 1 (под rev 2 дизайна) целиком: запись
теперь в каталог вызова, а не в дерево цели; disp вне v1; добавлены
безопасный примитив, допуск исполняемого файла, терминал, решение Q1.

**PR и мерж.** Каждая задача — PR от ветки задачи; задача, чей дифф трогает
authority-root (`contracts/authority-root/v1/paths.env`) — TASK-003 и TASK-006,
— мержится человеком (`human-merge.sh`), остальные — по правилу репо.

### TASK-001: Безопасный ввод-вывод раннера — примитив и контракт назначения
P1 | TODO   Est: 1d

Новый модуль `governance/safe_io.py` — единственный способ, которым раннер
касается каталога вызова и пишет файл узла в бандл (§15, F27). Чистая
библиотека: ни профиля, ни запуска агента.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §15, F27; §16 Z11, Z14, Z15

**Checklist:**
- [ ] корень канонизируется `realpath` один раз; все открытия — с `os.O_NOFOLLOW_ANY` (ссылка в любом компоненте — отказ); путь без канонизации (`/var/…`) отказывает — тест фиксирует это как поведение, а не сбой
- [ ] `read_regular(root, rel, limit, timeout)`: `O_RDONLY|O_NONBLOCK|O_NOFOLLOW_ANY`, `fstat` открытого дескриптора — только обычный файл с `st_nlink == 1`; читается не более `limit + 1` байт с ограничением времени; больше `limit` — отказ
- [ ] `list_tree(root, rel)` без следования ссылкам: типы — через `lstat`/`fstat` дескрипторов, ссылка и спецфайл — в перечне как есть, не раскрываются
- [ ] `ensure_dirs(root, rel)` и `publish(root, rel, data)`: родители — цепочкой дескрипторов (`O_DIRECTORY|O_NOFOLLOW_ANY`, `mkdir(…, dir_fd=)`); временный файл `O_CREAT|O_EXCL|O_NOFOLLOW` в закреплённом каталоге; `rename(…, src_dir_fd, dst_dir_fd)` поверх конечного имени; конечное имя — каталог — отказ
- [ ] `validate_bundle_dir(target_dir, bundle_dir, git_dirs)`: непустой относительный, без `..`, абсолютных и пустых компонентов, не `.`, без компонента `.git`; после канонизации строго внутри `target_dir` и не пересекается с gitdir/common dir — иначе отказ (Z15)
- [ ] тесты источника (Z11): файл — symlink; компонент пути — symlink на каталог; hardlink (`st_nlink > 1`); FIFO — отказ **без зависания** (тест с таймаутом); сверх потолка; рост файла после открытия — читается не больше `limit + 1`
- [ ] тесты назначения (Z14): висячая ссылка на месте узла на `<tmp>/target/.claude/settings.local.json` — заменена самой ссылкой, цель НЕ создана; ссылка-каталог в пути бандла — отказ; hardlink на месте узла — новый inode, внешний двойник побайтно неизменен; конечное имя — каталог — отказ
- [ ] при любом отказе вне `root` ничего не создано и не изменено (снимок внешних файлов до/после)
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_safe_io.py` зелёный; ruff и pyrefly чисты

**Touches:** governance/safe_io.py, tests/test_governance_safe_io.py

### TASK-002: Каталог вызова и генератор профиля
P1 | TODO   Est: 1d

`governance/sandbox.py`: создание свежего каталога вызова вне чекаута devtools
и профиль Seatbelt из его `realpath` (§11, §12). Генератор — чистая функция;
проверяется текстом профиля здесь и настоящим `sandbox-exec` в TASK-004.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §11, §12, F21, F22, F28; §16 Z6, Z7

**Checklist:**
- [ ] корень `<calls>` = `$XDG_STATE_HOME/devtools/agent-calls` (по умолчанию `~/.local/state/devtools/agent-calls`); `<run_id>/<call>` и `stage/`, `tmp/`, `claude-config/` создаются через `safe_io` (TASK-001); `<call>` — уникальный, существующий — отказ (Z6)
- [ ] `realpath` каталога вызова не пересекается (ни предок, ни потомок) с `target_dir`, gitdir и common dir цели (`git rev-parse --path-format=absolute --git-dir --git-common-dir`), чекаутом devtools и `run_dir` — иначе отказ (Z7); тест на цель «сам devtools» проходит (корень вне чекаута)
- [ ] профиль ровно по §12: `(allow default)`, `(deny file-write*)`, разрешения только `stage/`, `tmp/`, `claude-config/`, `/dev/null`, `/dev/fd/*`; `(deny file-write* (regex #"(^|/)\.git(/|$)"))`; `(deny file-ioctl (regex #"^/dev/tty"))`; запрет чтения `~/.config/{review,gh,ai-prosto}`, `~/.ssh`; mach-запрет трёх сервисов связки и префиксов `com.apple.coreservices.`, `com.apple.lsd.`
- [ ] юнит-тесты генератора: каждый пункт присутствует; НЕТ разрешения записи на `/private/tmp`, `/private/var/folders`, `/dev/tty`, `/dev/ttys*`, `target_dir`; пути в профиле — после `realpath`; спецсимволы пути экранированы (тест на путь с кавычкой и пробелом)
- [ ] `profile_template_sha256()` — хэш шаблона без подстановок, для записи допуска (TASK-003)
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox.py` зелёный

**Depends on:** [TASK-001]

**Touches:** governance/sandbox.py, tests/test_governance_sandbox.py

### TASK-003: Допуск исполняемого файла харнесса и регистрация в authority-root
P1 | TODO   Est: 0.5d

Контракт допуска и его проверка без исполнения кандидата (§14). Мержит человек:
дифф трогает `contracts/authority-root/v1/paths.env`.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §14; §16 Z16

**Checklist:**
- [ ] `contracts/author-sandbox/v1/admitted.toml` — пустой список записей `(harness, version, realpath, sha256, macos_major, profile_sha256)` + README контракта; первая запись — в TASK-006, не здесь
- [ ] префикс `contracts/author-sandbox/` добавлен в `AUTHORITY_ROOT_PREFIXES`; тест, что `merge-pr.sh` отказывает агентскому PR, трогающему допуск (по образцу существующих тестов authority-root)
- [ ] `admission_check()` в `governance/sandbox.py`: `shutil.which` в PATH раннера → `realpath` → sha256 через `safe_io.read_regular` → `sw_vers -productVersion` → сверка `(realpath, sha256, macos_major, profile_sha256)` с контрактом; версия — из записи; **кандидат не исполняется ни с каким аргументом**, включая `--version`
- [ ] Z16: подменённый в PATH исполняемый файл, оставляющий внешний маркер при любом запуске → отказ «не проверен под песочницей», маркера нет
- [ ] запись есть, но sha256/ОС/шаблон профиля не совпали — отказ с названным полем
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox.py tests/test_governance_authority_root.py tests/test_merge_pr.py` зелёный

**Depends on:** [TASK-002]

**Touches:** contracts/author-sandbox/v1/admitted.toml, contracts/author-sandbox/v1/README.md, contracts/authority-root/v1/paths.env, governance/sandbox.py, tests/test_governance_sandbox.py, tests/test_governance_authority_root.py, tests/test_merge_pr.py

### TASK-004: Проба-канарейка на настоящем `sandbox-exec`
P1 | TODO   Est: 1.5d

Проба перед каждым вызовом под той же обёрткой и профилем, с таймаутом (§4
п.1–4, §15). Тесты — на настоящем `sandbox-exec` (macOS; на другой ОС — skip с
названной причиной); каждый запрет с контролем без песочницы там, где иначе
не доказан.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §4, §15, F14–F21, F28; §16 Z1–Z10, Z18, Z19, N6

**Checklist:**
- [ ] обязаны НЕ удаться: запись в `run_dir/edge-check/` и `$HOME`; чтение приманки `~/.config/ai-prosto/.sandbox-bait` (раннер кладёт и снимает); связка ключей — 4 пробы §4 п.3; env внутри = allowlist вызова (равенство множеств)
- [ ] неразрушающие часовые в настоящей цели (`O_CREAT|O_EXCL`, имя с UUID) в `target_dir`, `target_dir/<bundle_dir>/`, gitdir, common dir; каталоги бандла раннер создаёт ДО пробы (`safe_io.ensure_dirs`); засчитывается только `EPERM`, `ENOENT`/`EEXIST`/прочее — отказ «сбой подготовки»; удавшийся часовой удаляется, вызов не делается
- [ ] разрушающие пробы — на одноразовом стенде git-репо + worktree вне разрешённых корней: `unlink`, переименование, подмена файла `.git`, запись в `config`/`hooks`, в gitdir/common dir; стенд удаляется в `finally`
- [ ] `ln <внешний> <call>/stage/x` и `mv <внешний> <call>/stage/` — отказ (F15, F17)
- [ ] `launchctl submit` пробного задания — ненулевой код и метки нет в `launchctl list`; `open -g` пробного `.app` из каталога вызова — ненулевой код, `lsappinfo front` → NULL; код 0, таймаут или неоднозначность — отказ запуска; задания и `.app` убираются в `finally`
- [ ] открытие `/dev/tty` — не удаётся (запуск в новой сессии, F28)
- [ ] обязаны УДАТЬСЯ: запись в `<call>/stage/` и `<call>/tmp/`
- [ ] Z18 мутации, каждая краснеет своей пробой: (1) после запретов добавлено разрешение записи в gitdir/common dir стенда → часовой краснеет; (2) снят regex `.git` → запись во вложенный `.git` в заготовке краснеет; (3) обёртка убрана → пробы запретов краснеют; (4) env наследованием → проба env краснеет; метаданные настоящей цели неизменны
- [ ] Z19 на одноразовом PTY: контроль без песочницы подкладывает ввод `TIOCSTI`; под обёрткой — отказ отдельно каждым поясом (новая сессия; `file-ioctl`), отложенного ввода нет
- [ ] N6: первый запуск без brief, бандла и его родителей — проба зелёная; отрицательный контроль с разрешённой записью в бандл — краснеет
- [ ] отказ пробы — `stopped_author` «песочница не действует: <пункт>», следы пробы убраны, агент не вызван
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_sandbox_live.py` зелёный на macOS

**Depends on:** [TASK-002]

**Touches:** governance/sandbox.py, tests/test_governance_sandbox_live.py

### TASK-005: Обвязка авторского вызова — отказ, запуск, перенос
P1 | TODO   Est: 1.5d

Включение v1: `_step_authoring` и `ops.author` запускают claude только под
песочницей; любой другой харнесс и disp — отказ до запуска (§13, Q1).
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §11, §12, §13, §15; §16 Z12, Z13, Z17, N1, N2

**Checklist:**
- [ ] предпроверка в `_step_authoring` (единственная точка вызова `ops.author`/`ops.author_disp`, `governance/runner.py:2492`) до каталога вызова и платного вызова, на каждом входе (`start`, `resume`, повтор шага, волна): ОС — macOS; `/usr/bin/sandbox-exec` есть; не вложенная песочница; харнесс резолвера (`AUTHOR_HARNESS` > `harness.env` > вшитый `codex`) = claude; `author_backend` ≠ disp; допуск (TASK-003); `validate_bundle_dir`; элемент связки токена claude
- [ ] отказ по харнессу — дословно по §13: «харнесс `<имя>` не поддерживается песочницей авторинга v1; поддерживается claude (`AUTHOR_HARNESS=claude`)»; disp — «disp вне v1»; нет флага, переменной или режима обхода; вшитое умолчание резолвера НЕ меняется
- [ ] Z17: харнесс ≠ claude, выбранный env, `harness.env` и вшитым умолчанием, на `start`, `resume`, повторе шага и волне → отказ; исполняемый файл-маркер харнесса не вызван; файлы цели и бандла неизменны (снимок)
- [ ] запуск: абсолютный путь из допуска, `sandbox-exec -f <профиль>`, `start_new_session=True`, `stdin=DEVNULL`, cwd = `target_dir`; env = `agent_env()` + `TMPDIR`/`CLAUDE_CODE_TMPDIR` = `<call>/tmp`, `CLAUDE_CONFIG_DIR` = `<call>/claude-config` (перенос `claude_isolation` из системного temp), токен, `SSL_CERT_FILE`
- [ ] промпт называет `bundle_dir` (чтение, `git hash-object`) и абсолютный `output_path` = `<call>/stage/<file>` (единственное место записи); DSL узлов не меняется
- [ ] таймаут вызова — `SIGKILL` группе, `stopped_author` «таймаут», переноса нет (Z13); после штатного выхода — `SIGKILL` группе (гигиена)
- [ ] перенос: `safe_io.read_regular` файла узла; в заготовке ничего лишнего (`list_tree`), иначе стоп с перечнем в карантин; `safe_io.publish` в `target_dir/<bundle_dir>/<file>`; каталог вызова удаляется
- [ ] Z12: отсоединённый потомок, переписывающий файл в заготовке после выхода агента, не меняет перенесённое; гонки до/во время переноса (подмена родителя ссылкой, regular→FIFO, рост) — отказ или перенесённые байты, но не внешний файл
- [ ] трипвайр конфиг-поверхности (#489) остаётся вокруг вызова как пояс
- [ ] тесты на фейковом харнессе (скрипт под тем же профилем); живой claude — в TASK-006/007
- [ ] `uv run --frozen --group=governance pytest -q tests/test_governance_ops.py tests/test_governance_runner.py tests/test_governance_sandbox_live.py` зелёный

**Depends on:** [TASK-001, TASK-003, TASK-004]

**Touches:** governance/ops.py, governance/runner.py, governance/sandbox.py, tests/test_governance_ops.py, tests/test_governance_runner.py, tests/test_governance_sandbox_live.py

### TASK-006: Процедура допуска и допуск текущего claude
P1 | TODO   Est: 0.5d

`make sandbox-admit` (исполняет оператор) и первая запись допуска (§14). Мержит
человек: дифф трогает контракт под authority-root.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §14, F22, F24–F26, F28; §16 N2

**Checklist:**
- [ ] `make sandbox-admit` под профилем §12 с реальным харнессом: Bash `true`→0, `false`→1, `exit 3`→3; `cd` сохраняется; Write в заготовку; отказ записи в дерево цели; снимки `$HOME`-поверхности и `/private/tmp` до/после — вне каталога вызова ничего; проба TASK-004 зелёная — сырые `tool_result` из `stream-json`, не пересказ модели
- [ ] не прошло — версия не допускается; расширение профиля — изменение дизайна, не строка контракта
- [ ] протокол — `docs/evidence/<дата>-author-sandbox-admit-<версия>.md`; запись в `admitted.toml` — тем же PR
- [ ] `make sandbox-admit` в справке Makefile

**Depends on:** [TASK-005]

**Touches:** Makefile, governance/sandbox.py, contracts/author-sandbox/v1/admitted.toml, docs/evidence/

### TASK-007: Живая приёмка v1
P1 | TODO   Est: 1d

Позитивная половина на живых вызовах (§5, §16 N5); до неё защита не считается
действующей.
Source: docs/superpowers/specs/2026-09-29-author-agent-sandbox-design.md §5, §10 п.4, §16 N5

**Checklist:**
- [ ] авторинг узла claude при **настоящем** расположении состояния раннера (`out/governance-runs`, `<calls>` по умолчанию) — для внешней цели И для цели «сам devtools»: файл перенесён, гейт зелёный, проба и проверки §15 зелёные
- [ ] сравнение качества (§10 п.4): тот же узел без обёртки и без изоляции конфигурации — гейт и находки edge-check сопоставлены; если под обёрткой чего-то не хватает — в конфиг вызова явно из доверенного источника, НЕ открытием `~/.claude`
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
