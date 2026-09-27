# selfcheck S3 — все репо манифеста (`--all`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** один прогон `make selfcheck ARGS='--all --fleet'` по всем репо манифеста с честными статусами: формат вендор-деклараций E, `llm-sites` по месту построения запуска, editable-пути для pyrefly, межрепные ast-дубли без вендор-групп, ruff без `PLR2004`/`PLC0415`, cargo-machete, allowlist по репо.

**Architecture:** правки внутри существующих модулей `selfcheck/` (vendor, roles, env, python_tools, llm + правила semgrep, dups, config, run, report) и одна новая проба `cargo-machete` в `other_tools.py`. Межрепная стадия дублей — функция `group_dups` в `dups.py`, её зовёт `run.main` после цикла по репо. Новых пакетов и зависимостей нет.

**Tech Stack:** Python 3.12+, pytest, semgrep 1.178.0 (`uvx`), pyrefly, ruff 0.16, cargo-machete (системный бинарь, ставит владелец).

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md` rev 5.9, §10 (плюс правки §1.1, §1.4, §2.4, §3.1, §3.4, §7, §9.7). Замер — dev-only `../_cowork_output/devtools-selfcheck-s3-measure-2026-09-27.md`. Ревью пары r1 — `../_cowork_output/devtools-selfcheck-s3-pair-review-r1-2026-09-27.md`.

## Global Constraints

- Пробы S1–S3 не исполняют код цели; `executes_target_code = false` у всех, включая cargo-machete (§1.2).
- Окружение цели — только данные: `.pth` читаются как текст, строки `import …` не исполняются и никуда не передаются (§1.5, §10.4).
- Пробы получают только путь к read-only копии (§1.3); путь из `.pth` вне checkout или отсутствующий в копии не передаётся.
- `logic_version` повышается (§4.3): `llm-sites` 1 → 2, `ast-dup` 1 → 2, `usage-graph` 3 → 4.
- Формат E (§10.5): ровно одна строка `source:`/`repo:`; ref `[0-9a-f]{7,40}`; E в корне репо — неразобран; строка-член (с sha256) с путём корпуса вне каталога E — неразобран, пути в прозе не считаются; члены — файлы корпуса каталога и ниже, **не узлы графа**, кроме самой декларации; узел — член, только если назван строкой-членом; нет членов — `vendor-pin-dangling`, `text_key` = каталог.
- Файлы под `.github/` — не кандидаты в декларации; роль `test` дополняется `test/**`, `**/*_test.exs` (§10.5).
- Вендор-группа дублей (§10.6): есть участник `vendored-in`, вне роли ≤ 1 участника, и он в репо — владельце из деклараций копий группы.
- При одном репо `id`, `owner_repo`, `anchor`, `locations`, `related` дублей совпадают с эталоном S1 (Task 6).
- `llm-sites` (§10.7): точка — кандидат, только если кандидат каждая её строка; `argv-literal` считает невидимость промпта как `py-launch`; `$SECOND` у `argv-literal` — флаг `-x`/`--x…` или `exec`/`run`; `endpoint` без пробела до пути; Python — строка кода целиком (не docstring, не кусок f-строки); TS — якорь `llm:<path>`, только инвентарь; файлы > 1 000 000 байт не передаются.
- `PLR2004`, `PLC0415` не приходят из добавочного набора ruff, если корневой конфиг репо их не включает (§10.3).
- `[[allow]] repo` — точное совпадение с `git_dir`, неизвестный — код 4 (§10.8); `--all` с `--repo` — код 4 (§10.2).
- Живая приёмка §10.9 — после мержа, на master devtools с чистым деревом.
- `uv run`, не pip; после каждой задачи `uv run pytest tests/selfcheck -q`, `uv run ruff format`/`check`, `uv run pyrefly check`; строка ≤ 88.

## Review Focus

1. **Формат E снимает dead.** Код рядом с E без упоминания обязан остаться узлом без роли (Task 1 `test_e_members_are_folder_non_code`, `test_e_code_member_only_when_named`).
2. **E теряет копии вне каталога.** Строка-член с путём вне каталога — неразобрана; проза с путём — нет (Task 1 `test_e_member_line_outside_folder_is_unparsed`, `test_e_prose_path_outside_folder_still_parses`).
3. **Межрепная группа ломает идентичность S1.** Эталонные значения сняты текущим кодом до рефакторинга (Task 6 `test_single_repo_matches_s1_golden`).
4. **`.pth` без каталога в копии роняет pyrefly.** Такой путь не передаётся (Task 3 `test_pyrefly_skips_search_path_missing_in_copy`).
5. **`endpoint` ловит прозу.** Текст с путём, docstring, комментарий, кусок f-строки — не точка (Task 5 двойники; S1 `test_inventory_mechanisms` с `e.py`).

---

### Task 1: формат E, кандидаты вне `.github/`, роль `test` для Elixir

**Files:**
- Modify: `selfcheck/vendor.py`, `selfcheck/roles.py:40`, `selfcheck/graph/probe.py` (`logic_version=4`)
- Test: `tests/selfcheck/test_vendor.py`, `tests/selfcheck/test_config_manifest.py`, `tests/selfcheck/test_zone_narrow.py:209-210`, `tests/selfcheck/test_fleet_run.py:119-122`

**Interfaces:**
- Produces: `Declaration(path, fmt, owner, ref, members, folder: str | None = None)`; для E `members=()` и `folder` — каталог. `vendor_roles(repo, corpus, texts, role, node_paths)` раскрывает членов E по корпусу. `is_candidate` — `False` для `.github/**`.

- [ ] **Step 1: Write the failing tests**

В `tests/selfcheck/test_vendor.py` (импорты `vendor_roles`, `role_of` уже есть):

```python
FORMAT_E_AT = (
    "source: impresario@8082e53b743169137f9e8c72c279043c7166ab03 contracts/idea/v1\n"
    "vendored: 2026-08-16\n"
    "purpose: consumer copy (design doc §7)\n"
    f"sha256 fixtures/valid/idea-001.yaml: {H}\n"
    f"sha256 schema.json: {H}\n"
)
FORMAT_E_COMMIT = (
    "source: impresario contracts/loop-state/v1\n"
    "commit: a9d11fa75bb101d2919dc9f99e075270de5d7976\n"
    "vendored: 2026-08-17\n"
    "note: pinned copy (repo-boundaries vendoring). Do not edit here —\n"
    "re-vendor from canon and update this header.\n"
)
FORMAT_E_REPO = (
    "repo: github.com/andrei-shtanakov/maestro\n"
    "commit: 346222e3b\n"
    "note: schema bytes unchanged vs pinned commit\n"
)
FORMAT_E_HASH = (
    "# VENDORED PINNED COPY — do not hand-edit the files under this directory.\n"
    "#\n"
    "# source: devtools@2533ff7b8c3afd74110b3838325bf76ba46ba186 contracts/x/v1\n"
    "# vendored: 2026-08-18\n"
)


@pytest.mark.parametrize(
    ("rel", "text", "owner", "ref"),
    [
        (
            "priv/c/idea/v1/PIN",
            FORMAT_E_AT,
            "impresario",
            "8082e53b743169137f9e8c72c279043c7166ab03",
        ),
        (
            "contracts/ls/v1/PINNED.txt",
            FORMAT_E_COMMIT,
            "impresario",
            "a9d11fa75bb101d2919dc9f99e075270de5d7976",
        ),
        ("contracts/mv/VENDORED_FROM", FORMAT_E_REPO, "maestro", "346222e3b"),
        (
            "core/tests/fx/v1/PIN",
            FORMAT_E_HASH,
            "devtools",
            "2533ff7b8c3afd74110b3838325bf76ba46ba186",
        ),
    ],
)
def test_format_e(rel: str, text: str, owner: str, ref: str) -> None:
    decl = parse_declaration(rel, text)
    folder = rel.rsplit("/", 1)[0]
    assert (decl.fmt, decl.owner, decl.ref, decl.members, decl.folder) == (
        "E",
        owner,
        ref,
        (),
        folder,
    )


@pytest.mark.parametrize(
    "text",
    [
        "source: impresario contracts/x/v1\ncommit: main\n",  # ref not hex
        "source: a@abcdef1 p\nsource: b@abcdef2 q\n",  # two source lines
        "source: impresario contracts/x/v1\nvendored: 2026-08-17\n",  # no ref
        "repo: github.com/o/r\n",  # repo without commit
    ],
)
def test_e_unparsed(text: str) -> None:
    with pytest.raises(DeclarationError):
        parse_declaration("contracts/x/v1/PINNED.txt", text)


def test_uppercase_source_stays_format_a() -> None:
    with pytest.raises(DeclarationError, match="no members"):
        parse_declaration("x/PIN", "# SOURCE: steward @ 5bfd829\n")


E_TEXTS = {
    "contracts/ls/v1/PINNED.txt": FORMAT_E_COMMIT,
    "contracts/ls/v1/schema.json": "{}",
    "contracts/ls/v1/fixtures/ok.json": "{}",
    "contracts/ls/v1/helper.py": "def f():\n    return 1\n",
    "contracts/ls/v2/schema.json": "{}",  # sibling folder: not a member
    "tools/check.py": "import json\n",
}
E_NODES = frozenset({"tools/check.py", "contracts/ls/v1/helper.py"})


def test_e_members_are_folder_non_code() -> None:
    res = vendor_roles("r", sorted(E_TEXTS), E_TEXTS, role_of, E_NODES)
    assert sorted(res.members) == [
        "contracts/ls/v1/fixtures/ok.json",
        "contracts/ls/v1/schema.json",
    ]
    assert res.findings == [] and res.broken is False


def _with(line: str) -> dict[str, str]:
    return {**E_TEXTS, "contracts/ls/v1/PINNED.txt": FORMAT_E_COMMIT + line}


def test_e_code_member_only_when_named() -> None:
    texts = _with(f"sha256 helper.py: {H}\n")  # the fleet's `sha256 <path>:` form
    res = vendor_roles("r", sorted(texts), texts, role_of, E_NODES)
    assert "contracts/ls/v1/helper.py" in res.members


def test_e_prose_does_not_name_code() -> None:
    texts = _with("note: helper.py is ours\n")
    res = vendor_roles("r", sorted(texts), texts, role_of, E_NODES)
    assert "contracts/ls/v1/helper.py" not in res.members


@pytest.mark.parametrize(
    "line", [f"{H}  tools/check.py\n", f"sha256 ../../../tools/check.py: {H}\n"]
)
def test_e_member_line_outside_folder_is_unparsed(line: str) -> None:
    texts = _with(line)
    res = vendor_roles("r", sorted(texts), texts, role_of, E_NODES)
    assert [f.rule for f in res.findings] == ["selfcheck/vendor-pin-unparsed"]
    assert "tools/check.py" in res.protected and res.broken is True


def test_e_prose_path_outside_folder_still_parses() -> None:
    texts = _with("note: consumed by tools/check.py; see TODO.md\n")
    res = vendor_roles("r", sorted(texts), texts, role_of, E_NODES)
    assert res.findings == [] and "contracts/ls/v1/schema.json" in res.members


def test_e_in_repo_root_is_unparsed_finding() -> None:
    texts = {"PIN": "source: o@abcdef1 p\n", "a.json": "{}"}
    res = vendor_roles("r", sorted(texts), texts, role_of, frozenset())
    assert [f.rule for f in res.findings] == ["selfcheck/vendor-pin-unparsed"]
    assert res.broken is True


def test_e_folder_with_only_unnamed_code_is_dangling() -> None:
    texts = {
        "contracts/c/v1/PINNED.txt": FORMAT_E_COMMIT,
        "contracts/c/v1/local.py": "x = 1\n",
    }
    nodes = frozenset({"contracts/c/v1/local.py"})
    res = vendor_roles("r", sorted(texts), texts, role_of, nodes)
    assert [(f.rule, f.text_key) for f in res.findings] == [
        ("selfcheck/vendor-pin-dangling", "contracts/c/v1")
    ]
    assert "contracts/c/v1/local.py" not in res.members


def test_e_folder_without_members_is_dangling() -> None:
    texts = {"contracts/gone/v1/PINNED.txt": FORMAT_E_COMMIT, "a.py": "x = 1\n"}
    res = vendor_roles("r", sorted(texts), texts, role_of, frozenset({"a.py"}))
    assert [(f.rule, f.text_key) for f in res.findings] == [
        ("selfcheck/vendor-pin-dangling", "contracts/gone/v1")
    ]
    assert res.broken is True


@pytest.mark.parametrize("rel", [".github/workflows/vendor-drift.yml", ".github/PIN"])
def test_github_files_are_not_candidates(rel: str) -> None:
    assert is_candidate(rel, "name: x\n", is_node=False) is False
```

В `tests/selfcheck/test_config_manifest.py` — в параметризацию `test_default_roles`:

```python
        ("test/contracts/vendored_test.exs", Role.TEST),
        ("apps/a/test/a_test.exs", Role.TEST),
        ("test/support/fixtures/x.json", Role.TEST),
        ("lib/kapelle/test_helper.ex", Role.SOURCE),
```

`tests/selfcheck/test_zone_narrow.py:209` — переименовать в `test_usage_graph_logic_version_is_4`, `== 4`; `tests/selfcheck/test_fleet_run.py:122` — `== 4  # format E and Elixir test role (rev 5.9)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_vendor.py tests/selfcheck/test_config_manifest.py tests/selfcheck/test_zone_narrow.py tests/selfcheck/test_fleet_run.py -q`
Expected: FAIL — `test_format_e` (`DeclarationError`), `test_e_members_are_folder_non_code`, `test_e_code_member_only_when_named`, `test_e_prose_path_outside_folder_still_parses`, оба `…_dangling`, `test_github_files_are_not_candidates`, новые строки `test_default_roles`, два теста `logic_version` (`3 != 4`). Проходят уже сейчас (двойники): `test_e_unparsed`, `test_uppercase_source_stays_format_a`, `test_e_in_repo_root_is_unparsed_finding`, `test_e_member_line_outside_folder_is_unparsed` (текст не разбирается как B), `test_e_prose_does_not_name_code`.

- [ ] **Step 3: Implement**

`selfcheck/roles.py`:

```python
    Role.TEST: (
        "tests/**",
        "**/test_*.py",
        "**/*_test.py",
        "test/**",
        "**/*_test.exs",
    ),
```

`selfcheck/vendor.py` — в docstring модуля строка «- E ``key: value`` prose with ``source:``/``repo:`` + a hex ref; members are the declaration's folder (spec §10.5).»; константы:

```python
_E_LINE = re.compile(r"^(?:#\s*)?([A-Za-z_]+):\s*(.*)$")
_E_SOURCE_AT = re.compile(rf"^(\S+?)@({_HEX})\b")
_E_REF = re.compile(rf"^({_HEX})\b")
_E_HEADS = ("source", "repo")
```

`Declaration` — поле `folder: str | None = None`. `is_candidate` — первые строки:

```python
    if rel.startswith(".github/"):
        return False
```

Разбор E:

```python
def _e_keys(text: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for raw in text.splitlines():
        m = _E_LINE.match(raw.strip())
        if m:
            keys.setdefault(m.group(1).lower(), []).append(m.group(2).strip())
    return keys


def _e_owner_ref(rel: str, keys: dict[str, list[str]]) -> tuple[str, str]:
    heads = [(k, v) for k in _E_HEADS for v in keys.get(k, [])]
    if len(heads) != 1:
        raise DeclarationError(f"{rel}: expected one source/repo line")
    kind, head = heads[0]
    at = _E_SOURCE_AT.match(head) if kind == "source" else None
    if at is not None:
        return at.group(1), at.group(2)
    commits = keys.get("commit", [])
    ref = _E_REF.match(commits[0]) if len(commits) == 1 else None
    if ref is None:
        raise DeclarationError(f"{rel}: no hex ref for the folder declaration")
    token = head.split()[0] if head.split() else ""
    owner = (
        posixpath.basename(token.rstrip("/")).removesuffix(".git")
        if kind == "repo"
        else token
    )
    if not owner:
        raise DeclarationError(f"{rel}: no owner")
    return owner, ref.group(1)


def _parse_e(rel: str, text: str) -> Declaration | None:
    keys = _e_keys(text)
    if not any(k in keys for k in _E_HEADS):
        return None
    folder = posixpath.dirname(rel)
    if not folder:
        raise DeclarationError(f"{rel}: folder declaration in the repo root")
    owner, ref = _e_owner_ref(rel, keys)
    return Declaration(rel, "E", owner, ref, (), folder)
```

`parse_declaration`: `return _parse_a(rel, lines) or _parse_c(rel, lines) or _parse_e(rel, text) or _parse_b(rel, lines)`.

Члены E:

```python
_SHA_ANY = re.compile(_SHA256)


def _member_line_paths(folder: str, text: str, known: frozenset[str]) -> set[str]:
    """Corpus paths named on sha256 member lines, from the root or the folder."""
    found: set[str] = set()
    for line in text.splitlines():
        if not _SHA_ANY.search(line):
            continue
        for token in line.split():
            token = token.rstrip(":,")
            for cand in (token, posixpath.join(folder, token)):
                norm = posixpath.normpath(cand)
                if norm in known:
                    found.add(norm)
    return found


def _e_members(
    decl: Declaration, text: str, known: frozenset[str], nodes: frozenset[str]
) -> tuple[str, ...]:
    """Folder members (§10.5): non-code files, plus code named on member lines.

    A member line (one carrying a sha256) naming a corpus path outside the
    folder makes the declaration unparsed; paths in prose do not count."""
    assert decl.folder is not None
    prefix = decl.folder + "/"
    named = _member_line_paths(decl.folder, text, known)
    outside = sorted(p for p in named if not p.startswith(prefix))
    if outside:
        raise DeclarationError(f"{decl.path}: names paths outside its folder: {outside}")
    return tuple(
        p
        for p in sorted(known)
        if p.startswith(prefix) and p != decl.path and (p not in nodes or p in named)
    )
```

В цикле `vendor_roles` разбор и раскрытие E — в одном `try`:

```python
        try:
            decl = parse_declaration(rel, text)
            members = (
                _e_members(decl, text, known, node_paths)
                if decl.folder is not None
                else decl.members
            )
        except DeclarationError:
            res.findings.append(
                _finding(repo, "selfcheck/vendor-pin-unparsed", "high", rel, None)
            )
            res.protected |= _named_paths(rel, text, known)
            continue
        if decl.folder is not None and not members:
            res.findings.append(
                _finding(
                    repo, "selfcheck/vendor-pin-dangling", "medium", rel, decl.folder
                )
            )
            continue
        for member in members:
            ...  # прежнее тело цикла по членам (было `for member in decl.members`)
```

`_finding` — `suggestion="почините декларацию вендоринга (формат A–E, §9.7, §10.5)"`. `graph/probe.py` — `USAGE_GRAPH` `logic_version=4`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком (прежние `test_four_formats`, `test_unparsed`, `test_candidates` зелёные: ни один их вход не содержит `source:`/`repo:` в нижнем регистре).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/vendor.py selfcheck/roles.py selfcheck/graph/probe.py tests/selfcheck/test_vendor.py tests/selfcheck/test_config_manifest.py tests/selfcheck/test_zone_narrow.py tests/selfcheck/test_fleet_run.py
git commit -m "feat(selfcheck): вендор-декларации формата E, .github вне кандидатов, роль test для Elixir (§10.5)"
```

---

### Task 2: ruff без `PLR2004`/`PLC0415`

**Files:**
- Modify: `selfcheck/probes/python_tools.py:20-56`
- Test: `tests/selfcheck/test_python_tools.py`

**Interfaces:**
- Produces: `RUFF_IGNORE = ("PLR2004", "PLC0415")`; `RUFF.rules == (*RUFF_SELECT, "-PLR2004", "-PLC0415")`.

- [ ] **Step 1: Write the failing tests**

```python
MAGIC = "def f(x: int) -> bool:\n    import os\n    return x > 42 and bool(os.sep)\n"


def _ignored(argv: list[str]) -> list[str]:
    if "--extend-ignore" not in argv:
        return []
    return argv[argv.index("--extend-ignore") + 1].split(",")


def test_ruff_extra_set_without_magic_and_lazy_import(build, tmp_path: Path) -> None:
    res = run(RUFF, build({"a.py": MAGIC}), tmp_path)
    assert not {f.rule for f in res.findings} & {"ruff/PLR2004", "ruff/PLC0415"}
    assert _ignored(res.argv) == ["PLR2004", "PLC0415"]


@pytest.mark.parametrize(
    ("tool_ruff", "kept"),
    [
        ('[tool.ruff.lint]\nselect = ["E", "PLR2004"]\n', {"PLR2004"}),
        ('[tool.ruff.lint]\nextend-select = ["PLC0415"]\n', {"PLC0415"}),
        ('[tool.ruff]\nselect = ["PLR2"]\n', {"PLR2004"}),
        (
            '[tool.ruff.lint]\nextend-select = ["PLR2004", "PLC0415"]\n',
            {"PLR2004", "PLC0415"},
        ),
    ],
)
def test_repo_that_selects_them_keeps_them(
    build, tmp_path: Path, tool_ruff: str, kept: set[str]
) -> None:
    pyproject = PYPROJECT.replace("[tool.ruff]\n", "") + tool_ruff
    res = run(RUFF, build({"a.py": MAGIC}, pyproject=pyproject), tmp_path)
    assert not kept & set(_ignored(res.argv))
    assert {f"ruff/{c}" for c in kept} <= {f.rule for f in res.findings}


def test_ruff_rules_name_the_ignores() -> None:
    assert RUFF.rules[-2:] == ("-PLR2004", "-PLC0415")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_python_tools.py -q -k "magic or selects_them or name_the_ignores"`
Expected: FAIL — `ruff/PLR2004` в находках первого теста и `_ignored(...) == []`; `rules` без `-PLR2004`. `test_repo_that_selects_them_keeps_them` проходит уже сейчас (двойник: без флага правило и так приходит).

- [ ] **Step 3: Implement**

```python
RUFF_IGNORE = ("PLR2004", "PLC0415")
# prefixes that come only from our own extra set (§10.3)
_OURS = frozenset({"ALL", "PL", "PLR", "PLC"})


def _repo_selects(ctx: ProbeCtx, code: str) -> bool:
    """A root config of the repo selects ``code`` itself (code or own prefix).

    Nested per-package configs are not read — a named cost (§10.3)."""
    for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        data = _toml(ctx, name)
        root = (
            data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
        )
        lint = root.get("lint", {})
        chosen = [
            *root.get("select", []),
            *root.get("extend-select", []),
            *lint.get("select", []),
            *lint.get("extend-select", []),
        ]
        if any(c not in _OURS and code.startswith(c) for c in chosen):
            return True
    return False


def _ruff_argv(ctx: ProbeCtx) -> list[str]:
    ignore = [c for c in RUFF_IGNORE if not _repo_selects(ctx, c)]
    return [
        "check",
        "--no-cache",
        "--output-format",
        "json",
        "--extend-select",
        ",".join(RUFF_SELECT),
        # hidden in `ruff check --help` 0.16.9 but accepted (review r1 P13)
        *(["--extend-ignore", ",".join(ignore)] if ignore else []),
        *copy_paths(ctx),
    ]
```

`RUFF` — `rules=(*RUFF_SELECT, *(f"-{c}" for c in RUFF_IGNORE))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/probes/python_tools.py tests/selfcheck/test_python_tools.py
git commit -m "feat(selfcheck): ruff без PLR2004/PLC0415 в добавочном наборе (§10.3, решение владельца)"
```

---

### Task 3: editable-пути `.pth` → pyrefly `--search-path`

**Files:**
- Modify: `selfcheck/env.py`, `selfcheck/probes/python_tools.py:131-180`, `selfcheck/run.py:254`
- Test: `tests/selfcheck/test_env.py`, `tests/selfcheck/test_python_tools.py`

**Interfaces:**
- Produces: `EnvInfo.search_paths: tuple[str, ...] = ()` — пути относительно корня checkout (`"."` — сам корень); `editable_paths(repo: Path, site: Path) -> tuple[str, ...]`; `_pyrefly_search(ctx) -> tuple[list[str], list[str]]` — (аргументы, пропущенные пути).

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_env.py`:

```python
def test_editable_paths_are_data(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages" / "core").mkdir(parents=True)
    site = fake_venv(repo)
    marker = tmp_path / "ran"
    (site / "_editable_core.pth").write_text(f"{repo / 'packages' / 'core'}\n")
    (site / "_editable_root.pth").write_text(f"{repo}\n")
    (site / "outside.pth").write_text(f"{tmp_path / 'elsewhere'}\n")
    (site / "hook.pth").write_text(f"import os; open({str(marker)!r}, 'w')\n")
    (site / "rel.pth").write_text("../../../../packages/core\n")
    env = detect_env(repo)
    assert env.search_paths == (".", "packages/core")
    assert not marker.exists()
```

`tests/selfcheck/test_python_tools.py`:

```python
def _search_paths(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--search-path"]


def test_pyrefly_resolves_editable_workspace_member(build, tmp_path: Path) -> None:
    files = {
        "packages/core/corelib/__init__.py": "def f() -> int:\n    return 1\n",
        "app/main.py": "from corelib import f\n\nprint(f())\n",
    }
    target = build(files, venv_marker=tmp_path / "marker")
    site = target.env.site_packages
    assert site is not None
    (site / "_editable_core.pth").write_text(
        f"{target.source / 'packages' / 'core'}\n"
    )
    target = replace(target, env=detect_env(target.source))
    res = run(PYREFLY, target, tmp_path)
    assert "pyrefly/missing-import" not in {f.rule for f in res.findings}
    assert _search_paths(res.argv) == [str(target.copy / "packages" / "core")]


def test_pyrefly_skips_search_path_missing_in_copy(build, tmp_path: Path) -> None:
    target = build({"a.py": "x = 1\n", ".gitignore": ".venv/\nbuild/\n"})
    (target.source / "build").mkdir()
    site = target.env.site_packages
    assert site is not None
    (site / "_editable_b.pth").write_text(f"{target.source / 'build'}\n")
    target = replace(target, env=detect_env(target.source))
    res = run(PYREFLY, target, tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert _search_paths(res.argv) == []
```

(у `build` в этом тесте `.venv` не создаётся без `venv_marker` — передать `venv_marker=tmp_path / "m"`; переданный `.gitignore` перекрывает фикстурный.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_env.py tests/selfcheck/test_python_tools.py -q -k "editable or missing_in_copy"`
Expected: FAIL — `test_editable_paths_are_data` (`AttributeError: … 'search_paths'`), `test_pyrefly_resolves_editable_workspace_member` (на утверждении `missing-import`); третий проходит уже сейчас (двойник: пути не передаются вовсе).

- [ ] **Step 3: Implement**

`selfcheck/env.py`:

```python
@dataclass(frozen=True)
class EnvInfo:
    """How pyrefly/deptry see the target's third-party packages."""

    mode: Literal["checkout-venv", "no-env"]
    stale: bool = False
    site_packages: Path | None = None
    python_version: str | None = None
    search_paths: tuple[str, ...] = ()  # editable .pth paths inside the checkout


def editable_paths(repo: Path, site: Path) -> tuple[str, ...]:
    """Path lines of ``site/*.pth`` inside ``repo``, relative to it (§10.4).

    Read as text only: ``import`` lines are executable and are skipped; a path
    outside the checkout is dropped (probes see only the copy)."""
    root = repo.resolve()
    found: set[str] = set()
    for pth in sorted(site.glob("*.pth")):
        try:
            lines = pth.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in (ln.strip() for ln in lines):
            if not line or line.startswith(("#", "import ", "import\t")):
                continue
            raw = Path(line)
            path = (raw if raw.is_absolute() else site / raw).resolve()
            if path == root or root in path.parents:
                found.add(path.relative_to(root).as_posix() or ".")
    return tuple(sorted(found))
```

`detect_env` — последняя строка:

```python
    return EnvInfo(
        "checkout-venv", stale, sites[0], version, editable_paths(repo, sites[0])
    )
```

`python_tools.py`:

```python
def _pyrefly_search(ctx: ProbeCtx) -> tuple[list[str], list[str]]:
    """``--search-path`` args for editable paths present in the copy (§10.4)."""
    args: list[str] = []
    dropped: list[str] = []
    for rel in ctx.target.env.search_paths:
        path = ctx.target.copy / rel
        if path.is_dir():
            args += ["--search-path", str(path)]
        else:
            dropped.append(rel)
    return args, dropped
```

`_pyrefly_argv` — в ветке `checkout-venv` после `--python-version`: `args += _pyrefly_search(ctx)[0]`. `_pyrefly_parse` — перед `return result`:

```python
    dropped = _pyrefly_search(ctx)[1]
    if dropped:
        result.notes.append(f"search-path not in copy, not passed: {dropped}")
```

`run.py:254`:

```python
    acc.env[repo.name] = {
        "mode": env.mode,
        "stale": env.stale,
        "search_paths": len(env.search_paths),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком; `test_deptry_env_as_data` зелёный (evil `.pth` фикстуры — строка `import`, пропущена).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/env.py selfcheck/probes/python_tools.py selfcheck/run.py tests/selfcheck/test_env.py tests/selfcheck/test_python_tools.py
git commit -m "feat(selfcheck): editable-пути .pth как данные → pyrefly --search-path в копии (§10.4)"
```

---

### Task 4: `[[allow]] repo`

**Files:**
- Modify: `selfcheck/config.py`, `selfcheck/run.py` (проверка рядом с `[[operator]]`), `selfcheck.toml`
- Test: `tests/selfcheck/test_config_manifest.py`, `tests/selfcheck/test_run.py`

**Interfaces:**
- Produces: `AllowEntry.repo: str | None = None`; `matches(finding)` — `False`, если `repo` задан и `finding.owner_repo != repo`.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_config_manifest.py` (импорты `from dataclasses import replace`, `from selfcheck.model import Confidence, Finding` — добавить, если нет):

```python
def test_allow_repo_limits_the_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = "a"\nreason = "r"\n'
        "until = 2099-01-01\n"
    )
    entry = load_config(cfg).allow[0]
    mine = Finding("r/x", "quality", "low", Confidence.LIKELY, "a", "file:x.py", [])
    assert entry.matches(mine)
    assert not entry.matches(replace(mine, owner_repo="b"))
```

`tests/selfcheck/test_run.py`:

```python
def test_allow_repo_not_in_manifest_is_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    cfg = ws / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = "nope"\nreason = "r"\n'
        "until = 2099-01-01\n"
    )
    assert main([*args(ws), "--config", str(cfg), "--probe", "ruff"]) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_config_manifest.py tests/selfcheck/test_run.py -q -k "allow_repo"`
Expected: FAIL — запись совпала с находкой репо `b`; код выхода не 4.

- [ ] **Step 3: Implement**

`AllowEntry` — поле `repo: str | None = None`; `matches` — первые строки:

```python
        if self.repo is not None and finding.owner_repo != self.repo:
            return False
```

`_allow_entry` — `repo=raw.get("repo")`. `run.main` — сразу после проверки `stray` для `[[operator]]`:

```python
        stray_allow = sorted(
            {a.repo for a in config.allow if a.repo} - set(manifest.order)
        )
        if stray_allow:
            raise ConfigError(f"[[allow]] repo not in manifest: {stray_allow}")
```

`selfcheck.toml` — в запись `issue_console.py` строка `repo = "devtools"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/config.py selfcheck/run.py selfcheck.toml tests/selfcheck/test_config_manifest.py tests/selfcheck/test_run.py
git commit -m "feat(selfcheck): [[allow]] repo — запись действует на своё репо (§10.8)"
```

---

### Task 5: `llm-sites` — место построения запуска, TS

**Files:**
- Modify: `selfcheck/rules/llm.yml`, `selfcheck/llm.py`
- Test: `tests/selfcheck/test_llm_s3.py` (новый)

**Interfaces:**
- Consumes: `python_files`, `shell_files`.
- Produces: `ts_files(target) -> tuple[str, ...]`; `code_endpoint(text: str, line: int) -> bool`; `MAX_TARGET_BYTES = 1_000_000`.

- [ ] **Step 1: Write the failing tests**

Сначала сверить, где `run_probe` кладёт `ParseResult.notes` в `ProbeResult` (`probes/base.py`): тест `test_big_file_named_in_notes` ниже читает `result.coverage["notes"]`; если заметки лежат в другом поле — тест пишется на него до запуска Step 2.

`tests/selfcheck/test_llm_s3.py`:

```python
"""S3 Task 5 — llm-sites: launch construction, harness resolve, TS (§10.7)."""

from __future__ import annotations

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, MAX_TARGET_BYTES, code_endpoint
from selfcheck.probes.base import (
    ProbeResult,
    ProbeStatus,
    RepoTarget,
    canary_files,
    run_probe,
)
from tests.selfcheck.helpers import NOW, make_repo, require_tool

SPAWNER = (
    "def argv(model, prompt):\n"
    '    return ["claude", "--model", model, "-p", prompt]\n'
)
TUPLE = 'def argv(prompt):\n    return ("codex", "exec", prompt)\n'
CONFIGURED = (
    "def run(config, prompt, runner):\n"
    '    return runner([config.claude_command, "-p", prompt])\n'
)
PRIVATE_CMD = (
    "class D:\n    def cmd(self, prompt):\n"
    '        return [self._claude_command, "--print", "-p", prompt]\n'
)
RESOLVE = (
    "import os\nimport shutil\n\n"
    'BIN = os.environ.get("CLAUDE_BIN", "claude")\n'
    'ALT = os.getenv("OPENCODE_BIN", "opencode")\n\n\n'
    'def find():\n    return shutil.which("codex")\n'
)
URL_F = "def url(host):\n    return f\"{host.rstrip('/')}/v1/chat/completions\"\n"
URL_OLLAMA = 'def url(host):\n    return host + "/api/chat"\n'
DOC_URL = (
    '"""Calls the HTTP `/api/chat` endpoint of ollama."""\n\n\n'
    "def f(base):\n"
    "    # POST http://localhost:11434/api/chat\n"
    '    note = "see /v1/messages for details"\n'
    '    return note + f"see the docs at {base}/v1/messages for details"\n'
)
NOT_ARGV = (
    'NAMES = {"codex_cli": "codex", "claude_code": "claude"}\n'
    'BACKENDS = ("codex", "disp")\n\n\n'
    "def add(parser):\n"
    '    parser.add_argument("--author", choices=["codex", "disp"], default="codex")\n'
)
SHELL_URL = (
    "#!/bin/sh\n"
    "curl -s http://localhost:11434/api/generate -d '{}'\n"
    "# curl http://h/api/chat\n"
)
TS_SPAWN = (
    'import { spawn } from "node:child_process";\n'
    'export const go = (p: string) => spawn("claude", ["-p", p]);\n'
)
TS_ARGV = 'export const argv = (p: string) => ["codex", "exec", p];\n'
TS_SDK_OPENAI = 'import OpenAI from "openai";\nexport const c = new OpenAI();\n'
TS_SDK_ANTHROPIC = (
    'import Anthropic from "@anthropic-ai/sdk";\n'
    "export const a = new Anthropic();\n"
    'export const r = (x: any) => x.messages.create({ model: "m" });\n'
)
TS_AGENTS = 'import { Agent, run } from "@openai/agents";\nexport { Agent, run };\n'
TS_URL = "export const u = (h: string) => `${h}/v1/chat/completions`;\n"
TS_COMMENT = "// fetch(`${h}/v1/chat/completions`)\nexport const x = 1;\n"
SPLIT = (
    "import json\nimport subprocess\n\n\n"
    "def go(items, prompt):\n"
    "    out = []\n"
    "    for item in items:\n"
    '        cmd = ["claude", "-p", prompt]\n'
    "        raw = subprocess.run(cmd, capture_output=True, text=True).stdout\n"
    '        out.append(json.loads(raw)["label"])\n'
    "    return out\n"
)
TS_STAR = 'import * as sdk from "@anthropic-ai/sdk";\nexport default sdk;\n'
BIG_JS = "// bundle\n" + "x" * MAX_TARGET_BYTES + '\nspawn("claude", ["-p", q]);\n'

FILES = {
    "spawner.py": SPAWNER,
    "tuple.py": TUPLE,
    "configured.py": CONFIGURED,
    "private.py": PRIVATE_CMD,
    "resolve.py": RESOLVE,
    "url_f.py": URL_F,
    "url_ollama.py": URL_OLLAMA,
    "doc_url.py": DOC_URL,
    "not_argv.py": NOT_ARGV,
    "fetch.sh": SHELL_URL,
    "spawn.ts": TS_SPAWN,
    "argv.ts": TS_ARGV,
    "sdk_openai.ts": TS_SDK_OPENAI,
    "sdk_anthropic.ts": TS_SDK_ANTHROPIC,
    "agents.ts": TS_AGENTS,
    "url.ts": TS_URL,
    "comment.ts": TS_COMMENT,
    "big.js": BIG_JS,
    "split.py": SPLIT,
    "star.ts": TS_STAR,
}


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> ProbeResult:
    require_tool("uvx")
    tmp = tmp_path_factory.mktemp("s3llm")
    repo = make_repo(tmp / "repo", FILES)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(LLM_SITES, target, tmp / "run" / "work")
    finally:
        release(copy)


def _rows(result: ProbeResult) -> set[tuple[str, str, str]]:
    return {(i["path"], i["mechanism"], i["rule"]) for i in result.extra["inventory"]}


@pytest.mark.parametrize(
    ("path", "mechanism", "rule"),
    [
        ("spawner.py", "A", "argv-literal"),
        ("tuple.py", "A", "argv-literal"),
        ("configured.py", "A", "argv-literal"),
        ("private.py", "A", "argv-literal"),
        ("resolve.py", "A", "harness-resolve"),
        ("url_f.py", "C", "endpoint"),
        ("url_ollama.py", "C", "endpoint"),
        ("fetch.sh", "C", "endpoint"),
        ("spawn.ts", "A", "spawn-ts"),
        ("argv.ts", "A", "argv-literal-ts"),
        ("sdk_openai.ts", "B", "sdk-ts"),
        ("sdk_anthropic.ts", "B", "sdk-ts"),
        ("agents.ts", "B", "sdk-ts"),
        ("star.ts", "B", "sdk-ts"),
        ("url.ts", "C", "endpoint"),
        ("split.py", "A", "argv-literal"),
    ],
)
def test_each_rule_fires(
    result: ProbeResult, path: str, mechanism: str, rule: str
) -> None:
    assert result.status is ProbeStatus.OK and result.canary == "hit"
    assert (path, mechanism, rule) in _rows(result)


def test_resolve_counts_getenv_and_which(result: ProbeResult) -> None:
    lines = {
        i["line"] for i in result.extra["inventory"] if i["path"] == "resolve.py"
    }
    assert lines == {4, 5, 9}


@pytest.mark.parametrize("path", ["doc_url.py", "not_argv.py", "comment.ts", "big.js"])
def test_negative_twins(result: ProbeResult, path: str) -> None:
    assert not any(r[0] == path for r in _rows(result))


def test_shell_comment_url_is_not_a_point(result: ProbeResult) -> None:
    lines = {i["line"] for i in result.extra["inventory"] if i["path"] == "fetch.sh"}
    assert lines == {2}


def test_big_file_named_in_notes(result: ProbeResult) -> None:
    assert any("big.js" in n for n in result.coverage.get("notes", []))


def test_ts_is_inventory_only(result: ProbeResult) -> None:
    ts = [i for i in result.extra["inventory"] if i["path"].endswith(".ts")]
    assert ts and not any(i["candidate"] or i["features"] for i in ts)
    assert not any(f.anchor.endswith(".ts") for f in result.findings)


def test_point_is_candidate_only_if_every_row_is(result: ProbeResult) -> None:
    # the prompt comes from a parameter: invisible on both the list and the launch
    assert not any(f.anchor == "llm:split.py::go" for f in result.findings)
    rows = [i for i in result.extra["inventory"] if i["path"] == "split.py"]
    assert rows and not any(i["candidate"] for i in rows)


def test_rows_deduplicated(result: ProbeResult) -> None:
    keys = [(i["path"], i["line"]) for i in result.extra["inventory"]]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("text", "line", "hit"),
    [
        (URL_F, 2, True),
        (URL_OLLAMA, 2, True),
        (DOC_URL, 1, False),  # docstring
        (DOC_URL, 5, False),  # comment
        (DOC_URL, 6, False),  # prose with whitespace before the path
        (DOC_URL, 7, False),  # constant piece of an f-string
    ],
)
def test_code_endpoint(text: str, line: int, hit: bool) -> None:
    assert code_endpoint(text, line) is hit


def test_logic_version_bumped() -> None:
    assert LLM_SITES.logic_version == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_llm_s3.py -q`
Expected: FAIL — `ImportError: cannot import name 'MAX_TARGET_BYTES'`.

- [ ] **Step 3: Implement**

`selfcheck/rules/llm.yml` — дописать (список харнессов совпадает с `HARNESSES`):

```yaml
  - id: argv-literal
    languages: [python]
    severity: INFO
    message: harness argv construction (mechanism A)
    patterns:
      - pattern-either:
          - pattern: "[$FIRST, $SECOND, ...]"
          - pattern: "($FIRST, $SECOND, ...)"
      - metavariable-regex:
          metavariable: $SECOND
          regex: ^["'](?:-{1,2}[A-Za-z][\w-]*|exec|run)["']$
      - metavariable-regex:
          metavariable: $FIRST
          regex: (?i)^(?:["'](?:claude|codex|opencode|aider|pi|qwen|ollama|llama-cli|copilot)["']|.*(?:claude|codex|opencode|aider|ollama|qwen|copilot)_?(?:command|cmd|bin|binary|cli|path|exe)\b.*)$
  - id: argv-literal-ts
    languages: [js, ts]
    severity: INFO
    message: harness argv construction (mechanism A)
    patterns:
      - pattern: "[$FIRST, $SECOND, ...]"
      - metavariable-regex:
          metavariable: $SECOND
          regex: ^["'`](?:-{1,2}[A-Za-z][\w-]*|exec|run)["'`]$
      - metavariable-regex:
          metavariable: $FIRST
          regex: ^["'`](?:claude|codex|opencode|aider|pi|qwen|ollama|llama-cli|copilot)["'`]$
  - id: harness-resolve
    languages: [python]
    severity: INFO
    message: harness binary resolution (mechanism A)
    patterns:
      - pattern-either:
          - pattern: os.environ.get($K, $BIN)
          - pattern: os.getenv($K, $BIN)
          - pattern: shutil.which($BIN)
      - metavariable-regex:
          metavariable: $BIN
          regex: ^["'](?:claude|codex|opencode|aider|pi|qwen|ollama|llama-cli|copilot)["']$
  - id: spawn-ts
    languages: [js, ts]
    severity: INFO
    message: harness process launch (mechanism A)
    patterns:
      - pattern-either:
          - pattern: spawn($BIN, ...)
          - pattern: spawnSync($BIN, ...)
          - pattern: execFile($BIN, ...)
          - pattern: execa($BIN, ...)
      - metavariable-regex:
          metavariable: $BIN
          regex: ^["'`](?:claude|codex|opencode|aider|pi|qwen|ollama|llama-cli|copilot)["'`]$
  - id: sdk-ts
    languages: [js, ts]
    severity: INFO
    message: LLM SDK (mechanism B)
    pattern-either:
      - pattern: new Anthropic(...)
      - pattern: new OpenAI(...)
      - pattern: $C.messages.create(...)
      - pattern: $C.chat.completions.create(...)
      - pattern: import $X from "@anthropic-ai/sdk"
      - pattern: import $X from "openai"
      - pattern: import { $X } from "@openai/agents"
      - pattern: import * as $X from "@anthropic-ai/sdk"
      - pattern: import * as $X from "openai"
  - id: endpoint
    languages: [python, js, ts, bash]
    severity: INFO
    message: LLM endpoint URL (mechanism C)
    pattern-regex: (?:/v1/messages|/chat/completions|/api/chat|/api/generate|/completion)\b
```

`selfcheck/llm.py`:

```python
MAX_TARGET_BYTES = 1_000_000  # semgrep's default --max-target-bytes (§10.7)
_MECHANISM = {
    "py-launch": "A",
    "cli-shell": "A",
    "argv-literal": "A",
    "argv-literal-ts": "A",
    "harness-resolve": "A",
    "spawn-ts": "A",
    "sdk-python": "B",
    "sdk-ts": "B",
    "http-python": "C",
    "endpoint": "C",
}
_TS_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")
_PATHS = r"(?:/v1/messages|/chat/completions|/api/chat|/api/generate|/completion)\b"
_ENDPOINT = re.compile(rf"^\S*?{_PATHS}")
# a word with no whitespace before the path: quoted, `=`-assigned or bare (shell)
_WORD_ENDPOINT = re.compile(rf"(?:^|[\s\"'`=(])[^\s\"'`]*{_PATHS}")


def ts_files(target: RepoTarget) -> tuple[str, ...]:
    """Corpus TS/JS files except canaries (spec §10.7)."""
    return tuple(
        p
        for p in target.corpus
        if p.endswith(_TS_SUFFIXES) and role_of(p, target.roles) is not Role.CANARY
    )


def _skipped_nodes(tree: ast.AST) -> set[int]:
    """Docstrings and the constant pieces of f-strings (checked joined)."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            ids |= {id(v) for v in node.values}
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
        )
    return None


def code_endpoint(text: str, line: int) -> bool:
    """A code string on ``line`` names an endpoint with no whitespace before it."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    skip = _skipped_nodes(tree)
    for node in ast.walk(tree):
        value = _literal(node)
        if value is None or id(node) in skip:
            continue
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", None) or start
        if start <= line <= end and _ENDPOINT.search(value):
            return True
    return False
```

`_select`:

```python
def _select(target: RepoTarget) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys((*python_files(target), *shell_files(target), *ts_files(target)))
    )
```

`_argv` — вместо `*copy_paths(ctx)`:

```python
        *[p for p in copy_paths(ctx) if Path(p).stat().st_size <= MAX_TARGET_BYTES],
```

`_parse` — сразу после создания `result`:

```python
    big = sorted(
        r for r in ctx.inputs if (ctx.target.copy / r).stat().st_size > MAX_TARGET_BYTES
    )
    if big:
        result.notes.append(f"larger than {MAX_TARGET_BYTES} bytes, not scanned: {big}")
        # counted as processed: the skip is named in the note, not a lost input
        result.processed_paths = [*(result.processed_paths or []), *big]
```

`_site` — новое начало (прежние Python- и shell-ветки ниже сохраняются; shell-ветка берёт уже вычисленный `line_text`):

```python
def _site(ctx: ProbeCtx, rule: str, rel: str, line: int) -> dict[str, Any] | None:
    text = source_text(ctx, rel)
    lines = text.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    if rule == "endpoint":
        if rel.endswith(".py"):
            if not code_endpoint(text, line):
                return None
        elif line_text.lstrip().startswith(("#", "//", "*")) or not (
            _WORD_ENDPOINT.search(line_text)
        ):
            return None
    if rel.endswith(_TS_SUFFIXES):
        # TS: file-level anchor, inventory only (§10.7, named cost)
        return {
            "path": rel,
            "line": line,
            "mechanism": _MECHANISM[rule],
            "rule": rule,
            "candidate": False,
            "features": [],
            "anchor": f"llm:{rel}",
        }
    ...  # прежнее тело: `if rel.endswith(".py"): …` / `else:` shell с этим line_text
```

Python-ветка `_site` — невидимость промпта у `argv-literal` (рядом с веткой `py-launch`):

```python
        if rule == "py-launch":
            ...  # как было
        elif rule == "argv-literal":
            invisible = _literal_argv_invisible(text, line)
```

```python
def _literal_argv_invisible(text: str, line: int) -> bool:
    """A literal argv on ``line`` whose non-flag elements after the binary are
    all non-literal: the prompt is not visible statically (§3.4, §10.7)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and node.lineno == line:
            rest = [
                e
                for e in node.elts[1:]
                if not (
                    isinstance(e, ast.Constant)
                    and isinstance(e.value, str)
                    and e.value.startswith("-")
                )
            ]
            return bool(rest) and not any(
                isinstance(e, ast.Constant | ast.JoinedStr) for e in rest
            )
    return False
```

`_parse` — строки инвентаря дедуплицируются, находки выпускаются **после** цикла по якорю, только если кандидат каждая строка якоря. Цикл по `data["results"]` больше не создаёт `Finding`; вместо этого:

```python
    seen: set[tuple[str, int]] = set()
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for hit in data["results"]:
        ...  # rule, rel, row как было
        if row is None or (row["path"], row["line"]) in seen:
            continue
        seen.add((row["path"], row["line"]))
        inventory.append(row)
        by_anchor.setdefault(row["anchor"], []).append(row)
    for anchor, rows in by_anchor.items():
        if not all(r["candidate"] for r in rows):
            continue
        features = sorted({f for r in rows for f in r["features"]})
        result.findings.append(
            Finding(
                rule="llm-sites/replaceable",
                category="llm-replaceable",
                severity="low",
                confidence=Confidence.CANDIDATE,
                owner_repo=ctx.target.name,
                anchor=anchor,
                locations=[Location(r["path"], r["line"]) for r in rows],
                evidence=[{"kind": "feature", "detail": f} for f in features],
                suggestion="скрипт / правила / дерево решений / малая модель",
            )
        )
```

(S1 `test_candidates` сохраняет ожидания: у `c.py::classify` и `b.py::tag` каждая строка — кандидат.)

`LLM_SITES` — `logic_version=2`, `rules=("A", "B", "C", "D", "candidate:schema|loop", "construction")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком, включая S1 `test_inventory_mechanisms` (`e.py` — текст с пробелом до пути, не точка).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/rules/llm.yml selfcheck/llm.py tests/selfcheck/test_llm_s3.py
git commit -m "feat(selfcheck): llm-sites — место построения запуска, harness-resolve, TS (§10.7, решение владельца)"
```

---

### Task 6: межрепные ast-дубли и вендор-группы

**Files:**
- Modify: `selfcheck/dups.py:126-235`, `selfcheck/run.py`, `selfcheck/report.py`
- Test: `tests/selfcheck/test_dups.py`, `tests/selfcheck/test_run.py`

**Interfaces:**
- Consumes: `acc.vendored[repo]` — `{path: [{"owner", "ref", "declaration"}, …]}` из `usage-graph` (§9.7).
- Produces: `group_dups(hashes: Mapping[str, list[FuncHash]], vendored: Mapping[str, Mapping[str, list[dict[str, str]]]] | None) -> tuple[list[Finding], list[dict[str, Any]]]`; `dup_findings(hashes, repo)` = `group_dups({repo: hashes}, None)[0]`; `ast-dup` пишет `extra["hashes"]` и выпускает только группы канарейки.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_dups.py` (эталон снят кодом S1 до рефакторинга, 2026-09-27):

```python
from selfcheck.dups import FuncHash, group_dups

GOLD_BODY = "".join(f"    v{i} = x * {i}\n" for i in range(8))  # не BODY: имя занято
GOLD_SBODY = "".join(f"    v{i} = x * {i + 50}\n" for i in range(8))


def _fn(path: str, name: str = "f", body: str = GOLD_BODY) -> FuncHash:
    return function_hashes(f"def {name}(x):\n{body}    return x\n", path)[0]


S1_GOLDEN = [
    {
        "id": "sc-a4f4f800",
        "rule": "ast-dup/exact",
        "owner_repo": "r",
        "anchor": "dup:exact:cf9bd0c1e0a973ff",
        "locations": [{"path": "a.py", "line": 1}, {"path": "b.py", "line": 1}],
        "related": [
            {"owner_repo": "r", "path": "a.py", "line": 1, "member": "f"},
            {"owner_repo": "r", "path": "b.py", "line": 1, "member": "g"},
        ],
    },
    {
        "id": "sc-e6d0475e",
        "rule": "ast-dup/structural",
        "owner_repo": "r",
        "anchor": "dup:structural:6b7354843b992577",
        "locations": [
            {"path": "a.py", "line": 1},
            {"path": "b.py", "line": 1},
            {"path": "c.py", "line": 1},
        ],
        "related": [
            {"owner_repo": "r", "path": "a.py", "line": 1, "member": "f"},
            {"owner_repo": "r", "path": "b.py", "line": 1, "member": "g"},
            {"owner_repo": "r", "path": "c.py", "line": 1, "member": "h"},
        ],
    },
]
KEYS = ("id", "rule", "owner_repo", "anchor", "locations", "related")


def test_single_repo_matches_s1_golden() -> None:
    hs = [_fn("a.py", "f"), _fn("b.py", "g"), _fn("c.py", "h", GOLD_SBODY)]
    found, vendor = group_dups({"r": hs}, None)
    got = sorted(({k: f.to_json()[k] for k in KEYS} for f in found), key=str)
    assert got == sorted(S1_GOLDEN, key=str)
    assert vendor == []


def test_cross_repo_exact_is_one_group() -> None:
    found, _ = group_dups({"b": [_fn("q.py")], "a": [_fn("p.py")]}, {})
    (f,) = found
    assert (f.rule, f.owner_repo) == ("ast-dup/exact", "a")
    assert [(r["owner_repo"], r["path"]) for r in f.related] == [
        ("a", "p.py"),
        ("b", "q.py"),
    ]
    assert [loc.path for loc in f.locations] == ["p.py"]


def test_cross_repo_group_id_stable_when_repo_drops_out() -> None:
    b = [_fn("q.py"), _fn("r.py", "g")]
    both, _ = group_dups({"a": [_fn("p.py")], "b": b}, {})
    one, _ = group_dups({"b": b}, {})
    assert [f.id for f in both] == [f.id for f in one]


def _decl(owner: str) -> list[dict[str, str]]:
    return [{"owner": owner, "ref": "abcdef1", "declaration": "x/PIN"}]


def test_upstream_plus_declared_copies_is_vendor_group() -> None:
    hashes = {"up": [_fn("g.py")], "c1": [_fn("v/g.py")], "c2": [_fn("w/g.py")]}
    vendored = {"c1": {"v/g.py": _decl("up")}, "c2": {"w/g.py": _decl("up")}}
    found, vendor = group_dups(hashes, vendored)
    assert found == [] and len(vendor) == 1
    assert sorted(m["owner_repo"] for m in vendor[0]["members"]) == ["c1", "c2", "up"]


def test_undeclared_copy_stays_a_finding() -> None:
    hashes = {"up": [_fn("g.py")], "c1": [_fn("v/g.py")], "c2": [_fn("w/g.py")]}
    found, vendor = group_dups(hashes, {"c1": {"v/g.py": _decl("up")}})
    assert len(found) == 1 and vendor == []


def test_upstream_out_of_scope_undeclared_copy_is_a_finding() -> None:
    hashes = {"c1": [_fn("v/g.py")], "c2": [_fn("w/g.py")]}
    found, vendor = group_dups(hashes, {"c1": {"v/g.py": _decl("up")}})
    assert len(found) == 1 and vendor == []
```

`test_ast_dup_probe` в том же файле меняется (группы репо выпускает прогон, не проба):

```python
def test_ast_dup_probe(tmp_path: Path) -> None:
    res = run(AST_DUP, tmp_path, {"a.py": func("one"), "b.py": func("two")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert res.findings == []  # the canary group is subtracted by the core
    hashes = [
        FuncHash(**{**h, "literals": tuple(h["literals"])})
        for h in res.extra["hashes"]
    ]
    assert [f.rule for f in group_dups({"repo": hashes}, None)[0]] == [
        "ast-dup/exact"
    ]
```

`tests/selfcheck/test_run.py` (`make_repo` — импорт из helpers):

```python
def test_cross_repo_dup_in_one_run(tmp_path: Path) -> None:
    body = "".join(f"    v{i} = x * {i}\n" for i in range(8))
    src = f"def helper(x):\n{body}    return x\n"
    ws = workspace(tmp_path, {"h.py": src})
    make_repo(
        tmp_path / "other",
        {"pyproject.toml": '[project]\nname = "o"\nversion = "0"\n', "g.py": src},
    )
    (ws / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    main([*args(ws), "--repo", "devtools", "--repo", "other", "--probe", "ast-dup"])
    doc = reports(ws)[-1]
    groups = [f for f in doc["findings"] if f["rule"] == "ast-dup/exact"]
    assert len(groups) == 1
    assert {r["owner_repo"] for r in groups[0]["related"]} == {"devtools", "other"}
    assert any("вендор-фильтр не применён" in w for w in doc["run"]["warnings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_dups.py tests/selfcheck/test_run.py -q`
Expected: FAIL — `ImportError: cannot import name 'group_dups'`.

- [ ] **Step 3: Implement**

`dups.py` (импорты `dataclasses`, `from collections.abc import Mapping`, `from typing import Any`) — вместо `_dup` и тела `dup_findings`:

```python
Member = tuple[str, FuncHash]
Vendored = Mapping[str, Mapping[str, list[dict[str, str]]]]


def _order(member: Member) -> tuple[str, str, int]:
    return (member[0], member[1].path, member[1].line)


def _group(
    rule: str,
    kind: str,
    key: str,
    members: list[Member],
    confidence: Confidence,
    evidence: list[dict[str, str]],
) -> Finding:
    ordered = sorted(members, key=_order)
    owner = ordered[0][0]
    return Finding(
        rule=rule,
        category="duplicate",
        severity="medium",
        confidence=confidence,
        owner_repo=owner,
        anchor=f"dup:{kind}:{key[:16]}",
        locations=[Location(h.path, h.line) for r, h in ordered if r == owner],
        related=[
            {"owner_repo": r, "path": h.path, "line": h.line, "member": h.qualname}
            for r, h in ordered
        ],
        evidence=evidence,
        suggestion="вынести в общую функцию или модуль",
    )


def _is_vendor_group(members: list[Member], vendored: Vendored) -> bool:
    """Declared copies plus at most one upstream in the owner repo (§10.6)."""
    decls = [vendored.get(r, {}).get(h.path, []) for r, h in members]
    owners = {d["owner"] for ds in decls for d in ds}
    outside = [r for (r, _), ds in zip(members, decls, strict=True) if not ds]
    return bool(owners) and len(outside) <= 1 and all(r in owners for r in outside)


def group_dups(
    hashes: Mapping[str, list[FuncHash]], vendored: Vendored | None
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Duplicate groups over every repo of the run; vendor groups aside (§10.6)."""
    by_exact: dict[str, list[Member]] = defaultdict(list)
    by_struct: dict[str, list[Member]] = defaultdict(list)
    for repo, items in hashes.items():
        for h in items:
            by_exact[h.exact].append((repo, h))
            by_struct[h.structural].append((repo, h))
    found: list[Finding] = []
    vendor_rows: list[dict[str, Any]] = []

    def emit(finding: Finding, members: list[Member]) -> None:
        if vendored is not None and _is_vendor_group(members, vendored):
            vendor_rows.append({"anchor": finding.anchor, "members": finding.related})
        else:
            found.append(finding)

    for key, group in by_exact.items():
        if len(group) >= 2:
            emit(
                _group("ast-dup/exact", "exact", key, group, Confidence.CONFIRMED, []),
                group,
            )
    for key, group in by_struct.items():
        if len(group) < 2 or len({h.exact for _, h in group}) == 1:
            continue
        cross = len({r for r, _ in group}) > 1
        evidence = [
            {
                "kind": "literals",
                "detail": f"{r + ':' if cross else ''}{h.path}:{h.line}: "
                f"{list(h.literals)}",
            }
            for r, h in sorted(group, key=_order)
        ]
        emit(
            _group(
                "ast-dup/structural",
                "structural",
                key,
                group,
                Confidence.CANDIDATE,
                evidence,
            ),
            group,
        )
    return found, vendor_rows


def dup_findings(hashes: list[FuncHash], repo: str) -> list[Finding]:
    """exact groups → confirmed; structural-only groups → candidate (one repo)."""
    return group_dups({repo: hashes}, None)[0]
```

`_ast_dup` — файлы репо копят `repo_hashes`, `dup_findings` по ним не зовётся; канарейка — как было (`result.findings += dup_findings(canary_hashes, ctx.target.name)`); в конце `result.extra["hashes"] = [dataclasses.asdict(h) for h in repo_hashes]`. `AST_DUP.logic_version = 2`.

`run.py`: `_Run` — `hashes: dict[str, list[FuncHash]] = field(default_factory=dict)`, `vendor_dups: list[dict[str, Any]] = field(default_factory=list)`. `_scan_repo`, в цикле по `results`:

```python
        if r.probe == "ast-dup" and "hashes" in r.extra:
            acc.hashes[repo.name] = [
                FuncHash(**{**h, "literals": tuple(h["literals"])})
                for h in r.extra["hashes"]
            ]
```

`main` — после цикла по репо, до `_fleet_surface`:

```python
    if acc.hashes:
        have_roles = all(n in acc.vendored for n in acc.hashes)
        if not have_roles:
            acc.warnings.append(
                "ast-dup: вендор-фильтр не применён — нет usage-graph (§10.6)"
            )
        dups, acc.vendor_dups = group_dups(
            acc.hashes, acc.vendored if have_roles else None
        )
        acc.findings += dups
```

`_document` — ключ `"vendor_dups": acc.vendor_dups`. `report.py` — в конце `_fleet_lines` перед `return`:

```python
    vendor_dups = doc.get("vendor_dups", [])
    if vendor_dups:
        lines += ["", f"### вендор-дубли ({len(vendor_dups)})", ""]
        lines += [
            f"- `{g['anchor']}`: "
            + ", ".join(
                f"{m['owner_repo']}:{m['path']}::{m['member']}" for m in g["members"]
            )
            for g in vendor_dups
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/dups.py selfcheck/run.py selfcheck/report.py tests/selfcheck/test_dups.py tests/selfcheck/test_run.py
git commit -m "feat(selfcheck): межрепные ast-дубли одной группой, вендор-группы не находка (§10.6)"
```

---

### Task 7: `--all`, сводная таблица, колонка репо, репо в инвентаре

**Files:**
- Modify: `selfcheck/run.py`, `selfcheck/report.py`, `Makefile:60`
- Test: `tests/selfcheck/test_run.py`, `tests/selfcheck/test_report.py`

**Interfaces:**
- Produces: `--all`; `inventory.llm[*].repo`; при `len(scope) > 1` — раздел «## Репо» и колонка `репо` у находок, инвентаря и вендор-копий.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_run.py`:

```python
def test_all_scans_every_manifest_repo(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    make_repo(tmp_path / "other", {"a.py": "import os\n"})
    (ws / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    code = main([*args(ws), "--all", "--probe", "ruff"])
    doc = reports(ws)[-1]
    assert doc["run"]["scope"] == ["devtools", "other"]
    assert code == run_module.exit_code(
        [run_module.ProbeResult(p["probe"], p["repo"], run_module.ProbeStatus(p["status"])) for p in doc["probes"]]
    )  # the worst over both repos
    md = next((ws / "out").glob("*/report.md")).read_text()
    assert "## Репо" in md and "| other |" in md


def test_all_with_repo_is_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    assert main([*args(ws), "--all", "--repo", "devtools"]) == 4


def test_inventory_rows_carry_repo(tmp_path: Path) -> None:
    require_tool("uvx")
    ws = workspace(
        tmp_path, {"c.py": 'import subprocess\nsubprocess.run(["claude", "-p", "x"])\n'}
    )
    main([*args(ws), "--probe", "llm-sites"])
    doc = reports(ws)[-1]
    assert {r["repo"] for r in doc["inventory"]["llm"]} == {"devtools"}
```

(`run_module.ProbeResult`/`ProbeStatus` — реэкспорт через `selfcheck.run`, импортированный в файле как `run_module`; ruff гейтится как в соседних тестах.)

`tests/selfcheck/test_report.py`:

```python
def _doc(scope: list[str], findings: list[dict], probes: list[dict]) -> dict:
    return {
        "run": {
            "run_id": "r",
            "host": "h",
            "scope": scope,
            "surface": {},
            "env": {},
            "warnings": [],
            "manifest": {"entries_read": 2, "repos": scope, "missing": []},
        },
        "probes": probes,
        "findings": findings,
        "suppressed": [],
        "suppressed_no_env": {},
        "delta": {"statuses": {}, "gone": []},
        "inventory": {
            "llm": [
                {
                    "repo": "b",
                    "path": "x.py",
                    "line": 1,
                    "mechanism": "A",
                    "candidate": False,
                    "features": [],
                }
            ]
        },
    }


def _finding(repo: str, anchor: str) -> dict:
    return {
        "id": f"sc-{repo}",
        "rule": "usage-graph/dead.file",
        "category": "dead",
        "confidence": "likely",
        "anchor": anchor,
        "occurrences": 1,
        "owner_repo": repo,
    }


def test_multi_repo_rows_are_distinguishable() -> None:
    doc = _doc(
        ["a", "b"],
        [_finding("a", "file:setup.sh"), _finding("b", "file:setup.sh")],
        [
            {
                "probe": "radon",
                "repo": "b",
                "status": "partial",
                "reason": "per-file problems",
                "exit_code": 0,
                "tool_version": "6",
                "canary": "hit",
                "coverage": {},
                "findings": 0,
            }
        ],
    )
    text = render_markdown(doc)
    assert "| a | usage-graph/dead.file |" in text
    assert "| b | usage-graph/dead.file |" in text
    assert "radon:partial (per-file problems)" in text
    assert "| b | x.py | 1 |" in text


def test_single_repo_keeps_s1_layout() -> None:
    text = render_markdown(_doc(["a"], [_finding("a", "file:x.sh")], []))
    assert "## Репо" not in text and "| правило | уверенность |" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_run.py tests/selfcheck/test_report.py -q -k "all_ or carry_repo or distinguishable or s1_layout"`
Expected: FAIL — `unrecognized arguments: --all` (код 2 от argparse, `SystemExit`); `KeyError: 'repo'`; нет «## Репо» и колонки. `test_single_repo_keeps_s1_layout` проходит уже сейчас.

- [ ] **Step 3: Implement**

`run.py` — `_args`: `parser.add_argument("--all", action="store_true", help="every manifest repo (spec §10.2)")`; `main`:

```python
        if args.all and args.repo:
            raise ConfigError("--all and --repo are mutually exclusive")
        wanted = (
            list(manifest.order)
            if args.all
            else list(dict.fromkeys(args.repo or ["devtools"]))
        )
```

`_scan_repo` — `acc.inventory += [{**row, "repo": repo.name} for row in r.extra.get("inventory", [])]`.

`report.py`:

```python
def _repo_rows(doc: dict[str, Any]) -> list[str]:
    """Per-repo summary when the run covers two repos or more (§10.2)."""
    scope = doc["run"]["scope"]
    if len(scope) < 2:
        return []
    cats = sorted({f["category"] for f in doc["findings"]})
    lines = [
        "## Репо",
        "",
        "| репо | пробы не ok | dead c/l/cand | " + " | ".join(cats) + " |",
        "|" + "---|" * (3 + len(cats)),
    ]
    for repo in scope:
        mine = [f for f in doc["findings"] if f.get("owner_repo") == repo]
        bad = [
            f"{p['probe']}:{p['status']} ({p.get('reason', '')[:60]})"
            for p in doc["probes"]
            if p["repo"] == repo and p["status"] not in ("ok", "skipped")
        ]
        dead = [
            f["confidence"] for f in mine if f["rule"].startswith("usage-graph/dead")
        ]
        dc = "/".join(str(dead.count(c)) for c in ("confirmed", "likely", "candidate"))
        counts = " | ".join(
            str(sum(1 for f in mine if f["category"] == c)) for c in cats
        )
        lines.append(f"| {repo} | {', '.join(bad) or '—'} | {dc} | {counts} |")
    return [*lines, ""]
```

`render_markdown`: `multi = len(run["scope"]) > 1`; перед `"## Пробы"` — `*_repo_rows(doc),`; таблица категорий:

```python
        head = "| репо | правило |" if multi else "| правило |"
        lines += [
            f"## {category} ({len(items)})",
            "",
            f"{head} уверенность | якорь | мест | статус |",
            "|" + "---|" * (6 if multi else 5),
        ]
        ordered = sorted(
            items, key=lambda x: (x.get("owner_repo", ""), x["rule"], x["anchor"])
        )
        for f in ordered[:MD_ROWS]:
            repo = f"| {f.get('owner_repo', '')} " if multi else ""
            lines.append(
                f"{repo}| {f['rule']} | {f['confidence']} | `{f['anchor']}` | "
                f"{f['occurrences']} | {statuses.get(f['id'], '—')} |"
            )
```

Инвентарь:

```python
    inv_head = "| репо | путь |" if multi else "| путь |"
    lines += [
        f"{inv_head} строка | механизм | кандидат | признаки |",
        "|" + "---|" * (6 if multi else 5),
    ]
    for item in doc["inventory"]["llm"]:
        repo = f"| {item.get('repo', '')} " if multi else ""
        lines.append(
            f"{repo}| {item['path']} | {item['line']} | {item['mechanism']} | "
            f"{'да' if item['candidate'] else 'нет'} | {', '.join(item['features'])} |"
        )
```

Вендор-копии в `_fleet_lines`:

```python
    multi = len(run["scope"]) > 1
    rows_v = [
        (repo, path, d)
        for repo in run["scope"]
        for path, decls in vendored.get(repo, {}).items()
        for d in decls
    ]
    if rows_v:
        head = "| репо | путь |" if multi else "| путь |"
        lines += [
            "",
            "### вендор-копии",
            "",
            f"{head} владелец | ref | декларация |",
            "|" + "---|" * (5 if multi else 4),
        ]
        lines += [
            f"{'| ' + repo + ' ' if multi else ''}| {path} | {d['owner']} | "
            f"{d['ref']} | {d['declaration']} |"
            for repo, path, d in rows_v
        ]
```

`Makefile:60` — `ARGS='[--repo r | --all] [--sched-dir ~/Library/LaunchAgents]'`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком (`test_truncated_category_says_so` — один репо, прежний формат).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/run.py selfcheck/report.py Makefile tests/selfcheck/test_run.py tests/selfcheck/test_report.py
git commit -m "feat(selfcheck): --all по всем репо манифеста, сводная таблица, колонка репо (§10.2)"
```

---

### Task 8: проба `cargo-machete`

**Files:**
- Modify: `selfcheck/probes/other_tools.py`, `selfcheck/registry.py`
- Test: `tests/selfcheck/test_other_tools.py`, `tests/selfcheck/test_registry.py`

**Interfaces:**
- Produces: `CARGO_MACHETE: ProbeSpec` (`languages={"rust"}`, `input_mode="roots"`, `binary="cargo-machete"`), в `REGISTRY` сразу после `*OTHER_PROBES`, **не** в `OTHER_PROBES` (иначе `test_other_tools.py::test_clean_repo_ok_on_read_only_copy`, параметризованный по `OTHER_PROBES`, получит `skipped: language`).

- [ ] **Step 1: Замер закреплённой версии (стоп-точка)**

Run: `cargo-machete --version`
Expected: печатает версию. **Если бинаря нет — стоп и вопрос владельцу** (`cargo install cargo-machete`, §10.3); задачу не исполнять вслепую.

Затем на трёх раскладках:

```bash
T=$(mktemp -d)
crate() {  # $1 dir, $2 name, $3 with src (1/0)
  mkdir -p "$1"
  printf '[package]\nname = "%s"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nlibc = "0.2"\n' "$2" > "$1/Cargo.toml"
  [ "$3" = 1 ] && mkdir -p "$1/src" && echo 'pub fn f() {}' > "$1/src/lib.rs"
}
crate "$T/a/vis" vis 1; crate "$T/a/.hid" hid 1
printf 'ign/\n' > "$T/a/.gitignore"; crate "$T/a/ign" ign 1
mkdir -p "$T/b"; printf '[workspace]\nmembers = ["m"]\n' > "$T/b/Cargo.toml"
crate "$T/b/m" m 1; crate "$T/b/selfcheck_canary/cargo_machete" canary 0
for d in a b; do cargo-machete "$T/$d"; echo "rc=$?"; done
```

Expected: rc 1 при находках, 0 без них. Записать в ledger: точную версию (`version_range` = [версия, следующая минорная)); формат строк вывода; найдена ли канарейка **без `src/`** внутри workspace, где она не член; обходятся ли скрытые и gitignored каталоги (в копии `.gitignore` репо есть как файл). Если формат расходится с `_CM_CRATE`/`_CM_DEP` или канарейка без `src/` не находится — ruling и правка до Step 2 (без `src/` — сначала проверить канарейку с `[lib]\npath = "Cargo.toml"`; не годится — расширить `Canary` вторым файлом отдельным ruling'ом).

- [ ] **Step 2: Write the failing tests**

`tests/selfcheck/test_other_tools.py` (импорты — дополнить по факту файла):

```python
from selfcheck.probes.other_tools import CARGO_MACHETE

RUST_OK = {
    "Cargo.toml": (
        '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n'
    ),
    "src/lib.rs": "pub fn f() {}\n",
}


def _run_rust(files: dict[str, str], tmp: Path) -> ProbeResult:
    require_probe(
        "cargo-machete", CARGO_MACHETE.version_args, CARGO_MACHETE.version_range
    )
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([CARGO_MACHETE]))
    target = RepoTarget(
        "repo", repo, copy, frozenset({"rust"}), corpus, EnvInfo("no-env"), now=NOW
    )
    try:
        return run_probe(CARGO_MACHETE, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_cargo_machete_unused_dependency(tmp_path: Path) -> None:
    files = {**RUST_OK, "Cargo.toml": RUST_OK["Cargo.toml"] + 'libc = "0.2"\n'}
    res = _run_rust(files, tmp_path)
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert [(f.rule, f.text_key, f.category) for f in res.findings] == [
        ("cargo-machete/unused-dependency", "a:libc", "deps")
    ]


def test_cargo_machete_clean_repo_ok(tmp_path: Path) -> None:
    res = _run_rust(RUST_OK, tmp_path)
    assert res.status is ProbeStatus.OK and res.findings == []


def test_cargo_machete_without_binary_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "repo", RUST_OK)
    corpus = tuple(list_corpus(repo))
    monkeypatch.setenv("PATH", str(tmp_path))  # after git ran: no cargo-machete
    target = RepoTarget(
        "repo", repo, repo, frozenset({"rust"}), corpus, EnvInfo("no-env"), now=NOW
    )
    res = run_probe(CARGO_MACHETE, target, tmp_path / "work")
    assert res.status is ProbeStatus.UNAVAILABLE


def test_cargo_machete_is_static_and_sees_cargo_config() -> None:
    assert CARGO_MACHETE.executes_target_code is False
    assert "Cargo.toml" in CARGO_MACHETE.config_files
```

`tests/selfcheck/test_registry.py` — в `EXPECTED` добавить `"cargo-machete"`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_other_tools.py tests/selfcheck/test_registry.py -q -k "cargo or registry"`
Expected: FAIL — `ImportError: cannot import name 'CARGO_MACHETE'`.

- [ ] **Step 4: Implement**

`other_tools.py` (регексы — по замеру Step 1; исходный вариант для текстового вывода 0.x):

```python
_CM_CRATE = re.compile(r"^(\S+) -- (.+Cargo\.toml):$")
_CM_DEP = re.compile(r"^\s+(\S+)$")
_CM_CANARY = "selfcheck_canary/cargo_machete/Cargo.toml"


def _cm_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    crate: str | None = None
    manifest: str | None = None
    for line in proc.stdout.splitlines():
        head = _CM_CRATE.match(line)
        if head:
            crate, manifest = head.group(1), rel_path(ctx, head.group(2))
            continue
        dep = _CM_DEP.match(line)
        if dep and crate and manifest:
            result.findings.append(
                Finding(
                    rule="cargo-machete/unused-dependency",
                    category="deps",
                    severity="low",
                    confidence=Confidence.LIKELY,
                    owner_repo=ctx.target.name,
                    anchor=f"file:{manifest}",
                    locations=[Location(manifest, 1)],
                    text_key=f"{crate}:{dep.group(1)}",
                    suggestion="удалить неиспользуемую зависимость",
                )
            )
    return result


def _cm_expected(target: RepoTarget) -> list[str]:
    return sorted(p for p in target.corpus if p.endswith("Cargo.toml"))


CARGO_MACHETE = ProbeSpec(
    name="cargo-machete",
    languages=frozenset({"rust"}),
    input_mode="roots",
    select=lambda target: (".",),
    canary=Canary(
        _CM_CANARY,
        '[package]\nname = "selfcheck_canary"\nversion = "0.1.0"\n'
        'edition = "2021"\n[dependencies]\nlibc = "0.2"\n',
        "cargo-machete/unused-dependency",
        f"file:{_CM_CANARY}",
    ),
    rules=("unused-dependency",),
    binary="cargo-machete",
    version_range=((0, 9), (0, 10)),  # по замеру Step 1
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: [str(ctx.target.copy)],
    parse=_cm_parse,
    expected_files=_cm_expected,
    config_files=("Cargo.toml",),
)
```

`registry.py` — импорт `CARGO_MACHETE`, в `REGISTRY` сразу после `*OTHER_PROBES`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q && SELFCHECK_REQUIRE_TOOLS=1 uv run pytest tests/selfcheck -q -k cargo`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add selfcheck/probes/other_tools.py selfcheck/registry.py tests/selfcheck/test_other_tools.py tests/selfcheck/test_registry.py
git commit -m "feat(selfcheck): проба cargo-machete для Rust-репо (§10.3)"
```

---

### Task 9: документация, проверки, приёмка после мержа

**Files:**
- Modify: `CLAUDE.md` (строка `selfcheck/`)
- Create (dev-only, вне devtools): `../_cowork_output/devtools-selfcheck-s3-acceptance-<дата>.md`

- [ ] **Step 1: Документация и коммит**

`CLAUDE.md` — в строку `selfcheck/` дописать: «`ARGS=--all` (S3) — все репо манифеста одним прогоном: вендор-декларации формата E («весь каталог»), `llm-sites` по месту построения запуска, межрепные ast-дубли без вендор-групп, cargo-machete для Rust; живая приёмка — на master с чистым деревом».

```bash
git add CLAUDE.md
git commit -m "docs(selfcheck): S3 --all в CLAUDE.md"
```

- [ ] **Step 2: Полный набор проверок ветки**

Run: `uv run ruff format --check selfcheck tests/selfcheck && uv run ruff check selfcheck tests/selfcheck && uv run pyrefly check && make selfcheck-dogfood && git status --porcelain`
Expected: всё зелёное, `git status --porcelain` пуст.

- [ ] **Step 3: Приёмка §10.9 на живых данных — после мержа**

На master devtools (после `git pull --ff-only`, чистое дерево):

Run: `./repos.sh pull && make selfcheck ARGS='--all --fleet'`
Expected: отчёт выпущен (код 0 или 2). Скрипт сверки (scratchpad, не коммитится) проверяет §10.9 п. 1–7 по `report.json`:
1. `run.scope` — все 22 репо; `surface.fleet == "complete"`;
2. нет `selfcheck/vendor-pin-unparsed`; `vendor-pin-dangling` — только discovery `src/discovery/contract/PINNED.txt`;
3. строки `inventory.llm` механик A–C покрывают эталон инвентаря 2026-09-02 (таблица «точка → найдена/пропуск» в отчёте приёмки), кроме atp `method/spawners/opencode_shim.py`, `pi_shim.py`;
4. есть находка `ast-dup/*` с участниками `devtools:tools/check_discovery_vendor.py::verify` и `discovery:tools/check_vendor.py::verify`; группа с `gate_check.py::check` — в `vendor_dups`;
5. нет `pyrefly/missing-import` с модулями `atp`, `game_envs`, `atp_sdk`;
6. `cargo-machete` `ok` на arbiter и prograph;
7. нет `ruff/PLR2004`, `ruff/PLC0415` в репо, чей корневой конфиг их не включает.

Результат — отчёт приёмки в `_cowork_output/` с перечнем `partial` по разбору файлов.

- [ ] **Step 4: TODO**

Отдельным маленьким PR после приёмки: `TODO.md` — `- [x] selfcheck S3: …` со ссылкой на отчёт приёмки.
