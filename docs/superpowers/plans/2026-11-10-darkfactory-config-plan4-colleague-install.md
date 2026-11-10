# DarkFactory для коллег — план 4: установка, `dfl`, подготовка репо, README

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** коллега клонирует devtools, запускает `install.sh`, в своём репо делает `dfl init-repo` (мержит его PR), затем `dfl run --subject '…'` → интервью → мерж брифа → `dfl run` → конвейер сам доходит до проверенного PR с кодом.

**Architecture:** новый пакет `lite/` (вне `governance/`, вне authority-root и харнесса): `lite/install.py` (комплектация, конфиг, обёртка `dfl`), `lite/dfl.py` (`run`/`status`), `lite/init_repo.py` (PR подготовки репо). `dfl` вызывает `governance.spec_loop.main` / `governance.brief_tools.main` в процессе, без `make`. В `governance/` — только именованный пропуск «проверок нет (test)» в ожидании проверок.

**Tech Stack:** Python 3.12, uv, git, gh, POSIX sh (`install.sh`), pytest (без сети: фейковый gh, локальные bare-репо).

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` (§5.3 п.3/7, §5.5–5.7, §6, §7 этапы 4–5).

## Global Constraints

- `strict` не меняется: всё новое поведение `governance/` включается только при `state.mode == "test"` и `rs.mode_refusal(state) is None`; `dfl` отказывает (код 2), если режим конфига не `test`.
- Не трогать authority-root (`contracts/authority-root/v1/paths.env`) и харнесс (`contracts/harness-paths/v1/paths.env`) пути. Если задача упирается в такой путь — стоп и запись в ledger, не правка.
- Каждый `subprocess.run` в `governance/` — с литеральной первой программой из `git, gh, tmux, spec-runner, security` (инвариант `tests/test_author_sandbox_invariant.py`). `uv` вызывается только из `lite/` и `install.sh`.
- Сообщения пользователю — по-русски; отказ всегда называет причину и следующий шаг.
- Тесты офлайн: никаких обращений к GitHub; gh — инъецируемый раннер/фейк, «удалённые» репо — локальные bare-репо.
- Ruff (88), pyrefly без ошибок, type hints, докстринги публичных функций.
- Полный набор: `PYTHONPATH=<…>/steward/src uv run pytest -q -p no:cache` (~60 мин); в worktree известны падения `test_runner_z14_*`.
- Имя строки пропуска — ровно `проверок нет (test)` (решение владельца 10.11).

## Решения владельца и исполнителя (для этого плана)

- **Владелец 10.11:** репо без CI в test не встаёт — именованный пропуск «проверок нет (test)».
- **Владелец 10.11:** порядок — план 4, затем 3c-H.
- **Исполнитель:** удаление флага `--authority-override` (D9) — последний пункт плана 3c-H, не этого плана: `merge-pr.sh` — харнесс (мержит владелец, D10), а 3c-H идёт после плана 4, так что это остаётся «последним PR работы». Этот план флаг не использует.
- **Исполнитель:** `acceptance_provenance` не меняется. `criteria-close` — отдельная ручная команда после мержа кода, путь test (D3) до неё не доходит; README говорит, что `criteria-close` в test не поддерживается. Цена ошибки: коллега, вызвавший `criteria-close`, получит красную находку политики, а не закрытие.
- **Исполнитель:** комплектация — `discovery` и `spec-runner` (единственный `workspace_dir("…")` в `governance/` — `discovery`; `spec-runner` — бинарь на PATH). `disputatio` нужен только песочнице `strict`; `steward` приходит uv-зависимостью devtools, кит ревью уже вендорен в `devtools/scripts/review/` (PIN steward e0ab116). Сам devtools — клон коллеги, его установщик не двигает. Тест сторожит покрытие.
- **Исполнитель:** `workspace.root` = родитель каталога devtools (README: клонировать в `~/darkfactory/devtools`); флага `--root` нет.
- **Исполнитель:** пропуск проверок — только когда rollup пуст `_NO_CHECKS_GRACE_POLLS = 4` опроса подряд (2 мин) И у головы PR нет `.github/workflows/*.yml|*.yaml`; непрочитанный каталог workflows — не «нет проверок» (обычное ожидание).

## Review Focus

1. Сторонний чек (GitHub App) появляется в rollup во время окна ожидания → обычный путь, без пропуска (тест `test_late_check_within_grace_not_skipped`).
2. Повторный `install.sh` посреди прогона → отказ до любого git-эффекта (тест `test_active_run_refuses_before_any_clone`).
3. `dfl run` в каталоге без GitHub-origin или не git → названный отказ, код 2 (тест `test_run_non_github_origin_refused`).
4. `dfl init-repo` на репо, где правила ветки требуют approving review (ruleset или защита), либо правила не читаются (403) → отказ без изменений (тесты Task 4).
5. Падение между `brief-approve` и стартом engineer-прогона → повтор `dfl run` не одобряет второй раз, только стартует engineer (тест `test_brief_approved_engineer_missing_starts_engineer_only`).

---

### Task 1: именованный пропуск «проверок нет (test)»

**Files:**
- Modify: `governance/pr_checks.py`, `governance/ops.py` (Protocol `Ops` + `RealOps`), `governance/approve_node.py:2979` (`_await_required_checks`), `governance/runner.py:1256` (`_auto_merge_candidate`), `governance/auto_tasks.py:72` (`_checks_refusal`) и его вызов в `_merge_spec_pr`, `governance/accept_pr.py` (цикл ожидания ~`:313`, `_finish_no_merge`)
- Test: `tests/test_governance_pr_checks.py` (или существующий тест pr_checks), `tests/test_governance_approve_node.py`, `tests/test_governance_runner.py`, `tests/test_governance_auto_tasks.py`, `tests/test_governance_accept_pr.py`, `tests/test_governance_ops.py`

**Interfaces:**
- Produces:
  - `pr_checks.NO_CHECKS_TEST = "проверок нет (test)"`
  - `pr_checks.no_checks(pr_facts: dict, workflows: list[str] | None) -> bool` — `True` ⇔ rollup пуст и `workflows == []`.
  - `Ops.workflow_files(self, repo_slug: str, ref: str) -> list[str] | None` — имена `*.yml`/`*.yaml` в `.github/workflows` на `ref`; HTTP 404 → `[]`; любая другая ошибка или не-список → `None`. RealOps: `gh api repos/{slug}/contents/.github/workflows?ref={ref}` по образцу соседних gh-вызовов `RealOps` (тот же профиль gh).
  - `approve_node._NO_CHECKS_GRACE_POLLS = 4`
  - `approve_node._await_required_checks(state, ops, pr, head, *, kind="finalize-PR", on_no_checks: Callable[[str], None] | None = None) -> MergeRefusal | None` — пропуск возможен только при `on_no_checks is not None`, `state.mode == "test"`, `rs.mode_refusal(state) is None`; после `_NO_CHECKS_GRACE_POLLS` опросов подряд с пустым rollup читает `ops.workflow_files(state.repo_slug, head)` один раз; `[]` → печать и `on_no_checks(reason)`, возврат `None`, где `reason = f"{NO_CHECKS_TEST}: у {kind} #{pr} нет проверок и нет .github/workflows на {head[:8]}"`. Проверка уехавшей головы — по-прежнему первой на каждом опросе. Strict-вызовы (без `on_no_checks`) не меняются.
- Consumes: `NoMergeAccept` (accept_pr), `auto_tasks._merge_spec_pr(state, ops, key, pr, kind)`.

- [ ] **Step 1: тесты предиката и Ops**

```python
def test_no_checks_predicate():
    assert pr_checks.no_checks({}, []) is True
    assert pr_checks.no_checks({"statusCheckRollup": []}, []) is True
    assert pr_checks.no_checks({}, None) is False
    assert pr_checks.no_checks({}, ["ci.yml"]) is False
    assert pr_checks.no_checks({"statusCheckRollup": [{"name": "x", "status": "QUEUED"}]}, []) is False
```
`test_workflow_files_404_is_empty`, `test_workflow_files_lists_yaml_only` (`["ci.yml", "a.yaml"]` из ответа с `README.md` и каталогом), `test_workflow_files_other_error_is_none` (403) — с подменой `subprocess.run` как в соседних тестах `tests/test_governance_ops.py`.

- [ ] **Step 2: тесты ожидания (approve_node)** — фейк с последовательностью фактов, `_SLEEP` подменён:
  - `test_no_checks_skip_in_test_after_grace`: test-прогон, rollup всегда пуст, `workflow_files → []` → `None`; колбэк вызван ровно раз, текст содержит `проверок нет (test)` и `#<pr>`; фактов прочитано `_NO_CHECKS_GRACE_POLLS`.
  - `test_no_checks_not_skipped_with_workflows` (`["ci.yml"]`) → `MergeRefusal` с `retry=True` после потолка.
  - `test_no_checks_unreadable_workflows_fail_closed` (`None`) → `MergeRefusal(retry=True)`.
  - `test_no_checks_strict_never_skips`: strict-прогон с колбэком → `MergeRefusal(retry=True)`, `workflow_files` не вызывался.
  - `test_no_checks_without_callback_never_skips`: test-прогон без колбэка → `MergeRefusal(retry=True)`.
  - `test_late_check_within_grace_not_skipped`: пусто, пусто, `PENDING`, `SUCCESS` → `None`, колбэк не вызван.
  - `test_head_moved_still_refused_before_skip`: голова сменилась на втором опросе → отказ «уехала».

- [ ] **Step 3: тесты вызывающих**
  - runner: `test_candidate_no_checks_skip_recorded` — `state.ops[request]["checks_skip"] == NO_CHECKS_TEST`, `human_merge` вызван.
  - auto_tasks: `test_tasks_merge_no_checks_skip_recorded` — `state.ops["tasks-merge"]["checks_skip"] == NO_CHECKS_TEST` и сохраняется после завершения (Ruling 1 3c-3: `_complete` переносит поля).
  - accept_pr: `test_no_merge_accept_no_checks_records_skip` — итог `record_result` содержит `"checks": NO_CHECKS_TEST`, строка вывода называет пропуск; `test_strict_accept_empty_rollup_still_stops` — без `test` пустой rollup и `workflow_files → []` дают стоп по потолку, `workflow_files` не вызывался.
  - Фейки `FakeOps`/`_ChainOps`/фейк accept_pr получают `workflow_files`, по умолчанию `["ci.yml"]` — прежние тесты не меняют поведения.

- [ ] **Step 4: прогнать — FAIL** (`uv run pytest -q tests/test_governance_approve_node.py tests/test_governance_runner.py tests/test_governance_auto_tasks.py tests/test_governance_accept_pr.py tests/test_governance_ops.py -k "no_checks or workflow_files or late_check or head_moved_still"`)

- [ ] **Step 5: реализовать** по Interfaces. Колбэки: runner — `state.ops[request]["checks_skip"] = NO_CHECKS_TEST; save(state)`; auto_tasks — `_checks_refusal(state, ops, pr, head, kind, key)` пишет то же в `state.ops[key]`. accept_pr (только при `test is not None`): в цикле ожидания считать подряд опросы с пустым rollup; после `_NO_CHECKS_GRACE_POLLS` (импорт из approve_node) один раз `ops.workflow_files(repo_slug, head0)`; `[]` → проверки считаются пройденными (mergeability ждётся как прежде), `_finish_no_merge` пишет `"checks": NO_CHECKS_TEST` и печатает его вместо «проверки зелёные».

- [ ] **Step 6: прогнать затронутые файлы целиком + `tests/test_author_sandbox_invariant.py` — PASS**; ruff, pyrefly.

- [ ] **Step 7: коммит** `feat(governance): named skip «проверок нет (test)» for repos without CI`

---

### Task 2: комплектация и `install.sh`

**Files:**
- Create: `lite/__init__.py`, `lite/components.toml`, `lite/install.py`, `install.sh`, `templates/darkfactory/config.test.example.toml`
- Test: `tests/test_lite_install.py`

**Interfaces:**
- `lite/components.toml`:
  ```toml
  schema = "darkfactory-components/v1"
  [repos.discovery]
  url = "https://github.com/andrei-shtanakov/discovery.git"
  sha = "c7961b9e13c290d30cadd2ceea946768a041de90"
  [repos.spec-runner]
  url = "https://github.com/andrei-shtanakov/spec-runner.git"
  sha = "d586fbb683158922bfd20091b6d4ed58f4583bfa"
  tool = true
  ```
- Produces (`lite/install.py`):
  - `class InstallError(Exception)`; `@dataclass(frozen=True) class Component: name: str; url: str; sha: str; tool: bool`
  - `COMPONENTS: Path` (= `lite/components.toml`); `load_components(path: Path = COMPONENTS) -> list[Component]` — схема, 40-hex `sha`, иначе `InstallError`.
  - `TERMINAL = ("completed", "merged_unverified", "brief_ready")`; `active_runs(runs_root: Path) -> list[str]` — run_id, у которых `run.json` не читается или `status ∉ TERMINAL`.
  - `Runner = Callable[..., subprocess.CompletedProcess]`; `sync_component(root: Path, comp: Component, run: Runner = subprocess.run) -> None` — каталога нет → `git clone <url> <dir>` и `git -C <dir> checkout --detach <sha>`; есть → отказ (`InstallError`), если не git-репо, `origin` ≠ `url`, `git status --porcelain` не пуст («локальные изменения»), HEAD не detached («нештатная ветка»); иначе `git fetch origin` и `checkout --detach <sha>`. Затем `tool` → `uv tool install --force <dir>`, иначе `uv sync` (`--frozen`, если есть `uv.lock`).
  - `write_config(login: str, root: Path, path: Path) -> bool` — из шаблона, если файла нет (`True`); существующий не трогается (`False`).
  - `write_launcher(bin_dir: Path, devtools: Path) -> Path` — файл `dfl`, режим 0o755.
  - `main(argv: list[str] | None = None) -> int` — флаги `--login`, `--yes`; порядок: `active_runs(rs.RUNS_ROOT)` (непусто → код 2, перечень, ни одного git-вызова) → компоненты → конфиг в `df_config.canonical_config_path()` (существующий не `test` → предупреждение «dfl работает только в режиме test») → обёртка в `~/.local/bin` (+ подсказка, если его нет в `PATH`). Логин без `--login`: подсказка из `gh api user -q .login`, при `--yes` берётся она.
- `templates/darkfactory/config.test.example.toml`: `schema = "darkfactory-config/v1"`, `mode = "test"`, `[accounts]` `human = agent = "{login}"`, `human_gh_config_dir = agent_gh_config_dir = "~/.config/gh"`, `[workspace] root = "{root}"`, `[harness] author = "claude"`, `model = "claude-opus-5"`; без `[policy]`; комментарий-шапка: что такое test и что он выключает.
- Обёртка `dfl` (точный текст):
  ```sh
  #!/bin/sh
  # DarkFactory (режим test) — создано install.sh; повторный install.sh перепишет.
  here=$PWD
  cd "<devtools>" || exit 2
  exec uv run --frozen --group governance python -m lite.dfl --cwd "$here" "$@"
  ```
- `install.sh` (POSIX sh, `set -eu`): `uname -s` = `Darwin`, иначе отказ; `command -v git gh uv claude` — каждый недостающий назван; `gh auth status` молча; `cd "$(dirname "$0")"`; `uv sync --frozen --group governance`; `exec uv run --frozen --group governance python -m lite.install "$@"`.

- [ ] **Step 1: тесты** (`tests/test_lite_install.py`; «удалённые» репо — `git init --bare` в `tmp_path`, url `file://…`; `uv` подменён рекордером в `run`):
  - `test_components_file_pins_full_shas` — реальный файл: имена `{"discovery", "spec-runner"}`, все `sha` 40-hex, `spec-runner.tool is True`.
  - `test_components_cover_workspace_dirs` — каждое литеральное `workspace_dir("<x>")` в `governance/*.py` ∈ именам комплектации.
  - `test_fresh_clone_checks_out_pin`, `test_rerun_moves_to_new_pin` (HEAD == новый sha, detached).
  - `test_dirty_clone_refused` («локальные изменения», checkout не вызван), `test_branch_clone_refused` («нештатная ветка»), `test_origin_mismatch_refused`.
  - `test_tool_component_installed_with_uv_tool`, `test_plain_component_uv_sync`.
  - `test_active_run_refuses_before_any_clone` — run.json со `status = "waiting_human_merge"` → код 2, рекордер пуст; `test_terminal_runs_not_active` (`completed`, `brief_ready`); `test_unreadable_run_counts_active`.
  - `test_existing_config_untouched` (байты те же, `False`); `test_written_config_loads_as_test` — через `use_test_real_home` из `tests/df_config_fixture.py`: `df_config.load()` → `mode == "test"`, `human == agent == login`, `workspace_root == root`.
  - `test_launcher_content_and_mode` — режим 0o755, строки `cd "<devtools>"` и `python -m lite.dfl --cwd "$here"`.
  - `test_install_sh_syntax` — `sh -n install.sh` код 0.
- [ ] **Step 2: FAIL** → **Step 3: реализовать** → **Step 4: PASS** (`uv run pytest -q tests/test_lite_install.py`), ruff, pyrefly.
- [ ] **Step 5: коммит** `feat(lite): install.sh — pinned components, test config, dfl launcher`

---

### Task 3: `dfl run` и `dfl status`

**Files:**
- Create: `lite/dfl.py`
- Modify: `governance/brief_tools.py` — вынести argv engineer-команды
- Test: `tests/test_lite_dfl.py`, `tests/test_governance_brief_tools.py` (существующий тест печатаемой команды)

**Interfaces:**
- Consumes: `user_targets.register(target_dir: Path) -> UserTarget`, `run_state` (`RUNS_ROOT`, `all_run_ids`, `load`, `run_dir`), `spec_loop.main(argv) -> int`, `brief_tools.main(argv) -> int`, `RealOps().find_brief_pr_fact(slug, head_ref)` / `brief_pr_fact(slug, pr)`, `df_config.load()`, `df_config.test_banner(cfg)`, `auto_tasks`-запись `code-accept` (`state.ops["code-accept"]["result"]["url"]`).
- Produces:
  - `brief_tools.engineer_args(state: RunState, pr: int, stakeholder: str) -> list[str]` — argv для `spec_loop.main` (`--subject … --repo … --need --frame engineer --new-run --ws-id <ws>-eng --stakeholder … --traces-to <run_dir>/brief-approval/customer-brief.md --approval-pr <pr>`); `_engineer_command` строится из него (печать не меняется).
  - `lite.dfl.main(argv: list[str] | None = None) -> int` — `--cwd <dir>` (обязателен), подкоманды `run [--subject S] [--stakeholder R]` (умолчание `--stakeholder customer`), `status`; `init-repo` добавит Task 4.
  - `lite.dfl.ENGINEER_SUFFIX = "-eng"`.
- Поведение `run` (каждый вызов идемпотентен, один шаг вперёд по журналу):
  1. `df_config.load()`; не `test` → «dfl работает только в режиме test (config.toml: mode = …)», код 2. Печать баннера.
  2. `user_targets.register(Path(cwd))`; `TargetsError` → текст ошибки, код 2.
  3. Customer-прогоны репо (`interview.frame == "customer"`): больше одного не в `completed` → отказ с перечнем run_id, код 2.
  4. Нет customer-прогона: без `--subject` → «первый запуск: dfl run --subject '<тема>'», код 2; иначе `spec_loop.main(["--subject", s, "--repo", repo, "--need", "--frame", "customer", "--stakeholder", role, "--brief-only"])`.
  5. Customer не `brief_ready` → `spec_loop.main(["--subject", subject, "--repo", repo, "--run-id", run_id])`.
  6. Customer `brief_ready`, engineer-прогона (`ws_id == cust.ws_id + ENGINEER_SUFFIX`) нет: файл `<run_dir>/brief-approval/customer-brief.md` есть → сразу шаг engineer; иначе `brief_tools.main(["propose", "--run", run_id])`, затем факты brief-PR (`brief/<ws_id>`): не вмержен → «бриф ждёт вашего мержа: <url> — после мержа снова dfl run», код 0; вмержен → `brief_tools.main(["approve", "--run", run_id, "--pr", str(n)])`, затем `spec_loop.main(brief_tools.engineer_args(cust, n, role))`.
  7. Engineer-прогон есть: `code-accept` завершён → «PR с кодом проверен: <url>; мерж — за вами», код 0, без эффектов; иначе `spec_loop.main(["--subject", eng.subject, "--repo", repo, "--run-id", eng.run_id])`.
- `status`: только журнал, без форджи и эффектов — по каждому прогону репо `run_id`, frame, `status`, и строка «дальше»: `brief_ready` → «мерж брифа, затем dfl run»; есть `code-accept` → URL; `stopped_*` → «разберите причину выше и dfl run».
- Проверено по коду (spec §5.7): engineer-маршрут не останавливается на брифе — `brief_only` допустим только для customer (`spec_loop.py:451`, `runner.py:2250`).

- [ ] **Step 1: тесты** (`spec_loop.main`, `brief_tools.main`, факты форджи, `register` — подменены рекордерами; прогоны — `rs.new_run`/`save` в `tmp_path`, `RUNS_ROOT` подменён; конфиг test — фикстура):
  - `test_refuses_strict_config`, `test_run_non_github_origin_refused`, `test_first_run_requires_subject`.
  - `test_first_run_starts_customer_brief_only` — argv ровно как в п. 4.
  - `test_customer_in_progress_resumes_by_run_id`.
  - `test_brief_ready_unmerged_proposes_and_waits` — propose вызван, approve нет, в выводе URL, код 0.
  - `test_brief_merged_approves_then_starts_engineer` — argv engineer == `brief_tools.engineer_args(...)`, в нём нет `--brief-only`.
  - `test_brief_approved_engineer_missing_starts_engineer_only` — файл одобрения есть: ни propose, ни approve.
  - `test_engineer_run_resumes`, `test_code_accepted_prints_url_no_effects`.
  - `test_two_active_customer_runs_refused`.
  - `test_status_lists_runs_no_effects`.
  - brief_tools: `test_engineer_command_built_from_args` — печать прежняя.
- [ ] **Step 2: FAIL** → **Step 3: реализовать** → **Step 4: PASS** (`tests/test_lite_dfl.py`, `tests/test_governance_brief_tools.py`), ruff, pyrefly.
- [ ] **Step 5: коммит** `feat(lite): dfl run/status — one command drives customer → brief → engineer`

---

### Task 4: `dfl init-repo`

**Files:**
- Create: `lite/init_repo.py`, `templates/darkfactory/target/spec-runner.config.yaml`
- Modify: `lite/dfl.py` (подкоманда `init-repo`)
- Test: `tests/test_lite_init_repo.py`

**Interfaces:**
- Produces:
  - `TEMPLATE_FILES: tuple[tuple[str, str], ...]` — (путь в devtools, путь в репо): `profiles/team-exp.yaml`, `profiles/roles.yaml`, `profiles/gate-catalog.yaml`, `profiles/risk-model.yaml`, все файлы `scripts/review/` (8, вкл. `PIN`, `prose-paths.env`), `.github/codex/review-schema.json`, `.github/codex/review-prompt.md` — в те же пути; `templates/darkfactory/target/spec-runner.config.yaml` → `spec-runner.config.yaml`.
  - `INIT_BRANCH = "darkfactory/init-repo"`, `LABEL = "human-merge-required"` (значение `brief_tools.LABEL` — импортировать оттуда).
  - `Gh = Callable[[list[str]], subprocess.CompletedProcess]` (argv без `gh`).
  - `review_rules_refusal(slug: str, branch: str, gh: Gh) -> str | None`: `api --paginate repos/{slug}/rules/branches/{branch}` — правило `type == "pull_request"` с `required_approving_review_count > 0` → отказ; `api repos/{slug}/branches/{branch}/protection` — 404 с «Branch not protected» → защиты нет; 200 с `required_pull_request_reviews.required_approving_review_count > 0` → отказ; любая другая ошибка любого из двух → отказ «правила ветки не прочитаны (…) — это не «правил нет»». Отказ называет правило и говорит, что `dfl` чужую защиту не меняет.
  - `conflicts(repo: Path) -> list[str]` — пути назначения, уже существующие в рабочем дереве.
  - `init_repo(repo: Path, devtools: Path, gh: Gh, run: Runner = subprocess.run) -> int`.
- Поведение: рабочее дерево чистое и на ветке по умолчанию (иначе отказ) → slug из `origin` (`user_targets.slug_from_url`) → открытый PR из `INIT_BRANCH` уже есть → печать URL, код 0 → правила ветки → конфликты (все перечислены, код 2, ничего не создано) → ветка, копирование (байты как в devtools, `harness-claude` 0o755), коммит «DarkFactory: подготовка репо (dfl init-repo)», `git push -u origin`, `gh pr create --base <default> --head INIT_BRANCH` с телом: перечень файлов, «этот PR мержите вы», ссылка на README коллег → метка `LABEL`: если её нет в `gh label list` — `gh label create` (существующая не трогается) → возврат на ветку по умолчанию.
- `spec-runner.config.yaml` шаблона: `claude_command: "claude"`, `claude_model: "sonnet"`, `skip_permissions: true`, `review_command: "claude"`, `review_model: "sonnet"`, `integration_pr: true`, `spec_governance: strict`, `execution_mode: standard`, `review_policy: required`; блок `commands:` закомментирован с пояснением «впишите команды тестов/линта вашего проекта».

- [ ] **Step 1: тесты** (рабочий репо + bare-remote в `tmp_path`; `gh` — фейк со сценарием ответов):
  - `test_rules_ruleset_requires_review_refused`, `test_protection_requires_review_refused`, `test_protection_403_refused_not_none` (текст содержит «не «правил нет»»), `test_rules_unpaginated_error_refused`, `test_no_rules_no_protection_ok`, `test_protection_without_reviews_ok`.
  - `test_conflicts_listed_nothing_created` — существуют `profiles/team-exp.yaml` и `spec-runner.config.yaml`: оба названы, ветки нет, коммитов нет.
  - `test_happy_path_pushes_branch_and_opens_pr` — в bare-remote ветка `INIT_BRANCH` с файлами, байты == источникам devtools, `harness-claude` исполняемый; `pr create` вызван с `--base`; после — текущая ветка по умолчанию.
  - `test_label_created_when_absent` / `test_label_kept_when_present`.
  - `test_existing_init_pr_is_reported` — ни копирования, ни push.
  - `test_dirty_tree_refused`.
  - `test_copied_kit_matches_pin` — sha256 скопированных файлов совпадают со строками `scripts/review/PIN`.
  - `test_spec_runner_template_selects_claude`.
  - `test_dfl_init_repo_subcommand` — `lite.dfl.main(["--cwd", d, "init-repo"])` вызывает `init_repo` с `Path(d)`.
- [ ] **Step 2: FAIL** → **Step 3: реализовать** → **Step 4: PASS**, ruff, pyrefly.
- [ ] **Step 5: коммит** `feat(lite): dfl init-repo — template PR, branch-rules check, label`

---

### Task 5: README «DarkFactory для коллег»

**Files:**
- Create: `docs/darkfactory-for-colleagues.md`
- Modify: `README.md` — раздел «DarkFactory для коллег» (2–3 строки + ссылка) перед «Конфиг DarkFactory»
- Test: `tests/test_lite_docs.py`

Содержание документа (по-русски, для человека, не видевшего экосистему):
1. Что получится: одна команда → интервью → мерж брифа → PR с кодом; мерж кода — ваш.
2. Нужно: macOS, `git`, `gh` (залогинен), `uv`, `claude` (залогинен), одна учётка GitHub; без sudo.
3. Установка: `git clone https://github.com/andrei-shtanakov/devtools ~/darkfactory/devtools && sh ~/darkfactory/devtools/install.sh`; что ставится (перечень из `lite/components.toml`), где конфиг, где `dfl`.
4. Подготовка репо: `dfl init-repo` в своём репо → PR → смержить самому; требование к правилам ветки (не требовать approving review) и почему.
5. Запуск: `dfl run --subject '…'` → интервью → `dfl run` (открывает PR брифа) → смержить бриф → `dfl run` → дальше сам; `dfl status`; повтор после перерыва — `dfl run`.
6. Что выключено в режиме test — честно (D6): перечень из `df_config.TEST_RELAXATIONS` дословно, плюс «нет песочницы авторинга (есть только в strict)», «утверждение задач и candidate-PR — автоматически», «ревью — комментариями», «репо без CI: пропуск «проверок нет (test)»».
7. Не поддерживается в test: `criteria-close`.
8. Обновление: повторный `install.sh` (откажет посреди прогона); остановки — строка с причиной, после исправления `dfl run`.

- [ ] **Step 1: тест** — документ содержит каждую строку `df_config.TEST_RELAXATIONS`, `проверок нет (test)`, `песочниц`, `criteria-close`, каждое имя из `lite/components.toml`, каждую подкоманду `dfl` (`run`, `status`, `init-repo`), `install.sh`; `README.md` ссылается на `docs/darkfactory-for-colleagues.md`.
- [ ] **Step 2: FAIL** → **Step 3: написать** → **Step 4: PASS**
- [ ] **Step 5: коммит** `docs: DarkFactory for colleagues — install, init-repo, run, what test mode switches off`

---

## После задач

- Полный набор (со steward), разбор падений.
- Сверка: `git diff --name-only master...HEAD` не пересекается с authority-root и харнесс-префиксами → мерж агентом без флага.
- Пробный прогон на чистом пользователе macOS (spec §7 этап 5) — шаг владельца после мержа, по желанию; этот план его не требует.
