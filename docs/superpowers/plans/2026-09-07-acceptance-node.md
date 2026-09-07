# Acceptance-узел конвейера — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Шестой узел `acceptance` (owner qa) в behaviour-конвейере:
AC-грамматика критериев приёмки с гейтом Must-покрытия FR/NFR, второй
upstream-пин decomposition, `--legacy-bundle=3|4|5`, справочная секция AC
в tasks-спеке.

**Architecture:** Зеркало механики узлов design/decomposition (спеки 1/3,
реализации #145/#147): строки в data-driven списки (`_AUTHOR_STEPS`,
`_GATE_EDGES`, `_BUNDLE_DAG`, preflight-циклы, DSL-EMPTY, GC-COMPLETENESS)
плюс новый чистый модуль `acceptance_guard.py`. Особые точки: legacy=5 —
НЕ срез нового DAG (отдельный вариант со старым upstream decomposition);
DT-ветвления deliver переводятся с `legacy_bundle is None` на состав DAG.

**Tech Stack:** Python stdlib (канон governance/ — без steward-импорта в
runner, без pydantic), pytest, FakeOps-фикстуры.

**Spec:** `docs/superpowers/specs/2026-09-07-acceptance-node-conveyor-design.md`
(влита PR #153). Конфликты решаются в пользу спеки.

## Global Constraints

- Файл бандла — ровно `25-acceptance.md`; frontmatter: `spec_stage:
  acceptance`, `status: draft`, `owner_role: qa`, `traces_to:
  [requirements, behaviour-spec]`, `upstream_hashes: {requirements:
  "<blob 10>", behaviour-spec: "<blob 15>"}`.
- Заголовок AC — ровно `#### AC-NN: <название> · verification:
  test|manual|metric`; метаданные-строки `traces: [FR-…|NFR-…]` (≥1),
  `scenarios: [BEH-…]` (обязателен при verification: test).
- Грамматика id в ссылках/заголовках: суффикс `[a-z]?`
  (`FR-\d+[a-z]?`/`NFR-\d+[a-z]?`/`BEH-\d+[a-z]?`/`AC-\d+[a-z]?`) —
  класс BEH-18a, PR #148.
- Строка-декларация пустого набора Must (дословно):
  `Must-требований во входном наборе нет`.
- Гарды — чистые функции над строками (канон design_guard); runner БЕЗ
  импорта steward/task_bridge.
- Уроки PR #145/#148/#152: блок сущности кончается на секции уровня 1–3;
  near-miss заголовки и дубли id — находки; NEAR-регексы широкие
  (`[^\s:]*`), строгие — точные.
- legacy=5 — точный состав `00/10/15/20/30`; `_dag_for(5)` возвращает
  СТАРЫЙ 5-узловой вариант (decomposition upstream `("design",)`), не
  срез нового DAG.
- `profiles/` — authority-root: PR этого плана мержит человек.
- ruff — только свои файлы; тесты `uv run --frozen --group governance
  python -m pytest tests/ -q` с явным RC (не пайп); 88 chars.

## Карта файлов

- Create: `governance/acceptance_guard.py`,
  `tests/test_governance_acceptance_guard.py`.
- Modify: `profiles/team-exp.yaml`, `governance/ops.py`,
  `governance/runner.py`, `governance/console_model.py`,
  `governance/task_bridge.py`, `README.md`, `Makefile`.
- Tests: `tests/test_governance_profile.py`, `tests/test_governance_ops.py`,
  `tests/test_governance_runner.py`, `tests/test_governance_console_model.py`,
  `tests/test_governance_task_bridge.py`.

---

### Task 1: Профиль team-exp — узел acceptance, второй upstream decomposition

**Files:**
- Modify: `profiles/team-exp.yaml`
- Test: `tests/test_governance_profile.py`

**Interfaces:**
- Produces: узел `acceptance` (owner_role qa, template acceptance.md,
  upstream `[requirements, behaviour-spec]`),
  `decomposition.upstream: [design, acceptance]` — читают preflight
  (Task 5/7) и real-steward тест.

- [ ] **Step 1: Красные тесты**

Real-steward тест (`graph.topo_order()`), новый ожидаемый порядок:

```python
    assert graph.topo_order() == [
        "charter", "requirements", "behaviour-spec", "design",
        "acceptance", "decomposition", "tasks",
    ]
```

(Если steward топологически ставит acceptance до design — оба валидны,
рёбер между ними нет: тогда ассерт — на множество и на порядок ПАР
(design < decomposition, acceptance < decomposition); зафиксируй
фактический вывод steward в отчёте.)

yaml-тест (`test_team_exp_profile_has_design_node` и соседний
decomposition-тест) — добавить/заменить:

```python
    assert nodes["acceptance"]["owner_role"] == "qa"
    assert nodes["acceptance"]["upstream"] == ["requirements", "behaviour-spec"]
    assert nodes["decomposition"]["upstream"] == ["design", "acceptance"]
```

(Существующий ассерт `nodes["decomposition"]["upstream"] == ["design"]`
ЗАМЕНЯЕТСЯ.)

- [ ] **Step 2: Прогнать — FAIL.**

Run: `uv run --frozen --group governance python -m pytest tests/test_governance_profile.py -q; echo RC=$?`

- [ ] **Step 3: Правка `profiles/team-exp.yaml`**

Между design и decomposition:

```yaml
  - id: acceptance
    template: acceptance.md
    owner_role: qa
    upstream: [requirements, behaviour-spec]
```

В узле decomposition: `upstream: [design, acceptance]`; комментарий-
отступление сузить до «compile (decomposition→maestro, tasks→spec-runner
у steward) не реализуется — лейн Mode-2» (§2/§8 спеки, acceptance больше
не срезан). ШАПОЧНЫЙ комментарий профиля (верх файла) тоже обновить —
после правки он не должен утверждать урезанный состав, противоречащий
фактическому (minor круга 1: перечитай шапку и приведи к 6-узловой
реальности).

- [ ] **Step 4: Прогнать — PASS; полный набор** (PIPELINE_KEYS-сверка не
  краснеет — профиль конвейерными списками не читается).

- [ ] **Step 5: Commit**

```bash
git add profiles/team-exp.yaml tests/test_governance_profile.py
git commit -m "feat(profile): узел acceptance (qa), второй upstream decomposition"
```

---

### Task 2: DSL авторинга — acceptance + двухпиновый decomposition

**Files:**
- Modify: `governance/ops.py`
- Test: `tests/test_governance_ops.py`

**Interfaces:**
- Produces: ключ `"acceptance"` в `_AUTHOR_FILENAMES`/`_AUTHOR_DSL`;
  обновлённый `_AUTHOR_DSL["decomposition"]` (traces_to
  `[design, acceptance]`, два пина).

- [ ] **Step 1: Красные тесты**

```python
def test_author_dsl_covers_acceptance() -> None:
    from governance.ops import _AUTHOR_DSL, _AUTHOR_FILENAMES

    assert _AUTHOR_FILENAMES["acceptance"] == "25-acceptance.md"
    dsl = _AUTHOR_DSL["acceptance"]
    for token in (
        "spec_stage: acceptance", "owner_role: qa",
        "traces_to: [requirements, behaviour-spec]",
        "#### AC-NN:", "verification: test|manual|metric",
        "traces:", "scenarios:",
        "Must-требований во входном наборе нет",
    ):
        assert token in dsl


def test_decomposition_dsl_carries_acceptance_pin() -> None:
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["decomposition"]
    assert "traces_to: [design, acceptance]" in dsl
    assert "<hash25>" in dsl
    assert "25-acceptance.md" in dsl
```

Существующий `test_author_dsl_covers_decomposition` (минор круга 2 плана:
токен-подстрока `"traces_to: [design]"` в его списке перестанет
совпадать) — обновить токен на `"traces_to: [design, acceptance]"`.

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Записи**

`_AUTHOR_FILENAMES`: `"acceptance": "25-acceptance.md",`.
`_AUTHOR_DSL["acceptance"]` (дословно, канон записи design):

```python
    "acceptance": (
        "YAML frontmatter (required): spec_stage: acceptance, "
        "status: draft, owner_role: qa, traces_to: [requirements, "
        "behaviour-spec], upstream_hashes: {requirements: \"<hash10>\", "
        "behaviour-spec: \"<hash15>\"} where <hash10> and <hash15> are "
        "the outputs of `git hash-object <bundle_dir>/10-requirements.md` "
        "and `git hash-object <bundle_dir>/15-behaviour-spec.md`. "
        "The document MUST contain these sections: Критерии приёмки, "
        "Инварианты покрытия, Порог приёмки, Вне объёма. Критерии "
        "приёмки: every criterion is a heading exactly `#### AC-NN: "
        "<title> · verification: test|manual|metric` followed by "
        "metadata lines `traces: [FR-…|NFR-…]` (>=1, ids from "
        "10-requirements.md, both classes are legal) and `scenarios: "
        "[BEH-…]` (REQUIRED for verification: test — the criterion is "
        "proven by those green scenarios; optional otherwise), then a "
        "prose paragraph naming the observable sign of fulfilment. "
        "verification: manual — the prose MUST name what a human "
        "observes; verification: metric — the prose MUST name the "
        "SOURCE of the number (artifact or named constant), never "
        "hard-code the number in the criterion. Every Must-priority "
        "requirement (FR and NFR alike) MUST be covered by at least one "
        "AC; Should and below are at qa's discretion. If the input set "
        "of Must requirements is empty, the document MUST instead carry "
        "the exact line `Must-требований во входном наборе нет`. "
        "Порог приёмки: which ACs must hold before the workstream is "
        "declared delivered (default: all with verification: test; "
        "manual/metric — by enumeration). Вне объёма: what is "
        "deliberately not an acceptance criterion. Forbidden: do not "
        "migrate the charter's AC numbering (this node is the single "
        "source of acceptance, authored fresh from requirements/"
        "behaviour); do not invent FR/NFR/BEH ids; do not restate "
        "requirements as criteria without an observable sign."
    ),
```

`_AUTHOR_DSL["requirements"]` — ДОПОЛНИТЕЛЬНАЯ правка (major круга 1
ревью плана: действующий контракт требует `**Priority**` только у FR —
гейт Must-покрытия стопил бы конформный бандл после шести оплаченных
вызовов): фрагмент про NFR дополнить обязательной строкой приоритета —
`Non-functional requirements use `#### NFR-NN: <title>` followed by the
same `**Priority**: Must` (or Should) line` (точную старую формулировку
взять из ops.py:~196 и заменить цельным фрагментом). Тест:

```python
def test_requirements_dsl_mandates_priority_for_nfr() -> None:
    from governance.ops import _AUTHOR_DSL

    dsl = _AUTHOR_DSL["requirements"]
    nfr_part = dsl.split("NFR-NN")[1]
    assert "**Priority**" in nfr_part
```

`_AUTHOR_DSL["decomposition"]` — правка существующей записи: фрагмент
`traces_to: [design], upstream_hashes: {design: \"<hash20>\"} where
<hash20> is the output of ...20-design.md` заменить на двухпиновую форму
(канон design-записи с двумя хешами):

```
traces_to: [design, acceptance], upstream_hashes: {design: "<hash20>",
acceptance: "<hash25>"} where <hash20> and <hash25> are the outputs of
`git hash-object <bundle_dir>/20-design.md` and
`git hash-object <bundle_dir>/25-acceptance.md`
```

(Точную старую формулировку взять из файла; заменить цельным фрагментом,
не по словам.)

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/ops.py tests/test_governance_ops.py
git commit -m "feat(ops): DSL авторинга acceptance + двухпиновый decomposition"
```

---

### Task 3: acceptance_guard — парсер AC-грамматики

**Files:**
- Create: `governance/acceptance_guard.py`
- Test: `tests/test_governance_acceptance_guard.py` (create)

**Interfaces:**
- Produces (Task 4/6/8 потребляют):
  - `@dataclass(frozen=True) AcCriterion: ac_id: str; title: str;
    verification: str; traces: tuple[str, ...]; scenarios: tuple[str, ...]`
  - `parse_ac_criteria(text: str) -> tuple[list[AcCriterion], list[str]]`
    — (критерии, findings формы).

- [ ] **Step 1: Красные тесты** (новый файл; канон —
  tests/test_governance_decomposition_guard.py, только строки):

```python
"""Unit-тесты governance.acceptance_guard: парсер AC и покрытие Must.
Никакого git/ФС — только строки."""

from __future__ import annotations

from governance.acceptance_guard import AcCriterion, parse_ac_criteria

AC_OK = (
    "#### AC-01: Прогон первым действием · verification: test\n"
    "traces: [FR-01, NFR-01]\n"
    "scenarios: [BEH-01]\n"
    "Наблюдаемый признак: живой прогон до платного вызова.\n"
    "\n"
    "#### AC-02: Ручная проверка консоли · verification: manual\n"
    "traces: [FR-02]\n"
    "Оператор видит стадию verify в статусе.\n"
)


def test_parse_two_criteria() -> None:
    crits, findings = parse_ac_criteria(AC_OK)
    assert findings == []
    assert [c.ac_id for c in crits] == ["AC-01", "AC-02"]
    assert crits[0] == AcCriterion(
        ac_id="AC-01", title="Прогон первым действием",
        verification="test", traces=("FR-01", "NFR-01"),
        scenarios=("BEH-01",),
    )
    assert crits[1].scenarios == ()


def test_near_miss_heading_is_a_finding() -> None:
    bad = "#### AC-03 Без двоеточия · verification: test\n"
    crits, findings = parse_ac_criteria(bad)
    assert crits == []
    assert any("AC-03" in f and "грамматик" in f for f in findings)


def test_unknown_suffix_form_is_a_finding() -> None:
    bad = AC_OK + "\n#### AC-02X: Мусорный суффикс · verification: test\n"
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-02X" in f for f in findings)


def test_duplicate_ac_id_is_a_finding() -> None:
    dup = AC_OK + (
        "\n#### AC-01: Дубль · verification: manual\ntraces: [FR-01]\n"
    )
    _crits, findings = parse_ac_criteria(dup)
    assert any("AC-01" in f and "раза" in f for f in findings)


def test_test_verification_requires_scenarios() -> None:
    bad = (
        "#### AC-05: Тестовый без сценариев · verification: test\n"
        "traces: [FR-01]\n"
    )
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-05" in f and "scenarios" in f for f in findings)


def test_empty_traces_is_a_finding() -> None:
    bad = (
        "#### AC-06: Без трасс · verification: manual\n"
        "traces: []\n"
    )
    _crits, findings = parse_ac_criteria(bad)
    assert any("AC-06" in f and "traces" in f for f in findings)


def test_block_ends_at_next_section() -> None:
    text = (
        "#### AC-01: Одинокий · verification: manual\n"
        "traces: [FR-01]\n"
        "\n## Порог приёмки\n\nscenarios: [BEH-99]\n"
    )
    crits, findings = parse_ac_criteria(text)
    assert findings == []
    assert crits[0].scenarios == ()
```

- [ ] **Step 2: Прогнать — FAIL** (модуля нет).

- [ ] **Step 3: Модуль**

```python
"""Гард acceptance-узла: AC-грамматика и покрытие Must-требований (спека
2026-09-07-acceptance-node §3). Чистые функции над строками — без
git/ФС/steward; потребители — S4-гейт (runner) и мост (task_bridge).
Канон — governance/design_guard.py и governance/decomposition_guard.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

_AC_HEAD_RE = re.compile(
    r"^####\s+(AC-\d+[a-z]?):\s*(.+?)\s*·\s*verification:\s*"
    r"(test|manual|metric)\s*$",
    re.M,
)
# NEAR ловит любую AC-подобную шапку (уроки PR #145/#148): неизвестная
# форма id/строки — находка формы, не молчание.
_AC_NEAR_RE = re.compile(r"^####\s+(AC-[^\s:]*)", re.M)
_SECTION_RE = re.compile(r"^#{1,3}\s", re.M)

#: Дословная декларация пустого набора Must (§3 спеки).
EMPTY_MUST_DECLARATION = "Must-требований во входном наборе нет"


def _list_field(block: str, name: str) -> tuple[str, ...] | None:
    m = re.search(rf"^{name}:\s*\[([^\]]*)\]\s*$", block, re.M)
    if m is None:
        return None
    inner = m.group(1).strip()
    if not inner:
        return ()
    return tuple(part.strip() for part in inner.split(","))


@dataclass(frozen=True)
class AcCriterion:
    """Один критерий приёмки (заголовок #### AC-NN)."""

    ac_id: str
    title: str
    verification: str
    traces: tuple[str, ...]
    scenarios: tuple[str, ...]


def parse_ac_criteria(text: str) -> tuple[list[AcCriterion], list[str]]:
    """AC-грамматика → (критерии, findings формы).

    Findings формы: near-miss заголовок, дубль AC-id, пустой/отсутствующий
    traces, `verification: test` без непустого scenarios. Блок критерия
    ограничен следующим AC-заголовком ЛИБО секцией уровня 1–3 (урок
    PR #145 — хвост документа не читается как метаданные последнего AC).
    """
    findings: list[str] = []
    strict = {m.start() for m in _AC_HEAD_RE.finditer(text)}
    for near in _AC_NEAR_RE.finditer(text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок не соответствует машинной "
                "грамматике AC (`#### AC-NN: <название> · verification: "
                "test|manual|metric`)"
            )
    matches = list(_AC_HEAD_RE.finditer(text))
    seen: dict[str, int] = {}
    crits: list[AcCriterion] = []
    for idx, m in enumerate(matches):
        ac_id = m.group(1)
        seen[ac_id] = seen.get(ac_id, 0) + 1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        section = _SECTION_RE.search(block)
        if section is not None:
            block = block[: section.start()]
        traces = _list_field(block, "traces")
        scenarios = _list_field(block, "scenarios") or ()
        if not traces:
            findings.append(
                f"{ac_id}: строка traces отсутствует или пуста (каждый "
                "критерий обязан покрывать хотя бы одно требование)"
            )
        if m.group(3) == "test" and not scenarios:
            findings.append(
                f"{ac_id}: verification: test без непустого scenarios — "
                "тестовый критерий обязан называть свои сценарии"
            )
        crits.append(AcCriterion(
            ac_id=ac_id, title=m.group(2), verification=m.group(3),
            traces=traces or (), scenarios=scenarios,
        ))
    for ac_id, count in seen.items():
        if count > 1:
            findings.append(
                f"{ac_id}: объявлен {count} раза (ожидается ровно один)"
            )
    return crits, findings
```

- [ ] **Step 4: Прогнать — PASS.**

- [ ] **Step 5: Commit**

```bash
git add governance/acceptance_guard.py tests/test_governance_acceptance_guard.py
git commit -m "feat(acceptance_guard): парсер AC-грамматики с findings формы"
```

---

### Task 4: acceptance_guard — покрытие Must-требований

**Files:**
- Modify: `governance/acceptance_guard.py`
- Test: `tests/test_governance_acceptance_guard.py`

**Interfaces:**
- Consumes: `parse_ac_criteria` (Task 3).
- Produces: `coverage_findings(req_text: str, beh_text: str,
  acc_text: str) -> list[str]` — единая точка для S4 (Task 6); включает
  findings формы парсера. Внутренние: `_parse_requirements(req_text) ->
  tuple[dict[str, str], list[str]]` (id → priority, findings
  достоверности), `_parse_beh_ids(beh_text) -> set[str]`.

- [ ] **Step 1: Красные тесты**

```python
REQ = (
    "#### FR-01: Первое\n**Priority**: Must\nтекст\n\n"
    "#### FR-02: Второе\n**Priority**: Should\nтекст\n\n"
    "#### NFR-01: Бюджет\n**Priority**: Must\nтекст\n"
)
BEH = "#### BEH-01: Один\nтекст\n\n#### BEH-02: Два\nтекст\n"


def test_clean_coverage() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Функция · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\nпроза\n\n"
        "#### AC-02: Бюджет · verification: metric\n"
        "traces: [NFR-01]\nисточник числа — артефакт замера\n"
    )
    assert coverage_findings(REQ, BEH, acc) == []


def test_uncovered_must_fr_and_nfr_are_findings() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Только FR · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\n"
    )
    findings = coverage_findings(REQ, BEH, acc)
    assert any("NFR-01" in f and "не покрыт" in f for f in findings)
    assert not any("FR-02" in f for f in findings)  # Should — не находка


def test_unknown_references_are_findings() -> None:
    from governance.acceptance_guard import coverage_findings
    acc = (
        "#### AC-01: Битые ссылки · verification: test\n"
        "traces: [FR-01, FR-99]\nscenarios: [BEH-01, BEH-99]\n"
    )
    findings = coverage_findings(REQ, BEH, acc)
    assert any("FR-99" in f for f in findings)
    assert any("BEH-99" in f for f in findings)


def test_near_miss_requirement_priority_is_a_finding() -> None:
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Без приоритета\nпроза без строки Priority\n"
    acc = "#### AC-01: X · verification: manual\ntraces: [FR-01]\n"
    findings = coverage_findings(req, BEH, acc)
    assert any(
        "FR-01" in f and "недостоверн" in f for f in findings
    )


def test_empty_must_set_needs_declaration() -> None:
    from governance.acceptance_guard import coverage_findings
    req = "#### FR-01: Только Should\n**Priority**: Should\nтекст\n"
    acc_without = (
        "#### AC-01: X · verification: manual\ntraces: [FR-01]\n"
    )
    assert any(
        "деклара" in f for f in coverage_findings(req, BEH, acc_without)
    )
    acc_with = acc_without + "\nMust-требований во входном наборе нет\n"
    assert coverage_findings(req, BEH, acc_with) == []
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация** (добавить в acceptance_guard.py):

```python
_REQ_HEAD_RE = re.compile(r"^####\s+((?:FR|NFR)-\d+[a-z]?):", re.M)
_REQ_NEAR_RE = re.compile(r"^####\s+((?:FR|NFR)-[^\s:]*)", re.M)
_PRIORITY_RE = re.compile(r"^\*\*Priority\*\*:\s*(\S+)", re.M)
_BEH_ID_RE = re.compile(r"^####\s+(BEH-\d+[a-z]?):", re.M)


def _parse_requirements(req_text: str) -> tuple[dict[str, str], list[str]]:
    """id → priority по строгой грамматике; findings достоверности.

    Near-miss заголовок FR/NFR и заголовок без распознанной строки
    `**Priority**: …` — находки «входное множество недостоверно» (канон
    design_guard, major круга 2 ревью спеки): промах грамматики не
    должен молча сжимать множество Must.
    """
    findings: list[str] = []
    strict = {m.start() for m in _REQ_HEAD_RE.finditer(req_text)}
    for near in _REQ_NEAR_RE.finditer(req_text):
        if near.start() not in strict:
            findings.append(
                f"{near.group(1)}: заголовок requirements не соответствует "
                "машинной грамматике — входное множество недостоверно"
            )
    heads = list(_REQ_HEAD_RE.finditer(req_text))
    priorities: dict[str, str] = {}
    for idx, m in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(req_text)
        block = req_text[m.end() : end]
        pr = _PRIORITY_RE.search(block)
        if pr is None:
            findings.append(
                f"{m.group(1)}: строка **Priority**: не распознана — "
                "входное множество недостоверно"
            )
            continue
        priorities[m.group(1)] = pr.group(1)
    return priorities, findings


def _parse_beh_ids(beh_text: str) -> set[str]:
    return {m.group(1) for m in _BEH_ID_RE.finditer(beh_text)}


def coverage_findings(
    req_text: str, beh_text: str, acc_text: str
) -> list[str]:
    """Инварианты §3 спеки: Must-покрытие FR/NFR + ссылочная целостность.

    Findings накапливаются (гейт показывает всё сразу); пустой список —
    покрытие валидно.
    """
    crits, findings = parse_ac_criteria(acc_text)
    priorities, req_findings = _parse_requirements(req_text)
    findings += req_findings
    beh_ids = _parse_beh_ids(beh_text)

    for c in crits:
        for ref in c.traces:
            if ref not in priorities:
                findings.append(
                    f"{c.ac_id}: ссылка на несуществующее требование {ref}"
                )
        for beh in c.scenarios:
            if beh not in beh_ids:
                findings.append(
                    f"{c.ac_id}: сценарий {beh} отсутствует в behaviour-spec"
                )

    covered: set[str] = set()
    for c in crits:
        covered.update(c.traces)
    must = [rid for rid, pr in priorities.items() if pr == "Must"]
    if not must:
        if EMPTY_MUST_DECLARATION not in acc_text:
            findings.append(
                "acceptance: множество Must-требований пусто, но "
                f"строка-декларация «{EMPTY_MUST_DECLARATION}» отсутствует"
            )
        return findings
    for rid in must:
        if rid not in covered:
            findings.append(
                f"{rid}: Must-требование не покрыто ни одним AC"
            )
    return findings
```

- [ ] **Step 4: Прогнать — PASS; полный набор; ruff по своим файлам.**

- [ ] **Step 5: Commit**

```bash
git add governance/acceptance_guard.py tests/test_governance_acceptance_guard.py
git commit -m "feat(acceptance_guard): Must-покрытие FR/NFR, достоверность входного набора"
```

---

### Task 5: runner — шаг авторинга, консоль, preflight, тест-инфраструктура

**Files:**
- Modify: `governance/runner.py`, `governance/console_model.py`
- Test: `tests/test_governance_runner.py`,
  `tests/test_governance_console_model.py`

**Interfaces:**
- Consumes: kind `"acceptance"` в ops (Task 2).
- Produces: шаг `("author-acceptance", "acceptance", "25-acceptance.md")`
  МЕЖДУ author-design и author-decomposition.

- [ ] **Step 1: Красные тесты**

```python
def test_author_steps_include_acceptance_between_design_and_decomposition() -> None:
    keys = [k for k, _, _ in runner._AUTHOR_STEPS]
    assert keys.index("author-design") < keys.index("author-acceptance")
    assert keys.index("author-acceptance") < keys.index("author-decomposition")
```

Двусторонний консольный тест закраснеет сам при правке `_AUTHOR_STEPS`
без `PIPELINE_KEYS`. Preflight (канон Task 5 плана decomposition):

```python
def test_start_stops_preflight_when_target_profile_lacks_acceptance(
    tmp_path: Path, runs_root, monkeypatch,
) -> None:
    """target с 5-узловым профилем (design+decomposition есть, acceptance
    нет) ⇒ stopped_preflight ДО единого вызова авторинга."""
    ops = FakeOps(facts=GREEN_PR_FACTS)
    kwargs = _start_kwargs(tmp_path, "r-preflight-no-acc", ops)
    _write_five_node_profile(Path(kwargs["target_dir"]))  # хелпер: копия
    # _write_four_node_profile + узел decomposition (upstream [design]),
    # БЕЗ acceptance

    state = runner.start(**kwargs)

    assert state.status == "stopped_preflight"
    assert ops.authored == []
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Правки**

`runner.py`: `_AUTHOR_STEPS` — вставить
`("author-acceptance", "acceptance", "25-acceptance.md")` между design и
decomposition; preflight-кортеж `("design", "decomposition")` (строка
~741) → `("design", "acceptance", "decomposition")`.
`console_model.py`: `PIPELINE_KEYS` — вставить `"author-acceptance"`
между `"author-design"` и `"author-decomposition"`.

Тест-инфраструктура В ЭТОЙ ЖЕ задаче (урок круга 2 плана decomposition —
иначе весь runner-набор падает KeyError):

- `FakeOps.author` — kind `"acceptance"` пишет валидный 25-acceptance.md:
  frontmatter с ДВУМЯ верными пинами (blob-хеши фактических
  10-requirements.md и 15-behaviour-spec.md фикстуры — паттерн design) и
  телом: один `#### AC-01: … · verification: manual` c `traces:` на
  реальный Must-FR фикстуры (посмотри `_DEFAULT_REQUIREMENTS_BODY`; если
  Must-требований в фикстуре нет — строка-декларация
  `Must-требований во входном наборе нет` + AC c traces на существующий
  FR) + все 4 обязательные секции — валидно наперёд для гардов Task 6.
- kind `"decomposition"` в FakeOps — ОБНОВИТЬ: frontmatter теперь
  `traces_to: [design, acceptance]` с двумя пинами (design + acceptance
  по фактическим файлам фикстуры).
- `_dt_smoke_decomposition_body`/`_DtSmokeOps` (фикстуры сквозных
  decomposition-смоуков, минор круга 2 плана: свой kind decomposition,
  общий FakeOps их не покрывает) — тот же двухпиновый frontmatter, плюс
  kind `"acceptance"` в `_DtSmokeOps` с валидным 25-acceptance.md.
- `_repin_bundle` — расширить: перепиновать design, ЗАТЕМ acceptance
  (requirements+behaviour), ЗАТЕМ decomposition (design+acceptance) —
  топологический порядок.
- `_write_five_node_profile` — новый хелпер;
  `test_start_preflight_silent_on_five_node_profile` → переименовать/
  обновить на актуальный 6-узловой профиль.
- Точные списки `ops.authored == [...]` — добавить `"acceptance"` на
  своей позиции.

- [ ] **Step 4: Прогнать — PASS (полный набор; S4-гарды acceptance ещё не
  заведены — Task 6, фикстуры готовы наперёд).**

- [ ] **Step 5: Commit**

```bash
git add governance/runner.py governance/console_model.py \
  tests/test_governance_runner.py tests/test_governance_console_model.py
git commit -m "feat(runner): шаг author-acceptance + preflight трёх узлов + консоль"
```

---

### Task 6: runner — S4-гарды acceptance

**Files:**
- Modify: `governance/runner.py`
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: `acceptance_guard.coverage_findings` (Task 4).
- Produces: находки `GC-COMPLETENESS(acceptance)`, `GC-UNPINNED/GC-STALE`
  для трёх новых рёбер, `GC-DSL-EMPTY` для 25-acceptance.md,
  `GC-AC-COVERAGE: <finding>` — все стопят S4.

- [ ] **Step 1: Красные тесты** (зеркала decomposition-гардов, канон —
  `_decomposition_*`-тесты рядом):

```python
def test_gate_stops_when_acceptance_missing_from_bundle(...)
    # ⇒ stopped_gate, "GC-COMPLETENESS(acceptance)"
def test_gate_acceptance_unpinned_requirements_edge_stops(...)
def test_gate_acceptance_stale_behaviour_pin_stops(...)
def test_gate_acceptance_undeclared_edge_stops(...)
def test_gate_decomposition_acceptance_edge_unpinned_stops(...)
    # ребро 30-decomposition.md → acceptance: пин снят ⇒ GC-UNPINNED
def test_gate_acceptance_dsl_empty_stops(...)
    # файл без `#### AC-` И без строки-декларации ⇒ GC-DSL-EMPTY
def test_gate_acceptance_dsl_declaration_line_passes_dsl_empty(...)
    # файл ТОЛЬКО со строкой-декларацией (+AC не нужен) НЕ стопится
    # DSL-EMPTY (минор круга 3 ревью спеки)
def test_gate_ac_coverage_finding_stops(...)
    # валидный DSL, непокрытый Must-FR ⇒ stopped_gate c "GC-AC-COVERAGE"
```

(Полные тела — копии соседних тестов с заменой файла/узла; ассерты на
КОНКРЕТНЫЙ текст находки. Тест согласованности
`test_gate_edges_derived_from_bundle_dag` закраснеет — рёбра в
_GATE_EDGES появятся здесь, в _BUNDLE_DAG — Task 7: пометить
`@pytest.mark.xfail(reason="рёбра DAG приходят Task 7", strict=True)`,
Task 7 снимает.)

- [ ] **Step 2: Прогнать — FAIL новых.**

- [ ] **Step 3: Правки**

`_GATE_EDGES` — три новых ребра ВСТАВКОЙ в порядке обхода
`_BUNDLE_DAG` (тест согласованности сравнивает кортежи НА РАВЕНСТВО —
append в конец оставил бы его красным, minor круга 1): два ребра
acceptance — ПОСЛЕ рёбер design и ПЕРЕД `("30-decomposition.md",
"design", …)`; ребро decomposition→acceptance — сразу ЗА
decomposition→design:

```python
    ("25-acceptance.md", "requirements", "10-requirements.md", True),
    ("25-acceptance.md", "behaviour-spec", "15-behaviour-spec.md", True),
    # (существующее ребро decomposition→design остаётся между ними)
    ("30-decomposition.md", "acceptance", "25-acceptance.md", True),
```

GC-COMPLETENESS-цикл node_paths += `("acceptance", "25-acceptance.md")`
(между design и decomposition). DSL-EMPTY-кортеж += запись:

```python
        (
            "25-acceptance.md",
            r"^####\s+AC-\d+[a-z]?:|^Must-требований во входном наборе нет",
            "критериев приёмки",
            "#### AC-NN: <название> · verification: test|manual|metric",
        ),
```

Гард GC-AC-COVERAGE — после GC-DT-GRAPH, тем же паттерном (existence
обоих входов + acceptance явные):

```python
    acc_path = node_paths["acceptance"]
    if req_path.exists() and beh_path.exists() and acc_path.exists():
        ac_cov = [
            f"error GC-AC-COVERAGE: {finding}"
            for finding in acceptance_guard.coverage_findings(
                req_path.read_text(encoding="utf-8"),
                beh_path.read_text(encoding="utf-8"),
                acc_path.read_text(encoding="utf-8"),
            )
        ]
        if ac_cov:
            (run_dir(state.run_id) / "gate-findings.txt").write_text(
                "\n".join(ac_cov) + "\n", encoding="utf-8"
            )
            state.status = "stopped_gate"
            save(state)
            return False
```

Импорт `acceptance_guard` рядом с соседями. Комментарий над `_GATE_EDGES`
(«required-рёбер три») обновить: required — все рёбра design/acceptance/
decomposition (шесть).

- [ ] **Step 4: Прогнать — PASS (кроме strict-xfail).**

- [ ] **Step 5: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): S4-гарды acceptance — три ребра, полнота, DSL, Must-покрытие"
```

---

### Task 7: task_bridge — DAG, второй пин decomposition, --legacy-bundle=3|4|5

**Files:**
- Modify: `governance/task_bridge.py`, `Makefile`, `README.md`
- Test: `tests/test_governance_task_bridge.py`,
  `tests/test_governance_runner.py` (снятие xfail + производная required)

**Interfaces:**
- Consumes: текущие `_BUNDLE_DAG`/`_dag_for(3|4)`/DT-ветвления
  `legacy_bundle is None` (строки 804/838), deliver-preflight
  `("design", "decomposition")` (строка 789).
- Produces: 6-узловой `_BUNDLE_DAG`; `_BUNDLE_DAG_LEGACY5` (отдельный
  кортеж); `_dag_for` с 3|4|5; DT-ветвления по составу DAG. Task 8
  потребляет полный DAG.

- [ ] **Step 1: Красные тесты**

```python
def test_bundle_dag_has_acceptance_and_two_pin_decomposition() -> None:
    from governance import task_bridge as tb
    assert ("25-acceptance.md", ("requirements", "behaviour-spec")) in tb._BUNDLE_DAG
    assert tb._BUNDLE_DAG[-1] == ("30-decomposition.md", ("design", "acceptance"))
    assert tb._ANCHOR_NODE_ID == "decomposition"


def test_dag_for_5_is_the_old_five_node_variant_not_a_slice() -> None:
    from governance.task_bridge import _dag_for
    dag5 = _dag_for(5)
    assert [f for f, _ in dag5] == [
        "00-charter.md", "10-requirements.md", "15-behaviour-spec.md",
        "20-design.md", "30-decomposition.md",
    ]
    # решённая ДО acceptance-эры декомпозиция пинует только design
    assert dag5[-1] == ("30-decomposition.md", ("design",))


def test_legacy_5_exact_composition() -> None:
    # каталог 00/10/15/20/30: legacy=5 штампуется; legacy=4 ⇒
    # RuntimeError (состав); None ⇒ RuntimeError переходного режима
    # (в тексте: 25-acceptance.md и --legacy-bundle=5); 6-узловой
    # каталог с legacy=5 ⇒ RuntimeError
    ...


def test_legacy_5_goes_dt_path_with_graph_validation(...)
    # major круга 1 ревью спеки: 5-узловой бандл с невалидным графом DT
    # ⇒ RuntimeError из graph_findings (НЕ молчаливый легаси-рендер);
    # валидный ⇒ tasks-спека с DT-провенансом и Mode: verify_first у
    # verify-DT — render_tasks_dt, не render_tasks
    ...
```

(Полные тела composition/DT-тестов — по образцу существующих legacy-
тестов; 3|4-тесты сами не меняются, НО миграция существующего набора
ОБЯЗАТЕЛЬНА и входит в этот шаг — major круга 1 ревью плана:

- константа `ACCEPTANCE_MD` (валидный 25-acceptance.md: два верных пина,
  AC-01 c traces на Must-требование фикстуры либо строка-декларация) и
  её укладка в `_target(tmp_path)` — иначе ВСЕ full-DAG тесты deliver/
  stamp/conform падают на `_check_bundle_composition`;
- ассерты committed-путей deliver-тестов += `25-acceptance.md`;
- `test_bundle_dag_terminates_at_decomposition` — ассерт формы
  терминального узла на `("design", "acceptance")` (замена, тест из
  Step 1 этого Task его дублирует — объединить);
- `test_dag_for_invalid_value_raises` и
  `test_legacy_flag_rejects_out_of_range_value` — значение вне НОВОГО
  диапазона (6 вместо 5), покрытие отказа сохраняется; в первом также
  `match="3 или 4"` → под новый текст ValueError (словарь 3|4|5);
- два deliver-теста с ИНЛАЙНОВЫМ бандлом внутри `_LateOps.checkout_and_pull`
  (`test_deliver_reads_bundle_only_after_base_checkout` и
  `test_deliver_reads_design_only_after_base_checkout`) — дописать
  25-acceptance.md в их инлайн-состав (минор круга 2 плана: _target их
  не покрывает);
- покрытие полного DAG остаётся на полном пути — существующим тестам
  НЕ дописывать `legacy_bundle=5` (это молча увело бы живую проверку
  двухпинового decomposition/штампа acceptance на легаси-путь).)

- [ ] **Step 2: Прогнать — FAIL; снять strict-xfail Task 6 и обновить
  производную required в test_gate_edges_derived_from_bundle_dag:**
  `_node_id(fname) in {"design", "decomposition", "acceptance"}`.

- [ ] **Step 3: Правки**

`_BUNDLE_DAG`: вставить `("25-acceptance.md", ("requirements",
"behaviour-spec"))` перед decomposition; узел decomposition →
`("30-decomposition.md", ("design", "acceptance"))` (штамп-механика двух
пинов уже есть — design). Отдельный вариант (НЕ срез — major круга 1
ревью спеки; состав `00/10/15/20/30`, decomposition старой эры пинует
только design):

```python
_BUNDLE_DAG_LEGACY5: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("00-charter.md", ()),
    ("10-requirements.md", ("charter",)),
    ("15-behaviour-spec.md", ("requirements",)),
    ("20-design.md", ("requirements", "behaviour-spec")),
    ("30-decomposition.md", ("design",)),
)
```

`_dag_for`: `5 → _BUNDLE_DAG_LEGACY5`; `3|4 → _BUNDLE_DAG[:n]` (префикс
нового DAG до design включительно совпадает со старым); ValueError-текст
и докстринг — словарь 3|4|5. CLI: `choices=(3, 4, 5)`.

DT-ветвления deliver (строки 804 и 838): `legacy_bundle is None` →
`any(_node_id(fname) == "decomposition" for fname, _ in dag)` (канон —
ветвление design_text строкой 831). Deliver-preflight (строка 789):
`("design", "decomposition")` → `("design", "acceptance",
"decomposition")` (узлы сверяются с активным dag — механика уже
фильтрует).

Операторские тексты — ШЕСТЬ мест (минор круга 3 ревью спеки): текст
RuntimeError `_check_bundle_composition`, ValueError `_dag_for`, help
`--legacy-bundle` CLI, `make help` Makefile, тело PR `deliver_conform`,
таблица README — везде словарь `3|4|5` с точным составом каждого.

- [ ] **Step 4: Прогнать — PASS; полный набор без xfail.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py \
  tests/test_governance_runner.py Makefile README.md
git commit -m "feat(task_bridge): узел acceptance в DAG; --legacy-bundle=3|4|5; DT-путь по составу DAG"
```

---

### Task 8: task_bridge — справочная секция AC в tasks-спеке

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: `acceptance_guard.parse_ac_criteria` (Task 3); полный DAG
  (Task 7); `_render_resolutions_section` (место вставки — сразу после).
- Produces: `_render_acceptance_section(acceptance_text: str) ->
  list[str]` — рендерится в deliver на DAG'ах, несущих acceptance
  (полный DAG); на legacy-путях секции нет.

- [ ] **Step 1: Красные тесты**

```python
def test_acceptance_section_lists_criteria() -> None:
    from governance.task_bridge import _render_acceptance_section
    acc = (
        "#### AC-01: Прогон первым · verification: test\n"
        "traces: [FR-01]\nscenarios: [BEH-01]\nпроза\n\n"
        "#### AC-02: Бюджет · verification: metric\n"
        "traces: [NFR-01]\nпроза\n"
    )
    lines = _render_acceptance_section(acc)
    joined = "\n".join(lines)
    assert "Критерии приёмки (уровень acceptance)" in joined
    assert "- **AC-01** (test): Прогон первым" in joined
    assert "- **AC-02** (metric): Бюджет" in joined


def test_acceptance_section_empty_input_renders_nothing() -> None:
    from governance.task_bridge import _render_acceptance_section
    assert _render_acceptance_section("") == []


def test_deliver_full_dag_embeds_acceptance_section(...)
    # deliver на 6-узловом бандле: tasks-спека содержит секцию AC;
    # deliver с --legacy-bundle=5 её НЕ содержит
    ...
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

```python
def _render_acceptance_section(acceptance_text: str) -> list[str]:
    """Справочная секция «Критерии приёмки» tasks-спеки из AC-DSL.

    Только id, verification и название — полный текст критериев живёт в
    25-acceptance.md бандла (§4 спеки); исполнителю задач достаточно
    знать, ЧТО будет приниматься. Пустой вход — секции нет.
    """
    crits, _findings = acceptance_guard.parse_ac_criteria(acceptance_text)
    if not crits:
        return []
    lines = ["## Критерии приёмки (уровень acceptance)", ""]
    lines += [
        f"- **{c.ac_id}** ({c.verification}): {c.title}" for c in crits
    ]
    lines.append("")
    return lines
```

В `deliver`: acceptance_text читается из 25-acceptance.md ТОЛЬКО когда
узел acceptance в активном dag (канон design_text; legacy — ""), секция
вставляется после `_render_resolutions_section` в render_tasks_dt
(параметр `acceptance_text: str = ""` по образцу design_text).

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(task_bridge): справочная секция критериев приёмки в tasks-спеке"
```

---

### Task 9: Сквозной смоук 6-узловой цепочки + доки

**Files:**
- Modify: `tests/test_governance_runner.py`, `README.md`

**Interfaces:**
- Consumes: всё выше; образец — `test_design_node_end_to_end_smoke` и
  6-узловой аналог decomposition-смоука.

- [ ] **Step 1: Смоук**: FakeOps-прогон start→completed на 6-узловом
  профиле (charter→requirements→behaviour→design→acceptance→
  decomposition) + deliver → tasks-спека: ассерты на traces_to
  [decomposition], секцию AC, DT-задачи. Негативный полукруг: Must-FR
  без покрытия ⇒ stopped_gate c GC-AC-COVERAGE.

- [ ] **Step 2: Прогнать — PASS с первого раза** (падение = найден шов,
  чинить по существу, не ослаблять ассерты).

- [ ] **Step 3: Доки**: README — `governance/acceptance_guard.py` в списке
  модулей; строка про 6-узловую цепочку.

- [ ] **Step 4: Полный набор + ruff по своим файлам; явный RC.**

- [ ] **Step 5: Commit**

```bash
git add tests/test_governance_runner.py README.md
git commit -m "test: сквозной смоук 6-узлового конвейера; docs: acceptance_guard"
```

---

## Self-Review (выполнен при написании)

1. **Spec coverage:** §2 профиль — T1; §3 грамматика/инварианты — T3/T4;
   §4 таблица: profiles T1, runner T5/T6, ops T2 (включая двухпиновый
   decomposition — minor круга 1 спеки), console T5, guard T3/T4,
   task_bridge T7/T8 (DT-путь по составу DAG — major круга 1; шесть
   операторских текстов — minor круга 3), переходный режим T7; §5 тесты
   распределены 1:1 (включая DSL-EMPTY с декларацией — minor круга 3;
   NFR-покрытие и near-miss/пустой набор — major круга 2); §6 секция AC —
   T8; смоук — T9.
2. **Placeholders:** «...» — только где план называет образец-сосед и
   требует копию с заменой; формулы/регексы/тексты находок дословны.
3. **Type consistency:** `parse_ac_criteria -> tuple[list[AcCriterion],
   list[str]]` (T3/T4/T8); `coverage_findings(req, beh, acc)` (T4/T6);
   `_render_acceptance_section -> list[str]` (T8/T9); `_dag_for(5)` →
   `_BUNDLE_DAG_LEGACY5` (T7); производная required
   `{design, decomposition, acceptance}` (T6→T7).
