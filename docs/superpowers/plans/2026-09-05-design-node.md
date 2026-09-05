# Design-узел конвейера — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Узел `design` (owner_role: architects, файл `20-design.md`) в
behaviour-конвейере: авторинг S2, гейты S4 (оба ребра, покрытие Q),
консоль, DAG пинов в task_bridge, переходный режим для легаси-бандлов.

**Architecture:** Всё по существующим швам: профиль → runner-таблица →
ops-DSL → консольный список → мост. Новая механика только в S4-гардах
(данные-ориентированный список рёбер вместо двух захардкоженных +
гард покрытия Q) и в штампе (DAG вместо линейной цепочки).

**Tech Stack:** Python stdlib + pyyaml (уже в governance-группе), pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-design-node-conveyor-design.md`

## Global Constraints

- Форма узла — пин steward team-exp: `{id: design, template: design.md,
  owner_role: architects, upstream: [requirements, behaviour-spec]}`.
  Роль — ровно `architects`.
- Файл бандла — `20-design.md`; frontmatter: `spec_stage: design`,
  `status: draft`, `owner_role: architects`,
  `traces_to: [requirements, behaviour-spec]`, `upstream_hashes` с
  ОБОИМИ пинами.
- runner без импорта steward (гарды — stdlib + blob_sha1, как сейчас).
- Перепиновка/штампы — парсером frontmatter, не текстовой заменой.
- Грамматика Q (обе стороны): `- **Q-NN · owner_role: <role> ·
  blocking: true|false.** <текст>` в requirements; в design —
  `#### Q-NN · owner_role: architects · resolution: resolved|deferred`
  (+ строка `reason: <…>` при deferred); пустой входной набор — только с
  явной строкой `Открытых архитектурных вопросов нет (входной набор пуст)`.
- Тестовая команда: `uv run --frozen --group governance python -m pytest
  tests/ -q` (devtools); линт `uv run ruff format` + `check`.

---

### Task 1: Профиль — узел design

**Files:**
- Modify: `profiles/team-exp.yaml`
- Test: `tests/test_governance_profile.py` (создать, если нет файла с
  профильными тестами; иначе — дописать в существующий)

**Interfaces:**
- Produces: узел `design` в `artifacts` профиля; `tasks.upstream:
  [design]`. Потребители: bundle_state (required), gate-check, Task 4/5.

- [ ] **Step 1: тест — профиль содержит design и новый upstream tasks**

```python
def test_team_exp_profile_has_design_node():
    import yaml

    prof = yaml.safe_load(Path("profiles/team-exp.yaml").read_text())
    nodes = {a["id"]: a for a in prof["artifacts"]}
    d = nodes["design"]
    assert d["owner_role"] == "architects"
    assert d["upstream"] == ["requirements", "behaviour-spec"]
    assert d["template"] == "design.md"
    assert nodes["tasks"]["upstream"] == ["design"]
```

- [ ] **Step 2: прогнать — FAIL (узла нет)**
- [ ] **Step 3: правка профиля** — после behaviour-spec:

```yaml
  - {id: design, template: design.md, owner_role: architects,
     upstream: [requirements, behaviour-spec]}
```

`tasks.upstream: [behaviour-spec]` → `[design]`. Обновить шапочный
комментарий: срезаны теперь только acceptance/decomposition; отступление
`tasks.upstream: [design]` (в полном — `[decomposition]`, придёт спекой 3).

- [ ] **Step 4: прогнать — PASS**
- [ ] **Step 5: commit** `feat(profile): узел design в team-exp конвейера`

### Task 2: ops — DSL design и грамматика Q в requirements

**Files:**
- Modify: `governance/ops.py` (`_AUTHOR_FILENAMES`, `_AUTHOR_DSL`)
- Test: `tests/test_governance_ops_author.py` (дописать к существующим
  author-тестам; если файла нет — найти тесты `ops.author` и дописать там)

**Interfaces:**
- Produces: `_AUTHOR_FILENAMES["design"] == "20-design.md"`;
  `_AUTHOR_DSL["design"]` — полный контракт §4 спеки;
  `_AUTHOR_DSL["requirements"]` дополнен грамматикой Q.
- Consumes: ничего нового.

- [ ] **Step 1: тесты — контракт промпта**

```python
def test_design_author_dsl_contract():
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["design"] == "20-design.md"
    dsl = _AUTHOR_DSL["design"]
    for needle in (
        "spec_stage: design",
        "owner_role: architects",
        "traces_to: [requirements, behaviour-spec]",
        "resolution: resolved|deferred",
        "reason:",
        "Открытых архитектурных вопросов нет (входной набор пуст)",
    ):
        assert needle in dsl, needle


def test_requirements_dsl_declares_q_grammar():
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["requirements"]
    assert "Q-NN" in dsl and "owner_role" in dsl and "blocking" in dsl
```

- [ ] **Step 2: FAIL**
- [ ] **Step 3: правка ops.py.** `_AUTHOR_FILENAMES` += design.
  `_AUTHOR_DSL["design"]` (одной строкой-конкатенацией, стиль соседей):
  frontmatter-требование с ДВУМЯ пинами (`upstream_hashes:
  {requirements: "<hash10>", behaviour-spec: "<hash15>"}` через
  `git hash-object` обоих файлов); обязательные секции §4 спеки
  (Резолюции Q по грамматике / Механика / Рамки red-дизайна / Карта
  модулей / Вне объёма); запреты (не переоткрывать продуктовые решения,
  не код построчно, не плодить Q без owner_role). В
  `_AUTHOR_DSL["requirements"]` добавить предложение: «Every open
  question MUST be a bullet `- **Q-NN · owner_role: <role> · blocking:
  true|false.** <text>`; architect-level questions use owner_role:
  architects and are the design stage's input set.»
- [ ] **Step 4: PASS; ruff; commit**
  `feat(ops): DSL design-узла + машинная грамматика Q в requirements`

### Task 3: S2 и консоль — author-design в обоих списках + сверка

**Files:**
- Modify: `governance/runner.py:49` (`_AUTHOR_STEPS`),
  `governance/console_model.py:19` (`PIPELINE_KEYS`)
- Test: `tests/test_governance_console_model.py` (+ сверка списков)

**Interfaces:**
- Produces: шаг `author-design` между `author-behaviour` и `commit` в
  ОБОИХ списках; тест согласованности, ловящий будущие узлы серии.

- [ ] **Step 1: тест согласованности**

```python
def test_pipeline_keys_cover_every_author_step_in_order():
    from governance.console_model import PIPELINE_KEYS
    from governance.runner import _AUTHOR_STEPS

    author_keys = [k for k, _, _ in _AUTHOR_STEPS]
    in_console = [k for k in PIPELINE_KEYS if k in set(author_keys)]
    assert in_console == author_keys, (
        "PIPELINE_KEYS и runner._AUTHOR_STEPS разошлись — консоль покажет "
        "неверный текущий шаг (FR-04)"
    )
```

и тест текущего шага: прогон со статусом `stopped_author` на
`author-design` (все предыдущие op completed) ⇒ `_current_step ==
"author-design"`, op виден в `run_detail`.

- [ ] **Step 2: FAIL (двумя способами: нет шага; после правки runner —
  расхождение списков)**
- [ ] **Step 3: правки.** `_AUTHOR_STEPS` +=
  `("author-design", "design", "20-design.md")` (после behaviour).
  `PIPELINE_KEYS`: вставить `"author-design"` после
  `"author-behaviour"`.
- [ ] **Step 4: PASS; commit**
  `feat(runner+console): шаг author-design и сверка списков`

### Task 4: S4 — рёбра design, гард отсутствия, покрытие Q

**Files:**
- Modify: `governance/runner.py` (`_step_gate`: цикл рёбер ~строка 798,
  DSL-empty блок ~840; новый гард покрытия Q; гард отсутствия узла)
- Create: `governance/design_guard.py` — парсер Q requirements/design
  (чистые функции, stdlib re; отдельный модуль — его же переиспользует
  task_bridge в Task 5)
- Test: `tests/test_governance_runner_gates.py` (дописать к
  существующим S4-тестам)

**Interfaces:**
- Produces: `design_guard.parse_requirements_questions(text) ->
  dict[str, str]` (Q-id → owner_role); `design_guard.
  parse_design_resolutions(text) -> dict[str, tuple[str, str | None]]`
  (Q-id → (state, reason)); `design_guard.coverage_findings(
  req_text, design_text) -> list[str]`.
- Consumes: Task 1 профиль (узел required), Task 2 грамматика.

- [ ] **Step 1: тесты design_guard (unit, без git)** — парс боевой формы
  (`- **Q-04 · owner_role: architects · blocking: false.** …`);
  резолюции resolved/deferred+reason; findings: непокрытый Q; deferred
  без reason; пустой входной набор без строки-декларации ⇒ finding, с
  ней ⇒ пусто; product-Q в input не попадают.

```python
REQ = "- **Q-03 · owner_role: architects · blocking: false.** Как?\n" \
      "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
DSN_OK = "#### Q-03 · owner_role: architects · resolution: resolved\nтекст\n"
DSN_DEF = "#### Q-03 · owner_role: architects · resolution: deferred\nreason: ждём steward#147\n"

def test_coverage_clean():
    assert coverage_findings(REQ, DSN_OK) == []

def test_uncovered_question_is_a_finding():
    assert any("Q-03" in f for f in coverage_findings(REQ, "## Механика\n"))

def test_deferred_without_reason_is_a_finding():
    bad = DSN_DEF.replace("reason: ждём steward#147\n", "")
    assert any("reason" in f for f in coverage_findings(REQ, bad))

def test_empty_input_set_needs_the_declaration_line():
    req = "- **Q-05 · owner_role: product · blocking: false.** Продукт.\n"
    assert coverage_findings(req, "## Механика\n")  # нет декларации — finding
    ok = "Открытых архитектурных вопросов нет (входной набор пуст)\n"
    assert coverage_findings(req, ok) == []
```

- [ ] **Step 2: FAIL; реализация design_guard.py; PASS**
- [ ] **Step 3: тесты S4-гардов (по образцу существующих: RunState +
  фейковые ops):** (а) бандл без 20-design.md ⇒ `stopped_gate`, в
  gate-findings строка `GC-COMPLETENESS(design)`; (б) design без пина
  requirements ⇒ GC-UNPINNED, без пина behaviour-spec ⇒ GC-UNPINNED —
  ДВА отдельных теста; (в) stale каждого пина по отдельности ⇒ GC-STALE;
  (г) непокрытый Q ⇒ `stopped_gate` с `GC-DESIGN-COVERAGE`.
- [ ] **Step 4: FAIL; правки `_step_gate`:**
  - цикл рёбер — данные дополняются двумя кортежами:
    `("20-design.md", "requirements", "10-requirements.md")`,
    `("20-design.md", "behaviour-spec", "15-behaviour-spec.md")`;
  - ПЕРЕД циклом — гард отсутствия: если профиль прогона содержит
    required-узел design (наш team-exp — содержит), а файла нет ⇒
    finding `error GC-COMPLETENESS(design): required-узел design
    отсутствует в бандле (20-design.md)` (candidate_state остаётся у
    консоли — гард локальный, по спеке);
  - в DSL-empty блок добавить
    `("20-design.md", r"^#### Q-\d+ · |^Открытых архитектурных вопросов нет", "резолюций design")`;
  - после DSL-empty — гард покрытия: `design_guard.coverage_findings(
    req_text, design_text)` → каждый finding с префиксом
    `error GC-DESIGN-COVERAGE: …`.
- [ ] **Step 5: PASS; ruff; commit**
  `feat(runner): S4-гарды design — рёбра, отсутствие, покрытие Q`

### Task 5: task_bridge — DAG пинов, traces_to design, секция резолюций

**Files:**
- Modify: `governance/task_bridge.py` (`_BUNDLE_CHAIN` →
  `_BUNDLE_DAG`, `stamp_bundle_approved` ~строка 355, `deliver`
  генерация tasks-спеки, `find`/рендер секции резолюций)
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Produces: `_BUNDLE_DAG: tuple[tuple[str, tuple[str, ...]], ...]` =
  `(("00-charter.md", ()), ("10-requirements.md", ("charter",)),
  ("15-behaviour-spec.md", ("requirements",)),
  ("20-design.md", ("requirements", "behaviour-spec")))` + отображение
  node-id → filename; штамп в этом (топологическом) порядке, пин(ы)
  каждого узла пересчитываются ПОСЛЕ штампа всех его upstream-файлов.
- Consumes: `design_guard.parse_design_resolutions` (Task 4) для
  генерации секции.

- [ ] **Step 1: тесты:** (а) штамп 4-узлового бандла: после
  `stamp_bundle_approved` оба пина design равны blob-хешам
  ЗАШТАМПОВАННЫХ requirements/behaviour-spec (пересчитать в тесте
  blob_sha1 файлов после штампа и сравнить с frontmatter design);
  (б) tasks-спека из `deliver`: `traces_to: [design]`,
  `upstream_hashes == {"design": <blob заштампованного 20-design.md>}`;
  (в) секция «Решения открытых вопросов (уровень design…)» в tasks-спеке
  сгенерирована из фикстурного 20-design.md (резолюция Q-03 попала в
  текст), рукописного варианта нет.
- [ ] **Step 2: FAIL**
- [ ] **Step 3: реализация.** `_BUNDLE_CHAIN` заменить на `_BUNDLE_DAG`
  (форма выше); цикл штампа: копить `stamped_blobs: dict[node_id, sha]`,
  для узла с upstream'ами обновлять ВСЕ пины из уже посчитанных blob'ов;
  `deliver`: traces_to/upstream_hashes от design; рендер секции —
  `design_guard.parse_design_resolutions(design_text)` → markdown-блок
  (resolved: `- **Q-NN:** <первый абзац обоснования>`; deferred:
  `- **Q-NN (deferred):** reason: …`).
- [ ] **Step 4: PASS; ruff; commit**
  `feat(task_bridge): DAG пинов + tasks от design + генерация резолюций`

### Task 6: conform_approved сохраняет design

**Files:**
- Modify: `governance/task_bridge.py:405` (`conform_approved`)
- Test: `tests/test_governance_task_bridge.py`

- [ ] **Step 1: тест-регрессия:** tasks-спека approved с
  `traces_to: [design]` и верным пином 20-design.md; после
  `conform_approved` traces_to НЕ откатился к behaviour-spec, пин —
  актуальный design (а изменённый вручную traces_to: [behaviour-spec]
  нормализуется К design).
- [ ] **Step 2: FAIL (сегодня функция принудительно пишет
  behaviour-spec)**
- [ ] **Step 3: правка:** якорный узел = последний узел `_BUNDLE_DAG`
  (design); `pin = blob_sha1(<bundle>/20-design.md)`;
  `meta["traces_to"] = ["design"]`, `upstream_hashes = {"design": pin}`.
  Не хардкодить имя второй раз — вывести из `_BUNDLE_DAG[-1]`.
- [ ] **Step 4: PASS; commit**
  `fix(task_bridge): conform_approved нормализует к design, не к behaviour-spec`

### Task 7: переходный режим легаси-бандлов

**Files:**
- Modify: `governance/task_bridge.py` (`stamp_bundle_approved`,
  `deliver`, CLI `main` — флаг `--legacy-bundle`)
- Test: `tests/test_governance_task_bridge.py`

- [ ] **Step 1: тесты:** (а) `stamp_bundle_approved` на 3-узловом бандле
  без флага ⇒ RuntimeError, текст содержит `20-design.md` и обе
  процедуры (доавторить design; `--legacy-bundle`), НЕ traceback от
  read_text; (б) с `legacy_bundle=True` ⇒ штамп по 3-узловому префиксу
  DAG, tasks-спека `traces_to: [behaviour-spec]` (унаследованное
  поведение), никакого чтения 20-design.md.
- [ ] **Step 2: FAIL**
- [ ] **Step 3: реализация:** параметр `legacy_bundle: bool = False`
  сквозь `deliver`/`stamp_bundle_approved`; в начале штампа — проверка
  существования `20-design.md`: нет и не legacy ⇒ RuntimeError с
  процедурой; legacy ⇒ DAG усечён до первых трёх узлов и якорь tasks —
  behaviour-spec. CLI: `--legacy-bundle` в argparse `task_bridge.main`.
- [ ] **Step 4: PASS; ruff; полный набор тестов devtools; commit**
  `feat(task_bridge): переходный режим --legacy-bundle`

### Task 8: смоук S2 + сквозная проверка

**Files:**
- Test: там же, где существующие author-смоуки runner'а

- [ ] **Step 1: смоук:** фейковый ops.author, пишущий канонический
  20-design.md ⇒ прогон S2 проходит все четыре author-шага в порядке,
  `author-design` completed, файл в бандле; S4 на полном 4-узловом
  фикстур-бандле (валидные пины, покрытые Q) ⇒ зелёный.
- [ ] **Step 2: прогнать ВЕСЬ тестовый набор devtools + ruff + (если
  настроен) pyrefly/mypy; FAIL-ы чинить до зелёного.**
- [ ] **Step 3: commit** `test: сквозной смоук design-узла`
