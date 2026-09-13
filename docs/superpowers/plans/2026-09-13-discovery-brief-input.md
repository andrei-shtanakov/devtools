# E1 discovery-brief input — Implementation Plan

**Goal:** `spec-loop --brief <path>` fail-closed принимает customer либо
engineer discovery-brief, доставляет полный source-слой тем же bundle-PR и
строит charter/requirements с durable source pins.

**Spec:**
`docs/superpowers/specs/2026-09-13-discovery-brief-input-design.md`
(влита devtools#217). При расхождении план уступает спецификации.

**Stack:** Python stdlib + PyYAML из текущего проекта, pytest, GitHub Actions.
Соседние репо read-only. Vendored bytes не редактировать.

## Global constraints

- Без `--brief` публичные API, старые ledger и шестиузловой DAG ведут себя
  прежним образом.
- Intake целиком завершается до `_reserve_run_id`, ветки и paid author.
- `gate_check.py` и контракт — точные байты discovery-toolkit на pin из
  спеки; адаптация только вне vendored-каталога.
- Primary и engineer-upstream копируются побайтово; destination всегда под
  `<bundle_dir>/00-discovery/`.
- Source не входит в human approval composition, но его blob'ы являются
  supplemental upstream charter во всех фазах approval/delivery.
- Ни один тест не доказывает отсутствие эффекта только кодом возврата:
  проверять ledger bytes и Ops calls.
- Каждый шаг: красный тест → минимальная реализация → целевой набор → commit.

## Task 1. Vendored discovery contract и две гарантии копии

**Files:**

- Create: `governance/discovery_contract/{__init__.py,gate_check.py,DISCOVERY-BRIEF-CONTRACT.md,PINNED.txt}`
- Create: `tools/check_discovery_vendor.py`
- Create: `tests/test_discovery_vendor.py`
- Create: `.github/workflows/discovery-contract-integrity.yml`
- Create: `.github/workflows/discovery-contract-drift.yml`

**Red tests:**

- expected surface нельзя сузить правкой `PINNED.txt`;
- consistency сравнивает local sha256;
- provenance сравнивает upstream blobs на pinned commit;
- drift различает equal / moved / unavailable;
- unavailable даёт `unknown` и exit 3, не pass.

**Implementation:** перенести точные bytes из read-only
`../discovery/src/discovery/contract/` для contract/linter и pin на исходный
discovery-toolkit commit. Checker повторяет интерфейс discovery
`consistency|provenance|drift`, но пути привязаны к devtools. Scheduled
workflow запускает drift; PR workflow запускает consistency+provenance.

**Verify:**

```bash
uv run --frozen --group dev pytest -q tests/test_discovery_vendor.py
uv run --frozen tools/check_discovery_vendor.py consistency
uv run --frozen tools/check_discovery_vendor.py provenance
```

## Task 2. Pure intake и переносимый source layout

**Files:**

- Create: `governance/brief_input.py`
- Create: `tests/test_governance_brief_input.py`

**Public interface:**

```python
@dataclass(frozen=True)
class BriefSource:
    frame: Literal["customer", "engineer"]
    primary_input: Path
    primary_rel: str
    requirements_input: Path
    requirements_rel: str
    source_paths: tuple[str, ...]
    source_blobs: tuple[tuple[str, str], ...]

def inspect_brief(path: Path) -> BriefSource: ...
def materialize(source: BriefSource, target_dir: Path, bundle_dir: str) -> None: ...
def inspect_materialized(target_dir: Path, bundle_dir: str) -> BriefSource: ...
```

`source_blobs` считаются через git blob sha1 по точным текстовым bytes, не
sha256 manifest. `primary_rel` всегда `00-discovery/brief.md`.

**Red matrix:** customer pass/error/warning; engineer good; absent/draft/
non-customer/multiple upstream; absolute/`..`/collision; binary/нечитаемый
input; exact copied bytes; повторный vendored gate из destination layout;
materialized mismatch.

**Implementation:** использовать публичные `check`/`parse_brief` vendored
module. Path ref извлекать из parsed meta, резолвить без приватного импорта
из соседа. Ошибки сводить в `BriefInputError` с правилами и refs.

## Task 3. Ledger и CLI intake до эффектов

**Files:**

- Modify: `governance/run_state.py`
- Modify: `governance/spec_loop.py`
- Modify: `tests/test_governance_run_state.py`
- Modify: `tests/test_governance_spec_loop.py`

**State:** добавить последним полем `brief: dict | None = None`; factory
принимает descriptor. Старый JSON без поля загружается через dataclass default.
Descriptor сериализуемый и не содержит bytes/Path.

**CLI:** `--brief` разрешён только на новом прогоне. При найденном run:

- без флага продолжение использует descriptor ledger;
- тот же флаг должен совпасть с source blobs ledger;
- другой brief — fail-closed с подсказкой создать другой ws-id;
- recovered run не принимает новый источник задним числом.

Intake происходит до генерации run-id/вызова `runner.start`. Таблица resolved
values печатает frame и source paths.

**Recovery:** по файлам MERGED bundle-PR обнаружить
`<bundle>/00-discovery/brief.md`; заново вызвать `inspect_materialized` на
base после checkout, записать descriptor. Отсутствие source означает legacy
run. Неполный source layer — fail-closed, не legacy.

**Red checks:** invalid brief оставляет `RUNS_ROOT` и Ops calls пустыми;
старый ledger грузится; mismatch rejected; recovery customer/engineer;
classic recovery unchanged.

## Task 4. Write-ahead materialization и prompts

**Files:**

- Modify: `governance/runner.py`
- Modify: `governance/ops.py`
- Modify: `governance/console_model.py`
- Modify: `tests/test_governance_runner.py`
- Modify: `tests/test_governance_ops.py`
- Modify: `tests/test_governance_console_model.py`

**Ordering:** `branch → materialize-brief → author-charter`. Новый op входит в
pipeline view только когда `state.brief != None`; classic rows не получают
ложный обязательный этап.

Материализация сверяет descriptor с исходными файлами непосредственно перед
записью, пишет source, затем `op_complete` с blobs. Если op completed, resume
сверяет destination bytes; расхождение стопит до author.

`Ops.author(..., brief_context: dict | None = None)` добавляет source guidance
только charter/requirements. Prompt называет repo-relative paths и pins, не
вставляет текст brief. Остальные четыре prompts байт-в-байт прежние.

**Red checks:** точный порядок calls/save; crash before/after copy; missing
source; changed input; charter/requirements prompt tokens; behaviour prompt
не содержит brief; no-brief old prompt exact.

## Task 5. Coverage guard до следующих paid nodes

**Files:**

- Extend: `governance/brief_input.py`
- Extend: `tests/test_governance_brief_input.py`
- Modify: `governance/runner.py`
- Modify: `tests/test_governance_runner.py`

**Interface:** `requirements_findings(source_text, requirements_text) ->
list[str]` — pure.

**Rules:** strict/near-miss parser для FR/NFR blocks; source ID uniqueness;
каждый source FR/NFR ровно один раз downstream; каждый source Must-FR остаётся
Must. Никакого semantic judge prose.

После завершения/skip author-requirements runner вызывает guard до
author-behaviour. Failure пишет `brief-findings.txt`, ставит
`stopped_author`, сохраняет state; следующий paid call отсутствует. Resume
после ручной правки requirements повторяет guard.

**Red checks:** missing/duplicate/demoted Must, missing Should FR, missing NFR,
near-miss; positive customer/engineer; assert call order and absent later
author calls.

## Task 6. Supplemental source pins во всём approval-контуре

**Files:**

- Create: `governance/bundle_inputs.py`
- Create: `tests/test_governance_bundle_inputs.py`
- Modify: `governance/approve_node.py`
- Modify: `governance/task_bridge.py`
- Modify: `tests/test_governance_approve_node.py`
- Modify: `tests/test_governance_task_bridge.py`

**SSOT:**

```python
def direct_blobs(state, ops, dag, node, ref) -> Fact[dict[str, str]]: ...
```

Для node != charter либо brief=None возвращает прежние DAG upstream blobs.
Для brief-enabled charter добавляет descriptor source keys и читает bytes из
того же ref, что стандартные upstream. Descriptor hash — ожидание, не источник.

Заменить локальные вычисления в:

- proposal intent snapshot;
- candidate bytes (`upstream_hashes` всегда полная exact map);
- post-candidate и post-finalize checks;
- `read_dag_state`;
- task delivery approval gate.

S4 prospective guard также вызывает тот же SSOT. Недоступный source —
unresolved, не debt и не pass.

**Mutation tests:** удалить source pin; изменить source bytes; не сохранить
extra pin в candidate; сравнить с пустым dict в finalize. Каждый мутант обязан
покрасить соответствующий тест. No-brief approval fixtures не меняются.

## Task 7. Content anchor и PR-head reconciliation

**Files:**

- Modify: `governance/task_bridge.py`
- Modify: `tests/test_governance_task_bridge.py`

`_content_anchor` получает source descriptor и добавляет canonical exact
source bytes после стандартных шести узлов. Source не проходит
signature-stripping: это чужой immutable input, его frontmatter целиком —
содержание.

`_delivered_content_anchor` материализует source paths из head PR вместе с
узлами DAG. Missing head/source ⇒ comparison unavailable по существующему §6,
не пересчёт по current base.

Все normal/supersede/replace пути передают `state.brief`. Direct legacy
`deliver` без state сохраняет прежний anchor.

**Red checks:** одинаковый bundle + изменённый brief меняет anchor; provenance
charter re-pin без изменения source не меняет source portion; reconciliation
читает source из `headRefOid`; missing source does not use base; no-op
supersede при неизменном source.

## Task 8. Документация, полный regression и live acceptance

**Files:**

- Modify: `README.md`, `Makefile`, `TODO.md`
- Create after live run:
  `docs/evidence/2026-09-*-discovery-brief-spec-loop-run.md`

README/Make help описывают `--brief`, обе frame semantics и что E1 не запускает
interview. TODO закрывается только после code PR + live evidence.

**Static/full checks:**

```bash
git diff --check
UV_CACHE_DIR=/tmp/devtools-uv-cache make plan-check
UV_CACHE_DIR=/tmp/devtools-uv-cache make plan-check-selftest
UV_CACHE_DIR=/tmp/devtools-uv-cache uv run --frozen --group dev --group governance pytest -q
```

Ruff запускается только если объявлен project dependency/CI command; не
подменять проверку случайным глобальным бинарём.

**Live sequence:**

1. Реальный discovery engineer session выпускает ready/pass brief с approved
   customer upstream.
2. Новый чистый target checkout; `spec-loop --brief` создаёт bundle PR.
3. Человек мержит bundle PR и candidate/finalize PR всех шести approval nodes.
4. Task bridge создаёт tasks PR; проверить source pins approved charter и S8
   evidence.
5. Человек approve tasks; conform; spec-runner strict до terminal result.
6. Evidence фиксирует sha, PR, run-id и команды без секретов.

Live шаги с человеческими merge/approve не автоматизируются и не считаются
зелёными по локальному mock.

## Delivery split

1. Этот plan PR — docs only, terminal review, agent merge.
2. Code PR tasks 1–7 + статическая часть 8. Workflow/authority-root paths могут
   потребовать human merge по `merge-pr.sh`; это ограничение не обходить.
3. Live acceptance PR — evidence + закрытие TODO после реальных человеческих
   границ.

