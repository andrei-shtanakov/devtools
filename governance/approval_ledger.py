"""Леджер заявок на одобрение узла бандла (§I12) — durable-половина.

Заявка (approval request) — одна попытка провести узлы одного уровня DAG
через двухфазную схему: candidate-PR выносит предложение, мерж candidate
учёткой из `authorized_approver_accounts` и есть акт одобрения,
финализирующий PR вписывает в узел уже состоявшуюся подпись. Всё, что
переживает падение процесса, живёт здесь — в `run.json` того же прогона, по
той же дисциплине write-ahead, что ревизии переиздания (§I4).

ЧТО ИМЕННО DURABLE и почему каждое поле:

- `wave`/`step`/`attempt` — номера волны, уровня и ПОПЫТКИ. Уникальность
  имени ветки на заявку не удобство именования, а то, что делает
  восстановление исполнимым: без `attempt` имя ветки новой заявки
  побайтово равнялось бы имени похороненной, и правило крэш-окна усыновляло
  бы её PR — заявка читала бы факты чужого мержа и хоронилась на них
  ВСЕГДА;
- `head_sha` — пишется сразу после коммита и ДО push. Без него падение
  между коммитом и созданием PR оставляет заявку без единственного факта,
  по которому её работу можно опознать в удалённой ветке. Опознание по
  имени ветки §I12 запрещает: имя говорит, где смотреть, идентичность
  устанавливает запись;
- `content_hashes`/`upstream_pins` — снимок того, что заявка вынесла на
  одобрение. Сверка фазы 3 сравнивает ПЕРЕСЧИТАННОЕ с записанным, а не
  поле с самим собой: поле отвечает «что мы записали», пересчёт — «что там
  лежит на самом деле», и сверка имеет смысл только между разными
  ответами;
- `candidate_pr`, факты его мержа, `finalize_pr` — шаги, уже сделанные;
  по ним и выводится, какой шаг остался (`next_step`);
- терминальный статус с причиной — единственное, что вправе записать
  отказ. Статус durable, а не выводимый из фактов: выводить мёртвость
  («логин не в allowlist — значит мертва») дешевле, но правка allowlist'а
  задним числом ОЖИВИЛА бы уже похороненную заявку, а вместе с ней и
  вторую, заведённую на смену.

Статуса узла заявка не несёт и нести не должна — он целиком определён
содержимым дерева (§I4, тот же довод про ревизии).
"""

from __future__ import annotations

from enum import Enum

from governance import approval_branches
from governance.approval_facts import Authorization, MergeEvent
from governance.run_state import RunState, save

#: Префикс ключа заявки в `state.ops`. Форма ключа — `approve-<W>-<K>-<A>`,
#: те же три числа, что в имени ветки (§I12), и по тому же основанию, по
#: которому §I4 держит номер ревизии в ключе `tasks-deliver-v<N>`: запись
#: журнала обязана быть адресуема тем же, чем адресуема её работа.
REQUEST_PREFIX = "approve-"

#: Живой статус заявки — один; терминальных три, и различие между ними
#: журнальное (§I12): `completed` — узлы одобрены и конверт записан;
#: `abandoned` — человек закрыл candidate, то есть РЕШИЛ не одобрять;
#: `invalidated` — заявку сделали неисполнимой факты, и решения человека в
#: ней нет. Обе последние открывают дорогу новой заявке, обе неприкосновенны
#: после записи (§I4).
STATUS_STARTED = "started"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"
STATUS_INVALIDATED = "invalidated"

TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_ABANDONED, STATUS_INVALIDATED)


class Step(Enum):
    """Шаг заявки, который ещё НЕ выполнен.

    Живость §I12 определяется наличием следующего шага, а не статусом файла
    узла, — поэтому шаг и есть первичная величина, а `is_live` выводится из
    него, а не наоборот. Второе определение живости разошлось бы с первым
    молча, и разойтись ему было бы где: у заявки три терминальных статуса и
    четыре живых положения.
    """

    CREATE_CANDIDATE = "create_candidate"
    AWAIT_CANDIDATE_MERGE = "await_candidate_merge"
    FINALIZE = "finalize"
    AWAIT_FINALIZE_MERGE = "await_finalize_merge"


def request_key(wave: int, step: int, attempt: int) -> str:
    """Ключ заявки в леджере прогона."""
    return f"{REQUEST_PREFIX}{wave}-{step}-{attempt}"


def requests(state: RunState) -> list[tuple[tuple[int, int, int], dict]]:
    """Все заявки прогона как `((W, K, A), запись)`, по возрастанию номеров.

    Разбор строгий: ключ обязан нести РОВНО три числа. Ключ соседней
    операции, случайно начавшийся с того же префикса, заявкой не считается
    — молча принять его значило бы завести в леджере запись, которую никто
    не заводил.
    """
    found: list[tuple[tuple[int, int, int], dict]] = []
    for key, op in state.ops.items():
        if not key.startswith(REQUEST_PREFIX):
            continue
        parts = key[len(REQUEST_PREFIX):].split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        found.append(((int(parts[0]), int(parts[1]), int(parts[2])), op))
    return sorted(found)


def next_step(op: dict) -> Step | None:
    """Шаг, который заявке остался; `None` — заявка терминальна.

    Выводится ТОЛЬКО из durable-записи. Факты форджи её не заменяют и
    заменить не могут: они говорят, что сейчас видно снаружи, а запись —
    что эта заявка уже сделала, и после падения второе восстанавливается
    только из леджера.

    Порядок шагов — тот же, что в таблице возобновления §I12: нет
    `candidate_pr` — создать (или усыновить свой PR крэш-окна по
    записанному `head_sha`); есть, но акта мержа ещё нет — ждём человека
    (§I9); акт есть, финализирующего PR нет — сверить и создать его; есть
    — ждём его мержа.
    """
    if op.get("status") in TERMINAL_STATUSES:
        return None
    if op.get("candidate_pr") is None:
        return Step.CREATE_CANDIDATE
    if not op.get("merged_by") or not op.get("merged_at"):
        return Step.AWAIT_CANDIDATE_MERGE
    if op.get("finalize_pr") is None:
        return Step.FINALIZE
    return Step.AWAIT_FINALIZE_MERGE


def is_live(op: dict) -> bool:
    """Заявка жива ⇔ у неё есть следующий шаг (§I12, пункт 7)."""
    return next_step(op) is not None


def live_requests(state: RunState) -> list[tuple[tuple[int, int, int], dict]]:
    """Живые заявки прогона — те, у которых остался шаг."""
    return [(nums, op) for nums, op in requests(state) if is_live(op)]


def live_request_over(
    state: RunState, node_id: str
) -> tuple[tuple[int, int, int], dict] | None:
    """Живая заявка, вынесшая этот узел; `None` — такой нет.

    Двух ЖИВЫХ заявок над узлом не бывает — и это ВЕСЬ запрет (§I12,
    пункт 7). Над терминальной заявкой новый candidate разрешён и является
    штатным восстановлением, а не обходом: первая редакция запрещала
    candidate над любым узлом в `approval_pending` и тем запирала
    воркстрим в трёх состояниях сразу.
    """
    for nums, op in live_requests(state):
        if node_id in (op.get("nodes") or ()):
            return nums, op
    return None


def live_request_for_step(
    state: RunState, wave: int, step: int
) -> tuple[tuple[int, int, int], dict] | None:
    """Живая заявка этого шага, к которой присоединяется новый вызов.

    Несколько вызовов одного шага накапливаются в ОДНОЙ ветке и одном PR
    (§I12): каждый читает её голову, добавляет свой узел и свой каскадный
    след. Присоединяться можно, только пока candidate не вмержен, — после
    мержа ветка уже отдана человеку и вносить в неё узлы нечего; такой
    вызов заводит новую заявку того же шага со следующим номером попытки
    (независимые узлы одного уровня объединять МОЖНО, но не обязано).

    Старшая по номеру попытки, если их почему-то несколько: присоединяться
    к более ранней значило бы дописывать в ветку, которую уже сменили.
    """
    joinable = [
        (nums, op)
        for nums, op in live_requests(state)
        if nums[:2] == (wave, step)
        and next_step(op)
        in (Step.CREATE_CANDIDATE, Step.AWAIT_CANDIDATE_MERGE)
    ]
    return max(joinable, key=lambda item: item[0]) if joinable else None


#: Ключ записи о закрытой волне. Форма намеренно НЕ похожа на ключ
#: заявки (`approve-<W>-<K>-<A>`): разбор заявок строгий и требует ровно
#: три числа, поэтому эта запись в них не попадает и попасть не может.
WAVE_PREFIX = "approve-wave-"


def close_wave(state: RunState, wave: int) -> None:
    """Долговечно записать, что проход по DAG этой волной завершён (§I12).

    Durable, а не выводимо — по тому же основанию, по которому durable
    статус заявки. Вывести «волна закрыта» из текущего состояния дерева
    нельзя: к моменту следующего вызова активный DAG успевает снова
    перестать быть честно одобренным (correction правит узел), и волна,
    закрывшаяся месяц назад, выглядела бы никогда не закрывавшейся —
    новый проход продолжал бы её номер.

    Идемпотентно: повторная запись говорит то же самое, и мутацией
    журнала не является — величина одна и та же.
    """
    state.ops[f"{WAVE_PREFIX}{wave}"] = {"status": "closed", "wave": wave}
    save(state)


def closed_waves(state: RunState) -> set[int]:
    """Номера волн, чей проход по DAG записан завершённым."""
    found: set[int] = set()
    for key, op in state.ops.items():
        if not key.startswith(WAVE_PREFIX):
            continue
        suffix = key[len(WAVE_PREFIX):]
        if suffix.isdigit() and op.get("status") == "closed":
            found.add(int(suffix))
    return found


def open_wave(state: RunState) -> int | None:
    """Номер идущей волны; `None` — живой волны нет.

    Волна живёт, пока по ней есть незакрытый шаг. `W` фиксируется НА ВОЛНУ,
    а не на вызов: иначе накопления вызовов одного шага в одной ветке не
    происходит вовсе — каждый уходил бы на собственное имя, и «второй вызов
    того же шага» никогда не попадал бы в тот же PR.

    Двух живых волн не бывает по построению (новая открывается только
    когда живой нет), поэтому две — не повод выбрать старшую, а повод
    отказать: молчаливый выбор увёл бы следующие вызовы в одну из них, а
    вторая осталась бы висеть незамеченной.
    """
    waves = {nums[0] for nums, _ in live_requests(state)}
    if len(waves) > 1:
        raise RuntimeError(
            f"живых волн больше одной: {sorted(waves)} — леджер прогона "
            "противоречив, закройте лишнюю заявку явно"
        )
    return max(waves) if waves else None


def next_wave(state: RunState) -> int:
    """Номер новой волны: максимальный ЗАПИСАННЫЙ плюс один.

    Из леджера, а не из существующих веток — тот же приём, которым §I1
    выбирает номер ревизии доставки, и по той же причине: ветка могла быть
    удалена после мержа, а могла остаться от захода, который умер. Старые
    волны в выборе номера не участвуют и переиспользованию не подлежат.
    """
    recorded = [nums[0] for nums, _ in requests(state)]
    return max(recorded) + 1 if recorded else 1


def next_attempt(state: RunState, wave: int, step: int) -> int:
    """Номер новой попытки внутри `(W, K)`: максимальный записанный плюс один.

    Номера ТЕРМИНАЛЬНЫХ заявок не переиспользуются — на этом стоит
    восстановление: заявка над узлом умирает в `invalidated`, штатное
    восстановление идёт новой заявкой обязательно в той же волне (новую
    открыть нельзя, живая есть) и на том же уровне, и без `A` её ветка
    называлась бы ровно как ветка похороненной.
    """
    recorded = [
        nums[2] for nums, _ in requests(state) if nums[:2] == (wave, step)
    ]
    return max(recorded) + 1 if recorded else 1


def start_request(
    state: RunState,
    ws_id: str,
    wave: int,
    step: int,
    attempt: int,
    nodes: list[str],
    content_hashes: dict[str, str],
    upstream_pins: dict[str, dict[str, str]],
) -> str:
    """Write-ahead намерения заявки (§I4); → её ключ в леджере.

    Пишется ДО единого эффекта — до коммита, ветки и PR: иначе падение
    между эффектом и записью оставило бы работу, которую некому опознать.

    Имена обеих веток берутся `approval_branches`, то есть выводятся из
    SSOT-шаблона `contracts/approval-branches/v1/patterns.env`, из которого
    вторая половина (`merge-pr.sh`) выводит свой глоб. Литерал здесь и был
    бы тем вторым определением, которое разъезжается молча — и уже дважды
    разъехалось.
    """
    key = request_key(wave, step, attempt)
    if key in state.ops:
        raise RuntimeError(
            f"заявка {key} уже есть в леджере — новая попытка обязана взять "
            "следующий номер (next_attempt), номера не переиспользуются"
        )
    state.ops[key] = {
        "status": STATUS_STARTED,
        "wave": wave,
        "step": step,
        "attempt": attempt,
        "nodes": list(nodes),
        "content_hashes": dict(content_hashes),
        "upstream_pins": {n: dict(p) for n, p in upstream_pins.items()},
        "branch": approval_branches.candidate_branch(
            ws_id, wave, step, attempt
        ),
        "finalize_branch": approval_branches.finalize_branch(
            ws_id, wave, step, attempt
        ),
        "head_sha": None,
        "candidate_pr": None,
        "merged_by": None,
        "merged_at": None,
        "merge_commit": None,
        # Решение об авторизации мержа: чья учётка и по какой политике
        # признана авторизованной. Пишется ВМЕСТЕ с фактами мержа и
        # больше не пересматривается — фаза 3 сверяет целостность этой
        # записи, а не применяет allowlist заново.
        "authorization": None,
        # Второй коммит заявки — конверт подписи — опознаётся своим
        # head'ом: одно поле не может назвать два разных коммита, а
        # усыновление PR крэш-окна сверяется именно с записанным head'ом
        # той ветки, на которую смотрит.
        "finalize_head_sha": None,
        "finalize_pr": None,
        "reason": None,
        "invalidated_by": None,
    }
    save(state)
    return key


def _update(state: RunState, key: str, **fields: object) -> dict:
    """Правка живой заявки; терминальная — RuntimeError (§I4).

    Терминальная запись не мутируется ни в другой терминальный статус, ни
    обратно в живой: причина, по которой заявку похоронили, — единственный
    след решения, а `save` пишет `run.json` целиком.
    """
    op = state.ops.get(key)
    if op is None:
        raise RuntimeError(f"заявки {key} нет в леджере")
    status = op.get("status")
    if status in TERMINAL_STATUSES:
        raise RuntimeError(
            f"заявка {key} терминальна ({status}, причина: "
            f"{op.get('reason')!r}) — терминальная запись не мутируется"
        )
    updated = {**op, **fields}
    state.ops[key] = updated
    save(state)
    return updated


def record_head_sha(state: RunState, key: str, head_sha: str) -> None:
    """`head_sha` своего коммита — durable СРАЗУ после коммита и ДО push.

    Та же дисциплина и по той же причине, что в §I3.1: без записанного
    head'а падение между коммитом и созданием PR оставляет заявку без
    единственного факта, по которому её работу можно опознать в удалённой
    ветке. Усыновление PR крэш-окна сверяется именно с ним — совпадения
    имени ветки мало.
    """
    _update(state, key, head_sha=head_sha)


def record_finalize_head_sha(state: RunState, key: str, head_sha: str) -> None:
    """`head_sha` коммита конверта — durable до push финализирующей ветки."""
    _update(state, key, finalize_head_sha=head_sha)


def record_candidate_pr(state: RunState, key: str, pr: int) -> None:
    """Номер candidate-PR заявки."""
    _update(state, key, candidate_pr=pr)


def extend_request(
    state: RunState,
    key: str,
    node_id: str,
    content_hash: str,
    upstream_pins: dict[str, str],
) -> None:
    """Добавить узел к заявке того же шага (накопление в одной ветке, §I12).

    Пишется ДО правки файлов — по той же причине, по которой пишется
    намерение: снимок того, что заявка выносит на одобрение, обязан
    существовать раньше, чем появятся байты, которые он описывает.

    Повторное добавление того же узла — отказ, а не молчаливая
    перезапись: перезапись снимка означала бы, что сверка фазы 3 сравнит
    пересчёт с величиной, посчитанной по ДРУГИМ байтам, чем те, что
    человек видел в PR.
    """
    op = state.ops.get(key)
    if op is None:
        raise RuntimeError(f"заявки {key} нет в леджере")
    if node_id in (op.get("nodes") or ()):
        raise RuntimeError(
            f"узел {node_id} уже вынесен заявкой {key} — снимок заявки не "
            "перезаписывается"
        )
    _update(
        state,
        key,
        nodes=[*op["nodes"], node_id],
        content_hashes={**op["content_hashes"], node_id: content_hash},
        upstream_pins={**op["upstream_pins"], node_id: dict(upstream_pins)},
    )


def record_merge(
    state: RunState,
    key: str,
    event: MergeEvent,
    authorization: Authorization,
) -> None:
    """Факты мержа candidate-PR и решение об их авторизации (§I12).

    Записывается СОБЫТИЕ форджи, а не самоописание процесса: логин,
    который команда сообщает о себе сама, не проверяем никем, а мерж —
    запись в фордже, которую видят все и которую нельзя переписать задним
    числом.

    `authorization` идёт ТЕМ ЖЕ write'ом и обязательным аргументом:
    решение о merger-учётке принимается ровно один раз, при установлении
    факта мержа, и с этого момента живёт как записанный факт. Разъедься
    оно с фактами мержа хоть на один шаг — и появилось бы состояние
    «мерж записан, а по какой политике он признан авторизованным,
    неизвестно», из которого честного выхода нет: перечитать список
    задним числом значит переавторизовать прошлое новой конфигурацией.
    """
    _update(
        state,
        key,
        merged_by=event.login,
        merged_at=event.merged_at,
        merge_commit=event.commit,
        authorization=authorization.as_record(),
    )


def record_finalize_pr(state: RunState, key: str, pr: int) -> None:
    """Номер финализирующего PR — второго и последнего PR заявки."""
    _update(state, key, finalize_pr=pr)


def complete_request(state: RunState, key: str) -> None:
    """Терминальный `completed`: конверт в base, предикат сходится."""
    _update(state, key, status=STATUS_COMPLETED)


def abandon_request(state: RunState, key: str, reason: str) -> None:
    """Терминальный `abandoned`: человек закрыл candidate, не одобрив.

    Инкремент `version` при этом откатывать нечего — в base он не попал.
    """
    _update(state, key, status=STATUS_ABANDONED, reason=reason)


def invalidate_request(
    state: RunState, key: str, reason: str, *, by: str | None = None
) -> None:
    """Терминальный `invalidated`: заявку сделали неисполнимой ФАКТЫ.

    Звать разрешено ТОЛЬКО по положительно установленному семантическому
    факту (`approval_facts.Fact.established`): прочитанному, однозначному и
    противоречащему заявке. `None`, `False`, `rc != 0`, исключение сети и
    любой иной свёрнутый исход заявку не убивают — она остаётся живой и
    возобновляемой, а вызов отказывает и предлагает повторить.

    `by` связывает убитую заявку с той, которая её сняла (заявка над
    upstream, §I12): знание «почему её сняли» обязано жить в самой записи,
    а не в памяти оператора — тот же довод, что у `replacement_reason`
    в §I10.
    """
    _update(
        state, key, status=STATUS_INVALIDATED, reason=reason, invalidated_by=by
    )
