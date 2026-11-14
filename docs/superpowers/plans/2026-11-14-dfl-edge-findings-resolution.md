# dfl: разбор замечаний edge-check — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** при стопе смысловой проверки `dfl run` сам разбирает замечания: агент правит узел, человек отвечает только на вопросы по существу (варианты, «своё», «не согласен», в `test` — «принять вопреки»), проверка повторяется; автор узла с первого раза получает правила своего ребра.

**Architecture:** правила рёбер — новый модуль `governance/edge_check/author_rules.py`, их подмешивает `ops.author_prompt`. Разбор — пакет `lite/` (сортировка агентом без инструментов, журнал решений, сверка «не ослаблен ли узел», оркестратор), переписывание — новый метод `RealOps.revise` через неизменную границу `author_launch.launch_author`. Принятие вопреки — запись в каталоге прогона, которую читают `runner._step_edge` и публикация волны (вердикт `OVERRIDE`, только `test`).

**Tech Stack:** Python 3.12+, pytest, PyYAML, `uv run`; агент — `lite.structure.claude_agent`.

**Spec:** `docs/superpowers/specs/2026-11-14-dfl-edge-findings-resolution-design.md` (§3 цели, §5 запреты, §6 решения владельца, §9 решения по сборке).

## Global Constraints

- Не менять authority-root (`contracts/authority-root/v1/paths.env`): `governance/author_launch.py`, `governance/act_*`, `governance/facts*`, `profiles/` и пр. Если задаче кажется, что нужно, — стоп и вопрос.
- Не менять правила рёбер (`contracts/edge-check/v1/rules/*.yaml`) и `edge_check.check.decide`.
- `OVERRIDE` — только `state.mode == "test"`; в `strict` путь отсутствует (отказ, не молчание).
- Рекомендации (`minor`) не сортируются и не правятся.
- Предел: `MAX_ROUNDS = 2` круга «переписать → проверить» на волну за вызов `dfl run`.
- Ключ находки — `(node, edge, rule_id)`.
- Без tty (`dfl._is_tty()` ложно) — разбора нет, прежний стоп (#758/#760).
- Вывод — уровни `lite.output.say` (0 — результат и действия человека; 1 — что исправлено; детали — `dfl log`).
- Проверки: `uv run --group selfcheck ruff format|check`, `uv run --group selfcheck pyrefly check <files>`, тесты `uv run pytest tests/test_lite_*.py tests/test_df_config.py tests/test_engineer_route_smoke.py tests/test_edge_check_*.py tests/test_governance_ops.py -q`; затрагивающие runner — плюс `tests/test_governance_runner.py tests/test_act_wave_runner.py`.
- Перед стартом: PR #759 и #760 влиты, ветка от свежего `master`.

## Review Focus

1. Агент сортировки недоступен или ответил не JSON → прежний стоп с находками и строкой причины, без трассировки и без переписывания (Task 5, `test_triage_failure_falls_back_to_stop`).
2. Переписывание удалило FR или понизило Must, а человек этого не выбирал → файл возвращён из копии, стоп с названным id (Task 4/5, `test_weakened_rewrite_restored`).
3. Ctrl-C или «пропустить» посреди вопросов → уже данные ответы в журнале, переписывания нет, следующий `dfl run` не спрашивает их снова (Task 5, `test_interrupted_answers_kept`).
4. Та же блокирующая находка после «не согласен» → предложено «принять вопреки», а не третий круг (Task 5, `test_repeat_after_disagree_offers_override`).
5. Находка указывает на файл вне волны или без `path` → разбор не начинается, прежний стоп (Task 5, `test_unresolvable_file_no_resolution`).

---

### Task 1: Правила ребра в промпте автора

**Files:**
- Create: `governance/edge_check/author_rules.py`
- Modify: `governance/ops.py` (`author_prompt`, ~968)
- Test: `tests/test_edge_check_author_rules.py`, `tests/test_governance_ops.py`

**Interfaces:**
- Produces: `rules_for(kind: str, contracts_dir: Path = DEFAULT_CONTRACTS) -> str` — блок текста; пусто, если рёбер с `subject_role == kind` нет.

- [ ] **Step 1: тесты.** `test_rules_for_requirements_lists_items` — для `"requirements"` в тексте есть `R1` и подстрока «Каждая цель, ограничение и метрика charter» и строка про ссылки только на id узлов-оснований с перечнем `charter`. `test_rules_for_unknown_kind_empty` — `rules_for("nope") == ""`. `test_rules_for_charter_has_both_edges` — для `charter` есть тексты из `charter-vs-customer-brief` и `charter-vs-engineer-brief`. В `tests/test_governance_ops.py`: `test_author_prompt_carries_edge_rules` — `author_prompt("requirements", …)` содержит `rules_for("requirements")`.
- [ ] **Step 2:** `uv run pytest tests/test_edge_check_author_rules.py tests/test_governance_ops.py -k "rules" -q` → FAIL (нет модуля).
- [ ] **Step 3: реализация.** Читать YAML через `governance.edge_check.rules.load_rules` по каждому файлу каталога, отбирать по `subject_role`; формат блока — заголовок «Your node will be checked against these rules (edge <id>):», пункты `- <id>: <text>`, затем «Reference only ids defined in: <basis_roles>; never ids from discovery briefs unless the basis defines them.» В `author_prompt` — дописать блок после `rules`.
- [ ] **Step 4:** тот же запуск → PASS; полный набор `tests/test_governance_ops.py tests/test_governance_runner.py -q` → PASS (снимки промптов, если есть, обновить осознанно).
- [ ] **Step 5: commit** `feat(author): правила ребра узла в промпте автора`.

### Task 2: Находки волны и журнал решений

**Files:**
- Create: `lite/edge_journal.py`
- Test: `tests/test_lite_edge_journal.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Finding: node: str; edge: str; rule_id: str; cls: str; statement: str; path: str | None; lines: str` + свойство `key -> tuple[str, str, str]`.
  - `blocking_findings(run_dir: Path, wave: int) -> list[Finding]` — из `edge_check.coordinator.load_level_records`, только классы `lite.stop_findings.BLOCKING`.
  - `@dataclass(frozen=True) class Decision: key: tuple[str, str, str]; question: str; answer: str; kind: str  # "option"|"own"|"disagree"|"override"; round: int; allows: dict[str, list[str]]  # {"removes": [...], "downgrades": [...]}`
  - `append(run_dir: Path, wave: int, d: Decision) -> None`; `load(run_dir: Path, wave: int) -> list[Decision]` — файл `edge-decisions.jsonl`, поле `wave` в строке.
  - `overridden(run_dir: Path, wave: int) -> set[tuple[str, str, str]]` — ключи с `kind == "override"`.

- [ ] **Step 1: тесты.** `test_blocking_findings_from_records` (запись ребра с одной major и одной minor → один `Finding`, `key == ("requirements", "requirements-vs-charter", "R1")`); `test_journal_roundtrip_per_wave` (решения волн 2 и 3 не смешиваются); `test_overridden_keys`; `test_corrupt_line_skipped` (битая строка JSONL не роняет `load`).
- [ ] **Step 2:** запуск → FAIL.
- [ ] **Step 3:** реализация (append — одна строка JSON, `ensure_ascii=False`).
- [ ] **Step 4:** → PASS.
- [ ] **Step 5: commit** `feat(dfl): журнал решений по замечаниям edge-check`.

### Task 3: Сортировка замечаний агентом

**Files:**
- Create: `lite/edge_triage.py`
- Test: `tests/test_lite_edge_triage.py`

**Interfaces:**
- Consumes: `Finding`, `Decision` (Task 2); `lite.structure.Agent`, `first_json_object`, `FormError`, `AgentUnavailable`.
- Produces:
  - `@dataclass(frozen=True) class Option: label: str; recommended: bool; removes: list[str]; downgrades: list[str]`
  - `@dataclass(frozen=True) class Item: finding: Finding; kind: str  # "fix"|"decision"; question: str; options: list[Option]`
  - `triage(findings: list[Finding], nodes: dict[str, str], bases: dict[str, str], prior: list[Decision], agent: Agent) -> list[Item]` — `nodes`/`bases`: имя файла → текст. Бросает `FormError`/`AgentUnavailable`.

- [ ] **Step 1: тесты** (агент подменён, как в `tests/test_lite_gate_repair.py`): `test_fix_and_decision_parsed`; `test_decision_needs_two_to_three_options_one_recommended` (иначе `FormError`); `test_unknown_finding_index_rejected` (`FormError`); `test_finding_with_prior_decision_not_asked` (решение с тем же ключом в `prior` → такой пункт становится `fix` с решением в контексте, без вопроса); `test_option_effects_parsed` (`removes: ["FR-03"]`); `test_triage_cached_by_input_hash`.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** промпт на русском: находки пронумерованы, тексты узлов и оснований, прошлые решения; правило «удаление требования, понижение Must, снятие сценария — только `decision` с эффектами в варианте»; ответ — JSON `{"items": [{"finding": <№>, "kind": "fix"|"decision", "question": "...", "options": [{"label": "...", "recommended": true, "removes": [], "downgrades": []}]}]}`. Каждая находка — ровно один пункт. Причина находки в утверждённом узле выше (чужой id, цель вне основания) — `decision` с вариантами внутри текущего узла (спека §9 п.1). Кэш: результат пишется в `run_dir/edge-triage/<sha256 находок+текстов>.json` и читается вместо вызова агента при том же хэше (спека §9 п.7); тест `test_triage_cached_by_input_hash` (второй вызов не зовёт агента).
- [ ] **Step 4:** → PASS.
- [ ] **Step 5: commit** `feat(dfl): сортировка замечаний edge-check агентом`.

### Task 4: Переписывание узла и сверка «не ослаблен»

**Files:**
- Create: `lite/node_guard.py`
- Modify: `governance/ops.py` (новые `revise_prompt`, `Ops.revise` в протоколе ~337, `RealOps.revise` рядом с `RealOps.author` ~2320)
- Test: `tests/test_lite_node_guard.py`, `tests/test_governance_ops.py`

**Interfaces:**
- Produces:
  - `node_items(text: str) -> dict[str, str | None]` — id из заголовков `#### <ID>` (`ID` — `[A-Z]+-\d+`), значение — `Priority` из строки `**Priority**: <X>` под ним или `None`.
  - `weakened(before: str, after: str, removes: set[str], downgrades: set[str]) -> list[str]` — id, исчезнувшие или с `Must`→не-`Must`, кроме разрешённых.
  - `revise_prompt(kind: str, subject: str, bundle_dir: str, output_path: str, current: str, findings: str, decisions: str) -> str` — промпт автора (`author_prompt` того же `kind`, т.е. с правилами Task 1) + «Перепиши файл, исправив только перечисленные замечания; решения заказчика — дословно; несогласие оформи строкой «Решение заказчика: … Причина: …» у места замечания; не трогай другие файлы; не удаляй и не ослабляй требования, кроме разрешённых решениями».
  - `RealOps.revise(call: AuthorCall, kind: str, subject: str, current: str, findings: str, decisions: str) -> LaunchOutcome` — `author_launch.launch_author(call, partial(revise_prompt, …))`.

- [ ] **Step 1: тесты.** `test_node_items_ids_and_priority`; `test_weakened_detects_removed_and_downgraded`; `test_weakened_allows_chosen_effects`; `test_node_without_priority_checked_by_ids_only`; в ops — `test_revise_prompt_has_rules_findings_decisions` и `test_revise_delegates_to_launch_author` (подменить `author_launch.launch_author`, проверить `call` и что `make_prompt(out)` содержит `out`).
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** реализация; `author_launch.py` не трогать.
- [ ] **Step 4:** → PASS; `tests/test_author_sandbox_invariant*` (если есть в наборе) → PASS — новый вызов идёт через ту же границу.
- [ ] **Step 5: commit** `feat(ops): переписывание узла по замечаниям через границу автора`.

### Task 5: Оркестратор разбора и встраивание в `dfl run`

**Files:**
- Create: `lite/edge_resolve.py`
- Modify: `lite/dfl.py` (`_run_chatting` ~499 или соседняя функция `_run_resolving`; `_next_hint` не трогать)
- Test: `tests/test_lite_edge_resolve.py`, `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Tasks 2–4; `lite.stop_findings.load` (стоп-файл), `lite.interview_chat.read_answer`/`stdin_read`, `lite.output.say`.
- Produces:
  - `class Outcome(Enum): REVISED, OVERRIDDEN, STOPPED, NOT_APPLICABLE`
  - `resolve(state: rs.RunState, *, read: Read, agent: Agent, revise: Callable[..., LaunchOutcome], round_no: int) -> Outcome`
  - `MAX_ROUNDS = 2`; строки: `FIXING = "исправлю сам: {n}; вопросов к вам: {m}"`, `OVERRIDE_OFFER = "принять узел вопреки замечанию (запишется в журнал и в PR)"`.

Поведение `resolve` (по спеке §3, §6, §9):
1. `NOT_APPLICABLE`, если статус не `stopped_review`, нет блокирующих находок (только отказ прибора/`insufficient`), находка без `path` или `path` не файл узла волны (`ops._AUTHOR_FILENAMES`).
2. Сортировка (`triage`); `FormError`/`AgentUnavailable` → `say` причины, `STOPPED`.
3. `say(FIXING…)`; по каждому `decision` — вопрос: варианты `1..k` (рекомендуемый помечен), «своё: …», «не согласен: …», «пропустить»; в `test`, если ключ уже был «не согласен» в журнале или `round_no == MAX_ROUNDS`, — ещё вариант `OVERRIDE_OFFER`. Каждый ответ сразу в журнал (`Decision`). «Пропустить»/`None` → `STOPPED`, переписывания нет.
4. Все блокирующие ключи `override` → `OVERRIDDEN` (переписывания нет).
5. Иначе по каждому файлу волны с находками: копия в `run_dir/edge-revise/<round>/<file>`, `revise(...)`; код ≠ 0 → вернуть копию, `STOPPED`; `node_guard.weakened` непуст → вернуть копию, `say` с id, `STOPPED`. Успех → `say(…, 1)` по каждой находке, `REVISED`.

В `dfl.py`: после `_step(...)` при `_is_tty()` и `harness.author == claude`: цикл до `MAX_ROUNDS`: `resolve` → `REVISED`/`OVERRIDDEN` → снова `_step` (resume перепроверит); `STOPPED`/`NOT_APPLICABLE` → выход с кодом шага. После `MAX_ROUNDS` с повторяющимися ключами — сводка «не сошлось за 2 круга: <ключи>; повторяются — признак devtools#728» и выбор (`dfl run` / правка руками / `dfl abandon`).

- [ ] **Step 1: тесты** `tests/test_lite_edge_resolve.py` (подменены `read`, `agent`, `revise`, каталог прогона во `tmp_path`): `test_fix_only_no_questions_revised`; `test_decision_option_recorded_and_passed_to_revise`; `test_disagree_recorded`; `test_interrupted_answers_kept`; `test_triage_failure_falls_back_to_stop`; `test_weakened_rewrite_restored`; `test_chosen_removal_allowed`; `test_repeat_after_disagree_offers_override`; `test_override_offer_absent_in_strict`; `test_unresolvable_file_no_resolution`; `test_instrument_failure_not_applicable`. В `tests/test_lite_dfl.py`: `test_run_resolves_then_steps_again` (два вызова `spec_loop.main`), `test_no_tty_keeps_old_stop`, `test_round_limit_summary`.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** → PASS; полный lite-набор → PASS.
- [ ] **Step 5: commit** `feat(dfl): разбор замечаний edge-check разговором`.

### Task 6: Вердикт `OVERRIDE` в раннере и публикации (только test)

**Files:**
- Modify: `governance/runner.py` (`_step_edge` ~4308, `_auto_merge_candidate` ~1265), `governance/edge_check/publish.py` (`level_verdict`/`edge_verdict`/`publish_wave` ~101–206, `checklist_body`)
- Test: `tests/test_governance_runner.py`, `tests/test_edge_check_publish.py`

**Interfaces:**
- Consumes: `lite.edge_journal.overridden` — НЕ импортировать `lite` из `governance`: продублировать чтение `edge-decisions.jsonl` минимальной функцией `governance/edge_check/override.py: overridden(run_dir: Path, wave: int) -> set[tuple[str, str, str]]` и использовать её же из `lite.edge_journal` (направление импорта `lite → governance`).
- Produces: вердикт уровня `"OVERRIDE"`.

- [ ] **Step 1: тесты.** `test_edge_fail_all_blocking_overridden_completes_in_test` (op `edge-<w>` завершён с `verdict="OVERRIDE"`, статус не `stopped_review`); `test_override_ignored_in_strict` (прежний `stopped_review`); `test_override_partial_still_fails` (не все блокирующие ключи — FAIL); `test_publish_override_body_names_override` (тело ревью candidate содержит «принято человеком вопреки замечаниям» и ключи); `test_auto_merge_accepts_override_in_test`.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3:** `_step_edge`: при `verdict == "FAIL"` и `state.mode == "test"` — если ключи блокирующих находок записей ⊆ `overridden(...)` → `op_complete(..., verdict="OVERRIDE")`. Публикация: `OVERRIDE` трактуется как `PASS` только при `mode == "test"`, тело называет принятие; `_auto_merge_candidate` принимает `{"PASS", "OVERRIDE"}` в test.
- [ ] **Step 4:** → PASS; `tests/test_governance_runner.py tests/test_act_wave_runner.py tests/test_edge_check_*.py -q` → PASS.
- [ ] **Step 5: commit** `feat(runner): принятие узла вопреки замечаниям edge-check (test)`.

### Task 7: Документация

**Files:**
- Modify: `docs/darkfactory-for-colleagues.md` (раздел «Остановки на смысловой проверке»), `CLAUDE.md` строка `edge_check.py`/`lite` при необходимости.

- [ ] **Step 1:** раздел переписать: разбор разговором, что исправляет агент, вид вопроса, «не согласен», «принять вопреки» (test, журнал, PR), предел 2 круга, без терминала — прежний стоп; пример экрана уровня 0.
- [ ] **Step 2:** `uv run pytest tests/test_lite_dfl.py -q` (тесты, сверяющие доку, если есть) → PASS.
- [ ] **Step 3: commit** `docs(dfl): разбор замечаний edge-check`.
