# dfl: пауза на просмотр артефакта после этапа — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** необязательная пауза после этапа (личный и enterprise пути): ссылка на документ, замечания в терминале, агент переписывает, edge-check заново, до «дальше».

**Architecture:** общее ядро `lite/review_pause.py` (выбор этапов, экран паузы, журнал замечаний, вопрос об удалённых id, раздел PR). Личный путь — пауза внутри `lite/personal/flow.py`. Enterprise — раннер останавливается статусом `waiting_owner_review` после PASS edge-check волны, цикл замечаний ведёт `lite/dfl.py` (по образцу `stopped_review` → `_resolve_rounds`). Умолчание — поле конфига DarkFactory (authority-root, отдельный PR), флаг `dfl run --review`.

**Tech Stack:** Python 3.12, uv, pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-11-15-dfl-stage-review-pause-design.md` (одобрен владельцем 15.11).

**Предусловие:** реализация начинается после мержа PR #774 (личный путь); ветка плана перебазируется на `origin/master` с ним.

## Global Constraints

- Без включённой паузы поведение обоих путей побайтово прежнее; существующие тесты не правятся (кроме механических — новое поле с умолчанием).
- Authority-root: `governance/df_config.py` меняет только Task 2, отдельной веткой и PR с мержем человеком; остальные задачи authority-root не трогают.
- Этапы: личный — `brief`, `design`, `decomposition`; enterprise — `charter`, `requirements`, `behaviour-spec`, `design`, `acceptance`, `decomposition`.
- «Дальше» — только целая строка без регистра: `дальше`, `да`, `ок`, `1`.
- Пауза — после PASS (или принятия вопреки) edge-check, до следующего шага; кругов без лимита.
- Без терминала — стоп с подсказкой, не пропуск; `/стоп`, Ctrl-C, EOF на паузе — пауза (код 0).
- Строки для человека — по-русски; длина строки 88; ruff + pyrefly чисто.

## Review Focus

1. Ctrl-C после записи замечаний, до переписывания → следующий `dfl run` доигрывает круг по записанным замечаниям, не спрашивая их снова.
2. Агент по замечанию удалил пункт с id → вопрос «удалить <id>? 1. да · 2. нет»; «нет» → файл восстановлен, агенту повтор с «<id> не удалять».
3. Enterprise «дальше» без замечаний → резюм без сброса `commit/gate-candidate/edge` (без повторной платной проверки); с замечаниями → сброс и полная перепроверка волны.
4. Прогон, запущенный не через `dfl` (`spec_loop` напрямую), не останавливается на паузе (пустой список).
5. Имя этапа, чужое текущему пути, в конфиге молча не действует; во флаге — отказ с перечнем допустимых.

---

### Task 1: ядро паузы

**Files:**
- Create: `lite/review_pause.py`
- Test: `tests/test_lite_review_pause.py`

**Interfaces:**
- Produces:
  - `PERSONAL_STAGES = ("brief", "design", "decomposition")`, `ENTERPRISE_STAGES = ("charter", "requirements", "behaviour-spec", "design", "acceptance", "decomposition")`, `DONE_WORDS = frozenset({"дальше", "да", "ок", "1"})`, `REMARKS = "review-remarks.jsonl"`.
  - `class ReviewError(Exception)`.
  - `def parse_flag(value: str, stages: tuple[str, ...]) -> tuple[str, ...]` — `"all"` → все `stages`, `"none"`/`""` → `()`, иначе список через запятую; неизвестное имя → `ReviewError` с перечнем допустимых.
  - `def from_config(names: tuple[str, ...], stages: tuple[str, ...]) -> tuple[str, ...]` — пересечение с `stages` (чужие имена молча отбрасываются).
  - `@dataclass(frozen=True) class Remark: stage: str; text: str; applied: bool`
  - `def pending(run_dir: Path, stage: str) -> list[Remark]`, `def record(run_dir: Path, stage: str, text: str) -> None` (applied=False), `def mark_applied(run_dir: Path, stage: str) -> None`, `def all_remarks(run_dir: Path) -> list[Remark]` — журнал JSON-строками.
  - `def ask(stage_label: str, paths: list[Path], *, read: Read) -> str | None` — печатает экран §4.2 спеки (строка «этап «…» готов и прошёл проверку — посмотрите:», ссылки `file://<abs>` по одной, подсказка), читает `read_answer(read, quick=lambda s: s.strip().casefold() in DONE_WORDS)`; возвращает `""` для «дальше», текст замечаний, `None` для `/стоп`/EOF.
  - `def confirm_removals(ids: list[str], *, read: Read) -> set[str]` — по каждому id вопрос «удалить <id>? 1. да · 2. нет»; возвращает разрешённые; `None` от чтения → пустое множество.
  - `def pr_section(run_dir: Path) -> str` — `## Замечания владельца` со списком «этап: текст» (applied), пусто — `""`.

- [ ] **Step 1: тесты**: `test_parse_flag_all_none_list`, `test_parse_flag_unknown_lists_allowed`, `test_from_config_drops_foreign`, `test_journal_pending_then_applied`, `test_ask_done_word_single_line` (параметризация по `DONE_WORDS` и « Дальше »), `test_ask_yes_but_is_remark` («да, но добавь X» + пустая строка → текст), `test_ask_prints_file_links` (вывод содержит `file:///…/20-design.md`), `test_ask_stop_returns_none`, `test_confirm_removals_yes_no`, `test_pr_section_lists_applied`.
- [ ] **Step 2:** `uv run pytest tests/test_lite_review_pause.py -q` → FAIL (модуля нет).
- [ ] **Step 3:** реализация (ссылки печатаются через `auto_tasks.notice`, чтобы быть видимыми и под перехватом вывода; ввод — `lite.interview_chat.read_answer`).
- [ ] **Step 4:** PASS. **Step 5:** commit `feat(review): ядро паузы на просмотр этапа (#768)`.

### Task 2: поле конфига `review.pause_after` (authority-root, отдельный PR)

**Files:**
- Modify: `governance/df_config.py` (`_TOP`, `_TABLES`, `DfConfig`, `parse`, валидатор списка строк, `_GETTERS`)
- Test: `tests/test_df_config.py` (или существующий файл тестов df_config)

**Interfaces:**
- Produces: `DfConfig.review_pause_after: tuple[str, ...] = ()`; таблица `[review]` необязательна (как `app`); `pause_after` — список строк, иначе `ConfigError`. Имена здесь не сверяются с этапами (это `review_pause.from_config`).

- [ ] **Step 1: тесты**: `test_review_table_optional`, `test_review_pause_after_parsed`, `test_review_pause_after_not_list_rejected`, `test_review_unknown_key_rejected`.
- [ ] **Step 2–4:** FAIL → реализация → PASS (+ существующие тесты df_config зелёные).
- [ ] **Step 5:** commit на ОТДЕЛЬНОЙ ветке `feat/df-config-review-pause` от `origin/master`, `feat(df_config): [review] pause_after (#768)`; PR с пометкой «authority-root — мержит человек». Остальные задачи не ждут его: читают поле через `getattr(cfg, "review_pause_after", ())`.

### Task 3: флаг `--review` и выбор этапов прогона

**Files:**
- Modify: `lite/dfl.py` (парсер `run`, `run(..., review=None)`, передача в личный и enterprise путь), `lite/personal/state.py` (`PersonalRun.review_pause: tuple[str, ...] = ()` — сохраняется списком), `governance/run_state.py` (`RunState.review_pause: list[str] = field(default_factory=list)`, `new_run(..., review_pause=())`), `governance/spec_loop.py` (аргумент `--review-pause a,b` → `new_run`)
- Test: `tests/test_lite_dfl_review.py`, дополнение `tests/test_governance_run_state.py`

**Interfaces:**
- Consumes: `parse_flag`, `from_config`, `PERSONAL_STAGES`, `ENTERPRISE_STAGES` (Task 1).
- Produces: `def resolve_review(flag: str | None, stages: tuple[str, ...]) -> tuple[str, ...]` в `lite/dfl.py` — флаг сильнее конфига (`df_config.load()` + `getattr(..., "review_pause_after", ())`); выбор фиксируется при создании прогона и далее не меняется (на возобновлении флаг, расходящийся с записанным, — отказ с подсказкой, как `--track`).

- [ ] **Step 1: тесты**: `test_flag_overrides_config`, `test_config_default_used`, `test_flag_unknown_stage_refused`, `test_personal_run_stores_review`, `test_enterprise_argv_carries_review_pause` (argv `spec_loop` содержит `--review-pause design`), `test_run_state_review_pause_default_empty` (старый леджер без поля читается), `test_resume_with_different_review_refused`, **RF-4** `test_spec_loop_direct_run_has_no_pause` (новый прогон без `--review-pause` → `review_pause == []`).
- [ ] **Step 2–4:** FAIL → реализация → PASS (+ `tests/test_lite_dfl*.py`, `tests/test_governance_spec_loop.py`, `tests/test_governance_run_state.py`).
- [ ] **Step 5:** commit `feat(dfl): --review — выбор этапов с паузой (#768)`.

### Task 4: пауза на личном пути

**Files:**
- Modify: `lite/personal/flow.py` (`_step_interview` перед переходом к `design`, `_step_design`, `_step_decomposition` перед сменой шага; `_pr_body` — раздел из `pr_section`)
- Test: `tests/test_lite_personal_review.py`

**Interfaces:**
- Consumes: Task 1 (`ask`, `record`, `pending`, `mark_applied`, `confirm_removals`, `pr_section`); `revise_node` (`lite/personal/nodes.py`); `node_guard.weakened` (`lite/node_guard.py:39`); `interview` (`lite/personal/interviewer.py`) и запись правки `{"role": "human", "text": "поправить: …"}` в его журнал.
- Produces: `def _review(ctx, stage: str, files: list[str]) -> bool` в `flow.py` — `True` — «дальше» (шаг продолжается), `False` — стоп/пауза (`run.stop_reason` записан для стопа; для паузы — 0 как у прерванного интервью).

Логика `_review` для `design`/`decomposition`: сначала `pending` (RF-1: недоигранные замечания применяются без вопроса); иначе `ask`; «дальше» → `True`; текст → `record`; `revise_node(..., findings="нет", decisions="- замечание владельца: <текст>")`; `weakened(before, after, set(), set())` непусто → `confirm_removals`; неразрешённые → файл восстановлен, повтор `revise_node` с «<id> не удалять» (один повтор; снова ослабляет → стоп «правка удаляет <id> — уточните замечание»); затем `_node` (edge-check + #762) заново; `mark_applied`; снова `ask`. Для `brief`: замечание → запись «поправить: <текст>» в журнал интервью → `interview(...)` (пересборка и «Верно?») → `00-brief.md` перезаписан → `mark_applied` → снова `ask`. `read` вернул `None` → пауза (код 0, «продолжить: dfl run»); `read` недоступен (нет терминала) → стоп с подсказкой «откройте <ссылка>; замечания — dfl run в терминале».

- [ ] **Step 1: тесты**: `test_no_pause_when_stage_not_selected` (поведение прежнее), `test_done_word_continues`, `test_remark_revises_and_rechecks` (revise вызван с замечанием, `check_node` повторён, пауза показана снова), **RF-2** `test_removal_asked_and_restored_on_no`, **RF-1** `test_pending_remark_applied_on_resume_without_asking`, `test_brief_remark_goes_to_interviewer`, `test_stop_on_pause_is_code_0`, `test_no_tty_stops_with_link_hint`, `test_spec_pr_body_has_owner_remarks`.
- [ ] **Step 2–4:** FAIL → реализация → PASS (+ `tests/test_lite_personal_*.py`).
- [ ] **Step 5:** commit `feat(personal): пауза на просмотр этапа (#768)`.

### Task 5: остановка enterprise-волны на паузе

**Files:**
- Modify: `governance/runner.py` (`_step_edge` после PASS `:4380-4382` и принятия вопреки `:4383-4393`; `reset_ops_for` `:773-786`; `_STOPPED_RESET_OPS` `:840-860`; `resume` `:972`), `governance/spec_loop.py` (`WAVE_PAUSES` `:874` — новый статус не авто-резюмится)
- Test: `tests/test_governance_review_pause.py`

**Interfaces:**
- Produces: статус `WAITING_OWNER_REVIEW = "waiting_owner_review"`; op `review-pause-w<k>` (ключ через `wave_key(state, "review-pause")`) с полями `{"status": "completed"|…, "revised": bool}` — пишет `dfl` (Task 6), читает раннер.
  - `_step_edge`: если узлы текущей волны пересекаются с `state.review_pause` и `review-pause-w<k>` не `completed` → `state.status = WAITING_OWNER_REVIEW`, `save`, `return False` (до `_step_candidate`).
  - `resume` из `WAITING_OWNER_REVIEW`: op `completed` и `revised=False` → `running` без сброса (RF-3: без повторной проверки); `revised=True` → сброс `commit/gate-candidate/edge` волны (как `stopped_review`) и op паузы снова незавершённый (пауза покажется после перепроверки); op не `completed` → отказ «пауза не пройдена — dfl run».

- [ ] **Step 1: тесты**: `test_no_pause_without_review_list` (байтово прежний порядок шагов), `test_pass_with_pause_stops_before_candidate` (нет вызова `propose_from_source`), `test_override_also_pauses`, **RF-3** `test_resume_done_without_remarks_skips_recheck`, `test_resume_revised_resets_and_pauses_again`, `test_spec_loop_does_not_autoresume_waiting_owner_review`, `test_w4_pause_when_either_node_selected`.
- [ ] **Step 2–4:** FAIL → реализация → PASS (+ `tests/test_governance_runner.py`, `tests/test_governance_spec_loop.py`, `tests/test_governance_waves_e2e.py`).
- [ ] **Step 5:** commit `feat(runner): остановка волны на паузе просмотра (#768)`.

### Task 6: цикл замечаний enterprise в `dfl`

**Files:**
- Modify: `lite/dfl.py` (по образцу `_resolve_rounds` `:525` / `_review_run` `:574`: новый цикл `_owner_review_rounds`), `governance/approve_node.py` (`_candidate_body` `:1796` — раздел `pr_section`)
- Test: `tests/test_lite_dfl_owner_review.py`

**Interfaces:**
- Consumes: Task 1; Task 5 (`WAITING_OWNER_REVIEW`, op `review-pause-w<k>`); `RealOps().revise(call, kind, subject, current, findings, decisions)`; `node_guard.weakened`; `_author_steps_for(state)` (`runner.py:669`) — узлы и файлы волны; путь файла `Path(state.target_dir) / state.bundle_dir / filename`.
- Produces: `_owner_review_rounds(run, read) -> int` — пока статус `WAITING_OWNER_REVIEW`: по каждому выбранному узлу волны `pending`/`ask` → «дальше» или замечание → `revise` + гвард ослабления + `confirm_removals` (как в Task 4) → `mark_applied`; затем запись op `review-pause-w<k>` (`completed`, `revised` = были ли применённые замечания в этом круге) и `_step` (resume). W4: оба файла показываются подряд; правка любого → `revised=True` (волна перепроверяется целиком, пауза покажет оба снова).

- [ ] **Step 1: тесты**: `test_waiting_owner_review_shows_wave_links`, `test_done_marks_op_not_revised_and_resumes`, `test_remark_revises_marks_revised`, **RF-1** `test_pending_applied_on_rerun`, **RF-2** `test_enterprise_removal_confirmed`, `test_stop_is_pause_code_0`, `test_no_tty_stop_hint`, `test_candidate_body_has_owner_remarks`.
- [ ] **Step 2–4:** FAIL → реализация → PASS (+ `tests/test_lite_dfl*.py`, `tests/test_governance_approve_node*.py`).
- [ ] **Step 5:** commit `feat(dfl): замечания владельца на паузе enterprise-волны (#768)`.

### Task 7: документация и проверка

**Files:**
- Modify: `README.md` (раздел `dfl`: «Пауза на просмотр этапа» — `--review`, `[review] pause_after`, что видит человек, «дальше», замечания)

- [ ] **Step 1:** абзац README по фактическому поведению.
- [ ] **Step 2:** полный набор `uv run pytest -q` → зелёный (гоняет контроллер); ruff, pyrefly по изменённым файлам → чисто.
- [ ] **Step 3:** commit `docs(dfl): пауза на просмотр этапа (#768)`.

Живая приёмка (владелец): личный прогон с `--review design` — пауза, замечание, правка, «дальше»; enterprise-прогон с `--review requirements` — то же до candidate-PR.
