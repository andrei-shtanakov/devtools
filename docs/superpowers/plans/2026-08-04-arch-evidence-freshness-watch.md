# Arch-Evidence Freshness Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scheduled READ-ONLY сенсор, исполняющий `todo://steward/arch-evidence-freshness-watch`: upstream-drift обеих вендоренных prograph-схем в steward + свежесть evidence-пары WS-005, с долговечным статус-файлом и reader-derived `unknown`.

**Architecture:** Один stdlib-скрипт `check-arch-evidence-freshness.py` (паттерн `check-release-drift.py`): режим прогона пишет атомарно статус-файл в `out/` и (под флагом) эскалирует inbox-issue в steward; режим `--read` — независимый потребитель, превращающий просроченный/отсутствующий статус в `unknown`. Сравнение drift — против `origin/<default>` prograph (через `git ls-remote --symref` + `fetch`), никогда против рабочего дерева. Планировщик — launchd (ЯВНО interim), plist-шаблон + make-цели.

**Tech Stack:** Python 3.11+ stdlib only; pytest (dev-группа uv); git CLI; gh CLI (только в `--escalate`); launchd.

## Global Constraints

- Пункт плана-источник: `TODO.md` `@id:arch-evidence-freshness-watch` — его текст является спекой; приёмка из него обязательна.
- READ-ONLY к соседним репо (инвариант №1 `CLAUDE.md`). `git fetch` соседа разрешён (устоявшееся поведение `make fetch`); любые записи в рабочие деревья/индексы соседей запрещены.
- Сенсор НИКОГДА не пишет статус `unknown` — только `clean|drift|stale|unavailable`. `unknown` выводит читатель.
- `stale` = evidence отсутствует/просрочено; `unavailable` = сравнение не состоялось (недоступен upstream/клон/git-ошибка).
- Drift сравнивает upstream `origin/<default-branch>` (resolved SHA записывается), НЕ локальный чекаут prograph — урок two-contract-guarantees.
- Пересчётное сравнение ⇒ состав поверхности наш: до пофайлового сравнения — проверка «upstream добавил файл в контрактный каталог, которого нет в копии» (added-under-excluded-name).
- Неожиданное исключение = exit 4 БЕЗ записи статус-файла (частичная правда хуже просрочки; просрочку поймает читатель). Никогда не мапить краш на код, означающий drift/stale/unavailable — урок dispatcher#110.
- Запись статус-файла атомарна (tmp в том же каталоге + `os.replace`).
- Эскалация только под `--escalate`; дедуп-ключ — префикс заголовка `arch-evidence-freshness-watch:<class>`; тело issue несёт `slug:`/`from:` (ADR-ECO-006). Сбой gh фиксируется в статус-файле, не роняет прогон.
- Тесты без сети и без реальных соседей: фикстурный workspace во временном каталоге; gh — monkeypatch.
- Имя скрипта через дефисы (конвенция репо) ⇒ тесты импортируют через importlib (conftest-фикстура).
- Инжектируемые часы: флаг `--now <ISO>`; в проде — реальный UTC.
- Все времена — UTC ISO-8601; парсинг `datetime.fromisoformat` (Python 3.11 понимает `Z`).
- Коммиты — после каждой задачи; сообщения в стиле репо, `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Фактические пути (проверены 2026-08-04)

- Вендоренные пары в steward: `contracts/prograph-intended-graph/v1/{schema.json,PIN}`, `contracts/prograph-conformance-report/v1/{schema.json,PIN}`. PIN-формат: строки `key: value` (`source: prograph@8deb730 contracts/intended-graph/v1/schema.json`, `sha256: …`, `vendored: …`, `purpose: …`).
- Upstream в prograph: `contracts/intended-graph/v1/schema.json`, `contracts/conformance-report/v1/schema.json`.
- Evidence-пара: `steward/workstreams/WS-005-gate-verdicts/spec/intended-graph.yaml` + `conformance-report.json`; в отчёте `snapshot.indexed_at` (свежесть) и `generated_at` (для сигнала «манифест новее отчёта»).
- Статус-файл: `devtools/out/arch-evidence-freshness/status.json` (`out/` уже в `.gitignore`).

## File Structure

- Create: `check-arch-evidence-freshness.py` — весь сенсор (прогон + reader + эскалация). Один файл — конвенция репо (top-level чекеры).
- Create: `tests/arch_freshness_fixtures.py` — билдер фикстурного workspace (fake prograph + bare «GitHub» + fake steward).
- Create: `tests/conftest.py` — importlib-загрузка дефисного скрипта (фикстура `sensor`).
- Create: `tests/test_arch_evidence_freshness.py` — вся приёмка.
- Create: `templates/com.devtools.arch-evidence-freshness.plist` — launchd-шаблон с плейсхолдерами.
- Modify: `Makefile` — цели `arch-freshness`, `arch-freshness-read`, `arch-freshness-schedule`, `arch-freshness-unschedule` + help.
- Modify: `CLAUDE.md` — строка в таблице «Инструменты».

---

### Task 1: Фикстурный workspace

**Files:**
- Create: `tests/arch_freshness_fixtures.py`
- Test: `tests/test_arch_evidence_freshness.py` (первый тест — самопроверка билдера)

**Interfaces:**
- Produces: `make_workspace(tmp: Path, *, now: datetime, report_age_hours: int = 1) -> Workspace`; `Workspace` (dataclass): `.root` (каталог с `prograph/`, `steward/`), `.prograph`, `.steward`, `.seed` (upstream-рабочий клон), `.canon` (bare-репо, играет GitHub); `upstream_change(ws, relpath: str, content: bytes, msg: str)` — коммит в canon через seed; `git(cwd, *args) -> str`.

- [ ] **Step 1: Написать билдер**

```python
"""arch_freshness_fixtures — фикстурный polyrepo-workspace для тестов сенсора.

Строит во временном каталоге: bare-репо canon (играет GitHub-remote prograph),
его клон prograph внутри workspace, и steward с вендоренными копиями + PIN +
evidence-парой WS-005. Все времена задаются снаружи — реальных часов здесь нет.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_INTENDED = b'{"$id": "intended-graph/v1", "type": "object"}\n'
SCHEMA_REPORT = b'{"$id": "conformance-report/v1", "type": "object"}\n'
UPSTREAM_DIRS = {
    "intended-graph": "contracts/intended-graph/v1",
    "conformance-report": "contracts/conformance-report/v1",
}
VENDORED_DIRS = {
    "intended-graph": "contracts/prograph-intended-graph/v1",
    "conformance-report": "contracts/prograph-conformance-report/v1",
}
EVIDENCE_DIR = "workstreams/WS-005-gate-verdicts/spec"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-b", "master")
    git(path, "config", "user.email", "fixture@test")
    git(path, "config", "user.name", "fixture")


@dataclass
class Workspace:
    root: Path      # workspace: содержит prograph/ и steward/
    prograph: Path  # локальный клон canon
    steward: Path
    seed: Path      # upstream-рабочий клон (для правок «на GitHub»)
    canon: Path     # bare-репо, играет origin


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_workspace(
    tmp: Path, *, now: datetime, report_age_hours: int = 1
) -> Workspace:
    root = tmp / "ws"
    root.mkdir()

    seed = tmp / "seed"
    _init_repo(seed)
    schemas = {"intended-graph": SCHEMA_INTENDED, "conformance-report": SCHEMA_REPORT}
    for name, rel in UPSTREAM_DIRS.items():
        target = seed / rel / "schema.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(schemas[name])
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "contracts v1")

    canon = tmp / "prograph-canon.git"
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", str(seed), str(canon)], check=True
    )
    subprocess.run(
        ["git", "clone", "--quiet", str(canon), str(root / "prograph")], check=True
    )
    prograph = root / "prograph"

    steward = root / "steward"
    _init_repo(steward)
    pinned = git(seed, "rev-parse", "--short", "HEAD")
    for name, rel in VENDORED_DIRS.items():
        vdir = steward / rel
        vdir.mkdir(parents=True)
        (vdir / "schema.json").write_bytes(schemas[name])
        sha = hashlib.sha256(schemas[name]).hexdigest()
        (vdir / "PIN").write_text(
            f"source: prograph@{pinned} {UPSTREAM_DIRS[name]}/schema.json\n"
            f"sha256: {sha}\nvendored: 2026-08-03\npurpose: test fixture\n"
        )
    evidence = steward / EVIDENCE_DIR
    evidence.mkdir(parents=True)
    (evidence / "intended-graph.yaml").write_text("components: []\n")
    report_time = now - timedelta(hours=report_age_hours)
    (evidence / "conformance-report.json").write_text(json.dumps({
        "schema": "conformance-report/v1",
        "generated_at": _iso(report_time),
        "snapshot": {"indexed_at": _iso(report_time), "id": 1, "complete": True},
    }))
    git(steward, "add", "-A")
    git(steward, "commit", "-m", "vendored contracts + WS-005 evidence")
    return Workspace(root, prograph, steward, seed, canon)


def upstream_change(ws: Workspace, relpath: str, content: bytes, msg: str) -> None:
    """Правка «на GitHub»: коммит в seed + push в canon. Клон ws.prograph не трогаем."""
    target = ws.seed / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    git(ws.seed, "add", "-A")
    git(ws.seed, "commit", "-m", msg)
    git(ws.seed, "push", "--quiet", str(ws.canon), "master:master")
```

- [ ] **Step 2: Написать тест-самопроверку билдера**

```python
# tests/test_arch_evidence_freshness.py
from __future__ import annotations

import json
from datetime import datetime, timezone

from arch_freshness_fixtures import (
    EVIDENCE_DIR, Workspace, git, make_workspace, upstream_change,
)

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def test_fixture_builds_polyrepo_workspace(tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    assert (ws.prograph / "contracts/intended-graph/v1/schema.json").is_file()
    assert (ws.steward / "contracts/prograph-intended-graph/v1/PIN").is_file()
    report = json.loads(
        (ws.steward / EVIDENCE_DIR / "conformance-report.json").read_text()
    )
    assert report["snapshot"]["indexed_at"] == "2026-08-04T11:00:00Z"
    upstream_change(
        ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "mutate"
    )
    # canon получил новый коммит, локальный клон — ещё нет
    assert git(ws.seed, "rev-parse", "HEAD") != git(ws.prograph, "rev-parse", "HEAD")
```

- [ ] **Step 3: Прогнать** — `uv run --frozen pytest tests/test_arch_evidence_freshness.py -v`, ожидание: PASS.
- [ ] **Step 4: Commit** — `git add tests/ && git commit -m "test: фикстурный polyrepo-workspace для arch-freshness сенсора"`

---

### Task 2: Каркас скрипта, conftest-загрузка, parse_pin и часы

**Files:**
- Create: `check-arch-evidence-freshness.py`
- Create: `tests/conftest.py`
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Produces: модуль-объект через фикстуру pytest `sensor`; в нём: `parse_pin(text: str) -> dict[str, str]`; `parse_iso(s: str) -> datetime` (aware UTC); `iso(dt: datetime) -> str`; константы `SENSOR_VERSION`, `STATUS_SCHEMA = "arch-evidence-freshness-status/v1"`, `CLASS_ORDER = {"clean": 0, "unavailable": 1, "stale": 2, "drift": 3}`, `VENDORED` (кортежи `(name, vendored_rel, upstream_rel)`), `EVIDENCE_REL`; `Finding` (dataclass: `check: str`, `cls: str` — в JSON сериализуется ключом `class`, `detail: str`).

- [ ] **Step 1: conftest**

```python
# tests/conftest.py
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check-arch-evidence-freshness.py"


@pytest.fixture(scope="session")
def sensor():
    spec = importlib.util.spec_from_file_location("arch_freshness_sensor", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["arch_freshness_sensor"] = mod
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: Тесты parse_pin / часов (падают: скрипта нет)**

```python
def test_parse_pin_reads_key_value_lines(sensor):
    pin = sensor.parse_pin(
        "source: prograph@8deb730 contracts/intended-graph/v1/schema.json\n"
        "sha256: abc\nvendored: 2026-08-03\npurpose: x\n"
    )
    assert pin["source"].startswith("prograph@8deb730")
    assert pin["sha256"] == "abc"


def test_parse_iso_handles_zulu(sensor):
    dt = sensor.parse_iso("2026-08-04T12:00:00Z")
    assert dt.tzinfo is not None
    assert sensor.iso(dt) == "2026-08-04T12:00:00Z"
```

- [ ] **Step 3: Убедиться, что падают** (`FileNotFoundError` в conftest).
- [ ] **Step 4: Каркас скрипта**

```python
#!/usr/bin/env python3
"""check-arch-evidence-freshness.py — freshness/drift-сенсор арх-evidence (v0.1).

Дом: devtools/. Исполняет scheduled-обязательство
todo://steward/arch-evidence-freshness-watch («вне CI этого репо» — здесь):
  A. upstream-drift обеих вендоренных prograph-схем steward — сравнение с
     origin/<default> prograph (resolved SHA записывается), НЕ с рабочим деревом;
     до пофайлового сравнения — проверка расширения upstream-поверхности.
  B. свежесть evidence-пары WS-005 (intended-graph.yaml + conformance-report.json)
     по snapshot.indexed_at + сигнал «манифест новее отчёта».

Два слоя статусов (не смешивать): сенсор пишет только статус ПРОГОНА —
clean|drift|stale|unavailable (stale = evidence отсутствует/просрочено,
unavailable = сравнение не состоялось). `unknown` сенсор не пишет НИКОГДА:
это выводимый статус читателя (--read) — просроченный или отсутствующий
статус-файл по next_expected_at читается как unknown, не как последний зелёный.
Поэтому неожиданный краш = exit 4 БЕЗ записи статус-файла: просрочку поймает
читатель; частичная запись лгала бы.

READ-ONLY: при красном — inbox-issue в steward (ADR-ECO-006, только под
--escalate) с дедуп-ключом в заголовке `arch-evidence-freshness-watch:<class>`;
никакого автоматического ре-вендора.

Использование:
    ./check-arch-evidence-freshness.py --workspace ..            # прогон
    ./check-arch-evidence-freshness.py --workspace .. --escalate # прогон + issue
    ./check-arch-evidence-freshness.py --read                    # читатель
Exit: прогон 0=clean, 1=есть findings, 4=краш сенсора;
      читатель 0=clean, 1=non-clean, 2=unknown.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SENSOR_VERSION = "0.1.0"
STATUS_SCHEMA = "arch-evidence-freshness-status/v1"
CLASS_ORDER = {"clean": 0, "unavailable": 1, "stale": 2, "drift": 3}
DEDUP_PREFIX = "arch-evidence-freshness-watch"

VENDORED = (
    ("intended-graph", "steward/contracts/prograph-intended-graph/v1",
     "contracts/intended-graph/v1"),
    ("conformance-report", "steward/contracts/prograph-conformance-report/v1",
     "contracts/conformance-report/v1"),
)
EVIDENCE_REL = "steward/workstreams/WS-005-gate-verdicts/spec"
DEFAULT_STATUS = Path(__file__).resolve().parent / "out/arch-evidence-freshness/status.json"


@dataclass
class Finding:
    check: str
    cls: str  # clean|drift|stale|unavailable — слой сенсора, unknown не бывает
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"check": self.check, "class": self.cls, "detail": self.detail}


def parse_pin(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(args: list[str], cwd: Path, timeout: int = 60) -> tuple[int, bytes, str]:
    """(код, stdout-байты, stderr-текст); отсутствие бинаря/таймаут — код -1."""
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr.decode(errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as err:
        return -1, b"", str(err)
```

- [ ] **Step 5: Прогнать оба теста** — PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat: каркас arch-freshness сенсора — PIN, часы, константы"`

---

### Task 3: Резолюция upstream (default branch, fetch, unavailable)

**Files:**
- Modify: `check-arch-evidence-freshness.py`
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Produces: `resolve_upstream(prograph: Path) -> tuple[dict | None, Finding | None]` — успех: (`{"remote": str, "default_branch": str, "head_sha": str}`, None); недоступность: (None, Finding(cls="unavailable")). После успеха объекты `head_sha` гарантированно в локальном клоне (fetch выполнен). `upstream_bytes(prograph: Path, sha: str, relpath: str) -> bytes | None` (None = файла нет в дереве); `upstream_ls(prograph: Path, sha: str, reldir: str) -> list[str]` (имена файлов).

- [ ] **Step 1: Тесты**

```python
def test_resolve_upstream_reports_moving_default_branch(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "move")
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert finding is None
    # видит именно canon-HEAD, а не отставший локальный чекаут
    assert up["head_sha"] == git(ws.seed, "rev-parse", "HEAD")
    assert up["default_branch"] == "master"
    assert sensor.upstream_bytes(
        ws.prograph, up["head_sha"], "contracts/intended-graph/v1/schema.json"
    ) == b"{}\n"
    assert sensor.upstream_ls(
        ws.prograph, up["head_sha"], "contracts/intended-graph/v1"
    ) == ["schema.json"]


def test_resolve_upstream_unavailable_when_remote_gone(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    import shutil
    shutil.rmtree(ws.canon)
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert up is None
    assert finding.cls == "unavailable"
    assert "prograph" in finding.check
```

- [ ] **Step 2: Убедиться, что падают** (AttributeError).
- [ ] **Step 3: Реализация**

```python
def resolve_upstream(prograph: Path) -> tuple[dict | None, "Finding | None"]:
    if not (prograph / ".git").exists():
        return None, Finding("upstream:prograph", "unavailable",
                             f"клона prograph нет: {prograph}")
    code, out, err = sh(["git", "ls-remote", "--symref", "origin", "HEAD"], prograph)
    if code != 0:
        return None, Finding("upstream:prograph", "unavailable",
                             f"origin недоступен: {err.strip() or code}")
    branch = head_sha = None
    for line in out.decode().splitlines():
        if line.startswith("ref:"):
            branch = line.split()[1].removeprefix("refs/heads/")
        elif line.endswith("HEAD"):
            head_sha = line.split()[0]
    if not branch or not head_sha:
        return None, Finding("upstream:prograph", "unavailable",
                             "ls-remote не вернул HEAD/symref")
    code, _, err = sh(["git", "fetch", "--quiet", "origin", branch], prograph)
    if code != 0:
        return None, Finding("upstream:prograph", "unavailable",
                             f"fetch origin/{branch} не удался: {err.strip()}")
    code, out, _ = sh(["git", "remote", "get-url", "origin"], prograph)
    remote = out.decode().strip() if code == 0 else "?"
    return {"remote": remote, "default_branch": branch, "head_sha": head_sha}, None


def upstream_bytes(prograph: Path, sha: str, relpath: str) -> bytes | None:
    code, out, _ = sh(["git", "cat-file", "blob", f"{sha}:{relpath}"], prograph)
    return out if code == 0 else None


def upstream_ls(prograph: Path, sha: str, reldir: str) -> list[str]:
    code, out, _ = sh(
        ["git", "ls-tree", "--name-only", sha, "--", reldir.rstrip("/") + "/"],
        prograph,
    )
    if code != 0:
        return []
    return sorted(Path(line).name for line in out.decode().splitlines() if line)
```

- [ ] **Step 4: Прогнать** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: резолюция upstream prograph — symref+fetch, unavailable при недоступности"`

---

### Task 4: Drift-проверка вендоренных схем + расширение поверхности

**Files:**
- Modify: `check-arch-evidence-freshness.py`
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Consumes: `resolve_upstream`, `upstream_bytes`, `upstream_ls`, `VENDORED`, `Finding`.
- Produces: `check_vendored(workspace: Path, up: dict) -> tuple[list[Finding], dict]` — findings только не-clean; второй элемент — `pins`: `{name: {"source": str, "sha256": str}}` для блока `resolved` статус-файла.

- [ ] **Step 1: Тесты**

```python
def _resolved(sensor, ws):
    up, finding = sensor.resolve_upstream(ws.prograph)
    assert finding is None
    return up


def test_vendored_clean_when_copies_match_upstream(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    findings, pins = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert findings == []
    assert set(pins) == {"intended-graph", "conformance-report"}
    assert pins["intended-graph"]["source"].startswith("prograph@")


def test_vendored_drift_when_upstream_schema_changed(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(
        ws, "contracts/intended-graph/v1/schema.json", b'{"v": 2}\n', "evolve"
    )
    findings, _ = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert [f.cls for f in findings] == ["drift"]
    assert findings[0].check == "schema-drift:intended-graph"


def test_vendored_drift_when_upstream_adds_file_to_surface(sensor, tmp_path):
    # added-under-excluded-name: файл сверх нашей копии не выпадает молча
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(
        ws, "contracts/conformance-report/v1/examples.json", b"[]\n", "add file"
    )
    findings, _ = sensor.check_vendored(ws.root, _resolved(sensor, ws))
    assert [f.cls for f in findings] == ["drift"]
    assert findings[0].check == "surface:conformance-report"
    assert "examples.json" in findings[0].detail
```

- [ ] **Step 2: Убедиться, что падают.**
- [ ] **Step 3: Реализация**

```python
def check_vendored(workspace: Path, up: dict) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    pins: dict[str, dict[str, str]] = {}
    prograph = workspace / "prograph"
    for name, vendored_rel, upstream_rel in VENDORED:
        vdir = workspace / vendored_rel
        pin_file = vdir / "PIN"
        if not vdir.is_dir() or not pin_file.is_file():
            findings.append(Finding(
                f"vendored:{name}", "unavailable",
                f"вендоренной копии/PIN нет: {vdir}"))
            continue
        pin = parse_pin(pin_file.read_text())
        pins[name] = {"source": pin.get("source", "?"),
                      "sha256": pin.get("sha256", "?")}
        # 1) расширение поверхности — ДО пофайлового сравнения
        ours = sorted(p.name for p in vdir.iterdir()
                      if p.is_file() and p.name != "PIN")
        theirs = upstream_ls(prograph, up["head_sha"], upstream_rel)
        extra = sorted(set(theirs) - set(ours))
        if extra:
            findings.append(Finding(
                f"surface:{name}", "drift",
                f"upstream добавил в {upstream_rel}: {', '.join(extra)} — "
                "поверхность расширилась, пересчёт её не видел бы"))
        # 2) пофайловое сравнение нашей поверхности
        for fname in ours:
            theirs_bytes = upstream_bytes(
                prograph, up["head_sha"], f"{upstream_rel}/{fname}")
            if theirs_bytes is None:
                findings.append(Finding(
                    f"schema-drift:{name}", "drift",
                    f"{fname} исчез из upstream {upstream_rel}"))
            elif theirs_bytes != (vdir / fname).read_bytes():
                findings.append(Finding(
                    f"schema-drift:{name}", "drift",
                    f"{fname}: origin/{up['default_branch']} "
                    f"({up['head_sha'][:9]}) отличается от копии — "
                    "нужен осознанный re-vendor PR в steward"))
    return findings, pins
```

- [ ] **Step 4: Прогнать** — PASS (обратить внимание: fixture-путь `vendored_rel` в скрипте начинается со `steward/` — совпадает с фикстурой).
- [ ] **Step 5: Commit** — `git commit -m "feat: drift вендоренных схем + проверка расширения upstream-поверхности"`

---

### Task 5: Freshness evidence-пары WS-005

**Files:**
- Modify: `check-arch-evidence-freshness.py`
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Consumes: `EVIDENCE_REL`, `parse_iso`, `sh`, `Finding`.
- Produces: `check_evidence(workspace: Path, now: datetime, max_age_days: int) -> list[Finding]`. Сигналы stale: отчёт/манифест отсутствует или нечитаем; `snapshot.indexed_at` старше `max_age_days`; манифест коммитнут (или грязен) позже `generated_at` отчёта.

- [ ] **Step 1: Тесты**

```python
from datetime import timedelta


def test_evidence_fresh_is_clean(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=1)
    assert sensor.check_evidence(ws.root, NOW, 30) == []


def test_evidence_stale_by_age(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=31 * 24)
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "evidence-age:conformance-report"


def test_evidence_stale_when_manifest_newer_than_report(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW, report_age_hours=1)
    manifest = ws.steward / EVIDENCE_DIR / "intended-graph.yaml"
    manifest.write_text("components: [{id: new}]\n")
    git(ws.steward, "add", "-A")
    git(ws.steward, "commit", "-m", "manifest evolves")
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "manifest-newer:intended-graph.yaml"


def test_evidence_missing_report_is_stale(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    (ws.steward / EVIDENCE_DIR / "conformance-report.json").unlink()
    findings = sensor.check_evidence(ws.root, NOW, 30)
    assert [f.cls for f in findings] == ["stale"]
    assert findings[0].check == "evidence-missing:conformance-report"
```

- [ ] **Step 2: Убедиться, что падают.**
- [ ] **Step 3: Реализация**

```python
def _manifest_changed_after(steward: Path, rel: str, cutoff: datetime) -> str | None:
    """Описание изменения манифеста позже cutoff, или None. Грязь тоже считается."""
    code, out, _ = sh(["git", "status", "--porcelain", "--", rel], steward)
    if code == 0 and out.decode().strip():
        return "манифест изменён и не закоммичен (не отражён в отчёте)"
    code, out, _ = sh(["git", "log", "-1", "--format=%cI", "--", rel], steward)
    if code != 0 or not out.decode().strip():
        return None
    committed = parse_iso(out.decode().strip())
    if committed > cutoff:
        return f"манифест коммитнут {iso(committed)} — позже отчёта {iso(cutoff)}"
    return None


def check_evidence(workspace: Path, now: datetime, max_age_days: int) -> list[Finding]:
    findings: list[Finding] = []
    evidence = workspace / EVIDENCE_REL
    steward = workspace / "steward"
    report_path = evidence / "conformance-report.json"
    manifest_rel = str(Path(EVIDENCE_REL).relative_to("steward") / "intended-graph.yaml")
    if not (evidence / "intended-graph.yaml").is_file():
        findings.append(Finding("evidence-missing:intended-graph.yaml", "stale",
                                "манифеста WS-005 нет"))
    if not report_path.is_file():
        findings.append(Finding("evidence-missing:conformance-report", "stale",
                                f"отчёта нет: {report_path}"))
        return findings
    try:
        report = json.loads(report_path.read_text())
        indexed_at = parse_iso(report["snapshot"]["indexed_at"])
        generated_at = parse_iso(report["generated_at"])
    except (ValueError, KeyError, TypeError) as err:
        findings.append(Finding("evidence-unreadable:conformance-report", "stale",
                                f"отчёт не разбирается: {err}"))
        return findings
    age = now - indexed_at
    if age > timedelta(days=max_age_days):
        findings.append(Finding(
            "evidence-age:conformance-report", "stale",
            f"snapshot.indexed_at={iso(indexed_at)} старше {max_age_days}д "
            f"(возраст {age.days}д)"))
    newer = _manifest_changed_after(steward, manifest_rel, generated_at)
    if newer:
        findings.append(Finding("manifest-newer:intended-graph.yaml", "stale", newer))
    return findings
```

- [ ] **Step 4: Прогнать** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: freshness evidence-пары WS-005 — возраст, отсутствие, манифест новее отчёта"`

---

### Task 6: Прогон целиком — статус-файл, exit-коды, краш-путь

**Files:**
- Modify: `check-arch-evidence-freshness.py`
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Consumes: всё выше.
- Produces: `run_sensor(workspace: Path, status_path: Path, now: datetime, max_age_days: int, next_expected_hours: int, escalate: bool) -> int` (exit-код 0/1; эскалация — Task 8, здесь параметр принимается и при `True` вызывает `escalate_findings`, которую Task 8 определит; до Task 8 — заглушка `def escalate_findings(...): return []`); `write_status_atomic(path: Path, payload: dict) -> None`; `overall(findings) -> str` (max по `CLASS_ORDER`, `clean` при пустом); `main(argv) -> int` с argparse (`--workspace`, `--status-file`, `--now`, `--max-age-days` default 30, `--next-expected-hours` default 26, `--escalate`, `--read`) и краш-гардом (exit 4, статус-файл не тронут).

- [ ] **Step 1: Тесты**

```python
def _run(sensor, ws, tmp_path, *extra):
    status = tmp_path / "status.json"
    code = sensor.main([
        "--workspace", str(ws.root), "--status-file", str(status),
        "--now", "2026-08-04T12:00:00Z", *extra,
    ])
    return code, status


def test_clean_run_writes_full_status_and_exits_0(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    code, status_path = _run(sensor, ws, tmp_path)
    assert code == 0
    status = json.loads(status_path.read_text())
    assert status["schema"] == "arch-evidence-freshness-status/v1"
    assert status["status"] == "clean" and status["findings"] == []
    assert status["completed_at"] == "2026-08-04T12:00:00Z"
    assert status["next_expected_at"] == "2026-08-05T14:00:00Z"  # +26h
    for key in ("host", "sensor_version", "started_at", "resolved", "escalations"):
        assert key in status
    assert status["resolved"]["upstream"]["default_branch"] == "master"
    assert "intended-graph" in status["resolved"]["pins"]


def test_drift_run_exits_1_with_drift_status(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")
    code, status_path = _run(sensor, ws, tmp_path)
    assert code == 1
    status = json.loads(status_path.read_text())
    assert status["status"] == "drift" and status["classes"] == ["drift"]


def test_unavailable_never_reads_as_clean(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    import shutil
    shutil.rmtree(ws.canon)
    code, status_path = _run(sensor, ws, tmp_path)
    assert code == 1
    assert json.loads(status_path.read_text())["status"] == "unavailable"


def test_crash_exits_4_and_leaves_status_untouched(sensor, tmp_path, monkeypatch):
    ws = make_workspace(tmp_path, now=NOW)
    status = tmp_path / "status.json"
    status.write_text('{"prior": true}')
    monkeypatch.setattr(
        sensor, "resolve_upstream",
        lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    code = sensor.main([
        "--workspace", str(ws.root), "--status-file", str(status),
        "--now", "2026-08-04T12:00:00Z",
    ])
    assert code == 4
    assert json.loads(status.read_text()) == {"prior": True}  # не перезаписан
```

- [ ] **Step 2: Убедиться, что падают.**
- [ ] **Step 3: Реализация**

```python
def overall(findings: list[Finding]) -> str:
    if not findings:
        return "clean"
    return max((f.cls for f in findings), key=lambda c: CLASS_ORDER[c])


def write_status_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def escalate_findings(workspace, classes, findings, host):  # Task 8 заменит
    return []


def run_sensor(workspace: Path, status_path: Path, now: datetime,
               max_age_days: int, next_expected_hours: int,
               escalate: bool) -> int:
    started = now
    findings: list[Finding] = []
    resolved: dict = {"workspace": str(workspace), "pins": {}}
    up, up_finding = resolve_upstream(workspace / "prograph")
    if up_finding:
        findings.append(up_finding)
    else:
        resolved["upstream"] = up
        vendored_findings, pins = check_vendored(workspace, up)
        findings.extend(vendored_findings)
        resolved["pins"] = pins
    findings.extend(check_evidence(workspace, now, max_age_days))
    classes = sorted({f.cls for f in findings}, key=lambda c: -CLASS_ORDER[c])
    host = socket.gethostname()
    escalations = (escalate_findings(workspace, classes, findings, host)
                   if escalate and classes else [])
    write_status_atomic(status_path, {
        "schema": STATUS_SCHEMA,
        "sensor_version": SENSOR_VERSION,
        "host": host,
        "started_at": iso(started),
        "completed_at": iso(now),
        "next_expected_at": iso(now + timedelta(hours=next_expected_hours)),
        "status": overall(findings),
        "classes": classes,
        "findings": [f.as_json() for f in findings],
        "resolved": resolved,
        "escalations": escalations,
    })
    for f in findings:
        print(f"[{f.cls}] {f.check}: {f.detail}")
    print(f"status: {overall(findings)} -> {status_path}")
    return 0 if not findings else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--now", default=None,
                        help="ISO-время 'сейчас' (тесты); по умолчанию UTC now")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--next-expected-hours", type=int, default=26)
    parser.add_argument("--escalate", action="store_true")
    parser.add_argument("--read", action="store_true",
                        help="режим читателя: unknown при просрочке")
    args = parser.parse_args(argv)
    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    if args.read:
        return read_status(args.status_file, now)  # Task 7
    try:
        return run_sensor(args.workspace.resolve(), args.status_file, now,
                          args.max_age_days, args.next_expected_hours,
                          args.escalate)
    except Exception:  # краш ≠ вердикт: статус-файл не трогаем, просрочку поймает читатель
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(main())
```

До Task 7 добавить заглушку `def read_status(path, now): return 2`, чтобы модуль загружался.

- [ ] **Step 4: Прогнать все тесты** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: прогон сенсора — атомарный статус-файл, exit 0/1, краш=4 без записи"`

---

### Task 7: Читатель — reader-derived unknown

**Files:**
- Modify: `check-arch-evidence-freshness.py` (заменить заглушку `read_status`)
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Consumes: `STATUS_SCHEMA`, `parse_iso`, `iso`.
- Produces: `read_status(path: Path, now: datetime) -> int` — печатает одну строку вердикта; exit 0=clean, 1=non-clean (fresh, но drift/stale/unavailable), 2=unknown (нет файла / не разбирается / чужая схема / `now > next_expected_at`).

- [ ] **Step 1: Тесты**

```python
def test_reader_unknown_when_no_status_file(sensor, tmp_path, capsys):
    code = sensor.read_status(tmp_path / "absent.json", NOW)
    assert code == 2
    assert "unknown" in capsys.readouterr().out


def test_reader_unknown_when_overdue_even_if_last_run_was_clean(
    sensor, tmp_path, capsys
):
    ws = make_workspace(tmp_path, now=NOW)
    _, status_path = _run(sensor, ws, tmp_path)  # clean, next_expected +26h
    late = NOW + timedelta(hours=27)
    assert sensor.read_status(status_path, late) == 2
    out = capsys.readouterr().out
    assert "unknown" in out and "next_expected_at" in out


def test_reader_clean_when_fresh_and_clean(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    _, status_path = _run(sensor, ws, tmp_path)
    assert sensor.read_status(status_path, NOW + timedelta(hours=1)) == 0


def test_reader_nonclean_when_fresh_with_drift(sensor, tmp_path):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")
    _, status_path = _run(sensor, ws, tmp_path)
    assert sensor.read_status(status_path, NOW + timedelta(hours=1)) == 1
```

- [ ] **Step 2: Убедиться, что падают** (заглушка всегда 2 → падают clean/nonclean-кейсы).
- [ ] **Step 3: Реализация**

```python
def read_status(path: Path, now: datetime) -> int:
    """Читатель. unknown — вывод ЭТОЙ стороны: сенсор его никогда не пишет."""
    try:
        status = json.loads(path.read_text())
        schema = status["schema"]
        next_expected = parse_iso(status["next_expected_at"])
        verdict = status["status"]
    except (OSError, ValueError, KeyError, TypeError) as err:
        print(f"unknown: статус-файл отсутствует/не разбирается ({err}) — {path}")
        return 2
    if schema != STATUS_SCHEMA:
        print(f"unknown: чужая схема статус-файла {schema!r} — {path}")
        return 2
    if now > next_expected:
        print(f"unknown: статус просрочен (next_expected_at={iso(next_expected)}, "
              f"now={iso(now)}) — последний вердикт {verdict!r} не считается")
        return 2
    print(f"{verdict}: прогон {status.get('completed_at')} host="
          f"{status.get('host')} classes={status.get('classes')}")
    return 0 if verdict == "clean" else 1
```

- [ ] **Step 4: Прогнать** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: читатель статуса — просрочка/отсутствие ⇒ unknown, exit 2"`

---

### Task 8: Эскалация — inbox-issue в steward с дедуп-ключом

**Files:**
- Modify: `check-arch-evidence-freshness.py` (заменить заглушку `escalate_findings`, добавить `_gh`)
- Test: `tests/test_arch_evidence_freshness.py`

**Interfaces:**
- Consumes: `DEDUP_PREFIX`, `sh`, `Finding`.
- Produces: `_gh(args: list[str]) -> tuple[int, str]` (единственная точка вызова gh — тесты её monkeypatch'ат); `steward_repo_slug(steward: Path) -> str | None` (парсит `owner/name` из origin-URL); `escalate_findings(workspace: Path, classes: list[str], findings: list[Finding], host: str) -> list[dict]` — по одной записи на класс: `{"class", "action": "created"|"exists"|"error", "detail"}`.

- [ ] **Step 1: Тесты**

```python
def test_escalation_off_by_default_never_calls_gh(sensor, tmp_path, monkeypatch):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")
    calls = []
    monkeypatch.setattr(sensor, "_gh", lambda a: calls.append(a) or (0, "[]"))
    code, status_path = _run(sensor, ws, tmp_path)  # без --escalate
    assert code == 1 and calls == []
    assert json.loads(status_path.read_text())["escalations"] == []


def test_escalation_creates_issue_with_dedup_key_and_adr006_fields(
    sensor, tmp_path, monkeypatch
):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")
    calls = []

    def fake_gh(args):
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return 0, "[]"
        return 0, "https://github.com/o/steward/issues/7"

    monkeypatch.setattr(sensor, "_gh", fake_gh)
    monkeypatch.setattr(sensor, "steward_repo_slug", lambda p: "o/steward")
    code, status_path = _run(sensor, ws, tmp_path, "--escalate")
    esc = json.loads(status_path.read_text())["escalations"]
    assert esc == [{"class": "drift", "action": "created",
                    "detail": "https://github.com/o/steward/issues/7"}]
    create = next(a for a in calls if a[:2] == ["issue", "create"])
    title = create[create.index("--title") + 1]
    body = create[create.index("--body") + 1]
    assert title.startswith("arch-evidence-freshness-watch:drift")
    assert "slug: arch-evidence-freshness-watch" in body
    assert "from: devtools#arch-evidence-freshness-watch" in body


def test_escalation_dedup_skips_when_open_issue_exists(sensor, tmp_path, monkeypatch):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")

    def fake_gh(args):
        if args[:2] == ["issue", "list"]:
            return 0, json.dumps([{
                "number": 5,
                "title": "arch-evidence-freshness-watch:drift — старое",
                "url": "https://github.com/o/steward/issues/5",
            }])
        raise AssertionError("create не должен вызываться")

    monkeypatch.setattr(sensor, "_gh", fake_gh)
    monkeypatch.setattr(sensor, "steward_repo_slug", lambda p: "o/steward")
    _, status_path = _run(sensor, ws, tmp_path, "--escalate")
    esc = json.loads(status_path.read_text())["escalations"]
    assert esc[0]["action"] == "exists" and "5" in esc[0]["detail"]


def test_escalation_gh_failure_recorded_not_raised(sensor, tmp_path, monkeypatch):
    ws = make_workspace(tmp_path, now=NOW)
    upstream_change(ws, "contracts/intended-graph/v1/schema.json", b"{}\n", "m")
    monkeypatch.setattr(sensor, "_gh", lambda a: (-1, "no gh"))
    monkeypatch.setattr(sensor, "steward_repo_slug", lambda p: "o/steward")
    code, status_path = _run(sensor, ws, tmp_path, "--escalate")
    assert code == 1  # findings есть; сбой gh не превращается в краш
    esc = json.loads(status_path.read_text())["escalations"]
    assert esc[0]["action"] == "error"
```

- [ ] **Step 2: Убедиться, что падают.**
- [ ] **Step 3: Реализация**

```python
def _gh(args: list[str]) -> tuple[int, str]:
    """Единственная точка вызова gh — тесты подменяют её."""
    code, out, err = sh(["gh", *args], Path.cwd(), timeout=60)
    return code, out.decode(errors="replace").strip() if code == 0 else err.strip()


def steward_repo_slug(steward: Path) -> str | None:
    code, out, _ = sh(["git", "remote", "get-url", "origin"], steward)
    if code != 0:
        return None
    import re
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", out.decode().strip())
    return match.group(1) if match else None


def escalate_findings(workspace: Path, classes: list[str],
                      findings: list[Finding], host: str) -> list[dict]:
    records: list[dict] = []
    repo = steward_repo_slug(workspace / "steward")
    if repo is None:
        return [{"class": c, "action": "error",
                 "detail": "origin steward не определён"} for c in classes]
    for cls in classes:
        prefix = f"{DEDUP_PREFIX}:{cls}"
        code, out = _gh(["issue", "list", "-R", repo, "--label", "inbox",
                         "--state", "open", "--limit", "100",
                         "--json", "number,title,url"])
        if code != 0:
            records.append({"class": cls, "action": "error", "detail": out})
            continue
        try:
            existing = [i for i in json.loads(out or "[]")
                        if i["title"].startswith(prefix)]
        except (ValueError, KeyError, TypeError) as err:
            records.append({"class": cls, "action": "error",
                            "detail": f"issue list не разбирается: {err}"})
            continue
        if existing:
            records.append({"class": cls, "action": "exists",
                            "detail": f"#{existing[0]['number']} уже открыт"})
            continue
        lines = "\n".join(f"- `{f.check}`: {f.detail}"
                          for f in findings if f.cls == cls)
        body = (
            f"slug: {DEDUP_PREFIX}\n"
            f"from: devtools#{DEDUP_PREFIX}\n\n"
            f"Автосенсор devtools (host `{host}`) обнаружил класс `{cls}`:\n\n"
            f"{lines}\n\n"
            "Действие — осознанный re-vendor/refresh PR в steward; сенсор "
            "READ-ONLY и ничего не меняет сам. Статус-файл: "
            "`devtools/out/arch-evidence-freshness/status.json`.\n"
        )
        title = f"{prefix} — автосенсор devtools, host {host}"
        code, out = _gh(["issue", "create", "-R", repo, "--label", "inbox",
                         "--title", title, "--body", body])
        records.append({"class": cls,
                        "action": "created" if code == 0 else "error",
                        "detail": out})
    return records
```

- [ ] **Step 4: Прогнать всю сюиту** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: эскалация inbox-issue в steward — дедуп по классу, gh-сбой не роняет прогон"`

---

### Task 9: Makefile, launchd-шаблон, CLAUDE.md, живой смоук

**Files:**
- Modify: `Makefile`
- Create: `templates/com.devtools.arch-evidence-freshness.plist`
- Modify: `CLAUDE.md` (строка в таблице «Инструменты»)

**Interfaces:**
- Consumes: CLI из Task 6/7.
- Produces: цели `arch-freshness` (прогон без эскалации), `arch-freshness-read`, `arch-freshness-schedule`, `arch-freshness-unschedule`.

- [ ] **Step 1: Makefile** — в `help` добавить:

```
	@echo "  make arch-freshness       — drift/freshness арх-evidence (steward↔prograph), без эскалации"
	@echo "  make arch-freshness-read  — читатель статуса: просрочка ⇒ unknown (exit 2)"
	@echo "  make arch-freshness-schedule   — launchd-агент (INTERIM-планировщик), ежедневно"
	@echo "  make arch-freshness-unschedule — снять launchd-агент"
```

цели (и дополнить `.PHONY`):

```
arch-freshness:      ; @python3 ./check-arch-evidence-freshness.py --workspace ..
arch-freshness-read: ; @python3 ./check-arch-evidence-freshness.py --read

PLIST_LABEL := com.devtools.arch-evidence-freshness
PLIST_DST   := $(HOME)/Library/LaunchAgents/$(PLIST_LABEL).plist
arch-freshness-schedule:
	@mkdir -p out/arch-evidence-freshness
	@sed -e "s|@DEVTOOLS_DIR@|$(CURDIR)|g" \
	     templates/$(PLIST_LABEL).plist > $(PLIST_DST)
	@launchctl unload $(PLIST_DST) 2>/dev/null || true
	@launchctl load $(PLIST_DST)
	@echo "launchd-агент загружен (INTERIM-планировщик): $(PLIST_DST)"
arch-freshness-unschedule:
	@launchctl unload $(PLIST_DST) 2>/dev/null || true
	@rm -f $(PLIST_DST)
	@echo "launchd-агент снят"
```

- [ ] **Step 2: plist-шаблон** (`templates/com.devtools.arch-evidence-freshness.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- INTERIM-планировщик (решение владельца 2026-08-04): до появления CI у
     devtools (@id:ci-selftest-and-plan-check). Выключенная машина не запустит
     сенсор и не сообщит об этом — поэтому unknown выводит ЧИТАТЕЛЬ статуса
     по next_expected_at, а не планировщик. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.devtools.arch-evidence-freshness</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>@DEVTOOLS_DIR@/check-arch-evidence-freshness.py</string>
    <string>--workspace</string><string>@DEVTOOLS_DIR@/..</string>
    <string>--escalate</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>40</integer></dict>
  <key>StandardOutPath</key>
  <string>@DEVTOOLS_DIR@/out/arch-evidence-freshness/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>@DEVTOOLS_DIR@/out/arch-evidence-freshness/launchd.log</string>
</dict>
</plist>
```

- [ ] **Step 3: CLAUDE.md** — в таблицу «Инструменты» добавить строку:

```
| `check-arch-evidence-freshness.py` | drift вендоренных prograph-схем steward + freshness evidence WS-005; `--read` — просрочка ⇒ unknown |
```

- [ ] **Step 4: Живой смоук** (реальный workspace, сеть нужна):

```
python3 ./check-arch-evidence-freshness.py --workspace ..
python3 ./check-arch-evidence-freshness.py --read
```

Ожидание: прогон печатает честный статус (clean — или реальные findings, тогда прочитать и оценить, не глушить), `--read` возвращает 0/1 согласованно. `make arch-freshness` и `make arch-freshness-read` работают из каталога devtools.

- [ ] **Step 5: Commit** — `git commit -m "feat: make-цели, launchd-шаблон (interim) и строка в CLAUDE.md"`

---

### Task 10: Финал — полная сюита, PR

- [ ] **Step 1:** `uv run --frozen pytest -v` — вся сюита зелёная (включая существующие characterization-тесты).
- [ ] **Step 2:** Пуш ветки, `gh pr create` с телом: что сделано, соответствие приёмке пункта `@id:arch-evidence-freshness-watch` (4 синтетических кейса + reader-тесты + краш-путь), что НЕ закрыто — «два штатных прогона по расписанию» (операционная приёмка после мержа и `make arch-freshness-schedule`; пункт TODO остаётся открытым до неё).
- [ ] **Step 3:** Отработать ревью Copilot; мерж — человек.

## Self-Review

- Приёмка пункта TODO покрыта: drift (Task 4/6), stale (Task 5), unavailable (Task 3/6), added-under-excluded-name (Task 4), читатель «просрочка ⇒ unknown» (Task 7), дедуп-ключ `arch-evidence-freshness-watch:<class>` (Task 8), статус-файл со всеми полями пункта — host/started/completed/next_expected_at/пины/статус/версия (Task 6), launchd interim (Task 9). «Два штатных прогона» — операционная приёмка, названа в PR-теле (Task 10), пункт TODO не закрывается этим PR.
- Слои статусов разведены: `overall()` знает только 4 класса; `unknown` существует единственно в `read_status`.
- Типы согласованы: `Finding.cls` сериализуется как `class` через `as_json`; `run_sensor` — единственный писатель статус-файла; `read_status(path, now) -> int` совпадает между Task 6 (вызов в `main`) и Task 7 (определение).
- Ловушка two-contract-guarantees закрыта: сравнение с `origin/<default>` по SHA из `ls-remote`, локальный чекаут не участвует; тест Task 3 прямо проверяет «видит canon-HEAD, а не отставший клон».
- Ловушка dispatcher#110 закрыта: краш → 4, не 1; тест Task 6 проверяет и код, и нетронутость статус-файла.
