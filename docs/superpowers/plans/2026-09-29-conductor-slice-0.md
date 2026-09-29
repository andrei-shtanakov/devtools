# conductor, срез 0 — советчик: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read-only советчик: собрать единый граф зависимостей флота с опубликованных веток и GitHub, активно проверить ожидания, разложить работу по `roadmap.toml` и объяснить порядок (`status`, `why`, `plan`, `run` → снимок), ничего не записывая во флот.

**Architecture:** Пакет `conductor/` в devtools (`python -m conductor`). Сбор (`sources_git`, `sources_gh`, `collect`) → сериализуемый `Inputs` (он же формат replay, включая текст манифеста) → чистое детерминированное ядро (`graph` → `waits` → `analysis` → `rank` → `policy`) → `snapshot` (контракт `conductor-snapshot/v1`) и текстовый рендер. Ядро не делает ввода-вывода: всё, что оно знает, лежит в `Inputs`, поэтому replay на сохранённых входах даёт тот же результат, что живой прогон.

**Tech Stack:** Python ≥ 3.12 (uv-окружение devtools), пакет `plan-fields` (пин в `pyproject.toml`), stdlib, `gh` и `git` как внешние процессы, pytest, jsonschema (dev-группа).

**Spec:** `docs/superpowers/specs/2026-09-29-conductor-design.md` (rev 10). Срез 0 — §11: §2, §3 (без модели), §4, §5.2–5.3 как выдача, §7.1–7.2, таймер на уровне 0, `roadmap.toml` в зонтике.

**План rev 4** — по кругу 3 ревью пары (3 major, 2 minor), класс fail-closed закрыт механизмом: каждое обязательное чтение и каждое усечение (соседние версии пути, неизвестный репо в `exists:`, список файлов и ревью PR) регистрируется в источнике `history`; CI и одобрение PR — об одном head SHA, иначе чтение PR — сбой; манифест читается с `origin` зонтика и входит в источники с SHA; `GR-ORPHAN-REQUEST` для inbox без `from:` и с неизвестным репо, `GR-SLUG-MATCH` для склейки без `@source-ref`; вопрос `GR-SHIPPED-OPEN` — только в фокусе; `status` печатает те же вопросы, что снимок. **Rev 3** — по кругу 2 ревью пары (7 major, 1 minor): одобрение PR на head SHA через GraphQL и выбор actor мержа по `Мерж: человек`/authority-root; ошибки вспомогательных чтений делают граф `partial`; начало ожидания — `git blame` строки пункта; `focus.epic` проверяется на тип; вопросы владельцу — по причине ожидания; `plan` ограничен `roadmap.autonomy`; `record` возвращает 4 при `RM-INVALID`. **Rev 2** — по кругу 1 ревью пары (1 blocker, 13 major, 1 minor). Листинги кода перед отдачей на ревью извлечены во временный каталог, прогнаны тестами, ruff и pyrefly на окружении devtools и живым прогоном на флоте (только чтение) — см. «Проверка плана исполнением» в конце. Живой прогон изменил два решения: обнаружение GitHub — только открытые (окно закрытых упиралось в потолок поиска), ожидание прозаического `@trigger` — `wait_condition`, не вопрос владельцу.

## Global Constraints

- Никаких записей во флот и GitHub: ни один модуль среза не вызывает мутирующих `gh`/`git push`; `git fetch` — единственная сетевая операция git (§11, срез 0).
- `run_level` среза 0 фактически 0; `plan --level N` вычисляет гипотетические действия и тоже ничего не пишет (§2.4, §7.2).
- TODO, роадмап, реестр эпиков, факты `exists:` — только с `origin/<default>` (зонтик — тоже), SHA в снимок (I6).
- `partial` при любом непрочитанном источнике (TODO, GitHub, роадмап, реестр эпиков), неполном поиске или недогруженной ссылке (I6).
- `_cowork_output/` не читается никогда (I9).
- Модель не вызывается: проза `@trigger` → `unknown`; структурные формы — `date>=YYYY-MM-DD`, `exists:<repo>:<path>` (§3.4 rev 10).
- Потолок уровня: `0` при `partial` или `RM-INVALID`, иначе `min(--level, roadmap.autonomy)`; `plan` — симуляция управляющего писателя (личность хоста не проверяется, потому что `plan` не пишет), `run` в срезе 0 всегда 0.
- Коды выхода: 0 — выполнено; 2 — аргументы CLI; 3 — не собран ни один источник или нет манифеста; 4 — `RM-INVALID` для любой команды, `run` при этом пишет снимок (§7.2 rev 10).
- Вопрос владельцу — только по позиции с рангом (§5.7 rev 10).
- Делегируемость в срезе 0 никогда не `yes`: путь authority-root не проверяется до среза 3, поэтому лучший исход — `unverified`, действие `launch?` (§5.2, I5).
- Python ≥ 3.12, запуск через `uv run --frozen`; строки ≤ 88; type hints; ruff и pyrefly чистые.
- Имена репо — канонические ключи манифеста плюс зонтик `ai-orchestrators-workspace`; GitHub-имена (`prograph-vault`) нормализуются к ключам (`ecosystem-kb`) на всех концах рёбер (§3.1).

**Отступление от §12 спеки, названное явно:** CLI — `conductor/__main__.py` (`python -m conductor`): файл `conductor.py` и пакет `conductor/` в корне devtools конфликтуют при импорте. Паттерн тот же, что у `selfcheck/`.

## Review Focus

1. Клон без `origin/HEAD` или с `origin/HEAD` на несуществующую ветку — откат на `origin/master`/`origin/main`, иначе `error`, без падения. Тест — Task 4.
2. `TODO.md` нет на default-ветке — `absent`, граф `complete`; повреждённый объект или таймаут git — `error`, не `absent`. Тест — Task 4.
3. `gh` не авторизован, офлайн или поиск открытых неполон (`incomplete_results`, `total_count` больше полученного) — GitHub-источник `error`, граф `partial`, TODO-плоскость всё равно ранжируется, код 0. Тесты — Task 5, Task 6, Task 12.
4. Тело inbox-issue со значениями в бэктиках и CRLF (живой `arbiter#104`: ``from: `deployer` ``), GitHub-имя репо в ссылке (`prograph-vault#3`) — поля разбираются, конец ребра нормализуется. Тест — Task 6.
5. Пункт ждёт сам себя и длинные цепочки — цикл найден, без рекурсии. Тест — Task 8.

---

## Файлы

| Файл | Ответственность |
|---|---|
| `conductor/__init__.py` | версия пакета |
| `conductor/model.py` | неизменяемые типы: `Source`, `Node`, `Edge`, `Finding` |
| `conductor/roadmap.py` | разбор и валидация `roadmap.toml` (§2) |
| `conductor/inputs.py` | `Inputs` — всё, что ядро знает о мире; JSON для replay |
| `conductor/manifest.py` | репо-цели, владелец, индекс манифеста из текста |
| `conductor/sources_git.py` | чтение с `origin/<default>`, история, факты путей |
| `conductor/sources_gh.py` | GitHub: поиск с проверкой полноты, дочитывание, CI/ревью PR |
| `conductor/collect.py` | сборка `Inputs` |
| `conductor/graph.py` | узлы, рёбра, склейка, нормализация имён (§3.1–3.2) |
| `conductor/waits.py` | предпосылки, `@trigger`, застой (§3.4) |
| `conductor/analysis.py` | готовность, циклы, находки, состояние работы, внимание (§3.3, §3.5) |
| `conductor/rank.py` | протекание ранга, ключ сортировки, `why` (§4) |
| `conductor/policy.py` | уровень позиции, `actor/need`, делегируемость, действие (§2.4, §5.2–5.3) |
| `conductor/snapshot.py` | конвейер ядра, снимок v1, показатели (§7.1, §10) |
| `conductor/render.py` | текст `status`, `why`, `plan` |
| `conductor/__main__.py` | CLI и коды выхода (§7.2) |
| `contracts/conductor-snapshot/v1/schema.json` | JSON-схема снимка |
| `tests/conductor/…` | тесты по модулям + `fixtures.py` |
| `deploy/conductor/…` | systemd-таймер уровня 0 |
| `skills/conductor/SKILL.md` | разговорный режим |
| `Makefile`, `CLAUDE.md`, `TODO.md` | алиас, строка инструментов, пункт плана |

Все команды — из корня devtools.

---

### Task 1: Каркас пакета и типы

**Files:**
- Create: `conductor/__init__.py`, `conductor/model.py`, `conductor/__main__.py` (заглушка), `tests/conductor/__init__.py`, `tests/conductor/test_model.py`
- Modify: `Makefile`

**Interfaces:**
- Produces: `SourceState`, `Source(name, state, detail="", sha=None)`; `NodeKind`; `Node(node_id, kind, repo, title, is_open, closed_as=None, epic=None, owner_ref=None, trigger=None, author=None, updated_at=None, body="", labels=(), url="")` с методом `owner() -> dict | None`; `closed_as ∈ {"completed","not_planned","merged","unmerged",None}`; `EdgeType`; `Edge(src, dst, type, origin)`; `Finding(code, severity, subject, detail="")`; `item_id`, `issue_id`, `pr_id`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_model.py`:
```python
from conductor.model import Edge, Finding, Node, Source, issue_id, item_id, pr_id


def test_ids_are_canonical() -> None:
    assert item_id("devtools", "conductor") == "todo://devtools/conductor"
    assert issue_id("spec-runner", 603) == "spec-runner#603"
    assert pr_id("devtools", 7) == "devtools!7"


def test_types_are_frozen_and_hashable() -> None:
    node = Node(
        "todo://a/x",
        "item",
        "a",
        "x",
        is_open=True,
        owner_ref=(("id", "own"), ("kind", "github_user")),
    )
    edge = Edge("todo://a/x", "todo://b/y", "depends_on", "todo")
    assert {node, node} == {node}
    assert node.owner() == {"id": "own", "kind": "github_user"}
    assert {edge} == {Edge("todo://a/x", "todo://b/y", "depends_on", "todo")}
    assert Source("github", "error", "offline").state == "error"
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
    source_ref: str | None = None

    def owner(self) -> dict[str, Any] | None:
        """owner_ref как словарь (хранится кортежем ради хэшируемости)."""
        return dict(self.owner_ref) if self.owner_ref is not None else None


@dataclass(frozen=True)
class Edge:
    """Типизированное ребро: src ждёт / принят как / реализует / упоминает dst."""

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

`Makefile`: после строки `edge-check:` добавить
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

### Task 2: Роадмап — разбор, валидация, классы

**Files:**
- Create: `conductor/roadmap.py`, `tests/conductor/test_roadmap.py`

**Interfaces:**
- Consumes: `Finding`
- Produces: `Focus(epic, rank, goal, autonomy, pull_prerequisites)`; `Roadmap(autonomy, writer_host, writer_since, focus, parked, limits, valid, findings)` с `focus_of(epic) -> Focus | None`, `klass(epic) -> "focus"|"parked"|"background"`; `parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap`; `LIMIT_DEFAULTS`.

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


def _codes(text: str | None) -> set[str]:
    return {f.code for f in parse_roadmap(text, EPICS).findings}


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


def test_invalid_cases() -> None:
    cases = [
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.nope"'),
        GOOD.replace('"eco.research-bench"', '"eco.dark-factory"'),
        GOOD.replace(
            '"eco.research-bench"]', '"eco.research-bench", "eco.research-bench"]'
        ),
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "airun.kapelle-m3"'),
        GOOD.replace("todo://devtools/bundle-docs-as-oracle", "devtools#1"),
        GOOD.replace("autonomy = 0", "autonomy = 7"),
        GOOD + "\n[limits]\nmax_writes_per_run = 0\n",
        GOOD.replace('"2026-09-29T12:00:00Z"', '"вчера"'),
        GOOD.replace('[parked]\nepics = ["eco.research-bench"]', "").replace(
            'writer_since = "2026-09-29T12:00:00Z"',
            'writer_since = "2026-09-29T12:00:00Z"\nparked = 5',
        ),
        GOOD.replace('[[focus]]\nepic = "eco.dark-factory"', "focus = 3\n[x]"),
        GOOD.replace('epic = "eco.dark-factory"', 'epic = ["eco.dark-factory"]'),
        GOOD.replace('epic = "eco.dark-factory"', "epic = { x = 1 }"),
        "not = [toml",
        None,
    ]
    for text in cases:
        assert "RM-INVALID" in _codes(text), text


def test_paused_focus_is_warning() -> None:
    rm = parse_roadmap(
        GOOD.replace('epic = "eco.governance-plane"', 'epic = "eco.cadence"'), EPICS
    )
    assert rm.valid and "RM-FOCUS-PAUSED" in {f.code for f in rm.findings}
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
    limits: dict[str, int] = field(
        default_factory=lambda: {k: v[0] for k, v in LIMIT_DEFAULTS.items()}
    )
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
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _is_utc_instant(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _limits(raw: Any, errors: list[Finding]) -> dict[str, int]:
    if raw is not None and not isinstance(raw, dict):
        errors.append(_invalid("limits должен быть таблицей"))
    table = raw if isinstance(raw, dict) else {}
    limits: dict[str, int] = {}
    for name, (default, low, high) in LIMIT_DEFAULTS.items():
        value = table.get(name, default)
        if not _int_in(value, low, high):
            errors.append(_invalid(f"limits.{name}={value!r} вне {low}..{high}"))
            value = default
        limits[name] = value
    return limits


def _focus(
    raw: Any,
    top: int,
    epics: dict[str, dict],
    errors: list[Finding],
    warnings: list[Finding],
) -> tuple[Focus, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(_invalid("focus должен быть массивом таблиц [[focus]]"))
        return ()
    result: list[Focus] = []
    for rank, entry in enumerate(raw, start=1):
        epic = entry.get("epic") if isinstance(entry, dict) else None
        if (
            not isinstance(entry, dict)
            or not isinstance(epic, str)
            or epic not in epics
        ):
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
        if not isinstance(pull, bool):
            errors.append(_invalid(f"focus #{rank}: pull_prerequisites={pull!r}"))
            pull = False
        result.append(Focus(epic, rank, goal, autonomy, pull))
    return tuple(result)


def _parked(raw: Any, epics: dict[str, dict], errors: list[Finding]) -> list[str]:
    if raw is None:
        return []
    items = raw.get("epics", []) if isinstance(raw, dict) else None
    if not isinstance(items, list) or not all(isinstance(e, str) for e in items):
        errors.append(_invalid("parked должен быть таблицей с epics = [строки]"))
        return []
    for epic in sorted(set(items) - set(epics)):
        errors.append(_invalid(f"parked: неизвестный эпик {epic!r}"))
    return items


def parse_roadmap(text: str | None, epics: dict[str, dict]) -> Roadmap:
    """Разбор и валидация (§2.1–2.2); ошибки становятся RM-INVALID."""
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
    parked = _parked(data.get("parked"), epics, errors)
    seen = [f.epic for f in focus] + parked
    for epic in sorted({e for e in seen if seen.count(e) > 1}):
        errors.append(_invalid(f"эпик {epic} встречается дважды"))
    limits = _limits(data.get("limits"), errors)
    return Roadmap(
        autonomy=top,
        writer_host=writer,
        writer_since=since if isinstance(since, str) else "",
        focus=focus,
        parked=frozenset(parked),
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

### Task 3: `Inputs`, манифест и replay

**Files:**
- Create: `conductor/inputs.py`, `conductor/manifest.py`, `tests/conductor/test_inputs.py`

**Interfaces:**
- Consumes: `SourceState`
- Produces:
  - `RepoTodo(repo, text, sha, state, detail="")`
  - `Inputs` (поля ниже), `save_inputs(inputs, path)`, `load_inputs(path) -> Inputs`, `INPUTS_VERSION = 1`
  - GhRecord (dict): `repo` (канонический ключ), `number`, `is_pr`, `title`, `body`, `state` (`open|closed`), `state_reason` (`completed|not_planned|None`), `merged`, `author`, `labels`, `updated_at`, `url`, `comments: [{author, body, created_at}]`, `closing_refs: ["<github-name>#N"]`, для PR — `head_sha`, `review_decision`, `ci` (`green|red|pending|unknown`), `approved_at_head`, `files`, `complete`
  - `UMBRELLA`, `FleetRepo(key, git_dir, github_name)`, `fleet_repos(manifest_text) -> list[FleetRepo]`, `github_owner(manifest_text) -> str`, `manifest_index(manifest_text) -> plan_fields.ManifestIndex`

Поля `Inputs`: `captured_at, host, owner, manifest_text, todos, gh_records, gh_state, gh_detail, roadmap_text, roadmap_state, roadmap_source, roadmap_sha, epics, epics_state, epics_detail, repo_names` (GitHub-имя → ключ), `movement` (node_id → ISO последнего коммита с `@id`), `wait_since` (`"<src>|<raw_ref>"` → ISO первого коммита, добавившего `@blocked_by:<raw_ref>`), `history` (`todo://r/id` → SHA коммита, где `@id` был, для отсутствующих предпосылок), `trigger_facts` (текст `exists:…` → `{"exists": bool|None, "sha": str|None, "siblings": [имена]}`), `epics_sha`, `aux_state`/`aux_detail` (ошибки вспомогательных чтений: история, факты, политики репо), `human_merge_repos` (репо со строкой `Мерж: человек` в CLAUDE.md на origin), `authority_prefixes` (из собственного `contracts/authority-root/v1/paths.env` devtools), `manifest_source` (`origin` | `file:<path>`), `manifest_sha`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_inputs.py`:
```python
from pathlib import Path

import pytest

from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
from conductor.manifest import UMBRELLA, fleet_repos, github_owner, manifest_index

MANIFEST = (
    '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    '[cores.a-sdk]\nmember = true\nrepo_url = "git@github.com:own/a.git"\n'
    'git_dir = "a"\n'
    '[tools.ecosystem-kb]\nrepo_url = "git@github.com:own/prograph-vault.git"\n'
    'git_dir = "prograph-vault"\n'
)


def _inputs() -> Inputs:
    return Inputs(
        captured_at="2026-09-29T12:00:00Z",
        host="mac",
        owner="own",
        manifest_text=MANIFEST,
        todos=[RepoTodo("a", "- [ ] x @owner:TBD @id:x\n", "abc", "read")],
        gh_records=[{"repo": "a", "number": 1, "is_pr": False}],
        gh_state="read",
        gh_detail="",
        roadmap_text="schema_version = 1\n",
        roadmap_state="read",
        roadmap_source="origin",
        roadmap_sha="def",
        epics={"eco.tooling": {"status": "active"}},
        epics_state="read",
        epics_detail="",
        repo_names={"prograph-vault": "ecosystem-kb", "a": "a"},
        movement={"todo://a/x": "2026-09-28T00:00:00Z"},
        wait_since={},
        history={},
        trigger_facts={},
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


def test_manifest_from_text() -> None:
    repos = {r.key: r for r in fleet_repos(MANIFEST)}
    assert set(repos) == {"a", "ecosystem-kb", UMBRELLA}
    assert repos["ecosystem-kb"].github_name == "prograph-vault"
    assert github_owner(MANIFEST) == "own"
    assert "ecosystem-kb" in manifest_index(MANIFEST).canonical_keys
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
    """Входы прогона; ядро не делает ввода-вывода сверх этого."""

    captured_at: str
    host: str
    owner: str
    manifest_text: str
    todos: list[RepoTodo]
    gh_records: list[dict[str, Any]]
    gh_state: SourceState
    gh_detail: str
    roadmap_text: str | None
    roadmap_state: SourceState
    roadmap_source: str
    roadmap_sha: str | None
    epics: dict[str, dict[str, Any]]
    epics_state: SourceState
    epics_detail: str
    repo_names: dict[str, str] = field(default_factory=dict)
    movement: dict[str, str] = field(default_factory=dict)
    wait_since: dict[str, str] = field(default_factory=dict)
    history: dict[str, str] = field(default_factory=dict)
    trigger_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    epics_sha: str | None = None
    aux_state: SourceState = "read"
    aux_detail: str = ""
    human_merge_repos: list[str] = field(default_factory=list)
    authority_prefixes: list[str] = field(default_factory=list)
    manifest_source: str = "file"
    manifest_sha: str | None = None


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

`conductor/manifest.py`:
```python
"""Репо-цели флота из текста workspace-manifest.toml плюс зонтик (§3.1).

Работает с ТЕКСТОМ манифеста: он сохраняется в Inputs, и replay не зависит от
файла на диске.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import plan_fields as pf

UMBRELLA = "ai-orchestrators-workspace"
_URL_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$")


@dataclass(frozen=True)
class FleetRepo:
    """Канонический ключ, каталог клона и имя репо на GitHub."""

    key: str
    git_dir: str
    github_name: str


def _entries(text: str) -> list[tuple[str, dict[str, Any]]]:
    data = tomllib.loads(text)
    return [
        (key, entry)
        for section in data.values()
        if isinstance(section, dict)
        for key, entry in section.items()
        if isinstance(entry, dict)
    ]


def fleet_repos(text: str) -> list[FleetRepo]:
    """Не-member записи с repo_url, уникальные по git_dir, + зонтик."""
    repos: dict[str, FleetRepo] = {}
    for key, entry in _entries(text):
        url, git_dir = entry.get("repo_url"), entry.get("git_dir")
        if not url or not git_dir or entry.get("member"):
            continue
        if match := _URL_RE.search(url):
            repos.setdefault(git_dir, FleetRepo(key, git_dir, match.group(2)))
    repos.setdefault(UMBRELLA, FleetRepo(UMBRELLA, UMBRELLA, UMBRELLA))
    return sorted(repos.values(), key=lambda r: r.key)


def github_owner(text: str) -> str:
    """Владелец флота на GitHub — из repo_url первой записи."""
    for _, entry in _entries(text):
        if match := _URL_RE.search(entry.get("repo_url", "")):
            return match.group(1)
    raise ValueError("в манифесте нет github repo_url")


def manifest_index(text: str) -> Any:
    """plan_fields.ManifestIndex из текста манифеста."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "workspace-manifest.toml"
        path.write_text(text, encoding="utf-8")
        return pf.manifest_index(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_inputs.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/inputs.py conductor/manifest.py tests/conductor/test_inputs.py
git commit -m "feat(conductor): формат входов, манифест из текста, replay"
```

---

### Task 4: git-источник — чтение с `origin/<default>`, история, факты путей

**Files:**
- Create: `conductor/sources_git.py`, `tests/conductor/test_sources_git.py`

**Interfaces:**
- Consumes: `RepoTodo`, `FleetRepo`
- Produces: `git(repo_dir, *args) -> tuple[int, str, str]` (таймаут → 124, OSError → 127); `GitError`; `default_ref(repo_dir) -> str | None`; `fetch(repo_dir) -> str | None`; `read_file_at_origin(repo_dir, path) -> (text, sha, state, detail)`; `read_todo(repo, root, do_fetch) -> RepoTodo`; `last_commit_mentioning(repo_dir, ref, token) -> str | None` (сбой — `GitError`); `line_since(repo_dir, ref, line, path="TODO.md") -> str` (`git blame` строки; сбой — `GitError`); `ever_had(repo_dir, ref, text, path="TODO.md") -> str | None` (SHA; сбой — `GitError`); `path_fact(repo_dir, path) -> {"exists", "sha", "siblings"}` (`exists=None` — ошибка чтения).

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_sources_git.py`:
```python
import subprocess
from pathlib import Path

import pytest

import conductor.sources_git as sg
from conductor.manifest import FleetRepo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _clone(tmp: Path, files: dict[str, str], msg: str = "init @id:x") -> Path:
    up = tmp / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "master")
    _git(up, "config", "user.email", "t@t")
    _git(up, "config", "user.name", "t")
    for name, text in files.items():
        (up / name).parent.mkdir(parents=True, exist_ok=True)
        (up / name).write_text(text, encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-q", "--allow-empty", "-m", msg)
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", str(up), str(clone)], check=True)
    return clone


def test_reads_origin_not_worktree(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "- [ ] a @id:a\n"})
    (clone / "TODO.md").write_text("- [x] a @id:a\n", encoding="utf-8")
    text, sha, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert (state, text) == ("read", "- [ ] a @id:a\n") and sha


def test_origin_head_unset_or_broken_falls_back(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    _git(clone, "remote", "set-head", "origin", "-d")
    assert sg.default_ref(clone) == "origin/master"
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    assert sg.default_ref(clone) == "origin/master"


def test_no_default_branch_is_error(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    _git(clone, "remote", "set-head", "origin", "-d")
    _git(clone, "update-ref", "-d", "refs/remotes/origin/master")
    assert sg.read_file_at_origin(clone, "TODO.md")[2] == "error"


def test_missing_todo_is_absent(tmp_path: Path) -> None:
    clone = _clone(tmp_path, {"README": "r"})
    _, sha, state, _ = sg.read_file_at_origin(clone, "TODO.md")
    assert state == "absent" and sha


def test_timeout_is_error_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, {"TODO.md": "x\n"})
    real = sg.git

    def slow(repo_dir: Path, *args: str) -> tuple[int, str, str]:
        if args[0] == "show":
            return 124, "", "git timeout"
        return real(repo_dir, *args)

    monkeypatch.setattr(sg, "git", slow)
    assert sg.read_file_at_origin(clone, "TODO.md")[2] == "error"


def test_missing_checkout_is_error(tmp_path: Path) -> None:
    assert sg.read_todo(FleetRepo("x", "x", "x"), tmp_path, False).state == "error"


def test_history_helpers(tmp_path: Path) -> None:
    clone = _clone(
        tmp_path,
        {
            "TODO.md": "- [ ] a @id:a @blocked_by:todo://b/c\n",
            "contracts/v2/x.json": "{}",
        },
    )
    ref = "origin/master"
    assert sg.last_commit_mentioning(clone, ref, "@id:x")
    assert sg.last_commit_mentioning(clone, ref, "@id:nope") is None
    assert sg.line_since(clone, ref, 1).endswith("Z")
    with pytest.raises(sg.GitError):
        sg.ever_had(clone, "origin/nope", "@id:a")
    assert sg.ever_had(clone, ref, "@id:a")
    assert sg.ever_had(clone, ref, "@id:zzz") is None
    fact = sg.path_fact(clone, "contracts/v1/x.json")
    assert fact["exists"] is False and "v2" in fact["siblings"]
    assert sg.path_fact(clone, "contracts/v2/x.json")["exists"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_sources_git.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.sources_git'`

- [ ] **Step 3: Write minimal implementation**

`conductor/sources_git.py`:
```python
"""git-источник: файлы с origin/<default>, история, факты путей (I6).

Любой сбой git (ненулевой код, таймаут, нет бинаря) — состояние error, не
absent: absent означает только «пути на опубликованной ветке нет».
"""

from __future__ import annotations

import posixpath
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.inputs import RepoTodo
from conductor.manifest import FleetRepo
from conductor.model import SourceState

GIT_TIMEOUT = 120


def git(repo_dir: Path, *args: str) -> tuple[int, str, str]:
    """(код, stdout, stderr); таймаут → 124, нет бинаря → 127."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "git timeout"
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _verifies(repo_dir: Path, ref: str) -> bool:
    return git(repo_dir, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")[0] == 0


def default_ref(repo_dir: Path) -> str | None:
    """origin/HEAD, если указывает на коммит; иначе origin/master, origin/main."""
    code, out, _ = git(
        repo_dir, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"
    )
    candidates = [out.strip()] if code == 0 and out.strip() else []
    for ref in [*candidates, "origin/master", "origin/main"]:
        if _verifies(repo_dir, ref):
            return ref
    return None


def fetch(repo_dir: Path) -> str | None:
    """git fetch origin; None — успех, иначе текст ошибки."""
    code, _, err = git(repo_dir, "fetch", "-q", "origin")
    return None if code == 0 else (err.strip() or f"fetch exit {code}")


def read_file_at_origin(
    repo_dir: Path, path: str
) -> tuple[str | None, str | None, SourceState, str]:
    """(text, sha, state, detail) файла на origin/<default>."""
    ref = default_ref(repo_dir)
    if ref is None:
        return None, None, "error", "нет origin/<default>"
    code, out, err = git(repo_dir, "rev-parse", ref)
    if code != 0:
        return None, None, "error", err.strip()
    sha = out.strip()
    code, listed, err = git(repo_dir, "ls-tree", "--name-only", ref, "--", path)
    if code != 0:
        return None, sha, "error", err.strip() or "ls-tree failed"
    if not listed.strip():
        return None, sha, "absent", f"{path} нет на {ref}"
    code, text, err = git(repo_dir, "show", f"{ref}:{path}")
    if code != 0:
        return None, sha, "error", err.strip() or f"show exit {code}"
    return text, sha, "read", ref


def read_todo(repo: FleetRepo, root: Path, do_fetch: bool) -> RepoTodo:
    """TODO.md репо с origin; нет клона / сбой fetch — error."""
    repo_dir = root / repo.git_dir
    if not (repo_dir / ".git").exists():
        return RepoTodo(repo.key, None, None, "error", f"нет клона {repo_dir}")
    if do_fetch and (problem := fetch(repo_dir)) is not None:
        return RepoTodo(repo.key, None, None, "error", problem)
    text, sha, state, detail = read_file_at_origin(repo_dir, "TODO.md")
    return RepoTodo(repo.key, text, sha, state, detail)


class GitError(Exception):
    """git не ответил: результат неизвестен, а не «события не было» (I6)."""


def _checked(repo_dir: Path, *args: str) -> str:
    code, out, err = git(repo_dir, *args)
    if code != 0:
        raise GitError(f"{repo_dir.name}: git {args[0]}: {err.strip() or code}")
    return out


def last_commit_mentioning(repo_dir: Path, ref: str, token: str) -> str | None:
    """ISO-дата последнего коммита на ref с token в сообщении; сбой — GitError."""
    out = _checked(repo_dir, "log", "-1", "--format=%cI", "-F", f"--grep={token}", ref)
    return out.strip() or None


def line_since(repo_dir: Path, ref: str, line: int, path: str = "TODO.md") -> str:
    """ISO-дата коммита, последним менявшего строку line (git blame).

    Нижняя граница начала текущего ожидания конкретного пункта: правка строки
    «молодит» ожидание — это безопасная сторона (меньше ложных пинков).
    """
    out = _checked(
        repo_dir, "blame", "--porcelain", "-L", f"{line},{line}", ref, "--", path
    )
    for row in out.splitlines():
        if row.startswith("committer-time "):
            stamp = datetime.fromtimestamp(int(row.split()[1]), UTC)
            return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise GitError(f"{repo_dir.name}: blame без committer-time")


def ever_had(repo_dir: Path, ref: str, text: str, path: str = "TODO.md") -> str | None:
    """SHA последнего коммита, менявшего вхождения text в path; сбой — GitError."""
    out = _checked(repo_dir, "log", "-1", "--format=%H", "-S", text, ref, "--", path)
    return out.strip() or None


def path_fact(repo_dir: Path, path: str) -> dict[str, Any]:
    """{exists, sha, siblings} пути на origin/<default>; exists=None — ошибка."""
    ref = default_ref(repo_dir)
    if ref is None:
        return {"exists": None, "sha": None, "siblings": []}
    sha = git(repo_dir, "rev-parse", ref)[1].strip() or None
    code, listed, _ = git(repo_dir, "ls-tree", "--name-only", ref, "--", path)
    if code != 0:
        return {"exists": None, "sha": sha, "siblings": []}
    parts = path.split("/")
    versioned = next(
        (i for i, p in enumerate(parts) if p[:1] == "v" and p[1:].isdigit()), None
    )
    siblings: list[str] = []
    if versioned is not None:
        parent = "/".join(parts[:versioned])
        tree = f"{ref}:{parent}" if parent else ref
        code, names, _ = git(repo_dir, "ls-tree", "--name-only", tree)
        if code != 0:
            return {"exists": None, "sha": sha, "siblings": []}
        siblings = [posixpath.basename(n) for n in names.split()]
    return {"exists": bool(listed.strip()), "sha": sha, "siblings": siblings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_sources_git.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/sources_git.py tests/conductor/test_sources_git.py
git commit -m "feat(conductor): git-источник с origin/<default>, история, пути"
```

---

### Task 5: GitHub-источник — поиск с проверкой полноты, дочитывание, CI и ревью PR

**Files:**
- Create: `conductor/sources_gh.py`, `tests/conductor/test_sources_gh.py`

**Interfaces:**
- Produces: `Runner = Callable[[list[str]], tuple[int, str, str]]`; `run_gh(args)`; `ci_state(rollup) -> "green"|"red"|"pending"|"unknown"`; `discover(owner, fleet_names: set[str], runner) -> (hits: list[(name, number, is_pr)], state, detail)` — только открытые; `fetch_record(owner, name, number, is_pr, runner) -> dict | None` (без поля `repo`; для PR — дополнительный GraphQL-запрос: `approved_at_head` — есть `APPROVED` на коммит, равный `headRefOid`, и нет `CHANGES_REQUESTED`; `files`; `complete` — списки файлов и ревью не усечены; `headRefOid` GraphQL ≠ `headRefOid` из `pr view` → `None`, т.е. сбой чтения); `GhResult(records, state, detail)`; `collect_gh(owner, names_to_keys, extra_refs, runner, max_hops=3) -> GhResult`, где `extra_refs(records) -> set[(key, number)]`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_sources_gh.py`:
```python
import json

from conductor.sources_gh import ci_state, collect_gh, discover


def fake(responses: dict[str, tuple[int, str, str]]):
    def run(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        for prefix, answer in responses.items():
            if key.startswith(prefix):
                return answer
        return 1, "", f"unexpected: {key}"

    return run


def _page(
    items: list[tuple[str, int, bool]],
    total: int | None = None,
    incomplete: bool = False,
) -> dict:
    return {
        "total_count": len(items) if total is None else total,
        "incomplete_results": incomplete,
        "items": [
            {
                "repository_url": f"https://api.github.com/repos/own/{n}",
                "number": k,
                **({"pull_request": {}} if p else {}),
            }
            for n, k, p in items
        ],
    }


OPEN = "api -X GET search/issues -f q=user:own is:open"
ISSUE = json.dumps(
    {
        "title": "t",
        "body": "b",
        "state": "OPEN",
        "stateReason": None,
        "author": {"login": "u"},
        "labels": [{"name": "inbox"}],
        "updatedAt": "2026-09-28T00:00:00Z",
        "url": "https://x/1",
    }
)
PR = json.dumps(
    {
        "title": "p",
        "body": "",
        "state": "OPEN",
        "mergedAt": None,
        "author": {"login": "u"},
        "labels": [],
        "updatedAt": "",
        "url": "",
        "closingIssuesReferences": [{"number": 1, "repository": {"name": "a"}}],
        "headRefOid": "abc",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"__typename": "CheckRun", "conclusion": "SUCCESS"}],
    }
)
PR_EXTRA = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": "abc",
                    "latestReviews": {
                        "totalCount": 1,
                        "nodes": [{"state": "APPROVED", "commit": {"oid": "old"}}],
                    },
                    "files": {"totalCount": 1, "nodes": [{"path": ".github/x.yml"}]},
                }
            }
        }
    }
)
COMMENTS = json.dumps(
    [[{"user": {"login": "u"}, "body": "c", "created_at": "2026-09-28T00:00:00Z"}]]
)


def test_offline_is_error() -> None:
    result = collect_gh(
        "own", {}, lambda _: set(), fake({OPEN: (1, "", "not logged in")})
    )
    assert result.state == "error" and "not logged in" in result.detail


def test_incomplete_results_is_error() -> None:
    run = fake({OPEN: (0, json.dumps([_page([("a", 1, False)], incomplete=True)]), "")})
    assert discover("own", {"a"}, run)[1] == "error"


def test_total_count_above_received_is_error() -> None:
    run = fake({OPEN: (0, json.dumps([_page([("a", 1, False)], total=5)]), "")})
    assert discover("own", {"a"}, run)[1] == "error"


def test_non_fleet_repos_are_ignored() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 1, False), ("zzz", 2, False)])]), ""),
        }
    )
    hits, state, _ = discover("own", {"a"}, run)
    assert state == "read" and hits == [("a", 1, False)]


def test_collects_pr_ci_and_follows_refs() -> None:
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
            "pr view 2 -R own/a": (0, PR, ""),
            "api graphql": (0, PR_EXTRA, ""),
            "issue view 1 -R own/a": (0, ISSUE, ""),
            "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
            "api --paginate --slurp repos/own/a/issues/1/comments": (0, COMMENTS, ""),
        }
    )
    result = collect_gh("own", {"a": "a"}, lambda _: {("a", 1)}, run)
    assert result.state == "read"
    by_number = {r["number"]: r for r in result.records}
    assert by_number[2]["ci"] == "green" and by_number[2]["head_sha"] == "abc"
    assert by_number[2]["approved_at_head"] is False  # одобрен старый SHA
    assert by_number[2]["files"] == [".github/x.yml"] and by_number[2]["complete"]
    assert by_number[2]["closing_refs"] == ["a#1"]
    assert by_number[1]["labels"] == ["inbox"] and by_number[1]["repo"] == "a"


def test_head_moved_between_reads_is_error() -> None:
    moved = PR_EXTRA.replace('"headRefOid": "abc"', '"headRefOid": "new"')
    run = fake(
        {
            OPEN: (0, json.dumps([_page([("a", 2, True)])]), ""),
            "pr view 2 -R own/a": (0, PR, ""),
            "api graphql": (0, moved, ""),
            "api --paginate --slurp repos/own/a/issues/2/comments": (0, COMMENTS, ""),
        }
    )
    assert collect_gh("own", {"a": "a"}, lambda _: set(), run).state == "error"


def test_ci_state_table() -> None:
    assert ci_state(None) == "unknown"
    assert ci_state([{"__typename": "CheckRun", "conclusion": "FAILURE"}]) == "red"
    assert ci_state([{"__typename": "StatusContext", "state": "PENDING"}]) == "pending"
    assert (
        ci_state(
            [
                {"__typename": "CheckRun", "conclusion": "SUCCESS"},
                {"__typename": "StatusContext", "state": "SUCCESS"},
            ]
        )
        == "green"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_sources_gh.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.sources_gh'`

- [ ] **Step 3: Write minimal implementation**

`conductor/sources_gh.py`:
```python
"""GitHub в два шага: поиск с проверкой полноты, адресное дочитывание (§3.1).

Только чтение: ни одна команда здесь не мутирует GitHub (срез 0). Поиск идёт
через REST `search/issues`, потому что только он отдаёт `incomplete_results`
и `total_count` — `gh search --json` их теряет. Обнаруживаются только
открытые узлы: закрытые, на которые кто-то ссылается, приходят адресным
дочитыванием независимо от возраста (окно закрытых упиралось бы в потолок
поиска в 1000 — замер 2026-09-29: 1310 закрытых за 30 дней).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from conductor.model import SourceState

GH_TIMEOUT = 120
Runner = Callable[[list[str]], tuple[int, str, str]]
ISSUE_FIELDS = "title,body,state,stateReason,author,labels,updatedAt,url"
PR_FIELDS = (
    "title,body,state,mergedAt,author,labels,updatedAt,url,"
    "closingIssuesReferences,headRefOid,reviewDecision,statusCheckRollup"
)
PR_EXTRA = (
    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
    "{pullRequest(number:$k){headRefOid latestReviews(first:50)"
    "{totalCount nodes{state commit{oid}}}"
    " files(first:100){totalCount nodes{path}}}}}"
)
RED = {
    "FAILURE",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """Настоящий вызов gh; нет бинаря/таймаут — код 127."""
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            check=False,
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


def ci_state(rollup: list[dict[str, Any]] | None) -> str:
    """Сводка проверок head SHA: red > pending > green; пусто — unknown."""
    if not rollup:
        return "unknown"
    states = [
        (
            (
                c.get("state")
                if c.get("__typename") == "StatusContext"
                else c.get("conclusion") or c.get("status")
            )
            or ""
        ).upper()
        for c in rollup
    ]
    if any(s in RED for s in states):
        return "red"
    return "green" if all(s in GREEN for s in states) else "pending"


def _search(
    owner: str, qualifier: str, runner: Runner
) -> tuple[list[dict[str, Any]] | None, str]:
    code, out, err = runner(
        [
            "api",
            "-X",
            "GET",
            "search/issues",
            "-f",
            f"q=user:{owner} {qualifier}",
            "-f",
            "per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    if code != 0:
        return None, err.strip() or f"gh api exit {code}"
    pages = json.loads(out or "[]")
    items = [item for page in pages for item in page.get("items", [])]
    total = pages[0].get("total_count", 0) if pages else 0
    if any(page.get("incomplete_results") for page in pages):
        return None, f"поиск «{qualifier}»: incomplete_results"
    if total > len(items):
        return None, f"поиск «{qualifier}»: получено {len(items)} из {total}"
    return items, ""


def discover(
    owner: str, fleet_names: set[str], runner: Runner
) -> tuple[list[tuple[str, int, bool]], SourceState, str]:
    """Открытые issues/PR репо флота (закрытые — адресным дочитыванием)."""
    items, problem = _search(owner, "is:open", runner)
    if items is None:
        return [], "error", problem
    hits = [
        (name, item["number"], "pull_request" in item)
        for item in items
        if (name := item["repository_url"].rsplit("/", 1)[-1]) in fleet_names
    ]
    return hits, "read", ""


def _comments(
    owner: str, name: str, number: int, runner: Runner
) -> list[dict[str, str]] | None:
    code, out, _ = runner(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{owner}/{name}/issues/{number}/comments",
        ]
    )
    if code != 0:
        return None
    return [
        {
            "author": (c.get("user") or {}).get("login", ""),
            "body": c.get("body") or "",
            "created_at": c.get("created_at", ""),
        }
        for page in json.loads(out or "[]")
        for c in page
    ]


def _pr_extra(
    owner: str, name: str, number: int, runner: Runner
) -> dict[str, Any] | None:
    """Одобрение именно head SHA и изменённые файлы (gh pr view их не отдаёт)."""
    code, out, _ = runner(
        [
            "api",
            "graphql",
            "-f",
            f"query={PR_EXTRA}",
            "-f",
            f"o={owner}",
            "-f",
            f"n={name}",
            "-F",
            f"k={number}",
        ]
    )
    if code != 0:
        return None
    pr = json.loads(out)["data"]["repository"]["pullRequest"]
    head = pr["headRefOid"]
    reviews = pr["latestReviews"]["nodes"]
    approved = any(
        r["state"] == "APPROVED" and (r.get("commit") or {}).get("oid") == head
        for r in reviews
    ) and not any(r["state"] == "CHANGES_REQUESTED" for r in reviews)
    files = [f["path"] for f in pr["files"]["nodes"]]
    return {
        "extra_head": head,
        "approved_at_head": approved,
        "files": files,
        "complete": pr["files"]["totalCount"] <= len(files)
        and pr["latestReviews"]["totalCount"] <= len(reviews),
    }


def fetch_record(
    owner: str, name: str, number: int, is_pr: bool, runner: Runner
) -> dict[str, Any] | None:
    """Одна запись (без поля repo) или None при любом сбое чтения."""
    kind, fields = ("pr", PR_FIELDS) if is_pr else ("issue", ISSUE_FIELDS)
    code, out, _ = runner(
        [kind, "view", str(number), "-R", f"{owner}/{name}", "--json", fields]
    )
    comments = _comments(owner, name, number, runner) if code == 0 else None
    if comments is None:
        return None
    raw = json.loads(out)
    record: dict[str, Any] = {
        "number": number,
        "is_pr": is_pr,
        "title": raw.get("title", ""),
        "body": raw.get("body") or "",
        "state": "open" if raw.get("state") == "OPEN" else "closed",
        "state_reason": (raw.get("stateReason") or "").lower() or None,
        "merged": bool(raw.get("mergedAt")),
        "author": (raw.get("author") or {}).get("login", ""),
        "labels": [lab["name"] for lab in raw.get("labels", [])],
        "updated_at": raw.get("updatedAt", ""),
        "url": raw.get("url", ""),
        "comments": comments,
        "closing_refs": [
            f"{r['repository']['name']}#{r['number']}"
            for r in raw.get("closingIssuesReferences") or []
        ],
    }
    if is_pr:
        extra = _pr_extra(owner, name, number, runner)
        # CI (из pr view) и одобрение (из GraphQL) — об одном и том же SHA,
        # иначе готовность к мержу не доказана: чтение считается сбоем.
        if extra is None or extra.pop("extra_head") != raw.get("headRefOid"):
            return None
        record.update(
            head_sha=raw.get("headRefOid"),
            review_decision=raw.get("reviewDecision"),
            ci=ci_state(raw.get("statusCheckRollup")),
            **extra,
        )
    return record


def collect_gh(
    owner: str,
    names_to_keys: dict[str, str],
    extra_refs: Callable[[list[dict[str, Any]]], set[tuple[str, int]]],
    runner: Runner,
    max_hops: int = 3,
) -> GhResult:
    """Обнаружение + дочитывание ссылок до неподвижной точки (≤ max_hops)."""
    hits, state, detail = discover(owner, set(names_to_keys), runner)
    if state != "read":
        return GhResult([], state, detail)
    key_to_name = {key: name for name, key in names_to_keys.items()}
    records: dict[tuple[str, int], dict[str, Any]] = {}
    queue = {(name, number): is_pr for name, number, is_pr in hits}
    for _ in range(max_hops + 1):
        for (name, number), is_pr in sorted(queue.items()):
            record = fetch_record(owner, name, number, is_pr, runner)
            if record is None and not is_pr:
                record = fetch_record(owner, name, number, True, runner)
            if record is None:
                return GhResult(
                    list(records.values()), "error", f"не дочитан {name}#{number}"
                )
            record["repo"] = names_to_keys.get(name, name)
            records[(name, number)] = record
        wanted = {
            (key_to_name.get(key, key), number)
            for key, number in extra_refs(list(records.values()))
        }
        queue = {ref: False for ref in wanted - set(records) if ref[0] in names_to_keys}
        if not queue:
            return GhResult(list(records.values()), "read", "")
    return GhResult(
        list(records.values()), "error", f"ссылки не сошлись за {max_hops} шага"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_sources_gh.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/sources_gh.py tests/conductor/test_sources_gh.py
git commit -m "feat(conductor): GitHub-источник с проверкой полноты и CI PR"
```

---

### Task 6: Граф — узлы, рёбра, склейка, нормализация имён

**Files:**
- Create: `conductor/graph.py`, `tests/conductor/fixtures.py`, `tests/conductor/test_graph.py`

**Interfaces:**
- Consumes: Tasks 1, 3; `plan_fields.parse_fleet`, `plan_fields.RepoInput`
- Produces:
  - `Graph(nodes, edges, canon, records, todo_sha, sources, findings, partial)`; методы `resolve(node_id) -> str`, `members(node_id) -> set[str]`, `out(node_id, type)`, `into(node_id, type)`, `epic_of(node_id) -> str | None`
  - `build_graph(inputs: Inputs) -> Graph` (индекс — из `inputs.manifest_text`)
  - `field_value(body, name) -> str | None`
  - `normalizer(inputs) -> dict[str, str]` — GitHub-имя и ключ → ключ
  - `referenced_issues(records, todos, norm) -> set[(key, number)]`
- Test fixtures (`tests/conductor/fixtures.py`): `REPOS`, `EPICS`, `ROADMAP`, `MANIFEST_TEXT`, `record(repo, number, **fields)`, `inputs(todos, records=(), roadmap=ROADMAP, epics=None, movement=None, gh_state="read", **extra)`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/fixtures.py`:
```python
"""Общие фикстуры флота для тестов conductor."""

from __future__ import annotations

from typing import Any

from conductor.inputs import Inputs, RepoTodo

REPOS = (
    "a",
    "b",
    "c",
    "devtools",
    "spec-runner",
    "arbiter",
    "deployer",
    "ecosystem-kb",
)
GITHUB_NAME = {"ecosystem-kb": "prograph-vault"}
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
MANIFEST_TEXT = "".join(
    f'[cores.{r}]\nrepo_url = "git@github.com:own/{GITHUB_NAME.get(r, r)}.git"\n'
    f'git_dir = "{GITHUB_NAME.get(r, r)}"\n'
    for r in REPOS
)


def record(repo: str, number: int, **fields: Any) -> dict[str, Any]:
    """GhRecord с разумными умолчаниями (PR — review/ci неизвестны)."""
    base: dict[str, Any] = {
        "repo": repo,
        "number": number,
        "is_pr": False,
        "title": f"{repo}#{number}",
        "body": "",
        "state": "open",
        "state_reason": None,
        "merged": False,
        "author": "own",
        "labels": [],
        "updated_at": "2026-09-28T00:00:00Z",
        "url": "",
        "comments": [],
        "closing_refs": [],
    }
    if fields.get("is_pr"):
        base.update(
            head_sha="h",
            review_decision="REVIEW_REQUIRED",
            ci="unknown",
            approved_at_head=False,
            files=[],
            complete=True,
        )
    base.update(fields)
    return base


def inputs(
    todos: dict[str, str],
    records: Any = (),
    roadmap: str | None = ROADMAP,
    epics: dict | None = None,
    movement: dict[str, str] | None = None,
    gh_state: str = "read",
    **extra: Any,
) -> Inputs:
    """Inputs из текстов TODO (прочие репо — absent)."""
    base = Inputs(
        captured_at="2026-09-29T12:00:00Z",
        host="test",
        owner="own",
        manifest_text=MANIFEST_TEXT,
        todos=[
            RepoTodo(r, todos.get(r), "sha-" + r, "read" if r in todos else "absent")
            for r in REPOS
        ],
        gh_records=list(records),
        gh_state=gh_state,  # type: ignore[arg-type]
        gh_detail="",
        roadmap_text=roadmap,
        roadmap_state="read" if roadmap is not None else "absent",
        roadmap_source="origin",
        roadmap_sha="sha-rm",
        epics=EPICS if epics is None else epics,
        epics_state="read",
        epics_detail="",
        repo_names={GITHUB_NAME.get(r, r): r for r in REPOS},
        movement=movement or {},
    )
    for key, value in extra.items():
        setattr(base, key, value)
    return base
```

`tests/conductor/test_graph.py`:
```python
from conductor.graph import build_graph, field_value, normalizer, referenced_issues
from tests.conductor.fixtures import inputs, record


def test_todo_edges_including_missing_and_legacy_issue() -> None:
    g = build_graph(
        inputs(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n"
                "- [ ] z @owner:TBD @id:z @blocked_by:spec-runner#603\n"
                "- [ ] m @owner:TBD @id:m @blocked_by:todo://b/nope\n",
                "b": "- [x] y @owner:TBD @id:y\n",
            },
            [record("spec-runner", 603)],
        )
    )
    deps = {(e.src, e.dst) for e in g.edges if e.type == "depends_on"}
    assert {
        ("todo://a/x", "todo://b/y"),
        ("todo://a/z", "spec-runner#603"),
        ("todo://a/m", "todo://b/nope"),
    } <= deps
    assert g.nodes["todo://b/y"].closed_as == "completed"
    assert not g.partial


def test_inbox_glue_from_edge_backticks_crlf_and_orphan() -> None:
    body = "slug: `deploy-action-decision-tool`\r\nfrom: `deployer#need-policy`\r\n"
    g = build_graph(
        inputs(
            {
                "arbiter": "- [ ] t @owner:TBD @id:deploy-action-decision-tool "
                "@epic:eco.focus1\n",
                "deployer": "- [ ] n @owner:TBD @id:need-policy\n",
            },
            [
                record("arbiter", 104, body=body, labels=["inbox"]),
                record(
                    "arbiter",
                    105,
                    body="slug: q\nfrom: deployer#gone\n",
                    labels=["inbox"],
                ),
            ],
        )
    )
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    target = "todo://arbiter/deploy-action-decision-tool"
    assert ("arbiter#104", target, "accepted_as") in edges
    assert ("todo://deployer/need-policy", "arbiter#104", "depends_on") in edges
    assert g.resolve("arbiter#104") == target
    assert g.members(target) == {target, "arbiter#104"}
    assert g.epic_of("arbiter#104") == "eco.focus1"
    assert "GR-ORPHAN-REQUEST" in {f.code for f in g.findings}
    assert "GR-SLUG-MATCH" in {f.code for f in g.findings}


def test_orphan_forms_and_source_ref_link() -> None:
    g = build_graph(
        inputs(
            {"arbiter": "- [ ] t @owner:TBD @id:t @source-ref:arbiter#1\n"},
            [
                record(
                    "arbiter", 1, body="slug: t\nfrom: deployer\n", labels=["inbox"]
                ),
                record("arbiter", 2, body="slug: q\n", labels=["inbox"]),
                record("arbiter", 3, body="slug: q\nfrom: ghost#x\n", labels=["inbox"]),
            ],
        )
    )
    by = {(f.code, f.subject) for f in g.findings}
    assert ("GR-ORPHAN-REQUEST", "arbiter#2") in by
    assert ("GR-ORPHAN-REQUEST", "arbiter#3") in by
    assert ("GR-ORPHAN-REQUEST", "arbiter#1") not in by  # report без пункта
    assert ("GR-SLUG-MATCH", "arbiter#1") not in by  # связь через @source-ref


def test_pr_implements_mentions_and_github_name_normalization() -> None:
    g = build_graph(
        inputs(
            {"a": "- [ ] x @owner:TBD @id:x\n"},
            [
                record(
                    "a",
                    5,
                    is_pr=True,
                    body="делает @id:x",
                    closing_refs=["prograph-vault#3"],
                ),
                record(
                    "a",
                    6,
                    comments=[
                        {
                            "author": "u",
                            "body": "см. prograph-vault#3",
                            "created_at": "",
                        }
                    ],
                ),
                record("ecosystem-kb", 3),
            ],
        )
    )
    edges = {(e.src, e.dst, e.type) for e in g.edges}
    assert ("a!5", "todo://a/x", "implements") in edges
    assert ("a!5", "ecosystem-kb#3", "implements") in edges
    assert ("a#6", "ecosystem-kb#3", "mentions") in edges


def test_field_value_strips_quotes() -> None:
    assert field_value("from: `deployer`\r\n", "from") == "deployer"
    assert field_value("no fields", "slug") is None


def test_referenced_issues_normalizes() -> None:
    inp = inputs({"a": "- [ ] z @owner:TBD @id:z @blocked_by:b#3\n"})
    recs = [record("a", 1, body="see prograph-vault#4", closing_refs=["c#5"])]
    got = referenced_issues(recs, inp.todos, normalizer(inp))
    assert {("b", 3), ("ecosystem-kb", 4), ("c", 5)} <= got


def test_any_unread_source_makes_graph_partial() -> None:
    todos = {"a": "- [ ] x @owner:TBD @id:x\n"}
    assert build_graph(inputs(todos, gh_state="error")).partial
    assert build_graph(inputs(todos, epics_state="error")).partial
    assert build_graph(inputs(todos, roadmap_state="error")).partial
    assert build_graph(inputs(todos, aux_state="error")).partial
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_graph.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.graph'`

- [ ] **Step 3: Write minimal implementation**

`conductor/graph.py`:
```python
"""Единый граф: узлы TODO/issue/PR, типизированные рёбра, склейка (§3.1–3.2).

Все концы рёбер — канонические ключи манифеста: GitHub-имя (`prograph-vault`)
нормализуется к ключу (`ecosystem-kb`). Склейка accepted_as делает issue и
пункт одним узлом работы: resolve() даёт представителя (пункт), members() —
всё множество.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import plan_fields as pf

from conductor.inputs import Inputs, RepoTodo
from conductor.manifest import manifest_index
from conductor.model import (
    Edge,
    EdgeType,
    Finding,
    Node,
    Source,
    issue_id,
    item_id,
    pr_id,
)

REF_RE = re.compile(r"(?<![\w/.-])([a-z0-9][a-z0-9-]*)#(\d+)\b")
TODO_REF_RE = re.compile(r"todo://([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9._-]{0,63})")
TODO_URI_RE = re.compile(r"^todo://[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,63}$")
LEGACY_ISSUE_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)#(\d+)$")
PR_ITEM_RE = re.compile(r"@id:([a-z0-9][a-z0-9._-]{0,63})")


@dataclass
class Graph:
    """Граф прогона; partial — хотя бы один источник не прочитан (I6)."""

    nodes: dict[str, Node]
    edges: list[Edge]
    canon: dict[str, str] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    todo_sha: dict[str, str | None] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    partial: bool = False

    def resolve(self, node_id: str) -> str:
        """Представитель узла работы (пункт для принятого issue)."""
        return self.canon.get(node_id, node_id)

    def members(self, node_id: str) -> set[str]:
        """Все узлы, склеенные с node_id в один узел работы."""
        rep = self.resolve(node_id)
        return {rep} | {k for k, v in self.canon.items() if v == rep}

    def out(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Исходящие рёбра типа kind."""
        return [e for e in self.edges if e.src == node_id and e.type == kind]

    def into(self, node_id: str, kind: EdgeType) -> list[Edge]:
        """Входящие рёбра типа kind."""
        return [e for e in self.edges if e.dst == node_id and e.type == kind]

    def epic_of(self, node_id: str) -> str | None:
        """Эпик узла работы (у принятого issue — эпик пункта)."""
        node = self.nodes.get(self.resolve(node_id))
        return node.epic if node is not None else None


def field_value(body: str, name: str) -> str | None:
    """Значение `name:` в теле; бэктики, кавычки и \\r снимаются."""
    match = re.search(rf"(?im)^\s*{name}:\s*(.+?)\s*$", body.replace("\r", ""))
    return match.group(1).strip("`'\" ") if match else None


def normalizer(inputs: Inputs) -> dict[str, str]:
    """GitHub-имя и канонический ключ → канонический ключ."""
    norm = {todo.repo: todo.repo for todo in inputs.todos}
    norm.update(inputs.repo_names)
    return norm


def _norm_ref(ref: str, norm: dict[str, str]) -> str | None:
    repo, _, number = ref.partition("#")
    if repo in norm and number.isdigit():
        return issue_id(norm[repo], int(number))
    return None


def _item_nodes(snapshot: dict[str, Any]) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for raw in snapshot["nodes"]:
        closed = raw["declared_status"] != "open"
        owner = raw.get("owner_ref")
        nodes[raw["node_id"]] = Node(
            raw["node_id"],
            "item",
            raw["repo"],
            raw["title"],
            is_open=not closed,
            closed_as="completed" if closed else None,
            epic=raw.get("epic"),
            owner_ref=tuple(sorted(owner.items())) if owner else None,
            trigger=raw.get("trigger"),
            source_ref=(raw.get("freshness") or {}).get("source_ref"),
        )
    return nodes


def _todo_edges(snapshot: dict[str, Any]) -> list[Edge]:
    """depends_on из references: plan-fields не строит edges на несуществующий
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
            edges.append(
                Edge(ref["source_node_id"], target, "depends_on", f"todo:{raw}")
            )
    return edges


def _gh_id(rec: dict[str, Any]) -> str:
    return (pr_id if rec["is_pr"] else issue_id)(rec["repo"], rec["number"])


def _gh_node(rec: dict[str, Any]) -> Node:
    if rec["state"] == "open":
        closed_as = None
    elif rec["is_pr"]:
        closed_as = "merged" if rec["merged"] else "unmerged"
    else:
        closed_as = rec.get("state_reason") or "completed"
    return Node(
        _gh_id(rec),
        "pr" if rec["is_pr"] else "issue",
        rec["repo"],
        rec["title"],
        is_open=rec["state"] == "open",
        closed_as=closed_as,
        author=rec.get("author"),
        updated_at=rec.get("updated_at"),
        body=rec.get("body", ""),
        labels=tuple(rec.get("labels", [])),
        url=rec.get("url", ""),
    )


def _gh_edges(
    rec: dict[str, Any],
    nodes: dict[str, Node],
    norm: dict[str, str],
    findings: list[Finding],
) -> list[Edge]:
    me = _gh_id(rec)
    edges: list[Edge] = []
    if rec["is_pr"]:
        edges += [
            Edge(me, target, "implements", "pr:closes")
            for ref in rec.get("closing_refs", [])
            if (target := _norm_ref(ref, norm)) is not None
        ]
        edges += [
            Edge(me, item_id(rec["repo"], m), "implements", "pr:@id")
            for m in PR_ITEM_RE.findall(rec.get("body", ""))
        ]
    if "inbox" in rec.get("labels", []):
        edges += _inbox_edges(rec, me, nodes, norm, findings)
    return edges


def _inbox_edges(
    rec: dict[str, Any],
    me: str,
    nodes: dict[str, Node],
    norm: dict[str, str],
    findings: list[Finding],
) -> list[Edge]:
    """Наследие ADR-ECO-006: склейка по slug (D2), ожидание отправителя по from."""
    edges: list[Edge] = []
    slug = field_value(rec["body"], "slug")
    target = item_id(rec["repo"], slug) if slug else None
    if target is not None and target in nodes:
        edges.append(Edge(me, target, "accepted_as", "inbox:slug"))
        own = {
            f"{name}#{rec['number']}"
            for name, key in norm.items()
            if key == rec["repo"]
        }
        if nodes[target].source_ref not in own:
            findings.append(
                Finding(
                    "GR-SLUG-MATCH",
                    "info",
                    me,
                    f"{target}: принят по slug без @source-ref",
                )
            )
    sender = field_value(rec["body"], "from") or ""
    repo, _, waiting = sender.partition("#")
    orphan = None
    if not sender:
        orphan = "нет from:"
    elif repo not in norm:
        orphan = f"from: {sender} — неизвестный репо"
    elif waiting and item_id(norm[repo], waiting) not in nodes:
        orphan = f"from: {sender} — ждущего пункта нет"
    elif waiting:
        edges.append(Edge(item_id(norm[repo], waiting), me, "depends_on", "inbox:from"))
    if orphan is not None:
        findings.append(Finding("GR-ORPHAN-REQUEST", "warning", me, orphan))
    return edges


def _text_of(rec: dict[str, Any]) -> str:
    comments = "\n".join(c["body"] for c in rec.get("comments", []))
    return rec.get("body", "") + "\n" + comments


def _mentions(
    rec: dict[str, Any], strong: set[tuple[str, str]], norm: dict[str, str]
) -> list[Edge]:
    me = _gh_id(rec)
    text = _text_of(rec)
    targets = {issue_id(norm[r], int(n)) for r, n in REF_RE.findall(text) if r in norm}
    targets |= {item_id(norm[r], i) for r, i in TODO_REF_RE.findall(text) if r in norm}
    return [
        Edge(me, t, "mentions", "body")
        for t in sorted(targets)
        if t != me and (me, t) not in strong and (t, me) not in strong
    ]


def referenced_issues(
    records: list[dict[str, Any]], todos: list[RepoTodo], norm: dict[str, str]
) -> set[tuple[str, int]]:
    """Все repo#N из TODO, тел, комментариев и closing_refs — в ключах."""
    refs: set[tuple[str, int]] = set()
    for todo in todos:
        refs |= {
            (norm[r], int(n))
            for r, n in re.findall(r"@blocked_by:([a-z0-9-]+)#(\d+)", todo.text or "")
            if r in norm
        }
    for rec in records:
        text = _text_of(rec) + " " + " ".join(rec.get("closing_refs", []))
        refs |= {(norm[r], int(n)) for r, n in REF_RE.findall(text) if r in norm}
    return refs


def _sources(inputs: Inputs) -> list[Source]:
    sources = [Source(f"todo:{t.repo}", t.state, t.detail, t.sha) for t in inputs.todos]
    sources += [
        Source("github", inputs.gh_state, inputs.gh_detail),
        Source(
            "roadmap", inputs.roadmap_state, inputs.roadmap_source, inputs.roadmap_sha
        ),
        Source("epics", inputs.epics_state, inputs.epics_detail, inputs.epics_sha),
        Source("history", inputs.aux_state, inputs.aux_detail),
        Source("manifest", "read", inputs.manifest_source, inputs.manifest_sha),
    ]
    return sources


def build_graph(inputs: Inputs) -> Graph:
    """Граф из входов; ядро без ввода-вывода (индекс — из текста манифеста)."""
    repo_inputs = [
        pf.RepoInput(
            t.repo,
            todo_text=t.text or "",
            commit=t.sha,
            available=t.state in ("read", "absent"),
        )
        for t in inputs.todos
    ]
    snapshot = pf.parse_fleet(repo_inputs, manifest_index(inputs.manifest_text))
    norm = normalizer(inputs)
    nodes = _item_nodes(snapshot)
    edges = _todo_edges(snapshot)
    findings: list[Finding] = []
    records = {_gh_id(rec): rec for rec in inputs.gh_records}
    nodes.update({node_id: _gh_node(rec) for node_id, rec in records.items()})
    for rec in inputs.gh_records:
        edges += _gh_edges(rec, nodes, norm, findings)
    strong = {(e.src, e.dst) for e in edges}
    for rec in inputs.gh_records:
        edges += _mentions(rec, strong, norm)
    sources = _sources(inputs)
    return Graph(
        nodes=nodes,
        edges=sorted(set(edges), key=lambda e: (e.src, e.dst, e.type)),
        canon={e.src: e.dst for e in edges if e.type == "accepted_as"},
        records=records,
        todo_sha={t.repo: t.sha for t in inputs.todos},
        sources=sources,
        findings=findings,
        partial=any(s.state in ("error", "not_queried") for s in sources),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_graph.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/graph.py tests/conductor/fixtures.py tests/conductor/test_graph.py
git commit -m "feat(conductor): единый граф — рёбра, склейка, нормализация (§3.1–3.2)"
```

---

### Task 7: Ожидания — предпосылки, `@trigger`, застой

**Files:**
- Create: `conductor/waits.py`, `tests/conductor/test_waits.py`

**Interfaces:**
- Consumes: `Graph` (6), `Inputs` (3)
- Produces: `prereq_state(graph, inputs, prereq_id) -> "done"|"cancelled"|"open"|"missing"`; `Wait(consumer, prereq, verdict, reason, evidence, since, moved)`; `evaluate_waits(graph, inputs, now, stale_after_days) -> list[Wait]`; `waits_of(waits, consumer)`; `last_movement(graph, inputs, node_id) -> str | None`; `DATE_RE`, `EXISTS_RE`.

Семантика (§3.4): потребитель и предпосылка — через `graph.resolve`; `cancelled` — отсутствующий пункт с `@id` в истории TODO (`inputs.history`), issue `not_planned`, PR без мержа; `stale` — `pending`, последнее движение предпосылки (коммит с `@id`, `updated_at` и комментарии её тредов, `updated_at` открытых PR, реализующих любой её член) старше порога **и** само ожидание не моложе порога; неизвестная дата движения — не застой (ложный пинок хуже пропущенного).

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_waits.py`:
```python
from conductor.graph import build_graph
from conductor.waits import evaluate_waits, waits_of
from tests.conductor.fixtures import inputs, record

NOW = "2026-09-29T12:00:00Z"


def _w(todos, records=(), **extra):
    inp = inputs(todos, records, **extra)
    return evaluate_waits(build_graph(inp), inp, NOW, stale_after_days=3)


def _one(waits, consumer):
    got = waits_of(waits, consumer)
    assert len(got) == 1, got
    return got[0]


def test_done_item_satisfies_with_sha_evidence() -> None:
    w = _one(
        _w(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                "b": "- [x] y @owner:TBD @id:y\n",
            }
        ),
        "todo://a/x",
    )
    assert (w.verdict, w.reason, w.evidence) == (
        "satisfied",
        "done",
        "todo://b/y@sha-b",
    )


def test_glued_issue_resolves_to_item() -> None:
    w = _one(
        _w(
            {
                "a": "- [ ] x @owner:TBD @id:x @blocked_by:b#3\n",
                "b": "- [x] q @owner:TBD @id:q\n",
            },
            [record("b", 3, body="slug: q\n", labels=["inbox"])],
        ),
        "todo://a/x",
    )
    assert (w.prereq, w.verdict) == ("todo://b/q", "satisfied")


def test_cancelled_forms() -> None:
    w = _w(
        {
            "a": "- [ ] x @owner:TBD @id:x @blocked_by:b#3\n"
            "- [ ] y @owner:TBD @id:y @blocked_by:todo://b/gone\n"
            "- [ ] z @owner:TBD @id:z @blocked_by:todo://b/never\n"
        },
        [record("b", 3, state="closed", state_reason="not_planned")],
        history={"todo://b/gone": "deadbeef"},
    )
    assert _one(w, "todo://a/x").reason == "cancelled"
    assert _one(w, "todo://a/y").reason == "cancelled"
    assert _one(w, "todo://a/z").reason == "missing"
    assert all(x.verdict == "unknown" for x in w)


def test_stale_needs_old_wait_and_no_movement() -> None:
    todos = {
        "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }
    old_wait = {"todo://a/x|todo://b/y": "2026-09-01T00:00:00Z"}
    quiet = _one(
        _w(todos, movement={"todo://b/y": "2026-09-20T00:00:00Z"}, wait_since=old_wait),
        "todo://a/x",
    )
    assert quiet.reason == "stale"
    fresh_wait = _one(
        _w(
            todos,
            movement={"todo://b/y": "2026-09-20T00:00:00Z"},
            wait_since={"todo://a/x|todo://b/y": "2026-09-28T00:00:00Z"},
        ),
        "todo://a/x",
    )
    assert fresh_wait.reason == "open"
    busy = _one(
        _w(
            todos,
            [
                record(
                    "b", 9, is_pr=True, body="@id:y", updated_at="2026-09-29T00:00:00Z"
                )
            ],
            movement={"todo://b/y": "2026-09-20T00:00:00Z"},
            wait_since=old_wait,
        ),
        "todo://a/x",
    )
    assert busy.reason == "open"
    unknown = _one(_w(todos, wait_since=old_wait), "todo://a/x")
    assert unknown.reason == "open"
    no_since = _one(
        _w(todos, movement={"todo://b/y": "2026-09-01T00:00:00Z"}), "todo://a/x"
    )
    assert no_since.reason == "open"


def test_triggers() -> None:
    facts = {
        "exists:b:contracts/v1/x.json": {
            "exists": False,
            "sha": "s",
            "siblings": ["v1", "v2"],
        },
        "exists:b:docs/y.md": {"exists": True, "sha": "s", "siblings": []},
    }
    w = _w(
        {
            "a": '- [ ] p @owner:TBD @id:p @trigger:"когда-нибудь"\n'
            '- [ ] d @owner:TBD @id:d @trigger:"date>=2026-09-01"\n'
            '- [ ] f @owner:TBD @id:f @trigger:"date>=2027-01-01"\n'
            "- [ ] v @owner:TBD @id:v "
            '@trigger:"exists:b:contracts/v1/x.json"\n'
            '- [ ] e @owner:TBD @id:e @trigger:"exists:b:docs/y.md"\n'
        },
        trigger_facts=facts,
    )
    got = {x.consumer: (x.verdict, x.reason) for x in w}
    assert got["todo://a/p"] == ("unknown", "prose_trigger")
    assert got["todo://a/d"] == ("satisfied", "date")
    assert got["todo://a/f"] == ("pending", "date")
    assert got["todo://a/v"] == ("unknown", "version_mismatch")
    assert got["todo://a/e"] == ("satisfied", "exists")


def test_closed_consumer_has_no_waits() -> None:
    assert (
        waits_of(
            _w(
                {
                    "a": "- [x] x @owner:TBD @id:x @blocked_by:todo://b/y\n",
                    "b": "- [ ] y @owner:TBD @id:y\n",
                }
            ),
            "todo://a/x",
        )
        == []
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_waits.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.waits'`

- [ ] **Step 3: Write minimal implementation**

`conductor/waits.py`:
```python
"""Предпосылки, условия @trigger и застой (спека §3.4, срез 0 без модели)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from conductor.graph import Graph
from conductor.inputs import Inputs

PrereqState = Literal["done", "cancelled", "open", "missing"]
Verdict = Literal["satisfied", "pending", "unknown"]
DATE_RE = re.compile(r"^date>=(\d{4}-\d{2}-\d{2})$")
EXISTS_RE = re.compile(r"^exists:([a-z0-9][a-z0-9-]*):(\S+)$")
VERSION_RE = re.compile(r"^v(\d+)$")


@dataclass(frozen=True)
class Wait:
    """Ожидание consumer → prereq (prereq=None — условие @trigger)."""

    consumer: str
    prereq: str | None
    verdict: Verdict
    reason: str
    evidence: str
    since: str | None
    moved: str | None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _days(now: str, then: str) -> int:
    return (_ts(now) - _ts(then)).days


def prereq_state(graph: Graph, inputs: Inputs, prereq_id: str) -> PrereqState:
    """Таблица §3.4 для представителя узла работы."""
    node = graph.nodes.get(prereq_id)
    if node is None:
        return "cancelled" if inputs.history.get(prereq_id) else "missing"
    if node.is_open:
        return "open"
    return "done" if node.closed_as in ("completed", "merged") else "cancelled"


def last_movement(graph: Graph, inputs: Inputs, node_id: str) -> str | None:
    """Последнее движение по узлу работы: коммит с @id, треды, открытые PR."""
    stamps: list[str] = []
    for member in graph.members(node_id):
        stamps.append(inputs.movement.get(member) or "")
        rec = graph.records.get(member)
        if rec is not None:
            stamps.append(rec.get("updated_at") or "")
            stamps += [c.get("created_at") or "" for c in rec.get("comments", [])]
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is not None and pr.is_open:
                stamps.append(pr.updated_at or "")
    stamps = [s for s in stamps if s]
    return max(stamps, key=_ts) if stamps else None


def _evidence(graph: Graph, prereq_id: str) -> str:
    node = graph.nodes[prereq_id]
    if node.kind == "item":
        return f"{prereq_id}@{graph.todo_sha.get(node.repo)}"
    return node.url or prereq_id


def _dependency_wait(
    graph: Graph,
    inputs: Inputs,
    src: str,
    dst: str,
    raw: str,
    now: str,
    stale_days: int,
) -> Wait:
    consumer, prereq = graph.resolve(src), graph.resolve(dst)
    state = prereq_state(graph, inputs, prereq)
    since = inputs.wait_since.get(f"{src}|{raw}")
    moved = last_movement(graph, inputs, prereq)
    if state == "done":
        return Wait(
            consumer,
            prereq,
            "satisfied",
            "done",
            _evidence(graph, prereq),
            since,
            moved,
        )
    if state == "open":
        stale = (
            moved is not None
            and _days(now, moved) >= stale_days
            and since is not None
            and _days(now, since) >= stale_days
        )
        return Wait(
            consumer,
            prereq,
            "pending",
            "stale" if stale else "open",
            prereq,
            since,
            moved,
        )
    return Wait(consumer, prereq, "unknown", state, prereq, since, moved)


def _version_mismatch(path: str, siblings: list[str]) -> bool:
    for part in path.split("/"):
        if m := VERSION_RE.match(part):
            wanted = int(m.group(1))
            return any(
                (v := VERSION_RE.match(s)) is not None and int(v.group(1)) > wanted
                for s in siblings
            )
    return False


def _trigger_wait(consumer: str, text: str, inputs: Inputs, now: str) -> Wait:
    if m := DATE_RE.match(text):
        ok = now[:10] >= m.group(1)
        return Wait(
            consumer, None, "satisfied" if ok else "pending", "date", text, None, None
        )
    if m := EXISTS_RE.match(text):
        fact = inputs.trigger_facts.get(text)
        if fact is None or fact.get("exists") is None:
            return Wait(consumer, None, "unknown", "fact_unread", text, None, None)
        if fact["exists"]:
            return Wait(
                consumer,
                None,
                "satisfied",
                "exists",
                f"{text}@{fact.get('sha')}",
                None,
                None,
            )
        if _version_mismatch(m.group(2), fact.get("siblings", [])):
            return Wait(consumer, None, "unknown", "version_mismatch", text, None, None)
        return Wait(consumer, None, "pending", "absent_path", text, None, None)
    return Wait(consumer, None, "unknown", "prose_trigger", text, None, None)


def evaluate_waits(
    graph: Graph, inputs: Inputs, now: str, stale_after_days: int
) -> list[Wait]:
    """Все ожидания открытых узлов работы; вычисляются заново каждым прогоном."""
    waits: dict[tuple[str, str | None], Wait] = {}
    for edge in graph.edges:
        consumer = graph.nodes.get(graph.resolve(edge.src))
        if edge.type != "depends_on" or consumer is None or not consumer.is_open:
            continue
        raw = (
            edge.origin.removeprefix("todo:")
            if edge.origin.startswith("todo:")
            else edge.dst
        )
        wait = _dependency_wait(
            graph, inputs, edge.src, edge.dst, raw, now, stale_after_days
        )
        waits.setdefault((wait.consumer, wait.prereq), wait)
    for node in graph.nodes.values():
        if node.kind == "item" and node.is_open and node.trigger:
            waits[(node.node_id, None)] = _trigger_wait(
                node.node_id, node.trigger, inputs, now
            )
    return sorted(waits.values(), key=lambda w: (w.consumer, w.prereq or ""))


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
git commit -m "feat(conductor): предпосылки, @trigger и застой (§3.4)"
```

---

### Task 8: Анализ — готовность, циклы, находки, состояние работы, внимание

**Files:**
- Create: `conductor/analysis.py`, `tests/conductor/test_analysis.py`

**Interfaces:**
- Consumes: `Graph` (6), `Wait`, `waits_of` (7), `Roadmap` (2)
- Produces: `dependency_adjacency(graph) -> dict[str, set[str]]` (концы через `resolve`); `find_cycles(adj) -> list[list[str]]`; `is_ready(node_id, graph, waits) -> bool`; `work_state(node_id, graph) -> "in_review"|"idle"`; `attention_nodes(graph, waits) -> list[str]`; `findings(graph, waits, cycles, roadmap) -> list[Finding]`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_analysis.py`:
```python
from conductor.analysis import (
    attention_nodes,
    dependency_adjacency,
    find_cycles,
    findings,
    is_ready,
    work_state,
)
from conductor.graph import build_graph
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _setup(todos, records=(), **extra):
    inp = inputs(todos, records, **extra)
    g = build_graph(inp)
    return g, evaluate_waits(g, inp, NOW, 3)


def test_chain_only_leaf_ready() -> None:
    g, w = _setup(
        {
            "a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://b/b\n",
            "b": "- [ ] b @owner:TBD @id:b @blocked_by:todo://c/c\n",
            "c": "- [ ] c @owner:TBD @id:c\n",
        }
    )
    assert [
        n for n in ("todo://a/a", "todo://b/b", "todo://c/c") if is_ready(n, g, w)
    ] == ["todo://c/c"]


def test_ping_pong_through_issue_plane_is_cycle() -> None:
    g, w = _setup(
        {
            "devtools": "- [ ] o @owner:TBD @id:oracle @blocked_by:spec-runner#603\n"
            "- [ ] q @owner:TBD @id:q "
            "@blocked_by:todo://spec-runner/verify\n",
            "spec-runner": "- [ ] v @owner:TBD @id:verify @blocked_by:devtools#491\n",
        },
        [
            record(
                "spec-runner",
                603,
                body="slug: verify\nfrom: devtools#oracle\n",
                labels=["inbox"],
            ),
            record(
                "devtools",
                491,
                body="slug: q\nfrom: spec-runner#verify\n",
                labels=["inbox"],
            ),
        ],
    )
    cycles = find_cycles(dependency_adjacency(g))
    assert any(
        {"todo://devtools/q", "todo://spec-runner/verify"} <= set(c) for c in cycles
    )
    assert "GR-CYCLE" in {f.code for f in findings(g, w, cycles, None)}


def test_self_loop_and_long_chain() -> None:
    g, _ = _setup({"a": "- [ ] a @owner:TBD @id:a @blocked_by:todo://a/a\n"})
    assert find_cycles(dependency_adjacency(g)) == [["todo://a/a"]]
    assert find_cycles({f"n{i}": {f"n{i + 1}"} for i in range(5000)}) == []


def test_findings_catalogue() -> None:
    g, w = _setup(
        {
            "a": "- [x] s @owner:TBD @id:shipped\n"
            "- [ ] d @owner:TBD @id:d @blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        },
        [
            record("a", 1, body="slug: shipped\n", labels=["inbox"]),
            record("a", 2, body="упоминаю b#3"),
            record("b", 3),
        ],
    )
    rm = parse_roadmap(ROADMAP, EPICS)
    codes = {f.code for f in findings(g, w, [], rm)}
    assert {
        "GR-SHIPPED-OPEN",
        "GR-WEAK-EDGE",
        "GR-DANGLING-WAIT",
        "RM-GOAL-MISSING",
    } <= codes


def test_open_pr_on_glued_issue_puts_item_in_review() -> None:
    g, _ = _setup(
        {"a": "- [ ] x @owner:TBD @id:x\n"},
        [
            record("a", 1, body="slug: x\n", labels=["inbox"]),
            record("a", 5, is_pr=True, closing_refs=["a#1"]),
        ],
    )
    assert work_state("todo://a/x", g) == "in_review"


def test_attention_lists_unknown_waits() -> None:
    g, w = _setup(
        {
            "a": "- [ ] x @owner:TBD @id:x @blocked_by:todo://b/nope\n"
            '- [ ] t @owner:TBD @id:t @trigger:"когда-нибудь"\n',
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert attention_nodes(g, w) == ["todo://a/t", "todo://a/x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_analysis.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.analysis'`

- [ ] **Step 3: Write minimal implementation**

`conductor/analysis.py`:
```python
"""Готовность, циклы, находки, состояние работы, внимание (спека §3.3, §3.5).

В срезе 0 поколений доставки нет (ветки conductor не создаются), поэтому
состояние работы — только in_review / idle.
"""

from __future__ import annotations

from typing import Literal

from conductor.graph import Graph
from conductor.model import Finding
from conductor.roadmap import Roadmap
from conductor.waits import Wait, waits_of


def dependency_adjacency(graph: Graph) -> dict[str, set[str]]:
    """depends_on по представителям узлов работы: узел → предпосылки."""
    adj: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.type == "depends_on":
            src, dst = graph.resolve(e.src), graph.resolve(e.dst)
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

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)

    for root in sorted(adj):
        if root in index:
            continue
        visit(root)
        work = [(root, iter(sorted(adj.get(root, ()))))]
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    visit(child)
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
    """Открыт и все ожидания узла работы satisfied (§3.3.1)."""
    rep = graph.resolve(node_id)
    node = graph.nodes.get(rep)
    if node is None or not node.is_open:
        return False
    return all(w.verdict == "satisfied" for w in waits_of(waits, rep))


def work_state(node_id: str, graph: Graph) -> Literal["in_review", "idle"]:
    """in_review — открытый PR реализует любой член узла работы (§3.5)."""
    for member in graph.members(node_id):
        for e in graph.into(member, "implements"):
            pr = graph.nodes.get(e.src)
            if pr is not None and pr.is_open:
                return "in_review"
    return "idle"


def attention_nodes(graph: Graph, waits: list[Wait]) -> list[str]:
    """Открытые узлы работы, чьё ожидание unknown — нужны решения (§5.2)."""
    return sorted(
        {
            w.consumer
            for w in waits
            if w.verdict == "unknown"
            and (n := graph.nodes.get(w.consumer)) is not None
            and n.is_open
        }
    )


def findings(
    graph: Graph, waits: list[Wait], cycles: list[list[str]], roadmap: Roadmap | None
) -> list[Finding]:
    """Находки графа (§3.3.2–3.3.5) и RM-GOAL-MISSING (§2.2)."""
    found = list(graph.findings)
    found += [Finding("GR-CYCLE", "error", c[0], " → ".join(c)) for c in cycles]
    for e in graph.edges:
        src, dst = graph.nodes.get(e.src), graph.nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if e.type == "accepted_as" and src.is_open and dst.closed_as == "completed":
            found.append(
                Finding("GR-SHIPPED-OPEN", "warning", e.src, f"{e.dst} выполнен")
            )
        if (
            e.type == "implements"
            and src.closed_as == "merged"
            and dst.kind == "issue"
            and dst.is_open
        ):
            found.append(Finding("GR-SHIPPED-OPEN", "warning", e.dst, f"{e.src} влит"))
        if e.type == "mentions":
            found.append(Finding("GR-WEAK-EDGE", "info", e.src, e.dst))
    for w in waits:
        if w.reason == "missing" and (w.prereq or "").startswith("todo://"):
            found.append(
                Finding("GR-DANGLING-WAIT", "warning", w.consumer, w.prereq or "")
            )
    if roadmap is not None:
        for focus in roadmap.focus:
            if focus.goal is not None and focus.goal not in graph.nodes:
                found.append(
                    Finding("RM-GOAL-MISSING", "warning", focus.goal, focus.epic)
                )
    return sorted(set(found), key=lambda f: (f.code, f.subject, f.detail))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_analysis.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/analysis.py tests/conductor/test_analysis.py
git commit -m "feat(conductor): готовность, циклы, находки, внимание (§3.3, §3.5)"
```

---

### Task 9: Порядок работ — протекание ранга и `why`

**Files:**
- Create: `conductor/rank.py`, `tests/conductor/test_rank.py`

**Interfaces:**
- Consumes: Tasks 2, 6, 7, 8
- Produces: `QueueEntry(node_id, rank, via_focus, own_focus, klass, on_goal_path, unblocks, oldest_wait_days, why)`; `build_queue(graph, waits, roadmap, now)` (кандидаты §4.1, порядок §4.3); `build_attention(graph, waits, roadmap, now)` (узлы `attention_nodes`, тот же ключ).

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_rank.py`:
```python
from conductor.graph import build_graph
from conductor.rank import build_attention, build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"


def _q(todos, records=(), roadmap=ROADMAP):
    inp = inputs(todos, records)
    g = build_graph(inp)
    w = evaluate_waits(g, inp, NOW, 3)
    rm = parse_roadmap(roadmap, EPICS)
    return build_queue(g, w, rm, NOW), build_attention(g, w, rm, NOW)


def test_leaf_of_parked_epic_inherits_rank_1() -> None:
    queue, _ = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
            "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg @blocked_by:todo://c/c\n",
            "c": "- [ ] c @owner:TBD @id:c @epic:eco.parked\n"
            "- [ ] f2 @owner:TBD @id:f2 @epic:eco.focus2\n",
        }
    )
    assert [e.node_id for e in queue][:2] == ["todo://c/c", "todo://c/f2"]
    leaf = queue[0]
    assert (leaf.rank, leaf.klass, leaf.via_focus, leaf.own_focus) == (
        1,
        "parked",
        "eco.focus1",
        None,
    )
    assert leaf.on_goal_path and leaf.unblocks == 2
    assert "todo://a/goal" in leaf.why and "todo://b/b" in leaf.why


def test_own_focus_is_kept_when_rank_is_inherited() -> None:
    queue, _ = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 @blocked_by:todo://c/f2\n",
            "c": "- [ ] f2 @owner:TBD @id:f2 @epic:eco.focus2\n",
        }
    )
    entry = next(e for e in queue if e.node_id == "todo://c/f2")
    assert (entry.rank, entry.via_focus, entry.own_focus) == (
        1,
        "eco.focus1",
        "eco.focus2",
    )


def test_glued_issue_is_represented_by_item() -> None:
    queue, _ = _q(
        {"a": "- [ ] x @owner:TBD @id:x @epic:eco.focus2\n"},
        [record("a", 1, body="slug: x\n", labels=["inbox"])],
    )
    assert [e.node_id for e in queue] == ["todo://a/x"]


def test_unranked_last_invalid_roadmap_unranked_cycles_excluded() -> None:
    todos = {
        "a": "- [ ] x @owner:TBD @id:x @epic:eco.bg\n"
        "- [ ] y @owner:TBD @id:y @epic:eco.focus2\n"
    }
    assert [e.node_id for e in _q(todos)[0]] == ["todo://a/y", "todo://a/x"]
    assert all(e.rank is None for e in _q(todos, roadmap="bad = [")[0])
    cyc = {
        "a": "- [ ] a @owner:TBD @id:a @epic:eco.focus1 @blocked_by:todo://a/b\n"
        "- [ ] b @owner:TBD @id:b @epic:eco.focus1 @blocked_by:todo://a/a\n"
    }
    assert _q(cyc)[0] == []


def test_attention_is_ranked_too() -> None:
    _, attention = _q(
        {
            "a": "- [ ] g @owner:TBD @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert [(e.node_id, e.rank) for e in attention] == [("todo://a/goal", 1)]
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
    attention_nodes,
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
    """Позиция очереди: via_focus — источник ранга, own_focus — свой фокус."""

    node_id: str
    rank: int | None
    via_focus: str | None
    own_focus: str | None
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
    """BFS по зависящим: узел → родитель на пути от start."""
    parent: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for up in sorted(rev.get(node, ())):
            if up not in parent:
                parent[up] = node
                queue.append(up)
    return parent


def _chain(parent: dict[str, str | None], end: str) -> str:
    path = [end]
    while (step := parent[path[-1]]) is not None:
        path.append(step)
    return " → ".join(path)


def _days(now: str, since: str | None) -> int:
    if since is None:
        return 0
    delta = datetime.fromisoformat(now) - datetime.fromisoformat(since)
    return max(delta.days, 0)


def _entry(
    node_id: str,
    graph: Graph,
    waits: list[Wait],
    roadmap: Roadmap,
    rev: dict[str, set[str]],
    now: str,
) -> QueueEntry:
    parent = _walk(node_id, rev)
    best: tuple[int, str] | None = None
    if roadmap.valid:
        for other in parent:
            focus = roadmap.focus_of(graph.epic_of(other))
            if focus is not None and (best is None or focus.rank < best[0]):
                best = (focus.rank, other)
    epic = graph.epic_of(node_id)
    own = roadmap.focus_of(epic) if roadmap.valid else None
    klass = roadmap.klass(epic) if roadmap.valid else "background"
    oldest = max((_days(now, w.since) for w in waits if w.prereq == node_id), default=0)
    unblocks = len(parent) - 1
    if best is None:
        return QueueEntry(
            node_id,
            None,
            None,
            None,
            klass,
            False,
            unblocks,
            oldest,
            "без ранга: не связан с фокусом",
        )
    rank, source = best
    focus = roadmap.focus[rank - 1]
    on_goal = focus.goal is not None and focus.goal in parent
    why = (
        f"rank {rank} ({focus.epic}) via {_chain(parent, source)}; unblocks {unblocks}"
    )
    return QueueEntry(
        node_id,
        rank,
        focus.epic,
        own.epic if own else None,
        klass,
        on_goal,
        unblocks,
        oldest,
        why,
    )


def _key(e: QueueEntry) -> tuple[bool, int, bool, int, int, str]:
    return (
        e.rank is None,
        e.rank or 0,
        not e.on_goal_path,
        -e.unblocks,
        -e.oldest_wait_days,
        e.node_id,
    )


def build_queue(
    graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str
) -> list[QueueEntry]:
    """Кандидаты §4.1 (готовые, вне циклов, idle; открытые PR) в порядке §4.3."""
    adj = dependency_adjacency(graph)
    in_cycle = {n for c in find_cycles(adj) for n in c}
    rev = _reverse(adj)
    entries = []
    for node_id, node in graph.nodes.items():
        if node_id in graph.canon or node_id in in_cycle or not node.is_open:
            continue
        if node.kind != "pr" and (
            not is_ready(node_id, graph, waits) or work_state(node_id, graph) != "idle"
        ):
            continue
        entries.append(_entry(node_id, graph, waits, roadmap, rev, now))
    return sorted(entries, key=_key)


def build_attention(
    graph: Graph, waits: list[Wait], roadmap: Roadmap, now: str
) -> list[QueueEntry]:
    """Узлы, которым нужно решение (unknown-ожидание), в том же порядке."""
    rev = _reverse(dependency_adjacency(graph))
    return sorted(
        (
            _entry(n, graph, waits, roadmap, rev, now)
            for n in attention_nodes(graph, waits)
        ),
        key=_key,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_rank.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/rank.py tests/conductor/test_rank.py
git commit -m "feat(conductor): протекание приоритета, внимание и why (§4)"
```

---

### Task 10: Политика как выдача — уровень, `actor/need`, делегируемость, действие

**Files:**
- Create: `conductor/policy.py`, `tests/conductor/test_policy.py`

**Interfaces:**
- Consumes: `QueueEntry` (9), `Graph` (6), `Wait`, `waits_of` (7), `Roadmap` (2), `work_state` (8), `Inputs` (3)
- Produces:
  - `DECISION_WORDS`, `DECISION_PATH_PREFIXES`, `OUT_OF_LOOP_REPOS`
  - `Assessment(node_id, level, actor, need, delegable: "yes"|"no"|"unverified", block_reason, action, ask_owner)`
  - `position_level(entry, roadmap, run_level) -> int` — §2.4; собственный класс берёт потолок **своего** фокуса
  - `delegable(node_id, graph, inputs) -> (verdict, reason)` — владелец, признаки решения в тексте всех членов склейки, статус эпика (неизвестный — `no`), клон (TODO `error` — `no`), вне контура; всё пройдено → `unverified` (authority-root — срез 3)
  - `assess(entry, graph, waits, roadmap, run_level, inputs) -> Assessment` — §5.2–5.3; `action` — имя действия §5.1, `launch?` для `unverified`, `"—"` если нет; ничего не исполняет

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_policy.py`:
```python
from conductor.graph import build_graph
from conductor.inputs import RepoTodo
from conductor.policy import assess, delegable, position_level
from conductor.rank import build_attention, build_queue
from conductor.roadmap import parse_roadmap
from conductor.waits import evaluate_waits
from tests.conductor.fixtures import EPICS, ROADMAP, inputs, record

NOW = "2026-09-29T12:00:00Z"
R3 = ROADMAP.replace("autonomy = 0", "autonomy = 3")


def _assess(todos, records=(), roadmap=R3, level=3, **extra):
    inp = inputs(todos, records, **extra)
    g = build_graph(inp)
    w = evaluate_waits(g, inp, NOW, 3)
    rm = parse_roadmap(roadmap, EPICS)
    entries = build_queue(g, w, rm, NOW) + build_attention(g, w, rm, NOW)
    return {e.node_id: assess(e, g, w, rm, level, inp) for e in entries}


def test_focus_item_at_level_3_is_unverified_launch() -> None:
    got = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})[
        "todo://a/x"
    ]
    assert (got.level, got.need, got.delegable, got.action) == (
        3,
        "implement",
        "unverified",
        "launch?",
    )


def test_level_0_is_output_only() -> None:
    got = _assess({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"}, level=0)[
        "todo://a/x"
    ]
    assert got.action == "—"


def test_own_focus_autonomy_caps_inherited_rank() -> None:
    rm = R3.replace('epic = "eco.focus2"', 'epic = "eco.focus2"\nautonomy = 0')
    got = _assess(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://c/f2\n",
            "c": "- [ ] f2 @owner:github:own @id:f2 @epic:eco.focus2\n",
        },
        roadmap=rm,
    )["todo://c/f2"]
    assert got.level == 0 and got.action == "—"


def test_owner_and_decision_signals() -> None:
    a = _assess(
        {
            "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
            "- [ ] подпись формы @owner:github:own @id:y "
            "@epic:eco.focus1\n"
            "- [ ] z @owner:github:someone @id:z @epic:eco.focus1\n"
            "- [ ] n @owner:github:own @id:n @epic:eco.unknown\n"
        }
    )
    assert a["todo://a/x"].block_reason == "owner-tbd"
    assert a["todo://a/y"].block_reason == "decision-signal"
    assert a["todo://a/z"].block_reason == "foreign-owner"
    assert a["todo://a/n"].block_reason == "epic-unknown"
    assert all(a[n].need == "decide" for n in a)


def test_decision_signal_in_glued_request_text() -> None:
    a = _assess(
        {"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"},
        [record("a", 1, body="slug: x\nнужен sign-off владельца\n", labels=["inbox"])],
    )
    assert a["todo://a/x"].block_reason == "decision-signal"


def test_adr_mention_is_not_a_decision() -> None:
    inp = inputs(
        {
            "a": "- [ ] исправить тест по ADR-ECO-006 @owner:github:own "
            "@id:x @epic:eco.focus1\n"
        }
    )
    assert delegable("todo://a/x", build_graph(inp), inp) == (
        "unverified",
        "authority-root",
    )


def test_no_checkout_is_not_delegable() -> None:
    inp = inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"})
    graph = build_graph(inp)
    inp.todos = [
        RepoTodo("a", None, None, "error", "нет клона") if t.repo == "a" else t
        for t in inp.todos
    ]
    assert delegable("todo://a/x", graph, inp)[0] == "no"


def test_parked_leaf_level_2_background_pull_launches() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://c/c\n",
        "c": "- [ ] c @owner:github:own @id:c @epic:eco.parked\n",
    }
    parked = _assess(todos)["todo://c/c"]
    assert parked.level == 2 and not parked.action.startswith("launch")
    bg = todos | {"c": "- [ ] c @owner:github:own @id:c @epic:eco.bg\n"}
    pull = R3.replace(
        'goal = "todo://a/goal"', 'goal = "todo://a/goal"\npull_prerequisites = true'
    )
    assert _assess(bg, roadmap=pull)["todo://c/c"].action == "launch?"
    assert _assess(bg)["todo://c/c"].action != "launch?"


def test_pr_table() -> None:
    a = _assess(
        {},
        [
            record("a", 1, is_pr=True, ci="red"),
            record("a", 2, is_pr=True, ci="green", review_decision="APPROVED"),
            record("a", 3, is_pr=True, ci="pending", approved_at_head=True),
            record("a", 4, is_pr=True, ci="green", approved_at_head=True),
            record(
                "a",
                5,
                is_pr=True,
                ci="green",
                approved_at_head=True,
                files=["merge-pr.sh"],
            ),
            record("b", 6, is_pr=True, ci="green", approved_at_head=True),
            record(
                "a",
                7,
                is_pr=True,
                ci="green",
                approved_at_head=True,
                complete=False,
            ),
        ],
        authority_prefixes=["merge-pr.sh", ".github/"],
        human_merge_repos=["b"],
    )
    got = {n: (a[n].need, a[n].actor) for n in a}
    assert got == {
        "a!1": ("fix_pr", "own"),
        "a!2": ("review", "review-loop"),  # одобрен не head SHA
        "a!3": ("wait_ci", "ci"),
        "a!4": ("merge", "merge-contour"),
        "a!5": ("merge", "owner"),  # authority-root
        "b!6": ("merge", "owner"),  # «Мерж: человек»
        "a!7": ("merge", "merge-contour?"),  # список файлов неполон
    }
    assert all(x.action in ("—", "pr_nudge") for x in a.values())


def test_questions_only_for_ranked() -> None:
    a = _assess(
        {
            "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
            "- [ ] y @owner:TBD @id:y @epic:eco.bg\n"
        }
    )
    assert a["todo://a/x"].ask_owner and not a["todo://a/y"].ask_owner


def test_prose_trigger_waits_without_question() -> None:
    a = _assess(
        {
            "a": "- [ ] t @owner:github:own @id:t @epic:eco.focus1 "
            '@trigger:"после появления X"\n'
            "- [ ] m @owner:github:own @id:m @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    t, m = a["todo://a/t"], a["todo://a/m"]
    assert (t.need, t.actor, t.ask_owner) == ("wait_condition", "condition", False)
    assert (m.need, m.ask_owner) == ("decide", True)


def test_position_level_formula() -> None:
    rm = parse_roadmap(R3, EPICS)
    inp = inputs({"a": "- [ ] x @owner:github:own @id:x @epic:eco.bg\n"})
    g = build_graph(inp)
    entry = build_queue(g, evaluate_waits(g, inp, NOW, 3), rm, NOW)[0]
    assert position_level(entry, rm, run_level=3) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/conductor/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.policy'`

- [ ] **Step 3: Write minimal implementation**

`conductor/policy.py`:
```python
"""Уровень позиции, actor/need, делегируемость, выбор действия (§2.4, §5.2–5.3).

В срезе 0 результат только показывается. Путь authority-root из контекст-пака
(§5.2) подключается в срезе 3, поэтому лучший вердикт делегируемости здесь —
unverified, а действие — `launch?`, не `launch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from conductor.analysis import work_state
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.rank import QueueEntry
from conductor.roadmap import Roadmap
from conductor.waits import Wait, waits_of

DECISION_WORDS = (
    "sign-off",
    "подпис",
    "approve",
    "одобр",
    "approval-policy",
    "утверд",
    "новая версия контракта",
    "new contract version",
)
DECISION_PATH_PREFIXES = ("prograph-vault/authored/decisions/", ".github/")
OUT_OF_LOOP_REPOS = frozenset({"sdd-framework"})
# Неизвестно лишь условие, которое срез 0 не умеет вычислить: это ожидание,
# а не решение владельца (§5.2 rev 10) — вопроса нет.
CONDITION_REASONS = frozenset({"prose_trigger", "fact_unread"})
Delegable = Literal["yes", "no", "unverified"]


@dataclass(frozen=True)
class Assessment:
    """Что conductor сделал бы с позицией на уровне level."""

    node_id: str
    level: int
    actor: str
    need: str
    delegable: Delegable
    block_reason: str | None
    action: str
    ask_owner: bool


def position_level(entry: QueueEntry, roadmap: Roadmap, run_level: int) -> int:
    """§2.4: свой класс задаёт потолок; ранг наследуется, полномочия — нет."""
    own = roadmap.focus_of(entry.own_focus)
    via = roadmap.focus_of(entry.via_focus)
    if entry.klass == "focus" and own is not None:
        return min(run_level, own.autonomy)
    if via is None:
        return min(run_level, 1) if entry.klass == "background" else 0
    if entry.klass == "background" and via.pull_prerequisites:
        return min(run_level, via.autonomy)
    return min(run_level, via.autonomy, 2)


def delegable(
    node_id: str, graph: Graph, inputs: Inputs
) -> tuple[Delegable, str | None]:
    """§5.2 (rev 9): владелец, признак решения, эпик, клон, контур."""
    node = graph.nodes[graph.resolve(node_id)]
    owner = node.owner()
    if owner is None or owner.get("kind") == "tbd":
        return "no", "owner-tbd"
    if owner["kind"] == "github_team" or (
        owner["kind"] == "github_user" and owner["id"] != inputs.owner
    ):
        return "no", "foreign-owner"
    text = "\n".join(
        f"{graph.nodes[m].title}\n{graph.nodes[m].body}"
        for m in graph.members(node_id)
        if m in graph.nodes
    ).lower()
    if any(w in text for w in DECISION_WORDS) or any(
        p in text for p in DECISION_PATH_PREFIXES
    ):
        return "no", "decision-signal"
    status = inputs.epics.get(node.epic or "", {}).get("status")
    if status is None:
        return "no", "epic-unknown"
    if status != "active":
        return "no", "epic-not-active"
    todo = next((t for t in inputs.todos if t.repo == node.repo), None)
    if node.repo in OUT_OF_LOOP_REPOS or todo is None or todo.state == "error":
        return "no", "out-of-loop"
    return "unverified", "authority-root"


def _pr_need(graph: Graph, node_id: str, inputs: Inputs) -> tuple[str, str]:
    """§5.2: CI и одобрение именно head SHA; мерж человеку — по политике."""
    rec = graph.records.get(node_id, {})
    if rec.get("ci") == "red":
        return "fix_pr", rec.get("author") or "author"
    if not rec.get("approved_at_head"):
        return "review", "review-loop"
    if rec.get("ci") != "green":
        return "wait_ci", "ci"
    files = rec.get("files", [])
    touches = any(f.startswith(p) for f in files for p in inputs.authority_prefixes)
    if graph.nodes[node_id].repo in inputs.human_merge_repos or touches:
        return "merge", "owner"
    if not rec.get("complete") or not inputs.authority_prefixes:
        return "merge", "merge-contour?"
    return "merge", "merge-contour"


def _need(
    entry: QueueEntry, graph: Graph, waits: list[Wait], inputs: Inputs
) -> tuple[str, str]:
    node = graph.nodes[entry.node_id]
    unknown = [w for w in waits_of(waits, entry.node_id) if w.verdict == "unknown"]
    if any(w.reason not in CONDITION_REASONS for w in unknown):
        return "decide", "owner"
    if unknown:
        return "wait_condition", "condition"
    if node.kind == "pr":
        return _pr_need(graph, entry.node_id, inputs)
    if node.kind == "issue":
        if "inbox" in node.labels:
            return "intake", "conductor"
        return "triage", "owner"
    if work_state(entry.node_id, graph) == "in_review":
        return "review", "review-loop"
    return "implement", node.repo


def _stale(
    entry: QueueEntry, graph: Graph, waits: list[Wait], roadmap: Roadmap
) -> bool:
    if any(w.prereq == entry.node_id and w.reason == "stale" for w in waits):
        return True
    node = graph.nodes[entry.node_id]
    return (
        node.kind == "pr"
        and entry.oldest_wait_days >= roadmap.limits["stale_after_days"]
    )


def _action(
    need: str,
    level: int,
    entry: QueueEntry,
    roadmap: Roadmap,
    verdict: Delegable,
    stale: bool,
) -> str:
    if level == 0:
        return "—"
    if need in ("decide", "triage"):
        return "owner_queue"
    if need == "intake":
        return "request_intake" if level >= 2 else "owner_queue"
    if need in ("review", "wait_ci", "merge", "fix_pr"):
        return "pr_nudge" if stale else "—"
    via = roadmap.focus_of(entry.via_focus)
    may_launch = entry.klass == "focus" or (
        entry.klass == "background" and via is not None and via.pull_prerequisites
    )
    if need == "implement" and level >= 3 and may_launch and verdict != "no":
        return "launch" if verdict == "yes" else "launch?"
    return "nudge" if stale else "—"


def assess(
    entry: QueueEntry,
    graph: Graph,
    waits: list[Wait],
    roadmap: Roadmap,
    run_level: int,
    inputs: Inputs,
) -> Assessment:
    """Позиция → уровень, actor/need, делегируемость, действие (не исполняется)."""
    level = position_level(entry, roadmap, run_level)
    need, actor = _need(entry, graph, waits, inputs)
    verdict: Delegable = "unverified"
    reason: str | None = None
    if need == "implement":
        verdict, reason = delegable(entry.node_id, graph, inputs)
        if verdict == "no":
            need, actor = "decide", "owner"
    action = _action(
        need, level, entry, roadmap, verdict, _stale(entry, graph, waits, roadmap)
    )
    return Assessment(
        entry.node_id,
        level,
        actor,
        need,
        verdict,
        reason,
        action,
        need == "decide" and entry.rank is not None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_policy.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/policy.py tests/conductor/test_policy.py
git commit -m "feat(conductor): политика как выдача — уровень, actor/need, действие"
```

---

### Task 11: Конвейер, снимок, схема, показатели

**Files:**
- Create: `conductor/snapshot.py`, `contracts/conductor-snapshot/v1/schema.json`, `tests/conductor/test_snapshot.py`

**Interfaces:**
- Consumes: всё ядро, `Inputs`
- Produces: `Result(graph, waits, queue, attention, assessments: dict[str, Assessment], findings, roadmap, cycles, run_level, graph_state)`; `evaluate(inputs, run_level) -> Result`; `question_id(kind, subject, evidence, options) -> str`; `to_snapshot(result, inputs, run_id, previous) -> dict`; `SCHEMA_PATH`.

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_snapshot.py`:
```python
import json

import jsonschema
import pytest

from conductor.snapshot import SCHEMA_PATH, evaluate, question_id, to_snapshot
from tests.conductor.fixtures import ROADMAP, inputs, record

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _snap(todos, **kw):
    inp = inputs(todos, **kw)
    return to_snapshot(evaluate(inp, 0), inp, "run-1", None)


def test_snapshot_matches_schema_and_carries_work_state() -> None:
    snap = _snap(
        {
            "a": "- [ ] x @owner:TBD @id:x @epic:eco.focus1\n"
            "- [ ] y @owner:TBD @id:y @epic:eco.bg\n"
        }
    )
    jsonschema.validate(snap, SCHEMA)
    assert snap["graph_state"] == "complete" and snap["run_level"] == 0
    node = next(n for n in snap["nodes"] if n["node_id"] == "todo://a/x")
    assert node["work_state"] == "idle"
    assert snap["metrics"]["owner_questions"] == 1
    assert snap["metrics"]["decide_unranked"] == 1


def test_attention_reaches_owner_questions() -> None:
    snap = _snap(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/nope\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        }
    )
    assert [q["subject"] for q in snap["owner_questions"]] == ["todo://a/goal"]


def test_partial_and_invalid_roadmap() -> None:
    snap = _snap(
        {"a": "- [ ] x @owner:TBD @id:x\n"}, gh_state="error", roadmap="nope = ["
    )
    jsonschema.validate(snap, SCHEMA)
    assert snap["graph_state"] == "partial"
    assert "RM-INVALID" in {f["code"] for f in snap["findings"]}
    assert all(q["rank"] is None for q in snap["queue"])


def test_schema_rejects_node_without_work_state() -> None:
    snap = _snap({"a": "- [ ] x @owner:TBD @id:x\n"})
    del snap["nodes"][0]["work_state"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(snap, SCHEMA)


def test_questions_by_reason() -> None:
    snap = _snap(
        {
            "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            "@blocked_by:todo://b/gone\n"
            "- [x] s @owner:TBD @id:shipped @epic:eco.focus1\n"
            "- [x] z @owner:TBD @id:bgshipped @epic:eco.bg\n",
            "b": "- [ ] y @owner:TBD @id:y\n",
        },
        records=[
            record("a", 1, body="slug: shipped\n", labels=["inbox"]),
            record("a", 2, body="slug: bgshipped\n", labels=["inbox"]),
        ],
        history={"todo://b/gone": "deadbeef"},
    )
    got = {(q["subject"], q["reason"]): q for q in snap["owner_questions"]}
    cancelled = got[("todo://a/goal", "cancelled")]
    assert cancelled["options"] == ["drop-wait", "replace", "keep"]
    assert "todo://b/gone" in cancelled["evidence"]
    assert got[("a#1", "GR-SHIPPED-OPEN")]["options"] == ["close", "keep"]
    assert ("a#2", "GR-SHIPPED-OPEN") not in got  # фон: находка есть, вопроса нет
    assert "GR-SHIPPED-OPEN" in {f["code"] for f in snap["findings"]}


def test_plan_ceiling_respects_roadmap_autonomy() -> None:
    rm = ROADMAP.replace('epic = "eco.focus1"', 'epic = "eco.focus1"\nautonomy = 3')
    inp = inputs(
        {"a": "- [ ] x @owner:github:own @id:x @epic:eco.focus1\n"}, roadmap=rm
    )
    result = evaluate(inp, 3)
    assert result.run_level == 0
    assert all(a.action == "—" for a in result.assessments.values())


def test_question_id_depends_on_options_order() -> None:
    a = question_id("owner-tbd", "todo://a/x", "ev", ("delegate", "keep"))
    b = question_id("owner-tbd", "todo://a/x", "ev", ("keep", "delegate"))
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
               "queue", "attention", "cycles", "findings", "actions",
               "owner_questions", "metrics", "changes_since_previous"],
  "properties": {
    "contract": {"const": "conductor-snapshot/v1"},
    "host": {"type": "string"},
    "run_id": {"type": "string"},
    "started_at": {"type": "string"},
    "writer": {"type": "object", "required": ["is_writer", "reason"]},
    "roadmap": {"type": "object", "required": ["source", "sha", "valid"]},
    "run_level": {"type": "integer", "minimum": 0, "maximum": 3},
    "sources": {"type": "array", "items": {"type": "object",
      "required": ["name", "state", "detail", "sha"],
      "properties": {"state": {"enum": ["read", "absent", "not_queried",
                                        "error"]}}}},
    "graph_state": {"enum": ["complete", "partial"]},
    "nodes": {"type": "array", "items": {"type": "object",
      "required": ["node_id", "kind", "repo", "title", "is_open", "closed_as",
                   "epic", "work_state"],
      "properties": {"kind": {"enum": ["item", "issue", "pr"]},
                     "work_state": {"enum": ["in_review", "idle"]}}}},
    "edges": {"type": "array", "items": {"type": "object",
      "required": ["src", "dst", "type", "origin"],
      "properties": {"type": {"enum": ["depends_on", "accepted_as",
                                        "implements", "mentions"]}}}},
    "waits": {"type": "array", "items": {"type": "object",
      "required": ["consumer", "prereq", "verdict", "reason", "evidence",
                   "since", "moved"],
      "properties": {"verdict": {"enum": ["satisfied", "pending", "unknown"]}}}},
    "queue": {"$ref": "#/$defs/positions"},
    "attention": {"$ref": "#/$defs/positions"},
    "cycles": {"type": "array", "items": {"type": "array",
                                          "items": {"type": "string"}}},
    "findings": {"type": "array", "items": {"type": "object",
      "required": ["code", "severity", "subject", "detail"]}},
    "actions": {"type": "object", "required": ["plan", "journal"]},
    "owner_questions": {"type": "array", "items": {"type": "object",
      "required": ["question_id", "subject", "reason", "question", "options",
                   "default"]}},
    "metrics": {"type": "object", "required": [
      "waits_satisfied", "waits_pending", "waits_stale", "waits_unknown",
      "actions_available", "actions_executed", "owner_questions",
      "owner_questions_by_reason", "decide_unranked", "partial"]},
    "changes_since_previous": {"type": "object", "required": ["first_run"]}
  },
  "$defs": {
    "positions": {"type": "array", "items": {"type": "object",
      "required": ["node_id", "rank", "via_focus", "own_focus", "klass", "why",
                   "actor", "need", "level", "delegable", "block_reason",
                   "action", "ask_owner"],
      "properties": {"delegable": {"enum": ["yes", "no", "unverified"]}}}}
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

from conductor.analysis import dependency_adjacency, find_cycles, work_state
from conductor.analysis import findings as graph_findings
from conductor.graph import Graph, build_graph
from conductor.inputs import Inputs
from conductor.model import Finding
from conductor.policy import Assessment, assess
from conductor.rank import QueueEntry, build_attention, build_queue
from conductor.roadmap import Roadmap, parse_roadmap
from conductor.waits import Wait, evaluate_waits

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "conductor-snapshot"
    / "v1"
    / "schema.json"
)
DELEGATE = ("делегировать агенту?", ("delegate", "keep"))
WAIT_QUESTIONS = {
    "cancelled": (
        "предпосылка отменена: снять ожидание или найти замену?",
        ("drop-wait", "replace", "keep"),
    ),
    "missing": (
        "предпосылки нет ни в одном TODO: снять ожидание или завести запрос?",
        ("drop-wait", "request", "keep"),
    ),
    "version_mismatch": (
        "вышла другая версия, чем ждали: принять её?",
        ("accept-version", "keep"),
    ),
}
SHIPPED = ("похоже отгружено: закрыть issue?", ("close", "keep"))


@dataclass
class Result:
    """Всё, что вычислило ядро за прогон."""

    graph: Graph
    waits: list[Wait]
    queue: list[QueueEntry]
    attention: list[QueueEntry]
    assessments: dict[str, Assessment]
    findings: list[Finding]
    roadmap: Roadmap
    cycles: list[list[str]]
    run_level: int
    graph_state: str


def evaluate(inputs: Inputs, run_level: int) -> Result:
    """Детерминированный конвейер: граф → ожидания → очередь → политика."""
    roadmap = parse_roadmap(inputs.roadmap_text, inputs.epics)
    graph = build_graph(inputs)
    waits = evaluate_waits(
        graph, inputs, inputs.captured_at, roadmap.limits["stale_after_days"]
    )
    cycles = find_cycles(dependency_adjacency(graph))
    # §2.4: потолок прогона. plan — симуляция управляющего писателя: личность
    # хоста не проверяется (plan не пишет), но роадмап ограничивает всегда.
    level = (
        0 if graph.partial or not roadmap.valid else min(run_level, roadmap.autonomy)
    )
    queue = build_queue(graph, waits, roadmap, inputs.captured_at)
    attention = build_attention(graph, waits, roadmap, inputs.captured_at)
    assessments = {
        e.node_id: assess(e, graph, waits, roadmap, level, inputs)
        for e in queue + attention
    }
    found = list(roadmap.findings) + graph_findings(graph, waits, cycles, roadmap)
    return Result(
        graph,
        waits,
        queue,
        attention,
        assessments,
        found,
        roadmap,
        cycles,
        level,
        "partial" if graph.partial else "complete",
    )


def question_id(
    kind: str, subject: str, evidence: str, options: tuple[str, ...]
) -> str:
    """§5.7: sha256(kind, subject, evidence, варианты по порядку)[:8]."""
    raw = "\x1f".join((kind, subject, evidence, *options)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def _question(
    subject: str, reason: str, evidence: str, text: str, options: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "question_id": question_id(reason, subject, evidence, options),
        "subject": subject,
        "reason": reason,
        "evidence": evidence,
        "question": f"{subject}: {text}",
        "options": list(options),
        "default": "keep",
    }


def owner_questions(result: Result) -> list[dict[str, Any]]:
    """Вопрос по причине: у ожидания — своя, у делегируемости — своя (§5.7)."""
    out = []
    for a in result.assessments.values():
        if not a.ask_owner:
            continue
        waits = [
            w
            for w in result.waits
            if w.consumer == a.node_id and w.reason in WAIT_QUESTIONS
        ]
        for w in waits:
            text, options = WAIT_QUESTIONS[w.reason]
            evidence = f"{w.prereq}|{w.evidence}"
            out.append(_question(a.node_id, w.reason, evidence, text, options))
        if not waits:
            reason = a.block_reason or "unknown-wait"
            out.append(_question(a.node_id, reason, reason, *DELEGATE))
    for f in result.findings:
        epic = result.graph.epic_of(f.subject)
        if f.code == "GR-SHIPPED-OPEN" and result.roadmap.focus_of(epic) is not None:
            out.append(_question(f.subject, f.code, f.detail, *SHIPPED))
    return sorted(out, key=lambda q: (q["subject"], q["reason"]))


def _metrics(result: Result, questions: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(w.verdict for w in result.waits)
    assessed = list(result.assessments.values())
    return {
        "waits_satisfied": verdicts["satisfied"],
        "waits_pending": verdicts["pending"],
        "waits_stale": sum(w.reason == "stale" for w in result.waits),
        "waits_unknown": verdicts["unknown"],
        "actions_available": sum(a.action != "—" for a in assessed),
        "actions_executed": 0,
        "owner_questions": len(questions),
        "owner_questions_by_reason": dict(Counter(q["reason"] for q in questions)),
        "decide_unranked": sum(
            a.need == "decide" and not a.ask_owner for a in assessed
        ),
        "partial": result.graph_state == "partial",
    }


def _changes(result: Result, previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {"first_run": True}
    before = {(w["consumer"], w["prereq"]): w["verdict"] for w in previous["waits"]}
    newly = [
        f"{w.consumer} ← {w.prereq}"
        for w in result.waits
        if w.verdict == "satisfied"
        and before.get((w.consumer, w.prereq)) not in (None, "satisfied")
    ]
    return {"first_run": False, "newly_satisfied": newly}


def _positions(entries: list[QueueEntry], result: Result) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        a = result.assessments[e.node_id]
        out.append(
            {
                **asdict(e),
                "actor": a.actor,
                "need": a.need,
                "level": a.level,
                "delegable": a.delegable,
                "block_reason": a.block_reason,
                "action": a.action,
                "ask_owner": a.ask_owner,
            }
        )
    return out


def to_snapshot(
    result: Result, inputs: Inputs, run_id: str, previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Снимок по контракту conductor-snapshot/v1."""
    questions = owner_questions(result)
    graph = result.graph
    return {
        "contract": "conductor-snapshot/v1",
        "host": inputs.host,
        "run_id": run_id,
        "started_at": inputs.captured_at,
        "writer": {"is_writer": False, "reason": "срез 0: записей нет"},
        "roadmap": {
            "source": inputs.roadmap_source,
            "sha": inputs.roadmap_sha,
            "valid": result.roadmap.valid,
        },
        "run_level": result.run_level,
        "sources": [asdict(s) for s in graph.sources],
        "graph_state": result.graph_state,
        "nodes": [
            {**asdict(n), "work_state": work_state(n.node_id, graph)}
            for n in graph.nodes.values()
        ],
        "edges": [asdict(e) for e in graph.edges],
        "waits": [asdict(w) for w in result.waits],
        "queue": _positions(result.queue, result),
        "attention": _positions(result.attention, result),
        "cycles": result.cycles,
        "findings": [asdict(f) for f in result.findings],
        "actions": {
            "plan": [
                {"node_id": a.node_id, "action": a.action}
                for a in result.assessments.values()
                if a.action != "—"
            ],
            "journal": [],
        },
        "owner_questions": questions,
        "metrics": _metrics(result, questions),
        "changes_since_previous": _changes(result, previous),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_snapshot.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add conductor/snapshot.py contracts/conductor-snapshot tests/conductor/test_snapshot.py
git commit -m "feat(conductor): конвейер, снимок conductor-snapshot/v1, показатели"
```

---

### Task 12: Сбор входов, рендер и CLI

**Files:**
- Create: `conductor/collect.py`, `conductor/render.py`, `tests/conductor/test_collect.py`, `tests/conductor/test_cli.py`
- Modify: `conductor/__main__.py` (заменить заглушку целиком)

**Interfaces:**
- Consumes: Tasks 2–11
- Produces: `read_epics(root) -> (dict, state, detail, sha)`; `read_authority_prefixes() -> list[str] | None`; `collect(root, manifest_text, manifest_origin, roadmap_path, do_fetch, runner, host, now)`; CLI читает манифест с `origin` зонтика, файл — только по явному `--manifest`; `collect(root, manifest_text, roadmap_path, do_fetch, runner, host, now) -> Inputs`; `render_status(result, top=15)`, `render_why(result, node_id)`, `render_plan(result)`; `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/conductor/test_collect.py`:
```python
import subprocess
from pathlib import Path

from conductor.collect import collect, read_epics
from conductor.manifest import UMBRELLA


def _repo(path: Path, files: dict[str, str]) -> None:
    up = path.parent / (path.name + "-up")
    up.mkdir(parents=True)
    subprocess.run(["git", "-C", str(up), "init", "-q", "-b", "master"], check=True)
    for name, text in files.items():
        (up / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )
    subprocess.run(["git", "clone", "-q", str(up), str(path)], check=True)


def test_read_epics_states(tmp_path: Path) -> None:
    assert read_epics(tmp_path)[:2] == ({}, "error")
    _repo(tmp_path / UMBRELLA, {"epics.toml": "x = ["})
    assert read_epics(tmp_path)[1] == "error"


def test_read_epics_ok(tmp_path: Path) -> None:
    _repo(
        tmp_path / UMBRELLA,
        {
            "epics.toml": (
                'schema_version = "1.0.0"\nadopted_at = "2026-09-01"\n'
                "[coverage_policy]\nrobin_cutover_todo = 0.98\n"
                "robin_cutover_issues = 0.90\nrobin_cutover_prs = 0.90\n"
                'missing_error_after = "2026-11-01"\nmin_sample = 10\n'
                "[exclusions]\nmerge_commits = true\nbot_authors = []\npaths = []\n"
                '[programs.eco]\ntitle = "e"\nkind = "ecosystem"\n'
                '[epics."eco.tooling"]\ntitle = "t"\nstatus = "active"\ngoal = "g"\n'
                'opened = "2026-01-01"\n[defect_classes.code]\ntitle = "c"\n'
            )
        },
    )
    epics, state, detail, sha = read_epics(tmp_path)
    assert sha
    assert state == "read", detail
    assert epics["eco.tooling"]["status"] == "active"


def test_collect_marks_missing_sources(tmp_path: Path) -> None:
    manifest = '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    _repo(tmp_path / "a", {"TODO.md": "- [ ] x @owner:TBD @id:x\n"})
    inp = collect(
        tmp_path,
        manifest,
        ("file", None),
        None,
        False,
        lambda _: (1, "", "offline"),
        "h",
        "2026-09-29T12:00:00Z",
    )
    assert {t.repo: t.state for t in inp.todos} == {"a": "read", UMBRELLA: "error"}
    assert (inp.gh_state, inp.roadmap_state, inp.epics_state) == ("error",) * 3
    assert inp.manifest_text == manifest


def test_collect_history_reversed_tags_and_human_merge(tmp_path: Path) -> None:
    manifest = (
        '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
        '[cores.b]\nrepo_url = "git@github.com:own/b.git"\ngit_dir = "b"\n'
    )
    _repo(
        tmp_path / "a",
        {
            "TODO.md": "- [ ] x @blocked_by:todo://b/gone @owner:TBD @id:x\n",
            "CLAUDE.md": "## Git\n- Мерж: человек\n",
        },
    )
    up = tmp_path / "b-up"
    _repo(tmp_path / "b", {"TODO.md": "- [ ] g @owner:TBD @id:gone\n"})
    (up / "TODO.md").write_text("- [ ] other @owner:TBD @id:other\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(up),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qam",
            "drop",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path / "b"), "fetch", "-q"], check=True)
    inp = collect(
        tmp_path,
        manifest,
        ("file", None),
        None,
        False,
        lambda _: (1, "", "offline"),
        "h",
        "2026-09-29T12:00:00Z",
    )
    assert inp.wait_since["todo://a/x|todo://b/gone"].endswith("Z")
    assert inp.history["todo://b/gone"]
    assert inp.human_merge_repos == ["a"]
    assert inp.authority_prefixes and inp.aux_state == "read"
```

`tests/conductor/test_cli.py`:
```python
import json
from pathlib import Path

from conductor.__main__ import main
from conductor.inputs import save_inputs
from tests.conductor.fixtures import ROADMAP, inputs

TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
    "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
}


def _replay(tmp: Path, todos=TODOS, **kw) -> Path:
    path = tmp / "inputs.json"
    save_inputs(inputs(todos, **kw), path)
    return path


def test_status_why_plan_from_replay_without_manifest_file(
    tmp_path: Path, capsys
) -> None:
    rep = _replay(tmp_path)
    assert main(["status", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "todo://b/b" in out and "вопросы владельцу" in out
    assert main(["why", "todo://b/b", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "todo://a/goal" in out and "need=decide" in out
    assert main(["plan", "--level", "3", "--replay", str(rep)]) == 0
    assert "план на уровне 0" in capsys.readouterr().out  # autonomy = 0
    rep3 = _replay(tmp_path, roadmap=ROADMAP.replace("autonomy = 0", "autonomy = 3"))
    assert main(["plan", "--level", "3", "--replay", str(rep3)]) == 0
    assert "план на уровне 3" in capsys.readouterr().out


def test_run_writes_snapshot_and_inputs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--replay",
                str(_replay(tmp_path)),
                "--out",
                str(out),
                "--level",
                "3",
            ]
        )
        == 0
    )
    run_dir = next(out.iterdir())
    snap = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["run_level"] == 0 and snap["actions"]["journal"] == []
    assert (run_dir / "inputs.json").is_file()


def test_invalid_roadmap_exit_4_for_every_command(tmp_path: Path) -> None:
    rep, out = _replay(tmp_path, roadmap="x = ["), tmp_path / "out"
    assert main(["run", "--replay", str(rep), "--out", str(out)]) == 4
    assert (next(out.iterdir()) / "snapshot.json").is_file()
    assert main(["status", "--replay", str(rep)]) == 4
    assert main(["record", str(tmp_path / "rec"), "--replay", str(rep)]) == 4
    assert (tmp_path / "rec" / "inputs.json").is_file()


def test_status_prints_reason_specific_questions(tmp_path: Path, capsys) -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
        "@blocked_by:todo://b/gone\n",
        "b": "- [ ] y @owner:TBD @id:y\n",
    }
    rep = _replay(tmp_path, todos, history={"todo://b/gone": "deadbeef"})
    assert main(["status", "--replay", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "вопросы владельцу: 1" in out and "cancelled — drop-wait" in out


def test_gh_error_still_exit_0(tmp_path: Path, capsys) -> None:
    assert main(["status", "--replay", str(_replay(tmp_path, gh_state="error"))]) == 0
    assert "partial" in capsys.readouterr().out


def test_missing_manifest_exit_3(tmp_path: Path) -> None:
    assert main(["status", "--manifest", str(tmp_path / "nope.toml")]) == 3


def test_bad_args_exit_2() -> None:
    assert main(["frobnicate"]) == 2
    assert main(["why"]) == 2


def test_selftest() -> None:
    assert main(["--selftest"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/conductor/test_collect.py tests/conductor/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.collect'`

- [ ] **Step 3: Write minimal implementation**

`conductor/collect.py`:
```python
"""Сбор Inputs из git и GitHub — единственное место ввода-вывода чтения.

Сбой любого источника становится его состоянием, а не исключением (I6):
ошибки вспомогательных чтений (история, факты путей, политики репо) копятся в
источнике `history`, и граф с ними — partial.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import plan_fields as pf

from conductor.graph import referenced_issues
from conductor.inputs import Inputs, RepoTodo
from conductor.manifest import (
    UMBRELLA,
    FleetRepo,
    fleet_repos,
    github_owner,
    manifest_index,
)
from conductor.model import SourceState
from conductor.sources_gh import Runner, collect_gh
from conductor.sources_git import (
    GitError,
    default_ref,
    ever_had,
    last_commit_mentioning,
    line_since,
    path_fact,
    read_file_at_origin,
    read_todo,
)
from conductor.waits import EXISTS_RE

TRIGGER_RE = re.compile(r'@trigger:"([^"]*)"')
HUMAN_MERGE_RE = re.compile(r"(?m)^\s*(?:[-*]\s*)?Мерж:\s*человек\s*$")
AUTHORITY_ROOT_ENV = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "authority-root"
    / "v1"
    / "paths.env"
)


def read_epics(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], SourceState, str, str | None]:
    """epics.toml зонтика с origin/<default>; ошибки реестра → error."""
    umbrella = root / UMBRELLA
    if not (umbrella / ".git").exists():
        return {}, "error", f"нет клона {umbrella}", None
    text, sha, state, detail = read_file_at_origin(umbrella, "epics.toml")
    if state != "read" or text is None:
        return {}, "error", detail, sha
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "epics.toml"
        path.write_text(text, encoding="utf-8")
        registry = pf.load_registry(path)
    errors = [
        d["message"] for d in registry.diagnostics if d.get("severity") == "error"
    ]
    if errors:
        return {}, "error", "; ".join(errors), sha
    return {k: dict(v) for k, v in registry.epics.items()}, "read", "", sha


def read_authority_prefixes() -> list[str] | None:
    """Префиксы authority-root из собственного контракта devtools."""
    if not AUTHORITY_ROOT_ENV.is_file():
        return None
    for line in AUTHORITY_ROOT_ENV.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "AUTHORITY_ROOT_PREFIXES":
            return value.split()
    return None


class _History:
    """movement, wait_since, history и ошибки вспомогательных чтений."""

    def __init__(self, root: Path, repos: dict[str, FleetRepo]) -> None:
        self.root, self.repos = root, repos
        self.movement: dict[str, str] = {}
        self.since: dict[str, str] = {}
        self.history: dict[str, str] = {}
        self.errors: list[str] = []
        self._refs: dict[str, str | None] = {}

    def ref(self, repo: str) -> tuple[Path, str | None]:
        repo_dir = self.root / self.repos[repo].git_dir
        if repo not in self._refs:
            self._refs[repo] = default_ref(repo_dir)
        return repo_dir, self._refs[repo]

    def collect(self, snapshot: dict[str, Any], readable: set[str]) -> None:
        nodes = {n["node_id"]: n for n in snapshot["nodes"]}
        for node in nodes.values():
            if node["declared_status"] == "open" and node["repo"] in readable:
                self._movement(node)
        for ref in snapshot["references"]:
            if ref["kind"] == "blocked_by" and ref["provenance"]["repo"] in readable:
                self._wait(ref, nodes)

    def _movement(self, node: dict[str, Any]) -> None:
        repo_dir, ref = self.ref(node["repo"])
        if ref is None:
            return
        try:
            when = last_commit_mentioning(repo_dir, ref, f"@id:{node['id']}")
        except GitError as exc:
            self.errors.append(str(exc))
            return
        if when:
            self.movement[node["node_id"]] = when

    def _wait(self, ref: dict[str, Any], nodes: dict[str, Any]) -> None:
        repo_dir, git_ref = self.ref(ref["provenance"]["repo"])
        raw = ref.get("raw_ref") or ""
        try:
            if git_ref is not None:
                line = ref["provenance"]["line"]
                self.since[f"{ref['source_node_id']}|{raw}"] = line_since(
                    repo_dir, git_ref, line
                )
            if ref.get("resolved_target") is None and raw.startswith("todo://"):
                self._history(raw, nodes)
        except GitError as exc:
            self.errors.append(str(exc))

    def _history(self, raw: str, nodes: dict[str, Any]) -> None:
        repo, _, target = raw.removeprefix("todo://").partition("/")
        if repo not in self.repos or raw in nodes:
            return
        repo_dir, git_ref = self.ref(repo)
        if git_ref is not None and (
            sha := ever_had(repo_dir, git_ref, f"@id:{target}")
        ):
            self.history[raw] = sha


def _trigger_facts(
    root: Path, repos: dict[str, FleetRepo], todos: list[RepoTodo], errors: list[str]
) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for todo in todos:
        for text in TRIGGER_RE.findall(todo.text or ""):
            if not (m := EXISTS_RE.match(text)):
                continue
            if m.group(1) not in repos:
                errors.append(f"exists: неизвестный репо в {text}")
                continue
            fact = path_fact(root / repos[m.group(1)].git_dir, m.group(2))
            if fact["exists"] is None:
                errors.append(f"факт не прочитан: {text}")
            facts[text] = fact
    return facts


def _human_merge(
    root: Path, repos: dict[str, FleetRepo], errors: list[str]
) -> list[str]:
    """Репо с объявленной строкой `Мерж: человек` в CLAUDE.md на origin."""
    found = []
    for repo in repos.values():
        repo_dir = root / repo.git_dir
        if not (repo_dir / ".git").exists():
            continue
        text, _, state, detail = read_file_at_origin(repo_dir, "CLAUDE.md")
        if state == "error":
            errors.append(f"{repo.key}: CLAUDE.md: {detail}")
        elif text is not None and HUMAN_MERGE_RE.search(text):
            found.append(repo.key)
    return sorted(found)


def _roadmap(
    root: Path, roadmap_path: Path | None
) -> tuple[str | None, str | None, SourceState, str]:
    if roadmap_path is not None:
        return roadmap_path.read_text(encoding="utf-8"), None, "read", str(roadmap_path)
    umbrella = root / UMBRELLA
    if not (umbrella / ".git").exists():
        return None, None, "error", "origin"
    text, sha, state, _ = read_file_at_origin(umbrella, "roadmap.toml")
    return text, sha, state, "origin"


def collect(
    root: Path,
    manifest_text: str,
    manifest_origin: tuple[str, str | None],
    roadmap_path: Path | None,
    do_fetch: bool,
    runner: Runner,
    host: str,
    now: str,
) -> Inputs:
    """Прочитать флот; сбои — состояния источников, не исключения."""
    repos = {r.key: r for r in fleet_repos(manifest_text)}
    todos = [read_todo(r, root, do_fetch) for r in repos.values()]
    names = {r.github_name: r.key for r in repos.values()}
    norm = {**{k: k for k in repos}, **names}
    owner = github_owner(manifest_text)
    gh = collect_gh(
        owner, names, lambda recs: referenced_issues(recs, todos, norm), runner
    )
    rm_text, rm_sha, rm_state, rm_source = _roadmap(root, roadmap_path)
    epics, epics_state, epics_detail, epics_sha = read_epics(root)
    snapshot = pf.parse_fleet(
        [
            pf.RepoInput(
                t.repo,
                todo_text=t.text or "",
                commit=t.sha,
                available=t.state in ("read", "absent"),
            )
            for t in todos
        ],
        manifest_index(manifest_text),
    )
    hist = _History(root, repos)
    hist.collect(snapshot, {t.repo for t in todos if t.state == "read"})
    facts = _trigger_facts(root, repos, todos, hist.errors)
    human = _human_merge(root, repos, hist.errors)
    prefixes = read_authority_prefixes()
    if prefixes is None:
        hist.errors.append(f"нет {AUTHORITY_ROOT_ENV}")
    hist.errors += [
        f"{r['repo']}!{r['number']}: список файлов или ревью усечён"
        for r in gh.records
        if r["is_pr"] and not r.get("complete")
    ]
    return Inputs(
        captured_at=now,
        host=host,
        owner=owner,
        manifest_text=manifest_text,
        todos=todos,
        gh_records=gh.records,
        gh_state=gh.state,
        gh_detail=gh.detail,
        roadmap_text=rm_text,
        roadmap_state=rm_state,
        roadmap_source=rm_source,
        roadmap_sha=rm_sha,
        epics=epics,
        epics_state=epics_state,
        epics_detail=epics_detail,
        repo_names=names,
        movement=hist.movement,
        wait_since=hist.since,
        history=hist.history,
        trigger_facts=facts,
        epics_sha=epics_sha,
        aux_state="error" if hist.errors else "read",
        aux_detail="; ".join(hist.errors[:5]),
        human_merge_repos=human,
        authority_prefixes=prefixes or [],
        manifest_source=manifest_origin[0],
        manifest_sha=manifest_origin[1],
    )
```

`conductor/render.py`:
```python
"""Текст для status / why / plan."""

from __future__ import annotations

from conductor.snapshot import Result, owner_questions


def _header(result: Result) -> list[str]:
    bad = [s for s in result.graph.sources if s.state in ("error", "not_queried")]
    lines = [f"граф: {result.graph_state}; уровень прогона: {result.run_level}"]
    lines += [f"  источник {s.name}: {s.state} {s.detail}" for s in bad]
    if not result.roadmap.valid:
        lines.append("  роадмап: RM-INVALID — очередь без рангов")
    return lines


def render_status(result: Result, top: int = 15) -> str:
    """Фокусы, очередь, внимание, циклы, ожидания, вопросы владельцу."""
    lines = _header(result)
    lines.append("фокусы: " + ", ".join(f.epic for f in result.roadmap.focus))
    for title, entries in (
        ("очередь", result.queue),
        ("нужны решения или условия", result.attention),
    ):
        lines.append(f"{title}:")
        for e in entries[:top]:
            a = result.assessments[e.node_id]
            lines.append(f"  {e.rank or '-'}  {e.node_id}  [{a.need} → {a.actor}]")
            lines.append(f"       {e.why}")
    lines += ["цикл: " + " → ".join(c) for c in result.cycles]
    stale = sum(w.reason == "stale" for w in result.waits)
    done = sum(w.verdict == "satisfied" for w in result.waits)
    lines.append(f"ожидания: выполнено {done}, застой {stale}")
    questions = owner_questions(result)
    lines.append(f"вопросы владельцу: {len(questions)}")
    lines += [
        f"  Q-{q['question_id']} {q['subject']}: {q['reason']} — "
        f"{' / '.join(q['options'])}"
        for q in questions[:top]
    ]
    return "\n".join(lines)


def render_why(result: Result, node_id: str) -> str:
    """Цепочка ожиданий от узла до листьев с actor/need."""
    root = result.graph.resolve(node_id)
    lines, frontier, seen = [root], [(root, 0)], {root}
    while frontier:
        node, depth = frontier.pop()
        for w in sorted(result.waits, key=lambda w: w.prereq or ""):
            if w.consumer != node:
                continue
            label = w.prereq or f"@trigger {w.evidence}"
            lines.append("  " * (depth + 1) + f"ждёт {label} [{w.verdict}/{w.reason}]")
            if w.prereq and w.prereq not in seen:
                seen.add(w.prereq)
                frontier.append((w.prereq, depth + 1))
    for other in sorted(seen):
        if (a := result.assessments.get(other)) is not None:
            lines.append(f"{other}: need={a.need} actor={a.actor} action={a.action}")
    entry = next(
        (e for e in result.queue + result.attention if e.node_id == root), None
    )
    lines.append(entry.why if entry else "не кандидат (заблокирован или закрыт)")
    return "\n".join(lines)


def render_plan(result: Result) -> str:
    """Действия, которые были бы выполнены (ничего не исполняется)."""
    lines = _header(result)
    lines.append(f"план на уровне {result.run_level} (ничего не исполняется):")
    for a in result.assessments.values():
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.collect import collect
from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
from conductor.manifest import UMBRELLA
from conductor.render import render_plan, render_status, render_why
from conductor.roadmap import parse_roadmap
from conductor.snapshot import evaluate, to_snapshot
from conductor.sources_gh import run_gh
from conductor.sources_git import read_file_at_origin

EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG = 0, 2, 3, 4
COMMANDS = ("status", "why", "plan", "run", "record")


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
    p.add_argument("command", nargs="?")
    p.add_argument("target", nargs="?")
    return p


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inputs(args: argparse.Namespace) -> Inputs | None:
    if args.replay is not None:
        return load_inputs(args.replay)
    if args.manifest is not None:
        if not args.manifest.is_file():
            print(f"нет манифеста {args.manifest}", file=sys.stderr)
            return None
        text: str | None = args.manifest.read_text(encoding="utf-8")
        origin: tuple[str, str | None] = (f"file:{args.manifest}", None)
    else:
        # Как и TODO: манифест — с опубликованной ветки зонтика (I6).
        umbrella = args.root / UMBRELLA
        text, sha, state, detail = read_file_at_origin(
            umbrella, "workspace-manifest.toml"
        )
        if state != "read" or text is None:
            print(f"манифест не прочитан: {detail}", file=sys.stderr)
            return None
        origin = ("origin", sha)
    return collect(
        args.root,
        text,
        origin,
        args.roadmap,
        not args.no_fetch,
        run_gh,
        socket.gethostname(),
        _now(),
    )


def _previous(out: Path) -> dict[str, Any] | None:
    runs = (
        sorted(d for d in out.glob("*") if (d / "snapshot.json").is_file())
        if out.is_dir()
        else []
    )
    if not runs:
        return None
    return json.loads((runs[-1] / "snapshot.json").read_text(encoding="utf-8"))


def _run(args: argparse.Namespace, inputs: Inputs) -> int:
    result = evaluate(inputs, 0)
    run_id = inputs.captured_at.replace(":", "")
    snap = to_snapshot(result, inputs, run_id, _previous(args.out))
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    save_inputs(inputs, run_dir / "inputs.json")
    print(render_status(result))
    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG


def _selftest() -> int:
    manifest = '[cores.a]\nrepo_url = "git@github.com:own/a.git"\ngit_dir = "a"\n'
    inp = Inputs(
        captured_at="2026-09-29T00:00:00Z",
        host="selftest",
        owner="own",
        manifest_text=manifest,
        todos=[RepoTodo("a", "- [ ] x @owner:TBD @id:x\n", "s", "read")],
        gh_records=[],
        gh_state="read",
        gh_detail="",
        roadmap_text=None,
        roadmap_state="absent",
        roadmap_source="selftest",
        roadmap_sha=None,
        epics={},
        epics_state="read",
        epics_detail="",
    )
    ok = [e.node_id for e in evaluate(inp, 0).queue] == ["todo://a/x"]
    print("selftest:", "ok" if ok else "FAIL")
    return EXIT_OK if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Точка входа; коды выхода — §7.2 (rev 10)."""
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return EXIT_ARGS
    if args.selftest:
        return _selftest()
    if args.command not in COMMANDS or (
        args.command in ("why", "record") and not args.target
    ):
        return EXIT_ARGS
    inputs = _inputs(args)
    if inputs is None:
        return EXIT_NO_SOURCE
    if all(t.state == "error" for t in inputs.todos) and inputs.gh_state != "read":
        print("ни один источник не прочитан", file=sys.stderr)
        return EXIT_NO_SOURCE
    if args.command == "record":
        save_inputs(inputs, Path(args.target) / "inputs.json")
        valid = parse_roadmap(inputs.roadmap_text, inputs.epics).valid
        return EXIT_OK if valid else EXIT_CONFIG
    if args.command == "run":
        return _run(args, inputs)
    result = evaluate(inputs, args.level if args.command == "plan" else 0)
    if args.command == "status":
        print(render_status(result))
    elif args.command == "why":
        print(render_why(result, args.target))
    else:
        print(render_plan(result))
    return EXIT_OK if result.roadmap.valid else EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests and linters**

Run: `uv run --frozen pytest tests/conductor -q`
Expected: PASS (все тесты пакета)

Run: `uv run --frozen --group selfcheck ruff check conductor tests/conductor && uv run --frozen --group selfcheck ruff format --check conductor tests/conductor && uv run --frozen --group selfcheck pyrefly check conductor`
Expected: без ошибок; найденное исправить в этом же шаге (`ruff format`, `ruff check --fix`), не меняя поведения.

- [ ] **Step 5: Commit**

```bash
git add conductor tests/conductor
git commit -m "feat(conductor): сбор входов, рендер, CLI status/why/plan/run/record"
```

---

### Task 13: Таймер на VPS (уровень 0)

**Files:**
- Create: `deploy/conductor/conductor.service`, `deploy/conductor/conductor.timer`, `deploy/conductor/setup.sh`, `deploy/conductor/README.md`, `tests/conductor/test_deploy.py`

- [ ] **Step 1: Write the failing test**

`tests/conductor/test_deploy.py`:
```python
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_service_runs_level_0_under_flock() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "flock -n /srv/conductor/state/conductor.lock" in unit
    assert "-m conductor run" in unit and "--level" not in unit
    assert "User=conductor" in unit and "TimeoutStartSec=55min" in unit


def test_timer_hourly_and_setup_does_not_enable() -> None:
    assert "OnCalendar=hourly" in (DEPLOY / "conductor.timer").read_text()
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    assert "systemctl enable" not in setup
    assert "clone_fleet.py" in setup and "--root" in setup
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
# run_timeout_min (спека §2.1): зависший git/gh не держит замок вечно.
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
# Installs units but does NOT enable the timer: that is the owner's step in
# deploy/conductor/README.md.
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

Срез 0 **ничего не пишет** во флот: прогон читает клоны и GitHub и пишет снимок
в `/srv/conductor/state/runs/<run_id>/`. GitHub App не нужен.

1. `sudo GIT_BASE=git@github.com:andrei-shtanakov deploy/conductor/setup.sh`
2. Авторизация `gh` **только на чтение** (fine-grained токен владельца:
   `contents`, `issues`, `pull_requests`, `metadata`, `checks`, `statuses` —
   read) в `GH_CONFIG_DIR=/srv/conductor/gh` пользователя `conductor`.
3. Пробный прогон: `sudo systemctl start conductor.service`,
   `journalctl -u conductor -n 50`, снимок — в `state/runs/`.
4. Включить таймер: `sudo systemctl enable --now conductor.timer`.
5. `hostname` VPS — значение `writer_host` в `roadmap.toml` зонтика (в срезе 0
   используется только для отчёта; писать начнёт срез 1).

Обновление кода: `sudo -u conductor git -C /srv/conductor/devtools pull --ff-only`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/conductor/test_deploy.py -q && uv run --frozen --group selfcheck shellcheck deploy/conductor/setup.sh`
Expected: PASS (2 passed); shellcheck без замечаний

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

- [ ] **Step 1: Write the skill**

`skills/conductor/SKILL.md`:
```markdown
---
name: conductor
description: Советчик флота — что сейчас главное, почему issue/пункт не закрыт, кто кого ждёт. Использовать при вопросах «почему не закрыт X», «что дальше», «кто чего ждёт».
---

# conductor — советчик (срез 0)

Ничего не пишет во флот. Команды — из devtools:

- `make conductor ARGS=status` — фокусы, очередь с `why`, «нужны решения»,
  циклы, ожидания, вопросы владельцу.
- `make conductor ARGS="why <node>"` — цепочка от узла до листьев с
  `need/actor`; `<node>` — `todo://<repo>/<id>`, `<repo>#<N>` или `<repo>!<N>`.
- `make conductor ARGS="plan --level 3"` — что conductor сделал бы на уровне 3
  (`launch?` — authority-root ещё не проверяется).
- `make conductor ARGS="record out/conductor/replays/<дата>"` — сохранить входы;
  `python -m conductor status --replay <dir>/inputs.json` — разбор без сети.

Отвечая пользователю, цитируй строку `why` и называй `partial`, если граф
неполон: «блокеров нет» на неполном графе не утверждается.
```

- [ ] **Step 2: Update CLAUDE.md**

В таблицу «Инструменты» после строки `selfcheck/` добавить:
```markdown
| `conductor/` | агент-оркестратор флота (спека `docs/superpowers/specs/2026-09-29-conductor-design.md`). Срез 0 — советчик: `make conductor ARGS=status\|"why <node>"\|"plan --level N"\|run\|"record <dir>"`; читает TODO/роадмап/эпики с `origin/<default>` и GitHub, строит граф `depends_on/accepted_as/implements/mentions`, ранжирует по `roadmap.toml` зонтика, пишет снимок `conductor-snapshot/v1` в `out/conductor/`. Во флот не пишет; VPS-таймер — `deploy/conductor/` |
```

- [ ] **Step 3: Update TODO.md**

В разделе «conductor — агент-оркестратор флота» добавить:
```markdown
- [ ] conductor срез 0 — советчик: граф, ожидания, порядок, `status/why/plan/run`, снимок, таймер уровня 0; приёмка — replay на сохранённых входах, верх очереди совпадает с приоритетами владельца @owner:github:andrei-shtanakov @id:conductor-slice-0 @epic:eco.tooling
```

- [ ] **Step 4: Verify**

Run: `make plan-check-selftest && uv run --frozen pytest tests/conductor -q`
Expected: selftest ok; все тесты пакета PASS

- [ ] **Step 5: Commit**

```bash
git add skills/conductor CLAUDE.md TODO.md
git commit -m "docs(conductor): скилл, строка инструментов, пункт среза 0"
```

---

### Task 15: `roadmap.toml` в зонтике (отдельный репо, свой PR)

**Files (в репо `ai-orchestrators-workspace`):**
- Create: `roadmap.toml`
- Modify: `.gitignore` (белый список: добавить `!/roadmap.toml`)

Зонтик живёт по своим правилам (ветка → PR → ревью ai-prosto → мерж). Ловушка: `.gitignore` зонтика — белый список; без `!/roadmap.toml` файл игнорируется молча.

- [ ] **Step 1: Черновик от текущих приоритетов**

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
`writer_host` — фактический `hostname` VPS из Task 13 (README, шаг 5); впишите его до коммита.

- [ ] **Step 2: Проверить разбором conductor**

Run (из devtools): `uv run --frozen python -m conductor status --roadmap ../ai-orchestrators-workspace/roadmap.toml --no-fetch | head -6`
Expected: нет строки `RM-INVALID`; `фокусы: eco.dark-factory, eco.tooling, eco.governance-plane`

- [ ] **Step 3: PR в зонтик**

```bash
git -C ../ai-orchestrators-workspace switch -c feat/roadmap-toml
git -C ../ai-orchestrators-workspace add roadmap.toml .gitignore
git -C ../ai-orchestrators-workspace commit -m "feat: roadmap.toml — порядок фокусов для conductor (срез 0)"
git -C ../ai-orchestrators-workspace push -u origin feat/roadmap-toml
gh pr create -R andrei-shtanakov/ai-orchestrators-workspace --fill
```
Порядок фокусов — решение владельца: PR просит подтвердить или переставить.

---

### Task 16: Живая приёмка — replay на сохранённых входах

Приёмка среза 0 по спеке §10: верх очереди совпадает с приоритетами владельца и показывает ранее невидимые цепочки.

- [ ] **Step 1: Записать входы флота**

Run: `make conductor ARGS="record out/conductor/replays/$(date +%F)"`
Expected: `out/conductor/replays/<дата>/inputs.json`; код 0

- [ ] **Step 2: Разбор без сети**

Run: `uv run --frozen python -m conductor status --replay out/conductor/replays/$(date +%F)/inputs.json`
Expected: `граф: complete` (иначе — перечень непрочитанных источников, починить до приёмки); очередь с `why`; блок «нужны решения»; число вопросов владельцу

- [ ] **Step 3: Проверить известную цепочку**

Run: `uv run --frozen python -m conductor why todo://devtools/bundle-docs-as-oracle --replay out/conductor/replays/$(date +%F)/inputs.json`
Expected: видны ожидания `spec-runner#603` / `steward#190` с вердиктами и `need/actor` узлов; пара `devtools#491` ↔ `spec-runner#603` — `цикл:` в `status`, если обе стороны оформлены структурно, иначе `GR-WEAK-EDGE` в снимке

- [ ] **Step 4: Разбор с владельцем**

Показать владельцу первые 15 позиций `status`, блок «нужны решения» и `metrics` снимка `run`. Записать итог в TODO.md (пункт `conductor-slice-0`): совпадает ли верх очереди с приоритетами, какие цепочки новые, сколько вопросов и по каким причинам (`owner_questions_by_reason`), сколько `decide_unranked`. Несовпадение — находка для роадмапа или ранжирования, а не повод закрывать пункт.

---

## Self-Review (выполнен)

1. **Покрытие спеки (срез 0, §11):** §2 — Task 2 (+`RM-GOAL-MISSING` в Task 8); §3.1 — Tasks 3–6, 12; §3.2 — Task 6; §3.3 — Task 8; §3.4 (структурные `date>=`/`exists:`, `version_mismatch`, `cancelled` по истории, застой по началу ожидания и движению) — Tasks 4, 7, 12; §3.5 (`in_review`/`idle` по членам склейки) — Task 8; §4 — Task 9; §5.2–5.3 как выдача, PR-таблица с `wait_ci`, вопросы только с рангом — Task 10; §7.1 и показатели §10 — Task 11; §7.2 — Task 12; таймер — Task 13; роадмап в зонтике — Task 15; replay-приёмка — Task 16. Вне среза 0: запросы §6 (кроме наследия inbox), записи §5.1/§5.4–5.10, App §8, публикация §7.4, authority-root (Task 10 выдаёт `unverified`/`launch?`).
2. **Плейсхолдеры:** нет; флаги `clone_fleet.py` сверены по `--help`; ребро на несуществующий пункт — из `references` (сверено на `plan-fields`); полнота поиска — REST `search/issues` (`gh search --json` теряет `incomplete_results`).
3. **Согласованность типов:** `Graph.resolve/members/epic_of`, `Wait(…, since, moved)`, `QueueEntry(… via_focus, own_focus …)`, `Assessment(… delegable, …, ask_owner)`, `Result(… attention, assessments: dict …)` — одни имена во всех задачах.
4. **Review Focus:** п.1–2 — Task 4; п.3 — Tasks 5, 6, 12; п.4 — Task 6; п.5 — Task 8.

## Проверка плана исполнением

Перед ревью листинги кода извлечены из этого файла скриптом (блоки после строки вида `` `путь`: ``; для `conductor/__main__.py` — последний, итоговый) во временный каталог вне репо, в раскладке devtools. Результат на 2026-09-29:

- `pytest tests/conductor` на `.venv` devtools — **79 passed** (rev 4);
- `ruff check` и `ruff format --check` с `pyproject.toml` devtools — чисто (листинги в плане — уже отформатированный вывод ruff);
- `pyrefly check conductor` — 0 errors; `shellcheck deploy/conductor/setup.sh` — чисто; `python -m conductor --selftest` — ok.

**Живой прогон на флоте** (`record --no-fetch --roadmap <черновик Task 15>`, только чтение): граф `complete`, 875 узлов, 133 ребра; первая позиция очереди — `spec-runner#603` с `why` = `rank 1 (eco.dark-factory) via todo://devtools/bundle-oracle-slice1 → spec-runner#603; unblocks 3`; циклов нет; `GR-WEAK-EDGE` 64, `GR-ORPHAN-REQUEST` 6, `GR-DANGLING-WAIT` 2; вопросов владельцу 9 (`decision-signal` 5, `owner-tbd` 3, `unknown-wait` 1). Прогон изменил план в двух местах: (1) поиск закрытых за 30 дней вернул 1310 > 1000 — граф был бы `partial` всегда, поэтому обнаружение — только открытые, закрытые приходят дочитыванием; (2) 30 из 38 вопросов владельцу были ожиданиями прозаического `@trigger` — теперь это `wait_condition` без вопроса. Повторный живой прогон после rev 3 (2 мин 37 с на Mac, без `fetch`): граф `complete`, вспомогательные чтения без ошибок, `wait_since` у 46 ожиданий (по `git blame`); из 12 открытых PR один (`atp-platform!322`) одобрен на head SHA с зелёным CI → `merge`/`merge-contour`, остальные — `review`; вопросы владельцу 9 (`decision-signal` 5, `owner-tbd` 3, `missing` 1 — с вариантами «снять ожидание / завести запрос»). Прогон после rev 4: манифест и реестр эпиков — с `origin` зонтика (SHA `34d96ee`), все источники `read`, граф `complete`; `GR-ORPHAN-REQUEST` 20, `GR-SLUG-MATCH` 18 (наследие inbox), вопросов владельцу 9, в `status` — с `Q-<id>` и вариантами по причине. Замечание для Task 15: веха-черновик `todo://devtools/bundle-docs-as-oracle` на момент прогона уже закрыта — владельцу стоит выбрать живую веху.
