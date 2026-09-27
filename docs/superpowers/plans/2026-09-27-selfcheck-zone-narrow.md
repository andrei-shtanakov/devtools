# selfcheck zone-narrow — план реализации (spec rev 5.8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** зона неразрешённого запуска без литерального суффикса перестаёт
освобождать от dead и даёт потолок P7. Ручные инструменты объявляются в
`[[operator]]` и становятся корнями. Пункт TODO — `selfcheck-zone-narrow`.

**Architecture:** точечные правки S1 и S2:
- `Zone.suffix`;
- `NodeFacts.dir_zone` и потолок P7 в `classify`;
- `Config.operator`;
- `RepoTarget.operator`;
- `usage-graph` — корни из `operator`, находка `selfcheck/operator-missing`,
  подсказка в `suggestion` для `doc-only`.

**Tech Stack:** Python ≥3.12, pytest. Новых зависимостей нет.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md`, **rev 5.8**:
§2.3 (шаг 2, P7, D10/D10b/D17), §3.2.2, §3.2.3, §3.2.5, §4.3 (конфиг-хэш).

## Жанр

Как у планов S1 и S2:
- **Нормативны:** тест (полный код ниже), «Интерфейсы», Global Constraints,
  таблица трассировки.
- **Ненормативны:** эскизы.
- **Red-фаза прогнана** 2026-09-27: сборка падает на `NodeFacts.__init__()
  got an unexpected keyword argument 'dir_zone'`; ruff чистый.

## Global Constraints

- `USAGE_GRAPH.logic_version` = 3: логика dead меняется, ключ сопоставимости
  обязан это видеть (§4.3).
- Поведение, которое не меняется:
  - зоны с литеральным суффиксом — по-прежнему освобождение (D10);
  - находка `usage-graph/unresolved-exec` на вызывающем сохраняется для
    обоих видов зон;
  - состав зон (граф) не меняется, поэтому тесты S1 на уровне графа
    (`test_graph_resolver`, `test_graph_final_review`) остаются в силе.
- В `tests/selfcheck/test_graph_final_review.py` у хелпера `alive()`
  docstring «inside an unresolved-launch zone» больше не означает «не
  кандидат в dead». Поправить docstring — «in a zone (a suffix zone exempts,
  a caller-dir zone caps at P7)» — утверждения тестов не менять.
- Всё прочее — как в S1 и S2: абсолютные пути, `ruff`/`pyrefly` зелёные,
  без `_cowork_output`.

## Интерфейсы (нормативно)

```python
# selfcheck/graph/model.py
@dataclass(frozen=True)
class Zone:
    caller: Location
    members: frozenset[str]
    reason: str
    suffix: bool = False          # True: literal-suffix zone (exempts); False: caller dir (P7)

# selfcheck/graph/classify.py
@dataclass(frozen=True)
class NodeFacts:
    ...                           # unchanged fields, then
    dir_zone: bool = False        # → cap "P7" (after P5/P6)
def classify(..., roots: frozenset[str] = frozenset()) -> list[Finding]: ...
    # roots: paths treated as roots (never dead) — the [[operator]] list
    # in_zone = member of a suffix zone; dir_zone = member of a caller-dir zone only
    # doc-only dead: suggestion mentions "[[operator]]"

# selfcheck/config.py
@dataclass(frozen=True)
class OperatorEntry:
    path: str
    reason: str
@dataclass(frozen=True)
class Config:
    ...
    operator: tuple[OperatorEntry, ...] = ()   # [[operator]]; missing path|reason → ConfigError
    # Config.sha1 already covers the whole file

# selfcheck/probes/base.py
class RepoTarget:
    ...
    operator: tuple[str, ...] = ()
def _analyzer_config_hash(target) -> str   # material gains "operator": sorted(target.operator)

# selfcheck/graph/probe.py — USAGE_GRAPH.logic_version = 3
#   classify(..., roots=frozenset(target.operator))
#   for path in target.operator not in target.corpus:
#     Finding(rule="selfcheck/operator-missing", category="selfcheck", severity="medium",
#             confidence=CONFIRMED, owner_repo=repo, anchor="file:selfcheck.toml",
#             locations=[Location("selfcheck.toml", 1)], text_key=path)

# selfcheck/run.py — RepoTarget(..., operator=tuple(o.path for o in config.operator))
```

## Таблица трассировки

| Спека | Требование | Тест |
|---|---|---|
| §2.3 шаг 2, D10 | суффикс-зона освобождает | `test_p7_matrix[D10]`, `test_caller_dir_zone_caps_instead_of_exempting` (`kit/local.sh`) |
| §2.3 P7, D10b | зона каталога → dead `likely`, cap P7; P7 сочетается с P5 | `test_p7_matrix[D10b]`, `[P7+P5]`, `test_caller_dir_zone_caps_instead_of_exempting` |
| §3.2.3 | вид зоны записан; P7 не зависит от `fleet` (`complete` в тесте, но всё равно `likely`); `unresolved-exec` сохраняется | `test_zone_kind_is_recorded`, `test_caller_dir_zone_caps_instead_of_exempting` |
| §3.2.5, D17 | `[[operator]]` — корень | `test_operator_entry_is_a_root`, `test_run_reads_operator_from_config` |
| §3.2.5 | запись без `path`/`reason` → код 4 | `test_operator_config_parsing`, `test_run_reads_operator_from_config` |
| §3.2.5 | запись на несуществующий путь → `selfcheck/operator-missing` | `test_operator_on_a_missing_path_is_a_finding` |
| §3.2.5 | README не заменяет роль: `doc-only` остаётся dead, подсказка в `suggestion` | `test_doc_only_dead_hints_at_operator` |
| §4.3 | `[[operator]]` в конфиг-хэше; `logic_version` 3 | `test_operator_enters_the_analyzer_config_hash`, `test_usage_graph_logic_version_is_3` |
| приёмка | на devtools | Task 2 |

---

### Task 1: P7, вид зоны, `[[operator]]`

**Files:**
- Modify: `selfcheck/graph/model.py`, `selfcheck/graph/resolver.py` (`_zone`
  получает `suffix=bool(pattern)`), `selfcheck/graph/classify.py`,
  `selfcheck/config.py`, `selfcheck/probes/base.py`,
  `selfcheck/graph/probe.py`, `selfcheck/run.py`,
  `tests/selfcheck/test_graph_final_review.py` (только docstring);
- Test: `tests/selfcheck/test_zone_narrow.py`.

- [ ] **Step 1: тест:**

```python
"""zone-narrow (spec rev 5.8): caller-dir zone → cap P7; [[operator]] roots (§2.3, §3.2.3, §3.2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.config import ConfigError, load_config
from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import NodeFacts, Surface, dead_confidence
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.model import Confidence
from selfcheck.probes.base import (
    ProbeResult,
    RepoTarget,
    _analyzer_config_hash,
    canary_files,
    run_probe,
)
from selfcheck.roles import role_of
from selfcheck.run import main
from tests.selfcheck.helpers import NOW, ago, make_repo, workspace, write

FULL = Surface("complete", "/sched", [])
L = Confidence.LIKELY

RUNNER = (
    "import subprocess\n\ndef run(cfg):\n"
    "    subprocess.run([cfg.tool_path, '--json'])\n"
)
SCRIPT = 'if __name__ == "__main__":\n    pass\n'


def usage(
    tmp: Path, files: dict[str, str], operator: tuple[str, ...] = ()
) -> ProbeResult:
    repo = make_repo(tmp / "repo", files, date=ago(90))
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([USAGE_GRAPH]))
    target = RepoTarget(
        "repo",
        repo,
        copy,
        frozenset({"python"}),
        corpus,
        EnvInfo("no-env"),
        fleet="complete",
        sched_dir=tmp,
        now=NOW,
        operator=operator,
    )
    try:
        return run_probe(USAGE_GRAPH, target, tmp / "run" / "work")
    finally:
        release(copy)


def dead(res: ProbeResult) -> dict[str, object]:
    return {f.anchor: f for f in res.findings if f.rule.startswith("usage-graph/dead")}


def caps(finding) -> set[str]:
    return {e["detail"] for e in finding.evidence if e["kind"] == "cap"}


@pytest.mark.parametrize(
    ("row", "facts", "expected"),
    [
        ("D10", NodeFacts("orphan", False, True, True, 90, False), (None, [])),
        (
            "D10b",
            NodeFacts("orphan", False, False, True, 90, False, dir_zone=True),
            (L, ["P7"]),
        ),
        (
            "P7+P5",
            NodeFacts("orphan", False, False, True, 90, True, dir_zone=True),
            (L, ["P5", "P7"]),
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_p7_matrix(row, facts, expected) -> None:
    assert dead_confidence(facts, FULL) == expected


def test_zone_kind_is_recorded(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "suffix.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
            "local.sh": "#!/bin/sh\n",
            "tools/runner.py": RUNNER,
            "tools/tool.py": SCRIPT,
        },
    )
    files = ["suffix.sh", "local.sh", "tools/runner.py", "tools/tool.py"]
    g = build_graph(files, tmp_path, role_of, repo_name="r", sched_dir=None)
    kinds = {z.caller.path: z.suffix for z in g.zones}
    assert kinds == {"suffix.sh": True, "tools/runner.py": False}


def test_caller_dir_zone_caps_instead_of_exempting(tmp_path: Path) -> None:
    res = usage(
        tmp_path,
        {
            "tools/runner.py": RUNNER,
            "tools/tool.py": SCRIPT,
            "solo/alone.py": SCRIPT,
            "suffix.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
            "kit/local.sh": "#!/bin/sh\n",
        },
    )
    found = dead(res)
    tool = found["file:tools/tool.py"]
    assert tool.confidence is L and "P7" in caps(tool)  # visible, never confirmed
    assert found["file:solo/alone.py"].confidence is Confidence.CONFIRMED  # no zone
    assert "file:kit/local.sh" not in found  # suffix zone still exempts (D10)
    zone = [f for f in res.findings if f.rule == "usage-graph/unresolved-exec"]
    assert {f.anchor for f in zone} == {"file:tools/runner.py", "file:suffix.sh"}


def test_operator_entry_is_a_root(tmp_path: Path) -> None:
    files = {"tools/runner.py": RUNNER, "tools/tool.py": SCRIPT, "gen.py": SCRIPT}
    res = usage(tmp_path, files, operator=("tools/tool.py", "gen.py"))
    assert not {"file:tools/tool.py", "file:gen.py"} & set(dead(res))  # D17


def test_operator_on_a_missing_path_is_a_finding(tmp_path: Path) -> None:
    res = usage(tmp_path, {"a.py": SCRIPT}, operator=("gone.sh",))
    (missing,) = [f for f in res.findings if f.rule == "selfcheck/operator-missing"]
    assert (missing.anchor, missing.text_key, missing.severity, missing.category) == (
        "file:selfcheck.toml",
        "gone.sh",
        "medium",
        "selfcheck",
    )


def test_doc_only_dead_hints_at_operator(tmp_path: Path) -> None:
    res = usage(
        tmp_path,
        {"README.md": "| `tool.sh` | генератор |\n", "tool.sh": "#!/bin/sh\n"},
    )
    finding = dead(res)["file:tool.sh"]
    assert {"kind": "class", "detail": "doc-only"} in finding.evidence
    assert "[[operator]]" in finding.suggestion  # a hint, not an exemption


def test_operator_config_parsing(tmp_path: Path) -> None:
    good = tmp_path / "good.toml"
    good.write_text('[[operator]]\npath = "a.sh"\nreason = "ручной гейт"\n')
    config = load_config(good)
    assert [(o.path, o.reason) for o in config.operator] == [("a.sh", "ручной гейт")]
    bad = tmp_path / "bad.toml"
    bad.write_text('[[operator]]\npath = "a.sh"\n')
    with pytest.raises(ConfigError):
        load_config(bad)


def test_operator_enters_the_analyzer_config_hash(tmp_path: Path) -> None:
    def target(operator: tuple[str, ...]) -> RepoTarget:
        return RepoTarget(
            "r",
            tmp_path,
            tmp_path,
            frozenset(),
            (),
            EnvInfo("no-env"),
            operator=operator,
        )

    assert _analyzer_config_hash(target(())) != _analyzer_config_hash(target(("a.sh",)))


def test_usage_graph_logic_version_is_3() -> None:
    assert USAGE_GRAPH.logic_version == 3


def test_run_reads_operator_from_config(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "sc.toml").write_text(
        '[[operator]]\npath = "orphan.py"\nreason = "запускает человек"\n'
    )
    argv = [
        "--workspace", str(ws), "--manifest", str(ws / "m.toml"),
        "--out", str(ws / "out"), "--config", str(ws / "sc.toml"),
        "--probe", "usage-graph",
    ]  # fmt: skip
    assert main(argv) == 0
    import json

    (run,) = list((ws / "out").iterdir())
    doc = json.loads((run / "report.json").read_text())
    assert not [f for f in doc["findings"] if f["anchor"] == "file:orphan.py"]
    (ws / "bad.toml").write_text('[[operator]]\nreason = "без пути"\n')
    argv[argv.index(str(ws / "sc.toml"))] = str(ws / "bad.toml")
    assert main(argv) == 4
```

- [ ] **Step 2:** `uv run --frozen pytest tests/selfcheck/test_zone_narrow.py -q`.
  Expected: сборка падает на `dir_zone`.
- [ ] **Step 3:** реализовать по эскизу; затем весь
  `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q`,
  `ruff`, `pyrefly`.
- [ ] **Step 4:** Expected: всё зелёное. Коммит
  `feat(selfcheck): caller-dir zone caps at P7; [[operator]] roots (rev 5.8)`.

**Эскиз.**
- **`classify`.** `suffix_members` — члены зон с `suffix=True`,
  `dir_members` — члены зон с `suffix=False`. У узла `in_zone = anchor in
  suffix_members`, `dir_zone = anchor in dir_members and not in_zone`, `root
  = node.root or node.path in roots`.
- **`dead_confidence`.** Если `dir_zone` — добавить `"P7"` после `P5`/`P6`.
- **`suggestion`.** Для `doc-only`: «упомянут в документации — если это
  ручной инструмент, внесите в `[[operator]]` selfcheck.toml». Иначе прежняя.
- **`load_config`.** `data.get("operator", [])`: каждой записи нужны `path` и
  `reason` (иначе `ConfigError(f"[[operator]] #{i}: missing …")`).

### Task 2: devtools `selfcheck.toml` и приёмка

**Files:** `selfcheck.toml`, `TODO.md` (`selfcheck-zone-narrow` → `[x]`).

- [ ] **Step 1:** в `selfcheck.toml` добавить:

```toml
[[operator]]
path = "attest-vendor.sh"
reason = "гейт волн ре-вендора; запускает оператор (authority-root, #263)"

[[operator]]
path = "discover_models.py"
reason = "discovery моделей по ADR-ECO-003a; запускает оператор, README"
```

  `gen_agents_toml.py` **не** вносится (решение 2026-09-27: последняя работа
  по существу — 10.07, выход заменён каталогом ADR-ECO-003). Если он заброшен,
  это первая настоящая находка selfcheck, и прятать её ролью не надо.
- [ ] **Step 2: приёмка** — `make selfcheck ARGS="--fleet --sched-dir ~/Library/LaunchAgents"`.
  Expected:
  - `attest-vendor.sh` и `discover_models.py` без dead;
  - `gen_agents_toml.py` — dead класса `doc-only`, `likely` (P5 от runbook во
    флоте), с подсказкой `[[operator]]` в `suggestion`;
  - находки `unresolved-exec` на широких вызывающих сохранены;
  - `selfcheck/operator-missing` = 0;
  - код выхода 0.

  Результат записать в заметку приёмки.
- [ ] **Step 3:** коммит `chore(selfcheck): operator entries for devtools; zone-narrow done`.
