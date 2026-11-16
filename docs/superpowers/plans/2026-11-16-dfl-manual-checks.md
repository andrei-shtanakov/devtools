# Ручные проверки в конце прогона dfl (#771) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** перед «конвейер закончен» `dfl` спрашивает владельца по каждой проверке `kind: manual`, записывает ответы, публикует один комментарий в PR с кодом и заводит issue на «не выполнено» — в enterprise и личном путях.

**Architecture:** чистое ядро `governance/manual_checks.py` (сбор проверок из бандла, тексты, запись, публикация через `Ops`) общее для обоих путей; вопрос человеку — `lite/manual_ask.py` (governance не зависит от `lite`). Enterprise встраивается в `lite/dfl.py` (`_engineer_step`, `_finished`, `_next_hint`, `status`) вне `runlog.captured`; личный — в `lite/personal/flow._step_code_pr`. Отметка «новый прогон» ставится при создании прогона, старые прогоны шаг пропускают.

**Tech Stack:** Python 3.12, pytest, `gh` через `governance/ops.RealOps`.

**Spec:** `docs/superpowers/specs/2026-11-16-dfl-manual-checks-design.md`

## Global Constraints

- Вопрос по одной проверке, форма (§3.3): `Ручная проверка {BEH} ({TASK}): «{title}»` / `шаги: {doc}` / `1. выполнено · 2. не выполнено · 3. принять с оговоркой`.
- Ответ окончателен; запись не меняется; комментарий PR — снимок (§3.5).
- Issue — только на «не выполнено», без метки `inbox` (§5).
- Один комментарий в PR на прогон, со всеми ответами (§3.4).
- Прогон без ручных проверок — ни строки, ни вопроса (анти-цель).
- Только новые прогоны: отметка при создании (§5, решение владельца).
- Одинаково в `test` и `strict`.
- Повтор `dfl run` не дублирует ни issue, ни комментарий (анти-цель).
- `ruff` через `uv run --group selfcheck ruff …`; тесты `uv run --group governance pytest`.

## Review Focus

1. Повтор после падения между `create_issue` и сохранением записи — второго issue быть не должно (поиск по маркеру, Task 3 `test_publish_recovers_issue_by_marker`).
2. Стоп-слово посреди ответов — уже данные ответы сохранены, повтор спрашивает только остальные (Task 4 `test_settle_stop_word_keeps_given_answers`).
3. Бандл не читается (нет файла / битый) — стоп с причиной, а не «проверок нет» (Task 4 `test_settle_unreadable_bundle_stops`).
4. Старый прогон (`manual_checks=False`) с законченным `code-accept` — поведение байт в байт прежнее (Task 5 `test_old_run_finished_without_questions`, Task 6 `test_old_personal_run_merged_without_questions`).
5. Waived-DT и BEH, покрытый двумя DT, — без вопроса / один вопрос (Task 1 `test_collect_skips_waived_dt`, `test_collect_one_question_per_beh`).

---

### Task 1: Ядро — сбор проверок и тексты

**Files:**
- Create: `governance/manual_checks.py`
- Test: `tests/test_governance_manual_checks.py`

**Interfaces:**
- Consumes: `task_bridge.parse_behaviour(text) -> list[Scenario]` (`beh_id, title, checked_kind, checked_target`), `decomposition_guard.parse_dt_tasks(text) -> (list[DtTask], list[str])` (`dt_id, scenarios, waiver`); номер задачи — `TASK-{idx:03d}`, idx — порядок DT с 1 (как `render_tasks_dt`).
- Produces:
  - `@dataclass(frozen=True) ManualCheck(beh_id: str, title: str, task_id: str, steps: str | None)`
  - `DONE, NOT_DONE, CAVEAT = "done", "not_done", "caveat"`
  - `collect(behaviour_text: str, decomposition_text: str) -> list[ManualCheck]` — по DT в порядке файла, BEH из `t.scenarios` с `checked_kind == "manual"`; DT с `waiver` пропускается; BEH один раз (первая DT). Ошибка разбора — `ValueError` наружу.
  - `question(check: ManualCheck) -> str` — форма из Global Constraints; строки `шаги:` нет при `steps is None`.
  - `issue_marker(run_id, beh_id) -> str` = `<!-- dfl-manual-check run={run_id} beh={beh_id} -->`
  - `issue_title(check) -> str` = `Ручная проверка не выполнена: {beh_id} — {title}`
  - `issue_body(check, answer: dict, *, run_id: str, pr_url: str) -> str` — первая строка маркер; дальше требование, задача, шаги, комментарий владельца, PR, прогон.
  - `comment_marker(run_id) -> str` = `<!-- dfl-manual-checks run={run_id} -->`
  - `pr_comment(checks, answers: dict[str, dict], *, run_id: str) -> str` — маркер + таблица `| BEH | задача | ответ | оговорка / комментарий | issue |`, ответ словами `выполнено / не выполнено / с оговоркой`.
  - `summary(checks, answers, closed: frozenset[int] = frozenset()) -> str` — `""`, если нет «не выполнено»/«с оговоркой»; иначе `ручные проверки — не выполнено: N (BEH-39 → issue #24); с оговоркой: M` (пустые части опускаются); issue из `closed` → вместо этого часть `выполнено позже: K (BEH-39, issue #24 закрыт)`.
  - Запись (`record: dict`, одна форма в обоих путях): `{"status": "started"|"completed", "checks": [asdict…], "answers": {beh: {"verdict", "note", "at", "issue"}}, "commented": bool}`; `pending(enabled: bool, record: Mapping | None) -> bool` = `enabled and (record is None or record.get("status") != "completed")`; `unanswered(record) -> list[ManualCheck]`.

- [ ] **Step 1: Write failing tests** — в `tests/test_governance_manual_checks.py`, на маленьком бандле из строк (сценарии `#### BEH-NN: title` + `**checked_by**` с `` `kind: manual` `` `` `target: docs/x.md` ``; DT-блоки по формату `parse_dt_tasks`, см. фикстуры `tests/test_governance_task_bridge.py`):
  - `test_collect_manual_with_task_numbers` — BEH-02 manual в DT-02 → `[ManualCheck("BEH-02", "<title>", "TASK-002", "docs/x.md")]`; pytest-BEH не попадает.
  - `test_collect_skips_waived_dt`, `test_collect_one_question_per_beh`, `test_collect_none_when_no_manual` (`== []`).
  - `test_question_form` — точный текст из Global Constraints; без `steps` — две строки.
  - `test_issue_body_starts_with_marker`, `test_pr_comment_has_marker_and_rows`.
  - `test_summary_empty_when_all_done`, `test_summary_not_done_and_caveat` (`"ручные проверки — не выполнено: 1 (BEH-39 → issue #24); с оговоркой: 1"`), `test_summary_closed_issue_reads_done_later`.
  - `test_pending` — `(False, None) → False`, `(True, None) → True`, `(True, {"status": "completed"}) → False`.
- [ ] **Step 2:** `uv run --group governance pytest tests/test_governance_manual_checks.py -q` → FAIL (ImportError).
- [ ] **Step 3:** реализовать `governance/manual_checks.py` по Interfaces.
- [ ] **Step 4:** тот же запуск → PASS; `ruff check`/`format --check` чисто.
- [ ] **Step 5:** commit `feat(dfl): manual_checks — ручные проверки бандла и тексты (#771)`.

### Task 2: Ops — issue без метки, поиск по маркеру, состояние issue

**Files:**
- Modify: `governance/ops.py` (Protocol `Ops` ~L432-434, `RealOps.create_issue`/`find_issue` ~L2775-2830)
- Test: `tests/test_governance_ops.py` (через `_install_fake_run`)

**Interfaces:**
- Produces:
  - `create_issue(self, repo_slug, title, body, *, labels: tuple[str, ...] = ("inbox",)) -> int` — `--label X` на каждую; `()` — без `--label`. Прежние вызовы не меняются.
  - `find_issue(self, repo_slug, body_prefix, *, labels: tuple[str, ...] = ("inbox",), state: str = "open") -> int | None` — `--state {state}`, `--label` по `labels`; контракт ошибок прежний (`RuntimeError`).
  - `issue_state(self, repo_slug: str, number: int) -> str | None` — `gh issue view N -R slug --json state` → `"OPEN"`/`"CLOSED"`; любая ошибка — `None` (украшение статуса, не стоп).

- [ ] **Step 1: failing tests:** `test_create_issue_without_labels_has_no_label_flag` (argv без `--label`), `test_create_issue_default_keeps_inbox`, `test_find_issue_all_states_without_label` (argv содержит `--state all`, нет `--label`), `test_issue_state_reads_state`, `test_issue_state_error_is_none`.
- [ ] **Step 2:** запуск `-k "create_issue or find_issue or issue_state"` → FAIL.
- [ ] **Step 3:** реализовать; Protocol обновить теми же сигнатурами. Если `SnapshotOps`/тест классификации методов (`tests/test_act_identity_table.py`, `tests/test_act_status.py`) требует учесть новый метод — `issue_state` в список **запрещённых** снимку (чтение форджи вне снимка не нужно).
- [ ] **Step 4:** `uv run --group governance pytest tests/test_governance_ops.py tests/test_act_identity_table.py tests/test_act_status.py -q` → PASS.
- [ ] **Step 5:** commit `feat(ops): create_issue/find_issue с метками, issue_state (#771)`.

### Task 3: Публикация — issue и комментарий, идемпотентно

**Files:**
- Modify: `governance/manual_checks.py`
- Test: `tests/test_governance_manual_checks.py`

**Interfaces:**
- Consumes: Task 1 (тексты, запись), Task 2 (`create_issue(labels=())`, `find_issue(labels=(), state="all")`), `Ops.comment`, `Ops.pr_comments_fact(slug, pr) -> Fact[list[PrComment]]` (`.established`, `.value[i].body`).
- Produces: `publish(ops, record: dict, *, repo_slug: str, pr: int, pr_url: str, run_id: str, save: Callable[[], None]) -> None` — для каждого «не выполнено» без `issue`: `find_issue(marker)`, иначе `create_issue`; номер в `answers[beh]["issue"]` и **сразу** `save()`. Затем, если не `record["commented"]`: есть комментарий с `comment_marker` (`pr_comments_fact` установлен) — не постить; иначе `ops.comment(...)`; `record["commented"] = True`, `save()`. В конце `record["status"] = "completed"`, `save()`. Исключения `Ops` — наружу (вызывающий стопит).

- [ ] **Step 1: failing tests** (фейк Ops — свой маленький класс в тесте, пишет вызовы):
  - `test_publish_creates_issue_only_for_not_done` (одна `create_issue` с `labels=()`, номер в записи).
  - `test_publish_recovers_issue_by_marker` (`find_issue` вернул 24 → `create_issue` не зван, `issue == 24`).
  - `test_publish_comment_once` (второй `publish` на той же записи → `comment` не зван).
  - `test_publish_skips_comment_found_by_marker`.
  - `test_publish_saves_after_issue` (`save` вызван между `create_issue` и `comment`).
- [ ] **Step 2:** запуск → FAIL.  **Step 3:** реализовать.  **Step 4:** PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): публикация ответов на ручные проверки (#771)`.

### Task 4: Вопрос человеку и общий проход «ответить и опубликовать»

**Files:**
- Create: `lite/manual_ask.py`
- Test: `tests/test_lite_manual_ask.py`

**Interfaces:**
- Consumes: Task 1, 3; `lite.interview_chat.read_answer(read, *, quick)`, `Read`, `STOP_WORD`; `governance.auto_tasks.notice`.
- Produces:
  - `ask_one(check: ManualCheck, read: Read) -> dict | None` — печатает `question(check)` через `notice`; ответ `1`/`выполнено` → `{"verdict": DONE, "note": ""}`; `2`/`не выполнено` → затем `что видно на деле (Enter — без комментария):` → note; `3`/`с оговоркой`/`принять с оговоркой` → `текст оговорки:`, пустой — повтор запроса; непонятный ответ — повтор вопроса; `None` (стоп/EOF) — `None`. Поле `at` (ISO-дата) ставит `settle`.
  - `NO_TTY = "ждут ответа {n} ручных проверок — запустите dfl run в терминале"`, `PAUSED = "ответы на ручные проверки — потом: dfl run"`.
  - `settle(record: dict, *, load_texts: Callable[[], tuple[str, str]], tty: bool, read: Read, ops, repo_slug: str, pr: int, pr_url: str, run_id: str, save: Callable[[], None]) -> str | None` — `None` = прошло (запись `completed`); строка = причина стопа (вызывающий печатает и выходит с 0). Порядок: нет `checks` в записи → `collect(*load_texts())` (ошибка чтения/разбора → `"бандл прогона не прочитан: {exc} — повторите dfl run"`), `status="started"`, `save()`; проверок нет → `completed`, `save()`, `None` без вывода. Есть неотвеченные и не `tty` → `NO_TTY`. Иначе по одной: ответ → в `answers` с `at`, `save()`; `None` → `PAUSED`. Затем `publish(...)`; исключение `Ops` → `"публикация ответов не удалась: {exc} — повторите dfl run"`.

- [ ] **Step 1: failing tests** (`read` — итератор строк):
  - `test_ask_one_done`, `test_ask_one_not_done_with_note`, `test_ask_one_caveat_requires_text` (`["3", "", "", "5/10 до SDK", ""]` → note `"5/10 до SDK"`), `test_ask_one_unknown_repeats`, `test_ask_one_stop_word_is_none`.
  - `test_settle_no_checks_is_silent` (`capsys` пуст, запись `completed`, Ops не звались).
  - `test_settle_no_tty_stops` (= `NO_TTY.format(n=2)`).
  - `test_settle_stop_word_keeps_given_answers` (ответ на первую сохранён, второй нет; повторный `settle` спрашивает только вторую).
  - `test_settle_unreadable_bundle_stops` (`load_texts` бросает `OSError` → причина начинается `бандл прогона не прочитан`).
  - `test_settle_publish_error_stops`.
- [ ] **Step 2:** FAIL.  **Step 3:** реализовать.  **Step 4:** PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): вопрос по ручной проверке и проход ответов (#771)`.

### Task 5: Enterprise — шаг в `dfl`, «закончен», подсказка, status

**Files:**
- Modify: `governance/run_state.py` (`RunState` — поле `manual_checks: bool = False` последним; `new_run(..., manual_checks: bool = False)`), `governance/runner.py` (`start` → `new_run(..., manual_checks=True)`), `lite/dfl.py` (`_finished`, `_engineer_step`, `_next_hint`, `status`)
- Test: `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Task 4 `settle`, Task 1 `pending`/`summary`, Task 2 `issue_state`. Запись — `state.ops["manual-checks"]`; PR/URL — `state.ops["code-accept"]["result"]["pr"|"url"]`; бандл — `Path(target_dir)/bundle_dir/"15-behaviour-spec.md"` и `"30-decomposition.md"`.
- Produces (в `lite/dfl.py`): `_manual_pending(eng) -> bool` = `mc.pending(eng.manual_checks, eng.ops.get("manual-checks"))`; `_manual_step(eng) -> int | None` — вызывает `settle` **вне** `runlog.captured` с `read=ic.stdin_read`, `tty=_is_tty()`, `ops=_QuietOps(RealOps())`, `save=lambda: rs.save(eng)`; стоп → `say(reason)`, `0`; прошло — `None`.

Поведение:
- `_finished(cust, runs)` = url есть **и** `not _manual_pending(eng)` → новая тема в репо отказывает, пока не отвечено.
- `_engineer_step`: url нет → `loop(...)`; после кода 0 перечитать прогон; url появился и `_manual_pending` → `_manual_step`, затем финальная строка. Url есть → сначала `_manual_step` (если pending), затем прежние три ветки (`merged_unreviewed`, `code_merged`, «проверен»), каждая с `"; " + summary`, если summary не пуст.
- `_next_hint`: url есть и `_manual_pending` → `"ждут ответа ручные проверки — dfl run"` (без «конвейер закончен»); иначе прежний текст + `"; " + summary` (если не пуст).
- `status -v` (уровень 1): для прогона с ответами — строка на проверку `  {BEH} ({TASK}): {ответ}[ — note][ → issue #N (открыт|закрыт)]`; состояние issue — `issue_state` только на `-v`; `closed` передаётся в `summary`.

- [ ] **Step 1: failing tests** (помощники `_run`, `_accepted`, `_tty`; бандл — файлы в `target_dir` прогона: тесту нужен `target_dir=tmp_path`, при необходимости параметр `_run(..., target_dir=…)`; `RealOps` подменить фейком, как в соседних тестах файла):
  - `test_old_run_finished_without_questions` — `manual_checks` по умолчанию False, `code-accept` с url → вывод прежний, `stdin_read` не зван.
  - `test_new_run_asks_before_finished` — `manual_checks=True`, бандл с одной manual-проверкой, tty, ответы `["1", ""]` → вывод содержит вопрос и `PR с кодом проверен`, запись `completed`, комментарий опубликован.
  - `test_new_run_no_tty_stops_with_hint` — без tty → `NO_TTY`, код 0, запись не `completed`.
  - `test_unanswered_blocks_new_subject` — `--subject другая` → код 2, `конвейер репо не закончен`.
  - `test_not_done_creates_issue_and_honest_line` — ответ `2` → `create_issue(labels=())`, финальная строка содержит `не выполнено: 1 (BEH-… → issue #`.
  - `test_merged_unreviewed_also_asks` — ветка «вмержен без автоматической приёмки» тоже спрашивает.
  - `test_hint_while_pending` — `_next_hint` = `ждут ответа ручные проверки — dfl run`.
  - `test_status_verbose_shows_issue_state` — `-v`, issue `CLOSED` → `выполнено позже`.
  - `tests/test_governance_runner.py`: `test_start_marks_manual_checks` — `start` создаёт прогон с `manual_checks is True`; старый `run.json` без ключа грузится с `False`.
- [ ] **Step 2:** FAIL.  **Step 3:** реализовать.  **Step 4:** `uv run --group governance pytest tests/test_lite_dfl.py tests/test_governance_runner.py tests/test_governance_run_state.py -q` → PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): enterprise — ручные проверки перед «конвейер закончен» (#771)`.

### Task 6: Личный путь, документация

**Files:**
- Modify: `lite/personal/state.py` (`PersonalRun`: `manual_checks: bool = False`, `manual: dict = field(default_factory=dict)`; `new_personal_run(..., manual_checks: bool = False)`), `lite/dfl.py:~697` (вызов `new_personal_run(..., manual_checks=True)`; строка `личный прогон …` в `status` + summary), `lite/personal/flow.py` (`_step_code_pr`), `README.md` (подраздел «Ручные проверки» после «Пауза на просмотр этапа»), `TODO.md` (пункт `@id:dfl-manual-checks` `[x]`)
- Test: `tests/test_lite_personal_flow.py`, `tests/test_lite_personal_state.py` (если есть; иначе в flow-тестах)

**Interfaces:**
- Consumes: Task 4 `settle`; бандл — `Path(run.target_dir)/run.bundle_dir/"30-decomposition.md"` как **оба** текста (`load_texts = lambda: (whole, whole)`); PR — `run.code_pr`, url `https://github.com/{repo_slug}/pull/{pr}`.

Поведение `_step_code_pr`: в начале, если `mc.pending(run.manual_checks, run.manual)` → `settle(run.manual, tty=ctx.tty(), read=ctx.read, ops=ctx.ops, save=lambda: save(run), …)`; причина → `ctx.stop(reason)`. Прошло → прежняя логика; строка MERGED — `integration-PR #{pr} вмержен — конвейер закончен` + `"; " + summary` (если не пуст). Вопрос задаётся, пока PR открыт (до строки «смержите …»).

- [ ] **Step 1: failing tests:**
  - `test_old_personal_run_merged_without_questions` (`manual_checks=False` — байт в байт прежнее).
  - `test_personal_asks_before_merge_line` — PR OPEN, tty, ответ `1` → вопрос, затем `смержите …`; запись `completed`; FakeOps получил `comment`.
  - `test_personal_no_tty_stops` — `stop_reason == NO_TTY.format(n=1)`, шаг остаётся `code-pr`.
  - `test_personal_merged_line_has_summary` — ответ `2` → `create_issue`, строка `вмержен — конвейер закончен; ручные проверки — не выполнено: 1 …`.
  - `test_personal_state_roundtrip_manual` — `save`/`load` сохраняют `manual_checks` и `manual`; старый `run.json` без ключей → `False`/`{}`.
  - FakeOps (`tests/test_lite_personal_flow.py`) дополнить `comment`, `create_issue(…, labels=…)`, `find_issue(…, labels=…, state=…)`, `pr_comments_fact`.
- [ ] **Step 2:** FAIL.  **Step 3:** реализовать + README (как отвечать, что значит каждый ответ, issue без метки, ответ окончателен, `dfl status -v`) + TODO.
- [ ] **Step 4:** `uv run --group governance pytest tests/test_lite_personal_flow.py tests/test_lite_dfl.py -q` → PASS; затем полный `uv run --group governance pytest -q` в фоне → зелёный.
- [ ] **Step 5:** commit `feat(dfl): личный путь — ручные проверки перед «конвейер закончен» (#771)`.
