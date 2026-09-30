# criteria-closure/v1: сверка ответа под схемы v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** потребитель devtools (`criteria_check` / `criteria_close`) принимает ответ `spec-runner verify --criteria` строго по опубликованным схемам `criteria-closure/v1` (spec-runner B2a, #627 @ `3a1b9aa`) и пересчитывает всё, что в нём проверяемо, по своим байтам на `product_sha` — при этом оракул остаётся недоступным, пока не выйдет релиз spec-runner с командой `verify --criteria` (B2b).

**Architecture:** схемы и эталонные ответы вендорятся пиненой копией; доступность оракула отделяется от вендоринга (`MIN_SPEC_RUNNER_VERSION=pending`). Новый модуль `criteria_product` читает конфиг продукта на `product_sha` из git-объектов (корни, окружение) и считает `content_sha256` §6.1; `criteria_check` получает таблицу видов ошибок, разбор ответа по веткам и коду выхода, пересчёт статусов §3.7 и проверку полноты по `node_id`/исключениям; `criteria_close` собирается из этих частей, ключ G6 — по ответу (C.4).

**Tech Stack:** Python 3.12, `jsonschema` (уже в группе governance), `git` через `subprocess`, pytest; `uv run --frozen --group governance`.

**Spec:** норма — `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` (rev 10; §3.1 G6, §4, §5.1–5.4); контракт производителя — spec-runner `docs/superpowers/specs/2026-09-29-criteria-closure-verify-design.md` @ `3a1b9aa` (rev 4: §3.2 коды, §3.3 окружение, §3.4 корни, §3.5 сбор/исключения, §3.7 статусы, §4 форма, §6.1 `content_sha256`, §6.2); наши условия — devtools#491 (A, B.6, C.1–C.4), spec-runner#623 (закрыт). Пункт TODO: `@id:criteria-closure-v1-signoff`.

## Global Constraints

- Источник схем и эталонов — spec-runner @ `3a1b9aa2…` (полный SHA берётся `git -C ../spec-runner rev-parse 3a1b9aa`): `schemas/criteria-closure/v1/{request,response}.schema.json`, `tests/fixtures/criteria-closure/v1/responses/{answer,error-blocked,error-retryable,not-applicable}.json`. Байты — `git show <sha>:<path>`, не правятся.
- Оракул недоступен до релиза с `verify --criteria`: `MIN_SPEC_RUNNER_VERSION=pending` ⇒ `oracle_available() is False` при любом установленном spec-runner. Число версии выставит PR, вендорящий `min-spec-runner.env` производителя (дизайн §4: «The release PR for X writes …»).
- Коды выхода производителя: 0 — ответ; 2 — ошибка, `retryable: true` (отказ шага, повторяемый); 3 — ошибка, `retryable: false` (закрытие `blocked`); любой другой — отказ шага. Ветка `not_applicable` в v1 не выпускается — её получение есть нарушение протокола.
- Вид ошибки → `(retryable, exit)` — своя таблица на все 21 вид (дизайн §3.2); несовпадение кода выхода с видом — отказ шага.
- Ничего из ответа не принимается на веру, что можно пересчитать по байтам на `product_sha`: `product_roots.declared/files`, `environment.groups/extras`, `lock_sha256`, `content_sha256`, `outcome` прогона, статусы селектора и BEH.
- Байты продукта читаются из git-объектов `product_sha` (`git show`, `git ls-tree`), не из рабочего дерева.
- `owner_repo` в запросе — всегда `owner/name` (`state.repo_slug`).
- `issue_console.py` и `governance/discovery_contract/**` не трогать; `.github/` не трогать (иначе мерж человеком).
- Полный набор тестов зелёный в обоих режимах: `uv run --frozen pytest -q` и `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q`; `uv run --frozen --group=selfcheck ruff check .` и `ruff format --check .` чисто.

## Review Focus

1. Повторяемая ошибка без конца (`clone-failed` на не-UTF-8 пути, `collection-failed` при `-p no:xdist` до B2b) — `criteria-close` на exit 2 отказывает шагу и **не** записывает ключ; оператор видит вид и `detail`, а не молчаливый цикл. Тест: Task 7, `test_retryable_error_is_step_failure_without_key`.
2. Владелец токена лежит в `skipped`/`ignored` файле, его тест `deselected` или файл вне `test_items` и `collection_excluded` (вне `testpaths`) — BEH не получает `traced`, даже если другой селектор traced. Тест: Task 5, `test_excluded_owner_blocks_traced` (три формы + вне testpaths).
3. Параметризованный тест потерял один `node_id` при том же определении — отказ полноты, а не traced. Тест: Task 5, `test_lost_parametrized_node_id_refused`.
4. pytest собрал определение из другой ветки `if/else` (другая `line`) — определение-владелец не собрано ⇒ BEH не traced. Тест: Task 5, `test_definition_from_other_branch_is_not_owner`.
5. Второй прогон пустой (нет строк продукта), первый полный — селектор не traced (объединение по прогонам запрещено §3.7). Тест: Task 4, `test_empty_second_run_is_not_traced`.

---

## File Structure

| файл | ответственность |
|---|---|
| `contracts/criteria-closure/v1/{PIN,manifest.json,request.schema.json,response.schema.json}` | вендоренные схемы (создать) |
| `contracts/criteria-closure/v1/fixtures/responses/{PIN,manifest.json,*.json}` | вендоренные эталонные ответы (создать; своя копия, как `fixtures/ownership/`) |
| `contracts/criteria-closure/v1/min-spec-runner.env` | `MIN_SPEC_RUNNER_VERSION=pending` (изменить) |
| `contracts/criteria-closure/v1/README.md` | состояние копии (изменить) |
| `governance/criteria_contract.py` | `pending`, путь апстрима схем по умолчанию (изменить) |
| `governance/criteria_product.py` | **новый**: чтение конфига продукта на `product_sha`, корни §3.4, окружение §3.3, `content_sha256` §6.1, строки тел функций продукта |
| `governance/criteria_check.py` | таблица видов, разбор ответа по веткам, пересчёт §3.7, полнота и исключения, `validate_answer` (переписать сверку) |
| `governance/criteria_close.py` | запрос со `repo_slug`, ветвление 0/2/3, ключ G6 по ответу (изменить `_measure`) |
| `tests/test_governance_criteria_contract.py`, `tests/test_governance_criteria_product.py` (новый), `tests/test_governance_criteria_check.py`, `tests/test_governance_criteria_close.py` | тесты |

Порядок: Task 1 → 2 → 3 → 4 → 5 → 6 → 7. Task 3 (`criteria_product`) не зависит от 2 и может идти параллельно с ним.

---

### Task 1: Вендоринг схем и эталонов, оракул остаётся недоступным

**Files:**
- Create: `contracts/criteria-closure/v1/PIN`, `manifest.json`, `request.schema.json`, `response.schema.json`
- Create: `contracts/criteria-closure/v1/fixtures/responses/PIN`, `manifest.json`, `answer.json`, `error-blocked.json`, `error-retryable.json`, `not-applicable.json`
- Modify: `contracts/criteria-closure/v1/min-spec-runner.env`, `contracts/criteria-closure/v1/README.md`
- Modify: `governance/criteria_contract.py`
- Test: `tests/test_governance_criteria_contract.py`

**Interfaces:**
- Produces: `criteria_contract.MinVersion(version: str | None)`; `read_min_version()` → `MinVersion(None)` для `pending`; `oracle_available(installed, minimum, *, is_vendored) -> bool` (False при `minimum.version is None`); `drift_findings(..., upstream_path="schemas/criteria-closure/v1")`; `RESPONSES_DIR = CONTRACT_DIR / "fixtures/responses"`.

- [ ] **Step 1: Скопировать байты апстрима**

```bash
SHA=$(git -C ../spec-runner rev-parse 3a1b9aa)
D=contracts/criteria-closure/v1
for f in request response; do git -C ../spec-runner show $SHA:schemas/criteria-closure/v1/$f.schema.json > $D/$f.schema.json; done
mkdir -p $D/fixtures/responses
for f in answer error-blocked error-retryable not-applicable; do git -C ../spec-runner show $SHA:tests/fixtures/criteria-closure/v1/responses/$f.json > $D/fixtures/responses/$f.json; done
printf 'SOURCE: spec-runner @ %s\n' "$SHA" > $D/PIN
printf 'SOURCE: spec-runner @ %s\n' "$SHA" > $D/fixtures/responses/PIN
python3 - <<'EOF'
import hashlib, json, pathlib
for d, names in [("contracts/criteria-closure/v1", ["request.schema.json", "response.schema.json"]),
                 ("contracts/criteria-closure/v1/fixtures/responses",
                  ["answer.json", "error-blocked.json", "error-retryable.json", "not-applicable.json"])]:
    m = {n: hashlib.sha256((pathlib.Path(d) / n).read_bytes()).hexdigest() for n in names}
    (pathlib.Path(d) / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
EOF
```

- [ ] **Step 2: Написать падающие тесты**

```python
# tests/test_governance_criteria_contract.py (добавить)
from governance import criteria_contract as cc


def test_vendored_copy_is_consistent():
    assert cc.integrity_findings() == []
    assert cc.vendored()


def test_responses_copy_is_consistent():
    assert cc.integrity_findings(cc.RESPONSES_DIR) == []


def test_pending_min_version_keeps_oracle_unavailable():
    """Схемы вендорены, но команды verify --criteria ещё нет (B2b):
    оракул недоступен при любом установленном spec-runner."""
    minimum = cc.read_min_version()
    assert minimum.version is None
    assert not cc.oracle_available("99.0.0", minimum, is_vendored=True)


def test_numeric_min_version_gates_as_before(tmp_path):
    env = tmp_path / "min.env"
    env.write_text("MIN_SPEC_RUNNER_VERSION=4.5.0\n")
    m = cc.read_min_version(env)
    assert cc.oracle_available("4.5.0", m, is_vendored=True)
    assert not cc.oracle_available("4.4.9", m, is_vendored=True)


def test_drift_reads_schemas_from_schemas_dir(tmp_path):
    """Апстрим держит схемы в schemas/criteria-closure/v1, не в contracts/."""
    import inspect

    sig = inspect.signature(cc.drift_findings)
    assert sig.parameters["upstream_path"].default == "schemas/criteria-closure/v1"
```

- [ ] **Step 3: Прогнать — падают**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_contract.py -q`
Expected: FAIL — `RESPONSES_DIR` нет, `version` = `"0.0.0"`, default `upstream_path` = `contracts/…`.

- [ ] **Step 4: Реализация**

`min-spec-runner.env`:

```
# Минимальная версия spec-runner с `verify --criteria` (spec-runner#603).
# pending — команды ещё нет (схемы v1 вендорены с B2a, команда — B2b):
# оракул недоступен при любом установленном spec-runner. Число выставит PR,
# вендорящий min-spec-runner.env производителя (дизайн §4).
MIN_SPEC_RUNNER_VERSION=pending
```

`governance/criteria_contract.py`:

```python
RESPONSES_DIR = CONTRACT_DIR / "fixtures/responses"


@dataclass(frozen=True)
class MinVersion:
    version: str | None  # None — команда ещё не выпущена (`pending`)


def read_min_version(path: Path = CONTRACT_DIR / "min-spec-runner.env") -> MinVersion:
    for line in path.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "MIN_SPEC_RUNNER_VERSION":
            value = value.strip()
            return MinVersion(None if value == "pending" else value)
    raise ValueError(f"{path}: нет MIN_SPEC_RUNNER_VERSION")


def oracle_available(
    installed: str | None, minimum: MinVersion, *, is_vendored: bool
) -> bool:
    """Оракул доступен: контракт вендорен, команда выпущена и spec-runner
    машины не ниже."""
    if not is_vendored or installed is None or minimum.version is None:
        return False
    return _parts(installed) >= _parts(minimum.version)
```

и в `drift_findings` default `upstream_path: str = "schemas/criteria-closure/v1"` (докстринг: «схемы — в `schemas/…`, эталоны и фикстуры владения — в `tests/fixtures/…`»).

`README.md`: заменить абзац «Сейчас здесь только `min-spec-runner.env`» на состояние: схемы v1 и эталоны вендорены @ `3a1b9aa` (B2a), `vendored()` — True, но `MIN_SPEC_RUNNER_VERSION=pending` держит оракул недоступным до релиза с `verify --criteria`; эталоны — отдельная копия `fixtures/responses/` со своими `PIN`/`manifest.json` (апстрим `tests/fixtures/criteria-closure/v1/responses/`).

- [ ] **Step 5: Прогнать — зелёные; проверить дрейф против соседа**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_contract.py tests/test_governance_criteria_close.py -q`
Expected: PASS (тесты `criteria_close`, ожидающие `not-applicable: spec-runner-version`, остаются зелёными: оракул недоступен).

Run:
```bash
uv run --frozen --group governance python -c "from pathlib import Path; from governance.criteria_contract import *; print(drift_findings(CONTRACT_DIR, Path('../spec-runner'), ci=True)); print(drift_findings(RESPONSES_DIR, Path('../spec-runner'), ci=True, upstream_path='tests/fixtures/criteria-closure/v1/responses'))"
```
Expected: `([], [])` дважды.

- [ ] **Step 6: Commit**

```bash
git add contracts/criteria-closure/v1 governance/criteria_contract.py tests/test_governance_criteria_contract.py
git commit -m "criteria-closure/v1: vendor schemas + response goldens @ spec-runner 3a1b9aa; oracle pending"
```

---

### Task 2: Таблица видов ошибок и разбор ответа по веткам и коду выхода

**Files:**
- Modify: `governance/criteria_check.py` (`parse_response`, новые `ERROR_KINDS`, `Parsed`)
- Test: `tests/test_governance_criteria_check.py`

**Interfaces:**
- Consumes: `criteria_contract.RESPONSES_DIR`, `CONTRACT_DIR / "response.schema.json"`.
- Produces:
  - `ERROR_KINDS: dict[str, tuple[bool, int]]` — вид → (retryable, exit), 21 запись;
  - `@dataclass(frozen=True) class Parsed: response: dict; branch: Literal["answer", "error"]; retryable: bool`;
  - `parse_response(code: int, stdout: str, schema: dict | None) -> tuple[Parsed | None, str | None]`.

- [ ] **Step 1: Падающие тесты на эталонах**

```python
import json

import pytest

from governance import criteria_check as ch
from governance import criteria_contract as cc

SCHEMA = json.loads((cc.CONTRACT_DIR / "response.schema.json").read_text())


def golden(name: str) -> str:
    return (cc.RESPONSES_DIR / f"{name}.json").read_text()


def test_error_kinds_cover_schema_enum():
    enum = SCHEMA["definitions"]["error_kind"]["enum"]
    retryable = set(SCHEMA["definitions"]["retryable_kinds"]["enum"])
    assert set(ch.ERROR_KINDS) == set(enum)
    for kind, (retry, exit_code) in ch.ERROR_KINDS.items():
        assert retry == (kind in retryable)
        assert exit_code == (2 if retry else 3)


@pytest.mark.parametrize(
    ("code", "name", "branch", "retryable"),
    [(0, "answer", "answer", False), (3, "error-blocked", "error", False),
     (2, "error-retryable", "error", True)],
)
def test_goldens_parse(code, name, branch, retryable):
    parsed, why = ch.parse_response(code, golden(name), SCHEMA)
    assert why is None
    assert (parsed.branch, parsed.retryable) == (branch, retryable)


@pytest.mark.parametrize(
    ("code", "name", "why"),
    [
        (2, "answer", "код выхода 2 при ответе"),
        (0, "error-blocked", "код выхода 0 при ошибке"),
        (2, "error-blocked", "вид product-roots-undeclared требует код 3"),
        (3, "error-retryable", "вид product-sha-absent требует код 2"),
        (0, "not-applicable", "not_applicable в v1 не выпускается"),
        (1, "answer", "код выхода 1 вне 0/2/3"),
    ],
)
def test_branch_and_exit_must_agree(code, name, why):
    parsed, got = ch.parse_response(code, golden(name), SCHEMA)
    assert parsed is None and why in got


def test_schema_is_required():
    parsed, why = ch.parse_response(0, golden("answer"), None)
    assert parsed is None and "схема" in why
```

- [ ] **Step 2: Прогнать — падают**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_check.py -q -k "kinds or goldens or agree or schema_is_required"`
Expected: FAIL — `ERROR_KINDS`/`Parsed` не определены.

- [ ] **Step 3: Реализация** (заменяет прежний `parse_response`; удалить старые тесты `test_parse_response_refusals`, `test_parse_response_schema_violation`, покрытые новыми)

```python
from typing import Literal

# Дизайн производителя §3.2: вид фиксирует retryable и код выхода. Своя
# таблица, а не чтение схемы: код выхода против вида сверяем сами.
ERROR_KINDS: dict[str, tuple[bool, int]] = {
    **dict.fromkeys(
        (
            "request-invalid", "owner-repo-mismatch", "product-sha-absent",
            "clone-failed", "environment-sync-failed", "unsupported-runtime",
            "collection-config-outside-checkout", "collection-failed", "timeout",
        ),
        (True, 2),
    ),
    **dict.fromkeys(
        (
            "lock-not-current", "environment-selection-invalid", "collection-error",
            "collection-mutated-checkout", "product-roots-undeclared",
            "product-roots-empty", "product-roots-invalid", "product-roots-no-python",
            "product-roots-overlap-tests", "definition-unresolved", "selector-absent",
            "distributed-execution",
        ),
        (False, 3),
    ),
}


@dataclass(frozen=True)
class Parsed:
    response: dict
    branch: Literal["answer", "error"]
    retryable: bool


def parse_response(
    code: int, stdout: str, schema: dict | None
) -> tuple[Parsed | None, str | None]:
    """Первая линия отказа §5.3: код, JSON, схема, ветка против кода выхода."""
    import json

    import jsonschema

    if code not in (0, 2, 3):
        return None, f"код выхода {code} вне 0/2/3"
    if schema is None:
        return None, "схема ответа не вендорена"
    if not stdout.strip():
        return None, "ответ пуст"
    try:
        resp = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"ответ не JSON: {exc}"
    try:
        jsonschema.validate(resp, schema)
    except jsonschema.ValidationError as exc:
        return None, f"ответ не по схеме: {exc.message}"
    if "not_applicable" in resp:
        return None, "not_applicable в v1 не выпускается (дизайн §4)"
    if "error" in resp:
        if code == 0:
            return None, "код выхода 0 при ошибке"
        kind = resp["error"]["kind"]
        retry, want = ERROR_KINDS[kind]
        if code != want:
            return None, f"вид {kind} требует код {want}, получен {code}"
        return Parsed(resp, "error", retry), None
    if code != 0:
        return None, f"код выхода {code} при ответе"
    return Parsed(resp, "answer", False), None
```

- [ ] **Step 4: Прогнать — зелёные**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_check.py -q`
Expected: новые PASS; старые тесты `validate_response`, опирающиеся на прежнюю форму, пока зелёные (переписываются в Task 6).

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_check.py tests/test_governance_criteria_check.py
git commit -m "criteria_check: v1 error-kind table, response branch vs exit code (0/2/3)"
```

---

### Task 3: `criteria_product` — конфиг продукта, корни, окружение и `content_sha256` на `product_sha`

**Files:**
- Create: `governance/criteria_product.py`
- Test: `tests/test_governance_criteria_product.py`

**Interfaces:**
- Produces:
  - `class ProductError(ValueError)`;
  - `@dataclass(frozen=True) class Tree: repo: Path; sha: str` с методами `blob(path) -> bytes | None`, `entries() -> dict[str, tuple[str, str]]` (путь → (mode, blob sha));
  - `read_declaration(tree: Tree) -> Declaration` где `Declaration(roots: tuple[str, ...], groups: tuple[str, ...] | None, extras: tuple[str, ...])` (нормализовано и отсортировано);
  - `resolve_roots(tree, roots) -> tuple[str, ...]` — отслеживаемые обычные `.py` под корнями, отсортировано;
  - `content_sha256(tree, decl, lock_sha256, test_files, excluded_paths) -> str` по §6.1;
  - `function_body_lines(tree, files) -> dict[str, set[int]]`.

- [ ] **Step 1: Падающие тесты на настоящем git-репо во временном каталоге**

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from governance import criteria_product as cp


def _repo(tmp_path: Path, files: dict[str, str]) -> cp.Tree:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return cp.Tree(tmp_path, sha)


CONFIG = "criteria:\n  product_roots: [pkg/, ./tool.py]\n  environment:\n    groups: [Gov_X]\n    extras: [cli]\n"


def test_declaration_is_normalised(tmp_path):
    tree = _repo(tmp_path, {"spec-runner.config.yaml": CONFIG, "pkg/a.py": "", "tool.py": ""})
    decl = cp.read_declaration(tree)
    assert decl.roots == ("pkg", "tool.py")
    assert decl.groups == ("gov-x",) and decl.extras == ("cli",)


def test_undeclared_groups_are_none_not_empty(tmp_path):
    tree = _repo(tmp_path, {"spec-runner.config.yaml": "criteria:\n  product_roots: [pkg]\n",
                            "pkg/a.py": ""})
    decl = cp.read_declaration(tree)
    assert decl.groups is None and decl.extras == ()


@pytest.mark.parametrize("roots", ["[/abs]", "[a/../b]", "[pkg, pkg/]", "[]"])
def test_bad_declarations_refused(tmp_path, roots):
    tree = _repo(tmp_path, {"spec-runner.config.yaml": f"criteria:\n  product_roots: {roots}\n",
                            "pkg/a.py": ""})
    with pytest.raises(cp.ProductError):
        cp.read_declaration(tree)


def test_resolve_roots_tracked_py_only(tmp_path):
    tree = _repo(tmp_path, {"spec-runner.config.yaml": CONFIG, "pkg/a.py": "", "pkg/b.txt": "",
                            "pkg/sub/c.py": "", "tool.py": ""})
    assert cp.resolve_roots(tree, ("pkg", "tool.py")) == ("pkg/a.py", "pkg/sub/c.py", "tool.py")


def test_content_sha256_matches_design_6_1(tmp_path):
    files = {"spec-runner.config.yaml": CONFIG, "pkg/a.py": "x = 1\n", "tool.py": "",
             "pyproject.toml": "[project]\nname='p'\n", "tests/test_a.py": "def test_a(): pass\n",
             "tests/skip/test_s.py": "def test_s(): pass\n"}
    tree = _repo(tmp_path, files)
    decl = cp.read_declaration(tree)
    got = cp.content_sha256(tree, decl, "c" * 64, ["tests/test_a.py"], ["tests/skip"])
    paths = sorted({"pkg/a.py", "tool.py", "tests/test_a.py", "pyproject.toml",
                    "tests/skip/test_s.py"}, key=lambda p: p.encode())
    obj = {"v": 1, "product_roots": ["pkg", "tool.py"], "lock": "c" * 64,
           "environment": {"groups": ["gov-x"], "extras": ["cli"]},
           "files": [[p, hashlib.sha256(files[p].encode()).hexdigest()] for p in paths]}
    want = hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()
    assert got == want


def test_function_body_lines(tmp_path):
    src = "X = 1\n\ndef f():\n    a = 1\n    return a\n"
    tree = _repo(tmp_path, {"pkg/a.py": src})
    assert cp.function_body_lines(tree, ["pkg/a.py"]) == {"pkg/a.py": {4, 5}}
```

- [ ] **Step 2: Прогнать — падают** (`ModuleNotFoundError: governance.criteria_product`).

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_product.py -q`

- [ ] **Step 3: Реализация**

```python
"""criteria_product — байты продукта на product_sha (контракт v1 §3.3, §3.4, §6.1).

devtools не принимает на веру корни, окружение и дайджест ответа: читает
конфиг продукта тем же правилом, что производитель, из git-объектов
`product_sha`, и пересчитывает сам (devtools#491, spec-runner#623).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILES = ("spec-runner.config.yaml", "spec/executor.config.yaml")
_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ProductError(ValueError):
    """Конфиг продукта на product_sha неприемлем — ответ так не мог быть верным."""


@dataclass(frozen=True)
class Tree:
    repo: Path
    sha: str

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, check=False)

    def blob(self, path: str) -> bytes | None:
        proc = self._git("show", f"{self.sha}:{path}")
        return proc.stdout if proc.returncode == 0 else None

    def entries(self) -> dict[str, tuple[str, str]]:
        proc = self._git("ls-tree", "-r", "-z", self.sha)
        if proc.returncode != 0:
            raise ProductError(f"ls-tree {self.sha[:12]}: {proc.stderr.decode().strip()}")
        out: dict[str, tuple[str, str]] = {}
        for raw in proc.stdout.split(b"\0"):
            if not raw:
                continue
            meta, _, rel = raw.partition(b"\t")
            mode, _kind, obj = meta.decode().split()
            out[rel.decode("utf-8", errors="surrogateescape")] = (mode, obj)
        return out


@dataclass(frozen=True)
class Declaration:
    roots: tuple[str, ...]
    groups: tuple[str, ...] | None
    extras: tuple[str, ...]


def _norm_name(raw: object) -> str:
    if not isinstance(raw, str) or not _NAME.match(raw):
        raise ProductError(f"имя группы/extra {raw!r} не по PEP 735/685")
    return re.sub(r"[-_.]+", "-", raw).lower()


def _names(raw: object, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ProductError(f"criteria.environment.{what} — не список")
    names = [_norm_name(n) for n in raw]
    if len(set(names)) != len(names):
        raise ProductError(f"criteria.environment.{what}: дубль после нормализации")
    return tuple(sorted(names))


def _norm_root(raw: object) -> str:
    if not isinstance(raw, str) or not raw or raw.startswith("/"):
        raise ProductError(f"product_root {raw!r}: пусто или абсолютный путь")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ProductError(f"product_root {raw!r}: вне репо")
    return "/".join(parts)


def read_declaration(tree: Tree) -> Declaration:
    for rel in CONFIG_FILES:
        raw = tree.blob(rel)
        if raw is not None:
            break
    else:
        raise ProductError("нет конфига spec-runner на product_sha")
    data = yaml.safe_load(raw.decode("utf-8")) or {}
    data = data.get("executor", data) if isinstance(data, dict) else {}
    crit = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(crit, dict) or not isinstance(crit.get("product_roots"), list):
        raise ProductError("criteria.product_roots не объявлен")
    roots = [_norm_root(r) for r in crit["product_roots"]]
    if not roots:
        raise ProductError("criteria.product_roots пуст")
    if len(set(roots)) != len(roots):
        raise ProductError("criteria.product_roots: дубль после нормализации")
    env = crit.get("environment") or {}
    if not isinstance(env, dict):
        raise ProductError("criteria.environment — не мэппинг")
    groups = _names(env["groups"], "groups") if "groups" in env else None
    extras = _names(env.get("extras", []), "extras")
    return Declaration(tuple(sorted(roots)), groups, extras)


def resolve_roots(tree: Tree, roots: tuple[str, ...]) -> tuple[str, ...]:
    entries = tree.entries()
    out: set[str] = set()
    for root in roots:
        hits = [p for p in entries if p == root or p.startswith(root + "/")]
        if not hits:
            raise ProductError(f"product_root {root!r} нет на product_sha")
        for p in hits:
            mode = entries[p][0]
            if mode == "120000":
                raise ProductError(f"product_root {root!r}: симлинк {p}")
            if mode.startswith("100") and p.endswith(".py"):
                out.add(p)
    return tuple(sorted(out))


def _under(entries: dict[str, tuple[str, str]], path: str) -> set[str]:
    return {p for p in entries
            if (p == path or p.startswith(path.rstrip("/") + "/")) and p.endswith(".py")}


def content_sha256(
    tree: Tree,
    decl: Declaration,
    lock_sha256: str,
    test_files: list[str],
    excluded_paths: list[str],
) -> str:
    """§6.1: файлы = корни ∪ test_files ∪ pyproject.toml ∪ .py под skipped/ignored."""
    entries = tree.entries()
    files = set(resolve_roots(tree, decl.roots)) | set(test_files) | {"pyproject.toml"}
    for path in excluded_paths:
        files |= _under(entries, path)
    rows = []
    for path in sorted(files, key=lambda p: p.encode()):
        data = tree.blob(path)
        if data is None:
            raise ProductError(f"{path} нет на product_sha")
        rows.append([path, hashlib.sha256(data).hexdigest()])
    obj = {
        "v": 1,
        "product_roots": list(decl.roots),
        "lock": lock_sha256,
        "environment": {
            "groups": list(decl.groups) if decl.groups is not None else None,
            "extras": list(decl.extras),
        },
        "files": rows,
    }
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def function_body_lines(tree: Tree, files: list[str]) -> dict[str, set[int]]:
    """Строки тел функций/методов продукта (G0 rev 8) по байтам product_sha."""
    out: dict[str, set[int]] = {}
    for path in files:
        data = tree.blob(path)
        if data is None:
            continue
        try:
            parsed = ast.parse(data)
        except SyntaxError:
            out[path] = set()
            continue
        lines: set[int] = set()
        for node in ast.walk(parsed):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                lines.update(range(node.body[0].lineno, (node.end_lineno or 0) + 1))
        out[path] = lines
    return out
```

- [ ] **Step 4: Прогнать — зелёные**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_product.py -q`
Expected: PASS. Затем `uv run --frozen --group=selfcheck ruff check governance/criteria_product.py` — чисто; `pyrefly check governance/criteria_product.py` — 0 ошибок.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_product.py tests/test_governance_criteria_product.py
git commit -m "criteria_product: product config, roots, environment, content_sha256 at product_sha"
```

---

### Task 4: Пересчёт статусов §3.7 по прогонам

**Files:**
- Modify: `governance/criteria_check.py` (новые `run_outcome`, `selector_status`, `beh_status`)
- Test: `tests/test_governance_criteria_check.py`

**Interfaces:**
- Consumes: `function_lines: dict[str, set[int]]` (Task 3 `function_body_lines`).
- Produces:
  - `run_outcome(phases: dict[str, str]) -> str` — `passed|failed|skipped`;
  - `selector_status(sel: dict, function_lines) -> tuple[str, str | None]`;
  - `beh_status(selector_results: list[tuple[str, str | None]]) -> tuple[str, str | None]`.

- [ ] **Step 1: Падающие тесты**

```python
from governance import criteria_check as ch

LINES = {"pkg/mod.py": {3, 4}}


def run(**over):
    base = {"result": "complete", "collected": ["t::a"],
            "phases": {"setup": "passed", "call": "passed", "teardown": "passed"},
            "outcome": "passed", "product_lines": [{"file": "pkg/mod.py", "lines": [3, 4]}],
            "product_line_count": 2, "process_operations": []}
    return {**base, **over}


def sel(*runs):
    return {"node_id": "t::a", "runs": list(runs)}


@pytest.mark.parametrize(("phases", "want"), [
    ({"setup": "passed", "call": "passed", "teardown": "passed"}, "passed"),
    ({"setup": "passed", "call": "passed", "teardown": "failed"}, "failed"),
    ({"setup": "passed", "call": "passed", "teardown": "skipped"}, "skipped"),
    ({"setup": "skipped", "call": "not-reached", "teardown": "passed"}, "skipped"),
])
def test_run_outcome_over_three_phases(phases, want):
    assert ch.run_outcome(phases) == want


def test_both_runs_traced():
    assert ch.selector_status(sel(run(), run()), LINES) == ("traced", None)


def test_empty_second_run_is_not_traced():
    empty = run(product_lines=[], product_line_count=0)
    assert ch.selector_status(sel(run(), empty), LINES) == ("unconfirmed", "no-product-execution")


def test_lines_outside_function_bodies_do_not_qualify():
    header = run(product_lines=[{"file": "pkg/mod.py", "lines": [1]}], product_line_count=1)
    assert ch.selector_status(sel(header, header), LINES)[1] == "no-product-execution"


def test_subprocess_only():
    child = run(product_lines=[], product_line_count=0, process_operations=["subprocess.Popen"])
    assert ch.selector_status(sel(child, child), LINES) == ("unconfirmed", "subprocess-only")


def test_error_run_wins():
    err = {"result": "error", "reason": "io", "detail": "x"}
    assert ch.selector_status(sel(run(), err), LINES) == ("error", "io")


def test_nondeterministic_vs_not_passed():
    failed = run(phases={"setup": "passed", "call": "failed", "teardown": "passed"}, outcome="failed")
    assert ch.selector_status(sel(run(), failed), LINES) == ("unconfirmed", "nondeterministic")
    assert ch.selector_status(sel(failed, failed), LINES) == ("unconfirmed", "not-passed")


def test_teardown_skipped_never_traced():
    t = run(phases={"setup": "passed", "call": "passed", "teardown": "skipped"}, outcome="skipped")
    assert ch.selector_status(sel(t, t), LINES) == ("unconfirmed", "not-passed")


def test_beh_precedence():
    assert ch.beh_status([]) == ("unconfirmed", "no-test")
    assert ch.beh_status([("traced", None), ("traced", None)]) == ("traced", None)
    assert ch.beh_status([("traced", None), ("error", "io")]) == ("error", "io")
    assert ch.beh_status([("unconfirmed", "subprocess-only"),
                          ("unconfirmed", "not-passed")]) == ("unconfirmed", "not-passed")
```

- [ ] **Step 2: Прогнать — падают** (`AttributeError: run_outcome`).

- [ ] **Step 3: Реализация**

```python
_PRECEDENCE = ("not-passed", "nondeterministic", "no-product-execution", "subprocess-only")


def run_outcome(phases: dict[str, str]) -> str:
    """§3.7: passed — только если все три фазы passed."""
    values = [phases["setup"], phases["call"], phases["teardown"]]
    if all(v == "passed" for v in values):
        return "passed"
    return "failed" if "failed" in values else "skipped"


def _qualifying(run: dict, function_lines: dict[str, set[int]]) -> int:
    return sum(
        1
        for block in run["product_lines"]
        for line in block["lines"]
        if line in function_lines.get(block["file"], set())
    )


def selector_status(sel: dict, function_lines: dict[str, set[int]]) -> tuple[str, str | None]:
    """§3.7 по порядку; оба прогона обязаны пройти каждую проверку."""
    runs = sel["runs"]
    for r in runs:
        if r["result"] == "error":
            return "error", r["reason"]
    outcomes = [run_outcome(r["phases"]) for r in runs]
    if any(o != "passed" for o in outcomes):
        same = runs[0]["phases"] == runs[1]["phases"]
        return "unconfirmed", "not-passed" if same else "nondeterministic"
    for r in runs:
        if _qualifying(r, function_lines) == 0:
            return "unconfirmed", (
                "subprocess-only" if r["process_operations"] else "no-product-execution"
            )
    return "traced", None


def beh_status(results: list[tuple[str, str | None]]) -> tuple[str, str | None]:
    if not results:
        return "unconfirmed", "no-test"
    if all(s == "traced" for s, _ in results):
        return "traced", None
    errors = [r for s, r in results if s == "error"]
    if errors:
        return "error", errors[0]
    reasons = {r for s, r in results if s == "unconfirmed"}
    return "unconfirmed", next(p for p in _PRECEDENCE if p in reasons)
```

- [ ] **Step 4: Прогнать — зелёные.**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_check.py -q`

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_check.py tests/test_governance_criteria_check.py
git commit -m "criteria_check: recompute run/selector/BEH status per §3.7 (both runs, three phases)"
```

---

### Task 5: Полнота по `node_id` и исключённые владельцы токена

**Files:**
- Modify: `governance/criteria_check.py` (новые `owners`, `completeness_findings`, `blocked_owner_behs`)
- Test: `tests/test_governance_criteria_check.py`

**Interfaces:**
- Consumes: `criteria_tokens.owned_definitions(source) -> list[OwnedDefinition(qualname, line, tokens)]`.
- Produces:
  - `owners(test_sources: dict[str, str], code: str, beh_ids: list[str]) -> dict[str, set[tuple[str, str, int]]]` — BEH → {(file, qualname, line)};
  - `completeness_findings(response: dict, owners_map) -> list[str]` — отказы полноты (исполняются как отказ шага);
  - `excluded_owner_behs(response: dict, owners_map) -> dict[str, str]` — BEH → причина «владелец вне сбора» (такой BEH не может быть `traced`).

- [ ] **Step 1: Падающие тесты**

```python
SRC = "def test_a():\n    # ENC:BEH-01\n    assert 1\n"


def answer(test_items, beh_selectors, excluded=()):
    return {"test_items": test_items, "collection_excluded": list(excluded),
            "beh": [{"id": "ENC:BEH-01", "status": "traced", "selectors": beh_selectors}]}


def item(node, line=1, file="tests/test_a.py", qn="test_a"):
    return {"node_id": node, "definition": {"file": file, "qualname": qn, "line": line}}


def test_owners_by_file_qualname_line():
    assert ch.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"]) == {
        "BEH-01": {("tests/test_a.py", "test_a", 1)}}


def test_lost_parametrized_node_id_refused():
    o = ch.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    items = [item("tests/test_a.py::test_a[1]"), item("tests/test_a.py::test_a[2]")]
    resp = answer(items, [item("tests/test_a.py::test_a[1]")])
    assert any("test_a[2]" in f for f in ch.completeness_findings(resp, o))


def test_selector_definition_must_equal_test_item():
    o = ch.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    resp = answer([item("tests/test_a.py::test_a")], [item("tests/test_a.py::test_a", line=9)])
    assert ch.completeness_findings(resp, o)


def test_definition_from_other_branch_is_not_owner():
    """Парсер (§1.4, «последнее определение побеждает») отдаёт токен def из
    `else` (строка 6); pytest на рантайме собрал def из `if` (строка 3) —
    владелец не собран, BEH не traced."""
    src = ("import sys\nif sys.version_info >= (3,):\n    def test_a():\n        assert 1\n"
           "else:\n    def test_a():\n        # ENC:BEH-01\n        assert 1\n")
    o = ch.owners({"tests/test_a.py": src}, "ENC", ["BEH-01"])
    assert o == {"BEH-01": {("tests/test_a.py", "test_a", 6)}}
    resp = answer([item("tests/test_a.py::test_a", line=3)], [])
    assert "BEH-01" in ch.excluded_owner_behs(resp, o)
    assert ch.completeness_findings(resp, o) == []


@pytest.mark.parametrize("excluded", [
    {"how": "skipped", "path": "tests/test_a.py", "reason": "importorskip"},
    {"how": "ignored", "path": "tests"},
    {"how": "deselected", "node_id": "tests/test_a.py::test_a",
     "definition": {"file": "tests/test_a.py", "qualname": "test_a", "line": 1}},
])
def test_excluded_owner_blocks_traced(excluded):
    o = ch.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    resp = answer([], [], [excluded])
    assert "BEH-01" in ch.excluded_owner_behs(resp, o)


def test_owner_outside_testpaths_blocks_traced():
    o = ch.owners({"tests/test_a.py": SRC}, "ENC", ["BEH-01"])
    assert "BEH-01" in ch.excluded_owner_behs(answer([], []), o)
```

- [ ] **Step 2: Прогнать — падают.**

- [ ] **Step 3: Реализация**

```python
def owners(
    test_sources: dict[str, str], code: str, beh_ids: list[str]
) -> dict[str, set[tuple[str, str, int]]]:
    """BEH → определения-владельцы токена по парсеру devtools (§1.4)."""
    out: dict[str, set[tuple[str, str, int]]] = {b: set() for b in beh_ids}
    for path, src in test_sources.items():
        for d in ct.owned_definitions(src):
            for b in beh_ids:
                if f"{code}:{b}" in d.tokens:
                    out[b].add((path, d.qualname, d.line))
    return out


def _def(entry: dict) -> tuple[str, str, int]:
    d = entry["definition"]
    return d["file"], d["qualname"], d["line"]


def completeness_findings(
    response: dict, owners_map: dict[str, set[tuple[str, str, int]]]
) -> list[str]:
    """B.6: селекторы BEH = все node_id из test_items, чьё определение владеет
    токеном; определение селектора = определение его node_id в test_items."""
    items = {i["node_id"]: _def(i) for i in response["test_items"]}
    out: list[str] = []
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        want = {n for n, d in items.items() if d in owners_map.get(bid, set())}
        got = {s["node_id"] for s in b["selectors"]}
        for n in sorted(want - got):
            out.append(f"{bid}: нет селектора {n} (владелец токена собран)")
        for n in sorted(got - want):
            out.append(f"{bid}: лишний селектор {n}")
        for s in b["selectors"]:
            if s["node_id"] in items and _def(s) != items[s["node_id"]]:
                out.append(f"{bid}: определение селектора {s['node_id']} ≠ test_items")
    return out


def _covers(path: str, file: str) -> bool:
    return file == path or file.startswith(path.rstrip("/") + "/")


def excluded_owner_behs(
    response: dict, owners_map: dict[str, set[tuple[str, str, int]]]
) -> dict[str, str]:
    """spec-runner#623 п.4: владелец токена исключён из сбора или вне
    test_items (вне testpaths) — BEH не получает traced."""
    collected = {_def(i) for i in response["test_items"]}
    excluded = response["collection_excluded"]
    out: dict[str, str] = {}
    for bid, defs in owners_map.items():
        for d in sorted(defs):
            if d in collected:
                continue
            why = "владелец не собран (вне test_items и collection_excluded)"
            for e in excluded:
                if e["how"] in ("skipped", "ignored") and _covers(e["path"], d[0]):
                    why = f"владелец в исключённом ({e['how']}) {e['path']}"
                elif e["how"] == "deselected" and e["definition"] and _def(e) == d:
                    why = f"тест владельца снят с отбора ({e['node_id']})"
            out[bid] = f"{d[0]}::{d[1]}@{d[2]}: {why}"
            break
    return out
```

- [ ] **Step 4: Прогнать — зелёные.**

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_check.py tests/test_governance_criteria_check.py
git commit -m "criteria_check: node_id completeness and excluded token owners (B.6, #623 p.4)"
```

---

### Task 6: `validate_answer` — сверка ответа целиком

**Files:**
- Modify: `governance/criteria_check.py` — новая `validate_answer`; удалить `validate_response`, `roots_findings`, `_traced_selector_findings`, `expected_definitions`, `_ECHO`, `_LEVEL` и тесты на них (их проверки перешли в Tasks 3–5)
- Test: `tests/test_governance_criteria_check.py`

**Interfaces:**
- Consumes: Task 3 (`Declaration`, `resolve_roots`, `content_sha256` — вычисленные значения передаются аргументами), Task 4, Task 5.
- Produces:
  - `@dataclass(frozen=True) class Checked: problems: list[str]; beh_status: dict[str, str]; notes: dict[str, str]`;
  - `validate_answer(request, response, *, declared_roots, resolved_files, groups, extras, lock_sha, content_sha, function_lines, owners_map, installed) -> Checked`.

- [ ] **Step 1: Падающие тесты на эталоне `answer.json`**

```python
def golden_answer():
    return json.loads(golden("answer"))


def ok_args(resp):
    return dict(
        declared_roots=("pkg",), resolved_files=("pkg/__init__.py", "pkg/mod.py"),
        groups=None, extras=(), lock_sha="c" * 64, content_sha="d" * 64,
        function_lines={"pkg/mod.py": {3, 4}},
        owners_map={"BEH-01": {("tests/test_mod.py", "test_run", 4)}, "BEH-02": set()},
        installed="4.5.0",
    )


def test_golden_answer_is_valid():
    resp = golden_answer()
    got = ch.validate_answer(resp["request"], resp, **ok_args(resp))
    assert got.problems == []
    assert got.beh_status == {"BEH-01": "traced", "BEH-02": "unconfirmed"}


@pytest.mark.parametrize(("mutate", "why"), [
    (lambda r, a: r["request"].update(code="XYZ"), "эхо request"),
    (lambda r, a: a.update(declared_roots=("other",)), "product_roots.declared"),
    (lambda r, a: a.update(resolved_files=("pkg/mod.py",)), "product_roots.files"),
    (lambda r, a: a.update(groups=("test",)), "environment.groups"),
    (lambda r, a: a.update(lock_sha="e" * 64), "lock_sha256"),
    (lambda r, a: a.update(content_sha="e" * 64), "content_sha256"),
    (lambda r, a: a.update(installed="4.4.0"), "spec_runner_version"),
    (lambda r, a: r["beh"][0]["selectors"][0]["runs"][1].update(outcome="failed"), "outcome"),
    (lambda r, a: r["beh"][0].update(status="unconfirmed", reason="not-passed"), "статус"),
    (lambda r, a: r["beh"][0]["selectors"][0]["runs"][0].update(product_line_count=5), "product_line_count"),
])
def test_answer_refusals(mutate, why):
    resp = golden_answer()
    args = ok_args(resp)
    mutate(resp, args)
    got = ch.validate_answer(ok_request(), resp, **args)
    assert any(why in p for p in got.problems), got.problems


def ok_request():
    return golden_answer()["request"]


def test_excluded_owner_downgrades_traced():
    resp = golden_answer()
    args = ok_args(resp)
    args["owners_map"]["BEH-01"].add(("tests/test_mod.py", "test_gone", 9))
    got = ch.validate_answer(ok_request(), resp, **args)
    assert got.problems == [] and got.beh_status["BEH-01"] == "unconfirmed"
    assert "BEH-01" in got.notes
```

- [ ] **Step 2: Прогнать — падают.**

- [ ] **Step 3: Реализация**

```python
@dataclass(frozen=True)
class Checked:
    problems: list[str]
    beh_status: dict[str, str]
    notes: dict[str, str] = field(default_factory=dict)


def validate_answer(
    request: dict,
    response: dict,
    *,
    declared_roots: tuple[str, ...],
    resolved_files: tuple[str, ...],
    groups: tuple[str, ...] | None,
    extras: tuple[str, ...],
    lock_sha: str,
    content_sha: str,
    function_lines: dict[str, set[int]],
    owners_map: dict[str, set[tuple[str, str, int]]],
    installed: str | None,
) -> Checked:
    """§5.3: ответ-вердикт сверяется по байтам devtools; статусы — свои."""
    out: list[str] = []
    if response["request"] != request:
        out.append("эхо request не совпало с запросом")
    if response["spec_runner_version"] != installed:
        out.append(f"spec_runner_version {response['spec_runner_version']} ≠ {installed}")
    roots, env = response["product_roots"], response["environment"]
    if tuple(roots["declared"]) != declared_roots:
        out.append("product_roots.declared ≠ декларации на product_sha")
    if tuple(roots["files"]) != resolved_files:
        out.append("product_roots.files ≠ развёртке корней на product_sha")
    want_groups = list(groups) if groups is not None else None
    if env["groups"] != want_groups or env["extras"] != list(extras):
        out.append("environment.groups/extras ≠ конфигу продукта")
    if env["lock_sha256"] != lock_sha:
        out.append("lock_sha256 ≠ uv.lock на product_sha")
    if response["content_sha256"] != content_sha:
        out.append("content_sha256 ≠ пересчёту §6.1")
    want = sorted(c["id"] for c in request["test_criteria"])
    got = sorted(b["id"] for b in response["beh"])
    if got != want:
        return Checked([*out, f"множество BEH {got} ≠ запросу {want}"], {})
    out += completeness_findings(response, owners_map)
    status: dict[str, str] = {}
    for b in response["beh"]:
        bid = b["id"].split(":", 1)[1]
        results = []
        for s in b["selectors"]:
            for i, r in enumerate(s["runs"]):
                if r["result"] != "complete":
                    continue
                if run_outcome(r["phases"]) != r["outcome"]:
                    out.append(f"{bid}: outcome прогона {i + 1} ≠ фазам ({s['node_id']})")
                if sum(len(p["lines"]) for p in r["product_lines"]) != r["product_line_count"]:
                    out.append(f"{bid}: product_line_count ≠ строкам ({s['node_id']})")
            mine = selector_status(s, function_lines)
            if mine != (s["status"], s.get("reason")):
                out.append(f"{bid}: статус селектора {s['node_id']} ≠ пересчёту {mine}")
            results.append(mine)
        mine_beh = beh_status(results)
        if mine_beh != (b["status"], b.get("reason")):
            out.append(f"{bid}: статус BEH ≠ пересчёту {mine_beh}")
        status[bid] = mine_beh[0]
    notes = excluded_owner_behs(response, owners_map)
    for bid in notes:
        if status.get(bid) == "traced":
            status[bid] = "unconfirmed"
    return Checked(out, status, notes)
```

- [ ] **Step 4: Прогнать весь файл тестов** — новые зелёные, удалённые проверки прежней формы убраны; `test_orphan_*`, `test_must_*`, `test_graph_errors_block` (функция `outcome`) не тронуты и зелёные.

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_check.py -q`

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_check.py tests/test_governance_criteria_check.py
git commit -m "criteria_check: validate_answer against v1 — echo, roots, env, lock, content, own statuses"
```

---

### Task 7: `criteria_close` — запрос, ветвление 0/2/3, ключ G6 по ответу

**Files:**
- Modify: `governance/criteria_close.py` (`_measure`; удалить `_py_files`, `_content_sha`, `_is_test_file`, `_function_lines`, `_key`, если больше не используются)
- Test: `tests/test_governance_criteria_close.py`

**Interfaces:**
- Consumes: Task 2 `parse_response`/`Parsed`/`ERROR_KINDS`; Task 3 `Tree`, `read_declaration`, `resolve_roots`, `content_sha256`, `function_body_lines`, `ProductError`; Task 5 `owners`; Task 6 `validate_answer`/`Checked`.
- Produces: поведение `_measure`:
  - запрос с `owner_repo = state.repo_slug`;
  - exit 2 (`Parsed.retryable`) → печать вида и `detail`, возврат 2, **ключ не записывается**;
  - exit 3 → закрытие `blocked` с видом ошибки, ключ `_tree_key` (как сейчас);
  - ответ → `validate_answer`; отказ → 2; иначе `outcome(graph, checked.beh_status)`, строки `notes` — в `report_rows`; ключ `f"{bundle_pin}:v1:{content_sha256}"` (C.4: по ответу);
  - пред-проверка G6: для сохранённого входа прошлого ответа (`decl`, `test_files`, `excluded_paths`, `lock`) пересчитать `content_sha256` на новом `product_sha`; совпало с измеренным ключом → 6 без вызова spec-runner.

- [ ] **Step 1: Падающие тесты** (к существующим фикстурам `test_governance_criteria_close.py`: `_env`, `_ops`, `_response`; `_response` переписать под форму v1 на основе эталона `answer.json` с подстановкой реальных `product_sha`, `bundle_pin`, корней и дайджеста, посчитанного `criteria_product`)

```python
def test_request_carries_owner_slug(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    cc.run("run-1", ops)
    sent = json.loads((cc.STATE_ROOT / "run-1" / "request.json").read_text())
    assert sent["owner_repo"] == "owner/alpha"


def test_retryable_error_is_step_failure_without_key(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {"protocol": 1, "spec_runner_version": "4.5.0", "request": _request(target, pin),
           "error": {"kind": "clone-failed", "retryable": True, "detail": "x"}}
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2
    assert cc._load("run-1")["measured"] == {}
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2  # повтор не упирается в G6


def test_blocking_error_publishes_blocked(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {"protocol": 1, "spec_runner_version": "4.5.0", "request": _request(target, pin),
           "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"}}
    assert cc.run("run-1", _ops((3, json.dumps(err)))) == 0
    meta, _ = split_frontmatter(_closure_on_origin(target, _ops()))
    assert meta["closure"] == "blocked"


def test_exit_code_contradicting_kind_is_refused(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    err = {"protocol": 1, "spec_runner_version": "4.5.0", "request": _request(target, pin),
           "error": {"kind": "lock-not-current", "retryable": False, "detail": "stale"}}
    assert cc.run("run-1", _ops((2, json.dumps(err)))) == 2


def test_same_content_second_measure_is_g6(tmp_path, monkeypatch):
    _, target, pin = _env(tmp_path, monkeypatch)
    _oracle_on(monkeypatch)
    ops = _ops((0, json.dumps(_response(target, pin))))
    assert cc.run("run-1", ops) == 0
    calls = sum(c[0] == "criteria_verify" for c in ops.calls)
    assert cc.run("run-1", ops) == 6
    assert sum(c[0] == "criteria_verify" for c in ops.calls) == calls  # G6 без вызова
```

Хелпер запроса — тот же, что строит `_measure`, чтобы эхо совпадало:

```python
def _request(target, pin):
    state = run_state.load("run-1")
    return {"protocol": 1, "owner_repo": state.repo_slug, "workstream": state.ws_id,
            "code": "ENC", "bundle_pin": pin,
            "product_sha": pin,  # в фикстуре _env product_sha == pin
            "test_criteria": [{"id": "ENC:BEH-01", "verify_task": False}]}
```

(Существующий `_oracle_on` поправить: `task_bridge.spec_runner_version` → `"4.5.0"` — в эталоне `spec_runner_version: "4.5.0"`, а `validate_answer` сверяет его с установленной; `read_min_version` → `MinVersion("4.5.0")`. `_response` переписать под v1: взять `answer.json`, подставить `request = _request(target, pin)`, корни `pkg` фикстуры, `lock_sha256` от `uv.lock` фикстуры и `content_sha256`, посчитанный `criteria_product.content_sha256` на `Tree(target, pin)`.)

- [ ] **Step 2: Прогнать — падают.**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_close.py -q`

- [ ] **Step 3: Реализация** — ядро нового `_measure` после построения `request` (с `"owner_repo": state.repo_slug`):

```python
    code, out = ops.criteria_verify(state.target_dir, str(req_path))
    parsed, why = criteria_check.parse_response(code, out, _schema())
    if parsed is None:
        print(f"criteria-close: отказ шага — {why}")
        return 2
    resp = parsed.response
    if parsed.branch == "error" and parsed.retryable:
        e = resp["error"]
        print(f"criteria-close: повторяемый отказ spec-runner — {e['kind']}: {e['detail']}")
        return 2  # ключ не пишется: повтор — не второе измерение (C.4)
    if parsed.branch == "error":
        result: object = criteria_check.Outcome(
            "blocked", {}, {}, [f"ошибка ответа: {resp['error']['kind']}: {resp['error']['detail']}"], []
        )
        closure, key, roots = "blocked", _tree_key(bundle_pin, root, product_sha), None
    else:
        tree = criteria_product.Tree(root, product_sha)
        try:
            decl = criteria_product.read_declaration(tree)
            files = criteria_product.resolve_roots(tree, decl.roots)
            lock = tree.blob("uv.lock")
            excluded = [e["path"] for e in resp["collection_excluded"] if e["how"] != "deselected"]
            lock_sha = hashlib.sha256(lock).hexdigest() if lock is not None else ""
            content = criteria_product.content_sha256(
                tree, decl, lock_sha, resp["test_files"], excluded
            )
        except criteria_product.ProductError as exc:
            print(f"criteria-close: ответ spec-runner отвергнут — {exc}")
            return 2
        sources = {
            p: (tree.blob(p) or b"").decode("utf-8", errors="replace")
            for p in resp["test_files"] if p.endswith(".py")
        }
        checked = criteria_check.validate_answer(
            request, resp,
            declared_roots=decl.roots, resolved_files=files,
            groups=decl.groups, extras=decl.extras,
            lock_sha=lock_sha, content_sha=content,
            function_lines=criteria_product.function_body_lines(tree, list(files)),
            owners_map=criteria_check.owners(sources, charter.code, tests),
            installed=installed,
        )
        if checked.problems:
            print("criteria-close: ответ spec-runner отвергнут:\n  " + "\n  ".join(checked.problems))
            return 2
        result = criteria_check.outcome(graph, checked.beh_status)
        result.report_rows.extend(f"{b}: {n}" for b, n in sorted(checked.notes.items()))
        closure, key, roots = result.closure, f"{bundle_pin}:v1:{content}", list(decl.roots)
        data = _load(run_id)
        data["inputs"] = {"test_files": resp["test_files"], "excluded": excluded}
        _save(run_id, data)
```

Пред-проверка G6 (заменяет кандидатов `_key(...)` до вызова):

```python
    cands = [_tree_key(bundle_pin, root, product_sha)]
    inputs = _load(run_id).get("inputs")
    if inputs:
        tree = criteria_product.Tree(root, product_sha)
        try:
            decl = criteria_product.read_declaration(tree)
            lock = tree.blob("uv.lock")
            lock_sha = hashlib.sha256(lock).hexdigest() if lock is not None else ""
            cands.insert(0, f"{bundle_pin}:v1:" + criteria_product.content_sha256(
                tree, decl, lock_sha, inputs["test_files"], inputs["excluded"]))
        except criteria_product.ProductError:
            pass  # вход прошлого ответа неприменим — измеряем заново
```

Остальное (`render_closure`, `_known`, `_publish`, запись `measured`) — без изменений; `render_closure(..., product_roots=roots)`.

- [ ] **Step 4: Прогнать файл и весь набор**

Run: `uv run --frozen --group governance pytest tests/test_governance_criteria_close.py tests/test_governance_criteria_check.py tests/test_governance_criteria_product.py tests/test_governance_criteria_contract.py -q`
Expected: PASS.

Run (оба режима CI): `uv run --frozen pytest -q` и `GOVERNANCE_REQUIRED=1 uv run --frozen --group governance pytest -q`
Expected: зелёные; `ruff check .`, `ruff format --check .`, `pyrefly check governance/criteria_*.py` — чисто.

- [ ] **Step 5: Commit**

```bash
git add governance/criteria_close.py tests/test_governance_criteria_close.py
git commit -m "criteria_close: v1 request slug, exit 0/2/3 branches, G6 key by answer (C.4)"
```

---

### Task 8: Спека, TODO и README — состояние после среза

**Files:**
- Modify: `docs/superpowers/specs/2026-09-28-bundle-criteria-oracle-design.md` (§3.1 G6 — граница C.3), `TODO.md` (пункт `@id:criteria-closure-v1-signoff`), `contracts/criteria-closure/v1/README.md`

- [ ] **Step 0 (C.3):** в §3.1 спеки (G6) дописать именованную границу: «Не-`.py` данные тестов (JSON-фикстуры и т.п.) в `content_sha256` не входят (контракт v1 §6.1): правка такого файла не меняет ключ и не покупает перемер; обратная сторона — тест, чей исход решают такие данные, меряется по ключу без них. Ключ G6 до измерения devtools строит сам по входу прошлого ответа (C.4); ответ-ошибка — ключ по дереву `product_sha` (`*.py` + конфиги измерения, #523).»

- [ ] **Step 1:** в `TODO.md` отметить пункт `criteria-closure-v1-signoff` `[x]` с итогом «потребитель под схемы v1 @ 3a1b9aa (B2a); оракул `pending` до релиза с `verify --criteria`», снять `@blocked_by:spec-runner#623` (закрыт), оставить `@blocked_by:spec-runner#603` только у пункта живой приёмки `bundle-oracle-slice1`; добавить пункт «выставить `MIN_SPEC_RUNNER_VERSION` и ре-вендорить `min-spec-runner.env` производителя с релизом B2b» с `@blocked_by:spec-runner#603`.
- [ ] **Step 2:** `uv run --frozen python check-plan-fields.py` (или `make plan-check`) — без новых ошибок.
- [ ] **Step 3: Commit**

```bash
git add TODO.md contracts/criteria-closure/v1/README.md
git commit -m "todo: criteria-closure v1 consumer done; oracle pending until spec-runner verify --criteria"
```
