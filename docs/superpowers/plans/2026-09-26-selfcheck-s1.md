# selfcheck S1 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** реализовать этап S1 selfcheck — детерминированный конвейер
статических проб над devtools, который выдаёт отчёт (JSON + Markdown) с
находками bug / quality / dead / duplicate / deps / llm-replaceable /
selfcheck и дельтой к прошлому прогону.

**Architecture:** пакет `devtools/selfcheck/`. Ядро материализует корпус репо
в read-only копию, прогоняет по реестру пробы (внешние инструменты и
собственные анализаторы) под единым контрактом статусов и канареек,
агрегирует находки по стабильному ключу, применяет политику окружения и
allowlist, считает дельту по судьбам файлов и пишет отчёт в
`out/selfcheck/<run_id>/`. Инструменты закреплены версиями в uv-группе
`selfcheck` (PyPI-обёртки для shellcheck/actionlint) и `npx` для jscpd.

**Tech Stack:** Python ≥3.12 (stdlib + `pyyaml`, уже в зависимостях
devtools), pytest; инструменты группы `selfcheck`: ruff 0.16.9, pyrefly
1.3.1, vulture 2.16, deptry 0.25.1, radon 6.0.1, shellcheck-py 0.11.0.1,
actionlint-py 1.7.12.25, zizmor 1.30.1, semgrep 1.178.0; jscpd 4.3.0 через
`npx`.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md` (rev 5.2,
converged). План покрывает этап S1 (§7); S2 (`--fleet`), S3 (все репо, TS в
`llm-sites`), S5 (`--judge`) — отдельные пункты TODO; S4 — отдельная спека.

## Global Constraints

- Shipped-код не читает и не резолвит `_cowork_output/` (корневой CLAUDE.md,
  спека «Основания»).
- Пробы S1 не исполняют код цели: у каждой `ProbeSpec`
  `executes_target_code = False`, и тест реестра это проверяет (спека §1.2).
- Пробы получают только путь к read-only копии корпуса
  `out/selfcheck/<run_id>/src/<repo>/`; кэши и вывод — в
  `out/selfcheck/<run_id>/work/<probe>/<repo>/` (спека §1.3).
- Git исходника читается только `git ls-files` и `git log` с
  `GIT_OPTIONAL_LOCKS=0`; `git status` не используется (спека §1.3).
- Окружение цели — только данные: каталог `site-packages` цели никогда не
  попадает в `PYTHONPATH`/путь импорта, интерпретатор цели не запускается
  (спека §1.5).
- `DEP003` отключён всегда (`--ignore DEP003`) (спека §1.5).
- `id = "sc-" + sha1(rule|owner_repo|anchor|text_key)[:8]`; для якорей
  `dup:*` — `sha1(rule|anchor)` без `owner_repo` (спека §2.1).
- Коды выхода: 0 — все пробы `ok`/`skipped`; 2 — есть `failed`/`partial`;
  3 — только `unavailable`; 4 — ошибка манифеста/конфига/материализации/
  записи отчёта (спека §4.3).
- `run_id` = `YYYYMMDDTHHMMSSZ-<6 hex>`, каталог создаётся без `exist_ok`
  (спека §1).
- Уверенность находок внешних линтеров — `likely` (спека §2.3, rev 5.2).
- Стиль репо: type hints везде, docstring у публичных функций, строки ≤ 88
  (`ruff format` перед каждым коммитом),
  `uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck`
  и `uv run --frozen --group selfcheck pyrefly check selfcheck` зелёные
  после каждой задачи.
- Тесты, которым нужны внешние инструменты, вызывают `require_tool(name)`:
  без инструмента — skip, а при `SELFCHECK_REQUIRE_TOOLS=1` — fail (так CI
  не может молча пропустить прибор).

## Review Focus

1. **Репо без Python** (только shell, нет `pyproject.toml`) — Python-пробы
   `skipped: language`, не `failed`; shell-пробы работают. Тест — Task 5.
2. **Имена файлов с пробелами и не-ASCII** в корпусе — `ls-files -z`,
   материализация и argv инструментов их не ломают. Тест — Task 3.
3. **Отслеживаемый файл удалён в рабочей копии** (есть в `ls-files --cached`,
   нет на диске) — пропускается без исключения. Тест — Task 3.
4. **Инструмент зависает** — `timeout` даёт `failed: timeout` этой пробе,
   остальные пробы прогона выполняются. Тест — Task 5.
5. **Makefile с переносами `\`, рецептами в строке `target: ; cmd` и
   `$(VAR)`** — парсер не падает, рёбра из продолжений строк находятся.
   Тест — Task 8.

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `selfcheck/__init__.py` | версия пакета |
| `selfcheck/__main__.py` | `python -m selfcheck` → `run.main` |
| `selfcheck/model.py` | `Confidence`, `Location`, `Finding`, `finding_id`, `aggregate` (§2) |
| `selfcheck/anchors.py` | `python_anchor` — якорь функции по строке |
| `selfcheck/roles.py` | `Role`, `glob_match`, `role_of` (§1.4) |
| `selfcheck/config.py` | `selfcheck.toml`: allowlist, роли, исключения корпуса (§2.4) |
| `selfcheck/manifest.py` | репо из манифеста, dedup по `git_dir`, языки (§1.2) |
| `selfcheck/corpus.py` | корпус, read-only копия, уборка, возраст файлов (§1.3–1.4) |
| `selfcheck/env.py` | режимы окружения, `package_module_map`, политика import-класса (§1.5) |
| `selfcheck/probes/base.py` | контракт пробы, статусы, канарейка, покрытие (§4.1–4.2) |
| `selfcheck/probes/common.py` | построение находок из строк, относительные пути |
| `selfcheck/probes/python_tools.py` | ruff, pyrefly, vulture, radon, deptry |
| `selfcheck/probes/other_tools.py` | shellcheck, actionlint, zizmor, jscpd |
| `selfcheck/graph/model.py` | узлы, рёбра, зоны, `Surface` |
| `selfcheck/graph/commands.py` | `scan_command` — цели команд в корпусе |
| `selfcheck/graph/build.py` | узлы и рёбра из Makefile/CI/импортов/skills/расписаний/доков |
| `selfcheck/graph/resolver.py` | вычисляемые запуски (Python, shell), зоны (§3.2.3) |
| `selfcheck/graph/classify.py` | классы, допустимость, уверенность dead, корни (§2.3, §3.2.2) |
| `selfcheck/graph/probe.py` | проба `usage-graph` |
| `selfcheck/dups.py` | пробы `ast-dup`, `cli-overlap` (§3.3) |
| `selfcheck/llm.py` + `selfcheck/rules/llm.yml` | проба `llm-sites` (§3.4) |
| `selfcheck/registry.py` | `REGISTRY` всех проб S1 |
| `selfcheck/delta.py` | судьбы, дельта (§4.3) |
| `selfcheck/report.py` | `run_id`, базовый прогон, JSON/Markdown |
| `selfcheck/run.py` | оркестратор и CLI |
| `selfcheck.toml` | allowlist devtools |
| `tests/selfcheck/helpers.py` | `make_repo`, `require_tool` |
| `tests/selfcheck/test_*.py` | тесты по задачам |

Правки: `pyproject.toml` (группа `selfcheck`), `uv.lock`, `Makefile`
(цели + help), `.github/workflows/ci.yml` (шаг selfcheck), `CLAUDE.md`
(строка в таблице инструментов), `TODO.md` (пункты S1–S5).

---

### Task 1: каркас, модель находки и стабильная идентичность

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (группа `selfcheck`)
- Create: `selfcheck/__init__.py`, `selfcheck/model.py`, `selfcheck/anchors.py`
- Create: `tests/selfcheck/__init__.py`, `tests/selfcheck/helpers.py`
- Test: `tests/selfcheck/test_identity.py`

**Interfaces:**
- Produces: `Confidence` (`CANDIDATE`/`LIKELY`/`CONFIRMED`), `cap(value, limit) -> Confidence`,
  `Location(path: str, line: int)`, `Finding(...)` c полями `rule, category, severity,
  confidence, owner_repo, anchor, locations, text_key=None, group=None, related=[],
  evidence=[], suggestion="", judge=None` и свойствами `probe`, `occurrences`, `id`,
  методом `to_json() -> dict`; `normalize_line(str) -> str`, `make_text_key(str) -> str`,
  `finding_id(rule, owner_repo, anchor, text_key) -> str`,
  `aggregate(Iterable[Finding]) -> list[Finding]`;
  `python_anchor(source: str, path: str, line: int) -> str`;
  helpers: `make_repo(root, files, *, date="2026-01-01T00:00:00") -> Path`,
  `commit(repo, files, *, date) -> None`, `require_tool(name) -> None`.

- [ ] **Step 1: добавить группу инструментов**

```bash
cd devtools
uv add --group selfcheck ruff==0.16.9 pyrefly==1.3.1 vulture==2.16 \
  deptry==0.25.1 radon==6.0.1 shellcheck-py==0.11.0.1 \
  actionlint-py==1.7.12.25 zizmor==1.30.1 semgrep==1.178.0
```

Expected: в `pyproject.toml` появилась `[dependency-groups] selfcheck = [...]`,
`uv.lock` обновлён. Проверка: `uv run --frozen --group selfcheck ruff --version`
→ `ruff 0.16.9`.

- [ ] **Step 2: тестовые помощники**

`tests/selfcheck/__init__.py` — пустой. `tests/selfcheck/helpers.py`:

```python
"""Test helpers for selfcheck: throwaway git repos and tool gating."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REQUIRE_TOOLS = os.environ.get("SELFCHECK_REQUIRE_TOOLS") == "1"


def require_tool(name: str) -> None:
    """Skip when ``name`` is absent; fail instead when tools are required."""
    if shutil.which(name) is not None:
        return
    if REQUIRE_TOOLS:
        pytest.fail(f"{name} missing while SELFCHECK_REQUIRE_TOOLS=1")
    pytest.skip(f"{name} not installed (uv run --group selfcheck)")


def _git(repo: Path, *args: str, date: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True)


def commit(repo: Path, files: dict[str, str], *, date: str) -> None:
    """Write ``files`` into ``repo`` and commit them with a fixed date."""
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A", date=date)
    _git(repo, "commit", "-q", "-m", "fixture", date=date)


def make_repo(root: Path, files: dict[str, str], *,
              date: str = "2026-01-01T00:00:00") -> Path:
    """Create a git repo at ``root`` with one dated commit of ``files``."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    commit(root, files, date=date)
    return root
```

- [ ] **Step 3: написать падающие тесты идентичности (C1–C10 спеки §2.1)**

`tests/selfcheck/test_identity.py`:

```python
from __future__ import annotations

import random

from selfcheck.anchors import python_anchor
from selfcheck.model import (
    Confidence,
    Finding,
    Location,
    aggregate,
    cap,
    finding_id,
    make_text_key,
)

SRC = """def alpha():
    x = eval("1")
    y = eval("1")
    return x + y


def beta():
    return eval("2")
"""


def raw(rule: str, source: str, line: int, path: str = "m.py",
        owner: str = "devtools") -> Finding:
    text = source.splitlines()[line - 1]
    return Finding(
        rule=rule, category="bug", severity="medium",
        confidence=Confidence.LIKELY, owner_repo=owner,
        anchor=python_anchor(source, path, line),
        locations=[Location(path, line)], text_key=make_text_key(text),
    )


def ids(findings: list[Finding]) -> set[str]:
    return {f.id for f in aggregate(findings)}


def test_c1_insert_above_keeps_id() -> None:
    shifted = "\n\n" + SRC
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", shifted, 10).id


def test_c2_c3_identical_lines_aggregate() -> None:
    two = aggregate([raw("ruff/S307", SRC, 2), raw("ruff/S307", SRC, 2)])
    one_src = SRC.replace('    y = eval("1")\n', "    y = 1\n")
    one = aggregate([raw("ruff/S307", one_src, 2)])
    pair = aggregate([raw("ruff/S307", SRC, 2), raw("ruff/S307", SRC, 3)])
    assert len(pair) == 2  # lines 2 and 3 differ in text (x vs y)
    same_text = SRC.replace('    y = eval("1")', '    x = eval("1")')
    agg = aggregate([raw("ruff/S307", same_text, 2),
                     raw("ruff/S307", same_text, 3)])
    assert len(agg) == 1 and agg[0].occurrences == 2
    assert agg[0].id == one[0].id
    assert two[0].occurrences == 1  # same location twice is one occurrence


def test_c4_changed_text_changes_id() -> None:
    changed = SRC.replace('return eval("2")', 'return eval("3")')
    assert raw("ruff/S307", SRC, 8).id != raw("ruff/S307", changed, 8).id


def test_c5_c6_move_or_rename_changes_anchor() -> None:
    renamed = SRC.replace("def beta", "def gamma")
    assert raw("ruff/S307", SRC, 8).anchor == "func:m.py::beta"
    assert raw("ruff/S307", renamed, 8).id != raw("ruff/S307", SRC, 8).id


def test_c7_two_rules_two_findings() -> None:
    assert len(ids([raw("ruff/S307", SRC, 8), raw("pyrefly/x", SRC, 8)])) == 2


def test_c8_order_does_not_matter() -> None:
    items = [raw("ruff/S307", SRC, n) for n in (2, 3, 8)]
    shuffled = items[:]
    random.Random(1).shuffle(shuffled)
    assert ids(items) == ids(shuffled)


def test_c9_whitespace_inside_line_ignored() -> None:
    spaced = SRC.replace('return eval("2")', 'return   eval("2")')
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", spaced, 8).id


def test_c10_dup_id_independent_of_owner() -> None:
    a = finding_id("ast-dup/exact", "devtools", "dup:exact:abc", None)
    b = finding_id("ast-dup/exact", "maestro", "dup:exact:abc", None)
    assert a == b


def test_module_level_line_gets_file_anchor() -> None:
    assert python_anchor("x = 1\n", "m.py", 1) == "file:m.py"
    assert python_anchor("def f(:\n", "m.py", 1) == "file:m.py"


def test_cap_takes_lower() -> None:
    assert cap(Confidence.CONFIRMED, Confidence.LIKELY) is Confidence.LIKELY
    assert cap(Confidence.CANDIDATE, Confidence.LIKELY) is Confidence.CANDIDATE
```

- [ ] **Step 4: убедиться, что тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck'`.

- [ ] **Step 5: реализовать модель и якоря**

`selfcheck/__init__.py`:

```python
"""selfcheck — static self-diagnosis for devtools and the fleet."""

__version__ = "0.1.0"
```

`selfcheck/model.py`:

```python
"""Finding model and stable identity (spec §2, §2.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class Confidence(StrEnum):
    """Confidence scale, lowest first (spec §2.3)."""

    CANDIDATE = "candidate"
    LIKELY = "likely"
    CONFIRMED = "confirmed"


_RANK = {Confidence.CANDIDATE: 0, Confidence.LIKELY: 1, Confidence.CONFIRMED: 2}


def cap(value: Confidence, limit: Confidence) -> Confidence:
    """Return the lower of ``value`` and ``limit``."""
    return value if _RANK[value] <= _RANK[limit] else limit


@dataclass(frozen=True, order=True)
class Location:
    """A path relative to the repo root and a 1-based line."""

    path: str
    line: int


@dataclass
class Finding:
    """One atomic finding: one probe, one rule, one key (spec §2.1)."""

    rule: str
    category: str
    severity: str
    confidence: Confidence
    owner_repo: str
    anchor: str
    locations: list[Location]
    text_key: str | None = None
    group: str | None = None
    related: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    suggestion: str = ""
    judge: dict[str, Any] | None = None

    @property
    def probe(self) -> str:
        return self.rule.split("/", 1)[0]

    @property
    def occurrences(self) -> int:
        return len(self.locations)

    @property
    def id(self) -> str:
        return finding_id(self.rule, self.owner_repo, self.anchor, self.text_key)

    def to_json(self) -> dict[str, Any]:
        """Serialise for report.json."""
        return {
            "id": self.id,
            "rule": self.rule,
            "probe": self.probe,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence.value,
            "owner_repo": self.owner_repo,
            "anchor": self.anchor,
            "text_key": self.text_key,
            "group": self.group or self.anchor,
            "occurrences": self.occurrences,
            "locations": [{"path": x.path, "line": x.line} for x in self.locations],
            "related": self.related,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "judge": self.judge,
        }


def normalize_line(text: str) -> str:
    """Strip and collapse whitespace runs (spec §2.1, text_key)."""
    return " ".join(text.split())


def make_text_key(line_text: str) -> str:
    """sha1 of the normalised violating line."""
    return hashlib.sha1(normalize_line(line_text).encode()).hexdigest()


def finding_id(rule: str, owner_repo: str, anchor: str,
               text_key: str | None) -> str:
    """Stable id; duplicate anchors exclude the (derived) owner."""
    if anchor.startswith("dup:"):
        raw = f"{rule}|{anchor}"
    else:
        raw = f"{rule}|{owner_repo}|{anchor}|{text_key or ''}"
    return "sc-" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def aggregate(findings: Iterable[Finding]) -> list[Finding]:
    """Merge findings sharing an id into one with all locations."""
    merged: dict[str, Finding] = {}
    for item in findings:
        current = merged.get(item.id)
        if current is None:
            merged[item.id] = replace(
                item,
                locations=sorted(set(item.locations)),
                evidence=list(item.evidence),
                related=list(item.related),
            )
            continue
        current.locations = sorted(set(current.locations) | set(item.locations))
        current.evidence += [e for e in item.evidence if e not in current.evidence]
        current.related += [r for r in item.related if r not in current.related]
    return sorted(merged.values(), key=lambda f: f.id)
```

`selfcheck/anchors.py`:

```python
"""Anchors for line-level findings (spec §2.1)."""

from __future__ import annotations

import ast


def python_anchor(source: str, path: str, line: int) -> str:
    """Innermost function containing ``line`` → ``func:``, else ``file:``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"file:{path}"
    best: str | None = None

    def visit(node: ast.AST, prefix: str) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                end = child.end_lineno or child.lineno
                is_func = not isinstance(child, ast.ClassDef)
                if is_func and child.lineno <= line <= end:
                    best = qual
                visit(child, qual)
            else:
                visit(child, prefix)

    visit(tree, "")
    return f"func:{path}::{best}" if best else f"file:{path}"
```

- [ ] **Step 6: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_identity.py -q`
Expected: PASS (10 passed).

- [ ] **Step 7: линт и типы**

Run: `uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck && uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck && uv run --frozen --group selfcheck pyrefly check selfcheck`
Expected: без ошибок.

- [ ] **Step 8: коммит**

```bash
git add pyproject.toml uv.lock selfcheck tests/selfcheck
git commit -m "feat(selfcheck): каркас, модель находки, стабильный id (спека §2.1)"
```

---

### Task 2: роли путей, конфиг и allowlist, манифест и языки

**Files:**
- Create: `selfcheck/roles.py`, `selfcheck/config.py`, `selfcheck/manifest.py`
- Test: `tests/selfcheck/test_config_manifest.py`

**Interfaces:**
- Consumes: `Finding`, `Location`, `Confidence` (Task 1).
- Produces: `Role` (StrEnum: `SOURCE="source"`, `SKILL_ROOT="skill-root"`, `TEST="test"`,
  `DOCUMENTATION="documentation"`, `DIAGNOSTIC_OUTPUT="diagnostic-output"`,
  `CANARY="canary"`), `glob_match(pattern, path) -> bool`,
  `role_of(path, extra: Mapping[str, Sequence[str]] | None = None) -> Role`;
  `ConfigError(ValueError)`, `AllowEntry`, `Config(allow, roles, corpus_exclude, sha1)`,
  `load_config(path: Path) -> Config`, `AllowResult(kept, suppressed, expired)`,
  `apply_allowlist(findings, config, today: date) -> AllowResult`;
  `RepoEntry(name: str, path: Path, languages: frozenset[str])`,
  `ManifestInfo(entries_read: int, repos: tuple[RepoEntry, ...], missing: tuple[str, ...])`,
  `load_manifest(manifest: Path, workspace: Path) -> ManifestInfo`,
  `detect_languages(path: Path) -> frozenset[str]` (значения `python`, `rust`, `elixir`, `ts`).

- [ ] **Step 1: падающие тесты**

`tests/selfcheck/test_config_manifest.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from selfcheck.config import ConfigError, apply_allowlist, load_config
from selfcheck.manifest import load_manifest
from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import Role, glob_match, role_of


@pytest.mark.parametrize(
    ("path", "role"),
    [
        (".selfcheck-canary/ruff/canary.py", Role.CANARY),
        ("selfcheck_canary/__init__.py", Role.CANARY),
        ("reports/2026-07-10-x.md", Role.DIAGNOSTIC_OUTPUT),
        ("skills/fleet-check/SKILL.md", Role.SKILL_ROOT),
        (".claude/skills/kb/SKILL.md", Role.SKILL_ROOT),
        ("authored/skills/kb-search/SKILL.md", Role.SKILL_ROOT),
        (".claude/commands/do.md", Role.SKILL_ROOT),
        ("tests/test_x.py", Role.TEST),
        ("governance/test_helper.py", Role.TEST),
        ("README.md", Role.DOCUMENTATION),
        ("docs/runbook.txt", Role.DOCUMENTATION),
        ("issue_worker.py", Role.SOURCE),
        ("skills/fleet-check/extra/SKILL.md", Role.DOCUMENTATION),
    ],
)
def test_default_roles(path: str, role: Role) -> None:
    assert role_of(path) is role


def test_glob_semantics() -> None:
    assert glob_match("skills/*/SKILL.md", "skills/a/SKILL.md")
    assert not glob_match("skills/*/SKILL.md", "skills/a/b/SKILL.md")
    assert glob_match("**/*.md", "a/b/c.md") and glob_match("**/*.md", "c.md")


def test_extra_roles_win(tmp_path: Path) -> None:
    extra = {"diagnostic-output": ["notes/**"]}
    assert role_of("notes/x.md", extra) is Role.DIAGNOSTIC_OUTPUT


def write(tmp: Path, text: str) -> Path:
    path = tmp / "selfcheck.toml"
    path.write_text(text)
    return path


def test_missing_config_is_empty(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.allow == () and cfg.corpus_exclude == ()


@pytest.mark.parametrize(
    "body",
    [
        '[[allow]]\nanchor = "file:x.py"\nuntil = 2027-01-01\n',
        '[[allow]]\nanchor = "file:x.py"\nreason = "r"\n',
        '[[allow]]\nreason = "r"\nuntil = 2027-01-01\n',
        '[roles]\nbogus = ["x"]\n',
        "not toml ===",
    ],
)
def test_bad_config_raises(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body))


def finding(anchor: str) -> Finding:
    return Finding(rule="ruff/X", category="bug", severity="low",
                   confidence=Confidence.LIKELY, owner_repo="devtools",
                   anchor=anchor, locations=[Location("x.py", 1)])


def test_allow_file_anchor_covers_functions(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, (
        '[[allow]]\nanchor = "file:issue_console.py"\nreason = "owner"\n'
        "until = 2027-01-01\n")))
    items = [finding("func:issue_console.py::main"),
             finding("file:issue_console.py"), finding("func:other.py::f")]
    res = apply_allowlist(items, cfg, date(2026, 9, 26))
    assert [f.anchor for f in res.kept] == ["func:other.py::f"]
    assert len(res.suppressed) == 2 and res.expired == []


def test_expired_allow_becomes_finding(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, (
        '[[allow]]\nanchor = "file:a.py"\nreason = "r"\nuntil = 2026-01-01\n')))
    res = apply_allowlist([finding("file:a.py")], cfg, date(2026, 9, 26))
    assert len(res.kept) == 1
    assert [f.rule for f in res.expired] == ["selfcheck/allow-expired"]


def test_manifest_dedup_and_missing(tmp_path: Path) -> None:
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "a" / "pyproject.toml").write_text("")
    (tmp_path / "a" / "Cargo.toml").write_text("")
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[cores.a]\ngit_dir = "a"\n'
        '[cores.a-sdk]\ngit_dir = "a"\nmember = true\n'
        '[apps.b]\ngit_dir = "b"\n'
        '[tools.c]\ngit_dir = "a"\n')
    info = load_manifest(manifest, tmp_path)
    assert info.entries_read == 4
    assert [r.name for r in info.repos] == ["a"]
    assert info.repos[0].languages == frozenset({"python", "rust"})
    assert info.missing == ("b",)


def test_manifest_entry_without_git_dir(tmp_path: Path) -> None:
    manifest = tmp_path / "m.toml"
    manifest.write_text("[apps.x]\nrepo_url = 'u'\n")
    with pytest.raises(ConfigError):
        load_manifest(manifest, tmp_path)
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_config_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.config'`.

- [ ] **Step 3: реализация**

`selfcheck/roles.py`:

```python
"""Source roles by path registry (spec §1.4)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import cache


class Role(StrEnum):
    """What a corpus file may contribute to the usage graph."""

    SOURCE = "source"
    SKILL_ROOT = "skill-root"
    TEST = "test"
    DOCUMENTATION = "documentation"
    DIAGNOSTIC_OUTPUT = "diagnostic-output"
    CANARY = "canary"


ROLE_ORDER = (
    Role.CANARY,
    Role.DIAGNOSTIC_OUTPUT,
    Role.SKILL_ROOT,
    Role.TEST,
    Role.DOCUMENTATION,
    Role.SOURCE,
)
DEFAULT_ROLES: dict[Role, tuple[str, ...]] = {
    Role.CANARY: (".selfcheck-canary/**", "selfcheck_canary/**"),
    Role.DIAGNOSTIC_OUTPUT: ("reports/**", "out/**"),
    Role.SKILL_ROOT: (
        "skills/*/SKILL.md",
        ".claude/skills/*/SKILL.md",
        "authored/skills/*/SKILL.md",
        ".claude/commands/*.md",
    ),
    Role.TEST: ("tests/**", "**/test_*.py", "**/*_test.py"),
    Role.DOCUMENTATION: ("**/*.md", "docs/**"),
    Role.SOURCE: (),
}
ROLE_NAMES = frozenset(r.value for r in Role)


@cache
def _regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, path: str) -> bool:
    """Match a POSIX path; ``*`` stays within a segment, ``**`` crosses."""
    return _regex(pattern).match(path) is not None


def role_of(path: str, extra: Mapping[str, Sequence[str]] | None = None) -> Role:
    """Role of ``path``: configured extras first, then defaults, then source."""
    extra = extra or {}
    for role in ROLE_ORDER:
        if any(glob_match(p, path) for p in extra.get(role.value, ())):
            return role
    for role in ROLE_ORDER:
        if any(glob_match(p, path) for p in DEFAULT_ROLES[role]):
            return role
    return Role.SOURCE
```

`selfcheck/config.py`:

```python
"""selfcheck.toml: allowlist, roles, corpus exclusions (spec §1.4, §2.4)."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import ROLE_NAMES


class ConfigError(ValueError):
    """Invalid manifest or config — exit code 4."""


@dataclass(frozen=True)
class AllowEntry:
    """One allowlist record; reason and until are mandatory."""

    reason: str
    until: date
    id: str | None = None
    anchor: str | None = None

    def matches(self, finding: Finding) -> bool:
        """id equality, or anchor equality; ``file:`` covers its file."""
        if self.id is not None:
            return finding.id == self.id
        assert self.anchor is not None
        if finding.anchor == self.anchor:
            return True
        if not self.anchor.startswith("file:"):
            return False
        path = self.anchor.removeprefix("file:")
        return any(finding.anchor.startswith(f"{kind}:{path}::")
                   for kind in ("func", "llm"))


@dataclass(frozen=True)
class Config:
    """Parsed selfcheck.toml."""

    allow: tuple[AllowEntry, ...] = ()
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    corpus_exclude: tuple[str, ...] = ()
    sha1: str = hashlib.sha1(b"").hexdigest()


def _allow_entry(index: int, raw: dict[str, Any]) -> AllowEntry:
    missing = [k for k in ("reason", "until") if not raw.get(k)]
    if not (raw.get("id") or raw.get("anchor")):
        missing.append("id|anchor")
    if missing:
        raise ConfigError(f"[[allow]] #{index}: missing {', '.join(missing)}")
    until = raw["until"]
    if not isinstance(until, date):
        raise ConfigError(f"[[allow]] #{index}: until must be a TOML date")
    return AllowEntry(reason=str(raw["reason"]), until=until,
                      id=raw.get("id"), anchor=raw.get("anchor"))


def load_config(path: Path) -> Config:
    """Load ``path``; a missing file is an empty config."""
    if not path.exists():
        return Config()
    raw = path.read_bytes()
    try:
        data = tomllib.loads(raw.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    roles = {k: tuple(v) for k, v in data.get("roles", {}).items()}
    unknown = set(roles) - ROLE_NAMES
    if unknown:
        raise ConfigError(f"[roles]: unknown roles {sorted(unknown)}")
    allow = tuple(_allow_entry(i, e) for i, e in enumerate(data.get("allow", [])))
    exclude = tuple(data.get("corpus", {}).get("exclude", []))
    return Config(allow, roles, exclude, hashlib.sha1(raw).hexdigest())


@dataclass
class AllowResult:
    """Findings split by the allowlist, plus expired-entry findings."""

    kept: list[Finding]
    suppressed: list[Finding]
    expired: list[Finding]


def apply_allowlist(findings: list[Finding], config: Config,
                    today: date) -> AllowResult:
    """Suppress allowed findings; expired entries become findings."""
    active = [a for a in config.allow if a.until >= today]
    kept: list[Finding] = []
    suppressed: list[Finding] = []
    for item in findings:
        target = suppressed if any(a.matches(item) for a in active) else kept
        target.append(item)
    expired = [
        Finding(
            rule="selfcheck/allow-expired", category="selfcheck",
            severity="medium", confidence=Confidence.CONFIRMED,
            owner_repo="devtools", anchor=f"allow:{a.id or a.anchor}",
            locations=[Location("selfcheck.toml", 1)],
            evidence=[{"kind": "until", "detail": a.until.isoformat()}],
            suggestion="продлить с новой причиной или удалить запись",
        )
        for a in config.allow
        if a.until < today
    ]
    return AllowResult(kept, suppressed, expired)
```

`selfcheck/manifest.py`:

```python
"""Repo set from workspace-manifest.toml (spec §1.2)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from selfcheck.config import ConfigError

SECTIONS = ("cores", "apps", "tools")
MARKERS = {
    "pyproject.toml": "python",
    "Cargo.toml": "rust",
    "mix.exs": "elixir",
    "package.json": "ts",
}


@dataclass(frozen=True)
class RepoEntry:
    """One unique git_dir present on disk."""

    name: str
    path: Path
    languages: frozenset[str]


@dataclass(frozen=True)
class ManifestInfo:
    """What the manifest yielded; the report prints all three."""

    entries_read: int
    repos: tuple[RepoEntry, ...]
    missing: tuple[str, ...]


def detect_languages(path: Path) -> frozenset[str]:
    """Languages by marker files at the repo root."""
    return frozenset(lang for marker, lang in MARKERS.items()
                     if (path / marker).is_file())


def load_manifest(manifest: Path, workspace: Path) -> ManifestInfo:
    """Dedupe entries of cores/apps/tools by git_dir, keep manifest order."""
    try:
        data = tomllib.loads(manifest.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"manifest {manifest}: {exc}") from exc
    order: dict[str, None] = {}
    entries = 0
    for section in SECTIONS:
        for key, entry in data.get(section, {}).items():
            entries += 1
            git_dir = entry.get("git_dir")
            if not git_dir:
                raise ConfigError(f"manifest {section}.{key}: no git_dir")
            order.setdefault(git_dir, None)
    repos: list[RepoEntry] = []
    missing: list[str] = []
    for git_dir in order:
        path = workspace / git_dir
        if (path / ".git").exists():
            repos.append(RepoEntry(git_dir, path, detect_languages(path)))
        else:
            missing.append(git_dir)
    return ManifestInfo(entries, tuple(repos), tuple(missing))
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_config_manifest.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck tests/selfcheck
git commit -m "feat(selfcheck): роли путей, selfcheck.toml и allowlist, манифест"
```

---

### Task 3: корпус, read-only копия, уборка, чтение истории

**Files:**
- Create: `selfcheck/corpus.py`
- Test: `tests/selfcheck/test_corpus.py`

**Interfaces:**
- Consumes: `glob_match` (Task 2), `make_repo`, `commit` (Task 1).
- Produces: `list_corpus(repo: Path, exclude: Sequence[str] = ()) -> list[str]`,
  `materialize(repo: Path, files: Sequence[str], dest: Path, extra_files: Mapping[str, str]) -> None`,
  `release(dest: Path) -> str | None`, `last_commit_ts(repo: Path, rel: str) -> int | None`,
  `snapshot_hashes(root: Path) -> dict[str, str]` (для тестов неизменности исходника).

- [ ] **Step 1: падающие тесты (включая Review Focus 2 и 3)**

`tests/selfcheck/test_corpus.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from selfcheck.corpus import (
    last_commit_ts,
    list_corpus,
    materialize,
    release,
    snapshot_hashes,
)
from tests.selfcheck.helpers import commit, make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = make_repo(tmp_path / "r", {
        "a.py": "print(1)\n",
        "with space.sh": "#!/bin/sh\necho hi\n",
        "юникод.py": "x = 1\n",
        "gone.py": "x = 2\n",
        ".gitignore": ".venv/\nout/\n",
        "vendor/lib.py": "y = 1\n",
    })
    (root / "gone.py").unlink()                      # tracked, deleted (RF 3)
    (root / "untracked.py").write_text("z = 1\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "x.py").write_text("")
    (root / "link.py").symlink_to(root / "a.py")
    return root


def test_corpus_membership(repo: Path) -> None:
    files = list_corpus(repo, exclude=["vendor/**"])
    assert files == sorted(["a.py", "with space.sh", "юникод.py", ".gitignore",
                            "untracked.py"])


def test_materialize_is_read_only_and_released(repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "run" / "src" / "r"
    files = list_corpus(repo)
    materialize(repo, files, dest, {".selfcheck-canary/x/c.py": "import os\n"})
    assert (dest / "with space.sh").read_text().startswith("#!/bin/sh")
    assert os.access(dest / "with space.sh", os.X_OK) == os.access(
        repo / "with space.sh", os.X_OK)
    with pytest.raises(PermissionError):
        (dest / "a.py").write_text("tampered")
    with pytest.raises(PermissionError):
        (dest / "new.py").write_text("")
    assert release(dest) is None
    assert not dest.exists()


def test_materialize_refuses_existing_dest(repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    with pytest.raises(FileExistsError):
        materialize(repo, list_corpus(repo), dest, {})


def test_source_untouched(repo: Path, tmp_path: Path) -> None:
    before = snapshot_hashes(repo)
    index_mtime = (repo / ".git" / "index").stat().st_mtime_ns
    files = list_corpus(repo)
    materialize(repo, files, tmp_path / "copy", {})
    last_commit_ts(repo, "a.py")
    release(tmp_path / "copy")
    assert snapshot_hashes(repo) == before
    assert (repo / ".git" / "index").stat().st_mtime_ns == index_mtime


def test_last_commit_ts(repo: Path) -> None:
    commit(repo, {"b.py": "b = 1\n"}, date="2026-03-01T00:00:00")
    assert last_commit_ts(repo, "b.py") is not None
    assert last_commit_ts(repo, "a.py") < last_commit_ts(repo, "b.py")
    assert last_commit_ts(repo, "untracked.py") is None
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_corpus.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.corpus'`.

- [ ] **Step 3: реализация**

`selfcheck/corpus.py`:

```python
"""Corpus listing and the read-only copy (spec §1.3, §1.4)."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from selfcheck.roles import glob_match


def _git(repo: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, env=env)
    return proc.stdout


def list_corpus(repo: Path, exclude: Sequence[str] = ()) -> list[str]:
    """Tracked + untracked-not-ignored regular files, minus ``exclude``."""
    raw = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    names = sorted({p for p in raw.decode().split("\0") if p})
    result: list[str] = []
    for rel in names:
        if any(glob_match(p, rel) for p in exclude):
            continue
        full = repo / rel
        if full.is_symlink() or not full.is_file():
            continue
        result.append(rel)
    return result


def _chmod_tree(root: Path, *, writable: bool) -> None:
    paths = [root, *root.rglob("*")]
    for path in paths:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        new = mode | stat.S_IWUSR if writable else mode & ~0o222
        os.chmod(path, new)


def materialize(repo: Path, files: Sequence[str], dest: Path,
                extra_files: Mapping[str, str]) -> None:
    """Copy ``files`` and canaries into a fresh ``dest``, then make it read-only."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()  # never reuse (FileExistsError)
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target)
    for rel, text in extra_files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _chmod_tree(dest, writable=False)


def release(dest: Path) -> str | None:
    """Restore write bits and delete; return a warning instead of raising."""
    try:
        _chmod_tree(dest, writable=True)
        shutil.rmtree(dest)
    except OSError as exc:
        return f"cleanup failed: {dest}: {exc}"
    return None


def last_commit_ts(repo: Path, rel: str) -> int | None:
    """Unix time of the last commit touching ``rel``; None if never committed."""
    out = _git(repo, "log", "-1", "--format=%ct", "--", rel).strip()
    return int(out) if out else None


def snapshot_hashes(root: Path) -> dict[str, str]:
    """sha1 of every regular file under ``root`` (incl. ignored, .git objects)."""
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            result[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
    return result
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_corpus.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/corpus.py tests/selfcheck/test_corpus.py
git commit -m "feat(selfcheck): корпус, read-only копия, уборка, чтение истории (§1.3–1.4)"
```

---

### Task 4: окружение статических проб (§1.5)

**Files:**
- Create: `selfcheck/env.py`
- Test: `tests/selfcheck/test_env.py`

**Interfaces:**
- Consumes: `Finding`, `Confidence`, `cap` (Task 1).
- Produces: `EnvInfo(mode: Literal["checkout-venv", "no-env"], stale: bool = False,
  site_packages: Path | None = None, python_version: str | None = None)`,
  `detect_env(repo: Path) -> EnvInfo`, `canonical_name(str) -> str`,
  `declared_requirements(pyproject: Path) -> list[str]`,
  `dist_modules(site_packages: Path) -> dict[str, tuple[str, ...]]`,
  `package_module_map(site_packages: Path, pyproject: Path) -> str`,
  `IMPORT_CLASS_RULES: frozenset[str]`,
  `apply_env_policy(findings, env) -> tuple[list[Finding], dict[str, int]]`.

- [ ] **Step 1: падающие тесты**

`tests/selfcheck/test_env.py`:

```python
from __future__ import annotations

import os
import time
from pathlib import Path

from selfcheck.env import (
    EnvInfo,
    apply_env_policy,
    detect_env,
    package_module_map,
)
from selfcheck.model import Confidence, Finding, Location


def fake_venv(repo: Path) -> Path:
    site = repo / ".venv" / "lib" / "python3.13" / "site-packages"
    dist = site / "PyYAML-6.0.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: PyYAML\n\nbody\n")
    (dist / "top_level.txt").write_text("_yaml\nyaml\n")
    rec = site / "tomli-2.0.dist-info"
    rec.mkdir()
    (rec / "METADATA").write_text("Name: tomli\n")
    (rec / "RECORD").write_text(
        "tomli/__init__.py,,\ntomli-2.0.dist-info/METADATA,,\n")
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /x\nversion_info = 3.13.7\n")
    return site


def test_detect_modes(tmp_path: Path) -> None:
    assert detect_env(tmp_path).mode == "no-env"
    site = fake_venv(tmp_path)
    env = detect_env(tmp_path)
    assert (env.mode, env.site_packages, env.python_version) == (
        "checkout-venv", site, "3.13")
    assert env.stale is False
    lock = tmp_path / "uv.lock"
    lock.write_text("")
    future = time.time() + 60
    os.utime(lock, (future, future))
    assert detect_env(tmp_path).stale is True


def test_map_uses_declaration_spelling(tmp_path: Path) -> None:
    site = fake_venv(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = ["pyyaml>=6.0.3"]\n'
        '[dependency-groups]\ndev = ["Tomli", {include-group = "x"}]\n')
    assert package_module_map(site, pyproject) == "pyyaml=_yaml|yaml,Tomli=tomli"


def imp(rule: str) -> Finding:
    return Finding(rule=rule, category="deps", severity="medium",
                   confidence=Confidence.LIKELY, owner_repo="r",
                   anchor="file:a.py", locations=[Location("a.py", 1)])


def test_no_env_suppresses_import_class() -> None:
    kept, counts = apply_env_policy(
        [imp("deptry/DEP001"), imp("pyrefly/missing-import"), imp("ruff/F401")],
        EnvInfo("no-env"))
    assert [f.rule for f in kept] == ["ruff/F401"]
    assert counts == {"deptry/DEP001": 1, "pyrefly/missing-import": 1}


def test_stale_caps_import_class() -> None:
    kept, counts = apply_env_policy(
        [imp("deptry/DEP001"), imp("ruff/F401")],
        EnvInfo("checkout-venv", stale=True))
    assert counts == {}
    assert kept[0].confidence is Confidence.CANDIDATE
    assert kept[1].confidence is Confidence.LIKELY
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.env'`.

- [ ] **Step 3: реализация**

`selfcheck/env.py`:

```python
"""Target environment as data only (spec §1.5)."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from selfcheck.model import Confidence, Finding, cap

IMPORT_CLASS_RULES = frozenset({"deptry/DEP001", "pyrefly/missing-import"})
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SKIP_TOPS = frozenset({"__pycache__", "bin"})


@dataclass(frozen=True)
class EnvInfo:
    """How pyrefly/deptry see the target's third-party packages."""

    mode: Literal["checkout-venv", "no-env"]
    stale: bool = False
    site_packages: Path | None = None
    python_version: str | None = None


def detect_env(repo: Path) -> EnvInfo:
    """Existing ``.venv`` of the source checkout, read as files only."""
    cfg = repo / ".venv" / "pyvenv.cfg"
    sites = sorted((repo / ".venv" / "lib").glob("python*/site-packages"))
    if not cfg.is_file() or not sites:
        return EnvInfo("no-env")
    version = None
    for line in cfg.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("version_info", "version"):
            version = ".".join(value.strip().split(".")[:2])
    lock = repo / "uv.lock"
    stale = lock.is_file() and lock.stat().st_mtime > cfg.stat().st_mtime
    return EnvInfo("checkout-venv", stale, sites[0], version)


def canonical_name(name: str) -> str:
    """PEP 503 normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_requirements(pyproject: Path) -> list[str]:
    """Requirement names exactly as written, in declaration order."""
    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    specs: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs += group
    for group in data.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    names: list[str] = []
    for spec in specs:
        match = _REQ_NAME.match(spec)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _metadata_name(dist: Path) -> str | None:
    meta = dist / "METADATA"
    if not meta.is_file():
        return None
    for line in meta.read_text(errors="ignore").splitlines():
        if not line.strip():
            return None
        if line.startswith("Name:"):
            return line.split(":", 1)[1].strip()
    return None


def _modules(dist: Path) -> tuple[str, ...]:
    top = dist / "top_level.txt"
    if top.is_file():
        mods = {m.strip() for m in top.read_text().splitlines() if m.strip()}
    else:
        mods = set()
        record = dist / "RECORD"
        lines = record.read_text().splitlines() if record.is_file() else []
        for line in lines:
            head = line.split(",", 1)[0].split("/", 1)[0].removesuffix(".py")
            if head.isidentifier() and head not in _SKIP_TOPS:
                mods.add(head)
    return tuple(sorted(mods))


def dist_modules(site_packages: Path) -> dict[str, tuple[str, ...]]:
    """canonical distribution name → top-level modules, from dist-info files."""
    result: dict[str, tuple[str, ...]] = {}
    for dist in sorted(site_packages.glob("*.dist-info")):
        name = _metadata_name(dist)
        mods = _modules(dist)
        if name and mods:
            result[canonical_name(name)] = mods
    return result


def package_module_map(site_packages: Path, pyproject: Path) -> str:
    """deptry ``--package-module-name-map`` keyed by declaration spelling."""
    available = dist_modules(site_packages)
    parts = []
    for req in declared_requirements(pyproject):
        mods = available.get(canonical_name(req))
        if mods:
            parts.append(f"{req}={'|'.join(mods)}")
    return ",".join(parts)


def apply_env_policy(findings: list[Finding],
                     env: EnvInfo) -> tuple[list[Finding], dict[str, int]]:
    """no-env: move import-class findings out; env-stale: cap them."""
    if env.mode == "no-env":
        counts = Counter(f.rule for f in findings if f.rule in IMPORT_CLASS_RULES)
        kept = [f for f in findings if f.rule not in IMPORT_CLASS_RULES]
        return kept, dict(counts)
    if env.stale:
        for item in findings:
            if item.rule in IMPORT_CLASS_RULES:
                item.confidence = cap(item.confidence, Confidence.CANDIDATE)
                item.evidence.append({"kind": "cap", "detail": "env-stale"})
    return findings, {}
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_env.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/env.py tests/selfcheck/test_env.py
git commit -m "feat(selfcheck): режимы окружения — только данные (§1.5)"
```

---
### Task 5: контракт пробы — статусы, канарейка, покрытие, формы входа (§4.1–4.2)

**Files:**
- Create: `selfcheck/probes/__init__.py` (пустой), `selfcheck/probes/base.py`, `selfcheck/probes/common.py`
- Test: `tests/selfcheck/test_probe_base.py`

**Interfaces:**
- Consumes: `Finding`, `Location`, `Confidence`, `make_text_key` (Task 1), `python_anchor` (Task 1),
  `Role`, `role_of` (Task 2), `EnvInfo` (Task 4), `materialize`, `list_corpus` (Task 3).
- Produces:
  - `Canary(relpath: str, content: str, expect_rule: str)`;
  - `RepoTarget(name: str, source: Path, copy: Path, languages: frozenset[str],
    corpus: tuple[str, ...], env: EnvInfo, roles: dict[str, tuple[str, ...]] = {},
    sched_dir: Path | None = None, fleet: str = "absent", now: float = 0.0)`;
  - `ProbeCtx(target: RepoTarget, work: Path, inputs: tuple[str, ...])`;
  - `ParseResult(findings, processed: int | None = None, skipped: list[str] = [],
    diagnostics: list[str] = [], extra: dict[str, Any] = {})`;
  - `ProbeStatus` (`ok`, `partial`, `failed`, `unavailable`, `skipped`);
  - `ProbeResult(probe, repo, status, reason="", tool_version=None, argv=[], exit_code=None,
    canary=None, coverage={}, diagnostics=[], findings=[], extra={}, duration=0.0,
    config_hash="")` с `to_json()`;
  - `ProbeSpec(name, languages, input_mode, select, canary, coverage="declared",
    executes_target_code=False, binary=None, version_args=("--version",),
    version_range=None, normal_codes=frozenset({0}), argv=None, parse=None,
    analyze=None, config_suppresses=_never, expected_files=None, config_files=(),
    timeout=900)`;
  - `canary_files(specs: Iterable[ProbeSpec]) -> dict[str, str]`;
  - `run_probe(spec, target, work_root, *, runner=subprocess.run, which=shutil.which) -> ProbeResult`;
  - `parse_version(text: str) -> tuple[int, ...] | None`;
  - `common.rel_path(ctx, raw: str) -> str`, `common.source_text(ctx, rel) -> str`,
    `common.line_finding(ctx, rule, rel, line, *, category, severity,
    confidence=Confidence.LIKELY, message="", key_text=None) -> Finding`,
    `common.copy_paths(ctx) -> list[str]`, `common.config_hash(copy, names) -> str`.

- [ ] **Step 1: падающие тесты (включая Review Focus 1 и 4)**

`tests/selfcheck/test_probe_base.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.model import Location
from selfcheck.probes.base import (
    Canary,
    ParseResult,
    ProbeCtx,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from tests.selfcheck.helpers import make_repo

CANARY = Canary(".selfcheck-canary/fake/c.py", "x = 1\n", "fake/CAN")


def fake_tool(tmp: Path, *, version: str = "1.2.3", code: int = 1,
              emit_repo: bool = True, emit_canary: bool = True,
              stdout: str | None = None, sleep: float = 0.0,
              write_copy: bool = False, skipped: list[str] | None = None) -> Path:
    script = tmp / "fake-tool"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, pathlib, sys, time
        args = sys.argv[1:]
        if args == ["--version"]:
            print("fake {version}")
            sys.exit(0)
        time.sleep({sleep})
        if {write_copy!r}:
            try:
                pathlib.Path(args[0]).parent.joinpath("new.py").write_text("")
            except PermissionError as exc:
                print(f"Permission denied: {{exc}}", file=sys.stderr)
                sys.exit(2)
        out = []
        for a in args:
            can = ".selfcheck-canary" in a
            if (can and {emit_canary!r}) or (not can and {emit_repo!r}):
                out.append({{"path": a, "line": 1, "code": "CAN" if can else "X"}})
        text = {stdout!r}
        print(text if text is not None else json.dumps(
            {{"items": out, "skipped": {skipped!r} or []}}))
        sys.exit({code})
        """))
    script.chmod(0o755)
    return script


def parse_fake(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    findings = [
        line_finding(ctx, f"fake/{i['code']}", rel_path(ctx, i["path"]), i["line"],
                     category="bug", severity="low")
        for i in data["items"]
    ]
    return ParseResult(findings, skipped=data["skipped"],
                       diagnostics=[f"skipped {s}" for s in data["skipped"]])


def spec_for(tool: Path, **overrides: object) -> ProbeSpec:
    fields: dict[str, object] = dict(
        name="fake", languages=frozenset({"python"}), input_mode="files",
        select=lambda t: tuple(p for p in t.corpus if p.endswith(".py")),
        canary=CANARY, binary=str(tool), version_range=((1, 0), (2, 0)),
        normal_codes=frozenset({0, 1}), argv=copy_paths, parse=parse_fake,
        timeout=10,
    )
    fields.update(overrides)
    return ProbeSpec(**fields)  # type: ignore[arg-type]


def which(binary: str) -> str | None:
    return binary if Path(binary).exists() else None


@pytest.fixture
def target(tmp_path: Path) -> RepoTarget:
    repo = make_repo(tmp_path / "repo", {"a.py": "x = 1\n", "run.sh": "echo\n"})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec_for(Path("t"))]))
    yield RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                     EnvInfo("no-env"))
    release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp_path: Path):
    return run_probe(spec, target, tmp_path / "run" / "work", which=which)


def test_findings_with_nonzero_normal_code_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, code=1)), target, tmp_path)
    assert (res.status, res.canary, res.exit_code) == (ProbeStatus.OK, "hit", 1)
    assert [f.rule for f in res.findings] == ["fake/X"]  # canary subtracted
    assert res.findings[0].locations == [Location("a.py", 1)]
    assert res.tool_version == "1.2.3"


def test_clean_nonempty_corpus_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, emit_repo=False)), target, tmp_path)
    assert res.status is ProbeStatus.OK and res.findings == []


def test_canary_missed_fails(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, emit_canary=False)), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


def test_canary_suppressed_by_config(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path, emit_canary=False),
                    config_suppresses=lambda ctx: True)
    res = run(spec, target, tmp_path)
    assert res.reason == "canary-suppressed-by-config"


def test_exit_code_outside_set_fails(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, code=7)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("exit-code")


def test_unparsable_output_fails(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, stdout="not json", code=0)),
              target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("unparsable")


def test_per_file_skip_is_partial(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, skipped=["a.py"])), target, tmp_path)
    assert res.status is ProbeStatus.PARTIAL
    assert res.coverage["skipped"] == ["a.py"]


def test_reported_zero_processed_fails(target, tmp_path) -> None:
    def parse_zero(ctx, proc):
        parsed = parse_fake(ctx, proc)
        parsed.processed = 0
        return parsed

    spec = spec_for(fake_tool(tmp_path), coverage="reported", parse=parse_zero)
    res = run(spec, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "zero-processed")


def test_missing_binary_unavailable(target, tmp_path) -> None:
    res = run(spec_for(tmp_path / "nope"), target, tmp_path)
    assert res.status is ProbeStatus.UNAVAILABLE


def test_version_out_of_range_unavailable(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, version="3.0.0")), target, tmp_path)
    assert res.status is ProbeStatus.UNAVAILABLE and "3.0.0" in res.reason


def test_no_inputs_skipped(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path), select=lambda t: ())
    res = run(spec, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.SKIPPED, "no-inputs")


def test_repo_without_python_skips_python_probes(target, tmp_path) -> None:
    shell_only = RepoTarget("repo", target.source, target.copy, frozenset(),
                            target.corpus, target.env)
    py = run(spec_for(fake_tool(tmp_path)), shell_only, tmp_path)
    anyp = run(spec_for(fake_tool(tmp_path), name="fake-any",
                        languages=frozenset({"any"})), shell_only, tmp_path)
    assert (py.status, py.reason) == (ProbeStatus.SKIPPED, "language")
    assert anyp.status is ProbeStatus.OK


def test_timeout_fails_only_that_probe(target, tmp_path) -> None:
    slow_dir = tmp_path / "slow"
    slow_dir.mkdir()
    slow = run(spec_for(fake_tool(slow_dir, sleep=5), timeout=1), target, tmp_path)
    fast = run(spec_for(fake_tool(tmp_path), name="fake2"), target, tmp_path)
    assert (slow.status, slow.reason) == (ProbeStatus.FAILED, "timeout")
    assert fast.status is ProbeStatus.OK


def test_write_to_corpus_is_visible_failure(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path, write_copy=True)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED
    assert res.reason.startswith("write-to-corpus")


def test_internal_analyzer_contract(target, tmp_path) -> None:
    def analyze(ctx: ProbeCtx) -> ParseResult:
        return ParseResult([line_finding(ctx, "fake/CAN", CANARY.relpath, 1,
                                         category="bug", severity="low")])

    ok = run_probe(ProbeSpec(name="int", languages=frozenset({"any"}),
                             input_mode="files", select=lambda t: t.corpus,
                             canary=CANARY, analyze=analyze),
                   target, tmp_path / "w")

    def boom(ctx: ProbeCtx) -> ParseResult:
        raise RuntimeError("x")

    bad = run_probe(ProbeSpec(name="int2", languages=frozenset({"any"}),
                              input_mode="files", select=lambda t: t.corpus,
                              canary=CANARY, analyze=boom), target, tmp_path / "w")
    assert ok.status is ProbeStatus.OK
    assert (bad.status, bad.reason) == (ProbeStatus.FAILED, "analyzer-error: RuntimeError('x')")
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_probe_base.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.probes'`.

- [ ] **Step 3: реализация `base.py`**

`selfcheck/probes/base.py`:

```python
"""Probe contract: statuses, canary, coverage, input forms (spec §4.1–4.2)."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from selfcheck.env import EnvInfo
from selfcheck.model import Finding
from selfcheck.probes.common import config_hash
from selfcheck.roles import Role, role_of


@dataclass(frozen=True)
class Canary:
    """A file with one known finding, fed to the probe with the repo."""

    relpath: str
    content: str
    expect_rule: str


@dataclass(frozen=True)
class RepoTarget:
    """One repo as the probes see it: the read-only copy plus metadata."""

    name: str
    source: Path
    copy: Path
    languages: frozenset[str]
    corpus: tuple[str, ...]
    env: EnvInfo
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sched_dir: Path | None = None
    fleet: str = "absent"
    now: float = 0.0


@dataclass(frozen=True)
class ProbeCtx:
    """What one probe invocation works with."""

    target: RepoTarget
    work: Path
    inputs: tuple[str, ...]


@dataclass
class ParseResult:
    """Normalised output of a tool or analyzer."""

    findings: list[Finding]
    processed: int | None = None
    skipped: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class ProbeStatus(StrEnum):
    """Probe outcome (spec §4.2)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


@dataclass
class ProbeResult:
    """One row of the probe summary table."""

    probe: str
    repo: str
    status: ProbeStatus
    reason: str = ""
    tool_version: str | None = None
    argv: list[str] = field(default_factory=list)
    exit_code: int | None = None
    canary: str | None = None
    coverage: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    config_hash: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialise without findings (they are reported separately)."""
        return {
            "probe": self.probe, "repo": self.repo, "status": self.status.value,
            "reason": self.reason, "tool_version": self.tool_version,
            "argv": self.argv, "exit_code": self.exit_code, "canary": self.canary,
            "coverage": self.coverage, "diagnostics": self.diagnostics,
            "findings": len(self.findings), "duration": self.duration,
            "config_hash": self.config_hash,
        }


def _never(ctx: ProbeCtx) -> bool:
    return False


@dataclass(frozen=True)
class ProbeSpec:
    """Registry entry for one probe (spec §4.1)."""

    name: str
    languages: frozenset[str]
    input_mode: Literal["files", "roots"]
    select: Callable[[RepoTarget], tuple[str, ...]]
    canary: Canary
    coverage: Literal["reported", "declared"] = "declared"
    executes_target_code: bool = False
    binary: str | None = None
    version_args: tuple[str, ...] = ("--version",)
    version_range: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    normal_codes: frozenset[int] = frozenset({0})
    argv: Callable[[ProbeCtx], list[str]] | None = None
    parse: Callable[[ProbeCtx, subprocess.CompletedProcess[str]], ParseResult] | None = None
    analyze: Callable[[ProbeCtx], ParseResult] | None = None
    config_suppresses: Callable[[ProbeCtx], bool] = _never
    expected_files: Callable[[RepoTarget], int] | None = None
    config_files: tuple[str, ...] = ()
    timeout: int = 900


def canary_files(specs: Iterable[ProbeSpec]) -> dict[str, str]:
    """relpath → content of every canary, for materialisation."""
    return {s.canary.relpath: s.canary.content for s in specs}


def parse_version(text: str) -> tuple[int, ...] | None:
    """First dotted version number in ``text``."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if match is None:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


class _Stop(Exception):
    def __init__(self, status: ProbeStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _external(spec: ProbeSpec, ctx: ProbeCtx, result: ProbeResult,
              runner: Runner, which: Callable[[str], str | None]) -> ParseResult:
    assert spec.binary and spec.argv and spec.parse and spec.version_range
    binary = which(spec.binary)
    if binary is None:
        raise _Stop(ProbeStatus.UNAVAILABLE, f"{spec.binary} not found")
    ver = runner([binary, *spec.version_args], capture_output=True, text=True,
                 timeout=120, cwd=ctx.work)
    version = parse_version(ver.stdout + ver.stderr)
    low, high = spec.version_range
    if version is None or not low <= version < high:
        shown = ".".join(map(str, version)) if version else "unknown"
        raise _Stop(ProbeStatus.UNAVAILABLE, f"version {shown} outside {low}..{high}")
    result.tool_version = ".".join(map(str, version))
    result.argv = [binary, *spec.argv(ctx)]
    try:
        proc = runner(result.argv, cwd=ctx.work, capture_output=True, text=True,
                      timeout=spec.timeout)
    except subprocess.TimeoutExpired as exc:
        raise _Stop(ProbeStatus.FAILED, "timeout") from exc
    result.exit_code = proc.returncode
    if proc.returncode not in spec.normal_codes:
        wrote = "Permission denied" in proc.stderr or "EACCES" in proc.stderr
        kind = "write-to-corpus" if wrote else "exit-code"
        raise _Stop(ProbeStatus.FAILED,
                    f"{kind}: {proc.returncode}: {proc.stderr.strip()[-300:]}")
    try:
        return spec.parse(ctx, proc)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise _Stop(ProbeStatus.FAILED, f"unparsable: {exc}") from exc


def _internal(spec: ProbeSpec, ctx: ProbeCtx) -> ParseResult:
    assert spec.analyze is not None
    try:
        return spec.analyze(ctx)
    except Exception as exc:  # noqa: BLE001 — any analyzer bug is a probe failure
        raise _Stop(ProbeStatus.FAILED, f"analyzer-error: {exc!r}") from exc


def _is_canary(finding: Finding) -> bool:
    return any(role_of(loc.path) is Role.CANARY for loc in finding.locations)


def _judge(spec: ProbeSpec, ctx: ProbeCtx, result: ProbeResult,
           parsed: ParseResult) -> None:
    result.diagnostics = parsed.diagnostics
    result.extra = parsed.extra
    result.coverage["processed"] = parsed.processed
    canary = [f for f in parsed.findings if _is_canary(f)]
    result.findings = [f for f in parsed.findings if not _is_canary(f)]
    result.canary = ("hit" if any(f.rule == spec.canary.expect_rule for f in canary)
                     else "missed")
    if result.canary == "missed":
        reason = ("canary-suppressed-by-config" if spec.config_suppresses(ctx)
                  else "canary-missed")
        raise _Stop(ProbeStatus.FAILED, reason)
    if spec.coverage == "reported" and parsed.processed == 0:
        raise _Stop(ProbeStatus.FAILED, "zero-processed")
    if parsed.skipped or parsed.diagnostics:
        result.coverage["skipped"] = parsed.skipped
        raise _Stop(ProbeStatus.PARTIAL, "per-file problems")
    result.status = ProbeStatus.OK


def run_probe(spec: ProbeSpec, target: RepoTarget, work_root: Path, *,
              runner: Runner = subprocess.run,
              which: Callable[[str], str | None] = shutil.which) -> ProbeResult:
    """Run one probe on one repo and classify the outcome (spec §4.2)."""
    started = time.monotonic()
    result = ProbeResult(probe=spec.name, repo=target.name, status=ProbeStatus.SKIPPED)
    if "any" not in spec.languages and not spec.languages & target.languages:
        result.reason = "language"
        return result
    inputs = spec.select(target)
    if not inputs:
        result.reason = "no-inputs"
        return result
    work = work_root / spec.name / target.name
    work.mkdir(parents=True)
    canary = (spec.canary.relpath,) if spec.input_mode == "files" else ()
    ctx = ProbeCtx(target, work, (*inputs, *canary))
    result.coverage = {"mode": spec.coverage, "input_mode": spec.input_mode,
                       "passed": len(inputs)}
    if spec.expected_files is not None:
        result.coverage["expected_files"] = spec.expected_files(target)
    result.config_hash = config_hash(target.copy, spec.config_files)
    try:
        if spec.analyze is not None:
            parsed = _internal(spec, ctx)
        else:
            parsed = _external(spec, ctx, result, runner, which)
        _judge(spec, ctx, result, parsed)
    except _Stop as stop:
        result.status, result.reason = stop.status, stop.reason
    result.duration = round(time.monotonic() - started, 3)
    return result
```

`selfcheck/probes/common.py`:

```python
"""Helpers shared by probe adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from selfcheck.anchors import python_anchor
from selfcheck.model import Confidence, Finding, Location, make_text_key

if TYPE_CHECKING:
    from selfcheck.probes.base import ProbeCtx


def rel_path(ctx: ProbeCtx, raw: str) -> str:
    """Tool-reported path (absolute or relative to cwd) → path in the copy."""
    path = Path(raw)
    if not path.is_absolute():
        path = ctx.work / path
    return path.resolve().relative_to(ctx.target.copy.resolve()).as_posix()


def source_text(ctx: ProbeCtx, rel: str) -> str:
    """Text of a file in the copy ('' when unreadable)."""
    try:
        return (ctx.target.copy / rel).read_text(errors="replace")
    except OSError:
        return ""


def copy_paths(ctx: ProbeCtx) -> list[str]:
    """Inputs as absolute paths inside the copy (files input form)."""
    return [str(ctx.target.copy / p) for p in ctx.inputs]


def line_finding(ctx: ProbeCtx, rule: str, rel: str, line: int, *,
                 category: str, severity: str,
                 confidence: Confidence = Confidence.LIKELY,
                 message: str = "", key_text: str | None = None) -> Finding:
    """Finding at ``rel:line`` with anchor and text_key (spec §2.1)."""
    text = source_text(ctx, rel)
    lines = text.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    anchor = python_anchor(text, rel, line) if rel.endswith(".py") else f"file:{rel}"
    evidence = [{"kind": "message", "detail": message}] if message else []
    return Finding(
        rule=rule, category=category, severity=severity, confidence=confidence,
        owner_repo=ctx.target.name, anchor=anchor,
        locations=[Location(rel, line)],
        text_key=make_text_key(key_text if key_text is not None else line_text),
        group=anchor, evidence=evidence,
    )


def config_hash(copy: Path, names: tuple[str, ...]) -> str:
    """sha1 over the probe's config files present in the copy."""
    digest = hashlib.sha1()
    for name in names:
        path = copy / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_probe_base.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/probes tests/selfcheck/test_probe_base.py
git commit -m "feat(selfcheck): контракт пробы — статусы, канарейка, покрытие (§4.1–4.2)"
```

---

### Task 6: Python-пробы — ruff, pyrefly, vulture, radon, deptry

**Files:**
- Create: `selfcheck/probes/python_tools.py`
- Test: `tests/selfcheck/test_python_tools.py`

**Interfaces:**
- Consumes: всё из Task 5; `package_module_map`, `EnvInfo` (Task 4); `role_of`, `Role` (Task 2).
- Produces: `RUFF`, `PYREFLY`, `VULTURE`, `RADON`, `DEPTRY: ProbeSpec`;
  `PYTHON_PROBES: tuple[ProbeSpec, ...]`; `python_files(target) -> tuple[str, ...]`.

- [ ] **Step 1: падающие тесты (реальные инструменты)**

`tests/selfcheck/test_python_tools.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import detect_env
from selfcheck.probes.base import ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.probes.python_tools import (
    DEPTRY,
    PYREFLY,
    PYTHON_PROBES,
    RADON,
    RUFF,
    VULTURE,
)
from tests.selfcheck.helpers import make_repo, require_tool

PYPROJECT = '[project]\nname = "x"\nversion = "0"\ndependencies = ["pyyaml>=6"]\n'


def target_for(tmp: Path, files: dict[str, str]) -> RepoTarget:
    repo = make_repo(tmp / "repo", {"pyproject.toml": PYPROJECT, **files})
    return materialized(tmp, repo)


def materialized(tmp: Path, repo: Path) -> RepoTarget:
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files(PYTHON_PROBES))
    return RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                      detect_env(repo))


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path):
    require_tool(spec.binary or "")
    return run_probe(spec, target, tmp / "run" / "work")


@pytest.fixture(autouse=True)
def _cleanup(tmp_path: Path):
    yield
    copy = tmp_path / "run" / "src" / "repo"
    if copy.exists():
        release(copy)


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(spec: ProbeSpec, tmp_path: Path) -> None:
    target = target_for(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": (
        "import yaml\n\n\ndef load(text: str) -> object:\n"
        "    return yaml.safe_load(text)\n")})
    res = run(spec, target, tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit"


def test_ruff_reports_repo_violation(tmp_path: Path) -> None:
    res = run(RUFF, target_for(tmp_path, {"a.py": "import os\n"}), tmp_path)
    assert res.status is ProbeStatus.OK
    assert [(f.rule, f.anchor) for f in res.findings] == [("ruff/F401", "file:a.py")]


def test_ruff_cli_restores_ignored_canary(tmp_path: Path) -> None:
    target = target_for(tmp_path, {"ruff.toml": '[lint]\nignore = ["F401"]\n',
                                   "a.py": "x = 1\n"})
    assert run(RUFF, target, tmp_path).status is ProbeStatus.OK


def test_ruff_per_file_ignore_suppresses_canary(tmp_path: Path) -> None:
    target = target_for(tmp_path, {"ruff.toml": (
        '[lint.per-file-ignores]\n".selfcheck-canary/**" = ["F401"]\n'),
        "a.py": "x = 1\n"})
    res = run(RUFF, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED,
                                        "canary-suppressed-by-config")


def test_vulture_syntax_error_is_partial(tmp_path: Path) -> None:
    res = run(VULTURE, target_for(tmp_path, {"bad.py": "def f(:\n"}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL and "bad.py" in res.coverage["skipped"]


def test_pyrefly_default_preset_catches_bad_return(tmp_path: Path) -> None:
    target = target_for(tmp_path, {"a.py": 'def f() -> int:\n    return "x"\n'})
    res = run(PYREFLY, target, tmp_path)
    assert "pyrefly/bad-return" in {f.rule for f in res.findings}


def test_radon_reports_high_complexity(tmp_path: Path) -> None:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(22))
    target = target_for(tmp_path, {"c.py": f"def big(x: int) -> int:\n{body}    return -1\n"})
    res = run(RADON, target, tmp_path)
    assert [f.anchor for f in res.findings] == ["func:c.py::big"]


def test_deptry_ignores_dep003_and_keys_by_declaration(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", {"pyproject.toml": PYPROJECT,
                                         ".gitignore": ".venv/\n",
                                         "a.py": "import yaml\nimport tomli\n"})
    marker = tmp_path / "EXECUTED"
    site = repo / ".venv" / "lib" / "python3.12" / "site-packages"
    dist = site / "PyYAML-6.0.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: PyYAML\n")
    (dist / "top_level.txt").write_text("_yaml\nyaml\n")
    (site / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w')\n")
    (site / "evil.pth").write_text(f"import os; open({str(marker)!r}, 'w')\n")
    (repo / ".venv" / "pyvenv.cfg").write_text("version_info = 3.12.1\n")
    target = materialized(tmp_path, repo)
    deptry = run(DEPTRY, target, tmp_path)
    pyrefly = run(PYREFLY, target, tmp_path)
    assert not marker.exists()
    assert deptry.status is ProbeStatus.OK and pyrefly.status is ProbeStatus.OK
    rules = {(f.rule, f.text_key) for f in deptry.findings}
    assert not any(r == "deptry/DEP003" for r, _ in rules)
    modules = [e["detail"] for f in deptry.findings for e in f.evidence]
    assert not any("'yaml'" in m for m in modules)
    assert deptry.coverage["expected_files"] >= 1
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_python_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.probes.python_tools'`.

- [ ] **Step 3: реализация**

`selfcheck/probes/python_tools.py`:

```python
"""Python static probes (spec §3.1, §1.5): none executes target code."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from fnmatch import fnmatch

from selfcheck.env import package_module_map
from selfcheck.model import Confidence
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from selfcheck.roles import Role, role_of

PY = frozenset({"python"})
RUFF_SELECT = "F,B,PL,SIM,ERA,C90,ARG,RET"
_BUG_PREFIXES = ("F", "B", "PLE")
_DEPTRY_DEFAULT_EXCLUDE = re.compile(
    r"(^|/)(venv|\.venv|\.direnv|tests|\.git)(/|$)|(^|/)setup\.py$")


def python_files(target: RepoTarget) -> tuple[str, ...]:
    """Corpus .py files except canaries (canaries are appended by the core)."""
    return tuple(p for p in target.corpus if p.endswith(".py")
                 and role_of(p, target.roles) is not Role.CANARY)


# ---- ruff -------------------------------------------------------------------

def _ruff_argv(ctx: ProbeCtx) -> list[str]:
    return ["check", "--no-cache", "--output-format", "json",
            "--extend-select", RUFF_SELECT, *copy_paths(ctx)]


def _ruff_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout or "[]"):
        rel = rel_path(ctx, item["filename"])
        code = item.get("code")
        if code is None:
            result.diagnostics.append(f"{rel}: {item.get('message', '')}")
            result.skipped.append(rel)
            continue
        bug = code.startswith(_BUG_PREFIXES)
        result.findings.append(line_finding(
            ctx, f"ruff/{code}", rel, item["location"]["row"],
            category="bug" if bug else "quality",
            severity="medium" if bug else "low", message=item["message"]))
    return result


def _ruff_suppresses(ctx: ProbeCtx) -> bool:
    for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        path = ctx.target.copy / name
        if not path.is_file():
            continue
        data = tomllib.loads(path.read_text())
        root = data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
        ignores = {**root.get("per-file-ignores", {}),
                   **root.get("lint", {}).get("per-file-ignores", {})}
        for pattern, codes in ignores.items():
            if fnmatch(RUFF.canary.relpath, pattern) and any(
                    c == "ALL" or "F401".startswith(c) for c in codes):
                return True
    return False


RUFF = ProbeSpec(
    name="ruff", languages=PY, input_mode="files", select=python_files,
    canary=Canary(".selfcheck-canary/ruff/canary.py", "import os\n", "ruff/F401"),
    binary="ruff", version_range=((0, 16), (0, 17)), normal_codes=frozenset({0, 1}),
    argv=_ruff_argv, parse=_ruff_parse, config_suppresses=_ruff_suppresses,
    config_files=("pyproject.toml", "ruff.toml", ".ruff.toml"),
)


# ---- pyrefly ----------------------------------------------------------------

def _pyrefly_configured(ctx: ProbeCtx) -> bool:
    pyproject = ctx.target.copy / "pyproject.toml"
    in_pyproject = pyproject.is_file() and "[tool.pyrefly" in pyproject.read_text()
    return (ctx.target.copy / "pyrefly.toml").is_file() or in_pyproject


def _pyrefly_argv(ctx: ProbeCtx) -> list[str]:
    args = ["check", "--output-format", "json", "--summary=none",
            "--skip-interpreter-query", "--error", "bad-return",
            "--python-platform", "darwin" if sys.platform == "darwin" else "linux"]
    if not _pyrefly_configured(ctx):
        args += ["--preset", "default"]
    env = ctx.target.env
    if env.mode == "checkout-venv" and env.site_packages is not None:
        args += ["--site-package-path", str(env.site_packages)]
        if env.python_version:
            args += ["--python-version", env.python_version]
    return args + copy_paths(ctx)


def _pyrefly_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for err in json.loads(proc.stdout)["errors"]:
        rel = rel_path(ctx, err["path"])
        if err["name"] == "parse-error":
            result.diagnostics.append(f"{rel}: {err['concise_description']}")
            result.skipped.append(rel)
            continue
        is_error = err.get("severity", "error") == "error"
        result.findings.append(line_finding(
            ctx, f"pyrefly/{err['name']}", rel, err["line"],
            category="bug" if is_error else "quality",
            severity="medium" if is_error else "low",
            message=err["concise_description"]))
    return result


def _pyrefly_suppresses(ctx: ProbeCtx) -> bool:
    texts = [ctx.target.copy / n for n in ("pyrefly.toml", "pyproject.toml")]
    return any(p.is_file() and "bad-return" in p.read_text() for p in texts)


PYREFLY = ProbeSpec(
    name="pyrefly", languages=PY, input_mode="files", select=python_files,
    canary=Canary(".selfcheck-canary/pyrefly/canary.py",
                  'def selfcheck_canary() -> int:\n    return "x"\n',
                  "pyrefly/bad-return"),
    binary="pyrefly", version_range=((1, 3), (2, 0)), normal_codes=frozenset({0, 1}),
    argv=_pyrefly_argv, parse=_pyrefly_parse, config_suppresses=_pyrefly_suppresses,
    config_files=("pyproject.toml", "pyrefly.toml"),
)


# ---- vulture ----------------------------------------------------------------

_VULTURE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<msg>.+?) "
    r"\((?P<conf>\d+)% confidence(?:, \d+ lines?)?\)$")


def _vulture_argv(ctx: ProbeCtx) -> list[str]:
    return ["--min-confidence", "60", *copy_paths(ctx)]


def _vulture_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    if "Error:" in proc.stdout or "Error:" in proc.stderr:
        raise ValueError((proc.stdout + proc.stderr).strip()[-300:])
    result = ParseResult([])
    for line in proc.stdout.splitlines():
        match = _VULTURE.match(line)
        if match:
            kind = match["msg"].split(" '", 1)[0].replace(" ", "-")
            conf = int(match["conf"])
            result.findings.append(line_finding(
                ctx, f"vulture/{kind}", rel_path(ctx, match["path"]),
                int(match["line"]), category="dead", severity="low",
                confidence=Confidence.LIKELY if conf == 100 else Confidence.CANDIDATE,
                message=match["msg"]))
        elif "invalid syntax" in line:
            rel = rel_path(ctx, line.split(":", 1)[0])
            result.diagnostics.append(line)
            result.skipped.append(rel)
        elif line.strip():
            raise ValueError(f"unexpected vulture output: {line}")
    return result


VULTURE = ProbeSpec(
    name="vulture", languages=PY, input_mode="files", select=python_files,
    canary=Canary(".selfcheck-canary/vulture/canary.py",
                  "def selfcheck_canary_unused() -> int:\n    return 1\n",
                  "vulture/unused-function"),
    binary="vulture", version_range=((2, 16), (3, 0)),
    normal_codes=frozenset({0, 1, 3}), argv=_vulture_argv, parse=_vulture_parse,
)


# ---- radon ------------------------------------------------------------------

_RADON_CANARY = "def selfcheck_canary(x: int) -> int:\n" + "".join(
    f"    if x == {i}:\n        return {i}\n" for i in range(22)) + "    return -1\n"


def _radon_argv(ctx: ProbeCtx) -> list[str]:
    return ["cc", "-j", "--min", "D", *copy_paths(ctx)]


def _radon_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    result = ParseResult([], processed=len(data))
    for raw, blocks in data.items():
        rel = rel_path(ctx, raw)
        if isinstance(blocks, dict):
            result.diagnostics.append(f"{rel}: {blocks.get('error')}")
            result.skipped.append(rel)
            continue
        for block in blocks:
            result.findings.append(line_finding(
                ctx, f"radon/cc-{block['rank']}", rel, block["lineno"],
                category="quality", severity="low",
                message=f"{block['name']}: complexity {block['complexity']}"))
    return result


RADON = ProbeSpec(
    name="radon", languages=PY, input_mode="files", select=python_files,
    canary=Canary(".selfcheck-canary/radon/canary.py", _RADON_CANARY, "radon/cc-D"),
    coverage="reported", binary="radon", version_range=((6, 0), (7, 0)),
    argv=_radon_argv, parse=_radon_parse,
)


# ---- deptry (roots) -----------------------------------------------------------

def _deptry_select(target: RepoTarget) -> tuple[str, ...]:
    return (".",) if "pyproject.toml" in target.corpus else ()


def _deptry_expected(target: RepoTarget) -> int:
    return sum(1 for p in python_files(target) if not _DEPTRY_DEFAULT_EXCLUDE.search(p))


def _deptry_argv(ctx: ProbeCtx) -> list[str]:
    copy = ctx.target.copy
    args = [str(copy), "--config", str(copy / "pyproject.toml"),
            "--json-output", str(ctx.work / "deptry.json"), "--ignore", "DEP003"]
    env = ctx.target.env
    if env.mode == "checkout-venv" and env.site_packages is not None:
        mapping = package_module_map(env.site_packages, copy / "pyproject.toml")
        if mapping:
            args += ["--package-module-name-map", mapping]
    return args


def _deptry_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for item in json.loads((ctx.work / "deptry.json").read_text()):
        code = item["error"]["code"]
        rel = rel_path(ctx, item["location"]["file"])
        result.findings.append(line_finding(
            ctx, f"deptry/{code}", rel, item["location"].get("line") or 1,
            category="deps", severity="medium", message=item["error"]["message"],
            key_text=f"{code}:{item.get('module')}"))
    return result


DEPTRY = ProbeSpec(
    name="deptry", languages=PY, input_mode="roots", select=_deptry_select,
    canary=Canary("selfcheck_canary/__init__.py",
                  "import selfcheck_canary_missing_dist\n", "deptry/DEP001"),
    binary="deptry", version_range=((0, 25), (0, 26)), normal_codes=frozenset({0, 1}),
    argv=_deptry_argv, parse=_deptry_parse, expected_files=_deptry_expected,
    config_files=("pyproject.toml",),
)

PYTHON_PROBES = (RUFF, PYREFLY, VULTURE, RADON, DEPTRY)
```

- [ ] **Step 4: тесты проходят**

Run: `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck/test_python_tools.py -q`
Expected: PASS. Если `test_clean_repo_ok_on_read_only_copy[deptry]` падает на
`DEP002 'pyyaml'` — это находка, не статус: проверьте, что assert смотрит
только на `status` и `canary` (так и написано). Если какой-то инструмент
пишет в копию, тест упадёт со `write-to-corpus` — вынести его кэш флагом в
argv (спека §1.3), не ослабляя тест.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/probes/python_tools.py tests/selfcheck/test_python_tools.py
git commit -m "feat(selfcheck): Python-пробы ruff/pyrefly/vulture/radon/deptry с канарейками"
```

---

### Task 7: прочие статические пробы — shellcheck, actionlint, zizmor, jscpd

**Files:**
- Create: `selfcheck/probes/other_tools.py`
- Test: `tests/selfcheck/test_other_tools.py`

**Interfaces:**
- Consumes: Task 5; `role_of`, `Role` (Task 2).
- Produces: `SHELLCHECK`, `ACTIONLINT`, `ZIZMOR`, `JSCPD: ProbeSpec`,
  `OTHER_PROBES: tuple[ProbeSpec, ...]`, `shell_files(target) -> tuple[str, ...]`,
  `workflow_files(target) -> tuple[str, ...]`, `JSCPD_VERSION = "4.3.0"`.

- [ ] **Step 1: падающие тесты**

`tests/selfcheck/test_other_tools.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.probes.base import ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.probes.other_tools import (
    ACTIONLINT,
    JSCPD,
    OTHER_PROBES,
    SHELLCHECK,
    ZIZMOR,
    shell_files,
)
from tests.selfcheck.helpers import make_repo, require_tool

WORKFLOW = """on: push
permissions: {}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
FUNC = "".join(f"    v{i} = a + {i}\n" for i in range(10))
DUP = f"def one(a):\n{FUNC}    return a\n\n\ndef two(a):\n{FUNC}    return a\n"


def target_for(tmp: Path, files: dict[str, str]) -> RepoTarget:
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files(OTHER_PROBES))
    return RepoTarget("repo", repo, copy, frozenset(), corpus, EnvInfo("no-env"))


@pytest.fixture(autouse=True)
def _cleanup(tmp_path: Path):
    yield
    copy = tmp_path / "run" / "src" / "repo"
    if copy.exists():
        release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path):
    require_tool(spec.binary or "")
    return run_probe(spec, target, tmp / "run" / "work")


CLEAN = {
    "run.sh": '#!/bin/sh\necho "$1"\n',
    "tool": '#!/usr/bin/env bash\nset -eu\necho "ok"\n',
    ".github/workflows/ci.yml": WORKFLOW,
    "m.py": "def f(a):\n    return a\n",
}


@pytest.mark.parametrize("spec", OTHER_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(spec: ProbeSpec, tmp_path: Path) -> None:
    res = run(spec, target_for(tmp_path, CLEAN), tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit" and res.findings == []


def test_shell_selection_by_shebang(tmp_path: Path) -> None:
    target = target_for(tmp_path, {**CLEAN, "script.py": "#!/usr/bin/env python3\n"})
    assert set(shell_files(target)) == {"run.sh", "tool"}


def test_shellcheck_finds_unquoted(tmp_path: Path) -> None:
    res = run(SHELLCHECK, target_for(tmp_path, {"x.sh": "#!/bin/sh\necho $1\n"}),
              tmp_path)
    assert [f.rule for f in res.findings] == ["shellcheck/SC2086"]


def test_actionlint_and_zizmor_find_injection(tmp_path: Path) -> None:
    bad = WORKFLOW.replace("echo ok", "echo ${{ github.event.head_commit.message }}")
    target = target_for(tmp_path, {".github/workflows/ci.yml": bad})
    assert {f.rule for f in run(ACTIONLINT, target, tmp_path).findings} == {
        "actionlint/expression"}
    assert "zizmor/template-injection" in {
        f.rule for f in run(ZIZMOR, target, tmp_path).findings}


def test_jscpd_clone_is_dup_text(tmp_path: Path) -> None:
    res = run(JSCPD, target_for(tmp_path, {"d.py": DUP}), tmp_path)
    assert res.status is ProbeStatus.OK
    assert res.coverage["processed"] >= 1
    assert [f.anchor.split(":")[1] for f in res.findings] == ["text"]
    assert res.findings[0].occurrences == 2
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_other_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.probes.other_tools'`.

- [ ] **Step 3: реализация**

`selfcheck/probes/other_tools.py`:

```python
"""Shell, workflow and text-clone probes (spec §3.1); all static."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

from selfcheck.model import Confidence, Finding, Location, normalize_line
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from selfcheck.roles import Role, role_of

ANY = frozenset({"any"})
JSCPD_VERSION = "4.3.0"
_SHEBANG = re.compile(rb"^#!.*\b(sh|bash|dash|ksh)\b")
_CLONE_SUFFIXES = (".py", ".sh", ".bash", ".js", ".ts", ".yml", ".yaml", ".toml")


def _is_shell(target: RepoTarget, rel: str) -> bool:
    if rel.endswith((".sh", ".bash")):
        return True
    if "." in rel.rsplit("/", 1)[-1]:
        return False
    try:
        with (target.copy / rel).open("rb") as handle:
            first = handle.readline(200)
    except OSError:
        return False
    return bool(_SHEBANG.match(first))


def shell_files(target: RepoTarget) -> tuple[str, ...]:
    """Shell scripts by suffix or by shebang (extensionless files)."""
    return tuple(p for p in target.corpus
                 if role_of(p, target.roles) is not Role.CANARY and _is_shell(target, p))


def workflow_files(target: RepoTarget) -> tuple[str, ...]:
    """GitHub workflow files."""
    return tuple(p for p in target.corpus if p.startswith(".github/workflows/")
                 and p.endswith((".yml", ".yaml")))


# ---- shellcheck ---------------------------------------------------------------

def _shellcheck_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout or '{"comments": []}')["comments"]:
        serious = item["level"] in ("error", "warning")
        result.findings.append(line_finding(
            ctx, f"shellcheck/SC{item['code']}", rel_path(ctx, item["file"]),
            item["line"], category="bug" if serious else "quality",
            severity="medium" if serious else "low", message=item["message"]))
    return result


def _shellcheck_suppresses(ctx: ProbeCtx) -> bool:
    rc = ctx.target.copy / ".shellcheckrc"
    return rc.is_file() and "2086" in rc.read_text()


SHELLCHECK = ProbeSpec(
    name="shellcheck", languages=ANY, input_mode="files", select=shell_files,
    canary=Canary(".selfcheck-canary/shellcheck/canary.sh",
                  "#!/bin/sh\necho $1\n", "shellcheck/SC2086"),
    binary="shellcheck", version_args=("-V",), version_range=((0, 11), (0, 12)),
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: ["-f", "json1", *copy_paths(ctx)],
    parse=_shellcheck_parse, config_suppresses=_shellcheck_suppresses,
    config_files=(".shellcheckrc",),
)

# ---- actionlint / zizmor --------------------------------------------------------

_INJECTION = """on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ github.event.head_commit.message }}
"""


def _actionlint_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    items = json.loads(proc.stdout.strip() or "[]") or []
    return ParseResult([
        line_finding(ctx, f"actionlint/{i['kind']}", rel_path(ctx, i["filepath"]),
                     i["line"], category="bug", severity="medium",
                     message=i["message"])
        for i in items
    ])


ACTIONLINT = ProbeSpec(
    name="actionlint", languages=ANY, input_mode="files", select=workflow_files,
    canary=Canary(".selfcheck-canary/actionlint/.github/workflows/canary.yml",
                  _INJECTION, "actionlint/expression"),
    binary="actionlint", version_args=("-version",), version_range=((1, 7), (2, 0)),
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: ["-format", "{{json .}}", "-no-color", *copy_paths(ctx)],
    parse=_actionlint_parse,
)

_ZIZMOR_SEVERITY = {"High": "high", "Medium": "medium"}


def _zizmor_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    for item in json.loads(proc.stdout or "[]"):
        loc = next(x for x in item["locations"] if x["symbolic"]["kind"] == "Primary")
        local = loc["symbolic"]["key"]["Local"]
        raw = local.get("given_path") or local["verbatim_path"]
        line = loc["concrete"]["location"]["start_point"]["row"] + 1
        sev = _ZIZMOR_SEVERITY.get(item["determinations"]["severity"], "low")
        result.findings.append(line_finding(
            ctx, f"zizmor/{item['ident']}", rel_path(ctx, raw), line,
            category="bug", severity=sev, message=item["desc"]))
    return result


ZIZMOR = ProbeSpec(
    name="zizmor", languages=ANY, input_mode="files", select=workflow_files,
    canary=Canary(".selfcheck-canary/zizmor/.github/workflows/canary.yml",
                  _INJECTION, "zizmor/template-injection"),
    binary="zizmor", version_range=((1, 30), (2, 0)),
    normal_codes=frozenset({0, 11, 12, 13, 14}),
    argv=lambda ctx: ["--offline", "--format", "json", *copy_paths(ctx)],
    parse=_zizmor_parse,
)

# ---- jscpd --------------------------------------------------------------------

_CLONE_BODY = "".join(f"    v{i} = a * {i} + 1\n" for i in range(10))
_JSCPD_CANARY = (f"def selfcheck_one(a):\n{_CLONE_BODY}    return a\n\n\n"
                 f"def selfcheck_two(a):\n{_CLONE_BODY}    return a\n")


def _clone_files(target: RepoTarget) -> tuple[str, ...]:
    keep = (Role.SOURCE, Role.TEST, Role.SKILL_ROOT)
    return tuple(p for p in target.corpus if p.endswith(_CLONE_SUFFIXES)
                 and role_of(p, target.roles) in keep)


def _jscpd_argv(ctx: ProbeCtx) -> list[str]:
    return ["--yes", f"jscpd@{JSCPD_VERSION}", "--silent", "--absolute",
            "--reporters", "json", "--output", str(ctx.work / "jscpd"),
            "--store-path", str(ctx.work / "jscpd-store"), *copy_paths(ctx)]


def _jscpd_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    report = json.loads((ctx.work / "jscpd" / "jscpd-report.json").read_text())
    result = ParseResult([], processed=report["statistics"]["total"]["sources"])
    for dup in report["duplicates"]:
        text = "\n".join(normalize_line(x) for x in dup["fragment"].splitlines())
        digest = hashlib.sha1(text.encode()).hexdigest()[:16]
        members = [(rel_path(ctx, dup[k]["name"]), dup[k]["start"])
                   for k in ("firstFile", "secondFile")]
        result.findings.append(Finding(
            rule="jscpd/clone", category="duplicate", severity="medium",
            confidence=Confidence.LIKELY, owner_repo=ctx.target.name,
            anchor=f"dup:text:{digest}",
            locations=[Location(p, n) for p, n in members],
            related=[{"owner_repo": ctx.target.name, "path": p, "line": n}
                     for p, n in members],
            evidence=[{"kind": "lines", "detail": str(dup["lines"])}]))
    return result


JSCPD = ProbeSpec(
    name="jscpd", languages=ANY, input_mode="files", select=_clone_files,
    canary=Canary(".selfcheck-canary/jscpd/canary.py", _JSCPD_CANARY, "jscpd/clone"),
    coverage="reported", binary="npx",
    version_args=("--yes", f"jscpd@{JSCPD_VERSION}", "--version"),
    version_range=((4, 3), (4, 4)), normal_codes=frozenset({0, 1}),
    argv=_jscpd_argv, parse=_jscpd_parse, config_files=(".jscpd.json",),
)

OTHER_PROBES = (SHELLCHECK, ACTIONLINT, ZIZMOR, JSCPD)
```

Замечание для исполнителя: `_is_canary` в ядре отбрасывает клон канарейки
jscpd, потому что обе локации лежат в `.selfcheck-canary/`; клон «файл репо ↔
канарейка» невозможен — содержимое канарейки уникально.

- [ ] **Step 4: тесты проходят**

Run: `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck/test_other_tools.py -q`
Expected: PASS. Если zizmor на канарейке не даёт `template-injection` в
`--offline`, поменять `expect_rule` канарейки на правило, которое он даёт на
этом файле (`artipacked` — проверено 2026-09-26), и закрепить новое ожидание
в тесте `test_actionlint_and_zizmor_find_injection`; ослаблять контракт
канарейки нельзя.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/probes/other_tools.py tests/selfcheck/test_other_tools.py
git commit -m "feat(selfcheck): shellcheck/actionlint/zizmor/jscpd с канарейками"
```

---
### Task 8: граф использования — модель, индекс, сканер команд, рёбра из структурных источников

**Files:**
- Create: `selfcheck/graph/__init__.py` (пустой), `selfcheck/graph/model.py`,
  `selfcheck/graph/commands.py`, `selfcheck/graph/build.py`
- Test: `tests/selfcheck/test_graph_build.py`

**Interfaces:**
- Consumes: `Location` (Task 1), `Role`, `role_of` (Task 2).
- Produces:
  - `NodeKind` (`file`, `make`, `skill`, `workflow`, `cli`), `Node(anchor, kind, path, name,
    root=False, executable=False)`;
  - `EdgeKind` (`make`, `ci`, `import`, `exec`, `entry`, `skill`, `sched`, `runbook`,
    `fleet`, `test`, `doc`), `NON_EXEC = {TEST, DOC}`, `Edge(target: str, kind, where: Location)`;
  - `Zone(caller: Location, members: frozenset[str], reason: str)`;
  - `Graph(nodes: dict[str, Node], edges: list[Edge], zones: list[Zone],
    mentions: dict[str, list[str]], broken: list[tuple[str, Location, str]],
    errors: list[str], sched_plists: int, root_texts: dict[str, str])` с
    `incoming(anchor) -> list[Edge]` и `add(target_path, kind, where) -> None`;
  - `Index(files: frozenset[str], modules: dict[str, str], clis: dict[str, str])`,
    `build_index(files: Sequence[str], pyproject: str | None) -> Index`;
  - `Scan(targets: list[str], missing: list[str], unresolved: list[str])`,
    `scan_command(cmd: str, base: str, index: Index, *, shell_vars: bool) -> Scan`;
  - `make_recipes(text: str) -> dict[str, list[tuple[int, str]]]`;
  - `build_graph(files: Sequence[str], root: Path, role: Callable[[str], Role],
    *, repo_name: str, sched_dir: Path | None) -> Graph`
    (вызывает `resolver.add_exec_edges(graph, ...)` — заглушка в этой задаче, реализация в Task 9).

- [ ] **Step 1: падающие тесты (включая Review Focus 5)**

`tests/selfcheck/test_graph_build.py`:

```python
from __future__ import annotations

import plistlib
from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.commands import build_index, scan_command
from selfcheck.graph.model import EdgeKind, NodeKind
from selfcheck.roles import role_of


def write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sorted(files)


def graph(tmp: Path, files: dict[str, str], sched_dir: Path | None = None):
    names = write(tmp / "repo", files)
    return build_graph(names, tmp / "repo", role_of, repo_name="repo",
                       sched_dir=sched_dir)


def kinds(g, anchor: str) -> set[EdgeKind]:
    return {e.kind for e in g.incoming(anchor)}


def test_scan_command_forms() -> None:
    index = build_index(["a.py", "deploy/r16/setup.sh", "gov/runner.py",
                         "gov/__init__.py", "x.sh"], None)
    scan = scan_command("@uv run --frozen --group g python -m gov.runner --x",
                        "", index, shell_vars=False)
    assert scan.targets == ["gov/__init__.py", "gov/runner.py"]
    scan = scan_command("sudo GIT_BASE=u deploy/r16/setup.sh", "", index,
                        shell_vars=True)
    assert scan.targets == ["deploy/r16/setup.sh"]
    scan = scan_command('sh "$kit/local.sh" "$@"', "", index, shell_vars=True)
    assert scan.unresolved == ["$kit/local.sh"]
    scan = scan_command("./missing.py --flag && ./x.sh", "", index, shell_vars=True)
    assert (scan.targets, scan.missing) == (["x.sh"], ["./missing.py"])
    assert scan_command("$(PYTHON) ./a.py", "", index, shell_vars=False).targets == [
        "a.py"]


def test_makefile_edges_roots_and_continuations(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "Makefile": (
            "help:\n\t@echo \"make run — запуск\"\n"
            "run: ; @python3 ./a.py $(ARGS)\n"
            "multi:\n\t@./b.sh \\\n\t  && ./c.sh\n"
            "broken:\n\t@./gone.py\n"
        ),
        "a.py": "print(1)\n", "b.sh": "echo\n", "c.sh": "echo\n",
    })
    assert kinds(g, "file:a.py") == {EdgeKind.MAKE}
    assert kinds(g, "file:c.sh") == {EdgeKind.MAKE}
    assert g.nodes["make:Makefile#run"].root
    assert not g.nodes["make:Makefile#multi"].root
    assert [(a, tok) for a, _, tok in g.broken] == []  # broken is not a root


def test_broken_root(tmp_path: Path) -> None:
    g = graph(tmp_path, {"Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\n'})
    assert [(a, tok) for a, _, tok in g.broken] == [("make:Makefile#gone", "./gone.py")]


def test_imports_ci_skills_runbook_doc_test_edges(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "pkg/__init__.py": "", "pkg/core.py": "from . import util\n",
        "pkg/util.py": "X = 1\n", "main.py": "import pkg.core\n",
        ".github/workflows/ci.yml": (
            "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
            "      - run: python3 main.py ${{ github.sha }}\n"),
        "skills/s/SKILL.md": "Запусти `./tool.sh --now`.\n",
        "tool.sh": "#!/bin/sh\necho\n",
        "deploy/README.md": "```bash\nsudo X=1 deploy/setup.sh\n```\nсм. [doc](../only_doc.py)\n",
        "deploy/setup.sh": "echo\n", "only_doc.py": "x = 1\n",
        "tests/test_x.py": "import helper_mod\nSCRIPT = 'tested.py'\n",
        "helper_mod.py": "", "tested.py": "",
    })
    assert kinds(g, "file:pkg/util.py") == {EdgeKind.IMPORT}
    assert kinds(g, "file:main.py") == {EdgeKind.CI}
    assert kinds(g, "file:tool.sh") == {EdgeKind.SKILL}
    assert kinds(g, "file:deploy/setup.sh") == {EdgeKind.RUNBOOK}
    assert kinds(g, "file:only_doc.py") == {EdgeKind.DOC}
    assert kinds(g, "file:helper_mod.py") == {EdgeKind.TEST}
    assert kinds(g, "file:tested.py") == {EdgeKind.TEST}
    assert g.nodes["skill:skills/s/SKILL.md"].root
    assert g.nodes["workflow:.github/workflows/ci.yml#t"].root


def test_cli_entry_and_sched(tmp_path: Path) -> None:
    sched = tmp_path / "agents"
    sched.mkdir()
    with (sched / "dev.atp.x.plist").open("wb") as handle:
        plistlib.dump({"ProgramArguments": [
            "/bin/sh", "-c", "cd /home/u/ws/repo && ./nightly.sh"]}, handle)
    g = graph(tmp_path, {
        "pyproject.toml": '[project.scripts]\nmytool = "pkg.cli:main"\n',
        "pkg/__init__.py": "", "pkg/cli.py": "def main(): ...\n",
        "nightly.sh": "echo\n", "unit.sh": "echo\n",
        "deploy/x.service": "[Service]\nExecStart=/srv/repo/unit.sh --go\n",
    }, sched_dir=sched)
    assert g.nodes["cli:mytool"].root
    assert kinds(g, "file:pkg/cli.py") == {EdgeKind.ENTRY}
    assert kinds(g, "file:nightly.sh") == {EdgeKind.SCHED}
    assert kinds(g, "file:unit.sh") == {EdgeKind.SCHED}
    assert g.sched_plists == 1


def test_diagnostic_output_gives_no_edges_or_mentions(tmp_path: Path) -> None:
    g = graph(tmp_path, {"reports/old.md": "ran `./lonely.py`\n", "lonely.py": "x = 1\n"})
    assert g.incoming("file:lonely.py") == []
    assert g.mentions.get("file:lonely.py", []) == []


def test_mentions_and_node_kinds(tmp_path: Path) -> None:
    g = graph(tmp_path, {"a.py": "# see helper.py\n", "helper.py": "x = 1\n",
                         "run": "#!/usr/bin/env bash\necho\n"})
    assert g.mentions["file:helper.py"] == ["a.py"]
    assert g.nodes["file:run"].kind is NodeKind.FILE and g.nodes["file:run"].executable
    assert not g.nodes["file:helper.py"].executable
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_build.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.graph'`.

- [ ] **Step 3: модель графа**

`selfcheck/graph/model.py`:

```python
"""Usage graph data model (spec §3.2.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from selfcheck.model import Location


class NodeKind(StrEnum):
    FILE = "file"
    MAKE = "make"
    SKILL = "skill"
    WORKFLOW = "workflow"
    CLI = "cli"


@dataclass(frozen=True)
class Node:
    """A graph node; roots are public entry points (spec §3.2.2)."""

    anchor: str
    kind: NodeKind
    path: str
    name: str
    root: bool = False
    executable: bool = False


class EdgeKind(StrEnum):
    MAKE = "make"
    CI = "ci"
    IMPORT = "import"
    EXEC = "exec"
    ENTRY = "entry"
    SKILL = "skill"
    SCHED = "sched"
    RUNBOOK = "runbook"
    FLEET = "fleet"
    TEST = "test"
    DOC = "doc"


NON_EXEC = frozenset({EdgeKind.TEST, EdgeKind.DOC})


@dataclass(frozen=True)
class Edge:
    target: str
    kind: EdgeKind
    where: Location


@dataclass(frozen=True)
class Zone:
    """Nodes a non-resolvable launch might reach (spec §3.2.3)."""

    caller: Location
    members: frozenset[str]
    reason: str


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    mentions: dict[str, list[str]] = field(default_factory=dict)
    broken: list[tuple[str, Location, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sched_plists: int = 0
    root_texts: dict[str, str] = field(default_factory=dict)

    def incoming(self, anchor: str) -> list[Edge]:
        return [e for e in self.edges if e.target == anchor]

    def add(self, target_path: str, kind: EdgeKind, where: Location) -> None:
        anchor = f"file:{target_path}"
        if anchor in self.nodes and target_path != where.path:
            self.edges.append(Edge(anchor, kind, where))
```

- [ ] **Step 4: индекс и сканер команд**

`selfcheck/graph/commands.py`:

```python
"""Resolve command lines to corpus files (spec §3.2.1, §3.2.3)."""

from __future__ import annotations

import posixpath
import re
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field

_SEPARATORS = {"&&", "||", ";", "|", "&", "then", "do", "else", "!", "("}
_WRAPPERS = {"sudo", "exec", "nohup", "env", "time", "command", "sh", "bash",
             "zsh", "source", ".", "python", "python3", "uv", "run"}
_UV_ARG_OPTS = {"--project", "--group", "--with", "--python", "--directory"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True)
class Index:
    """What a command token can resolve to."""

    files: frozenset[str]
    modules: dict[str, str]
    clis: dict[str, str]


def module_name(path: str) -> str | None:
    """``a/b/c.py`` → ``a.b.c``; ``src/`` stripped; packages → dir name."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def build_index(files: Sequence[str], pyproject: str | None) -> Index:
    """Index corpus files, modules and console scripts."""
    modules: dict[str, str] = {}
    for path in files:
        name = module_name(path)
        if name is not None:
            modules[name] = path
    clis: dict[str, str] = {}
    if pyproject:
        scripts = tomllib.loads(pyproject).get("project", {}).get("scripts", {})
        for cli, ref in scripts.items():
            mod = ref.split(":", 1)[0]
            if mod in modules:
                clis[cli] = modules[mod]
    return Index(frozenset(files), modules, clis)


def module_files(module: str, index: Index) -> list[str]:
    """Files executed/imported for ``module``: every package prefix + __main__."""
    parts = module.split(".")
    out = [index.modules[".".join(parts[:i])] for i in range(1, len(parts) + 1)
           if ".".join(parts[:i]) in index.modules]
    main = index.modules.get(f"{module}.__main__")
    if main is not None:
        out.append(main)
    return out


@dataclass
class Scan:
    targets: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _tokens(cmd: str) -> list[str]:
    cmd = cmd.replace("$(", " ; ").replace("`", " ; ")
    try:
        return shlex.split(cmd, comments=True)
    except ValueError:
        return cmd.split()


def scan_command(cmd: str, base: str, index: Index, *, shell_vars: bool) -> Scan:
    """Corpus files a command line runs or names.

    ``shell_vars``: shell ``$var`` in command position is unresolved (a zone);
    make ``$(VAR)`` tokens are skipped instead.
    """
    scan = Scan()
    position = True
    skip_next = False
    prev = ""
    for raw in _tokens(cmd):
        token = raw.lstrip("@-") if position else raw
        if skip_next:
            skip_next, prev = False, token
            continue
        if token in _SEPARATORS or token.endswith(";"):
            position, prev = True, token
            continue
        if position and _ASSIGN.match(token):
            prev = token
            continue
        if prev == "-m":
            scan.targets += module_files(token, index)
            position, prev = False, token
            continue
        if token in _UV_ARG_OPTS:
            skip_next, prev = True, token
            continue
        if position and token in _WRAPPERS:
            prev = token
            continue
        if position and token.startswith("-"):
            prev = token
            continue
        if "$" in token:
            if shell_vars and position and "${{" not in token:
                scan.unresolved.append(token)
            position, prev = False, token
            continue
        _resolve_path(token, base, index, scan, position)
        position, prev = False, token
    scan.targets = list(dict.fromkeys(scan.targets))
    return scan


def _resolve_path(token: str, base: str, index: Index, scan: Scan,
                  position: bool) -> None:
    clean = token.strip("'\"")
    if position and clean in index.clis:
        scan.targets.append(index.clis[clean])
        return
    norm = posixpath.normpath(posixpath.join(base, clean)) if clean else ""
    if norm in index.files:
        scan.targets.append(norm)
    elif (clean.endswith((".py", ".sh")) and not norm.startswith("..")
          and "*" not in clean and not clean.startswith("/")):
        scan.missing.append(clean)
```

- [ ] **Step 5: построение графа**

`selfcheck/graph/build.py`:

```python
"""Nodes and edges from structured sources (spec §3.2.1–3.2.2)."""

from __future__ import annotations

import ast
import plistlib
import posixpath
import re
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml

from selfcheck.graph import resolver
from selfcheck.graph.commands import Index, build_index, module_files, scan_command
from selfcheck.graph.model import EdgeKind, Graph, Node, NodeKind
from selfcheck.model import Location
from selfcheck.roles import Role

_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?![=:])(.*)$")
_FENCE = re.compile(r"^```\s*([\w-]*)\s*$")
_RUNBOOK_LANGS = {"sh", "bash", "console", "shell"}
_WORD = re.compile(r"[\w./-]+")


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(errors="replace")
    except OSError:
        return ""


def _is_script(text: str, rel: str) -> bool:
    return (text.startswith("#!") or rel.endswith(".sh")
            or "__name__ == \"__main__\"" in text or "__name__ == '__main__'" in text)


def _is_code_file(text: str, rel: str) -> bool:
    return rel.endswith((".py", ".sh")) or text.startswith("#!")


def build_graph(files: Sequence[str], root: Path, role: Callable[[str], Role], *,
                repo_name: str, sched_dir: Path | None) -> Graph:
    """Build the usage graph of one repo (or of one canary set)."""
    texts = {rel: _read(root, rel) for rel in files}
    roles = {rel: role(rel) for rel in files}
    pyproject = texts.get("pyproject.toml")
    index = build_index(list(files), pyproject)
    g = Graph()
    for rel in files:
        if roles[rel] is Role.SOURCE and _is_code_file(texts[rel], rel):
            g.nodes[f"file:{rel}"] = Node(f"file:{rel}", NodeKind.FILE, rel,
                                          posixpath.basename(rel),
                                          executable=_is_script(texts[rel], rel))
        if roles[rel] is Role.SKILL_ROOT:
            g.nodes[f"skill:{rel}"] = Node(f"skill:{rel}", NodeKind.SKILL, rel, rel,
                                           root=True)
    _entry_points(g, pyproject, index)
    for rel in files:
        text, kind = texts[rel], roles[rel]
        if kind is Role.DIAGNOSTIC_OUTPUT:
            continue
        name = posixpath.basename(rel)
        if kind is Role.SOURCE and (name == "Makefile" or name.endswith(".mk")):
            _makefile(g, rel, text, index)
        if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
            _workflow(g, rel, text, index)
        if rel.endswith(".service"):
            _service(g, rel, text, index)
        if rel.endswith(".py") and kind in (Role.SOURCE, Role.TEST, Role.CANARY):
            _python(g, rel, text, index, test=kind is Role.TEST)
        if kind is Role.SKILL_ROOT:
            _markdown(g, rel, text, index, fenced=EdgeKind.SKILL, prose=EdgeKind.SKILL)
        if kind is Role.DOCUMENTATION and rel.endswith(".md"):
            _markdown(g, rel, text, index, fenced=EdgeKind.RUNBOOK, prose=EdgeKind.DOC)
    if sched_dir is not None:
        _launchd(g, sched_dir, repo_name, index)
    resolver.add_exec_edges(g, files, texts, roles, index)
    _mentions(g, files, texts, roles)
    return g


def _entry_points(g: Graph, pyproject: str | None, index: Index) -> None:
    if not pyproject:
        return
    project = tomllib.loads(pyproject).get("project", {})
    where = Location("pyproject.toml", 1)
    for cli, ref in project.get("scripts", {}).items():
        g.nodes[f"cli:{cli}"] = Node(f"cli:{cli}", NodeKind.CLI, "pyproject.toml",
                                     cli, root=True)
        for path in module_files(ref.split(":", 1)[0], index):
            g.add(path, EdgeKind.ENTRY, where)
    for group in project.get("entry-points", {}).values():
        for ref in group.values():
            for path in module_files(ref.split(":", 1)[0], index):
                g.add(path, EdgeKind.ENTRY, where)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = number
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((start, buf + line))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def make_recipes(text: str) -> dict[str, list[tuple[int, str]]]:
    """Makefile target → [(line, command)], incl. ``target: ; cmd`` and ``\\``."""
    recipes: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for number, line in _logical_lines(text):
        if line.startswith("\t") and current is not None:
            recipes[current].append((number, line.strip()))
            continue
        match = _TARGET.match(line)
        if match and not line.startswith(".PHONY"):
            current = match.group(1)
            recipes.setdefault(current, [])
            rest = match.group(2)
            if ";" in rest:
                recipes[current].append((number, rest.split(";", 1)[1].strip()))
        elif line.strip():
            current = None
    return recipes


def _makefile(g: Graph, rel: str, text: str, index: Index) -> None:
    base = posixpath.dirname(rel)
    recipes = make_recipes(text)
    help_text = " ".join(cmd for _, cmd in recipes.get("help", []))
    for target, lines in recipes.items():
        root = target == "help" or re.search(rf"\bmake {re.escape(target)}\b",
                                             help_text) is not None
        anchor = f"make:{rel}#{target}"
        g.nodes[anchor] = Node(anchor, NodeKind.MAKE, rel, target, root=root)
        for number, cmd in lines:
            scan = scan_command(cmd, base, index, shell_vars=False)
            where = Location(rel, number)
            for path in scan.targets:
                g.add(path, EdgeKind.MAKE, where)
            if root:
                g.broken += [(anchor, where, tok) for tok in scan.missing]


def _workflow(g: Graph, rel: str, text: str, index: Index) -> None:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        g.errors.append(f"{rel}: {exc}")
        return
    for job, spec in (data.get("jobs") or {}).items():
        anchor = f"workflow:{rel}#{job}"
        g.nodes[anchor] = Node(anchor, NodeKind.WORKFLOW, rel, job, root=True)
        for step in (spec or {}).get("steps") or []:
            where = Location(rel, 1)
            uses = str(step.get("uses", ""))
            if uses.startswith("./"):
                for name in ("action.yml", "action.yaml"):
                    g.add(posixpath.join(uses[2:], name), EdgeKind.CI, where)
            for line in str(step.get("run", "")).splitlines():
                scan = scan_command(line, "", index, shell_vars=False)
                for path in scan.targets:
                    g.add(path, EdgeKind.CI, where)
                g.broken += [(anchor, where, tok) for tok in scan.missing]


def _service(g: Graph, rel: str, text: str, index: Index) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("ExecStart="):
            value = line.split("=", 1)[1]
            for word in _WORD.findall(value):
                for path in index.files:
                    if word.endswith("/" + path) or word == path:
                        g.add(path, EdgeKind.SCHED, Location(rel, number))


def _launchd(g: Graph, sched_dir: Path, repo_name: str, index: Index) -> None:
    """Edges from machine-local launchd plists (spec §3.2.1, sched)."""
    marker = f"/{repo_name}/"
    for plist in sorted(sched_dir.glob("*.plist")):
        g.sched_plists += 1
        try:
            with plist.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            g.errors.append(f"{plist}: {exc}")
            continue
        args = [str(data.get("Program", "")),
                *(str(a) for a in data.get("ProgramArguments", []))]
        where = Location(f"launchd:{plist.name}", 1)
        cwd: str | None = None
        for chunk in re.split(r"&&|;", " ".join(args)):
            words = chunk.split()
            if "cd" in words and words.index("cd") + 1 < len(words):
                path = words[words.index("cd") + 1].rstrip("/")
                inside = path.endswith(f"/{repo_name}") or marker in path
                cwd = path.split(marker, 1)[1] if marker in path else "" if inside else None
                continue
            for word in _WORD.findall(chunk):
                if marker in word:
                    g.add(posixpath.normpath(word.split(marker)[-1]), EdgeKind.SCHED, where)
                elif cwd is not None and word.startswith("./"):
                    g.add(posixpath.normpath(posixpath.join(cwd, word)),
                          EdgeKind.SCHED, where)


def _imports(tree: ast.AST, rel: str) -> list[str]:
    package = rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else ""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".") if package else []
            if node.level:
                parts = parts[: len(parts) - node.level + 1]
            base = ".".join([*parts, node.module] if node.module else parts)
            found.append(base)
            found += [f"{base}.{a.name}" if base else a.name for a in node.names]
    return found


def _python(g: Graph, rel: str, text: str, index: Index, *, test: bool) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        if not test:
            g.errors.append(f"{rel}: {exc.msg} (line {exc.lineno})")
        return
    kind = EdgeKind.TEST if test else EdgeKind.IMPORT
    for module in _imports(tree, rel):
        for path in module_files(module, index):
            g.add(path, kind, Location(rel, 1))
    if test:
        base = posixpath.dirname(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for cand in (node.value, posixpath.join(base, node.value)):
                    norm = posixpath.normpath(cand)
                    if norm in index.files:
                        g.add(norm, EdgeKind.TEST, Location(rel, node.lineno))


def _markdown(g: Graph, rel: str, text: str, index: Index, *, fenced: EdgeKind,
              prose: EdgeKind) -> None:
    base = posixpath.dirname(rel)
    lang: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        fence = _FENCE.match(line.strip())
        if fence:
            lang = None if lang is not None else fence.group(1).lower()
            continue
        where = Location(rel, number)
        runnable = lang is not None and (lang in _RUNBOOK_LANGS or fenced is EdgeKind.SKILL)
        if runnable:
            command = line.strip().removeprefix("$ ")
            for b in ("", base):
                scan = scan_command(command, b, index, shell_vars=False)
                for path in scan.targets:
                    g.add(path, fenced, where)
            continue
        snippets = re.findall(r"`([^`]+)`", line) if fenced is EdgeKind.SKILL else []
        for snippet in snippets:
            for path in scan_command(snippet, "", index, shell_vars=False).targets:
                g.add(path, fenced, where)
        for word in re.findall(r"\]\(([^)]+)\)", line) + _WORD.findall(line):
            for cand in (word, posixpath.join(base, word)):
                norm = posixpath.normpath(cand.removeprefix("./"))
                if norm in index.files:
                    g.add(norm, prose, where)


def _mentions(g: Graph, files: Sequence[str], texts: dict[str, str],
              roles: dict[str, Role]) -> None:
    sources = [f for f in files if roles[f] in (Role.SOURCE, Role.SKILL_ROOT)]
    g.root_texts = {f: texts[f] for f in sources}
    for anchor, node in g.nodes.items():
        if node.kind is not NodeKind.FILE:
            continue
        pattern = re.compile(rf"(?<![\w.-]){re.escape(node.name)}(?![\w-])")
        hits = [f for f in sources if f != node.path and node.name in texts[f]
                and pattern.search(texts[f])]
        if hits:
            g.mentions[anchor] = hits
```

`selfcheck/graph/resolver.py` (заглушка до Task 9):

```python
"""Computed-launch resolution (spec §3.2.3) — implemented in Task 9."""

from __future__ import annotations

from collections.abc import Sequence

from selfcheck.graph.commands import Index
from selfcheck.graph.model import Graph
from selfcheck.roles import Role


def add_exec_edges(g: Graph, files: Sequence[str], texts: dict[str, str],
                   roles: dict[str, Role], index: Index) -> None:
    """Add exec edges and zones; stub until Task 9."""
    return None
```

- [ ] **Step 6: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_build.py -q`
Expected: PASS.

- [ ] **Step 7: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/graph tests/selfcheck/test_graph_build.py
git commit -m "feat(selfcheck): граф использования — узлы, корни, рёбра из структурных источников"
```

---

### Task 9: вычисляемые запуски и зоны (§3.2.3)

**Files:**
- Modify: `selfcheck/graph/resolver.py` (заменить заглушку)
- Test: `tests/selfcheck/test_graph_resolver.py`

**Interfaces:**
- Consumes: `Graph`, `Zone`, `EdgeKind`, `Index`, `scan_command`, `Scan` (Task 8).
- Produces: `add_exec_edges(g, files, texts, roles, index) -> None` — добавляет рёбра
  `EXEC` и зоны; `evaluate(expr: ast.expr, scope: _Scope) -> str | list[str] | None`
  (внутренний, `UNKNOWN = "<?>"`).

- [ ] **Step 1: падающие тесты (трудные вызывающие спеки §6.1)**

`tests/selfcheck/test_graph_resolver.py`:

```python
from __future__ import annotations

from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.model import EdgeKind
from selfcheck.roles import role_of


def graph(tmp: Path, files: dict[str, str]):
    for rel, text in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return build_graph(sorted(files), tmp, role_of, repo_name="r", sched_dir=None)


def exec_targets(g) -> set[str]:
    return {e.target for e in g.edges if e.kind is EdgeKind.EXEC}


def zone_members(g) -> set[str]:
    return {m for z in g.zones for m in z.members}


def test_with_name_and_tmux_join(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "console.py": (
            "import shlex, subprocess, sys\nfrom pathlib import Path\n\n"
            "def spawn(repo):\n"
            "    worker = Path(__file__).with_name('worker.py')\n"
            "    cmd = [sys.executable, str(worker), '--repo', repo]\n"
            "    shell_cmd = ' '.join(shlex.quote(p) for p in cmd) + '; exec sh'\n"
            "    subprocess.run(['tmux', 'new-session', '-d', shell_cmd])\n"),
        "worker.py": "print(1)\n",
    })
    assert exec_targets(g) == {"file:worker.py"}
    assert g.zones == []


def test_parent_chain_and_os_path(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "tools/a.py": (
            "import os, subprocess\nfrom pathlib import Path\n"
            "ROOT = Path(__file__).parent.parent\n"
            "subprocess.run([str(ROOT / 'b.sh')])\n"
            "subprocess.call(os.path.join(os.path.dirname(__file__), 'c.sh'))\n"),
        "b.sh": "echo\n", "tools/c.sh": "echo\n",
    })
    assert exec_targets(g) == {"file:b.sh", "file:tools/c.sh"}


def test_shell_script_dir_forms(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "bin/run.sh": (
            '#!/bin/sh\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
            'HERE=$(dirname "$0")\n"$SCRIPT_DIR/x.sh" --go\nsh "$HERE/y.sh"\n'),
        "bin/x.sh": "echo\n", "bin/y.sh": "echo\n",
    })
    assert exec_targets(g) == {"file:bin/x.sh", "file:bin/y.sh"}


def test_unresolved_shell_launch_makes_suffix_zone(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "review.sh": ('#!/bin/sh\nkit=$(resolve "$1")\nREVIEW=1 \\\n'
                      '    sh "$kit/local.sh" "$@"\n'),
        "scripts/review/local.sh": "echo\n", "other.sh": "echo\n",
    })
    assert zone_members(g) == {"file:scripts/review/local.sh"}


def test_unresolved_without_suffix_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "pkg/loader.py": ("import importlib\n\ndef load(name):\n"
                          "    return importlib.import_module(name)\n"),
        "pkg/plugin_a.py": "x = 1\n", "elsewhere.py": "y = 1\n",
    })
    assert zone_members(g) == {"file:pkg/loader.py", "file:pkg/plugin_a.py"}


def test_extensionless_shebang_calls(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "harness": "#!/bin/sh\nexec ./helper.sh \"$@\"\n", "helper.sh": "echo\n",
    })
    assert exec_targets(g) == {"file:helper.sh"}


def test_same_module_wrapper_resolved_at_call_sites(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "runner.py": ("import subprocess\n\n\ndef _run(cmd, check=True):\n"
                      "    return subprocess.run(cmd, check=check)\n\n\n"
                      "_run(['./tool.sh', '--x'])\n"),
        "tool.sh": "echo\n", "other.sh": "echo\n",
    })
    assert exec_targets(g) == {"file:tool.sh"}
    assert g.zones == []


def test_constant_import_module_is_an_edge(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "a.py": "import importlib\nimportlib.import_module('b')\n", "b.py": "",
    })
    assert "file:b.py" in exec_targets(g) and g.zones == []
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_resolver.py -q`
Expected: FAIL — `assert set() == {'file:worker.py'}` (заглушка не добавляет рёбер).

- [ ] **Step 3: реализация**

`selfcheck/graph/resolver.py`:

```python
"""Computed-launch resolution and unresolved zones (spec §3.2.3)."""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from selfcheck.graph.commands import Index, Scan, module_files, scan_command
from selfcheck.graph.model import EdgeKind, Graph, NodeKind, Zone
from selfcheck.model import Location
from selfcheck.roles import Role

UNKNOWN = "<?>"
_RUN_FUNCS = {"run", "call", "check_call", "check_output", "Popen", "system",
              "execv", "execvp", "execl", "execlp", "create_subprocess_exec",
              "create_subprocess_shell", "spawnv"}
_IMPORT_FUNCS = {"import_module", "__import__"}
_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+)?([A-Za-z_]\w*)=(.*)$")
_VAR = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
Value = str | list[str] | None


@dataclass
class _Scope:
    rel: str
    names: dict[str, ast.expr] = field(default_factory=dict)
    depth: int = 0


def _dirname(value: str) -> str:
    return posixpath.dirname(value)


def evaluate(expr: ast.expr, scope: _Scope) -> Value:
    """Evaluate a path/argv expression; unknown parts become ``<?>``."""
    if scope.depth > 12:
        return None
    scope.depth += 1
    try:
        return _eval(expr, scope)
    finally:
        scope.depth -= 1


def _as_str(value: Value) -> str:
    if isinstance(value, list):
        return " ".join(value)
    return value if value is not None else UNKNOWN


def _eval(e: ast.expr, s: _Scope) -> Value:
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    if isinstance(e, ast.Name):
        if e.id == "__file__":
            return s.rel
        bound = s.names.get(e.id)
        return evaluate(bound, s) if bound is not None else None
    if isinstance(e, ast.Attribute):
        if e.attr == "executable" and isinstance(e.value, ast.Name) and e.value.id == "sys":
            return "python"
        base = evaluate(e.value, s)
        if e.attr == "parent" and isinstance(base, str):
            return _dirname(base)
        return None
    if isinstance(e, ast.List | ast.Tuple):
        return [_as_str(evaluate(x, s)) for x in e.elts]
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div | ast.Add):
        left, right = evaluate(e.left, s), evaluate(e.right, s)
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        if isinstance(e.op, ast.Div):
            return posixpath.join(_as_str(left), _as_str(right))
        return _as_str(left) + _as_str(right)
    if isinstance(e, ast.JoinedStr):
        parts = [v.value if isinstance(v, ast.Constant) else
                 _as_str(evaluate(v.value, s)) if isinstance(v, ast.FormattedValue)
                 else UNKNOWN for v in e.values]
        return "".join(str(p) for p in parts)
    if isinstance(e, ast.Call):
        return _eval_call(e, s)
    return None


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _eval_call(call: ast.Call, s: _Scope) -> Value:
    name = _call_name(call)
    args = [evaluate(a, s) for a in call.args]
    func = call.func
    if name in ("Path", "PurePath", "PosixPath", "str", "abspath", "realpath",
                "normpath", "resolve", "expanduser", "fspath") and args:
        return args[0]
    if name == "resolve" and isinstance(func, ast.Attribute):
        return evaluate(func.value, s)
    if name == "with_name" and isinstance(func, ast.Attribute) and args:
        return posixpath.join(_dirname(_as_str(evaluate(func.value, s))), _as_str(args[0]))
    if name == "dirname" and args:
        return _dirname(_as_str(args[0]))
    if name == "join" and isinstance(func, ast.Attribute):
        owner = func.value
        if isinstance(owner, ast.Constant) and isinstance(owner.value, str):
            items: Value = None
            if call.args and isinstance(call.args[0], ast.GeneratorExp):
                items = evaluate(call.args[0].generators[0].iter, s)
            elif args:
                items = args[0]
            if isinstance(items, list):
                return owner.value.join(items)
            return None
        return posixpath.join(*[_as_str(a) for a in args]) if args else None
    return None


@dataclass(frozen=True)
class _Site:
    call: ast.Call
    scope: _Scope
    params: tuple[str, ...]
    func: str


class _Collector(ast.NodeVisitor):
    """Every call with its evaluation scope and enclosing function."""

    def __init__(self, rel: str, module_names: dict[str, ast.expr]) -> None:
        self.rel = rel
        self.module_names = module_names
        self.sites: list[_Site] = []
        self.scope = _Scope(rel, dict(module_names))
        self.params: tuple[str, ...] = ()
        self.func = ""

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        outer = (self.scope, self.params, self.func)
        self.scope = _Scope(self.rel, {**self.module_names, **_assignments(node.body)})
        args = node.args
        self.params = tuple(a.arg for a in [*args.posonlyargs, *args.args,
                                            *args.kwonlyargs])
        self.func = node.name
        self.generic_visit(node)
        self.scope, self.params, self.func = outer

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        self.sites.append(_Site(node, self.scope, self.params, self.func))
        self.generic_visit(node)


def _assignments(body: list[ast.stmt]) -> dict[str, ast.expr]:
    names: dict[str, ast.expr] = {}
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                    node.targets[0], ast.Name):
                names[node.targets[0].id] = node.value
    return names


def add_exec_edges(g: Graph, files: Sequence[str], texts: dict[str, str],
                   roles: dict[str, Role], index: Index) -> None:
    """Add exec edges from Python and shell sources; record zones."""
    for rel in files:
        if roles[rel] not in (Role.SOURCE, Role.CANARY):
            continue
        text = texts[rel]
        if rel.endswith(".py"):
            _python(g, rel, text, index)
        elif rel.endswith((".sh", ".bash")) or (text.startswith("#!") and "sh" in
                                                text.splitlines()[0]):
            _shell(g, rel, text, index)


def _forwarded(site: _Site) -> str | None:
    """Name of the parameter a launch call forwards, if any."""
    first = site.call.args[0] if site.call.args else None
    if (isinstance(first, ast.Name) and first.id in site.params
            and first.id not in site.scope.names):
        return first.id
    return None


def _wrappers(sites: list[_Site]) -> dict[str, int]:
    """Same-module functions that pass a parameter straight into a launch."""
    out: dict[str, int] = {}
    for site in sites:
        param = _forwarded(site) if _call_name(site.call) in _RUN_FUNCS else None
        if param is not None and site.func:
            index = site.params.index(param)
            out[site.func] = index - 1 if site.params[0] in ("self", "cls") else index
    return out


def _python(g: Graph, rel: str, text: str, index: Index) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    collector = _Collector(rel, _assignments(tree.body))
    collector.visit(tree)
    wrappers = _wrappers(collector.sites)
    for site in collector.sites:
        call, name = site.call, _call_name(site.call)
        where = Location(rel, call.lineno)
        if name in _IMPORT_FUNCS and call.args:
            module = evaluate(call.args[0], site.scope)
            if isinstance(module, str) and UNKNOWN not in module:
                for path in module_files(module, index):
                    g.add(path, EdgeKind.EXEC, where)
            else:
                _zone(g, where, "", "dynamic import")
        elif name in _RUN_FUNCS and call.args:
            if site.func in wrappers and _forwarded(site) is not None:
                continue  # resolved at the wrapper's call sites
            _launch(g, where, evaluate(call.args[0], site.scope), index)
        elif name in wrappers and len(call.args) > wrappers[name]:
            _launch(g, where, evaluate(call.args[wrappers[name]], site.scope), index)


def _launch(g: Graph, where: Location, argv: Value, index: Index) -> None:
    command = argv if isinstance(argv, list) else [_as_str(argv)]
    _apply(g, where, " ".join(_quote(t) for t in command), index, "")
    for token in command[1:]:
        if " " in token:
            _apply(g, where, token, index, "")


def _quote(token: str) -> str:
    return token if " " not in token else f"'{token}'"


def _apply(g: Graph, where: Location, cmd: str, index: Index, base: str) -> Scan:
    scan = scan_command(cmd.replace(UNKNOWN, "$UNKNOWN"), base, index, shell_vars=True)
    for path in scan.targets:
        g.add(path, EdgeKind.EXEC, where)
    for token in scan.unresolved:
        suffix = token.rsplit("/", 1)[-1] if "/" in token else ""
        _zone(g, where, "" if "$" in suffix else suffix, "unresolved launch")
    return scan


def _zone(g: Graph, where: Location, suffix: str, reason: str) -> None:
    files = [n for n in g.nodes.values() if n.kind is NodeKind.FILE]
    if suffix:
        members = {n.anchor for n in files if n.name == suffix}
    else:
        folder = posixpath.dirname(where.path)
        members = {n.anchor for n in files if posixpath.dirname(n.path) == folder}
    if members:
        g.zones.append(Zone(where, frozenset(members), reason))


def _shell(g: Graph, rel: str, text: str, index: Index) -> None:
    base = posixpath.dirname(rel)
    known: dict[str, str] = {}
    buf, start = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = number
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        logical, buf = buf + line, ""
        stripped = logical.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGN.match(stripped)
        if match and "dirname" in match.group(2) and (
                "$0" in match.group(2) or "BASH_SOURCE" in match.group(2)):
            known[match.group(1)] = "."
            continue
        expanded = _VAR.sub(lambda m: known.get(m.group(1), m.group(0)), stripped)
        _apply(g, Location(rel, start), expanded, index, base)
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_resolver.py tests/selfcheck/test_graph_build.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/graph/resolver.py tests/selfcheck/test_graph_resolver.py
git commit -m "feat(selfcheck): резолвер вычисляемых запусков и зоны (§3.2.3)"
```

---

### Task 10: классификация dead, корни, проба `usage-graph` (§2.3, §3.2.2, §3.2.4)

**Files:**
- Create: `selfcheck/graph/classify.py`, `selfcheck/graph/probe.py`
- Test: `tests/selfcheck/test_graph_classify.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 5, 8, 9.
- Produces:
  - `Surface(fleet: str, sched_dir: str | None, sched_plists: int)`;
  - `NodeFacts(klass: str, root: bool, in_zone: bool, history: bool,
    age_days: float | None, mentioned: bool)`;
  - `dead_confidence(facts: NodeFacts, surface: Surface) -> tuple[Confidence | None, list[str]]`;
  - `klass_of(g: Graph, anchor: str) -> str` (`live` | `test-only` | `doc-only` | `orphan`);
  - `classify(g, *, repo: str, surface: Surface, ages: Callable[[str], float | None],
    now: float) -> list[Finding]`;
  - `USAGE_GRAPH: ProbeSpec` (`graph/probe.py`).

- [ ] **Step 1: падающие тесты — матрица D1–D12 и корни**

`tests/selfcheck/test_graph_classify.py`:

```python
from __future__ import annotations

import plistlib
import time
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import NodeFacts, Surface, dead_confidence, klass_of
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.model import Confidence
from selfcheck.probes.base import ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.roles import role_of
from tests.selfcheck.helpers import make_repo

C, L, K = Confidence.CONFIRMED, Confidence.LIKELY, Confidence.CANDIDATE
FULL = Surface("complete", "/sched", 3)

# Спека §2.3, матрица сочетаний: (klass, root, zone, fleet, sched, history, age, mention) → итог
MATRIX = [
    ("D1", "orphan", False, False, "complete", True, True, 90, False, C),
    ("D2", "orphan", False, False, "absent", True, True, 90, False, L),
    ("D3", "orphan", False, False, "complete", False, True, 90, False, L),
    ("D4", "orphan", False, False, "complete", True, True, 10, False, L),
    ("D5", "orphan", False, False, "complete", True, False, None, False, L),
    ("D6", "orphan", False, False, "complete", True, True, 90, True, L),
    ("D7", "test-only", False, False, "complete", True, True, 90, False, K),
    ("D8", "doc-only", False, False, "absent", False, False, None, True, K),
    ("D9", "orphan", True, False, "complete", True, True, 90, False, None),
    ("D10", "orphan", False, True, "complete", True, True, 90, False, None),
    ("D11", "live", False, False, "complete", True, True, 90, False, None),
]


@pytest.mark.parametrize("row", MATRIX, ids=lambda r: r[0])
def test_dead_matrix(row) -> None:
    _, klass, root, zone, fleet, sched, history, age, mention, expected = row
    surface = Surface(fleet, "/sched" if sched else None, 1 if sched else 0)
    facts = NodeFacts(klass, root, zone, history, age, mention)
    assert dead_confidence(facts, surface)[0] == expected


def test_d12_plist_makes_live(tmp_path: Path) -> None:
    sched = tmp_path / "agents"
    sched.mkdir()
    with (sched / "a.plist").open("wb") as handle:
        plistlib.dump({"ProgramArguments": ["/w/repo/job.py"]}, handle)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "job.py").write_text('if __name__ == "__main__":\n    pass\n')
    g = build_graph(["job.py"], root, role_of, repo_name="repo", sched_dir=sched)
    assert klass_of(g, "file:job.py") == "live"


def run_usage(tmp: Path, files: dict[str, str], date: str = "2026-01-01T00:00:00"):
    repo = make_repo(tmp / "repo", files, date=date)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([USAGE_GRAPH]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=time.time())
    try:
        return run_probe(USAGE_GRAPH, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_probe_s1_orphan_is_likely_and_roots_not_dead(tmp_path: Path) -> None:
    res = run_usage(tmp_path, {
        "Makefile": 'help:\n\t@echo "make go"\ngo: ; @python3 ./live.py\n',
        "live.py": 'if __name__ == "__main__":\n    pass\n',
        "orphan.py": 'if __name__ == "__main__":\n    pass\n',
        "skills/s/SKILL.md": "no calls\n",
    })
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    dead = {f.anchor: f for f in res.findings if f.category == "dead"}
    assert set(dead) == {"file:orphan.py"}
    assert dead["file:orphan.py"].confidence is Confidence.LIKELY  # P1, P2 in S1
    caps = {e["detail"] for e in dead["file:orphan.py"].evidence if e["kind"] == "cap"}
    assert {"P1", "P2"} <= caps


def test_probe_zone_reported_not_dead(tmp_path: Path) -> None:
    res = run_usage(tmp_path, {
        "review.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
        "scripts/review/local.sh": "#!/bin/sh\necho\n",
    })
    rules = {(f.rule, f.anchor) for f in res.findings}
    assert ("usage-graph/dead.file", "file:scripts/review/local.sh") not in rules
    assert any(r == "usage-graph/unresolved-exec" for r, _ in rules)


def test_probe_broken_and_stale_roots(tmp_path: Path) -> None:
    res = run_usage(tmp_path, {
        "Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\n',
    }, date="2025-01-01T00:00:00")
    rules = {f.rule for f in res.findings}
    assert "usage-graph/broken-root" in rules
    assert "usage-graph/root-stale" in rules


def test_probe_syntax_error_in_source_is_partial(tmp_path: Path) -> None:
    res = run_usage(tmp_path, {"bad.py": "def f(:\n"})
    assert res.status is ProbeStatus.PARTIAL
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_classify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.graph.classify'`.

- [ ] **Step 3: классификация**

`selfcheck/graph/classify.py`:

```python
"""Dead: class → eligibility → confidence; roots (spec §2.3, §3.2.2)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from selfcheck.graph.model import NON_EXEC, EdgeKind, Graph, NodeKind
from selfcheck.model import Confidence, Finding, Location, cap, make_text_key

DAY = 86400.0
AGE_DAYS = 60
ROOT_STALE_DAYS = 180


@dataclass(frozen=True)
class Surface:
    """What sources this run consulted (spec §3.2.4)."""

    fleet: str
    sched_dir: str | None
    sched_plists: int


@dataclass(frozen=True)
class NodeFacts:
    klass: str
    root: bool
    in_zone: bool
    history: bool
    age_days: float | None
    mentioned: bool


def dead_confidence(facts: NodeFacts,
                    surface: Surface) -> tuple[Confidence | None, list[str]]:
    """Steps 2 and 3 of spec §2.3: eligibility, then min(base, caps)."""
    if facts.klass == "live" or facts.root or facts.in_zone:
        return None, []
    value = Confidence.CONFIRMED if facts.klass == "orphan" else Confidence.CANDIDATE
    caps: list[str] = []
    if surface.fleet != "complete":
        caps.append("P1")
    if surface.sched_dir is None:
        caps.append("P2")
    if not facts.history:
        caps.append("P3")
    elif facts.age_days is not None and facts.age_days < AGE_DAYS:
        caps.append("P4")
    if facts.mentioned:
        caps.append("P5")
    for _ in caps:
        value = cap(value, Confidence.LIKELY)
    return value, caps


def klass_of(g: Graph, anchor: str) -> str:
    """Step 1 of spec §2.3."""
    kinds = {e.kind for e in g.incoming(anchor)}
    if kinds - NON_EXEC:
        return "live"
    if kinds == {EdgeKind.TEST}:
        return "test-only"
    if EdgeKind.DOC in kinds:
        return "doc-only"
    return "orphan"


def classify(g: Graph, *, repo: str, surface: Surface,
             ages: Callable[[str], float | None], now: float) -> list[Finding]:
    """All usage-graph findings of one graph."""
    in_zone = {m for z in g.zones for m in z.members}
    findings: list[Finding] = []
    for anchor, node in sorted(g.nodes.items()):
        if node.kind is not NodeKind.FILE:
            continue
        ts = ages(node.path)
        facts = NodeFacts(klass_of(g, anchor), node.root, anchor in in_zone,
                          ts is not None, (now - ts) / DAY if ts else None,
                          bool(g.mentions.get(anchor)))
        value, caps = dead_confidence(facts, surface)
        if value is None:
            continue
        rule = "usage-graph/dead.file" if node.executable else "usage-graph/dead.module"
        findings.append(Finding(
            rule=rule, category="dead", severity="medium", confidence=value,
            owner_repo=repo, anchor=anchor, locations=[Location(node.path, 1)],
            evidence=[{"kind": "class", "detail": facts.klass},
                      *[{"kind": "cap", "detail": c} for c in caps],
                      *[{"kind": "mentioned-in", "detail": p}
                        for p in g.mentions.get(anchor, [])]],
            suggestion="удалить или перенести в docs/archive"))
    for zone in g.zones:
        findings.append(Finding(
            rule="usage-graph/unresolved-exec", category="quality", severity="low",
            confidence=Confidence.CANDIDATE, owner_repo=repo,
            anchor=f"file:{zone.caller.path}", locations=[zone.caller],
            text_key=make_text_key(f"{zone.caller.line}:{zone.reason}"),
            evidence=[{"kind": "zone-member", "detail": m} for m in sorted(zone.members)],
            suggestion="сделайте вызов разрешимым (литеральный путь)"))
    for anchor, where, token in g.broken:
        findings.append(Finding(
            rule="usage-graph/broken-root", category="bug", severity="high",
            confidence=Confidence.LIKELY, owner_repo=repo, anchor=anchor,
            locations=[where], text_key=make_text_key(token),
            evidence=[{"kind": "missing", "detail": token}]))
    findings += _stale_roots(g, repo, ages, now)
    return findings


def _stale_roots(g: Graph, repo: str, ages: Callable[[str], float | None],
                 now: float) -> list[Finding]:
    out: list[Finding] = []
    texts = g.root_texts
    for anchor, node in sorted(g.nodes.items()):
        if not node.root or node.kind is NodeKind.FILE:
            continue
        ts = ages(node.path)
        if ts is None or (now - ts) / DAY <= ROOT_STALE_DAYS:
            continue
        name = re.escape(node.name)
        mentioned = any(re.search(rf"\b{name}\b", t) for p, t in texts.items()
                        if p != node.path)
        if mentioned:
            continue
        out.append(Finding(
            rule="usage-graph/root-stale", category="dead", severity="low",
            confidence=Confidence.CANDIDATE, owner_repo=repo, anchor=anchor,
            locations=[Location(node.path, 1)],
            evidence=[{"kind": "age-days", "detail": str(int((now - ts) / DAY))}]))
    return out
```

`selfcheck/graph/probe.py`:

```python
"""usage-graph probe: repo graph + isolated canary graph (spec §3.2)."""

from __future__ import annotations

from selfcheck.corpus import last_commit_ts
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import Surface, classify
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec
from selfcheck.roles import Role, role_of

CANARY_DIR = ".selfcheck-canary/usage-graph/"


def _analyze(ctx: ProbeCtx) -> ParseResult:
    target = ctx.target

    def role(path: str) -> Role:
        return role_of(path, target.roles)

    def ages(path: str) -> float | None:
        ts = last_commit_ts(target.source, path)
        return float(ts) if ts is not None else None

    graph = build_graph(list(target.corpus), target.copy, role,
                        repo_name=target.name, sched_dir=target.sched_dir)
    surface = Surface(target.fleet,
                      str(target.sched_dir) if target.sched_dir else None,
                      graph.sched_plists)
    findings = classify(graph, repo=target.name, surface=surface, ages=ages,
                        now=target.now)
    canary_files = [p for p in ctx.inputs if p.startswith(CANARY_DIR)]
    canary_graph = build_graph(canary_files, target.copy, lambda p: Role.SOURCE,
                               repo_name=target.name, sched_dir=None)
    findings += classify(canary_graph, repo=target.name, surface=surface,
                         ages=lambda p: None, now=target.now)
    skipped = [e.split(":", 1)[0] for e in graph.errors]
    return ParseResult(findings, processed=len(target.corpus), skipped=skipped,
                       diagnostics=graph.errors,
                       extra={"surface": surface.__dict__,
                              "zones": len(graph.zones)})


USAGE_GRAPH = ProbeSpec(
    name="usage-graph", languages=frozenset({"any"}), input_mode="files",
    select=lambda t: t.corpus,
    canary=Canary(f"{CANARY_DIR}orphan_canary.py",
                  'if __name__ == "__main__":\n    print("canary")\n',
                  "usage-graph/dead.file"),
    analyze=_analyze,
)
```

Замечание: канареечный граф строится с ролью `SOURCE` для файлов канарейки, и
потому она — `orphan` без истории → `dead.file` уровня `likely`; ядро
вычитает её находки по роли `canary` пути.

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_graph_classify.py tests/selfcheck/test_graph_build.py tests/selfcheck/test_graph_resolver.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/graph tests/selfcheck/test_graph_classify.py
git commit -m "feat(selfcheck): классификация dead по матрице D1–D12, корни, проба usage-graph"
```

---
### Task 11: дубли по смыслу — `ast-dup` и `cli-overlap` (§3.3)

**Files:**
- Create: `selfcheck/dups.py`
- Test: `tests/selfcheck/test_dups.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 5; `make_recipes` (Task 8).
- Produces: `FuncHash(path, qualname, line, exact, structural, literals)`,
  `function_hashes(source: str, path: str) -> list[FuncHash]`,
  `dup_findings(hashes: list[FuncHash], repo: str) -> list[Finding]`,
  `parser_flags(source: str, path: str) -> list[tuple[str, int, frozenset[str]]]`,
  `AST_DUP`, `CLI_OVERLAP: ProbeSpec`.

- [ ] **Step 1: падающие тесты**

`tests/selfcheck/test_dups.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.dups import AST_DUP, CLI_OVERLAP, dup_findings, function_hashes
from selfcheck.env import EnvInfo
from selfcheck.model import Confidence
from selfcheck.probes.base import ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from tests.selfcheck.helpers import make_repo

BODY = "".join(f"    {n} = {n}_src + {i}\n" for i, n in enumerate("abcdefg"))


def func(name: str, threshold: int = 5, var: str = "x") -> str:
    body = BODY.replace("a_src", var)
    return (f"def {name}({var}, a_src=0, b_src=0, c_src=0, d_src=0, e_src=0, "
            f"f_src=0, g_src=0):\n    \"\"\"doc\"\"\"\n{body}"
            f"    return a if a > {threshold} else b\n")


def test_exact_ignores_names_and_docstrings() -> None:
    one = function_hashes(func("one", var="x"), "a.py")[0]
    two = function_hashes(func("two", var="y").replace('"""doc"""', '"""other"""'),
                          "b.py")[0]
    assert one.exact == two.exact


def test_structural_only_when_literals_differ() -> None:
    hashes = (function_hashes(func("one", 5), "a.py")
              + function_hashes(func("two", 9), "b.py"))
    findings = dup_findings(hashes, "repo")
    assert [(f.rule, f.confidence) for f in findings] == [
        ("ast-dup/structural", Confidence.CANDIDATE)]
    assert any("9" in e["detail"] for e in findings[0].evidence)


def test_exact_group_three_members_one_finding() -> None:
    hashes = [h for i in range(3) for h in function_hashes(func(f"f{i}"), f"m{i}.py")]
    findings = dup_findings(hashes, "repo")
    assert [(f.rule, f.occurrences) for f in findings] == [("ast-dup/exact", 3)]


def test_short_and_different_functions_ignored() -> None:
    short = "def s(x):\n    return x\n"
    other = func("o").replace("+", "-")
    hashes = (function_hashes(short, "a.py") + function_hashes(short, "b.py")
              + function_hashes(func("f"), "c.py") + function_hashes(other, "d.py"))
    assert dup_findings(hashes, "repo") == []


def run(spec: ProbeSpec, tmp: Path, files: dict[str, str]):
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=time.time())
    try:
        return run_probe(spec, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_ast_dup_probe(tmp_path: Path) -> None:
    res = run(AST_DUP, tmp_path, {"a.py": func("one"), "b.py": func("two")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert [f.rule for f in res.findings] == ["ast-dup/exact"]


def test_cli_overlap_parsers_and_make(tmp_path: Path) -> None:
    parser = ("import argparse\n\ndef {n}():\n    p = argparse.ArgumentParser()\n"
              "    p.add_argument('--repo')\n    p.add_argument('--owner')\n"
              "    p.add_argument('--number')\n    p.add_argument('{extra}')\n"
              "    return p\n")
    res = run(CLI_OVERLAP, tmp_path, {
        "a.py": parser.format(n="a", extra="--mode"),
        "b.py": parser.format(n="b", extra="--mode"),
        "c.py": parser.format(n="c", extra="--other").replace("--owner", "--x"),
        "Makefile": ("one: ; @python3 ./a.py $(ARGS)\ntwo: ; @python3 ./a.py $(X)\n"
                     "three: ; @python3 ./b.py\n"),
    })
    assert res.status is ProbeStatus.OK
    anchors = sorted(f.anchor.split(":")[1] for f in res.findings)
    assert anchors == ["cli", "make"]
    make = next(f for f in res.findings if f.anchor.startswith("dup:make:"))
    assert make.occurrences == 2
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_dups.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.dups'`.

- [ ] **Step 3: реализация**

`selfcheck/dups.py`:

```python
"""Semantic duplicates: normalised AST hashes and CLI overlap (spec §3.3)."""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from selfcheck.graph.build import make_recipes
from selfcheck.model import Confidence, Finding, Location
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec
from selfcheck.probes.common import source_text
from selfcheck.roles import Role, role_of

MIN_LINES = 8
OVERLAP = 0.8


@dataclass(frozen=True)
class FuncHash:
    path: str
    qualname: str
    line: int
    exact: str
    structural: str
    literals: tuple[str, ...]


class _Normalizer(ast.NodeTransformer):
    def __init__(self, *, erase_literals: bool) -> None:
        self.names: dict[str, str] = {}
        self.erase = erase_literals

    def _rename(self, name: str) -> str:
        return self.names.setdefault(name, f"v{len(self.names)}")

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._rename(node.arg)
        node.annotation = None
        return node

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if isinstance(node.ctx, ast.Store) or node.id in self.names:
            node.id = self._rename(node.id)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        value = node.value
        if self.erase and isinstance(value, str | int | float) and not isinstance(
                value, bool):
            node.value = "S" if isinstance(value, str) else 0
        return node


def _digest(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, erase: bool) -> str:
    clone = copy.deepcopy(fn)
    clone.name, clone.decorator_list, clone.returns = "_", [], None
    body = clone.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(
            body[0].value.value, str):
        clone.body = body[1:] or [ast.Pass()]
    normalized = _Normalizer(erase_literals=erase).visit(clone)
    return hashlib.sha1(ast.dump(normalized).encode()).hexdigest()


def _functions(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if not isinstance(child, ast.ClassDef):
                    out.append((qual, child))
                visit(child, qual)

    visit(tree, "")
    return out


def function_hashes(source: str, path: str) -> list[FuncHash]:
    """Hashes of functions of at least MIN_LINES lines."""
    tree = ast.parse(source)
    result = []
    for qual, fn in _functions(tree):
        if (fn.end_lineno or fn.lineno) - fn.lineno + 1 < MIN_LINES:
            continue
        literals = tuple(sorted({repr(n.value) for n in ast.walk(fn)
                                 if isinstance(n, ast.Constant)
                                 and isinstance(n.value, str | int | float)}))
        result.append(FuncHash(path, qual, fn.lineno, _digest(fn, erase=False),
                               _digest(fn, erase=True), literals))
    return result


def _finding(rule: str, kind: str, key: str, members: list[FuncHash], repo: str,
             confidence: Confidence, evidence: list[dict[str, str]]) -> Finding:
    ordered = sorted(members, key=lambda m: (m.path, m.line))
    return Finding(
        rule=rule, category="duplicate", severity="medium", confidence=confidence,
        owner_repo=repo, anchor=f"dup:{kind}:{key[:16]}",
        locations=[Location(m.path, m.line) for m in ordered],
        related=[{"owner_repo": repo, "path": m.path, "line": m.line,
                  "qualname": m.qualname} for m in ordered],
        evidence=evidence, suggestion="вынести в общую функцию или модуль")


def dup_findings(hashes: list[FuncHash], repo: str) -> list[Finding]:
    """exact groups → confirmed; structural-only groups → candidate."""
    by_exact: dict[str, list[FuncHash]] = defaultdict(list)
    by_struct: dict[str, list[FuncHash]] = defaultdict(list)
    for item in hashes:
        by_exact[item.exact].append(item)
        by_struct[item.structural].append(item)
    findings = [_finding("ast-dup/exact", "exact", key, group, repo,
                         Confidence.CONFIRMED, [])
                for key, group in by_exact.items() if len(group) >= 2]
    for key, group in by_struct.items():
        if len(group) < 2 or len({g.exact for g in group}) == 1:
            continue
        evidence = [{"kind": "literals", "detail": f"{g.path}:{g.line}: {list(g.literals)}"}
                    for g in group]
        findings.append(_finding("ast-dup/structural", "structural", key, group, repo,
                                 Confidence.CANDIDATE, evidence))
    return findings


def _py_sets(ctx: ProbeCtx, prefix: str) -> tuple[list[str], list[str]]:
    canary = [p for p in ctx.inputs if p.startswith(prefix)]
    repo = [p for p in ctx.target.corpus if p.endswith(".py")
            and role_of(p, ctx.target.roles) is Role.SOURCE]
    return repo, canary


def _ast_dup(ctx: ProbeCtx) -> ParseResult:
    repo_files, canary_files = _py_sets(ctx, ".selfcheck-canary/ast-dup/")
    result = ParseResult([], processed=0)
    for files in (repo_files, canary_files):
        hashes: list[FuncHash] = []
        for rel in files:
            try:
                hashes += function_hashes(source_text(ctx, rel), rel)
                result.processed = (result.processed or 0) + 1
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
        result.findings += dup_findings(hashes, ctx.target.name)
    return result


_DUP_CANARY_BODY = "".join(f"    r{i} = seed * {i} + {i}\n" for i in range(8))
_DUP_CANARY = (f"def selfcheck_left(seed):\n{_DUP_CANARY_BODY}    return seed\n\n\n"
               f"def selfcheck_right(seed):\n{_DUP_CANARY_BODY}    return seed\n")

AST_DUP = ProbeSpec(
    name="ast-dup", languages=frozenset({"python"}), input_mode="files",
    select=lambda t: tuple(p for p in t.corpus if p.endswith(".py")),
    canary=Canary(".selfcheck-canary/ast-dup/canary.py", _DUP_CANARY, "ast-dup/exact"),
    coverage="reported", analyze=_ast_dup,
)


# ---- cli-overlap ----------------------------------------------------------------

def parser_flags(source: str, path: str) -> list[tuple[str, int, frozenset[str]]]:
    """Per function: long flags added with ``add_argument``."""
    tree = ast.parse(source)
    out = []
    for qual, fn in _functions(tree):
        flags = {c.args[0].value for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Attribute) and c.func.attr == "add_argument"
                 and c.args and isinstance(c.args[0], ast.Constant)
                 and isinstance(c.args[0].value, str) and c.args[0].value.startswith("--")}
        if len(flags) >= 2:
            out.append((f"{path}::{qual}", fn.lineno, frozenset(flags)))
    return out


_MAKE_VAR = re.compile(r"\$\([^)]*\)")


def _cli_overlap_files(ctx: ProbeCtx, files: list[str],
                       result: ParseResult) -> None:
    parsers: list[tuple[str, int, frozenset[str]]] = []
    recipes: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for rel in files:
        text = source_text(ctx, rel)
        if rel.endswith(".py"):
            try:
                parsers += parser_flags(text, rel)
            except SyntaxError as exc:
                result.diagnostics.append(f"{rel}: {exc.msg}")
                result.skipped.append(rel)
        elif rel.rsplit("/", 1)[-1] == "Makefile" or rel.endswith(".mk"):
            for target, lines in make_recipes(text).items():
                if lines:
                    norm = " ; ".join(" ".join(_MAKE_VAR.sub("$V", c).split())
                                      for _, c in lines)
                    recipes[norm].append((rel, target, lines[0][0]))
    repo = ctx.target.name
    for i, (a_id, a_line, a) in enumerate(parsers):
        for b_id, b_line, b in parsers[i + 1:]:
            common = a & b
            if len(common) >= 2 and len(common) / len(a | b) >= OVERLAP:
                key = hashlib.sha1(" ".join(sorted(common)).encode()).hexdigest()
                members = [(a_id, a_line), (b_id, b_line)]
                result.findings.append(Finding(
                    rule="cli-overlap/argparse", category="duplicate", severity="low",
                    confidence=Confidence.CANDIDATE, owner_repo=repo,
                    anchor=f"dup:cli:{key[:16]}",
                    locations=[Location(m.split("::")[0], n) for m, n in members],
                    related=[{"owner_repo": repo, "path": m.split("::")[0], "line": n,
                              "qualname": m.split("::")[1]} for m, n in members],
                    evidence=[{"kind": "common-flags", "detail": " ".join(sorted(common))}]))
    for norm, users in recipes.items():
        if len(users) >= 2:
            key = hashlib.sha1(norm.encode()).hexdigest()
            result.findings.append(Finding(
                rule="cli-overlap/make-recipe", category="duplicate", severity="low",
                confidence=Confidence.CANDIDATE, owner_repo=repo,
                anchor=f"dup:make:{key[:16]}",
                locations=[Location(p, n) for p, _, n in users],
                related=[{"owner_repo": repo, "path": p, "line": n, "target": t}
                         for p, t, n in users],
                evidence=[{"kind": "recipe", "detail": norm}]))


def _cli_overlap(ctx: ProbeCtx) -> ParseResult:
    result = ParseResult([])
    prefix = ".selfcheck-canary/cli-overlap/"
    repo_files = [p for p in ctx.target.corpus
                  if role_of(p, ctx.target.roles) is Role.SOURCE]
    _cli_overlap_files(ctx, repo_files, result)
    _cli_overlap_files(ctx, [p for p in ctx.inputs if p.startswith(prefix)], result)
    return result


_CLI_CANARY = "".join(
    f"import argparse\n\n\ndef selfcheck_{n}():\n    p = argparse.ArgumentParser()\n"
    "    p.add_argument('--alpha')\n    p.add_argument('--beta')\n"
    "    p.add_argument('--gamma')\n    return p\n\n\n" for n in ("left", "right"))

CLI_OVERLAP = ProbeSpec(
    name="cli-overlap", languages=frozenset({"any"}), input_mode="files",
    select=lambda t: t.corpus,
    canary=Canary(".selfcheck-canary/cli-overlap/canary.py", _CLI_CANARY,
                  "cli-overlap/argparse"),
    analyze=_cli_overlap,
)
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_dups.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/dups.py tests/selfcheck/test_dups.py
git commit -m "feat(selfcheck): ast-dup и cli-overlap (§3.3)"
```

---

### Task 12: LLM-вызовы — `llm-sites` (§3.4)

**Files:**
- Create: `selfcheck/rules/llm.yml`, `selfcheck/llm.py`
- Test: `tests/selfcheck/test_llm.py`

**Interfaces:**
- Consumes: Tasks 1, 5, 6 (`python_files`), 7 (`shell_files`).
- Produces: `RULES_PATH: Path`, `python_features(source: str, line: int) -> tuple[list[str], bool]`
  (признаки, исключение), `LLM_SITES: ProbeSpec`; `ParseResult.extra["inventory"]` —
  список `{"path", "line", "mechanism", "rule", "candidate", "features"}`.

- [ ] **Step 1: падающие тесты**

`tests/selfcheck/test_llm.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, python_features
from selfcheck.probes.base import ProbeStatus, RepoTarget, canary_files, run_probe
from tests.selfcheck.helpers import make_repo, require_tool

CLASSIFY = """import json
import subprocess


def classify(items):
    out = []
    for item in items:
        raw = subprocess.run(["claude", "-p", f"label {item}"],
                             capture_output=True, text=True).stdout
        out.append(json.loads(raw)["label"])
    return out
"""
REVIEW = """import subprocess


def review(diff):
    prompt = f"Review this diff: {diff}"
    return subprocess.run(["codex", "exec", prompt], capture_output=True).stdout
"""
HARNESS = '#!/bin/sh\nclaude -p "$1" --output-format json --json-schema s.json\n'


def test_features_and_exclusion() -> None:
    feats, excluded = python_features(CLASSIFY, 8)
    assert {"fixed-schema", "loop"} <= set(feats) and not excluded
    assert python_features(REVIEW, 6)[1] is True


def test_probe_inventory_and_candidates(tmp_path: Path) -> None:
    require_tool("semgrep")
    repo = make_repo(tmp_path / "repo", {"c.py": CLASSIFY, "r.py": REVIEW,
                                         "harness": HARNESS})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=time.time())
    try:
        res = run_probe(LLM_SITES, target, tmp_path / "run" / "work")
    finally:
        release(copy)
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert sorted(f.anchor for f in res.findings) == ["llm:c.py::classify",
                                                      "llm:harness"]
    inventory = {(i["path"], i["candidate"]) for i in res.extra["inventory"]}
    assert {("c.py", True), ("r.py", False), ("harness", True)} <= inventory
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_llm.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.llm'`.

- [ ] **Step 3: правила semgrep**

`selfcheck/rules/llm.yml`:

```yaml
rules:
  - id: cli-python
    languages: [python]
    severity: INFO
    message: harness CLI launch (mechanism A)
    patterns:
      - pattern-either:
          - pattern: subprocess.$F([$BIN, ...], ...)
          - pattern: subprocess.$F(($BIN, ...), ...)
      - metavariable-regex:
          metavariable: $BIN
          regex: ^["'](claude|codex|opencode|aider|pi|qwen|ollama|llama-cli|copilot)["']$
  - id: cli-shell
    languages: [bash]
    severity: INFO
    message: harness CLI launch (mechanism A)
    pattern-regex: (?m)^\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:exec\s+|command\s+)?(?:claude|codex|opencode|aider|qwen|ollama|llama-cli|copilot)\s
  - id: sdk-python
    languages: [python]
    severity: INFO
    message: LLM SDK call (mechanism B)
    pattern-either:
      - pattern: $C.messages.create(...)
      - pattern: $C.chat.completions.create(...)
      - pattern: $C.responses.create(...)
  - id: http-python
    languages: [python]
    severity: INFO
    message: LLM HTTP endpoint (mechanism C)
    pattern-regex: /v1/messages|/v1/chat/completions|/api/generate|/completion\b
```

- [ ] **Step 4: адаптер и эвристики**

`selfcheck/llm.py`:

```python
"""llm-sites: LLM call sites and replaceability heuristics (spec §3.4)."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

from selfcheck.anchors import python_anchor
from selfcheck.model import Confidence, Finding, Location, make_text_key
from selfcheck.probes.base import Canary, ParseResult, ProbeCtx, ProbeSpec, RepoTarget
from selfcheck.probes.common import copy_paths, rel_path, source_text
from selfcheck.probes.other_tools import shell_files
from selfcheck.probes.python_tools import python_files

RULES_PATH = Path(__file__).parent / "rules" / "llm.yml"
_MECHANISM = {"cli-python": "A", "cli-shell": "A", "sdk-python": "B", "http-python": "C"}
_EXCLUDE = re.compile(r"diff|read_text\(|\.read\(\)|git (?:show|diff)")
_FIXED_KEY = re.compile(r"\[\s*['\"]\w+['\"]\s*\]")
_HUMAN = re.compile(r"print\(|\.write\(|comment|post")


def _enclosing(tree: ast.AST, line: int) -> ast.AST:
    best: ast.AST = tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
                node.lineno <= line <= (node.end_lineno or node.lineno)):
            if best is tree or node.lineno >= getattr(best, "lineno", 0):
                best = node
    return best


def _in_loop(func: ast.AST, line: int) -> bool:
    loops = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp,
             ast.DictComp, ast.GeneratorExp)
    return any(isinstance(n, loops) and n.lineno <= line <= (n.end_lineno or n.lineno)
               for n in ast.walk(func) if hasattr(n, "lineno"))


def python_features(source: str, line: int) -> tuple[list[str], bool]:
    """Heuristic features of a Python call site and the exclusion flag."""
    tree = ast.parse(source)
    func = _enclosing(tree, line)
    text = ast.get_source_segment(source, func) or source if func is not tree else source
    features = []
    if "json.loads" in text and _FIXED_KEY.search(text) or "--json-schema" in text:
        features.append("fixed-schema")
    if _in_loop(func, line):
        features.append("loop")
    if re.search(r"f[\"'][^\"']*\{\w+\}", text) or ".format(" in text:
        features.append("template-prompt")
    if not _HUMAN.search(text):
        features.append("no-human-text")
    return features, bool(_EXCLUDE.search(text))


def _shell_features(text: str) -> tuple[list[str], bool]:
    features = ["fixed-schema"] if "--json-schema" in text or "jq " in text else []
    return features, "diff" in text


def _select(target: RepoTarget) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*python_files(target), *shell_files(target))))


def _argv(ctx: ProbeCtx) -> list[str]:
    return ["scan", "--config", str(RULES_PATH), "--json", "--metrics", "off",
            "--disable-version-check", "--no-git-ignore", "--scan-unknown-extensions",
            "--quiet", *copy_paths(ctx)]


def _parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    result = ParseResult([], processed=len(data.get("paths", {}).get("scanned", [])))
    for err in data.get("errors", []):
        path = err.get("path")
        result.diagnostics.append(f"{path}: {err.get('message', err.get('type'))}")
        if path:
            result.skipped.append(rel_path(ctx, path))
    inventory = []
    for hit in data["results"]:
        rule = hit["check_id"].rsplit(".", 1)[-1]
        rel, line = rel_path(ctx, hit["path"]), hit["start"]["line"]
        text = source_text(ctx, rel)
        if rel.endswith(".py"):
            features, excluded = python_features(text, line)
            anchor = python_anchor(text, rel, line).replace("func:", "llm:", 1)
            anchor = anchor.replace("file:", "llm:", 1)
        else:
            features, excluded = _shell_features(text)
            anchor = f"llm:{rel}"
        strong = {"fixed-schema", "loop"} & set(features)
        candidate = bool(strong) and not excluded
        inventory.append({"path": rel, "line": line, "mechanism": _MECHANISM.get(rule, "?"),
                          "rule": rule, "candidate": candidate, "features": features})
        if candidate:
            line_text = text.splitlines()[line - 1] if text else ""
            result.findings.append(Finding(
                rule="llm-sites/replaceable", category="llm-replaceable",
                severity="low", confidence=Confidence.CANDIDATE,
                owner_repo=ctx.target.name, anchor=anchor,
                locations=[Location(rel, line)], text_key=make_text_key(line_text),
                evidence=[{"kind": "feature", "detail": f} for f in features],
                suggestion="скрипт / правила / дерево решений / малая модель"))
    result.extra["inventory"] = [i for i in inventory
                                 if not i["path"].startswith(".selfcheck-canary/")]
    return result


_CANARY = """import json
import subprocess


def selfcheck_classify(items):
    labels = []
    for item in items:
        raw = subprocess.run(["claude", "-p", f"label {item}"],
                             capture_output=True, text=True).stdout
        labels.append(json.loads(raw)["label"])
    return labels
"""

LLM_SITES = ProbeSpec(
    name="llm-sites", languages=frozenset({"any"}), input_mode="files",
    select=_select,
    canary=Canary(".selfcheck-canary/llm-sites/canary.py", _CANARY,
                  "llm-sites/replaceable"),
    coverage="reported", binary="semgrep", version_range=((1, 178), (2, 0)),
    normal_codes=frozenset({0}), argv=_argv, parse=_parse,
)
```

Замечание для исполнителя: `rules/llm.yml` должен попасть в пакет — пакет
запускается из рабочего дерева devtools (`python -m selfcheck`), сборки
wheel нет, поэтому отдельной настройки package-data не нужно.

- [ ] **Step 5: тесты проходят**

Run: `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck/test_llm.py -q`
Expected: PASS.

- [ ] **Step 6: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/llm.py selfcheck/rules tests/selfcheck/test_llm.py
git commit -m "feat(selfcheck): llm-sites — инвентарь и кандидаты на замену LLM (§3.4)"
```

---

### Task 13: дельта — судьбы файлов и статусы (§4.3)

**Files:**
- Create: `selfcheck/delta.py`
- Test: `tests/selfcheck/test_delta.py`

**Interfaces:**
- Consumes: `ProbeResult`, `ProbeStatus` (Task 5).
- Produces:
  - `RunSnapshot(run_id: str, scope: list[str], materialized: list[str],
    probe_keys: dict[str, str | None], corpus: dict[str, list[str]],
    sources: dict[str, str], findings: dict[str, dict])` с `to_json()` / `from_json(d)`;
  - `comparability_key(result: ProbeResult, *, env_mode: str, surface: dict | None,
    run_dir: str) -> str | None`;
  - `Fate` (`checked`, `deleted`, `excluded`, `out-of-scope`, `unverified`);
  - `fate(cur: RunSnapshot, base: RunSnapshot, repo: str, path: str, probe: str) -> Fate`;
  - `compute_delta(base: RunSnapshot | None, cur: RunSnapshot) ->
    tuple[dict[str, str], list[dict[str, str]]]` (статусы текущих; ушедшие).

- [ ] **Step 1: падающие тесты (судьбы, Δ1–Δ4, именованные контрпримеры)**

`tests/selfcheck/test_delta.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.delta import Fate, RunSnapshot, compute_delta, fate


def finding(fid: str, anchor: str, *, owner: str = "devtools", paths=("a.py",),
            related=None, probe: str = "ruff") -> dict:
    return {"id": fid, "probe": probe, "anchor": anchor, "owner_repo": owner,
            "occurrences": len(paths),
            "locations": [{"path": p, "line": 1} for p in paths],
            "related": related or []}


def snap(tmp: Path, *, scope=("devtools",), corpus=None, keys=None,
         findings=(), missing=()) -> RunSnapshot:
    corpus = corpus if corpus is not None else {"devtools": ["a.py"]}
    sources = {}
    for repo, files in corpus.items():
        root = tmp / repo
        root.mkdir(exist_ok=True)
        for rel in files:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("")
        sources[repo] = str(root)
    return RunSnapshot(
        run_id="r", scope=list(scope),
        materialized=[r for r in scope if r not in missing],
        probe_keys=keys if keys is not None else {
            f"{p}@{r}": "k" for r in scope for p in ("ruff", "ast-dup")},
        corpus={r: f for r, f in corpus.items() if r in scope},
        sources=sources, findings={f["id"]: f for f in findings})


def test_new_persisting_changed(tmp_path: Path) -> None:
    base = snap(tmp_path, findings=[finding("a", "file:a.py"),
                                    finding("b", "file:a.py", paths=("a.py",))])
    cur = snap(tmp_path, findings=[finding("a", "file:a.py"),
                                   finding("b", "file:a.py", paths=("a.py", "b.py")),
                                   finding("c", "file:a.py")])
    statuses, gone = compute_delta(base, cur)
    assert statuses == {"a": "persisting", "b": "changed", "c": "new"}
    assert gone == []


def test_line_shift_is_not_change(tmp_path: Path) -> None:
    moved = finding("a", "file:a.py")
    moved["locations"][0]["line"] = 40
    statuses, _ = compute_delta(snap(tmp_path, findings=[finding("a", "file:a.py")]),
                                snap(tmp_path, findings=[moved]))
    assert statuses == {"a": "persisting"}


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("checked", "resolved"),
        ("deleted", "resolved: file-removed"),
        ("excluded", "not-rechecked"),
        ("out-of-scope", "not-rechecked"),
        ("key-changed", "not-rechecked"),
        ("probe-failed", "not-rechecked"),
        ("repo-missing", "not-rechecked"),
    ],
)
def test_single_anchor_fates(tmp_path: Path, setup: str, expected: str) -> None:
    base = snap(tmp_path, findings=[finding("x", "func:a.py::f")])
    corpus = {"devtools": ["a.py"]}
    kwargs: dict = {}
    if setup == "deleted":
        corpus = {"devtools": []}
    if setup == "excluded":
        corpus = {"devtools": []}
    if setup == "out-of-scope":
        kwargs["scope"] = ("maestro",)
        corpus = {"maestro": []}
    if setup == "key-changed":
        kwargs["keys"] = {"ruff@devtools": "other"}
    if setup == "probe-failed":
        kwargs["keys"] = {"ruff@devtools": None}
    if setup == "repo-missing":
        kwargs["missing"] = ("devtools",)
    cur = snap(tmp_path, corpus=corpus, **kwargs)
    if setup == "deleted":
        (tmp_path / "devtools" / "a.py").unlink()
    _, gone = compute_delta(base, cur)
    assert gone == [{"id": "x", "status": expected, "anchor": "func:a.py::f"}]


def dup(owner_paths: list[tuple[str, str]]) -> dict:
    related = [{"owner_repo": o, "path": p, "line": 1} for o, p in owner_paths]
    return finding("d", "dup:exact:abc", probe="ast-dup",
                   paths=[p for _, p in owner_paths], related=related)


@pytest.mark.parametrize(
    ("row", "fates", "expected"),
    [
        ("Δ1", ("checked", "checked"), "resolved"),
        ("Δ2", ("deleted", "deleted"), "resolved: file-removed"),
        ("Δ3", ("checked", "deleted"), "resolved"),
        ("Δ4", ("checked", "out-of-scope"), "not-rechecked"),
        ("Δ4", ("checked", "excluded"), "not-rechecked"),
        ("Δ4", ("unverified", "checked"), "not-rechecked"),
    ],
)
def test_dup_fates(tmp_path: Path, row: str, fates: tuple[str, str],
                   expected: str) -> None:
    del row
    repos = ["devtools", "maestro"]
    members = [("devtools", "a.py"), ("maestro", "m.py")]
    base = snap(tmp_path, scope=repos,
                corpus={"devtools": ["a.py"], "maestro": ["m.py"]},
                findings=[dup(members)])
    scope, corpus, keys = list(repos), {"devtools": ["a.py"], "maestro": ["m.py"]}, {}
    for (repo, path), f in zip(members, fates, strict=True):
        if f in ("deleted", "excluded"):
            corpus[repo] = []
        if f == "out-of-scope":
            scope.remove(repo)
        if f == "unverified":
            keys[f"ast-dup@{repo}"] = None
    full_keys = {f"ast-dup@{r}": "k" for r in scope} | {
        k: v for k, v in keys.items() if k.split("@")[1] in scope}
    cur = snap(tmp_path, scope=scope, corpus={r: corpus[r] for r in scope},
               keys=full_keys)
    for (repo, path), f in zip(members, fates, strict=True):
        if f == "deleted":
            (tmp_path / repo / path).unlink()
    _, gone = compute_delta(base, cur)
    assert gone[0]["status"] == expected


def test_fate_enum_values(tmp_path: Path) -> None:
    base = snap(tmp_path)
    assert fate(base, base, "devtools", "a.py", "ruff") is Fate.CHECKED
```

- [ ] **Step 2: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_delta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.delta'`.

- [ ] **Step 3: реализация**

`selfcheck/delta.py`:

```python
"""Delta against a baseline run: fates and statuses (spec §4.3)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from selfcheck.probes.base import ProbeResult, ProbeStatus


@dataclass
class RunSnapshot:
    """What a later run needs to judge disappearances."""

    run_id: str
    scope: list[str]
    materialized: list[str]
    probe_keys: dict[str, str | None]
    corpus: dict[str, list[str]]
    sources: dict[str, str]
    findings: dict[str, dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunSnapshot:
        return cls(**data)


def comparability_key(result: ProbeResult, *, env_mode: str,
                      surface: dict[str, Any] | None, run_dir: str) -> str | None:
    """Key of a probe run; None unless the probe is ok (spec §4.3)."""
    if result.status is not ProbeStatus.OK:
        return None
    options = [a for a in result.argv[1:] if not a.startswith(run_dir)]
    material = [result.probe, result.tool_version, result.config_hash, options,
                env_mode, surface]
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()


class Fate(StrEnum):
    CHECKED = "checked"
    DELETED = "deleted"
    EXCLUDED = "excluded"
    OUT_OF_SCOPE = "out-of-scope"
    UNVERIFIED = "unverified"


def fate(cur: RunSnapshot, base: RunSnapshot, repo: str, path: str,
         probe: str) -> Fate:
    """Fate of a baseline defining file / participant in the current run."""
    if repo not in cur.scope:
        return Fate.OUT_OF_SCOPE
    if repo not in cur.materialized:
        return Fate.UNVERIFIED
    in_corpus = path in cur.corpus.get(repo, [])
    source = cur.sources.get(repo)
    if not in_corpus and source and not (Path(source) / path).exists():
        return Fate.DELETED
    if not in_corpus:
        return Fate.EXCLUDED
    key = f"{probe}@{repo}"
    current = cur.probe_keys.get(key)
    if current is None or current != base.probe_keys.get(key):
        return Fate.UNVERIFIED
    return Fate.CHECKED


def _defining_path(anchor: str) -> str:
    body = anchor.split(":", 1)[1]
    return body.split("::", 1)[0].split("#", 1)[0]


def _changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    def paths(item: dict[str, Any]) -> set[str]:
        return {loc["path"] for loc in item["locations"]}

    def members(item: dict[str, Any]) -> set[tuple[str, str]]:
        return {(r["owner_repo"], r["path"]) for r in item.get("related", [])}

    return (old["occurrences"] != new["occurrences"] or paths(old) != paths(new)
            or members(old) != members(new))


def _gone_status(base: RunSnapshot, cur: RunSnapshot, item: dict[str, Any]) -> str:
    probe = item["probe"]
    if item["anchor"].startswith("dup:"):
        fates = [fate(cur, base, r["owner_repo"], r["path"], probe)
                 for r in item["related"]]
    else:
        fates = [fate(cur, base, item["owner_repo"], _defining_path(item["anchor"]),
                      probe)]
    if all(f is Fate.DELETED for f in fates):
        return "resolved: file-removed"
    if all(f in (Fate.CHECKED, Fate.DELETED) for f in fates):
        return "resolved"
    return "not-rechecked"


def compute_delta(base: RunSnapshot | None,
                  cur: RunSnapshot) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Statuses of current findings and of disappeared baseline findings."""
    if base is None:
        return {fid: "new" for fid in cur.findings}, []
    statuses: dict[str, str] = {}
    for fid, item in cur.findings.items():
        old = base.findings.get(fid)
        statuses[fid] = ("new" if old is None
                         else "changed" if _changed(old, item) else "persisting")
    gone = [{"id": fid, "status": _gone_status(base, cur, item),
             "anchor": item["anchor"]}
            for fid, item in base.findings.items() if fid not in cur.findings]
    return statuses, gone
```

- [ ] **Step 4: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_delta.py -q`
Expected: PASS.

- [ ] **Step 5: линт, типы, коммит**

```bash
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
git add selfcheck/delta.py tests/selfcheck/test_delta.py
git commit -m "feat(selfcheck): дельта — судьбы файлов, Δ1–Δ4, статусы (§4.3)"
```

---

### Task 14: реестр, отчёт, оркестратор, цели Makefile, CI, приёмка S1

**Files:**
- Create: `selfcheck/registry.py`, `selfcheck/report.py`, `selfcheck/run.py`,
  `selfcheck/__main__.py`, `selfcheck.toml`
- Modify: `Makefile`, `.github/workflows/ci.yml`, `CLAUDE.md`, `TODO.md`
- Test: `tests/selfcheck/test_registry.py`, `tests/selfcheck/test_run.py`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: `REGISTRY: tuple[ProbeSpec, ...]`;
  `new_run_dir(out_root: Path, now: datetime, token: Callable[[], str] = ...) -> tuple[str, Path]`,
  `find_baseline(out_root: Path, current: str) -> dict | None`,
  `write_report(run_dir: Path, doc: dict) -> None`, `render_markdown(doc: dict) -> str`;
  `exit_code(results: list[ProbeResult]) -> int`, `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: падающие тесты реестра**

`tests/selfcheck/test_registry.py`:

```python
from __future__ import annotations

from selfcheck.registry import REGISTRY
from selfcheck.roles import Role, role_of


def test_registry_invariants() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names))
    assert all(s.executes_target_code is False for s in REGISTRY)
    paths = [s.canary.relpath for s in REGISTRY]
    assert len(paths) == len(set(paths))
    assert all(role_of(p) is Role.CANARY for p in paths)
    assert {"ruff", "pyrefly", "vulture", "radon", "deptry", "shellcheck",
            "actionlint", "zizmor", "jscpd", "usage-graph", "ast-dup",
            "cli-overlap", "llm-sites"} == set(names)
```

- [ ] **Step 2: падающие тесты оркестратора и отчёта**

`tests/selfcheck/test_run.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from selfcheck.corpus import snapshot_hashes
from selfcheck.probes.base import ProbeResult, ProbeStatus
from selfcheck.report import new_run_dir
from selfcheck.run import exit_code, main
from tests.selfcheck.helpers import make_repo


def res(status: ProbeStatus) -> ProbeResult:
    return ProbeResult(probe="p", repo="r", status=status)


@pytest.mark.parametrize(
    ("statuses", "code"),
    [
        ([ProbeStatus.OK, ProbeStatus.SKIPPED], 0),
        ([ProbeStatus.OK, ProbeStatus.PARTIAL], 2),
        ([ProbeStatus.FAILED, ProbeStatus.UNAVAILABLE], 2),
        ([ProbeStatus.OK, ProbeStatus.UNAVAILABLE], 3),
    ],
)
def test_exit_codes(statuses, code) -> None:
    assert exit_code([res(s) for s in statuses]) == code


def test_run_dirs_unique_under_frozen_time(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    first, _ = new_run_dir(tmp_path, now)
    second, _ = new_run_dir(tmp_path, now)
    assert first != second and first.startswith("20260926T120000Z-")


def test_run_dir_collision_retries_then_gives_up(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    tokens = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    new_run_dir(tmp_path, now, token=lambda: "aaaaaa")
    run_id, _ = new_run_dir(tmp_path, now, token=lambda: next(tokens))
    assert run_id.endswith("bbbbbb")
    with pytest.raises(OSError):
        new_run_dir(tmp_path, now, token=lambda: "aaaaaa")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    make_repo(tmp_path / "devtools", {
        ".gitignore": "out/\n",
        "Makefile": 'help:\n\t@echo "make go"\ngo: ; @python3 ./live.py\n',
        "live.py": 'if __name__ == "__main__":\n    pass\n',
        "orphan.py": 'if __name__ == "__main__":\n    pass\n',
        "pyproject.toml": '[project]\nname = "d"\nversion = "0"\n',
    })
    (tmp_path / "m.toml").write_text('[tools.devtools]\ngit_dir = "devtools"\n')
    return tmp_path


def run_main(ws: Path, *extra: str) -> int:
    return main(["--workspace", str(ws), "--manifest", str(ws / "m.toml"),
                 "--out", str(ws / "out"), "--config", str(ws / "none.toml"),
                 "--probe", "usage-graph", "--probe", "ast-dup", *extra])


def test_end_to_end_report_and_delta(workspace: Path) -> None:
    before = snapshot_hashes(workspace / "devtools")
    assert run_main(workspace) == 0
    assert run_main(workspace) == 0
    assert snapshot_hashes(workspace / "devtools") == before   # spec §6.2
    runs = sorted((workspace / "out").iterdir(),
                  key=lambda p: (p / "report.json").stat().st_mtime_ns)
    assert len(runs) == 2
    doc = json.loads((runs[-1] / "report.json").read_text())
    dead = [f for f in doc["findings"] if f["rule"] == "usage-graph/dead.file"]
    assert [f["anchor"] for f in dead] == ["file:orphan.py"]
    assert doc["delta"]["statuses"][dead[0]["id"]] == "persisting"
    assert doc["run"]["manifest"]["entries_read"] == 1
    assert not (runs[-1] / "src" / "devtools").exists()   # copy released
    md = (runs[-1] / "report.md").read_text()
    assert "| usage-graph | devtools | ok |" in md


def test_bad_config_exit_4(workspace: Path) -> None:
    (workspace / "bad.toml").write_text("[[allow]]\nanchor = 'x'\n")
    code = main(["--workspace", str(workspace), "--manifest",
                 str(workspace / "m.toml"), "--out", str(workspace / "out"),
                 "--config", str(workspace / "bad.toml")])
    assert code == 4


def test_unknown_repo_exit_4(workspace: Path) -> None:
    assert run_main(workspace, "--repo", "nope") == 4
```

- [ ] **Step 3: тесты падают**

Run: `uv run --frozen pytest tests/selfcheck/test_registry.py tests/selfcheck/test_run.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.registry'`.

- [ ] **Step 4: реестр, отчёт, оркестратор**

`selfcheck/registry.py`:

```python
"""All S1 probes, in report order."""

from __future__ import annotations

from selfcheck.dups import AST_DUP, CLI_OVERLAP
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.llm import LLM_SITES
from selfcheck.probes.base import ProbeSpec
from selfcheck.probes.other_tools import OTHER_PROBES
from selfcheck.probes.python_tools import PYTHON_PROBES

REGISTRY: tuple[ProbeSpec, ...] = (
    *PYTHON_PROBES, *OTHER_PROBES, USAGE_GRAPH, AST_DUP, CLI_OVERLAP, LLM_SITES,
)
```

`selfcheck/report.py`:

```python
"""Run directory, baseline lookup, JSON and Markdown report (spec §1, §4)."""

from __future__ import annotations

import json
import secrets
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


def new_run_dir(out_root: Path, now: datetime,
                token: Callable[[], str] = lambda: secrets.token_hex(3)
                ) -> tuple[str, Path]:
    """Atomically create ``<out>/<YYYYMMDDTHHMMSSZ>-<hex6>``; never reuse."""
    out_root.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        run_id = f"{now:%Y%m%dT%H%M%SZ}-{token()}"
        path = out_root / run_id
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return run_id, path
    raise OSError(f"could not allocate a unique run directory in {out_root}")


def find_baseline(out_root: Path, current: str) -> dict[str, Any] | None:
    """Latest previous run with a report, by report write time.

    Two runs in the same second differ only by the random suffix, so the
    directory name does not order them.
    """
    reports = [r / "report.json" for r in out_root.iterdir()
               if r.name != current and (r / "report.json").is_file()]
    if not reports:
        return None
    latest = max(reports, key=lambda p: p.stat().st_mtime_ns)
    return json.loads(latest.read_text())


def write_report(run_dir: Path, doc: dict[str, Any]) -> None:
    """report.json + report.md."""
    (run_dir / "report.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    (run_dir / "report.md").write_text(render_markdown(doc))


def _probe_rows(doc: dict[str, Any]) -> list[str]:
    rows = ["| проба | репо | статус | причина | версия | код | канарейка | "
            "покрытие | находок |", "|---|---|---|---|---|---|---|---|---|"]
    for p in doc["probes"]:
        cov = p["coverage"]
        coverage = (f"{cov.get('mode', '-')}/{cov.get('input_mode', '-')} "
                    f"{cov.get('processed') if cov.get('processed') is not None else '-'}"
                    f"/{cov.get('passed', '-')}")
        rows.append(f"| {p['probe']} | {p['repo']} | {p['status']} | "
                    f"{p['reason'] or '—'} | {p['tool_version'] or '—'} | "
                    f"{p['exit_code'] if p['exit_code'] is not None else '—'} | "
                    f"{p['canary'] or '—'} | {coverage} | {p['findings']} |")
    return rows


def render_markdown(doc: dict[str, Any]) -> str:
    """Human report: probes first, then findings by category and rule."""
    run = doc["run"]
    lines = [f"# selfcheck {run['run_id']}", "",
             f"Репо: {', '.join(run['scope'])}; манифест: записей "
             f"{run['manifest']['entries_read']}, каталогов "
             f"{len(run['manifest']['repos'])}, отсутствуют "
             f"{', '.join(run['manifest']['missing']) or 'нет'}.",
             f"Окружение: {run['env']}. Поверхность: {run['surface']}.", "",
             "## Пробы", "", *_probe_rows(doc), ""]
    statuses = doc["delta"]["statuses"]
    lines += ["## Дельта", "",
              f"{dict(Counter(statuses.values()))}; ушедшие: "
              f"{dict(Counter(g['status'] for g in doc['delta']['gone']))}", ""]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for f in doc["findings"]:
        by_cat.setdefault(f["category"], []).append(f)
    for category, items in sorted(by_cat.items()):
        lines += [f"## {category} ({len(items)})", "",
                  "| правило | уверенность | якорь | мест | статус |",
                  "|---|---|---|---|---|"]
        for f in sorted(items, key=lambda x: (x["rule"], x["anchor"]))[:200]:
            lines.append(f"| {f['rule']} | {f['confidence']} | `{f['anchor']}` | "
                         f"{f['occurrences']} | {statuses.get(f['id'], '—')} |")
        lines.append("")
    lines += ["## Подавлено", "",
              f"allowlist: {len(doc['suppressed'])}; no-env: "
              f"{doc['suppressed_no_env']}", "",
              "## Инвентарь LLM-вызовов", "",
              "| путь | строка | механизм | кандидат | признаки |", "|---|---|---|---|---|"]
    for item in doc["inventory"]["llm"]:
        lines.append(f"| {item['path']} | {item['line']} | {item['mechanism']} | "
                     f"{'да' if item['candidate'] else 'нет'} | "
                     f"{', '.join(item['features'])} |")
    if run["warnings"]:
        lines += ["", "## Предупреждения", "", *[f"- {w}" for w in run["warnings"]]]
    return "\n".join(lines) + "\n"
```

`selfcheck/run.py`:

```python
"""Orchestrator and CLI: ``python -m selfcheck`` (spec §1, §4.3)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from selfcheck.config import ConfigError, apply_allowlist, load_config
from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.delta import RunSnapshot, comparability_key, compute_delta
from selfcheck.env import apply_env_policy, detect_env
from selfcheck.manifest import load_manifest
from selfcheck.model import Finding, aggregate
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from selfcheck.registry import REGISTRY
from selfcheck.report import find_baseline, new_run_dir, write_report
from selfcheck.roles import glob_match


def exit_code(results: list[ProbeResult]) -> int:
    """0 ok/skipped; 2 failed or partial; 3 only unavailable (spec §4.3)."""
    statuses = {r.status for r in results}
    if statuses & {ProbeStatus.FAILED, ProbeStatus.PARTIAL}:
        return 2
    if ProbeStatus.UNAVAILABLE in statuses:
        return 3
    return 0


def _args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="selfcheck", description=(
        "Static self-diagnosis: bugs, dead code, duplicates, LLM call sites."))
    parser.add_argument("--workspace", type=Path, default=Path(".."))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", action="append", default=[],
                        help="repo (git_dir) to scan; default devtools")
    parser.add_argument("--sched-dir", type=Path, default=None)
    parser.add_argument("--path", action="append", default=[],
                        help="glob limiting the corpus (dogfood)")
    parser.add_argument("--probe", action="append", default=[],
                        help="run only these probes")
    parser.add_argument("--out", type=Path, default=Path("out/selfcheck"))
    parser.add_argument("--config", type=Path, default=Path("selfcheck.toml"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run S1 and write the report; see exit codes in ``exit_code``."""
    args = _args(argv)
    try:
        config = load_config(args.config)
        manifest = load_manifest(args.manifest, args.workspace)
        wanted = args.repo or ["devtools"]
        known = {r.name: r for r in manifest.repos}
        unknown = [r for r in wanted if r not in known]
        if unknown:
            raise ConfigError(f"repos not in manifest or missing on disk: {unknown}")
        run_id, run_dir = new_run_dir(args.out, datetime.now(UTC))
    except (ConfigError, OSError) as exc:
        print(f"selfcheck: {exc}", file=sys.stderr)
        return 4
    specs = [s for s in REGISTRY if not args.probe or s.name in args.probe]
    results: list[ProbeResult] = []
    findings: list[Finding] = []
    no_env: dict[str, dict[str, int]] = {}
    inventory: list[dict[str, Any]] = []
    env_modes: dict[str, dict[str, Any]] = {}
    keys: dict[str, str | None] = {}
    corpora: dict[str, list[str]] = {}
    warnings: list[str] = []
    surface = {"fleet": "absent",
               "sched_dir": str(args.sched_dir) if args.sched_dir else None}
    for name in wanted:
        repo = known[name]
        try:
            corpus = [p for p in list_corpus(repo.path, config.corpus_exclude)
                      if not args.path or any(glob_match(g, p) for g in args.path)]
            env = detect_env(repo.path)
            copy = run_dir / "src" / name
            materialize(repo.path, corpus, copy, canary_files(specs))
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"selfcheck: materialize {name}: {exc}", file=sys.stderr)
            return 4
        target = RepoTarget(name, repo.path, copy, repo.languages, tuple(corpus), env,
                            config.roles, args.sched_dir, "absent", time.time())
        repo_results = [run_probe(s, target, run_dir / "work") for s in specs]
        results += repo_results
        repo_findings, counts = apply_env_policy(
            aggregate(f for r in repo_results for f in r.findings), env)
        findings += repo_findings
        no_env[name] = counts
        env_modes[name] = {"mode": env.mode, "stale": env.stale}
        corpora[name] = corpus
        for r in repo_results:
            inventory += r.extra.get("inventory", [])
            keys[f"{r.probe}@{name}"] = comparability_key(
                r, env_mode=env.mode,
                surface=surface if r.probe == "usage-graph" else None,
                run_dir=str(run_dir))
        warning = release(copy)
        if warning:
            warnings.append(warning)
    allow = apply_allowlist(findings, config, date.today())
    final = aggregate([*allow.kept, *allow.expired])
    snapshot = RunSnapshot(
        run_id=run_id, scope=list(wanted), materialized=list(wanted),
        probe_keys=keys, corpus=corpora,
        sources={n: str(known[n].path) for n in wanted},
        findings={f.id: f.to_json() for f in final})
    base_doc = find_baseline(args.out, run_id)
    base = RunSnapshot.from_json(base_doc["snapshot"]) if base_doc else None
    statuses, gone = compute_delta(base, snapshot)
    doc = {
        "schema": 1,
        "run": {"run_id": run_id, "scope": list(wanted), "surface": surface,
                "manifest": {"entries_read": manifest.entries_read,
                             "repos": [r.name for r in manifest.repos],
                             "missing": list(manifest.missing)},
                "config_sha1": config.sha1, "env": env_modes, "warnings": warnings},
        "probes": [{**r.to_json(), "key": keys.get(f"{r.probe}@{r.repo}")}
                   for r in results],
        "findings": [f.to_json() for f in final],
        "suppressed": [f.to_json() for f in allow.suppressed],
        "suppressed_no_env": no_env,
        "delta": {"statuses": statuses, "gone": gone},
        "inventory": {"llm": inventory},
        "snapshot": snapshot.to_json(),
    }
    try:
        write_report(run_dir, doc)
    except OSError as exc:
        print(f"selfcheck: write report: {exc}", file=sys.stderr)
        return 4
    print(run_dir / "report.md")
    return exit_code(results)
```

`selfcheck/__main__.py`:

```python
"""``python -m selfcheck``."""

import sys

from selfcheck.run import main

sys.exit(main())
```

- [ ] **Step 5: тесты проходят**

Run: `uv run --frozen pytest tests/selfcheck/test_registry.py tests/selfcheck/test_run.py -q`
Expected: PASS.

- [ ] **Step 6: allowlist devtools**

`selfcheck.toml`:

```toml
# selfcheck — allowlist и роли devtools (спека §1.4, §2.4).
# Каждая запись обязана иметь reason и until; истёкшая запись — находка.

[[allow]]
anchor = "file:issue_console.py"
reason = "неприкасаем по решению владельца: новый TUI — новый файл"
until = 2027-03-31
```

- [ ] **Step 7: цели Makefile**

В `Makefile`: добавить `selfcheck selfcheck-dogfood` в `.PHONY`, две строки в
`help` (после строки `make edge-check`, в том же стиле):

```make
	@echo "  make selfcheck ARGS='[--repo r] [--sched-dir ~/Library/LaunchAgents]' — самодиагностика: баги, мёртвое, дубли, LLM-вызовы (отчёт в out/selfcheck/)"
	@echo "  make selfcheck-dogfood — тесты selfcheck с обязательными инструментами + прогон на собственном пакете"
```

и цели в конце файла:

```make
selfcheck: ; @uv run --frozen --group selfcheck python -m selfcheck --workspace $(WORKSPACE) --manifest $(MANIFEST) $(ARGS)
selfcheck-dogfood: ; @SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q && uv run --frozen --group selfcheck python -m selfcheck --workspace $(WORKSPACE) --manifest $(MANIFEST) --repo devtools --path 'selfcheck/**' --path 'tests/selfcheck/**' --path Makefile --path pyproject.toml
```

Проверка: `make help | grep selfcheck` → две строки.

- [ ] **Step 8: шаг CI**

В `.github/workflows/ci.yml` после шага `make plan-check-selftest`:

```yaml
      - run: uv sync --frozen --group selfcheck
      - run: SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q
```

`npx` (jscpd) на `ubuntu-latest` предустановлен вместе с Node. Проверка
локально: `uv run --frozen --group selfcheck actionlint .github/workflows/ci.yml`
и `uv run --frozen --group selfcheck zizmor --offline .github/workflows/ci.yml`
— без новых находок.

- [ ] **Step 9: документация и план**

В `CLAUDE.md`, таблица «Инструменты», строка после `edge_check.py`:

```markdown
| `selfcheck/` | самодиагностика (`make selfcheck`): статические пробы (ruff, pyrefly, vulture, deptry, radon, shellcheck, actionlint, zizmor, jscpd, semgrep) + собственные (граф использования, ast-дубли, cli-overlap, LLM-вызовы); отчёт `out/selfcheck/<run_id>/report.{json,md}`, только советует. Спека `docs/superpowers/specs/2026-09-25-selfcheck-design.md`; пробы, исполняющие код цели, — отдельная спека S4 |
```

В `TODO.md` (эпик `eco.tooling`) — пять пунктов:

```markdown
- [ ] selfcheck S1: самодиагностика devtools (спека 2026-09-25-selfcheck-design, план 2026-09-26-selfcheck-s1) @owner:github:andrei-shtanakov @id:selfcheck-s1 @epic:eco.tooling
- [ ] selfcheck S2: `--fleet` — рёбра из всех репо манифеста, `confirmed` достижим @owner:github:andrei-shtanakov @id:selfcheck-s2 @blocked_by:todo://devtools/selfcheck-s1 @epic:eco.tooling
- [ ] selfcheck S3: все репо манифеста, межрепные ast-дубли, TS в llm-sites, cargo-machete @owner:github:andrei-shtanakov @id:selfcheck-s3 @blocked_by:todo://devtools/selfcheck-s2 @epic:eco.tooling
- [ ] selfcheck S4: отдельная спека — пробы, исполняющие код цели (clippy, credo, mix xref, knip): песочница, зависимости, проектная канарейка @owner:github:andrei-shtanakov @id:selfcheck-s4-spec @blocked_by:todo://devtools/selfcheck-s1 @epic:eco.tooling
- [ ] selfcheck S5: `--judge` — судья без инструментов над кандидатами llm-replaceable и duplicate @owner:github:andrei-shtanakov @id:selfcheck-s5 @blocked_by:todo://devtools/selfcheck-s3 @epic:eco.tooling
```

Проверка: `make plan-check-selftest` зелёный.

- [ ] **Step 10: полный прогон тестов и линтеров**

```bash
uv run --frozen pytest -q
SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q
uv run --frozen --group selfcheck ruff format selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff format --check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
```

Expected: всё зелёное; в первой команде тесты с инструментами — `skipped`,
не `failed`.

- [ ] **Step 11: приёмка S1 на devtools (спека §7)**

```bash
make selfcheck ARGS='--sched-dir ~/Library/LaunchAgents'; echo "exit=$?"
REPORT=$(ls -d out/selfcheck/*/ | tail -1)report.json
python3 - "$REPORT" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))
dead = {f["anchor"] for f in doc["findings"] if f["category"] == "dead"
        and f["rule"].startswith("usage-graph/dead")}
must_live = ["file:issue_worker.py", "file:deploy/r16/setup.sh"]
bad = [a for a in dead if a in must_live or a.startswith("file:scripts/review/")]
print("probes:", {p["probe"]: p["status"] for p in doc["probes"]})
print("dead:", len(dead), "false-dead on required nodes:", bad)
assert not bad, bad
assert not any(f["rule"].startswith("usage-graph/dead")
               and f["anchor"].startswith(("make:", "skill:", "workflow:", "cli:"))
               for f in doc["findings"])
EOF
```

Expected: скрипт без `AssertionError`; `exit` — 0, либо 2/3 с названной в
таблице проб причиной (её записать в описание PR; `failed` из-за самого
selfcheck — чинить до PR). В S1 `confirmed` dead не бывает (потолок P1) —
просмотреть все `likely` dead вручную и выписать в описание PR, сколько из
них действительно мёртвые, а сколько ложные (с причиной ложного — это вход
для S2).

- [ ] **Step 12: dogfood и коммит**

```bash
make selfcheck-dogfood; echo "exit=$?"
git add selfcheck selfcheck.toml Makefile .github/workflows/ci.yml CLAUDE.md TODO.md tests/selfcheck
git commit -m "feat(selfcheck): оркестратор, отчёт, цели make, CI; приёмка S1"
```
