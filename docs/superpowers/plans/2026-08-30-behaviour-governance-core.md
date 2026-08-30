# Behaviour Governance Core (этап A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ядро конвейера behaviour-spec в devtools: пин steward-пакета с
characterization-тестами трёх публичных символов, чистый `merge_gate`, prospective
stale-адаптер и read-модель `bundle_state` (candidate-срез) — всё, от чего зависит
runner этапа B.

**Architecture:** Пакет `devtools/governance/` (новый, py-модули + тесты). S4-гейт
реализуется прямыми вызовами пинованного steward-пакета (три символа, без git-facts);
merge-право — чистая функция-конъюнкция осей ADR-ECO-011 × safety-гейт steward ×
вердикт ревью × факты PR. Runner/console/smoke — этап B, отдельный план после мержа
ядра (characterization может сдвинуть их дизайн — потому и порядок такой).

**Tech Stack:** Python ≥3.12; uv dependency-group `governance` (пин
`steward @ git+…@4a1c7c4…`); pytest; стандартные для devtools проверки
(py_compile, line length 88, `uv run --frozen pytest`).

**Spec:** `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md`
(копируется в репо Task 1 из
`../.claude/worktrees/token-consumption-monitoring-27eb23/_cowork_output/plans/2026-08-30-behaviour-spec-pipeline-design.md`,
ревизия v4, вердикт GO; план аргументирует от неё).

## Global Constraints

- Ветка: `feat/behaviour-governance-core`; прямые коммиты в master запрещены;
  коммиты — с трейлером `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- steward-пин: `4a1c7c44a85accf609b40cb14115eccefb26f6c2` (master на 2026-08-30);
  зависимость ТОЛЬКО в группе `governance` — обычные devtools-команды не получают
  typer/pyyaml/jsonschema (спека §3).
- S4-поверхность — ровно три символа: `steward.gatecheck.checks.collect_bundle`,
  `steward.gatecheck.behaviour.check_behaviour_spec`,
  `steward.gatecheck.trace_matrix.build_trace_matrix`; внутренний
  `check_stale_cascade` НЕ импортируется (спека §3).
- Prospective stale — адаптер devtools по локальным blob-хешам; никаких injected
  git-facts, никакой подмены `default_branch_files` (спека §3).
- `merge_gate` — чистая функция, вердикт `agent | human | refuse` с обязательной
  причиной; requested authority может только ужесточать; всё fail-closed (спека §6, §8).
- Тесты: `uv run --frozen --group governance pytest tests/test_governance_*.py -q`,
  финально — полный `uv run --frozen --group governance pytest -q`.
- Line length ≤ 88 (мерить `len()` по символам, awk на кириллице врёт).

---

### Task 1: Ветка, спека в репо, uv-группа `governance`, пин steward

**Files:**
- Create: `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md` (копия)
- Create: `docs/superpowers/plans/2026-08-30-behaviour-governance-core.md` (этот план)
- Modify: `pyproject.toml`
- Create: `governance/__init__.py` (пустой, с однострочным докстрингом пакета)

**Interfaces:**
- Produces: рабочее окружение `uv run --frozen --group governance` с импортируемым
  `steward.gatecheck`; ветка и спека в репо для всех следующих задач.

- [ ] **Step 1: Ветка и спека**

```bash
cd ~/labs/all_ai_orchestrators/devtools
git switch -c feat/behaviour-governance-core
cp ../.claude/worktrees/token-consumption-monitoring-27eb23/_cowork_output/plans/2026-08-30-behaviour-spec-pipeline-design.md \
   docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md
```

(Этот план кладётся в `docs/superpowers/plans/` тем же коммитом — файл уже создан
контролёром, проверить его наличие.)

- [ ] **Step 2: pyproject — группа governance**

В `pyproject.toml` секцию `[dependency-groups]` привести к виду:

```toml
[dependency-groups]
dev = ["pytest>=8"]
# Только для governance-контура (спека §3/§4): steward — content-check API S4,
# textual понадобится console.py этапа B. Обычные devtools-команды группу не тянут.
governance = [
    "steward @ git+https://github.com/andrei-shtanakov/steward@4a1c7c44a85accf609b40cb14115eccefb26f6c2",
]
```

- [ ] **Step 3: Лок и smoke-импорт**

```bash
uv lock
uv run --frozen --group governance python -c \
  "from steward.gatecheck.checks import collect_bundle; \
   from steward.gatecheck.behaviour import check_behaviour_spec; \
   from steward.gatecheck.trace_matrix import build_trace_matrix; print('ok')"
```

Expected: `ok`. Если импорт падает — зафиксировать фактический traceback в отчёте и
СТОП (BLOCKED): пин или layout пакета отличаются от спеки, это вопрос контролёру.

- [ ] **Step 4: governance/__init__.py**

```python
"""Governance-ядро конвейера behaviour-spec (спека в docs/superpowers/specs/)."""
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md \
        docs/superpowers/plans/2026-08-30-behaviour-governance-core.md \
        pyproject.toml uv.lock governance/__init__.py
git commit -m "feat(governance): uv-группа governance + пин steward 4a1c7c4 + спека v4

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Characterization-тесты поверхности steward

Доказываем публичную поверхность и набор `gate_id` ДО state machine (вердикт GO,
финальное указание ревью). Тесты пишутся против пина, не против соседнего чекаута.

**Files:**
- Create: `tests/test_governance_steward_surface.py`
- Create: `tests/governance_fixtures/__init__.py` (пустой)
- Create: `tests/governance_fixtures/bundles.py` (фабрики фикстурных бандлов)

**Interfaces:**
- Consumes: пин Task 1.
- Produces: `make_profile(tmp_path) -> Path` и
  `make_bundle(tmp_path, *, behaviour_ok: bool) -> Path` в
  `tests/governance_fixtures/bundles.py` — их переиспользуют Task 4 и 5;
  доказанные сигнатуры: `collect_bundle(graph, spec_dir) -> (list[Artifact],
  list[Finding])`, `check_behaviour_spec(graph, artifacts) -> list[Finding]`,
  `build_trace_matrix(graph, artifacts) -> dict | None`; `Finding(severity,
  rule_id, artifact, message)`; `Artifact.meta.upstream_hashes:
  tuple[tuple[str, str], ...]`.

- [ ] **Step 1: Фабрики фикстур**

`tests/governance_fixtures/bundles.py`:

```python
"""Фабрики минимального профиля и бандла для characterization-тестов steward.

Профиль — урезанный team-exp: requirements -> behaviour-spec. Файлы бандла несут
frontmatter с spec_stage (узел определяется по нему, не по имени файла — спека §3).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PROFILE_YAML = """\
nodes:
  - {id: requirements, template: requirements.md, owner_role: analysts}
  - id: behaviour-spec
    template: behaviour-spec.md
    owner_role: analysts
    upstream: [requirements]
"""

ROLES_YAML = """\
roles:
  - {id: analysts, title: Analysts}
"""

REQUIREMENTS_MD = """\
---
spec_stage: requirements
status: approved
owner_role: analysts
---
# Requirements

## FR-1 (Must) Пользователь видит список
"""

BEHAVIOUR_OK_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
traces_to: [requirements]
upstream_hashes:
  requirements: "{req_hash}"
---
# Behaviour

## BEH-01
Trace: FR-1
Checked-by: {{status: planned, kind: pytest, owner: qa, target: tests/test_x.py}}
"""

BEHAVIOUR_BAD_MD = """\
---
spec_stage: behaviour-spec
status: draft
owner_role: analysts
---
# Behaviour

## BEH-01
Сценарий без Trace и без checked_by.
"""


def blob_hash(text: str) -> str:
    """git hash-object содержимого — чистым stdlib, git не нужен."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00%s" % (len(data), data)).hexdigest()


def make_profile(tmp_path: Path) -> Path:
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir(exist_ok=True)
    (prof_dir / "roles.yaml").write_text(ROLES_YAML)
    profile = prof_dir / "mini.yaml"
    profile.write_text(PROFILE_YAML)
    return profile


def make_bundle(tmp_path: Path, *, behaviour_ok: bool) -> Path:
    bundle = tmp_path / "spec"
    bundle.mkdir(exist_ok=True)
    (bundle / "10-requirements.md").write_text(REQUIREMENTS_MD)
    if behaviour_ok:
        text = BEHAVIOUR_OK_MD.format(req_hash=blob_hash(REQUIREMENTS_MD))
    else:
        text = BEHAVIOUR_BAD_MD
    (bundle / "15-behaviour-spec.md").write_text(text)
    (bundle / "notes.md").write_text("без frontmatter — должен пройти насквозь\n")
    return bundle
```

ВАЖНО: точный формат фронтматтера/Checked-by/Trace может отличаться от принятого
steward — это и есть предмет characterization. Если `collect_bundle`/`check_…`
возвращают неожиданные findings, НЕ подгонять assert'ы вслепую: привести фикстуры к
формату, который принимает steward (смотреть `steward/profiles/team-exp.yaml` и
тесты самого steward в пине), и зафиксировать фактический формат в докстринге фабрики.

- [ ] **Step 2: Characterization-тесты**

`tests/test_governance_steward_surface.py`:

```python
"""Characterization пинованного steward: поверхность трёх символов и их gate_id.

Ломается при bump'е пина, если steward изменил контракт, — это фича: план этапа B
строится на доказанной поверхности (спека §12 п.0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.gatecheck.behaviour import check_behaviour_spec
from steward.gatecheck.checks import Artifact, Finding, collect_bundle
from steward.gatecheck.trace_matrix import build_trace_matrix
from steward.graph import load_profile
from steward.roles import load_roles_catalog

from tests.governance_fixtures.bundles import make_bundle, make_profile


def _graph(tmp_path: Path):
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    return load_profile(profile, roles)


def test_collect_bundle_surface(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, findings = collect_bundle(graph, bundle)
    assert all(isinstance(a, Artifact) for a in artifacts)
    assert all(isinstance(f, Finding) for f in findings)
    node_ids = {a.node_id for a in artifacts}
    assert {"requirements", "behaviour-spec"} <= node_ids
    # файл без frontmatter не становится managed-артефактом
    assert not any(a.path == "notes.md" for a in artifacts)


def test_finding_shape_is_pinned(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_behaviour_spec(graph, artifacts)
    assert findings, "плохой behaviour-spec обязан дать findings"
    f = findings[0]
    assert f.severity in ("error", "warn")
    assert isinstance(f.rule_id, str) and f.rule_id
    assert isinstance(f.artifact, str)
    assert isinstance(f.message, str)


def test_behaviour_gate_ids(tmp_path: Path) -> None:
    """Полный набор gate_id, который выдаёт check_behaviour_spec на плохом бандле."""
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    artifacts, _ = collect_bundle(graph, bundle)
    ids = {f.rule_id for f in check_behaviour_spec(graph, artifacts)}
    # Ожидаемое множество — из спеки §7; фактическое зафиксировать здесь же.
    assert ids <= {"GC-BEH-TRACE", "GC-BEH-COVERAGE", "GC-CHECK-PLANNED"}, ids
    assert ids, "хотя бы один GC-BEH-* обязан сработать"


def test_good_bundle_is_clean(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, collect_findings = collect_bundle(graph, bundle)
    beh_findings = check_behaviour_spec(graph, artifacts)
    errors = [
        f for f in [*collect_findings, *beh_findings] if f.severity == "error"
    ]
    assert errors == []


def test_trace_matrix_surface(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    matrix = build_trace_matrix(graph, artifacts)
    assert matrix is None or isinstance(matrix, dict)


def test_upstream_hashes_shape(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    artifacts, _ = collect_bundle(graph, bundle)
    beh = next(a for a in artifacts if a.node_id == "behaviour-spec")
    assert isinstance(beh.meta.upstream_hashes, tuple)
    pinned = dict(beh.meta.upstream_hashes)
    assert "requirements" in pinned
    assert len(pinned["requirements"]) == 40  # git blob sha1 hex
```

- [ ] **Step 3: Прогнать; итерировать фикстуры до честного зелёного**

Run: `uv run --frozen --group governance pytest tests/test_governance_steward_surface.py -v`
Expected: сначала, вероятно, FAIL из-за формата фикстур — приводить ФИКСТУРЫ к
контракту steward (не ослаблять assert'ы), пока не станет PASS. Каждое открытие о
формате — в докстринг `bundles.py`. Если поверхность фактически иная (другие имена
символов/полей) — СТОП (BLOCKED), это меняет спеку.

- [ ] **Step 4: Commit**

```bash
git add tests/governance_fixtures/ tests/test_governance_steward_surface.py
git commit -m "test(governance): characterization пинованного steward — 3 символа + GC-BEH gate_id

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `merge_gate.py` — чистая функция-конъюнкция

**Files:**
- Create: `governance/merge_gate.py`
- Test: `tests/test_governance_merge_gate.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач (только stdlib).
- Produces (этап B полагается на это дословно):

```python
@dataclass(frozen=True)
class Authority:      # ось 1, ADR-ECO-011: каждый уровень только ужесточает
    ecosystem: str = "agent"          # "agent" | "human"; конфига нет = "agent"
    repo: str | None = None            # None | "human" (строка «Мерж: человек»)
    run: str | None = None             # None | "human" (объявление S0)

@dataclass(frozen=True)
class Safety:         # ось 2, срез steward-политики (вендоренная копия)
    agent_merge_allowed: bool | None   # None = копия недоступна/дрейф -> unknown
    actor_class: str                   # "agent" | "human" | "unknown"

@dataclass(frozen=True)
class PrFacts:        # §8 спеки
    checks_rollup: str        # "green" | "red" | "empty" | "unknown"
    mergeable: str            # "mergeable" | "conflicting" | "unknown"
    behind_base: bool
    unresolved_threads: bool
    diff_class: str           # "document" | "code" | "research"
    touches_authority_root: bool

@dataclass(frozen=True)
class MergeVerdict:
    decision: str             # "agent" | "human" | "refuse"
    reason: str               # всегда непустая

def decide(authority: Authority, safety: Safety,
           review_exit: int | None, facts: PrFacts) -> MergeVerdict: ...
```

- [ ] **Step 1: Табличные тесты (падающие)**

`tests/test_governance_merge_gate.py`:

```python
"""Табличные тесты merge_gate — «агент может» превращается в «агенту можно» (спека §9)."""

from __future__ import annotations

import pytest

from governance.merge_gate import Authority, MergeVerdict, PrFacts, Safety, decide

GOOD_FACTS = PrFacts(
    checks_rollup="green", mergeable="mergeable", behind_base=False,
    unresolved_threads=False, diff_class="document",
    touches_authority_root=False,
)
SAFE = Safety(agent_merge_allowed=True, actor_class="agent")
UNSAFE_TODAY = Safety(agent_merge_allowed=False, actor_class="agent")


def test_happy_path_agent() -> None:
    v = decide(Authority(), SAFE, 0, GOOD_FACTS)
    assert v == MergeVerdict("agent", v.reason)
    assert v.reason


def test_today_state_is_human_by_data() -> None:
    """Регрессия спеки §6: пока agent_merge_allowed=false — автономная ветка недостижима."""
    v = decide(Authority(), UNSAFE_TODAY, 0, GOOD_FACTS)
    assert v.decision == "human"
    assert "agent_merge_allowed" in v.reason


@pytest.mark.parametrize("auth", [
    Authority(ecosystem="human"),
    Authority(repo="human"),
    Authority(run="human"),
])
def test_any_level_tightens_to_human(auth: Authority) -> None:
    assert decide(auth, SAFE, 0, GOOD_FACTS).decision == "human"


@pytest.mark.parametrize("exit_code,expected", [
    (1, "human"),   # request-changes: находки -> человеку
    (2, "human"),   # прибор не отработал
    (3, "human"),
    (4, "human"),   # голова уехала
    (None, "human"),  # ревью не приходило вовсе = unknown
])
def test_review_gate(exit_code: int | None, expected: str) -> None:
    assert decide(Authority(), SAFE, exit_code, GOOD_FACTS).decision == expected


def test_refuse_on_red_gate() -> None:
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, "checks_rollup": "red"})
    v = decide(Authority(), SAFE, 0, facts)
    assert v.decision == "refuse"


@pytest.mark.parametrize("field,value", [
    ("checks_rollup", "empty"),      # пустой rollup != прошли (спека §8)
    ("checks_rollup", "unknown"),
    ("mergeable", "unknown"),
    ("mergeable", "conflicting"),
    ("behind_base", True),
    ("unresolved_threads", True),    # седьмое предусловие
])
def test_fact_degradations_block_agent(field: str, value) -> None:
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, field: value})
    assert decide(Authority(), SAFE, 0, facts).decision == "human"


@pytest.mark.parametrize("diff_class", ["code", "research"])
def test_non_document_diff_forces_human(diff_class: str) -> None:
    """Предохранитель runner'а (спека §6): сам мержит только document-диффы."""
    facts = GOOD_FACTS.__class__(**{**GOOD_FACTS.__dict__, "diff_class": diff_class})
    assert decide(Authority(), SAFE, 0, facts).decision == "human"


def test_authority_root_always_human() -> None:
    facts = GOOD_FACTS.__class__(
        **{**GOOD_FACTS.__dict__, "touches_authority_root": True})
    v = decide(Authority(), SAFE, 0, facts)
    assert v.decision == "human"
    assert "authority-root" in v.reason


@pytest.mark.parametrize("safety", [
    Safety(agent_merge_allowed=None, actor_class="agent"),   # копия недоступна
    Safety(agent_merge_allowed=True, actor_class="unknown"),  # актор не в списках
])
def test_safety_unknown_is_fail_closed(safety: Safety) -> None:
    assert decide(Authority(), safety, 0, GOOD_FACTS).decision == "human"


def test_reason_is_always_present() -> None:
    for v in (
        decide(Authority(), SAFE, 0, GOOD_FACTS),
        decide(Authority(run="human"), SAFE, 0, GOOD_FACTS),
        decide(Authority(), UNSAFE_TODAY, 1, GOOD_FACTS),
    ):
        assert v.reason.strip()
```

- [ ] **Step 2: Убедиться в красном**

Run: `uv run --frozen --group governance pytest tests/test_governance_merge_gate.py -q`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализация `governance/merge_gate.py`**

```python
"""Право мержа: чистая конъюнкция осей (спека §6) и деградаций (§8).

Порядок проверок = порядок причин в вердикте: сначала то, что «нельзя никому»
(refuse), затем то, что оставляет PR человеку (human), и только при полностью
зелёном наборе — agent. Функция не ходит в сеть и не читает диск.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Authority:
    """Ось 1 (ADR-ECO-011): каждый уровень может только ужесточить до human."""

    ecosystem: str = "agent"
    repo: str | None = None
    run: str | None = None

    def effective(self) -> str:
        if "human" in (self.ecosystem, self.repo, self.run):
            return "human"
        return "agent"


@dataclass(frozen=True)
class Safety:
    """Ось 2: срез steward-политики из вендоренной копии; None = unknown."""

    agent_merge_allowed: bool | None
    actor_class: str


@dataclass(frozen=True)
class PrFacts:
    checks_rollup: str
    mergeable: str
    behind_base: bool
    unresolved_threads: bool
    diff_class: str
    touches_authority_root: bool


@dataclass(frozen=True)
class MergeVerdict:
    decision: str
    reason: str


def decide(
    authority: Authority,
    safety: Safety,
    review_exit: int | None,
    facts: PrFacts,
) -> MergeVerdict:
    """Вердикт agent | human | refuse; причина обязательна (спека §4)."""
    if facts.checks_rollup == "red":
        return MergeVerdict("refuse", "красный rollup обязательных проверок")

    if facts.touches_authority_root:
        return MergeVerdict(
            "human", "дифф затрагивает authority-root пути (ADR-ECO-004 I2)"
        )
    if authority.effective() == "human":
        return MergeVerdict("human", "requested authority ужесточена до human")
    if safety.agent_merge_allowed is not True:
        return MergeVerdict(
            "human",
            "safety-гейт: agent_merge_allowed не поднят или копия недоступна",
        )
    if safety.actor_class != "agent":
        return MergeVerdict(
            "human", f"актор класса {safety.actor_class!r} — fail-closed"
        )
    if review_exit != 0:
        return MergeVerdict(
            "human", f"ревью не дало явного approve (exit={review_exit!r})"
        )
    if facts.diff_class != "document":
        return MergeVerdict(
            "human", f"дифф класса {facts.diff_class!r} — предохранитель runner'а"
        )
    if facts.checks_rollup != "green":
        return MergeVerdict(
            "human", f"rollup {facts.checks_rollup!r} не читается как зелёный"
        )
    if facts.mergeable != "mergeable":
        return MergeVerdict("human", f"mergeable={facts.mergeable!r}")
    if facts.behind_base:
        return MergeVerdict("human", "PR отстал от base — нужен update-branch")
    if facts.unresolved_threads:
        return MergeVerdict("human", "есть неразрешённые review threads")
    return MergeVerdict("agent", "все оси зелёные: authority+safety+review+facts")
```

- [ ] **Step 4: Зелёный прогон**

Run: `uv run --frozen --group governance pytest tests/test_governance_merge_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add governance/merge_gate.py tests/test_governance_merge_gate.py
git commit -m "feat(governance): merge_gate — конъюнкция ECO-011 authority x safety x review x facts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Prospective stale-адаптер

Маленький адаптер devtools (спека §3): `upstream_hashes` против локальных blob-хешей
worktree. Удаляется при переходе на candidate-контракт steward — семантика пинуется
тестами. Внутренний `check_stale_cascade` не импортируется.

**Files:**
- Create: `governance/stale_adapter.py`
- Test: `tests/test_governance_stale_adapter.py`

**Interfaces:**
- Consumes: `Artifact` (steward, из Task 2 доказан: `.path`, `.node_id`, `.text`,
  `.meta.upstream_hashes`), `blob_hash` из `tests/governance_fixtures/bundles.py`
  (реализация дублируется в адаптере — тестовый хелпер не импортируется в прод-код).
- Produces: `check_stale(artifacts: list[Artifact]) -> list[StaleFinding]`;
  `StaleFinding(artifact: str, upstream: str, pinned: str, actual: str | None)`;
  `blob_sha1(text: str) -> str`.

- [ ] **Step 1: Падающие тесты**

`tests/test_governance_stale_adapter.py`:

```python
"""Семантика prospective stale-адаптера (спека §3): пины против локального контента.

Адаптер временный (до candidate-контракта steward) — тесты и есть его контракт.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from steward.gatecheck.checks import collect_bundle
from steward.graph import load_profile
from steward.roles import load_roles_catalog

from governance.stale_adapter import StaleFinding, blob_sha1, check_stale
from tests.governance_fixtures.bundles import (
    REQUIREMENTS_MD,
    blob_hash,
    make_bundle,
    make_profile,
)


def _artifacts(tmp_path: Path, *, behaviour_ok: bool = True):
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    bundle = make_bundle(tmp_path, behaviour_ok=behaviour_ok)
    artifacts, _ = collect_bundle(graph, bundle)
    return bundle, artifacts


def test_blob_sha1_matches_git_hash_object() -> None:
    assert blob_sha1(REQUIREMENTS_MD) == blob_hash(REQUIREMENTS_MD)


def test_fresh_pins_give_no_findings(tmp_path: Path) -> None:
    _, artifacts = _artifacts(tmp_path)
    assert check_stale(artifacts) == []


def test_stale_pin_is_reported(tmp_path: Path) -> None:
    bundle, _ = _artifacts(tmp_path)
    req = bundle / "10-requirements.md"
    req.write_text(REQUIREMENTS_MD + "\n## FR-2 (Must) Новое требование\n")
    # перечитать бандл после правки
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_stale(artifacts)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, StaleFinding)
    assert f.upstream == "requirements"
    assert f.pinned != f.actual and f.actual is not None


def test_pin_to_absent_upstream_is_reported(tmp_path: Path) -> None:
    bundle, _ = _artifacts(tmp_path)
    (bundle / "10-requirements.md").unlink()
    profile = make_profile(tmp_path)
    roles = load_roles_catalog(profile.parent / "roles.yaml")
    graph = load_profile(profile, roles)
    artifacts, _ = collect_bundle(graph, bundle)
    findings = check_stale(artifacts)
    assert [f.upstream for f in findings] == ["requirements"]
    assert findings[0].actual is None  # «факт недоступен», не «совпало»
```

- [ ] **Step 2: Красный прогон**

Run: `uv run --frozen --group governance pytest tests/test_governance_stale_adapter.py -q`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализация `governance/stale_adapter.py`**

```python
"""Prospective stale-проверка по локальному контенту worktree (спека §3).

ВРЕМЕННЫЙ адаптер: публичного prospective-API у steward нет, а его внутренний
stale-каскад требует git-facts. Сверяем пины upstream_hashes артефактов бандла с
blob-хешами фактического содержимого тех же артефактов. Удалить при переходе S4
на candidate-контракт steward (`ref_kind: candidate`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StaleFinding:
    """Один протухший (или непроверяемый) пин: artifact -> upstream."""

    artifact: str
    upstream: str
    pinned: str
    actual: str | None  # None = upstream-артефакта нет в бандле (fail-closed)


def blob_sha1(text: str) -> str:
    """`git hash-object` содержимого — чистым stdlib, git не нужен."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00%s" % (len(data), data)).hexdigest()


def check_stale(artifacts: list[Any]) -> list[StaleFinding]:
    """Пины каждого артефакта против blob-хешей upstream-узлов того же бандла."""
    by_node = {a.node_id: a for a in artifacts if a.node_id is not None}
    findings: list[StaleFinding] = []
    for artifact in artifacts:
        for upstream, pinned in artifact.meta.upstream_hashes:
            up = by_node.get(upstream)
            actual = blob_sha1(up.text) if up is not None else None
            if actual != pinned:
                findings.append(
                    StaleFinding(
                        artifact=artifact.path,
                        upstream=upstream,
                        pinned=pinned,
                        actual=actual,
                    )
                )
    return findings
```

- [ ] **Step 4: Зелёный прогон**

Run: `uv run --frozen --group governance pytest tests/test_governance_stale_adapter.py -q`
Expected: PASS. Если `test_blob_sha1_matches_git_hash_object` падает — фикстурный
`blob_hash` и адаптер разошлись, чинить адаптер (семантика git hash-object первична).

- [ ] **Step 5: Commit**

```bash
git add governance/stale_adapter.py tests/test_governance_stale_adapter.py
git commit -m "feat(governance): prospective stale-адаптер по локальным blob-хешам

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `bundle_state.py` — candidate-срез read-модели

**Files:**
- Create: `governance/bundle_state.py`
- Test: `tests/test_governance_bundle_state.py`

**Interfaces:**
- Consumes: три символа steward (Task 2), `check_stale` (Task 4).
- Produces (runner этапа B читает только это):

```python
@dataclass(frozen=True)
class NodeState:
    node_id: str
    status: str            # "absent" | "draft" | "candidate_valid" | "stale"
    findings: tuple[str, ...]   # человекочитаемые "SEV rule_id: message"

@dataclass(frozen=True)
class BundleState:
    nodes: tuple[NodeState, ...]
    error_count: int
    trace_matrix: dict | None

def candidate_state(profile_path: Path, bundle_dir: Path) -> BundleState: ...
```

Authoritative-срез (`on_default/approved`) — этап B (ему нужен git/default branch);
`merged_unverified`/`waiting_human_merge` — состояния run'а, тоже этап B.

- [ ] **Step 1: Падающие тесты**

`tests/test_governance_bundle_state.py`:

```python
"""Candidate-срез read-модели бандла (спека §4): чистое чтение, никаких git-facts."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("steward")

from governance.bundle_state import BundleState, NodeState, candidate_state
from tests.governance_fixtures.bundles import (
    REQUIREMENTS_MD,
    make_bundle,
    make_profile,
)


def test_good_bundle_is_candidate_valid(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    state = candidate_state(profile, bundle)
    assert isinstance(state, BundleState)
    assert state.error_count == 0
    by_id = {n.node_id: n for n in state.nodes}
    assert by_id["behaviour-spec"].status == "candidate_valid"
    assert by_id["requirements"].status == "candidate_valid"


def test_bad_bundle_is_draft_with_findings(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=False)
    state = candidate_state(profile, bundle)
    assert state.error_count > 0
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "draft"
    assert beh.findings  # каждая строка несёт rule_id
    assert any("GC-" in f for f in beh.findings)


def test_missing_node_is_absent(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    (bundle / "15-behaviour-spec.md").unlink()
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "absent"


def test_stale_pin_marks_node_stale(tmp_path: Path) -> None:
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    (bundle / "10-requirements.md").write_text(
        REQUIREMENTS_MD + "\n## FR-2 (Must) Новое\n"
    )
    state = candidate_state(profile, bundle)
    beh = next(n for n in state.nodes if n.node_id == "behaviour-spec")
    assert beh.status == "stale"
    assert state.error_count > 0  # stale блокирует (спека §7: GC-STALE — ноль)


def test_no_git_facts_are_used(tmp_path: Path, monkeypatch) -> None:
    """Регрессия спеки §3/§9: candidate-срез не строит GitFacts вовсе."""
    import steward.gatecheck.git_facts as gf

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("candidate_state не должен трогать git_facts")

    monkeypatch.setattr(gf, "GitFacts", boom)
    profile = make_profile(tmp_path)
    bundle = make_bundle(tmp_path, behaviour_ok=True)
    assert candidate_state(profile, bundle).error_count == 0
```

- [ ] **Step 2: Красный прогон**

Run: `uv run --frozen --group governance pytest tests/test_governance_bundle_state.py -q`
Expected: FAIL (модуля нет).

- [ ] **Step 3: Реализация `governance/bundle_state.py`**

```python
"""Read-модель бандла, candidate-срез (спека §3/§4).

Единственное место, знающее раскладку бандла. Только контентные проверки:
три публичных символа пинованного steward + локальный stale-адаптер. Никаких
git-facts — регрессия закреплена тестом. Authoritative-срез (default branch,
--emit-verdicts) — этап B.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from steward.gatecheck.behaviour import check_behaviour_spec
from steward.gatecheck.checks import collect_bundle
from steward.gatecheck.trace_matrix import build_trace_matrix
from steward.graph import load_profile
from steward.roles import load_roles_catalog

from governance.stale_adapter import check_stale


@dataclass(frozen=True)
class NodeState:
    node_id: str
    status: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class BundleState:
    nodes: tuple[NodeState, ...]
    error_count: int
    trace_matrix: dict | None


def candidate_state(profile_path: Path, bundle_dir: Path) -> BundleState:
    """Состояние бандла по его содержимому (голова ветки, PR ещё может не быть)."""
    roles = load_roles_catalog(Path(profile_path).parent / "roles.yaml")
    graph = load_profile(profile_path, roles)
    artifacts, findings = collect_bundle(graph, bundle_dir)
    findings = [*findings, *check_behaviour_spec(graph, artifacts)]
    stale = check_stale(artifacts)

    per_node: dict[str, list[str]] = {}
    for f in findings:
        artifact_node = _node_of(artifacts, f.artifact)
        per_node.setdefault(artifact_node, []).append(
            f"{f.severity} {f.rule_id}: {f.message}"
        )
    stale_nodes: set[str] = set()
    for s in stale:
        node = _node_of(artifacts, s.artifact)
        stale_nodes.add(node)
        per_node.setdefault(node, []).append(
            f"error GC-STALE(prospective): {s.artifact} пин {s.upstream} "
            f"{s.pinned[:8]} != {(s.actual or 'absent')[:8]}"
        )

    present = {a.node_id for a in artifacts if a.node_id is not None}
    nodes: list[NodeState] = []
    for node_id in _profile_node_ids(graph):
        node_findings = tuple(per_node.get(node_id, ()))
        if node_id not in present:
            status = "absent"
        elif node_id in stale_nodes:
            status = "stale"
        elif any(f.startswith("error") for f in node_findings):
            status = "draft"
        else:
            status = "candidate_valid"
        nodes.append(NodeState(node_id, status, node_findings))

    error_count = sum(
        1
        for fs in per_node.values()
        for f in fs
        if f.startswith("error")
    )
    matrix = build_trace_matrix(graph, artifacts)
    return BundleState(tuple(nodes), error_count, matrix)


def _node_of(artifacts: list, path: str) -> str:
    for a in artifacts:
        if a.path == path and a.node_id is not None:
            return a.node_id
    return path  # finding о неизвестном файле группируется по пути


def _profile_node_ids(graph) -> list[str]:
    """Порядок узлов профиля; точный атрибут SpecGraph фиксируется Task 2."""
    return [node.id for node in graph.nodes]
```

ЗАМЕЧАНИЕ для исполнителя: `graph.nodes` / `node.id` — предположение о поверхности
`SpecGraph`; если characterization Task 2 показал иной атрибут — использовать его и
добавить в Task 2 закрепляющий assert (это единственное разрешённое расширение Task 2).

- [ ] **Step 4: Зелёный прогон + регресс**

Run: `uv run --frozen --group governance pytest tests/test_governance_bundle_state.py tests/test_governance_steward_surface.py tests/test_governance_stale_adapter.py tests/test_governance_merge_gate.py -q`
Expected: PASS все.

- [ ] **Step 5: Commit**

```bash
git add governance/bundle_state.py tests/test_governance_bundle_state.py
git commit -m "feat(governance): bundle_state — candidate-срез read-модели без git-facts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Обвязка, финальные проверки, PR

**Files:**
- Modify: `README.md` (короткая секция «Governance-ядро (этап A)» в таблицу/раздел)
- Modify: `TODO.md` (пункт `@id:behaviour-governance-core` + пункт-ожидание этапа B)
- Проверки: py_compile, полный pytest, line length.

**Interfaces:**
- Consumes: всё выше.

- [ ] **Step 1: README**

В таблицу инструментов README добавить строку:

```markdown
| `governance/` | ядро конвейера behaviour-spec (этап A): пин steward + characterization, merge_gate (оси ADR-ECO-011 × safety steward), prospective stale-адаптер, bundle_state; runner/TUI — этап B. Спека: docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md. Тесты: `uv run --frozen --group governance pytest tests/test_governance_*.py` |
```

- [ ] **Step 2: TODO.md**

В секцию Fleet issue console (или новую `## Behaviour-spec pipeline`) добавить:

```markdown
## Behaviour-spec pipeline

- [ ] Governance-ядро (этап A): пин steward + characterization, merge_gate, stale-адаптер, bundle_state @owner:github:andrei-shtanakov @id:behaviour-governance-core — спека docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md (v4, GO)
- [ ] Runner + console (этап B): S0–S8, waiting_human_merge/merged_unverified, textual-TUI @owner:github:andrei-shtanakov @id:behaviour-runner @blocked_by:todo://devtools/behaviour-governance-core — план пишется после мержа этапа A (characterization может сдвинуть дизайн)
```

- [ ] **Step 3: Финальные проверки**

```bash
python3 -m py_compile governance/__init__.py governance/merge_gate.py \
  governance/stale_adapter.py governance/bundle_state.py
uv run --frozen --group governance pytest -q
python3 - <<'EOF'
files = ["governance/merge_gate.py", "governance/stale_adapter.py",
         "governance/bundle_state.py", "governance/__init__.py",
         "tests/test_governance_merge_gate.py",
         "tests/test_governance_stale_adapter.py",
         "tests/test_governance_bundle_state.py",
         "tests/test_governance_steward_surface.py",
         "tests/governance_fixtures/bundles.py"]
bad = [(f, i) for f in files
       for i, l in enumerate(open(f), 1) if len(l.rstrip("\n")) > 88]
assert not bad, bad
print("88 ok")
EOF
```

Expected: компиляция чистая; ВСЕ тесты репо зелёные (в т.ч. существующие — группа
governance не должна ломать обычный `uv run --frozen pytest`, у которого steward не
установлен: поэтому во всех governance-тестах стоит `pytest.importorskip("steward")`
— проверить, что без группы они скипаются: `uv run --frozen pytest tests/test_governance_merge_gate.py -q`
даст PASS, остальные — SKIP); строк >88 нет.

- [ ] **Step 4: Commit + push + PR**

```bash
git add README.md TODO.md
git commit -m "docs(governance): обвязка этапа A + пункты плана в TODO

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feat/behaviour-governance-core
gh pr create --title "feat: behaviour governance core (этап A) — пин steward + merge_gate + bundle_state" --body "$(cat <<'EOF'
Спека: docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md (v4, GO, в этом же PR); план — docs/superpowers/plans/2026-08-30-behaviour-governance-core.md.

- uv-группа governance: пин steward@4a1c7c4; characterization-тесты трёх публичных символов и набора GC-BEH gate_id (спека §12 п.0).
- governance/merge_gate.py: чистая конъюнкция requested authority (ADR-ECO-011, только ужесточение) × safety-гейт steward × вердикт ревью × факты PR; табличные тесты по строкам спеки §8, включая регрессию «сегодня human по данным».
- governance/stale_adapter.py: prospective stale по локальным blob-хешам (временный, до candidate-контракта steward; семантика закреплена тестами).
- governance/bundle_state.py: candidate-срез read-модели без git-facts (регрессия — тестом с monkeypatch на GitFacts).
- Runner/console (S0–S8, TUI) — этап B, отдельный план после мержа: @id:behaviour-runner, @blocked_by этап A.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Ревью — по регламенту: `sh review-pr.sh devtools <pr> --dry-run --write-verdict <f>`,
затем `--use-verdict`. Мерж — по ADR-ECO-011 (агент от ai-prosto при approve; если у
ai-prosto ещё нет write-прав — передать владельцу с комментарием).
