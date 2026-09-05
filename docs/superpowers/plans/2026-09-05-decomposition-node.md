# Decomposition-узел конвейера — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пятый узел `decomposition` (owner tech-lead) в behaviour-конвейере:
DT-грамматика задач с типами implement/verify и графом зависимостей, гейт
инвариантов графа, task_bridge как транслятор решённой декомпозиции.

**Architecture:** Зеркало механики design-узла (спека 1, влита #142,
реализация #145): шаг авторинга в `_AUTHOR_STEPS`, data-driven ребро в
`_GATE_EDGES`, чистый модуль-гард `decomposition_guard.py` (парсер DT +
инварианты графа, переиспользуется S4 и мостом), узел в `_BUNDLE_DAG`
(якорь выводится из DAG автоматически). Генерация tasks-спеки переходит с
BEH-группировки на «1 DT = 1 задача»; `--legacy-bundle` обобщается bool →
`3|4` с проверкой ТОЧНОГО состава бандла.

**Tech Stack:** Python stdlib (по канону governance/ — без steward-импорта в
runner, без pydantic), pytest, FakeOps-фикстуры.

**Spec:** `docs/superpowers/specs/2026-09-05-decomposition-node-conveyor-design.md`
(влита PR #144). План аргументирует от неё; конфликты решаются в пользу спеки.

## Global Constraints

- Файл бандла — ровно `30-decomposition.md`; frontmatter:
  `spec_stage: decomposition`, `status: draft`, `owner_role: tech-lead`,
  `traces_to: [design]`, `upstream_hashes: {design: "<blob 20-design.md>"}`.
- Заголовок DT — ровно `#### DT-NN: <название> · type: implement|verify ·
  owner: <роль>`; метаданные-строки `scenarios:`/`depends_on:`/
  `delivered_by:`/`parallel_group:` (§3 спеки).
- `runner.py` НЕ импортирует steward и НЕ импортирует task_bridge; гарды —
  чистые функции над строками (канон design_guard).
- verify-DT до доставки spec-runner#367 ⇒ fail-closed отказ моста с текстом
  про блокер (issue OPEN на момент плана); implement-путь полноценен.
- `--legacy-bundle` принимает ЗНАЧЕНИЕ 3|4; флаг без значения ⇒ отказ
  парсера; проверка точного фактического состава бандла (заявили 3 — есть
  ровно 00/10/15; заявили 4 — ровно +20; иное ⇒ отказ). «По самому длинному
  существующему» запрещён.
- `profiles/` — authority-root (ADR-ECO-004 I2): PR этого плана мержит
  человек.
- ruff — только по своим изменённым файлам (непинованный uvx-ruff портит
  чужие; рулинг SDD-леджера спеки 1). Тесты:
  `uv run --frozen --group governance python -m pytest tests/ -q` с явной
  проверкой exit code (не через пайп).
- Урок PR #145 (major): блок сущности в парсере ограничивается СЛЕДУЮЩЕЙ
  секцией уровня 1–3, не только следующим заголовком той же сущности;
  near-miss заголовки и дубли id — находки с первого дня, не молчание.
- Line length 88; type hints; докстринги на публичном.

## Карта файлов

- Create: `governance/decomposition_guard.py` — парсер DT + инварианты графа
  (чистые функции; ЕДИНСТВЕННАЯ новая единица).
- Modify: `profiles/team-exp.yaml` (+узел), `governance/ops.py` (DSL),
  `governance/runner.py` (шаг, preflight, S4), `governance/console_model.py`
  (PIPELINE_KEYS), `governance/task_bridge.py` (DAG, legacy=3|4, DT-генерация),
  `TODO.md` (чекбокс блокера), `README.md`/`Makefile` (доки).
- Tests: `tests/test_governance_decomposition_guard.py` (create),
  `tests/test_governance_profile.py`, `tests/test_governance_runner.py`,
  `tests/test_governance_console_model.py`, `tests/test_governance_task_bridge.py`.

Машинная интерпретация инварианта «сводные за хвостами групп» (§3 спеки не
даёт маркера «сводной» задачи — фиксируем правило, проверяемое механически):
если DT X зависит хотя бы от одного члена ЧУЖОЙ группы G (`parallel_group`
G ≠ группы X), то `depends_on` X обязан включать ВСЕ стоки G (DT группы G,
от которых не зависит никто внутри G). Иначе X стартует до хвоста G —
ровно класс «сводная задача поверх недоделанной ветви». ВАЖНО (major
терминального ревью плана): `solo` — НЕ имя общей группы, а признак
«задача сама по себе»; каждая solo-задача образует СОБСТВЕННУЮ
одиночную группу (ключ `solo:<dt_id>`), иначе две независимые solo-задачи
читались бы одной ветвью и правило стоков давало бы невыполнимые находки
(зависимость от чужого solo требовала бы зависимости от ВСЕХ solo — вплоть
до цикла). Второе исключение (minor круга 2, §3 спеки называет verify→
delivered_by самостоятельным легитимным видом межгруппового ребра): рёбра,
покрытые `delivered_by` той же задачи, из проверки стоков ИСКЛЮЧАЮТСЯ —
verify точечно следует за проверяемой задачей, требовать от него все стоки
чужой группы значило бы навязывать искусственную сериализацию, которую §3
прямо запрещает.

---

### Task 1: Профиль team-exp + чекбокс кросс-репного блокера

**Files:**
- Modify: `profiles/team-exp.yaml`
- Modify: `TODO.md`
- Test: `tests/test_governance_profile.py`

**Interfaces:**
- Produces: узел `decomposition` в профиле (id, template, owner_role
  tech-lead, upstream `[design]`), `tasks.upstream: [decomposition]` —
  дальше их читают preflight (Task 5) и реальный steward-тест.

- [ ] **Step 1: Красный тест — профиль несёт decomposition, tasks перехвачен**

В `tests/test_governance_profile.py` в существующий real-steward тест
(`load_profile(Path("profiles/team-exp.yaml"), …)`) добавить/заменить ассерты:

Real-steward тест использует только `graph.topo_order()` (доступа к узлам
у него нет — minor ревью плана: API узлов в тестах дерева НЕ используется,
не выдумывать):

```python
    assert graph.topo_order() == [
        "charter", "requirements", "behaviour-spec", "design",
        "decomposition", "tasks",
    ]
```

Атрибуты узла проверяются там же, где сейчас (`yaml.safe_load`-тест
`test_team_exp_profile_has_design_node` в том же файле) — расширить его:

```python
    assert nodes["decomposition"]["owner_role"] == "tech-lead"
    assert nodes["decomposition"]["upstream"] == ["design"]
    assert nodes["tasks"]["upstream"] == ["decomposition"]
```

Существующий ассерт `nodes["tasks"]["upstream"] == ["design"]` в этом
тесте ЗАМЕНЯЕТСЯ на `["decomposition"]` (minor ревью плана — иначе
незаявленный красный).

- [ ] **Step 2: Прогнать — FAIL** (узла нет в YAML).

Run: `uv run --frozen --group governance python -m pytest tests/test_governance_profile.py -q; echo RC=$?`

- [ ] **Step 3: Правка `profiles/team-exp.yaml`**

После узла design, до tasks:

```yaml
  - id: decomposition
    template: decomposition.md
    owner_role: tech-lead
    # steward-форма: upstream: [design, acceptance]; acceptance срезан
    # (придёт спекой 4). compile: decomposition→maestro/project.yaml НЕ
    # реализуется (наш лейн Mode-2) — осознанное отступление, §2/§8 спеки.
    upstream: [design]
```

и в узле tasks: `upstream: [decomposition]` (перехват у design; строку-
комментарий спеки 1 «временно design» убрать).

- [ ] **Step 4: Прогнать — PASS.** Полный набор тоже
  (`pytest tests/ -q; echo RC=$?`) — сверка PIPELINE_KEYS краснеть НЕ должна
  (профиль конвейерными списками не читается).

- [ ] **Step 5: Чекбокс ожидания в TODO.md** (§7a спеки — заводится при
  старте реализации):

```markdown
- [ ] verify-DT в decomposition-мосте включаются после доставки verify-first
  slug: decomposition-verify-first-unblock
  @blocked_by:spec-runner#367
```

- [ ] **Step 6: Commit**

```bash
git add profiles/team-exp.yaml TODO.md tests/test_governance_profile.py
git commit -m "feat(profile): узел decomposition (tech-lead, upstream design), tasks перехвачен"
```

---

### Task 2: DSL авторинга decomposition (ops.py)

**Files:**
- Modify: `governance/ops.py` (словари `_AUTHOR_FILENAMES`, `_AUTHOR_DSL`)
- Test: `tests/test_governance_ops.py`

**Interfaces:**
- Consumes: словари `_AUTHOR_FILENAMES`/`_AUTHOR_DSL` (форма записей — как
  `"design"` там же).
- Produces: ключ `"decomposition"` в обоих; Task 5 ссылается на kind
  `"decomposition"` в `_AUTHOR_STEPS`.

- [ ] **Step 1: Красный тест**

```python
def test_author_dsl_covers_decomposition() -> None:
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["decomposition"] == "30-decomposition.md"
    dsl = _AUTHOR_DSL["decomposition"]
    for token in (
        "spec_stage: decomposition", "owner_role: tech-lead",
        "traces_to: [design]", "#### DT-NN:", "type: implement|verify",
        "scenarios:", "depends_on:", "delivered_by:", "parallel_group:",
    ):
        assert token in dsl
```

- [ ] **Step 2: Прогнать — FAIL** (KeyError).

- [ ] **Step 3: Записи в словари**

`_AUTHOR_FILENAMES`: `"decomposition": "30-decomposition.md",`.
`_AUTHOR_DSL` (по образу записи design; текст промпта — дословно):

```python
    "decomposition": (
        "YAML frontmatter (required): spec_stage: decomposition, "
        "status: draft, owner_role: tech-lead, traces_to: [design], "
        "upstream_hashes: {design: \"<hash20>\"} where <hash20> is the "
        "output of `git hash-object <bundle_dir>/20-design.md`. "
        "The document MUST contain these sections: Задачи, Инварианты "
        "графа, Порядок и параллельность, Вне объёма. Задачи: every task "
        "is a heading exactly `#### DT-NN: <title> · type: "
        "implement|verify · owner: <role>` followed by metadata lines "
        "`scenarios: [BEH-…]` (>=1, BEH ids from 15-behaviour-spec.md), "
        "`depends_on: [DT-…]` (may be empty list; these edges are the "
        "single source of truth for ordering), `delivered_by: [DT-…]` "
        "(REQUIRED for type: verify, FORBIDDEN for type: implement), "
        "`parallel_group: <name|solo>`, then a prose paragraph (subject, "
        "boundaries). Every BEH-* of the bundle MUST be covered by "
        "exactly one DT (no gaps, no duplicates). delivered_by MUST be a "
        "subset of the transitive closure of depends_on. checked_by "
        "target files of different DTs MUST NOT intersect (single owner "
        "per test file). The graph MUST be acyclic; do NOT add an "
        "artificial linear chain — only real dependencies. A task "
        "depending on another parallel_group MUST depend on ALL sink "
        "tasks of that group. Порядок и параллельность: which groups may "
        "run concurrently (operator documentation). Вне объёма: what is "
        "deliberately not decomposed. Forbidden: do not invent BEH ids; "
        "do not reopen design decisions; do not write implementation "
        "code."
    ),
```

- [ ] **Step 4: Прогнать — PASS.**

- [ ] **Step 5: Commit**

```bash
git add governance/ops.py tests/test_governance_ops.py
git commit -m "feat(ops): DSL авторинга decomposition — грамматика DT"
```

---

### Task 3: decomposition_guard — парсер DT-грамматики

**Files:**
- Create: `governance/decomposition_guard.py`
- Test: `tests/test_governance_decomposition_guard.py` (create)

**Interfaces:**
- Produces (Task 4, 6, 8 потребляют):
  - `@dataclass(frozen=True) DtTask: dt_id: str; title: str; type: str;
    owner: str; scenarios: tuple[str, ...]; depends_on: tuple[str, ...];
    delivered_by: tuple[str, ...]; parallel_group: str`
  - `parse_dt_tasks(text: str) -> tuple[list[DtTask], list[str]]` —
    (задачи, findings формы: near-miss заголовок, дубль DT-id, отсутствие
    обязательной строки метаданных, scenarios пуст, невалидный type).

- [ ] **Step 1: Красные тесты** (новый файл; канон —
  `tests/test_governance_design_guard.py`: только строки):

```python
"""Unit-тесты governance.decomposition_guard: парсер DT и инварианты
графа. Никакого git/ФС — только строки."""

from __future__ import annotations

from governance.decomposition_guard import DtTask, parse_dt_tasks

DT_OK = (
    "#### DT-01: Парсер · type: implement · owner: dev\n"
    "scenarios: [BEH-01, BEH-02]\n"
    "depends_on: []\n"
    "parallel_group: core\n"
    "Проза предмета.\n"
    "\n"
    "#### DT-02: Проверка парсера · type: verify · owner: qa\n"
    "scenarios: [BEH-03]\n"
    "depends_on: [DT-01]\n"
    "delivered_by: [DT-01]\n"
    "parallel_group: core\n"
    "Проза.\n"
)


def test_parse_two_tasks() -> None:
    tasks, findings = parse_dt_tasks(DT_OK)
    assert findings == []
    assert [t.dt_id for t in tasks] == ["DT-01", "DT-02"]
    assert tasks[0] == DtTask(
        dt_id="DT-01", title="Парсер", type="implement", owner="dev",
        scenarios=("BEH-01", "BEH-02"), depends_on=(),
        delivered_by=(), parallel_group="core",
    )
    assert tasks[1].delivered_by == ("DT-01",)


def test_near_miss_heading_is_a_finding() -> None:
    """Урок minor'ов PR #145: похожий на DT заголовок мимо строгой
    грамматики — находка, не молчаливое исключение."""
    bad = "#### DT-03 Парсер · type: implement · owner: dev\n"  # нет «:»
    tasks, findings = parse_dt_tasks(bad)
    assert tasks == []
    assert any("DT-03" in f and "грамматик" in f for f in findings)


def test_duplicate_dt_id_is_a_finding() -> None:
    dup = DT_OK + "\n#### DT-01: Дубль · type: implement · owner: dev\n" \
        "scenarios: [BEH-04]\ndepends_on: []\nparallel_group: solo\n"
    _tasks, findings = parse_dt_tasks(dup)
    assert any("DT-01" in f and "раза" in f for f in findings)


def test_missing_scenarios_is_a_finding() -> None:
    bad = (
        "#### DT-05: Пустой · type: implement · owner: dev\n"
        "depends_on: []\nparallel_group: solo\n"
    )
    _tasks, findings = parse_dt_tasks(bad)
    assert any("DT-05" in f and "scenarios" in f for f in findings)


def test_block_ends_at_next_section() -> None:
    """Урок major'а PR #145: блок DT кончается на следующей секции уровня
    1–3 — метаданные чужой секции не читаются как свои."""
    text = (
        "#### DT-01: Одинокий · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: solo\n"
        "\n## Инварианты графа\n\nparallel_group: мусор\n"
    )
    tasks, findings = parse_dt_tasks(text)
    assert findings == []
    assert tasks[0].parallel_group == "solo"
```

- [ ] **Step 2: Прогнать — FAIL** (модуля нет).

- [ ] **Step 3: Модуль**

```python
"""Гард decomposition-узла: DT-грамматика и инварианты графа (спека
2026-09-05-decomposition-node §3). Чистые функции над строками — без
git/ФС/steward; переиспользуются S4-гейтом (runner) и мостом
(task_bridge). Канон модуля — governance/design_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DT_HEAD_RE = re.compile(
    r"^####\s+(DT-\d+):\s*(.+?)\s*·\s*type:\s*(implement|verify)"
    r"\s*·\s*owner:\s*(\S+)\s*$",
    re.M,
)
# near-miss: начинается как DT-заголовок, но строгую грамматику не прошёл
_DT_NEAR_RE = re.compile(r"^####\s+(DT-\d+)\b.*$", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class DtTask:
    """Одна задача decomposition-узла (заголовок #### DT-NN)."""

    dt_id: str
    title: str
    type: str
    owner: str
    scenarios: tuple[str, ...]
    depends_on: tuple[str, ...]
    delivered_by: tuple[str, ...]
    parallel_group: str


def parse_dt_tasks(text: str) -> tuple[list[DtTask], list[str]]:
    """DT-грамматика → (задачи, findings формы).

    Findings формы (не графа — граф в graph_findings): near-miss
    заголовок, дубль DT-id, отсутствие scenarios/depends_on/
    parallel_group, пустой scenarios. Блок задачи ограничен следующим
    DT-заголовком ЛИБО следующей секцией уровня 1–3 (урок major'а
    PR #145 — хвост документа не читается как метаданные последней
    задачи).
    """
    findings: list[str] = []
    strict = {m.start() for m in _DT_HEAD_RE.finditer(text)}
    for near in _DT_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике DT (`#### DT-NN: <название> · type: "
                "implement|verify · owner: <роль>`)"
            )
    matches = list(_DT_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    tasks: list[DtTask] = []
    for idx, m in enumerate(matches):
        dt_id = m.group(1)
        seen[dt_id] = seen.get(dt_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        scenarios = _list_field(block, "scenarios")
        depends_on = _list_field(block, "depends_on")
        delivered_by = _list_field(block, "delivered_by") or ()
        group_m = re.search(r"^parallel_group:\s*(\S+)\s*$", block, re.M)
        if scenarios is None or not scenarios:
            findings.append(f"{dt_id}: строка scenarios отсутствует или пуста")
        if depends_on is None:
            findings.append(f"{dt_id}: строка depends_on отсутствует")
        if group_m is None:
            findings.append(f"{dt_id}: строка parallel_group отсутствует")
        tasks.append(DtTask(
            dt_id=dt_id, title=m.group(2), type=m.group(3),
            owner=m.group(4), scenarios=scenarios or (),
            depends_on=depends_on or (), delivered_by=delivered_by,
            parallel_group=group_m.group(1) if group_m else "",
        ))
    for dt_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{dt_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return tasks, findings
```

- [ ] **Step 4: Прогнать — PASS.**

- [ ] **Step 5: Commit**

```bash
git add governance/decomposition_guard.py tests/test_governance_decomposition_guard.py
git commit -m "feat(decomposition_guard): парсер DT-грамматики с findings формы"
```

---

### Task 4: decomposition_guard — инварианты графа

**Files:**
- Modify: `governance/decomposition_guard.py`
- Test: `tests/test_governance_decomposition_guard.py`

**Interfaces:**
- Consumes: `parse_dt_tasks` (Task 3).
- Produces: `graph_findings(behaviour_text: str, decomposition_text: str)
  -> list[str]` — ЕДИНАЯ точка для S4 (Task 6) и моста (Task 8); включает
  findings формы parse_dt_tasks. Внутренний BEH-парсер
  `_parse_beh_bindings(text) -> dict[str, tuple[str | None, str | None]]`
  (beh_id → (target_file, kind); target — файл, `::селектор` отброшен).

- [ ] **Step 1: Красные тесты** (фикстура behaviour-текста — мини-DSL как в
  `tests/test_governance_task_bridge.py`; связка ровно та же грамматика
  `**checked_by** … kind: … target: …`):

```python
BEH = (
    "#### BEH-01: Один\n**checked_by** `kind: integration` "
    "`target: tests/test_a.py::test_one`\n\n"
    "#### BEH-02: Два\n**checked_by** `kind: integration` "
    "`target: tests/test_a.py::test_two`\n\n"
    "#### BEH-03: Три\n**checked_by** `kind: e2e` "
    "`target: tests/test_b.py::test_three`\n"
)


def test_clean_graph_no_findings() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n"
    )
    assert graph_findings(BEH, dt) == []


def test_uncovered_and_double_covered_beh() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-03]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n"
    )
    findings = graph_findings(BEH, dt)
    assert any("BEH-02" in f and "не покрыт" in f for f in findings)
    assert any("BEH-03" in f and "дважды" in f for f in findings)


def test_cycle_is_a_finding() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: [DT-02]\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n"
    )
    assert any("цикл" in f for f in graph_findings(BEH, dt))


def test_verify_requires_delivered_by_and_closure() -> None:
    from governance.decomposition_guard import graph_findings
    # verify без delivered_by
    dt1 = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: V · type: verify · owner: qa\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n"
    )
    assert any(
        "DT-02" in f and "delivered_by" in f for f in graph_findings(BEH, dt1)
    )
    # delivered_by вне транзитивного замыкания depends_on
    dt2 = dt1.replace(
        "depends_on: [DT-01]\nparallel_group: core\n",
        "depends_on: []\ndelivered_by: [DT-01]\nparallel_group: core\n",
    )
    assert any(
        "DT-02" in f and "замыкан" in f for f in graph_findings(BEH, dt2)
    )


def test_delivered_by_forbidden_for_implement() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02, BEH-03]\ndepends_on: []\n"
        "delivered_by: [DT-01]\nparallel_group: core\n"
    )
    assert any(
        "DT-01" in f and "запрещ" in f for f in graph_findings(BEH, dt)
    )


def test_single_owner_of_test_file() -> None:
    from governance.decomposition_guard import graph_findings
    # BEH-01 и BEH-02 живут в одном tests/test_a.py, но в разных DT
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-03]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: B · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: side\n"
    )
    assert any(
        "tests/test_a.py" in f and "single-owner" in f
        for f in graph_findings(BEH, dt)
    )


def test_unknown_references_are_findings() -> None:
    from governance.decomposition_guard import graph_findings
    dt = (
        "#### DT-01: A · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02, BEH-03, BEH-99]\n"
        "depends_on: [DT-77]\nparallel_group: core\n"
    )
    findings = graph_findings(BEH, dt)
    assert any("BEH-99" in f for f in findings)
    assert any("DT-77" in f for f in findings)


def test_solo_tasks_are_independent_singleton_groups() -> None:
    """Major ревью плана (2 круга): два solo-DT — не одна общая группа;
    ребро в один из них не требует зависимости от другого. Фикстура
    разносит DT по РАЗНЫМ файлам checked_by (BEH-01+BEH-02 живут в одном
    tests/test_a.py и потому обязаны быть в ОДНОЙ DT — иначе тест
    закраснел бы на собственном single-owner-инварианте, круг 2)."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: S1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: solo\n\n"
        "#### DT-02: Core · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: [DT-01]\nparallel_group: core\n\n"
        "#### DT-03: S2 · type: implement · owner: dev\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-02]\nparallel_group: solo\n"
    )
    assert graph_findings(beh4, dt) == []


def test_delivered_by_edge_exempt_from_sinks_rule() -> None:
    """Minor круга 2: verify c depends_on=[DT-01] и delivered_by=[DT-01]
    в чужую группу с двумя стоками — НЕ находка про стоки."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: A1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01, BEH-02]\ndepends_on: []\n"
        "parallel_group: core\n\n"
        "#### DT-02: A2 · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: V · type: verify · owner: qa\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-01]\n"
        "delivered_by: [DT-01]\nparallel_group: qa\n"
    )
    assert not any("стоков" in f for f in graph_findings(beh4, dt))


def test_cross_group_dependency_must_cover_all_sinks() -> None:
    """Машинное правило «сводные за хвостами групп» (карта файлов плана):
    ребро в чужую группу обязывает зависеть от ВСЕХ её стоков."""
    from governance.decomposition_guard import graph_findings
    beh4 = BEH + (
        "\n#### BEH-04: Четыре\n**checked_by** `kind: e2e` "
        "`target: tests/test_c.py::test_four`\n"
    )
    dt = (
        "#### DT-01: A1 · type: implement · owner: dev\n"
        "scenarios: [BEH-01]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-02: A2 · type: implement · owner: dev\n"
        "scenarios: [BEH-02]\ndepends_on: []\nparallel_group: core\n\n"
        "#### DT-03: B · type: implement · owner: dev\n"
        "scenarios: [BEH-03]\ndepends_on: []\nparallel_group: side\n\n"
        # сводная: зависит от DT-01 (группа core), но не от DT-02 —
        # второго стока core
        "#### DT-04: Свод · type: implement · owner: dev\n"
        "scenarios: [BEH-04]\ndepends_on: [DT-01, DT-03]\n"
        "parallel_group: solo\n"
    )
    assert any(
        "DT-04" in f and "DT-02" in f for f in graph_findings(beh4, dt)
    )
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация** (добавить в `decomposition_guard.py`):

```python
_BEH_HEAD_RE = re.compile(r"^####\s+(BEH-\d+)\b", re.M)
_BEH_CHECKED_RE = re.compile(
    r"\*\*checked_by\*\*.*?`kind:\s*(\S+?)`.*?`target:\s*(\S+?)`"
)


def _parse_beh_bindings(text: str) -> dict[str, tuple[str | None, str | None]]:
    """beh_id → (файл checked_by-цели, kind); `::селектор` отброшен.

    Дубликат грамматики task_bridge._CHECKED (фактическое имя константы
    моста) намеренный и запинован тестом согласованности
    (test_beh_binding_grammar_matches_task_bridge): гард обязан остаться
    чистым модулем без импорта task_bridge (канон design_guard), а
    расхождение грамматик ловится тестом, не ревьюером. Берётся
    ПОСЛЕДНЕЕ вхождение checked_by в блоке — как у построчного разбора
    моста, где новая строка перетирает предыдущую (minor круга 2:
    расхождение «первое против последнего» пропускало бы single-owner
    по неактуальной цели).
    """
    heads = list(_BEH_HEAD_RE.finditer(text))
    result: dict[str, tuple[str | None, str | None]] = {}
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        block = text[m.start() : end]
        checked = None
        for checked in _BEH_CHECKED_RE.finditer(block):
            pass  # последнее вхождение — как у моста
        if checked is None:
            result[m.group(1)] = (None, None)
        else:
            target = checked.group(2).split("::", 1)[0]
            result[m.group(1)] = (target, checked.group(1))
    return result


def _transitive_deps(
    start: str, edges: dict[str, tuple[str, ...]]
) -> set[str]:
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return seen


def graph_findings(behaviour_text: str, decomposition_text: str) -> list[str]:
    """Инварианты графа DT (§3 спеки) + findings формы парсера.

    Порядок проверок фиксирован, findings накапливаются (гейт показывает
    всё сразу, не по одной). Пустой список — граф валиден.
    """
    tasks, findings = parse_dt_tasks(decomposition_text)
    bindings = _parse_beh_bindings(behaviour_text)
    ids = {t.dt_id for t in tasks}
    edges = {t.dt_id: t.depends_on for t in tasks}

    # ссылки на несуществующее
    for t in tasks:
        for ref in (*t.depends_on, *t.delivered_by):
            if ref not in ids:
                findings.append(f"{t.dt_id}: ссылка на несуществующий {ref}")
        for beh in t.scenarios:
            if beh not in bindings:
                findings.append(
                    f"{t.dt_id}: сценарий {beh} отсутствует в behaviour-spec"
                )

    # сюръекция BEH без дублей
    coverage: dict[str, list[str]] = {}
    for t in tasks:
        for beh in t.scenarios:
            coverage.setdefault(beh, []).append(t.dt_id)
    for beh in bindings:
        owners = coverage.get(beh, [])
        if not owners:
            findings.append(f"{beh}: не покрыт ни одной DT-задачей")
        elif len(owners) > 1:
            findings.append(
                f"{beh}: покрыт дважды и более ({', '.join(owners)})"
            )

    # single-owner тест-файла
    file_owner: dict[str, str] = {}
    for t in tasks:
        for beh in t.scenarios:
            target, _kind = bindings.get(beh, (None, None))
            if target is None:
                continue
            prior = file_owner.get(target)
            if prior is not None and prior != t.dt_id:
                findings.append(
                    f"{target}: нарушен single-owner — checked_by-цель у "
                    f"{prior} и {t.dt_id}"
                )
            file_owner.setdefault(target, t.dt_id)

    # verify/implement-контракт delivered_by + транзитивное замыкание
    for t in tasks:
        if t.type == "verify":
            if not t.delivered_by:
                findings.append(
                    f"{t.dt_id}: type: verify без delivered_by"
                )
            else:
                closure = _transitive_deps(t.dt_id, edges)
                outside = [d for d in t.delivered_by if d not in closure]
                if outside:
                    findings.append(
                        f"{t.dt_id}: delivered_by "
                        f"({', '.join(outside)}) вне транзитивного "
                        "замыкания depends_on"
                    )
        elif t.delivered_by:
            findings.append(
                f"{t.dt_id}: delivered_by запрещён при type: implement"
            )

    # ацикличность (DFS с цветами)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(ids, WHITE)

    def _visit(node: str) -> bool:
        color[node] = GRAY
        for nxt in edges.get(node, ()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                return True
            if color[nxt] == WHITE and _visit(nxt):
                return True
        color[node] = BLACK
        return False

    if any(color[n] == WHITE and _visit(n) for n in sorted(ids)):
        findings.append("depends_on: в графе есть цикл")

    # рёбра в чужую группу — от ВСЕХ стоков этой группы
    groups: dict[str, set[str]] = {}

    def _group_key(task: DtTask) -> str:
        # solo — задача сама по себе: собственная одиночная группа,
        # не общая ветвь всех solo (major ревью плана)
        if task.parallel_group == "solo":
            return f"solo:{task.dt_id}"
        return task.parallel_group

    for t in tasks:
        groups.setdefault(_group_key(t), set()).add(t.dt_id)
    dependents: dict[str, set[str]] = {i: set() for i in ids}
    for t in tasks:
        for dep in t.depends_on:
            if dep in dependents:
                dependents[dep].add(t.dt_id)
    for t in tasks:
        foreign = {
            dep for dep in t.depends_on
            if dep in ids
        }
        by_group: dict[str, set[str]] = {}
        own_key = _group_key(t)
        # рёбра, обоснованные delivered_by, из правила стоков исключены
        # (verify точечно за проверяемым — §3 спеки, minor круга 2)
        foreign -= set(t.delivered_by)
        for dep in foreign:
            dep_group = next(
                g for g, members in groups.items() if dep in members
            )
            if dep_group != own_key:
                by_group.setdefault(dep_group, set()).add(dep)
        for g, deps in by_group.items():
            sinks = {
                member for member in groups[g]
                if not (dependents[member] & groups[g])
            }
            missing = sinks - set(t.depends_on)
            if missing:
                findings.append(
                    f"{t.dt_id}: зависит от группы {g}, но не от всех её "
                    f"стоков (нет: {', '.join(sorted(missing))})"
                )
    return findings
```

- [ ] **Step 4: Тест согласованности BEH-грамматики с мостом**

```python
def test_beh_binding_grammar_matches_task_bridge() -> None:
    """Дубликат checked_by-регекса запинован: обе стороны читают одну
    фикстуру одинаково — включая блок с ДВУМЯ строками checked_by
    (правка поверх старой: обе стороны обязаны взять последнюю)."""
    from governance.decomposition_guard import _parse_beh_bindings
    from governance.task_bridge import parse_behaviour

    double = BEH + (
        "\n#### BEH-05: Пять\n**checked_by** `kind: e2e` "
        "`target: tests/test_old.py::test_five`\n"
        "**checked_by** `kind: integration` "
        "`target: tests/test_new.py::test_five`\n"
    )
    scenarios = parse_behaviour(double)
    bindings = _parse_beh_bindings(double)
    for sc in scenarios:
        target, kind = bindings[sc.beh_id]
        expected = (
            sc.checked_target.split("::", 1)[0]
            if sc.checked_target else None
        )
        assert target == expected
        assert kind == (sc.checked_kind if sc.checked_target else None)
```

(Публичный парсер моста называется `parse_behaviour` —
`governance/task_bridge.py:118`; атрибуты `checked_target`/`checked_kind`
сверить с фактической формой `Scenario` перед написанием ассертов.)

- [ ] **Step 5: Прогнать — PASS; полный набор; ruff по своим файлам.**

- [ ] **Step 6: Commit**

```bash
git add governance/decomposition_guard.py tests/test_governance_decomposition_guard.py
git commit -m "feat(decomposition_guard): инварианты графа DT — сюръекция, single-owner, verify-контракт, ацикличность, стоки групп"
```

---

### Task 5: runner — шаг авторинга, консоль, preflight

**Files:**
- Modify: `governance/runner.py` (`_AUTHOR_STEPS`, preflight в
  `_step_authoring`)
- Modify: `governance/console_model.py` (`PIPELINE_KEYS`)
- Test: `tests/test_governance_runner.py`,
  `tests/test_governance_console_model.py`

**Interfaces:**
- Consumes: kind `"decomposition"` в `ops._AUTHOR_DSL` (Task 2);
  `target_profile_declares` (policy_sources, уже есть).
- Produces: шаг `("author-decomposition", "decomposition",
  "30-decomposition.md")` в `_AUTHOR_STEPS` — Task 6 гейтит его артефакт.

- [ ] **Step 1: Красные тесты**

```python
def test_author_steps_include_decomposition_after_design() -> None:
    keys = [k for k, _, _ in runner._AUTHOR_STEPS]
    assert keys.index("author-design") < keys.index("author-decomposition")
    assert runner._AUTHOR_STEPS[-1] == (
        "author-decomposition", "decomposition", "30-decomposition.md"
    )
```

В test_governance_console_model.py существующий двусторонний тест порядка
(`test_pipeline_keys_cover_every_author_step_in_order`) закраснеет сам при
правке `_AUTHOR_STEPS` без правки `PIPELINE_KEYS` — прогнать и увидеть FAIL
обоих.

Preflight (переиспользование механики Task 8 спеки 1): в существующем
`test_start_stops_preflight_when_target_profile_lacks_design` рядом —

```python
def test_start_stops_preflight_when_target_profile_lacks_decomposition(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """target с 4-узловым профилем (design есть, decomposition нет) ⇒
    stopped_preflight ДО единого вызова авторинга."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    kwargs = _start_kwargs(tmp_path, "r-preflight-no-decomp", ops)
    _write_four_node_profile(Path(kwargs["target_dir"]))  # хелпер: профиль
    # спеки 1 (charter..design) БЕЗ узла decomposition

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"
    assert ops.authored == []
```

(Хелпер `_write_four_node_profile` — копия `_write_stale_profile` с design;
`_start_kwargs` уже материализует актуальный 5-узловой профиль — после
правки Task 1 регрессионный `test_start_preflight_silent_on_four_node_profile`
переименовать/обновить на 5-узловой актуальный.)

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Правки**

`runner.py`: `_AUTHOR_STEPS` += четвёртая строка
`("author-decomposition", "decomposition", "30-decomposition.md")`.
Preflight в `_step_authoring` — генерализация одной проверки на кортеж
(design остаётся, урок «проверка охраняет предстоящий авторинг» сохраняется):

```python
    authoring_pending = any(
        op_status(state, key) != "completed" for key, _, _ in _AUTHOR_STEPS
    )
    if authoring_pending:
        for node in ("design", "decomposition"):
            if not target_profile_declares(
                state.target_dir, state.profile, node
            ):
                print(
                    f"_step_authoring: {state.target_dir}/{state.profile} "
                    f"не декларирует узел {node!r} — "
                    f"{PREFLIGHT_PROCEDURE_HINT}"
                )
                state.status = "stopped_preflight"
                save(state)
                return False
```

`console_model.py`: в `PIPELINE_KEYS` вставить `"author-decomposition"`
между `"author-design"` и `"commit"`.

Тестовая инфраструктура runner-набора (minor ревью плана — иначе полный
набор падает KeyError, а не ассертами): `FakeOps.author` в
`tests/test_governance_runner.py` держит словарь-литерал kind → имя файла
(четыре ключа) — добавить `"decomposition": "30-decomposition.md"` и
генерацию файла с валидным frontmatter (пин design — blob-хеш
`20-design.md` фикстуры, как соседние kind'ы делают со своими
upstream'ами). Зелёные бандл-фикстуры start()-тестов дополнить валидным
`30-decomposition.md` (DT, покрывающие BEH фикстуры) — ЗДЕСЬ, не в Task 6.

- [ ] **Step 4: Прогнать — PASS (оба файла тестов + полный набор).**

- [ ] **Step 5: Commit**

```bash
git add governance/runner.py governance/console_model.py \
  tests/test_governance_runner.py tests/test_governance_console_model.py
git commit -m "feat(runner): шаг author-decomposition + preflight обоих узлов + консоль"
```

---

### Task 6: runner — S4-гарды decomposition

**Files:**
- Modify: `governance/runner.py` (`_GATE_EDGES`, `_step_gate`)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: `decomposition_guard.graph_findings` (Task 4); существующие
  механики `_GATE_EDGES`-цикла, DSL-EMPTY-кортежа, GC-COMPLETENESS.
- Produces: находки `GC-COMPLETENESS(decomposition)`,
  `GC-UNPINNED/GC-STALE(prospective)` для ребра decomposition→design,
  `GC-DSL-EMPTY(prospective)` для 30-decomposition.md,
  `GC-DT-GRAPH: <finding>` — все стопят S4 (`stopped_gate`).

- [ ] **Step 1: Красные тесты** (по канону тестов design-гардов рядом —
  `_design_pin_ops`-хелперы; писать зеркальные `_decomposition_*`):

```python
def test_gate_stops_when_decomposition_missing_from_bundle(...):
    # профиль 5-узловой, бандл без 30-decomposition.md ⇒ stopped_gate,
    # findings содержит "GC-COMPLETENESS(decomposition)"

def test_gate_decomposition_unpinned_edge_stops(...):
    # 30-decomposition.md с traces_to: [design], без пина ⇒ GC-UNPINNED

def test_gate_decomposition_stale_pin_stops(...):
    # пин design протух ⇒ GC-STALE

def test_gate_decomposition_undeclared_design_edge_stops(...):
    # traces_to без design ⇒ GC-UNPINNED «ребро design не объявлено»

def test_gate_decomposition_dsl_empty_stops(...):
    # файл без единого `#### DT-` ⇒ GC-DSL-EMPTY c формой `#### DT-NN: …`

def test_gate_dt_graph_finding_stops(...):
    # валидный DSL, но BEH-02 не покрыт ⇒ stopped_gate,
    # findings содержит "GC-DT-GRAPH" и "BEH-02"
```

(Полные тела — копия соседних design-тестов с заменой файла/узла; фикстуры
бандла дополняются валидным 30-decomposition.md в зелёных путях СРАЗУ — все
существующие 4-узловые green-path фикстуры runner-тестов дополнить файлом
и пином, иначе они лягут на новых гардах: это ЧАСТЬ этого шага.)

- [ ] **Step 2: Прогнать — FAIL новых, зафиксировать падения старых
  зелёных путей (ожидаемо до правки фикстур).**

- [ ] **Step 3: Правки**

`_GATE_EDGES` += `("30-decomposition.md", "design", "20-design.md", True)`.
GC-COMPLETENESS — генерализовать блок design на цикл:

```python
    node_paths: dict[str, Path] = {}
    for node, node_file in (
        ("design", "20-design.md"),
        ("decomposition", "30-decomposition.md"),
    ):
        node_paths[node] = Path(state.target_dir) / state.bundle_dir / node_file
        node_required = target_profile_declares(
            state.target_dir, state.profile, node
        )
        if node_required and not node_paths[node].exists():
            ...  # существующий текст находки с подстановкой node/node_file
```

Ниже по `_step_gate` гард GC-DESIGN-COVERAGE использует `design_path` —
перевести его на `node_paths["design"]` (minor ревью плана: снос имени
без перевода потребителя дал бы NameError на каждом прогоне); гард
GC-DT-GRAPH (ниже) берёт `node_paths["decomposition"]` оттуда же.

DSL-EMPTY-кортеж += запись:

```python
        (
            "30-decomposition.md",
            r"^####\s+DT-\d+:",
            "DT-задач",
            "#### DT-NN: <название> · type: implement|verify · owner: <роль>",
        ),
```

Гард графа — после блока GC-DESIGN-COVERAGE, тем же паттерном
(existence-гарды обоих файлов явные):

```python
    beh_path = Path(state.target_dir) / state.bundle_dir / "15-behaviour-spec.md"
    decomp_path = (
        Path(state.target_dir) / state.bundle_dir / "30-decomposition.md"
    )
    if beh_path.exists() and decomp_path.exists():
        graph = [
            f"error GC-DT-GRAPH: {finding}"
            for finding in decomposition_guard.graph_findings(
                beh_path.read_text(encoding="utf-8"),
                decomp_path.read_text(encoding="utf-8"),
            )
        ]
        if graph:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(graph) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
```

Импорт: `from governance import decomposition_guard` рядом с design_guard.

- [ ] **Step 4: Обновить зелёные фикстуры** (валидный 30-decomposition.md с
  верным пином design и DT, покрывающими BEH фикстуры). Отдельно (minor
  круга 2): хелпер `tests/test_governance_runner.py::_repin_design`
  переписывает 20-design.md по текущим requirements/behaviour — его blob
  меняется, и пин design внутри 30-decomposition.md протухает (GC-STALE),
  а DT старой фикстуры ссылаются на исчезнувшие BEH (GC-DT-GRAPH).
  Расширить хелпер до перепиновки ВСЕЙ цепочки (переименовать в
  `_repin_bundle`: перепиновать design И перегенерировать/перепиновать
  30-decomposition.md с DT-покрытием ПОСТправочного behaviour-spec) и
  перевести оба resume-теста, зовущих его после подмены behaviour-spec,
  на новый хелпер. Существующий
  тест согласованности `test_gate_edges_derived_from_bundle_dag` закраснеет
  до Task 7 (ребро в `_GATE_EDGES` есть, в `_BUNDLE_DAG` ещё нет): пометить
  xfail строкой `@pytest.mark.xfail(reason="ребро DAG приходит Task 7",
  strict=True)` И СНЯТЬ её в Task 7 (strict=True сам напомнит — тест
  упадёт как XPASS).

- [ ] **Step 5: Прогнать — PASS (кроме strict-xfail).**

- [ ] **Step 6: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): S4-гарды decomposition — ребро, полнота, DSL, граф DT"
```

---

### Task 7: task_bridge — DAG + `--legacy-bundle=3|4`

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: `_BUNDLE_DAG`/`_ANCHOR_*` (выводятся, не хардкодятся);
  сигнатуры `stamp_bundle_approved`/`conform_approved`/`deliver`/
  `deliver_conform` с текущим `legacy_bundle: bool`.
- Produces: `_BUNDLE_DAG` с пятым узлом (якорь автоматически —
  decomposition); параметр `legacy_bundle: int | None = None` (None —
  полный DAG; 3 — префикс charter..behaviour; 4 — префикс ..design) во ВСЕХ
  четырёх функциях и CLI; `_check_bundle_composition(target_dir, bundle_dir,
  dag) -> None` (RuntimeError при неточном составе). Task 8 потребляет
  полный DAG.

- [ ] **Step 1: Красные тесты**

```python
def test_bundle_dag_terminates_at_decomposition() -> None:
    from governance import task_bridge as tb
    assert tb._BUNDLE_DAG[-1] == ("30-decomposition.md", ("design",))
    assert tb._ANCHOR_NODE_ID == "decomposition"


def test_legacy_bundle_exact_composition() -> None:
    # каталог с ровно 00/10/15/20 (4 узла):
    #   legacy_bundle=4  ⇒ штамп проходит, якорь design
    #   legacy_bundle=3  ⇒ RuntimeError (состав не совпал точно)
    #   legacy_bundle=None ⇒ RuntimeError переходного режима с процедурой
    #     (в тексте: доавторить 30-decomposition.md ЛИБО --legacy-bundle)
    ...


def test_legacy_flag_requires_value() -> None:
    from governance.task_bridge import main
    with pytest.raises(SystemExit):
        main(["--run-id", "r-x", "--legacy-bundle"])  # без значения — отказ
```

(Полное тело composition-теста — по образцу существующих тестов
`stamp_bundle_approved`/`conform_approved` legacy-путей: они уже
материализуют бандлы во tmp_path; переписать их с bool на 3|4 — эти
существующие тесты падут на смене сигнатуры, их обновление входит сюда.)

- [ ] **Step 2: Прогнать — FAIL; снять strict-xfail Task 6 И обновить
  производную `required` в `test_gate_edges_derived_from_bundle_dag`**
  (major ревью плана: тест выводит 4-й элемент как
  `task_bridge._node_id(fname) == "design"` — для 30-decomposition.md это
  даст False против True в `_GATE_EDGES`; заменить вывод на
  `task_bridge._node_id(fname) in {"design", "decomposition"}` — ребро
  decomposition ОБЯЗАТЕЛЬНОЕ, понижать флаг нельзя: fail-open на
  необъявленном ребре).

- [ ] **Step 3: Правки**

`_BUNDLE_DAG` += `("30-decomposition.md", ("design",))` (якорные константы
пересчитаются выводом). `_BUNDLE_DAG_LEGACY`/`_LEGACY_ANCHOR_*` заменить
функцией:

```python
def _dag_for(legacy_bundle: int | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """None — полный DAG; 3|4 — точный префикс. Иное значение — ValueError."""
    if legacy_bundle is None:
        return _BUNDLE_DAG
    if legacy_bundle in (3, 4):
        return _BUNDLE_DAG[:legacy_bundle]
    raise ValueError(f"legacy_bundle: ожидается 3 или 4, получено {legacy_bundle!r}")


def _check_bundle_composition(
    target_dir: str, bundle_dir: str,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    """Заявленный состав обязан совпасть с фактическим РОВНО (спека §4:
    «по самому длинному существующему» запрещён — красил бы
    недоавторенный бандл зелёным)."""
    declared = {fname for fname, _ in dag}
    known = {fname for fname, _ in _BUNDLE_DAG}
    actual = {
        p.name for p in (Path(target_dir) / bundle_dir).glob("*.md")
        if p.name in known
    }
    if actual != declared:
        raise RuntimeError(
            f"состав бандла {sorted(actual)} не совпадает с заявленным "
            f"{sorted(declared)}: доавторьте недостающие узлы либо "
            "передайте --legacy-bundle=3|4 с ТОЧНЫМ фактическим составом"
        )
```

Во всех четырёх функциях: параметр `legacy_bundle: int | None = None`
и `dag = _dag_for(legacy_bundle)`; якоря — `dag[-1][0]` /
`_node_id(dag[-1][0])` вместо пары module-констант `_LEGACY_*`.
МЕСТО вызова `_check_bundle_composition` (major ревью плана — инвариант
приёмки PR #96 «чтение бандла строго ПОСЛЕ чекаута базы»):

- в `deliver`/`deliver_conform` — ПОСЛЕ `ops.is_dirty` и
  `ops.checkout_and_pull`, ровно там, где сейчас живут existence-гарды
  бандла (никакого «первым делом»: до чекаута каталог бандла может не
  существовать, и состав судился бы по произвольному состоянию чекаута);
- в `stamp_bundle_approved`/`conform_approved` — в начале функции (их
  зовут уже ПОСЛЕ чекаута — из deliver-обёрток либо тестами по
  материализованному каталогу).

Preflight профиля в `deliver` (MAJOR круга 2 — §4 спеки требует
fail-closed preflight decomposition в start/resume/DELIVER): существующая
проверка захардкожена на узел `design` — расширить на НАБОР узлов
активного DAG:

```python
    for node in ("design", "decomposition"):
        if any(fname == _AUTHOR_FILENAMES_BY_NODE[node] for fname, _ in dag) \
                and not target_profile_declares(target_dir, profile, node):
            raise RuntimeError(...)  # существующий текст с подстановкой node
```

(форму сопоставления узел↔файл взять фактическую по коду deliver; суть —
проверяются ровно узлы, входящие в выбранный `dag`, поэтому
`--legacy-bundle=3` не требует design, `=4` требует design но не
decomposition, полный DAG требует оба). Зеркальный тест
`test_deliver_refuses_when_target_profile_lacks_decomposition` — рядом с
существующим design-вариантом; фикстура существующего design-варианта
(бандл только 15/20) дополняется до состава, проходящего
`_check_bundle_composition`, В ЭТОМ ЖЕ шаге.

Булевы ветки `deliver` (minor круга 2 — `if legacy_bundle:` истинно и для
int 4, что уводило бы в behaviour-spec-ветку с ЛОЖНЫМ пином design =
blob 15-behaviour-spec.md и пустым design_text): переписать ОБЕ ветки на
данные DAG — `anchor_path = base / dag[-1][0]`; existence-гард и blob —
от фактического якоря `dag[-1]`; специальные `if legacy_bundle:` /
`if not legacy_bundle:` уходят целиком.

Существующие НЕ-легаси тесты доставки/конформа материализуют 4-узловые
бандлы и лягут на проверке состава — их фикстуры дополняются валидным
30-decomposition.md В ЭТОМ ЖЕ шаге (как минимум:
`test_deliver_reads_bundle_only_after_base_checkout` — файл добавляется
внутри его checkout_and_pull-хука, `test_deliver_dirty_target_refuses`,
`test_conform_refuses_draft`; пройтись grep'ом по фикстурам bundle_dir).
CLI:

```python
    parser.add_argument(
        "--legacy-bundle", type=int, choices=(3, 4), default=None,
        help="точный состав легаси-бандла: 3 (до design) или 4 (до "
        "decomposition); без флага — полный DAG; значение обязательно",
    )
```

(`action="store_true"` убрать; argparse сам откажет на флаге без значения.)

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(task_bridge): узел decomposition в DAG; --legacy-bundle=3|4 с проверкой точного состава"
```

---

### Task 8: task_bridge — генерация задач из DT-грамматики

**Files:**
- Modify: `governance/task_bridge.py` (`render_tasks`, `deliver`)
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: `decomposition_guard.parse_dt_tasks`/`graph_findings` (Task 3/4),
  `Scenario` с `beh_id/title/checked_target/checked_kind`, полный DAG (Task 7).
- Produces: `render_tasks_dt(ws_id, subject, bundle_path, scenarios,
  dt_tasks, generated_at, anchor_blob, design_text) -> str`; `deliver` на
  полном DAG читает 30-decomposition.md и идёт DT-путём; legacy-пути (3|4)
  идут прежним `render_tasks` без изменений.

- [ ] **Step 1: Красные тесты**

```python
def test_render_dt_one_task_per_dt_with_bindings_and_edges() -> None:
    # DT-01 (BEH-01+BEH-02, group core), DT-02 (BEH-03, depends_on [DT-01])
    # ⇒ ровно TASK-001, TASK-002; чеклист TASK-001 несёт оба BEH и
    # «проверка группы: tests/test_a.py (kind: integration) зелёные на
    # BEH-01, BEH-02»; TASK-002 несёт «**Depends on:** [TASK-001]»;
    # НИКАКОЙ искусственной цепочки: DT без depends_on не получает Depends on
    ...


def test_render_dt_frontmatter_traces_decomposition_from_birth() -> None:
    # рендер (не conform!) сразу пишет traces_to: [decomposition] и
    # upstream_hashes: {decomposition: "<blob 30-decomposition.md>"}
    ...


def test_verify_dt_fails_closed_until_spec_runner_367() -> None:
    # DT c type: verify ⇒ deliver/render_tasks_dt поднимает RuntimeError,
    # в тексте: "verify-first", "spec-runner#367", "@blocked_by"
    ...


def test_dt_path_skips_merge_featureless(...) -> None:
    # два DT с checked_by-целями в одном файле НЕ сливаются мостом —
    # такой вход обязан быть отвергнут graph_findings ДО рендера
    # (deliver зовёт graph_findings и поднимает RuntimeError со списком)
    ...
```

(Полные тела — по образцу соседних тестов render_tasks: собрать
scenarios-фикстуру через существующий парсер behaviour-текста, DT-текст —
литералом; ассерты — на подстроки результата.)

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

`render_tasks_dt` — новая функция рядом с `render_tasks` (легаси-рендер НЕ
трогается — им живут `--legacy-bundle`-пути):

```python
def render_tasks_dt(
    ws_id: str,
    subject: str,
    bundle_path: str,
    scenarios: list[Scenario],
    dt_tasks: list[decomposition_guard.DtTask],
    generated_at: str,
    anchor_blob: str,
    design_text: str = "",
) -> str:
    """tasks.md из решённой декомпозиции: 1 DT = 1 задача.

    Мост — ТРАНСЛЯТОР (§1 спеки): состав задач, типы и рёбра решены
    tech-lead-узлом и проверены гейтом; здесь только джойн BEH →
    checked_by и перевод depends_on → Depends on. Эвристика
    _merge_featureless_by_target_file на этом пути НЕ применяется — её
    инвариант переехал в гейт (single-owner, GC-DT-GRAPH).
    """
    verify_dts = [t.dt_id for t in dt_tasks if t.type == "verify"]
    if verify_dts:
        raise RuntimeError(
            f"verify-DT ({', '.join(verify_dts)}) требуют режим "
            "verify-first spec-runner#367 — он ещё не доставлен "
            "(@blocked_by:spec-runner#367, чекбокс в TODO.md devtools); "
            "implement-DT работают полностью"
        )
    by_beh = {sc.beh_id: sc for sc in scenarios}
    number = {t.dt_id: idx for idx, t in enumerate(dt_tasks, start=1)}
    # заголовок файла — тот же, что в render_tasks (frontmatter с
    # traces_to: [decomposition] и пином anchor_blob; вынести общий
    # хедер-хелпер, чтобы не дублировать literal-строки)
    lines = _render_header(ws_id, subject, generated_at, anchor_blob,
                           anchor_node_id="decomposition")
    lines += _render_resolutions_section(design_text)
    for t in dt_tasks:
        group = [by_beh[b] for b in t.scenarios if b in by_beh]
        beh_ids = [g.beh_id for g in group]
        bindings: list[str] = []
        for g in group:
            if g.checked_target:
                pair = f"{g.checked_target} (kind: {g.checked_kind})"
                if pair not in bindings:
                    bindings.append(pair)
        check = (
            f"проверка группы: {', '.join(bindings)} зелёные на "
            f"{', '.join(beh_ids)}"
            if bindings
            else f"проверка группы {', '.join(beh_ids)} определена и зелёная"
        )
        idx = number[t.dt_id]
        lines += [
            f"### TASK-{idx:03d}: {t.title}",
            "P2 | TODO   Est: 0.5d",
            "",
            f"Реализовать сценарии {', '.join(beh_ids)} ({t.dt_id}, "
            f"группа {t.parallel_group}).",
            f"Source: {bundle_path}#{t.dt_id}",
        ]
        if t.depends_on:
            deps = ", ".join(
                f"TASK-{number[d]:03d}" for d in t.depends_on if d in number
            )
            lines.append(f"**Depends on:** [{deps}]")
        lines += ["", "**Checklist:**"]
        lines += [f"- [ ] реализовать {g.beh_id}: {g.title}" for g in group]
        traces = []
        for g in group:
            traces += [x for x in g.traces if x not in traces]
        lines += [
            f"- [ ] {check}",
            "",
            f"**Traces to:** [{', '.join(traces)}]" if traces else "",
            "",
        ]
    return "\n".join(line for line in lines if line is not None) + "\n"
```

(`_render_header` — вынести из текущего `render_tasks` фактические строки
хедера/frontmatter в общий хелпер; при выносе поведение `render_tasks`
байт-в-байт сохраняется — регрессионные тесты рендера уже это держат.)

`deliver`: на полном DAG (`legacy_bundle is None`) читать
`30-decomposition.md`, звать `graph_findings(behaviour_text,
decomposition_text)` — непустой список ⇒ RuntimeError с его текстом (мост
не рендерит невалидный граф даже в обход S4, deliver зовётся и напрямую);
затем `parse_dt_tasks` и `render_tasks_dt`. Пин якоря — blob
30-decomposition.md (якорь — `dag[-1]`, Task 7). ИСТОЧНИК `design_text`
(minor круга 2): секция «Решения открытых вопросов» рендерится из
`20-design.md`, который читается ОТДЕЛЬНО от якоря (после смены якоря
`design_path`-выражение через `_ANCHOR_FILENAME` кормило бы
`parse_design_resolutions` текстом decomposition — секция молча исчезала
бы). Ожидания существующих deliver-тестов обновляются здесь же:
`traces_to == ['decomposition']`, секция резолюций по-прежнему
присутствует. Legacy-пути (3|4) — прежний `render_tasks`, якорь —
терминал соответствующего префикса DAG.

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(task_bridge): генерация tasks из DT-грамматики (1 DT = 1 задача), verify fail-closed"
```

---

### Task 9: Сквозной смоук + доки

**Files:**
- Modify: `tests/test_governance_runner.py` (смоук)
- Modify: `README.md`, `Makefile`

**Interfaces:**
- Consumes: всё выше; существующий сквозной смоук 4-узлового прогона (T9
  плана спеки 1) — образец.

- [ ] **Step 1: Смоук-тест** (по образцу существующего сквозного T9):
  FakeOps-прогон start→…→completed на 5-узловом профиле с валидным бандлом
  (все пины, DT покрывают BEH), затем deliver → tasks-спека: ассерты на
  `traces_to: [decomposition]`, задачи по DT, рёбра Depends on. Негативный
  полукруг: тот же бандл с непокрытым BEH ⇒ stopped_gate c GC-DT-GRAPH.

- [ ] **Step 2: Прогнать — PASS с первого раза** (всё уже реализовано;
  падение = найден шов, чинить по существу).

- [ ] **Step 3: Доки**: README — строка про `governance/decomposition_guard.py`
  в списке модулей; help/README `--legacy-bundle` — упомянуть значения 3|4 и
  точный состав.

- [ ] **Step 4: Полный набор + ruff по своим файлам; явный RC.**

- [ ] **Step 5: Commit**

```bash
git add tests/test_governance_runner.py README.md Makefile
git commit -m "test: сквозной смоук 5-узлового конвейера; docs: decomposition_guard и legacy-bundle=3|4"
```

---

## Self-Review (выполнен при написании)

1. **Spec coverage:** §2 профиль — Task 1; §3 грамматика/инварианты — Task
   3/4; §4 таблица: profiles — Task 1, runner — Task 5/6, ops — Task 2,
   console — Task 5, guard — Task 3/4, task_bridge — Task 7/8, переходный
   режим — Task 7; §5 тесты — распределены по задачам 1:1; §7a блокер —
   Task 1 (чекбокс) + Task 8 (fail-closed). Якорь conform_approved уже
   выводится из DAG (реализация #145) — Task 7 наследует, отдельной правки
   не требуется (уточнение к §4: «обобщается этой спекой» уже случилось в
   спеке 1 фактом реализации).
2. **Placeholders:** «...» стоят ТОЛЬКО в местах, где план явно называет
   образец-сосед и требует копию с заменой (полные тела в канонах рядом);
   формулы, регексы, сигнатуры и тексты находок — дословно.
3. **Type consistency:** `parse_dt_tasks -> tuple[list[DtTask], list[str]]`
   един в Task 3/4/8; `graph_findings(behaviour_text, decomposition_text)`
   един в Task 4/6/8; `legacy_bundle: int | None` един в Task 7/8;
   `render_tasks_dt` объявлен в Task 8 и потребляется только там и в смоуке.
