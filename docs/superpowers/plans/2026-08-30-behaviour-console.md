# Behaviour Console (этап B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Textual-консоль поверх runner'а этапа B1 (обзор прогонов, деталь run'а,
candidate-срез бандла, запуск resume/verify в tmux), disp-бэкенд авторинга opt-in
и follow-ups приёмки B1.

**Architecture:** `governance/console_model.py` — чистые view-model функции
(read-only чтение RUNS_ROOT + bundle_state); `governance/console.py` — textual-TUI
тонким слоем + non-TTY plain/JSON деградация (правило `tui.md`); действия не
исполняются в процессе TUI — tmux-сессия с `make behaviour-run` (паттерн
issue_console). Авторинг получает переключаемый бэкенд `codex|disp`.

**Tech Stack:** Python ≥3.12; uv-группа `governance` + новая зависимость
`textual` (только в группе); disp — вызовом `uv run --project ../disputatio`.

**Spec:** `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md`
(v4 GO, §4 console.py, §5 S3-disp, OQ-1). Факт против спеки: у disp НЕТ
`--mode document` (есть `run --mode {develop,analyze}`, `pipeline`) — B2 использует
`run --mode develop`, расхождение уходит inbox-issue в disputatio (OQ-1).

## Global Constraints

- Ветка `feat/behaviour-console`; трейлер `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Консоль — read-only к прогонам и репо: НИКАКИХ мутаций из процесса TUI;
  единственные эффекты — tmux-сессии (паттерн issue_console).
- textual — только в группе `governance`; тестируется модель, не пиксели;
  non-TTY → plain-вывод, `NO_COLOR` уважается, `--json` для машин (tui.md).
- Fail-closed сохраняется: битый run.json в списке показывается как
  `corrupt`, не скрывается и не роняет консоль.
- Тесты: `uv run --frozen --group governance pytest tests/test_governance_*.py -q`;
  line length ≤ 88 (len() по символам).

---

### Task 1: Follow-ups приёмки B1

**Files:**
- Modify: `governance/runner.py`
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Produces: исправленный `start()` (валидация ДО резервации); evidence-подсказка
  в стоп-комментарии S6 exit 1.

- [ ] Валидация `merge_authority` ПЕРЕД `_reserve_run_id` в `start()` (сейчас
  невалидное значение навсегда резервирует run_id пустым файлом — minor из
  приёмки #88). Тест: `start(..., merge_authority="agent")` → ValueError И
  run.json НЕ создан.
- [ ] Стоп-комментарий S6 при exit 1 дополняется evidence-подсказкой класса
  «файлов нет» (спека §7): «Известный ложный класс „файлов нет“ опровергается
  `git cat-file -e <head>:<путь>`; авто-перегон появится после машинного типа
  находки в ките steward». Head подставляется реальный. Тест: тело комментария
  содержит `git cat-file -e <head>`.
- [ ] TDD, прогон, коммит `fix(governance): follow-ups приёмки B1 — валидация до резервации, evidence-подсказка` (+трейлер).

---

### Task 2: Авторинг-бэкенд `codex|disp`

**Files:**
- Modify: `governance/ops.py` (новый метод), `governance/runner.py`,
  `governance/run_state.py` (поле `author_backend: str = "codex"`)
- Test: `tests/test_governance_ops.py`, `tests/test_governance_runner.py`

**Interfaces:**
- Produces: `Ops.author_disp(target_dir: str, task: str) -> int` — RealOps:
  `["uv", "run", "--project", str(DEVTOOLS_ROOT.parent / "disputatio"), "disp",
  "run", "--mode", "develop", "--root", target_dir, task]`, возврат rc as-is;
  `RunState.author_backend` (`"codex"|"disp"`, валидация в new_run); CLI
  `start --author-backend`; `_step_authoring` для behaviour-spec узла при
  `disp`-бэкенде зовёт `author_disp` с task-текстом (subject + путь бандла +
  требование DSL: `#### BEH-NN`, `traces:`, `- **checked_by**:`), charter/
  requirements всегда codex (disp-цикл осмыслен для полируемого документа).
- [ ] Комментарий в `_step_authoring`: «спека §5 называет `disp --mode document`
  — такого режима у disp нет (факт 2026-08-30), используем `run --mode develop`;
  выравнивание — inbox-issue disputatio (OQ-1)».
- [ ] Тесты: argv author_disp (включая --project путь); runner с backend=disp —
  author_disp вызван только для behaviour-узла; default codex не изменился;
  new_run отвергает неизвестный backend.
- [ ] TDD, прогон, коммит `feat(governance): авторинг-бэкенд codex|disp (disp run --mode develop, opt-in)` (+трейлер).

---

### Task 3: `console_model.py` — view-model

**Files:**
- Create: `governance/console_model.py`
- Test: `tests/test_governance_console_model.py`

**Interfaces:**
- Produces (console и тесты B2 полагаются дословно):

```python
@dataclass(frozen=True)
class RunRow:
    run_id: str; ws_id: str; repo: str; status: str
    step: str          # первый не-completed op-ключ либо "—"
    pr: int | None; remediated_by: str | None

@dataclass(frozen=True)
class RunDetail:
    row: RunRow
    ops: tuple[tuple[str, str], ...]     # (key, status) в порядке пайплайна
    findings: str                        # gate-findings.txt + s8-findings.txt (что есть)
    verdict_reason: str | None           # из op verdict, если был

def list_runs() -> tuple[RunRow, ...]        # RUNS_ROOT; битый run.json -> status="corrupt"
def run_detail(run_id: str) -> RunDetail
def bundle_summary(target_dir: str, profile: str, bundle_dir: str) -> tuple[tuple[str, str], ...]
    # (node_id, status) из candidate_state; ошибки чтения -> [("error", <msg>)], не exception
def rows_to_json(rows) -> str; detail_to_json(detail) -> str
```

- [ ] Тесты: список из 2 живых + 1 битого run.json (corrupt виден, не падает);
  detail с findings-файлами и без; bundle_summary на фикстурах governance_fixtures
  (importorskip steward) и на несуществующем пути (error-строка); step-вычисление.
- [ ] Реализация read-only (никаких ops/subprocess). TDD, прогон, коммит
  `feat(governance): console_model — read-only view-model прогонов и бандла` (+трейлер).

---

### Task 4: `console.py` — textual TUI + non-TTY

**Files:**
- Modify: `pyproject.toml` (textual в группу governance), `uv.lock`
- Create: `governance/console.py`
- Test: `tests/test_governance_console.py`

**Interfaces:**
- Produces: `python -m governance.console` — TUI: таблица прогонов
  (RunRow-колонки), Enter → деталь (ops-журнал, findings, verdict reason,
  bundle_summary), клавиши: `r` resume, `v` verify (запрос parent), `q` выход;
  действия — `tmux new-session -d -s beh-<run_id> -c <devtools> "make behaviour-run ARGS='resume --run-id <id>'; exec $SHELL"`,
  повторная сессия → подсказка `tmux attach -t =beh-<run_id>` (точный таргет,
  урок issue_console); non-TTY или `--json` → plain/JSON вывод list_runs без
  textual-импорта (ленивый import textual внутри TUI-ветки — plain-путь
  работает и без установленного textual).
- [ ] pyproject: `governance = ["steward", "textual>=1"]`; `uv lock`.
- [ ] Тесты (модель, не пиксели): `--json` путь (capsys, без TTY, textual не
  импортируется — monkeypatch sys.modules гардом); tmux-команда строится с
  `=`-таргетом и make-ARGS (перехват subprocess); ленивая загрузка textual
  (plain-путь при удалённом textual из sys.modules).
- [ ] TDD, прогон, коммит `feat(governance): behaviour-console — textual TUI поверх view-model` (+трейлер).

---

### Task 5: Обвязка и финал

**Files:**
- Modify: `Makefile` (`behaviour-console`), `README.md`, `TODO.md`

- [ ] `make behaviour-console: ; @uv run --frozen --group governance python -m governance.console $(ARGS)` + help.
- [ ] README: блок про консоль (клавиши, non-TTY, tmux-действия).
- [ ] TODO: `@id:behaviour-runner` → `[x]` (B1 PR #88 + B2 PR #<этот>);
  подпункт про inbox-issue disputatio (`disp --mode document` vs факт) — новой
  строкой `- [ ] inbox-issue в disputatio: режим полировки одиночного документа
  (OQ-1; спека называла --mode document) @owner:github:andrei-shtanakov @id:disp-document-mode-issue`.
- [ ] Финальные проверки: py_compile всех governance/*.py; полный
  `uv run --frozen --group governance pytest -q`; skip-гигиена
  (`uv sync --frozen` без группы → plain-путь консоли и тесты живы:
  `uv run --frozen pytest tests/test_governance_console_model.py tests/test_governance_console.py -q`
  → skip/pass без error; затем `uv sync --frozen --group governance`);
  line length len().
- [ ] Коммит `docs(governance): обвязка behaviour-console + закрытие @id:behaviour-runner` (+трейлер).
  Push/PR/приёмка/мерж — контролёр (DarkFactory).
