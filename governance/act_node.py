"""Путь узла `review/v1`: общий вход, создание, finalize в конверт, остановка, аудит.

`approve_node` только ветвится сюда (§2: режим — из манифеста в одобренном
коммите; §10: admission до выбора читателя). Решение по доставке —
`act_context.delivery_verdict` поверх доверенного контекста; старый код
получает управление после `LegacyAccepted` (или когда маркера нет и PR не
смёржен). Неустановленный факт заявку не хоронит (`_unresolved`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from governance import (
    act_context,
    act_facts,
    act_status,
    act_trust,
    bundle_dag,
    bundle_inputs,
)
from governance import approval_facts as af
from governance import approval_ledger as al
from governance import approve_node as an
from governance.act_check import LegacyAccepted, live_policy_refusal
from governance.act_decision import Accepted, DeliveryView, Rejected, find_act
from governance.act_envelope import (
    DeliveredRequest,
    DeliveredStop,
    History,
    StopRecord,
    envelope_path,
    expected_envelope,
    next_generation,
    node_history,
    render_envelope,
    render_stop,
    stop_path,
)
from governance.act_identity import RequestIdentity, manifest_refusal, paths_refusal
from governance.act_projection import ProjectionError, self_hash_v
from governance.act_request import (
    ACT_REVIEW_V1,
    NodeEntry,
    NodeRequest,
    PolicyPin,
    RequestError,
    manifest_path,
    mirror_mismatch,
    parse_manifest,
    render_manifest,
)
from governance.act_settings import ActSettings
from governance.act_time import AMBIGUOUS, Ambiguous, Clock, Unavailable
from governance.approval_facts import Disposition
from governance.facts import Outcome
from governance.ops import Ops
from governance.run_state import RunState

HUMAN_APPROVAL_LABEL = "human-approval-required"
WAITING_HUMAN_APPROVAL = "waiting_human_approval"
WAITING_DELIVERY = "waiting_delivery"
DEPS_FACTORY: Callable[[RunState], act_status.Deps] = act_status.real_deps
Dag = tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Creation:
    """Режим новой заявки: `act=None` — без маркера; `review/v1` — с пином и историей."""

    act: str | None
    pin: PolicyPin | None
    history: History


# --- создание ------------------------------------------------------------------------


_DRAINING = (
    "act_migration = draining: заявки без маркера не создаются (§10 п. 1) — "
    "дождитесь активации ACT_MODE=review для продукта"
)


def creation(
    state: RunState,
    ops: Ops,
    deps: act_status.Deps,
    live: act_status.LivePolicy | Unavailable,
) -> Creation:
    """Режим создания по режиму продукта сейчас (`mode_probe`) и настройке (§10, Task 10).

    Неустановленный режим (нет привязки, удалённая запись, откат) — отказ
    создания, не заявка без маркера (ревизия b1, B1-5); нечитаемые настройки
    установки — такой же отказ."""
    if isinstance(live, Unavailable):
        raise an._unresolved(f"режим акта продукта: {live.reason}")
    settings = deps.settings
    if not isinstance(settings, ActSettings):
        reason = settings.reason if isinstance(settings, Unavailable) else "не заданы"
        raise an._unresolved(f"настройки акта: {reason}")
    if live.mode != "review":
        if settings.draining:
            raise RuntimeError(_DRAINING)
        return Creation(None, None, ())
    if not settings.review_v1:
        raise RuntimeError(
            f"ACT_MODE[{state.repo_slug}] = review, а review_v1 в act.toml выключен — "
            "заявка не создаётся (§10): включите review_v1 либо дождитесь дефолта M4"
        )
    history = act_status.load_history(ops, state, an._base_ref(state))
    if isinstance(history, Unavailable):
        raise an._unresolved(f"история заявок узлов: {history.reason}")
    pin = PolicyPin(
        live.trust.source.repo,
        live.trust.source.branch,
        live.activation.merge_commit,
        dict(live.policy.files_sha256),
    )
    return Creation(ACT_REVIEW_V1, pin, history)


def generation_of(made: Creation, node: str) -> int:
    """Поколение узла в новой заявке — по полной истории (§9.1 п. 0a)."""
    return next_generation(made.history, node)


def request_of(state: RunState, dag: Dag, op: Mapping, key: str) -> NodeRequest:
    """Заявка `review/v1` из записи леджера (обратное `ledger_view`)."""
    pin = op["policy"]
    nodes = {
        n: NodeEntry(
            an._rel(state, an._filename(dag, n)),
            op["content_hashes"][n],
            op["generations"][n],
        )
        for n in op["nodes"]
    }
    return NodeRequest(
        key,
        state.ws_id,
        state.repo_slug,
        nodes,
        {n: dict(op["upstream_pins"][n]) for n in op["nodes"]},
        PolicyPin(
            pin["repo"],
            pin["branch"],
            pin["activation_commit"],
            dict(pin["files_sha256"]),
        ),
    )


def manifest_file(
    state: RunState, dag: Dag, op: Mapping, key: str
) -> tuple[str, str] | None:
    """Путь и текст манифеста для коммита candidate; `None` — заявка без маркера."""
    if op.get("act") != ACT_REVIEW_V1:
        return None
    return manifest_path(state.ws_id, key), render_manifest(
        request_of(state, dag, op, key)
    )


def label_for(op: Mapping) -> str:
    """Метка candidate-PR: акт = approve (`review/v1`) либо мерж человеком."""
    return (
        HUMAN_APPROVAL_LABEL if op.get("act") == ACT_REVIEW_V1 else an.HUMAN_MERGE_LABEL
    )


# --- общий вход ----------------------------------------------------------------------


def _invalidate(state: RunState, key: str, reason: str) -> NoReturn:
    al.invalidate_request(state, key, reason)
    raise RuntimeError(
        f"{reason}. Заявка {key} — invalidated; продолжение — новая заявка "
        f"(`--repropose {key}`)"
    )


def _manifest(state: RunState, ops: Ops, head: str, key: str) -> NodeRequest | None:
    fact = ops.repo_file_fact(state.repo_slug, head, manifest_path(state.ws_id, key))
    if fact.outcome is Outcome.ABSENT:
        return None
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        raise an._unresolved(f"манифест заявки {key} в {head[:12]} — {fact.detail}")
    try:
        return parse_manifest(fact.value)
    except RequestError as exc:
        _invalidate(state, key, f"манифест в одобренном коммите не читается: {exc}")


def _binding_refusal(
    state: RunState,
    ops: Ops,
    dag: Dag,
    op: Mapping,
    req: NodeRequest,
    head: str,
    key: str,
) -> str | None:
    """Привязка к прогону (B1-1): идентичность, пути DAG, upstream-пины головы."""
    ident = RequestIdentity(state.repo_slug, state.ws_id, key)
    expected = {bundle_dag.node_id(f): an._rel(state, f) for f, _ in dag}
    refusal = manifest_refusal(req, ident) or paths_refusal(req, expected)
    if refusal is not None:
        return refusal
    ops.fetch_branch(state.target_dir, op["branch"])
    for node in req.nodes:
        fact = bundle_inputs.direct_blobs(state, ops, dag, node, head)
        if fact.outcome is Outcome.FORBIDDEN:
            return f"прямые входы {node}: {fact.detail}"
        if fact.outcome is not Outcome.FOUND or fact.value is None:
            raise an._unresolved(f"прямые входы {node} в {head[:12]} — {fact.detail}")
        if dict(req.upstream_pins[node]) != fact.value:
            return f"манифест: upstream-пины {node} ≠ прямым входам в {head[:12]}"
    return None


def _check_marked(
    state: RunState, ops: Ops, dag: Dag, op: Mapping, key: str, pr: int, head: str
) -> NodeRequest | None:
    """Манифест головы и его зеркала (`run.json`, тело PR), привязка к прогону."""
    req = _manifest(state, ops, head, key)
    if (op.get("act") == ACT_REVIEW_V1) != (req is not None):
        _invalidate(
            state, key, "зеркало act в run.json расходится с манифестом в голове PR"
        )
    if req is None:
        return None
    body = ops.pr_body_fact(state.repo_slug, pr)
    if body.outcome is not Outcome.FOUND or body.value is None:
        raise an._unresolved(f"тело candidate-PR #{pr} — {body.detail}")
    refusal = mirror_mismatch(req, key, op, body.value)
    if refusal is None:
        refusal = _binding_refusal(state, ops, dag, op, req, head, key)
    if refusal is not None:
        _invalidate(state, key, refusal)
    return req


def route(
    state: RunState, ops: Ops, dag: Dag, op: dict, key: str, step: al.Step
) -> an.ApprovalOutcome | None:
    """Общий вход до выбора шага; `None` — старый код продолжает.

    `None` только: нет candidate-PR; PR открыт/закрыт без манифеста; смёржен
    без манифеста в продукте `PRE_MIGRATION`; `LegacyAccepted`. Для шагов
    после мержа решение переустанавливается из фактов при каждом входе, а не
    берётся из `op` (`step` в решение не входит)."""
    pr = op.get("candidate_pr")
    if pr is None:
        return None
    facts = an._pr_facts(state, ops, pr)
    head = facts.get("headRefOid")
    if not isinstance(head, str) or len(head) != 40:
        raise an._unresolved(f"голова candidate-PR #{pr} не названа")
    where = an._disposition(facts, pr)
    req = _check_marked(state, ops, dag, op, key, pr, head)
    if where is Disposition.OPEN:
        return None if req is None else _open(state, ops, key, pr, head, req)
    if where is Disposition.CLOSED_UNMERGED:
        return None
    deps = DEPS_FACTORY(state)
    if req is None:
        live = act_status.mode_probe(state, deps)
        if isinstance(live, Unavailable):
            raise an._unresolved(f"режим акта продукта: {live.reason}")
        if live.mode == act_trust.PRE_MIGRATION:
            # записи ACT_MODE[P] не было никогда: admission на T — legacy (§10),
            # тот же исход, что дал бы полный перебор; старый код продолжает
            return None
    return _merged(state, ops, dag, op, key, pr, head, req, facts, deps)


def _open(
    state: RunState, ops: Ops, key: str, pr: int, head: str, req: NodeRequest
) -> an.ApprovalOutcome:
    """Открытый candidate `review/v1`: живое правило, D1 на голове, доставка агентом."""
    deps = DEPS_FACTORY(state)
    live = act_status.mode_probe(state, deps)
    if isinstance(live, Unavailable):
        raise an._unresolved(f"режим акта продукта: {live.reason}")
    if live.mode != "review":
        raise an._unresolved(f"заявка review/v1, а режим продукта сейчас — {live.mode}")
    refusal = live_policy_refusal(req.policy, live.activation.merge_commit)
    if refusal is not None:
        _invalidate(state, key, refusal)
    facts = act_facts.open_review(deps.gh, state.repo_slug, pr)
    if isinstance(facts, Unavailable):
        raise an._unresolved(f"ревью candidate-PR #{pr}: {facts.reason}")
    if facts.head_sha != head:
        raise an._unresolved(f"голова PR #{pr} сдвинулась между чтениями")
    effective = act_facts.effective(facts, Clock(deps.now()))
    if isinstance(effective, Unavailable):
        raise an._unresolved(f"эффективные ревью PR #{pr}: {effective.reason}")
    act = find_act(head, effective, live.policy)
    if act is None:
        return an.ApprovalOutcome(
            f"{WAITING_HUMAN_APPROVAL}: candidate-PR #{pr} ждёт approve человека из "
            f"human_approvers по head {head[:12]}. Заявка {key} жива",
            request=key,
        )
    code = ops.merge(state.repo, pr, head, None)
    if code != 0:
        hint = an._MERGE_CODE_HINTS.get(code, "мерж не выполнен")
        return an.ApprovalOutcome(
            f"approve {act.login} есть, агентская доставка отказала кодом {code}: "
            f"{hint}. Заявка {key} жива",
            request=key,
        )
    return an.ApprovalOutcome(
        f"{WAITING_DELIVERY}: candidate-PR #{pr} доставлен агентом после approve "
        f"{act.login}; повторите вызов — решение по доставке и конверт",
        request=key,
    )


def _legacy(
    state: RunState, ops: Ops, dag: Dag, op: dict, key: str, facts: dict
) -> Callable[[], Any]:
    """Вердикт старого кода — лениво: зовётся только после `OLD_READER` (I-1)."""

    def verdict() -> Any:
        got = an._legacy_outcome(state, ops, dag, op, key, facts)
        return Unavailable(got.text) if isinstance(got, an.LegacyHalt) else got

    return verdict


def _head_hashes(
    state: RunState, ops: Ops, req: NodeRequest, head: str
) -> dict[str, str] | Unavailable:
    hashes: dict[str, str] = {}
    for entry in req.nodes.values():
        text = ops.show_file(state.target_dir, head, entry.path)
        if text is None:
            return Unavailable(f"{entry.path} в {head[:12]} не читается")
        try:
            hashes[entry.path] = self_hash_v(text, ACT_REVIEW_V1)
        except ProjectionError as exc:
            return Unavailable(str(exc))
    return hashes


def _merged(
    state: RunState,
    ops: Ops,
    dag: Dag,
    op: dict,
    key: str,
    pr: int,
    head: str,
    req: NodeRequest | None,
    facts: dict,
    deps: act_status.Deps,
) -> an.ApprovalOutcome | None:
    """Смёржен: admission и решение по доставке (Task 12a), затем finalize."""
    context = act_status.Decider(state, ops, deps, dag).context()
    if isinstance(context, Unavailable):
        raise an._unresolved(f"доверенный контекст: {context.reason}")
    trust, journal = context
    outcome, delivery = act_context.delivery_verdict(
        deps.readers,
        trust,
        journal,
        product=state.repo_slug,
        pr=pr,
        paths=sorted(e.path for e in req.nodes.values()) if req else [],
        request=req,
        request_act=ACT_REVIEW_V1 if req else None,
        head_sha=head,
        facts_transport=deps.gh,
        self_hashes=lambda: _head_hashes(state, ops, req, head) if req else {},
        legacy=_legacy(state, ops, dag, op, key, facts),
    )
    if isinstance(outcome, LegacyAccepted):
        return None
    if isinstance(outcome, Rejected):
        _invalidate(state, key, f"{outcome.code}: {outcome.detail}")
    if isinstance(outcome, Ambiguous):
        raise an._unresolved(
            f"{AMBIGUOUS}: исход зависит от порядка событий секунды мержа — факты не "
            f"изменятся; штатный выход — `--repropose {key}`"
        )
    if isinstance(outcome, Unavailable):
        tail = (
            " — факты PR не изменятся; выход — `--repropose`"
            if outcome.reason.startswith("actor_unresolved")
            else ""
        )
        raise an._unresolved(f"{outcome.reason}{tail}")
    if not isinstance(outcome, Accepted) or req is None or delivery is None:
        raise an._unresolved(f"доставка candidate-PR #{pr} не установлена")
    _record_act(state, key, req, outcome, delivery, trust)
    current = DeliveredRequest(outcome.merge_commit, head, req)
    expected = {
        n: render_envelope(expected_envelope(current, n, outcome.review_id))
        for n in req.nodes
    }
    return _finalize(state, ops, dag, key, expected)


def _record_act(
    state: RunState,
    key: str,
    req: NodeRequest,
    accepted: Accepted,
    delivery: DeliveryView,
    trust: act_trust.Trust,
) -> None:
    """Акт — один раз; записанный другой акт — стоп, запись не переписывается."""
    op = state.ops[key]
    if op.get("review_id") is None:
        al.record_review_act(
            state,
            key,
            accepted,
            delivery,
            req.policy.activation_commit,
            trust.unverified,
            trust.root,
        )
        return
    if (op["review_id"], op["merge_commit"]) != (
        accepted.review_id,
        accepted.merge_commit,
    ):
        raise an._unresolved(
            f"заявка {key}: записан акт {op['review_id']}/{op['merge_commit']}, решение "
            f"сейчас — {accepted.review_id}/{accepted.merge_commit}"
        )


# --- finalize ------------------------------------------------------------------------


def envelope_commit_defect(
    state: RunState, ops: Ops, expected: Mapping[str, str], head: str
) -> str | None:
    """Коммит конверта — ровно пути конвертов узлов заявки с ожидаемым содержимым."""
    touched = ops.commit_files(state.target_dir, head)
    if touched is None:
        return f"коммит конверта {head[:12]} не читается"
    want = {envelope_path(state.ws_id, n): text for n, text in expected.items()}
    if set(touched) != set(want):
        return (
            f"коммит конверта трогает {sorted(touched)}, ожидались ровно {sorted(want)}"
        )
    for path, text in sorted(want.items()):
        if ops.show_file(state.target_dir, head, path) != text:
            return f"{path}: содержимое не равно ожидаемому конверту"
    return None


def _finalize(
    state: RunState, ops: Ops, dag: Dag, key: str, expected: Mapping[str, str]
) -> an.ApprovalOutcome:
    """Конверты `review/v1` — коммит, PR, мерж (ADR-ECO-011 D5); повтор — по записи."""
    op = state.ops[key]
    if op.get("finalize_pr") is not None:
        return _reconcile_finalize(state, ops, dag, key, expected)
    branch, head = op["finalize_branch"], op.get("finalize_head_sha")
    paths = sorted(envelope_path(state.ws_id, n) for n in expected)
    nodes = ", ".join(expected)
    if head is None:
        ops.switch_to(state.target_dir, branch, an._base_ref(state))
        for node, text in expected.items():
            path = Path(state.target_dir) / envelope_path(state.ws_id, node)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        ops.commit_paths(
            state.target_dir,
            paths,
            f"spec: {state.ws_id} — конверт review/v1 узлов {nodes}",
        )
        head = ops.rev_parse(state.target_dir, "HEAD")
        if head is None:
            raise RuntimeError("коммит конверта не состоялся: HEAD не разрешается")
        al.record_finalize_head_sha(state, key, head)
    else:
        an._switch_to_request_branch(state, ops, branch, head)
    ops.push_branch(state.target_dir, branch)
    who, why = an.finalize_merge_policy(state.target_dir)
    pr = an._adopt_or_create_pr(
        state,
        ops,
        branch,
        head,
        f"spec: {state.ws_id} — конверт review/v1 узлов {nodes}",
        f"Конверт утверждения узлов {nodes} (спека §9.1 (а)).\n\n"
        f"Акт — approve {op['approved_by']} по head candidate-PR "
        f"#{op['candidate_pr']} (review {op['review_id']}); этот PR полномочий не "
        "создаёт.",
        an.HUMAN_MERGE_LABEL if who == "human" else "",
    )
    al.record_finalize_pr(state, key, pr)
    if who == "human":
        return an.ApprovalOutcome(
            f"конверт вынесен PR #{pr}; мержит человек ({why}). Заявка {key} жива",
            request=key,
        )
    return _agent_merge(state, ops, dag, key, expected, pr, head)


def _agent_merge(
    state: RunState,
    ops: Ops,
    dag: Dag,
    key: str,
    expected: Mapping[str, str],
    pr: int,
    head: str,
) -> an.ApprovalOutcome:
    reason = an._try_agent_merge(
        state,
        ops,
        dag,
        state.ops[key],
        list(expected),
        pr,
        head,
        form=lambda: envelope_commit_defect(state, ops, expected, head),
    )
    if reason is None:
        return _reconcile_finalize(state, ops, dag, key, expected)
    return an.ApprovalOutcome(
        f"конверт вынесен PR #{pr}; {reason.reason}. Заявка {key} жива", request=key
    )


def _reconcile_finalize(
    state: RunState, ops: Ops, dag: Dag, key: str, expected: Mapping[str, str]
) -> an.ApprovalOutcome:
    """После мержа finalize — конверты на его merge-коммите, побайтово, в базе."""
    op = state.ops[key]
    pr = op["finalize_pr"]
    facts = an._pr_facts(state, ops, pr)
    where = an._disposition(facts, pr)
    if where is Disposition.OPEN:
        head = op.get("finalize_head_sha")
        if (
            head is not None
            and an.finalize_merge_policy(state.target_dir)[0] == "agent"
        ):
            return _agent_merge(state, ops, dag, key, expected, pr, str(head))
        return an.ApprovalOutcome(f"PR конверта #{pr} открыт — ждём мержа", request=key)
    if where is Disposition.CLOSED_UNMERGED:
        _invalidate(state, key, f"PR конверта #{pr} закрыт без мержа")
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        raise an._unresolved(f"merge-коммит PR конверта #{pr} — {event.detail}")
    commit = event.value.commit
    _require_in_base(state, ops, key, commit, f"PR конверта #{pr}")
    for node, text in expected.items():
        got = ops.show_file(state.target_dir, commit, envelope_path(state.ws_id, node))
        if got is None:
            raise an._unresolved(f"конверт {node} на {commit[:12]} не читается")
        if got != text:
            _invalidate(
                state, key, f"PR конверта #{pr} доставил не тот конверт узла {node}"
            )
    an._ensure_finalize_identity(state, ops, key, op)
    al.complete_request(state, key)
    # Решение владельца 01.11 (гонка stop/finalize): заявка завершена — конверт в
    # base, — но «одобрены» объявляется только при актуальном допуске; остановка,
    # доставленная между актом и мержем finalize, — фактическое состояние вместо
    # успеха.
    refusal = act_status.noop_refusal(
        state, ops, dag, list(expected), DEPS_FACTORY(state)
    )
    if refusal is not None:
        raise RuntimeError(
            f"конверт review/v1 в base (PR #{pr}), заявка {key} завершена, но узлы не "
            f"одобрены — {refusal}"
        )
    return an.ApprovalOutcome(
        f"конверт review/v1 в base (PR #{pr}): узлы {', '.join(expected)} одобрены, "
        f"заявка {key} завершена",
        request=key,
    )


def _in_base(state: RunState, ops: Ops, commit: str) -> bool | None:
    """merge-коммит достижим из свежей базы (B1-15); `None` — не установлено."""
    ops.checkout_and_pull(state.target_dir, an._base_ref(state))
    return ops.is_ancestor(state.target_dir, commit, an._base_ref(state))


def _require_in_base(
    state: RunState, ops: Ops, key: str, commit: str, what: str
) -> None:
    reached = _in_base(state, ops, commit)
    if reached is None:
        raise an._unresolved(f"{what}: достижимость {commit[:12]} из базы")
    if not reached:
        _invalidate(state, key, f"{what} смёржен не в базу {an._base_ref(state)}")


# --- переиздание (--repropose) -------------------------------------------------------

REPROPOSED = "reproposed: переиздание заявки оператором (--repropose)"
#: Шаги после засчитанного мержа candidate: акт (или авторизация мержа) уже
#: записан, продолжение — `--approve-node`, не переиздание.
_AFTER_MERGE = (al.Step.FINALIZE, al.Step.AWAIT_FINALIZE_MERGE)


def repropose(
    state: RunState, ops: Ops, key: str, *, legacy_bundle: int | None = None
) -> an.ApprovalOutcome:
    """`--repropose <key>`: преемник заявки; терминальная запись не переписывается.

    Терминальный предшественник не трогается; живой (candidate не засчитан)
    — `invalidated: reproposed`. Открытые PR предшественника закрываются
    (иначе `_require_no_reopened_pr` не пустит новый candidate), затем
    создаётся новая заявка с `predecessor: <key>` и текущим пином. Повтор
    находит преемника (`successor_of`) и продолжает его; второго не заводит.
    """
    op = {al.request_key(*nums): rec for nums, rec in al.requests(state)}.get(key)
    if op is None:
        raise RuntimeError(f"заявки {key} нет в леджере")
    status = op.get("status")
    if status == al.STATUS_COMPLETED:
        raise RuntimeError(
            f"заявка {key} завершена — одобрение не переиздаётся; остановить его — "
            "`--reopen <node>`"
        )
    if status != al.STATUS_STARTED and status not in al.TERMINAL_STATUSES:
        raise RuntimeError(f"заявка {key}: статус {status!r} не распознан")
    if al.next_step(op) in _AFTER_MERGE:
        raise RuntimeError(
            f"заявка {key}: мерж candidate уже засчитан — продолжение "
            "`--approve-node`, не переиздание"
        )
    successor = al.successor_of(state, key)
    if successor is not None:
        return _continue_successor(state, ops, key, successor, legacy_bundle)
    if al.is_live(op):
        al.invalidate_request(state, key, REPROPOSED)
    # причина закрытия — из записи предшественника (свежей, после инвалидации)
    an._close_buried_proposals(state, ops, key)
    return _create_successor(state, ops, key, state.ops[key], legacy_bundle)


def _create_successor(
    state: RunState,
    ops: Ops,
    key: str,
    op: Mapping,
    legacy_bundle: int | None = None,
) -> an.ApprovalOutcome:
    """Новая заявка над узлами предшественника; текущий пин берёт `creation`."""
    nodes = list(op["nodes"])
    if op.get("source_sha"):
        return an.propose_from_source(
            state,
            ops,
            nodes,
            op["source_sha"],
            legacy_bundle=legacy_bundle,
            predecessor=key,
        )
    outcome = an.approve_node(
        state, ops, nodes[0], legacy_bundle=legacy_bundle, predecessor=key
    )
    for node in nodes[1:]:
        outcome = an.approve_node(state, ops, node, legacy_bundle=legacy_bundle)
    return outcome


def _continue_successor(
    state: RunState,
    ops: Ops,
    key: str,
    successor: str,
    legacy_bundle: int | None,
) -> an.ApprovalOutcome:
    """Повтор `--repropose`: продвинуть уже созданного преемника."""
    succ = state.ops[successor]
    if not al.is_live(succ):
        raise RuntimeError(
            f"заявка {key} уже переиздана: преемник {successor} — "
            f"{succ.get('status')}; продолжение — от него"
        )
    missing = [n for n in state.ops[key]["nodes"] if n not in succ["nodes"]]
    if missing and succ.get("source_sha"):
        raise RuntimeError(
            f"преемник {successor} заявки {key} без узлов {', '.join(missing)} "
            "(байты ветки волны) — разберите вручную"
        )
    for node in missing:  # падение между узлами при создании преемника
        an.approve_node(state, ops, node, legacy_bundle=legacy_bundle)
    return an.approve_node(state, ops, succ["nodes"][0], legacy_bundle=legacy_bundle)


# --- переоткрытие и аудит ------------------------------------------------------------

STOP_REASON = "переоткрытие узла {node}"


def reopen(state: RunState, ops: Ops, dag: Dag, node: str) -> str | None:
    """Остановка действующего одобрения `review/v1` до новой волны (§8, Q1).

    `None` — у узла нет действующего одобрения `review/v1` (событие и поколение
    — из `node_history`). Остановка засчитывается, только когда stop-PR смёржен,
    его merge-коммит в базе несёт ровно ожидаемую запись, а перечитанная
    история называет действующим событием узла именно её (B1-15, b2).

    Продукт не в `review` — событие `review/v1` одобрения не даёт (статус —
    старый предикат, §10), отнимать нечего: `None`, старый путь. Режим не
    установлен — отказ: есть ли что отнимать, неизвестно."""
    live = act_status.mode_probe(state, DEPS_FACTORY(state))
    if isinstance(live, Unavailable):
        raise an._unresolved(f"режим акта продукта (--reopen {node}): {live.reason}")
    if live.mode != "review":
        return None
    base = an._base_ref(state)
    ops.checkout_and_pull(state.target_dir, base)
    history = act_status.load_history(ops, state, base)
    if isinstance(history, Unavailable):
        raise an._unresolved(f"история заявок узлов: {history.reason}")
    current = node_history(history, node).current
    if not isinstance(current, DeliveredRequest):
        return None
    gen = current.request.nodes[node].generation
    stop = StopRecord(node, gen, STOP_REASON.format(node=node))
    path = stop_path(state.ws_id, node, gen)
    record = render_stop(stop)
    branch, pr, head = _stop_pr(state, ops, node, gen, path, record)
    facts = an._pr_facts(state, ops, pr)
    if an._disposition(facts, pr) is Disposition.OPEN:
        _merge_stop_pr(state, ops, dag, node, branch, pr, head, path, record)
        facts = an._pr_facts(state, ops, pr)
    if an._disposition(facts, pr) is not Disposition.MERGED:
        raise RuntimeError(
            f"stop-PR #{pr} не смёржен — узел {node} остаётся одобренным"
        )
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        raise an._unresolved(f"merge-коммит stop-PR #{pr} — {event.detail}")
    commit = event.value.commit
    _require_stop_delivered(state, ops, branch, pr, commit, path, record, node, gen)
    # положительное суждение «остановлен» — тем же входом, что статус (B2-3):
    # действующее событие узла в истории базы — именно эта остановка этим
    # merge-коммитом
    after = act_status.load_history(ops, state, base)
    if isinstance(after, Unavailable):
        raise an._unresolved(f"история после stop-PR #{pr}: {after.reason}")
    if node_history(after, node).current != DeliveredStop(commit, stop):
        raise RuntimeError(
            f"stop-PR #{pr}: действующее событие узла {node} в базе — не эта остановка "
            f"g{gen}; разберите вручную"
        )
    return f"остановка {path} (g{gen}) доставлена PR #{pr}"


def _merge_stop_pr(
    state: RunState,
    ops: Ops,
    dag: Dag,
    node: str,
    branch: str,
    pr: int,
    head: str,
    path: str,
    record: str,
) -> None:
    """Агентский мерж открытого stop-PR — только проверенной формы, с пином
    проверенной головы и базы (как finalize: `_try_agent_merge(form=…)`). PR на
    ветке остановки мог открыть кто угодно: он обязан вливаться в базу прогона, а
    дифф целиком (все коммиты) — быть ровно записью остановки; иначе мерж не
    выполняется."""
    if not head:
        raise an._unresolved(f"голова stop-PR #{pr} не названа")
    base_sha = ops.rev_parse(state.target_dir, an._base_ref(state))
    if base_sha is None:
        raise an._unresolved(f"верхушка базы {an._base_ref(state)} не разрешается")
    ops.fetch_branch(state.target_dir, branch)

    def form() -> str | None:
        return stop_pr_defect(state, ops, pr, head, base_sha, path, record)

    defect = form()
    if defect is not None:
        raise RuntimeError(
            f"{defect} — агентский мерж не выполнен, узел {node} остаётся одобренным; "
            "разберите вручную"
        )
    retry = f"`--reopen {node}`"
    missing = an._missing_approving_review(state, ops, pr, kind="stop-PR", retry=retry)
    if missing is not None:
        raise RuntimeError(missing)
    refusal = an._try_agent_merge(
        state,
        ops,
        dag,
        {"finalize_branch": branch},
        [node],
        pr,
        head,
        form=form,
        base=base_sha,
        kind="stop-PR",
        retry=retry,
    )
    if refusal is not None:
        late = form()  # форма разошлась за время ожидания — тот же отказ, что первый
        if late is not None:
            raise RuntimeError(
                f"{late} — агентский мерж не выполнен, узел {node} остаётся "
                "одобренным; разберите вручную"
            )
        raise RuntimeError(
            f"stop-PR #{pr}: {refusal.reason} — остановка не доставлена, узел {node} "
            "остаётся одобренным; повторите --reopen"
        )


def stop_pr_defect(
    state: RunState,
    ops: Ops,
    pr: int,
    head: str,
    base_sha: str,
    path: str,
    record: str,
) -> str | None:
    """Форма stop-PR против базы: PR вливается в базу прогона (`baseRefName`,
    перечитывается при каждой проверке — ретаргет закрыт и пином базы у мержа),
    весь дифф `base_sha...head` — ровно добавление `path`, в `head` по нему —
    ровно ожидаемая запись остановки."""
    target = an._pr_facts(state, ops, pr).get("baseRefName")
    if not isinstance(target, str) or not target:
        raise an._unresolved(f"база stop-PR #{pr} не названа")
    if target != an._base_ref(state):
        return f"stop-PR #{pr} вливается в {target}, а не в базу {an._base_ref(state)}"
    fact = ops.compare_files_fact(state.repo_slug, base_sha, head)
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        raise an._unresolved(f"файлы stop-PR #{pr} — {fact.detail}")
    files = sorted(fact.value)
    if files != [(path, "added")]:
        return f"stop-PR #{pr} трогает {files}, ожидалось ровно добавление {path}"
    if ops.show_file(state.target_dir, head, path) != record:
        return f"stop-PR #{pr}: {path} в {head[:12]} — не ожидаемая запись остановки"
    return None


def _stop_pr(
    state: RunState, ops: Ops, node: str, gen: int, path: str, record: str
) -> tuple[str, int, str]:
    """Ветка stop-PR узла, номер и голова: найденный по ветке либо созданный.

    Найденный PR, закрытый без мержа, остановку не доставит — создаётся свой, но
    только если ветки остановки на remote нет: ветку без PR (чужую либо от
    упавшего захода между push и созданием PR) механика не усыновляет, отказ
    называет выход."""
    branch = f"stop/{state.ws_id}-{node}-g{gen}"
    found = af.search_pr(ops, state.repo_slug, branch, any_state=True)
    if found.outcome is Outcome.UNAVAILABLE:
        raise an._unresolved(f"поиск stop-PR на ветке {branch} — {found.detail}")
    what = "ветка есть без stop-PR"
    if found.outcome is Outcome.FOUND:
        pr = int(found.value or 0)
        unusable = _unusable_stop_pr(state, ops, pr, path, record)
        if unusable is None:
            return branch, pr, an.pr_head(state, ops, pr) or ""
        what = unusable
    remote = ops.remote_branch_head_fact(state.repo_slug, branch)
    if remote.outcome is Outcome.UNAVAILABLE:
        raise an._unresolved(f"ветка остановки {branch} на remote — {remote.detail}")
    if remote.outcome is Outcome.FOUND:
        raise RuntimeError(
            f"{what} ({branch}) — остановка не доставлена, узел {node} остаётся "
            f"одобренным. {_remedy(branch, node)}"
        )
    ops.switch_to(state.target_dir, branch, an._base_ref(state))
    file = Path(state.target_dir) / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(record, encoding="utf-8")
    ops.commit_paths(
        state.target_dir, [path], f"spec: {state.ws_id} — остановка {node} g{gen}"
    )
    ops.push_branch(state.target_dir, branch)
    head = ops.rev_parse(state.target_dir, "HEAD") or ""
    pr = ops.create_pr(
        state.target_dir,
        state.repo_slug,
        branch,
        f"spec: {state.ws_id} — остановка одобрения {node} g{gen}",
        f"Запись остановки (спека §8, Q1): `{path}`. Только отзывает.",
        "",
    )
    return branch, pr, head


def _remedy(branch: str, node: str) -> str:
    """Выход из тупика ветки остановки: без неё `reopen` откроет свой stop-PR."""
    return (
        f"Выход: удалите ветку остановки на remote (`git push origin --delete "
        f"{branch}`), затем повторите --reopen {node}"
    )


def _unusable_stop_pr(
    state: RunState, ops: Ops, pr: int, path: str, record: str
) -> str | None:
    """Почему найденный stop-PR остановку не доставит (`None` — годен: открыт либо
    смёржен в базу с ожидаемой записью). Закрыт без мержа, смёржен не в базу или
    без записи — его повторный разбор ничего не даст."""
    facts = an._pr_facts(state, ops, pr)
    where = an._disposition(facts, pr)
    if where is Disposition.CLOSED_UNMERGED:
        return f"stop-PR #{pr} закрыт без мержа"
    if where is not Disposition.MERGED:
        return None
    event = af.merge_event(facts)
    if event.outcome is not Outcome.FOUND or event.value is None:
        raise an._unresolved(f"merge-коммит stop-PR #{pr} — {event.detail}")
    commit = event.value.commit
    reached = ops.is_ancestor(state.target_dir, commit, an._base_ref(state))
    if reached is None:
        raise an._unresolved(f"stop-PR #{pr}: достижимость из базы")
    if not reached:
        return f"stop-PR #{pr} смёржен не в базу {an._base_ref(state)}"
    if ops.show_file(state.target_dir, commit, path) != record:
        return f"stop-PR #{pr} смёржен без записи остановки {path}"
    return None


def _require_stop_delivered(
    state: RunState,
    ops: Ops,
    branch: str,
    pr: int,
    commit: str,
    path: str,
    record: str,
    node: str,
    gen: int,
) -> None:
    """Доставка остановки доказывается базой, а не состоянием PR (B1-15)."""
    reached = _in_base(state, ops, commit)
    if reached is None:
        raise an._unresolved(f"stop-PR #{pr}: достижимость из базы")
    if not reached:
        raise RuntimeError(
            f"stop-PR #{pr} смёржен не в базу {an._base_ref(state)} — остановка не "
            f"доставлена, узел {node} остаётся одобренным; разберите вручную (мерж в "
            f"чужую ветку — откатите там при необходимости). {_remedy(branch, node)}"
        )
    if ops.show_file(state.target_dir, commit, path) != record:
        raise RuntimeError(
            f"stop-PR #{pr} не несёт записи остановки {path} узла {node} поколения "
            f"{gen} — узел остаётся одобренным; разберите PR вручную. "
            f"{_remedy(branch, node)}"
        )


def audit_late_dismissals(
    state: RunState, ops: Ops, key: str, deps: act_status.Deps | None = None
) -> int:
    """Отзывы после доставки завершённой заявки `review/v1` → события аудита (§8).

    Решение не меняется (терминальная запись не мутируется, §I4): события —
    в соседней записи `audit-<key>`, идемпотентно по `review_id`."""
    op = state.ops[key]
    if op.get("act") != ACT_REVIEW_V1 or op.get("status") != al.STATUS_COMPLETED:
        return 0
    deps = deps or DEPS_FACTORY(state)
    facts = act_facts.open_review(deps.gh, state.repo_slug, op["candidate_pr"])
    if isinstance(facts, Unavailable):
        return 0  # аудит не прочитан; решение от него не зависит
    merged_at = op["merged_at"]
    written = 0
    for d in facts.dismissals:
        # метки — канон `YYYY-MM-DDTHH:MM:SSZ`: строковое сравнение = временному
        if d.created_at > merged_at and al.record_audit(
            state,
            key,
            {
                "event": "review_dismissed_after_delivery",
                "review_id": d.review_id,
                "at": d.created_at,
            },
        ):
            written += 1
    return written


def audit_completed(state: RunState, ops: Ops) -> int:
    """Аудит позднего отзыва по всем завершённым заявкам `review/v1` прогона
    (`resume`, `--approve-node`; приёмка №10); зависимости — один раз."""
    keys = [
        al.request_key(*nums)
        for nums, op in al.requests(state)
        if op.get("act") == ACT_REVIEW_V1 and op.get("status") == al.STATUS_COMPLETED
    ]
    if not keys:
        return 0
    deps = DEPS_FACTORY(state)
    return sum(audit_late_dismissals(state, ops, key, deps) for key in keys)
