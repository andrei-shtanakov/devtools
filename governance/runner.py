"""Runner: шаговая машина S0–S7 governance-конвейера (спека §4/§5).

Единственная точка внешних эффектов — переданный `Ops` (T3): этот модуль не
делает ни одного `subprocess`/сетевого вызова сам. Каждый шаг с внешним
эффектом ведётся как `pending → started → completed` со стабильным operation
key в `RunState.ops` (T2); resume (повторный `advance()` над загруженным
состоянием) всегда начинается с reconciliation по фактическому состоянию,
описанной для каждого шага ниже (спека §4, дословно перенесено в код).

S8 (authoritative-фиксация после мержа, `merged_unverified`, remediation) —
вне охвата этого модуля (Task 5); `advance()` останавливается сразу после
завершения `merge`, оставляя `RunState.status == "running"` для дальнейшего
подхвата.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.bundle_state import candidate_state
from governance.merge_gate import PrFacts, decide
from governance.ops import Ops
from governance.policy_sources import build_authority, load_safety
from governance.run_state import (
    RunState,
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
    """Выполняет шаги S1..S7 до стопа (не-``running`` статус) либо конца.

    Каждая шаг-функция сама решает, продолжать ли (``True``) или прервать
    этот вызов ``advance()`` (``False``) — не только по смене статуса
    (`stopped_*`/`waiting_human_merge`), но и когда шаг намеренно откладывает
    продолжение на следующий вызов (S6, exit=4 — «повторить весь S6»).
    """
    steps = (
        _step_branch,
        _step_authoring,
        _step_gate,
        _step_push,
        _step_pr,
        _step_ready,
        _step_review,
        _step_verdict,
    )
    for step in steps:
        if state.status != "running":
            break
        if not step(state, ops):
            break
    return state


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
