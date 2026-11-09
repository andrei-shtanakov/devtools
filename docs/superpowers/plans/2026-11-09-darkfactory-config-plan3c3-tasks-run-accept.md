# DarkFactory config — план 3c-3: задачи, `spec-runner run`, приёмка без мержа

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** в прогоне `test` после `completed` конвейер сам проходит задачи до проверенного
PR с кодом. По шагам: tasks-PR → `spec approve tasks` → approve-PR → `spec-runner run
--integration-pr` → приёмка integration-PR **без мержа**. Итог — открытый PR, проверенная
голова и URL в журнале. Мерж PR с кодом остаётся за коллегой.

**Результат точно:** `make spec-loop …` на прогоне `test` в статусе `completed` при зелёном
пути завершается строкой «PR с кодом готов: <URL> … мерж — за вами (test)» и кодом 0, без
человеческих действий. Повторный вызов на той же голове PR не делает платных вызовов и ничего
не мержит. `strict` не меняется, кроме общих долгов из Task 1.

**Architecture:** новая цепочка — модуль `governance/auto_tasks.py`. Его зовёт
`spec_loop._deliver_phase` только при `state.mode == "test"`. Каждый шаг — операция журнала
`run.json` с write-ahead (`op_start` до эффекта) и идемпотентным повтором по записи и фактам
форджи. Spec-PR (tasks и approve) мержатся существующим `Ops.human_merge` (`human-merge.sh
--expect-head`) после ожидания обязательных проверок — как candidate в 3c-2. Приёмка — новый
режим `accept_pr.accept(..., test=NoMergeAccept(...))`: ревью → вердикт из маркера в журнал →
ожидание проверок → стоп **до** мержа. Все правки — не authority-root и не харнесс: PR мержит
агент без `--authority-override`.

**Tech Stack:** Python 3.12 (uv), pytest; spec-runner 5.0.0 (CLI `spec approve`, `run`).

**Spec:** `docs/superpowers/specs/2026-11-07-darkfactory-config-design.md` §5.3 п.3
(задачи и run автоматически; «Приёмка без мержа», ревью f9cd546 R1), п.5 (путь клона из
записи списка целей), п.7 («положительный результат обязателен… на актуальной голове,
записанный в журнал»; «при `resume` на той же голове используется записанный вердикт»), §5.7.
Предыдущий план: `docs/superpowers/plans/2026-11-09-darkfactory-config-plan3c2-auto-waves.md`.

## Решения исполнителя (проверить владельцу)

1. **Мерж spec-PR в `test`.** Это tasks-PR `spec/<ws>-tasks` и approve-PR
   `spec/<ws>-tasks-approve`; в `strict` их мержит человек. В `test`:
   - `gh pr ready` черновика;
   - ожидание обязательных проверок (`approve_node._await_required_checks`, тот же предикат,
     что у finalize);
   - `Ops.human_merge(..., --expect-head)`.

   Перед мержем в операцию журнала write-ahead пишется
   `tasks_skip = "утверждение задач человеком не требуется (test)"`, и та же фраза
   выводится. `merge-pr.sh` не используется: ему нужен approve ревью-контура, а ревью
   spec-PR в `test` не требуется (разведка 3c, Q3).
2. **Репо без проверок CI.** Пустой rollup читается как `pending` (fail-closed,
   `pr_checks.checks_state`). Значит, в репо совсем без чеков цепочка (и candidate после
   Task 1) встаёт через `40 × 30 с` с названной причиной. Ослаблять предикат в `test` не
   стал: спека не называет такого пропуска. **Владельцу:** либо `dfl init-repo` (план 4)
   обязан ставить хотя бы одну проверку, либо в `test` вводится именованный пропуск
   «проверок нет (test)».
3. **`spec-runner run`.** Команда: `spec-runner run --all --strict --spec-prefix=<ws>-
   --integration-pr`, cwd — `state.target_dir`. PR с кодом — единственный **новый** открытый
   PR с head `spec-runner/run-*`: набор открытых таких PR записывается write-ahead до запуска.
   0 или ≥2 новых — стоп с названной причиной.
4. **Приёмка в `test` без журнала прогона (CLI `make accept-pr`).** Тот же ритуал без мержа,
   результат только в выводе. Журнал пишет цепочка `auto_tasks`, у которой есть прогон.
   Цель CLI в `test` берётся из `targets.toml` (`user_targets.lookup`), а не из
   `workspace.root/<repo>`.
5. **Вне плана, с причиной:**
   - Закрытие критериев (`acceptance_provenance`: test-координаты → 404, `login == agent`).
     Оно идёт после мержа PR с кодом, то есть за пределами автоматической цепочки D3.
     Потребитель `closure_gate` бежит в CI цели без конфига DarkFactory (strict). Вопрос
     переносится в план 4 вместе с README.
   - Харнесс-долги одним PR владельца («3c-H»):
     - строки баннера `TEST_RELAXATIONS` про задачи и приёмку;
     - текст scope-аттестации в `review-pr.sh`;
     - код `act_barrier` 2 против 3 в доке;
     - `df_config.mode_or_strict()` вместо трёх копий «нет файла = strict»;
     - реестр act identity для test-веток 3c-2.

## Global Constraints

- `strict` не меняется: каждая новая ветка поведения — под `state.mode == "test"` (в
  `accept_pr` — под `test is not None`); сигнатуры strict-вызовов не меняются, новые
  kwargs — со значением по умолчанию.
- Не трогать authority-root/харнесс-пути: `contracts/`, `governance/act_*`,
  `governance/acceptance_provenance*`, `governance/df_config*`, `governance/policy_rule*`,
  `review-pr.sh`, `merge-pr.sh`, `human-merge.sh`, `tests/test_act_*`, `tests/harness_trust*`,
  `tests/conftest*` (полный перечень — `contracts/authority-root/v1/paths.env:202`).
- Новый `subprocess.run` в `governance/` — только с литеральной программой первым элементом
  argv из `allowed_literal_programs` (`git`, `gh`, `spec-runner`); иначе краснеет
  `tests/test_author_sandbox_invariant.py`, а его контракт — authority-root. После каждой
  задачи этот тест гоняется явно.
- Строки именованных пропусков — дословно, константами:
  - `TASKS_SKIP_TEST = "утверждение задач человеком не требуется (test)"`
    (`governance/auto_tasks.py`);
  - `ACCEPT_NO_MERGE_TEST = "мерж PR с кодом — за вами (test)"`
    (`governance/accept_pr.py`).
- Ключи операций журнала — дословно: `tasks-merge`, `tasks-approve`, `approve-merge`,
  `spec-run`, `code-accept`.
- Тестовые двойники `Ops` обязаны принимать новые kwargs (CI — Linux, часть тестов локально
  скипается без steward): полный набор —
  `PYTHONPATH=<…>/steward/src uv run pytest -q -p no:cache`.
- Ruff — `uvx ruff check` / `uvx ruff format --check`; pyrefly — как в 3c-2 (rsync в
  scratch, `--python-interpreter-path <worktree>/.venv/bin/python`).

## Review Focus

1. Повтор `make spec-loop` после падения посреди цепочки (между мержем tasks-PR и `spec
   approve`, между `run` и записью PR). Ожидание: продолжение с первого незавершённого
   шага, без второго PR, без второго `spec-runner run`, если PR уже открыт. Тесты — в Task 2
   и Task 3.
2. Коллега запушил фикс в PR с кодом после приёмки. Ожидание: повтор видит новую голову и
   делает новое ревью; старый вердикт не засчитывается. Тест — в Task 4.
3. Ревью вернуло 0, но в маркере `verdict=request-changes`, маркера нет или он на чужой
   голове. Ожидание: стоп с причиной, не «PR готов». Тест — в Task 4.
4. Красные или незавершённые проверки на spec-PR или candidate. Ожидание: стоп с именами
   проверок, без мержа. Тесты — в Task 1 и Task 2.
5. Две открытые integration-PR (старая брошенная + новая). Ожидание: берётся только новая
   (не из записанного набора «до»); если новых две — стоп. Тест — в Task 3.

---

### Task 1: долги 3c-2 — проверки перед автомержем candidate, `prev` при переаттестации, режим в `rs.load`

**Files:**
- Modify: `governance/runner.py` (`_auto_merge_candidate` ~1224, `_attest_finalize` ~1300)
- Modify: `governance/run_state.py` (`load` ~296)
- Test: `tests/test_governance_runner.py`, `tests/test_governance_run_state.py` (если файла
  нет — тест в `tests/test_governance_runner.py`)
- Test (new): `tests/test_governance_test_mode_reach.py`

**Interfaces:**
- Consumes: `approve_node._await_required_checks(state, ops, pr, head, *, kind) ->
  MergeRefusal | None`; `run_state.validate_mode(mode) -> None` (бросает `ValueError`).
- Produces: ничего нового для следующих задач.

- [ ] **Step 1: Write the failing tests**

```python
def test_test_mode_candidate_waits_checks_before_merge(...):
    # edge PASS на голове, rollup [{"name": "ci", "conclusion": "FAILURE"}]
    # → ops.human_merge не вызван; state.status == "stopped_merge_refused";
    # текст стопа содержит "ci"
def test_test_mode_candidate_merges_after_green_checks(...):
    # rollup SUCCESS → human_merge вызван ровно раз с (repo, pr, head)
def test_reattest_keeps_prev_when_head_unreadable(...):
    # аттестация на старой голове h1 (review_exit 0), голова finalize сменилась,
    # pr_head при переаттестации бросает RuntimeError
    # → state.ops[att_key]["prev"] == {"head": h1, "review_exit": 0}
def test_load_rejects_unknown_mode(tmp_path, monkeypatch):
    # run.json с "mode": "fast" → rs.load бросает ValueError с "fast" в тексте
```

`tests/test_governance_test_mode_reach.py`:

```python
def test_test_run_creates_no_review_v1_requests():
    # act_node.creation(state_test, ops, deps(settings=ActSettings(True, False)),
    #                   act_status.TEST_LIVE_POLICY).act is None
```

Это фиксирует вывод ревью #721: путь `review/v1` (`act_node._finalize`, вызовы
`finalize_merge_policy` без `mode`) в `test` недостижим. Правка `governance/act_node.py` не
нужна.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_governance_runner.py -k "waits_checks or after_green or keeps_prev" tests/test_governance_test_mode_reach.py`
Expected: FAIL (merge без проверок; `prev` отсутствует; `load` принимает "fast"). Тест
недостижимости должен пройти сразу — это тест-фиксация, не TDD-красный.

- [ ] **Step 3: Implement**
- `_auto_merge_candidate` выполняет шаги по порядку:
  1. сверяет edge-вердикт;
  2. пишет write-ahead `act_skip`;
  3. зовёт `an._await_required_checks(state, ops, pr, head, kind="candidate-PR")`. При
     отказе — `_stop_with_comment(state, ops, "stopped_merge_refused", <волна, PR, причина
     отказа>)`, `return False`;
  4. только после этого вызывает `human_merge`.
- `_attest_finalize` получает `prev`. Его нужно записать в `op_start` при первом старте
  (`_ensure_started(state, att_key, **fields)` с новым параметром `**fields`), чтобы стоп на
  нечитаемой голове его не терял. При `op_complete` `prev` остаётся как сейчас.
- `rs.load` вызывает `validate_mode(state.mode)` после конструирования. Вызывающие уже ловят
  `ValueError` (`act_barrier`, `console*`, `spec_loop` — проверено).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_governance_runner.py tests/test_governance_test_mode_reach.py tests/test_author_sandbox_invariant.py`
Expected: PASS

- [ ] **Step 5: Commit** — `fix(runner): test-mode candidate waits required checks; keep prev attestation; validate run mode on load`

---

### Task 2: `auto_tasks` — мерж tasks-PR, `spec approve`, мерж approve-PR

**Files:**
- Create: `governance/auto_tasks.py`
- Modify: `governance/ops.py` — `Ops` protocol + `RealOps`: `mark_ready`, `spec_runner`
- Test (new): `tests/test_governance_auto_tasks.py`; `tests/test_governance_ops.py`

**Interfaces:**
- Consumes:
  - `task_bridge.deliver_for_run(state, ops) -> int` (идемпотентна; по завершённой
    `tasks-deliver` возвращает тот же PR);
  - `task_bridge.check_approved(target_dir, ws_id, bundle_dir)` (RuntimeError, если не
    утверждено);
  - `task_bridge.approve_command(ws_id) -> str`;
  - `task_bridge.deliver_approve(state, ops) -> int`;
  - `approve_node._await_required_checks`;
  - `Ops.human_merge(repo, pr, sha, *, target_dir)`;
  - `Ops.checkout_and_pull(target_dir, branch)`;
  - `Ops.pr_facts(slug, pr)`.
- Produces:
  - `Ops.mark_ready(repo_slug: str, pr: int) -> bool` — `gh pr ready <pr> -R <slug>`,
    `True` при rc 0.
  - `Ops.spec_runner(target_dir: str, args: list[str]) -> int` —
    `subprocess.run(["spec-runner", *args], cwd=target_dir, check=False).returncode`.
  - `auto_tasks.TASKS_SKIP_TEST: str`.
  - `auto_tasks.run_chain(state: RunState, ops: Ops, tasks_pr: int) -> int` — 0, когда PR с
    кодом проверен; 1 — стоп (причина в выводе). В этой задаче цепочка останавливается после
    `approve-merge` с `return 0`, Task 3 и Task 4 её продолжают.
  - `auto_tasks._merge_spec_pr(state, ops, key: str, pr: int, kind: str) -> bool`.

- [ ] **Step 1: Write the failing tests** (`tests/test_governance_auto_tasks.py`, двойник
  `_ChainOps` с записью вызовов, факты PR — словари `{"state", "isDraft", "headRefOid",
  "statusCheckRollup"}`):

```python
def test_tasks_pr_ready_checks_merge_then_pull():
    # PR draft OPEN, rollup SUCCESS → вызовы по порядку:
    # mark_ready(slug, T), human_merge(repo, T, head, target_dir=...),
    # checkout_and_pull(target_dir, base)
    # state.ops["tasks-merge"] == {"status": "completed", "pr": T, "head": head,
    #                              "tasks_skip": TASKS_SKIP_TEST}
def test_tasks_skip_written_before_merge():
    # human_merge бросает → state.ops["tasks-merge"]["tasks_skip"] уже записан,
    # status == "started"
def test_red_checks_stop_without_merge(capsys):
    # rollup FAILURE "lint" → run_chain == 1, human_merge не вызван, "lint" в выводе
def test_already_merged_tasks_pr_is_not_merged_again():
    # facts state MERGED → ни mark_ready, ни human_merge; операция completed
def test_spec_approve_runs_only_when_not_approved(monkeypatch):
    # check_approved бросает → ops.spec_runner(target_dir,
    #   ["spec", "approve", "tasks", "--profile", <p>, "--spec-prefix", f"{ws}-"]);
    # check_approved проходит → spec_runner не вызван
def test_approve_pr_merged_with_same_gate():
    # deliver_approve → A; A проходит _merge_spec_pr под ключом "approve-merge"
def test_resume_after_crash_between_merge_and_approve():
    # tasks-merge completed, tasks-approve отсутствует → повтор начинает с approve,
    # mark_ready/human_merge для T не вызываются
def test_spec_approve_nonzero_stops(capsys):
    # spec_runner → 3 → run_chain == 1, "spec approve" и "3" в выводе
```

`tests/test_governance_ops.py`:

```python
def test_mark_ready_argv(fake_run):   # ["gh","pr","ready","7","-R","o/r"]
def test_spec_runner_argv_and_cwd(fake_run):  # argv[0] == "spec-runner", cwd == target_dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_governance_auto_tasks.py tests/test_governance_ops.py -k "auto_tasks or mark_ready or spec_runner"`
Expected: FAIL — `ModuleNotFoundError: governance.auto_tasks` / `AttributeError: mark_ready`

- [ ] **Step 3: Implement**
- Порядок `run_chain`:
  1. `_merge_spec_pr(state, ops, "tasks-merge", tasks_pr, "tasks-PR")`;
  2. `tasks-approve`:
     - `check_approved`, а при `RuntimeError` — `ops.spec_runner(target_dir,
       approve_command(ws).split()[1:])`;
     - rc ≠ 0 — стоп;
     - затем `deliver_approve(state, ops)` → A, номер записывается в операцию;
  3. `_merge_spec_pr(state, ops, "approve-merge", A, "approve-PR")`.
- `_merge_spec_pr` выполняет шаги по порядку:
  1. завершённая операция — `True`;
  2. факты PR: `MERGED` → `op_complete` без эффектов;
  3. `CLOSED` → стоп «PR закрыт без мержа — решите вручную»;
  4. `op_start(state, key, pr=pr, head=head, tasks_skip=TASKS_SKIP_TEST)`;
  5. если `isDraft` — `mark_ready` (`False` → стоп);
  6. ожидание проверок;
  7. `human_merge(state.repo, pr, head, target_dir=state.target_dir)` (rc ≠ 0 → стоп с
     кодом);
  8. `checkout_and_pull(state.target_dir, state.base_ref or "master")`;
  9. `op_complete`;
  10. печать `f"{kind} #{pr} вмержен — {TASKS_SKIP_TEST}"`.
- Стоп = печать `f"spec-loop (test): {причина}"` и `return 1`. Статус прогона остаётся
  `completed`: цепочка — после прогона, повтор `make spec-loop` продолжает её.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_governance_auto_tasks.py tests/test_governance_ops.py tests/test_author_sandbox_invariant.py`
Expected: PASS

- [ ] **Step 5: Commit** — `feat(auto_tasks): test-mode tasks approval chain (ready+checks+merge, spec approve)`

---

### Task 3: `spec-runner run` и подключение цепочки к `spec_loop`

**Files:**
- Modify: `governance/auto_tasks.py` (шаг `spec-run`)
- Modify: `governance/spec_loop.py` (`_deliver_phase` ~951)
- Test: `tests/test_governance_auto_tasks.py`, `tests/test_governance_spec_loop.py`

**Interfaces:**
- Consumes: `Ops.spec_runner` (Task 2); `Ops.prs_by_head_prefix(slug, prefix) -> list[dict]`
  (`number`, `headRefName`, `state`).
- Produces: `auto_tasks._spec_run(state, ops) -> int | None` — номер integration-PR либо
  `None` (стоп напечатан). `run_chain` после `approve-merge` зовёт его.

- [ ] **Step 1: Write the failing tests**

```python
def test_spec_run_argv_and_new_pr_found():
    # prs_by_head_prefix до запуска: [{"number": 5, "state": "OPEN", ...}],
    # после — [5 OPEN, 9 OPEN] → spec_runner вызван с
    # ["run", "--all", "--strict", f"--spec-prefix={ws}-", "--integration-pr"];
    # state.ops["spec-run"] == {"status": "completed", "before": [5], "pr": 9, "exit": 0}
def test_spec_run_two_new_prs_stop(capsys):   # после — 5, 9, 10 → None, "9" и "10" в выводе
def test_spec_run_no_new_pr_stop(capsys):     # exit 0, новых нет → None, причина про PR
def test_spec_run_nonzero_exit_stop(capsys):  # exit 2 → None, "2" в выводе, PR не ищется
def test_spec_run_resume_with_pr_does_not_rerun():
    # spec-run started с before=[5], на фордже уже есть 9 OPEN → spec_runner НЕ вызван,
    # операция completed с pr 9
def test_spec_run_completed_is_noop():  # completed → ни одного вызова ops
```

`tests/test_governance_spec_loop.py`:

```python
def test_deliver_phase_test_mode_runs_chain(monkeypatch):
    # state.mode == "test", deliver_for_run → 11 → auto_tasks.run_chain(state, ops, 11)
    # вызван; _APPROVE_HINT не печатается
def test_deliver_phase_strict_unchanged(monkeypatch, capsys):
    # strict → run_chain не вызван, подсказка approve напечатана, rc 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_governance_auto_tasks.py -k spec_run tests/test_governance_spec_loop.py -k deliver_phase`
Expected: FAIL

- [ ] **Step 3: Implement**
- `_spec_run` по записи:
  1. `completed` → `pr`;
  2. `new`:
     - `before` = номера `OPEN` с префиксом `spec-runner/run-`;
     - `op_start(state, "spec-run", before=before)`;
     - запуск `spec-runner run`;
  3. `started`: если новый открытый PR уже есть — без запуска, иначе запуск;
  4. после запуска: rc ≠ 0 → стоп с кодом;
  5. новые = открытые − `before`: ровно один → `op_complete(..., pr=n, exit=rc)`, иначе
     стоп с перечнем.
- `_deliver_phase`: после `deliver_for_run` при `state.mode == "test"` —
  `return auto_tasks.run_chain(state, ops, pr)` (строка «tasks-спека доставлена» остаётся),
  ветка `strict` без изменений.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_governance_auto_tasks.py tests/test_governance_spec_loop.py tests/test_author_sandbox_invariant.py`
Expected: PASS

- [ ] **Step 5: Commit** — `feat(auto_tasks): spec-runner run with integration PR; wire chain into spec-loop (test)`

---

### Task 4: приёмка без мержа (`accept_pr` test) и последний шаг цепочки

**Files:**
- Modify: `governance/accept_pr.py` (`accept`, `_accept_on_head`, `main`)
- Modify: `governance/auto_tasks.py` (шаг `code-accept`)
- Test: `tests/test_governance_accept_pr.py`, `tests/test_governance_auto_tasks.py`

**Interfaces:**
- Consumes:
  - `review_verdict.parse_marker(body) -> ReviewVerdict | None`;
  - `review_verdict.record_review(state, key, v)`;
  - `review_verdict.positive_on(state, key, head) -> bool`;
  - `Ops.latest_review_body(slug, pr)`;
  - `Ops.review(repo, pr, *, target_dir)`;
  - `user_targets.lookup(repo) -> UserTarget | None` (`.path`, `.repo_url`).
- Produces:
  - `accept_pr.ACCEPT_NO_MERGE_TEST: str`.
  - `@dataclass(frozen=True) class NoMergeAccept` с полями:
    - `known_positive: Callable[[str], bool]` — записан ли approve на этой голове;
    - `record_verdict: Callable[[ReviewVerdict], None]`;
    - `record_result: Callable[[dict], None]`.
  - `accept(..., test: NoMergeAccept | None = None) -> int` — в test 0 означает «PR проверен,
    не вмержен».
  - `auto_tasks._accept_code(state, ops, pr) -> int`.

- [ ] **Step 1: Write the failing tests** (`tests/test_governance_accept_pr.py`, двойник
  `_Ops` дополняется `latest_review_body`; ревью в test — `review(repo, pr,
  target_dir=…)`):

```python
def test_test_accept_stops_before_merge(capsys):
    # review 0, тело с маркером verdict=approve на head0, rollup SUCCESS, MERGEABLE
    # → accept(..., test=t) == 0; ops.merge НЕ вызван; t.record_result получил
    # {"pr": 7, "url": "https://github.com/o/r/pull/7", "head": head0,
    #  "checks": "green", "merged": False};
    # ACCEPT_NO_MERGE_TEST и URL в выводе
def test_test_accept_request_changes_marker_stops(capsys):
    # review 0, маркер verdict=request-changes → 1, record_result не вызван
def test_test_accept_marker_on_other_head_stops():   # head в маркере ≠ head0 → 1
def test_test_accept_no_marker_stops():               # тело без маркера → 1
def test_test_accept_known_verdict_skips_review():
    # t.known_positive(head0) → True → ops.review не вызван, мерж не вызван, 0
def test_test_accept_red_checks_stop():               # FAILURE → 1, record_result не вызван
def test_strict_accept_unchanged_merges():            # test=None → ops.merge вызван как раньше
def test_main_test_mode_target_from_user_targets(monkeypatch, tmp_path):
    # конфиг mode test, user_targets.lookup("r") → UserTarget(path=tmp_path/"r", ...)
    # → accept вызван с target_dir == str(tmp_path/"r") и test is not None
```

`tests/test_governance_auto_tasks.py`:

```python
def test_code_accept_records_verdict_and_result():
    # state.ops["code-accept"]["verdict"]["head"] == head0;
    # state.ops["code-accept"]["result"]["url"] == URL; status completed; run_chain == 0
def test_code_accept_same_head_no_paid_call():
    # completed на head0, текущая голова head0 → review не вызван; печать URL; 0
def test_code_accept_new_head_reviews_again():
    # completed на h0, текущая голова h1 → операция переоткрыта, review вызван на h1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_governance_accept_pr.py -k "test_accept or main_test_mode" tests/test_governance_auto_tasks.py -k code_accept`
Expected: FAIL

- [ ] **Step 3: Implement**
- В `_accept_on_head` при `test is not None`:
  1. если `known_positive(head0)` — ревью пропускается;
  2. иначе `ops.review(repo, pr, target_dir=target_dir)` (rc ≠ 0 — как сейчас);
  3. `parse_marker(ops.latest_review_body(slug, pr))`: не `approve` или не на `head0` —
     стоп с названной причиной; иначе `record_verdict`;
  4. ожидание проверок и mergeability, пины head и base — как в `strict`;
  5. вместо `ops.merge` — `record_result({...})`, печать
     `f"accept-pr (test): {url} — голова {head0[:7]} проверена, проверки зелёные; "
     f"{ACCEPT_NO_MERGE_TEST}"`, `return 0`.
- URL — `f"https://github.com/{repo_slug}/pull/{pr}"`.
- Гарды харнесса и authority-root остаются в обоих режимах.
- `main`: при режиме конфига `test` (тот же способ, что `spec_loop._config_mode`) цель —
  `user_targets.lookup(args.repo)`:
  - нет записи — rc 2 с командой регистрации (`python -m governance.user_targets add
    <каталог>`);
  - `test=NoMergeAccept(lambda _h: False, lambda _v: None, lambda _r: None)` — без журнала,
    решение исполнителя №4.
- `_accept_code` (ключ `code-accept`):
  1. текущая голова PR (`pr_facts`);
  2. completed с `result.head == голова` → печать того же итога без вызовов, `return 0`;
  3. completed на другой голове → `op_start` заново (прежний `result` → `prev`);
  4. `accept(state.repo, state.repo_slug, pr, ops, state.target_dir, test=NoMergeAccept(
     known_positive=lambda h: positive_on(state, "code-accept", h),
     record_verdict=lambda v: record_review(state, "code-accept", v),
     record_result=lambda r: op_complete(state, "code-accept", result=r, …)))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_governance_accept_pr.py tests/test_governance_auto_tasks.py tests/test_governance_review_verdict.py tests/test_author_sandbox_invariant.py`
Expected: PASS

- [ ] **Step 5: Commit** — `feat(accept_pr): test-mode acceptance without merge; chain records verified code PR`

---

## После задач

- Полный набор со steward. Ожидаемые worktree-only падения: `test_runner_z14_*`,
  `test_create_call_dir_for_devtools_itself_as_target`.
- PR в devtools, ревью: `review-pr.sh --dry-run --write-verdict`, затем `--use-verdict`.
  Мерж — `merge-pr.sh --squash --delete-branch --expect-head <sha> --expect-base <base SHA>`
  агентом. Если дифф неожиданно задел authority-root — остановиться и разобрать, флаг
  обхода не применять по умолчанию.
- Харнесс-долги и решения владельца — в отчёт пользователю; план «3c-H» пишется по его
  «да».
