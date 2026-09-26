# selfcheck S2 — план реализации (`--fleet`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** этап S2 selfcheck — `--fleet`. Прогон читает все репо манифеста и
командный зонтик двумя каналами:
- точным (рёбра `fleet`);
- текстовым (потолок P5 по ключам узла).

Кроме того, этап вводит:
- канарейку флота на каждом прогоне;
- строгое `fleet = complete`, при котором `confirmed` dead становится
  достижим;
- роль `vendored-in` из вендор-деклараций с fail-closed.

**Architecture:** новый подпакет `selfcheck/fleet/`:
- `reader` — чтение репо флота и его состояния;
- `match` — точный и текстовый каналы;
- `assemble` — состав флота, полнота, канарейка, находки `fleet-partial`.

Остальные изменения:
- модуль `selfcheck/vendor.py` — декларации;
- точечные правки S1: `commands.Scan.external`, `model.Graph.external`,
  параметр `texts` у `build_graph`, новые поля `NodeFacts`, `fleet_only`,
  параметр `fleet_view` у `RepoTarget`, флаг `--fleet`, отчёт.

Флот читается один раз на прогон, пробы по флоту не запускаются.

**Tech Stack:** Python ≥3.12 (stdlib + `pyyaml`), pytest, git. Новых
зависимостей нет.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md`, **rev 5.7**:
§9 плюс отсылки в §1.4, §2.3, §3.2.1, §3.2.4, §4.3, §7, §8. План — только
этап S2. Пункт `selfcheck-zone-narrow` (сужение зоны «каталог вызывающего»)
— отдельный.

## Жанр плана — что нормативно

Жанр тот же, что у плана S1.
- **Нормативны:** тесты (полный код в задачах), раздел «Интерфейсы», порядок
  задач, Global Constraints, таблица трассировки. При расхождении эскиза,
  прозы и теста прав тест. При расхождении теста и спеки права спека: тест
  чинится отдельным коммитом с объяснением.
- **Ненормативны:** эскизы. Исполнитель доводит тела по TDD.
- **Red-фаза уже прогнана** (2026-09-26, на файлах этого плана):
  - фикстуры Task 0 зелёные (3 passed);
  - `test_fleet_reader`, `test_fleet_match`, `test_fleet_assemble` падают при
    сборке на `ModuleNotFoundError: selfcheck.fleet`;
  - `test_vendor` — на `ModuleNotFoundError: selfcheck.vendor`;
  - `test_fleet_classify` — на `ImportError: fleet_only`;
  - `test_fleet_run` падает при исполнении: `SystemExit: 2` на неизвестном
    `--fleet`, а без флага — на отсутствии строки `fleet-only: 0`;
  - `ruff check --select F,B,E9,PLE` по тестам чистый.

  Первый шаг каждой задачи повторяет red-проверку своего модуля.

## Global Constraints

- Shipped-код не читает и не резолвит `_cowork_output/` (корневой CLAUDE.md).
  Корневой зонтик во флот не входит (§9.2).
- Пробы по флоту не запускаются. Репо флота не материализуются. selfcheck
  ничего не пишет в репо флота. Git по флоту — только чтение с
  `GIT_OPTIONAL_LOCKS=0`: `ls-files`, `rev-parse`, `symbolic-ref`,
  `rev-list`, `diff-index`. Сеть не трогается: никаких `fetch`.
- **Ruling (план):** признак `dirty` (§9.6 п.5, «`git status --porcelain`
  пуст») вычисляется так же, как `corpus.repo_state` в S1: `diff-index
  --quiet HEAD` плюс `ls-files --others --exclude-standard`. По смыслу это
  то же самое — изменения отслеживаемых файлов плюс неотслеживаемые
  неигнорируемые. Сам `git status` S1 не использует (Global Constraints
  S1). Цена ошибки: изменение, видимое только `status` (например, смена
  режима файла при `core.fileMode=false`), не даст `stale`.
- Флот читается один раз на прогон и кэшируется по имени репо. Флот R — это
  U − {R} (§9.2), где U = уникальные `git_dir` манифеста ∪ {репо
  манифеста}.
- Каналы флота вызываются через атрибуты модулей: `match.match_external`,
  `match.find_mentions`, `reader.stale_reasons`, а не через `from … import`
  внутри `assemble`. Тесты канарейки подменяют их на модуле.
- Всё прочее — как в S1:
  - абсолютные пути;
  - исключение пробы — это её статус;
  - коды выхода 0/2/3/4;
  - `ruff format`, `ruff check` и `pyrefly check` зелёные после каждой
    задачи.
- Текстовый канал — одно объединённое регулярное выражение по всем ключам
  на репо и один проход по каждому файлу. Не делать «узлы × файлы»: это
  ≈150 узлов × 3 ключа × 9000 файлов.

## Review Focus

1. Имена с пробелами и не-ASCII в корпусе соседа —
   `test_fleet_reader::test_texts_binary_utf16_and_clean_state`
   (`с пробелом/ю.md`).
2. Файлы соседа в UTF-16 и latin-1 — тот же тест.
3. Манифест вне git-репо (как в фикстурах S1) — нет репо манифеста и нет
   «+1» — `test_fleet_assemble::test_composition_includes_manifest_repo`
   (`fleet_names(info, None, …)`).
4. Два репо в `scope` вызывают друг друга —
   `test_fleet_run::test_other_scope_repos_are_fleet_for_each_other`.
5. Время текстового канала на реальном флоте (≈9000 файлов) — шаг приёмки
   Task 6: прогон `--fleet` на devtools укладывается в 120 с; время пишется
   в заметку приёмки.

## Таблица трассировки: требование спеки → тест

| Спека | Требование | Тест(ы) |
|---|---|---|
| §9.2 | состав флота: U − {R}; репо манифеста; другие репо `scope` | `test_fleet_assemble::test_composition_includes_manifest_repo`; `test_fleet_run::test_other_scope_repos_are_fleet_for_each_other` |
| §9.2 | чтение: `ls-files` без exclude/roles `scope`, байты, NUL → двоичный, BOM UTF-16, symlink не читается | `test_fleet_reader::test_texts_binary_utf16_and_clean_state`, `test_unreadable_symlink_out_and_submodule` |
| §9.3 | внешняя цель → ребро `fleet`: `run:`, рецепт, `working-directory`, каждое вхождение сегмента, регистр, компромисс `chrome/`, `$`-токен — не ребро, проза — не ребро | `test_fleet_match::test_match_external`, `test_fleet_edges_from_neighbour_sources` |
| §9.3 | класс `fleet-only`, «fleet-only: N» явно | `test_fleet_classify::test_fleet_only_class_and_payload`; `test_fleet_run::test_fleet_end_to_end`, `test_without_fleet_nothing_changes` |
| §9.4 | ключи узла; граница слова; регистр; модульные формы | `test_fleet_match::test_node_keys`, `test_mention_boundaries`, `test_module_key_boundaries` |
| §9.4, §2.3 D14 | упоминание у соседа → P5, dead `likely`, evidence `mentioned-in: <репо>:<файл>` | `test_fleet_classify::test_s2_matrix[D14]`; `test_fleet_run::test_fleet_end_to_end` (`attest.sh`) |
| §9.5 | канарейка — git-репо через тот же код; каждый канал и `stale` ловятся; попадание только из файла канарейки; изоляция | `test_fleet_assemble::test_canary_*`, `test_real_mention_does_not_satisfy_canary` |
| §9.6 | условия `complete` 1–5; причины `missing`, `ls-files`, `empty`, `unreadable`, `symlink-out`, `submodule`, `stale`, `fleet:count` | `test_fleet_reader::*`; `test_fleet_assemble::test_partial_causes_are_findings`, `test_count_mismatch_is_partial` |
| §9.6 | формы `stale`: нет `origin/HEAD`, отставание, опережение, не та ветка, detached, dirty (включая неотслеживаемые) | `test_fleet_reader::test_stale_forms`, `test_stale_is_a_problem_and_fetched_at` |
| §9.6 | идентичность `fleet-partial`; код выхода не меняется; P1 остаётся; судьба `resolved` | `test_fleet_assemble::test_partial_causes_are_findings`; `test_fleet_run::test_partial_fleet_keeps_p1_and_exit_code` |
| §9.6, §4.3 | ключ сопоставимости включает состав флота; поверхность `fleet_repos`, `manifest_sha1`, фраза полноты | `test_fleet_run::test_fleet_composition_enters_the_key`, `test_fleet_end_to_end`; `test_fleet_assemble::test_complete_fleet` |
| §9.7 | кандидаты; форматы A–D; грамматика; неразобранные формы | `test_vendor::test_candidates`, `test_four_formats`, `test_unparsed` |
| §9.7 | роль, двойная декларация, висячая строка, `tests/` не кандидат | `test_vendor::test_vendor_roles_members_and_double_declaration` |
| §9.7 | fail-closed: названные пути, сам кандидат, P6, `partial` → код 2 | `test_vendor::test_unparsed_candidate_fails_closed`; `test_fleet_classify::test_classify_protected_and_decl_cap`; `test_fleet_run::test_broken_declaration_is_partial_exit_2_and_p6` |
| §2.3 | D1 (`confirmed` через `--fleet`), D13–D16, P6 | `test_fleet_classify::test_s2_matrix`; `test_fleet_run::test_fleet_end_to_end` |
| без `--fleet` | поведение S1 не меняется | `test_fleet_run::test_without_fleet_nothing_changes`; весь набор S1 зелёный |
| §9.8 | приёмка на devtools | Task 6, шаги 5–6 |

## Интерфейсы (нормативно)

```python
# selfcheck/graph/commands.py — addition
@dataclass
class Scan:
    ...
    external: list[str] = field(default_factory=list)
    # a literal program path in command position that resolved to no file of
    # this repo, normalised against the known base ("../devtools/x.py").
    # Collected only when base_known; "$" tokens never land here.

# selfcheck/graph/model.py — addition
@dataclass
class Graph:
    ...
    external: list[tuple[str, EdgeKind, Location]] = field(default_factory=list)

# selfcheck/graph/build.py — change
def build_graph(files, root, role, *, repo_name, sched_dir,
                texts: Mapping[str, str] | None = None) -> Graph: ...
# texts given → no disk reads (the fleet reader is the only reader of a fleet repo)

# selfcheck/fleet/reader.py
STALE = ("no-origin-head", "not-default-branch", "detached", "behind", "ahead", "dirty")

@dataclass(frozen=True)
class RepoState:
    head: str | None
    branch: str | None          # None when detached
    default: str | None         # branch of refs/remotes/origin/HEAD
    behind: int | None
    ahead: int | None
    dirty: bool
    fetched_at: str | None      # ISO mtime of .git/FETCH_HEAD, None if absent
    stale: tuple[str, ...]      # subset of STALE, in STALE order

@dataclass
class FleetRepo:
    name: str
    path: Path
    texts: dict[str, str] = field(default_factory=dict)   # rel → decoded text
    binary: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)  # (cause, detail)
    state: RepoState | None = None

def stale_reasons(path: Path) -> tuple[RepoState, tuple[str, ...]]: ...
def read_repo(path: Path, name: str) -> FleetRepo: ...

# selfcheck/fleet/match.py
def match_external(target: str, scope_name: str, scope_files: frozenset[str]) -> list[str]: ...
def fleet_edges(repo: FleetRepo, scope_name: str,
                scope_files: frozenset[str]) -> list[tuple[str, Location]]: ...
    # Location.path = "<repo>:<rel>"
def node_keys(path: str) -> tuple[str, ...]: ...
def find_mentions(keys: Mapping[str, Sequence[str]], texts: Mapping[str, str],
                  source: str) -> dict[str, list[str]]: ...
    # anchor → ["<source>:<rel>", …] (sorted, unique)

# selfcheck/fleet/assemble.py
CANARY_NODE = ".selfcheck-canary/usage-graph/fleet_canary.py"
CANARY_DIR = "fleet-canary"

@dataclass
class FleetView:
    repos: list[FleetRepo]
    expected: tuple[str, ...]
    canary_misses: list[str]
    def status(self) -> str: ...            # "complete" | "partial"

def manifest_repo(manifest: Path) -> RepoEntry | None: ...
def fleet_names(info: ManifestInfo, mrepo: RepoEntry | None,
                scope: Sequence[str]) -> tuple[str, ...]: ...
def load_fleet(workspace: Path, names: Sequence[str], run_dir: Path, *,
               scope_name: str,
               cache: dict[str, FleetRepo] | None = None,
               paths: Mapping[str, Path] | None = None) -> FleetView: ...
def build_canary(root: Path, scope_name: str,
                 forms: Sequence[str] = ("precise", "text")) -> Path: ...
def check_canary(root: Path, scope_name: str) -> list[str]: ...  # subset of ["precise","text","stale"]
def fleet_findings(view: FleetView, scope_repo: str) -> list[Finding]: ...
def surface_repos(view: FleetView) -> list[dict[str, Any]]: ...

# selfcheck/vendor.py
class DeclarationError(ValueError): ...

@dataclass(frozen=True)
class Declaration:
    path: str
    fmt: str                 # "A" | "B" | "C" | "D"
    owner: str
    ref: str
    members: tuple[str, ...]  # repo-relative, in file order

@dataclass
class VendorResult:
    members: dict[str, list[Declaration]]
    protected: set[str]       # named paths of broken declarations + every candidate
    findings: list[Finding]
    broken: bool              # any selfcheck/vendor-pin-* finding

def is_candidate(rel: str, text: str, *, is_node: bool) -> bool: ...
def parse_declaration(rel: str, text: str) -> Declaration: ...
def vendor_roles(repo: str, corpus: Sequence[str], texts: Mapping[str, str],
                 role: Callable[[str], Role], node_paths: frozenset[str]) -> VendorResult: ...

# selfcheck/graph/classify.py — changes
@dataclass(frozen=True)
class NodeFacts:
    ...  # S1 fields unchanged, then:
    vendored: bool = False
    decl_cap: bool = False
def fleet_only(g: Graph) -> list[str]: ...
def classify(g, *, repo, surface, ages, now,
             protected: frozenset[str] = frozenset(),   # paths
             decl_cap: bool = False,
             vendored: Mapping[str, list[Declaration]] | None = None) -> list[Finding]: ...
# graph_payload: every node also carries "fleet_only": bool and
# "vendored": [{"owner","ref","declaration"}] (empty list when none)

# selfcheck/probes/base.py — RepoTarget gains
    fleet_view: FleetView | None = None     # TYPE_CHECKING import only

# selfcheck/run.py
#   --fleet (store_true)
#   acc.keys[f"fleet@{repo}"] = "ok"  when --fleet and usage-graph ok
#   (so delta._instrument_status resolves probe:<repo>#fleet unchanged)
#   key_surface for usage-graph: {"fleet", "sched_dir_given", "fleet_repos": sorted names}
#   run.surface: "fleet_repos" and "manifest_sha1" only with --fleet
```

Находки S2 (правило → severity, category, anchor, text_key):
- `selfcheck/fleet-partial` → medium, `selfcheck`, `probe:<scope>#fleet`,
  `<repo>:<cause>` или `fleet:count`;
- `selfcheck/vendor-pin-unparsed` → high, `selfcheck`, `file:<декларация>`,
  `None`;
- `selfcheck/vendor-pin-dangling` → medium, `selfcheck`, `file:<декларация>`,
  путь члена.

---

### Task 0: фикстуры флота и red-фаза

**Files:**
- Modify: `tests/selfcheck/helpers.py` — дописать в конец блок ниже;
- Create: `tests/selfcheck/test_fleet_fixtures.py`.

- [ ] **Step 1: дописать фикстуры** в `tests/selfcheck/helpers.py`:

```python
# ---- S2 fleet fixtures (plan S2, Task 0) ----------------------------------------


def synced(repo: Path, branch: str = "main") -> Path:
    """Put ``repo`` on ``branch`` with ``origin/HEAD`` → it, no network (§9.6)."""
    git(repo, "branch", "-M", branch)
    git(repo, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")
    return repo


def commit_bytes(repo: Path, files: dict[str, bytes], *, date: str = "") -> None:
    """Commit raw bytes (binary, UTF-16) — ``commit`` only writes text."""
    for rel, data in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "add", "--", *files, date=date or ago(90))
    git(repo, "commit", "-q", "-m", "bytes", date=date or ago(90))


SCOPE_S2 = {
    ".gitignore": "out/\n",
    "pyproject.toml": '[project]\nname = "d"\nversion = "0"\n[tool.ruff]\n',
    "Makefile": 'help:\n\t@echo "make go"\ngo: ; @python3 ./live.py\n',
    "live.py": 'if __name__ == "__main__":\n    pass\n',
    "orphan.py": 'if __name__ == "__main__":\n    pass\n',
    "check.py": 'if __name__ == "__main__":\n    pass\n',
    "attest.sh": "#!/bin/sh\necho attest\n",
    "scripts/review/PIN": (
        "# SOURCE: steward @ 5bfd829 (master, 2026-09-21; tail of the header\n"
        "# continues here)\n" + "a" * 64 + "  scripts/review/local.sh\n"
    ),
    "scripts/review/local.sh": "#!/bin/sh\necho kit\n",
}
NEIGHBOURS_S2 = {
    "nb": {
        ".github/workflows/c.yml": (
            "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: python3 ws/devtools/check.py\n"
        ),
    },
    "docs-nb": {"TODO.md": "- [ ] run `../devtools/attest.sh maestro 1`\n"},
}


def fleet_ws(
    tmp: Path,
    scope_files: dict[str, str] | None = None,
    neighbours: dict[str, dict[str, str]] | None = None,
) -> Path:
    """S2: workspace = synced scope ``devtools`` + synced neighbours + a synced
    umbrella repo holding the manifest ``umbrella/m.toml`` (§9.2)."""
    scope = {**SCOPE_S2, **(scope_files or {})}
    nbs = NEIGHBOURS_S2 if neighbours is None else neighbours
    synced(make_repo(tmp / "devtools", scope))
    for name, files in nbs.items():
        synced(make_repo(tmp / name, files))
    entries = "".join(
        f'[tools.{n}]\ngit_dir = "{n}"\n' for n in ["devtools", *nbs]
    )
    synced(make_repo(tmp / "umbrella", {"m.toml": entries}))
    return tmp
```

- [ ] **Step 2: тест фикстур:**

```python
"""S2 Task 0 — the fleet fixtures themselves (red phase: green before any code)."""

from __future__ import annotations

from pathlib import Path

from tests.selfcheck.helpers import commit_bytes, fleet_ws, git, make_repo, synced


def test_synced_has_origin_head_without_network(tmp_path: Path) -> None:
    repo = synced(make_repo(tmp_path / "r", {"a.md": "x\n"}))
    assert git(repo, "symbolic-ref", "refs/remotes/origin/HEAD").strip() == (
        "refs/remotes/origin/main"
    )
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "remote") == ""  # no remote configured: nothing to fetch


def test_commit_bytes_keeps_bytes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.md": "x\n"})
    commit_bytes(repo, {"b.bin": b"\x00\xff"})
    assert (repo / "b.bin").read_bytes() == b"\x00\xff"
    assert "b.bin" in git(repo, "ls-files")


def test_fleet_ws_layout(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    for name in ("devtools", "nb", "docs-nb", "umbrella"):
        assert (ws / name / ".git").is_dir()
        assert git(ws / name, "status", "--porcelain") == ""
    manifest = (ws / "umbrella" / "m.toml").read_text()
    assert manifest.count("git_dir") == 3
    assert (ws / "devtools" / "scripts" / "review" / "PIN").is_file()
```

- [ ] **Step 3:** `uv run --frozen pytest tests/selfcheck/test_fleet_fixtures.py -q`.
  Expected: 3 passed.
- [ ] **Step 4:** положить тестовые файлы Task 1–6 из этого плана и
  выполнить `uv run --frozen pytest tests/selfcheck -q -k "fleet or vendor"`.
  Expected: ошибки сборки ровно на `selfcheck.fleet`, `selfcheck.vendor` и
  `fleet_only`; `test_fleet_run` падает на `SystemExit: 2`. Файлы Task 1–6
  **не** коммитить в Task 0: каждая задача коммитит свой тест.
- [ ] **Step 5:** коммит `test(selfcheck): S2 fleet fixtures`.

### Task 1: чтение репо флота

**Files:**
- Create: `selfcheck/fleet/__init__.py` (пустой), `selfcheck/fleet/reader.py`;
- Test: `tests/selfcheck/test_fleet_reader.py`.

**Interfaces:** Produces `RepoState`, `FleetRepo`, `stale_reasons`,
`read_repo` (см. «Интерфейсы»).

- [ ] **Step 1: тест:**

```python
"""S2 Task 1 — reading one fleet repo: texts, binaries, problems, state (§9.2, §9.6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from selfcheck.fleet.reader import read_repo
from tests.selfcheck.helpers import ago, commit, commit_bytes, git, make_repo, synced


def causes(repo) -> set[str]:
    return {cause for cause, _ in repo.problems}


def test_texts_binary_utf16_and_clean_state(tmp_path: Path) -> None:
    repo = synced(
        make_repo(tmp_path / "nb", {"a.md": "run x.sh\n", "с пробелом/ю.md": "y\n"})
    )
    commit_bytes(
        repo,
        {
            "bin.dat": b"\x00\x01\x02",
            "u16.txt": "call x.sh\n".encode("utf-16"),
            "latin.txt": "caf\xe9 x.sh\n".encode("latin-1"),
        },
    )
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    got = read_repo(repo, "nb")
    assert got.name == "nb" and got.problems == []
    assert set(got.texts) == {"a.md", "с пробелом/ю.md", "u16.txt", "latin.txt"}
    assert "call x.sh" in got.texts["u16.txt"]
    assert "x.sh" in got.texts["latin.txt"]
    assert got.binary == 1
    st = got.state
    assert st is not None and st.stale == ()
    assert (st.branch, st.default, st.behind, st.ahead) == ("main", "main", 0, 0)
    assert st.dirty is False and len(st.head or "") == 40


def test_missing_and_broken_git(tmp_path: Path) -> None:
    assert causes(read_repo(tmp_path / "nope", "nope")) == {"missing"}
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /does/not/exist\n")
    assert causes(read_repo(broken, "broken")) == {"ls-files"}


def test_empty_when_nothing_textual(tmp_path: Path) -> None:
    repo = tmp_path / "e"
    repo.mkdir()
    git(tmp_path, "init", "-q", str(repo))
    commit_bytes(repo, {"only.bin": b"\x00"})
    synced(repo)
    assert "empty" in causes(read_repo(repo, "e"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_symlink_out_and_submodule(tmp_path: Path) -> None:
    repo = synced(make_repo(tmp_path / "nb", {"a.md": "x\n", "secret.md": "y\n"}))
    (repo / "inside.md").symlink_to(repo / "a.md")
    (repo / "outside.md").symlink_to("/etc/hosts")
    git(repo, "add", "inside.md", "outside.md")
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    (repo / "sub").mkdir()  # an uninitialised submodule: clean, not dirty
    git(repo, "commit", "-q", "-m", "links", date=ago(90))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "secret.md").chmod(0)
    try:
        got = read_repo(repo, "nb")
    finally:
        (repo / "secret.md").chmod(0o644)
    assert dict(got.problems) == {
        "unreadable": "secret.md",
        "symlink-out": "outside.md",
        "submodule": "sub",
    }
    assert "inside.md" not in got.texts  # a symlink is never read (§9.2)


def _stale(repo: Path) -> tuple[str, ...]:
    state = read_repo(repo, repo.name).state
    assert state is not None
    return state.stale


def test_stale_forms(tmp_path: Path) -> None:
    no_origin = make_repo(tmp_path / "a", {"a.md": "x\n"})
    assert _stale(no_origin) == ("no-origin-head",)

    ahead = synced(make_repo(tmp_path / "b", {"a.md": "x\n"}))
    commit(ahead, {"b.md": "y\n"}, date=ago(1))
    assert _stale(ahead) == ("ahead",)

    behind = synced(make_repo(tmp_path / "c", {"a.md": "x\n"}))
    commit(behind, {"b.md": "y\n"}, date=ago(1))
    git(behind, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(behind, "reset", "-q", "--hard", "HEAD~1")
    assert _stale(behind) == ("behind",)

    feature = synced(make_repo(tmp_path / "d", {"a.md": "x\n"}))
    git(feature, "switch", "-q", "-c", "feat")
    assert _stale(feature) == ("not-default-branch",)

    detached = synced(make_repo(tmp_path / "e", {"a.md": "x\n"}))
    git(detached, "checkout", "-q", "--detach")
    assert _stale(detached) == ("detached",)

    dirty = synced(make_repo(tmp_path / "f", {"a.md": "x\n"}))
    (dirty / "untracked.md").write_text("new\n")
    assert _stale(dirty) == ("dirty",)
    assert read_repo(dirty, "f").state.dirty is True  # type: ignore[union-attr]


def test_stale_is_a_problem_and_fetched_at(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "a", {"a.md": "x\n"})
    got = read_repo(repo, "a")
    assert ("stale", "no-origin-head") in got.problems
    assert got.state is not None and got.state.fetched_at is None
    synced(repo)
    (repo / ".git" / "FETCH_HEAD").write_text("")
    state = read_repo(repo, "a").state
    assert state is not None and state.fetched_at is not None
```

- [ ] **Step 2:** `uv run --frozen pytest tests/selfcheck/test_fleet_reader.py -q`.
  Expected: сборка падает на `ModuleNotFoundError: selfcheck.fleet`.
- [ ] **Step 3:** реализовать по эскизу.
- [ ] **Step 4:** тот же прогон. Expected: 5 passed (от root —
  `test_unreadable…` skipped).
- [ ] **Step 5:** коммит `feat(selfcheck): fleet repo reader (S2 §9.2, §9.6)`.

**Эскиз.**
- Путь не существует или нет `.git` → `problems=[("missing", "")]`,
  `state=None`.
- Перечисление — `git ls-files -z --stage` (режим записи) плюс
  `git ls-files -z --others --exclude-standard`. Ненулевой код любого из них
  → `("ls-files", stderr[:200])`, дальше не читать.
- Режим `160000` → `("submodule", rel)`.
- Symlink (`os.path.islink`): цель через `os.path.realpath` вне `path` →
  `("symlink-out", rel)`; внутри — пропуск.
- Файл читается как `read_bytes()`, `OSError` → `("unreadable", rel)`.
- BOM `\xff\xfe`/`\xfe\xff` (или UTF-32) → декодирование по BOM. Иначе NUL в
  первых 8192 байтах → `binary += 1`. Иначе
  `decode("utf-8", errors="replace")`.
- Ни одного текстового файла → `("empty", "")`.
- `stale_reasons(path)`:
  - `head = rev-parse HEAD`;
  - `branch = symbolic-ref -q --short HEAD` (пусто → detached);
  - `default = symbolic-ref -q refs/remotes/origin/HEAD` → имя ветки;
  - `rev-list --left-right --count HEAD...origin/<default>` → ahead/behind;
  - `dirty` — как `corpus.repo_state`;
  - `fetched_at` — mtime `.git/FETCH_HEAD`.

  Причины собираются в порядке `STALE`:
  - `no-origin-head`, если нет default;
  - иначе `detached` или `not-default-branch`;
  - `behind` / `ahead` при ненулевых счётчиках;
  - `dirty`.

  `read_repo` вызывает `stale_reasons` через глобальное имя модуля: тест
  канарейки подменяет его. Каждая причина превращается в
  `("stale", reason)`.

### Task 2: точный и текстовый каналы

**Files:**
- Modify:
  - `selfcheck/graph/commands.py` — `Scan.external`, заполнение в
    `_command_position`;
  - `selfcheck/graph/model.py` — `Graph.external`;
  - `selfcheck/graph/build.py` — `_record` переносит `scan.external`,
    `build_graph(texts=…)`;
  - `selfcheck/graph/resolver.py` — `_apply` переносит `scan.external`;
- Create: `selfcheck/fleet/match.py`;
- Test: `tests/selfcheck/test_fleet_match.py`.

**Interfaces:**
- Consumes: `read_repo`, `FleetRepo` (Task 1).
- Produces: `match_external`, `fleet_edges`, `node_keys`, `find_mentions`.

- [ ] **Step 1: тест:**

```python
"""S2 Task 2 — precise channel (§9.3) and text channel keys (§9.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.fleet.match import fleet_edges, find_mentions, match_external, node_keys
from selfcheck.fleet.reader import read_repo
from tests.selfcheck.helpers import make_repo, synced

FILES = frozenset({"x.py", "sub/y.sh", "maestro/x.py"})


@pytest.mark.parametrize(
    ("target", "scope", "expected"),
    [
        ("../devtools/x.py", "devtools", ["x.py"]),
        ("ws/devtools/x.py", "devtools", ["x.py"]),
        ("/abs/p/devtools/sub/y.sh", "devtools", ["sub/y.sh"]),
        ("../DevTools/x.py", "devtools", ["x.py"]),  # case-insensitive segment
        ("../maestro/maestro/x.py", "maestro", ["maestro/x.py", "x.py"]),  # each
        ("chrome/devtools/x.py", "devtools", ["x.py"]),  # accepted compromise
        ("x.py", "devtools", []),  # no segment
        ("../devtools/nope.py", "devtools", []),
        ("../devtoolsx/x.py", "devtools", []),  # whole segment only
    ],
)
def test_match_external(target: str, scope: str, expected: list[str]) -> None:
    assert match_external(target, scope, FILES) == expected


def test_fleet_edges_from_neighbour_sources(tmp_path: Path) -> None:
    nb = synced(
        make_repo(
            tmp_path / "nb",
            {
                ".github/workflows/c.yml": (
                    "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - run: python3 ws/devtools/check.py\n"
                    "      - working-directory: ../devtools\n"
                    "        run: ./wd.sh\n"
                    '      - run: bash "$WS/devtools/var.sh"\n'
                ),
                "Makefile": "t:\n\tsh ../devtools/tool.sh\n",
                "notes.md": "see ../devtools/prose.sh\n",
            },
        )
    )
    scope = frozenset({"check.py", "wd.sh", "tool.sh", "var.sh", "prose.sh"})
    edges = fleet_edges(read_repo(nb, "nb"), "devtools", scope)
    assert {path for path, _ in edges} == {"check.py", "wd.sh", "tool.sh"}
    where = {path: w for path, w in edges}
    assert where["check.py"].path == "nb:.github/workflows/c.yml"
    assert where["tool.sh"].path == "nb:Makefile" and where["tool.sh"].line == 2


def test_node_keys() -> None:
    assert node_keys("governance/console.py") == (
        "console.py",
        "governance/console.py",
        "governance.console",
    )
    assert node_keys("attest-vendor.sh") == ("attest-vendor.sh",)
    assert node_keys("src/pkg/__init__.py") == (
        "__init__.py",
        "src/pkg/__init__.py",
        "pkg",
    )


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("xx.sh", False),
        ("attest-x.sh", False),  # '-' on the left glues
        ("x.shell", False),
        ("run ../devtools/x.sh.", True),  # sentence dot on the right
        ("RUN X.SH", True),  # case-insensitive
        ("`x.sh`", True),
        ("x.sh-helper", True),  # '-' on the right is a boundary
    ],
)
def test_mention_boundaries(text: str, hit: bool) -> None:
    found = find_mentions({"file:x.sh": ("x.sh",)}, {"a.md": text}, "nb")
    assert (found == {"file:x.sh": ["nb:a.md"]}) is hit


def test_module_key_boundaries() -> None:
    keys = {"file:g/c.py": node_keys("g/c.py")}
    texts = {
        "t1.py": 'mock.patch("g.c.main")',
        "t2.py": "import a.g.c",
        "t3.py": "g.cx",
    }
    assert find_mentions(keys, texts, "nb") == {"file:g/c.py": ["nb:t1.py", "nb:t2.py"]}
```

- [ ] **Step 2:** прогон. Expected: `ModuleNotFoundError: selfcheck.fleet.match`.
- [ ] **Step 3:** реализовать. Затем `uv run --frozen pytest tests/selfcheck -q`
  — набор S1 обязан остаться зелёным: `external` только добавляется.
- [ ] **Step 4:** Expected: test_fleet_match — all passed; S1 без регрессий.
- [ ] **Step 5:** коммит `feat(selfcheck): fleet precise and text channels (S2 §9.3–9.4)`.

**Эскиз.**
- **`_command_position`.** В ветке «не CLI, не разрешилось»: если
  `st.base_known` и в токене нет `$`, то `scan.external.append(clean` для
  абсолютного пути, иначе `posixpath.normpath(posixpath.join(st.base, clean)))`.
  Существующие ветки `missing` и `unresolved` не трогать.
- **`match_external`.** Нормализовать путь, разбить на сегменты. Для
  **каждого** `i`, где `seg[i].lower() == scope_name.lower()`, остаток
  `"/".join(seg[i+1:])` из `scope_files` → в результат. Результат
  отсортирован и уникален.
- **`fleet_edges`.**
  - `build_graph(list(repo.texts), repo.path, role_of, repo_name=repo.name,
    sched_dir=None, texts=repo.texts)`;
  - по каждому `(target, kind, where)` из `g.external` с `kind not in
    NON_EXEC` → `match_external` → `(path, Location(f"{repo.name}:{where.path}",
    where.line))`.
- **`node_keys`.** `(basename, path, module_name(path))` без `None` и без
  повторов, порядок сохраняется.
- **`find_mentions`.** Одно выражение `(?<![A-Za-z0-9_-])(?:k1|k2|…)(?![A-Za-z0-9_])`
  с `re.IGNORECASE`, ключи отсортированы по убыванию длины. Карта
  `key.lower() → anchors`. Один `finditer` на файл.

### Task 3: вендор-декларации

**Files:**
- Create: `selfcheck/vendor.py`;
- Test: `tests/selfcheck/test_vendor.py`.

**Interfaces:** Produces `DeclarationError`, `Declaration`, `VendorResult`,
`is_candidate`, `parse_declaration`, `vendor_roles`.

- [ ] **Step 1: тест:**

```python
"""S2 Task 3 — vendored-in role from declarations, fail-closed (§9.7)."""

from __future__ import annotations

import pytest

from selfcheck.roles import role_of
from selfcheck.vendor import (
    DeclarationError,
    is_candidate,
    parse_declaration,
    vendor_roles,
)

H = "a" * 64
FORMAT_A = (
    "# SOURCE: steward @ 5bfd829 (master, 2026-09-21; the header tail\n"
    "# continues on the next comment line)\n"
    "# re-vendor: copy files from steward\n"
    f"{H}  scripts/review/local.sh\n"
    f"{H}  .github/codex/review-schema.json\n"
)
FORMAT_B = f"{H}  approval-policy.yaml  steward@6a70d15ba586b8c17b41d33705477a42cf8ebfa5\n"
FORMAT_C = (
    "upstream: git@github.com:andrei-shtanakov/discovery-toolkit.git\n"
    "commit: ee93092fdfe6195c28c7392d85b41c6b94b9fe0a\n\n"
    f"DISCOVERY-BRIEF-CONTRACT.md {H}\n"
    f"gate_check.py {H}\n"
)
FORMAT_D = (
    "# VENDORED: devtools @ 8cd6456 — contracts/review-scope/v1/prose-paths.env\n"
    "# SSOT there; pinned copy here.\n"
    "a/**\n"
)


@pytest.mark.parametrize(
    ("rel", "text", "fmt", "owner", "ref", "members"),
    [
        (
            "scripts/review/PIN",
            FORMAT_A,
            "A",
            "steward",
            "5bfd829",
            ("scripts/review/local.sh", ".github/codex/review-schema.json"),
        ),
        (
            "contracts/p/v1/PIN",
            FORMAT_B,
            "B",
            "steward",
            "6a70d15ba586b8c17b41d33705477a42cf8ebfa5",
            ("contracts/p/v1/approval-policy.yaml",),
        ),
        (
            "governance/dc/PINNED.txt",
            FORMAT_C,
            "C",
            "discovery-toolkit",
            "ee93092fdfe6195c28c7392d85b41c6b94b9fe0a",
            ("governance/dc/DISCOVERY-BRIEF-CONTRACT.md", "governance/dc/gate_check.py"),
        ),
        (
            "scripts/review/prose-paths.env",
            FORMAT_D,
            "D",
            "devtools",
            "8cd6456",
            ("scripts/review/prose-paths.env",),
        ),
    ],
)
def test_four_formats(rel, text, fmt, owner, ref, members) -> None:
    decl = parse_declaration(rel, text)
    assert (decl.path, decl.fmt, decl.owner, decl.ref, decl.members) == (
        rel,
        fmt,
        owner,
        ref,
        members,
    )


@pytest.mark.parametrize(
    ("rel", "text", "is_node", "expected"),
    [
        ("scripts/review/PIN", FORMAT_A, False, True),
        ("x/PINNED.txt", FORMAT_C, False, True),
        ("x/vendor.lock", "whatever\n", False, True),
        ("tools/vendor_manifest.py", "import os\n", True, False),  # code by name
        ("a.yml", "upstream: x\nname: y\n", False, False),  # no commit:
        ("a.yml", "upstream: x\ncommit: main\n", False, True),  # grammar decides
        ("a.py", "# SOURCE: https://example.com/snippet\n", True, False),  # no @
        ("a.sh", "#!/bin/sh\n# SOURCE: steward @ v1.2\n", True, True),
        ("a.sh", "#!/bin/sh\n# VENDORED: owner@abc\n", True, True),
        ("a.sh", "l1\nl2\nl3\nl4\nl5\n# VENDORED: x @ abcdef1 — p\n", True, False),
    ],
)
def test_candidates(rel, text, is_node, expected) -> None:
    assert is_candidate(rel, text, is_node=is_node) is expected


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("a.sh", "# VENDORED: x @ v1.2 — p\n"),  # tag instead of sha
        ("x/PINNED.txt", "upstream: u/r.git\ncommit: main\n\np " + H + "\n"),
        ("x/PIN", FORMAT_A + FORMAT_B),  # mixed formats
        ("x/PIN", "# SOURCE: steward @ 5bfd829\n"),  # no members
        ("x/PIN", f"{H}  ../escape.sh  steward@5bfd829\n"),  # '..'
        ("x/PIN", f"# SOURCE: s @ 5bfd829\n{H}  /abs/file.sh\n"),  # absolute
        ("x/PIN", "# SOURCE: s @ 5bfd829\nnot a member line\n"),
    ],
)
def test_unparsed(rel: str, text: str) -> None:
    with pytest.raises(DeclarationError):
        parse_declaration(rel, text)


def _texts() -> dict[str, str]:
    return {
        "scripts/review/PIN": FORMAT_A + f"{H}  scripts/review/prose-paths.env\n",
        "scripts/review/local.sh": "#!/bin/sh\n",
        "scripts/review/prose-paths.env": FORMAT_D,
        "scripts/review/extra.sh": "#!/bin/sh\n",
        "tests/fixtures/PIN": "garbage\n",  # role test: never a candidate
    }


def test_vendor_roles_members_and_double_declaration() -> None:
    texts = _texts()
    res = vendor_roles("devtools", sorted(texts), texts, role_of, frozenset())
    assert set(res.members) == {
        "scripts/review/local.sh",
        "scripts/review/prose-paths.env",
    }
    owners = sorted(d.owner for d in res.members["scripts/review/prose-paths.env"])
    assert owners == ["devtools", "steward"]  # listed twice, not an error
    assert "scripts/review/extra.sh" not in res.members  # ordinary analysis
    dangling = [f for f in res.findings if f.rule == "selfcheck/vendor-pin-dangling"]
    assert [(f.anchor, f.text_key, f.severity) for f in dangling] == [
        ("file:scripts/review/PIN", ".github/codex/review-schema.json", "medium")
    ]
    assert res.broken is True  # any vendor-pin-* finding
    assert not [f for f in res.findings if f.rule == "selfcheck/vendor-pin-unparsed"]


def test_unparsed_candidate_fails_closed() -> None:
    texts = {
        "kit/PIN": f"# SOURCE: s @ 5bfd829\n{H}  kit/a.sh\nbroken line kit/b.sh\n",
        "kit/a.sh": "#!/bin/sh\n",
        "kit/b.sh": "#!/bin/sh\n",
        "kit/c.sh": "#!/bin/sh\n",
        "lib.sh": "#!/bin/sh\n# VENDORED: owner@abc\n",
    }
    res = vendor_roles("r", sorted(texts), texts, role_of, frozenset({"lib.sh"}))
    unparsed = sorted(
        f.anchor for f in res.findings if f.rule == "selfcheck/vendor-pin-unparsed"
    )
    assert unparsed == ["file:kit/PIN", "file:lib.sh"]
    assert all(
        f.severity == "high" and f.text_key is None
        for f in res.findings
        if f.rule == "selfcheck/vendor-pin-unparsed"
    )
    # named paths of any line + every candidate itself are protected
    assert {"kit/a.sh", "kit/b.sh", "kit/PIN", "lib.sh"} <= res.protected
    assert "kit/c.sh" not in res.protected  # P6 covers it instead
    assert res.members == {} and res.broken is True
```

- [ ] **Step 2:** прогон. Expected: `ModuleNotFoundError: selfcheck.vendor`.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** Expected: all passed.
- [ ] **Step 5:** коммит `feat(selfcheck): vendored-in declarations, fail-closed (S2 §9.7)`.

**Эскиз.**
- **Кандидат.** Одно из двух:
  - basename (`lower`) ∈ {`pin`} или начинается с `pinned` / `vendor`, **и
    не** `is_node`;
  - или в первых 5 строках есть:
    - `# VENDORED:` (любой хвост);
    - `# SOURCE:` с `@` в строке;
    - `upstream:` при любой строке `commit:` в файле.
- **Разбор.** `HEX = [0-9a-f]{7,40}`; строка члена A — `^([0-9a-f]{64})\s+(\S+)$`;
  B — `^([0-9a-f]{64})\s+(\S+)\s+([\w.-]+)@([0-9a-f]{7,40})$`; C —
  `^(\S+)\s+([0-9a-f]{64})$`. Порядок:
  - D: шапка `^# VENDORED:\s*(\S+)\s+@\s+(HEX)\b` в первых 5 строках → член —
    сам файл;
  - A: ровно одна шапка `^# SOURCE:\s*(\S+)\s+@\s+(HEX)\b`, остальные
    строки — комментарии, пустые или члены A;
  - C: `upstream:` и `commit: HEX`, остальные — члены C;
  - B: все непустые не-`#` строки — члены B.

  Любое отклонение → `DeclarationError`. Путь с `..`-сегментом или
  абсолютный → `DeclarationError`. Пути A — от корня; B и C — от каталога
  декларации.
- **`vendor_roles`.**
  - Кандидаты — только `role(rel) is Role.SOURCE`.
  - Каждый кандидат попадает в `protected`.
  - Неразобранный кандидат → находка `vendor-pin-unparsed`; каждый токен
    `\S+` каждой строки, совпавший с путём корпуса от корня или от каталога
    декларации, → в `protected`.
  - Разобранный: для каждого члена — есть в корпусе → `members[m].append(decl)`,
    нет → `vendor-pin-dangling` (`text_key` = путь).
  - `broken = bool(findings)`.

### Task 4: классификация — D13–D16, P6, fleet-only

**Files:**
- Modify: `selfcheck/graph/classify.py`;
- Test: `tests/selfcheck/test_fleet_classify.py`.

**Interfaces:**
- Consumes: `Declaration` (Task 3).
- Produces: `NodeFacts.vendored`, `NodeFacts.decl_cap`, `fleet_only`, новые
  параметры `classify`, поля `graph_payload`.

- [ ] **Step 1: тест:**

```python
"""S2 Task 4 — matrix D13–D16, cap P6, class fleet-only (§2.3, §9.3, §9.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import (
    NodeFacts,
    Surface,
    classify,
    dead_confidence,
    fleet_only,
    graph_payload,
)
from selfcheck.graph.model import EdgeKind
from selfcheck.model import Confidence, Location
from selfcheck.roles import role_of
from tests.selfcheck.helpers import NOW, USAGE_FILES, make_repo

C, L = Confidence.CONFIRMED, Confidence.LIKELY
FULL = Surface("complete", "/sched", [])


@pytest.mark.parametrize(
    ("row", "facts", "expected", "caps"),
    [
        ("D13", NodeFacts("live", False, False, True, 90, False), None, []),
        ("D14", NodeFacts("orphan", False, False, True, 90, True), L, ["P5"]),
        (
            "D15",
            NodeFacts("orphan", False, False, True, 90, False, vendored=True),
            None,
            [],
        ),
        (
            "D16",
            NodeFacts("orphan", False, False, True, 90, False, vendored=True),
            None,
            [],
        ),
        (
            "P6",
            NodeFacts("orphan", False, False, True, 90, False, decl_cap=True),
            L,
            ["P6"],
        ),
        ("D1", NodeFacts("orphan", False, False, True, 90, False), C, []),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_s2_matrix(row, facts, expected, caps) -> None:
    assert dead_confidence(facts, FULL) == (expected, caps)


def _graph(tmp_path: Path):
    files = {**USAGE_FILES, "fleet_only.py": "x = 1\n", "both.py": "x = 1\n"}
    files["Makefile"] += "b: ; @python3 ./both.py\n"
    repo = make_repo(tmp_path / "devtools", files)
    g = build_graph(sorted(files), repo, role_of, repo_name="devtools", sched_dir=None)
    g.add("fleet_only.py", EdgeKind.FLEET, Location("nb:.github/workflows/c.yml", 1))
    g.add("both.py", EdgeKind.FLEET, Location("nb:Makefile", 2))
    return g


def test_fleet_only_class_and_payload(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    assert fleet_only(g) == ["file:fleet_only.py"]
    payload = graph_payload(g)
    node = payload["file:fleet_only.py"]
    assert node["class"] == "live" and node["fleet_only"] is True
    assert node["edges"] == [{"kind": "fleet", "from": "nb:.github/workflows/c.yml:1"}]
    assert payload["file:both.py"]["fleet_only"] is False


def test_classify_protected_and_decl_cap(tmp_path: Path) -> None:
    g = _graph(tmp_path)
    found = classify(
        g,
        repo="devtools",
        surface=FULL,
        ages=lambda p: NOW - 90 * 86400,
        now=NOW,
        protected=frozenset({"orphan.py"}),
        decl_cap=False,
    )
    assert not [f for f in found if f.anchor == "file:orphan.py"]
    capped = classify(
        _graph(tmp_path / "2"),
        repo="devtools",
        surface=FULL,
        ages=lambda p: NOW - 90 * 86400,
        now=NOW,
        decl_cap=True,
    )
    (dead,) = [f for f in capped if f.anchor == "file:orphan.py"]
    assert dead.confidence is L
    assert {"kind": "cap", "detail": "P6"} in dead.evidence
```

- [ ] **Step 2:** прогон. Expected: `ImportError: fleet_only`.
- [ ] **Step 3:** реализовать. Весь `tests/selfcheck` зелёный: матрица S1
  D1–D12 не меняется, новые поля идут с умолчаниями.
- [ ] **Step 4:** Expected: all passed.
- [ ] **Step 5:** коммит `feat(selfcheck): D13–D16, P6, fleet-only (S2 §2.3, §9.3)`.

**Эскиз.**
- **`dead_confidence`.** `vendored` → `(None, [])`, как у корня. `decl_cap`
  → потолок `P6`, добавляется после `P5`.
- **`classify`.** `vendored = path in protected or path in (vendored or {})`.
- **`fleet_only(g)`.** Файловые узлы, у которых множество исполняемых
  входящих рёбер непусто и все они `FLEET`.
- **`graph_payload`.** Добавить `fleet_only` и `vendored` (список словарей
  из `Declaration`).

### Task 5: состав флота, полнота, канарейка, находки

**Files:**
- Create: `selfcheck/fleet/assemble.py`;
- Test: `tests/selfcheck/test_fleet_assemble.py`.

**Interfaces:**
- Consumes: Task 1, Task 2; `ManifestInfo`, `RepoEntry`, `load_manifest`.
- Produces: всё из `assemble` в «Интерфейсах».

- [ ] **Step 1: тест:**

```python
"""S2 Task 5 — fleet composition, completeness, fleet-partial, canary (§9.2, §9.5, §9.6)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from selfcheck.fleet import match, reader
from selfcheck.fleet.assemble import (
    CANARY_NODE,
    FleetView,
    build_canary,
    check_canary,
    fleet_findings,
    fleet_names,
    load_fleet,
    manifest_repo,
    surface_repos,
)
from selfcheck.manifest import load_manifest
from tests.selfcheck.helpers import commit, fleet_ws, git


def _view(ws: Path, run_dir: Path) -> FleetView:
    info = load_manifest(ws / "umbrella" / "m.toml", ws)
    names = fleet_names(info, manifest_repo(ws / "umbrella" / "m.toml"), ["devtools"])
    return load_fleet(ws, names, run_dir, scope_name="devtools")


def test_composition_includes_manifest_repo(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    info = load_manifest(ws / "umbrella" / "m.toml", ws)
    repo = manifest_repo(ws / "umbrella" / "m.toml")
    assert repo is not None and repo.name == "umbrella"
    assert fleet_names(info, repo, ["devtools"]) == ("nb", "docs-nb", "umbrella")
    assert fleet_names(info, None, ["devtools", "nb"]) == ("docs-nb",)


def test_complete_fleet(tmp_path: Path) -> None:
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    assert view.canary_misses == [] and view.status() == "complete"
    assert fleet_findings(view, "devtools") == []
    rows = surface_repos(view)
    assert [r["name"] for r in rows] == ["nb", "docs-nb", "umbrella"]
    assert set(rows[0]) == {
        "name", "head", "branch", "default", "behind", "ahead",
        "files", "binary", "dirty", "fetched_at",
    }  # fmt: skip
    assert all(r["files"] > 0 for r in rows)


def test_partial_causes_are_findings(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    shutil.rmtree(ws / "docs-nb")
    commit(ws / "nb", {"late.md": "x\n"}, date="2026-09-25T00:00:00")  # ahead
    view = _view(ws, tmp_path / "run")
    assert view.status() == "partial"
    found = fleet_findings(view, "devtools")
    assert sorted(f.text_key for f in found) == ["docs-nb:missing", "nb:stale"]
    for f in found:
        assert (f.rule, f.owner_repo, f.anchor, f.severity, f.category) == (
            "selfcheck/fleet-partial",
            "devtools",
            "probe:devtools#fleet",
            "medium",
            "selfcheck",
        )


def test_count_mismatch_is_partial(tmp_path: Path) -> None:
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    view.repos = [r for r in view.repos if r.name != "nb"]  # composition bug
    assert view.status() == "partial"
    assert [f.text_key for f in fleet_findings(view, "devtools")] == ["fleet:count"]


def test_canary_is_a_git_repo_through_the_same_code(tmp_path: Path) -> None:
    root = build_canary(tmp_path / "c", "devtools")
    assert (root / ".git").exists()
    assert git(root, "log", "--format=%an").strip() == "selfcheck"
    assert check_canary(root, "devtools") == []
    assert CANARY_NODE == ".selfcheck-canary/usage-graph/fleet_canary.py"


@pytest.mark.parametrize(
    ("broken", "miss"),
    [
        ("match_external", "precise"),
        ("find_mentions", "text"),
    ],
)
def test_canary_catches_broken_channels(
    tmp_path: Path, monkeypatch, broken: str, miss: str
) -> None:
    root = build_canary(tmp_path / "c", "devtools")
    if broken == "match_external":
        monkeypatch.setattr(match, "match_external", lambda *a, **k: [])
    else:
        monkeypatch.setattr(match, "find_mentions", lambda *a, **k: {})
    assert check_canary(root, "devtools") == [miss]


def test_canary_catches_broken_stale_check(tmp_path: Path, monkeypatch) -> None:
    root = build_canary(tmp_path / "c", "devtools")
    monkeypatch.setattr(reader, "stale_reasons", lambda *a, **k: ())
    assert check_canary(root, "devtools") == ["stale"]


def test_real_mention_does_not_satisfy_canary(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path, neighbours={"nb": {"n.md": "fleet_canary.py\n"}})
    root = build_canary(tmp_path / "c", "devtools", forms=("precise",))
    assert check_canary(root, "devtools") == ["text"]
    view = _view(ws, tmp_path / "run")
    assert view.status() == "complete"  # its own canary is intact
    view.canary_misses = check_canary(root, "devtools")
    assert view.status() == "partial"


def test_canary_is_isolated_from_the_view(tmp_path: Path) -> None:
    view = _view(fleet_ws(tmp_path), tmp_path / "run")
    assert "fleet-canary" not in [r.name for r in view.repos]
    assert all("fleet_canary" not in rel for r in view.repos for rel in r.texts)
```

- [ ] **Step 2:** прогон. Expected: `ModuleNotFoundError: selfcheck.fleet.assemble`.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** Expected: all passed.
- [ ] **Step 5:** коммит `feat(selfcheck): fleet composition, completeness, canary (S2 §9.5–9.6)`.

**Эскиз.**
- **`manifest_repo`.** `git -C <manifest.parent> rev-parse --show-toplevel`;
  при ошибке — `None`, иначе `RepoEntry(basename, path, detect_languages(path))`.
- **`fleet_names`.** Уникальные `git_dir` в порядке манифеста — это
  `info.repos` и `info.missing` в порядке чтения. Если метаданных порядка не
  хватает, добавить в `ManifestInfo` кортеж `order`: это ruling
  исполнителя, в ledger. Затем `mrepo.name`, если его ещё нет; минус
  `scope`.
- **`load_fleet`.**
  - Путь репо: `paths.get(name)` (репо манифеста), иначе `workspace / name`.
  - Чтение через `reader.read_repo` с кэшем.
  - `build_canary(run_dir / CANARY_DIR, scope_name)` и
    `canary_misses = check_canary(...)`.
  - `expected = tuple(names)`.
- **`status()`.** `complete`, только если:
  - `canary_misses` пуст;
  - `sorted(r.name for r in repos) == sorted(expected)`;
  - ни у одного репо нет `problems`.
- **`build_canary`.**
  - `git init`; ветка `main`.
  - Файл `.github/workflows/c.yml` с
    `run: python3 <scope>/.selfcheck-canary/usage-graph/fleet_canary.py`,
    если `"precise" in forms`.
  - Файл `notes.md` с блоком ```` ```zsh ```` и `fleet_canary.py`, если
    `"text" in forms`.
  - Файл `README.md` с «canary», чтобы корпус не был пуст.
  - Коммит с `-c user.name=selfcheck -c user.email=selfcheck@localhost`.
- **`check_canary`.**
  - `repo = reader.read_repo(root, CANARY_DIR)`;
  - `precise`: `CANARY_NODE` ∈ путей `match.fleet_edges(repo, scope,
    frozenset({CANARY_NODE}))`;
  - `text`: `match.find_mentions({"file:"+CANARY_NODE:
    match.node_keys(CANARY_NODE)}, repo.texts, CANARY_DIR)` непусто, и
    каждое место начинается с `CANARY_DIR + ":"`;
  - `stale`: `repo.state.stale == ("no-origin-head",)`;
  - промахи — в порядке `precise, text, stale`.
- **`fleet_findings`.** По одной находке на `(repo, cause)`: пути — в
  `locations` (`Location(f"{repo}:{detail}", 1)`), если `detail` непуст,
  иначе `Location("workspace-manifest.toml", 1)`. Если счёт не сошёлся —
  отдельная находка `fleet:count`, в evidence ожидаемый и фактический
  составы.
- **`surface_repos`.** Поля из «Интерфейсов»; `files = len(texts)`.

### Task 6: `--fleet` в оркестраторе, отчёте и пробе; приёмка

**Files:**
- Modify:
  - `selfcheck/run.py`;
  - `selfcheck/probes/base.py` (`RepoTarget.fleet_view`);
  - `selfcheck/graph/probe.py`;
  - `selfcheck/report.py`;
  - `selfcheck/manifest.py` (если понадобится `order`);
  - `CLAUDE.md` (строка `selfcheck/` — `--fleet`);
  - `TODO.md` (`selfcheck-s2` → `[x]`, `selfcheck-s3` разблокирован);
- Test: `tests/selfcheck/test_fleet_run.py`.

**Interfaces:** Consumes всё из Task 1–5.

- [ ] **Step 1: тест:**

```python
"""S2 Task 6 — `--fleet` end to end: report, exit codes, key, delta (§9, §4.3)."""

from __future__ import annotations

import json
from pathlib import Path

from selfcheck.run import main
from tests.selfcheck.helpers import commit, fleet_ws, git, plist_dir


def args(ws: Path, sched: Path, *extra: str) -> list[str]:
    return [
        "--workspace", str(ws),
        "--manifest", str(ws / "umbrella" / "m.toml"),
        "--out", str(ws / "out"),
        "--config", str(ws / "none.toml"),
        "--probe", "usage-graph",
        "--sched-dir", str(sched),
        *extra,
    ]  # fmt: skip


def reports(ws: Path) -> list[dict]:
    runs = sorted(
        (ws / "out").iterdir(), key=lambda p: (p / "report.json").stat().st_mtime_ns
    )
    return [json.loads((r / "report.json").read_text()) for r in runs]


def dead(doc: dict) -> dict[str, dict]:
    return {f["anchor"]: f for f in doc["findings"] if f["category"] == "dead"}


def test_fleet_end_to_end(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    assert main(args(ws, sched, "--fleet")) == 0
    (doc,) = reports(ws)
    surface = doc["run"]["surface"]
    assert surface["fleet"] == "complete"
    assert [r["name"] for r in surface["fleet_repos"]] == ["nb", "docs-nb", "umbrella"]
    assert len(surface["manifest_sha1"]) == 40
    graph = doc["graph"]["devtools"]
    check = graph["file:check.py"]
    assert check["class"] == "live" and check["fleet_only"] is True
    assert check["edges"][0]["kind"] == "fleet"
    assert check["edges"][0]["from"].startswith("nb:.github/workflows/c.yml")
    found = dead(doc)
    assert found["file:orphan.py"]["confidence"] == "confirmed"  # D1 via --fleet
    attest = found["file:attest.sh"]
    assert attest["confidence"] == "likely"
    assert {"kind": "cap", "detail": "P5"} in attest["evidence"]
    assert {"kind": "mentioned-in", "detail": "docs-nb:TODO.md"} in attest["evidence"]
    assert "file:scripts/review/local.sh" not in found  # vendored-in (D15)
    assert graph["file:scripts/review/local.sh"]["vendored"] == [
        {"owner": "steward", "ref": "5bfd829", "declaration": "scripts/review/PIN"}
    ]
    assert not [f for f in doc["findings"] if f["rule"].startswith("selfcheck/")]
    md = (ws / "out" / doc["run"]["run_id"] / "report.md").read_text()
    assert "complete относительно манифеста" in md
    assert "не покрыто: корневой зонтик, `~/.claude`" in md
    assert "fleet-only: 1" in md and "check.py" in md
    assert "вендор-копии" in md and "steward" in md


def test_without_fleet_nothing_changes(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    assert main(args(ws, sched)) == 0
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "absent"
    assert "fleet_repos" not in doc["run"]["surface"]
    assert dead(doc)["file:orphan.py"]["confidence"] == "likely"  # P1
    assert "fleet-only: 0" in (
        ws / "out" / doc["run"]["run_id"] / "report.md"
    ).read_text()


def test_partial_fleet_keeps_p1_and_exit_code(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    (ws / "nb" / "dirty.md").write_text("x\n")
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    assert main(args(ws, sched, "--fleet")) == 0  # fleet-partial: no exit change
    (doc,) = reports(ws)
    assert doc["run"]["surface"]["fleet"] == "partial"
    assert dead(doc)["file:orphan.py"]["confidence"] == "likely"
    (partial,) = [f for f in doc["findings"] if f["rule"] == "selfcheck/fleet-partial"]
    assert partial["anchor"] == "probe:devtools#fleet"
    (ws / "nb" / "dirty.md").unlink()
    assert main(args(ws, sched, "--fleet")) == 0
    _first, last = reports(ws)
    gone = {g["anchor"]: g["status"] for g in last["delta"]["gone"]}
    assert gone["probe:devtools#fleet"] == "resolved"


def test_broken_declaration_is_partial_exit_2_and_p6(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path, scope_files={"scripts/review/PIN": "garbage\n"})
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    assert main(args(ws, sched, "--fleet")) == 2
    (doc,) = reports(ws)
    (probe,) = [p for p in doc["probes"] if p["probe"] == "usage-graph"]
    assert probe["status"] == "partial"
    rules = {f["rule"] for f in doc["findings"]}
    assert "selfcheck/vendor-pin-unparsed" in rules
    orphan = dead(doc)["file:orphan.py"]
    assert orphan["confidence"] == "likely"
    assert {"kind": "cap", "detail": "P6"} in orphan["evidence"]


def test_fleet_composition_enters_the_key(tmp_path: Path) -> None:
    from tests.selfcheck.helpers import make_repo, synced

    ws = fleet_ws(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    assert main(args(ws, sched, "--fleet")) == 0
    synced(make_repo(ws / "extra", {"e.md": "e\n"}))
    manifest = ws / "umbrella" / "m.toml"
    commit(
        ws / "umbrella",
        {"m.toml": manifest.read_text() + '[tools.extra]\ngit_dir = "extra"\n'},
        date="2026-09-25T00:00:00",
    )
    git(ws / "umbrella", "update-ref", "refs/remotes/origin/main", "HEAD")
    assert main(args(ws, sched, "--fleet")) == 0
    first, last = reports(ws)
    assert last["run"]["surface"]["fleet"] == "complete"

    def key(doc: dict) -> str:
        (p,) = [p for p in doc["probes"] if p["probe"] == "usage-graph"]
        return p["key"]

    assert key(first) != key(last)


def test_other_scope_repos_are_fleet_for_each_other(tmp_path: Path) -> None:
    ws = fleet_ws(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"])
    code = main(args(ws, sched, "--fleet", "--repo", "devtools", "--repo", "nb"))
    assert code == 0
    (doc,) = reports(ws)
    assert doc["run"]["scope"] == ["devtools", "nb"]
    rows = [r["name"] for r in doc["run"]["surface"]["fleet_repos"]]
    assert rows == ["docs-nb", "umbrella"]  # display: repos outside scope
    check = doc["graph"]["devtools"]["file:check.py"]
    assert check["class"] == "live" and check["fleet_only"] is True  # nb → devtools
    assert "file:check.py" not in dead(doc)
```

- [ ] **Step 2:** прогон. Expected: `SystemExit: 2` на `--fleet`, а
  `test_without_fleet…` падает на `fleet-only: 0`.
- [ ] **Step 3:** реализовать по эскизу. Затем полный набор:
  `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q`,
  `ruff`, `pyrefly`. Expected: всё зелёное.
- [ ] **Step 4:** коммит `feat(selfcheck): --fleet end to end (S2 §9)`.
- [ ] **Step 5: приёмка на реальных данных (§9.8).**
  - `cd .. && ./devtools/repos.sh pull`, затем проверить, что все 22 репо
    чисты и на ветке по умолчанию.
  - `time make selfcheck ARGS="--fleet --sched-dir ~/Library/LaunchAgents"`.

  Expected:
  1. `surface.fleet = complete`, канарейка сработала;
  2. в `report.json` есть ребро `fleet` из
     `steward:.github/workflows/arch-evidence-freshness.yml` к
     `file:check-arch-evidence-freshness.py`;
  3. `fleet-only: 0` напечатано;
  4. члены четырёх деклараций §9.1, являющиеся узлами, имеют
     `vendored` ≠ [] и не дают dead; `vendor-pin-*` = 0; код выхода 0;
  5. время ≤ 120 с (Review Focus 5).

  Если какое-то условие не выполнено, находка разбирается до мержа. Условие
  не ослаблять.
- [ ] **Step 6:** заметка приёмки `_cowork_output/devtools-selfcheck-s2-acceptance-<дата>.md`
  (dev-only): время, число репо и файлов, список dead `confirmed` с ручной
  проверкой каждого. Коммит правок `CLAUDE.md` и `TODO.md`.

**Эскиз.**
- **`run.py`.**
  - `--fleet` → `mrepo = manifest_repo(args.manifest)`,
    `manifest_sha1 = sha1(bytes)`.
  - Один `cache: dict[str, FleetRepo]`.
  - Для каждого R из `scope`: `view_R = load_fleet(workspace, fleet_names(info,
    mrepo, [R]), run_dir, scope_name=R, cache=cache, paths={mrepo.name:
    mrepo.path})`, затем `RepoTarget(..., fleet=view_R.status(),
    fleet_view=view_R)`.
  - `surface.fleet` — худшее значение; `fleet_repos = surface_repos` по U −
    `scope`; находки `fleet_findings(view_R, R)` добавляются к находкам до
    allowlist.
  - Ключ `usage-graph`: `key_surface` плюс `fleet_repos`, отсортированные
    имена `view_R.expected`.
  - `acc.keys[f"fleet@{R}"] = "ok"`, если `--fleet` и `usage-graph` `ok` или
    `partial`.
- **`graph/probe.py`.**
  1. `vendor_roles` по `target.corpus` с текстами из копии → `VendorResult`.
  2. Граф `scope`.
  3. Если есть `fleet_view`: `canary_misses` непуст → `raise
     FleetCanaryMissed(...)` (статус `failed`). Иначе:
     - по каждому репо `fleet_edges` → `g.add(path, EdgeKind.FLEET, where)`;
     - `find_mentions` по `node_keys` файловых узлов → `g.mention(anchor, src)`.
  4. `classify(..., protected=frozenset(vendor.protected),
     decl_cap=vendor.broken, vendored=vendor.members)` плюс
     `vendor.findings`.
  5. `ParseResult.skipped` получает пути деклараций с находками, чтобы
     статус был `partial` (§9.7 п.3).
- **`report.py`.** Раздел «## флот»:
  - фраза полноты: «complete относительно манифеста `<путь>` (sha1 `<…>`): N
    репо; не покрыто: корневой зонтик, `~/.claude`» либо `partial` с
    причинами; давность refs — по §9.6;
  - строка `fleet-only: N` и таблица (узел, источник) по каждому репо
    `scope` — печатается и без `--fleet` (тогда 0);
  - таблица «вендор-копии» (путь, владелец, ref, декларация).

## Self-review (выполнен при написании)

- **Покрытие спеки.** §9.2–9.8 и отсылки §2.3/§4.3 прослежены в таблице
  трассировки. Пробелов нет.
- **Заглушки.** Нет. Эскизы ненормативны по жанру, и все имена в них есть в
  «Интерфейсах».
- **Типы.** `FleetRepo`, `FleetView`, `Declaration` и сигнатуры в тестах
  совпадают с «Интерфейсами». Это проверено red-фазой: падения — только на
  отсутствующих модулях или именах.
- **Review Focus.** 5 пунктов, у каждого есть тест или шаг приёмки.
