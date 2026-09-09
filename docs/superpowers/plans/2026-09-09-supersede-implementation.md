# Переиздание tasks-спеки (supersede) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Санкционированное переиздание tasks-спеки после correction'а
вышестоящего бандла — новое поколение доставки со своей веткой, своим PR и
своей записью в леджере.

**Architecture:** Ревизии `tasks-deliver-v<N>` в `run.json` (старые записи
неприкосновенны, связь вперёд через `supersedes`); проспективный
пост-штамповый anchor как идентичность доставки; provenance correction-PR как
источник подписи штампа; таблицы реконсиляции и восстановления коммита как
явные переходы, а не «продолжить с шага N».

**Tech Stack:** Python stdlib + PyYAML (канон `governance/`), pytest,
FakeOps/`_StubOps`-фикстуры.

**Spec:** `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md`
(влита PR #163). Конфликты решаются в пользу спеки.

## Global Constraints

- Ключ ревизии — ровно `tasks-deliver-v<N>`; `N` выводится ИЗ ЛЕДЖЕРА
  (максимальная существующая ревизия + 1), не из имён веток.
- Терминальные статусы ревизии — только `completed` и `abandoned`. Статуса
  `superseded` НЕТ: связь выражает поле `supersedes: <N>` у новой ревизии.
- Завершённые ревизии и op `tasks-deliver` (v1) НИКОГДА не перезаписываются.
- Равный anchor — успех (RC 0) и бесследность: `run.json` до и после
  побайтово совпадает.
- Никаких автоматических мержей; переиздание открывает PR и останавливается.
- Порядок внутри доставки не меняется: dirty-гард → `checkout_and_pull` →
  existence/composition/preflight/DT-граф → `ensure_branch` → штамп → рендер →
  commit → push → `create_draft_pr`.
- `git`-чекаут не является delivery-эффектом; бесследность относится к
  `run.json`.
- Стиль — канон соседнего кода; 88 chars; ruff-гейта в репо нет.
- Тесты: `uv run --frozen --group governance python -m pytest tests/ -q; echo RC=$?`.

## Карта файлов

- Modify: `governance/task_bridge.py` (ревизии, проспективный штамп,
  provenance, реконсиляция, CLI-флаги), `governance/ops.py` (новые
  read-only-операции git/gh), `README.md` (описание флагов).
- Test: `tests/test_governance_task_bridge.py` (все новые сценарии),
  `tests/test_governance_ops.py` (контракт новых Ops-методов).

---

### Task 1: Ops — read-only примитивы для provenance и восстановления

**Files:**
- Modify: `governance/ops.py`
- Test: `tests/test_governance_ops.py`

**Interfaces:**
- Produces (Task 2–7 потребляют):
  - `Ops.last_commit_touching(target_dir: str, rel_path: str) -> str | None`
  - `Ops.prs_containing_commit(repo_slug: str, sha: str) -> list[dict]`
  - `Ops.rev_parse(target_dir: str, ref: str) -> str | None`
  - `Ops.blob_in_commit(target_dir: str, sha: str, rel_path: str) -> str | None`
  - `Ops.commit_parent(target_dir: str, sha: str) -> str | None`

- [ ] **Step 1: Красные тесты** (`tests/test_governance_ops.py`)

```python
def test_ops_protocol_declares_provenance_primitives() -> None:
    from governance.ops import Ops

    for name in (
        "last_commit_touching", "prs_containing_commit",
        "rev_parse", "blob_in_commit", "commit_parent",
    ):
        assert hasattr(Ops, name), name


def test_real_ops_last_commit_touching_returns_none_without_history(
    tmp_path,
) -> None:
    import subprocess
    from governance.ops import RealOps

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert RealOps().last_commit_touching(str(tmp_path), "nope.md") is None
```

- [ ] **Step 2: Прогнать — FAIL.**

Run: `uv run --frozen --group governance python -m pytest tests/test_governance_ops.py -q; echo RC=$?`

- [ ] **Step 3: Реализация в `governance/ops.py`**

В `Ops`-протокол добавить объявления, в `RealOps` — реализации. Канон
соседей: `subprocess.run(..., capture_output=True, text=True)`, rc != 0 —
`RuntimeError` с текстом stderr (транзиентный сбой НЕ равен «нет данных»).

```python
    def last_commit_touching(self, target_dir: str, rel_path: str) -> str | None:
        """SHA последнего коммита, изменившего rel_path; None — не менялся."""
        done = subprocess.run(
            ["git", "-C", target_dir, "log", "-1", "--format=%H", "--", rel_path],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"last_commit_touching: git log rc={done.returncode}: "
                f"{done.stderr.strip()}"
            )
        sha = done.stdout.strip()
        return sha or None

    def prs_containing_commit(self, repo_slug: str, sha: str) -> list[dict]:
        """PR-ы, содержащие коммит (gh API). Сбой запроса — RuntimeError."""
        done = subprocess.run(
            ["gh", "api", f"repos/{repo_slug}/commits/{sha}/pulls",
             "--jq", "[.[] | {number, state, baseRefName: .base.ref, "
                     "mergedAt, mergedBy: .merged_by.login, "
                     "mergeCommit: .merge_commit_sha}]"],
            capture_output=True, text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"prs_containing_commit: gh api rc={done.returncode}: "
                f"{done.stderr.strip()}"
            )
        try:
            found = json.loads(done.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"prs_containing_commit: invalid JSON: {done.stdout!r}"
            ) from exc
        return found

    def rev_parse(self, target_dir: str, ref: str) -> str | None:
        """SHA ссылки; None — ссылки нет (нормальный случай, не сбой)."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True,
        )
        return done.stdout.strip() or None

    def blob_in_commit(
        self, target_dir: str, sha: str, rel_path: str
    ) -> str | None:
        """blob-хеш файла в коммите; None — файла в нём нет."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", f"{sha}:{rel_path}"],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else None

    def commit_parent(self, target_dir: str, sha: str) -> str | None:
        """SHA первого родителя; None — корневой коммит либо нет коммита."""
        done = subprocess.run(
            ["git", "-C", target_dir, "rev-parse", "--verify", "--quiet",
             f"{sha}^1"],
            capture_output=True, text=True,
        )
        return done.stdout.strip() or None
```

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/ops.py tests/test_governance_ops.py
git commit -m "feat(ops): read-only примитивы provenance и восстановления коммита"
```

---

### Task 2: Ревизии в леджере — нумерация, чтение, запись намерения

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Produces:
  - `_REVISION_PREFIX = "tasks-deliver-v"`
  - `_revisions(state) -> list[tuple[int, dict]]` — (N, op) по возрастанию N
  - `_last_delivery(state) -> tuple[int, dict] | None` — последняя запись
    доставки: максимальная ревизия либо v1-op `tasks-deliver`; N=1 для v1
  - `_next_revision(state) -> int` — max(N) + 1, минимум 2
  - `_start_revision(state, n, intent: dict) -> None` — write-ahead
    `op_start`-эквивалент с полным намерением
  - `_complete_revision(state, n, **result) -> None`
  - `_abandon_revision(state, n, reason: str) -> None`

- [ ] **Step 1: Красные тесты**

```python
def test_revision_numbering_starts_at_two_and_grows(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    assert tb._next_revision(state) == 2
    tb._complete_revision(state, 2, pr=10, anchor="a")
    assert tb._next_revision(state) == 3


def test_last_delivery_prefers_highest_revision(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    n, op = tb._last_delivery(state)
    assert (n, op["pr"]) == (1, 5)
    tb._complete_revision(state, 2, pr=10, anchor="a")
    n2, op2 = tb._last_delivery(state)
    assert (n2, op2["pr"]) == (2, 10)


def test_start_revision_records_full_intent(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {
        "branch": "spec/WS-alpha-7-tasks-v2",
        "base_sha": "deadbeef",
        "prospective_anchor": "anchor2",
        "approval_pr": 77,
        "tasks_version": 3,
        "dag": ["00-charter.md"],
        "dag_source": "previous_delivery",
        "supersedes": 1,
        "expected_generated_at": "2026-09-09T10:00:00+03:00",
        "tasks_blob": "blob2",
    })
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "started"
    assert saved["revision"] == 2
    assert saved["head_sha"] is None
    assert saved["supersedes"] == 1
    assert saved["base_sha"] == "deadbeef"


def test_abandon_revision_is_terminal_and_keeps_reason(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    tb._abandon_revision(state, 2, "оператор закрыл PR #99")
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "abandoned"
    assert "PR #99" in saved["reason"]


def test_completed_v1_op_is_never_rewritten(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    before = dict(state.ops["tasks-deliver"])
    tb._complete_revision(state, 2, pr=10, anchor="a")
    assert rs.load("r-recon").ops["tasks-deliver"] == before
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация** (в `governance/task_bridge.py`, рядом с
      `deliver_for_run`)

```python
#: Ключ ревизии переиздания. v1 живёт под историческим `tasks-deliver`
#: (§I4 спеки): переименовать её значило бы переписать журнал.
_REVISION_PREFIX = "tasks-deliver-v"
_V1_KEY = "tasks-deliver"


def _revisions(state: RunState) -> list[tuple[int, dict]]:
    """(N, op) всех ревизий переиздания по возрастанию N."""
    found: list[tuple[int, dict]] = []
    for key, op in state.ops.items():
        if not key.startswith(_REVISION_PREFIX):
            continue
        suffix = key[len(_REVISION_PREFIX):]
        if suffix.isdigit():
            found.append((int(suffix), op))
    return sorted(found)


def _last_delivery(state: RunState) -> tuple[int, dict] | None:
    """Последняя запись доставки: старшая ревизия либо v1; None — доставок нет.

    Ревизии в статусе `abandoned` пропускаются: они не доставили ничего,
    и сравнивать anchor с ними нельзя.
    """
    for n, op in reversed(_revisions(state)):
        if op.get("status") != "abandoned":
            return n, op
    v1 = state.ops.get(_V1_KEY)
    return (1, v1) if v1 else None


def _next_revision(state: RunState) -> int:
    """Следующий N: максимум из леджера + 1, минимум 2 (v1 — историческая)."""
    revs = _revisions(state)
    return (revs[-1][0] + 1) if revs else 2


def _start_revision(state: RunState, n: int, intent: dict) -> None:
    """Write-ahead намерения ревизии (§I4): пишется ДО единого эффекта."""
    state.ops[f"{_REVISION_PREFIX}{n}"] = {
        "status": "started", "revision": n, "head_sha": None, **intent,
    }
    save(state)


def _complete_revision(state: RunState, n: int, **result: object) -> None:
    state.ops[f"{_REVISION_PREFIX}{n}"] = {
        **state.ops.get(f"{_REVISION_PREFIX}{n}", {"revision": n}),
        "status": "completed", **result,
    }
    save(state)


def _abandon_revision(state: RunState, n: int, reason: str) -> None:
    """Терминальный `abandoned` с причиной — причина хранится навсегда."""
    key = f"{_REVISION_PREFIX}{n}"
    if key not in state.ops:
        raise RuntimeError(f"ревизии {n} нет в леджере — нечего абандонить")
    if state.ops[key].get("status") == "completed":
        raise RuntimeError(
            f"ревизия {n} завершена (PR #{state.ops[key].get('pr')}) — "
            "завершённая запись не мутируется"
        )
    state.ops[key] = {**state.ops[key], "status": "abandoned", "reason": reason}
    save(state)
```

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): ревизии переиздания в леджере — нумерация, намерение, терминальные статусы"
```

---

### Task 3: Provenance correction-PR

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: `Ops.last_commit_touching`, `Ops.prs_containing_commit` (Task 1).
- Produces: `_resolve_correction_pr(state, ops, anchor_rel, base_ref,
  approval_pr: int | None) -> tuple[int, str, str]` → `(pr, approved_by,
  approved_at)`; любое препятствие — `RuntimeError`.

- [ ] **Step 1: Красные тесты**

```python
class _ProvOps(_StubOps):
    def __init__(self, commit="c1", prs=None):
        super().__init__()
        self.commit, self.prs = commit, (prs if prs is not None else [])

    def last_commit_touching(self, target_dir, rel_path):
        return self.commit

    def prs_containing_commit(self, repo_slug, sha):
        return self.prs


_MERGED_PR = {
    "number": 403, "state": "MERGED", "baseRefName": "master",
    "mergedAt": "2026-09-09T05:00:00Z", "mergedBy": "andrei-shtanakov",
    "mergeCommit": "c1",
}


def test_provenance_single_merged_candidate(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    pr, by, at = tb._resolve_correction_pr(
        state, _ProvOps(prs=[_MERGED_PR]),
        "workstreams/WS-alpha-7/spec/30-decomposition.md", "master", None,
    )
    assert (pr, by) == (403, "andrei-shtanakov")
    assert at.startswith("2026-09-09")


def test_provenance_zero_candidates_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="подписи взять неоткуда"):
        tb._resolve_correction_pr(
            state, _ProvOps(prs=[]), "a/b.md", "master", None
        )


def test_provenance_two_candidates_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    other = {**_MERGED_PR, "number": 999}
    with pytest.raises(RuntimeError, match="подписи взять неоткуда"):
        tb._resolve_correction_pr(
            state, _ProvOps(prs=[_MERGED_PR, other]), "a/b.md", "master", None
        )


def test_provenance_no_commit_touching_anchor_refuses(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="не менялся"):
        tb._resolve_correction_pr(
            state, _ProvOps(commit=None), "a/b.md", "master", None
        )


def test_provenance_explicit_flag_is_verified_not_trusted(
    tmp_path, monkeypatch
):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)

    class _Explicit(_ProvOps):
        def pr_facts(self, repo_slug, pr):
            return {"state": "OPEN", "baseRefName": "master"}

    with pytest.raises(RuntimeError, match="не вмержен"):
        tb._resolve_correction_pr(
            state, _Explicit(), "a/b.md", "master", 500
        )
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

```python
def _resolve_correction_pr(
    state: RunState,
    ops: Ops,
    anchor_rel: str,
    base_ref: str,
    approval_pr: int | None,
) -> tuple[int, str, str]:
    """PR, доставивший correction → (номер, approved_by, approved_at).

    §I7 спеки: подпись штампа берётся у correction-PR, а не у исходного
    бандл-PR — иначе штамп утверждает, что текущие байты одобрил человек,
    одобрявший другую версию. Ноль или больше одного кандидатов — отказ:
    гадать нельзя. `--approval-pr` заменяет ПОИСК (шаги 1–3), но не
    ПРОВЕРКУ (шаг 4).
    """
    if approval_pr is None:
        sha = ops.last_commit_touching(state.target_dir, anchor_rel)
        if sha is None:
            raise RuntimeError(
                f"{anchor_rel} не менялся в истории {base_ref} — "
                "correction не найден, подписи взять неоткуда"
            )
        candidates = [
            p for p in ops.prs_containing_commit(state.repo_slug, sha)
            if p.get("state") == "MERGED" and p.get("baseRefName") == base_ref
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"коммит {sha[:7]} связан с {len(candidates)} вмерженными "
                f"PR в {base_ref} — подписи взять неоткуда; назовите PR "
                "явно: --approval-pr <n>"
            )
        cand = candidates[0]
        number = cand["number"]
        merged_by = cand.get("mergedBy")
        merged_at = cand.get("mergedAt")
    else:
        number = approval_pr
        facts = ops.pr_facts(state.repo_slug, number)
        if facts.get("state") != "MERGED":
            raise RuntimeError(
                f"--approval-pr {number}: PR не вмержен "
                f"(state={facts.get('state')!r}) — проверка та же, что у "
                "автоматического поиска"
            )
        if facts.get("baseRefName") != base_ref:
            raise RuntimeError(
                f"--approval-pr {number}: нацелен в "
                f"{facts.get('baseRefName')!r}, а не в {base_ref!r}"
            )
        merged_by = (facts.get("mergedBy") or {}).get("login")
        merged_at = facts.get("mergedAt")
    if not merged_by or not merged_at:
        raise RuntimeError(
            f"PR #{number}: нет mergedBy/mergedAt — подпись штампа неполна"
        )
    return number, merged_by, merged_at
```

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): provenance correction-PR — поиск, верификация, явный --approval-pr"
```

---

### Task 4: Проспективный штамп и anchor

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: `stamp_bundle_approved` (существующая), `_dag_for`, `_node_id`.
- Produces: `_prospective_anchor(target_dir, bundle_dir, approved_by,
  approved_at, legacy_bundle) -> str` — blob-хеш терминального узла ПОСЛЕ
  штампа, вычисленный БЕЗ записи на диск.

- [ ] **Step 1: Красные тесты**

```python
def test_prospective_anchor_matches_real_stamp(tmp_path) -> None:
    """Проспективный anchor равен тому, что даст фактический штамп."""
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    prospective = tb._prospective_anchor(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z", None,
    )
    tb.stamp_bundle_approved(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z",
    )
    actual = blob_sha1(
        (target / "workstreams/WS-alpha-7/spec/30-decomposition.md")
        .read_text(encoding="utf-8")
    )
    assert prospective == actual


def test_prospective_anchor_writes_nothing(tmp_path) -> None:
    from governance import task_bridge as tb
    from governance.stale_adapter import blob_sha1

    target = _target(tmp_path)
    anchor_file = target / "workstreams/WS-alpha-7/spec/30-decomposition.md"
    before = blob_sha1(anchor_file.read_text(encoding="utf-8"))
    tb._prospective_anchor(
        str(target), "workstreams/WS-alpha-7/spec", "ai-prosto",
        "2026-09-09T05:00:00Z", None,
    )
    after = blob_sha1(anchor_file.read_text(encoding="utf-8"))
    assert before == after
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

`stamp_bundle_approved` пишет файлы на диск. Проспективный вариант обязан
пройти те же преобразования без записи. Реализация — через временную копию
каталога бандла (`tempfile.TemporaryDirectory`), где штамп выполняется
физически, но вне рабочего дерева:

```python
def _prospective_anchor(
    target_dir: str,
    bundle_dir: str,
    approved_by: str,
    approved_at: str,
    legacy_bundle: int | None = None,
) -> str:
    """blob терминального узла ПОСЛЕ штампа, без записи в рабочее дерево.

    §I2 спеки: сравнивать надо те байты, что уйдут в PR и после мержа
    лягут в base, но штамп — эффект. Поэтому бандл копируется во временный
    каталог, штампуется ТАМ, и хеш берётся оттуда; рабочее дерево не
    трогается вовсе (проверяется тестом `..._writes_nothing`).
    """
    dag = _dag_for(legacy_bundle)
    with tempfile.TemporaryDirectory(prefix="prospective-stamp-") as tmp:
        shadow = Path(tmp) / "target"
        (shadow / bundle_dir).mkdir(parents=True)
        for fname, _ in dag:
            src = Path(target_dir) / bundle_dir / fname
            (shadow / bundle_dir / fname).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        stamp_bundle_approved(
            str(shadow), bundle_dir, approved_by, approved_at,
            legacy_bundle=legacy_bundle,
        )
        anchor_file = shadow / bundle_dir / dag[-1][0]
        return blob_sha1(anchor_file.read_text(encoding="utf-8"))
```

Импорт `tempfile` и `shutil` не нужен сверх stdlib-набора файла (`tempfile`
добавить в начало модуля рядом с существующими импортами).

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): проспективный пост-штамповый anchor без записи в дерево"
```

---

### Task 5: Сверка DAG предыдущей доставки

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Produces: `_previous_dag(state, prev_op, target_dir, bundle_dir) ->
  tuple[tuple[tuple[str, tuple[str, ...]], ...] | None, str]` →
  `(dag, dag_source)`, где `dag_source ∈ {"previous_delivery",
  "derived_from_spec", "unavailable"}`.

- [ ] **Step 1: Красные тесты**

```python
def test_previous_dag_from_revision_record(tmp_path, monkeypatch) -> None:
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    prev = {"dag": [list(x) for x in tb._BUNDLE_DAG]}
    dag, source = tb._previous_dag(
        state, prev, state.target_dir, state.bundle_dir
    )
    assert source == "previous_delivery"
    assert dag == tb._BUNDLE_DAG


def test_previous_dag_derived_from_bundle_composition(
    tmp_path, monkeypatch
) -> None:
    """Легаси-v1 без записи dag: состав каталога совпадает ровно с одним
    вариантом _dag_for."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    dag, source = tb._previous_dag(
        state, {"pr": 5}, state.target_dir, state.bundle_dir
    )
    assert source == "derived_from_spec"
    assert dag == tb._BUNDLE_DAG


def test_previous_dag_unavailable_when_composition_matches_nothing(
    tmp_path, monkeypatch
) -> None:
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / state.bundle_dir / "99-alien.md").write_text("x")
    dag, source = tb._previous_dag(
        state, {"pr": 5}, state.target_dir, state.bundle_dir
    )
    assert (dag, source) == (None, "unavailable")
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

```python
def _previous_dag(
    state: RunState,
    prev_op: dict,
    target_dir: str,
    bundle_dir: str,
) -> tuple[tuple[tuple[str, tuple[str, ...]], ...] | None, str]:
    """DAG предыдущей доставки + откуда он взят (§I8 спеки).

    Запись `dag` в ревизии фиксирует ВЫБОР, не доказательство; для
    легаси-v1 состав выводится из каталога бандла и обязан совпасть
    ТОЧНО с одним из `_dag_for(None|3|4|5)` — `--legacy-bundle=5` это
    отдельный вариант, а не префикс полного DAG.
    """
    recorded = prev_op.get("dag")
    if recorded:
        return tuple((f, tuple(u)) for f, u in recorded), "previous_delivery"
    present = {
        p.name for p in (Path(target_dir) / bundle_dir).iterdir()
        if p.is_file() and p.suffix == ".md"
    }
    matches = [
        _dag_for(v) for v in (None, 3, 4, 5)
        if {f for f, _ in _dag_for(v)} == present
    ]
    if len(matches) == 1:
        return matches[0], "derived_from_spec"
    return None, "unavailable"
```

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): вывод и сверка активного DAG предыдущей доставки"
```

---

### Task 6: Реконсиляция ревизии и восстановление коммита

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: Task 1 (`rev_parse`, `blob_in_commit`, `commit_parent`), Task 2.
- Produces:
  - `_reconcile_revision(state, ops, n, op, base_sha) -> str` — решение:
    `"return_pr"`, `"continue"`, `"complete"`, `"abandon_and_next"`,
    либо `RuntimeError` (fail-closed);
  - `_recover_commit(state, ops, op) -> str | None` — SHA принятого/
    созданного коммита по таблице §I3.1 (None — коммита ещё нет).

- [ ] **Step 1: Красные тесты** (таблица §I3 спеки — по строке на тест)

```python
def test_reconcile_completed_open_pr_returns_it(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "completed", "pr": 42, "base_sha": "s"}
    ops = _ReconOps(existing_pr=42)
    assert tb._reconcile_revision(state, ops, 2, op, "s") == "return_pr"


def test_reconcile_started_open_pr_same_base_continues(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "started", "base_sha": "s", "head_sha": "h",
          "tasks_blob": "b", "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def __init__(self):
            super().__init__(existing_pr=7)

        def pr_facts(self, repo_slug, pr):
            return {"state": "OPEN", "headRefOid": "h"}

    assert tb._reconcile_revision(state, _Ops(), 2, op, "s") == "continue"


def test_reconcile_started_open_pr_shifted_base_fails_closed(
    tmp_path, monkeypatch
):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "started", "base_sha": "old", "head_sha": "h",
          "branch": "b"}

    class _Ops(_ReconOps):
        def __init__(self):
            super().__init__(existing_pr=7)

        def pr_facts(self, repo_slug, pr):
            return {"state": "OPEN", "headRefOid": "h"}

    with pytest.raises(RuntimeError, match="--abandon-revision"):
        tb._reconcile_revision(state, _Ops(), 2, op, "new")


def test_reconcile_closed_unmerged_fails_closed(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "started", "base_sha": "s", "branch": "b"}

    class _Ops(_ReconOps):
        def __init__(self):
            super().__init__(existing_pr=7)

        def pr_facts(self, repo_slug, pr):
            return {"state": "CLOSED"}

    with pytest.raises(RuntimeError, match="отклонена"):
        tb._reconcile_revision(state, _Ops(), 2, op, "s")


def test_reconcile_identity_mismatch_fails_closed(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "started", "base_sha": "s", "head_sha": "mine",
          "branch": "b"}

    class _Ops(_ReconOps):
        def __init__(self):
            super().__init__(existing_pr=7)

        def pr_facts(self, repo_slug, pr):
            return {"state": "OPEN", "headRefOid": "someone-else"}

    with pytest.raises(RuntimeError, match="идентичность"):
        tb._reconcile_revision(state, _Ops(), 2, op, "s")


def test_reconcile_started_no_pr_shifted_base_abandons(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"status": "started", "base_sha": "old", "branch": "b"}
    ops = _ReconOps(existing_pr=None)
    assert tb._reconcile_revision(state, ops, 2, op, "new") == "abandon_and_next"


def test_recover_commit_accepts_matching_local_commit(tmp_path, monkeypatch):
    """head_sha: null, но подходящий коммит есть — принимается, не пересоздаётся."""
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "commit1" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "base1"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "blob1"

    assert tb._recover_commit(state, _Ops(), op) == "commit1"


def test_recover_commit_refuses_foreign_commit(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    op = {"head_sha": None, "base_sha": "base1", "tasks_blob": "blob1",
          "branch": "spec/WS-alpha-7-tasks-v2"}

    class _Ops(_ReconOps):
        def rev_parse(self, target_dir, ref):
            return "commitX" if "tasks-v2" in ref else None

        def commit_parent(self, target_dir, sha):
            return "OTHER-BASE"

        def blob_in_commit(self, target_dir, sha, rel_path):
            return "blob1"

    with pytest.raises(RuntimeError, match="чужой коммит"):
        tb._recover_commit(state, _Ops(), op)
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

```python
def _reconcile_revision(
    state: RunState, ops: Ops, n: int, op: dict, base_sha: str
) -> str:
    """Решение по таблице §I3 спеки. Возврат — имя перехода, не действие.

    Имя ветки НЕ доказывает принадлежность: под ним может лежать чужая
    работа, поэтому у каждого живого PR сверяется `headRefOid` с
    записанным `head_sha` намерения.
    """
    branch = op.get("branch")
    pr = ops.find_pr(state.repo_slug, branch, any_state=True) if branch else None
    pr_state = ops.pr_facts(state.repo_slug, pr).get("state") if pr else None
    if pr is not None and pr_state not in ("OPEN", "MERGED"):
        raise RuntimeError(
            f"PR #{pr} по ветке {branch} закрыт без мержа — ветка отклонена "
            "человеком; переиздание fail-closed"
        )
    if pr is not None and op.get("head_sha"):
        actual_head = ops.pr_facts(state.repo_slug, pr).get("headRefOid")
        if actual_head and actual_head != op["head_sha"]:
            raise RuntimeError(
                f"идентичность не сошлась: PR #{pr} стоит на "
                f"{actual_head[:7]}, намерение ревизии {n} — "
                f"{op['head_sha'][:7]}; под тем же именем ветки чужая работа"
            )
    if op.get("status") == "completed":
        return "return_pr"
    same_base = op.get("base_sha") == base_sha
    if pr is not None and pr_state == "MERGED":
        return "complete"
    if pr is not None and pr_state == "OPEN":
        if same_base:
            return "continue"
        raise RuntimeError(
            f"ревизия {n} начата с другого base, а её PR #{pr} открыт: "
            f"продолжать нельзя (PR выведен из {op.get('base_sha', '?')[:7]}), "
            "и второй открытый PR заводить нельзя. Закройте его явно: "
            f"--abandon-revision {n}"
        )
    return "continue" if same_base else "abandon_and_next"


def _recover_commit(state: RunState, ops: Ops, op: dict) -> str | None:
    """Таблица §I3.1: коммит и запись head_sha не атомарны.

    Возврат: SHA коммита, который надо пушить (уже созданный или
    принятый), либо None — коммита ещё нет, доставка создаёт его сама.
    """
    branch, head = op.get("branch"), op.get("head_sha")
    local = ops.rev_parse(state.target_dir, branch) if branch else None
    if head:
        if local and local != head:
            raise RuntimeError(
                f"remote/локальный head ветки {branch} = {local[:7]}, "
                f"намерение — {head[:7]}: ветку двигали снаружи"
            )
        return head
    if local is None:
        return None
    rel = f"spec/{state.ws_id}-tasks.md"
    parent = ops.commit_parent(state.target_dir, local)
    blob = ops.blob_in_commit(state.target_dir, local, rel)
    if parent == op.get("base_sha") and blob == op.get("tasks_blob"):
        return local
    raise RuntimeError(
        f"чужой коммит в ветке {branch}: родитель {parent!r} / блоб "
        f"{blob!r} не отвечают намерению ревизии"
    )
```

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): реконсиляция ревизии и восстановление коммита по таблицам спеки"
```

---

### Task 7: `deliver_superseded` — сборка переиздания

**Files:**
- Modify: `governance/task_bridge.py`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: Task 2–6.
- Produces: `deliver_superseded(state, ops, legacy_bundle=None,
  approval_pr=None) -> int | None` — номер PR, либо `None` при бесследном
  no-op (§I5).

- [ ] **Step 1: Красные тесты**

```python
def test_supersede_equal_anchor_is_traceless_noop(tmp_path, monkeypatch):
    """Равный anchor: RC-успех, run.json побайтово прежний."""
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    anchor = tb._prospective_anchor(
        state.target_dir, state.bundle_dir, "ai-prosto",
        "2026-09-09T05:00:00Z", None,
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": anchor}
    rs.save(state)
    before = (rs.run_dir("r-recon") / "run.json").read_bytes()
    result = tb.deliver_superseded(state, _ProvOps(prs=[_MERGED_PR]))
    after = (rs.run_dir("r-recon") / "run.json").read_bytes()
    assert result is None
    assert before == after


def test_supersede_changed_anchor_opens_new_branch_and_pr(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ-ДРУГОЙ"}
    rs.save(state)
    ops = _SupersedeOps(prs=[_MERGED_PR])
    pr = tb.deliver_superseded(state, ops)
    assert pr == 77
    assert ("ensure_branch", "spec/WS-alpha-7-tasks-v2") in ops.calls
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["status"] == "completed"
    assert saved["supersedes"] == 1
    assert saved["approval_pr"] == 403
    assert rs.load("r-recon").ops["tasks-deliver"]["pr"] == 5   # v1 цела


def test_supersede_version_is_monotonic(tmp_path, monkeypatch):
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    (Path(state.target_dir) / "spec").mkdir(exist_ok=True)
    (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").write_text(
        "---\nspec_stage: tasks\nversion: 4\n---\n", encoding="utf-8"
    )
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5,
                                  "anchor": "СТАРЫЙ"}
    ops = _SupersedeOps(prs=[_MERGED_PR])
    tb.deliver_superseded(state, ops)
    text = (Path(state.target_dir) / "spec/WS-alpha-7-tasks.md").read_text()
    assert "version: 5" in text


def test_supersede_dag_mismatch_fails_closed(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {
        "status": "completed", "pr": 5, "anchor": "СТАРЫЙ",
        "dag": [["00-charter.md", []], ["10-requirements.md", ["charter"]]],
    }
    rs.save(state)
    with pytest.raises(RuntimeError, match="другая доставка"):
        tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))


def test_supersede_legacy_without_anchor_records_unavailable(
    tmp_path, monkeypatch
):
    from governance import run_state as rs
    from governance import task_bridge as tb

    state = _recon_state(tmp_path, monkeypatch)
    state.ops["tasks-deliver"] = {"status": "completed", "pr": 5}  # без anchor
    rs.save(state)
    tb.deliver_superseded(state, _SupersedeOps(prs=[_MERGED_PR]))
    saved = rs.load("r-recon").ops["tasks-deliver-v2"]
    assert saved["comparison"] == "unavailable"
```

`_SupersedeOps` — стаб на базе `_ProvOps`, добавляющий `rev_parse`/
`commit_parent`/`blob_in_commit` (все `None`) и `find_pr` → `None`.

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

```python
def deliver_superseded(
    state: RunState,
    ops: Ops,
    legacy_bundle: int | None = None,
    approval_pr: int | None = None,
) -> int | None:
    """Санкционированное переиздание tasks-спеки (спека 2026-09-09).

    Порядок: синхронизация base → provenance → проспективный anchor →
    сверка (§I5, §I8) → намерение → доставка в НОВУЮ ветку. Возврат
    None — бесследный no-op: апстрим не менялся.
    """
    if state.status != "completed":
        raise RuntimeError(
            f"run {state.run_id!r} в статусе {state.status!r}, нужен "
            "'completed'"
        )
    if ops.is_dirty(state.target_dir):
        raise RuntimeError(
            f"target_dir {state.target_dir!r} грязный — переиздание не начато"
        )
    base_ref = state.base_ref or "master"
    ops.checkout_and_pull(state.target_dir, base_ref)
    base_sha = ops.rev_parse(state.target_dir, "HEAD")

    prev = _last_delivery(state)
    if prev is None:
        raise RuntimeError(
            "доставок ещё не было — переиздавать нечего; обычная доставка "
            "идёт без --supersede"
        )
    prev_n, prev_op = prev

    # Незавершённая ревизия реконсилируется ДО любых новых эффектов.
    for n, op in reversed(_revisions(state)):
        if op.get("status") == "started":
            decision = _reconcile_revision(state, ops, n, op, base_sha)
            if decision == "abandon_and_next":
                _abandon_revision(
                    state, n,
                    f"base сдвинулся ({op.get('base_sha', '?')[:7]} → "
                    f"{base_sha[:7]}), открытого PR нет",
                )
            break

    dag, dag_source = _previous_dag(
        state, prev_op, state.target_dir, state.bundle_dir
    )
    active = _dag_for(legacy_bundle)
    if dag is not None and dag != active:
        raise RuntimeError(
            "состав активного DAG отличается от предыдущей доставки — "
            "это не переиздание, а другая доставка"
        )
    anchor_rel = f"{state.bundle_dir}/{active[-1][0]}"
    approval, approved_by, approved_at = _resolve_correction_pr(
        state, ops, anchor_rel, base_ref, approval_pr
    )
    prospective = _prospective_anchor(
        state.target_dir, state.bundle_dir, approved_by, approved_at,
        legacy_bundle,
    )
    recorded_anchor = prev_op.get("anchor")
    if recorded_anchor is not None and recorded_anchor == prospective:
        print(
            "апстрим не менялся — переиздание не требуется "
            f"(anchor {prospective[:7]})"
        )
        return None

    n = _next_revision(state)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    version = _previous_tasks_version(state) + 1
    branch = f"spec/{state.ws_id}-tasks-v{n}"
    intent = {
        "branch": branch,
        "base_sha": base_sha,
        "prospective_anchor": prospective,
        "approval_pr": approval,
        "tasks_version": version,
        "dag": [[f, list(u)] for f, u in active],
        "dag_source": dag_source,
        "supersedes": prev_n,
        "expected_generated_at": generated_at,
        "tasks_blob": None,      # заполняется после рендера, до коммита
    }
    if recorded_anchor is None:
        # §6 спеки: сверка была невозможна — фиксируем это В ЖУРНАЛЕ.
        intent["comparison"] = "unavailable"
        print(
            "предыдущая доставка не записала anchor — сверка не "
            "производилась (comparison: unavailable)"
        )
    _start_revision(state, n, intent)
    pr = deliver(
        target_dir=state.target_dir,
        repo_slug=state.repo_slug,
        ws_id=state.ws_id,
        subject=state.subject,
        bundle_dir=state.bundle_dir,
        base_ref=base_ref,
        ops=ops,
        approved_by=approved_by,
        approved_at=approved_at,
        generated_at=generated_at,
        legacy_bundle=legacy_bundle,
        profile=state.profile,
        branch=branch,
        version=version,
    )
    _complete_revision(
        state, n, pr=pr, anchor=prospective,
        head_sha=ops.rev_parse(state.target_dir, branch),
    )
    return pr
```

`deliver()` получает два новых необязательных параметра — `branch` (по
умолчанию сегодняшний `f"spec/{ws_id}-tasks"`) и `version` (по умолчанию `1`).
Их проброс в рендер — часть этого шага; сигнатура рендера
`render_tasks_dt`/`render_tasks` не меняется, версия подставляется там, где
сегодня захардкожена строка `"version: 1"`.

`_previous_tasks_version(state)` — чтение `version:` из
`spec/<ws-id>-tasks.md` в свежем base; файла нет либо frontmatter не
разбирается — `RuntimeError` (fail-closed, §I6 + минор ревью #161).

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py
git commit -m "feat(bridge): deliver_superseded — переиздание в новую ветку с ревизией в леджере"
```

---

### Task 8: CLI, README и сквозной сценарий

**Files:**
- Modify: `governance/task_bridge.py` (main), `README.md`
- Test: `tests/test_governance_task_bridge.py`

**Interfaces:**
- Consumes: Task 7.
- Produces: флаги `--supersede`, `--approval-pr <n>`, `--abandon-revision <n>`.

- [ ] **Step 1: Красные тесты**

```python
def test_cli_supersede_calls_deliver_superseded(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    called = {}
    monkeypatch.setattr(
        tb, "deliver_superseded",
        lambda s, o, legacy_bundle=None, approval_pr=None: called.setdefault(
            "args", (s.run_id, approval_pr)
        ) or 77,
    )
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0
    assert called["args"] == ("r-recon", None)


def test_cli_supersede_noop_is_success(tmp_path, monkeypatch, capsys):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    _recon_state(tmp_path, monkeypatch)
    monkeypatch.setattr(
        tb, "deliver_superseded",
        lambda s, o, legacy_bundle=None, approval_pr=None: None,
    )
    monkeypatch.setattr(tb, "RealOps", lambda: object())
    assert tb.main(["--run-id", "r-recon", "--supersede"]) == 0


def test_cli_abandon_revision_marks_and_returns_zero(tmp_path, monkeypatch):
    from governance import run_state as rs
    from governance import task_bridge as tb

    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runs")
    state = _recon_state(tmp_path, monkeypatch)
    tb._start_revision(state, 2, {"branch": "b", "base_sha": "s"})
    assert tb.main([
        "--run-id", "r-recon", "--abandon-revision", "2",
        "--reason", "PR закрыт вручную",
    ]) == 0
    assert rs.load("r-recon").ops["tasks-deliver-v2"]["status"] == "abandoned"


def test_cli_abandon_revision_requires_reason(tmp_path, monkeypatch, capsys):
    from governance import task_bridge as tb

    with pytest.raises(SystemExit):
        tb.main(["--run-id", "r", "--abandon-revision", "2"])
```

- [ ] **Step 2: Прогнать — FAIL.**

- [ ] **Step 3: Реализация**

В `main`: три новых аргумента и ветвления ДО существующих путей.

```python
    parser.add_argument(
        "--supersede", action="store_true",
        help="переиздать tasks-спеку после correction'а апстрима: новая "
             "ветка spec/<ws-id>-tasks-v<N>, новый PR, отдельная ревизия в "
             "леджере; равный anchor — успешный no-op без изменений",
    )
    parser.add_argument(
        "--approval-pr", type=int, default=None,
        help="явный correction-PR для подписи штампа, когда автоматика даёт "
             "ноль или несколько кандидатов; проверяется так же",
    )
    parser.add_argument(
        "--abandon-revision", type=int, default=None,
        help="перевести незавершённую ревизию переиздания в терминальный "
             "abandoned (требует --reason)",
    )
    parser.add_argument("--reason", default=None, help="причина для --abandon-revision")
```

```python
    if args.abandon_revision is not None:
        if not args.reason:
            parser.error("--abandon-revision требует --reason")
        _abandon_revision(state, args.abandon_revision, args.reason)
        print(f"ревизия {args.abandon_revision} помечена abandoned")
        return 0
    if args.supersede:
        try:
            pr = deliver_superseded(
                state, ops, legacy_bundle=args.legacy_bundle,
                approval_pr=args.approval_pr,
            )
        except RuntimeError as exc:
            print(f"task_bridge: {exc}")
            return 1
        if pr is None:
            return 0
        print(f"переизданная tasks-спека доставлена: PR #{pr}")
        return 0
```

README: строка в таблице инструментов о трёх флагах, одним предложением
каждый.

- [ ] **Step 4: Прогнать — PASS; полный набор.**

- [ ] **Step 5: Commit**

```bash
git add governance/task_bridge.py tests/test_governance_task_bridge.py README.md
git commit -m "feat(bridge): CLI переиздания — --supersede, --approval-pr, --abandon-revision"
```

---

## Self-Review (выполнен при написании)

1. **Spec coverage:** I1 — Task 7 (имя ветки `-v<N>`) + Task 2 (нумерация из
   леджера); I2 — Task 4; I3 — Task 6 (обе таблицы); I4 — Task 2 (намерение,
   неприкосновенность v1, `supersedes`); I5 — Task 7 (бесследный no-op с
   побайтовой проверкой); I6 — Task 7 (`_previous_tasks_version` + проброс
   `version`); I7 — Task 3; I8 — Task 5; I9 — Task 7/8 (мержей нет нигде);
   §6 compatibility — Task 7 (`comparison: "unavailable"`); §4 таблица
   поведения — тесты Task 6–8.
2. **Placeholders:** формул и текстов отказов нет в виде «TBD»; каждый шаг
   несёт код либо точную команду.
3. **Type consistency:** `_last_delivery → tuple[int, dict] | None`
   (Task 2 → 7), `_previous_dag → (dag|None, str)` (Task 5 → 7),
   `_reconcile_revision → str` (Task 6 → 7), `deliver_superseded →
   int | None` (Task 7 → 8), `_prospective_anchor → str` (Task 4 → 7),
   `_resolve_correction_pr → (int, str, str)` (Task 3 → 7).
