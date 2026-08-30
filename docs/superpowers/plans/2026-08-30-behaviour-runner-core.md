# Behaviour Runner Core (этап B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Шаговый runner конвейера behaviour-spec (S0–S8) поверх governance-ядра
этапа A: убиваемый/возобновляемый, с policy-источниками осей merge_gate, честными
состояниями `waiting_human_merge`/`merged_unverified` и CLI-входом. TUI — этап B2.

**Architecture:** Три новых модуля в `governance/`: `policy_sources` (оси Authority
и Safety из вендоренной копии steward-политики и CLAUDE.md целевого репо),
`run_state` (журнал операций write-ahead + атомарный `run.json` в
`devtools/out/governance-runs/<run-id>/`), `ops` (протокол внешних эффектов
git/gh/review/codex/gate-check: RealOps — тонкие subprocess-обёртки, FakeOps в
тестах) и `runner` (машина S0–S8 с reconciliation перед каждым внешним эффектом).
Гейт S4 — `bundle_state.candidate_state` этапа A; право S7 — `merge_gate.decide`.

**Tech Stack:** Python ≥3.12; uv-группа `governance` (пин steward уже стоит,
console script `gate-check` доступен в venv — для S8); yaml — транзитивно из
steward (только в группе); gh CLI; `review-pr.sh` (S6); `codex exec` (S2/S3).

**Spec:** `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md`
(v4, GO). Этап B1 покрывает §4 (runner), §5 (S0–S8 без TUI), §6 (источники осей),
§12 п.3 + S8; console (§4 console.py) — этап B2.

## Global Constraints

- Ветка `feat/behaviour-runner-core`; коммиты с трейлером
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Внешние эффекты ТОЛЬКО через протокол `Ops`; ни одного прямого subprocess в
  `runner.py` — иначе тесты и reconciliation теряют смысл.
- Каждый шаг с внешним эффектом: `pending → started → completed` в журнале +
  reconciliation по фактическому состоянию ДО повторного эффекта (спека §4).
- Fail-closed везде: неизвестность/пустота/не-нулевой exit никогда не читаются
  как зелёное (спека §8).
- Runner пишет только под `devtools/out/governance-runs/<run-id>/` (плюс
  `.steward/` в целевом чекауте — ignored operational state S8, спека §4).
- Тесты: `uv run --frozen --group governance pytest tests/test_governance_*.py -q`;
  line length ≤ 88 (len() по символам, не awk).
- steward-политика — вендоренной пинованной копией
  (`contracts/steward-actor-policy/v1/`), integrity по sha256 из PIN;
  расхождение = `Safety(None, "unknown")`, не exception.

---

### Task 1: Вендор steward-actor-policy + policy_sources.py (+ tick TODO этапа A)

**Files:**
- Create: `contracts/steward-actor-policy/v1/approval-policy.yaml` (копия из
  `../steward/profiles/approval-policy.yaml` — чекаут steward на пин-коммите)
- Create: `contracts/steward-actor-policy/v1/PIN`
- Create: `governance/policy_sources.py`
- Test: `tests/test_governance_policy_sources.py`
- Modify: `TODO.md` (пункт `@id:behaviour-governance-core` → `[x]` с «PR #87»)
- Create: `docs/superpowers/plans/2026-08-30-behaviour-runner-core.md` (этот план,
  уже лежит untracked — закоммитить)

**Interfaces:**
- Produces: `load_safety(actor: str = "ai-prosto") -> Safety` (тип из
  `governance.merge_gate`); `repo_authority(target_dir: Path) -> str | None`
  («Мерж: человек» в CLAUDE.md → `"human"`, иначе None);
  `ecosystem_authority() -> str` (конфига флота ещё нет → `"agent"`, ECO-011
  переходное правило); `build_authority(target_dir, run_override) -> Authority`.

- [ ] **Step 1: Вендор копии**

```bash
cd ~/labs/all_ai_orchestrators/devtools
git switch -c feat/behaviour-runner-core
mkdir -p contracts/steward-actor-policy/v1
cp ../steward/profiles/approval-policy.yaml contracts/steward-actor-policy/v1/
shasum -a 256 contracts/steward-actor-policy/v1/approval-policy.yaml | \
  awk '{print $1 "  approval-policy.yaml  steward@4a1c7c44a85accf609b40cb14115eccefb26f6c2"}' \
  > contracts/steward-actor-policy/v1/PIN
```

Перед копированием проверить, что чекаут steward стоит на пине:
`git -C ../steward rev-parse master` == `4a1c7c44…` (иначе BLOCKED — спросить
контролёра).

- [ ] **Step 2: Падающие тесты**

```python
"""Оси merge_gate из реальных источников (спека §6): вендоренная политика + CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.merge_gate import Authority, Safety
from governance.policy_sources import (
    build_authority,
    ecosystem_authority,
    load_safety,
    repo_authority,
)


def test_safety_from_vendored_copy() -> None:
    s = load_safety("ai-prosto")
    assert isinstance(s, Safety)
    # факт сегодняшней политики: флаг False, ai-prosto не в списках -> unknown
    assert s.agent_merge_allowed is False
    assert s.actor_class == "unknown"


def test_safety_integrity_mismatch_is_unknown(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "v1"
    bad.mkdir()
    (bad / "approval-policy.yaml").write_text("agent_merge_allowed: true\n")
    (bad / "PIN").write_text("0" * 64 + "  approval-policy.yaml  x\n")
    monkeypatch.setattr("governance.policy_sources.CONTRACT_DIR", bad)
    s = load_safety("ai-prosto")
    assert s.agent_merge_allowed is None and s.actor_class == "unknown"


def test_safety_missing_copy_is_unknown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("governance.policy_sources.CONTRACT_DIR", tmp_path / "no")
    s = load_safety("ai-prosto")
    assert s.agent_merge_allowed is None and s.actor_class == "unknown"


def test_repo_authority_reads_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("## Git workflow\n- Мерж: человек\n")
    assert repo_authority(tmp_path) == "human"
    (tmp_path / "CLAUDE.md").write_text("обычный текст без объявления\n")
    assert repo_authority(tmp_path) is None
    assert repo_authority(tmp_path / "нет-такого") is None


def test_ecosystem_default_is_agent() -> None:
    assert ecosystem_authority() == "agent"


def test_build_authority_only_tightens(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("- Мерж: человек\n")
    a = build_authority(tmp_path, run_override=None)
    assert a == Authority(ecosystem="agent", repo="human", run=None)
    assert a.effective() == "human"
    b = build_authority(tmp_path / "пусто", run_override="human")
    assert b.run == "human" and b.effective() == "human"
```

Run: `uv run --frozen --group governance pytest tests/test_governance_policy_sources.py -q`
→ FAIL (модуля нет).

- [ ] **Step 3: Реализация `governance/policy_sources.py`**

```python
"""Оси merge_gate из реальных источников (спека §6).

Ось 1 (requested authority, ADR-ECO-011): экосистемный конфиг флота ещё не
существует (его схему фиксирует арка issue-runner) — по переходному правилу
ECO-011 отсутствие объявления читается как дефолт "agent". Репо-уровень —
строка «Мерж: человек» в CLAUDE.md целевого репо. Прогон может только
ужесточить (run_override).

Ось 2 (safety, steward): вендоренная пинованная копия среза approval-policy
(contracts/steward-actor-policy/v1/, integrity по sha256 из PIN). Любое
расхождение/отсутствие = Safety(None, "unknown") — fail-closed, merge_gate
сам отправит такой вердикт человеку. yaml — транзитивная зависимость пина
steward, живёт только в uv-группе governance.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from governance.merge_gate import Authority, Safety

CONTRACT_DIR = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "steward-actor-policy"
    / "v1"
)
_HUMAN_LINE = re.compile(r"(?m)^\s*[-*]?\s*Мерж:\s*человек\b")


def load_safety(actor: str = "ai-prosto") -> Safety:
    """Срез steward-политики из вендоренной копии; сбой = unknown."""
    policy = CONTRACT_DIR / "approval-policy.yaml"
    pin = CONTRACT_DIR / "PIN"
    try:
        expected = pin.read_text().split()[0]
        data = policy.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            return Safety(agent_merge_allowed=None, actor_class="unknown")
        doc = yaml.safe_load(data.decode("utf-8"))
        allowed = doc.get("agent_merge_allowed")
        if not isinstance(allowed, bool):
            return Safety(agent_merge_allowed=None, actor_class="unknown")
        agents = {str(x) for x in doc.get("agent_identities") or []}
        humans = {str(x) for x in doc.get("human_identities") or []}
        key = f"github:{actor}"
        if key in humans:
            actor_class = "human"
        elif key in agents:
            actor_class = "agent"
        else:
            actor_class = "unknown"
        return Safety(agent_merge_allowed=allowed, actor_class=actor_class)
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        return Safety(agent_merge_allowed=None, actor_class="unknown")


def repo_authority(target_dir: Path) -> str | None:
    """«Мерж: человек» в CLAUDE.md целевого репо -> human; иначе None."""
    claude = Path(target_dir) / "CLAUDE.md"
    try:
        text = claude.read_text(encoding="utf-8")
    except OSError:
        return None
    return "human" if _HUMAN_LINE.search(text) else None


def ecosystem_authority() -> str:
    """Конфиг флота ещё не существует: отсутствие объявления = agent (ECO-011)."""
    return "agent"


def build_authority(target_dir: Path, run_override: str | None) -> Authority:
    return Authority(
        ecosystem=ecosystem_authority(),
        repo=repo_authority(target_dir),
        run=run_override,
    )
```

Точные ключи yaml (`agent_identities`/`human_identities` — списки строк
`github:<login>` или иная форма) сверить с фактическим содержимым вендоренной
копии; если формат иной (например, маппинги) — адаптировать парсинг и тест
`test_safety_from_vendored_copy` под факт, инвариант «не в списках → unknown»
не менять.

- [ ] **Step 4: TODO tick + зелёный прогон + коммит**

В `TODO.md` строку `- [ ] Governance-ядро (этап A)…` → `- [x] …; PR #87`.

```bash
uv run --frozen --group governance pytest tests/test_governance_policy_sources.py -q
git add contracts/steward-actor-policy/ governance/policy_sources.py \
  tests/test_governance_policy_sources.py TODO.md \
  docs/superpowers/plans/2026-08-30-behaviour-runner-core.md
git commit -m "feat(governance): policy_sources — оси merge_gate из вендоренной политики и CLAUDE.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `run_state.py` — журнал операций и атомарный run.json

**Files:**
- Create: `governance/run_state.py`
- Test: `tests/test_governance_run_state.py`

**Interfaces:**
- Produces:

```python
RUNS_ROOT: Path  # devtools/out/governance-runs
@dataclass
class RunState:
    run_id: str; subject: str; repo: str; repo_slug: str; ws_id: str
    target_dir: str; bundle_dir: str; profile: str
    merge_authority: str | None   # run-уровень, только ужесточение (None|"human")
    status: str    # running|waiting_human_merge|stopped_review|merged_unverified|completed
    branch: str; pr: int | None; head: str | None
    ops: dict[str, dict]          # key -> {"status": pending|started|completed, ...result}
    remediated_by: str | None

def new_run(subject, repo, repo_slug, ws_id, target_dir, bundle_dir, profile,
            run_id, merge_authority=None) -> RunState   # run_id подаётся снаружи
def run_dir(run_id) -> Path
def save(state) -> None            # temp+os.replace, атомарно
def load(run_id) -> RunState
def op_status(state, key) -> str   # "new" если ключа нет
def op_start(state, key) -> None   # пишет started И СОХРАНЯЕТ (write-ahead)
def op_complete(state, key, **result) -> None  # completed + result + save
```

- [ ] **Step 1: Падающие тесты** (`tests/test_governance_run_state.py`)

```python
"""Журнал операций runner'а: write-ahead, атомарность, resume (спека §4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import run_state as rs


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path)
    return tmp_path


def _mk(runs_root) -> rs.RunState:
    s = rs.new_run(
        subject="тестовый функционал", repo="alpha", repo_slug="owner/alpha",
        ws_id="WS-T1", target_dir="/tmp/alpha", bundle_dir="workstreams/WS-T1/spec",
        profile="profiles/team-exp.yaml", run_id="r-0001",
    )
    rs.save(s)
    return s


def test_roundtrip(runs_root) -> None:
    s = _mk(runs_root)
    loaded = rs.load("r-0001")
    assert loaded == s
    assert loaded.status == "running" and loaded.ops == {}


def test_write_ahead_persists_started(runs_root) -> None:
    s = _mk(runs_root)
    rs.op_start(s, "branch")
    on_disk = rs.load("r-0001")
    assert rs.op_status(on_disk, "branch") == "started"  # записано ДО эффекта


def test_op_complete_stores_result(runs_root) -> None:
    s = _mk(runs_root)
    rs.op_start(s, "pr")
    rs.op_complete(s, "pr", number=87)
    on_disk = rs.load("r-0001")
    assert on_disk.ops["pr"]["status"] == "completed"
    assert on_disk.ops["pr"]["number"] == 87


def test_atomic_no_partial_file(runs_root) -> None:
    s = _mk(runs_root)
    rs.save(s)
    files = list(rs.run_dir("r-0001").iterdir())
    assert [f.name for f in files] == ["run.json"]
    assert json.loads((rs.run_dir("r-0001") / "run.json").read_text())


def test_run_override_only_tightens(runs_root) -> None:
    with pytest.raises(ValueError):
        rs.new_run(subject="s", repo="a", repo_slug="o/a", ws_id="w",
                   target_dir="/t", bundle_dir="b", profile="p",
                   run_id="r-2", merge_authority="agent")
```

Run → FAIL. **Step 2: Реализация** — dataclass + `asdict`/`json`; `new_run`
валидирует `merge_authority in (None, "human")` (ослаблять уровни выше нельзя —
спека §6); `save`: `run_dir.mkdir(parents=True)`, `tempfile.mkstemp` в том же
каталоге + `os.replace`; `op_start`/`op_complete` мутируют `state.ops` и зовут
`save` (write-ahead — спека §4). **Step 3:** зелёный прогон. **Step 4:** коммит
`feat(governance): run_state — write-ahead журнал прогона` (+трейлер).

---

### Task 3: `ops.py` — протокол внешних эффектов + RealOps

**Files:**
- Create: `governance/ops.py`
- Test: `tests/test_governance_ops.py`

**Interfaces:**
- Produces: `class Ops(Protocol)` с методами (все — единственная точка внешних
  эффектов; сигнатуры дословно):

```python
ensure_branch(target_dir: str, branch: str) -> None      # создать если нет
head_sha(target_dir: str, branch: str) -> str
push_branch(target_dir: str, branch: str) -> None
find_pr(repo_slug: str, branch: str) -> int | None
create_draft_pr(target_dir: str, repo_slug: str, branch: str,
                title: str, body: str, label: str) -> int
mark_ready(repo_slug: str, pr: int) -> None
review(repo_name: str, pr: int) -> int                   # exit-код review-pr.sh
pr_facts(repo_slug: str, pr: int) -> dict                # сырой gh-JSON
pr_files(repo_slug: str, pr: int) -> list[str]
unresolved_threads(repo_slug: str, pr: int) -> bool | None  # None = не смогли узнать
merge(repo_slug: str, pr: int, sha: str) -> bool         # PUT от ai-prosto
comment(repo_slug: str, pr: int, body: str) -> None
author(target_dir: str, kind: str, subject: str, bundle_dir: str) -> int
gate_check_s8(target_dir: str, bundle_dir: str, profile: str) -> int
create_issue(repo_slug: str, title: str, body: str) -> int
```

  и `class RealOps` (subprocess-обёртки). FakeOps живёт в тестах runner'а
  (Task 4), не здесь.

- [ ] **Step 1: Тесты построения команд** — RealOps тестируется перехватом
  argv (monkeypatch `governance.ops.subprocess.run` на фейк, возвращающий
  заданные stdout/returncode), БЕЗ живых вызовов:

```python
"""RealOps: точные команды внешних эффектов (спека §5/§8)."""
```

Кейсы (каждый — отдельный тест, проверяющий argv и разбор результата):
1. `merge` → команда `gh api -X PUT repos/<slug>/pulls/<pr>/merge -f merge_method=merge -f sha=<sha>` и env содержит `GH_CONFIG_DIR=~/.config/review` (актор — ai-prosto, ADR-ECO-011 D3); returncode 0 → True, не-0 → False (не exception);
2. `review` → `["sh", str(<devtools>/review-pr.sh), repo_name, str(pr)]`, cwd=devtools-корень; возврат = returncode как есть (0/1/2/3/4);
3. `create_draft_pr` → `gh pr create --draft --label codex-review …` (лейбл обязателен — спека §5 S5) c `-R <slug>`; из stdout-URL извлекается номер;
4. `gate_check_s8` → команда `[<venv>/bin/gate-check или "gate-check", "--bundle", <bundle>, "--profile", <profile>, "--emit-verdicts"]`, cwd=target_dir; ТОЧНЫЕ флаги CLI сверить с `gate-check --help` пинованного скрипта (он установлен в venv) и зафиксировать в тесте фактические — это мини-характеризация;
5. `author` → `codex exec --ephemeral --sandbox workspace-write <prompt>`, cwd=target_dir; промпт содержит kind, subject и bundle_dir;
6. `unresolved_threads` → gh GraphQL-запрос reviewThreads; ошибка запроса → None (не False!);
7. `create_issue` → `gh issue create -R <slug> --label inbox …`.

- [ ] **Step 2: Реализация RealOps** — тонкие методы, каждый ≤15 строк;
  `pr_facts` = `gh pr view <pr> -R <slug> --json mergeable,mergeStateStatus,statusCheckRollup,isDraft,headRefOid,baseRefName,state,mergedAt`;
  никаких интерпретаций в ops (интерпретация — в runner). `gate-check --help`
  прогнать один раз при реализации и записать фактическую сигнатуру CLI в
  докстринг метода. **Step 3:** зелёный прогон. **Step 4:** коммит
  `feat(governance): ops — протокол внешних эффектов и RealOps` (+трейлер).

---

### Task 4: `runner.py` — машина S0–S7 с reconciliation

**Files:**
- Create: `governance/runner.py`
- Test: `tests/test_governance_runner.py` (+ FakeOps здесь же)

**Interfaces:**
- Consumes: run_state (T2), Ops (T3), `policy_sources.build_authority`/
  `load_safety` (T1), `bundle_state.candidate_state`, `merge_gate.decide`.
- Produces:

```python
def start(subject, repo, repo_slug, ws_id, target_dir, bundle_dir, profile,
          run_id, ops, merge_authority=None) -> RunState   # S0 + advance()
def advance(state, ops) -> RunState   # выполняет шаги до стопа/завершения
def facts_from(pr_facts: dict, files: list[str], threads: bool | None) -> PrFacts
```

Шаги и их op-ключи: `branch`(S1) → `author-charter`/`author-requirements`(S2) →
`author-behaviour`(S3) → `gate-candidate`(S4) → `push`+`pr`(S5) →
`ready`+`review`(S6) → `verdict`+`merge`(S7). Каждый шаг: если op completed —
пропустить; если started — сперва reconciliation (см. ниже), потом решать.

**Reconciliation (спека §4, дословно в код):**
- `branch` started: `ensure_branch` идемпотентен по построению (создать-если-нет);
- `pr` started: `find_pr(slug, branch)` — есть → op_complete с его номером,
  второй PR не открывать;
- `review` started: exit неизвестен → перезапустить `review` (review-pr.sh сам
  наследует вердикт по fp — повтор дешёвый);
- `merge` started: `pr_facts.state == MERGED` → op_complete, иначе повторить
  merge с тем же sha (PUT с sha безопасен при гонке).

**S2/S3:** для каждого из `charter`/`requirements`/`behaviour-spec`: если файл
`<bundle_dir>/<NN>-<kind>.md` уже существует — op_complete(skipped=True), иначе
`ops.author(...)`; exit != 0 → status `stopped_author`, стоп.

**S4:** `candidate_state(profile, bundle)`; `error_count > 0` → status
`stopped_gate`, findings в `run_dir/gate-findings.txt`, стоп. `required_absent`
не блокирует, но пишется в результат op (спека: выбор узлов этапа — дело
потребителя; для конвейера B1 обязательные узлы — charter/requirements/
behaviour-spec — гарантированы S2/S3).

**S6:** `ops.review(repo, pr)`; exit 0 → op_complete(exit=0); exit 1 →
`comment` («ревью нашло находки, прогон остановлен») + status `stopped_review`;
2/3 → comment («прибор не отработал») + `stopped_review`; 4 → op сбрасывается в
pending (голова уехала — повторить весь S6 при следующем advance).

**S7:** `authority = build_authority(target_dir, state.merge_authority)`;
`safety = load_safety()`; `facts = facts_from(...)`; `verdict = decide(...)`.
`agent` → `ops.merge(slug, pr, head)`; True → op_complete + переход к S8-фазе
(Task 5); False → comment + `waiting_human_merge`. `human`/`refuse` → comment
с reason + status `waiting_human_merge` (refuse — `stopped_gate`).

**`facts_from` маппинг (fail-closed):** rollup: непустой список и все
conclusion ∈ {SUCCESS, NEUTRAL, SKIPPED} → "green"; пустой список → "empty";
любой FAILURE/… → "red"; иначе "unknown". mergeable: "MERGEABLE" → "mergeable",
"CONFLICTING" → "conflicting", иначе "unknown". behind_base: из
mergeStateStatus == "BEHIND". unresolved_threads: None → True (не смогли узнать
= считаем нерешёнными). diff_class: все пути под bundle_dir/ или docs/ →
"document", иначе "code". touches_authority_root: путь начинается с ".github/"
или "profiles/".

- [ ] **Step 1: FakeOps + падающие тесты.** FakeOps — класс со сценарием
  (dict-поля: existing_branches, existing_prs, review_exit, facts, merge_ok,
  authored=[], comments=[], merged=[]), каждый метод пишет в журнал вызовов.
  Тесты (минимум):

1. `test_happy_path_agent_merge` — чистый прогон: FakeOps с review_exit=0,
   зелёными facts; monkeypatch `load_safety` → Safety(True, "agent") и
   `candidate_state` → BundleState с error_count=0; итог: merge вызван с head,
   статус после S7 — продолжение на S8 (в B1-тесте Task 4: op "merge" completed);
2. `test_today_reality_waits_human` — БЕЗ monkeypatch safety (реальная
   вендоренная копия: allowed=False) → merge НЕ вызывался, comment оставлен,
   status waiting_human_merge;
3. `test_review_request_changes_stops` — review_exit=1 → stopped_review,
   merge не вызывался;
4. `test_resume_does_not_duplicate_pr` — первый advance убит после создания PR
   (op "pr" started, FakeOps.existing_prs содержит его); повторный advance —
   `create_draft_pr` НЕ вызван второй раз, номер взят из find_pr;
5. `test_gate_red_stops` — candidate_state → error_count=2 → stopped_gate,
   PR не создавался;
6. `test_author_skips_existing_files` — файлы бандла существуют →
   ops.author не вызывался;
7. `test_facts_from_fail_closed` — таблица: пустой rollup → "empty",
   threads=None → unresolved True, файл вне bundle_dir → "code".

- [ ] **Step 2: Реализация** по описанию выше; `advance` — цикл по шагам с
  ранним выходом на любом не-running статусе. **Step 3:** зелёный прогон +
  регресс всех governance-тестов. **Step 4:** коммит
  `feat(governance): runner S0–S7 — идемпотентная машина с reconciliation`.

---

### Task 5: S8 + `merged_unverified` + дочерний verification-run

**Files:**
- Modify: `governance/runner.py`, `governance/run_state.py` (поле уже есть)
- Test: `tests/test_governance_runner.py` (дополнение)

**Interfaces:**
- Produces: `resume(run_id, ops) -> RunState` (после human-мержа:
  `waiting_human_merge` + pr_facts.state==MERGED → S8); `verify(parent_run_id,
  ops, run_id) -> RunState` (дочерний run c `remediated_by`, исполняет только S8).

Логика S8 (op `gate-authoritative`): `ops.gate_check_s8(...)`; exit 0 → status
`completed`. Не-0 → status `merged_unverified` НАВСЕГДА (спека §5):
- вывод gate-check → `run_dir/s8-findings.txt`;
- `ops.create_issue(repo_slug, …)` — remediation-issue по ADR-ECO-006: тело
  начинается `slug: beh-remediation-<ws_id>` и `from: devtools#<run_id>`,
  дальше findings; лейбл inbox (в create_issue уже зашит);
- исходный run никогда не переходит в completed; повторный `advance`/`resume`
  на merged_unverified — отказ с подсказкой «создайте verification-run»;
- `verify(...)`: новый RunState (remediated_by=parent, те же координаты),
  сразу S8; exit 0 → completed (у ПОТОМКА), parent не трогается.

Тесты: happy S8 → completed; s8 fail → merged_unverified + issue создан с
`slug:`/`from:` в теле + повторный advance отвергнут; verify-потомок доходит до
completed, parent остаётся merged_unverified; resume из waiting_human_merge при
MERGED-факте идёт в S8, при OPEN — остаётся ждать. Коммит
`feat(governance): S8 authoritative + merged_unverified + verification-run`.

---

### Task 6: CLI, make-цель, README/TODO, финальные проверки

**Files:**
- Modify: `governance/runner.py` (блок `main()` + `if __name__`), `Makefile`,
  `README.md`, `TODO.md`

**Interfaces:**
- Produces: `uv run --frozen --group governance python -m governance.runner
  start|resume|verify|status …`; make-цель `behaviour-run`.

- [ ] **Step 1: CLI** — argparse-сабкоманды:
  `start --subject … --repo … --repo-slug … --ws-id … --target-dir …
  [--bundle-dir workstreams/<ws>/spec] [--profile profiles/team-exp.yaml]
  [--merge-authority human] [--run-id …]` (run_id по умолчанию:
  `f"{ws_id}-{os.urandom(3).hex()}"`);
  `resume --run-id`; `verify --parent --run-id`; `status --run-id` (печать
  run.json человекочитаемо). Все команды строят RealOps.
- [ ] **Step 2: Makefile** — `behaviour-run: ; @uv run --frozen --group governance python -m governance.runner $(ARGS)`
  + help-строка. README: короткий блок в секцию governance (start/resume/verify,
  где лежат run'ы, что S7 сегодня уходит в waiting_human_merge из-за
  agent_merge_allowed=false — по данным, не по коду). TODO: пункт
  `@id:behaviour-runner` дополнить «B1 (runner) — PR #<этот>; B2 (console) — следующий».
- [ ] **Step 3: Финальные проверки**

```bash
python3 -m py_compile governance/*.py
uv run --frozen --group governance pytest -q          # весь репо
uv sync --frozen && uv run --frozen pytest -q          # skip-гигиена без группы
uv sync --frozen --group governance                    # вернуть venv
# line length: python len() по всем новым/правленым файлам
```

- [ ] **Step 4: Коммит + push + PR** (`gh pr create`, тело: что/чего
  стоило/незакрыто — B2 console, ecosystem-config, steward identities issue).
  Приёмка и мерж — контролёром: `review-pr.sh --dry-run --write-verdict` →
  `--use-verdict` → **мерж от ai-prosto** (боевой тест DarkFactory после
  переработки рулсета).
