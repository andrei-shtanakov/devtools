# dfl run: продолжение прогона и сиротские тест-файлы — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** сиротский файл в `verifies` останавливает гейт; повтор `dfl run` при открытом частичном integration-PR показывает плашку проекта и останавливается, а не создаёт новую ветку.

**Architecture:** дефект 2 — перенос `_orphan_verifies_findings` из non-fatal в `graph_findings` (оба пути и `task_bridge.deliver` получают его автоматически). Дефект 1 — новый модуль `governance/run_resume.py` (чистые функции: кандидаты, счётчик задач, тексты), который зовут `governance/auto_tasks._spec_run` (enterprise) и `lite/personal/flow._step_code_run` (личный путь).

**Tech Stack:** Python 3.12, stdlib, pytest; `uv run pytest`, `uv run ruff`, `uv run pyrefly check`.

**Spec:** `docs/superpowers/specs/2026-11-16-dfl-run-resume-design.md`

## Global Constraints

- Два PR в devtools: PR-A = Task 1 (ветка `fix/orphan-verifies-fatal`), PR-B = Tasks 2–4 (ветка `feat/dfl-run-resume`). Authority-root файлов в плане нет; если понадобится — стоп и отдельный PR человеку.
- spec-runner не править (соседний репо); его режим продолжения — spec-runner#678, вне плана. Сейчас реализуется только ветка «spec-runner не умеет» (spec §3.4).
- Тексты для человека — по-русски, как в соседнем коде; строки ≤ 88 символов.
- Остановка — существующими механизмами: `ctx.stop(...)` на личном пути, `_stop(...)` на enterprise. Новых кодов выхода нет.
- Остановка при открытом частичном PR — **всегда**, без порога (решение владельца §7). Закрытый PR = начать заново (обычный запуск).
- На `failed_prs` для выбора кандидата не опираться (spec §3.3).

## Review Focus

1. Записанный ненулевой код + частичный PR, который человек **закрыл** → обычный перезапуск раннера, без плашки (Task 3/4 тест `..._closed_partial_pr_reruns`).
2. Исход прошлой попытки не наблюдён (`exit`/`run_exit` не записан) — прежний стоп «может быть частичным», без плашки (существующие тесты должны остаться зелёными).
3. head PR не начинается с `spec-runner/run-` — не кандидат (Task 2 `test_candidates_ignore_foreign_head`).
4. tasks.md на ветке не читается (fetch=False или show_file=None) — плашка без счётчика, остановка всё равно (Task 2/3 тесты).
5. Сирота с `::`-селектором (`tests/x.py::test_a`), чей файл принадлежит задаче — не находка (Task 1 `test_orphan_selector_of_owned_file_is_not_finding`).

---

### Task 1: сирота в `verifies` — ошибка `graph_findings` (PR-A)

**Files:**
- Modify: `governance/decomposition_guard.py` (`_ORPHAN_VERIFIES_TARGET_MARKER` ~32, `_orphan_verifies_findings` ~614, `non_fatal_findings` ~647, `graph_findings` ~663 + docstring)
- Modify: `governance/runner.py` ~4215–4240 (блок `warnings = [... non_fatal_findings ...]` и его комментарий)
- Modify: `governance/task_bridge.py` ~2094 (комментарий про non-fatal находку)
- Test: `tests/test_governance_decomposition_guard.py`, `tests/test_governance_runner.py`, `tests/test_lite_personal_check.py`

**Interfaces:**
- Produces: `graph_findings(...)` теперь включает строки сирот; `non_fatal_findings` удалена (единственный потребитель — runner).

- [ ] **Step 1: тесты.** В `test_governance_decomposition_guard.py`:
  - `test_orphan_verifies_is_graph_finding` — бандл, где у verify-задачи (форма DT-15 sdd-runer) в `verifies` два файла: один — цель `checked_by` задачи из её замыкания, второй (`tests/contract/test_run_state_schema.py`) — ничей. `graph_findings(beh, decomp)` содержит ровно одну строку с `"DT-15"` и `"tests/contract/test_run_state_schema.py"` и подстрокой `"checked_by"` и `"уберите"`.
  - `test_orphan_selector_of_owned_file_is_not_finding` — `verifies: tests/x.py::test_a`, `tests/x.py` — цель `checked_by` задачи в замыкании → `graph_findings == []`.
  - Существующие тесты на `non_fatal_findings` перевести на `graph_findings` (то же содержание находки) или удалить, если они проверяли именно «non-fatal».
  - В `test_lite_personal_check.py`: `test_orphan_verifies_is_decomposition_problem` — `decomposition_problems(...)` личного пути на той же форме возвращает проблему с путём сироты.
  - В `test_governance_runner.py`: тест(ы), ожидавшие `warning GC-DT-GRAPH` и прохождение гейта, — переписать: гейт стоит, в gate-findings строка сироты как error.
- [ ] **Step 2:** `uv run pytest tests/test_governance_decomposition_guard.py tests/test_lite_personal_check.py -q` → новые тесты FAIL.
- [ ] **Step 3: реализация.** В `graph_findings` добавить `findings.extend(_orphan_verifies_findings(tasks, bindings))` (переменные — те, что уже есть внутри функции; если `bindings` там под другим именем — использовать его). Текст находки:
  `f"{t.dt_id}: verifies {f}: файл не создаёт ни одна задача — дайте его задаче-реализации через checked_by её сценария или уберите из verifies"`.
  `_ORPHAN_VERIFIES_TARGET_MARKER` удалить, если больше не используется. Удалить `non_fatal_findings` и блок `warnings` в runner (вместе с местом, где `warnings` пишутся в gate-findings). Поправить докстринги/комментарии, которые называют сироту non-fatal/warning (decomposition_guard модульный комментарий ~24–32, `graph_findings` docstring, runner, task_bridge ~2094). Довод «легаси checked_by без verifies» оставить.
- [ ] **Step 4:** `uv run pytest -q -x` (весь набор) → PASS; `uv run ruff check . && uv run ruff format --check . && uv run pyrefly check` → чисто.
- [ ] **Step 5: commit** `fix(gate): сирота в verifies — ошибка гейта, а не предупреждение (#767)`.

---

### Task 2: модуль `governance/run_resume.py` (PR-B)

**Files:**
- Create: `governance/run_resume.py`
- Test: `tests/test_governance_run_resume.py`

**Interfaces:**
- Consumes: `governance.task_bridge._TASK_HEAD_RE`, `_TASK_META_RE` (формат tasks.md, запинованный по парсеру spec-runner); `RUN_BRANCH_PREFIX = "spec-runner/run-"` из `governance.auto_tasks` (если импорт даёт цикл — перенести константу в `run_resume` и реэкспортировать из `auto_tasks`).
- Produces:
  - `resume_candidates(prs: list[dict], before: list[int]) -> list[tuple[int, str]]` — `(number, headRefName)` для PR со `state == "OPEN"`, номером не из `before` и head, начинающимся с `RUN_BRANCH_PREFIX`; отсортировано по номеру.
  - `task_progress(text: str) -> tuple[int, int] | None` — `(done, total)`: total — число заголовков `### <ID>: `, done — блоков, чья ПЕРВАЯ мета-строка (`_TASK_META_RE`) имеет статус DONE (с эмодзи или без, регистр не важен); `None`, если заголовков нет.
  - `read_progress(ops, target_dir: str, branch: str, tasks_rel: str) -> tuple[int, int] | None` — `ops.fetch_branch(target_dir, branch)`; False → None; затем `ops.show_file(target_dir, f"origin/{branch}", tasks_rel)`; None → None; иначе `task_progress`. Любое исключение ops → None.
  - `banner(subject: str, repo_slug: str, branch: str, progress: tuple[int, int] | None) -> str` — ровно:
    ```
    ▶ продолжаю проект «{subject}» · {repo_slug}
      ветка {branch} · сделано {done} из {total} задач
    ```
    при `progress is None` вторая строка: `  ветка {branch} · счётчик задач не прочитан`.
  - `resume_stop(branch: str, pr: int) -> str` — ровно:
    `f"продолжить ветку {branch} пока нельзя: spec-runner не умеет продолжать начатую ветку (spec-runner#678), а новую dfl не создаёт, чтобы не потерять сделанное. Продолжить — влейте PR #{pr} в main и повторите dfl run; начать заново — закройте PR #{pr} и повторите dfl run"`
  - `several_stop(prs: list[int]) -> str` — `f"открыто несколько частичных integration-PR прогона: {', '.join(f'#{n}' for n in prs)} — закройте лишние и повторите dfl run"`.

- [ ] **Step 1: тесты** (`tests/test_governance_run_resume.py`):
  - `test_candidates_open_new_run_prs` — prs `[{number:5,state:OPEN,headRefName:"spec-runner/run-1"},{number:3,state:OPEN,headRefName:"spec-runner/run-0"}]`, before `[3]` → `[(5, "spec-runner/run-1")]`.
  - `test_candidates_skip_closed` — state `CLOSED`/`MERGED` → `[]`.
  - `test_candidates_ignore_foreign_head` — head `feature/x` → `[]`.
  - `test_task_progress_counts_done` — три блока `### TASK-001: a` / `P1 | ✅ DONE`, `### TASK-002: b` / `P1 | DONE`, `### TASK-003: c` / `P2 | TODO   Est: 0.5d` → `(2, 3)`.
  - `test_task_progress_no_tasks` → `None` для `"# пусто\n"`.
  - `test_read_progress_fetch_false_is_none`, `test_read_progress_missing_file_is_none`, `test_read_progress_reads_origin_ref` (fake ops фиксирует ref `"origin/spec-runner/run-1"` и путь).
  - `test_banner_with_and_without_progress` — обе формы байт в байт, как выше (пример: `subject="аналог sprc-runner на Claude Agent SDK"`, `repo_slug="ai-prosto/ssd-runner"`, `branch="spec-runner/run-20261114-173445"`, `(14, 16)`).
  - `test_resume_stop_names_branch_pr_and_issue` — содержит ветку, `#7`, `spec-runner#678`, «закройте PR #7».
- [ ] **Step 2:** `uv run pytest tests/test_governance_run_resume.py -q` → FAIL (модуля нет).
- [ ] **Step 3:** реализовать модуль (stdlib, type hints, docstrings на публичных функциях).
- [ ] **Step 4:** тот же прогон → PASS; ruff + pyrefly чисто.
- [ ] **Step 5: commit** `feat(dfl): run_resume — кандидат продолжения, счётчик задач, плашка (#767)`.

---

### Task 3: enterprise — `auto_tasks._spec_run` (PR-B)

**Files:**
- Modify: `governance/auto_tasks.py` (`_open_run_prs` ~171, `_spec_run` ~213)
- Test: `tests/test_governance_auto_tasks.py`

**Interfaces:**
- Consumes: всё из Task 2. `state.subject`, `state.repo_slug`, `state.target_dir`, `state.ws_id`; путь tasks.md — `f"spec/{state.ws_id}-tasks.md"`.

- [ ] **Step 1: тесты** (fake `_ChainOps` расширить `fetch_branch`/`show_file`, `prs_by_head_prefix` отдаёт `headRefName`):
  - `test_spec_run_failed_attempt_with_open_partial_pr_stops_with_banner` — `state.ops["spec-run"] = {"before": [], "exit": 1}`, открыт PR 9 `spec-runner/run-1`, tasks.md с 14/16 done → возвращает `None`; раннер **не** вызван; stdout содержит плашку (`сделано 14 из 16 задач`); причина стопа = `resume_stop("spec-runner/run-1", 9)`; запись `ops["spec-run"]` не перетёрта (`before`/`exit` прежние).
  - `test_spec_run_failed_attempt_closed_partial_pr_reruns` — тот же state, PR 9 `CLOSED` → раннер вызван (прежнее поведение).
  - `test_spec_run_two_partial_prs_stop` — два кандидата → стоп `several_stop([9, 10])`, раннер не вызван, плашки нет.
  - `test_spec_run_banner_without_progress` — `show_file` → None: плашка со «счётчик задач не прочитан», стоп тот же.
  - Существующие тесты, где после ненулевого кода с открытым новым PR ожидался перезапуск раннера, — переписать на стоп (решение §7); тексты «повтор перезапустит раннер» в этом сценарии больше не утверждать.
- [ ] **Step 2:** `uv run pytest tests/test_governance_auto_tasks.py -q` → новые FAIL.
- [ ] **Step 3: реализация.** `_open_run_prs` → возвращать сырые `list[dict]` OPEN-и-нет или ввести рядом `_run_prs(state, ops) -> list[dict] | None` (одно чтение форджи на шаг); номера для прежней логики — из него. В `_spec_run` в ветке `previous is not None`, ПОСЛЕ проверок `exit == 0` и «exit не записан», если `previous.get("exit")` — ненулевое число: `cands = resume_candidates(prs, old)`; `len(cands) > 1` → `_stop(several_stop(...))`, `return None`; `len(cands) == 1` → `print(banner(...read_progress(...)))`, `_stop(resume_stop(branch, pr))`, `return None`; пусто → прежний путь (union `before`, перезапуск).
- [ ] **Step 4:** весь набор `uv run pytest -q -x` → PASS; ruff + pyrefly чисто.
- [ ] **Step 5: commit** `feat(dfl): enterprise — стоп с плашкой вместо новой ветки прогона (#767)`.

---

### Task 4: личный путь — `flow._step_code_run` (PR-B)

**Files:**
- Modify: `lite/personal/flow.py` (`_open_run_prs` ~859, `_step_code_run` ~867; тексты ~881, ~894)
- Test: `tests/test_lite_personal_flow.py`

**Interfaces:**
- Consumes: всё из Task 2. `run.subject`, `run.repo_slug`, `run.target_dir`; путь tasks.md — `TASKS_PATH.format(ws_id=run.ws_id)` (уже есть в flow ~632). Плашка — через `say(...)`.

- [ ] **Step 1: тесты** (`FakeOps` расширить так же):
  - `test_code_run_failed_attempt_with_open_partial_pr_stops_with_banner` — `run.before_prs=[]`, `run.run_exit=1`, открыт PR 9 `spec-runner/run-1` → шаг возвращает False, `spec_runner` не вызван, вывод с плашкой, `run.stop_reason == resume_stop(...)`, `run.before_prs`/`run.run_exit` не изменены.
  - `test_code_run_failed_attempt_closed_partial_pr_reruns` — PR CLOSED → раннер вызван.
  - `test_code_run_two_partial_prs_stop` — `several_stop`.
  - Текст стопа при `rc != 0` (~894) больше не обещает «повтор перезапустит раннер», если после падения открыт новый PR: заменить на «повторите dfl run — dfl покажет, что сделано» (обновить тест, проверяющий этот текст, если он есть). При `rc != 0` без нового PR прежний текст оставить.
- [ ] **Step 2:** `uv run pytest tests/test_lite_personal_flow.py -q` → новые FAIL.
- [ ] **Step 3: реализация** — зеркально Task 3: при `run.before_prs is not None` и `run.run_exit not in (None, 0)` проверить кандидатов до union/перезапуска.
- [ ] **Step 4:** весь набор `uv run pytest -q -x` → PASS; ruff + pyrefly чисто.
- [ ] **Step 5: commit** `feat(dfl): личный путь — стоп с плашкой вместо новой ветки прогона (#767)`.

---

### После задач

- README (раздел про повтор `dfl run`, если есть): одна фраза о плашке и о «закройте PR — начать заново». Входит в коммит Task 4.
- TODO.md: пункт `dfl-run-resume-pass-branch @blocked_by:todo://spec-runner/integration-pr-resume-branch` — передача ветки в spec-runner, когда выйдет #678 (spec §3.4 «умеет»).
