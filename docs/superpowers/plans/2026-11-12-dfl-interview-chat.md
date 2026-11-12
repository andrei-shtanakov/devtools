# Интервью `dfl` разговором — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Коллега проходит интервью DarkFactory, отвечая текстом в терминале; агент раскладывает ответ на пункты, скрипт проверяет и отправляет в `discovery`; есть просмотр состояния и поправки текстом.

**Architecture:** `lite/interview_probe.py` исполняется интерпретатором `discovery` и отдаёт JSON состояния сессии; `lite/interview_state.py` вызывает его. `lite/structure.py` — гибрид: агент (`claude -p`) → JSON → скрипт (проверка, id, YAML). `lite/interview_chat.py` — цикл вопрос → ответ → уточнения → отправка. `lite/dfl.py` получает цикл в `run`/`answer`, команды `interview` и `correct`; раннер в пути `dfl` молчит про `discovery answer`.

**Tech Stack:** Python 3.12 stdlib, PyYAML (`pyyaml>=6.0.3` уже в зависимостях devtools), `discovery` на пине `c7961b9`, `claude` CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-dfl-interview-chat-design.md`

## Global Constraints

- Только режим test (`dfl` уже отказывает в strict); вызов агента — в `lite/`, не в `governance/`, не через `author_launch`.
- Агент: `claude -p` с `--safe-mode`, модель `df_config.load().author_model`; `author_harness != "claude"` → отказ «интервью разговором требует harness.author = claude — используйте dfl answer --file».
- Уточнений агента — не больше 2 на вопрос; повтор агента при ошибке формы/отказе `discovery` — 1.
- id: следующий номер префикса после **всех когда-либо объявленных** в журнале; `Q`/`X` агент не создаёт.
- Обязательные поля (gate_check): `FR` — `traces` на существующий `G`/`J`, `Priority` ∈ {Must, Should, Could, Won't}, `Acceptance`; `NFR` — `Acceptance` или `Target`; `M` — `traces` на `G`.
- Ответ уходит с `--question <id>`; `--supersede` — только `dfl correct`.
- Без tty — прежнее поведение; `dfl answer --file` не меняется; `--question`/`--supersede` только с `--file`.
- Пользователь не видит YAML, JSON и трассировки.
- `discovery` не правится; его код читается интерпретатором его окружения (`uv run --frozen --project <discovery> python -I <скрипт>`).

## Review Focus

1. Агент приписал факт, которого нет в ответе (например, критерий приёмки) — промпт запрещает, а пустое поле ведёт к уточнению, не к выдумке — тест в Task 2.
2. Ответ на вопрос «цели» без единого `G` — уточнение, а не отправка непокрывающего ответа — тест в Task 2.
3. Обрыв после отправки и до следующего вопроса — продолжение не дублирует ответ (берёт следующий вопрос из `discovery status`) — тест в Task 4.
4. Поправка пункта, чей держатель — ответ с другими пунктами: остальные пункты ответа сохранены — тест в Task 5.
5. Ввод с не-ASCII, пустой ответ, «пропустить» на первом же вопросе — тест в Task 4.

---

### Task 1: Состояние сессии из `discovery`

**Files:**
- Create: `lite/interview_probe.py`, `lite/interview_state.py`
- Test: `tests/test_lite_interview_state.py`

**Interfaces:**
- Produces:
  - `lite/interview_probe.py` — скрипт: `python -I interview_probe.py <session_id>`; импортирует `discovery.cli.sessions_root`, `discovery.journal.Journal`, `discovery.render.effective_entries/entry_holders/readiness`, `discovery.contract.gate_check.FRAMES`; печатает JSON `{"frame", "required": {key: prefix|null}, "optional": {...}, "entries": [{"id","body","fields"}], "declared": [id…всех деклараций], "holders": {id: question_id}, "answers": {question_id: payload-dict последнего ответа}, "answered_keys": [...], "readiness": verdict, "findings": [...]}`; frame — из `header.json` сессии.
  - `@dataclass(frozen=True) class SessionView` с теми же полями (`required: dict[str, str | None]`, `entries: list[Entry]`, `Entry(id, body, fields)`), методы `prefixes() -> dict[str, str]` (prefix → coverage_key), `covered() -> set[str]`, `next_id(prefix) -> str`.
  - `load_session(session_id: str, run: Runner = subprocess.run) -> SessionView`; сбой — `InterviewStateError` с причиной.

- [ ] **Step 1: Write the failing tests**

```python
def test_load_session_parses_probe_json(fake_runner): ...   # фейковый stdout -> SessionView
def test_next_id_after_all_declared(): ...   # declared [FR-01, FR-03] (FR-03 заменён) -> "FR-04"
def test_covered_by_prefix_and_answered_keys(): ...
def test_probe_failure_raises_with_reason(fake_runner): ...
@pytest.mark.skipif(not LIVE_DISCOVERY, reason="нет .venv discovery")
def test_probe_reads_real_session(tmp_path): ...   # discovery start + answer настоящим CLI (DISCOVERY_HOME=tmp) -> entries/holders
```

- [ ] **Step 2: Run** `uv run pytest tests/test_lite_interview_state.py -q` — FAIL.
- [ ] **Step 3: Implement** оба модуля (`LIVE_DISCOVERY` — `df_config.workspace_dir("discovery")/.venv` существует; путь к discovery — `df_config.workspace_dir("discovery")`, как у `_discovery_answer`).
- [ ] **Step 4: Run** — PASS (live — на этой машине).
- [ ] **Step 5: Commit** `feat(dfl): состояние сессии интервью из discovery (probe)`.

### Task 2: Раскладка ответа (`lite/structure.py`)

**Files:**
- Create: `lite/structure.py`
- Test: `tests/test_lite_structure.py`

**Interfaces:**
- Consumes: Task 1 `SessionView`.
- Produces:
  - `@dataclass(frozen=True) class Question: id: str; coverage_key: str; text: str`
  - `Agent = Callable[[str], str]` (промпт → сырой stdout); `claude_agent(model: str) -> Agent` (argv `["claude", "-p", "--safe-mode", "--model", model, "--output-format", "text"]` + запрет инструментов флагом, который принимает установленный `claude` — проверить `claude --help`; промпт в stdin; таймаут 300 с).
  - `Ready(payload: str)`, `Ask(question: str)`, `Failed(message: str)`; `structure_answer(q: Question, text: str, view: SessionView, agent: Agent, *, may_ask: bool) -> Ready | Ask | Failed`.
  - `build_prompt(q, text, view) -> str`; `parse_items(raw: str) -> list[dict]` (+ `ask`); `validate(items, q, view) -> list[str]` (нарушения); `to_payload(text: str, items, view) -> str` (YAML: `text`, `entries` с назначенными id).
- Поведение: агентский JSON `{"items": [{"prefix","body","traces","priority","acceptance","target"}], "ask": str|null}`. Битый JSON или нарушение формы → один повтор агента с текстом ошибки, затем `Failed("не удалось разобрать ответ — переформулируйте")`. Нет пункта требуемого префикса (если префикс не `None`) или пустое обязательное поле → `Ask` (если `may_ask`), иначе `Ready` с тем, что есть. `ask` от агента → `Ask`. `traces` на несуществующий id → повтор агента. Пустой `items` у темы без префикса → `Ready` с одним `text`.

- [ ] **Step 1: Write the failing tests** (поддельный агент — список ответов)

```python
def test_goal_answer_becomes_g_entry_with_next_id(): ...
def test_items_of_other_prefixes_allowed(): ...           # ответ про цели даёт и FR
def test_missing_required_prefix_asks(): ...              # нет G на вопрос goals -> Ask
def test_fr_without_acceptance_asks_not_invents(): ...    # пустой acceptance -> Ask; в Ready не попадает выдуманный
def test_no_ask_left_sends_what_is_there(): ...           # may_ask=False -> Ready
def test_broken_json_retried_once_then_failed(): ...
def test_traces_to_unknown_id_retries_agent(): ...
def test_q_and_x_prefixes_rejected(): ...
def test_feasibility_review_text_only(): ...
def test_payload_text_is_human_answer_verbatim(): ...
def test_prompt_forbids_inventing_and_lists_rules(): ...  # в промпте есть правила полей и запрет
def test_claude_agent_argv_has_safe_mode_and_model(): ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(dfl): раскладка ответа интервью — агент + проверка скриптом`.

### Task 3: Раннер в пути `dfl` молчит про `discovery answer`

**Files:**
- Modify: `governance/runner.py` (`_print_answer_hint` и три других места печати `iv.answer_command`/`supersede_template`/`make …` в стадии интервью)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Produces: `runner.quiet_interview_hints()` — контекстный менеджер (`contextvars`): внутри раннер **не печатает** ни вопрос, ни команды ответа (`discovery answer …`, шаблон `--supersede`, `make …`) — вопрос показывает `dfl`; вне — как сейчас.

- [ ] **Step 1: Test** `test_quiet_interview_hints_suppresses_answer_command` (стоп на вопросе под `quiet_interview_hints()` — в stdout нет `discovery answer` и `make`; вне — есть, как раньше).
- [ ] **Step 2–4:** FAIL → implement → PASS (`uv run pytest tests/test_governance_runner.py -q -k "interview or answer_hint"`).
- [ ] **Step 5: Commit** `feat(runner): тихие подсказки интервью для пути dfl`.

### Task 4: Цикл интервью в `dfl run`/`dfl answer`

**Files:**
- Create: `lite/interview_chat.py`
- Modify: `lite/dfl.py` (`run`, `answer`, `_parser`: `--file` необязателен; `_hint_if_waiting`)
- Test: `tests/test_lite_interview_chat.py`, `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `chat(state: rs.RunState, *, read: Callable[[str], str | None], agent: Agent, status: Callable[[str], iv.DiscoveryReply], send: Callable[[str, Path, str], iv.DiscoveryReply], load: Callable[[str], SessionView]) -> ChatResult` (`finished` — вопросы кончились; `stopped` — человек вышел).
  - Ввод ответа `read_answer(prompt) -> str | None`: строки до пустой; `None` на EOF или `/стоп`; `пропустить`/`не знаю` — маркер пропуска.
  - Вопрос: `discovery status` сессии → `next_action` (`question_id`, `coverage_key`, `question_text`); код не 20 — интервью кончилось.
  - `dfl run`: прогон в `waiting_interview` и `sys.stdin.isatty()` → `chat(...)` под `quiet_interview_hints()`; `finished` → обычный шаг `run` (как сейчас после `dfl answer`); `stopped` → «интервью прервано — продолжить: dfl run», код 0.
  - `dfl answer` без `--file`: то же, только в терминале; без tty — отказ «без терминала: dfl answer --file answer.yaml».
  - Экран: `Вопрос <n> (<тема>): <текст>` и `> ` для ввода; уточнение — `Уточнение: <текст>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_chat_answers_until_interview_complete(): ...      # 2 вопроса -> 2 send с --question
def test_chat_follow_up_appended_to_text(): ...            # Ask -> ответ -> Ready; text содержит оба
def test_chat_max_two_follow_ups(): ...
def test_chat_skip_sends_what_is_there(): ...              # «пропустить» на первом вопросе
def test_chat_eof_stops_and_resume_takes_next_question(): ...  # send прошёл, EOF; повторный chat берёт следующий question_id из status
def test_chat_discovery_refusal_retries_agent_once(): ...
def test_chat_failed_structure_reprompts_human(): ...
def test_chat_unicode_and_empty_answer(): ...             # пустой ответ -> повтор вопроса
def test_dfl_run_without_tty_keeps_old_hint(env, capsys): ...
def test_dfl_answer_file_still_works(env): ...
def test_dfl_answer_without_file_needs_tty(env): ...
def test_dfl_answer_question_requires_file(env): ...
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/test_lite_*.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(dfl): интервью разговором в терминале`.

### Task 5: `dfl interview` и `dfl correct`

**Files:**
- Modify: `lite/dfl.py`, `lite/interview_chat.py` (или новый `lite/interview_view.py`)
- Test: `tests/test_lite_interview_chat.py`, `tests/test_lite_dfl.py`

**Interfaces:**
- Consumes: Task 1 (`load_session`, `SessionView.holders/answers`), Task 2 (`Agent`, `parse_items`, `validate`).
- Produces:
  - `render_view(view: SessionView, current: Question | None) -> str` — текущий вопрос; темы `[x]`/`[ ]` (обязательные, затем необязательные); пункты по темам: `FR-01 <body> — Must; критерий: <Acceptance>; → G-01`; находки готовности по-человечески.
  - `correct(state, text: str, *, agent, load, send) -> str` — агент получает действующие пункты и поправку, возвращает изменённые пункты с id (`{"items":[{"id",...}], "ask"}`); скрипт для каждого — держатель `holders[id]`; ответ держателя пересобирается целиком (`answers[qid]`: `text` прежний, `entries` — прежние с заменой изменённых), отправка `--question <qid> --supersede`; возврат строки `FR-01: «…» → «…»`; не понял — уточнение (`Ask`) один раз.
  - CLI: `dfl interview`, `dfl correct '<текст>'` (прогон в интервью или с сессией; иначе отказ «интервью не идёт»).

- [ ] **Step 1: Write the failing tests**

```python
def test_render_view_topics_entries_and_findings(): ...
def test_correct_rebuilds_holder_answer_keeping_other_entries(): ...  # Review Focus 4
def test_correct_uses_supersede_with_holder_question(): ...
def test_correct_unclear_asks_once(): ...
def test_dfl_interview_without_session_refuses(env): ...
```

- [ ] **Step 2–4:** FAIL → implement → PASS (`uv run pytest tests/test_lite_*.py -q`).
- [ ] **Step 5: Commit** `feat(dfl): dfl interview и dfl correct`.

### Task 6: Памятка, inbox в discovery, живая проверка

- [ ] Памятка `docs/darkfactory-for-colleagues.md`: раздел «Интервью: как отвечать» переписать под разговор (YAML-путь — коротко, «для тех, кто хочет сам»); команды `dfl interview`, `dfl correct`; `tests/test_lite_docs.py` зелёный.
- [ ] Inbox-issue в `andrei-shtanakov/discovery` (метка `inbox`, `slug: discovery-session-view`, `from: devtools`): публичная команда просмотра сессии (действующие пункты, держатели, покрытие) — заменит внутренний API в `lite/interview_probe.py`.
- [ ] Commit `docs(dfl): памятка — интервью разговором`.
- [ ] Живая проверка (после ревью PR): сервер `ssh -t user@homelab`, учётка `betta-tester`, ветка PR; новая тема в `df-linux-smoke` → интервью разговором до PR брифа без YAML; `dfl interview` на середине; одна `dfl correct`; результат — в PR; брошенный конвейер — `dfl abandon`; сервер — на master.
