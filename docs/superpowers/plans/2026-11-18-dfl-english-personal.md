# dfl по-английски: личный путь (#769 срез 1) — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dfl run --lang en` ведёт личный путь целиком по-английски — экран и
артефакты, — не меняя русский режим.

**Architecture:** Каталог строк `lite/i18n.py` (ключ — русский шаблон,
перевод — `lite/i18n_en.py`), язык вызова выставляется до первого вывода, язык
прогона хранится в `PersonalRun.language`. Тексты для агентов и артефактов
получают язык явным параметром (`lang: str = "ru"`), промпты — английские
файлы-варианты, edge-check — каталог контрактов `contracts/edge-check/v1-en/`.

**Tech Stack:** Python 3.13, uv, pytest, ruff (88), pyrefly.

**Spec:** `docs/superpowers/specs/2026-11-18-dfl-english-personal-design.md`
(одобрен владельцем 18.11; учтено ревью
`../_cowork_output/2026-11-18-dfl-english-personal-design-review.md`).

## Global Constraints

- Умолчание — `ru`; русский вывод для прежних входов байт в байт прежний.
- Существующие тесты не меняются (кроме случаев, когда тест закрепляет
  точное множество принимаемых слов — тогда расширить, не ослабить).
- Не трогать `governance/df_config.py` и любые пути из
  `contracts/authority-root/v1/paths.env`.
- Машинная грамматика не переводится: id (`G-`, `FR-`, `D-`, `DT-`, `BEH-`),
  `#### D-NN:`, `### TASK-NNN:`, `P2 | TODO   Est:`, `Source:`, `- [ ]`,
  `**checked_by**`, `scenarios:`, `depends_on:`, `type: implement`,
  `owner: dev`, маркеры `comment_marker`/`issue_marker`, `_WAIVED`.
- Английские слова ответа принимаются **в обоих режимах**:
  `next`/`yes`/`ok`/`done` (как «дальше»/«да»/«ок»), `remove`/`drop`/
  `accept`/`keep` (удалить/снять/принять/оставить), `own:`/`disagree:`,
  `/stop`, `skip`/`don't know`, `fix:`, `passed`/`failed`/
  `passed with caveat` (вердикты ручных проверок).
- Порядок языка вызова: `--lang` → `DFL_LANG` → язык продолжаемого прогона →
  `ru`. Сохранённый язык прогона сильнее флага/env. Нет поля — `ru`.
  Недопустимое значение флага/env — отказ код 2 по-русски; в run.json —
  `ru` + предупреждение.
- Enterprise-путь, `--help`, README/руководство (кроме короткого раздела
  про `--lang`) — не в этом срезе.
- Перевод — не глобальной заменой по готовому тексту: шаблон переводится
  до `.format()`, артефакт рендерится сразу на нужном языке.

## Review Focus

1. Старый прогон без поля `language`, продолжение без флага — вывод
   прежний русский (Task 2).
2. Английский прогон, `dfl reply` с «дальше» / русский прогон с `next` —
   оба принимаются (Task 3).
3. `--lang de` или `DFL_LANG=xx` — отказ код 2 до баннера, ничего не
   создано (Task 2).
4. Английская декомпозиция с `**Criteria:**` / `## Checks` проходит
   `decomposition_problems`, английский `tasks.md` читается
   `run_progress.read_tasks` и `run_resume.task_progress` (Task 5).
5. Перевод шаблона потерял плейсхолдер `{name}` — тест каталога падает
   до рантайма (Task 1).

---

### Task 1: Ядро каталога и язык прогона

**Files:**
- Create: `lite/i18n.py`, `lite/i18n_en.py`, `tests/test_lite_i18n.py`,
  `tests/personal_en.py` (тестовые утилиты)
- Modify: `lite/personal/state.py` (`PersonalRun`, `new_personal_run`)

**Interfaces:**
- Produces:
  - `i18n.LANGS: tuple[str, ...] = ("ru", "en")`
  - `i18n.set_language(lang: str) -> None`, `i18n.current() -> str`
  - `i18n.language(lang: str) -> ContextManager[None]` — временно, с
    восстановлением
  - `i18n.t(template: str) -> str` — перевод для текущего языка; в `en`
    шаблон с кириллицей без перевода возвращается как есть и
    записывается в промахи
  - `i18n.misses() -> frozenset[str]`, `i18n.reset_misses() -> None`
  - `i18n.LanguageError(ValueError)`;
    `i18n.resolve(flag: str | None, env: Mapping[str, str],
    stored: str | None) -> str` — порядок из Global Constraints; плохой
    flag/env — `LanguageError`; плохой `stored` — `"ru"`
  - `i18n.stored_language(raw: object) -> tuple[str, bool]` — (язык,
    было ли значение недопустимым) для run.json
  - `i18n_en.EN: dict[str, str]`
  - `PersonalRun.language: str = "ru"`;
    `new_personal_run(..., language: str = "ru")`
  - тест-утилиты `tests/personal_en.py`:
    `cyrillic(text: str, allowed: Iterable[str] = ()) -> list[str]`
    (строки с кириллицей после вычёркивания `allowed`)

- [ ] **Step 1: Failing tests** в `tests/test_lite_i18n.py`:
  - `test_t_returns_template_in_ru`, `test_t_translates_in_en` (временная
    запись в `EN` через monkeypatch), `test_t_records_miss_in_en`;
  - `test_language_context_restores`;
  - `test_resolve_order` — `("en", {"DFL_LANG": "ru"}, None) == "en"`,
    `(None, {"DFL_LANG": "en"}, None) == "en"`,
    `(None, {}, "en") == "en"`, `(None, {}, None) == "ru"`;
    сохранённый сильнее: `("ru", {}, "en") == "en"`;
  - `test_resolve_rejects_bad_flag_and_env` — `LanguageError` для
    `"de"` во флаге и `DFL_LANG=xx`;
  - `test_stored_language_bad_value` — `stored_language("de") ==
    ("ru", True)`, `stored_language(None) == ("ru", False)`;
  - `test_catalog_placeholders_match` — для каждой пары `EN` множество
    `string.Formatter().parse` имён совпадает;
  - `test_catalog_keys_in_code` — каждый ключ `EN` встречается
    литералом (после склейки соседних строк — через `ast`) в `lite/` или
    `governance/`;
  - `test_old_run_json_loads_as_ru` — run.json без `language` →
    `load(...).language == "ru"`.
- [ ] **Step 2:** `uv run pytest tests/test_lite_i18n.py -q` — FAIL.
- [ ] **Step 3:** реализовать; `EN` пока пустой.
- [ ] **Step 4:** тесты PASS; `uv run pytest tests/test_lite_personal*.py -q`
  зелёный.
- [ ] **Step 5:** commit `dfl i18n: каталог строк и язык прогона (#769)`.

### Task 2: Язык вызова, баннер, `status`, `abandon`

**Files:**
- Modify: `lite/dfl.py` (парсер `run`, `main`/`_dispatch`,
  `_require_test_mode`, `_personal`, `status`, `_abandon_personal`,
  `pending_cmd`, `reply_cmd`)
- Test: `tests/test_lite_english_personal.py` (создать)

**Interfaces:**
- Consumes: Task 1.
- Produces: `dfl run --lang {ru,en}`; `_call_language(args, cwd) -> str`
  в `lite/dfl.py` — язык вызова до `_require_test_mode` (продолжаемый
  прогон — `personal_state.active_for(slug, subject)` без побочных
  эффектов); `_test_banner_en(cfg) -> str | None` — английский баннер из
  тех же фактов `cfg` (русский — прежний `df_config.test_banner*`).

- [ ] **Step 1: Failing tests** (через настоящий вход `dfl.main`, фикстуры
  как в `tests/test_lite_dfl.py`):
  - `test_lang_flag_unknown_refused_before_banner` — `--lang de` → код 2,
    в выводе нет строки баннера, прогон не создан;
  - `test_env_lang_unknown_refused` — `DFL_LANG=xx` → код 2;
  - `test_new_run_records_language` — `run --lang en --track personal` →
    `PersonalRun.language == "en"`;
  - `test_continue_without_flag_uses_run_language` — второй вызов без
    флага и env: баннер и первые строки — без кириллицы
    (`personal_en.cyrillic`);
  - `test_stored_language_beats_flag_with_warning` — прогон `en`,
    `--lang ru` → одна строка-предупреждение, вывод английский;
  - `test_old_run_stays_russian` — прогон без поля, вызов без флага —
    вывод равен выводу до изменения (сравнить с `DFL_LANG` не заданным и
    с русским ожидаемым фрагментом из существующих тестов);
  - `test_status_mixed_runs` — активный `en` и завершённый `ru` личные
    прогоны: блок `en` без кириллицы, блок `ru` по-русски, заголовок на
    языке вызова (`ru` без флага).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; переводимые строки этих мест — через `t()` и
  `EN`.
- [ ] **Step 4:** PASS; `uv run pytest tests/test_lite_dfl*.py -q` зелёный.
- [ ] **Step 5:** commit `dfl: --lang и язык вызова до первого вывода (#769)`.

### Task 3: Слова ответа на двух языках

**Files:**
- Modify: `lite/pending.py` (`VERDICT_WORDS`, `REVIEW_DONE_WORDS`,
  `EDGE_*_PREFIX`, `CONFIRM_KEEP_WORDS`, `check`), `lite/review_pause.py`
  (yes/no и слова согласия по виду), `lite/personal/interviewer.py`
  (`YES_WORDS`, `SKIP_WORD`, `_FIX_PREFIX`), `lite/interview_chat.py`
  (`STOP_WORD`, `SKIP_WORDS`, `is_skip`), `lite/edge_resolve.py`
  (`_parse`, префиксы), `governance/manual_checks.py` (разбор вердикта),
  `lite/manual_ask.py` (`is_wait` — оба языка префиксов ожидания)
- Test: `tests/test_lite_i18n_words.py`

**Interfaces:** приём — множества слов из Global Constraints;
`append_correction` пишет префикс языка прогона (`поправить:` / `fix:`),
разбор принимает оба.

- [ ] **Step 1: Failing tests** — параметризованные по словам из Global
  Constraints, по одному тесту на точку разбора:
  `test_review_done_words_both`, `test_confirm_words_both`,
  `test_edge_prefixes_both` (с `ё=е` и регистром, как сейчас),
  `test_manual_verdicts_both`, `test_interview_yes_skip_fix_both`,
  `test_stop_word_both`, `test_is_wait_both_languages`;
  плюс `test_ru_run_accepts_next` — русский прогон, `dfl reply --text next`
  проходит паузу.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** расширить множества/префиксы; подсказки ответа
  (`accepts`, `*_REFUSAL`, `ANSWER_HELP`) — через `t()`, слова языка
  прогона.
- [ ] **Step 4:** PASS; существующие тесты этих модулей зелёные.
- [ ] **Step 5:** commit `dfl: английские слова ответа в обоих режимах (#769)`.

### Task 4: Промпты агентов и edge-check `v1-en`

**Files:**
- Create: `lite/personal/prompts/en/{interviewer,design,decomposition}.md`;
  `contracts/edge-check/v1-en/{instruction.md,response-schema.json,README.md}`,
  `contracts/edge-check/v1-en/rules/{personal-design-vs-brief.yaml,
  personal-decomposition-vs-design-brief.yaml}`; `tests/test_lite_english_prompts.py`;
  английские фикстуры в `tests/personal_en.py`
- Modify: `lite/personal/nodes.py` (`build_prompt`, `revise_prompt`,
  `author_node`, `revise_node`, `_rules_block`), `lite/personal/interviewer.py`
  (`build_prompt`, `_SPEAKERS`, `_AGENT_SPEAKERS`, `CAP_NOTE`),
  `lite/personal/brief.py` (`render_brief` заголовки, `CONTEXT_LABELS`,
  `OTHER_LABEL`, `brief_problems`), `lite/personal/check.py` (`check_node`,
  `_CRITERIA`, `_CHECKS`), `governance/edge_check/prompt.py`
  (`build_prompt`), `governance/edge_check/check.py` (`run_check`),
  `lite/edge_triage.py` (`build_prompt`), `lite/edge_resolve.py`
  (каталог правил), `lite/personal/flow.py` (передача `run.language`)

**Interfaces:**
- `contracts_dir(lang: str) -> Path` в `lite/personal/check.py`
  (`v1` / `v1-en`); используют `check.py`, `nodes.py`, `edge_resolve.py`.
- Новые keyword-параметры `lang: str = "ru"`: `nodes.build_prompt`,
  `nodes.revise_prompt`, `nodes.author_node`, `nodes.revise_node`,
  `interviewer.interview`, `interviewer.build_prompt`, `brief.render_brief`,
  `brief.brief_problems`, `check.check_node`, `edge_check.prompt.build_prompt`,
  `edge_check.check.run_check`, `edge_triage.build_prompt`.
- `_CRITERIA` / `_CHECKS` принимают `**Критерии:**`|`**Criteria:**` и
  `## Проверки`|`## Checks` в обоих режимах.
- Английский промпт декомпозиции просит `**Criteria:**` и `## Checks`;
  промпт дизайна — `## Open questions` вместо `## Открытые вопросы`.
- Фикстуры: `EN_BRIEF_TURN` (JSON-ответ интервьюера), `EN_DESIGN`,
  `EN_DECOMPOSITION` — английский текст с той же грамматикой, что русские
  фикстуры `tests/test_lite_personal_review.py::NODE_TEXT`.

- [ ] **Step 1: Failing tests:**
  - `test_en_prompts_have_no_cyrillic` — для `lang="en"` промпты
    интервьюера, дизайна, декомпозиции, правки, edge-check и сортировки
    (собранные из английских фикстур) — `cyrillic(...) == []`;
  - `test_ru_prompts_unchanged` — для `lang="ru"` результат совпадает с
    вызовом без параметра;
  - `test_v1_en_schema_identical` — байты `response-schema.json` равны `v1`;
  - `test_v1_en_rules_structure_equal` — у двух правил всё, кроме
    текстовых полей (`statement`/`description`/текст правила — по
    фактической схеме YAML), равно `v1`;
  - `test_en_decomposition_passes_problems` — `decomposition_problems`
    на `EN_DECOMPOSITION` пуст;
  - `test_en_brief_render_no_cyrillic` — `render_brief(..., lang="en")`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; английские тексты — перевод русских по
  смыслу, без новых требований к агенту.
- [ ] **Step 4:** PASS; `uv run pytest tests/test_lite_personal*.py
  tests/test_edge_check*.py tests/test_lite_edge*.py -q` зелёный.
- [ ] **Step 5:** commit `dfl: английские промпты и edge-check v1-en (#769)`.

### Task 5: Артефакты — `tasks.md`, PR, ручные проверки

**Files:**
- Modify: `governance/task_bridge.py` (`_render_header`,
  `render_tasks_dt` и его помощники человеческих строк), `lite/personal/tasks.py`
  (`_DRAFT_SENTENCE`/`_PERSONAL_SENTENCE` по языку), `governance/review_remarks.py`
  (`STAGE_LABELS`, `pr_section`), `governance/manual_checks.py`
  (`question`, `OPTIONS`, `issue_title`, `issue_body`, `pr_comment`,
  `summary`), `lite/personal/flow.py` (тело/заголовок PR, передача языка)
- Test: `tests/test_lite_english_artifacts.py`

**Interfaces:** keyword `lang: str = "ru"` у `render_tasks_dt`,
`_render_header`, `render_personal_tasks`, `pr_section`, `question`,
`issue_title`, `issue_body`, `pr_comment`, `summary`.

- [ ] **Step 1: Failing tests:**
  - `test_tasks_md_en_no_cyrillic` — `render_personal_tasks` из
    `EN_DECOMPOSITION`/`EN_DESIGN` с `language="en"`: `cyrillic == []`;
  - `test_tasks_md_en_parsed` — тот же текст: `run_progress.read_tasks`
    видит все задачи, `run_resume.task_progress` возвращает `(0, N)`;
  - `test_tasks_md_ru_byte_identical` — русский вывод равен вызову без
    `lang`;
  - `test_pr_section_en`, `test_manual_issue_and_comment_en` — нет
    кириллицы, маркеры на месте и прежние;
  - `test_personal_pr_body_en` — тело и заголовок PR кода/спеки
    английского прогона (через `FakeOps` из
    `tests/test_lite_personal_flow.py`) без кириллицы.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; enterprise-вызовы не передают `lang`.
- [ ] **Step 4:** PASS; `uv run pytest tests/test_task_bridge*.py
  tests/test_*manual*.py tests/test_lite_personal*.py -q` зелёный.
- [ ] **Step 5:** commit `dfl: английские tasks.md, PR и ручные проверки (#769)`.

### Task 6: Экран личного пути — сценарии 1, 2, 3 (паузы), 7

**Files:**
- Modify: `lite/personal/flow.py`, `lite/personal/interviewer.py`
  (сообщения), `lite/dfl.py` (личная часть, `TRACK_*`, `REVIEW_*`,
  `REFUSALS`), `lite/output.py` (`refusal`), `lite/interview_chat.py`
  (`SEND_HINT`, `BROKEN_INPUT*`, `REFUSED_TEXT`, `TOP_UP_HINT`),
  `lite/pending.py`, `lite/review_pause.py`, `lite/owner_review.py`,
  `lite/i18n_en.py`
- Test: `tests/test_lite_english_personal.py`

- [ ] **Step 1: Failing scenario tests** (вход `dfl.main`, фейковые
  англоязычные агенты из `tests/personal_en.py`; вывод stdout+stderr
  проверяется `cyrillic(out, allowed=<ввод пользователя и тексты
  фейков>) == []`, и `i18n.misses() == frozenset()`):
  - `test_s1_new_run_track_interview_brief`;
  - `test_s2_pause_continue_separate_call` — без флага и env;
  - `test_s3_reply_remarks_and_remove_confirm` — `pending`, `reply` с
    замечаниями, вопрос «Remove …?», ответ `remove`;
  - `test_s7_stop_and_refusal` — штатная остановка (нет tty на брифе) и
    отказ код 2 (`reply` без вопроса).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** обернуть литералы в `t()`, добавить переводы в `EN`.
  Константы модулей остаются русскими шаблонами, перевод — в месте
  использования.
- [ ] **Step 4:** PASS; `uv run pytest tests/test_lite_*.py -q` зелёный.
- [ ] **Step 5:** commit `dfl: английский экран личного пути (#769)`.

### Task 7: Экран — edge-check, код, ручные проверки (сценарии 3-edge, 4, 5)

**Files:**
- Modify: `lite/edge_resolve.py` (личная часть: `resolve_scope`, `_ask*`,
  `_parse`, `_head_lines`, `_option_line`, константы 50–76),
  `lite/manual_ask.py`, `governance/run_resume.py`,
  `governance/run_progress.py` (`UNAVAILABLE`, строки прогресса),
  `governance/auto_tasks.py` (`notice`-тексты личного пути, `TUI_HINT`),
  `governance/decomposition_guard.py` (сообщения `graph_findings`,
  видимые на личном пути), `lite/i18n_en.py`
- Test: `tests/test_lite_english_personal.py`

- [ ] **Step 1: Failing scenario tests** (те же проверки, что в Task 6):
  - `test_s3_edge_question_via_reply` — вопрос `edge`, ответ
    `disagree: …`;
  - `test_s4_edge_resolution_terminal` — находки английские, ответ номером
    и `own: …`;
  - `test_s5_manual_check` — вопрос, ответ `passed with caveat: …`, тело
    issue/комментария (через `FakeOps`) без кириллицы;
  - `test_code_run_progress_en` — прогресс и баннер возобновления.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** обернуть, перевести.
- [ ] **Step 4:** PASS; `uv run pytest tests/test_lite_*.py
  tests/test_run_*.py tests/test_auto_tasks*.py -q` зелёный.
- [ ] **Step 5:** commit `dfl: английский экран edge-check, кода и ручных проверок (#769)`.

### Task 8: Приёмка целиком, документация

**Files:**
- Modify: `tests/test_lite_english_personal.py`, `README.md` (раздел
  «`--lang en`» в части про личный путь: флаг, `DFL_LANG`, язык прогона,
  слова ответа, что не переведено)

- [ ] **Step 1:** `test_s6_status_mixed` (если Task 2 покрыл — ссылка, не
  дубль) и `test_all_scenarios_no_misses` — прогон сценариев 1–7 подряд с
  `reset_misses()` в начале и `misses() == frozenset()` в конце.
- [ ] **Step 2:** `uv run ruff format --check . && uv run ruff check .`;
  `uv run pytest -q -p no:cacheprovider` — полный набор зелёный.
- [ ] **Step 3:** README; commit `docs: dfl --lang en (#769)`.
