# dfl: поля контракта брифа v1.3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** customer-интервью через `dfl` (терминал и без терминала) доходит до брифа, проходящего GC-18…GC-21 контракта v1.3, без ручной правки.

**Architecture:** раскладка ответа (`lite/structure.py`) для фрейма customer выдаёт `category` у NFR, `owns` у P/Q и строки `translations`; чистые правила v1.3 (разбор находок, словари, гипотеза категории, строки из `Preference`, разбор ответа о подтверждающем/сроке, записи владельцев) — новый модуль `lite/brief_v13.py`; терминальная починка (`lite/gate_repair.py`) и путь без терминала (`lite/interview_remote.py`, `lite/pending.py`, `lite/dfl.py`) зовут его в одном порядке.

**Tech Stack:** Python 3.12, PyYAML, pytest, ruff (88), uv.

**Spec:** `docs/superpowers/specs/2026-11-19-dfl-brief-v13-fields-design.md`

## Global Constraints

- Новые поля и вопросы — только для фрейма `customer` (`view.frame == "customer"`); для `engineer` промпт `build_prompt` побайтово прежний, починка прежняя.
- Словари — из vendored `governance/discovery_contract/gate_check.py`, не копией: `REQUIRED_CATEGORIES`, `TRANSLATION_CATEGORIES`, `NUMERIC_CATEGORIES`, `OWNER_DOMAINS`, `CONSTRAINT_PREFIXES` (если имени нет — экспортировать нельзя: vendored файл не правится; тогда константа в `brief_v13` с тестом равенства множеству из `gate_check`).
- Столбцы строки таблицы в payload discovery: `words, category, constraint, assumption, confirmer, due`; агент/скил пишет только первые четыре; `confirmer`/`due` — только из ответа человека (решение А); в ячейках нет `|`, `\n`, `\r`.
- Значения `category` NFR — строчные латинские слова `^[a-z]+$`; `owns` — подмножество `process, data, quality, operations`. В записи discovery поля `category`/`owns` — списки.
- Дата срока: ответ `YYYY-MM-DD` или `ДД.ММ.ГГГГ` → запись `YYYY-MM-DD`; дата раньше сегодняшней или нет даты — переспросить (без терминала — отказ reply).
- Порядок починки: GC-08, GC-13, GC-05 (как есть), затем: категория NFR → `perf` → `cost` → владельцы (`process, data, quality, operations`) → таблица (строки из `Preference` без вопроса, затем вопрос по недостающей обязательной категории) → один вопрос о подтверждающем и сроке.
- Новые строки — русские, без `i18n.t` (enterprise только по-русски); без новых зависимостей.
- Не править: authority-root пути (`contracts/authority-root/v1/paths.env`), `governance/discovery_contract/*` (vendored), `tests/conftest.py`.
- Существующие тесты не правятся, кроме явно названных в задаче.

## Review Focus

1. Человек отвечает на вопрос о владельце «никто»/«неизвестно»/«пока не решили» — открытый `Q` с `owns`, не `P` с телом «никто»; «не знаю» — как везде в `dfl`, пропуск (`interview_chat.SKIP_WORDS`).
2. Ответ о сроке «до конца месяца», «01.12» (без года), прошедшая дата — переспрос, а не запись мусора в `due`.
3. Повторный `dfl run` после частичной починки не дублирует строки таблицы и записи владельцев (идемпотентность по `words`+`constraint` и по домену).
4. Слова заказчика с `|` или переводом строки в `Preference` — строка таблицы без них (discovery откажет весь ответ).
5. Скил без терминала шлёт `translations` с `ref` нового пункта — `ref` заменяется id, как в `traces`.

---

### Task 1: Раскладка — `category`, `owns`, `translations` (customer)

**Files:**
- Modify: `lite/structure.py` (`build_prompt`, `parse_items`/`_parse_item`, `form_errors`, `to_payload`, `payload_from_items`, `_ENTRY_FIELDS`, `merge_entry`, `entry_as_item`)
- Test: `tests/test_lite_structure.py`

**Interfaces:**
- Produces:
  - `parse_items(raw) -> tuple[list[dict], str | None]` — без изменения сигнатуры; пункт может нести `category: list[str]` (NFR), `owns: list[str]` (P, Q); агент шлёт их строкой через запятую или списком — нормализуется в список.
  - `parse_translations(raw: str) -> list[dict[str, Any]]` — строки `translations` из того же JSON (нет ключа — `[]`); `FormError` на нарушение формы.
  - `translation_errors(rows, items, view) -> list[str]` — словарь категории, `constraint` (список) — id записанных `FR/NFR/CON/OUT` или `ref` нового пункта этого ответа такого префикса, непустые `words`, нет `|`/`\n` в ячейках, нет ключей `confirmer`/`due`.
  - `to_payload(text, items, view, rows: list[dict] | None = None) -> str` — YAML с `translations` (ref → id, ключи строго `words, category, constraint, assumption`), если `rows`.
  - `payload_from_items(text, raw, view) -> str` — включает `translations` из `raw`.
  - `structure_answer` — строки агента идут в `to_payload`; повтор агента и на ошибки строк.
  - `V13_FRAME = "customer"`.

- [ ] **Step 1: Тесты (падают)** в `tests/test_lite_structure.py`:
  - `test_engineer_prompt_unchanged` — `build_prompt` для engineer-вида равен снимку, взятому до правки (снимок — строка в тесте, получить запуском на `origin/master`).
  - `test_customer_prompt_mentions_v13_fields` — в промпте customer есть `"category"`, `"owns"`, `"translations"`, все `REQUIRED_CATEGORIES`, и нет `"confirmer"`.
  - `test_nfr_category_normalized` — `"category": "perf, cost"` → `["perf", "cost"]`; `"Perf"` → `FormError`-причина в `form_errors` (не `^[a-z]+$`).
  - `test_owns_only_p_and_q` — `owns` у FR отбрасывается; `owns: ["finance"]` у P → ошибка формы.
  - `test_translations_ref_replaced_by_id` — строка с `constraint: ["new-1"]` и новый `FR` с `ref: "new-1"` → в YAML `constraint: ["FR-0N"]` (следующий номер).
  - `test_translation_pipe_in_words_is_form_error`, `test_translation_unknown_category_is_form_error`, `test_translation_confirmer_from_agent_is_form_error`, `test_translation_constraint_to_g_is_form_error`.
  - `test_payload_from_items_carries_translations` — JSON `{"text","items","translations"}` → YAML с `translations`.
  - `test_engineer_translations_refused` — для engineer `translations` в ответе агента — ошибка формы (discovery откажет).
- [ ] **Step 2:** `uv run --frozen --group governance pytest -q tests/test_lite_structure.py` — новые падают.
- [ ] **Step 3: Реализация.** Блок правил v1.3 в промпт добавляется отдельной строковой вставкой только при `view.frame == V13_FRAME` (engineer — та же строка без вставки); в схему JSON-примера — `"category"`, `"owns"` и `"translations"` только в customer-варианте. В `_ENTRY_FIELDS` — `("category", "category")`, `("owns", "owns")` (имена записи discovery — строчные). Словари — из `gate_check` (Global Constraints).
- [ ] **Step 4:** тот же pytest + `tests/test_lite_interview_chat.py tests/test_lite_dfl_answer_items.py tests/test_lite_gate_repair.py` — зелёные; `uv run --frozen ruff check lite tests && uv run --frozen ruff format --check lite tests`.
- [ ] **Step 5: Commit** `dfl: раскладка customer выдаёт category, owns и строки translation table (#813)`.

### Task 2: `lite/brief_v13.py` — правила v1.3 без ввода-вывода

**Files:**
- Create: `lite/brief_v13.py`
- Modify: `lite/gate_repair.py` (`appended` — слияние `translations`)
- Test: `tests/test_lite_brief_v13.py`, `tests/test_lite_gate_repair.py` (новый тест `appended`)

**Interfaces:**
- Consumes: Task 1 (`to_payload(..., rows)`, поля записей `category`/`owns`).
- Produces (все чистые):
  - `@dataclass(frozen=True) class Gaps: no_category: list[str]; numeric: list[str]; owners: list[str]; table_empty: bool; table_categories: list[str]; unconfirmed: bool` и `gaps(findings: list[str]) -> Gaps` — разбор строк находок GC-18…GC-21 (форматы — `gate_check.check_translation_table/check_numeric_targets/check_owners`; `numeric` в порядке `perf, cost`; `owners` в порядке `OWNER_DOMAINS`; `unconfirmed` — есть находка «нет подтверждающего» или «нет срока»).
  - `DOMAIN_LABELS = {"process": "процесс", "data": "данные", "quality": "качество", "operations": "эксплуатация"}`; `NUMERIC_QUESTIONS = {"perf": "Есть ли ожидания по скорости ответа и объёму работы?", "cost": "Есть ли ожидания по стоимости и бюджету?"}`.
  - `category_hypothesis(body: str) -> list[str]` — словарь ключевых основ (`perf`: скорост, быстр, секунд, задержк, ожидан, объём, нагрузк; `cost`: стоим, бюджет, цен, расход, денег; `security`: доступ, парол, шифр, утечк; `compliance`: регламент, закон, аудит, соответств; `safety`: вред, опасн; `availability`: доступност, простой, отказ) → категории в этом порядке; ничего — `[]`.
  - `parse_categories(text: str, hypothesis: list[str]) -> list[str] | None` — согласие (`gate_repair._agrees`) → `hypothesis`; иначе слова (русские основы как выше или латинские коды) → категории; ничего не узнано — `None`.
  - `UNKNOWN_WORDS = frozenset({"неизвестно", "никто", "нет", "не назначен", "не назначено", "пока нет", "пока не решили", "не решили"})` — незнание (сравнение после `strip().casefold()` и снятия конечной пунктуации); «не знаю» сюда НЕ входит: это пропуск (`interview_chat.SKIP_WORDS`).
  - `is_unknown(text: str) -> bool`.
  - `owner_entries(domain: str, text: str, view) -> list[dict]` — `is_unknown(text)` → `[{"id": next Q, "body": f"Кто владелец: {label}?", "owns": [domain], "owner_role": "analyst", "blocking": False}]`; иначе `[{"id": next P, "body": f"{text} — владелец ({label})", "owns": [domain]}]`. Если в сессии уже есть P/открытый Q с этим `owns` — `[]`.
  - `derived_rows(view) -> list[tuple[str, dict]]` — `(holder, row)` для записей с `Preference`: FR→`делать`, OUT→`не делать`, NFR c perf/cost→`стоить`, NFR/CON c compliance→`доказывать`, прочие пропускаются; `words` — `Preference` без `|`/`\n` (заменить на пробел), `constraint=[eid]`, `assumption` — `Acceptance` или `Target`; строки, уже есть в ответе держателя (те же `words`+`constraint`), пропускаются.
  - `parse_confirmation(text: str, today: date) -> tuple[str, str] | str` — `(confirmer, "YYYY-MM-DD")` или причина отказа («нужна дата: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ», «дата уже прошла»); confirmer — текст без даты и разделителей, пусто — причина «кто подтверждает?».
  - `confirmed_rows(rows: list[dict], confirmer: str, due: str) -> list[dict]` — строкам с `assumption` без `confirmer`/`due` дописать их.
  - `gate_repair.appended(held, payload)` — сливает `translations`: строки держателя, затем новые; строка с теми же (`words`, `tuple(constraint)`) заменяется новой.
- [ ] **Step 1: Тесты (падают)** `tests/test_lite_brief_v13.py`: `test_gaps_parses_live_findings` (вход — 11 строк находок живого прогона из `devtools#813`: GC-18 «секции нет», GC-20 «Category: cost/perf», «NFR-01…04 нет **Category**», GC-21 четыре домена → `table_empty`, `numeric == ["perf","cost"]`, `no_category == ["NFR-01",…,"NFR-04"]`, `owners` — четыре в порядке); `test_category_hypothesis_examples` («Экспорт 500 слов за 2 секунды» → `["perf"]`, «Не дороже 300 ₽ в месяц» → `["cost"]`, «Красивый интерфейс» → `[]`); `test_parse_categories_agree_and_words`; `test_owner_unknown_is_open_q` (параметры: «неизвестно», «никто», «Пока не решили.»); `test_dont_know_is_not_unknown` («не знаю» → `is_unknown` ложно); `test_owner_named_is_p`; `test_owner_already_present_no_entries`; `test_derived_rows_maps_prefixes_and_skips_existing`; `test_derived_rows_strip_pipe_and_newline`; `test_parse_confirmation` (`"владелец продукта, 2026-12-01"` → `("владелец продукта", "2026-12-01")`; `"Иван, 01.12.2026"` → `("Иван", "2026-12-01")`; `"до конца месяца"` → причина; прошедшая дата → причина); `test_confirmed_rows_only_assumptions`; `test_vocab_matches_gate_check`. В `tests/test_lite_gate_repair.py` — `test_appended_merges_translations_by_words_and_constraint`.
- [ ] **Step 2:** pytest этих файлов — падают.
- [ ] **Step 3:** реализация по Interfaces.
- [ ] **Step 4:** pytest зелёный, ruff чистый.
- [ ] **Step 5: Commit** `dfl: правила контракта v1.3 — разбор находок, гипотезы, строки таблицы (#813)`.

### Task 3: Терминальная починка GC-18…GC-21

**Files:**
- Modify: `lite/gate_repair.py` (`repair` + новые `_fill_categories`, `_fill_numeric`, `_fill_owners`, `_fill_table`, `_fill_confirmation`)
- Test: `tests/test_lite_gate_repair.py`

**Interfaces:**
- Consumes: Task 2 (`gaps`, `category_hypothesis`, `parse_categories`, `owner_entries`, `derived_rows`, `parse_confirmation`, `confirmed_rows`, `appended`), Task 1 (`structure_answer`, `to_payload`).
- Produces: `repair(...)` — сигнатура прежняя; после GC-05 — шаги в порядке Global Constraints, только если `load(session).frame == "customer"`. Каждый шаг перечитывает `view = load(session)` перед правкой. Держатели: NFR — последний отвеченный вопрос темы `nfr` (иначе последний отвеченный); P/Q — темы `personas`; строки — держатель первого id в `constraint`. Ответ на `perf`/`cost` и на вопрос по категории таблицы раскладывается `structure_answer` с `Question(<держатель>, "nfr"|<тема по категории>, <вопрос>)`, `may_ask=False`; ответ `brief_v13.is_unknown` для `perf`/`cost` — NFR `{"category": [kind], "Target": "значение из Q-NN"}` + новый `Q` `owner_role: analyst`. Не починено — в общий счёт `GATE_LEFT`.
- [ ] **Step 1: Тесты (падают)** — фейки `ask` (очередь ответов), `agent` (фиксированный JSON), `resend` (запись вызовов), `load` (меняющийся `SessionView`): `test_category_confirmed_by_yes` (гипотеза из `category_hypothesis` в вопросе; «да» → resend с `category`); `test_category_named_in_words`; `test_numeric_unknown_creates_nfr_and_q`; `test_numeric_answer_structured_into_nfr`; `test_owner_named_and_unknown` (два домена); `test_table_rows_derived_without_question` (нет `ask` для строк из `Preference`); `test_table_missing_category_asks`; `test_confirmation_one_question_for_all_rows` (одна реплика `ask` → `confirmer`/`due` во всех строках с допущением); `test_confirmation_bad_date_reasks_then_skip`; `test_engineer_frame_no_v13_steps`; `test_skip_counts_in_gate_left`.
- [ ] **Step 2:** pytest `tests/test_lite_gate_repair.py` — новые падают.
- [ ] **Step 3:** реализация; тексты вопросов: категория — `«{body}»: категория — {hypothesis}? («да» — так; иначе: скорость, стоимость, безопасность, доступ, регламент…; «пропустить»)` (без гипотезы — вопрос без «да»); владелец — `Кто отвечает за {label}? (имя или роль; «неизвестно» — запишу открытым вопросом; «пропустить»)`; `perf`/`cost` — `NUMERIC_QUESTIONS[kind]` + ` («неизвестно» — заведу вопрос исследования; «пропустить»)`; таблица — `Что заказчик говорил о том, что система должна «{category}»? («пропустить» — оставить)`; подтверждение — `Кто подтвердит наши допущения и к какой дате? (например: «владелец продукта, 2026-12-01»; «пропустить»)`, вступление — список допущений по строкам.
- [ ] **Step 4:** pytest `tests/test_lite_gate_repair.py tests/test_lite_interview_chat.py` зелёный, ruff.
- [ ] **Step 5: Commit** `dfl: починка GC-18…GC-21 в терминале (#813)`.

### Task 4: Без терминала — вопросы `interview-repair` v1.3

**Files:**
- Modify: `lite/interview_remote.py` (`_repairs`, `apply_reply`), `lite/pending.py` (`_check_interview`), `lite/dfl.py` (`_remote_pass`, `_send_items`, `_resend_parts`, `_apply_items`)
- Test: `tests/test_lite_interview_remote.py`, `tests/test_lite_dfl_interview_remote.py`, `tests/test_lite_dfl_answer_items.py`, `tests/test_lite_pending_interview.py`

**Interfaces:**
- Consumes: Task 2 целиком; Task 3 — тексты вопросов (константы в `gate_repair`, переиспользовать, не копировать).
- Produces:
  - ключи `interview-repair` (после GC-05, порядок Global Constraints): `category:<NFR-NN>`, `nfr:perf`, `nfr:cost`, `owner:<domain>`, `table:<категория>`, `assumptions`; `data` несёт `hypothesis` (категория), `rows` (допущения для `assumptions`), `holder`, `prompt` (для `nfr:*`, `table:*` — `build_prompt` с целью вопроса).
  - `pending._check_interview`: ключи с префиксом `category:`, `owner:` и ключ `assumptions` принимают любой непустой текст (как `verdict:`).
  - `apply_reply`: `category:*` — `parse_categories` (None → `ValueError` с подсказкой словаря); `owner:*` — `owner_entries`; `assumptions` — `parse_confirmation(text, date.today())` (причина → `ValueError`), затем `confirmed_rows` по держателям; `nfr:*`/`table:*` + не пропуск → `ValueError(NOT_CONTENT)`; `is_unknown` для `nfr:*` через reply — запись NFR+Q как в терминале; `_check_interview` для `nfr:*` пускает пропуск и `is_unknown`, иной текст — отказ «ответ по существу — через dfl answer --items».
  - `_remote_pass`: до `next_question` — если `gaps(...).table_empty or table_categories` и отметки `table:auto` нет — переслать `derived_rows` держателям, отметить `table:auto` `done`, перечитать статус (одна итерация цикла).
  - `_send_items`: `nfr:*`, `table:*` — как добор: записи и строки из `payload_from_items` дописываются держателю `data.holder`.
  - `_resend_parts(state, parts: list[tuple[str, str, list[dict], list[dict]]], view)` — четвёртый элемент — строки `translations` (у прежних вызывающих `[]`).
- [ ] **Step 1: Тесты (падают):** `tests/test_lite_interview_remote.py` — `test_v13_keys_order` (находки живого прогона → первый ключ `category:NFR-01`; при отметках `done` всех категорий — `nfr:perf`, затем `nfr:cost`, `owner:process`…, `table:…`, `assumptions`); `test_v13_skipped_for_engineer`. `tests/test_lite_pending_interview.py` — `test_owner_and_assumptions_accept_text`, `test_nfr_key_refuses_text_accepts_unknown`. `tests/test_lite_dfl_interview_remote.py` — `test_owner_reply_writes_p_with_owns`, `test_owner_unknown_reply_writes_open_q`, `test_assumptions_reply_bad_date_refused_question_kept`, `test_table_auto_rows_sent_once` (второй `dfl run` не шлёт повторно), `test_category_reply_unknown_word_refused`. `tests/test_lite_dfl_answer_items.py` — `test_nfr_perf_items_appended_with_translations`.
- [ ] **Step 2:** pytest этих файлов — падают.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** pytest `tests/test_lite_*.py` зелёный, ruff.
- [ ] **Step 5: Commit** `dfl: починка GC-18…GC-21 без терминала (#813)`.

### Task 5: Сквозной тест, документация, попутное

**Files:**
- Test: `tests/test_lite_dfl_interview_remote.py` (сквозной), `tests/test_governance_spec_loop.py`
- Modify: `governance/spec_loop.py` (`recover_wave_run_from_github` — слаг лениво), `README.md` (раздел «`dfl pending` и `dfl reply`»), `docs/darkfactory-for-colleagues.md` (раздел «Без терминала»), `TODO.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- [ ] **Step 1: Тесты (падают):** `test_customer_v13_repair_end_to_end_without_terminal` — по образцу `test_enterprise_interview_end_to_end_without_terminal`: фейк-сессия, находки которой пересчитываются из её записей и строк (`category`/`owns`/`translations`, по правилам `brief_v13.gaps` в обратную сторону — фейк выдаёт строки находок в формате `gate_check`), проход: `category:*` → `reply`, `nfr:perf` → `answer --items`, `nfr:cost` → `reply «неизвестно»`, `owner:*` ×4 → `reply`, авто-строки, `table:*` → `answer --items`, `assumptions` → `reply`; итог — находок GC-18…GC-21 нет, фейк агента не вызывался. `test_recover_with_ws_id_skips_slug` — `recover_wave_run_from_github(subject="", requested_ws_id="x-20260907", …)` не поднимает `SpecLoopError` из слага.
- [ ] **Step 2:** падают.
- [ ] **Step 3:** реализация; README и памятка — по строке на новые вопросы (`category`/`owner`/`assumptions` — `dfl reply` текстом; `nfr`/`table` — `dfl answer --items`); `TODO.md` — в разделе dfl строка `- [ ] короткий набор вопросов customer-фрейма для фичи (devtools#816) @blocked_by:todo://discovery/customer-frame-short-set`.
- [ ] **Step 4:** полный `uv run --frozen --group governance pytest -q -p no:cacheprovider` зелёный; ruff.
- [ ] **Step 5: Commit** `dfl: сквозной тест починки v1.3, документация, ожидание discovery#86 (#813)`.
