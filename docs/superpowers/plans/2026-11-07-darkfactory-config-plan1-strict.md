# DarkFactory config — план 1: конфиг и strict без учёток владельца

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** все учётки, профили и источник политики, зашитые под владельца в devtools, читаются из
`$XDG_CONFIG_HOME/darkfactory/config.toml`; режим `strict` ведёт себя как сегодня.

**Architecture:** один Python-загрузчик `governance/df_config.py` (stdlib `tomllib`) с CLI
`get <ключ>` для shell; shell-скрипты берут умолчания через `df_config.sh`; Python-места — через
загрузчик. Загрузчик и его подключение — authority-root и харнесс-цепочка (их PR мержит
владелец, D10). Перечень харнесс-путей выносится в SSOT-файл, чтобы его мог читать и
`merge-pr.sh` (проверка при флаге обхода).

**Tech Stack:** Python ≥ 3.12 (`tomllib`), POSIX sh, pytest через `uv run`, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` (2313acc).

**Объём.** Это план 1 из четырёх (дизайн §7). Следующие пишутся после мержа этого, по его
интерфейсам: план 2 — discovery (вендоренный загрузчик, источник политики); план 3 — режим
`test` (§5.3); план 4 — `install.sh`, `dfl init-repo`, `dfl run`, README, удаление флага (D9).

## Global Constraints

- Файл: `$XDG_CONFIG_HOME/darkfactory/config.toml`; каталог — правилом
  `governance.act_settings.config_home(env)` (абсолютный `XDG_CONFIG_HOME`, иначе `~/.config`).
- `schema = "darkfactory-config/v1"`; `mode` ∈ {`strict`, `test`}, умолчания нет.
- Нет файла / нет ключа / неверный тип / неизвестный `mode` / ключ, обязательный для режима,
  отсутствует — отказ с названием файла и ключа. **Никаких умолчаний и fallback на
  `ai-prosto`, `andrei-shtanakov`, `andrei-shtanakov/approval-policy`** (анти-цель).
- Переменные окружения, переопределяющие профили сегодня (`REVIEW_GH_CONFIG_DIR`,
  `REVIEW_LOGIN`, `MERGE_GH_CONFIG_DIR`, `AGENT_LOGIN`, `HUMAN_GH_CONFIG_DIR`), сохраняют
  приоритет над конфигом. Запрет env для списка утверждающих (`human-merge.sh:117`) остаётся.
- `strict`: `accounts.human_gh_config_dir`, `accounts.agent_gh_config_dir` обязаны быть
  `~/` + элемент `governance.sandbox.SECRET_DIRS` (сегодня `.config/review`, `.config/gh`,
  `.config/ai-prosto`, `.ssh`) — иначе отказ с названием ключа (R3).
- Каждый новый/изменённый файл, исполняемый `merge-pr.sh`/`human-merge.sh`, — в
  `contracts/authority-root/v1/paths.env` И в перечне харнесс-путей; `tests/harness_trust.py`
  зелёный.
- Ветки `<type>/<slug>`, PR в devtools, ревью — `review-pr.sh` по правилам CLAUDE.md (1 полный +
  1 адресный). Мерж: PR, трогающие харнесс-пути, — **владелец**; остальные authority-root —
  агент `merge-pr.sh --authority-override '<причина>'` (после Task 1).
- `uv run ruff format .`, `uv run ruff check .`, `uv run pyrefly check` чистые; строки ≤ 88.

## Review Focus

1. **Хост без конфига** — CI devtools, VPS, прогон тестов:
   код, читающий конфиг на импорте, уронил бы всё. Ожидание: загрузчик вызывается лениво (в
   функции, не на импорте); тесты задают `XDG_CONFIG_HOME` фикстурой; отсутствие конфига
   ломает только команды, которым он нужен. Тест — Task 2 (`test_import_without_config`).
2. **`~` и относительные пути в конфиге** — человек напишет `~/.config/review`. Ожидание:
   `~` раскрывается в домашний каталог владельца файла-процесса; относительный путь — отказ.
   Тест — Task 2.
3. **Env и конфиг расходятся** (`REVIEW_LOGIN=x` при `accounts.agent=y`): ожидание — env
   выигрывает там, где выигрывал сегодня, и сверка логина профиля (`review-pr.sh:89`) работает
   по итоговому значению. Тест — Task 3.
4. **Дубль ключа/лишний ключ в TOML** (опечатка `acounts`): ожидание — отказ с названием
   неизвестного ключа, не молчаливое игнорирование. Тест — Task 2.
5. **Флаг обхода на харнесс-пути** — прямой вызов `merge-pr.sh --authority-override` на PR,
   трогающем `merge-pr.sh` или `governance/df_config.py`: ожидание — отказ (D10). Тест — Task 1.

---

### Task 1: SSOT харнесс-путей + флаг обхода в `merge-pr.sh` (PR-0, мержит владелец)

**Files:**
- Create: `contracts/harness-paths/v1/paths.env` (`HARNESS_PREFIXES=` — нынешний кортеж
  `governance/accept_pr.py:85-…` через пробел, комментарии переносятся)
- Modify: `governance/accept_pr.py:85` — `_HARNESS_PREFIXES` читается из файла
  `ssot_env.read_key` (имя атрибута сохраняется: его читает `tests/harness_trust.py:526`)
- Modify: `merge-pr.sh` — флаг `--authority-override <причина>`; гвард 4 (`:587-650`)
- Modify: `contracts/authority-root/v1/paths.env` — добавить `contracts/harness-paths/`
- Test: `tests/test_merge_pr.py`, `tests/test_governance_accept_pr.py` (существующий), `tests/test_harness_trust.py`

**Interfaces:**
- Produces: файл `contracts/harness-paths/v1/paths.env`, ключ `HARNESS_PREFIXES`;
  `merge-pr.sh --authority-override '<причина>'` (код 2 без непустой причины).

- [ ] **Step 1: Failing tests** в `tests/test_merge_pr.py` (тем же стендом фейкового `gh`, что
  соседние тесты гварда 4):
  - `test_authority_override_requires_reason` — `--authority-override ''` → exit 2.
  - `test_authority_override_lifts_guard4` — дифф `contracts/acceptance-targets/x.toml`, флаг с
    причиной → мерж выполнен; stderr и комментарий PR содержат причину.
  - `test_authority_override_refuses_harness_path` — дифф `merge-pr.sh` и отдельно
    `governance/df_config.py` (после Task 2 он в перечне; здесь — любой путь из
    `HARNESS_PREFIXES`) → exit 3, текст «харнесс-путь … мержит владелец (D10)».
  - `test_guard4_unchanged_without_flag` — дифф authority-root без флага → exit 3 как сейчас.
  - В `tests/test_governance_accept_pr.py`: `test_harness_prefixes_from_ssot` — `accept_pr._HARNESS_PREFIXES`
    равен разбору файла.
- [ ] **Step 2:** `uv run pytest tests/test_merge_pr.py tests/test_governance_accept_pr.py -q` → новые FAIL.
- [ ] **Step 3: Implement.** Флаг разбирается рядом с `--expect-head` (`merge-pr.sh:239`) по образцу
  `--budget-override` в `review-pr.sh:126-133`. В гварде 4: без флага — как сейчас; с флагом —
  каждый файл диффа сверяется с `HARNESS_PREFIXES` (читается `ssot_key` из нового файла) →
  совпадение = `die 3`; иначе authority-root совпадения пропускаются, причина печатается и
  уходит комментарием `gh_a pr comment`. `Ops.merge` флаг не передаёт (не трогать
  `governance/ops.py`).
- [ ] **Step 4:** тесты Step 1 + `uv run pytest tests/test_harness_trust.py -q` → PASS.
- [ ] **Step 5:** commit `feat(merge-pr): --authority-override (temporary, D9) + harness-paths SSOT`;
  PR; ревью; **мерж владельцем** (флаг вводится этим же PR).

### Task 2: загрузчик `governance/df_config.py`

**Files:**
- Create: `governance/df_config.py`, `df_config.sh`
- Modify: `contracts/authority-root/v1/paths.env` (+`governance/df_config`, `df_config.sh`),
  `contracts/harness-paths/v1/paths.env` (те же)
- Test: `tests/test_df_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Accounts: human: str; agent: str; human_gh_config_dir: Path; agent_gh_config_dir: Path`
  - `@dataclass(frozen=True) class PolicySource: repo: str; ref: str; path: str`
  - `@dataclass(frozen=True) class DfConfig: mode: Literal["strict","test"]; accounts: Accounts; policy: PolicySource | None; workspace_root: Path; author_harness: str; author_model: str; app_id: int | None; app_key_file: Path | None`
  - `config_path(env: Mapping[str, str]) -> Path`
  - `load(env: Mapping[str, str] = os.environ) -> DfConfig` — читает при каждом вызове, без кэша на импорте
  - `get_value(cfg: DfConfig, key: str) -> str` — ключи CLI: `mode`, `accounts.human`,
    `accounts.agent`, `accounts.human_gh_config_dir`, `accounts.agent_gh_config_dir`,
    `policy.repo`, `policy.ref`, `policy.path`, `workspace.root`
  - CLI: `python3 -I governance/df_config.py get <key>` → значение в stdout, exit 0; ошибка →
    stderr с файлом и ключом, exit 2
  - `df_config.sh`: функция `df_cfg <key>` (вызов строго `v=$(df_cfg k) || exit $?`, как `ssot_key`)

- [ ] **Step 1: Failing tests** `tests/test_df_config.py` (фикстура пишет TOML во временный
  `XDG_CONFIG_HOME`):
  - `test_strict_full_config_loads` — пример §5.1 дизайна → `cfg.mode == "strict"`,
    `cfg.accounts.agent == "ai-prosto"`, `cfg.policy.repo == "andrei-shtanakov/approval-policy"`.
  - `test_missing_file_names_path` — нет файла → `ConfigError`, в тексте полный путь.
  - `test_unknown_mode`, `test_missing_schema`, `test_wrong_type_app_id` → `ConfigError` с ключом.
  - `test_unknown_key_refused` — `[acounts]` → `ConfigError` «неизвестный ключ acounts».
  - `test_strict_requires_policy` — `strict` без `[policy]` → `ConfigError` «policy».
  - `test_test_mode_policy_optional` — `test` без `[policy]` → `cfg.policy is None`.
  - `test_strict_gh_dir_outside_sandbox_refused` — `agent_gh_config_dir = "~/creds/r"` при
    `strict` → `ConfigError` с ключом; тот же путь при `test` — загружается.
  - `test_tilde_expanded_relative_refused` — `~/.config/review` → абсолютный путь;
    `"rel/dir"` → `ConfigError`.
  - `test_no_owner_fallback` — `grep` по `governance/df_config.py`: нет литералов
    `ai-prosto`, `andrei-shtanakov`.
  - `test_import_without_config` — `import governance.df_config` при пустом `XDG_CONFIG_HOME` не
    бросает.
  - `test_cli_get_and_error` — subprocess `python3 -I governance/df_config.py get accounts.agent`
    → `ai-prosto`, exit 0; неизвестный ключ → exit 2.
  - Shell: `test_df_cfg_sh` — `sh -c '. ./df_config.sh; v=$(df_cfg accounts.agent) || exit $?; echo $v'`.
- [ ] **Step 2:** `uv run pytest tests/test_df_config.py -q` → FAIL (модуля нет).
- [ ] **Step 3: Implement** по Interfaces. `config_path` использует
  `governance.act_settings.config_home`. Импорты — только stdlib и `governance.act_settings`,
  `governance.sandbox.SECRET_DIRS` (оба уже под authority-root; проверить, что `harness_trust`
  принимает цепочку). Модуль не импортирует `governance.ops`.
- [ ] **Step 4:** `uv run pytest tests/test_df_config.py tests/test_harness_trust.py -q` → PASS;
  ruff, pyrefly.
- [ ] **Step 5:** commit `feat(config): darkfactory config loader (strict/test, no owner defaults)`.
  PR объединяется с Task 3 (оба — харнесс, один мерж владельца).

### Task 3: shell-скрипты берут умолчания из конфига

**Files:**
- Modify: `review-pr.sh:92-93`, `attest-vendor.sh:92-93`, `merge-pr.sh:166-168`,
  `human-merge.sh:87-88, 117-121`
- Modify: `tests/harness_trust.py` — распознать запуск `python3 -I "$script_dir/governance/df_config.py"`
  через `df_cfg` (добавить `df_cfg` в `SAFE_COMMANDS` только если тело `df_config.sh`
  проходит тот же разбор; иначе — распознанная форма запуска)
- Test: `tests/test_merge_pr.py`, `tests/test_human_merge.py`, тест `review-pr.sh` (существующий
  набор `tests/test_review_pr.py`)

**Interfaces:**
- Consumes: `df_cfg <key>` (Task 2).
- Правило: `X="${ENV_X:-$(df_cfg key)}"` — env первым; при пустом env и ошибке конфига скрипт
  завершается кодом 2 с текстом загрузчика.
  - `review-pr.sh`, `attest-vendor.sh`: `REVIEW_GH_CONFIG_DIR` ← `accounts.agent_gh_config_dir`,
    `REVIEW_LOGIN` ← `accounts.agent`.
  - `merge-pr.sh`: `AGENT_LOGIN` ← `accounts.agent`; `MERGE_GH_CONFIG_DIR` по-прежнему по
    умолчанию = итоговый `REVIEW_GH_CONFIG_DIR`.
  - `human-merge.sh`: `HUMAN_GH_CONFIG_DIR` ← `accounts.human_gh_config_dir`; источник политики
    `p_repo/p_ref/p_path` ← `policy.repo/ref/path` вместо `contracts/approval-policy-source/v1/source.env`.

- [ ] **Step 1: Failing tests:**
  - `test_review_login_from_config` — без env, конфиг `accounts.agent = "bot-x"` → сверка логина
    профиля ожидает `bot-x`.
  - `test_env_overrides_config` — `REVIEW_LOGIN=bot-y` + конфиг `bot-x` → ожидается `bot-y`.
  - `test_no_config_exit2` — без env и без конфига → exit 2, текст называет путь конфига.
  - `test_human_merge_policy_from_config` — конфиг `policy.repo = "o/p"` → фейковый `gh`
    получает запрос к `repos/o/p/…`.
- [ ] **Step 2:** соответствующие `uv run pytest …` → FAIL.
- [ ] **Step 3: Implement** по правилу выше; `. "$script_dir/df_config.sh"` рядом с
  `. "$script_dir/ssot_env.sh"`. `contracts/approval-policy-source/v1/source.env` остаётся
  только как документация формата: добавить в начало комментарий «не читается с <дата>, источник —
  config.toml [policy]» (значения не удалять до плана 2: их читает discovery).
- [ ] **Step 4:** `uv run pytest tests/test_merge_pr.py tests/test_human_merge.py tests/test_harness_trust.py tests/test_review_pr.py -q` → PASS.
- [ ] **Step 5:** commit `feat(scripts): accounts and policy source from darkfactory config`.
  Один PR с Task 2; **мерж владельцем** (D10).

### Task 4: Python-места читают конфиг

**Files:**
- Modify: `governance/ops.py:47, 131, 1853, 1885, 1998` — функции `review_gh_config_dir() -> Path`
  и `review_login() -> str` (env → конфиг), три места с константой — через функцию;
  константы `REVIEW_GH_CONFIG_DIR`, `REVIEW_LOGIN_DEFAULT` удалить (найти все использования
  `grep -rn`)
- Modify: `governance/act_premerge.py:103`, `governance/act_status.py:111` — умолчание профиля
  через загрузчик
- Modify: `governance/acceptance_provenance.py:27` — `agent_login() -> str` из конфига;
  параметр `agent: str = AGENT_LOGIN` (`:192`) → `agent: str | None = None` → `agent_login()`
- Modify: `governance/policy_sources.py:33` — `load_safety(actor: str | None = None)`;
  `None` → `df_config.load().accounts.agent`; `governance/approve_node.py:86` без изменений
- Modify: `governance/policy_rule.py:35` — `policy_source()` из `cfg.policy` (strict; `None` →
  `RuntimeError` «policy не задан»)
- Modify: `governance/sandbox.py:404-405` — пробы связки ключей для `cfg.accounts.agent` и
  `cfg.accounts.human` (множество, без повторов)
- Test: `tests/test_governance_ops.py`, `tests/test_governance_acceptance_provenance.py`, `tests/test_governance_policy_sources.py`,
  `tests/test_governance_sandbox.py` (существующие наборы; отдельного теста `policy_rule` нет — новый тест в `tests/test_df_config_wiring.py`) +
  `tests/conftest.py` — autouse-фикстура, пишущая strict-конфиг с нынешними значениями во
  временный `XDG_CONFIG_HOME` (тесты сохраняют ожидания `ai-prosto` без литерала в коде)

**Interfaces:**
- Consumes: `df_config.load()`, `DfConfig` (Task 2).
- Produces: `ops.review_gh_config_dir()`, `ops.review_login()`, `acceptance_provenance.agent_login()`.

- [ ] **Step 1: Failing tests:**
  - `test_ops_review_dir_honours_env_everywhere` — `REVIEW_GH_CONFIG_DIR=/tmp/x` → три метода
    (`post_review…`, `close_pr…`, `delete_branch…` — точные имена по `ops.py:1849-2000`) зовут
    `gh` с `GH_CONFIG_DIR=/tmp/x` (регрессия: сейчас константа).
  - `test_agent_login_from_config` — конфиг `accounts.agent="bot-x"` → предикат
    `acceptance_provenance` сравнивает с `bot-x`.
  - `test_load_safety_actor_from_config` — `load_safety()` классифицирует `accounts.agent`.
  - `test_policy_source_from_config` — `policy_rule.policy_source() == ("o/p","main","policy/approvers.env")`.
  - `test_no_owner_literals` — `grep -rn "ai-prosto\|andrei-shtanakov" governance/{ops,acceptance_provenance,policy_sources,policy_rule,act_premerge,act_status}.py`
    пусто (комментарии-ссылки на историю допустимы только без кавычек-литералов; тест ищет
    `"ai-prosto"` и `'ai-prosto'`).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement.** Загрузчик вызывается внутри функций, не на импорте модуля.
- [ ] **Step 4:** **полный** `uv run pytest -q` → PASS (регрессия strict).
- [ ] **Step 5:** commit `feat(governance): agent/human/policy from darkfactory config`. Если PR
  трогает `policy_rule.py` или `act_*` (харнесс) — мерж владельцем; иначе агент с
  `--authority-override 'darkfactory-config plan 1 (D7)'`. Разнести так, чтобы харнесс-файлы
  (`policy_rule.py`, `act_premerge.py`, `act_status.py`) ушли в PR Task 2–3, а
  `ops.py`, `acceptance_provenance.py`, `policy_sources.py`, `sandbox.py` — в отдельный PR агента.

### Task 5: конфиг на хостах владельца и страж «нет зашитых учёток»

**Files:**
- Create: `templates/darkfactory/config.strict.example.toml` (значения владельца как пример;
  шаблон не читается кодом)
- Create: `tests/test_no_owner_identities.py`
- Modify: `README.md` — раздел «Конфиг DarkFactory (`config.toml`)»: путь, ключи, режимы,
  приоритет env, ограничение путей в strict
- VPS: на 07.11 `grep -rln "review-pr.sh\|REVIEW_GH\|governance.ops" deploy/` пуст — сервисы VPS
  этот код не зовут; повторить grep перед мержем PR B, при находке — конфиг и на VPS

**Interfaces:** Consumes: всё выше.

- [ ] **Step 1:** `test_no_owner_identities` — по `git ls-files '*.py' '*.sh'` вне `tests/`,
  `docs/`, `df/`, `contracts/steward-actor-policy/`, `templates/`: нет литералов `"ai-prosto"`,
  `'ai-prosto'`, `andrei-shtanakov/approval-policy`. Перечень исключений — в тесте явным списком
  с причиной у каждого (например `attest-vendor.sh` маркер `ai-prosto-vendor-attestation` —
  имя протокола).
- [ ] **Step 2:** FAIL → убрать оставшиеся литералы, найденные тестом, или внести в исключения с
  причиной.
- [ ] **Step 3:** владелец кладёт `~/.config/darkfactory/config.toml` (копия примера) на Mac
  (и VPS, если grep выше что-то нашёл) **до** мержа PR Task 2–3 (иначе его конвейер встанет на «нет конфига»). Проверка:
  `python3 -I governance/df_config.py get accounts.agent` → `ai-prosto` на каждом хосте.
- [ ] **Step 4:** полный `uv run pytest -q`; `sh review-pr.sh devtools <любой открытый PR> --dry-run`
  печатает профиль из конфига (без платного вызова: остановиться на шапке — флаг или прервать
  до кита, по `review-pr.sh --help`).
- [ ] **Step 5:** commit `test+docs: no owner identities in code; config README`; PR агентом
  (с флагом, если трогает authority-root).

## Порядок PR и мержей

| PR | Задачи | Мерж |
|---|---|---|
| A | Task 1 | владелец |
| B | Task 2 + Task 3 + харнесс-часть Task 4 | владелец (до мержа — конфиг на хостах, Task 5 Step 3) |
| C | не-харнесс часть Task 4 | агент, `--authority-override` |
| D | Task 5 | агент (флаг при необходимости) |
