# dfl: вопрос наружу / ответ внутрь — срез 4 (личный путь без терминала) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Без терминала интервью личного пути ведёт Claude сессии (скил): `dfl run` ставит ожидающий вопрос «нужен бриф», скил пишет журнал разговора `dfl note`, сдаёт бриф `dfl brief`, `dfl` сам строит сводку и спрашивает «Верно?» через `dfl reply`; замечания к брифу на паузе принимаются и без терминала.

**Architecture:** Новый модуль `lite/personal/remote_interview.py` — интервью личного пути без терминала поверх того же журнала `interview.jsonl`, тех же проверок брифа (`brief_problems`, `demote_uncovered`) и тех же лимитов, что терминальный `interviewer.py`; общая логика «принять бриф или вернуть проблемы» вынесена из `interviewer._settle_brief` в чистую функцию. Протокол `lite/pending.py` получает два вида: `interview-personal` (нужен бриф; ответ — `dfl note`/`dfl brief`, не `dfl reply`) и `brief-confirm` («Верно?» по сводке, построенной `dfl`). `lite/personal/flow.py` без терминала зовёт удалённое интервью вместо терминального.

**Tech Stack:** Python 3.12, stdlib, pytest; `uv run --frozen --group governance pytest`.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-skill-desktop-design.md` (§5.1, §5.2 «Личный путь», §5.3, §5.6 пп.1–3, §6 п.4, §7). Срезы 1–3 — `docs/superpowers/plans/2026-11-17-dfl-pending-reply-slice1.md`, `…-slice2.md`, `2026-11-19-dfl-pending-reply-slice3.md` (влиты).

## Решения (утверждены владельцем 19.11 в разговоре; §8 спеки оставил их строителю)

- **R1. «Нужен бриф» — вопрос `interview-personal`.** Без терминала шаг интервью ставит его (ключ `brief`, текст — `NEED_BRIEF` с темой) и `dfl run` выходит кодом 0 со строкой ожидания. `data` записи: `subject`, `transcript` (абсолютный путь `interview.jsonl`), `prompt` (абсолютный путь `prompts/interviewer.md` языка прогона), `lang`, `questions`, `max_questions` (= `MAX_QUESTIONS`), `checks_in_row`, `max_checks_in_row`. `dfl reply` на него — отказ `PERSONAL_REPLY_REFUSAL` (ответ — `dfl note`/`dfl brief`).
- **R2. Журнал — `dfl note`.** `dfl note --id <id> --role agent|human --text '…' [--option '…' …]`: реплика агента пишется как `{"role":"agent","kind":"ask","text":…, "options":[…]}`, человека — `{"role":"human","text":…}`, в тот же `interview.jsonl`. Только пока ждёт `interview-personal` с этим id (иначе `pending.NO_PENDING`/`pending.STALE`). Вопрос агента сверх `MAX_QUESTIONS` — отказ `CAP_REFUSAL` (собирайте бриф). `--option` у человека — отказ. Пустой текст — отказ.
- **R3. Бриф — `dfl brief`, но не запись.** `dfl brief --id <id> --file brief.json [--accept-uncovered]` (файл — объект `brief_from_json`, без обёртки `{"brief": …}`). Форма не та — отказ `DflError` с текстом `FormError`. Дальше — общая с терминалом функция `settle(...)`: принудительно (лимит вопросов, `MAX_CHECKS_IN_ROW` проверок подряд или `--accept-uncovered`) — функции без критерия и остаток проблем в «Уточнить»; иначе проблемы → в журнал реплики `agent/brief` + `check` (как терминал), вывод проблем построчно, **код 1**. Бриф без целей или функций → реплика `agent/brief` в журнал, вывод `NOTHING_TO_BUILD_REMOTE`, код 1. Принят → черновик `brief-draft.json` в каталоге прогона, реплика `agent/summary` с `render_brief` в журнал, вопрос `brief-confirm` вместо `interview-personal`, вывод сводки и строки ожидания, код 0.
- **R4. «Верно?» — `brief-confirm` через `dfl reply`.** Текст вопроса — сводка + `"\n\n"` + `interviewer.CONFIRM`; id — хэш текста, так что ответ привязан ровно к показанной сводке. `dfl reply` принимает любой непустой текст. Следующий `dfl run`: «1»/слово из `interviewer.YES_WORDS` → бриф из черновика записывается как в терминале (`flow._write_brief`), черновик удаляется; иное → реплика человека `FIX_PREFIXES[lang] + <текст без префикса «поправить:»/«2»>` (как `interviewer._correction`, голое «2» — отказ `dfl reply`: нужна поправка словами), черновик удаляется, снова `interview-personal`.
- **R5. Терминал побеждает.** Терминальное интервью (`flow._interview` при `ctx.tty()`) перед стартом снимает `pending.json`, `replies.json` и `brief-draft.json` (`remote_interview.reset`) и продолжает тот же журнал (RF-1 уже есть). Поведение терминала не меняется.
- **R6. Замечания к брифу на паузе — и без терминала.** `pending._check_review` больше не отказывает замечаниям к `brief`; `accepts` вопроса паузы брифа — как у прочих этапов. В `flow._review` ветка «бриф правится только в терминале» снимается: замечание идёт `_brief_by_owner` → `append_correction` (уже идемпотентна) → `_interview`, которое без терминала ставит `interview-personal`; `mark_applied` — только после подтверждённого брифа (прежний порядок). Отдельных `brief-remarks`/`--revise` из спеки §5.6 п.1 нет — тот же механизм (решение владельца).
- **R7. `dfl interview --json` для личного прогона**: `{"track":"personal","subject","questions","max_questions","checks_in_row","draft": <объект брифа или null>,"uncovered": [id функций без критерия в черновике]}`. Текстовый `dfl interview` для личного — та же информация строками.
- **R8. Платных вызовов `dfl` на этом пути нет**: агент интервьюера без терминала не зовётся (тесты — агент-фейк, падающий при вызове).

## Global Constraints

- Терминальное поведение не меняется: `tests/test_lite_personal_interviewer.py`, `tests/test_lite_personal_flow.py`, `tests/test_lite_dfl_personal.py`, `tests/test_lite_english_personal.py` проходят **без правок**. Исключение — ассерты, закрепляющие отказ замечаниям к брифу без терминала (`tests/test_lite_pending.py:144,150-157`, `tests/test_lite_personal_review_remote.py:169-172`): они меняются под R6, и только они.
- Личный путь двуязычный: каждая новая строка, которую видит человек или скил, — константа на русском, выводится через `i18n.t`, с переводом в `lite/i18n_en.py`; язык — язык прогона (`PERSONAL_COMMANDS` получает `note` и `brief`).
- Ожидание человека — код 0; отказ — `DflError` (код 2) с причиной; бриф с проблемами — код 1 (не отказ: скил правит и сдаёт снова).
- Запись в журнал, черновик и `pending.json` — под `run_lock` прогона, как `dfl reply`.
- Человек не видит JSON; сводку «Верно?» строит только `dfl` (`render_brief`).
- `ruff check`, `ruff format --check` (`uv run --frozen --group selfcheck`), полный `pytest` зелёные.

## Review Focus

- Скил сдал бриф, человек ответил «да», но между `dfl brief` и `dfl run` скил сдал **другой** бриф: id `brief-confirm` сменился, ответ «да» на прежнюю сводку не применяется к новому брифу (ответ пропадает — `pending.post` с новым id стирает `replies.json`). → Task 1.
- `dfl note` после того, как вопрос стал `brief-confirm` (скил продолжил разговор, не дождавшись «Верно?»), — отказ `STALE`, журнал не тронут. → Task 3.
- Человек на «Верно?» ответил «2» без текста — `dfl reply` отказывает с просьбой написать поправку; «поправить: не раз в неделю» → реплика «поправить: не раз в неделю» (без двойного префикса). → Task 1.
- Терминальный `dfl run` при висящем `brief-confirm`: черновик и вопрос сняты, терминальный интервьюер продолжает с журнала (последняя реплика — `summary`), сводка не задаётся дважды без нового хода агента. → Task 2.
- Пауза на брифе без терминала, замечание через `dfl reply`, затем бриф сдан и подтверждён — пауза брифа показывается снова (замечание отмечено применённым один раз), а не уходит сразу в design. → Task 2.

---

### Task 1: протокол и ядро удалённого интервью

**Files:**
- Modify: `lite/pending.py` (виды, проверка ответа)
- Modify: `lite/personal/interviewer.py` (вынести `settle`)
- Create: `lite/personal/remote_interview.py`
- Modify: `lite/i18n_en.py`
- Test: `tests/test_lite_personal_remote_interview.py`, `tests/test_lite_pending.py` (только ассерты R6)

**Interfaces:**
- Produces (`lite/pending.py`): `INTERVIEW_PERSONAL = "interview-personal"`, `BRIEF_CONFIRM = "brief-confirm"`; `check()` для `INTERVIEW_PERSONAL` — всегда `i18n.t(PERSONAL_REPLY_REFUSAL)`, `PERSONAL_REPLY_REFUSAL = "ответ на этот вопрос — dfl note и dfl brief (бриф собирает скил)"`; для `BRIEF_CONFIRM` — пусто или голое «2» → `i18n.t(CONFIRM_FIX_REFUSAL)`, `CONFIRM_FIX_REFUSAL = "ответ: «да» или поправка словами"`; иначе `None`. `_check_review` для `brief` принимает замечания (R6).
- Produces (`lite/personal/interviewer.py`): `settle(brief: Brief, entries: list[Entry], *, force: bool = False, lang: str = "ru") -> tuple[Brief | None, list[str]]` — принудительно (`force` или лимиты) → (бриф с переносом в «Уточнить», []); проблем нет → (бриф, []); иначе → (None, проблемы). `_settle_brief` переписан через неё, поведение то же.
- Produces (`lite/personal/remote_interview.py`):
  - `DRAFT = "brief-draft.json"`; `NEED_BRIEF = "нужен бриф по теме «{subject}»: разговор ведёт скил — dfl note, затем dfl brief"`; `CAP_REFUSAL`, `NOTHING_TO_BUILD_REMOTE`, `BRIEF_PROBLEMS = "бриф не принят — поправьте и сдайте снова:"` (тексты двух первых — на усмотрение строителя, по-русски, с переводом).
  - `step(run_dir: Path, subject: str, *, lang: str = "ru") -> Brief | None` — шаг без терминала: подтверждённый бриф или `None` (вопрос поставлен; строку ожидания печатает вызывающий через `pending.wait_text`).
  - `note(run_dir: Path, qid: str, role: str, text: str, options: tuple[str, ...] = (), *, lang: str = "ru") -> None` — `pending.PendingError` при чужом/нет вопросе, `ValueError` при нарушении R2.
  - `submit(run_dir: Path, qid: str, data: object, *, subject: str, accept_uncovered: bool = False, lang: str = "ru") -> Submitted` — `Submitted(ok: bool, problems: list[str], summary: str)`; `pending.PendingError` / `FormError` — как у `note`.
  - `reset(run_dir: Path) -> None` — снять вопрос, ответы и черновик (R5).
  - `status(run_dir: Path, subject: str) -> dict` — данные R7.

- [ ] **Step 1: Write the failing tests** (`tests/test_lite_personal_remote_interview.py`, фикстура — `tmp_path` как каталог прогона, бриф — словарь в форме `brief_from_json` с одной целью `G-01` и функцией `F-01` с критерием)

```python
def test_step_posts_need_brief(tmp_path):
    assert ri.step(tmp_path, "заметки") is None
    rec = pending.load(tmp_path)
    assert rec["kind"] == pending.INTERVIEW_PERSONAL and "заметки" in rec["text"]
    assert rec["data"]["transcript"] == str(tmp_path / "interview.jsonl")
    assert rec["data"]["max_questions"] == interviewer.MAX_QUESTIONS

def test_note_appends_and_caps(tmp_path): ...
    # agent+options → kind ask; human → plain; 21-й ask → ValueError(CAP_REFUSAL), журнал не тронут

def test_note_stale_refused(tmp_path): ...  # чужой id → PendingError(STALE)

def test_submit_problems_logged_code(tmp_path): ...
    # функция без критерия → ok False, problems непусты; в журнале agent/brief и check

def test_submit_forced_after_three_checks(tmp_path): ...  # 3 check подряд → ok True, «Уточнить»

def test_submit_accept_uncovered(tmp_path): ...

def test_submit_ok_posts_confirm_with_summary(tmp_path):
    # ok True; pending kind BRIEF_CONFIRM; text начинается с render_brief(...) и
    # кончается interviewer.CONFIRM; DRAFT записан; в журнале agent/summary

def test_confirm_yes_returns_brief(tmp_path): ...  # reply «да» → step → Brief, DRAFT удалён, pending нет

def test_confirm_fix_appends_correction(tmp_path):
    # reply «поправить: раз в месяц» → step → None; последняя реплика
    # {"role":"human","text":"поправить: раз в месяц"}; kind снова INTERVIEW_PERSONAL

def test_resubmit_drops_old_yes(tmp_path): ...  # Review Focus 1

def test_reply_bare_two_refused(tmp_path): ...  # pending.record(..., "2") → PendingError(CONFIRM_FIX_REFUSAL)

def test_no_agent_called(tmp_path): ...  # модуль не импортирует/не зовёт агента: monkeypatch interviewer._next_turn на падающий
```

И в `tests/test_lite_pending.py` — ассерты R6: `pending.check(pending.REVIEW, "убери", brief) is None`, пустой ответ на паузе брифа — `REVIEW_EMPTY_REFUSAL`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_lite_personal_remote_interview.py tests/test_lite_pending.py`
Expected: FAIL (`ModuleNotFoundError: lite.personal.remote_interview`).

- [ ] **Step 3: Implement** — `settle` в `interviewer.py` (терминальные тесты без правок — проверка), виды и проверки в `pending.py`, модуль `remote_interview.py`. Журнал — через `interviewer._Log` (не вторая реализация). Черновик — `asdict(brief)` JSON; читается `brief_from_json`. Поправка — `interviewer._correction`-логика без чтения (префикс снимается тем же `_FIX_PREFIX`).

- [ ] **Step 4: Run tests**

Run: `uv run --frozen pytest -q tests/test_lite_personal_remote_interview.py tests/test_lite_pending.py tests/test_lite_personal_interviewer.py`
Expected: PASS; `test_lite_personal_interviewer.py` — без правок.

- [ ] **Step 5: Commit** — `dfl: удалённое интервью личного пути — ядро (interview-personal, brief-confirm) (#766 срез 4)`

---

### Task 2: шаг интервью и пауза брифа без терминала

**Files:**
- Modify: `lite/personal/flow.py` (`_interview`, `_review`)
- Modify: модуль вопроса паузы (`rp.question` — `accepts` для `brief`)
- Test: `tests/test_lite_personal_review_remote.py` (только ассерты R6 + новые тесты), `tests/test_lite_personal_flow_remote.py` (новый)

**Interfaces:**
- Consumes: `remote_interview.step`, `remote_interview.reset` (Task 1); `pending.wait_text`.
- Produces: `flow._interview(ctx, rd)` — без терминала: `step(...)` → `None` — `_stop_wait(ctx, pending.wait_text(rd))`, `False`; бриф — `_write_brief`, `True`. В терминале — `reset(rd)`, затем прежний `interview(...)`.

- [ ] **Step 1: Write the failing tests** (`advance(..., tty=lambda: False)` на фейках `tests/test_lite_personal_flow.py`; агент — падающий фейк)

```python
def test_no_tty_interview_posts_need_brief(...):
    # advance → 0; run.step == "interview"; pending kind INTERVIEW_PERSONAL;
    # stop_reason начинается с «ждёт ответа»

def test_no_tty_confirmed_brief_moves_to_design(...):
    # note/submit/record «да» между двумя advance → 00-brief.md и brief.json
    # записаны как в терминале, APPROVALS — одна строка одобрения, step дальше

def test_tty_resets_remote_state(...):  # Review Focus 4
    # висит brief-confirm + DRAFT; advance(tty=True) с терминальным фейком
    # интервью → pending.json и DRAFT сняты до вызова interview

def test_no_tty_brief_remarks_reinterview(...):  # Review Focus 5
    # review_pause=("brief",); бриф подтверждён; пауза → reply «добавь экспорт»
    # → advance: в журнале «поправить: добавь экспорт», pending INTERVIEW_PERSONAL;
    # бриф сдан и подтверждён → advance: пауза брифа снова (REVIEW), замечание
    # отмечено применённым один раз (rp.pending пуст)
```

В `tests/test_lite_personal_review_remote.py:169-172` — ассерты R6: `accepts` вопроса паузы брифа — как у design; `dfl reply` с замечанием к брифу записывается.

- [ ] **Step 2: Run to verify fail** — `uv run --frozen pytest -q tests/test_lite_personal_flow_remote.py tests/test_lite_personal_review_remote.py` → FAIL.

- [ ] **Step 3: Implement** — `_interview` по Interfaces; в `_review` убрать ветку `batch and stage == "brief" and not ctx.tty()`; `accepts` паузы брифа — общий.

- [ ] **Step 4: Run** — `uv run --frozen pytest -q tests/test_lite_personal_*.py tests/test_lite_dfl_personal.py tests/test_lite_english_personal.py` → PASS, терминальные — без правок.

- [ ] **Step 5: Commit** — `dfl: шаг интервью и пауза брифа личного пути без терминала (#766 срез 4)`

---

### Task 3: команды `dfl note`, `dfl brief`, `dfl interview` для личного

**Files:**
- Modify: `lite/dfl.py` (парсер, `_dispatch`, `PERSONAL_COMMANDS`, `interview`)
- Modify: `lite/i18n_en.py`
- Test: `tests/test_lite_dfl_personal_remote.py` (новый; окружение — как `tests/test_lite_dfl_pending.py`)

**Interfaces:**
- Consumes: `remote_interview.note/submit/status` (Task 1); `_pending_dir`, `run_lock`, `DflError`.
- Produces: `note_cmd(cwd, subject, qid, role, text, options) -> int` (0), `brief_cmd(cwd, subject, qid, path, *, accept_uncovered) -> int` (0 принят / 1 проблемы / 2 отказ), `interview` для активного личного прогона — R7. Подкоманды: `note` (`--id` обяз., `--role {agent,human}` обяз., `--text` обяз., `--option` повторяемый, `--subject`), `brief` (`--id`, `--file` обяз., `--accept-uncovered`, `--subject`). Только для активного личного прогона; иначе отказ `NO_PERSONAL_RUN = "нет личного прогона, ждущего бриф — dfl status"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_note_and_brief_cli_round_trip(env, ...):
    # run (no tty) → pending --json: kind interview-personal, data.prompt существует
    # note agent «Что должно получиться?» --option a --option b → 0
    # note human «список дел» → 0
    # brief --file плохой (без функций) → 1, вывод «бриф не принят»
    # brief --file хороший → 0, вывод — сводка; pending kind brief-confirm
    # reply «да» → 0; run → бриф записан, шаг дальше интервью

def test_note_after_confirm_posted_is_stale(env, ...): ...  # Review Focus 2, журнал не тронут
def test_brief_bad_json_refused(env, ...): ...  # код 2, причина в stderr
def test_interview_json_personal(env, ...): ...  # R7 поля; draft после brief
def test_note_brief_in_english_run(env, ...): ...  # прогон --lang en: строки по-английски
def test_note_without_personal_run_refused(env, ...): ...
```

- [ ] **Step 2: Run to verify fail** — `uv run --frozen pytest -q tests/test_lite_dfl_personal_remote.py` → FAIL.

- [ ] **Step 3: Implement** — запись под `run_lock.run_lock(run_dir.name)` (`LockBusy` → `_lock_busy`); `PendingError`/`ValueError`/`FormError` → `DflError`; файл брифа — `json.loads` (ошибка — `DflError`); `help` у `reply` — дописать `brief-confirm`.

- [ ] **Step 4: Run** — `uv run --frozen pytest -q tests/test_lite_dfl*.py tests/test_lite_personal_*.py tests/test_lite_english_personal.py` → PASS.

- [ ] **Step 5: Commit** — `dfl: команды note и brief, interview --json личного прогона (#766 срез 4)`

---

### Task 4: сквозной тест без терминала и документация

**Files:**
- Test: `tests/test_lite_dfl_personal_remote.py` (сквозной)
- Modify: `README.md` (раздел `dfl` без терминала), `docs/darkfactory-for-colleagues.md` (памятка: личный путь без терминала), `TODO.md` (пункт среза 4 #766 — `[x]` с номером PR оставит контроллер; здесь строка «срез 4» — отметка выполнения без номера)

- [ ] **Step 1: Write the failing test** — `test_personal_path_to_design_without_terminal`: новый личный прогон без терминала только через `run/pending/note/brief/reply`, с паузой на `brief` и одним замечанием через `reply`; дошёл до шага `design`; агент интервьюера — падающий фейк (ноль платных вызовов интервью); бриф в бандле равен `render_brief` подтверждённого.
- [ ] **Step 2: Run to verify fail/pass** — тест должен пройти на коде Tasks 1–3; падение — дефект там, чинить код, не тест.
- [ ] **Step 3: Docs** — README и памятка: цикл скила `run → pending → note… → brief → reply «да» → run`; что `dfl` сам строит сводку «Верно?»; `--accept-uncovered`; замечания к брифу без терминала.
- [ ] **Step 4: Run** — `uv run --frozen pytest -q tests/test_lite_*.py` и `uv run --frozen --group selfcheck ruff check . && uv run --frozen --group selfcheck ruff format --check .` → PASS.
- [ ] **Step 5: Commit** — `dfl: личный путь без терминала — сквозной тест и памятка (#766 срез 4)`
