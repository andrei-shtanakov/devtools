# dfl: личный путь — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dfl run` для личного проекта: свободное интервью → облегчённый бриф → дизайн → декомпозиция → spec-runner → integration-PR человеку, без волн и банка вопросов.

**Architecture:** Личный путь — отдельный оркестратор `lite/personal/` поверх существующих частей (`launch_author`, `edge_check.check.run_check`, ядро разбора замечаний `lite/edge_resolve`, `task_bridge.render_tasks_dt`, `Ops`). Волновой раннер, `RunState`, `BUNDLE_DAG` и профили не меняются. Состояние личного прогона — свой файл вне `RUNS_ROOT`.

**Tech Stack:** Python 3.12, uv, pytest, ruff, pyrefly; агент — `claude -p --safe-mode --tools ""` (`lite.structure.claude_agent`).

**Spec:** `docs/superpowers/specs/2026-11-15-dfl-personal-path-design.md` (одобрен владельцем 15.11).

## Global Constraints

- Только режим `test` (`lite/dfl.py:466` уже отказывает вне него); enterprise-путь не меняет поведения: существующие тесты полной цепочки не правятся.
- Authority-root не трогается (`contracts/authority-root/v1/paths.env`): ни `governance/bundle_dag.py`, ни `profiles/`, ни `governance/author_launch.py`.
- Соседние репо (discovery, discovery-toolkit, spec-runner) не правим.
- Строки для человека — по-русски; код, имена файлов — по-английски. Длина строки 88.
- После каждой задачи: `uv run --with ruff ruff format` + `ruff check`, `uvx pyrefly check <изменённые файлы>` → 0 ошибок, тесты задачи зелёные.
- Бриф — ровно формат §4.3 спеки; потолок интервью `MAX_QUESTIONS = 20`; разбор замечаний — `edge_resolve.MAX_ROUNDS` (2).

## Ruling (отступления от спеки, сказать владельцу)

1. **Отдельный оркестратор вместо `PERSONAL_DAG` + `profiles/personal.yaml`** (спека §4.4 «Реализация в коде»). Причина: цепочка захардкожена в ~6 местах раннера, `run_level`/`deliver`/`run_chain` завязаны на волны и полный DAG; отдельный путь не трогает authority-root и не рискует enterprise. Цена ошибки: при будущем сближении путей — перенос оркестратора в раннер.
2. **Два PR, а не один.** spec-runner форкает ветку интеграции от базовой ветки и читает `spec/<ws>-tasks.md` из неё (spec-runner `git_ops.py:649`, `cli.py:352-386`), флага базы нет. Поэтому бриф+дизайн+декомпозиция+`tasks.md` уходят spec-PR, который **мержит агент** (как tasks-PR в `test` сейчас, `auto_tasks._merge_spec_pr`), а человек мержит только итоговый integration-PR. Человеческих актов — один, как решил владелец. Цена ошибки: лишний PR в истории репо.
3. **`tasks.md` личного пути — неуправляемый (без frontmatter)**, запуск `spec-runner run --all --no-strict …`: гейт одобрения spec-runner применяется только к управляемому `tasks.md` (`cli.py:288-304`), а одобрение брифа уже есть («да» в разговоре).
4. **Разметка проверок задачи** (спека §6 оставила строителю): декомпозиция несёт раздел `## Проверки` из блоков `#### BEH-NN` той же грамматики, что behaviour-spec (`**checked_by**` с `kind`/`target`), плюс строку `**Критерии:** FR-01/1, …`; DT-блоки — грамматика `decomposition_guard.parse_dt_tasks`. Так переиспользуются `parse_behaviour`, `graph_findings`, `render_tasks_dt` без изменений.

## Review Focus

1. Ctrl-C посреди интервью или разбора → следующий `dfl run` продолжает с того же вопроса/шага, а не начинает заново и не падает трассировкой.
2. Агент интервью вернул не-JSON или JSON не той формы → один повтор агенту с причиной, затем стоп человеку «агент не ответил по форме; повторите dfl run» — без трассировки.
3. Грязное дерево цели или чужая ветка перед spec-PR → стоп с названной причиной до любых git-эффектов.
4. В репо есть и enterprise-прогон, и личный → `dfl run` без `--subject` продолжает личный активный прогон, enterprise-прогоны и их чтение (`_readable_runs`) не ломаются.
5. Edge-check вернул `ERROR`/отказ прибора → это не замечания: стоп «проверка ребра не состоялась», без вопросов человеку и без переписывания узла.

Тест на каждую строку — в задаче-владельце (помечены `RF-n`).

---

### Task 1: ядро разбора замечаний без `RunState`

**Files:**
- Modify: `lite/edge_resolve.py`
- Test: `tests/test_lite_edge_resolve.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Scope: run_dir: Path; target_dir: str; bundle_dir: str; wave: int; can_override: bool; filenames: Mapping[str, str]` — `filenames`: вид узла → имя файла (волновой путь передаёт `_AUTHOR_FILENAMES`).
  - `def resolve_scope(scope: Scope, findings: list[ej.Finding], *, read: Read, agent: Agent, revise: Revise, round_no: int, calls: PaidCalls | None = None) -> Outcome` — всё, что сейчас `resolve` делает после получения находок.
  - `resolve(state, …)` остаётся с прежней сигнатурой: строит `Scope` из `state` (`rs.run_dir(state.run_id)`, `state.target_dir`, `state.bundle_dir`, `state.wave`, `state.mode == "test"`, `_AUTHOR_FILENAMES`), берёт находки `applicable_findings(state)` и зовёт `resolve_scope`.

- [ ] **Step 1: тест** `test_resolve_scope_without_run_state`: тот же мир, что `test_decision_option_recorded_and_passed_to_revise`, но вызов `er.resolve_scope(scope, findings, read=…, agent=…, revise=…, round_no=1)` с `Scope`, собранным из каталогов фикстуры, и `filenames={"design": "20-design.md"}`; ожидание — `Outcome.REVISED`, решение в журнале `ej.load(run_dir, wave)`.
- [ ] **Step 2:** `uv run pytest tests/test_lite_edge_resolve.py -q` → новый тест FAIL (`resolve_scope` нет).
- [ ] **Step 3:** вынести тело; все обращения к `state.*` внутри ядра заменить полями `Scope`; `_AUTHOR_FILENAMES` в проверке «файл находки — узел её вида» → `scope.filenames`.
- [ ] **Step 4:** `uv run pytest tests/test_lite_edge_resolve.py tests/test_lite_dfl*.py -q` → всё зелёное, существующие тесты не правились.
- [ ] **Step 5:** commit `refactor(edge_resolve): ядро resolve_scope без RunState (#764)`.

### Task 2: состояние личного прогона

**Files:**
- Create: `lite/personal/__init__.py`, `lite/personal/state.py`
- Test: `tests/test_lite_personal_state.py`

**Interfaces:**
- Produces:
  - `PERSONAL_ROOT = rs.RUNS_ROOT.parent / "personal-runs"`
  - `STEPS = ("interview", "design", "decomposition", "spec-pr", "code-run", "code-pr", "done")`
  - `@dataclass class PersonalRun: run_id: str; ws_id: str; subject: str; repo: str; repo_slug: str; target_dir: str; bundle_dir: str; step: str = "interview"; base_ref: str = "master"; spec_pr: int | None = None; code_pr: int | None = None; stop_reason: str = ""`
  - `def new_personal_run(subject: str, repo: str, repo_slug: str, target_dir: str, today: date) -> PersonalRun` — `ws_id = spec_loop.ws_id_for(subject, today)`, `run_id = f"{ws_id}-{secrets.token_hex(3)}"`, `bundle_dir = f"workstreams/{ws_id}/spec"`.
  - `def run_dir(run_id: str) -> Path`, `def save(run: PersonalRun) -> None` (атомарно, `run.json`), `def load(run_id: str) -> PersonalRun` (незнакомые ключи игнорируются), `def active_for(repo_slug: str) -> PersonalRun | None` (последний по mtime с `step != "done"`).

- [ ] **Step 1: тесты** `test_roundtrip`, `test_load_ignores_unknown_keys`, `test_active_for_skips_done`, `test_personal_root_outside_runs_root` (`PERSONAL_ROOT != rs.RUNS_ROOT` и не внутри него).
- [ ] **Step 2:** запуск → FAIL (модуля нет).
- [ ] **Step 3:** реализация; корень подменяется в тестах monkeypatch'ем `PERSONAL_ROOT`.
- [ ] **Step 4:** `uv run pytest tests/test_lite_personal_state.py -q` → PASS.
- [ ] **Step 5:** commit `feat(personal): состояние личного прогона`.

### Task 3: облегчённый бриф

**Files:**
- Create: `lite/personal/brief.py`
- Test: `tests/test_lite_personal_brief.py`

**Interfaces:**
- Produces:
  - `Goal(id: str, title: str, text: str)`, `Function(id: str, title: str, goals: tuple[str, ...], criteria: tuple[str, ...], words: str)`, `Context(users: str, auth: str, network: str, local: str, platform: str, other: str = "")`, `Brief(goals: tuple[Goal, ...], functions: tuple[Function, ...], context: Context, open_items: tuple[str, ...] = ())` — frozen.
  - `def brief_from_json(data: object) -> Brief` — `FormError` (из `lite.structure`) с причиной при неверной форме.
  - `def render_brief(b: Brief) -> str` — ровно разметка §4.3 спеки (заголовки `## Цели`/`### G-01: …`, `## Функции`/`### FR-01: …`, `**Цели:**`, `**Критерии приёмки:**`, `**Слова владельца:**`, `## Контекст` с пятью строками + «Прочее», `## Уточнить` только если непусто).
  - `def brief_problems(b: Brief) -> list[str]` — правила §4.3: ≥1 G; ≥1 FR; у FR ≥1 критерий и ≥1 ссылка на существующую G; id `G-NN`/`FR-NN` уникальны и по порядку с 01; пять полей контекста непусты.
  - `def demote_uncovered(b: Brief) -> Brief` — FR без критериев → строка `«FR-NN <title>: нет критерия приёмки»` в `open_items`, из `functions` удаляется.

- [ ] **Step 1: тесты** `test_render_exact_format` (сравнение со строкой-эталоном на G-01, FR-01 с двумя критериями, контекст, без «Уточнить»), `test_render_open_items_section`, `test_problems_each_rule` (параметризация по каждому правилу: текст причины содержит id), `test_from_json_rejects_bad_shape`, `test_demote_uncovered`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(personal): облегчённый бриф — формат и проверка`.

### Task 4: свободный интервьюер

**Files:**
- Create: `lite/personal/interviewer.py`, `lite/personal/prompts/interviewer.md`
- Test: `tests/test_lite_personal_interviewer.py`

**Interfaces:**
- Consumes: `Brief`, `brief_from_json`, `brief_problems`, `demote_uncovered`, `render_brief` (Task 3); `run_dir` (Task 2); `read_answer`, `stdin_read`, `is_skip` (`lite/interview_chat.py`); `Agent`, `AgentUnavailable` (`lite/structure.py`).
- Produces:
  - `MAX_QUESTIONS = 20`
  - `def interview(run_dir: Path, subject: str, *, read: Read, agent: Agent) -> Brief | None` — `None` = человек остановился (`/стоп`, EOF, «пропустить» на подтверждении) или агент не ответил по форме дважды подряд (сообщение уже выведено).
  - Протокол агента (в промпте и в разборе): ответ — один JSON-объект, либо `{"ask": "<вопрос>", "options": ["…", …]}` (options может быть пуст), либо `{"brief": <объект brief_from_json>}`.
  - Реплики пишутся по одной JSON-строке в `run_dir / "interview.jsonl"` (`{"role": "agent"|"human"|"check", "text": …}`); при старте файл читается, и разговор продолжается с него (**RF-1**).

Промпт `interviewer.md` (содержание, формулировки — за исполнителем): цель — закрыть цели, функции с проверяемыми критериями и пять строк контекста; один вопрос за ход, выбранный по уже сказанному; варианты — когда помогают; контекст выясняется по ходу, неактуальное не спрашивать; противоречие с уже сказанным — следующий вопрос именно о нём, с вариантами разрешения; запрет: спонсор, аппрув, compliance, стейкхолдеры, метрики «до», бюджет, MoSCoW, исследования Q-NN; при сообщении «лимит вопросов исчерпан» — сразу `{"brief": …}`, функции без критериев — в `open_items`.

Логика `interview`: каждый ход — промпт + транскрипт → агент; `ask` → вывод вопроса и вариантов (`1. …`), чтение ответа `read_answer(read, quick=str.isdigit)`, запись; `brief` → `brief_problems`; есть проблемы и вопросов < `MAX_QUESTIONS` → реплика `check` с проблемами агенту (не человеку); после `MAX_QUESTIONS` вопросов — в промпт добавляется «лимит вопросов исчерпан», полученный бриф проходит `demote_uncovered`; затем сводка `render_brief` и вопрос «верно? 1. да · 2. поправить: <что>» — «1» → бриф, «2…»/свободный текст → реплика человека, разговор продолжается.

- [ ] **Step 1: тесты** (скриптованный агент — список ответов-строк, скриптованный `read`):
  - `test_ask_then_brief_confirmed` — 2 вопроса, бриф, «1» → возвращён `Brief`, в `interview.jsonl` 2+2 реплики + сводка.
  - `test_problems_go_back_to_agent_not_human` — первый бриф без критерия у FR → следующий промпт агента содержит текст проблемы, человек ничего не читал между.
  - `test_cap_forces_summary_and_demotes` — 20 `ask` подряд → 21-й промпт содержит «лимит вопросов исчерпан»; бриф с FR без критерия → в сводке раздел «Уточнить».
  - `test_resume_from_transcript` (**RF-1**) — `interview.jsonl` с одним вопросом и ответом → первый промпт содержит их, повторно человек не спрашивается.
  - `test_malformed_agent_twice_stops` (**RF-2**) — два не-JSON ответа → `None`, вывод содержит «агент не ответил по форме», без исключения.
  - `test_paid_calls_reported` — после подтверждения вывод содержит «платных вызовов агента: N» с числом вызовов скриптованного агента (спека §4.2: вызовы считаются и видны).
  - `test_prompt_forbids_enterprise_topics` — текст `interviewer.md` содержит «спонсор», «compliance», «противореч».
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(personal): свободный интервьюер`.

### Task 5: правила рёбер и авторинг узлов

**Files:**
- Create: `contracts/edge-check/v1/rules/personal-design-vs-brief.yaml`, `contracts/edge-check/v1/rules/personal-decomposition-vs-design-brief.yaml`, `lite/personal/nodes.py`, `lite/personal/prompts/design.md`, `lite/personal/prompts/decomposition.md`
- Test: `tests/test_lite_personal_nodes.py`

**Interfaces:**
- Consumes: `PersonalRun`, `run_dir` (Task 2); `AuthorCall`, `launch_author`, `LaunchOutcome` (`governance/author_launch.py:317,682`).
- Produces:
  - `FILENAMES = {"brief": "00-brief.md", "personal-design": "20-design.md", "personal-decomposition": "30-decomposition.md"}`
  - `EDGES = {"personal-design": ("personal-design-vs-brief", [("brief", "00-brief.md")]), "personal-decomposition": ("personal-decomposition-vs-design-brief", [("design", "20-design.md"), ("brief", "00-brief.md")])}`
  - `def author_node(run: PersonalRun, kind: str, *, launch=launch_author) -> LaunchOutcome`
  - `def revise_node(call: AuthorCall, kind: str, subject: str, current: str, findings: str, decisions: str, *, launch=launch_author) -> LaunchOutcome` — сигнатура `Revise` из `lite/edge_resolve.py`.

Правила: `subject_role` — `personal-design` / `personal-decomposition` (не `design`/`decomposition`: `rules_for(kind)` берёт правила по `subject_role`, enterprise-автор не должен их получить); `basis_roles` — `[brief]` / `[design, brief]`; severity как в существующих файлах. Пункты: design — каждая FR брифа получает механизм; решения учитывают пять строк контекста; не противоречит брифу. decomposition — каждый критерий `FR-NN/k` брифа покрыт ≥1 проверкой `#### BEH-NN` (`**Критерии:**`); каждая задача DT опирается на решение дизайна; у каждой проверки `checked_by` с `kind`/`target`.

Промпт `decomposition.md` фиксирует разметку из Ruling 4: раздел `## Проверки` (блоки `#### BEH-NN: <что проверяет>` + `**checked_by**` в грамматике `task_bridge._CHECKED` + `**Критерии:** FR-01/1`) и раздел `## Задачи` (DT-блоки в грамматике `decomposition_guard.parse_dt_tasks`, `scenarios` ссылаются на BEH-NN). Промпты получают правила своего ребра текстом (`load_rules` по `EDGES[kind][0]`).

- [ ] **Step 1: тесты**:
  - `test_rules_load` — `load_rules` оба ребра, роли совпадают с `EDGES`.
  - `test_enterprise_author_rules_unchanged` — `rules_for("design")` и `rules_for("decomposition")` не содержат `personal-*` рёбер.
  - `test_author_node_call_and_prompt` — фейковый `launch` получает `AuthorCall(filename="20-design.md", bundle_dir=run.bundle_dir, …)`, промпт содержит путь к `00-brief.md` и текст правил ребра.
  - `test_revise_node_prompt_has_findings_and_decisions`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** PASS + `uv run pytest tests -q -k "edge_check or author_rules"` зелёный.
- [ ] **Step 5:** commit `feat(personal): правила рёбер и авторинг дизайна/декомпозиции`.

### Task 6: проверка узла и инварианты декомпозиции

**Files:**
- Create: `lite/personal/check.py`
- Test: `tests/test_lite_personal_check.py`

**Interfaces:**
- Consumes: `EDGES`, `FILENAMES` (Task 5); `Brief` (Task 3); `run_check` (`governance/edge_check/check.py:69`); `ej.blocking_findings` (`lite/edge_journal.py:81`); `parse_behaviour` (`governance/task_bridge.py:117`), `graph_findings` (`governance/decomposition_guard.py:663`).
- Produces:
  - `WAVES = {"personal-design": 1, "personal-decomposition": 2}`
  - `@dataclass(frozen=True) class NodeCheck: verdict: str; findings: list[ej.Finding]; failure: str` — `failure` непуст при `ERROR`/исключении прибора.
  - `def check_node(run: PersonalRun, kind: str, *, check=run_check) -> NodeCheck` — запускает ребро, пишет запись `{**result, "node": kind, "wave": WAVES[kind]}` в `run_dir/edge-check/w{wave}/{kind}--{edge_id}.json` (раскладка, которую читает `ej.level_records`), находки — `ej.blocking_findings(run_dir, wave)`.
  - `def split_decomposition(text: str) -> tuple[str, str]` — (раздел `## Проверки`, весь текст).
  - `def decomposition_problems(text: str, brief: Brief) -> list[str]` — `graph_findings(checks, text)` + непокрытые критерии `FR-NN/k` (k — номер критерия с 1).

- [ ] **Step 1: тесты**: `test_record_layout_read_by_journal` (фейковый `check` → FAIL с одной major-находкой → `NodeCheck.findings` из `ej.blocking_findings`); `test_error_is_failure_not_findings` (**RF-5**: `verdict=ERROR` → `failure` непуст, `findings == []`); `test_decomposition_problems_uncovered_criterion`; `test_decomposition_valid_sample` (эталонная декомпозиция в `tests/fixtures/personal/30-decomposition.md` → пусто).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализация.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(personal): проверка узла и инварианты декомпозиции`.

### Task 7: `tasks.md` личного пути

**Files:**
- Create: `lite/personal/tasks.py`
- Test: `tests/test_lite_personal_tasks.py`

**Interfaces:**
- Consumes: `split_decomposition` (Task 6); `parse_behaviour`, `render_tasks_dt` (`task_bridge.py:117,786`); `parse_dt_tasks` (`decomposition_guard.py:435`).
- Produces: `def render_personal_tasks(run: PersonalRun, decomposition_text: str, design_text: str, generated_at: str) -> str` — `render_tasks_dt(..., anchor_blob=None, design_text=design_text)` без frontmatter (Ruling 3: срезается ведущий блок `---…---`, если `_render_header` его выдал); `TASKS_PATH = "spec/{ws_id}-tasks.md"`.

- [ ] **Step 1: тесты**: `test_one_task_per_dt_with_targets` (на фикстуре Task 6: число задач = число DT, у каждой — `target` её проверок); `test_no_frontmatter` (текст не начинается с `---`).
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(personal): tasks.md из декомпозиции`.

### Task 8: оркестратор шагов

**Files:**
- Create: `lite/personal/flow.py`
- Test: `tests/test_lite_personal_flow.py`

**Interfaces:**
- Consumes: всё выше; `Ops` (`governance/ops.py`: `is_dirty`, `current_branch`, `checkout_and_pull`, `switch_to`, `commit_paths`, `push_branch`, `create_pr`, `pr_facts`, `human_merge`, `spec_runner`, `prs_by_head_prefix`); `auto_tasks.notice`, `auto_tasks.TUI_HINT`, `auto_tasks.RUN_BRANCH_PREFIX`; `resolve_scope`, `Scope`, `MAX_ROUNDS`, `PaidCalls` (Task 1).
- Produces: `def advance(run: PersonalRun, ops: Ops, *, read: Read, agent: Agent, launch=launch_author, check=run_check) -> int` — выполняет шаги, пока может; 0 — дошёл до `code-pr`/`done` или ждёт человека, 1 — стоп (`run.stop_reason` записан и выведен); после каждого шага `save(run)`.

Шаги (`run.step`):
1. `interview` → `interview(...)`; `None` → стоп; иначе `00-brief.md` = `render_brief` в `target_dir/bundle_dir`, запись одобрения в `run_dir/approvals.jsonl` (`{"node": "brief", "how": "подтверждено в разговоре"}`) → `design`.
2. `design`, `decomposition` → если файла узла нет — `author_node`; `check_node`; `failure` → стоп «проверка ребра не состоялась: …» (**RF-5**); `PASS` → следующий шаг; `FAIL` → до `MAX_ROUNDS` кругов `resolve_scope(Scope(run_dir, target_dir, bundle_dir, wave=WAVES[kind], can_override=True, filenames=FILENAMES), findings, ...)` с перепроверкой после `REVISED`; не сошлось → стоп. Для `decomposition` после `PASS` — `decomposition_problems`; непусто → стоп с ними.
3. `spec-pr` → гард (**RF-3**): `is_dirty` или `current_branch != base_ref` → стоп до эффектов; `checkout_and_pull(base_ref)`, `switch_to(f"spec/{ws_id}-personal", base_ref)`, запись `tasks.md`, `commit_paths([bundle_dir, TASKS_PATH])`, `push_branch`, `create_pr(..., label="")` → `run.spec_pr`; `human_merge(repo, pr, head)` (как `auto_tasks._merge_spec_pr` в test); ≠0 → стоп «повторите dfl run»; `checkout_and_pull(base_ref)` → `code-run`.
4. `code-run` → `notice(TUI_HINT.format(ws=ws_id))`, `spec_runner(target_dir, ["run", "--all", "--no-strict", f"--spec-prefix={ws_id}-", "--integration-pr"])`; ≠0 → стоп; новый PR `spec-runner/run-*` → `run.code_pr` → `code-pr`.
5. `code-pr` → `pr_facts`: `MERGED` → `done` («конвейер закончен»), иначе вывод «смержите <url>» и 0.

- [ ] **Step 1: тесты** (фейковые `Ops`, агент, `launch`, `check`): `test_happy_path_to_code_pr` (все шаги, порядок вызовов Ops, аргументы `spec_runner` ровно как выше); `test_resume_each_step` (параметризация: `run.step` = каждый шаг → начинается с него); `test_fail_then_revised_then_pass`; `test_not_converged_stops_after_max_rounds`; `test_dirty_tree_stops_before_git_effects` (**RF-3**: ни одного вызова `switch_to`/`commit_paths`); `test_edge_error_stops_without_resolve` (**RF-5**); `test_code_pr_merged_marks_done`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(personal): оркестратор шагов личного пути`.

### Task 9: подключение к `dfl`

**Files:**
- Modify: `lite/dfl.py`
- Test: `tests/test_lite_dfl_personal.py`

**Interfaces:**
- Consumes: `new_personal_run`, `active_for`, `save`, `load` (Task 2); `advance` (Task 8); `claude_agent` (`lite/structure.py:656`); `ic.stdin_read`.
- Produces:
  - `TRACK_QUESTION = "Что это за проект?"`, варианты `("Личный — для себя (возможно, с близким кругом)", "Enterprise — заказчик, команда, регламенты")`.
  - `dfl run --subject '<тема>' [--track personal|enterprise]`: без `--track` — вопрос с вариантами (ответ номером, `read_answer(quick=str.isdigit)`); `personal` → `new_personal_run` + `advance`; `enterprise` → текущий путь без изменений.
  - `dfl run` без `--subject`: если `active_for(repo_slug)` есть — `advance` по нему (**RF-4**); иначе — текущий путь.
  - `dfl status`: при активном личном прогоне — строка `личный прогон <run_id>: шаг <step>` (+ `stop_reason`, если есть).
  - Ветвление — в `run()` до `_step` (`lite/dfl.py:497`); весь вызов `advance` — под `runlog.captured(_LOG, verbosity())`, как `_loop`.

- [ ] **Step 1: тесты**: `test_track_question_personal_starts_personal` (ответ «1» → `advance` вызван, `_step` нет); `test_track_flag_skips_question`; `test_enterprise_answer_keeps_old_path`; `test_resume_personal_without_subject` (**RF-4**: есть и enterprise-прогон в `RUNS_ROOT`, и активный личный → вызван `advance`, `_readable_runs` не падает); `test_status_shows_personal_step`.
- [ ] **Step 2:** FAIL. **Step 3:** реализация. **Step 4:** `uv run pytest tests/test_lite_dfl*.py -q` → PASS.
- [ ] **Step 5:** commit `feat(dfl): личный путь — выбор типа проекта и продолжение`.

### Task 10: документация и полный прогон

**Files:**
- Modify: `README.md` (раздел про `dfl`), `docs/superpowers/specs/2026-11-15-dfl-personal-path-design.md` (§8: отметка о Ruling 1–2 плана)

- [ ] **Step 1:** README: абзац «Личный проект» — первый вопрос, свободное интервью, что в брифе, что делает агент сам, где человек мержит; флаг `--track`.
- [ ] **Step 2:** полный набор `uv run pytest -q` → зелёный; `ruff check`; `uvx pyrefly check lite/personal lite/dfl.py lite/edge_resolve.py` → 0 ошибок.
- [ ] **Step 3:** commit `docs(dfl): личный путь (#764)`.

Живая приёмка (владелец, после мержа): новый личный проект `dfl run --subject …` → интервью без вопросов enterprise, противоречие уточнено вопросом, бриф подтверждён «да», дизайн и декомпозиция прошли edge-check, spec-PR вмержен агентом, integration-PR — человеку.
