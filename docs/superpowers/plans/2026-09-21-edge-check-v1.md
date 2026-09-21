# edge-check v1 — одна проверка: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** инструмент `edge-check`, который проверяет один узел бандла против его
объявленных оснований по объявленным правилам и выдаёт структурированный
результат с вычисленными хэшами всего явно переданного входа.

**Architecture:** новый пакет `governance/edge_check/` из шести модулей с
единственной точкой входа `run_check()`; каталог правил — YAML в
`contracts/edge-check/v1/`; ревьюер зовётся одним подпроцессом `claude` без
инструментов, вход передаётся в запросе; корневой скрипт `edge_check.py` и цель
`make edge-check` дают операторский вызов. Координатор бандла и публикующий
адаптер — отдельные планы (срезы 2 и 3), этот срез от них не зависит.

**Tech Stack:** Python 3.12 (stdlib + `pyyaml`, уже в пинах), pytest,
`claude` CLI как ревьюер, `uv run --frozen`.

**Spec:** `docs/superpowers/specs/2026-09-21-edge-check-design.md`

## Global Constraints

- Пункт плана: `TODO.md` `@id:bundle-edge-check`, эпик `eco.dark-factory`.
- Тесты: `uv run --frozen pytest -q` (testpaths `tests`, `pythonpath = ["."]`).
- Внешние зависимости не добавляются: только stdlib и `pyyaml` (уже в пинах).
  Новых пакетов в `pyproject.toml` этот срез не вводит.
- Модель вызывается ОДНИМ подпроцессом; в тестах подпроцесс не запускается,
  кроме единственного opt-in smoke (`DEVTOOLS_EDGE_SMOKE=1`).
- Изоляция ревьюера задаётся argv и рабочим каталогом; «на слово CLI» ничего не
  принимается — argv проверяется юнит-тестом, поведение — smoke-тестом.
- Инструмент документы НЕ правит и в сеть, кроме транспорта вызова модели, не
  ходит.
- Метаданные результата (`inputs`, хэши, `absence`, `rules`, `context`,
  `reviewer`) пишет инструмент. Модель возвращает только `criteria` и
  `findings`.
- Усечение входа запрещено: превышенный потолок — `ERROR` с числом.
- Состояния результата ровно четыре: `PASS`, `FAIL`, `N/A`, `ERROR`. `PENDING`
  в этом срезе не существует — это состояние координатора (срез 2).
- Коды выхода CLI: `0` — `PASS` или `N/A`; `1` — `FAIL`; `2` —
  конфигурация/аргументы; `3` — `ERROR`.
- Имена полей результата — дословно из спеки §4.3; переименования требуют
  правки спеки, а не плана.

---

### Task 1: Каталог правил и его identity

**Files:**
- Create: `contracts/edge-check/v1/rules/behaviour-vs-requirements.yaml`
- Create: `contracts/edge-check/v1/instruction.md`
- Create: `contracts/edge-check/v1/README.md`
- Create: `governance/edge_check/__init__.py`
- Create: `governance/edge_check/rules.py`
- Test: `tests/test_edge_check_rules.py`

**Interfaces:**
- Consumes: ничего (первая задача).
- Produces: `RuleItem(id: str, text: str)`;
  `SeverityPolicy(blocking: frozenset[str], advisory: frozenset[str])`;
  `ApplicabilityRule(id: str, role: str)`;
  `RuleSet(edge_id: str, subject_role: str, basis_roles: tuple[str, ...],
  instruction: str, items: tuple[RuleItem, ...], severity: SeverityPolicy,
  applicability: tuple[ApplicabilityRule, ...], identity: str)`;
  `load_rules(edge_id: str, contracts_dir: Path) -> RuleSet`;
  `EdgeCheckError(Exception)` с полем `code: str`.

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_rules.py
from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def test_identity_changes_when_items_are_reordered(tmp_path: Path) -> None:
    src = (CONTRACTS / "rules/behaviour-vs-requirements.yaml").read_text(
        encoding="utf-8"
    )
    first = r.load_rules("behaviour-vs-requirements", CONTRACTS)

    swapped_dir = tmp_path / "rules"
    swapped_dir.mkdir(parents=True)
    lines = src.splitlines(keepends=True)
    r1 = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- id: R1"))
    r2 = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- id: R2"))
    lines[r1], lines[r2] = lines[r2], lines[r1]
    (swapped_dir / "behaviour-vs-requirements.yaml").write_text(
        "".join(lines), encoding="utf-8"
    )
    (tmp_path / "instruction.md").write_text(
        (CONTRACTS / "instruction.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    second = r.load_rules("behaviour-vs-requirements", tmp_path)

    assert first.identity != second.identity


def test_unknown_edge_is_config_error() -> None:
    with pytest.raises(r.EdgeCheckError) as exc:
        r.load_rules("no-such-edge", CONTRACTS)
    assert exc.value.code == "unknown_edge"
    assert "no-such-edge" in str(exc.value)
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: governance.edge_check`

- [ ] **Step 3: Написать каталог правил**

```yaml
# contracts/edge-check/v1/rules/behaviour-vs-requirements.yaml
edge: behaviour-vs-requirements
subject_role: behaviour-spec
basis_roles: [requirements]
items:
  - id: R1
    text: >-
      Каждое Must/Should-требование получает реализацию по существу: сценарий,
      обоснованное обязательство или waiver с названной причиной.
  - id: R2
    text: >-
      Поведение не вводит необоснованных обязательств и не противоречит
      требованиям; уточнение поведения само по себе допустимо.
  - id: R3
    text: >-
      Сценарий наблюдаем снаружи: не описывает внутреннее устройство.
  - id: R4
    text: >-
      Формулировка сценария допускает единственное прочтение.
severity:
  blocking: [blocker, major]
  advisory: [minor]
applicability: []
```

```markdown
<!-- contracts/edge-check/v1/README.md -->
# edge-check v1 — каталог правил

Один файл на сочетание «тип узла + типы оснований». Порядок `items` — часть
identity: перестановка делает прежние результаты неприменимыми.

`severity` объявляет классы находок; отнесение находки к классу — суждение
модели, вычисление итога — механика инструмента.

`applicability` перечисляет основания, отсутствие которых разрешено, и id
правила, которым оно разрешено. Пустой список означает: все основания
обязательны, и отсутствие любого — `ERROR`, а не `N/A`.

Сочетание без файла правил — ошибка конфигурации (`unknown_edge`), не
молчаливый пропуск проверки.
```

- [ ] **Step 4: Написать инструкцию ревьюеру**

```markdown
<!-- contracts/edge-check/v1/instruction.md -->
Ты проверяешь один документ против его оснований. Тебе передано всё, что
можно использовать: других источников нет, и обращаться к ним нельзя.

Для каждого правила верни статус `pass` или `fail` и причину одной фразой.
Находку оформляй со ссылкой на файл и строки ИЗ ПЕРЕДАННОГО ВХОДА и с классом
из объявленного перечня. Итоговый вердикт не выводи: его вычисляет инструмент.

Если переданного не хватает, чтобы судить по правилу, — статус `fail` с
причиной «входа недостаточно», а не догадка.
```

- [ ] **Step 5: Написать модуль правил**

```python
# governance/edge_check/rules.py
"""Каталог правил edge-check и его identity (спека §5).

Identity — хэш упорядоченного набора: перестановка пунктов обязана менять её,
иначе результаты, снятые по прежней редакции, молча остались бы действующими.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


class EdgeCheckError(Exception):
    """Отказ с машинным кодом; код — часть контракта результата."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuleItem:
    id: str
    text: str


@dataclass(frozen=True)
class SeverityPolicy:
    blocking: frozenset[str]
    advisory: frozenset[str]

    def known(self) -> frozenset[str]:
        return self.blocking | self.advisory


@dataclass(frozen=True)
class ApplicabilityRule:
    id: str
    role: str


@dataclass(frozen=True)
class RuleSet:
    edge_id: str
    subject_role: str
    basis_roles: tuple[str, ...]
    instruction: str
    items: tuple[RuleItem, ...]
    severity: SeverityPolicy
    applicability: tuple[ApplicabilityRule, ...]
    identity: str


def load_rules(edge_id: str, contracts_dir: Path) -> RuleSet:
    """Прочитать набор правил ребра; неизвестное ребро — `unknown_edge`."""
    path = contracts_dir / "rules" / f"{edge_id}.yaml"
    if not path.is_file():
        raise EdgeCheckError(
            "unknown_edge", f"нет набора правил для ребра {edge_id!r}: {path}"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    instruction = (contracts_dir / "instruction.md").read_text(encoding="utf-8")
    items = tuple(
        RuleItem(str(it["id"]), str(it["text"]).strip())
        for it in doc.get("items", [])
    )
    if not items:
        raise EdgeCheckError("unknown_edge", f"{path}: пустой items")
    severity = SeverityPolicy(
        frozenset(doc.get("severity", {}).get("blocking", [])),
        frozenset(doc.get("severity", {}).get("advisory", [])),
    )
    applicability = tuple(
        ApplicabilityRule(str(a["id"]), str(a["role"]))
        for a in doc.get("applicability", [])
    )
    canon = json.dumps(
        {
            "edge": edge_id,
            "instruction": instruction,
            "items": [[i.id, i.text] for i in items],
            "severity": {
                "blocking": sorted(severity.blocking),
                "advisory": sorted(severity.advisory),
            },
            "applicability": [[a.id, a.role] for a in applicability],
        },
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return RuleSet(
        edge_id=edge_id,
        subject_role=str(doc.get("subject_role", "")),
        basis_roles=tuple(str(x) for x in doc.get("basis_roles", [])),
        instruction=instruction,
        items=items,
        severity=severity,
        applicability=applicability,
        identity=identity,
    )
```

```python
# governance/edge_check/__init__.py
"""edge-check — смысловая проверка узла бандла против его оснований."""

from governance.edge_check.rules import EdgeCheckError, RuleSet, load_rules

__all__ = ["EdgeCheckError", "RuleSet", "load_rules"]
```

- [ ] **Step 6: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_rules.py -q`
Expected: PASS (2 теста)

- [ ] **Step 7: Коммит**

```bash
git add contracts/edge-check governance/edge_check tests/test_edge_check_rules.py
git commit -m "feat(edge-check): каталог правил ребра и его identity"
```

---

### Task 2: Подготовка входа, хэши и применимость

**Files:**
- Create: `governance/edge_check/inputs.py`
- Test: `tests/test_edge_check_inputs.py`

**Interfaces:**
- Consumes: `RuleSet`, `EdgeCheckError` из Task 1.
- Produces: `InputFile(role: str, path: str, sha256: str, size: int, text: str)`;
  `Absence(path: str, rule_id: str)`;
  `PreparedInput(files: tuple[InputFile, ...], absences: tuple[Absence, ...],
  applicable: bool)`;
  `prepare_input(ruleset: RuleSet, bundle_dir: Path, subject: list[Path],
  bases: list[tuple[str, Path]]) -> PreparedInput`.

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_inputs.py
from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01 traces FR-01\n", encoding="utf-8")
    return b


def test_hash_is_computed_over_the_prepared_copy(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    prepared = i.prepare_input(
        rs, b, [b / "15-behaviour-spec.md"], [("requirements", b / "10-requirements.md")]
    )
    subject = next(f for f in prepared.files if f.role == "subject")
    # sha256 ровно того текста, что уедет в запрос
    import hashlib

    assert subject.sha256 == hashlib.sha256(subject.text.encode("utf-8")).hexdigest()
    assert subject.path == "15-behaviour-spec.md"
    assert prepared.applicable is True


def test_missing_mandatory_basis_is_error_not_na(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(
            rs, b, [b / "15-behaviour-spec.md"], [("requirements", b / "nope.md")]
        )
    assert exc.value.code == "missing_mandatory_input"


def test_symlink_input_is_refused(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    link = b / "20-design.md"
    link.symlink_to(outside)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(rs, b, [link], [("requirements", b / "10-requirements.md")])
    assert exc.value.code == "unsafe_input"


def test_unreadable_file_is_not_absence(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    bad = b / "10-requirements.md"
    bad.write_bytes(b"\xff\xfe\x00broken")
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        i.prepare_input(rs, b, [b / "15-behaviour-spec.md"], [("requirements", bad)])
    assert exc.value.code == "unreadable_input"
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_inputs.py -q`
Expected: FAIL — `ImportError: cannot import name 'inputs'`

- [ ] **Step 3: Написать модуль входа**

```python
# governance/edge_check/inputs.py
"""Подготовка объявленного входа: копии, хэши, применимость (спека §4.1, D7).

Хэш считается по ТЕКСТУ, который уедет в запрос, а не по файлу в дереве:
заявленное и прочитанное расходятся молча, вычисленное — нет.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from governance.edge_check.rules import EdgeCheckError, RuleSet


@dataclass(frozen=True)
class InputFile:
    role: str
    path: str
    sha256: str
    size: int
    text: str


@dataclass(frozen=True)
class Absence:
    path: str
    rule_id: str


@dataclass(frozen=True)
class PreparedInput:
    files: tuple[InputFile, ...]
    absences: tuple[Absence, ...]
    applicable: bool


def _read(role: str, path: Path, bundle_dir: Path) -> InputFile:
    if path.is_symlink():
        raise EdgeCheckError(
            "unsafe_input", f"{path}: ссылка, а не обычный файл — вход отклонён"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EdgeCheckError("unreadable_input", f"{path}: {exc}") from exc
    rel = str(path.resolve().relative_to(bundle_dir.resolve()))
    data = text.encode("utf-8")
    return InputFile(role, rel, hashlib.sha256(data).hexdigest(), len(data), text)


def prepare_input(
    ruleset: RuleSet,
    bundle_dir: Path,
    subject: list[Path],
    bases: list[tuple[str, Path]],
) -> PreparedInput:
    """Прочитать объявленный вход; решение о применимости — ДО вызова модели."""
    optional_roles = {a.role: a.id for a in ruleset.applicability}
    files: list[InputFile] = []
    absences: list[Absence] = []

    for path in subject:
        if not path.exists():
            raise EdgeCheckError(
                "missing_mandatory_input", f"проверяемый объект отсутствует: {path}"
            )
        files.append(_read("subject", path, bundle_dir))

    for role, path in bases:
        if path.exists():
            files.append(_read(role, path, bundle_dir))
            continue
        rule_id = optional_roles.get(role)
        if rule_id is None:
            raise EdgeCheckError(
                "missing_mandatory_input",
                f"обязательное основание {role!r} отсутствует: {path}",
            )
        rel = str((path.resolve()).relative_to(bundle_dir.resolve()))
        absences.append(Absence(rel, rule_id))

    return PreparedInput(tuple(files), tuple(absences), len(absences) == 0)
```

- [ ] **Step 4: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_inputs.py -q`
Expected: PASS (4 теста)

- [ ] **Step 5: Коммит**

```bash
git add governance/edge_check/inputs.py tests/test_edge_check_inputs.py
git commit -m "feat(edge-check): объявленный вход, вычисленные хэши, применимость"
```

---

### Task 3: Сборка запроса и контроль размера

**Files:**
- Create: `governance/edge_check/prompt.py`
- Create: `contracts/edge-check/v1/response-schema.json`
- Test: `tests/test_edge_check_prompt.py`

**Interfaces:**
- Consumes: `RuleSet` (Task 1), `PreparedInput` (Task 2).
- Produces: `Measure(unit: str, method: str, size: int, estimate_tokens: int,
  reserve_tokens: int, limit_tokens: int)`; `Prompt(text: str, measure: Measure)`;
  `build_prompt(ruleset: RuleSet, prepared: PreparedInput, *,
  limit_tokens: int = 120000, reserve_tokens: int = 8000) -> Prompt`.

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_prompt.py
from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import prompt as p
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _prepared(text: str = "BEH-01\n") -> i.PreparedInput:
    f = i.InputFile("subject", "15-behaviour-spec.md", "deadbeef", len(text), text)
    g = i.InputFile("requirements", "10-requirements.md", "cafe", 4, "FR-01\n")
    return i.PreparedInput((f, g), (), True)


def test_prompt_carries_every_rule_and_every_input() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    out = p.build_prompt(rs, _prepared())
    for item in rs.items:
        assert item.id in out.text
    assert "15-behaviour-spec.md" in out.text
    assert "BEH-01" in out.text
    assert "FR-01" in out.text


def test_measure_covers_instruction_rules_and_documents() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    small = p.build_prompt(rs, _prepared("BEH-01\n"))
    big = p.build_prompt(rs, _prepared("BEH-01\n" + "x" * 10_000))
    assert big.measure.size > small.measure.size + 9_000
    assert small.measure.method == "utf8-bytes/4"
    assert small.measure.reserve_tokens > 0


def test_oversized_input_is_error_without_truncation() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        p.build_prompt(rs, _prepared("x" * 200_000), limit_tokens=1000)
    assert exc.value.code == "input_too_large"
    assert "1000" in str(exc.value)
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_prompt.py -q`
Expected: FAIL — `ImportError: cannot import name 'prompt'`

- [ ] **Step 3: Написать схему ответа**

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["criteria", "findings"],
  "properties": {
    "criteria": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "status", "reason"],
        "properties": {
          "id": {"type": "string"},
          "status": {"enum": ["pass", "fail"]},
          "reason": {"type": "string"}
        }
      }
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["rule_id", "class", "path", "lines", "statement"],
        "properties": {
          "rule_id": {"type": "string"},
          "class": {"type": "string"},
          "path": {"type": "string"},
          "lines": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 2,
            "maxItems": 2
          },
          "statement": {"type": "string"}
        }
      }
    }
  }
}
```

- [ ] **Step 4: Написать сборщик запроса**

```python
# governance/edge_check/prompt.py
"""Сборка запроса и измерение его объёма (спека §4.1, D17).

Вход не усекается: превышенный потолок — отказ с числом. Усечение молча
меняет предмет проверки, а хэши при этом продолжают описывать полный вход.
"""

from __future__ import annotations

from dataclasses import dataclass

from governance.edge_check.inputs import PreparedInput
from governance.edge_check.rules import EdgeCheckError, RuleSet

#: Оценка токенов: utf-8 байты делим на 4. Метод объявлен в результате,
#: чтобы «поместилось/не поместилось» можно было перепроверить руками.
_BYTES_PER_TOKEN = 4


@dataclass(frozen=True)
class Measure:
    unit: str
    method: str
    size: int
    estimate_tokens: int
    reserve_tokens: int
    limit_tokens: int


@dataclass(frozen=True)
class Prompt:
    text: str
    measure: Measure


def build_prompt(
    ruleset: RuleSet,
    prepared: PreparedInput,
    *,
    limit_tokens: int = 120_000,
    reserve_tokens: int = 8_000,
) -> Prompt:
    parts = [ruleset.instruction.strip(), "", "## Правила", ""]
    for item in ruleset.items:
        parts.append(f"- {item.id}: {item.text}")
    parts += ["", "## Классы находок", ""]
    parts.append(
        "Допустимые: " + ", ".join(sorted(ruleset.severity.known()))
    )
    parts += ["", "## Вход", ""]
    for f in prepared.files:
        parts += [f"### {f.role}: {f.path}", "", "```markdown", f.text, "```", ""]
    for a in prepared.absences:
        parts.append(f"Отсутствует (разрешено правилом {a.rule_id}): {a.path}")
    text = "\n".join(parts)

    size = len(text.encode("utf-8"))
    estimate = size // _BYTES_PER_TOKEN
    if estimate + reserve_tokens > limit_tokens:
        raise EdgeCheckError(
            "input_too_large",
            f"вход {estimate} токенов + резерв {reserve_tokens} превышает "
            f"потолок {limit_tokens}; усечение запрещено",
        )
    return Prompt(
        text,
        Measure(
            unit="tokens",
            method="utf8-bytes/4",
            size=size,
            estimate_tokens=estimate,
            reserve_tokens=reserve_tokens,
            limit_tokens=limit_tokens,
        ),
    )
```

- [ ] **Step 5: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_prompt.py -q`
Expected: PASS (3 теста)

- [ ] **Step 6: Коммит**

```bash
git add governance/edge_check/prompt.py contracts/edge-check/v1/response-schema.json tests/test_edge_check_prompt.py
git commit -m "feat(edge-check): сборка запроса, измерение объёма, отказ без усечения"
```

---

### Task 4: Вызов ревьюера в изоляции

**Files:**
- Create: `governance/edge_check/reviewer.py`
- Test: `tests/test_edge_check_reviewer.py`

**Interfaces:**
- Consumes: `EdgeCheckError` (Task 1), `Prompt` (Task 3).
- Produces: `reviewer_argv(model: str, schema_path: Path, effort: str | None) ->
  list[str]`; `run_reviewer(prompt_text: str, argv: list[str], workdir: Path,
  timeout: int) -> str` (возвращает сырой stdout конверта).

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_reviewer.py
from __future__ import annotations

from pathlib import Path

import pytest

from governance.edge_check import reviewer as rv
from governance.edge_check import rules as r


def test_argv_disables_every_tool_and_every_customization() -> None:
    argv = rv.reviewer_argv("claude-opus-5", Path("contracts/edge-check/v1/response-schema.json"), None)
    assert argv[0] == "claude"
    # инструментов ноль — пустая строка, а не перечень
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    for flag in (
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
    ):
        assert flag in argv, flag
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    # промпт идёт stdin-ом: в argv его нет
    assert not any(a.startswith("Ты проверяешь") for a in argv)


def test_workdir_must_be_empty_and_outside_repo(tmp_path: Path) -> None:
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "CLAUDE.md").write_text("x", encoding="utf-8")
    with pytest.raises(r.EdgeCheckError) as exc:
        rv.run_reviewer("prompt", ["true"], busy, timeout=1)
    assert exc.value.code == "unsafe_workdir"


def test_reviewer_nonzero_exit_is_reviewer_failed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(r.EdgeCheckError) as exc:
        rv.run_reviewer("prompt", ["false"], empty, timeout=5)
    assert exc.value.code == "reviewer_failed"
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_reviewer.py -q`
Expected: FAIL — `ImportError: cannot import name 'reviewer'`

- [ ] **Step 3: Написать модуль вызова**

```python
# governance/edge_check/reviewer.py
"""Вызов ревьюера в изоляции (спека D4).

Изоляция обеспечивается конструкцией вызова, а не просьбой к модели:
`--tools ""` снимает все инструменты, `--restricted` игнорирует
пользовательские настройки, `--safe-mode` снимает CLAUDE.md, скиллы, плагины,
хуки и MCP, `--strict-mcp-config` отрезает MCP оператора. Рабочий каталог —
пустой временный каталог вне воркспейса: даже если дискавери инструкций
где-то переживёт флаги, подхватывать будет нечего.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from governance.edge_check.rules import EdgeCheckError


def reviewer_argv(model: str, schema_path: Path, effort: str | None) -> list[str]:
    argv = ["claude", "-p", "--model", model]
    if effort:
        argv += ["--effort", effort]
    return [
        *argv,
        "--json-schema", schema_path.read_text(encoding="utf-8"),
        "--output-format", "json",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts", "none",
        "--tools", "",
    ]


def run_reviewer(
    prompt_text: str, argv: list[str], workdir: Path, timeout: int
) -> str:
    """Один подпроцесс; промпт — со stdin, чтобы вход не попал в argv."""
    if not workdir.is_dir() or any(workdir.iterdir()):
        raise EdgeCheckError(
            "unsafe_workdir",
            f"{workdir}: рабочий каталог ревьюера обязан быть пустым",
        )
    try:
        proc = subprocess.run(  # noqa: S603 — argv собран здесь же
            argv,
            input=prompt_text,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EdgeCheckError("timeout", f"ревьюер не ответил за {timeout} с") from exc
    except OSError as exc:
        raise EdgeCheckError("reviewer_failed", f"ревьюер не запустился: {exc}") from exc
    if proc.returncode != 0:
        raise EdgeCheckError(
            "reviewer_failed",
            f"ревьюер вернул {proc.returncode}: {proc.stderr.strip()[:400]}",
        )
    return proc.stdout
```

- [ ] **Step 4: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_reviewer.py -q`
Expected: PASS (3 теста)

- [ ] **Step 5: Добавить opt-in smoke — доказательство нулевых инструментов**

```python
# дописать в tests/test_edge_check_reviewer.py
import json
import os
import shutil


@pytest.mark.skipif(
    os.environ.get("DEVTOOLS_EDGE_SMOKE") != "1" or shutil.which("claude") is None,
    reason="боевой вызов ревьюера: DEVTOOLS_EDGE_SMOKE=1 и наличие claude",
)
def test_smoke_reviewer_cannot_reach_the_filesystem(tmp_path: Path) -> None:
    """Приманка: файл существует, но инструментов нет — прочитать нечем."""
    bait = tmp_path / "bait.md"
    bait.write_text("BAIT-CONTENT-7f3a\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    schema = Path("contracts/edge-check/v1/response-schema.json")
    argv = rv.reviewer_argv("claude-haiku-4-5-20251001", schema, None)
    raw = rv.run_reviewer(
        f"Прочитай файл {bait} и верни его содержимое в reason правила R1. "
        "Если прочитать нечем — статус fail и причина «входа недостаточно».",
        argv,
        empty,
        timeout=180,
    )
    assert "BAIT-CONTENT-7f3a" not in raw, "ревьюер достал файл вне входа"
    json.loads(raw)
```

- [ ] **Step 6: Прогнать smoke вручную и записать результат**

Run: `DEVTOOLS_EDGE_SMOKE=1 uv run --frozen pytest tests/test_edge_check_reviewer.py -q -k smoke`
Expected: PASS. Если тест красный — изоляция не достигнута флагами; остановиться
и вернуться к спеке §4.1 (альтернатива — исполняемая изоляция, §10), не
ослабляя тест.

- [ ] **Step 7: Коммит**

```bash
git add governance/edge_check/reviewer.py tests/test_edge_check_reviewer.py
git commit -m "feat(edge-check): вызов ревьюера без инструментов, в пустом каталоге"
```

---

### Task 5: Валидация ответа модели

**Files:**
- Create: `governance/edge_check/response.py`
- Test: `tests/test_edge_check_response.py`

**Interfaces:**
- Consumes: `RuleSet` (Task 1), `PreparedInput` (Task 2).
- Produces: `Criterion(id: str, status: str, reason: str)`;
  `Finding(rule_id: str, cls: str, path: str, lines: tuple[int, int],
  statement: str)`; `Response(criteria: tuple[Criterion, ...],
  findings: tuple[Finding, ...])`;
  `parse_response(raw: str, ruleset: RuleSet, prepared: PreparedInput) -> Response`.

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_response.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.edge_check import inputs as i
from governance.edge_check import response as resp
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _prepared() -> i.PreparedInput:
    text = "BEH-01\nBEH-02\nBEH-03\n"
    f = i.InputFile("subject", "15-behaviour-spec.md", "aa", len(text), text)
    return i.PreparedInput((f,), (), True)


def _envelope(criteria, findings) -> str:
    return json.dumps(
        {"structured_output": {"criteria": criteria, "findings": findings}}
    )


def _all_pass(rs: r.RuleSet) -> list[dict]:
    return [{"id": it.id, "status": "pass", "reason": "ок"} for it in rs.items]


def test_complete_answer_parses() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    out = resp.parse_response(_envelope(_all_pass(rs), []), rs, _prepared())
    assert [c.id for c in out.criteria] == [it.id for it in rs.items]


def test_missing_criterion_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    partial = _all_pass(rs)[:-1]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(partial, []), rs, _prepared())
    assert exc.value.code == "criteria_incomplete"


def test_finding_outside_input_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "major", "path": "20-design.md",
            "lines": [1, 2], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "finding_outside_input"


def test_line_range_beyond_file_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "major", "path": "15-behaviour-spec.md",
            "lines": [1, 99], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_line_range"


def test_unknown_finding_class_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    bad = [{"rule_id": "R1", "class": "catastrophic", "path": "15-behaviour-spec.md",
            "lines": [1, 1], "statement": "x"}]
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response(_envelope(_all_pass(rs), bad), rs, _prepared())
    assert exc.value.code == "invalid_finding_class"


def test_non_json_answer_is_error() -> None:
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    with pytest.raises(r.EdgeCheckError) as exc:
        resp.parse_response("не json", rs, _prepared())
    assert exc.value.code == "invalid_response"
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_response.py -q`
Expected: FAIL — `ImportError: cannot import name 'response'`

- [ ] **Step 3: Написать валидатор**

```python
# governance/edge_check/response.py
"""Разбор и валидация ответа модели (спека §4.2, D5).

Инструмент не верит ответу: отсутствующий пункт, ссылка вне входа, номер
строки за пределами файла и неизвестный класс находки — `ERROR`, а не
молчаливое сужение проверки.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from governance.edge_check.inputs import PreparedInput
from governance.edge_check.rules import EdgeCheckError, RuleSet


@dataclass(frozen=True)
class Criterion:
    id: str
    status: str
    reason: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    cls: str
    path: str
    lines: tuple[int, int]
    statement: str


@dataclass(frozen=True)
class Response:
    criteria: tuple[Criterion, ...]
    findings: tuple[Finding, ...]


def parse_response(
    raw: str, ruleset: RuleSet, prepared: PreparedInput
) -> Response:
    try:
        envelope = json.loads(raw)
        payload = envelope.get("structured_output", envelope)
        criteria_raw = payload["criteria"]
        findings_raw = payload["findings"]
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError) as exc:
        raise EdgeCheckError("invalid_response", f"ответ негоден: {exc}") from exc

    criteria = tuple(
        Criterion(str(c["id"]), str(c["status"]), str(c.get("reason", "")))
        for c in criteria_raw
    )
    declared = [it.id for it in ruleset.items]
    got = [c.id for c in criteria]
    missing = [rid for rid in declared if rid not in got]
    if missing:
        raise EdgeCheckError(
            "criteria_incomplete",
            f"модель не ответила по пунктам: {', '.join(missing)}",
        )

    lines_by_path = {f.path: f.text.count("\n") + 1 for f in prepared.files}
    findings: list[Finding] = []
    for f in findings_raw:
        path = str(f["path"])
        if path not in lines_by_path:
            raise EdgeCheckError(
                "finding_outside_input", f"находка ссылается вне входа: {path}"
            )
        cls = str(f["class"])
        if cls not in ruleset.severity.known():
            raise EdgeCheckError(
                "invalid_finding_class", f"класс находки {cls!r} не объявлен"
            )
        start, end = (int(f["lines"][0]), int(f["lines"][1]))
        if not 1 <= start <= end <= lines_by_path[path]:
            raise EdgeCheckError(
                "invalid_line_range",
                f"{path}: строки {start}–{end} вне файла "
                f"({lines_by_path[path]} строк)",
            )
        findings.append(
            Finding(str(f["rule_id"]), cls, path, (start, end), str(f["statement"]))
        )
    return Response(criteria, tuple(findings))
```

- [ ] **Step 4: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_response.py -q`
Expected: PASS (6 тестов)

- [ ] **Step 5: Коммит**

```bash
git add governance/edge_check/response.py tests/test_edge_check_response.py
git commit -m "feat(edge-check): валидация ответа модели без молчаливых послаблений"
```

---

### Task 6: Вердикт по политике и запись результата

**Files:**
- Create: `governance/edge_check/check.py`
- Modify: `governance/edge_check/__init__.py`
- Test: `tests/test_edge_check_verdict.py`

**Interfaces:**
- Consumes: всё из Task 1–5.
- Produces: `decide(ruleset: RuleSet, response: Response) -> str`;
  `run_check(edge_id, bundle_dir, subject, bases, *, contracts_dir, model,
  effort=None, timeout=600, call=None) -> dict` — запись результата по §4.3.
  Параметр `call` — инъекция вызова ревьюера для тестов:
  `Callable[[str], str]`, по умолчанию боевой `run_reviewer`.

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_verdict.py
from __future__ import annotations

import json
from pathlib import Path

from governance.edge_check import check as c
from governance.edge_check import rules as r

CONTRACTS = Path("contracts/edge-check/v1")


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01 traces FR-01\n", encoding="utf-8")
    return b


def _answer(rs: r.RuleSet, findings: list[dict]) -> str:
    return json.dumps(
        {
            "structured_output": {
                "criteria": [
                    {"id": it.id, "status": "pass", "reason": "ок"} for it in rs.items
                ],
                "findings": findings,
            }
        }
    )


def _run(tmp_path: Path, findings: list[dict]) -> dict:
    b = _bundle(tmp_path)
    rs = r.load_rules("behaviour-vs-requirements", CONTRACTS)
    return c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=lambda prompt: _answer(rs, findings),
    )


def test_blocking_finding_gives_fail(tmp_path: Path) -> None:
    out = _run(tmp_path, [{"rule_id": "R2", "class": "major",
                           "path": "15-behaviour-spec.md", "lines": [1, 1],
                           "statement": "вводит обязательство сверх требований"}])
    assert out["verdict"] == "FAIL"


def test_advisory_only_gives_pass_and_keeps_findings(tmp_path: Path) -> None:
    out = _run(tmp_path, [{"rule_id": "R4", "class": "minor",
                           "path": "15-behaviour-spec.md", "lines": [1, 1],
                           "statement": "формулировка двусмысленна"}])
    assert out["verdict"] == "PASS"
    assert len(out["findings"]) == 1


def test_result_carries_computed_hashes_and_both_identities(tmp_path: Path) -> None:
    out = _run(tmp_path, [])
    assert out["schema_version"] == 1
    assert {f["role"] for f in out["subject"]} == {"subject"}
    assert out["bases"][0]["role"] == "requirements"
    assert len(out["subject"][0]["sha256"]) == 64
    assert len(out["check_identity"]) == 64
    assert out["context"]["method"] == "utf8-bytes/4"
    assert out["reviewer"]["model"] == "claude-opus-5"
    assert out["attempt_id"] and out["started_at"] and out["finished_at"]


def test_reviewer_failure_becomes_error_with_code(tmp_path: Path) -> None:
    b = _bundle(tmp_path)

    def boom(prompt: str) -> str:
        raise r.EdgeCheckError("timeout", "ревьюер не ответил за 600 с")

    out = c.run_check(
        "behaviour-vs-requirements",
        b,
        [b / "15-behaviour-spec.md"],
        [("requirements", b / "10-requirements.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=boom,
    )
    assert out["verdict"] == "ERROR"
    assert out["error_code"] == "timeout"
    assert out["reason"]


def test_absent_optional_basis_gives_na_without_calling_the_model(
    tmp_path: Path,
) -> None:
    b = _bundle(tmp_path)
    disco = b / "00-discovery"
    disco.mkdir()
    (disco / "brief.md").write_text("IF-01 интерфейс\n", encoding="utf-8")
    called: list[str] = []

    out = c.run_check(
        "engineer-brief-vs-customer-brief",
        b,
        [disco / "brief.md"],
        [("customer-brief", disco / "discovery-brief-customer.md")],
        contracts_dir=CONTRACTS,
        model="claude-opus-5",
        call=lambda prompt: called.append(prompt) or "{}",
    )
    assert out["verdict"] == "N/A"
    assert out["absence"][0]["rule_id"] == "A1"
    assert called == [], "при N/A модель не зовётся"
```

- [ ] **Step 2: Добавить второй набор правил — с разрешённым отсутствием**

```yaml
# contracts/edge-check/v1/rules/engineer-brief-vs-customer-brief.yaml
edge: engineer-brief-vs-customer-brief
subject_role: engineer-brief
basis_roles: [customer-brief]
items:
  - id: R1
    text: >-
      Каждое Must-требование брифа заказчика получает вердикт выполнимости по
      существу, а не только упоминание идентификатора.
  - id: R2
    text: >-
      Названные ограничения и интерфейсы не противоречат целям заказчика.
severity:
  blocking: [blocker, major]
  advisory: [minor]
applicability:
  - id: A1
    role: customer-brief
```

- [ ] **Step 3: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_verdict.py -q`
Expected: FAIL — `ImportError: cannot import name 'check'`

- [ ] **Step 4: Написать оркестратор и вердикт**

```python
# governance/edge_check/check.py
"""Одна проверка целиком: вход → запрос → ревьюер → валидация → запись.

Вердикт вычисляется механически по объявленной политике серьёзности: модель
классифицирует находку, итог выводит инструмент (спека §4.2, §5.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from governance.edge_check import inputs as inputs_mod
from governance.edge_check import prompt as prompt_mod
from governance.edge_check import response as response_mod
from governance.edge_check import reviewer as reviewer_mod
from governance.edge_check import rules as rules_mod
from governance.edge_check.rules import EdgeCheckError

SCHEMA_VERSION = 1


def decide(ruleset: rules_mod.RuleSet, response: response_mod.Response) -> str:
    """`FAIL` при блокирующей находке или проваленном пункте, иначе `PASS`."""
    if any(f.cls in ruleset.severity.blocking for f in response.findings):
        return "FAIL"
    if any(c.status != "pass" for c in response.criteria):
        return "FAIL"
    return "PASS"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_check(
    edge_id: str,
    bundle_dir: Path,
    subject: list[Path],
    bases: list[tuple[str, Path]],
    *,
    contracts_dir: Path,
    model: str,
    effort: str | None = None,
    timeout: int = 600,
    call: Callable[[str], str] | None = None,
) -> dict:
    started = _now()
    attempt_id = uuid.uuid4().hex
    ruleset = rules_mod.load_rules(edge_id, contracts_dir)

    record: dict = {
        "schema_version": SCHEMA_VERSION,
        "edge": edge_id,
        "attempt_id": attempt_id,
        "started_at": started,
        "check_identity": ruleset.identity,
        "rules": {
            "id": edge_id,
            "items": [[i.id, i.text] for i in ruleset.items],
        },
        "reviewer": {"harness": "claude", "model": model, "effort": effort},
        "subject": [],
        "bases": [],
        "absence": [],
        "criteria": [],
        "findings": [],
    }

    def finish(verdict: str, *, code: str = "", reason: str = "") -> dict:
        record["verdict"] = verdict
        record["finished_at"] = _now()
        if code:
            record["error_code"] = code
        if reason:
            record["reason"] = reason
        return record

    try:
        prepared = inputs_mod.prepare_input(ruleset, bundle_dir, subject, bases)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))

    record["subject"] = [
        {"role": f.role, "path": f.path, "sha256": f.sha256, "size": f.size}
        for f in prepared.files
        if f.role == "subject"
    ]
    record["bases"] = [
        {"role": f.role, "path": f.path, "sha256": f.sha256, "size": f.size}
        for f in prepared.files
        if f.role != "subject"
    ]
    record["absence"] = [{"path": a.path, "rule_id": a.rule_id} for a in prepared.absences]

    if not prepared.applicable:
        ids = ", ".join(a.rule_id for a in prepared.absences)
        return finish(
            "N/A", reason=f"основание отсутствует, разрешено правилом {ids}"
        )

    try:
        built = prompt_mod.build_prompt(ruleset, prepared)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))

    record["context"] = {
        "unit": built.measure.unit,
        "method": built.measure.method,
        "size": built.measure.size,
        "estimate_tokens": built.measure.estimate_tokens,
        "reserve_tokens": built.measure.reserve_tokens,
        "limit_tokens": built.measure.limit_tokens,
    }

    if call is None:
        schema = contracts_dir / "response-schema.json"
        argv = reviewer_mod.reviewer_argv(model, schema, effort)

        def call_real(text: str) -> str:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="edge-check-") as tmp:
                return reviewer_mod.run_reviewer(text, argv, Path(tmp), timeout)

        call = call_real

    try:
        raw = call(built.text)
        parsed = response_mod.parse_response(raw, ruleset, prepared)
    except EdgeCheckError as exc:
        return finish("ERROR", code=exc.code, reason=str(exc))

    record["criteria"] = [
        {"id": c.id, "status": c.status, "reason": c.reason} for c in parsed.criteria
    ]
    record["findings"] = [
        {
            "rule_id": f.rule_id,
            "class": f.cls,
            "location": {"path": f.path, "lines": list(f.lines)},
            "statement": f.statement,
        }
        for f in parsed.findings
    ]
    return finish(decide(ruleset, parsed))
```

```python
# governance/edge_check/__init__.py — дописать
from governance.edge_check.check import SCHEMA_VERSION, decide, run_check

__all__ = [
    "EdgeCheckError",
    "RuleSet",
    "SCHEMA_VERSION",
    "decide",
    "load_rules",
    "run_check",
]
```

- [ ] **Step 5: Прогнать весь набор**

Run: `uv run --frozen pytest tests/test_edge_check_verdict.py -q`
Expected: PASS (5 тестов)

- [ ] **Step 6: Коммит**

```bash
git add governance/edge_check tests/test_edge_check_verdict.py contracts/edge-check/v1/rules
git commit -m "feat(edge-check): вердикт по политике и запись результата"
```

---

### Task 7: Операторский вызов и документация

**Files:**
- Create: `edge_check.py`
- Modify: `Makefile:19` (список `.PHONY`), конец файла — новая цель
- Modify: `CLAUDE.md` (таблица «Инструменты»)
- Modify: `TODO.md` (пункт `@id:bundle-edge-check` — отметка о срезе 1)
- Test: `tests/test_edge_check_cli.py`

**Interfaces:**
- Consumes: `run_check` (Task 6).
- Produces: CLI `edge_check.py --edge <id> --bundle <dir> --subject <path>
  --basis <role>=<path> [--out <file>] [--model <m>] [--effort <e>]`;
  коды выхода `0` (`PASS`/`N/A`), `1` (`FAIL`), `2` (аргументы/конфигурация),
  `3` (`ERROR`).

- [ ] **Step 1: Написать красный тест**

```python
# tests/test_edge_check_cli.py
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "spec"
    b.mkdir(parents=True)
    (b / "10-requirements.md").write_text("FR-01 Must\n", encoding="utf-8")
    (b / "15-behaviour-spec.md").write_text("BEH-01\n", encoding="utf-8")
    return b


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "edge_check.py"), *args],
        capture_output=True, text=True, cwd=REPO,
    )


def test_bad_basis_syntax_is_exit_2(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "behaviour-vs-requirements", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", "requirements-without-equals"])
    assert got.returncode == 2
    assert "role=path" in got.stderr


def test_missing_mandatory_input_is_exit_3_and_prints_record(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "behaviour-vs-requirements", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", f"requirements={b / 'nope.md'}"])
    assert got.returncode == 3
    record = json.loads(got.stdout)
    assert record["verdict"] == "ERROR"
    assert record["error_code"] == "missing_mandatory_input"


def test_unknown_edge_is_exit_2(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    got = _run(["--edge", "no-such-edge", "--bundle", str(b),
                "--subject", str(b / "15-behaviour-spec.md"),
                "--basis", f"requirements={b / '10-requirements.md'}"])
    assert got.returncode == 2
    assert "no-such-edge" in got.stderr
```

- [ ] **Step 2: Прогнать и убедиться, что красный**

Run: `uv run --frozen pytest tests/test_edge_check_cli.py -q`
Expected: FAIL — `can't open file 'edge_check.py'`

- [ ] **Step 3: Написать CLI**

```python
#!/usr/bin/env python3
"""edge-check — смысловая проверка узла бандла против его оснований.

Срез 1 плана `docs/superpowers/plans/2026-09-21-edge-check-v1.md`: одна
проверка, вызываемая оператором. Состав рёбер, инвалидация и публикация —
срезы 2 и 3, здесь их нет.

Коды выхода: 0 — PASS или N/A; 1 — FAIL; 2 — аргументы/конфигурация;
3 — ERROR (проверка не завершена).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from governance.edge_check import run_check
from governance.edge_check.rules import EdgeCheckError

_EXIT = {"PASS": 0, "N/A": 0, "FAIL": 1, "ERROR": 3}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--subject", required=True, action="append", type=Path)
    parser.add_argument("--basis", required=True, action="append")
    parser.add_argument("--contracts", type=Path,
                        default=Path(__file__).parent / "contracts/edge-check/v1")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    bases: list[tuple[str, Path]] = []
    for raw in args.basis:
        role, sep, path = raw.partition("=")
        if not sep or not role or not path:
            print(f"--basis ожидает role=path, получено {raw!r}", file=sys.stderr)
            return 2
        bases.append((role, Path(path)))

    try:
        record = run_check(
            args.edge, args.bundle, args.subject, bases,
            contracts_dir=args.contracts, model=args.model,
            effort=args.effort, timeout=args.timeout,
        )
    except EdgeCheckError as exc:  # только конфигурация: неизвестное ребро
        print(f"edge-check: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return _EXIT[record["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Прогнать и убедиться, что зелёный**

Run: `uv run --frozen pytest tests/test_edge_check_cli.py -q`
Expected: PASS (3 теста)

- [ ] **Step 5: Добавить цель Makefile**

В `Makefile`: добавить `edge-check` в список `.PHONY` (строка 19) и цель:

```make
edge-check:  ; @python3 ./edge_check.py $(ARGS)
```

- [ ] **Step 6: Дописать строку в таблицу «Инструменты» CLAUDE.md**

```markdown
| `edge_check.py` | смысловая проверка узла бандла против его оснований (`make edge-check ARGS='--edge <id> --bundle <dir> --subject <p> --basis <role>=<p>'`): вход объявлен и передаётся в запросе, ревьюер без инструментов в пустом каталоге, результат несёт вычисленные хэши всего входа и identity правил. Каталог правил — `contracts/edge-check/v1/`. Срез 1 плана `2026-09-21-edge-check-v1.md`; координатор бандла и публикация — срезы 2 и 3 |
```

- [ ] **Step 7: Отметить срез в TODO**

Под пунктом `@id:bundle-edge-check` дописать дословно:

```
      Срез 1 доставлен PR этой ветки: одна проверка, вызываемая оператором
      (`make edge-check`), каталог правил `contracts/edge-check/v1/`,
      изоляция подтверждена opt-in smoke (`DEVTOOLS_EDGE_SMOKE=1`). Чего ещё
      нет: состава рёбер из профиля, ключа результата и инвалидации,
      готовности цепочки, леджера, публикации на PR — срезы 2 и 3 плана
      `docs/superpowers/plans/2026-09-21-edge-check-v1.md`.
```

- [ ] **Step 8: Прогнать всё и закоммитить**

Run: `uv run --frozen pytest -q`
Expected: PASS, включая шесть новых файлов тестов и весь прежний набор.

```bash
git add edge_check.py Makefile CLAUDE.md TODO.md tests/test_edge_check_cli.py
git commit -m "feat(edge-check): операторский вызов, цель make, документация"
```

---

## Что этот срез НЕ делает

Осознанно вне объёма — это следующие планы, и их отсутствие не дефект:

- **состав рёбер из профиля** (`upstream` артефактов) — срез 2: здесь ребро
  называет оператор флагом `--edge`;
- **ключ результата, попытки, инвалидация, готовность цепочки, леджер** —
  срез 2;
- **сверка с SHA, маркер, публикация review, коды раннера, область замены
  аттестации** — срез 3;
- **проверка реализации** («тесты и код ← задача») — ждёт адресуемых критериев
  (`@id:bundle-docs-as-oracle`);
- **интеграция с disputatio** — заявка соседу после приёмки v1;
- **поле `profile`** записи результата (спека §4.3) — его пишет координатор,
  которому профиль известен; в срезе 1 ребро называет оператор, и поля в
  записи нет;
- **размеченный корпус и пороги качества** (спека §9.2) — отдельная работа;
  критерий готовности инструмента включает их, и срез 1 его не закрывает.
