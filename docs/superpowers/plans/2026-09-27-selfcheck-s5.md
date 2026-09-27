# selfcheck S5 — судья (`--judge`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make selfcheck ARGS='--all --judge'` выносит вердикты `replace|keep|unsure` над кандидатами `llm-replaceable` и `duplicate` — под потолком, в нормативном порядке, с кэшем вердиктов и видимым статусом судьи.

**Architecture:** новый модуль `selfcheck/judge.py` — чистые функции (отбор и порядок, срез, ключ и файл кэша, argv/окружение, разбор ответа) плюс `run_judge` с подменяемыми `which`/`runner`. `run.main` зовёт его между `final` и снимком дельты; статус судьи — ещё одна `ProbeResult` в `acc.results` с ключом сопоставимости `judge@devtools`, поэтому код выхода, находки прибора и их судьба в дельте работают почти без новой логики (одна строка в `delta._instrument_status`). Отчёт получает сводку, раздел «Судья: оставить» и перечень не судившихся.

**Tech Stack:** Python 3.12+, pytest, `claude` CLI (в тестах — поддельный исполняемый файл в `PATH`), `concurrent.futures`, `probes.base.run_group`.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md` rev 5.10 — §5, §11 (плюс §1.3, §8). Замер — dev-only `../_cowork_output/devtools-selfcheck-s5-measure-2026-09-27.md`. Ревью пары r1 — `../_cowork_output/devtools-selfcheck-s5-pair-review-r1-2026-09-27.md`.

## Global Constraints

- Без `--judge` харнесс не вызывается ни разу; строки `judge` в таблице проб нет (§11.6).
- argv: `claude -p --output-format json --json-schema <SCHEMA> --model <M> --restricted --strict-mcp-config --no-session-persistence --permission-prompts none --tools "" --max-budget-usd 0.2`; `cwd` — пустой временный каталог; вход — только stdin (файл в этом каталоге) (§5, §11.4).
- Вызов — через `run_group` (своя группа процессов; таймаут и Ctrl-C убивают потомков); Ctrl-C отменяет ещё не начатые вызовы (§11.4).
- Окружение: `PATH`, `HOME`; `darwin` — ещё `USER`; `linux` — ещё `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, если заданы. Ничего больше (§11.4).
- Схема: поля `rationale`, `verdict` (`replace|keep|unsure`), `replacement` (`regex|rules|decision-tree|embedding-classifier|small-model|merge|none`); `rationale` первым; `additionalProperties: false` (§5).
- Таймаут 120 с, вывод ≤ 64 КиБ, до 4 вызовов параллельно, `--judge-max` 100, `--judge-model` `claude-opus-5-5` (§11.4, §11.2).
- Порядок: межрепные → вид (`llm-replaceable` → `ast-dup` → `cli-overlap` → `jscpd`) → `likely` → `candidate` → длина клона jscpd по убыванию → (`owner_repo`, путь, строка) (§11.2, решение владельца r1).
- Срез: строки по `\n`; файл по `raw_path` (#420); > 48 КиБ — обрезка по байтам, `judge.truncated` (§11.5).
- Кэш `out/selfcheck/judge-cache.json`: ключ `v<JUDGE_VERSION>:<модель>:<sha256 среза>`; ошибки не кэшируются; записи не по схеме — пропуск с предупреждением; сохранение по мере готовности, уникальный временный файл + `replace`; записи прежней версии отбрасываются; сбой записи — предупреждение (§11.3).
- Статус: попытки = новые вызовы + из кэша + сбои среза; `ok` без ошибок; `partial` — ошибки и ≥ 1 валидный вердикт; `failed` — ошибки и ни одного валидного; `unavailable` — нет `claude` (кэш применяется). Ключ `judge@devtools` = `judge v<JUDGE_VERSION> / <модель>` при `ok`; находка прибора судьи `resolved` при `ok`-прогоне с `--judge` независимо от `scope` (§11.6).
- Вердикт: только `judge` и уверенность; `replace` поднимает `candidate` до `likely`; ничто не даёт `confirmed` и не меняет `id` (§5, §11.6).
- `[corpus] exclude` — `.obsidian/plugins/**` (§11.2).
- Ни один тест не вызывает настоящий `claude`: поддельный первым в `PATH` либо `PATH` без `claude` вовсе.
- `uv run`, не pip; после каждой задачи `uv run --frozen --group selfcheck pytest tests/selfcheck -q`, `ruff format`, `ruff check`, `pyrefly check selfcheck tests/selfcheck`; строка ≤ 88.

## Review Focus

1. **Судья вызывается без `--judge`** (Task 6 `test_no_judge_flag_no_harness_call`).
2. **Окружение исполнителя утекает в процесс судьи** (Task 3 `test_env_allowlist_per_platform`).
3. **Ответ модели меняет идентичность или даёт `confirmed`** (Task 5 `test_apply_never_confirmed_nor_new_id`).
4. **Кэш подаёт устаревший вердикт на изменённый код** (Task 5 `test_changed_file_or_model_is_a_new_call`).
5. **Ошибка судьи проходит молча или не закрывается** (Task 5 статусы; Task 6 `test_judge_error_exit_2_then_resolves`).

---

### Task 1: артефакты сборки вне корпуса; отбор и порядок

**Files:**
- Modify: `selfcheck.toml`
- Create: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py` (новый)

**Interfaces:**
- Produces: `is_candidate(f) -> bool`; `clone_lines(f) -> int`; `order_key(f) -> tuple`; `JUDGE_RULES`.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_judge.py`:

```python
"""S5 — the judge (spec §5, §11)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.config import load_config
from selfcheck.corpus import list_corpus
from selfcheck.judge import clone_lines, is_candidate, order_key
from selfcheck.model import Confidence, Finding, Location
import os

from tests.selfcheck.helpers import git, make_repo


def _f(
    rule: str,
    conf: Confidence,
    repos: tuple[str, ...] = ("a",),
    path: str = "x.py",
    line: int = 1,
    lines: int | None = None,
) -> Finding:
    category = "llm-replaceable" if rule.startswith("llm") else "duplicate"
    members = repos * (2 if len(repos) == 1 else 1)
    related = [
        {"owner_repo": r, "path": path, "line": line + i, "member": str(i)}
        for i, r in enumerate(members)
    ]
    return Finding(
        rule=rule,
        category=category,
        severity="medium",
        confidence=conf,
        owner_repo=repos[0],
        anchor=f"{rule}:{path}:{line}:{lines}:{'-'.join(repos)}",
        locations=[Location(path, line)],
        related=related if category == "duplicate" else [],
        evidence=[{"kind": "lines", "detail": str(lines)}] if lines else [],
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
        _f("jscpd/clone", Confidence.LIKELY, repos=("a", "b"), lines=5),
        _f("ast-dup/structural", Confidence.CANDIDATE, repos=("a", "b")),
    ]
    got = [(f.rule, clone_lines(f)) for f in sorted(fs, key=order_key)]
    assert got == [
        ("ast-dup/structural", 0),  # cross-repo, kind before level
        ("jscpd/clone", 5),
        ("llm-sites/replaceable", 0),  # intra: llm → ast → cli → jscpd
        ("ast-dup/structural", 0),
        ("cli-overlap/argparse", 0),
        ("jscpd/clone", 40),  # longest clone first
        ("jscpd/clone", 10),
    ]


def test_obsidian_plugins_are_out_of_the_corpus(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[2] / "selfcheck.toml")
    repo = make_repo(
        tmp_path / "vault",
        {".obsidian/plugins/p/main.js": "x();\n", ".obsidian/app.json": "{}", "a.md": "#\n"},
    )
    corpus = list_corpus(repo, config.corpus_exclude)
    assert ".obsidian/plugins/p/main.js" not in corpus and "a.md" in corpus
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'selfcheck.judge'`.

- [ ] **Step 3: Implement**

`selfcheck.toml` — после записей `[[allow]]`:

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
    """Normative judge order under the cap: kind before level (§11.2)."""
    prefix = f.rule.split("/", 1)[0]
    loc = f.locations[0] if f.locations else None
    return (
        0 if len(_repos(f)) > 1 else 1,
        JUDGE_RULES.index(prefix) if prefix in JUDGE_RULES else len(JUDGE_RULES),
        _LEVEL.get(f.confidence, 2),
        -clone_lines(f),
        f.owner_repo,
        loc.path if loc else "",
        loc.line if loc else 0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком (ни один тест не пинует sha1 непустого `selfcheck.toml`; `test_run.py:97` — пустой конфиг).

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
- Produces: `build_slice(f, sources: Mapping[str, Path]) -> JudgeSlice | None`; `JudgeSlice(text: str, truncated: bool)`; `MAX_SLICE = 48 * 1024`; `LLM_PROMPT`, `DUP_PROMPT`.

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import MAX_SLICE, build_slice

FUNC = "".join(f"# pad {i}\n" for i in range(50)) + (
    "def f(x):\n" + "".join(f"    y{i} = x + {i}\n" for i in range(10)) + "    return x\n"
)  # line k (1-based) of the pad block is "# pad {k-1}"; `def f` is line 51


def _src(tmp_path: Path, files: dict[str, str]) -> dict[str, Path]:
    root = tmp_path / "a"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return {"a": root}


def test_llm_slice_is_the_line_pm_40(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, line=55)
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and not s.truncated
    assert "# pad 14" in s.text and "# pad 13" not in s.text  # lines 15..95
    assert "a:x.py lines 15-" in s.text


def test_ast_dup_slice_takes_whole_functions(tmp_path: Path) -> None:
    f = _f("ast-dup/structural", Confidence.CANDIDATE, line=51)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 51, "member": "f"}]
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and "    return x" in s.text and "# pad 45" in s.text


def test_jscpd_slice_is_clone_pm_5(tmp_path: Path) -> None:
    f = _f("jscpd/clone", Confidence.LIKELY, line=20, lines=3)  # clone 20..22
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 20, "member": "20"}]
    s = build_slice(f, _src(tmp_path, {"x.py": FUNC}))
    assert s is not None and "# pad 14" in s.text and "# pad 26" in s.text
    assert "# pad 13" not in s.text and "# pad 27" not in s.text


def test_form_feed_does_not_shift_lines(tmp_path: Path) -> None:
    text = "L1\x0c\n" + "".join(f"L{i}\n" for i in range(2, 21))  # \f: splitlines
    f = _f("jscpd/clone", Confidence.LIKELY, line=3, lines=1)  # window 1..8
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 3, "member": "3"}]
    s = build_slice(f, _src(tmp_path, {"x.py": text}))
    assert s is not None and "L8" in s.text and "L9" not in s.text


def test_non_utf8_name_is_read_by_raw_path(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "a", {"ok.py": "x = 1\n"})
    with open(os.fsencode(repo) + b"/n\xff.py", "wb") as fh:
        fh.write(b"y = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "non-utf8 name")
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="n\ufffd.py")
    s = build_slice(f, {"a": repo})
    assert s is not None and "y = 2" in s.text


def test_symlink_is_not_read(tmp_path: Path) -> None:
    src = _src(tmp_path, {"real.py": FUNC})
    (src["a"] / "link.py").symlink_to(src["a"] / "real.py")
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="link.py")
    assert build_slice(f, src) is None


def test_missing_file_gives_none(tmp_path: Path) -> None:
    f = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="gone.py")
    assert build_slice(f, {"a": tmp_path}) is None


def test_huge_slice_is_truncated(tmp_path: Path) -> None:
    f = _f("jscpd/clone", Confidence.LIKELY, line=1, lines=30000)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 1, "member": "1"}]
    s = build_slice(f, _src(tmp_path, {"x.py": "x = 1\n" * 40000}))
    assert s is not None and s.truncated
    assert len(s.text.encode()) <= MAX_SLICE + 32 and s.text.endswith("[truncated]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k "slice or form_feed or missing_file"`
Expected: FAIL — `ImportError: cannot import name 'MAX_SLICE'`.

- [ ] **Step 3: Implement**

```python
import ast
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from selfcheck.corpus import raw_path
from selfcheck.graph.model import parse_python

MAX_SLICE = 48 * 1024
LLM_CONTEXT = 40
DUP_CONTEXT = 5
MAKE_SPAN = 30
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
    """The file as lines split on ``\\n`` only (like the tools), read by raw name."""
    root = sources.get(repo)
    if root is None:
        return None
    try:
        path = root / raw_path(root, rel)
        if path.is_symlink() or not path.is_file():  # no symlinks (§1.3)
            return None
        return path.read_bytes().decode(errors="replace").split("\n")
    except (OSError, subprocess.CalledProcessError):  # raw_path runs git
        return None


def _span(lines: list[str], line: int, rule: str, clone: int) -> tuple[int, int]:
    if rule.startswith("llm-sites"):
        return max(1, line - LLM_CONTEXT), min(len(lines), line + LLM_CONTEXT)
    if rule == "cli-overlap/make-recipe":
        return line, min(len(lines), line + MAKE_SPAN)
    end = line + max(clone, 1) - 1  # jscpd `lines` is inclusive
    if rule.startswith(("ast-dup", "cli-overlap")):
        try:
            tree = parse_python("\n".join(lines))
        except SyntaxError:
            tree = None
        for node in ast.walk(tree) if tree is not None else ():
            if getattr(node, "lineno", None) == line and getattr(node, "end_lineno", 0):
                end = node.end_lineno  # type: ignore[attr-defined]
                break
    return max(1, line - DUP_CONTEXT), min(len(lines), end + DUP_CONTEXT)


def _parts(f: Finding) -> list[tuple[str, str, int]]:
    if f.related:
        return [(r["owner_repo"], r["path"], int(r["line"])) for r in f.related]
    return [(f.owner_repo, loc.path, loc.line) for loc in f.locations[:1]]


def build_slice(f: Finding, sources: Mapping[str, Path]) -> JudgeSlice | None:
    """Serialised judge input (§11.5); ``None`` when a source file is missing."""
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
    raw = text.encode()
    if len(raw) <= MAX_SLICE:
        return JudgeSlice(text, False)
    return JudgeSlice(raw[:MAX_SLICE].decode(errors="ignore") + "\n[truncated]", True)
```

(`raw_path` — `selfcheck/corpus.py`, #420/#435: для имени без U+FFFD возвращает его же.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — срез для stdin по raw_path, строки по \\n (§11.5)"
```

---

### Task 3: адаптер `claude` — argv, окружение, вызов, разбор

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `SCHEMA`; `DEFAULT_MODEL = "claude-opus-5-5"`; `judge_argv(binary, model) -> list[str]`; `judge_env(platform, environ) -> dict[str, str]`; `call_judge(binary, text, model, *, runner=run_group, platform=sys.platform, environ=os.environ) -> dict[str, str]` — вердикт либо `{"verdict": "error", "detail": …}`. Runner получает stdin **файлом** (`stdin=<file>`), как `run_group`.

- [ ] **Step 1: Write the failing tests**

```python
import json
import subprocess

import pytest

from selfcheck.judge import DEFAULT_MODEL, SCHEMA, call_judge, judge_argv, judge_env

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
GOOD = {"rationale": "r", "verdict": "replace", "replacement": "merge"}


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
    assert next(iter(SCHEMA["properties"])) == "rationale"


def _runner(stdout: str = "", code: int = 0, exc: BaseException | None = None):
    def run(argv, **kw):
        assert kw["stdin"].read() == "slice"  # the slice comes on stdin, as a file
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run


@pytest.mark.parametrize(
    ("stdout", "code", "exc", "expected"),
    [
        (json.dumps({"is_error": False, "structured_output": GOOD}), 0, None, "replace"),
        (json.dumps({"is_error": True, "result": "Not logged in"}), 1, None, "error"),
        (json.dumps({"structured_output": {**GOOD, "verdict": "maybe"}}), 0, None, "error"),
        (json.dumps({"structured_output": {**GOOD, "id": "sc-x"}}), 0, None, "error"),
        ("not json", 0, None, "error"),
        ("x" * (64 * 1024 + 1), 0, None, "error"),
        ("", 0, subprocess.TimeoutExpired(["claude"], 120), "error"),
        ("", 0, ValueError("boom"), "error"),
    ],
)
def test_call_judge_outcomes(
    stdout: str, code: int, exc: BaseException | None, expected: str
) -> None:
    got = call_judge(
        "claude",
        "slice",
        DEFAULT_MODEL,
        runner=_runner(stdout, code, exc),
        platform="darwin",
        environ=ENV,
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

from selfcheck.probes.base import run_group

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
                "regex",
                "rules",
                "decision-tree",
                "embedding-classifier",
                "small-model",
                "merge",
                "none",
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
        binary,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
        "--model",
        model,
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-prompts",
        "none",
        "--tools",
        "",
        "--max-budget-usd",
        "0.2",
    ]


def _error(detail: str) -> dict[str, str]:
    return {"verdict": "error", "detail": detail[:200]}


def valid_verdict(out: object) -> dict[str, str] | None:
    """The verdict fields when ``out`` matches SCHEMA exactly, else None."""
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
    runner: Runner = run_group,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """One judge call; any failure is ``{"verdict": "error"}`` (§5, §11.4)."""
    try:
        with tempfile.TemporaryDirectory(prefix="selfcheck-judge-") as cwd:
            stdin_path = Path(cwd) / "stdin.txt"
            stdin_path.write_text(text, encoding="utf-8")
            with stdin_path.open(encoding="utf-8") as stdin:
                proc = runner(
                    judge_argv(binary, model),
                    stdin=stdin,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=cwd,
                    env=judge_env(platform, environ),
                    timeout=TIMEOUT,
                )
    except subprocess.TimeoutExpired:
        return _error("timeout")
    except Exception as exc:  # noqa: BLE001 — the adapter never kills the run (r1 P6)
        return _error(f"adapter: {exc!r}")
    if len(proc.stdout.encode()) > MAX_OUTPUT:
        return _error("output-too-large")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _error(f"unparsable (rc {proc.returncode})")
    if not isinstance(data, dict) or data.get("is_error"):
        return _error(str(data.get("result") if isinstance(data, dict) else data))
    verdict = valid_verdict(data.get("structured_output"))
    return verdict if verdict is not None else _error("outside-schema")
```

(Файл stdin лежит в том же пустом `cwd`, что видит процесс судьи: это его единственный вход — граница §5 соблюдена.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — адаптер claude через run_group, окружение по платформе (§5, §11.4)"
```

---

### Task 4: кэш вердиктов

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `JUDGE_VERSION = 1`; `cache_key(text, model) -> str`; `load_cache(path) -> tuple[dict[str, dict], list[str]]` (кэш, предупреждения); `save_cache(path, cache) -> str | None` (предупреждение при сбое).

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import JUDGE_VERSION, cache_key, load_cache, save_cache


def test_cache_key_follows_slice_and_model() -> None:
    assert cache_key("a", "m") != cache_key("b", "m")
    assert cache_key("a", "m") != cache_key("a", "m2")
    assert cache_key("a", "m") == cache_key("a", "m")
    assert cache_key("a", "m").startswith(f"v{JUDGE_VERSION}:")


def test_cache_roundtrip_bad_entries_and_old_versions(tmp_path: Path) -> None:
    path = tmp_path / "judge-cache.json"
    assert load_cache(path) == ({}, [])
    good_key = cache_key("s", "m")
    cache = {
        good_key: {**GOOD, "at": "2026-09-27"},
        "v0:m:old": {**GOOD, "at": "2026-01-01"},
        cache_key("t", "m"): {"at": "x"},  # not by schema
    }
    assert save_cache(path, cache) is None
    loaded, warnings = load_cache(path)
    assert list(loaded) == [good_key] and warnings  # old version dropped, bad skipped
    path.write_text("{broken")
    assert load_cache(path)[0] == {} and load_cache(path)[1]


def test_cache_write_failure_is_a_warning(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert save_cache(ro / "judge-cache.json", {}) is not None
    finally:
        ro.chmod(0o700)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q -k cache`
Expected: FAIL — `ImportError: cannot import name 'JUDGE_VERSION'`.

- [ ] **Step 3: Implement**

```python
import hashlib

JUDGE_VERSION = 1  # bump on any prompt/schema change (§11.3)


def cache_key(text: str, model: str) -> str:
    """``v<version>:<model>:<sha256 of the slice>`` (§11.3)."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    return f"v{JUDGE_VERSION}:{model}:{digest}"


def _current(key: str) -> bool:
    return key.startswith(f"v{JUDGE_VERSION}:")


def load_cache(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Valid current-version entries; anything else is a warning, never exit 4."""
    if not path.is_file():
        return {}, []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {}, [f"judge-cache {path.name} unreadable, starting empty: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"judge-cache {path.name} is not an object, starting empty"]
    cache: dict[str, dict] = {}
    bad = 0
    for key, entry in data.items():
        if not _current(key):
            continue
        verdict = valid_verdict(
            {k: v for k, v in entry.items() if k != "at"} if isinstance(entry, dict) else None
        )
        if verdict is None:
            bad += 1
            continue
        cache[key] = {**verdict, "at": str(entry.get("at", ""))}
    warnings = [f"judge-cache: {bad} entries off the schema skipped"] if bad else []
    return cache, warnings


def save_cache(path: Path, cache: Mapping[str, dict]) -> str | None:
    """Atomic write via a unique temp file; old versions dropped (§11.3)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in cache.items() if _current(k)}
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, prefix=".judge-cache-", delete=False
        ) as tmp:
            json.dump(keep, tmp, ensure_ascii=False, sort_keys=True)
        os.replace(tmp.name, path)
    except OSError as exc:
        return f"judge-cache {path.name} not saved: {exc}"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — кэш вердиктов, валидация записей, атомарная запись (§11.3)"
```

---

### Task 5: `run_judge` — потолок, кэш, параллель, статус; применение

**Files:**
- Modify: `selfcheck/judge.py`
- Test: `tests/selfcheck/test_judge.py`

**Interfaces:**
- Produces: `JudgeRun(verdicts: dict[str, dict], not_judged: list[dict[str, str]], result: ProbeResult, warnings: list[str], calls: int, cached: int, errors: int, candidates: int)`; `run_judge(findings, sources, cache_path, *, cap=100, model=DEFAULT_MODEL, which=shutil.which, runner=run_group, workers=4) -> JudgeRun`; `apply_verdicts(findings, verdicts) -> None`; `judge_status(valid: int, errors: int) -> ProbeStatus`.

- [ ] **Step 1: Write the failing tests**

```python
from selfcheck.judge import apply_verdicts, judge_status, run_judge
from selfcheck.probes.base import ProbeStatus


def _counting(verdict: dict | None = None):
    calls: list[str] = []

    def run(argv, **kw):
        calls.append(kw["stdin"].read())
        out = {"structured_output": verdict or GOOD}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    return run, calls


def _llm(tmp_path: Path, n: int) -> tuple[list[Finding], dict[str, Path]]:
    src = _src(tmp_path, {f"m{i}.py": f"x = {i}\n" * 3 for i in range(n)})
    fs = [
        _f("llm-sites/replaceable", Confidence.CANDIDATE, path=f"m{i}.py")
        for i in range(n)
    ]
    return fs, src


def _claude(_: str) -> str:
    return "claude"


def test_cap_takes_the_first_in_order_and_cache_descends(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": FUNC})
    fs = [
        _f("jscpd/clone", Confidence.LIKELY, line=5, lines=3),
        _f("llm-sites/replaceable", Confidence.CANDIDATE, line=55),
        _f("ast-dup/structural", Confidence.CANDIDATE, line=51),
    ]
    for f in fs:
        if f.related:
            f.related = [{"owner_repo": "a", "path": "x.py", "line": f.locations[0].line,
                          "member": "m"}]
    cache = tmp_path / "judge-cache.json"
    run, calls = _counting()
    first = run_judge(fs, src, cache, cap=2, which=_claude, runner=run)
    order = [f.id for f in sorted(fs, key=order_key)]
    assert list(first.verdicts) == order[:2]
    assert [x["id"] for x in first.not_judged] == order[2:]
    assert (first.calls, first.cached, first.result.status) == (2, 0, ProbeStatus.OK)
    second = run_judge(fs, src, cache, cap=2, which=_claude, runner=run)
    assert (second.calls, second.cached, len(second.not_judged)) == (1, 2, 0)
    assert len(calls) == 3


def test_changed_file_or_model_is_a_new_call(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 1)
    cache = tmp_path / "c.json"
    run, calls = _counting()
    run_judge(fs, src, cache, which=_claude, runner=run)
    run_judge(fs, src, cache, which=_claude, runner=run)
    assert len(calls) == 1
    (src["a"] / "m0.py").write_text("x = 99\n")
    run_judge(fs, src, cache, which=_claude, runner=run)
    run_judge(fs, src, cache, model="other-model", which=_claude, runner=run)
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("valid", "errors", "status"),
    [
        (3, 0, ProbeStatus.OK),
        (3, 5, ProbeStatus.PARTIAL),
        (1, 1, ProbeStatus.PARTIAL),
        (0, 2, ProbeStatus.FAILED),
        (0, 0, ProbeStatus.OK),
    ],
)
def test_judge_status(valid: int, errors: int, status: ProbeStatus) -> None:
    assert judge_status(valid, errors) is status


def test_cache_hits_plus_missing_source_is_partial(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 3)
    cache = tmp_path / "c.json"
    run, _ = _counting()
    run_judge(fs, src, cache, which=_claude, runner=run)
    gone = _f("llm-sites/replaceable", Confidence.CANDIDATE, path="gone.py")
    res = run_judge([*fs, gone], src, cache, which=_claude, runner=run)
    assert res.cached == 3 and res.calls == 0
    assert res.verdicts[gone.id]["detail"] == "source-missing"
    assert res.result.status is ProbeStatus.PARTIAL


def test_errors_not_cached_and_all_errors_is_failed(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 2)

    def flaky(argv, **kw):
        ok = "x = 0" in kw["stdin"].read()
        out = {"structured_output": GOOD} if ok else {"is_error": True, "result": "x"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    part = run_judge(fs, src, tmp_path / "c.json", which=_claude, runner=flaky)
    assert part.result.status is ProbeStatus.PARTIAL
    assert len(load_cache(tmp_path / "c.json")[0]) == 1

    def broken(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "nope", "")

    fs2, src2 = _llm(tmp_path / "b", 2)
    failed = run_judge(fs2, src2, tmp_path / "d.json", which=_claude, runner=broken)
    assert failed.result.status is ProbeStatus.FAILED


def test_no_binary_is_unavailable_and_cache_still_applies(tmp_path: Path) -> None:
    fs, src = _llm(tmp_path, 2)
    cache = tmp_path / "c.json"
    run, calls = _counting()
    run_judge(fs[:1], src, cache, which=_claude, runner=run)
    res = run_judge(fs, src, cache, which=lambda _: None, runner=run)
    assert res.result.status is ProbeStatus.UNAVAILABLE and len(calls) == 1
    assert res.cached == 1 and [x["id"] for x in res.not_judged] == [fs[1].id]


def test_truncated_is_carried(tmp_path: Path) -> None:
    src = _src(tmp_path, {"x.py": "x = 1\n" * 40000})
    f = _f("jscpd/clone", Confidence.LIKELY, line=1, lines=30000)
    f.related = [{"owner_repo": "a", "path": "x.py", "line": 1, "member": "1"}]
    run, _ = _counting()
    res = run_judge([f], src, tmp_path / "c.json", which=_claude, runner=run)
    assert res.verdicts[f.id]["truncated"] is True


def test_apply_never_confirmed_nor_new_id() -> None:
    cand = _f("llm-sites/replaceable", Confidence.CANDIDATE)
    keep = _f("jscpd/clone", Confidence.LIKELY, lines=9)
    confirmed = _f("ast-dup/exact", Confidence.CONFIRMED)
    ids = (cand.id, keep.id, confirmed.id)
    apply_verdicts(
        [cand, keep, confirmed],
        {
            cand.id: GOOD,
            keep.id: {**GOOD, "verdict": "keep", "replacement": "none"},
            confirmed.id: GOOD,
        },
    )
    assert cand.confidence is Confidence.LIKELY and keep.confidence is Confidence.LIKELY
    assert confirmed.confidence is Confidence.CONFIRMED  # never lowered
    assert (cand.id, keep.id, confirmed.id) == ids
    assert cand.judge and cand.judge["verdict"] == "replace"
```

(`order_key` — импорт из Task 1 в шапке файла.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_verdicts'`.

- [ ] **Step 3: Implement**

```python
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
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
    errors: int = 0
    candidates: int = 0


def judge_status(valid: int, errors: int) -> ProbeStatus:
    """ok / partial (errors and ≥ 1 valid) / failed (errors, none valid) (§11.6)."""
    if not errors:
        return ProbeStatus.OK
    return ProbeStatus.PARTIAL if valid else ProbeStatus.FAILED


def _row(status: ProbeStatus, model: str, reason: str) -> ProbeResult:
    res = ProbeResult("judge", "devtools", status, reason)
    res.tool_version = f"judge v{JUDGE_VERSION} / {model}"
    return res


def run_judge(
    findings: list[Finding],
    sources: Mapping[str, Path],
    cache_path: Path,
    *,
    cap: int = 100,
    model: str = DEFAULT_MODEL,
    which: Callable[[str], str | None] = shutil.which,
    runner: Runner = run_group,
    workers: int = WORKERS,
) -> JudgeRun:
    """Judge candidates in the normative order under ``cap`` new calls (§11)."""
    cache, warnings = load_cache(cache_path)
    run = JudgeRun({}, [], _row(ProbeStatus.OK, model, ""), warnings)
    binary = which("claude")
    todo: list[tuple[Finding, JudgeSlice, str]] = []
    for f in sorted((f for f in findings if is_candidate(f)), key=order_key):
        run.candidates += 1
        sl = build_slice(f, sources)
        if sl is None:
            run.verdicts[f.id] = _error("source-missing")
            run.errors += 1
            continue
        key = cache_key(sl.text, model)
        if key in cache:
            entry = {k: v for k, v in cache[key].items() if k != "at"}
            run.verdicts[f.id] = {
                **entry, "cached": True, "model": model, "truncated": sl.truncated
            }
            run.cached += 1
        elif binary is not None and len(todo) < cap:
            todo.append((f, sl, key))
        else:
            run.not_judged.append({"id": f.id, "rule": f.rule})
    if binary is None:
        run.result = _row(ProbeStatus.UNAVAILABLE, model, "no claude on PATH")
        return run
    stamp = datetime.now(UTC).date().isoformat()
    pool = ThreadPoolExecutor(max_workers=workers)
    futures: list[tuple[Finding, JudgeSlice, str, Future[dict[str, str]]]] = []
    try:
        futures.extend(
            (f, sl, key, pool.submit(call_judge, binary, sl.text, model, runner=runner))
            for f, sl, key in todo
        )
        for f, sl, key, fut in futures:
            out = fut.result()
            run.verdicts[f.id] = {
                **out, "cached": False, "model": model, "truncated": sl.truncated
            }
            run.calls += 1
            if out["verdict"] == "error":
                run.errors += 1
                continue
            cache[key] = {**out, "at": stamp}
            if warning := save_cache(cache_path, cache):  # keep paid verdicts early
                run.warnings.append(warning)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        for _f, _sl, key, fut in futures:
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                out = fut.result()
                if out["verdict"] != "error":
                    cache.setdefault(key, {**out, "at": stamp})
        if warning := save_cache(cache_path, cache):  # Ctrl-C keeps finished verdicts
            run.warnings.append(warning)
    valid = run.cached + run.calls - (run.errors - _missing(run))
    run.result = _row(
        judge_status(valid, run.errors), model, f"{run.errors} errors"
    )
    return run


def _missing(run: JudgeRun) -> int:
    return sum(1 for v in run.verdicts.values() if v.get("detail") == "source-missing")


def apply_verdicts(findings: list[Finding], verdicts: Mapping[str, dict]) -> None:
    """Only ``judge`` and confidence change; never ``confirmed``, never lowered."""
    for f in findings:
        v = verdicts.get(f.id)
        if v is None:
            continue
        f.judge = dict(v)
        if v["verdict"] == "replace" and f.confidence is Confidence.CANDIDATE:
            f.confidence = Confidence.LIKELY
```

(`valid` — число валидных вердиктов: из кэша плюс новые вызовы без ошибки; ошибки новых вызовов = `errors − source-missing`. `save_cache` после каждого валидного вердикта и ещё раз в `finally` с вердиктами уже завершённых вызовов — Ctrl-C их не теряет; `cancel_futures=True` — не начатые вызовы не запускаются; уже запущенные дорабатывают (SIGINT получает только главный поток, §11.4).)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/judge.py tests/selfcheck/test_judge.py
git commit -m "feat(selfcheck): судья — потолок, кэш, параллель, статус; применение вердиктов (§11.2–11.6)"
```

---

### Task 6: `--judge` в прогоне, дельте и отчёте

**Files:**
- Modify: `selfcheck/run.py`, `selfcheck/delta.py:134-141`, `selfcheck/report.py`, `Makefile:60`
- Modify (tests helpers): `tests/selfcheck/test_run.py:73` (`reports`), `tests/selfcheck/test_fleet_run.py:36`, `tests/selfcheck/test_zone_narrow.py:240,269`
- Test: `tests/selfcheck/test_judge_run.py` (новый)

**Interfaces:**
- Consumes: `run_judge`, `apply_verdicts`, `instrument_findings`, `JUDGE_VERSION`.
- Produces: флаги `--judge`, `--judge-max N`, `--judge-model M`; `acc.keys["judge@devtools"]`; `report.json` `judge: {model, calls, cached, errors, candidates, not_judged}`; раздел `report.md` «## Судья».

- [ ] **Step 1: Write the failing tests**

Хелперы отчётов перестают считать каталогом любой элемент `out/` (там теперь лежит `judge-cache.json`): в `test_run.py:73` и `test_fleet_run.py:36` — `(p for p in (ws / "out").iterdir() if p.is_dir())`; в `test_zone_narrow.py:240,269` — `(run,) = [p for p in (ws / "out").iterdir() if p.is_dir()]`.

`tests/selfcheck/test_judge_run.py`:

```python
"""S5 — --judge wired into the run, the delta and the report (§11.6, §11.7)."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from selfcheck import judge as judge_module
from selfcheck.run import main
from tests.selfcheck.helpers import make_repo, require_tool, workspace
from tests.selfcheck.test_run import args, reports

CLASSIFY = (
    "import json\nimport subprocess\n\n\ndef classify(items):\n    out = []\n"
    "    for item in items:\n"
    '        raw = subprocess.run(["claude", "-p", f"label {item}"],\n'
    "                             capture_output=True, text=True).stdout\n"
    '        out.append(json.loads(raw)["label"])\n    return out\n'
)
GOOD = {"rationale": "r", "verdict": "replace", "replacement": "rules"}


def fake_claude(tmp: Path, answer: object) -> Path:
    """A `claude` that logs argv/env/stdin and prints ``answer`` (a dict or raw)."""
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp / "calls.jsonl"
    body = json.dumps({"structured_output": answer}) if isinstance(answer, dict) else answer
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv, "
        "'env': sorted(os.environ), 'stdin': sys.stdin.read()}) + '\\n')\n"
        f"print({body!r})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _calls(tmp: Path) -> list[dict]:
    log = tmp / "calls.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _use(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("claude") == str(bin_dir / "claude")  # never the real one


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    require_tool("uvx")
    _use(monkeypatch, fake_claude(tmp_path, GOOD))
    monkeypatch.setenv("GH_TOKEN", "must-not-leak")
    return workspace(tmp_path, {"c.py": CLASSIFY})


def _judge_row(doc: dict) -> dict:
    return next(p for p in doc["probes"] if p["probe"] == "judge")


def test_no_judge_flag_no_harness_call(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites"])
    assert _calls(tmp_path) == []
    assert all(p["probe"] != "judge" for p in reports(ws)[-1]["probes"])


def test_judge_runs_once_and_caches(ws: Path, tmp_path: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    (call,) = _calls(tmp_path)
    assert "--restricted" in call["argv"] and "GH_TOKEN" not in call["env"]
    assert "def classify" in call["stdin"]
    doc = reports(ws)[-1]
    (f,) = [x for x in doc["findings"] if x["rule"] == "llm-sites/replaceable"]
    assert f["judge"]["verdict"] == "replace" and f["confidence"] == "likely"
    assert _judge_row(doc)["status"] == "ok"
    main([*args(ws), "--probe", "llm-sites", "--judge"])
    assert len(_calls(tmp_path)) == 1
    assert reports(ws)[-1]["judge"]["cached"] == 1


def test_judge_max_zero_lists_not_judged(ws: Path) -> None:
    main([*args(ws), "--probe", "llm-sites", "--judge", "--judge-max", "0"])
    doc = reports(ws)[-1]
    assert [x["rule"] for x in doc["judge"]["not_judged"]] == ["llm-sites/replaceable"]
    md = next(p for p in (ws / "out").iterdir() if p.is_dir()) / "report.md"
    assert "не судились 1 из 1" in md.read_text()


def test_judge_error_exit_2_then_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 2
    doc = reports(ws)[-1]
    assert _judge_row(doc)["status"] == "failed"
    assert any(f["anchor"] == "probe:devtools#judge" for f in doc["findings"])
    good = tmp_path / "good"
    good.mkdir()
    _use(monkeypatch, fake_claude(good, GOOD))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 0
    gone = reports(ws)[-1]["delta"]["gone"]
    assert {"anchor": "probe:devtools#judge", "status": "resolved"}.items() <= next(
        g for g in gone if g["anchor"] == "probe:devtools#judge"
    ).items()


def test_judge_finding_resolves_outside_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11.6: the judge is run-level — an ok --judge run resolves it even when
    devtools is not in scope (mutation guard for delta `run_level`)."""
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    make_repo(tmp_path / "other", {"o.py": "x = 1\n"})
    (ws / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    _use(monkeypatch, fake_claude(tmp_path, "not json"))
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) == 2
    good = tmp_path / "good"
    good.mkdir()
    _use(monkeypatch, fake_claude(good, GOOD))
    main([*args(ws), "--repo", "other", "--probe", "llm-sites", "--judge"])
    gone = {g["anchor"]: g["status"] for g in reports(ws)[-1]["delta"]["gone"]}
    assert gone["probe:devtools#judge"] == "resolved"


def test_keep_is_shown_under_keep_not_in_its_category() -> None:
    from selfcheck.report import render_markdown

    keep = {
        "id": "sc-keep0001",
        "rule": "jscpd/clone",
        "category": "duplicate",
        "confidence": "likely",
        "anchor": "dup:text:keepme",
        "occurrences": 1,
        "owner_repo": "a",
        "judge": {"verdict": "keep", "replacement": "none", "rationale": "on purpose"},
    }
    doc = {
        "run": {
            "run_id": "r", "host": "h", "scope": ["a"], "surface": {}, "env": {},
            "warnings": [], "manifest": {"entries_read": 1, "repos": ["a"], "missing": []},
        },
        "probes": [],
        "findings": [keep],
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {"llm": []},
        "judge": {"model": "m", "calls": 1, "cached": 0, "errors": 0, "candidates": 1,
                  "not_judged": []},
    }
    text = render_markdown(doc)
    assert "### Судья: оставить" in text and "on purpose" in text
    assert "## duplicate" not in text


def test_missing_claude_is_unavailable_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tool("uvx")
    ws = workspace(tmp_path, {"c.py": CLASSIFY})
    only = tmp_path / "only"
    only.mkdir()
    for tool in ("uv", "uvx", "git"):
        found = shutil.which(tool)
        assert found, tool
        (only / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(only))
    assert shutil.which("claude") is None  # the real claude is unreachable
    monkeypatch.setattr(judge_module.shutil, "which", lambda _: None)  # belt and braces
    code = main([*args(ws), "--probe", "llm-sites", "--judge"])
    assert _judge_row(reports(ws)[-1])["status"] == "unavailable"
    assert code == 3


def test_broken_cache_is_a_warning_not_exit_4(ws: Path) -> None:
    (ws / "out").mkdir(exist_ok=True)
    (ws / "out" / "judge-cache.json").write_text("{broken")
    assert main([*args(ws), "--probe", "llm-sites", "--judge"]) in (0, 2)
    assert any("judge-cache" in w for w in reports(ws)[-1]["run"]["warnings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck/test_judge_run.py -q`
Expected: FAIL — `unrecognized arguments: --judge` (`SystemExit: 2`); `test_no_judge_flag_no_harness_call` проходит уже сейчас (двойник).

- [ ] **Step 3: Implement**

`run.py` — импорты `from selfcheck.judge import DEFAULT_MODEL, JUDGE_VERSION, apply_verdicts, run_judge`; `_args`:

```python
    parser.add_argument("--judge", action="store_true", help="LLM judge (spec §5, §11)")
    parser.add_argument("--judge-max", type=int, default=100)
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
```

`_Run` — `judge: dict[str, Any] = field(default_factory=dict)`. В `main` сразу после `final = aggregate([...])`:

```python
    if args.judge:
        final = _run_judge_pass(final, args, known, acc)
```

```python
def _run_judge_pass(
    final: list[Finding],
    args: argparse.Namespace,
    known: dict[str, RepoEntry],
    acc: _Run,
) -> list[Finding]:
    """The judge over the final findings; its status is a probe row (§11.6)."""
    jr = run_judge(
        final,
        {name: entry.path for name, entry in known.items()},
        args.out / "judge-cache.json",
        cap=args.judge_max,
        model=args.judge_model,
    )
    apply_verdicts(final, jr.verdicts)
    acc.results.append(jr.result)
    ok = jr.result.status is ProbeStatus.OK
    acc.keys["judge@devtools"] = f"judge v{JUDGE_VERSION} / {args.judge_model}" if ok else None
    acc.warnings += jr.warnings
    acc.judge = {
        "model": args.judge_model,
        "calls": jr.calls,
        "cached": jr.cached,
        "errors": jr.errors,
        "candidates": jr.candidates,
        "not_judged": jr.not_judged,
    }
    return aggregate([*final, *instrument_findings([jr.result])])
```

`_document` — ключ `"judge": acc.judge`.

`delta.py` `_instrument_status` — судья run-level (§11.6):

```python
    run_level = probe == "judge"  # not about a repo of the scope (§11.6)
    return "resolved" if (run_level or repo in cur.scope) and ran else "not-rechecked"
```

`report.py`:

```python
def _judge_lines(doc: dict[str, Any]) -> list[str]:
    """Judge summary, the «keep» section and what was not judged (§11.2, §11.6)."""
    j = doc.get("judge") or {}
    if not j:
        return []
    judged = [f for f in doc["findings"] if f.get("judge")]
    valid = [f for f in judged if f["judge"].get("verdict") != "error"]
    keep = [f for f in valid if f["judge"]["verdict"] == "keep"]
    lines = [
        "## Судья",
        "",
        (
            f"модель {j['model']}: вердиктов {len(valid)} (из кэша {j['cached']}, "
            f"новых вызовов {j['calls']}, ошибок {j['errors']}); "
            f"не судились {len(j['not_judged'])} из {j['candidates']} кандидатов"
        ),
    ]
    if keep:
        lines += [
            "",
            "### Судья: оставить",
            "",
            "| правило | якорь | обоснование |",
            "|---|---|---|",
        ]
        lines += [
            f"| {f['rule']} | `{f['anchor']}` | "
            + f["judge"].get("rationale", "")[:160].replace("|", "/").replace("\n", " ")
            + " |"
            for f in keep[:MD_ROWS]
        ]
    if j["not_judged"]:
        by_rule = dict(Counter(x["rule"] for x in j["not_judged"]))
        lines += ["", f"не судились по правилам: {by_rule}; первые 20:"]
        lines += [f"- {x['rule']} `{x['id']}`" for x in j["not_judged"][:20]]
    return [*lines, ""]
```

В `render_markdown`: в цикле категорий `items = [f for f in items if (f.get("judge") or {}).get("verdict") != "keep"]` и `continue`, если после фильтра пусто; `lines += _judge_lines(doc)` перед «## Подавлено». `Makefile:60` — `[--judge [--judge-max N] [--judge-model M]]` в help.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen --group selfcheck pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/run.py selfcheck/delta.py selfcheck/report.py Makefile tests/selfcheck/
git commit -m "feat(selfcheck): --judge в прогоне, дельте и отчёте — строка judge, кэш, не судившиеся (§11.6)"
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
Expected: всё зелёное (pyrefly — только известная `test_run.py:116`), дерево чистое.

- [ ] **Step 3: Приёмка §11.7 после мержа**

На master devtools: `make selfcheck ARGS='--all --judge'`, затем второй такой же прогон. Скрипт сверки (scratchpad) по `report.json`:
1. строка `judge` — `ok`; вердикты у всех 57 кандидатов не-jscpd и у крупнейших клонов jscpd до потолка (порядок §11.2);
2. у `replace` встречаются конкретные замены (не только `none`);
3. ручная выборка 10 вердиктов (≥ 3 `replace`, ≥ 3 `keep`): согласие вердикта с обоснованием и с кодом — таблица в отчёте приёмки;
4. второй прогон: `judge.cached` ≥ числа валидных вердиктов первого, новые вызовы — следующие по порядку клоны jscpd.

Отчёт — `_cowork_output/devtools-selfcheck-s5-acceptance-<дата>.md`; затем PR: `TODO.md` `selfcheck-s5` [x].
