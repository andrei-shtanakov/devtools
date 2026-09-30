---
traces_to:
- design
spec_stage: tasks
status: approved
version: 2
generated_by: claude-session
generated_at: '2026-09-30T18:00:00+04:00'
source_prompt_version: ''
validation: warn
approved_by: andrei-shtanakov
approved_at: '2026-09-30T13:51:38Z'
owner_role: stream-owner
---

## Milestone 1: находки selfcheck по devtools — реальный слой (прогон 2026-09-30)

Прогон `make selfcheck` 2026-09-30 (`out/selfcheck/20260930T123348Z-8b671a`)
дал 2114 находок. Разбор с владельцем того же дня: шум (сложность, тестовые
ARG/pyrefly/jscpd/vulture, колбэки textual, намеренные glob) подавлен
классовыми записями `selfcheck.toml` (спека selfcheck §2.4, отдельный PR), а
.github-гигиена (zizmor) ушла отдельным PR с человеческим мержем. Здесь —
реальный слой: мёртвый код (T1), Optional без сужения (T2), мелкие баги
ruff (T3; включение правил в CI — отдельным PR оператора), остальной pyrefly в продакшен-коде (T4).

Общие правила для всех задач:
- поведение не меняется, кроме явно названного в задаче; каждое место — свой
  осознанный выбор, не массовая замена;
- id находок (`sc-…`) стабильны: после задачи их нет в свежем прогоне
  (`make selfcheck ARGS='--probe <проба>'` из корня devtools; отчёт —
  `out/selfcheck/<run>/report.json`, поле `findings[].id`);
- `issue_console.py`, вендоренные `governance/discovery_contract/**` и пути
  `harness_files` конфига spec-runner не трогать;
- полный набор тестов зелёный в обоих режимах: `uv run --frozen pytest -q` и
  `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q`
  (урок #508: тестовые двойники ломаются от правок сигнатур — искать их по
  всему `tests/`, не только рядом).

### TASK-001: Удалить мёртвый код — Ops-методы без вызовов, функция и поле без читателя
P2 | TODO   Est: 2h

Продакшен-код не вызывает эти методы и функцию и не читает поле; остались
только их тестовые двойники.
Source: selfcheck 2026-09-30, vulture (решение владельца 2026-09-30: T1)

**Checklist:**
- [ ] `governance/ops.py`: удалить `Ops.mark_ready` (sc-1e1172cc), `Ops.review_fresh` (sc-2a6fb65a), `Ops.pr_files` (sc-9906d4c7) из протокола и их реализации `RealOps.mark_ready` (sc-30251235), `RealOps.review_fresh` (sc-f9b6832d), `RealOps.pr_files` (sc-18baa439); перед удалением подтвердить поиском по всему репо (включая `getattr` и строковые имена), что продакшен-вызовов нет
- [ ] обновить упоминание `mark_ready` в docstring `governance/ops.py` (≈ строка 1520), чтобы текст не ссылался на удалённый метод
- [ ] тестовые двойники: удалить одноимённые методы у фейков (`tests/test_governance_runner.py`, `tests/test_governance_accept_pr.py`, `tests/test_governance_task_bridge.py`); проверки вида «`pr_files` не вызывался» (`test_governance_task_bridge.py` ≈ 5134, 5652) переписать так, чтобы они по-прежнему что-то утверждали (например, оставить только `prs_containing_commit`), а не стали пустыми
- [ ] `governance/spec_loop.py`: удалить `_bundle_dir_from_pr_files` (sc-b30abbdb) вместе с его тестами, если есть
- [ ] `governance/bundle_state.py`: удалить поле `BundleState.trace_matrix` (sc-0ffff1a5), вызов `build_trace_matrix` и его импорт — поле никто не читает; поправить все места, где `BundleState` конструируется (прод и тесты); `tests/test_governance_steward_surface.py` (поверхность steward) не трогать
- [ ] свежий прогон `make selfcheck ARGS='--probe vulture'`: перечисленных id нет
- [ ] полный набор тестов зелёный в обоих режимах

**Touches:** governance/ops.py, governance/spec_loop.py, governance/bundle_state.py, tests/test_governance_runner.py, tests/test_governance_accept_pr.py, tests/test_governance_task_bridge.py, tests/test_governance_spec_loop.py, tests/test_governance_bundle_state.py

### TASK-002: Optional без сужения — явная проверка вместо неявного инварианта
P2 | TODO   Est: 4h

pyrefly видит обращение к значению с типом `X | None` без проверки. Где
инвариант гарантирован потоком — сделать его явным (`assert x is not None,
"<какой инвариант>"` или ранний выход); где `None` реально достижим —
обработать как ошибку с внятным сообщением и добавить тест на эту ветку.
Молчаливое `or {}`/`or []` вместо проверки — нельзя: оно прячет нарушенный
инвариант.
Source: selfcheck 2026-09-30, pyrefly (решение владельца 2026-09-30: T2)

**Checklist:**
- [ ] `governance/runner.py` 1596, 1605, 1623, 1651, 1707, 1738, 1761, 1791 (sc-1b02ec57, sc-2277f58d, sc-0c3a6ee3, sc-2a1343ef, sc-2782520d, sc-59d3fe76, sc-07f3f36a, sc-2d337fe9): `state.interview`/путь с типом `| None` — сузить один раз на входе функции, а не в каждой строке
- [ ] `todo_context.py` 197, 204, 214, 262, 373, 753 (sc-ba566fba, sc-c9b7837a, sc-5be61753, sc-5da7b0b6, sc-a29a9f7e, sc-a103940a): модуль, импортируемый как необязательный (`None` при отсутствии), используется без проверки — проверка с понятной ошибкой «нужна группа/пакет X»; 628, 643, 644, 646, 648 (sc-575ab789, sc-343eddcf, sc-c6098c3d, sc-3a5bf8eb, sc-6e5e71f4): список `| None` — сузить
- [ ] `governance/accept_pr.py` 402, 409 (sc-4626f709, sc-c131971e): `.detail` у `None`
- [ ] `inbox.py` 359, 363, 367, 371, 375 (sc-2ae71bc5, sc-cd47da30, sc-43bf319b, sc-ace44377, sc-fed021c1): `dict[str, Path]` против параметра `dict[str, Path | None]` — поправить аннотацию `render` (`Mapping[str, Path | None]` ковариантен по значению), а не приводить аргумент
- [ ] `check-plan-fields.py` 624, 631 (sc-6a7df261, sc-abf2831d) и `check-arch-evidence-freshness.py` 443 (sc-92ddae1d): ключ/аргумент `| None` — сузить
- [ ] `governance/spec_loop.py` 1183, 1185, 1187 (sc-ffb471ba, sc-8a07d0b7, sc-c2be1633): `ws_id`/`bundle_dir`/`run_id` «may be uninitialized» — ложное по потоку (ветка с `state is not None` делает `return` раньше); перестроить так, чтобы это было видно статически (инициализация до ветвления или вынос), без изменения поведения
- [ ] на каждую ветку `None`, которая после правки стала реально достижимой ошибкой, — тест (для `inbox.py` и `check-plan-fields.py` тестов ещё нет: `tests/test_inbox.py`, `tests/test_check_plan_fields.py` — новые файлы)
- [ ] свежий прогон `make selfcheck ARGS='--probe pyrefly'`: перечисленных id нет
- [ ] полный набор тестов зелёный в обоих режимах

**Touches:** governance/runner.py, todo_context.py, governance/accept_pr.py, inbox.py, check-plan-fields.py, check-arch-evidence-freshness.py, governance/spec_loop.py, tests/test_governance_runner.py, tests/test_todo_context.py, tests/test_governance_accept_pr.py, tests/test_inbox.py, tests/test_check_plan_fields.py, tests/test_arch_evidence_freshness.py, tests/test_governance_spec_loop.py

### TASK-003: Мелкие баги ruff — zip без strict, raise без from, неиспользуемые переменные цикла
P3 | TODO   Est: 2h

Эти правила вне набора CI (`[tool.ruff.lint] select`); чинятся по месту, а
после — правило добавляется в `select`, чтобы класс не вернулся.
Source: selfcheck 2026-09-30, ruff (решение владельца 2026-09-30: T3)

**Checklist:**
- [ ] `B905` `zip()` без `strict=`: `governance/runner.py:2875` (sc-9e685662), `governance/task_bridge.py:1681` (sc-d29b43d6), `recent_changes.py:105` (sc-8b657e73), `salvage_scan.py:407, 409, 410` (sc-bfc2cbdf, sc-b02c2242, sc-6d397148) — для каждого места выяснить, гарантировано ли равенство длин построением: да → `strict=True`; нет, и усечение задумано → `strict=False` с комментарием почему
- [ ] `B904` `raise` в `except` без `from`: `check-plan-fields.py:101` (sc-17875ae9), `inbox.py:50` (sc-9ead2ff4) — `from exc` (или `from None`, если исходная ошибка шум — с комментарием)
- [ ] `B007` переменная цикла не используется: `check-graph-registry-drift.py:177` (sc-fc740822), `governance/decomposition_guard.py:567, 953` (sc-7771e734, sc-b962027f), `selfcheck/graph/resolver.py:402` (sc-7da8536b) — префикс `_` или итерация по нужной части
- [ ] `PLW2901` переменная цикла перезаписана в теле: `governance/design_guard.py:53` (sc-7bc83f35), `selfcheck/graph/build.py:357` (sc-b81488cc), `selfcheck/vendor.py:263, 281` (sc-1748378e, sc-129fae77) — новое имя для преобразованного значения
- [ ] `pyproject.toml` НЕ трогать (`harness_guard: strict`): включение `B905`/`B904`/`B007`/`PLW2901` в `select` делает оператор отдельным PR после задачи; здесь — только чтобы `uv run --frozen --group=selfcheck ruff check . --select B905,B904,B007,PLW2901` (вне `issue_console.py` и `governance/discovery_contract/`) был чист
- [ ] `uv run --frozen --group=selfcheck ruff check .` и `ruff format --check .` чисто
- [ ] полный набор тестов зелёный в обоих режимах

**Touches:** governance/runner.py, governance/task_bridge.py, recent_changes.py, salvage_scan.py, check-plan-fields.py, inbox.py, check-graph-registry-drift.py, governance/decomposition_guard.py, selfcheck/graph/resolver.py, governance/design_guard.py, selfcheck/graph/build.py, selfcheck/vendor.py

### TASK-004: Остальной pyrefly в продакшен-коде — типизация или настоящая ошибка
P3 | TODO   Est: 3h

Каждое место разобрать: если тип неточен (значение из JSON/TOML типизировано
как `object`) — сузить проверкой формы данных с ошибкой на неверной форме;
если это настоящая ошибка — исправить с тестом, воспроизводящим её.
Source: selfcheck 2026-09-30, pyrefly (решение владельца 2026-09-30: T4)

**Checklist:**
- [ ] `governance/runner.py` 2847, 2869, 2870 (sc-9158d1d2, sc-6d419a81, sc-3e6da40e): `object` итерируется/`.values()` — сузить форму данных
- [ ] `governance/approve_node.py:1269` (sc-604f66b9), `governance/ops.py:2097` (sc-5e75e2db), `governance/bundle_inputs.py:140` (sc-8644fa09): `object` не итерируем / без `.items()` — сузить
- [ ] `governance/ops.py:339` (sc-d86f21ed): возвращается кортеж с `| None` при объявленном `tuple[str, str, str] | None` — либо проверить все три значения и вернуть `None`, либо исправить аннотацию, если `None` внутри кортежа допустим (решение — по вызывающим)
- [ ] `conductor/graph.py:275` (sc-0b320f5e): `dict(...)` из генератора кортежей неизвестной длины — явные пары ключ-значение
- [ ] `selfcheck/llm.py:78, 190` (sc-e537caf4, sc-b63fbe0b) и `selfcheck/run.py:306` (sc-27fd589b): `None` subscriptable, `str.join` над неоднородными значениями, распаковка kwargs неверного типа — сузить / привести явно
- [ ] свежий прогон `make selfcheck ARGS='--probe pyrefly'`: перечисленных id нет
- [ ] `uv run --frozen --group=selfcheck pytest tests/selfcheck -q` зелёный (для правок в `selfcheck/`)
- [ ] полный набор тестов зелёный в обоих режимах

**Depends on:** [TASK-002]

**Touches:** governance/runner.py, governance/approve_node.py, governance/ops.py, governance/bundle_inputs.py, conductor/graph.py, selfcheck/llm.py, selfcheck/run.py, tests/test_governance_runner.py, tests/test_governance_approve_node.py, tests/test_governance_ops.py, tests/test_governance_bundle_inputs.py, tests/conductor/test_graph.py, tests/selfcheck/test_llm.py, tests/selfcheck/test_run.py
