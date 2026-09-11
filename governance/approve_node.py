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

**Механизм ВКЛЮЧЁН** (катовер PR #191, 2026-09-11). Прежняя редакция этого
абзаца описывала состояние до катовера — «CLI здесь нет, ни одна доставка
модуль не зовёт, поэтому его мерж ничего не включает» — и после катовера оба
утверждения стали ложными разом, то есть читатель, открывший модуль ровно
затем, чтобы узнать, включено ли, получал противоположный ответ. Как есть
сейчас:

- CLI-поверхность — `--approve-node <node-id>` у моста
  (`task_bridge.main`), и это ЕДИНСТВЕННАЯ дорога узла в `approved`;
- обе доставки зовут отсюда `read_dag_state` и
  `reconcile_wave_after_approved_dag` через гейт §I12 и без честно
  одобренного DAG не начинаются.

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

#: Имя в `invalidated_by` у заявок, похороненных за выпадение их узлов из
#: активного DAG. Имя, а не ключ заявки: эта похоронная операция
#: принадлежит ВЫЗОВУ, а не другой заявке, — снявшей её заявки не
#: существует. По этому маркеру обход находит свои похороненные записи
#: снова и доводит закрытие их PR, если оно не подтвердилось (major
#: третьего круга ревью #191). Значение не совпадает с формой ключа
#: заявки (`approve-request-W-K-A`) и спутать их нельзя.
_OUTSIDE_DAG = "outside-active-dag"


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
            f"target_dir {state.target_dir!r} грязный — одобрение не "
            "начато. Процедура: разберитесь с незакоммиченными правками "
            "(они могли остаться от упавшего захода — тогда их безопасно "
            "снять: заявка приводит ветку к своему снимку заново) и "
            "повторите вызов"
        )
    ops.checkout_and_pull(state.target_dir, _base_ref(state))
    bundle_dag.check_bundle_composition(state.target_dir, state.bundle_dir, dag)
    _settle_requests_outside_dag(state, ops, dag)
    known = [bundle_dag.node_id(fname) for fname, _ in dag]
    if node_id not in known:
        raise RuntimeError(
            f"узел {node_id!r} не входит в активный DAG. Допустимы: "
            f"{', '.join(known)}. Процедура: назовите node-id из этого "
            "перечня либо укажите --legacy-bundle с точным составом бандла"
        )
    # Сверка состава — по ВСЕМ живым заявкам и ДО ветвления, а не внутри
    # ветки продвижения. Заявка, у которой выпали ВСЕ узлы, иначе
    # недостижима ни одним вызовом: по её узлам приходит отказ «нет в
    # активном DAG», по соседним вызов уходит в предложение и о ней не
    # вспоминает. Она оставалась бы живой навсегда — а её candidate-PR
    # открытым и мержаемым, и человеческий мерж вернул бы удалённый файл
    # в base (issue #190, вторая половина).
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
    nodes = bundle_dag.composition(dag)
    fingerprint = bundle_dag.composition_fingerprint(nodes)
    _close_obsolete_wave(state, nodes, fingerprint)
    wave = _wave_for(state, nodes, fingerprint)

    joined = al.live_request_for_step(state, wave, step)
    if joined is not None and not _still_accumulating(state, ops, joined[1]):
        joined = None
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
    return _publish_candidate(state, ops, dag, key, debt)


@dataclass(frozen=True)
class ApprovedDag:
    """СВИДЕТЕЛЬСТВО успешного общего гейта: установленный состав и отпечаток.

    Тип существует не для удобства передачи, а как структурная защита от
    второго определения одобренности. Функция, получившая свидетельство,
    физически не может ни сходить в сеть, ни применить предикат заново, ни
    вывести одобренность по-своему: у неё на входе уже установленный факт,
    а не повод его пересчитать. За прогон мы трижды платили за вторые
    определения — два списка `authority-root`, три разбора одного SSOT,
    предикат одобренности рядом с самим собой; здесь это закрыто типом, а
    не договорённостью.

    Отсюда же и правило про `UNAVAILABLE`: неустановленный состав в
    реконсиляцию **не передаётся вовсе**, потому что свидетельства из него
    не получается — гейт при неустановленном DAG успешно завершиться не
    мог. Это выражено сигнатурой, а не проверкой внутри.
    """

    nodes: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class DagState:
    """Результат применения общего предиката ко ВСЕМУ активному DAG.

    `evidence` непусто ТОГДА И ТОЛЬКО ТОГДА, когда предикат сошёлся на
    каждом узле и состав установлен; иначе непусты `debts` (узлы с долгом,
    для диагностики отказа) либо `unresolved` (факт не установлен).

    Три величины вместе, а не три вызова: гейту доставки нужны и решение,
    и перечень непрошедших узлов с процедурами, и различие «в долгу» от
    «не установлено», — а собери он их отдельными обходами, обходы
    разошлись бы.
    """

    evidence: ApprovedDag | None
    debts: tuple[na.NodeDebt, ...] = ()
    unresolved: str = ""


def read_dag_state(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> DagState:
    """Общий предикат по всему активному DAG в base — ОДНО определение.

    Единственное место, где «честно одобрен весь DAG» вычисляется.
    Потребители называются ПОИМЁННО, а не числом (ревью #194): число
    устаревает молча — прежняя редакция говорила «два места применения»,
    и третий потребитель появился, не сделав её красной. Поимённо:

    - `task_bridge._approved_dag_or_refuse` — гейт доставки (с катовера
      PR #191 зовёт, а не «будет звать»);
    - `task_bridge._debt_notice` — диагностика бесследного no-op'а §I5;
      доставкой она НЕ является и решения не принимает, читает после
      того, как §I5 уже ответил.

    Редакция при этом одна: понадобится изменить, что считается честной
    одобренностью, — менять надо здесь, и каждый потребитель прочитает
    новое значение без единой правки у себя.

    Предикат применяется к КАЖДОМУ узлу, а не к терминальному уровню:
    активный DAG не цепочка, у него могут быть узлы вне транзитивного
    замыкания терминального, и их одобрение последней заявки не касается
    вовсе.

    Неустановленный факт не превращается ни в долг, ни в одобренность:
    `unresolved` — третий исход, и он не даёт свидетельства.
    """
    texts: dict[str, str] = {}
    for fname, _ in dag:
        fact = af.read_blob_text(
            ops, state.target_dir, _base_ref(state), _rel(state, fname)
        )
        if fact.outcome is not Outcome.FOUND or fact.value is None:
            return DagState(None, unresolved=fact.detail)
        texts[bundle_dag.node_id(fname)] = fact.value
    debts: list[na.NodeDebt] = []
    for fname, ups in dag:
        node = bundle_dag.node_id(fname)
        blobs = {up: blob_sha1(texts[up]) for up in ups}
        debt = na.node_debt(
            node,
            texts[node],
            blobs,
            awaiting_merge_pr=_pr_awaiting_merge(state, node),
        )
        if debt is not None:
            debts.append(debt)
    if debts:
        return DagState(None, debts=tuple(debts))
    nodes = bundle_dag.composition(dag)
    return DagState(
        ApprovedDag(nodes, bundle_dag.composition_fingerprint(nodes))
    )


def _intent_of(record: dict, wave: int) -> tuple[list[str], str]:
    """Intent волны либо fail-closed БЕЗ терминализации.

    Повреждённая или неполная запись intent'а не даёт ни `completed`, ни
    `obsolete`: закрыть проход можно только сравнением двух ЗАПИСАННЫХ
    величин, а здесь одной из них нет. Терминализовать по отсутствию
    величины значило бы завести ту же эвристику, которую §I12 запрещает
    для заявки.

    Выход из состояния есть, и он человеческий: запись волны после записи
    неприкосновенна (§I4), поэтому починить её механикой нельзя —
    поправьте `run.json` прогона руками либо начните новый прогон
    (`--run-id`), у которого леджер свой. Отказ называет оба пути.
    """
    intent = record.get("intent")
    nodes = intent.get("dag") if isinstance(intent, dict) else None
    fingerprint = intent.get("fingerprint") if isinstance(intent, dict) else None
    if not isinstance(nodes, list) or not fingerprint:
        raise RuntimeError(
            f"intent волны {wave} повреждён либо неполон ({intent!r}): "
            "сравнивать не с чем, волна НЕ закрывается и записи не "
            "меняются. Процедура: поправьте запись волны в run.json "
            "прогона либо начните новый прогон (--run-id) — запись волны "
            "механикой не чинится, она неприкосновенна после записи"
        )
    return nodes, str(fingerprint)


def reconcile_wave_after_approved_dag(
    state: RunState, approved: ApprovedDag
) -> str | None:
    """Судьба открытой волны по свидетельству гейта; → исход либо `None`.

    Шаг 3 нормативного порядка §I2, и **только** он: гейт уже сошёлся,
    сеть и предикат эта функция не повторяет — у неё их нет на входе.
    Часть 3 зовёт её после успешного гейта и **до любых delivery-эффектов**
    на всех трёх путях доставки.

    Это бухгалтерская реконсиляция уже пройденного гейта, а не его часть:
    гейт судит о DAG — одобрены ли узлы, — реконсиляция судит о ЗАПИСИ
    прежнего прохода — чем он кончился. Статус волны в решении о допуске
    не участвует вовсе.

    Три исхода, из которых нельзя выпасть:

    - состав свидетельства совпал с `wave.intent` → `completed`;
    - положительно разошёлся → `obsolete` с обоими составами и причиной;
    - открытой волны нет либо её судьба уже записана → `None`,
      идемпотентный no-op. На нём держится право вызывающего повторить
      доставку после падения.

    **Новую волну эта функция не создаёт никогда** — заводить проход
    работа `--approve-node`, а у реконсиляции право ровно одно: закрыть
    прежний.
    """
    current = al.open_wave(state)
    if current is None:
        return None
    nodes, fingerprint = _intent_of(al.wave_records(state)[current], current)
    if fingerprint == approved.fingerprint:
        al.complete_wave(state, current)
        return al.WAVE_COMPLETED
    al.obsolete_wave(
        state,
        current,
        approved.nodes,
        approved.fingerprint,
        f"состав активного DAG изменился: волна шла по {nodes} "
        f"({fingerprint}), гейт сошёлся на {list(approved.nodes)} "
        f"({approved.fingerprint})",
    )
    return al.WAVE_OBSOLETE


def _close_obsolete_wave(
    state: RunState, nodes: tuple[str, ...], fingerprint: str
) -> None:
    """Открытая волна с разошедшимся составом — `obsolete` (§I12).

    Отдельный ИМЕНОВАННЫЙ переход, а не побочный эффект выбора номера:
    смешав их, мы получили бы функцию, которая по имени выбирает волну, а
    по делу хоронит проход. Зовётся с пути предложения — контракт прямо
    разрешает `--approve-node` закрыть устаревшую волну и тем же вызовом
    завести новую: оба действия следуют из одного прочитанного состава, а
    разделив их, мы дали бы составу право разойтись между чтениями.

    Отличие обязано быть ПОЛОЖИТЕЛЬНО установленным — состав здесь уже
    прочитан вызывающим (гвард состава отработал до первой записи), и
    неустановленного исхода на этом пути не существует.
    """
    current = al.open_wave(state)
    if current is None:
        return
    _, recorded = _intent_of(al.wave_records(state)[current], current)
    if recorded == fingerprint:
        return
    al.obsolete_wave(
        state,
        current,
        nodes,
        fingerprint,
        f"состав активного DAG изменился: волна шла по отпечатку "
        f"{recorded}, сейчас {fingerprint}",
    )


def _wave_for(
    state: RunState, nodes: tuple[str, ...], fingerprint: str
) -> int:
    """Волна для этого вызова: продолжить открытую либо начать проход.

    Ничего не закрывает — только выбирает. Открытость читается по ЗАПИСИ
    волны, а не по живым заявкам: между вмерженным уровнем `K` и
    заведённым `K+1` живой заявки нет ни одной, и волна обязана этот
    промежуток пережить — иначе `K` не рос бы никогда, а `A` не рос бы
    вовсе.
    """
    current = al.open_wave(state)
    if current is not None:
        return current
    fresh = al.next_wave(state)
    al.open_wave_record(state, fresh, nodes, fingerprint)
    return fresh


def _still_accumulating(state: RunState, ops: Ops, op: dict) -> bool:
    """Можно ли ДОПИСАТЬ узел в эту заявку — по факту форджи, не по леджеру.

    Леджер отвечает «шаг ещё не сделан», а вопрос здесь другой: открыт ли
    candidate-PR ПРЯМО СЕЙЧАС. Величины расходятся штатно — человек мержит
    PR, а фактов мержа в записи ещё нет, — и на этом расхождении
    присоединение стоило человеческого акта: коммит уходил в ветку уже
    вмерженного PR, второго PR не создавалось (номер записан), в base узел
    не попадал никогда, а следующий вызов хоронил ВСЮ заявку с причиной
    «тело узла правили после мержа» — правки, которой не было.

    Поэтому:

    - `candidate_pr` пуст — публикации ещё не было, ветка наша, дописывать
      можно;
    - PR ОТКРЫТ — можно: ровно это и есть накопление вызовов шага в одной
      ветке;
    - PR ВМЕРЖЕН либо ЗАКРЫТ — нельзя: ветка отдана человеку, и вызов
      обязан завести новую заявку того же шага со следующим `A`
      (докстринг `live_request_for_step` говорил это с самого начала, а
      код не делал);
    - состояние не установлено — отказ БЕЗ записей. Присоединиться вслепую
      значит рискнуть человеческим актом, а завести новую заявку вслепую —
      опубликовать второе mergeable предложение рядом с неизвестным
      первым. Обе цены выше цены повтора.
    """
    pr = op.get("candidate_pr")
    if pr is None:
        return True
    return _disposition(_pr_facts(state, ops, pr), pr) is Disposition.OPEN


def _pr_awaiting_merge(state: RunState, node: str) -> int | None:
    """PR живой заявки над узлом, который ЖДЁТ человеческого мержа.

    Нужен диагностике, и весь его смысл — назвать оператору то действие,
    которого система действительно ждёт. Статус узла на этот вопрос не
    отвечает: `approval_pending` одинаково выглядит и когда открыт
    candidate, и когда candidate уже вмержен, а ждут мержа конверта.
    Величину знает только леджер — через ШАГ заявки, а не через номер
    первого попавшегося её PR.

    У заявки два PR, и ждать может каждый из них:

    - шаг `AWAIT_CANDIDATE_MERGE` — ждём candidate: его мерж и есть акт
      одобрения;
    - шаг `AWAIT_FINALIZE_MERGE` — candidate уже вмержен, ждём конверта.
      Назови здесь candidate — оператор уйдёт на вмерженный PR и вернётся
      ни с чем, то есть получит ровно тот лишний круг, ради устранения
      которого эта функция и заведена (issue #189);
    - шаги `CREATE_CANDIDATE` и `FINALIZE` — не ждёт ни один PR: работа за
      механикой, и оператору называют повторный вызов.
    """
    found = al.live_request_over(state, node)
    if found is None:
        return None
    op = found[1]
    step = al.next_step(op)
    if step is al.Step.AWAIT_CANDIDATE_MERGE:
        return op.get("candidate_pr")
    if step is al.Step.AWAIT_FINALIZE_MERGE:
        return op.get("finalize_pr")
    return None


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
        debt = na.node_debt(
            up,
            text,
            _base_upstream_blobs(ops, state, dag, up),
            awaiting_merge_pr=_pr_awaiting_merge(state, up),
        )
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
        _close_buried_proposals(state, ops, key)
        for branch in (op.get("branch"), op.get("finalize_branch")):
            if branch:
                # Не блокирует: оставшаяся ветка — неубранный мусор, а не
                # опасность (имена уникальны на заявку, усыновление идёт
                # по записанному head_sha).
                ops.delete_remote_branch(state.repo_slug, branch)


def _close_if_open(
    state: RunState, ops: Ops, pr: int, key: str, reason: str
) -> None:
    """Закрыть PR снятой заявки, если он открыт; иначе — ничего.

    Состояние спрашивается ПЕРВЫМ, и это не оптимизация: `close_pr` по уже
    закрытому PR отдаёт `False`, неотличимый от «нет прав», и повтор
    операции упирался бы в него вечно.

    Вмерженный PR закрывать нечего и не надо: его предложение со стола уже
    ушло — в base, — и долг, который оно там создало, объявит каскад
    нашего же approve. Открытым остаётся только то, что ещё можно смержить,
    и ровно это блокирует публикацию.

    **Причина приходит аргументом, а не зашита в текст** (issue #193).
    Зашитая формулировка — «upstream выносится на одобрение заново, и это
    предложение посчитано против прежних байтов» — верна ровно для одного
    из двух погребений. У outside-DAG-погребения upstream заново никто не
    выносит и байты не «прежние»: узла в бандле нет вовсе. Человек,
    открывший закрытый PR, читал причину, которой не было, — и это хуже
    отсутствия причины, потому что выглядит как объяснение.

    Источник причины — САМА ЗАПИСЬ заявки, а не вторая формулировка рядом
    с первой: разойдись они, в журнале и в PR стояли бы разные объяснения
    одного события, и краснеть бы это не начало (тот же довод, по
    которому `_incomparable_reason` выведен из `_comparable_anchors`).
    """
    if _disposition(_pr_facts(state, ops, pr), pr) is not Disposition.OPEN:
        return
    closed = af.confirm_closed(
        ops,
        state.repo_slug,
        pr,
        f"Предложение снято (§I12). Причина из журнала заявки {key}: "
        f"{reason}",
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


def _carried_text(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    node: str,
) -> str:
    """Байты узла, которые заявка ВЫНОСИТ: фаза 1 поверх байтов в base.

    ОДНО определение того, что несёт заявка, и оно же — мера для всех
    сверок: приведения ветки к снимку и признака «опубликовано». Пока
    таких определений было два (снимок в леджере и фактическое содержимое
    ветки), они расходились, и расхождения закрывались по одному:
    вмерженный candidate, узел без push, узел без коммита, дельта вместо
    снимка. Пятый способ разойтись нашёлся бы тем же чередом.

    Считается от BASE, а не от текущей ветки, и это существенно: узел
    заявки не может быть тронут каскадом соседа по шагу — у независимых
    узлов одного уровня рёбер между собой нет, — поэтому base и есть та
    точка, от которой фаза 1 определена. Заодно результат перестаёт
    зависеть от того, сколько заходов уже было.

    Пишутся ровно четыре величины: `status`, пины из снимка,
    `approved_content_hash` из снимка и `version + 1`. Подпись НЕ
    пишется — акта ещё не было; прежняя, если узел шёл из `approved`,
    остаётся нетронутой.
    """
    text = _base_text(ops, state, _filename(dag, node))
    recorded = op["content_hashes"][node]
    actual = na.self_hash(text)
    if actual != recorded:
        raise RuntimeError(
            f"{node}: собственные байты в base не те, что вынесла заявка "
            f"(записано {recorded}, в base {actual}) — предложение не "
            "публикуется"
        )
    meta, body = split_frontmatter(text)
    meta["status"] = na.STATUS_APPROVAL_PENDING
    meta["version"] = int(meta.get("version") or 1) + 1
    pins = op["upstream_pins"][node]
    if pins:
        meta["upstream_hashes"] = dict(pins)
    meta[na.SELF_HASH_KEY] = recorded
    return join_frontmatter(meta, body)


def _sync_branch_to_snapshot(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    key: str,
) -> tuple[str, ...]:
    """Привести ветку заявки к её снимку ЦЕЛИКОМ и идемпотентно.

    Не «дописать новый узел», а «сделать так, чтобы в ветке лежало ровно
    то, что в снимке» — независимо от того, сколько узлов там уже есть и
    на каком шаге упал прошлый заход. Отсюда перестают быть разными
    случаями «узел не дошёл до коммита», «не дошёл до push» и
    «присоединился к заявке без коммита»: любой заход доводит ветку до
    снимка.

    Идемпотентность держится на сравнении БАЙТОВ с теми, что заявка
    выносит, а не на признаке «мы уже писали»: признак был бы третьим
    состоянием рядом со снимком и веткой, и разойтись ему было бы где.

    Коммитом уходят все изменённые файлы разом — узлы и весь рекурсивный
    след `stale`: частичный коммит оставил бы в ветке `approved`-узел с
    ложным пином. `head_sha` пишется между коммитом и push, иначе падение
    здесь оставляет заявку без единственного факта, по которому её работу
    опознают в удалённой ветке.
    """
    op = state.ops[key]
    _switch_to_request_branch(state, ops, op["branch"], op.get("head_sha"))
    changed: list[str] = []
    for node in op["nodes"]:
        carried = _carried_text(state, ops, dag, op, node)
        rel = _rel(state, _filename(dag, node))
        path = Path(state.target_dir) / rel
        if path.read_text(encoding="utf-8") == carried:
            continue
        path.write_text(carried, encoding="utf-8")
        changed.append(rel)
        changed += _cascade_stale(state, dag, node)
    if not changed:
        return ()
    ops.commit_paths(
        state.target_dir,
        changed,
        f"spec: {state.ws_id} — узел(ы) {', '.join(op['nodes'])} вынесены "
        f"на одобрение (approval_pending), каскад stale (fleet-agent)",
    )
    head = ops.rev_parse(state.target_dir, "HEAD")
    if head is None:
        raise RuntimeError(
            "коммит фазы 1 не состоялся: HEAD не разрешается — "
            "предложение не публикуется"
        )
    al.record_head_sha(state, key, head)
    return tuple(changed)


def _carries_snapshot_shape(text: str, op: dict, node: str) -> bool:
    """Несёт ли этот текст узла то, что заявка вынесла на одобрение.

    Предикат намеренно БАЗОНЕЗАВИСИМ — в отличие от `_carried_text`,
    который считает байты фазы 1 поверх base. Спрашивают его в том числе
    ПОСЛЕ мержа candidate, когда base уже содержит результат этого мержа:
    сверка точных байтов там объявила бы неопубликованным ровно то, что
    только что вмержено, и хоронила бы заявку на ровном месте.

    Сверяются три величины снимка — статус, пины и `approved_content_hash`.
    Их достаточно: узел, не дошедший до коммита, лежит в опубликованной
    голове своими прежними байтами, а прежний статус — не
    `approval_pending` (иначе одобрять было бы нечего).

    Принятый остаток: `version` в предикат не входит, потому что он
    относителен к base, а base между заходами уезжает. Узел, который в
    base УЖЕ лежит ровно в форме этой заявки (след её же предыдущей,
    похороненной попытки), пройдёт сверку и без нашего коммита — и
    получит конверт без инкремента поколения. Ни одна другая проверка от
    этого не страдает: фаза 3 сверяет self-hash и пины, а не `version`.
    """
    meta, _ = split_frontmatter(text)
    pins = meta.get("upstream_hashes")
    pins = dict(pins) if isinstance(pins, dict) else {}
    return (
        meta.get("status") == na.STATUS_APPROVAL_PENDING
        and meta.get(na.SELF_HASH_KEY) == op["content_hashes"][node]
        and pins == dict(op["upstream_pins"][node])
    )


def _snapshot_is_published(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
    op: dict,
    head: object,
) -> bool | None:
    """Всё ли из снимка лежит в опубликованной голове; `None` — не узнать.

    Спрашивается СОСТАВ, а не голова. `head_sha` отвечает на вопрос «наш
    ли это коммит», а не «всё ли вынесено» — две разные величины, и
    выводить вторую из первой значит объявить опубликованным узел, который
    в снимок попал, а до коммита не дошёл: голова-то совпадает.

    `None` — факт не установлен (голова не названа, объект недоступен):
    заявку это не хоронит, вызывающий отказывает и предлагает повторить.
    """
    if not isinstance(head, str) or not head:
        return None
    ops.fetch_branch(state.target_dir, op["branch"])
    if ops.rev_parse(state.target_dir, head) is None:
        return None
    for node in op["nodes"]:
        fact = af.read_blob_text(
            ops, state.target_dir, head, _rel(state, _filename(dag, node))
        )
        if fact.outcome is not Outcome.FOUND or fact.value is None:
            return None
        if not _carries_snapshot_shape(fact.value, op, node):
            return False
    return True


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
    debt: na.NodeDebt | None = None,
) -> ApprovalOutcome:
    """Опубликовать заявку: ветка приводится к снимку, пушится, PR есть.

    «Опубликовать» здесь значит одно: в ветке лежит ровно то, что в
    снимке, и это видно снаружи. Приведение идёт ЦЕЛИКОМ и идемпотентно,
    поэтому вызов одинаково годится и для первого захода, и для любого
    возобновления — падение на любом шаге доигрывается тем же кодом.

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
    changed = _sync_branch_to_snapshot(state, ops, dag, key)
    op = state.ops[key]
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
        # Ни одного «если упали до коммита» здесь больше нет: публикация
        # приводит ветку к снимку целиком и сама решает, что дописать.
        return _publish_candidate(state, ops, dag, key)
    if step is al.Step.AWAIT_CANDIDATE_MERGE:
        return _reconcile_candidate(state, ops, dag, op, key)
    if step is al.Step.FINALIZE:
        return _finalize(state, ops, dag, state.ops[key], key)
    return _reconcile_finalize(state, ops, dag, state.ops[key], key)


def _settle_requests_outside_dag(
    state: RunState,
    ops: Ops,
    dag: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    """Все заявки, чьи узлы выпали из активного DAG, — терминально.

    Обходятся ВСЕ заявки, а не заявка над запрошенным узлом: иначе
    заявка, у которой выпали все узлы до единого, не встречается ни одному
    вызову (по её узлам — отказ «нет в активном DAG», по соседним — уход в
    предложение), остаётся живой навсегда и держит открытым мержаемый
    candidate-PR. Мерж такого PR вернул бы удалённый файл в base — из
    пяти хвостов этот единственный оставлял на столе артефакт.

    **Обход идёт по `al.requests`, а не по `al.live_requests`** (major
    третьего круга ревью #191), и это исправление КЛАССА, а не случая.
    Инвариант, который здесь держится:

        у каждой терминализованной записи, чей сетевой эффект мог не
        состояться, обязан быть путь довести эффект на повторе — и путь
        этот не вправе зависеть от того, по какому узлу пришёл вызов.

    Порядок «durable-запись раньше сетевого эффекта» (§I10) сам по себе
    оставляет окно: между записью и закрытием PR вызов может упасть. После
    записи заявка терминальна, `is_live` False, и обход по живым её уже не
    видит; `_update` терминальную запись не мутирует, так что и руками
    довести нечем. Открытый candidate при этом остаётся мержаемым —
    ровно тот исход, который эта функция объявляет закрытым.

    Выход — маркер `invalidated_by`, по которому свои похороненные записи
    находятся снова (`already_ours`). Приём взят дословно у соседа,
    `_invalidate_downstream`: там он заведён по тому же поводу и тем же
    доводом. Разница только в том, ЧЬЁ имя стоит в маркере: у соседа —
    ключ заявки, снявшей чужую, здесь — `_OUTSIDE_DAG`, потому что эта
    похоронная операция принадлежит не заявке, а вызову.

    Цена названа честно: множество помеченных `_OUTSIDE_DAG` растёт
    монотонно, и каждый следующий вызов спрашивает состояние их PR заново
    (по одному `read_pr` на записанный номер). Растёт оно только там, где
    correction удалил узел из бандла, — событие редкое; а альтернатива
    («спросить один раз») и есть тот самый дефект, ради которого маркер
    заведён.

    Состав сверяется ПОСЛЕ гварда состава бандла, поэтому «узла нет в
    DAG» означает «файла нет в бандле», а не «оператор назвал другой
    `--legacy-bundle`»: несовпадение заявленного состава с фактическим
    каталогом отказывает раньше.
    """
    known = {bundle_dag.node_id(fname) for fname, _ in dag}
    for nums, op in al.requests(state):
        key = al.request_key(*nums)
        missing = [
            node for node in (op.get("nodes") or ()) if node not in known
        ]
        doomed_now = al.is_live(op) and bool(missing)
        already_ours = op.get("invalidated_by") == _OUTSIDE_DAG
        if not doomed_now and not already_ours:
            continue
        if doomed_now:
            _terminalize_request_outside_dag(state, op, key, known, missing)
        _close_buried_proposals(state, ops, key)


def _terminalize_request_outside_dag(
    state: RunState,
    op: dict,
    key: str,
    known: set[str],
    missing: list[str],
) -> None:
    """Узел заявки выпал из активного DAG — заявка неисполнима (issue #190).

    Живая заявка несёт узлы прежнего состава; correction мог удалить файл
    узла из бандла, и тогда продолжать её нечем: приведение ветки к
    снимку, признак «опубликовано» и сверки фазы 3 обходят `nodes` и ищут
    для каждого файл в активном DAG. Раньше здесь вылетал голый `KeyError`
    из `_filename` — трассировка без диагноза и без выхода.

    Факт установлен положительно, а не выведен из аргумента: состав
    активного DAG сверен с ФАКТИЧЕСКИМ каталогом бандла в base
    (`check_bundle_composition` отрабатывает раньше), значит файла в
    бандле действительно нет. Это тот же класс, что «заявка посчитана
    против байтов, которых больше не станет», и ответ тот же —
    терминальный `invalidated` с причиной, из которого выход обычный:
    новый candidate по актуальному составу.

    Терминализация — журнальный факт, а НЕ отказ вызова: оператор мог
    спросить про соседний узел, и хоронить его вызов за чужую мёртвую
    заявку не за что. Отказ, если он нужен, приходит своим порядком —
    вызов по самому выпавшему узлу упирается в проверку состава DAG,
    которая называет допустимые id.

    Цена названа честно: если candidate этой заявки уже вмержен,
    человеческий акт над её выжившими узлами сгорает — они остаются
    `approval_pending` и пойдут новым candidate. Дешевле замершего
    воркстрима, но не бесплатно.

    Функция пишет ТОЛЬКО запись. Закрытие открытых PR делает
    `_close_buried_proposals` отдельным шагом у вызывающей стороны —
    порядок «durable-запись раньше сетевого эффекта» (§I10) сохранён, но
    шаги разведены намеренно: повтор обязан уметь довести закрытие БЕЗ
    повторной записи, которую терминальная запись всё равно не примет.
    """
    al.invalidate_request(
        state,
        key,
        f"узлы {', '.join(missing)} выпали из состава активного DAG "
        f"({', '.join(sorted(known))}) — предложение заявки неисполнимо",
        by=_OUTSIDE_DAG,
    )


def _close_buried_proposals(state: RunState, ops: Ops, key: str) -> None:
    """Закрыть открытые PR похороненной заявки — идемпотентно.

    Предложение похороненной заявки остаётся мержаемым, а его мерж создал
    бы подпись под тем, что снято со стола. `_close_if_open` спрашивает
    состояние первым, поэтому повтор по уже закрытому PR — чтение и
    ничего больше; неподтверждённое закрытие отказывает вызову и
    возвращается сюда на следующем заходе.

    Запись читается ИЗ ЛЕДЖЕРА по ключу, а не принимается словарём
    (issue #193): `al.invalidate_request` кладёт в `state.ops` НОВЫЙ
    словарь, и словарь, пойманный обходом до погребения, причины ещё не
    несёт. Читая свежую запись, функция берёт ту самую причину, которая
    погребение и объясняет.
    """
    op = state.ops.get(key) or {}
    reason = op.get("reason") or (
        "причина в журнале заявки не записана — состояние старше правила "
        "§I12 о причинах либо запись правлена руками"
    )
    for pr in (op.get("candidate_pr"), op.get("finalize_pr")):
        if pr is not None:
            _close_if_open(state, ops, pr, key, reason)



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
    # Спрашивается СОСТАВ, а не голова: совпавший `head_sha` означает
    # «коммит наш», а не «всё вынесенное опубликовано», и узел, дошедший
    # до снимка, но не до коммита, при сверке по голове остался бы вне PR
    # навсегда.
    published = _snapshot_is_published(
        state, ops, dag, op, facts.get("headRefOid")
    )
    if published is None:
        raise _unresolved(
            f"состав предложения в голове PR #{pr} — объект недоступен "
            "либо голова не названа"
        )
    if _disposition(facts, pr) is Disposition.OPEN:
        if not published:
            # Работа заявки записана, но в голову PR не попала: между
            # коммитом и успешным push вызов отказал (штатно —
            # например, закрытие чужого PR не подтвердилось). «Ждём
            # мержа» здесь было бы тупиком: человек видит в PR не все
            # узлы снимка, а после мержа заявка хоронится целиком.
            # Доигрываем НЕВЫПОЛНЕННЫЙ шаг — ровно как на пути создания.
            _publish_candidate(state, ops, dag, key)
            return ApprovalOutcome(
                f"предложение заявки {key} допубликовано в PR #{pr}: "
                f"узлы снимка ({', '.join(op['nodes'])}) не все были в его "
                f"голове. Дальше — мерж учёткой из "
                f"{af.APPROVER_ALLOWLIST_ENV}",
                request=key,
            )
        return ApprovalOutcome(
            f"candidate-PR #{pr} открыт — ждём мержа учёткой из "
            f"{af.APPROVER_ALLOWLIST_ENV}. Заявка {key} жива, ничего не "
            "изменено",
            request=key,
        )
    if not published:
        # PR закрыт либо вмержен, а наш коммит в его голову не попал:
        # предложение, которое заявка ВЫНЕСЛА, человеку не показывали.
        # Факт положительно установлен (обе величины прочитаны), и он
        # противоречит заявке — значит терминальный статус, а не отказ с
        # сохранением. Восстановление обычное: новый candidate.
        al.invalidate_request(
            state,
            key,
            f"в голове PR #{pr} ({facts.get('headRefOid')}) лежат не все "
            f"узлы снимка заявки ({', '.join(op['nodes'])}), а PR уже "
            "закрыт либо вмержен: предъявлено человеку было не то, что "
            "заявка выносит",
        )
        raise RuntimeError(
            f"PR #{pr} завершён не на предложении заявки {key} — часть её "
            "узлов человеку не предъявлялась. Заявка invalidated; "
            "восстановление: новый candidate над теми же узлами"
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
        # Состояние PR — не конверт. Вмерженный финализирующий PR почти
        # всегда означает конверт в base, но «почти всегда» и есть тот
        # зазор, из-за которого контракт требует сверять БАЙТЫ, а не
        # ответ форджи: PR мог быть вмержен в другую базу, а мог нести не
        # тот конверт. Проверяются те же величины, что и в фазе 3, и
        # несошедшаяся хоронит заявку по установленному факту.
        ops.checkout_and_pull(state.target_dir, _base_ref(state))
        pending = _verify_nodes_in_base(state, ops, dag, op, key)
        _require(
            state,
            key,
            not pending,
            f"финализирующий PR #{pr} вмержен, но узлы {', '.join(pending)} "
            "в base по-прежнему approval_pending — конверта там нет",
        )
        al.complete_request(state, key)
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
