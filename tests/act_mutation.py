"""Обязательный мутационный проход по строкам реестра идентичности (Task 16 Step 4a).

Для КАЖДОЙ строки `tests/test_act_identity_table.py::ROWS`, включая двойники на стенде
`World` (`tests/test_act_node.py`, `tests/test_act_repropose.py`) и проверки внутри
изменённых существующих функций (`<функция>#<проверка>`): обойти проверку и запустить
названный двойник отдельно. Итог строки:

- `closed` — двойник упал на утверждении (`AssertionError`, `DID NOT RAISE`, regex
  `match=`, `pytest.fail`);
- `survived` — двойник прошёл; `error` — упал иным исключением либо красный ещё до
  мутации (двойник сначала прогоняется на чистом коде); `n/a` — оператор не
  применим. Любой из трёх БЛОКИРУЕТ коммит Task 16: двойник или оператор чинятся, либо
  строка вносится в `RULED` с причиной (решение ревьюера задачи, видно в диффе).

Операторы обхода: по умолчанию — условие самого внутреннего `if`/IfExp с литералом
проверки → `False`; для проверок, без которых дальнейший код падает (несущие), для
«принимающих» проверок, памяти и замыканий — `BYPASS`: замена, дающая корректные данные
(проверка «пропустила», дальше — штатный путь). Подмена причины («МУТАНТ», O3) —
только проверка причины: инструмент её не применяет, обход ею НЕ закрывается.

Сначала `--check` — только применимость операторов ко ВСЕМ строкам, без двойников
(b6: должно быть N/N; `n/a` чинится оператором `BYPASS` или записью `RULED`). Затем
запуск: `uv run --group governance python tests/act_mutation.py --out <файл.json>`
целиком или частями `--shard k/n --out <часть k>` с итогом `--merge <части…> --out
<файл.json>` (непрогнанная строка — отказ сборки); байткод не пишется и очищается в
`governance/` и `tests/` перед каждым мутантом (иначе мутант той же длины в ту же
секунду исполнит код предыдущего).

Исходник модуля восстанавливается в `finally`; прерванный извне прогон (`kill -9`)
оставляет мутанта на диске — после такого прогона `git diff governance/` обязан быть
пуст.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_act_identity_table import ROWS  # noqa: E402

#: Обход с корректными данными: `site:component` → (фрагмент в функции проверки, замена).
BYPASS: dict[str, tuple[str, str]] = {
    "act_anon.fetch_response:transport_failure": (
        "return cls(None, error=text)",
        "return cls(200, body={}, link=None)",
    ),
    "act_history_snapshot._git:deadline_kill": (
        'raise DeadlineExceeded("git: deadline passed") from None',
        "return subprocess.CompletedProcess(proc.args, -9, None, None)",
    ),
    "act_history_snapshot.SnapshotHistory._rest:deadline_during": (
        '_timeout(self._deadline, self._clock, "forge")\n            raise',
        "raise",
    ),
    "act_history_snapshot.SnapshotHistory._rest:deadline_after": (
        "self._left()  # an answer that completed past the deadline is no fact either",
        "pass",
    ),
    "act_history_snapshot.SnapshotHistory.prs_containing_commit:shape": (
        'raise ValueError(f"forge: commits/{sha[:12]}/pulls: unexpected shape")',
        "body = []",
    ),
    "act_precheck.serve:token_scrub": (
        'reason.replace(request.token, "[token]")',
        "reason",
    ),
    "act_precheck._locked:lock_deadline": (
        'raise DeadlineExceeded("precheck lock: deadline passed") from None',
        "break",
    ),
    "act_precheck._no_constant:constant": (
        'raise json.JSONDecodeError(f"constant {name}", name, 0)',
        "return float(name)",
    ),
    "act_precheck._decide:deadline": ('return UNSET, "deadline"', 'return ALLOW, ""'),
    "act_precheck._decide:read_failed": (
        'return UNSET, f"read_failed: {exc}"',
        'return ALLOW, ""',
    ),
    "act_precheck._stop_in_history:unreadable": (
        'return UNSET, f"stop_unreadable: {target.path} at {request.head_sha[:12]}"',
        'raw = b""',
    ),
    "act_precheck._stop_in_history:utf8": (
        'return REFUSE, "stop_record_invalid: not UTF-8"',
        'text = raw.decode("utf-8", "replace")',
    ),
    "act_precheck._stop_in_history:record_invalid": (
        'return REFUSE, f"stop_record_invalid: {exc}"',
        "record = target",
    ),
    "act_precheck._stop_in_history:history": (
        'return UNSET, f"history_unavailable: {history.reason}"',
        "history = ()",
    ),
    "act_precheck._stop_in_history:no_request": (
        'return REFUSE, f"no_delivered_request: node {target.node}"',
        'return ALLOW, ""',
    ),
    "act_precheck._stop_in_history:already_stopped": (
        'return REFUSE, f"already_stopped: node {target.node} g{gen}"',
        'return ALLOW, ""',
    ),
    "act_forge_http.TokenTransport._refusal:read_only_method": (
        'return f"read-only transport: {method} {path}"',
        "return None",
    ),
    "act_forge_http.TokenTransport.get_json:page_cap": (
        'raise OSError(f"forge: more than {MAX_PAGES} pages")',
        "return 200, items",
    ),
    "act_anon._locate:commit_unreadable": (
        'return Unavailable(f"{where}: коммит не прочитан: HTTP {resp.status}")',
        'sha = "1" * 40',
    ),
    "act_anon.contents_bytes:base64": (
        'return Unavailable(f"{where}: содержимое не base64")',
        'raw = b""',
    ),
    "act_app_token._bearer:key_unread": (
        'return Unavailable(f"ключ App {key} не прочитан")',
        'return "Bearer x"',
    ),
    "act_app_token._bearer:key_unsigned": (
        'return Unavailable(f"ключ App {key} не подписывает JWT (RS256)")',
        'return "Bearer x"',
    ),
    "act_app_token._page_ids:completeness": (
        'return Unavailable(f"GET {path} (стр. {page}): HTTP {status}")',
        "return items",
    ),
    "act_app_token._page_ids:pages_limit": (
        'return Unavailable(f"GET {path}: больше {MAX_PAGES} страниц")',
        "return items",
    ),
    "act_barrier._listing:list_failed": (
        "return [], str(exc) or repr(exc)",
        "return [], None",
    ),
    "act_barrier.live_unmarked:pr_exception": (
        'verdict = (f"#{pr.number}: факты PR не установлены — {exc!r}",)',
        "verdict = None",
    ),
    "act_barrier.main:crash_incomplete": (
        'барьер неполон", file=sys.stderr)\n        return EXIT_INCOMPLETE',
        'барьер неполон", file=sys.stderr)\n        return EXIT_LIVE',
    ),
    "act_barrier.main:usage": (
        (
            "    except SystemExit:  # argparse: --help или неверный аргумент — отчёта нет\n"
            "        return EXIT_INCOMPLETE"
        ),
        "    except SystemExit as exc:\n        return int(exc.code or 0)",
    ),
    "act_barrier.live_review_requests:pr_exception": (
        'marked = f"#{pr.number}: факты PR не установлены — {exc!r}"',
        "marked = False",
    ),
    "act_barrier.main:refused": ("return EXIT_REFUSED", "return EXIT_INCOMPLETE"),
    "act_barrier._candidate_nodes:pending": (
        "if found and _pending(text.value)",
        "if found",
    ),
    "act_barrier._identity:request_form": (
        "    if parsed is None:\n        return None",
        '    if parsed is None:\n        parsed = ("ws-9", "approve-9-9-9")',
    ),
    "act_barrier._legacy_resolved:merger_named": (
        'return (f"#{number}: кто и когда смёржил candidate — не прочитано",)',
        'by, at = "alice", "2026-10-01T10:00:00Z"',
    ),
    "act_barrier._legacy_resolved:node_in_base": (
        "NODE_MISSING\n            if",
        "None\n            if",
    ),
    "act_barrier._local_brief_stamp:file": ("stamped.is_file()", "True"),
    "act_barrier._local_brief_stamp:run": (
        'approval.get("repo") == product and approval.get("pr") == number',
        "True",
    ),
    "act_barrier._local_items:unreadable": (
        'gaps.append(f"леджер {run_id} не читается: {exc}")',
        "pass",
    ),
    "act_barrier._marked:absent": ("return Unmarked(NO_MANIFEST_FILE)", "return True"),
    "act_barrier._marked:unparsed": (
        'return Unmarked(f"{MANIFEST_UNPARSED}: {exc}")',
        "return True",
    ),
    "act_barrier._migrated:comments_unread": (
        'return f"#{number}: комментарии не прочитаны — {fact.detail}"',
        "return Unmarked(NO_MARKER)",
    ),
    "act_barrier._signed:frontmatter": (
        "return SIGNED_STATUS  # frontmatter не разобран — узел не одобрен",
        "return None",
    ),
    "act_barrier._signed:signer": ("parse_ts(later) > parse_ts(at)", "True"),
    "act_barrier.invalidate:request_form": (
        '        raise Refused(f"PR #{pr} не candidate/finalize заявки узла")',
        '        ident = RequestIdentity(product, "ws-9", "approve-9-9-9")',
    ),
    "act_check._admission:marker_before_bootstrap": ("ctx.request_act is None", "True"),
    "act_check._admission:mode_after_review": (
        "return Rejected(ADMISSION, admission.reason)",
        "return own",
    ),
    "act_check._decide:approver": (
        '        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)',
    ),
    "act_check._decide:dismissal": (
        '        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)',
    ),
    "act_check.check_delivery:act_head": (
        '        return Rejected(D1_NO_ACT, f"нет approve человека на {head_sha}")',
        '        act = ReviewView("0", "nobody", "User", "APPROVED", head_sha)',
    ),
    "act_facts._call:library_exception": (
        'return Unavailable(f"steward {what}: {type(exc).__name__}: {exc}")',
        "return None",
    ),
    "act_facts._own:steward_unavailable": (
        "return Unavailable(got.reason)",
        "return got",
    ),
    "act_journal.DevJournal.record:store_refused": (
        'return Unavailable(f"журнал периметра (dev): {exc.reason}")',
        "return None",
    ),
    "act_journal_wire.DevStoreJournal.record:insert_failed": (
        'return Unavailable(f"журнал периметра (dev): событие не записано: {exc}")',
        "return None",
    ),
    "act_journal_wire.DevStoreJournal.status:blocking": (
        '"blocked" if rows else "clear"',
        '"clear"',
    ),
    "act_journal_wire.WireJournal._call:garbled": (
        'return Unavailable(\n                f"{op}: ответ издателя не разобран (код {done.returncode})"\n            )',
        "return {}",
    ),
    "act_node._delivery_outcome:context": (
        'raise an._unresolved(f"доверенный контекст: {context.reason}")',
        "context = (None, None)",
    ),
    "act_node._undecided_delivery:accepted": (
        '    raise RuntimeError(\n        f"заявка {key}: доставка candidate-PR #{pr} засчитывается (акт есть) — "\n        "продолжение `--approve-node`, не переиздание"\n    )',
        '    return "mutant"',
    ),
    "act_node._undecided_delivery:legacy_accepted": (
        '    raise RuntimeError(\n        f"заявка {key}: доставка candidate-PR #{pr} засчитывается (акт есть) — "\n        "продолжение `--approve-node`, не переиздание"\n    )',
        '    return "mutant"',
    ),
    "act_node._manifest:parseable": (
        '_invalidate(state, key, f"манифест в одобренном коммите не читается: {exc}")',
        "return None",
    ),
    "act_premerge._approve_in_current_second:same_second": (
        "parse_ts(r.submitted_at) >= now",
        "False",
    ),
    "act_premerge._boundary:stamp": (
        'raise _Stop(EXIT_UNSET, f"метка события не разобрана: {exc}") from exc',
        'return parse_ts("2026-10-12T12:00:00Z")',
    ),
    "act_premerge._manifest_text:utf8": (
        'return Unavailable(f"{path}: не UTF-8")',
        'return ""',
    ),
    "act_premerge._check:manifest_present": (
        'raise _Stop(\n            EXIT_REFUSED, f"в {args.head[:12]} нет манифеста {ident.request_id}"\n        )',
        "return",
    ),
    "act_premerge._trust:app_unknown": (
        'raise _Stop(EXIT_UNSET, f"установки App: {found.reason}")',
        "found = None",
    ),
    "act_premerge.request_of_branch:form": (
        "    if match is None:\n        return None",
        '    if match is None:\n        match = re.fullmatch(pattern, "spec/ws-9-approve-9-9-9")',
    ),
    "act_status.Decider.__call__:memo_key": (
        "key = (*ident.memo_key, event.merge_commit)",
        "key = (event.merge_commit,)",
    ),
    "act_status.Decider.__call__:memo_request": (
        "key = (*ident.memo_key, event.merge_commit)",
        "key = (event.merge_commit,)",
    ),
    "act_status.Decider._decide:head_sha": (
        "head_sha=event.head_sha",
        'head_sha="0" * 40',
    ),
    "act_status.Decider._head_hashes:head_bytes": (
        '            if text is None:\n                return Unavailable(f"{entry.path} в {event.head_sha[:12]} не читается")',
        '            if text is None:\n                text = "---\\nspec_stage: x\\nstatus: approval_pending\\nversion: 1\\n---\\nтело\\n"',
    ),
    "act_status.Decider.legacy:contract_result": (
        "legacy=contract",
        'legacy=lambda: LegacyAccepted("alice")',
    ),
    "act_status.LegacyProbe._candidate:ledger_node": (
        'return _no(LEDGER_NODE, f"заявка {key} леджера не выносила узел {node}")',
        "return APPROVED",
    ),
    "act_status.LegacyProbe._candidate:ledger_record": (
        "        if op is None:\n",
        "        if op is None:\n            op = {}\n        if False:\n",
    ),
    "act_status.LegacyProbe._finalize_branch:via_pr": (
        '            return _no(NOT_VIA_PR, "подпись внесена не смёрженным PR")',
        "            merged = [0]",
    ),
    "act_status.Statuses._compute:delivery_verdict": (
        '            code = getattr(verdict, "code", type(verdict).__name__)\n            return f"not_approved:act:{code}"',
        '            verdict = Accepted(current.merge_commit, "x", "11", "alice")',
    ),
    "act_status.Statuses._envelope:ws_id": (
        'return f"узел {node}: нет конверта — не одобрен"',
        "return None",
    ),
    "act_status.Statuses.is_review:no_event": (
        "current_event(self.history, node) is not None",
        "True",
    ),
    "act_status.Statuses.of:memo_node": (
        "return self._memo[node]",
        "return next(iter(self._memo.values()))",
    ),
    "act_history._request_event:request:no_pr": (
        "    if isinstance(pr, Unavailable):\n        return pr",
        "    if isinstance(pr, Unavailable):\n        return None",
    ),
    "act_status._settings:settings": (
        'return Unavailable(f"настройки акта: {exc}")',
        "return ActSettings(False, False)",
    ),
    "act_status.fresh_base:closed": ("ops.snapshot_close(repo)", "pass"),
    "act_status.fresh_base:snapshot_unavailable": (
        'yield Unavailable(f"снимок {state.repo_slug}@{base}: {fact.detail}")',
        "yield base, ops",
    ),
    "act_status.gate_run:snapshot_digest": (
        'return Unavailable(f"gate-check --print-snapshot-digest: код {rc}: {digest}")',
        'return "review", str(out)',
    ),
    "act_history.load_history:base_unread": (
        "    if rows is None:\n        return Unavailable",
        "    if rows is None:\n        rows = []\n    if False:\n        return Unavailable",
    ),
    "act_status.noop_refusal:fresh_ref": (
        "statuses_at_base(state, reader, dag, live, deps, ref=ref)",
        "statuses_at_base(state, ops, dag, live, deps)",
    ),
    "act_status.noop_refusal:history_snapshot": (
        "load_history(ops, state, snapshot)",
        'load_history(ops, state, state.base_ref or "master")',
    ),
    "act_status.noop_refusal:legacy_snapshot": (
        "LegacyProbe(state, ops, snapshot, decider.legacy, dag)",
        'LegacyProbe(state, ops, state.base_ref or "master", decider.legacy, dag)',
    ),
    "act_status.noop_refusal:mode_unset": (
        'return f"режим акта продукта: {live.reason}"',
        "return None",
    ),
    "act_status.noop_refusal:stale_base": (
        'return f"свежая база: {snapshot.reason}"',
        'snapshot = (state.base_ref or "master", ops)',
    ),
    "act_history.pr_for_merge:exactly_one": (
        "    if len(found) != 1:",
        "    if not found:\n        found = [7]\n    if False:",
    ),
    "act_trust._checked_epochs:transition": (
        'return Unavailable(f"недопустимый переход журнала: {exc}")',
        "pass",
    ),
    "act_trust._journal_versions:journal_deleted": ("if versions:", "if False:"),
    "act_trust._modes:unread_set": (
        'return Unavailable(f"набор {a.merge_commit[:12]}: {policy.reason}")',
        "continue",
    ),
    "act_trust._register:violation_event": (
        'return Unavailable(f"нарушение периметра: {detail}")',
        "return None",
    ),
    "act_trust._self_reports:violation_wins": (
        "own if isinstance(own, tuple) or found is None else found",
        "found",
    ),
    "act_trust._auth_token:spawn": (
        'return Unavailable(f"{where}: {type(exc).__name__}")',
        'return "ghp_mutant"',
    ),
    "act_trust.history_token_at:call_dir": (
        'return Unavailable(f"{where}: каталог вызова: {type(exc).__name__}")',
        'call = tempfile.mkdtemp(prefix="gh-call-")',
    ),
    "act_trust.current_activation:open_epoch": (
        '    if not span:\n        return Unavailable("в открытой эпохе нет активаций")',
        "    if not span:\n        span = next(s for s in ranges.values() if isinstance(s, tuple) and s)",
    ),
    "act_trust.gh_get:not_json": (
        'return unavailable(f"gh api {path} ({cred.label}): ответ не JSON")',
        'return Fact(Outcome.FOUND, None, "ok")',
    ),
    "act_trust.load_binding:absent": (
        "    except FileNotFoundError:\n        return None\n",
        '    except FileNotFoundError as exc:\n        return Unavailable(f"привязка {path}: {exc}")\n',
    ),
    "act_trust.load_ranges:history_violation": (
        'return _register(journal, trust.snapshot, found, "policy_history_break")',
        "got = ()",
    ),
    "act_trust.load_ranges:unknown_anchor": (
        'ranges[epoch.epoch] = Unavailable(f"якорь эпохи {epoch.epoch}: {at.reason}")\n            continue',
        "at = t",
    ),
    "act_trust.product_mode:never_entered": (
        "if not entered:\n        return PRE_MIGRATION",
        'if not entered:\n        return "merge"',
    ),
    "act_trust.product_mode:prior_epoch": (
        'return Unavailable(f"история режима, эпоха {epoch.epoch}: {span.reason}")',
        "continue",
    ),
    "act_trust.read_trust_snapshot:head_named": (
        'return Unavailable(f"{repo}: голова {binding.df_trust_branch} не прочитана")',
        'head = "0" * 40',
    ),
    "approve_node._legacy_verdict:old_pin_missing": (
        "    if not pinned:\n",
        '    if not pinned:\n        pinned = {"sha": None, "fingerprint": None}\n    if False:\n',
    ),
    "approve_node._propose#mode_unavailable:mode": (
        'raise _unresolved(f"режим акта продукта (узел {node_id}): {live.reason}")',
        'live = act_status.LivePolicy("merge", None, None, None, None)',
    ),
    "approve_node._try_agent_merge#form:form": (
        "        form()\n        if form is not None\n        else _envelope_form_defect(state, ops, dag, op, nodes, head)",
        "        None",
    ),
    "ops.RealOps._credential_helper:gh_profile": (
        '"!gh auth git-credential"',
        '"!gh auth git-credential --other"',
    ),
    "ops.RealOps._snapshot_env:allowlist": (
        "{k: os.environ[k] for k in _SNAPSHOT_ENV_KEEP if k in os.environ}",
        "dict(os.environ)",
    ),
    "ops.RealOps._snapshot_env:empty_home": (
        'HOME=str(home),\n            XDG_CONFIG_HOME=str(home / ".config"),',
        'HOME=os.environ.get("HOME", ""),',
    ),
    "ops.RealOps._snapshot_env:no_global": (
        'GIT_CONFIG_NOSYSTEM="1",\n            GIT_CONFIG_GLOBAL=os.devnull,',
        'GIT_CONFIG_NOSYSTEM="1",',
    ),
    "ops.RealOps._snapshot_env:no_prompt": (
        '            GIT_TERMINAL_PROMPT="0",\n',
        "",
    ),
    "ops.RealOps._snapshot_env:profile_resolved": (
        "str(profile.expanduser().resolve())",
        "str(profile)",
    ),
    "ops.RealOps._snapshot_env:runner_profile": (
        'os.environ.get("REVIEW_GH_CONFIG_DIR") or REVIEW_GH_CONFIG_DIR',
        "REVIEW_GH_CONFIG_DIR",
    ),
    "ops.RealOps._snapshot_git:helper_reset": (
        '                "-c",\n                "credential.helper=",\n',
        "",
    ),
    "ops.RealOps._snapshot_url:canonical": (
        'f"https://github.com/{repo_slug}.git"',
        'f"https://example.invalid/{repo_slug}.git"',
    ),
    "act_node._head_hashes:head_bytes": (
        'return Unavailable(f"{entry.path} в {head[:12]} не читается")',
        'text = "---\\nspec_stage: x\\nstatus: approval_pending\\nversion: 1\\n---\\nтело\\n"',
    ),
    "act_node._finalize:commit_made": (
        'raise RuntimeError("коммит конверта не состоялся: HEAD не разрешается")',
        'head = "0" * 40',
    ),
    "act_check._legacy_verdict:legacy_missing": (
        'return Unavailable("вердикт старой проверки не вычислен")',
        'return LegacyAccepted("alice")',
    ),
    "approve_node._require_upstream_ready#mode_unset:mode": (
        'raise _unresolved(f"режим акта продукта (upstream {node_id}): {live.reason}")',
        'live = act_status.LivePolicy("merge", None, None, None, None)',
    ),
    "approve_node.read_dag_state#mode_unset:mode": (
        'return DagState(None, unresolved=f"режим акта продукта: {live.reason}")',
        'live = act_status.LivePolicy("merge", None, None, None, None)',
    ),
    "approve_node._supersedes_undecided:history_unread": (
        "if predecessor is None or isinstance(statuses.history, Unavailable):\n        return False",
        "if predecessor is None:\n        return False\n    if isinstance(statuses.history, Unavailable):\n        return True",
    ),
    "act_node.reopen:mode_unset": (
        'raise an._unresolved(f"режим акта продукта (--reopen {node}): {live.reason}")',
        'live = act_status.LivePolicy("merge", None, None, None, None)',
    ),
    "act_node.route:merged_unmarked_mode": (
        'raise an._unresolved(f"режим акта продукта: {live.reason}")',
        "live = act_status.LivePolicy(act_trust.PRE_MIGRATION, None, None, None, None)",
    ),
    "act_node._undecided_delivery:mode_unset": (
        'raise an._unresolved(f"режим акта продукта (--repropose {key}): {live.reason}")',
        "live = act_status.LivePolicy(act_trust.PRE_MIGRATION, None, None, None, None)",
    ),
    "act_node._marked_verdict:history": (
        'raise an._unresolved(f"история заявок узлов: {history.reason}")',
        "history = ()",
    ),
    "act_node._open:act": (
        "    if act is None:\n        return an.ApprovalOutcome(",
        '    if act is None:\n        act = type("Act", (), {"login": "nobody"})()\n    if False:\n        return an.ApprovalOutcome(',
    ),
    "act_app_token.installations_refusal:repos_unreadable": (
        'return Unavailable(f"репо установки {ident}: {repos.reason}")',
        "repos = []",
    ),
    "runner._step_gate#act_mode_unset:mode": (
        '    if isinstance(gate, Unavailable):\n        (run_dir(state.run_id) / "gate-findings.txt").write_text(',
        '    if isinstance(gate, Unavailable):\n        gate = ("legacy", None)\n    if False:\n        (run_dir(state.run_id) / "gate-findings.txt").write_text(',
    ),
    "runner._step_s8#act_mode_unset:mode": (
        "        if isinstance(gate, Unavailable):\n            print(",
        '        if isinstance(gate, Unavailable):\n            gate = ("legacy", None)\n        if False:\n            print(',
    ),
    "ops.RealOps.snapshot_open:own_repo": (
        'tempfile.mkdtemp(prefix="act-snapshot-")',
        'os.makedirs(os.path.join(tempfile.gettempdir(), "act-snapshot-shared"), exist_ok=True) or os.path.join(tempfile.gettempdir(), "act-snapshot-shared")',
    ),
    "ops.SnapshotOps.__getattr__:deny": (
        "        raise AttributeError(\n",
        "        return getattr(self._base, name)\n        raise AttributeError(\n",
    ),
    "runner._step_gate#completed_admission:admission": (
        "if refusal is None:",
        "if True:",
    ),
    "runner._step_s8#run_status:status": ('state.status = "running"', "pass"),
    "task_bridge.deliver_for_run#adopted:admission": (
        '_require_admission(\n            state, ops, legacy_bundle, f"принят существующий PR #{existing}"\n        )',
        "None",
    ),
    "task_bridge.deliver_for_run#completed:admission": (
        '_require_admission(\n            state, ops, legacy_bundle, f"tasks-спека уже доставлена (PR #{pr_done})"\n        )',
        "None",
    ),
    "task_bridge.deliver_superseded#redo_intent:admission": (
        '_require_admission(\n            state, ops, legacy_bundle, f"повтор доставки ревизии {n} из намерения"\n        )',
        "None",
    ),
    "task_bridge.deliver_superseded#replacement_repeat:admission": (
        '_require_admission(\n                state,\n                ops,\n                legacy_bundle,\n                f"возврат PR #{repeated.pr} замены первой доставки",\n            )',
        "None",
    ),
    "task_bridge.deliver_superseded#returned_merged:admission": (
        '_require_admission(\n                state, ops, legacy_bundle, f"возврат вмерженного PR #{pr} ревизии {n}"\n            )',
        "None",
    ),
    "task_bridge.deliver_superseded#returned_open:admission": (
        '_require_admission(\n                state, ops, legacy_bundle, f"возврат открытого PR #{pr} ревизии {n}"\n            )',
        "None",
    ),
    "task_bridge.deliver_superseded#returned_pr:admission": (
        '_require_admission(\n                state, ops, legacy_bundle, f"возврат PR #{pr} ревизии {n}"\n            )',
        "None",
    ),
    "task_bridge.deliver_superseded#returned_v1:admission": (
        '_require_admission(\n                    state, ops, legacy_bundle, f"возврат PR #{v1_pr} первой доставки"\n                )',
        "None",
    ),
    "task_bridge.deliver_superseded#review_noop:noop": (
        "if traceless and unconfirmed is None:",
        "if traceless:",
    ),  # интеграционное ревью части B, I-3: узкий `except` (класс C1) — двойник с
    # `CalledProcessError` краснит утверждение, а не прогон (`_outcome`)
    "act_history.pr_for_merge:ops_failure": (
        "    except OPS_FAILURES as exc:",
        "    except RuntimeError as exc:",
    ),
    "act_history._pr_head:ops_failure": (
        "    except OPS_FAILURES as exc:",
        "    except RuntimeError as exc:",
    ),
    "act_status.LegacyProbe._finalize_branch:ops_failure": (
        "        except OPS_FAILURES as exc:",
        "        except RuntimeError as exc:",
    ),
    "act_status.LegacyProbe._merged_candidate:ops_failure": (
        "        except OPS_FAILURES as exc:",
        "        except RuntimeError as exc:",
    ),
    "approve_node._commit_paths:ops_failure": (
        "    except OPS_FAILURES as exc:",
        "    except RuntimeError as exc:",
    ),
    "approve_node._ensure_finalize_identity#find_pr_failure:ops_failure": (
        "        except OPS_FAILURES as exc:",
        "        except RuntimeError as exc:",
    ),
    "approve_node._await_required_checks#pr_facts_failure:pr_facts": (
        "facts = _pr_facts(state, ops, pr)",
        "facts = ops.pr_facts(state.repo_slug, pr)",
    ),
    # m-7: прежнее поведение argparse (код 2 «повтор»)
    "act_premerge.main:usage": (
        "    except SystemExit:",
        "    except SystemExit as exc:\n        return int(exc.code or 0)\n    except ValueError:",
    ),
    # m-2: обход несущей проверки — сверка пропускает, доставка идёт штатно
    "act_node._require_head_content:unavailable": (
        'raise an._unresolved(f"self-hash узлов в голове PR #{pr}: {hashes.reason}")',
        "return",
    ),
    "approve_node._content_hash:act_projection": ("if act is None:", "if True:"),
    "approve_node._content_hash:projection_refused": (
        'raise RuntimeError(f"self-hash узла в проекции {act}: {exc}") from exc',
        "return na.self_hash(text)",
    ),
    # ревью PR #675: барьер и закрытый candidate живой заявки
    "act_barrier._judge:closed_live": (
        'label = live_local.get(number) if kind == "candidate" else None',
        "label = None",
    ),
    "act_barrier._local_items:live_local": (
        'items.append((f"{label} — заявка без маркера {where}", label, pr))',
        "pass",
    ),
    "act_barrier._local_items:with_candidate_pr": (
        'if op.get("act") == ACT_REVIEW_V1:',
        'if op.get("act") == ACT_REVIEW_V1 or op.get("candidate_pr") is not None:',
    ),
    "act_barrier.live_unmarked:local_dedup": (
        "pr is None or pr not in reported",
        "True",
    ),
    # N-5: узкий `except` на фактах candidate в resume
    "runner._resume_wave#pr_facts_failure:ops_failure": (
        "        except OPS_FAILURES as exc:",
        "        except RuntimeError as exc:",
    ),
    # I-2: отказ `approve_node` читается как ожидание
    "runner._deliver_review_candidate:unresolved": (
        (
            "        _stop_on_refusal(\n"
            '            state, ops, request, exc, f"доставка candidate-PR #{pr} заявки {request}"\n'
            "        )\n"
            "        return state"
        ),
        "        outcome = an.ApprovalOutcome(act_node.WAITING_HUMAN_APPROVAL)",
    ),
}
#: Обход невозможен (без данных продолжить нечем) — решение ревьюера с причиной.
RULED: dict[str, str] = {
    "act_precheck.parse_request:json": "запрос не JSON (или не UTF-8): данных нет, продолжить нечем; обход с корректными данными невозможен, проверяется причина (O3)",
    "act_precheck._stop_target:stop_path": "путь не путь записи остановки: нет `ws_id`, узла и поколения — продолжить нечем; обход невозможен, проверяется причина (O3)",
    "approve_node._try_agent_merge#stop_form_first:form_first": "у stop-PR (`base` и `form` переданы) первую проверку формы маскирует та же `form()` в цикле перед каждой попыткой мержа (`#stop_late_form`, closed): обход первой неотличим по исходу — мержа нет, отказ формы тот же; у finalize-PR (без `base`) первая проверка единственная и закрыта строкой `#form`",
    "ops.RealOps.snapshot_open:own_repo": "общий каталог двух снимков: второй `snapshot_open` падает на `mkdir home` (`FileExistsError`) до любых чтений — смешения снимков нет, мутант виден исключением, а не утверждением; независимость проверяет двойник `test_interleaved_snapshots_are_independent`",
    "runner._step_gate#act_mode_unset:mode": 'без проверки (O1) `act_mode, provider = gate` распаковывает `Unavailable` и падает `TypeError` — fail-closed, но исключением; BYPASS `gate = ("legacy", None)` пускает S4 дальше, и на стенде следующий шаг (`approve_node`) падает исключением «факт не установлен» — мутант виден, но не утверждением; тот же класс у S8 (`runner._step_s8#act_mode_unset`) закрыт утверждением',
    "act_status._load_journal:journal_unset": "журнала периметра нет (`deps.journal is None`): без него продолжить нечем — вызов `None`; обход с корректными данными подменил бы журнал решения; проверяется причина (O3)",
    "act_node.repropose:unknown": "заявки нет в леджере: переиздавать нечего (`op` = `None`), обход невозможен; проверяется причина (O3)",
    "act_node._marked_verdict:delivery_in_history": "события доставки заявки в истории нет: судить нечего (`Decider(None)`), обход с корректными данными подменил бы саму доставку; проверяется причина (O3)",
    "act_node.reopen:already_stopped": "действующее событие узла — остановка: поколения заявки нет, продолжить нечем; ранний выход идемпотентности, двойник утверждает отсутствие второго stop-PR",
    "act_check.check_delivery:not_merged": "PR не смёржен (или нет merged_at): нет T — перебор порядков на T невозможен, `parse_ts(None)`; обход с корректными данными подменил бы факт мержа, проверяемый этой же строкой; двойник утверждает `WaitingDelivery()`",
    "act_context.delivery_verdict:not_merged": "PR не смёржен: нет T и фактов доставки — продолжить без них нечем; обход с корректными данными невозможен, проверяется причина (O3) и отдельный двойник «ranges не читаются»",
    "act_journal.DevJournal._open:store": "хранилище журнала не открывается (битое/утраченное): продолжить без БД нечем; обход невозможен, проверяется причина (O3)",
    "act_journal.DevJournal.status:lost": "причина «lost» порождается хранилищем `df.publisher` (exc.reason), обработчик общий; обход невозможен (без БД нечем продолжить); двойник утверждает причину хранилища",
    "act_journal_wire.DevStoreJournal._open:missing": "нет файла блокировки — нет хранилища, продолжить без дескриптора нечем; обход невозможен, проверяется причина (O3)",
    "ops.RealOps.snapshot_open:refmap": "fetch снимка — по URL и во временный репозиторий без `remote.*`: git не применяет `remote.*.fetch` к fetch по URL (проверено), а `FETCH_HEAD` пишется во временный репозиторий, который удаляется; `--refmap=` и `--no-write-fetch-head` в `_SNAPSHOT_FETCH` — защита на случай смены источника; обход неотличим; неприкосновенность репозитория продукта проверяет двойник `test_snapshot_leaves_product_repo_untouched`",
}


def _wiring_sites() -> tuple[str, ...]:
    """Двойник места сборки — по ОДНОМУ узлу на каждую строку `WIRING_SITES`
    (ревью Task 16, I2): `-x` по непараметризованному узлу доказал бы только
    первое место."""
    from tests.test_act_wiring import WIRING_SITES

    node = "tests/test_act_wiring.py::test_wiring_site_decides_with_history_token"
    return tuple(f"{node}[{site}]" for site in sorted(WIRING_SITES))


_TRUST_SEAMS = _wiring_sites()
#: Обязательные мутации входа решения (Task 11c, C6; Task 16 Step 4a): функция,
#: фрагмент, замена, двойники — тесты Task 11c и каждая строка `WIRING_SITES`
#: (`test_wiring_site_decides_with_history_token`). Прогон — `--mandatory`.
MANDATORY: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "M1:transport_not_from_binding": (
        "act_trust.load_trust",
        "transport = history_transport(binding, open_url=open_url, token_source=token_source)",
        "transport = AnonTransport(open_url=open_url)",
        (
            "tests/test_act_trust.py::test_history_transport_built_from_binding",
            *_TRUST_SEAMS,
        ),
    ),
    "M2:history_self_report_skipped": (
        "act_trust.load_trust",
        "transport if keyed else None",
        "None",
        (
            "tests/test_act_trust.py::test_history_self_report_push_is_violation",
            *_TRUST_SEAMS,
        ),
    ),
    "M3:second_token_read": (
        "act_trust.load_trust",
        "    if isinstance(transport, Unavailable):\n        return transport\n",
        (
            "    if isinstance(transport, Unavailable):\n        return transport\n"
            "    history_transport(binding, open_url=open_url, token_source=token_source)\n"
        ),
        (
            "tests/test_act_trust.py::test_one_token_per_decision_through_journal",
            *_TRUST_SEAMS,
        ),
    ),
    "M4:credential_shapes_open": (
        "act_trust.load_trust",
        "refused = _creds_refusal(held) or _shared_profile_refusal(held, binding)",
        "refused = _shared_profile_refusal(held, binding)",
        (
            "tests/test_act_trust.py::test_caller_history_credential_refused_before_io",
            *_TRUST_SEAMS,
        ),
    ),
}


def _green(twin: str) -> bool:
    """Двойник зелёный на чистом коде (ревью Task 16, M7): красный двойник упал бы
    и без мутации — такая строка не «closed», а ошибка."""
    return _run(twin) == "survived"


def _mandatory(check: bool) -> list[dict]:
    """M1–M4: каждая мутация — по своим тестам Task 11c и по всем местам
    `WIRING_SITES`; выживший мутант блокирует коммит, как строка реестра."""
    results = []
    for key, (where, old, new, twins) in MANDATORY.items():
        path = ROOT / "governance" / f"{where.split('.')[0]}.py"
        src, fn = _locate(path, where)
        seg = (ast.get_source_segment(src, fn) or "") if fn is not None else ""
        if seg.count(old) != 1:
            results.append({"row": key, "status": "n/a", "note": "фрагмент не найден"})
            continue
        if check:
            results.append({"row": key, "status": "applicable"})
            continue
        red = [twin for twin in twins if not _green(twin)]
        if red:
            results.append(
                {
                    "row": key,
                    "status": "error",
                    "note": f"двойник красный без мутации: {red}",
                }
            )
            continue
        path.write_text(src.replace(seg, seg.replace(old, new), 1), encoding="utf-8")
        try:
            got = [_run(twin) for twin in twins]
        finally:
            path.write_text(src, encoding="utf-8")
        status = (
            "error"
            if "error" in got
            else ("closed" if all(g == "closed" for g in got) else "survived")
        )
        results.append(
            {"row": key, "status": status, "twins": dict(zip(twins, got, strict=True))}
        )
    return results


def _locate(path: Path, qualified: str) -> tuple[str, ast.AST | None]:
    src = path.read_text(encoding="utf-8")
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def find(node: ast.AST, names: list[str]) -> ast.AST | None:
        if not names:
            return node
        # все одноимённые определения, а не первое (протокол и реализация, `@overload`)
        return next(
            (
                got
                for c in ast.walk(node)
                if isinstance(c, defs) and c.name == names[0]
                for got in [find(c, names[1:])]
                if got is not None
            ),
            None,
        )

    return src, find(ast.parse(src), qualified.split(".")[1:])


def _offsets(src: str) -> list[int]:
    offs = [0]
    for line in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(line))
    return offs


def _guard_off(src: str, fn: ast.AST, lit: str) -> str | None:
    offs = _offsets(src)

    def pos(n: ast.AST, end: bool = False) -> int:
        return offs[(n.end_lineno if end else n.lineno) - 1] + (
            n.end_col_offset if end else n.col_offset
        )

    best = None
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp)):
            body = node.body if isinstance(node, ast.If) else [node.body]
            test = src[pos(node.test) : pos(node.test, True)]
            text = test + "".join(src[pos(b) : pos(b, True)] for b in body)
            if lit in text or (lit.startswith("if ") and lit[3:] in test):
                if best is None or pos(node) >= pos(best):
                    best = node
    if best is None:
        return None
    return src[: pos(best.test)] + "False" + src[pos(best.test, True) :]


def _run(twin: str, verbose: bool = False) -> str:
    # байткод — только своего кода (мутант меняет `governance/`); `.venv` не трогается
    for top in ("governance", "tests"):
        for cache in (ROOT / top).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
    done = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            twin,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    tail = done.stdout[-4000:]
    if verbose:
        print(f"--- {twin}\n{tail}", file=sys.stderr)
    if done.returncode == 0:
        return "survived"
    closed = r"^E\s+(AssertionError|assert )|DID NOT RAISE|Regex pattern did not match|^E\s+Failed:"
    return "closed" if re.search(closed, tail, re.MULTILINE) else "error"


def _shard(spec: str) -> tuple[int, int]:
    """`k/n` — k-я из n частей строк (1 ≤ k ≤ n): прогон частями укладывается в
    таймауты вызывающего; итог частей собирает `--merge`."""
    k, n = (int(x) for x in spec.split("/"))
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError(f"--shard {spec}: нужно 1 ≤ k ≤ n")
    return k, n


def _report(results: list[dict], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    blocking = [
        r for r in results if r["status"] not in ("closed", "ruled", "applicable")
    ]
    for r in blocking:
        print(f"act_mutation: {r['status']}: {r['row']}", file=sys.stderr)
    print(
        f"act_mutation: {len(results) - len(blocking)}/{len(results)} закрыто или решено"
    )
    return 1 if blocking else 0


def _merge(parts: list[Path], out: Path) -> int:
    """Итог частей: каждая строка ROWS ровно один раз, иначе отказ (непрогнанная
    строка блокирует так же, как выжившая)."""
    results = [
        r for part in parts for r in json.loads(part.read_text(encoding="utf-8"))
    ]
    keys = [f"{site}:{comp}" for site, comp, *_rest in ROWS]
    got = [r["row"] for r in results]
    if sorted(got) != sorted(keys):
        missing = sorted(set(keys) - set(got))
        extra = sorted(k for k in set(got) if got.count(k) > 1 or k not in keys)
        print(
            f"act_mutation: части неполны: нет {missing}, лишние {extra}",
            file=sys.stderr,
        )
        return 1
    order = {k: i for i, k in enumerate(keys)}
    return _report(sorted(results, key=lambda r: order[r["row"]]), out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="act_mutation")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--only", action="append", default=[])
    p.add_argument(
        "--check", action="store_true", help="только применимость, без двойников"
    )
    p.add_argument("--shard", type=_shard, help="k/n — прогнать k-ю часть строк")
    p.add_argument("--merge", nargs="+", type=Path, help="собрать итоги частей в --out")
    p.add_argument(
        "--verbose", action="store_true", help="хвост вывода двойника — в stderr"
    )
    p.add_argument(
        "--mandatory", action="store_true", help="только мутации M1–M4 (Task 11c)"
    )
    args = p.parse_args(argv)
    if args.merge:
        return _merge(args.merge, args.out)
    if args.mandatory:
        return _report(_mandatory(args.check), args.out)
    rows = list(ROWS)
    if args.shard:
        k, n = args.shard
        rows = rows[k - 1 :: n]
    results = []
    for site, comp, check, lit, twin, _tlit in rows:
        key = f"{site}:{comp}"
        if args.only and key not in args.only:
            continue
        path = ROOT / "governance" / f"{check.split('.')[0]}.py"
        src, fn = _locate(path, check)
        if fn is None:
            results.append(
                {"row": key, "status": "n/a", "note": "функция проверки не найдена"}
            )
            continue
        if key in RULED:
            results.append({"row": key, "status": "ruled", "note": RULED[key]})
            continue
        if key in BYPASS:
            old, new = BYPASS[key]
            seg = ast.get_source_segment(src, fn) or ""
            mutated = (
                src.replace(seg, seg.replace(old, new), 1)
                if seg.count(old) == 1
                else None
            )
        else:
            mutated = _guard_off(src, fn, lit)
        if mutated is None:
            results.append(
                {"row": key, "status": "n/a", "note": "оператор обхода не применим"}
            )
            continue
        if args.check:
            results.append({"row": key, "status": "applicable"})
            continue
        if not _green(twin):
            results.append(
                {"row": key, "status": "error", "note": "двойник красный без мутации"}
            )
            continue
        path.write_text(mutated, encoding="utf-8")
        try:
            status = _run(twin, args.verbose)
        finally:
            path.write_text(src, encoding="utf-8")
        results.append(
            {
                "row": key,
                "status": status,
                "operator": "BYPASS" if key in BYPASS else "O1",
            }
        )
    return _report(results, args.out)


if __name__ == "__main__":
    sys.exit(main())
