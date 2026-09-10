"""Двухфазное одобрение узла бандла (§I12): candidate, мерж, финализация.

Механика **готовит предложение об одобрении**, а не совершает его. Актом
одобрения является мерж candidate-PR учёткой из
`authorized_approver_accounts`, и оттуда же берётся подпись; всё, что делает
вызов, — приводит узел в предъявимый вид, выносит его на этот мерж и потом
переписывает в узел уже состоявшуюся подпись.

Граница ответственности (§I12): человек **решает**, механика **считает**.
Она валидирует узел, проверяет approved-состояние прямых upstream, обновляет
пины, `approved_content_hash` и версию, выносит предложение PR-ом и после
мержа регистрирует подпись. Решения об одобрении она не принимает и подписи
не создаёт — всё, что она умеет добавить от себя, это отказ.

CLI-поверхности здесь НЕТ намеренно: `--approve-node` появляется отдельно
(часть 3). Модуль вызывается напрямую и тестами; ни одна доставка его пока
не зовёт, поэтому его мерж ничего не включает.

Три правила, которые в этом файле держат всё остальное:

1. **Неустановленный факт не открывает дверь.** Терминализовать заявку
   вправе только положительно установленный факт
   (`approval_facts.Fact.established`); `UNAVAILABLE` — отказ вызова с
   сохранением ЖИВОЙ заявки, и повтор возобновляет ту же операцию.
2. **Имя — адрес, а не идентичность.** PR заявки опознаётся по записанным
   `pr`/`head_sha`; имя ветки говорит только, где смотреть.
3. **Отказ не создаёт эффектов.** Проверки состава, готовности upstream и
   разошедшихся пинов выполняются ДО первой записи; терминальный статус
   заявки с причиной — журнальный факт, а не эффект (§I4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from governance import approval_facts as af
from governance import approval_ledger as al
from governance import bundle_dag
from governance import node_approval as na
from governance.approval_facts import Disposition, Outcome
from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.ops import Ops
from governance.run_state import RunState
from governance.stale_adapter import blob_sha1

#: Машинная метка обоих approval-PR. Вешается В САМОМ вызове создания PR
#: (`ops.create_pr`), а не отдельным шагом после: PR без метки означал бы,
#: что создание не завершилось. Гвард агентского мержа проверяет и её, и
#: форму имени ветки — признаки независимы намеренно.
HUMAN_MERGE_LABEL = "human-merge-required"


@dataclass(frozen=True)
class ApprovalOutcome:
    """Что вызов сделал: сообщение оператору, заявка, изменённые файлы."""

    message: str
    request: str | None = None
    changed: tuple[str, ...] = ()


# --- Общие мелочи -------------------------------------------------------


def _base_ref(state: RunState) -> str:
    return state.base_ref or "master"


def _rel(state: RunState, fname: str) -> str:
    return f"{state.bundle_dir}/{fname}"


def _filename(dag: tuple[tuple[str, tuple[str, ...]], ...], node: str) -> str:
    for fname, _ in dag:
        if bundle_dag.node_id(fname) == node:
            return fname
    raise KeyError(node)


def _upstreams(
    dag: tuple[tuple[str, tuple[str, ...]], ...], node: str
) -> tuple[str, ...]:
    for fname, ups in dag:
        if bundle_dag.node_id(fname) == node:
            return ups
    raise KeyError(node)


def _levels(dag: tuple[tuple[str, tuple[str, ...]], ...]) -> dict[str, int]:
    """Уровень каждого узла: 0 у корня, иначе `1 + max` по прямым upstream.

    `K` в имени ветки — номер УРОВНЯ, и выводится он из графа, а не из
    леджера: два вызова по узлам одного уровня обязаны попасть в один шаг
    и одну ветку, а порядок вызовов на это влиять не вправе. Обход идёт по
    `dag`, который топологичен по построению.
    """
    levels: dict[str, int] = {}
    for fname, ups in dag:
        node = bundle_dag.node_id(fname)
        levels[node] = 1 + max((levels[u] for u in ups), default=-1)
    return levels


def _downstream_closure(
    dag: tuple[tuple[str, tuple[str, ...]], ...], node: str
) -> set[str]:
    """Транзитивное downstream-замыкание узла — без него самого.

    Критерий именно транзитивный (§I12): каскад `stale` меняет файл каждого
    узла замыкания, значит любая живая заявка внутри него посчитана против
    байтов, которых после этого узла не станет.
    """
    closure: set[str] = set()
    frontier = [node]
    while frontier:
        current = frontier.pop()
        for fname, ups in dag:
            child = bundle_dag.node_id(fname)
            if current in ups and child not in closure:
                closure.add(child)
                frontier.append(child)
    return closure


def _unresolved(detail: str) -> RuntimeError:
    """Отказ по НЕустановленному факту: заявка сохранена, повтор возобновит.

    Формулировка обязательная (§I12): диагностика говорит «факт не
    установлен, заявка сохранена» и называет сверку. Приписывать конкретную
    причину она не вправе — причина не установлена, а названная наугад
    уводит оператора чинить то, что не сломано.
    """
    return RuntimeError(
        f"факт не установлен, заявка сохранена: {detail}. "
        "Ничего не закрыто и не удалено; повторите вызов"
    )


def _base_text(ops: Ops, state: RunState, fname: str) -> str:
    """Байты файла бандла в синхронизированном `base`."""
    fact = af.read_blob_text(
        ops, state.target_dir, _base_ref(state), _rel(state, fname)
    )
    if fact.outcome is not Outcome.FOUND or fact.value is None:
        raise _unresolved(f"байты {fname} в base — {fact.detail}")
    return fact.value


def _pr_facts(state: RunState, ops: Ops, pr: int) -> dict:
    """Факты PR либо отказ без эффектов (заявка остаётся живой)."""
    fact = af.read_pr(ops, state.repo_slug, pr)
    if fact.outcome is not Outcome.FOUND or not isinstance(fact.value, dict):
        raise _unresolved(f"факты PR #{pr} — {fact.detail}")
    return fact.value


def _disposition(facts: dict, pr: int) -> Disposition:
    """Состояние PR либо отказ: «наверное открыт» здесь запрещено."""
    where = af.disposition(facts)
    if where.outcome is not Outcome.FOUND or where.value is None:
        raise _unresolved(f"состояние PR #{pr} — {where.detail}")
    return where.value


def _base_upstream_blobs(
    ops: Ops,
    state: RunState,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node: str,
) -> dict[str, str]:
    """Фактические блобы прямых upstream'ов узла в `base`."""
    return {
        up: blob_sha1(_base_text(ops, state, _filename(dag, up)))
        for up in _upstreams(dag, node)
    }


# --- Точка входа --------------------------------------------------------


def approve_node(
    state: RunState,
    ops: Ops,
    node_id: str,
    *,
    legacy_bundle: int | None = None,
) -> ApprovalOutcome:
    """Один вызов одобрения узла: предложение либо продвижение заявки.

    Порядок ровно такой, каким его задаёт §I12:

    1. вход — **node-id активного DAG, не путь**. Произвольный путь
       позволил бы вынести на одобрение файл вне DAG, копию файла, файл
       чужого бандла — то есть предложить к подписи то, чего активный граф
       не содержит; approve есть акт о ПОЗИЦИИ В ГРАФЕ, а не о файле;
    2. живая заявка над узлом — не повод отказать и не повод завести
       вторую: вызов реконсилирует существующую и продвигает её ровно на
       тот шаг, который ещё не выполнен (§I12, пункт 7);
    3. живой заявки нет — решение принимается по состоянию узла в base.
    """
    dag = bundle_dag.dag_for(legacy_bundle)
    if ops.is_dirty(state.target_dir):
        raise RuntimeError(
            f"target_dir {state.target_dir!r} грязный — одобрение не начато"
        )
    ops.checkout_and_pull(state.target_dir, _base_ref(state))
    bundle_dag.check_bundle_composition(state.target_dir, state.bundle_dir, dag)
    known = [bundle_dag.node_id(fname) for fname, _ in dag]
    if node_id not in known:
        raise RuntimeError(
            f"узел {node_id!r} не входит в активный DAG. Допустимы: "
            f"{', '.join(known)}. Процедура: назовите node-id из этого "
            "перечня либо укажите --legacy-bundle с точным составом бандла"
        )
    live = al.live_request_over(state, node_id)
    if live is not None:
        nums, op = live
        return _advance(state, ops, dag, op, al.request_key(*nums))
    return _propose(state, ops, dag, node_id)


# --- Предложение: новая заявка либо присоединение к идущему шагу ---------


def _propose(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node_id: str,
) -> ApprovalOutcome:
    """Решение по состоянию узла в base и, если надо, фаза 1."""
    fname = _filename(dag, node_id)
    text = _base_text(ops, state, fname)
    upstream_blobs = _base_upstream_blobs(ops, state, dag, node_id)
    debt = na.node_debt(node_id, text, upstream_blobs)
    if debt is None:
        # Пункт 4: повтор над честно одобренным узлом. Ни записи в файл, ни
        # candidate-PR, ни инкремента `version` — иначе сменился бы блоб
        # узла, его downstream уехал бы в `stale`, и каждый лишний вызов
        # плодил бы долг одобрения на ровном месте.
        return ApprovalOutcome(
            f"{node_id}: честно одобрен — одобрять нечего (no-op)"
        )
    if debt.kind in (na.DEBT_PINS, na.DEBT_UNKNOWN_STATUS):
        # Пункт 6: молчаливая перепиновка здесь означала бы ровно то
        # действие, которым spec-runner#410 сделал ложное утверждение
        # истинным на вид.
        raise RuntimeError(debt.render())

    _require_upstream_ready(state, ops, dag, node_id)
    _require_no_reopened_pr(state, ops, node_id)

    self_hash = na.self_hash(text)
    step = _levels(dag)[node_id]
    wave = _wave_for(state, dag)

    joined = al.live_request_for_step(state, wave, step)
    if joined is not None:
        nums, op = joined
        key = al.request_key(*nums)
        al.extend_request(state, key, node_id, self_hash, upstream_blobs)
    else:
        attempt = al.next_attempt(state, wave, step)
        key = al.start_request(
            state,
            state.ws_id,
            wave,
            step,
            attempt,
            [node_id],
            {node_id: self_hash},
            {node_id: upstream_blobs},
        )
    changed = _commit_phase1(state, ops, dag, [node_id], key)
    return _publish_candidate(state, ops, dag, key, changed, debt)


def _wave_for(
    state: RunState, dag: tuple[tuple[str, tuple[str, ...]], ...]
) -> int:
    """Номер волны текущего прохода по DAG (§I12).

    Волна — ОДИН проход по DAG, и `K` растёт внутри неё по уровням; номер
    выбирается один раз, при её открытии, и дальше все вызовы берут его из
    леджера. Отсюда три случая:

    - есть живая заявка — её волна и есть идущая;
    - живой нет, но старшая записанная волна не закрыта — проход
      продолжается в ней. Между завершением уровня `K` и созданием `K+1`
      живой заявки нет вовсе, и волна обязана это переживать; тот же
      случай делает восстановление тем, чем его описывает контракт:
      заявка, умершая в `invalidated`, восстанавливается новой **в той же
      волне и на том же уровне**, а различает их номер попытки `A`. Открой
      мы здесь новую волну — `A` не рос бы никогда, и механика
      уникальности имён держалась бы на другом числе, чем сказано;
    - волна закрыта — новая, номер `max + 1` из леджера. Старые волны в
      выборе номера не участвуют и переиспользованию не подлежат.

    Закрытие волны — ЗАПИСАННЫЙ факт (`al.closed_waves`), а не вывод из
    текущего состояния дерева, и записывается он тем вызовом, который
    довёл активный DAG до честной одобренности ЦЕЛИКОМ
    (`_close_wave_if_dag_approved`). Завершения заявки терминального
    уровня для этого мало: терминальный узел мог быть одобрен, а узел вне
    его транзитивного замыкания — нет. Это то же различие, из-за которого
    §I5 считает манифест по всем узлам DAG, а не блоб терминального.
    """
    live = al.open_wave(state)
    if live is not None:
        return live
    recorded = al.requests(state)
    if not recorded:
        return al.next_wave(state)
    current = max(nums[0] for nums, _ in recorded)
    return al.next_wave(state) if current in al.closed_waves(state) else current


def _require_upstream_ready(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node_id: str,
) -> None:
    """Все ПРЯМЫЕ upstream честно одобрены в base — и только в base (§I12).

    Не в рабочем дереве и не в head'е открытой candidate-ветки:
    окончательные байты upstream появляются только после фазы 3, до неё в
    ветке лежит `approval_pending` без подписи. Это же требование,
    сформулированное для одного узла, и есть механический запрет выносить
    зависимые уровни DAG одним candidate-PR: посчитай мы пин downstream в
    том же PR, он ссылался бы на состояние, которое в base никогда не
    попадёт.

    Прямые, а не транзитивные: каждый approve сам требовал того же от
    своих upstream, значит индукция покрывает всё замыкание предков — а
    замыкание, собранное помимо этой команды, ловит условие (3) предиката.
    """
    for up in _upstreams(dag, node_id):
        text = _base_text(ops, state, _filename(dag, up))
        debt = na.node_debt(up, text, _base_upstream_blobs(ops, state, dag, up))
        if debt is None:
            continue
        raise RuntimeError(
            f"upstream {up} узла {node_id} не одобрен в base — "
            f"{debt.reason}. Процедура: одобрите его первым "
            f"({debt.procedure}); зависимые уровни DAG одним candidate-PR "
            "не выносятся"
        )


def _require_no_reopened_pr(
    state: RunState, ops: Ops, node_id: str
) -> None:
    """PR терминализированной заявки над этим узлом не переоткрыт (§I12).

    Терминальная запись говорит одно, форджа другое, и разрешать это
    расхождение молча значит выбрать за человека, какое из двух
    предложений считать настоящим. Переход человеческий намеренно —
    расхождение создал человек, и только он знает, что имел в виду.

    Неустановленное состояние PR тоже останавливает, и это НЕ «не
    хоронить»: правило про неустановленный факт защищает живую заявку от
    смерти, а здесь речь о том, публиковать ли ВТОРОЕ mergeable
    предложение, пока про первое ничего не известно. Цена ошибки
    противоположная, значит и fail-closed противоположный.
    """
    for nums, op in al.requests(state):
        if al.is_live(op) or node_id not in (op.get("nodes") or ()):
            continue
        for pr in (op.get("candidate_pr"), op.get("finalize_pr")):
            if pr is None:
                continue
            if _disposition(_pr_facts(state, ops, pr), pr) is Disposition.OPEN:
                raise RuntimeError(
                    f"PR #{pr} терминализированной заявки "
                    f"{al.request_key(*nums)} (статус {op.get('status')}, "
                    f"причина: {op.get('reason')!r}) переоткрыт. "
                    "Процедура: закройте переоткрытый PR, после чего "
                    "восстановление идёт обычным новым candidate"
                )


# --- Инвалидация живых заявок ниже по течению ---------------------------


def _invalidate_downstream(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node_id: str,
    by_key: str,
) -> None:
    """Живые заявки downstream-замыкания — терминально, ДО публикации (§I12).

    Окно, которое это закрывает: заявка над X посчитана против прежнего
    блоба U; над U заводится новая; заявка X остаётся живой, и её candidate
    может быть смержен уже после того, как U сдвинулся, — в base попадает
    одобрение, посчитанное по байтам, которых нет. Убивается поэтому не
    статус, а ЗАЯВКА.

    Порядок «сначала запись, потом закрытие» — из §I10: только запись
    объясняет закрытие. Отсюда же и вторая половина обхода: заявки, уже
    помеченные ЭТОЙ операцией (`invalidated_by == by_key`), проходятся
    снова — их запись есть, а закрытие могло не состояться, и пропусти мы
    их, повтор опубликовал бы candidate поверх открытого предложения ниже.

    Блокирует РОВНО закрытие открытых PR. Вывод ветки из обращения — нет:
    с уникальным именем на заявку старая ветка не мешает ничем, а её
    удаление идёт через примитив, который сам неразличим (#177) — сделай
    этот шаг блокирующим, и воркстрим, где удаление не подтверждается, не
    заведёт новую заявку никогда.
    """
    closure = _downstream_closure(dag, node_id)
    for nums, op in al.requests(state):
        key = al.request_key(*nums)
        if key == by_key:
            continue
        doomed_now = al.is_live(op) and bool(
            set(op.get("nodes") or ()) & closure
        )
        already_ours = op.get("invalidated_by") == by_key
        if not doomed_now and not already_ours:
            continue
        if doomed_now:
            al.invalidate_request(
                state,
                key,
                f"снята заявкой {by_key} над upstream {node_id}: её "
                "предложение посчитано против байтов, которых после "
                "каскада не станет",
                by=by_key,
            )
        for pr in (op.get("candidate_pr"), op.get("finalize_pr")):
            if pr is not None:
                _close_if_open(state, ops, pr, by_key)
        for branch in (op.get("branch"), op.get("finalize_branch")):
            if branch:
                # Не блокирует: оставшаяся ветка — неубранный мусор, а не
                # опасность (имена уникальны на заявку, усыновление идёт
                # по записанному head_sha).
                ops.delete_remote_branch(state.repo_slug, branch)


def _close_if_open(
    state: RunState, ops: Ops, pr: int, by_key: str
) -> None:
    """Закрыть PR снятой заявки, если он открыт; иначе — ничего.

    Состояние спрашивается ПЕРВЫМ, и это не оптимизация: `close_pr` по уже
    закрытому PR отдаёт `False`, неотличимый от «нет прав», и повтор
    операции упирался бы в него вечно.

    Вмерженный PR закрывать нечего и не надо: его предложение со стола уже
    ушло — в base, — и долг, который оно там создало, объявит каскад
    нашего же approve. Открытым остаётся только то, что ещё можно смержить,
    и ровно это блокирует публикацию.
    """
    if _disposition(_pr_facts(state, ops, pr), pr) is not Disposition.OPEN:
        return
    closed = af.confirm_closed(
        ops,
        state.repo_slug,
        pr,
        f"Предложение снято заявкой {by_key} (§I12): upstream выносится на "
        "одобрение заново, и это предложение посчитано против прежних байтов.",
    )
    if not closed.established:
        raise _unresolved(
            f"закрытие PR #{pr} — {closed.detail}; candidate НЕ опубликован"
        )


# --- Фаза 1: предложение об одобрении -----------------------------------


def _switch_to_request_branch(
    state: RunState, ops: Ops, branch: str, head_sha: str | None
) -> None:
    """Встать на ветку заявки: на её коммит, если он записан, иначе на base.

    Идентичность даёт ЗАПИСЬ, а не имя: `head_sha` заявки пишется durable
    сразу после коммита и до push, поэтому именно он говорит, где наша
    работа. Ушедшая вперёд чужая голова под тем же именем не подхватывается
    — push такой ветки отвергнут как non-ff, и это правильный fail-closed.
    """
    if head_sha is None:
        ops.switch_to(state.target_dir, branch, _base_ref(state))
        return
    ops.fetch_branch(state.target_dir, branch)
    if ops.rev_parse(state.target_dir, head_sha) is None:
        raise RuntimeError(
            f"коммит {head_sha[:8]} заявки недоступен в клоне, а ветка "
            f"{branch} записана за ней. Процедура: подтяните ветку заявки "
            "(git fetch origin <ветка>) в этот клон и повторите вызов"
        )
    ops.switch_to(state.target_dir, branch, head_sha)


def _commit_phase1(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    nodes: list[str],
    key: str,
) -> tuple[str, ...]:
    """Фаза 1 на ветке заявки: правки, каскад, коммит, durable `head_sha`.

    Коммитом уходят ВСЕ изменённые вызовом файлы — вынесенный узел и весь
    рекурсивный след `stale`. Частичный коммит оставил бы в ветке
    `approved`-узел с ложным пином, то есть ровно то, что каскад обязан не
    допускать.

    `head_sha` пишется между коммитом и push, и это не порядок ради
    порядка: падение здесь иначе оставляет заявку без единственного факта,
    по которому её работу опознают в удалённой ветке.
    """
    op = state.ops[key]
    _switch_to_request_branch(
        state, ops, op["branch"], op.get("head_sha")
    )
    changed: list[str] = []
    for node in nodes:
        changed += _phase1_write(state, ops, dag, node, key)
    ops.commit_paths(
        state.target_dir,
        changed,
        f"spec: {state.ws_id} — узел(ы) {', '.join(nodes)} вынесены на "
        f"одобрение (approval_pending), каскад stale (fleet-agent)",
    )
    head = ops.rev_parse(state.target_dir, "HEAD")
    if head is None:
        raise RuntimeError(
            "коммит фазы 1 не состоялся: HEAD не разрешается — "
            "предложение не публикуется"
        )
    al.record_head_sha(state, key, head)
    return tuple(changed)


def _phase1_write(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    node: str,
    key: str,
) -> list[str]:
    """Узел → `approval_pending` плюс рекурсивный каскад; → rel-пути.

    Пишутся ровно четыре величины: `status`, пересчитанные с фактических
    байтов upstream в base пины, `approved_content_hash` с собственных
    байтов и `version + 1` — новое поколение одобрения предлагается именно
    этим вызовом.

    Подпись НЕ пишется: её неоткуда взять, акт ещё не совершён. Прежняя,
    если узел шёл из `approved`, остаётся в файле нетронутой — фаза 1
    перечисляет, что она пишет, и стирание в этот перечень не входит; для
    предиката она всё равно невидима, потому что условие (1) не выполнено.
    """
    fname = _filename(dag, node)
    path = Path(state.target_dir) / _rel(state, fname)
    text = path.read_text(encoding="utf-8")
    recorded = state.ops[key]["content_hashes"][node]
    actual = na.self_hash(text)
    if actual != recorded:
        raise RuntimeError(
            f"{node}: собственные байты на ветке заявки не те, что она "
            f"вынесла (записано {recorded}, на ветке {actual}) — "
            "предложение не публикуется"
        )
    meta, body = split_frontmatter(text)
    meta["status"] = na.STATUS_APPROVAL_PENDING
    meta["version"] = int(meta.get("version") or 1) + 1
    pins = state.ops[key]["upstream_pins"][node]
    if pins:
        meta["upstream_hashes"] = dict(pins)
    meta[na.SELF_HASH_KEY] = actual
    path.write_text(join_frontmatter(meta, body), encoding="utf-8")
    return [_rel(state, fname), *_cascade_stale(state, dag, node)]


def _cascade_stale(
    state: RunState,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    changed_node: str,
) -> list[str]:
    """Рекурсивный каскад `stale` вниз от изменённого узла; → rel-пути.

    Алгоритм §I12: downstream в `approved` ИЛИ `approval_pending`
    переводится в `stale`; файл этого downstream от перевода изменился —
    значит каскад продолжается от него; на `draft`/`stale` ветка
    обрывается. Меняется РОВНО `status`: подпись, `version`, пины и
    `approved_content_hash` сохраняются.

    Одного уровня мало по механической причине: смена статуса есть правка
    файла, а файл узла входит в пин его собственных downstream. Рекурсия
    конечна без счётчика — узел, уже несущий долг, повторно помечать
    нечем, и на нём ветка обрывается.

    `approval_pending` каскадируется наравне с `approved` (правка по
    ревью): его долг объявлен НЕ ПРО ТОТ upstream, и оставить его значило
    бы оставить на столе предложение, которое уже неверно.
    """
    changed: list[str] = []
    frontier = [changed_node]
    while frontier:
        current = frontier.pop()
        for fname, ups in dag:
            child = bundle_dag.node_id(fname)
            if current not in ups:
                continue
            path = Path(state.target_dir) / _rel(state, fname)
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
            if not na.cascade_marks_stale(meta.get("status")):
                continue
            meta["status"] = na.STATUS_STALE
            path.write_text(join_frontmatter(meta, body), encoding="utf-8")
            changed.append(_rel(state, fname))
            frontier.append(child)
    return changed


def _publish_candidate(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    key: str,
    changed: tuple[str, ...],
    debt: na.NodeDebt | None = None,
) -> ApprovalOutcome:
    """Инвалидация ниже, push ветки заявки, создание/усыновление PR.

    Инвалидация живых заявок downstream стоит ЗДЕСЬ, а не в вызывающем, и
    это существенно: она обязана предшествовать КАЖДОЙ публикации, включая
    возобновление после падения между записью заявки и push'ем. Оставь её
    в одном лишь свежем пути — повтор опубликовал бы mergeable candidate
    поверх живого предложения ниже, то есть ровно то окно, ради которого
    правило написано.
    """
    op = state.ops[key]
    for node in op["nodes"]:
        _invalidate_downstream(state, ops, dag, node, key)
    ops.push_branch(state.target_dir, op["branch"])
    pr = op.get("candidate_pr")
    if pr is None:
        pr = _adopt_or_create_pr(
            state,
            ops,
            op["branch"],
            op["head_sha"],
            f"spec: {state.ws_id} — одобрение узлов "
            f"{', '.join(op['nodes'])} (§I12)",
            _candidate_body(state, op, debt),
        )
        al.record_candidate_pr(state, key, pr)
    return ApprovalOutcome(
        f"{', '.join(op['nodes'])}: вынесены на одобрение, candidate-PR "
        f"#{pr}. Одобрение совершает МЕРЖ этого PR учёткой из "
        f"{af.APPROVER_ALLOWLIST_ENV}; после мержа повторите вызов — он "
        "запишет подпись финализирующим PR",
        request=key,
        changed=changed,
    )


def _candidate_body(state: RunState, op: dict, debt: na.NodeDebt | None) -> str:
    lines = [
        f"Предложение об одобрении узлов бандла {state.ws_id} (§I12).",
        "",
        "**Мерж этого PR И ЕСТЬ акт одобрения**: подпись узла берётся из "
        f"`mergedBy`/`mergedAt` этого мержа, поэтому мержит его человек из "
        f"`{af.APPROVER_ALLOWLIST_ENV}`. Агентский мерж подписи не создаёт "
        "и приведёт к отказу финализации.",
        "",
        f"Узлы: {', '.join(op['nodes'])}.",
        "Изменено: `status → approval_pending`, `version + 1`, пины с "
        "фактических байтов upstream в base, `approved_content_hash` с "
        "собственных байтов узла, плюс рекурсивный каскад `stale` вниз по "
        "DAG. Подпись НЕ записана — акта ещё не было.",
    ]
    if debt is not None:
        lines += ["", f"Повод: {debt.reason}."]
    return "\n".join(lines)


def _adopt_or_create_pr(
    state: RunState,
    ops: Ops,
    branch: str,
    head_sha: str,
    title: str,
    body: str,
) -> int:
    """PR на ветке: усыновить свой (по head) либо создать; иначе отказ.

    Усыновление — лечение окна «коммит есть, PR ещё нет», и законно оно
    при ДВУХ условиях сразу: имя ветки уникально на заявку (номер `A`),
    значит PR на ней не может принадлежать другой попытке; и head
    найденного PR совпадает с записанным заявкой `head_sha`. Идентичность
    устанавливает запись, имя лишь говорит, где смотреть.

    Разошёлся head — fail-closed: под нашим именем чужая работа. Одного
    первого условия было бы мало — имя защищает от НАШИХ коллизий, но не
    от чужого push.
    """
    found = af.search_pr(ops, state.repo_slug, branch, any_state=True)
    if found.outcome is Outcome.UNAVAILABLE:
        raise _unresolved(f"поиск PR на ветке {branch} — {found.detail}")
    if found.outcome is Outcome.ABSENT:
        return ops.create_pr(
            state.target_dir,
            state.repo_slug,
            branch,
            title,
            body,
            HUMAN_MERGE_LABEL,
        )
    pr = int(found.value or 0)
    head = _pr_facts(state, ops, pr).get("headRefOid")
    if head != head_sha:
        raise RuntimeError(
            f"на ветке {branch} уже есть PR #{pr}, но его head {head} не "
            f"совпадает с записанным заявкой {head_sha} — под нашим именем "
            "чужая работа. Процедура: разберитесь с этим PR вручную "
            "(закрыть либо принять), заявка не тронута"
        )
    return pr


# --- Продвижение живой заявки -------------------------------------------


def _advance(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
) -> ApprovalOutcome:
    """Живая заявка продвигается ровно на невыполненный шаг (§I12, пункт 7).

    Шаг выводится из durable-записи, а не из того, что видно снаружи:
    факты форджи говорят, что есть сейчас, а запись — что эта заявка уже
    сделала, и после падения второе восстанавливается только из леджера.
    """
    step = al.next_step(op)
    if step is al.Step.CREATE_CANDIDATE:
        changed: tuple[str, ...] = ()
        if op.get("head_sha") is None:
            # Падение ДО коммита: работы нет, узлы в base остались в долге,
            # фаза 1 выполняется заново и даёт тот же результат — она
            # детерминирована по base.
            changed = _commit_phase1(state, ops, dag, list(op["nodes"]), key)
        return _publish_candidate(state, ops, dag, key, changed)
    if step is al.Step.AWAIT_CANDIDATE_MERGE:
        return _reconcile_candidate(state, ops, dag, op, key)
    if step is al.Step.FINALIZE:
        return _finalize(state, ops, dag, state.ops[key], key)
    return _reconcile_finalize(state, ops, dag, state.ops[key], key)


def _close_wave_if_dag_approved(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    wave: int,
) -> None:
    """Волна закрывается, когда честно одобрен ВЕСЬ активный DAG (§I12).

    Не когда завершилась заявка терминального уровня: терминальный узел
    мог быть одобрен, а узел вне его транзитивного замыкания — нет.
    Проверка идёт по всем узлам, как §I5 считает манифест по всему DAG.

    Вызывается там, где заявка стала `completed`, и только там: это
    единственный момент, когда состояние могло измениться в сторону
    закрытия. Base перед проверкой пересинхронизируется — мерж
    финализирующего PR мы узнали ПОСЛЕ того, как синхронизировали его в
    начале вызова.

    Неустановленный факт волну НЕ закрывает и вызов НЕ роняет: заявка уже
    завершена, ронять после успеха нечего, а незакрытая волна безвредна —
    имена остаются уникальными, а следующий вызов проверит заново. Это
    та же асимметрия, что у правила «не хоронить»: ошибка в сторону
    «ещё открыта» стоит номера, ошибка в другую сторону переиспользует
    волну.
    """
    ops.checkout_and_pull(state.target_dir, _base_ref(state))
    if _dag_fully_approved(state, ops, dag):
        al.close_wave(state, wave)


def _dag_fully_approved(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> bool:
    """Каждый узел активного DAG честно одобрен в base; факт не установлен
    — `False`.

    Читает не поднимая: этот вопрос задаётся ПОСЛЕ успешного завершения
    заявки, и превращать неполный ответ в отказ вызова значило бы
    отчитываться неудачей об удавшемся шаге.
    """
    texts: dict[str, str] = {}
    for fname, _ in dag:
        fact = af.read_blob_text(
            ops, state.target_dir, _base_ref(state), _rel(state, fname)
        )
        if fact.outcome is not Outcome.FOUND or fact.value is None:
            return False
        texts[bundle_dag.node_id(fname)] = fact.value
    for fname, ups in dag:
        node = bundle_dag.node_id(fname)
        blobs = {up: blob_sha1(texts[up]) for up in ups}
        if na.node_debt(node, texts[node], blobs) is not None:
            return False
    return True


def _reconcile_candidate(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
) -> ApprovalOutcome:
    """Судьба candidate-PR: ждём, хороним по решению человека, либо подпись.

    Три исхода и ровно три источника:

    - PR ОТКРЫТ — ждём человека (§I9), ничего не пишем;
    - PR ЗАКРЫТ без мержа — установленное отсутствие акта: человек РЕШИЛ
      не одобрять. Заявка `abandoned`, инкремент `version` в base не попал
      и откатывать нечего;
    - PR ВМЕРЖЕН — акт состоялся. Подпись создаёт только мерж учёткой из
      allowlist; мерж чужой учёткой — установленный факт, и он ведёт в
      `invalidated`, а не в отказ с сохранением заявки.
    """
    pr = op["candidate_pr"]
    facts = _pr_facts(state, ops, pr)
    if _disposition(facts, pr) is Disposition.OPEN:
        return ApprovalOutcome(
            f"candidate-PR #{pr} открыт — ждём мержа учёткой из "
            f"{af.APPROVER_ALLOWLIST_ENV}. Заявка {key} жива, ничего не "
            "изменено",
            request=key,
        )
    event = af.merge_event(facts)
    if event.outcome is Outcome.ABSENT:
        al.abandon_request(
            state, key, f"candidate-PR #{pr} закрыт без мержа — не одобрено"
        )
        return ApprovalOutcome(
            f"candidate-PR #{pr} закрыт без мержа: одобрения не было, "
            f"заявка {key} — abandoned. Новый candidate разрешён и является "
            "штатным восстановлением",
            request=key,
        )
    if event.outcome is not Outcome.FOUND:
        raise _unresolved(f"акт мержа candidate-PR #{pr} — {event.detail}")
    merged = event.value
    assert merged is not None
    signature = af.authorized_signature(merged)
    if signature.outcome is Outcome.FORBIDDEN:
        al.invalidate_request(state, key, signature.detail)
        raise RuntimeError(
            f"{signature.detail}. Заявка {key} — invalidated; "
            "восстановление: новый candidate над теми же узлами, мерж "
            "учёткой из allowlist даст верную подпись"
        )
    authorization = signature.value
    assert authorization is not None
    al.record_merge(state, key, merged, authorization)
    return _finalize(state, ops, dag, state.ops[key], key, facts)


def _reconcile_finalize(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
) -> ApprovalOutcome:
    """Судьба финализирующего PR: ждём, завершаем либо инвалидируем."""
    pr = op["finalize_pr"]
    where = _disposition(_pr_facts(state, ops, pr), pr)
    if where is Disposition.OPEN:
        return ApprovalOutcome(
            f"финализирующий PR #{pr} открыт — ждём мержа. Заявка {key} "
            "жива всегда, пока он открыт",
            request=key,
        )
    if where is Disposition.MERGED:
        al.complete_request(state, key)
        _close_wave_if_dag_approved(state, ops, dag, op["wave"])
        return ApprovalOutcome(
            f"конверт подписи в base (PR #{pr}): узлы "
            f"{', '.join(op['nodes'])} честно одобрены, заявка {key} "
            "завершена",
            request=key,
        )
    al.invalidate_request(
        state, key, f"финализирующий PR #{pr} закрыт человеком без мержа"
    )
    raise RuntimeError(
        f"финализирующий PR #{pr} закрыт без мержа — конверт в base не "
        f"попал, заявка {key} invalidated. Восстановление: новый candidate "
        "над теми же узлами"
    )


# --- Фаза 3: механическая финализация конверта --------------------------


def _finalize(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
    facts: dict | None = None,
) -> ApprovalOutcome:
    """Сверить пять фактов и записать конверт — ничего кроме конверта.

    Сверки идут ДО первой записи, и каждая сравнивает ПЕРЕСЧЁТ с
    записанным, а не поле с самим собой: поле отвечает на вопрос «что мы
    записали», пересчёт — «что там лежит на самом деле», и сверка имеет
    смысл только между разными ответами.

    Любой несошедшийся факт — fail-closed без записи конверта; заявка при
    этом терминализуется в `invalidated` ТОЛЬКО по положительно
    установленному факту, а неустановленный оставляет её живой.

    Пятая сверка — про личность — выполнена там, где факт
    устанавливается: в `_reconcile_candidate`, до записи фактов мержа.
    Здесь проверяется ЦЕЛОСТНОСТЬ записанного решения — то ли это решение
    и о том ли мерже, — но allowlist не применяется заново. Иначе правка
    конфигурации задним числом переавторизовывала бы прошлое: заявка,
    честно классифицированная вчера, умирала бы сегодня оттого, что
    список сузили, — и это ровно тот класс, ради которого §I12 сделал
    терминальный статус durable, а не выводимым.

    `facts` передаются, когда PR уже прочитан этим же вызовом (сквозной
    проход «мерж увидели — тут же финализируем»). На нём сверка
    идентичности вырождается в тождество, и это честно: её предмет —
    ВОЗОБНОВЛЕНИЕ, где запись сделана прошлым запуском, а факты читаются
    заново. Второе чтение того же PR ради видимости независимости было бы
    хуже: оно ничего не доказывает и стоит запроса.
    """
    pr = op["candidate_pr"]
    event = af.merge_event(
        facts if facts is not None else _pr_facts(state, ops, pr)
    )
    if event.outcome is not Outcome.FOUND:
        raise _unresolved(f"акт мержа candidate-PR #{pr} — {event.detail}")
    merged = event.value
    assert merged is not None
    if (
        merged.commit != op["merge_commit"]
        or merged.login != op["merged_by"]
        or merged.merged_at != op["merged_at"]
    ):
        al.invalidate_request(
            state,
            key,
            f"идентичность мержа PR #{pr} разошлась с записанной: "
            f"заявка помнит {op['merged_by']}/{op['merged_at']}/"
            f"{op['merge_commit']}, форджа отдаёт {merged.login}/"
            f"{merged.merged_at}/{merged.commit}",
        )
        raise RuntimeError(
            f"факты мержа PR #{pr} не совпали с записанными — заявка {key} "
            "invalidated; восстановление: новый candidate"
        )
    auth = op.get("authorization")
    if (
        not isinstance(auth, dict)
        or auth.get("login") != op["merged_by"]
        or not auth.get("policy")
    ):
        al.invalidate_request(
            state,
            key,
            "решение об авторизации мержа не записано либо относится к "
            f"другому мержу: в заявке {auth!r} при merged_by "
            f"{op['merged_by']!r}",
        )
        raise RuntimeError(
            f"заявка {key} не несёт целого решения об авторизации своего "
            "мержа — invalidated; восстановление: новый candidate"
        )
    in_base = ops.is_ancestor(
        state.target_dir, op["merge_commit"], _base_ref(state)
    )
    if in_base is None:
        raise _unresolved(
            f"есть ли merge-коммит {op['merge_commit'][:8]} в истории "
            f"{_base_ref(state)}"
        )
    if not in_base:
        al.invalidate_request(
            state,
            key,
            f"merge-коммит {op['merge_commit']} PR #{pr} отсутствует в "
            f"истории {_base_ref(state)}",
        )
        raise RuntimeError(
            f"merge-коммит PR #{pr} не в истории {_base_ref(state)} — "
            f"заявка {key} invalidated; восстановление: новый candidate"
        )
    pending = _verify_nodes_in_base(state, ops, dag, op, key)
    if not pending:
        al.complete_request(state, key)
        _close_wave_if_dag_approved(state, ops, dag, op["wave"])
        return ApprovalOutcome(
            f"конверт узлов {', '.join(op['nodes'])} уже в base — заявка "
            f"{key} завершена",
            request=key,
        )
    return _publish_envelope(state, ops, dag, key, pending)


def _verify_nodes_in_base(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    key: str,
) -> list[str]:
    """Сверки 3 и 4 по каждому узлу; → узлы, которым конверт ещё нужен.

    По `approved_content_hash` сравнений ДВА, и это не педантизм: поле —
    то, что мы сами записали, и правку ТЕЛА узла после мержа оно не
    замечает по построению. Первое сравнение отвечает «вмержены ли те
    байты, которые заявка выносила», второе — «пройдёт ли узел предикат
    после конверта». По пинам — та же пара и по тому же поводу: одно
    ловит сдвиг upstream, другое — правку самих пинов.
    """
    pending: list[str] = []
    for node in op["nodes"]:
        text = _base_text(ops, state, _filename(dag, node))
        meta, _ = split_frontmatter(text)
        recomputed = na.self_hash(text)
        _require(
            state,
            key,
            recomputed == op["content_hashes"][node],
            f"{node}: self-hash по фактическим байтам в base {recomputed} != "
            f"вынесенного заявкой {op['content_hashes'][node]} — вмержены не "
            "те байты",
        )
        _require(
            state,
            key,
            meta.get(na.SELF_HASH_KEY) == recomputed,
            f"{node}: поле {na.SELF_HASH_KEY} = "
            f"{meta.get(na.SELF_HASH_KEY)} != пересчёта {recomputed} — тело "
            "узла правили после мержа",
        )
        actual = _base_upstream_blobs(ops, state, dag, node)
        pins = meta.get("upstream_hashes")
        pins = dict(pins) if isinstance(pins, dict) else {}
        _require(
            state,
            key,
            pins == actual,
            f"{node}: пины {pins} != фактических блобов upstream в base "
            f"{actual} — upstream уехал между фазами",
        )
        _require(
            state,
            key,
            pins == dict(op["upstream_pins"][node]),
            f"{node}: пины {pins} != снимка заявки "
            f"{dict(op['upstream_pins'][node])} — пины правили после мержа",
        )
        status = meta.get("status")
        if status == na.STATUS_APPROVAL_PENDING:
            pending.append(node)
            continue
        _require(
            state,
            key,
            status == na.STATUS_APPROVED
            and meta.get("approved_by") == op["merged_by"]
            and meta.get("approved_at") == op["merged_at"],
            f"{node}: в base статус {status!r} с подписью "
            f"{meta.get('approved_by')!r}/{meta.get('approved_at')!r} — "
            "заявка выносила его на одобрение, а конверт в base стоит не её",
        )
    return pending


def _require(state: RunState, key: str, ok: bool, detail: str) -> None:
    """Сверка фазы 3: несошлась — `invalidated` с причиной и отказ.

    Постоянный семантический отказ, а не «повторите»: факт прочитан,
    однозначен и противоречит заявке. Перемержить уже вмерженный candidate
    нельзя, поэтому выход отсюда — новый candidate над теми же узлами
    (пункт 7), и он разрешён ровно потому, что эта запись терминальна.
    """
    if ok:
        return
    al.invalidate_request(state, key, detail)
    raise RuntimeError(
        f"сверка фазы 3 не сошлась: {detail}. Заявка {key} invalidated; "
        "восстановление: новый candidate — пересчёт пинов и self-hash по "
        "актуальному base, мерж правильной учёткой даёт верную подпись"
    )


def _publish_envelope(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    key: str,
    nodes: list[str],
) -> ApprovalOutcome:
    """Второй PR заявки: `approval_pending → approved` плюс подпись.

    Меняется ТОЛЬКО конверт — ни тела, ни пинов, ни
    `approved_content_hash`, ни `version`. Последнее — жёсткое требование
    §I12: фаза 3 обязана быть проверяемо-узкой, иначе она снова становится
    механикой, правящей содержание; и один акт одобрения — одно поколение
    документа, а второй инкремент рассказал бы про поколение, которого не
    было.
    """
    op = state.ops[key]
    branch = op["finalize_branch"]
    head = op.get("finalize_head_sha")
    if head is None:
        ops.switch_to(state.target_dir, branch, _base_ref(state))
        changed: list[str] = []
        for node in nodes:
            path = Path(state.target_dir) / _rel(state, _filename(dag, node))
            meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
            meta["status"] = na.STATUS_APPROVED
            meta["approved_by"] = op["merged_by"]
            meta["approved_at"] = op["merged_at"]
            path.write_text(join_frontmatter(meta, body), encoding="utf-8")
            changed.append(_rel(state, _filename(dag, node)))
        ops.commit_paths(
            state.target_dir,
            changed,
            f"spec: {state.ws_id} — конверт подписи узлов "
            f"{', '.join(nodes)} (approved by {op['merged_by']}, §I12)",
        )
        head = ops.rev_parse(state.target_dir, "HEAD")
        if head is None:
            raise RuntimeError(
                "коммит конверта не состоялся: HEAD не разрешается"
            )
        al.record_finalize_head_sha(state, key, head)
    else:
        _switch_to_request_branch(state, ops, branch, head)
        changed = [
            _rel(state, _filename(dag, node)) for node in nodes
        ]
    ops.push_branch(state.target_dir, branch)
    pr = _adopt_or_create_pr(
        state,
        ops,
        branch,
        str(state.ops[key]["finalize_head_sha"]),
        f"spec: {state.ws_id} — подпись узлов {', '.join(nodes)} (§I12, "
        "конверт)",
        _finalize_body(state, state.ops[key], nodes),
    )
    al.record_finalize_pr(state, key, pr)
    return ApprovalOutcome(
        f"подпись узлов {', '.join(nodes)} вынесена финализирующим PR #{pr} "
        f"(approved_by = {op['merged_by']}, approved_at = "
        f"{op['merged_at']}). Мержит его учётка из "
        f"{af.APPROVER_ALLOWLIST_ENV}; источником подписи он НЕ является — "
        "подпись сформирована фактами мержа candidate-PR",
        request=key,
        changed=tuple(changed),
    )


def _finalize_body(state: RunState, op: dict, nodes: list[str]) -> str:
    return "\n".join(
        [
            f"Конверт подписи узлов бандла {state.ws_id} (§I12, фаза 3).",
            "",
            f"Узлы: {', '.join(nodes)}.",
            "Изменено РОВНО три поля на узел: `status: approval_pending → "
            "approved`, `approved_by`, `approved_at`. Ни тела, ни пинов, ни "
            "`approved_content_hash`, ни `version` этот PR не касается.",
            "",
            f"Подпись взята у мержа candidate-PR #{op['candidate_pr']}: "
            f"`approved_by = {op['merged_by']}`, `approved_at = "
            f"{op['merged_at']}`. Этот PR подпись не создаёт, а лишь "
            "записывает уже состоявшуюся.",
        ]
    )
