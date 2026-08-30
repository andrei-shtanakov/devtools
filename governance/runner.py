"""Runner: шаговая машина S0–S7 governance-конвейера (спека §4/§5).

Единственная точка внешних эффектов — переданный `Ops` (T3): этот модуль не
делает ни одного `subprocess`/сетевого вызова сам. Каждый шаг с внешним
эффектом ведётся как `pending → started → completed` со стабильным operation
key в `RunState.ops` (T2); resume (повторный `advance()` над загруженным
состоянием) всегда начинается с reconciliation по фактическому состоянию,
описанной для каждого шага ниже (спека §4, дословно перенесено в код).

S8 (authoritative-фиксация после мержа) продолжает `advance()` сразу после
успешного `merge` в рамках того же вызова (агентский мерж). Когда S7 оставил
PR человеку (`waiting_human_merge`), S8 не запускается сам — явный `resume()`
проверяет факт мержа и выполняет только его. Провал S8 — терминально:
`merged_unverified` навсегда, с remediation-issue по ADR-ECO-006; дальнейшее
продвижение — только через `verify()`, дочерний run с `remediated_by`.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from governance.bundle_state import candidate_state
from governance.merge_gate import PrFacts, decide
from governance.ops import Ops, RealOps
from governance.policy_sources import build_authority, load_safety
from governance.run_state import (
    RunState,
    load,
    new_run,
    op_complete,
    op_start,
    op_status,
    run_dir,
    save,
)

_ROLLUP_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# (op key, kind для ops.author, ожидаемое имя файла в bundle_dir) — S2/S3.
_AUTHOR_STEPS = (
    ("author-charter", "charter", "00-charter.md"),
    ("author-requirements", "requirements", "10-requirements.md"),
    ("author-behaviour", "behaviour-spec", "15-behaviour-spec.md"),
)


def start(
    subject: str,
    repo: str,
    repo_slug: str,
    ws_id: str,
    target_dir: str,
    bundle_dir: str,
    profile: str,
    run_id: str,
    ops: Ops,
    merge_authority: str | None = None,
) -> RunState:
    """S0: новый прогон, затем сразу `advance()` до стопа/завершения."""
    state = new_run(
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        ws_id=ws_id,
        target_dir=target_dir,
        bundle_dir=bundle_dir,
        profile=profile,
        run_id=run_id,
        merge_authority=merge_authority,
    )
    save(state)
    return advance(state, ops)


def advance(state: RunState, ops: Ops) -> RunState:
    """Выполняет шаги S1..S8 до стопа (не-``running`` статус) либо конца.

    Каждая шаг-функция сама решает, продолжать ли (``True``) или прервать
    этот вызов ``advance()`` (``False``) — не только по смене статуса
    (`stopped_*`/`waiting_human_merge`), но и когда шаг намеренно откладывает
    продолжение на следующий вызов (S6, exit=4 — «повторить весь S6»).

    ``merged_unverified`` — терминально и навсегда (спека §5): повторный
    ``advance()`` над таким состоянием отвергается явно, продвижение — только
    через дочерний run (`verify`).
    """
    if state.status == "merged_unverified":
        raise ValueError(
            f"run {state.run_id!r} — merged_unverified навсегда; создайте "
            "verification-run через verify(...)"
        )
    steps = (
        _step_branch,
        _step_authoring,
        _step_gate,
        _step_push,
        _step_pr,
        _step_ready,
        _step_review,
        _step_verdict,
        _step_s8,
    )
    for step in steps:
        if state.status != "running":
            break
        if not step(state, ops):
            break
    return state


def resume(run_id: str, ops: Ops) -> RunState:
    """Явный подхват сохранённого run'а (спека §5).

    ``merged_unverified`` — отказ (навсегда, см. `advance`). Из
    ``waiting_human_merge`` — reconciliation по факту мержа: PR ``MERGED`` →
    фиксирует op ``merge`` (если ещё не зафиксирован) и выполняет только S8;
    PR всё ещё ``OPEN`` — состояние не меняется, run продолжает ждать
    человека. Любой другой статус — обычный ``advance()``.
    """
    state = load(run_id)
    if state.status == "merged_unverified":
        raise ValueError(
            f"run {run_id!r} — merged_unverified навсегда; создайте "
            "verification-run через verify(...)"
        )
    if state.status != "waiting_human_merge":
        return advance(state, ops)
    pr_facts_now = ops.pr_facts(state.repo_slug, state.pr)
    if pr_facts_now.get("state") != "MERGED":
        return state
    if op_status(state, "merge") != "completed":
        op_complete(state, "merge", merged=True)
    state.status = "running"
    save(state)
    _step_s8(state, ops)
    return state


def verify(parent_run_id: str, ops: Ops, run_id: str) -> RunState:
    """Дочерний verification-run для `merged_unverified`-родителя (спека §5).

    Новый ``RunState`` с теми же координатами (repo/ws_id/target_dir/
    bundle_dir/profile/branch/pr/head), полем ``remediated_by`` на родителя и
    выполняет только S8. Успех фиксируется у ПОТОМКА (``completed``);
    родитель не трогается — он остаётся `merged_unverified` навсегда.
    """
    parent = load(parent_run_id)
    if parent.status != "merged_unverified":
        raise ValueError(
            f"verify: parent-run {parent_run_id!r} не merged_unverified "
            f"(текущий статус {parent.status!r})"
        )
    child = new_run(
        subject=parent.subject,
        repo=parent.repo,
        repo_slug=parent.repo_slug,
        ws_id=parent.ws_id,
        target_dir=parent.target_dir,
        bundle_dir=parent.bundle_dir,
        profile=parent.profile,
        run_id=run_id,
        merge_authority=parent.merge_authority,
    )
    child.remediated_by = parent_run_id
    child.branch = parent.branch
    child.pr = parent.pr
    child.head = parent.head
    child.status = "running"
    save(child)
    _step_s8(child, ops)
    return child


def facts_from(
    pr_facts: dict[str, Any],
    files: list[str],
    threads: bool | None,
    bundle_dir: str,
) -> PrFacts:
    """Fail-closed маппинг сырых gh-фактов PR в `PrFacts` (спека §8).

    Отклонение от produces-сигнатуры брифа (три параметра): без явного
    `bundle_dir` `diff_class` не может отличить document-дифф от прочего —
    поле у функции нет способа "узнать" его иначе. Добавлен четвёртым
    обязательным параметром; вызывающая сторона (`_step_verdict` этого же
    модуля) передаёт `state.bundle_dir`.
    """
    checks = pr_facts.get("statusCheckRollup") or []
    if not checks:
        checks_rollup = "empty"
    else:
        conclusions = [(c.get("conclusion") or "") for c in checks]
        if all(c in _ROLLUP_GREEN for c in conclusions):
            checks_rollup = "green"
        elif any(c and c not in _ROLLUP_GREEN for c in conclusions):
            checks_rollup = "red"
        else:
            checks_rollup = "unknown"

    raw_mergeable = pr_facts.get("mergeable")
    if raw_mergeable == "MERGEABLE":
        mergeable = "mergeable"
    elif raw_mergeable == "CONFLICTING":
        mergeable = "conflicting"
    else:
        mergeable = "unknown"

    behind_base = pr_facts.get("mergeStateStatus") == "BEHIND"
    unresolved_threads = True if threads is None else bool(threads)

    prefix = bundle_dir.rstrip("/") + "/"
    diff_class = (
        "document"
        if all(f.startswith(prefix) or f.startswith("docs/") for f in files)
        else "code"
    )
    touches_authority_root = any(
        f.startswith(".github/") or f.startswith("profiles/") for f in files
    )
    return PrFacts(
        checks_rollup=checks_rollup,
        mergeable=mergeable,
        behind_base=behind_base,
        unresolved_threads=unresolved_threads,
        diff_class=diff_class,
        touches_authority_root=touches_authority_root,
    )


def _ensure_started(state: RunState, key: str) -> None:
    """`op_start`, если операция ещё `new`; `started`/`completed` не трогает."""
    if op_status(state, key) == "new":
        op_start(state, key)


def _step_branch(state: RunState, ops: Ops) -> bool:
    """S1: ветка `spec/<ws_id>-behaviour`; `ensure_branch` идемпотентен."""
    key = "branch"
    state.branch = f"spec/{state.ws_id}-behaviour"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    ops.ensure_branch(state.target_dir, state.branch)
    op_complete(state, key)
    return True


def _step_authoring(state: RunState, ops: Ops) -> bool:
    """S2/S3: charter/requirements/behaviour-spec — файл есть → пропустить."""
    for key, kind, filename in _AUTHOR_STEPS:
        if op_status(state, key) == "completed":
            continue
        target = Path(state.target_dir) / state.bundle_dir / filename
        if target.exists():
            op_complete(state, key, skipped=True)
            continue
        _ensure_started(state, key)
        exit_code = ops.author(
            state.target_dir, kind, state.subject, state.bundle_dir
        )
        if exit_code != 0:
            state.status = "stopped_author"
            save(state)
            return False
        op_complete(state, key, skipped=False, exit=exit_code)
    return True


def _step_gate(state: RunState, ops: Ops) -> bool:
    """S4: prospective-гейт; error_count > 0 → stopped_gate + findings-файл."""
    key = "gate-candidate"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    bundle = candidate_state(
        Path(state.target_dir) / state.profile,
        Path(state.target_dir) / state.bundle_dir,
    )
    if bundle.error_count > 0:
        findings = [f for node in bundle.nodes for f in node.findings]
        findings.extend(bundle.bundle_findings)
        text = "\n".join(findings) + ("\n" if findings else "")
        (run_dir(state.run_id) / "gate-findings.txt").write_text(
            text, encoding="utf-8"
        )
        state.status = "stopped_gate"
        save(state)
        return False
    op_complete(
        state,
        key,
        error_count=bundle.error_count,
        required_absent=list(bundle.required_absent),
    )
    return True


def _step_push(state: RunState, ops: Ops) -> bool:
    """S5a: push черновой ветки."""
    key = "push"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    ops.push_branch(state.target_dir, state.branch)
    op_complete(state, key)
    return True


def _step_pr(state: RunState, ops: Ops) -> bool:
    """S5b: PR черновиком; started → find_pr первым — второй PR не открывать."""
    key = "pr"
    status = op_status(state, key)
    if status == "completed":
        return True
    if status == "started":
        existing = ops.find_pr(state.repo_slug, state.branch)
        if existing is not None:
            state.pr = existing
            op_complete(state, key, number=existing)
            return True
    else:
        op_start(state, key)
    title = f"{state.subject} — behaviour bundle {state.ws_id}"
    body = f"Автоматический прогон governance runner'а ({state.run_id})."
    pr_number = ops.create_draft_pr(
        state.target_dir,
        state.repo_slug,
        state.branch,
        title,
        body,
        "codex-review",
    )
    state.pr = pr_number
    op_complete(state, key, number=pr_number)
    return True


def _step_ready(state: RunState, ops: Ops) -> bool:
    """S6a: снять draft-статус перед ревью."""
    key = "ready"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    ops.mark_ready(state.repo_slug, state.pr)
    op_complete(state, key)
    return True


def _step_review(state: RunState, ops: Ops) -> bool:
    """S6b: review-pr.sh; started → перезапустить (дедуп по fp — дёшево)."""
    key = "review"
    _ensure_started(state, key)
    if op_status(state, key) == "completed":
        return True
    exit_code = ops.review(state.repo, state.pr)
    if exit_code == 0:
        op_complete(state, key, exit=exit_code)
        return True
    if exit_code == 1:
        ops.comment(
            state.repo_slug, state.pr, "ревью нашло находки, прогон остановлен"
        )
        state.status = "stopped_review"
        save(state)
        return False
    if exit_code in (2, 3):
        ops.comment(state.repo_slug, state.pr, "прибор не отработал")
        state.status = "stopped_review"
        save(state)
        return False
    # exit_code == 4 (или иной неопознанный) — голова PR уехала: сбросить
    # весь S6 (ready+review) в pending, повторить целиком на следующем advance.
    state.ops.pop("ready", None)
    state.ops.pop(key, None)
    save(state)
    return False


def _step_verdict(state: RunState, ops: Ops) -> bool:
    """S7: merge_gate → agent продолжает мержем, human/refuse — стоп."""
    key = "verdict"
    if op_status(state, key) == "completed":
        decision = state.ops[key]["decision"]
        reason = state.ops[key]["reason"]
    else:
        _ensure_started(state, key)
        authority = build_authority(Path(state.target_dir), state.merge_authority)
        safety = load_safety()
        review_exit = state.ops.get("review", {}).get("exit")
        pr_facts_raw = ops.pr_facts(state.repo_slug, state.pr)
        files = ops.pr_files(state.repo_slug, state.pr)
        threads = ops.unresolved_threads(state.repo_slug, state.pr)
        facts = facts_from(pr_facts_raw, files, threads, state.bundle_dir)
        verdict = decide(authority, safety, review_exit, facts)
        decision, reason = verdict.decision, verdict.reason
        op_complete(state, key, decision=decision, reason=reason)

    if decision == "agent":
        return _step_merge(state, ops)

    ops.comment(state.repo_slug, state.pr, f"merge_gate: {decision} — {reason}")
    state.status = "stopped_gate" if decision == "refuse" else "waiting_human_merge"
    save(state)
    return False


def _step_merge(state: RunState, ops: Ops) -> bool:
    """S7 (продолжение): merge; started → pr_facts.state==MERGED → complete."""
    key = "merge"
    status = op_status(state, key)
    if status == "completed":
        return True
    if status == "new":
        state.head = ops.head_sha(state.target_dir, state.branch)
        op_start(state, key)
    else:
        pr_facts_now = ops.pr_facts(state.repo_slug, state.pr)
        if pr_facts_now.get("state") == "MERGED":
            op_complete(state, key, merged=True)
            return True
    merged = ops.merge(state.repo_slug, state.pr, state.head)
    if merged:
        op_complete(state, key, merged=True)
        return True
    ops.comment(state.repo_slug, state.pr, "мерж не удался, ждёт человека")
    state.status = "waiting_human_merge"
    save(state)
    return False


def _step_s8(state: RunState, ops: Ops) -> bool:
    """S8: authoritative-гейт на дефолтной ветке после мержа (спека §5).

    exit 0 → `completed`. Не-0 → `merged_unverified` НАВСЕГДА: findings в
    `run_dir/s8-findings.txt`, remediation-issue в целевом репо по
    inbox-контракту ADR-ECO-006 (тело начинается `slug:`/`from:`). Op
    `gate-authoritative` завершается (`op_complete`) только при успехе —
    как и у S4 (`gate-candidate`), провал не маркируется completed, чтобы
    отличаться от «прошёл».
    """
    key = "gate-authoritative"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    exit_code = ops.gate_check_s8(state.target_dir, state.bundle_dir, state.profile)
    if exit_code == 0:
        op_complete(state, key, exit=exit_code)
        state.status = "completed"
        save(state)
        return True

    findings = f"gate-check (S8, authoritative) завершился с кодом {exit_code}\n"
    (run_dir(state.run_id) / "s8-findings.txt").write_text(
        findings, encoding="utf-8"
    )
    issue_title = f"beh-remediation: {state.subject} ({state.ws_id})"
    issue_body = (
        f"slug: beh-remediation-{state.ws_id}\n"
        f"from: devtools#{state.run_id}\n\n"
        f"{findings}"
    )
    ops.create_issue(state.repo_slug, issue_title, issue_body)
    state.status = "merged_unverified"
    save(state)
    return False


def _print_status(state: RunState) -> None:
    """Человекочитаемый дамп `RunState` для `start`/`resume`/`verify`/`status`."""
    print(f"run_id:        {state.run_id}")
    print(f"status:        {state.status}")
    print(f"subject:       {state.subject}")
    print(f"repo:          {state.repo} ({state.repo_slug})")
    print(f"ws_id:         {state.ws_id}")
    print(f"branch:        {state.branch or '-'}")
    print(f"pr:            {state.pr if state.pr is not None else '-'}")
    if state.remediated_by:
        print(f"remediated_by: {state.remediated_by}")
    print("ops:")
    for key in sorted(state.ops):
        op = state.ops[key]
        details = ", ".join(f"{k}={v}" for k, v in op.items() if k != "status")
        suffix = f" ({details})" if details else ""
        print(f"  {key}: {op['status']}{suffix}")


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m governance.runner start|resume|verify|status ...`.

    Все команды, кроме `status` (только читает `run.json`), строят `RealOps` —
    единственную точку внешних эффектов (git/gh/codex/gate-check, спека §5/§8).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    start_p = sub.add_parser("start", help="новый прогон, сразу advance() (S0..)")
    start_p.add_argument("--subject", required=True)
    start_p.add_argument("--repo", required=True)
    start_p.add_argument("--repo-slug", required=True)
    start_p.add_argument("--ws-id", required=True)
    start_p.add_argument("--target-dir", required=True)
    start_p.add_argument(
        "--bundle-dir", default=None, help="дефолт workstreams/<ws-id>/spec"
    )
    start_p.add_argument("--profile", default="profiles/team-exp.yaml")
    start_p.add_argument("--merge-authority", default=None, choices=["human"])
    start_p.add_argument(
        "--run-id", default=None, help="дефолт <ws-id>-<3 случайных байта hex>"
    )

    resume_p = sub.add_parser("resume", help="подхватить сохранённый прогон")
    resume_p.add_argument("--run-id", required=True)

    verify_p = sub.add_parser(
        "verify", help="verification-run для merged_unverified родителя"
    )
    verify_p.add_argument("--parent", required=True, help="run_id родителя")
    verify_p.add_argument("--run-id", required=True, help="run_id потомка")

    status_p = sub.add_parser("status", help="человекочитаемый дамп run.json")
    status_p.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)

    if args.command == "status":
        _print_status(load(args.run_id))
        return 0

    ops = RealOps()
    if args.command == "start":
        bundle_dir = args.bundle_dir or f"workstreams/{args.ws_id}/spec"
        run_id = args.run_id or f"{args.ws_id}-{os.urandom(3).hex()}"
        state = start(
            subject=args.subject,
            repo=args.repo,
            repo_slug=args.repo_slug,
            ws_id=args.ws_id,
            target_dir=args.target_dir,
            bundle_dir=bundle_dir,
            profile=args.profile,
            run_id=run_id,
            ops=ops,
            merge_authority=args.merge_authority,
        )
    elif args.command == "resume":
        state = resume(args.run_id, ops)
    else:
        state = verify(args.parent, ops, args.run_id)

    _print_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
