# DarkFactory config — план 3c-H: харнесс-долги и удаление флага D9

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** закрыть долги харнесса, накопленные в 3a–4, и последним коммитом
удалить временный флаг `merge-pr.sh --authority-override` (D9).

**Architecture:** один PR в devtools. В нём пять задач: одно правило «нет файла = strict»,
коды `act_barrier`, текст scope-аттестации, баннер test, реестр идентичности
суждений, удаление флага. PR трогает харнесс-пути (`df_config`, `review-pr.sh`,
`merge-pr.sh`, `governance/act_`, `tests/test_act_`), поэтому **мержит владелец** (D10).
Агент только открывает PR и проводит ревью. Задача 5 (D9) идёт последним коммитом:
после мержа флага нет нигде.

**Tech Stack:** Python 3.12 (uv, pytest, ruff, pyrefly), POSIX sh.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` (§5.8, §7 п. 6;
D9, D10). Долги сверены по памяти работы, раздел 3c-2/3c-3/план 4.

## Global Constraints

- Режим `strict` владельца — регрессия недопустима: существующие strict-тесты зелёные без правки ожиданий.
- Каждый `subprocess.run` в `governance/` — с литералом первой программы из git/gh/tmux/spec-runner/security (`tests/test_author_sandbox_invariant.py`).
- Правило «нет файла (`os.path.lexists` ложно, включая висячую ссылку) = strict»; файл есть, но битый — `ConfigError` (не strict).
- Именованные пропуски test (точный текст, не менять): `ACT_SKIP_TEST = "акт человека не требуется (test)"` (runner), `ACTOR_SKIP_TEST = "класс актора не применяется (test)"` (approve_node), `TASKS_SKIP_TEST = "утверждение задач человеком не требуется (test)"` (auto_tasks), `ACCEPT_NO_MERGE_TEST = "мерж PR с кодом — за вами (test)"` (accept_pr), `NO_CHECKS_TEST = "проверок нет (test)"` (pr_checks).
- `df_config.py` запускается как `python3 -I governance/df_config.py` (без пакета) — он не импортирует другие модули `governance/`.
- Гварды `merge-pr.sh` 1–3 (candidate §I12, `human-merge-required`, …) и rename-aware гвард 4 без флага — поведение не меняется.
- SSOT `contracts/harness-paths/v1/paths.env` остаётся (его читает `accept_pr._HARNESS_PREFIXES` и `tests/harness_trust.py`).
- Полный набор: `PYTHONPATH=…/steward/src uv run pytest -q -p no:cache --deselect tests/test_governance_sandbox.py::test_create_call_dir_for_devtools_itself_as_target` (~60–70 мин).

## Review Focus

1. Конфиг-файл — висячая ссылка: `mode_or_strict()` обязан дать `ConfigError`, а не `strict` (lexists истинно). Тест — Task 1.
2. Strict-PR с правкой authority-root после удаления флага: `merge-pr.sh` отказывает тем же гвардом 4 и тем же кодом, что до флага; `--authority-override` теперь — неизвестный аргумент (2), ни одного вызова gh. Тест — Task 5.
3. `review-pr.sh` в strict после правки текста: вывод scope-аттестации и полного ревью байт в байт прежний. Тест — Task 2.
4. Баннер не отстаёт от кода: новый именованный пропуск без строки баннера ловится тестом, а не человеком. Тест — Task 3.
5. `act_barrier` на битом конфиге не даёт 0/1 (fail-closed) и не читает активацию. Тест — Task 2.

---

### Task 1: `df_config.mode_or_strict()` — одно правило «нет файла = strict»

**Files:**
- Modify: `governance/df_config.py` (новая функция; `main` ветка `mode-or-strict` через неё)
- Modify: `governance/author_launch.py:105-111` (`config_mode` делегирует)
- Modify: `governance/spec_loop.py:278-285` (удалить `_config_mode`, звать `df_config.mode_or_strict()`)
- Modify: `governance/accept_pr.py:489-493` (удалить `_config_mode`, то же)
- Modify: `governance/act_barrier.py:446-456` (`_config_refusal` через `mode_or_strict`; см. Task 2 про коды)
- Test: `tests/test_df_config.py`, `tests/test_governance_accept_pr.py:1095` (monkeypatch → `df_config.mode_or_strict`)

**Interfaces:**
- Produces: `df_config.mode_or_strict(env: Mapping[str, str] = os.environ) -> Literal["strict", "test"]` — `"strict"`, если `os.path.lexists(config_path(env))` ложно; иначе `load(env).mode`; `ConfigError` пробрасывается.

- [ ] **Step 1: Failing tests** в `tests/test_df_config.py`:
  - `test_mode_or_strict_no_file` → `"strict"` при пустом `XDG_CONFIG_HOME`;
  - `test_mode_or_strict_reads_test` → `"test"` при конфиге `mode="test"` (фикстура `tests/df_config_fixture.write_config`);
  - `test_mode_or_strict_dangling_symlink_raises` → `config.toml` — ссылка на несуществующий файл, `pytest.raises(df_config.ConfigError)`;
  - `test_mode_or_strict_broken_raises` → `"это не toml ["`, `pytest.raises(df_config.ConfigError)`;
  - `test_no_strict_rule_copies` — по исходникам `governance/*.py` и `lite/*.py`: подстрока `lexists(df_config.config_path(` встречается только в `act_status.py` и `policy_rule.py` (у них другая форма — возвращают конфиг/`None`; причина в комментарии теста).
- [ ] **Step 2:** `uv run pytest tests/test_df_config.py -q -k "mode_or_strict or strict_rule"` → FAIL (нет функции; копии в spec_loop/accept_pr/author_launch/act_barrier).
- [ ] **Step 3:** Реализовать функцию; `main` (`mode-or-strict`) и четыре места — через неё. `author_launch.config_mode` остаётся (её зовут `ops.py:604` и тесты песочницы), тело — `return df_config.mode_or_strict()`.
- [ ] **Step 4:** `uv run pytest tests/test_df_config.py tests/test_governance_accept_pr.py tests/test_governance_spec_loop*.py tests/test_governance_sandbox.py tests/test_act_barrier.py -q` → PASS; `uvx ruff check` / `ruff format --check`; pyrefly на изменённых файлах.
- [ ] **Step 5: Commit** `refactor(config): one no-file=strict rule — df_config.mode_or_strict()`.

### Task 2: коды `act_barrier` и текст scope-аттестации `review-pr.sh`

**Files:**
- Modify: `governance/act_barrier.py` (`_config_refusal` → различает битый конфиг и test; `_main`)
- Modify: `review-pr.sh:493-507` (`publish`), `:1181` (вызов из `publish_scope_attestation`)
- Test: `tests/test_act_barrier.py:1082`, `tests/test_review_pr.py:1806`

**Interfaces:**
- Consumes: `df_config.mode_or_strict()` (Task 1).
- Produces: `act_barrier` CLI: конфиг `test` → 2 (`TEST_MODE_REFUSAL`, как сейчас); битый конфиг → **3** (`EXIT_INCOMPLETE`), stderr начинается `конфиг DarkFactory:`. `publish <действие> [<что>]` в `review-pr.sh`.

Решение исполнителя (на проверку владельцу): битый конфиг — сбой чтения, а docstring `main` обещает 3 за «любой сбой чтения»; код 2 зарезервирован за «действие человеческого контекста отвергнуто». Цена ошибки: вызывающий, различающий 2 и 3, на битом конфиге увидит «неполно» вместо «отвергнуто»; оба кода барьер не проходят.

- [ ] **Step 1: Failing tests:**
  - переименовать `test_act_barrier_broken_config_refuses` → `test_act_barrier_broken_config_is_incomplete`: `main([...]) == 3`, `"конфиг DarkFactory:"` в stderr, `act_status.live_policy` не вызван (тот же приём, что в `test_act_barrier_refuses_in_test_mode`);
  - в `test_test_mode_scope_attestation_is_comment` добавить: `"опубликовано: --comment (scope-аттестация prose-only, режим test)" in res.stdout` и `"вердикт approve" not in res.stdout`;
  - новый `test_strict_scope_attestation_stdout_unchanged`: strict, тот же сид, `"опубликовано: --approve на demo#7" in res.stdout` (строка прежняя).
- [ ] **Step 2:** `uv run pytest tests/test_act_barrier.py tests/test_review_pr.py -q -k "broken_config or scope_attestation"` → FAIL.
- [ ] **Step 3:** `act_barrier`: `try: mode = df_config.mode_or_strict() except df_config.ConfigError as exc: print(f"конфиг DarkFactory: {exc}", file=sys.stderr); return EXIT_INCOMPLETE`; `mode == "test"` → `EXIT_REFUSED`. Docstring `main` — без изменений смысла. `review-pr.sh`: `publish` берёт необязательный `$2` — название публикуемого в test-строке (умолчание `вердикт $1`); `publish_scope_attestation` передаёт `"scope-аттестация $_psa_kind"`. Strict-ветка `echo` не трогается.
- [ ] **Step 4:** те же тесты + весь `tests/test_review_pr.py tests/test_act_barrier.py` → PASS; `sh -n review-pr.sh`; ruff/pyrefly.
- [ ] **Step 5: Commit** `fix(harness): act_barrier broken config is incomplete (3); scope attestation is not a verdict in test output`.

### Task 3: баннер test — все именованные пропуски и одна учётка

**Files:**
- Modify: `governance/df_config.py:66-86` (`TEST_RELAXATIONS`)
- Modify: `docs/darkfactory-for-colleagues.md` (раздел ограничений: CI вне GitHub Actions)
- Test: `tests/test_df_config.py:463` (`test_banner_lists_relaxations_in_test`), новый тест-инвариант; `tests/test_lite_docs.py`

**Interfaces:**
- Consumes: константы пропусков из Global Constraints (импорт только в тесте).
- Produces: `TEST_RELAXATIONS` = прежние 5 строк + 6 новых ровно в этом порядке:
  1. `"акт человека не требуется (test): candidate-PR мержит раннер после зелёных проверок"`
  2. `"класс актора не применяется (test): finalize не сверяет класс актора; kill switch действует"`
  3. `"утверждение задач человеком не требуется (test): tasks-PR, spec approve и approve-PR проводит раннер"`
  4. `"мерж PR с кодом — за вами (test): приёмка проверяет PR с кодом, но не мержит его"`
  5. `"проверок нет (test): у PR нет .github/workflows и чеков — CI не ждём; чеки CI вне GitHub Actions, пришедшие позже 2 минут, не учитываются"`
  6. `"одна учётка GitHub: accounts.human может совпадать с accounts.agent"`

- [ ] **Step 1: Failing tests:**
  - обновить ожидание `test_banner_lists_relaxations_in_test` до 11 строк;
  - `test_banner_names_every_test_skip`: для каждой из пяти констант (`runner.ACT_SKIP_TEST`, `approve_node.ACTOR_SKIP_TEST`, `auto_tasks.TASKS_SKIP_TEST`, `accept_pr.ACCEPT_NO_MERGE_TEST`, `pr_checks.NO_CHECKS_TEST`) — `any(line.startswith(const) for line in df_config.TEST_RELAXATIONS)`;
  - `test_banner_skip_constants_are_complete`: по исходникам `governance/*.py` — множество имён, совпадающих с `^[A-Z_]+_TEST = "` (верхний уровень), равно ровно этим пяти; новый пропуск без строки баннера валит тест;
  - `tests/test_lite_docs.py`: инструкция содержит `"GitHub Actions"` и `"2 минут"` в разделе ограничений.
- [ ] **Step 2:** `uv run pytest tests/test_df_config.py tests/test_lite_docs.py -q -k "banner or docs"` → FAIL.
- [ ] **Step 3:** дописать строки; в инструкцию — абзац: репо с CI вне GitHub Actions (внешний статус/App), чьи чеки приходят позже 2 минут, в test читается как «проверок нет», и spec/candidate-PR вмерживаются без них; лекарство — workflow в `.github/workflows` или strict.
- [ ] **Step 4:** `tests/test_df_config.py tests/test_lite_docs.py tests/test_governance_runner.py -k banner tests/test_lite_dfl.py` → PASS.
- [ ] **Step 5: Commit** `feat(config): test banner names every test-mode skip; doc the non-Actions CI limit`.

### Task 4: реестр идентичности суждений — test-ветки 3c-1…план 4

**Files:**
- Modify: `tests/test_act_identity_table.py` (`MODIFIED_EXISTING` `:132-205`, `JUDGMENT`, при нужде `NOT_JUDGMENT`)
- Test: двойники — существующие тесты в `tests/test_governance_runner.py`, `tests/test_governance_approve_node*.py`; недостающий двойник — новый тест там же

**Interfaces:**
- Consumes: формат строки `r(component, "<module>.<func>", "<литерал проверки>", f"{FILE}::<тест>", "<литерал утверждения>")` и ключи `"<module>.<func>#<проверка>"`, как у `runner._resume_wave#review_delivery`.
- Produces: нет (только реестр).

Устаревшие описания (сверено 10.11): `approve_node._missing_approving_review` («проверки прежние» — в test делегирует `_missing_journal_attestation`), `runner._step_candidate` («проверки-отказа нет» — в test зовёт `_auto_merge_candidate`), `runner._finalize_wave` (в test переаттестация при сдвиге головы), `runner._resume_wave` (ветка `OPEN` + test → повтор автомержа). Новые функции с отказами, не описанные нигде: `approve_node._missing_journal_attestation`, `runner._auto_merge_candidate`, `runner._attest_finalize`, `runner._attested_head_moved`; изменённые: `approve_node.finalize_merge_policy` (`mode="test"` → `ACTOR_SKIP_TEST`), `approve_node._await_required_checks` (`on_no_checks`).

- [ ] **Step 1:** для каждой функции из списка прочитать тело и выписать условные пути к отказу (возврат текста отказа, стоп-статус, `return False` с записью стопа). Для каждого — строка `JUDGMENT` (литерал проверки, встречающийся в исходнике функции ровно один раз) с двойником, утверждающим ТЕКСТ отказа (не код/тип). Функции без отказов — описание-строка в `MODIFIED_EXISTING`, пропуск-не-отказ (`ACTOR_SKIP_TEST`, `on_no_checks`) — тоже описание с указанием, почему не суждение.
- [ ] **Step 2:** `PYTHONPATH=…/steward/src uv run pytest tests/test_act_identity_table.py -q` — новые строки, у которых двойника нет, падают; написать недостающие двойники.
- [ ] **Step 3: мутационная проверка** каждой новой строки: временно обойти проверку (условие → `False`) → её двойник падает на утверждении (не исключением); откатить. Протокол (строка → тест → «упал на assert») — в отчёт.
- [ ] **Step 4:** `PYTHONPATH=…/steward/src uv run pytest tests/test_act_identity_table.py tests/test_governance_runner.py tests/test_governance_approve_node*.py -q` → PASS; `git diff --stat` — правки только в `tests/`.
- [ ] **Step 5: Commit** `test(act): identity table covers test-mode branches (3c-1…plan 4)`.

### Task 5 (последний коммит PR): удалить `--authority-override` (D9)

**Files:**
- Modify: `merge-pr.sh` (разбор флага `:282-319`, usage `:163-173`, пол и чтение харнесс-SSOT `:360-382`, чистота/доверенный чекаут `:394-445`, `:579-600`, обход гварда 4, причина в журнал/комментарий; комментарии `:42`, `:122`)
- Modify: `tests/test_merge_pr.py` (тесты флага `:1152-1544`), комментарии `tests/test_governance_authority_root.py:31-32`, `tests/test_governance_accept_pr.py:895`
- Test: `tests/test_merge_pr.py`

**Interfaces:**
- Consumes: ничего из Tasks 1–4.
- Produces: `merge-pr.sh` без флага; остаются rename-aware гвард 4 (554ec77), чтение `df_config.sh`, SSOT `contracts/harness-paths/` (для accept_pr).

- [ ] **Step 1: Failing tests:**
  - `test_authority_override_flag_is_gone`: `merge-pr.sh demo 7 --squash --authority-override 'x' …` → rc 2, stderr — ошибка неизвестного аргумента, `gh` не вызывался;
  - `test_usage_has_no_authority_override`: usage без `authority-override`;
  - `test_no_authority_override_in_shipped_code`: подстрока `authority-override` отсутствует в `merge-pr.sh`, `human-merge.sh`, `governance/**/*.py`, `lite/**/*.py`, `docs/darkfactory-for-colleagues.md`;
  - сохранить/перенести без флага: `test_rename_out_of_authority_root_is_refused_without_flag`, `test_trusted_checkout_is_not_required_without_flag` (переименовать без `_without_flag`, если флага больше нет), `test_ops_merge_never_passes_authority_override` удалить (покрыт тестом подстроки).
- [ ] **Step 2:** `uv run pytest tests/test_merge_pr.py -q -k "gone or usage_has_no or shipped_code"` → FAIL.
- [ ] **Step 3:** удалить флаг и весь код, достижимый только с ним; тесты `test_authority_override_*` и `test_hard_floor_*` удалить. Комментарии про `--authority-override` в тестах authority_root/accept_pr переписать: SSOT харнесс-путей читает `accept_pr`.
- [ ] **Step 4:** `sh -n merge-pr.sh`; `uv run pytest tests/test_merge_pr.py tests/test_governance_authority_root.py tests/test_governance_accept_pr.py tests/test_harness_trust.py -q` → PASS; затем полный набор (команда из Global Constraints) → 0 failed.
- [ ] **Step 5: Commit** `feat(merge-pr)!: remove --authority-override (D9 — temporary flag of the darkfactory-config work)`.

---

## После задач

1. Финальное ревью ветки (самая сильная модель), один раунд правок.
2. PR в devtools: в описании — «харнесс, мержит владелец (D10)»; решения исполнителя (код 3 на битом конфиге; текст 6 строк баннера; `author_launch.config_mode` оставлен делегатом).
3. Платное ревью: `sh review-pr.sh devtools <pr> --dry-run --write-verdict <file>`, затем `--use-verdict <file>` (бюджет 1 полный + 1 адресный).
4. Агент **не мержит**: после approve — ссылка владельцу. После мержа владельцем — `git pull --ff-only`, удалить worktree/ветку, запись в память: работа darkfactory-config закрыта, D9 выполнен.
