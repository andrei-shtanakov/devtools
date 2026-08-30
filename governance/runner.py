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
    all_run_ids,
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


def _reserve_run_id(run_id: str) -> None:
    """Атомарно резервирует `run_id`, отказывая на занятом (круг 4/7).

    Круг 4: занятый `run_id` молча перезаписывался (codex-ревью PR #88,
    major) — `new_run`+`save` пишут `run.json` через `os.replace`
    (атомарно), но БЕЗ проверки, что там уже есть чужой леджер. Первая
    починка (`exists()`-проверка, отдельно от записи) сама была TOCTOU
    (круг 7, codex-major): между проверкой и `save()` могла проскочить
    гонка двух параллельных `start()`/`verify()` с одним `run_id`.

    Починка: эксклюзивное создание файла (`Path.touch(exist_ok=False)` —
    `O_CREAT|O_EXCL` под капотом, атомарно на уровне ОС) резервирует слот
    за один системный вызов; `save()` дальше по коду штатно перезаписывает
    этот пустой файл тем же атомарным `os.replace`, что и всегда.
    """
    target = run_dir(run_id)
    target.mkdir(parents=True, exist_ok=True)
    try:
        (target / "run.json").touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(
            f"run {run_id!r} уже существует — используйте resume(...), "
            "не start(...)/verify(...) с тем же run_id"
        ) from exc


def _blocking_merged_unverified(ws_id: str) -> str | None:
    """WS-lock (спека §5, финальное ревью круг 5): найти блокирующий run_id.

    Пока по ``ws_id`` висит ``merged_unverified`` без зелёного (``completed``)
    потомка (``remediated_by`` == его run_id), новый авторинг-прогон по этому
    же ``ws_id`` не стартует — fail-closed: непроверенное не читается как
    проверенное. Возвращает run_id блокирующего прогона либо ``None``.

    Обход соседей по `all_run_ids()`: битые/нечитаемые `run.json` (не тот
    формат, отсутствующие поля, невалидный JSON) пропускаются молча — сосед
    с испорченным леджером не должен мешать чужому прогону.
    """
    states: dict[str, RunState] = {}
    for run_id in all_run_ids():
        try:
            states[run_id] = load(run_id)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    verified_parents = {
        s.remediated_by
        for s in states.values()
        if s.remediated_by and s.status == "completed"
    }
    for run_id, s in states.items():
        if s.ws_id == ws_id and s.status == "merged_unverified":
            if run_id not in verified_parents:
                return run_id
    return None


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
    """S0: новый прогон, затем сразу `advance()` до стопа/завершения.

    WS-lock проверяется ДО резервирования `run_id` (круг 7): у неё нет
    побочных эффектов, а `_reserve_run_id` создаёт файл — так отказ по
    WS-lock не оставляет пустой `run.json`-заглушку под несостоявшимся
    `run_id`.
    """
    blocker = _blocking_merged_unverified(ws_id)
    if blocker is not None:
        raise ValueError(
            f"WS-id {ws_id!r} заблокирован merged_unverified-прогоном "
            f"{blocker!r} без зелёного потомка — создайте verification-run "
            "(verify(...)) прежде чем начинать новый авторинг-прогон"
        )
    _reserve_run_id(run_id)
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
        _step_commit,
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


# stopped_author/stopped_gate/stopped_review: между стопом и resume человек
# правит файлы бандла в worktree (устраняет gate-findings, отрабатывает
# review-находки) — сброс ТОЛЬКО op'а, отвечавшего за сам стоп, оставлял бы
# `commit` completed со СТАРЫМ, докоррекционным деревом, и `push` уносил бы
# уже неактуальный (возможно красный) коммит дальше по конвейеру (круг 9,
# codex-ревью PR #88). Сбрасывается весь диапазон commit→review; `pr` НЕ
# входит — существующий PR переиспользуется (push обновит его ветку,
# `_step_pr`-реконсиляция не создаёт второй, F-5); `commit_paths` на чистом
# дереве не падает (`git diff --cached --quiet`), так что повторный коммит
# без реальных правок — no-op, не ошибка.
_BUNDLE_EDIT_RESET_OPS: tuple[str, ...] = (
    "commit", "gate-candidate", "push", "ready", "review",
)

# Статус stopped_* -> op'ы, которые reconciliation обязан сбросить в pending
# перед повторным advance() (финальное ревью F-1). stopped_author не входит:
# у него дополнительно нужно найти НЕЗАВЕРШЁННЫЙ author-* узел (см.
# `_reset_stopped_author`).
_STOPPED_RESET_OPS: dict[str, tuple[str, ...]] = {
    "stopped_gate": _BUNDLE_EDIT_RESET_OPS,
    "stopped_review": _BUNDLE_EDIT_RESET_OPS,
    "stopped_merge_refused": ("verdict",),
    # stopped_dirty: `branch` ещё не стартовала (проверка идёт до
    # `_ensure_started`), сбрасывать нечего — только статус обратно в
    # running, чтобы `_step_branch` перепроверил `is_dirty` (круг 5).
    "stopped_dirty": (),
}


def _reset_stopped_author(state: RunState) -> None:
    """stopped_author: незавершённые ``author-*`` + диапазон commit→review
    (круг 9) — та же логика, что `_BUNDLE_EDIT_RESET_OPS`, на случай, если
    контент бандла успел измениться после починки."""
    for key, _kind, _filename in _AUTHOR_STEPS:
        if op_status(state, key) != "completed":
            state.ops.pop(key, None)
    for key in _BUNDLE_EDIT_RESET_OPS:
        state.ops.pop(key, None)


def resume(run_id: str, ops: Ops) -> RunState:
    """Явный подхват сохранённого run'а (спека §5).

    ``merged_unverified`` — отказ (навсегда, см. `advance`). Из
    ``waiting_human_merge`` — reconciliation по факту мержа: PR ``MERGED`` →
    фиксирует op ``merge`` (если ещё не зафиксирован) и выполняет только S8;
    PR всё ещё ``OPEN`` — состояние не меняется, run продолжает ждать
    человека.

    Из любого ``stopped_*`` — reconciliation вместо слепого no-op (финальное
    ревью F-1/M-1): op(ы), на которых прогон встал, сбрасываются в pending, и
    только после этого зовётся `advance()`. ``stopped_gate`` (S4 красный) и
    ``stopped_review`` — весь диапазон ``commit``→``review`` (``commit``,
    ``gate-candidate``, ``push``, ``ready``, ``review``, но не ``pr`` —
    круг 9: человек мог поправить бандл в worktree между стопом и resume,
    и старый `commit`/`push` унесли бы докоррекционное дерево дальше по
    конвейеру); ``stopped_author`` — незавершённые ``author-*`` плюс тот же
    диапазон; ``stopped_merge_refused`` (S7 `refuse`, отдельный от
    ``stopped_gate`` статус — M-1) — ``verdict``, хотя фактическая
    пересверка вердикта теперь происходит на каждом заходе в
    S7 независимо от этого сброса (см. `_step_verdict`, F-2);
    ``stopped_dirty`` (S1 fail-closed dirty-гард, круг 5) — сбрасывать
    нечего (``branch`` не стартовала), только статус обратно в ``running``.
    Любой другой статус — обычный ``advance()``.
    """
    state = load(run_id)
    if state.status == "merged_unverified":
        raise ValueError(
            f"run {run_id!r} — merged_unverified навсегда; создайте "
            "verification-run через verify(...)"
        )
    if state.status == "waiting_human_merge":
        pr_facts_now = ops.pr_facts(state.repo_slug, state.pr)
        if pr_facts_now.get("state") != "MERGED":
            return state
        if op_status(state, "merge") != "completed":
            op_complete(state, "merge", merged=True)
        state.status = "running"
        save(state)
        _step_s8(state, ops)
        return state
    if state.status == "stopped_author":
        _reset_stopped_author(state)
        state.status = "running"
        save(state)
        return advance(state, ops)
    if state.status in _STOPPED_RESET_OPS:
        for key in _STOPPED_RESET_OPS[state.status]:
            state.ops.pop(key, None)
        state.status = "running"
        save(state)
        return advance(state, ops)
    return advance(state, ops)


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
    _reserve_run_id(run_id)
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
    child.base_ref = parent.base_ref
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
    """S1: ветка `spec/<ws_id>-behaviour`; `ensure_branch` идемпотентен.

    Fail-closed dirty-гард (финальное ревью, круг 5): `target_dir` грязный
    (`git status --porcelain` непуст) ДО начала прогона — дальше по
    конвейеру `commit_paths` закоммитил бы рядом с чужими незакоммиченными
    правками. Проверяется, только пока `branch` ещё не заведена: ветка уже
    создана значит проверка на ЭТОМ прогоне уже пройдена, а грязь внутри
    неё — уже наши же авторенные файлы (S2/S3), не чужие. Resume после
    ручной очистки — обычный `advance()`: `branch` так и не стартовала,
    проверка просто повторяется.
    """
    key = "branch"
    state.branch = f"spec/{state.ws_id}-behaviour"
    if op_status(state, key) == "completed":
        return True
    if op_status(state, key) == "new" and ops.is_dirty(state.target_dir):
        print(
            f"_step_branch: target_dir {state.target_dir!r} грязный "
            "(git status --porcelain непуст) — прогон не начат"
        )
        state.status = "stopped_dirty"
        save(state)
        return False
    _ensure_started(state, key)
    ops.ensure_branch(state.target_dir, state.branch)
    op_complete(state, key)
    return True


def _step_authoring(state: RunState, ops: Ops) -> bool:
    """S2/S3: charter/requirements/behaviour-spec — файл есть → пропустить.

    B1-рулинг (финальное ревью F-6): все три узла авторятся общим
    `ops.author` (`codex exec --ephemeral`), а не циклом `disp --mode
    document` до сходимости, как буквально описывает спека §5 S3/§2. Замена
    осознанная для этапа B1; `disp`-цикл и критерий сходимости — предмет B2
    (OQ-1, `docs/superpowers/specs/2026-08-30-behaviour-spec-pipeline-design.md`).
    """
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


def _step_commit(state: RunState, ops: Ops) -> bool:
    """S3 (продолжение): коммит авторенного контента до push (F-6).

    Между авторингом и push не было коммита: `ops.author` (`codex exec`)
    не гарантированно коммитит сам, поэтому push уходил пустым и
    `create_draft_pr` падал неперехваченным `CalledProcessError`. Отдельный
    op `commit`, идемпотентный (пустой индекс — не ошибка, см.
    `RealOps.commit_paths`).

    Коммитится ТОЛЬКО `bundle_dir` (круг 5, codex-ревью PR #88): `git add
    -A` сгребал бы в этот коммит и чужие незакоммиченные изменения где
    угодно в `target_dir` — заменено на явный список путей.
    """
    key = "commit"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    message = (
        f"docs(governance): behaviour bundle {state.ws_id} — {state.subject}\n\n"
        "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
    )
    ops.commit_paths(state.target_dir, [state.bundle_dir], message)
    op_complete(state, key)
    return True


def _step_gate(state: RunState, ops: Ops) -> bool:
    """S4: prospective-гейт; error_count/required_absent/blocked-узлы → stop.

    ``error_count == 0`` сам по себе не значит «бандл зелёный»
    (`bundle_state.py` докстринг, финальное ревью F-4): бандл без
    frontmatter-узлов проходил бы насквозь — обязательные узлы отсутствуют
    (``required_absent``), но это не порождало ни одной находки. Блокируем
    явно на непустом ``required_absent`` и на любом узле в статусе
    ``blocked`` (upstream физически не мог быть прогейтчен), не только на
    ``error_count``.
    """
    key = "gate-candidate"
    if op_status(state, key) == "completed":
        return True
    _ensure_started(state, key)
    bundle = candidate_state(
        Path(state.target_dir) / state.profile,
        Path(state.target_dir) / state.bundle_dir,
    )
    blocked_nodes = [n.node_id for n in bundle.nodes if n.status == "blocked"]
    gate_red = bool(bundle.error_count) or bool(bundle.required_absent) or bool(
        blocked_nodes
    )
    if gate_red:
        findings = [f for node in bundle.nodes for f in node.findings]
        findings.extend(bundle.bundle_findings)
        if bundle.required_absent:
            findings.append(
                "error GC-REQUIRED-ABSENT(prospective): обязательные узлы "
                f"отсутствуют в бандле: {', '.join(bundle.required_absent)}"
            )
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
    """S5b: PR черновиком; started → find_pr первым — второй PR не открывать.

    ``find_pr`` поднимает ``RuntimeError`` на транзиентном сбое `gh` (не
    отличимом от «PR нет» иначе — финальное ревью F-5): реконсиляция обязана
    остановиться, а не читать сбой опроса как «PR не создан» и открывать
    второй PR на ту же ветку. Op остаётся ``started``, статус run'а не
    меняется — следующий `advance()`/`resume()` попробует снова.
    """
    key = "pr"
    status = op_status(state, key)
    if status == "completed":
        return True
    if status == "started":
        try:
            existing = ops.find_pr(state.repo_slug, state.branch)
        except RuntimeError as exc:
            print(f"_step_pr: реконсиляция find_pr не удалась: {exc}")
            return False
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
    # exit_code == 4 (или иной неопознанный) — голова PR уехала: в ветку
    # пришло новое содержимое, поэтому контентный гейт S4 (отработавший по
    # СТАРОЙ голове) обязан переиграться, не только весь S6 (финальное
    # ревью F-7) — иначе прошедший когда-то gate-candidate молча продолжает
    # покрывать содержимое, которого он не видел. push сбрасывается вместе с
    # gate-candidate: новый коммит после правок ещё не запушен.
    state.ops.pop("gate-candidate", None)
    state.ops.pop("push", None)
    state.ops.pop("ready", None)
    state.ops.pop(key, None)
    save(state)
    return False


def _step_verdict(state: RunState, ops: Ops) -> bool:
    """S7: merge_gate → agent продолжает мержем, human/refuse — стоп.

    ``verdict`` — только аудит-запись, НЕ кэш решения (финальное ревью F-2):
    пока op ``merge`` не ``completed``, факты PR пересобираются и `decide()`
    вызывается заново на КАЖДОМ заходе в этот шаг. Достижимо штатным
    write-ahead-сценарием: `_step_verdict` зафиксировал ``agent``,
    `_step_merge` начал op ``merge`` (``started``) и процесс умер раньше
    самого мержа — статус run'а остаётся ``running``, и без пересверки
    следующий `advance()` домержил бы по кэшированному вердикту, хотя за
    время простоя на том же sha мог покраснеть rollup, открыться review
    thread или PR — отстать от base (спека §8: «resume без reconciliation
    запрещён» — единственный неотменяемый шаг). Мерж стартует только на
    свежем ``agent``.
    """
    if op_status(state, "merge") == "completed":
        return True

    _ensure_started(state, "verdict")
    authority = build_authority(Path(state.target_dir), state.merge_authority)
    safety = load_safety()
    review_exit = state.ops.get("review", {}).get("exit")
    pr_facts_raw = ops.pr_facts(state.repo_slug, state.pr)
    # S8 гейтит default-ветку целевого репо, не feature-ветку прогона — ей
    # нужно имя (круг 5); фолбэк "master" на пустой/отсутствующий baseRefName.
    state.base_ref = pr_facts_raw.get("baseRefName") or "master"
    files = ops.pr_files(state.repo_slug, state.pr)
    threads = ops.unresolved_threads(state.repo_slug, state.pr)
    facts = facts_from(pr_facts_raw, files, threads, state.bundle_dir)
    verdict = decide(authority, safety, review_exit, facts)
    decision, reason = verdict.decision, verdict.reason
    op_complete(state, "verdict", decision=decision, reason=reason)

    if decision == "agent":
        return _step_merge(state, ops)

    ops.comment(state.repo_slug, state.pr, f"merge_gate: {decision} — {reason}")
    # refuse получает свой статус, отдельный от stopped_gate (S4): причины и
    # починки разные, и resume() должен различать их (финальное ревью M-1).
    state.status = (
        "stopped_merge_refused" if decision == "refuse" else "waiting_human_merge"
    )
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


def _s8_findings_text(exit_code: int | None, output: str) -> str:
    """Текст `s8-findings.txt`, собранный из журнала `gate-authoritative`
    (`exit`/`output`) — файл производный, журнал источник истины (круг 8)."""
    return f"gate-check (S8, authoritative) завершился с кодом {exit_code}\n\n{output}"


def _step_s8(state: RunState, ops: Ops) -> bool:
    """S8: authoritative-гейт на дефолтной ветке после мержа (спека §5).

    exit 0 → `completed`, run завершается. Не-0 → `merged_unverified`
    НАВСЕГДА: findings в `run_dir/s8-findings.txt`, remediation-issue в
    целевом репо по inbox-контракту ADR-ECO-006 (тело начинается
    `slug:`/`from:`).

    `gate-authoritative` завершается (`op_complete`, аудит-запись с `exit`)
    сразу после `gate_check_s8` — успех И провал одинаково (круг 3,
    codex-ревью PR #88): run на любом исходе S8 становится терминальным
    (либо `completed`, либо `merged_unverified` навсегда — `advance()`/
    `resume()` отказывают на `merged_unverified` явно), так что
    `completed` здесь не путается с «прошёл» — прошёл или нет, отличает
    сохранённый `exit`, не сам статус op'а.

    Создание remediation-issue — отдельный write-ahead op
    `remediation-issue`: `create_issue` раньше не имел собственного op'а,
    и гибель между вызовом `create_issue` (эффект состоялся) и фиксацией
    результата на resume приводила к дубль-issue (`op_start` пишется ДО
    эффекта; `started` при входе → реконсиляция через `ops.find_issue` по
    `slug:`-префиксу вместо слепого повторного `create_issue`).

    Перед самим `gate_check_s8` — `sync-default` (круг 5, codex-ревью
    PR #88): `target_dir` без явного чекаута мог стоять на feature-ветке
    прогона, и authoritative-срез гейтил бы не default-ветку. Выполняется
    БЕЗУСЛОВНО на каждом заходе в этот шаг, пока `gate-authoritative` не
    `completed` — не только когда сам `sync-default` ещё не `completed`
    (круг 6, codex-ревью PR #88): между попытками мог пройти произвольный
    отрезок времени, за который default-ветка целевого репо могла уехать
    дальше, а локальный чекаут — устареть; `checkout_and_pull` дёшев и
    идемпотентен, так что журнал `sync-default` держится только для аудита,
    не как ветка пропуска.

    `s8-findings.txt` — ПРОИЗВОДНЫЙ от журнала, не источник истины (круг 8,
    codex-ревью PR #88): op `gate-authoritative` несёт `output` в результате
    (`op_complete(..., exit=N, output=...)`), и findings-текст всегда
    пересобирается из этих полей и перезаписывается на диск — и на свежем
    провале, и на resume. Раньше файл писался ОДИН раз, после
    `op_complete`; гибель между этими двумя шагами оставляла op `completed`
    на диске без файла, и resume падал на `read_text()`
    (`FileNotFoundError`) вместо того, чтобы довести fail-путь до конца.
    """
    key = "gate-authoritative"
    findings_path = run_dir(state.run_id) / "s8-findings.txt"
    gate_op = state.ops.get(key)

    if gate_op is not None and gate_op["status"] == "completed":
        if gate_op.get("exit") == 0:
            # Резюме между write-ahead `op_complete(gate-authoritative)` и
            # финальным `state.status = "completed"` (круг 6, codex-ревью
            # PR #88): op на диске уже `completed`, но статус ещё "running" —
            # довести до конца, не оставлять прогон вечно "running".
            if state.status != "completed":
                state.status = "completed"
                save(state)
            return True
        findings = _s8_findings_text(gate_op.get("exit"), gate_op.get("output", ""))
        findings_path.write_text(findings, encoding="utf-8")
    else:
        sync_key = "sync-default"
        _ensure_started(state, sync_key)
        base_ref = state.base_ref or "master"
        try:
            ops.checkout_and_pull(state.target_dir, base_ref)
        except RuntimeError as exc:
            print(f"_step_s8: checkout_and_pull({base_ref!r}) не удался: {exc}")
            return False
        op_complete(state, sync_key)
        _ensure_started(state, key)
        exit_code, output = ops.gate_check_s8(
            state.target_dir, state.bundle_dir, state.profile
        )
        if exit_code == 0:
            op_complete(state, key, exit=exit_code)
            state.status = "completed"
            save(state)
            return True
        op_complete(state, key, exit=exit_code, output=output)
        findings = _s8_findings_text(exit_code, output)
        findings_path.write_text(findings, encoding="utf-8")

    issue_title = f"beh-remediation: {state.subject} ({state.ws_id})"
    body_prefix = f"slug: beh-remediation-{state.ws_id}"
    issue_body = f"{body_prefix}\nfrom: devtools#{state.run_id}\n\n{findings}"

    issue_key = "remediation-issue"
    issue_status = op_status(state, issue_key)
    if issue_status != "completed":
        if issue_status == "started":
            try:
                existing = ops.find_issue(state.repo_slug, body_prefix)
            except RuntimeError as exc:
                print(f"_step_s8: реконсиляция find_issue не удалась: {exc}")
                return False
            if existing is not None:
                op_complete(state, issue_key, number=existing)
            else:
                number = ops.create_issue(state.repo_slug, issue_title, issue_body)
                op_complete(state, issue_key, number=number)
        else:
            op_start(state, issue_key)
            number = ops.create_issue(state.repo_slug, issue_title, issue_body)
            op_complete(state, issue_key, number=number)

    # Безусловный фолл-through сюда с обеих веток выше (свежий провал и
    # резюме на уже-completed(exit!=0) gate-authoritative) — второе окно
    # круга 6: гибель между `op_complete(gate-authoritative)` и этой строкой
    # оставляла бы run вечно "running", если бы тут был досрочный return;
    # его нет, так что fail-путь всегда доводится до конца независимо от
    # того, каким статус пришёл на вход функции.
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
