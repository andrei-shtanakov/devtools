"""Цепочка после завершённого прогона в `mode = "test"` (план 3c-3).

Раннер сам проходит человеческую границу утверждения tasks-спеки: мерж
tasks-PR (ready, обязательные проверки, `human-merge.sh`), `spec-runner spec
approve tasks`, мерж approve-PR. Каждый шаг — операция `run.json`,
write-ahead: повтор `make spec-loop` продолжает с места остановки.
Стоп — строка `spec-loop (test): <причина>` и код 1; статус прогона не
меняется (цепочка идёт ПОСЛЕ прогона).
"""

from __future__ import annotations

import subprocess

from governance import accept_pr
from governance import approve_node as an
from governance import run_state as rs
from governance import task_bridge as tb
from governance.ops import Ops
from governance.review_verdict import positive_on, record_review
from governance.run_state import RunState, op_complete, op_start, op_status, save

TASKS_SKIP_TEST = "утверждение задач человеком не требуется (test)"


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


def _mode_gate(state: RunState) -> bool:
    """Defense in depth: цепочка работает только в `test` при живом конфиге."""
    if state.mode != "test":
        return _stop(f"цепочка только для режима test, прогон {state.mode}")
    refusal = rs.mode_refusal(state)
    if refusal is not None:
        return _stop(refusal)
    return True


def _pull(state: RunState, ops: Ops) -> bool:
    try:
        ops.checkout_and_pull(state.target_dir, state.base_ref or "master")
    except RuntimeError as exc:
        return _stop(f"checkout/pull {state.target_dir} не удался: {exc}")
    return True


def _read_facts(ops: Ops, state: RunState, pr: int) -> dict | None:
    try:
        facts = ops.pr_facts(state.repo_slug, pr)
    except Exception as exc:  # noqa: BLE001 - любая ошибка форджи = стоп
        _stop(f"факты PR #{pr} не прочитаны: {exc}")
        return None
    if not isinstance(facts, dict):
        _stop(f"факты PR #{pr} не прочитаны: ответ форджи не объект")
        return None
    return facts


def _checks_refusal(
    state: RunState, ops: Ops, pr: int, head: str, kind: str
) -> str | None:
    """Причина отказа ожидания проверок либо `None` (зелёные)."""
    try:
        refusal = an._await_required_checks(state, ops, pr, head, kind=kind)
    except RuntimeError as exc:
        return f"факты {kind} #{pr} не установлены: {exc} — повторите"
    return refusal.reason if refusal is not None else None


def _merge_spec_pr(state: RunState, ops: Ops, key: str, pr: int, kind: str) -> bool:
    """Ready → проверки → `human-merge.sh` → pull; `False` — стоп."""
    if not _mode_gate(state):
        return False
    if op_status(state, key) == "completed":
        return True
    facts = _read_facts(ops, state, pr)
    if facts is None:
        return False
    head = str(facts.get("headRefOid") or "")
    pr_state = facts.get("state")
    if pr_state == "MERGED":
        # Возобновление после падения между мержем и завершением: запись
        # (с `tasks_skip`) сохраняется, база подтягивается до продолжения.
        if key not in state.ops:
            op_start(state, key, pr=pr, head=head)
        if not _pull(state, ops):
            return False
        _complete(state, key)
        return True
    if pr_state == "CLOSED":
        return _stop(f"{kind} #{pr} закрыт без мержа — решите вручную")
    if not head:
        return _stop(f"{kind} #{pr}: голова PR (headRefOid) не установлена")
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
    if not _pull(state, ops):
        return False
    _complete(state, key)
    print(f"{kind} #{pr} вмержен — {TASKS_SKIP_TEST}")
    return True


def _approve_tasks(state: RunState, ops: Ops) -> bool:
    """`spec approve tasks`, если штампа нет; затем approve-PR (`tasks-approve`)."""
    key = "tasks-approve"
    if not _mode_gate(state):
        return False
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
    except subprocess.CalledProcessError as exc:
        return _stop(
            f"доставка approve-PR: git вернул {exc.returncode} — штамп в "
            "рабочем дереве сохранён, повторите"
        )
    except RuntimeError as exc:
        return _stop(f"доставка approve-PR: {exc}")
    _complete(state, key, pr=approve_pr)
    return True


RUN_BRANCH_PREFIX = "spec-runner/run-"


def _open_run_prs(state: RunState, ops: Ops) -> list[int] | None:
    """Номера OPEN integration-PR раннера; `None` — форджа недоступна (стоп)."""
    try:
        prs = ops.prs_by_head_prefix(state.repo_slug, RUN_BRANCH_PREFIX)
    except RuntimeError as exc:
        _stop(f"список PR {RUN_BRANCH_PREFIX}* не прочитан: {exc}")
        return None
    return sorted(int(p["number"]) for p in prs if p.get("state") == "OPEN")


def _run_args(state: RunState) -> list[str]:
    return [
        "run",
        "--all",
        "--strict",
        f"--spec-prefix={state.ws_id}-",
        "--integration-pr",
    ]


def _spec_run(state: RunState, ops: Ops) -> int | None:
    """`spec-runner run … --integration-pr`; номер нового PR либо `None` (стоп).

    PR берётся только после НАБЛЮДЁННОГО кода 0. Возобновление начатой
    операции (падение либо записанный ненулевой код) перезапускает раннер:
    уже открытые `spec-runner/run-*` PR (частичные) уходят в `before` и
    не принимаются.
    """
    key = "spec-run"
    if not _mode_gate(state):
        return None
    if op_status(state, key) == "completed":
        return state.ops[key]["pr"]
    current = _open_run_prs(state, ops)
    if current is None:
        return None
    previous = state.ops.get(key)
    before = current
    if previous is not None:
        before = sorted(set(previous.get("before", [])) | set(current))
    op_start(state, key, before=before)
    rc = ops.spec_runner(state.target_dir, _run_args(state))
    if rc != 0:
        return _record_run_failure(state, ops, key, rc, before)
    after = _open_run_prs(state, ops)
    if after is None:
        return None
    new = [n for n in after if n not in before]
    if len(new) != 1:
        _stop(
            f"spec-runner run: ожидался один новый integration-PR, "
            f"найдено {len(new)}: {new}"
            if new
            else "spec-runner run: новый integration-PR не найден"
        )
        return None
    _complete(state, key, pr=new[0], exit=rc)
    return new[0]


def _record_run_failure(
    state: RunState, ops: Ops, key: str, rc: int, before: list[int]
) -> None:
    """Ненулевой код `spec-runner run`: `exit`/`failed_prs` в запись, стоп."""
    state.ops[key]["exit"] = rc
    save(state)
    after = _open_run_prs(state, ops)
    if after is None:
        return None
    failed = [n for n in after if n not in before]
    state.ops[key]["failed_prs"] = failed
    save(state)
    partial = ", ".join(f"#{n}" for n in failed) or "нет"
    _stop(
        f"spec-runner run вернул {rc}; частичные integration-PR: {partial} "
        "— не принимаются; разберите и повторите (повтор перезапустит раннер)"
    )
    return None


def _accept_code(state: RunState, ops: Ops, pr: int) -> int:
    """Приёмка integration-PR без мержа (`code-accept`): 0 — проверен."""
    key = "code-accept"
    if not _mode_gate(state):
        return 1
    facts = _read_facts(ops, state, pr)
    if facts is None:
        return 1
    head = str(facts.get("headRefOid") or "")
    record = state.ops.get(key)
    if record is None:
        op_start(state, key, pr=pr)
    elif record.get("status") == "completed":
        result = record.get("result") or {}
        if result.get("head") == head:
            print(
                f"spec-loop (test): {result.get('url')} — голова {head[:7]} "
                f"проверена; {accept_pr.ACCEPT_NO_MERGE_TEST}"
            )
            return 0
        # Новая голова: прежний итог — в `prev`, вердикт не переносится.
        op_start(state, key, pr=pr, prev=result)
    test = accept_pr.NoMergeAccept(
        known_positive=lambda h: positive_on(state, key, h),
        record_verdict=lambda v: record_review(state, key, v),
        record_result=lambda r: _complete(state, key, result=r),
    )
    return accept_pr.accept(
        state.repo, state.repo_slug, pr, ops, state.target_dir, test=test
    )


def run_chain(state: RunState, ops: Ops, tasks_pr: int) -> int:
    """Цепочка до приёмки integration-PR: 0 — код проверен (не вмержен), 1 — стоп."""
    if not _mode_gate(state):
        return 1
    if not _merge_spec_pr(state, ops, "tasks-merge", tasks_pr, "tasks-PR"):
        return 1
    if not _approve_tasks(state, ops):
        return 1
    approve_pr = state.ops["tasks-approve"]["pr"]
    if not _merge_spec_pr(state, ops, "approve-merge", approve_pr, "approve-PR"):
        return 1
    run_pr = _spec_run(state, ops)
    if run_pr is None:
        return 1
    return _accept_code(state, ops, run_pr)
