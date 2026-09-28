# Замена отклонённой первой доставки (§I10 для v1) — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** воркстрим, чей tasks-PR первой доставки закрыт человеком без мержа,
переиздаётся ходом `--supersede --replace-revision 1 --reason …`; доставка из
состояния леджера «успешной доставки нет» (NSD) работает на любой цепочке
замен и отказов, а не на один заход.

**Spec:** `docs/superpowers/specs/2026-09-28-v1-closed-pr-redelivery-design.md`
(rev 3.1) — согласование владельцем и решение Q-1 — Task 0.

**Tech Stack:** Python 3.12, `uv run --frozen --group governance pytest -q`
(без группы тесты моста молча skip), ветка → PR → терминальное ревью.

## Global Constraints

- Файлы моста на master не в ruff-формате — **не переформатировать**; `ruff
  check` сверять с master множеством (новых замечаний — ноль); pyrefly — по
  изменённым файлам.
- «Нет эффекта» — по `ops.calls` (нет `close_pr`, `ensure_branch`,
  `commit_paths`, `push_branch`, `create_draft_pr`) и побайтово по
  `run.json`; `ops.touched` для этого непригоден.
- Двойник, красный и до правки, проверяет **текст** диагностики нового
  правила.
- Для N ≥ 2 поведение не меняется: существующие тесты §I10 — регрессия.
- `_reconcile_v1`, `_replacement_close`, `_replacement_cleanup` **не
  правятся** (спека §7): при forward-ссылке недостижимы / уже выходят рано.
- Каждая задача оставляет полный набор зелёным.

---

### Task 0: Согласование (решение владельца)

- [ ] Спека rev 2 и **Q-1** (гейт «закрыл человек»: (а) отказ при
  агентском акторе / удалении ветки — рекомендация; (б) только журнал).
  Мерж PR спеки согласованием не считается.

### Task 1: Факты форджи — закрытие и ошибки `pr_facts`

**Files:** `governance/ops.py` (протокол `Ops`, `RealOps`); фейки во всех
тестовых модулях, реализующих `Ops` (task_bridge, runner, spec_loop,
approval_facts, accept_pr, approve_node — проверить pyrefly по каждому).

- [ ] **Step 1 (red):** `RealOps.pr_closure(repo_slug, pr)` — один GraphQL-
  запрос `timelineItems(itemTypes:[CLOSED_EVENT, REOPENED_EVENT,
  HEAD_REF_DELETED_EVENT], last: 3)` + `state`; последний из
  Closed/Reopened — `ClosedEvent` → `{"closed_by", "closed_at"}` (для Q-1(а)
  — признак `HeadRefDeletedEvent` того же актора раньше или в ту же
  секунду; фикстура с порядком #373 — гейт пропускает); `ReopenedEvent` /
  пусто / ошибка вызова → `None`. Подмена `subprocess.run`, как у соседних
  `RealOps`-тестов.
- [ ] **Step 2 (red):** ошибка `pr_facts` на пути хода → `RuntimeError` с
  диагностикой; обёртка **в `_validate_replacement`**, не в `RealOps`
  (26 вызовов `pr_facts` в `governance/` поведения не меняют).
- [ ] **Step 3 (green):** реализация; фейки получают настраиваемый
  `pr_closure`.

### Task 2: Предикат NSD и доставка из него

**Files:** `governance/task_bridge.py` (`deliver_superseded`, новые
`_no_successful_delivery`, S8-чтение в общем помощнике с `deliver_for_run`).

- [ ] **Step 1 (red):** фикстура **без спеки в base и в дереве**; леджер, где
  единственная доставка отозвана (`replaces_revision: 1`, неразряжена).
  Ожидание: доставка в NSD — `supersedes: null`, `version: 1`, слово §I8 с
  префиксом `(comparison: unavailable)`, `dag_source: "none"`, S8 verdicts
  переданы в `deliver`.
- [ ] **Step 2 (red):** спека в base есть при NSD → отказ «леджер и base
  расходятся»; чтение base упало → отказ (строгий `show_file_for_carry`, не
  `show_file`); без эффекта.
- [ ] **Step 2a (red):** точный п.1 предиката — `tasks-deliver: started` +
  `--supersede` и пустой леджер → прежние отказы, NSD не включается.
- [ ] **Step 3 (red):** нет `s8-gate-verdicts.jsonl` → отказ до эффекта.
- [ ] **Step 4 (red):** цепочки — брошенная ревизия-замена (перенятое
  обязательство) и закрытый PR ревизии-замены (`--replace-revision M`) → обе
  доставляются в NSD.
- [ ] **Step 4a (red):** повтор при `started` NSD-ревизии (путь `continue`)
  → S8 передан в `deliver`.
- [ ] **Step 5 (green):** предикат §3 спеки на месте сегодняшнего
  `prev is None`; признак `nsd: true` в намерении; S8-чтение — помощник,
  общий для `deliver_for_run`, NSD и пути `continue`;
  `_pending_replacement` переносит `replaces_closed_by/at` (тест).

### Task 3: Ход N = 1

**Files:** `governance/task_bridge.py` (`_validate_replacement`,
`_check_replacement_target`, CLI help).

- [ ] **Step 1 (red):** основной сценарий **#373-подобный** — v1
  `completed`, PR закрыт владельцем ради подрезки очереди (`pr_closure` →
  владелец), причина задана → ревизия-замена с полями §4.2 спеки, доставка в
  NSD (Task 2), v1 побайтово прежняя.
- [ ] **Step 2 (red):** двойники §9 для хода — вмержен, открыт (текст
  ручного выхода), `pr_facts` падает, нет `pr`, нет `--reason` (текст),
  последний timeline — `ReopenedEvent`, Q-1(а) агентский актор / удаление
  ветки; повтор при `started` замене; ответы `--replace-revision 1` при
  живой / брошенной замене и при закрытом PR M (спека §4.4).
- [ ] **Step 3 (green):** ветка `n == 1` в `_validate_replacement`;
  сообщение `_check_replacement_target` для N = 1 при OPEN; докстринг без
  обещания «обычной доставки»; help `--replace-revision`.

### Task 4: Гонка с переоткрытием

**Files:** `governance/task_bridge.py` (`deliver_for_run`,
`deliver_superseded`).

- [ ] **Step 1 (red):** M `completed` с открытым PR, v1 переоткрыт →
  отказ на `--supersede`, а не `returned M`; v1 вмержен после замены при
  живой M → отказ с процедурой, **а после выполнения процедуры следующий ход
  проходит** (брошенная M / M с закрытым PR проверку не держат); без эффекта.
- [ ] **Step 2 (red):** `deliver_for_run` после замены v1: M `completed`
  (PR открыт/вмержен) → PR M со строкой о замене, RC 0; M `started` → отказ
  «доиграйте»; PR M закрыт → подсказка `--replace-revision M`; никогда не
  «доставлена: PR #373».
- [ ] **Step 3 (green):** в `deliver_superseded` — **до цикла
  реконсиляции**, после `_validate_replacement`; в `deliver_for_run` —
  внутри ветки `completed` до раннего возврата.

### Task 5: Мутации и контракт

- [ ] Мутации: каждый пункт предиката NSD, каждое условие §4.1, S8, проверка
  спеки в base, обе проверки §5 — убиваются своим тестом.
- [ ] Контракт `2026-09-09-tasks-supersede-contract-design.md`: §I10 (v1 и
  подраздел NSD), §I6 (`version = 1` в NSD) — по §7 спеки.

### Task 6: Живая приёмка на devtools#171 (оператор + владелец)

- [ ] Ход на прогоне `i5-traceless-noop-after-abandon-20260923-3e0ea3`
  (PR #373) → tasks-PR → терминальное ревью → approve владельца → прогон
  spec-runner по схеме #173 (клон `.worktrees/devtools-run`; путь бандла #171
  в `harness_files` — PR-ом) → integration-PR → приёмка.
