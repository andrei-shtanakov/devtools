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

- Пакет `df/` — только stdlib (проверка `tests/df/test_launcher_entry.py:340`); издатель и root
  YAML не разбирают (§3.9, §6.1).
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
   эпохи событию не назначается. Тест — Task 7, `test_lost_answer_reevaluated_same_inputs`.
2. **Два издателя одновременно продвигают голову одного корня.** Ожидание: CAS принимает одну
   голову, второе наблюдение «вытеснено», находки обоих зарегистрированы. Тест — Task 7,
   `test_concurrent_advance_one_wins_findings_kept` (первый вызов остановлен перед транзакцией).
3. **Предпроверка мержа зависла или вернула мусор.** Ожидание: мержа нет, корень `unavailable`
   до повторной оценки, **не** консервативное событие до следующей эпохи. Тест — Task 9,
   `test_precheck_timeout_no_root_stop`.
4. **Хост 2b ставит пин 3a своим установщиком.** Ожидание: stop работает, candidate/finalize —
   отказ по имени до импорта, `doctor` называет переустановку. Тест — Task 1,
   `test_old_installer_tree_v1_served_v2_refused_by_name`; root — Task 12.
5. **Оператор запускает `gh` со своим настоящим `HOME`.** Ожидание: путь не допускается —
   отдельный `HOME` вызова, пустой профиль даёт отказ, а не токен из связки ключей. Тест —
   Task 2, `test_gh_env_allowlist_and_call_home` и root-строка devtools#693 (macOS).

---

## Файлы

| Файл | Роль | Task |
|---|---|---|
| `df/precheck_tree.toml` (новый), `df/admin_tree.py`, `df/admin.py`, `df/cli.py` (`precheck serve`) | перечень дерева, проверка полноты | 1 |
| `df/cli.py` (`cli set gh`), `df/config.py`, `governance/act_trust.py` (`history_token`), `governance/act_history_snapshot.py`, `tests/df/test_root_gh_shared_profile.py` (из devtools#693) | договор запуска | 2 |
| `governance/act_surface.py` (новый), `governance/edge_check/publish.py`, `df/stage2_config.py` (`Product`) | поверхность D16, запись продукта | 3 |
| `governance/act_trust_pair.py` (новый), `governance/act_trust.py` | чтение пары по SHA, отношение, инкрементальность | 4 |
| `governance/act_facts.py`, `governance/act_trust_findings.py` (новый) | история политики до P, дескрипторы, перечень находок, бюджет | 5 |
| `governance/act_precheck.py`, `governance/act_premerge.py`, `governance/act_app_installations.py` (новый) | программа v2 | 6 |
| `df/publisher/trust_state.py` (новый), `df/publisher/perimeter.py` | состояние доверия издателя | 7 |
| `df/publisher/perimeter.py`, `df/publisher/trust_routes.py` (новый), `df/publisher/ops.py`, `df/publisher/main.py`, `df/publisher/request.py` | маршруты периметра, `trust-status` | 8 |
| `df/publisher/merge.py`, `df/publisher/precheck.py` | мерж candidate/finalize | 9 |
| `df/setup_trust.py` (новый), `df/cli.py`, `df/roles.py`, `df/platform.py` | `setup-trust`, группа `df-history` | 10 |
| `df/doctor.py`, `df/sudoers.py` | `doctor` уровня 3, строка оператора | 11 |
| `tests/df/test_acceptance_root_trust.py` (новый), `tests/df/roles_world.py`, `.github/workflows/df-stage1.yml` | root-строки | 12 |
| `tests/df/live_trust_stand.py` (новый), `.github/workflows/df-stage3-live.yml` (новый) | живые строки | 13, 15 |
| `governance/act_precheck.py`, `df/publisher/precheck.py`, `df/precheck_reap.py`, спека §4.1 | сроки v2 | 14 |
| evidence, `TODO.md` | учёт | 15 |

---

### Task 1: перечень дерева предпроверки в `df/` и проверка полноты до импорта

Первой: роста замыкания в ней нет, установщик 2b строит для неё полное дерево (§3.9, «Путь
обновления»).

**Files:** создать `df/precheck_tree.toml`; изменить `df/admin_tree.py:54-71,121-150`,
`df/admin.py:405-500`, `df/cli.py:515-545`; тесты `tests/df/test_admin_tree.py`,
`tests/df/test_cli.py`.

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
  def tree_problems(tree: Path, listing: TreeList) -> list[str]   # недостающие файлы
  ```
  `precheck serve` передаёт программе `complete_v2: bool` (`serve(..., complete_v2=...)`).
- [ ] **Step 1: Тесты:** `test_tree_list_read_from_pin_as_data` (файл пина, а не константа
  установщика); `test_legacy_pin_falls_back_to_2b_list`; `test_list_matches_ast_closure`
  (AST-замыкание пиненого `act_precheck.py` ⊆ `modules`); `test_2b_modules_retained`
  (`LEGACY_2B` ⊆ перечню 3a, файлы существуют);
  `test_old_installer_tree_v1_served_v2_refused_by_name` (дерево по `LEGACY_2B` при пине 3a:
  запрос v1 обслужен, v2 — отказ `tree_incomplete` без `ModuleNotFoundError` в stderr);
  `test_incomplete_tree_doctor_names_reinstall`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать: `install_admin` копирует `df/` целиком (как
  сейчас) и файлы `load_tree_list(devtools_ref)`; `precheck serve` до `sys.path.append(site)`
  вызывает `tree_problems` по `df/precheck_tree.toml` своего дерева; недостача модулей 2b —
  отказ, недостача только v2 — `complete_v2=False`.
- [ ] **Step 4:** PASS (+ `tests/df/test_admin*.py`, `test_cli.py`). **Step 5:** коммит
  `feat(df): precheck tree list as data inside df/`.

### Task 2: договор запуска `gh` и `git`

**Files:** `df/cli.py:91-92,880-915` (путь записи `gh` в `install.toml` — по ответу на вопрос
6: `cli set` сейчас разрешает только `openssl`, решение владельца 27.10), `df/config.py:44`
(`REQUIRED_CLI` не меняется: `gh` не обязателен для уровня 1), `governance/act_trust.py:432-466`, `governance/act_history_snapshot.py:108-125`;
тесты `tests/df/test_cli_set.py`, `tests/test_act_trust.py`, `tests/test_act_history_snapshot.py`;
перенести `tests/df/test_root_gh_shared_profile.py` из draft devtools#693 (ветка
`test/df-stage3-gh-shared-profile`) и вернуть `df-stage1.yml` к `full`/пустому `-k`.

**Interfaces:**
- Produces:
  ```python
  GH_PINNED_VERSION = "2.102.1"
  GH_ENV_KEYS = ("PATH", "HOME", "LANG", "GH_CONFIG_DIR", "GH_NO_UPDATE_NOTIFIER")
  def history_token(config_dir: Path, *, gh: Path, call_home: Path) -> str | Unavailable
  def open_snapshot(home: Path, full_name: str, refs: Sequence[str], token: str,
                    *, git: Path, git_base: str, deadline: float) -> Path
  ```
  Путь `gh`/`git` программе предпроверки даёт `precheck serve` из `install.toml` своего снимка
  (не запрос). `call_home` — временный каталог вызова в домашнем каталоге роли, удаляется
  после вызова.
- [ ] **Step 1: Тесты:** `test_gh_registration_requires_pinned_version`;
  `test_gh_registration_refuses_untrusted_path`; `test_gh_env_allowlist_and_call_home` (в
  окружении `gh` ровно `GH_ENV_KEYS`; `GH_TOKEN`, `GITHUB_TOKEN` процесса не доходят);
  `test_missing_gh_refused_no_path_fallback`; `test_git_absolute_path_only`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. Root-строка `test_gh_shared_profile`
  (devtools#693) зелёная в `df-stage1` полного объёма на обеих ОС — приёмка задачи.
- [ ] **Step 5:** коммит `feat(df): gh/git launch contract` и закрыть devtools#693 со ссылкой.

### Task 3: узкая поверхность D16 и запись продукта

**Files:** создать `governance/act_surface.py`; изменить `governance/edge_check/publish.py:61-95`
(вызов общей функции), `df/stage2_config.py:200-260` (`Product`); тесты `tests/test_act_surface.py`,
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
  bundle_dir: str = "workstreams/{ws}/spec"     # шаблон каталога бандла
  dag_variant: Literal["full"] = "full"          # legacy-варианты в v1 — отказ разбора
  ```
  `edge_check/publish.surface_violations(state, ops, dag, op, key)` берёт состав коммита и
  вызывает `act_surface.surface_violations` — один код для конвейера и проверяющего (O6).
- [ ] **Step 1: Тесты:** `test_surface_full_pr_foreign_file_in_early_commit_refused`;
  `test_surface_manifest_included`; `test_surface_w1_discovery_allowed`;
  `test_surface_stale_downstream_allowed`; `test_evidence_only_json` (O6, парный:
  `test_pipeline_and_precheck_same_surface`); `test_surface_closure_is_narrow`;
  `test_product_legacy_dag_variant_refused`; `test_product_default_bundle_dir`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ `tests/test_edge_check*.py`).
- [ ] **Step 5:** коммит `refactor(act): shared D16 surface; product bundle source`.

### Task 4: `act_trust` по SHA — пара `df-trust`, отношение, инкрементальность от A

**Files:** создать `governance/act_trust_pair.py`; изменить `governance/act_trust.py:298-410,601-633`;
тесты `tests/test_act_trust_pair.py`, `tests/test_act_trust.py`.

**Interfaces:**
- Consumes: Task 2 (`history_transport` с путём `gh`).
- Produces:
  ```python
  @dataclass(frozen=True)
  class AcceptedBase:            # из запроса: принятая запись издателя
      sha: str
      last_epoch: int
      source_digest: str
      validation: str            # admin_digest + версия правил
  @dataclass(frozen=True)
  class PairResult:
      relation: Literal["descendant", "not_descendant", "first"]
      snapshot: TrustSnapshot | None      # None — журнал H не проверен
      source_descriptor: SourceDescriptor
      findings: tuple[Finding, ...]
  def read_pair(transport, binding: Binding, base: AcceptedBase | None, head: str,
                *, validation: str) -> PairResult | Unavailable
  def settings_findings(transport, snap: TrustSnapshot) -> tuple[Finding, ...]
  ```
  Чтение только по SHA (коммиты, деревья, `source.env`/`epochs.yaml` на A и H), не по ветке
  (сейчас `:375-383`). `base is None` или иная `validation` или иной digest `source.env` —
  холодный путь через `_checked_epochs` (`:346-364`). `settings_findings` явно вызывает
  `_history_self_report` (`:571`), `_rulesets` (`:601`), `_rebase` (`:616`) и новую проверку
  `allow_rebase_merge` у `df-trust` (§6.1 «Свежие проверки»).
- [ ] **Step 1: Тесты** (replay ответов GitHub): `test_pair_descendant_incremental_from_A`;
  `test_pair_rollback_is_not_descendant`; `test_pair_other_validated_branch_not_descendant`;
  `test_pair_reads_by_sha_not_branch` (ветка сдвинута — пара та же, исход тот же);
  `test_changed_bootstrap_warm_equals_cold` (журнал неизменен, `bootstrap_activation` изменён);
  `test_validation_change_goes_cold`; `test_unreadable_head_objects_history_unverifiable`;
  `test_df_trust_rebase_allowed_finding`; `test_settings_checks_called_explicitly`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ `tests/test_act_trust*.py`).
- [ ] **Step 5:** коммит `feat(act): trust pair read by SHA with incremental base`.

### Task 5: история политики до P, дескрипторы, перечень находок, бюджет вердикта

**Files:** `governance/act_facts.py:166-230` (граница `until_sha`), создать
`governance/act_trust_findings.py`; тесты `tests/test_act_facts.py`,
`tests/test_act_trust_findings.py`.

**Interfaces:**
- Produces:
  ```python
  def history(transport, source: SourceV2, anchor: str, until: str | None,
              *, head: str | None = None) -> ... # head — закреплённый P для открытой эпохи
  KINDS: Mapping[str, frozenset[Role]]   # 12 кодов §6.1, допустимые роли
  ROUTE_KINDS: Mapping[Route, frozenset[str]]   # perimeter-status/trust-status/merge
  @dataclass(frozen=True)
  class Finding: kind: str; role: Role; detail: str      # detail ≤ 512 байт
  def range_descriptor(epochs, p_sha: str) -> RangeDescriptor   # ≤ 64, иначе UnsupportedSize
  def source_descriptor(source: SourceV2, raw: bytes) -> SourceDescriptor
  def fit_verdict(envelope: Mapping[str, object], findings, diagnostics) -> bytes  # ≤ 32 КиБ
  class UnsupportedSize(Exception): field: str
  ```
  `source_descriptor`: digest, полное имя (≤ 140 байт), ветка (≤ 255 байт),
  `bootstrap_activation`, число и digest `POLICY_ADMINS`.
- [ ] **Step 1: Тесты:** `test_policy_history_until_pinned_p` (голова политики ушла дальше —
  читается до P); `test_policy_rollback_after_p_same_break`; `test_range_descriptor_closed_and_open`;
  `test_65_epochs_unsupported_size`; `test_policy_admins_4000_compact`;
  `test_all_kind_role_combinations_fit_32k` (16 сочетаний, детали предельной длины,
  многобайтовый UTF-8); `test_codes_never_truncated`; `test_kind_outside_route_rejected`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(act): pinned policy history, trust descriptors, bounded findings`.

### Task 6: программа проверяющего v2 — `op: trust`, `op: merge`

**Files:** `governance/act_precheck.py` (диспетчер по `schema`), `governance/act_premerge.py`
(ядро без `act_app_token`), создать `governance/act_app_installations.py`
(`app_installations` из `act_premerge.py:274-290`); тесты `tests/test_act_precheck.py`,
`tests/test_act_precheck_v2.py`, `tests/test_act_premerge.py`.

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5; `act_context.delivery_verdict` (`governance/act_context.py:26`).
- Produces (договор Task 7–9):
  - запрос `df/precheck-request/v2`: `schema, nonce, op ∈ {trust, merge}, origin_route,
    ctx_id, binding (host, df_trust_repository_id, df_trust_full_name, branch,
    approval_policy_repository_id), accepted (AcceptedBase), head_sha (H или X),
    policy (full_name, branch, p_sha) | null, preliminary_state ∈ {clear}` и для `merge`:
    `kind ∈ {candidate, finalize}, repository_id, full_name, pr, head_ref, base_ref,
    base_tip, changes, token, merger_login, merger_type, bundle_dir, dag_variant,
    api_base, git_base`;
  - вердикт `df/precheck-verdict/v2`: `schema, nonce, request_digest, ctx_id, op,
    outcome ∈ {allow, refuse, unset, tree_incomplete, unsupported_size}, reason,
    relation, snapshot_sha, accepted_sha, last_epoch | null, source (descriptor),
    ranges (descriptor) | null, findings[], diagnostics, version = "df-precheck/2",
    admin_digest` (≤ 32 КиБ — Task 5);
  - `serve(stdin, stdout, *, admin_digest, home, complete_v2, gh, git, deps=None) -> int`.
  `op: merge`: `load_trust` с журналом-адаптером на данных запроса — `status(snap)` =
  `preliminary_state`, только если `snap.root.sha == head_sha`, иначе
  `Unavailable(stale_snapshot)`; `record` складывает находки в вердикт; производный токен —
  credential режима «покрыт держателем ключа» (не пустой `creds`, N3); candidate —
  `act_premerge` с мержером из запроса (N4), поверхность — `act_surface` (Task 3); finalize —
  `act_context.delivery_verdict(..., legacy=None)`, `review_id` из решения доставки (N7).
  Допустимые находки — `ROUTE_KINDS[origin_route]`; `op: trust`, повторно оценивающий наблюдение
  `merge`, вправе вернуть `policy_history_break`.
- [ ] **Step 1: Тесты:** `test_v1_stop_unchanged` (прежние тесты v1 зелёные);
  `test_dispatch_by_schema`; `test_trust_op_writes_nothing` (`~df-precheck` без изменений);
  `test_candidate_allowed_with_act_on_final_head`; `test_approve_before_evidence_not_counted`;
  `test_candidate_foreign_file_refused`; `test_finalize_review_id_from_delivery`
  (поздний dismissal, поздний чужой approve, смена политики после доставки, недопустимый
  мержер — исход равен читателю доставки); `test_finalize_wrong_envelopes_refused`;
  `test_merger_substitution_changes_digest_and_refuses`; `test_stale_snapshot_adapter`;
  `test_derived_credential_mode_not_empty_creds`; `test_reeval_merge_returns_policy_break`;
  `test_closure_excludes_jwt_ops_df` (AST, включая отложенные импорты);
  `test_v2_refused_when_tree_incomplete`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(act): precheck protocol v2 (trust, candidate, finalize)`.

### Task 7: состояние доверия издателя

**Files:** создать `df/publisher/trust_state.py`; изменить `df/publisher/perimeter.py`
(схема, `_expected_schema`, `admit:910-921`, `restore_conservative:924-948`,
`record_violation:1013-1048`); тесты `tests/df/test_trust_state.py`, `tests/df/test_perimeter*.py`.

**Interfaces:**
- Produces:
  ```python
  Route = Literal["perimeter-status", "trust-status", "merge"]
  Outcome = Literal["open", "unresolved", "accepted", "finding_registered",
                    "superseded", "conservative"]
  def ctx_id(route: Route, binding: TrustBinding, admin_digest: str, rules: str,
             accepted: str | None, head: str, policy: PolicyPin | None) -> str
  def open_observation(db: Db, root: Root, ctx: str, ...) -> int        # observation_id; дедуп по (root, ctx)
  def register_findings(db: Db, root: Root, ctx: str, findings, epochs_of: Callable) -> list[int]
  def advance_head(db: Db, root: Root, obs: int, head_read: Callable[[], str]) -> Outcome
  def conservative(db: Db, root: Root, ctx: str, obs: int) -> int        # К
  def blocked(conn, root: Root) -> bool                                  # инвариант И
  def known_roots(conn, current: Root) -> list[Root]   # events ∪ trust_heads ∪ trust_observations ∪ привязка
  def mark_restored(conn) -> None; def restored(conn) -> bool
  ```
  Таблицы `trust_heads`, `trust_observations`, ключи событий и правило эпохи регистрации — §6.1
  (модель). `register_findings` — своя транзакция, без условия на голову; `advance_head` —
  `BEGIN IMMEDIATE`, повторное чтение строки и головы ветки, CAS по `row_version`. Эпоха — из
  проверенного текущего снимка под блокировкой (`trust_heads`, если голова в транзакции = SHA
  строки и контекст текущий), иначе `pending`. `restored` — постоянный признак в `meta`,
  отдельный от `admission`. Корень без строк после `restored` — `conservative` при первом
  появлении.
- [ ] **Step 1: Тесты** (фейк проверяющего, реальная SQLite): `test_observation_dedup_equal_ctx_only`;
  `test_trust_status_does_not_complete_merge_observation`; `test_two_p_two_events`;
  `test_finding_registered_without_advance`; `test_concurrent_advance_one_wins_findings_kept`;
  `test_rollback_h1_h2_h1_finding_after_restart`; `test_lost_answer_reevaluated_same_inputs`;
  `test_unresolved_until_journal_readable`; `test_context_change_conservative`;
  `test_unsupported_size_conservative`; `test_epoch_from_current_snapshot_e2_blocks_until_e3`;
  `test_rebind_return_known_root_uses_old_A`; `test_rebind_same_triple_cold_history_kept`;
  `test_restore_copy_without_root_then_rebind_conservative`;
  `test_known_roots_include_heads_and_observations`; `test_crash_points` (после создания
  наблюдения, после вердикта, до/после `COMMIT` регистрации, до/после `COMMIT` продвижения).
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): publisher trust state (heads, observations, invariants)`.

### Task 8: маршруты периметра издателя и `trust-status`

**Files:** создать `df/publisher/trust_routes.py`; изменить `df/publisher/perimeter.py`
(`perimeter_status:1081-1107`, `_check_root:1159-1167`, `EPOCHS` убрать), `df/publisher/ops.py`
(установки App на источниках доверия в `self_check_first`, `_check_app`),
`df/publisher/request.py` (операция `trust-status`), `df/publisher/main.py` (`SUDO_UID = 0` —
`self-check` и `trust-status`), `df/stage2_config.py` (`expected_publisher`); тесты
`tests/df/test_trust_routes.py`, `tests/df/test_publisher_main.py`, `tests/df/test_publisher_ops.py`.

**Interfaces:**
- Consumes: Tasks 6, 7.
- Produces:
  ```python
  def perimeter_status(ctx: Context) -> Response   # быстрый путь или наблюдение + op: trust
  def trust_status_op(ctx: Context) -> Response     # всегда наблюдение (A, H|A) + op: trust
  def pin_policy(oc: OpContext, repository_id: int) -> PolicyPin   # GET /repositories/{id} → имя, default branch → голова
  def run_trust(oc: OpContext, obs: Observation) -> Verdict | Unavailable   # вызов v2, сверка эха
  ```
  Ответ `trust-status` — `df/publisher-response/v1`, `result`: `root, snapshot_sha,
  last_epoch, state, unverified, findings`. Корень прогона сверяется у всех PR-операций, не
  только у операций периметра (`_check_root`). Установка App на `approval-policy`/`df-trust`
  — находка настроек в каждой операции издателя и в `self-check`. Ключ истории отсутствует —
  `perimeter-status`/`trust-status` анонимно с предупреждением (O14).
- [ ] **Step 1: Тесты:** `test_fast_path_only_without_open_observations`;
  `test_head_moved_creates_observation`; `test_trust_status_always_calls_checker`;
  `test_trust_status_root_only`; `test_root_validates_response_schema_and_root`;
  `test_pin_policy_by_id_default_branch`; `test_echo_mismatch_unbound`;
  `test_app_on_trust_repo_finding`; `test_old_root_run_refused_pr_ops`;
  `test_no_history_key_anonymous_warning`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): perimeter routes and trust-status over trust state`.

### Task 9: мерж candidate и finalize — окончательный допуск

**Files:** `df/publisher/merge.py` (`_check_kind:444-453`, `_precheck:455-465`, `_KIND:424`,
`TRUST_ROOT:78`), `df/publisher/precheck.py` (`admin_precheck:133`, `_bound:292`); тесты
`tests/df/test_publisher_merge.py`, `tests/df/test_publisher_precheck.py`.

**Interfaces:**
- Consumes: Tasks 6–8; фазы и сверка 2b без изменений.
- Produces: `admin_precheck(kind, oc, pr, facts, *, obs: Observation) -> PrecheckResult`
  (v2 для candidate/finalize, v1 для stop); `admit(db, root, obs, verdict) -> None |
  Refused("perimeter_blocked")` — одна транзакция: завершить своё наблюдение, зарегистрировать
  находки, заново применить И (голова в транзакции = X = принятой записи, нет других
  незавершённых наблюдений и блокирующих событий); после `COMMIT` — `armed` и `PUT`. Сверка
  `admin_digest` вердикта с записью `current` (`binding_mismatch`); `TrustRoot` evidence —
  идентичность привязки для всех видов, SHA X для candidate/finalize. finalize публикует
  аттестацию, как stop. Без привязки — прежний отказ `no_binding`; привязка без ключа истории
  — `history_token_required` (O14).
- [ ] **Step 1: Тесты:** `test_candidate_merged_after_admission`;
  `test_block_registered_during_precheck_refused_no_put`; `test_precheck_timeout_no_root_stop`;
  `test_reevaluated_merge_observation_never_puts`; `test_admin_digest_mismatch_refused`;
  `test_trust_root_identity_for_stop_with_binding`; `test_finalize_attested_then_merged`;
  `test_history_token_required`; `test_stop_unchanged_v1`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит
  `feat(df): candidate and finalize merge with final admission`.

### Task 10: `darkfactory setup-trust` и группа `df-history`

**Files:** создать `df/setup_trust.py`; изменить `df/cli.py` (подкоманда `setup-trust` с
`--rebind`, режим писателя для записи), `df/roles.py`, `df/platform.py` (группа `df-history`,
членство оператора); тесты `tests/df/test_setup_trust.py`, `tests/df/test_roles_setup.py`.

**Interfaces:**
- Consumes: Task 8 (`trust-status`).
- Produces:
  ```python
  TEMPLATES: Mapping[str, str]     # source.env, epochs.yaml, policy/* — фиксированный текст
  def show_publishable(...) -> str                       # точные байты публикуемого (§2.3)
  def check_perimeter(pat_transport, policy_repo: int, trust_repo: int) -> PerimeterReport
  def write_binding(layout, binding: TrustBinding, token_file: Path) -> None   # root; профиль /etc/df/history-gh
  def verify_after_write(layout) -> TrustStatus | Refused
  HISTORY_GROUP = "df-history"
  ```
  Проверка периметра — человек без root, временный PAT (§6.2): коды ответа каждого API в
  evidence `perimeter-<время>.json`; 403/404 — «не проверено»; установки сторонних App —
  разрешённая непроверенная предпосылка. Стоп: тариф без rulesets, `allow_rebase_merge = true`,
  ветка источника не default branch (С-2), нет профиля истории (O14). Запись — root, значения
  из аргументов человека, строгий `parse_trust` после записи; затем `trust-status`. `--rebind`
  — новая полная проверка. Отказ `unsupported_size: epochs` называет выход §6.1 дословно.
- [ ] **Step 1: Тесты:** `test_templates_exact_bytes_shown`; `test_tariff_without_rulesets_stops`;
  `test_rebase_allowed_stops`; `test_policy_branch_not_default_stops`;
  `test_no_history_profile_not_finished`; `test_pat_403_is_not_verified`;
  `test_profile_written_root_group_modes`; `test_binding_from_args_parsed_strictly`;
  `test_verify_after_write_unavailable_not_finished`; `test_rebind_full_check`;
  `test_roles_setup_history_group_exact`; `test_epochs_exhausted_text_names_exit`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит `feat(df): setup-trust`.

### Task 11: `doctor` уровня 3 (доверие) и строка оператора

**Files:** `df/doctor.py`, `df/sudoers.py`; тесты `tests/df/test_doctor.py`,
`tests/df/test_doctor_level3.py`, `tests/df/test_sudoers.py`.

**Interfaces:**
- Consumes: Tasks 8, 10.
- Produces: проверки `trust.binding`, `trust.history` (ключ, профиль, режимы, состав
  `df-history` точно), `trust.status` (через `trust-status`), `trust.unsupported_size` (выход
  §6.1); строка оператора — `sudoers.operator_rights(...) -> Check` с двумя результатами:
  (i) PASS/FAIL по `/etc/sudoers.d/df` и эффективным правилам df; (ii) для оператора — «не
  проверено, принято исключение». Побочные эффекты проб названы в выводе (§8).
- [ ] **Step 1: Тесты:** `test_history_group_extra_member_broken`;
  `test_trust_status_schema_checked`; `test_operator_two_results_never_pass_ii`;
  `test_level3_shown_separately_from_level`; `test_side_effects_listed`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS. **Step 5:** коммит `feat(df): doctor level 3 (trust)`.

### Task 12: root-строки CI

**Files:** создать `tests/df/test_acceptance_root_trust.py`; изменить `tests/df/roles_world.py`,
`.github/workflows/df-stage1.yml` (job `stage2a`).

Строки (обе ОС, фейк GitHub и dumb-HTTP git, как в 2b Task 10):
- положительные candidate и finalize через всю цепочку издатель → sudo → вход → `act_precheck` v2;
- `op: trust` через цепочку; `~df-precheck` после вызова без долговечного состояния;
- профиль истории: член читает, посторонний — отказ ОС; токен истории не в argv, I/O-журнале
  sudo, evidence;
- строка оператора — (i) PASS, (ii) «не проверено, принято исключение»;
- **обновление:** установщик 2b (пин `91ac40c`) ставит пин этой ветки — stop `allow`,
  candidate — `tree_incomplete` до импорта; повторная установка кодом 3a — новый `<id>`,
  candidate доходит до программы v2;
- 65 эпох → `unsupported_size: epochs` → новый корень → `--rebind` → новый прогон
  (фейк GitHub).
- [ ] **Step 1:** тесты строк (`test_root_trust_*`); локально пропуск, в CI — job `stage2a`.
- [ ] **Step 2:** коммит `test(df): stage 3a root rows`.

### Task 13: живые строки доверия и замер холодного пути (первый прогон)

**Files:** создать `tests/df/live_trust_stand.py`, `.github/workflows/df-stage3-live.yml`
(только `workflow_dispatch` с `master`, environment `df-stage3-live` с одобрением владельца);
тесты `tests/df/test_live_trust_stand.py`.

Строки:
- **замер холодного пути** — ветка-замер стенда с N = 50 коммитов first-parent (5 меняют
  журнал), пустые факты после обновления административного дерева (новый `admin_digest`),
  обе ОС: время `op: trust`, число запросов — в артефакт;
- bootstrap-порядок на пустых `df-roles-probe-policy`, `df-roles-probe-trust`; показ публикуемого;
- таблица запросов §6.1 (каждый запрос → credential → поведение при отказе);
- `trust-status` с привязкой; App, установленный на репо доверия, → находка;
- переписанная история `df-trust` (возврат на прежний SHA) → находка и холодный путь;
- stop с привязкой: `trust_root` — идентичность (регресс строк 10–12 2b).
- [ ] **Step 1:** офлайн-тесты помощников (`DF_STAND_*` не читаются вне живого прогона).
- [ ] **Step 2:** workflow; **Step 3:** коммит `test(df): stage 3a live trust rows`.
- [ ] **Step 4:** владелец выполняет H1–H6 и запускает прогон; артефакт замера сохраняется.

### Task 14: сроки v2 по замеру

**Files:** `governance/act_precheck.py` (`DEADLINE` для v2), `df/publisher/precheck.py`
(`PRECHECK_WAIT`), `df/precheck_reap.py` (`REAP_AFTER`), `df/sudoers.py` (`command_timeout`),
спека §4.1, §5.4 (значения); README «Поддерживаемая длина истории».

**Interfaces:** значения выводятся из артефакта Task 13 по правилу, которое даст владелец
(вопрос 1); до ответа задача не начинается.
- [ ] **Step 1:** тест `test_v2_deadlines_from_measurement` (константы равны значениям из
  записанного замера, порядок рубежей `DEADLINE < command_timeout < PRECHECK_WAIT < REAP_AFTER`).
- [ ] **Step 2–4:** FAIL → выставить → PASS (+ root-строки рубежей 2b). **Step 5:** коммит
  `feat(df): v2 precheck deadlines from the cold-path measurement`.

### Task 15: живые строки мержа (второй прогон), evidence, учёт

**Files:** `tests/df/live_trust_stand.py` (строки мержа), `docs/evidence/2026-11-XX-df-stage3a-acceptance.md`,
`TODO.md`.

Строки: засев candidate техническим PR (как засев 2b) → **approve владельца на окончательном
head** (H7, двухфазный запуск) → мерж издателем; approve до evidence-коммита не засчитан;
посторонний файл в раннем коммите — отказ; finalize по доставленной заявке — аттестация и мерж;
временный таймаут предпроверки — повтор даёт тот же исход без остановки корня.
- [ ] **Step 1:** строки и офлайн-тесты помощников; коммит `test(df): stage 3a live merge rows`.
- [ ] **Step 2:** владелец запускает прогон; H8.
- [ ] **Step 3:** evidence: прогоны, ячейки по строкам §10 3a, исключения владельца поимённо
  («не проверено, принято исключение»); TODO: закрыть `df-installer-stage3-precheck`,
  `df-trust-history-token`, `df-precheck-admin-digest-crosscheck`, `df-precheck-git-approved-path`.
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

## Вне этого плана

- 3b: `setup-repo`, `products.toml` командой, ruleset и CODEOWNERS продукта, `ACT_MODE`,
  `policy preview`, процессы человека с `site/` (§3.9), спайк форк-профиля, живая проверка
  аттестаций под D7.
- Этап 4: таблица конвейеров `df-launch run`, `verify`.
- Проверка происхождения звеньев `df-trust` (PR) — спека A, названная предпосылка.

## Самопроверка

- **Покрытие спеки:** строки §10 3a → задачи: доверие (10, 8, 13), профиль и группа (10, 12),
  договор запуска (2), предпроверка candidate/finalize (3, 5, 6, 9, 12, 15), переписанная
  история и откат (4, 7, 13), гонка и прерывания (7), `source.env` (4), `--rebind` и
  восстановление (7, 12), замер холодного пути (13 → 14), ключ истории O14 (8, 9, 10), бюджет и
  65 эпох (5, 12), обновление 2b → 3a (1, 12), stop с привязкой (9, 13). §3.9 (перечень) — 1;
  §3.10 — 2; §4.1 — 10, 14; §4.5 — 11, 12; §5.2 — 8; §5.4 — 3, 6, 9; §5.5 — 9; §6.1 — 4–8;
  §6.2 — 10; §8 — 11. Пробел: исключение §3.9 для процессов человека — 3b (вне плана).
- **Шаги:** каждый тестовый шаг называет тесты; значения спеки (32 КиБ, 512 байт, 64 эпохи, 12
  кодов, 16 сочетаний, окружение `gh`, режимы профиля) — в Global Constraints и Interfaces.
  Тела функций не выписаны.
- **Типы:** `ctx_id`, `Route`, `Outcome`, `AcceptedBase`, `PolicyPin`, `Finding`, `ROUTE_KINDS`
  совпадают в задачах 4–9; `complete_v2`, `gh`, `git` в `serve` — Tasks 1, 2, 6.
- **Review Focus:** пять строк, у каждой тест в задаче-владельце.
- **Пропорция:** план короче затрагиваемых разделов спеки; кода — только сигнатуры.
