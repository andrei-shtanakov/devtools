# Последовательное одобрение узлов бандла волнами — план имплементации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Прогон `spec-loop` авторит бандл по уровням DAG; после каждого уровня — механический гейт steward по проекции профиля, обязательный edge-check, заявка §I12, чей candidate-PR несёт авторские байты; шесть человеческих актов на бандл; переоткрытие верхнего узла даёт `stale` нижним и блокирует продвижение.

**Architecture:** Раннер получает цикл по уровням (`state.wave`) с per-wave op'ами и веткой на волну от base; `approve_node` получает режим `source_sha` (байты узла из коммита ветки волны) и правило состава base «префикс уровней» при `state.authoring == "waves"`; edge-check дорастает до координатора и адаптера публикации, который и даёт candidate-PR одобряющее ревью; finalize-PR аттестуется раннером на resume; `--reopen` — явная команда.

**Tech Stack:** Python 3.12, `uv run --frozen pytest`; стенды `tests/test_governance_runner.py` (FakeOps) и `tests/test_governance_approve_node.py` (настоящий git + фейк-форджа); steward `gate-check --candidate` (соседний репо, только чтение); edge-check `governance/edge_check/`.

**Spec:** `docs/superpowers/specs/2026-09-22-sequential-node-approval-design.md` (accepted; D1 подписано 2026-09-22 — шесть актов, таблица соответствия трём гейтам).

## Global Constraints

- Волны = уровни DAG: W1 charter, W2 requirements, W3 behaviour-spec, W4 design + acceptance, W5 decomposition (S1); `wave_of(node) = level(node)`.
- Контракт пина §I12 (пин = blob всего файла upstream с конвертом) и гейт steward **не меняются** (D1).
- Ледгер §I12 не меняется: `wave`/`step`/`attempt` — проход/уровень/попытка; intent по **полному** составу; `approve_node` получает **полный** DAG, усечение — только в проверке состава base (S3, S4а).
- Два переключателя: `op["source_sha"]` — источник байтов узла; `state.authoring == "waves"` — правило состава (S4).
- Candidate волны мержит только человек; finalize — агентом после аттестации раннером (S6, S8).
- Edge-check обязателен: `FAIL`/`PENDING` (код 1) и `ERROR`/невозможность (код 3) — `stopped_review`, обхода нет (S6).
- Evidence edge-check пишется в каталог прогона и леджер; в ветку заявки — только адаптер публикации (S6/S7).
- Ветка волны `spec/<ws>-behaviour-w<k>` от base через `ops.switch_to`; `materialize-brief` per-wave (S9/S10).
- Ничего не удаляется и не переписывается автоматически; `--reopen` — явная команда (D2, S11).
- Коммиты по-русски, трейлеры `Epic: eco.dark-factory` и `Co-Authored-By` в финальном блоке; PR кода — обычный агентский мерж (харнесс-путей и authority-root в дифе нет), кроме задач, трогающих `profiles/` (authority-root) — их нет в плане: проекция пишется в каталог прогона.
- Прежний путь «один бандл-PR» остаётся рабочим на каждом шаге плана (S13: run.json без `authoring` = legacy).
- **Порядок с планом approval-policy:** этот план исполняется ПОСЛЕ мержа кода `2026-09-22-approver-policy-trusted-source.md` (параметр `start_request(..., policy=)`, `af.policy_snapshot`, `_join_target` приходят оттуда); пункт `@id:sequential-node-approval` получает `@blocked_by:todo://devtools/approver-policy-trusted-source` тем же PR, что и этот план (ветка спеки), и снимает его при закрытии продюсера.
- **Нумерация волн — 1-based, как в спеке** (W1..W5): `state.wave ∈ 1..5` в волновом режиме (`0` — не волновой режим), `level(node) = wave − 1`, `wave_of(node) = level(node) + 1`; ключи op'ов, каталоги проекций и ветки — `…-w1`…`…-w5`; `waiting_human_merge(wave=1)`. Во всех формулах плана «уровень» — 0-based индекс DAG, «волна» — 1-based номер.

---

### Task 1: Уровни DAG и префикс состава — `bundle_dag`

**Files:**
- Modify: `governance/bundle_dag.py` (после `node_filenames`; `check_bundle_composition` 109–158)
- Modify: `governance/approve_node.py:133-146` (`_levels` → делегирует в `bundle_dag.levels`)
- Test: `tests/test_governance_bundle_dag.py` (новый) + существующие тесты `_levels` в `tests/test_governance_approve_node.py` остаются зелёными

**Interfaces:**
- Produces: `bundle_dag.levels(dag) -> dict[str, int]`; `bundle_dag.dag_upto(dag, level: int) -> tuple[...]` (узлы уровней ≤ level, тот же порядок); `bundle_dag.wave_count(dag) -> int`; `check_bundle_composition(target_dir, bundle_dir, dag, *, mode: str = "full")` — `mode="waves"`: каталога нет ⇒ пустой состав; фактический состав обязан быть **префиксом по уровням** (`dag_upto(dag, m)` для некоторого m ≥ −1), иначе `RuntimeError` с названной «дырой»; возвращает `m` (в режиме `full` — прежнее поведение, возвращает максимальный уровень).

- [ ] **Step 1: Failing tests**

```python
# tests/test_governance_bundle_dag.py
from pathlib import Path
import pytest
from governance import bundle_dag

DAG = bundle_dag.BUNDLE_DAG


def test_levels_match_dag_topology() -> None:
    assert bundle_dag.levels(DAG) == {
        "charter": 0, "requirements": 1, "behaviour-spec": 2,
        "design": 3, "acceptance": 3, "decomposition": 4,
    }
    assert bundle_dag.wave_count(DAG) == 5


def test_dag_upto_is_a_level_prefix() -> None:
    assert [f for f, _ in bundle_dag.dag_upto(DAG, 1)] == ["00-charter.md", "10-requirements.md"]
    assert bundle_dag.dag_upto(DAG, -1) == ()
    assert bundle_dag.dag_upto(DAG, 4) == DAG


def _bundle(tmp_path: Path, names: list[str]) -> Path:
    b = tmp_path / "spec"; b.mkdir()
    for n in names:
        (b / n).write_text("---\nnode: x\n---\n", encoding="utf-8")
    return b


def test_waves_mode_accepts_level_prefix_and_missing_dir(tmp_path: Path) -> None:
    assert bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG, mode="waves") == -1
    _bundle(tmp_path, ["00-charter.md", "10-requirements.md"])
    assert bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG, mode="waves") == 1


def test_waves_mode_refuses_a_hole_in_levels(tmp_path: Path) -> None:
    _bundle(tmp_path, ["00-charter.md", "15-behaviour-spec.md"])
    with pytest.raises(RuntimeError, match="префикс"):
        bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG, mode="waves")


def test_full_mode_is_unchanged(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="каталога бандла"):
        bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG)
    _bundle(tmp_path, [f for f, _ in DAG])
    assert bundle_dag.check_bundle_composition(str(tmp_path), "spec", DAG) == 4
```

- [ ] **Step 2: Run** `uv run --frozen pytest tests/test_governance_bundle_dag.py -q` — FAIL.

- [ ] **Step 3: Реализация**

```python
def levels(dag) -> dict[str, int]:
    """Уровень узла: 0 у корня, иначе 1 + max по прямым upstream (§I12 `K`)."""
    out: dict[str, int] = {}
    for fname, ups in dag:
        out[node_id(fname)] = 1 + max((out[u] for u in ups), default=-1)
    return out


def wave_count(dag) -> int:
    return max(levels(dag).values(), default=-1) + 1


def dag_upto(dag, level: int):
    """Узлы уровней ≤ level — префикс DAG по уровням, порядок сохранён."""
    lv = levels(dag)
    return tuple((fname, ups) for fname, ups in dag if lv[node_id(fname)] <= level)


def check_bundle_composition(target_dir, bundle_dir, dag, *, mode: str = "full") -> int:
    bundle = Path(target_dir) / bundle_dir
    declared = {fname for fname, _ in dag}
    known = {fname for fname, _ in BUNDLE_DAG}
    if not bundle.is_dir():
        if mode == "waves":
            return -1        # волновой режим: каталога ещё нет — пустой состав
        raise RuntimeError(...)   # прежний текст без изменений
    actual = {p.name for p in bundle.glob("*.md") if p.name in known}
    if mode == "waves":
        lv = levels(dag)
        top = max((lv[node_id(f)] for f in actual), default=-1)
        prefix = {fname for fname, _ in dag_upto(dag, top)}
        if actual != prefix:
            raise RuntimeError(
                f"состав бандла {sorted(actual)} не является префиксом DAG по "
                f"уровням (ожидалось {sorted(prefix)} для уровней ≤ {top}): "
                "дыра в уровнях — волновой прогон не продолжается"
            )
        return top
    if actual != declared:
        raise RuntimeError(...)   # прежний текст
    return max(levels(dag).values())
```
В `approve_node.py` тело `_levels` заменить на `return bundle_dag.levels(dag)` (имя и вызовы остаются). `check_bundle_composition` зовут ещё семь мест `task_bridge` (через алиас `_check_bundle_composition`) с игнорируемым возвратом — совместимость держится на дефолте `mode="full"`; тест `test_full_mode_is_unchanged` это и фиксирует, плюс в `tests/test_governance_task_bridge.py` один тест-страж: вызов без `mode` на полном бандле проходит, на неполном — прежний `RuntimeError`.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_bundle_dag.py tests/test_governance_approve_node.py -q -k "levels or composition"` — PASS.

- [ ] **Step 5: Commit** `git add governance/bundle_dag.py governance/approve_node.py tests/test_governance_bundle_dag.py && git commit -m "feat(bundle_dag): уровни DAG, префикс состава и волновой режим проверки состава"`

---

### Task 2: Состояние прогона — `authoring` и `wave`

**Files:**
- Modify: `governance/run_state.py` (`RunState` после `allow_legacy_dt`; `new_run`; `validate_authoring`)
- Modify: `governance/runner.py:267-348` (`start(..., authoring: str = "legacy")`)
- Test: `tests/test_governance_run_state.py` (существующий файл; добавить тесты)

**Interfaces:**
- Produces: `RunState.authoring: str = "legacy"` (`"legacy"` | `"waves"`), `RunState.wave: int = 0` (номер текущей волны 1..5 в волновом режиме; `0` — не волновой режим), `run_state.validate_authoring(value)`, `new_run(..., authoring="legacy")`, `runner.start(..., authoring="legacy")`; старые `run.json` без полей читаются через дефолты.

- [ ] **Step 1: Failing tests**

```python
def test_run_state_defaults_to_legacy_authoring(tmp_path, runs_root) -> None:
    state = rs.new_run(subject="s", repo="r", repo_slug="o/r", ws_id="WS", target_dir=str(tmp_path),
                       bundle_dir="spec", profile="profiles/team-exp.yaml", run_id="r-1")
    assert state.authoring == "legacy" and state.wave == 0   # 0 — не волновой режим
    rs.save(state)
    assert rs.load("r-1").authoring == "legacy"


def test_waves_authoring_is_persisted(tmp_path, runs_root) -> None:
    state = rs.new_run(subject="s", repo="r", repo_slug="o/r", ws_id="WS", target_dir=str(tmp_path),
                       bundle_dir="spec", profile="profiles/team-exp.yaml", run_id="r-2", authoring="waves")
    rs.save(state)
    assert rs.load("r-2").authoring == "waves"


def test_unknown_authoring_is_refused() -> None:
    with pytest.raises(ValueError, match="authoring"):
        rs.validate_authoring("chunks")
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Реализация** — в `RunState`: `authoring: str = "legacy"` и `wave: int = 0` с комментарием (S13: без поля — прежний путь; в waves `runner.start` ставит `wave = 1`); `_ALLOWED_AUTHORING = ("legacy", "waves")`, `validate_authoring`; `new_run(..., authoring="legacy")` вызывает валидацию и передаёт поле; `runner.start(..., authoring="legacy")` валидирует ДО `_reserve_run_id` (как `merge_authority`) и передаёт в `new_run`.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_run_state.py tests/test_governance_runner.py -q` — PASS.

- [ ] **Step 5: Commit** `git add governance/run_state.py governance/runner.py tests/test_governance_run_state.py && git commit -m "feat(run_state): режим авторинга waves и номер волны в состоянии прогона"`

---

### Task 3: Проекция профиля на волну

**Files:**
- Modify: `governance/policy_sources.py` (после `target_profile_declares`)
- Test: `tests/test_governance_policy_sources.py` (существующий; добавить)

**Interfaces:**
- Produces: `wave_profile_dir(target_dir: str, profile: str, wave: int, run_dir: Path) -> Path` — копирует `<target_dir>/<dirname(profile)>/` целиком в `<run_dir>/profile-w<wave>/`, усекает копию профиля до узлов уровней ≤ `wave − 1` (по `upstream` профиля — уровни считаются по нему, не по `BUNDLE_DAG`), пишет `<run_dir>/profile-w<wave>/PROJECTION.sha256` (sha256 каждого скопированного файла target'а) и возвращает путь усечённого профиля; `verify_wave_profile_dir(target_dir, profile, projected: Path) -> list[str]` — расхождения sha256 копий с target (пусто = совпало).

- [ ] **Step 1: Failing tests**

```python
def test_wave_profile_is_a_level_prefix_of_the_target_profile(tmp_path) -> None:
    target = tmp_path / "t"; (target / "profiles").mkdir(parents=True)
    src = Path("profiles/team-exp.yaml").read_text(encoding="utf-8")   # devtools' own
    (target / "profiles/team-exp.yaml").write_text(src, encoding="utf-8")
    for sib in ("roles.yaml", "gate-catalog.yaml", "approval-policy.yaml", "arch-policy.yaml"):
        (target / "profiles" / sib).write_text(f"# {sib}\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    projected = policy_sources.wave_profile_dir(str(target), "profiles/team-exp.yaml", 2, run_dir)   # W2 → уровни ≤ 1
    data = yaml.safe_load(projected.read_text(encoding="utf-8"))
    assert [a["id"] for a in data["artifacts"]] == ["charter", "requirements"]
    assert projected.parent.name == "profile-w2"
    assert (projected.parent / "roles.yaml").read_text() == "# roles.yaml\n"
    assert policy_sources.verify_wave_profile_dir(str(target), "profiles/team-exp.yaml", projected) == []
    (target / "profiles/roles.yaml").write_text("# changed\n", encoding="utf-8")
    assert policy_sources.verify_wave_profile_dir(str(target), "profiles/team-exp.yaml", projected) == ["roles.yaml"]


def test_wave_profile_last_wave_equals_source_artifacts(tmp_path) -> None:
    ... # wave 5 (уровни ≤ 4) → все шесть узлов бандла; delegate `tasks` (уровень 5) отсекается — upstream замкнут
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Реализация**

```python
def _profile_levels(artifacts: list[dict]) -> dict[str, int]:
    """Уровни по `upstream` профиля; порядок `artifacts` не предполагается
    топологическим (steward сортирует сам) — считаем до неподвижной точки."""
    by_id = {a["id"]: a.get("upstream", []) for a in artifacts}
    lv: dict[str, int] = {}
    pending = set(by_id)
    while pending:
        ready = [i for i in pending if all(u in lv for u in by_id[i])]
        if not ready:
            raise RuntimeError(f"профиль: цикл или неизвестный upstream среди {sorted(pending)}")
        for i in ready:
            lv[i] = 1 + max((lv[u] for u in by_id[i]), default=-1)
            pending.discard(i)
    return lv


def wave_profile_dir(target_dir: str, profile: str, wave: int, run_dir: Path) -> Path:
    level = wave - 1                       # волны 1-based, уровни DAG 0-based
    src_dir = Path(target_dir) / Path(profile).parent
    dst_dir = run_dir / f"profile-w{wave}"
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src_dir.iterdir() if p.is_file()}
    (dst_dir / "PROJECTION.sha256").write_text(
        "".join(f"{d}  {n}\n" for n, d in sorted(digests.items())), encoding="utf-8")
    projected = dst_dir / Path(profile).name
    data = yaml.safe_load(projected.read_text(encoding="utf-8"))
    lv = _profile_levels(data["artifacts"])
    data["artifacts"] = [a for a in data["artifacts"] if lv[a["id"]] <= level]
    projected.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return projected


def verify_wave_profile_dir(target_dir: str, profile: str, projected: Path) -> list[str]:
    src_dir = Path(target_dir) / Path(profile).parent
    recorded = dict(line.split("  ", 1)[::-1] for line in
                    (projected.parent / "PROJECTION.sha256").read_text(encoding="utf-8").splitlines())
    return sorted(n for n, d in recorded.items()
                  if hashlib.sha256((src_dir / n).read_bytes()).hexdigest() != d)
```
Компромисс S5 — в докстроке: гейт судит по копии, которую пишет раннер; sha256 удерживает копию равной target; целевое состояние — `--upto` у steward (заявка соседу — Task 12).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `git add governance/policy_sources.py tests/test_governance_policy_sources.py && git commit -m "feat(policy_sources): проекция профиля на волну с sha256-сверкой siblings"`

---

### Task 4: Ледгер — `source_sha` в заявке

**Files:**
- Modify: `governance/approval_ledger.py:359-421`
- Test: `tests/test_governance_approval_ledger.py`

**Interfaces:**
- Produces: `start_request(..., policy=None, source_sha: str | None = None)`; запись несёт `"source_sha": source_sha` (None — байты из base, как сегодня).

- [ ] **Step 1: Failing test** — `test_request_records_source_sha`: `start_request(..., source_sha="c"*40)` → `ops[key]["source_sha"] == "c"*40`; без аргумента — `None`.
- [ ] **Step 2: Run** — FAIL. **Step 3:** параметр + поле с комментарием (спека S2/S4: источник байтов узла для заявки волны — коммит ветки волны). **Step 4:** PASS. **Step 5: Commit** `git commit -m "feat(ledger): source_sha — источник байтов узла заявки волны"`

---

### Task 5: `approve_node` — режим `source_sha` и правило состава волн

**Files:**
- Modify: `governance/approve_node.py` (`approve_node` 234–290; `_propose` 290–372; `_base_text` 183–190; `_carried_text` 895–930; `_sync_branch_to_snapshot` 940–1000; `_cascade_stale` 1062–1100; `_candidate_body` 1152–1170; публичная функция `propose_from_source(...)`)
- Test: `tests/test_governance_approve_node.py` (стенд получает вспомогательный `authored_branch(world, nodes)`: коммит узлов на ветку `spec/WS-T1-behaviour-w<k>` от base, возвращает SHA)

**Interfaces:**
- Consumes: Task 1 (`check_bundle_composition(mode=)`), Task 4 (`source_sha`).
- Produces: `approve_node.propose_from_source(state, ops, node_ids: list[str], source_sha: str, *, legacy_bundle=None) -> ApprovalOutcome` — заявка над узлами одного уровня, байты из `source_sha`; `approve_node(...)` (по узлу) — как сегодня, но в `state.authoring == "waves"` состав base проверяется `mode="waves"`; `_node_text(ops, state, op, fname)` — байты из `op["source_sha"]`, если задан, иначе из base.

- [ ] **Step 1: Failing tests**

```python
def authored_branch(world: World, wave: int, files: dict[str, str]) -> str:
    """Ветка волны (1-based) от base с авторскими байтами узлов; → SHA коммита."""
    branch = f"spec/{WS_ID}-behaviour-w{wave}"
    _git(world.target, "switch", "-q", "-C", branch, "master")
    for fname, text in files.items():
        (world.target / BUNDLE / fname).write_text(text, encoding="utf-8")
    _git(world.target, "add", "-A")
    _git(world.target, "commit", "-qm", f"author wave {wave}")
    _git(world.target, "push", "-q", "-u", "origin", branch)
    sha = _git(world.target, "rev-parse", "HEAD")
    _git(world.target, "switch", "-q", "master")
    return sha


@pytest.fixture()
def waves_world(tmp_path, monkeypatch) -> World:
    """Как `world`, но base ПУСТОЙ (нет каталога бандла) и authoring=waves."""
    ...  # копия фикстуры world без записи узлов в seed; state.authoring = "waves"


def test_w1_candidate_carries_authored_bytes_from_source_sha(waves_world: World) -> None:
    w = waves_world
    sha = authored_branch(w, 1, {"00-charter.md": _node_text("charter", body="Хартия v1.")})
    outcome = an.propose_from_source(w.state, w.ops, ["charter"], sha)
    key, op = only_request(w)
    assert op["source_sha"] == sha and op["nodes"] == ["charter"]
    branch = w.forge.prs[op["candidate_pr"]]["branch"]
    text = _show(w.target, f"origin/{branch}:{BUNDLE}/00-charter.md")
    assert "Хартия v1." in text and "status: approval_pending" in text
    merge_pr(w, op["candidate_pr"])
    an.approve_node(w.state, w.ops, "charter")           # finalize
    w.sync()
    assert w.base_meta("00-charter.md")["status"] == na.STATUS_APPROVED
    assert w.state.ops[key]["status"] == al.STATUS_COMPLETED


def test_w2_pins_the_approved_charter_from_base(waves_world: World) -> None:
    ...  # W1 как выше; затем requirements на ветке w1 с upstream_hashes на charter из base;
         # propose_from_source(["requirements"], sha) → мерж → finalize → approved; пин == blob charter в base


def test_composition_prefix_rule_accepts_reopen_and_stale_reapproval(waves_world: World) -> None:
    ...  # W1..W3 approved; propose_from_source(["requirements"], sha2) над новым текстом →
         # candidate несёт stale для behaviour-spec (каскад по существующему файлу),
         # design/acceptance отсутствуют — _cascade_stale их пропускает; после мержа/finalize
         # approve_node(state, ops, "behaviour-spec") без source_sha — байты из base, состав base
         # (уровни ≤ 2) принят правилом префикса, заявка публикуется


def test_missing_lower_nodes_keep_read_dag_state_unresolved(waves_world: World) -> None:
    ...  # W1 approved; an.read_dag_state(...) → unresolved (гейт доставки не проходим)
```

- [ ] **Step 2: Run** — FAIL (`propose_from_source` не существует; `check_bundle_composition` отказывает на пустом base).

- [ ] **Step 3: Реализация**

1. `approve_node()`: `mode = "waves" if state.authoring == "waves" else "full"`; `bundle_dag.check_bundle_composition(state.target_dir, state.bundle_dir, dag, mode=mode)`.
2. `_node_text(ops, state, op, fname) -> str`: если `op.get("source_sha")` — `ops.show_file(state.target_dir, op["source_sha"], _rel(state, fname))` (None → `_unresolved`), иначе `_base_text`. `_carried_text` и `_snapshot_is_published` (в части исходного текста) читают через него; `_carried_text` при `source_sha`: `version = max(version в base если файл в base есть, version в source) + 1` (S4е).
3. `_cascade_stale`: `if not path.exists(): continue` с комментарием (S4б — узлов нижних уровней в base ещё нет).
4. `_sync_branch_to_snapshot` при `source_sha`: source-слой (`state.brief["source_paths"]`, если `state.brief`) копируется из `source_sha` (`ops.show_file_bytes`) и добавляется в `changed` с `force_paths` (S4д); файлы узлов — как сегодня из `_carried_text`.
5. `propose_from_source(state, ops, node_ids, source_sha, *, legacy_bundle=None)`: `dag`, `is_dirty`, `checkout_and_pull(base)`, `check_bundle_composition(mode="waves")`, `_settle_requests_outside_dag`; гвард «все `node_ids` одного уровня» (иначе `RuntimeError` «зависимые уровни в одном candidate запрещены»); для каждого узла — **те же гварды, что у `_propose`**: `_require_upstream_ready(state, ops, dag, node)` (upstream `approved` в base) и `_require_no_reopened_pr(state, ops, node)`; `self_hash` по тексту из `source_sha`; `upstream_blobs` — `bundle_inputs.direct_blobs(state, ops, dag, node, ref)`, где для узлов с DAG-upstream `ref = base_ref` (upstream в base), а для charter (DAG-upstream нет, только source-слой) `ref = source_sha` — source-слой по ref хешируется БАЙТАМИ и сверяется с descriptor, `known_texts` не используется (тот хеширует текст и дал бы ложный FORBIDDEN); политика подписи — `_join_target` + `af.policy_snapshot` как в `_propose`; `_close_obsolete_wave`/`_wave_for`; `start_request(..., policy=..., source_sha=source_sha)` с первым узлом, `extend_request` для остальных; `_publish_candidate(push=False)` — см. Task 8; возвращает `ApprovalOutcome(request=key)`. Тесты: «W2 при charter не `approved` в base → `RuntimeError` от `_require_upstream_ready`, заявка не создана»; «узел с открытым PR терминальной заявки → отказ `_require_no_reopened_pr`».
6. `_candidate_body`: строка `run-id: {state.run_id}` (S2, m3) и, при `policy`, `policy: …` (план approval-policy).
7. `_publish_candidate(..., push: bool = True)` — при `push=False` возвращает после `_sync_branch_to_snapshot` (коммит и `record_head_sha` сделаны, PR не создан); адаптер (Task 8) докладывает evidence-коммит, перезаписывает `head_sha`, пушит и зовёт `_publish_candidate(push=True)` для создания PR (тот усыновит/создаст PR по записанному `head_sha`).

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_approve_node.py -q` — PASS (старые тесты — legacy-режим — без изменений).

- [ ] **Step 5: Commit** `git add governance/approve_node.py tests/test_governance_approve_node.py && git commit -m "feat(approve_node): заявка над байтами ветки волны (source_sha), правило префикса уровней, каскад по отсутствующим"`

---

### Task 6: Edge-check срез 2 — координатор волны

**Files:**
- Create: `governance/edge_check/coordinator.py`
- Modify: `contracts/edge-check/v1/rules/` — шесть новых наборов **той же YAML-формы, что `behaviour-vs-requirements.yaml`** и того же загрузчика (`governance/edge_check/rules.py`): ключи `edge`, `subject_role`, `basis_roles`, `items[id,text]`, `severity: {blocking: [blocker, major], advisory: [minor]}` (политика по КЛАССАМ находок, как в срезе 1 — per-rule severity схема не знает и не вводится), `applicability`. Ограничение загрузчика «несколько оснований + applicability не поддерживаются» (`multi_basis_applicability_unsupported`) обходится разбиением ребра charter на два, а не правкой загрузчика. Содержание нормативно для D4 (без набора проверка = `ERROR`):
  - `requirements-vs-charter.yaml` — `subject_role: requirements`, `basis_roles: [charter]`, `applicability: []`; items: R1 «каждая цель, ограничение и метрика charter (`G-*`, `CON-*`, `M-*`) имеет хотя бы одно требование, которое её реализует или явно отклоняет с названной причиной»; R2 «ни одно Must-требование не противоречит ограничениям charter»; R3 «требование не вводит цель, которой нет в charter, без ссылки на него»; R4 «формулировка каждого Must/Should проверяема снаружи: наблюдаемый результат, не внутреннее устройство».
  - `design-vs-requirements-behaviour.yaml` — `subject_role: design`, `basis_roles: [requirements, behaviour-spec]`, `applicability: []`; items: R1 «каждое Must-требование и каждый сценарий BEH получают в дизайне названный механизм (компонент, поток, решение) или явный отказ с причиной»; R2 «дизайн не противоречит требованиям и сценариям и не сужает их без объявления»; R3 «каждое решение дизайна (`D-*`) прослеживается к требованию или сценарию»; R4 «состояния ошибок и границы authority из требований отражены в дизайне».
  - `acceptance-vs-requirements-behaviour.yaml` — `subject_role: acceptance`, `basis_roles: [requirements, behaviour-spec]`, `applicability: []`; items: R1 «каждый сценарий BEH покрыт хотя бы одним критерием приёмки (`AC-*`) с наблюдаемым исходом»; R2 «каждый Must-FR имеет критерий, различающий выполнено и не выполнено»; R3 «критерий не утверждает больше, чем даёт сценарий: нет обязательств вне требований»; R4 «критерий выполним без доступа к внутреннему состоянию».
  - `decomposition-vs-design-acceptance.yaml` — `subject_role: decomposition`, `basis_roles: [design, acceptance]`, `applicability: []`; items: R1 «каждый DT ссылается на решение дизайна или критерий приёмки, который он реализует»; R2 «каждый критерий приёмки закрыт хотя бы одним DT (сценарии или `delivers`)»; R3 «DT не вводит обязательств, отсутствующих в дизайне и приёмке»; R4 «зависимости DT совместимы с порядком, который задаёт дизайн».
  - `charter-vs-customer-brief.yaml` — `subject_role: charter`, `basis_roles: [customer-brief]`, `applicability: []` (бриф заказчика обязателен: у W1 нет других оснований); items: R1 «каждая цель и ограничение брифа заказчика отражены в charter или явно отклонены с причиной»; R2 «charter не вводит целей, отсутствующих в брифе»; R3 «метрики charter выводимы из брифа».
  - `charter-vs-engineer-brief.yaml` — `subject_role: charter`, `basis_roles: [engineer-brief]`, `applicability: [{id: R0-no-engineer-brief, role: engineer-brief}]` (customer-маршрут E2 без инженерного брифа — `N/A`, не `ERROR`); items: R1 «уточнения инженерного брифа (ограничения реализации, границы) отражены в charter»; R2 «charter не противоречит инженерному брифу». Ребро `engineer-brief-vs-customer-brief.yaml` уже есть и не дублируется.
  Координатор (ниже) для узла charter выводит ДВА ребра (по одному на основание), для остальных — одно с несколькими основаниями, как объявляет профиль. Каждый набор — тест «идентичность каталога стабильна» по образцу теста среза 1 (`check_identity` меняется только с содержанием).
- Test: `tests/test_governance_edge_check_coordinator.py`

**Interfaces:**
- Produces: `coordinator.edges_for_level(profile_artifacts: list[dict], level: int, *, has_engineer_brief: bool) -> list[Edge]` (`Edge(node, edge_id, bases: dict[role, node])` из `upstream` профиля; для charter — `charter-vs-customer-brief` всегда и `charter-vs-engineer-brief` всегда (второе даёт `N/A` по applicability, когда инженерного брифа нет); `coordinator.result_key(subject_files, bases_files, check_identity) -> str` (D9); `coordinator.run_level(state, run_dir, level, profile_path, *, call=None) -> LevelResult` (`records: dict[node, record]`, `verdict: "PASS" | "FAIL" | "ERROR"`, `exit_code: 0|1|3`) — пишет `<run_dir>/edge-check/w<level>/<node>.json` и леджер `<run_dir>/edge-check/ledger.jsonl` (ключ D9, `attempt_id`, действующий результат — последняя завершённая попытка D10).

- [ ] **Step 1: Failing tests** — три: (а) `edges_for_level` для team-exp даёт на уровне 3 два ребра (design, acceptance) с базами requirements+behaviour-spec, на уровне 0 — два ребра charter (customer-brief, engineer-brief); (б) `run_level` с фейковым `call`, отвечающим `PASS` для всех, пишет два файла и возвращает код 0; ответ `FAIL` для одного — код 1, `verdict: FAIL`; исключение `call` — код 3, `ERROR`; (в) повторный `run_level` при неизменных входах даёт новый `attempt_id`, действующий результат — последний.
- [ ] **Step 2: Run** — FAIL. **Step 3:** реализация поверх `run_check` (`governance/edge_check/check.py`): subject — файл узла в worktree ветки волны, bases — файлы upstream **в base** (`ops.show_file` в temp-каталог) плюс source-слой для charter; `call` пробрасывается. **Step 4:** PASS. **Step 5: Commit** `git commit -m "feat(edge_check): координатор волны — состав рёбер из профиля, леджер результатов"`

---

### Task 7: Раннер — волновой цикл (ветка волны, авторинг уровня, гейт по проекции, edge-check)

**Files:**
- Modify: `governance/runner.py` (`advance` 349–398; `_step_branch` 1167–1206; `_step_materialize_brief` 1207–1262; `_step_authoring` 1476–1690; `_step_commit` 1687; `_step_gate` 1804–2228; новый `_step_edge`; `_STOPPED_RESET_OPS`)
- Test: `tests/test_governance_runner.py` (FakeOps: `switch_to`, `edge_results: dict[str, str]`, per-wave журнал)

**Interfaces:**
- Consumes: Task 2 (`state.authoring`, `state.wave`), Task 3 (`wave_profile_dir`, `verify_wave_profile_dir`), Task 6 (`coordinator.run_level`).
- Produces: в режиме `waves` шаги S1–S4 идут по ключам `branch-<w>`, `materialize-brief-<w>` (`w = state.wave`, 1-based), `author-<node>` (только узлы уровня `state.wave − 1`), `commit-<w>`, `gate-<w>`, `edge-<w>`; функция `wave_key(base: str, wave: int) -> str`; предикат `_upstream_ready(state, ops, dag, level) -> str | None` (инвариант 1: все узлы уровней < level `approved` в base и ни один не `stale`; текст отказа — узлы); стопы `stopped_stale`, `stopped_review` (edge), `stopped_gate` («проекция не совпала»).

- [ ] **Step 1: Failing tests** (FakeOps.author пишет узлы, как сегодня; `gate_candidate` — очередь; новый атрибут `edge_results: dict[str, str] = {}` — вердикт по узлу, дефолт PASS)

```python
def test_waves_run_authors_only_level_zero_and_stops_at_edge_fail(tmp_path, runs_root):
    ops = FakeOps(edge_results={"charter": "FAIL"})
    state = runner.start(**_start_kwargs(tmp_path, "r-w-edge", ops), authoring="waves")
    assert ops.authored == ["charter"]
    assert state.wave == 1 and state.status == "stopped_review"
    assert (rs.run_dir("r-w-edge") / "edge-findings.txt").exists()
    assert state.ops["branch-1"]["status"] == "completed"
    assert ops.switched == [("spec/WS-1-behaviour-w1", "master")]


def test_waves_gate_uses_projected_profile_with_siblings(tmp_path, runs_root):
    ops = FakeOps()
    state = runner.start(**_start_kwargs(tmp_path, "r-w-gate", ops), authoring="waves")
    profile_arg = [c for c in ops.calls if c[0] == "gate_check_candidate"][0][2]
    assert profile_arg.endswith("profile-w1/team-exp.yaml")
    assert (Path(profile_arg).parent / "roles.yaml").exists()


def test_waves_local_completeness_is_by_level(tmp_path, runs_root):
    ops = FakeOps()
    state = runner.start(**_start_kwargs(tmp_path, "r-w-compl", ops), authoring="waves")
    assert state.status != "stopped_gate", "GC-COMPLETENESS по полному профилю остановил бы W1"


def test_waves_refuse_to_author_level_when_upstream_not_approved(tmp_path, runs_root):
    ops = FakeOps()
    state = runner.start(**_start_kwargs(tmp_path, "r-w-up", ops), authoring="waves")
    state.wave = 2; state.status = "running"; rs.save(state)   # W2: charter в base не approved
    result = runner.advance(state, ops)
    assert result.status == "stopped_stale" and "charter" in ops.comments[-1]
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Реализация**

- `wave_key(base, wave)` → `f"{base}-{wave}"`; в режиме `legacy` ключи прежние (функция возвращает `base`).
- `_step_branch`: в `waves` — `state.branch = f"spec/{ws}-behaviour-w{state.wave}"`, dirty-гард, `ops.switch_to(target_dir, branch, base_ref or "master")` (**после** `checkout_and_pull(base)` — свежий base), op `branch-<w>`.
- `_step_materialize_brief`: ключ `materialize-brief-<w>`, тело без изменений.
- `_step_authoring`: в `waves` перед циклом — `_upstream_ready(state, ops, dag, level=state.wave - 1)`; отказ → `stopped_stale` с `_stop_with_comment`; цикл только по `_AUTHOR_STEPS` с `levels[kind] == state.wave - 1`; `--reopen` (Task 10) снимает файл и op заранее.
- `_step_commit`: ключ `commit-<w>`.
- `_step_gate`: в `waves` — `projected = wave_profile_dir(...)`, `mismatch = verify_wave_profile_dir(...)` → `stopped_gate` «проекция не совпала: …»; `gate_check_candidate(target_dir, bundle_dir, str(projected))`; локальный GC-COMPLETENESS — по `bundle_dag.dag_upto(dag, state.wave - 1)`; ключ `gate-<w>`.
- `_step_edge` (после гейта): `(run_dir / "edge-findings.txt").unlink(missing_ok=True)`; `result = coordinator.run_level(state, run_dir, state.wave - 1, projected, call=None)`; код 0 → `op_complete(edge-<w>)`; код 1 → findings-файл из `records[*].findings`, `stopped_review`; код 3 → findings с `error_code`, `stopped_review`. В `legacy` шаг — no-op.
- `advance`: список шагов в `waves` — `_step_interview, _step_branch, _step_materialize_brief, _step_authoring, _step_commit, _step_gate, _step_edge, _step_candidate (Task 8)`; после `waiting_human_merge` волны — Task 9.
- `_STOPPED_RESET_OPS` — статический dict, `resume` его итерирует, а `_reset_stopped_author` pop'ает статический `_BUNDLE_EDIT_RESET_OPS`: оба заменяются функцией `reset_ops_for(state) -> tuple[str, ...]` — в `legacy` возвращает прежние кортежи, в `waves` — `commit-<w>`, `gate-<w>`, `edge-<w>` текущей волны (без `push`/`ready`/`review`); `stopped_stale: ()`. Тесты `_STOPPED_RESET_OPS` из legacy остаются зелёными: словарь сохраняется как источник для `legacy`.
- `console_model.PIPELINE_KEYS` — статический кортеж, покрыт тестами FR-04 (индекс `"commit"`) и `_REQUIRED_STEP_KEYS`: в Task 7 он не трогается — **между Task 7 и Task 11 консоль показывает волновой прогон неверно** (промежуточное состояние названо; legacy показывается верно); Task 11 вводит `pipeline_keys(state)` при сохранённом кортеже.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/test_governance_runner.py -q` — PASS (legacy-тесты без изменений).

- [ ] **Step 5: Commit** `git commit -m "feat(runner): волновой цикл — ветка волны, авторинг уровня, гейт по проекции, обязательный edge-check"`

---

### Task 8: Edge-check срез 3 — адаптер публикации и `_step_candidate`

**Files:**
- Create: `governance/edge_check/publish.py`
- Modify: `governance/runner.py` (новый `_step_candidate`), `governance/ops.py` (`Ops.publish_review(repo_slug, pr, *, event: str, body: str, marker: str) -> bool` — `gh api pulls/<n>/reviews` под профилем `~/.config/review`; протокол + RealOps + FakeOps)
- Test: `tests/test_governance_edge_check_publish.py`, `tests/test_governance_runner.py`

**Interfaces:**
- Consumes: Task 5 (`propose_from_source`, `_publish_candidate(push=False/True)`), Task 6 (`LevelResult`).
- Produces: `publish.publish_wave(state, ops, key, level_result) -> int` — (1) проверяет поверхность D16: изменённые пути коммита заявки ⊆ {файлы узлов заявки, каскад `stale`, `00-discovery/*` для W1} — иначе `RuntimeError` до PR; (2) пишет `workstreams/<ws>/evidence/edge-check/<node>.json` из записей координатора, коммитит одним коммитом поверх головы заявки, `al.record_head_sha` (перезапись), `ops.push_branch`; (3) `_publish_candidate(push=True)` создаёт/усыновляет PR; (4) сверка головы PR == записанный `head_sha` (иначе код 4); (5) `ops.publish_review(..., event="APPROVE", body=<чек-лист с identity и SHA>, marker="edge-check")` — только при `verdict == "PASS"`; `CHANGES_REQUESTED` человека не гасится (читает `ops.pr_reviews`, при наличии — код 1); коды 0/1/3/4 из словаря раннера.
- `_step_candidate` (ключ `candidate-<w>`): `outcome = an.propose_from_source(state, ops, nodes_of_level, head_sha_of_branch)`; `state.base_ref = pr_facts(candidate_pr)["baseRefName"] or "master"` (в waves его больше никто не ставит — legacy ставил `_step_verdict`); `code = publish_wave(...)`; 0 → `waiting_human_merge` с `state.wave` и `candidate_pr` в комментарии; 4 → сброс `gate-<w>`/`edge-<w>`, повтор; 1/3 → `stopped_review`.

- [ ] **Step 1: Failing tests** — стенд approve_node (настоящий git): после `propose_from_source` + `publish_wave` в ветке заявки лежит `evidence/edge-check/charter.json`, `head_sha` заявки == голова ветки, у PR ревью `APPROVED` от логина ревьюера с маркером `edge-check`; посторонний путь в коммите заявки → `RuntimeError` до PR; повтор `publish_wave` (падение между push и review) идемпотентен — критерий: дерево головы заявки уже содержит `evidence/edge-check/*.json` с теми же байтами ⇒ evidence-коммит не создаётся, `head_sha` не меняется, публикуется только ревью. Раннер (FakeOps): `_step_candidate` даёт `waiting_human_merge` с комментарием, называющим номер PR и волну.
- [ ] **Step 2: Run** — FAIL. **Step 3:** реализация по интерфейсу. **Step 4:** PASS. **Step 5: Commit** `git commit -m "feat(edge_check): адаптер публикации — evidence в candidate, одобряющее ревью edge-check, шаг candidate раннера"`

---

### Task 9: Resume волны — реконсиляция candidate, finalize агентом, следующая волна

**Files:**
- Modify: `governance/runner.py` (`resume` 451–545; `_reconcile_pr_merged_out_of_band` 547–609; новый `_finalize_wave`; `_step_s8` вход после W5)
- Test: `tests/test_governance_runner.py` (FakeOps: `pr_reviews`, `approve_node_calls`) + сквозной тест на стенде approve_node

**Interfaces:**
- Consumes: Task 8; `an.approve_node` (finalize путь), `ops.review` (аттестация finalize-PR), `_missing_approving_review`.
- Produces: в `waves`: `resume` при `waiting_human_merge` находит заявку волны как **живую заявку над любым узлом уровня** — `al.live_request_over(state, node)` для узлов волны (не `live_request_for_step`: та отдаёт заявки только до мержа candidate, и падение между `record_merge` и finalize оставила бы resume без заявки при открытом finalize-PR); `pr_facts(candidate_pr)`: OPEN → стоим; MERGED (или факт мержа уже записан) → `_finalize_wave`: `an.approve_node(state, ops, node)` (фаза 2 + finalize-PR), затем `ops.review(state.repo, finalize_pr)` (аттестация; ненулевой код → «finalize остаётся человеку», `waiting_human_merge` с причиной) и повторный `approve_node` (агентский мерж finalize); заявка `completed` → `state.wave += 1`, `status = "running"`, `advance` (следующая волна) либо после W5 (`state.wave > bundle_dag.wave_count(dag)`) — `_step_s8`. Реконсиляция «мерж вне раннера» ключуется на `candidate_pr` заявки, не на `state.pr`.

- [ ] **Step 1: Failing tests** — (а) FakeOps: после `waiting_human_merge(wave=1)` помечаем candidate MERGED → `resume` зовёт approve_node дважды и `review` на finalize, `state.wave == 2`, авторится requirements; (а′) стенд approve_node: крэш после `record_merge` (finalize-PR открыт, заявка на шаге `AWAIT_FINALIZE_MERGE`) → `resume` находит заявку через `live_request_over` и доводит finalize; (а″) `state.base_ref` после `_step_candidate` равен `baseRefName` candidate-PR; (б) `review` на finalize вернул 6 → статус `waiting_human_merge`, комментарий «finalize остаётся человеку»; (в) сквозной на стенде approve_node: пять волн → пять `merge_pr` человеком → `read_dag_state` = approved DAG → `deliver_for_run` проходит (число человеческих актов = 5 + approve tasks).
- [ ] **Step 2: Run** — FAIL. **Step 3:** реализация. **Step 4:** PASS. **Step 5: Commit** `git commit -m "feat(runner): resume волны — реконсиляция по candidate, finalize агентом после аттестации, переход к следующему уровню"`

---

### Task 10: `--reopen <node>` и `stopped_stale`

**Files:**
- Modify: `governance/runner.py` (`reopen(run_id, node, ops, *, manual: bool)`), `governance/task_bridge.py:3947-4030` (CLI `--reopen`, `--manual`), `governance/approve_node.py` (`stale_below_top_level(state, ops, dag) -> list[str]` — публичный предикат)
- Test: `tests/test_governance_runner.py`, `tests/test_governance_approve_node.py`

**Interfaces:**
- Produces: `runner.reopen(run_id, node, ops, manual=False) -> RunState`: `state.wave = level(node) + 1`; ветка `spec/<ws>-behaviour-w<k>` от base (S9); файл узла удаляется из worktree и `author-<node>` сбрасывается; `manual` → `stopped_author` с подсказкой; иначе `advance` (авторинг заново → гейт → candidate над новым текстом; `_publish_candidate` каскадом ставит `stale` нижним, лежащим в base). `stale_below_top_level` — узлы `stale` в base ниже верхнего approved уровня; `_upstream_ready` использует его для `stopped_stale`; после переодобрения по уровням (заявки без `source_sha`, Task 5) продвижение возобновляется.

- [ ] **Step 1: Failing tests** — стенд approve_node: W1..W3 approved; `reopen(run_id, "requirements")` → FakeOps не применим, стенд настоящий git: новая ветка `…-w1` от base без `10-requirements.md`; после авторинга (файл пишется тестом) и `propose_from_source` candidate несёт `stale` у `15-behaviour-spec.md`; после мержа/finalize `stale_below_top_level` == `["behaviour-spec"]`; `deliver_for_run` отказывает; переодобрение behaviour-spec заявкой без `source_sha` → пусто → продвижение.
- [ ] **Step 2–5:** FAIL → реализация → PASS → `git commit -m "feat(runner): --reopen — явное переоткрытие узла, stopped_stale и переодобрение по уровням"`

---

### Task 11: `spec-loop`, консоль, документы

**Files:**
- Modify: `governance/spec_loop.py` (флаг `--waves` → `authoring="waves"`; сообщения паузы называют волну и candidate-PR; восстановление из фактов GitHub для волнового прогона: `prs_by_head_prefix("spec/<ws-id>-approve-")` → кандидаты по глобу `patterns.env`; «последняя волна» = максимальный `(W, K, A)` из имени ветки среди PR со `state == MERGED`; тело обязано нести `run-id:` (иначе отказ «PR без run-id — восстановить нечем»); OPEN candidate/finalize → отказ «одобрение в полёте, восстановление после мержа»; несколько MERGED с одинаковым `(W, K, A)` → отказ с перечнем; ноль — `None` (новый прогон). Восстановленный леджер: `authoring=waves`, `wave = K + 1`, заявки волн ≤ K — `completed` по факту MERGED finalize-PR; `_APPROVE_NODE_HINT` и docstring — две модели), `governance/console_model.py` (`pipeline_keys(state)` — per-wave ключи в волновом режиме при сохранённом кортеже `PIPELINE_KEYS` для legacy и тестов FR-04; показ волны и edge-check), `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md` (§I12: заявка волны над байтами ветки волны, таблица актов), `TODO.md`
- Test: `tests/test_governance_spec_loop.py`, `tests/test_governance_console_model.py`

- [ ] **Step 1: Failing tests** — `spec-loop --waves` стартует прогон с `authoring == "waves"`; пауза печатает `wave=<k>` и `candidate-PR #<n>`; восстановление находит прогон по candidate последней волны; консоль показывает `edge-1 … edge-5`.
- [ ] **Step 2–5:** FAIL → реализация → PASS → `git commit -m "feat(spec_loop,console): волновой режим кнопки, восстановление по candidate, показ волн и edge-check"`

---

### Task 12: Заявка соседу, полный прогон, PR, живая приёмка

- [ ] **Step 1: Inbox-issue в steward** (ADR-ECO-006): `slug: gate-check-upto-level`, `from: devtools#<PR>` — флаг `gate-check --candidate --upto <node>` как целевое состояние вместо проекции профиля копией (спека S5, §7).
- [ ] **Step 2: Полный прогон** `uv run --frozen pytest -q` — PASS; `make plan-check-selftest` — OK.
- [ ] **Step 3: TODO** — `@id:sequential-node-approval` и `@id:bundle-edge-check` (срезы 2–3) закрываются PR кода; ловушки — в тело пункта.
- [ ] **Step 4: PR кода** — ветка `feat/sequential-node-approval`; ревью терминальным прогоном (1 full + 1 targeted); агентский мерж (харнесс-путей нет).
- [ ] **Step 5: Живая приёмка** (спека §6.3): `make spec-loop … ARGS='--brief … --waves'` на крошечном предмете; evidence `docs/evidence/<дата>-waves-live-run.md`: 6 человеческих актов, edge-check evidence и его ревью на каждом candidate, ноль платных ревью моделью на candidate/finalize, ноль правок узлов после одобрения без `--reopen`. До двух живых прогонов дефолт остаётся `legacy` (S13).

---

## Self-review

- **Покрытие спеки:** S1/§3.3 — Task 1; S13 — Task 2, 11; S5 — Task 3, 7; S2/S4 — Task 4, 5; S6 — Task 6, 7; S7 — Task 8; S8/S10 — Task 9; S11/§3.4 — Task 10; S9 — Task 7, 10; S12/§6.3 — Task 9(в), 12; §7 риск проекции — Task 12 Step 1.
- **Плейсхолдеры:** тела Task 6, 8, 9, 10, 11 заданы интерфейсами и тестами (шаги реализации — «по интерфейсу»); тесты Task 5 (б–г) и Task 6 описаны словами — исполнитель разворачивает их по образцу (а). Это сознательный уровень детализации для второго и третьего срезов, где точная форма зависит от Task 5–7; перед исполнением каждой такой задачи исполнитель дописывает тест по образцу и запускает красным.
- **Типы:** `wave_key(base, wave)`, `state.wave` (int, уровень), `propose_from_source(state, ops, node_ids, source_sha)`, `LevelResult(records, verdict, exit_code)`, `publish_wave(state, ops, key, level_result) -> int` — сквозные.
- **Порядок:** после каждой задачи прежний путь (`authoring="legacy"`) работает; волновой путь становится проходимым насквозь после Task 9.
