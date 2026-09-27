# selfcheck S3 — все репо манифеста (`--all`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** один прогон `make selfcheck ARGS='--all --fleet'` по всем репо манифеста с честными статусами: формат вендор-деклараций E, `llm-sites` по месту построения запуска, editable-пути для pyrefly, межрепные ast-дубли без вендор-групп, ruff без `PLR2004`/`PLC0415`, cargo-machete, allowlist по репо.

**Architecture:** правки внутри существующих модулей `selfcheck/` (vendor, roles, env, python_tools, llm + правила semgrep, dups, config, run, report) и одна новая проба `cargo-machete` в `other_tools.py`. Межрепная стадия дублей — функция в `dups.py`, вызываемая `run.main` после цикла по репо. Новых пакетов и зависимостей нет.

**Tech Stack:** Python 3.12+, pytest, semgrep 1.178.0 (`uvx`), pyrefly, ruff 0.16, cargo-machete (системный бинарь, ставит владелец).

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md` rev 5.9, §10 (плюс правки §1.1, §1.4, §2.4, §3.1, §3.4, §7, §9.7). Замер — dev-only `../_cowork_output/devtools-selfcheck-s3-measure-2026-09-27.md`.

## Global Constraints

- Пробы S1–S3 не исполняют код цели; `executes_target_code = false` у всех, включая cargo-machete (§1.2).
- Окружение цели — только данные: `.pth` читаются как текст, строки `import …` не исполняются и никуда не передаются (§1.5, §10.4).
- Пробы получают только путь к read-only копии (§1.3); путь из `.pth` вне checkout цели не передаётся.
- `logic_version` собственного анализатора повышается при изменении логики (§4.3): `llm-sites` 1 → 2, `ast-dup` 1 → 2, `usage-graph` 3 → 4 (формат E и роли).
- `PLR2004`, `PLC0415` не приходят из добавочного набора ruff, если конфиг репо их не включает явно (§10.3).
- Формат E: ровно одна строка `source:`/`repo:`; ref `[0-9a-f]{7,40}`; E в корне репо — неразобран; члены — все файлы корпуса в каталоге декларации и ниже, кроме неё самой; каталог без других файлов — `vendor-pin-dangling`, `text_key` = каталог (§10.5).
- Файлы под `.github/` — не кандидаты в декларации (§10.5).
- Роль `test` дополняется `test/**`, `**/*_test.exs` (§10.5).
- Вендор-группа дублей: не больше одного участника вне `vendored-in` → не находка, строка «вендор-дубли» (§10.6).
- `llm-sites` `$SECOND` у `argv-literal` — обязательно флаг `-x`/`--x…` или `exec`/`run` (§10.7).
- `endpoint`: до пути в литерале нет пробельных символов; для Python — строка кода, не docstring и не комментарий (§10.7).
- `[[allow]] repo` — точное совпадение с `git_dir`, неизвестный — код 4 (§10.8).
- `--all` и `--repo` несовместимы — код 4 (§10.2).
- `uv run`, не pip; `uv run pytest`, `uv run pyrefly check`, `uv run ruff check`/`format` по `selfcheck/` и `tests/selfcheck/` после каждой задачи; строка ≤ 88.

## Review Focus

1. **Формат E снимает dead слишком широко.** Декларация E в каталоге с чужим кодом делает весь каталог `vendored-in`. Ожидание: E только в подкаталоге (корень — неразобран), и роль получает только корпус этого каталога. Тест — Task 1 (`test_e_in_repo_root_is_unparsed`, `test_e_members_are_folder_corpus`).
2. **`endpoint` ловит прозу.** Строка-текст с путём эндпоинта (`"see /v1/messages"`) или docstring. Ожидание: не точка. Тесты — Task 5 (двойники в `test_llm_s3.py`) и существующий `test_inventory_mechanisms` (e.py).
3. **Межрепная группа ломает идентичность S1.** При одном репо в `scope` `id`, `owner_repo`, `locations` дублей обязаны совпасть с S1. Тест — Task 6 (`test_single_repo_groups_match_s1`).
4. **`.pth` с исполняемой строкой или путём наружу.** Ожидание: не исполнена, не передана; путь вне checkout не передан. Тест — Task 3.
5. **`--extend-ignore` отменяет правило, которое репо включило само.** Ожидание: при явном `select`/`extend-select` с `PLR2004` флаг не передаётся. Тест — Task 2.

---

### Task 1: формат E, кандидаты вне `.github/`, роль `test` для Elixir

**Files:**
- Modify: `selfcheck/vendor.py` (формат E, `.github/`, пустой каталог)
- Modify: `selfcheck/roles.py:40` (`Role.TEST` паттерны)
- Modify: `selfcheck/graph/probe.py` (`logic_version=4`)
- Test: `tests/selfcheck/test_vendor.py`, `tests/selfcheck/test_config_manifest.py`

**Interfaces:**
- Produces: `Declaration(path, fmt, owner, ref, members, folder: str | None = None)` — для E `members=()` и `folder` = каталог декларации; `vendor_roles(...)` раскрывает членов E по корпусу. `is_candidate(rel, text, *, is_node)` возвращает `False` для `.github/**`.

- [ ] **Step 1: Write the failing tests**

В `tests/selfcheck/test_vendor.py` дописать:

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
        ("priv/c/idea/v1/PIN", FORMAT_E_AT, "impresario",
         "8082e53b743169137f9e8c72c279043c7166ab03"),
        ("contracts/ls/v1/PINNED.txt", FORMAT_E_COMMIT, "impresario",
         "a9d11fa75bb101d2919dc9f99e075270de5d7976"),
        ("contracts/mv/VENDORED_FROM", FORMAT_E_REPO, "maestro", "346222e3b"),
        ("core/tests/fx/v1/PIN", FORMAT_E_HASH, "devtools",
         "2533ff7b8c3afd74110b3838325bf76ba46ba186"),
    ],
)
def test_format_e(rel: str, text: str, owner: str, ref: str) -> None:
    decl = parse_declaration(rel, text)
    folder = rel.rsplit("/", 1)[0]
    assert (decl.fmt, decl.owner, decl.ref, decl.members, decl.folder) == (
        "E", owner, ref, (), folder
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


def test_e_in_repo_root_is_unparsed() -> None:
    with pytest.raises(DeclarationError):
        parse_declaration("PIN", "source: o@abcdef1 p\n")


def test_uppercase_source_stays_format_a() -> None:
    with pytest.raises(DeclarationError):  # A without member lines
        parse_declaration("x/PIN", "# SOURCE: steward @ 5bfd829\n")


def test_e_members_are_folder_corpus() -> None:
    texts = {
        "contracts/ls/v1/PINNED.txt": FORMAT_E_COMMIT,
        "contracts/ls/v1/schema.json": "{}",
        "contracts/ls/v1/fixtures/ok.json": "{}",
        "contracts/ls/v2/schema.json": "{}",  # sibling folder: not a member
        "tools/check.py": "import json\n",
    }
    res = vendor_roles(
        "r", sorted(texts), texts, role_of, frozenset({"tools/check.py"})
    )
    assert sorted(res.members) == [
        "contracts/ls/v1/fixtures/ok.json",
        "contracts/ls/v1/schema.json",
    ]
    assert res.findings == [] and res.broken is False


def test_e_folder_without_files_is_dangling() -> None:
    texts = {"contracts/gone/v1/PINNED.txt": FORMAT_E_COMMIT, "a.py": "x = 1\n"}
    res = vendor_roles("r", sorted(texts), texts, role_of, frozenset({"a.py"}))
    assert [(f.rule, f.text_key) for f in res.findings] == [
        ("selfcheck/vendor-pin-dangling", "contracts/gone/v1")
    ]
    assert res.broken is True


@pytest.mark.parametrize(
    "rel", [".github/workflows/vendor-drift.yml", ".github/PIN"]
)
def test_github_files_are_not_candidates(rel: str) -> None:
    assert is_candidate(rel, "name: x\n", is_node=False) is False
```

В `tests/selfcheck/test_config_manifest.py` — в параметризацию `test_default_roles` добавить строки:

```python
        ("test/contracts/vendored_test.exs", Role.TEST),
        ("apps/a/test/a_test.exs", Role.TEST),
        ("test/support/fixtures/x.json", Role.TEST),
        ("lib/kapelle/test_helper.ex", Role.SOURCE),
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_vendor.py tests/selfcheck/test_config_manifest.py -q`
Expected: FAIL — `test_format_e` (`DeclarationError: not a declaration`), `test_e_members_are_folder_corpus`, `test_e_folder_without_files_is_dangling`, `test_github_files_are_not_candidates`, новые строки `test_default_roles`; `test_e_unparsed`, `test_e_in_repo_root_is_unparsed`, `test_uppercase_source_stays_format_a` проходят уже сейчас (двойники — ok).

- [ ] **Step 3: Implement**

`selfcheck/roles.py` — строка `Role.TEST`:

```python
    Role.TEST: (
        "tests/**",
        "**/test_*.py",
        "**/*_test.py",
        "test/**",
        "**/*_test.exs",
    ),
```

`selfcheck/vendor.py` — docstring модуля дополнить строкой про E; добавить:

```python
_E_LINE = re.compile(r"^(?:#\s*)?([A-Za-z_]+):\s*(.*)$")
_E_SOURCE_AT = re.compile(rf"^(\S+?)@({_HEX})\b")
_E_REF = re.compile(rf"^({_HEX})\b")
```

`Declaration` — поле `folder: str | None = None`.

`is_candidate` — первой строкой:

```python
    if rel.startswith(".github/"):
        return False
```

Новый разбор (вызывается после C, до B):

```python
def _e_keys(text: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for raw in text.splitlines():
        m = _E_LINE.match(raw.strip())
        if m:
            keys.setdefault(m.group(1).lower(), []).append(m.group(2).strip())
    return keys


def _parse_e(rel: str, text: str) -> Declaration | None:
    keys = _e_keys(text)
    heads = keys.get("source", []) + keys.get("repo", [])
    if not heads:
        return None
    folder = posixpath.dirname(rel)
    if not folder:
        raise DeclarationError(f"{rel}: folder declaration in the repo root")
    if len(heads) != 1:
        raise DeclarationError(f"{rel}: more than one source/repo line")
    commits = keys.get("commit", [])
    head = heads[0]
    at = _E_SOURCE_AT.match(head) if "source" in keys else None
    if at is not None:
        owner, ref = at.group(1), at.group(2)
    else:
        if len(commits) != 1 or not _E_REF.match(commits[0]):
            raise DeclarationError(f"{rel}: no hex ref for the folder declaration")
        ref = _E_REF.match(commits[0]).group(1)  # type: ignore[union-attr]
        token = head.split()[0] if head.split() else ""
        owner = (
            posixpath.basename(token.rstrip("/")).removesuffix(".git")
            if "repo" in keys
            else token
        )
    if not owner:
        raise DeclarationError(f"{rel}: no owner")
    return Declaration(rel, "E", owner, ref, (), folder)
```

`parse_declaration` — `return _parse_a(...) or _parse_c(...) or _parse_e(rel, text) or _parse_b(...)`.

`vendor_roles` — после успешного разбора:

```python
        members = decl.members
        if decl.folder is not None:
            prefix = decl.folder + "/"
            members = tuple(p for p in sorted(known) if p.startswith(prefix) and p != rel)
            if not members:
                res.findings.append(
                    _finding(
                        repo, "selfcheck/vendor-pin-dangling", "medium", rel,
                        decl.folder,
                    )
                )
                continue
        for member in members:
            ...  # прежний цикл, итерирует members вместо decl.members
```

`_finding(... suggestion=...)` — «(формат A–E, §9.7, §10.5)». `graph/probe.py` — `USAGE_GRAPH` `logic_version=4`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком (прежние `test_four_formats`, `test_unparsed`, `test_candidates` зелёные — ни один их вход не содержит `source:`/`repo:` в нижнем регистре).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/vendor.py selfcheck/roles.py selfcheck/graph/probe.py tests/selfcheck/test_vendor.py tests/selfcheck/test_config_manifest.py
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


def test_ruff_extra_set_without_magic_and_lazy_import(build, tmp_path: Path) -> None:
    res = run(RUFF, build({"a.py": MAGIC}), tmp_path)
    rules = {f.rule for f in res.findings}
    assert not rules & {"ruff/PLR2004", "ruff/PLC0415"}
    assert "--extend-ignore" in res.argv


@pytest.mark.parametrize(
    "tool_ruff",
    [
        '[tool.ruff.lint]\nselect = ["E", "PLR2004"]\n',
        '[tool.ruff.lint]\nextend-select = ["PLC0415"]\n',
        '[tool.ruff]\nselect = ["PLR2"]\n',
    ],
)
def test_repo_that_selects_them_keeps_them(build, tmp_path: Path, tool_ruff: str) -> None:
    pyproject = PYPROJECT.replace("[tool.ruff]\n", "") + tool_ruff
    res = run(RUFF, build({"a.py": MAGIC}, pyproject=pyproject), tmp_path)
    assert "--extend-ignore" not in res.argv


def test_ruff_rules_name_the_ignores() -> None:
    assert RUFF.rules[-2:] == ("-PLR2004", "-PLC0415")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_python_tools.py -q -k "magic or selects_them or name_the_ignores"`
Expected: FAIL — `ruff/PLR2004` в находках; `--extend-ignore` нет в argv; `rules` без `-PLR2004`.

- [ ] **Step 3: Implement**

```python
RUFF_IGNORE = ("PLR2004", "PLC0415")
# prefixes that only come from our own extra set (§10.3)
_OURS = frozenset({"ALL", "PL", "PLR", "PLC"})


def _repo_selects(ctx: ProbeCtx, code: str) -> bool:
    """The repo config selects ``code`` itself (a code or a prefix but ours)."""
    for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        data = _toml(ctx, name)
        root = (
            data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
        )
        chosen = [
            *root.get("select", []),
            *root.get("extend-select", []),
            *root.get("lint", {}).get("select", []),
            *root.get("lint", {}).get("extend-select", []),
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
- Modify: `selfcheck/env.py` (`EnvInfo.search_paths`, `editable_paths`)
- Modify: `selfcheck/probes/python_tools.py:131-151` (`_pyrefly_argv`)
- Modify: `selfcheck/run.py:254` (`acc.env[...]["search_paths"]`)
- Test: `tests/selfcheck/test_env.py`, `tests/selfcheck/test_python_tools.py`

**Interfaces:**
- Produces: `EnvInfo.search_paths: tuple[str, ...] = ()` — пути **относительно корня checkout** (`"."` — сам корень); `editable_paths(repo: Path, site: Path) -> tuple[str, ...]`.

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
    i = res.argv.index("--search-path")
    assert res.argv[i + 1] == str(target.copy / "packages" / "core")
    assert str(target.source) not in " ".join(res.argv)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_env.py tests/selfcheck/test_python_tools.py -q -k "editable"`
Expected: FAIL — `AttributeError: 'EnvInfo' object has no attribute 'search_paths'`.

- [ ] **Step 3: Implement**

`selfcheck/env.py`:

```python
@dataclass(frozen=True)
class EnvInfo:
    ...
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
            path = (site / line).resolve() if not Path(line).is_absolute() else Path(
                line
            ).resolve()
            if path == root or root in path.parents:
                found.add(path.relative_to(root).as_posix() or ".")
    return tuple(sorted(found))
```

`detect_env` — `return EnvInfo("checkout-venv", stale, sites[0], version, editable_paths(repo, sites[0]))`.

`_pyrefly_argv` — внутри ветки `checkout-venv`:

```python
        for rel in env.search_paths:
            args += ["--search-path", str(ctx.target.copy / rel)]
```

`run.py` — `acc.env[repo.name] = {"mode": env.mode, "stale": env.stale, "search_paths": len(env.search_paths)}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком; `test_pyrefly_*` и `test_deptry_env_as_data` зелёные (evil `.pth` фикстуры — строка `import`, пропущена).

- [ ] **Step 5: Commit**

```bash
git add selfcheck/env.py selfcheck/probes/python_tools.py selfcheck/run.py tests/selfcheck/test_env.py tests/selfcheck/test_python_tools.py
git commit -m "feat(selfcheck): editable-пути .pth как данные → pyrefly --search-path в копии (§10.4)"
```

---

### Task 4: `[[allow]] repo`

**Files:**
- Modify: `selfcheck/config.py:21-80` (`AllowEntry.repo`)
- Modify: `selfcheck/run.py:359-362` (проверка `repo` по манифесту)
- Modify: `selfcheck.toml` (`repo = "devtools"` у `issue_console.py`)
- Test: `tests/selfcheck/test_config_manifest.py`, `tests/selfcheck/test_run.py`

**Interfaces:**
- Produces: `AllowEntry.repo: str | None = None`; `matches(finding)` — `False`, если `repo` задан и `finding.owner_repo != repo`.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_config_manifest.py`:

```python
def test_allow_repo_limits_the_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = "a"\nreason = "r"\n'
        "until = 2099-01-01\n"
    )
    entry = load_config(cfg).allow[0]
    mine = Finding("r/x", "quality", "low", Confidence.LIKELY, "a", "file:x.py", [])
    other = replace(mine, owner_repo="b")
    assert entry.matches(mine) and not entry.matches(other)
```

`tests/selfcheck/test_run.py`:

```python
def test_allow_repo_not_in_manifest_is_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    cfg = tmp_path / "s.toml"
    cfg.write_text(
        '[[allow]]\nanchor = "file:x.py"\nrepo = "nope"\nreason = "r"\n'
        "until = 2099-01-01\n"
    )
    assert main([*args(ws), "--config", str(cfg), "--probe", "ruff"]) == 4
```

(в `test_config_manifest.py` импортировать `from dataclasses import replace`, `Finding`, `Confidence`, если их нет; `args` в `test_run.py` уже есть.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_config_manifest.py tests/selfcheck/test_run.py -q -k "allow_repo"`
Expected: FAIL — `other` совпал (поле игнорируется); код выхода не 4.

- [ ] **Step 3: Implement**

`AllowEntry` — поле `repo: str | None = None`; в `matches` первой строкой:

```python
        if self.repo is not None and finding.owner_repo != self.repo:
            return False
```

`_allow_entry` — `repo=raw.get("repo")`. `run.main` — рядом с проверкой `[[operator]]`:

```python
        stray_allow = sorted(
            {a.repo for a in config.allow if a.repo} - set(manifest.order)
        )
        if stray_allow:
            raise ConfigError(f"[[allow]] repo not in manifest: {stray_allow}")
```

`selfcheck.toml` — в запись `issue_console.py` добавить `repo = "devtools"`.

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
- Modify: `selfcheck/rules/llm.yml`
- Modify: `selfcheck/llm.py` (`_MECHANISM`, `_select`, `_site`, `_parse`, `logic_version=2`)
- Test: `tests/selfcheck/test_llm_s3.py` (новый)

**Interfaces:**
- Consumes: `python_files`, `shell_files` (как в S1).
- Produces: `ts_files(target) -> tuple[str, ...]`; `code_endpoint(text: str, line: int) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_llm_s3.py`:

```python
"""S3 Task 5 — llm-sites: launch construction, harness resolve, TS (§10.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, code_endpoint
from selfcheck.probes.base import ProbeResult, ProbeStatus, RepoTarget, canary_files, run_probe
from tests.selfcheck.helpers import NOW, make_repo, require_tool

SPAWNER = '''def argv(model, prompt):
    return ["claude", "--model", model, "-p", prompt]
'''
CONFIGURED = '''def run(config, prompt, runner):
    return runner([config.claude_command, "-p", prompt])
'''
PRIVATE_CMD = '''class D:
    def cmd(self, prompt):
        return [self._claude_command, "--print", "-p", prompt]
'''
RESOLVE = '''import os
import shutil

BIN = os.environ.get("CLAUDE_BIN", "claude")


def find():
    return shutil.which("codex")
'''
URL_F = '''def url(host):
    return f"{host.rstrip('/')}/v1/chat/completions"
'''
URL_OLLAMA = '''def url(host):
    return host + "/api/chat"
'''
DOC_URL = '''"""Calls the HTTP `/api/chat` endpoint of ollama."""


def f():
    # POST http://localhost:11434/api/chat
    return "see /v1/messages for details"
'''
NOT_ARGV = '''NAMES = {"codex_cli": "codex", "claude_code": "claude"}
BACKENDS = ("codex", "disp")


def add(parser):
    parser.add_argument("--author", choices=["codex", "disp"], default="codex")
'''
TS_SPAWN = 'import { spawn } from "node:child_process";\nexport const go = (p: string) => spawn("claude", ["-p", p]);\n'
TS_ARGV = 'export const argv = (p: string) => ["codex", "exec", p];\n'
TS_SDK = 'import OpenAI from "openai";\nexport const c = new OpenAI();\n'
TS_URL = "export const u = (h: string) => `${h}/v1/chat/completions`;\n"
TS_COMMENT = "// fetch(`${h}/v1/chat/completions`)\nexport const x = 1;\n"


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> ProbeResult:
    require_tool("uvx")
    tmp = tmp_path_factory.mktemp("s3llm")
    files = {
        "spawner.py": SPAWNER, "configured.py": CONFIGURED, "private.py": PRIVATE_CMD,
        "resolve.py": RESOLVE, "url_f.py": URL_F, "url_ollama.py": URL_OLLAMA,
        "doc_url.py": DOC_URL, "not_argv.py": NOT_ARGV, "spawn.ts": TS_SPAWN,
        "argv.ts": TS_ARGV, "sdk.ts": TS_SDK, "url.ts": TS_URL, "comment.ts": TS_COMMENT,
    }
    repo = make_repo(tmp / "repo", files)
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
        ("configured.py", "A", "argv-literal"),
        ("private.py", "A", "argv-literal"),
        ("resolve.py", "A", "harness-resolve"),
        ("url_f.py", "C", "endpoint"),
        ("url_ollama.py", "C", "endpoint"),
        ("spawn.ts", "A", "spawn-ts"),
        ("argv.ts", "A", "argv-literal-ts"),
        ("sdk.ts", "B", "sdk-ts"),
        ("url.ts", "C", "endpoint"),
    ],
)
def test_each_rule_fires(result: ProbeResult, path: str, mechanism: str, rule: str) -> None:
    assert result.status is ProbeStatus.OK and result.canary == "hit"
    assert (path, mechanism, rule) in _rows(result)


@pytest.mark.parametrize("path", ["doc_url.py", "not_argv.py", "comment.ts"])
def test_negative_twins(result: ProbeResult, path: str) -> None:
    assert not any(r[0] == path for r in _rows(result))


def test_rows_deduplicated(result: ProbeResult) -> None:
    keys = [(i["path"], i["line"]) for i in result.extra["inventory"]]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("text", "line", "hit"),
    [
        (URL_F, 2, True),
        (URL_OLLAMA, 2, True),
        (DOC_URL, 1, False),
        (DOC_URL, 5, False),
        (DOC_URL, 6, False),
    ],
)
def test_code_endpoint(text: str, line: int, hit: bool) -> None:
    assert code_endpoint(text, line) is hit


def test_logic_version_bumped() -> None:
    assert LLM_SITES.logic_version == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_llm_s3.py -q`
Expected: FAIL — `ImportError: cannot import name 'code_endpoint'`.

- [ ] **Step 3: Implement**

`selfcheck/rules/llm.yml` — дописать (регекс харнессов — тот же список, что `HARNESSES` в `llm.py`):

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
      - pattern: import { $...X } from "@openai/agents"
  - id: endpoint
    languages: [python, js, ts, bash]
    severity: INFO
    message: LLM endpoint URL (mechanism C)
    pattern-regex: (?:/v1/messages|/chat/completions|/api/chat|/api/generate|/completion)\b
```

`selfcheck/llm.py`:

```python
_MECHANISM = {
    "py-launch": "A", "cli-shell": "A", "argv-literal": "A", "argv-literal-ts": "A",
    "harness-resolve": "A", "spawn-ts": "A", "sdk-python": "B", "sdk-ts": "B",
    "http-python": "C", "endpoint": "C",
}
_TS_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs")
_ENDPOINT = re.compile(
    r"^\S*?(?:/v1/messages|/chat/completions|/api/chat|/api/generate|/completion)\b"
)
_QUOTED_ENDPOINT = re.compile(
    r"[\"'`][^\"'`\s]*(?:/v1/messages|/chat/completions|/api/chat|/api/generate"
    r"|/completion)\b"
)


def ts_files(target: RepoTarget) -> tuple[str, ...]:
    """Corpus TS/JS files except canaries (spec §10.7)."""
    return tuple(
        p
        for p in target.corpus
        if p.endswith(_TS_SUFFIXES) and role_of(p, target.roles) is not Role.CANARY
    )


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
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
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        value = _literal(node)
        if value is None or id(node) in skip:
            continue
        if node.lineno <= line <= (node.end_lineno or node.lineno):  # type: ignore[attr-defined]
            if _ENDPOINT.search(value):
                return True
    return False
```

`_select` — `tuple(dict.fromkeys((*python_files(target), *shell_files(target), *ts_files(target))))`.

`_site` — в начале:

```python
    text = source_text(ctx, rel)
    lines = text.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    if rule == "endpoint":
        if rel.endswith(".py"):
            if not code_endpoint(text, line):
                return None
        elif line_text.lstrip().startswith(("#", "//", "*")) or not (
            _QUOTED_ENDPOINT.search(line_text)
        ):
            return None
```

и заменить ветку `else:` (не-Python) так, чтобы использовать уже вычисленный `line_text`; якорь для TS — `llm:{rel}`, как у shell. В `_parse` — дедупликация: перед `inventory.append(row)`:

```python
        if (row["path"], row["line"]) in seen:
            continue
        seen.add((row["path"], row["line"]))
```

(`seen: set[tuple[str, int]] = set()` перед циклом). `LLM_SITES.logic_version = 2`; `rules=("A", "B", "C", "D", "candidate:schema|loop", "construction")`.

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
- Modify: `selfcheck/dups.py:126-235` (`FuncHash` в данных пробы; `group_dups`)
- Modify: `selfcheck/run.py` (`_Run.hashes`, `_Run.vendor_dups`, вызов после цикла)
- Modify: `selfcheck/report.py` (таблица «Вендор-дубли»)
- Test: `tests/selfcheck/test_dups.py`, `tests/selfcheck/test_run.py`

**Interfaces:**
- Consumes: `acc.vendored[repo]` — `{path: [...]}` из `usage-graph` (§9.7).
- Produces: `group_dups(hashes: Mapping[str, list[FuncHash]], vendored: Mapping[str, Collection[str]] | None) -> tuple[list[Finding], list[dict[str, Any]]]` — находки и строки вендор-дублей; `ast-dup` пишет `extra["hashes"] = [asdict(h) …]` и возвращает только находки канарейки.

- [ ] **Step 1: Write the failing tests**

`tests/selfcheck/test_dups.py`:

```python
from selfcheck.dups import FuncHash, dup_findings, group_dups

BODY = "".join(f"    v{i} = x * {i}\n" for i in range(8))


def _h(path: str, name: str = "f") -> FuncHash:
    return function_hashes(f"def {name}(x):\n{BODY}    return x\n", path)[0]


def test_cross_repo_exact_is_one_group() -> None:
    found, vendor = group_dups({"b": [_h("q.py")], "a": [_h("p.py")]}, {})
    (f,) = found
    assert (f.rule, f.owner_repo, f.confidence.value) == ("ast-dup/exact", "a", "confirmed")
    assert [(r["owner_repo"], r["path"]) for r in f.related] == [("a", "p.py"), ("b", "q.py")]
    assert [loc.path for loc in f.locations] == ["p.py"]
    assert vendor == []


def test_upstream_plus_declared_copies_is_vendor_group() -> None:
    hashes = {"up": [_h("g.py")], "c1": [_h("v/g.py")], "c2": [_h("w/g.py")]}
    found, vendor = group_dups(hashes, {"c1": {"v/g.py"}, "c2": {"w/g.py"}})
    assert found == [] and len(vendor) == 1
    assert sorted(m["owner_repo"] for m in vendor[0]["members"]) == ["c1", "c2", "up"]


def test_undeclared_copies_stay_a_finding() -> None:
    hashes = {"up": [_h("g.py")], "c1": [_h("v/g.py")], "c2": [_h("w/g.py")]}
    found, vendor = group_dups(hashes, {"c1": {"v/g.py"}})
    assert len(found) == 1 and vendor == []


def test_single_repo_groups_match_s1() -> None:
    hs = [_h("a.py", "f"), _h("b.py", "g")]
    found, _ = group_dups({"r": hs}, None)
    assert [x.to_json() for x in found] == [x.to_json() for x in dup_findings(hs, "r")]
```

`test_ast_dup_probe` в том же файле меняется (проба больше не выпускает группы репо — их выпускает прогон):

```python
def test_ast_dup_probe(tmp_path: Path) -> None:
    res = run(AST_DUP, tmp_path, {"a.py": func("one"), "b.py": func("two")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert res.findings == []  # only the canary group, subtracted by the core
    hashes = [FuncHash(**{**h, "literals": tuple(h["literals"])}) for h in res.extra["hashes"]]
    assert [f.rule for f in group_dups({"repo": hashes}, None)[0]] == ["ast-dup/exact"]
```

`tests/selfcheck/test_run.py` — двухрепный прогон (использовать `make_repo` из helpers и манифест с двумя `[tools.*]`):

```python
def test_cross_repo_dup_in_one_run(tmp_path: Path) -> None:
    body = "".join(f"    v{i} = x * {i}\n" for i in range(8))
    src = f"def helper(x):\n{body}    return x\n"
    ws = workspace(tmp_path, {"h.py": src})
    make_repo(tmp_path / "other", {"pyproject.toml": '[project]\nname = "o"\nversion = "0"\n', "g.py": src})
    (tmp_path / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    main([*args(ws), "--repo", "devtools", "--repo", "other", "--probe", "ast-dup"])
    doc = reports(ws)[-1]
    groups = [f for f in doc["findings"] if f["rule"] == "ast-dup/exact"]
    assert len(groups) == 1
    assert {r["owner_repo"] for r in groups[0]["related"]} == {"devtools", "other"}
```


- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_dups.py tests/selfcheck/test_run.py -q -k "cross_repo or vendor_group or undeclared or match_s1"`
Expected: FAIL — `ImportError: cannot import name 'group_dups'`.

- [ ] **Step 3: Implement**

`dups.py`:

```python
def _owner_key(member: tuple[str, FuncHash]) -> tuple[str, str, int]:
    return (member[0], member[1].path, member[1].line)


def _group(rule, kind, key, members, confidence, evidence) -> Finding:
    ordered = sorted(members, key=_owner_key)
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


def group_dups(
    hashes: Mapping[str, list[FuncHash]],
    vendored: Mapping[str, Collection[str]] | None,
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Duplicate groups over every repo of the run; vendor groups aside (§10.6)."""
    by_exact: dict[str, list[tuple[str, FuncHash]]] = defaultdict(list)
    by_struct: dict[str, list[tuple[str, FuncHash]]] = defaultdict(list)
    for repo, items in hashes.items():
        for h in items:
            by_exact[h.exact].append((repo, h))
            by_struct[h.structural].append((repo, h))
    found: list[Finding] = []
    vendor_rows: list[dict[str, Any]] = []

    def outside(members: list[tuple[str, FuncHash]]) -> int:
        if vendored is None:
            return len(members)
        return sum(1 for r, h in members if h.path not in vendored.get(r, ()))

    def emit(finding: Finding, members: list[tuple[str, FuncHash]]) -> None:
        if vendored is not None and outside(members) <= 1:
            vendor_rows.append({"anchor": finding.anchor, "members": finding.related})
        else:
            found.append(finding)

    for key, group in by_exact.items():
        if len(group) >= 2:
            emit(_group("ast-dup/exact", "exact", key, group, Confidence.CONFIRMED, []), group)
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
            for r, h in sorted(group, key=_owner_key)
        ]
        emit(
            _group("ast-dup/structural", "structural", key, group, Confidence.CANDIDATE, evidence),
            group,
        )
    return found, vendor_rows
```

`dup_findings(hashes, repo)` — тонкая обёртка `return group_dups({repo: hashes}, None)[0]` (канарейка и S1-тесты идут через неё; вывод при одном репо побайтно прежний: префикс репо в `evidence` только у межрепной группы; сортировка участников по `(repo, path, line)` при одном репо совпадает с прежней `(path, line)`). Прежние `_dup` и тело `dup_findings` удаляются.

`_ast_dup` — для файлов репо копить `hashes`, **не** звать `dup_findings`; для канарейки — как было. `result.extra["hashes"] = [dataclasses.asdict(h) for h in hashes]`. `AST_DUP.logic_version = 2`.

`run.py`: в `_Run` — `hashes: dict[str, list[FuncHash]]`, `vendor_dups: list[dict[str, Any]]`; в `_scan_repo` для `r.probe == "ast-dup"` — `acc.hashes[repo.name] = [FuncHash(**h) for h in r.extra.get("hashes", [])]` (поле `literals` — кортеж: `FuncHash(**{**h, "literals": tuple(h["literals"])})`). В `main` после цикла по репо:

```python
    if acc.hashes:
        have_roles = all(n in acc.vendored for n in acc.hashes)
        vendored = {n: set(v) for n, v in acc.vendored.items()} if have_roles else None
        if not have_roles:
            acc.warnings.append("ast-dup: вендор-фильтр не применён — нет usage-graph (§10.6)")
        dups, acc.vendor_dups = group_dups(acc.hashes, vendored)
        acc.findings += dups
```

`_document` — `"vendor_dups": acc.vendor_dups`. `report.py` — после таблицы вендор-копий: `## Вендор-дубли (N)` со строками `- <anchor>: repo:path::member, …`, при N = 0 раздел не печатается.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/dups.py selfcheck/run.py selfcheck/report.py tests/selfcheck/test_dups.py tests/selfcheck/test_run.py
git commit -m "feat(selfcheck): межрепные ast-дубли одной группой, вендор-группы не находка (§10.6)"
```

---

### Task 7: `--all`, сводная таблица по репо, репо в инвентаре

**Files:**
- Modify: `selfcheck/run.py` (`--all`, `wanted`, `repo` в строках инвентаря)
- Modify: `selfcheck/report.py` (`_repo_rows`, раздел «## Репо»)
- Modify: `Makefile:60` (help)
- Test: `tests/selfcheck/test_run.py`, `tests/selfcheck/test_report.py`

**Interfaces:**
- Produces: `--all` (store_true); `inventory.llm[*].repo`; `render_markdown` печатает «## Репо» при `len(scope) > 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_all_scans_every_manifest_repo(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    make_repo(tmp_path / "other", {"a.sh": "#!/bin/sh\necho hi\n"})
    (tmp_path / "m.toml").write_text(
        '[tools.devtools]\ngit_dir = "devtools"\n[tools.other]\ngit_dir = "other"\n'
    )
    main([*args(ws), "--all", "--probe", "shellcheck"])
    doc = reports(ws)[-1]
    assert doc["run"]["scope"] == ["devtools", "other"]
    md = next((ws / "out").glob("*/report.md")).read_text()
    assert "## Репо" in md and "| other |" in md


def test_all_with_repo_is_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    assert main([*args(ws), "--all", "--repo", "devtools"]) == 4


def test_inventory_rows_carry_repo(tmp_path: Path) -> None:
    ws = workspace(tmp_path, {"c.py": 'import subprocess\nsubprocess.run(["claude", "-p", "x"])\n'})
    main([*args(ws), "--probe", "llm-sites"])
    doc = reports(ws)[-1]
    assert {r["repo"] for r in doc["inventory"]["llm"]} == {"devtools"}
```

(`llm-sites`-тест гейтится `require_tool("uvx")`; shellcheck — `require_probe("shellcheck", …)` как в соседних тестах.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_run.py -q -k "all_ or carry_repo"`
Expected: FAIL — `unrecognized arguments: --all`; `KeyError: 'repo'`.

- [ ] **Step 3: Implement**

`_args` — `parser.add_argument("--all", action="store_true", help="every manifest repo (spec §10.2)")`. `main`:

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
    """One row per scope repo: probes not ok/skipped, findings by category (§10.2)."""
    scope = doc["run"]["scope"]
    if len(scope) < 2:
        return []
    lines = [
        "## Репо", "",
        "| репо | пробы не ok | находок | dead c/l/cand | duplicate | llm |",
        "|---|---|---|---|---|---|",
    ]
    for repo in scope:
        mine = [f for f in doc["findings"] if f["owner_repo"] == repo]
        bad = [
            f"{p['probe']}:{p['status']}"
            for p in doc["probes"]
            if p["repo"] == repo and p["status"] not in ("ok", "skipped")
        ]
        dead = [f["confidence"] for f in mine if f["rule"].startswith("usage-graph/dead")]
        dc = "/".join(str(dead.count(c)) for c in ("confirmed", "likely", "candidate"))
        dup = sum(1 for f in mine if f["category"] == "duplicate")
        llm = sum(1 for f in mine if f["category"] == "llm-replaceable")
        lines.append(
            f"| {repo} | {', '.join(bad) or '—'} | {len(mine)} | {dc} | {dup} | {llm} |"
        )
    return [*lines, ""]
```

`render_markdown` — вставить `lines += _repo_rows(doc)` перед «## Пробы». `Makefile` help — `ARGS='[--repo r | --all] …'`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 5: Commit**

```bash
git add selfcheck/run.py selfcheck/report.py Makefile tests/selfcheck/test_run.py tests/selfcheck/test_report.py
git commit -m "feat(selfcheck): --all по всем репо манифеста, сводная таблица, репо в инвентаре (§10.2)"
```

---

### Task 8: проба `cargo-machete`

**Files:**
- Modify: `selfcheck/probes/other_tools.py` (новая `CARGO_MACHETE`)
- Modify: `selfcheck/registry.py` (регистрация)
- Test: `tests/selfcheck/test_other_tools.py`

**Interfaces:**
- Produces: `CARGO_MACHETE: ProbeSpec` (`languages={"rust"}`, `input_mode="roots"`, `binary="cargo-machete"`).

- [ ] **Step 1: Замер закреплённой версии (стоп-точка)**

Run: `cargo-machete --version`
Expected: печатает версию. **Если бинаря нет — стоп и вопрос владельцу** (установка — `cargo install cargo-machete`, §10.3); задачу не исполнять вслепую.

Затем на минимальном крейте (скрытый и не скрытый каталоги):

```bash
T=$(mktemp -d); mkdir -p $T/.hid/c/src $T/vis/c/src
for d in .hid vis; do
  printf '[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nlibc = "0.2"\n' > $T/$d/c/Cargo.toml
  echo 'pub fn f() {}' > $T/$d/c/src/lib.rs
done
cargo-machete $T; echo "rc=$?"
```

Expected: rc 1 и в выводе `libc` для `vis/c/Cargo.toml`; видно, обходит ли инструмент `.hid`. Зафиксировать в ledger: точную версию (диапазон `version_range` = [версия, следующая минорная)), формат строк, rc при «нет находок» (ожидается 0) и при находках (1), обход скрытых. Если формат отличается от разбора ниже — ruling и правка регекса до Step 2.

- [ ] **Step 2: Write the failing tests**

```python
RUST_OK = {
    "Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n',
    "src/lib.rs": "pub fn f() {}\n",
}


def test_cargo_machete_unused_dependency(tmp_path: Path) -> None:
    require_probe("cargo-machete", CARGO_MACHETE.version_args, CARGO_MACHETE.version_range)
    files = {**RUST_OK, "Cargo.toml": RUST_OK["Cargo.toml"] + 'libc = "0.2"\n'}
    res = _run(CARGO_MACHETE, files, tmp_path)  # хелпер файла: make_repo → copy → run_probe
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert [(f.rule, f.text_key, f.category) for f in res.findings] == [
        ("cargo-machete/unused-dependency", "a:libc", "deps")
    ]


def test_cargo_machete_clean_repo_ok(tmp_path: Path) -> None:
    require_probe("cargo-machete", CARGO_MACHETE.version_args, CARGO_MACHETE.version_range)
    res = _run(CARGO_MACHETE, RUST_OK, tmp_path)
    assert res.status is ProbeStatus.OK and res.findings == []


def test_cargo_machete_is_static() -> None:
    assert CARGO_MACHETE.executes_target_code is False
```

(`_run` — хелпер в `test_other_tools.py` по образцу фикстуры `build` из `test_python_tools.py`: `make_repo` → `list_corpus` → `materialize(..., canary_files([CARGO_MACHETE]))` → `RepoTarget(..., frozenset({"rust"}), ...)` → `run_probe` → `release`.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/selfcheck/test_other_tools.py -q -k cargo`
Expected: FAIL — `ImportError: cannot import name 'CARGO_MACHETE'`.

- [ ] **Step 4: Implement**

Разбор — по замеру Step 1; исходный вариант (текстовый вывод 0.x: строка `<crate> -- <path>/Cargo.toml:` и далее строки зависимостей с отступом):

```python
_CM_CRATE = re.compile(r"^(\S+) -- (.+Cargo\.toml):$")
_CM_DEP = re.compile(r"^\s+(\S+)$")
_CM_CANARY = "selfcheck_canary/cargo_machete/Cargo.toml"


def _cm_parse(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    result = ParseResult([])
    crate, manifest = None, None
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


CARGO_MACHETE = ProbeSpec(
    name="cargo-machete",
    languages=frozenset({"rust"}),
    input_mode="roots",
    select=lambda target: (".",),
    canary=Canary(
        _CM_CANARY,
        '[package]\nname = "selfcheck_canary"\nversion = "0.1.0"\nedition = "2021"\n'
        '[dependencies]\nlibc = "0.2"\n',
        "cargo-machete/unused-dependency",
        f"file:{_CM_CANARY}",
    ),
    rules=("unused-dependency",),
    binary="cargo-machete",
    version_range=((0, 9), (0, 10)),  # по замеру Step 1
    normal_codes=frozenset({0, 1}),
    argv=lambda ctx: [str(ctx.target.copy)],
    parse=_cm_parse,
)
```

Канарейке нужен `src/lib.rs`: `Canary` несёт один файл — если cargo-machete без `src/` не разбирает крейт (замер Step 1), канарейка переходит на `[lib] path = "Cargo.toml"`-трюк или на расширение `canary_files` вторым файлом; решение — ruling. `registry.py` — добавить `CARGO_MACHETE` в `REGISTRY` после `JSCPD`. `tests/selfcheck/test_registry.py` — если он перечисляет пробы, дополнить.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/selfcheck -q`
Expected: PASS целиком.

- [ ] **Step 6: Commit**

```bash
git add selfcheck/probes/other_tools.py selfcheck/registry.py tests/selfcheck/test_other_tools.py tests/selfcheck/test_registry.py
git commit -m "feat(selfcheck): проба cargo-machete для Rust-репо (§10.3)"
```

---

### Task 9: документация и приёмка на живых данных

**Files:**
- Modify: `CLAUDE.md` (строка `selfcheck/` в таблице инструментов), `README.md` (если описывает selfcheck), `TODO.md:2655` (`[x]` после приёмки)
- Create: `../_cowork_output/devtools-selfcheck-s3-acceptance-<дата>.md` (dev-only, не коммитится в devtools)

- [ ] **Step 1: Документация**

`CLAUDE.md` — в строку `selfcheck/` дописать: «`ARGS=--all` (S3) — все репо манифеста одним прогоном: формат вендор-деклараций E («весь каталог»), `llm-sites` по месту построения запуска, межрепные ast-дубли без вендор-групп, cargo-machete для Rust».

- [ ] **Step 2: Полный набор проверок**

Run: `uv run ruff format --check selfcheck tests/selfcheck && uv run ruff check selfcheck tests/selfcheck && uv run pyrefly check && make selfcheck-dogfood`
Expected: всё зелёное.

- [ ] **Step 3: Приёмка §10.9 на живых данных**

Run: `./repos.sh pull && make selfcheck ARGS='--all --fleet'`
Expected: отчёт выпущен (код 0 или 2). Скрипт сверки (в scratchpad, не коммитится) проверяет пункты 1–7 §10.9 по `report.json`:
1. `run.scope` = все 22 репо; `surface.fleet == "complete"`;
2. нет `selfcheck/vendor-pin-unparsed`; `vendor-pin-dangling` — только discovery `src/discovery/contract/PINNED.txt`;
3. строки `inventory.llm` механик A–C покрывают эталон инвентаря 2026-09-02 (список точек — таблица в отчёте приёмки), кроме atp `method/spawners/opencode_shim.py`, `pi_shim.py`;
4. находка `ast-dup/*` с участниками `devtools:tools/check_discovery_vendor.py::verify` и `discovery:tools/check_vendor.py::verify` есть; группа с `gate_check.py::check` — в `vendor_dups`, не в `findings`;
5. нет `pyrefly/missing-import` с модулями `atp`, `game_envs`, `atp_sdk`;
6. `cargo-machete` `ok` на arbiter и prograph;
7. нет `ruff/PLR2004`, `ruff/PLC0415` в репо, чей конфиг их не включает.

Результат — отчёт приёмки в `_cowork_output/` с таблицей эталона (точка → найдена/пропуск) и перечнем `partial` по разбору файлов.

- [ ] **Step 4: TODO и коммит**

`TODO.md` — `- [x] selfcheck S3: …`.

```bash
git add CLAUDE.md README.md TODO.md
git commit -m "docs(selfcheck): S3 --all — CLAUDE.md, TODO"
```
