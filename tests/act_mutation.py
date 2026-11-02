"""Обязательный мутационный проход по строкам реестра идентичности (Task 16 Step 4a).

Для КАЖДОЙ строки `tests/test_act_identity_table.py::ROWS`, включая двойники на стенде
`World` (`tests/test_act_node.py`, `tests/test_act_repropose.py`) и проверки внутри
изменённых существующих функций (`<функция>#<проверка>`): обойти проверку и запустить
названный двойник отдельно. Итог строки:

- `closed` — двойник упал на утверждении (`AssertionError`, `DID NOT RAISE`, regex
  `match=`, `pytest.fail`);
- `survived` — двойник прошёл; `error` — упал иным исключением; `n/a` — оператор не
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
    "act_anon.AnonTransport._fetch:transport_failure": (
        'return cls(None, error=self._mask(f"anon: {type(exc).__name__}: {exc}"))',
        "return cls(200, body={}, link=None)",
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
        '        raise RuntimeError(f"PR #{pr} не candidate/finalize заявки узла")',
        '        ident = RequestIdentity(product, "ws-9", "approve-9-9-9")',
    ),
    "act_barrier.live_review_requests:product_gaps": (
        'gaps.append(f"{product}: список PR неполон — {exc}")',
        "pass",
    ),
    "act_barrier.live_unmarked:pagination": (
        'gaps.append(f"список PR {product} неполон: {exc}")',
        "pass",
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
    "act_status._request_event:request:no_pr": (
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
    "act_status.load_history:base_unread": (
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
    "act_status.pr_for_merge:exactly_one": (
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
    "act_trust.history_token:spawn": (
        'return Unavailable(f"{where}: {type(exc).__name__}")',
        'return "ghp_mutant"',
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
        'return _register(journal, trust.snapshot, ("approval-policy", got.reason))',
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
    "ops.RealOps.snapshot_open:own_repo": (
        'tempfile.mkdtemp(prefix="act-snapshot-")',
        'os.path.join(tempfile.gettempdir(), "act-snapshot-shared")',
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
    ),
}
#: Обход невозможен (без данных продолжить нечем) — решение ревьюера с причиной.
RULED: dict[str, str] = {
    "act_check.check_delivery:not_merged": "PR не смёржен (или нет merged_at): нет T — перебор порядков на T невозможен, `parse_ts(None)`; обход с корректными данными подменил бы факт мержа, проверяемый этой же строкой; двойник утверждает `WaitingDelivery()`",
    "act_context.delivery_verdict:not_merged": "PR не смёржен: нет T и фактов доставки — продолжить без них нечем; обход с корректными данными невозможен, проверяется причина (O3) и отдельный двойник «ranges не читаются»",
    "act_journal.DevJournal._open:store": "хранилище журнала не открывается (битое/утраченное): продолжить без БД нечем; обход невозможен, проверяется причина (O3)",
    "act_journal.DevJournal.status:lost": "причина «lost» порождается хранилищем `df.publisher` (exc.reason), обработчик общий; обход невозможен (без БД нечем продолжить); двойник утверждает причину хранилища",
    "act_journal_wire.DevStoreJournal._open:missing": "нет файла блокировки — нет хранилища, продолжить без дескриптора нечем; обход невозможен, проверяется причина (O3)",
    "ops.RealOps.snapshot_open:refmap": "fetch снимка — по URL и во временный репозиторий без `remote.*`: git не применяет `remote.*.fetch` к fetch по URL (проверено), а `FETCH_HEAD` пишется во временный репозиторий, который удаляется; `--refmap=` и `--no-write-fetch-head` в `_SNAPSHOT_FETCH` — защита на случай смены источника; обход неотличим; неприкосновенность репозитория продукта проверяет двойник `test_snapshot_leaves_product_repo_untouched`",
}


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


def _run(twin: str) -> str:
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
    if done.returncode == 0:
        return "survived"
    tail = done.stdout[-4000:]
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
    args = p.parse_args(argv)
    if args.merge:
        return _merge(args.merge, args.out)
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
        path.write_text(mutated, encoding="utf-8")
        try:
            status = _run(twin)
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
