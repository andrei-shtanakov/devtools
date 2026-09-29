# Оракул бандла, срез 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Срез 1 §7.1 спеки: charter схемы 2 с кодом и `plan_item`, реестр кодов, словарь приоритетов и сироты, квалифицированный токен, строка `**Scenarios:**` в мосте, команда `criteria-close` (измерение через spec-runner, сверка, файл закрытия агентским PR) и гейт `[x]` в CI devtools.

**Architecture:** Чистые модули в `governance/`: `charter_guard` (грамматика charter + реестр), `criteria_tokens` (владение токеном по AST), `criteria_graph` (граф BEH/AC, приоритеты, сироты, вывод AC), `criteria_check` (сверка ответа spec-runner по §5.3), `closure_gate` (правило `[x]`). Оркестрация — `criteria_close.py` поверх существующего `Ops` (create_pr/review/merge). Всё, что зависит от spec-runner, открывается только при **доступном оракуле**: контракт вендорен (есть `PIN`, манифест сходится — отдельного флага нет) и `spec-runner --version` на машине ≥ `min-spec-runner.env`; иначе честно `not-applicable: spec-runner-version`, пока spec-runner#603 не выпущен.

**Tech Stack:** Python 3.12+, stdlib `ast`/`re`/`tomllib`/`json`, `jsonschema` (уже в зависимостях), pytest; uv.

**Spec:** `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` (rev 9) + приложение `…-spec-runner-annex.md`.

## Global Constraints

- Токен: `(?<![A-Za-z0-9_])<CODE>:<ID>(?![A-Za-z0-9_])`, `CODE` = `^[A-Z]{2,6}$`.
- Charter схемы 2: frontmatter `schema: 2`, `code: <CODE>`, `plan_item: todo://<repo>/<id>` — оба поля обязательны; `code` неизменяем.
- Реестр кодов — сами charter'ы схемы 2 (спека §1.2 rev 10): удаление/перенос charter'а схемы 2 запрещены; коллизия — нарушитель позже влитый по first-parent; смена кода — только нарушителем.
- Словарь приоритетов один: `Must | Should | Could | Won't`; приоритет — максимум по `traces`; пустая/неразрешимая трасса — ошибка.
- Сирота: BEH (кроме Won't) не входит в `scenarios` ни одного не-Won't AC.
- Статусы test-BEH: `traced` · `unconfirmed` (`no-test`, `no-product-execution`, `subprocess-only`, `not-passed`, `nondeterministic`) · `error` (`io`, `runner`); исход §3.3: Must `unconfirmed`/`error` — стоп; Should/Could `unconfirmed` — строка отчёта, `error` — стоп.
- `closure` файла: `traced | blocked | not-applicable`; причины n/a: `schema-1 · language · spec-runner-version`.
- Файл закрытия: `workstreams/<ws>/spec/90-acceptance-closure.md`.
- Флага обхода стопа нет и не добавлять (`--skip-criteria` и аналоги запрещены спекой).
- Соседние репо read-only; `_cowork_output` не читать из кода; uv, не pip; реальный `claude`/`codex` в тестах не вызывать.
- PR, меняющий `.github/`, мержит человек (Task 9 выделен отдельным PR).

## Review Focus

1. Бандл схемы 1 (все сегодняшние) — `criteria-close` и гейт `[x]` обязаны пропускать с видимым `not-applicable: schema-1`, не падать и не молчать → тест в Task 8 и Task 9.
2. Ответ spec-runner валидный по форме, но с лишним/пропущенным BEH или `traced` без исполненных строк тела функции → отказ шага, не зелёный → тесты отрицательной таблицы в Task 6.
3. Токен в теле вложенной функции-хелпера внутри теста или в docstring класса-не-теста → не засчитывается тесту → тест в Task 3.
4. `TODO.md` с `[x]`-пунктом, на который ссылается charter схемы 2 из **другого** воркстрима с тем же `@id` дважды (два charter на один пункт) → гейт проверяет оба файла закрытия → тест в Task 9.
5. Повторный `criteria-close` на том же содержимом после `blocked` → отказ «ключ измерен», без нового вызова spec-runner → тест в Task 8.

## Исполнение (решение владельца 2026-09-29)

- Отдельный worktree: `git -C /Users/Andrei_Shtanakov/labs/all_ai_orchestrators/devtools worktree add ../devtools-oracle-slice1 -b feat/bundle-oracle-slice1 spec/bundle-criteria-oracle` — не основной чекаут `devtools/` (ловушка общего чекаута с параллельными сессиями).
- Tasks 1–6 — нативно, TDD. После Tasks 7–8 — **отдельный свежий ревьюер (opus) на дифф 7–8** до Task 9: `runner.py` — самый нагруженный файл (S13, волны, #445). Затем сквозная проверка ветки в конце.
- Зависимости: 2 после 1 (общий фикстурный бандл); 5 после 1 и 4; 6 после 2 и 3; 7 после 1 и 2 (оба правят `runner.py` и `tests/test_governance_runner.py`); 8 после 4, 5, 6, 7; 9 после 1, 4 и 8 (тест гейта читает настоящий файл из `render_closure`); 10 последним.

---

### Task 1: `charter_guard` — charter схемы 2 как реестр кодов

> **Редакция после ревью задач 7–8 (rev 10 спеки, решение владельца 2026-09-29).**
> Исходная редакция задачи вводила файл `workstreams/codes.toml`; он не
> доезжал до default-ветки (candidate переносит только узлы) — снят. Код
> модуля — `governance/charter_guard.py` (коммит `1a8db90`), тесты —
> `tests/test_governance_charter_guard.py`. Ниже — действующий контракт.

**Files:**
- Create: `governance/charter_guard.py`
- Test: `tests/test_governance_charter_guard.py`

**Interfaces (Produces):**
- `CODE_RE`, `PLAN_ITEM_RE`, `CHARTER_GLOB = "workstreams/*/spec/00-charter.md"`
- `@dataclass(frozen=True) class Charter: schema: int; code: str | None; plan_item: str | None; malformed: bool = False`
- `read_charter(text) -> Charter` — нет frontmatter → схема 1; frontmatter не разбирается → `malformed` (находка, не схема 1)
- `charter_findings(charter, *, ws_id, todo_ids, repo) -> list[str]` — грамматика §1.1, `plan_item` в TODO.md своего репо
- `collision_findings(charters: dict[ws, Charter], *, order: dict[ws, int]) -> list[str]` — нарушитель = позже влитый (не влитый — всегда)
- `deletion_findings(base: dict[path, Charter], head: dict[path, Charter]) -> list[str]` — удаление/перенос схемы 2 запрещены
- `code_change_findings(base, head, *, violator_in_base: bool) -> list[str]` — смена кода только нарушителем
- `stamp_charter(text, *, code, plan_item) -> str` — идемпотентно; битый frontmatter → `ValueError`
- `charters_at(repo, ref) -> dict[path, Charter]` (git-объекты), `merge_order(repo, ref, paths) -> dict[path, int]` (`rev-list --first-parent`)
- `repo_findings(repo, base_ref) -> list[str]`, CLI `python -m governance.charter_guard --repo . --base <ref>` → exit 1 при находках

**Тесты (все в `tests/test_governance_charter_guard.py`):** схема 1 без находок; без frontmatter — схема 1, битый — находка; схема 2 требует code и plan_item; форма CODE; plan_item в своём TODO.md; коллизия — нарушитель позже влитый; коллизия с не влитым — нарушитель он; удаление и перенос схемы 2 — отказ, схема 1 — нет; код неизменяем, кроме нарушителя; штамп идемпотентен и отказывает на битом; `repo_findings` на настоящем git: порядок по first-parent и отказ на удалённом charter.

---

### Task 2: словарь приоритетов и граф критериев с сиротами

**Files:**
- Modify: `governance/acceptance_guard.py:98-150` (`_parse_requirements`: словарь)
- Create: `governance/criteria_graph.py`
- Modify: `governance/runner.py` около строки 2982 (вызов сирот рядом с `acceptance_guard.coverage_findings`)
- Test: `tests/test_governance_criteria_graph.py`, дополнить `tests/test_governance_acceptance_guard.py`

**Interfaces:**
- Consumes: `acceptance_guard.parse_ac_criteria(text) -> (list[AcCriterion], list[str])`, `acceptance_guard._parse_requirements(text) -> (dict[str,str], list[str])`.
- Produces (`criteria_graph`):
  - `PRIORITIES = ("Must", "Should", "Could", "Won't")`, `EXEC_KINDS = frozenset({"unit","integration","contract","e2e","atp"})`
  - `@dataclass(frozen=True) class Beh: id: str; kind: str; waived: bool; traces: tuple[str, ...]; priority: str | None`
  - `@dataclass(frozen=True) class Ac: id: str; verification: str; traces: tuple[str, ...]; scenarios: tuple[str, ...]; priority: str | None`
  - `@dataclass class Graph: behs: dict[str, Beh]; acs: dict[str, Ac]; errors: list[str]`
  - `build_graph(req_text: str, beh_text: str, acc_text: str) -> Graph`
  - `orphan_findings(graph: Graph) -> list[str]`
  - `test_behs(graph: Graph) -> list[Beh]` (исполняемый `kind`, не `waived`)
  - `derive_ac(ac: Ac, graph: Graph, beh_status: dict[str, str]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
"""criteria_graph: словарь приоритетов, приоритет по трассам, сироты, вывод AC."""
from __future__ import annotations

from governance import acceptance_guard as ag
from governance import criteria_graph as cgr

REQ = """#### FR-01: A
**Priority**: Must

#### FR-02: B
**Priority**: Could

#### FR-03: C
**Priority**: Won't
"""
BEH = """#### BEH-01: one
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-02: two
`traces: [FR-02, FR-01]`
- **checked_by**: `status: planned` `kind: manual` `owner: qa` `target: doc`

#### BEH-03: three
`traces: [FR-03]`
- **checked_by**: `status: waived` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-04: four
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-05: five
`traces: []`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`

#### BEH-06: six
`traces: [FR-01]`
- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: tests/t.py`
"""
ACC = """#### AC-01: a · verification: test
traces: [FR-01]
scenarios: [BEH-01, BEH-02]

#### AC-02: b · verification: test
traces: [FR-03]
scenarios: [BEH-04]
"""


def test_could_and_wont_are_in_the_vocabulary():
    prio, findings = ag._parse_requirements(REQ)
    assert prio == {"FR-01": "Must", "FR-02": "Could", "FR-03": "Won't"}
    assert findings == []


def test_priority_is_max_of_traces_and_empty_trace_is_error():
    g = cgr.build_graph(REQ, BEH, ACC)
    assert g.behs["BEH-02"].priority == "Must"
    assert g.behs["BEH-03"].priority == "Won't"
    assert g.behs["BEH-05"].priority is None
    assert any("BEH-05" in e for e in g.errors)


def test_waived_wins_over_executable_kind():
    g = cgr.build_graph(REQ, BEH, ACC)
    assert g.behs["BEH-03"].waived
    assert [b.id for b in cgr.test_behs(g)] == ["BEH-01", "BEH-04", "BEH-05", "BEH-06"]


def test_orphans_both_forms():
    g = cgr.build_graph(REQ, BEH, ACC)
    orphans = cgr.orphan_findings(g)
    # форма 1: Must-BEH-04 только в Won't-AC (AC-02 трассирует FR-03) → сирота
    assert g.behs["BEH-04"].priority == "Must"
    assert any("BEH-04" in f for f in orphans)
    # форма 2: BEH-06 вне всех AC → сирота
    assert any("BEH-06" in f for f in orphans)
    # BEH-03 сам Won't → не сирота
    assert not any("BEH-03" in f for f in orphans)
    assert not any("BEH-01" in f for f in orphans)


def test_derive_ac_table():
    g = cgr.build_graph(REQ, BEH, ACC)
    ac = g.acs["AC-01"]
    assert cgr.derive_ac(ac, g, {"BEH-01": "traced"}) == "human"  # BEH-02 manual
    g2 = cgr.build_graph(REQ, BEH.replace("`kind: manual`", "`kind: unit`"), ACC)
    ac2 = g2.acs["AC-01"]
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "traced", "BEH-02": "traced"}) == "traced"
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "traced", "BEH-02": "unconfirmed"}) == "unconfirmed"
    assert cgr.derive_ac(ac2, g2, {"BEH-01": "error", "BEH-02": "unconfirmed"}) == "error"
    manual = cgr.Ac("AC-09", "manual", ("FR-01",), (), "Must")
    assert cgr.derive_ac(manual, g2, {}) == "human"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_criteria_graph.py -q`
Expected: FAIL — `Could` даёт находку «вне словаря Must|Should», модуль `criteria_graph` отсутствует.

- [ ] **Step 3: Implement**

В `governance/acceptance_guard.py` заменить проверку словаря:

```python
        if pr.group(1) not in ("Must", "Should", "Could", "Won't"):
            findings.append(
                f"{rid}: значение **Priority**: {pr.group(1)} вне "
                "словаря Must|Should|Could|Won't — входное множество недостоверно"
            )
            continue
```

Создать `governance/criteria_graph.py`:

```python
"""criteria_graph — типизированный граф BEH/AC бандла (спека §1.5–1.7, §5.2).

Приоритет выводится по трассам (максимум), пустая/неразрешимая трасса —
ошибка. Сирота — BEH (кроме Won't) вне scenarios всех не-Won't AC.
Статусы AC выводятся только здесь (§5.2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from governance import acceptance_guard as ag

PRIORITIES = ("Must", "Should", "Could", "Won't")
EXEC_KINDS = frozenset({"unit", "integration", "contract", "e2e", "atp"})
_BEH_HEAD = re.compile(r"^####\s+(BEH-\d+[a-z]?):", re.M)
_TRACES = re.compile(r"`traces:\s*\[([^\]]*)\]`")
_CHECKED = re.compile(r"\*\*checked_by\*\*:(.*)$", re.M)
_FIELD = re.compile(r"`(\w+):\s*([^`]*)`")


@dataclass(frozen=True)
class Beh:
    id: str
    kind: str
    waived: bool
    traces: tuple[str, ...]
    priority: str | None


@dataclass(frozen=True)
class Ac:
    id: str
    verification: str
    traces: tuple[str, ...]
    scenarios: tuple[str, ...]
    priority: str | None


@dataclass
class Graph:
    behs: dict[str, Beh]
    acs: dict[str, Ac]
    errors: list[str] = field(default_factory=list)


def _max_priority(refs: tuple[str, ...], prio: dict[str, str]) -> str | None:
    found = [prio[r] for r in refs if r in prio]
    if not refs or len(found) != len(refs):
        return None
    return min(found, key=PRIORITIES.index)


def _parse_behs(beh_text: str, prio: dict[str, str], errors: list[str]) -> dict[str, Beh]:
    heads = list(_BEH_HEAD.finditer(beh_text))
    out: dict[str, Beh] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(beh_text)
        block = beh_text[m.end():end]
        tr = _TRACES.search(block)
        traces = tuple(x.strip() for x in tr.group(1).split(",") if x.strip()) if tr else ()
        cb = _CHECKED.search(block)
        fields = dict(_FIELD.findall(cb.group(1))) if cb else {}
        kind = fields.get("kind", "").strip()
        priority = _max_priority(traces, prio)
        if priority is None:
            errors.append(f"{m.group(1)}: пустая или неразрешимая трасса {list(traces)}")
        if kind not in EXEC_KINDS | {"manual"}:
            errors.append(f"{m.group(1)}: неизвестный kind {kind!r}")
        out[m.group(1)] = Beh(
            id=m.group(1), kind=kind, waived=fields.get("status", "").strip() == "waived",
            traces=traces, priority=priority,
        )
    return out


def build_graph(req_text: str, beh_text: str, acc_text: str) -> Graph:
    """Граф из байтов трёх узлов (читаются по пину вызывающим)."""
    prio, errors = ag._parse_requirements(req_text)
    behs = _parse_behs(beh_text, prio, errors)
    crits, ac_findings = ag.parse_ac_criteria(acc_text)
    errors += ac_findings
    acs: dict[str, Ac] = {}
    for c in crits:
        priority = _max_priority(tuple(c.traces), prio)
        if priority is None:
            errors.append(f"{c.ac_id}: пустая или неразрешимая трасса {list(c.traces)}")
        acs[c.ac_id] = Ac(c.ac_id, c.verification, tuple(c.traces), tuple(c.scenarios), priority)
    return Graph(behs, acs, errors)


def orphan_findings(graph: Graph) -> list[str]:
    """§1.7: каждый не-Won't BEH входит в scenarios хотя бы одного не-Won't AC."""
    live = {s for a in graph.acs.values() if a.priority not in (None, "Won't") for s in a.scenarios}
    return [
        f"{b.id}: сирота — не входит в scenarios ни одного не-Won't AC"
        for b in graph.behs.values()
        if b.priority != "Won't" and b.id not in live
    ]


def test_behs(graph: Graph) -> list[Beh]:
    """Test-критерии: исполняемый kind и не waived (waived приоритетнее)."""
    return [b for b in graph.behs.values() if b.kind in EXEC_KINDS and not b.waived]


def derive_ac(ac: Ac, graph: Graph, beh_status: dict[str, str]) -> str:
    """Таблица §5.2."""
    if ac.verification != "test":
        return "human"
    statuses = []
    for s in ac.scenarios:
        beh = graph.behs.get(s)
        if beh is None:
            return "error"
        if beh.waived or beh.kind == "manual":
            statuses.append("human")
        else:
            statuses.append(beh_status.get(s, "error"))
    if "error" in statuses:
        return "error"
    if "unconfirmed" in statuses:
        return "unconfirmed"
    if "human" in statuses:
        return "human"
    return "traced"
```

Ruling-заметка: `test_behs` имя начинается с `test_` — pytest может принять его за тест при импорте в тестовый модуль. В тестах импортировать модуль целиком (`from governance import criteria_graph as cgr`), как выше, а не имя функции.

В `governance/runner.py` (блок «Гард Must-покрытия acceptance», около строк 2978–2995) тексты сейчас читаются инлайн внутри вызова, а находки копятся в `ac_cov`. Прочитать тексты один раз и добавить сирот в тот же список, до `if ac_cov:`:

```python
    acc_path = node_paths["acceptance"]
    if req_path.exists() and beh_path.exists() and acc_path.exists():
        req_text = req_path.read_text(encoding="utf-8")
        beh_text = beh_path.read_text(encoding="utf-8")
        acc_text = acc_path.read_text(encoding="utf-8")
        ac_cov = [
            f"error GC-AC-COVERAGE: {finding}"
            for finding in acceptance_guard.coverage_findings(req_text, beh_text, acc_text)
        ]
        ac_cov += [
            f"error GC-ORPHAN: {finding}"
            for finding in criteria_graph.orphan_findings(
                criteria_graph.build_graph(req_text, beh_text, acc_text)
            )
        ]
        if ac_cov:
            ...  # без изменений: gate-findings.txt, stopped_gate
```

Импорт `from governance import criteria_graph` в шапке runner.py. Гейт выполняется при каждом прохождении волны W4 и далее — в том числе после `--reopen` 15: reopen ведёт нижестоящие уровни через переодобрение, и этот блок исполняется заново над новым 15 и прежним 25.

Тест раннера (в `tests/test_governance_runner.py`, по образцу ближайшего теста с `GC-AC-COVERAGE` — grep `GC-AC-COVERAGE`): тот же стенд, но 15-behaviour-spec добавляет `BEH-99` с трассой на Must-FR и без AC → `state.status == "stopped_gate"` и в `gate-findings.txt` строка `error GC-ORPHAN: BEH-99`. Второй тест — сценарий reopen: 25 не меняется, 15 переписан с новым BEH → тот же стоп.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_criteria_graph.py tests/test_governance_acceptance_guard.py tests/test_governance_runner.py -q -k "graph or acceptance or ORPHAN or orphan"` затем без `-k`
Expected: PASS. Если тест `acceptance_guard` ожидал находку на `Could` — обновить его ожидание на новый словарь (это намеренное изменение спеки §1.5).

- [ ] **Step 5: Commit**

```bash
git add governance/acceptance_guard.py governance/criteria_graph.py governance/runner.py tests/test_governance_criteria_graph.py tests/test_governance_acceptance_guard.py
git commit -m "feat(governance): criteria_graph — словарь Must|Should|Could|Won't, приоритет по трассам, сироты"
```

---

### Task 3: `criteria_tokens` — квалифицированный токен и владение по AST

**Files:**
- Create: `governance/criteria_tokens.py`
- Test: `tests/test_governance_criteria_tokens.py`

**Interfaces:**
- Produces:
  - `token_re(code: str, cid: str) -> re.Pattern`
  - `ANY_TOKEN: re.Pattern` (группы: код, ID)
  - `definition_tokens(source: str) -> dict[str, frozenset[str]]` — qualname тест-функции/метода → множество токенов `CODE:ID`, которыми она владеет.

- [ ] **Step 1: Write the failing tests (таблица форм)**

```python
"""criteria_tokens: граница токена и владение по AST (спека §1.3–1.4)."""
from __future__ import annotations

from governance import criteria_tokens as ct


def test_boundary():
    r = ct.token_re("ENC", "BEH-03")
    assert r.search("# ENC:BEH-03 x")
    for bad in ("XENC:BEH-03", "ENC:BEH-030", "ENC:BEH-03a", "ENC:BEH-03_"):
        assert not r.search(bad)


SRC = '''
"""ENC:BEH-09 module header does not count"""

def helper():
    # ENC:BEH-08 helper owns this, not a test
    return 1

def test_plain():
    # ENC:BEH-01
    assert helper() == 1

def test_outer():
    def inner():
        # ENC:BEH-07 nested helper owns this
        return 2
    assert inner() == 2

class TestGroup:
    """ENC:BEH-02 class-level token goes to every test method"""

    def test_red_neighbour(self):
        assert False

    def test_green(self):
        # ENC:BEH-03 only this method
        assert True

@decorator  # ENC:BEH-04 decorator line counts
def test_decorated():
    pass

@pytest.mark.parametrize("x", [1, 2])
def test_param(x):
    # ENC:BEH-05 one definition, every collected param selector
    assert x
'''


def test_ownership_table():
    got = ct.definition_tokens(SRC)
    assert got["test_plain"] == {"ENC:BEH-01"}
    assert got["test_outer"] == frozenset()
    assert got["TestGroup.test_red_neighbour"] == {"ENC:BEH-02"}
    assert got["TestGroup.test_green"] == {"ENC:BEH-02", "ENC:BEH-03"}
    assert got["test_decorated"] == {"ENC:BEH-04"}
    assert got["test_param"] == {"ENC:BEH-05"}
    assert "helper" not in got
    assert all("ENC:BEH-09" not in v for v in got.values())
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_criteria_tokens.py -q`
Expected: FAIL — модуль отсутствует.

- [ ] **Step 3: Implement**

```python
"""criteria_tokens — квалифицированный токен CODE:ID и его владелец.

Спека §1.3–1.4: токен принадлежит наиболее вложенному определению; область
определения — строки от первого декоратора до end_lineno минус вложенные
def/class (комментарии в AST не видны — считаем по строкам). Токен области
класса достаётся всем его тест-методам; токен метода в класс не поднимается.
"""
from __future__ import annotations

import ast
import re

ANY_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z]{2,6}):((?:BEH|AC)-\d+[a-z]?)(?![A-Za-z0-9_])"
)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def token_re(code: str, cid: str) -> re.Pattern[str]:
    """Регэксп одного квалифицированного токена с границами."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(code)}:{re.escape(cid)}(?![A-Za-z0-9_])")


def _span(node: ast.AST) -> tuple[int, int]:
    decos = getattr(node, "decorator_list", [])
    start = decos[0].lineno if decos else node.lineno
    return start, node.end_lineno or node.lineno


def _walk(body: list[ast.stmt], prefix: str, out: list[tuple[str, ast.AST]]) -> None:
    for node in body:
        if isinstance(node, _DEFS):
            qn = f"{prefix}{node.name}"
            out.append((qn, node))
            _walk(node.body, f"{qn}.", out)


def definition_tokens(source: str) -> dict[str, frozenset[str]]:
    """qualname тест-функции/метода → токены, которыми она владеет."""
    tree = ast.parse(source)
    defs: list[tuple[str, ast.AST]] = []
    _walk(tree.body, "", defs)
    lines = source.splitlines()
    owner: dict[int, str] = {}
    # наиболее вложенный владелец: более поздние (вложенные) перезаписывают строки
    for qn, node in defs:
        start, end = _span(node)
        for ln in range(start, end + 1):
            owner[ln] = qn
    owned: dict[str, set[str]] = {qn: set() for qn, _ in defs}
    for ln, text in enumerate(lines, start=1):
        qn = owner.get(ln)
        if qn is None:
            continue
        for m in ANY_TOKEN.finditer(text):
            owned[qn].add(f"{m.group(1)}:{m.group(2)}")
    kinds = {qn: node for qn, node in defs}
    result: dict[str, frozenset[str]] = {}
    for qn, node in defs:
        if isinstance(node, ast.ClassDef) or not node.name.startswith("test"):
            continue
        parent = qn.rpartition(".")[0]
        tokens = set(owned[qn])
        if parent and isinstance(kinds.get(parent), ast.ClassDef):
            tokens |= owned[parent]
        result[qn] = frozenset(tokens)
    return result
```

Проверка корректности «наиболее вложенного»: `_walk` добавляет родителя раньше детей, поэтому при заполнении `owner` строки вложенного определения перезаписывают строки родителя — владельцем остаётся самое вложенное. Строки заголовка класса (`class TestGroup:` и docstring) принадлежат классу; строки методов — методам.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_criteria_tokens.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_tokens.py tests/test_governance_criteria_tokens.py
git commit -m "feat(governance): criteria_tokens — квалифицированный токен и владение по AST"
```

---

### Task 4: вендоренный `criteria-closure/v1` (минимальная версия и целостность)

**Files:**
- Create: `contracts/criteria-closure/v1/min-spec-runner.env`
- Create: `contracts/criteria-closure/v1/README.md`
- Create: `governance/criteria_contract.py`
- Test: `tests/test_governance_criteria_contract.py`

**Interfaces:**
- Produces:
  - `CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contracts/criteria-closure/v1"`
  - `@dataclass(frozen=True) class MinVersion: version: str`
  - `read_min_version(path: Path = CONTRACT_DIR / "min-spec-runner.env") -> MinVersion`
  - `vendored(contract_dir: Path = CONTRACT_DIR) -> bool` — «выпущено» выводится: есть `PIN` и `integrity_findings` пуст (отдельного флага нет — рассогласоваться нечему)
  - `oracle_available(installed: str | None, minimum: MinVersion, *, is_vendored: bool) -> bool`
  - `integrity_findings(contract_dir: Path = CONTRACT_DIR) -> list[str]` — нет `PIN` → пусто (контракт ещё не вендорен — ожидаемое состояние); `PIN` есть → требуется `manifest.json` и совпадение sha256 каждого его файла.
  - `drift_findings(contract_dir: Path, upstream: Path | None, *, ci: bool) -> tuple[list[str], list[str]]` → (ошибки, заметки): не вендорен → пусто; `PIN` = `SOURCE: spec-runner @ <sha>`; каждый файл манифеста сверяется с `git -C <upstream> show <sha>:contracts/criteria-closure/v1/<name>`; `upstream` нет/не git → при `ci` ошибка, локально заметка `not-checked`.

- [ ] **Step 1: Write the failing tests**

```python
"""criteria_contract: min-spec-runner.env и целостность вендоренной копии."""
from __future__ import annotations

import hashlib
import json

from governance import criteria_contract as cc


def write_min(d, v="4.3.0"):
    (d / "min-spec-runner.env").write_text(f"MIN_SPEC_RUNNER_VERSION={v}\n")


def test_not_vendored_means_unavailable(tmp_path):
    write_min(tmp_path)
    mv = cc.read_min_version(tmp_path / "min-spec-runner.env")
    assert mv == cc.MinVersion("4.3.0")
    assert cc.integrity_findings(tmp_path) == []
    assert not cc.vendored(tmp_path)
    assert not cc.oracle_available("9.9.9", mv, is_vendored=False)


def test_vendored_requires_matching_manifest(tmp_path):
    write_min(tmp_path)
    schema = tmp_path / "response.schema.json"
    schema.write_text("{}")
    (tmp_path / "PIN").write_text("SOURCE: spec-runner @ abc1234\n")
    assert cc.integrity_findings(tmp_path) != [] and not cc.vendored(tmp_path)  # нет manifest
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"response.schema.json": hashlib.sha256(b"{}").hexdigest()}))
    assert cc.integrity_findings(tmp_path) == [] and cc.vendored(tmp_path)
    mv = cc.read_min_version(tmp_path / "min-spec-runner.env")
    assert cc.oracle_available("4.3.0", mv, is_vendored=True)
    assert cc.oracle_available("4.10.1", mv, is_vendored=True)
    assert not cc.oracle_available("4.2.9", mv, is_vendored=True)
    assert not cc.oracle_available(None, mv, is_vendored=True)
    schema.write_text("{ }")
    assert cc.integrity_findings(tmp_path) != [] and not cc.vendored(tmp_path)


def _upstream(tmp_path, content: bytes) -> tuple[Path, str]:
    import subprocess
    up = tmp_path / "up"
    d = up / "contracts/criteria-closure/v1"
    d.mkdir(parents=True)
    (d / "response.schema.json").write_bytes(content)
    subprocess.run(["git", "init", "-q", str(up)], check=True)
    subprocess.run(["git", "-C", str(up), "add", "."], check=True)
    subprocess.run(["git", "-C", str(up), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], check=True)
    sha = subprocess.run(["git", "-C", str(up), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    return up, sha


def _vendor(d, sha, content: bytes):
    write_min(d)
    (d / "response.schema.json").write_bytes(content)
    (d / "PIN").write_text(f"SOURCE: spec-runner @ {sha}\n")
    (d / "manifest.json").write_text(json.dumps(
        {"response.schema.json": hashlib.sha256(content).hexdigest()}))


def test_drift_against_pinned_upstream(tmp_path):
    up, sha = _upstream(tmp_path, b"{}")
    vend = tmp_path / "v"
    vend.mkdir()
    _vendor(vend, sha, b"{}")
    assert cc.drift_findings(vend, up, ci=True) == ([], [])
    _vendor(vend, sha, b'{"x": 1}')
    errors, _ = cc.drift_findings(vend, up, ci=True)
    assert errors


def test_drift_missing_upstream_is_error_in_ci_note_locally(tmp_path):
    vend = tmp_path / "v"
    vend.mkdir()
    _vendor(vend, "a" * 40, b"{}")
    errors, _ = cc.drift_findings(vend, None, ci=True)
    assert errors
    errors, notes = cc.drift_findings(vend, None, ci=False)
    assert errors == [] and any("not-checked" in n for n in notes)


def test_shipped_contract_is_not_vendored_yet():
    assert not cc.vendored()
    assert cc.integrity_findings() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_criteria_contract.py -q`
Expected: FAIL — модуль отсутствует.

- [ ] **Step 3: Implement**

`contracts/criteria-closure/v1/min-spec-runner.env`:

```
# Минимальная версия spec-runner с `verify --criteria` (spec-runner#603).
# «Выпущено» не флаг: оракул доступен, когда здесь лежат PIN и сходящийся
# manifest.json (вендоринг схем — отдельный PR); до того devtools отвечает
# not-applicable: spec-runner-version. Значение ниже уточняется тем же PR.
MIN_SPEC_RUNNER_VERSION=0.0.0
```

`contracts/criteria-closure/v1/README.md`: три абзаца — владелец схемы (spec-runner, заявка #603), что лежит здесь до и после выпуска, две гарантии (целостность — `integrity_findings` в CI; дрейф — отдельная проверка по ref из `PIN`, в CI отсутствие входа — ошибка, локально — `not-checked`).

`governance/criteria_contract.py`:

```python
"""criteria_contract — вендоренная копия criteria-closure/v1 (спека §5.4).

«Выпущено» выводится из данных: есть PIN и манифест сходится. Отдельного
флага нет — рассогласоваться нечему.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contracts/criteria-closure/v1"


@dataclass(frozen=True)
class MinVersion:
    version: str


def read_min_version(path: Path = CONTRACT_DIR / "min-spec-runner.env") -> MinVersion:
    for line in path.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "MIN_SPEC_RUNNER_VERSION":
            return MinVersion(value.strip())
    raise ValueError(f"{path}: нет MIN_SPEC_RUNNER_VERSION")


def integrity_findings(contract_dir: Path = CONTRACT_DIR) -> list[str]:
    """Нет PIN — не вендорен (пусто); есть — manifest.json и sha256 сходятся."""
    if not (contract_dir / "PIN").exists():
        return []
    manifest_path = contract_dir / "manifest.json"
    if not manifest_path.exists():
        return ["criteria-closure/v1: есть PIN, но нет manifest.json"]
    out: list[str] = []
    for name, digest in sorted(json.loads(manifest_path.read_text()).items()):
        path = contract_dir / name
        if not path.exists():
            out.append(f"criteria-closure/v1: {name} из manifest отсутствует")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            out.append(f"criteria-closure/v1: {name} не совпал с manifest")
    return out


def vendored(contract_dir: Path = CONTRACT_DIR) -> bool:
    return (contract_dir / "PIN").exists() and not integrity_findings(contract_dir)


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def drift_findings(contract_dir: Path, upstream: Path | None, *, ci: bool) -> tuple[list[str], list[str]]:
    """Дрейф копии от апстрима по ref из PIN (вторая гарантия вендоринга)."""
    import subprocess

    if not (contract_dir / "PIN").exists():
        return [], []
    sha = (contract_dir / "PIN").read_text().split("@")[-1].strip()
    if upstream is None or not (upstream / ".git").exists():
        msg = "criteria-closure/v1: апстрим spec-runner недоступен"
        return ([msg], []) if ci else ([], [f"{msg} — not-checked"])
    errors: list[str] = []
    manifest = json.loads((contract_dir / "manifest.json").read_text())
    for name in sorted(manifest):
        proc = subprocess.run(
            ["git", "-C", str(upstream), "show", f"{sha}:contracts/criteria-closure/v1/{name}"],
            capture_output=True,
        )
        if proc.returncode != 0:
            errors.append(f"criteria-closure/v1: {name} нет в апстриме @ {sha[:7]}")
        elif proc.stdout != (contract_dir / name).read_bytes():
            errors.append(f"criteria-closure/v1: {name} разошёлся с апстримом @ {sha[:7]}")
    return errors, []


def oracle_available(installed: str | None, minimum: MinVersion, *, is_vendored: bool) -> bool:
    """Оракул доступен: контракт вендорен и spec-runner машины не ниже."""
    if not is_vendored or installed is None:
        return False
    return _parts(installed) >= _parts(minimum.version)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_criteria_contract.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add contracts/criteria-closure governance/criteria_contract.py tests/test_governance_criteria_contract.py
git commit -m "feat(contracts): criteria-closure/v1 — минимальная версия spec-runner, вендоринг выводится из PIN"
```

---

### Task 5: мост — строка `**Scenarios:**` под пином версии

**Files:**
- Modify: `governance/task_bridge.py` — функция `render_tasks_dt` (строка ~709; блок рендера задачи у строк ~1010–1030)
- Test: `tests/test_governance_task_bridge.py` (новые тесты в конец)

**Interfaces:**
- Consumes: `charter_guard.read_charter(text) -> Charter`, `criteria_contract.read_min_version() -> MinVersion`, `criteria_contract.oracle_available(installed, minimum, *, is_vendored) -> bool`, `criteria_contract.vendored() -> bool`.
- Produces: keyword-only параметр `render_tasks_dt(..., scenarios_code: str | None = None)`; при `scenarios_code` задача получает строку `**Scenarios:** ENC:BEH-01, ENC:BEH-02` сразу перед `**Traces to:**`. Вызывающий (`deliver`-путь моста) передаёт `scenarios_code=charter.code`, только если charter схемы 2 **и** `oracle_available(spec_runner_version(), read_min_version(), is_vendored=vendored())`; иначе `None` (сегодня всегда `None` — контракт не вендорен).
- Produces: `spec_runner_version() -> str | None` в `task_bridge.py` — `spec-runner --version` (subprocess, таймаут 30 с), первая строка вида `\d+\.\d+\.\d+`; нет бинаря/ошибка — `None`.

- [ ] **Step 1: Write the failing tests**

Найти в `tests/test_governance_task_bridge.py` существующий тест, вызывающий `render_tasks_dt` на минимальном бандле (grep `render_tasks_dt(`), скопировать его вход и добавить:

```python
def test_scenarios_line_rendered_only_with_code(<existing fixtures>):
    text = task_bridge.render_tasks_dt(<same args as the existing test>, scenarios_code="ENC")
    block = text.split("**Traces to:**")[0]
    assert "**Scenarios:** ENC:BEH-01" in block
    plain = task_bridge.render_tasks_dt(<same args>)
    assert "**Scenarios:**" not in plain


def test_spec_runner_version_absent_binary(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert task_bridge.spec_runner_version() is None
```

(`<existing fixtures>`/`<same args>` — буквально те же, что у найденного существующего теста; ни одного нового фикстурного бандла.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_task_bridge.py -q -k "scenarios_line or spec_runner_version"`
Expected: FAIL — неизвестный keyword `scenarios_code`, нет `spec_runner_version`.

- [ ] **Step 3: Implement**

В сигнатуру `render_tasks_dt` добавить `*, scenarios_code: str | None = None` (если keyword-only маркер уже есть — просто параметр). В блоке рендера задачи, где собирается `lines += [f"- [ ] {check}", "", ("**Traces to:** " ...)]`, перед строкой `**Traces to:**` вставить:

```python
            (
                "**Scenarios:** "
                + ", ".join(f"{scenarios_code}:{g.beh_id}" for g in group)
                if scenarios_code
                else None
            ),
```

(отфильтровка `None` уже есть: `"\n".join(line for line in lines if line is not None)`). И функцию:

```python
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def spec_runner_version() -> str | None:
    """Версия установленного spec-runner или None (нет бинаря/ошибка)."""
    try:
        proc = subprocess.run(
            ["spec-runner", "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _VERSION_RE.search(proc.stdout + proc.stderr)
    return m.group(1) if proc.returncode == 0 and m else None
```

В точке вызова `render_tasks_dt` из пути доставки моста (grep `render_tasks_dt(` в `task_bridge.py`) прочитать charter бандла (`<bundle_dir>/00-charter.md`) и передать:

```python
    charter = charter_guard.read_charter(charter_text)
    scenarios_code = (
        charter.code
        if charter.schema == 2
        and criteria_contract.oracle_available(
            spec_runner_version(), criteria_contract.read_min_version(),
            is_vendored=criteria_contract.vendored(),
        )
        else None
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_task_bridge.py -q`
Expected: PASS (включая все прежние тесты моста — сегодня `scenarios_code` всегда `None`, вывод не меняется).

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(task_bridge): строка **Scenarios:** с квалифицированными ID под пином версии spec-runner"
```

---

### Task 6: `criteria_check` — сверка ответа spec-runner и исход

**Files:**
- Create: `governance/criteria_check.py`
- Test: `tests/test_governance_criteria_check.py`

**Interfaces:**
- Consumes: `criteria_graph.Graph/build_graph/test_behs/derive_ac/orphan_findings`, `criteria_tokens.definition_tokens`, `jsonschema` (уже зависимость).
- Produces:
  - `REASONS = {"unconfirmed": {"no-test","no-product-execution","subprocess-only","not-passed","nondeterministic"}, "error": {"io","runner"}}`
  - `expected_definitions(test_files: dict[str, str], code: str, beh_ids: list[str]) -> dict[str, set[tuple[str, str]]]` — BEH → {(файл, qualname)} по байтам devtools
  - `validate_response(request: dict, response: dict, *, expected: dict[str, set[tuple[str, str]]], function_lines: dict[str, set[int]], lock_sha: str, content_sha: str) -> list[str]` — пусто = валиден; иначе причины отказа шага (§5.3)
  - `@dataclass(frozen=True) class Outcome: closure: str; beh_status: dict[str, str]; ac_status: dict[str, str]; stop_reasons: list[str]; report_rows: list[str]`
  - `outcome(graph: Graph, beh_status: dict[str, str]) -> Outcome` — таблица §3.3 + стоп по Must-AC с производным `unconfirmed`/`error` (Must-AC может ссылаться на Should-BEH — без этого он прошёл бы без стопа)
  - `parse_response(code: int, stdout: str, schema: dict | None) -> tuple[dict | None, str | None]` → (ответ, причина отказа): код ∉ {0, 3} → отказ; пустой stdout/не JSON → отказ; при вендоренной схеме — `jsonschema.validate`, нарушение → отказ

- [ ] **Step 1: Write the failing tests (отрицательная таблица §5.3 + исход)**

```python
"""criteria_check: сверка ответа (§5.3) и исход (§3.3)."""
from __future__ import annotations

import copy

import pytest

from governance import criteria_check as ck
from governance import criteria_graph as cgr

REQ = "#### FR-01: A\n**Priority**: Must\n\n#### FR-02: B\n**Priority**: Should\n"
BEH = (
    "#### BEH-01: a\n`traces: [FR-01]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n\n"
    "#### BEH-02: b\n`traces: [FR-02]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n"
)
ACC = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01]\n\n#### AC-02: b · verification: test\ntraces: [FR-02]\nscenarios: [BEH-02]\n"
TEST_SRC = "def test_a():\n    # ENC:BEH-01\n    assert 1\n\ndef test_b():\n    # ENC:BEH-02\n    assert 1\n"

REQUEST = {"protocol": 1, "owner_repo": "devtools", "workstream": "ws", "code": "ENC",
           "bundle_pin": "p" * 40, "product_sha": "s" * 40,
           "test_criteria": [{"id": "ENC:BEH-01", "verify_task": False}, {"id": "ENC:BEH-02", "verify_task": False}]}


def sel(qn, lines=(5,)):
    return {"node_id": f"tests/t.py::{qn}", "definition": {"file": "tests/t.py", "qualname": qn},
            "runs": [{"phase": "call", "outcome": "passed"}, {"phase": "call", "outcome": "passed"}],
            "product_lines": [{"file": "pkg/m.py", "line": n} for n in lines], "subprocess": False}


GOOD = {**{k: REQUEST[k] for k in ("protocol", "owner_repo", "workstream", "code", "bundle_pin", "product_sha")},
        "product_roots": ["pkg"], "environment": {"lock_sha256": "L", "python": "3.12", "pytest_plugins": []},
        "content_sha256": "C",
        "beh": [{"id": "ENC:BEH-01", "status": "traced", "selectors": [sel("test_a")]},
                {"id": "ENC:BEH-02", "status": "traced", "selectors": [sel("test_b")]}]}


def check(resp):
    expected = ck.expected_definitions({"tests/t.py": TEST_SRC}, "ENC", ["BEH-01", "BEH-02"])
    return ck.validate_response(REQUEST, resp, expected=expected,
                                function_lines={"pkg/m.py": {5, 6}}, lock_sha="L", content_sha="C")


def test_good_response_is_valid():
    assert check(GOOD) == []


def mutate(fn):
    r = copy.deepcopy(GOOD)
    fn(r)
    return r


@pytest.mark.parametrize("name,fn", [
    ("foreign product_sha", lambda r: r.update(product_sha="x" * 40)),
    ("missing BEH", lambda r: r["beh"].pop()),
    ("extra BEH", lambda r: r["beh"].append({"id": "ENC:BEH-09", "status": "traced", "selectors": [sel("test_a")]})),
    ("duplicate BEH", lambda r: r["beh"].append(copy.deepcopy(r["beh"][0]))),
    ("no environment", lambda r: r.pop("environment")),
    ("traced one run", lambda r: r["beh"][0]["selectors"][0]["runs"].pop()),
    ("traced not passed", lambda r: r["beh"][0]["selectors"][0]["runs"][1].update(outcome="failed")),
    ("traced zero body lines", lambda r: r["beh"][0]["selectors"][0].update(product_lines=[])),
    ("traced module-level line", lambda r: r["beh"][0]["selectors"][0].update(product_lines=[{"file": "pkg/m.py", "line": 1}])),
    ("traced subprocess-only", lambda r: r["beh"][0]["selectors"][0].update(subprocess=True, product_lines=[])),
    ("traced with reason", lambda r: r["beh"][0].update(reason="no-test")),
    ("unconfirmed without reason", lambda r: r["beh"][0].update(status="unconfirmed")),
    ("selector without token", lambda r: r["beh"][0]["selectors"].__setitem__(0, sel("test_b"))),
    ("incomplete definitions", lambda r: r["beh"][0].update(selectors=[])),
    ("lock mismatch", lambda r: r["environment"].update(lock_sha256="X")),
    ("content mismatch", lambda r: r.update(content_sha256="X")),
    ("error with beh", lambda r: r.update(error="collection")),
    ("not_applicable with beh", lambda r: r.update(not_applicable="language")),
    ("foreign bundle_pin", lambda r: r.update(bundle_pin="x" * 40)),
    ("foreign code", lambda r: r.update(code="XYZ")),
    ("no product_roots", lambda r: r.pop("product_roots")),
    ("no content_sha256", lambda r: r.pop("content_sha256")),
    ("error without reason", lambda r: r["beh"][0].update(status="error", selectors=[])),
])
def test_negative_table(name, fn):
    assert check(mutate(fn)) != [], name


PARAM_SRC = "@pytest.mark.parametrize('x', [1, 2])\ndef test_a(x):\n    # ENC:BEH-01\n    assert x\n"


def test_parametrized_selectors_share_one_definition():
    expected = ck.expected_definitions({"tests/t.py": PARAM_SRC}, "ENC", ["BEH-01"])
    assert expected == {"BEH-01": {("tests/t.py", "test_a")}}


@pytest.mark.parametrize("code,stdout,why", [
    (0, "", "пуст"),
    (0, "not json", "JSON"),
    (2, "{}", "код"),
    (99, "{}", "код"),
])
def test_parse_response_refusals(code, stdout, why):
    resp, reason = ck.parse_response(code, stdout, None)
    assert resp is None and why in reason


def test_parse_response_schema_violation():
    schema = {"type": "object", "required": ["beh"]}
    resp, reason = ck.parse_response(0, "{}", schema)
    assert resp is None and "схем" in reason
    resp, reason = ck.parse_response(0, '{"beh": []}', schema)
    assert resp == {"beh": []} and reason is None


def test_orphan_blocks_closure():
    beh = BEH + "\n#### BEH-03: c\n`traces: [FR-01]`\n- **checked_by**: `status: planned` `kind: unit` `owner: qa` `target: t`\n"
    g = cgr.build_graph(REQ, beh, ACC)
    out = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "traced", "BEH-03": "traced"})
    assert out.closure == "blocked" and any("BEH-03" in r for r in out.stop_reasons)


def test_must_ac_over_should_beh_stops():
    acc = "#### AC-01: a · verification: test\ntraces: [FR-01]\nscenarios: [BEH-01, BEH-02]\n\n#### AC-02: b · verification: test\ntraces: [FR-02]\nscenarios: [BEH-02]\n"
    g = cgr.build_graph(REQ, BEH, acc)
    out = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
    assert out.ac_status["AC-01"] == "unconfirmed"
    assert out.closure == "blocked" and any("AC-01" in r for r in out.stop_reasons)


def test_outcome_must_unconfirmed_stops_should_reports():
    g = cgr.build_graph(REQ, BEH, ACC)
    ok = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "unconfirmed"})
    assert ok.closure == "traced" and ok.stop_reasons == []
    assert any("BEH-02" in r for r in ok.report_rows)
    stop = ck.outcome(g, {"BEH-01": "unconfirmed", "BEH-02": "traced"})
    assert stop.closure == "blocked" and any("BEH-01" in r for r in stop.stop_reasons)
    err = ck.outcome(g, {"BEH-01": "traced", "BEH-02": "error"})
    assert err.closure == "blocked"


def test_graph_errors_block():
    g = cgr.build_graph(REQ, BEH.replace("`traces: [FR-02]`", "`traces: []`"), ACC)
    assert ck.outcome(g, {"BEH-01": "traced", "BEH-02": "traced"}).closure == "blocked"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_criteria_check.py -q`
Expected: FAIL — модуль отсутствует.

- [ ] **Step 3: Implement**

```python
"""criteria_check — сверка ответа criteria-closure/v1 (спека §5.3) и исход (§3.3).

Итоги производителя и статусы AC не принимаются: devtools пересчитывает по
своим байтам (полнота определений, тела функций, lock, content).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from governance import criteria_graph as cgr
from governance import criteria_tokens as ct

REASONS = {
    "unconfirmed": {"no-test", "no-product-execution", "subprocess-only", "not-passed", "nondeterministic"},
    "error": {"io", "runner"},
}
_ECHO = ("protocol", "owner_repo", "workstream", "code", "bundle_pin", "product_sha")
_LEVEL = ("product_roots", "environment", "content_sha256")


def expected_definitions(
    test_files: dict[str, str], code: str, beh_ids: list[str]
) -> dict[str, set[tuple[str, str]]]:
    """BEH → определения тестов, несущие его токен (по байтам devtools)."""
    out: dict[str, set[tuple[str, str]]] = {b: set() for b in beh_ids}
    for path, src in test_files.items():
        for qn, tokens in ct.definition_tokens(src).items():
            for b in beh_ids:
                if f"{code}:{b}" in tokens:
                    out[b].add((path, qn))
    return out


def _traced_selector_findings(bid: str, s: dict, function_lines: dict[str, set[int]]) -> list[str]:
    out: list[str] = []
    runs = [r for r in s.get("runs", []) if r.get("phase") == "call"]
    if len(runs) < 2 or any(r.get("outcome") != "passed" for r in runs):
        out.append(f"{bid}: traced без двух passed в фазе call ({s.get('node_id')})")
    body = [p for p in s.get("product_lines", []) if p.get("line") in function_lines.get(p.get("file"), set())]
    if not body:
        out.append(f"{bid}: traced без исполненных строк тел функций продукта ({s.get('node_id')})")
    if s.get("subprocess") and not body:
        out.append(f"{bid}: traced при исполнении только в подпроцессе ({s.get('node_id')})")
    return out


def validate_response(
    request: dict, response: dict, *, expected: dict[str, set[tuple[str, str]]],
    function_lines: dict[str, set[int]], lock_sha: str, content_sha: str,
) -> list[str]:
    """Пусто — ответ валиден; иначе причины отказа шага."""
    out: list[str] = []
    for key in _ECHO:
        if response.get(key) != request.get(key):
            out.append(f"эхо {key} не совпало с запросом")
    if response.get("error") is not None or response.get("not_applicable") is not None:
        if response.get("beh"):
            out.append("error/not_applicable уровня ответа вместе с beh")
        return out
    for key in _LEVEL:
        if key not in response:
            out.append(f"нет поля {key}")
    env = response.get("environment") or {}
    if env.get("lock_sha256") != lock_sha:
        out.append("sha256 lock не совпал")
    if response.get("content_sha256") != content_sha:
        out.append("content_sha256 не совпал")
    code = request["code"]
    want = [c["id"] for c in request["test_criteria"]]
    got = [b.get("id") for b in response.get("beh", [])]
    if sorted(got) != sorted(want) or len(set(got)) != len(got):
        out.append(f"множество BEH ответа {got} ≠ запросу {want}")
        return out
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        status, reason = b.get("status"), b.get("reason")
        if status == "traced":
            if reason is not None:
                out.append(f"{bid}: traced с reason")
        elif status in REASONS:
            if reason not in REASONS[status]:
                out.append(f"{bid}: {status} без допустимой reason")
            continue
        else:
            out.append(f"{bid}: статус {status!r} вне словаря")
            continue
        defs = {(s["definition"]["file"], s["definition"]["qualname"]) for s in b.get("selectors", [])}
        if defs != expected.get(bid, set()):
            out.append(f"{bid}: определения селекторов {sorted(defs)} ≠ определениям с токеном {code}:{bid}")
        for s in b.get("selectors", []):
            out += _traced_selector_findings(bid, s, function_lines)
    return out


@dataclass(frozen=True)
class Outcome:
    closure: str
    beh_status: dict[str, str]
    ac_status: dict[str, str]
    stop_reasons: list[str] = field(default_factory=list)
    report_rows: list[str] = field(default_factory=list)


def outcome(graph: cgr.Graph, beh_status: dict[str, str]) -> Outcome:
    """Таблица §3.3: Must unconfirmed/error и любой error — стоп."""
    stops = [f"граф: {e}" for e in graph.errors] + [f"сирота: {o}" for o in cgr.orphan_findings(graph)]
    rows: list[str] = []
    for beh in cgr.test_behs(graph):
        st = beh_status.get(beh.id, "error")
        if st == "traced":
            continue
        line = f"{beh.id} ({beh.priority}): {st}"
        if st == "error" or beh.priority == "Must":
            stops.append(line)
        else:
            rows.append(line)
    acs = {a.id: cgr.derive_ac(a, graph, beh_status) for a in graph.acs.values()}
    for a in graph.acs.values():
        if a.priority == "Must" and acs[a.id] in ("unconfirmed", "error"):
            stops.append(f"{a.id} (Must): {acs[a.id]}")
    return Outcome("blocked" if stops else "traced", dict(beh_status), acs, stops, rows)


def parse_response(code: int, stdout: str, schema: dict | None) -> tuple[dict | None, str | None]:
    """Первая линия отказа §5.3: код, пустота, JSON, схема (если вендорена)."""
    import json

    import jsonschema

    if code not in (0, 3):
        return None, f"код выхода {code} вне договорённых 0/3"
    if not stdout.strip():
        return None, "ответ пуст"
    try:
        resp = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"ответ не JSON: {exc}"
    if schema is not None:
        try:
            jsonschema.validate(resp, schema)
        except jsonschema.ValidationError as exc:
            return None, f"ответ не по схеме: {exc.message}"
    return resp, None
```

`function_lines` строит вызывающий (Task 8) по своим байтам на `product_sha`: для каждого файла продуктовых корней — множество номеров строк, лежащих внутри тел `FunctionDef`/`AsyncFunctionDef` (от `body[0].lineno` до `end_lineno`), не на модульном уровне и не в телах классов вне методов (G0 rev 8).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_criteria_check.py -q`
Expected: PASS (17 отрицательных строк + позитив + исход).

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_check.py tests/test_governance_criteria_check.py
git commit -m "feat(governance): criteria_check — сверка ответа §5.3 и исход §3.3"
```

---

### Task 7: раннер — charter схемы 2 при авторинге

**Files:**
- Modify: `governance/run_state.py` (класс `RunState`: поля `code: str | None = None`, `plan_item: str | None = None`)
- Modify: `governance/spec_loop.py` (аргументы `--code`, `--plan-item` для нового прогона)
- Modify: `governance/runner.py` — `_step_authoring` (после авторинга узла charter) и список путей коммита
- Test: `tests/test_governance_runner.py`, `tests/test_governance_spec_loop.py`

**Interfaces:**
- Consumes: `charter_guard.stamp_charter`; `Ops.charter_codes_elsewhere(target_dir, repo_slug, base_ref, own_ws) -> dict[code, ws]` (профилактика коллизии: живая верхушка default + открытые candidate W1).
- Produces: новый прогон с `code`+`plan_item` рождает charter схемы 2 (штамп и на пути resume, идемпотентно); код, занятый на живой верхушке или в открытом W1, — `stopped_preflight` до штампа; недоступность факта — тоже стоп. Реестра-файла нет, коммитится только бандл. **Редакция после ревью 7–8** — код в коммите `1a8db90`; регрессия на настоящем git — `tests/test_governance_waves_realgit.py`. Прогон без них — прежнее поведение (схема 1), чтобы живые и исторические прогоны не ломались.

- [ ] **Step 1: Write the failing tests**

В `tests/test_governance_runner.py` рядом с существующими тестами `_step_authoring` (grep `_step_authoring`) — по образцу ближайшего теста, использующего `FakeOps` и временный target:

```python
def test_authoring_stamps_schema2_charter_and_registers_code(tmp_path):
    state, ops = <build like the nearest _step_authoring test>, 
    state.code, state.plan_item = "ENC", "todo://devtools/oracle"
    runner._step_authoring(state, ops)
    charter = (Path(state.target_dir) / state.bundle_dir / "00-charter.md").read_text()
    assert charter_guard.read_charter(charter) == charter_guard.Charter(2, "ENC", "todo://devtools/oracle")
    reg = charter_guard.load_registry((Path(state.target_dir) / "workstreams/codes.toml").read_text())
    assert reg["ENC"]["workstream"] == state.ws_id


def test_authoring_without_code_keeps_schema1(tmp_path):
    state, ops = <same>
    runner._step_authoring(state, ops)
    charter = (Path(state.target_dir) / state.bundle_dir / "00-charter.md").read_text()
    assert charter_guard.read_charter(charter).schema == 1
```

В `tests/test_governance_spec_loop.py`: `--code ENC --plan-item todo://devtools/oracle` попадают в `RunState` нового прогона; `--code enc` → `SpecLoopError` с «CODE».

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_runner.py tests/test_governance_spec_loop.py -q -k "schema2 or schema1 or plan_item or code"`
Expected: FAIL — нет полей/аргументов.

- [ ] **Step 3: Implement**

`RunState`: два поля со значением по умолчанию `None` (сериализация `run_state.save/load` уже работает через `dataclasses.asdict`/конструктор — сверить и, если загрузка строгая к ключам, принимать отсутствующие как `None`).

`spec_loop.py`: `parser.add_argument("--code")`, `parser.add_argument("--plan-item")`; при создании нового прогона валидировать `charter_guard.CODE_RE`/`PLAN_ITEM_RE` (ошибка — `SpecLoopError` с текстом «CODE ^[A-Z]{2,6}$» / «plan_item todo://<repo>/<id>») и передавать в `RunState`.

`runner._step_authoring`: сразу после того, как узел `charter` записан авторингом (ветка, где пишется `00-charter.md`), при `state.code and state.plan_item`:

```python
        charter_path = Path(state.target_dir) / state.bundle_dir / "00-charter.md"
        charter_path.write_text(
            charter_guard.stamp_charter(
                charter_path.read_text(encoding="utf-8"),
                code=state.code, plan_item=state.plan_item,
            ),
            encoding="utf-8",
        )
        registry = Path(state.target_dir) / charter_guard.REGISTRY_PATH
        registry.parent.mkdir(parents=True, exist_ok=True)
        current = registry.read_text() if registry.exists() else ""
        if state.code not in charter_guard.load_registry(current):
            registry.write_text(charter_guard.register_code(
                current, code=state.code, ws_id=state.ws_id,
                approved=datetime.date.today().isoformat(),
            ))
```

и добавить `charter_guard.REGISTRY_PATH` в список путей, которые `_step_commit` передаёт `ops.commit_paths` для волны charter (grep `commit_paths(` в runner.py; путь реестра — только когда `state.code`). Занятый код (`ValueError` из `register_code`) — `_stop_with_comment(state, ops, "stopped_preflight", f"code {state.code} занят: …")`.

Ruling-заметка: `approved` = дата штампа при авторинге, а не дата мержа одобрения — реестр фиксирует резервирование кода; неизменность проверяет `charter_guard` на каждом PR.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_runner.py tests/test_governance_spec_loop.py -q`
Expected: PASS, включая все прежние тесты (прогоны без `--code` не меняются).

- [ ] **Step 5: Commit**

```bash
git add governance/run_state.py governance/spec_loop.py governance/runner.py tests/test_governance_runner.py tests/test_governance_spec_loop.py
git commit -m "feat(runner): charter схемы 2 — code и plan_item при авторинге, запись в реестр кодов"
```

---

### Task 8: `criteria_close` — команда закрытия (срез 1)

**Files:**
- Create: `governance/criteria_close.py`
- Modify: `governance/ops.py` (Protocol + `RealOps`: `criteria_verify(target_dir: str, request_path: str) -> tuple[int, str]`)
- Modify: `Makefile` (цель `criteria-close`, строка help)
- Test: `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: `run_state.load(run_id) -> RunState`, `runner._verified_result_sha(state) -> str | None`, `charter_guard.read_charter`, `criteria_graph.build_graph/test_behs`, `criteria_check.expected_definitions/validate_response/outcome/parse_response`, `criteria_contract.read_min_version/oracle_available/vendored`, `governance.frontmatter.join_frontmatter`, `task_bridge.spec_runner_version`, `spec_runner_contract.target_selector_policy`, `Ops.create_pr/review/merge/find_pr/push_branch/commit_paths/ensure_branch/head_sha`.
- Produces:
  - `decide_not_applicable(charter: Charter, selector_policy, installed: str | None, minimum: MinVersion, *, is_vendored: bool) -> str | None` (`schema-1` · `language` · `spec-runner-version` · `None`)
  - `render_closure(outcome_or_na, *, ws_id, code, bundle_pin, product_sha, response_sha: str | None, spec_runner_version: str | None, host: str) -> str` — текст `90-acceptance-closure.md` с frontmatter `closure`, `not_applicable_reason`, `human_pending`, `spec_runner_version`, `host`, `bundle_pin`, `product_sha`, счётчики и таблица
  - `run(run_id: str, ops: Ops, *, product_sha: str | None = None) -> int` — 0 = файл закрытия опубликован (любой `closure`), 2 = отказ шага (невалидный ответ, нет пина), 6 = ключ уже измерен
  - Состояние: `out/criteria-close/<run_id>/state.json` — `{"measured": {"<bundle_pin>:<content_sha>": "<closure>"}, "pending": {...write-ahead...}}`

- [ ] **Step 1: Write the failing tests**

```python
"""criteria_close: срез 1 — not-applicable, повтор ключа, файл закрытия, публикация."""
from __future__ import annotations

from governance import charter_guard as cg
from governance import criteria_close as cc
from governance import criteria_contract as ctr

MIN = ctr.MinVersion("4.3.0")


def test_not_applicable_order():
    ch1 = cg.Charter(1, None, None)
    ch2 = cg.Charter(2, "ENC", "todo://devtools/x")
    assert cc.decide_not_applicable(ch1, None, "9.9.9", MIN, is_vendored=True) == "schema-1"
    exunit = type("P", (), {"name": "exunit"})()
    assert cc.decide_not_applicable(ch2, exunit, "9.9.9", MIN, is_vendored=True) == "language"
    assert cc.decide_not_applicable(ch2, None, "9.9.9", MIN, is_vendored=False) == "spec-runner-version"
    assert cc.decide_not_applicable(ch2, None, "4.2.0", MIN, is_vendored=True) == "spec-runner-version"
    assert cc.decide_not_applicable(ch2, None, "4.3.0", MIN, is_vendored=True) is None


def test_closure_file_frontmatter_records_version_and_host():
    text = cc.render_closure("spec-runner-version", ws_id="ws", code=None, bundle_pin="p" * 40,
                             product_sha="s" * 40, response_sha=None,
                             spec_runner_version="4.2.0", host="pr0sto.net")
    assert "closure: not-applicable" in text and "not_applicable_reason: spec-runner-version" in text
    meta, _ = split_frontmatter(text)  # разбирается — нет голых «-»
    assert meta["spec_runner_version"] == "4.2.0" and meta["host"] == "pr0sto.net"
    assert meta["code"] is None and meta["response_sha256"] is None
    assert "Оракул не применим" in text


def test_remeasure_same_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "STATE_ROOT", tmp_path)
    cc._record_measured("run-1", "PIN:CONTENT", "blocked")
    assert cc._already_measured("run-1", "PIN:CONTENT") == "blocked"
    assert cc._already_measured("run-1", "PIN:OTHER") is None


def test_run_publishes_not_applicable_closure_for_schema1(<FakeOps-based fixture: completed run state with schema-1 bundle>):
    rc = cc.run("run-1", ops)
    assert rc == 0
    assert ops.existing_prs  # PR создан (FakeOps.create_pr пишет existing_prs)
    assert any("90-acceptance-closure.md" in p for p in ops.committed[-1][1])
    assert any(c[0] == "review" for c in ops.calls)  # scope-аттестация
    assert ops.merged  # агентский мерж
```

(Фикстура для последнего теста — `FakeOps` из `tests/test_governance_runner.py` как есть: он уже пишет `existing_prs` (create_pr), `committed` (commit_paths), `calls` с `("review", pr)` и `merged`; поле `reviews` у него — другое (возврат `pr_reviews`), не использовать; `run_state` подменяется `monkeypatch.setattr(cc.run_state, "load", lambda rid: state)`, `runner._verified_result_sha` — `lambda s: "p"*40`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_criteria_close.py -q`
Expected: FAIL — модуль отсутствует.

- [ ] **Step 3: Implement**

```python
"""criteria_close — закрытие воркстрима по оракулу бандла, срез 1 (спека §7.1).

Измерение (spec-runner verify --criteria) → сверка (§5.3) → исход (§3.3) →
файл закрытия `workstreams/<ws>/spec/90-acceptance-closure.md` агентским PR
(создаёт учётка оператора, scope-аттестация ai-prosto, мерж merge-pr.sh).
Ревизий, подписи и штампа нет — это срез 2. Флага обхода стопа нет и быть
не должно (спека §3.3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from pathlib import Path

from governance import charter_guard, criteria_check, criteria_contract, criteria_graph
from governance import run_state, runner, spec_runner_contract, task_bridge
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import Ops, RealOps

STATE_ROOT = Path(__file__).resolve().parent.parent / "out" / "criteria-close"
CLOSURE_NAME = "90-acceptance-closure.md"


def decide_not_applicable(charter, selector_policy, installed, minimum, *, is_vendored) -> str | None:
    """Порядок: схема 1 → язык → доступность оракула (вендоринг и версия машины)."""
    if charter.schema != 2:
        return "schema-1"
    if selector_policy is not None and selector_policy.name != "pytest":
        return "language"
    if not criteria_contract.oracle_available(installed, minimum, is_vendored=is_vendored):
        return "spec-runner-version"
    return None


def _state_path(run_id: str) -> Path:
    return STATE_ROOT / run_id / "state.json"


def _load(run_id: str) -> dict:
    p = _state_path(run_id)
    return json.loads(p.read_text()) if p.exists() else {"measured": {}}


def _save(run_id: str, data: dict) -> None:
    p = _state_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)


def _already_measured(run_id: str, key: str) -> str | None:
    return _load(run_id)["measured"].get(key)


def _record_measured(run_id: str, key: str, closure: str) -> None:
    data = _load(run_id)
    data["measured"][key] = closure
    _save(run_id, data)


def render_closure(result, *, ws_id, code, bundle_pin, product_sha, response_sha,
                   spec_runner_version, host) -> str:
    """Текст файла закрытия; result — Outcome или строка причины not-applicable.

    Frontmatter пишется `join_frontmatter` (yaml.safe_dump): отсутствующее
    значение — YAML null, не голый `-` (его не разберёт split_frontmatter).
    """
    meta: dict = {"workstream": ws_id, "code": code, "bundle_pin": bundle_pin,
                  "product_sha": product_sha, "response_sha256": response_sha,
                  "spec_runner_version": spec_runner_version, "host": host}
    if isinstance(result, str):
        meta.update(closure="not-applicable", not_applicable_reason=result, human_pending=0)
        body = [f"Оракул не применим: `{result}` (спека §3.6). Приёмки по критериям нет."]
    else:
        human = sum(1 for s in result.ac_status.values() if s == "human")
        traced = sum(1 for s in result.beh_status.values() if s == "traced")
        meta.update(closure=result.closure, human_pending=human)
        body = [
            f"Прослежено {traced} из {len(result.beh_status)} test-критериев; ждут человека {human}.",
            "`traced` не утверждает способность теста упасть (спека §4.1).",
            "", "## Стоп", *([f"- {r}" for r in result.stop_reasons] or ["- нет"]),
            "", "## Отчёт", *([f"- {r}" for r in result.report_rows] or ["- нет"]),
            "", "## AC", *[f"- {k}: {v}" for k, v in sorted(result.ac_status.items())],
        ]
    return join_frontmatter(meta, "# Закрытие воркстрима\n\n" + "\n".join(body) + "\n")


def _publish(state, ops: Ops, text: str, closure: str) -> int:
    """Агентский PR файла закрытия: write-ahead ветки, аттестация, мерж."""
    rel = f"{state.bundle_dir}/{CLOSURE_NAME}"
    branch = f"criteria-close/{state.ws_id}"
    existing = ops.find_pr(state.repo_slug, branch)
    if existing is None:
        ops.ensure_branch(state.target_dir, branch)
        (Path(state.target_dir) / rel).write_text(text, encoding="utf-8")
        ops.commit_paths(state.target_dir, [rel], f"criteria-close: {state.ws_id} — {closure}")
        ops.push_branch(state.target_dir, branch)
        pr = ops.create_pr(state.target_dir, state.repo_slug, branch,
                           f"criteria-close: {state.ws_id} — {closure}",
                           f"Файл закрытия воркстрима (срез 1 оракула). closure: {closure}.",
                           "criteria-close")
    else:
        pr = existing
    if ops.review(state.repo, pr) != 0:
        return 2
    head = ops.head_sha(state.target_dir, branch)
    return 0 if ops.merge(state.repo, pr, head) == 0 else 2
```

Остаток `run(...)` (в том же файле):

```python
def run(run_id: str, ops: Ops, *, product_sha: str | None = None) -> int:
    state = run_state.load(run_id)
    if state.status != "completed":
        print(f"criteria-close: прогон {run_id} в статусе {state.status!r}, нужен completed")
        return 2
    bundle_pin = runner._verified_result_sha(state)
    if bundle_pin is None:
        print("criteria-close: у прогона нет идентичности результата (finalize) — пин неизвестен")
        return 2
    bundle = Path(state.target_dir) / state.bundle_dir
    charter = charter_guard.read_charter((bundle / "00-charter.md").read_text(encoding="utf-8"))
    product_sha = product_sha or ops.head_sha(state.target_dir, "HEAD")
    installed = task_bridge.spec_runner_version()
    host = socket.gethostname()
    na = decide_not_applicable(
        charter, spec_runner_contract.target_selector_policy(state.target_dir),
        installed, criteria_contract.read_min_version(),
        is_vendored=criteria_contract.vendored(),
    )
    if na is not None:
        text = render_closure(na, ws_id=state.ws_id, code=charter.code,
                              bundle_pin=bundle_pin, product_sha=product_sha, response_sha=None,
                              spec_runner_version=installed, host=host)
        return _publish(state, ops, text, "not-applicable")
    return _measure_and_publish(state, ops, charter, bundle, bundle_pin, product_sha)
```

`_measure_and_publish` (тот же файл) — до выпуска spec-runner недостижим, но реализуется целиком и покрывается тестом с подменённым `ops.criteria_verify`:
1. `graph = criteria_graph.build_graph(<10>, <15>, <25>)` по текстам узлов бандла;
2. `content_sha` = sha256 по отсортированным (путь, байты) всех `*.py` под `tests/` и продуктовыми корнями (продуктовые корни — из ответа недоступны до вызова, поэтому ключ сначала по `tests/` + всем отслеживаемым `*.py` вне `tests/`; ruling: это надмножество продуктовых корней — строже, не слабее); `key = f"{bundle_pin}:{content_sha}"`; `_already_measured` → вернуть 6 с сообщением «ключ измерен (§3.1 G6): доработайте продукт»;
3. запрос §5.1 → файл в `STATE_ROOT/run_id/request.json`; `code, out = ops.criteria_verify(state.target_dir, request_path)`;
4. `response, why = criteria_check.parse_response(code, out, schema)` (`schema` — `response.schema.json` вендоренного контракта или `None`); `why` → печать и 2 (повтор); ответ с `error` уровня ответа (код 3) → `closure: blocked`; `expected` через `criteria_check.expected_definitions` по `tests/**/*.py`; `function_lines` — по AST продуктовых корней из ответа; `lock_sha` — sha256 `uv.lock`; `problems = criteria_check.validate_response(...)` → непусто: печать и 2;
5. `beh_status = {b["id"].split(":")[1]: b["status"] for b in response["beh"]}`; `result = criteria_check.outcome(graph, beh_status)`; `_record_measured(run_id, key, result.closure)`; `render_closure(result, …, spec_runner_version=installed, host=host)`; `_publish(...)`.

`RealOps.criteria_verify`: `subprocess.run(["spec-runner", "verify", "--criteria", "--request", request_path, "--json"], cwd=target_dir, capture_output=True, text=True)` → `(returncode, stdout)`.

`main()`: `--run` (обязателен), `--product-sha`; `return run(args.run, RealOps(), product_sha=args.product_sha)`. Makefile:

```make
criteria-close: ; @uv run --frozen python -m governance.criteria_close $(ARGS)
```

и строка help: `make criteria-close ARGS='--run <id> [--product-sha <sha>]' — закрытие воркстрима по оракулу бандла (срез 1: файл закрытия агентским PR; флага обхода нет)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_criteria_close.py -q`
Expected: PASS. Добавить тест `_measure_and_publish` с `ops.criteria_verify`, возвращающим `(0, json.dumps(GOOD-like response))` — `closure: traced`, и с ответом, где Must-BEH `unconfirmed` — `closure: blocked` (файл публикуется в обоих случаях), и повтор того же ключа → 6 без вызова `criteria_verify`.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_close.py governance/ops.py Makefile tests/test_governance_criteria_close.py
git commit -m "feat(governance): criteria-close — закрытие воркстрима, срез 1"
```

---

### Task 9: гейт `[x]` — `closure_gate` и шаги CI (отдельный PR, мерж человеком)

**Files:**
- Create: `governance/closure_gate.py`
- Test: `tests/test_governance_closure_gate.py`
- Modify: `.github/workflows/ci.yml` (шаги `charter_guard` и `closure_gate`) — **в отдельном PR после мержа Tasks 1–8**: трогает `.github/`, мержит человек

**Interfaces:**
- Consumes: `charter_guard.read_charter/PLAN_ITEM_RE`, `criteria_contract.vendored`, `governance.frontmatter.split_frontmatter`.
- Produces:
  - `gate_findings(repo: Path, *, is_vendored: bool) -> tuple[list[str], list[str]]` → (ошибки, предупреждения). Гейт CI не зависит от spec-runner машины: блокировка `not-applicable: spec-runner-version` — по данным репо (контракт вендорен), версия и `host` из файла закрытия печатаются в предупреждении/ошибке.
  - CLI `python -m governance.closure_gate --repo .` → exit 1 при ошибках; предупреждения печатаются со счётчиком.

- [ ] **Step 1: Write the failing tests (таблица §7.1)**

```python
"""closure_gate: [x] пункта плана требует файл закрытия (спека §7.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from governance import closure_gate as g

CH2 = "---\nschema: 2\ncode: {code}\nplan_item: todo://repo/{item}\n---\n"


def make(tmp_path: Path, *, done: bool, closure: str | None, item="oracle", ws="ws-a", code="ENC") -> Path:
    repo = tmp_path / "repo"
    (repo / f"workstreams/{ws}/spec").mkdir(parents=True, exist_ok=True)
    (repo / f"workstreams/{ws}/spec/00-charter.md").write_text(CH2.format(code=code, item=item))
    mark = "x" if done else " "
    (repo / "TODO.md").write_text(f"- [{mark}] пункт @id:{item}\n")
    if closure is not None:
        (repo / f"workstreams/{ws}/spec/90-acceptance-closure.md").write_text(f"---\n{closure}\n---\n")
    return repo


NA_SR = "closure: not-applicable\nnot_applicable_reason: spec-runner-version\nspec_runner_version: 4.2.0\nhost: mac"


@pytest.mark.parametrize("closure,is_vendored,red", [
    (None, False, True),
    ("closure: blocked", False, True),
    ("closure: traced\nhuman_pending: 0", False, False),
    ("closure: not-applicable\nnot_applicable_reason: schema-1", True, False),
    (NA_SR, False, False),
    (NA_SR, True, True),
])
def test_gate_table(tmp_path, closure, is_vendored, red):
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=closure), is_vendored=is_vendored)
    assert bool(errors) is red


def test_na_version_names_version_and_host_in_error_and_warning(tmp_path):
    errors, _ = g.gate_findings(make(tmp_path, done=True, closure=NA_SR), is_vendored=True)
    assert any("4.2.0" in e and "mac" in e for e in errors)
    _, warns = g.gate_findings(make(tmp_path, done=True, closure=NA_SR), is_vendored=False)
    assert any("4.2.0" in w and "mac" in w for w in warns)


def test_language_is_green_with_visible_warning(tmp_path):
    na = "closure: not-applicable\nnot_applicable_reason: language"
    errors, warns = g.gate_findings(make(tmp_path, done=True, closure=na), is_vendored=True)
    assert errors == [] and any("language" in w for w in warns)


def test_gate_reads_a_real_rendered_closure(tmp_path):
    from governance import criteria_close
    repo = make(tmp_path, done=True, closure=None)
    text = criteria_close.render_closure(
        "spec-runner-version", ws_id="ws-a", code=None, bundle_pin="p" * 40,
        product_sha="s" * 40, response_sha=None, spec_runner_version=None, host="mac")
    (repo / "workstreams/ws-a/spec/90-acceptance-closure.md").write_text(text)
    errors, warns = g.gate_findings(repo, is_vendored=False)
    assert errors == [] and warns


def test_open_item_is_not_checked(tmp_path):
    errors, _ = g.gate_findings(make(tmp_path, done=False, closure=None), is_vendored=False)
    assert errors == []


def test_warnings_visible_for_human_pending(tmp_path):
    _, warns = g.gate_findings(make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 2"),
                               is_vendored=False)
    assert any("human" in w for w in warns)


def test_two_charters_same_item_both_checked(tmp_path):
    repo = make(tmp_path, done=True, closure="closure: traced\nhuman_pending: 0")
    make(tmp_path, done=True, closure=None, ws="ws-b", code="ABC")
    errors, _ = g.gate_findings(repo, is_vendored=False)
    assert any("ws-b" in e for e in errors)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_governance_closure_gate.py -q`
Expected: FAIL — модуль отсутствует.

- [ ] **Step 3: Implement**

```python
"""closure_gate — потребитель стопа среза 1: [x] пункта плана требует закрытия.

Спека §7.1: пункт @id:X отмечен [x], и в репо есть charter схемы 2 с
plan_item todo://<repo>/X → рядом обязан лежать 90-acceptance-closure.md не
в состоянии blocked. Гейт живёт в CI репо-владельца; check-plan-fields.py
(сенсор без CI) может показывать то же только как предупреждение.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from governance import charter_guard, criteria_contract
from governance.frontmatter import split_frontmatter

_DONE = re.compile(r"^\s*- \[x\].*?@id:([A-Za-z0-9_.-]+)", re.M)


def gate_findings(repo: Path, *, is_vendored: bool) -> tuple[list[str], list[str]]:
    todo = repo / "TODO.md"
    done = set(_DONE.findall(todo.read_text())) if todo.exists() else set()
    errors: list[str] = []
    warns: list[str] = []
    for charter_path in sorted(repo.glob("workstreams/*/spec/00-charter.md")):
        ch = charter_guard.read_charter(charter_path.read_text())
        if ch.schema != 2 or not ch.plan_item:
            continue
        m = charter_guard.PLAN_ITEM_RE.match(ch.plan_item)
        if m is None or m.group(2) not in done:
            continue
        ws = charter_path.parent.parent.name
        closure_path = charter_path.parent / "90-acceptance-closure.md"
        if not closure_path.exists():
            errors.append(f"@id:{m.group(2)} [x], но у {ws} нет файла закрытия")
            continue
        meta, _ = split_frontmatter(closure_path.read_text())
        state = meta.get("closure")
        if state == "blocked":
            errors.append(f"@id:{m.group(2)} [x], но закрытие {ws} — blocked")
        elif state == "traced":
            if int(meta.get("human_pending", 0)) > 0:
                warns.append(f"{ws}: human_pending={meta['human_pending']} — подпись в срезе 2")
        elif state == "not-applicable":
            reason = meta.get("not_applicable_reason")
            where = f"spec-runner {meta.get('spec_runner_version')} на {meta.get('host')}"
            if reason == "spec-runner-version" and is_vendored:
                errors.append(f"{ws}: not-applicable spec-runner-version ({where}) при вендоренном контракте — перегнать закрытие на машине с spec-runner ≥ min")
            elif reason == "spec-runner-version":
                warns.append(f"{ws}: оракул не применим (spec-runner-version, {where})")
            else:
                warns.append(f"{ws}: оракул не применим ({reason})")
        else:
            errors.append(f"{ws}: closure {state!r} вне словаря traced|blocked|not-applicable")
    return errors, warns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="closure_gate")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    errors, warns = gate_findings(args.repo.resolve(), is_vendored=criteria_contract.vendored())
    for w in warns:
        print(f"closure_gate: warning: {w}")
    if warns:
        print(f"closure_gate: предупреждений {len(warns)}")
    for e in errors:
        print(f"closure_gate: error: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_governance_closure_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit (в ветке кода), затем CI отдельным PR**

```bash
git add governance/closure_gate.py tests/test_governance_closure_gate.py
git commit -m "feat(governance): closure_gate — [x] пункта плана требует файл закрытия"
```

Отдельная ветка после мержа кода, `.github/workflows/ci.yml`, после шага `make plan-check-selftest`:

```yaml
      - run: uv run --frozen python -m governance.charter_guard --repo . --base "origin/${{ github.base_ref || 'master' }}"
      - run: uv run --frozen python -m governance.closure_gate --repo .
      - run: uv run --frozen python -c "import sys; from governance.criteria_contract import integrity_findings as f; r=f(); print(*r, sep='\n'); sys.exit(1 if r else 0)"
      - uses: actions/checkout@<тот же SHA, что у первого checkout в ci.yml>
        with: { repository: andrei-shtanakov/spec-runner, path: spec-runner-upstream, fetch-depth: 0 }
      - run: uv run --frozen python -c "import sys; from pathlib import Path; from governance.criteria_contract import CONTRACT_DIR, drift_findings as f; e,n=f(CONTRACT_DIR, Path('spec-runner-upstream'), ci=True); print(*e, *n, sep='\n'); sys.exit(1 if e else 0)"
```

(checkout апстрима spec-runner — вторая гарантия вендоринга, дрейф; пока контракт не вендорен, шаг пуст. У `actions/checkout` для `--base` нужен `fetch-depth: 0` или явный `git fetch origin <base>` — добавить шаг `git fetch --depth=1 origin "${{ github.base_ref || 'master' }}"` перед `charter_guard`). PR помечается к человеческому мержу (трогает `.github/`).

---

### Task 10: документация, план, заявка steward

**Files:**
- Modify: `CLAUDE.md` (строка в таблицу инструментов: `criteria-close`, `charter_guard`, `closure_gate`)
- Modify: `TODO.md` (раздел DarkFactory: пункт `@id:bundle-docs-as-oracle` — ссылка на спеку и план; новые пункты `@id:bundle-oracle-slice1` и `@id:bundle-oracle-slice2`; ожидание spec-runner — `@blocked_by:spec-runner#603` на пункте живой приёмки среза 1)
- Внешнее действие: inbox-issue в steward (спека §6: парсер BEH `kind: unit` и суффикс `[a-z]`, гейт `schema: 2`/`code:`/`plan_item`/реестра или вендоринг `charter_guard`, сироты §1.7, указание «свойство текста продукта → `kind: manual`» в шаблоне 15-behaviour-spec)

- [ ] **Step 1:** Правки `CLAUDE.md` и `TODO.md` по списку выше (формат пунктов — как у соседних пунктов `TODO.md`, теги в одной строке с чекбоксом).
- [ ] **Step 2:** `uv run --frozen python ./check-plan-fields.py --selftest` и `make plan-check` — без новых ошибок по devtools.
- [ ] **Step 3:** Commit:

```bash
git add CLAUDE.md TODO.md
git commit -m "docs: оракул бандла — срез 1 в CLAUDE.md и TODO.md"
```

- [ ] **Step 4:** Заявка steward — `gh issue create -R andrei-shtanakov/steward --label inbox` с `slug: bundle-oracle-parsers`, `from: devtools`, пунктами §6 и ссылкой на спеку по коммиту; номер записать в пункт `TODO.md` (`@blocked_by:steward#N` там, где ждём).

---

## Приёмка среза 1 (не часть исполнения плана)

Срез 1 закрыт только живым прогоном на spec-runner ≥ X (после spec-runner#603 и PR, вендорящего схемы, `PIN` и `manifest.json`): пункты 1–4 спеки §8.4 и красный гейт `[x]` при `closure: blocked`. До этого код может быть влит, но пункт `@id:bundle-oracle-slice1` не закрывается.
