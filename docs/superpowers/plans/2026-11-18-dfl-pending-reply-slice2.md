# dfl: вопрос наружу / ответ внутрь — срез 2 (замечания владельца, разбор edge-check) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Без терминала `dfl` принимает замечания к этапу через `dfl reply`, правит этап, задаёт вопрос «Удалить D-02?» и вопросы разбора edge-check как ожидающие вопросы (`pending.json`, код 0), и продолжение на следующем `dfl run` не повторяет платную работу, сделанную до вопроса, а лимит кругов тот же, что в терминале.

**Architecture:** Протокол среза 1 (`lite/pending.py`: `Question`, `asker`, `take`, `record`, `check`) получает два новых вида вопроса и проверку ответа по записанному вопросу. Место 4 (`lite/owner_review.py`) сохраняет результат платной правки (кандидат) в каталоге прогона до вопроса и берёт его на продолжении. Место 5 (`lite/edge_resolve.py`) спрашивает через `Ask`, сортировка уже кэшируется (`cache_dir=run_dir`), решения уже пишутся в журнал сразу; номер круга и находки ожидающего круга сохраняются в каталоге прогона, чтобы продолжение не делало лишней платной проверки и не сбрасывало лимит.

**Tech Stack:** Python 3.12, stdlib, pytest; `uv run --frozen --group governance pytest`.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-skill-desktop-design.md` (§5.1, §5.6 п.5, §6 п.2, §7). Срез 1 — `docs/superpowers/plans/2026-11-17-dfl-pending-reply-slice1.md` (влит, #803/#804).

## Global Constraints

- Терминальное поведение не меняется: существующие тесты экрана/диалога (`tests/test_lite_review_pause.py`, `tests/test_lite_owner_review.py`, `tests/test_lite_edge_resolve.py`, `tests/test_lite_personal_review.py`, `tests/test_lite_dfl_owner_review.py`, `tests/test_lite_dfl_review.py`, `tests/test_lite_dfl_personal.py`, `tests/test_lite_personal_flow.py`) проходят **без правок**. Если тест падает — меняется код, не тест.
- Ожидание человека — код 0 и строка `pending.wait_line(q)`; отказ `dfl reply` — `PendingError` (код 2 через `DflError`, как в срезе 1).
- Терминал побеждает: в терминале сначала `pending.take(run_dir, q)` (записанный `dfl reply` — ответ человека), иначе живой ввод, после живого ответа `pending.discard(run_dir)`.
- Платные вызовы на продолжении: N циклов `dfl run` → `dfl reply` без терминала = столько же вызовов сортировки, переписывания и проверки, сколько один терминальный проход с теми же ответами (§7). Счётчики — `PaidCalls`/`ctx.calls` фейков.
- Кандидат правки и состояние ожидающего круга пишутся **только когда вопрос ушёл в `pending.json`**; чисто терминальный прогон их не создаёт (байтово прежнее поведение).
- Замечания к **брифу** личного пути без терминала по-прежнему не принимаются (`REVIEW_REFUSAL`) — это срез 4 (§5.6 п.1).
- Тексты по-русски, в стиле соседних констант; `ruff check`, `ruff format --check`, полный `pytest` зелёные.

## Review Focus

- Человек ответил на первый из двух вопросов «Удалить …?», второй ждёт — следующий `dfl run` не зовёт автора заново и не переспрашивает первый. → Task 2.
- Файл этапа поправлен руками, пока вопрос «Удалить …?» ждал ответа — кандидат не берётся (sha `before` другой), ожидающий вопрос снят, правка заново. → Task 2.
- `dfl reply` номером вне диапазона вариантов edge-вопроса или «не согласен:» без причины — отказ, ответ не записан. → Task 1.
- Ожидание на втором круге разбора: продолжение остаётся на втором круге (вариант «принять вопреки» в `test` предлагается), не начинает с первого. → Task 4.
- Узел поправлен руками, пока ждал edge-вопрос (личный путь) — сохранённые находки не берутся, проверка заново. → Task 4.

---

### Task 1: `lite/pending.py` — новые виды и проверка по записанному вопросу

**Files:**
- Modify: `lite/pending.py`
- Test: `tests/test_lite_pending.py` (только новые тесты; существующие — без правок)

**Interfaces:**
- Produces:
  - `REMARKS_CONFIRM = "remarks-confirm"`, `EDGE = "edge"`.
  - `def check(kind: str, text: str, rec: Mapping | None = None) -> str | None` — `rec` — записанный вопрос (`load()`); `record()` передаёт его.
    - `REVIEW`: слово «дальше» (`REVIEW_DONE_WORDS`) — принято; непустой текст — принят как замечания, **кроме** `rec["key"] == "brief"` → `REVIEW_REFUSAL`; пустой — `REVIEW_REFUSAL`.
    - `REMARKS_CONFIRM`: принято `{"1", "да", rec["options"][0]}` и `{"2", "нет", "оставить", "оставить как было"}` (регистр не важен); иначе `CONFIRM_REFUSAL = "ответ: 1/{yes} или 2/оставить"`.
    - `EDGE`: число `1..len(rec["options"])`; `«своё: …»`/`«не согласен: …»` с непустым остатком (префиксы и сравнение как `edge_resolve._starts`: регистр и «ё» не важны); иначе `EDGE_REFUSAL = "ответ: номер варианта 1–{n}, «своё: …» или «не согласен: <почему>»"`.
  - `def is_done(text: str) -> bool` — `text.strip().casefold() in REVIEW_DONE_WORDS`.
- Consumes: ничего нового.

- [ ] **Step 1: Failing tests**

```python
def _rec(kind, key="design", options=()):
    return {"kind": kind, "key": key, "options": list(options)}

def test_review_reply_accepts_remarks_except_brief():
    assert pending.check(pending.REVIEW, "убери D-02", _rec(pending.REVIEW)) is None
    assert pending.check(pending.REVIEW, "дальше", _rec(pending.REVIEW)) is None
    assert pending.check(pending.REVIEW, "убери", _rec(pending.REVIEW, "brief")) == pending.REVIEW_REFUSAL
    assert pending.check(pending.REVIEW, "  ", _rec(pending.REVIEW)) == pending.REVIEW_REFUSAL

def test_confirm_reply_words():
    rec = _rec(pending.REMARKS_CONFIRM, "design:D-02", ("удалить", "оставить"))
    for ok in ("1", "2", "Удалить", "да", "оставить как было"):
        assert pending.check(pending.REMARKS_CONFIRM, ok, rec) is None
    assert pending.check(pending.REMARKS_CONFIRM, "снять", rec) is not None  # слово чужого вопроса

def test_edge_reply_range_and_prefixes():
    rec = _rec(pending.EDGE, "a/b/R1", ("вариант А", "вариант Б"))
    assert pending.check(pending.EDGE, "2", rec) is None
    assert pending.check(pending.EDGE, "3", rec) is not None
    assert pending.check(pending.EDGE, "Своё: иначе", rec) is None
    assert pending.check(pending.EDGE, "не согласен:", rec) is not None
    assert pending.check(pending.EDGE, "пропустить", rec) is not None

def test_record_checks_against_recorded_question(tmp_path):
    q = pending.Question(pending.EDGE, "a/b/R1", "замечание", options=("А",))
    pending.asker(tmp_path)(q)
    with pytest.raises(pending.PendingError):
        pending.record(tmp_path, q.id, "2")
    pending.record(tmp_path, q.id, "1")
```

- [ ] **Step 2:** `uv run --frozen --group governance pytest tests/test_lite_pending.py -q` — FAIL (нет `EDGE`, `REMARKS_CONFIRM`).
- [ ] **Step 3:** Реализовать; `record()` зовёт `check(kind, text, current)`.
- [ ] **Step 4:** Тот же прогон и `tests/test_lite_dfl_pending.py` — PASS.
- [ ] **Step 5:** Commit `dfl pending: виды remarks-confirm и edge, замечания через reply (#766 срез 2)`.

---

### Task 2: `lite/owner_review.py` — кандидат правки и вопрос «Удалить …?» без терминала

**Files:**
- Modify: `lite/owner_review.py`, `lite/review_pause.py`
- Test: `tests/test_lite_owner_review_remote.py` (новый)

**Interfaces:**
- Consumes: Task 1 (`REMARKS_CONFIRM`, `Question`, `Ask`, `take`, `discard`, `wait_line`).
- Produces:
  - `WAITING = "waiting"` — новый исход `revise_by_owner`; подробность — `pending.wait_line(q)`.
  - `CANDIDATE_FILE = "owner-revise.json"`.
  - `def revise_by_owner(path, text, *, revise, read, ask: pending.Ask | None = None, store: Path | None = None) -> tuple[str, str]` — новые параметры только ключевые; при `ask is None and store is None` поведение байтово прежнее.
  - `review_pause.confirm_question(stage: str, item: str, kind: str, title: str) -> pending.Question` — `kind=REMARKS_CONFIRM`, `key=f"{stage}:{item}"`, `text` — та же строка `_QUESTIONS[kind][0].format(named=…)`, `options=(слово согласия, "оставить")`.
  - `review_pause.confirm_remote(items, *, stage, titles, kinds, ask, answers: dict[str, bool]) -> set[str] | None` — по каждому id без ответа в `answers` зовёт `ask`; ответ — в `answers` (вызывающий сохраняет), `None` — вопрос записан. Итог — разрешённые id, когда отвечены все.
- Кандидат (`store / CANDIDATE_FILE`): `{"path": path.name, "before": sha256(before), "decisions": text, "after": <текст после первой правки>, "answers": {id: bool}}`.
  - Пишется после первой правки, только если дальше нужен вопрос и `ask` задан (или в терминале при записанном `pending` — терминал продолжает кандидата).
  - Берётся, если `path`, `before` и `decisions` совпали: `after` записывается в файл, `revise` не зовётся.
  - Не совпал — удаляется, `pending.discard(store)` (вопрос о старой правке), правка заново.
  - Удаляется при любом окончательном исходе (`ACCEPTED`, `UNCLEAR`, `FAILED`); при `WAITING` файл этапа возвращается к `before`, кандидат остаётся.
- Повтор (второй вызов `revise` с `KEEP_NOTE`) — в том же вызове, где пришли все ответы; вопросов после него нет (как сейчас).

- [ ] **Step 1: Failing tests** (фейк `revise` считает вызовы и пишет заранее заданный текст)

```python
def test_remote_confirm_waits_and_keeps_candidate(tmp_path): ...
    # первый вызов: revise 1 раз, итог WAITING, файл = before,
    # pending.json вида remarks-confirm по первому id, owner-revise.json есть

def test_resume_takes_candidate_without_second_revise(tmp_path): ...
    # dfl reply "1" на первый id → второй вызов: revise не зван,
    # вопрос по второму id; reply "2" → третий вызов: повтор с KEEP_NOTE
    # (revise.calls == 2 всего), ACCEPTED, кандидата нет

def test_candidate_dropped_when_file_edited(tmp_path): ...
    # файл поправлен руками между вызовами → кандидат и pending сняты,
    # revise зовётся заново

def test_terminal_takes_recorded_confirm(tmp_path): ...
    # pending записан, reply "1", вызов с ask=None, read без строк →
    # ответ взят из replies.json, ввод не читался

def test_without_store_behaviour_unchanged(tmp_path): ...
    # ask=None, store=None → owner-revise.json не создаётся
```

- [ ] **Step 2:** `uv run --frozen --group governance pytest tests/test_lite_owner_review_remote.py tests/test_lite_owner_review.py tests/test_lite_review_pause.py -q` — новые FAIL, старые PASS.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** Тот же прогон — PASS.
- [ ] **Step 5:** Commit `owner_review: кандидат правки и вопрос об удалении без терминала (#766 срез 2)`.

---

### Task 3: место 4 в обоих путях — замечания через `dfl reply`

**Files:**
- Modify: `lite/review_pause.py` (`ask_remote`), `lite/personal/flow.py` (`_pause_answer`, цикл паузы ~l.479, `_revise_by_owner`), `lite/dfl.py` (`_owner_node`, `_owner_answer_remote`)
- Test: `tests/test_lite_personal_review_remote.py`, `tests/test_lite_dfl_owner_review_remote.py` (новые)

**Interfaces:**
- Consumes: Task 1 (`is_done`), Task 2 (`WAITING`, параметры `ask`/`store`).
- Produces:
  - `rp.ask_remote(...) -> str | None`: `""` — «дальше» (`pending.is_done`), текст — замечания, `None` — ждём.
  - Личный путь: замечания без терминала записываются (`rp.record`) и правятся (`_revise_by_owner` с `ask=pending.asker(rd)`, `store=rd`); стоп `NO_TTY` при недоигранных замечаниях убирается для этапов `design`/`decomposition` (для `brief` остаётся).
  - `WAITING` → `_stop_wait(ctx, detail)` (код 0); `ctx.stopped` не ставится.
  - Enterprise: `_owner_node` с `ask` правит так же; `WAITING` → `say(detail)`, код 0; «дальше» запоминается в `review-acked.json` только для «дальше», не для замечаний.

- [ ] **Step 1: Failing tests** — на фейках каждого пути, без терминала:
  - `test_remarks_by_reply_revise_and_continue` — пауза → `reply "убери D-02"` → `dfl run`: правка (1 вызов автора), вопрос «Удалить D-02 …?» в `pending.json`, код 0 → `reply "1"` → `dfl run`: автор не зван, этап перепроверен, пауза снова (как в терминале после правки) → `reply "дальше"` → этап пройден. Итог `calls.revise == 1`.
  - `test_brief_remarks_by_reply_refused` (личный) — `dfl reply` с текстом на паузе брифа → отказ `REVIEW_REFUSAL`.
  - `test_remote_cycle_paid_calls_equal_terminal` — тот же сценарий в терминале (ввод: «убери D-02», «1», «дальше») и без терминала: одинаковые счётчики платных вызовов.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** Новые и все тесты из Global Constraints — PASS.
- [ ] **Step 5:** Commit `dfl: замечания к этапу через dfl reply в обоих путях (#766 срез 2)`.

---

### Task 4: место 5 — вопросы разбора edge-check без терминала, круг сохраняется

**Files:**
- Modify: `lite/edge_resolve.py`, `lite/dfl.py` (`_resolve_rounds`, `_resolve`, `_run_chatting`), `lite/personal/flow.py` (цикл ребра ~l.350)
- Test: `tests/test_lite_edge_resolve_remote.py`, `tests/test_lite_dfl_edge_remote.py` (новые)

**Interfaces:**
- Consumes: Task 1 (`EDGE`, `check`).
- Produces (в `lite/edge_resolve.py`):
  - `Outcome.WAITING = "waiting"` — вопрос записан, прогон ждёт.
  - `resolve(..., ask: pending.Ask | None = None)`, `resolve_scope(..., ask: pending.Ask | None = None)`.
  - `def edge_question(q: _Question, question: str) -> pending.Question` — `kind=EDGE`, `key="/".join(finding.key)`, `text` — строки, которые печатает `_ask` до списка вариантов (замечание, «то же правило…», вопрос), `options` — `_option_line` каждого варианта и `offer`, если он есть, `accepts=ANSWER_HELP`.
  - `_ask`: с `ask` — ответ `ask(q)`, `None` → ожидание; в терминале сначала `pending.take(scope.run_dir, q)`, иначе живой ввод (после живого ответа `pending.discard`). Ответ разбирается тем же `_parse`.
  - `WAIT_FILE = "edge-wait.json"`, `{slot: {"round": int, "node": sha256 | null, "findings": [...]}}`, где `slot` — `f"wave-{wave}"` (enterprise) или `f"node-{kind}"` (личный). Функции `wait_load(run_dir, slot) -> dict | None`, `wait_save(run_dir, slot, data) -> None`, `wait_clear(run_dir, slot) -> None`. Находки — `dataclasses.asdict(Finding)` и обратно.
- Правила круга (§5.6 п.5):
  - `WAITING` → сохранить номер текущего круга (enterprise) / круг, sha узла и находки (личный).
  - Любой другой исход, сходимость или предел — `wait_clear`.
  - Начало: есть запись → продолжить с её круга.
  - Enterprise: при ожидающем edge-вопросе (`pending.load(rd)["kind"] == EDGE`) `_run_chatting` не делает начальный `_step` (иначе платная перепроверка неизменного узла).
  - Личный: при совпавшем sha узла берутся сохранённые находки вместо `check_node` (без `ctx.calls.check += 1`); sha не совпал — запись и вопрос сняты, обычная проверка.
- Без терминала личный путь передаёт `ask=pending.asker(rd)`, enterprise — `pending.asker(rs.run_dir(...))` при `not _is_tty()`.
- `WAITING`: личный путь — `_stop_wait(ctx, wait_line)`; enterprise — `say(wait_line)`, код 0, без «что править» (`_say_next`).

- [ ] **Step 1: Failing tests**
  - `test_edge_question_written_without_tty` — `resolve_scope` с `ask=pending.asker(rd)`: `WAITING`, `pending.json` вида `edge` с вариантами, сортировка 1 раз, переписывания нет.
  - `test_edge_resume_no_repaid_triage_or_check` (оба пути) — reply «1» → `dfl run`: сортировка из кэша (`calls.triage` не растёт), проверка до переписывания не звалась, переписывание 1.
  - `test_waiting_on_second_round_keeps_round` — ожидание на круге 2 в `test`: продолжение предлагает «принять вопреки» (вариант `OVERRIDE_OFFER` в `options`), после него не начинается круг 1.
  - `test_personal_edited_node_drops_saved_findings` — sha узла сменился → проверка заново, ожидающий вопрос снят.
  - `test_edge_remote_paid_calls_equal_terminal` — счётчики равны терминальному проходу с теми же ответами.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** Новые и все тесты из Global Constraints — PASS.
- [ ] **Step 5:** Commit `dfl: разбор замечаний edge-check без терминала, круг в каталоге прогона (#766 срез 2)`.

---

### Task 5: документация и сквозная проверка

**Files:**
- Modify: `README.md` (раздел про `dfl pending`/`dfl reply`), `docs/darkfactory-for-colleagues.md` (форма ответов `dfl reply`), `lite/dfl.py` (`--help` у `reply`: перечень видов)

- [ ] **Step 1:** README и памятка: какие ответы принимает `dfl reply` для `review` (замечания словами, кроме брифа личного пути), `remarks-confirm`, `edge`; что продолжение не повторяет платную работу.
- [ ] **Step 2:** `uv run --frozen --group selfcheck ruff check . && uv run --frozen --group selfcheck ruff format --check .` и полный `uv run --frozen --group governance pytest -q` — зелёные.
- [ ] **Step 3:** Commit `docs: dfl reply — замечания, вопрос об удалении, разбор edge-check (#766 срез 2)`.
