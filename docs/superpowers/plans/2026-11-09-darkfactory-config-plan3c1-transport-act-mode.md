# DarkFactory config — план 3c-1: ревью комментарием, режим акта без корня доверия, долги якоря

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** в `mode = "test"` прогон на машине коллеги проходит гейт режима акта без
`/etc/df/trust.toml`, а `review-pr.sh` публикует вердикт комментарием с вердиктом в маркере
(GitHub не даёт одобрить свой PR) и сам читает такие вердикты; закрыты долги 3a/3b на
харнесс-путях.

**Результат точно:** фундамент для 3c-2 (автоволны, вердикт в журнале) и 3c-3 (задачи,
`spec-runner run`, `accept-pr` без мержа). Сам по себе 3c-1 автоматизации волн не даёт.
`strict` не меняется ни в одной строке поведения.

**Architecture:** две точки решения по режиму. (1) `act_status.mode_probe` в прогоне `test`
возвращает синтетическую политику «до миграции» (`LivePolicy(PRE_MIGRATION, None…)` — ровно то,
что тесты уже подставляют в `tests/conftest.py`), поэтому гейт S4, `approve_node`, `act_node`
идут маршрутом §I12 «мерж = акт» без корня доверия. (2) `review-pr.sh` в `test` публикует
`--comment` вместо `--approve`/`--request-changes`, вердикт — полем маркера; его собственные
читатели (наследование по отпечатку, стоп-правило, красный гвард, дедуп аттестации) принимают
`COMMENTED` с вердиктом в маркере только в `test`.

**Tech Stack:** Python 3.12 (uv), POSIX sh, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` §5.3 п.7
(«отключается только транспорт одобрения, не вердикт»), §5.3 абзац 1 (режим в журнале), §5.8 (D10).
Разведка: исполнитель 09.11 (отчёт в сессии; ключевые места названы в задачах).

## Разбиение плана 3c (исполнитель, 09.11)

- **3c-1 (этот, мержит владелец — authority-root + харнесс):** транспорт ревью в `review-pr.sh`,
  синтетический режим акта, долги якоря и `human-merge.sh`, баннер.
- **3c-2 (агент, не authority-root):** edge-check комментарием + вердикт (голова + хэши) в
  журнале; раннер сам мержит candidate через `human-merge.sh` с записью «акт человека не
  требуется (test)»; именованный пропуск «класс актора не применяется (test)» в
  `approve_node.finalize_merge_policy`/`_missing_approving_review`; `FLEET_ROOT` для целей из
  `targets.toml` вне `workspace.root`.
- **3c-3 (в основном агент):** автоутверждение задач (ready + мерж черновых tasks/approve-PR,
  `spec approve`), `spec-runner run`; `accept-pr` без мержа (цель из `targets.toml`, итог в
  журнале); долг `acceptance_provenance` (authority-root, не харнесс — агент с флагом).

3c-2 и 3c-3 пишутся после мержа 3c-1 — по коду, который он оставит.

## Решения исполнителя (проверить владельцу)

1. **Режим акта в `test` — синтетический «до миграции» в `act_status.mode_probe`** (одна
   точка для гейта, `approve_node`, `act_node`), а не ветки в каждом вызывающем. Решает
   `state.mode` прогона (не конфиг: вход прогона уже сверил их). Маршрут review/v1 (approve =
   акт) в `test` недостижим и не нужен.
2. **`act_barrier` в `test` — отказ целиком** («режим test: миграция акта не применяется»);
   `invalidate` в `strict` не трогает прогоны с `state.mode == "test"`.
3. **Маркер вердикта:** strict-маркер `<!-- codex-terminal-review head=… fp=… -->` байт в
   байт прежний; в `test` — `… fp=… verdict=approve|request-changes -->`. Читатели принимают
   `COMMENTED` только от учётки агента, только с полем `verdict=` и только когда `df_mode=test`;
   в `strict` `COMMENTED` по-прежнему «нет вердикта».
4. **Версия синтетической политики — одна функция** `df_config.test_approver_version(human)`
   (stdlib, sha256 строки `AUTHORIZED_APPROVER_ACCOUNTS=<human>\n`, первые 40), ею пользуются
   `policy_rule.test_policy` и `human-merge.sh` (через ключ CLI `test.approver_version`).
   Перепин копии в discovery — отдельным PR после мержа (как в 3b).

## Global Constraints

- `strict`: существующие тесты зелёные без правок ожиданий; strict-маркер и тела вердиктов
  байт в байт прежние.
- Режим в Python-шагах — `state.mode` прогона; в shell — `df_cfg_mode` (`mode-or-strict`:
  нет файла — strict, битый — отказ кодом 2).
- Новых форм запуска в `human-merge.sh` нет (`tests/harness_trust.py` зелёный без расширения
  `SAFE_COMMANDS`): только `df_cfg`/`df_cfg_mode`.
- `governance/df_config.py` остаётся stdlib-only (вендорится в discovery).
- Все файлы плана — authority-root + харнесс → один PR, мержит владелец (D10).
- Строки ≤ 88, ruff + pyrefly (в worktree — на rsync-копии с `--python-interpreter-path`
  venv worktree), docstring у публичных функций; полный набор ~50 мин — один раз в конце ветки.

## Review Focus

1. Прогон `strict` на машине без `/etc/df/trust.toml` → прежний `Unavailable` (гейт `GC-ACT-UNAVAILABLE`), синтетики нет (Task 1).
2. В `strict` чужой/собственный `COMMENTED`-обзор с полем `verdict=approve` в маркере → не вердикт: наследование не срабатывает, стоп-правило не видит approve (Task 3).
3. В `test` вердикт `request-changes` комментарием → следующий прогон видит красный (гвард и наследование работают так же, как от `CHANGES_REQUESTED`) (Task 3).
4. В `test` `accounts.human` сменился между проверкой brief-PR и мержем → `human-merge.sh` отказывает кодом 3 (Task 2).
5. `act_barrier --invalidate` в `strict` при наличии прогона `test` с тем же `(repo_slug, ws_id)` → прогон `test` не тронут (Task 1).

---

## PR (devtools, мержит владелец): Tasks 1–3

Ветка `feat/df-config-3c1`.

### Task 1: синтетический режим акта в `test`; `act_barrier`

**Files:**
- Modify: `governance/act_status.py` (`mode_probe`, ~890)
- Modify: `governance/act_barrier.py` (`invalidate` ~442, `_main` ~504)
- Test: `tests/test_act_status*.py` / `tests/test_act_barrier*.py` (там, где уже тестируются эти функции; `grep -ln "mode_probe\|act_barrier" tests/`)

**Interfaces:**
- Produces: `act_status.TEST_LIVE_POLICY = LivePolicy(act_trust.PRE_MIGRATION, None, None, None, None)`;
  `mode_probe(state, deps=None)` возвращает его, если `getattr(state, "mode", "strict") == "test"`,
  иначе прежнее `live_policy(...)`.
- Produces: `act_barrier.TEST_MODE_REFUSAL = "режим test: миграция акта не применяется"`.

Поведение `act_barrier`: `_main`, если режим конфига (нет файла — strict; битый — отказ
кодом 2) равен `test`, печатает `TEST_MODE_REFUSAL` в stderr и возвращает 2 до вызова
`live_policy`. `invalidate` пропускает прогоны с `state.mode == "test"`.

Важно для тестов: autouse-фикстура `tests/conftest.py` подменяет `mode_probe` целиком — тесты
Task 1 должны вызывать НАСТОЯЩУЮ функцию (сохранить ссылку до подмены или снять подмену
`monkeypatch.undo`-эквивалентом в самом тесте; `tests/conftest.py` — authority-root, правка
допустима, но минимальная).

- [ ] **Step 1: тесты.** `test_mode_probe_test_run_is_pre_migration` (state.mode="test", без
  привязки → `TEST_LIVE_POLICY`, `real_deps` не вызывался); `test_mode_probe_strict_run_without_binding_unavailable`
  (state.mode="strict", без привязки → `Unavailable` с «нет привязки корня доверия»);
  `test_act_barrier_refuses_in_test_mode` (якорный test-конфиг → код 2, stderr содержит
  `TEST_MODE_REFUSAL`, `live_policy` не вызывался); `test_invalidate_skips_test_runs` (два
  прогона одного `(repo_slug, ws_id)`, strict и test, оба с живой заявкой → инвалидирован только strict).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest -q <файлы act_status/act_barrier> tests/test_harness_trust*.py` — PASS.
- [ ] **Step 5:** commit `feat(act): synthetic pre-migration act mode for test runs; act_barrier refuses test`.

### Task 2: единая версия синтетической политики; `human-merge.sh` пересверяет пин брифа; интеграционный `check_merge`

**Files:**
- Modify: `governance/df_config.py` (новая функция, ключ CLI, `usage` знает `mode-or-strict`)
- Modify: `governance/policy_rule.py` (`test_policy` через `df_config.test_approver_version`)
- Modify: `human-merge.sh` (test-ветка ~234)
- Test: `tests/test_df_config.py`, `tests/test_human_merge.py`, `tests/test_brief_tools.py` (или новый `tests/test_brief_merge_check_test_mode.py`)

**Interfaces:**
- Produces: `df_config.test_approver_version(human: str) -> str` — `sha256(f"AUTHORIZED_APPROVER_ACCOUNTS={human}\n")[:40]`;
  CLI `df_config.py get test.approver_version` (только при `mode = "test"`, иначе отказ кодом 2
  «ключ только для режима test»).
- `policy_rule.test_policy()` — `content` прежний, `sha = df_config.test_approver_version(human)` (значение то же).

`human-merge.sh` test-ветка: для `brief/*` после проверки логина —
`current=$(df_cfg test.approver_version) || exit $?`;
`[ "$current" = "$brief_pin" ] || die 3 "утверждающий в конфиге сменился после проверки brief-PR (пин $brief_pin, актуальная $current) — мерж не создаст годного акта"`.
Прочие ветки head_ref в `test` — без изменений.

- [ ] **Step 1: тесты.** `test_test_approver_version_value` (`"alice"` → ровно
  `hashlib.sha256(b"AUTHORIZED_APPROVER_ACCOUNTS=alice\n").hexdigest()[:40]`);
  `test_cli_test_approver_version_strict_refused`; `test_policy_rule_sha_unchanged` (значение
  `test_policy().sha` до и после рефакторинга совпадает на фиксированном конфиге);
  `test_human_merge_test_brief_pin_rechecked` (test-конфиг с `human = "bob"`, логин профиля
  `bob`, stub-проверяльщик отдаёт `brief_pin = test_approver_version("alice")` → код 3, мерж не
  вызывался); `test_human_merge_test_brief_pin_matches_merges` (пин от `bob` → мерж вызван);
  `test_check_merge_real_test_config_accepts` и `…_after_approver_change_refused` (НАСТОЯЩИЙ
  якорный test-конфиг, без подмены `policy_source`/`policy_version_fact`: заявка на пин
  `test_approver_version("alice")` → `brief_merge_check.check_merge` принимает; после смены
  `human` на `bob` — отказ `policy_rule.TEST_APPROVER_CHANGED`).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest -q tests/test_df_config.py tests/test_human_merge.py tests/test_brief_tools.py tests/test_harness_trust*.py tests/test_governance_ops_brief_facts.py` — PASS.
- [ ] **Step 5:** commit `fix(human-merge): re-check the brief pin in test mode; one synthetic policy version`.

### Task 3: `review-pr.sh` — вердикт комментарием в `test`

**Files:**
- Modify: `review-pr.sh` (чтение режима, `publish()` ~489, маркеры ~1338/1361/1440, `STRICT_MARKER_JQ` ~773, `lr_delivered_state` ~805, `enforce_review_barrier` ~958, наследование по отпечатку ~1300, аттестация `publish_scope_attestation` ~1016/1115, шапка «ревьюер из [harness]» ~1450, `usage` ~73)
- Modify: `governance/df_config.py` (`TEST_RELAXATIONS` +2 строки)
- Modify: `CLAUDE.md` (строка `review-pr.sh` — одно предложение)
- Test: `tests/test_review_pr.py`, `tests/test_df_config.py`

Поведение:
- Режим читается всегда в начале (`df_mode=$(df_cfg_mode) || exit $?`), выбор харнесса 3b
  использует ту же переменную.
- `test`: `publish` вызывает `gh pr review --comment`; в маркер вердикта добавляется
  ` verdict=approve` или ` verdict=request-changes` (по действию); аттестация области —
  `--comment`, маркер аттестации прежний. Код выхода скрипта — прежний (0/1/…).
- Читатели последнего вердикта агента: в `test` ревью с `state == "COMMENTED"` и маркером с
  `verdict=` трактуется как `APPROVED`/`CHANGES_REQUESTED` соответственно (наследование по
  `head+fp`, стоп-правило, красный гвард, `--targeted` база); без `verdict=` — как сейчас («нет»).
  В `strict` поведение прежнее.
- Шапка «ревьюер из [harness] конфига (режим test)» — только если внешний `REVIEW_CMD` не
  перехватил ревьюера (долг 3b).
- `usage` называет слой `[harness]` в режиме test (долг 3b).
- `TEST_RELAXATIONS` (после двух строк 3b, в этом порядке):
  `"одобрение ревью: вердикт публикуется комментарием (GitHub не даёт одобрить свой PR), вердикт обязателен"`,
  `"корень доверия df (/etc/df/trust.toml) не используется: акт — по мержу (режим до миграции)"`.

- [ ] **Step 1: тесты.** `test_test_mode_publishes_comment_with_verdict` (approve → вызов
  `gh pr review … --comment`, тело содержит `verdict=approve` в маркере, нет `--approve`);
  `test_test_mode_request_changes_comment`; `test_test_mode_inherits_commented_verdict` (последний
  обзор агента `COMMENTED` с маркером `head+fp+verdict=approve` → `--use`/повтор наследует без
  вызова кита); `test_test_mode_barrier_sees_commented_approve` (стоп-правило срабатывает);
  `test_strict_ignores_commented_verdict_marker` (strict, тот же `COMMENTED`-обзор → «нет вердикта»,
  кит вызывается); `test_strict_marker_unchanged` (strict: маркер байт в байт `<!-- codex-terminal-review head=… fp=… -->`);
  `test_test_mode_scope_attestation_is_comment`; `test_header_no_config_line_with_external_review_cmd`;
  `test_usage_mentions_harness_layer`; баннер — кортеж `TEST_RELAXATIONS` целиком, пять строк в
  порядке: политика, песочница, цели, одобрение ревью, корень доверия.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `uv run pytest -q tests/test_review_pr.py tests/test_df_config.py tests/test_harness_trust*.py -p no:cache` — PASS; затем весь набор один раз (foreground-эквивалент: контроллер).
- [ ] **Step 5:** commit `feat(review): verdict as a comment in test mode; banner lines`.

**Мерж PR:** ревью по бюджету → владелец. После мержа — перепин `df_config.py` в discovery (агент), затем планы 3c-2 и 3c-3.
