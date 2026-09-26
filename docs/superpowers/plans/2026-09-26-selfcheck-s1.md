# selfcheck S1 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** этап S1 selfcheck — детерминированный конвейер статических проб над
devtools, который выдаёт отчёт (JSON + Markdown) с находками bug / quality /
dead / duplicate / deps / llm-replaceable / selfcheck и дельтой к прошлому
прогону.

**Architecture:** пакет `devtools/selfcheck/`. Ядро материализует корпус репо
в read-only копию, прогоняет по реестру пробы (внешние инструменты и
собственные анализаторы) под единым контрактом статусов и канареек,
агрегирует находки по стабильному ключу, применяет политику окружения и
allowlist, считает дельту по судьбам файлов и пишет отчёт в
`out/selfcheck/<run_id>/`.

**Tech Stack:** Python ≥3.12 (stdlib + `pyyaml`), pytest; uv-группа
`selfcheck`: ruff 0.16.9, pyrefly 1.3.1, vulture 2.16, deptry 0.25.1, radon
6.0.1, shellcheck-py 0.11.0.1, actionlint-py 1.7.12.25, zizmor 1.30.1,
semgrep 1.178.0; jscpd 4.3.0 через `npx`.

**Spec:** `docs/superpowers/specs/2026-09-25-selfcheck-design.md`, **rev 5.5**.
План — только этап S1 (§7); S2 `--fleet`, S3, S5 `--judge` — отдельные пункты
TODO; S4 — отдельная спека.

## Жанр плана — что нормативно

- **Нормативны:** тесты (полный код в задачах), раздел «Интерфейсы», порядок
  задач, Global Constraints, таблица трассировки. Тест — исполняемая форма
  требования спеки; при расхождении эскиза, прозы и теста прав тест, а при
  расхождении теста и спеки — спека (и тест чинится отдельным коммитом с
  объяснением).
- **Ненормативны:** эскизы реализации. Исполнитель доводит тела функций по
  TDD; эскиз показывает замысел и ловушки, а не готовый код.
- **Red-фаза уже прогнана** (2026-09-26, на файлах этого плана, после
  раунда 2 ревью пары): фикстуры Task 0 зелёные (8 passed), каждый из 15
  нормативных модулей падает при сборке ровно на `ModuleNotFoundError: No
  module named 'selfcheck'`; `ruff check --select F,B,E9,PLE` по тестам
  чистый. Поведение реальных инструментов, на которое опираются тесты,
  замерено (раздел «Замеры» в Task 6/7). Первый шаг каждой задачи повторяет
  red-проверку для своего модуля.

## Global Constraints

- Shipped-код не читает и не резолвит `_cowork_output/` (корневой CLAUDE.md).
- Пробы S1 не исполняют код цели: у каждой `ProbeSpec`
  `executes_target_code = False` (§1.2).
- **Все пути абсолютные до запуска пробы:** `main` резолвит `--workspace`,
  `--manifest`, `--out`, `--config`, `--sched-dir` относительно `cwd` сразу
  после разбора аргументов; `materialize` и `run_probe` отвергают
  относительные пути `ValueError` (находка M1 ревью пары).
- Пробы получают только путь к read-only копии
  `out/selfcheck/<run_id>/src/<repo>/`; кэши и вывод — в
  `out/selfcheck/<run_id>/work/<probe>/<repo>/` (§1.3).
- Git исходника — только `git ls-files`, `git log`, `git rev-parse`,
  `git diff-index` с `GIT_OPTIONAL_LOCKS=0`; `git status` не используется.
- Окружение цели — только данные: `site-packages` цели не попадает в путь
  импорта, интерпретатор цели не запускается; `DEP003` отключён (§1.5).
- Нарушение **предусловия** (относительный путь в `materialize`/`run_probe`)
  — `ValueError` наружу. **Любое другое** исключение пробы (`select`,
  проверка версии, запуск, разбор, анализатор) — статус этой пробы
  (`failed` с причиной), прогон продолжается; копия убирается в `finally`.
- Коды выхода: 0 / 2 / 3 / 4 (§4.3). `run_id` =
  `YYYYMMDDTHHMMSSZ-<6 hex>`, каталог без `exist_ok` (§1).
- Стиль: type hints, docstring у публичных функций, `ruff format` перед
  коммитом; `uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck`
  и `uv run --frozen --group selfcheck pyrefly check selfcheck` зелёные после
  каждой задачи.
- Тесты с внешними инструментами зовут `require_tool(name)`: без инструмента
  — skip, при `SELFCHECK_REQUIRE_TOOLS=1` — fail.

## Review Focus

1. Репо без Python — Python-пробы `skipped: language` —
   `test_probe_base.py::test_no_inputs_and_language_skip`.
2. Имена с пробелами и не-ASCII — `test_corpus.py::test_corpus_membership`,
   `test_read_only_copy_and_release`.
3. Отслеживаемый файл удалён в рабочей копии — `test_corpus_membership`.
4. Инструмент зависает на запуске **или на проверке версии** —
   `test_probe_base.py::test_run_timeout_fails_only_that_probe`,
   `test_version_timeout_fails_without_raising`.
5. Makefile с `\`, `target: ; cmd`, `$(VAR)`, `$(MAKE) x` —
   `test_graph_build.py::test_makefile_roots_continuations_and_make_edges`.

## Таблица трассировки: требование спеки → тест

| Спека | Требование | Тест(ы) |
|---|---|---|
| §0 п.1, §4.2 | сводная таблица проб; сбой прибора — находка `selfcheck/probe-*`; `repo-missing` | `test_probe_base::test_instrument_findings`, `test_select_error_is_a_status_not_an_exception`; `test_run::test_failed_probe_is_a_finding_and_run_continues`, `test_repo_missing_is_finding_and_exit_2` |
| §0 п.2, §3.2.1 | класс использования и рёбра **каждого** узла графа в отчёте | `test_graph_classify::test_report_graph_payload` (полнота множества узлов); `test_run::test_end_to_end_report_delta_and_provenance` |
| §0 п.3, §2.1 | стабильный `id`, формы C1–C10 | `test_identity` (C1–C10); `test_graph_classify::test_zone_reported_not_dead_and_id_stable`; `test_python_tools::test_deptry_dep002_per_package_distinct` |
| §2.1 | `text_key = null` у якорных правил; ключевой токен deptry | `test_graph_classify::test_s1_orphan_likely_roots_never_dead`; `test_dups::test_exact_group_members_carry_qualname`; `test_llm::test_candidates`; `test_deptry_dep002_per_package_distinct` |
| §2.1, §4.3 | идентичность участника дубля (repo, path, member) | `test_dups::test_exact_group_members_carry_qualname`, `test_cli_overlap_parsers_and_make_recipes`; `test_delta::test_participant_identity_includes_member` |
| §0 п.5, §1.2 | пробы не исполняют код цели | `test_registry::test_registry_invariants`; `test_python_tools::test_deptry_env_as_data` |
| §1 | `run_id` уникален, каталог не переиспользуется | `test_run::test_run_dirs_unique_under_frozen_time`, `test_run_dir_collision_retries_then_gives_up` |
| §1.2 | манифест: dedup по `git_dir`, отсутствующие, языки | `test_config_manifest::test_manifest_dedup_missing_languages`, `test_manifest_entry_without_git_dir` |
| §1.3 | read-only копия, уборка, запись в копию — видимый сбой | `test_corpus::test_read_only_copy_and_release`, `test_materialize_never_reuses_dest`; `test_probe_base::test_write_to_corpus_is_visible_failure`; каждый `test_clean_repo_ok_on_read_only_copy` |
| §1.3 | абсолютные пути (M1) | `test_corpus::test_materialize_requires_absolute_paths`; `test_probe_base::test_relative_paths_are_rejected`; `test_run::test_relative_out_from_make_style_cwd` |
| §1.3, §6.2 | исходник неизменен (включая индекс git) после **всех** проб, `out/` внутри checkout | `test_corpus::test_source_untouched_including_index`; `test_run::test_end_to_end_report_delta_and_provenance`, `test_full_registry_leaves_source_untouched` |
| §1.3 | исключение `cwd` для deptry: исключения реально применяются, чужие канарейки не обходятся | `test_python_tools::test_deptry_excludes_really_apply` |
| §1.3 | уборка копии; сбой уборки — предупреждение | `test_run::test_cleanup_failure_is_a_warning`; `test_run::test_failed_probe_is_a_finding_and_run_continues` |
| §1.4 | корпус, роли, `diagnostic-output` без рёбер | `test_corpus::test_corpus_membership`; `test_config_manifest::test_default_roles`, `test_configured_roles_win`; `test_graph_build::test_diagnostic_output_gives_nothing` |
| §1.5 | режимы окружения, ключи mapping, `DEP003 off`; хуки окружения цели не исполняются | `test_env::*`; `test_python_tools::test_deptry_env_as_data` |
| §3.1 | pyrefly: `--preset default` без конфигурации, свой пресет при `pyrefly.toml` | `test_python_tools::test_pyrefly_default_preset_catches_bad_return`, `test_pyrefly_configured_repo_keeps_its_preset` |
| §2.2, §2.3 | severity/уверенность линтеров, vulture, дублей, llm | `test_python_tools::test_ruff_reports_repo_violation`, `test_vulture_confidence_scale`; `test_other_tools::test_jscpd_clone_reported_coverage`; `test_dups::test_exact_group_members_carry_qualname`, `test_cli_overlap_parsers_and_make_recipes`; `test_llm::test_candidates` |
| §2.3 | dead: матрица D1–D12, потолки | `test_graph_classify::test_dead_matrix`, `test_d12_plist_makes_live`, `test_s1_orphan_likely_roots_never_dead` |
| §2.4 | allowlist: обязательные поля, `file:` покрывает файл, истечение | `test_config_manifest::test_bad_config_raises`, `test_file_anchor_covers_its_file`, `test_expired_entry_becomes_finding` |
| §3.1, §4.2, §6.2 | **каждая реальная проба**: чистый репо на read-only копии → ok; ошибка разбора одного файла → `partial`; правило канарейки выключено в реестре → `canary-missed`; radon cc+mi | `test_python_tools::test_clean_repo_ok_on_read_only_copy`, `test_parse_error_in_one_file_is_partial`, `test_canary_rule_disabled_in_registry_is_missed`, `test_radon_cc_and_mi`; то же в `test_other_tools` (jscpd — без матрицы разбора: он не парсер); `test_dups::test_parse_error_is_partial`; `test_llm::test_python_parse_error_is_partial`; `test_graph_classify::test_syntax_error_in_source_is_partial` |
| §3.2.1 | цель запуска ≠ файл-аргумент; `-m`; импорты; источники рёбер; юниты | `test_graph_build::*` |
| §3.2.2 | корни не dead; broken-root (make/skill/CLI); root-stale | `test_graph_build::test_broken_roots`; `test_graph_classify::test_broken_and_stale_roots`, `test_s1_orphan_likely_roots_never_dead` |
| §3.2.3 | формы резолвера (`with_name`, `.parent`, `os.path`, `$SCRIPT_DIR`, `BASH_SOURCE`, обёртки), зоны (суффикс, каталог, `getattr`, glob, `"$cmd"`), entry-points — ребро | `test_graph_resolver::*`; `test_graph_build::test_runbook_console_and_uv_project_and_entry_points` |
| §3.2.1 | локальный composite-action, `.timer` с `Unit=`, `console`-runbook, `uv run --project` | `test_graph_build::test_local_composite_action_and_timer_unit`, `test_runbook_console_and_uv_project_and_entry_points` |
| §3.2.4 | поверхность: fleet, плисты, пофайловая история | `test_graph_classify::test_report_graph_payload`; `test_run::test_end_to_end_report_delta_and_provenance` |
| §3.3 | exact/structural, декораторы и аннотации значимы, Жаккар | `test_dups::*` |
| §3.4 | точки A (список и переменная), B (SDK), C (HTTP), D (конфиг); endpoint без вызова — не точка; промпт, невидимый статически, — только инвентарь; порог кандидата | `test_llm::test_candidates`, `test_inventory_mechanisms`, `test_features_and_exclusion` |
| §4.1 | канарейка = правило + якорь; формы входа; покрытие как набор; jscpd сужает ожидаемые входы | `test_probe_base::test_canary_right_rule_wrong_anchor_is_missed`, `test_reported_coverage_missing_input_is_partial`, `test_no_inputs_and_language_skip` (канарейка не запускается при `skipped`); `test_python_tools::test_deptry_excludes_really_apply`; `test_other_tools::test_jscpd_clone_reported_coverage` |
| §4.2 | статусы и их условия | `test_probe_base::*`; `test_python_tools::test_vulture_syntax_error_on_stderr_is_partial` |
| §4.3 | судьбы, Δ1–Δ4, `changed`, `unverified` в `related[]`, ключ сопоставимости (режим окружения, конфиг репо, версия логики и `[roles]`/`[corpus]` собственных анализаторов), судьба находок прибора | `test_delta::*`; `test_probe_base::test_internal_analyzer_contract` |
| §4.3 | коды выхода 0/2/3/4 | `test_run::test_exit_codes`, `test_bad_config_and_unknown_repo_exit_4`, `test_report_write_failure_exit_4`, `test_repo_missing_is_finding_and_exit_2` |
| §7 S1 | приёмка на devtools | Task 14, шаги 6–7 |

## Интерфейсы (нормативно)

Имена, сигнатуры и поля ниже используют тесты; их нельзя переименовывать без
правки тестов.

- `selfcheck.model`: `Confidence` (`CANDIDATE`/`LIKELY`/`CONFIRMED`);
  `cap(value, limit) -> Confidence`; `Location(path: str, line: int)` (frozen,
  order); `Finding(rule, category, severity, confidence, owner_repo, anchor,
  locations, text_key=None, group=None, related=[], evidence=[],
  suggestion="", judge=None)` со свойствами `probe`, `occurrences`, `id` и
  `to_json()`; `make_text_key(text) -> str`; `finding_id(rule, owner_repo,
  anchor, text_key) -> str` (для `dup:*` без `owner_repo`);
  `aggregate(findings) -> list[Finding]` (слияние по `id`, сортировка по `id`).
- `selfcheck.anchors.python_anchor(source, path, line) -> str` — `func:` для
  строки внутри функции, иначе `file:` (и при `SyntaxError`).
- `selfcheck.roles`: `Role` (`source`, `skill-root`, `test`, `documentation`,
  `diagnostic-output`, `canary`); `glob_match(pattern, path)`;
  `role_of(path, extra=None) -> Role` (настроенные роли раньше дефолтных).
- `selfcheck.config`: `ConfigError(ValueError)`; `load_config(path) -> Config`
  (`allow`, `roles: dict[str, tuple[str, ...]]`, `corpus_exclude`, `sha1`);
  `apply_allowlist(findings, config, today) -> AllowResult(kept, suppressed,
  expired)`.
- `selfcheck.manifest.load_manifest(manifest, workspace) -> ManifestInfo(
  entries_read, repos: tuple[RepoEntry(name, path: абсолютный, languages)],
  missing: tuple[str, ...])`.
- `selfcheck.corpus`: `list_corpus(repo, exclude=()) -> list[str]`;
  `materialize(repo, files, dest, extra_files)` (ValueError на
  относительном `dest`, FileExistsError на существующем); `release(dest) ->
  str | None`; `last_commit_ts(repo, rel) -> int | None`; `repo_state(repo) ->
  {"head": str, "dirty": bool}`; `snapshot_hashes(root) -> dict[str, str]`.
- `selfcheck.env`: `EnvInfo(mode, stale=False, site_packages=None,
  python_version=None)`; `detect_env(repo)`; `package_module_map(site,
  pyproject) -> str`; `apply_env_policy(findings, env) -> (kept,
  counts_by_rule)`.
- `selfcheck.probes.base`: `Canary(relpath, content, expect_rule,
  expect_anchor)`; `RepoTarget(name, source, copy, languages, corpus, env,
  roles={}, corpus_exclude=(), sched_dir=None, fleet="absent", now=0.0)`; `ProbeCtx(target, work,
  inputs)`; `ParseResult(findings, processed_paths=None, skipped=[],
  diagnostics=[], notes: list[str]=[], extra={})`; `ProbeStatus`; `ProbeResult(probe,
  repo, status, reason="", tool_version=None, argv=[], exit_code=None,
  canary=None, coverage={}, diagnostics=[], findings=[], extra={}, rules=(),
  duration=0.0, config_hash="")` с `to_json()`; `ProbeSpec(name, languages,
  input_mode, select, canary, coverage="declared", executes_target_code=False,
  rules=(), logic_version=0, binary=None, version_args=("--version",),
  version_range=None (полуинтервал `[low, high)`), version_timeout=60,
  normal_codes=frozenset({0}), cwd="work" | "copy", argv=None, parse=None,
  analyze=None, config_suppresses=<never>, expected_files=None,
  config_files=(), timeout=900)`;
  `canary_files(specs)`; `run_probe(spec, target, work_root, *,
  runner=subprocess.run, which=shutil.which) -> ProbeResult`;
  `instrument_findings(results) -> list[Finding]`.
  Ключи `coverage`: `mode`, `input_mode`, `passed` (список входов без
  канарейки), `unprocessed`, `skipped`, `expected_files` (список), `notes`
  (`list[str]`). `ProbeResult.canary` — `"hit"`, `"missed"` или `None`
  (проба `skipped`). У собственных анализаторов `tool_version =
  "selfcheck <версия>/logic <logic_version>"`, `config_hash` — sha1 от
  `roles` и `corpus_exclude` цели. Находки прибора — якорь
  `probe:<repo>#<probe>`, правила `selfcheck/probe-failed|partial|unavailable`,
  `selfcheck/repo-missing`, `selfcheck/allow-expired`. Evidence потолков —
  `{"kind": "cap", "detail": "P1".."P5"}`.
- `selfcheck.probes.common`: `rel_path(ctx, raw)`, `source_text(ctx, rel)`,
  `copy_paths(ctx)`, `line_finding(ctx, rule, rel, line, *, category,
  severity, confidence=LIKELY, message="", key_text=None)`, `config_hash(copy,
  names)`.
- `selfcheck.probes.python_tools`: `RUFF`, `PYREFLY`, `VULTURE`, `RADON`,
  `DEPTRY`, `PYTHON_PROBES`; правила `ruff/<код>`, `pyrefly/<name>`,
  `vulture/<слова до первой кавычки через дефис>`, `radon/cc-<ранг>`,
  `radon/mi-<ранг>` (якорь `file:`), `deptry/<код>`.
- `selfcheck.probes.other_tools`: `SHELLCHECK`, `ACTIONLINT`, `ZIZMOR`,
  `JSCPD`, `OTHER_PROBES`, `shell_files(target)`, `workflow_files(target)`.
- `selfcheck.graph.model`: `NodeKind` (`file`, `make`, `skill`, `workflow`,
  `cli`, `unit`), `Node(anchor, kind, path, name, root=False,
  executable=False)`, `EdgeKind` (`make`, `ci`, `import`, `exec`, `entry`,
  `skill`, `sched`, `runbook`, `fleet`, `test`, `doc`), `NON_EXEC`, `Edge(target,
  kind, where)`, `Zone(caller, members, reason)`, `Graph(nodes, edges, zones,
  mentions: {anchor: [path]}, broken: [(root_anchor, Location, token)],
  errors, plists: list[str], root_texts)` с `incoming(anchor)`. Якоря узлов: `file:<p>`, `make:<p>#<t>`, `skill:<p>`,
  `workflow:<p>#<job>`, `cli:<name>`, `unit:<p>`.
- `selfcheck.graph.commands`: `build_index(files, pyproject_text) -> Index`;
  `scan_command(cmd, base, index, *, shell_vars) -> Scan(targets, mentions,
  missing, unresolved)`.
- `selfcheck.graph.build`: `build_graph(files, root, role, *, repo_name,
  sched_dir) -> Graph`; `make_recipes(text) -> dict[str, list[tuple[int,
  str]]]`.
- `selfcheck.graph.classify`: `Surface(fleet, sched_dir, plists)`,
  `NodeFacts(klass, root, in_zone, history, age_days, mentioned)`,
  `dead_confidence(facts, surface) -> (Confidence | None, caps)`,
  `klass_of(graph, anchor)`, `classify(graph, *, repo, surface, ages, now)`.
- `selfcheck.graph.probe.USAGE_GRAPH`; `extra["graph"]` — **все** узлы графа
  репо: `{anchor: {"class": str, "root": bool, "edges": [{"kind", "from":
  "<path>:<line>"}]}}`; `extra["surface"]` = `{"fleet", "sched_dir",
  "plists", "history": {path: bool}}` (история — по файловым узлам).
- `selfcheck.dups`: `function_hashes(source, path) -> list[FuncHash(path,
  qualname, line, exact, structural, literals)]`, `dup_findings(hashes,
  repo)`, `overlaps(a, b) -> bool`, `AST_DUP`, `CLI_OVERLAP`; у участников в
  `related` поле `member`.
- `selfcheck.llm`: `python_features(source, line) -> (features, excluded)`,
  `LLM_SITES`; `extra["inventory"]` — строки `{path, line, mechanism (A–D),
  rule, candidate, features}`.
- `selfcheck.delta`: `RunSnapshot(run_id, scope, materialized, probe_keys,
  corpus, sources, findings)` с `to_json`/`from_json`; ключи `probe_keys` —
  `"<probe>@<repo>"`; `comparability_key(result, *, env_mode, surface,
  run_dir) -> str | None`; `Fate`; `fate(cur, base, repo, path, probe)`;
  `compute_delta(base, cur) -> (statuses: {id: status}, gone: [{id, status,
  anchor}])` — прежних участников сохранившегося дубля с судьбой вне
  {checked, deleted} дописывает в `cur.findings[id]["related"]` с
  `"unverified": True`.
- `selfcheck.registry.REGISTRY`; `selfcheck.report.new_run_dir(out_root, now,
  token=...) -> (run_id, path)`, `find_baseline(out_root, current)`,
  `write_report(run_dir, doc)`; `selfcheck.run.exit_code(results)`,
  `selfcheck.run.main(argv=None, registry=REGISTRY) -> int` — печатает путь
  `report.md`; `run.write_report` и `run.release` — имена модуля `run`
  (тесты их подменяют). CLI: `--workspace`, `--manifest`, `--repo`
  (повторяемый; по умолчанию `devtools`), `--sched-dir`, `--path`
  (повторяемый glob корпуса), `--probe` (повторяемый), `--out` (по умолчанию
  `out/selfcheck`), `--config` (по умолчанию `selfcheck.toml`).
- Документ отчёта: `run` (`run_id`, `host`, `scope`, `repos[r].head/dirty`,
  `manifest`, `surface` с `plists`, `env`, `config_sha1`, `warnings`),
  `probes[]` (`ProbeResult.to_json()` + `key`, `rules`), `findings`,
  `suppressed`, `suppressed_no_env`, `graph[repo][anchor]`, `delta`
  (`statuses`, `gone`), `inventory.llm`, `snapshot`.

---

### Task 0: фикстуры и red-фаза

**Files:** Create `tests/selfcheck/__init__.py` (пустой),
`tests/selfcheck/helpers.py`, `tests/selfcheck/test_fixtures.py`.

`tests/selfcheck/helpers.py`:

```python
"""Fixture builders for selfcheck tests.

Deliberately free of ``selfcheck`` imports: ``test_fixtures.py`` checks these
builders before any implementation exists (red phase, plan Task 0).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REQUIRE_TOOLS = os.environ.get("SELFCHECK_REQUIRE_TOOLS") == "1"
NOW_DT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
NOW = NOW_DT.timestamp()


def require_tool(name: str) -> None:
    """Skip when ``name`` is absent; fail instead when tools are required."""
    if shutil.which(name) is not None:
        return
    if REQUIRE_TOOLS:
        pytest.fail(f"{name} missing while SELFCHECK_REQUIRE_TOOLS=1")
    pytest.skip(f"{name} not installed (uv run --group selfcheck)")


def ago(days: int) -> str:
    """ISO date ``days`` before the fixed test clock NOW."""
    return (NOW_DT - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    if date is not None:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                          capture_output=True, text=True)
    return proc.stdout


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if text.startswith("#!"):
            path.chmod(0o755)


def commit(repo: Path, files: dict[str, str], *, date: str) -> None:
    """Write and commit exactly ``files`` (never ``git add -A``)."""
    write(repo, files)
    git(repo, "add", "--", *files, date=date)
    git(repo, "commit", "-q", "-m", "fixture", date=date)


def make_repo(root: Path, files: dict[str, str], *, date: str = "") -> Path:
    """git repo at ``root`` with one commit of ``files`` (default: 90 days old)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    commit(root, files, date=date or ago(90))
    return root


def tracked(repo: Path) -> set[str]:
    """Tracked paths, NUL-separated (spaces and non-ASCII names intact)."""
    return {p for p in git(repo, "ls-files", "-z").split("\0") if p}


# ---- named fixtures ------------------------------------------------------------

def corpus_repo(tmp: Path) -> Path:
    """Task 3: every corpus membership case in one repo."""
    root = make_repo(tmp / "r", {
        "a.py": "print(1)\n",
        "with space.sh": "#!/bin/sh\necho hi\n",
        "юникод.py": "x = 1\n",
        "gone.py": "x = 2\n",
        ".gitignore": ".venv/\nout/\n",
        "vendor/lib.py": "y = 1\n",
    })
    (root / "gone.py").unlink()
    (root / "untracked.py").write_text("z = 1\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "x.py").write_text("")
    (root / "link.py").symlink_to(root / "a.py")
    return root


def fake_venv(repo: Path, *, version: str = "3.12.1", evil_marker: Path | None = None
              ) -> Path:
    """A .venv with PyYAML metadata only; optional startup hooks writing a marker."""
    site = repo / ".venv" / "lib" / f"python{version.rsplit('.', 1)[0]}" / "site-packages"
    dist = site / "PyYAML-6.0.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: PyYAML\n\nbody\n")
    (dist / "top_level.txt").write_text("_yaml\nyaml\n")
    rec = site / "tomli-2.0.dist-info"
    rec.mkdir()
    (rec / "METADATA").write_text("Name: tomli\n")
    (rec / "RECORD").write_text("tomli/__init__.py,,\ntomli-2.0.dist-info/METADATA,,\n")
    if evil_marker is not None:
        (site / "sitecustomize.py").write_text(f"open({str(evil_marker)!r}, 'w')\n")
        (site / "evil.pth").write_text(f"import os; open({str(evil_marker)!r}, 'w')\n")
    (repo / ".venv" / "pyvenv.cfg").write_text(f"home = /x\nversion_info = {version}\n")
    return site


def fake_tool(folder: Path, *, version: str = "1.2.3", code: int = 1,
              emit_repo: bool = True, emit_canary: bool = True,
              canary_line: int = 1, stdout: str | None = None, sleep: float = 0.0,
              version_sleep: float = 0.0, write_copy: bool = False,
              unprocessed: str | None = None) -> Path:
    """A scriptable stand-in for an external linter (Task 5).

    Emits one JSON item per input; canary inputs get code ``CAN`` at
    ``canary_line``; ``processed`` lists every input except those ending with
    ``unprocessed`` (``"*"`` drops all) — independent of argument order.
    """
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "fake-tool"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, pathlib, sys, time
        args = sys.argv[1:]
        if args == ["--version"]:
            time.sleep({version_sleep})
            print("fake {version}")
            sys.exit(0)
        time.sleep({sleep})
        if {write_copy!r}:
            try:
                pathlib.Path(args[0]).parent.joinpath("new.py").write_text("")
            except PermissionError as exc:
                print(f"Permission denied: {{exc}}", file=sys.stderr)
                sys.exit(2)
        out = []
        for a in args:
            can = ".selfcheck-canary" in a
            if (can and {emit_canary!r}) or (not can and {emit_repo!r}):
                out.append({{"path": a, "line": {canary_line} if can else 1,
                            "code": "CAN" if can else "X"}})
        drop = {unprocessed!r}
        processed = [a for a in args if not (drop == "*" or (drop and a.endswith(drop)))]
        text = {stdout!r}
        print(text if text is not None else json.dumps(
            {{"items": out, "processed": processed}}))
        sys.exit({code})
        """))
    script.chmod(0o755)
    return script


def require_npx_package(spec: str) -> None:
    """Skip/fail unless ``npx`` can actually run ``spec`` (network, cache)."""
    require_tool("npx")
    try:
        ok = subprocess.run(["npx", "--yes", spec, "--version"], capture_output=True,
                            timeout=180).returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    if not ok:
        if REQUIRE_TOOLS:
            pytest.fail(f"npx cannot run {spec} while SELFCHECK_REQUIRE_TOOLS=1")
        pytest.skip(f"npx cannot run {spec}")


def plist_dir(tmp: Path, args: list[str], name: str = "dev.atp.x.plist") -> Path:
    folder = tmp / "agents"
    folder.mkdir(exist_ok=True)
    with (folder / name).open("wb") as handle:
        plistlib.dump({"ProgramArguments": args}, handle)
    return folder


USAGE_FILES = {
    "Makefile": 'help:\n\t@echo "make go"\ngo: ; @python3 ./live.py\n',
    "live.py": 'if __name__ == "__main__":\n    pass\n',
    "orphan.py": 'if __name__ == "__main__":\n    pass\n',
    "skills/s/SKILL.md": "no calls\n",
}


def workspace(tmp: Path, files: dict[str, str] | None = None, *,
              date: str = "") -> Path:
    """Task 14: a workspace with one-repo manifest and a devtools repo."""
    make_repo(tmp / "devtools", {
        ".gitignore": "out/\n",
        "pyproject.toml": '[project]\nname = "d"\nversion = "0"\n',
        **USAGE_FILES, **(files or {})}, date=date)
    (tmp / "m.toml").write_text('[tools.devtools]\ngit_dir = "devtools"\n')
    return tmp


def mi_rank_c_source() -> str:
    """A function radon 6.0.1 ranks MI 'C' (checked: mi 0.0)."""
    lines = ["def tangled(a, b, c, d):", "    total = 0"]
    for i in range(45):
        lines.append(f"    if a * {i} + b > c - {i} and d != {i} or a % {i + 1} == b // {i + 2}:")
        body = (f"        total += (a ** 2 + b * {i}) / (c + {i + 1}) - d * {i}"
                f" + (a - b) * (c + d) % {i + 3}")
        lines += [body] * 6
        lines.append(f"    elif b << 1 > {i} ^ c:")
        lines.append(f"        total -= (a | b) & (c ^ d) + {i}")
    lines.append("    return total")
    return "\n".join(lines) + "\n"
```

`tests/selfcheck/test_fixtures.py`:

```python
"""Task 0: fixture builders are correct before any implementation exists."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

from tests.selfcheck.helpers import (
    NOW,
    ago,
    commit,
    corpus_repo,
    fake_tool,
    fake_venv,
    git,
    make_repo,
    plist_dir,
    tracked,
    workspace,
)


def test_commit_adds_only_listed_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": ""})
    (repo / "untracked.py").write_text("")
    commit(repo, {"b.py": ""}, date=ago(10))
    assert tracked(repo) == {"a.py", "b.py"}


def test_commit_dates_follow_fixed_clock(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", {"a.py": ""}, date=ago(200))
    ts = int(git(repo, "log", "-1", "--format=%ct").strip())
    assert 199 < (NOW - ts) / 86400 < 201


def test_corpus_repo_shape(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    assert "gone.py" in tracked(repo) and not (repo / "gone.py").exists()
    assert "untracked.py" not in tracked(repo) and (repo / "untracked.py").exists()
    assert (repo / "link.py").is_symlink()
    assert "with space.sh" in tracked(repo) and "юникод.py" in tracked(repo)
    assert os.access(repo / "with space.sh", os.X_OK)
    others = git(repo, "ls-files", "-z", "--others", "--exclude-standard").split("\0")
    assert ".venv/x.py" not in others


def test_fake_venv_layout(tmp_path: Path) -> None:
    marker = tmp_path / "EXECUTED"
    site = fake_venv(tmp_path, evil_marker=marker)
    assert site.name == "site-packages" and site.parent.name == "python3.12"
    assert (site / "PyYAML-6.0.3.dist-info" / "top_level.txt").read_text().split() == [
        "_yaml", "yaml"]
    assert (site / "sitecustomize.py").exists() and (site / "evil.pth").exists()
    assert not marker.exists()


def run_tool(tool: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(tool), *args], capture_output=True, text=True,
                          timeout=20)


def test_fake_tool_contract(tmp_path: Path) -> None:
    tool = fake_tool(tmp_path / "bin", unprocessed=".py")
    assert run_tool(tool, "--version").stdout.strip() == "fake 1.2.3"
    proc = run_tool(tool, "/x/a.py", "/x/.selfcheck-canary/fake/c.py")
    data = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert [i["code"] for i in data["items"]] == ["X", "CAN"]
    assert data["processed"] == []
    whole = json.loads(run_tool(fake_tool(tmp_path / "b2", unprocessed="b.py"),
                                "/x/.selfcheck-canary/c.py", "/x/b.py", "/x/a.py").stdout)
    assert whole["processed"] == ["/x/.selfcheck-canary/c.py", "/x/a.py"]


def test_fake_tool_write_to_read_only_dir(tmp_path: Path) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    (folder / "a.py").write_text("")
    folder.chmod(0o555)
    try:
        proc = run_tool(fake_tool(tmp_path / "bin", write_copy=True),
                        str(folder / "a.py"))
    finally:
        folder.chmod(0o755)
    assert proc.returncode == 2 and "Permission denied" in proc.stderr


def test_plist_dir(tmp_path: Path) -> None:
    folder = plist_dir(tmp_path, ["/w/repo/job.py"])
    with (folder / "dev.atp.x.plist").open("rb") as handle:
        assert plistlib.load(handle)["ProgramArguments"] == ["/w/repo/job.py"]


def test_workspace_layout(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    repo = ws / "devtools"
    assert {"Makefile", "live.py", "orphan.py", "pyproject.toml",
            "skills/s/SKILL.md", ".gitignore"} <= tracked(repo)
    assert (ws / "m.toml").read_text().startswith("[tools.devtools]")
    ts = int(git(repo, "log", "-1", "--format=%ct").strip())
    assert (NOW - ts) / 86400 > 60
```

- [ ] **Step 1:** `uv run --frozen pytest tests/selfcheck/test_fixtures.py -q` → 8 passed.
- [ ] **Step 2:** коммит `test(selfcheck): фикстуры и их самопроверка`.

Каждая следующая задача начинается одинаково: положить свой тестовый файл,
запустить его и убедиться, что падение — `ModuleNotFoundError` /
`ImportError` на ещё не существующем модуле или имени selfcheck, а не
ошибка в самом тесте.

---

### Task 1: модель находки, якоря, группа инструментов

**Files:** `pyproject.toml`, `uv.lock` (группа `selfcheck`); `selfcheck/__init__.py`,
`selfcheck/model.py`, `selfcheck/anchors.py`; тест `tests/selfcheck/test_identity.py`.

- [ ] **Step 1:** группа инструментов:

```bash
uv add --group selfcheck ruff==0.16.9 pyrefly==1.3.1 vulture==2.16 \
  deptry==0.25.1 radon==6.0.1 shellcheck-py==0.11.0.1 \
  actionlint-py==1.7.12.25 zizmor==1.30.1 semgrep==1.178.0
```

- [ ] **Step 2:** тест (red):

`tests/selfcheck/test_identity.py`:

```python
"""Task 1 — finding identity, spec §2.1 forms C1–C10."""

from __future__ import annotations

import random

from selfcheck.anchors import python_anchor
from selfcheck.model import Confidence, Finding, Location, aggregate, cap, finding_id, make_text_key

SRC = """def alpha():
    x = eval("1")
    y = eval("1")
    return x + y


def beta():
    return eval("2")
"""


def raw(rule: str, source: str, line: int, path: str = "m.py") -> Finding:
    text = source.splitlines()[line - 1]
    return Finding(rule=rule, category="bug", severity="medium",
                   confidence=Confidence.LIKELY, owner_repo="devtools",
                   anchor=python_anchor(source, path, line),
                   locations=[Location(path, line)], text_key=make_text_key(text))


def ids(findings: list[Finding]) -> set[str]:
    return {f.id for f in aggregate(findings)}


def test_c1_insert_above_keeps_id() -> None:
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", "\n\n" + SRC, 10).id


def test_c2_c3_identical_lines_aggregate() -> None:
    same = SRC.replace('    y = eval("1")', '    x = eval("1")')
    two = aggregate([raw("ruff/S307", same, 2), raw("ruff/S307", same, 3)])
    one = aggregate([raw("ruff/S307", SRC.replace('    y = eval("1")\n', ""), 2)])
    assert len(two) == 1 and two[0].occurrences == 2
    assert two[0].id == one[0].id and one[0].occurrences == 1


def test_c4_changed_text_changes_id() -> None:
    changed = SRC.replace('return eval("2")', 'return eval("3")')
    assert raw("ruff/S307", SRC, 8).id != raw("ruff/S307", changed, 8).id


def test_c5_c6_new_anchor_new_id() -> None:
    renamed = SRC.replace("def beta", "def gamma")
    assert raw("ruff/S307", SRC, 8).anchor == "func:m.py::beta"
    assert raw("ruff/S307", renamed, 8).id != raw("ruff/S307", SRC, 8).id


def test_c7_two_rules_two_findings() -> None:
    assert len(ids([raw("ruff/S307", SRC, 8), raw("pyrefly/x", SRC, 8)])) == 2


def test_c8_probe_order_irrelevant() -> None:
    items = [raw("ruff/S307", SRC, n) for n in (2, 3, 8)]
    shuffled = items[:]
    random.Random(1).shuffle(shuffled)
    assert ids(items) == ids(shuffled)


def test_c9_whitespace_inside_line_ignored() -> None:
    spaced = SRC.replace('return eval("2")', 'return   eval( "2")'.replace("( ", "("))
    assert raw("ruff/S307", SRC, 8).id == raw("ruff/S307", spaced, 8).id


def test_c10_dup_id_excludes_owner() -> None:
    assert (finding_id("ast-dup/exact", "devtools", "dup:exact:abc", None)
            == finding_id("ast-dup/exact", "maestro", "dup:exact:abc", None))


def test_module_level_and_broken_source_get_file_anchor() -> None:
    assert python_anchor("x = 1\n", "m.py", 1) == "file:m.py"
    assert python_anchor("def f(:\n", "m.py", 1) == "file:m.py"


def test_cap_takes_lower() -> None:
    assert cap(Confidence.CONFIRMED, Confidence.LIKELY) is Confidence.LIKELY
    assert cap(Confidence.CANDIDATE, Confidence.LIKELY) is Confidence.CANDIDATE
```

**Эскиз.** `finding_id` — sha1 от `rule|owner_repo|anchor|text_key` (для
`dup:*` — `rule|anchor`), первые 8 hex с префиксом `sc-`. `make_text_key` —
sha1 строки после `" ".join(text.split())`. `aggregate` сливает по `id`
множества `locations`, `evidence`, `related`. `python_anchor` — самая
вложенная функция (не класс), чьи `lineno..end_lineno` содержат строку.

- [ ] **Step 3:** реализовать, зелёный прогон, линт, типы, коммит
  `feat(selfcheck): модель находки и стабильный id (§2.1)`.

---

### Task 2: роли, конфиг и allowlist, манифест

**Files:** `selfcheck/roles.py`, `selfcheck/config.py`, `selfcheck/manifest.py`;
тест `tests/selfcheck/test_config_manifest.py`.

`tests/selfcheck/test_config_manifest.py`:

```python
"""Task 2 — roles (§1.4), selfcheck.toml and allowlist (§2.4), manifest (§1.2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from selfcheck.config import ConfigError, apply_allowlist, load_config
from selfcheck.manifest import load_manifest
from selfcheck.model import Confidence, Finding, Location
from selfcheck.roles import Role, glob_match, role_of


@pytest.mark.parametrize(("path", "role"), [
    (".selfcheck-canary/ruff/canary.py", Role.CANARY),
    ("selfcheck_canary/__init__.py", Role.CANARY),
    ("reports/2026-07-10-x.md", Role.DIAGNOSTIC_OUTPUT),
    ("skills/fleet-check/SKILL.md", Role.SKILL_ROOT),
    (".claude/skills/kb/SKILL.md", Role.SKILL_ROOT),
    ("authored/skills/kb-search/SKILL.md", Role.SKILL_ROOT),
    (".claude/commands/do.md", Role.SKILL_ROOT),
    ("tests/test_x.py", Role.TEST),
    ("governance/test_helper.py", Role.TEST),
    ("README.md", Role.DOCUMENTATION),
    ("docs/runbook.txt", Role.DOCUMENTATION),
    ("issue_worker.py", Role.SOURCE),
    ("skills/fleet-check/extra/SKILL.md", Role.DOCUMENTATION),
])
def test_default_roles(path: str, role: Role) -> None:
    assert role_of(path) is role


def test_glob_semantics() -> None:
    assert glob_match("skills/*/SKILL.md", "skills/a/SKILL.md")
    assert not glob_match("skills/*/SKILL.md", "skills/a/b/SKILL.md")
    assert glob_match("**/*.md", "a/b/c.md") and glob_match("**/*.md", "c.md")


def test_configured_roles_win() -> None:
    assert role_of("notes/x.md", {"diagnostic-output": ["notes/**"]}) is Role.DIAGNOSTIC_OUTPUT


def cfg_file(tmp: Path, text: str) -> Path:
    path = tmp / "selfcheck.toml"
    path.write_text(text)
    return path


def test_missing_config_is_empty(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.allow == () and cfg.corpus_exclude == () and cfg.roles == {}


@pytest.mark.parametrize("body", [
    '[[allow]]\nanchor = "file:x.py"\nuntil = 2027-01-01\n',
    '[[allow]]\nanchor = "file:x.py"\nreason = "r"\n',
    '[[allow]]\nreason = "r"\nuntil = 2027-01-01\n',
    '[[allow]]\nanchor = "file:x.py"\nreason = "r"\nuntil = "2027-01-01"\n',
    '[roles]\nbogus = ["x"]\n',
    "not toml ===",
])
def test_bad_config_raises(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(cfg_file(tmp_path, body))


def finding(anchor: str) -> Finding:
    return Finding(rule="ruff/X", category="bug", severity="low",
                   confidence=Confidence.LIKELY, owner_repo="devtools",
                   anchor=anchor, locations=[Location("x.py", 1)])


def test_file_anchor_covers_its_file(tmp_path: Path) -> None:
    cfg = load_config(cfg_file(tmp_path, '[[allow]]\nanchor = "file:issue_console.py"\n'
                               'reason = "owner"\nuntil = 2027-01-01\n'))
    items = [finding("func:issue_console.py::main"), finding("file:issue_console.py"),
             finding("llm:issue_console.py::run"), finding("func:other.py::f"),
             finding("func:issue_console.pyx::f")]
    res = apply_allowlist(items, cfg, date(2026, 9, 26))
    assert [f.anchor for f in res.kept] == ["func:other.py::f", "func:issue_console.pyx::f"]
    assert len(res.suppressed) == 3 and res.expired == []


def test_expired_entry_becomes_finding(tmp_path: Path) -> None:
    cfg = load_config(cfg_file(tmp_path, '[[allow]]\nanchor = "file:a.py"\n'
                               'reason = "r"\nuntil = 2026-01-01\n'))
    res = apply_allowlist([finding("file:a.py")], cfg, date(2026, 9, 26))
    assert len(res.kept) == 1
    assert [f.rule for f in res.expired] == ["selfcheck/allow-expired"]


def test_manifest_dedup_missing_languages(tmp_path: Path) -> None:
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "a" / "pyproject.toml").write_text("")
    (tmp_path / "a" / "Cargo.toml").write_text("")
    manifest = tmp_path / "m.toml"
    manifest.write_text('[cores.a]\ngit_dir = "a"\n[cores.a-sdk]\ngit_dir = "a"\n'
                        'member = true\n[apps.b]\ngit_dir = "b"\n[tools.c]\ngit_dir = "a"\n')
    info = load_manifest(manifest, tmp_path)
    assert info.entries_read == 4
    assert [r.name for r in info.repos] == ["a"]
    assert info.repos[0].path.is_absolute()
    assert info.repos[0].languages == frozenset({"python", "rust"})
    assert info.missing == ("b",)


def test_manifest_entry_without_git_dir(tmp_path: Path) -> None:
    manifest = tmp_path / "m.toml"
    manifest.write_text("[apps.x]\nrepo_url = 'u'\n")
    with pytest.raises(ConfigError):
        load_manifest(manifest, tmp_path)
```

**Эскиз.** `glob_match`: `**/` → `(?:.*/)?`, `**` → `.*`, `*` → `[^/]*`.
Порядок ролей: canary, diagnostic-output, skill-root, test, documentation,
source. `AllowEntry.matches`: `id` — равенство; `anchor = file:P` покрывает
`file:P`, `func:P::…`, `llm:P::…` (ровно `P`, не префикс имени). `until` —
TOML-дата, иначе `ConfigError`. Манифест: секции `cores`, `apps`, `tools`,
порядок сохраняется, путь репо — `(workspace / git_dir).resolve()`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): роли, selfcheck.toml, манифест (§1.2, §1.4, §2.4)`.

---

### Task 3: корпус, read-only копия, чтение git

**Files:** `selfcheck/corpus.py`; тест `tests/selfcheck/test_corpus.py`.

`tests/selfcheck/test_corpus.py`:

```python
"""Task 3 — corpus, read-only copy, cleanup, git reads (§1.3–1.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from selfcheck.corpus import (
    last_commit_ts,
    list_corpus,
    materialize,
    release,
    repo_state,
    snapshot_hashes,
)
from tests.selfcheck.helpers import ago, commit, corpus_repo, make_repo


def test_corpus_membership(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    assert list_corpus(repo, exclude=["vendor/**"]) == sorted(
        [".gitignore", "a.py", "untracked.py", "with space.sh", "юникод.py"])


def test_read_only_copy_and_release(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    dest = tmp_path / "run" / "src" / "r"
    materialize(repo, list_corpus(repo), dest, {".selfcheck-canary/x/c.py": "import os\n"})
    assert (dest / "with space.sh").read_text().startswith("#!/bin/sh")
    assert os.access(dest / "with space.sh", os.X_OK)
    assert (dest / ".selfcheck-canary/x/c.py").read_text() == "import os\n"
    with pytest.raises(PermissionError):
        (dest / "a.py").write_text("tampered")
    with pytest.raises(PermissionError):
        (dest / "new.py").write_text("")
    assert release(dest) is None and not dest.exists()


def test_materialize_never_reuses_dest(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    (tmp_path / "d").mkdir()
    with pytest.raises(FileExistsError):
        materialize(repo, list_corpus(repo), tmp_path / "d", {})


def test_materialize_requires_absolute_paths(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    with pytest.raises(ValueError):
        materialize(repo, list_corpus(repo), Path("relative/dest"), {})


def test_source_untouched_including_index(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    before = snapshot_hashes(repo)
    index_mtime = (repo / ".git" / "index").stat().st_mtime_ns
    materialize(repo, list_corpus(repo), tmp_path / "copy", {})
    last_commit_ts(repo, "a.py")
    repo_state(repo)
    release(tmp_path / "copy")
    assert snapshot_hashes(repo) == before
    assert (repo / ".git" / "index").stat().st_mtime_ns == index_mtime


def test_last_commit_ts(tmp_path: Path) -> None:
    repo = corpus_repo(tmp_path)
    commit(repo, {"b.py": "b = 1\n"}, date=ago(5))
    a, b = last_commit_ts(repo, "a.py"), last_commit_ts(repo, "b.py")
    assert a is not None and b is not None and a < b
    assert last_commit_ts(repo, "untracked.py") is None


def test_repo_state(tmp_path: Path) -> None:
    clean = make_repo(tmp_path / "c", {"a.py": ""})
    state = repo_state(clean)
    assert len(state["head"]) == 40 and state["dirty"] is False
    (clean / "new.py").write_text("")
    assert repo_state(clean)["dirty"] is True
```

**Эскиз.** `list_corpus`: `git ls-files -z --cached --others --exclude-standard`,
минус `exclude`, symlink'и и отсутствующие на диске. `materialize`: проверить
`is_absolute()`, `dest.mkdir()` без `exist_ok`, `shutil.copy2`, затем снять
`w` со всего дерева. `release`: вернуть `u+w`, `rmtree`, ошибку — строкой.
`repo_state`: `git rev-parse HEAD`; dirty = `git diff-index --quiet HEAD --`
≠ 0 или есть `ls-files --others --exclude-standard`. Все вызовы git — с
`GIT_OPTIONAL_LOCKS=0`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): корпус и read-only копия (§1.3–1.4)`.

---

### Task 4: окружение как данные

**Files:** `selfcheck/env.py`; тест `tests/selfcheck/test_env.py`.

`tests/selfcheck/test_env.py`:

```python
"""Task 4 — target environment as data only (§1.5)."""

from __future__ import annotations

import os
from pathlib import Path

from selfcheck.env import EnvInfo, apply_env_policy, detect_env, package_module_map
from selfcheck.model import Confidence, Finding, Location
from tests.selfcheck.helpers import fake_venv


def test_modes_and_staleness(tmp_path: Path) -> None:
    assert detect_env(tmp_path).mode == "no-env"
    site = fake_venv(tmp_path, version="3.13.7")
    env = detect_env(tmp_path)
    assert (env.mode, env.site_packages, env.python_version, env.stale) == (
        "checkout-venv", site, "3.13", False)
    lock = tmp_path / "uv.lock"
    lock.write_text("")
    later = (tmp_path / ".venv" / "pyvenv.cfg").stat().st_mtime + 60
    os.utime(lock, (later, later))
    assert detect_env(tmp_path).stale is True


def test_mapping_keyed_by_declaration_spelling(tmp_path: Path) -> None:
    site = fake_venv(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = ["pyyaml>=6.0.3"]\n'
                         '[dependency-groups]\ndev = ["Tomli", {include-group = "x"}]\n')
    assert package_module_map(site, pyproject) == "pyyaml=_yaml|yaml,Tomli=tomli"


def imp(rule: str) -> Finding:
    return Finding(rule=rule, category="deps", severity="medium",
                   confidence=Confidence.LIKELY, owner_repo="r",
                   anchor="file:a.py", locations=[Location("a.py", 1)])


def test_no_env_moves_import_class_out() -> None:
    kept, counts = apply_env_policy(
        [imp("deptry/DEP001"), imp("pyrefly/missing-import"), imp("ruff/F401")],
        EnvInfo("no-env"))
    assert [f.rule for f in kept] == ["ruff/F401"]
    assert counts == {"deptry/DEP001": 1, "pyrefly/missing-import": 1}


def test_stale_caps_import_class() -> None:
    kept, counts = apply_env_policy([imp("deptry/DEP001"), imp("ruff/F401")],
                                    EnvInfo("checkout-venv", stale=True))
    assert counts == {}
    assert [f.confidence for f in kept] == [Confidence.CANDIDATE, Confidence.LIKELY]
```

**Эскиз.** `detect_env`: нужен `.venv/pyvenv.cfg` и
`.venv/lib/python*/site-packages`; версия — `version_info` до минорной;
`stale` — `uv.lock` новее `pyvenv.cfg`. `package_module_map`: имена из
`METADATA` (`Name:`), модули из `top_level.txt`, иначе верхние элементы
`RECORD`; ключи — в написании деклараций `pyproject.toml`
(`dependencies`, `optional-dependencies`, `dependency-groups`, словари
`include-group` пропускаются), сопоставление по PEP 503.
`IMPORT_CLASS_RULES = {"deptry/DEP001", "pyrefly/missing-import"}`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): окружение цели — только данные (§1.5)`.

---

### Task 5: контракт пробы

**Files:** `selfcheck/probes/__init__.py`, `selfcheck/probes/base.py`,
`selfcheck/probes/common.py`; тест `tests/selfcheck/test_probe_base.py`.

`tests/selfcheck/test_probe_base.py`:

```python
"""Task 5 — probe contract: statuses, canary, coverage, instrument findings (§4)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.model import Location
from selfcheck.probes.base import (
    Canary,
    ParseResult,
    ProbeCtx,
    ProbeResult,
    ProbeSpec,
    ProbeStatus,
    RepoTarget,
    canary_files,
    instrument_findings,
    run_probe,
)
from selfcheck.probes.common import copy_paths, line_finding, rel_path
from tests.selfcheck.helpers import fake_tool, make_repo

CANARY = Canary(".selfcheck-canary/fake/c.py", "x = 1\n", "fake/CAN",
                "file:.selfcheck-canary/fake/c.py")


def parse_fake(ctx: ProbeCtx, proc: subprocess.CompletedProcess[str]) -> ParseResult:
    data = json.loads(proc.stdout)
    return ParseResult(
        [line_finding(ctx, f"fake/{i['code']}", rel_path(ctx, i["path"]), i["line"],
                      category="bug", severity="low") for i in data["items"]],
        processed_paths=[rel_path(ctx, p) for p in data["processed"]])


def spec_for(tool: Path, **overrides: object) -> ProbeSpec:
    fields: dict[str, object] = dict(
        name="fake", languages=frozenset({"python"}), input_mode="files",
        select=lambda t: tuple(p for p in t.corpus if p.endswith(".py")),
        canary=CANARY, binary=str(tool), version_range=((1, 0), (2, 0)),
        normal_codes=frozenset({0, 1}), argv=copy_paths, parse=parse_fake,
        timeout=5, version_timeout=2)
    fields.update(overrides)
    return ProbeSpec(**fields)  # type: ignore[arg-type]


def which(binary: str) -> str | None:
    return binary if Path(binary).exists() else None


@pytest.fixture
def target(tmp_path: Path):
    repo = make_repo(tmp_path / "repo", {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec_for(Path("t"))]))
    yield RepoTarget("repo", repo, copy, frozenset({"python"}), corpus, EnvInfo("no-env"))
    release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    return run_probe(spec, target, tmp / "run" / "work", which=which)


def test_findings_with_nonzero_normal_code_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b")), target, tmp_path)
    assert (res.status, res.canary, res.exit_code) == (ProbeStatus.OK, "hit", 1)
    assert sorted(f.locations[0] for f in res.findings) == [Location("a.py", 1),
                                                           Location("b.py", 1)]
    assert res.tool_version == "1.2.3"
    assert res.coverage["passed"] == ["a.py", "b.py"]


def test_clean_nonempty_corpus_is_ok(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", emit_repo=False)), target, tmp_path)
    assert res.status is ProbeStatus.OK and res.findings == []


def test_canary_missed(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", emit_canary=False)), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


def test_canary_right_rule_wrong_anchor_is_missed(target, tmp_path) -> None:
    other = Canary(CANARY.relpath, CANARY.content, "fake/CAN", "func:elsewhere::f")
    res = run(spec_for(fake_tool(tmp_path / "b"), canary=other), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")


def test_canary_suppressed_by_config(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path / "b", emit_canary=False),
                    config_suppresses=lambda ctx: True)
    assert run(spec, target, tmp_path).reason == "canary-suppressed-by-config"


def test_exit_code_outside_normal_set(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", code=7)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("exit-code")


def test_unparsable_output(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", stdout="not json", code=0)),
              target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("unparsable")


def test_reported_coverage_missing_input_is_partial(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path / "b", unprocessed="b.py"), coverage="reported")
    res = run(spec, target, tmp_path)
    assert res.status is ProbeStatus.PARTIAL
    assert res.coverage["unprocessed"] == ["b.py"]


def test_reported_zero_processed_fails(target, tmp_path) -> None:
    spec = spec_for(fake_tool(tmp_path / "b", unprocessed="*"), coverage="reported")
    res = run(spec, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "zero-processed")


def test_missing_binary_unavailable(target, tmp_path) -> None:
    assert run(spec_for(tmp_path / "nope"), target, tmp_path).status is ProbeStatus.UNAVAILABLE


def test_version_out_of_range_unavailable(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", version="3.0.0")), target, tmp_path)
    assert res.status is ProbeStatus.UNAVAILABLE and "3.0.0" in res.reason


def test_version_timeout_fails_without_raising(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", version_sleep=5)), target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "timeout")


def test_run_timeout_fails_only_that_probe(target, tmp_path) -> None:
    slow = run(spec_for(fake_tool(tmp_path / "slow", sleep=10), timeout=1), target, tmp_path)
    fast = run(spec_for(fake_tool(tmp_path / "fast"), name="fake2"), target, tmp_path)
    assert (slow.status, slow.reason) == (ProbeStatus.FAILED, "timeout")
    assert fast.status is ProbeStatus.OK


def test_write_to_corpus_is_visible_failure(target, tmp_path) -> None:
    res = run(spec_for(fake_tool(tmp_path / "b", write_copy=True)), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("write-to-corpus")


def test_no_inputs_and_language_skip(target, tmp_path) -> None:
    tool = fake_tool(tmp_path / "b")
    none = run(spec_for(tool, select=lambda t: ()), target, tmp_path)
    shell_only = RepoTarget("repo", target.source, target.copy, frozenset(),
                            target.corpus, target.env)
    py = run(spec_for(tool, name="py"), shell_only, tmp_path)
    anyp = run(spec_for(tool, name="any", languages=frozenset({"any"})), shell_only, tmp_path)
    assert (none.status, none.reason, none.canary) == (ProbeStatus.SKIPPED, "no-inputs", None)
    assert (py.status, py.reason) == (ProbeStatus.SKIPPED, "language")
    assert anyp.status is ProbeStatus.OK


def test_select_error_is_a_status_not_an_exception(target, tmp_path) -> None:
    def broken(t: RepoTarget) -> tuple[str, ...]:
        raise OSError("disk")

    res = run(spec_for(fake_tool(tmp_path / "b"), select=broken), target, tmp_path)
    assert res.status is ProbeStatus.FAILED and res.reason.startswith("select-error")


def test_relative_paths_are_rejected(target, tmp_path) -> None:
    relative = RepoTarget("repo", target.source, Path("run/src/repo"), target.languages,
                          target.corpus, target.env)
    with pytest.raises(ValueError):
        run_probe(spec_for(fake_tool(tmp_path / "b")), relative, tmp_path / "w", which=which)
    with pytest.raises(ValueError):
        run_probe(spec_for(fake_tool(tmp_path / "b")), target, Path("w"), which=which)


def test_internal_analyzer_contract(target, tmp_path) -> None:
    def good(ctx: ProbeCtx) -> ParseResult:
        return ParseResult([line_finding(ctx, "fake/CAN", CANARY.relpath, 1,
                                         category="bug", severity="low")])

    def boom(ctx: ProbeCtx) -> ParseResult:
        raise RuntimeError("x")

    base = dict(languages=frozenset({"any"}), input_mode="files",
                select=lambda t: t.corpus, canary=CANARY, logic_version=1)
    ok = run_probe(ProbeSpec(name="int", analyze=good, **base), target, tmp_path / "w")
    bad = run_probe(ProbeSpec(name="int2", analyze=boom, **base), target, tmp_path / "w")
    assert ok.status is ProbeStatus.OK
    assert (bad.status, bad.reason) == (ProbeStatus.FAILED, "analyzer-error: RuntimeError('x')")
    assert ok.tool_version is not None and ok.tool_version.endswith("/logic 1")
    variants = [RepoTarget("repo", target.source, target.copy, target.languages, target.corpus,
                           target.env, roles=roles, corpus_exclude=exclude)
                for roles, exclude in [({}, ()), ({"test": ("qa/**",)}, ()), ({}, ("vendor/**",))]]
    hashes = {run_probe(ProbeSpec(name=f"h{i}", analyze=good, **base), t,
                        tmp_path / "w").config_hash for i, t in enumerate(variants)}
    assert len(hashes) == 3


def test_instrument_findings() -> None:
    rows = [ProbeResult("a", "r", ProbeStatus.FAILED, "timeout"),
            ProbeResult("b", "r", ProbeStatus.PARTIAL, "per-file problems"),
            ProbeResult("c", "r", ProbeStatus.UNAVAILABLE, "c not found"),
            ProbeResult("d", "r", ProbeStatus.OK), ProbeResult("e", "r", ProbeStatus.SKIPPED)]
    found = {(f.rule, f.severity) for f in instrument_findings(rows)}
    assert found == {("selfcheck/probe-failed", "high"),
                     ("selfcheck/probe-partial", "medium"),
                     ("selfcheck/probe-unavailable", "medium")}
```

**Эскиз.** Порядок в `run_probe`: проверка абсолютности `target.copy` и
`work_root` → язык → входы → каталог пробы → **один** `try` вокруг проверки
версии (свой `version_timeout`), запуска, разбора и оценки. Статусы по
таблице §4.2. Канарейка `hit`, только если среди находок в путях роли
`canary` есть находка с `rule == expect_rule` **и** `anchor ==
expect_anchor`; находки канарейки вычитаются. `reported`: `unprocessed =
passed − processed_paths`; пусто → ok, всё → `failed: zero-processed`,
часть → `partial`. `instrument_findings`: `failed` → `selfcheck/probe-failed`
(high), `partial` → `selfcheck/probe-partial` (medium), `unavailable` →
`selfcheck/probe-unavailable` (medium), якорь `probe:<repo>#<probe>`,
`text_key` — причина. Тела `rel_path`, `line_finding` — как в спеке §2.1.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): контракт пробы — статусы, канарейка, покрытие (§4)`.

---

### Task 6: Python-пробы

**Files:** `selfcheck/probes/python_tools.py`; тест `tests/selfcheck/test_python_tools.py`.

`tests/selfcheck/test_python_tools.py`:

```python
"""Task 6 — Python static probes on a read-only copy (§3.1, §1.5, §4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import detect_env
from selfcheck.model import Confidence
from selfcheck.probes.base import ProbeResult, ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.probes.python_tools import DEPTRY, PYREFLY, PYTHON_PROBES, RADON, RUFF, VULTURE
from tests.selfcheck.helpers import fake_venv, make_repo, mi_rank_c_source, require_tool

# [tool.ruff] cuts off any user-level ruff config on the test machine (review r2 m9).
PYPROJECT = ('[project]\nname = "x"\nversion = "0"\ndependencies = ["pyyaml>=6"]\n'
             "[tool.ruff]\n")


@pytest.fixture
def build(tmp_path: Path):
    copies: list[Path] = []

    def _build(files: dict[str, str], *, venv_marker: Path | None = None,
               pyproject: str = PYPROJECT) -> RepoTarget:
        repo = make_repo(tmp_path / "repo", {"pyproject.toml": pyproject,
                                             ".gitignore": ".venv/\n", **files})
        if venv_marker is not None:
            fake_venv(repo, evil_marker=venv_marker)
        corpus = tuple(list_corpus(repo))
        copy = tmp_path / "run" / "src" / "repo"
        materialize(repo, corpus, copy, canary_files(PYTHON_PROBES))
        copies.append(copy)
        return RepoTarget("repo", repo, copy, frozenset({"python"}), corpus, detect_env(repo))

    yield _build
    for copy in copies:
        release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    require_tool(spec.binary or "")
    return run_probe(spec, target, tmp / "run" / "work")


CLEAN = {"pkg/__init__.py": "", "pkg/m.py": (
    "import yaml\n\n\ndef load(text: str) -> object:\n    return yaml.safe_load(text)\n")}


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(spec: ProbeSpec, build, tmp_path: Path) -> None:
    res = run(spec, build(CLEAN), tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit"


def test_ruff_reports_repo_violation(build, tmp_path: Path) -> None:
    res = run(RUFF, build({"a.py": "import os\n"}), tmp_path)
    assert [(f.rule, f.anchor, f.confidence) for f in res.findings] == [
        ("ruff/F401", "file:a.py", Confidence.LIKELY)]


def test_vulture_confidence_scale(build, tmp_path: Path) -> None:
    source = "import os\n\n\ndef f():\n    return 1\n    print('x')\n"
    conf = {f.rule: f.confidence for f in run(VULTURE, build({"u.py": source}), tmp_path).findings}
    assert conf["vulture/unused-import"] is Confidence.CANDIDATE          # 90 %
    assert conf["vulture/unreachable-code-after"] is Confidence.LIKELY    # 100 %


def test_ruff_cli_restores_ignored_canary_rule(build, tmp_path: Path) -> None:
    res = run(RUFF, build({"ruff.toml": '[lint]\nignore = ["F401"]\n', "a.py": "x = 1\n"}),
              tmp_path)
    assert res.status is ProbeStatus.OK


def test_ruff_per_file_ignore_suppresses_canary(build, tmp_path: Path) -> None:
    target = build({"ruff.toml": '[lint.per-file-ignores]\n".selfcheck-canary/**" = ["F401"]\n',
                    "a.py": "x = 1\n"})
    res = run(RUFF, target, tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-suppressed-by-config")


def test_vulture_syntax_error_on_stderr_is_partial(build, tmp_path: Path) -> None:
    res = run(VULTURE, build({"bad.py": "def f(:\n"}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL
    assert "bad.py" in res.coverage["skipped"]


def test_pyrefly_default_preset_catches_bad_return(build, tmp_path: Path) -> None:
    # bad-assignment is silent under the `basic` preset and is not the canary rule
    res = run(PYREFLY, build({"a.py": 'x: int = "s"\n'}), tmp_path)
    assert "pyrefly/bad-assignment" in {f.rule for f in res.findings}
    assert "--preset" in res.argv


def test_pyrefly_configured_repo_keeps_its_preset(build, tmp_path: Path) -> None:
    res = run(PYREFLY, build({"a.py": "x = 1\n", "pyrefly.toml": "\n"}), tmp_path)
    assert res.status is ProbeStatus.OK and "--preset" not in res.argv


def test_radon_cc_and_mi(build, tmp_path: Path) -> None:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(22))
    target = build({"c.py": f"def big(x: int) -> int:\n{body}    return -1\n",
                    "t.py": mi_rank_c_source()})
    rules = {(f.rule, f.anchor) for f in run(RADON, target, tmp_path).findings}
    assert ("radon/cc-D", "func:c.py::big") in rules
    assert ("radon/mi-C", "file:t.py") in rules


def test_deptry_env_as_data(build, tmp_path: Path) -> None:
    marker = tmp_path / "EXECUTED"
    target = build({"a.py": "import yaml\n"}, venv_marker=marker)
    deptry, pyrefly = run(DEPTRY, target, tmp_path), run(PYREFLY, target, tmp_path)
    assert not marker.exists(), "target startup hooks must not run"
    assert deptry.status is ProbeStatus.OK and pyrefly.status is ProbeStatus.OK
    assert not any(f.rule == "deptry/DEP003" for f in deptry.findings)
    assert not any("'yaml'" in e["detail"] for f in deptry.findings for e in f.evidence)
    assert any("DEP003 off" in note for note in deptry.coverage["notes"])


def test_deptry_dep002_per_package_distinct(build, tmp_path: Path) -> None:
    pyproject = ('[project]\nname = "x"\nversion = "0"\n'
                 'dependencies = ["pyyaml>=6", "requests>=2"]\n')
    res = run(DEPTRY, build({"a.py": "x = 1\n"}, pyproject=pyproject), tmp_path)
    dep002 = [f for f in res.findings if f.rule == "deptry/DEP002"]
    assert len(dep002) == 2 and len({f.id for f in dep002}) == 2


def test_deptry_excludes_really_apply(build, tmp_path: Path) -> None:
    pyproject = PYPROJECT + '[tool.deptry]\nextend_exclude = ["legacy"]\n'
    undeclared = "import undeclared_mod\n"
    target = build({"a.py": "import yaml\n", "legacy/x.py": undeclared,
                    "tests/test_x.py": undeclared}, pyproject=pyproject)
    res = run(DEPTRY, target, tmp_path)
    assert res.coverage["expected_files"] == ["a.py"]
    flagged = {loc.path for f in res.findings for loc in f.locations}
    assert not flagged & {"legacy/x.py", "tests/test_x.py"}


BROKEN = {"ruff": "bad.py", "pyrefly": "bad.py", "vulture": "bad.py", "radon": "bad.py",
          "deptry": "bad.py"}


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_parse_error_in_one_file_is_partial(spec: ProbeSpec, build, tmp_path: Path) -> None:
    res = run(spec, build({**CLEAN, BROKEN[spec.name]: "def f(:\n"}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL, (res.status, res.reason)
    assert BROKEN[spec.name] in res.coverage["skipped"]


@pytest.mark.parametrize("spec", PYTHON_PROBES, ids=lambda s: s.name)
def test_canary_rule_disabled_in_registry_is_missed(spec: ProbeSpec, build,
                                                    tmp_path: Path) -> None:
    broken = replace(spec, canary=replace(spec.canary, expect_rule="never/rule"))
    res = run(broken, build(CLEAN), tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")
```

**Эскиз (факты, замеренные 2026-09-26).**
- ruff: `check --no-cache --output-format json --extend-select F,B,PL,SIM,ERA,C90,ARG,RET <файлы>`;
  коды 0/1; синтаксис — `"code": "invalid-syntax"` → `skipped`; `--extend-select` из CLI
  восстанавливает правило, выключенное `ignore`, но не `per-file-ignores`
  (эта ветка → `canary-suppressed-by-config`).
- pyrefly: `check --output-format json --summary=none --skip-interpreter-query
  --python-platform <host> [--preset default] [--site-package-path S
  --python-version V] --error bad-return <файлы>`; коды 0/1; без
  конфигурации pyrefly пресет `basic` молча не репортит `bad-assignment`
  (а `bad-return` репортит, если он включён явно `--error`, — поэтому тест
  пресета смотрит на `bad-assignment`); синтаксис — `name: parse-error` →
  `skipped`.
- vulture: `--min-confidence 60 <файлы>`; коды 0/1/3; находки в stdout
  `path:line: msg (N% confidence)`; **синтаксические ошибки — в stderr**
  (`path:1: invalid syntax at …`) при коде 3 → `skipped` + `partial`;
  строка с `Error:` → `unparsable`.
- radon: `cc -j --min D <файлы>` и `mi -j --min C <файлы>` — два вызова в
  одной пробе (или `argv` на cc, второй вызов через `runner` в `parse`);
  покрытие `reported` по ключам JSON; синтаксис — `{"<file>": {"error": …}}`
  при коде 0 → `skipped`.
- deptry: форма `roots`, **`cwd = копия`** (спека §1.3, исключение `cwd`):
  `. --config pyproject.toml --json-output <work>/deptry.json --ignore
  DEP003 --extend-exclude .selfcheck-canary [--package-module-name-map M]`;
  с абсолютным корнем его регулярные исключения (свои и `tests`/`venv`) не
  срабатывают — замер ревью раунда 2; коды 0/1; синтаксис — строка
  `Warning: Skipping processing of <file> because …` → `skipped`;
  `key_text = "<код>:<модуль>"`;
  `expected_files` учитывает дефолтные исключения deptry и
  `exclude`/`extend_exclude` из `[tool.deptry]`; `notes` содержит
  `DEP003 off — tool-env packages not covered`.

- [ ] **Step:** red → реализация →
  `SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck/test_python_tools.py -q`
  → линт/типы → коммит `feat(selfcheck): Python-пробы (§3.1, §1.5)`.

---

### Task 7: shell, workflows, текстовые клоны

**Files:** `selfcheck/probes/other_tools.py`; тест `tests/selfcheck/test_other_tools.py`.

`tests/selfcheck/test_other_tools.py`:

```python
"""Task 7 — shell, workflow and text-clone probes (§3.1)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.probes.base import ProbeResult, ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.probes.other_tools import ACTIONLINT, JSCPD, OTHER_PROBES, SHELLCHECK, ZIZMOR, shell_files
from tests.selfcheck.helpers import make_repo, require_npx_package, require_tool

WORKFLOW = """on: push
permissions: {}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
BODY = "".join(f"    v{i} = a + {i}\n" for i in range(10))
DUP = f"def one(a):\n{BODY}    return a\n\n\ndef two(a):\n{BODY}    return a\n"
CLEAN = {"run.sh": '#!/bin/sh\necho "$1"\n', "tool": '#!/usr/bin/env bash\nset -eu\necho "ok"\n',
         ".github/workflows/ci.yml": WORKFLOW, "m.py": "def f(a):\n    return a\n"}


@pytest.fixture
def build(tmp_path: Path):
    copies: list[Path] = []

    def _build(files: dict[str, str]) -> RepoTarget:
        repo = make_repo(tmp_path / "repo", files)
        corpus = tuple(list_corpus(repo))
        copy = tmp_path / "run" / "src" / "repo"
        materialize(repo, corpus, copy, canary_files(OTHER_PROBES))
        copies.append(copy)
        return RepoTarget("repo", repo, copy, frozenset(), corpus, EnvInfo("no-env"))

    yield _build
    for copy in copies:
        release(copy)


def run(spec: ProbeSpec, target: RepoTarget, tmp: Path) -> ProbeResult:
    if spec is JSCPD or spec.name == "jscpd":
        require_npx_package("jscpd@4.3.0")
    else:
        require_tool(spec.binary or "")
    return run_probe(spec, target, tmp / "run" / "work")


@pytest.mark.parametrize("spec", OTHER_PROBES, ids=lambda s: s.name)
def test_clean_repo_ok_on_read_only_copy(spec: ProbeSpec, build, tmp_path: Path) -> None:
    res = run(spec, build(CLEAN), tmp_path)
    assert res.status is ProbeStatus.OK, res.reason
    assert res.canary == "hit" and res.findings == []


def test_shell_selection_by_suffix_and_shebang(build) -> None:
    target = build({**CLEAN, "script.py": "#!/usr/bin/env python3\n", "notes": "plain\n"})
    assert set(shell_files(target)) == {"run.sh", "tool"}


def test_shellcheck_finds_unquoted(build, tmp_path: Path) -> None:
    res = run(SHELLCHECK, build({"x.sh": "#!/bin/sh\necho $1\n"}), tmp_path)
    assert [f.rule for f in res.findings] == ["shellcheck/SC2086"]


def test_workflow_injection(build, tmp_path: Path) -> None:
    bad = WORKFLOW.replace("echo ok", "echo ${{ github.event.head_commit.message }}")
    target = build({".github/workflows/ci.yml": bad})
    assert {f.rule for f in run(ACTIONLINT, target, tmp_path).findings} == {"actionlint/expression"}
    assert "zizmor/template-injection" in {f.rule for f in run(ZIZMOR, target, tmp_path).findings}


def test_jscpd_clone_reported_coverage(build, tmp_path: Path) -> None:
    res = run(JSCPD, build({"d.py": DUP, "e.py": "x = 1\n"}), tmp_path)
    assert res.status is ProbeStatus.OK      # e.py (< 5 lines) is not "unprocessed"
    assert res.coverage["unprocessed"] == []
    assert [f.anchor.split(":")[1] for f in res.findings] == ["text"]
    clone = res.findings[0]
    assert (clone.occurrences, clone.severity, clone.confidence.value) == (2, "medium", "likely")


BROKEN = {"shellcheck": ("bad.sh", "#!/bin/sh\nif then fi (\n"),
          "actionlint": (".github/workflows/bad.yml", "on: [push\njobs: {\n"),
          "zizmor": (".github/workflows/bad.yml", "on: [push\njobs: {\n")}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_parse_error_in_one_file_is_partial(name: str, build, tmp_path: Path) -> None:
    spec = next(s for s in OTHER_PROBES if s.name == name)
    path, text = BROKEN[name]
    res = run(spec, build({**CLEAN, path: text}), tmp_path)
    assert res.status is ProbeStatus.PARTIAL, (res.status, res.reason)
    assert path in res.coverage["skipped"] + res.coverage.get("unprocessed", [])


@pytest.mark.parametrize("spec", OTHER_PROBES, ids=lambda s: s.name)
def test_canary_rule_disabled_in_registry_is_missed(spec: ProbeSpec, build,
                                                    tmp_path: Path) -> None:
    broken = replace(spec, canary=replace(spec.canary, expect_rule="never/rule"))
    res = run(broken, build(CLEAN), tmp_path)
    assert (res.status, res.reason) == (ProbeStatus.FAILED, "canary-missed")
```

**Эскиз.** shellcheck `-f json1`, коды 0/1, версия `-V`; выбор файлов — по
`.sh`/`.bash` и по shebang у безрасширенных; ошибки разбора — коды
SC1000–SC1199 уровня `error` (замер: `SC1073`, `SC1072`) → `skipped`, не
находки. actionlint `-format '{{json .}}' -no-color`, коды 0/1, версия
`-version`; kind `syntax-check` → `skipped`. zizmor `--offline --format
json`, коды 0/11–14, строка — `start_point.row + 1`; битый YAML он не
называет по имени (`WARN … failed to parse input`), но печатает в stderr
`completed <path>` по каждому обработанному файлу — покрытие `reported` по
этим строкам. jscpd — `npx --yes
jscpd@4.3.0 --silent --absolute --reporters json --output <work>/jscpd
--store-path <work>/jscpd-store <файлы>`, покрытие `reported` по
`statistics.formats.*.sources`, ожидаемые входы — файлы известного jscpd
формата длиной ≥ 5 строк (спека §4.1); клон → `dup:text:<sha1 нормализованного фрагмента>`,
`likely`, `medium`, участник `member` = начальная строка фрагмента.

- [ ] **Step:** red → реализация → green (с `SELFCHECK_REQUIRE_TOOLS=1`) →
  линт/типы → коммит `feat(selfcheck): shellcheck/actionlint/zizmor/jscpd (§3.1)`.

---

### Task 8: граф — узлы, корни, рёбра структурных источников

**Files:** `selfcheck/graph/__init__.py`, `selfcheck/graph/model.py`,
`selfcheck/graph/commands.py`, `selfcheck/graph/build.py`,
`selfcheck/graph/resolver.py` (пока заглушка `add_exec_edges`);
тест `tests/selfcheck/test_graph_build.py`.

`tests/selfcheck/test_graph_build.py`:

```python
"""Task 8 — graph nodes, roots, edges from structured sources (§3.2.1–3.2.2)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.commands import build_index, scan_command
from selfcheck.graph.model import EdgeKind, Graph, NodeKind
from selfcheck.roles import role_of
from tests.selfcheck.helpers import plist_dir, write


def graph(tmp: Path, files: dict[str, str], sched_dir: Path | None = None) -> Graph:
    write(tmp / "repo", files)
    return build_graph(sorted(files), tmp / "repo", role_of, repo_name="repo",
                       sched_dir=sched_dir)


def kinds(g: Graph, anchor: str) -> set[EdgeKind]:
    return {e.kind for e in g.incoming(anchor)}


INDEX = build_index(["a.py", "deploy/r16/setup.sh", "gov/__init__.py", "gov/runner.py",
                     "x.sh", "gov/__main__.py"], None)


def test_scan_python_m_module() -> None:
    scan = scan_command("@uv run --frozen --group g python -m gov.runner --x", "",
                        INDEX, shell_vars=False)
    assert scan.targets == ["gov/__init__.py", "gov/runner.py"]
    assert scan_command("python3 -m gov", "", INDEX, shell_vars=False).targets == [
        "gov/__init__.py", "gov/__main__.py"]


def test_scan_wrappers_and_assignments() -> None:
    scan = scan_command("sudo GIT_BASE=u deploy/r16/setup.sh", "", INDEX, shell_vars=True)
    assert scan.targets == ["deploy/r16/setup.sh"]


def test_scan_argument_is_mention_not_target() -> None:
    scan = scan_command("cat a.py && shellcheck x.sh", "", INDEX, shell_vars=True)
    assert scan.targets == [] and scan.mentions == ["a.py", "x.sh"]


def test_scan_unresolved_and_missing() -> None:
    assert scan_command('sh "$kit/local.sh" "$@"', "", INDEX,
                        shell_vars=True).unresolved == ["$kit/local.sh"]
    scan = scan_command("./missing.py --flag && ./x.sh", "", INDEX, shell_vars=True)
    assert (scan.targets, scan.missing) == (["x.sh"], ["./missing.py"])
    assert scan_command("$(PYTHON) ./a.py", "", INDEX, shell_vars=False).targets == ["a.py"]


def test_makefile_roots_continuations_and_make_edges(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "Makefile": ('help:\n\t@echo "make run — запуск"\n'
                     "run: ; @python3 ./a.py $(ARGS)\n"
                     "multi:\n\t@./b.sh \\\n\t  && ./c.sh\n"
                     "all:\n\t$(MAKE) multi\n"),
        "a.py": "print(1)\n", "b.sh": "echo\n", "c.sh": "echo\n"})
    assert kinds(g, "file:a.py") == {EdgeKind.MAKE}
    assert kinds(g, "file:c.sh") == {EdgeKind.MAKE}
    assert g.nodes["make:Makefile#run"].root and not g.nodes["make:Makefile#multi"].root
    assert kinds(g, "make:Makefile#multi") == {EdgeKind.MAKE}


def test_broken_roots(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\nquiet:\n\t@./gone2.py\n',
        "skills/s/SKILL.md": "```bash\n./missing_tool.sh --x\n```\n",
        "pyproject.toml": '[project.scripts]\nghost = "pkg.nothere:main"\n'})
    assert {(a, tok) for a, _, tok in g.broken} == {
        ("make:Makefile#gone", "./gone.py"),
        ("skill:skills/s/SKILL.md", "./missing_tool.sh"),
        ("cli:ghost", "pkg.nothere")}


def test_imports_absolute_relative_and_packages(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "pkg/__init__.py": "", "pkg/a.py": "from pkg import b\nfrom . import c\n",
        "pkg/b.py": "", "pkg/c.py": "", "main.py": "import pkg.a\n"})
    assert kinds(g, "file:pkg/b.py") == {EdgeKind.IMPORT}
    assert kinds(g, "file:pkg/c.py") == {EdgeKind.IMPORT}
    assert kinds(g, "file:pkg/a.py") == {EdgeKind.IMPORT}


def test_ci_skill_runbook_doc_test_edges(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "main.py": "x = 1\n",
        ".github/workflows/ci.yml": ("on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
                                     "      - run: python3 main.py ${{ github.sha }}\n"),
        "skills/s/SKILL.md": "Запусти `./tool.sh --now`.\n", "tool.sh": "#!/bin/sh\necho\n",
        "deploy/README.md": ("```bash\nsudo X=1 deploy/setup.sh\n```\n"
                             "см. [doc](../only_doc.py)\n"),
        "deploy/setup.sh": "echo\n", "only_doc.py": "x = 1\n",
        "tests/test_x.py": "import helper_mod\nSCRIPT = 'tested.py'\n",
        "helper_mod.py": "", "tested.py": ""})
    assert kinds(g, "file:main.py") == {EdgeKind.CI}
    assert kinds(g, "file:tool.sh") == {EdgeKind.SKILL}
    assert kinds(g, "file:deploy/setup.sh") == {EdgeKind.RUNBOOK}
    assert kinds(g, "file:only_doc.py") == {EdgeKind.DOC}
    assert kinds(g, "file:helper_mod.py") == {EdgeKind.TEST}
    assert kinds(g, "file:tested.py") == {EdgeKind.TEST}
    assert g.nodes["skill:skills/s/SKILL.md"].root
    assert g.nodes["workflow:.github/workflows/ci.yml#t"].root


def test_cli_entry_units_and_launchd(tmp_path: Path) -> None:
    sched = plist_dir(tmp_path, ["/bin/sh", "-c", "cd /home/u/ws/repo && ./nightly.sh"])
    g = graph(tmp_path, {
        "pyproject.toml": '[project.scripts]\nmytool = "pkg.cli:main"\n',
        "pkg/__init__.py": "", "pkg/cli.py": "def main(): ...\n",
        "nightly.sh": "echo\n", "unit.sh": "echo\n",
        "deploy/x.service": "[Service]\nExecStart=/srv/repo/unit.sh --go\n",
        "deploy/x.timer": "[Timer]\nOnCalendar=daily\n"}, sched_dir=sched)
    assert g.nodes["cli:mytool"].root
    assert kinds(g, "file:pkg/cli.py") == {EdgeKind.ENTRY}
    assert kinds(g, "file:nightly.sh") == {EdgeKind.SCHED}
    assert kinds(g, "file:unit.sh") == {EdgeKind.SCHED}
    assert g.nodes["unit:deploy/x.timer"].root and g.nodes["unit:deploy/x.service"].root
    assert kinds(g, "unit:deploy/x.service") == {EdgeKind.SCHED}
    assert g.plists == ["dev.atp.x.plist"]


def test_diagnostic_output_gives_nothing(tmp_path: Path) -> None:
    g = graph(tmp_path, {"reports/old.md": "ran `./lonely.py`\n", "lonely.py": "x = 1\n"})
    assert g.incoming("file:lonely.py") == [] and g.mentions.get("file:lonely.py", []) == []


def test_mentions_and_node_flags(tmp_path: Path) -> None:
    g = graph(tmp_path, {"a.py": "# see helper.py\n", "helper.py": "x = 1\n",
                         "run": "#!/usr/bin/env bash\necho\n",
                         "Makefile": "lint:\n\tshellcheck run\n"})
    assert g.mentions["file:helper.py"] == ["a.py"]
    assert "Makefile" in g.mentions["file:run"] and g.incoming("file:run") == []
    assert g.nodes["file:run"].kind is NodeKind.FILE and g.nodes["file:run"].executable
    assert not g.nodes["file:helper.py"].executable


def test_local_composite_action_and_timer_unit(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        ".github/workflows/ci.yml": ("on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n"
                                     "      - uses: ./scripts/act\n"),
        "scripts/act/action.yml": ("runs:\n  using: composite\n  steps:\n"
                                   "    - run: ./scripts/act/go.sh\n      shell: bash\n"),
        "scripts/act/go.sh": "echo\n",
        "deploy/x.timer": "[Timer]\nOnCalendar=daily\nUnit=y.service\n",
        "deploy/y.service": "[Service]\nExecStart=/bin/true\n"})
    assert kinds(g, "file:scripts/act/go.sh") == {EdgeKind.CI}
    assert kinds(g, "unit:deploy/y.service") == {EdgeKind.SCHED}


def test_runbook_console_and_uv_project_and_entry_points(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "README.md": "```console\n$ uv run --project sub python sub/x.py --go\n```\n",
        "sub/x.py": "print(1)\n",
        "pyproject.toml": '[project.entry-points."maestro.spawners"]\nplug = "pkg.plug:P"\n',
        "pkg/__init__.py": "", "pkg/plug.py": "class P: ...\n"})
    assert kinds(g, "file:sub/x.py") == {EdgeKind.RUNBOOK}
    assert kinds(g, "file:pkg/plug.py") == {EdgeKind.ENTRY}
    assert g.zones == []
```

**Эскиз — ловушки, найденные ревью пары.**
- Разбор команды отделён от разрешения цели: токенизация (`shlex`,
  разделители `&& || ; |`), пропуск присваиваний `X=…` и обёрток
  (`sudo`, `exec`, `env`, `nohup`, `sh`, `bash`, `python`, `python3`,
  `uv run [--project D|--group G|--frozen…]`), затем **одна** цель запуска.
  `@`/`-` снимаются только с первого токена рецепта, **не** с `-m`.
- Файловые токены вне командной позиции → `Scan.mentions`, не `targets`.
- Импорты: абсолютный `from pkg import b` — модули `pkg`, `pkg.b`, без
  префикса текущего пакета; относительный `from . import c` — от пакета
  файла.
- Узлы `unit:` для `.service`/`.timer` (корни); ребро timer → service по
  `Unit=` или одноимённому `.service`. `$(MAKE) x` / `make x` → ребро к
  `make:<p>#x`. broken-root: make-корень, fenced-команда skill, CLI с
  несуществующим модулем.
- Упоминания: голое имя файла в файлах ролей `source`/`skill-root` вне
  определяющего файла, **плюс** файловые аргументы команд.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): граф — узлы, корни, рёбра (§3.2.1–3.2.2)`.

---

### Task 9: вычисляемые запуски и зоны

**Files:** `selfcheck/graph/resolver.py`; тест `tests/selfcheck/test_graph_resolver.py`.

`tests/selfcheck/test_graph_resolver.py`:

```python
"""Task 9 — computed launches and unresolved zones (§3.2.3)."""

from __future__ import annotations

from pathlib import Path

from selfcheck.graph.build import build_graph
from selfcheck.graph.model import EdgeKind, Graph
from selfcheck.roles import role_of
from tests.selfcheck.helpers import write


def graph(tmp: Path, files: dict[str, str]) -> Graph:
    write(tmp, files)
    return build_graph(sorted(files), tmp, role_of, repo_name="r", sched_dir=None)


def exec_targets(g: Graph) -> set[str]:
    return {e.target for e in g.edges if e.kind is EdgeKind.EXEC}


def zone_members(g: Graph) -> set[str]:
    return {m for z in g.zones for m in z.members}


def test_with_name_and_tmux_join(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "console.py": ("import shlex, subprocess, sys\nfrom pathlib import Path\n\n"
                       "def spawn(repo):\n"
                       "    worker = Path(__file__).with_name('worker.py')\n"
                       "    cmd = [sys.executable, str(worker), '--repo', repo]\n"
                       "    shell_cmd = ' '.join(shlex.quote(p) for p in cmd) + '; exec sh'\n"
                       "    subprocess.run(['tmux', 'new-session', '-d', shell_cmd])\n"),
        "worker.py": "print(1)\n"})
    assert exec_targets(g) == {"file:worker.py"} and g.zones == []


def test_parent_chain_and_os_path(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "tools/a.py": ("import os, subprocess\nfrom pathlib import Path\n"
                       "ROOT = Path(__file__).parent.parent\n"
                       "subprocess.run([str(ROOT / 'b.sh')])\n"
                       "subprocess.call(os.path.join(os.path.dirname(__file__), 'c.sh'))\n"),
        "b.sh": "echo\n", "tools/c.sh": "echo\n"})
    assert exec_targets(g) == {"file:b.sh", "file:tools/c.sh"}


def test_shell_script_dir_forms(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "bin/run.sh": ('#!/bin/sh\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
                       'HERE=$(dirname "$0")\n"$SCRIPT_DIR/x.sh" --go\nsh "$HERE/y.sh"\n'),
        "bin/x.sh": "echo\n", "bin/y.sh": "echo\n"})
    assert exec_targets(g) == {"file:bin/x.sh", "file:bin/y.sh"}


def test_unresolved_shell_launch_suffix_zone(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "review.sh": ('#!/bin/sh\nkit=$(resolve "$1")\nREVIEW=1 \\\n'
                      '    sh "$kit/local.sh" "$@"\n'),
        "scripts/review/local.sh": "echo\n", "other.sh": "echo\n"})
    assert zone_members(g) == {"file:scripts/review/local.sh"}


def test_dynamic_import_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "pkg/loader.py": ("import importlib\n\ndef load(name):\n"
                          "    return importlib.import_module(name)\n"),
        "pkg/plugin_a.py": "x = 1\n", "elsewhere.py": "y = 1\n"})
    assert zone_members(g) == {"file:pkg/loader.py", "file:pkg/plugin_a.py"}


def test_non_constant_getattr_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "cmd/dispatch.py": "def run(obj, name):\n    return getattr(obj, name)()\n",
        "cmd/sub.py": "x = 1\n", "top.py": "y = 1\n",
        "cmd/const.py": "def f(o):\n    return getattr(o, 'attr', None)\n"})
    assert zone_members(g) == {"file:cmd/dispatch.py", "file:cmd/sub.py", "file:cmd/const.py"}
    assert len(g.zones) == 1


def test_extensionless_shebang_calls(tmp_path: Path) -> None:
    g = graph(tmp_path, {"harness": '#!/bin/sh\nexec ./helper.sh "$@"\n',
                         "helper.sh": "echo\n"})
    assert exec_targets(g) == {"file:helper.sh"}


def test_same_module_wrapper_resolved_at_call_sites(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "runner.py": ("import subprocess\n\n\ndef _run(cmd, check=True):\n"
                      "    return subprocess.run(cmd, check=check)\n\n\n"
                      "_run(['./tool.sh', '--x'])\n"),
        "tool.sh": "echo\n", "other.sh": "echo\n"})
    assert exec_targets(g) == {"file:tool.sh"} and g.zones == []


def test_constant_import_module_is_an_edge(tmp_path: Path) -> None:
    g = graph(tmp_path, {"a.py": "import importlib\nimportlib.import_module('b')\n", "b.py": ""})
    assert "file:b.py" in exec_targets(g) and g.zones == []


def test_glob_then_launch_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "runner/all.py": ("import subprocess\nfrom pathlib import Path\n\n"
                          "for script in Path(__file__).parent.glob('*.sh'):\n"
                          "    subprocess.run([str(script)])\n"),
        "runner/a.sh": "echo\n", "other.sh": "echo\n"})
    assert zone_members(g) == {"file:runner/all.py", "file:runner/a.sh"}


def test_shell_launch_without_suffix_zones_caller_dir(tmp_path: Path) -> None:
    g = graph(tmp_path, {"bin/go.sh": '#!/bin/sh\ncmd="$1"\n"$cmd" --x\n',
                         "bin/tool.sh": "echo\n", "top.sh": "echo\n"})
    assert zone_members(g) == {"file:bin/go.sh", "file:bin/tool.sh"}


def test_bash_source_dir_form(tmp_path: Path) -> None:
    g = graph(tmp_path, {
        "lib/run.sh": ('#!/usr/bin/env bash\n'
                       'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n"$DIR/z.sh"\n'),
        "lib/z.sh": "echo\n"})
    assert exec_targets(g) == {"file:lib/z.sh"} and g.zones == []
```

**Эскиз.** Мини-вычислитель выражений до строки/списка, неизвестное —
`<?>`: `Path(__file__)`, `.parent`, `.with_name()`, `/`, `+`, `str()`,
`os.path.join/dirname`, f-строки, `" ".join(<генератор/список>)`,
`sys.executable` → `python`; имена — последние простые присваивания в
функции, затем в модуле. Вызовы запуска: `subprocess.*`, `os.system`,
`os.exec*`. Обёртка того же модуля (параметр первым аргументом в запуск)
разрешается по своим вызовам. Неразрешимая командная позиция → зона
(суффикс после последнего `/`, иначе каталог вызывающего). Неконстантный
`import_module`/`__import__`/`getattr(x, name)` → зона каталога. Shell:
присваивания с `dirname "$0"`/`BASH_SOURCE` → каталог скрипта, склейка
строк с `\`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): резолвер запусков и зоны (§3.2.3)`.

---

### Task 10: классификация dead, корни, проба usage-graph

**Files:** `selfcheck/graph/classify.py`, `selfcheck/graph/probe.py`;
тест `tests/selfcheck/test_graph_classify.py`.

`tests/selfcheck/test_graph_classify.py`:

```python
"""Task 10 — dead classification D1–D12, roots, usage-graph probe (§2.3, §3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.graph.build import build_graph
from selfcheck.graph.classify import NodeFacts, Surface, dead_confidence, klass_of
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.model import Confidence
from selfcheck.probes.base import ProbeResult, ProbeStatus, RepoTarget, canary_files, run_probe
from selfcheck.roles import role_of
from tests.selfcheck.helpers import NOW, USAGE_FILES, ago, make_repo, plist_dir, write

C, L, K = Confidence.CONFIRMED, Confidence.LIKELY, Confidence.CANDIDATE

# spec §2.3 matrix: id, class, root, zone, fleet, sched-dir, history, age, mention → result
MATRIX = [
    ("D1", "orphan", False, False, "complete", True, True, 90, False, C),
    ("D2", "orphan", False, False, "absent", True, True, 90, False, L),
    ("D3", "orphan", False, False, "complete", False, True, 90, False, L),
    ("D4", "orphan", False, False, "complete", True, True, 10, False, L),
    ("D5", "orphan", False, False, "complete", True, False, None, False, L),
    ("D6", "orphan", False, False, "complete", True, True, 90, True, L),
    ("D7", "test-only", False, False, "complete", True, True, 90, False, K),
    ("D8", "doc-only", False, False, "absent", False, False, None, True, K),
    ("D9", "orphan", True, False, "complete", True, True, 90, False, None),
    ("D10", "orphan", False, True, "complete", True, True, 90, False, None),
    ("D11", "live", False, False, "complete", True, True, 90, False, None),
]


@pytest.mark.parametrize("row", MATRIX, ids=lambda r: r[0])
def test_dead_matrix(row) -> None:
    _, klass, root, zone, fleet, sched, history, age, mention, expected = row
    surface = Surface(fleet, "/sched" if sched else None, [])
    facts = NodeFacts(klass, root, zone, history, age, mention)
    assert dead_confidence(facts, surface)[0] == expected


def test_d12_plist_makes_live(tmp_path: Path) -> None:
    sched = plist_dir(tmp_path, ["/w/repo/job.py"])
    write(tmp_path / "repo", {"job.py": 'if __name__ == "__main__":\n    pass\n'})
    g = build_graph(["job.py"], tmp_path / "repo", role_of, repo_name="repo", sched_dir=sched)
    assert klass_of(g, "file:job.py") == "live"


def run_usage(tmp: Path, files: dict[str, str], *, date: str = "") -> ProbeResult:
    repo = make_repo(tmp / "repo", files, date=date or ago(90))
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([USAGE_GRAPH]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=NOW)
    try:
        return run_probe(USAGE_GRAPH, target, tmp / "run" / "work")
    finally:
        release(copy)


def by_rule(res: ProbeResult, rule: str) -> dict[str, object]:
    return {f.anchor: f for f in res.findings if f.rule == rule}


def test_s1_orphan_likely_roots_never_dead(tmp_path: Path) -> None:
    res = run_usage(tmp_path, USAGE_FILES)
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    dead = by_rule(res, "usage-graph/dead.file")
    assert set(dead) == {"file:orphan.py"}
    finding = dead["file:orphan.py"]
    assert finding.confidence is Confidence.LIKELY and finding.text_key is None
    assert {"P1", "P2"} <= {e["detail"] for e in finding.evidence if e["kind"] == "cap"}
    assert not any(f.anchor.startswith(("make:", "skill:", "cli:", "unit:", "workflow:"))
                   for f in res.findings if f.rule.startswith("usage-graph/dead"))


def test_zone_reported_not_dead_and_id_stable(tmp_path: Path) -> None:
    files = {"review.sh": '#!/bin/sh\nsh "$kit/local.sh"\n',
             "scripts/review/local.sh": "#!/bin/sh\necho\n"}
    first = run_usage(tmp_path / "a", files)
    shifted = run_usage(tmp_path / "b", {**files, "review.sh": '#!/bin/sh\n\n\nsh "$kit/local.sh"\n'})
    assert "file:scripts/review/local.sh" not in by_rule(first, "usage-graph/dead.file")
    ids = [{f.id for f in r.findings if f.rule == "usage-graph/unresolved-exec"}
           for r in (first, shifted)]
    assert ids[0] and ids[0] == ids[1]


def test_broken_and_stale_roots(tmp_path: Path) -> None:
    res = run_usage(tmp_path, {"Makefile": 'help:\n\t@echo "make gone"\ngone:\n\t@./gone.py\n'},
                    date=ago(400))
    assert "make:Makefile#gone" in by_rule(res, "usage-graph/broken-root")
    assert "make:Makefile#gone" in by_rule(res, "usage-graph/root-stale")
    fresh = run_usage(tmp_path / "f", {"Makefile": 'help:\n\t@echo "make x"\nx:\n\t@true\n'},
                      date=ago(30))
    assert by_rule(fresh, "usage-graph/root-stale") == {}


def test_syntax_error_in_source_is_partial(tmp_path: Path) -> None:
    assert run_usage(tmp_path, {"bad.py": "def f(:\n"}).status is ProbeStatus.PARTIAL


def test_report_graph_payload(tmp_path: Path) -> None:
    res = run_usage(tmp_path, USAGE_FILES)
    graph = res.extra["graph"]
    assert set(graph) == {"file:live.py", "file:orphan.py", "make:Makefile#help",
                          "make:Makefile#go", "skill:skills/s/SKILL.md"}
    assert graph["file:live.py"] == {"class": "live", "root": False,
                                     "edges": [{"kind": "make", "from": "Makefile:3"}]}
    assert graph["file:orphan.py"]["class"] == "orphan"
    assert graph["skill:skills/s/SKILL.md"]["root"] is True
    assert res.extra["surface"] == {"fleet": "absent", "sched_dir": None, "plists": [],
                                    "history": {"live.py": True, "orphan.py": True}}
```

**Эскиз.** `dead_confidence`: живой/корень/зона → `None`; база
`orphan` → confirmed, иначе candidate; потолки P1 (fleet ≠ complete), P2
(нет `sched_dir`), P3 (нет истории), P4 (< 60 дней), P5 (упоминание) — каждый
до `likely`. `classify` берёт `now` из `RepoTarget.now` (тесты — фиксированное
`NOW`). `unresolved-exec` — строковое правило (ключ — текст строки вызова).
`root-stale` — корень старше 180 дней без упоминания имени вне определяющего
файла. Проба строит отдельный граф канарейки (файлы
`.selfcheck-canary/usage-graph/` с ролью `source`) и кладёт в `extra`
`graph` и `surface`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): dead по матрице D1–D12, корни, usage-graph (§2.3, §3.2)`.

---

### Task 11: ast-dup и cli-overlap

**Files:** `selfcheck/dups.py`; тест `tests/selfcheck/test_dups.py`.

`tests/selfcheck/test_dups.py`:

```python
"""Task 11 — ast-dup and cli-overlap (§3.3, §2.1 participant identity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.dups import AST_DUP, CLI_OVERLAP, dup_findings, function_hashes, overlaps
from selfcheck.env import EnvInfo
from selfcheck.model import Confidence
from selfcheck.probes.base import ProbeResult, ProbeSpec, ProbeStatus, RepoTarget, canary_files, run_probe
from tests.selfcheck.helpers import NOW, make_repo

BODY = "".join(f"    {n} = {n}_src + {i}\n" for i, n in enumerate("abcdefg"))


def func(name: str, threshold: int = 5, var: str = "x", deco: str = "",
         ann: str = "") -> str:
    sig = (f"def {name}({var}{ann}, a_src=0, b_src=0, c_src=0, d_src=0, e_src=0, "
           f"f_src=0, g_src=0):\n")
    return (f"{deco}{sig}    \"\"\"doc\"\"\"\n{BODY.replace('a_src', var)}"
            f"    return a if a > {threshold} else b\n")


def hashes(*sources: tuple[str, str]) -> list:
    return [h for src, path in sources for h in function_hashes(src, path)]


def test_exact_ignores_local_names_and_docstrings() -> None:
    one = function_hashes(func("one", var="x"), "a.py")[0]
    two = function_hashes(func("two", var="y").replace('"""doc"""', '"""other"""'), "b.py")[0]
    assert one.exact == two.exact


@pytest.mark.parametrize(("left", "right"), [
    (func("one"), func("two", deco="@cache\n")),
    (func("one"), func("two", ann=": int")),
])
def test_decorator_or_annotation_difference_is_not_exact(left: str, right: str) -> None:
    rules = [f.rule for f in dup_findings(hashes((left, "a.py"), (right, "b.py")), "repo")]
    assert "ast-dup/exact" not in rules


def test_structural_only_when_literals_differ() -> None:
    found = dup_findings(hashes((func("one", 5), "a.py"), (func("two", 9), "b.py")), "repo")
    assert [(f.rule, f.confidence) for f in found] == [("ast-dup/structural", Confidence.CANDIDATE)]
    assert any("9" in e["detail"] for e in found[0].evidence)


def test_exact_group_members_carry_qualname() -> None:
    found = dup_findings(hashes(*[(func(f"f{i}"), f"m{i}.py") for i in range(3)]), "repo")
    assert [(f.rule, f.occurrences, f.confidence) for f in found] == [
        ("ast-dup/exact", 3, Confidence.CONFIRMED)]
    assert {r["member"] for r in found[0].related} == {"f0", "f1", "f2"}
    assert found[0].text_key is None and found[0].severity == "medium"


def test_short_and_different_functions_ignored() -> None:
    short = "def s(x):\n    return x\n"
    other = func("o").replace("+", "-")
    assert dup_findings(hashes((short, "a.py"), (short, "b.py"), (func("f"), "c.py"),
                               (other, "d.py")), "repo") == []


@pytest.mark.parametrize(("a", "b", "hit"), [
    ({"--a", "--b", "--c", "--d"}, {"--a", "--b", "--c", "--d", "--e"}, True),   # 0.8
    ({"--a", "--b", "--c"}, {"--a", "--b", "--c", "--d"}, False),                # 0.75
    ({"--a"}, {"--a"}, False),                                                   # |∩| = 1
])
def test_jaccard_threshold(a: set[str], b: set[str], hit: bool) -> None:
    assert overlaps(frozenset(a), frozenset(b)) is hit


def run(spec: ProbeSpec, tmp: Path, files: dict[str, str]) -> ProbeResult:
    repo = make_repo(tmp / "repo", files)
    corpus = tuple(list_corpus(repo))
    copy = tmp / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([spec]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=NOW)
    try:
        return run_probe(spec, target, tmp / "run" / "work")
    finally:
        release(copy)


def test_ast_dup_probe(tmp_path: Path) -> None:
    res = run(AST_DUP, tmp_path, {"a.py": func("one"), "b.py": func("two")})
    assert res.status is ProbeStatus.OK and res.canary == "hit"
    assert [f.rule for f in res.findings] == ["ast-dup/exact"]


PARSER = ("import argparse\n\n\ndef {n}():\n    p = argparse.ArgumentParser()\n"
          "    p.add_argument('--repo')\n    p.add_argument('--owner')\n"
          "    p.add_argument('--number')\n    p.add_argument('{extra}')\n    return p\n")


def test_cli_overlap_parsers_and_make_recipes(tmp_path: Path) -> None:
    res = run(CLI_OVERLAP, tmp_path, {
        "a.py": PARSER.format(n="a", extra="--mode"),
        "b.py": PARSER.format(n="b", extra="--mode"),
        "c.py": PARSER.format(n="c", extra="--other").replace("--owner", "--x"),
        "Makefile": ("one: ; @python3 ./a.py $(ARGS)\ntwo: ; @python3 ./a.py $(X)\n"
                     "three: ; @python3 ./b.py\n")})
    assert res.status is ProbeStatus.OK
    assert sorted(f.anchor.split(":")[1] for f in res.findings) == ["cli", "make"]
    make = next(f for f in res.findings if f.anchor.startswith("dup:make:"))
    assert make.occurrences == 2 and {r["member"] for r in make.related} == {"one", "two"}
    assert {f.severity for f in res.findings} == {"medium"}
    assert {f.confidence for f in res.findings} == {Confidence.CANDIDATE}


@pytest.mark.parametrize("spec", [AST_DUP, CLI_OVERLAP], ids=lambda s: s.name)
def test_parse_error_is_partial(spec: ProbeSpec, tmp_path: Path) -> None:
    res = run(spec, tmp_path, {"a.py": func("one"), "bad.py": "def f(:\n"})
    assert res.status is ProbeStatus.PARTIAL and "bad.py" in res.coverage["skipped"]
```

**Эскиз.** exact: копия функции, имя `_`, стираются только docstring и имена
локальных переменных/аргументов; **декораторы, аннотации, возвращаемый тип
сохраняются**. structural: дополнительно литералы str/int/float. Функции
≥ 8 строк. Группа exact ≥ 2 → `confirmed`; structural-группа, не
совпадающая целиком с одной exact → `candidate` с литералами в evidence.
`overlaps(a, b)`: `len(a & b) >= 2 and len(a & b) / len(a | b) >= 0.8`.
Рецепты Makefile — через `make_recipes`, `$(X)` → `$V`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): ast-dup и cli-overlap (§3.3)`.

---

### Task 12: llm-sites

**Files:** `selfcheck/llm.py`, `selfcheck/rules/llm.yml`; тест `tests/selfcheck/test_llm.py`.

`tests/selfcheck/test_llm.py`:

```python
"""Task 12 — llm-sites: call sites, AST argv, mechanism D, heuristics (§3.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.corpus import list_corpus, materialize, release
from selfcheck.env import EnvInfo
from selfcheck.llm import LLM_SITES, python_features
from selfcheck.probes.base import ProbeResult, ProbeStatus, RepoTarget, canary_files, run_probe
from tests.selfcheck.helpers import NOW, make_repo, require_tool

CLASSIFY = """import json
import subprocess


def classify(items):
    out = []
    for item in items:
        raw = subprocess.run(["claude", "-p", f"label {item}"],
                             capture_output=True, text=True).stdout
        out.append(json.loads(raw)["label"])
    return out
"""
REVIEW = """import subprocess


def review(diff):
    prompt = f"Review this diff: {diff}"
    return subprocess.run(["codex", "exec", prompt], capture_output=True).stdout
"""
VARIABLE_ARGV = """import subprocess


def spawn(prompt):
    cmd = ["codex", "exec", "--json", prompt]
    return subprocess.run(cmd, capture_output=True, text=True)
"""
ENDPOINT_TEXT = """DOCS = []
for name in ("a", "b"):
    DOCS.append("see /v1/messages for " + name)
"""
HARNESS = '#!/bin/sh\nclaude -p "$1" --output-format json --json-schema s.json\n'
SDK_LOOP = """import json

import anthropic


def tag(items):
    client = anthropic.Anthropic()
    out = []
    for item in items:
        msg = client.messages.create(model="m", max_tokens=5,
                                     messages=[{"role": "user", "content": f"tag {item}"}])
        out.append(json.loads(msg.content[0].text)["tag"])
    return out
"""
HTTP_CALL = """import requests


def ask(text):
    return requests.post("https://api.anthropic.com/v1/messages", json={"q": text}).text
"""
CONFIG = '[agents.reviewer]\nbinary = "codex"\nmodel = "claude-opus-5-5"\n'


def test_features_and_exclusion() -> None:
    feats, excluded = python_features(CLASSIFY, 8)
    assert {"fixed-schema", "loop"} <= set(feats) and not excluded
    assert python_features(REVIEW, 6)[1] is True


@pytest.fixture
def result(tmp_path: Path) -> ProbeResult:
    require_tool("semgrep")
    repo = make_repo(tmp_path / "repo", {"c.py": CLASSIFY, "r.py": REVIEW, "v.py": VARIABLE_ARGV,
                                         "e.py": ENDPOINT_TEXT, "harness": HARNESS,
                                         "agents.toml": CONFIG, "b.py": SDK_LOOP,
                                         "h.py": HTTP_CALL})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=NOW)
    try:
        return run_probe(LLM_SITES, target, tmp_path / "run" / "work")
    finally:
        release(copy)


def test_candidates(result: ProbeResult) -> None:
    assert result.status is ProbeStatus.OK and result.canary == "hit"
    assert sorted(f.anchor for f in result.findings) == ["llm:b.py::tag", "llm:c.py::classify"]
    assert all(f.text_key is None for f in result.findings)
    assert {f.confidence.value for f in result.findings} == {"candidate"}


def test_inventory_mechanisms(result: ProbeResult) -> None:
    rows = {(i["path"], i["mechanism"], i["candidate"]) for i in result.extra["inventory"]}
    assert {("c.py", "A", True), ("r.py", "A", False), ("v.py", "A", False),
            ("harness", "A", False), ("b.py", "B", True), ("h.py", "C", False),
            ("agents.toml", "D", False)} <= rows
    assert not any(i["path"] == "e.py" for i in result.extra["inventory"])


def test_python_parse_error_is_partial(tmp_path: Path) -> None:
    require_tool("semgrep")
    repo = make_repo(tmp_path / "repo", {"c.py": CLASSIFY, "bad.py": "def f(:\n"})
    corpus = tuple(list_corpus(repo))
    copy = tmp_path / "run" / "src" / "repo"
    materialize(repo, corpus, copy, canary_files([LLM_SITES]))
    target = RepoTarget("repo", repo, copy, frozenset({"python"}), corpus,
                        EnvInfo("no-env"), now=NOW)
    try:
        res = run_probe(LLM_SITES, target, tmp_path / "run" / "work")
    finally:
        release(copy)
    assert res.status is ProbeStatus.PARTIAL and "bad.py" in res.coverage["skipped"]
```

**Эскиз.** semgrep (`scan --config rules/llm.yml --json --metrics off
--disable-version-check --no-git-ignore --scan-unknown-extensions --quiet`)
находит кандидатов в точки: любой `subprocess.$F($X, ...)` в Python, строки
вызова харнесса в bash, `$C.messages.create(...)` /
`$C.chat.completions.create(...)`, HTTP-вызовы `requests|httpx.$M($URL, …)` и
`urlopen($URL)`. Для Python argv `$X` досчитывается вычислителем Task 9
(так находится `cmd = [...]; subprocess.run(cmd)`); точка A — только если
первый элемент — харнесс. Строка с endpoint без HTTP-вызова — не точка.
Механизм D: конфиги (`*.toml`, `*.yaml`, `*.yml`, `*.json`) с именем
харнесса или модели → строка инвентаря, никогда не кандидат. Кандидат:
нет исключения и есть `fixed-schema` или `loop`; промпт, невидимый
статически (`"$1"`, `"$@"`, stdin), — только инвентарь (спека §3.4, rev 5.5);
`text_key = None`. semgrep битый Python ошибкой не считает (замер), поэтому
ошибку разбора даёт собственный `ast.parse` каждого `.py` входа →
`skipped`.

- [ ] **Step:** red → реализация → green (с `SELFCHECK_REQUIRE_TOOLS=1`) →
  линт/типы → коммит `feat(selfcheck): llm-sites (§3.4)`.

---

### Task 13: дельта

**Files:** `selfcheck/delta.py`; тест `tests/selfcheck/test_delta.py`.

`tests/selfcheck/test_delta.py`:

```python
"""Task 13 — delta: fates, Δ1–Δ4, statuses, comparability key (§4.3, §2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfcheck.delta import Fate, RunSnapshot, comparability_key, compute_delta, fate
from selfcheck.probes.base import ProbeResult, ProbeStatus
from selfcheck.probes.common import config_hash


def finding(fid: str, anchor: str, *, paths=("a.py",), related=None,
            probe: str = "ruff") -> dict:
    return {"id": fid, "probe": probe, "anchor": anchor, "owner_repo": "devtools",
            "occurrences": len(paths), "locations": [{"path": p, "line": 1} for p in paths],
            "related": related or []}


def snap(tmp: Path, *, scope=("devtools",), corpus=None, keys=None, findings=(),
         missing=()) -> RunSnapshot:
    corpus = corpus if corpus is not None else {"devtools": ["a.py"]}
    sources = {}
    for repo in scope:
        root = tmp / repo
        root.mkdir(exist_ok=True)
        for rel in corpus.get(repo, []):
            (root / rel).write_text("")
        sources[repo] = str(root)
    return RunSnapshot(
        run_id="r", scope=list(scope), materialized=[r for r in scope if r not in missing],
        probe_keys=keys if keys is not None else {
            f"{p}@{r}": "k" for r in scope for p in ("ruff", "ast-dup")},
        corpus={r: corpus.get(r, []) for r in scope}, sources=sources,
        findings={f["id"]: f for f in findings})


def test_new_persisting_changed(tmp_path: Path) -> None:
    base = snap(tmp_path, findings=[finding("a", "file:a.py"), finding("b", "file:a.py")])
    cur = snap(tmp_path, findings=[finding("a", "file:a.py"),
                                   finding("b", "file:a.py", paths=("a.py", "b.py")),
                                   finding("c", "file:a.py")])
    assert compute_delta(base, cur) == ({"a": "persisting", "b": "changed", "c": "new"}, [])


def test_line_shift_is_not_change(tmp_path: Path) -> None:
    moved = finding("a", "file:a.py")
    moved["locations"][0]["line"] = 40
    statuses, _ = compute_delta(snap(tmp_path, findings=[finding("a", "file:a.py")]),
                                snap(tmp_path, findings=[moved]))
    assert statuses == {"a": "persisting"}


def member(path: str, name: str, repo: str = "devtools") -> dict:
    return {"owner_repo": repo, "path": path, "line": 1, "member": name}


def test_participant_identity_includes_member(tmp_path: Path) -> None:
    old = finding("d", "dup:exact:x", probe="ast-dup", paths=("a.py", "a.py"),
                  related=[member("a.py", "f"), member("a.py", "g")])
    new = finding("d", "dup:exact:x", probe="ast-dup", paths=("a.py", "a.py"),
                  related=[member("a.py", "f"), member("a.py", "h")])
    statuses, _ = compute_delta(snap(tmp_path, findings=[old]), snap(tmp_path, findings=[new]))
    assert statuses == {"d": "changed"}


@pytest.mark.parametrize(("setup", "expected"), [
    ("checked", "resolved"), ("deleted", "resolved: file-removed"),
    ("excluded", "not-rechecked"), ("out-of-scope", "not-rechecked"),
    ("key-changed", "not-rechecked"), ("probe-failed", "not-rechecked"),
    ("repo-missing", "not-rechecked"),
])
def test_single_anchor_fates(tmp_path: Path, setup: str, expected: str) -> None:
    base = snap(tmp_path, findings=[finding("x", "func:a.py::f")])
    kwargs: dict = {}
    if setup in ("deleted", "excluded"):
        kwargs["corpus"] = {"devtools": []}
    if setup == "out-of-scope":
        kwargs |= {"scope": ("maestro",), "corpus": {"maestro": []}}
    if setup == "key-changed":
        kwargs["keys"] = {"ruff@devtools": "other"}
    if setup == "probe-failed":
        kwargs["keys"] = {"ruff@devtools": None}
    if setup == "repo-missing":
        kwargs["missing"] = ("devtools",)
    cur = snap(tmp_path, **kwargs)
    if setup == "deleted":
        (tmp_path / "devtools" / "a.py").unlink()
    _, gone = compute_delta(base, cur)
    assert gone == [{"id": "x", "status": expected, "anchor": "func:a.py::f"}]


DUP_MEMBERS = [member("a.py", "f"), member("m.py", "g", "maestro")]


@pytest.mark.parametrize(("fates", "expected"), [
    (("checked", "checked"), "resolved"),                  # Δ1
    (("deleted", "deleted"), "resolved: file-removed"),    # Δ2
    (("checked", "deleted"), "resolved"),                  # Δ3
    (("checked", "out-of-scope"), "not-rechecked"),        # Δ4
    (("checked", "excluded"), "not-rechecked"),            # Δ4
    (("unverified", "checked"), "not-rechecked"),          # Δ4
])
def test_dup_fates(tmp_path: Path, fates: tuple[str, str], expected: str) -> None:
    corpus = {"devtools": ["a.py"], "maestro": ["m.py"]}
    base = snap(tmp_path, scope=("devtools", "maestro"), corpus=corpus,
                findings=[finding("d", "dup:exact:abc", probe="ast-dup",
                                  paths=("a.py", "m.py"), related=DUP_MEMBERS)])
    scope, cur_corpus = ["devtools", "maestro"], {k: list(v) for k, v in corpus.items()}
    keys = {"ast-dup@devtools": "k", "ast-dup@maestro": "k"}
    for m, f in zip(DUP_MEMBERS, fates, strict=True):
        repo = m["owner_repo"]
        if f in ("deleted", "excluded"):
            cur_corpus[repo] = []
        if f == "out-of-scope":
            scope.remove(repo)
            keys.pop(f"ast-dup@{repo}")
        if f == "unverified":
            keys[f"ast-dup@{repo}"] = None
    cur = snap(tmp_path, scope=tuple(scope), corpus=cur_corpus, keys=keys)
    for m, f in zip(DUP_MEMBERS, fates, strict=True):
        if f == "deleted":
            (tmp_path / m["owner_repo"] / m["path"]).unlink()
    assert compute_delta(base, cur)[1][0]["status"] == expected


def test_persisting_dup_marks_unverified_participant(tmp_path: Path) -> None:
    three = DUP_MEMBERS + [member("b.py", "h")]
    corpus = {"devtools": ["a.py", "b.py"], "maestro": ["m.py"]}
    base = snap(tmp_path, scope=("devtools", "maestro"), corpus=corpus,
                findings=[finding("d", "dup:exact:abc", probe="ast-dup",
                                  paths=("a.py", "b.py", "m.py"), related=three)])
    now = finding("d", "dup:exact:abc", probe="ast-dup", paths=("a.py", "b.py"),
                  related=DUP_MEMBERS[:1] + [member("b.py", "h")])
    cur = snap(tmp_path, scope=("devtools",), corpus={"devtools": ["a.py", "b.py"]},
               findings=[now])
    statuses, _ = compute_delta(base, cur)
    assert statuses == {"d": "changed"}
    marked = [r for r in cur.findings["d"]["related"] if r.get("unverified")]
    assert [(r["owner_repo"], r["path"], r["member"]) for r in marked] == [
        ("maestro", "m.py", "g")]


def result(**kw) -> ProbeResult:
    return ProbeResult(probe=kw.get("probe", "usage-graph"), repo="devtools",
                       status=ProbeStatus.OK, tool_version=kw.get("version"),
                       config_hash=kw.get("config", ""))


def test_internal_analyzer_key_tracks_logic_and_roles() -> None:
    base = comparability_key(result(version="selfcheck 0.1.0/logic 1", config="roles-a"),
                             env_mode="no-env", surface={"fleet": "absent"}, run_dir="/r")
    assert base is not None
    assert base != comparability_key(result(version="selfcheck 0.1.0/logic 2", config="roles-a"),
                                     env_mode="no-env", surface={"fleet": "absent"}, run_dir="/r")
    assert base != comparability_key(result(version="selfcheck 0.1.0/logic 1", config="roles-b"),
                                     env_mode="no-env", surface={"fleet": "absent"}, run_dir="/r")


def test_key_tracks_env_mode_and_repo_config(tmp_path: Path) -> None:
    ruff = ProbeResult("ruff", "devtools", ProbeStatus.OK, tool_version="0.16.9",
                       argv=["ruff", "check", "--no-cache"])
    keys = {comparability_key(ruff, env_mode=m, surface=None, run_dir="/r")
            for m in ("no-env", "checkout-venv")}
    assert len(keys) == 2
    (tmp_path / "ruff.toml").write_text('[lint]\nignore = ["F401"]\n')
    first = config_hash(tmp_path, ("ruff.toml",))
    (tmp_path / "ruff.toml").write_text("[lint]\n")
    assert config_hash(tmp_path, ("ruff.toml",)) != first


@pytest.mark.parametrize(("scope", "keys", "expected"), [
    (("devtools",), {"ruff@devtools": "k"}, "resolved"),
    (("devtools",), {"ruff@devtools": None}, "not-rechecked"),
    (("maestro",), {}, "not-rechecked"),
])
def test_gone_instrument_finding(tmp_path: Path, scope, keys, expected) -> None:
    item = {"id": "p", "probe": "ruff", "anchor": "probe:devtools#ruff",
            "owner_repo": "devtools", "occurrences": 1,
            "locations": [{"path": "-", "line": 1}], "related": [],
            "rule": "selfcheck/probe-failed"}
    base = snap(tmp_path, findings=[item])
    cur = snap(tmp_path, scope=scope, corpus={r: [] for r in scope}, keys=keys)
    assert compute_delta(base, cur)[1][0]["status"] == expected


def test_fate_values(tmp_path: Path) -> None:
    base = snap(tmp_path)
    assert fate(base, base, "devtools", "a.py", "ruff") is Fate.CHECKED
```

**Эскиз.** Порядок судьбы: вне scope → `out-of-scope`; не материализован →
`unverified`; нет в корпусе и нет на диске → `deleted`; нет в корпусе →
`excluded`; ключ текущей пробы `None` или не равен базовому → `unverified`;
иначе `checked`. Участник дубля — `(owner_repo, path, member)`. `changed` —
другое `occurrences`, множество путей или множество участников.
Ключ сопоставимости: sha1 от (проба, `tool_version`, `config_hash`, опции argv
без путей каталога прогона, режим окружения, surface для usage-graph); для
собственных анализаторов `tool_version = "selfcheck <версия>/logic <N>"`,
`config_hash` — sha1 `[roles]` и `[corpus]`. Ушедшая находка прибора
(`probe:<repo>#<probe>`): `resolved`, если репо в scope и ключ пробы сейчас
не `None`; иначе `not-rechecked`.

- [ ] **Step:** red → реализация → green → линт/типы → коммит
  `feat(selfcheck): дельта — судьбы и Δ1–Δ4 (§4.3)`.

---

### Task 14: реестр, отчёт, оркестратор, make, CI, приёмка

**Files:** `selfcheck/registry.py`, `selfcheck/report.py`, `selfcheck/run.py`,
`selfcheck/__main__.py`, `selfcheck.toml`; `Makefile`,
`.github/workflows/ci.yml`, `CLAUDE.md`; тесты
`tests/selfcheck/test_registry.py`, `tests/selfcheck/test_run.py`.

`tests/selfcheck/test_registry.py`:

```python
"""Task 14 — registry invariants (§1.2, §4.1)."""

from __future__ import annotations

from selfcheck.registry import REGISTRY
from selfcheck.roles import Role, role_of

EXPECTED = {"ruff", "pyrefly", "vulture", "radon", "deptry", "shellcheck", "actionlint",
            "zizmor", "jscpd", "usage-graph", "ast-dup", "cli-overlap", "llm-sites"}


def test_registry_invariants() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)) and set(names) == EXPECTED
    assert all(s.executes_target_code is False for s in REGISTRY)
    paths = [s.canary.relpath for s in REGISTRY]
    assert len(paths) == len(set(paths))
    assert all(role_of(p) is Role.CANARY for p in paths)
    assert all(s.logic_version >= 1 for s in REGISTRY if s.analyze is not None)
    assert all(s.rules for s in REGISTRY)
```

`tests/selfcheck/test_run.py`:

```python
"""Task 14 — orchestrator, report, exit codes (§1, §4.2–4.3, §0 criteria)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hashlib

from selfcheck import run as run_module
from selfcheck.corpus import snapshot_hashes
from selfcheck.registry import REGISTRY
from selfcheck.graph.probe import USAGE_GRAPH
from selfcheck.probes.base import Canary, ProbeResult, ProbeSpec, ProbeStatus
from selfcheck.probes.python_tools import RUFF
from selfcheck.report import new_run_dir
from selfcheck.run import exit_code, main
from tests.selfcheck.helpers import plist_dir, require_npx_package, require_tool, workspace


@pytest.mark.parametrize(("statuses", "code"), [
    ([ProbeStatus.OK, ProbeStatus.SKIPPED], 0),
    ([ProbeStatus.OK, ProbeStatus.PARTIAL], 2),
    ([ProbeStatus.FAILED, ProbeStatus.UNAVAILABLE], 2),
    ([ProbeStatus.OK, ProbeStatus.UNAVAILABLE], 3),
])
def test_exit_codes(statuses, code) -> None:
    assert exit_code([ProbeResult("p", "r", s) for s in statuses]) == code


def test_run_dirs_unique_under_frozen_time(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    first, _ = new_run_dir(tmp_path, now)
    second, _ = new_run_dir(tmp_path, now)
    assert first != second and first.startswith("20260926T120000Z-")


def test_run_dir_collision_retries_then_gives_up(tmp_path: Path) -> None:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    new_run_dir(tmp_path, now, token=lambda: "aaaaaa")
    tokens = iter(["aaaaaa", "aaaaaa", "bbbbbb"])
    assert new_run_dir(tmp_path, now, token=lambda: next(tokens))[0].endswith("bbbbbb")
    with pytest.raises(OSError):
        new_run_dir(tmp_path, now, token=lambda: "aaaaaa")


def args(ws: Path, *extra: str) -> list[str]:
    return ["--workspace", str(ws), "--manifest", str(ws / "m.toml"),
            "--out", str(ws / "out"), "--config", str(ws / "none.toml"), *extra]


def reports(ws: Path) -> list[dict]:
    runs = sorted((ws / "out").iterdir(), key=lambda p: (p / "report.json").stat().st_mtime_ns)
    return [json.loads((r / "report.json").read_text()) for r in runs]


def test_end_to_end_report_delta_and_provenance(tmp_path: Path, capsys) -> None:
    ws = workspace(tmp_path)
    sched = plist_dir(tmp_path, ["/x/devtools/live.py"], name="dev.atp.live.plist")
    before = snapshot_hashes(ws / "devtools")
    extra = ("--probe", "usage-graph", "--probe", "ast-dup", "--sched-dir", str(sched))
    assert main(args(ws, *extra)) == 0
    printed = capsys.readouterr().out.strip()
    assert main(args(ws, *extra)) == 0
    assert snapshot_hashes(ws / "devtools") == before                 # §6.2
    first, doc = reports(ws)
    assert printed.endswith(f"{first['run']['run_id']}/report.md")    # m1
    dead = [f for f in doc["findings"] if f["rule"] == "usage-graph/dead.file"]
    assert [f["anchor"] for f in dead] == ["file:orphan.py"]
    assert doc["delta"]["statuses"][dead[0]["id"]] == "persisting"
    run = doc["run"]
    assert run["manifest"]["entries_read"] == 1 and run["host"]
    assert run["scope"] == ["devtools"]
    assert run["config_sha1"] == hashlib.sha1(b"").hexdigest()
    assert len(run["repos"]["devtools"]["head"]) == 40
    assert run["repos"]["devtools"]["dirty"] is False
    assert run["surface"]["plists"] == ["dev.atp.live.plist"]
    live = doc["graph"]["devtools"]["file:live.py"]
    assert live["class"] == "live"
    assert {e["kind"] for e in live["edges"]} == {"make", "sched"}
    assert all(p["rules"] for p in doc["probes"] if p["status"] == "ok")
    assert not (ws / "out" / run["run_id"] / "src" / "devtools").exists()
    md = (ws / "out" / run["run_id"] / "report.md").read_text()
    assert "| usage-graph | devtools | ok |" in md


BOOM = ProbeSpec(name="boom", languages=frozenset({"any"}), input_mode="files",
                 select=lambda t: t.corpus,
                 canary=Canary(".selfcheck-canary/boom/c.py", "x = 1\n", "boom/X", "file:x"),
                 analyze=lambda ctx: 1 / 0, logic_version=1, rules=("boom/X",))


def test_failed_probe_is_a_finding_and_run_continues(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    assert main(args(ws), registry=(BOOM, USAGE_GRAPH)) == 2
    (doc,) = reports(ws)
    assert {p["probe"]: p["status"] for p in doc["probes"]} == {"boom": "failed",
                                                              "usage-graph": "ok"}
    assert any(f["rule"] == "selfcheck/probe-failed" and f["severity"] == "high"
               for f in doc["findings"])
    assert not (ws / "out" / doc["run"]["run_id"] / "src" / "devtools").exists()


def test_relative_out_from_make_style_cwd(tmp_path: Path, monkeypatch) -> None:
    require_tool("ruff")
    ws = workspace(tmp_path, {"a.py": "import os\n"})
    monkeypatch.chdir(ws / "devtools")
    code = main(["--workspace", "..", "--manifest", "../m.toml", "--config", "none.toml"],
                registry=(RUFF,))
    (doc,) = [json.loads(p.read_text()) for p in (ws / "devtools" / "out" / "selfcheck").glob(
        "*/report.json")]
    assert code == 0 and doc["probes"][0]["status"] == "ok"
    assert any(f["rule"] == "ruff/F401" for f in doc["findings"])


def test_bad_config_and_unknown_repo_exit_4(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "bad.toml").write_text("[[allow]]\nanchor = 'x'\n")
    bad = args(ws)
    bad[bad.index("--config") + 1] = str(ws / "bad.toml")
    assert main(bad) == 4
    assert main(args(ws, "--repo", "nope")) == 4


def test_full_registry_leaves_source_untouched(tmp_path: Path, monkeypatch) -> None:
    for spec in REGISTRY:
        if spec.name == "jscpd":
            require_npx_package("jscpd@4.3.0")
        elif spec.binary:
            require_tool(spec.binary)
    ws = workspace(tmp_path, {"a.py": "import os\n", "run.sh": "#!/bin/sh\necho $1\n"})
    repo = ws / "devtools"
    before = snapshot_hashes(repo)
    before.pop(".git/index", None)
    index_mtime = (repo / ".git" / "index").stat().st_mtime_ns
    monkeypatch.chdir(repo)
    code = main(["--workspace", "..", "--manifest", "../m.toml", "--config", "none.toml"])
    after = {k: v for k, v in snapshot_hashes(repo).items()
             if not k.startswith("out/") and k != ".git/index"}
    assert code in (0, 3)
    assert after == before
    assert (repo / ".git" / "index").stat().st_mtime_ns == index_mtime


def test_repo_missing_is_finding_and_exit_2(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    (ws / "m.toml").write_text('[tools.devtools]\ngit_dir = "devtools"\n'
                               '[apps.ghost]\ngit_dir = "ghost"\n')
    assert main(args(ws, "--repo", "devtools", "--repo", "ghost",
                     "--probe", "usage-graph")) == 2
    (doc,) = reports(ws)
    assert any(f["rule"] == "selfcheck/repo-missing" for f in doc["findings"])


def test_report_write_failure_exit_4(tmp_path: Path, monkeypatch) -> None:
    ws = workspace(tmp_path)

    def fail(run_dir, doc):
        raise OSError("disk full")

    monkeypatch.setattr(run_module, "write_report", fail)
    assert main(args(ws, "--probe", "usage-graph")) == 4


def test_cleanup_failure_is_a_warning(tmp_path: Path, monkeypatch) -> None:
    ws = workspace(tmp_path)
    monkeypatch.setattr(run_module, "release", lambda dest: f"cleanup failed: {dest}")
    assert main(args(ws, "--probe", "usage-graph")) == 0
    (doc,) = reports(ws)
    assert any(w.startswith("cleanup failed") for w in doc["run"]["warnings"])
```

**Эскиз.** `main`: разбор → резолв путей → конфиг и манифест (ошибка → 4) →
`new_run_dir` → по репо: корпус (`--path` фильтрует), окружение,
`repo_state`, материализация, пробы (каждая изолирована), `finally: release`
→ находки прибора → env-политика → allowlist → снимок → базовый прогон
(`find_baseline` по времени записи `report.json`) → дельта → отчёт (ошибка
записи → 4) → печать пути `report.md` → `exit_code`. `host` —
`socket.gethostname()`.

- [ ] **Step 1:** red → реализация → green.
- [ ] **Step 2:** `selfcheck.toml`:

```toml
# selfcheck — allowlist и роли devtools (спека §1.4, §2.4).
[[allow]]
anchor = "file:issue_console.py"
reason = "неприкасаем по решению владельца: новый TUI — новый файл"
until = 2027-03-31
```

- [ ] **Step 3:** `Makefile` — в `.PHONY` добавить `selfcheck selfcheck-dogfood`;
  в `help` после строки `make edge-check`:

```make
	@echo "  make selfcheck ARGS='[--repo r] [--sched-dir ~/Library/LaunchAgents]' — самодиагностика: баги, мёртвое, дубли, LLM-вызовы (отчёт в out/selfcheck/)"
	@echo "  make selfcheck-dogfood — тесты selfcheck с обязательными инструментами + прогон на собственном пакете"
```

  цели:

```make
selfcheck: ; @uv run --frozen --group selfcheck python -m selfcheck --workspace $(WORKSPACE) --manifest $(MANIFEST) $(ARGS)
selfcheck-dogfood: ; @SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q && uv run --frozen --group selfcheck python -m selfcheck --workspace $(WORKSPACE) --manifest $(MANIFEST) --repo devtools --path 'selfcheck/**' --path 'tests/selfcheck/**' --path Makefile --path pyproject.toml
```

- [ ] **Step 4:** CI (`.github/workflows/ci.yml`, после `make plan-check-selftest`):

```yaml
      - run: uv sync --frozen --group selfcheck
      - run: SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q
```

  проверить `actionlint` и `zizmor --offline` на `ci.yml` — без новых находок.
- [ ] **Step 5:** `CLAUDE.md`, таблица «Инструменты», строка после `edge_check.py`:

```markdown
| `selfcheck/` | самодиагностика (`make selfcheck`): статические пробы (ruff, pyrefly, vulture, deptry, radon, shellcheck, actionlint, zizmor, jscpd, semgrep) + собственные (граф использования, ast-дубли, cli-overlap, LLM-вызовы); отчёт `out/selfcheck/<run_id>/report.{json,md}`, только советует. Спека `docs/superpowers/specs/2026-09-25-selfcheck-design.md`; пробы, исполняющие код цели, — отдельная спека S4 |
```

- [ ] **Step 6:** полный прогон:

```bash
uv run --frozen pytest -q
SELFCHECK_REQUIRE_TOOLS=1 uv run --frozen --group selfcheck pytest tests/selfcheck -q
uv run --frozen --group selfcheck ruff format --check selfcheck tests/selfcheck
uv run --frozen --group selfcheck ruff check selfcheck tests/selfcheck
uv run --frozen --group selfcheck pyrefly check selfcheck
```

- [ ] **Step 7:** приёмка S1 на devtools (§7):

```bash
REPORT_MD=$(uv run --frozen --group selfcheck python -m selfcheck --workspace .. \
  --manifest ../ai-orchestrators-workspace/workspace-manifest.toml \
  --sched-dir ~/Library/LaunchAgents); echo "exit=$?"
python3 - "${REPORT_MD%.md}.json" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))
dead = {f["anchor"] for f in doc["findings"] if f["rule"].startswith("usage-graph/dead")}
must_live = {"file:issue_worker.py", "file:deploy/r16/setup.sh"}
bad = sorted(a for a in dead if a in must_live or a.startswith("file:scripts/review/"))
print("probes:", {f'{p["probe"]}': p["status"] for p in doc["probes"]})
print("dead:", len(dead), "false-dead on required nodes:", bad)
assert not bad, bad
EOF
```

  Ожидание: скрипт без `AssertionError`; `exit` 0 или 2/3 с причиной из
  таблицы проб в описании PR (`failed` по вине самого selfcheck чинится до
  PR). В S1 `confirmed` dead нет (P1); все `likely` dead просмотреть руками и
  записать в PR: сколько настоящих, сколько ложных и почему (вход для S2).
- [ ] **Step 8:** `make selfcheck-dogfood`; коммит
  `feat(selfcheck): оркестратор, отчёт, make, CI; приёмка S1`; в `TODO.md`
  отметить `@id:selfcheck-s1` выполненным.
