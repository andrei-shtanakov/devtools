"""Цепочка после завершённого прогона в `mode = "test"` (план 3c-3).

Раннер сам проходит человеческую границу утверждения tasks-спеки: мерж
tasks-PR (ready, обязательные проверки, `human-merge.sh`), `spec-runner spec
approve tasks`, мерж approve-PR. Каждый шаг — операция `run.json`,
write-ahead: повтор `make spec-loop` продолжает с места остановки.
Стоп — строка `spec-loop (test): <причина>` и код 1; статус прогона не
меняется (цепочка идёт ПОСЛЕ прогона).
"""

from __future__ import annotations

from governance import approve_node as an
from governance import task_bridge as tb
from governance.ops import Ops
from governance.run_state import RunState, op_complete, op_start, op_status

TASKS_SKIP_TEST = (
    "режим test: tasks-спека утверждается раннером, человеческий approve "
    "пропущен по конфигурации"
)


def _stop(reason: str) -> bool:
    """Печатает причину стопа; `False` — цепочка встала."""
    print(f"spec-loop (test): {reason}")
    return False


def _complete(state: RunState, key: str, **extra: object) -> None:
    """`op_complete`, сохраняя поля стартовой записи (write-ahead не теряется).

    `op_complete` заменяет запись целиком, поэтому `pr`/`head`/`tasks_skip`
    переносятся явно.
    """
    kept = {k: v for k, v in state.ops.get(key, {}).items() if k != "status"}
    op_complete(state, key, **{**kept, **extra})


def _read_facts(ops: Ops, state: RunState, pr: int) -> dict | None:
    try:
        facts = ops.pr_facts(state.repo_slug, pr)
    except Exception as exc:  # noqa: BLE001 - любая ошибка форджи = стоп
        _stop(f"факты PR #{pr} не прочитаны: {exc}")
        return None
    return facts if isinstance(facts, dict) else None


def _checks_refusal(state: RunState, ops: Ops, pr: int, head: str, kind: str):
    """Причина отказа ожидания проверок либо `None` (зелёные)."""
    try:
        refusal = an._await_required_checks(state, ops, pr, head, kind=kind)
    except RuntimeError as exc:
        return f"факты {kind} #{pr} не установлены: {exc} — повторите"
    return refusal.reason if refusal is not None else None


def _merge_spec_pr(state: RunState, ops: Ops, key: str, pr: int, kind: str) -> bool:
    """Ready → проверки → `human-merge.sh` → pull; `False` — стоп."""
    if op_status(state, key) == "completed":
        return True
    facts = _read_facts(ops, state, pr)
    if facts is None:
        return False
    head = str(facts.get("headRefOid") or "")
    pr_state = facts.get("state")
    if pr_state == "MERGED":
        op_start(state, key, pr=pr, head=head)
        _complete(state, key)
        return True
    if pr_state == "CLOSED":
        return _stop(f"{kind} #{pr} закрыт без мержа — решите вручную")
    # Write-ahead: отметка пропуска — до эффектов.
    op_start(state, key, pr=pr, head=head, tasks_skip=TASKS_SKIP_TEST)
    if facts.get("isDraft") and not ops.mark_ready(state.repo_slug, pr):
        return _stop(f"{kind} #{pr}: gh pr ready не удался")
    reason = _checks_refusal(state, ops, pr, head, kind)
    if reason is not None:
        return _stop(f"{kind} #{pr} не вмержен — {reason}")
    rc = ops.human_merge(state.repo, pr, head, target_dir=state.target_dir)
    if rc != 0:
        return _stop(
            f"{kind} #{pr} не вмержен — human-merge.sh вернул {rc} "
            f"(--expect-head {head[:8]})"
        )
    ops.checkout_and_pull(state.target_dir, state.base_ref or "master")
    _complete(state, key)
    print(f"{kind} #{pr} вмержен — {TASKS_SKIP_TEST}")
    return True


def _approve_tasks(state: RunState, ops: Ops) -> bool:
    """`spec approve tasks`, если штампа нет; затем approve-PR (`tasks-approve`)."""
    key = "tasks-approve"
    if op_status(state, key) == "completed":
        return True
    op_start(state, key)
    try:
        tb.check_approved(state.target_dir, state.ws_id, state.bundle_dir)
    except RuntimeError:
        args = tb.approve_command(state.ws_id).split()[1:]
        rc = ops.spec_runner(state.target_dir, args)
        if rc != 0:
            return _stop(f"spec approve tasks вернул {rc}")
    try:
        approve_pr = tb.deliver_approve(state, ops)
    except RuntimeError as exc:
        return _stop(f"доставка approve-PR: {exc}")
    _complete(state, key, pr=approve_pr)
    return True


def run_chain(state: RunState, ops: Ops, tasks_pr: int) -> int:
    """Цепочка до approve-PR включительно: 0 — код можно проверять, 1 — стоп."""
    if not _merge_spec_pr(state, ops, "tasks-merge", tasks_pr, "tasks-PR"):
        return 1
    if not _approve_tasks(state, ops):
        return 1
    approve_pr = state.ops["tasks-approve"]["pr"]
    if not _merge_spec_pr(state, ops, "approve-merge", approve_pr, "approve-PR"):
        return 1
    # Шов для Task 3: здесь добавляется шаг `spec-run`.
    return 0
