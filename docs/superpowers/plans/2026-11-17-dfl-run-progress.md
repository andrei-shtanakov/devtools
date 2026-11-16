# Видимый прогресс фазы реализации dfl (#765) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** пока `ops.spec_runner(...)` идёт, окно `dfl` показывает строки событий задач и живой посекундный отсчёт текущей задачи; в конце — итог «сделано N из M · время».

**Architecture:** чистое ядро `governance/run_progress.py` (путь БД, задачи из `tasks.md`, снимок БД только на чтение, разница снимков → события, тексты) + наблюдатель `Watcher` (детерминированный `tick(now)`; поток лишь зовёт его раз в секунду) + обёртка `watched(call, …)` вокруг блокирующего вызова раннера. Вывод — через `notice` (`DFL_SCREEN_FD`), живая строка — `\r…\x1b[K` в экранный fd, только если он терминал и уровень < PASSTHROUGH. Оба пути (`auto_tasks._spec_run`, `flow._step_code_run`) оборачивают свой вызов.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `threading`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-run-progress-design.md`

## Global Constraints

- События — новой строкой: `▶ {TASK} «{title}» — начата`, `✓ {TASK} готова · {время}`, `↻ {TASK} «{title}» — попытка {N}`, `✗ {TASK} не прошла: {первая строка ошибки}`.
- Живая строка: `  {TASK} · {N} из {M} · {время}`; раз в секунду; только TTY и уровень < 3.
- Время: `ММ:СС`, от часа — `Ч:ММ:СС`.
- Итог: `реализация: сделано {N} из {M} · {время}`. Стоимости нет.
- БД — только на чтение (`file:{path}?mode=ro`, URI); ошибка чтения — пропуск такта.
- БД не появилась за 30 с — одна строка `прогресс недоступен: нет {путь}`.
- События до старта этого запуска не печатаются (базовый снимок при старте).
- Наблюдатель никогда не валит прогон и не меняет код возврата раннера.
- `governance` не импортирует `lite`; уровень вывода — из `DFL_VERBOSITY` (`lite.output.VERBOSITY_ENV`).

## Review Focus

1. Раннер падает/выходит — живая строка стёрта, поток остановлен, код возврата тот же (Task 3 `test_watched_returns_runner_code_and_stops`).
2. БД занята писателем / битая / пропала посреди прогона — пропуск такта, без исключения (Task 1 `test_read_snapshot_unreadable_is_none`).
3. Повторная попытка: `started_at` сдвинулся при статусе `running` — событие «попытка N», отсчёт с нуля (Task 2 `test_retry_event_and_timer_reset`).
4. Продолжение прогона (#767): задачи `success` до старта не печатаются, но входят в «сделано» (Task 2 `test_baseline_suppresses_old_events`).
5. Вывод в файл (не TTY) — ни одного `\r` (Task 3 `test_no_tty_no_live_line`).

---

### Task 1: Ядро — путь БД, задачи, снимок, тексты

**Files:** Create `governance/run_progress.py`; Test `tests/test_governance_run_progress.py`

**Interfaces — Produces:**
- `state_db_path(target_dir: str, prefix: str) -> Path` — как spec-runner `config.py`: конфиг `spec-runner.config.yaml` (корень, плоский) или legacy `spec/executor.config.yaml` (обёртка `executor:`); ключ `paths.state` (если задан — префикс не применяется), `paths.root` — база относительного пути; иначе `spec/.executor-{prefix}state.db`. Нечитаемый конфиг — правило по умолчанию.
- `read_tasks(text: str) -> list[tuple[str, str]]` — `(id, title)` по заголовкам `_TASK_HEAD_RE` (`### TASK-004: Title`) в порядке файла.
- `@dataclass(frozen=True) TaskRow(status: str, started_at: str | None, completed_at: str | None, attempts: int, last_error: str | None)`; `read_snapshot(db: Path) -> dict[str, TaskRow] | None` — `tasks` + агрегат `attempts` (`COUNT(*)`, ошибка последней по `timestamp`), `mode=ro`; нет файла / `sqlite3.Error` — `None`.
- `fmt_duration(seconds: float) -> str`; `event_line(kind: str, task_id: str, title: str, *, seconds: float | None = None, attempt: int | None = None, error: str | None = None) -> str` (kind ∈ `started|done|retry|failed`); `live_line(task_id, ordinal, total, seconds) -> str`; `summary_line(done, total, seconds) -> str`; `UNAVAILABLE = "прогресс недоступен: нет {path}"`.

- [ ] **Step 1: tests:** `test_state_db_path_default` (`/t/spec/.executor-WS-1-state.db`), `test_state_db_path_config_paths_state` (плоский конфиг `paths: {state: x/s.db}` → `/t/x/s.db`, без префикса), `test_state_db_path_legacy_wrapper`, `test_state_db_path_bad_yaml_default`, `test_read_tasks_titles_in_order`, `test_read_snapshot_rows` (реальная SQLite с таблицами по схеме `tasks`/`attempts`), `test_read_snapshot_missing_is_none`, `test_read_snapshot_unreadable_is_none` (файл-мусор), `test_fmt_duration` (`75 → "01:15"`, `3725 → "1:02:05"`), `test_event_lines_exact` (четыре формы из Global Constraints; ошибка — первая строка), `test_live_and_summary_lines`.
- [ ] **Step 2:** `uv run --group governance pytest tests/test_governance_run_progress.py -q` → FAIL.
- [ ] **Step 3:** реализовать. **Step 4:** PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): run_progress — путь БД, снимок, тексты прогресса (#765)`.

### Task 2: Наблюдатель — события из разницы снимков, отсчёт

**Files:** Modify `governance/run_progress.py`; Test `tests/test_governance_run_progress.py`

**Interfaces — Produces:**
- `class Watcher(db: Path, tasks: list[tuple[str, str]], *, emit: Callable[[str], None], live: Callable[[str], None] | None, now: Callable[[], float] = time.time)`:
  - `begin(t: float) -> None` — базовый снимок (без событий), запомнить старт.
  - `tick(t: float) -> None` — снимок → события через `emit`: новый `running` → `started`; `running`, а `started_at` сменился или `attempts` выросло без смены статуса → `retry` (попытка = `attempts + 1`); → `success` → `done` (время от первого увиденного старта в этом запуске, иначе от `started_at`); → `failed` → `failed` (`last_error`). Затем `live(...)` для текущей `running` задачи: отсчёт от её `started_at` (наивное ISO, локальное время; через `now`), ordinal = сделано + 1. БД нет 30 с от `begin` — один раз `emit(UNAVAILABLE)`. Любое исключение внутри — проглочено.
  - `summary(t: float) -> str` — `summary_line(сделано, M, t - begin)`; сделано = `success`/`skipped` в последнем снимке (включая до старта).
- Событие по задаче печатается один раз на переход.

- [ ] **Step 1: tests** (SQLite в `tmp_path`, `now` — фиктивные часы, `emit`/`live` — списки): `test_baseline_suppresses_old_events` (TASK-001 `success` до `begin` → нет событий, но `summary` считает её), `test_started_then_done_lines`, `test_retry_event_and_timer_reset`, `test_failed_line_has_first_error_line`, `test_live_line_counts_and_elapsed` (`"  TASK-002 · 2 из 3 · 00:05"`), `test_unavailable_once_after_30s`, `test_tick_swallows_errors` (`emit` бросает → `tick` не бросает).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): наблюдатель прогресса реализации (#765)`.

### Task 3: Экран и обёртка вызова раннера

**Files:** Modify `governance/run_progress.py`; Test `tests/test_governance_run_progress.py`

**Interfaces — Produces:**
- `screen_live() -> Callable[[str], None] | None` — `None`, если `DFL_VERBOSITY` ≥ 3 или экранный fd (из `DFL_SCREEN_FD`, иначе 1) не TTY (`os.isatty`); иначе пишет `"\r" + text + "\x1b[K"` в этот fd (fd читается в момент записи, ошибки `OSError` глушатся). Парная `clear_live()` — `"\r\x1b[K"`.
- `watched(call: Callable[[], int], *, target_dir: str, prefix: str, tasks_rel: str, period: float = 1.0) -> int` — читает `tasks_rel` (нет файла — пустой список, отсчёт без «из M»: `M=0` → ordinal/total опускаются), строит `Watcher(emit=notice, live=screen_live())`, `begin`, поток-демон `while not stop.wait(period): tick(time())`, вызывает `call()`, в `finally`: `stop.set()`, `join(timeout=2)`, финальный `tick`, `clear_live()` (если live был), `notice(summary)`. Возвращает код `call()` как есть; исключение `call()` — пробрасывается после остановки потока.

- [ ] **Step 1: tests:** `test_watched_returns_runner_code_and_stops` (call пишет в БД переходы и возвращает 3 → код 3, поток не жив, в выводе `▶`/`✓` и итог), `test_watched_propagates_exception_and_stops`, `test_no_tty_no_live_line` (`DFL_SCREEN_FD` — pipe → нет `\r` в прочитанном), `test_passthrough_no_live_line` (`DFL_VERBOSITY=3`), `test_live_line_on_tty` (pty через `os.openpty()` → в выводе есть `\r  TASK-` и финальный `\r\x1b[K`).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS + ruff.
- [ ] **Step 5:** commit `feat(dfl): прогресс на экран вокруг вызова раннера (#765)`.

### Task 4: Подключение в оба пути, документация

**Files:** Modify `governance/auto_tasks.py` (`_spec_run`, вызов `ops.spec_runner` ~L295), `lite/personal/flow.py` (`_step_code_run` ~L924), `README.md` (подраздел «Прогресс реализации» рядом с «Ручные проверки…»), `TODO.md` (раздел #765, `@id:dfl-run-progress` `[x]`); Test `tests/test_governance_auto_tasks.py`, `tests/test_lite_personal_flow.py`

**Interfaces — Consumes:** Task 3 `watched`. Enterprise: `watched(lambda: ops.spec_runner(state.target_dir, _run_args(state)), target_dir=state.target_dir, prefix=f"{state.ws_id}-", tasks_rel=f"spec/{state.ws_id}-tasks.md")`; личный — то же с `run` и `TASKS_PATH.format(ws_id=run.ws_id)`. `TUI_HINT` остаётся перед вызовом.

- [ ] **Step 1: tests:** enterprise `test_spec_run_prints_progress_events` — фейк `spec_runner` пишет в `<target_dir>/spec/.executor-WS-1-state.db` переход задачи `running→success`, `tasks.md` с одной задачей → вывод содержит `✓ TASK-001 готова` и `реализация: сделано 1 из 1`; RUN_ARGS и код возврата прежние. Личный `test_code_run_prints_progress_summary` — то же через `_advance`. Существующие тесты (фейк без БД) — зелёные: без БД итог `сделано 0 из N`, `прогресс недоступен` за 0 с не печатается.
- [ ] **Step 2:** FAIL. **Step 3:** подключить + README + TODO.
- [ ] **Step 4:** `uv run --group governance pytest tests/test_governance_run_progress.py tests/test_governance_auto_tasks.py tests/test_lite_personal_flow.py tests/test_lite_dfl.py -q` → PASS; полный прогон в фоне → зелёный.
- [ ] **Step 5:** commit `feat(dfl): прогресс реализации в обоих путях (#765)`.
