# conductor, срез 0 — советчик: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read-only советчик: собрать единый граф зависимостей флота с опубликованных веток и GitHub, проверить ожидания, разложить готовую работу по `roadmap.toml` и объяснить порядок (`status`, `why`, `plan`, `run` → снимок), ничего не записывая во флот.

**Architecture:** Пакет `conductor/` в devtools (`python -m conductor`). Сбор входов (`sources_git`, `sources_gh`) → сериализуемый `Inputs` (он же формат replay) → чистое детерминированное ядро (`graph` → `waits` → `analysis` → `rank` → `policy`) → `snapshot` (контракт `conductor-snapshot/v1`) и текстовый рендер. Ядро не делает ввода-вывода: всё, что оно знает, лежит в `Inputs`, поэтому replay на сохранённых входах даёт тот же результат, что живой прогон.

**Tech Stack:** Python ≥ 3.12 (uv-окружение devtools), пакет `plan-fields` (пин в `pyproject.toml`), stdlib (`tomllib`, `subprocess`, `dataclasses`, `json`), `gh` CLI и `git` как внешние процессы, pytest, jsonschema (dev-группа).

**Spec:** `docs/superpowers/specs/2026-09-29-conductor-design.md` (rev 9). Срез 0 — §11: §2, §3 (без модели), §4, §5.2–5.3 как выдача, §7.1–7.2, таймер на уровне 0, `roadmap.toml` в зонтике.

## Global Constraints

- Никаких записей во флот и GitHub: ни один модуль среза не вызывает мутирующих `gh`/`git push`; `git fetch` — единственная сетевая операция git (спека §11, срез 0 «советчик»).
- `run_level` среза 0 всегда 0; `plan --level N` вычисляет гипотетические действия и тоже ничего не пишет (§2.4, §7.2).
- Читать TODO и роадмап только с `origin/<default>`, SHA — в снимок; рабочая копия не источник (I6).
- `partial` при любом непрочитанном источнике, усечённом поиске или недогруженной ссылке (I6).
- `_cowork_output/` не читается никогда (I9).
- Модель не вызывается: проза `@trigger` → вердикт `unknown` (§3.4, §11 срез 0).
- Коды выхода: 0 — выполнено (`complete`/`partial`/`skipped`); 2 — аргументы CLI; 3 — не собран ни один источник; 4 — `RM-INVALID`, снимок записан (§7.2).
- Python ≥ 3.12, запуск через `uv run --frozen`; строки ≤ 88; type hints везде; ruff и pyrefly чистые (CLAUDE.md владельца).
- Имена репо — канонические ключи манифеста (`workspace-manifest.toml`) плюс зонтик `ai-orchestrators-workspace` (§3.1).
- Контракт снимка — `devtools/contracts/conductor-snapshot/v1/schema.json` (§7.1).

**Отступление от раскладки §12 спеки, названное явно:** CLI — `conductor/__main__.py` (`python -m conductor`), а не `conductor.py`: файл и пакет с одним именем в корне devtools конфликтуют при импорте. Паттерн тот же, что у `selfcheck/`.

## Review Focus

1. Клон, у которого не выставлен `origin/HEAD` (клон без symbolic-ref) — ожидаю откат на `origin/master` / `origin/main`, иначе источник `error`, а не падение. Тест — Task 4.
2. У репо нет `TODO.md` на default-ветке — источник `absent`, граф при этом `complete` (отсутствие файла — не «не прочитали»). Тест — Task 4.
3. `gh` не авторизован или офлайн — GitHub-источник `error`, граф `partial`, TODO-плоскость всё равно ранжируется, код выхода 0. Тест — Task 5 и Task 11.
4. Тело inbox-issue со значениями в бэктиках и CRLF (живой `arbiter#104`: ``from: `deployer` ``) — поля разбираются без кавычек и `\r`. Тест — Task 6.
5. Пункт ждёт сам себя (`@blocked_by` на свой `@id`) и длинные цепочки — цикл найден, без рекурсии и зависания (итеративный Tarjan). Тест — Task 8.

---

## Файлы

| Файл | Ответственность |
|---|---|
| `conductor/__init__.py` | версия пакета, ничего больше |
| `conductor/model.py` | неизменяемые типы: `Source`, `Node`, `Edge`, `Finding` |
| `conductor/roadmap.py` | разбор и валидация `roadmap.toml` (§2.1–2.3) |
| `conductor/inputs.py` | `Inputs` — всё, что ядро знает о мире; JSON для replay |
| `conductor/manifest.py` | репо-цели и имена из манифеста (+ зонтик) |
| `conductor/sources_git.py` | `git fetch`, `TODO.md` с `origin/<default>`, движение по `@id` |
| `conductor/sources_gh.py` | `gh`: обнаружение, адресное дочитывание, комментарии |
| `conductor/collect.py` | сборка `Inputs` из двух источников |
| `conductor/graph.py` | узлы и типизированные рёбра (§3.1–3.2) |
| `conductor/waits.py` | состояние предпосылки и вердикты ожиданий (§3.4) |
| `conductor/analysis.py` | готовность, циклы, находки, состояние работы (§3.3, §3.5) |
| `conductor/rank.py` | протекание ранга, ключ сортировки, `why` (§4) |
| `conductor/policy.py` | уровень позиции, `actor/need`, делегируемость, действие (§2.4, §5.2–5.3) |
| `conductor/snapshot.py` | снимок `conductor-snapshot/v1` и показатели (§7.1, §10) |
| `conductor/render.py` | текст для `status`, `why`, `plan` |
| `conductor/__main__.py` | CLI и коды выхода (§7.2) |
| `contracts/conductor-snapshot/v1/schema.json` | JSON-схема снимка |
| `tests/conductor/…` | тесты по модулям + фикстуры |
| `deploy/conductor/…` | systemd-таймер уровня 0 (по образцу `deploy/r16/`) |
| `skills/conductor/SKILL.md` | разговорный режим |
| `Makefile`, `CLAUDE.md`, `TODO.md` | алиас, строка инструментов, пункт плана |

Все команды ниже — из корня devtools-репо.

---

### Task 1: Каркас пакета и типы

**Files:**
- Create: `conductor/__init__.py`, `conductor/model.py`, `conductor/__main__.py` (заглушка), `tests/conductor/__init__.py`, `tests/conductor/test_model.py`
- Modify: `Makefile` (цель `conductor`, `.PHONY`)

**Interfaces:**
- Produces:
  - `SourceState = Literal["read", "absent", "not_queried", "error"]`
  - `Source(name: str, state: SourceState, detail: str = "", sha: str | None = None)`
  - `NodeKind = Literal["item", "issue", "pr"]`
  - `Node(node_id, kind, repo, title, is_open, closed_as=None, epic=None, owner_ref=None, trigger=None, author=None, updated_at=None, body="", labels=(), url="")`; `closed_as ∈ {"completed","not_planned","merged","unmerged", None}`
  - `EdgeType = Literal["depends_on", "accepted_as", "implements", "mentions"]`
  - `Edge(src: str, dst: str, type: EdgeType, origin: str)`
  - `Finding(code: str, severity: Literal["error","warning","info"], subject: str, detail: str = "")`
  - `issue_id(repo, n) -> "repo#n"`, `pr_id(repo, n) -> "repo!n"`, `item_id(repo, id) -> "todo://repo/id"`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_model.py`:
```python
from conductor.model import Edge, Finding, Node, Source, issue_id, item_id, pr_id


def test_ids_are_canonical() -> None:
    assert item_id("devtools", "conductor") == "todo://devtools/conductor"
    assert issue_id("spec-runner", 603) == "spec-runner#603"
    assert pr_id("devtools", 7) == "devtools!7"


def test_types_are_frozen_and_hashable() -> None:
    node = Node("todo://a/x", "item", "a", "x", is_open=True)
    edge = Edge("todo://a/x", "todo://b/y", "depends_on", "todo")
    assert {node, node} == {node}
    assert {edge} == {Edge("todo://a/x", "todo://b/y", "depends_on", "todo")}
    assert Source("gh", "error", "offline").state == "error"
    assert Finding("GR-CYCLE", "error", "todo://a/x").severity == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor'`

- [ ] **Step 3: Write minimal implementation**

`conductor/__init__.py`:
```python
"""conductor — агент-оркестратор флота (спека 2026-09-29-conductor-design.md)."""

__version__ = "0.1.0"
```

`conductor/model.py`:
```python
"""Неизменяемые типы ядра conductor (спека §3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SourceState = Literal["read", "absent", "not_queried", "error"]
NodeKind = Literal["item", "issue", "pr"]
EdgeType = Literal["depends_on", "accepted_as", "implements", "mentions"]
Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Source:
    """Один источник и честное состояние его чтения (I6)."""

    name: str
    state: SourceState
    detail: str = ""
    sha: str | None = None


@dataclass(frozen=True)
class Node:
    """Пункт TODO, issue или PR."""

    node_id: str
    kind: NodeKind
    repo: str
    title: str
    is_open: bool
    closed_as: str | None = None
    epic: str | None = None
    owner_ref: tuple[tuple[str, Any], ...] | None = None
    trigger: str | None = None
    author: str | None = None
    updated_at: str | None = None
    body: str = ""
    labels: tuple[str, ...] = ()
    url: str = ""

    def owner(self) -> dict[str, Any] | None:
        """owner_ref как словарь (хранится кортежем ради хэшируемости)."""
        return dict(self.owner_ref) if self.owner_ref is not None else None


@dataclass(frozen=True)
class Edge:
    """Типизированное ребро: src ждёт/реализует/упоминает dst (§3.2)."""

    src: str
    dst: str
    type: EdgeType
    origin: str


@dataclass(frozen=True)
class Finding:
    """Находка с кодом из спеки (GR-*, RM-*, …)."""

    code: str
    severity: Severity
    subject: str
    detail: str = ""


def item_id(repo: str, item: str) -> str:
    """todo://<repo>/<id>."""
    return f"todo://{repo}/{item}"


def issue_id(repo: str, number: int) -> str:
    """<repo>#<N>."""
    return f"{repo}#{number}"


def pr_id(repo: str, number: int) -> str:
    """<repo>!<N>."""
    return f"{repo}!{number}"
```

`conductor/__main__.py` (заглушка, заменяется в Task 12):
```python
"""CLI conductor (заменяется в Task 12)."""

import sys

if __name__ == "__main__":
    sys.exit(0)
```

`tests/conductor/__init__.py`: пустой файл.

`Makefile`: добавить после строки `edge-check:`:
```make
conductor: ; @uv run --frozen python -m conductor $(ARGS) --root $(WORKSPACE) --manifest $(MANIFEST)
```
и дописать `conductor` в конец списка `.PHONY`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_model.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor tests/conductor Makefile
git commit -m "feat(conductor): каркас пакета и типы ядра (срез 0)"
```

---

### Task 2: Роадмап — разбор, валидация, классы эпиков

**Files:**
- Create: `conductor/roadmap.py`, `tests/conductor/test_roadmap.py`

**Interfaces:**
- Consumes: `Finding` (Task 1)
- Produces:
  - `Focus(epic: str, rank: int, goal: str | None, autonomy: int, pull_prerequisites: bool)`
  - `Roadmap(autonomy: int, writer_host: str, writer_since: str, focus: tuple[Focus, ...], parked: frozenset[str], limits: dict[str, int], valid: bool, findings: tuple[Finding, ...])`
  - `parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap` — `epics` = `{epic_id: {"status": ...}}`
  - `Roadmap.klass(epic: str | None) -> Literal["focus","parked","background"]`
  - `Roadmap.focus_of(epic: str | None) -> Focus | None`
  - `LIMIT_DEFAULTS: dict[str, tuple[int, int, int]]` — имя → (по умолчанию, min, max)

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_roadmap.py`:
```python
from conductor.roadmap import parse_roadmap

EPICS = {
    "eco.dark-factory": {"status": "active"},
    "eco.governance-plane": {"status": "active"},
    "eco.research-bench": {"status": "active"},
    "eco.cadence": {"status": "paused"},
    "airun.kapelle-m3": {"status": "done"},
}

GOOD = """
schema_version = 1
updated = "2026-09-29"
autonomy = 0
writer_host = "vps-conductor"
writer_since = "2026-09-29T12:00:00Z"

[[focus]]
epic = "eco.dark-factory"
goal = "todo://devtools/bundle-docs-as-oracle"

[[focus]]
epic = "eco.governance-plane"
autonomy = 1
pull_prerequisites = true

[parked]
epics = ["eco.research-bench"]
"""


def test_good_roadmap() -> None:
    rm = parse_roadmap(GOOD, EPICS)
    assert rm.valid and not [f for f in rm.findings if f.severity == "error"]
    assert [f.epic for f in rm.focus] == ["eco.dark-factory", "eco.governance-plane"]
    assert rm.focus[0].rank == 1 and rm.focus[0].autonomy == 0
    assert rm.focus[1].autonomy == 1 and rm.focus[1].pull_prerequisites
    assert rm.klass("eco.dark-factory") == "focus"
    assert rm.klass("eco.research-bench") == "parked"
    assert rm.klass("eco.cadence") == "background"
    assert rm.klass(None) == "background"
    assert rm.limits["max_writes_per_run"] == 20


def _codes(text: str) -> set[str]:
    return {f.code for f in parse_roadmap(text, EPICS).findings}


def test_invalid_cases() -> None:
    base = GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.nope"')
    assert "RM-INVALID" in _codes(base)
    dup = GOOD.replace('"eco.research-bench"', '"eco.dark-factory"')
    assert "RM-INVALID" in _codes(dup)
    done = GOOD.replace('epic = "eco.governance-plane"', 'epic = "airun.kapelle-m3"')
    assert "RM-INVALID" in _codes(done)
    bad_goal = GOOD.replace("todo://devtools/bundle-docs-as-oracle", "devtools#1")
    assert "RM-INVALID" in _codes(bad_goal)
    assert "RM-INVALID" in _codes(GOOD.replace("autonomy = 0", "autonomy = 7"))
    assert "RM-INVALID" in _codes(GOOD + "\n[limits]\nmax_writes_per_run = 0\n")
    assert "RM-INVALID" in _codes(GOOD.replace('"2026-09-29T12:00:00Z"', '"вчера"'))
    assert "RM-INVALID" in _codes("not = [toml")
    assert not parse_roadmap(None, EPICS).valid


def test_paused_focus_is_warning() -> None:
    text = GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.cadence"')
    rm = parse_roadmap(text, EPICS)
    assert rm.valid
    assert "RM-FOCUS-PAUSED" in {f.code for f in rm.findings}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_roadmap.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.roadmap'`

- [ ] **Step 3: Write minimal implementation**

`conductor/roadmap.py`:
```python
"""roadmap.toml: разбор, валидация, классы эпиков (спека §2)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from conductor.model import Finding

GOAL_RE = re.compile(r"^todo://[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,63}$")
LIMIT_DEFAULTS: dict[str, tuple[int, int, int]] = {
    "max_writes_per_run": (20, 1, 200),
    "max_launches_per_run": (2, 0, 10),
    "max_open_prs_per_repo": (2, 0, 10),
    "worker_timeout_min": (45, 5, 180),
    "run_timeout_min": (55, 10, 240),
    "max_model_calls_per_run": (20, 0, 200),
    "max_prerequisite_launches_per_run": (1, 0, 10),
    "stale_after_days": (3, 1, 60),
    "renudge_after_days": (7, 1, 60),
}
Klass = Literal["focus", "parked", "background"]


@dataclass(frozen=True)
class Focus:
    """Фокус роадмапа; rank — позиция, начиная с 1."""

    epic: str
    rank: int
    goal: str | None
    autonomy: int
    pull_prerequisites: bool


@dataclass(frozen=True)
class Roadmap:
    """Разобранный роадмап; valid=False — RM-INVALID (§2.2)."""

    autonomy: int = 0
    writer_host: str = ""
    writer_since: str = ""
    focus: tuple[Focus, ...] = ()
    parked: frozenset[str] = frozenset()
    limits: dict[str, int] = field(default_factory=dict)
    valid: bool = False
    findings: tuple[Finding, ...] = ()

    def focus_of(self, epic: str | None) -> Focus | None:
        """Фокус эпика или None."""
        return next((f for f in self.focus if f.epic == epic), None)

    def klass(self, epic: str | None) -> Klass:
        """Собственный класс узла по его эпику (§2.3)."""
        if self.focus_of(epic) is not None:
            return "focus"
        if epic is not None and epic in self.parked:
            return "parked"
        return "background"


def _invalid(detail: str) -> Finding:
    return Finding("RM-INVALID", "error", "roadmap", detail)


def _int_in(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_utc_instant(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _limits(raw: Any, errors: list[Finding]) -> dict[str, int]:
    table = raw if isinstance(raw, dict) else {}
    limits: dict[str, int] = {}
    for name, (default, low, high) in LIMIT_DEFAULTS.items():
        value = table.get(name, default)
        if not _int_in(value, low, high):
            errors.append(_invalid(f"limits.{name}={value!r} вне {low}..{high}"))
            value = default
        limits[name] = value
    return limits


def _focus(raw: Any, top: int, epics: dict[str, dict], errors: list[Finding],
           warnings: list[Finding]) -> tuple[Focus, ...]:
    result: list[Focus] = []
    for rank, entry in enumerate(raw if isinstance(raw, list) else [], start=1):
        epic = entry.get("epic") if isinstance(entry, dict) else None
        if epic not in epics:
            errors.append(_invalid(f"focus #{rank}: неизвестный эпик {epic!r}"))
            continue
        status = epics[epic].get("status")
        if status == "done":
            errors.append(_invalid(f"focus #{rank}: эпик {epic} в статусе done"))
        if status == "paused":
            warnings.append(Finding("RM-FOCUS-PAUSED", "warning", epic))
        goal = entry.get("goal")
        if goal is not None and not (isinstance(goal, str) and GOAL_RE.match(goal)):
            errors.append(_invalid(f"focus #{rank}: goal {goal!r} не todo://"))
            goal = None
        autonomy = entry.get("autonomy", top)
        if not _int_in(autonomy, 0, 3):
            errors.append(_invalid(f"focus #{rank}: autonomy={autonomy!r}"))
            autonomy = 0
        pull = entry.get("pull_prerequisites", False)
        result.append(Focus(epic, rank, goal, autonomy, pull is True))
    return tuple(result)


def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
    """Разбор и валидация (§2.1–2.2); ошибки не бросаются, а становятся RM-INVALID."""
    if text is None:
        return Roadmap(findings=(_invalid("roadmap.toml не прочитан"),))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return Roadmap(findings=(_invalid(f"TOML: {exc}"),))
    errors: list[Finding] = []
    warnings: list[Finding] = []
    if data.get("schema_version") != 1:
        errors.append(_invalid(f"schema_version={data.get('schema_version')!r}"))
    top = data.get("autonomy", 0)
    if not _int_in(top, 0, 3):
        errors.append(_invalid(f"autonomy={top!r}"))
        top = 0
    writer = data.get("writer_host", "")
    if not isinstance(writer, str) or not writer:
        errors.append(_invalid("writer_host пуст"))
        writer = ""
    since = data.get("writer_since", "")
    if not _is_utc_instant(since):
        errors.append(_invalid(f"writer_since={since!r} не RFC 3339 UTC"))
    focus = _focus(data.get("focus"), top, epics, errors, warnings)
    parked_raw = data.get("parked", {}).get("epics", [])
    parked = frozenset(e for e in parked_raw if isinstance(e, str))
    for epic in parked - set(epics):
        errors.append(_invalid(f"parked: неизвестный эпик {epic!r}"))
    seen = [f.epic for f in focus] + sorted(parked)
    for epic in {e for e in seen if seen.count(e) > 1}:
        errors.append(_invalid(f"эпик {epic} встречается дважды"))
    limits = _limits(data.get("limits"), errors)
    return Roadmap(
        autonomy=top,
        writer_host=writer,
        writer_since=since if isinstance(since, str) else "",
        focus=focus,
        parked=parked,
        limits=limits,
        valid=not errors,
        findings=tuple(errors + warnings),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_roadmap.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/roadmap.py tests/conductor/test_roadmap.py
git commit -m "feat(conductor): разбор и валидация roadmap.toml (§2)"
```

---

### Task 3: `Inputs` — формат входов и replay

**Files:**
- Create: `conductor/inputs.py`, `tests/conductor/test_inputs.py`

**Interfaces:**
- Consumes: `SourceState` (Task 1)
- Produces:
  - `RepoTodo(repo: str, text: str | None, sha: str | None, state: SourceState, detail: str = "")`
  - `GhRecord` — `dict` с ключами: `repo` (канонический), `number: int`, `is_pr: bool`, `title`, `body`, `state` (`"open"|"closed"`), `state_reason` (`"completed"|"not_planned"|None`), `merged: bool`, `author`, `labels: list[str]`, `updated_at`, `url`, `comments: list[{"author","body","created_at"}]`, `closing_refs: list[str]` (`"repo#N"`)
  - `Inputs(captured_at, host, owner, todos: list[RepoTodo], gh_records: list[dict], gh_state: SourceState, gh_detail: str, roadmap_text: str | None, roadmap_source: str, roadmap_sha: str | None, epics: dict[str, dict], movement: dict[str, str], repo_names: dict[str, str])`
  - `save_inputs(inputs: Inputs, path: Path) -> None`, `load_inputs(path: Path) -> Inputs`
  - `INPUTS_VERSION = 1`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_inputs.py`:
```python
from pathlib import Path

import pytest

from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs


def _inputs() -> Inputs:
    return Inputs(
        captured_at="2026-09-29T12:00:00Z",
        host="mac",
        owner="andrei-shtanakov",
        todos=[RepoTodo("devtools", "- [ ] x @owner:TBD @id:x\n", "abc", "read")],
        gh_records=[{"repo": "devtools", "number": 1, "is_pr": False}],
        gh_state="read",
        gh_detail="",
        roadmap_text="schema_version = 1\n",
        roadmap_source="origin",
        roadmap_sha="def",
        epics={"eco.tooling": {"status": "active"}},
        movement={"todo://devtools/x": "2026-09-28T00:00:00Z"},
        repo_names={"prograph-vault": "ecosystem-kb"},
    )


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "inputs.json"
    save_inputs(_inputs(), path)
    assert load_inputs(path) == _inputs()


def test_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "inputs.json"
    path.write_text('{"version": 99}', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_inputs(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_inputs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.inputs'`

- [ ] **Step 3: Write minimal implementation**

`conductor/inputs.py`:
```python
"""Inputs — всё, что ядро знает о мире; тот же JSON служит replay (§10, §11)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from conductor.model import SourceState

INPUTS_VERSION = 1


@dataclass
class RepoTodo:
    """TODO.md одного репо с origin/<default>."""

    repo: str
    text: str | None
    sha: str | None
    state: SourceState
    detail: str = ""


@dataclass
class Inputs:
    """Снимок входов прогона; ядро не делает ввода-вывода сверх этого."""

    captured_at: str
    host: str
    owner: str
    todos: list[RepoTodo]
    gh_records: list[dict[str, Any]]
    gh_state: SourceState
    gh_detail: str
    roadmap_text: str | None
    roadmap_source: str
    roadmap_sha: str | None
    epics: dict[str, dict[str, Any]]
    movement: dict[str, str] = field(default_factory=dict)
    repo_names: dict[str, str] = field(default_factory=dict)


def save_inputs(inputs: Inputs, path: Path) -> None:
    """Записать входы (UTF-8, стабильный порядок ключей)."""
    payload = {"version": INPUTS_VERSION, **asdict(inputs)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def load_inputs(path: Path) -> Inputs:
    """Прочитать входы; чужая версия формата — ValueError."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.pop("version", None) != INPUTS_VERSION:
        raise ValueError(f"inputs version != {INPUTS_VERSION}: {path}")
    data["todos"] = [RepoTodo(**t) for t in data["todos"]]
    return Inputs(**data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_inputs.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/inputs.py tests/conductor/test_inputs.py
git commit -m "feat(conductor): формат входов и replay"
```

---

### Task 4: Манифест и git-источник (TODO с `origin/<default>`)

**Files:**
- Create: `conductor/manifest.py`, `conductor/sources_git.py`, `tests/conductor/test_sources_git.py`

**Interfaces:**
- Consumes: `RepoTodo` (Task 3)
- Produces:
  - `UMBRELLA = "ai-orchestrators-workspace"`
  - `FleetRepo(key: str, git_dir: str, github_name: str)`
  - `fleet_repos(manifest_path: Path) -> list[FleetRepo]` — все не-`member` записи манифеста с `repo_url`, плюс зонтик (`git_dir = github_name = UMBRELLA`), уникальные по `git_dir`
  - `github_owner(manifest_path: Path) -> str` — владелец из `repo_url`
  - `default_ref(repo_dir: Path) -> str | None` — `origin/HEAD` → `origin/master` → `origin/main`
  - `fetch(repo_dir: Path) -> str | None` — `None` при успехе, иначе текст ошибки
  - `read_file_at_origin(repo_dir: Path, path: str) -> RepoTodo`-подобный результат: `(text | None, sha | None, state, detail)`
  - `read_todo(repo: FleetRepo, root: Path, do_fetch: bool) -> RepoTodo`
  - `last_commit_mentioning(repo_dir: Path, ref: str, token: str) -> str | None` — ISO дата последнего коммита с `token` в сообщении

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_sources_git.py`:
```python
import subprocess
from pathlib import Path

from conductor.manifest import UMBRELLA, fleet_repos, github_owner
from conductor.sources_git import (
    default_ref,
    last_commit_mentioning,
    read_file_at_origin,
    read_todo,
)
from conductor.manifest import FleetRepo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo_with_origin(tmp: Path, files: dict[str, str], set_head: bool) -> Path:
    upstream = tmp / "up"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "master")
    _git(upstream, "config", "user.email", "t@t")
    _git(upstream, "config", "user.name", "t")
    for name, text in files.items():
        (upstream / name).write_text(text, encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "--allow-empty", "-m", "init @id:x")
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    if not set_head:
        _git(clone, "remote", "set-head", "origin", "-d")
    return clone


def test_reads_origin_not_worktree(tmp_path: Path) -> None:
    clone = _repo_with_origin(tmp_path, {"TODO.md": "- [ ] a @id:a\n"}, True)
    (clone / "TODO.md").write_text("- [x] a @id:a\n", encoding="utf-8")
    text, sha, state, _ = read_file_at_origin(clone, "TODO.md")
    assert state == "read" and text == "- [ ] a @id:a\n" and sha


def test_origin_head_unset_falls_back_to_master(tmp_path: Path) -> None:
    clone = _repo_with_origin(tmp_path, {"TODO.md": "x\n"}, False)
    assert default_ref(clone) == "origin/master"


def test_missing_todo_is_absent(tmp_path: Path) -> None:
    clone = _repo_with_origin(tmp_path, {}, True)
    _, sha, state, _ = read_file_at_origin(clone, "TODO.md")
    assert state == "absent" and sha


def test_missing_checkout_is_error(tmp_path: Path) -> None:
    todo = read_todo(FleetRepo("x", "x", "x"), tmp_path, do_fetch=False)
    assert todo.state == "error"


def test_last_commit_mentioning(tmp_path: Path) -> None:
    clone = _repo_with_origin(tmp_path, {"a": "1"}, True)
    assert last_commit_mentioning(clone, "origin/master", "@id:x")
    assert last_commit_mentioning(clone, "origin/master", "@id:nope") is None


def test_fleet_repos_adds_umbrella_and_dedups(tmp_path: Path) -> None:
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
        '[cores.a-sdk]\nmember = true\nrepo_url = "git@github.com:own/a.git"\n'
        'git_dir = "a"\n'
        '[tools.ecosystem-kb]\nrepo_url = "git@github.com:own/prograph-vault.git"\n'
        'git_dir = "prograph-vault"\n',
        encoding="utf-8",
    )
    repos = {r.key: r for r in fleet_repos(manifest)}
    assert set(repos) == {"a", "ecosystem-kb", UMBRELLA}
    assert repos["ecosystem-kb"].github_name == "prograph-vault"
    assert github_owner(manifest) == "own"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_sources_git.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.manifest'`

- [ ] **Step 3: Write minimal implementation**

`conductor/manifest.py`:
```python
"""Репо-цели флота из workspace-manifest.toml плюс зонтик (§3.1)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

UMBRELLA = "ai-orchestrators-workspace"
_URL_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$")


@dataclass(frozen=True)
class FleetRepo:
    """Канонический ключ, каталог клона и имя репо на GitHub."""

    key: str
    git_dir: str
    github_name: str


def _entries(manifest_path: Path) -> list[tuple[str, dict]]:
    data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        (key, entry)
        for section in data.values() if isinstance(section, dict)
        for key, entry in section.items() if isinstance(entry, dict)
    ]


def fleet_repos(manifest_path: Path) -> list[FleetRepo]:
    """Не-member записи с repo_url, уникальные по git_dir, + зонтик."""
    repos: dict[str, FleetRepo] = {}
    for key, entry in _entries(manifest_path):
        url, git_dir = entry.get("repo_url"), entry.get("git_dir")
        if not url or not git_dir or entry.get("member"):
            continue
        match = _URL_RE.search(url)
        if match:
            repos.setdefault(git_dir, FleetRepo(key, git_dir, match.group(2)))
    repos.setdefault(UMBRELLA, FleetRepo(UMBRELLA, UMBRELLA, UMBRELLA))
    return sorted(repos.values(), key=lambda r: r.key)


def github_owner(manifest_path: Path) -> str:
    """Владелец флота на GitHub — из repo_url первой записи."""
    for _, entry in _entries(manifest_path):
        match = _URL_RE.search(entry.get("repo_url", ""))
        if match:
            return match.group(1)
    raise ValueError(f"в манифесте нет github repo_url: {manifest_path}")
```

`conductor/sources_git.py`:
```python
"""git-источник: TODO.md и роадмап с origin/<default>, движение по @id (I6)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conductor.inputs import RepoTodo
from conductor.manifest import FleetRepo
from conductor.model import SourceState

GIT_TIMEOUT = 120


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT,
    )


def default_ref(repo_dir: Path) -> str | None:
    """origin/HEAD, иначе origin/master, иначе origin/main."""
    head = _git(repo_dir, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()
    for ref in ("origin/master", "origin/main"):
        if _git(repo_dir, "rev-parse", "-q", "--verify", ref).returncode == 0:
            return ref
    return None


def fetch(repo_dir: Path) -> str | None:
    """git fetch origin; None — успех, иначе текст ошибки."""
    try:
        done = _git(repo_dir, "fetch", "-q", "origin")
    except subprocess.TimeoutExpired:
        return "fetch timeout"
    return None if done.returncode == 0 else (done.stderr.strip() or "fetch failed")


def read_file_at_origin(
    repo_dir: Path, path: str
) -> tuple[str | None, str | None, SourceState, str]:
    """(text, sha, state, detail) файла на origin/<default>."""
    ref = default_ref(repo_dir)
    if ref is None:
        return None, None, "error", "нет origin/<default>"
    sha = _git(repo_dir, "rev-parse", ref).stdout.strip() or None
    shown = _git(repo_dir, "show", f"{ref}:{path}")
    if shown.returncode == 0:
        return shown.stdout, sha, "read", ref
    if _git(repo_dir, "cat-file", "-e", f"{ref}:{path}").returncode != 0:
        return None, sha, "absent", f"{path} нет на {ref}"
    return None, sha, "error", shown.stderr.strip()


def read_todo(repo: FleetRepo, root: Path, do_fetch: bool) -> RepoTodo:
    """TODO.md репо с origin; нет клона/сбой fetch — error."""
    repo_dir = root / repo.git_dir
    if not (repo_dir / ".git").exists():
        return RepoTodo(repo.key, None, None, "error", f"нет клона {repo_dir}")
    if do_fetch and (problem := fetch(repo_dir)) is not None:
        return RepoTodo(repo.key, None, None, "error", problem)
    text, sha, state, detail = read_file_at_origin(repo_dir, "TODO.md")
    return RepoTodo(repo.key, text, sha, state, detail)


def last_commit_mentioning(repo_dir: Path, ref: str, token: str) -> str | None:
    """ISO-дата последнего коммита на ref, где в сообщении есть token."""
    done = _git(repo_dir, "log", "-1", "--format=%cI", "-F", f"--grep={token}", ref)
    return done.stdout.strip() or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_sources_git.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/manifest.py conductor/sources_git.py tests/conductor/test_sources_git.py
git commit -m "feat(conductor): репо-цели и чтение TODO с origin/<default>"
```

---

### Task 5: GitHub-источник — обнаружение и адресное дочитывание

**Files:**
- Create: `conductor/sources_gh.py`, `tests/conductor/test_sources_gh.py`

**Interfaces:**
- Consumes: `GhRecord` shape (Task 3)
- Produces:
  - `SEARCH_LIMIT = 1000`, `CLOSED_WINDOW_DAYS = 30`
  - `Runner = Callable[[list[str]], tuple[int, str, str]]` — (код, stdout, stderr)
  - `run_gh(args: list[str]) -> tuple[int, str, str]` — настоящий вызов `gh`
  - `GhResult(records: list[dict], state: SourceState, detail: str)`
  - `discover(owner: str, since: str, runner: Runner) -> tuple[list[tuple[str, int, bool]], SourceState, str]` — (github_name, number, is_pr)
  - `fetch_record(owner: str, github_name: str, number: int, is_pr: bool, runner: Runner) -> dict | None`
  - `collect_gh(owner: str, since: str, names_to_keys: dict[str, str], extra_refs: Callable[[list[dict]], set[tuple[str, int]]], runner: Runner, max_hops: int = 3) -> GhResult`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_sources_gh.py`:
```python
import json

from conductor.sources_gh import collect_gh, discover


def fake_runner(responses: dict[str, tuple[int, str, str]]):
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        key = " ".join(args)
        for prefix, answer in responses.items():
            if key.startswith(prefix):
                return answer
        return 1, "", f"unexpected: {key}"

    run.calls = calls  # type: ignore[attr-defined]
    return run


SEARCH_OPEN = "search issues --owner own --include-prs --state open"
SEARCH_CLOSED = "search issues --owner own --include-prs --state closed"


def _hits(*items: tuple[str, int, bool]) -> str:
    return json.dumps([
        {"repository": {"name": n}, "number": k, "isPullRequest": p}
        for n, k, p in items
    ])


ISSUE = json.dumps({
    "title": "t", "body": "b", "state": "OPEN", "stateReason": None,
    "author": {"login": "u"}, "labels": [{"name": "inbox"}],
    "updatedAt": "2026-09-28T00:00:00Z", "url": "https://x/1",
})
COMMENTS = json.dumps([[{"user": {"login": "u"}, "body": "c",
                         "created_at": "2026-09-28T00:00:00Z"}]])


def test_offline_is_error() -> None:
    run = fake_runner({SEARCH_OPEN: (1, "", "not logged in")})
    result = collect_gh("own", "2026-08-30", {}, lambda _: set(), run)
    assert result.state == "error" and "not logged in" in result.detail


def test_truncated_search_is_partial() -> None:
    hits = _hits(*[("a", i, False) for i in range(1000)])
    run = fake_runner({SEARCH_OPEN: (0, hits, ""), SEARCH_CLOSED: (0, "[]", "")})
    _, state, _ = discover("own", "2026-08-30", run)
    assert state == "error"


def test_collects_and_follows_refs() -> None:
    run = fake_runner({
        SEARCH_OPEN: (0, _hits(("a", 1, False)), ""),
        SEARCH_CLOSED: (0, "[]", ""),
        "issue view 1 -R own/a": (0, ISSUE, ""),
        "issue view 9 -R own/a": (0, ISSUE, ""),
        "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        "api --paginate --slurp repos/own/a/issues/9/comments": (0, COMMENTS, ""),
    })
    refs = lambda recs: {("a", 9)} if len(recs) == 1 else set()  # noqa: E731
    result = collect_gh("own", "2026-08-30", {"a": "a"}, refs, run)
    assert result.state == "read"
    assert sorted(r["number"] for r in result.records) == [1, 9]
    first = next(r for r in result.records if r["number"] == 1)
    assert first["labels"] == ["inbox"] and first["comments"][0]["body"] == "c"
    assert first["repo"] == "a" and first["state"] == "open"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_sources_gh.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.sources_gh'`

- [ ] **Step 3: Write minimal implementation**

`conductor/sources_gh.py`:
```python
"""GitHub-источник в два шага: обнаружение поиском, адресное дочитывание (§3.1).

Только чтение: ни одна команда здесь не мутирует GitHub (срез 0).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from conductor.model import SourceState

SEARCH_LIMIT = 1000
CLOSED_WINDOW_DAYS = 30
GH_TIMEOUT = 120
Runner = Callable[[list[str]], tuple[int, str, str]]
ISSUE_FIELDS = "title,body,state,stateReason,author,labels,updatedAt,url"
PR_FIELDS = (
    "title,body,state,mergedAt,author,labels,updatedAt,url,closingIssuesReferences"
)


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """Настоящий вызов gh; недоступный бинарь — код 127."""
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


@dataclass
class GhResult:
    """Записи GitHub и честное состояние источника."""

    records: list[dict[str, Any]]
    state: SourceState
    detail: str


def _search(owner: str, state: str, extra: list[str], runner: Runner
            ) -> tuple[list[dict[str, Any]] | None, str]:
    args = ["search", "issues", "--owner", owner, "--include-prs",
            "--state", state, *extra, "--limit", str(SEARCH_LIMIT),
            "--json", "repository,number,isPullRequest"]
    code, out, err = runner(args)
    if code != 0:
        return None, err.strip() or f"gh search exit {code}"
    return json.loads(out or "[]"), ""


def discover(owner: str, since: str, runner: Runner
             ) -> tuple[list[tuple[str, int, bool]], SourceState, str]:
    """Открытые + закрытые с `since` (YYYY-MM-DD); потолок поиска — error."""
    found: list[tuple[str, int, bool]] = []
    for state, extra in (("open", []), ("closed", ["--closed", f">={since}"])):
        hits, problem = _search(owner, state, extra, runner)
        if hits is None:
            return [], "error", problem
        if len(hits) >= SEARCH_LIMIT:
            return [], "error", f"поиск {state} упёрся в потолок {SEARCH_LIMIT}"
        found += [(h["repository"]["name"], h["number"], h["isPullRequest"])
                  for h in hits]
    return found, "read", ""


def _comments(owner: str, name: str, number: int, runner: Runner
              ) -> list[dict[str, str]] | None:
    code, out, _ = runner(["api", "--paginate", "--slurp",
                           f"repos/{owner}/{name}/issues/{number}/comments"])
    if code != 0:
        return None
    pages = json.loads(out or "[]")
    return [{"author": (c.get("user") or {}).get("login", ""),
             "body": c.get("body") or "", "created_at": c.get("created_at", "")}
            for page in pages for c in page]


def fetch_record(owner: str, name: str, number: int, is_pr: bool, runner: Runner
                 ) -> dict[str, Any] | None:
    """Одна запись GhRecord (без поля repo) или None при сбое чтения."""
    kind, fields = ("pr", PR_FIELDS) if is_pr else ("issue", ISSUE_FIELDS)
    code, out, _ = runner([kind, "view", str(number), "-R", f"{owner}/{name}",
                           "--json", fields])
    if code != 0:
        return None
    raw = json.loads(out)
    comments = _comments(owner, name, number, runner)
    if comments is None:
        return None
    reason = (raw.get("stateReason") or "").lower() or None
    return {
        "number": number, "is_pr": is_pr, "title": raw.get("title", ""),
        "body": raw.get("body") or "", "state": raw["state"].lower()
        if raw["state"] in ("OPEN", "CLOSED") else "closed",
        "state_reason": reason, "merged": bool(raw.get("mergedAt")),
        "author": (raw.get("author") or {}).get("login", ""),
        "labels": [lab["name"] for lab in raw.get("labels", [])],
        "updated_at": raw.get("updatedAt", ""), "url": raw.get("url", ""),
        "comments": comments,
        "closing_refs": [
            f"{ref['repository']['name']}#{ref['number']}"
            for ref in raw.get("closingIssuesReferences", []) or []
        ],
    }


def collect_gh(owner: str, since: str, names_to_keys: dict[str, str],
               extra_refs: Callable[[list[dict[str, Any]]], set[tuple[str, int]]],
               runner: Runner, max_hops: int = 3) -> GhResult:
    """Обнаружение + дочитывание ссылок до неподвижной точки (≤ max_hops)."""
    found, state, detail = discover(owner, since, runner)
    if state != "read":
        return GhResult([], state, detail)
    records: dict[tuple[str, int], dict[str, Any]] = {}
    queue = {(name, number): is_pr for name, number, is_pr in found}
    for _ in range(max_hops + 1):
        for (name, number), is_pr in sorted(queue.items()):
            record = fetch_record(owner, name, number, is_pr, runner)
            if record is None and not is_pr:
                record = fetch_record(owner, name, number, True, runner)
            if record is None:
                return GhResult(list(records.values()), "error",
                                f"не дочитан {name}#{number}")
            record["repo"] = names_to_keys.get(name, name)
            records[(name, number)] = record
        key_to_name = {v: k for k, v in names_to_keys.items()}
        wanted = {(key_to_name.get(repo, repo), n)
                  for repo, n in extra_refs(list(records.values()))}
        queue = {ref: False for ref in wanted - set(records)}
        if not queue:
            return GhResult(list(records.values()), "read", "")
    return GhResult(list(records.values()), "error",
                    f"ссылки не сошлись за {max_hops} шага")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_sources_gh.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/sources_gh.py tests/conductor/test_sources_gh.py
git commit -m "feat(conductor): GitHub-источник — обнаружение и дочитывание"
```

---

### Task 6: Граф — узлы и типизированные рёбра

**Files:**
- Create: `conductor/graph.py`, `tests/conductor/fixtures.py`, `tests/conductor/test_graph.py`

**Interfaces:**
- Consumes: `Node`, `Edge`, `Finding`, `Source`, ids (Task 1); `Inputs`, `RepoTodo` (Task 3); `plan_fields.parse_fleet`, `plan_fields.RepoInput`, `plan_fields.manifest_index`
- Produces:
  - `Graph(nodes: dict[str, Node], edges: list[Edge], findings: list[Finding], sources: list[Source], partial: bool)` с методами `out(node_id, type) -> list[Edge]`, `into(node_id, type) -> list[Edge]`
  - `build_graph(inputs: Inputs, index: Any) -> Graph` — `index` — `plan_fields.ManifestIndex`
  - `referenced_issues(records: list[dict], todos: list[RepoTodo], known: set[str]) -> set[tuple[str, int]]` — все `repo#N` из тел, комментариев, `from:` и `@blocked_by:repo#N` (вход `extra_refs` Task 5)
  - `field_value(body: str, name: str) -> str | None` — `slug:`/`from:` без бэктиков и `\r`
- Test fixtures (`tests/conductor/fixtures.py`): `inputs(todos: dict[str, str], records: list[dict] = (), roadmap: str | None = ROADMAP, epics=EPICS, movement=None) -> Inputs`, `record(repo, number, **fields) -> dict`, `index()` — `ManifestIndex` из временного манифеста с репо `a`, `b`, `c`, `devtools`, `spec-runner`, `arbiter`, `deployer`

- [ ] **Step 1: Write the failing test**

`tests/conductor/fixtures.py`:
```python
"""Общие фикстуры флота для тестов conductor."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import plan_fields as pf

from conductor.inputs import Inputs, RepoTodo

REPOS = ("a", "b", "c", "devtools", "spec-runner", "arbiter", "deployer")
EPICS = {
    "eco.focus1": {"status": "active"},
    "eco.focus2": {"status": "active"},
    "eco.parked": {"status": "active"},
    "eco.bg": {"status": "active"},
    "eco.paused": {"status": "paused"},
}
ROADMAP = """
schema_version = 1
updated = "2026-09-29"
autonomy = 0
writer_host = "vps"
writer_since = "2026-09-29T00:00:00Z"
[[focus]]
epic = "eco.focus1"
goal = "todo://a/goal"
[[focus]]
epic = "eco.focus2"
[parked]
epics = ["eco.parked"]
"""


def index() -> Any:
    """ManifestIndex на временном манифесте с REPOS."""
    lines = []
    for repo in REPOS:
        lines += [f"[cores.{repo}]", f'repo_url = "git@github.com:own/{repo}.git"',
                  f'git_dir = "{repo}"']
    path = Path(tempfile.mkdtemp()) / "m.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pf.manifest_index(path)


def record(repo: str, number: int, **fields: Any) -> dict[str, Any]:
    """GhRecord с разумными умолчаниями."""
    base: dict[str, Any] = {
        "repo": repo, "number": number, "is_pr": False, "title": f"{repo}#{number}",
        "body": "", "state": "open", "state_reason": None, "merged": False,
        "author": "own", "labels": [], "updated_at": "2026-09-28T00:00:00Z",
        "url": "", "comments": [], "closing_refs": [],
    }
    base.update(fields)
    return base


def inputs(todos: dict[str, str], records: list[dict[str, Any]] | tuple = (),
           roadmap: str | None = ROADMAP, epics: dict | None = None,
           movement: dict[str, str] | None = None, gh_state: str = "read"
           ) -> Inputs:
    """Inputs из текстов TODO (прочие репо — absent)."""
    todo_list = [
        RepoTodo(repo, todos.get(repo), "sha-" + repo,
                 "read" if repo in todos else "absent")
        for repo in REPOS
    ]
    return Inputs(
        captured_at="2026-09-29T12:00:00Z", host="test", owner="own",
        todos=todo_list, gh_records=list(records), gh_state=gh_state,  # type: ignore[arg-type]
        gh_detail="", roadmap_text=roadmap, roadmap_source="origin",
        roadmap_sha="sha-rm", epics=epics if epics is not None else EPICS,
        movement=movement or {}, repo_names={r: r for r in REPOS},
    )
```

`tests/conductor/test_graph.py`:
```python
from conductor.graph import build_graph, field_value, referenced_issues
from tests.conductor.fixtures import index, inputs, record


def test_todo_edges_and_legacy_issue_blocker() -> None:
    g = build_graph(inputs({
        "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n"
             "- [ ] z @owner:TBD @id:z @blocked_by:spec-runner#603\n",
        "b": "- [x] y @owner:TBD @id:y\n",
    }, [record("spec-runner", 603)]), index())
    deps = {(e.src, e.dst) for e in g.edges if e.type == "depends_on"}
    assert ("todo://a/x", "todo://b/y") in deps
    assert ("todo://a/z", "spec-runner#603") in deps
    assert g.nodes["todo://b/y"].closed_as == "completed"
    assert not g.partial


def test_inbox_glue_and_from_edge_with_backticks_and_crlf() -> None:
    body = "slug: `deploy-action-decision-tool`\r\nfrom: `deployer#need-policy`\r\n"
    g = build_graph(inputs({
        "arbiter": "- [ ] t @owner:TBD @id:deploy-action-decision-tool\n",
        "deployer": "- [ ] n @owner:TBD @id:need-policy\n",
    }, [record("arbiter", 104, body=body, labels=["inbox"])]), index())
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    assert ("arbiter#104", "todo://arbiter/deploy-action-decision-tool",
            "accepted_as") in edges
    assert ("todo://deployer/need-policy", "arbiter#104", "depends_on") in edges


def test_pr_implements_and_mentions() -> None:
    g = build_graph(inputs({"a": "- [ ] x @owner:TBD @id:x\n"}, [
        record("a", 5, is_pr=True, body="делает @id:x", closing_refs=["a#6"]),
        record("a", 6, comments=[{"author": "u", "body": "см. b#7", "created_at": ""}]),
        record("b", 7),
    ]), index())
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    assert ("a!5", "todo://a/x", "implements") in edges
    assert ("a!5", "a#6", "implements") in edges
    assert ("a#6", "b#7", "mentions") in edges


def test_field_value_strips_quotes() -> None:
    assert field_value("from: `deployer`\r\n", "from") == "deployer"
    assert field_value("no fields", "slug") is None


def test_referenced_issues_collects_all_forms() -> None:
    todos = inputs({"a": "- [ ] z @owner:TBD @id:z @blocked_by:b#3\n"}).todos
    recs = [record("a", 1, body="see c#4", comments=[])]
    assert referenced_issues(recs, todos, {"a", "b", "c"}) >= {("b", 3), ("c", 4)}


def test_gh_error_makes_graph_partial() -> None:
    g = build_graph(inputs({"a": "- [ ] x @owner:TBD @id:x\n"}, gh_state="error"),
                    index())
    assert g.partial and any(s.name == "github" and s.state == "error"
                             for s in g.sources)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_graph.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.graph'`

- [ ] **Step 3: Write minimal implementation**

`conductor/graph.py`:
```python
"""Единый граф: узлы TODO/issue/PR и типизированные рёбра (спека §3.1–3.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import plan_fields as pf

from conductor.inputs import Inputs, RepoTodo
from conductor.model import Edge, EdgeType, Finding, Node, Source, issue_id, item_id, pr_id

REF_RE = re.compile(r"(?<![\w/.-])([a-z0-9][a-z0-9-]*)#(\d+)\b")
TODO_REF_RE = re.compile(r"todo://([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9._-]{0,63})")
LEGACY_ISSUE_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)#(\d+)$")
TODO_URI_RE = re.compile(r"^todo://[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,63}$")
PR_ITEM_RE = re.compile(r"@id:([a-z0-9][a-z0-9._-]{0,63})")


@dataclass
class Graph:
    """Граф прогона; partial — хотя бы один источник не прочитан (I6)."""

    nodes: dict[str, Node]
    edges: list[Edge]
    findings: list[Finding] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    partial: bool = False

    def out(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Исходящие рёбра типа kind."""
        return [e for e in self.edges if e.src == node_id and e.type == kind]

    def into(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Входящие рёбра типа kind."""
        return [e for e in self.edges if e.dst == node_id and e.type == kind]


def field_value(body: str, name: str) -> str | None:
    """Значение `name:` в теле; бэктики, кавычки и \\r снимаются."""
    match = re.search(rf"(?im)^\s*{name}:\s*(.+?)\s*$", body.replace("\r", ""))
    return match.group(1).strip("`'\" ") if match else None


def _item_nodes(snapshot: dict[str, Any]) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for raw in snapshot["nodes"]:
        closed = raw["declared_status"] != "open"
        owner = raw.get("owner_ref")
        nodes[raw["node_id"]] = Node(
            raw["node_id"], "item", raw["repo"], raw["title"], is_open=not closed,
            closed_as="completed" if closed else None, epic=raw.get("epic"),
            owner_ref=tuple(sorted(owner.items())) if owner else None,
            trigger=raw.get("trigger"),
        )
    return nodes


def _todo_edges(snapshot: dict[str, Any]) -> list[Edge]:
    """depends_on из references: plan-fields не строит ребро на несуществующий
    пункт (resolved_target = None), а висячее ожидание должно остаться видимым."""
    edges: list[Edge] = []
    for ref in snapshot["references"]:
        if ref["kind"] != "blocked_by":
            continue
        raw = ref.get("raw_ref") or ""
        target = ref.get("resolved_target") or (raw if TODO_URI_RE.match(raw) else None)
        legacy = ref.get("legacy_blocker_ref") or ""
        if target is None and (match := LEGACY_ISSUE_RE.match(legacy)):
            target = issue_id(match.group(1), int(match.group(2)))
        if target is not None:
            edges.append(Edge(ref["source_node_id"], target, "depends_on", "todo"))
    return edges


def _gh_node(rec: dict[str, Any]) -> Node:
    repo, number = rec["repo"], rec["number"]
    if rec["is_pr"]:
        node_id = pr_id(repo, number)
        closed_as = None if rec["state"] == "open" else (
            "merged" if rec["merged"] else "unmerged")
    else:
        node_id = issue_id(repo, number)
        closed_as = None if rec["state"] == "open" else (
            rec.get("state_reason") or "completed")
    return Node(node_id, "pr" if rec["is_pr"] else "issue", repo, rec["title"],
                is_open=rec["state"] == "open", closed_as=closed_as,
                author=rec.get("author"), updated_at=rec.get("updated_at"),
                body=rec.get("body", ""), labels=tuple(rec.get("labels", [])),
                url=rec.get("url", ""))


def _gh_edges(rec: dict[str, Any], nodes: dict[str, Node]) -> list[Edge]:
    me = _gh_node(rec).node_id
    edges: list[Edge] = []
    if rec["is_pr"]:
        edges += [Edge(me, ref, "implements", "pr:closes")
                  for ref in rec.get("closing_refs", [])]
        edges += [Edge(me, item_id(rec["repo"], m), "implements", "pr:@id")
                  for m in PR_ITEM_RE.findall(rec.get("body", ""))]
    if "inbox" in rec.get("labels", []):
        slug = field_value(rec["body"], "slug")
        if slug and item_id(rec["repo"], slug) in nodes:
            edges.append(Edge(me, item_id(rec["repo"], slug), "accepted_as",
                              "inbox:slug"))
        sender = field_value(rec["body"], "from") or ""
        if "#" in sender:
            repo, _, waiting = sender.partition("#")
            if item_id(repo, waiting) in nodes:
                edges.append(Edge(item_id(repo, waiting), me, "depends_on",
                                  "inbox:from"))
    return edges


def _mentions(rec: dict[str, Any], strong: set[tuple[str, str]],
              known: set[str]) -> list[Edge]:
    me = _gh_node(rec).node_id
    text = rec.get("body", "") + "\n".join(c["body"] for c in rec.get("comments", []))
    targets = {issue_id(r, int(n)) for r, n in REF_RE.findall(text) if r in known}
    targets |= {item_id(r, i) for r, i in TODO_REF_RE.findall(text) if r in known}
    return [Edge(me, t, "mentions", "body") for t in sorted(targets)
            if t != me and (me, t) not in strong and (t, me) not in strong]


def referenced_issues(records: list[dict[str, Any]], todos: list[RepoTodo],
                      known: set[str]) -> set[tuple[str, int]]:
    """Все repo#N, на которые ссылаются TODO и тела/комментарии (для дочитывания)."""
    refs: set[tuple[str, int]] = set()
    for todo in todos:
        for repo, number in re.findall(r"@blocked_by:([a-z0-9-]+)#(\d+)",
                                       todo.text or ""):
            refs.add((repo, int(number)))
    for rec in records:
        text = rec.get("body", "") + "\n".join(
            c["body"] for c in rec.get("comments", []))
        refs |= {(r, int(n)) for r, n in REF_RE.findall(text) if r in known}
        refs |= {(r.split("#")[0], int(n)) for r, n in
                 re.findall(r"([a-z0-9-]+)#(\d+)", " ".join(rec.get("closing_refs", [])))}
    return refs


def _sources(inputs: Inputs) -> list[Source]:
    sources = [Source(f"todo:{t.repo}", t.state, t.detail, t.sha)
               for t in inputs.todos]
    sources.append(Source("github", inputs.gh_state, inputs.gh_detail))
    return sources


def build_graph(inputs: Inputs, index: Any) -> Graph:
    """Граф из входов; ядро без ввода-вывода."""
    repo_inputs = [pf.RepoInput(t.repo, todo_text=t.text or "", commit=t.sha,
                                available=t.state in ("read", "absent"))
                   for t in inputs.todos]
    snapshot = pf.parse_fleet(repo_inputs, index)
    nodes = _item_nodes(snapshot)
    edges = _todo_edges(snapshot)
    for rec in inputs.gh_records:
        node = _gh_node(rec)
        nodes[node.node_id] = node
    for rec in inputs.gh_records:
        edges += _gh_edges(rec, nodes)
    strong = {(e.src, e.dst) for e in edges}
    known = {t.repo for t in inputs.todos}
    for rec in inputs.gh_records:
        edges += _mentions(rec, strong, known)
    sources = _sources(inputs)
    partial = any(s.state in ("error", "not_queried") for s in sources)
    return Graph(nodes, sorted(set(edges), key=lambda e: (e.src, e.dst, e.type)),
                 [], sources, partial)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_graph.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/graph.py tests/conductor/fixtures.py tests/conductor/test_graph.py
git commit -m "feat(conductor): единый граф с типизированными рёбрами (§3.1–3.2)"
```

---

### Task 7: Ожидания — состояние предпосылки и вердикты

**Files:**
- Create: `conductor/waits.py`, `tests/conductor/test_waits.py`

**Interfaces:**
- Consumes: `Graph` (Task 6), `Inputs.movement` (Task 3)
- Produces:
  - `prereq_state(node: Node | None) -> Literal["done", "cancelled", "open", "missing"]`
  - `Wait(consumer: str, prereq: str | None, verdict: Literal["satisfied","pending","unknown"], reason: str, evidence: str, since: str | None)`
  - `evaluate_waits(graph: Graph, movement: dict[str, str], now: str, stale_after_days: int) -> list[Wait]` — по одному `Wait` на `depends_on` открытого узла и на `@trigger` открытого пункта
  - `waits_of(waits: list[Wait], consumer: str) -> list[Wait]`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_waits.py`:
```python
from conductor.graph import build_graph
from conductor.waits import evaluate_waits, waits_of
from tests.conductor.fixtures import index, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _waits(todos: dict[str, str], records=(), movement=None):
    g = build_graph(inputs(todos, records, movement=movement), index())
    return evaluate_waits(g, movement or {}, NOW, stale_after_days=3)


def test_done_item_satisfies() -> None:
    w = _waits({"a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                "b": "- [x] y @owner:TBD @id:y\n"})
    assert [(x.verdict, x.reason) for x in waits_of(w, "todo://a/x")] == [
        ("satisfied", "done")]


def test_not_planned_issue_is_cancelled_unknown() -> None:
    w = _waits({"a": "- [ ] x @owner:TBD @id:x @blocked_by:b#3\n"},
               [record("b", 3, state="closed", state_reason="not_planned")])
    assert waits_of(w, "todo://a/x")[0].reason == "cancelled"
    assert waits_of(w, "todo://a/x")[0].verdict == "unknown"


def test_missing_prereq_is_unknown() -> None:
    w = _waits({"a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/nope\n",
                "b": "- [ ] y @owner:TBD @id:y\n"})
    assert waits_of(w, "todo://a/x")[0].reason == "missing"


def test_stale_pending() -> None:
    todos = {"a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
             "b": "- [ ] y @owner:TBD @id:y\n"}
    fresh = _waits(todos, movement={"todo://b/y": "2026-09-28T00:00:00Z"})
    stale = _waits(todos, movement={"todo://b/y": "2026-09-20T00:00:00Z"})
    assert waits_of(fresh, "todo://a/x")[0].reason == "open"
    assert waits_of(stale, "todo://a/x")[0].reason == "stale"


def test_prose_trigger_is_unknown() -> None:
    w = _waits({"a": '- [ ] x @owner:TBD @id:x @trigger:"когда-нибудь"\n'})
    got = waits_of(w, "todo://a/x")
    assert [(x.prereq, x.verdict, x.reason) for x in got] == [
        (None, "unknown", "prose_trigger")]


def test_closed_consumer_has_no_waits() -> None:
    w = _waits({"a": "- [x] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                "b": "- [ ] y @owner:TBD @id:y\n"})
    assert waits_of(w, "todo://a/x") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_waits.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.waits'`

- [ ] **Step 3: Write minimal implementation**

`conductor/waits.py`:
```python
"""Состояние предпосылки и вердикты ожиданий (спека §3.4, срез 0 без модели)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from conductor.graph import Graph
from conductor.model import Node

PrereqState = Literal["done", "cancelled", "open", "missing"]
Verdict = Literal["satisfied", "pending", "unknown"]


@dataclass(frozen=True)
class Wait:
    """Одно ожидание consumer → prereq (prereq=None — условие @trigger)."""

    consumer: str
    prereq: str | None
    verdict: Verdict
    reason: str
    evidence: str
    since: str | None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def prereq_state(node: Node | None) -> PrereqState:
    """Таблица §3.4: выполнено / отменено / открыто / нет узла."""
    if node is None:
        return "missing"
    if node.is_open:
        return "open"
    if node.closed_as in ("completed", "merged"):
        return "done"
    return "cancelled"


def _evidence(node: Node) -> str:
    return node.url or node.node_id


def evaluate_waits(graph: Graph, movement: dict[str, str], now: str,
                   stale_after_days: int) -> list[Wait]:
    """Все ожидания открытых узлов; вердикт вычисляется заново каждым прогоном."""
    waits: list[Wait] = []
    for edge in graph.edges:
        consumer = graph.nodes.get(edge.src)
        if edge.type != "depends_on" or consumer is None or not consumer.is_open:
            continue
        prereq = graph.nodes.get(edge.dst)
        state = prereq_state(prereq)
        last = movement.get(edge.dst) or (prereq.updated_at if prereq else None)
        if state == "done":
            waits.append(Wait(edge.src, edge.dst, "satisfied", "done",
                              _evidence(prereq), last))  # type: ignore[arg-type]
        elif state == "open":
            idle = last is not None and (
                (_ts(now) - _ts(last)).days >= stale_after_days)
            waits.append(Wait(edge.src, edge.dst, "pending",
                              "stale" if idle else "open", edge.dst, last))
        else:
            waits.append(Wait(edge.src, edge.dst, "unknown", state, edge.dst, last))
    for node in graph.nodes.values():
        if node.kind == "item" and node.is_open and node.trigger:
            waits.append(Wait(node.node_id, None, "unknown", "prose_trigger",
                              node.trigger, None))
    return waits


def waits_of(waits: list[Wait], consumer: str) -> list[Wait]:
    """Ожидания одного потребителя."""
    return [w for w in waits if w.consumer == consumer]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_waits.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/waits.py tests/conductor/test_waits.py
git commit -m "feat(conductor): активная проверка ожиданий (§3.4)"
```

---

### Task 8: Анализ — склейка, готовность, циклы, находки, состояние работы

**Files:**
- Create: `conductor/analysis.py`, `tests/conductor/test_analysis.py`

**Interfaces:**
- Consumes: `Graph` (Task 6), `Wait`, `waits_of` (Task 7), `Finding` (Task 1)
- Produces:
  - `canonical_map(graph: Graph) -> dict[str, str]` — issue → пункт по `accepted_as`
  - `dependency_adjacency(graph: Graph, canon: dict[str, str]) -> dict[str, set[str]]` — `depends_on` после склейки
  - `find_cycles(adj: dict[str, set[str]]) -> list[list[str]]` — SCC размера > 1 и петли; итеративно
  - `is_ready(node_id: str, graph: Graph, waits: list[Wait]) -> bool`
  - `work_state(node_id: str, graph: Graph) -> Literal["in_review", "idle"]`
  - `findings(graph: Graph, waits: list[Wait], cycles: list[list[str]]) -> list[Finding]` — `GR-CYCLE`, `GR-SHIPPED-OPEN`, `GR-DANGLING-WAIT`, `GR-WEAK-EDGE`, `GR-SLUG-MATCH`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_analysis.py`:
```python
from conductor.analysis import (
    canonical_map,
    dependency_adjacency,
    find_cycles,
    findings,
    is_ready,
    work_state,
)
from conductor.graph import build_graph
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import index, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _setup(todos, records=()):
    g = build_graph(inputs(todos, records), index())
    return g, evaluate_waits(g, {}, NOW, 3)


def test_chain_only_leaf_ready() -> None:
    g, w = _setup({"a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://b/b\n",
                   "b": "- [ ] b @owner:TBD @id:b @blocked_by:todo://c/c\n",
                   "c": "- [ ] c @owner:TBD @id:c\n"})
    assert [n for n in ("todo://a/a", "todo://b/b", "todo://c/c")
            if is_ready(n, g, w)] == ["todo://c/c"]


def test_ping_pong_through_issue_plane_is_cycle() -> None:
    body = "slug: q\nfrom: spec-runner#verify\n"
    g, w = _setup({
        "devtools": "- [ ] o @owner:TBD @id:oracle @blocked_by:spec-runner#603\n"
                    "- [ ] q @owner:TBD @id:q @blocked_by:todo://spec-runner/verify\n",
        "spec-runner": "- [ ] v @owner:TBD @id:verify @blocked_by:devtools#491\n",
    }, [record("spec-runner", 603, body="slug: verify\nfrom: devtools#oracle\n",
               labels=["inbox"]),
        record("devtools", 491, body=body, labels=["inbox"])])
    cycles = find_cycles(dependency_adjacency(g, canonical_map(g)))
    assert any({"todo://devtools/q", "todo://spec-runner/verify"} <= set(c)
               for c in cycles)
    assert "GR-CYCLE" in {f.code for f in findings(g, w, cycles)}


def test_self_loop_is_cycle_without_recursion() -> None:
    g, _ = _setup({"a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://a/a\n"})
    assert find_cycles(dependency_adjacency(g, {})) == [["todo://a/a"]]
    long = {f"n{i}": {f"n{i + 1}"} for i in range(5000)}
    assert find_cycles(long) == []


def test_shipped_open_and_weak_edge_and_dangling() -> None:
    g, w = _setup({
        "a": "- [x] s @owner:TBD @id:shipped\n"
             "- [ ] d @owner:TBD @id:d @blocked_by:todo://b/nope\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }, [record("a", 1, body="slug: shipped\n", labels=["inbox"]),
        record("a", 2, body="упоминаю b#3"), record("b", 3)])
    codes = {f.code for f in findings(g, w, [])}
    assert {"GR-SHIPPED-OPEN", "GR-WEAK-EDGE", "GR-DANGLING-WAIT"} <= codes


def test_open_pr_puts_item_in_review() -> None:
    g, _ = _setup({"a": "- [ ] x @owner:TBD @id:x\n"},
                  [record("a", 5, is_pr=True, body="@id:x")])
    assert work_state("todo://a/x", g) == "in_review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_analysis.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.analysis'`

- [ ] **Step 3: Write minimal implementation**

`conductor/analysis.py`:
```python
"""Готовность, циклы, находки и состояние работы (спека §3.3, §3.5).

В срезе 0 поколений доставки ещё нет (ветки conductor не создаются), поэтому
состояние работы — только in_review / idle.
"""

from __future__ import annotations

from typing import Literal

from conductor.graph import Graph
from conductor.model import Finding
from conductor.waits import Wait, waits_of


def canonical_map(graph: Graph) -> dict[str, str]:
    """accepted_as: issue и пункт — один узел работы; представитель — пункт."""
    return {e.src: e.dst for e in graph.edges if e.type == "accepted_as"}


def dependency_adjacency(graph: Graph, canon: dict[str, str]) -> dict[str, set[str]]:
    """depends_on после склейки: узел → множество предпосылок."""
    adj: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.type == "depends_on":
            src, dst = canon.get(e.src, e.src), canon.get(e.dst, e.dst)
            adj.setdefault(src, set()).add(dst)
            adj.setdefault(dst, set())
    return adj


def find_cycles(adj: dict[str, set[str]]) -> list[list[str]]:
    """SCC размера > 1 и петли; итеративный Tarjan (без рекурсии)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for root in sorted(adj):
        if root in index:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adj.get(child, ())))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1 or node in adj.get(node, ()):
                    result.append(sorted(component))
    return sorted(result)


def is_ready(node_id: str, graph: Graph, waits: list[Wait]) -> bool:
    """Открыт и все ожидания satisfied (§3.3.1)."""
    node = graph.nodes.get(node_id)
    if node is None or not node.is_open:
        return False
    return all(w.verdict == "satisfied" for w in waits_of(waits, node_id))


def work_state(node_id: str, graph: Graph) -> Literal["in_review", "idle"]:
    """in_review — есть открытый PR, реализующий узел (§3.5)."""
    for e in graph.into(node_id, "implements"):
        pr = graph.nodes.get(e.src)
        if pr is not None and pr.is_open:
            return "in_review"
    return "idle"


def findings(graph: Graph, waits: list[Wait], cycles: list[list[str]]
             ) -> list[Finding]:
    """Находки графа (§3.3.2–3.3.5)."""
    found = [Finding("GR-CYCLE", "error", c[0], " → ".join(c)) for c in cycles]
    for e in graph.edges:
        src, dst = graph.nodes.get(e.src), graph.nodes.get(e.dst)
        if e.type == "accepted_as" and src and dst and src.is_open \
                and dst.closed_as == "completed":
            found.append(Finding("GR-SHIPPED-OPEN", "warning", e.src,
                                 f"{e.dst} выполнен"))
        if e.type == "implements" and src and dst and src.closed_as == "merged" \
                and dst.kind == "issue" and dst.is_open:
            found.append(Finding("GR-SHIPPED-OPEN", "warning", e.dst,
                                 f"{e.src} влит"))
        if e.type == "mentions":
            found.append(Finding("GR-WEAK-EDGE", "info", e.src, e.dst))
    for w in waits:
        if w.reason == "missing" and w.prereq is not None \
                and w.prereq.startswith("todo://"):
            found.append(Finding("GR-DANGLING-WAIT", "warning", w.consumer, w.prereq))
    return sorted(set(found), key=lambda f: (f.code, f.subject, f.detail))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_analysis.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/analysis.py tests/conductor/test_analysis.py
git commit -m "feat(conductor): готовность, циклы и находки графа (§3.3, §3.5)"
```

---

### Task 9: Порядок работ — протекание ранга и `why`

**Files:**
- Create: `conductor/rank.py`, `tests/conductor/test_rank.py`

**Interfaces:**
- Consumes: `Graph` (6), `Wait` (7), `canonical_map`, `dependency_adjacency`, `find_cycles`, `is_ready`, `work_state` (8), `Roadmap` (2)
- Produces:
  - `QueueEntry(node_id: str, rank: int | None, via_focus: str | None, klass: str, on_goal_path: bool, unblocks: int, oldest_wait_days: int, why: str)`
  - `build_queue(graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str) -> list[QueueEntry]` — кандидаты §4.1 в порядке §4.3; при `roadmap.valid = False` ранги `None`
  - `dependents(adj: dict[str, set[str]]) -> dict[str, set[str]]` — транзитивно зависящие

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_rank.py`:
```python
from conductor.graph import build_graph
from conductor.rank import build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, index, inputs

NOW = "2026-09-29T12:00:00Z"


def _queue(todos, roadmap=ROADMAP):
    g = build_graph(inputs(todos), index())
    w = evaluate_waits(g, {}, NOW, 3)
    return build_queue(g, w, parse_roadmap(roadmap, EPICS), NOW)


def test_leaf_of_parked_epic_inherits_rank_1() -> None:
    q = _queue({
        "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
        "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg @blocked_by:todo://c/c\n",
        "c": "- [ ] c @owner:TBD @id:c @epic:eco.parked\n"
             "- [ ] f2 @owner:TBD @id:f2 @epic:eco.focus2\n",
    })
    assert [e.node_id for e in q][:2] == ["todo://c/c", "todo://c/f2"]
    leaf = q[0]
    assert leaf.rank == 1 and leaf.klass == "parked" and leaf.on_goal_path
    assert leaf.unblocks == 2
    assert "todo://a/goal" in leaf.why and "todo://b/b" in leaf.why


def test_unranked_last_and_invalid_roadmap_unranked() -> None:
    todos = {"a": "- [ ] x @owner:TBD @id:x @epic:eco.bg\n"
                  "- [ ] y @owner:TBD @id:y @epic:eco.focus2\n"}
    assert [e.node_id for e in _queue(todos)] == ["todo://a/y", "todo://a/x"]
    assert all(e.rank is None for e in _queue(todos, roadmap="bad = ["))


def test_cycle_members_are_not_candidates() -> None:
    q = _queue({"a": "- [ ] a @owner:TBD @id:a @epic:eco.focus1 "
                     "@blocked_by:todo://a/b\n"
                     "- [ ] b @owner:TBD @id:b @epic:eco.focus1 "
                     "@blocked_by:todo://a/a\n"})
    assert q == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_rank.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.rank'`

- [ ] **Step 3: Write minimal implementation**

`conductor/rank.py`:
```python
"""Протекание приоритета, ключ сортировки, объяснение (спека §4)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from conductor.analysis import (
    canonical_map,
    dependency_adjacency,
    find_cycles,
    is_ready,
    work_state,
)
from conductor.graph import Graph
from conductor.roadmap import Roadmap
from conductor.waits import Wait


@dataclass(frozen=True)
class QueueEntry:
    """Позиция очереди с объяснением."""

    node_id: str
    rank: int | None
    via_focus: str | None
    klass: str
    on_goal_path: bool
    unblocks: int
    oldest_wait_days: int
    why: str


def _reverse(adj: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {n: set() for n in adj}
    for node, prereqs in adj.items():
        for p in prereqs:
            rev.setdefault(p, set()).add(node)
    return rev


def _walk(start: str, rev: dict[str, set[str]]) -> dict[str, str | None]:
    """BFS по зависящим: узел → родитель (для пути why)."""
    parent: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for up in sorted(rev.get(node, ())):
            if up not in parent:
                parent[up] = node
                queue.append(up)
    return parent


def dependents(adj: dict[str, set[str]]) -> dict[str, set[str]]:
    """Транзитивно зависящие от каждого узла."""
    rev = _reverse(adj)
    return {n: set(_walk(n, rev)) - {n} for n in rev}


def _path(parent: dict[str, str | None], end: str) -> list[str]:
    path = [end]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])  # type: ignore[arg-type]
    return list(reversed(path))


def _days(now: str, since: str | None) -> int:
    if since is None:
        return 0
    delta = datetime.fromisoformat(now.replace("Z", "+00:00")) - \
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    return max(delta.days, 0)


def _entry(node_id: str, graph: Graph, waits: list[Wait], roadmap: Roadmap,
           rev: dict[str, set[str]], now: str) -> QueueEntry:
    parent = _walk(node_id, rev)
    best: tuple[int, str] | None = None
    for other in parent:
        epic = graph.nodes[other].epic if other in graph.nodes else None
        focus = roadmap.focus_of(epic) if roadmap.valid else None
        if focus is not None and (best is None or focus.rank < best[0]):
            best = (focus.rank, other)
    node = graph.nodes[node_id]
    klass = roadmap.klass(node.epic)
    held = [w for w in waits if w.prereq == node_id]
    oldest = max((_days(now, w.since) for w in held), default=0)
    if best is None:
        return QueueEntry(node_id, None, None, klass, False, len(parent) - 1,
                          oldest, "без ранга: не связан с фокусом")
    rank, source = best
    focus = roadmap.focus[rank - 1]
    chain = " → ".join(reversed(_path(parent, source)))
    on_goal = focus.goal is not None and focus.goal in parent
    why = f"rank {rank} ({focus.epic}) via {chain}; unblocks {len(parent) - 1}"
    return QueueEntry(node_id, rank, focus.epic, klass, on_goal,
                      len(parent) - 1, oldest, why)


def build_queue(graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str
                ) -> list[QueueEntry]:
    """Кандидаты §4.1 в порядке §4.3."""
    canon = canonical_map(graph)
    adj = dependency_adjacency(graph, canon)
    in_cycle = {n for c in find_cycles(adj) for n in c}
    rev = _reverse(adj)
    entries = []
    for node_id, node in graph.nodes.items():
        if node_id in canon or node_id in in_cycle:
            continue
        if node.kind == "item" and (not is_ready(node_id, graph, waits)
                                    or work_state(node_id, graph) != "idle"):
            continue
        if node.kind != "item" and not node.is_open:
            continue
        entries.append(_entry(node_id, graph, waits, roadmap, rev, now))
    return sorted(entries, key=lambda e: (
        e.rank is None, e.rank or 0, not e.on_goal_path, -e.unblocks,
        -e.oldest_wait_days, e.node_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_rank.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/rank.py tests/conductor/test_rank.py
git commit -m "feat(conductor): протекание приоритета и why (§4)"
```

---

### Task 10: Политика как выдача — уровень, `actor/need`, делегируемость, действие

**Files:**
- Create: `conductor/policy.py`, `tests/conductor/test_policy.py`

**Interfaces:**
- Consumes: `QueueEntry` (9), `Graph` (6), `Wait`, `waits_of` (7), `Roadmap`, `Focus` (2), `work_state` (8)
- Produces:
  - `DECISION_WORDS: tuple[str, ...]`, `DECISION_PATH_PREFIXES: tuple[str, ...]`
  - `Assessment(node_id: str, level: int, actor: str, need: str, delegable: bool, block_reason: str | None, action: str)`
  - `position_level(entry: QueueEntry, roadmap: Roadmap, run_level: int) -> int` — §2.4
  - `delegable(node: Node, fleet_owner: str, epic_status: str | None) -> tuple[bool, str | None]` — §5.2 (класс «решение человека» по словам; путь authority-root — вне среза 0, контекст-пак подключается в срезе 3)
  - `assess(entry: QueueEntry, graph: Graph, waits: list[Wait], roadmap: Roadmap, run_level: int, fleet_owner: str, epics: dict[str, dict]) -> Assessment` — §5.2–5.3; `action` — имя действия §5.1 или `"—"`; в срезе 0 **только вычисляется**

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_policy.py`:
```python
from conductor.graph import build_graph
from conductor.policy import assess, delegable, position_level
from conductor.rank import build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, index, inputs, record

NOW = "2026-09-29T12:00:00Z"
R3 = ROADMAP.replace("autonomy = 0", "autonomy = 3")


def _assess(todos, records=(), roadmap=R3, level=3):
    g = build_graph(inputs(todos, records), index())
    w = evaluate_waits(g, {}, NOW, 3)
    rm = parse_roadmap(roadmap, EPICS)
    return {e.node_id: assess(e, g, w, rm, level, "own", EPICS)
            for e in build_queue(g, w, rm, NOW)}


def test_focus_item_delegable_launches_at_level_3() -> None:
    a = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})
    got = a["todo://a/x"]
    assert (got.level, got.need, got.action) == (3, "implement", "launch")


def test_same_item_at_level_0_is_output_only() -> None:
    a = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"},
                level=0)
    assert a["todo://a/x"].action == "—"


def test_tbd_owner_and_signoff_word_go_to_owner() -> None:
    a = _assess({"a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
                      "- [ ] подпись формы @owner:github:own @id:y @epic:eco.focus1\n"})
    assert a["todo://a/x"].need == "decide"
    assert a["todo://a/y"].block_reason == "decision-signal"


def test_adr_mention_is_not_a_decision() -> None:
    ok, why = delegable(
        build_graph(inputs({"a": "- [ ] исправить тест по ADR-ECO-006 "
                                 "@owner:github:own @id:x\n"}), index()
                    ).nodes["todo://a/x"], "own", "active")
    assert ok and why is None


def test_parked_leaf_never_launches_without_permit() -> None:
    a = _assess({
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
             "@blocked_by:todo://c/c\n",
        "c": "- [ ] c @owner:github:own @id:c @epic:eco.parked\n",
    })
    got = a["todo://c/c"]
    assert got.level == 2 and got.action != "launch"


def test_background_prereq_with_pull_prerequisites_launches() -> None:
    rm = R3.replace('epic = "eco.focus1"', 'epic = "eco.focus1"\n'
                    "pull_prerequisites = true")
    a = _assess({
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
             "@blocked_by:todo://c/c\n",
        "c": "- [ ] c @owner:github:own @id:c @epic:eco.bg\n",
    }, roadmap=rm)
    assert a["todo://c/c"].action == "launch"


def test_green_approved_pr_is_output_not_merge() -> None:
    a = _assess({}, [record("a", 5, is_pr=True)])
    assert a["a!5"].action in ("—", "pr_nudge")


def test_position_level_formula() -> None:
    rm = parse_roadmap(R3, EPICS)
    g = build_graph(inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.bg\n"}),
                    index())
    w = evaluate_waits(g, {}, NOW, 3)
    entry = build_queue(g, w, rm, NOW)[0]
    assert position_level(entry, rm, run_level=3) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.policy'`

- [ ] **Step 3: Write minimal implementation**

`conductor/policy.py`:
```python
"""Уровень позиции, actor/need, делегируемость, выбор действия (§2.4, §5.2–5.3).

В срезе 0 результат только показывается: ни одно действие не исполняется.
Путь authority-root из контекст-пака (§5.2 п.2) подключается в срезе 3, где
делегируемость впервые исполняется.
"""

from __future__ import annotations

from dataclasses import dataclass

from conductor.analysis import work_state
from conductor.graph import Graph
from conductor.model import Node
from conductor.rank import QueueEntry
from conductor.roadmap import Roadmap
from conductor.waits import Wait, waits_of

DECISION_WORDS = (
    "sign-off", "подпис", "approve", "одобр", "approval-policy", "утверд",
    "новая версия контракта", "new contract version",
)
DECISION_PATH_PREFIXES = ("prograph-vault/authored/decisions/", ".github/")
OUT_OF_LOOP_REPOS = frozenset({"sdd-framework"})


@dataclass(frozen=True)
class Assessment:
    """Что conductor сделал бы с позицией на уровне level."""

    node_id: str
    level: int
    actor: str
    need: str
    delegable: bool
    block_reason: str | None
    action: str


def position_level(entry: QueueEntry, roadmap: Roadmap, run_level: int) -> int:
    """§2.4: уровень позиции по собственному классу и унаследованному рангу."""
    focus = roadmap.focus_of(entry.via_focus)
    if entry.klass == "focus" and focus is not None:
        return min(run_level, focus.autonomy)
    if entry.klass == "background" and focus is not None:
        cap = focus.autonomy if focus.pull_prerequisites else min(focus.autonomy, 2)
        return min(run_level, cap)
    if entry.klass == "parked" and focus is not None:
        return min(run_level, focus.autonomy, 2)
    if entry.klass == "background":
        return min(run_level, 1)
    return 0


def delegable(node: Node, fleet_owner: str, epic_status: str | None
              ) -> tuple[bool, str | None]:
    """§5.2: owner_ref, признак решения, статус эпика."""
    owner = node.owner()
    if owner is None or owner["kind"] == "tbd":
        return False, "owner-tbd"
    if owner["kind"] == "github_team" or (
            owner["kind"] == "github_user" and owner["id"] != fleet_owner):
        return False, "foreign-owner"
    text = f"{node.title}\n{node.body}".lower()
    if any(word in text for word in DECISION_WORDS) or any(
            prefix in text for prefix in DECISION_PATH_PREFIXES):
        return False, "decision-signal"
    if epic_status is not None and epic_status != "active":
        return False, "epic-not-active"
    return True, None


def _need(entry: QueueEntry, graph: Graph, waits: list[Wait]) -> tuple[str, str]:
    node = graph.nodes[entry.node_id]
    if any(w.verdict == "unknown" for w in waits_of(waits, node.node_id)):
        return "decide", "owner"
    if node.kind == "pr":
        return "review", "review-loop"
    if node.kind == "issue":
        if "inbox" in node.labels:
            return "intake", "conductor"
        return "triage", "owner"
    return "implement", node.repo


def _action(need: str, level: int, entry: QueueEntry, roadmap: Roadmap,
            ok: bool) -> str:
    if level == 0:
        return "—"
    if need in ("decide", "triage"):
        return "owner_queue"
    if need == "intake":
        return "request_intake" if level >= 2 else "owner_queue"
    if need == "review":
        return "—"
    if need == "implement" and level >= 3 and ok:
        if entry.klass == "focus":
            return "launch"
        focus = roadmap.focus_of(entry.via_focus)
        if entry.klass == "background" and focus is not None \
                and focus.pull_prerequisites:
            return "launch"
    return "nudge" if entry.oldest_wait_days >= roadmap.limits.get(
        "stale_after_days", 3) else "—"


def assess(entry: QueueEntry, graph: Graph, waits: list[Wait], roadmap: Roadmap,
           run_level: int, fleet_owner: str, epics: dict[str, dict]
           ) -> Assessment:
    """Позиция очереди → уровень, actor/need, делегируемость, действие."""
    node = graph.nodes[entry.node_id]
    level = position_level(entry, roadmap, run_level)
    need, actor = _need(entry, graph, waits)
    ok, reason = True, None
    if need == "implement":
        status = epics.get(node.epic or "", {}).get("status")
        ok, reason = delegable(node, fleet_owner, status)
        if node.repo in OUT_OF_LOOP_REPOS:
            ok, reason = False, "out-of-loop"
        if not ok:
            need, actor = "decide", "owner"
        if work_state(node.node_id, graph) != "idle":
            need = "review"
    return Assessment(entry.node_id, level, actor, need, ok, reason,
                      _action(need, level, entry, roadmap, ok))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_policy.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/policy.py tests/conductor/test_policy.py
git commit -m "feat(conductor): политика как выдача — уровень, actor/need, действие"
```

---

### Task 11: Снимок, схема и показатели

**Files:**
- Create: `conductor/snapshot.py`, `contracts/conductor-snapshot/v1/schema.json`, `tests/conductor/test_snapshot.py`

**Interfaces:**
- Consumes: всё ядро (Tasks 2, 6–10), `Inputs` (3)
- Produces:
  - `Result(graph, waits, queue, assessments, findings, roadmap, cycles, run_level, graph_state)` — dataclass
  - `evaluate(inputs: Inputs, index: Any, run_level: int) -> Result` — полный детерминированный конвейер
  - `question_id(kind: str, subject: str, evidence: str, options: tuple[str, ...]) -> str` — §5.7
  - `to_snapshot(result: Result, inputs: Inputs, run_id: str, previous: dict | None) -> dict` — контракт v1
  - `SCHEMA_PATH: Path`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_snapshot.py`:
```python
import json

import jsonschema

from conductor.snapshot import SCHEMA_PATH, evaluate, question_id, to_snapshot
from tests.conductor.fixtures import index, inputs


def _snap(todos, gh_state="read", roadmap=None):
    kwargs = {} if roadmap is None else {"roadmap": roadmap}
    inp = inputs(todos, gh_state=gh_state, **kwargs)
    return to_snapshot(evaluate(inp, index(), 0), inp, "run-1", None)


def test_snapshot_matches_schema() -> None:
    snap = _snap({"a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"})
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(snap, schema)
    assert snap["graph_state"] == "complete" and snap["run_level"] == 0
    assert snap["metrics"]["owner_questions"] == 1


def test_partial_and_invalid_roadmap() -> None:
    snap = _snap({"a": "- [ ] x @owner:TBD @id:x\n"}, gh_state="error",
                 roadmap="nope = [")
    assert snap["graph_state"] == "partial"
    assert "RM-INVALID" in {f["code"] for f in snap["findings"]}
    assert all(q["rank"] is None for q in snap["queue"])


def test_question_id_depends_on_options_order() -> None:
    a = question_id("launch", "todo://a/x", "ev", ("retry", "keep"))
    b = question_id("launch", "todo://a/x", "ev", ("keep", "retry"))
    assert a != b and len(a) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.snapshot'`

- [ ] **Step 3: Write minimal implementation**

`contracts/conductor-snapshot/v1/schema.json`:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "conductor-snapshot/v1",
  "type": "object",
  "required": ["contract", "host", "run_id", "started_at", "writer", "roadmap",
               "run_level", "sources", "graph_state", "nodes", "edges", "waits",
               "queue", "cycles", "findings", "actions", "owner_questions",
               "metrics", "changes_since_previous"],
  "properties": {
    "contract": {"const": "conductor-snapshot/v1"},
    "host": {"type": "string"},
    "run_id": {"type": "string"},
    "started_at": {"type": "string"},
    "writer": {"type": "object", "required": ["is_writer", "reason"]},
    "roadmap": {"type": "object", "required": ["source", "sha", "valid"]},
    "run_level": {"type": "integer", "minimum": 0, "maximum": 3},
    "sources": {"type": "array", "items": {"type": "object",
      "required": ["name", "state"],
      "properties": {"state": {"enum": ["read", "absent", "not_queried", "error"]}}}},
    "graph_state": {"enum": ["complete", "partial"]},
    "nodes": {"type": "array"},
    "edges": {"type": "array", "items": {"type": "object",
      "required": ["src", "dst", "type"],
      "properties": {"type": {"enum": ["depends_on", "accepted_as", "implements",
                                        "mentions"]}}}},
    "waits": {"type": "array", "items": {"type": "object",
      "required": ["consumer", "prereq", "verdict", "reason", "evidence"],
      "properties": {"verdict": {"enum": ["satisfied", "pending", "unknown"]}}}},
    "queue": {"type": "array", "items": {"type": "object",
      "required": ["node_id", "rank", "why", "actor", "need", "level", "action"]}},
    "cycles": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
    "findings": {"type": "array", "items": {"type": "object",
      "required": ["code", "severity", "subject"]}},
    "actions": {"type": "object", "required": ["plan", "journal"]},
    "owner_questions": {"type": "array", "items": {"type": "object",
      "required": ["question_id", "subject", "question", "options", "default"]}},
    "metrics": {"type": "object", "required": [
      "waits_satisfied", "waits_pending", "waits_unknown", "actions_available",
      "actions_executed", "owner_questions", "owner_questions_by_reason",
      "partial"]},
    "changes_since_previous": {"type": "object"}
  }
}
```

`conductor/snapshot.py`:
```python
"""Конвейер ядра и снимок conductor-snapshot/v1 (спека §7.1, §10)."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from conductor.analysis import canonical_map, dependency_adjacency, find_cycles
from conductor.analysis import findings as graph_findings
from conductor.graph import Graph, build_graph
from conductor.inputs import Inputs
from conductor.model import Finding
from conductor.policy import Assessment, assess
from conductor.rank import QueueEntry, build_queue
from conductor.roadmap import Roadmap, parse_roadmap
from conductor.waits import Wait, evaluate_waits

SCHEMA_PATH = (Path(__file__).resolve().parents[1]
               / "contracts" / "conductor-snapshot" / "v1" / "schema.json")
OWNER_OPTIONS = ("delegate", "keep")


@dataclass
class Result:
    """Всё, что вычислило ядро за прогон."""

    graph: Graph
    waits: list[Wait]
    queue: list[QueueEntry]
    assessments: list[Assessment]
    findings: list[Finding]
    roadmap: Roadmap
    cycles: list[list[str]]
    run_level: int
    graph_state: str


def evaluate(inputs: Inputs, index: Any, run_level: int) -> Result:
    """Детерминированный конвейер: граф → ожидания → очередь → политика."""
    roadmap = parse_roadmap(inputs.roadmap_text, inputs.epics)
    graph = build_graph(inputs, index)
    stale = roadmap.limits.get("stale_after_days", 3)
    waits = evaluate_waits(graph, inputs.movement, inputs.captured_at, stale)
    cycles = find_cycles(dependency_adjacency(graph, canonical_map(graph)))
    level = 0 if graph.partial or not roadmap.valid else run_level
    queue = build_queue(graph, waits, roadmap, inputs.captured_at)
    assessments = [assess(e, graph, waits, roadmap, level, inputs.owner,
                          inputs.epics) for e in queue]
    found = list(roadmap.findings) + graph_findings(graph, waits, cycles)
    return Result(graph, waits, queue, assessments, found, roadmap, cycles, level,
                  "partial" if graph.partial else "complete")


def question_id(kind: str, subject: str, evidence: str,
                options: tuple[str, ...]) -> str:
    """§5.7: sha256(kind, subject, evidence, варианты по порядку)[:8]."""
    raw = "\x1f".join((kind, subject, evidence, *options)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def _questions(result: Result) -> list[dict[str, Any]]:
    out = []
    for a in result.assessments:
        if a.need != "decide":
            continue
        reason = a.block_reason or "unknown-wait"
        out.append({
            "question_id": question_id("decide", a.node_id, reason, OWNER_OPTIONS),
            "subject": a.node_id, "reason": reason,
            "question": f"{a.node_id}: {reason} — делегировать агенту?",
            "options": list(OWNER_OPTIONS), "default": "keep",
        })
    return out


def _metrics(result: Result, questions: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(w.verdict for w in result.waits)
    return {
        "waits_satisfied": verdicts["satisfied"],
        "waits_pending": verdicts["pending"],
        "waits_unknown": verdicts["unknown"],
        "actions_available": sum(a.action != "—" for a in result.assessments),
        "actions_executed": 0,
        "owner_questions": len(questions),
        "owner_questions_by_reason": dict(Counter(q["reason"] for q in questions)),
        "partial": result.graph_state == "partial",
    }


def _changes(result: Result, previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {"first_run": True}
    before = {(w["consumer"], w["prereq"]): w["verdict"] for w in previous["waits"]}
    newly = [f"{w.consumer} ← {w.prereq}" for w in result.waits
             if w.verdict == "satisfied"
             and before.get((w.consumer, w.prereq)) not in (None, "satisfied")]
    return {"first_run": False, "newly_satisfied": newly}


def to_snapshot(result: Result, inputs: Inputs, run_id: str,
                previous: dict[str, Any] | None) -> dict[str, Any]:
    """Снимок по контракту conductor-snapshot/v1."""
    by_node = {a.node_id: a for a in result.assessments}
    questions = _questions(result)
    return {
        "contract": "conductor-snapshot/v1",
        "host": inputs.host, "run_id": run_id, "started_at": inputs.captured_at,
        "writer": {"is_writer": False, "reason": "срез 0: записей нет"},
        "roadmap": {"source": inputs.roadmap_source, "sha": inputs.roadmap_sha,
                    "valid": result.roadmap.valid},
        "run_level": result.run_level,
        "sources": [asdict(s) for s in result.graph.sources],
        "graph_state": result.graph_state,
        "nodes": [asdict(n) for n in result.graph.nodes.values()],
        "edges": [asdict(e) for e in result.graph.edges],
        "waits": [asdict(w) for w in result.waits],
        "queue": [{**asdict(e), "actor": by_node[e.node_id].actor,
                   "need": by_node[e.node_id].need,
                   "level": by_node[e.node_id].level,
                   "action": by_node[e.node_id].action,
                   "block_reason": by_node[e.node_id].block_reason}
                  for e in result.queue],
        "cycles": result.cycles,
        "findings": [asdict(f) for f in result.findings],
        "actions": {"plan": [
            {"node_id": a.node_id, "action": a.action}
            for a in result.assessments if a.action != "—"], "journal": []},
        "owner_questions": questions,
        "metrics": _metrics(result, questions),
        "changes_since_previous": _changes(result, previous),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_snapshot.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/snapshot.py contracts/conductor-snapshot tests/conductor/test_snapshot.py
git commit -m "feat(conductor): снимок conductor-snapshot/v1 и показатели (§7.1, §10)"
```

---

### Task 12: Сбор входов, рендер и CLI

**Files:**
- Create: `conductor/collect.py`, `conductor/render.py`, `tests/conductor/test_cli.py`
- Modify: `conductor/__main__.py` (заменить заглушку целиком)

**Interfaces:**
- Consumes: Tasks 2–11
- Produces:
  - `collect(root: Path, manifest: Path, roadmap_path: Path | None, do_fetch: bool, runner: Runner, host: str, now: str) -> Inputs`
  - `render_status(result: Result, top: int = 15) -> str`, `render_why(result: Result, node_id: str) -> str`, `render_plan(result: Result) -> str`
  - `main(argv: list[str] | None = None) -> int` — подкоманды `status | why <node> | plan [--level N] | run | record <dir>`; общие флаги `--root`, `--manifest`, `--roadmap`, `--replay <inputs.json>`, `--no-fetch`, `--out` (по умолчанию `out/conductor`), `--selftest`; коды выхода по §7.2

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_cli.py`:
```python
import json
from pathlib import Path

from conductor.__main__ import main
from conductor.inputs import save_inputs
from tests.conductor.fixtures import inputs


def _replay(tmp: Path, todos, **kw) -> Path:
    path = tmp / "inputs.json"
    save_inputs(inputs(todos, **kw), path)
    return path


def _manifest(tmp: Path) -> Path:
    lines = []
    for repo in ("a", "b", "c", "devtools", "spec-runner", "arbiter", "deployer"):
        lines += [f"[cores.{repo}]", f'repo_url = "git@github.com:own/{repo}.git"',
                  f'git_dir = "{repo}"']
    path = tmp / "m.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
              "@blocked_by:todo://b/b\n",
         "b": "- [ ] b @owner:github:own @id:b @epic:eco.bg\n"}


def test_status_why_plan_from_replay(tmp_path: Path, capsys) -> None:
    rep, man = _replay(tmp_path, TODOS), _manifest(tmp_path)
    common = ["--replay", str(rep), "--manifest", str(man)]
    assert main(["status", *common]) == 0
    assert "todo://b/b" in capsys.readouterr().out
    assert main(["why", "todo://b/b", *common]) == 0
    assert "todo://a/goal" in capsys.readouterr().out
    assert main(["plan", "--level", "3", *common]) == 0
    assert "уровень 3" in capsys.readouterr().out


def test_run_writes_snapshot_and_inputs(tmp_path: Path) -> None:
    rep, man = _replay(tmp_path, TODOS), _manifest(tmp_path)
    out = tmp_path / "out"
    assert main(["run", "--replay", str(rep), "--manifest", str(man),
                 "--out", str(out), "--level", "3"]) == 0
    run_dir = next(out.iterdir())
    snap = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["run_level"] == 0 and snap["actions"]["journal"] == []
    assert (run_dir / "inputs.json").is_file()


def test_invalid_roadmap_exit_4_with_snapshot(tmp_path: Path) -> None:
    rep, man = _replay(tmp_path, TODOS, roadmap="x = ["), _manifest(tmp_path)
    out = tmp_path / "out"
    assert main(["run", "--replay", str(rep), "--manifest", str(man),
                 "--out", str(out)]) == 4
    assert (next(out.iterdir()) / "snapshot.json").is_file()


def test_gh_error_still_exit_0(tmp_path: Path, capsys) -> None:
    rep, man = _replay(tmp_path, TODOS, gh_state="error"), _manifest(tmp_path)
    assert main(["status", "--replay", str(rep), "--manifest", str(man)]) == 0
    assert "partial" in capsys.readouterr().out


def test_bad_args_exit_2() -> None:
    assert main(["frobnicate"]) == 2


def test_selftest() -> None:
    assert main(["--selftest"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_cli.py -q`
Expected: FAIL — `ImportError: cannot import name 'main' from 'conductor.__main__'`

- [ ] **Step 3: Write minimal implementation**

`conductor/collect.py`:
```python
"""Сбор Inputs из git и GitHub (единственное место ввода-вывода чтения)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import plan_fields as pf

from conductor.graph import referenced_issues
from conductor.inputs import Inputs, RepoTodo
from conductor.manifest import UMBRELLA, fleet_repos, github_owner
from conductor.sources_gh import CLOSED_WINDOW_DAYS, Runner, collect_gh
from conductor.sources_git import (
    default_ref,
    last_commit_mentioning,
    read_file_at_origin,
    read_todo,
)

ID_RE = re.compile(r"@id:([a-z0-9][a-z0-9._-]{0,63})")


def _epics(root: Path) -> dict[str, dict]:
    registry = pf.load_registry(root / UMBRELLA / "epics.toml")
    return {k: dict(v) for k, v in registry.epics.items()}


def _movement(root: Path, repos: dict[str, str],
              todos: list[RepoTodo]) -> dict[str, str]:
    """Последний коммит с `@id:<id>` в сообщении — движение по пункту (§3.4)."""
    moved: dict[str, str] = {}
    for todo in todos:
        if todo.state != "read" or todo.text is None:
            continue
        repo_dir = root / repos[todo.repo]
        ref = default_ref(repo_dir)
        if ref is None:
            continue
        for token in sorted(set(ID_RE.findall(todo.text))):
            when = last_commit_mentioning(repo_dir, ref, f"@id:{token}")
            if when:
                moved[f"todo://{todo.repo}/{token}"] = when
    return moved


def collect(root: Path, manifest: Path, roadmap_path: Path | None, do_fetch: bool,
            runner: Runner, host: str, now: str) -> Inputs:
    """Прочитать флот; сбои становятся состояниями источников, не исключениями."""
    repos = fleet_repos(manifest)
    todos = [read_todo(r, root, do_fetch) for r in repos]
    names = {r.github_name: r.key for r in repos}
    owner = github_owner(manifest)
    since = (datetime.fromisoformat(now.replace("Z", "+00:00"))
             - timedelta(days=CLOSED_WINDOW_DAYS)).date().isoformat()
    known = {r.key for r in repos}
    gh = collect_gh(owner, since, names,
                    lambda recs: referenced_issues(recs, todos, known), runner)
    if roadmap_path is not None:
        rm_text: str | None = roadmap_path.read_text(encoding="utf-8")
        rm_sha, rm_source = None, str(roadmap_path)
    else:
        rm_text, rm_sha, _, _ = read_file_at_origin(root / UMBRELLA, "roadmap.toml")
        rm_source = "origin"
    return Inputs(
        captured_at=now, host=host, owner=owner, todos=todos,
        gh_records=gh.records, gh_state=gh.state, gh_detail=gh.detail,
        roadmap_text=rm_text, roadmap_source=rm_source, roadmap_sha=rm_sha,
        epics=_epics(root),
        movement=_movement(root, {r.key: r.git_dir for r in repos}, todos),
        repo_names=names,
    )
```

`conductor/render.py`:
```python
"""Текст для status / why / plan."""

from __future__ import annotations

from conductor.snapshot import Result


def _header(result: Result) -> list[str]:
    bad = [s for s in result.graph.sources if s.state in ("error", "not_queried")]
    lines = [f"граф: {result.graph_state}; уровень прогона: {result.run_level}"]
    lines += [f"  источник {s.name}: {s.state} {s.detail}" for s in bad]
    if not result.roadmap.valid:
        lines.append("  роадмап: RM-INVALID — очередь без рангов")
    return lines


def render_status(result: Result, top: int = 15) -> str:
    """Фокусы, верх очереди, циклы, ожидания."""
    by_node = {a.node_id: a for a in result.assessments}
    lines = _header(result)
    lines.append("фокусы: " + ", ".join(f.epic for f in result.roadmap.focus))
    lines.append("очередь:")
    for e in result.queue[:top]:
        a = by_node[e.node_id]
        lines.append(f"  {e.rank or '-'}  {e.node_id}  [{a.need} → {a.actor}]")
        lines.append(f"       {e.why}")
    for cycle in result.cycles:
        lines.append("цикл: " + " → ".join(cycle))
    fresh = [w for w in result.waits if w.verdict == "satisfied"]
    stale = [w for w in result.waits if w.reason == "stale"]
    lines.append(f"ожидания: выполнено {len(fresh)}, застой {len(stale)}")
    return "\n".join(lines)


def render_why(result: Result, node_id: str) -> str:
    """Цепочка depends_on от узла до листьев."""
    edges = [e for e in result.graph.edges if e.type == "depends_on"]
    lines, frontier, seen = [node_id], [(node_id, 0)], {node_id}
    while frontier:
        node, depth = frontier.pop()
        for e in sorted(edges, key=lambda e: e.dst):
            if e.src != node:
                continue
            verdict = next((w.verdict for w in result.waits
                            if w.consumer == node and w.prereq == e.dst), "?")
            lines.append("  " * (depth + 1) + f"ждёт {e.dst} [{verdict}]")
            if e.dst not in seen:
                seen.add(e.dst)
                frontier.append((e.dst, depth + 1))
    entry = next((e for e in result.queue if e.node_id == node_id), None)
    lines.append(entry.why if entry else "не кандидат (заблокирован или закрыт)")
    for e in result.queue:
        if node_id in e.why and e.node_id != node_id:
            lines.append(f"лист: {e.node_id} — {e.why}")
    return "\n".join(lines)


def render_plan(result: Result) -> str:
    """Действия, которые были бы выполнены на уровне прогона."""
    lines = _header(result)
    lines.append(f"план на уровне {result.run_level} (ничего не исполняется):")
    for a in result.assessments:
        if a.action != "—":
            lines.append(f"  {a.action:<16} {a.node_id}  ({a.need})")
    return "\n".join(lines)
```

`conductor/__main__.py` (заменить целиком):
```python
"""CLI conductor: status | why | plan | run | record (спека §7.2, срез 0)."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import plan_fields as pf

from conductor.collect import collect
from conductor.inputs import Inputs, load_inputs, save_inputs
from conductor.render import render_plan, render_status, render_why
from conductor.snapshot import evaluate, to_snapshot
from conductor.sources_gh import run_gh

EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG = 0, 2, 3, 4


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="conductor", exit_on_error=False)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--root", type=Path, default=Path(".."))
    p.add_argument("--manifest", type=Path)
    p.add_argument("--roadmap", type=Path)
    p.add_argument("--replay", type=Path)
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--out", type=Path, default=Path("out/conductor"))
    p.add_argument("--level", type=int, choices=range(4), default=0)
    p.add_argument("command", nargs="?", choices=["status", "why", "plan", "run",
                                                   "record"])
    p.add_argument("target", nargs="?")
    return p


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inputs(args: argparse.Namespace, manifest: Path) -> Inputs:
    if args.replay is not None:
        return load_inputs(args.replay)
    return collect(args.root, manifest, args.roadmap, not args.no_fetch, run_gh,
                   socket.gethostname(), _now())


def _run(args: argparse.Namespace, inputs: Inputs, index: object) -> int:
    result = evaluate(inputs, index, 0)
    run_id = inputs.captured_at.replace(":", "")
    run_dir = args.out / run_id
    previous_dirs = sorted(d for d in args.out.glob("*") if d.is_dir()) \
        if args.out.is_dir() else []
    previous = None
    if previous_dirs:
        prev_file = previous_dirs[-1] / "snapshot.json"
        previous = json.loads(prev_file.read_text(encoding="utf-8")) \
            if prev_file.is_file() else None
    run_dir.mkdir(parents=True, exist_ok=True)
    snap = to_snapshot(result, inputs, run_id, previous)
    (run_dir / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    save_inputs(inputs, run_dir / "inputs.json")
    print(render_status(result))
    return EXIT_CONFIG if not result.roadmap.valid else EXIT_OK


def _selftest() -> int:
    from tests.conductor.fixtures import index, inputs  # noqa: PLC0415

    result = evaluate(inputs({"a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"}),
                      index(), 0)
    ok = [e.node_id for e in result.queue] == ["todo://a/x"]
    print("selftest:", "ok" if ok else "FAIL")
    return EXIT_OK if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Точка входа; коды выхода — §7.2."""
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return EXIT_ARGS
    if args.selftest:
        return _selftest()
    if args.command is None or (args.command in ("why", "record")
                                and not args.target):
        return EXIT_ARGS
    manifest = args.manifest or (
        args.root / "ai-orchestrators-workspace" / "workspace-manifest.toml")
    index = pf.manifest_index(manifest)
    inputs = _inputs(args, manifest)
    if all(t.state == "error" for t in inputs.todos) and inputs.gh_state != "read":
        print("ни один источник не прочитан", file=sys.stderr)
        return EXIT_NO_SOURCE
    if args.command == "record":
        save_inputs(inputs, Path(args.target) / "inputs.json")
        return EXIT_OK
    if args.command == "run":
        return _run(args, inputs, index)
    level = args.level if args.command == "plan" else 0
    result = evaluate(inputs, index, level)
    if args.command == "status":
        print(render_status(result))
    elif args.command == "why":
        print(render_why(result, args.target))
    else:
        print(render_plan(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor -q`
Expected: PASS (все тесты пакета)

Run: `uv run --frozen --group selfcheck ruff check conductor tests/conductor && uv run --frozen --group selfcheck ruff format --check conductor tests/conductor && uv run --frozen --group selfcheck pyrefly check conductor`
Expected: без ошибок; найденное исправить в этом же шаге (форматирование и порядок импортов — `ruff format`, `ruff check --fix`)

- [ ] **Step 5: Commit**

```bash
git add conductor tests/conductor
git commit -m "feat(conductor): сбор входов, рендер и CLI status/why/plan/run/record"
```

---

### Task 13: Таймер на VPS (уровень 0)

**Files:**
- Create: `deploy/conductor/conductor.service`, `deploy/conductor/conductor.timer`, `deploy/conductor/setup.sh`, `deploy/conductor/README.md`, `tests/conductor/test_deploy.py`

**Interfaces:**
- Consumes: `python -m conductor run` (Task 12)
- Produces: юниты systemd; `/srv/conductor/{devtools,workspace,state,gh}`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_deploy.py`:
```python
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_service_runs_level_0_under_flock_without_writes() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "flock -n /srv/conductor/state/conductor.lock" in unit
    assert "-m conductor run" in unit and "--level" not in unit
    assert "User=conductor" in unit and "TimeoutStartSec=55min" in unit


def test_timer_hourly_and_setup_does_not_enable() -> None:
    assert "OnCalendar=hourly" in (DEPLOY / "conductor.timer").read_text()
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    assert "systemctl enable" not in setup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_deploy.py -q`
Expected: FAIL — `FileNotFoundError` на `conductor.service`

- [ ] **Step 3: Write minimal implementation**

`deploy/conductor/conductor.service`:
```ini
[Unit]
Description=conductor: fleet advisor, level 0 (devtools python -m conductor run)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=conductor
Group=conductor
UMask=0027
# run_timeout_min из спеки §2.1: зависший git/gh не держит замок вечно.
TimeoutStartSec=55min
Environment=GH_CONFIG_DIR=/srv/conductor/gh
WorkingDirectory=/srv/conductor/devtools
ExecStart=/usr/bin/flock -n /srv/conductor/state/conductor.lock /usr/local/bin/uv run --frozen python -m conductor run --root /srv/conductor/workspace --out /srv/conductor/state/runs
```

`deploy/conductor/conductor.timer`:
```ini
[Unit]
Description=conductor: hourly advisor run

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/conductor/setup.sh`:
```bash
#!/usr/bin/env bash
# One-time VPS bring-up for conductor slice 0 (level 0, read-only). Idempotent.
# Run as root from a devtools checkout:
#   sudo GIT_BASE=git@github.com:<owner> deploy/conductor/setup.sh
# Installs units but does NOT enable the timer: turning it on is the owner's
# step in deploy/conductor/README.md.
set -euo pipefail

HOME_DIR=/srv/conductor
UNIT_DIR=/etc/systemd/system
GIT_BASE="${GIT_BASE:?set GIT_BASE, e.g. git@github.com:your-org}"
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update -q
apt-get install -y -q git python3 util-linux
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
command -v gh >/dev/null || { echo ">>> install gh (https://cli.github.com) and re-run"; exit 1; }

id conductor &>/dev/null || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin conductor
install -d -o conductor -g conductor -m 0750 "$HOME_DIR"
install -d -o conductor -g conductor -m 0700 "$HOME_DIR/devtools" "$HOME_DIR/workspace" "$HOME_DIR/gh"
install -d -o conductor -g conductor -m 0750 "$HOME_DIR/state" "$HOME_DIR/state/runs"
[ -f "$HOME_DIR/state/conductor.lock" ] || install -o conductor -g conductor -m 0600 /dev/null "$HOME_DIR/state/conductor.lock"

[ -d "$HOME_DIR/devtools/.git" ] || sudo -u conductor git clone -q "$GIT_BASE/devtools.git" "$HOME_DIR/devtools"
WS="$HOME_DIR/workspace"
[ -d "$WS/ai-orchestrators-workspace/.git" ] || sudo -u conductor git clone -q "$GIT_BASE/ai-orchestrators-workspace.git" "$WS/ai-orchestrators-workspace"
sudo -u conductor env HOME="$HOME_DIR" python3 "$HOME_DIR/devtools/clone_fleet.py" \
    --manifest "$WS/ai-orchestrators-workspace/workspace-manifest.toml" --root "$WS"

install -m 0644 "$HERE/conductor.service" "$HERE/conductor.timer" "$UNIT_DIR/"
systemctl daemon-reload
echo ">>> next: sudo -u conductor env GH_CONFIG_DIR=$HOME_DIR/gh gh auth login (read-only token), then README"
```

`deploy/conductor/README.md`:
```markdown
# conductor на VPS — срез 0 (советчик, уровень 0)

Срез 0 **ничего не пишет** во флот: прогон читает клоны и GitHub, пишет
снимок в `/srv/conductor/state/runs/<run_id>/`. GitHub App не нужен.

1. `sudo GIT_BASE=git@github.com:andrei-shtanakov deploy/conductor/setup.sh`
2. Авторизация `gh` **только на чтение** (fine-grained токен владельца:
   `contents`, `issues`, `pull_requests`, `metadata` — read) в
   `GH_CONFIG_DIR=/srv/conductor/gh` пользователя `conductor`.
3. Пробный прогон: `sudo systemctl start conductor.service`,
   `journalctl -u conductor -n 50`, снимок в `state/runs/`.
4. Включить таймер: `sudo systemctl enable --now conductor.timer`.
5. Имя хоста (`hostname`) — значение `writer_host` в `roadmap.toml` зонтика
   (для среза 0 используется только для отчёта; писать начнёт срез 1).

Обновление кода: `sudo -u conductor git -C /srv/conductor/devtools pull --ff-only`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_deploy.py -q && shellcheck deploy/conductor/setup.sh`
Expected: PASS (2 passed); shellcheck без замечаний (если бинаря нет — `uv run --frozen --group selfcheck shellcheck deploy/conductor/setup.sh`)

- [ ] **Step 5: Commit**

```bash
git add deploy/conductor tests/conductor/test_deploy.py
git commit -m "feat(conductor): таймер на VPS для советчика (уровень 0)"
```

---

### Task 14: Скилл, документация, пункт плана

**Files:**
- Create: `skills/conductor/SKILL.md`
- Modify: `CLAUDE.md` (таблица «Инструменты»), `TODO.md` (раздел «conductor»)

**Interfaces:**
- Consumes: CLI (Task 12)

- [ ] **Step 1: Write the skill**

`skills/conductor/SKILL.md`:
```markdown
---
name: conductor
description: Советчик флота — что сейчас главное, почему issue/пункт не закрыт, кто кого ждёт. Использовать при вопросах «почему не закрыт X», «что дальше», «кто чего ждёт».
---

# conductor — советчик (срез 0)

Ничего не пишет во флот. Все команды — из devtools:

- `make conductor ARGS=status` — фокусы, верх очереди с `why`, циклы, ожидания.
- `make conductor ARGS="why <node>"` — цепочка от узла до листьев; `<node>` —
  `todo://<repo>/<id>`, `<repo>#<N>` или `<repo>!<N>`.
- `make conductor ARGS="plan --level 3"` — что conductor сделал бы на уровне 3.
- `make conductor ARGS="record out/conductor/replays/<дата>"` — сохранить входы;
  `--replay <dir>/inputs.json` — повторить разбор без сети.

Отвечая пользователю, цитируй строку `why` и называй `partial`, если граф
неполон: «блокеров нет» на неполном графе не утверждается.
```

- [ ] **Step 2: Update CLAUDE.md**

В `CLAUDE.md` в таблицу «Инструменты» после строки `selfcheck/` добавить:
```markdown
| `conductor/` | агент-оркестратор флота (спека `docs/superpowers/specs/2026-09-29-conductor-design.md`). Срез 0 — советчик: `make conductor ARGS=status\|"why <node>"\|"plan --level N"\|run\|"record <dir>"`; читает TODO с `origin/<default>` и GitHub, строит граф `depends_on/accepted_as/implements/mentions`, ранжирует по `roadmap.toml` зонтика, пишет снимок `conductor-snapshot/v1` в `out/conductor/`. Во флот не пишет; VPS-таймер — `deploy/conductor/` |
```

- [ ] **Step 3: Update TODO.md**

В разделе «conductor — агент-оркестратор флота» под пунктом `@id:conductor` добавить:
```markdown
- [ ] conductor срез 0 — советчик: граф, ожидания, порядок, `status/why/plan/run`, снимок, таймер уровня 0; приёмка — replay на сохранённых входах, верх очереди совпадает с приоритетами владельца @owner:github:andrei-shtanakov @id:conductor-slice-0 @epic:eco.tooling
```

- [ ] **Step 4: Verify plan-check stays clean**

Run: `make plan-check-selftest && uv run --frozen pytest tests/conductor -q`
Expected: selftest ok; все тесты пакета PASS

- [ ] **Step 5: Commit**

```bash
git add skills/conductor CLAUDE.md TODO.md
git commit -m "docs(conductor): скилл, строка инструментов, пункт среза 0"
```

---

### Task 15: `roadmap.toml` в зонтике (отдельный репо, свой PR)

**Files (в репо `ai-orchestrators-workspace`, не devtools):**
- Create: `roadmap.toml`
- Modify: `.gitignore` (белый список: `!/roadmap.toml`)

Работа идёт в зонтике по его правилам (ветка → PR → ревью ai-prosto → мерж). Ловушка из памяти: `.gitignore` зонтика — белый список; без `!/roadmap.toml` файл игнорируется молча.

- [ ] **Step 1: Черновик роадмапа от текущих приоритетов владельца**

`roadmap.toml`:
```toml
# roadmap.toml — порядок работ. Владелец: Andrei. Порядок [[focus]] = приоритет.
# Схема и семантика: devtools/docs/superpowers/specs/2026-09-29-conductor-design.md §2.
schema_version = 1
updated      = "2026-09-29"
autonomy     = 0
writer_host  = "vps-conductor"
writer_since = "2026-09-29T00:00:00Z"

[[focus]]
epic = "eco.dark-factory"
goal = "todo://devtools/bundle-docs-as-oracle"

[[focus]]
epic = "eco.tooling"

[[focus]]
epic = "eco.governance-plane"
```
`writer_host` заменить на фактический `hostname` VPS из Task 13, Step 3 README, до коммита.

- [ ] **Step 2: Проверить разбором conductor**

Run (из devtools): `uv run --frozen python -m conductor status --roadmap ../ai-orchestrators-workspace/roadmap.toml --no-fetch | head -5`
Expected: нет строки `RM-INVALID`; «фокусы: eco.dark-factory, eco.tooling, eco.governance-plane»

- [ ] **Step 3: PR в зонтик**

```bash
git -C ../ai-orchestrators-workspace switch -c feat/roadmap-toml
git -C ../ai-orchestrators-workspace add roadmap.toml .gitignore
git -C ../ai-orchestrators-workspace commit -m "feat: roadmap.toml — порядок фокусов для conductor (срез 0)"
git -C ../ai-orchestrators-workspace push -u origin feat/roadmap-toml
gh pr create -R andrei-shtanakov/ai-orchestrators-workspace --fill
```
Порядок фокусов — решение владельца: PR просит его подтвердить или переставить.

---

### Task 16: Живая приёмка — replay на сохранённых входах

Приёмка среза 0 по спеке §10: верх очереди совпадает с приоритетами владельца и показывает ранее невидимые цепочки.

- [ ] **Step 1: Записать входы флота**

Run: `make conductor ARGS="record out/conductor/replays/$(date +%F)"`
Expected: файл `out/conductor/replays/<дата>/inputs.json`; код 0

- [ ] **Step 2: Разбор без сети**

Run: `uv run --frozen python -m conductor status --replay out/conductor/replays/$(date +%F)/inputs.json --manifest ../ai-orchestrators-workspace/workspace-manifest.toml`
Expected: `граф: complete` (иначе — перечень непрочитанных источников, починить до приёмки); очередь с `why`

- [ ] **Step 3: Проверить известную цепочку**

Run: `uv run --frozen python -m conductor why todo://devtools/bundle-docs-as-oracle --replay out/conductor/replays/$(date +%F)/inputs.json --manifest ../ai-orchestrators-workspace/workspace-manifest.toml`
Expected: в цепочке видны `spec-runner#603` / `steward#190` (ожидания приёмки оракула из памяти 2026-09-29) и их вердикты; если `devtools#491` ↔ `spec-runner#603` оформлены структурно — `цикл:` в `status`, иначе `GR-WEAK-EDGE`

- [ ] **Step 4: Разбор с владельцем**

Показать владельцу первые 15 позиций `status` и список `owner_questions` из снимка `run`. Записать в TODO.md (пункт `conductor-slice-0`) итог: совпадает ли верх очереди с его приоритетами, какие цепочки оказались новыми, сколько вопросов владельцу и по каким причинам. Несовпадение — это находка для роадмапа или ранжирования, а не повод закрывать пункт.

---

## Self-Review (выполнен при написании)

1. **Покрытие спеки (срез 0 по §11):** §2 — Task 2; §3.1 — Tasks 4–6, 12; §3.2 — Task 6; §3.3 — Task 8; §3.4 (структурные условия без модели, `@trigger` → `unknown`) — Task 7; §3.5 (`in_review`/`idle`; поколений в срезе 0 нет) — Task 8; §4 — Task 9; §5.2–5.3 как выдача — Task 10; §7.1 и показатели §10 — Task 11; §7.2 — Task 12; таймер уровня 0 — Task 13; `roadmap.toml` в зонтике — Task 15; replay-приёмка — Task 16. Намеренно вне среза 0: запросы в зонтике §6 (кроме наследия inbox через `slug:`/`from:`), записи §5.1/§5.4–5.10, App §8, публикация снимка §7.4, путь authority-root в делегируемости (контекст-пак — срез 3).
2. **Плейсхолдеры:** нет TBD и «добавить обработку»; флаги `clone_fleet.py` (`--manifest`, `--root`) сверены по `--help`; ребро на несуществующий пункт строится из `references` (сверено: `plan-fields` не создаёт `edges` при `resolved_target = None`).
3. **Согласованность типов:** `Graph.out/into`, `Wait(consumer, prereq, verdict, reason, evidence, since)`, `QueueEntry(... via_focus, klass ...)`, `Assessment(... action)`, `Result` — одни имена во всех задачах.
4. **Review Focus:** п.1 и п.2 — тесты Task 4; п.3 — Task 5 (`test_offline_is_error`), Task 6 (`test_gh_error_makes_graph_partial`), Task 12 (`test_gh_error_still_exit_0`); п.4 — Task 6; п.5 — Task 8.
