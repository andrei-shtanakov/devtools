# dfl: вопрос наружу / ответ внутрь — срез 3 (enterprise-интервью без терминала) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Без терминала enterprise-интервью ведётся снаружи (скилом): `dfl run` ставит ожидающий вопрос интервью (`interview-question`, `interview-topup`, `interview-repair`) с покрытием тем и промптом раскладки, скил отдаёт разложенный ответ `dfl answer --items`, пропуск и вердикт реализуемости — `dfl reply`; состояние интервью — `dfl interview --json`.

**Architecture:** Новый модуль `lite/interview_remote.py` вычисляет следующий вопрос интервью из состояния discovery (`discovery status` + `SessionView`) и отметок прохода (`interview-pass.json` в каталоге прогона) и применяет ответы. Раскладку ответа делает Claude сессии по тому же промпту, что сегодня уходит вложенному агенту (`structure.build_prompt`); проверку формы, назначение id, слияние с ответом держателя и YAML делает `dfl` тем же кодом (`structure`, `gate_repair.appended`). Протокол среза 1 (`lite/pending.py`) получает три вида и запись вопроса с дополнительными данными.

**Tech Stack:** Python 3.12, stdlib, pyyaml, pytest; `uv run --frozen --group governance pytest`.

**Spec:** `docs/superpowers/specs/2026-11-17-dfl-skill-desktop-design.md` (§5.1, §5.2 «Enterprise», §5.6 п.4, §6 п.3, §7). Срезы 1–2 — `docs/superpowers/plans/2026-11-17-dfl-pending-reply-slice1.md`, `docs/superpowers/plans/2026-11-18-dfl-pending-reply-slice2.md` (влиты).

## Решения строителя (§8 спеки оставил их строителю)

- **R1. Раскладку отдаёт скил в форме агента, не YAML.** `dfl answer --items f.json --id <id>`: файл — `{"text": "<слова человека>", "items": [...]}` в схеме ответа агента `structure.build_prompt` (prefix/ref/id/traces/priority/…). `dfl` проверяет `form_errors` и `criterion_errors` (нарушение — отказ с текстом ошибок, код 2; критерий — гипотеза раскладчика, у скила она всегда есть), сливает обновления (`_merged`), назначает id и собирает YAML (`to_payload`). Так правила раскладки и id не копируются в скил (§5.2 «без второй копии правил»). Прежний `dfl answer --file answer.yaml` остаётся.
- **R2. Промпт — тот же источник.** Запись вопроса несёт `prompt` = `structure.build_prompt(question, HUMAN_PLACEHOLDER, view)`, `HUMAN_PLACEHOLDER = "<<ответ человека>>"`.
- **R3. Пропуск и вердикт — `dfl reply`.** Для всех трёх видов принимается слово пропуска (`interview_chat.SKIP_WORDS`); для вердикта GC-05 (`interview-repair` с ключом `verdict:FR-NN`) — любой непустой текст («да» и согласие по `gate_repair._agrees` — `resolved`, иначе `open` со словами). Запись `X` строит `dfl`, как `gate_repair._fill_verdicts`.
- **R4. Один проход на вызов, как в терминале.** Отметки пропуска/исправления пунктов добора и починки — в `interview-pass.json` каталога прогона; когда пунктов больше нет, `dfl run` стирает отметки и делает шаг ещё раз; остановился неполным снова — прежний вывод стопа, следующий `dfl run` начинает новый проход.
- **R5. Вопрос интервью при статусе `waiting_interview` ставится молча**: вывод `dfl run` не меняется (прежние `_say_question`/`ANSWER_HINT`), скил читает `dfl pending --json` после каждого `dfl run`. Добор и починка (сегодня без терминала — только стоп) печатают `pending.wait_line`.
- **R6. Терминал побеждает.** Разговор интервью в терминале (`dfl._converse`) и любой успешный `dfl answer --file` снимают `pending.json` и `interview-pass.json` (`pending.discard`, `interview_remote.clear_marks`).

## Global Constraints

- Терминальное поведение не меняется: существующие тесты (`tests/test_lite_dfl.py`, `tests/test_lite_interview_chat.py`, `tests/test_lite_gate_repair.py`, `tests/test_lite_structure.py`, `tests/test_lite_interview_state.py`, `tests/test_lite_pending.py`, `tests/test_lite_dfl_owner_review.py`, `tests/test_lite_dfl_pending.py`) проходят **без правок**. Если тест падает — меняется код, не тест.
- Enterprise-путь всегда по-русски (`dfl._enterprise_language`); новые строки — русские константы, в каталог `i18n_en` не добавляются.
- Ожидание человека — код 0; отказ — `DflError` (код 2) с причиной.
- Платных вызовов `dfl` в новом пути нет: раскладку делает скил; агент `structure`/`gate_repair` без терминала не зовётся (тест — агент-фейк, падающий при вызове).
- Человек не видит ни JSON, ни YAML, ни id пунктов в тексте вопроса (`Question.text`); id — только в данных записи для скила.
- `ruff check`, `ruff format --check`, полный `pytest` зелёные.

## Review Focus

- `dfl answer --items` с `--id` прежнего вопроса (скил ответил на устаревший) — отказ `pending.STALE`, discovery не зовётся. → Task 4.
- Пункт с `id`, которого нет в сессии, или traces на несуществующий id — отказ с текстом `form_errors`, ничего не отправлено. → Task 4.
- Добор: человек пропустил тему, следующий `dfl run` не спрашивает её снова в том же проходе, но новый проход (после шага) — спрашивает. → Task 3.
- Человек ответил в терминале (`dfl answer` разговором), пока висел `interview-question` — запись снята, `dfl pending` — «вопросов нет» или новый вопрос. → Task 3.
- Вердикт GC-05 двумя `dfl reply` по двум FR — две записи `X` с разными номерами, обе в ответе держателя реализуемости. → Task 2.

---

### Task 1: `lite/pending.py` — виды интервью, запись с данными, проверка ответа

**Files:**
- Modify: `lite/pending.py`
- Test: `tests/test_lite_pending_interview.py` (новый)

**Interfaces:**
- Produces:
  - `INTERVIEW_QUESTION = "interview-question"`, `INTERVIEW_TOPUP = "interview-topup"`, `INTERVIEW_REPAIR = "interview-repair"`, `INTERVIEW_KINDS = frozenset({...трёх...})`.
  - `def post(run_dir: Path, q: Question, data: Mapping | None = None) -> None` — запись вопроса (то, что сегодня делает `asker` после неудачного `take`, включая сброс `replies.json` при смене вопроса) плюс поле `"data": dict(data)` если `data`; `asker` переписан через `post` без изменения поведения.
  - `check(kind, text, rec)` для видов интервью: слово пропуска (`lite.interview_chat.SKIP_WORDS`, регистр не важен) — принято; `INTERVIEW_REPAIR` с `rec["key"]` на `verdict:` — любой непустой текст; иначе `INTERVIEW_REFUSAL = "ответ по существу: dfl answer --items <файл> --id {qid}; здесь — только «пропустить»"` (форматируется `qid=rec["id"]`). Импорт `interview_chat` — внутри функции, если иначе цикл импорта.
- Consumes: срез 1 (`Question`, `take`, `asker`, `record`, `check`).

- [ ] **Step 1: Failing tests** — `test_post_writes_data_and_keeps_asker_behaviour` (запись содержит `id`, `kind`, `data`; `asker` ведёт себя как раньше: повторный тот же вопрос не стирает `replies.json`, другой — стирает); `test_interview_reply_accepts_skip_only` (`"пропустить"`, `"Skip"` → `None`; `"FR-01 …"` → `INTERVIEW_REFUSAL` с id); `test_verdict_reply_accepts_text` (`key="verdict:FR-03"`, `"да"` и `"нужен кэш"` → `None`, `"  "` → отказ).
- [ ] **Step 2:** `uv run --frozen --group governance pytest tests/test_lite_pending_interview.py -q` — FAIL.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** Новый файл и `tests/test_lite_pending.py` — PASS.
- [ ] **Step 5:** Commit `dfl: виды вопросов интервью в pending (#766 срез 3)`.

### Task 2: `lite/interview_remote.py` — следующий вопрос, покрытие, отметки, применение reply

**Files:**
- Create: `lite/interview_remote.py`
- Modify: `lite/gate_repair.py` (вынести построение записи вердикта в `verdict_entry`; `_fill_verdicts` зовёт её — поведение байтово прежнее)
- Test: `tests/test_lite_interview_remote.py`

**Interfaces:**
- Consumes: Task 1; `SessionView`, `interview_chat.current_question`, `gate_repair` (`NO_CRITERION_RE`, `NO_SYSTEM_RE`, `NO_VERDICT_RE`, `_ids`, `_feasibility_holder`, `must_bodies`, `appended`, `_agrees`), `structure.build_prompt`, `structure.TOPIC_LABELS`, `interview_view._finding_text`.
- Produces:
  - `PASS_FILE = "interview-pass.json"`; `def load_marks(run_dir: Path) -> dict[str, list[str]]` (`{"skipped": [...], "done": [...]}`, нет файла — пустые), `def mark(run_dir: Path, key: str, how: str) -> None` (`how` ∈ `"skipped"`, `"done"`), `def clear_marks(run_dir: Path) -> None`.
  - `def coverage(view: SessionView) -> list[dict]` — по темам фрейма в порядке required, затем optional: `{"key", "label", "required": bool, "covered": bool, "entries": [{"id", "body", "fields"}]}`.
  - `def findings(view: SessionView) -> list[dict]` — `{"raw": str, "text": <_finding_text>}`.
  - `def next_question(reply: DiscoveryReply, view: SessionView, marks: dict, *, incomplete: bool, upstream: str) -> tuple[Question, dict] | None` — порядок: (1) код 20 → `INTERVIEW_QUESTION`, `key` = question_id, `text` = `"Вопрос {n} ({label}): {text}"` (как `interview_chat._answer_question`); (2) `incomplete` и тема из `view.uncovered_required()` с префиксом и держателем (`interview_chat._holder_question`), не в `skipped` → `INTERVIEW_TOPUP`, `key` = `topic:{key}`, текст `"Не хватает: {label} — {TOP_UP_HINT}"`; (3) находки `reply.envelope["findings"]` по порядку gate_repair: GC-08 → `criterion:{eid}` (текст: `CRITERIA_INTRO` + «: «{body}»»), GC-13 → один вопрос `traces:{ids через ,}` (`TRACES_INTRO`), GC-05 → по одному `verdict:{FR}` (`"{FR} «{label}» — {VERDICT_HINT}"`, label из `must_bodies(upstream)`); ключи из `skipped`/`done` пропускаются. `data`: `{"topic", "label", "holder": qid|None, "entries": [записи `entry_record` чинимых пунктов], "coverage", "findings", "prompt": build_prompt(...)|None (для вердикта — None), "answer": строка команды}`. Пунктов нет — `None`.
  - `def apply_reply(rec: Mapping, text: str, view: SessionView, *, send: Send, resend: Send) -> str | None` — пропуск: вопрос — `send(qid, to_payload(text, [], view))`, добор/починка — `mark skipped` делает вызывающий; вердикт — `resend(holder, appended(held, payload с verdict_entry))`. Возвращает причину отказа discovery или `None`.
  - `gate_repair.verdict_entry(fr: str, text: str, number: int) -> dict` (`id X-NN`, `body`, `target`, `status`).

- [ ] **Step 1: Failing tests** на фейковых `SessionView`/`DiscoveryReply` (как в `tests/test_lite_gate_repair.py`, `tests/test_lite_interview_chat.py`): `test_question_from_code_20` (вид, key, текст, `data.prompt` содержит `<<ответ человека>>` и правила полей); `test_topup_after_incomplete_skips_marked`; `test_repair_order_criterion_traces_verdict`; `test_verdict_reply_builds_x_entry_to_feasibility_holder`; `test_two_verdicts_get_distinct_numbers` (Review Focus); `test_none_when_all_marked`; `test_marks_roundtrip_and_clear`; `test_coverage_lists_required_then_optional`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать; `gate_repair` — только вынос `verdict_entry`.
- [ ] **Step 4:** Новые тесты и `tests/test_lite_gate_repair.py` без правок — PASS.
- [ ] **Step 5:** Commit `dfl: вопросы интервью без терминала — вычисление и применение (#766 срез 3)`.

### Task 3: `dfl run` без терминала — постановка вопроса интервью, проход, терминал побеждает

**Files:**
- Modify: `lite/dfl.py` (`run`, новый `_remote_interview`, `_converse`, `answer`)
- Test: `tests/test_lite_dfl_interview_remote.py`

**Interfaces:**
- Consumes: Tasks 1–2; `_waiting_run`, `_stopped_incomplete`, `_sender`, `_discovery_status`, `load_session`, `_upstream_text`.
- Produces: `def _remote_interview(target: user_targets.UserTarget, subject: str | None, role: str, code: int) -> int` — зовётся в `run` после шага в ветке без разговора (не `chatting`); алгоритм:
  1. Прогон (`_waiting_run(..., incomplete=True)`); нет — вернуть `code`.
  2. Цикл: `next_question(...)`; есть ответ `pending.take(rd, q)` → `apply_reply` (+ `mark` для добора/починки; отказ discovery — `DflError` с причиной) и снова; ответа нет → `pending.post(rd, q, data)`; для добора/починки `say(pending.wait_line(q))`; вернуть 0.
  3. Вопросов нет: ответ применён и прогон `waiting_interview` (интервью кончилось) или проход исчерпан у `stopped_interview` → `clear_marks`, `_step` один раз, вернуть его код (снова неполный — прежний вывод).
  - `_converse` в начале: `pending.discard(rd)`, `clear_marks(rd)`; `answer(... file ...)` после принятого discovery — то же.
- [ ] **Step 1: Failing tests** (фейки `_discovery_status`/`_discovery_answer`/`load_session`, как в `tests/test_lite_dfl.py`): `test_waiting_question_posted_silently` (stdout байтово как без патча: тот же вывод, что в существующем тесте вопроса; `pending.json` вида `interview-question`); `test_topup_wait_line_and_skip_via_reply` (стоп неполный → wait_line; `dfl reply --id … --text пропустить`; `dfl run` → следующая тема, пропущенная не спрашивается); `test_pass_exhausted_steps_once_and_new_pass_asks_again` (Review Focus); `test_terminal_chat_discards_pending` (Review Focus); `test_no_agent_called` (агент-фейк падает при вызове).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** Новые тесты и `tests/test_lite_dfl*.py` без правок — PASS.
- [ ] **Step 5:** Commit `dfl: интервью без терминала через pending (#766 срез 3)`.

### Task 4: `dfl answer --items f.json --id <id>`

**Files:**
- Modify: `lite/dfl.py` (парсер `answer`, функция `answer_items`), `lite/structure.py` (если нужно — публичная `payload_from_items(text, raw: str, view) -> str` поверх `parse_items`/`form_errors`/`criterion_errors`/`_merged`/`to_payload`, бросает `FormError` с текстом ошибок)
- Test: `tests/test_lite_dfl_answer_items.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `dfl answer --items <файл> --id <id вопроса>` (оба обязательны вместе; `--items` несовместим с `--file`/`--question`/`--supersede` — отказ). Под `run_lock` прогона: записанный вопрос (`pending.load`) отсутствует или его `id` ≠ `--id` → `pending.STALE`/`pending.NO_PENDING`; вид не интервью — отказ. Файл не JSON или нет `text` — отказ с подсказкой формы. `payload_from_items` → `FormError` — отказ `"раскладка не принята: {ошибки}"`. Вид `interview-question` → `send(qid, payload)`; добор → `resend(holder, appended(held, payload))`; починка `criterion:`/`traces:` — пункты обязаны быть обновлениями чинимых id (иначе отказ), группировка по держателю, `resend` каждому (`appended`), затем `mark done`. Отказ discovery — `"discovery не принял ответ ({reason})"`. Успех — `pending.clear(rd)` и `run(cwd, subject, role, chat=False)` (как ветка `--file`).
- [ ] **Step 1: Failing tests:** `test_items_answer_question` (YAML ушёл с назначенным `FR-NN`, `traces` с ref заменён на id); `test_items_stale_id_refused` (Review Focus, discovery не зван); `test_items_bad_form_refused` (неизвестный id → текст `form_errors`); `test_items_topup_appends_to_holder` (прежние записи держателя сохранены); `test_items_repair_criterion_updates_entry_and_marks_done`; `test_items_without_id_refused`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать; `--help` с новыми флагами.
- [ ] **Step 4:** PASS + `tests/test_lite_dfl.py`, `tests/test_lite_structure.py` без правок.
- [ ] **Step 5:** Commit `dfl: answer --items — раскладка скила проверяется dfl (#766 срез 3)`.

### Task 5: `dfl interview --json`, сквозной тест, README

**Files:**
- Modify: `lite/dfl.py` (`interview(..., as_json)` и парсер), `README.md` (раздел про `dfl pending`/`reply`: интервью без терминала — 6–10 строк)
- Test: `tests/test_lite_dfl_interview_remote.py` (дополнить)

**Interfaces:**
- Produces: `dfl interview --json` → `{"run_id", "subject", "question": {"id", "topic", "label", "text"} | null, "coverage": coverage(view), "findings": findings(view), "readiness": view.readiness, "unavailable": str|null}`; без `--json` вывод прежний.
- [ ] **Step 1: Failing tests:** `test_interview_json_shape`; `test_enterprise_interview_end_to_end_without_terminal` — на фейках discovery: новый прогон без терминала → вопрос 1 → `answer --items` → вопрос 2 → `reply пропустить` → конец с неполной темой → добор `answer --items` → находка GC-08 → `answer --items` с критерием → находка GC-05 → `reply да` → шаг дальше; агент-фейк ни разу не вызван; все переходы только через `run/pending/reply/answer`.
- [ ] **Step 2:** FAIL. **Step 3:** Реализовать. **Step 4:** PASS, полный `pytest`, `ruff`.
- [ ] **Step 5:** Commit `dfl: interview --json и сквозной путь интервью без терминала (#766 срез 3)`.
