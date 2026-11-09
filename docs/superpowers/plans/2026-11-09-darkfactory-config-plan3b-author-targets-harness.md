# DarkFactory config — план 3b: авторинг без песочницы, свой список целей, харнесс из конфига

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** в `mode = "test"` конвейер авторит узлы обычным процессом `claude` пользователя
(без `sandbox-exec`, допуска и связки ключей), берёт цель из пользовательского списка целей
рядом с конфигом (манифест флота необязателен) и во всех ролях (авторинг, disputatio,
edge-check, `review-pr.sh` → кит) использует харнесс и модель из `[harness]` конфига.

**Результат точно:** на машине коллеги без манифеста флота и без песочницы прогон в `test`
доходит до узлов бандла, написанных `claude`, на репо из `targets.toml`. Регистрацию цели
командой запуска (`dfl run`), ревью комментарием и автоматические волны делают планы 3c/4.
`strict` не меняется ни в одной строке поведения.

**Architecture:** режим читается из конфига в точке решения (как `policy_rule`): нет файла
конфига — поведение `strict` как сегодня; файл есть, но битый — отказ с названием файла.
Авторинг: та же граница `author_launch.launch_author`, тот же каталог вызова, перенос одного
файла узла и транскрипт; в `test` пропускаются только песочница, проба, допуск и токен
связки. Цели: новый модуль `governance/user_targets.py` (чтение/регистрация
`<канонический каталог конфига>/targets.toml`), `spec_loop` читает его только в `test`
последним, после манифеста и списка приёмки.

**Tech Stack:** Python 3.12 (uv), POSIX sh, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` — §5.3 п.4 (без
песочницы), п.5 (список целей, путь клона из записи), п.9 (Claude во всех ролях); §5.8 (флаг
обхода и D10). План 3a: `docs/superpowers/plans/2026-11-08-darkfactory-config-plan3a-test-foundation.md`.

## Решения исполнителя (проверить владельцу)

1. **`[harness]` действует только в `test`.** В `strict` порядок прежний: env
   `<P>_HARNESS`/`<P>_MODEL` > `~/.config/ai-prosto/harness.env` > вшитый codex. Иначе
   конфиг молча подменял бы выбор харнесса владельца (решение §13 песочницы: «никакой
   автоматической замены»). В `test`: env > `[harness]`; `harness.env` не читается (файл
   машины владельца, у коллеги его нет). Цена ошибки: если владелец хотел `[harness]` и в
   `strict` — одна строка условия.
2. **Один харнесс на все роли:** `[harness].author`/`.model` — и для `AUTHOR`, и для `REVIEW`
   (одна подписка Claude, §5.3 п.9). Отдельных ключей ревьюера нет.
3. **Edge-check** всегда `claude`; в `test` модель — `[harness].model`, если
   `[harness].author == "claude"`, иначе прежний `DEFAULT_MODEL`.
4. **Авторинг в `test` поддерживает только `claude`** (argv — claude-специфичный); иной
   `[harness].author` — отказ на границе с названием ключа.
5. **Файл целей** — `canonical_config_path().parent / "targets.toml"` (тот же якорь, что у
   режима test: подмена `XDG_CONFIG_HOME` не перенаправляет цель). Агент его не пишет,
   кроме явной регистрации пользователем (`python -m governance.user_targets add`).
6. **`accept_pr.py:415`** (`workspace_dir(args.repo)`) не трогаем — его режим test (без мержа)
   переделывает 3c, там же путь клона из `targets.toml`.

## Global Constraints

- `strict`: существующие тесты зелёные без правок ожиданий; поведение при отсутствии файла
  конфига — как сегодня (маркер `no_df_config`).
- Режим — только `df_config.load().mode`; файл конфига отсутствует (`os.path.lexists` ложно) —
  считать `strict`; присутствует, но `ConfigError` — отказ «конфиг DarkFactory: <ошибка>».
- Новых мест порождения процессов вне защищённой цепочки нет: обычный запуск `claude` —
  через существующий `author_launch._run_agent` (`tests/test_author_sandbox_invariant.py`
  зелёный без правки `spawn-sites.toml`).
- Тексты баннера (`df_config.TEST_RELAXATIONS`) меняются только в PR B — после того, как
  поведение из PR A влито (баннер не обещает несделанного).
- Разметка путей: `governance/author_launch*`, `tests/test_governance_sandbox*` — authority-root
  (не харнесс) → PR A мержит агент с `--authority-override '<причина>'`; `review-pr.sh`,
  `governance/df_config*` — харнесс → PR B мержит владелец (D10).
- `tests/df_config_fixture.write_config` по умолчанию пишет `mode="test"` — тест, которому
  важен прежний выбор харнесса (codex по умолчанию), обязан явно передать `mode="strict"`.
- Строки ≤ 88, ruff + pyrefly (pyrefly в worktree — на rsync-копии), docstring у публичных функций.

## Review Focus

1. `[harness].author = "codex"` в `test` → авторинг отказывает до каталога вызова с текстом,
   называющим `harness.author` (Task 2).
2. `claude` нет в `PATH` → отказ до создания каталога вызова, бандл не тронут (Task 2).
3. Запись `targets.toml`, чей `path` указывает на клон с другим origin, → прежний отказ
   «расхождение origin» с подсказкой про `targets.toml` (Task 4).
4. Репо есть и в манифесте, и в `targets.toml` → берётся манифест; в `strict` `targets.toml`
   не читается, даже если существует и битый (Task 4).
5. `AUTHOR_HARNESS=codex` в env при `test`-конфиге `claude` → codex без модели конфига
   (модель привязана к слою) (Task 1).

---

## PR A (devtools, агент с `--authority-override`): Tasks 1–4

Ветка `feat/df-config-3b`.

### Task 1: харнесс и модель из `[harness]` в `test`

**Files:**
- Modify: `governance/author_launch.py` (`harness_for`)
- Modify: `governance/ops.py` (`RealOps.edge_check_level`, ~2408)
- Test: `tests/test_governance_sandbox.py` (или соседний тест `harness_for`, где он уже покрыт), `tests/test_governance_edge_check_coordinator.py`

**Interfaces:**
- Produces: `author_launch.config_mode() -> str` — `"strict"`, если файла конфига нет,
  иначе `df_config.load().mode` (`ConfigError` пробрасывается).
- Produces: `harness_for(prefix)` — в `test`: env `<P>_HARNESS` (модель только env
  `<P>_MODEL`), иначе `(cfg.author_harness, cfg.author_model)`; `harness.env` не читается.
  `strict` — без изменений. `ConfigError` пробрасывается; `check_config` превращает её в
  строку `"конфиг DarkFactory: …"`, `ops.disp_agent` — в `ValueError` с тем же текстом.
- Produces: `ops.edge_check_model() -> str` — по решению 3.

- [ ] **Step 1: тесты.** `test_harness_for_test_mode_reads_config` (конфиг test
  `author="claude", model="m-x"` → `harness_for("AUTHOR") == harness_for("REVIEW") ==
  ("claude", "m-x")`, при этом существующий `harness.env` с `AUTHOR_HARNESS=codex`
  игнорируется); `test_harness_for_test_env_overrides_without_model` (env
  `AUTHOR_HARNESS=codex` → `("codex", "")`); `test_harness_for_strict_unchanged` (strict-конфиг
  + `harness.env` claude → claude из файла; без файла → `("codex", "")`);
  `@pytest.mark.no_df_config test_harness_for_no_config_is_strict`;
  `test_check_config_broken_config_named` (битый конфиг → строка начинается с
  `"конфиг DarkFactory: "`); `test_edge_check_model_from_config_in_test` (test + claude →
  `"m-x"`; test + codex → `coordinator.DEFAULT_MODEL`; strict → `DEFAULT_MODEL`).
- [ ] **Step 2:** прогнать — FAIL.
- [ ] **Step 3:** реализовать; `RealOps.edge_check_level` передаёт `model=edge_check_model()` в `run_level`.
- [ ] **Step 4:** `uv run pytest tests/test_governance_sandbox.py tests/test_governance_edge_check_coordinator.py tests/test_governance_ops*.py -q` — PASS.
- [ ] **Step 5:** commit `feat(harness): [harness] from the DarkFactory config in test mode`.

### Task 2: авторинг без песочницы в `test`

**Files:**
- Modify: `governance/author_launch.py` (`launch_author`, `_preflight`, `_launch_in`)
- Test: `tests/test_governance_sandbox.py` (там же, где тесты `launch_author` с подменёнными `_run_agent`/`_os_name`)

**Interfaces:**
- Consumes: `config_mode()`, `harness_for("AUTHOR")` (Task 1).
- Produces: `PLAIN_HARNESS_REFUSAL` — текст отказа для не-claude в `test`:
  `"режим test: авторинг поддерживает только harness.author = \"claude\" (сейчас {harness!r})"`.
- Produces: `plain_env(call: sandbox.CallDir) -> dict[str, str]` — `agent_env()` +
  `TMPDIR`/`CLAUDE_CODE_TMPDIR`/`TMPPREFIX` каталога вызова; без `CLAUDE_CONFIG_DIR` и
  `CLAUDE_CODE_OAUTH_TOKEN` (вход claude — собственный пользователя).

Поведение `test`: `_preflight` не проверяет ОС, `sandbox-exec`, вложенность, допуск и связку;
бинарь — `os.path.realpath(shutil.which("claude"))`, нет — отказ `"claude не найден в PATH"`;
проверки `run_dir`, git-каталогов цели и `bundle_dir` — как в `strict`; `_Ready.token = ""`.
`_launch_in` в `test` не рендерит профиль, не делает пробу, не пишет `settings.json`;
argv — `author_argv(binary, model, prompt)` без `sandbox-exec -f`; env — `plain_env`. Каталог
вызова, `_save_transcript`, таймаут, `_transfer` и снятие пустых каталогов — те же.

- [ ] **Step 1: тесты.** `test_test_mode_launches_without_sandbox` (подменённый
  `_run_agent` получает argv, `argv[0]` — путь claude, нет `sandbox-exec`; env без
  `CLAUDE_CONFIG_DIR`/`CLAUDE_CODE_OAUTH_TOKEN`; `author_claude_token`, `admission_check`,
  `run_probe` не вызывались — подмена бросает AssertionError; узел перенесён в бандл);
  `test_test_mode_runs_off_darwin` (`_os_name` → `"linux"`, запуск состоялся);
  `test_test_mode_non_claude_refused` (`[harness].author="codex"` → `REFUSED`,
  `reason == PLAIN_HARNESS_REFUSAL.format(...)`, каталог вызова не создан);
  `test_test_mode_claude_missing_refused` (`shutil.which` → None → отказ, бандл не создан);
  `test_strict_still_requires_sandbox` (strict + `_os_name` linux → прежний отказ macOS).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest tests/test_governance_sandbox.py tests/test_author_sandbox_invariant.py -q` — PASS.
- [ ] **Step 5:** commit `feat(author): plain claude author call in test mode (no sandbox)`.

### Task 3: пользовательский список целей `governance/user_targets.py`

**Files:**
- Create: `governance/user_targets.py`
- Test: `tests/test_governance_user_targets.py`

**Interfaces:**
- Produces: `SCHEMA = "darkfactory-targets/v1"`; `targets_path() -> Path` =
  `df_config.canonical_config_path().parent / "targets.toml"`.
- Produces: `@dataclass(frozen=True) UserTarget(repo: str, path: Path, repo_url: str)`.
- Produces: `class TargetsError(RuntimeError)`; `load() -> dict[str, UserTarget]` (нет
  файла — `{}`); `lookup(repo: str) -> UserTarget | None`;
  `register(target_dir: Path) -> UserTarget`; `main(argv: list[str]) -> int`
  (`add [<dir>]` — по умолчанию cwd, печатает `repo path repo_url`; `list`); exit 0/2.

Формат файла:

```toml
schema = "darkfactory-targets/v1"

[targets.my-repo]
path = "/Users/me/src/my-repo"
repo_url = "git@github.com:me/my-repo.git"
```

Правила `load`: `schema` обязателен и равен `SCHEMA`; ключ таблицы — `run_state.validate_id_component(label="repo")`;
`path` абсолютный, каталог существует; `repo_url` разбирается `spec_loop.repo_slug_from_url`
(импорт без цикла — вынести разбор, если нужно, в `user_targets` и переиспользовать в `spec_loop`);
неизвестный ключ, не-TOML — `TargetsError` с путём файла и ключом.
`register`: `repo` = имя git-верхушки (`git -C <dir> rev-parse --show-toplevel`, basename),
`path` — realpath верхушки, `repo_url` — `git remote get-url origin`; та же запись — идемпотентно;
тот же `repo` с другим `path`/`repo_url` — `TargetsError` («уже зарегистрирован … — правьте файл вручную»);
запись атомарная (временный файл + `os.replace`), каталог создаётся, существующие записи и порядок сохраняются.

- [ ] **Step 1: тесты** (канонический каталог — через `use_test_real_home`):
  `test_load_missing_file_empty`, `test_load_roundtrip`, `test_load_rejects_relative_path`,
  `test_load_rejects_bad_schema`, `test_load_rejects_unknown_key`,
  `test_register_from_git_checkout` (tmp `git init` + `remote add origin`),
  `test_register_idempotent`, `test_register_conflict_refused`,
  `test_targets_path_ignores_xdg_redirect` (`XDG_CONFIG_HOME` → другой каталог; путь — под
  каноническим домом), `test_cli_add_and_list`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest tests/test_governance_user_targets.py -q` — PASS.
- [ ] **Step 5:** commit `feat(targets): user target list next to the DarkFactory config`.

### Task 4: `spec_loop` берёт цель из списка в `test`

**Files:**
- Modify: `governance/spec_loop.py` (`RepoEntry`, `resolve_repo_entry`, чтение манифеста ~1147, `target_dir` ~1219, `_acceptance_origin_hint`)
- Test: `tests/test_governance_spec_loop.py`

**Interfaces:**
- Consumes: `user_targets.lookup` (Task 3), `author_launch.config_mode` (Task 1) — или
  собственная тонкая обёртка над `df_config.load().mode` с тем же правилом «нет файла — strict».
- Produces: `RepoEntry.path: Path | None = None`, `source` — `"fleet" | "acceptance" | "user"`.
- Produces: `resolve_repo_entry(manifest_text, repo, targets_text, *, user: UserTarget | None = None)` —
  порядок: манифест → список приёмки → `user`.

Поведение: в `test` отсутствующий манифест — пустой текст (не отказ); иная ошибка чтения —
прежний отказ. `user` передаётся только в `test` (`user_targets.lookup(args.repo)`; `TargetsError` →
`SpecLoopError`). `target_dir` нового прогона: `args.target_dir`, иначе `entry.path` для
`source == "user"`, иначе `workspace_root()/repo`. Сообщение «нет ни в манифесте…» в `test`
называет и `targets.toml` с командой регистрации `python -m governance.user_targets add`.
Подсказка при расхождении origin для `source == "user"` называет файл целей.

- [ ] **Step 1: тесты.** `test_test_mode_user_target_used_without_manifest` (манифеста нет,
  цель из `targets.toml`, `target_dir == entry.path`, печать `цель … — из списка целей пользователя`);
  `test_manifest_wins_over_user_target`; `test_strict_ignores_user_targets` (strict + битый
  `targets.toml` → прежний отказ «нет ни в манифесте…», файл не читался);
  `test_strict_missing_manifest_still_refused`; `test_user_target_origin_mismatch_named`;
  `test_test_mode_unknown_repo_names_registration`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest tests/test_governance_spec_loop.py -q` — PASS; затем весь `uv run pytest -q` (foreground).
- [ ] **Step 5:** commit `feat(spec-loop): target from the user list in test mode`.

**Мерж PR A:** ревью по бюджету → `sh merge-pr.sh devtools <pr> --squash --delete-branch --expect-head <sha> --expect-base <sha базы> --authority-override 'DarkFactory config plan 3b (D9)'`.

## PR B (devtools, мержит владелец): Task 5

Ветка `feat/df-config-3b-harness` от master после мержа PR A.

### Task 5: `review-pr.sh` — ревьюер из `[harness]` в `test`; баннер

**Files:**
- Modify: `review-pr.sh` (блок выбора харнесса, ~194–222)
- Modify: `governance/df_config.py` (`TEST_RELAXATIONS`)
- Modify: `CLAUDE.md` (строки `review-pr.sh` и `governance/author_launch.py` в таблице инструментов — по одному предложению про test)
- Test: `tests/test_review_pr.py`, `tests/test_df_config*.py` (где проверяется баннер)

Поведение `review-pr.sh`: только если нет `--harness` и env `REVIEW_HARNESS` пуст —
`df_mode=$(df_cfg mode) || exit $?`; при `test`: `harness=$(df_cfg harness.author)`,
`model=${opt_model:-$(df_cfg harness.model)}`, `harness.env` не читается. `strict` — прежняя
ветка `cfg_harness`. Шапка вердикта при `test` добавляет строку `ревьюер из [harness] конфига (режим test)`.

`TEST_RELAXATIONS` — добавить ровно две строки:
- `"песочница авторинга: claude запускается обычным процессом пользователя (нет sandbox-exec, допуска и связки ключей)"`
- `"белый список целей: цель — из targets.toml рядом с конфигом, манифест флота необязателен"`

- [ ] **Step 1: тесты.** `test_test_mode_reviewer_from_config` (test-конфиг `claude`/`test-model`,
  кит с харнесс-слоем → киту ушли `REVIEW_HARNESS=claude`, `REVIEW_MODEL=test-model`; файл
  `AI_PROSTO_HARNESS_ENV` с codex игнорирован); `test_test_mode_flag_overrides_config`;
  `test_strict_reviewer_unchanged` (`write_config(..., mode="strict")` + harness.env claude → claude
  из файла; без файла → codex); существующие тесты, полагавшиеся на codex по умолчанию,
  переводятся на `mode="strict"` явно (ожидания не меняются); тест баннера — три строки, в
  порядке: политика, песочница, цели.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest tests/test_review_pr.py tests/test_df_config*.py tests/test_harness_trust*.py -q` — PASS; весь набор.
- [ ] **Step 5:** commit `feat(review): reviewer harness from [harness] in test mode; banner lines`.

**Мерж PR B:** ревью по бюджету → владелец.

## PR C (discovery, агент): Task 6

### Task 6: перепин вендоренного загрузчика в discovery

**Files (в discovery):** `src/discovery/darkfactory/df_config.py`, `src/discovery/darkfactory/PINNED.txt`; при необходимости тест баннера discovery (`tests/test_approve_cli.py::TestTestModeBanner`).

- [ ] **Step 1:** worktree discovery от master, копия `governance/df_config.py` @ мерж-коммит PR B, `PINNED.txt` (commit + sha256).
- [ ] **Step 2:** `uv run pytest -q`, `uv run python tools/check_df_config_vendor.py consistency|provenance|drift` — все `ok`; ruff, pyrefly.
- [ ] **Step 3:** commit `chore(darkfactory): re-pin df_config to devtools <sha> (3b banner)`; PR; ревью; `merge-pr.sh` (workflow-файлы не трогаются → агент); затем локальный `tests/test_discovery_test_mode_local.py` в devtools — PASS.
