# `dfl report` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dfl` записывает каждый свой сбой, а `dfl report` после показа и подтверждения доставляет отчёт о нём публичным issue (или комментарием к дублю) в `andrei-shtanakov/devtools`.

**Architecture:** Три новых модуля в `lite/`: `failure_log.py` (ключ репо, запись сбоя, снимок прогонов, причина), `tee.py` (дублирование stdout/stderr на уровне дескрипторов в кольцевой буфер), `report.py` (сбор, очистка, отпечаток, отправка через `gh`). `lite/dfl.py` получает точку входа `cli()` (tee + `main`), обёртку записи сбоя в `main` и подкоманду `report`, обрабатываемую до `_require_test_mode`.

**Tech Stack:** Python 3.12 stdlib (`os.pipe`, `threading`, `hashlib`, `json`, `re`), `gh` CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-dfl-report-design.md`

## Global Constraints

- Репо-цель отчётов — `andrei-shtanakov/devtools` (константа `REPORT_REPO`).
- Запись сбоя: `$XDG_STATE_HOME/darkfactory/failures/<repo-key>/last.json`; без абсолютного `XDG_STATE_HOME` — `~/.local/state`.
- `<repo-key>` — первые 16 hex `sha256` от realpath `git rev-parse --show-toplevel` из `--cwd`, не git — от realpath `--cwd`.
- Хвост вывода — последние 200 строк.
- Тело issue/комментария — не длиннее 65 536 символов; режется хвост вывода, с пометкой.
- Заголовок: `[dfl-report] <command>: <причина, ≤80 символов> (fp <12hex>)`.
- Подсказка: строка `отчёт разработчикам: dfl report` — в stderr последней при ненулевом коде любой подкоманды, кроме `report`.
- Отправки без подтверждения в терминале нет; без tty — не отправлять.
- Отправка — профилем `accounts.human_gh_config_dir`, если конфиг читается, иначе `gh` по умолчанию.
- Транскрипты, файлы бандла, `brief`, ответы интервью, env — в отчёт не попадают.
- `lite/` вне authority-root; тесты не ходят в сеть (`gh` — подмена).

## Review Focus

1. Неперехваченное исключение в подкоманде: коллега видит тот же traceback и код 1, запись сбоя есть — тест в Task 3.
2. Сбой до регистрации цели (конфиг не test, `--cwd` не git): запись есть, ключ по `--cwd` — тест в Task 3.
3. Сломанный конфиг при `dfl report`: отчёт собирается со строкой «конфиг не читается», отправка через `gh` по умолчанию — тест в Task 5.
4. Токен в выводе (`ghp_…` в сообщении `gh`) не доходит до тела issue ни в новом issue, ни в комментарии — тест в Task 4.
5. Вывод дочернего процесса (`subprocess.run(["sh","-c","echo x >&2"])`) попадает в хвост, а на экране остаётся — тест в Task 2.

---

### Task 1: Запись сбоя и снимок прогонов (`lite/failure_log.py`)

**Files:**
- Create: `lite/failure_log.py`
- Test: `tests/test_lite_failure_log.py`

**Interfaces:**
- Produces:
  - `repo_key(cwd: Path) -> str`
  - `record_path(cwd: Path, env: Mapping[str, str] = os.environ) -> Path`
  - `@dataclass(frozen=True) class FailureRecord: command: str; argv: list[str]; exit_code: int; time: str; output_tail: list[str]; run_ids: list[str]; runs_note: str; cwd_repo: str; cause: str`
  - `write(cwd: Path, rec: FailureRecord) -> None` (атомарно: tmp + `os.replace`, каталоги `0o700`)
  - `load(cwd: Path) -> FailureRecord | None` (нет файла или битый JSON — `None`)
  - `RunsSnapshot = dict[str, float]`; `snapshot_runs() -> RunsSnapshot` (`run_id` → mtime `run.json`, по `rs.all_run_ids()`)
  - `changed_runs(before: RunsSnapshot, after: RunsSnapshot, cwd: Path) -> tuple[list[str], str]` — `(ids, note)`; изменённых нет → самый свежий прогон, чей `target_dir` realpath совпадает с корнем репо, и note `"вызов его не менял"`; нет и такого → `([], "")`
  - `cause_from(exc: BaseException | None, run_ids: list[str], tail: list[str]) -> str` — порядок спеки §5: refusal-исключение → «причина» (часть до первого « — » вне кавычек, тем же делителем, что `lite.output`); `stop-reason.txt` изменённого прогона — первая строка; прочее исключение — `f"{type(exc).__name__}: {первая строка}"`; иначе последняя непустая строка `tail`; всё пусто — `"причина не записана"`
  - `REFUSALS: tuple[type[BaseException], ...]` заполняется в Task 3 (`DflError`, `ConfigError`, `TargetsError`, `InitError`) — здесь параметр `refusals` у `cause_from` со значением по умолчанию `()`

- [ ] **Step 1: Write the failing tests**

```python
def test_repo_key_same_for_subdir_of_git_repo(git_repo): ...
    # repo_key(git_repo) == repo_key(git_repo / "sub"); len == 16
def test_repo_key_non_git_uses_cwd(tmp_path): ...
    # repo_key(tmp_path/"a") != repo_key(tmp_path/"b")
def test_record_path_respects_absolute_xdg_state_home(tmp_path): ...
    # .../darkfactory/failures/<key>/last.json; relative XDG -> ~/.local/state
def test_write_then_load_roundtrip(tmp_path): ...
def test_load_missing_or_broken_is_none(tmp_path): ...
def test_changed_runs_detects_new_and_touched(runs_root): ...
def test_changed_runs_falls_back_to_latest_of_repo(runs_root): ...
    # note == "вызов его не менял"
def test_cause_prefers_refusal_then_stop_reason_then_exception_then_tail(runs_root): ...
    # refusal "нет 'a — b' — сделайте" -> "нет 'a — b'"
    # RuntimeError("boom\nx") -> "RuntimeError: boom"
    # tail ["", "last line", ""] -> "last line"; all empty -> "причина не записана"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_failure_log.py -q` — FAIL (module missing).
- [ ] **Step 3: Implement** `lite/failure_log.py`. Делитель вынести из `lite/output.py` в публичную `split_refusal(message) -> tuple[str, str, str]` и использовать в обоих местах.
- [ ] **Step 4: Run** `uv run pytest tests/test_lite_failure_log.py tests/test_lite_output.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(dfl): запись сбоя и снимок прогонов (lite/failure_log)`.

### Task 2: Дублирование вывода (`lite/tee.py`)

**Files:**
- Create: `lite/tee.py`
- Test: `tests/test_lite_tee.py`

**Interfaces:**
- Produces: `@contextmanager def tee_output(max_lines: int = 200) -> Iterator[OutputTail]`; `class OutputTail: def lines(self) -> list[str]`.
- Поведение: на входе `sys.stdout/stderr.flush()`, `os.dup` дескрипторов 1 и 2, для каждого `os.pipe` + `os.dup2` в 1/2 и поток-читатель, который пишет байты в сохранённый дескриптор и складывает строки в общий `deque(maxlen=max_lines)` под замком. На выходе (и при исключении) — flush, восстановить 1/2, закрыть концы pipe, `join` потоков, затем `lines()` полон. Декодирование `utf-8`, `errors="replace"`.

- [ ] **Step 1: Write the failing tests** (через `capfd`)

```python
def test_tee_keeps_screen_and_collects_python_and_child(capfd):
    with tee_output() as tail:
        print("py out"); print("py err", file=sys.stderr)
        subprocess.run(["sh", "-c", "echo child out; echo child err >&2"], check=True)
    out, err = capfd.readouterr()
    assert "py out" in out and "child out" in out
    assert "py err" in err and "child err" in err
    assert {"py out", "py err", "child out", "child err"} <= set(tail.lines())

def test_tee_keeps_last_n_lines(capfd): ...      # 250 строк, max_lines=200 -> последние 200
def test_tee_restores_fds_on_exception(capfd): ...  # raise внутри; после — print виден capfd, tail заполнен
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_tee.py -q` — FAIL.
- [ ] **Step 3: Implement** `lite/tee.py`.
- [ ] **Step 4: Run** — PASS (на macOS; на Linux — тем же CI).
- [ ] **Step 5: Commit** `feat(dfl): дублирование вывода на уровне дескрипторов (lite/tee)`.

### Task 3: Обёртка `main` — запись сбоя и подсказка

**Files:**
- Modify: `lite/dfl.py` (`main`, `__main__`), `lite/init_repo.py` (только если нужен экспорт `InitError` — уже есть)
- Test: `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Task 1 (`snapshot_runs`, `changed_runs`, `cause_from`, `write`, `FailureRecord`), Task 2 (`tee_output`, `OutputTail`).
- Produces:
  - `REPORT_HINT = "отчёт разработчикам: dfl report"`
  - `def cli(argv: list[str] | None = None) -> int` — `with tee_output() as tail: return main(argv, tail=tail.lines)`; `__main__` зовёт `cli()`.
  - `def main(argv: list[str] | None = None, *, tail: Callable[[], list[str]] = lambda: []) -> int` — разбор аргументов; `report` (Task 5) — до обёртки; для прочих: `before = snapshot_runs()`, тело как сейчас в `try`; при ненулевом коде или исключении — `write(cwd, FailureRecord(...))`, печать `REPORT_HINT` в stderr; исключение-не-отказ пробрасывается после записи (код и traceback прежние). Сбой записи сбоя (`OSError`) не меняет код и не печатает traceback — одна строка в stderr «запись сбоя не удалась: …».
  - `failure_log.REFUSALS` — кортеж отказов `dfl`.
  - `argv` в записи: значение после `--file` заменено на имя файла.
  - `cwd_repo` — slug `origin` или `""`.

- [ ] **Step 1: Write the failing tests** (фикстура `env`, `XDG_STATE_HOME` в tmp)

```python
def test_refusal_writes_failure_record_and_hint(env, capsys):
    assert env.run("run") == 2                          # первый запуск без --subject
    rec = failure_log.load(env.tmp / "target")
    assert rec.command == "run" and rec.exit_code == 2
    assert rec.cause.startswith("незаконченного прогона нет")
    assert capsys.readouterr().err.rstrip().endswith(dfl.REPORT_HINT)

def test_success_keeps_previous_record(env): ...         # сбой, потом успешный status -> запись прежняя
def test_failure_before_registration_is_recorded(env, monkeypatch): ...  # mode strict -> запись есть
def test_unhandled_exception_recorded_and_reraised(env, monkeypatch): ...
    # подменить dfl.status -> raise RuntimeError("boom"); pytest.raises(RuntimeError); rec.cause == "RuntimeError: boom"
def test_file_argument_reduced_to_name(env): ...          # answer --file /abs/x/answer.yaml -> "answer.yaml" в argv
def test_record_write_failure_keeps_exit_code(env, monkeypatch): ...
def test_cli_tail_contains_screen_output(env, capfd): ...  # dfl.cli([...]) -> rec.output_tail содержит строку отказа
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_dfl.py -q` — новые FAIL.
- [ ] **Step 3: Implement** в `lite/dfl.py`.
- [ ] **Step 4: Run** `uv run pytest tests/test_lite_dfl.py tests/test_lite_init_repo.py tests/test_lite_install.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(dfl): запись сбоя каждого вызова и подсказка dfl report`.

### Task 4: Сбор отчёта (`lite/report.py`, без отправки)

**Files:**
- Create: `lite/report.py`
- Test: `tests/test_lite_report.py`

**Interfaces:**
- Consumes: Task 1 (`FailureRecord`, `load`).
- Produces:
  - `REPORT_REPO = "andrei-shtanakov/devtools"`, `BODY_LIMIT = 65_536`
  - `fingerprint(rec: FailureRecord, last_op: str) -> str` — 12 hex `sha256` от `"\n".join([rec.command, last_op or "dfl", normalize(rec.cause)])`
  - `normalize(text: str) -> str` — заменить на плейсхолдеры: URL → `<url>`, `'…'`/`"…"` → `<q>`, абсолютные пути и `~/…` → `<path>`, hex ≥7 → `<sha>`, `#\d+` и числа → `<n>`
  - `redact(text: str, home: Path) -> str` — `home` → `~`; токены `gh[pousr]_[A-Za-z0-9]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, `sk-ant-[A-Za-z0-9_-]{10,}`, `Bearer\s+\S+`, `xox[abp]-[A-Za-z0-9-]+`, `(?i)(token|secret|password)\s*[=:]\s*\S+` → `<скрыто>` (для последнего — значение)
  - `@dataclass(frozen=True) class Report: title: str; body: str; fp: str`
  - `build(rec: FailureRecord, facts: Facts) -> Report`; `@dataclass(frozen=True) class Facts: runs: list[dict]; versions: dict[str, str]; config: str` — `collect_facts(rec: FailureRecord) -> Facts` собирает реальные (прогоны через `rs.load`, `stop-reason.txt`; версии: `git -C <devtools> rev-parse HEAD`, пины `lite/components.toml` и HEAD клонов, `claude/gh/uv/git --version`, `platform.platform()`, `platform.machine()`, `sys.version`; конфиг: `mode` + `accounts.human/agent` или `"конфиг не читается: <причина>"`); недоступная версия — `"нет (<причина>)"`, не исключение.
  - Тело — Markdown: `## Где остановилось`, `## Вывод` (блок кода), `## Версии`, `## Конфиг`, последняя строка `fp <12hex>`. `redact` — ко всему телу и заголовку. Превышение `BODY_LIMIT` — режется начало хвоста вывода, строка `… (обрезано N строк)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fingerprint_stable_across_sha_path_number_quote(): ...
def test_fingerprint_differs_by_cause_and_command(): ...
@pytest.mark.parametrize("token", ["ghp_" + "a"*36, "gho_" + "b"*36, "github_pat_" + "c"*40,
    "sk-ant-api03-xyz123abc", "Bearer abc.def", "xoxb-123-abc", "TOKEN=abc123"])
def test_redact_hides_token(token): assert token not in redact(f"x {token} y", Path("/h"))
def test_redact_home_to_tilde(): ...
def test_build_title_and_sections(): ...   # title format, все 4 раздела, fp последней строкой
def test_build_never_includes_transcripts(runs_root): ...  # файл author-transcripts/… с маркером -> маркера нет в body
def test_build_truncates_to_body_limit(): ...  # tail из 200 строк по 1000 символов -> len(body) <= BODY_LIMIT, есть "обрезано"
def test_collect_facts_broken_config(monkeypatch): ...  # df_config.load -> ConfigError -> config.startswith("конфиг не читается")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_report.py -q` — FAIL.
- [ ] **Step 3: Implement** `lite/report.py` (сбор и рендер).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(dfl): сбор отчёта — очистка, отпечаток, состав (lite/report)`.

### Task 5: `dfl report` — показ, подтверждение, отправка; памятка

**Files:**
- Modify: `lite/report.py`, `lite/dfl.py` (`_parser`, `main`), `docs/darkfactory-for-colleagues.md`
- Test: `tests/test_lite_report.py`, `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Task 3 (`main`), Task 4 (`build`, `collect_facts`, `Report`).
- Produces:
  - `Gh = Callable[[list[str], Path | None], subprocess.CompletedProcess]` (argv, `GH_CONFIG_DIR` или `None`); `real_gh`.
  - `send(rep: Report, gh: Gh, profile: Path | None) -> str` — `gh issue list -R REPORT_REPO --state open --search "fp <fp> in:title" --json number,url,title`; совпадение по подстроке `(fp <fp>)` в `title` → `gh issue comment <n> -R … --body-file -`, вернуть URL комментария; иначе `gh issue create -R … --title … --body-file -` → URL issue. Ошибка `gh` → `ReportError` с причиной и «— используйте dfl report --print».
  - `run_report(cwd: Path, *, print_only: bool, gh: Gh = real_gh, ask: Callable[[str], str] = input, is_tty: Callable[[], bool] = sys.stdin.isatty) -> int`: нет записи → `записанного сбоя нет`, 0; печать отчёта (заголовок + тело); `print_only` → 0, без `gh`; не tty → отказ (код 2) «подтвердите отправку в терминале или используйте --print»; вопрос — `Отправить в andrei-shtanakov/devtools как публичное issue? [y/N] ` либо при найденном дубле `Добавить комментарий к <url>? [y/N] ` (поиск дубля — до вопроса); только `y`/`Y`/`да` отправляют; отказ коллеги → «не отправлено», 0; успех → печать URL, 0.
  - профиль: `df_config.load().accounts.human_gh_config_dir`, при `ConfigError` — `None`.
  - `_parser`: подкоманда `report` с `--print` (`dest="print_only"`); `main` обрабатывает `report` до `_require_test_mode` и до обёртки записи сбоя; отказы `report` — через `refusal`, без `REPORT_HINT`.
- Памятка: раздел `## Если что-то сломалось` (после «Обновление и остановки»): `dfl report` показывает отчёт и спрашивает; уходит публичным issue в devtools от вашей учётки; что в нём есть и чего нет; `--print` — только показать.

- [ ] **Step 1: Write the failing tests** (подменённый `gh` пишет вызовы в список)

```python
def test_report_without_record_says_none(env, capsys): ...           # код 0, gh не вызывался
def test_report_print_only_never_calls_gh(env_with_failure, capsys): ...
def test_report_no_tty_refuses_without_sending(env_with_failure): ...  # код 2, gh не вызывался
def test_report_confirm_creates_issue(env_with_failure, capsys): ...  # ask -> "y"; argv содержит issue create -R andrei-shtanakov/devtools; URL напечатан
def test_report_duplicate_goes_to_comment(env_with_failure): ...     # list вернул заголовок с (fp X) -> issue comment, не create
def test_report_decline_sends_nothing(env_with_failure): ...         # ask -> "" -> код 0, только list
def test_report_uses_human_profile_or_default(env_with_failure, monkeypatch): ...  # GH_CONFIG_DIR == human_gh_config_dir; ConfigError -> None
def test_report_works_with_broken_config(env_with_failure, monkeypatch): ...  # mode strict / битый конфиг -> отчёт печатается
def test_report_gh_failure_suggests_print(env_with_failure): ...     # gh rc=1 -> код 2, "dfl report --print" в stderr
def test_report_token_not_in_sent_body(env_with_failure): ...        # tail с ghp_… -> тело, ушедшее в gh, без токена (и в comment)
def test_report_does_not_overwrite_record(env_with_failure): ...     # отказ самого report не пишет запись сбоя
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_report.py tests/test_lite_dfl.py -q` — FAIL.
- [ ] **Step 3: Implement** отправку, подкоманду, раздел памятки.
- [ ] **Step 4: Run** `uv run pytest tests/test_lite_*.py tests/test_df_config.py -q` и `uv run ruff check lite tests && uv run ruff format --check lite tests` — PASS.
- [ ] **Step 5: Commit** `feat(dfl): dfl report — показ, подтверждение, issue или комментарий в devtools`.

### Task 6: Живая проверка (после ревью PR, до мержа)

- [ ] На сервере (`ssh user@homelab 'bash -lc "…"'`, учётка `betta-tester`) — ветка PR в `~/darkfactory/devtools`, `install.sh`.
- [ ] В репо без прогонов: `dfl run` → отказ, последняя строка `отчёт разработчикам: dfl report`.
- [ ] `dfl report --print` — отчёт без токенов, путей `/home/…` нет.
- [ ] Интерактивно (`ssh -t`) `dfl report` → `y` → issue в devtools; повтор `dfl run` + `dfl report` → комментарий к тому же issue.
- [ ] Ссылки на issue и комментарий — в тело PR; issue закрыть вручную «проверка #<PR>».
- [ ] Вернуть сервер на master (`git switch master`, `install.sh`).
