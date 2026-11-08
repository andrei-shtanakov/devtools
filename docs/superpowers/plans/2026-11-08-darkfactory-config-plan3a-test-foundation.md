# DarkFactory config — план 3a: фундамент режима test и стоп на брифе одной учёткой

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** в `mode = "test"` бриф утверждается одной учёткой GitHub: утверждающий —
`accounts.human` конфига, репо политики не нужен; режим записан в журнал прогона, любой
действующий вход при смене режима отказывает; discovery читает тот же конфиг через
вендоренную пиненую копию загрузчика.

**Результат точно (ревью codex 354de7d):** подписанный бриф на **подготовленном** репо, правила
целевой ветки которого не требуют чужого review. Произвольный репо коллеги и полный
автоматический проход — планы 3b/3c/4. «Без сети» — только чтение политики; факты PR
(мерж, мержер, файлы) по-прежнему проверяются через GitHub.

**Architecture:** одна точка — «синтетическая политика test»: в `test` `policy_rule.policy_source()`
возвращает служебные координаты, а слой фактов (`brief_facts`) отвечает на них версией и
составом из конфига (`AUTHORIZED_APPROVER_ACCOUNTS=<accounts.human>`), не ходя в сеть за
политикой. Машинерия брифа (пин в заявке, `read_act`, `check_policy`, `human-merge.sh`)
работает без ветвлений; единственная test-ветка — понятный отказ при смене утверждающего. `strict` не меняется ни в одной строке поведения.

**Tech Stack:** Python 3.12 (uv), POSIX sh, pytest; discovery — Python 3.12, uv.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` (§5.1 загрузчик в
discovery, §5.3 п.2 «утверждающий — из конфига», §5.3 абзац 1 «режим в журнале», §7 этап 3).
План 2: `docs/superpowers/plans/2026-11-08-darkfactory-config-plan2.md`.

## Разбиение плана 3 (исполнитель, 08.11)

Этап 3 дизайна (§5.3) по разведке — ~25 мест в devtools, discovery и пайплайне; один план
был бы стеной. Три плана, каждый даёт проверяемый результат:

- **3a (этот):** фундамент режима (загрузчик самодостаточен, режим в журнале, баннер),
  утверждающий из конфига, discovery читает конфиг; TODO plan 3 у фикстуры. Итог: прогон в
  `test` доходит до подписанного брифа одной учёткой.
- **3b:** авторинг без песочницы, пользовательский список целей (манифест необязателен в
  `test`), харнесс из `[harness]` во всех ролях (авторинг, `review-pr.sh`, кит).
- **3c:** ревью комментарием + вердикт в журнале, автоматические волны (акт человека и класс
  актора — именованные пропуски), автоматическое утверждение задач и `spec-runner run`,
  `accept-pr` без мержа.

`dfl`/установка/README — план 4.

## Решения плана (исполнитель, 08.11)

- **Политика test — синтетическая, а не ветвление по местам.** Координаты
  `("darkfactory-config", "test", "accounts.human")`; содержимое
  `AUTHORIZED_APPROVER_ACCOUNTS=<accounts.human>\n`; версия — первые 40 hex sha256 содержимого
  (идентификатор версии, не защита: конфиг — данные человека). Синтетический источник знает
  только текущую версию, истории нет.
- **Смена утверждающего в test (ревью codex 354de7d, п.2):** контракт — «сменил утверждающего
  — сделай новое предложение брифа (`brief-propose`), при необходимости новый прогон».
  Старый пин после смены неизвестен; вместо безликого `not_policy_version` — именованный отказ
  `test_approver_changed`: «утверждающий в конфиге сменился после предложения брифа — сделайте
  новое предложение (brief-propose), при необходимости новый прогон». Переподтверждения
  комментарием и истории версий в test нет.
- **Один логин — одно правило в обоих репо (ревью codex 354de7d, п.3):** загрузчик проверяет
  `accounts.human` и `accounts.agent` как один логин GitHub
  (`^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$`) в обоих режимах; `"alice,bob"`,
  пробелы, пустая строка → `ConfigError` с ключом. discovery получает то же правило с
  дословной копией загрузчика; совместный локальный тест devtools → discovery — Task 8.
- **В `test` `[policy]` конфига не используется и не сверяется с SSOT** (репо политики не нужен,
  §5.3 п.2). Запрет env `AUTHORIZED_APPROVER_ACCOUNTS` остаётся в обоих режимах.
- **Загрузчик самодостаточен (только stdlib)**, чтобы discovery вендорила его дословно:
  `config_home` переезжает в `df_config.py` (`act_settings` реэкспортирует), перечень каталогов
  strict — константа `STRICT_GH_DIRS` в `df_config.py`, равенство с `secret_dirs.SECRET_DIRS`
  закрепляет тест; блок загрузки пакета по пути уходит.
- **discovery без файла конфига ведёт себя как сегодня** (как `policy_rule` devtools:
  `lexists` нет → strict по SSOT); битый конфиг или висячая ссылка → `PolicyRefused`.
- **Режим прогона:** `RunState.mode` с умолчанием `"strict"` (старые `run.json` — strict).
  Проверка — на **каждом действующем входе** до любых действий (ревью codex 354de7d, п.1):
  `runner.resume`, `advance`, `reopen`, `verify` (создаёт дочерний прогон, S8, issue — не
  read-only), `spec_loop._dispatch`, `brief_tools propose` и `approve` (грузят прогон сами,
  мимо `resume`). Дочерний прогон `verify` наследует режим родителя. Без проверки — только
  `status`. Исполнитель грепом `rs.load(`/`run_state.load(` по `governance/` сверяет, что
  других действующих входов нет, и перечисляет их в отчёте.
- **Баннер:** `df_config.TEST_RELAXATIONS` — кортеж того, что реально выключено; в 3a — одна
  строка про политику; 3b/3c дописывают свои. Баннер не обещает несделанного.
- **TODO plan 3 у фикстуры:** логины в `gen_discovery_approval_fixture.py` — данные подписанной
  фикстуры (байты подписаны настоящим discovery), не учётки конвейера: исключение стража
  остаётся с этой причиной; генератор пишет себе временный strict-конфиг, чтобы работать и
  после того, как discovery начнёт читать конфиг.

## Global Constraints

- `strict` владельца — регрессия недопустима: существующие тесты зелёные; без файла конфига
  поведение devtools и discovery — как сегодня.
- `test` включается только явным `mode = "test"`; значения владельца — ни умолчаниями, ни
  fallback.
- Коды выхода: 2 — конфигурация/аргументы; ошибки конфига начинаются с «конфиг DarkFactory: ».
- Тесты — только на переднем плане. Единственное допустимое падение в worktree —
  `test_create_call_dir_for_devtools_itself_as_target` (`.git` — файл); финальный прогон его —
  в основном чекауте.
- Строки ≤ 88, ruff + pyrefly чистые (в обоих репо).
- Полирепо: правки discovery — отдельный PR в discovery, из его чекаута; devtools не правит
  discovery и наоборот.

## Review Focus

1. Конфиг владельца (`strict`) и отсутствие конфига: `policy_source()` = SSOT, `run.json` без
   поля `mode` читается как strict, discovery `allowlist` ходит в форджу как сегодня.
2. Конфиг переключён `strict → test` (или обратно) между `start` и `resume` — именованный
   отказ, код 2, прогон не продвигается.
3. `test` с заданным `[policy]`, расходящимся с SSOT — не отказ (не используется).
4. `test`: `accounts.human` сменили после `brief-propose` — `brief-approve` отказывает
   `test_approver_changed` с советом нового предложения, а не подписывает.
5. discovery: висячая симлинка `config.toml` — отказ, не «конфига нет».

---

### Task 1: самодостаточный загрузчик, ключи harness, баннер (devtools, харнесс)

**Files:**
- Modify: `governance/df_config.py`, `governance/act_settings.py`
- Test: `tests/test_df_config.py`, `tests/test_act_settings.py`, `tests/test_harness_trust.py`

**Interfaces:**
- Produces: `df_config.config_home(env: Mapping[str, str]) -> Path` (переехала; правило то же);
  `act_settings.config_home` — тот же объект (реэкспорт).
- Produces: `df_config.STRICT_GH_DIRS: tuple[str, ...]`.
- Produces: `_GETTERS` с `harness.author`, `harness.model`.
- Produces: `df_config.TEST_RELAXATIONS: tuple[str, ...]` =
  `("репо политики утверждения не используется: утверждающий — accounts.human",)`;
  `df_config.test_banner(cfg: DfConfig) -> str | None` — `None` в strict, иначе
  `"режим test: выключено — " + "; ".join(TEST_RELAXATIONS)`.

- [ ] **Step 1: тесты.**
  - `test_df_config_imports_stdlib_only`: AST модуля — ни одного импорта `governance.*`.
  - `test_strict_gh_dirs_match_sandbox`: `df_config.STRICT_GH_DIRS == secret_dirs.SECRET_DIRS`.
  - `test_act_settings_config_home_is_df_config`: `act_settings.config_home is df_config.config_home`.
  - `test_get_harness_keys`: CLI `get harness.author` → `claude`, `get harness.model` → модель
    из конфига.
  - `test_banner_none_in_strict`, `test_banner_lists_relaxations_in_test`.
  - `test_login_must_be_single_github_login` (параметризовать: `"alice,bob"`, `"alice bob"`,
    `" alice"`, `""`, `"-alice"`, `"alice-"`, `"a--b"`, 40 символов; для `accounts.human` и
    `accounts.agent`, в обоих режимах) → `ConfigError` с ключом; `"ai-prosto"`,
    `"andrei-shtanakov"`, `"a1-b2"`, 39 символов — проходят.
  - CLI `python3 -I governance/df_config.py get mode` из чужого cwd — работает (без загрузки
    пакета по пути).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; убрать блок `importlib` загрузки пакета; докстринг — «импорты
  только stdlib». Цепочка `tests/harness_trust.py`/authority-root: если перечни импортов
  df_config там закреплены — обновить.
- [ ] **Step 4:** PASS; плюс `tests/test_df_config*.py`, `tests/test_act_*.py`,
  `tests/test_author_sandbox_invariant.py`, `tests/test_harness_trust.py`,
  `tests/test_merge_pr.py`, `tests/test_human_merge*.py`.
- [ ] **Step 5:** commit `refactor(config): df_config is stdlib-only; harness keys; test banner`.

### Task 2: режим в журнале прогона (devtools)

**Files:**
- Modify: `governance/run_state.py` (`RunState`, `new_run`, валидатор), `governance/runner.py`
  (`start`, `resume`, `advance`, `reopen`, `verify` + дочерний прогон), `governance/spec_loop.py`
  (баннер на старте, `_dispatch`), `governance/brief_tools.py` (`propose`, `approve`)
- Test: `tests/test_governance_run_state.py`, `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: `df_config.load()`, `df_config.test_banner`.
- Produces: `RunState.mode: str = "strict"` (последнее поле); `run_state.validate_mode(mode: str) -> None`
  (`ValueError` на не-`strict|test`); `new_run(..., mode: str)`.
- Produces: `runner.mode_refusal(state: RunState) -> str | None` —
  `"конфиг DarkFactory: режим конфига {cfg} ≠ режиму прогона {run} ({run_id}) — начните новый прогон"`.

- [ ] **Step 1: тесты.**
  - `test_run_json_without_mode_loads_strict` (старый `run.json` без поля).
  - `test_start_records_config_mode` (`test`-конфиг → `run.json["mode"] == "test"`).
  - `test_resume_refuses_mode_mismatch` (параметризовать strict→test и test→strict;
    также `advance`, `reopen`, `verify`, `brief_tools propose`, `brief_tools approve`): код 2,
    текст отказа, `run.json` не изменён, побочных действий нет (фейковые ops не вызваны).
  - `test_verify_child_inherits_parent_mode`.
  - `test_resume_same_mode_proceeds`.
  - `test_banner_printed_on_start_and_resume_in_test` (stderr содержит `режим test:`), в strict
    — не печатается.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; проверка — сразу после `load(run_id)`, до любых действий.
- [ ] **Step 4:** PASS; плюс `tests/test_governance_spec_loop.py`.
- [ ] **Step 5:** commit `feat(runner): record config mode in run.json; refuse resume on mismatch`.

### Task 3: синтетическая политика test (devtools, харнесс)

**Files:**
- Modify: `governance/policy_rule.py`, `governance/brief_facts.py` (реальные
  `policy_version_fact`, `policy_version_fact_at`, `repo_file_fact`)
- Test: `tests/test_policy_rule_source.py` (новый, если нет подходящего), `tests/test_governance_ops_brief_facts.py`, `tests/test_brief_tools.py`

**Interfaces:**
- Produces: `policy_rule.TEST_POLICY_SOURCE = ("darkfactory-config", "test", "accounts.human")`.
- Produces: `policy_rule.TestPolicy(sha: str, content: str)` (frozen dataclass);
  `policy_rule.test_policy() -> TestPolicy | None` — `None`, если файла конфига нет или
  `mode == "strict"`; битый конфиг → `RuntimeError` (как `policy_source`).
- Changes: `policy_source()` в `test` → `TEST_POLICY_SOURCE` (SSOT и `[policy]` не читаются).
- Changes: в `brief_facts` для координат `TEST_POLICY_SOURCE`: версия → `FOUND(sha)`;
  `…_at(sha)` → `FOUND(sha == текущий)`; `repo_file_fact(repo, sha, path)` → `FOUND(content)`
  при совпадении sha, иначе `ABSENT`. Ни одного вызова `gh`. Для прочих координат — как сегодня.

- [ ] **Step 1: тесты.**
  - `test_policy_source_strict_and_no_config_unchanged` (Review Focus 1).
  - `test_policy_source_test_mode_ignores_policy_table` (`[policy]` ≠ SSOT → без отказа,
    Review Focus 3).
  - `test_test_policy_sha_is_content_digest`: `sha == sha256(content)[:40]`, 40 hex.
  - `test_brief_facts_test_coords_never_call_gh` (стаб `gh` падает при вызове).
  - `test_brief_facts_test_coords_other_sha_absent`.
  - Сквозной на фейковом форджe (приём `tests/test_brief_tools.py`): `test`-конфиг
    `human = agent = "alice"` → `propose` пинит sha синтетической политики → PR смержен
    `alice` → `approve` проходит `read_act` + `check_policy`; смержен `bob` →
    `merger_not_in_act_policy`; `accounts.human` сменён на `carol` после `propose` →
    `test_approver_changed` с текстом решения (Review Focus 4) — и в `brief_provenance.read_act`,
    и в проверке пина `brief_tools approve`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; отказ `test_approver_changed` — только для координат
  `TEST_POLICY_SOURCE` при пине ≠ текущей версии. `approval_facts.policy_snapshot` не трогать — он читает через те
  же факты. `acceptance_provenance` (свои `policy_file`/`policy_on_ref`) — вне 3a (план 3c).
- [ ] **Step 4:** PASS; плюс `tests/test_governance_approval_facts.py`, `tests/test_brief_*.py`,
  `tests/test_human_merge*.py`, `tests/test_harness_trust.py`.
- [ ] **Step 5:** commit `feat(policy): synthetic approval policy from config in test mode`.

### Task 4: `human-merge.sh` одной учёткой в test (devtools, харнесс)

**Files:**
- Modify: `human-merge.sh`
- Test: `tests/test_human_merge*.py`

**Interfaces:**
- Consumes: `df_cfg mode`, `df_cfg accounts.human`; Task 3 (`brief_merge_check` идёт через
  `brief_facts` — синтетическая политика работает без правок проверяльщика).

- [ ] **Step 1: тесты.**
  - `test` : `human_gh_config_dir == agent_gh_config_dir` — не отказ; в `strict` — отказ
    как сегодня.
  - `test`: логин профиля == `accounts.human` → мерж; ≠ → код 3 «… не accounts.human»; чтения
    репо политики (GraphQL) нет — лог стаба `gh`.
  - `test` и `strict`: выставленный `AUTHORIZED_APPROVER_ACCOUNTS` → код 3 как сегодня.
  - `brief/*` в `test` — `brief_merge_check` вызывается и проходит на синтетической политике.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать ветку по `df_cfg mode`; strict-путь побайтово прежний.
- [ ] **Step 4:** PASS; `tests/test_harness_trust.py`.
- [ ] **Step 5:** commit `feat(human-merge): one account in test mode (approver = accounts.human)`.

### Task 5: TODO plan 3 у фикстуры discovery (devtools)

**Files:**
- Modify: `tools/gen_discovery_approval_fixture.py`, `tests/test_no_owner_identities.py`

- [ ] **Step 1:** генератор до `cli.main` выставляет `XDG_CONFIG_HOME` во временный каталог
  с strict-конфигом (`human = HUMAN`, агент — нейтральный `fixture-agent`, `[policy]` =
  `POLICY_REPO`/`main`/`policy/approvers.env`, `workspace.root` — tmp, `[harness]`); TOML —
  строкой в генераторе (он исполняется в venv discovery, импорт тестов devtools невозможен).
- [ ] **Step 2:** в стражe причина исключения генератора: «данные подписанной фикстуры
  (байты подписаны настоящим discovery на пине), не учётки конвейера»; слов «TODO plan 3» нет.
- [ ] **Step 3:** `tests/test_no_owner_identities.py`, тесты фикстуры T40 — PASS; байты
  фикстуры не меняются.
- [ ] **Step 4:** commit `chore(fixture): discovery approval generator runs with its own config`.

## PR 1 (devtools)

`feat/df-config-3a`: Tasks 1–5. Мержит владелец (харнесс: `df_config.py`, `policy_rule.py`,
`brief_facts.py`, `human-merge.sh`, `act_settings.py`). Флаг `--authority-override` не нужен.

---

### Task 6: вендоренная копия загрузчика в discovery (репо discovery, после мержа PR 1)

Работа — в чекауте discovery (`../discovery`), своя ветка `feat/df-config-vendor`.

**Files:**
- Create: `src/discovery/darkfactory/__init__.py`, `src/discovery/darkfactory/df_config.py`
  (байты `governance/df_config.py` devtools на коммите мержа PR 1),
  `src/discovery/darkfactory/PINNED.txt` (формат как `src/discovery/contract/PINNED.txt`:
  `upstream:`, `commit:`, строка `df_config.py <sha256>`)
- Create: `tools/check_df_config_vendor.py` — режимы `consistency|provenance|drift` на функциях
  `tools/check_vendor.py` (импорт, не копия сетевого слоя); апстрим
  `andrei-shtanakov/devtools`, путь `governance/df_config.py`
- Modify: `.github/workflows/vendor-integrity.yml` (consistency + provenance),
  `.github/workflows/vendor-drift.yml` (drift), `pyproject.toml` (ruff `extend-exclude` копии)
- Create: `tests/conftest.py` — autouse-фикстура: `XDG_CONFIG_HOME` → tmp со strict-конфигом;
  маркер `no_df_config` — пустой каталог
- Test: `tests/test_df_config_vendor.py` (по образцу `tests/test_vendored_copy.py`)

- [ ] **Step 1:** тесты: sha копии = пину; правка байта копии → `consistency` код 1; копия
  импортируется как `discovery.darkfactory.df_config` и грузит strict-конфиг из фикстуры.
- [ ] **Step 2:** FAIL → скопировать байты, пин, инструмент, CI → PASS (`uv run pytest`,
  ruff, pyrefly).
- [ ] **Step 3:** commit `feat: vendor DarkFactory config loader (pinned devtools copy)`.

### Task 7: утверждающий из конфига в discovery

**Files:**
- Modify: `src/discovery/policy.py` (`allowlist`)
- Test: `tests/test_policy.py`, `tests/test_approve_cli.py`

**Interfaces:**
- Consumes: `discovery.darkfactory.df_config.load`, `.config_path`, `.ConfigError`.
- Changes: `allowlist(forge)`: (1) env-отказ — как сегодня; (2) нет файла конфига
  (`not os.path.lexists`) → как сегодня; (3) `ConfigError` → `PolicyRefused("конфиг
  DarkFactory: …")`; (4) `mode == "test"` → `frozenset({cfg.accounts.human})` без обращения к
  форджу и `source()`; (5) strict → как сегодня.

- [ ] **Step 1: тесты.** `test_allowlist_test_mode_is_config_human` (фордж падает при вызове);
  `test_allowlist_no_config_reads_forge`; `test_allowlist_dangling_symlink_refused`
  (Review Focus 5); `test_allowlist_broken_config_refused`; `test_allowlist_env_still_refused_in_test`;
  `approve` CLI в test: мержер `alice` = `accounts.human` → подписано, `bob` →
  `APPROVER_NOT_AUTHORIZED`.
- [ ] **Step 2:** FAIL → реализовать → PASS (весь `uv run pytest`, ruff, pyrefly).
- [ ] **Step 3:** commit `feat(policy): approver from DarkFactory config in test mode`.

## PR 2 (discovery)

`feat/df-config-vendor`: Tasks 6–7. По `discovery/CLAUDE.md` — мерж агентом после ревью
(`authority-guard: false`); ревью — терминальное, бюджет 1 полный + 1 адресный.

---

### Task 8: совместный локальный тест devtools → discovery (devtools, после мержа PR 2)

**Files:**
- Create: `tests/test_discovery_test_mode_local.py`

- [ ] **Step 1:** тест пропускается (`skip` с причиной), если нет `workspace_dir("discovery")`
  или в нём нет `src/discovery/darkfactory/df_config.py` (CI без соседей). Иначе:
  - байты вендоренной копии == `governance/df_config.py` (тот же пин);
  - `test`-конфиг `human = agent = "alice"`: devtools `policy_rule.test_policy()` даёт состав
    `{alice}`; в venv discovery (`uv run --frozen --project <discovery> python -I -c …`,
    `cli.build_forge` подменён фейковым форджем, как в `tools/gen_discovery_approval_fixture.py`)
    `discovery approve` с мержером `alice` подписывает, с `bob` — `APPROVER_NOT_AUTHORIZED`;
  - конфиг `human = "alice,bob"`: и devtools `df_config.load()`, и discovery `allowlist` —
    отказ «конфиг DarkFactory: … accounts.human».
- [ ] **Step 2:** прогнать локально (в основном чекауте с соседом discovery) — PASS; в отчёте
  — вывод прогона.
- [ ] **Step 3:** commit `test: local devtools→discovery test-mode parity`.

## PR 3 (devtools)

`test/df-config-3a-parity`: Task 8 (только `tests/`). Мерж агентом после ревью.

