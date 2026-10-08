# Engineer-маршрут стадии Need — план реализации, часть B (интеграция)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** поверх части A (`2026-10-08-need-stage-engineer-route-plan-a.md`) — customer
`--brief-only`, engineer-preflight, engineer-стадия раннера, E1-сверка, обёртки
`brief-propose`/`brief-approve`, проверка brief-PR в `human-merge.sh`, smoke и TODO.

**Architecture:** spec-loop получает флаги и preflight (до run-id); раннер — engineer-
ветку стадии Need (write-ahead id, перепроверка перед каждым вызовом соседа и перед
публикацией, восстановление только через `status`); E1 сверяет source-слой с
`upstream_blob`; обёртки `governance/brief_tools.py` не держат леджера —
восстанавливаются чтением форджа.

**Tech Stack:** как в части A.

**Spec:** `docs/superpowers/specs/2026-09-15-need-stage-design.md`, ревизия 7, **§11**.

**Проверено исполнением.** Задачи 7–14 исполнены в одноразовом worktree поверх
проверенной части A (вершина `724ea12`); код и тесты ниже вставлены генератором из
проверенных коммитов (`c4e97a3` … `8654c34`), уже включающих исправления ревью части B
(круги 1–3: B1–B5 и доводка B5). Каждая задача прогнана своими тестами
и мутационной проверкой ключевых условий; полный набор — см. журнал исполнения
(`_cowork_output/2026-10-08-e2-engineer-route/plan-exec/LOG.md`, dev-only), там же
патчи. Полный набор гонять с группой `governance` (`GOVERNANCE_REQUIRED=1`).

## Global Constraints

Действуют все ограничения части A, плюс:

- Engineer-доказательства — только факты форджа; из `run.json` в решении участвуют
  лишь `approval_pr` (адрес акта) и `upstream_blob` (эталон байтов копии) (§11.1.2).
- Перепроверка engineer (`_engineer_guard`) — перед КАЖДЫМ вызовом соседа и перед
  необратимой публикацией брифа; после `completed_at` политика не перепроверяется
  (§11.10 Q2), байты source-слоя — да (E1).
- Правило «сирота» (`session_id is None`) — только customer: engineer, остановленный
  перепроверкой до первого `start`, продолжаем.
- Коды CLI `brief_tools`: 0 — результат есть; 1 — отказ; 2 — факт не установлен
  (повторите); 3 — отказ проверки заявки (`check-merge`).
- `create_pr` принимает необязательный keyword `base` (обратно совместимо).

## Review Focus

- **Политика сменилась ВО ВРЕМЯ рендера брифа** (между `brief` и `os.replace`):
  публикации нет — Task 9, `test_policy_change_during_render_blocks_publication`.
- **Согласованная локальная подмена** (`upstream.md`, `upstream_blob` и отчёт
  `approval` переписаны вместе): стоп по сверке копии с актом из форджа — Task 9,
  `test_consistent_local_tamper_cannot_pass`.
- **Во время `approve` появились новая версия политики и подтверждение к ней**: и
  «до», и «после» проходят, версии разные — отказ — Task 12,
  `test_policy_switch_to_reconfirmed_version_during_approve`.
- **Найденный brief-PR той же формы, но про другой прогон** (`run_id`, `ws_id`,
  пин — не версия): отказ, не «результат уже есть» — Task 11,
  `test_found_pr_with_foreign_content_refuses`.
- **Две одновременные `start` с одним id у соседа** без нашей блокировки: живой замер
  (`discovery@94c88cc`) — одинаковые upstream: `[20, 20]`, `[1, 20]`, `[20, 20]`; разные
  upstream (каждый подписан своим `stamp`): `[20, 20]`, `[1, 20]`, `[1, 20]` — обе сессии
  «созданы» в части прогонов; зафиксировано xfail-smoke T52, ждёт discovery#63 п.3 —
  Task 14. Наша блокировка прогона (часть A) этот случай исключает внутри devtools.
- **Ветку brief-PR сдвинули между проверкой и созданием PR** (тот же набор путей, другой
  коммит): не «успех» — Task 11, `test_branch_moved_between_check_and_pr_is_not_success`;
  мерж-скрипт мержит только проверенный SHA — Task 13.

---

## File Structure (часть B)

| Файл | Ответственность |
|---|---|
| `governance/interview.py` | `brief_only`, `UPSTREAM_NAME`/`UPSTREAM_REL`, `engineer_session_id`, `EngineerIntake` |
| `governance/brief_input.py` | `check_customer_upstream(path, data)` — одна реализация для E1 и preflight |
| `governance/spec_loop.py` | `--brief-only`, `--approval-pr`, `engineer_preflight`, `brief_ready` в выдаче |
| `governance/runner.py` | `brief_ready`, durable-копия, engineer-стадия, `_LOCK_FD`, E1-сверка |
| `governance/ops.py` | `create_pr(..., base=None)` |
| `governance/brief_tools.py` | `propose`, `approve`, `check_merge`, CLI |
| `human-merge.sh` | ветка `brief/*` → `brief_tools check-merge` |
| `Makefile` | `brief-propose`, `brief-approve` |
| `TODO.md`, `CLAUDE.md` | пункты Q3–Q5, discovery#63; строка `human-merge.sh` |
| `tests/test_brief_tools.py`, `tests/test_engineer_route_smoke.py` | новые |
| `tests/fixtures/discovery_approval/engineer-brief.md` | engineer-бриф, проходящий E1 рядом с подписанным upstream |

---

### Task 7: Customer `--brief-only` → терминальный `brief_ready`

**Files:**
- Modify: `governance/interview.py`, `governance/spec_loop.py`, `governance/runner.py`
- Test: `tests/test_governance_runner.py`, `tests/test_governance_spec_loop.py`

**Interfaces:**
- Produces: `InterviewSpec.brief_only: bool = False`; статус `"brief_ready"`;
  `interview.brief_blob`; `runner._interview_complete(state, spec, final, source, **result)`;
  `spec_loop._brief_ready(state) -> int` (печать `make brief-propose RUN=…`, код 0).

- [ ] **Step 1: Тесты** (T5–T11; первый переход в `brief_ready` после resume; повтор без
эффектов по всему дереву `run_dir`; сбой — в `run_state.save` при записи статуса):

```diff
diff --git a/tests/test_governance_runner.py b/tests/test_governance_runner.py
index 12a8caa..a7c0c76 100644
--- a/tests/test_governance_runner.py
+++ b/tests/test_governance_runner.py
@@ -9401,3 +9401,95 @@ def test_verify_holds_child_lock_through_s8(  # ревью части A, A2
     child = locked_runner.verify(parent_id, ops, "r-s8-lockc")
     assert seen == ["tried", "busy"]  # конкурент не вошёл в S8 потомка
     assert child.status == "completed"
+
+
+# --- §11.2: customer --brief-only → терминальный brief_ready (Task 7, часть B) ---
+
+
+def _brief_only_ops(extra: list | None = None) -> FakeOps:
+    return FakeOps(
+        discovery=[
+            ("start", _reply(20)),
+            ("status", _reply(0)),
+            ("brief", _reply(0)),
+            *(extra or []),
+        ],
+        brief_text=_need_brief_text(),
+    )
+
+
+def _run_tree(run_id: str) -> dict[str, bytes]:
+    root = rs.run_dir(run_id)
+    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
+
+
+_GIT_CALLS = ("is_dirty", "ensure_branch", "switch_to", "checkout_and_pull")
+
+
+def test_brief_only_publication_ends_in_brief_ready(tmp_path, runs_root) -> None:  # T7
+    ops = _brief_only_ops()
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-bo", ops),
+        interview_spec=_need_spec(brief_only=True),
+    )
+    state = locked_runner.resume("r-bo", ops)
+    assert state.status == "brief_ready"
+    assert state.interview["completed_at"] and state.interview["brief_blob"]
+    assert state.brief is not None and state.branch == ""
+    assert not any(c[0] in _GIT_CALLS for c in ops.calls)
+    assert rs.load("r-bo").status == "brief_ready"
+
+
+def test_brief_ready_resume_has_no_effects(tmp_path, runs_root) -> None:  # T8
+    ops = _brief_only_ops()
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-bo2", ops),
+        interview_spec=_need_spec(brief_only=True),
+    )
+    locked_runner.resume("r-bo2", ops)
+    tree = _run_tree("r-bo2")
+    before = (len(ops.calls), len(ops.discovery_calls))
+    again = locked_runner.resume("r-bo2", ops)
+    assert again.status == "brief_ready"
+    assert _run_tree("r-bo2") == tree
+    assert (len(ops.calls), len(ops.discovery_calls)) == before
+
+
+def test_crash_before_brief_ready_write_recovers_to_brief_ready(  # T9
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops = _brief_only_ops(extra=[("brief", _reply(0))])
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-bo3", ops),
+        interview_spec=_need_spec(brief_only=True),
+    )
+    real_save = rs.save
+
+    def die_on_brief_ready(state):
+        if state.status == "brief_ready":
+            raise SystemExit("crash")
+        real_save(state)
+
+    monkeypatch.setattr(rs, "save", die_on_brief_ready)
+    with pytest.raises(SystemExit):
+        locked_runner.resume("r-bo3", ops)
+    monkeypatch.setattr(rs, "save", real_save)
+    persisted = rs.load("r-bo3")
+    assert persisted.ops["interview-brief"]["status"] == "started"
+    assert persisted.interview["completed_at"] is None
+    assert (rs.run_dir("r-bo3") / "brief-input/00-discovery/brief.md").exists()
+    state = locked_runner.resume("r-bo3", ops)
+    assert state.status == "brief_ready"
+    assert state.ops["interview-brief"].get("reconciled") is True
+
+
+def test_customer_without_brief_only_still_goes_to_s1(
+    tmp_path, runs_root
+) -> None:  # T10
+    ops = _brief_only_ops()
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-nobo", ops), interview_spec=_need_spec()
+    )
+    state = locked_runner.resume("r-nobo", ops)
+    assert state.status != "brief_ready"
+    assert any(c[0] in _GIT_CALLS for c in ops.calls)
```

```diff
diff --git a/tests/test_governance_spec_loop.py b/tests/test_governance_spec_loop.py
index 3bac3cc..1b9ab4f 100644
--- a/tests/test_governance_spec_loop.py
+++ b/tests/test_governance_spec_loop.py
@@ -1629,3 +1629,95 @@ def test_wave_recovery_locks_before_reading_or_writing_ledger(
         assert spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"]) == 1
     assert rs.all_run_ids() == []  # леджер не записан мимо блокировки
     assert "другим процессом" in capsys.readouterr().out
+
+
+# --- §11.2: --brief-only (Task 7, часть B) ---
+
+
+@pytest.mark.parametrize(
+    ("argv", "needle"),
+    [
+        (["--subject", "s", "--repo", "alpha", "--brief-only"], "--need"),
+        (_need("--brief-only", "--session", "s-1"), "--brief-only"),
+        (_need("--brief-only", "--brief", "x.md"), "--brief"),
+        (
+            [
+                "--subject",
+                "s",
+                "--repo",
+                "alpha",
+                "--need",
+                "--frame",
+                "engineer",
+                "--stakeholder",
+                "r",
+                "--brief-only",
+            ],
+            "--brief-only",
+        ),
+    ],
+    ids=["without-need", "with-session", "with-brief", "engineer"],
+)
+def test_brief_only_invalid_combinations_refuse_before_run(  # T5
+    runs_root, tmp_path, monkeypatch, capsys, argv, needle
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    assert spec_loop.main(argv) == 1
+    assert env.calls == [] and rs.all_run_ids() == []
+    assert needle in capsys.readouterr().out
+
+
+def test_brief_only_customer_starts_with_flag(  # T5 двойник
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    assert spec_loop.main(_need("--brief-only")) == 0
+    assert env.calls[0][1]["interview_spec"].brief_only is True
+
+
+def test_repeat_with_other_brief_only_refuses(  # T6
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _make_need_run(env)  # прогон стартовал без --brief-only
+    before = (rs.run_dir("r-a") / "run.json").read_bytes()
+    assert spec_loop.main(_need("--brief-only")) == 1
+    assert "brief_only" in capsys.readouterr().out
+    assert env.calls == []
+    assert (rs.run_dir("r-a") / "run.json").read_bytes() == before
+
+
+def test_new_run_allowed_next_to_brief_ready(  # T11
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _make_need_run(env, status="brief_ready")
+    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-eng")) == 0
+    assert env.calls[0][0] == "start" and env.calls[0][1]["ws_id"] == "ws-eng"
+
+
+def test_brief_ready_on_entry_prints_next_step_without_resume(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    st = _make_need_run(env, status="brief_ready")
+    st.interview["brief_only"] = True
+    rs.save(st)
+    assert spec_loop.main(_need("--brief-only")) == 0
+    assert env.calls == []
+    assert "make brief-propose RUN=r-a" in capsys.readouterr().out
+
+
+def test_first_transition_to_brief_ready_prints_next_step(  # P14
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    st = _make_need_run(env)
+    st.interview["brief_only"] = True
+    rs.save(st)
+    done = rs.load("r-a")
+    done.status = "brief_ready"
+    env.resume_result = done
+    assert spec_loop.main(_need("--brief-only")) == 0
+    assert [c[0] for c in env.calls] == ["resume"]
+    assert "make brief-propose RUN=r-a" in capsys.readouterr().out
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/interview.py b/governance/interview.py
index e8bf452..66ee931 100644
--- a/governance/interview.py
+++ b/governance/interview.py
@@ -99,6 +99,8 @@ class InterviewSpec:
     target: str
     traces_to: str | None
     upstream_blob: str | None
+    #: customer `--brief-only` (§11.2): после брифа — терминальный `brief_ready`.
+    brief_only: bool = False
 
     def as_state(self) -> dict:
         return {
@@ -108,6 +110,7 @@ class InterviewSpec:
             "target": self.target,
             "traces_to": self.traces_to,
             "upstream_blob": self.upstream_blob,
+            "brief_only": self.brief_only,
             "brief_rel": BRIEF_REL,
             "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
             "completed_at": None,
@@ -121,6 +124,7 @@ class InterviewSpec:
             target=st["target"],
             traces_to=st.get("traces_to"),
             upstream_blob=st.get("upstream_blob"),
+            brief_only=bool(st.get("brief_only", False)),
         )
```

```diff
diff --git a/governance/spec_loop.py b/governance/spec_loop.py
index 05ade23..3e59c82 100644
--- a/governance/spec_loop.py
+++ b/governance/spec_loop.py
@@ -267,7 +267,7 @@ def _origin_url(target_dir: str | Path) -> str:
     return out.stdout.strip()
 
 
-NEED_ONLY = ("frame", "stakeholder", "traces_to", "session", "new_run")
+NEED_ONLY = ("frame", "stakeholder", "traces_to", "session", "new_run", "brief_only")
 STAKEHOLDER_RULE = (
     "стадия Need запускается только при наличии реального стейкхолдера — "
     "укажите --stakeholder <role> (декларация, не проверка); без "
@@ -300,10 +300,22 @@ def build_interview_spec(args, repo_slug: str) -> iv.InterviewSpec | None:
         raise SpecLoopError("--new-run взаимоисключающ с --run-id и --session")
     if args.new_run and not args.ws_id:
         raise SpecLoopError("--new-run требует --ws-id <fresh-id>")
+    if args.brief_only and (args.frame != "customer" or args.session):
+        raise SpecLoopError(
+            "--brief-only допустим только с --need --frame customer и без "
+            "--session (§11.2)"
+        )
     if args.frame == "customer":
         if args.traces_to:
             raise SpecLoopError("customer-фрейм не принимает --traces-to")
-        return iv.InterviewSpec("customer", args.stakeholder, repo_slug, None, None)
+        return iv.InterviewSpec(
+            "customer",
+            args.stakeholder,
+            repo_slug,
+            None,
+            None,
+            brief_only=bool(args.brief_only),
+        )
     if not args.traces_to:
         raise SpecLoopError(
             "engineer-фрейм требует --traces-to <approved customer-brief>"
@@ -809,8 +821,19 @@ def _report_state(state: rs.RunState) -> int:
     return 1
 
 
+def _brief_ready(state: rs.RunState) -> int:
+    """`brief_ready` (§11.2): терминальный; печать следующего шага, код 0."""
+    print(
+        "spec-loop: бриф готов (brief_ready) — следующий шаг: "
+        f"make brief-propose RUN={state.run_id}"
+    )
+    return 0
+
+
 def _dispatch(state: rs.RunState, ops, lock: run_lock.RunLock) -> int:
     """Действие по фактическому статусу найденного прогона."""
+    if state.status == "brief_ready":
+        return _brief_ready(state)
     if state.status == "waiting_human_merge":
         after = runner.resume(state.run_id, ops, lock=lock)
         if after.status == "waiting_human_merge":
@@ -828,6 +851,8 @@ def _dispatch(state: rs.RunState, ops, lock: run_lock.RunLock) -> int:
             # дошёл ли вызов до runner.
             return _report_interview_stop(state)
         after = runner.resume(state.run_id, ops, lock=lock)
+        if after.status == "brief_ready":
+            return _brief_ready(after)
         if after.status == "waiting_interview":
             return 0
         if after.status == "stopped_interview":
@@ -865,6 +890,11 @@ def main(argv: list[str] | None = None) -> int:
     )
     parser.add_argument("--traces-to", help="approved customer-brief для engineer")
     parser.add_argument("--session", help="recovery: присоединить сессию discovery")
+    parser.add_argument(
+        "--brief-only",
+        action="store_true",
+        help="customer: после брифа — терминальный brief_ready, без S1 (§11.2)",
+    )
     parser.add_argument(
         "--new-run",
         action="store_true",
@@ -978,7 +1008,8 @@ def main(argv: list[str] | None = None) -> int:
             past_s1 = [
                 st
                 for st in matches
-                if st.status not in ("waiting_interview", "stopped_interview")
+                if st.status
+                not in ("waiting_interview", "stopped_interview", "brief_ready")
             ]
             if past_s1:
                 raise SpecLoopError(
@@ -1081,16 +1112,23 @@ def main(argv: list[str] | None = None) -> int:
                     "--new-run --ws-id <fresh-id>"
                 )
             recorded = iv.InterviewSpec.from_state(state.interview)
-            if (recorded.frame, recorded.stakeholder_role, recorded.traces_to) != (
+            if (
+                recorded.frame,
+                recorded.stakeholder_role,
+                recorded.traces_to,
+                recorded.brief_only,
+            ) != (
                 interview_spec.frame,
                 interview_spec.stakeholder_role,
                 interview_spec.traces_to,
+                interview_spec.brief_only,
             ):
                 raise SpecLoopError(
                     "координаты интервью зафиксированы стартом "
                     f"(frame={recorded.frame}, "
                     f"stakeholder={recorded.stakeholder_role!r}, "
-                    f"traces_to={recorded.traces_to!r}) — сменить их: "
+                    f"traces_to={recorded.traces_to!r}, "
+                    f"brief_only={recorded.brief_only}) — сменить их: "
                     "--new-run --ws-id"
                 )
 
@@ -1210,6 +1248,8 @@ def main(argv: list[str] | None = None) -> int:
         if started.status == "waiting_human_merge":
             print(_pause_message(started))
             return 0
+        if started.status == "brief_ready":
+            return _brief_ready(started)
         if started.status == "completed":
             return _deliver_phase(started, ops)
         if started.status == "stopped_interview":
```

```diff
diff --git a/governance/runner.py b/governance/runner.py
index 93e423c..0b4c0bd 100644
--- a/governance/runner.py
+++ b/governance/runner.py
@@ -83,7 +83,7 @@ from governance.run_state import (
     validate_merge_authority,
 )
 from governance.spec_runner_contract import target_selector_policy
-from governance.stale_adapter import blob_sha1
+from governance.stale_adapter import blob_sha1, blob_sha1_bytes
 
 _ROLLUP_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
 
@@ -891,6 +891,9 @@ def resume(run_id: str, ops: Ops, *, lock: rl.RunLock) -> RunState:
     """
     rl.require(lock, run_id)
     state = load(run_id)
+    if state.status == "brief_ready":
+        # Терминальный (§11.2): ни шагов, ни соседа, ни записей.
+        return state
     if state.status == "merged_unverified":
         raise ValueError(
             f"run {run_id!r} — merged_unverified навсегда; создайте "
@@ -1784,9 +1787,7 @@ def _interview_publish(
     _interview_of(state)["completed_at"] = datetime.now(UTC).isoformat(
         timespec="seconds"
     )
-    state.status = "running"
-    op_complete(state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs))
-    return True
+    return _interview_complete(state, spec, final, source)
 
 
 def _interview_reconcile_published(
@@ -1839,11 +1840,29 @@ def _interview_reconcile_published(
     _interview_of(state)["completed_at"] = datetime.now(UTC).isoformat(
         timespec="seconds"
     )
-    state.status = "running"
-    op_complete(
-        state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs), reconciled=True
-    )
-    return True
+    return _interview_complete(state, spec, final, source, reconciled=True)
+
+
+def _interview_complete(
+    state: RunState,
+    spec: iv.InterviewSpec,
+    final: Path,
+    source: brief_input.BriefSource,
+    **result: object,
+) -> bool:
+    """Публикация брифа завершена: S1 либо терминальный `brief_ready` (§11.2).
+
+    Статус и `op_complete` пишутся ОДНОЙ записью `run.json`: гибель до неё
+    оставляет `interview-brief` в `started` при durable `brief.md` — повтор
+    идёт реконсиляцией §5.5 и снова приходит сюда.
+    """
+    if spec.brief_only:
+        _interview_of(state)["brief_blob"] = blob_sha1_bytes(final.read_bytes())
+        state.status = "brief_ready"
+    else:
+        state.status = "running"
+    op_complete(state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs), **result)
+    return not spec.brief_only
 
 
 def attach_session(
@@ -3854,6 +3873,8 @@ def _print_status(state: RunState) -> None:
     print(f"pr:            {state.pr if state.pr is not None else '-'}")
     if state.remediated_by:
         print(f"remediated_by: {state.remediated_by}")
+    if state.status == "brief_ready":
+        print(f"next:          make brief-propose RUN={state.run_id}")
     print("ops:")
     for key in sorted(state.ops):
         op = state.ops[key]
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_governance_runner.py tests/test_governance_spec_loop.py -q -k "brief_only or brief_ready or need_preflight"`; мутации: переход при публикации, preflight-сочетания, `--new-run` рядом с `brief_ready` — пойманы; явная терминальность в `resume` — эквивалентный мутант (без неё `advance` всё равно не исполняет шагов вне `running`), оставлена защитой (проверено).

- [ ] **Step 5: Commit** — `feat(need): customer --brief-only → терминальный brief_ready (§11.2)`.

---

### Task 8: Engineer-preflight в spec-loop

**Files:**
- Modify: `governance/interview.py`, `governance/brief_input.py`, `governance/spec_loop.py`,
  `governance/runner.py` (`start(..., engineer_intake=…)`, `_write_engineer_upstream`)
- Test: `tests/test_governance_spec_loop.py`, `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: часть A — `brief_provenance.read_act/check_operator_brief/check_policy`,
  `Act.as_record()`, `tests/forge_fake.consistent_world`.
- Produces: `iv.UPSTREAM_NAME = "upstream.md"`, `iv.UPSTREAM_REL`,
  `iv.engineer_session_id(run_id)`, `iv.EngineerIntake(buffer, blob, approval,
  approval_pr, source_path)`; `brief_input.check_customer_upstream(path, data)`;
  `spec_loop.engineer_preflight(path, approval_pr, repo_slug, ops) -> EngineerIntake`;
  `runner.start(..., engineer_intake: iv.EngineerIntake | None = None, …)`.

- [ ] **Step 1: Тесты** (T12, T13; T14–T22, T25–T27, T31 — через ПОЛНЫЙ вход `spec_loop.main`
по всем наборам `tests/provenance_cases.py` части A (Task 4): новая форма дефекта там
автоматически проверяется и здесь; каждый случай — отказ кодом 1 до run-id, без вызовов,
с причиной в выводе; двойник — согласованный мир, код 0 (T17 — точное зеркало мержа);
T21a/T21b — общий набор `DEFECTS`; T23 — факт PR и запрос форджа не несут тела/меток;
T24 — цель — настоящий git-чекаут с верным origin и ПОДДЕЛЬНОЙ локальной историей
(«смерженные» бриф и заявка, ветка `brief/WS-1`); настоящий `_origin_url` читает origin, любой
другой вызов git падает; решение — по форджу (подлинный акт принят, испорченный — нет); T32,
T33; повтор с иным `--approval-pr`; `--session` с engineer):

```diff
diff --git a/tests/test_governance_spec_loop.py b/tests/test_governance_spec_loop.py
index 1b9ab4f..34628bf 100644
--- a/tests/test_governance_spec_loop.py
+++ b/tests/test_governance_spec_loop.py
@@ -938,8 +938,9 @@ def test_need_customer_starts_with_interview_spec(
                 "--traces-to",
                 "c.md",
             ],
-            "discovery#49",
+            "--approval-pr",
         ),
+        (_need("--approval-pr", "7"), "--approval-pr"),
     ],
 )
 def test_need_preflight_refuses_before_run_id(
@@ -1721,3 +1722,398 @@ def test_first_transition_to_brief_ready_prints_next_step(  # P14
     assert spec_loop.main(_need("--brief-only")) == 0
     assert [c[0] for c in env.calls] == ["resume"]
     assert "make brief-propose RUN=r-a" in capsys.readouterr().out
+
+
+# --- §11.4.1–§11.4.2: engineer-preflight (Task 8, часть B) ---
+
+from tests.forge_fake import (  # noqa: E402
+    DRAFT,
+    POLICY_PATH,
+    SIGNED,
+    P,
+    consistent_world,
+)
+
+ENGINEER_REPO = "owner/alpha"
+
+
+def _engineer_ops(monkeypatch):
+    """Мир форджа + пустой поиск candidate-PR (путь восстановления main)."""
+    forge = consistent_world(monkeypatch)
+    forge.prs_by_head_prefix = lambda repo_slug, prefix: []  # type: ignore[attr-defined]
+    monkeypatch.setattr(spec_loop, "_real_ops", lambda: forge)
+    return forge
+
+
+def _operator(
+    tmp_path: Path, text: str = SIGNED, name: str = "customer-brief.md"
+) -> str:
+    path = tmp_path / name
+    path.write_bytes(text.encode("utf-8"))
+    return str(path)
+
+
+def test_engineer_preflight_accepts_consistent_world(tmp_path, monkeypatch) -> None:
+    forge = consistent_world(monkeypatch)
+    intake = spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
+    assert intake.buffer == SIGNED.encode() and intake.approval_pr == 7
+    assert intake.approval["act_policy_sha"] == P
+
+
+def test_operator_symlink_read_once(tmp_path, monkeypatch) -> None:  # T33
+    forge = consistent_world(monkeypatch)
+    real = tmp_path / "real.md"
+    real.write_text(SIGNED, encoding="utf-8")
+    link = tmp_path / "link.md"
+    link.symlink_to(real)
+    reads: list[str] = []
+    original = Path.read_bytes
+
+    def spy(self: Path) -> bytes:
+        reads.append(str(self))
+        return original(self)
+
+    monkeypatch.setattr(Path, "read_bytes", spy)
+    intake = spec_loop.engineer_preflight(str(link), 7, ENGINEER_REPO, forge)
+    real.write_text("подмена после чтения", encoding="utf-8")
+    assert reads == [str(link)] and intake.buffer == SIGNED.encode()
+
+
+@pytest.mark.parametrize(
+    ("case", "needle"),
+    [
+        ("draft", "approved"),
+        ("no-hash", "migration"),
+        ("edited", "self_hash"),
+        ("crlf", "CR"),
+        ("frame-engineer", "upstream не годится"),
+        ("merger-out", "merger_not_in_act_policy"),
+        ("bundle-pr", "pr_files"),
+        ("forge-down", "повторите"),
+    ],
+)
+def test_engineer_preflight_refusals(tmp_path, monkeypatch, case, needle) -> None:
+    forge = consistent_world(monkeypatch)
+    text = SIGNED
+    if case == "draft":
+        text = DRAFT
+    elif case == "no-hash":
+        text = SIGNED.replace("approved_content_hash: ", "x_hash: ")
+    elif case == "edited":
+        text = SIGNED.replace("## Goals", "## Goals\n\nправка после подписи\n", 1)
+    elif case == "crlf":
+        text = SIGNED.replace("\n", "\r\n")
+    elif case == "frame-engineer":
+        text = SIGNED.replace("frame: customer", "frame: engineer")
+    elif case == "merger-out":
+        forge.files[(P, POLICY_PATH)] = "AUTHORIZED_APPROVER_ACCOUNTS=other\n"
+    elif case == "bundle-pr":
+        forge.set_pr(files=forge.prs[7].files + (("x/30-decomposition.md", "added"),))
+    elif case == "forge-down":
+        forge.unavailable_facts.add("pr")
+    with pytest.raises(spec_loop.SpecLoopError) as exc:
+        spec_loop.engineer_preflight(_operator(tmp_path, text), 7, ENGINEER_REPO, forge)
+    assert needle in str(exc.value)
+    if case == "forge-down":
+        assert "не одобрено" not in str(exc.value)
+
+
+def test_engineer_preflight_reads_no_local_git(tmp_path, monkeypatch) -> None:  # T24
+    # FakeForge не реализует ни одного git-метода Ops: любое чтение локального
+    # git упало бы AttributeError — решение принимается только по форджу.
+    forge = consistent_world(monkeypatch)
+    spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
+
+
+def _engineer_args(*extra: str) -> list[str]:
+    return [
+        "--subject",
+        "Fleet Inbox",
+        "--repo",
+        "alpha",
+        "--need",
+        "--frame",
+        "engineer",
+        "--stakeholder",
+        "product owner",
+        *extra,
+    ]
+
+
+def test_engineer_new_run_passes_intake_and_upstream_blob(
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _engineer_ops(monkeypatch)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
+    )
+    assert rc == 0
+    kwargs = env.calls[0][1]
+    spec = kwargs["interview_spec"]
+    assert (spec.frame, spec.traces_to) == ("engineer", "upstream.md")
+    intake = kwargs["engineer_intake"]
+    assert spec.upstream_blob == intake.blob and intake.buffer == SIGNED.encode()
+
+
+def test_engineer_preflight_refusal_creates_no_run(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _engineer_ops(monkeypatch)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path, DRAFT), "--approval-pr", "7")
+    )
+    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []
+
+
+def test_engineer_next_to_customer_run_needs_new_run(  # T12
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    _make_need_run(env, status="brief_ready")
+    _engineer_ops(monkeypatch)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
+    )
+    assert rc == 1 and env.calls == []
+    assert "--new-run --ws-id" in capsys.readouterr().out
+    rc = spec_loop.main(
+        _engineer_args(
+            "--traces-to",
+            _operator(tmp_path),
+            "--approval-pr",
+            "7",
+            "--new-run",
+            "--ws-id",
+            "ws-eng",
+        )
+    )
+    assert rc == 0 and env.calls[0][1]["ws_id"] == "ws-eng"
+
+
+def test_engineer_session_flag_refused(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", "x.md", "--approval-pr", "7", "--session", "s")
+    )
+    assert rc == 1 and env.calls == []
+    assert "--session" in capsys.readouterr().out
+
+
+def test_engineer_repeat_with_other_approval_pr_refuses(
+    runs_root, tmp_path, monkeypatch, capsys
+) -> None:
+    env = _LoopEnv(monkeypatch, tmp_path)
+    st = rs.new_run(
+        subject="Fleet Inbox",
+        repo="alpha",
+        repo_slug="owner/alpha",
+        ws_id="ws-eng",
+        target_dir=str(env.target),
+        bundle_dir="workstreams/ws-eng/spec",
+        profile="profiles/team-exp.yaml",
+        run_id="r-eng",
+        merge_authority="human",
+        interview={
+            **iv.InterviewSpec(
+                "engineer", "product owner", "owner/alpha", "upstream.md", "b" * 40
+            ).as_state(),
+            "session_id": "s-r-eng-e",
+            "approval_pr": 7,
+        },
+    )
+    st.status = "waiting_interview"
+    rs.save(st)
+    before = (rs.run_dir("r-eng") / "run.json").read_bytes()
+    _engineer_ops(monkeypatch)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "8")
+    )
+    assert rc == 1 and env.calls == []
+    assert "--approval-pr" in capsys.readouterr().out
+    assert (rs.run_dir("r-eng") / "run.json").read_bytes() == before
+
+
+# --- ревью части B, круг 1 (B5): матрица preflight на уровне spec-loop ---
+
+from tests.approval_request_cases import DEFECTS  # noqa: E402
+from tests.forge_fake import C1, DIR, MERGE  # noqa: E402
+from tests.provenance_cases import (  # noqa: E402
+    ACT_DEFECTS,
+    ACT_UNAVAILABLE,
+    OPERATOR_DEFECTS,
+    POLICY_DEFECTS,
+    POLICY_UNAVAILABLE,
+    ids,
+)
+
+_REQ = f"{DIR}/approval-request.yaml"
+
+#: Весь набор дефектов предикатов (Task 4) — через полный вход spec-loop:
+#: новая форма в `tests/provenance_cases.py` проверяется здесь автоматически.
+_PREFLIGHT_DEFECTS = (
+    [(f"act-{i}", spoil, SIGNED, reason) for i, spoil, reason in ACT_DEFECTS]
+    + [(f"operator-{i}", spoil, text, r) for i, spoil, text, r in OPERATOR_DEFECTS]
+    + [(f"policy-{i}", spoil, SIGNED, reason) for i, spoil, reason in POLICY_DEFECTS]
+)
+#: Гейт upstream (§11.4.2 п.1) срабатывает раньше чтения акта.
+_GATE_FIRST = {"operator-draft", "operator-crlf"}
+
+
+def _main_engineer(tmp_path, monkeypatch, spoil=None, text: str = SIGNED):
+    """Полный вход `spec_loop.main` engineer-прогона над миром форджа."""
+    env = _LoopEnv(monkeypatch, tmp_path)
+    forge = _engineer_ops(monkeypatch)
+    if spoil is not None:
+        spoil(forge)
+    operator = _operator(tmp_path, text)
+    rc = spec_loop.main(_engineer_args("--traces-to", operator, "--approval-pr", "7"))
+    return env, rc
+
+
+@pytest.mark.parametrize(
+    ("case", "spoil", "text", "reason"),
+    _PREFLIGHT_DEFECTS,
+    ids=[d[0] for d in _PREFLIGHT_DEFECTS],
+)
+def test_engineer_main_refuses_every_provenance_defect(  # T14–T22, T25–T27
+    runs_root, tmp_path, monkeypatch, capsys, case, spoil, text, reason
+) -> None:
+    env, rc = _main_engineer(tmp_path, monkeypatch, spoil, text)
+    out = capsys.readouterr().out
+    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []
+    assert ("upstream не годится" if case in _GATE_FIRST else f"{reason}:") in out
+
+
+def test_engineer_main_accepts_consistent_world(  # двойник матрицы; T17
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    # Конверт фикстуры — точное зеркало мержа; кем он вписан, не проверяется.
+    env, rc = _main_engineer(tmp_path, monkeypatch)
+    assert rc == 0 and [c[0] for c in env.calls] == ["start"]
+
+
+@pytest.mark.parametrize("fact", ACT_UNAVAILABLE + POLICY_UNAVAILABLE)
+def test_engineer_main_unavailable_each_fact(  # T31
+    runs_root, tmp_path, monkeypatch, capsys, fact
+) -> None:
+    env, rc = _main_engineer(
+        tmp_path, monkeypatch, lambda f: f.unavailable_facts.add(fact)
+    )
+    out = capsys.readouterr().out
+    assert rc == 1 and env.calls == [] and rs.all_run_ids() == []
+    assert "повторите" in out and "не одобрено" not in out
+
+
+@pytest.mark.parametrize("spoiled", [False, True], ids=["authentic", "merger-out"])
+def test_engineer_main_decides_by_forge_not_local_git(  # T24
+    runs_root, tmp_path, monkeypatch, spoiled
+) -> None:
+    """Цель — настоящий git-чекаут с верным origin, но с ПОДДЕЛЬНОЙ локальной
+    историей: «смерженные» бриф и заявка, ветка brief/WS-1. Настоящий
+    `_origin_url` читает origin; любой другой вызов git падает. Решение —
+    ровно по форджу: подлинный акт принят, испорченный — нет."""
+    import subprocess
+
+    from governance import approval_request as _ar
+    from tests.forge_fake import request as _request
+
+    real_origin = spec_loop._origin_url
+    env = _LoopEnv(monkeypatch, tmp_path)
+    monkeypatch.setattr(spec_loop, "_origin_url", real_origin)
+    target = env.target
+    (target / ".git").rmdir()
+    forged = target / DIR
+    forged.mkdir(parents=True)
+    (forged / "brief.md").write_text(SIGNED, encoding="utf-8")
+    (forged / "approval-request.yaml").write_text(
+        _ar.render(_request()), encoding="utf-8"
+    )
+    for argv in (
+        ["init", "-q", "-b", "main"],
+        ["remote", "add", "origin", "git@github.com:owner/alpha.git"],
+        ["add", "-A"],
+        ["-c", "user.name=h", "-c", "user.email=h@x", "commit", "-qm", "Merge #7"],
+        ["branch", "brief/WS-1"],
+    ):
+        subprocess.run(["git", "-C", str(target), *argv], check=True)
+    real_run = subprocess.run
+    origin_reads: list[list[str]] = []
+
+    def only_origin(argv, *args, **kwargs):
+        if argv[0] == "git":
+            assert argv[-3:] == ["remote", "get-url", "origin"], argv
+            origin_reads.append(argv)
+        return real_run(argv, *args, **kwargs)
+
+    monkeypatch.setattr(subprocess, "run", only_origin)
+    forge = _engineer_ops(monkeypatch)
+    if spoiled:
+        ACT_DEFECTS[ids(ACT_DEFECTS).index("merger-out-of-p")][1](forge)
+    rc = spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
+    )
+    assert origin_reads  # настоящий путь чтения origin пройден
+    assert rc == (1 if spoiled else 0)
+
+
+def test_preflight_facts_carry_no_pr_body_or_labels() -> None:  # T23
+    """Метки/тело PR, изменённые после мержа, не меняют решения: факт PR их не
+    несёт и запрос форджа их не читает — доказательство только в коммите."""
+    import dataclasses
+
+    from governance import brief_facts
+
+    names = {f.name for f in dataclasses.fields(brief_facts.BriefPrFacts)}
+    assert not names & {"body", "labels", "title"}
+    query = brief_facts._BRIEF_PR_QUERY
+    assert "labels" not in query and "body" not in query and "title" not in query
+
+
+def test_engineer_preflight_valid_reconfirm_passes(
+    tmp_path, monkeypatch
+) -> None:  # T27
+    forge = consistent_world(monkeypatch)
+    forge.add_policy(C1)
+    forge.reconfirm(C1)
+    assert spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
+
+
+@pytest.mark.parametrize(
+    "spoil",
+    [
+        ("gate-fail", lambda t: t.replace("gate_passed: true", "gate_passed: false")),
+        ("path-traces", lambda t: t.replace("traces_to: []", "traces_to: [notes.md]")),
+        (
+            "blocking",
+            lambda t: t.replace(
+                "blocking_open_questions: 0", "blocking_open_questions: 2"
+            ),
+        ),
+    ],
+    ids=lambda v: v[0] if isinstance(v, tuple) else v,
+)
+def test_engineer_preflight_upstream_gate_refusals(
+    tmp_path, monkeypatch, spoil
+) -> None:  # T32
+    forge = consistent_world(monkeypatch)
+    text = spoil[1](SIGNED)
+    assert text != SIGNED
+    with pytest.raises(spec_loop.SpecLoopError, match="upstream не годится"):
+        spec_loop.engineer_preflight(_operator(tmp_path, text), 7, ENGINEER_REPO, forge)
+
+
+@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
+def test_engineer_preflight_ambiguous_merged_request(  # T21a/T21b через preflight
+    tmp_path, monkeypatch, case, mutate, _m
+) -> None:
+    from governance import approval_request as _ar
+    from tests.forge_fake import request as _request
+
+    forge = consistent_world(monkeypatch)
+    forge.files[(MERGE, _REQ)] = mutate(_ar.render(_request()))
+    with pytest.raises(spec_loop.SpecLoopError, match="request_invalid"):
+        spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
```

```diff
diff --git a/tests/test_governance_runner.py b/tests/test_governance_runner.py
index a7c0c76..92ff7da 100644
--- a/tests/test_governance_runner.py
+++ b/tests/test_governance_runner.py
@@ -9493,3 +9493,45 @@ def test_customer_without_brief_only_still_goes_to_s1(
     state = locked_runner.resume("r-nobo", ops)
     assert state.status != "brief_ready"
     assert any(c[0] in _GIT_CALLS for c in ops.calls)
+
+
+# --- §11.4.4: durable upstream.md из буфера preflight (Task 8, часть B) ---
+
+
+def _engineer_intake(buffer: bytes = b"---\nupstream\n") -> iv.EngineerIntake:
+    return iv.EngineerIntake(
+        buffer=buffer,
+        blob=blob_sha1_bytes(buffer),
+        approval={"pr": 7, "self_hash": "sha256:x"},
+        approval_pr=7,
+        source_path="/op/customer-brief.md",
+    )
+
+
+def test_start_writes_upstream_copy_from_intake_buffer(tmp_path, runs_root) -> None:
+    intake = _engineer_intake()
+    ops = FakeOps(discovery=[("start", _reply(1))])
+    state = locked_runner.start(
+        **_start_kwargs(tmp_path, "r-eng-copy", ops),
+        interview_spec=_need_spec(
+            frame="engineer", traces_to="upstream.md", upstream_blob=intake.blob
+        ),
+        engineer_intake=intake,
+    )
+    copy = rs.run_dir("r-eng-copy") / iv.UPSTREAM_REL
+    assert copy.read_bytes() == intake.buffer
+    assert state.interview["approval_pr"] == 7
+    assert state.interview["upstream_source"] == "/op/customer-brief.md"
+    assert state.interview["approval"]["pr"] == 7
+
+
+def test_start_refuses_intake_blob_mismatch(tmp_path, runs_root) -> None:
+    intake = _engineer_intake()
+    with pytest.raises(ValueError, match="upstream_blob"):
+        locked_runner.start(
+            **_start_kwargs(tmp_path, "r-eng-bad", FakeOps()),
+            interview_spec=_need_spec(
+                frame="engineer", traces_to="upstream.md", upstream_blob="0" * 40
+            ),
+            engineer_intake=intake,
+        )
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/interview.py b/governance/interview.py
index 66ee931..71a8066 100644
--- a/governance/interview.py
+++ b/governance/interview.py
@@ -87,6 +87,32 @@ def parse_reply(returncode: int, stdout: str, stderr: str) -> DiscoveryReply:
 
 
 BRIEF_REL = "brief-input/00-discovery/brief.md"
+#: Имя принятой копии upstream у соседа и у нас (§11.4.4): фиксированное.
+UPSTREAM_NAME = "upstream.md"
+UPSTREAM_REL = "brief-input/00-discovery/upstream.md"
+
+
+def engineer_session_id(run_id: str) -> str:
+    """Caller-assigned id сессии engineer'а (§11.4.5): детерминирован от прогона."""
+    return f"s-{run_id}-e"
+
+
+@dataclass(frozen=True)
+class EngineerIntake:
+    """Итог engineer-preflight (§11.4.2): проверенный буфер и отчёт об акте.
+
+    `buffer` — байты файла оператора, прочитанные ОДИН раз; durable-копия
+    пишется только из них. `approval` — `Act.as_record()` (отчёт, не источник
+    доверия: при перепроверках акт выводится из форджа по `approval_pr`).
+    """
+
+    buffer: bytes
+    blob: str
+    approval: dict
+    approval_pr: int
+    source_path: str
+
+
 _FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
```

```diff
diff --git a/governance/brief_input.py b/governance/brief_input.py
index 66a31f5..8785e5d 100644
--- a/governance/brief_input.py
+++ b/governance/brief_input.py
@@ -182,6 +182,35 @@ def _reject_customer_path_traces(meta: dict[str, object]) -> None:
         )
 
 
+def check_customer_upstream(path: Path, data: bytes) -> None:
+    """Customer-бриф годится в upstream engineer'а (E1 и engineer-preflight §11.4.2 п.1).
+
+    Байты уже прочитаны вызывающим (preflight читает файл оператора ОДИН раз):
+    UTF-8 без CR, gate pass/`gate_passed`/блокеры (`_gate`), `frame: customer`,
+    без путевых `traces_to`, `status: approved`. Происхождение подписи здесь НЕ
+    проверяется — это `brief_provenance` (§11.4.2 п.2–7).
+    """
+    if b"\r" in data:
+        raise BriefInputError(f"upstream {path} использует CR/CRLF; нужен LF")
+    try:
+        text = data.decode("utf-8")
+    except UnicodeError as exc:
+        raise BriefInputError(f"upstream {path} не UTF-8: {exc}") from exc
+    customer = _gate(path, text)
+    interview = customer.meta.get("interview") or {}
+    frame = interview.get("frame") if isinstance(interview, dict) else None
+    if frame != "customer":
+        raise BriefInputError(
+            f"engineer upstream {path.name!r} имеет frame={frame!r}, ожидался customer"
+        )
+    _reject_customer_path_traces(customer.meta)
+    if customer.meta.get("status") != "approved":
+        raise BriefInputError(
+            f"engineer upstream {path.name!r} не approved: "
+            f"status={customer.meta.get('status')!r}"
+        )
+
+
 def inspect_brief(path: Path) -> BriefSource:
     """Validate an input brief and resolve its effective requirements source."""
     path = path.resolve()
@@ -210,25 +239,7 @@ def inspect_brief(path: Path) -> BriefSource:
     if customer_path is None:
         raise BriefInputError(f"customer upstream {ref!r} не разрешается")
     customer_data = _read_bytes(customer_path)
-    customer_text = _decode(customer_data)
-    customer = _gate(customer_path, customer_text)
-    customer_interview = customer.meta.get("interview") or {}
-    customer_frame = (
-        customer_interview.get("frame")
-        if isinstance(customer_interview, dict)
-        else None
-    )
-    if customer_frame != "customer":
-        raise BriefInputError(
-            f"engineer upstream {ref!r} имеет frame={customer_frame!r}, "
-            "ожидался customer"
-        )
-    _reject_customer_path_traces(customer.meta)
-    if customer.meta.get("status") != "approved":
-        raise BriefInputError(
-            f"engineer upstream {ref!r} не approved: "
-            f"status={customer.meta.get('status')!r}"
-        )
+    check_customer_upstream(customer_path, customer_data)
     requirements_rel = f"00-discovery/{ref}"
     return BriefSource(
         frame="engineer",
```

```diff
diff --git a/governance/spec_loop.py b/governance/spec_loop.py
index 3e59c82..bc01db2 100644
--- a/governance/spec_loop.py
+++ b/governance/spec_loop.py
@@ -60,13 +60,14 @@ import subprocess
 import sys
 import tomllib
 from collections.abc import Callable
-from dataclasses import dataclass
+from dataclasses import dataclass, replace
 from datetime import date
 from pathlib import Path
 
 from governance import (
     approval_branches,
     brief_input,
+    brief_provenance,
     bundle_dag,
     charter_guard,
     run_lock,
@@ -75,9 +76,9 @@ from governance import (
 )
 from governance import approval_ledger as al
 from governance import interview as iv
-from governance import ops as ops_mod
 from governance import run_state as rs
 from governance.ops import DEVTOOLS_ROOT, RealOps
+from governance.stale_adapter import blob_sha1_bytes
 
 WORKSPACE_ROOT = DEVTOOLS_ROOT.parent
 MANIFEST_PATH = (
@@ -267,7 +268,15 @@ def _origin_url(target_dir: str | Path) -> str:
     return out.stdout.strip()
 
 
-NEED_ONLY = ("frame", "stakeholder", "traces_to", "session", "new_run", "brief_only")
+NEED_ONLY = (
+    "frame",
+    "stakeholder",
+    "traces_to",
+    "session",
+    "new_run",
+    "brief_only",
+    "approval_pr",
+)
 STAKEHOLDER_RULE = (
     "стадия Need запускается только при наличии реального стейкхолдера — "
     "укажите --stakeholder <role> (декларация, не проверка); без "
@@ -306,8 +315,10 @@ def build_interview_spec(args, repo_slug: str) -> iv.InterviewSpec | None:
             "--session (§11.2)"
         )
     if args.frame == "customer":
-        if args.traces_to:
-            raise SpecLoopError("customer-фрейм не принимает --traces-to")
+        if args.traces_to or args.approval_pr is not None:
+            raise SpecLoopError(
+                "customer-фрейм не принимает --traces-to и --approval-pr"
+            )
         return iv.InterviewSpec(
             "customer",
             args.stakeholder,
@@ -316,11 +327,59 @@ def build_interview_spec(args, repo_slug: str) -> iv.InterviewSpec | None:
             None,
             brief_only=bool(args.brief_only),
         )
-    if not args.traces_to:
+    if not args.traces_to or args.approval_pr is None:
         raise SpecLoopError(
-            "engineer-фрейм требует --traces-to <approved customer-brief>"
+            "engineer-фрейм требует --traces-to <подписанный customer-бриф> и "
+            "--approval-pr <номер brief-PR> (§11.4.1)"
         )
-    raise SpecLoopError(ops_mod.ENGINEER_BLOCKED)  # D6, до discovery#49
+    if args.session:
+        raise SpecLoopError(
+            "--session для engineer запрещён: id сессии записан до вызова (§11.4.5)"
+        )
+    # Соседу уходит `--upstream <durable upstream.md>`; переносимая ссылка
+    # итогового брифа — `upstream.md`. `upstream_blob` ставит preflight.
+    return iv.InterviewSpec(
+        "engineer", args.stakeholder, repo_slug, iv.UPSTREAM_NAME, None
+    )
+
+
+def _refusal_text(refusal: brief_provenance.Refusal) -> str:
+    return refusal.detail if refusal.retry else f"{refusal.reason}: {refusal.detail}"
+
+
+def engineer_preflight(
+    path: str, approval_pr: int, repo_slug: str, ops
+) -> iv.EngineerIntake:
+    """§11.4.2: файл оператора читается ОДИН раз; всё — над буфером и фактами форджа.
+
+    До run-id, до леджера, до вызова соседа. Отказ — `SpecLoopError`; при
+    неустановленном факте форджа текст говорит «повторите», не «не одобрено».
+    """
+    try:
+        buffer = Path(path).read_bytes()
+    except OSError as exc:
+        raise SpecLoopError(f"{path}: не читается ({exc})") from exc
+    try:
+        brief_input.check_customer_upstream(Path(path), buffer)
+    except brief_input.BriefInputError as exc:
+        raise SpecLoopError(f"upstream не годится: {exc}") from exc
+    act = brief_provenance.read_act(ops, repo_slug, approval_pr)
+    if isinstance(act, brief_provenance.Refusal):
+        raise SpecLoopError(_refusal_text(act))
+    text = buffer.decode("utf-8")
+    for refusal in (
+        brief_provenance.check_operator_brief(act, text),
+        brief_provenance.check_policy(ops, act),
+    ):
+        if isinstance(refusal, brief_provenance.Refusal):
+            raise SpecLoopError(_refusal_text(refusal))
+    return iv.EngineerIntake(
+        buffer=buffer,
+        blob=blob_sha1_bytes(buffer),
+        approval=act.as_record(),
+        approval_pr=approval_pr,
+        source_path=str(path),
+    )
 
 
 def find_runs(repo: str, subject: str) -> list[rs.RunState]:
@@ -888,7 +947,15 @@ def main(argv: list[str] | None = None) -> int:
     parser.add_argument(
         "--stakeholder", help="роль реального стейкхолдера (декларация)"
     )
-    parser.add_argument("--traces-to", help="approved customer-brief для engineer")
+    parser.add_argument(
+        "--traces-to", help="engineer: подписанный customer-бриф (файл оператора)"
+    )
+    parser.add_argument(
+        "--approval-pr",
+        type=int,
+        default=None,
+        help="engineer: номер brief-PR — акта одобрения upstream (§11.4.1)",
+    )
     parser.add_argument("--session", help="recovery: присоединить сессию discovery")
     parser.add_argument(
         "--brief-only",
@@ -1131,6 +1198,12 @@ def main(argv: list[str] | None = None) -> int:
                     f"brief_only={recorded.brief_only}) — сменить их: "
                     "--new-run --ws-id"
                 )
+            if state.interview.get("approval_pr") != args.approval_pr:
+                raise SpecLoopError(
+                    "brief-PR акта зафиксирован стартом (--approval-pr "
+                    f"{state.interview.get('approval_pr')}) — сменить: "
+                    "--new-run --ws-id"
+                )
 
         if args.session:
             # Позиция намеренная (ruling 3, controller Task 10): ПОСЛЕ
@@ -1175,6 +1248,13 @@ def main(argv: list[str] | None = None) -> int:
             }
             _print_values(values)
             return _dispatch(state, ops, held[state.run_id])
+        engineer_intake = None
+        if interview_spec is not None and interview_spec.frame == "engineer":
+            # §11.4.2: до run-id, до леджера, до вызова соседа.
+            engineer_intake = engineer_preflight(
+                args.traces_to, args.approval_pr, entry.repo_slug, ops
+            )
+            interview_spec = replace(interview_spec, upstream_blob=engineer_intake.blob)
         ws_id = args.ws_id or ws_id_for(args.subject, date.today())
         rs.validate_id_component(ws_id, label="ws_id")
         collisions = []
@@ -1232,6 +1312,7 @@ def main(argv: list[str] | None = None) -> int:
             author_backend=args.author_backend,
             brief_source=supplied_brief,
             interview_spec=interview_spec,
+            engineer_intake=engineer_intake,
             authoring="legacy" if args.legacy else "waves",
             code=args.code,
             plan_item=args.plan_item,
```

```diff
diff --git a/governance/runner.py b/governance/runner.py
index 0b4c0bd..d2eadba 100644
--- a/governance/runner.py
+++ b/governance/runner.py
@@ -335,6 +335,7 @@ def start(
     author_backend: str = "codex",
     brief_source: brief_input.BriefSource | None = None,
     interview_spec: iv.InterviewSpec | None = None,
+    engineer_intake: iv.EngineerIntake | None = None,
     allow_legacy_dt: bool = False,
     authoring: str = "waves",
     code: str | None = None,
@@ -432,10 +433,33 @@ def start(
         code=code,
         plan_item=plan_item,
     )
+    if engineer_intake is not None:
+        _write_engineer_upstream(state, engineer_intake)
     save(state)
     return advance(state, ops, lock=lock)
 
 
+def _write_engineer_upstream(state: RunState, intake: iv.EngineerIntake) -> None:
+    """§11.4.4: durable `upstream.md` — из буфера preflight, атомарно, до `start`.
+
+    `upstream_blob` уже стоит в координатах интервью (из того же буфера);
+    `approval` — отчёт; `approval_pr` — адрес, по которому акт перечитывается
+    из форджа при каждой перепроверке (§11.1.2).
+    """
+    if state.interview is None or state.interview.get("frame") != "engineer":
+        raise ValueError("engineer_intake без engineer-координат интервью")
+    if blob_sha1_bytes(intake.buffer) != state.interview.get("upstream_blob"):
+        raise ValueError("upstream_blob координат ≠ буферу preflight")
+    target = run_dir(state.run_id) / iv.UPSTREAM_REL
+    target.parent.mkdir(parents=True, exist_ok=True)
+    tmp = target.with_name(".upstream.tmp")
+    tmp.write_bytes(intake.buffer)
+    os.replace(tmp, target)
+    state.interview["approval"] = dict(intake.approval)
+    state.interview["approval_pr"] = intake.approval_pr
+    state.interview["upstream_source"] = intake.source_path
+
+
 def advance(state: RunState, ops: Ops, *, lock: rl.RunLock) -> RunState:
     """Выполняет шаги S1..S8 до стопа (не-``running`` статус) либо конца.
```

Промежуточное состояние (проверено): после задачи 8 engineer-прогон стартует, но стадия
останавливается на порте (переданы и `traces_to`, и `upstream_path`) — снимается в
задаче 9; набор зелёный.

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_governance_spec_loop.py -q` → 171 passed; мутации матрицы через `main`: снятие `check_operator_brief` (6 случаев падают; черновик и CRLF раньше отсекает гейт upstream), снятие `check_policy` (12), чтение локального git в preflight (T24) — пойманы; прежние мутации: сверка `approval_pr` на повторе, вызов preflight в `main`, `check_operator_brief`, сверка blob в `start` — 4/4 пойманы (проверено).

- [ ] **Step 5: Commit** — `feat(need): engineer-preflight по фактам форджа (§11.4.1–§11.4.2)`.

---

### Task 9: Engineer-стадия — write-ahead id, перепроверки, восстановление

**Files:**
- Modify: `governance/runner.py` (`_LOCK_FD`, `advance`, `_interview_stop` пишет
  `interview.stop_reason`, `_step_interview` → `_step_interview_engineer`,
  `_engineer_recover`, `_engineer_guard`, `_engineer_source_mismatch`, перепроверки в
  `_interview_poll`/`_interview_publish`/`_interview_reconcile_published`, правило
  «сирота» в `resume`, отказ `attach_session` для engineer; удалены `_upstream_path` и
  `ENGINEER_BLOCKED`), `governance/ops.py` (константа `ENGINEER_BLOCKED` удалена),
  `governance/spec_loop.py` (правило «сирота» — только customer)
- Create: `tests/fixtures/discovery_approval/engineer-brief.md`
- Test: `tests/test_governance_runner.py`, `tests/test_governance_spec_loop.py`

**Interfaces:**
- Produces: причины `stopped_interview` в `interview.stop_reason` с машинным префиксом
  (`upstream_blob_mismatch`, `session_id_mismatch`, `session_unverifiable`,
  `session_state_unknown`, `upstream_policy_drift` и причины `Refusal` акта).
- `FakeOps` раннера: `forge` (делегирование фактов форджа), `lock_fds`,
  `discovery_hook(kind, session_id)`.

Engineer-бриф фикстуры (проходит E1 рядом с подписанным `upstream.md`: GC-05 —
упомянуты все Must-FR upstream; GC-12 — дата после одобрения upstream):

```markdown
---
spec_stage: discovery
status: draft
version: 1
generated_by: discovery-runtime
generated_at: '2026-10-09T00:00:00Z'
validation: pass
owner_role: architect
schema: discovery-brief
schema_version: 1
feeds: [system-assessment, tech-selection]
interview:
  frame: engineer
  sessions:
    - participant_role: po
coverage:
  systems: covered
  interfaces: covered
  constraints: covered
  arch_preferences: covered
  risks: covered
  feasibility_review: covered
  gate_passed: true
open_questions: 0
blocking_open_questions: 0
conflicts: 0
traces_to: [upstream.md]
---

# Discovery Brief — owner/alpha (engineer-фрейм)

- **S-01** System
- **IF-01** `traces: [S-01]` Interface
- **CON-01** Constraint
- **AP-01** `traces: [S-01, CON-01]` Preference
- **RK-01** Risk

## Feasibility

- FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07 и FR-08 выполнимы.
```

- [ ] **Step 1: Тесты** (write-ahead до вызова; `upstream.md`/`--session-id`/`lock_fd` в
`start`; T28–T30, T34, T36, T39, T41, T42; смена политики во время рендера;
согласованная локальная подмена; стоп до `start` продолжаем; `--session` для engineer —
отказ; B2 — `session_id` удалён/обнулён после write-ahead — стоп `session_id_mismatch`
(пустой id допустим только пока операция `interview-start` в статусе `new`); B4 — `lock_fd`
передаётся КАЖДОМУ вызову соседа, включая `attach_session`; T38 для git/gh-потомков —
класс, а не экземпляр: во всём `governance/` `pass_fds` есть только у порта discovery и нигде
нет `close_fds=False`; настоящие пути `RealOps` (git, gh) под блокировкой — зонд-заглушка на
`PATH` не видит файла блокировки среди своих дескрипторов, положительный контроль зонда —
явно переданный дескриптор он видит):

```diff
diff --git a/tests/test_governance_runner.py b/tests/test_governance_runner.py
index 92ff7da..5e6a95d 100644
--- a/tests/test_governance_runner.py
+++ b/tests/test_governance_runner.py
@@ -27,6 +27,7 @@ from governance import interview as iv
 from governance import run_state as rs
 from governance.stale_adapter import blob_sha1, blob_sha1_bytes
 from tests import locked_runner
+from tests.forge_fake import C1, C2, SIGNED, consistent_world
 from tests.governance_fixtures.bundles import make_bundle, make_profile
 
 GREEN_PR_FACTS: dict[str, Any] = {
@@ -175,6 +176,12 @@ class FakeOps:
     calls: list[tuple] = field(default_factory=list)
     # Очередь ответов discovery: ("start"|"status"|"brief", DiscoveryReply).
     discovery: list[tuple[str, Any]] = field(default_factory=list)
+    #: Стенд форджа engineer-маршрута (§11.4.2–§11.4.3); факты делегируются.
+    forge: Any = None
+    #: `lock_fd`, с которыми звали соседа (§11.4.5).
+    lock_fds: list[int | None] = field(default_factory=list)
+    #: Вызывается В МОМЕНТ обращения к соседу: (kind, kwargs) → None.
+    discovery_hook: Any = None
     discovery_calls: list[tuple] = field(default_factory=list)
     # Текст, который `discovery_brief` пишет в `out_path` при кодах 0/10/11/20.
     brief_text: str = ""
@@ -575,15 +582,48 @@ class FakeOps:
         session_id=None,
         lock_fd=None,
     ):
-        self.discovery_calls.append(("start", frame, target, traces_to, upstream_path))
+        call = ("start", frame, target, traces_to, upstream_path)
+        self.discovery_calls.append(call if session_id is None else (*call, session_id))
+        self.lock_fds.append(lock_fd)
+        if self.discovery_hook is not None:
+            self.discovery_hook("start", session_id)
         return self._discovery_reply("start")
 
     def discovery_status(self, session_id, cwd, *, lock_fd=None):
         self.discovery_calls.append(("status", session_id))
+        self.lock_fds.append(lock_fd)
+        if self.discovery_hook is not None:
+            self.discovery_hook("status", session_id)
         return self._discovery_reply("status")
 
+    # Факты форджа engineer-маршрута — делегирование в `FakeForge`.
+    def _forge(self) -> Any:
+        assert self.forge is not None, "факт форджа без стенда forge"
+        return self.forge
+
+    def brief_pr_fact(self, repo_slug, pr):
+        return self._forge().brief_pr_fact(repo_slug, pr)
+
+    def default_branch_fact(self, repo_slug):
+        return self._forge().default_branch_fact(repo_slug)
+
+    def pr_comments_fact(self, repo_slug, pr):
+        return self._forge().pr_comments_fact(repo_slug, pr)
+
+    def repo_file_fact(self, repo_slug, sha, path):
+        return self._forge().repo_file_fact(repo_slug, sha, path)
+
+    def policy_version_fact(self, repo_slug, branch, path):
+        return self._forge().policy_version_fact(repo_slug, branch, path)
+
+    def policy_version_fact_at(self, repo_slug, ref, path, sha):
+        return self._forge().policy_version_fact_at(repo_slug, ref, path, sha)
+
     def discovery_brief(self, session_id, out_path, cwd, *, lock_fd=None):
         self.discovery_calls.append(("brief", session_id, out_path))
+        self.lock_fds.append(lock_fd)
+        if self.discovery_hook is not None:
+            self.discovery_hook("brief", session_id)
         reply = self._discovery_reply("brief")
         # Стенд пишет артефакт при кодах 0/10/11/20, как сосед.
         if reply.code in (0, 10, 11, 20):
@@ -9342,34 +9382,6 @@ def test_interview_of_without_coordinates_is_an_explicit_error() -> None:
         runner._interview_of(state)
 
 
-def test_upstream_path_engineer_frame_needs_traces_to() -> None:
-    state = SimpleNamespace(run_id="r-eng")
-    spec = _need_spec(frame="engineer", traces_to=None)
-    with pytest.raises(ValueError, match="traces_to"):
-        runner._upstream_path(state, spec)
-    assert runner._upstream_path(state, _need_spec()) is None
-
-
-@pytest.mark.parametrize(
-    "brief",
-    [{}, {"source_paths": []}, {"source_blobs": {}}],
-)
-def test_source_layer_guard_stops_on_incomplete_descriptor(monkeypatch, brief) -> None:
-    """Ревью #530 (major): дескриптор без source_paths/source_blobs не должен
-    давать зелёный S3 — гвард останавливает шаг, ничего не сверив."""
-    from types import SimpleNamespace
-
-    stops: list[str] = []
-    monkeypatch.setattr(
-        runner, "_brief_stop", lambda state, msg: stops.append(msg) or False
-    )
-    state = SimpleNamespace(brief=brief, target_dir="t", bundle_dir="b", run_id="r")
-    ops = SimpleNamespace(rev_parse=lambda *a: "h", blob_in_commit=lambda *a: None)
-
-    assert runner._source_layer_committed(state, ops) is False
-    assert stops and "отсутствует" in stops[0]
-
-
 def test_verify_holds_child_lock_through_s8(  # ревью части A, A2
     tmp_path: Path, runs_root, monkeypatch
 ) -> None:
@@ -9510,7 +9522,9 @@ def _engineer_intake(buffer: bytes = b"---\nupstream\n") -> iv.EngineerIntake:
 
 def test_start_writes_upstream_copy_from_intake_buffer(tmp_path, runs_root) -> None:
     intake = _engineer_intake()
-    ops = FakeOps(discovery=[("start", _reply(1))])
+    # Буфер — не бриф: перепроверка стадии остановит прогон до соседа; предмет
+    # теста — durable-копия и поля интервью, записанные ДО первого шага.
+    ops = FakeOps(forge=consistent_world())
     state = locked_runner.start(
         **_start_kwargs(tmp_path, "r-eng-copy", ops),
         interview_spec=_need_spec(
@@ -9535,3 +9549,415 @@ def test_start_refuses_intake_blob_mismatch(tmp_path, runs_root) -> None:
             ),
             engineer_intake=intake,
         )
+
+
+# --- §11.4.3–§11.4.5: engineer-стадия (Task 9, часть B) ---
+
+ENGINEER_BRIEF = (
+    Path(__file__).parent / "fixtures" / "discovery_approval" / "engineer-brief.md"
+).read_text(encoding="utf-8")
+
+
+def _eng_reply(code: int, run_id: str, **over) -> iv.DiscoveryReply:
+    if code == 20:
+        over.setdefault(
+            "next_action", {"session_id": f"s-{run_id}-e", "question_id": "Q-01"}
+        )
+    return _reply(code, **over)
+
+
+def _engineer_run(tmp_path, monkeypatch, run_id, replies, forge=None):
+    forge = forge or consistent_world(monkeypatch)
+    ops = FakeOps(discovery=list(replies), forge=forge, brief_text=ENGINEER_BRIEF)
+    buffer = SIGNED.encode("utf-8")
+    state = locked_runner.start(
+        **_start_kwargs(tmp_path, run_id, ops),
+        interview_spec=_need_spec(
+            frame="engineer",
+            traces_to="upstream.md",
+            upstream_blob=blob_sha1_bytes(buffer),
+        ),
+        engineer_intake=iv.EngineerIntake(
+            buffer=buffer,
+            blob=blob_sha1_bytes(buffer),
+            approval={"pr": 7, "self_hash": "report-only"},
+            approval_pr=7,
+            source_path="/op/customer-brief.md",
+        ),
+    )
+    return ops, state
+
+
+def _stop_reason(run_id: str) -> str:
+    return str(rs.load(run_id).interview.get("stop_reason", ""))
+
+
+def test_engineer_start_uses_upstream_session_id_and_lock_fd(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, state = _engineer_run(
+        tmp_path, monkeypatch, "r-e1", [("start", _eng_reply(20, "r-e1"))]
+    )
+    assert state.status == "waiting_interview"
+    call = ops.discovery_calls[0]
+    assert call[0] == "start" and call[3] is None  # traces_to соседу не уходит
+    assert call[4].endswith("brief-input/00-discovery/upstream.md")
+    assert call[5] == "s-r-e1-e"
+    assert ops.lock_fds == [ops.lock_fds[0]] and isinstance(ops.lock_fds[0], int)
+    assert state.ops["interview-start"]["status"] == "completed"
+
+
+def test_engineer_session_id_is_written_before_start(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    seen: list[object] = []
+
+    def hook(kind, session_id):
+        if kind == "start":
+            persisted = rs.load("r-e2")
+            seen.append(persisted.interview["session_id"])
+            seen.append(persisted.ops["interview-start"]["status"])
+
+    forge = consistent_world(monkeypatch)
+    ops = FakeOps(
+        discovery=[("start", _eng_reply(20, "r-e2"))],
+        forge=forge,
+        discovery_hook=hook,
+    )
+    buffer = SIGNED.encode()
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-e2", ops),
+        interview_spec=_need_spec(
+            frame="engineer",
+            traces_to="upstream.md",
+            upstream_blob=blob_sha1_bytes(buffer),
+        ),
+        engineer_intake=iv.EngineerIntake(
+            buffer, blob_sha1_bytes(buffer), {}, 7, "/op/b.md"
+        ),
+    )
+    assert seen == ["s-r-e2-e", "started"]
+
+
+def test_upstream_copy_tamper_stops_before_neighbor(  # T34
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-e3", [("start", _eng_reply(20, "r-e3"))]
+    )
+    (rs.run_dir("r-e3") / iv.UPSTREAM_REL).write_text("подмена", encoding="utf-8")
+    calls = len(ops.discovery_calls)
+    state = locked_runner.resume("r-e3", ops)
+    assert state.status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason("r-e3").startswith("upstream_blob_mismatch")
+
+
+def test_consistent_local_tamper_cannot_pass(  # T30 + P1 ревью r1
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    """Подмена upstream.md, upstream_blob и approval В СОГЛАСИИ между собой не
+    проходит: durable-копия сверяется с актом, перевыведенным из форджа."""
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-e4", [("start", _eng_reply(20, "r-e4"))]
+    )
+    forged = SIGNED.replace("## Goals", "## Goals\n\nчужая цель\n", 1).encode()
+    (rs.run_dir("r-e4") / iv.UPSTREAM_REL).write_bytes(forged)
+    st = rs.load("r-e4")
+    st.interview["upstream_blob"] = blob_sha1_bytes(forged)
+    st.interview["approval"]["self_hash"] = "sha256:forged"
+    rs.save(st)
+    calls = len(ops.discovery_calls)
+    state = locked_runner.resume("r-e4", ops)
+    assert state.status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason("r-e4").startswith("operator_brief")
+
+
+def test_policy_drift_between_visits_stops(  # T29-сценарий
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-e5", [("start", _eng_reply(20, "r-e5"))]
+    )
+    ops.forge.add_policy(C1)
+    calls = len(ops.discovery_calls)
+    state = locked_runner.resume("r-e5", ops)
+    assert state.status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason("r-e5").startswith("upstream_policy_drift")
+
+
+def test_reconfirm_resume_drift_sequence(
+    tmp_path, runs_root, monkeypatch
+) -> None:  # T28
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-e6",
+        [("start", _eng_reply(20, "r-e6")), ("status", _eng_reply(20, "r-e6"))],
+    )
+    ops.forge.add_policy(C1)
+    assert locked_runner.resume("r-e6", ops).status == "stopped_interview"
+    ops.forge.reconfirm(C1)
+    assert locked_runner.resume("r-e6", ops).status == "waiting_interview"
+    ops.forge.add_policy(C2)
+    assert locked_runner.resume("r-e6", ops).status == "stopped_interview"
+    assert _stop_reason("r-e6").startswith("upstream_policy_drift")
+
+
+def test_policy_change_during_render_blocks_publication(  # P4 ревью r1
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-e7",
+        [
+            ("start", _eng_reply(20, "r-e7")),
+            ("status", _eng_reply(0, "r-e7")),
+            ("brief", _eng_reply(0, "r-e7")),
+        ],
+    )
+
+    def drift_on_brief(kind, session_id):
+        if kind == "brief":
+            ops.forge.add_policy(C1)
+
+    ops.discovery_hook = drift_on_brief
+    state = locked_runner.resume("r-e7", ops)
+    assert state.status == "stopped_interview"
+    assert not (rs.run_dir("r-e7") / "brief-input/00-discovery/brief.md").exists()
+    assert _stop_reason("r-e7").startswith("upstream_policy_drift")
+
+
+def test_engineer_publication_reaches_s1_with_upstream_source(
+    tmp_path, runs_root, monkeypatch
+) -> None:  # T43 (публикация)
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-e8",
+        [
+            ("start", _eng_reply(20, "r-e8")),
+            ("status", _eng_reply(0, "r-e8")),
+            ("brief", _eng_reply(0, "r-e8")),
+        ],
+    )
+    state = locked_runner.resume("r-e8", ops)
+    assert state.interview["completed_at"]
+    blobs = dict(state.brief["source_blobs"])
+    assert blobs["discovery-customer"] == blob_sha1_bytes(SIGNED.encode())
+
+
+def test_session_id_mismatch_stops(tmp_path, runs_root, monkeypatch) -> None:  # T36
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-e9", [("start", _eng_reply(20, "r-e9"))]
+    )
+    st = rs.load("r-e9")
+    st.interview["session_id"] = "s-other"
+    rs.save(st)
+    calls = len(ops.discovery_calls)
+    assert locked_runner.resume("r-e9", ops).status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason("r-e9").startswith("session_id_mismatch")
+
+
+@pytest.mark.parametrize("code", [20, 0, 10, 11])
+def test_recovery_with_existing_session_is_unverifiable(  # T39
+    tmp_path, runs_root, monkeypatch, code
+) -> None:
+    run_id = f"r-ea{code}"
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        run_id,
+        [("start", _eng_reply(1, run_id)), ("status", _eng_reply(code, run_id))],
+    )
+    state = locked_runner.resume(run_id, ops)
+    assert state.status == "stopped_interview"
+    assert [c[0] for c in ops.discovery_calls] == ["start", "status"]
+    assert _stop_reason(run_id).startswith("session_unverifiable")
+    assert state.interview["session_id"] == f"s-{run_id}-e"
+
+
+def test_recovery_unknown_status_is_hard_stop(
+    tmp_path, runs_root, monkeypatch
+) -> None:  # T41
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-eb",
+        [("start", _eng_reply(1, "r-eb")), ("status", _reply(1))],
+    )
+    state = locked_runner.resume("r-eb", ops)
+    assert state.status == "stopped_interview"
+    assert [c[0] for c in ops.discovery_calls] == ["start", "status"]
+    assert _stop_reason("r-eb").startswith("session_state_unknown")
+
+
+@pytest.mark.parametrize(
+    "traces", ["[upstream.md, other.md]", "[brief.md]", "[customer.md]"]
+)
+def test_engineer_brief_must_trace_only_upstream(  # T42
+    tmp_path, runs_root, monkeypatch, traces
+) -> None:
+    run_id = "r-ec" + str(abs(hash(traces)) % 1000)
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        run_id,
+        [
+            ("start", _eng_reply(20, run_id)),
+            ("status", _eng_reply(0, run_id)),
+            ("brief", _eng_reply(0, run_id)),
+        ],
+    )
+    ops.brief_text = ENGINEER_BRIEF.replace(
+        "traces_to: [upstream.md]", f"traces_to: {traces}"
+    )
+    state = locked_runner.resume(run_id, ops)
+    assert state.status == "stopped_interview"
+    assert not (rs.run_dir(run_id) / "brief-input/00-discovery/brief.md").exists()
+
+
+def test_guard_stop_before_start_is_resumable(  # не «сирота»
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    forge = consistent_world(monkeypatch)
+    forge.unavailable_facts.add("pr")
+    ops, state = _engineer_run(
+        tmp_path, monkeypatch, "r-ed", [("start", _eng_reply(20, "r-ed"))], forge=forge
+    )
+    assert state.status == "stopped_interview" and ops.discovery_calls == []
+    forge.unavailable_facts.clear()
+    assert locked_runner.resume("r-ed", ops).status == "waiting_interview"
+
+
+def test_attach_session_refused_for_engineer(tmp_path, runs_root, monkeypatch) -> None:
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-ee", [("start", _eng_reply(1, "r-ee"))]
+    )
+    with pytest.raises(ValueError, match="engineer"):
+        locked_runner.attach_session("r-ee", "s-x", ops)
+
+
+# --- ревью части B, круг 1: B2, B4 ---
+
+
+@pytest.mark.parametrize("value", ["s-other", None, "", "removed"])
+def test_session_id_tamper_after_start_stops(  # T36 (B2)
+    tmp_path, runs_root, monkeypatch, value
+) -> None:
+    run_id = "r-sid-" + str(abs(hash(str(value))) % 1000)
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, run_id, [("start", _eng_reply(20, run_id))]
+    )
+    st = rs.load(run_id)
+    if value == "removed":
+        del st.interview["session_id"]
+    else:
+        st.interview["session_id"] = value
+    rs.save(st)
+    calls = len(ops.discovery_calls)
+    assert locked_runner.resume(run_id, ops).status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason(run_id).startswith("session_id_mismatch")
+
+
+def test_attach_session_passes_lock_fd(tmp_path, runs_root) -> None:  # B4
+    ops = FakeOps(
+        discovery=[("start", _reply(1)), ("brief", _reply(20))],
+        brief_text=_need_brief_text(),
+    )
+    locked_runner.start(
+        **_start_kwargs(tmp_path, "r-att-fd", ops), interview_spec=_need_spec()
+    )
+    locked_runner.attach_session("r-att-fd", "s-1", ops)
+    assert ops.lock_fds and isinstance(ops.lock_fds[-1], int)
+
+
+def test_every_neighbor_call_in_engineer_flow_passes_lock_fd(  # T38 (production call sites)
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-fds",
+        [
+            ("start", _eng_reply(20, "r-fds")),
+            ("status", _eng_reply(0, "r-fds")),
+            ("brief", _eng_reply(0, "r-fds")),
+        ],
+    )
+    locked_runner.resume("r-fds", ops)
+    assert len(ops.lock_fds) == 3 and all(isinstance(fd, int) for fd in ops.lock_fds)
+
+
+def test_only_the_discovery_port_passes_descriptors() -> None:  # T38 (git/gh-потомки)
+    """Класс, а не экземпляр: во всём `governance/` дескрипторы потомку передаёт
+    только порт discovery (`RealOps._discovery`); `close_fds=False` нет нигде —
+    git/gh-потомки блокировку прогона не наследуют (`O_CLOEXEC` + close_fds)."""
+    import ast
+
+    passing: set[str] = set()
+    root = Path(runner.__file__).parent
+    for path in sorted(root.glob("*.py")):
+        tree = ast.parse(path.read_text(encoding="utf-8"))
+        for func in ast.walk(tree):
+            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
+                continue
+            for call in ast.walk(func):
+                if not isinstance(call, ast.Call):
+                    continue
+                for kw in call.keywords:
+                    if kw.arg == "pass_fds":
+                        passing.add(f"{path.name}:{func.name}")
+                    if kw.arg == "close_fds":
+                        raise AssertionError(f"close_fds в {path.name}:{func.name}")
+    assert passing == {"ops.py:_discovery"}
+
+
+_FD_PROBE = """#!{python}
+import os, sys
+lock = os.stat(os.environ["PROBE_LOCK"])
+held = []
+for name in os.listdir("/dev/fd"):
+    try:
+        st = os.fstat(int(name))
+    except OSError:
+        continue
+    if (st.st_dev, st.st_ino) == (lock.st_dev, lock.st_ino):
+        held.append(name)
+with open(os.environ["PROBE_OUT"], "a", encoding="utf-8") as out:
+    out.write(os.path.basename(sys.argv[0]) + ":" + ",".join(held) + "\\n")
+"""
+
+
+def test_git_and_gh_children_do_not_hold_the_lock(  # T38 (git/gh-потомки)
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    """Настоящие пути RealOps (`is_dirty` → git, `_graphql_repository` → gh) под
+    взятой блокировкой: потомок не видит открытого файла блокировки."""
+    import subprocess
+    import sys
+
+    from governance.ops import RealOps
+
+    bin_dir = tmp_path / "bin"
+    bin_dir.mkdir()
+    for name in ("git", "gh"):
+        stub = bin_dir / name
+        stub.write_text(_FD_PROBE.format(python=sys.executable), encoding="utf-8")
+        stub.chmod(0o755)
+    out = tmp_path / "probe.txt"
+    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
+    monkeypatch.setenv("PROBE_OUT", str(out))
+    with run_lock.run_lock("r-fd") as lock:
+        monkeypatch.setenv("PROBE_LOCK", str(run_lock.lock_path("r-fd")))
+        RealOps().is_dirty(str(tmp_path))
+        RealOps()._graphql_repository("query{viewer{login}}")
+        # Положительный контроль зонда: явно переданный дескриптор он видит.
+        subprocess.run([str(bin_dir / "git")], pass_fds=(lock.fd,), check=True)
+    lines = out.read_text(encoding="utf-8").splitlines()
+    assert lines == ["git:", "gh:", f"git:{lock.fd}"]
```

```diff
diff --git a/tests/test_governance_spec_loop.py b/tests/test_governance_spec_loop.py
index 34628bf..17ca7e4 100644
--- a/tests/test_governance_spec_loop.py
+++ b/tests/test_governance_spec_loop.py
@@ -2117,3 +2117,36 @@ def test_engineer_preflight_ambiguous_merged_request(  # T21a/T21b через pr
     forge.files[(MERGE, _REQ)] = mutate(_ar.render(_request()))
     with pytest.raises(spec_loop.SpecLoopError, match="request_invalid"):
         spec_loop.engineer_preflight(_operator(tmp_path), 7, ENGINEER_REPO, forge)
+
+
+def test_engineer_stop_without_session_is_not_an_orphan(  # Task 9
+    runs_root, tmp_path, monkeypatch
+) -> None:
+    """Стоп перепроверки ДО первого `start` (например, фордж недоступен):
+    у engineer `session_id` ещё пуст, но это не сирота — повтор идёт в resume."""
+    env = _LoopEnv(monkeypatch, tmp_path)
+    st = rs.new_run(
+        subject="Fleet Inbox",
+        repo="alpha",
+        repo_slug="owner/alpha",
+        ws_id="ws-eng",
+        target_dir=str(env.target),
+        bundle_dir="workstreams/ws-eng/spec",
+        profile="profiles/team-exp.yaml",
+        run_id="r-eng",
+        merge_authority="human",
+        interview={
+            **iv.InterviewSpec(
+                "engineer", "product owner", "owner/alpha", "upstream.md", "b" * 40
+            ).as_state(),
+            "approval_pr": 7,
+        },
+    )
+    st.status = "stopped_interview"
+    rs.save(st)
+    env.resume_result = st
+    _engineer_ops(monkeypatch)
+    spec_loop.main(
+        _engineer_args("--traces-to", _operator(tmp_path), "--approval-pr", "7")
+    )
+    assert [c[0] for c in env.calls] == ["resume"]
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/runner.py b/governance/runner.py
index d2eadba..42ee704 100644
--- a/governance/runner.py
+++ b/governance/runner.py
@@ -18,6 +18,7 @@ PR человеку (`waiting_human_merge`), S8 не запускается са
 from __future__ import annotations
 
 import argparse
+import contextvars
 import hashlib
 import json
 import os
@@ -36,6 +37,7 @@ from governance import (
     acceptance_guard,
     authority_root,
     brief_input,
+    brief_provenance,
     bundle_dag,
     bundle_inputs,
     charter_guard,
@@ -56,7 +58,6 @@ from governance.frontmatter import split_frontmatter
 from governance.merge_gate import PrFacts
 from governance.ops import (
     _AUTHOR_DSL,
-    ENGINEER_BLOCKED,
     Ops,
     RealOps,
     disp_agent,
@@ -491,14 +492,25 @@ def advance(state: RunState, ops: Ops, *, lock: rl.RunLock) -> RunState:
     if op_status(state, "merge") == "completed":
         _step_s8(state, ops)
         return state
-    for step in _STEPS:
-        if state.status != "running":
-            break
-        if not step(state, ops):
-            break
+    token = _LOCK_FD.set(lock.fd)
+    try:
+        for step in _STEPS:
+            if state.status != "running":
+                break
+            if not step(state, ops):
+                break
+    finally:
+        _LOCK_FD.reset(token)
     return state
 
 
+#: Дескриптор блокировки текущего `advance` (§11.4.5): шаги, вызывающие
+#: соседа, передают его в `pass_fds`, не меняя сигнатур шагов.
+_LOCK_FD: contextvars.ContextVar[int | None] = contextvars.ContextVar(
+    "_LOCK_FD", default=None
+)
+
+
 # --- Волновой режим (спека sequential-node-approval, S9/S10/S13) ----------
 
 
@@ -942,7 +954,11 @@ def resume(run_id: str, ops: Ops, *, lock: rl.RunLock) -> RunState:
         _reconcile_pr_merged_out_of_band(state, ops, lock=lock)
         return state
     if state.status in ("waiting_interview", "stopped_interview"):
-        if state.interview and state.interview.get("session_id") is None:
+        if (
+            state.interview
+            and state.interview.get("frame") != "engineer"
+            and state.interview.get("session_id") is None
+        ):
             # Сирота — координаты стадии Need без сессии: discovery не
             # зовём, оператор присоединяется вручную (спека §5.2).
             print(
@@ -1648,9 +1664,14 @@ def _interview_of(state: RunState) -> dict:
 
 
 def _interview_stop(state: RunState, reason: str) -> bool:
-    """Персистентный стоп стадии Need; координаты и session_id не трогаются."""
+    """Персистентный стоп стадии Need; координаты и session_id не трогаются.
+
+    Причина пишется в `interview.stop_reason` той же записью: у engineer
+    она машинная (`upstream_blob_mismatch: …`, `session_unverifiable: …`).
+    """
     print(f"_step_interview: {reason}")
     state.status = "stopped_interview"
+    _interview_of(state)["stop_reason"] = reason
     save(state)
     return False
 
@@ -1669,15 +1690,6 @@ def _print_answer_hint(state: RunState, reply: iv.DiscoveryReply) -> None:
     )
 
 
-def _upstream_path(state: RunState, spec: iv.InterviewSpec) -> str | None:
-    """Путь upstream-blob'а для engineer-фрейма; `None` для остальных."""
-    if spec.frame != "engineer":
-        return None
-    if spec.traces_to is None:
-        raise ValueError("engineer-фрейм без traces_to: upstream-blob не найти")
-    return str(run_dir(state.run_id) / "brief-input" / "00-discovery" / spec.traces_to)
-
-
 def _interview_poll(
     state: RunState, ops: Ops, spec: iv.InterviewSpec, cwd: str
 ) -> bool:
@@ -1696,7 +1708,10 @@ def _interview_poll(
     session_id = _interview_of(state)["session_id"]
     if op_status(state, INTERVIEW_BRIEF) != "new":
         return _interview_publish(state, ops, spec, cwd)
-    reply = ops.discovery_status(session_id, cwd)
+    guard = _engineer_guard(state, ops, spec)
+    if guard is not None:
+        return _interview_stop(state, guard)
+    reply = ops.discovery_status(session_id, cwd, lock_fd=_LOCK_FD.get())
     return _interview_after_reply(state, ops, spec, cwd, reply, "status")
 
 
@@ -1790,7 +1805,10 @@ def _interview_publish(
     ):
         return _interview_reconcile_published(state, ops, spec, cwd, final)
     tmp.unlink(missing_ok=True)
-    reply = ops.discovery_brief(session_id, str(tmp), cwd)
+    guard = _engineer_guard(state, ops, spec)
+    if guard is not None:
+        return _interview_stop(state, guard)
+    reply = ops.discovery_brief(session_id, str(tmp), cwd, lock_fd=_LOCK_FD.get())
     if reply.code != 0:
         tmp.unlink(missing_ok=True)
         return _interview_after_reply(state, ops, spec, cwd, reply, "brief")
@@ -1805,8 +1823,17 @@ def _interview_publish(
         brief_input.inspect_brief(tmp)
     except brief_input.BriefInputError as exc:
         return _interview_stop(state, f"бриф не проходит inspect_brief: {exc}")
+    # §11.4.3/P4: политика и upstream могли смениться ВО ВРЕМЯ рендера —
+    # перепроверка непосредственно перед необратимой публикацией.
+    guard = _engineer_guard(state, ops, spec)
+    if guard is not None:
+        tmp.unlink(missing_ok=True)
+        return _interview_stop(state, guard)
     os.replace(tmp, final)
     source = brief_input.inspect_brief(final)  # дескриптор — по durable-пути
+    mismatch = _engineer_source_mismatch(spec, source)
+    if mismatch is not None:
+        return _interview_stop(state, mismatch)
     state.brief = source.as_state()
     _interview_of(state)["completed_at"] = datetime.now(UTC).isoformat(
         timespec="seconds"
@@ -1831,7 +1858,12 @@ def _interview_reconcile_published(
     """
     probe = final.with_name(".brief.reconcile.tmp")
     probe.unlink(missing_ok=True)
-    reply = ops.discovery_brief(_interview_of(state)["session_id"], str(probe), cwd)
+    guard = _engineer_guard(state, ops, spec)
+    if guard is not None:
+        return _interview_stop(state, guard)
+    reply = ops.discovery_brief(
+        _interview_of(state)["session_id"], str(probe), cwd, lock_fd=_LOCK_FD.get()
+    )
     try:
         if reply.code != 0:
             return _interview_stop(
@@ -1860,6 +1892,9 @@ def _interview_reconcile_published(
         source = brief_input.inspect_brief(final)
     except brief_input.BriefInputError as exc:
         return _interview_stop(state, f"recovery: inspect_brief: {exc}")
+    guard = _engineer_guard(state, ops, spec) or _engineer_source_mismatch(spec, source)
+    if guard is not None:
+        return _interview_stop(state, guard)
     state.brief = source.as_state()
     _interview_of(state)["completed_at"] = datetime.now(UTC).isoformat(
         timespec="seconds"
@@ -1910,6 +1945,10 @@ def attach_session(
     state = load(run_id)
     if state.interview is None:
         raise ValueError("у прогона нет стадии Need")
+    if state.interview.get("frame") == "engineer":
+        raise ValueError(
+            "--session для engineer запрещён: id записан до вызова (§11.4.5)"
+        )
     if state.interview.get("session_id") is not None:
         raise ValueError("session_id уже записан — замена сессии запрещена")
     if op_status(state, INTERVIEW_START) != "started":
@@ -1919,7 +1958,9 @@ def attach_session(
     spec = iv.InterviewSpec.from_state(state.interview)
     probe = run_dir(run_id) / "brief-input" / ".attach.tmp"
     probe.parent.mkdir(parents=True, exist_ok=True)
-    reply = ops.discovery_brief(session_id, str(probe), str(run_dir(run_id)))
+    reply = ops.discovery_brief(
+        session_id, str(probe), str(run_dir(run_id)), lock_fd=lock.fd
+    )
     # `cmd_brief` соседа пишет артефакт (`write_artifact`) ДО `_emit` при
     # любом коде, кроме отказа загрузки сессии (discovery `cli.py:267-283`);
     # 1/2 — отказ присоединения, отсутствующий файл — тоже отказ, не
@@ -1966,6 +2007,8 @@ def _step_interview(state: RunState, ops: Ops) -> bool:
     # Заход в стадию — единственная точка, через которую проходят все
     # маршруты.
     (run_dir(state.run_id) / INTERVIEW_FINDINGS).unlink(missing_ok=True)
+    if spec.frame == "engineer":
+        return _step_interview_engineer(state, ops, spec, cwd)
     if op_status(state, INTERVIEW_START) != "completed":
         if (
             state.interview.get("session_id") is None
@@ -1982,14 +2025,8 @@ def _step_interview(state: RunState, ops: Ops) -> bool:
             )
             return False
         _ensure_started(state, INTERVIEW_START)
-        if spec.frame == "engineer" and spec.upstream_blob is None:
-            return _interview_stop(state, ENGINEER_BLOCKED)
         reply = ops.discovery_start(
-            spec.frame,
-            spec.target,
-            spec.traces_to,
-            _upstream_path(state, spec),
-            cwd,
+            spec.frame, spec.target, spec.traces_to, None, cwd, lock_fd=_LOCK_FD.get()
         )
         if reply.code != 20:
             return _interview_stop(
@@ -2005,6 +2042,110 @@ def _step_interview(state: RunState, ops: Ops) -> bool:
     return _interview_poll(state, ops, spec, cwd)
 
 
+def _step_interview_engineer(
+    state: RunState, ops: Ops, spec: iv.InterviewSpec, cwd: str
+) -> bool:
+    """§11.4.5: engineer — write-ahead id, перепроверка, `start`/восстановление."""
+    guard = _engineer_guard(state, ops, spec)
+    if guard is not None:
+        return _interview_stop(state, guard)
+    if op_status(state, INTERVIEW_START) == "completed":
+        return _interview_poll(state, ops, spec, cwd)
+    sid = iv.engineer_session_id(state.run_id)
+    if op_status(state, INTERVIEW_START) == "started":
+        return _engineer_recover(state, ops, cwd, sid)
+    # Write-ahead: id и `interview-start: started` — одной записью ДО вызова.
+    _interview_of(state)["session_id"] = sid
+    op_start(state, INTERVIEW_START, session_id=sid)
+    reply = ops.discovery_start(
+        "engineer",
+        spec.target,
+        None,
+        str(run_dir(state.run_id) / iv.UPSTREAM_REL),
+        cwd,
+        session_id=sid,
+        lock_fd=_LOCK_FD.get(),
+    )
+    if reply.code != 20 or reply.envelope["next_action"].get("session_id") != sid:
+        reason = reply.envelope.get("operation", {}).get("reason", "")
+        return _interview_stop(state, f"start вернул {reply.code}: {reason}")
+    state.status = "waiting_interview"
+    op_complete(state, INTERVIEW_START, session_id=sid)
+    _print_answer_hint(state, reply)
+    return False
+
+
+def _engineer_recover(state: RunState, ops: Ops, cwd: str, sid: str) -> bool:
+    """§11.4.5 восстановление: только `status`; повторного `start` нет.
+
+    20/0/10/11 — сессия с нашим id есть, но её upstream через публичный
+    интерфейс не проверить (discovery#63): стоп. Иное — unknown: стоп.
+    """
+    reply = ops.discovery_status(sid, cwd, lock_fd=_LOCK_FD.get())
+    if reply.code in (20, 0, 10, 11):
+        print(
+            "_step_interview: сессия с id прогона существует, но её upstream не "
+            "проверить через публичный интерфейс (discovery#63) — продолжение: "
+            "--new-run --ws-id <fresh-id>"
+        )
+        return _interview_stop(state, f"session_unverifiable: status {reply.code}")
+    print(
+        "_step_interview: состояние сессии неизвестно — проверьте запуск "
+        "discovery и $DISCOVERY_HOME и повторите команду, либо --new-run "
+        "--ws-id <fresh-id>"
+    )
+    reason = reply.envelope.get("operation", {}).get("reason", "")
+    return _interview_stop(state, f"session_state_unknown: {reason}")
+
+
+def _engineer_guard(state: RunState, ops: Ops, spec: iv.InterviewSpec) -> str | None:
+    """§11.4.3–§11.4.5: перед КАЖДЫМ обращением к соседу и перед публикацией.
+
+    Ничего из `run.json`, кроме адреса акта (`approval_pr`) и эталона
+    `upstream_blob`, в решении не участвует: durable-копия читается заново и
+    сверяется с актом, перевыведенным из форджа (§11.1.2). Customer — `None`.
+    """
+    if spec.frame != "engineer":
+        return None
+    interview = _interview_of(state)
+    expected = iv.engineer_session_id(state.run_id)
+    recorded = interview.get("session_id")
+    # Пустой id допустим только ДО write-ahead первого `start`; после — точно
+    # `s-<run_id>-e` (удалённый/обнулённый id не переадресует прогон).
+    if recorded is None and op_status(state, INTERVIEW_START) != "new":
+        return "session_id_mismatch: id удалён после write-ahead"
+    if recorded is not None and recorded != expected:
+        return f"session_id_mismatch: {recorded!r} ≠ {expected!r}"
+    copy = run_dir(state.run_id) / iv.UPSTREAM_REL
+    try:
+        data = copy.read_bytes()
+    except OSError as exc:
+        return f"upstream_blob_mismatch: upstream.md не читается ({exc})"
+    if blob_sha1_bytes(data) != spec.upstream_blob:
+        return "upstream_blob_mismatch: durable upstream.md ≠ upstream_blob"
+    act = brief_provenance.read_act(ops, state.repo_slug, int(interview["approval_pr"]))
+    if isinstance(act, brief_provenance.Refusal):
+        return f"{act.reason}: {act.detail}"
+    for refusal in (
+        brief_provenance.check_operator_brief(act, data.decode("utf-8")),
+        brief_provenance.check_policy(ops, act),
+    ):
+        if isinstance(refusal, brief_provenance.Refusal):
+            return f"{refusal.reason}: {refusal.detail}"
+    return None
+
+
+def _engineer_source_mismatch(
+    spec: iv.InterviewSpec, source: brief_input.BriefSource
+) -> str | None:
+    """Опубликованный source-слой несёт ровно закреплённый upstream (T43)."""
+    if spec.frame != "engineer":
+        return None
+    if dict(source.source_blobs).get("discovery-customer") != spec.upstream_blob:
+        return "upstream_blob_mismatch: source-слой брифа ≠ upstream_blob"
+    return None
+
+
 def _step_branch(state: RunState, ops: Ops) -> bool:
     """S1: ветка `spec/<ws_id>-behaviour`; `ensure_branch` идемпотентен.
```

```diff
diff --git a/governance/ops.py b/governance/ops.py
index 5a4a69e..280c13f 100644
--- a/governance/ops.py
+++ b/governance/ops.py
@@ -35,7 +35,6 @@ from governance.decomposition_guard import DELIVERABLE_KINDS
 from governance.facts import Fact, Outcome, unavailable
 
 DEVTOOLS_ROOT = Path(__file__).resolve().parent.parent
-ENGINEER_BLOCKED = "engineer-маршрут ждёт discovery#49 (приём upstream при start)"
 REVIEW_GH_CONFIG_DIR = Path.home() / ".config" / "review"
 
 _PR_URL_RE = re.compile(r"/pull/(\d+)")
```

```diff
diff --git a/governance/spec_loop.py b/governance/spec_loop.py
index bc01db2..281fd22 100644
--- a/governance/spec_loop.py
+++ b/governance/spec_loop.py
@@ -902,7 +902,11 @@ def _dispatch(state: rs.RunState, ops, lock: run_lock.RunLock) -> int:
             return _deliver_phase(after, ops)
         return _report_state(after)
     if state.status in ("waiting_interview", "stopped_interview"):
-        if state.interview and state.interview.get("session_id") is None:
+        if (
+            state.interview
+            and state.interview.get("frame") != "engineer"
+            and state.interview.get("session_id") is None
+        ):
             # Сирота — координаты стадии Need без записанной сессии.
             # `runner.resume` тоже отказался бы звать discovery, но кнопка
             # проверяет это САМА и не делает вызов вовсе (ruling 2, Task 10):
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_governance_runner.py tests/test_governance_spec_loop.py -q` → 449 passed (проверено на ревизии задачи); мутации: `close_fds=False` в gh-вызове + наследуемый дескриптор — пойманы обоими тестами T38; `check_operator_brief` в перепроверке, перепроверка перед публикацией, сверка blob копии, сверка id, write-ahead, восстановление-без-start, `lock_fd` в `start`, правило «сирота» (раннер и spec-loop) — пойманы (проверено; для правила «сирота» тест выбирается явно — узкий `-k` его не цеплял).

- [ ] **Step 5: Commit** — `feat(need): engineer-стадия — write-ahead id, перепроверки, восстановление (§11.4.3–§11.4.5)`.

---

### Task 10: E1 сверяет `upstream.md` source-слоя с `upstream_blob`

**Files:**
- Modify: `governance/runner.py` (`_step_materialize_brief`)
- Test: `tests/test_governance_runner.py`

- [ ] **Step 1: Тесты** (T43: целый upstream — проходит; подмена после публикации —
`stopped_author`; дескриптор ≠ `upstream_blob` — `upstream_blob_mismatch`; плюс матрица
engineer-стадии из ревью части B (B5): T28 — цепочка подтверждений после второго, T29 —
подделанное локальное подтверждение не маскирует дрейф, T30 — адрес акта в леджере
перечитывается из форджа, подтверждение удалено ИЛИ отредактировано после продолжения —
стоп дрейфа (мутация «правка не учитывается» поймана), T35 — смена политики
после `completed_at` не стопит (Q2), T41 — `status` без конверта — жёсткий стоп):

```diff
diff --git a/tests/test_governance_runner.py b/tests/test_governance_runner.py
index 5e6a95d..7ab0d3f 100644
--- a/tests/test_governance_runner.py
+++ b/tests/test_governance_runner.py
@@ -9961,3 +9961,177 @@ def test_git_and_gh_children_do_not_hold_the_lock(  # T38 (git/gh-потомки
         subprocess.run([str(bin_dir / "git")], pass_fds=(lock.fd,), check=True)
     lines = out.read_text(encoding="utf-8").splitlines()
     assert lines == ["git:", "gh:", f"git:{lock.fd}"]
+
+
+# --- §11.4.4 в E1: source-слой несёт закреплённый upstream (Task 10, часть B) ---
+
+
+def _published_engineer(tmp_path, monkeypatch, run_id):
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        run_id,
+        [
+            ("start", _eng_reply(20, run_id)),
+            ("status", _eng_reply(0, run_id)),
+            ("brief", _eng_reply(0, run_id)),
+        ],
+    )
+    return ops, locked_runner.resume(run_id, ops)
+
+
+def _findings(run_id: str) -> str:
+    path = rs.run_dir(run_id) / "brief-findings.txt"
+    return path.read_text(encoding="utf-8") if path.exists() else ""
+
+
+def _rerun_e1(run_id: str, ops) -> object:
+    st = rs.load(run_id)
+    st.status = "running"
+    st.ops.pop(runner.wave_key(st, "materialize-brief"), None)
+    rs.save(st)
+    return locked_runner.resume(run_id, ops)
+
+
+def test_e1_proceeds_with_intact_upstream(
+    tmp_path, runs_root, monkeypatch
+) -> None:  # T43
+    ops, state = _published_engineer(tmp_path, monkeypatch, "r-f1")
+    assert state.interview["completed_at"]
+    assert "upstream_blob_mismatch" not in _findings("r-f1")
+    _rerun_e1("r-f1", ops)
+    assert "upstream_blob_mismatch" not in _findings("r-f1")
+
+
+def test_e1_refuses_tampered_upstream_after_publication(  # T43 двойник
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _published_engineer(tmp_path, monkeypatch, "r-f2")
+    (rs.run_dir("r-f2") / iv.UPSTREAM_REL).write_text("подмена", encoding="utf-8")
+    after = _rerun_e1("r-f2", ops)
+    assert after.status == "stopped_author"
+    assert "GC-BRIEF-SOURCE" in _findings("r-f2")
+
+
+def test_e1_refuses_descriptor_not_matching_upstream_blob(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _published_engineer(tmp_path, monkeypatch, "r-f3")
+    st = rs.load("r-f3")
+    st.interview["upstream_blob"] = "0" * 40
+    rs.save(st)
+    after = _rerun_e1("r-f3", ops)
+    assert after.status == "stopped_author"
+    assert "upstream_blob_mismatch" in _findings("r-f3")
+
+
+# --- ревью части B, круг 1 (B5): матрица engineer-стадии ---
+
+
+def test_reconfirm_sequence_continues_after_second_reconfirm(  # T28 полностью
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-g1",
+        [
+            ("start", _eng_reply(20, "r-g1")),
+            ("status", _eng_reply(20, "r-g1")),
+            ("status", _eng_reply(20, "r-g1")),
+        ],
+    )
+    ops.forge.add_policy(C1)
+    assert locked_runner.resume("r-g1", ops).status == "stopped_interview"
+    ops.forge.reconfirm(C1, created="2026-10-09T00:00:00Z")
+    assert locked_runner.resume("r-g1", ops).status == "waiting_interview"
+    ops.forge.add_policy(C2)
+    assert locked_runner.resume("r-g1", ops).status == "stopped_interview"
+    ops.forge.reconfirm(C2, created="2026-10-10T00:00:00Z")
+    assert locked_runner.resume("r-g1", ops).status == "waiting_interview"
+
+
+def test_forged_local_confirmation_does_not_mask_drift(  # T29
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-g2", [("start", _eng_reply(20, "r-g2"))]
+    )
+    ops.forge.add_policy(C1)
+    st = rs.load("r-g2")
+    st.interview["approval"]["act_policy_sha"] = C1
+    st.interview["working_policy"] = C1
+    st.ops["upstream-policy-reconfirm"] = {"status": "completed", "to": C1}
+    rs.save(st)
+    calls = len(ops.discovery_calls)
+    assert locked_runner.resume("r-g2", ops).status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+    assert _stop_reason("r-g2").startswith("upstream_policy_drift")
+
+
+def test_act_address_changed_in_ledger_is_rechecked_from_forge(  # T30
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, _ = _engineer_run(
+        tmp_path, monkeypatch, "r-g3", [("start", _eng_reply(20, "r-g3"))]
+    )
+    st = rs.load("r-g3")
+    st.interview["approval_pr"] = 99  # такого brief-PR нет
+    rs.save(st)
+    calls = len(ops.discovery_calls)
+    assert locked_runner.resume("r-g3", ops).status == "stopped_interview"
+    assert len(ops.discovery_calls) == calls
+
+
+@pytest.mark.parametrize("how", ["deleted", "edited"])
+def test_reconfirm_deleted_or_edited_on_resume_stops(
+    tmp_path, runs_root, monkeypatch, how
+) -> None:  # T30
+    import dataclasses
+
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-g4",
+        [("start", _eng_reply(20, "r-g4")), ("status", _eng_reply(20, "r-g4"))],
+    )
+    ops.forge.add_policy(C1)
+    ops.forge.reconfirm(C1)
+    assert locked_runner.resume("r-g4", ops).status == "waiting_interview"
+    if how == "deleted":
+        ops.forge.comments[7] = []
+    else:  # отредактирован после продолжения — больше не считается
+        ops.forge.comments[7] = [
+            dataclasses.replace(c, last_edited_at="2026-10-09T02:00:00Z")
+            for c in ops.forge.comments[7]
+        ]
+    assert locked_runner.resume("r-g4", ops).status == "stopped_interview"
+    assert _stop_reason("r-g4").startswith("upstream_policy_drift")
+
+
+def test_policy_change_after_completed_at_does_not_stop(  # T35 (Q2)
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    ops, state = _published_engineer(tmp_path, monkeypatch, "r-g5")
+    assert state.interview["completed_at"]
+    ops.forge.add_policy(C1)
+    after = _rerun_e1("r-g5", ops)
+    assert after.status != "stopped_interview"
+    assert "upstream_blob_mismatch" not in _findings("r-g5")
+
+
+def test_recovery_status_without_envelope_is_hard_stop(  # T41
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    no_envelope = iv.parse_reply(2, "", "")  # argparse-ошибка соседа без stdout
+    assert no_envelope.code == 1
+    ops, _ = _engineer_run(
+        tmp_path,
+        monkeypatch,
+        "r-g6",
+        [("start", _eng_reply(1, "r-g6")), ("status", no_envelope)],
+    )
+    state = locked_runner.resume("r-g6", ops)
+    assert state.status == "stopped_interview"
+    assert [c[0] for c in ops.discovery_calls] == ["start", "status"]
+    assert _stop_reason("r-g6").startswith("session_state_unknown")
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/runner.py b/governance/runner.py
index 42ee704..1fb5639 100644
--- a/governance/runner.py
+++ b/governance/runner.py
@@ -2229,6 +2229,15 @@ def _step_materialize_brief(state: RunState, ops: Ops) -> bool:
             state,
             "durable intake bytes не совпадают с descriptor в run.json",
         )
+    interview = state.interview or {}
+    if interview.get("frame") == "engineer" and dict(source.source_blobs).get(
+        "discovery-customer"
+    ) != interview.get("upstream_blob"):
+        # §11.4.4: source-слой несёт ровно закреплённый upstream, и после
+        # публикации тоже (Q2: политика больше не перепроверяется, байты — да).
+        return _brief_stop(
+            state, "upstream_blob_mismatch: upstream.md source-слоя ≠ upstream_blob"
+        )
 
     if op_status(state, key) == "completed":
         try:
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_governance_runner.py -q -k "e1_ or upstream or reconfirm or policy or recovery or forged or act_address"` → 28 passed; мутация сверки — поймана (проверено).

- [ ] **Step 5: Commit** — `feat(need): E1 сверяет upstream.md source-слоя с upstream_blob (§11.4.4)`.

---

### Task 11: `brief-propose`

**Files:**
- Create: `governance/brief_tools.py` (`propose`, CLI), `tests/test_brief_tools.py`
- Modify: `governance/ops.py` (`create_pr(..., base=None)`), `Makefile`

**Interfaces:**
- Produces: `brief_branch(ws_id)`, `proposal_dir(state)`, `expected_request(state,
  brief_text, policy_sha)`, `propose(run_id, ops) -> int`, `BriefToolError(retry)`.

- [ ] **Step 1: Тесты** (T47 — каждое окно §11.3 п.4, в т.ч. открытый PR без дрейфа (без
предупреждения) и с дрейфом (пин сохранён, дрейф сообщён); T48 — одна таблица форм чужого
содержимого (`run_id`, `ws_id`, `policy.repo|ref|path`, `purpose`, байты брифа, нет заявки,
пин — не версия, лишний файл) прогоняется по ВСЕМ трём путям восстановления — смерженный
PR, открытый PR, ветка без PR — с проверкой причины отказа и отсутствием commit/push/PR;
двойник — нетронутое содержимое на каждом пути; T49 — предусловия; недоступность —
retry; грязное дерево — отказ до git; T21b — общий набор дефектов заявки у найденного PR;
B3: состав изменённых путей сверяется ДО push (лишний путь, например от pre-commit, —
отказ без push); compare и восстановление — по SHA проверенной головы, не по имени ветки;
голова созданного/найденного PR обязана совпасть с проверенной; сбой push или создания PR —
retry, повтор досоздаёт только PR без нового коммита):

```diff
diff --git a/tests/test_brief_tools.py b/tests/test_brief_tools.py
new file mode 100644
index 0000000..6463f5c
--- /dev/null
+++ b/tests/test_brief_tools.py
@@ -0,0 +1,493 @@
+"""Обёртки brief-маршрута (§11.3, §11.5, §11.3 п.6) на стенде форджа."""
+
+from __future__ import annotations
+
+from dataclasses import dataclass, field, replace
+from pathlib import Path
+
+import pytest
+
+from governance import approval_request as ar
+from governance import brief_tools as bt
+from governance import interview as iv
+from governance import run_state as rs
+from governance.brief_facts import BriefPrFacts
+from governance.facts import Fact, Outcome, unavailable
+from governance.stale_adapter import blob_sha1_bytes
+from tests.forge_fake import (
+    BASE,
+    C1,
+    DIR,
+    DRAFT,
+    HEAD,
+    MERGE,
+    REPO,
+    FakeForge,
+    P,
+    consistent_world,
+    request,
+)
+
+RUN_ID = "WS-1-abc123"
+BRANCH = "brief/WS-1"
+FILES = ((f"{DIR}/brief.md", "added"), (f"{DIR}/{ar.FILE_NAME}", "added"))
+
+
+@dataclass
+class ToolOps(FakeForge):
+    """Стенд форджа + git-эффекты обёрток (журнал `calls`)."""
+
+    calls: list[tuple] = field(default_factory=list)
+    remote_heads: dict[str, str] = field(default_factory=dict)
+    dirty: bool = False
+    next_pr: int = 42
+    push_error: str | None = None
+
+    def remote_branch_head_fact(self, repo_slug: str, branch: str) -> Fact[str]:
+        if "remote" in self.unavailable_facts:
+            return unavailable("remote")
+        sha = self.remote_heads.get(branch)
+        if sha is None:
+            return Fact(Outcome.ABSENT, None, "нет ветки")
+        return Fact(Outcome.FOUND, sha, "ветка")
+
+    def is_dirty(self, target_dir: str) -> bool:
+        return self.dirty
+
+    def fetch_branch(self, target_dir: str, branch: str) -> bool:
+        self.calls.append(("fetch_branch", branch))
+        return True
+
+    def switch_to(self, target_dir: str, branch: str, start_point: str) -> None:
+        self.calls.append(("switch_to", branch, start_point))
+
+    def commit_paths(self, target_dir, paths, message, force_paths=()) -> None:
+        self.calls.append(("commit_paths", tuple(paths)))
+        root = Path(target_dir)
+        for path in paths:
+            self.files[(HEAD, path)] = (root / path).read_text(encoding="utf-8")
+
+    create_error: str | None = None
+    #: Голова, с которой форджа откроет PR (сдвиг ветки между проверкой и PR).
+    pr_head: str | None = None
+
+    #: Пути, которые локальный коммит меняет сверх предложения (хук и т.п.).
+    extra_changed: tuple[str, ...] = ()
+
+    def changed_paths(self, target_dir: str, base_branch: str):
+        committed = next(c for c in reversed(self.calls) if c[0] == "commit_paths")
+        return BASE, [*committed[1], *self.extra_changed]
+
+    def head_sha(self, target_dir: str, branch: str) -> str:
+        return HEAD
+
+    def push_branch(self, target_dir: str, branch: str) -> None:
+        if self.push_error:
+            raise RuntimeError(self.push_error)
+        self.calls.append(("push_branch", branch))
+        self.remote_heads[branch] = HEAD
+        self.compare[(BASE, branch)] = FILES
+
+    def create_pr(
+        self,
+        target_dir,
+        repo_slug,
+        branch,
+        title,
+        body,
+        label,
+        *,
+        draft=False,
+        base=None,
+    ) -> int:
+        if self.create_error:
+            raise RuntimeError(self.create_error)
+        self.calls.append(("create_pr", branch, label, base, body))
+        head = self.pr_head or self.remote_heads.get(branch, HEAD)
+        self.prs[self.next_pr] = BriefPrFacts(
+            self.next_pr, "OPEN", base or "", branch, head, FILES, None, None, None
+        )
+        return self.next_pr
+
+
+@pytest.fixture()
+def runs_root(tmp_path, monkeypatch) -> Path:
+    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
+    return tmp_path / "runs"
+
+
+def _brief_ready_run(
+    tmp_path: Path, status: str = "brief_ready", text: str = DRAFT
+) -> None:
+    target = tmp_path / "target"
+    target.mkdir(exist_ok=True)
+    state = rs.new_run(
+        subject="s",
+        repo="alpha",
+        repo_slug=REPO,
+        ws_id="WS-1",
+        target_dir=str(target),
+        bundle_dir="workstreams/WS-1/spec",
+        profile="profiles/team-exp.yaml",
+        run_id=RUN_ID,
+        merge_authority="human",
+        interview={
+            **iv.InterviewSpec(
+                "customer", "po", REPO, None, None, brief_only=True
+            ).as_state(),
+            "session_id": "s-1",
+            "brief_blob": blob_sha1_bytes(text.encode()),
+        },
+    )
+    state.status = status
+    brief = rs.run_dir(RUN_ID) / iv.BRIEF_REL
+    brief.parent.mkdir(parents=True, exist_ok=True)
+    brief.write_text(text, encoding="utf-8")
+    rs.save(state)
+
+
+def _ops(monkeypatch, *, with_pr: bool = False) -> ToolOps:
+    world = consistent_world(monkeypatch)
+    ops = ToolOps()
+    ops.files.update(world.files)
+    ops.policy_versions.update(world.policy_versions)
+    ops.policy_head = world.policy_head
+    if with_pr:
+        ops.prs.update(world.prs)
+    return ops
+
+
+# --- §11.3: brief-propose (Task 11) ---
+
+
+def test_nothing_exists_creates_two_file_pr(
+    tmp_path, runs_root, monkeypatch
+) -> None:  # T47
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    assert bt.propose(RUN_ID, ops) == 42
+    assert ("switch_to", BRANCH, BASE) in ops.calls  # от immutable SHA базы форджа
+    commit = next(c for c in ops.calls if c[0] == "commit_paths")
+    assert sorted(commit[1]) == sorted(p for p, _ in FILES)
+    pr = next(c for c in ops.calls if c[0] == "create_pr")
+    assert pr[1:4] == (BRANCH, "human-merge-required", "main")
+    assert f"policy: andrei-shtanakov/approval-policy@{P}" in pr[4]
+    written = ar.parse(ops.files[(HEAD, f"{DIR}/{ar.FILE_NAME}")])
+    assert written == request()
+
+
+def test_merged_pr_with_our_content_is_reported(
+    tmp_path, runs_root, monkeypatch, capsys
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    assert bt.propose(RUN_ID, ops) == 7
+    assert not any(c[0] in ("create_pr", "push_branch") for c in ops.calls)
+    assert "уже смержен" in capsys.readouterr().out
+
+
+def test_open_pr_with_drifted_pin_keeps_pin_and_warns(
+    tmp_path, runs_root, monkeypatch, capsys
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.prs[7] = replace(
+        ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
+    )
+    ops.add_policy(C1)  # актуальная версия ушла вперёд
+    assert bt.propose(RUN_ID, ops) == 7
+    out = capsys.readouterr().out
+    assert "открыт" in out and "--repropose" in out
+    assert not any(c[0] == "create_pr" for c in ops.calls)
+
+
+def test_open_pr_without_drift_is_reported_without_warning(  # T47
+    tmp_path, runs_root, monkeypatch, capsys
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.prs[7] = replace(
+        ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
+    )
+    assert bt.propose(RUN_ID, ops) == 7
+    out = capsys.readouterr().out
+    assert "открыт" in out and "ВНИМАНИЕ" not in out
+    assert not any(c[0] in ("create_pr", "push_branch") for c in ops.calls)
+
+
+def test_closed_pr_refuses_with_repropose_hint(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.prs[7] = replace(
+        ops.prs[7], state="CLOSED", merged_by=None, merged_at=None, merge_commit=None
+    )
+    with pytest.raises(bt.BriefToolError, match="--repropose"):
+        bt.propose(RUN_ID, ops)
+
+
+def test_branch_without_pr_creates_only_pr(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.remote_heads[BRANCH] = HEAD
+    ops.compare[(BASE, HEAD)] = FILES
+    assert bt.propose(RUN_ID, ops) == 42
+    assert not any(c[0] in ("commit_paths", "push_branch") for c in ops.calls)
+
+
+def test_branch_without_pr_after_base_moved_is_still_ours(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.default_branch = replace(ops.default_branch, sha="f2" * 20)  # база ушла вперёд
+    ops.remote_heads[BRANCH] = HEAD
+    ops.compare[("f2" * 20, HEAD)] = FILES  # compare судит от merge-base
+    assert bt.propose(RUN_ID, ops) == 42
+
+
+def test_branch_with_foreign_files_refuses(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.remote_heads[BRANCH] = HEAD
+    ops.compare[(BASE, HEAD)] = FILES + (("x.txt", "added"),)
+    with pytest.raises(bt.BriefToolError, match="чужое"):
+        bt.propose(RUN_ID, ops)
+
+
+def test_foreign_file_in_base_refuses(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.files[(BASE, f"{DIR}/brief.md")] = "чужой бриф"
+    with pytest.raises(bt.BriefToolError, match="чужой"):
+        bt.propose(RUN_ID, ops)
+    assert not any(c[0] == "switch_to" for c in ops.calls)
+
+
+def test_two_prs_on_branch_is_ambiguous(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.prs[8] = ops.prs[7]
+    with pytest.raises(bt.BriefToolError, match="неоднозначно"):
+        bt.propose(RUN_ID, ops)
+
+
+_REQ = f"{DIR}/{ar.FILE_NAME}"
+_BRIEF = f"{DIR}/brief.md"
+
+
+def _request_at(**changes):
+    return lambda ops, sha: ops.files.__setitem__(
+        (sha, _REQ), ar.render(request(**changes))
+    )
+
+
+def _extra_file(ops, sha) -> None:
+    if 7 in ops.prs:
+        ops.set_pr(files=FILES + (("x.txt", "added"),))
+    else:
+        ops.compare[(BASE, HEAD)] = FILES + (("x.txt", "added"),)
+
+
+#: (id, порча содержимого предложения по его SHA, фраза отказа) — T48.
+_FOREIGN = [
+    ("run_id", _request_at(run_id="WS-1-other"), "не про этот прогон"),
+    ("ws_id", _request_at(ws_id="WS-2"), "не про этот прогон"),
+    ("policy_repo", _request_at(policy_repo="o/other"), "не про этот прогон"),
+    ("policy_ref", _request_at(policy_ref="dev"), "не про этот прогон"),
+    ("policy_path", _request_at(policy_path="x.env"), "не про этот прогон"),
+    (
+        "purpose",
+        lambda ops, sha: ops.files.__setitem__(
+            (sha, _REQ),
+            ops.files[(sha, _REQ)].replace("discovery-brief-approval\n", "x\n", 1),
+        ),
+        "заявка найденного предложения",
+    ),
+    (
+        "brief_bytes",
+        lambda ops, sha: ops.files.__setitem__((sha, _BRIEF), DRAFT + "\n"),
+        "≠ брифу прогона",
+    ),
+    ("no_request", lambda ops, sha: ops.files.pop((sha, _REQ)), "без брифа или заявки"),
+    (
+        "not_a_version",
+        lambda ops, sha: ops.policy_versions.__setitem__(P, False),
+        "не версия",
+    ),
+    ("extra_file", _extra_file, "чужое"),
+]
+#: Пути восстановления §11.3 п.4: (id, SHA, где лежит предложение).
+_PATHS = ["merged-pr", "open-pr", "branch-without-pr"]
+
+
+def _proposal_world(monkeypatch, path: str) -> tuple[ToolOps, str]:
+    if path == "branch-without-pr":
+        ops = _ops(monkeypatch)
+        ops.remote_heads[BRANCH] = HEAD
+        ops.compare[(BASE, HEAD)] = FILES
+        return ops, HEAD
+    ops = _ops(monkeypatch, with_pr=True)
+    if path == "open-pr":
+        ops.prs[7] = replace(
+            ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
+        )
+        return ops, HEAD
+    return ops, MERGE
+
+
+@pytest.mark.parametrize("path", _PATHS)
+def test_found_proposal_with_our_content_is_ours(  # T48 двойник
+    tmp_path, runs_root, monkeypatch, path
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops, _ = _proposal_world(monkeypatch, path)
+    assert bt.propose(RUN_ID, ops) == (42 if path == "branch-without-pr" else 7)
+    assert not any(c[0] in ("commit_paths", "push_branch") for c in ops.calls)
+
+
+@pytest.mark.parametrize("path", _PATHS)
+@pytest.mark.parametrize(
+    ("spoil", "phrase"),
+    [(f, p) for _, f, p in _FOREIGN],
+    ids=[i for i, _, _ in _FOREIGN],
+)
+def test_found_proposal_with_foreign_content_refuses(  # T48 (каждое поле — пара)
+    tmp_path, runs_root, monkeypatch, path, spoil, phrase
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops, sha = _proposal_world(monkeypatch, path)
+    spoil(ops, sha)
+    with pytest.raises(bt.BriefToolError, match=phrase):
+        bt.propose(RUN_ID, ops)
+    effects = ("create_pr", "commit_paths", "push_branch")
+    assert not any(c[0] in effects for c in ops.calls)
+
+
+@pytest.mark.parametrize("bad", ["not_ready", "approved", "blob"])
+def test_run_preconditions(tmp_path, runs_root, monkeypatch, bad) -> None:  # T49
+    if bad == "not_ready":
+        _brief_ready_run(tmp_path, status="waiting_interview")
+    elif bad == "approved":
+        from tests.forge_fake import SIGNED
+
+        _brief_ready_run(tmp_path, text=SIGNED)
+    else:
+        _brief_ready_run(tmp_path)
+        (rs.run_dir(RUN_ID) / iv.BRIEF_REL).write_text(DRAFT + "\n", encoding="utf-8")
+    with pytest.raises(bt.BriefToolError):
+        bt.propose(RUN_ID, _ops(monkeypatch))
+
+
+def test_forge_unavailable_is_retry(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.unavailable_facts.add("find")
+    with pytest.raises(bt.BriefToolError) as exc:
+        bt.propose(RUN_ID, ops)
+    assert exc.value.retry
+
+
+def test_dirty_target_refuses_before_git(tmp_path, runs_root, monkeypatch) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.dirty = True
+    with pytest.raises(bt.BriefToolError, match="грязный"):
+        bt.propose(RUN_ID, ops)
+    assert not any(c[0] == "switch_to" for c in ops.calls)
+
+
+# --- ревью части B, круг 1: B3 и окна сбоев (T47/T48) ---
+
+from tests.approval_request_cases import DEFECTS  # noqa: E402
+
+
+def test_branch_moved_between_check_and_pr_is_not_success(  # B3
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.remote_heads[BRANCH] = HEAD
+    ops.compare[(BASE, HEAD)] = FILES
+    ops.pr_head = "f0" * 20  # ветку сдвинули на тот же набор путей
+    with pytest.raises(bt.BriefToolError, match="НЕ годно"):
+        bt.propose(RUN_ID, ops)
+
+
+def test_recovery_compares_files_at_the_checked_sha(  # B3
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.remote_heads[BRANCH] = HEAD
+    ops.compare[(BASE, BRANCH)] = FILES  # по имени ветки — не годится
+    with pytest.raises(bt.BriefToolError):  # compare по SHA HEAD не задан → retry
+        bt.propose(RUN_ID, ops)
+    ops.compare[(BASE, HEAD)] = FILES
+    assert bt.propose(RUN_ID, ops) == 42
+
+
+def test_push_failure_is_retry_and_rerun_recovers(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.push_error = "rejected: fetch first"
+    with pytest.raises(bt.BriefToolError) as exc:
+        bt.propose(RUN_ID, ops)
+    assert exc.value.retry and not any(c[0] == "create_pr" for c in ops.calls)
+    ops.push_error = None
+    assert bt.propose(RUN_ID, ops) == 42
+
+
+def test_create_pr_failure_then_rerun_creates_only_pr(
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.create_error = "HTTP 502"
+    with pytest.raises(bt.BriefToolError) as exc:
+        bt.propose(RUN_ID, ops)
+    assert exc.value.retry and BRANCH in ops.remote_heads
+    ops.create_error = None
+    ops.compare[(BASE, HEAD)] = FILES
+    commits = sum(1 for c in ops.calls if c[0] == "commit_paths")
+    assert bt.propose(RUN_ID, ops) == 42
+    assert (
+        sum(1 for c in ops.calls if c[0] == "commit_paths") == commits
+    )  # без нового коммита
+
+
+@pytest.mark.parametrize("field", ["policy_ref", "policy_path"])
+def test_found_pr_with_foreign_policy_coordinates_refuses(  # T48 (поля ref/path)
+    tmp_path, runs_root, monkeypatch, field
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    value = "dev" if field == "policy_ref" else "x.env"
+    ops.files[(MERGE, f"{DIR}/{ar.FILE_NAME}")] = ar.render(request(**{field: value}))
+    with pytest.raises(bt.BriefToolError):
+        bt.propose(RUN_ID, ops)
+
+
+@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
+def test_found_pr_with_ambiguous_request_refuses(  # T21b через propose
+    tmp_path, runs_root, monkeypatch, case, mutate, _m
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.files[(MERGE, f"{DIR}/{ar.FILE_NAME}")] = mutate(ar.render(request()))
+    with pytest.raises(bt.BriefToolError):
+        bt.propose(RUN_ID, ops)
+
+
+def test_local_commit_with_extra_path_refuses_before_push(  # B3 (до push)
+    tmp_path, runs_root, monkeypatch
+) -> None:
+    _brief_ready_run(tmp_path)
+    ops = _ops(monkeypatch)
+    ops.extra_changed = (".pre-commit-generated",)
+    with pytest.raises(bt.BriefToolError, match="не предложение"):
+        bt.propose(RUN_ID, ops)
+    assert not any(c[0] == "push_branch" for c in ops.calls)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/brief_tools.py b/governance/brief_tools.py
new file mode 100644
index 0000000..18e73a8
--- /dev/null
+++ b/governance/brief_tools.py
@@ -0,0 +1,316 @@
+"""Обёртки brief-маршрута (спека need-stage §11.3, §11.5, §11.3 п.6).
+
+`propose` — brief-PR (бриф + заявка на одобрение) из customer-прогона в
+`brief_ready`; `approve` — зеркало человеческого мержа через
+`discovery approve`; `check-merge` — проверка заявки brief-PR перед
+человеческим мержем (`human-merge.sh`). Ни у одной обёртки нет леджера:
+состояние предложения — в фордже, повтор восстанавливается его чтением.
+
+CLI: `python -m governance.brief_tools {propose,approve,check-merge} …`;
+коды: 0 — результат есть; 1 — отказ; 2 — факт не установлен (повторите);
+3 — отказ проверки заявки (`check-merge`).
+"""
+
+from __future__ import annotations
+
+import argparse
+import subprocess
+import sys
+from pathlib import Path
+
+from governance import (
+    approval_facts,
+    approval_request,
+    brief_input,
+    discovery_approval,
+    policy_rule,
+)
+from governance import interview as iv
+from governance import run_lock as rl
+from governance import run_state as rs
+from governance.approval_request import ApprovalRequest
+from governance.facts import Outcome
+from governance.stale_adapter import blob_sha1_bytes
+
+LABEL = "human-merge-required"
+APPROVED_REL = "brief-approval/customer-brief.md"
+
+
+class BriefToolError(RuntimeError):
+    """Отказ обёртки; `retry` — факт форджа не установлен."""
+
+    def __init__(self, message: str, *, retry: bool = False) -> None:
+        super().__init__(message)
+        self.retry = retry
+
+
+def brief_branch(ws_id: str) -> str:
+    """Ветка brief-PR (§11.3 п.2)."""
+    return f"brief/{ws_id}"
+
+
+def proposal_dir(state: rs.RunState) -> str:
+    """Каталог предложения в репо цели: `<bundle_dir>/00-discovery`."""
+    return f"{state.bundle_dir}/00-discovery"
+
+
+def expected_request(
+    state: rs.RunState, brief_text: str, policy_sha: str
+) -> ApprovalRequest:
+    """Заявка этого прогона с пином `policy_sha` (§11.3 п.3)."""
+    repo, ref, path = policy_rule.policy_source()
+    return ApprovalRequest(
+        brief_self_hash=discovery_approval.self_hash(brief_text),
+        policy_repo=repo,
+        policy_ref=ref,
+        policy_path=path,
+        policy_sha=policy_sha,
+        run_id=state.run_id,
+        ws_id=state.ws_id,
+    )
+
+
+def _fact(fact, what: str):
+    if fact.outcome is Outcome.UNAVAILABLE:
+        raise BriefToolError(f"{what}: {fact.detail} — повторите", retry=True)
+    return fact
+
+
+def _current_policy(ops) -> str:
+    snapshot = approval_facts.policy_snapshot(ops, pinned_sha=None)
+    _fact(snapshot, "политика подписи")
+    if snapshot.outcome is not Outcome.FOUND:
+        raise BriefToolError(f"политика подписи не установлена: {snapshot.detail}")
+    return snapshot.value.sha
+
+
+def _ready_brief(state: rs.RunState) -> tuple[bytes, str]:
+    """§11.3 п.1: `brief_ready`, blob совпадает, бриф проходит E1, `status: draft`."""
+    if state.status != "brief_ready":
+        raise BriefToolError(
+            f"прогон {state.run_id} в статусе {state.status}, нужен brief_ready "
+            "(customer --brief-only)"
+        )
+    path = rs.run_dir(state.run_id) / iv.BRIEF_REL
+    data = path.read_bytes()
+    if blob_sha1_bytes(data) != (state.interview or {}).get("brief_blob"):
+        raise BriefToolError(
+            "brief.md прогона ≠ brief_blob — бриф изменён после публикации"
+        )
+    try:
+        brief_input.inspect_brief(path)
+    except brief_input.BriefInputError as exc:
+        raise BriefToolError(f"бриф не проходит inspect_brief: {exc}") from exc
+    text = data.decode("utf-8")
+    if discovery_approval.verify(text) != discovery_approval.DEBT_STATUS:
+        raise BriefToolError("бриф уже несёт подпись — предлагать нечего")
+    return data, text
+
+
+def _matches(
+    ops, state: rs.RunState, sha: str, files, brief_text: str, dir_: str
+) -> str:
+    """Найденное предложение — НАШЕ (§11.3 п.4); вернуть его исходный пин политики.
+
+    Список файлов, байты брифа и ВСЕ поля заявки, кроме `policy.sha`,
+    сравниваются с ожидаемыми; `policy.sha` — версия источника политики.
+    """
+    if set(files) != _expected_files(dir_) or len(files) != 2:
+        raise BriefToolError(f"найденное предложение меняет {list(files)} — чужое")
+    brief = _fact(
+        ops.repo_file_fact(state.repo_slug, sha, f"{dir_}/{approval_request.BRIEF}"),
+        "бриф предложения",
+    )
+    req_text = _fact(
+        ops.repo_file_fact(
+            state.repo_slug, sha, f"{dir_}/{approval_request.FILE_NAME}"
+        ),
+        "заявка предложения",
+    )
+    if brief.outcome is not Outcome.FOUND or req_text.outcome is not Outcome.FOUND:
+        raise BriefToolError(f"предложение @{sha} без брифа или заявки — чужое")
+    if brief.value != brief_text:
+        raise BriefToolError("бриф найденного предложения ≠ брифу прогона — чужое")
+    try:
+        req = approval_request.parse(req_text.value)
+    except approval_request.RequestError as exc:
+        raise BriefToolError(f"заявка найденного предложения: {exc}") from exc
+    if req != expected_request(state, brief_text, req.policy_sha):
+        raise BriefToolError("заявка найденного предложения не про этот прогон — чужое")
+    repo, ref, path = policy_rule.policy_source()
+    version = _fact(ops.policy_version_fact_at(repo, ref, path, req.policy_sha), "пин")
+    if version.value is not True:
+        raise BriefToolError(f"пин предложения {req.policy_sha} — не версия политики")
+    return req.policy_sha
+
+
+def _existing_pr(ops, state, number: int, brief_text: str, dir_: str) -> int:
+    facts = _fact(ops.brief_pr_fact(state.repo_slug, number), f"PR #{number}").value
+    sha = facts.merge_commit if facts.state == "MERGED" else facts.head_sha
+    pin = _matches(ops, state, sha, facts.files, brief_text, dir_)
+    if facts.state == "CLOSED":
+        raise BriefToolError(
+            f"brief-PR #{number} закрыт без мержа — новое предложение: --repropose "
+            "(вне E2)"
+        )
+    if facts.state == "MERGED":
+        print(
+            f"brief-PR #{number} уже смержен — следующий шаг: "
+            f"make brief-approve RUN={state.run_id} PR={number}"
+        )
+        return number
+    print(f"brief-PR #{number} открыт")
+    if pin != _current_policy(ops):
+        print(
+            f"ВНИМАНИЕ: пин политики заявки {pin} ≠ актуальной версии — "
+            "human-merge откажет до мержа; нужен --repropose (вне E2)"
+        )
+    return number
+
+
+def _expected_files(dir_: str) -> set[tuple[str, str]]:
+    return {
+        (f"{dir_}/{approval_request.BRIEF}", "added"),
+        (f"{dir_}/{approval_request.FILE_NAME}", "added"),
+    }
+
+
+def _create(ops, state, branch: str, data: bytes, brief_text: str, dir_: str) -> int:
+    default = _fact(
+        ops.default_branch_fact(state.repo_slug), "ветка по умолчанию"
+    ).value
+    for name in (approval_request.BRIEF, approval_request.FILE_NAME):
+        present = _fact(
+            ops.repo_file_fact(state.repo_slug, default.sha, f"{dir_}/{name}"),
+            f"{dir_}/{name} в базе",
+        )
+        if present.outcome is Outcome.FOUND:
+            raise BriefToolError(f"в базе уже есть {dir_}/{name} — чужой бриф")
+    target = Path(state.target_dir)
+    if ops.is_dirty(state.target_dir):
+        raise BriefToolError(f"{target} грязный — предложение не создаётся")
+    request = expected_request(state, brief_text, _current_policy(ops))
+    ops.fetch_branch(state.target_dir, default.name)
+    ops.switch_to(state.target_dir, branch, default.sha)
+    paths = [f"{dir_}/{approval_request.BRIEF}", f"{dir_}/{approval_request.FILE_NAME}"]
+    (target / dir_).mkdir(parents=True, exist_ok=True)
+    (target / paths[0]).write_bytes(data)
+    (target / paths[1]).write_text(approval_request.render(request), encoding="utf-8")
+    ops.commit_paths(
+        state.target_dir,
+        paths,
+        f"brief: заявка на одобрение discovery-брифа {state.ws_id}",
+    )
+    # До push (ревью части B, B3): локальный коммит над базой меняет ровно
+    # два пути предложения; его SHA — то, что обязан нести будущий PR.
+    _, changed = ops.changed_paths(state.target_dir, default.name)
+    if sorted(changed) != sorted(paths):
+        raise BriefToolError(f"коммит предложения меняет {changed} — не предложение")
+    head = ops.head_sha(state.target_dir, branch)
+    try:
+        ops.push_branch(state.target_dir, branch)
+    except RuntimeError as exc:
+        raise BriefToolError(
+            f"push {branch} не прошёл ({exc}) — повторите: make brief-propose "
+            f"RUN={state.run_id}",
+            retry=True,
+        ) from exc
+    return _open_pr(ops, state, branch, default, request.policy_sha, head)
+
+
+def _open_pr(ops, state, branch: str, default, policy_sha: str, head: str) -> int:
+    """PR на ветку, проверенную по SHA `head`; голова созданного PR обязана быть им.
+
+    Ветка изменяема: между проверкой и созданием её могли сдвинуть. PR с
+    другой головой не объявляется результатом — это отказ с номером PR.
+    """
+    repo = policy_rule.policy_source()[0]
+    body = (
+        "Акт одобрения discovery-брифа (D5): мерж этого PR учёткой человека из "
+        "политики подписи — это подпись брифа, а не одобрение governance-узлов.\n\n"
+        f"policy: {repo}@{policy_sha}\n\n"
+        "Если ruleset цели требует одобряющего ревью — перед мержем: "
+        f"`sh review-pr.sh {state.repo} <этот PR>`.\n"
+        "Мерж: `make human-merge ARGS='<repo> <этот PR>'`; затем "
+        f"`make brief-approve RUN={state.run_id} PR=<этот PR>`.\n"
+    )
+    try:
+        number = ops.create_pr(
+            state.target_dir,
+            state.repo_slug,
+            branch,
+            f"brief: одобрение discovery-брифа {state.ws_id}",
+            body,
+            LABEL,
+            base=default.name,
+        )
+    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
+        raise BriefToolError(
+            f"PR не создан ({exc}) — повторите: make brief-propose RUN={state.run_id} "
+            "(ветка уже на форджe, повтор создаст только PR)",
+            retry=True,
+        ) from exc
+    created = _fact(ops.brief_pr_fact(state.repo_slug, number), f"PR #{number}").value
+    if created.head_sha != head:
+        raise BriefToolError(
+            f"brief-PR #{number} создан с головой {created.head_sha}, проверялась "
+            f"{head} — ветку сдвинули; предложение НЕ годно, разберитесь вручную"
+        )
+    print(f"brief-PR #{number} создан")
+    return number
+
+
+def propose(run_id: str, ops) -> int:
+    """§11.3: brief-PR из `brief_ready`; повтор восстанавливается по форджу."""
+    with rl.run_lock(run_id):
+        state = rs.load(run_id)
+        data, brief_text = _ready_brief(state)
+        dir_ = proposal_dir(state)
+        branch = brief_branch(state.ws_id)
+        found = _fact(ops.find_brief_pr_fact(state.repo_slug, branch), "PR ветки").value
+        if len(found) > 1:
+            raise BriefToolError(f"неоднозначно: у {branch} несколько PR {found}")
+        if found:
+            return _existing_pr(ops, state, found[0], brief_text, dir_)
+        head = ops.remote_branch_head_fact(state.repo_slug, branch)
+        _fact(head, f"ветка {branch}")
+        if head.outcome is Outcome.FOUND:
+            # Окно «push есть, PR нет»: и список файлов, и содержимое — по
+            # ОДНОМУ неизменяемому SHA головы (ревью части B, B3).
+            default = _fact(
+                ops.default_branch_fact(state.repo_slug), "ветка по умолчанию"
+            ).value
+            files = _fact(
+                ops.compare_files_fact(state.repo_slug, default.sha, head.value),
+                "diff",
+            ).value
+            pin = _matches(ops, state, head.value, files, brief_text, dir_)
+            return _open_pr(ops, state, branch, default, pin, head.value)
+        return _create(ops, state, branch, data, brief_text, dir_)
+
+
+def main(argv: list[str] | None = None) -> int:
+    """CLI обёрток brief-маршрута."""
+    parser = argparse.ArgumentParser(prog="brief_tools")
+    sub = parser.add_subparsers(dest="command", required=True)
+    p_propose = sub.add_parser("propose", help="brief-PR из customer-прогона")
+    p_propose.add_argument("--run", required=True)
+    args = parser.parse_args(argv)
+    from governance.ops import RealOps
+
+    ops = RealOps()
+    try:
+        if args.command == "propose":
+            propose(args.run, ops)
+            return 0
+    except rl.LockBusy as exc:
+        print(f"brief-tools: {exc}", file=sys.stderr)
+        return 1
+    except BriefToolError as exc:
+        print(f"brief-tools: {exc}", file=sys.stderr)
+        return 2 if exc.retry else 1
+    return 1
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
```

```diff
diff --git a/governance/ops.py b/governance/ops.py
index 280c13f..415ccab 100644
--- a/governance/ops.py
+++ b/governance/ops.py
@@ -171,6 +171,7 @@ class Ops(Protocol):
         label: str,
         *,
         draft: bool = False,
+        base: str | None = None,
     ) -> int: ...
 
     def create_draft_pr(
@@ -1550,8 +1551,12 @@ class RealOps(BriefFactsMixin):
         label: str,
         *,
         draft: bool = False,
+        base: str | None = None,
     ) -> int:
-        """gh pr create [--draft] [--label <label>] -R <slug>; номер из URL.
+        """gh pr create [--draft] [--label <label>] [--base <base>] -R <slug>; номер из URL.
+
+        `base` — явная база (brief-PR, §11.3): без неё gh берёт ветку по
+        умолчанию на момент вызова, а вызывающий уже прочитал её из форджа.
 
         Пустой ``label`` не передаётся вовсе (решение владельца 2026-08-31:
         лейбл `codex-review` больше не вешается — он триггерил платный
@@ -1572,6 +1577,7 @@ class RealOps(BriefFactsMixin):
         """
         draft_args = ["--draft"] if draft else []
         label_args = ["--label", label] if label else []
+        base_args = ["--base", base] if base else []
         done = subprocess.run(
             [
                 "gh",
@@ -1587,6 +1593,7 @@ class RealOps(BriefFactsMixin):
                 "--body",
                 body,
                 *label_args,
+                *base_args,
             ],
             cwd=target_dir,
             capture_output=True,
```

```diff
diff --git a/Makefile b/Makefile
index e82c967..c6af73f 100644
--- a/Makefile
+++ b/Makefile
@@ -16,7 +16,7 @@ WORKSPACE ?= ..
 MANIFEST ?= $(WORKSPACE)/ai-orchestrators-workspace/workspace-manifest.toml
 
 .DEFAULT_GOAL := help
-.PHONY: help status fetch pull dirty branches bootstrap drift conformance catalog-fixtures graph-drift plan-check plan-check-selftest todo-context todo-work plan-check-fixture inbox issues morning evening snapshot fleet-report today salvage config-policy install arch-freshness arch-freshness-read behaviour-run spec-loop behaviour-console behaviour-tasks accept-pr preflight edge-check selfcheck selfcheck-dogfood criteria-close conductor
+.PHONY: help status fetch pull dirty branches bootstrap drift conformance catalog-fixtures graph-drift plan-check plan-check-selftest todo-context todo-work plan-check-fixture inbox issues morning evening snapshot fleet-report today salvage config-policy install arch-freshness arch-freshness-read behaviour-run spec-loop behaviour-console behaviour-tasks brief-propose brief-approve accept-pr preflight edge-check selfcheck selfcheck-dogfood criteria-close conductor
 
 help:
 	@echo "Цели:"
@@ -51,6 +51,7 @@ help:
 	@echo "  make behaviour-run ARGS=… — governance runner CLI: start|resume|verify|status (uv + группа governance)"
 	@echo "  make criteria-close ARGS='--run <id> [--product-sha <sha>]' — закрытие воркстрима по оракулу бандла (срез 2a: предложение → мерж (человеком при ручных критериях) → штамп accepted; выходы 0/2/4/5/6; флага обхода нет)"
 	@echo "  make spec-loop SUBJECT='…' REPO=… — операторская кнопка: start → мерж бандла (человек) → одобрение узлов (человек, --approve-node) → повтор той же команды → deliver tasks-спеки → approve (человек); merge-authority жёстко human, неоднозначности — fail-closed (--run-id/--ws-id через ARGS)"
+	@echo "  make brief-propose RUN=<customer-run> — brief-PR (бриф + заявка на одобрение) из прогона в brief_ready; мерж — человек (make human-merge), затем make brief-approve (спека need-stage §11.3)"
 	@echo "  make spec-loop … ARGS='--legacy' — ОТКАЗ с названной причиной (S13, 2026-09-23): прежний путь бандл-PR удалён из исполнения; --waves принимается и ничего не меняет; исторические леджеры читаются как прежде (make behaviour-console)"
 	@echo "  make spec-loop ARGS='--need --frame customer --stakeholder <role>' — стадия Need вместо готового --brief: запускает discovery-интервью, прогон паркуется в waiting_interview и печатает команду ответа стейкхолдеру; ответьте вне spec-loop и повторите ту же команду с --run-id <id> (engineer-фрейм отказан до discovery#49)"
 	@echo "  make behaviour-console ARGS=… — governance console TUI (uv + группа governance)"
@@ -105,6 +106,7 @@ behaviour-console: ; @uv run --frozen --group governance python -m governance.co
 behaviour-tasks: ; @uv run --frozen --group governance python -m governance.task_bridge $(ARGS)
 accept-pr: ; @uv run --frozen python -m governance.accept_pr $(ARGS)
 human-merge: ; @sh ./human-merge.sh $(ARGS)
+brief-propose: ; @uv run --frozen python -m governance.brief_tools propose --run "$(RUN)"
 preflight: ; @uv run --frozen python ./spec_run_preflight.py $(ARGS)
 edge-check:  ; @uv run --frozen python ./edge_check.py $(ARGS)
 conductor: ; @uv run --frozen python -m conductor $(ARGS) --root $(WORKSPACE)
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_brief_tools.py -q` → 69 passed; мутации (поля заявки, байты брифа, чужой файл в базе, грязное дерево, `base`, `brief_ready`, пин — не версия, неоднозначность; после B3 — проверка путей до push, сверка головы PR) — пойманы (проверено).

- [ ] **Step 5: Commit** — `feat(need): brief-propose — brief-PR с заявкой, восстановление по форджу (§11.3)`.

---

### Task 12: `brief-approve`

**Files:**
- Modify: `governance/brief_tools.py` (`approve`, `_judge_approve`, CLI), `Makefile`
- Test: `tests/test_brief_tools.py`

**Interfaces:**
- Produces: `approve(run_id, pr, ops) -> Path` (подписанный файл
  `run_dir/brief-approval/customer-brief.md`); `APPROVED_REL`.

- [ ] **Step 1: Тесты** (T44, T44a, T44b, T45 по каждому коду, T46 — смена политики во
время вызова и недоступность после; идемпотентный повтор; чужой файл на месте; PR
другого прогона; T45 — код 0, но записанный конверт не зеркалит мерж — отказ; T21b — общий
набор дефектов заявки; T37 — `brief-propose`/`brief-approve`
при занятой блокировке: код 1, `run.json` не читался, дерево прогона байт в байт):

```diff
diff --git a/tests/test_brief_tools.py b/tests/test_brief_tools.py
index 6463f5c..38ffe3e 100644
--- a/tests/test_brief_tools.py
+++ b/tests/test_brief_tools.py
@@ -42,6 +42,30 @@ class ToolOps(FakeForge):
     dirty: bool = False
     next_pr: int = 42
     push_error: str | None = None
+    #: (код, что записать в файл или None, reason) — ответ `discovery approve`.
+    approve_reply: tuple[int, str | None, str] = (0, None, "")
+    approve_calls: int = 0
+    after_approve: object = None
+
+    def discovery_approve(self, brief_path, repo, pr, path, cwd, *, lock_fd=None):
+        from governance import interview as _iv
+
+        self.approve_calls += 1
+        code, text, reason = self.approve_reply
+        if text is not None:
+            Path(brief_path).write_text(text, encoding="utf-8")
+        if callable(self.after_approve):
+            self.after_approve()
+        envelope = {
+            "lifecycle": "complete",
+            "gate": "fail" if code == 10 else "pass",
+            "readiness": "incomplete" if code == 11 else "ready",
+            "next_action": {},
+            "findings": [],
+            "readiness_findings": [],
+            "operation": {"status": "refused" if code == 2 else "ok", "reason": reason},
+        }
+        return _iv.DiscoveryReply(code, envelope, "")
 
     def remote_branch_head_fact(self, repo_slug: str, branch: str) -> Fact[str]:
         if "remote" in self.unavailable_facts:
@@ -491,3 +515,195 @@ def test_local_commit_with_extra_path_refuses_before_push(  # B3 (до push)
     with pytest.raises(bt.BriefToolError, match="не предложение"):
         bt.propose(RUN_ID, ops)
     assert not any(c[0] == "push_branch" for c in ops.calls)
+
+
+# --- §11.5: brief-approve (Task 12) ---
+
+from governance import spec_loop  # noqa: E402
+from tests.forge_fake import SIGNED  # noqa: E402
+
+
+@pytest.fixture()
+def approve_env(tmp_path, runs_root, monkeypatch):
+    _brief_ready_run(tmp_path)
+    return _ops(monkeypatch, with_pr=True)
+
+
+def _signed_path() -> Path:
+    return rs.run_dir(RUN_ID) / bt.APPROVED_REL
+
+
+def test_approve_success_prints_engineer_command(approve_env, capsys) -> None:  # T44
+    approve_env.approve_reply = (0, SIGNED, "")
+    path = bt.approve(RUN_ID, 7, approve_env)
+    assert path == _signed_path() and path.read_text(encoding="utf-8") == SIGNED
+    out = capsys.readouterr().out
+    assert "--frame engineer" in out and "--approval-pr 7" in out and "--new-run" in out
+
+
+@pytest.mark.parametrize(
+    ("reply", "phrase"),
+    [
+        ((1, None, "boom"), "неизвестен"),
+        ((2, DRAFT, "brief_bytes_diverged"), "draft"),
+        ((2, None, "approver_not_authorized"), "не тронут"),
+        ((10, SIGNED, ""), "подпись записана"),
+        ((11, SIGNED, ""), "подпись записана"),
+        ((0, None, ""), "не зеркалит"),
+        (
+            (0, SIGNED.replace("approver: andrei-shtanakov", "approver: someone"), ""),
+            "не зеркалит",
+        ),
+    ],
+    ids=[
+        "code1",
+        "diverged",
+        "refused",
+        "code10",
+        "code11",
+        "code0-unsigned",
+        "code0-foreign-envelope",
+    ],
+)
+def test_approve_failures_print_no_engineer_command(  # T45
+    approve_env, capsys, reply, phrase
+) -> None:
+    approve_env.approve_reply = reply
+    with pytest.raises(bt.BriefToolError, match=phrase):
+        bt.approve(RUN_ID, 7, approve_env)
+    assert "--frame engineer" not in capsys.readouterr().out
+
+
+def test_policy_moved_during_approve(approve_env) -> None:  # T46
+    approve_env.approve_reply = (0, SIGNED, "")
+    approve_env.after_approve = lambda: approve_env.add_policy(C1)
+    with pytest.raises(bt.BriefToolError, match="не подтверждён"):
+        bt.approve(RUN_ID, 7, approve_env)
+
+
+def test_policy_unavailable_after_approve(approve_env) -> None:  # T46
+    approve_env.approve_reply = (0, SIGNED, "")
+    approve_env.after_approve = lambda: approve_env.unavailable_facts.add("policy")
+    with pytest.raises(bt.BriefToolError, match="не подтверждён"):
+        bt.approve(RUN_ID, 7, approve_env)
+
+
+def test_drift_before_first_approve_then_reconfirm(approve_env, capsys) -> None:  # T44a
+    approve_env.add_policy(C1)
+    with pytest.raises(bt.BriefToolError, match="policy-reconfirm:"):
+        bt.approve(RUN_ID, 7, approve_env)
+    assert approve_env.approve_calls == 0
+    approve_env.reconfirm(C1)
+    approve_env.approve_reply = (0, SIGNED, "")
+    assert bt.approve(RUN_ID, 7, approve_env) == _signed_path()
+
+
+def test_drift_after_approve_blocks_engineer_until_reconfirm(  # T44b
+    approve_env, tmp_path
+) -> None:
+    approve_env.approve_reply = (0, SIGNED, "")
+    path = bt.approve(RUN_ID, 7, approve_env)
+    approve_env.add_policy(C1)
+    with pytest.raises(spec_loop.SpecLoopError, match="upstream_policy_drift"):
+        spec_loop.engineer_preflight(str(path), 7, REPO, approve_env)
+    approve_env.reconfirm(C1)
+    assert (
+        spec_loop.engineer_preflight(str(path), 7, REPO, approve_env).approval_pr == 7
+    )
+
+
+def test_repeat_after_success_is_idempotent(approve_env) -> None:
+    approve_env.approve_reply = (0, SIGNED, "")
+    bt.approve(RUN_ID, 7, approve_env)
+    before = _signed_path().read_bytes()
+    assert bt.approve(RUN_ID, 7, approve_env) == _signed_path()
+    assert _signed_path().read_bytes() == before
+
+
+@pytest.mark.parametrize("command", ["propose", "approve"])
+def test_busy_run_exits_1_before_reading_state(  # T37 (brief-propose/brief-approve)
+    tmp_path, runs_root, monkeypatch, command
+) -> None:
+    from governance import run_lock
+
+    _brief_ready_run(tmp_path)
+    before = {p: p.read_bytes() for p in rs.run_dir(RUN_ID).rglob("*") if p.is_file()}
+
+    def no_load(run_id):
+        raise AssertionError("run.json прочитан при занятой блокировке")
+
+    monkeypatch.setattr(rs, "load", no_load)
+    argv = [command, "--run", RUN_ID] + (["--pr", "7"] if command == "approve" else [])
+    with run_lock.run_lock(RUN_ID):
+        assert bt.main(argv) == 1
+    after = {p: p.read_bytes() for p in rs.run_dir(RUN_ID).rglob("*") if p.is_file()}
+    assert after == before
+
+
+def test_foreign_file_in_place_refuses(approve_env) -> None:
+    _signed_path().parent.mkdir(parents=True, exist_ok=True)
+    _signed_path().write_text(DRAFT + "\nчужое\n", encoding="utf-8")
+    with pytest.raises(bt.BriefToolError, match="чужой"):
+        bt.approve(RUN_ID, 7, approve_env)
+    assert approve_env.approve_calls == 0
+
+
+def test_pr_of_another_run_refuses(approve_env) -> None:
+    approve_env.set_pr(
+        files=(
+            ("workstreams/WS-9/spec/00-discovery/brief.md", "added"),
+            (f"workstreams/WS-9/spec/00-discovery/{ar.FILE_NAME}", "added"),
+        )
+    )
+    for name in ("brief.md", ar.FILE_NAME):
+        approve_env.files[(MERGE, f"workstreams/WS-9/spec/00-discovery/{name}")] = (
+            approve_env.files[(MERGE, f"{DIR}/{name}")]
+        )
+    with pytest.raises(bt.BriefToolError, match="не бриф этого прогона"):
+        bt.approve(RUN_ID, 7, approve_env)
+
+
+@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
+def test_browser_merged_ambiguous_request_refuses(
+    approve_env, case, mutate, _m
+) -> None:  # T21a/b
+    key = (MERGE, f"{DIR}/{ar.FILE_NAME}")
+    approve_env.files[key] = mutate(ar.render(request()))
+    with pytest.raises(bt.BriefToolError):
+        bt.approve(RUN_ID, 7, approve_env)
+    assert approve_env.approve_calls == 0
+
+
+def test_policy_switch_to_reconfirmed_version_during_approve(
+    approve_env,
+) -> None:  # T46
+    """Во время вызова появились новая версия C1 И подтверждение к ней: проверка
+    «до» (P) и «после» (C1) обе проходят, но версии разные — подпись могла быть
+    записана под любой из них. Ловит только сравнение «до/после»."""
+    approve_env.approve_reply = (0, SIGNED, "")
+
+    def switch() -> None:
+        approve_env.add_policy(C1)
+        approve_env.reconfirm(C1)
+
+    approve_env.after_approve = switch
+    with pytest.raises(bt.BriefToolError, match="сменилась во время approve"):
+        bt.approve(RUN_ID, 7, approve_env)
+
+
+@pytest.mark.parametrize(
+    ("reason", "writes", "phrase"),
+    [
+        ("pr_not_merged", None, "не тронут"),
+        ("approver_not_authorized", None, "не тронут"),
+        ("brief_not_in_pr", None, "не тронут"),
+        ("brief_bytes_diverged", DRAFT, "draft"),
+    ],
+)
+def test_approve_each_refusal_reason(
+    approve_env, capsys, reason, writes, phrase
+) -> None:  # T45
+    approve_env.approve_reply = (2, writes, reason)
+    with pytest.raises(bt.BriefToolError, match=phrase):
+        bt.approve(RUN_ID, 7, approve_env)
+    assert "--frame engineer" not in capsys.readouterr().out
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/brief_tools.py b/governance/brief_tools.py
index 18e73a8..d6b88df 100644
--- a/governance/brief_tools.py
+++ b/governance/brief_tools.py
@@ -14,6 +14,7 @@ CLI: `python -m governance.brief_tools {propose,approve,check-merge} …`;
 from __future__ import annotations
 
 import argparse
+import os
 import subprocess
 import sys
 from pathlib import Path
@@ -22,6 +23,7 @@ from governance import (
     approval_facts,
     approval_request,
     brief_input,
+    brief_provenance,
     discovery_approval,
     policy_rule,
 )
@@ -289,12 +291,122 @@ def propose(run_id: str, ops) -> int:
         return _create(ops, state, branch, data, brief_text, dir_)
 
 
+def _policy_or_fail(ops, act: brief_provenance.Act) -> str:
+    got = brief_provenance.check_policy(ops, act)
+    if isinstance(got, brief_provenance.Refusal):
+        raise BriefToolError(got.detail, retry=got.retry)
+    return got.current
+
+
+def _engineer_command(state: rs.RunState, pr: int, path: Path) -> str:
+    return (
+        f"make spec-loop SUBJECT='{state.subject}' REPO={state.repo} ARGS='--need "
+        f"--frame engineer --new-run --ws-id {state.ws_id}-eng --stakeholder "
+        f"<role> --traces-to {path} --approval-pr {pr}'"
+    )
+
+
+def approve(run_id: str, pr: int, ops) -> Path:
+    """§11.5: зеркало человеческого мержа brief-PR через `discovery approve`.
+
+    Успех требует ВСЕГО вместе: акт по форджу, политика до и после вызова
+    той же версии, код 0 и перечитанный файл, честно зеркалящий мерж.
+    Возвращает путь подписанного файла (вход engineer-прогона).
+    """
+    with rl.run_lock(run_id) as lock:
+        state = rs.load(run_id)
+        act = brief_provenance.read_act(ops, state.repo_slug, pr)
+        if isinstance(act, brief_provenance.Refusal):
+            raise BriefToolError(act.detail, retry=act.retry)
+        if act.dir != proposal_dir(state):
+            raise BriefToolError(f"PR #{pr} одобряет {act.dir}, не бриф этого прогона")
+        own = (rs.run_dir(run_id) / iv.BRIEF_REL).read_text(encoding="utf-8")
+        if discovery_approval.self_hash(own) != act.brief_self_hash:
+            raise BriefToolError(f"заявка PR #{pr} — не про бриф этого прогона")
+        before = _policy_or_fail(ops, act)
+        target = rs.run_dir(run_id) / APPROVED_REL
+        merged = _fact(
+            ops.repo_file_fact(
+                state.repo_slug, act.merge_commit, f"{act.dir}/{approval_request.BRIEF}"
+            ),
+            "бриф merge-коммита",
+        )
+        if merged.outcome is not Outcome.FOUND:
+            raise BriefToolError(f"бриф в merge-коммите не найден: {merged.detail}")
+        if target.exists():
+            current = target.read_text(encoding="utf-8")
+            if discovery_approval.self_hash(current) != act.brief_self_hash:
+                raise BriefToolError(f"{target}: на месте чужой файл")
+        else:
+            target.parent.mkdir(parents=True, exist_ok=True)
+            tmp = target.with_name(".customer-brief.tmp")
+            tmp.write_text(merged.value, encoding="utf-8")
+            os.replace(tmp, target)
+        reply = ops.discovery_approve(
+            str(target),
+            state.repo_slug,
+            pr,
+            f"{act.dir}/{approval_request.BRIEF}",
+            str(rs.run_dir(run_id)),
+            lock_fd=lock.fd,
+        )
+        try:
+            after = _policy_or_fail(ops, act)
+        except BriefToolError as exc:
+            raise BriefToolError(
+                f"итог не подтверждён: подпись могла быть записана под "
+                f"неустановленной политикой ({exc})",
+                retry=exc.retry,
+            ) from exc
+        if after != before:
+            raise BriefToolError(
+                f"итог не подтверждён: политика сменилась во время approve "
+                f"({before} → {after}); подпись могла быть записана — повторите"
+            )
+        _judge_approve(reply, target, act)
+        print("подписано; следующий шаг:")
+        print("  " + _engineer_command(state, pr, target))
+        return target
+
+
+def _judge_approve(reply, target: Path, act: brief_provenance.Act) -> None:
+    """Код вызова — подсказка для текста; истина — перечитанный файл (§11.5 п.5)."""
+    reason = reply.envelope.get("operation", {}).get("reason", "")
+    if reply.code == 1:
+        raise BriefToolError(f"итог approve неизвестен: {reason}", retry=True)
+    if reply.code == 2:
+        if reason == "brief_bytes_diverged":
+            raise BriefToolError(
+                "approve: байты разошлись со смерженными — файл откатан в draft"
+            )
+        raise BriefToolError(f"approve отказал ({reason}) — файл не тронут")
+    if reply.code in (10, 11):
+        axis = "линтер отклоняет бриф" if reply.code == 10 else "readiness=incomplete"
+        raise BriefToolError(
+            f"approve вернул {reply.code}: подпись записана, но {axis} — engineer "
+            "такой upstream не примет"
+        )
+    data = target.read_bytes()
+    refusal = brief_provenance.check_operator_brief(act, data.decode("utf-8"))
+    if refusal is not None:
+        raise BriefToolError(
+            f"approve вернул 0, но конверт не зеркалит мерж: {refusal.detail}"
+        )
+    try:
+        brief_input.check_customer_upstream(target, data)
+    except brief_input.BriefInputError as exc:
+        raise BriefToolError(f"подписанный бриф не годится в upstream: {exc}") from exc
+
+
 def main(argv: list[str] | None = None) -> int:
     """CLI обёрток brief-маршрута."""
     parser = argparse.ArgumentParser(prog="brief_tools")
     sub = parser.add_subparsers(dest="command", required=True)
     p_propose = sub.add_parser("propose", help="brief-PR из customer-прогона")
     p_propose.add_argument("--run", required=True)
+    p_approve = sub.add_parser("approve", help="зеркало мержа brief-PR")
+    p_approve.add_argument("--run", required=True)
+    p_approve.add_argument("--pr", required=True, type=int)
     args = parser.parse_args(argv)
     from governance.ops import RealOps
 
@@ -303,6 +415,9 @@ def main(argv: list[str] | None = None) -> int:
         if args.command == "propose":
             propose(args.run, ops)
             return 0
+        if args.command == "approve":
+            approve(args.run, args.pr, ops)
+            return 0
     except rl.LockBusy as exc:
         print(f"brief-tools: {exc}", file=sys.stderr)
         return 1
```

```diff
diff --git a/Makefile b/Makefile
index c6af73f..da7c5a3 100644
--- a/Makefile
+++ b/Makefile
@@ -52,6 +52,7 @@ help:
 	@echo "  make criteria-close ARGS='--run <id> [--product-sha <sha>]' — закрытие воркстрима по оракулу бандла (срез 2a: предложение → мерж (человеком при ручных критериях) → штамп accepted; выходы 0/2/4/5/6; флага обхода нет)"
 	@echo "  make spec-loop SUBJECT='…' REPO=… — операторская кнопка: start → мерж бандла (человек) → одобрение узлов (человек, --approve-node) → повтор той же команды → deliver tasks-спеки → approve (человек); merge-authority жёстко human, неоднозначности — fail-closed (--run-id/--ws-id через ARGS)"
 	@echo "  make brief-propose RUN=<customer-run> — brief-PR (бриф + заявка на одобрение) из прогона в brief_ready; мерж — человек (make human-merge), затем make brief-approve (спека need-stage §11.3)"
+	@echo "  make brief-approve RUN=<customer-run> PR=<brief-PR> — после человеческого мержа brief-PR: discovery approve, проверка политики до и после, печать команды engineer-прогона (§11.5)"
 	@echo "  make spec-loop … ARGS='--legacy' — ОТКАЗ с названной причиной (S13, 2026-09-23): прежний путь бандл-PR удалён из исполнения; --waves принимается и ничего не меняет; исторические леджеры читаются как прежде (make behaviour-console)"
 	@echo "  make spec-loop ARGS='--need --frame customer --stakeholder <role>' — стадия Need вместо готового --brief: запускает discovery-интервью, прогон паркуется в waiting_interview и печатает команду ответа стейкхолдеру; ответьте вне spec-loop и повторите ту же команду с --run-id <id> (engineer-фрейм отказан до discovery#49)"
 	@echo "  make behaviour-console ARGS=… — governance console TUI (uv + группа governance)"
@@ -107,6 +108,7 @@ behaviour-tasks: ; @uv run --frozen --group governance python -m governance.task
 accept-pr: ; @uv run --frozen python -m governance.accept_pr $(ARGS)
 human-merge: ; @sh ./human-merge.sh $(ARGS)
 brief-propose: ; @uv run --frozen python -m governance.brief_tools propose --run "$(RUN)"
+brief-approve: ; @uv run --frozen python -m governance.brief_tools approve --run "$(RUN)" --pr "$(PR)"
 preflight: ; @uv run --frozen python ./spec_run_preflight.py $(ARGS)
 edge-check:  ; @uv run --frozen python ./edge_check.py $(ARGS)
 conductor: ; @uv run --frozen python -m conductor $(ARGS) --root $(WORKSPACE)
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_brief_tools.py -q` → 105 passed (T45 — по каждой причине отказа соседа отдельно; T37 — мутация «чтение до блокировки» поймана); мутации (политика после, политика до, суд по файлу при коде 0, код 11, каталог прогона, чужой файл) — пойманы (проверено; «политика после» ловится только тестом появления новой версии И подтверждения во время вызова).

- [ ] **Step 5: Commit** — `feat(need): brief-approve — зеркало акта с проверкой политики до и после (§11.5)`.

---

### Task 13: `human-merge.sh` проверяет заявку brief-PR по head SHA

**Files:**
- Modify: `governance/brief_tools.py` (`check_merge`, `MergeRefused`, CLI `check-merge`),
  `human-merge.sh`
- Test: `tests/test_brief_tools.py`, `tests/test_human_merge.py` (стаб `uv`)

- [ ] **Step 1: Тесты** (T50 — каждый отказ до мержа, в т.ч. лишний файл и координаты
политики `repo`/`ref`/`path` по отдельности; смена головы — retry; недоступность —
retry; T21b; тело/метки не участвуют; коды CLI; shell: проверка по голове и мерж с тем же
SHA, код проверяльщика 2/3 — без мержа, профиль человека, T50a — candidate и прочие PR
проверяльщика не зовут; B1 — CLI `check-merge` печатает в stdout ТОЛЬКО пин политики, скрипт
забирает его в `brief_pin`, проверяет 40 hex и для `brief/*` требует `current == brief_pin`,
версией мержа берёт `brief_pin`; мусор в stdout — без мержа):

```diff
diff --git a/tests/test_brief_tools.py b/tests/test_brief_tools.py
index 38ffe3e..9a18751 100644
--- a/tests/test_brief_tools.py
+++ b/tests/test_brief_tools.py
@@ -707,3 +707,109 @@ def test_approve_each_refusal_reason(
     with pytest.raises(bt.BriefToolError, match=phrase):
         bt.approve(RUN_ID, 7, approve_env)
     assert "--frame engineer" not in capsys.readouterr().out
+
+
+# --- §11.3 п.6: check-merge перед человеческим мержем (Task 13) ---
+
+
+@pytest.fixture()
+def open_pr(monkeypatch) -> ToolOps:
+    ops = _ops(monkeypatch, with_pr=True)
+    ops.prs[7] = replace(
+        ops.prs[7], state="OPEN", merged_by=None, merged_at=None, merge_commit=None
+    )
+    return ops
+
+
+def test_check_merge_accepts_consistent_head(open_pr) -> None:  # T50
+    assert bt.check_merge(REPO, 7, HEAD, open_pr) == P
+
+
+@pytest.mark.parametrize(
+    "spoil",
+    [
+        "no-request",
+        "one-file",
+        "modified",
+        "two-dirs",
+        "coords",
+        "coords-ref",
+        "coords-path",
+        "extra-file",
+        "self-hash",
+        "pin-drift",
+        "not-open",
+        "not-brief-branch",
+        "wrong-base",
+    ],
+)
+def test_check_merge_refusals(open_pr, spoil) -> None:  # T50 (каждый — пара с accepts)
+    req_key, brief_key = (HEAD, f"{DIR}/{ar.FILE_NAME}"), (HEAD, f"{DIR}/brief.md")
+    if spoil == "no-request":
+        del open_pr.files[req_key]
+    elif spoil == "one-file":
+        open_pr.set_pr(files=(FILES[0],))
+    elif spoil == "modified":
+        open_pr.set_pr(files=((FILES[0][0], "modified"), FILES[1]))
+    elif spoil == "two-dirs":
+        open_pr.set_pr(files=(("a/00-discovery/brief.md", "added"), FILES[1]))
+    elif spoil == "coords":
+        open_pr.files[req_key] = ar.render(request(policy_repo="o/other"))
+    elif spoil == "coords-ref":
+        open_pr.files[req_key] = ar.render(request(policy_ref="dev"))
+    elif spoil == "coords-path":
+        open_pr.files[req_key] = ar.render(request(policy_path="x.env"))
+    elif spoil == "extra-file":
+        open_pr.set_pr(files=FILES + (("x.txt", "added"),))
+    elif spoil == "self-hash":
+        open_pr.files[brief_key] = DRAFT + "\nправка\n"
+    elif spoil == "pin-drift":
+        open_pr.add_policy(C1)
+    elif spoil == "not-open":
+        open_pr.set_pr(state="CLOSED")
+    elif spoil == "not-brief-branch":
+        open_pr.set_pr(head_ref="feature/x")
+    elif spoil == "wrong-base":
+        open_pr.set_pr(base_ref="side")
+    with pytest.raises(bt.MergeRefused):
+        bt.check_merge(REPO, 7, HEAD, open_pr)
+
+
+@pytest.mark.parametrize(("case", "mutate", "_m"), DEFECTS, ids=[d[0] for d in DEFECTS])
+def test_check_merge_ambiguous_request(open_pr, case, mutate, _m) -> None:  # T21b
+    open_pr.files[(HEAD, f"{DIR}/{ar.FILE_NAME}")] = mutate(ar.render(request()))
+    with pytest.raises(bt.MergeRefused):
+        bt.check_merge(REPO, 7, HEAD, open_pr)
+
+
+def test_check_merge_head_changed_is_retry(open_pr) -> None:  # T50
+    with pytest.raises(bt.BriefToolError) as exc:
+        bt.check_merge(REPO, 7, "9" * 40, open_pr)
+    assert exc.value.retry and not isinstance(exc.value, bt.MergeRefused)
+
+
+@pytest.mark.parametrize("fact", ["pr", "file", "policy", "default"])
+def test_check_merge_unavailable_is_retry(open_pr, fact) -> None:  # T50
+    open_pr.unavailable_facts.add(fact)
+    with pytest.raises(bt.BriefToolError) as exc:
+        bt.check_merge(REPO, 7, HEAD, open_pr)
+    assert exc.value.retry
+
+
+def test_check_merge_ignores_body_and_labels(open_pr) -> None:  # T50, T23
+    # Контракт факта brief-PR не несёт ни тела, ни меток: доказательство —
+    # только содержимое головы; «правильное» тело неверную заявку не спасает.
+    open_pr.files[(HEAD, f"{DIR}/{ar.FILE_NAME}")] = ar.render(request(ws_id="WS-2"))
+    assert "body" not in BriefPrFacts.__dataclass_fields__
+    assert bt.check_merge(REPO, 7, HEAD, open_pr) == P  # ws_id не связывает с прогоном
+
+
+@pytest.mark.parametrize(("exc", "code"), [("MergeRefused", 3), ("retry", 2)])
+def test_check_merge_cli_exit_codes(monkeypatch, capsys, exc, code) -> None:
+    def fake(repo, pr, head, ops):
+        if exc == "MergeRefused":
+            raise bt.MergeRefused("нет")
+        raise bt.BriefToolError("сеть", retry=True)
+
+    monkeypatch.setattr(bt, "check_merge", fake)
+    assert bt.main(["check-merge", "--repo", REPO, "--pr", "7", "--head", HEAD]) == code
```

```diff
diff --git a/tests/test_human_merge.py b/tests/test_human_merge.py
index 002f8d6..056669a 100644
--- a/tests/test_human_merge.py
+++ b/tests/test_human_merge.py
@@ -50,6 +50,14 @@ esac
 """
 
 
+#: Стаб `uv`: журнал вызова (с cwd и профилем) и код проверяльщика brief-PR.
+UV_STUB = """#!/usr/bin/env bash
+echo "cwd=$(pwd) GH_CONFIG_DIR=${GH_CONFIG_DIR:-} uv $*" >> "$GH_STUB_LOG"
+[ "${UV_STUB_EXIT:-0}" = 0 ] && echo "${UV_STUB_PIN:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"
+exit "${UV_STUB_EXIT:-0}"
+"""
+
+
 class Fleet:
     def __init__(self, tmp_path: Path) -> None:
         self.fleet_root = tmp_path / "fleet"
@@ -79,6 +87,9 @@ class Fleet:
         gh = self.stub_bin / "gh"
         gh.write_text(GH_STUB)
         gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
+        uv = self.stub_bin / "uv"
+        uv.write_text(UV_STUB)
+        uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
 
     def env(self, **extra: str) -> dict[str, str]:
         env = os.environ.copy()
@@ -285,3 +296,79 @@ def test_unknown_repo_and_bad_args(fleet: Fleet) -> None:
         check=False,
     )
     assert res.returncode == 2
+
+
+# --- brief-PR (спека need-stage §11.3 п.6) ---
+
+
+def _uv_calls(fleet: Fleet) -> list[str]:
+    return [ln for ln in fleet.calls() if " uv " in f" {ln}"]
+
+
+def test_brief_pr_is_checked_by_head_then_merged_with_same_sha(fleet: Fleet) -> None:
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1", GH_STUB_BODY="")
+    assert res.returncode == 0, res.stderr
+    (uv,) = _uv_calls(fleet)
+    assert f"cwd={SCRIPT.parent}" in uv
+    assert (
+        "uv run --frozen python -m governance.brief_tools check-merge "
+        f"--repo andrei-shtanakov/demo --pr 7 --head {HEAD_SHA}"
+    ) in uv
+    assert fleet.merge_calls() and f"sha={HEAD_SHA}" in fleet.merge_calls()[0]
+
+
+@pytest.mark.parametrize("code", [2, 3])
+def test_brief_pr_refusal_stops_before_merge(fleet: Fleet, code: int) -> None:
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1", UV_STUB_EXIT=str(code))
+    assert res.returncode == code
+    assert fleet.merge_calls() == []
+    assert "заявка не прошла проверку" in res.stderr
+
+
+def test_brief_pr_check_runs_under_human_profile(fleet: Fleet, tmp_path: Path) -> None:
+    profile = tmp_path / "gh-human"
+    profile.mkdir()
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1", HUMAN_GH_CONFIG_DIR=str(profile))
+    assert res.returncode == 0, res.stderr
+    (uv,) = _uv_calls(fleet)
+    assert f"GH_CONFIG_DIR={profile} uv" in uv
+
+
+def test_candidate_and_other_prs_do_not_call_the_brief_check(
+    fleet: Fleet,
+) -> None:  # T50a
+    assert fleet.run().returncode == 0  # candidate (умолчание стаба)
+    res = fleet.run(GH_STUB_HEADREF="feature/x", GH_STUB_BODY="")
+    assert res.returncode == 0, res.stderr
+    assert _uv_calls(fleet) == []
+
+
+def test_brief_pr_policy_moved_after_check_refuses(fleet: Fleet) -> None:  # B1
+    """Пин, проверенный вместе с заявкой (UV_STUB_PIN), ≠ актуальной версии,
+    прочитанной shell позже: мерж не выполняется."""
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1", GH_STUB_POLICY_SHA="b" * 40)
+    assert res.returncode == 3
+    assert fleet.merge_calls() == []
+    assert "после проверки brief-PR" in res.stderr
+
+
+def test_brief_pr_merger_is_authorized_by_the_checked_pin(fleet: Fleet) -> None:  # B1
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1")
+    assert res.returncode == 0, res.stderr
+    reads = [c for c in fleet.calls() if "api graphql" in c and "s=" in c]
+    assert reads and all("s=" + "a" * 40 in c for c in reads)
+
+
+def test_brief_pr_without_pin_from_checker_refuses(fleet: Fleet) -> None:
+    res = fleet.run(GH_STUB_HEADREF="brief/WS-1", UV_STUB_PIN="not-a-sha")
+    assert res.returncode == 2 and fleet.merge_calls() == []
+
+
+def test_brief_pr_head_race_then_recheck_of_new_head(fleet: Fleet) -> None:  # T50
+    first = fleet.run(GH_STUB_HEADREF="brief/WS-1", GH_STUB_MERGE_FAIL="1")
+    assert first.returncode == 4  # форджа отклонила: голова сменилась после проверки
+    new_head = "1" * 40
+    second = fleet.run(GH_STUB_HEADREF="brief/WS-1", GH_STUB_HEADOID=new_head)
+    assert second.returncode == 0, second.stderr
+    assert f"--head {new_head}" in _uv_calls(fleet)[-1]
+    assert f"sha={new_head}" in fleet.merge_calls()[-1]
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Реализация**

```diff
diff --git a/governance/brief_tools.py b/governance/brief_tools.py
index d6b88df..084c16b 100644
--- a/governance/brief_tools.py
+++ b/governance/brief_tools.py
@@ -398,6 +398,63 @@ def _judge_approve(reply, target: Path, act: brief_provenance.Act) -> None:
         raise BriefToolError(f"подписанный бриф не годится в upstream: {exc}") from exc
 
 
+class MergeRefused(BriefToolError):
+    """Заявка brief-PR не прошла проверку перед человеческим мержем (код 3)."""
+
+
+def check_merge(repo: str, pr: int, head: str, ops) -> str:
+    """§11.3 п.6: проверка brief-PR перед мержем по ПРОВЕРЕННОЙ голове `head`.
+
+    Заявка и бриф читаются форджем по `head`; мерж вызывающий пинует тем же
+    SHA. Тело и метки PR в решении не участвуют. Возвращает пин политики.
+    """
+    facts = _fact(ops.brief_pr_fact(repo, pr), f"PR #{pr}").value
+    if facts.state != "OPEN":
+        raise MergeRefused(f"brief-PR #{pr} не открыт ({facts.state})")
+    if facts.head_sha != head:
+        raise BriefToolError(
+            f"голова PR #{pr} — {facts.head_sha}, проверялась {head}: перепроверьте",
+            retry=True,
+        )
+    if not facts.head_ref.startswith("brief/"):
+        raise MergeRefused(f"PR #{pr} — не brief-PR ({facts.head_ref})")
+    default = _fact(ops.default_branch_fact(repo), "ветка по умолчанию").value
+    if facts.base_ref != default.name:
+        raise MergeRefused(f"база PR #{pr} — {facts.base_ref}, не {default.name}")
+    dir_ = brief_provenance.files_dir(facts)
+    if isinstance(dir_, brief_provenance.Refusal):
+        raise MergeRefused(dir_.detail)
+    texts = {}
+    for name in (approval_request.FILE_NAME, approval_request.BRIEF):
+        fact = _fact(ops.repo_file_fact(repo, head, f"{dir_}/{name}"), name)
+        if fact.outcome is not Outcome.FOUND:
+            raise MergeRefused(f"в голове PR нет {dir_}/{name}")
+        texts[name] = fact.value
+    try:
+        req = approval_request.parse(texts[approval_request.FILE_NAME])
+    except approval_request.RequestError as exc:
+        raise MergeRefused(f"заявка: {exc}") from exc
+    if (
+        req.policy_repo,
+        req.policy_ref,
+        req.policy_path,
+    ) != policy_rule.policy_source():
+        raise MergeRefused("координаты политики заявки ≠ SSOT")
+    try:
+        own = discovery_approval.self_hash(texts[approval_request.BRIEF])
+    except discovery_approval.NotABrief as exc:
+        raise MergeRefused(f"бриф PR: {exc}") from exc
+    if own != req.brief_self_hash:
+        raise MergeRefused("brief_self_hash заявки ≠ брифу той же головы")
+    current = _current_policy(ops)
+    if req.policy_sha != current:
+        raise MergeRefused(
+            f"политика сменилась после предложения (пин {req.policy_sha}, "
+            f"актуальная {current}) — мерж не создал бы годного акта; --repropose"
+        )
+    return req.policy_sha
+
+
 def main(argv: list[str] | None = None) -> int:
     """CLI обёрток brief-маршрута."""
     parser = argparse.ArgumentParser(prog="brief_tools")
@@ -407,6 +464,10 @@ def main(argv: list[str] | None = None) -> int:
     p_approve = sub.add_parser("approve", help="зеркало мержа brief-PR")
     p_approve.add_argument("--run", required=True)
     p_approve.add_argument("--pr", required=True, type=int)
+    p_check = sub.add_parser("check-merge", help="заявка brief-PR перед мержем")
+    p_check.add_argument("--repo", required=True)
+    p_check.add_argument("--pr", required=True, type=int)
+    p_check.add_argument("--head", required=True)
     args = parser.parse_args(argv)
     from governance.ops import RealOps
 
@@ -418,9 +479,19 @@ def main(argv: list[str] | None = None) -> int:
         if args.command == "approve":
             approve(args.run, args.pr, ops)
             return 0
+        if args.command == "check-merge":
+            pin = check_merge(args.repo, args.pr, args.head, ops)
+            # stdout — ТОЛЬКО проверенный пин: его забирает human-merge.sh и
+            # по нему же авторизует мержера (ревью части B, B1).
+            print(f"brief-PR #{args.pr}: заявка годна", file=sys.stderr)
+            print(pin)
+            return 0
     except rl.LockBusy as exc:
         print(f"brief-tools: {exc}", file=sys.stderr)
         return 1
+    except MergeRefused as exc:
+        print(f"brief-tools: {exc}", file=sys.stderr)
+        return 3
     except BriefToolError as exc:
         print(f"brief-tools: {exc}", file=sys.stderr)
         return 2 if exc.retry else 1
```

```diff
diff --git a/human-merge.sh b/human-merge.sh
index 72c5264..6bdc5e2 100755
--- a/human-merge.sh
+++ b/human-merge.sh
@@ -139,6 +139,32 @@ head_ref=$(printf '%s\n' "$facts" | sed -n '5p')
 [ -n "$head_ref" ] && [ "$head_ref" != "null" ] \
     || die 2 "PR ${slug}#${pr}: имя head-ветки не установлено — тип PR неизвестен, мерж не выполняется"
 
+# brief-PR (спека need-stage §11.3 п.6): заявка читается форджем по
+# ПРОВЕРЕННОЙ голове `head_oid`; мерж ниже пинуется тем же sha= — смена
+# головы между проверкой и мержем отказывает на стороне форджи. Тело и
+# метки PR в решении не участвуют. Проверяльщик исполняется из каталога
+# devtools (там пакет governance) профилем человека.
+brief_pin=""
+case "$head_ref" in
+    brief/*)
+        if [ -n "$human_profile" ]; then
+            brief_pin=$(cd "$script_dir" && GH_CONFIG_DIR="$human_profile" uv run --frozen \
+                python -m governance.brief_tools check-merge \
+                --repo "$slug" --pr "$pr" --head "$head_oid") \
+                || die $? "brief-PR ${slug}#${pr}: заявка не прошла проверку — мерж не выполняется"
+        else
+            brief_pin=$(cd "$script_dir" && uv run --frozen \
+                python -m governance.brief_tools check-merge \
+                --repo "$slug" --pr "$pr" --head "$head_oid") \
+                || die $? "brief-PR ${slug}#${pr}: заявка не прошла проверку — мерж не выполняется"
+        fi
+        brief_pin=$(printf '%s\n' "$brief_pin" | tail -n 1)
+        case "$brief_pin" in
+            *[!0-9a-f]*|"") die 2 "brief-PR ${slug}#${pr}: проверяльщик не вернул пин политики" ;;
+        esac
+        [ "${#brief_pin}" -eq 40 ] || die 2 "brief-PR ${slug}#${pr}: пин политики не 40 hex"
+        ;;
+esac
 # Версия политики, по которой судится логин. У candidate-PR (форма ветки —
 # из того же SSOT, что у merge-pr.sh) версия ЗАКРЕПЛЕНА заявкой и написана
 # в теле (`policy: <repo>@<sha>`, пишет approve_node): актуальная обязана
@@ -154,6 +180,12 @@ current=$(gh_h api graphql -f 'query=query($o:String!,$n:String!,$q:String!,$p:S
     --jq '.data.repository.ref.target.history.nodes[0].oid' 2>&1) \
     || die 2 "версия политики $p_repo не прочитана: $current"
 case "$head_ref" in
+    brief/*)
+        # Мержер авторизуется по пину, ПРОВЕРЕННОМУ вместе с заявкой; если
+        # актуальная версия ушла после проверки — мерж не создал бы годного
+        # акта (ревью части B, B1).
+        [ "$current" = "$brief_pin" ] || die 3 "политика сменилась после проверки brief-PR (пин $brief_pin, актуальная $current) — мерж не создаст годного акта"
+        version="$brief_pin" ;;
     $finalize_glob)
         # finalize — суффикс candidate-формы, проверяется ПЕРВЫМ: иначе
         # candidate-глоб накрыл бы и его (тот же порядок, что у merge-pr.sh).
```

- [ ] **Step 4: Run — PASS** — `uv run --frozen --group governance pytest tests/test_brief_tools.py tests/test_human_merge.py -q` → 170 passed; мутации (ветка `brief/*` в скрипте, пин политики, голова, self-hash, база) — 5/5 пойманы (проверено).

- [ ] **Step 5: Commit** — `feat(need): human-merge.sh проверяет заявку brief-PR по head SHA (§11.3 п.6)`.

---

### Task 14: Smoke с настоящим discovery, TODO, CLAUDE.md

**Files:**
- Create: `tests/test_engineer_route_smoke.py` (opt-in `DEVTOOLS_DISCOVERY_SMOKE=1`)
- Modify: `TODO.md`, `CLAUDE.md`

```diff
diff --git a/tests/test_engineer_route_smoke.py b/tests/test_engineer_route_smoke.py
new file mode 100644
index 0000000..d41bc13
--- /dev/null
+++ b/tests/test_engineer_route_smoke.py
@@ -0,0 +1,231 @@
+"""Opt-in smoke engineer-маршрута с НАСТОЯЩИМ discovery (спека need-stage §11.7).
+
+Запуск: DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen --group governance pytest -q
+tests/test_engineer_route_smoke.py
+Без переменной — skip: обычный pytest от соседа не зависит. Сосед берётся
+там же, где его берёт `RealOps` (`<devtools>/../discovery`).
+
+- T51: подписанный бриф → `start --upstream --session-id` → банк вопросов →
+  `brief`: итоговый бриф ссылается ровно на `upstream.md`.
+- T38: блокировка прогона живёт, пока жив настоящий `uv → discovery`,
+  даже когда процесс, взявший её, убит.
+- T52: два одновременных `start` с одним id и разными upstream без нашей
+  блокировки — фиксирует поведение соседа для discovery#63 п.3 (не гейт).
+"""
+
+from __future__ import annotations
+
+import os
+import signal
+import subprocess
+import sys
+import time
+from pathlib import Path
+
+import pytest
+import yaml
+
+from governance import brief_input, run_lock
+from governance import interview as iv
+from governance import run_state as rs
+from governance.ops import DEVTOOLS_ROOT, RealOps
+
+pytestmark = pytest.mark.skipif(
+    not os.environ.get("DEVTOOLS_DISCOVERY_SMOKE"),
+    reason="opt-in: DEVTOOLS_DISCOVERY_SMOKE=1",
+)
+
+FIX = Path(__file__).parent / "fixtures" / "discovery_approval"
+SIGNED = (FIX / "signed-brief.md").read_text(encoding="utf-8")
+MUST_FR = ", ".join(f"FR-0{i}" for i in range(1, 9))
+_ANSWERS = {
+    # GC-05 ищет id Must-FR upstream'а в ТЕЛЕ брифа; текст ответа о
+    # feasibility в тело не рендерится — вердикт несёт запись системы.
+    "systems": {
+        "text": "система — раннер",
+        "entries": [{"id": "S-01", "body": f"раннер; выполнимы {MUST_FR}"}],
+    },
+    "interfaces": {
+        "text": "CLI",
+        "entries": [{"id": "IF-01", "body": "CLI раннера", "traces": ["S-01"]}],
+    },
+    "constraints": {
+        "text": "без новых зависимостей",
+        "entries": [{"id": "CON-01", "body": "без новых зависимостей"}],
+    },
+    "arch_preferences": {
+        "text": "stdlib",
+        "entries": [{"id": "AP-01", "body": "stdlib", "traces": ["S-01", "CON-01"]}],
+    },
+    "risks": {"text": "гонки", "entries": [{"id": "RK-01", "body": "гонки"}]},
+    "feasibility_review": {"text": f"{MUST_FR} выполнимы"},
+}
+
+
+def _upstream(tmp_path: Path) -> str:
+    path = tmp_path / "run" / "upstream.md"
+    path.parent.mkdir(parents=True, exist_ok=True)
+    path.write_text(SIGNED, encoding="utf-8")
+    return str(path)
+
+
+def test_real_discovery_engineer_loop(tmp_path: Path, monkeypatch) -> None:  # T51
+    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
+    ops = RealOps()
+    cwd = str(tmp_path / "run")
+    upstream = _upstream(tmp_path)
+    reply = ops.discovery_start(
+        "engineer", "owner/smoke", None, upstream, cwd, session_id="s-smoke-e"
+    )
+    assert reply.code == 20, reply
+    assert reply.envelope["next_action"]["session_id"] == "s-smoke-e"
+    for _ in range(60):
+        action = reply.envelope["next_action"]
+        answer = tmp_path / "answer.yaml"
+        payload = _ANSWERS.get(
+            action.get("coverage_key", ""), {"text": f"ответ {MUST_FR}"}
+        )
+        answer.write_text(yaml.safe_dump(payload, allow_unicode=True), "utf-8")
+        got = ops._discovery(
+            ["answer", "--session", "s-smoke-e", "--role", "po", "--file", str(answer)],
+            cwd,
+        )
+        assert got.code in (0, 20, 10, 11), got
+        reply = ops.discovery_status("s-smoke-e", cwd)
+        if reply.code != 20:
+            break
+    out = Path(cwd) / "brief.md"
+    final = ops.discovery_brief("s-smoke-e", str(out), cwd)
+    assert final.code == 0, final.envelope.get("findings")
+    meta = yaml.safe_load(out.read_text(encoding="utf-8").split("---\n")[1])
+    assert meta["traces_to"] == [iv.UPSTREAM_NAME]
+    spec = iv.InterviewSpec("engineer", "po", "owner/smoke", iv.UPSTREAM_NAME, None)
+    assert iv.brief_coordinate_findings(out.read_text(encoding="utf-8"), spec) == []
+    # E1 проходит: бриф рядом с durable upstream.md — source-слой из двух файлов.
+    source = brief_input.inspect_brief(out)
+    assert source.source_paths == (
+        "00-discovery/brief.md",
+        "00-discovery/upstream.md",
+    )
+
+
+_HOLDER = """
+import subprocess, sys
+from pathlib import Path
+from governance import run_lock, run_state as rs
+rs.RUNS_ROOT = Path(sys.argv[1])
+read_fd, project, session = int(sys.argv[2]), sys.argv[3], sys.argv[4]
+with run_lock.run_lock("r-smoke") as lock:
+    child = subprocess.Popen(
+        ["uv", "run", "--frozen", "--project", project, "discovery", "answer",
+         "--session", session, "--role", "po", "--file", "-"],
+        stdin=read_fd, pass_fds=(lock.fd,), stdout=subprocess.DEVNULL,
+        stderr=subprocess.DEVNULL,
+    )
+    print(child.pid, flush=True)
+    child.wait()
+"""
+
+
+def test_lock_lives_while_real_discovery_lives(
+    tmp_path: Path, monkeypatch
+) -> None:  # T38
+    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
+    runs = tmp_path / "runs"
+    monkeypatch.setattr(rs, "RUNS_ROOT", runs)
+    reply = RealOps().discovery_start(
+        "customer", "owner/smoke", None, None, str(tmp_path)
+    )
+    session = reply.envelope["next_action"]["session_id"]
+    read_fd, write_fd = os.pipe()
+    holder = subprocess.Popen(
+        [
+            sys.executable,
+            "-c",
+            _HOLDER,
+            str(runs),
+            str(read_fd),
+            str(DEVTOOLS_ROOT.parent / "discovery"),
+            session,
+        ],
+        pass_fds=(read_fd,),
+        stdout=subprocess.PIPE,
+        text=True,
+        cwd=DEVTOOLS_ROOT,
+        env={**os.environ, "DISCOVERY_HOME": str(tmp_path / "home")},
+    )
+    os.close(read_fd)
+    assert holder.stdout is not None
+    holder.stdout.readline()  # uv → discovery запущен и ждёт stdin
+    time.sleep(1.0)
+    holder.send_signal(signal.SIGKILL)  # родитель, взявший блокировку, убит
+    holder.wait()
+    with pytest.raises(run_lock.LockBusy):  # discovery жив — блокировка держится
+        with run_lock.run_lock("r-smoke"):
+            pass
+    os.close(write_fd)  # EOF: discovery дочитывает stdin и выходит
+    deadline = time.monotonic() + 30
+    while True:
+        try:
+            with run_lock.run_lock("r-smoke"):
+                break
+        except run_lock.LockBusy:
+            assert time.monotonic() < deadline, "блокировка не отпущена после выхода"
+            time.sleep(0.2)
+
+
+def _signed_variant(name: str) -> str:
+    """Отдельный корректно подписанный upstream (своя подпись `stamp` соседа
+    над своими байтами): T52 требует РАЗНЫЕ upstream у двух вызовов."""
+    from governance import discovery_approval as da
+
+    draft = (FIX / "draft-brief.md").read_text(encoding="utf-8") + "\n" * (
+        1 if name == "a" else 2
+    )
+    event = da._approval.MergeEvent(
+        "andrei-shtanakov", "2026-10-08T10:00:00Z", "c" * 40
+    )
+    return da._approval.stamp(draft, event)
+
+
+@pytest.mark.xfail(
+    strict=False, reason="discovery#63 п.3: достройка резервации не эксклюзивна"
+)
+def test_concurrent_start_same_id_is_exclusive(
+    tmp_path: Path, monkeypatch
+) -> None:  # T52
+    home = tmp_path / "home"
+    monkeypatch.setenv("DISCOVERY_HOME", str(home))
+    project = str(DEVTOOLS_ROOT.parent / "discovery")
+    procs = []
+    for name in ("a", "b"):
+        upstream = tmp_path / name / "upstream.md"
+        upstream.parent.mkdir()
+        upstream.write_text(_signed_variant(name), encoding="utf-8")
+        procs.append(
+            subprocess.Popen(
+                [
+                    "uv",
+                    "run",
+                    "--frozen",
+                    "--project",
+                    project,
+                    "discovery",
+                    "start",
+                    "--frame",
+                    "engineer",
+                    "--target",
+                    "owner/smoke",
+                    "--upstream",
+                    str(upstream),
+                    "--session-id",
+                    "s-race",
+                ],
+                stdout=subprocess.PIPE,
+                stderr=subprocess.PIPE,
+                text=True,
+            )
+        )
+    codes = sorted(p.wait() for p in procs)
+    print(f"коды двух одновременных start: {codes}")
+    assert codes.count(20) == 1  # эксклюзивность — то, о чём просит discovery#63
```

```diff
diff --git a/TODO.md b/TODO.md
index 7ab21b4..e40982e 100644
--- a/TODO.md
+++ b/TODO.md
@@ -1836,6 +1836,13 @@ spec-runner#334/#335/#336/#337; соседям — dispatcher#251 (lint-хук).
       Спека need-stage приведена в соответствие: удаление файла больше не
       привязано к переходу 20 ни в таблице §5.1, ни в §5.3.
 - [ ] E2 Engineer-маршрут стадии Need: `--frame engineer --traces-to <approved customer-brief>` — preflight с явной проверкой `status: approved`, durable-копия upstream в `brief-input/00-discovery/` с `upstream_blob`, `discovery_start(..., upstream_path)` на копию; маршрут отказывает до run-id, пока не реализован @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:spec-loop-need-engineer-route
+      **Ревизия 7 спеки (2026-10-08):** маршрут — по §11 спеки need-stage
+      (вариант А: engineer отдельным прогоном, `--approval-pr`, доказательства
+      только по форджу, write-ahead id, блокировка прогона); план —
+      `docs/superpowers/plans/2026-10-08-need-stage-engineer-route-plan-{a,b}.md`.
+      Чекбокс — после живой приёмки §11.8 (гарантия происхождения — только
+      engineer `--need`; невосстановимость после потери ответа `start` до
+      discovery#63 — принятое ограничение).
       **Разблокирован 2026-09-18.** Ждал п.1 discovery#49 (приём upstream при
       `start --frame engineer`); сосед доставил его PR-ом discovery#50
       (`49dbc2a`, master) вместе с п.2 — пункт продюсера
@@ -1867,6 +1874,10 @@ spec-runner#334/#335/#336/#337; соседям — dispatcher#251 (lint-хук).
       маршрута, тем же PR.
 
 - [ ] Контур approval discovery-брифа: назвать акт, которым бриф получает `status: approved`, и место его подписи @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:discovery-brief-approval-act
+      **2026-10-08:** «подкоманды approve у соседа нет» устарело — discovery#57
+      доставил `discovery approve`. Остаток на нашей стороне — brief-PR с
+      заявкой (`make brief-propose`), `make brief-approve`, проверка заявки в
+      `human-merge.sh` (§11.3, §11.5 спеки); закрывается вместе с engineer-route.
       Сегодня цепочка customer → engineer внутри одного прогона держится на
       ручной правке frontmatter: `--need` выпускает бриф со `status: draft`
       (D5 дизайна need-stage — автоматически в `approved` он не превращается),
@@ -1895,6 +1906,14 @@ spec-runner#334/#335/#336/#337; соседям — dispatcher#251 (lint-хук).
       Кода не блокирует — делает engineer-маршрут недостижимым без ручной
       правки файла.
 
+- [ ] Write-ahead id сессии и для customer-маршрута (сейчас — только engineer, §11.4.5; customer остаётся на `--session`, §5.3) @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:need-customer-write-ahead-id
+- [ ] `--repropose` brief-PR: новое предложение, когда прежний закрыт без мержа или его пин политики устарел (§11.10 Q4, вне E2) @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:brief-repropose
+- [ ] Известная дыра: E1 `--brief` с готовым engineer-брифом проверяет у upstream только `status: approved`, не происхождение подписи (гарантия §11 — только engineer `--need`, §11.10 Q5) @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:e1-brief-engineer-provenance
+- [ ] Присоединение существующей engineer-сессии по отпечатку принятого upstream вместо стопа `session_unverifiable` (§11.4.5) @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:need-engineer-session-attach @blocked_by:discovery#63
+      Живой замер 2026-10-08 (smoke T52): два одновременных `start` с одним id
+      дали `[20, 20]` в двух прогонах из трёх — гонка достройки резервации у
+      соседа реальна (discovery#63 п.3); наша блокировка прогона закрывает её
+      только для собственных процессов.
 - [x] Контрольный прогон хвоста S7: дешёвый `spec-loop --brief` на крошечном предмете доказывает §9.2 спеки need-stage — раннер САМ дошёл до `waiting_human_merge`, мерж бандл-PR сделан через `make human-merge` (учётка человека из `AUTHORIZED_APPROVER_ACCOUNTS`, сверка логина), `resume` подтвердил факт и перевёл на S8, S8 `exit=0` (не `merged_unverified`), заведён draft tasks-PR, получена approved tasks-спека; негативный контроль — мерж в обход раннера реконсилируется `_reconcile_pr_merged_out_of_band`, а не теряется @owner:github:andrei-shtanakov @epic:eco.dark-factory @id:s7-control-run — прогон `review-pr-unreachable-base-coverage-20260921-3f8af3`, PR этой ветки
       **Штатный путь доказан живьём.** Предмет — реальный открытый хвост
       `@id:review-scope-unreachable-base-coverage`, вход `--brief`, цель —
```

```diff
diff --git a/CLAUDE.md b/CLAUDE.md
index 223fd57..65f5f83 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -52,7 +52,7 @@
 | `check-plan-fields.py` | кросс-репный граф `@blocked_by` — ловит пункт, ждущий уже отгруженного (режим отказа R-03) |
 | `check-arch-evidence-freshness.py` | drift вендоренных prograph-схем steward + freshness evidence WS-005; `--read` — просрочка ⇒ unknown |
 | `merge-pr.sh` | единственный разрешённый путь агентского мержа (ADR-ECO-011) — как правило и defense-in-depth, не security boundary: намеренный обход прямым merge-API не предотвращается, настоящая граница только на стороне форджи (devtools#184); в т.ч. для `accept-pr` и S7 раннера через `Ops.merge`; сам скрипт и его SSOT `contracts/approval-branches/` — харнесс-пути (`_HARNESS_PREFIXES`): PR, который их трогает, агентом не ревьюится и не мержится, иначе контур исполнил бы гвард из проверяемого дерева. PR из форка с `--delete-branch` — отказ до мержа (ветка форка живёт в чужом репо); четвёртый категорический отказ — дифф, трогающий authority-root пути (перечень — SSOT `contracts/authority-root/v1/paths.env`, его же читают `accept_pr` и раннер): сверяет профиль `ai-prosto`, отказывает на candidate-ветках заявки одобрения §I12 и на лейбле `human-merge-required` (finalize-форма с ADR-ECO-011 D5 не отказ: её мержит `--approve-node` с пином на свой конверт), мержит прямым `PUT /pulls/{n}/merge` с пином головы `sha=` (не `gh pr merge` — решение дизайна 2026-08-30 §8: тот при `BLOCKED` отказывает сам, не проверив bypass актора); `--expect-head`/`--expect-base` — пины вызывающего, база без `--expect-base` НЕ проверяется и об этом говорится вслух; стратегии — закрытый allowlist `--squash\|--merge\|--rebase` (+`--delete-branch`), свободного passthrough нет. Формы веток одобрения читаются из `contracts/approval-branches/v1/patterns.env` — того же файла, из которого их строит `governance/approval_branches.py` |
-| `human-merge.sh` | триггер ЧЕЛОВЕЧЕСКОГО мержа по команде (ADR-ECO-011 D6): мерж от профиля оператора, `~/.config/review` — отказ; логин сверяется с политикой `approval-policy` по пину из тела candidate-PR. **Защиту ветки не обходит** — `--admin` нет и не будет (devtools#277, решение владельца 2026-09-23): под ruleset'ом с обязательным ревью мерж пройдёт только при наличии ревью на PR. В волновом режиме (с 2026-09-23 единственном) оно есть всегда — candidate несёт одобряющее ревью edge-check, и за две живые приёмки `--admin` не понадобился ни разу; прежний путь, где у candidate ревью не было, удалён из исполнения тем же решением. «Единственным способом совершить акт» команда не называется — это было бы обещанием, которого контур не держит |
+| `human-merge.sh` | триггер ЧЕЛОВЕЧЕСКОГО мержа по команде (ADR-ECO-011 D6): для веток `brief/*` (brief-PR, спека need-stage §11.3 п.6) сперва `governance.brief_tools check-merge` проверяет заявку на одобрение по head SHA (заявка из содержимого, не из тела/меток; пин политики == актуальной), мерж — с пином той же головы; мерж от профиля оператора, `~/.config/review` — отказ; логин сверяется с политикой `approval-policy` по пину из тела candidate-PR. **Защиту ветки не обходит** — `--admin` нет и не будет (devtools#277, решение владельца 2026-09-23): под ruleset'ом с обязательным ревью мерж пройдёт только при наличии ревью на PR. В волновом режиме (с 2026-09-23 единственном) оно есть всегда — candidate несёт одобряющее ревью edge-check, и за две живые приёмки `--admin` не понадобился ни разу; прежний путь, где у candidate ревью не было, удалён из исполнения тем же решением. «Единственным способом совершить акт» команда не называется — это было бы обещанием, которого контур не держит |
 | `review-pr.sh` | терминальный прогон ревью PR через review-kit целевого репо + публикация вердикта как PR review от ai-prosto (профиль `~/.config/review`); харнесс ревьюера настраиваем: `--harness claude\|codex` / env / `~/.config/ai-prosto/harness.env` (свойство машины/подписки, вшитый дефолт codex); свежему киту (`local.sh --print-review-cmd`, steward @ a2d7e71) харнесс уходит окружением `REVIEW_HARNESS`/`REVIEW_MODEL`, строка ревьюера берётся у кита (`harness-claude --model …`); копия кита без харнесс-слоя умеет только codex (`REVIEW_CMD` как раньше), claude на ней — отказ «ре-вендорьте кит» (переходник `scripts/harness/claude-review` снят волной devtools#228, шаг 2 devtools#222); `--dry-run` — показать, не постить; opt-in `--dry-run --write-verdict <file>` → `--use-verdict <file>` переносит тот же проверенный результат без второго вызова ревьюера только при точных `head + fp`. Потолки дифа кита (умолчания `build-prompt.sh`: 400000 байт / 30 файлов) поднимаются явно: `--max-diff-bytes N`/`--max-diff-files N` (флаг > env `REVIEW_MAX_DIFF_BYTES`/`REVIEW_MAX_DIFF_FILES`); один набор уходит в оба вызова кита (отпечаток и полный прогон), факт поднятия печатается в шапке вердикта; S6 раннера берёт потолки из env вызова `make behaviour-run` (`RealOps.review` без параметров). Литерал маркера `codex-terminal-review` — имя протокола, НЕ бинаря: не переименовывать |
 | `attest-vendor.sh` | детерминированная аттестация целостности вендор-копии review-kit на волновом PR ре-вендора (решение владельца 2026-09-19) — публикует `--approve` от `ai-prosto` (тот же профиль, что у `review-pr.sh`), формально закрывая требование одобряющего ревью там, где содержательного ревью нет и не нужно: предмет — побайтовая копия файлов кита из апстрима. Материализация головы PR и профиль публикации — тем же приёмом, что у `review-pr.sh` (служебный ref `refs/attest/pr-<N>`, свой, + временный detached worktree, рабочее дерево целевого репо не трогается). Ни один факт, определяющий исход, не приходит из дерева проверяемого PR: `# SOURCE: steward @ <sha>` из `scripts/review/PIN` головы (сокращённый SHA — формат, реально принятый во флоте — резолвится в единственный коммит `git rev-parse --verify` против доверенного `../steward`, Git сам откажет на неоднозначном префиксе и несуществующем коммите) — только санитарная проверка провенанса (обязан существовать и быть предком `origin/master` steward), а не якорь сравнения. И полнота PIN (`checksum.sh`, ИЗВЛЕЧЁННЫЙ из ТЕКУЩЕГО апстрима, не из дерева PR — иначе PR, сузивший свою же копию чекера, прошёл бы зелёным), и побайтовая/режимная сверка (по git-объектам `ls-tree`: mode + blob-oid, не по чекауту) идут против ТЕКУЩЕГО `origin/master` steward, а не против названного в PIN коммита — так откат на старый, но валидный коммит закрывается по существу (старые байты не совпадут с сегодняшним апстримом), а PR, не менявший кит, остаётся валиден при любом постороннем движении мастера steward; побочный эффект: пока волна ре-вендора едет, реальное изменение кита в steward делает открытые PR волны недействительными до повторного прогона. Диапазон PR (`base`..head) обязан целиком лежать внутри состава кита — посторонний путь отказывает кодом 3. Доставленный `CHANGES_REQUESTED` от `ai-prosto` не гасится синтетическим approve; повтор на той же голове идемпотентен (дедуп по собственному маркеру). Тело называет честное число — N присутствующих из M членов апстрима (M — из `required_kit_default` апстримного `checksum.sh`), легально отсутствующий переходный член (`?path`, двухшаговый ре-вендор состава кита) называется поимённо, а не растворяется в «все совпали»; в тело идёт ТОЛЬКО собственный вычисленный текст (вывод чужого, даже апстримного, кода — только в stderr), страж перед публикацией отказывает при лишней `<!--`-последовательности. Маркер — собственный (`ai-prosto-vendor-attestation`), без подстроки `<!-- codex-terminal-review `. **Аттестуется только PR, состоящий РОВНО из членов кита и его PIN**: PR, везущий что-то ещё (например `.github/codex/review-prompt.md` — репо-данные, намеренно вне инвентаря), получает отказ кодом 3 и остаётся предметом обычного ревью. Коды выхода: 0 — чисто (или `--dry-run`, или дедуп), 2 — конфигурация/аргументы/состояние PR/публикация/неустановленный факт, 3 — сверка не прошла (апстрим не предок доверенной ветки, PR трогает пути вне кита, расхождение с текущим апстримом, красный `checksum.sh`), 4 — голова уехала между сверкой и публикацией |
 | `governance/criteria_close.py` | закрытие воркстрима DarkFactory по оракулу бандла (`make criteria-close ARGS='--run <id> [--product-sha <sha>]'`, спека `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md`): после исполнения задач — `spec-runner verify --criteria` (контракт `criteria-closure/v1` вендорен @ spec-runner v4.5.0; оракул применим при установленном spec-runner ≥ 4.5.0, ниже — `not-applicable: spec-runner-version`), сверка ответа devtools'ом по своим байтам, файл `90-acceptance-closure.md` агентским PR из временного worktree от `origin/<base>`; бандл читается по пину, продукт — только чистый чекаут ровно на `product_sha`; флага обхода стопа нет. Срез 2a (спека §7.2a): закрытие `traced` — предложение (`status: proposed`, снимок политики, свой коммит на ветке `…-proposal`); PR с человеческими критериями (`human_criteria` по графу) — метка `human-merge-required`, мержит человек из снимка (выход 4 — ждём); приёмку ведёт `_advance` по фактам форджи: предикат §3.5, stamp-PR `status: accepted`; выходы 0/2/4/5/6; незавершённое закрытие среза 1 мигрирует в предложение без перемера. Гейт `[x]` на `traced` проверяет происхождение штампа (`acceptance_provenance`: акт, связанный с закрытием; политика по пину из SSOT; граф по пину) — нужны история git и чтение форджи. `charter_guard` — charter схемы 2 (`code`, `plan_item`): реестр кодов — сами charter'ы (удалять/переносить нельзя, коллизия — нарушитель позже влитый). `closure_gate` — `[x]` пункта плана на `traced` требует штамп, доказанный `acceptance_provenance` (не просто файл закрытия не `blocked`, как в срезе 1); словарь n/a поддержан и для `closed`; `plan_item` неизменяем — держит `charter_guard` |
```

- [ ] **Run** — `DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen --group governance pytest tests/test_engineer_route_smoke.py -q -rxX` (сосед — `<devtools>/../discovery`): T51 и T38 — PASS на `discovery@94c88cc` (T51 требует код 0 у `brief` и проходящий E1 рядом с
`upstream.md`; T38 держит настоящий `discovery answer --file -` на открытом stdin); T52 —
xfail на двух РАЗНЫХ корректно подписанных upstream: замер `[20, 20]`, `[1, 20]`, `[1, 20]`
(проверено). Без `DEVTOOLS_DISCOVERY_SMOKE=1` все три — skip.

- [ ] **Commit** — `docs(need): smoke engineer-маршрута, TODO Q3–Q5 и discovery#63`.

---

## Матрица §11.7 → тест → задача (обе части)

Явное отображение (`plan-exec/tmap.py` в журнале исполнения): для каждого T перечислены
ВСЕ функции, чьи проверки его закрывают; скрипт проверяет, что каждая функция есть на
вершине, и указывает задачу, в которой она появилась, или «база» — тест существовал до
E2 (регрессия). `[act-*]`/`[operator-*]`/`[policy-*]` — параметризации матрицы через
`spec_loop.main`. T38 (живой smoke), T51, T52 — opt-in.

| T | Задача | Тест (`tests/…`) |
|---|---|---|
| T1 | 6 | `test_governance_ops.py::test_discovery_start_engineer_upstream_and_session_id` (6) |
| T2 | 6 | `test_governance_ops.py::test_discovery_start_upstream_with_traces_to_is_synthetic_1` (6) |
| T3 | 6 | `test_governance_ops.py::test_discovery_approve_impossible_forms_are_synthetic_1` (6)<br>`test_governance_ops.py::test_discovery_approve_argv_and_refusal_code` (6) |
| T4 | 3 | `test_governance_ops_brief_facts.py::test_files_pagination_followed` (3)<br>`test_governance_ops_brief_facts.py::test_incomplete_pages_unavailable` (3)<br>`test_governance_ops_brief_facts.py::test_page_without_pageinfo_unavailable` (3) |
| T5 | 7 | `test_governance_spec_loop.py::test_brief_only_invalid_combinations_refuse_before_run` (7)<br>`test_governance_spec_loop.py::test_brief_only_customer_starts_with_flag` (7) |
| T6 | 7 | `test_governance_spec_loop.py::test_repeat_with_other_brief_only_refuses` (7) |
| T7 | 7 | `test_governance_runner.py::test_brief_only_publication_ends_in_brief_ready` (7) |
| T8 | 7 | `test_governance_runner.py::test_brief_ready_resume_has_no_effects` (7)<br>`test_governance_spec_loop.py::test_brief_ready_on_entry_prints_next_step_without_resume` (7) |
| T9 | 7 | `test_governance_runner.py::test_crash_before_brief_ready_write_recovers_to_brief_ready` (7) |
| T10 | 7 | `test_governance_runner.py::test_customer_without_brief_only_still_goes_to_s1` (7) |
| T11 | база, 7 | `test_governance_spec_loop.py::test_new_run_allowed_next_to_brief_ready` (7)<br>`test_governance_spec_loop.py::test_new_run_refused_when_a_match_reached_s1` (база) |
| T12 | 8 | `test_governance_spec_loop.py::test_engineer_next_to_customer_run_needs_new_run` (8) |
| T13 | база | `test_governance_spec_loop.py::test_need_preflight_refuses_before_run_id` (база) |
| T14–T16, T18 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[operator-*]` (8)<br>`test_governance_spec_loop.py::test_engineer_preflight_refusals` (8)<br>`test_brief_provenance.py::test_operator_brief_defects` (4) |
| T17 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_accepts_consistent_world` (8)<br>`test_brief_provenance.py::test_manual_envelope_mirroring_the_merge_is_accepted` (4) |
| T19, T20, T25 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[act-*]` (8)<br>`test_brief_provenance.py::test_each_act_defect_refuses` (4) |
| T21 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[act-coords-*]` (8)<br>`test_governance_spec_loop.py::test_engineer_preflight_ambiguous_merged_request` (8)<br>`test_brief_provenance.py::test_merged_request_defects_refuse` (4) |
| T21a | 4, 8, 12 | `test_governance_spec_loop.py::test_engineer_preflight_ambiguous_merged_request` (8)<br>`test_brief_tools.py::test_browser_merged_ambiguous_request_refuses` (12)<br>`test_brief_provenance.py::test_merged_request_defects_refuse` (4) |
| T21b | 2, 4, 8, 11, 12, 13 | `test_approval_request.py::*` (2)<br>`test_brief_provenance.py::test_merged_request_defects_refuse` (4)<br>`test_governance_spec_loop.py::test_engineer_preflight_ambiguous_merged_request` (8)<br>`test_brief_tools.py::test_found_pr_with_ambiguous_request_refuses` (11)<br>`test_brief_tools.py::test_browser_merged_ambiguous_request_refuses` (12)<br>`test_brief_tools.py::test_check_merge_ambiguous_request` (13) |
| T21c | 3, 8 | `test_governance_ops_brief_facts.py::test_policy_version_at_found` (3)<br>`test_governance_ops_brief_facts.py::test_policy_version_at_not_in_history` (3)<br>`test_governance_ops_brief_facts.py::test_policy_version_at_unavailable` (3)<br>`test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[act-p-not-version]` (8) |
| T22 | 8 | `test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[act-brief-hash]` (8)<br>`test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[operator-*-other-brief]` (8) |
| T23 | 8 | `test_governance_spec_loop.py::test_preflight_facts_carry_no_pr_body_or_labels` (8) |
| T24 | 8 | `test_governance_spec_loop.py::test_engineer_main_decides_by_forge_not_local_git` (8)<br>`test_governance_spec_loop.py::test_engineer_preflight_reads_no_local_git` (8) |
| T26 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[policy-drift-no-reconfirm]` (8)<br>`test_brief_provenance.py::test_drift_without_reconfirm_stops` (4) |
| T27 | 4, 8 | `test_governance_spec_loop.py::test_engineer_preflight_valid_reconfirm_passes` (8)<br>`test_governance_spec_loop.py::test_engineer_main_refuses_every_provenance_defect[policy-*]` (8)<br>`test_brief_provenance.py::test_valid_reconfirm_continues` (4)<br>`test_brief_provenance.py::test_each_policy_defect_refuses` (4) |
| T28 | 4, 9, 10 | `test_governance_runner.py::test_reconfirm_sequence_continues_after_second_reconfirm` (10)<br>`test_governance_runner.py::test_reconfirm_resume_drift_sequence` (9)<br>`test_brief_provenance.py::test_latest_reconfirm_wins` (4) |
| T29 | 9, 10 | `test_governance_runner.py::test_forged_local_confirmation_does_not_mask_drift` (10)<br>`test_governance_runner.py::test_policy_drift_between_visits_stops` (9) |
| T30 | 9, 10 | `test_governance_runner.py::test_act_address_changed_in_ledger_is_rechecked_from_forge` (10)<br>`test_governance_runner.py::test_consistent_local_tamper_cannot_pass` (9)<br>`test_governance_runner.py::test_reconfirm_deleted_or_edited_on_resume_stops` (10) |
| T31 | 4, 8 | `test_governance_spec_loop.py::test_engineer_main_unavailable_each_fact` (8)<br>`test_brief_provenance.py::test_unavailable_is_retry_not_rejection` (4)<br>`test_brief_provenance.py::test_policy_unavailable_is_retry` (4) |
| T32 | 8 | `test_governance_spec_loop.py::test_engineer_preflight_refusals` (8)<br>`test_governance_spec_loop.py::test_engineer_preflight_upstream_gate_refusals` (8) |
| T33 | 8 | `test_governance_spec_loop.py::test_operator_symlink_read_once` (8)<br>`test_governance_spec_loop.py::test_engineer_new_run_passes_intake_and_upstream_blob` (8)<br>`test_governance_runner.py::test_start_writes_upstream_copy_from_intake_buffer` (8)<br>`test_governance_runner.py::test_start_refuses_intake_blob_mismatch` (8) |
| T34 | 9 | `test_governance_runner.py::test_upstream_copy_tamper_stops_before_neighbor` (9) |
| T35 | 10 | `test_governance_runner.py::test_policy_change_after_completed_at_does_not_stop` (10) |
| T36 | 9 | `test_governance_runner.py::test_session_id_mismatch_stops` (9)<br>`test_governance_runner.py::test_session_id_tamper_after_start_stops` (9) |
| T37 | 5, 12 | `test_run_lock.py::test_second_entry_exits_before_reading_state` (5)<br>`test_run_lock.py::test_runner_cli_resume_exits_before_reading_state` (5)<br>`test_brief_tools.py::test_busy_run_exits_1_before_reading_state` (12) |
| T37a | 5 | `test_run_lock.py::test_runner_functions_refuse_without_token` (5) |
| T37b | 5 | `test_run_lock.py::test_state_is_reread_under_lock` (5) |
| T38 | 5, 9, 14 | `test_engineer_route_smoke.py::test_lock_lives_while_real_discovery_lives` (14)<br>`test_governance_runner.py::test_every_neighbor_call_in_engineer_flow_passes_lock_fd` (9)<br>`test_governance_runner.py::test_only_the_discovery_port_passes_descriptors` (9)<br>`test_governance_runner.py::test_git_and_gh_children_do_not_hold_the_lock` (9)<br>`test_run_lock.py::test_lock_survives_parent_death_via_pass_fds` (5)<br>`test_run_lock.py::test_other_children_do_not_inherit` (5) |
| T39 | 9 | `test_governance_runner.py::test_recovery_with_existing_session_is_unverifiable` (9) |
| T40 | 1 | `test_discovery_approval.py::*` (1) |
| T41 | 9, 10 | `test_governance_runner.py::test_recovery_unknown_status_is_hard_stop` (9)<br>`test_governance_runner.py::test_recovery_status_without_envelope_is_hard_stop` (10) |
| T42 | 9 | `test_governance_runner.py::test_engineer_brief_must_trace_only_upstream` (9) |
| T43 | 9, 10 | `test_governance_runner.py::test_e1_proceeds_with_intact_upstream` (10)<br>`test_governance_runner.py::test_e1_refuses_tampered_upstream_after_publication` (10)<br>`test_governance_runner.py::test_e1_refuses_descriptor_not_matching_upstream_blob` (10)<br>`test_governance_runner.py::test_engineer_publication_reaches_s1_with_upstream_source` (9) |
| T44 | 12 | `test_brief_tools.py::test_approve_success_prints_engineer_command` (12) |
| T44a | 12 | `test_brief_tools.py::test_drift_before_first_approve_then_reconfirm` (12) |
| T44b | 12 | `test_brief_tools.py::test_drift_after_approve_blocks_engineer_until_reconfirm` (12) |
| T45 | 12 | `test_brief_tools.py::test_approve_failures_print_no_engineer_command` (12)<br>`test_brief_tools.py::test_approve_each_refusal_reason` (12) |
| T46 | 12 | `test_brief_tools.py::test_policy_moved_during_approve` (12)<br>`test_brief_tools.py::test_policy_unavailable_after_approve` (12)<br>`test_brief_tools.py::test_policy_switch_to_reconfirmed_version_during_approve` (12) |
| T47 | 11 | `test_brief_tools.py::test_merged_pr_with_our_content_is_reported` (11)<br>`test_brief_tools.py::test_found_proposal_with_our_content_is_ours` (11)<br>`test_brief_tools.py::test_open_pr_without_drift_is_reported_without_warning` (11)<br>`test_brief_tools.py::test_open_pr_with_drifted_pin_keeps_pin_and_warns` (11)<br>`test_brief_tools.py::test_closed_pr_refuses_with_repropose_hint` (11)<br>`test_brief_tools.py::test_branch_without_pr_creates_only_pr` (11)<br>`test_brief_tools.py::test_branch_without_pr_after_base_moved_is_still_ours` (11)<br>`test_brief_tools.py::test_nothing_exists_creates_two_file_pr` (11)<br>`test_brief_tools.py::test_foreign_file_in_base_refuses` (11)<br>`test_brief_tools.py::test_two_prs_on_branch_is_ambiguous` (11)<br>`test_brief_tools.py::test_branch_with_foreign_files_refuses` (11)<br>`test_brief_tools.py::test_branch_moved_between_check_and_pr_is_not_success` (11)<br>`test_brief_tools.py::test_recovery_compares_files_at_the_checked_sha` (11)<br>`test_brief_tools.py::test_push_failure_is_retry_and_rerun_recovers` (11)<br>`test_brief_tools.py::test_create_pr_failure_then_rerun_creates_only_pr` (11)<br>`test_brief_tools.py::test_local_commit_with_extra_path_refuses_before_push` (11) |
| T48 | 11 | `test_brief_tools.py::test_found_proposal_with_foreign_content_refuses` (11)<br>`test_brief_tools.py::test_found_proposal_with_our_content_is_ours` (11)<br>`test_brief_tools.py::test_found_pr_with_foreign_policy_coordinates_refuses` (11) |
| T48a | 3 | `test_governance_ops_brief_facts.py::test_impossible_shapes_unavailable` (3)<br>`test_governance_ops_brief_facts.py::test_incomplete_pages_unavailable[head-moved]` (3) |
| T49 | 11 | `test_brief_tools.py::test_run_preconditions` (11) |
| T50 | 13 | `test_brief_tools.py::test_check_merge_accepts_consistent_head` (13)<br>`test_brief_tools.py::test_check_merge_refusals` (13)<br>`test_brief_tools.py::test_check_merge_ambiguous_request` (13)<br>`test_brief_tools.py::test_check_merge_head_changed_is_retry` (13)<br>`test_brief_tools.py::test_check_merge_unavailable_is_retry` (13)<br>`test_brief_tools.py::test_check_merge_ignores_body_and_labels` (13)<br>`test_brief_tools.py::test_check_merge_cli_exit_codes` (13)<br>`test_human_merge.py::test_brief_pr_is_checked_by_head_then_merged_with_same_sha` (13)<br>`test_human_merge.py::test_brief_pr_refusal_stops_before_merge` (13)<br>`test_human_merge.py::test_brief_pr_check_runs_under_human_profile` (13)<br>`test_human_merge.py::test_brief_pr_policy_moved_after_check_refuses` (13)<br>`test_human_merge.py::test_brief_pr_merger_is_authorized_by_the_checked_pin` (13)<br>`test_human_merge.py::test_brief_pr_without_pin_from_checker_refuses` (13)<br>`test_human_merge.py::test_brief_pr_head_race_then_recheck_of_new_head` (13) |
| T50a | база, 13 | `test_human_merge.py::test_candidate_and_other_prs_do_not_call_the_brief_check` (13)<br>`test_human_merge.py::test_merges_from_operator_profile_with_head_pin` (база)<br>`test_human_merge.py::test_policy_changed_after_candidate_is_refused_before_merge` (база)<br>`test_human_merge.py::test_body_without_policy_line_is_refused` (база)<br>`test_human_merge.py::test_head_pin_mismatch_refuses` (база)<br>`test_human_merge.py::test_non_approval_branch_needs_no_pin` (база)<br>`test_human_merge.py::test_env_variable_is_refused` (база) |
| T51 | 14 | `test_engineer_route_smoke.py::test_real_discovery_engineer_loop` (14) |
| T52 | 14 | `test_engineer_route_smoke.py::test_concurrent_start_same_id_is_exclusive` (14) |

## Self-Review части B

- **Покрытие §11:** §11.2 → Task 7; §11.3 → Task 11, 13; §11.4.1–§11.4.2 → Task 8;
  §11.4.3 → Task 9–10 (+ часть A); §11.4.4 → Task 8–10; §11.4.5 → Task 9 (+ часть A);
  §11.4.6 → часть A + `create_pr(base)`; §11.5 → Task 12; §11.9 — discovery#63 (заведена,
  живой замер T52 — комментарием в issue и в TODO); §11.10 Q3–Q5 → TODO.
- **Тесты §11.7:** каждый T1–T52 (с подпунктами) имеет тест — таблица выше; ни одного
  T без теста.
- **Проверено исполнением:** каждая задача на своей ревизии — ruff и свои тесты (числа в
  шагах), мутации ключевых условий; полный набор `GOVERNANCE_REQUIRED=1 uv run --frozen
  --group governance --group selfcheck pytest -q` на итоговой вершине A+B `8654c34` →
  **4784 passed, 9 skipped** (пропуски — opt-in пробы соседей, в т.ч. три smoke этого плана);
  отдельный полный прогон части A — на `d952b16` (4502 passed, 6 skipped), до тестового
  рефакторинга Task 4 (`tests/provenance_cases.py`), который вошёл в прогон A+B; pyrefly — новых ошибок нет, кроме
  `missing-import` вендоренной копии соседа и dev-инструментов `tools/`.
- **НЕ проверено исполнением (остаётся приёмкой реализации):** живая приёмка §11.8 (после
  мержа реализации); `make brief-propose`/`brief-approve` и `human-merge.sh` против
  настоящего GitHub — обёртки проверены на стенде форджа и записанных ответах `gh`;
  T38/T51/T52 исполнены вручную в opt-in-режиме на `discovery@94c88cc`, в обычном прогоне
  — skip; T52 — xfail до discovery#63 п.3.
