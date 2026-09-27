# selfcheck S5 — судья (`--judge`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make selfcheck ARGS='--all --judge'` выносит вердикты `replace|keep|unsure` над кандидатами `llm-replaceable` и `duplicate` — под потолком, в нормативном порядке, с кэшем вердиктов и видимым статусом судьи.

**Architecture:** новый модуль `selfcheck/judge.py` — чистые функции (отбор и порядок, срез, ключ кэша, argv/окружение, разбор ответа) плюс `run_judge` с подменяемыми `which`/`runner`. `run.main` зовёт его между `final` и снимком дельты; статус судьи — ещё одна `ProbeResult` в `acc.results`, поэтому код выхода и находки прибора работают без новой логики. Отчёт получает строку-сводку, раздел «Судья: оставить» и перечень не судившихся.

**Tech Stack:** Python 3.12+, pytest, `claude` CLI (подменяется в тестах исполняемым файлом в `PATH`), `concurrent.futures`.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md` rev 5.10 — §5 и §11. Замер — dev-only `../_cowork_output/devtools-selfcheck-s5-measure-2026-09-27.md`.

## Global Constraints

- Без `--judge` харнесс не вызывается ни разу; строки `judge` в таблице проб нет (§11.6).
- argv: `claude -p --output-format json --json-schema <SCHEMA> --model <M> --restricted --strict-mcp-config --no-session-persistence --permission-prompts none --tools "" --max-budget-usd 0.2`; `cwd` — пустой временный каталог; вход — только stdin (§5, §11.4).
- Окружение: `PATH`, `HOME`; `darwin` — ещё `USER`; `linux` — ещё `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, если заданы. Ничего больше (§11.4).
- Схема: поля `rationale`, `verdict` (`replace|keep|unsure`), `replacement` (`regex|rules|decision-tree|embedding-classifier|small-model|merge|none`); `rationale` первым; `additionalProperties: false` (§5).
- Таймаут 120 с, вывод ≤ 64 КиБ, до 4 вызовов параллельно, `--judge-max` 100, `--judge-model` `claude-opus-5-5` (§11.4, §11.2).
- Порядок: межрепные → внутрирепные; `likely` → `candidate`; `llm-replaceable` → `ast-dup` → `cli-overlap` → `jscpd`; в jscpd длина клона по убыванию; затем (`owner_repo`, путь, строка) (§11.2).
- Кэш `out/selfcheck/judge-cache.json`: ключ = sha256(срез) + `JUDGE_VERSION` + модель; ошибки не кэшируются; запись атомарна; битый файл — предупреждение и пустой кэш (§11.3).
- Вердикт: `replace` → уверенность не выше `likely` (кандидат поднимается до `likely`); `keep` — находка в `findings` с прежним `id`; `unsure` — без изменений; никакой вердикт не даёт `confirmed` и не меняет `id`/`owner_repo` (§5, §11.6).
- Статус судьи: `ok` / `partial` (есть ошибки) / `failed` (ошибки у всех вызовов) / `unavailable` (нет `claude`); `ProbeResult("judge", "devtools", …)` (§11.6).
- `[corpus] exclude` в `selfcheck.toml` — `.obsidian/plugins/**` (§11.2).
- `uv run`, не pip; после каждой задачи `uv run --frozen --group selfcheck pytest tests/selfcheck -q`, `ruff format`/`check`, `pyrefly check selfcheck tests/selfcheck`; строка ≤ 88.

## Review Focus

1. **Судья вызывается без `--judge`.** Ожидание: ни одного запуска харнесса (Task 6 `test_no_judge_flag_no_harness_call`).
2. **Окружение исполнителя утекает в процесс судьи** (токены, `GH_*`, `REVIEW_*`). Ожидание: ровно allowlist платформы (Task 3 `test_env_allowlist_per_platform`).
3. **Ответ модели меняет идентичность или поднимает до `confirmed`.** Ожидание: только поле `judge` и `likely` максимум (Task 5 `test_apply_never_confirmed_nor_new_id`).
4. **Кэш подаёт устаревший вердикт на изменённый код.** Ожидание: другой срез — другой ключ, новый вызов (Task 4 `test_changed_slice_misses_cache`).
5. **Ошибка судьи проходит молча.** Ожидание: `partial`/`failed`/`unavailable` в таблице проб и код 2/3 (Task 5, Task 6).

---

### Task 1: артефакты сборки вне корпуса; отбор и порядок кандидатов

**Files:**
- Modify: `selfcheck.toml` (`[corpus] exclude`)
- Create: `selfcheck/judge.py` (отбор и порядок)
- Test: `tests/selfcheck/test_judge.py` (новый)

**Interfaces:**
- Produces: `is_candidate(f: Finding) -> bool`; `clone_lines(f: Finding) -> int`; `order_key(f: Finding) -> tuple`; `JUDGE_RULES` (порядок категорий).

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_judge.py`:

```python
"""S5 — the judge (spec §5, §11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.config import load_config
from selfcheck.judge import clone_lines, is_candidate, order_key
from selfcheck.model import Confidence, Finding, Location


def _f(
    rule: str,
    conf: Confidence,
    repos: tuple[str, ...] = ("a",),
    path: str = "x.py",
    line: int = 1,
    lines: int | None = None,
) -> Finding:
    category = "llm-replaceable" if rule.startswith("llm") else "duplicate"
    related = [
        {"owner_repo": r, "path": path, "line": line + i, "member": str(i)}
        for i, r in enumerate(repos * (2 if len(repos) == 1 else 1))
    ]
    evidence = [{"kind": "lines", "detail": str(lines)}] if lines else []
    return Finding(
        rule=rule,
        category=category,
        severity="medium",
        confidence=conf,
        owner_repo=repos[0],
        anchor=f"{rule}:{path}:{line}:{lines}:{'-'.join(repos)}",
        locations=[Location(path, line)],
        related=related if category == "duplicate" else [],
        evidence=evidence,
    )


def test_candidates_are_llm_and_dups_below_confirmed() -> None:
    assert is_candidate(_f("llm-sites/replaceable", Confidence.CANDIDATE))
    assert is_candidate(_f("jscpd/clone", Confidence.LIKELY, lines=9))
    assert not is_candidate(_f("ast-dup/exact", Confidence.CONFIRMED))
    other = _f("ruff/F401", Confidence.LIKELY)
    other.category = "quality"
    assert not is_candidate(other)


def test_order_is_normative() -> None:
    fs = [
        _f("jscpd/clone", Confidence.LIKELY, lines=10),
        _f("jscpd/clone", Confidence.LIKELY, lines=40),
        _f("cli-overlap/argparse", Confidence.CANDIDATE),
        _f("ast-dup/structural", Confidence.CANDIDATE),
        _f("llm-sites/replaceable", Confidence.CANDIDATE),
        _f("ast-dup/structural", Confidence.CANDIDATE, repos=("a", "b")),
        _f("jscpd/clone", Confidence.LIKELY, repos=("a", "b"), lines=5),
    ]
    got = [(f.rule, clone_lines(f)) for f in sorted(fs, key=order_key)]
    assert got == [
        ("jscpd/clone", 5),  # cross-repo likely first
        ("ast-dup/structural", 0),  # cross-repo candidate
        ("jscpd/clone", 40),  # intra likely, longest clone first
        ("jscpd/clone", 10),
        ("llm-sites/replaceable", 0),  # intra candidate: llm → ast → cli
        ("ast-dup/structural", 0),
        ("cli-overlap/argparse", 0),
    ]


def test_obsidian_plugins_are_out_of_the_corpus() -> None:
    config = load_config(Path(__file__).parents[2] / "selfcheck.toml")
    assert ".obsidian/plugins/**" in config.corpus_exclude
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.judge'`.

- [ ] **Step 3: Implement**

`selfcheck.toml` — после `[[allow]]`:

```toml
# Артефакты сборки и вендор-бандлы — вне корпуса (спека §11.2): иначе как самые
# крупные клоны jscpd они займут потолок судьи первыми (замер S5 2026-09-27).
[corpus]
exclude = [".obsidian/plugins/**"]
```

`selfcheck/judge.py`:

```python
"""The judge: tool-less LLM verdicts over candidates (spec §5, §11)."""

from __future__ import annotations

from selfcheck.model import Confidence, Finding

JUDGE_RULES = ("llm-sites", "ast-dup", "cli-overlap", "jscpd")
_LEVEL = {Confidence.LIKELY: 0, Confidence.CANDIDATE: 1}


def is_candidate(f: Finding) -> bool:
    """``llm-replaceable`` and ``duplicate`` at candidate/likely (§5)."""
    return f.category in ("llm-replaceable", "duplicate") and f.confidence in _LEVEL


def clone_lines(f: Finding) -> int:
    """Length of a jscpd clone (0 for other rules)."""
    for e in f.evidence:
        if e.get("kind") == "lines":
            return int(e["detail"])
    return 0


def _repos(f: Finding) -> set[str]:
    return {r["owner_repo"] for r in f.related} or {f.owner_repo}


def order_key(f: Finding) -> tuple[int, int, int, int, str, str, int]:
    """Normative judge order under the cap (§11.2)."""
    prefix = f.rule.split("/", 1)[0]
    loc = f.locations[0] if f.locations else None
    return (
        0 if len(_repos(f)) > 1 else 1,
        _LEVEL.get(f.confidence, 2),
        JUDGE_RULES.index(prefix) if prefix in JUDGE_RULES else len(JUDGE_RULES),
        -clone_lines(f),
        f.owner_repo,
        loc.path if loc else "",
        loc.line if loc else 0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком (правка `[corpus]` меняет sha1 конфига — тесты, сверяющие конкретный sha1 `selfcheck.toml`, если есть, правятся в этом же шаге; ruling в ledger).

- [ ] **Step 5: Commit**

```bash
git add selfcheck.toml selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — отбор и порядок кандидатов; бандлы Obsidian вне корпуса (§11.2)"
```

---

### Task 2: срез для судьи

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `build_slice(f: Finding, sources: Mapping[str, Path]) -> JudgeSlice | None` — `None`, если файла нет; `JudgeSlice(text: str, truncated: bool)`; `MAX_SLICE = 48 * 1024`; `LLM_PROMPT`, `DUP_PROMPT`.

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import MAX_SLICE, build_slice

FUNC = "".join(f"# pad {i}\n" for i in range(50)) + (
    "def f(x):\n" + "".join(f"    y{i} = x + {i}\n" for i in range(10)) + "    return x\n"
)


def _src(tmp_path: Path, files: dict[str, str]) -> dict[str, Path]:
    for rel, text in files.items():
        (tmp_path / "a" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "a" / rel).write_text(text)
    return {"a": tmp_path / "a"}


def test_llm_slice_is_the_call_pm_40(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": FUNC})
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, line=55)
    s = build_slice(f, src)
    assert s is not None and not s.truncated
    assert "# pad 15" in s.text and "# pad 14" not in s.text  # 55-40 = line 15
    assert "a:x.py" in s.text


def test_ast_dup_slice_takes_whole_functions(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": FUNC})
    f = _f("ast-dup/structural", Confidence.CANDIDATE, line=51)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 51, "member": "f"}]
    s = build_slice(f, src)
    assert s is not None and "    return x" in s.text and "# pad 45" in s.text


def test_jscpd_slice_is_clone_pm_5(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": FUNC})
    f = _f("jscpd/clone", Confidence.LIKELY, line=20, lines=3)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 20, "member": "20"}]
    s = build_slice(f, src)
    assert s is not None and "# pad 14" in s.text and "# pad 27" in s.text
    assert "# pad 13" not in s.text and "# pad 28" not in s.text


def test_missing_file_gives_none(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="gone.py")
    assert build_slice(f, {"a": tmp_path}) is None


def test_huge_slice_is_truncated(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": "x = 1\n" * 40000})
    f = _f("jscpd/clone", Confidence.LIKELY, line=1, lines=30000)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 1, "member": "1"}]
    s = build_slice(f, src)
    assert s is not None and s.truncated and len(s.text.encode()) <= MAX_SLICE + 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k slice`
Expected: FAIL — `ImportError: cannot import name 'MAX_SLICE'`.

- [ ] **Step 3: Implement**

```python
import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from selfcheck.graph.model import parse_python

MAX_SLICE = 48 * 1024
LLM_CONTEXT = 40
DUP_CONTEXT = 5
LLM_PROMPT = (
    "You judge whether an LLM call site can be replaced by a script, rules, a "
    "decision tree, an embedding classifier or a small model. Reason first in "
    "`rationale`, then give `verdict` (replace|keep|unsure) and `replacement` "
    "(none when keep/unsure). Answer only via the JSON schema.\n"
)
DUP_PROMPT = (
    "You judge a reported duplicate. Decide whether the fragments should be merged "
    "into one shared function/module/fixture (verdict replace, replacement merge), "
    "kept as they are (keep, none) or you cannot tell (unsure, none). Consider test "
    "readability, intentional parallel structure and different evolution paths. "
    "Reason first in `rationale` (2-3 sentences). Answer only via the JSON schema.\n"
)


@dataclass(frozen=True)
class JudgeSlice:
    """What the judge sees on stdin (§11.5)."""

    text: str
    truncated: bool


def _lines(sources: Mapping[str, Path], repo: str, rel: str) -> list[str] | None:
    root = sources.get(repo)
    path = root / rel if root else None
    if path is None or not path.is_file():
        return None
    return path.read_text(errors="replace").splitlines()


def _span(lines: list[str], line: int, rule: str, clone: int) -> tuple[int, int]:
    if rule.startswith("llm-sites"):
        return max(1, line - LLM_CONTEXT), min(len(lines), line + LLM_CONTEXT)
    end = line + max(clone, 1) - 1
    if rule.startswith(("ast-dup", "cli-overlap")):
        try:
            tree = parse_python("\n".join(lines))
            for node in ast.walk(tree):
                if getattr(node, "lineno", None) == line and hasattr(node, "end_lineno"):
                    end = node.end_lineno or end
                    break
        except SyntaxError:
            end = line + 30
    return max(1, line - DUP_CONTEXT), min(len(lines), end + DUP_CONTEXT)


def _parts(f: Finding) -> list[tuple[str, str, int]]:
    if f.related:
        return [(r["owner_repo"], r["path"], int(r["line"])) for r in f.related]
    return [(f.owner_repo, loc.path, loc.line) for loc in f.locations[:1]]


def build_slice(f: Finding, sources: Mapping[str, Path]) -> JudgeSlice | None:
    """Serialised judge input: the call ± 40 lines, or every participant (§11.5)."""
    head = LLM_PROMPT if f.category == "llm-replaceable" else DUP_PROMPT
    feats = ", ".join(e["detail"] for e in f.evidence if e.get("kind") == "feature")
    blocks = [head, f"Finding: {f.rule} {f.anchor}" + (f" ({feats})" if feats else "")]
    for repo, rel, line in _parts(f):
        lines = _lines(sources, repo, rel)
        if lines is None:
            return None
        a, b = _span(lines, line, f.rule, clone_lines(f))
        body = "\n".join(lines[a - 1 : b])
        blocks.append(f"## {repo}:{rel} lines {a}-{b}\n```\n{body}\n```")
    text = "\n".join(blocks)
    if len(text.encode()) <= MAX_SLICE:
        return JudgeSlice(text, False)
    cut = text.encode()[:MAX_SLICE].decode(errors="ignore")
    return JudgeSlice(cut + "\n[truncated]", True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — срез для stdin (§11.5)"
```

---

### Task 3: адаптер `claude` — argv, окружение, разбор ответа

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `SCHEMA: dict`; `DEFAULT_MODEL = "claude-opus-5-5"`; `judge_argv(binary: str, model: str) -> list[str]`; `judge_env(platform: str, environ: Mapping[str, str]) -> dict[str, str]`; `call_judge(binary, text, model, *, runner=subprocess.run, platform=sys.platform, environ=os.environ) -> dict[str, str]` — вердикт (`rationale`, `verdict`, `replacement`) либо `{"verdict": "error", "detail": …}`.

- [ ] **Step 1: Write the failing tests**

```python
import json
import subprocess

from selfcheck.judge import (
    DEFAULT_MODEL,
    SCHEMA,
    call_judge,
    judge_argv,
    judge_env,
)

ENV = {
    "PATH": "/bin",
    "HOME": "/h",
    "USER": "u",
    "LOGNAME": "u",
    "GH_TOKEN": "secret",
    "REVIEW_MODEL": "x",
    "CLAUDE_CODE_OAUTH_TOKEN": "t",
    "ANTHROPIC_API_KEY": "k",
}


@pytest.mark.parametrize(
    ("platform", "keys"),
    [
        ("darwin", {"PATH", "HOME", "USER"}),
        ("linux", {"PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}),
    ],
)
def test_env_allowlist_per_platform(platform: str, keys: set[str]) -> None:
    assert set(judge_env(platform, ENV)) == keys


def test_argv_is_tool_less_and_pinned() -> None:
    argv = judge_argv("claude", DEFAULT_MODEL)
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == DEFAULT_MODEL
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert list(SCHEMA["properties"])[0] == "rationale"


def _runner(stdout: str, code: int = 0, raise_timeout: bool = False):
    def run(argv, **kw):
        if raise_timeout:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run


GOOD = {"rationale": "r", "verdict": "replace", "replacement": "merge"}


@pytest.mark.parametrize(
    ("stdout", "code", "timeout", "expected"),
    [
        (json.dumps({"is_error": False, "structured_output": GOOD}), 0, False, "replace"),
        (json.dumps({"is_error": True, "result": "Not logged in"}), 1, False, "error"),
        (json.dumps({"structured_output": {**GOOD, "verdict": "maybe"}}), 0, False, "error"),
        (json.dumps({"structured_output": {**GOOD, "id": "sc-x"}}), 0, False, "error"),
        ("not json", 0, False, "error"),
        ("x" * (64 * 1024 + 1), 0, False, "error"),
        ("", 0, True, "error"),
    ],
)
def test_call_judge_outcomes(stdout: str, code: int, timeout: bool, expected: str) -> None:
    got = call_judge(
        "claude", "slice", DEFAULT_MODEL, runner=_runner(stdout, code, timeout),
        platform="darwin", environ=ENV,
    )
    assert got["verdict"] == expected
    if expected == "error":
        assert got["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k "env_allowlist or argv_is or call_judge"`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_MODEL'`.

- [ ] **Step 3: Implement**

```python
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable

DEFAULT_MODEL = "claude-opus-5-5"
TIMEOUT = 120
MAX_OUTPUT = 64 * 1024
SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rationale", "verdict", "replacement"],
    "properties": {
        "rationale": {"type": "string"},
        "verdict": {"enum": ["replace", "keep", "unsure"]},
        "replacement": {
            "enum": [
                "regex", "rules", "decision-tree", "embedding-classifier",
                "small-model", "merge", "none",
            ]
        },
    },
}
_ENV_BASE = ("PATH", "HOME")
_ENV_PLATFORM = {
    "darwin": ("USER",),  # keychain lookup (§11.4)
    "linux": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
}
Runner = Callable[..., subprocess.CompletedProcess[str]]


def judge_env(platform: str, environ: Mapping[str, str]) -> dict[str, str]:
    """The allowlisted environment of the judge process (§11.4)."""
    keys = (*_ENV_BASE, *_ENV_PLATFORM.get(platform, ()))
    return {k: environ[k] for k in keys if k in environ}


def judge_argv(binary: str, model: str) -> list[str]:
    """Tool-less, stdin-only claude invocation (§5)."""
    return [
        binary, "-p", "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--model", model,
        "--restricted", "--strict-mcp-config", "--no-session-persistence",
        "--permission-prompts", "none", "--tools", "",
        "--max-budget-usd", "0.2",
    ]


def _error(detail: str) -> dict[str, str]:
    return {"verdict": "error", "detail": detail[:200]}


def _valid(out: object) -> dict[str, str] | None:
    if not isinstance(out, dict) or set(out) != set(SCHEMA["required"]):
        return None
    props = SCHEMA["properties"]
    if out["verdict"] not in props["verdict"]["enum"]:
        return None
    if out["replacement"] not in props["replacement"]["enum"]:
        return None
    if not isinstance(out["rationale"], str):
        return None
    return {k: str(out[k]) for k in SCHEMA["required"]}


def call_judge(
    binary: str,
    text: str,
    model: str,
    *,
    runner: Runner = subprocess.run,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """One judge call; any failure is ``{"verdict": "error"}`` (§5)."""
    with tempfile.TemporaryDirectory(prefix="selfcheck-judge-") as cwd:
        try:
            proc = runner(
                judge_argv(binary, model),
                input=text,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=judge_env(platform, environ),
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return _error("timeout")
        except OSError as exc:
            return _error(f"launch: {exc}")
    if len(proc.stdout.encode()) > MAX_OUTPUT:
        return _error("output-too-large")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _error(f"unparsable (rc {proc.returncode})")
    if not isinstance(data, dict) or data.get("is_error"):
        return _error(str(data.get("result") if isinstance(data, dict) else data))
    verdict = _valid(data.get("structured_output"))
    return verdict if verdict is not None else _error("outside-schema")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — адаптер claude, окружение по платформе, разбор по схеме (§5, §11.4)"
```

---

### Task 4: кэш вердиктов

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `JUDGE_VERSION = 1`; `cache_key(text: str, model: str) -> str`; `load_cache(path: Path) -> tuple[dict[str, dict], str | None]` (кэш, предупреждение); `save_cache(path: Path, cache: dict[str, dict]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import JUDGE_VERSION, cache_key, load_cache, save_cache


def test_changed_slice_misses_cache() -> None:
    assert cache_key("a", "m") != cache_key("b", "m")
    assert cache_key("a", "m") != cache_key("a", "m2")
    assert cache_key("a", "m") == cache_key("a", "m")
    assert str(JUDGE_VERSION) in cache_key("a", "m")


def test_cache_roundtrip_and_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "judge-cache.json"
    assert load_cache(path) == ({}, None)
    save_cache(path, {"k": {**GOOD, "at": "2026-09-27"}})
    assert load_cache(path)[0]["k"]["verdict"] == "replace"
    path.write_text("{broken")
    cache, warning = load_cache(path)
    assert cache == {} and warning and "judge-cache" in warning
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k cache`
Expected: FAIL — `ImportError: cannot import name 'JUDGE_VERSION'`.

- [ ] **Step 3: Implement**

```python
import hashlib

JUDGE_VERSION = 1  # bump on any prompt/schema change (§11.3)


def cache_key(text: str, model: str) -> str:
    """sha256 of the slice + prompt/schema version + model (§11.3)."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    return f"v{JUDGE_VERSION}:{model}:{digest}"


def load_cache(path: Path) -> tuple[dict[str, dict], str | None]:
    """The verdict cache; a broken file is a warning and an empty cache."""
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {}, f"judge-cache {path.name} unreadable, starting empty: {exc}"
    if not isinstance(data, dict):
        return {}, f"judge-cache {path.name} is not an object, starting empty"
    return data, None


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    """Atomic write: temp file + rename (§11.3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True))
    tmp.replace(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — кэш вердиктов (§11.3)"
```

---

### Task 5: `run_judge` — потолок, кэш, параллель, статус; применение вердиктов

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `JudgeRun(verdicts: dict[str, dict], not_judged: list[dict[str, str]], result: ProbeResult, warnings: list[str], calls: int, cached: int)`; `run_judge(findings, sources, cache_path, *, cap=100, model=DEFAULT_MODEL, which=shutil.which, runner=subprocess.run, workers=4) -> JudgeRun`; `apply_verdicts(findings: list[Finding], verdicts: Mapping[str, dict]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import apply_verdicts, run_judge
from selfcheck.probes.base import ProbeStatus


def _counting(verdict: dict | None = None):
    calls: list[str] = []

    def run(argv, **kw):
        calls.append(kw["input"])
        out = {"structured_output": verdict or GOOD}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    return run, calls


def _llm(tmp_path: Path, n: int) -> tuple[list[Finding], dict[str, Path]]:
    src = _src(tmp_path, {f"m{i}.py": f"x = {i}\n" * 3 for i in range(n)})
    return [
        _f("llm-sites/replaceable", Confidence.CANDIDATE, path=f"m{i}.py")
        for i in range(n)
    ], src


def test_cap_and_cache_descend(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 3)
    cache = tmp_path / "judge-cache.json"
    run, calls = _counting()
    first = run_judge(fs, src, cache, cap=2, which=lambda _: "claude", runner=run)
    assert (first.calls, first.cached, len(first.not_judged)) == (2, 0, 1)
    assert first.result.status is ProbeStatus.OK
    second = run_judge(fs, src, cache, cap=2, which=lambda _: "claude", runner=run)
    assert (second.calls, second.cached, len(second.not_judged)) == (1, 2, 0)
    assert len(calls) == 3


def test_errors_are_partial_then_failed_and_not_cached(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 2)
    cache = tmp_path / "c.json"

    def flaky(argv, **kw):
        ok = "x = 0" in kw["input"]
        out = {"structured_output": GOOD} if ok else {"is_error": True, "result": "x"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    part = run_judge(fs, src, cache, which=lambda _: "claude", runner=flaky)
    assert part.result.status is ProbeStatus.PARTIAL
    assert len(load_cache(cache)[0]) == 1  # the error is not cached

    def broken(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "nope", "")

    fs2, src2 = _llm(tmp_path / "b", 2)
    failed = run_judge(fs2, src2, tmp_path / "d.json", which=lambda _: "claude", runner=broken)
    assert failed.result.status is ProbeStatus.FAILED


def test_no_binary_is_unavailable(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 1)
    run, calls = _counting()
    res = run_judge(fs, src, tmp_path / "c.json", which=lambda _: None, runner=run)
    assert res.result.status is ProbeStatus.UNAVAILABLE and not calls
    assert len(res.not_judged) == 1


def test_missing_source_is_an_error_verdict(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="gone.py")
    run, calls = _counting()
    res = run_judge([f], {"a": tmp_path}, tmp_path / "c.json", which=lambda _: "claude", runner=run)
    assert res.verdicts[f.id]["detail"] == "source-missing" and not calls


def test_apply_never_confirmed_nor_new_id() -> None:
    cand = _f("llm-sites/replaceable", Confidence.CANDIDATE)
    keep = _f("jscpd/clone", Confidence.LIKELY, lines=9)
    ids = (cand.id, keep.id)
    apply_verdicts(
        [cand, keep],
        {cand.id: GOOD, keep.id: {**GOOD, "verdict": "keep", "replacement": "none"}},
    )
    assert cand.confidence is Confidence.LIKELY and keep.confidence is Confidence.LIKELY
    assert (cand.id, keep.id) == ids
    assert cand.judge and cand.judge["verdict"] == "replace"
    assert keep.judge and keep.judge["verdict"] == "keep"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k "cap_and or errors_are or no_binary or missing_source or apply_never"`
Expected: FAIL — `ImportError: cannot import name 'apply_verdicts'`.

- [ ] **Step 3: Implement**

```python
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from selfcheck.probes.base import ProbeResult, ProbeStatus

WORKERS = 4


@dataclass
class JudgeRun:
    """What one judge pass produced (§11.2, §11.6)."""

    verdicts: dict[str, dict]
    not_judged: list[dict[str, str]]
    result: ProbeResult
    warnings: list[str]
    calls: int = 0
    cached: int = 0


def _status(calls: int, errors: int) -> ProbeStatus:
    if errors and errors >= calls:
        return ProbeStatus.FAILED
    return ProbeStatus.PARTIAL if errors else ProbeStatus.OK


def run_judge(
    findings: list[Finding],
    sources: Mapping[str, Path],
    cache_path: Path,
    *,
    cap: int = 100,
    model: str = DEFAULT_MODEL,
    which: Callable[[str], str | None] = shutil.which,
    runner: Runner = subprocess.run,
    workers: int = WORKERS,
) -> JudgeRun:
    """Judge candidates in the normative order under ``cap`` new calls (§11)."""
    cache, warning = load_cache(cache_path)
    run = JudgeRun({}, [], ProbeResult("judge", "devtools", ProbeStatus.OK), [])
    if warning:
        run.warnings.append(warning)
    binary = which("claude")
    todo: list[tuple[Finding, str, str]] = []
    for f in sorted((f for f in findings if is_candidate(f)), key=order_key):
        sl = build_slice(f, sources)
        if sl is None:
            run.verdicts[f.id] = _error("source-missing")
            continue
        key = cache_key(sl.text, model)
        if key in cache:
            run.verdicts[f.id] = {**cache[key], "cached": True, "model": model}
            run.cached += 1
        elif binary is not None and len(todo) < cap:
            todo.append((f, sl.text, key))
        else:
            run.not_judged.append({"id": f.id, "rule": f.rule})
    if binary is None:
        run.result = ProbeResult("judge", "devtools", ProbeStatus.UNAVAILABLE, "no claude")
        return run
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outs = list(pool.map(lambda t: call_judge(binary, t[1], model, runner=runner), todo))
    stamp = datetime.now(UTC).date().isoformat()
    errors = 0
    for (f, _text, key), out in zip(todo, outs, strict=True):
        run.verdicts[f.id] = {**out, "cached": False, "model": model}
        if out["verdict"] == "error":
            errors += 1
        else:
            cache[key] = {**out, "at": stamp}
    run.calls = len(todo)
    errors += sum(1 for v in run.verdicts.values() if v.get("detail") == "source-missing")
    run.result = ProbeResult(
        "judge", "devtools", _status(run.calls or errors, errors), f"{errors} errors"
    )
    save_cache(cache_path, cache)
    return run


def apply_verdicts(findings: list[Finding], verdicts: Mapping[str, dict]) -> None:
    """Only ``judge`` and confidence change; never ``confirmed`` (§5, §11.6)."""
    for f in findings:
        v = verdicts.get(f.id)
        if v is None:
            continue
        f.judge = dict(v)
        if v["verdict"] == "replace":
            f.confidence = Confidence.LIKELY  # candidate/likely → likely, never confirmed
```

(`reason` у `ProbeResult` — строка вида `"N errors"`; при `ok` — `"0 errors"`. Поля `ProbeResult`, которых здесь нет, — по умолчанию.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — потолок, кэш, параллель, статус; применение вердиктов (§11.2–11.6)"
```

---

### Task 6: `--judge` в прогоне и отчёте

**Files:**
- Modify: `selfcheck/run.py` (флаги, вызов между `final` и снимком), `selfcheck/report.py` (сводка, «оставить», не судившиеся), `Makefile:60` (help)
- Test: `tests/selfcheck/test_judge_run.py` (новый)

**Interfaces:**
- Consumes: `run_judge`, `apply_verdicts`, `instrument_findings`.
- Produces: флаги `--judge`, `--judge-max N`, `--judge-model M`; в `report.json` ключ `judge: {calls, cached, not_judged, model}`; раздел `report.md` «Судья».

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_judge_run.py`:

```python
"""S5 — --judge wired into the run and the report (§11.6, §11.7)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from selfcheck.run import main
from tests.selfcheck.helpers import require_tool, workspace
from tests.selfcheck.test_run import args, reports

CLASSIFY = (
    "import json\nimport subprocess\n\n\ndef classify(items):\n    out = []\n"
    "    for item in items:\n"
    '        raw = subprocess.run(["claude", "-p", f"label {item}"],\n'
    "                             capture_output=True, text=True).stdout\n"
    '        out.append(json.loads(raw)["label"])\n    return out\n'
)


def fake_claude(tmp: Path, answer: dict) -> Path:
    """A `claude` on PATH that logs argv/env and prints ``answer``."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    log = tmp / "calls.jsonl"
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv, "
        "'env': sorted(os.environ), 'stdin': sys.stdin.read()}) + '\\n')\n"
        f"print(json.dumps({{'structured_output': {answer!r}}}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _calls(tmp: Path) -> list[dict]:
    log = tmp / "calls.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


GOOD = {"rationale": "r", "verdict": "replace", "replacement": "rules"}


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    require_tool("uvx")
    bin_dir = fake_claude(tmp_path, GOOD)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "must-not-leak")
    return workspace(tmp_path, {"c.py": CLASSIFY})


def test_no_judge_flag_no_harness_call(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites"])
    assert _calls(tmp_path) == []
    assert all(p["probe"] != "judge" for p in reports(ws)[-1]["probes"])


def test_judge_runs_once_and_caches(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    (call,) = _calls(tmp_path)
    assert "--restricted" in call["argv"] and "GH_TOKEN" not in call["env"]
    doc = reports(ws)[-1]
    (f,) = [x for x in doc["findings"] if x["rule"] == "llm-sites/replaceable"]
    assert f["judge"]["verdict"] == "replace" and f["confidence"] == "likely"
    assert {p["probe"]: p["status"] for p in doc["probes"]}["judge"] == "ok"
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    assert len(_calls(tmp_path)) == 1  # second run: from cache
    assert reports(ws)[-1]["judge"]["cached"] == 1


def test_judge_max_zero_lists_not_judged(ws: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge", "--judge-max", "0"])
    doc = reports(ws)[-1]
    assert [x["rule"] for x in doc["judge"]["not_judged"]] == ["llm-sites/replaceable"]
    assert "не судились" in next((ws / "out").glob("*/report.md")).read_text()


def test_missing_claude_is_unavailable_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    uvx = Path(os.popen("command -v uvx").read().strip()).parent
    monkeypatch.setenv("PATH", f"{uvx}{os.pathsep}/usr/bin{os.pathsep}/bin")
    code = main([*args(ws), "--probe", "llm-sites", "--judge"])
    doc = reports(ws)[-1]
    assert {p["probe"]: p["status"] for p in doc["probes"]}["judge"] == "unavailable"
    assert code == 3
```

(если `claude` стоит в `/usr/bin` или рядом с `uvx` — тест `unavailable` строит `PATH` из пустого каталога с симлинками только на `uvx`, `git`; ruling в ledger.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge_run.py -q`
Expected: FAIL — `unrecognized arguments: --judge` (`SystemExit: 2`); `test_no_judge_flag_no_harness_call` проходит уже сейчас (двойник).

- [ ] **Step 3: Implement**

`run.py` — `_args`:

```python
    parser.add_argument("--judge", action="store_true", help="LLM judge (spec §5, §11)")
    parser.add_argument("--judge-max", type=int, default=100)
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
```

`_Run` — `judge: dict[str, Any] = field(default_factory=dict)`. В `main` — сразу после `final = aggregate([...])`:

```python
    if args.judge:
        final = _judge(final, args, known, acc)
```

```python
def _judge(
    final: list[Finding], args: argparse.Namespace, known: dict[str, RepoEntry], acc: _Run
) -> list[Finding]:
    """Run the judge over the final findings; its status is a probe row (§11.6)."""
    sources = {name: entry.path for name, entry in known.items()}
    jr = run_judge(
        final,
        sources,
        args.out / "judge-cache.json",
        cap=args.judge_max,
        model=args.judge_model,
    )
    apply_verdicts(final, jr.verdicts)
    acc.results.append(jr.result)
    acc.warnings += jr.warnings
    acc.judge = {
        "model": args.judge_model,
        "calls": jr.calls,
        "cached": jr.cached,
        "not_judged": jr.not_judged,
    }
    return aggregate([*final, *instrument_findings([jr.result])])
```

`_document` — ключ `"judge": acc.judge`. `report.py` — функция `_judge_lines(doc)` и вызов перед «## Подавлено»:

```python
def _judge_lines(doc: dict[str, Any]) -> list[str]:
    """Judge summary, the «keep» section and what was not judged (§11.2, §11.6)."""
    j = doc.get("judge") or {}
    if not j:
        return []
    judged = [f for f in doc["findings"] if f.get("judge")]
    keep = [f for f in judged if f["judge"].get("verdict") == "keep"]
    lines = [
        "## Судья",
        "",
        (
            f"модель {j['model']}: вердиктов {len(judged)} (из кэша {j['cached']}, "
            f"новых вызовов {j['calls']}); не судились {len(j['not_judged'])}"
        ),
    ]
    if keep:
        lines += ["", "### Судья: оставить", "", "| правило | якорь | обоснование |",
                  "|---|---|---|"]
        lines += [
            f"| {f['rule']} | `{f['anchor']}` | "
            f"{f['judge'].get('rationale', '')[:160].replace('|', '/')} |"
            for f in keep[:MD_ROWS]
        ]
    if j["not_judged"]:
        by_rule = Counter(x["rule"] for x in j["not_judged"])
        lines += ["", f"не судились: {dict(by_rule)}; первые 20:"]
        lines += [f"- {x['rule']} `{x['id']}`" for x in j["not_judged"][:20]]
    return [*lines, ""]
```

В таблицах категорий `render_markdown` находки с `judge.verdict == "keep"` пропускаются (они в разделе «оставить»): фильтр `items = [f for f in items if (f.get("judge") or {}).get("verdict") != "keep"]` до построения таблицы. `Makefile:60` — `[--judge [--judge-max N]]` в help.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/run.py selfcheck/report.py Makefile tests/selfcheck/test_judge_run.py
git commit -m "feat(selfcheck): --judge в прогоне и отчёте — строка judge, кэш, не судившиеся (§11.6)"
```

---

### Task 7: документация, проверки, приёмка после мержа

**Files:**
- Modify: `CLAUDE.md` (строка `selfcheck/`)
- Create (dev-only): `../_cowork_output/devtools-selfcheck-s5-acceptance-<дата>.md`

- [ ] **Step 1: Документация и коммит**

`CLAUDE.md` — в строку `selfcheck/`: «`ARGS=--judge` (S5) — судья без инструментов над кандидатами `llm-replaceable`/`duplicate`: потолок `--judge-max` (100) в нормативном порядке, кэш вердиктов `out/selfcheck/judge-cache.json` (каждый прогон спускается глубже), статус — строка `judge` в таблице проб; модель `--judge-model`».

```bash
git add CLAUDE.md
git commit -m "docs(selfcheck): S5 --judge в CLAUDE.md"
```

- [ ] **Step 2: Полный набор проверок ветки**

Run: `uv run --frozen ruff format --check selfcheck tests/selfcheck && uv run --frozen ruff check selfcheck tests/selfcheck && uv run --frozen --group selfcheck pyrefly check selfcheck tests/selfcheck && make selfcheck-dogfood && git status --porcelain`
Expected: всё зелёное (pyrefly — только известная `test_run.py:115`), дерево чистое.

- [ ] **Step 3: Приёмка §11.7 после мержа**

На master devtools: `make selfcheck ARGS='--all --judge'`, затем второй такой же прогон. Скрипт сверки (scratchpad) по `report.json`:
1. строка `judge` — `ok`; вердикты у всех кандидатов не-jscpd (59 по замеру) и у крупнейших клонов jscpd до потолка;
2. у `replace` встречаются конкретные замены (не только `none`);
3. ручная выборка 10 вердиктов (≥ 3 `replace`, ≥ 3 `keep`): согласие вердикта с обоснованием и с кодом — таблица в отчёте приёмки;
4. второй прогон: `judge.cached` ≥ числа вердиктов первого, новые вызовы — следующие по порядку кандидаты.

Отчёт — `_cowork_output/devtools-selfcheck-s5-acceptance-<дата>.md`; затем PR: `TODO.md` `selfcheck-s5` [x].
