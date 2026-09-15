# E2: стадия Need вызывается прогоном — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make spec-loop … ARGS='--need --frame customer --stakeholder <role>'` запускает интервью discovery из прогона, останавливается в персистентном `waiting_interview`, продолжается повтором той же команды и передаёт готовый бриф в конвейер путём E1.

**Architecture:** Стадия живёт внутри раннера (`_step_interview` перед `_step_branch`), координаты сессии — в `RunState.interview`, вызовы discovery — через три метода порта `Ops` с единой формой `DiscoveryReply`; чистая логика (транспортный контракт, сверка присоединения, печатаемые команды) вынесена в новый модуль `governance/interview.py` без побочных эффектов. `spec_loop` добавляет preflight need-флагов, `--new-run --ws-id` и диспетчер для двух новых статусов.

**Tech Stack:** Python 3.12, stdlib (`json`, `shlex`, `os.replace`), pytest, существующие `governance.brief_input`, `governance.run_state`, `governance.runner`, `governance.spec_loop`, `governance.ops`; discovery CLI соседа через `uv run --frozen --project`.

**Spec:** `docs/superpowers/specs/2026-09-15-need-stage-design.md` (accepted) + три уточнения из комментария PR #244 (роли при attach — множество `participant_role`; `--new-run` без гварда неоднозначности; `traces_to` для customer — без путевых элементов), вносятся в спеку ревизией 5 в Task 11.

## Global Constraints

- Только customer-маршрут исполняется; `--frame engineer` отказывает **до run-id** с текстом «engineer-маршрут ждёт discovery#49» (спека D6). Порт `discovery_start` уже принимает `upstream_path: str | None` и отказывает на непустом значении тем же текстом.
- Прямая запись в `$DISCOVERY_HOME` и чтение `header.json` запрещены; сверки — только по публичному envelope и отрендеренному брифу.
- `discovery start` — ровно один раз на прогон; повтор при существующем леджере — только `status` (orphan-attach — сначала `brief` в tmp).
- Все need-флаги (`--frame`, `--stakeholder`, `--traces-to`, `--session`, `--new-run`) без `--need` — отказ; `--brief`/legacy не меняются.
- Коды discovery: `1 > 2 > 20 > 10 > 11 > 0`; `code = process.returncode` при валидном контракте, иначе синтетический `1` с каноническим synthetic envelope.
- Печатаемые команды — через `shlex.quote`.
- Тесты по слоям: runner+FakeOps, spec_loop, RealOps, opt-in smoke. Negative controls на три гварда: сверка роли при attach, требование кода 0 у `brief`, сравнение байтов при recovery.
- CI-команды: `uv run --frozen pytest -q`; `uv run --frozen --group governance pytest tests/test_governance_steward_surface.py tests/test_governance_stale_adapter.py tests/test_governance_bundle_state.py -q`; `make plan-check-selftest`; `make plan-check`.
- Коммиты — с `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Ветка `feat/need-stage` от `master`.

---

## File Structure

| файл | ответственность |
|---|---|
| `governance/interview.py` (create) | чистые помощники: `DiscoveryReply`, `parse_reply` (транспортный контракт), `InterviewSpec`, `answer_command`/`supersede_template`, `attach_findings` (сверка присоединения по брифу), `H1` |
| `governance/run_state.py` (modify) | поле `interview: dict \| None`, параметр `new_run(interview=…)` |
| `governance/ops.py` (modify) | протокол `Ops.discovery_start/status/brief`, `RealOps` — argv + `parse_reply` |
| `governance/runner.py` (modify) | `start(..., interview_spec=…)`, `_step_interview`, обработка `waiting_interview`/`stopped_interview` в `resume`, `attach_session` |
| `governance/spec_loop.py` (modify) | need-флаги и preflight, `--new-run --ws-id`, диспетчер новых статусов, подсказки |
| `tests/test_governance_interview.py` (create) | помощники и транспорт |
| `tests/test_governance_runner.py` (modify) | машина состояний, crash-recovery, attach |
| `tests/test_governance_spec_loop.py` (modify) | CLI-preflight, диспетчер, `--new-run` |
| `tests/test_governance_ops.py` (modify) | argv трёх команд |
| `tests/test_discovery_smoke.py` (create) | opt-in smoke с настоящим discovery |
| `docs/superpowers/specs/2026-09-15-need-stage-design.md` (modify) | ревизия 5 |
| `TODO.md` (modify) | состояние пункта `spec-loop-need-stage` |

---

### Task 1: `governance/interview.py` — `DiscoveryReply` и транспортный контракт

**Files:**
- Create: `governance/interview.py`
- Test: `tests/test_governance_interview.py`

**Interfaces:**
- Produces: `DiscoveryReply(code: int, envelope: dict, stderr: str)` (frozen dataclass); `parse_reply(returncode: int, stdout: str, stderr: str) -> DiscoveryReply`; константа `SYNTHETIC_REASON_PREFIX = "граница discovery:"`; `KNOWN_CODES = (0, 1, 2, 10, 11, 20)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_governance_interview.py
from __future__ import annotations

import json

import pytest

from governance import interview


def _envelope(**over):
    base = {
        "lifecycle": "awaiting_input", "gate": "unknown", "readiness": "unknown",
        "next_action": {"session_id": "s-1", "question_id": "Q-01",
                        "coverage_key": "goals", "question_text": "?"},
        "findings": [], "readiness_findings": [],
        "operation": {"status": "ok", "reason": ""},
    }
    base.update(over)
    return base


def test_valid_contract_keeps_process_returncode() -> None:
    reply = interview.parse_reply(20, json.dumps(_envelope()), "")
    assert reply.code == 20
    assert reply.envelope["next_action"]["session_id"] == "s-1"


@pytest.mark.parametrize("stdout", ["", "not json", "[1,2]", "42"])
def test_unparsable_or_non_object_stdout_is_synthetic_one(stdout: str) -> None:
    reply = interview.parse_reply(0, stdout, "boom")
    assert reply.code == 1
    assert reply.envelope["operation"]["status"] == "unknown"
    assert reply.envelope["operation"]["reason"].startswith(
        interview.SYNTHETIC_REASON_PREFIX
    )
    assert reply.envelope["lifecycle"] == "unknown"
    assert reply.stderr == "boom"


def test_missing_required_field_is_synthetic_one() -> None:
    env = _envelope()
    del env["readiness_findings"]
    reply = interview.parse_reply(20, json.dumps(env), "")
    assert reply.code == 1
    assert "readiness_findings" in reply.envelope["operation"]["reason"]


def test_unknown_returncode_is_synthetic_one() -> None:
    reply = interview.parse_reply(7, json.dumps(_envelope()), "")
    assert reply.code == 1
    assert "7" in reply.envelope["operation"]["reason"]


def test_code_20_without_session_or_question_is_synthetic_one() -> None:
    env = _envelope(next_action={"question_id": "Q-01"})
    reply = interview.parse_reply(20, json.dumps(env), "")
    assert reply.code == 1
    assert "session_id" in reply.envelope["operation"]["reason"]


def test_synthetic_envelope_has_protocol_shape() -> None:
    reply = interview.parse_reply(0, "", "")
    assert set(reply.envelope) == set(interview.REQUIRED_FIELDS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_interview.py`
Expected: FAIL — `ModuleNotFoundError: governance.interview`

- [ ] **Step 3: Write minimal implementation**

```python
# governance/interview.py
"""Стадия Need (E2): чистые помощники без побочных эффектов.

Спека: docs/superpowers/specs/2026-09-15-need-stage-design.md.
Транспортный контракт (§6): `code = process.returncode` при валидном
контракте discovery; любая негодная форма — синтетический 1 с каноническим
synthetic envelope формы протокола. Это проверка согласованности границы,
НЕ второй вычислитель `protocol.exit_code` соседа.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

KNOWN_CODES: tuple[int, ...] = (0, 1, 2, 10, 11, 20)
REQUIRED_FIELDS: tuple[str, ...] = (
    "lifecycle", "gate", "readiness", "next_action",
    "findings", "readiness_findings", "operation",
)
SYNTHETIC_REASON_PREFIX = "граница discovery:"


@dataclass(frozen=True)
class DiscoveryReply:
    """Ответ одного вызова discovery CLI после проверки границы."""

    code: int
    envelope: dict
    stderr: str


def synthetic_envelope(reason: str) -> dict:
    """Канонический envelope синтетического кода 1 (форма протокола)."""
    return {
        "lifecycle": "unknown", "gate": "unknown", "readiness": "unknown",
        "next_action": {}, "findings": [], "readiness_findings": [],
        "operation": {
            "status": "unknown",
            "reason": f"{SYNTHETIC_REASON_PREFIX} {reason}",
        },
    }


def parse_reply(returncode: int, stdout: str, stderr: str) -> DiscoveryReply:
    """Проверка границы: валидный контракт → код процесса, иначе синтетический 1."""
    try:
        envelope = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError as exc:
        return DiscoveryReply(1, synthetic_envelope(f"stdout не JSON ({exc})"), stderr)
    if not isinstance(envelope, dict):
        return DiscoveryReply(1, synthetic_envelope("stdout не JSON-object"), stderr)
    missing = [f for f in REQUIRED_FIELDS if f not in envelope]
    if missing:
        return DiscoveryReply(
            1, synthetic_envelope(f"нет обязательных полей {missing}"), stderr
        )
    if returncode not in KNOWN_CODES:
        return DiscoveryReply(
            1, synthetic_envelope(f"неизвестный код {returncode}"), stderr
        )
    if returncode == 20:
        action = envelope.get("next_action")
        if not isinstance(action, dict) or not all(
            isinstance(action.get(k), str) and action.get(k)
            for k in ("session_id", "question_id")
        ):
            return DiscoveryReply(
                1,
                synthetic_envelope(
                    "код 20 без next_action.session_id/question_id"
                ),
                stderr,
            )
    return DiscoveryReply(returncode, envelope, stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_interview.py`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/interview.py tests/test_governance_interview.py
git commit -m "feat(interview): DiscoveryReply и транспортный контракт границы discovery (E2, Task 1)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `interview.py` — координаты, печатаемые команды, сверка присоединения

**Files:**
- Modify: `governance/interview.py`
- Test: `tests/test_governance_interview.py`

**Interfaces:**
- Produces: `InterviewSpec(frame: str, stakeholder_role: str, target: str, traces_to: str | None, upstream_blob: str | None)` с методом `as_state() -> dict` (все поля + `session_id: None`, `brief_rel: "brief-input/00-discovery/brief.md"`, `started_at`, `completed_at: None`); `answer_command(session_id, role, answer_file="answer.yaml") -> str`; `supersede_template(session_id, role) -> str` (содержит литерал `<QUESTION_ID>`); `h1_line(target, frame) -> str`; `attach_findings(brief_text: str, spec: InterviewSpec) -> list[str]` (пусто = совпало); `brief_coordinate_findings(brief_text, spec) -> list[str]` (без ролей — штатный путь `brief` 0).

- [ ] **Step 1: Write the failing tests**

```python
# добавить в tests/test_governance_interview.py
import shlex
import yaml  # уже зависимость devtools (используется governance)


def _spec(**over):
    base = dict(frame="customer", stakeholder_role="product owner",
                target="owner/alpha", traces_to=None, upstream_blob=None)
    base.update(over)
    return interview.InterviewSpec(**base)


def _brief(frame="customer", target="owner/alpha", roles=(), traces=()):
    meta = {"interview": {"frame": frame,
                          "sessions": [{"participant_role": r} for r in roles]},
            "traces_to": list(traces), "status": "draft"}
    return (
        "---\n" + yaml.safe_dump(meta, allow_unicode=True) + "---\n\n"
        + interview.h1_line(target, frame) + "\n\n## Goals\n"
    )


def test_answer_command_is_shell_safe() -> None:
    cmd = interview.answer_command("s-1", "product owner", "my answer.yaml")
    assert cmd.startswith("discovery answer --session s-1 --role ")
    assert shlex.split(cmd)[shlex.split(cmd).index("--role") + 1] == "product owner"
    assert "--file" in cmd and "my answer.yaml" in shlex.split(cmd)


def test_supersede_template_keeps_question_placeholder() -> None:
    cmd = interview.supersede_template("s-1", "qa")
    assert "--question <QUESTION_ID>" in cmd and "--supersede" in cmd


def test_attach_accepts_empty_or_exact_role_set() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(), spec) == []
    assert interview.attach_findings(_brief(roles=("product owner",)), spec) == []
    # тот же участник дважды в списке недопустим по построению рендера, но
    # множество ролей ⊆ {stakeholder} — критерий по контракту, не по рендеру
    assert interview.attach_findings(
        _brief(roles=("product owner", "product owner")), spec
    ) == []


def test_attach_rejects_foreign_or_extra_role() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(roles=("qa",)), spec)
    assert interview.attach_findings(_brief(roles=("product owner", "qa")), spec)


def test_attach_rejects_h1_frame_and_traces_mismatch() -> None:
    spec = _spec()
    assert interview.attach_findings(_brief(target="owner/beta"), spec)
    assert interview.attach_findings(_brief(frame="engineer"), spec)
    # customer: путевой элемент traces_to — расхождение (как у E1)
    assert interview.attach_findings(_brief(traces=("up.md",)), spec)
    # KB-ссылки для customer законны
    assert interview.attach_findings(_brief(traces=("[[kb-note]]",)), spec) == []


def test_brief_coordinates_ignore_roles() -> None:
    spec = _spec()
    assert interview.brief_coordinate_findings(_brief(roles=("qa",)), spec) == []
    assert interview.brief_coordinate_findings(_brief(target="owner/beta"), spec)


def test_engineer_traces_must_equal_recorded_ref() -> None:
    spec = _spec(frame="engineer", traces_to="customer.md")
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=("customer.md",)), spec) == []
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=("other.md",)), spec)
    assert interview.brief_coordinate_findings(
        _brief(frame="engineer", traces=()), spec)


def test_as_state_carries_pins_and_no_session() -> None:
    st = _spec().as_state()
    assert st["session_id"] is None and st["completed_at"] is None
    assert st["brief_rel"] == "brief-input/00-discovery/brief.md"
    assert st["stakeholder_role"] == "product owner"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_interview.py`
Expected: FAIL — `AttributeError: module 'governance.interview' has no attribute 'InterviewSpec'`

- [ ] **Step 3: Write minimal implementation**

```python
# добавить в governance/interview.py
import re
import shlex
from datetime import datetime, timezone

import yaml

BRIEF_REL = "brief-input/00-discovery/brief.md"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass(frozen=True)
class InterviewSpec:
    """Координаты интервью, фиксируемые при старте (спека §4)."""

    frame: str
    stakeholder_role: str
    target: str
    traces_to: str | None
    upstream_blob: str | None

    def as_state(self) -> dict:
        return {
            "session_id": None,
            "frame": self.frame,
            "stakeholder_role": self.stakeholder_role,
            "target": self.target,
            "traces_to": self.traces_to,
            "upstream_blob": self.upstream_blob,
            "brief_rel": BRIEF_REL,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "completed_at": None,
        }

    @classmethod
    def from_state(cls, st: dict) -> "InterviewSpec":
        return cls(
            frame=st["frame"], stakeholder_role=st["stakeholder_role"],
            target=st["target"], traces_to=st.get("traces_to"),
            upstream_blob=st.get("upstream_blob"),
        )


def h1_line(target: str, frame: str) -> str:
    """Точная форма H1 рендера discovery (`render.py:371`)."""
    return f"# Discovery Brief — {target} ({frame}-фрейм)"


def answer_command(session_id: str, role: str, answer_file: str = "answer.yaml") -> str:
    """Точная команда ответа стейкхолдера (D1); shell-safe."""
    return " ".join([
        "discovery", "answer", "--session", shlex.quote(session_id),
        "--role", shlex.quote(role), "--file", shlex.quote(answer_file),
    ])


def supersede_template(session_id: str, role: str) -> str:
    """Шаблон повторного ответа при 10/11: question_id подставляет человек."""
    return " ".join([
        "discovery", "answer", "--session", shlex.quote(session_id),
        "--role", shlex.quote(role), "--question", "<QUESTION_ID>",
        "--supersede", "--file", shlex.quote("answer.yaml"),
    ])


def _parse_brief(text: str) -> tuple[dict, str | None]:
    """(frontmatter, первая строка H1) — без валидации контракта."""
    m = _FRONTMATTER_RE.match(text)
    meta = yaml.safe_load(m.group(1)) if m else {}
    if not isinstance(meta, dict):
        meta = {}
    h1 = next((ln for ln in text.splitlines() if ln.startswith("# ")), None)
    return meta, h1


def _traces(meta: dict) -> list[str]:
    raw = meta.get("traces_to") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def _is_path_ref(ref: str) -> bool:
    return ref.endswith(".md") and not ref.startswith("[[")


def brief_coordinate_findings(brief_text: str, spec: InterviewSpec) -> list[str]:
    """Сверка H1 / frame / traces_to — штатный путь `brief` 0 (ролей нет: D3)."""
    meta, h1 = _parse_brief(brief_text)
    findings: list[str] = []
    expected_h1 = h1_line(spec.target, spec.frame)
    if h1 != expected_h1:
        findings.append(f"H1 {h1!r} ≠ {expected_h1!r}")
    frame = (meta.get("interview") or {}).get("frame") if isinstance(
        meta.get("interview"), dict) else None
    if frame != spec.frame:
        findings.append(f"interview.frame {frame!r} ≠ {spec.frame!r}")
    paths = [t for t in _traces(meta) if _is_path_ref(t)]
    if spec.frame == "customer" and paths:
        findings.append(f"customer-бриф с путевыми traces_to {paths!r}")
    if spec.frame == "engineer" and paths != [spec.traces_to]:
        findings.append(f"traces_to {paths!r} ≠ [{spec.traces_to!r}]")
    return findings


def attach_findings(brief_text: str, spec: InterviewSpec) -> list[str]:
    """Сверка при присоединении `--session`: координаты + роли (§5.3)."""
    findings = brief_coordinate_findings(brief_text, spec)
    meta, _ = _parse_brief(brief_text)
    sessions = (meta.get("interview") or {}).get("sessions") or []
    roles = {
        s.get("participant_role") for s in sessions if isinstance(s, dict)
    }
    if not roles <= {spec.stakeholder_role}:
        findings.append(
            f"роли участников {sorted(r for r in roles if r)!r} ⊄ "
            f"{{{spec.stakeholder_role!r}}}"
        )
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_interview.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add governance/interview.py tests/test_governance_interview.py
git commit -m "feat(interview): InterviewSpec, shell-safe команды ответа, сверка координат и присоединения (E2, Task 2)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `RunState.interview`

**Files:**
- Modify: `governance/run_state.py` (поля `RunState` после `disp_anchor_dir`; `new_run`)
- Test: `tests/test_governance_run_state.py`

**Interfaces:**
- Produces: `RunState.interview: dict | None = None`; `new_run(..., interview: dict | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# добавить в tests/test_governance_run_state.py
def test_new_run_carries_interview_state_and_old_ledgers_load(runs_root) -> None:
    from governance import run_state as rs
    st = rs.new_run(
        subject="s", repo="alpha", repo_slug="o/alpha", ws_id="WS-1",
        target_dir="/tmp/x", bundle_dir="spec",
        profile="profiles/team-exp.yaml", run_id="r-int",
        interview={"session_id": None, "frame": "customer"},
    )
    rs.save(st)
    assert rs.load("r-int").interview == {"session_id": None, "frame": "customer"}
    # старый run.json без поля читается через default
    raw = (rs.run_dir("r-int") / "run.json").read_text(encoding="utf-8")
    import json
    data = json.loads(raw); del data["interview"]
    (rs.run_dir("r-int") / "run.json").write_text(json.dumps(data), encoding="utf-8")
    assert rs.load("r-int").interview is None
```

(Если в файле нет фикстуры `runs_root` — скопировать из `tests/test_governance_runner.py:438`: `monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest -q tests/test_governance_run_state.py -k interview_state`
Expected: FAIL — `TypeError: new_run() got an unexpected keyword argument 'interview'`

- [ ] **Step 3: Write minimal implementation**

```python
# governance/run_state.py — после поля disp_anchor_dir в RunState:
    # Координаты стадии Need (E2, спека §4): session_id, frame,
    # stakeholder_role, target, traces_to, upstream_blob, brief_rel,
    # started_at, completed_at. None — прогон без интервью (E1/legacy).
    interview: dict | None = None

# в new_run: параметр
    interview: dict | None = None,
# и в конструкторе RunState(...): interview=interview,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_run_state.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add governance/run_state.py tests/test_governance_run_state.py
git commit -m "feat(run_state): поле interview (E2, Task 3)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: порт `Ops.discovery_*` и `RealOps`

**Files:**
- Modify: `governance/ops.py` (протокол `Ops` рядом с `author_disp`; `RealOps` после `author_disp`)
- Test: `tests/test_governance_ops.py`

**Interfaces:**
- Produces:
  ```python
  def discovery_start(self, frame: str, target: str, traces_to: str | None,
                      upstream_path: str | None, cwd: str) -> interview.DiscoveryReply
  def discovery_status(self, session_id: str, cwd: str) -> interview.DiscoveryReply
  def discovery_brief(self, session_id: str, out_path: str, cwd: str) -> interview.DiscoveryReply
  ```
  `cwd` — каталог прогона (`run_dir`). `ENGINEER_BLOCKED = "engineer-маршрут ждёт discovery#49 (приём upstream при start)"`.

- [ ] **Step 1: Write the failing tests**

```python
# добавить в tests/test_governance_ops.py (используются ops_mod, RealOps, monkeypatch как в файле)
def _capture(monkeypatch, returncode=20, stdout=None):
    from types import SimpleNamespace
    seen: list[dict] = []
    payload = stdout if stdout is not None else json.dumps({
        "lifecycle": "awaiting_input", "gate": "unknown", "readiness": "unknown",
        "next_action": {"session_id": "s-9", "question_id": "Q-01"},
        "findings": [], "readiness_findings": [],
        "operation": {"status": "ok", "reason": ""}})

    def fake_run(argv, **kwargs):
        seen.append({"argv": list(argv), **kwargs})
        return SimpleNamespace(returncode=returncode, stdout=payload, stderr="")

    monkeypatch.setattr(ops_mod.subprocess, "run", fake_run)
    return seen


def test_discovery_start_argv_and_boundary(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    reply = RealOps().discovery_start("customer", "o/alpha", None, None, str(tmp_path))
    argv = seen[0]["argv"]
    assert argv[:5] == ["uv", "run", "--frozen", "--project",
                        str(ops_mod.DEVTOOLS_ROOT.parent / "discovery")]
    assert argv[5:] == ["discovery", "start", "--frame", "customer", "--target", "o/alpha"]
    assert seen[0]["cwd"] == str(tmp_path)
    assert seen[0]["capture_output"] is True and seen[0]["text"] is True
    assert reply.code == 20 and reply.envelope["next_action"]["session_id"] == "s-9"


def test_discovery_start_engineer_traces_to_argv(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    RealOps().discovery_start("engineer", "o/alpha", "customer.md", None, str(tmp_path))
    assert seen[0]["argv"][-2:] == ["--traces-to", "customer.md"]


def test_discovery_start_refuses_upstream_until_inbox(monkeypatch, tmp_path):
    seen = _capture(monkeypatch)
    reply = RealOps().discovery_start(
        "engineer", "o/alpha", "customer.md", str(tmp_path / "customer.md"), str(tmp_path))
    assert seen == []  # сосед не вызван
    assert reply.code == 1 and "discovery#49" in reply.envelope["operation"]["reason"]


def test_discovery_status_and_brief_argv(monkeypatch, tmp_path):
    seen = _capture(monkeypatch, returncode=0, stdout=json.dumps({
        "lifecycle": "complete", "gate": "pass", "readiness": "ready",
        "next_action": {}, "findings": [], "readiness_findings": [],
        "operation": {"status": "ok", "reason": ""}}))
    RealOps().discovery_status("s-9", str(tmp_path))
    RealOps().discovery_brief("s-9", str(tmp_path / "out.md"), str(tmp_path))
    assert seen[0]["argv"][5:] == ["discovery", "status", "--session", "s-9"]
    assert seen[1]["argv"][5:] == ["discovery", "brief", "--session", "s-9",
                                   "--out", str(tmp_path / "out.md")]


def test_discovery_reply_is_synthetic_on_bad_stdout(monkeypatch, tmp_path):
    _capture(monkeypatch, returncode=0, stdout="garbage")
    reply = RealOps().discovery_status("s-9", str(tmp_path))
    assert reply.code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_ops.py -k discovery`
Expected: FAIL — `AttributeError: 'RealOps' object has no attribute 'discovery_start'`

- [ ] **Step 3: Write minimal implementation**

```python
# governance/ops.py — импорт вверху рядом с другими governance-импортами:
from governance import interview as _interview

ENGINEER_BLOCKED = "engineer-маршрут ждёт discovery#49 (приём upstream при start)"

# в class Ops(Protocol), после author_disp:
    def discovery_start(
        self, frame: str, target: str, traces_to: str | None,
        upstream_path: str | None, cwd: str,
    ) -> _interview.DiscoveryReply: ...

    def discovery_status(self, session_id: str, cwd: str) -> _interview.DiscoveryReply: ...

    def discovery_brief(
        self, session_id: str, out_path: str, cwd: str
    ) -> _interview.DiscoveryReply: ...

# в class RealOps, после author_disp:
    def _discovery(self, args: list[str], cwd: str) -> _interview.DiscoveryReply:
        """Один вызов discovery CLI соседа + проверка границы (спека §6).

        `--frozen --project`: тот же способ, что у disputatio (`author_disp`).
        stdout захватывается целиком — envelope один на вызов; stderr
        сохраняется для диагностики, но в контракт не входит.
        """
        argv = [
            "uv", "run", "--frozen", "--project",
            str(DEVTOOLS_ROOT.parent / "discovery"), "discovery", *args,
        ]
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        return _interview.parse_reply(done.returncode, done.stdout, done.stderr)

    def discovery_start(
        self, frame: str, target: str, traces_to: str | None,
        upstream_path: str | None, cwd: str,
    ) -> _interview.DiscoveryReply:
        """`discovery start`. `upstream_path` — durable-копия из run_dir; до
        discovery#49 сосед upstream не принимает — отказ ДО вызова, тем же
        текстом, что preflight spec-loop (порт после разблокировки не меняется)."""
        if upstream_path is not None:
            return _interview.DiscoveryReply(
                1, _interview.synthetic_envelope(ENGINEER_BLOCKED), ""
            )
        args = ["start", "--frame", frame, "--target", target]
        if traces_to:
            args += ["--traces-to", traces_to]
        return self._discovery(args, cwd)

    def discovery_status(self, session_id: str, cwd: str) -> _interview.DiscoveryReply:
        return self._discovery(["status", "--session", session_id], cwd)

    def discovery_brief(
        self, session_id: str, out_path: str, cwd: str
    ) -> _interview.DiscoveryReply:
        return self._discovery(
            ["brief", "--session", session_id, "--out", out_path], cwd
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_ops.py -k discovery`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add governance/ops.py tests/test_governance_ops.py
git commit -m "feat(ops): порт discovery_start/status/brief с проверкой границы; upstream до discovery#49 — отказ (E2, Task 4)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `runner.start(interview_spec=…)` — старт интервью без ветки

**Files:**
- Modify: `governance/runner.py` (`start`, `advance` — первый шаг `_step_interview`), `tests/test_governance_runner.py` (`FakeOps`)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: `interview.InterviewSpec`, `interview.DiscoveryReply`, `Ops.discovery_*` (Task 1–4).
- Produces: `start(..., interview_spec: interview.InterviewSpec | None = None)`; `_step_interview(state, ops) -> bool`; `FakeOps.discovery: list[tuple[str, interview.DiscoveryReply]]` — очередь ответов `("start"|"status"|"brief", reply)`; `FakeOps.discovery_calls: list[tuple]`; статусы `"waiting_interview"`, `"stopped_interview"`; `INTERVIEW_START = "interview-start"`, `INTERVIEW_BRIEF = "interview-brief"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_governance_runner.py — расширить FakeOps (поля + методы):
    discovery: list[tuple[str, Any]] = field(default_factory=list)
    discovery_calls: list[tuple] = field(default_factory=list)

    def _discovery_reply(self, kind: str):
        assert self.discovery and self.discovery[0][0] == kind, (
            f"неожиданный вызов discovery {kind!r}; очередь {self.discovery!r}")
        return self.discovery.pop(0)[1]

    def discovery_start(self, frame, target, traces_to, upstream_path, cwd):
        self.discovery_calls.append(("start", frame, target, traces_to, upstream_path))
        return self._discovery_reply("start")

    def discovery_status(self, session_id, cwd):
        self.discovery_calls.append(("status", session_id))
        return self._discovery_reply("status")

    def discovery_brief(self, session_id, out_path, cwd):
        self.discovery_calls.append(("brief", session_id, out_path))
        reply = self._discovery_reply("brief")
        # стенд пишет артефакт при кодах 0/10/11/20, как сосед
        if reply.code in (0, 10, 11, 20):
            Path(out_path).write_text(self.brief_text, encoding="utf-8")
        return reply

    brief_text: str = ""

# помощники модуля (рядом с _start_kwargs):
from governance import interview as iv

def _reply(code, **over):
    env = {"lifecycle": "awaiting_input", "gate": "unknown", "readiness": "unknown",
           "next_action": {"session_id": "s-1", "question_id": "Q-01"},
           "findings": [], "readiness_findings": [],
           "operation": {"status": "ok", "reason": ""}}
    if code == 0:
        env.update(lifecycle="complete", gate="pass", readiness="ready", next_action={})
    if code in (10, 11):
        env.update(lifecycle="complete", gate="fail" if code == 10 else "pass",
                   readiness="incomplete", next_action={},
                   findings=[{"rule": "GC-04", "message": "x"}])
    if code in (1, 2):
        env.update(lifecycle="unknown", operation={"status": "refused", "reason": "boom"})
    env.update(over)
    return iv.DiscoveryReply(code, env, "")

def _need_spec(**over):
    base = dict(frame="customer", stakeholder_role="po", target="owner/alpha",
                traces_to=None, upstream_blob=None)
    base.update(over)
    return iv.InterviewSpec(**base)


def test_need_start_20_waits_without_branch(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(20))])
    state = runner.start(**_start_kwargs(tmp_path, "r-need-1", ops), interview_spec=_need_spec())
    assert state.status == "waiting_interview"
    assert state.interview["session_id"] == "s-1"
    assert state.ops["interview-start"]["status"] == "completed"
    assert ops.discovery_calls == [("start", "customer", "owner/alpha", None, None)]
    assert not any(c[0] == "restore" for c in ops.calls)  # ensure_branch не звался
    assert state.branch == ""
    assert rs.load("r-need-1").status == "waiting_interview"


@pytest.mark.parametrize("code", [1, 2, 0, 10, 11])
def test_need_start_non_20_stops_without_session(tmp_path, runs_root, code) -> None:
    ops = FakeOps(discovery=[("start", _reply(code))])
    state = runner.start(**_start_kwargs(tmp_path, f"r-need-{code}", ops),
                         interview_spec=_need_spec())
    assert state.status == "stopped_interview"
    assert state.interview["session_id"] is None
    assert state.ops["interview-start"]["status"] == "started"
    assert not any(c[0] == "restore" for c in ops.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k need_start`
Expected: FAIL — `TypeError: start() got an unexpected keyword argument 'interview_spec'`

- [ ] **Step 3: Write minimal implementation**

```python
# governance/runner.py — импорт:
from governance import interview as iv

INTERVIEW_START = "interview-start"
INTERVIEW_BRIEF = "interview-brief"

# start(): параметр `interview_spec: iv.InterviewSpec | None = None`,
# после brief_descriptor:
    if interview_spec is not None and brief_source is not None:
        raise ValueError("--need и --brief взаимоисключающи")
    state = new_run(..., brief=brief_descriptor,
                    interview=interview_spec.as_state() if interview_spec else None)

# advance(): steps = (_step_interview, _step_branch, ...)

def _interview_stop(state: RunState, reason: str) -> bool:
    """Персистентный стоп стадии Need; координаты и session_id не трогаются."""
    print(f"_step_interview: {reason}")
    state.status = "stopped_interview"
    save(state)
    return False


def _print_answer_hint(state: RunState, reply: iv.DiscoveryReply) -> None:
    action = reply.envelope.get("next_action", {})
    print(f"_step_interview: вопрос {action.get('question_id')}: "
          f"{action.get('question_text', '')}")
    print("ответьте вне spec-loop и повторите команду:")
    print("  " + iv.answer_command(
        state.interview["session_id"], state.interview["stakeholder_role"]))


def _step_interview(state: RunState, ops: Ops) -> bool:
    """S0.5 — стадия Need (спека §5). Прогон без interview проходит насквозь."""
    if state.interview is None or state.interview.get("completed_at"):
        return True
    spec = iv.InterviewSpec.from_state(state.interview)
    cwd = str(run_dir(state.run_id))
    if op_status(state, INTERVIEW_START) != "completed":
        if state.interview.get("session_id") is None and op_status(
            state, INTERVIEW_START) == "started":
            # сирота: сессия могла быть создана без записи — discovery не зовём
            state.status = "stopped_interview"
            save(state)
            print("_step_interview: сессия могла быть создана без записи — "
                  "присоедините её: --session <id>, либо --new-run --ws-id <fresh-id>")
            return False
        _ensure_started(state, INTERVIEW_START)
        if spec.frame == "engineer" and spec.upstream_blob is None:
            return _interview_stop(state, ENGINEER_BLOCKED_TEXT)
        reply = ops.discovery_start(
            spec.frame, spec.target, spec.traces_to,
            _upstream_path(state, spec), cwd,
        )
        if reply.code != 20:
            return _interview_stop(
                state, f"start вернул {reply.code}: "
                f"{reply.envelope['operation'].get('reason', '')}")
        state.interview["session_id"] = reply.envelope["next_action"]["session_id"]
        state.status = "waiting_interview"
        op_complete(state, INTERVIEW_START, session_id=state.interview["session_id"])
        _print_answer_hint(state, reply)
        return False
    return _interview_poll(state, ops, spec, cwd)   # Task 6


def _upstream_path(state: RunState, spec: iv.InterviewSpec) -> str | None:
    if spec.frame != "engineer":
        return None
    return str(run_dir(state.run_id) / "brief-input" / "00-discovery" / spec.traces_to)

ENGINEER_BLOCKED_TEXT = "engineer-маршрут ждёт discovery#49 (приём upstream при start)"
```

Пока `_interview_poll` — заглушка `return _interview_stop(state, "poll не реализован")` (Task 6 заменяет).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k need_start`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the whole runner file to confirm no regressions**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): старт стадии Need — waiting_interview без ветки; start≠20 — stopped_interview без сессии (E2, Task 5)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `resume` и опрос `status` (20 / 10, 11 / 1, 2 / чужой session_id)

**Files:**
- Modify: `governance/runner.py` (`resume`, `_interview_poll`)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: Task 5.
- Produces: `resume(run_id, ops)` для `waiting_interview`/`stopped_interview` (с записанным `session_id`) ставит `running` и зовёт `advance` → `_step_interview` → `_interview_poll`; для `stopped_interview` без `session_id` возвращает состояние без вызова discovery. `INTERVIEW_FINDINGS = "interview-findings.txt"`.

- [ ] **Step 1: Write the failing tests**

```python
def _waiting_run(tmp_path, runs_root, run_id, extra_replies):
    ops = FakeOps(discovery=[("start", _reply(20)), *extra_replies])
    state = runner.start(**_start_kwargs(tmp_path, run_id, ops), interview_spec=_need_spec())
    assert state.status == "waiting_interview"
    return ops, state


def test_status_20_from_waiting_keeps_ledger_bytes(tmp_path, runs_root, capsys) -> None:
    ops, _ = _waiting_run(tmp_path, runs_root, "r-w20", [("status", _reply(20))])
    before = (rs.run_dir("r-w20") / "run.json").read_bytes()
    state = runner.resume("r-w20", ops)
    assert state.status == "waiting_interview"
    assert (rs.run_dir("r-w20") / "run.json").read_bytes() == before
    assert "discovery answer --session s-1 --role po" in capsys.readouterr().out


def test_status_20_with_foreign_session_id_stops(tmp_path, runs_root) -> None:
    ops, _ = _waiting_run(tmp_path, runs_root, "r-w-foreign",
                          [("status", _reply(20, next_action={"session_id": "s-9", "question_id": "Q-02"}))])
    assert runner.resume("r-w-foreign", ops).status == "stopped_interview"


@pytest.mark.parametrize("code", [10, 11])
def test_status_10_11_stops_with_findings_and_template(tmp_path, runs_root, code, capsys) -> None:
    ops, _ = _waiting_run(tmp_path, runs_root, f"r-w{code}", [("status", _reply(code))])
    state = runner.resume(f"r-w{code}", ops)
    assert state.status == "stopped_interview"
    assert "GC-04" in (rs.run_dir(f"r-w{code}") / "interview-findings.txt").read_text()
    assert "--question <QUESTION_ID> --supersede" in capsys.readouterr().out


def test_stopped_then_status_20_returns_to_waiting(tmp_path, runs_root) -> None:
    ops, _ = _waiting_run(tmp_path, runs_root, "r-s20",
                          [("status", _reply(10)), ("status", _reply(20))])
    assert runner.resume("r-s20", ops).status == "stopped_interview"
    state = runner.resume("r-s20", ops)
    assert state.status == "waiting_interview"
    assert not (rs.run_dir("r-s20") / "interview-findings.txt").exists()


@pytest.mark.parametrize("code", [1, 2])
def test_status_1_2_stops_and_keeps_session(tmp_path, runs_root, code) -> None:
    ops, _ = _waiting_run(tmp_path, runs_root, f"r-e{code}", [("status", _reply(code))])
    state = runner.resume(f"r-e{code}", ops)
    assert state.status == "stopped_interview"
    assert state.interview["session_id"] == "s-1"


def test_orphan_stop_resume_does_not_call_discovery(tmp_path, runs_root, capsys) -> None:
    ops = FakeOps(discovery=[("start", _reply(2))])
    runner.start(**_start_kwargs(tmp_path, "r-orphan", ops), interview_spec=_need_spec())
    state = runner.resume("r-orphan", ops)
    assert state.status == "stopped_interview"
    assert [c for c in ops.discovery_calls if c[0] != "start"] == []
    assert "--session" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "status_ or orphan_stop"`
Expected: FAIL (resume не знает новых статусов / poll-заглушка)

- [ ] **Step 3: Write minimal implementation**

```python
# runner.resume(): перед `if state.status == "stopped_author":`
    if state.status in ("waiting_interview", "stopped_interview"):
        if state.interview and state.interview.get("session_id") is None:
            # сирота — discovery не вызывается (§5.2)
            print("resume: стадия Need без записанной сессии — присоедините: "
                  "--session <id>, либо --new-run --ws-id <fresh-id>")
            return state
        state.status = "running"
        save(state)
        return advance(state, ops)

INTERVIEW_FINDINGS = "interview-findings.txt"


def _interview_poll(state, ops, spec, cwd) -> bool:
    session_id = state.interview["session_id"]
    findings_file = run_dir(state.run_id) / INTERVIEW_FINDINGS
    if op_status(state, INTERVIEW_BRIEF) != "new":
        return _interview_publish(state, ops, spec, cwd)   # Task 7/8
    reply = ops.discovery_status(session_id, cwd)
    return _interview_after_reply(state, ops, spec, cwd, reply, "status")


def _interview_after_reply(state, ops, spec, cwd, reply, call) -> bool:
    """Общая таблица переходов для `status` и `brief` (§5.1)."""
    session_id = state.interview["session_id"]
    findings_file = run_dir(state.run_id) / INTERVIEW_FINDINGS
    reason = reply.envelope.get("operation", {}).get("reason", "")
    if reply.code == 20:
        if reply.envelope["next_action"].get("session_id") != session_id:
            return _interview_stop(state, "next_action.session_id ≠ записанной сессии")
        findings_file.unlink(missing_ok=True)
        state.ops.pop(INTERVIEW_BRIEF, None)
        state.status = "waiting_interview"
        save(state)
        _print_answer_hint(state, reply)
        return False
    if reply.code in (10, 11):
        findings_file.write_text(json.dumps(
            {"findings": reply.envelope["findings"],
             "readiness_findings": reply.envelope["readiness_findings"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("повторный ответ по findings (question_id подставьте сами):")
        print("  " + iv.supersede_template(session_id, spec.stakeholder_role))
        return _interview_stop(state, f"{call} вернул {reply.code}: findings в {findings_file}")
    if reply.code in (1, 2):
        return _interview_stop(state, f"{call} вернул {reply.code}: {reason}")
    assert reply.code == 0
    if call == "status":
        return _interview_publish(state, ops, spec, cwd)   # Task 7
    return True  # brief 0 обрабатывает вызывающий (Task 7)
```

`import json` вверху файла, если ещё нет. `_interview_publish` пока заглушка `return _interview_stop(state, "publish не реализован")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "status_ or orphan_stop or need_start"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): resume стадии Need — опрос status: 20 ждёт, 10/11 стоп с findings, 1/2 стоп, чужая сессия стоп, сирота без вызова (E2, Task 6)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: публикация брифа — `status` 0 → `brief` → E1

**Files:**
- Modify: `governance/runner.py` (`_interview_publish`)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: `brief_input.inspect_brief`, `iv.brief_coordinate_findings`, `_step_materialize_brief` (E1, без изменений).
- Produces: `_interview_publish(state, ops, spec, cwd) -> bool`; при успехе `state.brief`, `state.interview["completed_at"]`, `running`, продолжение S1.

- [ ] **Step 1: Write the failing tests**

```python
def _customer_brief_text(target="owner/alpha", roles=("po",)):
    # берётся из tests/test_governance_spec_loop.py::_customer_brief() — gate-passing
    # customer-бриф; здесь дописать H1 discovery и sessions:
    from tests.test_governance_spec_loop import _customer_brief
    text = _customer_brief()
    return text.replace("interview:\n  frame: customer",
                        "interview:\n  frame: customer\n  sessions:\n"
                        + "".join(f"    - participant_role: {r}\n" for r in roles))


def test_status_0_brief_0_publishes_and_continues_by_e1(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0))],
                  brief_text=_customer_brief_text(),
                  review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    runner.start(**_start_kwargs(tmp_path, "r-pub", ops), interview_spec=_need_spec())
    state = runner.resume("r-pub", ops)
    assert state.interview["completed_at"]
    assert state.brief and state.brief["frame"] == "customer"
    brief = rs.run_dir("r-pub") / "brief-input" / "00-discovery" / "brief.md"
    assert brief.exists() and not brief.with_name(".brief.tmp").exists()
    assert state.ops["interview-brief"]["status"] == "completed"
    # E1: source layer материализован в бандл и charter получил brief_context
    assert state.ops["materialize-brief"]["status"] == "completed"
    assert any(c[0] == "author" and c[1] == "charter" for c in ops.calls)


def test_brief_20_returns_to_waiting_without_publish(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(20))],
                  brief_text=_customer_brief_text())
    runner.start(**_start_kwargs(tmp_path, "r-b20", ops), interview_spec=_need_spec())
    state = runner.resume("r-b20", ops)
    assert state.status == "waiting_interview" and state.brief is None
    d = rs.run_dir("r-b20") / "brief-input" / "00-discovery"
    assert not (d / "brief.md").exists() and not (d / ".brief.tmp").exists()
    assert "interview-brief" not in state.ops


@pytest.mark.parametrize("code", [10, 11, 1, 2])
def test_brief_non_zero_stops_without_publish(tmp_path, runs_root, code) -> None:
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(code))],
                  brief_text=_customer_brief_text())
    runner.start(**_start_kwargs(tmp_path, f"r-b{code}", ops), interview_spec=_need_spec())
    state = runner.resume(f"r-b{code}", ops)
    assert state.status == "stopped_interview" and state.brief is None
    assert not (rs.run_dir(f"r-b{code}") / "brief-input" / "00-discovery" / "brief.md").exists()


def test_brief_0_failing_inspect_or_coordinates_stops(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0))],
                  brief_text=_customer_brief_text(target="owner/beta"))
    runner.start(**_start_kwargs(tmp_path, "r-bad", ops), interview_spec=_need_spec())
    state = runner.resume("r-bad", ops)
    assert state.status == "stopped_interview" and state.brief is None
    assert not (rs.run_dir("r-bad") / "brief-input" / "00-discovery" / "brief.md").exists()
    assert not any(c[0] == "restore" for c in ops.calls)


def test_brief_0_with_second_participant_role_is_accepted(tmp_path, runs_root) -> None:
    """D3: роль — декларация; второй участник законен на штатном пути."""
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0))],
                  brief_text=_customer_brief_text(roles=("po", "qa")),
                  review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    runner.start(**_start_kwargs(tmp_path, "r-two", ops), interview_spec=_need_spec())
    assert runner.resume("r-two", ops).brief is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "brief_ or status_0"`
Expected: FAIL (publish-заглушка)

- [ ] **Step 3: Write minimal implementation**

```python
def _interview_publish(state, ops, spec, cwd) -> bool:
    """`status` 0 → `brief` в tmp → inspect → replace → state.brief (§5.1/§7)."""
    from datetime import datetime, timezone
    session_id = state.interview["session_id"]
    out_dir = run_dir(state.run_id) / "brief-input" / "00-discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ".brief.tmp"
    final = out_dir / "brief.md"
    _ensure_started(state, INTERVIEW_BRIEF)
    tmp.unlink(missing_ok=True)
    reply = ops.discovery_brief(session_id, str(tmp), cwd)
    if reply.code != 0:
        tmp.unlink(missing_ok=True)
        return _interview_after_reply(state, ops, spec, cwd, reply, "brief")
    try:
        text = tmp.read_text(encoding="utf-8")
    except OSError as exc:
        return _interview_stop(state, f"brief tmp нечитаем: {exc}")
    findings = iv.brief_coordinate_findings(text, spec)
    if findings:
        return _interview_stop(state, "координаты брифа: " + "; ".join(findings))
    try:
        source = brief_input.inspect_brief(tmp)
    except brief_input.BriefInputError as exc:
        return _interview_stop(state, f"бриф не проходит inspect_brief: {exc}")
    os.replace(tmp, final)
    source = brief_input.inspect_brief(final)   # дескриптор — по durable-пути
    state.brief = source.as_state()
    state.interview["completed_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    state.status = "running"
    op_complete(state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs))
    return True
```

`_step_materialize_brief` E1 читает `run_dir/brief-input` через `inspect_materialized(intake_root, ".")` — `brief.md` лежит по `00-discovery/brief.md` = `PRIMARY_REL`, ничего менять не нужно.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "brief_ or status_0 or need"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): публикация брифа стадии Need — tmp → inspect → replace → state.brief → S1 по E1; brief 20/10/11/1/2 без публикации (E2, Task 7)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: crash-recovery `interview-brief` и присоединение `--session`

**Files:**
- Modify: `governance/runner.py` (`_interview_publish` — три окна; `attach_session`)
- Test: `tests/test_governance_runner.py`

**Interfaces:**
- Produces: `attach_session(run_id: str, session_id: str, ops) -> RunState` (публичная функция, зовёт spec_loop); при отказе поднимает `ValueError` с findings.

- [ ] **Step 1: Write the failing tests**

```python
def _published_run(tmp_path, runs_root, run_id, brief_text=None):
    ops = FakeOps(discovery=[("start", _reply(20)), ("status", _reply(0)), ("brief", _reply(0))],
                  brief_text=brief_text or _customer_brief_text(),
                  review_exit=0, facts=GREEN_PR_FACTS, files=GREEN_BUNDLE_FILES)
    runner.start(**_start_kwargs(tmp_path, run_id, ops), interview_spec=_need_spec())
    return ops


def _crash_after(state_run_id, brief_present: bool, tmp_present: bool):
    """Имитация гибели: op started, дескриптора нет."""
    st = rs.load(state_run_id)
    st.brief = None; st.interview["completed_at"] = None; st.status = "running"
    st.ops["interview-brief"] = {"status": "started"}
    rs.save(st)
    d = rs.run_dir(state_run_id) / "brief-input" / "00-discovery"
    if not brief_present: (d / "brief.md").unlink(missing_ok=True)
    if tmp_present: (d / ".brief.tmp").write_text("stale", encoding="utf-8")


def test_crash_without_tmp_or_brief_re_renders(tmp_path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c0")
    _crash_after("r-c0", brief_present=False, tmp_present=False)
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c0", ops)
    assert state.brief is not None and state.ops["interview-brief"]["status"] == "completed"


def test_crash_with_stale_tmp_re_renders(tmp_path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c1")
    _crash_after("r-c1", brief_present=False, tmp_present=True)
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c1", ops)
    assert state.brief is not None
    assert not (rs.run_dir("r-c1") / "brief-input/00-discovery/.brief.tmp").exists()


def test_crash_after_replace_reconciles_by_re_render_equality(tmp_path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c2")
    before = (rs.run_dir("r-c2") / "brief-input/00-discovery/brief.md").read_bytes()
    _crash_after("r-c2", brief_present=True, tmp_present=False)
    ops.discovery = [("brief", _reply(0))]          # тот же brief_text ⇒ байты равны
    state = runner.resume("r-c2", ops)
    assert state.brief is not None
    assert (rs.run_dir("r-c2") / "brief-input/00-discovery/brief.md").read_bytes() == before
    assert [c for c in ops.discovery_calls if c[0] == "brief"][-1][2].endswith(".brief.tmp")


def test_crash_after_replace_with_diverged_render_stops(tmp_path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c3")
    _crash_after("r-c3", brief_present=True, tmp_present=False)
    ops.brief_text = _customer_brief_text(roles=("po", "qa"))   # другие байты
    ops.discovery = [("brief", _reply(0))]
    state = runner.resume("r-c3", ops)
    assert state.status == "stopped_interview" and state.brief is None


def test_crash_after_replace_requires_code_0_on_re_render(tmp_path, runs_root) -> None:
    ops = _published_run(tmp_path, runs_root, "r-c4")
    _crash_after("r-c4", brief_present=True, tmp_present=False)
    ops.discovery = [("brief", _reply(20))]
    assert runner.resume("r-c4", ops).status == "stopped_interview"


def test_attach_session_only_for_orphans_and_verifies_brief(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(2))], brief_text=_customer_brief_text(roles=()))
    runner.start(**_start_kwargs(tmp_path, "r-att", ops), interview_spec=_need_spec())
    ops.discovery = [("brief", _reply(20))]
    state = runner.attach_session("r-att", "s-77", ops)
    assert state.interview["session_id"] == "s-77"
    assert state.ops["interview-start"]["status"] == "completed"
    # повторное присоединение при записанном id — отказ
    with pytest.raises(ValueError):
        runner.attach_session("r-att", "s-78", ops)


def test_attach_session_rejects_foreign_role(tmp_path, runs_root) -> None:
    ops = FakeOps(discovery=[("start", _reply(1))], brief_text=_customer_brief_text(roles=("qa",)))
    runner.start(**_start_kwargs(tmp_path, "r-att-bad", ops), interview_spec=_need_spec())
    ops.discovery = [("brief", _reply(20))]
    with pytest.raises(ValueError):
        runner.attach_session("r-att-bad", "s-77", ops)
    assert rs.load("r-att-bad").interview["session_id"] is None


@pytest.mark.parametrize("code", [1, 2])
def test_attach_session_rejects_render_codes_1_2(tmp_path, runs_root, code) -> None:
    ops = FakeOps(discovery=[("start", _reply(1))], brief_text=_customer_brief_text(roles=()))
    runner.start(**_start_kwargs(tmp_path, f"r-att-{code}", ops), interview_spec=_need_spec())
    ops.discovery = [("brief", _reply(code))]
    with pytest.raises(ValueError):
        runner.attach_session(f"r-att-{code}", "s-77", ops)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "crash_ or attach_session"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# в _interview_publish, сразу после _ensure_started(...) и до tmp.unlink:
    if op_status(state, INTERVIEW_BRIEF) == "started" and final.exists() \
            and state.brief is None:
        return _interview_reconcile_published(state, ops, spec, cwd, final)

def _interview_reconcile_published(state, ops, spec, cwd, final) -> bool:
    """§5.5: brief.md есть, дескриптора нет — повторный рендер обязан быть 0 и
    побайтово равен durable-файлу; тогда завершение без повторного replace."""
    from datetime import datetime, timezone
    probe = final.with_name(".brief.reconcile.tmp")
    probe.unlink(missing_ok=True)
    reply = ops.discovery_brief(state.interview["session_id"], str(probe), cwd)
    try:
        if reply.code != 0:
            return _interview_stop(
                state, f"recovery: повторный рендер вернул {reply.code}, "
                "сессия ушла от опубликованного состояния")
        if probe.read_bytes() != final.read_bytes():
            return _interview_stop(
                state, "recovery: повторный рендер не совпадает с durable brief.md")
    finally:
        probe.unlink(missing_ok=True)
    text = final.read_text(encoding="utf-8")
    findings = iv.brief_coordinate_findings(text, spec)
    if findings:
        return _interview_stop(state, "recovery: координаты: " + "; ".join(findings))
    try:
        source = brief_input.inspect_brief(final)
    except brief_input.BriefInputError as exc:
        return _interview_stop(state, f"recovery: inspect_brief: {exc}")
    state.brief = source.as_state()
    state.interview["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.status = "running"
    op_complete(state, INTERVIEW_BRIEF, brief_blob=dict(source.source_blobs), reconciled=True)
    return True


def attach_session(run_id: str, session_id: str, ops: Ops) -> RunState:
    """§5.3: присоединение сироты. Только при interview-start == started и
    session_id None; сверка по отрендеренному брифу (коды 0/10/11/20)."""
    state = load(run_id)
    if state.interview is None:
        raise ValueError("у прогона нет стадии Need")
    if state.interview.get("session_id") is not None:
        raise ValueError("session_id уже записан — замена сессии запрещена")
    if op_status(state, INTERVIEW_START) != "started":
        raise ValueError("присоединение допустимо только при interview-start == started")
    spec = iv.InterviewSpec.from_state(state.interview)
    probe = run_dir(run_id) / "brief-input" / ".attach.tmp"
    probe.parent.mkdir(parents=True, exist_ok=True)
    reply = ops.discovery_brief(session_id, str(probe), str(run_dir(run_id)))
    try:
        if reply.code not in (0, 10, 11, 20):
            raise ValueError(f"рендер сессии {session_id} вернул {reply.code}")
        findings = iv.attach_findings(probe.read_text(encoding="utf-8"), spec)
    finally:
        probe.unlink(missing_ok=True)
    if findings:
        raise ValueError("сессия не совпадает с координатами прогона: " + "; ".join(findings))
    state.interview["session_id"] = session_id
    state.status = "waiting_interview"
    op_complete(state, INTERVIEW_START, session_id=session_id, attached=True)
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_runner.py -k "crash_ or attach_session or need or brief_ or status_"`
Expected: PASS

- [ ] **Step 5: Negative controls (выполнить, зафиксировать в сообщении коммита)**

Мутант A: в `attach_session` заменить `if findings:` на `if False:` → `test_attach_session_rejects_foreign_role` красный. Мутант B: в `_interview_reconcile_published` заменить `if reply.code != 0` на `if False` → `test_crash_after_replace_requires_code_0_on_re_render` красный. Мутант C: заменить сравнение байтов на `if False` → `test_crash_after_replace_with_diverged_render_stops` красный. Каждый мутант откатить (`git checkout governance/runner.py` после проверки).

- [ ] **Step 6: Commit**

```bash
git add governance/runner.py tests/test_governance_runner.py
git commit -m "feat(runner): crash-recovery interview-brief (три окна, повторный рендер, байты) и attach_session для сирот (E2, Task 8)

Негативные проверки: мутанты «сверка ролей снята», «код 0 не требуется»,
«байты не сравниваются» — по одному красному тесту каждый.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `spec_loop` — need-флаги и preflight

**Files:**
- Modify: `governance/spec_loop.py` (argparse, preflight в `main` до run-id)
- Test: `tests/test_governance_spec_loop.py`

**Interfaces:**
- Consumes: `iv.InterviewSpec`, `brief_input.inspect_brief`, `ops.ENGINEER_BLOCKED`.
- Produces: `build_interview_spec(args, repo_slug) -> iv.InterviewSpec | None` (чистая; поднимает `SpecLoopError`); `runner.start(..., interview_spec=…)`; `_LoopEnv._start` в тестах записывает `interview_spec`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_governance_spec_loop.py — _LoopEnv._start: добавить в rs.new_run
#   interview=(kwargs["interview_spec"].as_state() if kwargs.get("interview_spec") else None)
# и статус: если interview — "waiting_interview", иначе как было.

def _need(*extra):
    return ["--subject", "Fleet Inbox", "--repo", "alpha", "--need",
            "--frame", "customer", "--stakeholder", "product owner", *extra]


def test_need_customer_starts_with_interview_spec(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(_need())
    assert rc == 0
    spec = env.calls[0][1]["interview_spec"]
    assert (spec.frame, spec.stakeholder_role, spec.target) == ("customer", "product owner", "owner/alpha")
    assert spec.traces_to is None
    assert "waiting_interview" in capsys.readouterr().out


@pytest.mark.parametrize("argv,needle", [
    (["--subject", "s", "--repo", "alpha", "--need", "--frame", "customer"], "--stakeholder"),
    (["--subject", "s", "--repo", "alpha", "--need", "--stakeholder", "r"], "--frame"),
    (["--subject", "s", "--repo", "alpha", "--frame", "customer"], "--need"),
    (["--subject", "s", "--repo", "alpha", "--stakeholder", "r"], "--need"),
    (["--subject", "s", "--repo", "alpha", "--session", "s-1"], "--need"),
    (["--subject", "s", "--repo", "alpha", "--new-run", "--ws-id", "x"], "--need"),
    (_need("--traces-to", "c.md"), "customer"),
    (_need("--brief", "x.md"), "--brief"),
    (["--subject", "s", "--repo", "alpha", "--need", "--frame", "engineer",
      "--stakeholder", "r", "--traces-to", "c.md"], "discovery#49"),
])
def test_need_preflight_refuses_before_run_id(runs_root, tmp_path, monkeypatch, capsys, argv, needle):
    env = _LoopEnv(monkeypatch, tmp_path)
    rc = spec_loop.main(argv)
    assert rc == 1
    assert env.calls == [] and rs.all_run_ids() == []
    assert needle in capsys.readouterr().out


def test_need_without_stakeholder_explains_rule_and_brief_route(runs_root, tmp_path, monkeypatch, capsys):
    _LoopEnv(monkeypatch, tmp_path)
    spec_loop.main(["--subject", "s", "--repo", "alpha", "--need", "--frame", "customer"])
    out = capsys.readouterr().out
    assert "реального стейкхолдера" in out and "--brief" in out


def test_need_repeat_with_other_coordinates_refuses(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path, resume_result=None)
    spec_loop.main(_need())
    state = rs.load(env.calls[0][1]["run_id"]); state.status = "waiting_interview"; rs.save(state)
    rc = spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", "--need",
                         "--frame", "customer", "--stakeholder", "qa"])
    assert rc == 1 and "координаты" in capsys.readouterr().out
    assert [c[0] for c in env.calls] == ["start"]


def test_need_against_run_without_interview_refuses(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])   # legacy-прогон
    rc = spec_loop.main(_need())
    assert rc == 1 and "--new-run --ws-id" in capsys.readouterr().out
    assert [c[0] for c in env.calls] == ["start"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_spec_loop.py -k need`
Expected: FAIL — argparse `unrecognized arguments: --need`

- [ ] **Step 3: Write minimal implementation**

```python
# spec_loop.py — argparse (после --brief):
    parser.add_argument("--need", action="store_true",
                        help="стадия Need: интервью discovery из прогона (E2)")
    parser.add_argument("--frame", choices=["customer", "engineer"])
    parser.add_argument("--stakeholder", help="роль реального стейкхолдера (декларация)")
    parser.add_argument("--traces-to", help="approved customer-brief для engineer")
    parser.add_argument("--session", help="recovery: присоединить сессию discovery")
    parser.add_argument("--new-run", action="store_true",
                        help="новый прогон при существующем (только до S1), требует --ws-id")

NEED_ONLY = ("frame", "stakeholder", "traces_to", "session", "new_run")
STAKEHOLDER_RULE = (
    "стадия Need запускается только при наличии реального стейкхолдера — "
    "укажите --stakeholder <role> (декларация, не проверка); без стейкхолдера "
    "используйте вход из готового брифа: --brief <path>"
)


def build_interview_spec(args, repo_slug: str) -> iv.InterviewSpec | None:
    """Preflight need-флагов — весь до run-id (спека §3)."""
    given = [f for f in NEED_ONLY if getattr(args, f)]
    if not args.need:
        if given:
            raise SpecLoopError(
                f"флаги {['--' + g.replace('_', '-') for g in given]} существуют "
                "только вместе с --need")
        return None
    if args.brief:
        raise SpecLoopError("--need и --brief взаимоисключающи")
    if not args.frame:
        raise SpecLoopError("--need требует явный --frame customer|engineer")
    if not args.stakeholder:
        raise SpecLoopError(STAKEHOLDER_RULE)
    if args.new_run and (args.run_id or args.session):
        raise SpecLoopError("--new-run взаимоисключающ с --run-id и --session")
    if args.new_run and not args.ws_id:
        raise SpecLoopError("--new-run требует --ws-id <fresh-id>")
    if args.frame == "customer":
        if args.traces_to:
            raise SpecLoopError("customer-фрейм не принимает --traces-to")
        return iv.InterviewSpec("customer", args.stakeholder, repo_slug, None, None)
    if not args.traces_to:
        raise SpecLoopError("engineer-фрейм требует --traces-to <approved customer-brief>")
    raise SpecLoopError(ops_mod.ENGINEER_BLOCKED)   # D6, до discovery#49
```

В `main` — сразу после `supplied_brief = …` и `entry = …`: `interview_spec = build_interview_spec(args, entry.repo_slug)`. При найденном `state`:

```python
        if state is not None and interview_spec is not None:
            if state.interview is None:
                raise SpecLoopError(
                    "у прогона нет стадии Need (создан через --brief/legacy или "
                    "восстановлен из GitHub) — новый: --need … --new-run --ws-id <fresh-id>")
            recorded = iv.InterviewSpec.from_state(state.interview)
            if (recorded.frame, recorded.stakeholder_role, recorded.traces_to) != (
                    interview_spec.frame, interview_spec.stakeholder_role, interview_spec.traces_to):
                raise SpecLoopError(
                    "координаты интервью зафиксированы стартом "
                    f"(frame={recorded.frame}, stakeholder={recorded.stakeholder_role!r}, "
                    f"traces_to={recorded.traces_to!r}) — сменить их: --new-run --ws-id")
```

В вызове `runner.start(...)` добавить `interview_spec=interview_spec`; в `values` — строки `"need-frame"` и `"stakeholder"`. `import governance.ops as ops_mod` и `from governance import interview as iv` вверху.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_spec_loop.py`
Expected: PASS (все, включая старые)

- [ ] **Step 5: Commit**

```bash
git add governance/spec_loop.py tests/test_governance_spec_loop.py
git commit -m "feat(spec-loop): need-флаги и preflight до run-id — --frame/--stakeholder только с --need, customer без traces-to, engineer до discovery#49 отказ, координаты повтора (E2, Task 9)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `spec_loop` — диспетчер новых статусов, `--session`, `--new-run --ws-id`

**Files:**
- Modify: `governance/spec_loop.py` (`_dispatch`, `_report_state`, поиск/`--new-run`, подсказка E1)
- Test: `tests/test_governance_spec_loop.py`

**Interfaces:**
- Consumes: `runner.resume`, `runner.attach_session` (Task 8).
- Produces: коды выхода по §5.2; после `--new-run` печать `--run-id <new>`; `--run-id` вместе с `--need` разрешён.

- [ ] **Step 1: Write the failing tests**

```python
def _make_need_run(env, run_id_suffix="", status="waiting_interview", session="s-1"):
    st = rs.new_run(subject="Fleet Inbox", repo="alpha", repo_slug="owner/alpha",
                    ws_id="ws-a" + run_id_suffix, target_dir=str(env.target),
                    bundle_dir="workstreams/ws-a/spec", profile="profiles/team-exp.yaml",
                    run_id="r-a" + run_id_suffix, merge_authority="human",
                    interview={**iv.InterviewSpec("customer", "product owner", "owner/alpha",
                                                  None, None).as_state(), "session_id": session})
    st.status = status
    st.ops["interview-start"] = {"status": "completed" if session else "started"}
    rs.save(st)
    return st


def test_waiting_interview_resume_still_waiting_exits_0(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env)
    env.resume_result = st
    assert spec_loop.main(_need()) == 0
    assert [c[0] for c in env.calls] == ["resume"]
    assert "discovery answer" in capsys.readouterr().out


def test_stopped_interview_with_session_resumes_and_exits_1_if_still_stopped(runs_root, tmp_path, monkeypatch):
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview")
    env.resume_result = st
    assert spec_loop.main(_need()) == 1
    assert [c[0] for c in env.calls] == ["resume"]


def test_stopped_interview_orphan_prints_recovery_without_resume(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="stopped_interview", session=None)
    assert spec_loop.main(_need()) == 1
    assert env.calls == []
    out = capsys.readouterr().out
    assert "--session" in out and "--new-run --ws-id" in out


def test_session_attach_calls_attach_then_resume(runs_root, tmp_path, monkeypatch):
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="stopped_interview", session=None)
    attached = []
    def _attach(run_id, session_id, ops):
        attached.append((run_id, session_id)); st.interview["session_id"] = session_id
        st.status = "waiting_interview"; rs.save(st); return st
    monkeypatch.setattr(spec_loop.runner, "attach_session", _attach)
    env.resume_result = st
    assert spec_loop.main(_need("--session", "s-77")) == 0
    assert attached == [("r-a", "s-77")] and [c[0] for c in env.calls] == ["resume"]


def test_new_run_requires_pre_s1_runs_and_prints_run_id(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    _make_need_run(env, status="stopped_interview")
    _make_need_run(env, run_id_suffix="2", status="waiting_interview")   # два совпавших — гвард неоднозначности НЕ применяется
    rc = spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh"))
    assert rc == 0
    assert env.calls[0][0] == "start" and env.calls[0][1]["ws_id"] == "ws-fresh"
    out = capsys.readouterr().out
    assert f"--run-id {env.calls[0][1]['run_id']}" in out
    assert rs.load("r-a").status == "stopped_interview"   # старые леджеры не тронуты


def test_new_run_refused_when_a_match_reached_s1(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env, status="waiting_human_merge"); st.branch = "spec/ws-a-behaviour"; rs.save(st)
    assert spec_loop.main(_need("--new-run", "--ws-id", "ws-fresh")) == 1
    assert env.calls == [] and "--run-id" in capsys.readouterr().out


def test_need_with_run_id_on_repeat_is_allowed(runs_root, tmp_path, monkeypatch):
    env = _LoopEnv(monkeypatch, tmp_path)
    st = _make_need_run(env); env.resume_result = st
    assert spec_loop.main(_need("--run-id", "r-a")) == 0


def test_e1_hint_names_new_run_instead_of_ws_id(runs_root, tmp_path, monkeypatch, capsys):
    env = _LoopEnv(monkeypatch, tmp_path)
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha"])
    source = tmp_path / "input.md"; source.write_text(_customer_brief(), encoding="utf-8")
    spec_loop.main(["--subject", "Fleet Inbox", "--repo", "alpha", "--brief", str(source)])
    assert "--new-run --ws-id" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q tests/test_governance_spec_loop.py -k "interview or new_run or session_attach or e1_hint"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# _dispatch: перед `if state.status == "waiting_human_merge":`
    if state.status in ("waiting_interview", "stopped_interview"):
        if state.interview and state.interview.get("session_id") is None:
            print("spec-loop: стадия Need без записанной сессии (start не дошёл до "
                  "записи). Присоедините сессию: повторите команду с --session <id>; "
                  f"либо новый прогон: --new-run --ws-id <fresh-id> (старый run-id {state.run_id})")
            return 1
        after = runner.resume(state.run_id, ops)
        if after.status == "waiting_interview":
            return 0
        if after.status == "stopped_interview":
            print(f"spec-loop: интервью остановлено — findings: "
                  f"out/governance-runs/{after.run_id}/interview-findings.txt; "
                  "ответьте и повторите команду")
            return 1
        if after.status == "waiting_human_merge":
            print(f"бандл-PR #{after.pr} создан ({after.repo_slug}) — смержьте его и повторите make spec-loop")
            return 0
        if after.status == "completed":
            return _deliver_phase(after, ops)
        return _report_state(after)

# main: после выбора state и проверки координат (Task 9):
        if args.session:
            if state is None:
                raise SpecLoopError("--session присоединяет сессию к существующему прогону, а его нет")
            state = runner.attach_session(state.run_id, args.session, ops)   # ValueError → SpecLoopError
# поиск: если args.new_run — matches = find_runs(...); не применять гвард
# «несколько прогонов — --run-id»; вместо этого:
        if args.new_run:
            past_s1 = [st for st in matches if st.status not in ("waiting_interview", "stopped_interview")]
            if past_s1:
                raise SpecLoopError(
                    "--new-run запрещён: прогон(ы) с этими (repo, subject) уже достигли S1: "
                    + ", ".join(f"--run-id {st.run_id} [{st.status}]" for st in past_s1))
            for st in matches:
                print(f"spec-loop: прежний прогон {st.run_id} остаётся ({st.status}), "
                      f"сессия discovery: {(st.interview or {}).get('session_id')}")
            state = None
# после runner.start при interview_spec: печать
        if started.status == "waiting_interview":
            print(f"интервью начато — ответьте и повторите: make spec-loop … ARGS='--need … --run-id {started.run_id}'")
# подсказка E1 (две строки «другим --ws-id») → "--new-run --ws-id <fresh-id>"
```

`ValueError` из `attach_session` — обернуть в `SpecLoopError(str(exc))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q tests/test_governance_spec_loop.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add governance/spec_loop.py tests/test_governance_spec_loop.py
git commit -m "feat(spec-loop): диспетчер waiting/stopped_interview (0/1), сирота без вызова, --session attach, --new-run --ws-id только до S1 без гварда неоднозначности, подсказка E1 (E2, Task 10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: opt-in smoke с настоящим discovery, ревизия 5 спеки, TODO, CI

**Files:**
- Create: `tests/test_discovery_smoke.py`
- Modify: `docs/superpowers/specs/2026-09-15-need-stage-design.md`, `TODO.md`

- [ ] **Step 1: Write the smoke test (opt-in)**

```python
# tests/test_discovery_smoke.py
"""Opt-in smoke стадии Need с НАСТОЯЩИМ discovery (спека §8).

Запуск: DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen pytest -q tests/test_discovery_smoke.py
Без переменной — skip: обычный pytest от соседа не зависит.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from governance import brief_input, interview as iv
from governance.ops import RealOps

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEVTOOLS_DISCOVERY_SMOKE"),
    reason="opt-in: DEVTOOLS_DISCOVERY_SMOKE=1",
)

_ANSWERS = {  # покрытие required-ключей customer-фрейма синтетическими записями
    "goals": {"text": "цель", "entries": [{"id": "G-01", "body": "сократить время приёмки"}]},
    "functions": {"text": "функция", "entries": [{"id": "FR-01", "body": "одна кнопка",
                  "Priority": "Must", "Acceptance": "прогон стартует одной командой",
                  "traces": ["G-01"]}]},
}


def _answer_for(coverage_key: str) -> dict:
    return _ANSWERS.get(coverage_key, {"text": f"ответ по {coverage_key}"})


def test_real_discovery_customer_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_HOME", str(tmp_path / "home"))
    ops = RealOps()
    cwd = str(tmp_path)
    reply = ops.discovery_start("customer", "owner/smoke", None, None, cwd)
    assert reply.code == 20, reply
    session = reply.envelope["next_action"]["session_id"]
    for _ in range(60):   # цикл по ВСЕМУ банку вопросов фрейма
        action = reply.envelope["next_action"]
        answer = tmp_path / "answer.yaml"
        answer.write_text(yaml.safe_dump(_answer_for(action.get("coverage_key", "")),
                                         allow_unicode=True), encoding="utf-8")
        ops._discovery(["answer", "--session", session, "--role", "po",
                        "--file", str(answer)], cwd)
        reply = ops.discovery_status(session, cwd)
        if reply.code != 20:
            break
    assert reply.code in (0, 11), reply   # 11 = gate pass, readiness incomplete — тоже терминал банка
    out = tmp_path / "brief.md"
    assert ops.discovery_brief(session, str(out), cwd).code == reply.code
    text = out.read_text(encoding="utf-8")
    spec = iv.InterviewSpec("customer", "po", "owner/smoke", None, None)
    assert iv.brief_coordinate_findings(text, spec) == []
    # детерминизм рендера (§5.5): второй рендер побайтово равен
    out2 = tmp_path / "brief2.md"
    ops.discovery_brief(session, str(out2), cwd)
    assert out2.read_bytes() == out.read_bytes()
    if reply.code == 0:
        brief_input.inspect_brief(out)
```

- [ ] **Step 2: Run smoke locally once and record the outcome**

Run: `DEVTOOLS_DISCOVERY_SMOKE=1 uv run --frozen pytest -q tests/test_discovery_smoke.py -x`
Expected: PASS либо честный отчёт, какой required-ключ банка не покрыт синтетическими ответами (тогда дополнить `_ANSWERS` по `readiness_findings` — не ослаблять ассерты). Без переменной: `1 skipped`.

- [ ] **Step 3: Spec revision 5 and TODO**

В спеке: §5.3 — критерий по `sessions` как множество `participant_role` ⊆ `{stakeholder}`; §5.4 — при `--new-run` гвард неоднозначности не применяется; §5.1/§5.3 — `traces_to` как у E1 (customer: без путевых элементов; engineer: ровно один, равный записанному); шапка: «ревизия 5 — реализация PR #<n>». В `TODO.md` пункт `spec-loop-need-stage` дополнить строкой «Код — PR #<n>; чекбокс — после живой приёмки §9 спеки».

- [ ] **Step 4: Full CI commands**

Run:
```bash
uv run --frozen pytest -q
uv run --frozen --group governance pytest tests/test_governance_steward_surface.py tests/test_governance_stale_adapter.py tests/test_governance_bundle_state.py -q
make plan-check-selftest
make plan-check
```
Expected: all green; записать фактические числа в описание PR.

- [ ] **Step 5: Commit and open PR**

```bash
git add tests/test_discovery_smoke.py docs/superpowers/specs/2026-09-15-need-stage-design.md TODO.md
git commit -m "test(smoke): opt-in прогон стадии Need с настоящим discovery; спека ревизия 5; TODO (E2, Task 11)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin feat/need-stage
gh pr create --base master --title "feat(governance): E2 — стадия Need вызывается прогоном (spec-loop --need, customer)" --body-file <описание с числами проверок, negative controls, ссылкой на спеку и ограничением «engineer до discovery#49»>
```

PR не трогает харнесс-пути — терминальное ревью и агентский мерж через `merge-pr.sh`. Живая приёмка (§9 спеки) — отдельно, по сигналу владельца, с реальным стейкхолдером.

---

## Self-review

- **Spec coverage:** §3 preflight → Task 9; §4 состояние → Task 3/5; §5.1 таблица → Task 5/6/7; §5.2 диспетчер → Task 10; §5.3 attach → Task 8/10; §5.4 `--new-run` → Task 10; §5.5 crash → Task 8; §5.6 upstream_blob — engineer заблокирован (D6), порт готов (Task 4), пересверка не реализуется до discovery#49 (записано в Task 4/9); §6 порт и транспорт → Task 1/4; §7 → Task 7; §8 матрица → Task 1–10; smoke → Task 11; §9 живая приёмка — вне плана, по сигналу владельца.
- **Placeholder scan:** нет TBD/TODO; заглушки Task 5/6 заменяются в Task 6/7 явно.
- **Type consistency:** `DiscoveryReply(code, envelope, stderr)`, `InterviewSpec(frame, stakeholder_role, target, traces_to, upstream_blob)`, `discovery_start(frame, target, traces_to, upstream_path, cwd)`, `attach_session(run_id, session_id, ops)`, статусы `waiting_interview`/`stopped_interview`, op-ключи `interview-start`/`interview-brief` — одинаковы во всех задачах.
