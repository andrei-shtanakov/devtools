"""Таблица идентичности судимых объектов: исчерпывающая классификация определений
части B (ревизии b2–b4, Р-17; правило «класс, а не экземпляр»).

Механизм (ревизия b4), каждое свойство — отдельный мета-тест:

1. Состав. Рекурсивный обход AST (вложенные функции и классы, `async def`,
   определения под `if`/`try`) всех модулей части B и перечисленных дополнений
   части B в существующие модули; каждое определение — ровно в одном реестре.
2. Классификация по правилу, а не по имени. `NOT_JUDGMENT` допустим, только если в
   собственном теле функции нет условного пути к отказу (`raise`, `return
   Unavailable/Rejected/Violation/_Stop/LegacyHalt`, `cls(..., error=…)`,
   `_unresolved`, `_invalidate`, ответ `unavailable:`/`not_approved:`, условный
   `return False`, форма «отказ | None», отказ, присвоенный имени и возвращённый
   позже); ложные срабатывания — `GUARD_EXEMPT` с причиной. Детектор — ПЕРЕКРЁСТНАЯ
   проверка формы, а не доказательство полноты: отказ иной формы (доменный объект,
   вызов помощника, вернувший отказ) он не видит. Реестр ведёт ревьюер задачи: каждое
   новое определение части B и каждая новая проверка внутри изменённой существующей
   функции (`MODIFIED_EXISTING`) вносятся руками при ревью, с двойником.
3. Точная причина. Строка `JUDGMENT` = компонент × функция проверки × литерал
   проверки (встречается в её исходнике ровно один раз — то есть принадлежит одной
   проверке) × двойник × литерал утверждения: по AST литерал (или имя, связанное с
   ним) входит в `assert` о результате либо в `pytest.raises(..., match=…)` —
   утверждение только о типе или коде выхода не засчитывается; механизм проверяется
   собственными отрицательными двойниками (`test_reason_link_mechanism`).
4. Маркеры положительного суждения — перекрёстная проверка (`MARKERS`).

Мутационная проверка строк (обойти проверку → двойник падает на утверждении, а не
исключением) обязательна перед коммитом Task 16 по ВСЕМ строкам, включая стенд
`World` (`tests/act_mutation.py`, Task 16 Step 4a).
"""

# ruff: noqa: E501 — таблица данных: строка = место × компонент × тест, перенос её не читается

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    "act_facts",
    "act_anon",
    "act_git_rows",
    "act_forge_http",
    "act_check",
    "act_trust",
    "act_journal",
    "act_context",
    "act_identity",
    "act_journal_wire",
    "act_status",
    "act_history",
    "act_history_snapshot",
    "act_precheck",
    "act_node",
    "act_premerge",
    # df этап 3a (Task 7, ревью D1): ядро предпроверки без CLI — его импортирует v2
    "act_premerge_core",
    "act_app_token",
    "act_barrier",
    # df этап 3a (Task 3): общая поверхность D16 конвейера и издателя (O6)
    "act_surface",
    # df этап 3a (Tasks 4–7): типы доверия, пара df-trust по SHA, дескрипторы и
    # бюджет вердикта, программа проверяющего v2, самоотчёт установок App
    "act_trust_types",
    "act_trust_findings",
    "act_trust_pair",
    "act_precheck_v2",
    "act_app_installations",
)
#: Определения, которые часть B добавляет в существующие модули (полный перечень по
#: листингам Tasks 11–16); изменённые существующие функции — в разделе ревизии плана.
EXISTING_ADDITIONS = {
    "ops": (
        "_gate_check_exe",
        "_act_args",
        "Ops.gate_snapshot_digest",
        "RealOps.gate_snapshot_digest",
        "Ops.first_parent_changes",
        "RealOps.first_parent_changes",
        "Ops.pr_body_fact",
        "RealOps.pr_body_fact",
        "RealOps._snapshot_url",
        "RealOps._credential_helper",
        "RealOps._snapshot_env",
        "RealOps._snapshot_git",
        "Ops.snapshot_open",
        "RealOps.snapshot_open",
        "Ops.snapshot_reader",
        "RealOps.snapshot_reader",
        "Ops.snapshot_close",
        "RealOps.snapshot_close",
        "SnapshotOps.__init__",
        "SnapshotOps.__getattr__",
        "SnapshotOps._git",
        "SnapshotOps.first_parent_changes",
        "SnapshotOps.show_file",
        "SnapshotOps.show_file_bytes",
        "SnapshotOps.rev_parse",
        "SnapshotOps.fetch_branch",
    ),
    "approval_ledger": (
        "record_review_act",
        "audit_events",
        "record_audit",
        "successor_of",
    ),
    "act_decision": ("find_act", "merger_allowed"),
    "act_envelope": ("expected_envelope", "node_history"),
    "task_bridge": ("_admission_refusal", "_require_admission"),
    "act_settings": ("config_home",),
    "approve_node": (
        "_legacy_outcome",
        "_legacy_verdict",
        "_review_debt",
        "_supersedes_undecided",
        "_require_no_join_for_successor",
        "_require_same_act",
        "_creation_policy",
        "require_clean_synced_base",
        # интеграционное ревью части B: I-3 (коммит без трассировки), m-2 (проекция)
        "_commit_paths",
        "_content_hash",
    ),
    # интеграционное ревью части B, I-2: доставка candidate `review/v1` на resume
    "runner": ("_deliver_review_candidate", "_stop_on_refusal"),
}
#: Изменённые частью B СУЩЕСТВУЮЩИЕ функции (не дополнения): новая проверка — кортеж имён,
#: каждое — ключ `JUDGMENT` `<функция>#<имя>`; новой проверки нет — причина строкой. Список
#: ведёт ревьюер задачи (детектор формы его не доказывает); сверено с листингами Tasks 11–16.
MODIFIED_EXISTING = {
    # проверки акта, пина и преемника вынесены в дополнения `_require_same_act`,
    # `_creation_policy`, `_require_no_join_for_successor` (классифицированы как
    # определения части B)
    "approve_node._propose": (
        "status_unavailable",
        "mode_unavailable",
        "legacy_noop_review",
    ),
    "approve_node.read_dag_state": ("mode_unset", "status_unavailable", "review_debt"),
    "approve_node._require_upstream_ready": (
        "mode_unset",
        "upstream_unavailable",
        "upstream_not_approved",
    ),
    # stop-PR (Task 13; рубежи после M3 ревью Task 16): ревью и форма в цикле мержа
    "approve_node._try_agent_merge": (
        "form",
        "stop_review",
        "stop_late_form",
        "stop_form_first",
    ),
    "runner._step_gate": ("act_mode_unset", "completed_admission"),
    "runner._step_s8": ("act_mode_unset", "completed_admission", "run_status"),
    "runner._next_wave": ("admission",),
    "act_envelope.envelope_refusal": ("bytes", "pins"),
    "approve_node._advance": "маршрутизация в `act_node.route` до выбора шага; своей проверки нет",
    "approve_node._reconcile_candidate": "проверки вынесены в `_legacy_outcome` (Task 14 Step 3a), новых нет",
    # создание делегирует `act_node.creation` (место суждения); новая своя проверка —
    # повтор чужой живой заявки при `--repropose` (Task 13a)
    "approve_node.propose_from_source": ("source_successor",),
    "approve_node._sync_branch_to_snapshot": "пишет манифест из `act_node.manifest_file`; проверок нет",
    "approve_node.approve_node": ("successor_over_live",),
    "approval_ledger.start_request": "пишет поле `predecessor`; проверок нет",
    "task_bridge._canonical_dag_hash": "хеш (В-1), не суждение",
    "task_bridge._content_anchor": "пробрасывает `ws_id`; не суждение",
    "task_bridge.deliver_superseded": (
        "review_noop",
        "replacement_repeat",
        "returned_pr",
        "returned_merged",
        "returned_open",
        "redo_intent",
        "returned_v1",
    ),
    "task_bridge.deliver_for_run": ("completed", "adopted"),
    "act_envelope.current_event": "выводится из `node_history` (п. 9 (Б), 01.11); своей проверки нет",
    "act_envelope.next_generation": "выводится из `node_history` (п. 9 (Б), 01.11); своей проверки нет",
    "ops.RealOps.gate_check_s8": "аргументы `--act-mode`/`--node-status`; не суждение",
    "ops.RealOps.gate_check_candidate": "аргументы `--act-mode`/`--node-status`; не суждение",
    "bundle_state.candidate_state": '`act_mode="legacy"` в `collect_bundle`; не суждение',
    # сверено по диффу части B (Task 16): правки без новой проверки-отказа
    "act_perimeter.permissions_refusal": "только текст докстринга (маркер → событие); проверки прежние",
    "approval_ledger.extend_request": "пишет `generations` узла `review/v1`; проверок нет",
    "approve_node._sync_base_and_settle": "проверки вынесены в `require_clean_synced_base` без изменений",
    "approve_node._policy_ref": "текст пина `review/v1`; не суждение",
    "approve_node._publish_candidate": "метка и сообщение `review/v1`; проверок нет",
    "approve_node._candidate_body": "тело candidate `review/v1`; не суждение",
    "approve_node._missing_approving_review": "параметры `kind`/`retry` текста отказа; проверки прежние",
    "runner.resume": "зовёт аудит `act_node.audit_completed`; решения не меняет",
    "runner.reopen": "делегирует `act_node.reopen` (место суждения); своей проверки нет",
    "task_bridge.deliver_for_run._capture_anchor": "пробрасывает `ws_id`; не суждение",
    "task_bridge.main": ("repropose_exclusive",),
    # интеграционное ревью части B (I-1–I-3, m-2)
    # df этап 3a (Task 3): проверки поверхности вынесены в `act_surface` (O6)
    "edge_check/publish.surface_violations": "делегирует `act_surface.surface_violations` (место суждения); своя проверка — прежний `_unresolved` состава коммита",
    "runner._resume_wave": ("review_delivery", "pr_facts_failure"),
    "runner._step_candidate": "пауза `review/v1` — `waiting_human_approval` и текст об approve; проверки-отказа нет",
    "runner._finalize_wave": "стоп по отказу вынесен в `_stop_on_refusal` без изменений текста",
    "runner._wave_pause": "параметр статуса паузы; не суждение",
    "approve_node._ensure_finalize_identity": ("find_pr_failure",),
    "approve_node._await_required_checks": ("pr_facts_failure",),
    "approve_node._carried_text": "сверка байтов заявки — хешем `_content_hash` её акта (m-2); проверка прежняя",
    "approve_node._verify_nodes_in_base": "self-hash в base — `_content_hash` акта заявки (m-2); проверка прежняя",
    "edge_check/publish.publish_wave": "коммит evidence — через `an._commit_paths` (I-3), акт заявки — в текст ревью (N-2); проверки прежние",
    "edge_check/publish.checklist_body": "текст одобряющего ревью по акту заявки (N-2); не суждение",
    "edge_check/publish.evidence_dir": "каталог evidence — из `ws_id` через `act_surface.evidence_dir`, не из `bundle_dir` (df 3a); не суждение",
}

S, N, L = (
    "tests/test_act_status.py",
    "tests/test_act_node.py",
    "tests/test_act_legacy.py",
)
E, TB = "tests/test_act_envelope.py", "tests/test_governance_task_bridge.py"
RN = "tests/test_governance_runner.py"
P, B, C = (
    "tests/test_act_premerge.py",
    "tests/test_act_barrier.py",
    "tests/test_act_check.py",
)
T, J, W = (
    "tests/test_act_trust.py",
    "tests/test_act_journal.py",
    "tests/test_act_journal_wire.py",
)
ID, A, G = (
    "tests/test_act_identity.py",
    "tests/test_act_anon.py",
    "tests/test_act_gate.py",
)
X, K, F = (
    "tests/test_act_context.py",
    "tests/test_act_app_token.py",
    "tests/test_act_facts.py",
)
FH = "tests/test_act_forge_http.py"
HS = "tests/test_act_history_snapshot.py"
PC = "tests/test_act_precheck.py"
RP, OB = "tests/test_act_repropose.py", "tests/test_act_ops_body.py"
OF, WR = "tests/test_act_ops_failures.py", "tests/test_act_wave_runner.py"
SF = "tests/test_act_surface.py"
TP = "tests/test_act_trust_pair.py"
TT, TF = "tests/test_act_trust_types.py", "tests/test_act_trust_findings.py"
V2 = "tests/test_act_precheck_v2.py"
ENVELOPE_FIELDS = (
    "node",
    "request_id",
    "generation",
    "manifest_path",
    "head_sha",
    "self_hash",
    "upstream_pins",
    "merge_commit",
    "review_id",
    "policy",
)
ENV_CHECK = {
    "node": "подставлен для",
    "request_id": "конверт другого поколения",
    "generation": "конверт другого поколения",
}


def r(component, check, lit, twin, tlit=None):
    """Строка: компонент, функция проверки, литерал проверки, двойник, литерал assert."""
    return (component, check, lit, twin, lit if tlit is None else tlit)


JUDGMENT = {
    # --- act_facts: граница библиотеки steward
    "act_facts._call": (
        r(
            "library_exception",
            "act_facts._call",
            "except Exception",
            f"{F}::test_call_turns_exception_into_unavailable",
            '"steward read_act_facts: OSError: socket"',
        ),
    ),
    "act_facts._own": (
        r(
            "steward_unavailable",
            "act_facts._own",
            "isinstance(got, _v3().Unavailable)",
            f"{F}::test_own_translates_steward_unavailable",
            '"transport"',
        ),
    ),
    # --- act_anon
    "act_anon.AnonTransport.send": (
        r(
            "memo_only_sha",
            "act_anon.AnonTransport.send",
            "_IMMUTABLE.match(path)",
            f"{A}::test_memo_only_for_sha_addressed_paths",
            "t.sent == [commit, pulls, pulls]",
        ),
        r(
            "relative_get",
            "act_anon.AnonTransport.send",
            "_UNSAFE_PATH.search(path)",
            f"{A}::test_only_relative_get",
            '"только GET относительного пути"',
        ),
    ),
    "act_anon._response": (
        r(
            "size_limit",
            "act_anon._response",
            "len(raw) > MAX_BODY",
            f"{A}::test_oversized_response_refused",
            '"anon: ответ больше 8 байт"',
        ),
    ),
    "act_anon.fetch_response": (
        r(
            "transport_failure",
            "act_anon.fetch_response",
            "except Exception as exc",
            f"{A}::test_transport_failure_is_status_none",
            '"TimeoutError"',
        ),
    ),
    # --- act_forge_http
    "act_forge_http.TokenTransport.__init__": (
        r(
            "empty_token",
            "act_forge_http.TokenTransport.__init__",
            "if not token",
            f"{FH}::test_init_refuses_empty_token",
            '"forge: empty token"',
        ),
    ),
    "act_forge_http.TokenTransport.send": (
        r(
            "refusal_status_none",
            "act_forge_http.TokenTransport.send",
            "refusal is not None",
            f"{FH}::test_send_refusal_is_status_none",
            '"forge: read-only transport: DELETE repos/o/r"',
        ),
    ),
    "act_forge_http.TokenTransport._refusal": (
        r(
            "relative_path",
            "act_forge_http.TokenTransport._refusal",
            "_UNSAFE_PATH.search(path)",
            f"{FH}::test_refusal_unsafe_path",
            "\"forge: not a relative API path: '/abs'\"",
        ),
        r(
            "graphql_query_only",
            "act_forge_http.TokenTransport._refusal",
            'return "GraphQL text is not a query"',
            f"{FH}::test_refusal_graphql_not_query",
            '"forge: GraphQL text is not a query"',
        ),
        r(
            "read_only_method",
            "act_forge_http.TokenTransport._refusal",
            "read-only transport",
            f"{FH}::test_send_refusal_is_status_none",
            '"forge: read-only transport: DELETE repos/o/r"',
        ),
    ),
    "act_forge_http.TokenTransport.get_json": (
        r(
            "delivery_failure",
            "act_forge_http.TokenTransport.get_json",
            "resp.status is None",
            f"{FH}::test_get_json_delivery_failure",
            '"forge: OSError: down"',
        ),
        r(
            "mid_walk_status",
            "act_forge_http.TokenTransport.get_json",
            'raise OSError(f"forge: HTTP {resp.status} mid-walk")',
            f"{FH}::test_get_json_mid_walk_status",
            '"forge: HTTP 500 mid-walk"',
        ),
        r(
            "mid_walk_shape",
            "act_forge_http.TokenTransport.get_json",
            'raise OSError("forge: page shape changed mid-walk")',
            f"{FH}::test_get_json_mid_walk_shape",
            '"forge: page shape changed mid-walk"',
        ),
        r(
            "page_cap",
            "act_forge_http.TokenTransport.get_json",
            'raise OSError(f"forge: more than {MAX_PAGES} pages")',
            f"{FH}::test_get_json_page_cap",
            '"forge: more than 2 pages"',
        ),
    ),
    "act_forge_http.TokenTransport._next_path": (
        r(
            "outside_api_base",
            "act_forge_http.TokenTransport._next_path",
            "url.startswith(self._base)",
            f"{FH}::test_get_json_next_outside_base",
            '"forge: Link next outside api_base"',
        ),
    ),
    # --- act_history_snapshot (этап 2b, Task 3)
    "act_history_snapshot._timeout": (
        r(
            "deadline_passed",
            "act_history_snapshot._timeout",
            "if left <= 0",
            f"{HS}::test_expired_deadline_spawns_no_git",
            '"git: deadline passed"',
        ),
    ),
    "act_history_snapshot._git": (
        r(
            "deadline_kill",
            "act_history_snapshot._git",
            "isinstance(exc, subprocess.TimeoutExpired)",
            f"{HS}::test_fetch_respects_deadline",
            '"git: deadline passed"',
        ),
    ),
    "act_history_snapshot._require": (
        r(
            "git_failure",
            "act_history_snapshot._require",
            "if done.returncode != 0",
            f"{HS}::test_fetch_failure_is_os_error",
            '"snapshot: fetch owner/p: exit 128"',
        ),
    ),
    "act_history_snapshot._absolute_git": (
        r(
            "absolute",
            "act_history_snapshot._absolute_git",
            "if not git.is_absolute()",
            f"{HS}::test_git_absolute_path_only[bare]",
            '"is not an absolute path"',
        ),
    ),
    "act_history_snapshot.open_snapshot": (
        r(
            "full_name",
            "act_history_snapshot.open_snapshot",
            "_FULL_NAME.fullmatch(full_name)",
            f"{HS}::test_open_snapshot_refuses_bad_full_name",
            "\"full_name 'owner' is not owner/repo\"",
        ),
        r(
            "refs",
            "act_history_snapshot.open_snapshot",
            "if bad or not refs",
            f"{HS}::test_open_snapshot_refuses_bad_refs",
            '"are not full ref names"',
        ),
    ),
    "act_history_snapshot.SnapshotHistory.fetch_branch": (
        r(
            "full_ref",
            "act_history_snapshot.SnapshotHistory.fetch_branch",
            "if not _full_ref(ref)",
            f"{HS}::test_fetch_branch_refuses_bad_ref",
            "is False",
        ),
    ),
    "act_history_snapshot.SnapshotHistory.prs_containing_commit": (
        r(
            "shape",
            "act_history_snapshot.SnapshotHistory.prs_containing_commit",
            "not all(_is_pull(p) for p in body)",
            f"{HS}::test_prs_containing_commit_shape_refused",
            '"forge: commits/aaaaaaaaaaaa/pulls: unexpected"',
        ),
        r(
            "merged_by_merged_at",
            "act_history_snapshot.SnapshotHistory.prs_containing_commit",
            'p.get("merged_at") is not None',
            f"{HS}::test_prs_containing_commit_normalized_from_rest",
            '"MERGED"',
        ),
    ),
    "act_history_snapshot.SnapshotHistory._rest": (
        r(
            "slug",
            "act_history_snapshot.SnapshotHistory._rest",
            "_FULL_NAME.fullmatch(repo_slug)",
            f"{HS}::test_rest_refuses_bad_slug",
            "\"forge: repo_slug '../x' is not owner/repo\"",
        ),
        r(
            "deadline_during",
            "act_history_snapshot.SnapshotHistory._rest",
            "except OSError",
            f"{HS}::test_deadline_propagates_from_forge_path",
            '"forge: deadline passed"',
        ),
        r(
            "deadline_after",
            "act_history_snapshot.SnapshotHistory._rest",
            "self._left()  # an answer that completed past the deadline",
            f"{HS}::test_rest_answer_completed_after_deadline_raises",
            '"forge: deadline passed"',
        ),
        r(
            "status",
            "act_history_snapshot.SnapshotHistory._rest",
            "if status != 200",
            f"{HS}::test_rest_non_200_is_os_error",
            '"forge: HTTP 404: repos/owner/p/pulls/7"',
        ),
    ),
    # --- act_precheck (этап 2b, Task 4): запрос, вердикт, вид stop
    "act_precheck.serve": (
        r(
            "token_scrub",
            "act_precheck.serve",
            'reason.replace(request.token, "[token]")',
            f"{PC}::test_token_not_in_verdict_or_stderr",
            "'stop/[token]'",
        ),
        r(
            "reason_cap",
            "act_precheck.serve",
            "if len(reason) > MAX_REASON",
            f"{PC}::test_reason_truncated",
            '"...[truncated]"',
        ),
    ),
    "act_precheck._locked": (
        r(
            "lock_deadline",
            "act_precheck._locked",
            'raise DeadlineExceeded("precheck lock: deadline passed")',
            f"{PC}::test_lock_wait_bounded_by_deadline",
            "seen == []",
        ),
    ),
    "act_precheck.parse_request": (
        r(
            "size",
            "act_precheck.parse_request",
            "if len(raw) > MAX_REQUEST",
            f"{PC}::test_oversize_request_exit_2",
            '"request larger than 65536 bytes"',
        ),
        r(
            "json",
            "act_precheck.parse_request",
            "except (UnicodeDecodeError, json.JSONDecodeError)",
            f"{PC}::test_request_not_json_exit_2",
            '"request is not JSON"',
        ),
        r(
            "object",
            "act_precheck.parse_request",
            "if not isinstance(data, dict)",
            f"{PC}::test_request_not_object_exit_2",
            '"request is not a JSON object"',
        ),
        r(
            "keys",
            "act_precheck.parse_request",
            "if set(data) != set(REQUEST_KEYS)",
            f"{PC}::test_unknown_request_key_exit_2",
            "\"keys: unexpected ['extra'], missing []\"",
        ),
        r(
            "values",
            "act_precheck.parse_request",
            'f"invalid values: {bad}"',
            f"{PC}::test_kind_candidate_rejected_by_schema",
            "\"invalid values: ['kind']\"",
        ),
    ),
    # --- act_precheck (этап 3a, Task 1): минимальный диспетчер v2
    "act_precheck.parse_request_v2": (
        r(
            "surrogates",
            "act_precheck.parse_request_v2",
            "except UnicodeEncodeError",
            f"{V2}::test_lone_surrogates_request_exit_2_api_text_replaced",
            '"request carries lone surrogates"',
        ),
        r(
            "nonce",
            "act_precheck.parse_request_v2",
            """raise InvalidRequest("invalid values: ['nonce']")""",
            f"{PC}::test_v2_bad_nonce_exit_2",
            "\"invalid values: ['nonce']\"",
        ),
    ),
    "act_precheck._no_duplicates": (
        r(
            "duplicate",
            "act_precheck._no_duplicates",
            'f"duplicate keys: {dups}"',
            f"{PC}::test_duplicate_request_key_exit_2",
            "\"duplicate keys: ['nonce']\"",
        ),
    ),
    "act_precheck._no_constant": (
        r(
            "constant",
            "act_precheck._no_constant",
            "raise json.JSONDecodeError",
            f"{PC}::test_request_not_json_exit_2",
            '"request is not JSON"',
        ),
    ),
    "act_precheck._decide": (
        r(
            "deadline",
            "act_precheck._decide",
            "except DeadlineExceeded",
            f"{PC}::test_deadline_kills_git_and_answers_unset",
            '("unset", "deadline")',
        ),
        r(
            "read_failed",
            "act_precheck._decide",
            "except OPS_FAILURES",
            f"{PC}::test_snapshot_failure_is_unset",
            '"read_failed: snapshot: fetch owner/p"',
        ),
    ),
    "act_precheck._stop_target": (
        r(
            "count",
            "act_precheck._stop_target",
            "if len(changes) != 1",
            f"{PC}::test_stop_with_extra_change_refused",
            '"stop_changes: expected one change, got 2"',
        ),
        r(
            "addition",
            "act_precheck._stop_target",
            'if status != "added" or previous is not None',
            f"{PC}::test_stop_modifying_delivered_stop_refused",
            '" is not a plain addition"',
        ),
        r(
            "stop_path",
            "act_precheck._stop_target",
            "if found is None",
            f"{PC}::test_stop_foreign_path_refused_before_io",
            'is not a stop record path"',
        ),
        r(
            "canonical",
            "act_precheck._stop_target",
            "if stop_path(ws_id, node, generation) != path",
            f"{PC}::test_stop_noncanonical_path_refused",
            'is not canonical"',
        ),
        r(
            "branch",
            "act_precheck._stop_target",
            "if parse_stop_branch(head_ref, ws_id) != (node, generation)",
            f"{PC}::test_stop_branch_mismatch_refused",
            '"stop_branch_mismatch: "',
        ),
    ),
    "act_precheck._stop_in_history": (
        r(
            "unreadable",
            "act_precheck._stop_in_history",
            "if raw is None",
            f"{PC}::test_stop_record_absent_at_head_is_unset",
            '"stop_unreadable: "',
        ),
        r(
            "utf8",
            "act_precheck._stop_in_history",
            "except UnicodeDecodeError",
            f"{PC}::test_stop_record_not_utf8_refused",
            '"stop_record_invalid: not UTF-8"',
        ),
        r(
            "record_invalid",
            "act_precheck._stop_in_history",
            "except EnvelopeError",
            f"{PC}::test_stop_record_invalid_refused",
            '"stop_record_invalid: "',
        ),
        r(
            "content",
            "act_precheck._stop_in_history",
            "if (record.node, record.generation) != (target.node, target.generation)",
            f"{PC}::test_stop_path_content_mismatch_refused",
            '"stop_content_mismatch: "',
        ),
        r(
            "history",
            "act_precheck._stop_in_history",
            "if isinstance(history, Unavailable)",
            f"{PC}::test_history_unavailable_is_unset",
            '"history_unavailable: "',
        ),
        r(
            "no_request",
            "act_precheck._stop_in_history",
            "if current is None",
            f"{PC}::test_stop_without_delivered_request_refused",
            '"no_delivered_request: node requirements"',
        ),
        r(
            "already_stopped",
            "act_precheck._stop_in_history",
            "if isinstance(current, DeliveredStop)",
            f"{PC}::test_stop_already_stopped_refused",
            '"already_stopped: node requirements g1"',
        ),
        r(
            "generation",
            "act_precheck._stop_in_history",
            "if delivered != target.generation",
            f"{PC}::test_stop_for_superseded_generation_refused",
            '"stop_generation_not_current: stop g1, current g2"',
        ),
    ),
    "act_anon.contents_bytes": (
        r(
            "blob_oid",
            "act_anon.contents_bytes",
            "содержимое не равно blob",
            f"{A}::test_contents_bytes_checked_against_blob_oid",
        ),
        r(
            "form",
            "act_anon.contents_bytes",
            "not _REPO.fullmatch(repo)",
            f"{A}::test_bad_repo_slug_sends_nothing",
            "t.sent == []",
        ),
        r(
            "unreadable",
            "act_anon.contents_bytes",
            "resp.status != 200 or not isinstance(body, dict)",
            f"{A}::test_contents_404_with_path_in_tree_is_unavailable",
            '"HTTP 404"',
        ),
        r(
            "not_file",
            "act_anon.contents_bytes",
            'body.get("type") != "file"',
            f"{A}::test_contents_not_a_file_is_unavailable",
            '"не файл base64"',
        ),
        r(
            "base64",
            "act_anon.contents_bytes",
            "except (binascii.Error, ValueError)",
            f"{A}::test_contents_not_base64_is_unavailable",
            '"содержимое не base64"',
        ),
    ),
    # отсутствие — только по полному дереву (`None`), иначе отказ (Task 11 ревью)
    "act_anon._locate": (
        r(
            "commit_unreadable",
            "act_anon._locate",
            "resp.status != 200 or not isinstance(sha, str)",
            f"{A}::test_unreadable_commit_is_unavailable",
            'f"HTTP {status}"',
        ),
        r(
            "blob_oid_form",
            "act_anon._locate",
            "blob-oid {name!r} не SHA",
            f"{A}::test_tree_blob_oid_not_sha_is_unavailable",
            "\"blob-oid 'modes.yaml' не SHA\"",
        ),
        r(
            "entry_type",
            "act_anon._locate",
            "запись дерева {name!r} типа {kind!r}",
            f"{A}::test_tree_entry_of_other_type_is_unavailable",
            "\"запись дерева 'policy' типа 'commit'\"",
        ),
    ),
    "act_anon._tree_entry": (
        r(
            "tree_unreadable",
            "act_anon._tree_entry",
            "дерево {tree[:12]}: HTTP {resp.status}",
            f"{A}::test_unreadable_tree_is_unavailable",
            '"HTTP 500"',
        ),
        r(
            "truncated",
            "act_anon._tree_entry",
            'body.get("truncated") is not False',
            f"{A}::test_truncated_tree_is_unavailable_not_absent",
            '"не полностью"',
        ),
    ),
    # --- act_check / act_decision (Task 12)
    "act_check.check_delivery": (
        r(
            "not_merged",
            "act_check.check_delivery",
            "not ctx.delivery.merged",
            f"{C}::test_not_merged_waits",
            "WaitingDelivery()",
        ),
        r(
            "product",
            "act_check._pinned_policy",
            "ctx.request.product != ctx.product",
            f"{C}::test_manifest_product_must_be_delivery_repo",
            "REQUEST_BINDING",
        ),
        r(
            "act_head",
            "act_decision.decide",
            "if act is None",
            f"{C}::test_new_head_after_approve_is_no_act",
            '"d1_no_act"',
        ),
    ),
    "act_check.live_policy_refusal": (
        r(
            "current_activation",
            "act_check.live_policy_refusal",
            "pin.activation_commit == current_activation",
            f"{C}::test_live_pin_against_current_activation",
            "POLICY_CHANGED",
        ),
    ),
    "act_check._admission": (
        r(
            "mode_after_review",
            "act_check._admission",
            "isinstance(admission, Refused)",
            f"{C}::test_unmarked_after_review_mode_refused",
            "ADMISSION",
        ),
        r(
            "marker_before_bootstrap",
            "act_check._admission",
            "ctx.request_act is None",
            f"{C}::test_before_bootstrap_marked_rejected",
            '"marker_before_mode"',
        ),
    ),
    "act_check._epoch_at": (
        r(
            "interepoch_gap",
            "act_check._epoch_at",
            "epoch.valid_until is not None",
            f"{C}::test_interepoch_gap_refused_for_review",
            "EPOCH_GAP",
        ),
    ),
    "act_check._span": (
        r(
            "activation_order",
            "act_check._span",
            "later.at < earlier.at",
            f"{C}::test_activations_out_of_order_unavailable",
            "merged_at активаций противоречит first-parent",
        ),
    ),
    "act_check._pinned_policy": (
        r(
            "activation_pin",
            "act_check._pinned_policy",
            "pin.activation_commit != seen[-1].merge_commit",
            f"{C}::test_policy_changed_before_merge",
            "POLICY_CHANGED",
        ),
        r(
            "mixed_set",
            "act_check._pinned_policy",
            "dict(pin.files_sha256) != dict(policy.files_sha256)",
            f"{C}::test_mixed_policy_refused",
            '"mixed_policy"',
        ),
    ),
    "act_check._decide": (
        r(
            "approver",
            "act_decision.decide",
            "if act is None",
            f"{C}::test_approver_outside_list_rejected",
            '"d1_no_act"',
        ),
        r(
            "dismissal",
            "act_decision.decide",
            "if act is None",
            f"{C}::test_dismissed_before_merge_rejected",
            '"d1_no_act"',
        ),
        r(
            "merger",
            "act_decision.decide",
            "not _merger_ok(policy, login",
            f"{C}::test_agent_outside_merger_list",
            '"d3_merger"',
        ),
        r(
            "bytes",
            "act_decision._delivery_refusal",
            "if at_merge != at_head",
            f"{C}::test_bytes_and_content_are_distinct_refusals",
            '"i_bytes"',
        ),
        r(
            "content",
            "act_decision._delivery_refusal",
            "self_hashes[entry.path] != entry.content_hash",
            f"{C}::test_bytes_and_content_are_distinct_refusals",
            '"i_content"',
        ),
    ),
    "act_check._legacy_verdict": (
        r(
            "legacy_missing",
            "act_check._legacy_verdict",
            "ctx.legacy is None",
            f"{C}::test_old_reader_without_legacy_verdict_unavailable",
            "вердикт старой проверки не вычислен",
        ),
    ),
    # --- act_trust (Task 12a)
    "act_trust.parse_binding": (
        r(
            "transport",
            "act_trust.parse_binding",
            'data["transport"] != "public"',
            f"{T}::test_binding_rejects_bad_text[transport]",
            '"только transport"',
        ),
        r(
            "keys",
            "act_trust.parse_binding",
            "not set(_BINDING_KEYS) <= keys <= set(_BINDING_KEYS) | {HISTORY_KEY}",
            f"{T}::test_binding_rejects_bad_text[keys]",
            '"ключи"',
        ),
        r(
            "ids",
            "act_trust.parse_binding",
            "data[k] <= 0",
            f"{T}::test_binding_rejects_bad_text[ids]",
            '"положительные целые"',
        ),
    ),
    "act_trust.load_binding": (
        r(
            "owner",
            "act_trust.load_binding",
            "st.st_uid != owner_uid",
            f"{T}::test_binding_symlink_or_foreign_owner_refused",
            "не файл root",
        ),
        r(
            "absent",
            "act_trust.load_binding",
            "except FileNotFoundError",
            f"{T}::test_binding_absent_is_none",
            "is None",
        ),
    ),
    "act_trust._get": (
        r(
            "http_status",
            "act_trust._get",
            "resp.status != 200",
            f"{T}::test_branch_head_unreadable_is_unavailable",
            "branches/main: HTTP 500",
        ),
    ),
    "act_trust._named": (
        r(
            "full_name",
            "act_trust._named",
            'body.get("full_name") != expected',
            f"{T}::test_root_substitution_refused[df_trust_name]",
            '"привязка —"',
        ),
        r(
            "public",
            "act_trust._named",
            'body.get("private") is not False',
            f"{T}::test_root_substitution_refused[private_root]",
            '"только публичные"',
        ),
    ),
    "act_trust._walk": (
        r(
            "commit_identity",
            "act_trust._walk",
            'body.get("sha") != sha',
            f"{T}::test_commit_answer_for_other_sha_unavailable",
            "ответ не о том коммите",
        ),
    ),
    # df этап 3a (Task 5): пара `df-trust` по SHA (`act_trust_pair`)
    "act_trust._commit": (
        r(
            "gone",
            "act_trust._commit",
            "resp.status in GONE",
            f"{TP}::test_unreadable_head_objects_history_unverifiable",
            'f"коммит не читается (HTTP {status})"',
        ),
        r(
            "http_status",
            "act_trust._commit",
            "resp.status != 200",
            f"{TP}::test_head_read_temporarily_failing_is_unavailable",
            'f"repos/{DT}/commits/{C2}: HTTP 500"',
        ),
    ),
    "act_trust._source_at": (
        r(
            "absent",
            "act_trust._source_at",
            "if raw is None",
            f"{TP}::test_source_env_invalid_finding[absent]",
            '"нет source.env"',
        ),
        r(
            "parse",
            "act_trust._source_at",
            "except (SourceError, UnicodeDecodeError) as exc",
            f"{TP}::test_source_env_invalid_finding[invalid]",
            '"ожидалась ровно одна"',
        ),
    ),
    "act_trust._journal_versions": (
        r(
            "journal_deleted",
            "act_trust._journal_versions",
            "if versions:",
            f"{T}::test_journal_deleted_after_appearing_unavailable",
            "epochs.yaml удалён",
        ),
        r(
            "journal_parse",
            "act_trust._journal_versions",
            "except (JournalError, UnicodeDecodeError) as exc",
            f"{TP}::test_journal_parse_and_transition_findings",
            '"journal_parse"',
        ),
    ),
    "act_trust._checked_epochs": (
        r(
            "bootstrap_first",
            "act_trust._checked_epochs",
            "first[0].anchor != source.bootstrap_activation",
            f"{T}::test_first_version_must_be_bootstrap",
            "не bootstrap",
        ),
        r(
            "transition",
            "act_trust._checked_epochs",
            "except JournalError",
            f"{T}::test_rewritten_closed_epoch_in_history_unavailable",
            "недопустимый переход журнала",
        ),
        r(
            "bootstrap_on_cold_only",
            "act_trust._checked_epochs",
            "bootstrap and (",
            f"{TP}::test_validation_change_goes_cold",
            '"не bootstrap"',
        ),
    ),
    "act_trust.read_trust_snapshot": (
        r(
            "head_named",
            "act_trust.read_trust_snapshot",
            "len(head) != 40",
            f"{T}::test_branch_head_not_named_is_unavailable",
            '"голова main не прочитана"',
        ),
    ),
    "act_trust.gh_get": (
        r(
            "gh_failed",
            "act_trust.gh_get",
            "done.returncode != 0",
            f"{T}::test_gh_get_failures_are_unavailable",
            "(profile:agent): boom",
        ),
        r(
            "not_json",
            "act_trust.gh_get",
            "except ValueError",
            f"{T}::test_gh_get_failures_are_unavailable",
            "ответ не JSON",
        ),
        r(
            "history_label",
            "act_trust.gh_get",
            "if cred.label == HISTORY_LABEL",
            f"{T}::test_gh_get_refuses_history_profile",
            '"только через транспорт истории"',
        ),
    ),
    # самоотчёт переданных credentials и `profile:history` — один `_report` (Task 11c)
    "act_trust._report_of": (
        r(
            "push_on_policy",
            "act_trust._report_of",
            "isinstance(verdict, Violation)",
            f"{T}::test_token_with_push_records_and_blocks",
            'startswith("env:GH_TOKEN на ")',
        ),
        r(
            "push_on_df_trust",
            "act_trust._report_of",
            "isinstance(verdict, Violation)",
            f"{T}::test_merge_profile_with_push_on_df_trust",
            'startswith("profile:merge на ")',
        ),
        r(
            "unreadable",
            "act_trust._report_of",
            "isinstance(verdict, Unavailable)",
            f"{T}::test_self_report_404_is_unavailable_without_record",
            'startswith("самоотчёт profile:agent")',
        ),
        r(
            "history_push",
            "act_trust._report_of",
            "isinstance(verdict, Violation)",
            f"{T}::test_history_violation_wins_over_passed_credential_gap",
            'startswith(f"profile:history на {DT}: ")',
        ),
    ),
    "act_trust._report": (
        r(
            "first_gap",
            "act_trust._report",
            "isinstance(found, Unavailable) and gap is None",
            f"{T}::test_self_report_404_is_unavailable_without_record",
            'startswith("самоотчёт profile:agent")',
        ),
    ),
    "act_trust._self_reports": (
        r(
            "violation_wins",
            "act_trust._self_reports",
            "own if isinstance(own, tuple) or found is None else found",
            f"{T}::test_history_violation_wins_over_passed_credential_gap",
            'startswith(f"profile:history на {DT}: ")',
        ),
    ),
    "act_trust._creds_refusal": (
        r(
            "shape",
            "act_trust._creds_refusal",
            "_CRED_SHAPES.get(cred.label) != shape",
            f"{T}::test_caller_history_credential_refused_before_io",
            "act_trust.HISTORY_NOT_WIRED",
        ),
    ),
    "act_trust._shared_profile_refusal": (
        r(
            "shared",
            "act_trust._shared_profile_refusal",
            "cred.gh_config_dir.resolve() == own",
            f"{T}::test_profile_shared_with_history_refused_before_io",
            "act_trust.HISTORY_PROFILE_SHARED",
        ),
    ),
    "act_trust._auth_token": (
        r(
            "hosts",
            "act_trust._auth_token",
            'not (config_dir / "hosts.yml").is_file()',
            f"{T}::test_history_token_failures_are_unavailable[no_hosts]",
            '"профиль токена истории"',
        ),
        r(
            "spawn",
            "act_trust._auth_token",
            "except (OSError, subprocess.TimeoutExpired) as exc",
            f"{T}::test_history_token_failures_are_unavailable[oserror]",
            '"gh auth token (profile:history): OSError"',
        ),
        r(
            "exit",
            "act_trust._auth_token",
            "if done.returncode != 0",
            f"{T}::test_history_token_failures_are_unavailable[exit]",
            '"gh auth token (profile:history): код выхода 1"',
        ),
        r(
            "empty",
            "act_trust._auth_token",
            "if not token",
            f"{T}::test_history_token_failures_are_unavailable[empty]",
            '"gh auth token (profile:history): пустой вывод"',
        ),
        r(
            "form",
            "act_trust._auth_token",
            "if not _TOKEN_FORM.fullmatch(token)",
            f"{T}::test_history_token_failures_are_unavailable[form]",
            '"gh auth token (profile:history): вывод не в форме токена"',
        ),
    ),
    "act_trust.history_token_at": (
        r(
            "absolute",
            "act_trust.history_token_at",
            "if not gh.is_absolute()",
            f"{T}::test_missing_gh_refused_no_path_fallback[relative]",
            '"gh auth token (profile:history): путь gh не абсолютный"',
        ),
        r(
            "call_dir",
            "act_trust.history_token_at",
            "except OSError as exc",
            f"{T}::test_call_dir_unavailable_is_unavailable",
            '"gh auth token (profile:history): каталог вызова: FileNotFoundError"',
        ),
    ),
    "act_trust.history_transport": (
        r(
            "token_failed",
            "act_trust.history_transport",
            "isinstance(token, Unavailable)",
            f"{T}::test_broken_history_token_no_anonymous_fallback",
            'f"токен истории: {why}"',
        ),
        r(
            "token_form",
            "act_trust.history_transport",
            "not _TOKEN_FORM.fullmatch(token)",
            f"{T}::test_token_source_value_not_token_form_refused",
            '"токен истории: значение не в форме токена"',
        ),
    ),
    "act_trust.transport_fact": (
        r(
            "status",
            "act_trust.transport_fact",
            "resp.status != 200 or not isinstance(resp.body, dict)",
            f"{T}::test_transport_fact_failure_is_unavailable",
            '"GET repos/o/p (profile:history): HTTP 403"',
        ),
    ),
    "act_trust._rulesets": (
        r(
            "first_wins",
            "act_trust._rulesets",
            "if found is not None",
            f"{T}::test_ruleset_without_pull_request_is_violation",
            '"нет правил"',
        ),
    ),
    "act_trust._ruleset_of": (
        r(
            "pull_request_rule",
            "act_trust._ruleset_of",
            "missing := sorted(REQUIRED_RULES - kinds)",
            f"{T}::test_ruleset_without_pull_request_is_violation",
            '"нет правил"',
        ),
    ),
    "act_trust._rebase": (
        r(
            "rebase_df_trust",
            "act_trust._rebase",
            "dict(_roles(snap))[role]",
            f"{TP}::test_df_trust_rebase_allowed_finding",
            '"rebase_allowed", "df-trust"',
        ),
        r(
            "rebase_true",
            "act_trust._rebase",
            "if value is True",
            f"{T}::test_rebase_true_is_violation",
            '"allow_rebase_merge = true"',
        ),
        r(
            "rebase_hidden",
            "act_trust._rebase",
            "if value is None",
            f"{T}::test_rebase_hidden_is_unverified_premise",
            '("allow_rebase_merge",)',
        ),
    ),
    "act_trust._register": (
        r(
            "violation_event",
            "act_trust._register",
            "нарушение периметра: {detail}",
            f"{T}::test_token_with_push_records_and_blocks",
            '"нарушение периметра: "',
        ),
    ),
    "act_trust._snapshot_for_decision": (
        r(
            "blocked",
            "act_trust._snapshot_for_decision",
            'state != "clear"',
            f"{T}::test_blocked_journal_blocks",
            '"периметр заблокирован"',
        ),
        r(
            "stale",
            "act_trust._snapshot_for_decision",
            "state == STALE",
            f"{T}::test_stale_snapshot_reloads",
            '["clear"]',
        ),
    ),
    "act_trust.load_trust": (
        r(
            "violation_wins",
            "act_trust.load_trust",
            "isinstance(found, tuple)",
            f"{T}::test_violation_wins_over_other_unavailable",
            '"df-trust"',
        ),
    ),
    "act_trust.policy_set": (
        r(
            "bytes_vs_activation",
            "act_trust.policy_set",
            "hashlib.sha256(raw).hexdigest() != digest",
            f"{T}::test_policy_bytes_not_matching_activation_is_mixing",
            '"смешение версий"',
        ),
        r(
            "actors_file",
            "act_trust.policy_set",
            "if digest is None",
            f"{T}::test_activation_without_actors_file_unavailable",
            '": нет policy/actors.yaml"',
        ),
    ),
    "act_trust.load_ranges": (
        r(
            "history_violation",
            "act_trust.load_ranges",
            "isinstance(got, act_facts.HistoryBreak)",
            f"{T}::test_history_violation_records_event",
            '"нарушение периметра"',
        ),
        r(
            "unknown_anchor",
            "act_trust.load_ranges",
            "якорь эпохи {epoch.epoch}",
            f"{T}::test_unknown_anchor_time_makes_range_unavailable",
            '"якорь эпохи 1: object_missing"',
        ),
    ),
    "act_trust._modes": (
        r(
            "unread_set",
            "act_trust._modes",
            "набор {a.merge_commit[:12]}",
            f"{T}::test_mode_history_with_unread_set_is_unavailable",
            'f"набор {views[1].merge_commit[:12]}"',
        ),
    ),
    "act_trust.product_mode": (
        r(
            "never_entered",
            "act_trust.product_mode",
            "if not entered",
            f"{T}::test_mode_never_entered_is_pre_migration",
            "PRE_MIGRATION",
        ),
        r(
            "removed",
            "act_trust.product_mode",
            'tail[-1] == ""',
            f"{T}::test_mode_removed_or_rolled_back_is_unavailable[merge_removed]",
            '"удалён после первой записи"',
        ),
        r(
            "rolled_back",
            "act_trust.product_mode",
            '"review" in tail',
            f"{T}::test_mode_removed_or_rolled_back_is_unavailable[rolled_back]",
            '"обратное переключение review → merge"',
        ),
        r(
            "prior_epoch",
            "act_trust.product_mode",
            "история режима, эпоха",
            f"{T}::test_mode_needs_prior_epoch_unless_review",
            '"история режима, эпоха 1"',
        ),
    ),
    "act_trust.current_activation": (
        r(
            "open_epoch",
            "act_trust.current_activation",
            "if not span",
            f"{T}::test_current_activation_is_last_of_open_epoch_or_unavailable",
            '"в открытой эпохе нет активаций"',
        ),
    ),
    # --- журналы периметра
    "act_journal.ForgeEpochs.head": (
        r(
            "root",
            "act_journal.ForgeEpochs.head",
            "snap.root.id_tuple != tuple(root)",
            f"{J}::test_forge_epochs_refuse_other_root",
            '"root_mismatch"',
        ),
    ),
    "act_journal._status_of": (
        r(
            "snapshot_identity",
            "act_journal._status_of",
            "ответ не о снимке решения",
            f"{J}::test_status_answer_about_other_snapshot_unavailable",
        ),
        r(
            "state",
            "act_journal._status_of",
            'state not in ("blocked", "clear")',
            f"{J}::test_status_answer_about_other_snapshot_unavailable",
            '"периметр: x"',
        ),
    ),
    "act_journal.DevJournal._open": (
        r(
            "store",
            "act_journal.DevJournal._open",
            "except (store.Unavailable, OSError)",
            f"{J}::test_corrupted_store_blocks",
            '"журнал периметра (dev): "',
        ),
    ),
    "act_journal.DevJournal.status": (
        r(
            "stale",
            "act_journal._status_of",
            '"stale_snapshot"',
            f"{J}::test_stale_snapshot_reported",
            "act_trust.STALE",
        ),
        r(
            "lost",
            "act_journal.DevJournal._open",
            "except (store.Unavailable, OSError)",
            f"{J}::test_deleted_store_with_marker_is_lost",
            '"lost"',
        ),
    ),
    "act_journal.DevJournal.record": (
        r(
            "store_refused",
            "act_journal.DevJournal.record",
            "except store.Unavailable as exc",
            f"{J}::test_dev_record_refused_by_store_is_unavailable",
            '"журнал периметра (dev): integrity"',
        ),
    ),
    "act_journal.InstalledJournal.status": (
        r(
            "df_run",
            "act_journal.journal_for",
            "if not df_run",
            f"{J}::test_installed_journal_requires_df_run",
            '"DF_RUN_ID"',
        ),
    ),
    "act_journal.InstalledJournal.record": (
        r(
            "refused",
            "act_journal.InstalledJournal.record",
            'resp.outcome != "done"',
            f"{J}::test_installed_record_refused_is_unavailable",
            '"record-violation: refused: busy"',
        ),
    ),
    "act_journal.journal_for": (
        r(
            "df_run",
            "act_journal.journal_for",
            "if not df_run",
            f"{J}::test_installed_journal_requires_df_run",
            '"DF_RUN_ID"',
        ),
    ),
    "act_journal_wire.WireJournal._call": (
        r(
            "garbled",
            "act_journal_wire.WireJournal._call",
            'resp.get("schema") != RESPONSE_SCHEMA',
            f"{W}::test_wire_garbled_or_refused_answers_unavailable",
            '"ответ издателя не разобран"',
        ),
    ),
    "act_journal_wire.WireJournal.status": (
        r(
            "snapshot_identity",
            "act_journal_wire.WireJournal.status",
            "ответ не о снимке решения",
            f"{W}::test_wire_stale_and_foreign_snapshot",
        ),
    ),
    "act_journal_wire.WireJournal.record": (
        r(
            "refused",
            "act_journal_wire.WireJournal.record",
            'resp.get("outcome") != "done"',
            f"{W}::test_wire_garbled_or_refused_answers_unavailable",
            '"record-violation: refused: busy"',
        ),
    ),
    "act_journal_wire.DevStoreJournal._open": (
        r(
            "restore",
            "act_journal_wire.DevStoreJournal._open",
            "os.path.lexists(self.base / RESTORE_INTENT)",
            f"{W}::test_restore_intent_and_corruption_unavailable",
            '"restore_incomplete"',
        ),
        r(
            "missing",
            "act_journal_wire.DevStoreJournal._open",
            "except FileNotFoundError",
            f"{W}::test_missing_store_is_unavailable",
            '"не создан"',
        ),
    ),
    "act_journal_wire.DevStoreJournal.status": (
        r(
            "blocking",
            "act_journal_wire.DevStoreJournal.status",
            '"blocked" if rows else "clear"',
            f"{W}::test_narrow_pending_blocks_and_full_journal_completes_it",
            '"blocked"',
        ),
    ),
    "act_journal_wire.DevStoreJournal.record": (
        r(
            "insert_failed",
            "act_journal_wire.DevStoreJournal.record",
            "except sqlite3.Error as exc",
            f"{W}::test_record_insert_failure_is_unavailable",
            '"событие не записано"',
        ),
    ),
    "act_journal_wire._publish": (
        r(
            "publisher_only",
            "act_journal_wire._publish",
            "argv[:5] != prefix or len(argv) != 6 or argv[5] not in OPERATIONS",
            f"{W}::test_publish_refuses_anything_but_the_publisher",
            '"не вызов издателя"',
        ),
    ),
    # проверки `_verify` издателя на том же файле (ревью #666); детектор формы их не
    # видит (отказ — строка-код), классифицировано по правилу
    "act_journal_wire._refusal": (
        r(
            "schema",
            "act_journal_wire._refusal",
            "schema_digest(conn) != SCHEMA_DIGEST",
            f"{W}::test_both_clients_refuse_the_same_store[schema]",
            '"integrity"',
        ),
        r(
            "meta",
            "act_journal_wire._refusal",
            'meta["schema"] != SCHEMA_VERSION',
            f"{W}::test_both_clients_refuse_the_same_store[meta]",
            '"integrity"',
        ),
        r(
            "invariant",
            "act_journal_wire._refusal",
            "for sql in _INVARIANTS",
            f"{W}::test_both_clients_refuse_the_same_store[invariant]",
            '"integrity"',
        ),
        r(
            "observation_json",
            "act_journal_wire._refusal",
            "not _observations_ok(conn)",
            f"{W}::test_both_clients_refuse_the_same_store[observation_event]",
            '"integrity"',
        ),
        r(
            "max_events",
            "act_journal_wire._refusal",
            "> MAX_EVENTS",
            f"{W}::test_both_clients_refuse_the_same_store[max_events]",
            '"too_many_events"',
        ),
    ),
    # JSON строк наблюдений — копия `perimeter._observations_ok` (схема 2, Task 8)
    "act_journal_wire._observations_ok": (
        r(
            "observation_type",
            "act_journal_wire._observations_ok",
            "not isinstance(ids, list)",
            f"{W}::test_both_clients_refuse_the_same_store[observation_type]",
            '"integrity"',
        ),
        r(
            "observation_event",
            "act_journal_wire._observations_ok",
            "i not in events",
            f"{W}::test_both_clients_refuse_the_same_store[observation_event]",
            '"integrity"',
        ),
        r(
            "observation_binding",
            "act_journal_wire._observations_ok",
            "binding.get(k)) is not int",
            f"{W}::test_both_clients_refuse_the_same_store[observation_binding]",
            '"integrity"',
        ),
    ),
    "act_journal_wire.journal_for_premerge": (
        r(
            "df_run",
            "act_journal_wire.journal_for_premerge",
            "if not df_run",
            f"{W}::test_premerge_selection_follows_publisher_presence",
            '"DF_RUN_ID"',
        ),
    ),
    # --- act_context
    "act_context.delivery_verdict": (
        r(
            "not_merged",
            "act_context.delivery_verdict",
            "delivery.merged_at is None",
            f"{X}::test_not_merged_waits_without_reading_ranges",
            "WaitingDelivery()",
        ),
        r(
            "legacy_only_unmarked",
            "act_context.delivery_verdict",
            "if request is None and legacy is not None",
            f"{X}::test_legacy_computed_only_for_unmarked",
            "calls == [1]",
        ),
    ),
    # --- act_identity
    "act_identity.manifest_refusal": tuple(
        r(
            c,
            "act_identity.manifest_refusal",
            "if got != want",
            f"{ID}::test_each_identity_component_is_checked[{c}]",
            'f"манифест: {field} other"',
        )
        for c in ("product", "ws_id", "request_id")
    ),
    "act_identity.paths_refusal": (
        r(
            "node_in_dag",
            "act_identity.paths_refusal",
            "if want is None",
            f"{ID}::test_node_outside_dag_and_wrong_path",
            '"не из активного DAG"',
        ),
        r(
            "node_path",
            "act_identity.paths_refusal",
            "if entry.path != want",
            f"{ID}::test_node_outside_dag_and_wrong_path",
            '"путь узла"',
        ),
    ),
    "act_identity.head_manifest_refusal": (
        r(
            "in_head",
            "act_identity.head_manifest_refusal",
            "if text_at_head is None",
            f"{ID}::test_manifest_only_in_delivery_or_changed",
            '"не в одобренном head"',
        ),
        r(
            "bytes",
            "act_identity.head_manifest_refusal",
            "if text_at_head != text_delivered",
            f"{ID}::test_manifest_only_in_delivery_or_changed",
            '"≠"',
        ),
    ),
    # --- act_history (Task 14; вынесено из act_status — этап 2b, Task 3)
    "act_history.pr_for_merge": (
        r(
            "exactly_one",
            "act_history.pr_for_merge",
            "len(found) != 1",
            f"{S}::test_pr_for_merge_needs_exactly_one",
            '"нужен ровно один"',
        ),
        r(
            "ops_failure",
            "act_history.pr_for_merge",
            "except OPS_FAILURES",
            f"{OF}::test_pr_for_merge_failure_is_unavailable[CalledProcessError]",
            '"PR merge-коммита"',
        ),
    ),
    "act_history._pr_head": (
        r(
            "head_fetched",
            "act_history._pr_head",
            "не догружена",
            f"{S}::test_history_fetches_squash_head_absent_locally",
        ),
        r(
            "ops_failure",
            "act_history._pr_head",
            "except OPS_FAILURES",
            f"{OF}::test_pr_head_failure_is_unavailable[CalledProcessError]",
            '"голова PR #7"',
        ),
    ),
    # событие-заявка разбирается в `_request_event` (вынесено при Task 14)
    "act_history._request_event": (
        r(
            "request:path",
            "act_history._request_event",
            "manifest_path(req.ws_id, req.request_id) != path",
            f"{S}::test_history_manifest_off_its_path_unavailable[ws_id]",
            '"манифест лежит не по своему пути"',
        ),
        r(
            "request:no_pr",
            "act_history._request_event",
            "isinstance(pr, Unavailable)",
            f"{S}::test_history_manifest_commit_without_pr_unavailable",
            '"нужен ровно один"',
        ),
    ),
    "act_history._event": (
        r(
            "immutable",
            "act_history._event",
            'if status != "A"',
            f"{S}::test_manifest_edited_after_delivery_unavailable",
            '"запись истории изменена после доставки"',
        ),
        r(
            "stop:ws_id",
            "act_history._event",
            "if not path.startswith((requests_dir, stops_dir))",
            f"{S}::test_history_stop_of_other_workstream_ignored",
            "== ()",
        ),
        r(
            "stop:path",
            "act_history._event",
            "остановка лежит не по своему пути",
            f"{S}::test_history_stop_off_its_path_unavailable[node]",
        ),
    ),
    "act_history.load_history": (
        r(
            "base_unread",
            "act_history.load_history",
            "if rows is None",
            f"{S}::test_history_unreadable_base_unavailable",
            '"история master: first-parent не прочитан"',
        ),
    ),
    # --- act_status (Task 14)
    "act_status.Decider._binding": (
        r(
            "identity",
            "act_status.Decider._binding",
            "if identity is not None",
            f"{S}::test_decider_refuses_unbound_manifest[foreign_ws]",
            '"манифест: ws_id"',
        ),
        r(
            "head_manifest",
            "act_status.Decider._binding",
            "if copy is not None",
            f"{S}::test_decider_refuses_unbound_manifest[absent_in_head]",
            '"не в одобренном head"',
        ),
    ),
    "act_status.Decider._pins_refusal": (
        r(
            "upstream_pins",
            "act_status.Decider._pins_refusal",
            "dict(req.upstream_pins[node]) != fact.value",
            f"{S}::test_decider_refuses_unbound_manifest[pins_changed]",
            '"upstream-пины"',
        ),
    ),
    "act_status.Decider.__call__": (
        r(
            "memo_key",
            "act_status.Decider.__call__",
            "key = (*ident.memo_key, event.merge_commit)",
            f"{S}::test_decider_memo_key_carries_full_identity",
            "decider._memo",
        ),
        r(
            "memo_request",
            "act_status.Decider.__call__",
            "key = (*ident.memo_key, event.merge_commit)",
            f"{S}::test_decider_memo_is_per_request_not_per_commit[good_first]",
            "act_status.REQUEST_BINDING",
        ),
    ),
    "act_status._load_journal": (
        r(
            "binding",
            "act_status._load_journal",
            "if binding is None",
            f"{S}::test_decider_without_binding_trust_unavailable",
            '"нет привязки корня доверия (§6.1 п. 1)"',
        ),
        r(
            "journal_unset",
            "act_status._load_journal",
            "if deps.journal is None",
            f"{S}::test_decider_without_journal_unavailable",
            '"журнал периметра не задан"',
        ),
    ),
    "act_status._settings": (
        r(
            "settings",
            "act_status._settings",
            "except SettingsError as exc",
            f"{S}::test_bad_settings_is_a_named_refusal",
            '"режим акта продукта: настройки акта: "',
        ),
    ),
    "act_status.Decider._head_hashes": (
        r(
            "head_bytes",
            "act_status.Decider._head_hashes",
            "if text is None",
            f"{S}::test_decider_head_bytes_unreadable_unavailable",
            "не читается",
        ),
    ),
    "act_status.Decider._decide": (
        r(
            "merge_commit",
            "act_status.Decider._decide",
            "delivery.merge_commit != event.merge_commit",
            f"{S}::test_decider_delivery_of_other_merge_commit_unavailable",
            "доставил",
        ),
        r(
            "head_sha",
            "act_status.Decider._decide",
            "head_sha=event.head_sha",
            f"{S}::test_decider_judges_review_on_event_head",
            '"h" * 40',
        ),
    ),
    "act_status.Decider.legacy": (
        r(
            "contract_result",
            "act_status.Decider.legacy",
            "legacy=contract",
            f"{L}::test_decider_legacy_returns_contract_result",
            "is refused",
        ),
    ),
    "act_status.candidate_identity": (
        r(
            "ssot_roundtrip",
            "act_status.candidate_identity",
            "round_trip != branch",
            f"{L}::test_candidate_identity_round_trips_ssot_form",
            'act_status.candidate_identity("spec/ws-7-approve-01-0-1") is None',
        ),
    ),
    "act_status.legacy_contract_refusal": (
        r(
            "pending",
            "act_status.legacy_contract_refusal",
            "return NOT_PENDING",
            f"{L}::test_node_not_pending_in_candidate_refused",
            "act_status.NOT_PENDING",
        ),
        r(
            "bytes",
            "act_status.legacy_contract_refusal",
            "return BYTES",
            f"{L}::test_node_bytes_changed_by_finalize_refused",
            "act_status.BYTES",
        ),
        r(
            "hash_field",
            "act_status.legacy_contract_refusal",
            "return HASH_FIELD",
            f"{L}::test_hash_field_not_matching_base_bytes_refused",
            "act_status.HASH_FIELD",
        ),
        r(
            "pins",
            "act_status.legacy_contract_refusal",
            "return PINS",
            f"{L}::test_signature_under_changed_pins_refused",
            "act_status.PINS",
        ),
    ),
    "act_status.LegacyProbe.__call__": (
        r(
            "edit_in_history",
            "act_status.LegacyProbe.__call__",
            "_no(NO_EDIT",
            f"{L}::test_no_edit_of_node_in_history_refused",
            "act_status.NO_EDIT",
        ),
        r(
            "signature_commit",
            "act_status.LegacyProbe.__call__",
            "_no(NOT_SIGNATURE",
            f"{L}::test_frontmatter_signature_differs_from_last_edit",
            "act_status.NOT_SIGNATURE",
        ),
    ),
    "act_status.LegacyProbe._finalize_branch": (
        r(
            "via_pr",
            "act_status.LegacyProbe._finalize_branch",
            "_no(NOT_VIA_PR",
            f"{L}::test_signature_not_through_pr_refused",
            "act_status.NOT_VIA_PR",
        ),
        r(
            "finalize_form",
            "act_status.LegacyProbe._finalize_branch",
            "_no(NOT_FINALIZE",
            f"{L}::test_signature_without_finalize_pr_refused",
            "act_status.NOT_FINALIZE",
        ),
        r(
            "ops_failure",
            "act_status.LegacyProbe._finalize_branch",
            "except OPS_FAILURES",
            f"{OF}::test_legacy_finalize_branch_failure_unavailable[CalledProcessError]",
            '"unavailable:PR коммита подписи"',
        ),
    ),
    "act_status.LegacyProbe._candidate": (
        r(
            "ws_id",
            "act_status.LegacyProbe._candidate",
            "FOREIGN_WS,",
            f"{L}::test_finalize_of_other_workstream_refused",
            "act_status.FOREIGN_WS",
        ),
        r(
            "ledger_record",
            "act_status.LegacyProbe._candidate",
            "NO_LEDGER,",
            f"{L}::test_ledger_record_binds_candidate_and_node[absent]",
            "act_status.NO_LEDGER",
        ),
        r(
            "ledger_candidate",
            "act_status.LegacyProbe._candidate",
            "LEDGER_CANDIDATE,",
            f"{L}::test_ledger_record_binds_candidate_and_node[other_candidate]",
            "act_status.LEDGER_CANDIDATE",
        ),
        r(
            "ledger_node",
            "act_status.LegacyProbe._candidate",
            "_no(LEDGER_NODE",
            f"{L}::test_ledger_record_binds_candidate_and_node[node_absent]",
            "act_status.LEDGER_NODE",
        ),
    ),
    "act_status.LegacyProbe._merged_candidate": (
        r(
            "candidate_exists",
            "act_status.LegacyProbe._merged_candidate",
            "_no(NO_CANDIDATE",
            f"{L}::test_candidate_absent_or_not_matching_signature[absent]",
            "act_status.NO_CANDIDATE",
        ),
        r(
            "candidate_merged",
            "act_status.LegacyProbe._merged_candidate",
            "_no(CANDIDATE_NOT_MERGED",
            f"{L}::test_candidate_absent_or_not_matching_signature[not_merged]",
            "act_status.CANDIDATE_NOT_MERGED",
        ),
        r(
            "candidate_merger",
            "act_status.LegacyProbe._merged_candidate",
            "_no(CANDIDATE_MERGER",
            f"{L}::test_candidate_absent_or_not_matching_signature[other_merger]",
            "act_status.CANDIDATE_MERGER",
        ),
        r(
            "candidate_time",
            "act_status.LegacyProbe._merged_candidate",
            "_no(CANDIDATE_TIME",
            f"{L}::test_candidate_absent_or_not_matching_signature[other_time]",
            "act_status.CANDIDATE_TIME",
        ),
        r(
            "ops_failure",
            "act_status.LegacyProbe._merged_candidate",
            "except OPS_FAILURES",
            f"{OF}::test_legacy_merged_candidate_pr_facts_failure_unavailable[CalledProcessError]",
            '"unavailable:candidate cand"',
        ),
    ),
    "act_status.LegacyProbe._offered": (
        r(
            "node_in_candidate",
            "act_status.LegacyProbe._offered",
            "_no(NOT_IN_CANDIDATE",
            f"{L}::test_node_not_in_candidate_refused",
            "act_status.NOT_IN_CANDIDATE",
        ),
    ),
    "act_status.LegacyProbe._verdict": (
        r(
            "admission",
            "act_status.LegacyProbe._verdict",
            'f"legacy_admission:{verdict.code}"',
            f"{L}::test_candidate_delivered_after_activation_refused",
            '"legacy_admission:"',
        ),
    ),
    "act_status.LegacyProbe._verdict.contract": (
        r(
            "byte_contract",
            "act_status.LegacyProbe._verdict.contract",
            "if refusal is not None",
            f"{L}::test_node_bytes_changed_by_finalize_refused",
            "act_status.BYTES",
        ),
    ),
    "act_status.Statuses.of": (
        r(
            "memo_node",
            "act_status.Statuses.of",
            "if node not in self._memo",
            f"{S}::test_statuses_memo_is_per_node[approved_first]",
            '"нет конверта"',
        ),
    ),
    "act_status.Statuses.is_review": (
        r(
            "no_event",
            "act_status.Statuses.is_review",
            "current_event(self.history, node) is not None",
            f"{S}::test_legacy_node_needs_provenance_not_frontmatter",
            "False,\n        True,",
        ),
    ),
    "act_status.Statuses._legacy": (
        r(
            "debt_first",
            "act_status.Statuses._legacy",
            "if debt is not None",
            f"{S}::test_legacy_probe_not_called_for_debt_node",
            '"not_approved:debt_status"',
        ),
    ),
    "act_envelope.node_history": (  # п. 9 (Б), решение владельца 01.11
        r(
            "stop_generation",
            "act_envelope.node_history",
            "isinstance(event, DeliveredStop) and (gen != last or last == 0)",
            f"{E}::test_stop_of_other_generation_is_noop",
            '"узел req: остановка поколения 1, действует 2"',
        ),
        r(
            "stop_without_request",
            "act_envelope.node_history",
            "isinstance(event, DeliveredStop) and (gen != last or last == 0)",
            f"{E}::test_stop_before_any_request_ignored",
            '"узел req: остановка поколения 1, действует 0"',
        ),
        r(
            "request_generation",
            "act_envelope.node_history",
            "isinstance(event, DeliveredRequest) and gen <= last",
            f"{E}::test_non_monotonic_request_ignored",
            '"узел req: поколение 2 не больше прежнего 2"',
        ),
    ),
    "act_status.fresh_base": (
        r(
            "snapshot_unavailable",
            "act_status.fresh_base",
            "fact.outcome is not FactOutcome.FOUND or fact.value is None",
            f"{RN}::test_admission_refused_when_base_not_refreshed[s4]",
            '"no_success"',
        ),
        r(
            "closed",
            "act_status.fresh_base",
            "ops.snapshot_close(repo)",
            f"{S}::test_snapshot_removed_on_exception",
            "Path(seen[0]).exists() is False",
        ),
    ),
    "ops.RealOps._snapshot_url": (
        r(
            "canonical",
            "ops.RealOps._snapshot_url",
            'f"https://github.com/{repo_slug}.git"',
            f"{S}::test_production_snapshot_command_is_isolated",
            '"https://github.com/owner/p.git"',
        ),
    ),
    "ops.RealOps._credential_helper": (
        r(
            "gh_profile",
            "ops.RealOps._credential_helper",
            '"!gh auth git-credential"',
            f"{S}::test_production_snapshot_command_is_isolated",
            '"credential.helper=!gh auth git-credential"',
        ),
    ),
    "ops.RealOps._snapshot_env": (  # d5: изолированное окружение git снимка
        r(
            "allowlist",
            "ops.RealOps._snapshot_env",
            "for k in _SNAPSHOT_ENV_KEEP if k in os.environ",
            f"{S}::test_injected_git_config_and_env_have_no_effect",
            "STOPPED",
        ),
        r(
            "no_global",
            "ops.RealOps._snapshot_env",
            "GIT_CONFIG_GLOBAL=os.devnull",
            f"{S}::test_production_snapshot_command_is_isolated",
            '"0", str(profile)',
        ),
        r(
            "no_prompt",
            "ops.RealOps._snapshot_env",
            'GIT_TERMINAL_PROMPT="0"',
            f"{S}::test_production_snapshot_command_is_isolated",
            '"0", str(profile)',
        ),
        r(
            "empty_home",
            "ops.RealOps._snapshot_env",
            "HOME=str(home)",
            f"{S}::test_foreign_home_credentials_have_no_effect",
            "snapshot_home == expected",
        ),
        r(
            "profile_resolved",
            "ops.RealOps._snapshot_env",
            "profile.expanduser().resolve()",
            f"{S}::test_foreign_home_credentials_have_no_effect",
            "snapshot_home == expected",
        ),
        r(
            "runner_profile",
            "ops.RealOps._snapshot_env",
            'os.environ.get("REVIEW_GH_CONFIG_DIR")',
            f"{S}::test_production_snapshot_command_is_isolated",
            '"0", str(profile)',
        ),
    ),
    "ops.RealOps._snapshot_git": (
        r(
            "helper_reset",
            "ops.RealOps._snapshot_git",
            '"credential.helper="',
            f"{S}::test_production_snapshot_command_is_isolated",
            '"credential.helper=!gh auth git-credential"',
        ),
    ),
    "ops.SnapshotOps.__getattr__": (
        r(
            "deny",
            "ops.SnapshotOps.__getattr__",
            "if name in SNAPSHOT_FORGE_METHODS",
            f"{S}::test_snapshot_ops_denies_unlisted_methods[blob_in_commit]",
            "не разрешён",
        ),
    ),
    "ops.RealOps.snapshot_open": (  # d3–d5: свежий изолированный снимок базы продукта
        r(
            "slug_shape",
            "ops.RealOps.snapshot_open",
            "not _REPO_SLUG.fullmatch(repo_slug)",
            f"{S}::test_snapshot_rejects_non_slug_source[local_path]",
            "не owner/repo",
        ),
        r(
            "fetch_failed",
            "ops.RealOps.snapshot_open",
            "done.returncode != 0",
            f"{S}::test_noop_refusal_refuses_unfetchable_product",
            '"свежая база: снимок owner/p@master: fetch owner/p master: код 128"',
        ),
        r(
            "own_repo",
            "ops.RealOps.snapshot_open",
            'tempfile.mkdtemp(prefix="act-snapshot-")',
            f"{S}::test_interleaved_snapshots_are_independent",
            "len(set(dirs)) == 2",
        ),
        r(
            "refmap",
            "ops.RealOps.snapshot_open",
            "*_SNAPSHOT_FETCH, url",
            f"{S}::test_snapshot_leaves_product_repo_untouched",
            "_refs(clone) == refs_before",
        ),
    ),
    "act_status.noop_refusal": (  # В-1, d1, d2 — решение владельца 01.11
        r(
            "stale_base",
            "act_status.noop_refusal",
            "isinstance(snapshot, Unavailable)",
            f"{RN}::test_admission_refused_when_base_not_refreshed[s8]",
            '"no_success"',
        ),
        r(
            "fresh_ref",
            "act_status.noop_refusal",
            "ref=ref",
            f"{RN}::test_stale_clone_remote_stop_end_to_end[next_wave]",
            '"no_success"',
        ),
        r(
            "history_snapshot",
            "act_status._statuses",
            "load_history(ops, state, snapshot)",
            f"{S}::test_noop_refusal_reads_fresh_product_base",
            "STOPPED",
        ),
        r(
            "legacy_snapshot",
            "act_status._statuses",
            "LegacyProbe(state, ops, snapshot, decider.legacy, dag)",
            f"{S}::test_admission_reads_only_the_snapshot",
            "node_reads == snapshot_refs",
        ),
        r(
            "mode_unset",
            "act_status.noop_refusal",
            "isinstance(live, Unavailable)",
            f"{TB}::test_review_noop_refused_when_mode_unset",
            '"бесследного no-op нет (режим акта продукта: нет привязки)"',
        ),
        r(
            "stopped",
            "act_status.noop_refusal",
            "verdict != APPROVED",
            f"{TB}::test_review_noop_refused_without_fresh_approval[stop_delivered]",
            '"not_approved:stopped:g1"',
        ),
        r(
            "act_revoked",
            "act_status.noop_refusal",
            "verdict != APPROVED",
            f"{TB}::test_review_noop_refused_without_fresh_approval[act_revoked]",
            '"not_approved:act:d1_no_act"',
        ),
        r(
            "unavailable",
            "act_status.noop_refusal",
            "verdict != APPROVED",
            f"{TB}::test_review_noop_refused_without_fresh_approval[trust_unavailable]",
            '"unavailable:df-trust: HTTP 403"',
        ),
    ),
    "task_bridge._require_admission": (
        r(
            "refused",
            "task_bridge._require_admission",
            "if refusal is not None",
            f"{TB}::test_stored_success_requires_admission[returned_pr]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "act_status.Statuses._compute": (
        r(
            "stop",
            "act_status.Statuses._compute",
            "isinstance(current, DeliveredStop)",
            f"{S}::test_stop_makes_node_not_approved",
            '"not_approved:stopped:g1"',
        ),
        r(
            "upstream",
            "act_status.Statuses._compute",
            "if status != APPROVED",
            f"{S}::test_stopped_upstream_with_unchanged_bytes_blocks_downstream",
            '"not_approved:upstream_stopped:requirements"',
        ),
        r(
            "delivery_verdict",
            "act_status.Statuses._compute",
            "not isinstance(verdict, Accepted)",
            f"{S}::test_rejected_delivery_not_approved",
            '"not_approved:act:d1_no_act"',
        ),
    ),
    "act_status.Statuses._envelope": (
        *(
            r(
                f"envelope:{f}",
                "act_envelope.envelope_refusal",
                ENV_CHECK.get(f, "конверт не совпадает с манифестом заявки"),
                f"{S}::test_envelope_component_substituted_not_approved[{f}]",
                "reason in got",
            )
            for f in ENVELOPE_FIELDS
        ),
        r(
            "ws_id",
            "act_envelope.envelope_refusal",
            "нет конверта — не одобрен",
            f"{S}::test_envelope_of_other_workstream_not_used",
            '"нет конверта"',
        ),
        r(
            "node_bytes",
            "act_envelope.envelope_refusal",
            "байты узла изменились после одобрения",
            f"{S}::test_provider_twin_changed_candidate_bytes_not_approved",
        ),
        r(
            "current_pins",
            "act_envelope.envelope_refusal",
            "upstream-пины изменились после одобрения",
            f"{S}::test_cascade_by_pins_without_a_history_record",
        ),
        r(
            "generation",
            "act_envelope.envelope_refusal",
            "конверт другого поколения",
            f"{S}::test_previous_generation_envelope_with_same_bytes_refused",
        ),
    ),
    "act_status._blobs.read": (
        r(
            "inputs_unread",
            "act_status._blobs.read",
            "прямые входы {node}",
            f"{S}::test_blobs_reader_unavailable_names_node",
            '"прямые входы requirements: объект недоступен"',
        ),
    ),
    "act_status.live_policy": (
        r(
            "binding",
            "act_status.live_policy",
            "deps.binding is None",
            f"{G}::test_no_binding_is_unavailable_not_legacy",
            '"нет привязки корня доверия"',
        ),
    ),
    "act_status.gate_run": (
        r(
            "legacy_mode",
            "act_status.gate_run",
            'live.mode != "review"',
            f"{G}::test_pre_migration_and_merge_are_legacy",
            '("legacy", None)',
        ),
        r(
            "snapshot_digest",
            "act_status.gate_run",
            'not digest.startswith("sha256:")',
            f"{G}::test_snapshot_digest_failure_stops",
            '"gate-check --print-snapshot-digest: код 2: boom"',
        ),
    ),
    # --- act_node (Task 13, 13a)
    "act_node.creation": (
        r(
            "mode_unset",
            "act_node.creation",
            "isinstance(live, Unavailable)",
            f"{N}::test_creation_refuses_unset_mode_itself",
            '"режим акта продукта: нет привязки"',
        ),
        r(
            "draining",
            "act_node.creation",
            "if settings.draining",
            f"{N}::test_draining_refuses_unmarked_creation",
            "draining",
        ),
        r(
            "review_v1_off",
            "act_node.creation",
            "if not settings.review_v1",
            f"{N}::test_review_mode_without_review_v1_refuses",
            "review_v1",
        ),
    ),
    "act_node._manifest": (
        r(
            "parseable",
            "act_node._manifest",
            "except RequestError",
            f"{N}::test_unreadable_manifest_in_head_invalidates",
            '"манифест в одобренном коммите не читается"',
        ),
    ),
    "act_node._binding_refusal": (
        r(
            "identity",
            "act_node._binding_refusal",
            "if refusal is not None",
            f"{N}::test_foreign_identity_binding_invalidates[ws_id]",
            '"манифест: ws_id ws-other"',
        ),
        r(
            "upstream_pins",
            "act_node._binding_refusal",
            "dict(req.upstream_pins[node]) != fact.value",
            f"{N}::test_foreign_identity_binding_invalidates[upstream_pins]",
            '"upstream-пины"',
        ),
    ),
    "act_node._check_marked": (
        r(
            "act_mirror",
            "act_node._check_marked",
            '(op.get("act") == ACT_REVIEW_V1) != (req is not None)',
            f"{N}::test_act_removed_from_ledger_with_manifest_in_head",
            '"зеркало act в run.json расходится"',
        ),
    ),
    "act_node.route": (
        r(
            "merged_unmarked_mode",
            "act_node.route",
            "isinstance(live, Unavailable)",
            f"{N}::test_merged_unmarked_with_unset_mode_stays_alive",
            '"режим акта"',
        ),
    ),
    "act_node._open": (
        r(
            "mode",
            "act_node._open",
            'live.mode != "review"',
            f"{N}::test_open_candidate_in_merge_mode_refused",
            '"а режим продукта сейчас — merge"',
        ),
        r(
            "live_pin",
            "act_node._open",
            "refusal is not None",
            f"{N}::test_live_request_with_old_pin_invalidated",
            '"policy_changed"',
        ),
        r(
            "head",
            "act_node._open",
            "review.head_sha != head",
            f"{N}::test_open_candidate_head_moved_between_reads",
            '"сдвинулась между чтениями"',
        ),
        r(
            "act",
            "act_node._open",
            "if act is None",
            f"{N}::test_open_without_approve_waits",
            "WAITING_HUMAN_APPROVAL",
        ),
        r(
            "checks",
            "act_node._open",
            "if waited is not None",
            f"{WR}::test_resume_red_required_checks_stop_review",
            '"stopped_review"',
        ),
        r(
            "checks_retry",
            "act_node._open",
            "if waited.retry",
            f"{WR}::test_resume_waits_delivery_while_required_checks_run",
            "act_node.WAITING_DELIVERY",
        ),
    ),
    "act_node._legacy.verdict": (
        r(
            "halt",
            "act_node._legacy.verdict",
            "isinstance(got, an.LegacyHalt)",
            f"{N}::test_legacy_halt_is_unresolved",
            '"политика недоступна"',
        ),
    ),
    "act_node._head_hashes": (
        r(
            "head_bytes",
            "act_node._head_hashes",
            "if text is None",
            f"{N}::test_head_hashes_unreadable_node_unavailable",
            "не читается",
        ),
    ),
    "act_node._merged": (
        r(
            "rejected",
            "act_node._merged",
            "isinstance(outcome, Rejected)",
            f"{N}::test_legacy_candidate_merged_after_review_activation_refused",
            "STATUS_INVALIDATED",
        ),
    ),
    "act_node._record_act": (
        r(
            "recorded_act",
            "act_node._record_act",
            'if (op["review_id"], op["merge_commit"]) != (',
            f"{N}::test_recorded_act_differs_from_decision_unresolved",
            '"записан акт 99/"',
        ),
    ),
    "act_node.envelope_commit_defect": (
        r(
            "paths",
            "act_node.envelope_commit_defect",
            "set(touched) != set(want)",
            f"{N}::test_envelope_commit_defect_twins[extra_path]",
            "is not None",
        ),
        r(
            "bytes",
            "act_node.envelope_commit_defect",
            "ops.show_file(state.target_dir, head, path) != text",
            f"{N}::test_envelope_commit_defect_twins[bytes]",
            "is not None",
        ),
    ),
    "act_node._finalize": (
        r(
            "commit_made",
            "act_node._finalize",
            "if head is None:\n            raise",
            f"{N}::test_envelope_commit_not_made_is_refused",
            '"коммит конверта не состоялся"',
        ),
    ),
    "act_node._reconcile_finalize": (
        *(
            r(
                f"envelope:{f}",
                "act_node._reconcile_finalize",
                "if got != text",
                f"{N}::test_finalize_delivered_envelope_component_substituted_invalidates[{f}]",
                '"не тот конверт"',
            )
            for f in ENVELOPE_FIELDS
        ),
        r(
            "current_admission",
            "act_node._reconcile_finalize",
            "if refusal is not None",
            f"{N}::test_stop_between_act_and_finalize_merge_not_reported_approved",
            '"узлы не одобрены — узел charter: not_approved:stopped:g1"',
        ),
    ),
    "act_node._require_in_base": (
        r(
            "in_base",
            "act_node._require_in_base",
            "if not reached",
            f"{N}::test_finalize_merged_outside_base_invalidates",
            '"не в базу"',
        ),
    ),
    "act_node.reopen": (
        r(
            "mode_unset",
            "act_node.reopen",
            "isinstance(live, Unavailable)",
            f"{N}::test_reopen_mode_unset_refused",
            "режим акта продукта .--reopen charter.",
        ),
        r(
            "current_event_after",
            "act_node.reopen",
            "node_history(after, node).current != DeliveredStop(commit, stop)",
            f"{N}::test_reopen_new_event_between_read_and_stop_check",
            '"не эта остановка"',
        ),
        r(
            "already_stopped",
            "act_node.reopen",
            "not isinstance(current, DeliveredRequest)",
            f"{N}::test_reopen_already_delivered_stop_is_idempotent",
            "is None",
        ),
    ),
    "act_node.repropose": (
        r(
            "unknown",
            "act_node.repropose",
            "if op is None",
            f"{RP}::test_unknown_request_refused",
            '"нет в леджере"',
        ),
        r(
            "completed",
            "act_node.repropose",
            "status == al.STATUS_COMPLETED",
            f"{RP}::test_completed_request_refused",
            '"--reopen"',
        ),
        r(
            "status",
            "act_node.repropose",
            "status != al.STATUS_STARTED and status not in al.TERMINAL_STATUSES",
            f"{RP}::test_unrecognized_status_refused",
            '"не распознан"',
        ),
        r(
            "after_merge",
            "act_node.repropose",
            "al.next_step(op) in _AFTER_MERGE",
            f"{RP}::test_after_merge_step_refused",
            '"мерж candidate уже засчитан"',
        ),
    ),
    # доставка остановки доказывается базой (B1-15): отказ, а не «остановлен»
    "act_node._require_stop_delivered": (
        r(
            "in_base",
            "act_node._require_stop_delivered",
            "if not reached",
            f"{N}::test_reopen_refuses_stop_pr_outside_base",
            "не в базу master",
        ),
        r(
            "record",
            "act_node._require_stop_delivered",
            "ops.show_file(state.target_dir, commit, path) != record",
            f"{N}::test_reopen_post_merge_record_check_is_independent",
            '"записи остановки"',
        ),
    ),
    "act_node._merge_stop_pr": (
        r(
            "form",
            "act_node._merge_stop_pr",
            "if late is not None",
            f"{N}::test_reopen_refuses_foreign_stop_pr_before_merge[extra_file]",
            'f"stop-PR #{stop_pr}.*разберите вручную"',
        ),
        r(
            "refusal",
            "act_node._merge_stop_pr",
            "if refusal is not None",
            f"{N}::test_reopen_stop_pr_without_approving_review_names_stop_procedure",
            'f"review-pr.sh {stop_pr}"',
        ),
    ),
    "act_node.stop_pr_defect": (
        r(
            "base_named",
            "act_node.stop_pr_defect",
            "not isinstance(target, str) or not target",
            f"{N}::test_reopen_stop_pr_base_unreadable_unresolved",
            '"факт не установлен.*база stop-PR"',
        ),
        r(
            "target",
            "act_node.stop_pr_defect",
            "if target != an._base_ref(state)",
            f"{N}::test_reopen_stop_pr_into_other_branch_refused",
            '"вливается в release-x.*разберите вручную"',
        ),
        r(
            "files",
            "act_node.stop_pr_defect",
            'files != [(path, "added")]',
            f"{N}::test_reopen_refuses_foreign_stop_pr_before_merge[extra_file]",
            'f"stop-PR #{stop_pr}.*разберите вручную"',
        ),
        r(
            "record",
            "act_node.stop_pr_defect",
            "ops.show_file(state.target_dir, head, path) != record",
            f"{N}::test_reopen_refuses_foreign_stop_pr_before_merge[other_reason]",
            'f"stop-PR #{stop_pr}.*разберите вручную"',
        ),
    ),
    "act_node._stop_pr": (
        r(
            "branch_without_pr",
            "act_node._stop_pr",
            "if remote.outcome is Outcome.FOUND",
            f"{N}::test_reopen_stop_branch_without_pr_names_remedy",
            'f"ветка есть без stop-PR.*git push origin --delete {branch}"',
        ),
    ),
    "act_node._unusable_stop_pr": (
        r(
            "closed",
            "act_node._unusable_stop_pr",
            "where is Disposition.CLOSED_UNMERGED",
            f"{N}::test_reopen_closed_stop_pr_names_remedy",
            'f"stop-PR #{stop_pr} закрыт без мержа',
        ),
        r(
            "outside_base",
            "act_node._unusable_stop_pr",
            "if not reached",
            f"{N}::test_reopen_found_stop_pr_merged_outside_base_names_remedy",
            'f"stop-PR #{stop_pr} смёржен не в базу master',
        ),
    ),
    # --- act_node: переиздание (Task 13a)
    "act_node._continue_successor": (
        r(
            "successor_terminal",
            "act_node._continue_successor",
            "if not al.is_live(succ)",
            f"{RP}::test_terminal_successor_refused",
            'f"преемник {succ}"',
        ),
        r(
            "source_missing",
            "act_node._continue_successor",
            'if missing and succ.get("source_sha")',
            f"{RP}::test_source_successor_missing_nodes_refused",
            '"разберите вручную"',
        ),
        r(
            "not_accumulating",
            "act_node._continue_successor",
            "if missing and not an._still_accumulating(state, ops, succ)",
            f"{RP}::test_merged_successor_with_missing_node_refused",
            '"уже не накапливает узлы .*acceptance"',
        ),
    ),
    "act_node._undecided_delivery": (
        r(
            "mode_unset",
            "act_node._undecided_delivery",
            "isinstance(live, Unavailable)",
            f"{RP}::test_undecided_delivery_mode_unset_refused",
            '"режим акта продукта .--repropose"',
        ),
        r(
            "accepted",
            "act_node._undecided_delivery",
            "засчитывается (акт есть)",
            f"{RP}::test_merged_predecessor_with_accepted_delivery_refused",
            '"засчитывается .* `--approve-node`"',
        ),
        r(
            "legacy_accepted",
            "act_node._undecided_delivery",
            "засчитывается (акт есть)",
            f"{RP}::test_legacy_merged_accepted_refused",
            '"засчитывается .* `--approve-node`"',
        ),
    ),
    "act_node._delivery_outcome": (
        r(
            "context",
            "act_node._delivery_outcome",
            "isinstance(context, Unavailable)",
            f"{RP}::test_unmarked_merged_trusted_context_unavailable_refused",
            '"доверенный контекст: журнал недоступен"',
        ),
    ),
    "act_node._marked_verdict": (
        r(
            "mode",
            "act_node._marked_verdict",
            'if live.mode != "review"',
            f"{RP}::test_merged_predecessor_outside_review_mode_refused",
            '"режим продукта сейчас — merge"',
        ),
        r(
            "history",
            "act_node._marked_verdict",
            "isinstance(history, Unavailable)",
            f"{RP}::test_merged_predecessor_history_unreadable_refused",
            '"история заявок узлов: сбой"',
        ),
        r(
            "delivery_in_history",
            "act_node._marked_verdict",
            "if event is None",
            f"{RP}::test_merged_predecessor_missing_from_history_refused",
            '"доставки в истории"',
        ),
    ),
    "act_node._unmarked_verdict": (
        r(
            "legacy_halt",
            "act_node._unmarked_verdict",
            "isinstance(verdict, an.LegacyHalt)",
            f"{RP}::test_legacy_merged_outcome_unresolved_refused",
            '"вердикт старого кода .*сеть"',
        ),
        r(
            "head_named",
            "act_node._unmarked_verdict",
            "not isinstance(head, str) or len(head) != 40",
            f"{RP}::test_unmarked_merged_unnamed_head_refused",
            'f"голова candidate-PR #{pr} не названа"',
        ),
    ),
    # --- act_premerge (Task 15)
    "act_premerge_core.request_of_branch": (
        r(
            "form",
            "act_premerge_core.request_of_branch",
            "if match is None",
            f"{P}::test_request_of_branch",
            '"feat/x"',
        ),
    ),
    "act_premerge_core._trust": (
        r(
            "binding",
            "act_premerge_core._trust",
            "deps.binding is None",
            f"{P}::test_no_binding_refused",
            '"без корня доверия"',
        ),
        r(
            "journal",
            "act_premerge_core._trust",
            "isinstance(deps.journal, Unavailable)",
            f"{P}::test_journal_unavailable_is_unset",
            '"журнал периметра: "',
        ),
        r(
            "app_unknown",
            "act_premerge_core._trust",
            "установки App: ",
            f"{P}::test_app_installations_unknown_is_unset",
            '"установки App: "',
        ),
        r(
            "app_violation",
            "act_premerge_core._trust",
            "if found is not None",
            f"{P}::test_app_installation_violation_recorded",
            "act_premerge.REGISTERED",
        ),
    ),
    "act_premerge_core._live": (
        r(
            "mode_unset",
            "act_premerge_core._live",
            "isinstance(mode, Unavailable)",
            f"{P}::test_product_mode_gate[unset]",
            '"режим продукта: "',
        ),
        r(
            "not_review",
            "act_premerge_core._live",
            'if mode != "review"',
            f"{P}::test_product_mode_gate[merge]",
            '"— не review"',
        ),
    ),
    "act_premerge_core._check": (
        r(
            "identity",
            "act_premerge_core._check",
            "if refusal is not None",
            f"{P}::test_manifest_of_other_identity_refused[ws_id]",
            'f"манифест: {field}"',
        ),
        r(
            "manifest_present",
            "act_premerge_core._check",
            "if text is None",
            f"{P}::test_no_manifest_refused",
            '"нет манифеста"',
        ),
        r(
            "surface",
            "act_premerge_core._check",
            "if off_surface is not None",
            f"{V2}::test_candidate_foreign_file_refused",
            '"surface_violation: scripts/foreign.sh"',
        ),
        r(
            "merger",
            "act_premerge_core._check",
            "not merger_allowed(policy, args.merger",
            f"{P}::test_merger_outside_allowlist_refused",
            '"не пройдёт D3"',
        ),
        r(
            "skew",
            "act_premerge_core._check",
            "wait > MAX_WAIT",
            f"{P}::test_clock_skew_beyond_ten_seconds_unset",
            '"часы расходятся"',
        ),
        r(
            "reread",
            "act_premerge_core._check",
            "again != facts or current2 != current",
            f"{P}::test_reread_sees_dismissal_unset",
            '"изменились за время ожидания"',
        ),
    ),
    "act_premerge_core._act": (
        r(
            "head_sha",
            "act_premerge_core._act",
            "facts.head_sha != args.head",
            f"{P}::test_review_facts_head_differs_refused",
            '"≠ --head"',
        ),
        r(
            "act",
            "act_premerge_core._act",
            "find_act(args.head, effective, policy) is None",
            f"{P}::test_no_approve_refused",
            '"нет approve человека"',
        ),
        r(
            "current_second",
            "act_premerge_core._act",
            "_approve_in_current_second(facts, args.head, now)",
            f"{P}::test_approve_in_current_second_is_unset",
            '"в текущей секунде"',
        ),
    ),
    # approve этой же секунды — «не установлено», а не отказ (ревью PR #673)
    "act_premerge_core._approve_in_current_second": (
        r(
            "same_second",
            "act_premerge_core._approve_in_current_second",
            "parse_ts(r.submitted_at) >= now",
            f"{P}::test_approve_in_current_second_is_unset",
            '"в текущей секунде"',
        ),
    ),
    "act_premerge_core._boundary": (
        r(
            "stamp",
            "act_premerge_core._boundary",
            "except ValueError as exc",
            f"{P}::test_boundary_unparsed_stamp_is_unset",
            '"метка события не разобрана"',
        ),
    ),
    "act_premerge_core._manifest_text": (
        r(
            "utf8",
            "act_premerge_core._manifest_text",
            "except UnicodeDecodeError",
            f"{P}::test_manifest_text_reads_head_bytes[not_utf8]",
            '"m.yaml: не UTF-8"',
        ),
    ),
    # --- act_app_token (Task 15a)
    "act_app_token._page_ids": (
        r(
            "completeness",
            "act_app_token._page_ids",
            "status != 200 or not isinstance(chunk, list)",
            f"{K}::test_full_last_page_requires_empty_next",
            '"стр. 2"',
        ),
        r(
            "entry_id",
            "act_app_token._page_ids",
            "type(ident) is not int for ident in ids",
            f"{K}::test_installation_without_int_id_is_unavailable",
            '"GET /app/installations (стр. 1): запись без целого id"',
        ),
        r(
            "total_count",
            "act_app_token._page_ids",
            "type(total) is not int or total != len(items)",
            f"{K}::test_repositories_total_mismatch_is_unavailable",
            '"total_count"',
        ),
        r(
            "pages_limit",
            "act_app_token._page_ids",
            "больше {MAX_PAGES} страниц",
            f"{K}::test_too_many_installation_pages_is_unavailable",
            '"больше 2 страниц"',
        ),
    ),
    "act_app_token._open_no_link": (
        r(
            "key_unread",
            "act_app_token._open_no_link",
            "не прочитан",
            f"{K}::test_missing_key_is_unavailable",
            '"не прочитан"',
        ),
        r(
            "key_link",
            "act_app_token._open_no_link",
            "if link:",
            f"{K}::test_key_swapped_to_link_after_shell_check_refused",
            '"путь после darkfactory/apps через ссылку"',
        ),
    ),
    "act_app_token._read_key": (
        r(
            "key_anchor",
            "act_app_token._read_key",
            "anchor is None or anchor >= len(parts)",
            f"{K}::test_key_outside_anchor_refused",
            '"вне каталога darkfactory/apps"',
        ),
        r(
            "key_regular",
            "act_app_token._read_key",
            "stat.S_ISREG(",
            f"{K}::test_key_not_regular_file_refused",
            '"не обычный файл"',
        ),
    ),
    "act_app_token._bearer": (
        r(
            "key_unsigned",
            "act_app_token._bearer",
            "except Exception",
            f"{K}::test_bad_key_reason_has_no_key_bytes",
            '"не подписывает JWT"',
        ),
    ),
    "act_app_token._token": (
        r(
            "token",
            "act_app_token._token",
            "status != 201",
            f"{K}::test_installation_token_refused_is_unavailable",
            '"токен установки 9: HTTP 502"',
        ),
    ),
    "act_app_token.revoke": (
        r(
            "revoke_form",
            "act_app_token.revoke",
            "TOKEN.fullmatch(token) is None",
            f"{K}::test_revoke_malformed_token_no_request",
            '"токен установки не той формы"',
        ),
        r(
            "revoke_status",
            "act_app_token.revoke",
            "status != 204",
            f"{K}::test_revoke_refused_is_unavailable_without_token",
            '"отзыв токена установки: HTTP 401"',
        ),
    ),
    "act_app_token.app_identity": (
        r(
            "app",
            "act_app_token.app_identity",
            "status != 200 or not isinstance(slug, str)",
            f"{K}::test_identity_unreadable_is_unavailable",
            '"GET /app: HTTP 403"',
        ),
        r(
            "installation",
            "act_app_token.app_identity",
            "type(installation) is not int",
            f"{K}::test_installation_unreadable_is_unavailable",
            '"установка App на o/p: HTTP 404"',
        ),
        r(
            "repo_form",
            "act_app_token.app_identity",
            "REPO_SLUG.fullmatch(repo) is None",
            f"{K}::test_identity_bad_repo_no_request",
            "http.seen == []",
        ),
    ),
    "act_app_token.installations_refusal": (
        r(
            "trust_repo",
            "act_app_token.installations_refusal",
            "if repo_id in repos",
            f"{K}::test_installation_on_trust_repo_is_violation",
            '"установка App 9 видит approval-policy (22)"',
        ),
        r(
            "second_page",
            "act_app_token.installations_refusal",
            "if repo_id in repos",
            f"{K}::test_violation_on_second_installations_page",
            '"установка App 9 видит df-trust (11)"',
        ),
        r(
            "repos_unreadable",
            "act_app_token.installations_refusal",
            "isinstance(repos, Unavailable)",
            f"{K}::test_repos_failure_reason_has_no_token",
            '"репо установки 9"',
        ),
    ),
    # --- act_barrier (Task 16)
    "act_barrier._record": (
        r(
            "shape",
            "act_barrier._record",
            "or state not in _PR_STATES",
            f"{B}::test_unexpected_record_shape_is_a_gap",
            '"список PR owner/p неполон: 1 записей неожиданной формы"',
        ),
    ),
    "act_barrier._listing": (
        r(
            "list_failed",
            "act_barrier._listing",
            "except Exception as exc",
            f"{B}::test_broken_pagination_incomplete",
            '"список PR owner/p неполон',
        ),
        r(
            "records",
            "act_barrier._listing",
            "len(found) != len(records)",
            f"{B}::test_unexpected_record_shape_is_a_gap",
            '"список PR owner/p неполон: 1 записей неожиданной формы"',
        ),
    ),
    "act_barrier.human_context": (
        r(
            "human",
            "act_barrier.human_context",
            'flags and (caller or "") not in humans',
            f"{B}::test_human_context_refuses_agent",
            '"только человек из human_approvers"',
        ),
    ),
    "act_barrier.main": (
        r(
            "usage",
            "act_barrier.main",
            "except SystemExit",
            f"{B}::test_cli_usage_and_help_are_incomplete",
            "act_barrier.EXIT_INCOMPLETE",
        ),
        r(
            "crash_incomplete",
            "act_barrier.main",
            "барьер неполон",
            f"{B}::test_cli_unexpected_exception_is_incomplete",
            '"TypeError"',
        ),
        r(
            "refused",
            "act_barrier.main",
            "return EXIT_REFUSED",
            f"{B}::test_cli_invalidate_by_agent_refused",
            '"human_approvers"',
        ),
    ),
    "act_barrier._main": (
        r(
            "activation",
            "act_barrier._main",
            "isinstance(live, Unavailable)",
            f"{B}::test_cli_unavailable_activation_is_incomplete",
            '"текущая активация — нет привязки"',
        ),
    ),
    "act_barrier._identity": (
        r(
            "request_form",
            "act_barrier._identity",
            "if parsed is None",
            f"{B}::test_identity_only_for_request_branches",
            '"brief/ws-7") is None',
        ),
    ),
    "act_barrier._marked": (
        r(
            "absent",
            "act_barrier._marked",
            "Unmarked(NO_MANIFEST_FILE)",
            f"{B}::test_open_unmarked_stage_is_live[candidate]",
            "манифеста нет в голове",
        ),
        r(
            "unparsed",
            "act_barrier._marked",
            "except RequestError as exc",
            f"{B}::test_malformed_or_foreign_manifest_is_not_a_marker[bad_yaml]",
            '"манифест не разобран"',
        ),
        r(
            "foreign",
            "act_barrier._marked",
            "if refusal is not None",
            f"{B}::test_malformed_or_foreign_manifest_is_not_a_marker[ws_id]",
            '"манифест чужой заявки: манифест: ws_id"',
        ),
        r(
            "head",
            "act_barrier._marked",
            "if head is None",
            f"{B}::test_unreadable_head_or_manifest_is_a_gap[head]",
            '"#5: голова не прочитана"',
        ),
        r(
            "manifest_unread",
            "act_barrier._marked",
            "fact.outcome is Outcome.UNAVAILABLE",
            f"{B}::test_unreadable_head_or_manifest_is_a_gap[manifest]",
            '"#5: манифест не прочитан — сеть"',
        ),
    ),
    "act_barrier._migrated": (
        r(
            "author",
            "act_barrier._migrated",
            "c.author not in humans",
            f"{B}::test_marker_must_be_human_unedited_and_this_request[author]",
            '"маркер не от human_approvers"',
        ),
        r(
            "edited",
            "act_barrier._migrated",
            "c.last_edited_at is not None",
            f"{B}::test_marker_must_be_human_unedited_and_this_request[edited]",
            '"маркер правлен"',
        ),
        r(
            "identity",
            "act_barrier._migrated",
            "c.body.strip() != ident.migration_marker",
            f"{B}::test_marker_must_be_human_unedited_and_this_request[ws_id]",
            '"маркер не этой заявки"',
        ),
        r(
            "comments_unread",
            "act_barrier._migrated",
            "fact.outcome is not Outcome.FOUND or fact.value is None",
            f"{B}::test_unreadable_comments_are_a_gap",
            '"#5: комментарии не прочитаны — сеть"',
        ),
    ),
    "act_barrier._signed": (
        r(
            "status",
            "act_barrier._signed",
            'meta.get("status") != "approved"',
            f"{B}::test_unapproved_node_in_base_is_live",
            '"статус узла в базе не approved"',
        ),
        r(
            "frontmatter",
            "act_barrier._signed",
            "except ValueError:\n        return SIGNED_STATUS",
            f"{B}::test_node_without_frontmatter_in_base_is_live",
            '"статус узла в базе не approved"',
        ),
        r(
            "signer",
            "act_barrier._signed",
            "parse_ts(later) > parse_ts(at)",
            f"{B}::test_merged_legacy_without_signature_in_base_is_live",
            "подпись другого акта",
        ),
    ),
    "act_barrier._candidate_nodes": (
        r(
            "facts_unread",
            "act_barrier._candidate_nodes",
            "files.outcome is not Outcome.FOUND or files.value is None or head is None",
            f"{B}::test_candidate_files_unread_is_a_gap",
            '"#5: состав или голова candidate не прочитаны"',
        ),
        r(
            "pending",
            "act_barrier._candidate_nodes",
            "if found and _pending(text.value)",
            f"{B}::test_candidate_without_pending_nodes_is_live",
            '"узлов на одобрении не найдено"',
        ),
        r(
            "unread",
            "act_barrier._candidate_nodes",
            "в голове candidate не прочитан",
            f"{B}::test_unreadable_candidate_file_is_a_gap",
            '"#5: {NODE} в голове candidate не прочитан"',
        ),
    ),
    "act_barrier._legacy_resolved": (
        r(
            "tip_unread",
            "act_barrier._legacy_resolved",
            "tip.outcome is not Outcome.FOUND or tip.value is None",
            f"{B}::test_legacy_base_tip_unread_is_a_gap",
            '"#5: голова базы не прочитана"',
        ),
        r(
            "base_unread",
            "act_barrier._legacy_resolved",
            "if text.outcome is Outcome.UNAVAILABLE",
            f"{B}::test_legacy_base_node_unread_is_a_gap",
            'f"#5: {NODE} в базе не прочитан"',
        ),
        r(
            "finalize_merged",
            "act_barrier._legacy_resolved",
            "branch + ab.finalize_suffix() not in merged",
            f"{B}::test_merged_candidate_without_finalize_is_live[open]",
            "— pending-finalize",
        ),
        r(
            "node_in_base",
            "act_barrier._legacy_resolved",
            "text.outcome is not Outcome.FOUND or text.value is None",
            f"{B}::test_partial_finalize_leaves_candidate_live",
            "узла нет в базе",
        ),
        r(
            "nodes_found",
            "act_barrier._legacy_resolved",
            "if not nodes",
            f"{B}::test_candidate_without_pending_nodes_is_live",
            '"узлов на одобрении не найдено"',
        ),
        r(
            "merger_named",
            "act_barrier._legacy_resolved",
            "not isinstance(by, str) or not isinstance(at, str)",
            f"{B}::test_unnamed_candidate_merger_is_a_gap",
            '"#5: кто и когда смёржил candidate — не прочитано"',
        ),
    ),
    "act_barrier._judge": (
        r(
            "brief_open",
            "act_barrier._judge",
            "brief-PR OPEN — заявка v1 без маркера",
            f"{B}::test_open_unmarked_stage_is_live[brief]",
        ),
        r(
            "brief_merged",
            "act_barrier._judge",
            "if stamp is None",
            f"{B}::test_merged_brief_needs_human_confirmation",
            '"--confirm-resolved 7"',
        ),
        r(
            "open_unmarked",
            "act_barrier._judge",
            "OPEN без маркера: {reason}",
            f"{B}::test_open_unmarked_stage_is_live[finalize]",
            "OPEN без маркера: манифеста нет в голове",
        ),
        r(
            "migrated",
            "act_barrier._judge",
            "if migrated is True",
            f"{B}::test_human_migration_marker_resolves",
            ".items == ()",
        ),
        r(
            "closed_live",
            "act_barrier._judge",
            "label = live_local.get(number)",
            f"{B}::test_closed_candidate_with_live_record_is_item",
            '"#5 candidate CLOSED без мержа — живая заявка r1:approve-2-1-1 без маркера"',
        ),
    ),
    "act_barrier._local_brief_stamp": (
        r(
            "run",
            "act_barrier._local_brief_stamp",
            'approval.get("repo") == product and approval.get("pr") == number',
            f"{B}::test_local_brief_stamp_binds_product_pr_and_file[other_product]",
            '"нет прогона с interview.approval этого PR"',
        ),
        r(
            "file",
            "act_barrier._local_brief_stamp",
            "stamped.is_file()",
            f"{B}::test_local_brief_stamp_binds_product_pr_and_file[no_stamp]",
            '"нет файла штампа брифа"',
        ),
    ),
    "act_barrier._local_items": (
        r(
            "live_local",
            "act_barrier._local_items",
            "items.append((",
            f"{B}::test_local_live_request_without_pr_is_item",
            '"r1:approve-2-1-1 — заявка без маркера до PR"',
        ),
        r(
            "with_candidate_pr",
            "act_barrier._local_items",
            'if op.get("act") == ACT_REVIEW_V1',
            f"{B}::test_local_live_request_with_pr_is_item",
            '"r1:approve-2-1-1 — заявка без маркера (candidate #9)"',
        ),
        r(
            "unreadable",
            "act_barrier._local_items",
            "леджер {run_id} не читается",
            f"{B}::test_unreadable_local_ledger_incomplete",
            '"леджер r1 не читается"',
        ),
    ),
    "act_barrier.live_unmarked": (
        r(
            "pr_exception",
            "act_barrier.live_unmarked",
            "факты PR не установлены",
            f"{B}::test_forge_exception_per_pr_is_a_gap",
            '"#5: факты PR не установлены — CalledProcessError"',
        ),
        r(
            "draining",
            "act_barrier.live_unmarked",
            "if not draining",
            f"{B}::test_draining_off_incomplete",
            '"draining не включён (act.toml act_migration)"',
        ),
        r(
            "attestation",
            "act_barrier.live_unmarked",
            "if not attested",
            f"{B}::test_without_attestation_incomplete",
            '"draining других установок не подтверждён',
        ),
        r(
            "pagination",
            "act_barrier.live_unmarked",
            "список PR {product} неполон",
            f"{B}::test_broken_pagination_incomplete",
            '"список PR owner/p неполон',
        ),
        r(
            "local_dedup",
            "act_barrier.live_unmarked",
            "pr not in reported",
            f"{B}::test_open_candidate_not_duplicated_by_local_pass",
            "len(got.items) == 1",
        ),
    ),
    "act_barrier.live_review_requests": (
        r(
            "pr_exception",
            "act_barrier.live_review_requests",
            "факты PR не установлены",
            f"{B}::test_review_preview_per_pr_exception_is_a_gap",
            '"owner/p#5: факты PR не установлены — CalledProcessError"',
        ),
        r(
            "pr_unread",
            "act_barrier.live_review_requests",
            'gaps.append(f"{product}{marked}")',
            f"{B}::test_review_preview_unread_pr_is_a_gap",
            '"owner/p#5: манифест не прочитан — сеть"',
        ),
        r(
            "product_gaps",
            "act_barrier.live_review_requests",
            "{product}: список PR неполон",
            f"{B}::test_review_preview_unavailable_product_incomplete",
            '"owner/b: список PR неполон',
        ),
    ),
    "act_barrier.invalidate": (
        r(
            "listing",
            "act_barrier.invalidate",
            "branch is None and broken is not None",
            f"{B}::test_cli_invalidate_listing_failure_is_incomplete",
            '"pagination broken"',
        ),
        r(
            "human",
            "act_barrier.invalidate",
            "if caller not in humans",
            f"{B}::test_invalidate_posts_marker_for_human_only",
            '"human_approvers"',
        ),
        r(
            "request_form",
            "act_barrier.invalidate",
            "if ident is None",
            f"{B}::test_invalidate_refuses_non_request_pr",
            '"не candidate/finalize заявки узла"',
        ),
        r(
            "ws_id",
            "act_barrier.invalidate",
            "(state.repo_slug, state.ws_id) != (ident.product, ident.ws_id)",
            f"{B}::test_invalidate_touches_only_selected_workstream",
            'hit == ["ws-7"]',
        ),
    ),
    # --- дополнения части B в существующие модули
    # `_legacy_outcome` вычисляет опубликованность и делегирует `_legacy_verdict`
    "approve_node._legacy_verdict": (
        r(
            "old_allowlist",
            "approve_node._legacy_verdict",
            "signature.outcome is Outcome.FORBIDDEN",
            f"{L}::test_merger_outside_old_allowlist_refused",
            '"legacy_signature"',
        ),
        r(
            "old_pin_missing",
            "approve_node._legacy_verdict",
            "if not pinned",
            f"{L}::test_old_pin_missing_refused",
            '"legacy_unpinned"',
        ),
        r(
            "old_pin_substituted",
            "approve_node._legacy_verdict",
            'snapshot.fingerprint != pinned["fingerprint"]',
            f"{L}::test_old_pin_substituted_is_unavailable",
            '"unavailable:политика"',
        ),
        r(
            "old_pin_unavailable",
            "approve_node._legacy_verdict",
            "policy.outcome is Outcome.UNAVAILABLE",
            f"{L}::test_old_pin_unavailable_is_unavailable",
            '"unavailable:политика подписи"',
        ),
        r(
            "old_pin_superseded",
            "approve_node._legacy_verdict",
            "refusal.kind == af.POLICY_REFUSAL_SUPERSEDED",
            f"{L}::test_old_pin_superseded_is_policy_changed",
            '"policy_changed"',
        ),
        r(
            "snapshot_published",
            "approve_node._legacy_verdict",
            "if not published",
            f"{L}::test_snapshot_not_published_in_candidate_refused",
            '"legacy_unpublished"',
        ),
    ),
    "approval_ledger.successor_of": (
        r(
            "single_successor",
            "approval_ledger.successor_of",
            "len(found) > 1",
            f"{RP}::test_two_successors_refused",
            '"несколько преемников"',
        ),
    ),
    "approve_node._supersedes_undecided": (
        r(
            "other_request",
            "approve_node._supersedes_undecided",
            "current.request.request_id != predecessor",
            f"{RP}::test_undecided_status_of_other_request_still_refuses",
            '"unavailable:ambiguous_event_order"',
        ),
        r(
            "history_unread",
            "approve_node._supersedes_undecided",
            "isinstance(statuses.history, Unavailable)",
            f"{RP}::test_undecided_status_with_unreadable_history_refuses",
            '"unavailable:история"',
        ),
    ),
    "approve_node._require_no_join_for_successor": (
        r(
            "successor_joins_live",
            "approve_node._require_no_join_for_successor",
            'and joined[1].get("predecessor") != predecessor',
            f"{RP}::test_successor_refused_when_step_has_live_request",
            '"уже есть — продвиньте её"',
        ),
        r(
            "second_successor",
            "approve_node._require_no_join_for_successor",
            "if existing is not None",
            f"{RP}::test_merged_successor_with_missing_node_refused",
            'f"уже есть преемник {succ}"',
        ),
    ),
    "approve_node._require_same_act": (
        r(
            "act",
            "approve_node._require_same_act",
            'joined[1].get("act") != act',
            f"{N}::test_join_live_legacy_request_refused",
            '"в одну заявку они не соединяются"',
        ),
    ),
    "approve_node._creation_policy": (
        r(
            "policy_pin",
            "approve_node._creation_policy",
            'joined[1].get("policy") != record',
            f"{N}::test_join_request_pinned_to_other_activation_refused",
            '"закрепила другую активацию"',
        ),
    ),
    "approve_node.require_clean_synced_base": (
        r(
            "dirty",
            "approve_node.require_clean_synced_base",
            "if ops.is_dirty(state.target_dir)",
            f"{RP}::test_dirty_tree_refused_before_burial",
            '"грязный"',
        ),
    ),
    "ops.RealOps.first_parent_changes": (
        r(
            "git_failed",
            "ops.RealOps.first_parent_changes",
            "if done.returncode != 0",
            f"{S}::test_first_parent_unreadable_is_none",
            '"master") is None',
        ),
    ),
    # --- новые проверки части B внутри изменённых существующих функций (ключ `<функция>#<проверка>`)
    "approve_node._propose#status_unavailable": (
        r(
            "status",
            "approve_node._propose",
            "статус узла {node_id}: {verdict}",
            f"{N}::test_review_status_unavailable_blocks_proposal",
            '"статус узла charter: unavailable:история"',
        ),
    ),
    "approve_node.read_dag_state#mode_unset": (
        r(
            "mode",
            "approve_node.read_dag_state",
            'unresolved=f"режим акта продукта: {live.reason}"',
            f"{N}::test_read_dag_state_mode_unset",
            '"режим акта продукта: нет привязки корня доверия"',
        ),
    ),
    "approve_node.read_dag_state#status_unavailable": (
        r(
            "status",
            "approve_node.read_dag_state",
            'if verdict.startswith("unavailable:")',
            f"{N}::test_read_dag_state_status_unavailable",
            '"unavailable:история"',
        ),
    ),
    "approve_node.read_dag_state#review_debt": (
        r(
            "debt",
            "approve_node.read_dag_state",
            "debts.append(_review_debt(node, texts[node], verdict))",
            f"{N}::test_read_dag_state_review_debt",
            '"act:d1_no_act"',
        ),
    ),
    "approve_node._require_upstream_ready#mode_unset": (
        r(
            "mode",
            "approve_node._require_upstream_ready",
            "режим акта продукта (upstream {node_id})",
            f"{N}::test_upstream_readiness_in_review[mode_unset]",
            "режим акта продукта",
        ),
    ),
    "approve_node._require_upstream_ready#upstream_unavailable": (
        r(
            "status",
            "approve_node._require_upstream_ready",
            "upstream {up} узла {node_id}: {verdict}",
            f"{N}::test_upstream_readiness_in_review[upstream_unavailable]",
            "upstream charter узла",
        ),
    ),
    "approve_node._require_upstream_ready#upstream_not_approved": (
        r(
            "debt",
            "approve_node._require_upstream_ready",
            "не одобрен (review/v1)",
            f"{N}::test_upstream_readiness_in_review[upstream_not_approved]",
            "не одобрен",
        ),
    ),
    "approve_node._try_agent_merge#form": (
        r(
            "form",
            "approve_node._try_agent_merge",
            "if form is not None\n        else _envelope_form_defect",
            f"{N}::test_finalize_form_defect_blocks_agent_merge",
            '"коммит конверта трогает лишний путь"',
        ),
    ),
    "approve_node.approve_node#successor_over_live": (
        r(
            "successor",
            "approve_node.approve_node",
            'predecessor is not None and op.get("predecessor") != predecessor',
            f"{RP}::test_approve_node_refuses_successor_over_live_node_request",
            '"уже есть живая заявка"',
        ),
    ),
    "approve_node.propose_from_source#source_successor": (
        r(
            "successor",
            "approve_node.propose_from_source",
            'state.ops[resumed].get("predecessor") != predecessor',
            f"{RP}::test_source_resume_refuses_successor",
            '"над теми же узлами уже есть"',
        ),
    ),
    "task_bridge.main#repropose_exclusive": (
        r(
            "cli",
            "task_bridge.main",
            "--repropose — отдельное действие",
            f"{RP}::test_cli_repropose_with_approve_node_runs_neither",
            "ran == []",
        ),
    ),
    "approve_node._try_agent_merge#stop_review": (
        r(
            "review",
            "approve_node._try_agent_merge",
            "if missing is not None",
            f"{N}::test_reopen_stop_pr_without_approving_review_names_stop_procedure",
            'f"review-pr.sh {stop_pr}"',
        ),
    ),
    "approve_node._try_agent_merge#stop_late_form": (
        r(
            "late_form",
            "approve_node._try_agent_merge",
            "if late is not None",
            f"{N}::test_reopen_stop_pr_retargeted_during_checks_wait_not_merged",
            '"вливается в release-x.*разберите вручную"',
        ),
    ),
    "approve_node._try_agent_merge#stop_form_first": (
        r(
            "form_first",
            "approve_node._try_agent_merge",
            "if defect is not None",
            f"{N}::test_reopen_refuses_foreign_stop_pr_before_merge[extra_file]",
            'f"stop-PR #{stop_pr}.*разберите вручную"',
        ),
    ),
    "runner._step_gate#act_mode_unset": (
        r(
            "mode",
            "runner._step_gate",
            "GC-ACT-UNAVAILABLE(devtools)",
            "tests/test_governance_runner.py::test_s4_stops_when_act_mode_unset",
        ),
    ),
    "runner._step_s8#act_mode_unset": (
        r(
            "mode",
            "runner._step_s8",
            "S8: режим акта не установлен",
            "tests/test_governance_runner.py::test_s8_stops_when_act_mode_unset",
        ),
    ),
    "task_bridge.deliver_superseded#replacement_repeat": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"возврат PR #{repeated.pr} замены первой доставки"',
            f"{TB}::test_stored_success_requires_admission[v1_replacement]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_superseded#returned_pr": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"возврат PR #{pr} ревизии {n}"',
            f"{TB}::test_stored_success_requires_admission[returned_pr]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_superseded#returned_merged": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"возврат вмерженного PR #{pr} ревизии {n}"',
            f"{TB}::test_stored_success_requires_admission[returned_merged]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_superseded#returned_open": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"возврат открытого PR #{pr} ревизии {n}"',
            f"{TB}::test_stored_success_requires_admission[returned_open]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_superseded#redo_intent": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"повтор доставки ревизии {n} из намерения"',
            f"{TB}::test_stored_success_requires_admission[redo_intent]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_superseded#returned_v1": (
        r(
            "admission",
            "task_bridge.deliver_superseded",
            'f"возврат PR #{v1_pr} первой доставки"',
            f"{TB}::test_stored_success_requires_admission[returned_v1]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_for_run#completed": (
        r(
            "admission",
            "task_bridge.deliver_for_run",
            'f"tasks-спека уже доставлена (PR #{pr_done})"',
            f"{TB}::test_stored_success_requires_admission[for_run_completed]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "task_bridge.deliver_for_run#adopted": (
        r(
            "admission",
            "task_bridge.deliver_for_run",
            'f"принят существующий PR #{existing}"',
            f"{TB}::test_stored_success_requires_admission[for_run_adopted]",
            '"допуск review/v1 не подтверждён — узел requirements"',
        ),
    ),
    "runner._step_gate#completed_admission": (
        r(
            "admission",
            "runner._step_gate",
            "refusal is None",
            "tests/test_governance_runner.py::test_s4_completed_gate_rerun_without_admission[stop_delivered]",
            '"гейт S4 заново"',
        ),
    ),
    "runner._step_s8#completed_admission": (
        r(
            "admission",
            "runner._step_s8",
            "refusal is not None",
            "tests/test_governance_runner.py::test_s8_completed_gate_rerun_without_admission[stop_delivered]",
            '"started"',
        ),
    ),
    "runner._step_s8#run_status": (
        r(
            "status",
            "runner._step_s8",
            'state.status = "running"',
            f"{RN}::test_s8_lost_admission_run_not_completed_when_checkout_fails",
            '"running"',
        ),
    ),
    "runner._next_wave#admission": (
        r(
            "admission",
            "runner._next_wave",
            "refusal is not None",
            "tests/test_governance_runner.py::test_next_wave_needs_admission[stop_delivered]",
            '"stopped_review"',
        ),
    ),
    "approve_node._propose#mode_unavailable": (
        r(
            "mode",
            "approve_node._propose",
            "режим акта продукта (узел {node_id})",
            f"{N}::test_frontmatter_noop_refused_when_mode_unavailable",
            '"режим акта продукта .узел charter.: нет привязки"',
        ),
    ),
    "approve_node._propose#legacy_noop_review": (
        r(
            "status",
            "approve_node._propose",
            "debt is None and statuses is not None",
            f"{N}::test_frontmatter_noop_in_review_needs_status",
            "outcome.request in world.state.ops",
        ),
    ),
    "task_bridge.deliver_superseded#review_noop": (
        r(
            "noop",
            "task_bridge.deliver_superseded",
            "traceless and unconfirmed is None",
            f"{TB}::test_review_noop_refused_without_fresh_approval[stop_delivered]",
            '"гейт §I12 вызван"',
        ),
    ),
    "act_envelope.envelope_refusal#bytes": (
        r(
            "bytes",
            "act_envelope.envelope_refusal",
            "байты узла изменились после одобрения",
            f"{S}::test_provider_twin_changed_candidate_bytes_not_approved",
        ),
    ),
    "act_envelope.envelope_refusal#pins": (
        r(
            "pins",
            "act_envelope.envelope_refusal",
            "upstream-пины изменились после одобрения",
            f"{S}::test_cascade_by_pins_without_a_history_record",
        ),
    ),
    "ops.RealOps.pr_body_fact": (
        r(
            "gh_failed",
            "ops.RealOps.pr_body_fact",
            "done.returncode != 0",
            f"{OB}::test_pr_body_unreadable_is_unavailable",
            '"тело PR #7: no network"',
        ),
    ),
    # --- интеграционное ревью части B (I-1–I-3, m-2, m-3, m-7)
    "act_node._require_head_content": (
        r(
            "unavailable",
            "act_node._require_head_content",
            "isinstance(hashes, Unavailable)",
            f"{N}::test_open_head_hashes_unreadable_unresolved",
            '"факт не установлен"',
        ),
        r(
            "drift",
            "act_node._require_head_content",
            "if drift",
            f"{N}::test_open_content_drift_invalidates_before_merge",
            '"i_content"',
        ),
    ),
    "act_node._run_base_tip": (
        r(
            "retarget",
            "act_node._run_base_tip",
            "target != base",
            f"{N}::test_open_retargeted_candidate_not_delivered",
            '"базу прогона"',
        ),
        r(
            "tip",
            "act_node._run_base_tip",
            "tip.outcome is not Outcome.FOUND",
            f"{N}::test_open_base_tip_unreadable_unresolved",
            '"верхушка базы master"',
        ),
    ),
    "act_premerge.main": (
        r(
            "usage",
            "act_premerge.main",
            "except SystemExit",
            f"{P}::test_argparse_error_is_terminal_not_retry[unknown]",
            "act_premerge.EXIT_REFUSED",
        ),
    ),
    "approve_node._commit_paths": (
        r(
            "ops_failure",
            "approve_node._commit_paths",
            "except OPS_FAILURES",
            f"{OF}::test_commit_paths_failure_unresolved[CalledProcessError]",
            '"факт не установлен"',
        ),
    ),
    "approve_node._content_hash": (
        r(
            "act_projection",
            "approve_node._content_hash",
            "if act is None:",
            f"{N}::test_review_stamp_keys_in_base_hashed_in_review_projection",
            "got is None",
        ),
        r(
            "projection_refused",
            "approve_node._content_hash",
            "except ProjectionError",
            f"{N}::test_content_hash_marker_of_other_act_refused",
            '"self-hash узла в проекции review/v1"',
        ),
    ),
    "runner._deliver_review_candidate": (
        r(
            "unresolved",
            "runner._deliver_review_candidate",
            "except RuntimeError as exc",
            f"{WR}::test_resume_unresolved_is_stopped_review",
            '"stopped_review"',
        ),
        r(
            "not_waiting",
            "runner._deliver_review_candidate",
            "if waiting is None",
            f"{WR}::test_resume_agent_merge_refused_is_not_a_merge_request[3]",
            '"stopped_review"',
        ),
        r(
            "retry_code",
            "runner._deliver_review_candidate",
            "outcome.merge_code in _DELIVERY_RETRY_CODES",
            f"{WR}::test_resume_transient_merge_refusal_waits_delivery[2]",
            "act_node.WAITING_DELIVERY",
        ),
    ),
    # --- act_surface: поверхность заявки D16 (df этап 3a, Task 3; O6)
    "act_surface.surface_violations": (
        r(
            "foreign_path",
            "act_surface.surface_violations",
            "if path not in allowed",
            f"{SF}::test_surface_full_pr_foreign_file_in_early_commit_refused",
            '"scripts/foreign.sh"',
        ),
        r(
            "own_manifest",
            "act_surface.surface_violations",
            "act == ACT_REVIEW_V1",
            f"{SF}::test_surface_manifest_included",
            '"workstreams/WS-1/approvals/requests/approve-1-0-1.yaml"',
        ),
        r(
            "downstream_closure",
            "act_surface.surface_violations",
            "downstream_closure(dag, node)",
            f"{SF}::test_surface_stale_downstream_allowed",
            '"workstreams/WS-1/spec/10-requirements.md"',
        ),
        r(
            "w1_discovery",
            "act_surface.surface_violations",
            '"charter" in nodes',
            f"{SF}::test_surface_w1_discovery_allowed",
            '"workstreams/WS-1/spec/00-discovery/brief.md"',
        ),
    ),
    "act_surface._own_evidence": (
        r(
            "evidence_json",
            "act_surface._own_evidence",
            "EVIDENCE_RE.fullmatch(path)",
            f"{SF}::test_evidence_only_json",
            '"workstreams/WS-1/evidence/edge-check/design--requirements.md"',
        ),
        r(
            "evidence_ws",
            "act_surface._own_evidence",
            'path.startswith(evidence_dir(ws_id) + "/")',
            f"{SF}::test_evidence_only_json",
            '"workstreams/WS-OTHER/evidence/edge-check/design--requirements.json"',
        ),
    ),
    "runner._resume_wave#review_delivery": (
        r(
            "review_delivery",
            "runner._resume_wave",
            'op.get("act") == ACT_REVIEW_V1',
            f"{WR}::test_resume_approve_agent_merge_then_finalize",
            "act_node.WAITING_DELIVERY",
        ),
    ),
    "runner._resume_wave#pr_facts_failure": (
        r(
            "ops_failure",
            "runner._resume_wave",
            "except OPS_FAILURES",
            f"{WR}::test_resume_pr_facts_failure_is_stopped_review",
            '"stopped_review"',
        ),
    ),
    "approve_node._ensure_finalize_identity#find_pr_failure": (
        r(
            "ops_failure",
            "approve_node._ensure_finalize_identity",
            "except OPS_FAILURES",
            f"{OF}::test_finalize_identity_find_pr_failure_unresolved[CalledProcessError]",
            '"факт не установлен"',
        ),
    ),
    "approve_node._await_required_checks#pr_facts_failure": (
        r(
            "pr_facts",
            "approve_node._await_required_checks",
            "facts = _pr_facts(state, ops, pr)",
            f"{OF}::test_await_checks_pr_facts_failure_unresolved[CalledProcessError]",
            '"факт не установлен"',
        ),
    ),
    # --- df этап 3a (Tasks 4–6): типы доверия, дескрипторы и бюджет, пара по SHA
    "act_trust_types.Finding.__post_init__": (
        r(
            "kind",
            "act_trust_types.Finding.__post_init__",
            "self.kind not in KINDS",
            f"{TT}::test_finding_and_ranges_refusals_named",
            '"unknown finding kind"',
        ),
        r(
            "role",
            "act_trust_types.Finding.__post_init__",
            "self.role not in KINDS[self.kind]",
            f"{TT}::test_finding_and_ranges_refusals_named",
            '"not allowed for role"',
        ),
        r(
            "detail",
            "act_trust_types.Finding.__post_init__",
            "detail_size(self.detail) > MAX_DETAIL_BYTES",
            f"{TT}::test_finding_and_ranges_refusals_named",
            '"detail over 512 bytes"',
        ),
    ),
    "act_trust_types.RangeDescriptor.__post_init__": (
        r(
            "epochs",
            "act_trust_types.RangeDescriptor.__post_init__",
            "len(self.ranges) > MAX_EPOCHS",
            f"{TT}::test_finding_and_ranges_refusals_named",
            '"more than 64 epochs"',
        ),
    ),
    "act_trust_findings.range_descriptor": (
        r(
            "epochs",
            "act_trust_findings.range_descriptor",
            'raise UnsupportedSize("epochs")',
            f"{TF}::test_65_epochs_unsupported_size",
            '"unsupported_size: epochs"',
        ),
        r(
            "empty",
            "act_trust_findings.range_descriptor",
            'raise ValueError("no epochs")',
            f"{TF}::test_range_descriptor_malformed_epochs",
            '"no epochs"',
        ),
        r(
            "contiguous",
            "act_trust_findings.range_descriptor",
            "list(range(1, len(epochs) + 1))",
            f"{TF}::test_range_descriptor_malformed_epochs",
            '"not contiguous"',
        ),
        r(
            "open_before_last",
            "act_trust_findings.range_descriptor",
            "e.last_confirmed is None",
            f"{TF}::test_range_descriptor_malformed_epochs",
            '"open epoch 1"',
        ),
    ),
    "act_trust_findings.source_descriptor": (
        r(
            "full_name",
            "act_trust_findings.source_descriptor",
            'raise UnsupportedSize("policy_full_name")',
            f"{TF}::test_source_over_limit_unsupported_size",
            '"policy_full_name"',
        ),
        r(
            "branch",
            "act_trust_findings.source_descriptor",
            'raise UnsupportedSize("policy_branch")',
            f"{TF}::test_source_over_limit_unsupported_size",
            '"policy_branch"',
        ),
    ),
    "act_trust_findings.fit_verdict": (
        r(
            "route",
            "act_trust_findings.fit_verdict",
            "route not in ROUTE_KINDS",
            f"{TF}::test_kind_outside_route_rejected",
            '"unknown origin_route"',
        ),
        r(
            "reserved",
            "act_trust_findings.fit_verdict",
            "any(key in envelope for key in _RESERVED)",
            f"{TF}::test_reserved_keys_rejected",
            '"reserved"',
        ),
        r(
            "budget",
            "act_trust_findings.fit_verdict",
            'raise UnsupportedSize("verdict")',
            f"{TF}::test_envelope_over_budget_unsupported_size",
            '"verdict"',
        ),
    ),
    "act_trust_findings._finding_rows": (
        r(
            "route_kinds",
            "act_trust_findings._finding_rows",
            "f.kind not in allowed",
            f"{TF}::test_kind_outside_route_rejected",
            '"outside route"',
        ),
        r(
            "one_per_kind_role",
            "act_trust_findings._finding_rows",
            "(f.kind, f.role) in rows",
            f"{TF}::test_one_finding_per_kind_and_role",
            '"duplicate finding"',
        ),
    ),
    "act_trust_pair.read_pair": (
        r(
            "host",
            "act_trust_pair.read_pair",
            "binding.host != SUPPORTED_HOST",
            f"{TP}::test_pair_unsupported_host_refused",
            "\"привязка: хост 'gitlab.example' не поддерживается v1\"",
        ),
        r(
            "sha",
            "act_trust_pair.read_pair",
            "not _SHA.fullmatch(head)",
            f"{TP}::test_pair_refuses_non_sha",
            '"пара df-trust: A и H — полные SHA"',
        ),
        r(
            "not_descendant",
            "act_trust_pair.read_pair",
            'relation == "not_descendant" and base is not None',
            f"{TP}::test_pair_rollback_is_not_descendant",
            '"не потомок принятой"',
        ),
    ),
    "act_trust_pair.settings_findings": (
        r(
            "unverified_rebase",
            "act_trust_pair.settings_findings",
            "found == REBASE_PREMISE",
            f"{TP}::test_settings_hidden_rebase_is_unverified_per_role",
            '"allow_rebase_merge@df-trust"',
        ),
        r(
            "policy_branch",
            "act_trust_pair.settings_findings",
            "snap.source.branch != policy_branch",
            f"{TP}::test_runtime_source_branch_not_default_finding",
            "\"default branch 'trunk'\"",
        ),
        r(
            "gap",
            "act_trust_pair.settings_findings",
            "gap is not None and not findings",
            f"{TP}::test_settings_gap_without_finding_names_the_read",
            '"repos/owner/approval-policy/rules/branches/main"',
        ),
    ),
    "act_trust_pair._visible": (
        r(
            "identity",
            "act_trust_pair._visible",
            'body.get("full_name") != expected',
            f"{TP}::test_pair_root_identity_checked",
            '"привязка —"',
        ),
        r(
            "not_public",
            "act_trust_pair._visible",
            "private is True",
            f"{TP}::test_pair_private_repo_is_not_public_finding",
            "репо не публичный (private = true)",
        ),
        r(
            "visibility",
            "act_trust_pair._visible",
            "видимость не прочитана",
            f"{TP}::test_pair_visibility_unread_is_unavailable",
            '"owner/approval-policy: видимость не прочитана (None)"',
        ),
    ),
    # --- df этап 3a (Task 7): программа проверяющего v2
    "act_precheck_v2.parse": (
        r(
            "keys",
            "act_precheck_v2.parse",
            'f"keys: unexpected {extra}, missing {missing}"',
            f"{V2}::test_v2_unknown_key_exit_2",
            "\"keys: unexpected ['extra'], missing []\"",
        ),
        r(
            "values",
            "act_precheck_v2.parse",
            'f"invalid values: {bad}"',
            f"{V2}::test_v2_invalid_value_named_exit_2",
            "\"invalid values: ['ctx_id']\"",
        ),
    ),
    "act_precheck_v2._merge_request": (
        r(
            "route",
            "act_precheck_v2._merge_request",
            'common.origin_route != "merge"',
            f"{V2}::test_merge_cross_fields_exit_2[route]",
            '"cross-field: op merge needs origin_route merge"',
        ),
        r(
            "accepted_is_x",
            "act_precheck_v2._merge_request",
            'accepted.sha != data["expected_snapshot_sha"]',
            f"{V2}::test_merge_cross_fields_exit_2[accepted_not_x]",
            '"cross-field: trust_accepted.sha != expected_snapshot_sha"',
        ),
    ),
    "act_precheck_v2.answer": (
        r(
            "invariant",
            "act_precheck_v2.answer",
            "except ValueError as exc",
            f"{V2}::test_reader_invariant_is_named_unset",
            '"invariant_violated: epoch numbers not contiguous from 1"',
        ),
        r(
            "deadline",
            "act_precheck_v2.answer",
            "except (DeadlineExceeded, DeadlineAlarm)",
            f"{V2}::test_deadline_is_unset",
            '"deadline"',
        ),
        r(
            "unsupported_size",
            "act_precheck_v2.answer",
            "except UnsupportedSize as exc",
            f"{V2}::test_unsupported_size_is_typed",
            '"unsupported_size: epochs"',
        ),
    ),
    "act_precheck_v2._wire": (
        r(
            "shape",
            "act_precheck_v2._wire",
            "if problem is not None",
            f"{V2}::test_shape_contract_enforced",
            '"invariant_violated: source null with findings []"',
        ),
        r(
            "verdict_budget",
            "act_precheck_v2._wire",
            "except UnsupportedSize as exc",
            f"{V2}::test_verdict_overflow_is_fixed_size",
            '"unsupported_size: verdict"',
        ),
    ),
    "act_precheck_v2._history": (
        r(
            "approved_gh",
            "act_precheck_v2._history",
            "return Unavailable(NO_APPROVED_GH)",
            f"{V2}::test_keyed_binding_without_approved_gh_refused_not_path",
            "NO_APPROVED_GH",
        ),
    ),
    "act_precheck_v2.trust_part": (
        r(
            "policy_pin",
            "act_precheck_v2.trust_part",
            "policy.full_name.casefold() != source.repo.casefold()",
            f"{V2}::test_policy_pin_of_other_repo_irreproducible",
            "PIN_MISMATCH",
        ),
    ),
    "act_precheck_v2.policy_history_break": (
        r(
            "policy_history_break",
            "act_precheck_v2.policy_history_break",
            "isinstance(got, act_facts.HistoryBreak)",
            f"{V2}::test_reeval_merge_returns_policy_break",
            '"policy_history_break"',
        ),
    ),
    "act_precheck_v2._decided": (
        r(
            "findings_refuse",
            "act_precheck_v2._decided",
            "if findings:",
            f"{V2}::test_trust_op_rollback_is_finding_not_synthesised",
            '"perimeter_findings: history_unverifiable@df-trust"',
        ),
    ),
    "act_precheck_v2._premerge_deps": (
        r(
            "approved_gh",
            "act_precheck_v2._premerge_deps",
            "if ctx.gh is None",
            f"{V2}::test_premerge_deps_without_approved_gh_is_invariant",
            "premerge deps without an approved gh",
        ),
    ),
    "act_precheck_v2._shape_problem": (
        r(
            "findings_without_pair",
            "act_precheck_v2._shape_problem",
            "findings without a read pair",
            f"{V2}::test_shape_contract_enforced",
            '"findings without a read pair"',
        ),
        r(
            "source_iff",
            "act_precheck_v2._shape_problem",
            "null_source != bool(kinds & SOURCE_FINDINGS)",
            f"{V2}::test_shape_contract_enforced",
            '"invariant_violated: source null with findings []"',
        ),
    ),
    "act_precheck_v2.Adapter.status": (
        r(
            "stale_snapshot",
            "act_precheck_v2.Adapter.status",
            "snap.root.snapshot_sha != self.expected_sha",
            f"{V2}::test_stale_snapshot_adapter",
            '"stale_snapshot"',
        ),
    ),
    "act_precheck_v2.Adapter.record": (
        r(
            "kindless",
            "act_precheck_v2.Adapter.record",
            "without kind",
            f"{V2}::test_stale_snapshot_adapter",
            '"journal adapter: a violation of df-trust without kind"',
        ),
    ),
    "act_precheck_v2._merge": (
        r(
            "history_key",
            "act_precheck_v2._merge",
            "binding.history_gh_config_dir is None",
            f"{V2}::test_merge_without_history_key_refused",
            '"history_token_required: the binding has no key"',
        ),
        r(
            "approved_gh",
            "act_precheck_v2._merge",
            "return Answer(REFUSE, NO_APPROVED_GH)",
            f"{V2}::test_keyed_binding_without_approved_gh_refused_not_path",
            "NO_APPROVED_GH",
        ),
        r(
            "findings",
            "act_precheck_v2._merge",
            "if findings:",
            f"{V2}::test_merge_settings_finding_refuses_with_findings",
            '"ruleset_missing"',
        ),
    ),
    "act_precheck_v2._premerge_deps.snapshot": (
        r(
            "findings_stop",
            "act_precheck_v2._premerge_deps.snapshot",
            "if adapter.findings:",
            f"{V2}::test_merge_settings_finding_refuses_with_findings",
            "candidate.heads == []",
        ),
    ),
    "act_precheck_v2.surface_check.check": (
        r(
            "dag_variant",
            "act_precheck_v2.surface_check.check",
            "if dag is None",
            f"{V2}::test_candidate_legacy_dag_variant_refused",
            "\"surface_violation: dag variant 'legacy5' is not served in v1\"",
        ),
        r(
            "own_manifest",
            "act_precheck_v2.surface_check.check",
            "is not changed by this PR",
            f"{V2}::test_candidate_manifest_not_in_pr_refused",
            '"is not changed by this PR"',
        ),
        r(
            "surface",
            "act_precheck_v2.surface_check.check",
            'return ", ".join(bad) if bad else None',
            f"{V2}::test_candidate_foreign_file_refused",
            '"surface_violation: scripts/foreign.sh"',
        ),
    ),
    "act_precheck_v2.finalize": (
        r(
            "read_failed",
            "act_precheck_v2.finalize",
            "except OPS_FAILURES as exc",
            f"{V2}::test_finalize_read_failure_unset",
            '"read_failed: disk"',
        ),
    ),
    "act_precheck_v2._finalize_target": (
        r(
            "suffix",
            "act_precheck_v2._finalize_target",
            "has no finalize suffix",
            f"{V2}::test_finalize_branch_of_unknown_request_refused",
            "has no finalize suffix",
        ),
        r(
            "form",
            "act_precheck_v2._finalize_target",
            "is not a finalize branch",
            f"{V2}::test_finalize_not_a_finalize_branch_refused",
            "\"finalize_branch: 'spec/ws-7-approve-x-final' is not a finalize branch\"",
        ),
    ),
    "act_precheck_v2._finalize_in_history": (
        r(
            "history",
            "act_precheck_v2._finalize_in_history",
            'f"history_unavailable: {history.reason}"',
            f"{V2}::test_finalize_history_unavailable_unset",
            '"history_unavailable: first-parent"',
        ),
        r(
            "delivered",
            "act_precheck_v2._finalize_in_history",
            "len(found) != 1",
            f"{V2}::test_finalize_branch_of_unknown_request_refused",
            '"no_delivered_request: ws-7/approve-3-1-1"',
        ),
        r(
            "current",
            "act_precheck_v2._finalize_in_history",
            "if stale:",
            f"{V2}::test_finalize_superseded_request_refused",
            "\"request_not_current: nodes ['requirements']\"",
        ),
        r(
            "candidate_pr",
            "act_precheck_v2._finalize_in_history",
            'f"candidate_pr: {pr.reason}"',
            f"{V2}::test_finalize_candidate_pr_unreadable_unset",
            '"candidate_pr: no PR"',
        ),
    ),
    "act_precheck_v2._delivery": (
        r(
            "rejected",
            "act_precheck_v2._delivery",
            "isinstance(outcome, Rejected)",
            f"{V2}::test_finalize_delivery_not_accepted[rejected]",
            '"delivery_rejected: I_BYTES: x"',
        ),
        r(
            "waiting",
            "act_precheck_v2._delivery",
            "isinstance(outcome, WaitingDelivery)",
            f"{V2}::test_finalize_delivery_not_accepted[waiting]",
            '"delivery_waiting: candidate PR #5"',
        ),
        r(
            "unset",
            "act_precheck_v2._delivery",
            'f"delivery_unset: {why}"',
            f"{V2}::test_finalize_delivery_not_accepted[unset]",
            '"delivery_unset: facts down"',
        ),
    ),
    "act_precheck_v2.head_hashes": (
        r(
            "unreadable",
            "act_precheck_v2.head_hashes",
            "does not read",
            f"{V2}::test_head_hashes_refusals",
            "does not read",
        ),
        r(
            "projection",
            "act_precheck_v2.head_hashes",
            "except ProjectionError as exc",
            f"{V2}::test_head_hashes_refusals",
            '"маркер узла"',
        ),
    ),
    "act_precheck_v2._envelopes": (
        r(
            "merge_commit",
            "act_precheck_v2._envelopes",
            "accepted.merge_commit != current.merge_commit",
            f"{V2}::test_envelope_merge_commits_differ_refused",
            "merge_commit_mismatch: delivery decision nnnnnnnnnnnn",
        ),
        r(
            "set",
            "act_precheck_v2._envelopes",
            "touched != set(expected) or not plain",
            f"{V2}::test_finalize_wrong_envelopes_refused",
            '"envelope_set: the PR changes"',
        ),
        r(
            "unreadable",
            "act_precheck_v2._envelopes",
            "if raw is None",
            f"{V2}::test_envelope_unreadable_unset",
            '"envelope_unreadable: workstreams/ws-7/approvals/requirements.yaml"',
        ),
        r(
            "content",
            "act_precheck_v2._envelopes",
            'raw != text.encode("utf-8")',
            f"{V2}::test_finalize_wrong_envelopes_refused",
            '"envelope_content: workstreams/ws-7/approvals/requirements.yaml"',
        ),
    ),
}


_ADAPTER = "адаптер чтения steward: перевод исхода библиотеки; отказ не порождает (его порождает `_call`/`_own`)"
_HELPER = (
    "вычисляет вход суждения без собственной проверки-отказа; судит вызывающий вход"
)
_BUILD = "строит значение/запрос; проверок-отказов нет"
_IO = "транспорт/ввод-вывод без проверки-отказа"
_PC_FIELD = (
    "предикат формы поля запроса; отказ выносит `parse_request` (`invalid values`)"
)
NOT_JUDGMENT = {
    "act_surface.evidence_dir": "путь каталога evidence воркстрима; не суждение",
    "task_bridge._admission_refusal": "делегирует `act_status.noop_refusal` (место суждения)",
    **{
        f"act_facts.{n}": _ADAPTER
        for n in (
            "_v3",
            "gh_transport",
            "_delivery",
            "read",
            "open_review",
            "effective",
            "event_stamps",
            "review_requires",
            "_activation",
            "history",
            "activation",
        )
    },
    "act_anon._NoRedirect.redirect_request": "транспортная политика (3xx не исполняется); отказа об объекте нет",
    "act_anon._default_open": _IO,
    "act_anon._read_bounded": "чтение тела порциями; срок судит `left()` вызывающего",
    "act_anon.AnonTransport.__init__": _BUILD,
    "act_anon.AnonTransport._fetch": _IO,
    "act_forge_http.TokenTransport.__repr__": _BUILD,
    "act_forge_http.TokenTransport._fetch": _IO,
    "act_history.HistoryOps.first_parent_changes": "протокол",
    "act_history.HistoryOps.show_file": "протокол",
    "act_history.HistoryOps.rev_parse": "протокол",
    "act_history.HistoryOps.fetch_branch": "протокол",
    "act_history.HistoryOps.prs_containing_commit": "протокол",
    "act_history.HistoryOps.pr_facts": "протокол",
    "act_precheck._positive_int": _PC_FIELD,
    "act_precheck._matches": _PC_FIELD,
    "act_precheck._text": _PC_FIELD,
    "act_precheck._http_url": _PC_FIELD,
    "act_precheck._change": _PC_FIELD,
    "act_precheck._changes": _PC_FIELD,
    "act_precheck._default_transport": _BUILD,
    "act_precheck.request_digest": _BUILD,
    "act_precheck.verdict": _BUILD,
    "act_precheck.verdict_v2": "строит вердикт `tree_incomplete`; исход по `complete_v2`, который судит `precheck serve` (`df/cli._tree_complete`)",
    "act_precheck._judge_stop": "передаёт отказ `_stop_target` и решение `_stop_in_history`; своей проверки нет",
    "act_precheck._snapshot": _IO,
    "act_precheck._remove": _IO,
    "act_precheck._remove.report": _IO,
    "act_history_snapshot.git_auth_env": _BUILD,
    "act_history_snapshot._env": _BUILD,
    "act_history_snapshot._url": _BUILD,
    "act_history_snapshot._is_pull": "форма элемента ответа; отказ выносит `prs_containing_commit`",
    "act_history_snapshot.SnapshotHistory.__init__": _BUILD,
    "act_history_snapshot.SnapshotHistory.__repr__": _BUILD,
    "act_history_snapshot.SnapshotHistory._run": _IO,
    "act_history_snapshot.SnapshotHistory._left": "срок страницы REST; отказ выносит `_timeout`",
    "act_history_snapshot.SnapshotHistory.first_parent_changes": _IO,
    "act_history_snapshot.SnapshotHistory.show_file": _IO,
    "act_history_snapshot.SnapshotHistory.show_file_bytes": _IO,
    "act_history_snapshot.SnapshotHistory.rev_parse": _IO,
    "act_history_snapshot._full_ref": _HELPER,
    "act_history_snapshot.SnapshotHistory.pr_facts": "нормализация REST → `headRefOid`; судит `act_history._pr_head`",
    **{
        f"act_check.{n}": _HELPER
        for n in (
            "_activations",
            "_stamps",
            "_requires",
            "_until_t",
            "_mode_events",
            "_earlier",
            "_mode_history",
        )
    },
    "act_trust.TrustRoot.id_tuple": _BUILD,
    "act_trust.Trust.source": _BUILD,
    "act_trust.Trust.epochs": _BUILD,
    "act_trust.Trust.root": _BUILD,
    "act_trust.Trust.transport": _BUILD,
    "act_trust._self_report": "делегирует `_report` (место суждения)",
    "act_trust._history_self_report": "делегирует `_report` транспортом решения (место суждения)",
    "act_trust.Journal.status": "протокол (Protocol); суждение — у реализаций",
    "act_trust.Journal.record": "протокол (Protocol); запись, не суждение",
    # С-1: `act_trust.default_readers` остаётся `_BUILD` и после Task 11c (`Readers(gh_get)`);
    # места суждения Task 11c — в её Step 7
    "act_trust.default_readers": _BUILD,
    "act_trust.history_token": "строит окружение флота; проверки — `_auth_token` (место суждения)",
    "act_trust._repo_path": _BUILD,
    "act_trust.credentials": "перечень того, что процесс держит; судит `_self_report`",
    "act_trust._roles": _BUILD,
    "act_trust._chain": "делегирует `_walk` (место суждения)",
    "act_trust._history_report_of": "делегирует `_report_of` транспортом решения (место суждения)",
    "act_trust._refusal": "переводит находку истории в отказ читателя ветки; судят `_chain`, `_journal_versions`, `_checked_epochs`, `_source_at`",
    "act_trust.load_anchor_times": "собирает времена якорей; недоступность — значение, судит `_epoch_at`",
    "act_journal._now": _BUILD,
    "act_journal._publisher_present": "наличие файла издателя; выбор — `journal_for`",
    "act_journal.ForgeEpochs.__init__": _BUILD,
    "act_journal._root_id": _BUILD,
    "act_journal.ForgeEpochs.via": "задаёт транспорт решения; проверок нет",
    "act_journal.DevJournal.__init__": _BUILD,
    "act_journal.DevJournal._request": _BUILD,
    "act_journal.InstalledJournal.__init__": _BUILD,
    "act_journal.InstalledJournal.status.reload": "обратный вызов клиента издателя: отмечает сдвиг снимка (STALE решает `status`)",
    "act_identity.RequestIdentity.manifest_path": _BUILD,
    "act_identity.RequestIdentity.migration_marker": _BUILD,
    "act_identity.RequestIdentity.memo_key": _BUILD,
    "act_journal_wire._now": _BUILD,
    "act_journal_wire._root_id": _BUILD,
    "act_journal_wire.schema_digest": "отпечаток схемы базы; сверяет `_refusal`",
    "act_journal_wire._json": "разбор JSON строки наблюдения; `None` — не JSON",
    "act_journal_wire._no_constant": "`parse_constant`: NaN/Infinity — не JSON (`ValueError`)",
    "act_journal_wire.WireJournal.__init__": _BUILD,
    "act_journal_wire.DevStoreJournal.__init__": _BUILD,
    "act_status._rel": _BUILD,
    "act_status._node_paths": _BUILD,
    "act_status._no": "форматирует ответ провайдера `not_approved:<код>:<деталь>`; проверок нет",
    "act_status.real_deps": _BUILD,
    "act_status.real_deps.journal": _BUILD,
    "act_status.Decider.__init__": _BUILD,
    "act_status.Decider.journal": _BUILD,
    "act_status.Decider.trust": "грузит доверие один раз; отказы — `_load_journal`/`load_trust`",
    "act_status.Decider.context": "передаёт отказы `trust()`/`_load_journal`; своей проверки нет",
    "act_status._candidate_pattern": _BUILD,
    "act_status._settled": "предикат «ответ звена окончателен»; не суждение",
    "act_status.LegacyProbe.__init__": _BUILD,
    "act_status.LegacyProbe._changes": _IO,
    "act_status.Statuses.__init__": _BUILD,
    "act_status.Statuses._upstreams": _BUILD,
    "act_status.Statuses._stopped": "выбирает текст причины для уже решённого отказа (`_compute`)",
    "act_status._blobs": "фабрика читателя (`_blobs.read` — место суждения)",
    "act_status._statuses": _BUILD,
    "act_status.statuses_at_base": _BUILD,
    "act_status.mode_probe": "точка подмены для тестов вне act_*; суждение — `live_policy`",
    "act_status._gate_statuses": "собирает статусы для провайдера; суждения — `Statuses`",
    "act_status._gate_statuses.base_text": _IO,
    "act_status._gate_statuses.worktree_text": _IO,
    "act_node.generation_of": _BUILD,
    "act_node.request_of": _BUILD,
    "act_node.manifest_file": _BUILD,
    "act_node.label_for": _BUILD,
    "act_node._invalidate": "терминальная запись без условия; причина — у вызывающего",
    "act_node._legacy": "фабрика вердикта старого кода (`_legacy.verdict` — место суждения)",
    "act_node._agent_merge": "оркестровка агентского мержа; форма — `envelope_commit_defect`",
    "act_node._in_base": "отвечает фактом достижимости (`bool | None`); отказ — `_require_in_base`/`reopen`",
    "act_node.audit_late_dismissals": "аудит; решения не меняет (приёмка №10)",
    "act_node._create_successor": "оркестровка: создаёт преемника через `approve_node`; проверок нет",
    "act_node._require_successor_creatable": "делегирует гвардам `approve_node` (`require_clean_synced_base`, `_require_known`, `_require_upstream_ready`)",
    "act_node._merge_stop_pr.form": "замыкание проверки формы; суждение — `stop_pr_defect`",
    "act_node._remedy": _BUILD,
    "act_node.audit_completed": "аудит завершённых заявок; решения не меняет (приёмка №10)",
    "act_premerge_core._Stop.__init__": _BUILD,
    "act_premerge._parser": _BUILD,
    "act_premerge.real_deps": _BUILD,
    "act_app_token._NoRedirect.redirect_request": "транспортная политика; отказа об объекте нет",
    "act_app_token._http": _IO,
    "act_app_token._build_opener": _BUILD,
    "act_app_token._field": _BUILD,
    "act_app_token.main": "CLI вокруг `app_identity` и `revoke`",
    "act_barrier._kind": "классификация формы ветки; суждения — `_judge`",
    "act_barrier._head": _IO,
    "act_barrier._pending": "узел отдан на одобрение (`status: approval_pending`); не узел — `False`, отказ судит `_legacy_resolved`",
    # дополнения части B в существующие модули
    "ops._gate_check_exe": _BUILD,
    "ops._act_args": _BUILD,
    "ops.Ops.gate_snapshot_digest": "протокол",
    "ops.Ops.first_parent_changes": "протокол",
    "ops.Ops.pr_body_fact": "протокол",
    "ops.Ops.snapshot_open": "протокол",
    "ops.Ops.snapshot_reader": "протокол",
    "ops.Ops.snapshot_close": "протокол",
    "ops.RealOps.snapshot_reader": _BUILD,
    "ops.RealOps.snapshot_close": _IO,
    "ops.SnapshotOps.__init__": _BUILD,
    "ops.SnapshotOps._git": _IO,
    "ops.SnapshotOps.first_parent_changes": _IO,
    "ops.SnapshotOps.show_file": _IO,
    "ops.SnapshotOps.show_file_bytes": _IO,
    "ops.SnapshotOps.rev_parse": _IO,
    "ops.SnapshotOps.fetch_branch": _IO,
    "act_git_rows.parse_first_parent": "разбор вывода `git log`; не суждение",
    "ops.RealOps.gate_snapshot_digest": "возвращает код и вывод CLI; судит `gate_run`",
    "approval_ledger.record_review_act": "запись акта; проверок нет",
    "approval_ledger.audit_events": _BUILD,
    "approval_ledger.record_audit": "идемпотентная запись события аудита (повтор — `False`, не отказ)",
    "act_decision.find_act": "делегирует `_act` (часть A); `None` — нет акта, судит вызывающий",
    "act_decision.merger_allowed": "делегирует `_merger_ok` (часть A)",
    "act_envelope.expected_envelope": _BUILD,
    "act_settings.config_home": "правило пути XDG; относительный путь — откат на дефолт, не отказ",
    "approve_node._review_debt": _BUILD,
    "approve_node._legacy_outcome": "вычисляет опубликованность снимка; делегирует `_legacy_verdict`",
    "runner._stop_on_refusal": "выбор стоп-статуса по уже вынесенному отказу `approve_node` (вынесено из `_finalize_wave`); не суждение",
    # --- df этап 3a (Task 7): диспетчер v2, программа проверяющего v2, Tasks 4–6
    **{
        f"act_precheck.SnapshotRequest.{n}": "поле протокола запроса снимка; не суждение"
        for n in (
            "nonce",
            "full_name",
            "pr",
            "base_ref",
            "token",
            "api_base",
            "git_base",
        )
    },
    "act_precheck._wall": _BUILD,
    "act_precheck._emit": _IO,
    "act_precheck._serve_v2": "выбор по `complete_v2` (его судит `precheck serve`, `df/cli._tree_complete`) и отложенный вызов программы v2; своей проверки нет",
    "act_premerge_core.decide": "переводит `_Stop` проверки в код и причину; суждения — `_check`",
    "act_trust.FindingSink.record_finding": "протокол журнала с видом находки; не суждение",
    "act_app_installations.app_installations": "фабрика проверки; суждение — `installations_refusal`",
    "act_app_installations.app_installations.check": "делегирует `act_app_token.installations_refusal`",
    "act_trust_types.detail_size": _HELPER,
    "act_trust_types.clip_detail": _HELPER,
    "act_trust_findings.UnsupportedSize.__init__": _BUILD,
    "act_trust_findings._canonical": _BUILD,
    "act_trust_findings._plain": "сериализация dataclass для `_canonical`; иное значение — ошибка вызывающего (`TypeError`), не отказ",
    "act_trust_findings._fit_text": _HELPER,
    **{
        f"act_trust_findings.EpochLike.{n}": "протокол эпохи журнала; не суждение"
        for n in ("epoch", "anchor", "last_confirmed")
    },
    "act_trust_pair._checks": _BUILD,
    "act_trust_pair._cold": "чтение цепочки и журнала; суждения — `act_trust._checked_epochs`",
    "act_trust_pair._of": _BUILD,
    "act_trust_pair._relation": _HELPER,
    "act_trust_pair._warm": "выбор пути: `None` — холодный путь (база не описывает A), не отказ; суждения — `act_trust._checked_epochs`",
    "act_precheck_v2.validation_of": _BUILD,
    "act_precheck_v2._absolute_or_none": _PC_FIELD,
    "act_precheck_v2._bytes_at_most": _PC_FIELD,
    "act_precheck_v2._relative_dir": _PC_FIELD,
    "act_precheck_v2._object": _PC_FIELD,
    "act_precheck_v2._common": _BUILD,
    "act_precheck_v2.MergeRequest.nonce": _BUILD,
    "act_precheck_v2.Context.token_source": "строит источник токена по договору запуска; отсутствие `gh` судят `_history` и `_merge`",
    "act_precheck_v2._alarm": "механизм срока (рубеж 1); исход `unset: deadline` выносит `answer`",
    "act_precheck_v2._alarm.fire": "механизм срока (рубеж 1); исход `unset: deadline` выносит `answer`",
    "act_precheck_v2._reason": _BUILD,
    "act_precheck_v2._masked": _BUILD,
    "act_precheck_v2._envelope": _BUILD,
    "act_precheck_v2._trust_op": "передаёт исходы чтений и суждения `trust_part`, `policy_history_break`, `_decided`; своей проверки нет",
    "act_precheck_v2._findings_reason": _BUILD,
    "act_precheck_v2.Adapter.__init__": _BUILD,
    "act_precheck_v2.Adapter.record_finding": "хранит находку, найденную проверкой `load_trust`; не суждение",
    "act_precheck_v2.Adapter.add": "хранит находки `trust_part`; не суждение",
    "act_precheck_v2._id": _PC_FIELD,
    "act_precheck_v2._unverified": _BUILD,
    "act_precheck_v2._args": _BUILD,
    "act_precheck_v2.surface_check": "фабрика проверки; суждение — `surface_check.check`",
}

#: Ложные срабатывания детектора проверок-отказов: путь, похожий на отказ, отказом
#: о судимом объекте не является (функция остаётся в NOT_JUDGMENT).
GUARD_EXEMPT = {
    "act_status.Statuses._stopped": "`False` — «не остановлен», выбор текста причины",
    "approval_ledger.record_audit": "`False` — событие уже записано (идемпотентность)",
    "act_node.manifest_file": "`None` — заявка без маркера (legacy), не отказ",
    "act_barrier._pending": "`False` — файл без frontmatter не узел бандла (не отказ)",
    "act_journal_wire._json": (
        "`None` — не JSON; отказ решает проверка типа в `_observations_ok`"
    ),
    "act_trust_pair._warm": (
        "`None` — холодный путь (база не описывает A), `Unavailable` — проброс "
        "чтения; своего отказа нет"
    ),
}


#: Перекрёстная проверка: функция с признаком положительного суждения — в JUDGMENT.
MARKERS = {"APPROVED", "Accepted", "LegacyAccepted", "complete_request"}
REFUSE_CALLS = {
    "Unavailable",
    "Rejected",
    "Violation",
    "_Stop",
    "unavailable",
    "_unresolved",
    "_invalidate",
    "_no",
    "LegacyHalt",
}
CONDITIONAL = (
    ast.If,
    ast.IfExp,
    ast.While,
    ast.For,
    ast.Try,
    ast.ExceptHandler,
    ast.BoolOp,
)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
#: Утверждения, которые не называют причину конкретной проверки.
GENERIC = {
    "Unavailable",
    "isinstance",
    "== 2",
    "== 3",
    "== 0",
    "not_approved:",
    "unavailable:",
}


def _walk(module: str) -> dict[str, tuple[ast.AST, str]]:
    """Квалифицированное имя → (определение, исходник модуля), рекурсивно."""
    src = (ROOT / "governance" / f"{module}.py").read_text(encoding="utf-8")
    found: dict[str, tuple[ast.AST, str]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}"
                if not isinstance(child, ast.ClassDef):
                    found.setdefault(name, (child, src))
                visit(child, name)
            else:
                visit(child, prefix)

    visit(ast.parse(src), module)
    return found


def _definitions() -> dict[str, tuple[ast.AST, str]]:
    found: dict[str, tuple[ast.AST, str]] = {}
    for module in MODULES:
        found.update(_walk(module))
    for module, names in EXISTING_ADDITIONS.items():
        defs = _walk(module)
        for name in names:
            found[f"{module}.{name}"] = defs.get(f"{module}.{name}", (None, ""))
    return found


_ANY: dict[str, tuple[ast.AST, str]] = {}


def _function(qualified: str) -> tuple[ast.AST | None, str]:
    module = qualified.split(".")[0]
    if not any(k.startswith(module + ".") for k in _ANY):
        _ANY.update(_walk(module))
    node, src = _ANY.get(qualified, (None, ""))
    return node, (ast.get_source_segment(src, node) or "") if node is not None else ""


def _refusal_paths(fn: ast.AST) -> list[int]:
    """Строки условных путей к отказу в собственном теле функции."""
    found: list[int] = []

    def refusing(value: ast.AST | None) -> bool:
        if isinstance(value, ast.Call):
            func = value.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            # доменный отказ вида `cls(None, error=…)` (ответ транспорта) — тоже отказ (b5)
            return name in REFUSE_CALLS or any(
                kw.arg == "error" for kw in value.keywords
            )
        if isinstance(value, ast.Constant):
            return value.value is False or (
                isinstance(value.value, str)
                and value.value.startswith(("unavailable:", "not_approved:"))
            )
        if (
            isinstance(value, ast.JoinedStr)
            and value.values
            and isinstance(value.values[0], ast.Constant)
        ):
            return str(value.values[0].value).startswith(
                ("unavailable:", "not_approved:")
            )
        return isinstance(value, ast.Tuple) and any(refusing(e) for e in value.elts)

    def visit(node: ast.AST, conditional: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                continue
            inside = conditional or isinstance(node, CONDITIONAL)
            if inside and (
                isinstance(child, ast.Raise)
                or (isinstance(child, ast.Return) and refusing(child.value))
                # отказ, присвоенный имени/атрибуту и возвращённый позже (b5)
                or (
                    isinstance(child, (ast.Assign, ast.AnnAssign))
                    and refusing(child.value)
                )
                or (
                    isinstance(child, ast.Call)
                    and refusing(child)
                    and isinstance(node, ast.Expr)
                )
            ):
                found.append(child.lineno)
            visit(child, inside)

    visit(fn, False)
    return found + _refusal_or_none(fn)


def _refusal_or_none(fn: ast.AST) -> list[int]:
    """Форма «отказ | None»: функция возвращает и `None`, и значение, и хотя бы один из
    возвратов — условный (отказ-строка, кортеж нарушения, `None` как «не установлено»)."""
    returns: list[tuple[bool, bool, int]] = []

    def visit(node: ast.AST, conditional: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPES):
                continue
            inside = conditional or isinstance(node, CONDITIONAL)
            if isinstance(child, ast.Return):
                none = child.value is None or (
                    isinstance(child.value, ast.Constant) and child.value.value is None
                )
                returns.append((inside, none, child.lineno))
            visit(child, inside)

    visit(fn, False)
    if {none for _c, none, _l in returns} != {True, False}:
        return []
    return [line for inside, _none, line in returns if inside]


ROWS = [(site, *row) for site, rows in JUDGMENT.items() for row in rows]


def _twin(ref: str) -> tuple[ast.FunctionDef | None, str, str]:
    path, name = ref.split("::")
    file = ROOT / path
    if not file.is_file():
        return None, "", ""
    src = file.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == name.split("[")[0]
        ),
        None,
    )
    if fn is None:
        return None, "", src
    start = min([d.lineno for d in fn.decorator_list] + [fn.lineno])
    return fn, "\n".join(src.splitlines()[start - 1 : fn.end_lineno]), src


def test_every_definition_classified_exactly_once() -> None:
    defs = _definitions()
    missing = sorted(q for q, (node, _s) in defs.items() if node is None)
    assert missing == [], f"дополнения части B не найдены в коде: {missing}"
    judged = {k for k in JUDGMENT if "#" not in k}
    plain = set(NOT_JUDGMENT)
    assert sorted(set(defs) - judged - plain) == [], "определения вне реестров"
    assert sorted(judged & plain) == [], "определения в обоих реестрах"
    assert sorted((judged | plain) - set(defs)) == [], (
        "реестр называет исчезнувшие определения"
    )


def test_modified_functions_audited() -> None:
    """b5 (B3-9): новые проверки внутри изменённых существующих функций — в `JUDGMENT`."""
    guards = {k for k in JUDGMENT if "#" in k}
    expected = {
        f"{func}#{g}"
        for func, entry in MODIFIED_EXISTING.items()
        if isinstance(entry, tuple)
        for g in entry
    }
    assert sorted(guards ^ expected) == [], (
        "ключи `<функция>#<проверка>` ≠ MODIFIED_EXISTING"
    )
    missing = sorted(f for f in MODIFIED_EXISTING if _function(f)[0] is None)
    assert missing == [], f"изменённые функции не найдены: {missing}"
    assert [
        f for f, e in MODIFIED_EXISTING.items() if isinstance(e, str) and not e.strip()
    ] == []


def test_not_judgment_has_no_refusal_guard() -> None:
    """Правило b4: проверка-отказ в собственном теле — значит место суждения."""
    defs = _definitions()
    guarded = {q: _refusal_paths(defs[q][0]) for q in NOT_JUDGMENT}
    assert (
        sorted(q for q, lines in guarded.items() if lines and q not in GUARD_EXEMPT)
        == []
    )
    assert sorted(q for q in GUARD_EXEMPT if not guarded.get(q)) == [], (
        "устаревшие исключения"
    )


def test_every_judgment_has_rows_and_exemption_a_reason() -> None:
    assert [s for s, rows in JUDGMENT.items() if not rows] == []
    assert [s for s, why in NOT_JUDGMENT.items() if not why.strip()] == []


@pytest.mark.parametrize(
    "site,component,check,lit,twin,tlit", ROWS, ids=[f"{r[0]}:{r[1]}" for r in ROWS]
)
def test_row_check_literal_belongs_to_one_check(
    site, component, check, lit, twin, tlit
) -> None:
    """(a) литерал проверки — в исходнике функции проверки ровно один раз."""
    node, src = _function(check)
    assert node is not None, f"{site} × {component}: функции проверки {check} нет"
    assert src.count(lit) == 1, (
        f"{check}: литерал {lit!r} встречается {src.count(lit)} раз"
    )


def _empty_container(node: ast.AST) -> bool:
    """Пустой список/словарь (`[]`, `{}`) — сборщик, который наполняет проверяемый код.

    Структурно, а не сравнением `ast.dump` с узлом, собранным руками: на CPython 3.12
    у разобранного `[]` есть `ctx=Load()`, у `ast.List(elts=[])` — нет, и дампы
    расходились; 3.13 дописывает `ctx` по умолчанию и прячет пустые поля — дампы
    совпадали. Итог зависел от версии: на 3.12 сборщик `calls: list[int] = []`
    читался как «данные», и наблюдение `calls == [1]` не засчитывалось (CI #675)."""
    if isinstance(node, ast.List):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return False


def asserts_reason(fn: ast.FunctionDef, module_src: str, tlit: str) -> bool:
    """Причина участвует в проверяемом условии о результате (b5–b7, B3-10/B6-3/B7-1).

    Разбирается только `Assert.test` (не сообщение `msg`), на конъюнкты `and`. Конъюнкт —
    сравнение `==`/`in`/`is` (или `startswith`/`endswith`) с происхождением сторон:
    наблюдаемая сторона укоренена (база цепочки атрибутов/подписок/вызовов) в локальном
    значении или вызове, а не в ожиданиях; ожидания — литерал и имена, связанные с ним без
    вычисления (присваивание, `parametrize`, константы модуля). Принимается: (а) наблюдаемая
    сторона без литерала против стороны с литералом; (б) литерал называет наблюдаемое
    выражение (`decider._memo`) против чистого ожидания; (в) литерал — всё сравнение
    (`… is None`) с наблюдаемой и ожидаемой сторонами; либо литерал (имя) в
    `pytest.raises(..., match=…)`. Fail closed: сравнение только ожиданий, `or`, `not`, IfExp,
    `!=`/`not in`, тавтология, утверждение только о типе — не засчитываются."""

    def seg(node: ast.AST) -> str:
        return ast.get_source_segment(module_src, node) or ""

    pure = {
        "str",
        "repr",
        "len",
        "list",
        "tuple",
        "set",
        "frozenset",
        "sorted",
        "dict",
        "bool",
        "int",
    }

    def no_call(node: ast.AST) -> bool:
        """Нет вычисления вне лямбд и конструкторов данных (`dict(…)` — данные)."""
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, ast.Call) and not (
                isinstance(n.func, ast.Name) and n.func.id in pure
            ):
                return False
            stack.extend(
                c for c in ast.iter_child_nodes(n) if not isinstance(c, ast.Lambda)
            )
        return True

    def names_of(targets: list[ast.AST]) -> set[str]:
        """Только имена (в т. ч. в распаковке), не базы атрибутов/подписок."""
        out: set[str] = set()
        for t in targets:
            if isinstance(t, ast.Name):
                out.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                out |= names_of(list(t.elts))
        return out

    def assigns(scope: list[ast.AST]) -> list[tuple[set[str], ast.AST]]:
        return [
            (names_of(n.targets if isinstance(n, ast.Assign) else [n.target]), n.value)
            for n in scope
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
        ]

    own, module = assigns(list(ast.walk(fn))), assigns(ast.parse(module_src).body)
    constants = {
        n for names, _ in module for n in names
    }  # данные модуля — не наблюдение
    bound: set[str] = set()  # ожидания: связанные с литералом данные (без вычисления)
    for names, value in own + module:
        if tlit in seg(value) and no_call(value):
            bound |= names
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and "parametrize" in ast.unparse(dec.func)
            and tlit in seg(dec)
        ):
            first = dec.args[0]
            names = (
                first.value
                if isinstance(first, ast.Constant)
                else ",".join(
                    e.value
                    for e in getattr(first, "elts", [])
                    if isinstance(e, ast.Constant)
                )
            )
            bound |= {n.strip() for n in str(names).split(",")}
    changed = True
    while changed:  # имена, выведенные из ожиданий без вычисления (`a, b = TABLE[c]`)
        changed = False
        for names, value in own:
            if (
                no_call(value)
                and {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} & bound
            ):
                if not names <= bound:
                    bound |= names
                    changed = True

    # локальные данные без вычисления (`commit = "…"`) — ожидание; пустой контейнер — сборщик,
    # который наполняет проверяемый код (`seen = []`), — наблюдение
    data: set[str] = set()
    changed = True
    while changed:  # данные — из литералов и других данных, не из наблюдаемых имён
        changed = False
        for names, value in own:
            local = {
                t.id
                for c in ast.walk(value)
                if isinstance(c, ast.comprehension)
                for t in ast.walk(c.target)
                if isinstance(t, ast.Name)
            }
            refs = {r.id for r in ast.walk(value) if isinstance(r, ast.Name)} - local
            if (
                no_call(value)
                and not _empty_container(value)
                and refs <= bound | constants | data | pure
                and not names <= data
            ):
                data |= names
                changed = True

    def rooted(node: ast.AST) -> bool:
        """Наблюдаемое: корень цепочки — локальное значение/фикстура или вызов, не ожидание."""
        if isinstance(node, ast.Name):
            return node.id not in bound | constants | data
        if isinstance(node, (ast.Tuple, ast.List)):
            return bool(node.elts) and all(rooted(e) for e in node.elts)
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return rooted(node.value)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in pure:
                return any(rooted(a) for a in node.args)
            return rooted(node.func)
        return False

    def mentions(node: ast.AST) -> bool:
        return tlit in seg(node) or any(
            isinstance(n, ast.Name) and n.id in bound for n in ast.walk(node)
        )

    def root(node: ast.AST) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    def inputs_only(node: ast.AST) -> bool:
        """Ожидания в наблюдаемой стороне — только аргументы вызова (входы), не её корень."""
        args = {
            id(n)
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
            for a in [*c.args, *(k.value for k in c.keywords)]
            for n in ast.walk(a)
        }
        return all(
            id(n) in args
            for n in ast.walk(node)
            if isinstance(n, ast.Name) and n.id in bound
        )

    def observed(node: ast.AST) -> bool:
        return rooted(node) and tlit not in seg(node) and inputs_only(node)

    def expectation(node: ast.AST) -> bool:
        return no_call(node) and not any(
            isinstance(n, ast.Name) and rooted(n) for n in ast.walk(node)
        )

    def conjuncts(e: ast.AST) -> list[ast.AST]:
        if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.And):
            return [c for v in e.values for c in conjuncts(v)]
        return [e]

    def checks_reason(e: ast.AST) -> bool:
        if isinstance(e, ast.Compare):
            sides = [e.left, *e.comparators]
            pairs = [(a, b) for a in sides for b in sides if a is not b]
            if tlit in seg(e) and not any(tlit in seg(s) for s in sides):
                # литерал — всё сравнение вместе с оператором (`… is not None`, `x is refused`):
                # наблюдаемая сторона против ожидания или значения с другим корнем
                return any(
                    observed(a) and (expectation(b) or root(b) not in (None, root(a)))
                    for a, b in pairs
                )
            if not all(isinstance(op, (ast.Eq, ast.In, ast.Is)) for op in e.ops):
                return False
            return any(
                observed(a) and mentions(b) and (no_call(b) or tlit in seg(b))
                for a, b in pairs
            ) or any(rooted(a) and tlit in seg(a) and expectation(b) for a, b in pairs)
        if (
            isinstance(e, ast.Call)
            and isinstance(e.func, ast.Attribute)
            and e.func.attr in ("startswith", "endswith")
        ):
            return observed(e.func.value) and (
                tlit in seg(e) or any(mentions(a) for a in e.args)
            )
        return False

    for node in ast.walk(fn):
        if isinstance(node, ast.Assert) and any(
            checks_reason(c) for c in conjuncts(node.test)
        ):
            return True
        if isinstance(node, ast.Call) and "raises" in ast.unparse(node.func):
            if any(kw.arg == "match" and mentions(kw.value) for kw in node.keywords):
                return True
    return False


@pytest.mark.parametrize(
    "src,expected",
    [("[]", True), ("{}", True), ("[1]", False), ('{"k": 1}', False), ("()", False)],
)
def test_empty_container_is_structural(src: str, expected: bool) -> None:
    """Разобранный и собранный руками пустой контейнер судятся одинаково (3.12/3.13)."""
    assert _empty_container(ast.parse(src, mode="eval").body) is expected
    if expected:
        built = ast.List(elts=[]) if src == "[]" else ast.Dict(keys=[], values=[])
        assert _empty_container(built) is True


def _assert_src(body: str, module: str = "") -> bool:
    """Вспомогательное для самопроверки механизма: тело теста → asserts_reason."""
    src = (
        module
        + "\n\ndef test_twin(x):\n"
        + "\n".join("    " + ln for ln in body.splitlines())
    )
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef))
    return asserts_reason(fn, src, "ПРИЧИНА")


@pytest.mark.parametrize(
    "body,module,expected",
    [
        ('got = f()\nassert got.reason == "ПРИЧИНА"', "", True),
        # сборщик (пустой контейнер, наполняет проверяемый код) — наблюдение, на любой
        # версии CPython (3.12: `ast.dump` разобранного `[]` несёт `ctx`)
        ('calls: list[str] = []\nf(calls)\nassert calls == ["ПРИЧИНА"]', "", True),
        ('calls = []\nf(calls)\nassert calls == ["ПРИЧИНА"]', "", True),
        ('seen = {}\nf(seen)\nassert seen == {"k": "ПРИЧИНА"}', "", True),
        # непустой список — данные, не наблюдение: сравнение двух ожиданий
        ('calls = ["x"]\nassert calls == ["ПРИЧИНА"]', "", False),
        ('expected = "ПРИЧИНА"\ngot = f()\nassert got.reason == expected', "", True),
        ('with pytest.raises(E, match="ПРИЧИНА"):\n    f()', "", True),
        (
            'expected = "ПРИЧИНА"\ngot = f()\nassert isinstance(got, Unavailable)',
            "",
            False,
        ),
        ("got = f()  # ПРИЧИНА\nassert isinstance(got, Unavailable)", "", False),
        ("got = f()\nassert isinstance(got, Unavailable)", 'REASON = "ПРИЧИНА"', False),
        ("got = f()\nassert got.reason == REASON", 'REASON = "ПРИЧИНА"', True),
        ('unused = "ПРИЧИНА"\nassert f() == 3', "", False),
        ('got = f()\nassert isinstance(got, Unavailable), "ПРИЧИНА"', "", False),
        (
            'expected = "ПРИЧИНА"\ngot = f()\nassert isinstance(got, Unavailable), expected',
            "",
            False,
        ),
        ('assert "ПРИЧИНА"', "", False),
        (
            (
                'expected = {"reason": "ПРИЧИНА"}\ngot = f()\nassert isinstance(got, Unavailable)\n'
                'assert expected["reason"] == expected["reason"]'
            ),
            "",
            False,
        ),
        ('got = f()\nassert True or got.reason == "ПРИЧИНА"', "", False),
        ('got = f()\nassert got.reason != "ПРИЧИНА"', "", False),
        ('got = f()\nassert not got.reason == "ПРИЧИНА"', "", False),
        ('got = f()\nassert got.reason == "ПРИЧИНА" if x else True', "", False),
        (
            'got = f()\nassert isinstance(got, Unavailable) and got.reason == "ПРИЧИНА"',
            "",
            True,
        ),
        ('got = f()\nassert got.reason.startswith("ПРИЧИНА")', "", True),
        (
            'expected = dict(reason="ПРИЧИНА")\nassert expected["reason"] == "ПРИЧИНА"',
            "",
            False,
        ),
        ('assert REASON == "ПРИЧИНА"', 'REASON = "ПРИЧИНА"', False),
    ],
    ids=[
        "literal_in_assert",
        "collector_annotated",
        "collector_plain",
        "collector_dict",
        "nonempty_list_is_data",
        "bound_name",
        "raises_match",
        "assignment_only",
        "comment_only",
        "module_constant_unused",
        "module_constant_used",
        "exit_code_only",
        "message_only",
        "bound_in_message",
        "tautology",
        "expected_vs_expected",
        "or_true_branch",
        "not_equal",
        "negated",
        "ifexp",
        "and_conjunct",
        "startswith",
        "expected_dict_call",
        "constant_vs_literal",
    ],
)
def test_reason_link_mechanism(body: str, module: str, expected: bool) -> None:
    """B3-10: самопроверка механизма (b) на отрицательных и положительных двойниках."""
    assert _assert_src(body, module) is expected


@pytest.mark.parametrize(
    "site,component,check,lit,twin,tlit", ROWS, ids=[f"{r[0]}:{r[1]}" for r in ROWS]
)
def test_row_twin_asserts_exact_reason(site, component, check, lit, twin, tlit) -> None:
    """(b) двойник утверждает именно причину своей проверки (связь — по AST)."""
    fn, _text, module_src = _twin(twin)
    assert fn is not None, f"{site} × {component}: двойника {twin} нет"
    assert tlit.strip() not in GENERIC, (
        f"{twin}: утверждение {tlit!r} не называет причину"
    )
    assert asserts_reason(fn, module_src, tlit), (
        f"{twin}: {tlit!r} не входит в утверждение"
    )
    if "[" in twin:
        param = twin.split("[", 1)[1].rstrip("]")
        marks = [d for d in fn.decorator_list if "parametrize" in ast.unparse(d)]
        assert marks, f"{twin}: параметр назван, а тест не параметризован"
        literal_ids = [
            [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
            for d in marks
            if isinstance(d, ast.Call)
            for kw in d.keywords
            if kw.arg == "ids" and isinstance(kw.value, ast.List)
        ]
        if literal_ids:
            assert any(param in ids for ids in literal_ids), f"{twin}: такого id нет"


def test_markers_imply_judgment() -> None:
    def body_names(node: ast.AST) -> set:
        body = ast.Module(
            body=list(node.body), type_ignores=[]
        )  # без аннотаций сигнатуры
        return {getattr(n, "id", getattr(n, "attr", None)) for n in ast.walk(body)}

    marked = sorted(
        q for q, (node, _s) in _definitions().items() if body_names(node) & MARKERS
    )
    assert [m for m in marked if m not in JUDGMENT] == []
