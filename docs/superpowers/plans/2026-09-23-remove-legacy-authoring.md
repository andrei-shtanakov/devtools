# Удаление прежнего пути авторинга (S13) — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** прежний путь авторинга (бандл целиком одним PR) удалён из
исполнения; исторические леджеры по-прежнему читаются, а попытка исполнить
прежний путь отказывает с названной причиной.

**Spec:** `docs/superpowers/specs/2026-09-23-remove-legacy-authoring-design.md`
— **не согласована на момент написания плана**; Task 0 это фиксирует.

**Tech Stack:** Python 3.12, `uv run --frozen pytest`, ветка → PR → ревью.

## Global Constraints

- Удаляется исполнение, НЕ история: поле `authoring` и его десериализационное
  умолчание `"legacy"` остаются (D2).
- Тест, который ИСПОЛНЯЕТ прежний путь, — удаляется; тест, который ЧИТАЕТ
  прежний леджер, — остаётся (D5).
- Каждая задача оставляет набор зелёным: полный прогон после каждой.
- Коммиты по-русски, трейлеры `Epic: eco.dark-factory` и `Co-Authored-By`.

---

### Task 0: Согласование спеки (внешний эффект — решение владельца)

- [ ] **Step 1: Получить решение по D1–D5.** Особенно: resume legacy —
  отказ или миграция (D3); судьба `--waves` (D4); список стражей,
  обязанных пережить чистку тестов (D5). Без этого остальные задачи не
  начинаются: удаление кода необратимо по смыслу, даже будучи обратимым в git.

---

### Task 1: Страж resume — ДО удаления кода

**Files:** `governance/runner.py`, `tests/test_governance_runner.py`

- [ ] **Step 1: Failing test** — `resume` прогона с `authoring="legacy"`
  завершается отказом, называющим причину (путь удалён, решение и дата) и
  не падает traceback'ом.
- [ ] **Step 2: Run** — FAIL (сегодня resume ведёт прогон прежним путём).
- [ ] **Step 3: Реализация** — проверка в начале `resume`, до любой
  реконсиляции.
- [ ] **Step 4: Run** — PASS. Полный набор — зелёный (прежний путь ещё жив,
  но недостижим через resume).
- [ ] **Step 5: Commit**

Порядок намеренный: страж пишется, пока удаляемый код ещё на месте, —
иначе его нечем проверить на красном.

---

### Task 2: Стражи чтения истории — инвентаризация и закрепление

**Files:** `tests/test_governance_runner.py`, `tests/test_governance_console_model.py`

- [ ] **Step 1: Инвентаризация** — перечислить ВСЕ тесты, упоминающие
  legacy, и разметить каждый: исполняет путь / читает леджер. Перечень —
  в теле PR, чтобы разметка была предъявлена, а не подразумевалась.
- [ ] **Step 2: Failing test (если нужен)** — покрытие чтения реальных
  леджеров: консоль и WS-lock на 14 исторических прогонах из
  `out/governance-runs` (не фикстура — приёмка §7.3).
- [ ] **Step 3: Run + Commit**

---

### Task 3: Снять legacy-шаги конвейера

**Files:** `governance/runner.py`, тесты соответствующих шагов

- [ ] **Step 1** — удалить `_step_push`, `_step_pr`, `_step_ready`,
  `_step_review`, `_step_verdict`, `_step_merge` и `_LEGACY_STEPS`;
  `advance` ведёт единственный кортеж.
- [ ] **Step 2** — удалить тесты, ИСПОЛНЯЮЩИЕ эти шаги (по разметке Task 2).
- [ ] **Step 3: Run** — полный набор зелёный. **Проверить число тестов:**
  падение объясняется поимённо, иначе непонятно, что ушло лишнего.
- [ ] **Step 4: Commit**

---

### Task 4: Схлопнуть развилки `_waves`

**Files:** `governance/runner.py`

- [ ] **Step 1** — 16 вхождений `_waves(state)` заменить волновой веткой;
  удалить предикат.
- [ ] **Step 2: Run** — полный набор зелёный.
- [ ] **Step 3: Проверка приёмки** — `grep -c "_waves(" governance/runner.py`
  → 0.
- [ ] **Step 4: Commit**

---

### Task 5: CLI

**Files:** `governance/spec_loop.py`, `governance/runner.py`, `Makefile`, `CLAUDE.md`

- [ ] **Step 1: Failing tests** — `--legacy` отказывает с названной
  причиной; `--waves` принимается и ничего не меняет.
- [ ] **Step 2: Реализация + удаление `--authoring` выбора.**
- [ ] **Step 3: Доки** — `Makefile` help и строка CLAUDE.md: прежний путь
  удалён, дата и решение названы.
- [ ] **Step 4: Run + Commit**

---

### Task 6: Живая приёмка на исторических леджерах

- [ ] **Step 1** — консоль и WS-lock прогнать на РЕАЛЬНОМ
  `out/governance-runs` (14 legacy + 2 waves), а не на фикстуре.
- [ ] **Step 2** — `spec-loop --legacy` и `behaviour-run resume --run-id
  <legacy>`: оба отказа предъявлены выводом в теле PR.
- [ ] **Step 3** — TODO: пункт S13 закрыт, evidence приложен.
