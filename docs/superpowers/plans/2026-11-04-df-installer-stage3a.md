# Установщик `df` (команда `darkfactory`), этап 3a — доверие и предпроверка candidate/finalize: план реализации (devtools)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** установка получает корень доверия `df-trust` (`setup-trust`, привязка, профиль токена
истории), издатель держит состояние доверия по инварианту И и метаинварианту К и мержит
candidate и finalize через предпроверку протокола v2 — с живой приёмкой на стенде.

**Architecture:** журнал эпох и историю источников понимает только `governance/act_trust` в
роли `df-precheck` (без состояния, чтение по SHA); издатель (stdlib) — единственный владелец
состояния доверия в `events.sqlite` (`trust_heads`, `trust_observations`, события) и сверяет
вердикты сам. Операции v2 — `op: trust` и `op: merge`; v1 (stop) не меняется. `gh` и `git`
запускаются по утверждённым путям с разрешительным окружением.

**Tech Stack:** Python ≥ 3.12; `df/` — только stdlib; `governance/act_*` — steward `68f46a9` и
pyyaml 6.0.3 в чистом Python; SQLite (stdlib); системные `sudo` (классический 1.9), `git`,
`openssl`; `gh` 2.102.1 (утверждённый CLI, O12). Тесты: pytest, ruff, pyrefly.

**Spec:** `docs/superpowers/specs/2026-10-13-df-installer-design.md` **rev 11** (HEAD этой ветки,
коммит `7706fec`); этап 3a — §2.5 строка 3a, §10 строка 3a; нормативные разделы — §3.9, §3.10,
§4.1, §4.5, §5.2, §5.4, §5.5, §6.1, §6.2, §8. Спека A — rev 18. Решения владельца O1–O14 приняты
04.11 (§10). План спорит со спекой, а не заменяет её; нового решения план не вводит —
неразрешённое спекой вынесено в «Вопросы владельцу».

## Global Constraints

- Пакет `df/` — только stdlib (`tests/df/test_launcher_entry.py:340` проверяет только
  верхнеуровневые импорты входа; замыкание издателя — `test_publisher_closure_stdlib`, Task 9);
  издатель и root YAML не разбирают (§3.9, §6.1).
- Код с YAML исполняет только `df-precheck` (в 3a процессы человека с `site/` не нужны — §3.9,
  исключение rev 11 относится к 3b).
- Замыкание `act_precheck`: `governance/act_*`, `governance/facts`, `governance/ssot_env`,
  `governance/approval_branches`, `governance/bundle_dag`, `steward`, `yaml`, stdlib. Ни `df`, ни
  `governance/ops`, `runner`, `act_status`, `act_node`, `edge_check`, ни `jwt` (§5.4, R9).
- Перечень дерева предпроверки — данные `df/precheck_tree.toml`; модули 2b и
  `contracts/approval-branches/v1/patterns.env` не удаляются и не переименовываются (§3.9).
- Протокол v1 (`df/precheck-request/v1`, `df/precheck-verdict/v1`, `kind ∈ ("stop",)`) не
  меняется; v2 — схемы `df/precheck-request/v2`, `df/precheck-verdict/v2` (§5.4).
- Запрос и вердикт ≤ 64 КиБ; вердикт v2 ≤ **32 КиБ** сериализованно; деталь находки ≤ 512 байт
  UTF-8; дескриптор диапазонов ≤ **64** эпох; закрытый перечень 12 кодов, ≤ 1 на (вид, роль)
  (§6.1 «Размер вердикта»).
- `gh` и `git` — только по путям `install.toml`, без отката к `PATH`; окружение `gh auth
  token` — ровно `PATH=/usr/bin:/bin`, `HOME` (отдельный каталог вызова), `LANG=C`,
  `GH_CONFIG_DIR`, `GH_NO_UPDATE_NOTIFIER=1` (§3.10).
- Профиль токена истории — `/etc/df/history-gh`, `root:df-history`, `0750`/`0640`; группа
  `df-history` = `df-publisher`, `df-precheck`, оператор (§4.1).
- Сроки рубежей до замера — значения 2b (`DEADLINE = 150.0`, `command_timeout=180`,
  `PRECHECK_WAIT = 200.0`, `REAP_AFTER = 230`); изменяются только задачей 14 по замеру (§5.4).
- Ответ издателя — `df/publisher-response/v1` без новых верхних ключей.
- `_cowork_output/` не читается. Разговор по-русски, код по-английски.
- Тесты не трогают `~/.claude`, `~/.codex`, `~/.config/gh`, связку ключей и настоящие
  credentials; сеть — только локальные фейки (кроме живых строк); root-строки — только CI.
- Тесты на переднем плане:
  `perl -e 'alarm 590; exec @ARGV' -- uv run --group governance pytest <path> -q -p no:cacheprovider`.
- Все файлы плана, кроме `governance/edge_check/publish.py`, — authority-root (`df/`,
  `tests/df/`, `governance/act_*`, `contracts/`, `.github/`). PR задач сливает **владелец**.

## Review Focus

1. **Ответ проверяющего потерян в середине перехода головы `df-trust`.** Ожидание: наблюдение
   остаётся незавершённым, корень не `clear`, повтор на тех же входах даёт тот же исход; новой
   эпохи событию не назначается. Тест — Task 8, `test_lost_answer_reevaluated_same_inputs`.
2. **Два издателя одновременно продвигают голову одного корня.** Ожидание: CAS принимает одну
   голову, второе наблюдение «вытеснено», находки обоих зарегистрированы. Тест — Task 8,
   `test_concurrent_advance_one_wins_findings_kept` (первый вызов остановлен перед транзакцией).
3. **Предпроверка мержа зависла или вернула мусор.** Ожидание: мержа нет, корень `unavailable`
   до повторной оценки, **не** консервативное событие до следующей эпохи. Тест — Task 11,
   `test_precheck_timeout_no_root_stop`.
4. **Хост 2b ставит пин 3a своим установщиком.** Ожидание: stop работает, candidate/finalize —
   отказ по имени до импорта, `doctor` называет переустановку. Тест — Task 1,
   `test_old_installer_tree_v1_served_v2_refused_by_name`; root — Task 14.
5. **Оператор запускает `gh` со своим настоящим `HOME`.** Ожидание: путь не допускается —
   отдельный `HOME` вызова, пустой профиль даёт отказ, а не токен из связки ключей. Тест —
   Task 2, `test_gh_env_allowlist_and_call_home` и root-строка devtools#693 (macOS).

---

## Файлы

| Файл | Роль | Task |
|---|---|---|
| `df/precheck_tree.toml` (новый), `df/admin_tree.py`, `df/admin.py`, `df/cli.py` (`precheck serve`), `governance/act_precheck.py` (`serve`, диспетчер) | перечень дерева, проверка полноты, минимальный диспетчер v2 | 1 |
| `df/cli.py`, `df/config.py`, `governance/act_trust.py` (`history_token_at`), `governance/act_history_snapshot.py`, `governance/act_precheck.py`, `tests/df/test_root_gh_shared_profile.py` (из devtools#693) | договор запуска | 2 |
| `governance/act_surface.py` (новый), `governance/edge_check/publish.py`, `df/stage2_config.py` (`Product`) | поверхность D16, запись продукта | 3 |
| `governance/act_trust_types.py` (новый), `df/publisher/trust_types.py` (новый) | общие типы доверия | 4 |
| `governance/act_trust_pair.py` (новый), `governance/act_trust.py` | чтение пары по SHA, отношение, инкрементальность | 5 |
| `governance/act_facts.py`, `governance/act_trust_findings.py` (новый) | история политики до P, дескрипторы, бюджет | 6 |
| `governance/act_precheck.py`, `governance/act_premerge.py`, `governance/act_app_installations.py` (новый) | программа v2 | 7 |
| `df/publisher/trust_state.py` (новый), `df/publisher/perimeter.py`, `df/cli.py` (`perimeter-init`) | состояние доверия, переход схемы | 8 |
| `df/publisher/history.py` (новый) | токен истории и самоотчёт издателя | 9 |
| `df/publisher/perimeter.py`, `df/publisher/trust_routes.py` (новый), `df/publisher/ops.py`, `df/publisher/main.py`, `df/publisher/request.py` | маршруты периметра, `trust-status`, сверка диапазонов | 10 |
| `df/publisher/merge.py`, `df/publisher/precheck.py` | мерж candidate/finalize | 11 |
| `df/setup_trust.py` (новый), `df/cli.py`, `df/launcher_entry.py`, `df/roles.py`, `df/roles_setup.py`, `df/platform.py` | `setup-trust`, группа `df-history` | 12 |
| `df/doctor.py`, `df/sudoers.py` | `doctor` уровня 3, строка оператора | 13 |
| `tests/df/test_acceptance_root_trust.py` (новый), `tests/df/roles_world.py`, `.github/workflows/df-stage1.yml` | root-строки | 14 |
| `tests/df/live_trust_stand.py` (новый), `.github/workflows/df-stage3-live.yml` (новый) | живые строки | 15, 17 |
| `governance/act_precheck.py`, `df/publisher/precheck.py`, `df/precheck_reap.py`, `df/sudoers.py`, спека §4.1 | сроки v2 | 16 |
| evidence, `TODO.md` | учёт | 17 |

---

Правило всех задач: изменение сигнатуры переносится **вместе со всеми вызывающими местами**
в той же задаче; задача, добавляющая импорт, достижимый из `act_precheck`, в том же коммите
обновляет `df/precheck_tree.toml` (сторож — `test_list_matches_ast_closure`, Task 1); после
каждой задачи stop v1 обслуживается, а установка без `gh` и без привязки работает как в 2b
(сторож — `test_no_gh_no_binding_stop_served`, Task 2).

### Task 1: перечень дерева в `df/`, проверка полноты до импорта, минимальный диспетчер v2

Первой: роста замыкания в ней нет, установщик 2b строит для неё полное дерево (§3.9, «Путь
обновления»).

**Files:** создать `df/precheck_tree.toml`; изменить `df/admin_tree.py:54-71,121-150`,
`df/admin.py:405-500`, `df/cli.py:515-545`, `governance/act_precheck.py:190-260` (`serve`,
разбор `schema`); тесты `tests/df/test_admin_tree.py`, `tests/df/test_cli.py`,
`tests/test_act_precheck.py`.

**Interfaces:**
- Produces:
  ```python
  # df/precheck_tree.toml: schema = "df/precheck-tree/v1"; modules = [...]; contract_files = [...]
  @dataclass(frozen=True)
  class TreeList:
      modules: tuple[str, ...]
      contract_files: tuple[str, ...]
  LEGACY_2B: TreeList            # нынешние GOVERNANCE_MODULES/CONTRACT_FILES 2b
  def load_tree_list(devtools_ref: Path) -> TreeList   # tomllib из пина; нет файла — LEGACY_2B
  def closure_problems(devtools_ref: Path, listing: TreeList) -> list[str]
      # AST-замыкание пиненого act_precheck.py (stdlib ast, без исполнения) ⊆ listing.modules
  def tree_problems(tree: Path, listing: TreeList) -> list[str]   # недостающие файлы
  # governance/act_precheck.py
  def serve(stdin, stdout, *, admin_digest: str, home: Path, complete_v2: bool,
            deps: PrecheckDeps | None = None) -> int
  ```
  `install_admin` отказывает (`TreeError`), если `closure_problems` не пуст. Диспетчер по
  `schema`: v1 — как в 2b; `df/precheck-request/v2` — типизированный отказ `tree_incomplete`
  при `complete_v2=False`, иначе `v2_unavailable` до Task 7. `precheck serve` и все вызовы
  `serve` в тестах передают `complete_v2`.
- [ ] **Step 1: Тесты:** `test_tree_list_read_from_pin_as_data`; `test_legacy_pin_falls_back_to_2b_list`;
  `test_list_matches_ast_closure`; `test_installer_refuses_pin_with_unlisted_import`
  (искусственный пин: `act_precheck.py` импортирует модуль, которого нет в перечне);
  `test_2b_modules_retained`; `test_old_installer_tree_v1_served_v2_refused_by_name`
  (дерево по `LEGACY_2B`: v1 обслужен, v2 — `tree_incomplete`, без `ModuleNotFoundError` в
  stderr); `test_v2_unavailable_until_task7`; `test_incomplete_tree_doctor_names_reinstall`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ `tests/df/test_admin*.py`, `test_cli.py`,
  `tests/test_act_precheck.py`). **Step 5:** коммит `feat(df): precheck tree list as data inside df/`.

### Task 2: договор запуска `gh` и `git`

**Files:** `df/cli.py:91-92,880-915` (путь записи `gh` в `install.toml` — по ответу на вопрос
6), `df/config.py:44` (`REQUIRED_CLI` не меняется), `df/cli.py` (`precheck serve` передаёт
пути), `governance/act_trust.py:411-466`, `governance/act_history_snapshot.py:108-125`,
`governance/act_precheck.py:372-384` (вызов `open_snapshot`); тесты `tests/df/test_cli_set.py`,
`tests/test_act_trust.py`, `tests/test_act_history_snapshot.py`, `tests/test_act_precheck.py`;
перенести `tests/df/test_root_gh_shared_profile.py` из draft devtools#693 и вернуть
`df-stage1.yml` к `full`/пустому `-k`.

**Interfaces:**
- Produces:
  ```python
  GH_PINNED_VERSION = "2.102.1"
  GH_ENV_KEYS = ("PATH", "HOME", "LANG", "GH_CONFIG_DIR", "GH_NO_UPDATE_NOTIFIER")
  def history_token_at(config_dir: Path, *, gh: Path, call_home: Path) -> str | Unavailable
  # history_transport(binding, *, open_url=None, token_source=None) — без изменений:
  # df передаёт token_source=partial(history_token_at, gh=..., call_home=...);
  # прежний history_token(config_dir) остаётся для развёртывания флота (б) — вне договора df
  def open_snapshot(home, full_name, refs, token, *, git: Path, git_base: str,
                    deadline: float) -> Path
  def serve(..., complete_v2: bool, gh: Path | None, git: Path, deps=None) -> int
  ```
  `gh`/`git` программе даёт `precheck serve` из `install.toml` своего снимка (не запрос);
  `gh is None` — профиль истории недоступен (v2 `op: trust`/`merge` с ключом — отказ). Все
  вызовы `open_snapshot` и `serve` обновлены в этой задаче. `call_home` — временный каталог
  вызова в домашнем каталоге роли, удаляется после вызова.
- [ ] **Step 1: Тесты:** `test_gh_registration_requires_pinned_version`;
  `test_gh_registration_refuses_untrusted_path`; `test_gh_env_allowlist_and_call_home`
  (`GH_TOKEN`, `GITHUB_TOKEN` процесса не доходят); `test_missing_gh_refused_no_path_fallback`;
  `test_git_absolute_path_only`; `test_no_gh_no_binding_stop_served` (установка без `gh` и без
  `trust.toml` — stop через `serve` как в 2b).
- [ ] **Step 2–4:** FAIL → реализовать → PASS. Root-строка `test_gh_shared_profile`
  (devtools#693) зелёная в `df-stage1` полного объёма на обеих ОС — приёмка задачи.
- [ ] **Step 5:** коммит `feat(df): gh/git launch contract`; закрыть devtools#693 со ссылкой.

### Task 3: узкая поверхность D16 и запись продукта

**Files:** создать `governance/act_surface.py`; изменить `governance/edge_check/publish.py:61-95`,
`df/stage2_config.py:200-260` (`Product`); тесты `tests/test_act_surface.py`,
`tests/test_edge_check_publish.py` (новый), `tests/df/test_stage2_config.py`.

**Interfaces:**
- Produces:
  ```python
  # governance/act_surface.py (замыкание: act_request, bundle_dag)
  def surface_violations(paths: Sequence[str], *, ws_id: str, request_key: str,
                         nodes: Sequence[str], bundle_dir: str,
                         dag: tuple[tuple[str, tuple[str, ...]], ...],
                         act: str | None) -> list[str]
  EVIDENCE_RE: re.Pattern[str]   # workstreams/<ws>/evidence/edge-check/[^/]+\.json
  # df/stage2_config.Product
  bundle_dir: str = "workstreams/{ws}/spec"
  dag_variant: Literal["full"] = "full"          # legacy-варианты в v1 — отказ разбора
  ```
  `edge_check/publish.surface_violations(state, ops, dag, op, key)` вызывает общую функцию
  (O6). В замыкание `act_precheck` модуль входит в Task 7 (там же — перечень).
- [ ] **Step 1: Тесты:** `test_surface_full_pr_foreign_file_in_early_commit_refused`;
  `test_surface_manifest_included`; `test_surface_w1_discovery_allowed`;
  `test_surface_stale_downstream_allowed`; `test_evidence_only_json`;
  `test_pipeline_and_precheck_same_surface` (O6, парный); `test_surface_closure_is_narrow`;
  `test_product_legacy_dag_variant_refused`; `test_product_default_bundle_dir`;
  `test_products_toml_2b_rows_still_parse` (строки 2b без новых полей — умолчания).
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ `tests/test_edge_check*.py`).
- [ ] **Step 5:** коммит `refactor(act): shared D16 surface; product bundle source`.

### Task 4: общие типы доверия

**Files:** создать `governance/act_trust_types.py` (stdlib, без `yaml`/`steward`) и его зеркало
для издателя `df/publisher/trust_types.py` (те же имена, stdlib); тесты
`tests/test_act_trust_types.py`.

**Interfaces:**
- Produces (обе копии; тест сверяет совпадение перечней):
  ```python
  Role = Literal["approval-policy", "df-trust"]
  Route = Literal["perimeter-status", "trust-status", "merge"]
  KINDS: Mapping[str, frozenset[Role]]          # 12 кодов §6.1
  ROUTE_KINDS: Mapping[Route, frozenset[str]]
  @dataclass(frozen=True)
  class Finding: kind: str; role: Role; detail: str          # detail ≤ 512 байт
  @dataclass(frozen=True)
  class AcceptedBase: sha: str; last_epoch: int; source_digest: str; validation: str
  @dataclass(frozen=True)
  class PolicyPin: full_name: str; branch: str; p_sha: str
  @dataclass(frozen=True)
  class RangeDescriptor: ranges: tuple[tuple[int, str, str], ...]; digest: str   # ≤ 64
  @dataclass(frozen=True)
  class SourceDescriptor: digest: str; policy_full_name: str; policy_branch: str
                          bootstrap_activation: str; admins_count: int; admins_digest: str
  ```
- [ ] **Step 1: Тесты:** `test_kinds_closed_list_12_codes_16_combinations`;
  `test_route_kinds`; `test_detail_byte_limit`; `test_mirror_equal_to_governance`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит `feat(act): shared trust types`.

### Task 5: `act_trust` по SHA — пара `df-trust`, отношение, инкрементальность от A

**Files:** создать `governance/act_trust_pair.py`; изменить `governance/act_trust.py:298-410,601-633`,
`df/precheck_tree.toml`; тесты `tests/test_act_trust_pair.py`, `tests/test_act_trust.py`.

**Interfaces:**
- Consumes: Tasks 2 (`history_token_at` через `token_source`), 4.
- Produces:
  ```python
  @dataclass(frozen=True)
  class PairResult:
      relation: Literal["descendant", "not_descendant", "first"]
      snapshot: TrustSnapshot | None        # None — журнал H не проверен
      source: SourceDescriptor
      findings: tuple[Finding, ...]
  def read_pair(transport, binding: Binding, base: AcceptedBase | None, head: str,
                *, validation: str) -> PairResult | Unavailable
  def settings_findings(transport, snap: TrustSnapshot, *,
                        policy_branch: str) -> tuple[Finding, ...]
  ```
  Чтение только по SHA (сейчас ветка, `:375-383`). Холодный путь — `base is None`, иная
  `validation`, иной digest `source.env` (`_checked_epochs`, `:346-364`). `settings_findings`
  явно вызывает `_history_self_report` (`:571`), `_rulesets` (`:601`), `_rebase` (`:616`),
  проверку `allow_rebase_merge` у `df-trust` и сверку ветки `source.env` с переданной
  default branch (`policy_branch_mismatch`).
- [ ] **Step 1: Тесты** (replay GitHub): `test_pair_descendant_incremental_from_A`;
  `test_pair_rollback_is_not_descendant`; `test_pair_other_validated_branch_not_descendant`;
  `test_pair_reads_by_sha_not_branch`; `test_changed_bootstrap_warm_equals_cold`;
  `test_validation_change_goes_cold`; `test_unreadable_head_objects_history_unverifiable`;
  `test_df_trust_rebase_allowed_finding`; `test_settings_checks_called_explicitly`;
  `test_runtime_source_branch_not_default_finding`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(act): trust pair read by SHA with incremental base`.

### Task 6: история политики до P, дескрипторы, бюджет вердикта

**Files:** `governance/act_facts.py:166-230`, создать `governance/act_trust_findings.py`;
тесты `tests/test_act_facts.py`, `tests/test_act_trust_findings.py`.

**Interfaces:**
- Consumes: Task 4.
- Produces:
  ```python
  def history(transport, source: SourceV2, anchor: str, until: str | None,
              *, head: str | None = None) -> ...   # head — закреплённый P открытой эпохи
  def range_descriptor(epochs, p_sha: str) -> RangeDescriptor     # > 64 — UnsupportedSize
  def source_descriptor(source: SourceV2, raw: bytes) -> SourceDescriptor
  def fit_verdict(envelope: Mapping[str, object], findings, diagnostics) -> bytes  # ≤ 32 КиБ
  class UnsupportedSize(Exception): field: str
  ```
  Прежние вызовы `history(...)` без `head` — поведение 2b.
- [ ] **Step 1: Тесты:** `test_policy_history_until_pinned_p`; `test_policy_rollback_after_p_same_break`;
  `test_range_descriptor_closed_and_open`; `test_65_epochs_unsupported_size`;
  `test_policy_admins_4000_compact`; `test_64_epochs_all_16_combinations_max_details_fit_32k`
  (один вердикт: 64 эпохи и все 16 сочетаний с деталями предельной длины, многобайтовый
  UTF-8); `test_codes_never_truncated`; `test_kind_outside_route_rejected`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(act): pinned policy history, trust descriptors, bounded verdict`.

### Task 7: программа проверяющего v2 — `op: trust`, `op: merge`

**Files:** `governance/act_precheck.py` (замена `v2_unavailable` программой), `governance/act_premerge.py`,
создать `governance/act_app_installations.py` (`app_installations` из `act_premerge.py:274-290`),
`df/precheck_tree.toml`; тесты `tests/test_act_precheck.py`, `tests/test_act_precheck_v2.py`,
`tests/test_act_premerge.py`.

**Interfaces:**
- Consumes: Tasks 1–6; `act_context.delivery_verdict` (`governance/act_context.py:26`).
- Produces (договор Tasks 8–11):
  - запрос `df/precheck-request/v2`, общие ключи: `schema, nonce, op ∈ {trust, merge},
    origin_route, ctx_id, binding {host, df_trust_repository_id, df_trust_full_name, branch,
    approval_policy_repository_id}, policy (PolicyPin) | null`;
    - `op: trust`: `trust_accepted (AcceptedBase | null), trust_head` (H);
    - `op: merge`: `expected_snapshot_sha` (X), `trust_accepted (AcceptedBase с sha = X)`,
      `preliminary_state = "clear"`, `kind ∈ {candidate, finalize}, repository_id, full_name,
      pr, head_sha` (**C — голова продуктового PR**), `head_ref, base_ref, base_tip, changes,
      token, merger_login, merger_type, bundle_dir, dag_variant, api_base, git_base`;
  - вердикт `df/precheck-verdict/v2`: **обязательная связка** `schema, nonce, request_digest,
    ctx_id, op, root {host, df_trust_repository_id, branch}`; для `trust` — `snapshot_sha` (=
    H), `accepted_sha`; для `merge` — `repository_id, pr, head_sha` (= C), `kind`,
    **`snapshot_sha`** (снимок, на котором построен вердикт; спека §5.4 п. 3, §5.4 «Протокол
    v2») и эхо `expected_snapshot_sha`; межполевая сверка издателем: `snapshot_sha` вердикта =
    `expected_snapshot_sha` запроса = X (Task 11, `_bound`); затем `outcome ∈ {allow, refuse, unset, tree_incomplete,
    unsupported_size}, reason, relation, last_epoch | null, source (SourceDescriptor),
    ranges (RangeDescriptor) | null, policy (эхо PolicyPin) | null, findings[], diagnostics,
    version = "df-precheck/2", admin_digest` (≤ 32 КиБ — Task 6).
  `op: merge`: `load_trust` с журналом-адаптером — `status(snap)` = `preliminary_state`, только
  если `snap.root.sha == expected_snapshot_sha`, иначе `Unavailable(stale_snapshot)`; `record`
  складывает находки; производный токен — режим «покрыт держателем ключа» (N3); candidate —
  `act_premerge` с мержером из запроса (N4), поверхность — `act_surface`; finalize —
  `act_context.delivery_verdict(..., legacy=None)` (N7). Допустимые находки —
  `ROUTE_KINDS[origin_route]`.
- [ ] **Step 1: Тесты:** `test_v1_stop_unchanged`; `test_dispatch_by_schema`;
  `test_trust_op_writes_nothing`; `test_candidate_allowed_with_act_on_final_head`;
  `test_approve_before_evidence_not_counted`; `test_candidate_foreign_file_refused`;
  `test_finalize_review_id_from_delivery`; `test_finalize_wrong_envelopes_refused`;
  `test_merger_substitution_changes_digest_and_refuses`; `test_stale_snapshot_adapter`;
  `test_derived_credential_mode_not_empty_creds`; `test_reeval_merge_returns_policy_break`;
  `test_binding_echoes_c_x_pr_kind_root` (связка вердикта несёт C, X, PR, kind, корень,
  `snapshot_sha` = X);
  `test_closure_excludes_jwt_ops_df` (AST, включая отложенные импорты).
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(act): precheck protocol v2 (trust, candidate, finalize)`.

### Task 8: состояние доверия издателя и переход схемы `events.sqlite`

**Files:** создать `df/publisher/trust_state.py`; изменить `df/publisher/perimeter.py` (`SCHEMA`,
`SCHEMA_VERSION:86`, `_verify:500-516`, `admit:910-921`, `restore_conservative:924-948`,
`record_violation:1013-1048`, `create_db:313-346` — ранний `return "present"` заменяется
проверкой и переходом существующей базы), `df/cli.py` (`publisher perimeter-init` выполняет
переход), `df/publisher/perimeter.py:_stage:723` (восстановление пишет `restored = true` в
новую базу), `df/roles_setup.py:134-192,344-351`
(шаг `perimeter-init`: `Step.check` в `journal._execute` (`df/journal.py:499`) — и условие
пропуска, и постусловие, поэтому вместо файлового предиката — **флаг этого запуска**, как
`verified` в `_steps:146`: `checked: list[bool]`; `check = lambda: _perimeter_lost(layout) or
bool(checked)` — `_perimeter_lost` сохраняет отказ «база потеряна при маркерах»
(`perimeter.LOST_TEXT`) и иначе возвращает `False`; `do` вызывает `perimeter-init` у издателя,
и только при ответе `created | migrated | current` — `checked.append(True)`; так проверку или
переход существующей базы издатель выполняет ровно один раз за запуск, а постусловие истинно
после успеха); тесты `tests/df/test_trust_state.py`,
`tests/df/test_perimeter_migrate.py`, `tests/df/test_perimeter*.py`, `tests/df/test_roles_setup.py`.

**Interfaces:**
- Consumes: Task 4 (`df/publisher/trust_types.py`).
- Produces:
  ```python
  Outcome = Literal["open", "unresolved", "accepted", "finding_registered",
                    "superseded", "conservative"]
  def ctx_id(route: Route, binding: TrustBinding, admin_digest: str, rules: str,
             accepted: str | None, head: str, policy: PolicyPin | None) -> str
  def open_observation(db: Db, root: Root, ctx: str, ...) -> int       # дедуп по (root, ctx)
  def register_findings(db: Db, root: Root, ctx: str, findings, epochs_of: Callable) -> list[int]
  def advance_head(db: Db, root: Root, obs: int, head_read: Callable[[], str]) -> Outcome
  def conservative(db: Db, root: Root, ctx: str, obs: int) -> int        # К
  def blocked(conn, root: Root) -> bool                                  # И
  def known_roots(conn, current: Root) -> list[Root]   # events ∪ trust_heads ∪ trust_observations ∪ привязка
  def mark_restored(conn) -> None; def restored(conn) -> bool
  def migrate(layout: Layout) -> Literal["migrated", "current"]          # переход схемы
  # create_db(layout) -> Literal["created", "migrated", "current"]   (было "created" | "present")
  SCHEMA_VERSION = "2"
  ```
  **Переход схемы** `"1"` (2b) → `"2"`: только командой `publisher perimeter-init` (её вызывает
  `roles setup`); обычное открытие базы схемы `"1"` — `Unavailable("schema_migration_required:
  выполните darkfactory roles setup")`. План написан под рекомендованный вариант (а) вопроса 7
  (вопрос остаётся открытым). **Допустимая база 2b:** схема ровно `"1"` (`_expected_schema` 2b
  хранится константой), таблица `events` пуста, `trust.toml` нет. Одна транзакция `BEGIN
  IMMEDIATE`: `CREATE TABLE` `trust_heads`, `trust_observations`; `meta` += `restored`, где
  **известное восстановление не теряется**: `admission = restored_pending` в v1 (база
  `restore --empty` или из копии, `perimeter._stage:723`, `test_restore_empty_is_pending`) —
  `restored = true`; иначе — `false` (вариант (а) относится только к **неизвестной** истории,
  вопрос 7 этим не решается);
  `meta.schema = "2"`; `COMMIT` (прагмы журнала); `transitions`, `admission` не меняются.
  **Недопустимая база 2b** (схема `"1"`, но события есть или `trust.toml` есть) — отказ
  `Unavailable("migration_precondition")` **без изменений**, решает человек. История
  восстановления из текущего `admission` не выводится (`restore_conservative`,
  `perimeter.py:924`, возвращает его в `ready`). **Прерывание — два допустимых исхода:** целая
  `"1"` (процесс завершён до `COMMIT`; повтор выполняет переход) либо целая `"2"` (после
  `COMMIT`, ответ потерян; повтор возвращает `current` после **полной** проверки — `meta`,
  схема, инварианты, как `_verify`, `perimeter.py:500`). Схема `"2"` — `current`. Неизвестная
  схема — `Unavailable("integrity")`, база не трогается, решает человек (§6.1 «Восстановление
  базы»); `roles setup` передаёт отказ с причиной. **Если владелец выберет (б)**, строки «допустимая
  база мигрирует» и «недопустимая база — отказ без изменений» заменяются на «база 2b с любыми
  событиями мигрирует с `restored = true`; первый корень после `setup-trust` допускается по К». Остальное — модель §6.1: `register_findings` —
  своя транзакция без условия на голову; `advance_head` — CAS по `row_version` с чтением
  головы под блокировкой; эпоха — из проверенного текущего снимка, иначе `pending`;
  `restored` отделён от `admission`, **постоянен и никогда не возвращается в `false`**: в схеме
  `"2"` его ставит сама операция восстановления (`_stage`) при создании новой базы — до
  `admit`/`restore_conservative`, которые снимают `restored_pending`; при переходе v1 —
  в той же транзакции, что `meta.schema = "2"`, то есть до любого последующего снятия
  `restored_pending` (`restore_conservative:924`).
- [ ] **Step 1: Тесты:** `test_migrate_admissible_2b_db`;
  `test_migrate_non_admissible_2b_db_refused_unchanged` (события или `trust.toml`);
  `test_migrate_killed_before_commit_whole_v1_then_completes`;
  `test_migrate_killed_after_commit_whole_v2_rerun_current_verified`;
  `test_migrate_rerun_idempotent`; `test_unknown_schema_untouched_integrity`;
  `test_v1_db_ordinary_open_names_migration`; `test_roles_setup_rerun_with_markers_migrates_v1`;
  `test_roles_setup_completes_all_steps_fresh_v1_v2_and_rerun` (весь `roles setup` до `libexec`,
  `sudoers`, `verify` — для свежей установки, базы v1, базы v2 и повторного запуска);
  `test_roles_setup_lost_db_with_markers_refused`;
  `test_restored_empty_v1_migrates_restored_true`;
  `test_restored_empty_v1_admit_r1_then_rebind_r2_conservative` (восстановленная пустая v1 →
  переход → допуск R1 → поздний `--rebind R2` → R2 по К);
  `test_restored_flag_never_returns_false` (после `restore_conservative` и `admit`);
  `test_restore_v2_sets_restored_before_admission`;
  `test_roles_setup_unknown_schema_refusal_passed_through`;
  `test_observation_dedup_equal_ctx_only`; `test_trust_status_does_not_complete_merge_observation`;
  `test_two_p_two_events`; `test_finding_registered_without_advance`;
  `test_concurrent_advance_one_wins_findings_kept`; `test_rollback_h1_h2_h1_finding_after_restart`;
  `test_lost_answer_reevaluated_same_inputs`; `test_unresolved_until_journal_readable`;
  `test_context_change_conservative`; `test_unsupported_size_conservative`;
  `test_pinned_p_objects_missing_conservative`; `test_epoch_from_current_snapshot_e2_blocks_until_e3`;
  `test_rebind_return_known_root_uses_old_A`; `test_rebind_same_triple_cold_history_kept`;
  `test_restore_copy_without_root_then_rebind_conservative`;
  `test_restored_copy_with_open_observation_conservative_never_clear`;
  `test_known_roots_include_heads_and_observations`; `test_crash_points`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): publisher trust state and perimeter schema migration`.

### Task 9: путь токена истории самого издателя

**Files:** создать `df/publisher/history.py`; тесты `tests/df/test_publisher_history.py`,
`tests/df/test_publisher_closure.py`.

**Interfaces:**
- Consumes: Task 2 (утверждённый `gh`, `GH_ENV_KEYS`), Task 4.
- Produces (stdlib, владелец — издатель):
  ```python
  def history_token(binding: TrustBinding, *, gh: Path, call_home: Path) -> str | None | Unavailable
      # None — ключа нет (анонимно); ключ есть и токен не получен — Unavailable, без отката
  def history_transport(token: str | None) -> Transport           # stdlib urllib, без прокси и перенаправлений
  def self_report(transport, binding: TrustBinding,
                  policy_repository: int) -> Finding | Unavailable | None
      # GET /repos/{…} тем же значением токена на обоих репо: push/maintain/admin
  def read_branch_head(transport, full_name: str, branch: str) -> str | Unavailable
  ```
- [ ] **Step 1: Тесты** (модульные): `test_self_report_write_permission_finding`
  (`push`/`maintain`/`admin` = `true` — находка `token_write`); `test_self_report_permissions_missing_unavailable`;
  `test_self_report_403_404_unavailable`; `test_unreadable_profile_unavailable_no_anonymous`;
  `test_no_key_anonymous`; `test_same_token_value_for_reads_and_report`;
  `test_publisher_closure_stdlib` (AST-корни `df.publisher.main` **и** `df.publisher.history` —
  только stdlib и `df`).
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): publisher history-token transport and self-report`.

### Task 10: маршруты периметра издателя и `trust-status`

**Files:** создать `df/publisher/trust_routes.py`; изменить `df/publisher/perimeter.py`
(`perimeter_status:1081-1107`, `_check_root:1159-1167`, `EPOCHS` убрать), `df/publisher/ops.py`
(установки App на источниках доверия в `self_check_first`, `_check_app`),
`df/publisher/request.py` (операция `trust-status`), `df/publisher/main.py` (`SUDO_UID = 0` —
`self-check` и `trust-status`), `df/stage2_config.py` (`expected_publisher`); тесты
`tests/df/test_trust_routes.py`, `tests/df/test_publisher_main.py`, `tests/df/test_publisher_ops.py`.

**Interfaces:**
- Consumes: Tasks 7, 8, 9.
- Produces:
  ```python
  def perimeter_status(ctx: Context) -> Response   # быстрый путь или наблюдение + op: trust
  def trust_status_op(ctx: Context) -> Response     # всегда наблюдение + op: trust
  def pin_policy(oc: OpContext, repository_id: int) -> PolicyPin   # GET /repositories/{id} → имя, default branch → голова
  def run_trust(oc: OpContext, obs: Observation) -> Verdict | Unavailable   # v2, сверка связки и эха
  def check_ranges(desc: RangeDescriptor, *, last_epoch: int, p_sha: str,
                   stored_digest: str | None) -> bool
      # эпохи подряд 1…last_epoch; конец открытого диапазона = p_sha;
      # при повторной оценке digest = stored_digest
  ```
  Ответ `trust-status` — `df/publisher-response/v1`, `result`: `root, snapshot_sha,
  last_epoch, state, unverified, findings`. Корень прогона сверяется у всех PR-операций
  (`_check_root`). Установка App на `approval-policy`/`df-trust` — находка настроек. Ключа
  истории нет — анонимно с предупреждением (O14).
- [ ] **Step 1: Тесты:** быстрый путь `perimeter-status` (H = A) с путём Task 9:
  `test_fast_path_token_write_not_clear`, `test_fast_path_permissions_missing_unavailable`,
  `test_fast_path_403_404_unavailable`, `test_fast_path_unreadable_profile_no_anonymous`;
  `test_publisher_closure_stdlib` повторно (после подключения `history.py` к `main`);
  `test_fast_path_only_without_open_observations`;
  `test_head_moved_creates_observation`; `test_trust_status_always_calls_checker`;
  `test_trust_status_root_only`; `test_root_validates_response_schema_and_root`;
  `test_pin_policy_by_id_default_branch`; `test_trust_verdict_substituted_h_or_root_unbound`;
  `test_ranges_correct_p_other_set_unbound`; `test_ranges_reeval_digest_mismatch_unbound`;
  `test_app_on_trust_repo_finding`; `test_old_root_run_refused_pr_ops`;
  `test_no_history_key_anonymous_warning`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): perimeter routes and trust-status over trust state`.

### Task 11: мерж candidate и finalize — окончательный допуск

**Files:** `df/publisher/merge.py` (`_check_kind:444-453`, `_precheck:455-465`, `_KIND:424`,
`TRUST_ROOT:78`), `df/publisher/precheck.py` (`admin_precheck:133`, `_bound:292`); тесты
`tests/df/test_publisher_merge.py`, `tests/df/test_publisher_precheck.py`.

**Interfaces:**
- Consumes: Tasks 7–10; фазы и сверка 2b без изменений.
- Produces: `admin_precheck(kind, oc, pr, facts, *, obs: Observation) -> PrecheckResult` (v2
  для candidate/finalize с `head_sha` = C и `expected_snapshot_sha` = X; v1 для stop);
  `_bound` v2 сверяет всю обязательную связку Task 7, межполевое `snapshot_sha` вердикта =
  `expected_snapshot_sha` запроса = X и `check_ranges` (Task 10);
  `admit(db, root, obs, verdict) -> None | Refused("perimeter_blocked")` — одна транзакция:
  завершить своё наблюдение, зарегистрировать находки, заново применить И (голова в
  транзакции = X = принятой записи, нет других незавершённых наблюдений и блокирующих
  событий); после `COMMIT` — `armed` и `PUT`. Сверка `admin_digest` (`binding_mismatch`);
  `TrustRoot` — идентичность привязки для всех видов, X для candidate/finalize. Без
  привязки — `no_binding`; привязка без ключа — `history_token_required` (O14).
- [ ] **Step 1: Тесты:** `test_candidate_merged_after_admission`;
  `test_block_registered_during_precheck_refused_no_put`;
  `test_other_open_observation_positive_verdict_no_put`; `test_head_moved_from_x_before_admission_no_put`;
  `test_precheck_timeout_no_root_stop`; `test_reevaluated_merge_observation_never_puts`;
  `test_substitution_c_x_pr_kind_root_each_unbound` (пять независимых подмен);
  `test_echo_x_correct_but_snapshot_sha_missing_or_other_refused`;
  `test_ranges_correct_p_other_set_unbound_at_merge`; `test_admin_digest_mismatch_refused`;
  `test_trust_root_identity_for_stop_with_binding`; `test_finalize_attested_then_merged`;
  `test_history_token_required`; `test_stop_unchanged_v1`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): candidate and finalize merge with final admission`.

### Task 12: `darkfactory setup-trust` и группа `df-history`

**Files:** создать `df/setup_trust.py`; изменить `df/cli.py` (подкоманды `setup-trust check`
— читатель, человек; `setup-trust write` — писатель, root; обе с `--rebind`),
`df/launcher_entry.py:68-80` (`WRITER_COMMANDS += (("setup-trust", "write"),)` — кортеж
кортежей; `mode_of` `:215`), `df/cli.py:245-260` (`parsed_mode` распознаёт `setup_trust_command
== "write"`), `df/roles.py`,
`df/roles_setup.py:134-210` (`_role_problems` учитывает группу `df-history` и членство —
повторный `roles setup` на хосте 2b её создаёт), `df/platform.py` (группа, членство оператора);
тесты `tests/df/test_setup_trust.py`, `tests/df/test_roles_setup.py`, `tests/df/test_launcher_entry.py`.

**Interfaces:**
- Consumes: Task 10 (`trust-status`).
- Produces:
  ```python
  TEMPLATES: Mapping[str, str]     # source.env, epochs.yaml, policy/* — фиксированный текст
  def show_publishable(...) -> str
  def check_perimeter(pat_transport, policy_repo: int, trust_repo: int) -> PerimeterReport  # фаза чтения
  def write_binding(layout, binding: TrustBinding, token_file: Path) -> None   # фаза записи, root
  def verify_after_write(layout) -> TrustStatus | Refused
  HISTORY_GROUP = "df-history"
  ```
  Фаза чтения (`check`) — человек без root, временный PAT (§6.2), отчёт и черновик evidence;
  фаза записи (`write`) — root под исключительной блокировкой писателя, значения из
  аргументов и отчёта фазы чтения (сверка digest отчёта), профиль `/etc/df/history-gh`,
  `trust.toml`, затем `trust-status`. Стопы: тариф без rulesets, `allow_rebase_merge = true`,
  ветка не default branch (С-2), нет профиля истории (O14). Отказ `unsupported_size: epochs`
  называет выход §6.1.
- [ ] **Step 1: Тесты:** `test_templates_exact_bytes_shown`; `test_tariff_without_rulesets_stops`;
  `test_rebase_allowed_stops`; `test_policy_branch_not_default_stops`;
  `test_no_history_profile_not_finished`; `test_pat_403_is_not_verified`;
  `test_profile_written_root_group_modes`; `test_binding_from_args_parsed_strictly`;
  `test_write_refuses_report_digest_mismatch`; `test_verify_after_write_unavailable_not_finished`;
  `test_rebind_full_check`; `test_roles_setup_rerun_on_2b_creates_history_group`;
  `test_roles_setup_history_group_exact`; `test_setup_trust_check_runs_as_non_root_reader`;
  `test_setup_trust_write_requires_root_and_writer_lock`; `test_setup_trust_write_takes_writer_lock_via_entry`
  (через настоящий вход команды); `test_epochs_exhausted_text_names_exit`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит `feat(df): setup-trust`.

### Task 13: `doctor` уровня 3 (доверие) и строка оператора

**Files:** `df/doctor.py`, `df/sudoers.py`; тесты `tests/df/test_doctor.py`,
`tests/df/test_doctor_level3.py`, `tests/df/test_sudoers.py`.

**Interfaces:**
- Consumes: Tasks 10, 12.
- Produces: проверки `trust.binding`, `trust.history` (ключ, профиль, режимы, состав
  `df-history` точно), `trust.status` (через `trust-status`), `trust.unsupported_size` (выход
  §6.1), `perimeter.schema` (схема `"1"` — «нарушено: выполните roles setup»); строка
  оператора — `sudoers.operator_rights(...) -> Check`: (i) PASS/FAIL по `/etc/sudoers.d/df` и
  эффективным правилам df; (ii) для оператора — «не проверено, принято исключение».
  Побочные эффекты проб — в выводе (§8).
- [ ] **Step 1: Тесты:** `test_history_group_extra_member_broken`; `test_trust_status_schema_checked`;
  `test_operator_two_results_never_pass_ii`; `test_level3_shown_separately_from_level`;
  `test_side_effects_listed`; `test_schema_v1_names_migration`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит `feat(df): doctor level 3 (trust)`.

### Task 14: root-строки CI

**Files:** создать `tests/df/test_acceptance_root_trust.py`; изменить `tests/df/roles_world.py`,
`.github/workflows/df-stage1.yml` (job `stage2a`).

Строки (обе ОС, фейк GitHub и dumb-HTTP git, как в 2b Task 10):
- положительные candidate и finalize через всю цепочку издатель → sudo → вход → `act_precheck` v2;
- `op: trust` через цепочку; `~df-precheck` после вызова без долговечного состояния;
- профиль истории: член читает, посторонний — отказ ОС; токен истории не в argv, I/O-журнале
  sudo, evidence;
- строка оператора — (i) PASS, (ii) «не проверено, принято исключение»;
- **обновление** (вариант (а) вопроса 7): установка кодом 2b (`291cb30`) — роли, допустимая
  `events.sqlite` схемы `"1"` (событий нет, `trust.toml` нет) → установка пина этой ветки
  установщиком 2b → stop `allow`, candidate — `tree_incomplete` до импорта → `roles setup`
  кодом 3a (маркеры уже есть): переход схемы, `transitions` и `admission` сохранены, группа
  `df-history` создана → повтор `roles setup` — `current`, идемпотентно → повторная установка
  кодом 3a — новый `<id>`, candidate доходит до программы v2; отдельно — **недопустимая** база
  2b (вставлено событие от `df-publisher`): `roles setup` отказывает `migration_precondition`,
  база побайтово не изменилась. Если владелец выберет (б), эти две строки заменяются строкой
  «база 2b с событием мигрирует с `restored = true`, событие и блокировка сохранены, первый
  корень после `setup-trust` — по К»;
- установка без `gh` и без привязки — stop обслуживается;
- 65 эпох → `unsupported_size: epochs` → новый корень → `--rebind` → новый прогон (фейк GitHub).
- [ ] **Step 1:** тесты строк (`test_root_trust_*`); локально пропуск, в CI — job `stage2a`.
- [ ] **Step 2:** коммит `test(df): stage 3a root rows`.

### Task 15: живые строки доверия и замер холодного пути (первый прогон)

**Files:** создать `tests/df/live_trust_stand.py`, `.github/workflows/df-stage3-live.yml`
(только `workflow_dispatch` с `master`, environment `df-stage3-live` с одобрением владельца);
тесты `tests/df/test_live_trust_stand.py`.

Строки:
- **замер холодного пути** — ветка-замер стенда с N = 50 коммитов first-parent (5 меняют
  журнал; вопрос 3), пустые факты после обновления административного дерева (новый
  `admin_digest`), обе ОС: время `op: trust`, число запросов — в артефакт;
- bootstrap-порядок на пустых `df-roles-probe-policy`, `df-roles-probe-trust`; показ публикуемого;
- таблица запросов §6.1;
- `trust-status` с привязкой; App, установленный на репо доверия, → находка;
- переписанная история `df-trust` (возврат на прежний SHA) → находка и холодный путь;
- stop с привязкой: `trust_root` — идентичность (регресс строк 10–12 2b).
- [ ] **Step 1:** офлайн-тесты помощников (`DF_STAND_*` не читаются вне живого прогона).
- [ ] **Step 2:** workflow; **Step 3:** коммит `test(df): stage 3a live trust rows`.
- [ ] **Step 4:** владелец выполняет H1–H6 и запускает прогон; артефакт замера сохраняется.

### Task 16: сроки v2 по замеру

**Files:** `governance/act_precheck.py` (`DEADLINE` для v2), `df/publisher/precheck.py`
(`PRECHECK_WAIT`), `df/precheck_reap.py` (`REAP_AFTER`), `df/sudoers.py` (`command_timeout`),
спека §4.1, §5.4 (значения); README «Поддерживаемая длина истории».

**Interfaces:** значения — из артефакта Task 15 по правилу владельца (вопрос 1); до ответа
задача не начинается.
- [ ] **Step 1:** тест `test_v2_deadlines_from_measurement` (константы равны значениям из
  записанного замера; порядок `DEADLINE < command_timeout < PRECHECK_WAIT < REAP_AFTER`).
- [ ] **Step 2–4:** FAIL → выставить → PASS (+ root-строки рубежей 2b). **Step 5:** коммит
  `feat(df): v2 precheck deadlines from the cold-path measurement`.

### Task 17: живые строки мержа (второй прогон), evidence, учёт

**Files:** `tests/df/live_trust_stand.py` (строки мержа), `docs/evidence/2026-11-XX-df-stage3a-acceptance.md`,
`TODO.md`.

Строки: засев candidate техническим PR → **approve владельца на окончательном head** (H7,
двухфазный запуск) → мерж издателем; approve до evidence-коммита не засчитан; посторонний файл
в раннем коммите — отказ; finalize по доставленной заявке — аттестация и мерж; временный
таймаут предпроверки — повтор даёт тот же исход без остановки корня.
- [ ] **Step 1:** строки и офлайн-тесты помощников; коммит `test(df): stage 3a live merge rows`.
- [ ] **Step 2:** владелец запускает прогон; H8.
- [ ] **Step 3:** evidence: прогоны, ячейки по строкам §10 3a, исключения владельца поимённо;
  TODO: закрыть `df-installer-stage3-precheck`, `df-trust-history-token`,
  `df-precheck-admin-digest-crosscheck`, `df-precheck-git-approved-path`.
- [ ] **Step 4:** PR evidence (owner-merge).

## Шаги человека

- **H1** (предложено, ждёт подтверждения владельца — вопрос 2): очистка `df-roles-probe` —
  закрыть PR #7, удалить ветки `agent/codeowners`, `agent/drop-owners-2`, `agent/probe-1`,
  `agent/self-own`, `brief/probe-6a`, `brief/probe-6b`, поправить README («Throwaway spike»),
  заменить коллаборатора `ai-prosto` на `betta-tester`.
- **H2.** Владелец проверяет: установка App 170589886 — «Only select repositories», репо доверия
  не охвачены; App установлен на `df-roles-probe`.
- **H3.** Пустые публичные `df-roles-probe-policy`, `df-roles-probe-trust` (O8); PR-ы человека по
  шаблонам `setup-trust`, ruleset-ы, мерж человеком; `modes.yaml` — `review` для
  `df-roles-probe`, первая активация без предпросмотра по O9а (условия — в evidence).
- **H4.** Новый classic PAT `betta-tester` и новый ключ App 5285716 → секреты
  `df-stage3-live`.
- **H5.** Read-only токен истории (тип — вопрос 4) → секрет; профиль пишет root в прогоне.
- **H6.** Временный fine-grained PAT владельца, только чтение, на два репо доверия (O9б) → секрет.
- **H7.** Approve владельца на candidate-PR в каждой ячейке второго прогона.
- **H8.** После каждого прогона (в том числе аварийного): отзыв PAT, ключа, токенов; удаление
  секретов.
- **H9.** Перепин `core` в манифесте зонтика на коммит этапа 3a (TODO `df-core-repin-precheck`) —
  PR в `ai-orchestrators-workspace`, вне этого репо.

## Вопросы владельцу

1. **Правило сроков v2 по замеру.** Спека требует выставить сроки по строке-замеру, но не
   задаёт формулу (запас над измеренным максимумом). Нужна формула или значения.
2. **Очистка `df-roles-probe`** (H1) — подтвердить.
3. **Ветка-замер в `df-roles-probe-trust`** для N = 50 коммитов: создать её прямыми коммитами
   без ruleset (это замер, не приёмка защиты) — разрешить?
4. **Тип токена истории** на стенде: classic без scopes или fine-grained «public repositories,
   read-only» (спека A rev 18 допускает оба).
5. **Порядок перепина `core`** (H9) относительно живых прогонов: до прогона 1 или после
   прогона 2.
6. **Как `gh` попадает в `install.toml`:** расширить `cli set` (сейчас по решению 27.10 —
   только `openssl`) или задавать при `init`/`install`. O12 решил «утверждённый CLI по
   абсолютному пути», но не способ записи.
7. **Значение `restored` при переходе базы 2b с неизвестной историей** (Task 8). Известное
   восстановление (`admission = restored_pending`) всегда даёт `restored = true` (решение
   ведущего 04.11); вопрос — только о базе `ready`, история восстановлений которой не
   сохранена. Спека не говорит, считать ли такую базу 2b «восстановленной». Варианты: (а) `restored = false` — в базе 2b не может быть событий
   доверия (`record-violation` без привязки — `refused: no_binding`, §6.1), переход проверяет
   это предусловием (таблица `events` пуста, `trust.toml` нет; иначе — отказ перехода,
   решает человек); (б) `restored = true`, если `admission` когда-либо был
   `restored_pending` (база 2b этой истории не хранит — то есть всегда `true`), и тогда
   первый корень после `setup-trust` допускается по К (событие до следующей эпохи сразу после
   bootstrap). Рекомендация — (а).

## Вне этого плана

- 3b: `setup-repo`, `products.toml` командой, ruleset и CODEOWNERS продукта, `ACT_MODE`,
  `policy preview`, процессы человека с `site/` (§3.9), спайк форк-профиля, живая проверка
  аттестаций под D7.
- Этап 4: таблица конвейеров `df-launch run`, `verify`.
- Проверка происхождения звеньев `df-trust` (PR) — спека A, названная предпосылка.

## Самопроверка

- **Покрытие спеки:** строки §10 3a → задачи: доверие (12, 10, 15), профиль и группа (12, 14),
  договор запуска (2), предпроверка candidate/finalize (3, 6, 7, 11, 14, 17), переписанная
  история и откат (5, 8, 15), гонка и прерывания (8), `source.env` (5), `--rebind` и
  восстановление (8, 14), замер холодного пути (15 → 16), ключ истории O14 (9–12), бюджет и 65
  эпох (6, 14), обновление 2b → 3a, включая базу периметра (1, 8, 12, 14), stop с привязкой
  (11, 15). §3.9 — 1; §3.10 — 2; §4.1 — 12, 16; §4.5 — 13, 14; §5.2 — 10; §5.4 — 3, 7, 11; §5.5
  — 11; §6.1 — 4–11; §6.2 — 12; §8 — 13. Пробел — исключение §3.9 для процессов человека (3b).
- **Рабочее состояние после каждой задачи:** сигнатуры `serve` (Tasks 1, 2), `open_snapshot`
  (Task 2) и `history` (Task 6, необязательный `head`) меняются вместе с вызывающими; общие
  типы — Task 4 до их потребителей; `history_transport` не меняется (путь `gh` — через
  `token_source`); перечень дерева обновляет каждая задача, расширяющая замыкание; сторож
  `test_no_gh_no_binding_stop_served`.
- **Шаги:** каждый тестовый шаг называет тесты; значения спеки — в Global Constraints и
  Interfaces; тел функций нет.
- **Типы:** `ctx_id`, `Route`, `Outcome`, `AcceptedBase`, `PolicyPin`, `Finding`,
  `RangeDescriptor`, `ROUTE_KINDS` — Task 4, потребители 5–11; связка вердикта v2 (Task 7)
  сверяется в 10 и 11; `head_sha` = C, `expected_snapshot_sha` = X, `trust_head` = H.
- **Review Focus:** пять строк, у каждой тест в задаче-владельце (8, 8, 11, 1/14, 2).
- **Пропорция:** план короче затрагиваемых разделов спеки; кода — только сигнатуры.
