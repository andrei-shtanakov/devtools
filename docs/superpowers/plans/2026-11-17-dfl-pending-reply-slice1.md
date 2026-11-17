# dfl: вопрос наружу / ответ внутрь — срез 1 (протокол, пауза «дальше», ручные проверки) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dfl` без терминала не упирается в «запустите в терминале» на паузе просмотра и ручных проверках: записывает вопрос, останавливается кодом 0, принимает ответ командой `dfl reply` и продолжает на следующем `dfl run`.

**Architecture:** Новый модуль `lite/pending.py` — объект вопроса, файл ожидающего вопроса и записанных ответов в каталоге прогона, проверка ответа по виду. Места ожидания получают «спрашивающего» (`Ask = Callable[[Question], str | None]`): в терминале — как сейчас, без терминала — `pending.asker(run_dir)` (есть записанный ответ → он; нет → вопрос записан, `None`). Команды `dfl pending` / `dfl reply` — тонкие обёртки.

**Tech Stack:** Python 3.12, stdlib (`json`, `hashlib`, `dataclasses`), pytest; запуск `uv run --frozen --group governance pytest`.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-skill-desktop-design.md` (§5.1, §5.5, §6 п.1). Срезы 2–5 — отдельные планы.

## Global Constraints

- Терминальное поведение не меняется: существующие тесты экрана/диалога (`tests/test_lite_review_pause.py`, `tests/test_lite_manual_ask.py`, `tests/test_lite_personal_review.py`, `tests/test_lite_dfl_owner_review.py`, `tests/test_lite_dfl_review.py`, `tests/test_lite_dfl_personal.py`) проходят **без правок**.
- Ожидание человека — код 0, не сбой; отказ `dfl reply` — код 2 через `DflError` (как прочие отказы `dfl`).
- Все тексты для человека — по-русски, в стиле соседних констант.
- Терминал побеждает: в терминале вопрос задаётся вживую, ожидающий вопрос снимается. Уже записанный (`dfl reply`) ответ — ответ человека: его берут и в терминале.
- Повторных платных вызовов на продолжении нет (в срезе 1 мест с агентом до вопроса нет — проверяется в срезе 2).
- `ruff check`, `ruff format --check` (`uv run --frozen --group selfcheck`) и полный `pytest` зелёные.

## Review Focus

- `dfl reply` с id прежнего вопроса (вопрос сменился, пока человек думал) — отказ «вопрос уже другой: dfl pending», ответ не записан. → Task 1, Task 2.
- Ответ записан (`dfl reply`), а следующий `dfl run` запущен в терминале — берётся записанный ответ, второй раз не спрашивают. → Task 3.
- Ручная проверка «с оговоркой» без текста оговорки — отказ `reply`; «не выполнено» без заметки — принято. → Task 1.
- Замечания словами на паузе просмотра без терминала (в срезе 1 не поддержаны) — `reply` отказывает с подсказкой «замечания — в терминале: dfl run», «дальше» принимается. → Task 1, Task 4.
- Личный и enterprise прогон у одного репо — `pending`/`reply` берут тот же прогон, что выберет `dfl run` (личный важнее, RF-4). → Task 2.

---

### Task 1: `lite/pending.py` — вопрос, хранение, проверка ответа

**Files:**
- Create: `lite/pending.py`
- Test: `tests/test_lite_pending.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Question: kind: str; key: str; text: str; options: tuple[str, ...] = (); links: tuple[str, ...] = (); accepts: str = ""` и свойство `id -> str` = `f"{kind}:{key}:{sha256(text)[:8]}"`.
  - `REVIEW = "review"`, `MANUAL = "manual"` — виды среза 1.
  - `PENDING_FILE = "pending.json"`, `REPLIES_FILE = "replies.json"`.
  - `Ask = Callable[[Question], str | None]`.
  - `def asker(run_dir: Path) -> Ask` — есть ответ на `q.id` в `replies.json` → удалить его оттуда, снять `pending.json`, вернуть текст; нет → записать `pending.json` (`as_dict(q)` + `run_dir`-независимые поля + `"at"` ISO-время), вернуть `None`.
  - `def load(run_dir: Path) -> dict | None` — ожидающий вопрос или `None` (битый файл = `None`).
  - `def clear(run_dir: Path) -> None`.
  - `def check(kind: str, text: str) -> str | None` — причина отказа или `None`.
  - `def record(run_dir: Path, qid: str, text: str) -> None` — отказ `PendingError` если `load()` пуст или его id ≠ `qid`, или `check()` вернул причину; иначе ответ в `replies.json`.
  - `class PendingError(Exception)`.
  - `def wait_line(q: Question) -> str` — `"ждёт ответа: {первая строка text} — вопрос: dfl pending; ответ: dfl reply --id {q.id} --text '…'"`.
  - `def parse_manual(text: str) -> dict` — `{"verdict", "note"}` по словам `manual_ask._WORDS` (вердикт — до первого `:`, заметка — после).
- Consumes: `governance.manual_checks` (`DONE`, `NOT_DONE`, `CAVEAT`), слова вердиктов из `lite.manual_ask` (вынести `_WORDS` в публичное `VERDICT_WORDS` там же, `manual_ask` ссылается на него).

- [ ] **Step 1: Failing tests**

```python
def test_question_id_stable_and_content_bound():
    a = pending.Question(pending.REVIEW, "design", "этап «дизайн» готов")
    assert a.id == pending.Question(pending.REVIEW, "design", "этап «дизайн» готов").id
    assert a.id != pending.Question(pending.REVIEW, "design", "другой текст").id
    assert a.id.startswith("review:design:")

def test_asker_writes_pending_then_returns_recorded_reply(tmp_path):
    q = pending.Question(pending.REVIEW, "design", "посмотрите")
    ask = pending.asker(tmp_path)
    assert ask(q) is None
    assert pending.load(tmp_path)["id"] == q.id
    pending.record(tmp_path, q.id, "дальше")
    assert ask(q) == "дальше"
    assert pending.load(tmp_path) is None
    assert ask(q) is None  # ответ израсходован — вопрос снова записан

def test_record_refuses_stale_id(tmp_path):
    pending.asker(tmp_path)(pending.Question(pending.REVIEW, "a", "x"))
    with pytest.raises(pending.PendingError, match="вопрос уже другой"):
        pending.record(tmp_path, "review:a:00000000", "дальше")

def test_record_refuses_without_pending(tmp_path):
    with pytest.raises(pending.PendingError, match="нет вопроса"):
        pending.record(tmp_path, "review:a:00000000", "дальше")

def test_check_review_accepts_only_done_words_in_slice1():
    assert pending.check(pending.REVIEW, "дальше") is None
    assert "в терминале" in pending.check(pending.REVIEW, "поправь требования")

def test_check_manual():
    assert pending.check(pending.MANUAL, "1") is None
    assert pending.check(pending.MANUAL, "не выполнено") is None
    assert pending.check(pending.MANUAL, "3") is not None  # оговорка без текста
    assert pending.check(pending.MANUAL, "с оговоркой: только в Chrome") is None
    assert pending.check(pending.MANUAL, "может быть") is not None
    assert pending.parse_manual("2: письмо не пришло") == {
        "verdict": mc.NOT_DONE, "note": "письмо не пришло"}
```

- [ ] **Step 2:** `uv run --frozen --group governance pytest tests/test_lite_pending.py -q` → FAIL (нет модуля).
- [ ] **Step 3:** Реализовать `lite/pending.py` по Interfaces. Запись файлов — через временный файл и `os.replace`. Слова «дальше» для review — `review_pause.DONE_WORDS`. Тексты отказов: `"нет вопроса, ждущего ответа — dfl pending"`, `"вопрос уже другой — dfl pending"`, review-замечания: `"замечания к этапу пока принимаются только в терминале: dfl run; без замечаний — «дальше»"`, manual: `"ответ: 1/выполнено, 2/не выполнено[: заметка], 3/с оговоркой: <текст>"`.
- [ ] **Step 4:** тот же pytest → PASS; `tests/test_lite_manual_ask.py` → PASS без правок.
- [ ] **Step 5:** Commit `feat(dfl): lite/pending — вопрос наружу / ответ внутрь (#766)`.

### Task 2: команды `dfl pending` и `dfl reply`

**Files:**
- Modify: `lite/dfl.py` (`_parser`, диспетчер подкоманд ~строка 446, новые `pending_cmd`, `reply_cmd`, `_pending_dir`)
- Test: `tests/test_lite_dfl_pending.py`

**Interfaces:**
- Consumes: Task 1 (`load`, `record`, `PendingError`).
- Produces:
  - `def _pending_dir(cwd: Path, subject: str | None) -> Path | None` — каталог прогона, который продолжит `dfl run`: активный личный (`personal_state.active_for(slug, subject=subject)` → `personal_state.run_dir(run_id)`), иначе enterprise `_current_run(repo, subject)` → `rs.run_dir(run_id)`; engineer-прогон с открытыми ручными проверками — его каталог (как выбирает `_engineer_step`).
  - `def pending_cmd(cwd: Path, subject: str | None, *, as_json: bool) -> int` — нет вопроса: «вопросов нет» (json: `null`), код 0; есть: текст, варианты, ссылки, что принимается, строка `dfl reply --id …` (json: запись `pending.json` как есть), код 0.
  - `def reply_cmd(cwd: Path, subject: str | None, qid: str, text: str) -> int` — под `run_lock.run_lock(run_id)`; `PendingError` → `DflError`; успех: «ответ записан — продолжить: dfl run», код 0.
  - CLI: `dfl pending [--json] [--subject S]`; `dfl reply --id ID (--text T | --file F) [--subject S]`. `pending` и `reply` — в `LOGGED_COMMANDS` не добавлять.

- [ ] **Step 1: Failing tests** (фикстуры прогонов — как в `tests/test_lite_dfl_owner_review.py` / `test_lite_dfl_personal.py`):
  - `test_pending_none` — нет вопроса → вывод «вопросов нет», код 0; `--json` → `null`.
  - `test_pending_shows_question_and_reply_hint` — записанный `Question(MANUAL, "BEH-01", "…")` → вывод содержит текст и `dfl reply --id manual:BEH-01:`.
  - `test_reply_records_and_hints_run` — код 0, `replies.json` содержит ответ, вывод «продолжить: dfl run».
  - `test_reply_stale_id_refused` — код 2, `replies.json` не создан.
  - `test_pending_prefers_personal_run` — у репо есть и личный, и enterprise прогон с вопросами → показан вопрос личного.
  - `test_reply_text_from_file` — `--file` с многострочным текстом.
- [ ] **Step 2:** pytest → FAIL.
- [ ] **Step 3:** Реализовать по Interfaces.
- [ ] **Step 4:** `pytest tests/test_lite_dfl_pending.py tests/test_lite_dfl.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(dfl): команды pending и reply (#766)`.

### Task 3: ручные проверки без терминала

**Files:**
- Modify: `lite/manual_ask.py` (`settle`, `is_wait`)
- Modify: `lite/dfl.py:1541` (`_manual_step`), `lite/personal/flow.py:~998` (вызов `ma.settle`)
- Test: `tests/test_lite_manual_ask_remote.py`

**Interfaces:**
- Consumes: Task 1 (`Question`, `MANUAL`, `asker`, `parse_manual`, `wait_line`, `clear`).
- Produces: `settle(..., tty: bool, read: Read, ask: pending.Ask | None = None, ...)` — `tty` → как сейчас, но перед первым вопросом: записанный ответ на `Question(MANUAL, beh_id, mc.question(check))` (если есть) берётся вместо чтения, `pending.clear`; не `tty` и `ask` задан → по каждой неотвеченной проверке `ask(q)`: `None` → вернуть `pending.wait_line(q)`; текст → `parse_manual`, сохранить как терминальный ответ (`at`, `issue=None`), `save()`; не `tty` и `ask is None` → прежний `NO_TTY`. `is_wait` признаёт и строку `wait_line` (префикс `"ждёт ответа: "`).
  - Вызывающие передают `ask=pending.asker(<каталог прогона>)` (enterprise — `rs.run_dir(state.run_id)`, личный — `run_dir(run.run_id)`).

- [ ] **Step 1: Failing tests:**
  - `test_remote_first_call_writes_question_and_waits` — 2 проверки, не tty → причина начинается «ждёт ответа:», `is_wait` → True, `pending.load` — первая проверка, `answers` пусто.
  - `test_remote_reply_then_next_question` — `record(… "1")` → второй `settle`: первая отвечена `DONE`, ждёт вторую.
  - `test_remote_all_answered_publishes` — после двух ответов `settle` → `None`, фейк `ops` получил публикацию ровно один раз.
  - `test_recorded_reply_used_in_terminal` — записанный ответ, `tty=True`, `read` падает при вызове на первой проверке (`pytest.fail`) и отвечает «1» на второй → обе отвечены.
  - Регрессия: `tests/test_lite_manual_ask.py`, `tests/test_lite_dfl_manual*.py`, `tests/test_lite_personal_*` — без правок.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS + регрессия.
- [ ] **Step 5:** Commit `feat(dfl): ручные проверки без терминала через dfl reply (#766)`.

### Task 4: пауза просмотра «дальше» без терминала (оба пути)

**Files:**
- Modify: `lite/review_pause.py` (новая `def ask_remote(stage: str, stage_label: str, paths: list[Path], *, ask: pending.Ask) -> str | None`)
- Modify: `lite/personal/flow.py:_review` (ветка `not ctx.tty()`)
- Modify: `lite/dfl.py` (`run` ветка без терминала; `_owner_node`, `_owner_pause`, `_owner_review_rounds` — параметр `ask: pending.Ask | None = None`)
- Test: `tests/test_lite_review_pause_remote.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `ask_remote` — вопрос `Question(REVIEW, stage, f"этап «{label}» готов и прошёл проверку — посмотрите", links=tuple(file://…))`, `accepts="«дальше»"`; `ask` вернул `None` → `None`; иначе `""` (ответ «дальше» — только такой и пропустит `check`).
  - Личный `_review`: без терминала вместо `NO_TTY` — `ask_remote(stage, label, paths, ask=pending.asker(rd))`; `None` → `ctx.run.stop_reason = wait_line(q)` (без `ctx.stopped`: код 0), `return False`; `""` → та же ветка, что «дальше» в терминале (бриф: сверка с `brief.json`; узел: `_edited_on_pause`).
  - Enterprise: `run()` без терминала при `_owner_review_run(...)` не `None` → `_owner_review_rounds(target, subject, role, code, ask=pending.asker(rs.run_dir(state.run_id)))`; в `_owner_node` при `ask` задан и `batch` пуст — `rp.ask_remote` вместо `rp.ask`; `None` → печать `wait_line`, код 0. Недоигранные замечания (`batch` не пуст) без терминала — прежняя подсказка `orv.NO_TTY` (правка — срез 2).
  - Терминал: перед `rp.ask` записанный ответ (если есть) используется, `pending.clear(rd)`.

- [ ] **Step 1: Failing tests:**
  - `test_personal_pause_remote_waits_then_continues` — личный прогон на паузе `design`, `tty=False`: `advance` → код 0, `stop_reason` начинается «ждёт ответа:»; `record(… "дальше")`; `advance` → шаг прошёл паузу.
  - `test_enterprise_pause_remote_waits_then_continues` — прогон `WAITING_OWNER_REVIEW`, `dfl run` без терминала → код 0, `pending.json` с видом `review`; `reply` «дальше» → следующий `dfl run` пишет op паузы (`runner.review_pause_passed`).
  - `test_pause_remote_w4_two_nodes` — W4 (design + acceptance): два вопроса подряд, по одному на вызов.
  - `test_pause_manual_edit_then_done_rechecks` — личный: файл правлен руками после показа, «дальше» через reply → перепроверка, как в терминале.
  - Регрессия: существующие файлы тестов паузы — без правок.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS + регрессия.
- [ ] **Step 5:** Commit `feat(dfl): пауза просмотра «дальше» без терминала (#766)`.

### Task 5: `dfl status` и памятка

**Files:**
- Modify: `lite/dfl.py` (`status`: строка «ждёт ответа: … — dfl pending» на уровне 0, если у прогона есть `pending.json`)
- Modify: `docs/darkfactory-for-colleagues.md` (раздел про работу без терминала: `dfl pending`, `dfl reply`, что поддержано в срезе 1)
- Test: `tests/test_lite_dfl_pending.py` (добавить), `tests/test_lite_docs.py` (если там пинится перечень команд — дополнить)

- [ ] **Step 1: Failing test** `test_status_shows_pending` — у прогона ожидающий вопрос → `dfl status` содержит «ждёт ответа:» и «dfl pending».
- [ ] **Step 2:** FAIL. **Step 3:** реализовать, обновить памятку. **Step 4:** полный `uv run --frozen --group governance pytest -q` → PASS; `uv run --frozen --group selfcheck ruff check .` и `ruff format --check .` → чисто.
- [ ] **Step 5:** Commit `docs(dfl): pending/reply в status и памятке (#766)`.
