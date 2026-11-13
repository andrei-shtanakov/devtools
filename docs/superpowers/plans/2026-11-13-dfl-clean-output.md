# Чистый вывод dfl — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** По умолчанию `dfl` показывает 1–3 строки: итог шага и действие,
которое требуется от человека. Флаги `-v`/`-vv`/`-vvv` добавляют подсказки,
состояние и полный вывод. Полный вывод всегда пишется в лог, его показывает
`dfl log`.

**Architecture:** Уровень подробности и печать по уровням живут в
`lite/output.py`, функция `say` дублирует каждую строку в текущий лог.
`lite/runlog.py` отвечает за файлы логов, ротацию и перехват вывода раннера и
его потомков через `os.dup2` в файл. `lite/dfl.py` разбирает флаги, открывает
лог, оборачивает вызовы раннера, сам формулирует итог шага и добавляет команду
`log`. `lite/report.py` берёт хвост вывода из лога.

**Tech Stack:** Python 3.12, stdlib (`os.dup2`, `argparse`), pytest (`capfd`
для проверок на уровне fd), uv, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-11-13-dfl-clean-output-design.md`

## Global Constraints

- `governance/runner.py` и прочие модули раннера не меняются. `make`-путь
  остаётся прежним.
- Коды выхода и логика шагов конвейера не меняются.
- Уровни: 0 — без флага, `-v`=1, `-vv`=2, `-vvv`=3. Больше 3 считается за 3.
- `DFL_VERBOSITY=0..3` задаёт уровень по умолчанию, флаг сильнее. Неверное
  значение считается за 0 и печатает одну строку предупреждения.
- Флаги принимаются и до, и после подкоманды: `dfl -v run`, `dfl run -vv`.
- Лог пишут только `run`, `answer`, `correct`, `init-repo`. Путь —
  `$XDG_STATE_HOME/darkfactory/logs/<repo_key>/<имя>.log`, где `repo_key` —
  `lite.failure_log.repo_key`. Хранятся 20 логов на репо. Лог из `last.json`
  (`log_path`) ротацией не удаляется.
- На любом уровне всегда видны: строка режима test, отказ блоком, требуемое
  действие человека, текст ожидающего вопроса и при сбое — `подробно: dfl log`.
- При перехвате в окружении выставляются `GH_PROMPT_DISABLED=1` и
  `GIT_TERMINAL_PROMPT=0`.
- Строки для человека — на русском, в стиле существующих строк `dfl`.
  Идентификаторы и имена файлов — на английском.
- Литералы логинов владельца в коде запрещены (`test_no_owner_identities`).
- Тесты запускаются только по `tests/test_lite_*.py` и
  `tests/test_df_config.py`, полный набор не прогоняется:
  `uv run pytest tests/test_lite_*.py tests/test_df_config.py -q`. Перед
  коммитом нужны `uv run ruff format`, `uv run ruff check` и
  `uv run pyrefly check` по изменённым файлам.

## Review Focus

1. Ctrl-C или исключение, пока перехват активен и потомок ещё пишет: fd 1 и 2
   восстановлены, «прервано» видно на экране, код 130. Тест в Task 2.
2. Экран закрыт (stdout — pipe с закрытым концом чтения, как у `| head`):
   перехват не виснет и не падает, вывод идёт в файл. Тест в Task 2.
3. Каталог логов недоступен для записи: `dfl` работает, печатает одну строку
   «лог не пишется: …» и идёт без перехвата, как на уровне 3. Тест в Task 3.
4. Два вызова `dfl` подряд в пределах одной секунды: имена логов различаются,
   ротация не трогает свежий лог. Тест в Task 2.
5. Потомок пишет байты не в UTF-8: `dfl log` и `dfl report` не падают
   (`errors="replace"`). Тест в Task 5.

---

### Task 1: Уровни и `say` в `lite/output.py`; строка режима test

**Files:**
- Modify: `lite/output.py`
- Modify: `governance/df_config.py` (рядом с `test_banner`)
- Test: `tests/test_lite_output.py`, `tests/test_df_config.py`

**Interfaces:**
- Produces:
  - `set_verbosity(level: int) -> None` — ограничивает до 0..3;
  - `verbosity() -> int`;
  - `set_sink(sink: TextIO | None) -> None` — текущий лог, `None` — без лога;
  - `say(text: str, level: int = 0, *, err: bool = False) -> None`;
  - `verbosity_from_env(env: Mapping[str, str]) -> tuple[int, str | None]` —
    возвращает уровень и предупреждение или `None`;
  - `df_config.test_banner_line(cfg: DfConfig) -> str | None`.

`say` работает так:

- если `verbosity() >= level`, печатает в `sys.stdout` или `sys.stderr` (при
  `err`);
- независимо от уровня, если есть sink: сначала `sys.stdout.flush()` и
  `sys.stderr.flush()`, затем `sink.write(text + "\n")` и `sink.flush()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_say_respects_level(capsys):
    set_verbosity(0); set_sink(None)
    say("итог"); say("подсказка", 1)
    assert capsys.readouterr().out == "итог\n"

def test_say_always_writes_sink(capsys):
    buf = io.StringIO(); set_verbosity(0); set_sink(buf)
    say("скрыто", 3, err=True)
    assert capsys.readouterr().err == "" and buf.getvalue() == "скрыто\n"
    set_sink(None)

def test_set_verbosity_clamps():
    set_verbosity(7); assert verbosity() == 3
    set_verbosity(-1); assert verbosity() == 0

@pytest.mark.parametrize("raw,level,warned", [
    ("", 0, False), ("2", 2, False), ("9", 3, False), ("abc", 0, True)])
def test_verbosity_from_env(raw, level, warned):
    got, warn = verbosity_from_env({"DFL_VERBOSITY": raw} if raw else {})
    assert got == level and (warn is not None) == warned
```

В `tests/test_df_config.py`:

```python
def test_test_banner_line_counts_relaxations(...):  # test-конфиг как у соседних тестов
    line = df_config.test_banner_line(cfg)
    assert line == (f"режим test: ослаблено {len(df_config.TEST_RELAXATIONS)} "
                    "проверок — список: dfl status -v")

def test_test_banner_line_strict_none(...):
    assert df_config.test_banner_line(strict_cfg) is None
```

Значение `"9"` в тесте считается за 3: верхний предел — это ограничение, а не
неверное значение. Неверным считается только то, что не является целым числом
≥ 0.

- [ ] **Step 2: Run them, expect FAIL** (ImportError/AttributeError)

`uv run pytest tests/test_lite_output.py tests/test_df_config.py -q`

- [ ] **Step 3: Implement.** Состояние уровня и sink — переменные модуля.
  `test_banner` не меняется.

- [ ] **Step 4: Run, expect PASS**; ruff, pyrefly.

- [ ] **Step 5: Commit** `feat(lite): уровни подробности и say; строка режима test`

---

### Task 2: `lite/runlog.py` — логи, ротация, перехват

**Files:**
- Create: `lite/runlog.py`
- Test: `tests/test_lite_runlog.py`

**Interfaces:**
- Consumes: `lite.failure_log.repo_key`, а также `_home_state` или правило
  `XDG_STATE_HOME` из `record_path`. Повторите то же правило: абсолютный
  `XDG_STATE_HOME`, иначе `~/.local/state`.
- Produces:
  - `KEEP = 20`;
  - `log_dir(cwd: Path, env: Mapping[str, str] = os.environ) -> Path`;
  - `@dataclass class RunLog: path: Path; fh: TextIO`;
  - `open_log(cwd: Path, command: str, header: str, *, protect: Path | None) -> RunLog`.
    Создаёт каталог `0o700`. Имя файла —
    `<UTC %Y%m%dT%H%M%S%fZ>-<command>.log`, файл открывается на дозапись
    (`encoding="utf-8"`, `errors="replace"`). Первой строкой пишется
    `# <header>`. Затем `prune(dir, KEEP, protect)`.
  - `close_log(log: RunLog, code: int) -> None` — пишет последней строкой
    `# код выхода: <code>` и закрывает файл.
  - `prune(directory: Path, keep: int, protect: Path | None) -> None` —
    удаляет самые старые по имени, кроме `protect`.
  - `@contextmanager captured(log: RunLog | None, level: int) -> Iterator[None]`:
    - при `log is None` или `level >= 3` — сквозной проход;
    - иначе: flush `sys.stdout`/`sys.stderr`, `log.fh.flush()`, `dup` fd 1 и 2,
      `dup2(log.fh.fileno(), 1/2)`, в `os.environ`
      `GH_PROMPT_DISABLED=1` и `GIT_TERMINAL_PROMPT=0`;
    - в `finally`: flush, восстановить fd и окружение (прежние значения или
      их отсутствие), закрыть дубликаты.
  - `@dataclass class LogInfo: path: Path; command: str; code: int | None`;
  - `list_logs(cwd: Path) -> list[LogInfo]` — от старых к новым. Команда
    берётся из имени файла, код — из последней строки `# код выхода: N`, если
    её нет — `None`.
  - `read_log(path: Path) -> str` — `errors="replace"`.

- [ ] **Step 1: Write the failing tests** (`capfd`, `XDG_STATE_HOME` → `tmp_path`)

```python
def test_captured_level0_hides_python_and_child(tmp_path, capfd):
    log = open_log(tmp_path, "run", "dfl run", protect=None)
    with captured(log, 0):
        print("runner-line"); sys.stdout.flush()
        subprocess.run(["sh", "-c", "echo child-line; echo child-err >&2"])
    print("after")
    close_log(log, 0)
    out, err = capfd.readouterr()
    assert "runner-line" not in out + err and "child-line" not in out + err
    assert "after" in out
    text = read_log(log.path)
    assert "runner-line" in text and "child-line" in text and "child-err" in text
    assert text.rstrip().endswith("# код выхода: 0")

def test_captured_level3_passes_through(tmp_path, capfd): ...
    # тот же сценарий с level=3: child-line на экране, в логе его нет

def test_captured_restores_on_keyboardinterrupt(tmp_path, capfd):
    log = open_log(tmp_path, "run", "h", protect=None)
    with pytest.raises(KeyboardInterrupt):
        with captured(log, 0):
            raise KeyboardInterrupt
    print("visible"); sys.stdout.flush()
    assert "visible" in capfd.readouterr().out
    assert "GH_PROMPT_DISABLED" not in os.environ  # если не было до

def test_captured_with_closed_screen(tmp_path):
    # fd 1 → pipe с закрытым концом чтения; внутри captured печать и потомок
    # не бросают BrokenPipeError, всё в логе; fd 1 восстановлен после выхода
    ...

def test_prune_keeps_20_and_protected(tmp_path):
    # 22 файла с возрастающими именами; protect = самый старый
    # → остаются protect и 20 новейших (21 файл)

def test_open_log_names_unique_back_to_back(tmp_path):
    a = open_log(tmp_path, "run", "h", protect=None)
    b = open_log(tmp_path, "run", "h", protect=None)
    assert a.path != b.path

def test_list_logs_parses_command_and_code(tmp_path): ...
    # закрытый с кодом 2 → code 2; незакрытый → None; порядок по времени
```

- [ ] **Step 2: Run, expect FAIL** (ModuleNotFoundError)

`uv run pytest tests/test_lite_runlog.py -q`

- [ ] **Step 3: Implement** по Interfaces.

- [ ] **Step 4: Run, expect PASS**; ruff, pyrefly.

- [ ] **Step 5: Commit** `feat(lite): лог вызова dfl и перехват вывода раннера в файл`

---

### Task 3: Флаги, лог вызова и перехват в `dfl`; запись сбоя

**Files:**
- Modify: `lite/dfl.py` (`main`, `_parser`, `_dispatch`, `_require_test_mode`,
  `_step`, `_engineer_step`, `_brief_step`, `_discovery`, `_record`)
- Modify: `lite/failure_log.py` (`FailureRecord`)
- Test: `tests/test_lite_dfl.py`, `tests/test_lite_failure_log.py`

**Interfaces:**
- Consumes: Task 1 (`set_verbosity`, `verbosity`, `set_sink`, `say`,
  `verbosity_from_env`, `test_banner_line`) и Task 2 (`open_log`, `close_log`,
  `captured`).
- Produces:
  - `LOGGED_COMMANDS = frozenset({"run", "answer", "correct", "init-repo"})`;
  - поля `FailureRecord.log_path: str = ""` и `FailureRecord.verbosity: int = 0`.
    Значения по умолчанию нужны, чтобы старые записи читались;
  - модульный `_LOG: RunLog | None` текущего вызова. Его читает `_record`.

Правила реализации:

- Флаг `-v` объявляется с `action="count"` в родительском парсере. Тот
  передаётся `parents=` и в корневой парсер, и в каждый `add_parser`. Атрибуты
  получают разные имена (`dest="verbose_root"` и `dest="verbose"`) и
  складываются. Если флага нет, уровень берётся из `verbosity_from_env`, а
  предупреждение печатается `say(..., 0, err=True)`.
- `main` открывает лог только для `LOGGED_COMMANDS`, после разбора аргументов и
  после строки режима test:
  - заголовок лога — `dfl <command> <_public_argv> уровень N`;
  - `protect` — `Path(fl.load(cwd).log_path)`, если есть;
  - `set_sink(log.fh)`;
  - если уровень 3 — строка `say("уровень 3: вывод раннера шёл на экран", 4)`.
    Уровня 4 нет, поэтому строка попадает только в лог.
  - `finally` вокруг `_dispatch`: `close_log(log, code)` и `set_sink(None)`.
- Если `open_log` бросил `OSError`, печатается
  `say(f"лог не пишется: {err}", 0, err=True)` и работа идёт без лога.
  `captured(None, …)` — сквозной проход.
- Вызовы `spec_loop.main` и `bt.main` в `_step`, `_engineer_step` и
  `_brief_step` оборачиваются в `with captured(_LOG, verbosity()):`.
- `_discovery` после `subprocess.run` вызывает `say(done.stdout, 3)` и
  `say(done.stderr, 3, err=True)` для непустых.
- `_require_test_mode`:
  - на уровне 3 — `say(df_config.test_banner(cfg))`;
  - иначе — `say(df_config.test_banner_line(cfg))`;
  - затем пустая строка, как раньше.
- Отказ в `_dispatch` идёт через `say(refusal(...), 0, err=True)`.
- `REPORT_HINT` при ненулевом коде печатается через `say(REPORT_HINT, 1, err=True)`,
  а перед ним `say("подробно: dfl log", 0, err=True)`. Вторую строку печатаем,
  только если вызов писал лог; без лога — прежний `REPORT_HINT` на уровне 0.
- `_record` пишет `log_path=str(_LOG.path) if _LOG else ""` и `verbosity=verbosity()`.
  `_record` выполняется отложенно, когда лог уже закрыт: путь сохраните до
  закрытия, в переменную рядом с `_LOG`.

Существующие тесты `tests/test_lite_dfl.py`, которые проверяют строки уровня
≥1 (`REPORT_HINT`, `ANSWER_FORMAT`, подсказки), переводятся на `-v` в argv.
Их ожидания не меняются, кроме `REPORT_HINT`. Ruling на исполнителе не нужен:
так требует спека §3.

- [ ] **Step 1: Write the failing tests** в `tests/test_lite_dfl.py`
  (fixture `env`; `spec_main` фикстуры печатает `"runner-line"`; используйте
  `capfd`)

```python
def test_level0_hides_runner_output_and_logs_it(env, capfd):
    _run(env, ["run", "--subject", "t"])
    out, err = capfd.readouterr()
    assert "runner-line" not in out + err
    assert "режим test: ослаблено" in out
    [info] = runlog.list_logs(env.repo_path)  # имя поля фикстуры — по факту
    assert "runner-line" in runlog.read_log(info.path)

def test_vvv_shows_runner_output_and_full_banner(env, capfd):
    _run(env, ["run", "-vvv", "--subject", "t"])
    out, _ = capfd.readouterr()
    assert "runner-line" in out and df_config.TEST_BANNER_HEADER in out

def test_flag_before_and_after_subcommand_add_up(env): ...
    # dfl -v run -v → verbosity() == 2 во время шага (spec_main фиксирует)

def test_env_verbosity_default_and_flag_wins(env, monkeypatch): ...
def test_env_verbosity_garbage_warns(env, monkeypatch, capfd): ...

def test_status_writes_no_log(env, ...):
    # dfl status → runlog.list_logs пуст

def test_log_dir_unwritable_runs_without_log(env, monkeypatch, capfd):
    # XDG_STATE_HOME → файл, а не каталог → «лог не пишется:» в err,
    # runner-line на экране, код шага прежний

def test_failure_record_has_log_path_and_level(env, ...):
    # spec_main возвращает 3 → fl.load(cwd).log_path существует, verbosity == 0;
    # в err «подробно: dfl log», REPORT_HINT нет; с -v — есть

def test_discovery_output_goes_to_log_only(env, ...):
    # _discovery с подменённым subprocess.run (stdout "disc-out") на уровне 0:
    # нет на экране, есть в логе
```

В `tests/test_lite_failure_log.py`: старая запись без `log_path` и
`verbosity` читается `load` с пустыми значениями по умолчанию.

- [ ] **Step 2: Run, expect FAIL**

`uv run pytest tests/test_lite_dfl.py tests/test_lite_failure_log.py -q`

- [ ] **Step 3: Implement** по правилам выше; поправьте затронутые прежние
  тесты (`-v`).

- [ ] **Step 4: Run lite scope, expect PASS**

`uv run pytest tests/test_lite_*.py tests/test_df_config.py -q`

- [ ] **Step 5: Commit** `feat(lite): dfl -v/-vv/-vvv, лог вызова и перехват вывода раннера`

---

### Task 4: Итог шага, вопрос на экране, подсказки и состояние по уровням, `status -v/-vv`

**Files:**
- Modify: `lite/dfl.py` (`run`, `_run_chatting`, `_step`, `_hint_if_waiting` →
  удаляется, `status`, новые `_outcome`, `_state_lines`, `_say_question`)
- Test: `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: `say`, `verbosity`; `fl.snapshot_runs`, `fl.changed_runs`,
  `fl.cause_from`; `_next_hint`; `_discovery_status`; `ic.current_question`.
- Produces:
  - `_loop(argv: list[str], cwd: Path, *, chatting: bool) -> int` — единая
    обёртка вызова `spec_loop.main`, заменяет прямые вызовы в `_step`,
    `_engineer_step` и `_brief_step`. Делает следующее:
    1. снимает `before = fl.snapshot_runs()` и ops прогонов до шага;
    2. выполняет `captured(...)` вокруг `spec_loop.main(argv)`;
    3. по `fl.changed_runs(before, after, cwd)` берёт последний изменённый
       прогон;
    4. печатает итог `_outcome`, состояние `_state_lines` и подсказку
       `_next_hint`.

    Протяните `cwd` и `chatting` через `_step`: сигнатуры внутренних функций
    можно менять.
  - `_outcome(state: rs.RunState, code: int, *, chatting: bool) -> str | None`.
    Правила по спеке §5:
    - `WAITING` и `chatting` → `None`: вопрос покажет цикл;
    - `WAITING` без чата → `None`, а вопрос печатает `_say_question`;
    - `stopped_*` → `f"прогон {id} остановлен: {причина}"`, где причина — первая
      строка `fl.cause_from(None, [id], [], refusals=REFUSALS)`;
    - `code != 0` без причины → `f"шаг завершился с кодом {code}"`;
    - иначе → `f"прогон {id}: {state.status}"`.

    Итог печатается на уровне 0. При `code != 0` строку `подробно: dfl log`
    печатает `main` (Task 3). При `stopped_*` с `code == 0` её печатает сам
    `_loop`. На вызов — ровно одна такая строка.
  - `_say_question(state: rs.RunState) -> None`. Печатает на уровне 0 текст
    текущего вопроса из `ic.current_question(_discovery_status(...))`, затем
    `ANSWER_HINT`; `ANSWER_FORMAT` — на уровне 1. Если вопрос не прочитался
    (`None` или исключение `InterviewStateError`/`OSError`), печатает
    `f"прогон {id} ждёт ответа; вопрос: dfl interview"`.
  - `_state_lines(before_ops: dict, state: rs.RunState) -> list[str]` — уровень 2:
    - `f"{run_id} · {frame} · {status}"`, к ней `· PR #n` и `· волна w`, если
      есть;
    - `"шаги: a, b"` — ключи `ops`, у которых статус изменился или появился за
      вызов;
    - если таких нет — `"шаги: —"`.
  - `_next_hint` печатается на уровне 1, в форме `f"дальше: {hint}"`.
  - `status`: на уровне 1 в шапке — полный `test_banner` и `конфиг: <путь>`
    (`df_config.canonical_config_path()`). На уровне 2 под каждым прогоном —
    его `ops` строками `f"  {key}: {status}"`. Уровень 0 не меняется.

Строки, которые `dfl` уже печатает сам («бриф ждёт вашего мержа…», «PR с
кодом проверен…», «…конвейер закончен»), остаются на уровне 0 через `say` без
изменения текста.

- [ ] **Step 1: Write the failing tests**

```python
def test_waiting_without_tty_shows_question_text(env, monkeypatch, capfd):
    # spec_main переводит прогон в waiting_interview; _discovery_status подменён
    # → reply с вопросом «Кто ваш клиент?»; _is_tty False
    _run(env, ["run", "--subject", "t"])
    out, _ = capfd.readouterr()
    assert "Кто ваш клиент?" in out and ANSWER_HINT in out
    assert "вопрос выше" not in out and "text (ответ своими словами)" not in out

def test_waiting_question_unreadable_points_to_interview(...): ...
def test_stopped_run_prints_cause_and_log_hint(env, capfd):
    # stop-reason.txt «нет прав на репо» → «остановлен: нет прав на репо» и
    # «подробно: dfl log» на уровне 0
def test_level1_adds_next_hint(...): ...   # «дальше:» есть с -v, нет без
def test_level2_adds_state_lines(...): ... # «шаги:» с -vv, нет с -v
def test_brief_waiting_merge_visible_at_level0(...): ...
def test_chatting_waiting_prints_no_answer_hint(...): ...
def test_status_v_lists_relaxations_and_config(...): ...
def test_status_vv_lists_ops(...): ...
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_lite_dfl.py -q`

- [ ] **Step 3: Implement**; удалите `_hint_if_waiting` и его вызов.

- [ ] **Step 4: Run lite scope, expect PASS**

- [ ] **Step 5: Commit** `feat(lite): итог шага dfl по журналу, вопрос на экране, подсказки и состояние по уровням`

---

### Task 5: `dfl log` и хвост отчёта из лога

**Files:**
- Modify: `lite/dfl.py` (подкоманда `log`), `lite/report.py` (`build`/хвост)
- Test: `tests/test_lite_dfl.py`, `tests/test_lite_report.py`

**Interfaces:**
- Consumes: `runlog.list_logs`, `runlog.read_log`, `FailureRecord.log_path` и
  `.verbosity`.
- Produces:
  - `dfl log [--list] [-n N]`. Работает до `_require_test_mode`, как `report`:
    регистрации и конфига не требует.
    - Без аргументов печатает последний лог целиком.
    - `-n N` печатает N-й с конца, `-n 1` — то же, что без аргументов. Если
      N больше числа логов — отказ блоком `refusal` с кодом 2.
    - `--list` печатает строки `f"{время} {команда} код {code or '—'}"` от
      новых к старым.
    - Если логов нет — `логов нет`, код 0.
    - Если показанный лог закрыт ненулевым кодом, последней строкой идёт
      `REPORT_HINT`.
    - Строку режима test `dfl log` не печатает и лог не пишет.
  - `report.tail_lines(rec: FailureRecord) -> list[str]`:
    - если `rec.log_path`, `rec.verbosity < 3` и файл читается — последние
      200 строк `read_log`, без строк `# …` заголовка и кода;
    - иначе — `rec.output_tail`.

    Используется вместо `rec.output_tail` в сборке тела, и к нему тот же
    `redact`.

- [ ] **Step 1: Write the failing tests**

```python
def test_log_prints_last_and_report_hint_on_failure(env, capfd): ...
def test_log_n_and_list(env, capfd): ...
def test_log_n_too_large_refused(env, capfd): ...
def test_log_empty(env, capfd): ...          # «логов нет», код 0
def test_log_non_utf8_bytes(env, capfd):
    # лог содержит b"\xff\xfe" от потомка → печать без исключения
```

В `tests/test_lite_report.py`:

```python
def test_tail_from_log_includes_child_output_redacted(tmp_path): ...
    # лог с "child-line" и "token=ghp_abc" → в теле child-line, токена нет
def test_tail_level3_uses_output_tail(tmp_path): ...
def test_tail_missing_log_uses_output_tail(tmp_path): ...
def test_tail_caps_200_lines(tmp_path): ...
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run lite scope, expect PASS**

- [ ] **Step 5: Commit** `feat(lite): dfl log; хвост отчёта dfl report из лога вызова`

---

### Task 6: Документация

**Files:**
- Modify: `docs/darkfactory-for-colleagues.md`
- Test: `tests/test_lite_docs.py`, если он сверяет перечень команд `dfl`.
  Добавьте `log` и флаги туда, где перечислены команды.

Добавьте раздел «Сколько `dfl` пишет на экран»:

- таблица уровней из спеки §3;
- `DFL_VERBOSITY`;
- `dfl log` и `--list`/`-n`;
- что лог хранится 20 вызовов;
- пример вывода `dfl run` на уровне 0 в двух случаях: ожидание мержа брифа и
  остановка с причиной.

Упоминания `dfl report` дополните: хвост берётся из лога. Тексты строк в
примерах должны совпадать с кодом из Task 3–5.

- [ ] **Step 1:** если `test_lite_docs.py` сверяет команды — добавьте
  ожидание `dfl log`, запустите, ожидайте FAIL.
- [ ] **Step 2:** обновите документ; запустите `uv run pytest tests/test_lite_docs.py -q` → PASS.
- [ ] **Step 3: Commit** `docs: уровни вывода dfl и dfl log`

Живая проверка на сервере (homelab, df-linux-smoke) — после влития, по
спеке §8.
