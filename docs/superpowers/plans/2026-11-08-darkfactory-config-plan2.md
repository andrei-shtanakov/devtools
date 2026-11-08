# DarkFactory config — план 2: раскладка workspace и хвосты плана 1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** соседние репо (discovery, disputatio, цели приёмки) находятся через `workspace.root`
конфига, а не через `DEVTOOLS_ROOT.parent`; закрыты хвосты плана 1 (accept-pr `--owner`,
именованные ошибки конфига, проверка «файла нет», страж человеческого логина, допуск
песочницы с проверкой протокола).

**Architecture:** один новый помощник `df_config.workspace_root(env=None) -> Path` — источник
корня workspace для всех мест раскладки; остальное — точечные правки уже существующих мест.
Всё в одном PR, мержит владелец (затрагивает authority-root: `author_launch.py`,
`policy_rule.py`, `human-merge.sh`, `merge-pr.sh`, `sandbox.py`).

**Tech Stack:** Python 3.12 (uv), POSIX sh, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` (§4 «Раскладка и
цели», §5.1, §7 этап 2). План 1: `docs/superpowers/plans/2026-11-07-darkfactory-config-plan1-strict.md`.

## Решения плана (исполнитель, 08.11)

- **discovery в плане 2 не меняется.** В `strict` discovery читает только источник политики —
  вендоренную копию SSOT; по решению плана 1 источник политики всегда SSOT, конфиг его не
  перенаправляет. Перенос загрузчика в discovery нужен тестовому режиму — план 3.
- **Вне границ плана 2: `strict` у коллеги** (этап 2) — способ, которым коллега задаёт свою
  доверенную политику, отдельно согласуется с владельцем; форки как обязательство не
  подразумеваются (ревью codex 6a9778e).
- **`workspace_root`:** конфиг обязателен — нет файла → `ConfigError` (пользовательский запуск
  не выбирает раскладку молча); CI и тесты обеспечены автоматической фикстурой конфига
  `tests/conftest.py`. Битый конфиг → `ConfigError`. Новой переменной окружения нет (§5.1);
  существующий `FLEET_ROOT` shell-скриптов сохраняет приоритет.
- **Одна раскладка для Python и shell:** `review-pr.sh`, `merge-pr.sh`, `human-merge.sh`,
  `attest-vendor.sh` берут `FLEET_ROOT` из env, иначе `df_cfg workspace.root` (ревью codex
  6a9778e: иначе Python и shell смотрели бы в разные копии репо).
- **accept-pr `--owner`:** по умолчанию — владелец из origin локального чекаута цели; явный
  `--owner` сверяется как сегодня.

## Global Constraints

- Режим `strict` владельца — регрессия недопустима: существующие тесты зелёные.
- Значения владельца в коде не остаются ни как умолчания, ни как fallback.
- Коды выхода: 2 — конфигурация/аргументы; сообщения об ошибке конфига начинаются с
  «конфиг DarkFactory: ».
- Тесты — только на переднем плане. Единственное допустимое падение в worktree —
  `test_create_call_dir_for_devtools_itself_as_target` на его известном assertion (`.git` —
  файл); в обычном чекауте он обязан пройти — финальный прогон этого теста делается в основном
  чекауте devtools на ветке плана.
- Строки ≤ 88, ruff + pyrefly чистые.

## Review Focus

1. Конфиг владельца с `workspace.root = "~/labs/all_ai_orchestrators"` — раскладка прогона
   владельца побайтово та же (discovery, цели, disputatio, `FLEET_ROOT` скриптов) — тест на
   равенство путей.
1a. workspace отдельно от devtools (`workspace.root` ≠ `DEVTOOLS_ROOT.parent`) — и Python, и
   shell-скрипты смотрят в `workspace.root`, ни один не обращается к соседям devtools.
2. `workspace.root` указывает на несуществующий каталог — запуск discovery/цели отказывает с
   названным путём, а не падает трейсбэком в `subprocess`.
3. accept-pr на чекауте без origin / с не-GitHub origin без `--owner` — код 2 с причиной.
4. Конфиг — висячая симлинка: не «файла нет», а отказ (сверка не пропускается молча).
5. Запись допуска со ссылкой на отсутствующий протокол — отказ «не проверен под песочницей».

---

### Task 1: корень workspace из конфига

**Files:**
- Modify: `governance/df_config.py` (новый помощник)
- Modify: `governance/spec_loop.py:84-86,1212` (`WORKSPACE_ROOT` → функция)
- Modify: `governance/ops.py:2133` (путь discovery)
- Modify: `governance/author_launch.py:453` (venv disputatio в целях пробы)
- Modify: `governance/accept_pr.py:415` (`target_dir`)
- Modify: `review-pr.sh:102`, `merge-pr.sh:271`, `human-merge.sh:67`, `attest-vendor.sh:101`
  (`FLEET_ROOT`)
- Modify: `governance/df_config.py` `_GETTERS` — уже содержит `workspace.root`; проверить
- Test: `tests/test_df_config.py`, `tests/test_governance_spec_loop.py`, `tests/test_accept_pr.py`,
  `tests/test_review_pr.py`, `tests/test_merge_pr.py`, `tests/test_human_merge*.py`,
  `tests/test_attest_vendor*.py`, `tests/test_harness_trust.py`

**Interfaces:**
- Produces: `df_config.workspace_root(env: Mapping[str, str] | None = None) -> Path` —
  `load(env).workspace_root`; нет файла / битый → `ConfigError`.
- Produces (shell): в каждом из четырёх скриптов
  `if [ -z "${FLEET_ROOT:-}" ]; then FLEET_ROOT=$(df_cfg workspace.root) || exit $?; fi`
  (тот же приём, что для учёток в плане 1).
- Produces: `spec_loop.workspace_root() -> Path` — тонкая обёртка над помощником; тесты
  патчат её вместо атрибута `WORKSPACE_ROOT` (атрибут удаляется, `test_governance_spec_loop.py:654,1547`
  переходят на `monkeypatch.setattr(spec_loop, "workspace_root", lambda: tmp_path)`).

- [ ] **Step 1: тесты.**
  - `test_workspace_root_from_config`: конфиг с `root = "<tmp>/ws"` → `Path("<tmp>/ws")`.
  - `test_workspace_root_without_config_raises` (`@pytest.mark.no_df_config`) → `ConfigError`
    с путём файла.
  - `test_workspace_root_broken_config_raises` → `ConfigError`.
  - `test_workspace_root_dangling_symlink_raises`: `config.toml` — висячая симлинка →
    `ConfigError` (не fallback).
  - `test_owner_layout_unchanged`: пример `templates/darkfactory/config.strict.example.toml`
    с подменённым `HOME` → `workspace_root() / "discovery"` == `HOME/labs/all_ai_orchestrators/discovery`.
  - spec_loop: цель по умолчанию `workspace_root() / entry.repo`; ops: argv discovery
    содержит `--project <root>/discovery`; accept_pr: `target_dir == <root>/<repo>`.
  - shell, по одному на скрипт: `workspace.root` = отдельный tmp-каталог (не родитель
    devtools), `FLEET_ROOT` не задан → скрипт обращается к `<root>/<repo>` (по логу стаба git/gh
    или по сообщению об отсутствующем чекауте с этим путём); `FLEET_ROOT` задан → он важнее
    конфига; конфига нет и `FLEET_ROOT` не задан → код 2 «конфиг DarkFactory».
- [ ] **Step 2:** запустить — FAIL.
- [ ] **Step 3:** реализовать помощник и заменить четыре места. Все вызовы ленивые (не на
  импорте). `ConfigError` пробрасывается, как в остальных местах Ops. Нет каталога
  `<root>/discovery` (ops) или `<root>/<repo>` (spec_loop, accept_pr) → именованный отказ
  «конфиг DarkFactory: workspace.root=<p>: нет каталога <имя>» до `subprocess`
  (Review Focus 2); тест на каждое из трёх мест.
- [ ] **Step 4:** PASS; плюс `tests/test_author_sandbox_invariant*.py`, `tests/test_harness_trust.py`.
- [ ] **Step 5:** commit `feat(config): workspace root from darkfactory config` (харнесс —
  скрипты и `author_launch.py` — отдельным коммитом).

### Task 2: хвосты плана 1

**Files:**
- Modify: `governance/accept_pr.py:396-430` (`--owner`)
- Modify: `governance/ops.py:1738` (`Ops.agent_login`)
- Modify: `governance/approve_node.py` (`finalize_merge_policy` — `ConfigError` из `load_safety`)
- Modify: `governance/policy_rule.py:50`, `human-merge.sh:141` (проверка «файла нет»)
- Modify: `merge-pr.sh:461` (каталог ключей App — правило `act_settings.config_home`)
- Test: соответствующие `tests/test_*.py`

- [ ] **Step 1: тесты.**
  - `test_accept_pr_owner_defaults_to_checkout_origin`: origin `git@github.com:alice/kapelle.git`,
    без `--owner` → мерж адресован `alice/kapelle`.
  - `test_accept_pr_without_origin_and_owner_is_2`: нет origin → код 2, в выводе «--owner».
  - `test_accept_pr_explicit_owner_mismatch_is_2` — как сегодня.
  - `test_ops_agent_login_config_error_is_none_with_reason`: битый конфиг → `None` и сообщение
    «конфиг DarkFactory: …» в stderr (контракт `agent_login() -> str | None` — None уже значит
    «не установлено»).
  - `test_finalize_merge_policy_config_error_named`: битый конфиг → отказ с «конфиг
    DarkFactory: », без трейсбэка.
  - `test_policy_source_dangling_symlink_fails_closed` (Python) и аналог для
    `human-merge.sh`: висячая симлинка → отказ, сверка не пропущена.
  - `test_app_key_dir_relative_xdg_ignored`: `XDG_CONFIG_HOME=rel` → каталог ключей
    `~/.config/darkfactory/apps/` (как у конфига).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать. «Файла нет» = `not os.path.lexists(...)` / `[ ! -e f ] && [ ! -L f ]`.
  В `accept_pr` убрать литерал владельца из `default=` и докстринга.
- [ ] **Step 4:** PASS; `tests/test_merge_pr.py`, `tests/test_human_merge*.py`,
  `tests/test_harness_trust.py`.
- [ ] **Step 5:** два коммита: харнесс (`policy_rule.py`, `human-merge.sh`, `merge-pr.sh`) и
  остальное.

### Task 3: страж человеческого логина

**Files:**
- Modify: `tests/test_no_owner_identities.py`

- [ ] **Step 1:** страж ловит `andrei-shtanakov` и некавыченный `ai-prosto` (граница слова,
  не часть `ai-prosto-<маркер>` и не компонент пути `.config/ai-prosto`) в `.py`/`.sh` вне
  исключённых каталогов, включая комментарии кода (докстринги — тоже). Исключения — явным
  списком «файл → литерал → причина»:
  - `inbox.py` `DEFAULT_OWNER` — инструмент флота инстанса владельца, не конвейер;
  - `issue_console.py` — файл неприкосновенен (правило репо);
  - `r16_runner.py` — задача VPS владельца;
  - `tools/check_discovery_vendor.py`, `tools/check_discovery_approval_vendor.py` — апстрим
    вендоринга владельца;
  - `tools/gen_discovery_approval_fixture.py` — данные фикстуры.
- [ ] **Step 2:** FAIL на оставшихся местах → убрать литерал (комментарий/докстринг — словами
  «учётка агента/человека») либо внести в исключения с причиной.
- [ ] **Step 3:** PASS; commit `test: owner-literal guard covers human login and unquoted forms`.

### Task 4: допуск песочницы требует протокол

**Files:**
- Modify: `governance/sandbox.py` (`admission_check`, ~:360)
- Test: `tests/test_governance_sandbox.py`

- [ ] **Step 1: тесты.**
  - `test_admission_requires_evidence_file`: запись с `evidence`, файла нет → отказ
    «не проверен под песочницей» с названным путём.
  - `test_admission_evidence_failed_protocol_refused`: протокол, сгенерированный
    `render_admit_protocol` с красной пробой (`Итог: НЕ ПРОШЛО`), содержит ПРАВИЛЬНЫЕ оба
    хеша — отказ (ревью codex 6a9778e: генератор пишет хеши и при провале).
  - `test_admission_evidence_record_mismatch_refused`: протокол «ПРОШЛО», но его блок
    `[[admitted]]` отличается от записи одним полем (`realpath` / `version` / `sha256` /
    `profile_sha256`, параметризовать) → отказ.
  - `test_admission_evidence_outside_docs_evidence_refused`: `evidence = "../x.md"` или
    абсолютный путь → отказ.
  - текущая запись 2.1.319 и её протокол из репо — проходят.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** в `admission_check` после совпадения записи: `evidence` — относительный путь
  под `docs/evidence/`, обычный файл (не симлинк); его текст содержит строку
  `**Итог: ПРОШЛО — запись допуска предложена.**`, не содержит раздела `## Отказы` и содержит
  `admission_record(entry)` дословно (все поля записи). Константы строк — общие с
  `render_admit_protocol`, не копии. Чтение — от корня devtools, без сети.
- [ ] **Step 4:** PASS; `tests/test_author_sandbox_invariant*.py`.
- [ ] **Step 5:** commit `feat(sandbox): admission requires its evidence protocol`.

## PR

Один PR `feat/df-config-2`, мержит владелец (authority-root: `author_launch.py`, `sandbox.py`,
`policy_rule.py`, `human-merge.sh`, `merge-pr.sh`). Флаг `--authority-override` не нужен.
